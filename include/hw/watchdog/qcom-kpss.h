/*
 * Qualcomm KPSS watchdog digital counter model.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_WATCHDOG_QCOM_KPSS_H
#define HW_WATCHDOG_QCOM_KPSS_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_KPSS_WATCHDOG "qcom-kpss-watchdog"
OBJECT_DECLARE_SIMPLE_TYPE(QcomKpssWatchdogState, QCOM_KPSS_WATCHDOG)

#define QCOM_KPSS_WATCHDOG_SIZE 0x1000

#endif
