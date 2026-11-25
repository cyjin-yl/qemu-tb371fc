/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_QCOM_SM8250_RPM_STATS_H
#define HW_QCOM_SM8250_RPM_STATS_H

#include "hw/core/sysbus.h"
#include "hw/misc/qcom-sm8250-rpm-stats-reference.h"
#include "qom/object.h"

#define TYPE_QCOM_SM8250_RPM_STATS "qcom-sm8250-rpm-stats"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250RpmStatsState, QCOM_SM8250_RPM_STATS)

enum {
    QCOM_RPM_STATS_OFFSET_MMIO,
    QCOM_RPM_STATS_RECORDS_MMIO,
};

#endif
