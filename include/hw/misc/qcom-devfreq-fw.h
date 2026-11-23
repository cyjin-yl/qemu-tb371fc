/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_DEVFREQ_FW_H
#define HW_MISC_QCOM_DEVFREQ_FW_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_DEVFREQ_FW "qcom-devfreq-fw"
OBJECT_DECLARE_SIMPLE_TYPE(QcomDevfreqFwState, QCOM_DEVFREQ_FW)

#define QCOM_DEVFREQ_FW_WINDOWS 3

typedef struct QcomDevfreqFwRegion {
    hwaddr physical_base;
    uint32_t byte_count;
    const char *suffix;
} QcomDevfreqFwRegion;

extern const QcomDevfreqFwRegion
    qcom_devfreq_fw_regions[QCOM_DEVFREQ_FW_WINDOWS];

#endif
