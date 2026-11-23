/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_NOC_SBM_H
#define HW_MISC_QCOM_NOC_SBM_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_NOC_SBM "qcom-noc-sbm"
OBJECT_DECLARE_SIMPLE_TYPE(QcomNocSbmState, QCOM_NOC_SBM)

#define QCOM_NOC_SBM_WINDOWS 3

typedef struct QcomNocSbmRegion {
    hwaddr physical_base;
    uint32_t byte_count;
    const char *suffix;
} QcomNocSbmRegion;

extern const QcomNocSbmRegion qcom_noc_sbm_regions[QCOM_NOC_SBM_WINDOWS];

#endif
