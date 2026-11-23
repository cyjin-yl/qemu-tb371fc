/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_SPMI_PMIC_ARB_H
#define HW_MISC_QCOM_SPMI_PMIC_ARB_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_SPMI_PMIC_ARB "qcom-spmi-pmic-arb"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSpmiPmicArbState, QCOM_SPMI_PMIC_ARB)

/* Native DT reg-names order. No native APID/version capture is implied. */
enum {
    QCOM_SPMI_PMIC_ARB_CORE,
    QCOM_SPMI_PMIC_ARB_CHNLS,
    QCOM_SPMI_PMIC_ARB_OBSRVR,
    QCOM_SPMI_PMIC_ARB_INTR,
    QCOM_SPMI_PMIC_ARB_CNFG,
    QCOM_SPMI_PMIC_ARB_NUM_REGIONS,
};

#endif
