/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Source-bound SM8250 DSI 7nm v4.1 static digital PLL reference.
 * OnePlusOSS/android_kernel_oneplus_sm8250 at
 * 98719830b0de5584f5588bbe9f4c8304d97a64c6:
 * techpack/display/pll/dsi_pll_7nm.c and msm/dsi/dsi_phy_hw_v4_0.c.
 *
 * The 64-XO-cycle qualification delay is a reference policy, not measured
 * silicon lock latency. PHY REFGEN_READY, analog lanes, trim handoff, dynamic
 * refresh execution and panels are not implemented. Unknown external sources
 * and zero PHY dividers never manufacture a carrier.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-dsi-pll.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "system/qtest.h"

#define DSI_XO_HZ UINT64_C(19200000)
#define DSI_FREF_HZ (2 * DSI_XO_HZ)
#define DSI_FRAC_SCALE UINT64_C(262144)
#define DSI_MIN_VCO_HZ UINT64_C(600000000)
#define DSI_MAX_VCO_HZ UINT64_C(5000000000)
#define DSI_LOCK_NS ((int64_t)((DSI_PLL_LOCK_REFERENCE_CYCLES * \
                       UINT64_C(1000000000) + DSI_XO_HZ - 1) / DSI_XO_HZ))

#define PHY_CFG0 0x010
#define PHY_CFG1 0x014
#define PHY_GLBL 0x018
#define PHY_RBUF 0x01c
#define PHY_CTRL0 0x024
#define PHY_CTRL2 0x02c
#define PHY_CTRL3 0x030
#define PHY_PLL_CTRL 0x03c
#define PHY_SPARE4 0x128
#define PLL_SYS_MUX 0x528
#define PLL_DEC 0x5e0
#define PLL_FRAC_LOW 0x5e4
#define PLL_FRAC_MID 0x5e8
#define PLL_FRAC_HIGH 0x5ec
#define PLL_SSC_STEP_LOW 0x620
#define PLL_SSC_STEP_HIGH 0x624
#define PLL_SSC_PER_LOW 0x628
#define PLL_SSC_PER_HIGH 0x62c
#define PLL_SSC_ADJ_LOW 0x630
#define PLL_SSC_ADJ_HIGH 0x634
#define PLL_SSC_CTRL 0x650
#define PLL_OUTDIV 0x654
#define PLL_STATUS 0x6b0
#define PLL_VCO_CONFIG1 0x740
#define PLL_INVERTERS1 0x748
#define PLL_CMODE1 0x750
#define PLL_ANALOG5_1 0x758

enum { DSI_REG_PHY, DSI_REG_PLL, DSI_REG_STATUS };
enum {
    DSI_RULE_NONE, DSI_RULE_FIXED, DSI_RULE_FACTOR,
    DSI_RULE_RATE, DSI_RULE_MODE, DSI_RULE_SSC,
};

typedef struct DsiPllRegister {
    uint32_t offset, write_mask;
    uint8_t kind, rule;
    uint32_t recipe_value;
} DsiPllRegister;

#include "qcom-sm8250-dsi-pll-data.inc"

static int dsi_register(hwaddr offset)
{
    unsigned lo = 0, hi = DSI_PLL_WORDS_PER_BANK;

    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;

        if (dsi_pll_registers[mid].offset < offset) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo < DSI_PLL_WORDS_PER_BANK &&
           dsi_pll_registers[lo].offset == offset ? (int)lo : -1;
}

static uint32_t dsi_value(const uint32_t *regs, unsigned offset)
{
    int id = dsi_register(offset);

    assert(id >= 0);
    return regs[id];
}

static uint32_t *dsi_bank(QcomSm8250DsiPllState *s, unsigned bank)
{
    return &s->regs[bank * DSI_PLL_WORDS_PER_BANK];
}

static uint32_t *dsi_capture(QcomSm8250DsiPllState *s, unsigned bank)
{
    return &s->captured[bank * DSI_PLL_WORDS_PER_BANK];
}

static uint64_t dsi_factor(const uint32_t *regs)
{
    return ((uint64_t)dsi_value(regs, PLL_DEC) << 18) |
           ((uint64_t)dsi_value(regs, PLL_FRAC_HIGH) << 16) |
           ((uint64_t)dsi_value(regs, PLL_FRAC_MID) << 8) |
           dsi_value(regs, PLL_FRAC_LOW);
}

static uint64_t dsi_nominal_rate(const uint32_t *regs)
{
    return DSI_FREF_HZ * dsi_factor(regs) / DSI_FRAC_SCALE;
}

static bool dsi_complete(QcomSm8250DsiPllState *s, unsigned bank)
{
    unsigned i, base = bank * DSI_PLL_WORDS_PER_BANK;

    for (i = 0; i < DSI_PLL_WORDS_PER_BANK; i++) {
        if (dsi_pll_registers[i].rule != DSI_RULE_NONE &&
            !s->written[base + i]) {
            return false;
        }
    }
    return true;
}

/*
 * DEC/FRAC is floor(requested_rate * 2^18 / 38.4MHz). The driver's recipe
 * branches on requested_rate, not that quantized result. Intersect all recipe
 * intervals with the inverse quantization interval; exact threshold requests
 * must not be rejected because the decoded result is fractionally smaller.
 */
static bool dsi_rate_recipe(const uint32_t *regs)
{
    uint64_t factor = dsi_factor(regs);
    uint64_t low = (DSI_FREF_HZ * factor + DSI_FRAC_SCALE - 1) /
                   DSI_FRAC_SCALE;
    uint64_t high = (DSI_FREF_HZ * (factor + 1) + DSI_FRAC_SCALE - 1) /
                    DSI_FRAC_SCALE - 1;

    low = MAX(low, DSI_MIN_VCO_HZ);
    high = MIN(high, DSI_MAX_VCO_HZ);
    switch (dsi_value(regs, PLL_INVERTERS1)) {
    case 0xa0:
        high = MIN(high, UINT64_C(1000000000));
        break;
    case 0x20:
        low = MAX(low, UINT64_C(1000000001));
        high = MIN(high, UINT64_C(2500000000));
        break;
    case 0:
        low = MAX(low, UINT64_C(2500000001));
        high = MIN(high, UINT64_C(3020000000));
        break;
    case 0x40:
        low = MAX(low, UINT64_C(3020000001));
        break;
    default:
        return false;
    }
    switch (dsi_value(regs, PLL_VCO_CONFIG1)) {
    case 8:
        high = MIN(high, UINT64_C(1519999999));
        break;
    case 1:
        low = MAX(low, UINT64_C(1520000000));
        high = MIN(high, UINT64_C(2989999999));
        break;
    case 0:
        low = MAX(low, UINT64_C(2990000000));
        break;
    default:
        return false;
    }
    switch (dsi_value(regs, PLL_ANALOG5_1)) {
    case 1:
        high = MIN(high, UINT64_C(3099999999));
        break;
    case 3:
        low = MAX(low, UINT64_C(3100000000));
        break;
    default:
        return false;
    }
    return low <= high;
}

static bool dsi_ssc_recipe(const uint32_t *regs)
{
    uint32_t ctrl = dsi_value(regs, PLL_SSC_CTRL);
    uint64_t step;
    unsigned i;
    static const unsigned fields[] = {
        PLL_SSC_STEP_LOW, PLL_SSC_STEP_HIGH,
        PLL_SSC_PER_LOW, PLL_SSC_PER_HIGH, PLL_SSC_ADJ_LOW, PLL_SSC_ADJ_HIGH,
    };

    if (!ctrl) {
        for (i = 0; i < ARRAY_SIZE(fields); i++) {
            if (dsi_value(regs, fields[i])) {
                return false;
            }
        }
        return true;
    }
    if (ctrl != 2 && ctrl != 3) {
        return false;
    }
    /*
     * Source defaults: 31500 Hz, 4800 ppm, adjustment=2.
     * DIV_ROUND_CLOSEST(19.2MHz, 31500)/2-1, aligned to 3 => 302.
     * Preserve the source's division before its final round-to-nearest.
     * This validates programming, not an analog SSC waveform.
     */
    step = (dsi_factor(regs) * 4800 * 3 / 303 + 500000) / 1000000;
    return dsi_value(regs, PLL_SSC_STEP_LOW) == (step & 0xff) &&
           dsi_value(regs, PLL_SSC_STEP_HIGH) == ((step >> 8) & 0xff) &&
           dsi_value(regs, PLL_SSC_PER_LOW) == 0x2e &&
           dsi_value(regs, PLL_SSC_PER_HIGH) == 1 &&
           dsi_value(regs, PLL_SSC_ADJ_LOW) == 2 &&
           dsi_value(regs, PLL_SSC_ADJ_HIGH) == 0;
}

static bool dsi_recipe(const uint32_t *regs)
{
    unsigned i;
    bool cphy = dsi_value(regs, PHY_GLBL) & 0x40;

    for (i = 0; i < DSI_PLL_WORDS_PER_BANK; i++) {
        const DsiPllRegister *r = &dsi_pll_registers[i];

        if (regs[i] & ~r->write_mask) {
            return false;
        }
        if (r->rule == DSI_RULE_FIXED && regs[i] != r->recipe_value) {
            return false;
        }
    }
    return dsi_value(regs, PLL_CMODE1) == (cphy ? 0 : 0x10) &&
           dsi_rate_recipe(regs) && dsi_ssc_recipe(regs);
}

static bool dsi_powered(QcomSm8250DsiPllState *s)
{
    return s->enabled_reference && s->power_known && s->power_on;
}

static bool dsi_bias_requested(QcomSm8250DsiPllState *s, unsigned bank)
{
    return dsi_powered(s) &&
           (dsi_value(dsi_bank(s, bank), PHY_CTRL0) & 0x60) == 0x60;
}

static bool dsi_environment(QcomSm8250DsiPllState *s, unsigned bank)
{
    const uint32_t *regs = dsi_bank(s, bank);

    return dsi_bias_requested(s, bank) &&
           clock_get(s->input[0]) == CLOCK_PERIOD_FROM_HZ(DSI_XO_HZ) &&
           dsi_value(regs, PLL_SYS_MUX) == 0xc0 &&
           dsi_value(regs, PHY_PLL_CTRL) == 1;
}

static bool dsi_capture_matches(QcomSm8250DsiPllState *s, unsigned bank)
{
    const uint32_t *regs = dsi_bank(s, bank);
    const uint32_t *captured = dsi_capture(s, bank);
    unsigned i;

    for (i = 0; i < DSI_PLL_WORDS_PER_BANK; i++) {
        const DsiPllRegister *r = &dsi_pll_registers[i];

        if (r->kind == DSI_REG_PLL && r->offset != PLL_OUTDIV &&
            regs[i] != captured[i]) {
            return false;
        }
    }
    return dsi_value(regs, PHY_GLBL) == dsi_value(captured, PHY_GLBL) &&
           (dsi_value(regs, PHY_CTRL0) & 0x60) ==
           (dsi_value(captured, PHY_CTRL0) & 0x60) &&
           dsi_value(regs, PHY_PLL_CTRL) ==
           dsi_value(captured, PHY_PLL_CTRL);
}

static bool dsi_transaction_valid(QcomSm8250DsiPllState *s, unsigned bank)
{
    return dsi_environment(s, bank) && dsi_complete(s, bank) &&
           s->captured_xo_period[bank] == clock_get(s->input[0]) &&
           dsi_recipe(dsi_capture(s, bank)) && dsi_capture_matches(s, bank);
}

static bool dsi_locked(QcomSm8250DsiPllState *s, unsigned bank)
{
    return s->pll_locked[bank] && s->bias_settled[bank] &&
           dsi_transaction_valid(s, bank);
}

static void dsi_cancel(QcomSm8250DsiPllState *s, unsigned bank)
{
    s->pll_locked[bank] = 0;
    s->lock_deadline[bank] = -1;
    s->lock_qualify_after[bank] = -1;
    s->captured_xo_period[bank] = 0;
    memset(dsi_capture(s, bank), 0,
           DSI_PLL_WORDS_PER_BANK * sizeof(s->captured[0]));
    s->reset_asserted[bank] = 0;
    s->digital_ready[bank] = 0;
}

/* clk-regmap-divider.c -> divider_recalc_rate(): ceil for generic dividers. */
static uint64_t dsi_divider_rate(uint64_t parent, unsigned divider)
{
    return divider ? parent / divider + !!(parent % divider) : 0;
}

/* Computation is pure. CCF shadow views alias the one active physical PLL. */
static void dsi_rates(QcomSm8250DsiPllState *s, uint64_t *rates)
{
    unsigned b;

    memset(rates, 0, DSI_PLL_CLOCKS * sizeof(rates[0]));
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        const uint32_t *regs = dsi_bank(s, b);
        uint64_t *r = &rates[b * DSI_PLL_CLOCKS_PER_BANK];
        uint32_t cfg0 = dsi_value(regs, PHY_CFG0);
        uint32_t cfg1 = dsi_value(regs, PHY_CFG1);
        unsigned bit_div = cfg0 & 15, pixel_div = (cfg0 >> 4) & 15;
        bool cphy = dsi_value(regs, PHY_GLBL) & 0x40;
        bool internal = !(cfg1 & 4);
        bool full_rate = cphy || dsi_value(regs, PHY_CTRL2) == 0x40;
        bool gate = s->digital_ready[b] &&
                    (cfg1 & 0x30) == 0x30 &&
                    dsi_value(regs, PHY_CTRL3) == 4 &&
                    !(dsi_value(regs, PHY_SPARE4) & 1);

        if (!dsi_locked(s, b)) {
            continue;
        }
        r[0] = dsi_nominal_rate(dsi_capture(s, b));
        r[1] = dsi_divider_rate(r[0],
                                 1U << dsi_value(regs, PLL_OUTDIV));
        /*
         * The source identifies the non-native selector but does not establish
         * which master tap feeds it. Preserve unknown instead of guessing.
         * ALLOW_ZERO is a CCF fallback, not evidence of a hardware /1 carrier.
         */
        if (internal && full_rate && bit_div) {
            r[2] = dsi_divider_rate(r[1], bit_div);
        }
        r[4] = r[2] / 2;
        r[5] = r[1] / 4;
        if (cphy) {
            r[18] = r[2] / 7;
            r[19] = r[1] * 2 / 7;
            if (internal && (cfg1 & 3) == 3) {
                r[20] = r[19];
                r[21] = dsi_divider_rate(r[20], pixel_div);
            }
        } else {
            r[3] = r[2] / 8;
            if ((cfg1 & 3) <= 1) {
                r[7] = (cfg1 & 1) ? r[4] : r[2];
                r[8] = dsi_divider_rate(r[7], pixel_div);
            }
        }
        r[6] = gate ? (cphy ? r[18] : r[3]) : 0;
        r[9] = gate ? (cphy ? r[21] : r[8]) : 0;
        r[10] = r[0];
        r[11] = r[1];
        r[12] = r[2];
        r[13] = r[3];
        r[14] = r[4];
        r[15] = r[5];
        r[16] = r[7];
        r[17] = r[8];
        r[22] = r[18];
        r[23] = r[19];
        r[24] = r[20];
        r[25] = r[21];
    }
}

static void dsi_publish(QcomSm8250DsiPllState *s, bool propagate)
{
    uint64_t rates[DSI_PLL_CLOCKS];
    unsigned i;

    if (s->publishing) {
        return;
    }
    s->publishing = true;
    dsi_rates(s, rates);
    for (i = 0; i < DSI_PLL_CLOCKS; i++) {
        bool changed = clock_set(s->clocks[i],
                                 CLOCK_PERIOD_FROM_HZ(rates[i]));

        if (changed && propagate) {
            clock_propagate(s->clocks[i]);
        }
    }
    s->publishing = false;
}

static void dsi_schedule(QcomSm8250DsiPllState *s)
{
    int64_t next = -1;
    unsigned b;

    timer_del(s->timer);
    if (!s->ready) {
        return;
    }
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        int64_t times[] = { s->bias_deadline[b], s->lock_deadline[b] };
        unsigned i;

        for (i = 0; i < ARRAY_SIZE(times); i++) {
            if (times[i] >= 0 && (next < 0 || times[i] < next)) {
                next = times[i];
            }
        }
    }
    if (next >= 0) {
        timer_mod(s->timer, next);
    }
}

static void dsi_bias_update(QcomSm8250DsiPllState *s, unsigned bank)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (!dsi_bias_requested(s, bank)) {
        s->bias_settled[bank] = 0;
        s->bias_deadline[bank] = -1;
        dsi_cancel(s, bank);
    } else if (!s->bias_settled[bank] && s->bias_deadline[bank] < 0) {
        if (now < 0 || now > INT64_MAX - DSI_PLL_BIAS_SETTLE_NS) {
            qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DSI_PLL
                          ": bias reference timer out of range\n");
            return;
        }
        s->bias_deadline[bank] = now + DSI_PLL_BIAS_SETTLE_NS;
    }
}

static void dsi_start(QcomSm8250DsiPllState *s, unsigned bank)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    int64_t after = MAX(now, s->bias_deadline[bank]);

    dsi_cancel(s, bank);
    if (!dsi_environment(s, bank) || !dsi_complete(s, bank) ||
        !dsi_recipe(dsi_bank(s, bank)) || now < 0 ||
        after > INT64_MAX - DSI_LOCK_NS) {
        return;
    }
    memcpy(dsi_capture(s, bank), dsi_bank(s, bank),
           DSI_PLL_WORDS_PER_BANK * sizeof(s->captured[0]));
    s->captured_xo_period[bank] = clock_get(s->input[0]);
    s->lock_qualify_after[bank] = after;
    s->lock_deadline[bank] = s->lock_qualify_after[bank] + DSI_LOCK_NS;
}

static void dsi_timer(void *opaque)
{
    QcomSm8250DsiPllState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned b;

    if (!s->ready) {
        return;
    }
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        if (s->bias_deadline[b] >= 0 && s->bias_deadline[b] <= now) {
            s->bias_deadline[b] = -1;
            s->bias_settled[b] = dsi_bias_requested(s, b);
        }
        if (s->lock_deadline[b] >= 0 && s->lock_deadline[b] <= now) {
            if (s->bias_settled[b] && dsi_transaction_valid(s, b)) {
                s->lock_deadline[b] = -1;
                s->pll_locked[b] = 1;
            } else {
                dsi_cancel(s, b);
            }
        }
    }
    dsi_publish(s, true);
    dsi_schedule(s);
}

static void dsi_clock_changed(void *opaque, ClockEvent event)
{
    QcomSm8250DsiPllState *s = opaque;
    uint64_t period = clock_get(s->input[0]);
    unsigned b;

    if (!s->ready || period == s->last_xo_period) {
        return;
    }
    s->last_xo_period = period;
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        dsi_cancel(s, b);
    }
    dsi_publish(s, true);
    dsi_schedule(s);
}

static void dsi_power_changed(QcomSm8250DsiPllState *s)
{
    unsigned b;

    if (!s->ready) {
        return;
    }
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        if (!dsi_powered(s)) {
            /* Retained raw bytes do not imply programming qualification. */
            memset(&s->written[b * DSI_PLL_WORDS_PER_BANK], 0,
                   DSI_PLL_WORDS_PER_BANK * sizeof(s->written[0]));
        }
        dsi_bias_update(s, b);
    }
    dsi_publish(s, true);
    dsi_schedule(s);
}

static void dsi_power_known(void *opaque, int index, int level)
{
    QcomSm8250DsiPllState *s = opaque;

    if (s->power_known == !!level) {
        return;
    }
    s->power_known = !!level;
    dsi_power_changed(s);
}

static void dsi_power_on(void *opaque, int index, int level)
{
    QcomSm8250DsiPllState *s = opaque;

    if (s->power_on == !!level) {
        return;
    }
    s->power_on = !!level;
    dsi_power_changed(s);
}

static MemTxResult dsi_read(void *opaque, hwaddr address, uint64_t *value,
                           unsigned size, MemTxAttrs attrs)
{
    QcomDsiPllWindow *w = opaque;
    QcomSm8250DsiPllState *s = w->state;
    int id = dsi_register(w->offset + address);
    const DsiPllRegister *r;

    *value = 0;
    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        goto unmodeled;
    }
    r = &dsi_pll_registers[id];
    if (r->kind == DSI_REG_STATUS) {
        if (r->offset != PLL_STATUS) {
            goto unmodeled;
        }
        *value = dsi_locked(s, w->bank) ? 1 : 0;
    } else {
        *value = dsi_bank(s, w->bank)[id];
    }
    return MEMTX_OK;

unmodeled:
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DSI_PLL
                  ": unmodeled read bank%u +0x%" HWADDR_PRIx " size %u\n",
                  w->bank, w->offset + address, size);
    return MEMTX_ERROR;
}

static MemTxResult dsi_write(void *opaque, hwaddr address, uint64_t value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomDsiPllWindow *w = opaque;
    QcomSm8250DsiPllState *s = w->state;
    int id = dsi_register(w->offset + address);
    const DsiPllRegister *r;
    unsigned base = w->bank * DSI_PLL_WORDS_PER_BANK;
    uint32_t old, val;

    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        goto unmodeled;
    }
    r = &dsi_pll_registers[id];
    if (value & ~(uint64_t)r->write_mask) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DSI_PLL
                      ": unsupported bits bank%u +0x%x\n",
                      w->bank, r->offset);
        return MEMTX_ERROR;
    }
    /*
     * Source init writes zero to these words. It must not set/clear readiness
     * or acknowledge any transaction; all nonzero status writes are rejected.
     */
    if (r->kind == DSI_REG_STATUS) {
        return MEMTX_OK;
    }
    old = s->regs[base + id];
    val = value;
    s->regs[base + id] = val;
    /* Programming while the supply is unknown/off is not qualified. */
    s->written[base + id] = dsi_powered(s);
    if (old != val) {
        if ((r->kind == DSI_REG_PLL && r->offset != PLL_OUTDIV) ||
            r->offset == PHY_GLBL ||
            (r->offset == PHY_CTRL0 && ((old ^ val) & 0x60)) ||
            r->offset == PHY_PLL_CTRL) {
            dsi_cancel(s, w->bank);
        }
    }
    if (r->offset == PHY_CTRL0) {
        dsi_bias_update(s, w->bank);
    }
    if (r->offset == PHY_PLL_CTRL && !old && val == 1) {
        dsi_start(s, w->bank);
    }
    if (r->offset == PHY_SPARE4) {
        if (val) {
            /* Source enable pulses the digital reset only after LOCK. */
            s->reset_asserted[w->bank] = dsi_locked(s, w->bank);
            s->digital_ready[w->bank] = 0;
        } else if (old && s->reset_asserted[w->bank]) {
            s->reset_asserted[w->bank] = 0;
            s->digital_ready[w->bank] = dsi_locked(s, w->bank);
        }
    }
    dsi_publish(s, true);
    dsi_schedule(s);
    return MEMTX_OK;

unmodeled:
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DSI_PLL
                  ": unmodeled write bank%u +0x%" HWADDR_PRIx " size %u\n",
                  w->bank, w->offset + address, size);
    return MEMTX_ERROR;
}

static const MemoryRegionOps dsi_ops = {
    .read_with_attrs = dsi_read,
    .write_with_attrs = dsi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void dsi_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);
    unsigned b;

    s->ready = false;
    s->publishing = false;
    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    memset(s->written, 0, sizeof(s->written));
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        dsi_cancel(s, b);
        s->bias_settled[b] = 0;
        s->bias_deadline[b] = -1;
    }
    /* GPIO and Clock inputs belong to their providers and survive reset. */
    s->last_xo_period = clock_get(s->input[0]);
}

static void dsi_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);

    s->ready = true;
    dsi_publish(s, true);
}

static int dsi_pre_load(void *opaque)
{
    QcomSm8250DsiPllState *s = opaque;
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    for (i = 0; i < DSI_PLL_CLOCKS; i++) {
        clock_set(s->clocks[i], 0);
    }
    return 0;
}

static int dsi_bad_state(const char *what, unsigned index)
{
    error_report(TYPE_QCOM_SM8250_DSI_PLL ": invalid migrated %s %u",
                 what, index);
    return -EINVAL;
}

static bool dsi_deadline_valid(int64_t deadline, int64_t now, uint64_t maximum)
{
    return deadline >= -1 &&
           (deadline <= now || (uint64_t)(deadline - now) <= maximum);
}

static int dsi_post_load(void *opaque, int version_id)
{
    QcomSm8250DsiPllState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned b, i;

    if (now < 0 || s->power_known > 1 || s->power_on > 1 ||
        s->input[0]->multiplier != 1 || s->input[0]->divider != 1 ||
        s->last_xo_period != clock_get(s->input[0])) {
        return dsi_bad_state("input/virtual time", 0);
    }
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        unsigned base = b * DSI_PLL_WORDS_PER_BANK;
        bool has_capture = s->pll_locked[b] || s->lock_deadline[b] >= 0;
        uint32_t *regs = dsi_bank(s, b);
        uint32_t *captured = dsi_capture(s, b);

        for (i = 0; i < DSI_PLL_WORDS_PER_BANK; i++) {
            uint32_t mask = dsi_pll_registers[i].write_mask;

            if ((regs[i] & ~mask) || (captured[i] & ~mask) ||
                s->written[base + i] > 1 ||
                (dsi_pll_registers[i].kind == DSI_REG_STATUS &&
                 s->written[base + i]) ||
                (!has_capture && captured[i]) ||
                (!dsi_powered(s) && s->written[base + i]) ||
                (!s->enabled_reference && (regs[i] || s->written[base + i]))) {
                return dsi_bad_state("register/capture", base + i);
            }
        }
        if (s->pll_locked[b] > 1 || s->bias_settled[b] > 1 ||
            s->reset_asserted[b] > 1 || s->digital_ready[b] > 1 ||
            !dsi_deadline_valid(s->bias_deadline[b], now,
                                DSI_PLL_BIAS_SETTLE_NS) ||
            !dsi_deadline_valid(s->lock_deadline[b], now,
                                DSI_PLL_BIAS_SETTLE_NS + DSI_LOCK_NS)) {
            return dsi_bad_state("flags/deadlines", b);
        }
        if ((dsi_bias_requested(s, b) && !s->bias_settled[b] &&
             s->bias_deadline[b] < 0 &&
             now <= INT64_MAX - DSI_PLL_BIAS_SETTLE_NS) ||
            (s->bias_settled[b] &&
             (s->bias_deadline[b] >= 0 || !dsi_bias_requested(s, b))) ||
            (s->bias_deadline[b] >= 0 &&
             (!dsi_bias_requested(s, b) || s->bias_settled[b])) ||
            (s->reset_asserted[b] &&
             (!dsi_locked(s, b) || dsi_value(regs, PHY_SPARE4) != 1)) ||
            (s->digital_ready[b] &&
             (!dsi_locked(s, b) || s->reset_asserted[b] ||
              dsi_value(regs, PHY_SPARE4)))) {
            return dsi_bad_state("PHY qualification", b);
        }
        if (has_capture) {
            int64_t after = s->lock_qualify_after[b];

            if (!dsi_transaction_valid(s, b) || after < 0 ||
                !dsi_deadline_valid(after, now, DSI_PLL_BIAS_SETTLE_NS) ||
                after > INT64_MAX - DSI_LOCK_NS ||
                (s->pll_locked[b] &&
                 (!s->bias_settled[b] || s->lock_deadline[b] >= 0 ||
                  after + DSI_LOCK_NS > now)) ||
                (s->lock_deadline[b] >= 0 &&
                 (s->pll_locked[b] ||
                  s->lock_deadline[b] != after + DSI_LOCK_NS)) ||
                (s->bias_deadline[b] >= 0 &&
                 s->bias_deadline[b] != after)) {
                return dsi_bad_state("lock transaction", b);
            }
        } else if (s->captured_xo_period[b] || s->lock_qualify_after[b] != -1) {
            return dsi_bad_state("inactive capture", b);
        }
        if (!s->enabled_reference &&
            (has_capture || s->bias_settled[b] || s->bias_deadline[b] >= 0 ||
             s->reset_asserted[b] || s->digital_ready[b])) {
            return dsi_bad_state("disabled profile", b);
        }
    }
    s->publishing = false;
    s->ready = true;
    /* No deadline consumption or MMIO: first paused QOM sees the saved state. */
    dsi_publish(s, false);
    dsi_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_dsi_pll = {
    .name = TYPE_QCOM_SM8250_DSI_PLL,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = dsi_pre_load,
    .post_load = dsi_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(source_variant, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(bank_count, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(words_per_bank, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(register_count, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(clock_count, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(input_count, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(window_count, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(lock_reference_cycles, QcomSm8250DsiPllState),
        VMSTATE_UINT32_EQUAL(bias_settle_ns, QcomSm8250DsiPllState),
        VMSTATE_UINT64_EQUAL(physical_base, QcomSm8250DsiPllState),
        VMSTATE_UINT64_EQUAL(bank_stride, QcomSm8250DsiPllState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250DsiPllState, DSI_PLL_REGISTERS),
        VMSTATE_UINT8_ARRAY(written, QcomSm8250DsiPllState, DSI_PLL_REGISTERS),
        VMSTATE_UINT32_ARRAY(captured, QcomSm8250DsiPllState, DSI_PLL_REGISTERS),
        VMSTATE_UINT64_ARRAY(captured_xo_period, QcomSm8250DsiPllState,
                             DSI_PLL_BANKS),
        VMSTATE_UINT64(last_xo_period, QcomSm8250DsiPllState),
        VMSTATE_INT64_ARRAY(bias_deadline, QcomSm8250DsiPllState, DSI_PLL_BANKS),
        VMSTATE_INT64_ARRAY(lock_deadline, QcomSm8250DsiPllState, DSI_PLL_BANKS),
        VMSTATE_INT64_ARRAY(lock_qualify_after, QcomSm8250DsiPllState,
                            DSI_PLL_BANKS),
        VMSTATE_UINT8_ARRAY(pll_locked, QcomSm8250DsiPllState, DSI_PLL_BANKS),
        VMSTATE_UINT8_ARRAY(bias_settled, QcomSm8250DsiPllState, DSI_PLL_BANKS),
        VMSTATE_UINT8_ARRAY(reset_asserted, QcomSm8250DsiPllState, DSI_PLL_BANKS),
        VMSTATE_UINT8_ARRAY(digital_ready, QcomSm8250DsiPllState, DSI_PLL_BANKS),
        VMSTATE_UINT8(power_known, QcomSm8250DsiPllState),
        VMSTATE_UINT8(power_on, QcomSm8250DsiPllState),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250DsiPllState, DSI_PLL_INPUTS),
        VMSTATE_END_OF_LIST()
    },
};

static void dsi_u8_bool_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    bool value = *(uint8_t *)opaque;

    visit_type_bool(v, name, &value, errp);
}

static void dsi_signed_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    int64_t value = *(int64_t *)opaque;

    visit_type_int64(v, name, &value, errp);
}

static void dsi_clock_get(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);
    unsigned n = GPOINTER_TO_UINT(opaque), id = n / 2;
    uint64_t value = clock_get(s->clocks[id]);

    if (!(n & 1)) {
        value = CLOCK_PERIOD_TO_HZ(value);
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dsi_bank_rate_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);
    unsigned n = GPOINTER_TO_UINT(opaque), bank = n / 2;
    uint64_t value;

    if (n & 1) {
        value = dsi_locked(s, bank) ? dsi_nominal_rate(dsi_capture(s, bank)) : 0;
    } else {
        value = s->enabled_reference ? dsi_nominal_rate(dsi_bank(s, bank)) : 0;
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dsi_bank_status_get(Object *obj, Visitor *v, const char *name,
                                void *opaque, Error **errp)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);
    unsigned n = GPOINTER_TO_UINT(opaque), bank = n / 11;
    const uint32_t *regs = dsi_bank(s, bank);
    bool values[] = {
        dsi_locked(s, bank), s->lock_deadline[bank] >= 0,
        s->bias_settled[bank], s->reset_asserted[bank], s->digital_ready[bank],
        dsi_complete(s, bank), dsi_recipe(regs),
        !!(dsi_value(regs, PHY_GLBL) & 0x40),
        !!(dsi_value(regs, PHY_CFG1) & 4),
        false, /* external carrier is not established by the pinned source */
        dsi_environment(s, bank),
    };
    bool value = values[n % 11];

    visit_type_bool(v, name, &value, errp);
}

static void dsi_false_get(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    bool value = false;

    visit_type_bool(v, name, &value, errp);
}

static void dsi_fixture_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    uint64_t value = CLOCK_PERIOD_TO_HZ(clock_get(opaque));

    visit_type_uint64(v, name, &value, errp);
}

static void dsi_fixture_set(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);
    Clock *input = opaque;
    uint64_t value;

    if (!visit_type_uint64(v, name, &value, errp)) {
        return;
    }
    if (clock_has_source(input)) {
        error_setg(errp, "%s cannot override a connected clock source", name);
        return;
    }
    if (value > DSI_MAX_VCO_HZ) {
        error_setg(errp, "%s exceeds the bounded fixture range", name);
        return;
    }
    clock_set(input, CLOCK_PERIOD_FROM_HZ(value));
    dsi_clock_changed(s, ClockUpdate);
}

static void dsi_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(dev);

    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->last_xo_period = clock_get(s->input[0]);
    s->ready = true;
    dsi_publish(s, false);
}

static void dsi_init(Object *obj)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const statuses[] = {
        "pll-locked", "lock-pending", "bias-settled", "reset-asserted",
        "digital-ready", "config-complete", "recipe-valid", "cphy",
        "external-selected", "external-known", "environment-qualified",
    };
    unsigned b, i;

    s->reference_identity = DSI_PLL_REFERENCE_IDENTITY;
    s->source_variant = DSI_PLL_SOURCE_VARIANT;
    s->bank_count = DSI_PLL_BANKS;
    s->words_per_bank = DSI_PLL_WORDS_PER_BANK;
    s->register_count = DSI_PLL_REGISTERS;
    s->clock_count = DSI_PLL_CLOCKS;
    s->input_count = DSI_PLL_INPUTS;
    s->window_count = DSI_PLL_WINDOWS;
    s->mapped_bytes = DSI_PLL_MAPPED_BYTES;
    s->lock_reference_cycles = DSI_PLL_LOCK_REFERENCE_CYCLES;
    s->bias_settle_ns = DSI_PLL_BIAS_SETTLE_NS;
    s->physical_base = DSI_PLL_PHYSICAL_BASE;
    s->bank_stride = DSI_PLL_BANK_STRIDE;
#define DSI_U32_PROPERTY(name, field) \
    object_property_add_uint32_ptr(obj, name, &s->field, OBJ_PROP_FLAG_READ)
    DSI_U32_PROPERTY("reference-profile", reference_profile);
    DSI_U32_PROPERTY("reference-identity", reference_identity);
    DSI_U32_PROPERTY("source-variant", source_variant);
    DSI_U32_PROPERTY("bank-count", bank_count);
    DSI_U32_PROPERTY("words-per-bank", words_per_bank);
    DSI_U32_PROPERTY("register-count", register_count);
    DSI_U32_PROPERTY("clock-count", clock_count);
    DSI_U32_PROPERTY("input-count", input_count);
    DSI_U32_PROPERTY("window-count", window_count);
    DSI_U32_PROPERTY("mapped-bytes", mapped_bytes);
    DSI_U32_PROPERTY("lock-reference-cycles", lock_reference_cycles);
    DSI_U32_PROPERTY("bias-settle-ns", bias_settle_ns);
#undef DSI_U32_PROPERTY
    object_property_add_uint64_ptr(obj, "physical-base", &s->physical_base,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "bank-stride", &s->bank_stride,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "last-xo-period", &s->last_xo_period,
                                   OBJ_PROP_FLAG_READ);
    object_property_add(obj, "power-known", "bool", dsi_u8_bool_get, NULL,
                        NULL, &s->power_known);
    object_property_add(obj, "power-on", "bool", dsi_u8_bool_get, NULL,
                        NULL, &s->power_on);
    object_property_add(obj, "dynamic-sequencer-supported", "bool",
                        dsi_false_get, NULL, NULL, NULL);
    object_property_add(obj, "phy-refgen-ready-supported", "bool",
                        dsi_false_get, NULL, NULL, NULL);
    for (b = 0; b < DSI_PLL_BANKS; b++) {
        g_autofree char *container_name =
            g_strdup_printf("dsi-pll%u-sparse", b);

        memory_region_init(&s->container[b], obj, container_name,
                           DSI_PLL_BANK_SIZE);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container[b]);
        for (i = 0; i < DSI_PLL_WINDOWS_PER_BANK; i++) {
            unsigned n = b * DSI_PLL_WINDOWS_PER_BANK + i;
            const QcomDsiPllWindowDef *d = &dsi_pll_windows[i];
            g_autofree char *name =
                g_strdup_printf(TYPE_QCOM_SM8250_DSI_PLL ".bank%u.%04x",
                                b, d->offset);

            s->windows[n].state = s;
            s->windows[n].bank = b;
            s->windows[n].offset = d->offset;
            memory_region_init_io(&s->iomem[n], obj, &dsi_ops, &s->windows[n],
                                  name, d->size);
            memory_region_add_subregion(&s->container[b], d->offset,
                                        &s->iomem[n]);
        }
        for (i = 0; i < DSI_PLL_WORDS_PER_BANK; i++) {
            unsigned n = b * DSI_PLL_WORDS_PER_BANK + i;
            unsigned off = b * DSI_PLL_BANK_STRIDE +
                           dsi_pll_registers[i].offset;
            g_autofree char *reg = g_strdup_printf("reg.%04x", off);
            g_autofree char *captured = g_strdup_printf("captured.%04x", off);
            g_autofree char *written = g_strdup_printf("written.%04x", off);

            object_property_add_uint32_ptr(obj, reg, &s->regs[n],
                                           OBJ_PROP_FLAG_READ);
            object_property_add_uint32_ptr(obj, captured, &s->captured[n],
                                           OBJ_PROP_FLAG_READ);
            object_property_add(obj, written, "bool", dsi_u8_bool_get,
                                NULL, NULL, &s->written[n]);
        }
        for (i = 0; i < ARRAY_SIZE(statuses); i++) {
            g_autofree char *name =
                g_strdup_printf("bank%u-%s", b, statuses[i]);

            object_property_add(obj, name, "bool", dsi_bank_status_get, NULL,
                                NULL, GUINT_TO_POINTER(b * 11 + i));
        }
        {
            static const char *const time_names[] = {
                "bias-deadline", "lock-deadline", "lock-qualify-after",
            };
            int64_t *times[] = {
                &s->bias_deadline[b], &s->lock_deadline[b],
                &s->lock_qualify_after[b],
            };
            g_autofree char *period =
                g_strdup_printf("bank%u-captured-xo-period", b);

            for (i = 0; i < ARRAY_SIZE(times); i++) {
                g_autofree char *name =
                    g_strdup_printf("bank%u-%s", b, time_names[i]);

                *times[i] = -1;
                object_property_add(obj, name, "int64", dsi_signed_get,
                                    NULL, NULL, times[i]);
            }
            object_property_add_uint64_ptr(obj, period,
                                           &s->captured_xo_period[b],
                                           OBJ_PROP_FLAG_READ);
        }
        for (i = 0; i < 2; i++) {
            g_autofree char *name =
                g_strdup_printf("bank%u-%s-vco-hz", b,
                                i ? "active" : "configured");

            object_property_add(obj, name, "uint64", dsi_bank_rate_get,
                                NULL, NULL, GUINT_TO_POINTER(b * 2 + i));
        }
    }
    s->input[0] = qdev_init_clock_in(dev, "bi_tcxo", dsi_clock_changed,
                                     s, ClockUpdate);
    if (qtest_enabled()) {
        object_property_add(obj, "qtest-bi_tcxo-hz", "uint64",
                            dsi_fixture_get, dsi_fixture_set, NULL, s->input[0]);
    }
    for (i = 0; i < DSI_PLL_CLOCKS; i++) {
        unsigned j;

        s->clocks[i] = qdev_init_clock_out(dev, dsi_pll_clock_names[i]);
        for (j = 0; j < 2; j++) {
            g_autofree char *name =
                g_strdup_printf("clk.%s.%s", dsi_pll_clock_names[i],
                                j ? "period" : "hz");

            object_property_add(obj, name, "uint64", dsi_clock_get,
                                NULL, NULL, GUINT_TO_POINTER(i * 2 + j));
        }
    }
    qdev_init_gpio_in_named(dev, dsi_power_known, "power-known", 1);
    qdev_init_gpio_in_named(dev, dsi_power_on, "power-on", 1);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dsi_timer, s);
}

static void dsi_finalize(Object *obj)
{
    QcomSm8250DsiPllState *s = QCOM_SM8250_DSI_PLL(obj);

    timer_free(s->timer);
}

static const Property dsi_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250DsiPllState,
                     enabled_reference, false),
};

static void dsi_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 DSI 7nm v4.1 static digital PLL reference";
    dc->realize = dsi_realize;
    dc->vmsd = &vmstate_dsi_pll;
    device_class_set_props(dc, dsi_properties);
    rc->phases.enter = dsi_reset_enter;
    rc->phases.hold = dsi_reset_hold;
}

static const TypeInfo dsi_info = {
    .name = TYPE_QCOM_SM8250_DSI_PLL,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250DsiPllState),
    .instance_init = dsi_init,
    .instance_finalize = dsi_finalize,
    .class_init = dsi_class_init,
};

static void dsi_register_types(void)
{
    type_register_static(&dsi_info);
}
type_init(dsi_register_types)
