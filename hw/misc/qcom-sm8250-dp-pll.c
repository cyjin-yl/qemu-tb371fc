/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Source-bound SM8250 non-V2 DP 7nm digital PLL reference.
 * OnePlusOSS/android_kernel_oneplus_sm8250
 * 98719830b0de5584f5588bbe9f4c8304d97a64c6:
 * dp_pll_7nm.c, dp_pll_7nm_util.c, and dp_catalog_v420.c.
 *
 * The two 64-XO-cycle delays are finite reference policies, not measured
 * silicon lock/PHY latencies. The late PHY reset's 2 ms minimum is from the
 * source. AUX, USB combo arbitration, analog swing, training and a sink are
 * not implemented. Interface/pipe clock control is outside this PLL model.
 * The vendor CCF graph uses kHz numbers; Clock objects carry physical Hz.
 * Cold register-derived software rates never establish a physical carrier.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-dp-pll.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define DP_XO_HZ UINT64_C(19200000)
#define DP_DELAY_NS ((int64_t)((DP_PLL_REFERENCE_CYCLES * \
                     UINT64_C(1000000000) + DP_XO_HZ - 1) / DP_XO_HZ))
#define DP_LATE_RESET_NS INT64_C(2000000)
#define DP_PHY_CFG 0x010
#define DP_PHY_PD_CTL 0x018
#define DP_PHY_AUX_CFG2 0x028
#define DP_PHY_VCO_DIV 0x070
#define DP_PHY_SPARE0 0x0c8
#define DP_PHY_STATUS 0x0dc
#define DP_PLL_RESET 0x09c
#define DP_PLL_CMN_STATUS 0x140
#define DP_PLL_HSCLK_SEL 0x158
#define DP_PLL_C_READY 0x178

enum {
    DP_IDLE,
    DP_PLL_PENDING,
    DP_PLL_LOCKED,
    DP_PHY_PENDING,
    DP_PHY_READY,
    DP_LATE_PHY_WAIT,
};

const uint64_t qcom_sm8250_dp_pll_bases[DP_PLL_REGIONS] = {
    0x088ea000, 0x088eaa00, 0x088ea200, 0x088ea600,
};

static const char *const dp_region_names[DP_PLL_REGIONS] = {
    "pll", "phy", "tx0", "tx1",
};

#include "qcom-sm8250-dp-pll-data.inc"

typedef struct DpPllWindow {
    QcomSm8250DpPllState *state;
    uint8_t region;
    uint16_t offset;
} DpPllWindow;

struct QcomSm8250DpPllState {
    SysBusDevice parent_obj;
    MemoryRegion container[DP_PLL_REGIONS];
    MemoryRegion iomem[DP_PLL_WINDOWS];
    DpPllWindow windows[DP_PLL_WINDOWS];
    Clock *input[DP_PLL_INPUTS];
    Clock *clocks[DP_PLL_CLOCKS];
    QEMUTimer *timer;
    DeviceState *mdss_provider;
    bool enabled_reference;
    bool ready;
    bool updating;

    uint32_t reference_profile;
    uint32_t reference_identity;
    uint32_t region_count;
    uint32_t register_count;
    uint32_t window_count;
    uint32_t mapped_bytes;
    uint32_t clock_count;
    uint32_t input_count;
    uint32_t timer_count;
    uint32_t ccf_rate_scale;
    uint32_t pll_delay_cycles;
    uint32_t phy_delay_cycles;
    uint32_t region_bytes;
    uint64_t physical_base[DP_PLL_REGIONS];

    uint32_t regs[DP_PLL_REGISTERS];
    uint8_t written[DP_PLL_REGISTERS];
    uint32_t captured[DP_PLL_REGISTERS];
    uint64_t captured_xo_period;
    uint64_t last_xo_period;
    int64_t deadline;
    uint32_t phase;
    uint32_t sequence;
    uint64_t captured_rate_khz;
    uint8_t power_known;
    uint8_t power_on;
};

static int dp_register(unsigned region, hwaddr offset)
{
    unsigned i;

    for (i = 0; i < DP_PLL_REGISTERS; i++) {
        if (dp_pll_regs[i].region == region &&
            dp_pll_regs[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static uint32_t dp_value(const uint32_t *regs, unsigned region, unsigned offset)
{
    int reg = dp_register(region, offset);

    g_assert(reg >= 0);
    return regs[reg];
}

static bool dp_supplied(QcomSm8250DpPllState *s)
{
    return s->enabled_reference && s->power_known && s->power_on &&
           clock_get(s->input[0]) == CLOCK_PERIOD_FROM_HZ(DP_XO_HZ);
}

static bool dp_locked(QcomSm8250DpPllState *s)
{
    return s->phase >= DP_PLL_LOCKED && s->phase <= DP_LATE_PHY_WAIT;
}

static uint64_t dp_raw_rate(const uint32_t *regs, bool *known)
{
    uint32_t hs = dp_value(regs, DP_REGION_PLL, DP_PLL_HSCLK_SEL) & 15;
    uint32_t aux = (dp_value(regs, DP_REGION_PHY, DP_PHY_AUX_CFG2) >> 2) & 3;
    uint64_t rate;

    *known = aux != 3;
    switch (hs) {
    case 5: rate = 1620000; break;
    case 3: rate = 2700000; break;
    case 1: rate = 5400000; break;
    case 0: rate = 8100000; break;
    default:
        *known = false;
        return 0;
    }
    return !*known ? 0 : aux == 2 ? 2700000 : rate;
}

static uint64_t dp_recipe(QcomSm8250DpPllState *s, const uint32_t *regs)
{
    static const uint64_t rates[] = { 1620000, 2700000, 5400000, 8100000 };
    uint32_t spare = dp_value(regs, DP_REGION_PHY, DP_PHY_SPARE0);
    unsigned lanes = spare & 15, orientation = spare >> 4;
    unsigned i, rate;

    if ((lanes != 1 && lanes != 2 && lanes != 4) ||
        (orientation != 1 && orientation != 2)) {
        return 0;
    }
    for (rate = 0; rate < ARRAY_SIZE(rates); rate++) {
        bool match = true;

        for (i = 0; i < DP_PLL_REGISTERS; i++) {
            const DpPllRegDef *d = &dp_pll_regs[i];
            uint32_t expected;

            if (d->required && !s->written[i]) {
                match = false;
                break;
            }
            switch (d->rule) {
            case DP_RULE_NONE:
            case DP_RULE_SPARE:
                continue;
            case DP_RULE_FIXED: expected = d->value[0]; break;
            case DP_RULE_RATE: expected = d->value[rate]; break;
            case DP_RULE_MODE: expected = d->value[orientation - 1]; break;
            case DP_RULE_POWER:
                expected = d->value[lanes == 4 ? 2 : orientation - 1];
                break;
            default: g_assert_not_reached();
            }
            if (regs[i] != expected) {
                match = false;
                break;
            }
        }
        if (match) {
            return rates[rate];
        }
    }
    return 0;
}

static bool dp_capture_valid(QcomSm8250DpPllState *s)
{
    unsigned i;

    if (!dp_supplied(s) || !s->captured_rate_khz ||
        s->captured_xo_period != clock_get(s->input[0]) ||
        !s->written[dp_register(DP_REGION_PHY, DP_PHY_CFG)] ||
        !s->written[dp_register(DP_REGION_PLL, DP_PLL_RESET)] ||
        dp_value(s->captured, DP_REGION_PHY, DP_PHY_CFG) != 9 ||
        dp_value(s->captured, DP_REGION_PLL, DP_PLL_RESET) != 0x20 ||
        dp_recipe(s, s->captured) != s->captured_rate_khz ||
        dp_recipe(s, s->regs) != s->captured_rate_khz) {
        return false;
    }
    for (i = 0; i < DP_PLL_REGISTERS; i++) {
        const DpPllRegDef *d = &dp_pll_regs[i];

        /* The live pixel mux and post-PLL analog settings do not retune PLL. */
        if (d->required &&
            !(d->region == DP_REGION_PHY && d->offset == DP_PHY_VCO_DIV) &&
            s->regs[i] != s->captured[i]) {
            return false;
        }
    }
    return true;
}

static void dp_rates(QcomSm8250DpPllState *s, uint64_t rates[DP_PLL_CLOCKS])
{
    uint64_t hs;
    uint32_t mux;

    memset(rates, 0, sizeof(uint64_t) * DP_PLL_CLOCKS);
    if (!dp_locked(s) || !dp_capture_valid(s)) {
        return;
    }
    /*
     * dp_vco_clk is the source HSCLK view, not the analog 9.72/10.8 GHz VCO.
     * Normalize the proven kHz CCF convention once at this provider boundary.
     */
    hs = s->captured_rate_khz * DP_PLL_CCF_RATE_SCALE;
    rates[0] = hs;
    rates[2] = hs / 2;
    rates[3] = hs / 4;
    rates[4] = hs / 6;
    if (s->phase != DP_PHY_READY) {
        return;
    }
    rates[1] = hs / 10;
    mux = dp_value(s->regs, DP_REGION_PHY, DP_PHY_VCO_DIV) & 3;
    rates[5] = mux == 1 ? rates[2] : mux == 2 ? rates[3] :
               mux == 0 ? rates[4] : 0;
}

static void dp_publish(QcomSm8250DpPllState *s, bool propagate)
{
    uint64_t rates[DP_PLL_CLOCKS];
    bool changed[DP_PLL_CLOCKS];
    unsigned i;

    if (!s->ready || s->updating) {
        return;
    }
    s->updating = true;
    dp_rates(s, rates);
    for (i = 0; i < DP_PLL_CLOCKS; i++) {
        changed[i] = clock_set(s->clocks[i], rates[i] ?
                               CLOCK_PERIOD_FROM_HZ(rates[i]) : 0);
    }
    if (propagate) {
        for (i = 0; i < DP_PLL_CLOCKS; i++) {
            if (changed[i]) {
                clock_propagate(s->clocks[i]);
            }
        }
    }
    s->updating = false;
}

static void dp_cancel(QcomSm8250DpPllState *s, bool lose_configuration)
{
    timer_del(s->timer);
    s->phase = DP_IDLE;
    s->sequence = 0;
    s->deadline = -1;
    s->captured_xo_period = 0;
    s->captured_rate_khz = 0;
    memset(s->captured, 0, sizeof(s->captured));
    if (lose_configuration) {
        memset(s->written, 0, sizeof(s->written));
    }
}

static bool dp_wait(QcomSm8250DpPllState *s, unsigned phase, int64_t delay)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (now < 0 || now > INT64_MAX - delay) {
        dp_cancel(s, false);
        return false;
    }
    s->phase = phase;
    s->deadline = now + delay;
    timer_mod_ns(s->timer, s->deadline);
    return true;
}

static void dp_timer(void *opaque)
{
    QcomSm8250DpPllState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (!s->ready || s->deadline < 0) {
        return;
    }
    if (!dp_capture_valid(s)) {
        dp_cancel(s, false);
    } else if (now < s->deadline) {
        timer_mod_ns(s->timer, s->deadline);
        return;
    } else {
        s->phase = s->phase == DP_PHY_PENDING ? DP_PHY_READY : DP_PLL_LOCKED;
        s->deadline = -1;
    }
    dp_publish(s, true);
}

static void dp_clock_changed(void *opaque, ClockEvent event)
{
    QcomSm8250DpPllState *s = opaque;
    uint64_t period = clock_get(s->input[0]);

    if (period == s->last_xo_period) {
        return;
    }
    s->last_xo_period = period;
    dp_cancel(s, false);
    dp_publish(s, true);
}

static void dp_power_known(void *opaque, int index, int level)
{
    QcomSm8250DpPllState *s = opaque;

    if (s->power_known == !!level) {
        return;
    }
    s->power_known = !!level;
    if (!level) {
        dp_cancel(s, true);
    }
    dp_publish(s, true);
}

static void dp_power_on(void *opaque, int index, int level)
{
    QcomSm8250DpPllState *s = opaque;

    if (s->power_on == !!level) {
        return;
    }
    s->power_on = !!level;
    if (!level) {
        dp_cancel(s, true);
    }
    dp_publish(s, true);
}

static void dp_phy_command(QcomSm8250DpPllState *s, uint32_t value)
{
    unsigned old_sequence = s->sequence;

    if (value == 0x19 && s->phase == DP_PLL_LOCKED && dp_capture_valid(s)) {
        dp_wait(s, DP_PHY_PENDING, DP_DELAY_NS);
        return;
    }
    if (value == 0x18 && s->phase == DP_PHY_READY && dp_capture_valid(s)) {
        /* dp_catalog_v420.c: late PHY reset has a source-required 2 ms wait. */
        dp_wait(s, DP_LATE_PHY_WAIT, DP_LATE_RESET_NS);
        return;
    }
    dp_cancel(s, false);
    if (!dp_supplied(s)) {
        return;
    }
    if (value == 1) {
        s->sequence = old_sequence == 2 ? 3 : 1;
    } else if (value == 5 && old_sequence == 1) {
        s->sequence = 2;
    } else if (value == 9 && old_sequence == 3) {
        s->sequence = 4;
    }
}

static void dp_reset_command(QcomSm8250DpPllState *s, uint32_t value)
{
    uint64_t rate = dp_recipe(s, s->regs);

    if (value != 0x20 || s->sequence != 4 || !dp_supplied(s) || !rate ||
        dp_value(s->regs, DP_REGION_PHY, DP_PHY_CFG) != 9) {
        dp_cancel(s, false);
        return;
    }
    memcpy(s->captured, s->regs, sizeof(s->captured));
    s->captured_xo_period = clock_get(s->input[0]);
    s->captured_rate_khz = rate;
    dp_wait(s, DP_PLL_PENDING, DP_DELAY_NS);
}

static MemTxResult dp_reject(DpPllWindow *w, hwaddr offset, const char *op,
                            unsigned size, const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DP_PLL
                  ": rejected %s %s+0x%03" HWADDR_PRIx " size %u (%s)\n",
                  op, dp_region_names[w->region], offset, size, reason);
    return MEMTX_ERROR;
}

static MemTxResult dp_read(void *opaque, hwaddr address, uint64_t *value,
                           unsigned size, MemTxAttrs attrs)
{
    DpPllWindow *w = opaque;
    QcomSm8250DpPllState *s = w->state;
    hwaddr offset = w->offset + address;
    int reg = dp_register(w->region, offset);

    if (!s->enabled_reference || size != 4 || (offset & 3) || reg < 0) {
        return dp_reject(w, offset, "read", size, "disabled-or-invalid-access");
    }
    if (dp_pll_regs[reg].kind == DP_REG_PLL_STATUS) {
        *value = dp_locked(s) ? (offset == DP_PLL_C_READY ? 1 : 3) : 0;
    } else if (dp_pll_regs[reg].kind == DP_REG_PHY_STATUS) {
        *value = s->phase == DP_PHY_READY ? 3 : 0;
    } else {
        *value = s->regs[reg];
    }
    return MEMTX_OK;
}

static MemTxResult dp_write(void *opaque, hwaddr address, uint64_t value,
                            unsigned size, MemTxAttrs attrs)
{
    DpPllWindow *w = opaque;
    QcomSm8250DpPllState *s = w->state;
    hwaddr offset = w->offset + address;
    int reg = dp_register(w->region, offset);
    const DpPllRegDef *d;
    bool changed;

    if (!s->enabled_reference || size != 4 || (offset & 3) || reg < 0) {
        return dp_reject(w, offset, "write", size, "disabled-or-invalid-access");
    }
    d = &dp_pll_regs[reg];
    if (d->kind != DP_REG_CONFIG) {
        return dp_reject(w, offset, "write", size, "read-only-status");
    }
    if (value & ~((uint64_t)d->write_mask)) {
        return dp_reject(w, offset, "write", size, "unsupported-bits");
    }
    changed = s->regs[reg] != value;
    if (changed && d->required &&
        !(w->region == DP_REGION_PHY && offset == DP_PHY_VCO_DIV)) {
        dp_cancel(s, false);
    }
    s->regs[reg] = value;
    s->written[reg] = s->power_known && s->power_on;
    if (w->region == DP_REGION_PHY && offset == DP_PHY_CFG) {
        dp_phy_command(s, value);
    } else if (w->region == DP_REGION_PLL && offset == DP_PLL_RESET) {
        dp_reset_command(s, value);
    } else if (w->region == DP_REGION_PHY && offset == DP_PHY_PD_CTL &&
               value == 2) {
        dp_cancel(s, false);
    }
    dp_publish(s, true);
    return MEMTX_OK;
}

static const MemoryRegionOps dp_ops = {
    .read_with_attrs = dp_read,
    .write_with_attrs = dp_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Preserve the width presented within one leaf for callback rejection. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void dp_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);

    s->ready = false;
    dp_cancel(s, true);
    memset(s->regs, 0, sizeof(s->regs));
    s->last_xo_period = clock_get(s->input[0]);
}

static void dp_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);

    s->ready = true;
    dp_publish(s, true);
}

static int dp_pre_load(void *opaque)
{
    QcomSm8250DpPllState *s = opaque;
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    for (i = 0; i < DP_PLL_CLOCKS; i++) {
        clock_set(s->clocks[i], 0);
    }
    return 0;
}

static int dp_bad_state(const char *category)
{
    error_report(TYPE_QCOM_SM8250_DP_PLL ": invalid migrated %s", category);
    return -EINVAL;
}

static bool dp_provider_matches(QcomSm8250DpPllState *s)
{
    Error *err = NULL;
    bool known, powered;

    if (!s->mdss_provider) {
        return !s->power_known && !s->power_on;
    }
    known = object_property_get_bool(OBJECT(s->mdss_provider),
                                     "domain0.state-known", &err);
    if (err) {
        error_free(err);
        return false;
    }
    powered = object_property_get_bool(OBJECT(s->mdss_provider),
                                       "domain0.powered", &err);
    if (err) {
        error_free(err);
        return false;
    }
    return s->power_known == known && s->power_on == (known && powered);
}

static int dp_post_load(void *opaque, int version_id)
{
    QcomSm8250DpPllState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    uint32_t cfg = dp_value(s->regs, DP_REGION_PHY, DP_PHY_CFG);
    bool pending;
    unsigned i;

    if (now < 0 || s->power_known > 1 || s->power_on > 1 ||
        s->input[0]->multiplier != 1 || s->input[0]->divider != 1 ||
        s->last_xo_period != clock_get(s->input[0]) ||
        (clock_has_source(s->input[0]) &&
         clock_get(s->input[0]) != clock_get(s->input[0]->source))) {
        return dp_bad_state("reference clock");
    }
    /*
     * An enabled consumer must match the real restored supplier. The disabled
     * profile has no carrier; legacy MDSS QTest output interception can
     * intentionally leave its inactive cached GPIO level disconnected.
     */
    if (s->enabled_reference && !dp_provider_matches(s)) {
        return dp_bad_state("MDSS provider");
    }
    for (i = 0; i < DP_PLL_REGISTERS; i++) {
        const DpPllRegDef *d = &dp_pll_regs[i];

        if ((s->regs[i] & ~d->write_mask) ||
            (s->captured[i] & ~d->write_mask)) {
            return dp_bad_state("register");
        }
        if (s->written[i] > 1 ||
            (!(s->power_known && s->power_on) && s->written[i]) ||
            (d->kind != DP_REG_CONFIG && s->written[i])) {
            return dp_bad_state("written");
        }
        if (!s->enabled_reference && (s->regs[i] || s->written[i])) {
            return dp_bad_state("disabled state");
        }
    }
    if (s->phase > DP_LATE_PHY_WAIT || s->sequence > 4) {
        return dp_bad_state("transition");
    }
    if (s->phase == DP_IDLE) {
        if (s->deadline != -1 || s->captured_xo_period ||
            s->captured_rate_khz ||
            (s->sequence && (!dp_supplied(s) ||
              !s->written[dp_register(DP_REGION_PHY, DP_PHY_CFG)] ||
              cfg != (s->sequence == 2 ? 5 : s->sequence == 4 ? 9 : 1)))) {
            return dp_bad_state("transition");
        }
        for (i = 0; i < DP_PLL_REGISTERS; i++) {
            if (s->captured[i]) {
                return dp_bad_state("capture");
            }
        }
    } else {
        int64_t maximum = s->phase == DP_LATE_PHY_WAIT ?
                          DP_LATE_RESET_NS : DP_DELAY_NS;

        if (!dp_capture_valid(s) || s->sequence != 4) {
            return dp_bad_state("capture");
        }
        if ((s->phase == DP_PLL_PENDING && cfg != 9) ||
            (s->phase == DP_PLL_LOCKED && cfg != 9 && cfg != 0x18) ||
            ((s->phase == DP_PHY_PENDING || s->phase == DP_PHY_READY) &&
             cfg != 0x19) ||
            (s->phase == DP_LATE_PHY_WAIT && cfg != 0x18) ||
            dp_value(s->regs, DP_REGION_PLL, DP_PLL_RESET) != 0x20) {
            return dp_bad_state("transition");
        }
        pending = s->phase == DP_PLL_PENDING || s->phase == DP_PHY_PENDING ||
                  s->phase == DP_LATE_PHY_WAIT;
        if ((pending && (s->deadline < now ||
                         s->deadline - now > maximum)) ||
            (!pending && s->deadline != -1)) {
            return dp_bad_state("transition");
        }
    }
    s->updating = false;
    s->ready = true;
    dp_publish(s, false);
    if (s->deadline >= 0) {
        timer_mod_ns(s->timer, s->deadline);
    }
    return 0;
}

static const VMStateDescription vmstate_dp_pll = {
    .name = TYPE_QCOM_SM8250_DP_PLL,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = dp_pre_load,
    .post_load = dp_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(region_count, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(register_count, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(window_count, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(clock_count, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(input_count, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(timer_count, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(ccf_rate_scale, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(pll_delay_cycles, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(phy_delay_cycles, QcomSm8250DpPllState),
        VMSTATE_UINT32_EQUAL(region_bytes, QcomSm8250DpPllState),
        VMSTATE_UINT64_EQUAL(physical_base[0], QcomSm8250DpPllState),
        VMSTATE_UINT64_EQUAL(physical_base[1], QcomSm8250DpPllState),
        VMSTATE_UINT64_EQUAL(physical_base[2], QcomSm8250DpPllState),
        VMSTATE_UINT64_EQUAL(physical_base[3], QcomSm8250DpPllState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250DpPllState, DP_PLL_REGISTERS),
        VMSTATE_UINT8_ARRAY(written, QcomSm8250DpPllState, DP_PLL_REGISTERS),
        VMSTATE_UINT32_ARRAY(captured, QcomSm8250DpPllState, DP_PLL_REGISTERS),
        VMSTATE_UINT64(captured_xo_period, QcomSm8250DpPllState),
        VMSTATE_UINT64(last_xo_period, QcomSm8250DpPllState),
        VMSTATE_INT64(deadline, QcomSm8250DpPllState),
        VMSTATE_UINT32(phase, QcomSm8250DpPllState),
        VMSTATE_UINT32(sequence, QcomSm8250DpPllState),
        VMSTATE_UINT64(captured_rate_khz, QcomSm8250DpPllState),
        VMSTATE_UINT8(power_known, QcomSm8250DpPllState),
        VMSTATE_UINT8(power_on, QcomSm8250DpPllState),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250DpPllState, DP_PLL_INPUTS),
        VMSTATE_END_OF_LIST()
    },
};

static void dp_bool_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    bool value = *(uint8_t *)opaque;

    visit_type_bool(v, name, &value, errp);
}

static void dp_signed_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    int64_t value = *(int64_t *)opaque;

    visit_type_int64(v, name, &value, errp);
}

static void dp_status_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);
    bool value;

    switch (GPOINTER_TO_UINT(opaque)) {
    case 0: value = dp_locked(s); break;
    case 1: value = s->phase == DP_PHY_READY; break;
    case 2: value = s->phase == DP_PLL_PENDING; break;
    case 3: value = s->phase == DP_PHY_PENDING; break;
    case 4: value = s->phase == DP_LATE_PHY_WAIT; break;
    case 5: value = dp_recipe(s, s->regs) != 0; break;
    case 6: dp_raw_rate(s->regs, &value); break;
    default: g_assert_not_reached();
    }
    visit_type_bool(v, name, &value, errp);
}

static void dp_raw_rate_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);
    bool known;
    uint64_t value = dp_raw_rate(s->regs, &known);

    visit_type_uint64(v, name, &value, errp);
}

static void dp_lane_get(Object *obj, Visitor *v, const char *name,
                        void *opaque, Error **errp)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);
    uint32_t spare = dp_value(s->regs, DP_REGION_PHY, DP_PHY_SPARE0);
    uint32_t value = GPOINTER_TO_UINT(opaque) ? spare >> 4 : spare & 15;

    visit_type_uint32(v, name, &value, errp);
}

static void dp_clock_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);
    unsigned index = GPOINTER_TO_UINT(opaque), id = index / 2;
    uint64_t value = clock_get(id == DP_PLL_CLOCKS ?
                               s->input[0] : s->clocks[id]);

    if (!(index & 1)) {
        value = CLOCK_PERIOD_TO_HZ(value);
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dp_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(dev);

    s->reference_profile = s->enabled_reference;
    s->last_xo_period = clock_get(s->input[0]);
    s->ready = true;
    dp_publish(s, false);
}

static void dp_init(Object *obj)
{
    QcomSm8250DpPllState *s = QCOM_SM8250_DP_PLL(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const metadata[] = {
        "enabled-profile", "reference-identity", "region-count",
        "register-count", "sparse-window-count", "mapped-bytes",
        "output-clock-count", "input-clock-count", "timer-count",
        "ccf-rate-scale", "pll-delay-xo-cycles", "phy-delay-xo-cycles",
        "resource-bytes-per-region",
    };
    uint32_t *values[] = {
        &s->reference_profile, &s->reference_identity, &s->region_count,
        &s->register_count, &s->window_count, &s->mapped_bytes,
        &s->clock_count, &s->input_count, &s->timer_count, &s->ccf_rate_scale,
        &s->pll_delay_cycles, &s->phy_delay_cycles, &s->region_bytes,
    };
    static const uint32_t initial[] = {
        0, DP_PLL_REFERENCE_ID, DP_PLL_REGIONS, DP_PLL_REGISTERS,
        DP_PLL_WINDOWS, DP_PLL_MAPPED_BYTES, DP_PLL_CLOCKS, DP_PLL_INPUTS,
        1, DP_PLL_CCF_RATE_SCALE, DP_PLL_REFERENCE_CYCLES,
        DP_PLL_REFERENCE_CYCLES, DP_PLL_REGION_BYTES,
    };
    static const char *const statuses[] = {
        "pll-locked", "phy-ready", "pll-pending", "phy-pending",
        "late-phy-pending", "recipe-valid", "raw-rate-known",
    };
    unsigned i;

    s->deadline = -1;
    for (i = 0; i < ARRAY_SIZE(values); i++) {
        *values[i] = initial[i];
        object_property_add_uint32_ptr(obj, metadata[i], values[i],
                                       OBJ_PROP_FLAG_READ);
    }
    object_property_add_uint32_ptr(obj, "phase", &s->phase, OBJ_PROP_FLAG_READ);
    object_property_add_uint32_ptr(obj, "sequence-index", &s->sequence,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "captured-xo-period",
                                   &s->captured_xo_period, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "last-xo-period", &s->last_xo_period,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "captured-rate-khz",
                                   &s->captured_rate_khz, OBJ_PROP_FLAG_READ);
    object_property_add(obj, "deadline-ns", "int64", dp_signed_get,
                        NULL, NULL, &s->deadline);
    object_property_add(obj, "power-known", "bool", dp_bool_get,
                        NULL, NULL, &s->power_known);
    object_property_add(obj, "power-on", "bool", dp_bool_get,
                        NULL, NULL, &s->power_on);
    object_property_add(obj, "raw-ccf-rate-khz", "uint64", dp_raw_rate_get,
                        NULL, NULL, NULL);
    object_property_add(obj, "lane-count", "uint32", dp_lane_get,
                        NULL, NULL, GUINT_TO_POINTER(0));
    object_property_add(obj, "orientation", "uint32", dp_lane_get,
                        NULL, NULL, GUINT_TO_POINTER(1));
    for (i = 0; i < ARRAY_SIZE(statuses); i++) {
        object_property_add(obj, statuses[i], "bool", dp_status_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < DP_PLL_REGIONS; i++) {
        g_autofree char *name = g_strdup_printf("%s.%s",
                          TYPE_QCOM_SM8250_DP_PLL, dp_region_names[i]);
        g_autofree char *property = g_strdup_printf("region%u.physical-base", i);

        s->physical_base[i] = qcom_sm8250_dp_pll_bases[i];
        object_property_add_uint64_ptr(obj, property, &s->physical_base[i],
                                       OBJ_PROP_FLAG_READ);
        memory_region_init(&s->container[i], obj, name, DP_PLL_REGION_BYTES);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container[i]);
    }
    for (i = 0; i < DP_PLL_WINDOWS; i++) {
        const DpPllWindowDef *d = &dp_pll_windows[i];
        g_autofree char *name = g_strdup_printf("%s.%s.%03x",
                     TYPE_QCOM_SM8250_DP_PLL, dp_region_names[d->region],
                     d->offset);

        s->windows[i].state = s;
        s->windows[i].region = d->region;
        s->windows[i].offset = d->offset;
        memory_region_init_io(&s->iomem[i], obj, &dp_ops, &s->windows[i],
                              name, d->size);
        memory_region_add_subregion(&s->container[d->region], d->offset,
                                     &s->iomem[i]);
    }
    for (i = 0; i < DP_PLL_REGISTERS; i++) {
        const DpPllRegDef *d = &dp_pll_regs[i];
        g_autofree char *reg = g_strdup_printf("reg.%s.%03x",
                                 dp_region_names[d->region], d->offset);
        g_autofree char *captured = g_strdup_printf("captured.%s.%03x",
                                 dp_region_names[d->region], d->offset);
        g_autofree char *written = g_strdup_printf("written.%s.%03x",
                                 dp_region_names[d->region], d->offset);

        object_property_add_uint32_ptr(obj, reg, &s->regs[i], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, captured, &s->captured[i],
                                       OBJ_PROP_FLAG_READ);
        object_property_add(obj, written, "bool", dp_bool_get,
                            NULL, NULL, &s->written[i]);
    }
    s->input[0] = qdev_init_clock_in(dev, "ref_clk_src", dp_clock_changed,
                                    s, ClockUpdate);
    for (i = 0; i <= DP_PLL_CLOCKS; i++) {
        const char *name = i == DP_PLL_CLOCKS ? "ref_clk_src" :
                                                dp_pll_clock_names[i];
        unsigned j;

        if (i < DP_PLL_CLOCKS) {
            s->clocks[i] = qdev_init_clock_out(dev, name);
        }
        for (j = 0; j < 2; j++) {
            g_autofree char *property = g_strdup_printf("%s.%s.%s",
                i == DP_PLL_CLOCKS ? "input" : "clk", name, j ? "period" : "hz");

            object_property_add(obj, property, "uint64", dp_clock_get,
                                NULL, NULL, GUINT_TO_POINTER(i * 2 + j));
        }
    }
    qdev_init_gpio_in_named(dev, dp_power_known, "power-known", 1);
    qdev_init_gpio_in_named(dev, dp_power_on, "power-on", 1);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dp_timer, s);
}

static void dp_finalize(Object *obj)
{
    timer_free(QCOM_SM8250_DP_PLL(obj)->timer);
}

static const Property dp_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250DpPllState,
                     enabled_reference, false),
    DEFINE_PROP_LINK("mdss-provider", QcomSm8250DpPllState,
                     mdss_provider, TYPE_DEVICE, DeviceState *),
};

static void dp_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 non-V2 DP 7nm digital PLL reference";
    dc->realize = dp_realize;
    dc->vmsd = &vmstate_dp_pll;
    device_class_set_props(dc, dp_properties);
    rc->phases.enter = dp_reset_enter;
    rc->phases.hold = dp_reset_hold;
}

static const TypeInfo dp_info = {
    .name = TYPE_QCOM_SM8250_DP_PLL,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250DpPllState),
    .instance_init = dp_init,
    .instance_finalize = dp_finalize,
    .class_init = dp_class_init,
};

static void dp_register_types(void)
{
    type_register_static(&dp_info);
}
type_init(dp_register_types)
