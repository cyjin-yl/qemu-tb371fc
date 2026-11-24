/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_ARM_QCOM_SMMUV500_H
#define HW_ARM_QCOM_SMMUV500_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SMMUV500 "qcom-smmuv500"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSmmuv500State, QCOM_SMMUV500)

/* Original TB371FC apps-SMMU DT geometry; not the architectural aperture. */
#define QCOM_SMMUV500_TCU_BASE UINT64_C(0x15182000)
#define QCOM_SMMUV500_QTEST_CACHE_BASE UINT64_C(0x51000000)

#endif
