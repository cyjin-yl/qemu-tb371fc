/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_SM8250_QFPROM_GPU_H
#define HW_MISC_QCOM_SM8250_QFPROM_GPU_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SM8250_QFPROM_GPU "qcom-sm8250-qfprom-gpu"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250QfpromGpuState, QCOM_SM8250_QFPROM_GPU)

/* Software decode guards, not claims about physical fuse bank extents. */
#define QCOM_QFPROM_GPU_SPEED_GUARD_BASE UINT64_C(0x00784190)
#define QCOM_QFPROM_GPU_SLOPE_GUARD_BASE UINT64_C(0x007845c0)
#define QCOM_QFPROM_GPU_GUARD_SIZE 32
#define QCOM_QFPROM_GPU_SPEED_OFFSET 11
#define QCOM_QFPROM_GPU_SLOPE_OFFSET 8

#endif
