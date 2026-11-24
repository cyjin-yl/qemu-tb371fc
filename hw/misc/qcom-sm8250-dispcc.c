/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Bounded Kona DISPCC digital reference protocol.
 * OnePlusOSS/android_kernel_oneplus_sm8250 at
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae: dispcc-kona.c,
 * clk-alpha-pll.c, clk-rcg2.c, clk-branch.c and clk-regmap-divider.c.
 * See tb371fc/docs/dispcc-sm8250.md for the source and timing contract.
 * Display engines, analog PLL behavior and opaque registers are not modeled.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-dispcc.h"
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
#define PLL_LOCK_NS 5000
#define PLL_LATCH_NS 1000
#define RCG_UPDATE_NS 1000
#define PLL_RATE_MARGIN_HZ 500
#define NO_PARENT (-127)

enum {
    R_MODE, R_L, R_CAL_L, R_USER, R_USER_U, R_USER_U1,
    R_CONFIG, R_CONFIG_U, R_CONFIG_U1, R_TEST, R_TEST_U, R_TEST_U1,
    R_OPMODE, R_ALPHA, PLL_FIELDS,
};
enum { RC_CMD, RC_CFG, RC_M, RC_N, RC_D, RCG_FIELDS };
enum { REG_PLL, REG_RCG, REG_BRANCH, REG_DIVIDER, REG_RESET, REG_MISC };
enum { I_XO };

typedef struct DispccRegister {
    uint32_t offset, write_mask, status_mask, stored_mask;
    unsigned kind, index, field;
} DispccRegister;
typedef struct DispccPll {
    unsigned clock_id;
    int reg[PLL_FIELDS];
} DispccPll;
typedef struct DispccRcg {
    unsigned clock_id, mnd_width;
    uint32_t cfg_mask;
    int reg[RCG_FIELDS];
    int parents[8];
} DispccRcg;
typedef struct DispccBranch {
    unsigned clock_id, reg;
    int parent, remote_vote;
} DispccBranch;
typedef struct DispccDivider {
    unsigned clock_id, reg;
    int parent;
    unsigned shift, width;
    bool read_only;
} DispccDivider;
typedef struct DispccReset {
    unsigned reg;
    uint32_t mask;
} DispccReset;

#include "qcom-sm8250-dispcc-data.inc"

#define PREG(s, p, field) ((s)->regs[dispcc_plls[p].reg[field]])
#define RREG(s, r, field) ((s)->regs[dispcc_rcgs[r].reg[field]])

static int dispcc_register(hwaddr offset)
{
    unsigned lo = 0, hi = DISPCC_REGISTERS;

    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;

        if (dispcc_registers[mid].offset < offset) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo < DISPCC_REGISTERS && dispcc_registers[lo].offset == offset ?
           lo : -1;
}

static bool dispcc_user_supported(uint32_t value)
{
    return value == 0 || value == 7;
}

static bool dispcc_recipe(QcomSm8250DispccState *s, unsigned p)
{
    return !PREG(s, p, R_TEST) && !PREG(s, p, R_TEST_U) &&
           !PREG(s, p, R_TEST_U1) && PREG(s, p, R_CAL_L) == 0x44 &&
           PREG(s, p, R_CONFIG) == 0x20485699 &&
           PREG(s, p, R_CONFIG_U) == 0x2261 &&
           PREG(s, p, R_CONFIG_U1) == 0x029a699c &&
           PREG(s, p, R_USER_U) == 0x805 &&
           PREG(s, p, R_USER_U1) == 0;
}

static uint64_t dispcc_pll_frequency(QcomSm8250DispccState *s, unsigned p,
                                    uint32_t l, uint32_t alpha)
{
    uint64_t xo = clock_get_hz(s->input[I_XO]);
    uint64_t maximum = 2000000000ULL;
    uint64_t numerator, factor, max_factor, hz;

    if (!xo || xo > maximum + PLL_RATE_MARGIN_HZ || !l || l > 0xffff ||
        alpha > 0xffff) {
        return 0;
    }
    factor = ((uint64_t)l << 16) | alpha;
    numerator = maximum << 16;
    /* One rounded-up endpoint, as allowed by the fixed driver's margin. */
    max_factor = numerator / xo + !!(numerator % xo);
    if (factor > max_factor) {
        return 0;
    }
    hz = xo * l + ((xo * alpha) >> 16);
    return hz >= 249600000 && hz <= maximum + PLL_RATE_MARGIN_HZ ? hz : 0;
}

static bool dispcc_pll_running(QcomSm8250DispccState *s, unsigned p)
{
    return s->enabled_reference && (PREG(s, p, R_MODE) & PLL_RESET_N) &&
           PREG(s, p, R_OPMODE) == 1 && dispcc_recipe(s, p) &&
           dispcc_pll_frequency(s, p, s->active_l[p], s->active_alpha[p]);
}

static bool dispcc_cfg_valid(unsigned r, uint32_t cfg, uint32_t m,
                             uint32_t n, uint32_t d)
{
    const DispccRcg *root = &dispcc_rcgs[r];
    uint32_t mask = (1U << root->mnd_width) - 1;
    uint32_t mode = (cfg >> 12) & 3;
    uint32_t denominator;

    if ((cfg & ~root->cfg_mask) || ((m | n | d) & ~mask) ||
        root->parents[(cfg >> 8) & 7] == NO_PARENT) {
        return false;
    }
    if (!mode) {
        /* Integer entries leave previous M/N/D shadow storage untouched. */
        return true;
    }
    denominator = ((~n) & mask) + m;
    return mode == 2 && root->mnd_width && m && m < denominator &&
           denominator <= mask && d == ((~denominator) & mask);
}

static uint32_t dispcc_rcg_stored(QcomSm8250DispccState *s, unsigned r,
                                unsigned field)
{
    int reg = dispcc_rcgs[r].reg[field];

    return reg < 0 ? 0 : s->regs[reg];
}

static uint64_t dispcc_parent_rate(QcomSm8250DispccState *s, int parent,
                                  const uint64_t *rates)
{
    return parent == NO_PARENT ? 0 : parent < 0 ?
           clock_get_hz(s->input[-parent - 1]) : rates[parent];
}

static bool dispcc_branch_requested(QcomSm8250DispccState *s, unsigned index)
{
    const DispccBranch *b = &dispcc_branches[index];

    return (s->regs[b->reg] & 1) ||
           (b->remote_vote >= 0 && s->remote_vote[b->remote_vote]);
}

static void dispcc_rates(QcomSm8250DispccState *s, uint64_t *rates)
{
    bool demand[DISPCC_CLOCKS] = { false };
    unsigned i;

    memset(rates, 0, sizeof(uint64_t) * DISPCC_CLOCKS);
    if (!s->enabled_reference) {
        return;
    }
    for (i = 0; i < DISPCC_PLLS; i++) {
        if (s->pll_locked[i] && dispcc_pll_running(s, i) &&
            (PREG(s, i, R_MODE) & PLL_OUTCTRL) &&
            PREG(s, i, R_USER) == 7) {
            rates[dispcc_plls[i].clock_id] =
                dispcc_pll_frequency(s, i, s->active_l[i], s->active_alpha[i]);
        }
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        demand[dispcc_rcgs[i].clock_id] = RREG(s, i, RC_CMD) & RCG_ROOT_EN;
    }
    for (i = 0; i < DISPCC_BRANCHES; i++) {
        if (dispcc_branch_requested(s, i) && dispcc_branches[i].parent >= 0) {
            demand[dispcc_branches[i].parent] = true;
        }
    }
    for (i = 0; i < DISPCC_DIVIDERS; i++) {
        const DispccDivider *d = &dispcc_dividers[i];

        if (demand[d->clock_id]) {
            demand[d->parent] = true;
        }
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        uint32_t cfg = s->active_cfg[i], hid = cfg & 0x1f;
        uint64_t parent, hz;

        if (!demand[dispcc_rcgs[i].clock_id] ||
            !dispcc_cfg_valid(i, cfg, s->active_m[i], s->active_n[i],
                             s->active_d[i])) {
            continue;
        }
        parent = dispcc_parent_rate(s, dispcc_rcgs[i].parents[(cfg >> 8) & 7],
                                    rates);
        hz = hid ? parent / (hid + 1) * 2 +
                   (parent % (hid + 1)) * 2 / (hid + 1) : parent;
        if (((cfg >> 12) & 3) == 2) {
            uint32_t mask = (1U << dispcc_rcgs[i].mnd_width) - 1;
            uint32_t denominator = ((~s->active_n[i]) & mask) + s->active_m[i];

            hz = hz * s->active_m[i] / denominator;
        }
        rates[dispcc_rcgs[i].clock_id] = hz;
    }
    for (i = 0; i < DISPCC_DIVIDERS; i++) {
        const DispccDivider *d = &dispcc_dividers[i];
        uint64_t parent = rates[d->parent];
        unsigned divisor = 1 + ((s->regs[d->reg] >> d->shift) &
                                ((1U << d->width) - 1));

        /* clk-regmap-divider uses generic divider_recalc_rate: round up. */
        rates[d->clock_id] = parent / divisor + !!(parent % divisor);
    }
    for (i = 0; i < DISPCC_BRANCHES; i++) {
        if (dispcc_branch_requested(s, i)) {
            rates[dispcc_branches[i].clock_id] =
                dispcc_parent_rate(s, dispcc_branches[i].parent, rates);
        }
    }
}

static void dispcc_reset_outputs(QcomSm8250DispccState *s)
{
    unsigned i;

    for (i = 0; i < DISPCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], s->enabled_reference &&
                     (s->regs[dispcc_resets[i].reg] & dispcc_resets[i].mask));
    }
}

static void dispcc_publish(QcomSm8250DispccState *s, bool propagate)
{
    uint64_t rates[DISPCC_CLOCKS];
    unsigned i;

    dispcc_rates(s, rates);
    for (i = 0; i < DISPCC_CLOCKS; i++) {
        bool changed = clock_set_hz(s->clocks[i], rates[i]);

        if (changed && propagate) {
            clock_propagate(s->clocks[i]);
        }
    }
    if (propagate) {
        dispcc_reset_outputs(s);
    }
}

static void dispcc_earlier(int64_t *next, int64_t value)
{
    if (value >= 0 && (*next < 0 || value < *next)) {
        *next = value;
    }
}

static void dispcc_schedule(QcomSm8250DispccState *s)
{
    int64_t next = -1;
    unsigned i;

    for (i = 0; i < DISPCC_PLLS; i++) {
        dispcc_earlier(&next, s->lock_deadline[i]);
        dispcc_earlier(&next, s->latch_deadline[i]);
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        dispcc_earlier(&next, s->rcg_deadline[i]);
    }
    if (next < 0) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static bool dispcc_latch_valid(QcomSm8250DispccState *s, unsigned p)
{
    uint32_t mode = PREG(s, p, R_MODE);

    return s->enabled_reference && (mode & PLL_RESET_N) &&
           (mode & PLL_UPDATE) && (mode & PLL_UPDATE_BYPASS) &&
           dispcc_recipe(s, p) &&
           dispcc_pll_frequency(s, p, s->pending_l[p], s->pending_alpha[p]);
}

static void dispcc_update(QcomSm8250DispccState *s)
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
    for (i = 0; i < DISPCC_PLLS; i++) {
        if (xo_changed) {
            s->pll_locked[i] = false;
            s->lock_deadline[i] = -1;
        }
        if (s->latch_deadline[i] >= 0) {
            if (!dispcc_latch_valid(s, i)) {
                s->latch_deadline[i] = -1;
            } else if (now >= s->latch_deadline[i]) {
                int64_t deadline = s->latch_deadline[i];

                s->active_l[i] = s->pending_l[i];
                s->active_alpha[i] = s->pending_alpha[i];
                s->pll_ack[i] = true;
                s->pll_locked[i] = false;
                s->latch_deadline[i] = -1;
                s->lock_deadline[i] = deadline + PLL_LOCK_NS;
                if (xo_changed) {
                    s->lock_deadline[i] = MAX(s->lock_deadline[i],
                                               now + PLL_LOCK_NS);
                }
            }
        }
        if (!dispcc_pll_running(s, i)) {
            s->pll_locked[i] = false;
            s->lock_deadline[i] = -1;
        } else if (!s->pll_locked[i]) {
            if (s->lock_deadline[i] < 0) {
                s->lock_deadline[i] = now + PLL_LOCK_NS;
            } else if (now >= s->lock_deadline[i]) {
                s->pll_locked[i] = true;
                s->pll_calibrated[i] = true;
                s->lock_deadline[i] = -1;
            }
        }
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        if (s->rcg_deadline[i] >= 0 && now >= s->rcg_deadline[i]) {
            s->active_cfg[i] = s->pending_cfg[i];
            s->active_m[i] = s->pending_m[i];
            s->active_n[i] = s->pending_n[i];
            s->active_d[i] = s->pending_d[i];
            RREG(s, i, RC_CMD) &= ~RCG_UPDATE;
            s->rcg_deadline[i] = -1;
        }
    }
    dispcc_publish(s, true);
    dispcc_schedule(s);
    s->updating = false;
}

static void dispcc_timer(void *opaque)
{
    dispcc_update(opaque);
}

static void dispcc_clock_changed(void *opaque, ClockEvent event)
{
    dispcc_update(opaque);
}

static MemTxResult dispcc_read(void *opaque, hwaddr address, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomDispccWindow *w = opaque;
    QcomSm8250DispccState *s = w->state;
    int id = dispcc_register(w->offset + address);
    const DispccRegister *reg;
    uint64_t rates[DISPCC_CLOCKS];

    *value = 0;
    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DISPCC
                      ": unmodeled read +0x%" HWADDR_PRIx " size %u\n",
                      w->offset + address, size);
        return MEMTX_ERROR;
    }
    dispcc_update(s);
    reg = &dispcc_registers[id];
    *value = s->regs[id];
    if (reg->kind == REG_PLL && reg->field == R_MODE) {
        unsigned p = reg->index;

        *value |= s->pll_locked[p] ? PLL_LOCK : 0;
        *value |= s->pll_ack[p] ? PLL_ACK : 0;
        *value |= s->pll_calibrated[p] ? PLL_PCAL_DONE : 0;
    } else if (reg->kind == REG_RCG && reg->field == RC_CMD) {
        dispcc_rates(s, rates);
        if (!rates[dispcc_rcgs[reg->index].clock_id]) {
            *value |= CLOCK_OFF;
        }
    } else if (reg->kind == REG_BRANCH) {
        dispcc_rates(s, rates);
        if (!rates[dispcc_branches[reg->index].clock_id]) {
            *value |= CLOCK_OFF;
        }
    }
    return MEMTX_OK;
}

static void dispcc_start_pll(QcomSm8250DispccState *s, unsigned p)
{
    bool valid = (PREG(s, p, R_MODE) & PLL_RESET_N) &&
                 PREG(s, p, R_OPMODE) == 1 && dispcc_recipe(s, p) &&
                 dispcc_pll_frequency(s, p, PREG(s, p, R_L),
                                      PREG(s, p, R_ALPHA));

    s->pll_locked[p] = false;
    s->lock_deadline[p] = -1;
    if (valid) {
        s->active_l[p] = PREG(s, p, R_L);
        s->active_alpha[p] = PREG(s, p, R_ALPHA);
    } else {
        s->active_l[p] = s->active_alpha[p] = 0;
    }
}

static void dispcc_pll_write(QcomSm8250DispccState *s, unsigned p,
                             unsigned field, uint32_t old, uint32_t val)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (field == R_MODE) {
        if (!(val & PLL_RESET_N)) {
            s->pll_locked[p] = s->pll_ack[p] = s->pll_calibrated[p] = false;
            s->active_l[p] = s->active_alpha[p] = 0;
            s->pending_l[p] = s->pending_alpha[p] = 0;
            s->lock_deadline[p] = s->latch_deadline[p] = -1;
        } else if (!(old & PLL_RESET_N)) {
            dispcc_start_pll(s, p);
        }
        if (!(val & PLL_UPDATE) || !(val & PLL_UPDATE_BYPASS)) {
            s->pll_ack[p] = false;
            s->latch_deadline[p] = -1;
        } else if (!(old & PLL_UPDATE)) {
            s->pll_ack[p] = false;
            s->latch_deadline[p] = -1;
            if ((val & PLL_RESET_N) && dispcc_recipe(s, p) &&
                dispcc_pll_frequency(s, p, PREG(s, p, R_L),
                                     PREG(s, p, R_ALPHA))) {
                s->pending_l[p] = PREG(s, p, R_L);
                s->pending_alpha[p] = PREG(s, p, R_ALPHA);
                if (dispcc_latch_valid(s, p)) {
                    s->latch_deadline[p] = now + PLL_LATCH_NS;
                }
            }
        }
    } else if (field == R_OPMODE && val != old) {
        dispcc_start_pll(s, p);
    }
}

static MemTxResult dispcc_write(void *opaque, hwaddr address, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomDispccWindow *w = opaque;
    QcomSm8250DispccState *s = w->state;
    int id = dispcc_register(w->offset + address);
    const DispccRegister *reg;
    uint32_t old, val;

    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DISPCC
                      ": unmodeled write +0x%" HWADDR_PRIx " size %u\n",
                      w->offset + address, size);
        return MEMTX_ERROR;
    }
    reg = &dispcc_registers[id];
    if (reg->kind == REG_DIVIDER && dispcc_dividers[reg->index].read_only) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DISPCC
                      ": read-only link divider +0x%" HWADDR_PRIx "\n",
                      w->offset + address);
        return MEMTX_ERROR;
    }
    if (value & ~(uint64_t)(reg->write_mask | reg->status_mask)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DISPCC
                      ": unsupported bits +0x%" HWADDR_PRIx "\n",
                      w->offset + address);
        return MEMTX_ERROR;
    }
    val = value & reg->write_mask;
    if (reg->kind == REG_PLL && reg->field == R_USER &&
        !dispcc_user_supported(val)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_DISPCC
                      ": unsupported PLL output selection\n");
        return MEMTX_ERROR;
    }
    dispcc_update(s);
    old = s->regs[id];
    s->regs[id] = val;
    if (reg->kind == REG_PLL) {
        dispcc_pll_write(s, reg->index, reg->field, old, val);
    } else if (reg->kind == REG_RCG && reg->field == RC_CMD) {
        unsigned r = reg->index;
        uint32_t cfg = RREG(s, r, RC_CFG);
        uint32_t m = dispcc_rcg_stored(s, r, RC_M);
        uint32_t n = dispcc_rcg_stored(s, r, RC_N);
        uint32_t d = dispcc_rcg_stored(s, r, RC_D);

        if (!(val & RCG_UPDATE)) {
            s->rcg_deadline[r] = -1;
        } else if (!(old & RCG_UPDATE) && dispcc_cfg_valid(r, cfg, m, n, d)) {
            s->pending_cfg[r] = cfg;
            s->pending_m[r] = m;
            s->pending_n[r] = n;
            s->pending_d[r] = d;
            s->rcg_deadline[r] =
                qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + RCG_UPDATE_NS;
        }
    }
    dispcc_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps dispcc_ops = {
    .read_with_attrs = dispcc_read,
    .write_with_attrs = dispcc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void dispcc_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
    unsigned i;

    s->ready = false;
    s->updating = false;
    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    for (i = 0; i < DISPCC_PLLS; i++) {
        s->active_l[i] = s->active_alpha[i] = 0;
        s->pending_l[i] = s->pending_alpha[i] = 0;
        s->pll_locked[i] = s->pll_ack[i] = s->pll_calibrated[i] = false;
        s->lock_deadline[i] = s->latch_deadline[i] = -1;
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        s->active_cfg[i] = s->active_m[i] = s->active_n[i] = s->active_d[i] = 0;
        s->pending_cfg[i] = s->pending_m[i] = 0;
        s->pending_n[i] = s->pending_d[i] = 0;
        s->rcg_deadline[i] = -1;
    }
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
    /* Input carriers and remote votes are provider-owned and survive reset. */
}

static void dispcc_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);

    s->ready = true;
    dispcc_publish(s, true);
}

static int dispcc_pre_load(void *opaque)
{
    QcomSm8250DispccState *s = opaque;
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    for (i = 0; i < DISPCC_CLOCKS; i++) {
        clock_set_hz(s->clocks[i], 0);
    }
    for (i = 0; i < DISPCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], 0);
    }
    return 0;
}

static int dispcc_bad_state(const char *what, unsigned index)
{
    error_report(TYPE_QCOM_SM8250_DISPCC ": invalid migrated %s %u",
                 what, index);
    return -EINVAL;
}

static bool dispcc_deadline_valid(int64_t deadline, int64_t now,
                                 uint64_t maximum_remaining)
{
    return deadline >= -1 &&
           (deadline <= now || (uint64_t)(deadline - now) <= maximum_remaining);
}

static int dispcc_post_load(void *opaque, int version_id)
{
    QcomSm8250DispccState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (now < 0) {
        return dispcc_bad_state("virtual clock", 0);
    }
    for (i = 0; i < DISPCC_INPUTS; i++) {
        /* Read the incoming Clock ratio; never alter a provider's state. */
        if (s->input[i]->multiplier != 1 || s->input[i]->divider != 1 ||
            CLOCK_PERIOD_TO_HZ(clock_get(s->input[i])) > UINT32_MAX) {
            return dispcc_bad_state("input clock", i);
        }
    }
    if (s->last_xo_hz != clock_get_hz(s->input[I_XO])) {
        return dispcc_bad_state("XO observation", 0);
    }
    for (i = 0; i < DISPCC_REGISTERS; i++) {
        if ((s->regs[i] & ~dispcc_registers[i].stored_mask) ||
            (!s->enabled_reference && s->regs[i])) {
            return dispcc_bad_state("register", i);
        }
    }
    for (i = 0; i < DISPCC_PLLS; i++) {
        uint32_t mode = PREG(s, i, R_MODE);
        bool running = dispcc_pll_running(s, i);

        if (!dispcc_user_supported(PREG(s, i, R_USER)) ||
            s->active_l[i] > 0xffff || s->pending_l[i] > 0xffff ||
            s->active_alpha[i] > 0xffff || s->pending_alpha[i] > 0xffff ||
            (!s->active_l[i] && s->active_alpha[i]) ||
            (!s->pending_l[i] && s->pending_alpha[i]) ||
            !dispcc_deadline_valid(s->lock_deadline[i], now, PLL_LOCK_NS) ||
            !dispcc_deadline_valid(s->latch_deadline[i], now, PLL_LATCH_NS) ||
            (s->pll_locked[i] && (!running || s->lock_deadline[i] >= 0 ||
                                 !s->pll_calibrated[i])) ||
            (s->lock_deadline[i] >= 0 && !running) ||
            (s->pll_calibrated[i] && !(mode & PLL_RESET_N)) ||
            (s->pll_ack[i] && (!(mode & PLL_UPDATE) ||
                              !(mode & PLL_UPDATE_BYPASS) ||
                              !(mode & PLL_RESET_N))) ||
            (s->latch_deadline[i] >= 0 &&
             (!dispcc_latch_valid(s, i) || s->pll_ack[i])) ||
            (!(mode & PLL_RESET_N) &&
             (s->active_l[i] || s->active_alpha[i] || s->pending_l[i] ||
              s->pending_alpha[i] || s->pll_locked[i] || s->pll_ack[i] ||
              s->pll_calibrated[i] || s->lock_deadline[i] >= 0 ||
              s->latch_deadline[i] >= 0))) {
            return dispcc_bad_state("PLL", i);
        }
        if (!s->enabled_reference &&
            (s->active_l[i] || s->active_alpha[i] || s->pending_l[i] ||
             s->pending_alpha[i] || s->pll_locked[i] || s->pll_ack[i] ||
             s->pll_calibrated[i] || s->lock_deadline[i] >= 0 ||
             s->latch_deadline[i] >= 0)) {
            return dispcc_bad_state("disabled PLL", i);
        }
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        if (!dispcc_cfg_valid(i, s->active_cfg[i], s->active_m[i],
                            s->active_n[i], s->active_d[i]) ||
            !dispcc_cfg_valid(i, s->pending_cfg[i], s->pending_m[i],
                            s->pending_n[i], s->pending_d[i]) ||
            !dispcc_deadline_valid(s->rcg_deadline[i], now, RCG_UPDATE_NS) ||
            (s->rcg_deadline[i] >= 0 && !(RREG(s, i, RC_CMD) & RCG_UPDATE)) ||
            (!s->enabled_reference &&
             (s->active_cfg[i] || s->active_m[i] || s->active_n[i] ||
              s->active_d[i] || s->pending_cfg[i] || s->pending_m[i] ||
              s->pending_n[i] || s->pending_d[i] || s->rcg_deadline[i] >= 0))) {
            return dispcc_bad_state("RCG", i);
        }
    }
    s->updating = false;
    s->ready = true;
    /* Derive outputs before first target MMIO, without consuming deadlines. */
    dispcc_publish(s, false);
    dispcc_reset_outputs(s);
    dispcc_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_dispcc = {
    .name = TYPE_QCOM_SM8250_DISPCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = dispcc_pre_load,
    .post_load = dispcc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(register_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(clock_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(input_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(pll_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(rcg_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(branch_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(divider_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(reset_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(remote_vote_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(window_count, QcomSm8250DispccState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomSm8250DispccState),
        VMSTATE_UINT64_EQUAL(physical_base, QcomSm8250DispccState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250DispccState, DISPCC_REGISTERS),
        VMSTATE_UINT32_ARRAY(active_l, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_UINT32_ARRAY(active_alpha, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_UINT32_ARRAY(pending_l, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_UINT32_ARRAY(pending_alpha, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_UINT32_ARRAY(active_cfg, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(active_m, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(active_n, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(active_d, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_cfg, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_m, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_n, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_d, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_INT64_ARRAY(lock_deadline, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_INT64_ARRAY(latch_deadline, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_INT64_ARRAY(rcg_deadline, QcomSm8250DispccState, DISPCC_RCGS),
        VMSTATE_UINT64(last_xo_hz, QcomSm8250DispccState),
        VMSTATE_BOOL_ARRAY(pll_locked, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_BOOL_ARRAY(pll_ack, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_BOOL_ARRAY(pll_calibrated, QcomSm8250DispccState, DISPCC_PLLS),
        VMSTATE_BOOL_ARRAY(remote_vote, QcomSm8250DispccState,
                           DISPCC_REMOTE_VOTES),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250DispccState, DISPCC_INPUTS),
        VMSTATE_END_OF_LIST()
    },
};

static void dispcc_fixture_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    uint32_t hz = clock_get_hz(opaque);

    visit_type_uint32(v, name, &hz, errp);
}

static void dispcc_fixture_set(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
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
        error_setg(errp, "%s exceeds the representable input clock range",
                   name);
        return;
    }
    clock_set_hz(input, hz);
    dispcc_update(s);
}

static void dispcc_divider_get(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
    uint32_t value = s->regs[dispcc_dividers[GPOINTER_TO_UINT(opaque)].reg];

    visit_type_uint32(v, name, &value, errp);
}

static void dispcc_divider_set(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
    const DispccDivider *divider = &dispcc_dividers[GPOINTER_TO_UINT(opaque)];
    uint32_t value;

    if (!visit_type_uint32(v, name, &value, errp)) {
        return;
    }
    if (!s->enabled_reference || !divider->read_only || value > 3) {
        error_setg(errp, "%s requires an enabled reference and a 2-bit value",
                   name);
        return;
    }
    s->regs[divider->reg] = value;
    dispcc_update(s);
}

static void dispcc_rate_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
    uint64_t rates[DISPCC_CLOCKS];

    dispcc_rates(s, rates);
    visit_type_uint64(v, name, &rates[GPOINTER_TO_UINT(opaque)], errp);
}

static void dispcc_signed_get(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    int64_t value = *(int64_t *)opaque;

    visit_type_int64(v, name, &value, errp);
}

static void dispcc_bool_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    bool value = *(bool *)opaque;

    visit_type_bool(v, name, &value, errp);
}

static void dispcc_status_get(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
    unsigned id = GPOINTER_TO_UINT(opaque);
    bool value;

    if (id < 5 * DISPCC_PLLS) {
        unsigned p = id / 5;
        bool flags[] = {
            s->pll_locked[p], s->pll_ack[p], s->pll_calibrated[p],
            s->lock_deadline[p] >= 0, s->latch_deadline[p] >= 0,
        };

        value = flags[id % 5];
    } else {
        value = s->rcg_deadline[id - 5 * DISPCC_PLLS] >= 0;
    }
    visit_type_bool(v, name, &value, errp);
}

static void dispcc_remote_vote(void *opaque, int index, int level)
{
    QcomSm8250DispccState *s = opaque;

    s->remote_vote[index] = !!level;
    dispcc_update(s);
}

static void dispcc_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(dev);

    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->ready = true;
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
}

static void dispcc_init(Object *obj)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const statuses[] = {
        "locked", "ack", "calibrated",
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
        "lock-deadline", "latch-deadline",
    };
    uint32_t *pll_arrays[] = {
        s->active_l, s->active_alpha, s->pending_l, s->pending_alpha,
    };
    uint32_t *rcg_arrays[] = {
        s->active_cfg, s->active_m, s->active_n, s->active_d,
        s->pending_cfg, s->pending_m, s->pending_n, s->pending_d,
    };
    int64_t *time_arrays[] = {
        s->lock_deadline, s->latch_deadline,
    };
    unsigned i, j;

    s->reference_identity = DISPCC_REFERENCE_IDENTITY;
    s->register_count = DISPCC_REGISTERS;
    s->clock_count = DISPCC_CLOCKS;
    s->input_count = DISPCC_INPUTS;
    s->pll_count = DISPCC_PLLS;
    s->rcg_count = DISPCC_RCGS;
    s->branch_count = DISPCC_BRANCHES;
    s->divider_count = DISPCC_DIVIDERS;
    s->reset_count = DISPCC_RESETS;
    s->remote_vote_count = DISPCC_REMOTE_VOTES;
    s->window_count = DISPCC_WINDOWS;
    s->mapped_bytes = DISPCC_MAPPED_BYTES;
    s->physical_base = DISPCC_PHYSICAL_BASE;
#define DISPCC_U32_PROPERTY(name, field) \
    object_property_add_uint32_ptr(obj, name, &s->field, OBJ_PROP_FLAG_READ)
    DISPCC_U32_PROPERTY("reference-profile", reference_profile);
    DISPCC_U32_PROPERTY("reference-identity", reference_identity);
    DISPCC_U32_PROPERTY("register-count", register_count);
    DISPCC_U32_PROPERTY("clock-count", clock_count);
    DISPCC_U32_PROPERTY("input-count", input_count);
    DISPCC_U32_PROPERTY("pll-count", pll_count);
    DISPCC_U32_PROPERTY("rcg-count", rcg_count);
    DISPCC_U32_PROPERTY("branch-count", branch_count);
    DISPCC_U32_PROPERTY("divider-count", divider_count);
    DISPCC_U32_PROPERTY("reset-count", reset_count);
    DISPCC_U32_PROPERTY("remote-vote-count", remote_vote_count);
    DISPCC_U32_PROPERTY("window-count", window_count);
    DISPCC_U32_PROPERTY("mapped-bytes", mapped_bytes);
#undef DISPCC_U32_PROPERTY
    object_property_add_uint64_ptr(obj, "physical-base", &s->physical_base,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "last-xo-hz", &s->last_xo_hz,
                                   OBJ_PROP_FLAG_READ);
    /*
     * SysBus has 32 export slots. A transparent container exports all 39
     * sparse leaves without handlers or storage in any intervening hole.
     */
    memory_region_init(&s->container, obj, "dispcc-sparse", 0x20000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container);
    for (i = 0; i < DISPCC_WINDOWS; i++) {
        const QcomDispccWindowDef *d = &qcom_sm8250_dispcc_windows[i];
        g_autofree char *name =
            g_strdup_printf(TYPE_QCOM_SM8250_DISPCC ".%04x", d->offset);

        s->windows[i].state = s;
        s->windows[i].offset = d->offset;
        memory_region_init_io(&s->iomem[i], obj, &dispcc_ops, &s->windows[i],
                              name, d->size);
        memory_region_add_subregion(&s->container, d->offset, &s->iomem[i]);
    }
    for (i = 0; i < DISPCC_REGISTERS; i++) {
        g_autofree char *name =
            g_strdup_printf("reg.%04x", dispcc_registers[i].offset);

        object_property_add_uint32_ptr(obj, name, &s->regs[i],
                                       OBJ_PROP_FLAG_READ);
    }
    for (i = 0; i < DISPCC_INPUTS; i++) {
        s->input[i] = qdev_init_clock_in(dev, dispcc_input_names[i],
                                        dispcc_clock_changed, s, ClockUpdate);
        if (qtest_enabled()) {
            g_autofree char *name =
                g_strdup_printf("qtest-%s-hz", dispcc_input_names[i]);

            object_property_add(obj, name, "uint32", dispcc_fixture_get,
                                dispcc_fixture_set, NULL, s->input[i]);
        }
    }
    for (i = 0; i < DISPCC_CLOCKS; i++) {
        g_autofree char *name =
            g_strdup_printf("clk.%s.hz", dispcc_clock_names[i]);

        s->clocks[i] = qdev_init_clock_out(dev, dispcc_clock_names[i]);
        object_property_add(obj, name, "uint64", dispcc_rate_get, NULL,
                            NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < DISPCC_PLLS; i++) {
        for (j = 0; j < ARRAY_SIZE(statuses); j++) {
            g_autofree char *name = g_strdup_printf("pll%u-%s", i, statuses[j]);

            object_property_add(obj, name, "bool", dispcc_status_get,
                                NULL, NULL, GUINT_TO_POINTER(i * 5 + j));
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
            object_property_add(obj, name, "int64", dispcc_signed_get,
                                NULL, NULL, &time_arrays[j][i]);
        }
    }
    for (i = 0; i < DISPCC_RCGS; i++) {
        g_autofree char *pending = g_strdup_printf("rcg%u-pending", i);
        g_autofree char *deadline = g_strdup_printf("rcg%u-deadline", i);

        s->rcg_deadline[i] = -1;
        object_property_add(obj, pending, "bool", dispcc_status_get, NULL, NULL,
                            GUINT_TO_POINTER(5 * DISPCC_PLLS + i));
        object_property_add(obj, deadline, "int64", dispcc_signed_get,
                            NULL, NULL, &s->rcg_deadline[i]);
        for (j = 0; j < ARRAY_SIZE(rcg_values); j++) {
            g_autofree char *name =
                g_strdup_printf("rcg%u-%s", i, rcg_values[j]);

            object_property_add_uint32_ptr(obj, name, &rcg_arrays[j][i],
                                           OBJ_PROP_FLAG_READ);
        }
    }
    for (i = 0; i < DISPCC_DIVIDERS; i++) {
        g_autofree char *name = g_strdup_printf("divider%u-value", i);

        object_property_add(obj, name, "uint32", dispcc_divider_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
        if (qtest_enabled() && dispcc_dividers[i].read_only) {
            g_autofree char *fixture =
                g_strdup_printf("qtest-divider%u-value", i);

            object_property_add(obj, fixture, "uint32", dispcc_divider_get,
                                dispcc_divider_set, NULL, GUINT_TO_POINTER(i));
        }
    }
    for (i = 0; i < DISPCC_REMOTE_VOTES; i++) {
        g_autofree char *name = g_strdup_printf("remote-vote%u", i);

        object_property_add(obj, name, "bool", dispcc_bool_get,
                            NULL, NULL, &s->remote_vote[i]);
    }
    qdev_init_gpio_in_named(dev, dispcc_remote_vote, "remote-vote",
                           DISPCC_REMOTE_VOTES);
    qdev_init_gpio_out_named(dev, s->reset_out, "reset", DISPCC_RESETS);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dispcc_timer, s);
}

static void dispcc_finalize(Object *obj)
{
    QcomSm8250DispccState *s = QCOM_SM8250_DISPCC(obj);

    timer_free(s->timer);
}

static const Property dispcc_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250DispccState,
                     enabled_reference, false),
};

static void dispcc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm SM8250/Kona DISPCC digital reference protocol";
    dc->realize = dispcc_realize;
    dc->vmsd = &vmstate_dispcc;
    device_class_set_props(dc, dispcc_properties);
    rc->phases.enter = dispcc_reset_enter;
    rc->phases.hold = dispcc_reset_hold;
}

static const TypeInfo dispcc_info = {
    .name = TYPE_QCOM_SM8250_DISPCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250DispccState),
    .instance_init = dispcc_init,
    .instance_finalize = dispcc_finalize,
    .class_init = dispcc_class_init,
};

static void dispcc_register_types(void)
{
    type_register_static(&dispcc_info);
}
type_init(dispcc_register_types)
