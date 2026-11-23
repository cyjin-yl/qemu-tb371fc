/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_REFGEN_H
#define HW_MISC_QCOM_REFGEN_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_REFGEN "qcom-refgen"
OBJECT_DECLARE_SIMPLE_TYPE(QcomRefgenState, QCOM_REFGEN)

/*
 * Only the Kona/SM8250 CTRL5 software-request register is implemented.
 * Map MMIO0 at the DT resource base + 0x80, not at the resource base.
 */
#define QCOM_REFGEN_CTRL5_OFFSET 0x80
#define QCOM_REFGEN_CTRL5_SIZE 4

/*
 * GPIO outputs (one line each):
 *   "software-enable": CTRL5 bit 0, meaningful only when state-known is high.
 *   "state-known": the modeled software-request register has a known value.
 *
 * QOM read-only properties with the same names expose these logical values;
 * "state" is "unknown", "disabled-request", or "enabled-request".
 * These signals do not describe analog voltage, power-good, or HW votes.
 *
 * "boot-disabled-reference" defaults to false: reset state is unknown and
 * register reads fail. Explicit true selects a reference disabled reset state.
 */
#endif
