/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm GENI UART FIFO-mode behavioral model for TB371FC development.
 * Register definitions/driver-visible sequencing are based on Linux v6.12:
 *   include/linux/soc/qcom/geni-se.h
 *   drivers/tty/serial/qcom_geni_serial.c
 *   drivers/soc/qcom/qcom-geni-se.c
 *
 * This does not execute GENI firmware or model baud timing, DMA/GSI, flow
 * control, parity, power collapse or the electrical UART. RX host chunks
 * become immediately stale; TX drains as the chardev accepts bytes. FIFO
 * geometry is a model choice (16 words x 32 bits), not a silicon measurement.
 */

#include "qemu/osdep.h"
#include "hw/char/qcom-geni-uart.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/qdev-properties-system.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define R(s, off) ((s)->regs[(off) / 4])
#define GENI_BIT(n) (1U << (n))

enum {
    GENI_FORCE_DEFAULT = 0x020,
    GENI_STATUS = 0x040,
    GENI_FW_REV = 0x068,
    GENI_FW_S_REV = 0x06c,
    GENI_DMA_MODE = 0x258,
    UART_TX_LEN = 0x270,
    GENI_M_CMD = 0x600,
    GENI_M_CTRL = 0x604,
    GENI_M_STATUS = 0x610,
    GENI_M_EN = 0x614,
    GENI_M_CLEAR = 0x618,
    GENI_M_EN_SET = 0x61c,
    GENI_M_EN_CLEAR = 0x620,
    GENI_S_CMD = 0x630,
    GENI_S_CTRL = 0x634,
    GENI_S_STATUS = 0x640,
    GENI_S_EN = 0x644,
    GENI_S_CLEAR = 0x648,
    GENI_S_EN_SET = 0x64c,
    GENI_S_EN_CLEAR = 0x650,
    GENI_TX_FIFO = 0x700,
    GENI_RX_FIFO = 0x780,
    GENI_TX_FIFO_STATUS = 0x800,
    GENI_RX_FIFO_STATUS = 0x804,
    GENI_TX_WM = 0x80c,
    GENI_RX_WM = 0x810,
    GENI_RX_RFR_WM = 0x814,
    GENI_IOS = 0x908,
    GENI_M_GP_LENGTH = 0x910,
    GENI_S_GP_LENGTH = 0x914,
    GENI_IRQ_EN = 0xe1c,
    GENI_HW_PARAM0 = 0xe24,
    GENI_HW_PARAM1 = 0xe28,
};

#define IRQ_DONE GENI_BIT(0)
#define IRQ_OVERRUN GENI_BIT(1)
#define IRQ_ILLEGAL GENI_BIT(2)
#define IRQ_FAILURE GENI_BIT(3)
#define IRQ_CANCEL GENI_BIT(4)
#define IRQ_ABORT GENI_BIT(5)
#define IRQ_RX_READ_ERR GENI_BIT(24)
#define IRQ_RX_WRITE_ERR GENI_BIT(25)
#define IRQ_RX_WM GENI_BIT(26)
#define IRQ_RX_LAST GENI_BIT(27)
#define IRQ_TX_WRITE_ERR GENI_BIT(29)
#define IRQ_TX_WM GENI_BIT(30)
#define IRQ_SECONDARY GENI_BIT(31)
#define CTRL_CANCEL GENI_BIT(2)
#define CTRL_ABORT GENI_BIT(1)
#define CTRL_DISABLE GENI_BIT(0)

/* Unconnected inputs preserve the standalone device-test configuration. */
static bool geni_clock_running(Clock *clk)
{
    return !clock_has_source(clk) || clock_get(clk) != 0;
}

static bool geni_bus_available(QcomGeniUartState *s)
{
    return !s->reset_held && geni_clock_running(s->m_ahb) &&
           geni_clock_running(s->s_ahb);
}

static bool geni_engine_available(QcomGeniUartState *s)
{
    return geni_bus_available(s) && geni_clock_running(s->se_clk);
}

static uint32_t geni_s_status(QcomGeniUartState *s)
{
    uint32_t status = R(s, GENI_S_STATUS);
    uint32_t words = DIV_ROUND_UP(s->rx_count, 4);

    if (words && words >= R(s, GENI_RX_WM)) {
        status |= IRQ_RX_WM;
    }
    return status;
}

static uint32_t geni_m_status(QcomGeniUartState *s)
{
    uint32_t status = R(s, GENI_M_STATUS);
    uint32_t secondary = geni_s_status(s);

    /* Linux start_tx_fifo relies on an empty FIFO IRQ before START_TX. */
    if (R(s, GENI_TX_WM) &&
        DIV_ROUND_UP(s->tx_count, 4) < R(s, GENI_TX_WM)) {
        status |= IRQ_TX_WM;
    }
    status |= secondary & IRQ_RX_WM;
    if (secondary & R(s, GENI_S_EN)) {
        status |= IRQ_SECONDARY;
    }
    return status;
}

static void geni_update_irq(QcomGeniUartState *s)
{
    bool main_irq = (R(s, GENI_IRQ_EN) & GENI_BIT(2)) &&
                    (geni_m_status(s) & R(s, GENI_M_EN));
    bool secondary_irq = (R(s, GENI_IRQ_EN) & GENI_BIT(3)) &&
                         (geni_s_status(s) & R(s, GENI_S_EN));

    qemu_set_irq(s->irq, geni_engine_available(s) &&
                 (main_irq || secondary_irq));
}

static void geni_remove_tx_watch(QcomGeniUartState *s)
{
    if (s->tx_watch) {
        g_source_remove(s->tx_watch);
        s->tx_watch = 0;
    }
}

static gboolean geni_tx_ready(void *opaque, GIOCondition cond, void *user_data);

static void geni_drain_tx(QcomGeniUartState *s)
{
    int written;

    geni_remove_tx_watch(s);
    if (!geni_engine_available(s)) {
        geni_update_irq(s);
        return;
    }
    if (s->tx_zero_pending) {
        s->tx_zero_pending = false;
        R(s, GENI_M_STATUS) |= IRQ_DONE;
    }
    if (!s->tx_active || !s->tx_count) {
        geni_update_irq(s);
        return;
    }
    if (!qemu_chr_fe_backend_connected(&s->chr)) {
        written = s->tx_count;
    } else {
        written = qemu_chr_fe_write(&s->chr, s->tx_fifo, s->tx_count);
    }
    if (written > 0) {
        s->tx_count -= written;
        s->tx_remaining -= written;
        s->tx_sent += written;
        memmove(s->tx_fifo, s->tx_fifo + written, s->tx_count);
    }
    if (s->tx_count) {
        s->tx_watch = qemu_chr_fe_add_watch(&s->chr, G_IO_OUT | G_IO_HUP,
                                          geni_tx_ready, s);
        /* Keep queued bytes when a backend cannot install a watch. */
        if (!s->tx_watch) {
            R(s, GENI_M_STATUS) |= IRQ_FAILURE;
        }
    } else if (!s->tx_remaining) {
        s->tx_active = false;
        R(s, GENI_M_STATUS) |= IRQ_DONE;
    }
    geni_update_irq(s);
}

static gboolean geni_tx_ready(void *opaque, GIOCondition cond, void *user_data)
{
    QcomGeniUartState *s = user_data;

    s->tx_watch = 0;
    geni_drain_tx(s);
    return G_SOURCE_REMOVE;
}

static int geni_can_receive(void *opaque)
{
    QcomGeniUartState *s = opaque;

    if (!geni_engine_available(s) || !s->rx_active ||
        R(s, GENI_DMA_MODE)) {
        return 0;
    }
    return QCOM_GENI_UART_FIFO_SIZE - s->rx_count;
}

static void geni_receive(void *opaque, const uint8_t *buf, int size)
{
    QcomGeniUartState *s = opaque;
    unsigned int available = geni_can_receive(s);
    unsigned int count = MIN((unsigned int)size, available);

    if (count) {
        memcpy(s->rx_fifo + s->rx_count, buf, count);
        s->rx_count += count;
        s->rx_received += count;
        /* Deliberately no real-time/baud-dependent stale timer. */
        R(s, GENI_M_STATUS) |= IRQ_RX_LAST;
        R(s, GENI_S_STATUS) |= IRQ_RX_LAST;
    }
    if ((unsigned int)size > available) {
        R(s, GENI_M_STATUS) |= IRQ_RX_WRITE_ERR;
        R(s, GENI_S_STATUS) |= IRQ_RX_WRITE_ERR;
    }
    geni_update_irq(s);
}

static void geni_event(void *opaque, QEMUChrEvent event)
{
    QcomGeniUartState *s = opaque;

    if (event == CHR_EVENT_OPENED) {
        geni_drain_tx(s);
        qemu_chr_fe_accept_input(&s->chr);
    }
}

static bool geni_config_register(hwaddr offset)
{
    switch (offset) {
    case 0x000: /* GENI_INIT_CFG_REVISION */
    case 0x004: /* GENI_S_INIT_CFG_REVISION */
    case 0x010: /* GENI_GENERAL_CFG */
    case 0x020: /* FORCE_DEFAULT: IO policy only, not FIFO reset */
    case 0x024: /* GENI_OUTPUT_CTRL */
    case 0x028: /* GENI_CGC_CTRL */
    case 0x048: /* GENI_SER_M_CLK_CFG */
    case 0x04c: /* GENI_SER_S_CLK_CFG */
    case 0x07c: /* GENI_CLK_SEL */
    case 0x22c: /* UART_LOOPBACK_CFG (stored, not implemented) */
    case 0x240: /* UART_IO_MACRO_CTRL */
    case 0x254: /* GENI_BYTE_GRAN */
    case 0x25c: /* UART_TX_TRANS_CFG */
    case 0x260: /* TX_PACKING_CFG0 */
    case 0x264: /* TX_PACKING_CFG1 */
    case 0x268: /* UART_TX_WORD_LEN */
    case 0x26c: /* UART_TX_STOP_BIT_LEN */
    case UART_TX_LEN:
    case 0x280: /* UART_RX_TRANS_CFG */
    case 0x284: /* RX_PACKING_CFG0 */
    case 0x288: /* RX_PACKING_CFG1 */
    case 0x28c: /* UART_RX_WORD_LEN */
    case 0x294: /* UART_RX_STALE_CNT */
    case 0x2a4: /* UART_TX_PARITY_CFG */
    case 0x2a8: /* UART_RX_PARITY_CFG */
    case 0x2ac: /* UART_MANUAL_RFR */
    case 0xe18: /* GSI_EVENT_EN: GSI unsupported */
    case 0xe30: /* DMA_GENERAL_CFG: initialization bookkeeping */
        return true;
    default:
        return false;
    }
}

static uint64_t geni_read(void *opaque, hwaddr offset, unsigned int size)
{
    QcomGeniUartState *s = opaque;
    uint32_t value = 0;
    unsigned int count;

    switch (offset) {
    case GENI_STATUS:
        return ((s->tx_active || s->tx_zero_pending) ? GENI_BIT(0) : 0) |
               (s->rx_active ? GENI_BIT(12) : 0);
    case GENI_FW_REV:
    case GENI_FW_S_REV:
        return 2 << 8; /* Model provides UART protocol, no firmware image. */
    case GENI_HW_PARAM0:
    case GENI_HW_PARAM1:
        return (32U << 24) | (16U << 16);
    case 0x060: /* GENI_CLK_CTRL_RO */
    case 0x064: /* GENI_IF_DISABLE_RO: FIFO interface present */
    case 0x074: /* GENI_FW_MULTILOCK_MSA_RO */
    case 0xc40: /* DMA TX IRQ status: no DMA interrupts in FIFO model */
    case 0xd40: /* DMA RX IRQ status */
    case 0xe20: /* DMA_IF_EN_RO: no DMA interface */
    case 0xe2c: /* SE_HW_PARAM_2: no programmable firmware RAM */
    case GENI_M_CTRL:
    case GENI_S_CTRL:
    case GENI_M_CLEAR:
    case GENI_S_CLEAR:
    case GENI_M_EN_SET:
    case GENI_S_EN_SET:
    case GENI_M_EN_CLEAR:
    case GENI_S_EN_CLEAR:
        return 0;
    case GENI_M_STATUS:
        return geni_m_status(s);
    case GENI_S_STATUS:
        return geni_s_status(s);
    case GENI_TX_FIFO_STATUS:
        return DIV_ROUND_UP(s->tx_count, 4);
    case GENI_RX_FIFO_STATUS:
        if (!s->rx_count) {
            return 0;
        }
        return GENI_BIT(31) | ((s->rx_count % 4) << 28) |
               DIV_ROUND_UP(s->rx_count, 4);
    case GENI_RX_FIFO:
        count = MIN(s->rx_count, 4U);
        if (!count) {
            R(s, GENI_M_STATUS) |= IRQ_RX_READ_ERR;
            R(s, GENI_S_STATUS) |= IRQ_RX_READ_ERR;
        }
        for (unsigned int i = 0; i < count; i++) {
            value |= (uint32_t)s->rx_fifo[i] << (8 * i);
        }
        s->rx_count -= count;
        memmove(s->rx_fifo, s->rx_fifo + count, s->rx_count);
        geni_update_irq(s);
        qemu_chr_fe_accept_input(&s->chr);
        return value;
    case GENI_IOS:
        return GENI_BIT(0); /* RX idle high; CTS asserted (active low). */
    case GENI_M_GP_LENGTH:
        return s->tx_sent;
    case GENI_S_GP_LENGTH:
        return s->rx_received;
    case GENI_DMA_MODE:
    case GENI_M_CMD:
    case GENI_S_CMD:
    case GENI_M_EN:
    case GENI_S_EN:
    case GENI_TX_WM:
    case GENI_RX_WM:
    case GENI_RX_RFR_WM:
    case GENI_IRQ_EN:
        return R(s, offset);
    default:
        if (geni_config_register(offset)) {
            return R(s, offset);
        }
        qemu_log_mask(LOG_UNIMP, "%s: unimplemented read at 0x%" HWADDR_PRIx
                      "\n", TYPE_QCOM_GENI_UART, offset);
        return 0;
    }
}

static void geni_write_tx(QcomGeniUartState *s, uint32_t value)
{
    unsigned int count;

    if (!s->tx_active || R(s, GENI_DMA_MODE)) {
        R(s, GENI_M_STATUS) |= IRQ_TX_WRITE_ERR;
        return;
    }
    count = MIN(4U, s->tx_remaining - s->tx_count);
    if (!count || count > QCOM_GENI_UART_FIFO_SIZE - s->tx_count) {
        R(s, GENI_M_STATUS) |= IRQ_TX_WRITE_ERR;
        return;
    }
    for (unsigned int i = 0; i < count; i++) {
        s->tx_fifo[s->tx_count++] = value >> (i * 8);
    }
    geni_drain_tx(s);
}

static void geni_write_cmd(QcomGeniUartState *s, bool secondary, uint32_t value)
{
    uint32_t status_reg = secondary ? GENI_S_STATUS : GENI_M_STATUS;
    bool active = secondary ? s->rx_active :
                  (s->tx_active || s->tx_zero_pending);

    if (active) {
        R(s, status_reg) |= IRQ_OVERRUN;
        return;
    }
    if (R(s, GENI_DMA_MODE)) {
        R(s, status_reg) |= IRQ_FAILURE;
        qemu_log_mask(LOG_UNIMP, "%s: DMA-mode command rejected\n",
                      TYPE_QCOM_GENI_UART);
        return;
    }
    if (value >> 27 != 1) {
        R(s, status_reg) |= IRQ_ILLEGAL;
        return;
    }
    R(s, secondary ? GENI_S_CMD : GENI_M_CMD) = value;
    if (secondary) {
        s->rx_active = true;
        s->rx_received = 0;
        qemu_chr_fe_accept_input(&s->chr);
    } else {
        s->tx_remaining = R(s, UART_TX_LEN);
        s->tx_sent = 0;
        s->tx_active = s->tx_remaining != 0;
        if (!s->tx_active) {
            if (geni_engine_available(s)) {
                R(s, GENI_M_STATUS) |= IRQ_DONE;
            } else {
                s->tx_zero_pending = true;
            }
        }
    }
}

static void geni_write_ctrl(QcomGeniUartState *s, bool secondary,
                            uint32_t value)
{
    uint32_t status_reg = secondary ? GENI_S_STATUS : GENI_M_STATUS;

    if (!(value & (CTRL_CANCEL | CTRL_ABORT | CTRL_DISABLE))) {
        return;
    }
    if (secondary) {
        s->rx_active = false;
        if (value & (CTRL_ABORT | CTRL_DISABLE)) {
            s->rx_count = 0;
            R(s, GENI_S_STATUS) &= ~IRQ_RX_LAST;
            R(s, GENI_M_STATUS) &= ~IRQ_RX_LAST;
        }
    } else {
        geni_remove_tx_watch(s);
        s->tx_active = false;
        s->tx_zero_pending = false;
        s->tx_count = 0;
        s->tx_remaining = 0;
    }
    if (value & CTRL_ABORT) {
        R(s, status_reg) |= IRQ_ABORT | IRQ_DONE;
    } else if (value & CTRL_CANCEL) {
        R(s, status_reg) |= IRQ_CANCEL | IRQ_DONE;
    }
}

static void geni_write(void *opaque, hwaddr offset, uint64_t value,
                       unsigned int size)
{
    QcomGeniUartState *s = opaque;

    switch (offset) {
    case GENI_M_CMD:
    case GENI_S_CMD:
        geni_write_cmd(s, offset == GENI_S_CMD, value);
        break;
    case GENI_M_CTRL:
    case GENI_S_CTRL:
        geni_write_ctrl(s, offset == GENI_S_CTRL, value);
        break;
    case GENI_M_CLEAR:
        R(s, GENI_M_STATUS) &= ~value;
        break;
    case GENI_S_CLEAR:
        R(s, GENI_S_STATUS) &= ~value;
        break;
    case GENI_M_EN_SET:
        R(s, GENI_M_EN) |= value;
        break;
    case GENI_S_EN_SET:
        R(s, GENI_S_EN) |= value;
        break;
    case GENI_M_EN_CLEAR:
        R(s, GENI_M_EN) &= ~value;
        break;
    case GENI_S_EN_CLEAR:
        R(s, GENI_S_EN) &= ~value;
        break;
    case GENI_DMA_MODE:
        R(s, offset) = value & 1;
        if (value & 1) {
            qemu_log_mask(LOG_UNIMP, "%s: DMA/GSI not implemented\n",
                          TYPE_QCOM_GENI_UART);
        }
        break;
    case GENI_TX_FIFO:
        geni_write_tx(s, value);
        break;
    case GENI_TX_WM:
    case GENI_RX_WM:
    case GENI_RX_RFR_WM:
        R(s, offset) = value & 0x3f;
        break;
    case GENI_M_EN:
    case GENI_S_EN:
    case GENI_IRQ_EN:
        R(s, offset) = value;
        break;
    /* Clearing unsupported DMA status is benign during FIFO init. */
    case 0xc44:
    case 0xd44:
        break;
    default:
        if (geni_config_register(offset)) {
            R(s, offset) = value;
            if ((offset == 0x22c && value) ||
                (offset == 0xe18 && value)) {
                qemu_log_mask(LOG_UNIMP, "%s: unsupported config 0x%"
                              HWADDR_PRIx " = 0x%" PRIx64 "\n",
                              TYPE_QCOM_GENI_UART, offset, value);
            }
        } else {
            qemu_log_mask(LOG_UNIMP, "%s: unimplemented write at 0x%"
                          HWADDR_PRIx " = 0x%" PRIx64 "\n",
                          TYPE_QCOM_GENI_UART, offset, value);
        }
        break;
    }
    geni_update_irq(s);
}

static MemTxResult geni_read_with_attrs(void *opaque, hwaddr offset,
                                        uint64_t *value, unsigned size,
                                        MemTxAttrs attrs)
{
    QcomGeniUartState *s = opaque;
    if (!geni_bus_available(s)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_GENI_UART
                      ": MMIO read while AHB is gated or reset is asserted\n");
        *value = 0;
        return MEMTX_ERROR;
    }
    *value = geni_read(opaque, offset, size);
    return MEMTX_OK;
}

static MemTxResult geni_write_with_attrs(void *opaque, hwaddr offset,
                                         uint64_t value, unsigned size,
                                         MemTxAttrs attrs)
{
    QcomGeniUartState *s = opaque;
    if (!geni_bus_available(s)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_GENI_UART
                      ": MMIO write while AHB is gated or reset is asserted\n");
        return MEMTX_ERROR;
    }
    geni_write(opaque, offset, value, size);
    return MEMTX_OK;
}

static const MemoryRegionOps geni_ops = {
    .read_with_attrs = geni_read_with_attrs,
    .write_with_attrs = geni_write_with_attrs,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void geni_reset(DeviceState *dev)
{
    QcomGeniUartState *s = QCOM_GENI_UART(dev);

    geni_remove_tx_watch(s);
    memset(s->regs, 0, sizeof(s->regs));
    memset(s->tx_fifo, 0, sizeof(s->tx_fifo));
    memset(s->rx_fifo, 0, sizeof(s->rx_fifo));
    s->tx_count = 0;
    s->rx_count = 0;
    s->tx_remaining = 0;
    s->tx_sent = 0;
    s->rx_received = 0;
    s->tx_active = false;
    s->rx_active = false;
    s->tx_zero_pending = false;
    R(s, 0x268) = 8;
    R(s, 0x28c) = 8;
    geni_update_irq(s);
}

static void geni_clock_changed(void *opaque, ClockEvent event)
{
    QcomGeniUartState *s = opaque;
    if (!s->ready) {
        return;
    }
    geni_drain_tx(s);
    qemu_chr_fe_accept_input(&s->chr);
    geni_update_irq(s);
}

static void geni_reset_input(void *opaque, int n, int level)
{
    QcomGeniUartState *s = opaque;
    bool previously_held = s->reset_held;

    s->reset_held = level;
    if (level && !previously_held) {
        geni_reset(DEVICE(s));
    } else if (!level && previously_held) {
        geni_clock_changed(s, ClockUpdate);
    }
}

static int geni_pre_load(void *opaque)
{
    geni_remove_tx_watch(opaque);
    return 0;
}

static int geni_post_load(void *opaque, int version_id)
{
    QcomGeniUartState *s = opaque;

    if (s->tx_count > QCOM_GENI_UART_FIFO_SIZE ||
        s->rx_count > QCOM_GENI_UART_FIFO_SIZE ||
        s->tx_count > s->tx_remaining ||
        (s->tx_active && !s->tx_remaining) ||
        (!s->tx_active && (s->tx_count || s->tx_remaining)) ||
        (s->tx_zero_pending && s->tx_active) ||
        (s->reset_held && (s->tx_active || s->rx_active ||
                           s->tx_count || s->rx_count || s->tx_zero_pending))) {
        return -EINVAL;
    }
    geni_drain_tx(s);
    qemu_chr_fe_accept_input(&s->chr);
    geni_update_irq(s);
    return 0;
}

static bool geni_clock_state_needed(void *opaque)
{
    QcomGeniUartState *s = opaque;
    return clock_has_source(s->se_clk) || clock_has_source(s->m_ahb) ||
           clock_has_source(s->s_ahb) || s->reset_held || s->tx_zero_pending;
}

static const VMStateDescription vmstate_geni_clocks = {
    .name = TYPE_QCOM_GENI_UART "/clocks",
    .version_id = 1,
    .minimum_version_id = 1,
    .needed = geni_clock_state_needed,
    .fields = (const VMStateField[]) {
        VMSTATE_CLOCK(se_clk, QcomGeniUartState),
        VMSTATE_CLOCK(m_ahb, QcomGeniUartState),
        VMSTATE_CLOCK(s_ahb, QcomGeniUartState),
        VMSTATE_BOOL(reset_held, QcomGeniUartState),
        VMSTATE_BOOL(tx_zero_pending, QcomGeniUartState),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_geni = {
    .name = TYPE_QCOM_GENI_UART,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = geni_pre_load,
    .post_load = geni_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, QcomGeniUartState, QCOM_GENI_UART_REG_COUNT),
        VMSTATE_UINT8_ARRAY(tx_fifo, QcomGeniUartState,
                            QCOM_GENI_UART_FIFO_SIZE),
        VMSTATE_UINT8_ARRAY(rx_fifo, QcomGeniUartState,
                            QCOM_GENI_UART_FIFO_SIZE),
        VMSTATE_UINT32(tx_count, QcomGeniUartState),
        VMSTATE_UINT32(rx_count, QcomGeniUartState),
        VMSTATE_UINT32(tx_remaining, QcomGeniUartState),
        VMSTATE_UINT32(tx_sent, QcomGeniUartState),
        VMSTATE_UINT32(rx_received, QcomGeniUartState),
        VMSTATE_BOOL(tx_active, QcomGeniUartState),
        VMSTATE_BOOL(rx_active, QcomGeniUartState),
        VMSTATE_END_OF_LIST()
    },
    .subsections = (const VMStateDescription * const []) {
        &vmstate_geni_clocks,
        NULL,
    },
};

static const Property geni_properties[] = {
    DEFINE_PROP_CHR("chardev", QcomGeniUartState, chr),
};

static void geni_realize(DeviceState *dev, Error **errp)
{
    QcomGeniUartState *s = QCOM_GENI_UART(dev);

    s->ready = true;
    qemu_chr_fe_set_handlers(&s->chr, geni_can_receive, geni_receive,
                             geni_event, NULL, s, NULL, true);
}

static void geni_init(Object *obj)
{
    QcomGeniUartState *s = QCOM_GENI_UART(obj);

    memory_region_init_io(&s->mmio, obj, &geni_ops, s, TYPE_QCOM_GENI_UART,
                          QCOM_GENI_UART_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    s->se_clk = qdev_init_clock_in(DEVICE(obj), "se-clk", geni_clock_changed,
                                  s, ClockUpdate);
    s->m_ahb = qdev_init_clock_in(DEVICE(obj), "m-ahb", geni_clock_changed,
                                 s, ClockUpdate);
    s->s_ahb = qdev_init_clock_in(DEVICE(obj), "s-ahb", geni_clock_changed,
                                 s, ClockUpdate);
    qdev_init_gpio_in_named(DEVICE(obj), geni_reset_input, "reset", 1);
}

static void geni_finalize(Object *obj)
{
    geni_remove_tx_watch(QCOM_GENI_UART(obj));
}

static void geni_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = geni_realize;
    dc->vmsd = &vmstate_geni;
    dc->desc = "Qualcomm GENI UART FIFO behavioral subset (no DMA)";
    device_class_set_legacy_reset(dc, geni_reset);
    device_class_set_props(dc, geni_properties);
    set_bit(DEVICE_CATEGORY_INPUT, dc->categories);
}

static const TypeInfo geni_info = {
    .name = TYPE_QCOM_GENI_UART,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomGeniUartState),
    .instance_init = geni_init,
    .instance_finalize = geni_finalize,
    .class_init = geni_class_init,
};

static void geni_register_types(void)
{
    type_register_static(&geni_info);
}

type_init(geni_register_types)
