/*
 * Qualcomm SM8250 UFS host wrapper and QMP v4 PHY digital state.
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * Uses QEMU's upstream UFS transport/SCSI implementation. Register contracts
 * come from Linux v6.12.114 ufs-qcom, ufshci/unipro and phy-qcom-qmp-ufs.
 * This does not implement analog calibration, ICE, RPMB or vendor firmware.
 * Version defaults are reference configurations, not tablet register dumps.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "hw/core/clock.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/ufs/qcom-sm8250-ufs.h"
#include "migration/qemu-file.h"
#include "migration/vmstate.h"
#include "system/address-spaces.h"
#include "system/block-backend.h"
#include "ufs.h"

#define PHY_RX0_INTERFACE_MODE 0x734
#define PHY_RX1_INTERFACE_MODE 0xb34
#define PHY_START       0xc00
#define PHY_POWER       0xc04
#define PHY_RESET       0xc08
#define PHY_READY       0xd80
#define V_CFG1          0xdc
#define V_HW_VERSION    0xe4
#define V_CFG2          0xe0
#define UFS_PHY_RESET   BIT(1)
#define PHY_READY_NS    10000
#define UIC_COMPLETE_NS 1000
#define AHIT_TIMER_MASK 0x3ff
#define AHIT_SCALE_MASK 0x1c00
#define AHIT_MASK       (AHIT_TIMER_MASK | AHIT_SCALE_MASK)
#define AHIT_MAX_SCALE  5
#define DME_VALUES      256
#define PHY_WORDS       (QCOM_SM8250_UFS_PHY_SIZE / 4)

typedef struct QcomUfsPhyRegister {
    uint16_t offset;
    bool required;
} QcomUfsPhyRegister;

#include "qcom-sm8250-ufs-phy.inc"

typedef enum QcomUfsLink {
    LINK_DOWN, LINK_ACTIVE, LINK_HIBERN8,
} QcomUfsLink;

typedef struct QcomUfsDmeValue {
    uint16_t id, selector;
    uint32_t value;
    bool peer, used;
} QcomUfsDmeValue;

struct QcomSm8250UfsState {
    SysBusDevice parent_obj;
    UfsHc ufs;
    MemoryRegion hci, phy;
    MemoryRegion *dma_memory;
    AddressSpace dma_as;
    Clock *core_clk, *iface_clk, *unipro_clk, *ref_clk, *phy_aux_clk;
    Clock *aggre_clk, *qref_clk, *sync_clk[3];
    Clock *symbol_clk[3];
    QEMUTimer *phy_timer, *uic_timer, *ahit_timer;
    qemu_irq irq;
    uint32_t vendor[QCOM_SM8250_UFS_HCI_SIZE / 4];
    uint32_t phy_regs[PHY_WORDS];
    bool phy_written[PHY_WORDS];
    QcomUfsDmeValue dme[DME_VALUES];
    uint32_t hw_version, hci_version, symbol_hz;
    uint32_t uic_cmd, uic_args[3];
    QcomUfsLink link;
    bool power_on, reset_held, device_reset_held;
    bool phy_ready, initialized, uic_busy;
    bool auto_hibern8, auto_hibern8_active;
    uint8_t migration_config[32];
};

static void qcom_ufs_update_phy(QcomSm8250UfsState *s);
static void qcom_ufs_update_ahit(QcomSm8250UfsState *s);

/*
 * The transport notifies its IRQ output after completing a request even when
 * IE is zero. Observe that callback to start AHIT at the actual completion,
 * without polling or treating a masked IRQ as an outstanding request.
 */
static void qcom_ufs_core_irq(void *opaque, int n, int level)
{
    QcomSm8250UfsState *s = opaque;

    qcom_ufs_update_ahit(s);
    qemu_set_irq(s->irq, level);
}

static void qcom_ufs_irq(QcomSm8250UfsState *s)
{
    qemu_set_irq(s->ufs.irq, (s->ufs.reg.is & s->ufs.reg.ie) != 0);
}

static bool qcom_ufs_host_accessible(QcomSm8250UfsState *s)
{
    return s->power_on && !s->reset_held &&
           clock_get_hz(s->iface_clk) != 0;
}

static bool qcom_ufs_host_running(QcomSm8250UfsState *s)
{
    return qcom_ufs_host_accessible(s) &&
           clock_get_hz(s->core_clk) && clock_get_hz(s->unipro_clk) &&
           clock_get_hz(s->aggre_clk);
}

static bool qcom_ufs_link_running(QcomSm8250UfsState *s)
{
    return qcom_ufs_host_running(s) && s->phy_ready &&
           !s->device_reset_held && clock_get_hz(s->sync_clk[0]) &&
           clock_get_hz(s->sync_clk[1]) && clock_get_hz(s->sync_clk[2]);
}

static int64_t qcom_ufs_ahit_ns(QcomSm8250UfsState *s)
{
    uint32_t ahit = s->ufs.reg.ahit;
    int64_t ns = (ahit & AHIT_TIMER_MASK) * 1000;
    unsigned scale = (ahit & AHIT_SCALE_MASK) >> 10;

    while (scale--) {
        ns *= 10;
    }
    return ns;
}

static bool qcom_ufs_queues_idle(QcomSm8250UfsState *s)
{
    UfsHc *u = &s->ufs;
    unsigned i;

    if (u->resetting || u->reg.utrldbr || u->reg.utmrldbr) {
        return false;
    }
    /* A submitted doorbell has READY slots before its BH sets UTRLDBR. */
    for (i = 0; i < u->params.nutrs; i++) {
        if (u->req_list[i].state != UFS_REQUEST_IDLE) {
            return false;
        }
    }
    return true;
}

static bool qcom_ufs_ahit_idle(QcomSm8250UfsState *s)
{
    return s->initialized && s->auto_hibern8 &&
           (s->ufs.reg.ahit & AHIT_TIMER_MASK) && (s->ufs.reg.hce & 1) &&
           s->link == LINK_ACTIVE && qcom_ufs_link_running(s) &&
           !s->uic_busy && qcom_ufs_queues_idle(s);
}

static void qcom_ufs_update_ahit(QcomSm8250UfsState *s)
{
    if (!s->initialized) {
        return;
    }
    if (!qcom_ufs_ahit_idle(s)) {
        timer_del(s->ahit_timer);
    } else if (!timer_pending(s->ahit_timer)) {
        timer_mod(s->ahit_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  qcom_ufs_ahit_ns(s));
    }
}

static void qcom_ufs_auto_enter(void *opaque)
{
    QcomSm8250UfsState *s = opaque;

    if (qcom_ufs_ahit_idle(s)) {
        s->link = LINK_HIBERN8;
        s->ufs.link_hibern8 = true;
        s->auto_hibern8_active = true;
        /* Successful automatic transitions do not signal a UIC command. */
    }
}

static bool qcom_ufs_auto_exit(QcomSm8250UfsState *s)
{
    if (!s->auto_hibern8_active) {
        return true;
    }
    if (!qcom_ufs_link_running(s)) {
        return false;
    }
    s->link = LINK_ACTIVE;
    s->ufs.link_hibern8 = false;
    s->auto_hibern8_active = false;
    return true;
}

static bool qcom_ufs_phy_can_start(QcomSm8250UfsState *s)
{
    unsigned i;

    if (!s->initialized || !s->power_on || s->reset_held ||
        (s->vendor[V_CFG1 / 4] & UFS_PHY_RESET) ||
        !clock_get_hz(s->ref_clk) || !clock_get_hz(s->phy_aux_clk) ||
        !clock_get_hz(s->qref_clk) ||
        !(s->phy_regs[PHY_POWER / 4] & 1) ||
        (s->phy_regs[PHY_RESET / 4] & 1) ||
        !(s->phy_regs[PHY_START / 4] & 1)) {
        return false;
    }
    /* Require the real driver's complete base programming sequence. */
    for (i = 0; i < ARRAY_SIZE(qcom_ufs_phy_registers); i++) {
        const QcomUfsPhyRegister *r = &qcom_ufs_phy_registers[i];
        if (r->required && !s->phy_written[r->offset / 4]) {
            return false;
        }
    }
    return true;
}

static void qcom_ufs_symbols(QcomSm8250UfsState *s)
{
    unsigned i;

    /*
     * Explicit digital reference rate; physical M-PHY symbol timing is not
     * inferred from the sampled gear. No payload bandwidth claim is made.
     */
    for (i = 0; i < ARRAY_SIZE(s->symbol_clk); i++) {
        clock_update_hz(s->symbol_clk[i], s->phy_ready ? s->symbol_hz : 0);
    }
}

static void qcom_ufs_drop_link(QcomSm8250UfsState *s)
{
    timer_del(s->ahit_timer);
    s->link = LINK_DOWN;
    s->ufs.link_hibern8 = false;
    s->auto_hibern8_active = false;
    s->ufs.reg.hcs &= ~(R_HCS_DP_MASK | R_HCS_UTRLRDY_MASK |
                         R_HCS_UTMRLRDY_MASK);
}

static void qcom_ufs_phy_ready(void *opaque)
{
    QcomSm8250UfsState *s = opaque;

    if (qcom_ufs_phy_can_start(s)) {
        s->phy_ready = true;
        qcom_ufs_symbols(s);
        qcom_ufs_update_ahit(s);
    }
}

static void qcom_ufs_update_phy(QcomSm8250UfsState *s)
{
    if (!s->initialized) {
        return;
    }
    if (!qcom_ufs_phy_can_start(s)) {
        timer_del(s->phy_timer);
        if (s->phy_ready) {
            s->phy_ready = false;
            qcom_ufs_symbols(s);
            /* Linux powers the PHY off while retaining a Hibern8 link. */
            if (s->link != LINK_HIBERN8) {
                qcom_ufs_drop_link(s);
            }
        }
    } else if (!s->phy_ready && !timer_pending(s->phy_timer)) {
        timer_mod(s->phy_timer,
                   qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + PHY_READY_NS);
    }
}

static void qcom_ufs_clock_changed(void *opaque, ClockEvent event)
{
    QcomSm8250UfsState *s = opaque;

    qcom_ufs_update_phy(s);
    qcom_ufs_update_ahit(s);
}

static const QcomUfsPhyRegister *qcom_ufs_phy_reg(hwaddr offset)
{
    size_t i;

    for (i = 0; i < ARRAY_SIZE(qcom_ufs_phy_registers); i++) {
        if (qcom_ufs_phy_registers[i].offset == offset) {
            return &qcom_ufs_phy_registers[i];
        }
    }
    return NULL;
}

static bool qcom_ufs_phy_preclock_control(hwaddr offset)
{
    /*
     * The vendor power-control sequence programs these controls before it
     * enables ref/aux clocks. Register access does not make the PHY ready.
     */
    return offset == PHY_RX0_INTERFACE_MODE ||
           offset == PHY_RX1_INTERFACE_MODE || offset == PHY_POWER;
}

static MemTxResult qcom_ufs_phy_read(void *opaque, hwaddr offset,
                                   uint64_t *value, unsigned size,
                                   MemTxAttrs attrs)
{
    QcomSm8250UfsState *s = opaque;

    *value = 0;
    if (!s->power_on || s->reset_held ||
        (!qcom_ufs_phy_preclock_control(offset) &&
         (!clock_get_hz(s->ref_clk) || !clock_get_hz(s->phy_aux_clk)))) {
        return MEMTX_ERROR;
    }
    if (!qcom_ufs_phy_reg(offset)) {
        qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unknown PHY read 0x%"
                      HWADDR_PRIx "\n", offset);
        return MEMTX_ERROR;
    }
    *value = offset == PHY_READY ? s->phy_ready : s->phy_regs[offset / 4];
    return MEMTX_OK;
}

static MemTxResult qcom_ufs_phy_write(void *opaque, hwaddr offset,
                                    uint64_t value, unsigned size,
                                    MemTxAttrs attrs)
{
    QcomSm8250UfsState *s = opaque;

    if (!s->power_on || s->reset_held ||
        (!qcom_ufs_phy_preclock_control(offset) &&
         (!clock_get_hz(s->ref_clk) || !clock_get_hz(s->phy_aux_clk)))) {
        return MEMTX_ERROR;
    }
    if (!qcom_ufs_phy_reg(offset) || offset == PHY_READY || value > 0xff) {
        qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unsupported PHY write 0x%"
                      HWADDR_PRIx " = 0x%" PRIx64 "\n", offset, value);
        return MEMTX_ERROR;
    }
    s->phy_regs[offset / 4] = value;
    s->phy_written[offset / 4] = true;
    qcom_ufs_update_phy(s);
    qcom_ufs_update_ahit(s);
    return MEMTX_OK;
}

/*
 * The writable MIBs used by Linux's link/power negotiation. Unknown IDs fail
 * rather than becoming a successful zero-valued shadow register.
 */
static bool qcom_ufs_dme_writable(uint16_t id)
{
    switch (id) {
    case 0x002c: /* TX_LCC_ENABLE, one selector per TX lane */
    case 0x155e: /* PA_LOCAL_TX_LCC_ENABLE */
    case 0x1560: /* PA_ACTIVETXDATALANES */
    case 0x1562: /* PA_TXFORCECLOCK */
    case 0x1564: /* PA_TXTRAILINGCLOCKS */
    case 0x1568: /* PA_TXGEAR */
    case 0x1569: /* PA_TXTERMINATION */
    case 0x156a: /* PA_HSSERIES */
    case 0x1571: /* PA_PWRMODE */
    case 0x1580: /* PA_ACTIVERXDATALANES */
    case 0x1583: /* PA_RXGEAR */
    case 0x1584: /* PA_RXTERMINATION */
    case 0x1590: /* PA_PACPREQTIMEOUT */
    case 0x1591: /* PA_PACPREQEOBTIMEOUT */
    case 0x15a7: /* PA_HIBERN8TIME */
    case 0x15a8: /* PA_TACTIVATE */
    case 0x15b0: case 0x15b1: case 0x15b2:
    case 0x15b3: case 0x15b4: case 0x15b5:
    case 0x15d4: /* PA_TXHSADAPTTYPE */
    case 0x2040: /* DL_FC0PROTTIMEOUTVAL */
    case 0x2041: /* DL_TC0REPLAYTIMEOUTVAL */
    case 0x2042: /* DL_AFC0REQTIMEOUTVAL */
    case 0xd041: /* DME_LocalFC0ProtectionTimeOutVal */
    case 0xd042: /* DME_LocalTC0ReplayTimeOutVal */
    case 0xd043: /* DME_LocalAFC0ReqTimeOutVal */
    case 0xd002: /* DME_VS_CORE_CLK_CTRL */
    case 0x9000: /* PA_VS_CONFIG_REG1 */
    case 0x9007: /* PA_VS_CORE_CLK_40NS_CYCLES */
        return true;
    default:
        return false;
    }
}

static int qcom_ufs_dme_default(QcomSm8250UfsState *s, uint16_t id,
                                uint16_t selector, uint32_t *value)
{
    if ((id < 0x1000 && selector > 1) || (id >= 0x1000 && selector)) {
        return UFS_UIC_CMD_RESULT_BAD_INDEX;
    }
    switch (id) {
    case 0x0041:
        if (!s->phy_ready || s->link == LINK_ACTIVE) {
            return UFS_UIC_CMD_RESULT_DME_FAILURE;
        }
        *value = 1; /* Documented TX_FSM_HIBERN8 observation. */
        return 0;
    case 0x1520: case 0x1540: /* available TX/RX lanes */
        *value = 2;
        return 0;
    case 0x1561: case 0x1581: /* connected TX/RX lanes */
        *value = s->link != LINK_DOWN ? 2 : 0;
        return 0;
    case 0x1586: case 0x1587: /* supported PWM/HS gear, reference profile */
        *value = 4;
        return 0;
    case 0x15a9: case 0x15a0: /* local/remote UniPro revision */
        *value = 0x180;
        return 0;
    case 0x15aa: /* PA_GRANULARITY */
    case 0x008f: case 0x0092: /* RX time capability units */
        *value = 1;
        return 0;
    case 0x1560: case 0x1580: /* initial active lanes */
    case 0x1568: case 0x1583: /* initial gears */
    case 0x156a: /* initial HS series A */
    case 0x15a7: case 0x15a8:
        *value = 1;
        return 0;
    case 0xd041:
        *value = 8191; /* Linux reference defaults, not sampled silicon. */
        return 0;
    case 0xd042:
        *value = 65535;
        return 0;
    case 0xd043:
        *value = 32767;
        return 0;
    case 0x1571:
        *value = 0x55; /* SLOWAUTO at link startup. */
        return 0;
    default:
        if (qcom_ufs_dme_writable(id)) {
            *value = 0;
            return 0;
        }
        return UFS_UIC_CMD_RESULT_INVALID_ATTR;
    }
}

static bool qcom_ufs_valid_power_mode(uint32_t value)
{
    unsigned tx = value & 0xf, rx = (value >> 4) & 0xf;

    return value <= 0xff &&
           (tx == 1 || tx == 2 || tx == 4 || tx == 5) &&
           (rx == 1 || rx == 2 || rx == 4 || rx == 5);
}

static int qcom_ufs_dme_access(QcomSm8250UfsState *s, bool write, bool peer,
                               uint16_t id, uint16_t selector, uint32_t *value)
{
    QcomUfsDmeValue *entry = NULL, *free_entry = NULL;
    uint32_t initial = 0;
    unsigned i;
    int ret;

    if (peer && s->link == LINK_DOWN) {
        return UFS_UIC_CMD_RESULT_PEER_COMM_FAILURE;
    }
    ret = qcom_ufs_dme_default(s, id, selector, &initial);
    if (ret) {
        qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unsupported DME %s "
                      "id=0x%x selector=%u peer=%d\n",
                      write ? "set" : "get", id, selector, peer);
        return ret;
    }
    if (write && !qcom_ufs_dme_writable(id)) {
        return UFS_UIC_CMD_RESULT_READ_ONLY_ATTR;
    }
    if (write && (((id == 0x1560 || id == 0x1580) &&
                   (!*value || *value > 2)) ||
                  ((id == 0x1568 || id == 0x1583) &&
                   (!*value || *value > 4)) ||
                  (id == 0x156a && *value != 1 && *value != 2) ||
                  (id == 0x1571 && !qcom_ufs_valid_power_mode(*value)))) {
        return UFS_UIC_CMD_RESULT_INVALID_ATTR_VALUE;
    }
    for (i = 0; i < DME_VALUES; i++) {
        QcomUfsDmeValue *e = &s->dme[i];
        if (e->used && e->id == id && e->selector == selector &&
            e->peer == peer) {
            entry = e;
            break;
        }
        if (!e->used && !free_entry) {
            free_entry = e;
        }
    }
    if (!write) {
        *value = entry ? entry->value : initial;
        return 0;
    }
    if (id == 0x1571 && (s->link != LINK_ACTIVE || !s->phy_ready)) {
        return UFS_UIC_CMD_RESULT_DME_FAILURE;
    }
    if (!entry) {
        entry = free_entry;
    }
    if (!entry) {
        return UFS_UIC_CMD_RESULT_DME_FAILURE;
    }
    *entry = (QcomUfsDmeValue) {
        .id = id, .selector = selector, .value = *value,
        .peer = peer, .used = true,
    };
    if (id == 0x1571 && !peer) {
        s->ufs.reg.is |= R_IS_UPMS_MASK;
        s->ufs.reg.hcs = FIELD_DP32(s->ufs.reg.hcs, HCS, UPMCRS,
                                    UFS_PWR_LOCAL);
    }
    return 0;
}

static void qcom_ufs_uic_complete(void *opaque)
{
    QcomSm8250UfsState *s = opaque;
    UfsReg *r = &s->ufs.reg;
    uint32_t value = s->uic_args[2];
    uint32_t result = UFS_UIC_CMD_RESULT_DME_FAILURE;

    s->uic_busy = false;
    if (!qcom_ufs_host_running(s) || !(r->hce & 1)) {
        goto complete;
    }
    switch (s->uic_cmd) {
    case UFS_UIC_CMD_DME_GET:
    case UFS_UIC_CMD_DME_PEER_GET:
    case UFS_UIC_CMD_DME_SET:
    case UFS_UIC_CMD_DME_PEER_SET:
        result = qcom_ufs_dme_access(s, !(s->uic_cmd & 1),
                                     s->uic_cmd >= 3,
                                     s->uic_args[0] >> 16,
                                     s->uic_args[0] & 0xffff, &value);
        break;
    case UFS_UIC_CMD_DME_LINK_STARTUP:
        if (qcom_ufs_link_running(s)) {
            s->link = LINK_ACTIVE;
            s->ufs.link_hibern8 = false;
            s->auto_hibern8_active = false;
            r->hcs |= R_HCS_DP_MASK | R_HCS_UTRLRDY_MASK |
                      R_HCS_UTMRLRDY_MASK;
            result = 0;
        }
        break;
    case UFS_UIC_CMD_DME_HIBER_ENTER:
        if ((s->link == LINK_ACTIVE || s->auto_hibern8_active) &&
            qcom_ufs_link_running(s) &&
            qcom_ufs_queues_idle(s)) {
            s->link = LINK_HIBERN8;
            s->ufs.link_hibern8 = true;
            s->auto_hibern8_active = false;
            r->is |= R_IS_UHES_MASK;
            r->hcs = FIELD_DP32(r->hcs, HCS, UPMCRS, UFS_PWR_LOCAL);
            result = 0;
        }
        break;
    case UFS_UIC_CMD_DME_HIBER_EXIT:
        if (s->link == LINK_HIBERN8 && qcom_ufs_link_running(s)) {
            s->link = LINK_ACTIVE;
            s->ufs.link_hibern8 = false;
            s->auto_hibern8_active = false;
            r->is |= R_IS_UHXS_MASK;
            r->hcs = FIELD_DP32(r->hcs, HCS, UPMCRS, UFS_PWR_LOCAL);
            result = 0;
        }
        break;
    case UFS_UIC_CMD_DME_RESET:
    case UFS_UIC_CMD_DME_END_PT_RST:
        if (qcom_ufs_queues_idle(s)) {
            qcom_ufs_drop_link(s);
            memset(s->dme, 0, sizeof(s->dme));
            result = 0;
        }
        break;
    case UFS_UIC_CMD_DME_ENABLE:
        result = s->phy_ready ? 0 : UFS_UIC_CMD_RESULT_DME_FAILURE;
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unsupported UIC command 0x%x\n",
                      s->uic_cmd);
    }
complete:
    r->ucmdarg1 = s->uic_args[0];
    r->ucmdarg2 = (s->uic_args[1] & ~0xffU) | result;
    r->ucmdarg3 = value;
    r->hcs |= R_HCS_UCRDY_MASK;
    r->is |= R_IS_UCCS_MASK;
    qcom_ufs_irq(s);
}

static bool qcom_ufs_vendor_register(hwaddr offset)
{
    switch (offset) {
    case 0xc0: case 0xc4: case 0xc8: case 0xcc: case 0xd0:
    case 0xd8: case 0xdc: case 0xe0: case 0xe4:
    case 0xec: case 0xf0: case 0xf4: case 0xf8: case 0xfc:
    case 0x271c: case 0x284c:
        return true;
    default:
        return false;
    }
}

/*
 * Public Linux ufs-qcom_dump_dbg_regs() reads these exact major-3/4 diagnostic
 * apertures. They are not command-completion or link-ready registers.
 * Internal debug-bus sampling is not implemented: reads are explicitly
 * diagnosed and return the model's inactive (zero) diagnostic data; writes
 * remain rejected. Do not turn the rest of the HCI aperture into a RAZ stub.
 */
static bool qcom_ufs_diagnostic_register(hwaddr offset)
{
    static const struct {
        uint16_t offset;
        uint16_t words;
    } ranges[] = {
        { 0x100, 4 },   /* UAWM */
        { 0x200, 4 },   /* UARM */
        { 0x300, 48 },  /* TXUC */
        { 0x400, 27 },  /* RXUC */
        { 0x500, 19 },  /* DFC */
        { 0x600, 34 },  /* TRLUT */
        { 0x700, 9 },   /* TMRLUT */
        { 0x800, 44 },  /* OCSC */
        { 0x1500, 128 }, /* descriptor RAM */
        { 0x1700, 64 }, /* PRDT RAM */
        { 0x1900, 32 }, /* expected transfer length RAM */
    };
    unsigned i;

    /* Reserved word within the documented 16-word vendor block; test bus. */
    if (offset == 0xd4 || offset == 0xe8) {
        return true;
    }
    for (i = 0; i < ARRAY_SIZE(ranges); i++) {
        hwaddr start = ranges[i].offset + 0x400;

        if (offset >= start && offset < start + ranges[i].words * 4) {
            return true;
        }
    }
    return false;
}

static MemTxResult qcom_ufs_hci_read(void *opaque, hwaddr offset,
                                   uint64_t *value, unsigned size,
                                   MemTxAttrs attrs)
{
    QcomSm8250UfsState *s = opaque;

    *value = 0;
    if (!qcom_ufs_host_accessible(s)) {
        return MEMTX_ERROR;
    }
    if (offset < 0xc0) {
        *value = ufs_mmio_read(&s->ufs, offset, size);
        return MEMTX_OK;
    }
    if (qcom_ufs_vendor_register(offset)) {
        *value = s->vendor[offset / 4];
        return MEMTX_OK;
    }
    if (qcom_ufs_diagnostic_register(offset)) {
        qemu_log_mask(LOG_UNIMP, "sm8250-ufs: inactive diagnostic read 0x%"
                      HWADDR_PRIx " (internal debug sampling not modeled)\n",
                      offset);
        return MEMTX_OK;
    }
    qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unknown HCI read 0x%"
                  HWADDR_PRIx "\n", offset);
    return MEMTX_ERROR;
}

static MemTxResult qcom_ufs_hci_write(void *opaque, hwaddr offset,
                                    uint64_t value, unsigned size,
                                    MemTxAttrs attrs)
{
    QcomSm8250UfsState *s = opaque;
    UfsReg *r = &s->ufs.reg;

    if (!qcom_ufs_host_accessible(s)) {
        return MEMTX_ERROR;
    }
    if (offset == A_UICCMD) {
        if (s->uic_busy || !(r->hce & 1) || !qcom_ufs_host_running(s)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "sm8250-ufs: UIC command while controller busy/off\n");
            return MEMTX_ERROR;
        }
        /* Explicit Hibern8 commands take ownership of an automatic sleep. */
        if ((value & 0xff) != UFS_UIC_CMD_DME_HIBER_ENTER &&
            (value & 0xff) != UFS_UIC_CMD_DME_HIBER_EXIT &&
            !qcom_ufs_auto_exit(s)) {
            return MEMTX_ERROR;
        }
        timer_del(s->ahit_timer);
        s->uic_cmd = value & 0xff;
        s->uic_args[0] = r->ucmdarg1;
        s->uic_args[1] = r->ucmdarg2;
        s->uic_args[2] = r->ucmdarg3;
        s->uic_busy = true;
        r->hcs &= ~R_HCS_UCRDY_MASK;
        timer_mod(s->uic_timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                  UIC_COMPLETE_NS);
        return MEMTX_OK;
    }
    if (offset == A_AHIT) {
        if (!s->auto_hibern8 || value & ~AHIT_MASK ||
            ((value & AHIT_SCALE_MASK) >> 10) > AHIT_MAX_SCALE) {
            qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unsupported AHIT 0x%"
                          PRIx64 "\n", value);
            return MEMTX_ERROR;
        }
        s->ufs.reg.ahit = value;
        timer_del(s->ahit_timer);
        qcom_ufs_update_ahit(s);
        return MEMTX_OK;
    }
    if (offset == A_UTRLDBR || offset == A_UTMRLDBR) {
        if (!value) {
            return MEMTX_OK;
        }
        if (!qcom_ufs_link_running(s) || !(r->hce & 1) ||
            (offset == A_UTRLDBR && !(r->utrlrsr & 1)) ||
            (offset == A_UTMRLDBR && !(r->utmrlrsr & 1)) ||
            !qcom_ufs_auto_exit(s) || s->link != LINK_ACTIVE) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "sm8250-ufs: transfer before active link/request list\n");
            return MEMTX_ERROR;
        }
        /* Task management can complete synchronously inside the core. */
        timer_del(s->ahit_timer);
    }
    if (offset < 0xc0) {
        ufs_mmio_write(&s->ufs, offset, value, size);
        if (offset == A_HCE && !(value & 1)) {
            timer_del(s->uic_timer);
            s->uic_busy = false;
            qcom_ufs_drop_link(s);
            memset(s->dme, 0, sizeof(s->dme));
        }
        qcom_ufs_update_ahit(s);
        return MEMTX_OK;
    }
    if (!qcom_ufs_vendor_register(offset) ||
        offset == V_HW_VERSION || offset == 0xd0 || offset == 0xcc) {
        qemu_log_mask(LOG_UNIMP, "sm8250-ufs: unsupported HCI write 0x%"
                      HWADDR_PRIx " = 0x%" PRIx64 "\n", offset, value);
        return MEMTX_ERROR;
    }
    s->vendor[offset / 4] = value;
    if (offset == V_CFG1) {
        qcom_ufs_update_phy(s);
        qcom_ufs_update_ahit(s);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps qcom_ufs_hci_ops = {
    .read_with_attrs = qcom_ufs_hci_read,
    .write_with_attrs = qcom_ufs_hci_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .valid.unaligned = false,
};

static const MemoryRegionOps qcom_ufs_phy_ops = {
    .read_with_attrs = qcom_ufs_phy_read,
    .write_with_attrs = qcom_ufs_phy_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4, .valid.max_access_size = 4,
    .valid.unaligned = false,
};

static void qcom_ufs_reset_state(QcomSm8250UfsState *s)
{
    timer_del(s->phy_timer);
    timer_del(s->uic_timer);
    timer_del(s->ahit_timer);
    s->uic_busy = false;
    s->phy_ready = false;
    s->link = LINK_DOWN;
    s->auto_hibern8_active = false;
    memset(s->vendor, 0, sizeof(s->vendor));
    memset(s->phy_regs, 0, sizeof(s->phy_regs));
    memset(s->phy_written, 0, sizeof(s->phy_written));
    memset(s->dme, 0, sizeof(s->dme));
    s->vendor[V_HW_VERSION / 4] = s->hw_version;
    s->vendor[0xd0 / 4] = 4 << 4;
    s->phy_regs[PHY_RESET / 4] = 1;
    if (s->initialized) {
        ufs_reset_controller(&s->ufs);
    }
    qcom_ufs_symbols(s);
}

static void qcom_ufs_reset(DeviceState *dev)
{
    qcom_ufs_reset_state(QCOM_SM8250_UFS(dev));
}

static void qcom_ufs_reset_input(void *opaque, int n, int level)
{
    QcomSm8250UfsState *s = opaque;

    s->reset_held = level;
    if (level && s->initialized) {
        qcom_ufs_reset_state(s);
    }
    qcom_ufs_update_phy(s);
}

static void qcom_ufs_power_input(void *opaque, int n, int level)
{
    QcomSm8250UfsState *s = opaque;

    s->power_on = level;
    if (!level && s->initialized) {
        qcom_ufs_reset_state(s);
    }
    qcom_ufs_update_phy(s);
}

static void qcom_ufs_device_reset_input(void *opaque, int n, int level)
{
    QcomSm8250UfsState *s = opaque;

    s->device_reset_held = level;
    if (level && s->initialized) {
        timer_del(s->uic_timer);
        s->uic_busy = false;
        qcom_ufs_drop_link(s);
        memset(s->dme, 0, sizeof(s->dme));
        ufs_reset_controller(&s->ufs);
    }
}

static char *qcom_ufs_get_link_state(Object *obj, Error **errp)
{
    QcomSm8250UfsState *s = QCOM_SM8250_UFS(obj);
    const char *names[] = { "down", "active", "hibern8" };

    return g_strdup(names[s->link]);
}

static void qcom_ufs_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250UfsState *s = QCOM_SM8250_UFS(dev);

    if (!s->symbol_hz || s->symbol_hz > 1000000000 ||
        ((s->hw_version >> 28) != 3 && (s->hw_version >> 28) != 4) ||
        (s->hci_version != 0x210 && s->hci_version != 0x300)) {
        error_setg(errp, "SM8250 UFS requires a major-3/4 reference controller, "
                   "HCI 2.1/3.0 and a nonzero reference symbol clock <= 1 GHz");
        return;
    }
    if (!s->dma_memory) {
        s->dma_memory = get_system_memory();
    }
    address_space_init(&s->dma_as, s->dma_memory, "sm8250-ufs-dma");
    if (!ufs_realize(&s->ufs, dev, &s->dma_as, errp)) {
        address_space_destroy(&s->dma_as);
        return;
    }
    s->ufs.reg.ver = s->hci_version;
    s->ufs.reg.cap = FIELD_DP32(s->ufs.reg.cap, CAP, AUTOH8, s->auto_hibern8);
    s->ufs.device_desc.spec_version = cpu_to_be16(0x310);
    s->ufs.device_desc.manufacturer_id = cpu_to_be16(0x1ce);
    /*
     * Retain only implemented legacy features. Native 0x183 extended feature
     * bits are evidence, not permission to advertise unimplemented behavior.
     */
    s->ufs.device_desc.ufs_features_support = 0;
    s->ufs.device_desc.extended_ufs_features_support =
        cpu_to_be32(UFS_DEV_WB_SUPPORT);
    s->ufs.device_desc.extended_wb_support = 0;
    s->ufs.attributes.ref_clk_freq = 0; /* 19.2 MHz digital board input */
    s->initialized = true;
    qcom_ufs_reset_state(s);
}

static void qcom_ufs_unrealize(DeviceState *dev)
{
    QcomSm8250UfsState *s = QCOM_SM8250_UFS(dev);

    s->initialized = false;
    timer_del(s->phy_timer);
    timer_del(s->uic_timer);
    timer_del(s->ahit_timer);
    ufs_unrealize(&s->ufs);
    address_space_destroy(&s->dma_as);
}

static void qcom_ufs_init(Object *obj)
{
    QcomSm8250UfsState *s = QCOM_SM8250_UFS(obj);
    DeviceState *dev = DEVICE(obj);
    const char *names[] = { "tx-lane0", "rx-lane0", "rx-lane1" };
    const char *sync_names[] = { "tx-sync-clk", "rx0-sync-clk", "rx1-sync-clk" };
    unsigned i;

    memory_region_init_io(&s->hci, obj, &qcom_ufs_hci_ops, s,
                          "sm8250-ufs-hci", QCOM_SM8250_UFS_HCI_SIZE);
    memory_region_init_io(&s->phy, obj, &qcom_ufs_phy_ops, s,
                          "sm8250-ufs-phy", QCOM_SM8250_UFS_PHY_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->hci);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->phy);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq);
    s->ufs.irq = qemu_allocate_irq(qcom_ufs_core_irq, s, 0);
    object_property_add_str(obj, "link-state", qcom_ufs_get_link_state, NULL);
    qdev_init_gpio_in_named(dev, qcom_ufs_reset_input, "reset", 1);
    qdev_init_gpio_in_named(dev, qcom_ufs_power_input, "power", 1);
    qdev_init_gpio_in_named(dev, qcom_ufs_device_reset_input,
                            "device-reset", 1);
    s->core_clk = qdev_init_clock_in(dev, "core-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    s->iface_clk = qdev_init_clock_in(dev, "iface-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    s->unipro_clk = qdev_init_clock_in(dev, "unipro-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    s->ref_clk = qdev_init_clock_in(dev, "ref-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    s->phy_aux_clk = qdev_init_clock_in(dev, "phy-aux-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    s->aggre_clk = qdev_init_clock_in(dev, "aggre-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    s->qref_clk = qdev_init_clock_in(dev, "qref-clk",
        qcom_ufs_clock_changed, s, ClockUpdate);
    for (i = 0; i < ARRAY_SIZE(names); i++) {
        s->symbol_clk[i] = qdev_init_clock_out(dev, names[i]);
        s->sync_clk[i] = qdev_init_clock_in(dev, sync_names[i],
            qcom_ufs_clock_changed, s, ClockUpdate);
    }
    s->phy_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, qcom_ufs_phy_ready, s);
    s->uic_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, qcom_ufs_uic_complete, s);
    s->ahit_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, qcom_ufs_auto_enter, s);
}

static void qcom_ufs_finalize(Object *obj)
{
    QcomSm8250UfsState *s = QCOM_SM8250_UFS(obj);

    timer_free(s->phy_timer);
    timer_free(s->uic_timer);
    timer_free(s->ahit_timer);
    qemu_free_irq(s->ufs.irq);
}

static const Property qcom_ufs_properties[] = {
    DEFINE_PROP_BOOL("auto-hibern8", QcomSm8250UfsState, auto_hibern8, true),
    DEFINE_PROP_STRING("serial", QcomSm8250UfsState, ufs.params.serial),
    DEFINE_PROP_UINT8("nutrs", QcomSm8250UfsState, ufs.params.nutrs, 32),
    DEFINE_PROP_UINT8("nutmrs", QcomSm8250UfsState, ufs.params.nutmrs, 8),
    DEFINE_PROP_UINT32("wb-max-size", QcomSm8250UfsState,
                       ufs.params.wb_max_size, 0x400),
    DEFINE_PROP_UINT32("wb-min-size", QcomSm8250UfsState,
                       ufs.params.wb_min_size, 0x100),
    DEFINE_PROP_UINT32("hw-version", QcomSm8250UfsState,
                       hw_version, 0x40000000),
    DEFINE_PROP_UINT32("hci-version", QcomSm8250UfsState,
                       hci_version, 0x300),
    DEFINE_PROP_UINT32("reference-symbol-hz", QcomSm8250UfsState,
                       symbol_hz, 150000000),
    DEFINE_PROP_LINK("dma-memory", QcomSm8250UfsState, dma_memory,
                     TYPE_MEMORY_REGION, MemoryRegion *),
};

/* The bounded migration contract is specific to this legacy QCOM wrapper. */
#include "qcom-sm8250-ufs-migration.inc"

static void qcom_ufs_class_init(ObjectClass *oc, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);

    dc->desc = "SM8250 UFS host wrapper and QMP v4 PHY digital model";
    dc->realize = qcom_ufs_realize;
    dc->unrealize = qcom_ufs_unrealize;
    dc->vmsd = &vmstate_qcom_ufs;
    device_class_set_props(dc, qcom_ufs_properties);
    device_class_set_legacy_reset(dc, qcom_ufs_reset);
    set_bit(DEVICE_CATEGORY_STORAGE, dc->categories);
}

static const TypeInfo qcom_ufs_info = {
    .name = TYPE_QCOM_SM8250_UFS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250UfsState),
    .instance_init = qcom_ufs_init,
    .instance_finalize = qcom_ufs_finalize,
    .class_init = qcom_ufs_class_init,
};

static void qcom_ufs_register_types(void)
{
    type_register_static(&qcom_ufs_info);
}
type_init(qcom_ufs_register_types)
