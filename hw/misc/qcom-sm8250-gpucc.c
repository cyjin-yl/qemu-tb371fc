/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * SM8250 GPUCC digital reference model. See tb371fc/docs/gpucc-sm8250.md.
 * Register topology and software sequences are from the pinned official Kona
 * gpucc-kona.c / clk-alpha-pll.c and Linux v6.12.114 gpucc-sm8250.c.
 * No GPU execution, analog PLL, inferred PHY frequency or firmware is modeled.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-gpucc.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/irq.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
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
#define PLL_WRITE_MASK (PLL_OUTCTRL | PLL_BYPASSNL | PLL_RESET_N | \
                        PLL_UPDATE | PLL_UPDATE_BYPASS)
#define PLL_STATUS_MASK (PLL_PCAL_DONE | PLL_ACK | PLL_LOCK)
#define RCG_UPDATE BIT(0)
#define RCG_ROOT_EN BIT(1)
#define CLOCK_OFF BIT(31)
#define RCG_CFG_MASK 0x0010371fU

/* Explicit ordering delays in virtual ns; not measured silicon latencies. */
#define PLL_LOCK_NS 5000
#define PLL_LATCH_NS 1000
#define RCG_UPDATE_NS 1000

enum {
    R_MODE, R_L, R_CAL_L, R_USER, R_USER_U, R_USER_U1,
    R_CONFIG, R_CONFIG_U, R_CONFIG_U1, R_TEST, R_TEST_U, R_TEST_U1,
    R_OPMODE, R_ALPHA, R_CMD, R_CFG, R_BRANCH, R_RESET = R_BRANCH + 15,
};

enum {
    I_XO, I_PLL0, I_GCC_MAIN, I_GCC_DIV, I_TEST, I_QDSS,
    I_AHB, I_CRC_AHB, I_CX_APB, I_CX_SNOC, I_CXO_AON, I_CXO,
    I_GX_VSENSE, I_SLEEP, I_SMMU,
};

typedef struct GpuccRegister {
    uint32_t offset;
    uint32_t write_mask;
    uint32_t status_mask;
} GpuccRegister;

typedef struct GpuccBranch {
    const char *name;
    int input;                 /* -1 means the GMU RCG. */
} GpuccBranch;

static const GpuccRegister registers[GPUCC_REGISTERS] = {
    [R_MODE]      = { 0x0100, PLL_WRITE_MASK, PLL_STATUS_MASK },
    [R_L]         = { 0x0104, 0xffff, 0 },
    [R_CAL_L]     = { 0x0108, 0xffff, 0 },
    [R_USER]      = { 0x010c, 7, 0 },
    [R_USER_U]    = { 0x0110, UINT32_MAX, 0 },
    [R_USER_U1]   = { 0x0114, UINT32_MAX, 0 },
    [R_CONFIG]    = { 0x0118, UINT32_MAX, 0 },
    [R_CONFIG_U]  = { 0x011c, UINT32_MAX, 0 },
    [R_CONFIG_U1] = { 0x0120, UINT32_MAX, 0 },
    [R_TEST]      = { 0x0124, UINT32_MAX, 0 },
    [R_TEST_U]    = { 0x0128, UINT32_MAX, 0 },
    [R_TEST_U1]   = { 0x012c, UINT32_MAX, 0 },
    /* STATUS +0x30 has no modeled bit semantics and remains inaccessible. */
    [R_OPMODE]    = { 0x0138, 1, 0 },
    [R_ALPHA]     = { 0x0140, 0xffff, 0 },
    [R_CMD]       = { 0x1120, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG]       = { 0x1124, RCG_CFG_MASK, 0 },
    [R_BRANCH + 0]  = { 0x1078, 1, CLOCK_OFF },
    [R_BRANCH + 1]  = { 0x107c, 1, CLOCK_OFF },
    [R_BRANCH + 2]  = { 0x1088, 1, CLOCK_OFF },
    [R_BRANCH + 3]  = { 0x1098, 0xff1, CLOCK_OFF },
    [R_BRANCH + 4]  = { 0x1080, 1, CLOCK_OFF },
    [R_BRANCH + 5]  = { 0x1094, 1, CLOCK_OFF },
    [R_BRANCH + 6]  = { 0x1084, 1, CLOCK_OFF },
    [R_BRANCH + 7]  = { 0x108c, 1, CLOCK_OFF },
    [R_BRANCH + 8]  = { 0x1004, 1, CLOCK_OFF },
    [R_BRANCH + 9]  = { 0x109c, 1, CLOCK_OFF },
    [R_BRANCH + 10] = { 0x1064, 1, CLOCK_OFF },
    [R_BRANCH + 11] = { 0x105c, 1, CLOCK_OFF },
    [R_BRANCH + 12] = { 0x1058, 1, CLOCK_OFF },
    [R_BRANCH + 13] = { 0x1090, 1, CLOCK_OFF },
    [R_BRANCH + 14] = { 0x5000, 1, CLOCK_OFF },
    /* GX BCR 0x1008 belongs to the existing GX device, never this model. */
    [R_RESET + 0] = { 0x1160, 1, 0 },
    [R_RESET + 1] = { 0x1068, 1, 0 },
    [R_RESET + 2] = { 0x10a0, 1, 0 },
    [R_RESET + 3] = { 0x111c, 1, 0 },
    [R_RESET + 4] = { 0x1000, 1, 0 },
};

static const GpuccBranch branches[GPUCC_BRANCHES] = {
    { "gpu_cc_ahb_clk", I_AHB },
    { "gpu_cc_crc_ahb_clk", I_CRC_AHB },
    { "gpu_cc_cx_apb_clk", I_CX_APB },
    { "gpu_cc_cx_gmu_clk", -1 },
    { "gpu_cc_cx_qdss_at_clk", I_QDSS },
    { "gpu_cc_cx_qdss_trig_clk", I_QDSS },
    { "gpu_cc_cx_qdss_tsctr_clk", I_QDSS },
    { "gpu_cc_cx_snoc_dvm_clk", I_CX_SNOC },
    { "gpu_cc_cxo_aon_clk", I_CXO_AON },
    { "gpu_cc_cxo_clk", I_CXO },
    { "gpu_cc_gx_gmu_clk", -1 },
    { "gpu_cc_gx_qdss_tsctr_clk", I_QDSS },
    { "gpu_cc_gx_vsense_clk", I_GX_VSENSE },
    { "gpu_cc_sleep_clk", I_SLEEP },
    { "gpu_cc_hlos1_vote_gpu_smmu_clk", I_SMMU },
};

static const char *const input_names[GPUCC_INPUTS] = {
    "xo", "gpu_cc_pll0", "gcc_gpu_gpll0_clk_src",
    "gcc_gpu_gpll0_div_clk_src", "core_bi_pll_test_se", "qdss_qmp_clk",
    "gpu_cc_ahb_clk_parent", "gpu_cc_crc_ahb_clk_parent",
    "gpu_cc_cx_apb_clk_parent", "gpu_cc_cx_snoc_dvm_clk_parent",
    "gpu_cc_cxo_aon_clk_parent", "gpu_cc_cxo_clk_parent",
    "gpu_cc_gx_vsense_clk_parent", "gpu_cc_sleep_clk_parent",
    "gpu_cc_hlos1_vote_gpu_smmu_clk_parent",
};

const hwaddr qcom_sm8250_gpucc_window_offsets[GPUCC_WINDOWS] = {
    0x100, 0x1000, 0x1058, 0x1078, 0x111c, 0x1160, 0x5000,
};
static const uint32_t window_sizes[GPUCC_WINDOWS] = {
    0x44, 8, 0x14, 0x2c, 0xc, 4, 4,
};

static int gpucc_register(hwaddr offset)
{
    unsigned i;

    for (i = 0; i < GPUCC_REGISTERS; i++) {
        if (registers[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static bool gpucc_recipe(QcomSm8250GpuccState *s)
{
    return s->regs[R_CAL_L] == 0x44 && s->regs[R_USER_U] == 0x805 &&
           s->regs[R_USER_U1] == 0 &&
           s->regs[R_CONFIG] == 0x20485699 &&
           s->regs[R_CONFIG_U] == 0x2261 &&
           s->regs[R_CONFIG_U1] == 0x029a699c &&
           !s->regs[R_TEST] && !s->regs[R_TEST_U] && !s->regs[R_TEST_U1];
}

static uint64_t gpucc_pll_frequency(QcomSm8250GpuccState *s,
                                     uint32_t l, uint32_t alpha)
{
    uint64_t xo = clock_get_hz(s->input[I_XO]);
    uint64_t factor = ((uint64_t)l << 16) | alpha;
    uint64_t hz;

    if (!l || l > 0xffff || alpha > 0xffff || !xo ||
        xo > UINT64_MAX / factor) {
        return 0;
    }
    hz = xo * factor / 65536;
    return hz >= 249600000 && hz <= 2000000000 ? hz : 0;
}

static bool gpucc_pll_running(QcomSm8250GpuccState *s)
{
    return s->enabled_reference && (s->regs[R_MODE] & PLL_RESET_N) &&
           s->regs[R_OPMODE] == 1 && gpucc_recipe(s) &&
           gpucc_pll_frequency(s, s->active_l, s->active_alpha);
}

static bool gpucc_cfg_valid(uint32_t cfg)
{
    unsigned src = (cfg >> 8) & 7;

    /* GMU has no M/N counter. Selectors 2 and 4 have no documented parent. */
    return !(cfg & ~RCG_CFG_MASK) && !(cfg & 0x3000) &&
           src != 2 && src != 4;
}

static bool gpucc_branch_requested(QcomSm8250GpuccState *s, unsigned index)
{
    return (s->regs[R_BRANCH + index] & 1) || s->remote_vote[index];
}

static void gpucc_rates(QcomSm8250GpuccState *s, uint64_t *rates)
{
    uint64_t parent = 0;
    uint32_t div;
    unsigned i;
    bool demand;

    memset(rates, 0, sizeof(uint64_t) * GPUCC_OUTPUTS);
    if (!s->enabled_reference) {
        return;
    }
    if (s->pll_locked && gpucc_pll_running(s) &&
        (s->regs[R_MODE] & PLL_OUTCTRL) && (s->regs[R_USER] & 7) == 7) {
        rates[0] = gpucc_pll_frequency(s, s->active_l, s->active_alpha);
    }
    demand = (s->regs[R_CMD] & RCG_ROOT_EN) ||
             gpucc_branch_requested(s, 3) || gpucc_branch_requested(s, 10);
    if (demand && gpucc_cfg_valid(s->active_cfg)) {
        switch ((s->active_cfg >> 8) & 7) {
        case 0: parent = clock_get_hz(s->input[I_XO]); break;
        case 1: parent = clock_get_hz(s->input[I_PLL0]); break;
        case 3: parent = rates[0]; break;
        case 5: parent = clock_get_hz(s->input[I_GCC_MAIN]); break;
        case 6: parent = clock_get_hz(s->input[I_GCC_DIV]); break;
        case 7: parent = clock_get_hz(s->input[I_TEST]); break;
        }
        div = s->active_cfg & 0x1f;
        /* The HID field 0 bypasses; fields >=1 divide by (field+1)/2. */
        rates[1] = div ? parent / (div + 1) * 2 +
                        (parent % (div + 1)) * 2 / (div + 1) : parent;
    }
    for (i = 0; i < GPUCC_BRANCHES; i++) {
        if (gpucc_branch_requested(s, i)) {
            rates[i + 2] = branches[i].input < 0 ? rates[1] :
                           clock_get_hz(s->input[branches[i].input]);
        }
    }
}

static void gpucc_publish(QcomSm8250GpuccState *s, bool propagate)
{
    uint64_t rates[GPUCC_OUTPUTS];
    unsigned i;

    gpucc_rates(s, rates);
    for (i = 0; i < GPUCC_OUTPUTS; i++) {
        bool changed = clock_set_hz(s->clocks[i], rates[i]);

        if (changed && propagate) {
            clock_propagate(s->clocks[i]);
        }
    }
    if (propagate) {
        for (i = 0; i < GPUCC_RESETS; i++) {
            qemu_set_irq(s->reset_out[i], s->enabled_reference &&
                         (s->regs[R_RESET + i] & 1));
        }
    }
}

static void gpucc_restore_reset_outputs(QcomSm8250GpuccState *s)
{
    unsigned i;

    for (i = 0; i < GPUCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], s->enabled_reference &&
                     (s->regs[R_RESET + i] & 1));
    }
}

static void gpucc_schedule(QcomSm8250GpuccState *s)
{
    int64_t next = -1;
    int64_t deadlines[] = {
        s->lock_deadline, s->latch_deadline, s->rcg_deadline,
    };
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(deadlines); i++) {
        if (deadlines[i] >= 0 && (next < 0 || deadlines[i] < next)) {
            next = deadlines[i];
        }
    }
    if (next < 0) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static void gpucc_update(QcomSm8250GpuccState *s)
{
    int64_t now;
    uint64_t xo;

    if (!s->ready || s->updating) {
        return;
    }
    s->updating = true;
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    xo = clock_get_hz(s->input[I_XO]);
    if (xo != s->last_xo_hz) {
        s->last_xo_hz = xo;
        s->pll_locked = false;
        s->lock_deadline = -1;
    }
    if (s->latch_deadline >= 0) {
        if (!(s->regs[R_MODE] & PLL_RESET_N) ||
            !(s->regs[R_MODE] & PLL_UPDATE) || !gpucc_recipe(s) ||
            !(s->regs[R_MODE] & PLL_UPDATE_BYPASS) ||
            !gpucc_pll_frequency(s, s->pending_l, s->pending_alpha)) {
            s->latch_deadline = -1;
        } else if (now >= s->latch_deadline) {
            s->active_l = s->pending_l;
            s->active_alpha = s->pending_alpha;
            s->pll_ack = true;
            s->pll_locked = false;
            s->lock_deadline = s->latch_deadline + PLL_LOCK_NS;
            s->latch_deadline = -1;
        }
    }
    if (!gpucc_pll_running(s)) {
        s->pll_locked = false;
        s->lock_deadline = -1;
    } else if (!s->pll_locked) {
        if (s->lock_deadline < 0) {
            s->lock_deadline = now + PLL_LOCK_NS;
        } else if (now >= s->lock_deadline) {
            s->pll_locked = true;
            s->pll_calibrated = true;
            s->lock_deadline = -1;
        }
    }
    if (s->rcg_deadline >= 0 && now >= s->rcg_deadline) {
        s->active_cfg = s->pending_cfg;
        s->regs[R_CMD] &= ~RCG_UPDATE;
        s->rcg_deadline = -1;
    }
    gpucc_publish(s, true);
    gpucc_schedule(s);
    s->updating = false;
}

static void gpucc_timer(void *opaque)
{
    gpucc_update(opaque);
}

static void gpucc_clock_changed(void *opaque, ClockEvent event)
{
    gpucc_update(opaque);
}

static void gpucc_remote_vote(void *opaque, int index, int level)
{
    QcomSm8250GpuccState *s = opaque;

    s->remote_vote[index] = !!level;
    gpucc_update(s);
}

static MemTxResult gpucc_read(void *opaque, hwaddr address, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomGpuccWindow *window = opaque;
    QcomSm8250GpuccState *s = window->state;
    int id = gpucc_register(window->offset + address);
    uint64_t rates[GPUCC_OUTPUTS];

    *value = 0;
    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GPUCC
                      ": unmodeled read at 0x%" HWADDR_PRIx " size %u\n",
                      window->offset + address, size);
        return MEMTX_ERROR;
    }
    gpucc_update(s);
    *value = s->regs[id];
    if (id == R_MODE) {
        *value |= s->pll_locked ? PLL_LOCK : 0;
        *value |= s->pll_ack ? PLL_ACK : 0;
        *value |= s->pll_calibrated ? PLL_PCAL_DONE : 0;
    } else if (id == R_CMD || (id >= R_BRANCH && id < R_RESET)) {
        gpucc_rates(s, rates);
        if (!rates[id == R_CMD ? 1 : id - R_BRANCH + 2]) {
            *value |= CLOCK_OFF;
        }
    }
    return MEMTX_OK;
}

static void gpucc_start_pll(QcomSm8250GpuccState *s)
{
    s->pll_locked = false;
    s->lock_deadline = -1;
    if ((s->regs[R_MODE] & PLL_RESET_N) && s->regs[R_OPMODE] == 1 &&
        gpucc_recipe(s) && s->regs[R_L]) {
        s->active_l = s->regs[R_L];
        s->active_alpha = s->regs[R_ALPHA];
    } else {
        s->active_l = 0;
        s->active_alpha = 0;
    }
}

static MemTxResult gpucc_write(void *opaque, hwaddr address, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomGpuccWindow *window = opaque;
    QcomSm8250GpuccState *s = window->state;
    int id = gpucc_register(window->offset + address);
    uint32_t old, val;

    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GPUCC
                      ": unmodeled write at 0x%" HWADDR_PRIx " size %u\n",
                      window->offset + address, size);
        return MEMTX_ERROR;
    }
    if (value & ~(uint64_t)(registers[id].write_mask |
                             registers[id].status_mask)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GPUCC
                      ": unsupported bits at 0x%" HWADDR_PRIx "\n",
                      window->offset + address);
        return MEMTX_ERROR;
    }
    /* The pinned Lucid driver enables/disables all three outputs together.
     * Individual main/aux selections are outside this bounded profile; do
     * not misreport an unsupported selection as a physically stopped PLL.
     */
    if (id == R_USER && value != 0 && value != 7) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GPUCC
                      ": unsupported PLL output selection\n");
        return MEMTX_ERROR;
    }
    gpucc_update(s);
    old = s->regs[id];
    val = value & registers[id].write_mask;
    s->regs[id] = val;
    if (id == R_MODE) {
        if (!(val & PLL_RESET_N)) {
            s->pll_locked = false;
            s->pll_ack = false;
            s->pll_calibrated = false;
            s->active_l = s->active_alpha = 0;
            s->lock_deadline = s->latch_deadline = -1;
        } else if (!(old & PLL_RESET_N)) {
            gpucc_start_pll(s);
        }
        if (!(val & PLL_UPDATE)) {
            s->pll_ack = false;
            s->latch_deadline = -1;
        } else if (!(old & PLL_UPDATE)) {
            s->pll_ack = false;
            s->latch_deadline = -1;
            if ((val & PLL_RESET_N) && (val & PLL_UPDATE_BYPASS) &&
                gpucc_recipe(s) &&
                gpucc_pll_frequency(s, s->regs[R_L], s->regs[R_ALPHA])) {
                s->pending_l = s->regs[R_L];
                s->pending_alpha = s->regs[R_ALPHA];
                s->latch_deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                                    PLL_LATCH_NS;
            }
        }
    } else if (id == R_OPMODE && val != old) {
        gpucc_start_pll(s);
    } else if (id == R_CMD) {
        if (!(val & RCG_UPDATE)) {
            s->rcg_deadline = -1;
        } else if (!(old & RCG_UPDATE) && gpucc_cfg_valid(s->regs[R_CFG])) {
            s->pending_cfg = s->regs[R_CFG];
            s->rcg_deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                              RCG_UPDATE_NS;
        }
    }
    gpucc_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps gpucc_ops = {
    .read_with_attrs = gpucc_read,
    .write_with_attrs = gpucc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void gpucc_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);

    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    s->active_l = s->active_alpha = s->active_cfg = 0;
    s->pending_l = s->pending_alpha = s->pending_cfg = 0;
    s->lock_deadline = s->latch_deadline = s->rcg_deadline = -1;
    s->pll_locked = s->pll_ack = s->pll_calibrated = false;
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
    s->updating = false;
    s->ready = false;
    /* Input Clock values and external remote requests are provider-owned. */
}

static void gpucc_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);

    s->ready = true;
    gpucc_publish(s, true);
}

static int gpucc_pre_load(void *opaque)
{
    QcomSm8250GpuccState *s = opaque;

    s->ready = false;
    timer_del(s->timer);
    return 0;
}

static int gpucc_post_load(void *opaque, int version_id)
{
    QcomSm8250GpuccState *s = opaque;
    unsigned i;

    for (i = 0; i < GPUCC_REGISTERS; i++) {
        if (s->regs[i] & ~registers[i].write_mask) {
            return -EINVAL;
        }
    }
    if ((s->regs[R_USER] != 0 && s->regs[R_USER] != 7) ||
        s->active_l > 0xffff || s->active_alpha > 0xffff ||
        s->pending_l > 0xffff || s->pending_alpha > 0xffff ||
        !gpucc_cfg_valid(s->active_cfg) || !gpucc_cfg_valid(s->pending_cfg) ||
        s->lock_deadline < -1 || s->latch_deadline < -1 ||
        s->rcg_deadline < -1 ||
        (s->pll_locked && (!gpucc_pll_running(s) ||
                          s->lock_deadline >= 0 || !s->pll_calibrated)) ||
        (s->lock_deadline >= 0 && !gpucc_pll_running(s)) ||
        (s->pll_calibrated && !(s->regs[R_MODE] & PLL_RESET_N)) ||
        (s->pll_ack && (!(s->regs[R_MODE] & PLL_UPDATE) ||
                       !(s->regs[R_MODE] & PLL_RESET_N))) ||
        (s->latch_deadline >= 0 &&
         (!(s->regs[R_MODE] & PLL_UPDATE) || s->pll_ack ||
          !(s->regs[R_MODE] & PLL_RESET_N) ||
          !(s->regs[R_MODE] & PLL_UPDATE_BYPASS) || !s->pending_l)) ||
        (s->rcg_deadline >= 0 && !(s->regs[R_CMD] & RCG_UPDATE))) {
        return -EINVAL;
    }
    if (!s->enabled_reference) {
        for (i = 0; i < GPUCC_REGISTERS; i++) {
            if (s->regs[i]) {
                return -EINVAL;
            }
        }
        if (s->active_l || s->active_alpha || s->active_cfg ||
            s->pending_l || s->pending_alpha || s->pending_cfg ||
            s->pll_locked || s->pll_ack || s->pll_calibrated ||
            s->lock_deadline >= 0 || s->latch_deadline >= 0 ||
            s->rcg_deadline >= 0) {
            return -EINVAL;
        }
    }
    s->updating = false;
    s->ready = true;
    /* No callbacks, time advancement or new deadlines during incoming load. */
    gpucc_publish(s, false);
    /* BCR levels, unlike Clock propagation, must be re-driven after load. */
    gpucc_restore_reset_outputs(s);
    gpucc_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_gpucc = {
    .name = TYPE_QCOM_SM8250_GPUCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = gpucc_pre_load,
    .post_load = gpucc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250GpuccState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250GpuccState, GPUCC_REGISTERS),
        VMSTATE_UINT32(active_l, QcomSm8250GpuccState),
        VMSTATE_UINT32(active_alpha, QcomSm8250GpuccState),
        VMSTATE_UINT32(active_cfg, QcomSm8250GpuccState),
        VMSTATE_UINT32(pending_l, QcomSm8250GpuccState),
        VMSTATE_UINT32(pending_alpha, QcomSm8250GpuccState),
        VMSTATE_UINT32(pending_cfg, QcomSm8250GpuccState),
        VMSTATE_INT64(lock_deadline, QcomSm8250GpuccState),
        VMSTATE_INT64(latch_deadline, QcomSm8250GpuccState),
        VMSTATE_INT64(rcg_deadline, QcomSm8250GpuccState),
        VMSTATE_UINT64(last_xo_hz, QcomSm8250GpuccState),
        VMSTATE_BOOL(pll_locked, QcomSm8250GpuccState),
        VMSTATE_BOOL(pll_ack, QcomSm8250GpuccState),
        VMSTATE_BOOL(pll_calibrated, QcomSm8250GpuccState),
        VMSTATE_BOOL_ARRAY(remote_vote, QcomSm8250GpuccState, GPUCC_BRANCHES),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250GpuccState, GPUCC_INPUTS),
        VMSTATE_END_OF_LIST()
    },
};

static void gpucc_fixture_get(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    uint32_t hz = clock_get_hz(opaque);

    visit_type_uint32(v, name, &hz, errp);
}

static void gpucc_fixture_set(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);
    Clock *input = opaque;
    uint32_t hz;

    if (!visit_type_uint32(v, name, &hz, errp)) {
        return;
    }
    if (clock_has_source(input)) {
        error_setg(errp, "%s cannot override a connected clock source", name);
        return;
    }
    clock_set_hz(input, hz);
    gpucc_update(s);
}

static void gpucc_rate_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);
    uint64_t rates[GPUCC_OUTPUTS];

    gpucc_rates(s, rates);
    visit_type_uint64(v, name, &rates[GPOINTER_TO_UINT(opaque)], errp);
}

static void gpucc_active_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);
    uint32_t values[] = { s->active_l, s->active_alpha, s->active_cfg };

    visit_type_uint32(v, name, &values[GPOINTER_TO_UINT(opaque)], errp);
}

static void gpucc_status_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);
    bool values[] = {
        s->pll_locked, s->pll_ack, s->pll_calibrated,
        s->lock_deadline >= 0, s->latch_deadline >= 0, s->rcg_deadline >= 0,
    };

    visit_type_bool(v, name, &values[GPOINTER_TO_UINT(opaque)], errp);
}

static void gpucc_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(dev);

    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->ready = true;
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
}

static void gpucc_init(Object *obj)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const statuses[] = {
        "pll-locked", "pll-ack", "pll-calibrated", "pll-lock-pending",
        "pll-latch-pending", "rcg-pending",
    };
    static const char *const active[] = {
        "pll-active-l", "pll-active-alpha", "rcg-active-cfg",
    };
    unsigned i;

    s->lock_deadline = s->latch_deadline = s->rcg_deadline = -1;
    for (i = 0; i < GPUCC_WINDOWS; i++) {
        g_autofree char *name = g_strdup_printf("gpucc-%x",
                                 (unsigned)qcom_sm8250_gpucc_window_offsets[i]);
        s->windows[i].state = s;
        s->windows[i].offset = qcom_sm8250_gpucc_window_offsets[i];
        memory_region_init_io(&s->iomem[i], obj, &gpucc_ops, &s->windows[i],
                              name, window_sizes[i]);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem[i]);
    }
    for (i = 0; i < GPUCC_INPUTS; i++) {
        s->input[i] = qdev_init_clock_in(dev, input_names[i],
                                        gpucc_clock_changed, s, ClockUpdate);
        if (qtest_enabled()) {
            g_autofree char *name = g_strdup_printf("qtest-%s-hz", input_names[i]);
            object_property_add(obj, name, "uint32", gpucc_fixture_get,
                                gpucc_fixture_set, NULL, s->input[i]);
        }
    }
    for (i = 0; i < GPUCC_OUTPUTS; i++) {
        const char *name = i == 0 ? "gpu_cc_pll1" :
                           i == 1 ? "gpu_cc_gmu_clk_src" : branches[i - 2].name;
        g_autofree char *property = g_strdup_printf("clk.%s.hz", name);

        s->clocks[i] = qdev_init_clock_out(dev, name);
        object_property_add(obj, property, "uint64", gpucc_rate_get, NULL,
                            NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < ARRAY_SIZE(statuses); i++) {
        object_property_add(obj, statuses[i], "bool", gpucc_status_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < ARRAY_SIZE(active); i++) {
        object_property_add(obj, active[i], "uint32", gpucc_active_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
    }
    qdev_init_gpio_in_named(dev, gpucc_remote_vote, "remote-vote", GPUCC_BRANCHES);
    qdev_init_gpio_out_named(dev, s->reset_out, "reset", GPUCC_RESETS);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, gpucc_timer, s);
}

static void gpucc_finalize(Object *obj)
{
    QcomSm8250GpuccState *s = QCOM_SM8250_GPUCC(obj);

    timer_free(s->timer);
}

static const Property gpucc_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250GpuccState,
                     enabled_reference, false),
};

static void gpucc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm SM8250 GPUCC explicit digital reference model";
    dc->realize = gpucc_realize;
    dc->vmsd = &vmstate_gpucc;
    device_class_set_props(dc, gpucc_properties);
    rc->phases.enter = gpucc_reset_enter;
    rc->phases.hold = gpucc_reset_hold;
}

static const TypeInfo gpucc_info = {
    .name = TYPE_QCOM_SM8250_GPUCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250GpuccState),
    .instance_init = gpucc_init,
    .instance_finalize = gpucc_finalize,
    .class_init = gpucc_class_init,
};

static void gpucc_register_types(void)
{
    type_register_static(&gpucc_info);
}
type_init(gpucc_register_types)
