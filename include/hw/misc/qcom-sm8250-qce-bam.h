/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_QCE_BAM_H
#define HW_MISC_QCOM_SM8250_QCE_BAM_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SM8250_QCE_BAM "qcom-sm8250-qce-bam"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250QceBamState, QCOM_SM8250_QCE_BAM)

/* Union of the actual shared crypto-base and crypto-bam-base resources. */
#define QCOM_SM8250_QCE_BAM_BASE UINT64_C(0x01dc4000)
#define QCOM_SM8250_QCE_BAM_SIZE UINT32_C(0x0003c000)

#endif
