/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_NPU_GDSC_H
#define HW_MISC_QCOM_NPU_GDSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_NPU_GDSC "qcom-npu-gdsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomNpuGdscState, QCOM_NPU_GDSC)

#define QCOM_NPU_GDSC_DOMAINS 1
#define QCOM_NPU_GDSC_REGISTER_BYTES 4

typedef struct QcomNpuGdscDomainDesc {
    const char *name;
    uint64_t address;
    uint32_t dt_flags;
    uint32_t timeout_us;
} QcomNpuGdscDomainDesc;

extern const QcomNpuGdscDomainDesc
qcom_npu_gdsc_domains[QCOM_NPU_GDSC_DOMAINS];

/*
 * One four-byte NPU GDSCR window from the pinned original DT. This independent
 * type preserves the NPUCC, camera and MDSS models and their VMState.
 * The explicit enabled-reference profile starts collapsed; it supplies no
 * power, clock, retained payload, compute engine or transition timing.
 *
 * The DT declares no parent supply. parent-known/on GPIO inputs remain
 * unknown and unconnected in the board; no RPMh resource is selected.
 * A collapsed software request resolves known-off even with unknown supply.
 * An asserted software request has unknown feedback until the parent is
 * explicitly known. Unknown feedback returns a transaction error, never ON.
 * The actual DT does not declare hardware mode: HW_CONTROL writes fail and
 * the retained hw-request-known/request inputs cannot enable that mode.
 * Protocol reset preserves external inputs and restores SW_COLLAPSE=1.
 */
#endif
