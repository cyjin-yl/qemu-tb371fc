/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_DISPCC_H
#define HW_MISC_QCOM_SM8250_DISPCC_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_DISPCC "qcom-sm8250-dispcc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250DispccState, QCOM_SM8250_DISPCC)

#define DISPCC_REGISTERS 130
#define DISPCC_CLOCKS 61
#define DISPCC_INPUTS 17
#define DISPCC_PLLS 2
#define DISPCC_DIVIDERS 5
#define DISPCC_RCGS 22
#define DISPCC_BRANCHES 32
#define DISPCC_RESETS 2
#define DISPCC_REMOTE_VOTES 2
#define DISPCC_WINDOWS 39
#define DISPCC_MAPPED_BYTES 520
#define DISPCC_PHYSICAL_BASE UINT64_C(0x0af00000)
#define DISPCC_REFERENCE_IDENTITY 0x44495331U

typedef struct QcomDispccWindowDef {
    uint32_t offset, size;
} QcomDispccWindowDef;

extern const QcomDispccWindowDef qcom_sm8250_dispcc_windows[DISPCC_WINDOWS];

typedef struct QcomDispccWindow {
    QcomSm8250DispccState *state;
    uint32_t offset;
} QcomDispccWindow;

struct QcomSm8250DispccState {
    SysBusDevice parent_obj;
    MemoryRegion container;
    MemoryRegion iomem[DISPCC_WINDOWS];
    QcomDispccWindow windows[DISPCC_WINDOWS];
    Clock *input[DISPCC_INPUTS];
    Clock *clocks[DISPCC_CLOCKS];
    qemu_irq reset_out[DISPCC_RESETS];
    QEMUTimer *timer;

    bool enabled_reference;
    bool ready, updating;
    uint32_t reference_profile, reference_identity;
    uint32_t register_count, clock_count, input_count, pll_count;
    uint32_t rcg_count, branch_count, divider_count, reset_count;
    uint32_t remote_vote_count, window_count, mapped_bytes;
    uint64_t physical_base;
    uint32_t regs[DISPCC_REGISTERS];
    uint32_t active_l[DISPCC_PLLS], active_alpha[DISPCC_PLLS];
    uint32_t pending_l[DISPCC_PLLS], pending_alpha[DISPCC_PLLS];
    uint32_t active_cfg[DISPCC_RCGS], active_m[DISPCC_RCGS];
    uint32_t active_n[DISPCC_RCGS], active_d[DISPCC_RCGS];
    uint32_t pending_cfg[DISPCC_RCGS], pending_m[DISPCC_RCGS];
    uint32_t pending_n[DISPCC_RCGS], pending_d[DISPCC_RCGS];
    int64_t lock_deadline[DISPCC_PLLS], latch_deadline[DISPCC_PLLS];
    int64_t rcg_deadline[DISPCC_RCGS];
    uint64_t last_xo_hz;
    bool pll_locked[DISPCC_PLLS], pll_ack[DISPCC_PLLS];
    bool pll_calibrated[DISPCC_PLLS];
    bool remote_vote[DISPCC_REMOTE_VOTES];
};

#endif
