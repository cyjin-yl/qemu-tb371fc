/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Qualcomm GENI UART: a documented FIFO-mode behavioral subset. */
#ifndef HW_CHAR_QCOM_GENI_UART_H
#define HW_CHAR_QCOM_GENI_UART_H

#include "hw/core/sysbus.h"
#include "chardev/char-fe.h"
#include "qom/object.h"

#define TYPE_QCOM_GENI_UART "qcom-geni-uart"
OBJECT_DECLARE_SIMPLE_TYPE(QcomGeniUartState, QCOM_GENI_UART)

#define QCOM_GENI_UART_MMIO_SIZE 0x4000
#define QCOM_GENI_UART_FIFO_SIZE 64
#define QCOM_GENI_UART_REG_COUNT (QCOM_GENI_UART_MMIO_SIZE / 4)

struct QcomGeniUartState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    CharFrontend chr;
    qemu_irq irq;
    uint32_t regs[QCOM_GENI_UART_REG_COUNT];
    uint8_t tx_fifo[QCOM_GENI_UART_FIFO_SIZE];
    uint8_t rx_fifo[QCOM_GENI_UART_FIFO_SIZE];
    uint32_t tx_count;
    uint32_t rx_count;
    uint32_t tx_remaining;
    uint32_t tx_sent;
    uint32_t rx_received;
    bool tx_active;
    bool rx_active;
    guint tx_watch;
};

#endif
