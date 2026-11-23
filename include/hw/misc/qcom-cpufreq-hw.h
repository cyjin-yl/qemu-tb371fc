/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_CPUFREQ_HW_H
#define HW_MISC_QCOM_CPUFREQ_HW_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_CPUFREQ_HW_EPSS "qcom-cpufreq-hw-epss"
OBJECT_DECLARE_SIMPLE_TYPE(QcomCpufreqHwState, QCOM_CPUFREQ_HW_EPSS)

#define QCOM_CPUFREQ_HW_DOMAINS 3

typedef struct QcomCpufreqHwDomain {
    hwaddr base;
    uint32_t size;
    uint32_t first_cpu_index;
    uint32_t cpu_count;
    uint32_t max_cores;
    uint32_t cpu_mask;
    uint32_t spi;
} QcomCpufreqHwDomain;

extern const QcomCpufreqHwDomain
    qcom_cpufreq_hw_domains[QCOM_CPUFREQ_HW_DOMAINS];

#endif
