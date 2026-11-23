/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_TIMER_QCOM_MPM2_SLEEP_COUNTER_H
#define HW_TIMER_QCOM_MPM2_SLEEP_COUNTER_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_MPM2_SLEEP_COUNTER "qcom-mpm2-sleep-counter"
OBJECT_DECLARE_SIMPLE_TYPE(QcomMpm2SleepCounterState, QCOM_MPM2_SLEEP_COUNTER)

#define QCOM_MPM2_SLEEP_COUNTER_MMIO_SIZE 4

#endif
