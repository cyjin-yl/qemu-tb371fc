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
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#include "qcom-sm8250-geni-hs-data.inc"

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
    GENI_DMA_TX_EN = 0xc48,
    GENI_DMA_TX_EN_SET = 0xc4c,
    GENI_DMA_TX_EN_CLEAR = 0xc50,
    GENI_DMA_RX_EN = 0xd48,
    GENI_DMA_RX_EN_SET = 0xd4c,
    GENI_DMA_RX_EN_CLEAR = 0xd50,
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

/*
 * Paused/incoming reference engines cannot consume backend data. This guard
 * also covers provider-clock callbacks that run before this device's preload.
 * The legacy UART keeps its original qtest and chardev behavior.
 */
static bool geni_hs_io_blocked(QcomGeniUartState *s)
{
    return s->hs_profile && (!s->hs_enabled || s->hs_loading ||
                             !runstate_is_running());
}

static void geni_accept_input(QcomGeniUartState *s)
{
    if (!geni_hs_io_blocked(s)) {
        qemu_chr_fe_accept_input(&s->chr);
    }
}

/* Unconnected inputs preserve the standalone device-test configuration. */
static bool geni_clock_running(Clock *clk)
{
    return !clock_has_source(clk) || clock_get(clk) != 0;
}

static bool geni_bus_available(QcomGeniUartState *s)
{
    return (!s->hs_profile || (s->hs_enabled && !s->hs_loading)) &&
           !s->reset_held && geni_clock_running(s->m_ahb) &&
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

static bool geni_irq_level(QcomGeniUartState *s)
{
    bool main_irq = (R(s, GENI_IRQ_EN) & GENI_BIT(2)) &&
                    (geni_m_status(s) & R(s, GENI_M_EN));
    bool secondary_irq = (R(s, GENI_IRQ_EN) & GENI_BIT(3)) &&
                         (geni_s_status(s) & R(s, GENI_S_EN));

    return geni_engine_available(s) && (main_irq || secondary_irq);
}

static void geni_update_irq(QcomGeniUartState *s)
{
    if (!s->hs_profile || !s->hs_loading) {
        qemu_set_irq(s->irq, geni_irq_level(s));
    }
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

    if (geni_hs_io_blocked(s)) {
        return;
    }
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
        if (s->hs_profile) {
            /* An absent peer never acknowledges or discards queued bytes. */
            geni_update_irq(s);
            return;
        }
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

    if (geni_hs_io_blocked(s) || !geni_engine_available(s) || !s->rx_active ||
        R(s, GENI_DMA_MODE)) {
        return 0;
    }
    return QCOM_GENI_UART_FIFO_SIZE - s->rx_count;
}

static void geni_receive(void *opaque, const uint8_t *buf, int size)
{
    QcomGeniUartState *s = opaque;
    unsigned int available;
    unsigned int count;

    if (geni_hs_io_blocked(s)) {
        return;
    }
    available = geni_can_receive(s);
    count = MIN((unsigned int)size, available);

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
        geni_accept_input(s);
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

/*
 * Strict reference-policy allowlist, independent of the legacy zero fallback.
 * Stored configuration does not model baud timing or electrical pin behavior.
 * DMA IRQ masks are real mask storage; no DMA engine/status is supplied.
 */
static bool geni_hs_write_mask(hwaddr offset, uint32_t *mask)
{
    switch (offset) {
    case GENI_FORCE_DEFAULT:
        *mask = 1;
        return true;
    case 0x024: /* output policy */
    case 0x028: /* common CGC, excluding firmware-RAM programming */
        *mask = 0x7f;
        return true;
    case 0x048:
    case 0x04c:
        *mask = 0xfff1;
        return true;
    case 0x07c:
        *mask = 7;
        return true;
    case 0x254:
        *mask = 3;
        return true;
    case 0x260:
    case 0x264:
    case 0x284:
    case 0x288:
        *mask = 0xfffff; /* two source-defined 10-bit packing vectors */
        return true;
    case GENI_TX_FIFO:
    case GENI_M_EN:
    case GENI_S_EN:
    case GENI_M_EN_SET:
    case GENI_S_EN_SET:
    case GENI_M_EN_CLEAR:
    case GENI_S_EN_CLEAR:
    case GENI_M_CLEAR:
    case GENI_S_CLEAR:
        *mask = UINT32_MAX;
        return true;
    case 0x268:
    case 0x28c:
        *mask = 0x3ff;
        return true;
    case UART_TX_LEN:
    case 0x294:
        *mask = 0xffffff;
        return true;
    case GENI_M_CMD:
    case GENI_S_CMD:
        *mask = 0xf8000000; /* implemented commands have no parameters */
        return true;
    case GENI_M_CTRL:
    case GENI_S_CTRL:
        *mask = 7;
        return true;
    case GENI_TX_WM:
    case GENI_RX_WM:
    case GENI_RX_RFR_WM:
        *mask = 0x3f;
        return true;
    case GENI_IRQ_EN:
        *mask = 0xf;
        return true;
    case GENI_DMA_TX_EN:
    case GENI_DMA_TX_EN_SET:
    case GENI_DMA_TX_EN_CLEAR:
        *mask = GENI_HS_DMA_TX_IRQ_MASK;
        return true;
    case GENI_DMA_RX_EN:
    case GENI_DMA_RX_EN_SET:
    case GENI_DMA_RX_EN_CLEAR:
        *mask = GENI_HS_DMA_RX_IRQ_MASK;
        return true;
    case GENI_DMA_MODE:
    case 0x22c: /* no loopback */
    case 0x240: /* no electrical pin remapping */
    case 0x25c: /* no parity or CTS policy */
    case 0x26c: /* one stop bit */
    case 0x280: /* byte-only RX, no inserted status or parity */
    case 0x2a4:
    case 0x2a8:
    case 0x2ac: /* no electrical RFR */
    case 0xe18: /* no GSI */
        *mask = 0;
        return true;
    default:
        return false;
    }
}

/*
 * se_get_packing_config(8, 4, false) in the pinned vendor source produces
 * 0x4380e/0xc3e0e. This reference engine has a fixed four-byte little-endian
 * FIFO format: reset zero means that same model default, not a silicon reset
 * capture. Both the explicit canonical words and zero keep that byte format;
 * other vector layouts, byte granularities and UART word sizes are rejected.
 */
static bool geni_hs_format_supported(hwaddr offset, uint32_t value)
{
    switch (offset) {
    case 0x254:
        return value == 0;
    case 0x260:
    case 0x284:
        return value == 0 || value == 0x4380e;
    case 0x264:
    case 0x288:
        return value == 0 || value == 0xc3e0e;
    case 0x268:
    case 0x28c:
        return value == 8;
    default:
        return true;
    }
}

static bool geni_hs_readable(hwaddr offset)
{
    uint32_t mask;

    if (offset != GENI_TX_FIFO && geni_hs_write_mask(offset, &mask)) {
        return true;
    }
    switch (offset) {
    case 0x000:
    case 0x004:
    case GENI_STATUS:
    case 0x060:
    case 0x064:
    case GENI_FW_REV:
    case GENI_FW_S_REV:
    case GENI_M_STATUS:
    case GENI_S_STATUS:
    case GENI_RX_FIFO:
    case GENI_TX_FIFO_STATUS:
    case GENI_RX_FIFO_STATUS:
    case GENI_IOS:
    case GENI_M_GP_LENGTH:
    case GENI_S_GP_LENGTH:
    case GENI_HW_PARAM0:
    case GENI_HW_PARAM1:
        return true;
    default:
        return false;
    }
}

/* Public DMA registers whose transfer/status semantics are not implemented. */
static bool geni_hs_unsupported_dma(hwaddr offset)
{
    switch (offset) {
    case 0xc30: /* TX pointer low/high, attributes and length */
    case 0xc34:
    case 0xc38:
    case 0xc3c:
    case 0xc40: /* TX status/clear; EN/SET/CLEAR masks are separate */
    case 0xc44:
    case 0xc54: /* TX length-in, FSM reset and burst */
    case 0xc58:
    case 0xc5c:
    case 0xd30: /* RX pointer low/high, attributes and length */
    case 0xd34:
    case 0xd38:
    case 0xd3c:
    case 0xd40: /* RX status/clear; EN/SET/CLEAR masks are separate */
    case 0xd44:
    case 0xd54: /* RX length-in, FSM reset, burst and flush */
    case 0xd58:
    case 0xd5c:
    case 0xd60:
        return true;
    default:
        return false;
    }
}

static bool geni_hs_stored_register(hwaddr offset, uint32_t *mask)
{
    if (!geni_hs_write_mask(offset, mask)) {
        return false;
    }
    switch (offset) {
    case GENI_TX_FIFO:
    case GENI_M_CTRL:
    case GENI_S_CTRL:
    case GENI_M_CLEAR:
    case GENI_S_CLEAR:
    case GENI_M_EN_SET:
    case GENI_S_EN_SET:
    case GENI_M_EN_CLEAR:
    case GENI_S_EN_CLEAR:
    case GENI_DMA_TX_EN_SET:
    case GENI_DMA_TX_EN_CLEAR:
    case GENI_DMA_RX_EN_SET:
    case GENI_DMA_RX_EN_CLEAR:
        return false;
    default:
        return true;
    }
}

static uint64_t geni_read(void *opaque, hwaddr offset, unsigned int size)
{
    QcomGeniUartState *s = opaque;
    uint32_t value = 0;
    unsigned int count;

    if (s->hs_profile) {
        switch (offset) {
        case 0x000:
        case 0x004:
            return GENI_HS_CFG_REVISION;
        case GENI_DMA_TX_EN:
        case GENI_DMA_RX_EN:
            return R(s, offset);
        case GENI_DMA_TX_EN_SET:
        case GENI_DMA_TX_EN_CLEAR:
        case GENI_DMA_RX_EN_SET:
        case GENI_DMA_RX_EN_CLEAR:
            return 0; /* write-one aliases do not retain written values */
        }
    }
    switch (offset) {
    case GENI_STATUS:
        return ((s->tx_active || s->tx_zero_pending) ? GENI_BIT(0) : 0) |
               (s->rx_active ? GENI_BIT(12) : 0);
    case GENI_FW_REV:
    case GENI_FW_S_REV:
        if (s->hs_profile) {
            return offset == GENI_FW_REV ? GENI_HS_MAIN_FW_REVISION :
                                          GENI_HS_SECONDARY_FW_REVISION;
        }
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
        geni_accept_input(s);
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
        geni_accept_input(s);
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

    if (s->hs_profile && (!geni_hs_readable(offset) ||
                          !geni_bus_available(s))) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GENI_HS
                      ": rejected read offset=0x%" HWADDR_PRIx
                      " size=%u reason=%s\n", offset, size,
                      !geni_bus_available(s) ? "bus-unavailable" :
                      geni_hs_unsupported_dma(offset) ? "unsupported-dma" :
                                                        "unsupported-register");
        *value = 0;
        return MEMTX_ERROR;
    }
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

    if (s->hs_profile) {
        uint32_t mask = 0;
        const char *reason = NULL;
        bool writable = geni_hs_write_mask(offset, &mask);

        if (!geni_bus_available(s)) {
            reason = "bus-unavailable";
        } else if (!writable) {
            reason = geni_hs_unsupported_dma(offset) ? "unsupported-dma" :
                     geni_hs_readable(offset) ? "read-only" :
                                               "unsupported-register";
        } else if (offset == GENI_DMA_MODE && value) {
            reason = "unsupported-dma";
        } else if (value & ~((uint64_t)mask)) {
            reason = "reserved-bits";
        } else if (!geni_hs_format_supported(offset, value)) {
            reason = "unsupported-format";
        }
        if (reason) {
            qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GENI_HS
                          ": rejected write offset=0x%" HWADDR_PRIx
                          " size=%u value=0x%" PRIx64 " reason=%s\n",
                          offset, size, value, reason);
            return MEMTX_ERROR;
        }
        switch (offset) {
        case GENI_DMA_TX_EN:
        case GENI_DMA_RX_EN:
            R(s, offset) = value;
            return MEMTX_OK;
        case GENI_DMA_TX_EN_SET:
            R(s, GENI_DMA_TX_EN) |= value;
            return MEMTX_OK;
        case GENI_DMA_TX_EN_CLEAR:
            R(s, GENI_DMA_TX_EN) &= ~value;
            return MEMTX_OK;
        case GENI_DMA_RX_EN_SET:
            R(s, GENI_DMA_RX_EN) |= value;
            return MEMTX_OK;
        case GENI_DMA_RX_EN_CLEAR:
            R(s, GENI_DMA_RX_EN) &= ~value;
            return MEMTX_OK;
        }
    }
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

static MemTxResult geni_hs_wrapper_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    QcomGeniUartState *s = opaque;

    if (offset || size != 4 || !geni_bus_available(s)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GENI_HS
                      ": rejected wrapper read offset=0x%" HWADDR_PRIx
                      " size=%u reason=bus-unavailable\n", offset, size);
        *value = 0;
        return MEMTX_ERROR;
    }
    *value = GENI_HS_WRAPPER_VERSION;
    return MEMTX_OK;
}

static MemTxResult geni_hs_wrapper_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_GENI_HS
                  ": rejected wrapper write offset=0x%" HWADDR_PRIx
                  " size=%u value=0x%" PRIx64 " reason=read-only\n",
                  offset, size, value);
    return MEMTX_ERROR;
}

static const MemoryRegionOps geni_hs_wrapper_ops = {
    .read_with_attrs = geni_hs_wrapper_read,
    .write_with_attrs = geni_hs_wrapper_write,
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
    if (!s->ready || geni_hs_io_blocked(s)) {
        return;
    }
    geni_drain_tx(s);
    geni_accept_input(s);
    geni_update_irq(s);
}

static void geni_reset_input(void *opaque, int n, int level)
{
    QcomGeniUartState *s = opaque;
    bool previously_held = s->reset_held;

    if (s->hs_profile) {
        s->hs_reset_input = !!level;
        if (s->ready && (s->hs_loading || !runstate_is_running())) {
            /* Provider restore precedes the consumer's wire validation. */
            return;
        }
    }
    s->reset_held = level;
    if (level && !previously_held) {
        geni_reset(DEVICE(s));
    } else if (!level && previously_held) {
        geni_clock_changed(s, ClockUpdate);
    }
}

static void geni_hs_vm_change(void *opaque, bool running, RunState state)
{
    QcomGeniUartState *s = opaque;

    if (!running) {
        geni_remove_tx_watch(s);
        return;
    }
    if (s->hs_loading) {
        return;
    }
    if (s->reset_held != s->hs_reset_input) {
        geni_reset_input(s, 0, s->hs_reset_input);
    }
    if (!s->hs_enabled) {
        return;
    }
    geni_drain_tx(s);
    geni_accept_input(s);
    geni_update_irq(s);
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

/* The native stream is independent: the legacy v1 body above is unchanged. */
static QcomGeniHsProfile geni_hs_expected_profile(QcomGeniUartState *s)
{
    return (QcomGeniHsProfile) {
        .enabled = s->hs_enabled,
        .identity = GENI_HS_PROFILE_ID,
        .se_base = GENI_HS_SE_BASE,
        .se_size = GENI_HS_SE_WINDOW_BYTES,
        .wrapper_address = GENI_HS_WRAPPER_VERSION_ADDRESS,
        .wrapper_size = GENI_HS_WRAPPER_WINDOW_BYTES,
        .main_revision = GENI_HS_MAIN_FW_REVISION,
        .secondary_revision = GENI_HS_SECONDARY_FW_REVISION,
        .wrapper_version = GENI_HS_WRAPPER_VERSION,
        .fifo_bytes = GENI_HS_FIFO_BYTES,
        .owner = s,
    };
}

static int geni_hs_profile_post_load(void *opaque, int version_id)
{
    QcomGeniHsProfile *p = opaque;
    QcomGeniUartState *s = p->owner;
    QcomGeniHsProfile expected = geni_hs_expected_profile(s);

    if (!s->hs_profile || p->enabled != expected.enabled ||
        p->identity != expected.identity || p->se_base != expected.se_base ||
        p->se_size != expected.se_size ||
        p->wrapper_address != expected.wrapper_address ||
        p->wrapper_size != expected.wrapper_size ||
        p->main_revision != expected.main_revision ||
        p->secondary_revision != expected.secondary_revision ||
        p->wrapper_version != expected.wrapper_version ||
        p->fifo_bytes != expected.fifo_bytes) {
        error_report(TYPE_QCOM_SM8250_GENI_HS ": invalid migration profile");
        return -EINVAL;
    }
    s->hs_profile_seen = true;
    return 0;
}

static const VMStateDescription vmstate_geni_hs_profile = {
    .name = TYPE_QCOM_SM8250_GENI_HS "/profile",
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = geni_hs_profile_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(enabled, QcomGeniHsProfile),
        VMSTATE_UINT32(identity, QcomGeniHsProfile),
        VMSTATE_UINT64(se_base, QcomGeniHsProfile),
        VMSTATE_UINT32(se_size, QcomGeniHsProfile),
        VMSTATE_UINT64(wrapper_address, QcomGeniHsProfile),
        VMSTATE_UINT32(wrapper_size, QcomGeniHsProfile),
        VMSTATE_UINT32(main_revision, QcomGeniHsProfile),
        VMSTATE_UINT32(secondary_revision, QcomGeniHsProfile),
        VMSTATE_UINT32(wrapper_version, QcomGeniHsProfile),
        VMSTATE_UINT32(fifo_bytes, QcomGeniHsProfile),
        VMSTATE_END_OF_LIST()
    },
};

static int geni_hs_pre_save(void *opaque)
{
    QcomGeniUartState *s = opaque;

    s->hs_wire = geni_hs_expected_profile(s);
    s->hs_input_periods[0] = clock_get(s->se_clk);
    s->hs_input_periods[1] = clock_get(s->m_ahb);
    s->hs_input_periods[2] = clock_get(s->s_ahb);
    s->hs_tx_active_wire = s->tx_active;
    s->hs_rx_active_wire = s->rx_active;
    s->hs_reset_wire = s->reset_held;
    s->hs_zero_wire = s->tx_zero_pending;
    return 0;
}

static int geni_hs_pre_load(void *opaque)
{
    QcomGeniUartState *s = opaque;

    s->hs_loading = true;
    s->hs_profile_seen = false;
    geni_remove_tx_watch(s);
    qemu_set_irq(s->irq, 0);
    return 0;
}

static bool geni_hs_register_state_valid(QcomGeniUartState *s)
{
    unsigned i;

    for (i = 0; i < QCOM_GENI_UART_REG_COUNT; i++) {
        hwaddr offset = i * 4;
        uint32_t mask = 0;
        uint32_t value = s->regs[i];

        if (offset == GENI_M_STATUS) {
            mask = 0x2b00003f;
        } else if (offset == GENI_S_STATUS) {
            mask = 0x0b00003f;
        } else if (!geni_hs_stored_register(offset, &mask)) {
            mask = 0;
        }
        if ((value & ~mask) ||
            ((offset == GENI_M_CMD || offset == GENI_S_CMD) &&
             value && value != (1U << 27)) ||
            !geni_hs_format_supported(offset, value)) {
            return false;
        }
        if (!s->hs_enabled &&
            value != ((offset == 0x268 || offset == 0x28c) ? 8 : 0)) {
            return false;
        }
    }
    return true;
}

static int geni_hs_post_load(void *opaque, int version_id)
{
    QcomGeniUartState *s = opaque;
    Clock *clocks[] = { s->se_clk, s->m_ahb, s->s_ahb };
    bool tx_active = s->hs_tx_active_wire;
    bool rx_active = s->hs_rx_active_wire;
    bool reset_held = s->hs_reset_wire;
    bool tx_zero_pending = s->hs_zero_wire;
    unsigned i;

    if (!s->hs_profile_seen || !geni_hs_register_state_valid(s) ||
        s->hs_tx_active_wire > 1 || s->hs_rx_active_wire > 1 ||
        s->hs_reset_wire > 1 || s->hs_zero_wire > 1 ||
        s->tx_count > QCOM_GENI_UART_FIFO_SIZE ||
        s->rx_count > QCOM_GENI_UART_FIFO_SIZE ||
        s->tx_count > s->tx_remaining ||
        (uint64_t)s->tx_sent + s->tx_remaining > 0xffffff ||
        (tx_active && !s->tx_remaining) ||
        (!tx_active && (s->tx_count || s->tx_remaining)) ||
        (tx_zero_pending && tx_active) ||
        (tx_zero_pending && s->tx_sent) ||
        ((tx_active || tx_zero_pending) &&
         R(s, GENI_M_CMD) != (1U << 27)) ||
        (rx_active && R(s, GENI_S_CMD) != (1U << 27)) ||
        (reset_held && (tx_active || rx_active || s->tx_count ||
                        s->rx_count || tx_zero_pending)) ||
        reset_held != s->hs_reset_input) {
        error_report(TYPE_QCOM_SM8250_GENI_HS ": invalid migration state");
        return -EINVAL;
    }
    if (!s->hs_enabled) {
        static const uint8_t empty[QCOM_GENI_UART_FIFO_SIZE];

        if (s->tx_count || s->rx_count || s->tx_remaining || s->tx_sent ||
            s->rx_received || tx_active || rx_active ||
            tx_zero_pending || memcmp(s->tx_fifo, empty, sizeof(empty)) ||
            memcmp(s->rx_fifo, empty, sizeof(empty))) {
            error_report(TYPE_QCOM_SM8250_GENI_HS
                         ": invalid disabled migration state");
            return -EINVAL;
        }
    }
    for (i = 0; i < ARRAY_SIZE(clocks); i++) {
        /* Inputs belong to the restored GCC provider; never repair them. */
        if (clocks[i]->multiplier != 1 || clocks[i]->divider != 1 ||
            s->hs_input_periods[i] != clock_get(clocks[i])) {
            error_report(TYPE_QCOM_SM8250_GENI_HS
                         ": input clock witness mismatch");
            return -EINVAL;
        }
    }
    s->tx_active = tx_active;
    s->rx_active = rx_active;
    s->reset_held = reset_held;
    s->tx_zero_pending = tx_zero_pending;
    s->hs_loading = false;
    geni_update_irq(s);
    /* Backend work waits for the running transition, after pure snapshots. */
    return 0;
}

static const VMStateDescription vmstate_geni_hs = {
    .name = TYPE_QCOM_SM8250_GENI_HS,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = geni_hs_pre_save,
    .pre_load = geni_hs_pre_load,
    .post_load = geni_hs_post_load,
    .fields = (const VMStateField[]) {
        /* Exactly 48 bytes, validated before any functional field is read. */
        VMSTATE_STRUCT(hs_wire, QcomGeniUartState, 1,
                       vmstate_geni_hs_profile, QcomGeniHsProfile),
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
        /* uint8 transport rejects noncanonical boolean bytes on input. */
        VMSTATE_UINT8(hs_tx_active_wire, QcomGeniUartState),
        VMSTATE_UINT8(hs_rx_active_wire, QcomGeniUartState),
        VMSTATE_UINT64_ARRAY(hs_input_periods, QcomGeniUartState, 3),
        VMSTATE_UINT8(hs_reset_wire, QcomGeniUartState),
        VMSTATE_UINT8(hs_zero_wire, QcomGeniUartState),
        VMSTATE_END_OF_LIST()
    },
};

enum {
    GET_TX_COUNT, GET_RX_COUNT, GET_TX_REMAINING, GET_TX_SENT, GET_RX_RECEIVED,
    GET_SE_PERIOD, GET_M_PERIOD, GET_S_PERIOD, GET_FIFO_BYTES,
    GET_IDENTITY, GET_SE_BASE, GET_SE_SIZE, GET_WRAPPER_ADDRESS, GET_WRAPPER_SIZE,
    GET_MAIN_REVISION, GET_SECONDARY_REVISION, GET_WRAPPER_VERSION,
    GET_VMSTATE_BYTES, GET_PROFILE_BYTES,
    GET_TX_ACTIVE, GET_RX_ACTIVE, GET_TX_ZERO_PENDING, GET_RESET_HELD,
    GET_IRQ_LEVEL, GET_FALSE, GET_TRUE,
};

static void geni_get_uint(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    QcomGeniUartState *s = QCOM_GENI_UART(obj);
    uint64_t value;

    switch ((uintptr_t)opaque) {
    case GET_TX_COUNT: value = s->tx_count; break;
    case GET_RX_COUNT: value = s->rx_count; break;
    case GET_TX_REMAINING: value = s->tx_remaining; break;
    case GET_TX_SENT: value = s->tx_sent; break;
    case GET_RX_RECEIVED: value = s->rx_received; break;
    case GET_SE_PERIOD: value = clock_get(s->se_clk); break;
    case GET_M_PERIOD: value = clock_get(s->m_ahb); break;
    case GET_S_PERIOD: value = clock_get(s->s_ahb); break;
    case GET_FIFO_BYTES: value = QCOM_GENI_UART_FIFO_SIZE; break;
    case GET_IDENTITY: value = GENI_HS_PROFILE_ID; break;
    case GET_SE_BASE: value = GENI_HS_SE_BASE; break;
    case GET_SE_SIZE: value = GENI_HS_SE_WINDOW_BYTES; break;
    case GET_WRAPPER_ADDRESS: value = GENI_HS_WRAPPER_VERSION_ADDRESS; break;
    case GET_WRAPPER_SIZE: value = GENI_HS_WRAPPER_WINDOW_BYTES; break;
    case GET_MAIN_REVISION: value = GENI_HS_MAIN_FW_REVISION; break;
    case GET_SECONDARY_REVISION: value = GENI_HS_SECONDARY_FW_REVISION; break;
    case GET_WRAPPER_VERSION: value = GENI_HS_WRAPPER_VERSION; break;
    case GET_VMSTATE_BYTES: value = 16608; break;
    case GET_PROFILE_BYTES: value = 48; break;
    default: g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
}

static void geni_get_bool(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    QcomGeniUartState *s = QCOM_GENI_UART(obj);
    bool value;

    switch ((uintptr_t)opaque) {
    case GET_TX_ACTIVE: value = s->tx_active; break;
    case GET_RX_ACTIVE: value = s->rx_active; break;
    case GET_TX_ZERO_PENDING: value = s->tx_zero_pending; break;
    case GET_RESET_HELD: value = s->reset_held; break;
    case GET_IRQ_LEVEL: value = geni_irq_level(s); break;
    case GET_FALSE: value = false; break;
    case GET_TRUE: value = true; break;
    default: g_assert_not_reached();
    }
    visit_type_bool(v, name, &value, errp);
}

static char *geni_registers_hex(Object *obj, Error **errp)
{
    QcomGeniUartState *s = QCOM_GENI_UART(obj);
    GString *str = g_string_sized_new(QCOM_GENI_UART_REG_COUNT * 8);
    unsigned i;

    for (i = 0; i < QCOM_GENI_UART_REG_COUNT; i++) {
        g_string_append_printf(str, "%08x", s->regs[i]);
    }
    return g_string_free(str, false);
}

static char *geni_fifo_hex(const uint8_t *fifo)
{
    GString *str = g_string_sized_new(QCOM_GENI_UART_FIFO_SIZE * 2);
    unsigned i;

    for (i = 0; i < QCOM_GENI_UART_FIFO_SIZE; i++) {
        g_string_append_printf(str, "%02x", fifo[i]);
    }
    return g_string_free(str, false);
}

static char *geni_tx_fifo_hex(Object *obj, Error **errp)
{
    return geni_fifo_hex(QCOM_GENI_UART(obj)->tx_fifo);
}

static char *geni_rx_fifo_hex(Object *obj, Error **errp)
{
    return geni_fifo_hex(QCOM_GENI_UART(obj)->rx_fifo);
}

static void geni_add_pure_properties(Object *obj)
{
    static const struct { const char *name; unsigned id; } ints[] = {
        { "tx-count", GET_TX_COUNT }, { "rx-count", GET_RX_COUNT },
        { "tx-remaining", GET_TX_REMAINING }, { "tx-sent", GET_TX_SENT },
        { "rx-received", GET_RX_RECEIVED }, { "se-clock-period", GET_SE_PERIOD },
        { "m-ahb-clock-period", GET_M_PERIOD },
        { "s-ahb-clock-period", GET_S_PERIOD }, { "fifo-bytes", GET_FIFO_BYTES },
    }, bools[] = {
        { "tx-active", GET_TX_ACTIVE }, { "rx-active", GET_RX_ACTIVE },
        { "tx-zero-pending", GET_TX_ZERO_PENDING },
        { "reset-held", GET_RESET_HELD }, { "irq-level", GET_IRQ_LEVEL },
    };
    unsigned i;

    object_property_add_str(obj, "registers-hex", geni_registers_hex, NULL);
    object_property_add_str(obj, "tx-fifo-hex", geni_tx_fifo_hex, NULL);
    object_property_add_str(obj, "rx-fifo-hex", geni_rx_fifo_hex, NULL);
    for (i = 0; i < ARRAY_SIZE(ints); i++) {
        object_property_add(obj, ints[i].name, "uint64", geni_get_uint,
                            NULL, NULL, (void *)(uintptr_t)ints[i].id);
    }
    for (i = 0; i < ARRAY_SIZE(bools); i++) {
        object_property_add(obj, bools[i].name, "bool", geni_get_bool,
                            NULL, NULL, (void *)(uintptr_t)bools[i].id);
    }
}

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

    memory_region_init_io(&s->mmio, obj, &geni_ops, s, object_get_typename(obj),
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
    geni_add_pure_properties(obj);
}

static void geni_finalize(Object *obj)
{
    QcomGeniUartState *s = QCOM_GENI_UART(obj);

    geni_remove_tx_watch(s);
    if (s->hs_vmstate) {
        qemu_del_vm_change_state_handler(s->hs_vmstate);
    }
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

static const Property geni_hs_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomGeniUartState, hs_enabled, false),
};

static void geni_hs_realize(DeviceState *dev, Error **errp)
{
    QcomGeniUartState *s = QCOM_GENI_UART(dev);

    QEMU_BUILD_BUG_ON(GENI_HS_SE_WINDOW_BYTES != QCOM_GENI_UART_MMIO_SIZE);
    QEMU_BUILD_BUG_ON(GENI_HS_FIFO_BYTES != QCOM_GENI_UART_FIFO_SIZE);
    if (!clock_has_source(s->se_clk) || !clock_has_source(s->m_ahb) ||
        !clock_has_source(s->s_ahb)) {
        error_setg(errp, TYPE_QCOM_SM8250_GENI_HS
                   " requires explicit SE, M-AHB and S-AHB clock inputs");
        return;
    }
    /* The configured target profile determines mapping, never incoming data. */
    memory_region_set_enabled(&s->mmio, s->hs_enabled);
    memory_region_set_enabled(&s->hs_wrapper, s->hs_enabled);
    s->hs_wire = geni_hs_expected_profile(s);
    s->hs_vmstate = qemu_add_vm_change_state_handler(geni_hs_vm_change, s);
    geni_realize(dev, errp);
}

static void geni_hs_init(Object *obj)
{
    QcomGeniUartState *s = QCOM_GENI_UART(obj);
    static const struct { const char *name; unsigned id; } ints[] = {
        { "reference-identity", GET_IDENTITY },
        { "physical-base", GET_SE_BASE },
        { "window-bytes", GET_SE_SIZE },
        { "wrapper-address", GET_WRAPPER_ADDRESS },
        { "wrapper-window-bytes", GET_WRAPPER_SIZE },
        { "main-fw-revision", GET_MAIN_REVISION },
        { "secondary-fw-revision", GET_SECONDARY_REVISION },
        { "wrapper-version", GET_WRAPPER_VERSION },
        { "vmstate-bytes", GET_VMSTATE_BYTES },
        { "profile-wire-bytes", GET_PROFILE_BYTES },
    }, bools[] = {
        { "firmware-executed", GET_FALSE },
        { "dma-modeled", GET_FALSE },
        { "physical-state-captured", GET_FALSE },
        { "dma-mask-modeled", GET_TRUE },
    };
    unsigned i;

    s->hs_profile = true;
    s->hs_wire.owner = s;
    memory_region_init_io(&s->hs_wrapper, obj, &geni_hs_wrapper_ops, s,
                          TYPE_QCOM_SM8250_GENI_HS "/wrapper-version",
                          GENI_HS_WRAPPER_WINDOW_BYTES);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->hs_wrapper);
    for (i = 0; i < ARRAY_SIZE(ints); i++) {
        object_property_add(obj, ints[i].name, "uint64", geni_get_uint,
                            NULL, NULL, (void *)(uintptr_t)ints[i].id);
    }
    for (i = 0; i < ARRAY_SIZE(bools); i++) {
        object_property_add(obj, bools[i].name, "bool", geni_get_bool,
                            NULL, NULL, (void *)(uintptr_t)bools[i].id);
    }
}

static void geni_hs_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = geni_hs_realize;
    dc->vmsd = &vmstate_geni_hs;
    dc->desc = "SM8250 GENI HS reference UART (FIFO and DMA IRQ masks only)";
    device_class_set_props(dc, geni_hs_properties);
}

static const TypeInfo geni_hs_info = {
    .name = TYPE_QCOM_SM8250_GENI_HS,
    .parent = TYPE_QCOM_GENI_UART,
    .instance_size = sizeof(QcomGeniUartState),
    .instance_init = geni_hs_init,
    .class_init = geni_hs_class_init,
};

hwaddr qcom_geni_hs_se_base(void)
{
    return GENI_HS_SE_BASE;
}

hwaddr qcom_geni_hs_wrapper_version_address(void)
{
    return GENI_HS_WRAPPER_VERSION_ADDRESS;
}

static void geni_register_types(void)
{
    type_register_static(&geni_info);
    type_register_static(&geni_hs_info);
}

type_init(geni_register_types)
