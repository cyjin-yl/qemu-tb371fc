/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Qualcomm GENI UART: a documented FIFO-mode behavioral subset. */
#ifndef HW_CHAR_QCOM_GENI_UART_H
#define HW_CHAR_QCOM_GENI_UART_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "chardev/char-fe.h"
#include "qom/object.h"
#include "system/runstate.h"

#define TYPE_QCOM_GENI_UART "qcom-geni-uart"
#define TYPE_QCOM_SM8250_GENI_HS "qcom-sm8250-geni-hs"
OBJECT_DECLARE_SIMPLE_TYPE(QcomGeniUartState, QCOM_GENI_UART)

#define QCOM_GENI_UART_MMIO_SIZE 0x4000
#define QCOM_GENI_UART_FIFO_SIZE 64
#define QCOM_GENI_UART_REG_COUNT (QCOM_GENI_UART_MMIO_SIZE / 4)

/* The owner pointer is local configuration, never part of the wire. */
typedef struct QcomGeniHsProfile {
    uint32_t enabled;
    uint32_t identity;
    uint64_t se_base;
    uint32_t se_size;
    uint64_t wrapper_address;
    uint32_t wrapper_size;
    uint32_t main_revision;
    uint32_t secondary_revision;
    uint32_t wrapper_version;
    uint32_t fifo_bytes;
    QcomGeniUartState *owner;
} QcomGeniHsProfile;

struct QcomGeniUartState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    CharFrontend chr;
    qemu_irq irq;
    Clock *se_clk;
    Clock *m_ahb;
    Clock *s_ahb;
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
    bool tx_zero_pending;
    bool reset_held;
    bool ready;
    guint tx_watch;

    /* Only the distinct, default-off HS reference subtype uses these. */
    bool hs_profile;
    bool hs_enabled;
    bool hs_loading;
    bool hs_profile_seen;
    bool hs_reset_input;
    MemoryRegion hs_wrapper;
    QcomGeniHsProfile hs_wire;
    uint64_t hs_input_periods[3];
    uint8_t hs_tx_active_wire;
    uint8_t hs_rx_active_wire;
    uint8_t hs_reset_wire;
    uint8_t hs_zero_wire;
    VMChangeStateEntry *hs_vmstate;
};

hwaddr qcom_geni_hs_se_base(void);
hwaddr qcom_geni_hs_wrapper_version_address(void);

#endif
