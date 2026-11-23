/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * TB371FC / SM8250 TLMM functional GPIO subset.
 */
#ifndef HW_GPIO_QCOM_TB371FC_TLMM_H
#define HW_GPIO_QCOM_TB371FC_TLMM_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_TB371FC_TLMM "qcom-tb371fc-tlmm"
OBJECT_DECLARE_SIMPLE_TYPE(QcomTB371FCTLMMState, QCOM_TB371FC_TLMM)

#define QCOM_TB371FC_TLMM_GPIOS 180
#define QCOM_TB371FC_TLMM_SIZE 0x01000000

/*
 * MMIO 0: native 16MiB window, board maps at 0x0f000000.
 * IRQ 0: summary interrupt, board connects to GIC SPI 208.
 * Named inputs:  gpio-in[180] (external digital pad levels).
 * Named outputs: gpio-out[180] (output latch),
 *                gpio-oe[180] (GPIO function and output enabled),
 *                ufs-reset[1] (dedicated output-only pad, NOT GPIO 180).
 *
 * Upstream Linux exposes the dedicated UFS_RESET as its group 180. Its
 * separate registers are implemented, but it has no input/IRQ/OE register.
 * Property boot-ufs-reset-high (default false) explicitly releases the pad
 * on virtual reset for a direct-kernel handoff. It is not a measured POR value.
 * No alternate-function fabric, PDC wakeup, or analog timing.
 */
#endif
