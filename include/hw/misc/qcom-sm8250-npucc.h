/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_NPUCC_H
#define HW_MISC_QCOM_SM8250_NPUCC_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_NPUCC "qcom-sm8250-npucc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250NpuccState, QCOM_SM8250_NPUCC)

#define NPUCC_WINDOWS 26
#define NPUCC_REGISTERS 95
#define NPUCC_INPUTS 12
#define NPUCC_PLLS 3
#define NPUCC_RCGS 6
#define NPUCC_BRANCHES 33
#define NPUCC_OUTPUTS 45
#define NPUCC_RESETS 7
#define NPUCC_PLL_REGISTERS 14

typedef struct QcomNpuccWindowDef {
    uint32_t bank, offset, size;
} QcomNpuccWindowDef;

extern const QcomNpuccWindowDef qcom_sm8250_npucc_windows[NPUCC_WINDOWS];

typedef struct QcomNpuccWindow {
    QcomSm8250NpuccState *state;
    uint32_t bank, offset;
} QcomNpuccWindow;

struct QcomSm8250NpuccState {
    SysBusDevice parent_obj;
    MemoryRegion iomem[NPUCC_WINDOWS];
    QcomNpuccWindow windows[NPUCC_WINDOWS];
    Clock *input[NPUCC_INPUTS];
    Clock *clocks[NPUCC_OUTPUTS];
    qemu_irq reset_out[NPUCC_RESETS];
    QEMUTimer *timer;

    bool enabled_reference;
    uint32_t reference_profile;
    bool ready, updating;
    uint32_t regs[NPUCC_REGISTERS];
    uint32_t active_l[NPUCC_PLLS], active_alpha[NPUCC_PLLS];
    uint32_t pending_l[NPUCC_PLLS], pending_alpha[NPUCC_PLLS];
    uint32_t active_cfg[NPUCC_RCGS], pending_cfg[NPUCC_RCGS];
    int64_t lock_deadline[NPUCC_PLLS], latch_deadline[NPUCC_PLLS];
    int64_t rcg_deadline[NPUCC_RCGS];
    uint64_t last_xo_hz;
    bool pll_locked[NPUCC_PLLS], pll_ack[NPUCC_PLLS];
    bool pll_calibrated[NPUCC_PLLS];
    bool remote_vote[NPUCC_BRANCHES];
};

#endif
