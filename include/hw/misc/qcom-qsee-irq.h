/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_QSEE_IRQ_H
#define HW_MISC_QCOM_QSEE_IRQ_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_QSEE_IRQ "qcom-qsee-irq"
OBJECT_DECLARE_SIMPLE_TYPE(QcomQseeIrqState, QCOM_QSEE_IRQ)

#define QCOM_QSEE_IRQ_BANKS 2
#define QCOM_QSEE_IRQ_BITS 4
#define QCOM_QSEE_IRQ_WINDOW_SIZE 0x20

/*
 * Latch one notification event. This reports local latch acceptance only;
 * it is not a TEE/SCM call or a secure-firmware completion response.
 */
bool qcom_qsee_irq_notify(DeviceState *dev, unsigned bank, unsigned bit);

/*
 * MMIO0/1: bank-local status +0, W1C clear +8, mask +0x1c.
 * IRQ0/1: pending & ~mask for each bank.
 * "notify" GPIO0..7: reference rising-edge event adapter, bank * 4 + bit.
 */
#endif
