/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Kona v2 NPUCC digital reference model. Register/sequence evidence:
 * OnePlusOSS/android_kernel_oneplus_sm8250 at
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae, npucc-kona.c,
 * clk-alpha-pll.c, clk-rcg2.c, reset.c and clk-rpmh.c.
 * See tb371fc/docs/npucc-sm8250.md. This is not NPU/DSP execution.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-npucc.h"
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
#define PLL_LOCK_NS 5000
#define PLL_LATCH_NS 1000
#define RCG_UPDATE_NS 1000
#define PLL_MAX_HZ UINT64_C(2000000000)
#define PLL_RATE_MARGIN_HZ 500

enum {
    R_MODE, R_L, R_CAL_L, R_USER, R_USER_U, R_USER_U1,
    R_CONFIG, R_CONFIG_U, R_CONFIG_U1, R_TEST, R_TEST_U, R_TEST_U1,
    R_OPMODE, R_ALPHA,
};
#define PR(p, field) ((p) * NPUCC_PLL_REGISTERS + (field))
#define PREG(s, p, field) ((s)->regs[PR(p, field)])
#define R_CMD(r) (NPUCC_PLLS * NPUCC_PLL_REGISTERS + 2 * (r))
#define R_CFG(r) (R_CMD(r) + 1)
#define R_BRANCH (NPUCC_PLLS * NPUCC_PLL_REGISTERS + 2 * NPUCC_RCGS)
#define R_BCR (R_BRANCH + NPUCC_BRANCHES)
#define R_CRC (R_BCR + 4)
#define INPUT(i) (-(i) - 1)
#define NO_PARENT (-127)

enum {
    I_XO, I_XO_AO, I_GCC_MAIN, I_GCC_DIV, I_TEST, I_QDSS, I_LLM_TEMP,
    I_LLM_CURR, I_S2P, I_GCC_AXI, I_GCC_DMA, I_NOC_AHB,
};
enum {
    C_PLL0, C_PLL1, C_QPLL, C_EVEN0, C_EVEN1, C_CRC, C_RCG,
    C_BRANCH = C_RCG + NPUCC_RCGS,
};

typedef struct NpuccRegister {
    uint32_t bank, offset, write_mask, status_mask;
} NpuccRegister;
typedef struct NpuccBranch {
    const char *name;
    int parent;
} NpuccBranch;
typedef struct NpuccRcg {
    const char *name;
    int parents[8];
} NpuccRcg;
typedef struct NpuccReset {
    unsigned reg;
    uint32_t mask;
} NpuccReset;

static const NpuccRegister registers[NPUCC_REGISTERS] = {
    [PR(0, R_MODE)] = { 0, 0x0, PLL_WRITE_MASK, PLL_STATUS_MASK },
    [PR(0, R_L)] = { 0, 0x4, 0xffff, 0 },
    [PR(0, R_CAL_L)] = { 0, 0x8, 0xffff, 0 },
    [PR(0, R_USER)] = { 0, 0xc, 0xf07, 0 },
    [PR(0, R_USER_U)] = { 0, 0x10, UINT32_MAX, 0 },
    [PR(0, R_USER_U1)] = { 0, 0x14, UINT32_MAX, 0 },
    [PR(0, R_CONFIG)] = { 0, 0x18, UINT32_MAX, 0 },
    [PR(0, R_CONFIG_U)] = { 0, 0x1c, UINT32_MAX, 0 },
    [PR(0, R_CONFIG_U1)] = { 0, 0x20, UINT32_MAX, 0 },
    [PR(0, R_TEST)] = { 0, 0x24, UINT32_MAX, 0 },
    [PR(0, R_TEST_U)] = { 0, 0x28, UINT32_MAX, 0 },
    [PR(0, R_TEST_U1)] = { 0, 0x2c, UINT32_MAX, 0 },
    [PR(0, R_OPMODE)] = { 0, 0x38, 1, 0 },
    [PR(0, R_ALPHA)] = { 0, 0x40, 0xffff, 0 },
    [PR(1, R_MODE)] = { 0, 0x400, PLL_WRITE_MASK, PLL_STATUS_MASK },
    [PR(1, R_L)] = { 0, 0x404, 0xffff, 0 },
    [PR(1, R_CAL_L)] = { 0, 0x408, 0xffff, 0 },
    [PR(1, R_USER)] = { 0, 0x40c, 0xf07, 0 },
    [PR(1, R_USER_U)] = { 0, 0x410, UINT32_MAX, 0 },
    [PR(1, R_USER_U1)] = { 0, 0x414, UINT32_MAX, 0 },
    [PR(1, R_CONFIG)] = { 0, 0x418, UINT32_MAX, 0 },
    [PR(1, R_CONFIG_U)] = { 0, 0x41c, UINT32_MAX, 0 },
    [PR(1, R_CONFIG_U1)] = { 0, 0x420, UINT32_MAX, 0 },
    [PR(1, R_TEST)] = { 0, 0x424, UINT32_MAX, 0 },
    [PR(1, R_TEST_U)] = { 0, 0x428, UINT32_MAX, 0 },
    [PR(1, R_TEST_U1)] = { 0, 0x42c, UINT32_MAX, 0 },
    [PR(1, R_OPMODE)] = { 0, 0x438, 1, 0 },
    [PR(1, R_ALPHA)] = { 0, 0x440, 0xffff, 0 },
    [PR(2, R_MODE)] = { 2, 0x0, PLL_WRITE_MASK, PLL_STATUS_MASK },
    [PR(2, R_L)] = { 2, 0x4, 0xffff, 0 },
    [PR(2, R_CAL_L)] = { 2, 0x8, 0xffff, 0 },
    [PR(2, R_USER)] = { 2, 0xc, 7, 0 },
    [PR(2, R_USER_U)] = { 2, 0x10, UINT32_MAX, 0 },
    [PR(2, R_USER_U1)] = { 2, 0x14, UINT32_MAX, 0 },
    [PR(2, R_CONFIG)] = { 2, 0x18, UINT32_MAX, 0 },
    [PR(2, R_CONFIG_U)] = { 2, 0x1c, UINT32_MAX, 0 },
    [PR(2, R_CONFIG_U1)] = { 2, 0x20, UINT32_MAX, 0 },
    [PR(2, R_TEST)] = { 2, 0x24, UINT32_MAX, 0 },
    [PR(2, R_TEST_U)] = { 2, 0x28, UINT32_MAX, 0 },
    [PR(2, R_TEST_U1)] = { 2, 0x2c, UINT32_MAX, 0 },
    [PR(2, R_OPMODE)] = { 2, 0x38, 1, 0 },
    [PR(2, R_ALPHA)] = { 2, 0x40, 0xffff, 0 },
    [R_CMD(0)] = { 0, 0x1010, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG(0)] = { 0, 0x1014, RCG_CFG_MASK, 0 },
    [R_CMD(1)] = { 0, 0x1060, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG(1)] = { 0, 0x1064, RCG_CFG_MASK, 0 },
    [R_CMD(2)] = { 0, 0x1100, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG(2)] = { 0, 0x1104, RCG_CFG_MASK, 0 },
    [R_CMD(3)] = { 0, 0x1140, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG(3)] = { 0, 0x1144, RCG_CFG_MASK, 0 },
    [R_CMD(4)] = { 0, 0x1400, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG(4)] = { 0, 0x1404, RCG_CFG_MASK, 0 },
    [R_CMD(5)] = { 1, 0x28, RCG_UPDATE | RCG_ROOT_EN, CLOCK_OFF },
    [R_CFG(5)] = { 1, 0x2c, RCG_CFG_MASK, 0 },
    [R_BRANCH + 0] = { 0, 0x10d0, 1, CLOCK_OFF },
    [R_BRANCH + 1] = { 0, 0x10dc, 1, CLOCK_OFF },
    [R_BRANCH + 2] = { 0, 0x10d8, 1, CLOCK_OFF },
    [R_BRANCH + 3] = { 0, 0x1098, 1, CLOCK_OFF },
    [R_BRANCH + 4] = { 0, 0x1110, 1, CLOCK_OFF },
    [R_BRANCH + 5] = { 0, 0x109c, 1, CLOCK_OFF },
    [R_BRANCH + 6] = { 0, 0x10a0, 1, CLOCK_OFF },
    [R_BRANCH + 7] = { 0, 0x10a4, 1, CLOCK_OFF },
    [R_BRANCH + 8] = { 0, 0x1150, 1, CLOCK_OFF },
    [R_BRANCH + 9] = { 0, 0x10a8, 1, CLOCK_OFF },
    [R_BRANCH + 10] = { 0, 0x10ac, 1, CLOCK_OFF },
    [R_BRANCH + 11] = { 0, 0x1030, 1, CLOCK_OFF },
    [R_BRANCH + 12] = { 0, 0x1238, 1, CLOCK_OFF },
    [R_BRANCH + 13] = { 0, 0x1234, 1, CLOCK_OFF },
    [R_BRANCH + 14] = { 0, 0x107c, 1, CLOCK_OFF },
    [R_BRANCH + 15] = { 0, 0x10c4, 5, CLOCK_OFF },
    [R_BRANCH + 16] = { 0, 0x1094, 1, CLOCK_OFF },
    [R_BRANCH + 17] = { 0, 0x1214, 1, CLOCK_OFF },
    [R_BRANCH + 18] = { 0, 0x1210, 1, CLOCK_OFF },
    [R_BRANCH + 19] = { 0, 0x121c, 1, CLOCK_OFF },
    [R_BRANCH + 20] = { 0, 0x1218, 1, CLOCK_OFF },
    [R_BRANCH + 21] = { 0, 0x1224, 1, CLOCK_OFF },
    [R_BRANCH + 22] = { 0, 0x1078, 1, CLOCK_OFF },
    [R_BRANCH + 23] = { 0, 0x1074, 1, CLOCK_OFF },
    [R_BRANCH + 24] = { 0, 0x10d4, 5, CLOCK_OFF },
    [R_BRANCH + 25] = { 0, 0x10c8, 5, CLOCK_OFF },
    [R_BRANCH + 26] = { 0, 0x1090, 1, CLOCK_OFF },
    [R_BRANCH + 27] = { 0, 0x10c0, 1, CLOCK_OFF },
    [R_BRANCH + 28] = { 0, 0x10b8, 1, CLOCK_OFF },
    [R_BRANCH + 29] = { 0, 0x10b0, 1, CLOCK_OFF },
    [R_BRANCH + 30] = { 0, 0x10e0, 1, CLOCK_OFF },
    [R_BRANCH + 31] = { 0, 0x10cc, 1, CLOCK_OFF },
    [R_BRANCH + 32] = { 0, 0x1410, 1, CLOCK_OFF },
    [R_BCR + 0] = { 0, 0x10f0, 1, 0 },
    [R_BCR + 1] = { 0, 0x1130, 1, 0 },
    [R_BCR + 2] = { 0, 0x1000, 1, 0 },
    [R_BCR + 3] = { 0, 0x1200, 1, 0 },
    [R_CRC + 0] = { 0, 0x11a0, UINT32_MAX, 0 },
    [R_CRC + 1] = { 0, 0x11a4, UINT32_MAX, 0 },
    [R_CRC + 2] = { 0, 0x11b0, UINT32_MAX, 0 },
    [R_CRC + 3] = { 0, 0x11b4, UINT32_MAX, 0 },
};

static const NpuccRcg roots[NPUCC_RCGS] = {
    { "npu_cc_core_clk_src",
      { INPUT(I_XO), C_EVEN1, C_EVEN0, NO_PARENT, INPUT(I_GCC_MAIN), INPUT(I_GCC_DIV), NO_PARENT, INPUT(I_TEST) } },
    { "npu_cc_lmh_clk_src",
      { INPUT(I_XO), C_EVEN1, C_EVEN0, NO_PARENT, INPUT(I_GCC_MAIN), INPUT(I_GCC_DIV), NO_PARENT, INPUT(I_TEST) } },
    { "npu_cc_cal_hm0_clk_src",
      { INPUT(I_XO), C_EVEN1, C_CRC, NO_PARENT, INPUT(I_GCC_MAIN), INPUT(I_GCC_DIV), NO_PARENT, INPUT(I_TEST) } },
    { "npu_cc_cal_hm1_clk_src",
      { INPUT(I_XO), C_EVEN1, C_CRC, NO_PARENT, INPUT(I_GCC_MAIN), INPUT(I_GCC_DIV), NO_PARENT, INPUT(I_TEST) } },
    { "npu_cc_xo_clk_src",
      { INPUT(I_XO_AO), NO_PARENT, NO_PARENT, NO_PARENT, NO_PARENT, NO_PARENT, NO_PARENT, INPUT(I_TEST) } },
    { "npu_dsp_core_clk_src",
      { INPUT(I_XO), NO_PARENT, C_QPLL, NO_PARENT, NO_PARENT, NO_PARENT, NO_PARENT, INPUT(I_TEST) } },
};

static const NpuccBranch branches[NPUCC_BRANCHES] = {
    { "npu_cc_atb_clk", INPUT(5) },
    { "npu_cc_bto_core_clk", C_RCG + 4 },
    { "npu_cc_bwmon_clk", C_RCG + 4 },
    { "npu_cc_cal_hm0_cdc_clk", C_RCG + 2 },
    { "npu_cc_cal_hm0_clk", C_RCG + 2 },
    { "npu_cc_cal_hm0_dpm_ip_clk", C_RCG + 2 },
    { "npu_cc_cal_hm0_perf_cnt_clk", C_RCG + 2 },
    { "npu_cc_cal_hm1_cdc_clk", C_RCG + 2 },
    { "npu_cc_cal_hm1_clk", C_RCG + 2 },
    { "npu_cc_cal_hm1_dpm_ip_clk", C_RCG + 2 },
    { "npu_cc_cal_hm1_perf_cnt_clk", C_RCG + 2 },
    { "npu_cc_core_clk", C_RCG + 0 },
    { "npu_cc_dl_dpm_clk", C_RCG + 1 },
    { "npu_cc_dl_llm_clk", C_RCG + 1 },
    { "npu_cc_dpm_clk", C_RCG + 1 },
    { "npu_cc_dpm_temp_clk", INPUT(6) },
    { "npu_cc_dpm_xo_clk", C_RCG + 4 },
    { "npu_cc_dsp_ahbm_clk", C_RCG + 0 },
    { "npu_cc_dsp_ahbs_clk", C_RCG + 0 },
    { "npu_cc_dsp_axi_clk", INPUT(9) },
    { "npu_cc_dsp_bwmon_ahb_clk", C_RCG + 0 },
    { "npu_cc_dsp_bwmon_clk", C_RCG + 4 },
    { "npu_cc_isense_clk", C_RCG + 1 },
    { "npu_cc_llm_clk", C_RCG + 1 },
    { "npu_cc_llm_curr_clk", INPUT(7) },
    { "npu_cc_llm_temp_clk", INPUT(6) },
    { "npu_cc_llm_xo_clk", C_RCG + 4 },
    { "npu_cc_noc_ahb_clk", INPUT(I_NOC_AHB) },
    { "npu_cc_noc_axi_clk", INPUT(9) },
    { "npu_cc_noc_dma_clk", INPUT(10) },
    { "npu_cc_rsc_xo_clk", C_RCG + 4 },
    { "npu_cc_s2p_clk", INPUT(8) },
    { "npu_cc_xo_clk", C_RCG + 4 },
};

static const NpuccReset resets[NPUCC_RESETS] = {
    { R_BCR + 0, 1 }, { R_BCR + 1, 1 }, { R_BCR + 2, 1 },
    { R_BRANCH + 15, 4 }, { R_BCR + 3, 1 },
    { R_BRANCH + 24, 4 }, { R_BRANCH + 25, 4 },
};

static const char *const input_names[NPUCC_INPUTS] = {
    "bi_tcxo",
    "bi_tcxo_ao",
    "gcc_npu_gpll0_clk_src",
    "gcc_npu_gpll0_div_clk_src",
    "core_bi_pll_test_se",
    "qdss_qmp_clk",
    "npu_llm_temp_clk",
    "npu_llm_curr_clk",
    "npu_s2p_clk",
    "gcc_npu_noc_axi_clk",
    "gcc_npu_noc_dma_clk",
    "npu_cc_noc_ahb_clk_parent",
};
static const char *const pll_names[NPUCC_PLLS] = {
    "npu_cc_pll0", "npu_cc_pll1", "npu_q6ss_pll",
};
static const char *const derived_names[3] = {
    "npu_cc_pll0_out_even", "npu_cc_pll1_out_even", "npu_cc_crc_div",
};
static const char *const bank_names[3] = {
    "cc", "qdsp6ss", "qdsp6ss_pll",
};
static const uint32_t crc_values[4] = { 0x800000, 0x15011, 0x800000, 0x15011 };

const QcomNpuccWindowDef qcom_sm8250_npucc_windows[NPUCC_WINDOWS] = {
    { 0, 0x0, 0x44 },
    { 0, 0x400, 0x44 },
    { 0, 0x1000, 0x4 },
    { 0, 0x1010, 0x8 },
    { 0, 0x1030, 0x4 },
    { 0, 0x1060, 0x8 },
    { 0, 0x1074, 0xc },
    { 0, 0x1090, 0x24 },
    { 0, 0x10b8, 0x4 },
    { 0, 0x10c0, 0x24 },
    { 0, 0x10f0, 0x4 },
    { 0, 0x1100, 0x8 },
    { 0, 0x1110, 0x4 },
    { 0, 0x1130, 0x4 },
    { 0, 0x1140, 0x8 },
    { 0, 0x1150, 0x4 },
    { 0, 0x11a0, 0x8 },
    { 0, 0x11b0, 0x8 },
    { 0, 0x1200, 0x4 },
    { 0, 0x1210, 0x10 },
    { 0, 0x1224, 0x4 },
    { 0, 0x1234, 0x8 },
    { 0, 0x1400, 0x8 },
    { 0, 0x1410, 0x4 },
    { 1, 0x28, 0x8 },
    { 2, 0x0, 0x44 },
};

static int npucc_register(unsigned bank, hwaddr offset)
{
    unsigned i;

    for (i = 0; i < NPUCC_REGISTERS; i++) {
        if (registers[i].bank == bank && registers[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static bool npucc_recipe(QcomSm8250NpuccState *s, unsigned p)
{
    return PREG(s, p, R_CAL_L) == 0x44 &&
           PREG(s, p, R_USER_U) == 0x805 && !PREG(s, p, R_USER_U1) &&
           PREG(s, p, R_CONFIG) == 0x20485699 &&
           PREG(s, p, R_CONFIG_U) == 0x2261 &&
           PREG(s, p, R_CONFIG_U1) == 0x329a699c &&
           !PREG(s, p, R_TEST) && !PREG(s, p, R_TEST_U) &&
           !PREG(s, p, R_TEST_U1);
}

static uint64_t npucc_pll_frequency(QcomSm8250NpuccState *s,
                                    uint32_t l, uint32_t alpha)
{
    uint64_t xo = clock_get_hz(s->input[I_XO]);
    uint64_t factor = ((uint64_t)l << 16) | alpha;
    uint64_t max_numerator = PLL_MAX_HZ << 16;
    uint64_t max_factor, hz;

    if (!l || l > 0xffff || alpha > 0xffff || !xo ||
        xo > UINT64_MAX / factor) {
        return 0;
    }
    /*
     * The driver clamps the nominal round_rate to 2 GHz, then set_rate
     * rounds up to Q16 again, allowing at most its 500 Hz rate margin.
     * Accept that one quantized upper endpoint, not an arbitrary band.
     */
    max_factor = max_numerator / xo + !!(max_numerator % xo);
    hz = xo * factor / 65536;
    return factor <= max_factor && hz >= 249600000 &&
           hz <= PLL_MAX_HZ + PLL_RATE_MARGIN_HZ ? hz : 0;
}

static bool npucc_pll_running(QcomSm8250NpuccState *s, unsigned p)
{
    return s->enabled_reference && (PREG(s, p, R_MODE) & PLL_RESET_N) &&
           PREG(s, p, R_OPMODE) == 1 && npucc_recipe(s, p) &&
           npucc_pll_frequency(s, s->active_l[p], s->active_alpha[p]);
}

static bool npucc_cfg_valid(unsigned r, uint32_t cfg)
{
    return !(cfg & ~RCG_CFG_MASK) && !(cfg & 0x3000) &&
           roots[r].parents[(cfg >> 8) & 7] != NO_PARENT;
}

static bool npucc_crc_configured(QcomSm8250NpuccState *s)
{
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(crc_values); i++) {
        if (s->regs[R_CRC + i] != crc_values[i]) {
            return false;
        }
    }
    return s->enabled_reference;
}

static bool npucc_branch_requested(QcomSm8250NpuccState *s, unsigned b)
{
    return (s->regs[R_BRANCH + b] & 1) || s->remote_vote[b];
}

static void npucc_rates(QcomSm8250NpuccState *s, uint64_t *rates)
{
    bool demand[NPUCC_RCGS];
    unsigned i;

    memset(rates, 0, sizeof(uint64_t) * NPUCC_OUTPUTS);
    if (!s->enabled_reference) {
        return;
    }
    for (i = 0; i < NPUCC_PLLS; i++) {
        if (s->pll_locked[i] && npucc_pll_running(s, i) &&
            (PREG(s, i, R_MODE) & PLL_OUTCTRL) &&
            (PREG(s, i, R_USER) & 7) == 7) {
            rates[i] = npucc_pll_frequency(s, s->active_l[i],
                                            s->active_alpha[i]);
        }
    }
    /* The source's two postdivider tables contain only selector 0 / divide1. */
    if (!(PREG(s, 0, R_USER) & 0xf00)) {
        rates[C_EVEN0] = rates[C_PLL0];
    }
    if (!(PREG(s, 1, R_USER) & 0xf00)) {
        rates[C_EVEN1] = rates[C_PLL1];
    }
    /* CRC setup qualifies only this reference path, never a PLL's lock. */
    if (npucc_crc_configured(s)) {
        rates[C_CRC] = rates[C_EVEN0] / 2;
    }
    for (i = 0; i < NPUCC_RCGS; i++) {
        demand[i] = s->regs[R_CMD(i)] & RCG_ROOT_EN;
    }
    for (i = 0; i < NPUCC_BRANCHES; i++) {
        int parent = branches[i].parent;

        if (npucc_branch_requested(s, i) &&
            parent >= C_RCG && parent < C_BRANCH) {
            demand[parent - C_RCG] = true;
        }
    }
    for (i = 0; i < NPUCC_RCGS; i++) {
        uint32_t cfg = s->active_cfg[i];
        uint32_t div = cfg & 0x1f;
        int source;
        uint64_t parent;

        if (!demand[i] || !npucc_cfg_valid(i, cfg)) {
            continue;
        }
        source = roots[i].parents[(cfg >> 8) & 7];
        parent = source < 0 ? clock_get_hz(s->input[-source - 1]) :
                              rates[source];
        rates[C_RCG + i] = div ? parent / (div + 1) * 2 +
                                (parent % (div + 1)) * 2 / (div + 1) : parent;
    }
    for (i = 0; i < NPUCC_BRANCHES; i++) {
        int parent = branches[i].parent;

        if (npucc_branch_requested(s, i)) {
            rates[C_BRANCH + i] = parent < 0 ?
                clock_get_hz(s->input[-parent - 1]) : rates[parent];
        }
    }
}

static void npucc_reset_outputs(QcomSm8250NpuccState *s)
{
    unsigned i;

    for (i = 0; i < NPUCC_RESETS; i++) {
        qemu_set_irq(s->reset_out[i], s->enabled_reference &&
                     (s->regs[resets[i].reg] & resets[i].mask));
    }
}

static void npucc_publish(QcomSm8250NpuccState *s, bool propagate)
{
    uint64_t rates[NPUCC_OUTPUTS];
    unsigned i;

    npucc_rates(s, rates);
    for (i = 0; i < NPUCC_OUTPUTS; i++) {
        bool changed = clock_set_hz(s->clocks[i], rates[i]);

        if (changed && propagate) {
            clock_propagate(s->clocks[i]);
        }
    }
    if (propagate) {
        npucc_reset_outputs(s);
    }
}

static void npucc_earlier(int64_t *next, int64_t value)
{
    if (value >= 0 && (*next < 0 || value < *next)) {
        *next = value;
    }
}

static void npucc_schedule(QcomSm8250NpuccState *s)
{
    int64_t next = -1;
    unsigned i;

    for (i = 0; i < NPUCC_PLLS; i++) {
        npucc_earlier(&next, s->lock_deadline[i]);
        npucc_earlier(&next, s->latch_deadline[i]);
    }
    for (i = 0; i < NPUCC_RCGS; i++) {
        npucc_earlier(&next, s->rcg_deadline[i]);
    }
    if (next < 0) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static void npucc_update(QcomSm8250NpuccState *s)
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
    for (i = 0; i < NPUCC_PLLS; i++) {
        uint32_t mode = PREG(s, i, R_MODE);

        if (xo_changed) {
            s->pll_locked[i] = false;
            s->lock_deadline[i] = -1;
        }
        if (s->latch_deadline[i] >= 0) {
            if (!(mode & PLL_RESET_N) || !(mode & PLL_UPDATE) ||
                !(mode & PLL_UPDATE_BYPASS) || !npucc_recipe(s, i) ||
                !npucc_pll_frequency(s, s->pending_l[i], s->pending_alpha[i])) {
                s->latch_deadline[i] = -1;
            } else if (now >= s->latch_deadline[i]) {
                s->active_l[i] = s->pending_l[i];
                s->active_alpha[i] = s->pending_alpha[i];
                s->pll_ack[i] = true;
                s->pll_locked[i] = false;
                s->lock_deadline[i] = s->latch_deadline[i] + PLL_LOCK_NS;
                /*
                 * An XO change observed now also needs its own lock interval,
                 * even if an older latch callback is being dispatched late.
                 */
                if (xo_changed) {
                    s->lock_deadline[i] = MAX(s->lock_deadline[i],
                                               now + PLL_LOCK_NS);
                }
                s->latch_deadline[i] = -1;
            }
        }
        if (!npucc_pll_running(s, i)) {
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
    for (i = 0; i < NPUCC_RCGS; i++) {
        if (s->rcg_deadline[i] >= 0 && now >= s->rcg_deadline[i]) {
            s->active_cfg[i] = s->pending_cfg[i];
            s->regs[R_CMD(i)] &= ~RCG_UPDATE;
            s->rcg_deadline[i] = -1;
        }
    }
    npucc_publish(s, true);
    npucc_schedule(s);
    s->updating = false;
}

static void npucc_timer(void *opaque)
{
    npucc_update(opaque);
}

static void npucc_clock_changed(void *opaque, ClockEvent event)
{
    npucc_update(opaque);
}

static void npucc_remote_vote(void *opaque, int index, int level)
{
    QcomSm8250NpuccState *s = opaque;

    s->remote_vote[index] = !!level;
    npucc_update(s);
}

static MemTxResult npucc_read(void *opaque, hwaddr address, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomNpuccWindow *w = opaque;
    QcomSm8250NpuccState *s = w->state;
    int id = npucc_register(w->bank, w->offset + address);
    uint64_t rates[NPUCC_OUTPUTS];

    *value = 0;
    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_NPUCC
                      ": unmodeled read bank %u +0x%" HWADDR_PRIx " size %u\n",
                      w->bank, w->offset + address, size);
        return MEMTX_ERROR;
    }
    npucc_update(s);
    *value = s->regs[id];
    if (id < R_CMD(0) && id % NPUCC_PLL_REGISTERS == R_MODE) {
        unsigned p = id / NPUCC_PLL_REGISTERS;

        *value |= s->pll_locked[p] ? PLL_LOCK : 0;
        *value |= s->pll_ack[p] ? PLL_ACK : 0;
        *value |= s->pll_calibrated[p] ? PLL_PCAL_DONE : 0;
    } else if (id >= R_CMD(0) && id < R_BRANCH &&
               (id - R_CMD(0)) % 2 == 0) {
        npucc_rates(s, rates);
        if (!rates[C_RCG + (id - R_CMD(0)) / 2]) {
            *value |= CLOCK_OFF;
        }
    } else if (id >= R_BRANCH && id < R_BCR) {
        npucc_rates(s, rates);
        if (!rates[C_BRANCH + id - R_BRANCH]) {
            *value |= CLOCK_OFF;
        }
    }
    return MEMTX_OK;
}

static void npucc_start_pll(QcomSm8250NpuccState *s, unsigned p)
{
    s->pll_locked[p] = false;
    s->lock_deadline[p] = -1;
    if ((PREG(s, p, R_MODE) & PLL_RESET_N) &&
        PREG(s, p, R_OPMODE) == 1 && npucc_recipe(s, p) &&
        PREG(s, p, R_L)) {
        s->active_l[p] = PREG(s, p, R_L);
        s->active_alpha[p] = PREG(s, p, R_ALPHA);
    } else {
        s->active_l[p] = s->active_alpha[p] = 0;
    }
}

static bool npucc_user_supported(uint32_t value)
{
    /* Only grouped outputs 0/7 and the source's postdiv selector 0 are known. */
    return value == 0 || value == 7;
}

static MemTxResult npucc_write(void *opaque, hwaddr address, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomNpuccWindow *w = opaque;
    QcomSm8250NpuccState *s = w->state;
    int id = npucc_register(w->bank, w->offset + address);
    uint32_t old, val;

    if (!s->enabled_reference || size != 4 || (address & 3) || id < 0) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_NPUCC
                      ": unmodeled write bank %u +0x%" HWADDR_PRIx " size %u\n",
                      w->bank, w->offset + address, size);
        return MEMTX_ERROR;
    }
    if (value & ~(uint64_t)(registers[id].write_mask |
                             registers[id].status_mask)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_NPUCC
                      ": unsupported bits bank %u +0x%" HWADDR_PRIx "\n",
                      w->bank, w->offset + address);
        return MEMTX_ERROR;
    }
    if (id < R_CMD(0) && id % NPUCC_PLL_REGISTERS == R_USER &&
        !npucc_user_supported(value)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_NPUCC
                      ": unsupported PLL output/postdivider selection\n");
        return MEMTX_ERROR;
    }
    if (id >= R_CRC && value != 0 && value != crc_values[id - R_CRC]) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_NPUCC
                      ": unsupported CRC reference configuration\n");
        return MEMTX_ERROR;
    }
    npucc_update(s);
    old = s->regs[id];
    val = value & registers[id].write_mask;
    s->regs[id] = val;
    if (id < R_CMD(0)) {
        unsigned p = id / NPUCC_PLL_REGISTERS;
        unsigned field = id % NPUCC_PLL_REGISTERS;

        if (field == R_MODE) {
            if (!(val & PLL_RESET_N)) {
                s->pll_locked[p] = s->pll_ack[p] = s->pll_calibrated[p] = false;
                s->active_l[p] = s->active_alpha[p] = 0;
                s->lock_deadline[p] = s->latch_deadline[p] = -1;
            } else if (!(old & PLL_RESET_N)) {
                npucc_start_pll(s, p);
            }
            if (!(val & PLL_UPDATE)) {
                s->pll_ack[p] = false;
                s->latch_deadline[p] = -1;
            } else if (!(old & PLL_UPDATE)) {
                s->pll_ack[p] = false;
                s->latch_deadline[p] = -1;
                if ((val & PLL_RESET_N) && (val & PLL_UPDATE_BYPASS) &&
                    npucc_recipe(s, p) &&
                    npucc_pll_frequency(s, PREG(s, p, R_L),
                                          PREG(s, p, R_ALPHA))) {
                    s->pending_l[p] = PREG(s, p, R_L);
                    s->pending_alpha[p] = PREG(s, p, R_ALPHA);
                    s->latch_deadline[p] =
                        qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + PLL_LATCH_NS;
                }
            }
        } else if (field == R_OPMODE && val != old) {
            npucc_start_pll(s, p);
        }
    } else if (id < R_BRANCH && (id - R_CMD(0)) % 2 == 0) {
        unsigned r = (id - R_CMD(0)) / 2;

        if (!(val & RCG_UPDATE)) {
            s->rcg_deadline[r] = -1;
        } else if (!(old & RCG_UPDATE) &&
                   npucc_cfg_valid(r, s->regs[R_CFG(r)])) {
            s->pending_cfg[r] = s->regs[R_CFG(r)];
            s->rcg_deadline[r] =
                qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + RCG_UPDATE_NS;
        }
    }
    npucc_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps npucc_ops = {
    .read_with_attrs = npucc_read,
    .write_with_attrs = npucc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void npucc_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);
    unsigned i;

    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    for (i = 0; i < NPUCC_PLLS; i++) {
        s->active_l[i] = s->active_alpha[i] = 0;
        s->pending_l[i] = s->pending_alpha[i] = 0;
        s->pll_locked[i] = s->pll_ack[i] = s->pll_calibrated[i] = false;
        s->lock_deadline[i] = s->latch_deadline[i] = -1;
    }
    for (i = 0; i < NPUCC_RCGS; i++) {
        s->active_cfg[i] = s->pending_cfg[i] = 0;
        s->rcg_deadline[i] = -1;
    }
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
    s->updating = false;
    s->ready = false;
    /* Input Clocks and remote votes belong to external providers. */
}

static void npucc_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);

    s->ready = true;
    npucc_publish(s, true);
}

static int npucc_pre_load(void *opaque)
{
    QcomSm8250NpuccState *s = opaque;

    s->ready = false;
    timer_del(s->timer);
    return 0;
}

static int npucc_post_load(void *opaque, int version_id)
{
    QcomSm8250NpuccState *s = opaque;
    unsigned i;

    for (i = 0; i < NPUCC_REGISTERS; i++) {
        if ((s->regs[i] & ~registers[i].write_mask) ||
            (!s->enabled_reference && s->regs[i])) {
            return -EINVAL;
        }
    }
    for (i = 0; i < NPUCC_PLLS; i++) {
        uint32_t mode = PREG(s, i, R_MODE);

        if (!npucc_user_supported(PREG(s, i, R_USER)) ||
            s->active_l[i] > 0xffff || s->active_alpha[i] > 0xffff ||
            s->pending_l[i] > 0xffff || s->pending_alpha[i] > 0xffff ||
            s->lock_deadline[i] < -1 || s->latch_deadline[i] < -1 ||
            (s->pll_locked[i] &&
             (!npucc_pll_running(s, i) || s->lock_deadline[i] >= 0 ||
              !s->pll_calibrated[i])) ||
            (s->lock_deadline[i] >= 0 && !npucc_pll_running(s, i)) ||
            (s->pll_calibrated[i] && !(mode & PLL_RESET_N)) ||
            (s->pll_ack[i] && (!(mode & PLL_UPDATE) || !(mode & PLL_RESET_N))) ||
            (s->latch_deadline[i] >= 0 &&
             (!(mode & PLL_UPDATE) || s->pll_ack[i] ||
              !(mode & PLL_RESET_N) || !(mode & PLL_UPDATE_BYPASS) ||
              !s->pending_l[i]))) {
            return -EINVAL;
        }
        if (!s->enabled_reference &&
            (s->active_l[i] || s->active_alpha[i] || s->pending_l[i] ||
             s->pending_alpha[i] || s->pll_locked[i] || s->pll_ack[i] ||
             s->pll_calibrated[i] || s->lock_deadline[i] >= 0 ||
             s->latch_deadline[i] >= 0)) {
            return -EINVAL;
        }
    }
    for (i = 0; i < NPUCC_RCGS; i++) {
        if (!npucc_cfg_valid(i, s->active_cfg[i]) ||
            !npucc_cfg_valid(i, s->pending_cfg[i]) ||
            s->rcg_deadline[i] < -1 ||
            (s->rcg_deadline[i] >= 0 &&
             !(s->regs[R_CMD(i)] & RCG_UPDATE)) ||
            (!s->enabled_reference &&
             (s->active_cfg[i] || s->pending_cfg[i] || s->rcg_deadline[i] >= 0))) {
            return -EINVAL;
        }
    }
    for (i = 0; i < ARRAY_SIZE(crc_values); i++) {
        if (s->regs[R_CRC + i] && s->regs[R_CRC + i] != crc_values[i]) {
            return -EINVAL;
        }
    }
    s->updating = false;
    s->ready = true;
    /* No time advancement, new deadlines or Clock callbacks while loading. */
    npucc_publish(s, false);
    npucc_reset_outputs(s);
    npucc_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_npucc = {
    .name = TYPE_QCOM_SM8250_NPUCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = npucc_pre_load,
    .post_load = npucc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250NpuccState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250NpuccState, NPUCC_REGISTERS),
        VMSTATE_UINT32_ARRAY(active_l, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_UINT32_ARRAY(active_alpha, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_UINT32_ARRAY(pending_l, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_UINT32_ARRAY(pending_alpha, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_UINT32_ARRAY(active_cfg, QcomSm8250NpuccState, NPUCC_RCGS),
        VMSTATE_UINT32_ARRAY(pending_cfg, QcomSm8250NpuccState, NPUCC_RCGS),
        VMSTATE_INT64_ARRAY(lock_deadline, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_INT64_ARRAY(latch_deadline, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_INT64_ARRAY(rcg_deadline, QcomSm8250NpuccState, NPUCC_RCGS),
        VMSTATE_UINT64(last_xo_hz, QcomSm8250NpuccState),
        VMSTATE_BOOL_ARRAY(pll_locked, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_BOOL_ARRAY(pll_ack, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_BOOL_ARRAY(pll_calibrated, QcomSm8250NpuccState, NPUCC_PLLS),
        VMSTATE_BOOL_ARRAY(remote_vote, QcomSm8250NpuccState, NPUCC_BRANCHES),
        VMSTATE_ARRAY_CLOCK(input, QcomSm8250NpuccState, NPUCC_INPUTS),
        VMSTATE_END_OF_LIST()
    },
};

static void npucc_fixture_get(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    uint32_t hz = clock_get_hz(opaque);

    visit_type_uint32(v, name, &hz, errp);
}

static void npucc_fixture_set(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);
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
    npucc_update(s);
}

static void npucc_rate_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);
    uint64_t rates[NPUCC_OUTPUTS];

    npucc_rates(s, rates);
    visit_type_uint64(v, name, &rates[GPOINTER_TO_UINT(opaque)], errp);
}

static void npucc_active_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);
    unsigned id = GPOINTER_TO_UINT(opaque);
    uint32_t value;

    if (id < 2 * NPUCC_PLLS) {
        value = id & 1 ? s->active_alpha[id / 2] : s->active_l[id / 2];
    } else {
        value = s->active_cfg[id - 2 * NPUCC_PLLS];
    }
    visit_type_uint32(v, name, &value, errp);
}

static void npucc_status_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);
    unsigned id = GPOINTER_TO_UINT(opaque);
    bool value;

    if (id < 5 * NPUCC_PLLS) {
        unsigned p = id / 5;
        bool flags[] = {
            s->pll_locked[p], s->pll_ack[p], s->pll_calibrated[p],
            s->lock_deadline[p] >= 0, s->latch_deadline[p] >= 0,
        };
        value = flags[id % 5];
    } else if (id < 5 * NPUCC_PLLS + NPUCC_RCGS) {
        value = s->rcg_deadline[id - 5 * NPUCC_PLLS] >= 0;
    } else {
        value = npucc_crc_configured(s);
    }
    visit_type_bool(v, name, &value, errp);
}

static void npucc_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(dev);

    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->ready = true;
    s->last_xo_hz = clock_get_hz(s->input[I_XO]);
}

static void npucc_init(Object *obj)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);
    DeviceState *dev = DEVICE(obj);
    static const char *const statuses[] = {
        "locked", "ack", "calibrated", "lock-pending", "latch-pending",
    };
    static const char *const active[] = { "active-l", "active-alpha" };
    unsigned i, j;

    QEMU_BUILD_BUG_ON(R_CRC + 4 != NPUCC_REGISTERS);
    QEMU_BUILD_BUG_ON(C_BRANCH + NPUCC_BRANCHES != NPUCC_OUTPUTS);
    for (i = 0; i < NPUCC_WINDOWS; i++) {
        const QcomNpuccWindowDef *d = &qcom_sm8250_npucc_windows[i];
        g_autofree char *name = g_strdup_printf("npucc-%s-%x",
                                                bank_names[d->bank], d->offset);

        s->windows[i].state = s;
        s->windows[i].bank = d->bank;
        s->windows[i].offset = d->offset;
        memory_region_init_io(&s->iomem[i], obj, &npucc_ops, &s->windows[i],
                              name, d->size);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem[i]);
    }
    for (i = 0; i < NPUCC_INPUTS; i++) {
        s->input[i] = qdev_init_clock_in(dev, input_names[i],
                                        npucc_clock_changed, s, ClockUpdate);
        if (qtest_enabled()) {
            g_autofree char *name = g_strdup_printf("qtest-%s-hz", input_names[i]);

            object_property_add(obj, name, "uint32", npucc_fixture_get,
                                npucc_fixture_set, NULL, s->input[i]);
        }
    }
    for (i = 0; i < NPUCC_OUTPUTS; i++) {
        const char *name;
        g_autofree char *property = NULL;

        if (i < 3) {
            name = pll_names[i];
        } else if (i < C_RCG) {
            name = derived_names[i - 3];
        } else if (i < C_BRANCH) {
            name = roots[i - C_RCG].name;
        } else {
            name = branches[i - C_BRANCH].name;
        }
        property = g_strdup_printf("clk.%s.hz", name);
        s->clocks[i] = qdev_init_clock_out(dev, name);
        object_property_add(obj, property, "uint64", npucc_rate_get, NULL,
                            NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < NPUCC_PLLS; i++) {
        s->lock_deadline[i] = s->latch_deadline[i] = -1;
        for (j = 0; j < ARRAY_SIZE(statuses); j++) {
            g_autofree char *name = g_strdup_printf("pll%u-%s", i, statuses[j]);

            object_property_add(obj, name, "bool", npucc_status_get,
                                NULL, NULL, GUINT_TO_POINTER(i * 5 + j));
        }
        for (j = 0; j < ARRAY_SIZE(active); j++) {
            g_autofree char *name = g_strdup_printf("pll%u-%s", i, active[j]);

            object_property_add(obj, name, "uint32", npucc_active_get,
                                NULL, NULL, GUINT_TO_POINTER(i * 2 + j));
        }
    }
    for (i = 0; i < NPUCC_RCGS; i++) {
        g_autofree char *pending = g_strdup_printf("rcg%u-pending", i);
        g_autofree char *active_cfg = g_strdup_printf("rcg%u-active-cfg", i);

        s->rcg_deadline[i] = -1;
        object_property_add(obj, pending, "bool", npucc_status_get, NULL, NULL,
                            GUINT_TO_POINTER(5 * NPUCC_PLLS + i));
        object_property_add(obj, active_cfg, "uint32", npucc_active_get, NULL,
                            NULL, GUINT_TO_POINTER(2 * NPUCC_PLLS + i));
    }
    object_property_add(obj, "crc-reference-configured", "bool",
                        npucc_status_get, NULL, NULL,
                        GUINT_TO_POINTER(5 * NPUCC_PLLS + NPUCC_RCGS));
    qdev_init_gpio_in_named(dev, npucc_remote_vote, "remote-vote", NPUCC_BRANCHES);
    qdev_init_gpio_out_named(dev, s->reset_out, "reset", NPUCC_RESETS);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, npucc_timer, s);
}

static void npucc_finalize(Object *obj)
{
    QcomSm8250NpuccState *s = QCOM_SM8250_NPUCC(obj);

    timer_free(s->timer);
}

static const Property npucc_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250NpuccState,
                     enabled_reference, false),
};

static void npucc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm SM8250/Kona v2 NPUCC digital reference model";
    dc->realize = npucc_realize;
    dc->vmsd = &vmstate_npucc;
    device_class_set_props(dc, npucc_properties);
    rc->phases.enter = npucc_reset_enter;
    rc->phases.hold = npucc_reset_hold;
}

static const TypeInfo npucc_info = {
    .name = TYPE_QCOM_SM8250_NPUCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250NpuccState),
    .instance_init = npucc_init,
    .instance_finalize = npucc_finalize,
    .class_init = npucc_class_init,
};

static void npucc_register_types(void)
{
    type_register_static(&npucc_info);
}
type_init(npucc_register_types)
