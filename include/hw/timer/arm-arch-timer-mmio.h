/*
 * Memory-mapped Arm Generic Timer.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Register layout: Arm ARM DDI 0406C.d, Appendix D5.
 *
 * QOM:
 *  num-frames: 1..8 CNTBase frames (default 1)
 *  frequency: counter input frequency in Hz (default 19200000)
 *  exact-frequency: rational ticks/ns conversion (default false)
 *  virtual-frame-mask: frames with virtual timers (default bit 0)
 *  el0-frame-mask: frames with a second CNTEL0Base view (default bit 0)
 *  reset-ns-access: firmware-initialized CNTNSAR mask (default 0)
 *
 * MMIO 0: CNTCTLBase, 4 KiB.
 * MMIO 1 + 2*n: CNTBase<n>, 4 KiB.
 * MMIO 2 + 2*n: CNTEL0Base<n>, 4 KiB (map only if implemented).
 * IRQ 2*n: physical timer. IRQ 2*n + 1: virtual timer.
 *
 * These are independent timer channels, not aliases of a CPU's timer
 * registers. The time origin matches the Arm CPU generic timer. The board
 * must select matching CPU cntfrq-exact and device exact-frequency modes.
 */
#ifndef HW_TIMER_ARM_ARCH_TIMER_MMIO_H
#define HW_TIMER_ARM_ARCH_TIMER_MMIO_H

#include "hw/core/sysbus.h"
#include "qemu/timer.h"
#include "qom/object.h"

#define TYPE_ARM_ARCH_TIMER_MMIO "arm-arch-timer-mmio"
OBJECT_DECLARE_SIMPLE_TYPE(ArmArchTimerMmioState, ARM_ARCH_TIMER_MMIO)

#define ARM_ARCH_TIMER_MMIO_MAX_FRAMES 8

typedef struct ArmArchTimerMmioFrame ArmArchTimerMmioFrame;

typedef struct ArmArchTimerMmioView {
    MemoryRegion mr;
    ArmArchTimerMmioFrame *frame;
    bool el0;
} ArmArchTimerMmioView;

struct ArmArchTimerMmioFrame {
    ArmArchTimerMmioState *parent;
    unsigned int index;
    ArmArchTimerMmioView view[2];
    qemu_irq irq[2];
    QEMUTimer timer[2];
    uint32_t cntacr;
    uint32_t el0acr;
    uint64_t voff;
    uint64_t cval[2];
    uint32_t ctl[2];
};

struct ArmArchTimerMmioState {
    SysBusDevice parent_obj;
    MemoryRegion control;
    ArmArchTimerMmioFrame frame[ARM_ARCH_TIMER_MMIO_MAX_FRAMES];
    uint64_t frequency;
    uint32_t num_frames;
    uint8_t virtual_frame_mask;
    uint8_t el0_frame_mask;
    uint8_t reset_ns_access;
    uint32_t period_ns;
    bool exact_frequency;
    bool exact_frequency_received;
    uint32_t cntfrq;
    uint32_t cntnsar;
};

#endif
