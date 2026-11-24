/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_MDSS_GDSC_H
#define HW_MISC_QCOM_MDSS_GDSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_MDSS_GDSC "qcom-mdss-gdsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomMdssGdscState, QCOM_MDSS_GDSC)

#define QCOM_MDSS_GDSC_DOMAINS 1
#define QCOM_MDSS_GDSC_REGISTER_BYTES 4

typedef struct QcomMdssGdscDomainDesc {
    const char *name;
    uint64_t address;
    uint32_t dt_flags;
    uint32_t timeout_us;
} QcomMdssGdscDomainDesc;

extern const QcomMdssGdscDomainDesc
qcom_mdss_gdsc_domains[QCOM_MDSS_GDSC_DOMAINS];

/*
 * One four-byte MDSS GDSCR window from the pinned original DT. This independent
 * type does not change the six-camera VMState. The explicit enabled-reference
 * profile starts collapsed; no clock, display engine, retained payload or
 * analog transition timing is modeled.
 *
 * Shared supply GPIO inputs: parent-known and parent-on, one each. The board
 * fans out RPMh mmcx.lvl availability to camera and MDSS without a parent loop.
 * MDSS's self-referencing proxy-supply is a software regulator consumer only.
 * Hardware request GPIO inputs: hw-request-known and hw-request, one each;
 * initially unknown. Outputs power and state-known likewise have one line.
 * Protocol reset preserves provider inputs and restores SW_COLLAPSE=1.
 */
#endif
