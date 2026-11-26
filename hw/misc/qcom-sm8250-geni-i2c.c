/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * SM8250 GENI I2C empty-bus reference controller.
 *
 * Register sequencing and error handling are bound to the complete public
 * GENI I2C consumer/helper/header at revision 98719830 and the original
 * kernel's finite consumer functions. Instance placement comes from the
 * complete original DT. See tb371fc/docs/geni-i2c-reference-policy.json.
 *
 * This is an explicit virtual profile: protocol 3, version 0, sixteen
 * 32-bit FIFO words, idle pulled-up pins, and no attached peripheral.
 * Address commands fail synchronously with CMD_FAILURE | GP_IRQ_1 (NACK).
 * There is no firmware execution, successful payload, electrical timing,
 * DMA guest-memory access, GSI engine, or emulated child response.
 *
 * The consumer submits M_CMD0 before DMA setup. Descriptor writes therefore
 * only retain metadata; they cannot initiate or finish a transfer. DMA FSM
 * reset reports RESET_DONE, never DMA_DONE. The reference consumer's ignored
 * reset wait may expire; no success bit is fabricated to avoid that wait.
 *
 * The existing UART/HS implementation and migration ABI are independent.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-geni-i2c.h"
#include "hw/core/clock.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "system/runstate.h"

#include "qcom-sm8250-geni-i2c-data.inc"

#define BIT32(n) (UINT32_C(1) << (n))
#define M_ILLEGAL BIT32(2)
#define M_FAILURE BIT32(3)
#define M_CANCEL BIT32(4)
#define M_ABORT BIT32(5)
#define M_NACK BIT32(10)
#define DMA_RESET_DONE BIT32(3)
#define M_STATUS_MASK UINT32_C(0x0000043c)
#define M_ENABLE_MASK UINT32_C(0xffc07fff)
#define S_ENABLE_MASK UINT32_C(0x0fc07f3f)
#define DMA_TX_ENABLE_MASK UINT32_C(0x0000400f)
#define DMA_RX_ENABLE_MASK UINT32_C(0x000077ff)
#define PACK_CFG0 UINT32_C(0x0007f8fe)
#define PACK_CFG1 UINT32_C(0x000ffefe)

/*
 * Compact migration order. Unlisted offsets have no storage or readable
 * contents. Status registers occupy explicit slots but are never guest-RW.
 */
enum {
    I_FORCE_DEFAULT, I_OUTPUT_CTRL, I_CGC_CTRL, I_M_CLK_CFG, I_S_CLK_CFG,
    I_CLK_SEL, I_BYTE_GRAN, I_DMA_MODE, I_TX_PACK0, I_TX_PACK1,
    I_TX_LEN, I_RX_LEN, I_SCL_COUNTERS, I_RX_PACK0, I_RX_PACK1,
    I_M_CMD, I_M_STATUS, I_M_EN, I_S_EN, I_TX_WM, I_RX_WM, I_RX_RFR_WM,
    I_DMA_TX_PTR_L, I_DMA_TX_PTR_H, I_DMA_TX_ATTR, I_DMA_TX_LEN,
    I_DMA_TX_STATUS, I_DMA_TX_EN,
    I_DMA_RX_PTR_L, I_DMA_RX_PTR_H, I_DMA_RX_ATTR, I_DMA_RX_LEN,
    I_DMA_RX_STATUS, I_DMA_RX_EN, I_GSI_EVENT_EN, I_IRQ_EN, I_DMA_GENERAL_CFG,
    I_REGISTER_COUNT
};

typedef struct GeniI2CRegister {
    hwaddr offset;
    uint32_t mask;
} GeniI2CRegister;

static const GeniI2CRegister geni_i2c_registers[] = {
    [I_FORCE_DEFAULT] = { 0x020, 0x00000001 },
    [I_OUTPUT_CTRL] = { 0x024, 0x0000007f },
    [I_CGC_CTRL] = { 0x028, 0x0000037f },
    [I_M_CLK_CFG] = { 0x048, 0x0000fff1 },
    [I_S_CLK_CFG] = { 0x04c, 0x0000fff1 },
    [I_CLK_SEL] = { 0x07c, 0x00000007 },
    [I_BYTE_GRAN] = { 0x254, 0x00000000 },
    [I_DMA_MODE] = { 0x258, 0x00000001 },
    [I_TX_PACK0] = { 0x260, 0x000fffff },
    [I_TX_PACK1] = { 0x264, 0x000fffff },
    [I_TX_LEN] = { 0x26c, 0xffffffff },
    [I_RX_LEN] = { 0x270, 0xffffffff },
    [I_SCL_COUNTERS] = { 0x278, 0x3fffffff },
    [I_RX_PACK0] = { 0x284, 0x000fffff },
    [I_RX_PACK1] = { 0x288, 0x000fffff },
    [I_M_CMD] = { 0x600, 0xffffffff },
    [I_M_STATUS] = { 0x610, M_STATUS_MASK },
    [I_M_EN] = { 0x614, M_ENABLE_MASK },
    [I_S_EN] = { 0x644, S_ENABLE_MASK },
    [I_TX_WM] = { 0x80c, 0x0000003f },
    [I_RX_WM] = { 0x810, 0x0000003f },
    [I_RX_RFR_WM] = { 0x814, 0x0000003f },
    [I_DMA_TX_PTR_L] = { 0xc30, 0xffffffff },
    [I_DMA_TX_PTR_H] = { 0xc34, 0xffffffff },
    [I_DMA_TX_ATTR] = { 0xc38, 0x00000001 },
    [I_DMA_TX_LEN] = { 0xc3c, 0xffffffff },
    [I_DMA_TX_STATUS] = { 0xc40, DMA_RESET_DONE },
    [I_DMA_TX_EN] = { 0xc48, DMA_TX_ENABLE_MASK },
    [I_DMA_RX_PTR_L] = { 0xd30, 0xffffffff },
    [I_DMA_RX_PTR_H] = { 0xd34, 0xffffffff },
    [I_DMA_RX_ATTR] = { 0xd38, 0x00000000 },
    [I_DMA_RX_LEN] = { 0xd3c, 0xffffffff },
    [I_DMA_RX_STATUS] = { 0xd40, DMA_RESET_DONE },
    [I_DMA_RX_EN] = { 0xd48, DMA_RX_ENABLE_MASK },
    [I_GSI_EVENT_EN] = { 0xe18, 0x00000000 },
    [I_IRQ_EN] = { 0xe1c, 0x0000000f },
    [I_DMA_GENERAL_CFG] = { 0xe30, 0x000001ff },
};

typedef struct QcomGeniI2CProfile {
    uint32_t enabled;
    uint32_t identity;
    uint32_t instance;
    uint64_t base;
    uint32_t size;
    uint32_t spi;
    uint32_t wrapper;
    uint32_t main_revision;
    uint32_t secondary_revision;
    uint32_t main_config_revision;
    uint32_t secondary_config_revision;
    uint32_t fifo_words;
    uint32_t fifo_bits;
    uint32_t ios_idle;
    uint32_t slave_count;
    /* Local ownership is configuration, never serialized. */
    QcomGeniI2CState *owner;
} QcomGeniI2CProfile;

struct QcomGeniI2CState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq irq;
    Clock *se_clk;
    Clock *m_ahb;
    Clock *s_ahb;
    bool enabled;
    uint32_t instance_id;
    const QcomGeniI2CInstance *instance;
    uint32_t regs[QCOM_GENI_I2C_REGISTER_COUNT];
    uint32_t tx_fifo[QCOM_GENI_I2C_FIFO_WORDS];
    uint32_t tx_count;
    bool reset_input;
    bool reset_held;
    bool ready;
    bool loading;
    bool profile_seen;
    QcomGeniI2CProfile wire;
    uint8_t reset_wire;
    uint64_t input_periods[3];
    VMChangeStateEntry *vmchange;
};

static bool geni_i2c_bus_available(QcomGeniI2CState *s)
{
    return s->enabled && s->ready && !s->loading &&
           !s->reset_input && !s->reset_held &&
           clock_get(s->m_ahb) != 0 && clock_get(s->s_ahb) != 0;
}

static bool geni_i2c_engine_available(QcomGeniI2CState *s)
{
    return geni_i2c_bus_available(s) && clock_get(s->se_clk) != 0;
}

static bool geni_i2c_irq_level(QcomGeniI2CState *s)
{
    uint32_t gate = s->regs[I_IRQ_EN];

    return geni_i2c_engine_available(s) &&
           (((gate & BIT32(2)) &&
             (s->regs[I_M_STATUS] & s->regs[I_M_EN])) ||
            ((gate & BIT32(1)) &&
             (s->regs[I_DMA_TX_STATUS] & s->regs[I_DMA_TX_EN])) ||
            ((gate & BIT32(0)) &&
             (s->regs[I_DMA_RX_STATUS] & s->regs[I_DMA_RX_EN])));
}

static void geni_i2c_update_irq(QcomGeniI2CState *s)
{
    qemu_set_irq(s->irq, geni_i2c_irq_level(s));
}

static int geni_i2c_register_index(hwaddr offset)
{
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(geni_i2c_registers); i++) {
        if (geni_i2c_registers[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static bool geni_i2c_format_supported(int index, uint32_t value)
{
    switch (index) {
    case I_BYTE_GRAN:
    case I_DMA_RX_ATTR:
    case I_GSI_EVENT_EN:
        return value == 0;
    case I_TX_PACK0:
    case I_RX_PACK0:
        return value == 0 || value == PACK_CFG0;
    case I_TX_PACK1:
    case I_RX_PACK1:
        return value == 0 || value == PACK_CFG1;
    case I_DMA_TX_ATTR:
        return value <= 1;
    default:
        return true;
    }
}

static MemTxResult geni_i2c_reject(QcomGeniI2CState *s, hwaddr offset,
                                  unsigned size, bool write,
                                  const char *reason)
{
    qemu_log_mask(LOG_UNIMP | LOG_GUEST_ERROR,
                  TYPE_QCOM_SM8250_GENI_I2C
                  ": rejected %s instance=%u offset=0x%" HWADDR_PRIx
                  " size=%u reason=%s\n",
                  write ? "write" : "read", s->instance_id, offset,
                  size, reason);
    return MEMTX_ERROR;
}

static void geni_i2c_clear_fifo(QcomGeniI2CState *s)
{
    memset(s->tx_fifo, 0, sizeof(s->tx_fifo));
    s->tx_count = 0;
}

/*
 * A zero-time address phase is the declared reference policy. The bus has
 * no attached slave, so every supported address command gets NACK. Neither
 * prefilled FIFO data nor a late DMA descriptor can turn it into success.
 */
static void geni_i2c_address_command(QcomGeniI2CState *s, uint32_t value)
{
    uint32_t opcode = value >> 27;
    uint32_t params = value & UINT32_C(0x07ffffff);
    bool supported = opcode >= 1 && opcode <= 4 &&
                     !(params & ~UINT32_C(0x0000fe04));
    bool configured = (s->regs[I_M_CLK_CFG] & 1) &&
                      (s->regs[I_M_CLK_CFG] & UINT32_C(0x0000fff0));

    s->regs[I_M_CMD] = value;
    if (!supported || !configured) {
        s->regs[I_M_STATUS] |= M_FAILURE | M_ILLEGAL;
    } else {
        s->regs[I_M_STATUS] |= M_FAILURE | M_NACK;
    }
    geni_i2c_update_irq(s);
}

static MemTxResult geni_i2c_read(void *opaque, hwaddr offset,
                                uint64_t *value, unsigned size,
                                MemTxAttrs attrs)
{
    QcomGeniI2CState *s = opaque;
    int index;

    *value = 0;
    if (size != 4 || (offset & 3)) {
        return geni_i2c_reject(s, offset, size, false, "access-shape");
    }
    if (!geni_i2c_bus_available(s)) {
        return geni_i2c_reject(s, offset, size, false, "bus-unavailable");
    }
    index = geni_i2c_register_index(offset);
    if (index >= 0) {
        *value = s->regs[index];
        return MEMTX_OK;
    }
    switch (offset) {
    case 0x000: /* virtual main configuration revision */
    case 0x004: /* virtual secondary configuration revision */
    case 0x040: /* no outstanding sequencer command */
    case 0x064: /* FIFO interface available; no GSI-only advertisement */
    case 0x604: /* command-control requests are self-clearing */
    case 0x630: /* unused secondary sequencer */
    case 0x640: /* no secondary status generated */
    case 0x804: /* RX FIFO empty; no RX_LAST or valid-byte flags */
    case 0xc54: /* no DMA TX payload was transferred */
    case 0xd54: /* no DMA RX payload was transferred */
    case 0xe40: /* no DMA engine active */
        return MEMTX_OK;
    case 0x068:
    case 0x06c:
        *value = QCOM_GENI_I2C_FW_REVISION;
        return MEMTX_OK;
    case 0x800:
        *value = s->tx_count;
        return MEMTX_OK;
    case 0x908:
        *value = QCOM_GENI_I2C_IOS_IDLE;
        return MEMTX_OK;
    case 0xe24:
    case 0xe28:
        *value = QCOM_GENI_I2C_HW_PARAM;
        return MEMTX_OK;
    case 0x780:
        return geni_i2c_reject(s, offset, size, false, "rx-fifo-empty");
    default:
        return geni_i2c_reject(s, offset, size, false, "unknown-register");
    }
}

static MemTxResult geni_i2c_write(void *opaque, hwaddr offset,
                                 uint64_t value, unsigned size,
                                 MemTxAttrs attrs)
{
    QcomGeniI2CState *s = opaque;
    uint32_t word = value;
    int index;

    if (size != 4 || (offset & 3)) {
        return geni_i2c_reject(s, offset, size, true, "access-shape");
    }
    if (!geni_i2c_bus_available(s)) {
        return geni_i2c_reject(s, offset, size, true, "bus-unavailable");
    }
    if ((offset == 0x600 || offset == 0x604 ||
         offset == 0xc58 || offset == 0xd58) &&
        !geni_i2c_engine_available(s)) {
        return geni_i2c_reject(s, offset, size, true, "engine-unavailable");
    }
    index = geni_i2c_register_index(offset);
    if (index >= 0) {
        switch (index) {
        case I_M_STATUS:
        case I_DMA_TX_STATUS:
        case I_DMA_RX_STATUS:
            return geni_i2c_reject(s, offset, size, true, "read-only-status");
        case I_M_CMD:
            geni_i2c_address_command(s, word);
            return MEMTX_OK;
        default:
            if (!geni_i2c_format_supported(index, word)) {
                return geni_i2c_reject(s, offset, size, true,
                                       "unsupported-format");
            }
            /*
             * In particular SE_IRQ_EN=UINT32_MAX is a source-supported
             * enable-all request; reserved bits do not become state.
             */
            s->regs[index] = word & geni_i2c_registers[index].mask;
            geni_i2c_update_irq(s);
            return MEMTX_OK;
        }
    }
    switch (offset) {
    case 0x604:
        if (word != 0 && word != 1 && word != 2 && word != 4) {
            return geni_i2c_reject(s, offset, size, true,
                                   "unsupported-control");
        }
        if (word) {
            geni_i2c_clear_fifo(s);
        }
        if (word == 1) {
            /* Local sequencer disable, without a successful data event. */
            s->regs[I_M_STATUS] = 0;
        } else if (word == 2) {
            s->regs[I_M_STATUS] |= M_ABORT;
        } else if (word == 4) {
            s->regs[I_M_STATUS] |= M_CANCEL;
        }
        break;
    case 0x618:
        s->regs[I_M_STATUS] &= ~(word & M_STATUS_MASK);
        break;
    case 0x648:
        /* The initialization sequence clears this unused secondary view. */
        break;
    case 0x700:
        if (s->tx_count == QCOM_GENI_I2C_FIFO_WORDS) {
            return geni_i2c_reject(s, offset, size, true, "tx-fifo-full");
        }
        s->tx_fifo[s->tx_count++] = word;
        break;
    case 0xc44:
        s->regs[I_DMA_TX_STATUS] &= ~(word & DMA_RESET_DONE);
        break;
    case 0xc4c:
        s->regs[I_DMA_TX_EN] |= word & DMA_TX_ENABLE_MASK;
        break;
    case 0xc50:
        s->regs[I_DMA_TX_EN] &= ~(word & DMA_TX_ENABLE_MASK);
        break;
    case 0xc58:
        if (word != 1) {
            return geni_i2c_reject(s, offset, size, true,
                                   "unsupported-dma-reset");
        }
        s->regs[I_DMA_TX_PTR_L] = 0;
        s->regs[I_DMA_TX_PTR_H] = 0;
        s->regs[I_DMA_TX_ATTR] = 0;
        s->regs[I_DMA_TX_LEN] = 0;
        s->regs[I_DMA_TX_STATUS] |= DMA_RESET_DONE;
        break;
    case 0xd44:
        s->regs[I_DMA_RX_STATUS] &= ~(word & DMA_RESET_DONE);
        break;
    case 0xd4c:
        s->regs[I_DMA_RX_EN] |= word & DMA_RX_ENABLE_MASK;
        break;
    case 0xd50:
        s->regs[I_DMA_RX_EN] &= ~(word & DMA_RX_ENABLE_MASK);
        break;
    case 0xd58:
        if (word != 1) {
            return geni_i2c_reject(s, offset, size, true,
                                   "unsupported-dma-reset");
        }
        s->regs[I_DMA_RX_PTR_L] = 0;
        s->regs[I_DMA_RX_PTR_H] = 0;
        s->regs[I_DMA_RX_ATTR] = 0;
        s->regs[I_DMA_RX_LEN] = 0;
        s->regs[I_DMA_RX_STATUS] |= DMA_RESET_DONE;
        break;
    default:
        return geni_i2c_reject(s, offset, size, true,
                               "read-only-or-unknown-register");
    }
    geni_i2c_update_irq(s);
    return MEMTX_OK;
}

/*
 * Preserve a single delivered 1..8-byte MMIO transaction for rejection.
 * Restricting impl to four bytes here would split an eight-byte write.
 * An arbitrary bulk buffer may already be split by the address-space caller;
 * this does not promise atomic rejection of that larger caller operation.
 */
static const MemoryRegionOps geni_i2c_ops = {
    .read_with_attrs = geni_i2c_read,
    .write_with_attrs = geni_i2c_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void geni_i2c_clear_functional_state(QcomGeniI2CState *s)
{
    memset(s->regs, 0, sizeof(s->regs));
    geni_i2c_clear_fifo(s);
}

static void geni_i2c_reset(DeviceState *dev)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(dev);

    geni_i2c_clear_functional_state(s);
    s->reset_held = s->reset_input;
    memory_region_set_enabled(&s->mmio, s->enabled);
    geni_i2c_update_irq(s);
}

static void geni_i2c_clock_changed(void *opaque, ClockEvent event)
{
    QcomGeniI2CState *s = opaque;

    if (s->ready && !s->loading && runstate_is_running()) {
        /* Restoring clocks never submits, retries, or finishes a command. */
        geni_i2c_update_irq(s);
    }
}

static void geni_i2c_reset_input(void *opaque, int n, int level)
{
    QcomGeniI2CState *s = opaque;
    bool asserted = !!level;

    s->reset_input = asserted;
    if (s->ready && (s->loading || !runstate_is_running())) {
        /* The provider restores before the consumer validates its wire. */
        return;
    }
    if (asserted && !s->reset_held) {
        geni_i2c_clear_functional_state(s);
    }
    s->reset_held = asserted;
    geni_i2c_update_irq(s);
}

static void geni_i2c_vm_change(void *opaque, bool running, RunState state)
{
    QcomGeniI2CState *s = opaque;

    if (!running || s->loading) {
        return;
    }
    if (s->reset_held != s->reset_input) {
        if (s->reset_input) {
            geni_i2c_clear_functional_state(s);
        }
        s->reset_held = s->reset_input;
    }
    geni_i2c_update_irq(s);
}

static QcomGeniI2CProfile geni_i2c_expected_profile(QcomGeniI2CState *s)
{
    QcomGeniI2CProfile p = {
        .enabled = s->enabled,
        .identity = QCOM_GENI_I2C_PROFILE_ID,
        .instance = s->instance_id,
        .base = s->instance->base,
        .size = QCOM_GENI_I2C_WINDOW_BYTES,
        .spi = s->instance->spi,
        .wrapper = s->instance->wrapper,
        .main_revision = QCOM_GENI_I2C_FW_REVISION,
        .secondary_revision = QCOM_GENI_I2C_FW_REVISION,
        .main_config_revision = 0,
        .secondary_config_revision = 0,
        .fifo_words = QCOM_GENI_I2C_FIFO_WORDS,
        .fifo_bits = QCOM_GENI_I2C_FIFO_BITS,
        .ios_idle = QCOM_GENI_I2C_IOS_IDLE,
        .slave_count = 0,
        .owner = s,
    };
    return p;
}

static int geni_i2c_profile_post_load(void *opaque, int version_id)
{
    QcomGeniI2CProfile *p = opaque;
    QcomGeniI2CState *s = p->owner;
    QcomGeniI2CProfile e = geni_i2c_expected_profile(s);

    if (p->enabled != e.enabled || p->identity != e.identity ||
        p->instance != e.instance || p->base != e.base ||
        p->size != e.size || p->spi != e.spi || p->wrapper != e.wrapper ||
        p->main_revision != e.main_revision ||
        p->secondary_revision != e.secondary_revision ||
        p->main_config_revision != e.main_config_revision ||
        p->secondary_config_revision != e.secondary_config_revision ||
        p->fifo_words != e.fifo_words || p->fifo_bits != e.fifo_bits ||
        p->ios_idle != e.ios_idle || p->slave_count != e.slave_count) {
        error_report(TYPE_QCOM_SM8250_GENI_I2C ": invalid migration profile");
        return -EINVAL;
    }
    s->profile_seen = true;
    return 0;
}

static const VMStateDescription vmstate_geni_i2c_profile = {
    .name = TYPE_QCOM_SM8250_GENI_I2C "/profile",
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = geni_i2c_profile_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(enabled, QcomGeniI2CProfile),
        VMSTATE_UINT32(identity, QcomGeniI2CProfile),
        VMSTATE_UINT32(instance, QcomGeniI2CProfile),
        VMSTATE_UINT64(base, QcomGeniI2CProfile),
        VMSTATE_UINT32(size, QcomGeniI2CProfile),
        VMSTATE_UINT32(spi, QcomGeniI2CProfile),
        VMSTATE_UINT32(wrapper, QcomGeniI2CProfile),
        VMSTATE_UINT32(main_revision, QcomGeniI2CProfile),
        VMSTATE_UINT32(secondary_revision, QcomGeniI2CProfile),
        VMSTATE_UINT32(main_config_revision, QcomGeniI2CProfile),
        VMSTATE_UINT32(secondary_config_revision, QcomGeniI2CProfile),
        VMSTATE_UINT32(fifo_words, QcomGeniI2CProfile),
        VMSTATE_UINT32(fifo_bits, QcomGeniI2CProfile),
        VMSTATE_UINT32(ios_idle, QcomGeniI2CProfile),
        VMSTATE_UINT32(slave_count, QcomGeniI2CProfile),
        VMSTATE_END_OF_LIST()
    },
};

static int geni_i2c_pre_save(void *opaque)
{
    QcomGeniI2CState *s = opaque;

    if (s->loading || s->reset_held != s->reset_input) {
        error_report(TYPE_QCOM_SM8250_GENI_I2C
                     ": cannot save an unvalidated or unsettled reset state");
        return -EINVAL;
    }
    s->wire = geni_i2c_expected_profile(s);
    s->reset_wire = s->reset_held;
    s->input_periods[0] = clock_get(s->se_clk);
    s->input_periods[1] = clock_get(s->m_ahb);
    s->input_periods[2] = clock_get(s->s_ahb);
    return 0;
}

static int geni_i2c_pre_load(void *opaque)
{
    QcomGeniI2CState *s = opaque;

    s->loading = true;
    s->profile_seen = false;
    qemu_set_irq(s->irq, 0);
    return 0;
}

static int geni_i2c_post_load(void *opaque, int version_id)
{
    QcomGeniI2CState *s = opaque;
    Clock *clocks[] = { s->se_clk, s->m_ahb, s->s_ahb };
    unsigned i;

    if (!s->profile_seen || s->reset_wire > 1 ||
        s->reset_wire != s->reset_input ||
        s->tx_count > QCOM_GENI_I2C_FIFO_WORDS) {
        goto invalid;
    }
    for (i = 0; i < ARRAY_SIZE(geni_i2c_registers); i++) {
        if ((s->regs[i] & ~geni_i2c_registers[i].mask) ||
            !geni_i2c_format_supported(i, s->regs[i]) ||
            ((!s->enabled || s->reset_wire) && s->regs[i])) {
            goto invalid;
        }
    }
    for (i = 0; i < QCOM_GENI_I2C_FIFO_WORDS; i++) {
        if ((i >= s->tx_count && s->tx_fifo[i]) ||
            ((!s->enabled || s->reset_wire) && s->tx_fifo[i])) {
            goto invalid;
        }
    }
    if ((!s->enabled || s->reset_wire) && s->tx_count) {
        goto invalid;
    }
    for (i = 0; i < ARRAY_SIZE(clocks); i++) {
        /* Shared providers may run even for a disabled/reset controller. */
        if (!clock_has_source(clocks[i]) ||
            s->input_periods[i] != clock_get(clocks[i])) {
            goto invalid;
        }
    }
    s->reset_held = s->reset_wire;
    s->loading = false;
    geni_i2c_update_irq(s);
    return 0;

invalid:
    error_report(TYPE_QCOM_SM8250_GENI_I2C ": invalid migration state");
    return -EINVAL;
}

static const VMStateDescription vmstate_geni_i2c = {
    .name = TYPE_QCOM_SM8250_GENI_I2C,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = geni_i2c_pre_save,
    .pre_load = geni_i2c_pre_load,
    .post_load = geni_i2c_post_load,
    .fields = (const VMStateField[]) {
        /* The 64-byte immutable profile is checked before functional data. */
        VMSTATE_STRUCT(wire, QcomGeniI2CState, 1,
                       vmstate_geni_i2c_profile, QcomGeniI2CProfile),
        VMSTATE_UINT32_ARRAY(regs, QcomGeniI2CState,
                             QCOM_GENI_I2C_REGISTER_COUNT),
        VMSTATE_UINT32_ARRAY(tx_fifo, QcomGeniI2CState,
                             QCOM_GENI_I2C_FIFO_WORDS),
        VMSTATE_UINT32(tx_count, QcomGeniI2CState),
        VMSTATE_UINT8(reset_wire, QcomGeniI2CState),
        VMSTATE_UINT64_ARRAY(input_periods, QcomGeniI2CState, 3),
        VMSTATE_END_OF_LIST()
    },
};

enum {
    GET_IDENTITY, GET_INSTANCE, GET_BASE, GET_SIZE, GET_SPI, GET_WRAPPER,
    GET_MAIN_REVISION, GET_SECONDARY_REVISION, GET_CONFIG_REVISION,
    GET_FIFO_WORDS, GET_FIFO_BITS, GET_IOS_IDLE, GET_TX_COUNT,
    GET_SE_PERIOD, GET_M_PERIOD, GET_S_PERIOD, GET_VMSTATE_BYTES,
    GET_PROFILE_BYTES, GET_ZERO,
    GET_IRQ_LEVEL, GET_RESET_HELD, GET_BUS_AVAILABLE, GET_ENGINE_AVAILABLE,
    GET_FALSE, GET_TRUE,
};

static void geni_i2c_get_uint(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);
    const QcomGeniI2CInstance *instance =
        qcom_geni_i2c_instance_by_id(s->instance_id);
    uint64_t value;

    switch ((uintptr_t)opaque) {
    case GET_IDENTITY: value = QCOM_GENI_I2C_PROFILE_ID; break;
    case GET_INSTANCE: value = s->instance_id; break;
    case GET_BASE: value = instance ? instance->base : 0; break;
    case GET_SIZE: value = QCOM_GENI_I2C_WINDOW_BYTES; break;
    case GET_SPI: value = instance ? instance->spi : 0; break;
    case GET_WRAPPER: value = instance ? instance->wrapper : 0; break;
    case GET_MAIN_REVISION:
    case GET_SECONDARY_REVISION: value = QCOM_GENI_I2C_FW_REVISION; break;
    case GET_CONFIG_REVISION:
    case GET_ZERO: value = 0; break;
    case GET_FIFO_WORDS: value = QCOM_GENI_I2C_FIFO_WORDS; break;
    case GET_FIFO_BITS: value = QCOM_GENI_I2C_FIFO_BITS; break;
    case GET_IOS_IDLE: value = QCOM_GENI_I2C_IOS_IDLE; break;
    case GET_TX_COUNT: value = s->tx_count; break;
    case GET_SE_PERIOD: value = clock_get(s->se_clk); break;
    case GET_M_PERIOD: value = clock_get(s->m_ahb); break;
    case GET_S_PERIOD: value = clock_get(s->s_ahb); break;
    case GET_VMSTATE_BYTES: value = QCOM_GENI_I2C_VMSTATE_BYTES; break;
    case GET_PROFILE_BYTES: value = QCOM_GENI_I2C_PROFILE_BYTES; break;
    default: g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
}

static void geni_i2c_get_bool(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);
    bool value;

    switch ((uintptr_t)opaque) {
    case GET_IRQ_LEVEL: value = geni_i2c_irq_level(s); break;
    case GET_RESET_HELD: value = s->reset_held; break;
    case GET_BUS_AVAILABLE: value = geni_i2c_bus_available(s); break;
    case GET_ENGINE_AVAILABLE: value = geni_i2c_engine_available(s); break;
    case GET_FALSE: value = false; break;
    case GET_TRUE: value = true; break;
    default: g_assert_not_reached();
    }
    visit_type_bool(v, name, &value, errp);
}

static char *geni_i2c_profile_name(Object *obj, Error **errp)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);

    return g_strdup(s->enabled ? "empty-bus-v1" : "disabled");
}

static char *geni_i2c_registers_hex(Object *obj, Error **errp)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);
    GString *str = g_string_sized_new(QCOM_GENI_I2C_REGISTER_COUNT * 8);
    unsigned i;

    for (i = 0; i < QCOM_GENI_I2C_REGISTER_COUNT; i++) {
        g_string_append_printf(str, "%08x", s->regs[i]);
    }
    return g_string_free(str, false);
}

static char *geni_i2c_fifo_hex(Object *obj, Error **errp)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);
    GString *str = g_string_sized_new(QCOM_GENI_I2C_FIFO_WORDS * 8);
    unsigned i;

    for (i = 0; i < QCOM_GENI_I2C_FIFO_WORDS; i++) {
        g_string_append_printf(str, "%08x", s->tx_fifo[i]);
    }
    return g_string_free(str, false);
}

static void geni_i2c_add_pure_properties(Object *obj)
{
    static const struct { const char *name; unsigned id; } ints[] = {
        { "reference-identity", GET_IDENTITY },
        { "instance-id-readonly", GET_INSTANCE },
        { "physical-base", GET_BASE }, { "window-bytes", GET_SIZE },
        { "interrupt-spi", GET_SPI }, { "wrapper-index", GET_WRAPPER },
        { "main-fw-revision", GET_MAIN_REVISION },
        { "secondary-fw-revision", GET_SECONDARY_REVISION },
        { "main-config-revision", GET_CONFIG_REVISION },
        { "secondary-config-revision", GET_CONFIG_REVISION },
        { "fifo-words", GET_FIFO_WORDS }, { "fifo-word-bits", GET_FIFO_BITS },
        { "ios-idle", GET_IOS_IDLE }, { "tx-count", GET_TX_COUNT },
        { "se-clock-period", GET_SE_PERIOD },
        { "m-ahb-clock-period", GET_M_PERIOD },
        { "s-ahb-clock-period", GET_S_PERIOD },
        { "vmstate-bytes", GET_VMSTATE_BYTES },
        { "profile-wire-bytes", GET_PROFILE_BYTES },
        { "attached-slaves", GET_ZERO }, { "tx-payload-bytes", GET_ZERO },
        { "rx-payload-bytes", GET_ZERO }, { "dma-read-bytes", GET_ZERO },
        { "dma-write-bytes", GET_ZERO },
    }, bools[] = {
        { "irq-level", GET_IRQ_LEVEL }, { "reset-held", GET_RESET_HELD },
        { "bus-available", GET_BUS_AVAILABLE },
        { "engine-available", GET_ENGINE_AVAILABLE },
        { "command-active", GET_FALSE }, { "firmware-executed", GET_FALSE },
        { "physical-state-captured", GET_FALSE },
        { "dma-data-modeled", GET_FALSE }, { "address-phase-modeled", GET_TRUE },
    };
    unsigned i;

    object_property_add_str(obj, "profile", geni_i2c_profile_name, NULL);
    object_property_add_str(obj, "registers-hex", geni_i2c_registers_hex, NULL);
    object_property_add_str(obj, "tx-fifo-hex", geni_i2c_fifo_hex, NULL);
    for (i = 0; i < ARRAY_SIZE(ints); i++) {
        object_property_add(obj, ints[i].name, "uint64", geni_i2c_get_uint,
                            NULL, NULL, (void *)(uintptr_t)ints[i].id);
    }
    for (i = 0; i < ARRAY_SIZE(bools); i++) {
        object_property_add(obj, bools[i].name, "bool", geni_i2c_get_bool,
                            NULL, NULL, (void *)(uintptr_t)bools[i].id);
    }
}

static const Property geni_i2c_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomGeniI2CState, enabled, false),
    DEFINE_PROP_UINT32("instance-id", QcomGeniI2CState, instance_id, UINT32_MAX),
};

static void geni_i2c_realize(DeviceState *dev, Error **errp)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(dev);

    QEMU_BUILD_BUG_ON(I_REGISTER_COUNT != QCOM_GENI_I2C_REGISTER_COUNT);
    QEMU_BUILD_BUG_ON(ARRAY_SIZE(geni_i2c_registers) !=
                      QCOM_GENI_I2C_REGISTER_COUNT);
    s->instance = qcom_geni_i2c_instance_by_id(s->instance_id);
    if (!s->instance) {
        error_setg(errp, TYPE_QCOM_SM8250_GENI_I2C
                   ": instance-id must name an original-DT enabled instance");
        return;
    }
    if (!clock_has_source(s->se_clk) || !clock_has_source(s->m_ahb) ||
        !clock_has_source(s->s_ahb)) {
        error_setg(errp, TYPE_QCOM_SM8250_GENI_I2C
                   ": explicit SE, M-AHB and S-AHB clock inputs are required");
        return;
    }
    s->wire = geni_i2c_expected_profile(s);
    s->ready = true;
    memory_region_set_enabled(&s->mmio, s->enabled);
    s->vmchange = qemu_add_vm_change_state_handler(geni_i2c_vm_change, s);
    geni_i2c_update_irq(s);
}

static void geni_i2c_init(Object *obj)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);

    s->wire.owner = s;
    memory_region_init_io(&s->mmio, obj, &geni_i2c_ops, s,
                          TYPE_QCOM_SM8250_GENI_I2C,
                          QCOM_GENI_I2C_WINDOW_BYTES);
    memory_region_set_enabled(&s->mmio, false);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    s->se_clk = qdev_init_clock_in(DEVICE(obj), "se-clk",
                                  geni_i2c_clock_changed, s, ClockUpdate);
    s->m_ahb = qdev_init_clock_in(DEVICE(obj), "m-ahb",
                                 geni_i2c_clock_changed, s, ClockUpdate);
    s->s_ahb = qdev_init_clock_in(DEVICE(obj), "s-ahb",
                                 geni_i2c_clock_changed, s, ClockUpdate);
    qdev_init_gpio_in_named(DEVICE(obj), geni_i2c_reset_input, "reset", 1);
    geni_i2c_add_pure_properties(obj);
}

static void geni_i2c_finalize(Object *obj)
{
    QcomGeniI2CState *s = QCOM_SM8250_GENI_I2C(obj);

    if (s->vmchange) {
        qemu_del_vm_change_state_handler(s->vmchange);
    }
}

static void geni_i2c_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "SM8250 GENI I2C empty-bus reference controller (NACK, no DMA payload)";
    dc->realize = geni_i2c_realize;
    dc->vmsd = &vmstate_geni_i2c;
    device_class_set_legacy_reset(dc, geni_i2c_reset);
    device_class_set_props(dc, geni_i2c_properties);
}

static const TypeInfo geni_i2c_type = {
    .name = TYPE_QCOM_SM8250_GENI_I2C,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomGeniI2CState),
    .instance_init = geni_i2c_init,
    .instance_finalize = geni_i2c_finalize,
    .class_init = geni_i2c_class_init,
};

static void geni_i2c_register_types(void)
{
    type_register_static(&geni_i2c_type);
}

type_init(geni_i2c_register_types)
