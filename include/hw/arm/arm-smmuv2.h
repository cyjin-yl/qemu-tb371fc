/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Functional AArch64 stage-1 subset of the Arm SMMUv2 architecture.
 */
#ifndef HW_ARM_ARM_SMMUV2_H
#define HW_ARM_ARM_SMMUV2_H

#include "hw/core/sysbus.h"
#include "system/memory.h"

#define TYPE_ARM_SMMUV2 "arm-smmuv2"
OBJECT_DECLARE_SIMPLE_TYPE(ArmSMMUv2State, ARM_SMMUV2)

/*
 * Call after realize. Each SID obtains its own translating DMA address space.
 * MMIO 0: architectural registers. IRQ 0: global fault, 1: configuration fault,
 * IRQ 2+n: context-bank n. Properties: mmio-size, num-context-banks, num-smrs,
 * memory (MemoryRegion link, defaults to system memory).
 */
AddressSpace *arm_smmuv2_get_dma_as(DeviceState *dev, uint16_t sid);
MemoryRegion *arm_smmuv2_get_dma_mr(DeviceState *dev, uint16_t sid);

/*
 * Opt-in reference debug adapter. Each bit gates one 0x400-SID group for
 * ordinary emulated DMA; a gated access fails rather than being queued.
 * The caller owns and migrates the gate mask, and must restore it before CPUs
 * resume. Queries bypass this debug gate but require an enabled TRANS context
 * and a real page-table translation: architectural bypass is not a success.
 */
void arm_smmuv2_set_debug_halt_groups(DeviceState *dev, uint32_t groups);
bool arm_smmuv2_debug_translate(DeviceState *dev, uint16_t sid, uint64_t va,
                                IOMMUAccessFlags access, MemTxAttrs attrs,
                                uint64_t *pa);

#endif
