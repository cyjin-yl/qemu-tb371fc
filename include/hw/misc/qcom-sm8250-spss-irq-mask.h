/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_QCOM_SM8250_SPSS_IRQ_MASK_H
#define HW_QCOM_SM8250_SPSS_IRQ_MASK_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_QCOM_SM8250_SPSS_IRQ_MASK "qcom-sm8250-spss-irq-mask"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250SpssIrqMaskState,
                          QCOM_SM8250_SPSS_IRQ_MASK)

#endif
