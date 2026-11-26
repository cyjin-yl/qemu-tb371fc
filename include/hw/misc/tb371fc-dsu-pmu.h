/* TB371FC opt-in shared DSU PMU reference model. */
/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_TB371FC_DSU_PMU_H
#define HW_MISC_TB371FC_DSU_PMU_H

#include "qemu/typedefs.h"

#define TYPE_TB371FC_DSU_PMU "tb371fc-dsu-pmu"
#define TB371FC_DSU_PROFILE 1
#define TB371FC_DSU_WIRE_MAGIC UINT32_C(0x44535531)
#define TB371FC_DSU_CPU_MASK UINT32_C(0xff)
#define TB371FC_DSU_IRQ_INTID 82
#define TB371FC_DSU_EVENT_COUNTERS 6
#define TB371FC_DSU_COUNTER_MASK UINT32_C(0x8000003f)
#define TB371FC_DSU_EVENT_TYPE_MASK UINT32_C(0xa000ffff)
#define TB371FC_DSU_PMCR_FIXED UINT32_C(0x41413040)
#define TB371FC_DSU_CEID0 UINT32_C(0x00020000)
#define TB371FC_DSU_ACTLR_WRITE_ENABLE (UINT64_C(1) << 12)
#define TB371FC_DSU_DEFAULT_HZ UINT64_C(100000000)
#define TB371FC_DSU_MAX_HZ UINT64_C(1000000000)
#define TB371FC_DSU_WIRE_BYTES 108

/* Exact AArch64 S3_0_C15_<CRm>_<op2> encodings; no wildcard bank. */
enum {
    TB371FC_DSU_PMCR = (5 << 3) | 0,
    TB371FC_DSU_CNTENSET = (5 << 3) | 1,
    TB371FC_DSU_CNTENCLR = (5 << 3) | 2,
    TB371FC_DSU_OVSSET = (5 << 3) | 3,
    TB371FC_DSU_OVSCLR = (5 << 3) | 4,
    TB371FC_DSU_SELR = (5 << 3) | 5,
    TB371FC_DSU_INTENSET = (5 << 3) | 6,
    TB371FC_DSU_INTENCLR = (5 << 3) | 7,
    TB371FC_DSU_CCNTR = (6 << 3) | 0,
    TB371FC_DSU_XEVTYPER = (6 << 3) | 1,
    TB371FC_DSU_XEVCNTR = (6 << 3) | 2,
    TB371FC_DSU_CEID0_REG = (6 << 3) | 4,
    TB371FC_DSU_CEID1_REG = (6 << 3) | 5,
};

/*
 * Explicit field-by-field VMState, not a packed host structure. CPU0 owns
 * its sole copy in cpu/tb371fc-dsu-pmu. All other CPUs carry only their
 * immutable profile/index/frequency and private ACTLR_EL2 permission state.
 */
typedef struct TB371FCDSUPMUWire {
    uint32_t magic;
    uint32_t cpu_mask;
    uint32_t irq_intid;
    uint32_t event_counters;
    uint64_t frequency_hz;
    uint32_t pmcr;
    uint32_t count_enable;
    uint32_t overflow;
    uint32_t interrupt_enable;
    uint32_t selector;
    uint32_t event_count[TB371FC_DSU_EVENT_COUNTERS];
    uint32_t event_type[TB371FC_DSU_EVENT_COUNTERS];
    uint64_t cycle_count;
    uint64_t fraction;
} TB371FCDSUPMUWire;

/*
 * Board-established backend, immutable before CPU realize. The ARM target
 * does not link against an optional board-device implementation. Guest
 * accesses use ARM_CP_IO, so QEMU's existing CPreg helpers hold the BQL.
 */
typedef struct TB371FCDSUPMULink {
    void *opaque;
    uint32_t profile;
    uint32_t cpu_mask;
    uint32_t irq_intid;
    uint64_t frequency_hz;
    uint64_t (*read)(void *opaque, unsigned reg);
    void (*write)(void *opaque, unsigned reg, uint64_t value);
    int (*snapshot)(void *opaque, TB371FCDSUPMUWire *wire);
    int (*begin_load)(void *opaque, unsigned cpu_index);
    int (*stage_load)(void *opaque, const TB371FCDSUPMUWire *wire);
    int (*complete_load)(void *opaque, unsigned cpu_index);
} TB371FCDSUPMULink;

TB371FCDSUPMULink *tb371fc_dsu_pmu_get_link(DeviceState *dev);

#endif
