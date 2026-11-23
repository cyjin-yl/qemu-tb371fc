/*
 * SM8250 UFS controller and QMP v4 PHY software interface.
 * SPDX-License-Identifier: GPL-2.0-only
 */
#ifndef HW_UFS_QCOM_SM8250_UFS_H
#define HW_UFS_QCOM_SM8250_UFS_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SM8250_UFS "qcom-sm8250-ufs"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250UfsState, QCOM_SM8250_UFS)

#define QCOM_SM8250_UFS_HCI_SIZE 0x3000
#define QCOM_SM8250_UFS_PHY_SIZE 0x1000

#endif
