/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Qualcomm PDC legacy interrupt conversion and configuration subset.
 */
#ifndef HW_INTC_QCOM_PDC_H
#define HW_INTC_QCOM_PDC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_PDC "qcom-pdc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomPDCState, QCOM_PDC)

#define QCOM_PDC_MAX_PINS 256
#define QCOM_PDC_NATIVE_PINS 138
#define QCOM_PDC_MMIO_SIZE 0x30000
#define QCOM_PDC_SPI_CONFIG_SIZE 0x60

/*
 * MMIO 0: legacy PDC registers (native TB371FC base 0x0b220000).
 * MMIO 1: shared SPI level/edge configuration (base 0x17c000f0).
 * Named GPIO input "irq-in"[num-pins].
 * SysBus IRQ output n: converted interrupt for PDC pin n. Board connects
 * these outputs using the captured qcom,pdc-ranges, not a fixed SPI offset.
 *
 * num-pins defaults to the native mapped interval 0..137.
 * reference-version defaults to 0: VERSION reads fault because the native
 * value has not been observed. An explicit nonzero legacy (<3.2) reference
 * version may be supplied for mainline software that reads this register.
 * It is a declared model profile, never evidence of the silicon version.
 *
 * Edge conversion emits a functional pulse into the parent edge detector.
 * Parent GIC owns pending/ACK state. Power-domain wake and electrical pulse
 * timing, newer v3.2 enable layout, and phantom-IRQ quirks are not modeled.
 */
#endif
