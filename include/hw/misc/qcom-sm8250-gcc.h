/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_GCC_H
#define HW_MISC_QCOM_SM8250_GCC_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_GCC "qcom-sm8250-gcc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250GccState, QCOM_SM8250_GCC)

#define GCC_CLOCKS 200
/* Slot 63 remains for v1 migration; SYS_NOC_CPUSS now has a known parent. */
#define GCC_EXTERNAL_PARENTS 84
#define GCC_RESETS 45
#define GCC_POWER_DOMAINS 11
#define GCC_REGISTER_WORDS (0x9c104 / 4)
#define SM8250_GCC_MMIO_SIZE 0x1f0000

struct QcomSm8250GccState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    Clock *xo;
    Clock *sleep;
    Clock *audio;
    Clock *external[GCC_EXTERNAL_PARENTS];
    Clock *clocks[GCC_CLOCKS];
    qemu_irq reset_out[GCC_RESETS];
    qemu_irq power_out[GCC_POWER_DOMAINS];
    QEMUTimer *timer;
    uint32_t regs[GCC_REGISTER_WORDS];
    uint32_t active_cfg[GCC_CLOCKS];
    uint32_t active_m[GCC_CLOCKS];
    uint32_t active_n[GCC_CLOCKS];
    uint32_t active_d[GCC_CLOCKS];
    int64_t clock_deadline[GCC_CLOCKS];
    int64_t power_deadline[GCC_POWER_DOMAINS];
    int64_t pll_latch_deadline[4];
    bool pll_locked[4];
    bool pll_ack[4];
    bool power_on[GCC_POWER_DOMAINS];
    bool remote_vote[GCC_CLOCKS];
    bool hw_request[GCC_CLOCKS];
    bool boot_configured;
    bool ready;
    bool updating;
};

#endif
