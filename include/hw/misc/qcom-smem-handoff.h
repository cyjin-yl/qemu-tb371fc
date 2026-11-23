/*
 * Explicit reference-only SMEM v12 boot handoff for the TB371FC direct loader.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_MISC_QCOM_SMEM_HANDOFF_H
#define HW_MISC_QCOM_SMEM_HANDOFF_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SMEM_HANDOFF "qcom-smem-handoff"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSmemHandoffState, QCOM_SMEM_HANDOFF)

#define QCOM_SMEM_RAM_BASE UINT64_C(0x80000000)
#define QCOM_SMEM_BASE UINT64_C(0x80900000)
#define QCOM_SMEM_SIZE 0x200000
#define QCOM_SMEM_GLOBAL_OFFSET 0x3000
#define QCOM_SMEM_PTABLE_OFFSET (QCOM_SMEM_SIZE - 0x1000)
#define QCOM_SMEM_GLOBAL_SIZE \
    (QCOM_SMEM_PTABLE_OFFSET - QCOM_SMEM_GLOBAL_OFFSET)

/*
 * Always instantiate the device so migration compares both profiles in both
 * directions. reference-empty=false registers no ROM and changes no RAM.
 *
 * reference-empty=true registers one boot blob in existing ordinary board
 * RAM. Normal system reset restores the empty reference heap; the QEMU ROM
 * loader skips writes while receiving migration, and normal RAM migration
 * restores guest allocations. The marker device migrates only the profile.
 *
 * This is an explicit synthetic loader handoff, not a physical device, an SBL
 * capture, socinfo, remote CPU state, or a secure/IPC firmware service.
 */
#endif
