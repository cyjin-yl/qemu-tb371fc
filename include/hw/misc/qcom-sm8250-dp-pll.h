/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_QCOM_SM8250_DP_PLL_H
#define HW_QCOM_SM8250_DP_PLL_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_QCOM_SM8250_DP_PLL "qcom-sm8250-dp-pll"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250DpPllState, QCOM_SM8250_DP_PLL)

#define DP_PLL_REGIONS 4
#define DP_PLL_REGISTERS 73
#define DP_PLL_WINDOWS 42
#define DP_PLL_MAPPED_BYTES (DP_PLL_REGISTERS * 4)
#define DP_PLL_REGION_BYTES 0x200
#define DP_PLL_CLOCKS 6
#define DP_PLL_INPUTS 1
#define DP_PLL_REFERENCE_ID UINT32_C(0x44503731)
#define DP_PLL_CCF_RATE_SCALE 1000
#define DP_PLL_REFERENCE_CYCLES 64
#define DP_PLL_VMSTATE_BYTES 791

enum {
    DP_REGION_PLL,
    DP_REGION_PHY,
    DP_REGION_TX0,
    DP_REGION_TX1,
};

enum {
    DP_REG_CONFIG,
    DP_REG_PLL_STATUS,
    DP_REG_PHY_STATUS,
};

enum {
    DP_RULE_NONE,
    DP_RULE_FIXED,
    DP_RULE_RATE,
    DP_RULE_MODE,
    DP_RULE_POWER,
    DP_RULE_SPARE,
};

typedef struct DpPllRegDef {
    const char *name;
    uint16_t offset;
    uint8_t region;
    uint8_t kind;
    uint8_t rule;
    bool required;
    uint32_t write_mask;
    uint32_t value[4];
} DpPllRegDef;

typedef struct DpPllWindowDef {
    uint8_t region;
    uint16_t offset;
    uint16_t size;
} DpPllWindowDef;

extern const DpPllWindowDef dp_pll_windows[DP_PLL_WINDOWS];
extern const uint64_t qcom_sm8250_dp_pll_bases[DP_PLL_REGIONS];

#endif
