/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Explicit reference AOP QMP peer, selected by the board profile.
 */
#ifndef HW_MISC_QCOM_AOP_QMP_H
#define HW_MISC_QCOM_AOP_QMP_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_AOP_QMP "qcom-aop-qmp-reference"
OBJECT_DECLARE_SIMPLE_TYPE(QcomAopQmpState, QCOM_AOP_QMP)

#endif /* HW_MISC_QCOM_AOP_QMP_H */
