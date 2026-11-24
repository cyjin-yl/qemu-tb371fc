/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_ARM_QCOM_SM8250_TBU_H
#define HW_ARM_QCOM_SM8250_TBU_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SM8250_TBU "qcom-sm8250-tbu-debug"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250TbuState, QCOM_SM8250_TBU)

/* MMIO 0: TCU; 1/2: ANOC1 base/status; 3/4: ANOC2 base/status. */
#define QCOM_SM8250_TBU_COUNT 2

#endif
