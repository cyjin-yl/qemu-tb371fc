/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Source-qualified SM8250 DSI 7nm v4.1 PLL/shared-PHY digital reference.
 * See tb371fc/docs/dsi-pll7nm-sm8250.md for source pins and boundaries.
 * This does not model analog calibration, lane signaling, panel execution,
 * bootloader handoff, unknown REFGEN providers, or dynamic-refresh completion.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-dsi-pll7nm.h"
#include "hw/misc/qcom-mdss-gdsc.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/irq.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "system/qtest.h"
#include "system/runstate.h"

enum { REG_PHY, REG_PLL, REG_LOCK, REG_UNKNOWN_ZERO };
enum { C_VCO, C_OUT, C_BIT, C_POST_BIT, C_POST_VCO, C_CPHY_POST,
       C_BYTE, C_PIXEL };

typedef struct DsiPll7nmRegister {
    uint32_t offset, write_mask;
    unsigned kind;
    int32_t recipe;
} DsiPll7nmRegister;

typedef struct DsiPll7nmWindowDef {
    uint32_t offset, size;
} DsiPll7nmWindowDef;

typedef struct DsiPll7nmRecipeBucket {
    uint64_t minimum, maximum;
    uint32_t inverters, vco_config, analog_five;
} DsiPll7nmRecipeBucket;

#include "qcom-sm8250-dsi-pll7nm-data.inc"

#define DREG(s, p, r) ((s)->regs[(p) * DSI_PLL7NM_REGISTERS_PER_BANK + (r)])
#define REF_HZ UINT64_C(19200000)
#define FACTOR_UNIT UINT64_C(262144)
#define FACTOR_HZ (2 * REF_HZ)

static uint64_t dsi_ceil_div(uint64_t numerator, uint64_t divisor)
{
    return numerator / divisor + !!(numerator % divisor);
}

static uint32_t dsi_factor(QcomSm8250DsiPll7nmState *s, unsigned p)
{
    return (DREG(s, p, R_PLL_DECIMAL_DIV_START_1) << 18) |
           (DREG(s, p, R_PLL_FRAC_DIV_START_HIGH_1) << 16) |
           (DREG(s, p, R_PLL_FRAC_DIV_START_MID_1) << 8) |
           DREG(s, p, R_PLL_FRAC_DIV_START_LOW_1);
}

static bool dsi_cphy(QcomSm8250DsiPll7nmState *s, unsigned p)
{
    return DREG(s, p, R_PHY_CMN_GLBL_CTRL) & BIT(6);
}

static bool dsi_rate_recipe(QcomSm8250DsiPll7nmState *s, unsigned p,
                            uint32_t factor)
{
    uint64_t low = dsi_ceil_div((uint64_t)factor * FACTOR_HZ, FACTOR_UNIT);
    uint64_t high = dsi_ceil_div(((uint64_t)factor + 1) * FACTOR_HZ,
                               FACTOR_UNIT) - 1;
    unsigned i;

    /* The driver chooses the recipe before floor-quantizing DEC/FRAC. */
    for (i = 0; i < ARRAY_SIZE(dsi_recipe_buckets); i++) {
        const DsiPll7nmRecipeBucket *b = &dsi_recipe_buckets[i];

        if (low <= b->maximum && high >= b->minimum &&
            DREG(s, p, R_PLL_CLOCK_INVERTERS_1) == b->inverters &&
            DREG(s, p, R_PLL_VCO_CONFIG_1) == b->vco_config &&
            DREG(s, p, R_PLL_ANALOG_CONTROLS_FIVE_1) == b->analog_five) {
            return true;
        }
    }
    return false;
}

static bool dsi_ssc_recipe(QcomSm8250DsiPll7nmState *s, unsigned p,
                           uint32_t factor)
{
    uint32_t control = DREG(s, p, R_PLL_SSC_CONTROL);
    uint32_t step = DREG(s, p, R_PLL_SSC_STEPSIZE_LOW_1) |
                    (DREG(s, p, R_PLL_SSC_STEPSIZE_HIGH_1) << 8);
    uint32_t period = DREG(s, p, R_PLL_SSC_DIV_PER_LOW_1) |
                      (DREG(s, p, R_PLL_SSC_DIV_PER_HIGH_1) << 8);
    uint32_t adjust = DREG(s, p, R_PLL_SSC_ADJPER_LOW_1) |
                      (DREG(s, p, R_PLL_SSC_ADJPER_HIGH_1) << 8);
    uint64_t expected = ((uint64_t)factor * 4800 * 3) / 303;

    if (!control) {
        return !step && !period && !adjust;
    }
    /* Only the source defaults, with down/center spread, are qualified. */
    expected = (expected + 500000) / 1000000;
    return (control == 2 || control == 3) && period == 302 && adjust == 2 &&
           step == expected;
}

static bool dsi_recipe(QcomSm8250DsiPll7nmState *s, unsigned p)
{
    uint32_t factor = dsi_factor(s, p);
    unsigned i;

    for (i = 0; i < DSI_PLL7NM_REGISTERS_PER_BANK; i++) {
        if (dsi_registers[i].recipe >= 0 &&
            DREG(s, p, i) != dsi_registers[i].recipe) {
            return false;
        }
    }
    return DREG(s, p, R_PLL_CMODE_1) == (dsi_cphy(s, p) ? 0 : 0x10) &&
           dsi_rate_recipe(s, p, factor) && dsi_ssc_recipe(s, p, factor);
}

static bool dsi_qualified(QcomSm8250DsiPll7nmState *s, unsigned p)
{
    return s->enabled_reference && s->power_known && s->power_on &&
           s->refgen_known[p] && s->refgen_ready[p] &&
           CLOCK_PERIOD_TO_HZ(clock_get(s->input[0])) == REF_HZ &&
           (DREG(s, p, R_PHY_CMN_PLL_CNTRL) & 1) &&
           (DREG(s, p, R_PHY_CMN_CTRL_0) & BIT(5)) &&
           DREG(s, p, R_PLL_SYSTEM_MUXES) == 0xc0 && dsi_recipe(s, p);
}

static bool dsi_route_known(QcomSm8250DsiPll7nmState *s, unsigned p)
{
    uint32_t cfg = DREG(s, p, R_PHY_CMN_CLK_CFG1);

    /* The external selector is observed, but its physical tap is unproven. */
    return !(cfg & 0x0c) &&
           (dsi_cphy(s, p) ? (cfg & 3) == 3 : (cfg & 3) <= 1);
}

static void dsi_rates(QcomSm8250DsiPll7nmState *s,
                     uint64_t rates[DSI_PLL7NM_CLOCKS])
{
    unsigned p;

    memset(rates, 0, sizeof(uint64_t) * DSI_PLL7NM_CLOCKS);
    for (p = 0; p < DSI_PLL7NM_BANKS; p++) {
        uint64_t *r = &rates[p * DSI_PLL7NM_CLOCKS_PER_BANK];
        uint32_t cfg0 = DREG(s, p, R_PHY_CMN_CLK_CFG0);
        uint32_t cfg1 = DREG(s, p, R_PHY_CMN_CLK_CFG1);
        uint32_t bit_div = cfg0 & 15, pixel_div = (cfg0 >> 4) & 15;
        uint64_t pixel;

        if (!s->locked[p] || !dsi_qualified(s, p)) {
            continue;
        }
        r[C_VCO] = FACTOR_HZ * s->active_factor[p] / FACTOR_UNIT;
        r[C_OUT] = dsi_ceil_div(r[C_VCO],
                    1U << DREG(s, p, R_PLL_PLL_OUTDIV_RATE));
        if (cfg1 & 0x0c) {
            continue;
        }
        /* ONE_BASED | ALLOW_ZERO: zero is a parent-rate bypass. */
        r[C_BIT] = dsi_ceil_div(r[C_OUT], bit_div ? bit_div : 1);
        r[C_POST_BIT] = r[C_BIT] / 2;
        r[C_POST_VCO] = r[C_OUT] / 4;
        r[C_CPHY_POST] = r[C_OUT] * 2 / 7;
        /* CLK_EN_SEL bit4 is cleared by the PHY resync operation. */
        if (!(cfg1 & BIT(5)) ||
            DREG(s, p, R_PHY_CMN_CTRL_3) != 4 ||
            DREG(s, p, R_PHY_CMN_GLBL_DIGTOP_SPARE4)) {
            continue;
        }
        r[C_BYTE] = r[C_BIT] / (dsi_cphy(s, p) ? DSI_CPHY_BYTE_DIV :
                                               DSI_DPHY_BYTE_DIV);
        if (!dsi_route_known(s, p)) {
            continue;
        }
        pixel = dsi_cphy(s, p) ? r[C_CPHY_POST] :
                (cfg1 & 1) ? r[C_POST_BIT] : r[C_BIT];
        r[C_PIXEL] = dsi_ceil_div(pixel, pixel_div ? pixel_div : 1);
    }
}

static void dsi_publish(QcomSm8250DsiPll7nmState *s, bool propagate)
{
    uint64_t rates[DSI_PLL7NM_CLOCKS];
    unsigned i;

    dsi_rates(s, rates);
    for (i = 0; i < DSI_PLL7NM_CLOCKS; i++) {
        if (clock_set(s->clocks[i], CLOCK_PERIOD_FROM_HZ(rates[i])) &&
            propagate) {
            clock_propagate(s->clocks[i]);
        }
    }
}

static void dsi_invalidate(QcomSm8250DsiPll7nmState *s, unsigned p)
{
    s->locked[p] = false;
    s->active_factor[p] = s->pending_factor[p] = 0;
    s->lock_deadline[p] = -1;
}

static void dsi_schedule(QcomSm8250DsiPll7nmState *s)
{
    int64_t nearest = -1;
    unsigned p;

    for (p = 0; p < DSI_PLL7NM_BANKS; p++) {
        if (s->lock_deadline[p] >= 0 &&
            (nearest < 0 || s->lock_deadline[p] < nearest)) {
            nearest = s->lock_deadline[p];
        }
    }
    if (nearest < 0) {
        timer_del(s->timer);
    } else {
        timer_mod(s->timer, nearest);
    }
}

static void dsi_update(QcomSm8250DsiPll7nmState *s)
{
    int64_t now;
    uint64_t xo;
    unsigned p;

    /*
     * A provider's post_load may call us before our own pre_load. Keep its
     * input observations, but never propagate clocks or consume deadlines
     * while restoring either an incoming migration or an in-process snapshot.
     */
    if (!s->ready || s->updating || runstate_check(RUN_STATE_INMIGRATE) ||
        runstate_check(RUN_STATE_RESTORE_VM)) {
        return;
    }
    s->updating = true;
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    xo = CLOCK_PERIOD_TO_HZ(clock_get(s->input[0]));
    for (p = 0; p < DSI_PLL7NM_BANKS; p++) {
        if (xo != s->last_xo_hz) {
            dsi_invalidate(s, p);
        }
        if (!dsi_qualified(s, p)) {
            dsi_invalidate(s, p);
        } else if (!s->locked[p]) {
            if (s->lock_deadline[p] < 0) {
                s->pending_factor[p] = dsi_factor(s, p);
                s->lock_deadline[p] = now + DSI_PLL7NM_LOCK_NS;
            } else if (now >= s->lock_deadline[p]) {
                s->active_factor[p] = s->pending_factor[p];
                s->locked[p] = true;
                s->lock_deadline[p] = -1;
            }
        }
    }
    s->last_xo_hz = xo;
    dsi_publish(s, true);
    dsi_schedule(s);
    s->updating = false;
}

static void dsi_timer(void *opaque)
{
    dsi_update(opaque);
}

static void dsi_clock_changed(void *opaque, ClockEvent event)
{
    dsi_update(opaque);
}

static int dsi_register(hwaddr offset)
{
    unsigned lo = 0, hi = DSI_PLL7NM_REGISTERS_PER_BANK;

    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;

        if (dsi_registers[mid].offset < offset) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo < DSI_PLL7NM_REGISTERS_PER_BANK &&
           dsi_registers[lo].offset == offset ? lo : -1;
}

static MemTxResult dsi_reject(DsiPll7nmWindow *w, hwaddr offset,
                             const char *operation, const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DSI_PLL7NM
                  ": rejected %s bank=%u %s+0x%03" HWADDR_PRIx " (%s)\n",
                  operation, w->bank, w->dynamic ? "dynamic" : "phy",
                  offset, reason);
    return MEMTX_ERROR;
}

static MemTxResult dsi_read(void *opaque, hwaddr address, uint64_t *value,
                           unsigned size, MemTxAttrs attrs)
{
    DsiPll7nmWindow *w = opaque;
    QcomSm8250DsiPll7nmState *s = w->state;
    hwaddr offset = w->offset + address;
    int reg = dsi_register(offset);

    if (!s->enabled_reference || size != 4 || (offset & 3)) {
        return dsi_reject(w, offset, "read", "disabled-or-invalid-access");
    }
    if (w->dynamic) {
        if (offset) {
            return dsi_reject(w, offset, "read", "unknown-dynamic-word");
        }
        *value = 0;
        return MEMTX_OK;
    }
    if (reg < 0 || dsi_registers[reg].kind == REG_UNKNOWN_ZERO) {
        return dsi_reject(w, offset, "read", "unknown-status-or-word");
    }
    dsi_update(s);
    *value = dsi_registers[reg].kind == REG_LOCK ? s->locked[w->bank] :
             DREG(s, w->bank, reg);
    return MEMTX_OK;
}

static MemTxResult dsi_write(void *opaque, hwaddr address, uint64_t value,
                            unsigned size, MemTxAttrs attrs)
{
    DsiPll7nmWindow *w = opaque;
    QcomSm8250DsiPll7nmState *s = w->state;
    hwaddr offset = w->offset + address;
    int reg = dsi_register(offset);
    const DsiPll7nmRegister *r;

    if (!s->enabled_reference || size != 4 || (offset & 3)) {
        return dsi_reject(w, offset, "write", "disabled-or-invalid-access");
    }
    if (w->dynamic) {
        return !offset && !value ? MEMTX_OK :
               dsi_reject(w, offset, "write", "unsupported-dynamic-trigger");
    }
    if (reg < 0) {
        return dsi_reject(w, offset, "write", "unknown-word");
    }
    r = &dsi_registers[reg];
    if (value & ~((uint64_t)r->write_mask)) {
        return dsi_reject(w, offset, "write", "unsupported-bits");
    }
    /* Source initialization writes zero to status words. It is not an ACK. */
    if (r->kind == REG_LOCK || r->kind == REG_UNKNOWN_ZERO) {
        return MEMTX_OK;
    }
    if (DREG(s, w->bank, reg) != value) {
        if ((r->kind == REG_PLL && reg != R_PLL_PLL_OUTDIV_RATE) ||
            reg == R_PHY_CMN_GLBL_CTRL) {
            dsi_invalidate(s, w->bank);
        }
        DREG(s, w->bank, reg) = value;
    }
    dsi_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps dsi_ops = {
    .read_with_attrs = dsi_read,
    .write_with_attrs = dsi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void dsi_power_known(void *opaque, int index, int level)
{
    QcomSm8250DsiPll7nmState *s = opaque;

    s->power_known = !!level;
    dsi_update(s);
}

static void dsi_power_on(void *opaque, int index, int level)
{
    QcomSm8250DsiPll7nmState *s = opaque;

    s->power_on = !!level;
    dsi_update(s);
}

static void dsi_refgen_known(void *opaque, int index, int level)
{
    QcomSm8250DsiPll7nmState *s = opaque;

    s->refgen_known[index] = !!level;
    if (!level) {
        s->refgen_ready[index] = false;
    }
    dsi_update(s);
}

static void dsi_refgen_ready(void *opaque, int index, int level)
{
    QcomSm8250DsiPll7nmState *s = opaque;

    s->refgen_ready[index] = s->refgen_known[index] && !!level;
    dsi_update(s);
}

static void dsi_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);
    unsigned p;

    s->ready = false;
    s->updating = false;
    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    for (p = 0; p < DSI_PLL7NM_BANKS; p++) {
        dsi_invalidate(s, p);
    }
    s->last_xo_hz = CLOCK_PERIOD_TO_HZ(clock_get(s->input[0]));
    /* The power and REFGEN observations belong to their external providers. */
}

static void dsi_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);

    s->ready = true;
    dsi_publish(s, true);
}

static int dsi_pre_load(void *opaque)
{
    QcomSm8250DsiPll7nmState *s = opaque;
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    for (i = 0; i < DSI_PLL7NM_CLOCKS; i++) {
        clock_set(s->clocks[i], 0);
    }
    return 0;
}

static int dsi_bad_state(const char *what, unsigned index)
{
    error_report(TYPE_QCOM_SM8250_DSI_PLL7NM ": invalid migrated %s %u",
                 what, index);
    return -EINVAL;
}

static bool dsi_provider_matches(QcomSm8250DsiPll7nmState *s)
{
    Error *local_err = NULL;
    bool known, on;

    if (!s->mdss_provider) {
        return !s->power_known && !s->power_on;
    }
    known = object_property_get_bool(OBJECT(s->mdss_provider),
                                      "domain0.state-known", &local_err);
    if (local_err) {
        error_free(local_err);
        return false;
    }
    on = object_property_get_bool(OBJECT(s->mdss_provider),
                                   "domain0.powered", &local_err);
    if (local_err) {
        error_free(local_err);
        return false;
    }
    return s->power_known == known && s->power_on == (known && on);
}

static int dsi_post_load(void *opaque, int version_id)
{
    QcomSm8250DsiPll7nmState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned p, i;

    if (now < 0 || s->input[0]->multiplier != 1 ||
        s->input[0]->divider != 1 ||
        CLOCK_PERIOD_TO_HZ(clock_get(s->input[0])) > UINT32_MAX ||
        s->last_xo_hz != CLOCK_PERIOD_TO_HZ(clock_get(s->input[0])) ||
        (clock_has_source(s->input[0]) &&
         clock_get(s->input[0]) != clock_get(s->input[0]->source))) {
        return dsi_bad_state("XO input", 0);
    }
    if (!dsi_provider_matches(s)) {
        return dsi_bad_state("MDSS provider", 0);
    }
    for (p = 0; p < DSI_PLL7NM_BANKS; p++) {
        bool qualified;
        uint32_t factor;
        int64_t deadline = s->lock_deadline[p];

        for (i = 0; i < DSI_PLL7NM_REGISTERS_PER_BANK; i++) {
            if ((DREG(s, p, i) & ~dsi_registers[i].write_mask) ||
                (!s->enabled_reference && DREG(s, p, i))) {
                return dsi_bad_state("register", p * 154 + i);
            }
        }
        if (s->refgen_ready[p] && !s->refgen_known[p]) {
            return dsi_bad_state("unknown REFGEN", p);
        }
        qualified = dsi_qualified(s, p);
        factor = dsi_factor(s, p);
        if (deadline < -1 ||
            (deadline > now && deadline - now > DSI_PLL7NM_LOCK_NS) ||
            (!qualified && (s->locked[p] || s->active_factor[p] ||
                           s->pending_factor[p] || deadline >= 0)) ||
            (qualified && s->pending_factor[p] != factor) ||
            (s->locked[p] && (deadline >= 0 || s->active_factor[p] != factor)) ||
            (!s->locked[p] && (s->active_factor[p] ||
                              (qualified && deadline < 0)))) {
            return dsi_bad_state("PLL transition", p);
        }
    }
    s->updating = false;
    s->ready = true;
    /* No clock callbacks or deadline consumption during migration. */
    dsi_publish(s, false);
    dsi_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_dsi = {
    .name = TYPE_QCOM_SM8250_DSI_PLL7NM,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = dsi_pre_load,
    .post_load = dsi_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(bank_count, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(registers_per_bank, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(register_count, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(clock_count, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(input_count, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(window_count, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomSm8250DsiPll7nmState),
        VMSTATE_UINT64_EQUAL(physical_base[0], QcomSm8250DsiPll7nmState),
        VMSTATE_UINT64_EQUAL(physical_base[1], QcomSm8250DsiPll7nmState),
        VMSTATE_UINT64_EQUAL(dynamic_base[0], QcomSm8250DsiPll7nmState),
        VMSTATE_UINT64_EQUAL(dynamic_base[1], QcomSm8250DsiPll7nmState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250DsiPll7nmState, DSI_PLL7NM_REGISTERS),
        VMSTATE_UINT32_ARRAY(active_factor, QcomSm8250DsiPll7nmState, 2),
        VMSTATE_UINT32_ARRAY(pending_factor, QcomSm8250DsiPll7nmState, 2),
        VMSTATE_INT64_ARRAY(lock_deadline, QcomSm8250DsiPll7nmState, 2),
        VMSTATE_UINT64(last_xo_hz, QcomSm8250DsiPll7nmState),
        VMSTATE_BOOL_ARRAY(locked, QcomSm8250DsiPll7nmState, 2),
        VMSTATE_BOOL(power_known, QcomSm8250DsiPll7nmState),
        VMSTATE_BOOL(power_on, QcomSm8250DsiPll7nmState),
        VMSTATE_BOOL_ARRAY(refgen_known, QcomSm8250DsiPll7nmState, 2),
        VMSTATE_BOOL_ARRAY(refgen_ready, QcomSm8250DsiPll7nmState, 2),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250DsiPll7nmState, 1),
        VMSTATE_END_OF_LIST()
    },
};

static void dsi_bool_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    bool value = *(bool *)opaque;

    visit_type_bool(v, name, &value, errp);
}

static void dsi_signed_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    int64_t value = *(int64_t *)opaque;

    visit_type_int64(v, name, &value, errp);
}

static void dsi_status_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);
    unsigned id = GPOINTER_TO_UINT(opaque), p = id / 4;
    bool value;

    switch (id % 4) {
    case 0: value = s->lock_deadline[p] >= 0; break;
    case 1: value = dsi_recipe(s, p); break;
    case 2: value = dsi_cphy(s, p); break;
    case 3: value = dsi_route_known(s, p); break;
    default: g_assert_not_reached();
    }
    visit_type_bool(v, name, &value, errp);
}

static void dsi_clock_get(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);
    unsigned index = GPOINTER_TO_UINT(opaque), id = index / 2;
    uint64_t rates[DSI_PLL7NM_CLOCKS], value;

    if (id == DSI_PLL7NM_CLOCKS) {
        value = clock_get(s->input[0]);
        if (!(index & 1)) {
            value = CLOCK_PERIOD_TO_HZ(value);
        }
    } else if (index & 1) {
        value = clock_get(s->clocks[id]);
    } else {
        dsi_rates(s, rates);
        value = rates[id];
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dsi_refgen_fixture_get(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);
    unsigned index = GPOINTER_TO_UINT(opaque);
    bool value = index & 1 ? s->refgen_ready[index / 2] :
                            s->refgen_known[index / 2];

    visit_type_bool(v, name, &value, errp);
}

static void dsi_refgen_fixture_set(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);
    unsigned index = GPOINTER_TO_UINT(opaque), p = index / 2;
    bool value;

    if (!visit_type_bool(v, name, &value, errp)) {
        return;
    }
    if (!s->enabled_reference ||
        ((index & 1) && value && !s->refgen_known[p])) {
        error_setg(errp, "%s requires an enabled reference and known REFGEN",
                   name);
        return;
    }
    if (index & 1) {
        dsi_refgen_ready(s, p, value);
    } else {
        dsi_refgen_known(s, p, value);
    }
}

static void dsi_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(dev);

    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->last_xo_hz = CLOCK_PERIOD_TO_HZ(clock_get(s->input[0]));
    s->ready = true;
}

static void dsi_init(Object *obj)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const derived[] = {
        "lock-pending", "recipe-valid", "cphy", "output-route-known",
    };
    unsigned p, i;

    s->reference_identity = DSI_PLL7NM_REFERENCE_IDENTITY;
    s->bank_count = DSI_PLL7NM_BANKS;
    s->registers_per_bank = DSI_PLL7NM_REGISTERS_PER_BANK;
    s->register_count = DSI_PLL7NM_REGISTERS;
    s->clock_count = DSI_PLL7NM_CLOCKS;
    s->input_count = DSI_PLL7NM_INPUTS;
    s->window_count = 2 * (DSI_PLL7NM_WINDOWS_PER_BANK + 1);
    s->mapped_bytes = DSI_PLL7NM_REGISTERS * 4 + 8;
#define DSI_U32(name, field) \
    object_property_add_uint32_ptr(obj, name, &s->field, OBJ_PROP_FLAG_READ)
    DSI_U32("reference-profile", reference_profile);
    DSI_U32("reference-identity", reference_identity);
    DSI_U32("bank-count", bank_count);
    DSI_U32("registers-per-bank", registers_per_bank);
    DSI_U32("register-count", register_count);
    DSI_U32("clock-count", clock_count);
    DSI_U32("input-count", input_count);
    DSI_U32("window-count", window_count);
    DSI_U32("mapped-bytes", mapped_bytes);
#undef DSI_U32
    object_property_add_uint64_ptr(obj, "last-xo-hz", &s->last_xo_hz,
                                   OBJ_PROP_FLAG_READ);
    object_property_add(obj, "power-known", "bool", dsi_bool_get, NULL,
                        NULL, &s->power_known);
    object_property_add(obj, "power-on", "bool", dsi_bool_get, NULL,
                        NULL, &s->power_on);
    for (p = 0; p < DSI_PLL7NM_BANKS; p++) {
        g_autofree char *phy_name = g_strdup_printf("dsi%u-phy-pll-sparse", p);
        g_autofree char *dyn_name = g_strdup_printf("dsi%u-dynamic-control", p);
        g_autofree char *base = g_strdup_printf("phy%u-base", p);
        g_autofree char *dyn_base = g_strdup_printf("dynamic%u-base", p);
        g_autofree char *active = g_strdup_printf("pll%u-active-factor", p);
        g_autofree char *pending = g_strdup_printf("pll%u-pending-factor", p);
        g_autofree char *deadline = g_strdup_printf("pll%u-lock-deadline", p);
        g_autofree char *locked = g_strdup_printf("pll%u-locked", p);
        g_autofree char *known = g_strdup_printf("pll%u-refgen-known", p);
        g_autofree char *ready = g_strdup_printf("pll%u-refgen-ready", p);

        s->physical_base[p] = DSI_PLL7NM_PHY0_BASE + p * DSI_PLL7NM_BANK_STRIDE;
        s->dynamic_base[p] = s->physical_base[p] - 0x200;
        s->lock_deadline[p] = -1;
        object_property_add_uint64_ptr(obj, base, &s->physical_base[p], OBJ_PROP_FLAG_READ);
        object_property_add_uint64_ptr(obj, dyn_base, &s->dynamic_base[p], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, active, &s->active_factor[p], OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, pending, &s->pending_factor[p], OBJ_PROP_FLAG_READ);
        object_property_add(obj, deadline, "int64", dsi_signed_get, NULL, NULL,
                            &s->lock_deadline[p]);
        object_property_add(obj, locked, "bool", dsi_bool_get, NULL, NULL, &s->locked[p]);
        object_property_add(obj, known, "bool", dsi_bool_get, NULL, NULL, &s->refgen_known[p]);
        object_property_add(obj, ready, "bool", dsi_bool_get, NULL, NULL, &s->refgen_ready[p]);
        for (i = 0; i < ARRAY_SIZE(derived); i++) {
            g_autofree char *name = g_strdup_printf("pll%u-%s", p, derived[i]);

            object_property_add(obj, name, "bool", dsi_status_get, NULL,
                                NULL, GUINT_TO_POINTER(p * 4 + i));
        }
        for (i = 0; qtest_enabled() && i < 2; i++) {
            g_autofree char *name = g_strdup_printf("qtest-refgen%u-%s", p,
                                                    i ? "ready" : "known");

            object_property_add(obj, name, "bool", dsi_refgen_fixture_get,
                                dsi_refgen_fixture_set, NULL,
                                GUINT_TO_POINTER(p * 2 + i));
        }
        memory_region_init(&s->phy[p], obj, phy_name, 0x800);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->phy[p]);
        for (i = 0; i < DSI_PLL7NM_WINDOWS_PER_BANK; i++) {
            const DsiPll7nmWindowDef *w = &dsi_windows[i];
            g_autofree char *name = g_strdup_printf("%s.%03x", phy_name, w->offset);

            s->windows[p][i] = (DsiPll7nmWindow) { s, p, w->offset, false };
            memory_region_init_io(&s->iomem[p][i], obj, &dsi_ops,
                                  &s->windows[p][i], name, w->size);
            memory_region_add_subregion(&s->phy[p], w->offset, &s->iomem[p][i]);
        }
        s->dynamic_windows[p] = (DsiPll7nmWindow) { s, p, 0, true };
        memory_region_init_io(&s->dynamic[p], obj, &dsi_ops,
                              &s->dynamic_windows[p], dyn_name, 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->dynamic[p]);
        for (i = 0; i < DSI_PLL7NM_REGISTERS_PER_BANK; i++) {
            g_autofree char *name = g_strdup_printf("bank%u.reg.%03x", p,
                                                    dsi_registers[i].offset);

            object_property_add_uint32_ptr(obj, name, &DREG(s, p, i),
                                           OBJ_PROP_FLAG_READ);
        }
    }
    s->input[0] = qdev_init_clock_in(dev, "bi_tcxo", dsi_clock_changed, s,
                                    ClockUpdate);
    for (i = 0; i <= DSI_PLL7NM_CLOCKS; i++) {
        g_autofree char *hz = i == DSI_PLL7NM_CLOCKS ?
            g_strdup("input.bi_tcxo.hz") : g_strdup_printf("clk.%s.hz", dsi_clock_names[i]);
        g_autofree char *period = i == DSI_PLL7NM_CLOCKS ?
            g_strdup("input.bi_tcxo.period") : g_strdup_printf("clk.%s.period", dsi_clock_names[i]);

        if (i < DSI_PLL7NM_CLOCKS) {
            s->clocks[i] = qdev_init_clock_out(dev, dsi_clock_names[i]);
        }
        object_property_add(obj, hz, "uint64", dsi_clock_get, NULL, NULL,
                            GUINT_TO_POINTER(i * 2));
        object_property_add(obj, period, "uint64", dsi_clock_get, NULL, NULL,
                            GUINT_TO_POINTER(i * 2 + 1));
    }
    qdev_init_gpio_in_named(dev, dsi_power_known, "power-known", 1);
    qdev_init_gpio_in_named(dev, dsi_power_on, "power-on", 1);
    qdev_init_gpio_in_named(dev, dsi_refgen_known, "refgen-known", 2);
    qdev_init_gpio_in_named(dev, dsi_refgen_ready, "refgen-ready", 2);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dsi_timer, s);
}

static void dsi_finalize(Object *obj)
{
    QcomSm8250DsiPll7nmState *s = QCOM_SM8250_DSI_PLL7NM(obj);

    timer_free(s->timer);
}

static const Property dsi_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250DsiPll7nmState,
                     enabled_reference, false),
    DEFINE_PROP_LINK("mdss-provider", QcomSm8250DsiPll7nmState,
                     mdss_provider, TYPE_QCOM_MDSS_GDSC, DeviceState *),
};

static void dsi_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->realize = dsi_realize;
    dc->vmsd = &vmstate_dsi;
    device_class_set_props(dc, dsi_properties);
    rc->phases.enter = dsi_reset_enter;
    rc->phases.hold = dsi_reset_hold;
}

static const TypeInfo dsi_type_info = {
    .name = TYPE_QCOM_SM8250_DSI_PLL7NM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250DsiPll7nmState),
    .instance_init = dsi_init,
    .instance_finalize = dsi_finalize,
    .class_init = dsi_class_init,
};

static void dsi_register_types(void)
{
    type_register_static(&dsi_type_info);
}

type_init(dsi_register_types)
