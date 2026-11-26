/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_SM8250_SPMI_DEBUG_FUSE_H
#define HW_MISC_QCOM_SM8250_SPMI_DEBUG_FUSE_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE "qcom-sm8250-spmi-debug-fuse"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250SpmiDebugFuseState,
                          QCOM_SM8250_SPMI_DEBUG_FUSE)

/* Software decode guard, not a claimed physical fuse-bank extent. */
#define QCOM_SPMI_DEBUG_FUSE_GUARD_BASE UINT64_C(0x007820a0)
#define QCOM_SPMI_DEBUG_FUSE_GUARD_SIZE 16
#define QCOM_SPMI_DEBUG_FUSE_WORD_OFFSET 8

#endif
