/*
 * Qualcomm SM8250 apps RSC and typed RPMh resource service.
 *
 * SPDX-License-Identifier: GPL-2.0-only
 *
 * Register protocol: Linux rpmh-rsc.c, cmd-db.c, clk-rpmh.c,
 * rpmhpd.c, qcom-rpmh-regulator.c and Qualcomm's downstream 4.19 driver.
 * The optional built-in command-db is an explicitly synthetic test profile.
 * This is not an AOP firmware, PMIC analogue or PDC solver emulation.
 */

#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-rpmh.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/bitops.h"
#include "qemu/bswap.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define TCS_BASE                0xd00
#define TCS_STRIDE              0x2a0
#define CMD_BASE                0x30
#define CMD_STRIDE              0x14
#define AMC_ENABLE              BIT(16)
#define AMC_TRIGGER             BIT(24)
#define MSG_RESPONSE            BIT(8)
#define MSG_WRITE               BIT(16)
#define CMD_ISSUED              BIT(8)
#define CMD_COMPLETE            BIT(16)
#define BCM_COMMIT              BIT(30)
#define BCM_VALID               BIT(29)
#define BCM_MASK                (BCM_COMMIT | BCM_VALID | 0x0fffffff)
#define DB_HEADER_SIZE          144
#define DB_ENTRY_SIZE           24

typedef enum RpmhResourceKind {
    RPMH_ARC,
    RPMH_VRM_CLOCK,
    RPMH_VRM_REGULATOR,
    RPMH_BCM,
} RpmhResourceKind;

typedef struct RpmhResourceDesc {
    const char *name;
    RpmhResourceKind kind;
    uint32_t reference_address;
    uint32_t max_mv;
    uint32_t mode_mask;
    uint8_t max_level;
    const char *clock;
    unsigned divider;
    uint16_t reference_levels[16];
} RpmhResourceDesc;

#include "qcom-sm8250-rpmh-data.inc"

static uint32_t rpmh_aggregate(QcomSm8250RpmhState *s, unsigned res,
                                unsigned field)
{
    uint32_t value = 0;
    unsigned i;

    for (i = 0; i < RPMH_DRVS; i++) {
        value = MAX(value, s->resources[res].votes[i][field]);
    }
    return value;
}

static bool rpmh_enabled(QcomSm8250RpmhState *s, unsigned res)
{
    switch (rpmh_resources[res].kind) {
    case RPMH_ARC:
        return rpmh_aggregate(s, res, 0) != 0;
    case RPMH_VRM_CLOCK:
    case RPMH_VRM_REGULATOR:
        return rpmh_aggregate(s, res, 1) != 0;
    case RPMH_BCM:
        return rpmh_aggregate(s, res, 2) &&
               (rpmh_aggregate(s, res, 0) || rpmh_aggregate(s, res, 1));
    }
    g_assert_not_reached();
}

static void rpmh_outputs(QcomSm8250RpmhState *s)
{
    unsigned i;

    if (!s->ready) {
        return;
    }
    for (i = 0; i < RPMH_DRVS; i++) {
        qemu_set_irq(s->drv[i].irq,
                     (s->drv[i].irq_status & s->drv[i].irq_enable) != 0);
    }
    for (i = 0; i < RPMH_RESOURCES; i++) {
        bool enabled = s->resources[i].present && rpmh_enabled(s, i);
        uint64_t hz = 0;

        qemu_set_irq(s->power[i], enabled);
        if (!s->clocks[i]) {
            continue;
        }
        if (enabled) {
            if (rpmh_resources[i].kind == RPMH_BCM) {
                hz = (uint64_t)rpmh_aggregate(s, i, 1) *
                     s->resources[i].unit * 1000;
            } else {
                hz = clock_get_hz(s->xo_board) / rpmh_resources[i].divider;
            }
        }
        clock_update_hz(s->clocks[i], hz);
    }
}

static void rpmh_clock_changed(void *opaque, ClockEvent event)
{
    rpmh_outputs(opaque);
}

static void rpmh_schedule(QcomSm8250RpmhState *s)
{
    int64_t next = INT64_MAX;
    unsigned d, t;

    for (d = 0; d < RPMH_DRVS; d++) {
        for (t = 0; t < RPMH_TCS_PER_DRV; t++) {
            QcomRpmhTcs *tcs = &s->drv[d].tcs[t];
            if (tcs->running && !tcs->stalled) {
                next = MIN(next, tcs->deadline);
            }
        }
    }
    if (next == INT64_MAX) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static int rpmh_find_resource(QcomSm8250RpmhState *s, uint32_t address,
                               unsigned *field)
{
    unsigned i;

    for (i = 0; i < RPMH_RESOURCES; i++) {
        QcomRpmhResource *r = &s->resources[i];
        RpmhResourceKind kind = rpmh_resources[i].kind;
        unsigned span = (kind == RPMH_VRM_CLOCK ||
                         kind == RPMH_VRM_REGULATOR) ? 16 : 4;
        if (r->present && address >= r->address &&
            address < (uint64_t)r->address + span && !(address & 3)) {
            *field = (address - r->address) / 4;
            return i;
        }
    }
    return -1;
}

static bool rpmh_value_valid(QcomSm8250RpmhState *s, unsigned i,
                              unsigned field, uint32_t value)
{
    const RpmhResourceDesc *desc = &rpmh_resources[i];

    switch (desc->kind) {
    case RPMH_ARC:
        return field == 0 &&
               value < s->resources[i].level_count;
    case RPMH_VRM_CLOCK:
        return field == 1 && value <= 1;
    case RPMH_VRM_REGULATOR:
        if (field == 0) {
            /* Requested millivolts, not a simulated physical output voltage. */
            return value <= desc->max_mv;
        }
        if (field == 1) {
            return value <= 1;
        }
        return field == 2 && value < 32 && (desc->mode_mask & BIT(value));
    case RPMH_BCM:
        return field == 0 && !(value & ~BCM_MASK);
    }
    return false;
}

static bool rpmh_apply(QcomSm8250RpmhState *s, unsigned drv,
                        QcomRpmhCommand *cmd)
{
    QcomRpmhResource *r;
    unsigned field, i;
    int index;

    /* Only the documented Linux 8-byte write opcode is implemented. */
    if ((cmd->msgid & ~MSG_RESPONSE) != (MSG_WRITE | 8)) {
        return false;
    }
    index = rpmh_find_resource(s, cmd->address, &field);
    if (index < 0 || !rpmh_value_valid(s, index, field, cmd->data)) {
        return false;
    }
    r = &s->resources[index];
    if (rpmh_resources[index].kind != RPMH_BCM) {
        r->votes[drv][field] = cmd->data;
    } else {
        r->staged[drv] = cmd->data & ~BCM_COMMIT;
        r->dirty[drv] = true;
        if (cmd->data & BCM_COMMIT) {
            /* Commit pending votes for this DRV and virtual clock domain. */
            for (i = 0; i < RPMH_RESOURCES; i++) {
                QcomRpmhResource *b = &s->resources[i];
                uint32_t v = b->staged[drv];
                if (rpmh_resources[i].kind == RPMH_BCM && b->present &&
                    b->vcd == r->vcd && b->dirty[drv]) {
                    b->votes[drv][0] = (v & BCM_VALID) ? (v >> 14) & 0x3fff : 0;
                    b->votes[drv][1] = (v & BCM_VALID) ? v & 0x3fff : 0;
                    b->votes[drv][2] = !!(v & BCM_VALID);
                    b->dirty[drv] = false;
                }
            }
        }
    }
    /* No undocumented payload is invented for a successful write response. */
    cmd->response = 0;
    rpmh_outputs(s);
    return true;
}

static void rpmh_next_command(QcomSm8250RpmhState *s, unsigned d,
                               unsigned t, int64_t from)
{
    QcomRpmhTcs *tcs = &s->drv[d].tcs[t];

    while (++tcs->cursor < RPMH_COMMANDS) {
        if (tcs->enable & BIT(tcs->cursor)) {
            tcs->commands[tcs->cursor].status = CMD_ISSUED;
            tcs->deadline = from + s->command_latency_ns;
            return;
        }
    }
    tcs->running = false;
    tcs->deadline = -1;
    s->drv[d].irq_status |= BIT(t);
}

static void rpmh_timer(void *opaque)
{
    QcomSm8250RpmhState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned d, t;

    for (d = 0; d < RPMH_DRVS; d++) {
        for (t = 0; t < RPMH_TCS_PER_DRV; t++) {
            QcomRpmhTcs *tcs = &s->drv[d].tcs[t];
            while (tcs->running && !tcs->stalled && tcs->deadline <= now) {
                QcomRpmhCommand *cmd = &tcs->commands[tcs->cursor];
                int64_t completed = tcs->deadline;
                if (!rpmh_apply(s, d, cmd)) {
                    qemu_log_mask(LOG_UNIMP,
                        TYPE_QCOM_SM8250_RPMH ": DRV%u TCS%u CMD%d unsupported "
                        "msg=%08x addr=%08x data=%08x; no completion\n",
                        d, t, tcs->cursor, cmd->msgid, cmd->address, cmd->data);
                    tcs->stalled = true;
                    tcs->deadline = -1;
                    break;
                }
                if ((cmd->msgid & MSG_RESPONSE) ||
                    (tcs->wait & BIT(tcs->cursor))) {
                    cmd->status |= CMD_COMPLETE;
                }
                rpmh_next_command(s, d, t, completed);
            }
        }
    }
    rpmh_outputs(s);
    rpmh_schedule(s);
}

static void rpmh_start(QcomSm8250RpmhState *s, unsigned d, unsigned t)
{
    QcomRpmhTcs *tcs = &s->drv[d].tcs[t];
    unsigned i;

    if (tcs->running || !tcs->enable) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_RPMH
                      ": DRV%u TCS%u trigger while busy or empty\n", d, t);
        return;
    }
    for (i = 0; i < RPMH_COMMANDS; i++) {
        tcs->commands[i].status = 0;
        tcs->commands[i].response = 0;
    }
    tcs->running = true;
    tcs->stalled = false;
    tcs->cursor = -1;
    rpmh_next_command(s, d, t, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    rpmh_schedule(s);
}

static void rpmh_sleep(void *opaque, int drv, int level)
{
    QcomSm8250RpmhState *s = opaque;
    unsigned t, first = level ? 2 : 5;

    if (!s->ready || s->drv[drv].sleeping == !!level) {
        return;
    }
    for (t = 0; t < RPMH_TCS_PER_DRV; t++) {
        if (s->drv[drv].tcs[t].running) {
            qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_RPMH
                          ": DRV%d sleep/wake transition while busy\n", drv);
            return;
        }
    }
    s->drv[drv].sleeping = !!level;
    for (t = first; t < first + 3; t++) {
        if (s->drv[drv].tcs[t].enable) {
            rpmh_start(s, drv, t);
        }
    }
}

static MemTxResult rpmh_bad_access(QcomRpmhDrv *drv, hwaddr addr,
                                    bool write, uint64_t value)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_RPMH
                  ": DRV%td unsupported %s @0x%04" HWADDR_PRIx
                  " value=0x%08" PRIx64 "\n",
                  drv - drv->owner->drv, write ? "write" : "read", addr, value);
    return MEMTX_ERROR;
}

static MemTxResult rpmh_read(void *opaque, hwaddr addr, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomRpmhDrv *drv = opaque;
    QcomRpmhTcs *tcs;
    QcomRpmhCommand *cmd;
    unsigned t, reg, index;

    *value = 0;
    if (size != 4 || (addr & 3)) {
        return rpmh_bad_access(drv, addr, false, 0);
    }
    switch (addr) {
    case 0:
        *value = 0x00020700; /* Reference 2.7 ABI, not a silicon ID sample. */
        return MEMTX_OK;
    case 4:
        return MEMTX_OK; /* No autonomous hardware solver advertised. */
    case 0xc:
        *value = (RPMH_COMMANDS << 27) | (RPMH_TCS_PER_DRV << 12) |
                 (RPMH_TCS_PER_DRV << 6) | RPMH_TCS_PER_DRV;
        return MEMTX_OK;
    case 0x38:
        *value = drv->wake_hi;
        return MEMTX_OK;
    case 0x40:
        *value = drv->wake_lo;
        return MEMTX_OK;
    case TCS_BASE:
        *value = drv->irq_enable;
        return MEMTX_OK;
    case TCS_BASE + 4:
        *value = drv->irq_status;
        return MEMTX_OK;
    case TCS_BASE + 8:
        return MEMTX_OK;
    }
    if (addr < TCS_BASE || addr >= TCS_BASE + RPMH_TCS_PER_DRV * TCS_STRIDE) {
        return rpmh_bad_access(drv, addr, false, 0);
    }
    t = (addr - TCS_BASE) / TCS_STRIDE;
    reg = (addr - TCS_BASE) % TCS_STRIDE;
    tcs = &drv->tcs[t];
    switch (reg) {
    case 0x10:
        *value = tcs->wait;
        return MEMTX_OK;
    case 0x14:
        *value = tcs->control;
        return MEMTX_OK;
    case 0x1c:
        *value = tcs->enable;
        return MEMTX_OK;
    }
    /* RSC_DRV_STATUS polarity is not specified by the cited drivers. */
    if (reg < CMD_BASE || reg >= CMD_BASE + RPMH_COMMANDS * CMD_STRIDE) {
        return rpmh_bad_access(drv, addr, false, 0);
    }
    index = (reg - CMD_BASE) / CMD_STRIDE;
    cmd = &tcs->commands[index];
    switch ((reg - CMD_BASE) % CMD_STRIDE) {
    case 0:
        *value = cmd->msgid;
        break;
    case 4:
        *value = cmd->address;
        break;
    case 8:
        *value = cmd->data;
        break;
    case 12:
        *value = cmd->status;
        break;
    case 16:
        *value = cmd->response;
        break;
    default:
        g_assert_not_reached();
    }
    return MEMTX_OK;
}

static MemTxResult rpmh_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomRpmhDrv *drv = opaque;
    QcomSm8250RpmhState *s = drv->owner;
    QcomRpmhTcs *tcs;
    QcomRpmhCommand *cmd;
    unsigned t, reg;

    if (size != 4 || (addr & 3)) {
        return rpmh_bad_access(drv, addr, true, value);
    }
    switch (addr) {
    case 0x38:
        /* PDC/architectural-counter wake routing is not implemented. */
        if (value & ~0x00ffffffU) {
            return rpmh_bad_access(drv, addr, true, value);
        }
        drv->wake_hi = value;
        return MEMTX_OK;
    case 0x40:
        drv->wake_lo = value;
        return MEMTX_OK;
    case TCS_BASE:
        if (value & ~0xffU) {
            return rpmh_bad_access(drv, addr, true, value);
        }
        drv->irq_enable = value;
        rpmh_outputs(s);
        return MEMTX_OK;
    case TCS_BASE + 8:
        if (value & ~0xffU) {
            return rpmh_bad_access(drv, addr, true, value);
        }
        drv->irq_status &= ~value;
        rpmh_outputs(s);
        return MEMTX_OK;
    }
    if (addr < TCS_BASE || addr >= TCS_BASE + RPMH_TCS_PER_DRV * TCS_STRIDE) {
        return rpmh_bad_access(drv, addr, true, value);
    }
    t = (addr - TCS_BASE) / TCS_STRIDE;
    reg = (addr - TCS_BASE) % TCS_STRIDE;
    tcs = &drv->tcs[t];
    if (reg == 0x14) {
        bool trigger = (value & AMC_TRIGGER) && !(tcs->control & AMC_TRIGGER);
        if ((value & ~(AMC_ENABLE | AMC_TRIGGER)) ||
            ((value & AMC_TRIGGER) && !(value & AMC_ENABLE)) ||
            (trigger && (tcs->running || !tcs->enable))) {
            return rpmh_bad_access(drv, addr, true, value);
        }
        tcs->control = value;
        if (!(value & AMC_ENABLE)) {
            /* Cancelling cannot roll back commands that already completed. */
            tcs->running = false;
            tcs->stalled = false;
            tcs->deadline = -1;
            rpmh_schedule(s);
        } else if (trigger) {
            rpmh_start(s, drv - s->drv, t);
        }
        return MEMTX_OK;
    }
    if (tcs->running) {
        return rpmh_bad_access(drv, addr, true, value);
    }
    if (reg == 0x10 || reg == 0x1c) {
        if (value & ~0xffffU) {
            return rpmh_bad_access(drv, addr, true, value);
        }
        if (reg == 0x10) {
            tcs->wait = value;
        } else {
            tcs->enable = value;
        }
        return MEMTX_OK;
    }
    if (reg < CMD_BASE || reg >= CMD_BASE + RPMH_COMMANDS * CMD_STRIDE) {
        return rpmh_bad_access(drv, addr, true, value);
    }
    cmd = &tcs->commands[(reg - CMD_BASE) / CMD_STRIDE];
    switch ((reg - CMD_BASE) % CMD_STRIDE) {
    case 0:
        cmd->msgid = value;
        return MEMTX_OK;
    case 4:
        cmd->address = value;
        return MEMTX_OK;
    case 8:
        cmd->data = value;
        return MEMTX_OK;
    default:
        return rpmh_bad_access(drv, addr, true, value);
    }
}

static const MemoryRegionOps rpmh_ops = {
    .read_with_attrs = rpmh_read,
    .write_with_attrs = rpmh_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4 },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static MemTxResult rpmh_db_read(void *opaque, hwaddr addr, uint64_t *value,
                                unsigned size, MemTxAttrs attrs)
{
    QcomSm8250RpmhState *s = opaque;
    unsigned i;

    *value = 0;
    if (addr + size > RPMH_CMD_DB_SIZE) {
        return MEMTX_ERROR;
    }
    for (i = 0; i < size; i++) {
        *value |= (uint64_t)s->command_db[addr + i] << (i * 8);
    }
    return MEMTX_OK;
}

static MemTxResult rpmh_db_write(void *opaque, hwaddr addr, uint64_t value,
                                 unsigned size, MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_RPMH
                  ": command-db is read-only @0x%" HWADDR_PRIx "\n", addr);
    return MEMTX_ERROR;
}

static const MemoryRegionOps rpmh_db_ops = {
    .read_with_attrs = rpmh_db_read,
    .write_with_attrs = rpmh_db_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8,
               .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8,
              .unaligned = true },
};

static unsigned rpmh_slave(RpmhResourceKind kind)
{
    return kind == RPMH_ARC ? 3 : kind == RPMH_BCM ? 5 : 4;
}

static void rpmh_reference_db(QcomSm8250RpmhState *s)
{
    unsigned group, i, cursor = DB_HEADER_SIZE;
    uint8_t *db = s->command_db;

    stl_le_p(db, 1);
    stl_le_p(db + 4, 0x0c0330db);
    for (group = 0; group < 3; group++) {
        unsigned slave = group + 3, count = 0, h, a;
        uint8_t *header = db + 8 + group * 16;
        for (i = 0; i < RPMH_RESOURCES; i++) {
            count += rpmh_slave(rpmh_resources[i].kind) == slave;
        }
        h = cursor;
        cursor += count * DB_ENTRY_SIZE;
        a = cursor;
        stw_le_p(header, slave);
        stw_le_p(header + 2, h - DB_HEADER_SIZE);
        stw_le_p(header + 4, a - DB_HEADER_SIZE);
        stw_le_p(header + 6, count);
        stw_le_p(header + 8, 0x100);
        for (i = 0; i < RPMH_RESOURCES; i++) {
            const RpmhResourceDesc *desc = &rpmh_resources[i];
            unsigned len = 0, k;
            if (rpmh_slave(desc->kind) != slave) {
                continue;
            }
            memcpy(db + h, desc->name, strlen(desc->name));
            stl_le_p(db + h + 16, desc->reference_address);
            if (desc->kind == RPMH_ARC && !desc->clock[0]) {
                len = (desc->max_level + 1) * 2;
                for (k = 0; k <= desc->max_level; k++) {
                    stw_le_p(db + cursor + k * 2, desc->reference_levels[k]);
                }
            } else if (desc->kind == RPMH_BCM) {
                len = 8;
                stl_le_p(db + cursor, 1000);
                stw_le_p(db + cursor + 4, 1);
            }
            stw_le_p(db + h + 20, len);
            stw_le_p(db + h + 22, cursor - a);
            cursor += len;
            h += DB_ENTRY_SIZE;
        }
    }
    assert(cursor < RPMH_CMD_DB_SIZE);
}

typedef struct RpmhDbSeenResource {
    const uint8_t *entry;
    const uint8_t *aux;
    uint16_t slave;
    uint16_t version;
} RpmhDbSeenResource;

static bool rpmh_parse_db(QcomSm8250RpmhState *s, size_t size, Error **errp)
{
    uint8_t *db = s->command_db;
    g_autoptr(GHashTable) names = g_hash_table_new_full(g_str_hash,
                                                       g_str_equal, g_free,
                                                       g_free);
    unsigned g, j, i, groups = 0;

    if (size < DB_HEADER_SIZE || ldl_le_p(db) != 1 ||
        ldl_le_p(db + 4) != 0x0c0330db) {
        error_setg(errp, "RPMh command-db must have version 1 and valid magic");
        return false;
    }
    for (g = 0; g < 8; g++) {
        const uint8_t *h = db + 8 + 16 * g;
        unsigned slave = lduw_le_p(h);
        unsigned start = DB_HEADER_SIZE + lduw_le_p(h + 2);
        unsigned aux = DB_HEADER_SIZE + lduw_le_p(h + 4);
        unsigned count = lduw_le_p(h + 6);
        unsigned version = lduw_le_p(h + 8);
        if (!slave) {
            break;
        }
        if (slave < 3 || slave > 5 ||
            !count || start + (uint64_t)count * DB_ENTRY_SIZE > size ||
            aux > size) {
            error_setg(errp, "RPMh command-db has invalid slave/header bounds");
            return false;
        }
        groups |= BIT(slave);
        for (j = 0; j < count; j++) {
            const uint8_t *e = db + start + j * DB_ENTRY_SIZE;
            uint32_t address = ldl_le_p(e + 16);
            unsigned len = lduw_le_p(e + 20);
            unsigned offset = aux + lduw_le_p(e + 22);
            unsigned span = slave == 4 ? 16 : 4;
            g_autofree char *name = g_strndup((const char *)e, 8);
            RpmhDbSeenResource *seen;

            if (!name[0] ||
                (address ? ((address >> 16) & 15) != slave : !len) ||
                (address & (span - 1)) || offset + (uint64_t)len > size) {
                error_setg(errp, "RPMh command-db has invalid resource");
                return false;
            }
            seen = g_hash_table_lookup(names, name);
            if (seen) {
                /*
                 * AOP can insert a parent again during ordinary registration.
                 * Compare the name, priorities, address and auxiliary length,
                 * then auxiliary bytes: their file offsets may differ.
                 */
                if (seen->slave != slave || seen->version != version ||
                    memcmp(seen->entry, e, DB_ENTRY_SIZE - 2) ||
                    memcmp(seen->aux, db + offset, len)) {
                    error_setg(errp, "RPMh command-db has conflicting "
                               "duplicate resource");
                    return false;
                }
                continue;
            }
            seen = g_new(RpmhDbSeenResource, 1);
            *seen = (RpmhDbSeenResource) {
                .entry = e,
                .aux = db + offset,
                .slave = slave,
                .version = version,
            };
            g_hash_table_insert(names, g_steal_pointer(&name), seen);
            for (i = 0; i < RPMH_RESOURCES; i++) {
                QcomRpmhResource *r = &s->resources[i];
                const RpmhResourceDesc *desc = &rpmh_resources[i];
                unsigned k;
                if (strncmp(desc->name, (const char *)e, 8)) {
                    continue;
                }
                if (rpmh_slave(desc->kind) != slave || !address) {
                    error_setg(errp, "RPMh %s has unexpected slave type",
                               desc->name);
                    return false;
                }
                r->address = address;
                r->present = true;
                if (desc->kind == RPMH_ARC) {
                    if (!strcmp(desc->name, "xo.lvl")) {
                        r->level_count = 4;
                    } else {
                        if (!len || len > 32 || (len & 1)) {
                            error_setg(errp, "RPMh %s has invalid ARC aux data",
                                       desc->name);
                            return false;
                        }
                        r->level_count = len / 2;
                        for (k = 0; k < r->level_count; k++) {
                            r->levels[k] = lduw_le_p(db + offset + 2 * k);
                            if (k && !r->levels[k]) {
                                r->level_count = k;
                                break;
                            }
                            if (k && r->levels[k] < r->levels[k - 1]) {
                                error_setg(errp, "RPMh ARC levels not "
                                           "monotonic");
                                return false;
                            }
                        }
                        if (r->levels[0] != 0 || r->level_count < 2) {
                            error_setg(errp, "RPMh ARC table lacks "
                                       "off/on levels");
                            return false;
                        }
                    }
                } else if (desc->kind == RPMH_BCM) {
                    if (len != 8 || !ldl_le_p(db + offset)) {
                        error_setg(errp, "RPMh %s has invalid BCM aux data",
                                   desc->name);
                        return false;
                    }
                    r->unit = ldl_le_p(db + offset);
                    r->width = lduw_le_p(db + offset + 4);
                    r->vcd = db[offset + 6];
                }
                break;
            }
        }
    }
    if (!groups) {
        error_setg(errp, "RPMh command-db has no resource groups");
        return false;
    }
    /*
     * Unknown names can alias known hardware (e.g. vrm.xob/lnbclka1).
     * Do not create two independent state machines for one known address.
     * Auxiliary-only .mol records have address zero, without writable state.
     */
    for (i = 0; i < RPMH_RESOURCES; i++) {
        if (!s->resources[i].present) {
            continue;
        }
        for (j = 0; j < i; j++) {
            if (s->resources[j].present &&
                s->resources[i].address == s->resources[j].address) {
                error_setg(errp, "RPMh aliases between modeled resources "
                           "are unsupported: %s / %s", rpmh_resources[i].name,
                           rpmh_resources[j].name);
                return false;
            }
        }
    }
    return true;
}

static void rpmh_reset(DeviceState *dev)
{
    QcomSm8250RpmhState *s = QCOM_SM8250_RPMH(dev);
    unsigned d, t, i;

    timer_del(s->timer);
    for (d = 0; d < RPMH_DRVS; d++) {
        QcomRpmhDrv *drv = &s->drv[d];
        drv->irq_status = drv->irq_enable = 0;
        drv->wake_hi = drv->wake_lo = 0;
        drv->sleeping = false;
        memset(drv->tcs, 0, sizeof(drv->tcs));
        for (t = 0; t < RPMH_TCS_PER_DRV; t++) {
            drv->tcs[t].deadline = -1;
            drv->tcs[t].cursor = -1;
        }
    }
    for (i = 0; i < RPMH_RESOURCES; i++) {
        QcomRpmhResource *r = &s->resources[i];
        memset(r->votes, 0, sizeof(r->votes));
        memset(r->staged, 0, sizeof(r->staged));
        memset(r->dirty, 0, sizeof(r->dirty));
    }
    if (s->boot_configured && s->resources[0].present) {
        /* Explicit firmware handoff: AP's XO vote, replaceable by Linux. */
        s->resources[0].votes[2][0] = 3;
    }
    rpmh_outputs(s);
}

static int rpmh_post_load(void *opaque, int version_id)
{
    QcomSm8250RpmhState *s = opaque;
    unsigned d, t, i, f;

    if (memcmp(s->digest, s->expected_digest, sizeof(s->digest))) {
        return -EINVAL;
    }
    for (i = 0; i < RPMH_RESOURCES; i++) {
        QcomRpmhResource *r = &s->resources[i];
        for (d = 0; d < RPMH_DRVS; d++) {
            if (rpmh_resources[i].kind == RPMH_BCM) {
                if (r->votes[d][0] > 0x3fff || r->votes[d][1] > 0x3fff ||
                    r->votes[d][2] > 1 || r->votes[d][3] ||
                    (!r->votes[d][2] && (r->votes[d][0] || r->votes[d][1])) ||
                    (r->staged[d] & ~(BCM_MASK & ~BCM_COMMIT))) {
                    return -EINVAL;
                }
            } else {
                if (r->staged[d] || r->dirty[d]) {
                    return -EINVAL;
                }
                for (f = 0; f < 4; f++) {
                    if (r->votes[d][f] &&
                        !rpmh_value_valid(s, i, f, r->votes[d][f])) {
                        return -EINVAL;
                    }
                }
            }
            if (!r->present && (r->votes[d][0] || r->votes[d][1] ||
                r->votes[d][2] || r->votes[d][3] || r->staged[d] ||
                r->dirty[d])) {
                return -EINVAL;
            }
        }
    }
    for (d = 0; d < RPMH_DRVS; d++) {
        QcomRpmhDrv *drv = &s->drv[d];
        if ((drv->irq_enable | drv->irq_status) & ~0xffU ||
            drv->wake_hi & ~0xffffffU) {
            return -EINVAL;
        }
        for (t = 0; t < RPMH_TCS_PER_DRV; t++) {
            QcomRpmhTcs *tcs = &drv->tcs[t];
            if (((tcs->wait | tcs->enable) & ~0xffffU) ||
                (tcs->control & ~(AMC_ENABLE | AMC_TRIGGER)) ||
                ((tcs->control & AMC_TRIGGER) &&
                 !(tcs->control & AMC_ENABLE)) ||
                tcs->cursor < -1 || tcs->cursor > RPMH_COMMANDS ||
                tcs->deadline < -1) {
                return -EINVAL;
            }
            if (tcs->running) {
                if (tcs->cursor < 0 || tcs->cursor >= RPMH_COMMANDS ||
                    !(tcs->enable & BIT(tcs->cursor)) ||
                    tcs->commands[tcs->cursor].status != CMD_ISSUED ||
                    (tcs->stalled ? tcs->deadline != -1 :
                                    tcs->deadline < 0)) {
                    return -EINVAL;
                }
            } else if (tcs->stalled || tcs->deadline != -1) {
                return -EINVAL;
            }
            for (i = 0; i < RPMH_COMMANDS; i++) {
                uint32_t status = tcs->commands[i].status;
                if ((status & ~(CMD_ISSUED | CMD_COMPLETE)) ||
                    (status == CMD_COMPLETE) || tcs->commands[i].response) {
                    return -EINVAL;
                }
            }
        }
    }
    s->ready = true;
    rpmh_outputs(s);
    rpmh_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_rpmh_command = {
    .name = TYPE_QCOM_SM8250_RPMH "/command",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(msgid, QcomRpmhCommand),
        VMSTATE_UINT32(address, QcomRpmhCommand),
        VMSTATE_UINT32(data, QcomRpmhCommand),
        VMSTATE_UINT32(status, QcomRpmhCommand),
        VMSTATE_UINT32(response, QcomRpmhCommand),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_rpmh_tcs = {
    .name = TYPE_QCOM_SM8250_RPMH "/tcs",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(wait, QcomRpmhTcs),
        VMSTATE_UINT32(control, QcomRpmhTcs),
        VMSTATE_UINT32(enable, QcomRpmhTcs),
        VMSTATE_STRUCT_ARRAY(commands, QcomRpmhTcs, RPMH_COMMANDS, 1,
                             vmstate_rpmh_command, QcomRpmhCommand),
        VMSTATE_INT32(cursor, QcomRpmhTcs),
        VMSTATE_INT64(deadline, QcomRpmhTcs),
        VMSTATE_BOOL(running, QcomRpmhTcs),
        VMSTATE_BOOL(stalled, QcomRpmhTcs),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_rpmh_drv = {
    .name = TYPE_QCOM_SM8250_RPMH "/drv",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(irq_enable, QcomRpmhDrv),
        VMSTATE_UINT32(irq_status, QcomRpmhDrv),
        VMSTATE_UINT32(wake_hi, QcomRpmhDrv),
        VMSTATE_UINT32(wake_lo, QcomRpmhDrv),
        VMSTATE_BOOL(sleeping, QcomRpmhDrv),
        VMSTATE_STRUCT_ARRAY(tcs, QcomRpmhDrv, RPMH_TCS_PER_DRV, 1,
                             vmstate_rpmh_tcs, QcomRpmhTcs),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_rpmh_resource = {
    .name = TYPE_QCOM_SM8250_RPMH "/resource",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_2DARRAY(votes, QcomRpmhResource, RPMH_DRVS, 4),
        VMSTATE_UINT32_ARRAY(staged, QcomRpmhResource, RPMH_DRVS),
        VMSTATE_BOOL_ARRAY(dirty, QcomRpmhResource, RPMH_DRVS),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_rpmh = {
    .name = TYPE_QCOM_SM8250_RPMH,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = rpmh_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_BUFFER(digest, QcomSm8250RpmhState),
        VMSTATE_UINT64_EQUAL(command_latency_ns, QcomSm8250RpmhState),
        VMSTATE_STRUCT_ARRAY(drv, QcomSm8250RpmhState, RPMH_DRVS, 1,
                             vmstate_rpmh_drv, QcomRpmhDrv),
        VMSTATE_STRUCT_ARRAY(resources, QcomSm8250RpmhState, RPMH_RESOURCES, 1,
                             vmstate_rpmh_resource, QcomRpmhResource),
        VMSTATE_CLOCK(xo_board, QcomSm8250RpmhState),
        VMSTATE_END_OF_LIST()
    },
};

static void rpmh_get_resource(Object *obj, Visitor *visitor, const char *name,
                               void *opaque, Error **errp)
{
    QcomSm8250RpmhState *s = QCOM_SM8250_RPMH(obj);
    unsigned encoded = GPOINTER_TO_UINT(opaque);
    unsigned i = encoded / 8, field = encoded % 8;
    uint64_t value;

    switch (field) {
    case 0:
        value = s->resources[i].present;
        break;
    case 1:
        value = s->resources[i].address;
        break;
    case 2:
        value = rpmh_aggregate(s, i, 0);
        break;
    case 3:
        value = rpmh_enabled(s, i);
        break;
    case 4:
        value = rpmh_aggregate(s, i, 2);
        break;
    case 5:
        value = rpmh_aggregate(s, i, 0);
        break;
    case 6:
        value = rpmh_aggregate(s, i, 1);
        break;
    case 7:
        value = s->resources[i].level_count;
        break;
    default:
        g_assert_not_reached();
    }
    visit_type_uint64(visitor, name, &value, errp);
}

static char *rpmh_get_db_sha256(Object *obj, Error **errp)
{
    QcomSm8250RpmhState *s = QCOM_SM8250_RPMH(obj);
    return g_strdup(s->cmd_db_sha256 ? s->cmd_db_sha256 : "");
}

static void rpmh_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250RpmhState *s = QCOM_SM8250_RPMH(dev);
    g_autoptr(GChecksum) checksum = NULL;
    gsize digest_len = sizeof(s->digest);
    size_t size = RPMH_CMD_DB_SIZE;

    if (s->command_latency_ns < 1 || s->command_latency_ns > 1000000000) {
        error_setg(errp, "RPMh command-latency-ns must be 1..1000000000");
        return;
    }
    if (s->reference_cmd_db == (s->cmd_db_file != NULL)) {
        error_setg(errp, "RPMh requires exactly one of cmd-db-file or "
                   "reference-cmd-db=on (synthetic test data)");
        return;
    }
    if (s->reference_cmd_db) {
        rpmh_reference_db(s);
    } else {
        g_autofree char *contents = NULL;
        g_autoptr(GError) err = NULL;
        struct stat st;
        gsize len;
        if (stat(s->cmd_db_file, &st) < 0 || !S_ISREG(st.st_mode) ||
            st.st_size < DB_HEADER_SIZE || st.st_size > RPMH_CMD_DB_SIZE) {
            error_setg(errp, "RPMh cmd-db-file must be a regular 144..131072 "
                       "byte command-db image");
            return;
        }
        if (!g_file_get_contents(s->cmd_db_file, &contents, &len, &err)) {
            error_setg(errp, "RPMh cannot load command-db: %s", err->message);
            return;
        }
        if (len < DB_HEADER_SIZE || len > RPMH_CMD_DB_SIZE) {
            error_setg(errp, "RPMh command-db file size changed while loading");
            return;
        }
        memcpy(s->command_db, contents, len);
        size = len;
    }
    if (!rpmh_parse_db(s, size, errp)) {
        return;
    }
    if (s->boot_configured && !s->resources[0].present) {
        error_setg(errp, "RPMh boot-configured requires an xo.lvl resource");
        return;
    }
    checksum = g_checksum_new(G_CHECKSUM_SHA256);
    g_checksum_update(checksum, s->command_db, sizeof(s->command_db));
    s->cmd_db_sha256 = g_strdup(g_checksum_get_string(checksum));
    g_checksum_get_digest(checksum, s->digest, &digest_len);
    memcpy(s->expected_digest, s->digest, sizeof(s->digest));
    s->ready = true;
    rpmh_reset(dev);
}

static void rpmh_init(Object *obj)
{
    QcomSm8250RpmhState *s = QCOM_SM8250_RPMH(obj);
    DeviceState *dev = DEVICE(obj);
    static const char * const fields[] = {
        "present", "address", "value", "enabled", "mode",
        "vote-x", "vote-y", "level-count",
    };
    unsigned i, f;

    QEMU_BUILD_BUG_ON(ARRAY_SIZE(rpmh_resources) != RPMH_RESOURCES);
    for (i = 0; i < RPMH_DRVS; i++) {
        g_autofree char *name = g_strdup_printf(
            TYPE_QCOM_SM8250_RPMH ".drv%u", i);
        s->drv[i].owner = s;
        memory_region_init_io(&s->drv[i].iomem, obj, &rpmh_ops, &s->drv[i],
                              name, RPMH_DRV_SIZE);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->drv[i].iomem);
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->drv[i].irq);
    }
    memory_region_init_io(&s->command_db_region, obj, &rpmh_db_ops, s,
                          TYPE_QCOM_SM8250_RPMH ".command-db",
                          RPMH_CMD_DB_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->command_db_region);
    qdev_init_gpio_out_named(dev, s->power, "power", RPMH_RESOURCES);
    qdev_init_gpio_in_named(dev, rpmh_sleep, "sleep", RPMH_DRVS);
    s->xo_board = qdev_init_clock_in(dev, "xo-board", rpmh_clock_changed,
                                     s, ClockUpdate);
    for (i = 0; i < RPMH_RESOURCES; i++) {
        const RpmhResourceDesc *desc = &rpmh_resources[i];
        if (desc->clock[0]) {
            s->clocks[i] = qdev_init_clock_out(dev, desc->clock);
        }
        for (f = 0; f < ARRAY_SIZE(fields); f++) {
            g_autofree char *name = g_strdup_printf("res.%s.%s", desc->name,
                                                    fields[f]);
            object_property_add(obj, name, "uint64", rpmh_get_resource,
                                NULL, NULL, GUINT_TO_POINTER(i * 8 + f));
        }
    }
    object_property_add_str(obj, "cmd-db-sha256", rpmh_get_db_sha256, NULL);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, rpmh_timer, s);
}

static void rpmh_finalize(Object *obj)
{
    QcomSm8250RpmhState *s = QCOM_SM8250_RPMH(obj);
    timer_free(s->timer);
    g_free(s->cmd_db_sha256);
}

static const Property rpmh_properties[] = {
    DEFINE_PROP_STRING("cmd-db-file", QcomSm8250RpmhState, cmd_db_file),
    DEFINE_PROP_BOOL("reference-cmd-db", QcomSm8250RpmhState,
                     reference_cmd_db, false),
    DEFINE_PROP_BOOL("boot-configured", QcomSm8250RpmhState,
                     boot_configured, false),
    DEFINE_PROP_UINT64("command-latency-ns", QcomSm8250RpmhState,
                       command_latency_ns, 1000),
};

static void rpmh_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->desc = "Qualcomm SM8250 RSC with typed RPMh digital resources";
    dc->realize = rpmh_realize;
    dc->vmsd = &vmstate_rpmh;
    device_class_set_props(dc, rpmh_properties);
    device_class_set_legacy_reset(dc, rpmh_reset);
}

static const TypeInfo rpmh_info = {
    .name = TYPE_QCOM_SM8250_RPMH,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250RpmhState),
    .instance_init = rpmh_init,
    .instance_finalize = rpmh_finalize,
    .class_init = rpmh_class_init,
};

static void rpmh_register_types(void)
{
    type_register_static(&rpmh_info);
}
type_init(rpmh_register_types)
