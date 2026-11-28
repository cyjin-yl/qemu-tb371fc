/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_SM8250_SDE_RSC_H
#define HW_MISC_QCOM_SM8250_SDE_RSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SM8250_SDE_RSC "qcom-sm8250-sde-rsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250SdeRscState, QCOM_SM8250_SDE_RSC)

/* Overlay only the listed non-TCS DRV windows; wrapper is a separate region. */
void qcom_sm8250_sde_rsc_map(DeviceState *dev, hwaddr drv, hwaddr wrapper);

#endif
