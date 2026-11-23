/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_GPUCC_H
#define HW_MISC_QCOM_SM8250_GPUCC_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_GPUCC "qcom-sm8250-gpucc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250GpuccState, QCOM_SM8250_GPUCC)

#define GPUCC_WINDOWS 7
#define GPUCC_REGISTERS 36
#define GPUCC_INPUTS 15
#define GPUCC_BRANCHES 15
#define GPUCC_OUTPUTS (GPUCC_BRANCHES + 2)
#define GPUCC_RESETS 5

extern const hwaddr qcom_sm8250_gpucc_window_offsets[GPUCC_WINDOWS];

typedef struct QcomGpuccWindow {
    QcomSm8250GpuccState *state;
    uint32_t offset;
} QcomGpuccWindow;

struct QcomSm8250GpuccState {
    SysBusDevice parent_obj;
    MemoryRegion iomem[GPUCC_WINDOWS];
    QcomGpuccWindow windows[GPUCC_WINDOWS];
    Clock *input[GPUCC_INPUTS];
    Clock *clocks[GPUCC_OUTPUTS];
    qemu_irq reset_out[GPUCC_RESETS];
    QEMUTimer *timer;

    bool enabled_reference;
    uint32_t reference_profile;
    bool ready;
    bool updating;
    uint32_t regs[GPUCC_REGISTERS];
    uint32_t active_l;
    uint32_t active_alpha;
    uint32_t active_cfg;
    uint32_t pending_l;
    uint32_t pending_alpha;
    uint32_t pending_cfg;
    int64_t lock_deadline;
    int64_t latch_deadline;
    int64_t rcg_deadline;
    uint64_t last_xo_hz;
    bool pll_locked;
    bool pll_ack;
    bool pll_calibrated;
    bool remote_vote[GPUCC_BRANCHES];
};

#endif
