/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Qualcomm TCSR mutex driver-protocol model.
 */
#ifndef HW_MISC_QCOM_TCSR_MUTEX_H
#define HW_MISC_QCOM_TCSR_MUTEX_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_TCSR_MUTEX "qcom-tcsr-mutex"
OBJECT_DECLARE_SIMPLE_TYPE(QcomTcsrMutexState, QCOM_TCSR_MUTEX)

/*
 * Geometry is independently present in both the captured live DT and the
 * first DTB in the current boot_a partition backup. The matching public
 * kernel mirror registers 32 locks; these are software-visible slots, not
 * a measurement of the silicon access-control policy.
 */
#define QCOM_TCSR_MUTEX_MMIO_SIZE 0x20000
#define QCOM_TCSR_MUTEX_LOCK_STRIDE 0x1000
#define QCOM_TCSR_MUTEX_NUM_LOCKS 32

/*
 * SysBus MMIO 0, board physical base 0x01f40000. No IRQ or GPIO ports.
 * Only aligned 32-bit owner words at n * 0x1000 are implemented.
 *
 * boot-unlocked-reference (bool, default false):
 *   false: reset leaves every owner unknown; reads and nonzero claims fail.
 *   true: reset explicitly assumes all owners are zero/unlocked.
 * This is a reference handoff assumption, never a captured POR value.
 *
 * A full-word write of zero releases a slot, including an unknown slot,
 * without an invented owner ACL. A nonzero write claims a known free slot
 * or leaves a known held slot unchanged. Repeated writes of the same owner
 * do not create recursion or per-CPU exclusion. The Linux hwspinlock core
 * provides its own local SMP lock.
 *
 * Remote processors, TrustZone access controls, electrical arbitration
 * timing, and initial hardware owner values are not modeled or inferred.
 */
#endif
