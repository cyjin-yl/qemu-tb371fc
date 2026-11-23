/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * SM8250 global clock controller: software-visible register state and gates.
 * Register evidence: Linux v6.12.114 drivers/clk/qcom/gcc-sm8250.c,
 * clk-alpha-pll.c, clk-rcg2.c, clk-branch.c, gdsc.c, reset.c; the pinned
 * official Kona source supplies SYS_NOC/CPUSS CBCR and parent topology.
 * See tb371fc/docs/gcc-sm8250.md for evidence, assumptions and limitations.
 * This implements neither analog PLLs nor undocumented Qualcomm firmware.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-gcc.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define PLL_OUTCTRL       BIT(0)
#define PLL_RESET_N       BIT(2)
#define PLL_OFFLINE_REQ   BIT(7)
#define PLL_FSM_EN        BIT(20)
#define PLL_FSM_RESET     BIT(21)
#define PLL_UPDATE        BIT(22)
#define PLL_OFFLINE_ACK   BIT(28)
#define PLL_ACK           BIT(29)
#define PLL_ACTIVE        BIT(30)
#define PLL_LOCK          BIT(31)
#define RCG_UPDATE        BIT(0)
#define RCG_ROOT_EN       BIT(1)
#define RCG_ROOT_OFF      BIT(31)
#define CBCR_OFF          BIT(31)
#define GDSC_COLLAPSE     BIT(0)
#define GDSC_HW_CONTROL   BIT(1)
#define GDSC_ON           BIT(31)

/* Ordering delays, in virtual ns; not measured silicon timing. */
#define PLL_LOCK_NS       5000
#define PLL_LATCH_NS      1000
#define RCG_UPDATE_NS     1000
#define GDSC_TRANSITION_NS 3000

typedef enum GccClockKind {
    GCC_NONE, GCC_PLL, GCC_POSTDIV, GCC_RCG, GCC_DIV, GCC_BRANCH,
} GccClockKind;

typedef struct GccClockDesc {
    const char *name;
    GccClockKind kind;
    uint32_t reg, enable_reg, enable_mask;
    int16_t parent;
    uint8_t mnd_width, hid_width;
    uint32_t hwcg_reg, hwcg_mask;
    int16_t parents[8];
    int16_t external;
} GccClockDesc;

typedef struct GccResetDesc {
    uint32_t reg, mask;
} GccResetDesc;

typedef struct GccPowerDesc {
    uint32_t reg;
} GccPowerDesc;

typedef struct GccRegisterDesc {
    uint32_t reg, write_mask, status_mask;
} GccRegisterDesc;

#include "qcom-sm8250-gcc-data.inc"

#define REG(s, off) ((s)->regs[(off) / 4])

static const GccRegisterDesc *gcc_find_register(hwaddr address)
{
    size_t lo = 0, hi = ARRAY_SIZE(gcc_registers);

    while (lo < hi) {
        size_t mid = lo + (hi - lo) / 2;
        if (gcc_registers[mid].reg < address) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo < ARRAY_SIZE(gcc_registers) &&
           gcc_registers[lo].reg == address ? &gcc_registers[lo] : NULL;
}

static bool gcc_branch_requested(QcomSm8250GccState *s, unsigned id)
{
    const GccClockDesc *d = &gcc_clocks[id];
    bool requested = (REG(s, d->enable_reg) & d->enable_mask) ||
                     s->remote_vote[id];

    /* HWCG is an explicit consumer request, not an always-running clock. */
    if (d->hwcg_mask && (REG(s, d->hwcg_reg) & d->hwcg_mask)) {
        requested &= s->hw_request[id];
    }
    return requested;
}

static void gcc_compute_demand(QcomSm8250GccState *s, bool *demand)
{
    unsigned i, pass;
    bool changed;

    memset(demand, 0, sizeof(bool) * GCC_CLOCKS);
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        if (d->kind == GCC_BRANCH) {
            demand[i] = gcc_branch_requested(s, i);
        } else if (d->kind == GCC_RCG) {
            demand[i] = REG(s, d->reg) & RCG_ROOT_EN;
        }
    }
    /* Descriptors form a DAG; bounded iteration also tolerates table order. */
    for (pass = 0; pass < GCC_CLOCKS; pass++) {
        changed = false;
        for (i = 0; i < GCC_CLOCKS; i++) {
            const GccClockDesc *d = &gcc_clocks[i];
            if (demand[i] && d->kind != GCC_RCG && d->parent >= 0 &&
                !demand[d->parent]) {
                demand[d->parent] = true;
                changed = true;
            }
        }
        if (!changed) {
            break;
        }
    }
}

static bool gcc_rcg_valid(const GccClockDesc *d, uint32_t cfg,
                          uint32_t m, uint32_t n, uint32_t den)
{
    unsigned mode = (cfg >> 12) & 3;
    uint32_t mask = (1U << d->mnd_width) - 1;
    uint32_t numerator = m & mask;
    uint32_t denominator = ((~n) & mask) + numerator;

    if (d->parents[(cfg >> 8) & 7] == -127 || (mode && mode != 2)) {
        return false;
    }
    if (!mode) {
        return true;
    }
    /* M/N dual-edge programming follows clk_rcg2_configure(). */
    return d->mnd_width && numerator && denominator &&
           numerator <= denominator && denominator <= mask &&
           ((~den) & mask) >= numerator &&
           ((~den) & mask) <= 2 * (denominator - numerator);
}

static uint64_t gcc_rate(QcomSm8250GccState *s, int id,
                         const bool *demand, unsigned depth)
{
    const GccClockDesc *d;
    uint32_t cfg, div, mode, mask, n;
    uint64_t parent;

    if (id < 0) {
        switch (id) {
        case -1: return clock_get_hz(s->xo);
        case -2: return clock_get_hz(s->sleep);
        case -3: return clock_get_hz(s->audio);
        default: return 0;
        }
    }
    if (id >= GCC_CLOCKS || depth > GCC_CLOCKS) {
        return 0;
    }
    d = &gcc_clocks[id];
    switch (d->kind) {
    case GCC_PLL:
        if (!s->pll_locked[id]) {
            return 0;
        }
        if ((REG(s, d->reg + 0xc) & 7) != 7 ||
            (!(REG(s, d->reg) & PLL_FSM_EN) &&
             !(REG(s, d->reg) & PLL_OUTCTRL))) {
            return 0;
        }
        return clock_get_hz(s->xo) *
               (((uint64_t)s->active_m[id] << 16) + s->active_n[id]) /
               65536;
    case GCC_POSTDIV:
        div = (REG(s, d->reg) >> d->mnd_width) &
              ((1U << d->hid_width) - 1);
        /* Only field=1 / divide2 is documented for GPLL0_OUT_EVEN. */
        return div == 1 ? gcc_rate(s, d->parent, demand, depth + 1) / 2 : 0;
    case GCC_DIV:
        div = ((REG(s, d->reg) >> d->mnd_width) &
               ((1U << d->hid_width) - 1)) + 1;
        return gcc_rate(s, d->parent, demand, depth + 1) / div;
    case GCC_RCG:
        if (!demand[id]) {
            return 0;
        }
        cfg = s->active_cfg[id];
        if (!gcc_rcg_valid(d, cfg, s->active_m[id], s->active_n[id],
                           s->active_d[id])) {
            return 0;
        }
        parent = gcc_rate(s, d->parents[(cfg >> 8) & 7], demand, depth + 1);
        div = cfg & ((1U << d->hid_width) - 1);
        if (div) {
            parent = parent * 2 / (div + 1);
        }
        mode = (cfg >> 12) & 3;
        if (mode) {
            mask = (1U << d->mnd_width) - 1;
            n = ((~s->active_n[id]) & mask) + (s->active_m[id] & mask);
            parent = parent * (s->active_m[id] & mask) / n;
        }
        return parent;
    case GCC_BRANCH:
        if (!demand[id]) {
            return 0;
        }
        if (d->external >= 0) {
            return clock_get_hz(s->external[d->external]);
        }
        return gcc_rate(s, d->parent, demand, depth + 1);
    default:
        return 0;
    }
}

static bool gcc_pll_wanted(QcomSm8250GccState *s, unsigned id)
{
    const GccClockDesc *d = &gcc_clocks[id];
    uint32_t mode = REG(s, d->reg);

    if (mode & (PLL_OFFLINE_REQ | PLL_FSM_RESET)) {
        return false;
    }
    if (mode & PLL_FSM_EN) {
        return (REG(s, d->enable_reg) & d->enable_mask) || s->remote_vote[id];
    }
    return (mode & PLL_RESET_N) && REG(s, d->reg + 0x38) == 1;
}

static bool gcc_pll_valid(QcomSm8250GccState *s, const GccClockDesc *d)
{
    uint64_t hz = clock_get_hz(s->xo) *
                  (((uint64_t)REG(s, d->reg + 4) << 16) +
                   REG(s, d->reg + 0x40)) / 65536;

    /* Kona's vendor lucid_vco table bounds, not an analog lock simulation. */
    return hz >= 249600000 && hz <= 2000000000;
}

static void gcc_update(QcomSm8250GccState *s)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    int64_t next = INT64_MAX;
    bool demand[GCC_CLOCKS];
    unsigned i;

    if (!s->ready || s->updating) {
        return;
    }
    s->updating = true;
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        int64_t *deadline = &s->clock_deadline[i];
        if (d->kind == GCC_PLL) {
            bool wanted = gcc_pll_wanted(s, i) && clock_get_hz(s->xo);
            bool latch = REG(s, d->reg) & PLL_UPDATE;
            int64_t *latch_deadline = &s->pll_latch_deadline[i];

            /* Lucid permits programming/latching while the output is off. */
            if (!latch || !gcc_pll_valid(s, d)) {
                s->pll_ack[i] = false;
                *latch_deadline = -1;
            } else if (!s->pll_ack[i]) {
                if (*latch_deadline < 0) {
                    *latch_deadline = now + PLL_LATCH_NS;
                } else if (now >= *latch_deadline) {
                    s->active_m[i] = REG(s, d->reg + 4);
                    s->active_n[i] = REG(s, d->reg + 0x40);
                    s->pll_ack[i] = true;
                    *latch_deadline = -1;
                }
            }
            if (*latch_deadline >= 0) {
                next = MIN(next, *latch_deadline);
            }
            if (!wanted) {
                s->pll_locked[i] = false;
                *deadline = -1;
            } else if (!s->pll_locked[i]) {
                if (!gcc_pll_valid(s, d)) {
                    *deadline = -1;
                } else if (*deadline < 0) {
                    *deadline = now + PLL_LOCK_NS;
                } else if (now >= *deadline) {
                    s->active_m[i] = REG(s, d->reg + 4);
                    s->active_n[i] = REG(s, d->reg + 0x40);
                    s->pll_locked[i] = true;
                    *deadline = -1;
                }
            } else {
                *deadline = -1;
            }
        } else if (d->kind == GCC_RCG) {
            uint32_t r = d->reg;
            if (!(REG(s, r) & RCG_UPDATE)) {
                *deadline = -1;
            } else if (clock_get_hz(s->xo) &&
                       gcc_rcg_valid(d, REG(s, r + 4), REG(s, r + 8),
                                     REG(s, r + 12), REG(s, r + 16))) {
                if (*deadline < 0) {
                    *deadline = now + RCG_UPDATE_NS;
                } else if (now >= *deadline) {
                    s->active_cfg[i] = REG(s, r + 4);
                    if (d->mnd_width) {
                        s->active_m[i] = REG(s, r + 8);
                        s->active_n[i] = REG(s, r + 12);
                        s->active_d[i] = REG(s, r + 16);
                    }
                    REG(s, r) &= ~RCG_UPDATE;
                    *deadline = -1;
                }
            } else {
                *deadline = -1;
            }
        }
        if (*deadline >= 0) {
            next = MIN(next, *deadline);
        }
    }
    for (i = 0; i < GCC_POWER_DOMAINS; i++) {
        uint32_t value = REG(s, gcc_power[i].reg);
        bool wanted = !(value & GDSC_COLLAPSE);
        int64_t *deadline = &s->power_deadline[i];
        if (wanted == s->power_on[i] || !clock_get_hz(s->xo) ||
            (value & GDSC_HW_CONTROL)) {
            *deadline = -1;
        } else if (*deadline < 0) {
            *deadline = now + GDSC_TRANSITION_NS;
        } else if (now >= *deadline) {
            s->power_on[i] = wanted;
            *deadline = -1;
        }
        if (*deadline >= 0) {
            next = MIN(next, *deadline);
        }
        qemu_set_irq(s->power_out[i], s->power_on[i]);
    }
    for (i = 0; i < GCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], !!(REG(s, gcc_resets[i].reg) &
                                      gcc_resets[i].mask));
    }
    gcc_compute_demand(s, demand);
    for (i = 0; i < GCC_CLOCKS; i++) {
        if (s->clocks[i]) {
            clock_update(s->clocks[i],
                         CLOCK_PERIOD_FROM_HZ(gcc_rate(s, i, demand, 0)));
        }
    }
    if (next < INT64_MAX) {
        timer_mod_ns(s->timer, next);
    } else {
        timer_del(s->timer);
    }
    s->updating = false;
}

static MemTxResult gcc_read(void *opaque, hwaddr address, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomSm8250GccState *s = opaque;
    const GccRegisterDesc *reg = gcc_find_register(address);
    bool demand[GCC_CLOCKS];
    unsigned i;
    uint32_t result;

    if (!reg) {
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_GCC
                      ": unsupported read at 0x%" HWADDR_PRIx "\n", address);
        *value = 0;
        return MEMTX_ERROR;
    }
    gcc_update(s);
    result = REG(s, address);
    gcc_compute_demand(s, demand);
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        if (d->kind == GCC_PLL) {
            if (address == d->reg) {
                if (s->pll_locked[i]) {
                    result |= PLL_LOCK;
                    if (REG(s, d->reg) & PLL_FSM_EN) {
                        result |= PLL_ACTIVE | PLL_OUTCTRL;
                    }
                }
                if (s->pll_ack[i]) {
                    result |= PLL_ACK;
                }
                if (result & PLL_OFFLINE_REQ) {
                    result |= PLL_OFFLINE_ACK;
                }
            } else if (address == d->reg + 0x30 && s->pll_locked[i]) {
                result |= BIT(27);
            } else if (address == d->reg + 0x38 &&
                       s->pll_locked[i] && (REG(s, d->reg) & PLL_FSM_EN)) {
                result = 1;
            }
        } else if (d->kind == GCC_RCG && address == d->reg) {
            if (!gcc_rate(s, i, demand, 0)) {
                result |= RCG_ROOT_OFF;
            }
            if (s->active_cfg[i] != REG(s, d->reg + 4)) {
                result |= BIT(4);
            }
            if (d->mnd_width) {
                if (s->active_n[i] != REG(s, d->reg + 12)) {
                    result |= BIT(5);
                }
                if (s->active_m[i] != REG(s, d->reg + 8)) {
                    result |= BIT(6);
                }
                if (s->active_d[i] != REG(s, d->reg + 16)) {
                    result |= BIT(7);
                }
            }
        } else if (d->kind == GCC_BRANCH && address == d->reg) {
            if (!gcc_rate(s, i, demand, 0)) {
                result |= CBCR_OFF;
            }
        }
    }
    for (i = 0; i < GCC_POWER_DOMAINS; i++) {
        if (address == gcc_power[i].reg && s->power_on[i]) {
            result |= GDSC_ON;
        }
    }
    *value = result;
    return MEMTX_OK;
}

static MemTxResult gcc_write(void *opaque, hwaddr address, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomSm8250GccState *s = opaque;
    const GccRegisterDesc *reg = gcc_find_register(address);
    uint32_t bits = value;
    unsigned i;

    if (!reg || (bits & ~(reg->write_mask | reg->status_mask))) {
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_GCC
                      ": unsupported write 0x%08x at 0x%" HWADDR_PRIx "\n",
                      bits, address);
        return MEMTX_ERROR;
    }
    for (i = 0; i < GCC_POWER_DOMAINS; i++) {
        if (address == gcc_power[i].reg && (bits & GDSC_HW_CONTROL)) {
            qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_GCC
                          ": GDSC hardware power control is not implemented\n");
            return MEMTX_ERROR;
        }
    }
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        if (d->kind == GCC_PLL && address == d->reg + 0xc &&
            (bits & 7) != 0 && (bits & 7) != 7) {
            qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_GCC
                          ": independently gated Lucid outputs not modelled\n");
            return MEMTX_ERROR;
        }
    }
    REG(s, address) = (REG(s, address) & ~reg->write_mask) |
                      (bits & reg->write_mask);
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        if (d->kind == GCC_RCG && address == d->reg &&
            (bits & RCG_UPDATE) &&
            !gcc_rcg_valid(d, REG(s, address + 4), REG(s, address + 8),
                           REG(s, address + 12), REG(s, address + 16))) {
            qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GCC
                          ": invalid RCG programming for %s; UPDATE pending\n",
                          d->name);
        } else if (d->kind == GCC_PLL && address == d->reg &&
                   (bits & PLL_UPDATE) && !gcc_pll_valid(s, d)) {
            qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GCC
                          ": invalid PLL programming for %s; no latch ACK\n",
                          d->name);
        }
    }
    if (address == 0x4d110 || address == 0x71028) {
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_GCC
                      ": GPU/NPU active-request routing retained; consumers"
                      " are not modelled\n");
    }
    gcc_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps gcc_ops = {
    .read_with_attrs = gcc_read,
    .write_with_attrs = gcc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void gcc_timer(void *opaque)
{
    gcc_update(opaque);
}

static void gcc_clock_changed(void *opaque, ClockEvent event)
{
    gcc_update(opaque);
}

static void gcc_remote_vote(void *opaque, int n, int level)
{
    QcomSm8250GccState *s = opaque;
    if (gcc_clocks[n].kind != GCC_PLL && gcc_clocks[n].kind != GCC_BRANCH) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GCC
                      ": remote vote on non-votable clock %d\n", n);
        return;
    }
    s->remote_vote[n] = level;
    gcc_update(s);
}

static void gcc_hw_request(void *opaque, int n, int level)
{
    QcomSm8250GccState *s = opaque;
    s->hw_request[n] = level;
    gcc_update(s);
}

static void gcc_reset(DeviceState *dev)
{
    QcomSm8250GccState *s = QCOM_SM8250_GCC(dev);
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    memset(s->active_cfg, 0, sizeof(s->active_cfg));
    memset(s->active_m, 0, sizeof(s->active_m));
    memset(s->active_n, 0, sizeof(s->active_n));
    memset(s->active_d, 0, sizeof(s->active_d));
    memset(s->pll_locked, 0, sizeof(s->pll_locked));
    memset(s->pll_ack, 0, sizeof(s->pll_ack));
    memset(s->power_on, 0, sizeof(s->power_on));
    /* Inputs (remote-vote/hw-request and parent clocks) belong to providers. */
    for (i = 0; i < GCC_CLOCKS; i++) {
        s->clock_deadline[i] = -1;
    }
    for (i = 0; i < ARRAY_SIZE(s->pll_latch_deadline); i++) {
        s->pll_latch_deadline[i] = -1;
    }
    for (i = 0; i < GCC_POWER_DOMAINS; i++) {
        s->power_deadline[i] = -1;
        REG(s, gcc_power[i].reg) = GDSC_COLLAPSE;
    }
    if (s->boot_configured) {
        /* Explicit Linux-entry firmware assumption; not a silicon POR dump. */
        REG(s, 0) = PLL_FSM_EN | PLL_RESET_N;
        REG(s, 4) = 31;
        REG(s, 0x40) = 0x4000;
        REG(s, 0xc) = 0x107; /* 600 MHz main, 300 MHz even at 19.2 MHz XO */
        REG(s, 0x52018) = BIT(0);
        s->active_m[0] = 31;
        s->active_n[0] = 0x4000;
        s->pll_locked[0] = clock_get_hz(s->xo) != 0;
        REG(s, 0x1e4d4) = (6 << 8) | (2 << 12) | 1;
        REG(s, 0x1e4d8) = 384;
        REG(s, 0x1e4dc) = (~(15625 - 384)) & 0xffff;
        REG(s, 0x1e4e0) = (~15625) & 0xffff;
        s->active_cfg[128] = REG(s, 0x1e4d4);
        s->active_m[128] = REG(s, 0x1e4d8);
        s->active_n[128] = REG(s, 0x1e4dc);
        s->active_d[128] = REG(s, 0x1e4e0);
        REG(s, 0x52010) = BIT(8) | BIT(2) | BIT(1);
    }
    s->ready = true;
    gcc_update(s);
}

static int gcc_post_load(void *opaque, int version)
{
    QcomSm8250GccState *s = opaque;
    size_t i;
    unsigned reg_index = 0;

    /* Reject corrupt or reserved state on incoming migration. */
    for (i = 0; i < GCC_REGISTER_WORDS; i++) {
        if (reg_index < ARRAY_SIZE(gcc_registers) &&
            gcc_registers[reg_index].reg == i * 4) {
            if (s->regs[i] & ~gcc_registers[reg_index].write_mask) {
                return -EINVAL;
            }
            reg_index++;
        } else if (s->regs[i]) {
            return -EINVAL;
        }
    }
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        if (s->clock_deadline[i] < -1 ||
            (d->kind != GCC_PLL && d->kind != GCC_RCG &&
             s->clock_deadline[i] != -1) ||
            (d->kind == GCC_RCG &&
             !gcc_rcg_valid(d, s->active_cfg[i], s->active_m[i],
                            s->active_n[i], s->active_d[i]))) {
            return -EINVAL;
        }
        if (d->kind == GCC_RCG) {
            uint32_t mask = (1U << d->mnd_width) - 1;
            const GccRegisterDesc *cfg = gcc_find_register(d->reg + 4);
            if ((s->active_cfg[i] & ~cfg->write_mask) ||
                ((s->active_m[i] | s->active_n[i] | s->active_d[i]) &
                 ~mask)) {
                return -EINVAL;
            }
        } else if (d->kind == GCC_PLL) {
            if (s->active_cfg[i] || s->active_d[i] ||
                s->active_m[i] > 0xffff || s->active_n[i] > 0xffff) {
                return -EINVAL;
            }
        } else if (s->active_cfg[i] || s->active_m[i] ||
                   s->active_n[i] || s->active_d[i]) {
            return -EINVAL;
        }
    }
    for (i = 0; i < ARRAY_SIZE(s->pll_latch_deadline); i++) {
        if (s->pll_latch_deadline[i] < -1 ||
            (i == 1 && (s->pll_latch_deadline[i] != -1 ||
                        s->pll_locked[i] || s->pll_ack[i]))) {
            return -EINVAL;
        }
    }
    for (i = 0; i < GCC_POWER_DOMAINS; i++) {
        if (s->power_deadline[i] < -1 ||
            (REG(s, gcc_power[i].reg) & GDSC_HW_CONTROL)) {
            return -EINVAL;
        }
    }
    s->ready = true;
    s->updating = false;
    gcc_update(s);
    return 0;
}

static const VMStateDescription vmstate_gcc = {
    .name = TYPE_QCOM_SM8250_GCC,
    /* v1 used 84 external parents, including a synthetic CPUSS input.
     * v2 has the real CPUSS parent chain and 83 inputs; reject v1 before
     * reading its incompatible clock array or accepting its semantics.
     */
    .version_id = 2,
    .minimum_version_id = 2,
    .post_load = gcc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250GccState, GCC_REGISTER_WORDS),
        VMSTATE_UINT32_ARRAY(active_cfg, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_UINT32_ARRAY(active_m, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_UINT32_ARRAY(active_n, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_UINT32_ARRAY(active_d, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_INT64_ARRAY(clock_deadline, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_INT64_ARRAY(power_deadline, QcomSm8250GccState,
                            GCC_POWER_DOMAINS),
        VMSTATE_INT64_ARRAY(pll_latch_deadline, QcomSm8250GccState, 4),
        VMSTATE_BOOL_ARRAY(pll_locked, QcomSm8250GccState, 4),
        VMSTATE_BOOL_ARRAY(pll_ack, QcomSm8250GccState, 4),
        VMSTATE_BOOL_ARRAY(power_on, QcomSm8250GccState, GCC_POWER_DOMAINS),
        VMSTATE_BOOL_ARRAY(remote_vote, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_BOOL_ARRAY(hw_request, QcomSm8250GccState, GCC_CLOCKS),
        VMSTATE_CLOCK(xo, QcomSm8250GccState),
        VMSTATE_CLOCK(sleep, QcomSm8250GccState),
        VMSTATE_CLOCK(audio, QcomSm8250GccState),
        VMSTATE_ARRAY_CLOCK(external, QcomSm8250GccState,
                             GCC_EXTERNAL_PARENTS),
        VMSTATE_END_OF_LIST()
    },
};

static void gcc_init(Object *obj)
{
    QcomSm8250GccState *s = QCOM_SM8250_GCC(obj);
    DeviceState *dev = DEVICE(obj);
    unsigned i, count = 0;

    memory_region_init_io(&s->iomem, obj, &gcc_ops, s,
                          TYPE_QCOM_SM8250_GCC, SM8250_GCC_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    qdev_init_gpio_out_named(dev, s->reset_out, "reset", GCC_RESETS);
    qdev_init_gpio_out_named(dev, s->power_out, "power", GCC_POWER_DOMAINS);
    qdev_init_gpio_in_named(dev, gcc_remote_vote, "remote-vote", GCC_CLOCKS);
    qdev_init_gpio_in_named(dev, gcc_hw_request, "hw-request", GCC_CLOCKS);
    s->xo = qdev_init_clock_in(dev, "xo", gcc_clock_changed, s, ClockUpdate);
    s->sleep = qdev_init_clock_in(dev, "sleep", gcc_clock_changed, s,
                                 ClockUpdate);
    s->audio = qdev_init_clock_in(dev, "audio", gcc_clock_changed, s,
                                 ClockUpdate);
    for (i = 0; i < GCC_CLOCKS; i++) {
        const GccClockDesc *d = &gcc_clocks[i];
        if (d->name) {
            s->clocks[i] = qdev_init_clock_out(dev, d->name);
        }
        if (d->kind == GCC_BRANCH && d->external >= 0) {
            g_autofree char *name = g_strdup_printf("%s_parent", d->name);
            s->external[d->external] = qdev_init_clock_in(dev, name,
                                            gcc_clock_changed, s, ClockUpdate);
            count++;
        }
    }
    assert(count == GCC_EXTERNAL_PARENTS);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, gcc_timer, s);
}

static void gcc_finalize(Object *obj)
{
    QcomSm8250GccState *s = QCOM_SM8250_GCC(obj);
    timer_free(s->timer);
}

static const Property gcc_properties[] = {
    DEFINE_PROP_BOOL("boot-configured", QcomSm8250GccState,
                     boot_configured, false),
};

static void gcc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->desc = "Qualcomm SM8250 GCC register and clock-gate model";
    dc->vmsd = &vmstate_gcc;
    device_class_set_props(dc, gcc_properties);
    device_class_set_legacy_reset(dc, gcc_reset);
}

static const TypeInfo gcc_info = {
    .name = TYPE_QCOM_SM8250_GCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250GccState),
    .instance_init = gcc_init,
    .instance_finalize = gcc_finalize,
    .class_init = gcc_class_init,
};

static void gcc_register_types(void)
{
    type_register_static(&gcc_info);
}
type_init(gcc_register_types)
