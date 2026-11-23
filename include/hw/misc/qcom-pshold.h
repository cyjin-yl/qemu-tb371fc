/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Qualcomm PS_HOLD digital output, separate from the receiving PMIC.
 */
#ifndef HW_MISC_QCOM_PSHOLD_H
#define HW_MISC_QCOM_PSHOLD_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_PSHOLD "qcom-pshold"
OBJECT_DECLARE_SIMPLE_TYPE(QcomPsHoldState, QCOM_PSHOLD)

#endif
