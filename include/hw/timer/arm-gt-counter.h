/*
 * Arm Generic Timer counter and deadline conversion.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_TIMER_ARM_GT_COUNTER_H
#define HW_TIMER_ARM_GT_COUNTER_H

#include "qemu/host-utils.h"
#include "qemu/timer.h"

/*
 * Exact mode requires 0 < frequency <= 1 GHz, checked at realization.
 * The time origin is QEMU_CLOCK_VIRTUAL zero. CNTFRQ is a software
 * annotation and must never be passed here instead of the clock property.
 */
static inline uint64_t arm_gt_ns_to_ticks(uint64_t ns, uint64_t frequency,
                                         bool exact)
{
    if (exact) {
        return muldiv64(ns, frequency, NANOSECONDS_PER_SECOND);
    }
    return ns / (NANOSECONDS_PER_SECOND > frequency ?
                 NANOSECONDS_PER_SECOND / frequency : 1);
}

/*
 * First virtual nanosecond at which the count reaches ticks. Rounding up
 * is essential: a rounded-down expiry may repeatedly re-arm in the past.
 * A deadline beyond QEMUTimer's signed range is saturated, as in the
 * legacy Arm CPU timer. Intermediate products use muldiv64's wide math.
 */
static inline int64_t arm_gt_ticks_to_ns(uint64_t ticks, uint64_t frequency,
                                         bool exact)
{
    uint64_t ns, max_ticks, remainder;
    if (!exact) {
        uint64_t period = NANOSECONDS_PER_SECOND > frequency ?
                          NANOSECONDS_PER_SECOND / frequency : 1;
        return ticks > INT64_MAX / period ? INT64_MAX : ticks * period;
    }
    max_ticks = muldiv64(INT64_MAX, frequency, NANOSECONDS_PER_SECOND);
    if (ticks > max_ticks) {
        return INT64_MAX;
    }
    ns = muldiv64(ticks, NANOSECONDS_PER_SECOND, frequency);
    /* frequency <= 1 GHz, so this remainder multiplication cannot overflow. */
    remainder = ((ticks % frequency) * NANOSECONDS_PER_SECOND) % frequency;
    return ns + (remainder != 0);
}

#endif
