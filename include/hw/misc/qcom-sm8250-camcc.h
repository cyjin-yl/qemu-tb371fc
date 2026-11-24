/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_CAMCC_H
#define HW_MISC_QCOM_SM8250_CAMCC_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_CAMCC "qcom-sm8250-camcc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250CamccState, QCOM_SM8250_CAMCC)

#define CAMCC_REGISTERS 236
#define CAMCC_CLOCKS 111
#define CAMCC_INPUTS 5
#define CAMCC_PLLS 5
#define CAMCC_POSTDIVS 6
#define CAMCC_RCGS 33
#define CAMCC_BRANCHES 66
#define CAMCC_RESETS 6
#define CAMCC_WINDOWS 74
#define CAMCC_MAPPED_BYTES 944
#define CAMCC_PHYSICAL_BASE UINT64_C(0x0ad00000)
#define CAMCC_REFERENCE_IDENTITY 0x43414d32U

typedef struct QcomCamccWindowDef {
    uint32_t offset, size;
} QcomCamccWindowDef;

extern const QcomCamccWindowDef qcom_sm8250_camcc_windows[CAMCC_WINDOWS];

typedef struct QcomCamccWindow {
    QcomSm8250CamccState *state;
    uint32_t offset;
} QcomCamccWindow;

struct QcomSm8250CamccState {
    SysBusDevice parent_obj;
    MemoryRegion container;
    MemoryRegion iomem[CAMCC_WINDOWS];
    QcomCamccWindow windows[CAMCC_WINDOWS];
    Clock *input[CAMCC_INPUTS];
    Clock *clocks[CAMCC_CLOCKS];
    qemu_irq reset_out[CAMCC_RESETS];
    QEMUTimer *timer;

    bool enabled_reference;
    bool ready, updating;
    uint32_t reference_profile, reference_identity;
    uint32_t register_count, clock_count, input_count, pll_count;
    uint32_t rcg_count, branch_count, reset_count, window_count, mapped_bytes;
    uint64_t physical_base;
    uint32_t regs[CAMCC_REGISTERS];
    uint32_t active_l[CAMCC_PLLS], active_alpha[CAMCC_PLLS];
    uint32_t pending_l[CAMCC_PLLS], pending_alpha[CAMCC_PLLS];
    uint32_t active_cfg[CAMCC_RCGS], active_m[CAMCC_RCGS];
    uint32_t active_n[CAMCC_RCGS], active_d[CAMCC_RCGS];
    uint32_t pending_cfg[CAMCC_RCGS], pending_m[CAMCC_RCGS];
    uint32_t pending_n[CAMCC_RCGS], pending_d[CAMCC_RCGS];
    int64_t lock_deadline[CAMCC_PLLS], latch_deadline[CAMCC_PLLS];
    int64_t bypass_since[CAMCC_PLLS];
    int64_t rcg_deadline[CAMCC_RCGS];
    uint64_t last_xo_hz;
    bool pll_locked[CAMCC_PLLS], pll_ack[CAMCC_PLLS];
    bool pll_calibrated[CAMCC_PLLS];
};

#endif
