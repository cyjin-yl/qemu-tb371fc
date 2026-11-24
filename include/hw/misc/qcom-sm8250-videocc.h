/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_VIDEOCC_H
#define HW_MISC_QCOM_SM8250_VIDEOCC_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_VIDEOCC "qcom-sm8250-videocc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250VideoccState, QCOM_SM8250_VIDEOCC)

#define VIDEOCC_REGISTERS 55
#define VIDEOCC_CLOCKS 19
#define VIDEOCC_INPUTS 4
#define VIDEOCC_PLLS 2
#define VIDEOCC_DIVIDERS 4
#define VIDEOCC_RCGS 5
#define VIDEOCC_BRANCHES 8
#define VIDEOCC_RESETS 7
#define VIDEOCC_REMOTE_VOTES 4
#define VIDEOCC_WINDOWS 26
#define VIDEOCC_MAPPED_BYTES 220
#define VIDEOCC_PHYSICAL_BASE UINT64_C(0x0abf0000)
#define VIDEOCC_REFERENCE_IDENTITY 0x56494432U

typedef struct QcomVideoccWindowDef {
    uint32_t offset, size;
} QcomVideoccWindowDef;

extern const QcomVideoccWindowDef qcom_sm8250_videocc_windows[VIDEOCC_WINDOWS];

typedef struct QcomVideoccWindow {
    QcomSm8250VideoccState *state;
    uint32_t offset;
} QcomVideoccWindow;

struct QcomSm8250VideoccState {
    SysBusDevice parent_obj;
    MemoryRegion container;
    MemoryRegion iomem[VIDEOCC_WINDOWS];
    QcomVideoccWindow windows[VIDEOCC_WINDOWS];
    Clock *input[VIDEOCC_INPUTS];
    Clock *clocks[VIDEOCC_CLOCKS];
    qemu_irq reset_out[VIDEOCC_RESETS];
    QEMUTimer *timer;

    bool enabled_reference;
    bool ready, updating;
    uint32_t reference_profile, reference_identity;
    uint32_t register_count, clock_count, input_count, pll_count;
    uint32_t rcg_count, branch_count, divider_count, reset_count;
    uint32_t remote_vote_count, window_count, mapped_bytes;
    uint64_t physical_base;
    uint32_t regs[VIDEOCC_REGISTERS];
    uint32_t active_l[VIDEOCC_PLLS], active_alpha[VIDEOCC_PLLS];
    uint32_t pending_l[VIDEOCC_PLLS], pending_alpha[VIDEOCC_PLLS];
    uint32_t active_cfg[VIDEOCC_RCGS], pending_cfg[VIDEOCC_RCGS];
    int64_t lock_deadline[VIDEOCC_PLLS], latch_deadline[VIDEOCC_PLLS];
    int64_t rcg_deadline[VIDEOCC_RCGS];
    uint64_t last_xo_hz;
    bool pll_locked[VIDEOCC_PLLS], pll_ack[VIDEOCC_PLLS];
    bool pll_calibrated[VIDEOCC_PLLS];
    bool remote_vote[VIDEOCC_REMOTE_VOTES];
};

#endif
