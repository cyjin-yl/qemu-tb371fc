/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_VIDEO_GDSC_H
#define HW_MISC_QCOM_VIDEO_GDSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_VIDEO_GDSC "qcom-video-gdsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomVideoGdscState, QCOM_VIDEO_GDSC)

#define QCOM_VIDEO_GDSC_DOMAINS 4
#define QCOM_VIDEO_GDSC_REGISTER_BYTES 4

typedef struct QcomVideoGdscDomainDesc {
    const char *name;
    uint64_t address;
    uint32_t dt_flags;
    uint32_t timeout_us;
} QcomVideoGdscDomainDesc;

extern const QcomVideoGdscDomainDesc
qcom_video_gdsc_domains[QCOM_VIDEO_GDSC_DOMAINS];

/*
 * Four independent four-byte VIDEO GDSCR windows from the pinned original DT.
 * This type preserves the camera, MDSS and NPU devices and their VMState.
 * The explicit enabled-reference profile starts collapsed; no clock, video
 * engine, retained payload or analog transition timing is modeled.
 *
 * Shared supply GPIO inputs: parent-known and parent-on, one each. The board
 * fans out RPMh mmcx.lvl availability to camera, MDSS and VIDEO as siblings.
 * Hardware request inputs: hw-request-known/request, one pair per domain;
 * all begin unknown. Only mvs0 and mvs1 declare hardware mode. power and
 * state-known outputs have four lines each. Unknown feedback rejects reads.
 * Protocol reset preserves provider inputs and restores SW_COLLAPSE=1.
 */
#endif
