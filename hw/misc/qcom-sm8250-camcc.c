/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Bounded Kona-v2 CAMCC digital reference protocol.
 * OnePlusOSS/android_kernel_oneplus_sm8250 at
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae: camcc-kona.c,
 * clk-alpha-pll.c, clk-rcg2.c, clk-branch.c and clk-regmap-divider.c.
 * See tb371fc/docs/camcc-sm8250.md for the source and timing contract.
 * Camera engines, analog PLL behavior and opaque registers are not modeled.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-camcc.h"
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

#define PLL_OUTCTRL BIT(0)
#define PLL_BYPASSNL BIT(1)
#define PLL_RESET_N BIT(2)
#define PLL_UPDATE BIT(22)
#define PLL_UPDATE_BYPASS BIT(23)
#define PLL_PCAL_DONE BIT(27)
#define PLL_ACK BIT(29)
#define PLL_LOCK BIT(31)
#define RCG_UPDATE BIT(0)
#define RCG_ROOT_EN BIT(1)
#define CLOCK_OFF BIT(31)
#define RCG_CFG_MASK 0x0010371fU
#define PLL_LOCK_NS 5000
#define PLL_LATCH_NS 1000
#define ZONDA_SETTLE_NS 5000
#define ZONDA_BYPASS_NS 1000
#define RCG_UPDATE_NS 1000
#define PLL_RATE_MARGIN_HZ 500
#define NO_PARENT (-127)

enum {
    R_MODE, R_L, R_CAL_L, R_USER, R_USER_U, R_USER_U1,
    R_CONFIG, R_CONFIG_U, R_CONFIG_U1, R_TEST, R_TEST_U, R_TEST_U1,
    R_OPMODE, R_ALPHA, PLL_FIELDS,
};
enum { RC_CMD, RC_CFG, RC_M, RC_N, RC_D, RCG_FIELDS };
enum { REG_PLL, REG_RCG, REG_BRANCH, REG_DIVIDER, REG_RESET };
enum { PLL_LUCID, PLL_ZONDA };
enum { I_XO, I_XO_AO, I_TEST, I_SLEEP, I_AUX2 };

typedef struct CamccRegister {
    uint32_t offset, write_mask, status_mask, stored_mask;
    unsigned kind, index, field;
} CamccRegister;
typedef struct CamccPll {
    unsigned clock_id, kind;
    int reg[PLL_FIELDS];
} CamccPll;
typedef struct CamccPostdiv {
    unsigned clock_id, pll, shift, width, selector, divisor;
} CamccPostdiv;
typedef struct CamccRcg {
    unsigned clock_id, mnd_width;
    int reg[RCG_FIELDS];
    int parents[8];
} CamccRcg;
typedef struct CamccBranch {
    unsigned clock_id, reg;
    int parent;
} CamccBranch;
typedef struct CamccDivider {
    unsigned clock_id, reg;
    int parent;
    unsigned shift, width;
} CamccDivider;
typedef struct CamccReset {
    unsigned reg;
    uint32_t mask;
} CamccReset;

#include "qcom-sm8250-camcc-data.inc"

#define PREG(s, p, field) ((s)->regs[camcc_plls[p].reg[field]])
#define RREG(s, r, field) ((s)->regs[camcc_rcgs[r].reg[field]])

static int camcc_register(hwaddr offset)
{
    unsigned lo = 0, hi = CAMCC_REGISTERS;

    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;

        if (camcc_registers[mid].offset < offset) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo < CAMCC_REGISTERS && camcc_registers[lo].offset == offset ?
           lo : -1;
}

static bool camcc_user_supported(unsigned p, uint32_t value)
{
    uint32_t group = camcc_plls[p].kind == PLL_ZONDA ? 15 : 7;
    unsigned i;

    if ((value & group) != 0 && (value & group) != group) {
        return false;
    }
    for (i = 0; i < CAMCC_POSTDIVS; i++) {
        const CamccPostdiv *d = &camcc_postdivs[i];
        uint32_t selector;

        if (d->pll != p) {
            continue;
        }
        selector = (value >> d->shift) & ((1U << d->width) - 1);
        if (selector && selector != d->selector) {
            return false;
        }
    }
    return true;
}

static bool camcc_recipe(QcomSm8250CamccState *s, unsigned p)
{
    if (PREG(s, p, R_TEST) || PREG(s, p, R_TEST_U) ||
        PREG(s, p, R_TEST_U1)) {
        return false;
    }
    if (camcc_plls[p].kind == PLL_ZONDA) {
        return PREG(s, p, R_CONFIG) == 0x08200920 &&
               PREG(s, p, R_CONFIG_U) == 0x05008011 &&
               PREG(s, p, R_CONFIG_U1) == 0;
    }
    return PREG(s, p, R_CAL_L) == 0x44 &&
           PREG(s, p, R_CONFIG) == 0x20485699 &&
           PREG(s, p, R_CONFIG_U) == 0x2261 &&
           PREG(s, p, R_CONFIG_U1) == 0x029a699c &&
           PREG(s, p, R_USER_U) == 0x805 &&
           PREG(s, p, R_USER_U1) == 0;
}

static uint64_t camcc_pll_frequency(QcomSm8250CamccState *s, unsigned p,
                                   uint32_t l, uint32_t alpha)
{
    bool zonda = camcc_plls[p].kind == PLL_ZONDA;
    unsigned width = zonda ? 32 : 16;
    uint64_t xo = clock_get_hz(s->input[I_XO]);
    uint64_t minimum = zonda ? 595200000 : 249600000;
    uint64_t maximum = zonda ? 3600000000ULL : 2000000000ULL;
    uint64_t numerator, factor, max_factor, hz;

    if (!xo || xo > maximum + PLL_RATE_MARGIN_HZ || !l || l > 0xffff ||
        (!zonda && alpha > 0xffff)) {
        return 0;
    }
    factor = ((uint64_t)l << width) | alpha;
    numerator = maximum << width;
    /* One rounded-up endpoint, as allowed by the fixed driver's margin. */
    max_factor = numerator / xo + !!(numerator % xo);
    if (factor > max_factor) {
        return 0;
    }
    /* Avoid overflowing xo * factor for Zonda's Q32 representation. */
    hz = xo * l + ((xo * alpha) >> width);
    return hz >= minimum && hz <= maximum + PLL_RATE_MARGIN_HZ ? hz : 0;
}

static bool camcc_pll_running(QcomSm8250CamccState *s, unsigned p)
{
    uint32_t mode = PREG(s, p, R_MODE);

    return s->enabled_reference && (mode & PLL_RESET_N) &&
           PREG(s, p, R_OPMODE) == 1 && camcc_recipe(s, p) &&
           (camcc_plls[p].kind != PLL_ZONDA ||
            ((mode & PLL_BYPASSNL) && s->bypass_since[p] >= 0 &&
             s->latch_deadline[p] < 0)) &&
           camcc_pll_frequency(s, p, s->active_l[p], s->active_alpha[p]);
}

static bool camcc_cfg_valid(unsigned r, uint32_t cfg, uint32_t m,
                            uint32_t n, uint32_t d)
{
    uint32_t mode = (cfg >> 12) & 3;
    uint32_t denominator;

    if ((cfg & ~RCG_CFG_MASK) || ((m | n | d) & ~0xffU) ||
        camcc_rcgs[r].parents[(cfg >> 8) & 7] == NO_PARENT) {
        return false;
    }
    if (!camcc_rcgs[r].mnd_width && (m || n || d)) {
        return false;
    }
    if (!mode) {
        /* The driver's integer entries leave old M/N/D storage untouched. */
        return true;
    }
    denominator = ((~n) & 0xff) + m;
    return mode == 2 && camcc_rcgs[r].mnd_width == 8 && m &&
           m < denominator && denominator <= 255 &&
           d == ((~denominator) & 0xff);
}

static uint32_t camcc_rcg_stored(QcomSm8250CamccState *s, unsigned r,
                                unsigned field)
{
    int reg = camcc_rcgs[r].reg[field];

    return reg < 0 ? 0 : s->regs[reg];
}

static uint64_t camcc_parent_rate(QcomSm8250CamccState *s, int parent,
                                  const uint64_t *rates)
{
    return parent == NO_PARENT ? 0 : parent < 0 ?
           clock_get_hz(s->input[-parent - 1]) : rates[parent];
}

static void camcc_rates(QcomSm8250CamccState *s, uint64_t *rates)
{
    bool demand[CAMCC_CLOCKS] = { false };
    unsigned i;

    memset(rates, 0, sizeof(uint64_t) * CAMCC_CLOCKS);
    if (!s->enabled_reference) {
        return;
    }
    for (i = 0; i < CAMCC_PLLS; i++) {
        uint32_t group = camcc_plls[i].kind == PLL_ZONDA ? 15 : 7;

        if (s->pll_locked[i] && camcc_pll_running(s, i) &&
            (PREG(s, i, R_MODE) & PLL_OUTCTRL) &&
            (PREG(s, i, R_USER) & group) == group) {
            rates[camcc_plls[i].clock_id] =
                camcc_pll_frequency(s, i, s->active_l[i], s->active_alpha[i]);
        }
    }
    for (i = 0; i < CAMCC_POSTDIVS; i++) {
        const CamccPostdiv *d = &camcc_postdivs[i];
        uint32_t selector = (PREG(s, d->pll, R_USER) >> d->shift) &
                            ((1U << d->width) - 1);

        /* Empty table terminators are not evidence of selector0/divide1. */
        if (selector == d->selector) {
            rates[d->clock_id] =
                rates[camcc_plls[d->pll].clock_id] / d->divisor;
        }
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        demand[camcc_rcgs[i].clock_id] = RREG(s, i, RC_CMD) & RCG_ROOT_EN;
    }
    for (i = 0; i < CAMCC_BRANCHES; i++) {
        if ((s->regs[camcc_branches[i].reg] & 1) &&
            camcc_branches[i].parent >= 0) {
            demand[camcc_branches[i].parent] = true;
        }
    }
    if (demand[camcc_sbi_divider.clock_id]) {
        demand[camcc_sbi_divider.parent] = true;
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        uint32_t cfg = s->active_cfg[i], hid = cfg & 0x1f;
        uint64_t parent, hz;

        if (!demand[camcc_rcgs[i].clock_id] ||
            !camcc_cfg_valid(i, cfg, s->active_m[i], s->active_n[i],
                            s->active_d[i])) {
            continue;
        }
        parent = camcc_parent_rate(s, camcc_rcgs[i].parents[(cfg >> 8) & 7],
                                   rates);
        hz = hid ? parent / (hid + 1) * 2 +
                   (parent % (hid + 1)) * 2 / (hid + 1) : parent;
        if (((cfg >> 12) & 3) == 2) {
            uint32_t denominator = ((~s->active_n[i]) & 0xff) + s->active_m[i];

            hz = hz * s->active_m[i] / denominator;
        }
        rates[camcc_rcgs[i].clock_id] = hz;
    }
    {
        uint64_t parent = rates[camcc_sbi_divider.parent];
        unsigned divisor = 1 +
            ((s->regs[camcc_sbi_divider.reg] >> camcc_sbi_divider.shift) &
             ((1U << camcc_sbi_divider.width) - 1));

        /* clk-regmap-divider uses generic divider_recalc_rate: round up. */
        rates[camcc_sbi_divider.clock_id] = parent / divisor +
                                          !!(parent % divisor);
    }
    for (i = 0; i < CAMCC_BRANCHES; i++) {
        if (s->regs[camcc_branches[i].reg] & 1) {
            rates[camcc_branches[i].clock_id] =
                camcc_parent_rate(s, camcc_branches[i].parent, rates);
        }
    }
}

static void camcc_reset_outputs(QcomSm8250CamccState *s)
{
    unsigned i;

    for (i = 0; i < CAMCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], s->enabled_reference &&
                     (s->regs[camcc_resets[i].reg] & camcc_resets[i].mask));
    }
}

static void camcc_publish(QcomSm8250CamccState *s, bool propagate)
{
    uint64_t rates[CAMCC_CLOCKS];
    unsigned i;

    camcc_rates(s, rates);
    for (i = 0; i < CAMCC_CLOCKS; i++) {
        bool changed = clock_set_hz(s->clocks[i], rates[i]);

        if (changed && propagate) {
            clock_propagate(s->clocks[i]);
        }
    }
    if (propagate) {
        camcc_reset_outputs(s);
    }
}

static void camcc_earlier(int64_t *next, int64_t value)
{
    if (value >= 0 && (*next < 0 || value < *next)) {
        *next = value;
    }
}

static void camcc_schedule(QcomSm8250CamccState *s)
{
    int64_t next = -1;
    unsigned i;

    for (i = 0; i < CAMCC_PLLS; i++) {
        camcc_earlier(&next, s->lock_deadline[i]);
        camcc_earlier(&next, s->latch_deadline[i]);
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        camcc_earlier(&next, s->rcg_deadline[i]);
    }
    if (next < 0) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static bool camcc_latch_valid(QcomSm8250CamccState *s, unsigned p)
{
    uint32_t mode = PREG(s, p, R_MODE);

    if (!s->enabled_reference || !(mode & PLL_RESET_N) ||
        !camcc_recipe(s, p) ||
        !camcc_pll_frequency(s, p, s->pending_l[p], s->pending_alpha[p])) {
        return false;
    }
    if (camcc_plls[p].kind == PLL_ZONDA) {
        return (mode & PLL_BYPASSNL) && s->bypass_since[p] >= 0 &&
               PREG(s, p, R_OPMODE) == 1;
    }
    return (mode & PLL_UPDATE) && (mode & PLL_UPDATE_BYPASS);
}

static void camcc_update(QcomSm8250CamccState *s)
{
    int64_t now;
    uint64_t xo;
    bool xo_changed;
    unsigned i;

    if (!s->ready || s->updating) {
        return;
    }
    s->updating = true;
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    xo = clock_get_hz(s->input[I_XO]);
    xo_changed = xo != s->last_xo_hz;
    s->last_xo_hz = xo;
    for (i = 0; i < CAMCC_PLLS; i++) {
        if (xo_changed) {
            s->pll_locked[i] = false;
            s->lock_deadline[i] = -1;
        }
        if (s->latch_deadline[i] >= 0) {
            if (!camcc_latch_valid(s, i)) {
                s->latch_deadline[i] = -1;
                if (camcc_plls[i].kind == PLL_ZONDA) {
                    s->active_l[i] = s->active_alpha[i] = 0;
                }
            } else if (now >= s->latch_deadline[i]) {
                int64_t deadline = s->latch_deadline[i];

                s->active_l[i] = s->pending_l[i];
                s->active_alpha[i] = s->pending_alpha[i];
                s->pll_ack[i] = camcc_plls[i].kind == PLL_LUCID;
                s->pll_locked[i] = false;
                s->latch_deadline[i] = -1;
                s->lock_deadline[i] = deadline + PLL_LOCK_NS;
                if (xo_changed) {
                    s->lock_deadline[i] = MAX(s->lock_deadline[i],
                                               now + PLL_LOCK_NS);
                }
            }
        }
        if (!camcc_pll_running(s, i)) {
            s->pll_locked[i] = false;
            s->lock_deadline[i] = -1;
        } else if (!s->pll_locked[i]) {
            if (s->lock_deadline[i] < 0) {
                s->lock_deadline[i] = now + PLL_LOCK_NS;
            } else if (now >= s->lock_deadline[i]) {
                s->pll_locked[i] = true;
                s->pll_calibrated[i] = camcc_plls[i].kind == PLL_LUCID;
                s->lock_deadline[i] = -1;
            }
        }
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        if (s->rcg_deadline[i] >= 0 && now >= s->rcg_deadline[i]) {
            s->active_cfg[i] = s->pending_cfg[i];
            s->active_m[i] = s->pending_m[i];
            s->active_n[i] = s->pending_n[i];
            s->active_d[i] = s->pending_d[i];
            RREG(s, i, RC_CMD) &= ~RCG_UPDATE;
            s->rcg_deadline[i] = -1;
        }
    }
    camcc_publish(s, true);
    camcc_schedule(s);
    s->updating = false;
}

static void camcc_timer(void *opaque)
{
    camcc_update(opaque);
}

static void camcc_clock_changed(void *opaque, ClockEvent event)
{
    camcc_update(opaque);
}

static MemTxResult camcc_read(void *opaque, hwaddr address, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomCamccWindow *w = opaque;
    QcomSm8250CamccState *s = w->state;
    int id = camcc_register(w->offset + address);
    const CamccRegister *reg;
    uint64_t rates[CAMCC_CLOCKS];

    *value = 0;
    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_CAMCC
                      ": unmodeled read +0x%" HWADDR_PRIx " size %u\n",
                      w->offset + address, size);
        return MEMTX_ERROR;
    }
    camcc_update(s);
    reg = &camcc_registers[id];
    *value = s->regs[id];
    if (reg->kind == REG_PLL && reg->field == R_MODE) {
        unsigned p = reg->index;

        *value |= s->pll_locked[p] ? PLL_LOCK : 0;
        if (camcc_plls[p].kind == PLL_LUCID) {
            *value |= s->pll_ack[p] ? PLL_ACK : 0;
            *value |= s->pll_calibrated[p] ? PLL_PCAL_DONE : 0;
        }
        /* Zonda bit29 is FREQ_LOCK; the known v2 recipe is not CFA mode. */
    } else if (reg->kind == REG_RCG && reg->field == RC_CMD) {
        camcc_rates(s, rates);
        if (!rates[camcc_rcgs[reg->index].clock_id]) {
            *value |= CLOCK_OFF;
        }
    } else if (reg->kind == REG_BRANCH) {
        camcc_rates(s, rates);
        if (!rates[camcc_branches[reg->index].clock_id]) {
            *value |= CLOCK_OFF;
        }
    }
    return MEMTX_OK;
}

static void camcc_start_pll(QcomSm8250CamccState *s, unsigned p, int64_t now)
{
    uint32_t mode = PREG(s, p, R_MODE);
    bool valid = (mode & PLL_RESET_N) && PREG(s, p, R_OPMODE) == 1 &&
                 camcc_recipe(s, p) &&
                 camcc_pll_frequency(s, p, PREG(s, p, R_L),
                                     PREG(s, p, R_ALPHA));

    s->pll_locked[p] = false;
    s->lock_deadline[p] = -1;
    if (camcc_plls[p].kind == PLL_ZONDA) {
        valid = valid && (mode & PLL_BYPASSNL) && s->bypass_since[p] >= 0 &&
                now - s->bypass_since[p] >= ZONDA_BYPASS_NS;
        s->latch_deadline[p] = -1;
    }
    if (valid) {
        s->active_l[p] = PREG(s, p, R_L);
        s->active_alpha[p] = PREG(s, p, R_ALPHA);
    } else {
        s->active_l[p] = s->active_alpha[p] = 0;
    }
}

static void camcc_pll_write(QcomSm8250CamccState *s, unsigned p,
                            unsigned field, uint32_t old, uint32_t val)
{
    bool zonda = camcc_plls[p].kind == PLL_ZONDA;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (field == R_MODE) {
        if (zonda) {
            if (!(val & PLL_BYPASSNL)) {
                s->bypass_since[p] = -1;
                s->active_l[p] = s->active_alpha[p] = 0;
                s->latch_deadline[p] = -1;
            } else if (!(old & PLL_BYPASSNL)) {
                s->bypass_since[p] = now;
            }
        }
        if (!(val & PLL_RESET_N)) {
            s->pll_locked[p] = s->pll_ack[p] = s->pll_calibrated[p] = false;
            s->active_l[p] = s->active_alpha[p] = 0;
            s->pending_l[p] = s->pending_alpha[p] = 0;
            s->lock_deadline[p] = s->latch_deadline[p] = -1;
        } else if (!(old & PLL_RESET_N)) {
            camcc_start_pll(s, p, now);
        }
        if (!zonda) {
            if (!(val & PLL_UPDATE) || !(val & PLL_UPDATE_BYPASS)) {
                s->pll_ack[p] = false;
                s->latch_deadline[p] = -1;
            } else if (!(old & PLL_UPDATE)) {
                s->pll_ack[p] = false;
                s->latch_deadline[p] = -1;
                if ((val & PLL_RESET_N) && (val & PLL_UPDATE_BYPASS) &&
                    camcc_recipe(s, p) &&
                    camcc_pll_frequency(s, p, PREG(s, p, R_L),
                                        PREG(s, p, R_ALPHA))) {
                    s->pending_l[p] = PREG(s, p, R_L);
                    s->pending_alpha[p] = PREG(s, p, R_ALPHA);
                    s->latch_deadline[p] = now + PLL_LATCH_NS;
                }
            }
        }
    } else if (field == R_OPMODE && val != old) {
        camcc_start_pll(s, p, now);
    } else if (zonda && field == R_L && PREG(s, p, R_OPMODE) == 1 &&
               (PREG(s, p, R_MODE) & PLL_RESET_N)) {
        /* Zonda has no UPDATE/ACK: L captures the preceding ALPHA write. */
        s->pll_locked[p] = false;
        s->lock_deadline[p] = s->latch_deadline[p] = -1;
        s->pending_l[p] = PREG(s, p, R_L);
        s->pending_alpha[p] = PREG(s, p, R_ALPHA);
        if (camcc_latch_valid(s, p) &&
            now - s->bypass_since[p] >= ZONDA_BYPASS_NS) {
            s->latch_deadline[p] = now + ZONDA_SETTLE_NS;
        } else {
            s->active_l[p] = s->active_alpha[p] = 0;
        }
    }
}

static MemTxResult camcc_write(void *opaque, hwaddr address, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomCamccWindow *w = opaque;
    QcomSm8250CamccState *s = w->state;
    int id = camcc_register(w->offset + address);
    const CamccRegister *reg;
    uint32_t old, val;

    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_CAMCC
                      ": unmodeled write +0x%" HWADDR_PRIx " size %u\n",
                      w->offset + address, size);
        return MEMTX_ERROR;
    }
    reg = &camcc_registers[id];
    if (reg->kind == REG_DIVIDER) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_CAMCC
                      ": read-only SBI divider +0x%" HWADDR_PRIx "\n",
                      w->offset + address);
        return MEMTX_ERROR;
    }
    if (value & ~(uint64_t)(reg->write_mask | reg->status_mask)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_CAMCC
                      ": unsupported bits +0x%" HWADDR_PRIx "\n",
                      w->offset + address);
        return MEMTX_ERROR;
    }
    val = value & reg->write_mask;
    if (reg->kind == REG_PLL && reg->field == R_USER &&
        !camcc_user_supported(reg->index, val)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_CAMCC
                      ": unsupported PLL output/postdivider selection\n");
        return MEMTX_ERROR;
    }
    camcc_update(s);
    old = s->regs[id];
    s->regs[id] = val;
    if (reg->kind == REG_PLL) {
        camcc_pll_write(s, reg->index, reg->field, old, val);
    } else if (reg->kind == REG_RCG && reg->field == RC_CMD) {
        unsigned r = reg->index;
        uint32_t cfg = RREG(s, r, RC_CFG);
        uint32_t m = camcc_rcg_stored(s, r, RC_M);
        uint32_t n = camcc_rcg_stored(s, r, RC_N);
        uint32_t d = camcc_rcg_stored(s, r, RC_D);

        if (!(val & RCG_UPDATE)) {
            s->rcg_deadline[r] = -1;
        } else if (!(old & RCG_UPDATE) && camcc_cfg_valid(r, cfg, m, n, d)) {
            s->pending_cfg[r] = cfg;
            s->pending_m[r] = m;
            s->pending_n[r] = n;
            s->pending_d[r] = d;
            s->rcg_deadline[r] =
                qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + RCG_UPDATE_NS;
        }
    }
    camcc_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps camcc_ops = {
    .read_with_attrs = camcc_read,
    .write_with_attrs = camcc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void camcc_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    unsigned i;

    s->ready = false;
    s->updating = false;
    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    for (i = 0; i < CAMCC_PLLS; i++) {
        s->active_l[i] = s->active_alpha[i] = 0;
        s->pending_l[i] = s->pending_alpha[i] = 0;
        s->pll_locked[i] = s->pll_ack[i] = s->pll_calibrated[i] = false;
        s->lock_deadline[i] = s->latch_deadline[i] = s->bypass_since[i] = -1;
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        s->active_cfg[i] = s->active_m[i] = s->active_n[i] = s->active_d[i] = 0;
        s->pending_cfg[i] = s->pending_m[i] = 0;
        s->pending_n[i] = s->pending_d[i] = 0;
        s->rcg_deadline[i] = -1;
    }
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
    /* Input carriers belong to their providers and survive a device reset. */
}

static void camcc_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);

    s->ready = true;
    camcc_publish(s, true);
}

static int camcc_pre_load(void *opaque)
{
    QcomSm8250CamccState *s = opaque;
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    for (i = 0; i < CAMCC_CLOCKS; i++) {
        clock_set_hz(s->clocks[i], 0);
    }
    for (i = 0; i < CAMCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], 0);
    }
    return 0;
}

static int camcc_bad_state(const char *what, unsigned index)
{
    error_report(TYPE_QCOM_SM8250_CAMCC ": invalid migrated %s %u",
                 what, index);
    return -EINVAL;
}

static bool camcc_deadline_valid(int64_t deadline, int64_t now,
                                 uint64_t maximum_remaining)
{
    return deadline >= -1 &&
           (deadline <= now || (uint64_t)(deadline - now) <= maximum_remaining);
}

static int camcc_post_load(void *opaque, int version_id)
{
    QcomSm8250CamccState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (now < 0) {
        return camcc_bad_state("virtual clock", 0);
    }
    for (i = 0; i < CAMCC_INPUTS; i++) {
        /* Read the incoming Clock ratio; never alter a provider's state. */
        if (s->input[i]->multiplier != 1 || s->input[i]->divider != 1 ||
            CLOCK_PERIOD_TO_HZ(clock_get(s->input[i])) > UINT32_MAX) {
            return camcc_bad_state("input clock", i);
        }
    }
    if (s->last_xo_hz != clock_get_hz(s->input[I_XO])) {
        return camcc_bad_state("XO observation", 0);
    }
    for (i = 0; i < CAMCC_REGISTERS; i++) {
        if ((s->regs[i] & ~camcc_registers[i].stored_mask) ||
            (!s->enabled_reference && s->regs[i])) {
            return camcc_bad_state("register", i);
        }
    }
    for (i = 0; i < CAMCC_PLLS; i++) {
        bool zonda = camcc_plls[i].kind == PLL_ZONDA;
        uint32_t mode = PREG(s, i, R_MODE);
        bool running = camcc_pll_running(s, i);

        if (!camcc_user_supported(i, PREG(s, i, R_USER)) ||
            s->active_l[i] > 0xffff || s->pending_l[i] > 0xffff ||
            (!zonda && (s->active_alpha[i] > 0xffff ||
                        s->pending_alpha[i] > 0xffff)) ||
            (!s->active_l[i] && s->active_alpha[i]) ||
            (!zonda && !s->pending_l[i] && s->pending_alpha[i]) ||
            !camcc_deadline_valid(s->lock_deadline[i], now, PLL_LOCK_NS) ||
            !camcc_deadline_valid(s->latch_deadline[i], now,
                                 zonda ? ZONDA_SETTLE_NS : PLL_LATCH_NS) ||
            s->bypass_since[i] < -1 || s->bypass_since[i] > now ||
            (!zonda && s->bypass_since[i] != -1) ||
            (zonda && ((mode & PLL_BYPASSNL) != 0) !=
                       (s->bypass_since[i] >= 0)) ||
            (zonda && (s->pll_ack[i] || s->pll_calibrated[i])) ||
            (zonda && running && now - s->bypass_since[i] < ZONDA_BYPASS_NS) ||
            (s->pll_locked[i] && (!running || s->lock_deadline[i] >= 0 ||
                                 (!zonda && !s->pll_calibrated[i]))) ||
            (s->lock_deadline[i] >= 0 && !running) ||
            (s->pll_calibrated[i] && !(mode & PLL_RESET_N)) ||
            (s->pll_ack[i] && (!(mode & PLL_UPDATE) ||
                              !(mode & PLL_UPDATE_BYPASS) ||
                              !(mode & PLL_RESET_N))) ||
            (s->latch_deadline[i] >= 0 &&
             (!camcc_latch_valid(s, i) || s->pll_ack[i] ||
              (zonda && (s->pll_locked[i] || s->lock_deadline[i] >= 0)))) ||
            (!(mode & PLL_RESET_N) &&
             (s->active_l[i] || s->active_alpha[i] || s->pending_l[i] ||
              s->pending_alpha[i] || s->pll_locked[i] || s->pll_ack[i] ||
              s->pll_calibrated[i] || s->lock_deadline[i] >= 0 ||
              s->latch_deadline[i] >= 0))) {
            return camcc_bad_state("PLL", i);
        }
        if (!s->enabled_reference &&
            (s->active_l[i] || s->active_alpha[i] || s->pending_l[i] ||
             s->pending_alpha[i] || s->pll_locked[i] || s->pll_ack[i] ||
             s->pll_calibrated[i] || s->lock_deadline[i] >= 0 ||
             s->latch_deadline[i] >= 0 || s->bypass_since[i] >= 0)) {
            return camcc_bad_state("disabled PLL", i);
        }
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        if (!camcc_cfg_valid(i, s->active_cfg[i], s->active_m[i],
                            s->active_n[i], s->active_d[i]) ||
            !camcc_cfg_valid(i, s->pending_cfg[i], s->pending_m[i],
                            s->pending_n[i], s->pending_d[i]) ||
            !camcc_deadline_valid(s->rcg_deadline[i], now, RCG_UPDATE_NS) ||
            (s->rcg_deadline[i] >= 0 && !(RREG(s, i, RC_CMD) & RCG_UPDATE)) ||
            (!s->enabled_reference &&
             (s->active_cfg[i] || s->active_m[i] || s->active_n[i] ||
              s->active_d[i] || s->pending_cfg[i] || s->pending_m[i] ||
              s->pending_n[i] || s->pending_d[i] || s->rcg_deadline[i] >= 0))) {
            return camcc_bad_state("RCG", i);
        }
    }
    s->updating = false;
    s->ready = true;
    /* Derive outputs before first target MMIO, without consuming deadlines. */
    camcc_publish(s, false);
    camcc_reset_outputs(s);
    camcc_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_camcc = {
    .name = TYPE_QCOM_SM8250_CAMCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = camcc_pre_load,
    .post_load = camcc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(register_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(clock_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(input_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(pll_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(rcg_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(branch_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(reset_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(window_count, QcomSm8250CamccState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomSm8250CamccState),
        VMSTATE_UINT64_EQUAL(physical_base, QcomSm8250CamccState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250CamccState, CAMCC_REGISTERS),
        VMSTATE_UINT32_ARRAY(active_l, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_UINT32_ARRAY(active_alpha, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_UINT32_ARRAY(pending_l, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_UINT32_ARRAY(pending_alpha, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_UINT32_ARRAY(active_cfg, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(active_m, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(active_n, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(active_d, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_cfg, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_m, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_n, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_d, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_INT64_ARRAY(lock_deadline, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_INT64_ARRAY(latch_deadline, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_INT64_ARRAY(bypass_since, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_INT64_ARRAY(rcg_deadline, QcomSm8250CamccState, CAMCC_RCGS),
        VMSTATE_UINT64(last_xo_hz, QcomSm8250CamccState),
        VMSTATE_BOOL_ARRAY(pll_locked, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_BOOL_ARRAY(pll_ack, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_BOOL_ARRAY(pll_calibrated, QcomSm8250CamccState, CAMCC_PLLS),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250CamccState, CAMCC_INPUTS),
        VMSTATE_END_OF_LIST()
    },
};

static void camcc_fixture_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    uint32_t hz = clock_get_hz(opaque);

    visit_type_uint32(v, name, &hz, errp);
}

static void camcc_fixture_set(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    Clock *input = opaque;
    uint32_t hz;

    if (!visit_type_uint32(v, name, &hz, errp)) {
        return;
    }
    if (clock_has_source(input)) {
        error_setg(errp, "%s cannot override a connected clock source", name);
        return;
    }
    /* clock_get_hz returns unsigned; reject a quantized period that wraps. */
    if (CLOCK_PERIOD_TO_HZ(CLOCK_PERIOD_FROM_HZ(hz)) > UINT32_MAX) {
        error_setg(errp, "%s exceeds the representable input clock range", name);
        return;
    }
    clock_set_hz(input, hz);
    camcc_update(s);
}

static void camcc_sbi_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    uint32_t value = s->regs[camcc_sbi_divider.reg];

    visit_type_uint32(v, name, &value, errp);
}

static void camcc_sbi_set(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    uint32_t value;

    if (!visit_type_uint32(v, name, &value, errp)) {
        return;
    }
    if (!s->enabled_reference || value > 7) {
        error_setg(errp, "%s requires an enabled reference and a 3-bit value",
                   name);
        return;
    }
    s->regs[camcc_sbi_divider.reg] = value;
    camcc_update(s);
}

static void camcc_rate_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    uint64_t rates[CAMCC_CLOCKS];

    camcc_rates(s, rates);
    visit_type_uint64(v, name, &rates[GPOINTER_TO_UINT(opaque)], errp);
}

static void camcc_signed_get(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    int64_t value = *(int64_t *)opaque;

    visit_type_int64(v, name, &value, errp);
}

static void camcc_status_get(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    unsigned id = GPOINTER_TO_UINT(opaque);
    bool value;

    if (id < 6 * CAMCC_PLLS) {
        unsigned p = id / 6;
        bool flags[] = {
            s->pll_locked[p], s->pll_ack[p], s->pll_calibrated[p], false,
            s->lock_deadline[p] >= 0, s->latch_deadline[p] >= 0,
        };

        value = flags[id % 6];
    } else {
        value = s->rcg_deadline[id - 6 * CAMCC_PLLS] >= 0;
    }
    visit_type_bool(v, name, &value, errp);
}

static void camcc_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(dev);

    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->ready = true;
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
}

static void camcc_init(Object *obj)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const statuses[] = {
        "locked", "ack", "calibrated", "freq-locked",
        "lock-pending", "latch-pending",
    };
    static const char *const pll_values[] = {
        "active-l", "active-alpha", "pending-l", "pending-alpha",
    };
    static const char *const rcg_values[] = {
        "active-cfg", "active-m", "active-n", "active-d",
        "pending-cfg", "pending-m", "pending-n", "pending-d",
    };
    static const char *const pll_times[] = {
        "lock-deadline", "latch-deadline", "bypass-since",
    };
    uint32_t *pll_arrays[] = {
        s->active_l, s->active_alpha, s->pending_l, s->pending_alpha,
    };
    uint32_t *rcg_arrays[] = {
        s->active_cfg, s->active_m, s->active_n, s->active_d,
        s->pending_cfg, s->pending_m, s->pending_n, s->pending_d,
    };
    int64_t *time_arrays[] = {
        s->lock_deadline, s->latch_deadline, s->bypass_since,
    };
    unsigned i, j;

    s->reference_identity = CAMCC_REFERENCE_IDENTITY;
    s->register_count = CAMCC_REGISTERS;
    s->clock_count = CAMCC_CLOCKS;
    s->input_count = CAMCC_INPUTS;
    s->pll_count = CAMCC_PLLS;
    s->rcg_count = CAMCC_RCGS;
    s->branch_count = CAMCC_BRANCHES;
    s->reset_count = CAMCC_RESETS;
    s->window_count = CAMCC_WINDOWS;
    s->mapped_bytes = CAMCC_MAPPED_BYTES;
    s->physical_base = CAMCC_PHYSICAL_BASE;
#define CAMCC_U32_PROPERTY(name, field) \
    object_property_add_uint32_ptr(obj, name, &s->field, OBJ_PROP_FLAG_READ)
    CAMCC_U32_PROPERTY("reference-profile", reference_profile);
    CAMCC_U32_PROPERTY("reference-identity", reference_identity);
    CAMCC_U32_PROPERTY("register-count", register_count);
    CAMCC_U32_PROPERTY("clock-count", clock_count);
    CAMCC_U32_PROPERTY("input-count", input_count);
    CAMCC_U32_PROPERTY("pll-count", pll_count);
    CAMCC_U32_PROPERTY("rcg-count", rcg_count);
    CAMCC_U32_PROPERTY("branch-count", branch_count);
    CAMCC_U32_PROPERTY("reset-count", reset_count);
    CAMCC_U32_PROPERTY("window-count", window_count);
    CAMCC_U32_PROPERTY("mapped-bytes", mapped_bytes);
#undef CAMCC_U32_PROPERTY
    object_property_add_uint64_ptr(obj, "physical-base", &s->physical_base,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "last-xo-hz", &s->last_xo_hz,
                                   OBJ_PROP_FLAG_READ);
    /*
     * SysBus has 32 export slots. A transparent container exports all 74
     * sparse leaves without handlers or storage in any intervening hole.
     */
    memory_region_init(&s->container, obj, "camcc-sparse", 0x10000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container);
    for (i = 0; i < CAMCC_WINDOWS; i++) {
        const QcomCamccWindowDef *d = &qcom_sm8250_camcc_windows[i];
        g_autofree char *name =
            g_strdup_printf(TYPE_QCOM_SM8250_CAMCC ".%04x", d->offset);

        s->windows[i].state = s;
        s->windows[i].offset = d->offset;
        memory_region_init_io(&s->iomem[i], obj, &camcc_ops, &s->windows[i],
                              name, d->size);
        memory_region_add_subregion(&s->container, d->offset, &s->iomem[i]);
    }
    for (i = 0; i < CAMCC_REGISTERS; i++) {
        g_autofree char *name =
            g_strdup_printf("reg.%04x", camcc_registers[i].offset);

        object_property_add_uint32_ptr(obj, name, &s->regs[i],
                                       OBJ_PROP_FLAG_READ);
    }
    for (i = 0; i < CAMCC_INPUTS; i++) {
        s->input[i] = qdev_init_clock_in(dev, camcc_input_names[i],
                                        camcc_clock_changed, s, ClockUpdate);
        if (qtest_enabled()) {
            g_autofree char *name =
                g_strdup_printf("qtest-%s-hz", camcc_input_names[i]);

            object_property_add(obj, name, "uint32", camcc_fixture_get,
                                camcc_fixture_set, NULL, s->input[i]);
        }
    }
    for (i = 0; i < CAMCC_CLOCKS; i++) {
        g_autofree char *name =
            g_strdup_printf("clk.%s.hz", camcc_clock_names[i]);

        s->clocks[i] = qdev_init_clock_out(dev, camcc_clock_names[i]);
        object_property_add(obj, name, "uint64", camcc_rate_get, NULL,
                            NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < CAMCC_PLLS; i++) {
        for (j = 0; j < ARRAY_SIZE(statuses); j++) {
            g_autofree char *name = g_strdup_printf("pll%u-%s", i, statuses[j]);

            object_property_add(obj, name, "bool", camcc_status_get,
                                NULL, NULL, GUINT_TO_POINTER(i * 6 + j));
        }
        for (j = 0; j < ARRAY_SIZE(pll_values); j++) {
            g_autofree char *name =
                g_strdup_printf("pll%u-%s", i, pll_values[j]);

            object_property_add_uint32_ptr(obj, name, &pll_arrays[j][i],
                                           OBJ_PROP_FLAG_READ);
        }
        for (j = 0; j < ARRAY_SIZE(pll_times); j++) {
            g_autofree char *name =
                g_strdup_printf("pll%u-%s", i, pll_times[j]);

            time_arrays[j][i] = -1;
            object_property_add(obj, name, "int64", camcc_signed_get,
                                NULL, NULL, &time_arrays[j][i]);
        }
    }
    for (i = 0; i < CAMCC_RCGS; i++) {
        g_autofree char *pending = g_strdup_printf("rcg%u-pending", i);
        g_autofree char *deadline = g_strdup_printf("rcg%u-deadline", i);

        s->rcg_deadline[i] = -1;
        object_property_add(obj, pending, "bool", camcc_status_get, NULL, NULL,
                            GUINT_TO_POINTER(6 * CAMCC_PLLS + i));
        object_property_add(obj, deadline, "int64", camcc_signed_get,
                            NULL, NULL, &s->rcg_deadline[i]);
        for (j = 0; j < ARRAY_SIZE(rcg_values); j++) {
            g_autofree char *name =
                g_strdup_printf("rcg%u-%s", i, rcg_values[j]);

            object_property_add_uint32_ptr(obj, name, &rcg_arrays[j][i],
                                           OBJ_PROP_FLAG_READ);
        }
    }
    object_property_add(obj, "sbi-divider", "uint32", camcc_sbi_get,
                        NULL, NULL, NULL);
    if (qtest_enabled()) {
        object_property_add(obj, "qtest-sbi-divider", "uint32", camcc_sbi_get,
                            camcc_sbi_set, NULL, NULL);
    }
    qdev_init_gpio_out_named(dev, s->reset_out, "reset", CAMCC_RESETS);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, camcc_timer, s);
}

static void camcc_finalize(Object *obj)
{
    QcomSm8250CamccState *s = QCOM_SM8250_CAMCC(obj);

    timer_free(s->timer);
}

static const Property camcc_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250CamccState,
                     enabled_reference, false),
};

static void camcc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm SM8250/Kona v2 CAMCC digital reference protocol";
    dc->realize = camcc_realize;
    dc->vmsd = &vmstate_camcc;
    device_class_set_props(dc, camcc_properties);
    rc->phases.enter = camcc_reset_enter;
    rc->phases.hold = camcc_reset_hold;
}

static const TypeInfo camcc_info = {
    .name = TYPE_QCOM_SM8250_CAMCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250CamccState),
    .instance_init = camcc_init,
    .instance_finalize = camcc_finalize,
    .class_init = camcc_class_init,
};

static void camcc_register_types(void)
{
    type_register_static(&camcc_info);
}
type_init(camcc_register_types)
