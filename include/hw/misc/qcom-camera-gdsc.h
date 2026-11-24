/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_CAMERA_GDSC_H
#define HW_MISC_QCOM_CAMERA_GDSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_CAMERA_GDSC "qcom-camera-gdsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomCameraGdscState, QCOM_CAMERA_GDSC)

#define QCOM_CAMERA_GDSC_DOMAINS 6
#define QCOM_CAMERA_GDSC_REGISTER_BYTES 4

typedef struct QcomCameraGdscDomainDesc {
    const char *name;
    uint64_t address;
    uint32_t dt_flags;
    uint32_t timeout_us;
} QcomCameraGdscDomainDesc;

extern const QcomCameraGdscDomainDesc
qcom_camera_gdsc_domains[QCOM_CAMERA_GDSC_DOMAINS];

/*
 * Six independent, four-byte camera GDSCR windows from the pinned original DT.
 * This type directly derives from SysBusDevice, not the existing GPU GDSC type.
 * The explicit enabled-reference profile starts every domain collapsed. No
 * clock, analog rail, camera execution, reset MMIO or transition time is modeled.
 *
 * Shared GPIO inputs: parent-known and parent-on, one each. Board integration
 * connects the existing RPMh mmcx.lvl digital availability, not a fixed level.
 * Per-domain GPIO inputs: hw-request-known and hw-request, six each. Their
 * initial unknown state does not fabricate a camera hardware requester.
 * Per-domain GPIO outputs: power and state-known, six each. A power output is
 * meaningful only when its corresponding state-known output is asserted.
 * Protocol reset preserves inputs and restores each GDSCR to SW_COLLAPSE=1.
 */
#endif
