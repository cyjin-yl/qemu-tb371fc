/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_QCOM_SM8250_LLCC_H
#define HW_QCOM_SM8250_LLCC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_QCOM_SM8250_LLCC "qcom-sm8250-llcc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250LlccState, QCOM_SM8250_LLCC)

#define LLCC_BANK_COUNT 4
#define LLCC_SLICES 16
#define LLCC_REGISTERS 71
#define LLCC_WINDOWS 139
#define LLCC_REGIONS 2
#define LLCC_MAPPED_WORDS 349
#define LLCC_MAPPED_BYTES (LLCC_MAPPED_WORDS * 4)
#define LLCC_BANK_STRIDE 0x80000
#define LLCC_BANK_BYTES 0x50000
#define LLCC_BANKS_REGION_BYTES 0x1d0000
#define LLCC_TRANSITION_NS 1000
#define LLCC_REFERENCE_ID UINT32_C(0x4c4c4332)
#define LLCC_VMSTATE_BYTES 783

enum {
    LLCC_REG_CTRL,
    LLCC_REG_STATUS,
    LLCC_REG_ATTR0,
    LLCC_REG_ATTR1,
    LLCC_REG_CAD,
    LLCC_REG_PCB,
    LLCC_REG_WRSC,
    LLCC_REG_HW_INFO,
    LLCC_REG_COMMON_STATUS,
    LLCC_REG_TRP_ERROR,
    LLCC_REG_DRP_ERROR,
};

typedef struct LlccSliceDef {
    const char *name;
    uint8_t sid;
    uint32_t attr0;
    uint32_t attr1;
    uint8_t write_scid;
    uint8_t dis_cap;
    uint8_t retain;
    uint8_t activate_init;
} LlccSliceDef;

typedef struct LlccRegDef {
    const char *name;
    uint32_t offset;
    uint32_t write_mask;
    uint8_t kind;
    uint8_t slice;
} LlccRegDef;

typedef struct LlccWindowDef {
    uint8_t region;
    uint8_t bank;
    uint32_t offset;
    uint32_t size;
} LlccWindowDef;

extern const LlccWindowDef llcc_windows[LLCC_WINDOWS];
extern const uint64_t qcom_sm8250_llcc_bases[LLCC_REGIONS];

#endif
