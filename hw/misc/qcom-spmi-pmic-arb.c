/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm SPMI PMIC arbiter v5, explicitly selected reference profile.
 *
 * Protocol/geometry: pinned Linux and Qualcomm drivers in
 * tb371fc/docs/spmi-pmic-arb.md. Native VERSION/APID/ownership registers
 * have not been captured. This deterministic table is a reference fixture.
 * No regulator, charger, ADC, GPIO, RTC, or analog PMIC is synthesized.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-spmi-pmic-arb.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bswap.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define ARB_APIDS              512
#define ARB_EES                8
#define ARB_OBSERVERS           (ARB_APIDS * ARB_EES)
#define ARB_VERSION            0x50000000u
#define ARB_MAP_BASE           0x900
#define ARB_OWNER_BASE         0x700
#define ARB_RW_STRIDE          0x10000
#define ARB_OBS_STRIDE         0x80
#define ARB_CMD                0x00
#define ARB_STATUS             0x08
#define ARB_WDATA0             0x10
#define ARB_WDATA1             0x14
#define ARB_RDATA0             0x18
#define ARB_RDATA1             0x1c
#define ARB_ACC_ENABLE         0x100
#define ARB_IRQ_STATUS         0x104
#define ARB_IRQ_CLEAR          0x108
#define ARB_DONE               1u
#define ARB_FAILURE            2u
#define ARB_DENIED             4u
#define ARB_DROPPED            8u
#define ARB_CMD_FIELDS         0xf8000ff7u
#define ARB_EXT_WRITEL         0u
#define ARB_EXT_READL          1u
#define ARB_TEST_FIRST         4
#define ARB_TEST_COUNT         2
#define PMIC_RT                0x10
#define PMIC_TYPE              0x11
#define PMIC_POL_HIGH          0x12
#define PMIC_POL_LOW           0x13
#define PMIC_LATCH_CLEAR       0x14
#define PMIC_ENABLE_SET        0x15
#define PMIC_ENABLE_CLEAR      0x16
#define PMIC_LATCH_STATUS      0x18

typedef struct SpmiChannel {
    uint32_t command;
    uint32_t status;
    uint32_t wdata[2];
    uint32_t rdata[2];
    uint32_t pending_data[2];
    int64_t deadline;
    bool busy;
} SpmiChannel;

typedef struct SpmiPeripheralIrq {
    uint8_t raw;
    uint8_t type;
    uint8_t polarity_high;
    uint8_t polarity_low;
    uint8_t enabled;
    uint8_t latched;
} SpmiPeripheralIrq;

typedef struct SpmiWindow {
    QcomSpmiPmicArbState *s;
    unsigned index;
} SpmiWindow;

typedef struct SpmiReferenceEntry {
    uint16_t ppid;
    uint8_t owner;
    uint8_t subtype;
    bool irq_owner;
} SpmiReferenceEntry;

/*
 * Primary identity PPIDs follow captured DT SID names and Linux subtype
 * definitions. APID numbering and all owners are reference assumptions.
 * SID15 peripherals are synthetic and absent unless test-peripheral=on.
 */
static const SpmiReferenceEntry spmi_reference[] = {
    { 0x001, 0, 0x1e, false }, /* PM8150 */
    { 0x201, 0, 0x20, false }, /* PM8150B */
    { 0x401, 0, 0x1f, false }, /* PM8150L */
    { 0xa01, 0, 0x24, false }, /* PM8009 */
    { 0xffe, 0, 0, true },    /* Synthetic IRQ peripheral, EE0 */
    { 0xffd, 1, 0, true },    /* Synthetic IRQ peripheral, EE1 */
};

struct QcomSpmiPmicArbState {
    SysBusDevice parent_obj;
    MemoryRegion regions[QCOM_SPMI_PMIC_ARB_NUM_REGIONS];
    SpmiWindow windows[QCOM_SPMI_PMIC_ARB_NUM_REGIONS];
    qemu_irq irq;
    QEMUTimer *timer;
    SpmiChannel rw[ARB_APIDS];
    SpmiChannel obs[ARB_OBSERVERS];
    SpmiPeripheralIrq peripheral[ARB_TEST_COUNT];
    uint8_t access_enable[ARB_APIDS];
    uint8_t irq_pending[ARB_APIDS];
    uint64_t transaction_delay_ns;
    uint32_t execution_environment;
    uint32_t profile_key;
    bool reference_map;
    bool test_peripheral;
};

static const SpmiReferenceEntry *spmi_entry(QcomSpmiPmicArbState *s,
                                           unsigned apid)
{
    if (apid >= ARRAY_SIZE(spmi_reference) ||
        (apid >= ARB_TEST_FIRST && !s->test_peripheral)) {
        return NULL;
    }
    return &spmi_reference[apid];
}

static uint8_t spmi_level_active(const SpmiPeripheralIrq *p)
{
    return ~p->type & ((p->raw & p->polarity_high) |
                       (~p->raw & p->polarity_low));
}

static uint8_t spmi_visible_irq(QcomSpmiPmicArbState *s, unsigned apid)
{
    if (!s->test_peripheral || apid < ARB_TEST_FIRST ||
        apid >= ARB_TEST_FIRST + ARB_TEST_COUNT || !s->access_enable[apid]) {
        return 0;
    }
    return s->irq_pending[apid] &
           s->peripheral[apid - ARB_TEST_FIRST].enabled;
}

static void spmi_update_output(QcomSpmiPmicArbState *s)
{
    bool active = false;

    for (unsigned i = 0; i < ARB_TEST_COUNT; i++) {
        unsigned apid = ARB_TEST_FIRST + i;
        const SpmiReferenceEntry *entry = spmi_entry(s, apid);

        active |= entry && entry->owner == s->execution_environment &&
                  spmi_visible_irq(s, apid);
    }
    qemu_set_irq(s->irq, active);
}

/*
 * Peripheral and arbiter latches are separate. Clearing the arbiter does
 * not acknowledge the peripheral. A following peripheral ACK removes an
 * edge, or relatches a still-active level and delivers it again.
 */
static void spmi_propagate_peripheral(QcomSpmiPmicArbState *s, unsigned index)
{
    SpmiPeripheralIrq *p = &s->peripheral[index];

    p->latched |= spmi_level_active(p);
    s->irq_pending[ARB_TEST_FIRST + index] |= p->latched & p->enabled;
    spmi_update_output(s);
}

static void spmi_test_input(void *opaque, int line, int level)
{
    QcomSpmiPmicArbState *s = opaque;
    unsigned index = line / 8;
    uint8_t bit = 1u << (line % 8);
    SpmiPeripheralIrq *p = &s->peripheral[index];
    uint8_t previous = p->raw;
    uint8_t rising, falling;

    p->raw = level ? p->raw | bit : p->raw & ~bit;
    rising = ~previous & p->raw;
    falling = previous & ~p->raw;
    p->latched |= p->type & ((rising & p->polarity_high) |
                             (falling & p->polarity_low));
    spmi_propagate_peripheral(s, index);
}

static bool spmi_read_byte(QcomSpmiPmicArbState *s, unsigned apid,
                           unsigned offset, uint8_t *value)
{
    const SpmiReferenceEntry *entry = spmi_entry(s, apid);
    static const uint8_t test_rom[8] = {
        'T', 'B', '3', '7', '1', 'F', 'C', 1,
    };
    SpmiPeripheralIrq *p;

    if (!entry) {
        return false;
    }
    if (apid < ARB_TEST_FIRST) {
        if (offset == 4) {
            *value = 0x51; /* PMIC type, from the primary Linux driver. */
        } else if (offset == 5) {
            *value = entry->subtype;
        } else if (apid == 0 && (offset == 2 || offset == 3)) {
            /* Firmware PM8150 revision 3.0; not a register capture. */
            *value = offset == 2 ? 0 : 3;
        } else {
            return false; /* REV1/REV2/fab and other revisions unmeasured. */
        }
        return true;
    }
    p = &s->peripheral[apid - ARB_TEST_FIRST];
    if (offset < sizeof(test_rom)) {
        *value = test_rom[offset];
        return true;
    }
    switch (offset) {
    case PMIC_RT:
        *value = p->raw;
        break;
    case PMIC_TYPE:
        *value = p->type;
        break;
    case PMIC_POL_HIGH:
        *value = p->polarity_high;
        break;
    case PMIC_POL_LOW:
        *value = p->polarity_low;
        break;
    case PMIC_ENABLE_SET:
    case PMIC_ENABLE_CLEAR:
        *value = p->enabled;
        break;
    case PMIC_LATCH_STATUS:
        *value = p->latched;
        break;
    default:
        return false;
    }
    return true;
}

static bool spmi_byte_writable(QcomSpmiPmicArbState *s, unsigned apid,
                               unsigned offset)
{
    return s->test_peripheral && apid >= ARB_TEST_FIRST &&
           apid < ARB_TEST_FIRST + ARB_TEST_COUNT &&
           offset >= PMIC_TYPE && offset <= PMIC_ENABLE_CLEAR;
}

static void spmi_write_byte(SpmiPeripheralIrq *p, unsigned offset, uint8_t value)
{
    switch (offset) {
    case PMIC_TYPE:
        p->type = value;
        break;
    case PMIC_POL_HIGH:
        p->polarity_high = value;
        break;
    case PMIC_POL_LOW:
        p->polarity_low = value;
        break;
    case PMIC_LATCH_CLEAR:
        p->latched &= ~value;
        break;
    case PMIC_ENABLE_SET:
        p->enabled |= value;
        break;
    case PMIC_ENABLE_CLEAR:
        p->enabled &= ~value;
        break;
    default:
        g_assert_not_reached();
    }
}

static uint32_t spmi_execute(QcomSpmiPmicArbState *s, SpmiChannel *c,
                             unsigned apid, bool observer, unsigned ee)
{
    const SpmiReferenceEntry *entry = spmi_entry(s, apid);
    unsigned opcode = c->command >> 27;
    unsigned offset = (c->command >> 4) & 0xff;
    unsigned length = (c->command & 7) + 1;
    uint8_t data[8] = { 0 };

    if (!entry || (c->command & ~ARB_CMD_FIELDS) || offset + length > 256 ||
        opcode != (observer ? ARB_EXT_READL : ARB_EXT_WRITEL)) {
        return ARB_DONE | ARB_FAILURE;
    }
    if (ee != s->execution_environment ||
        (!observer && entry->owner != s->execution_environment)) {
        return ARB_DONE | ARB_DENIED;
    }
    if (observer) {
        /* Validate every byte before publishing any received data. */
        for (unsigned i = 0; i < length; i++) {
            if (!spmi_read_byte(s, apid, offset + i, &data[i])) {
                return ARB_DONE | ARB_FAILURE;
            }
        }
        c->rdata[0] = ldl_le_p(data);
        c->rdata[1] = ldl_le_p(data + 4);
    } else {
        /* No partial mutation on a cross-register or read-only failure. */
        for (unsigned i = 0; i < length; i++) {
            if (!spmi_byte_writable(s, apid, offset + i)) {
                return ARB_DONE | ARB_FAILURE;
            }
        }
        stl_le_p(data, c->pending_data[0]);
        stl_le_p(data + 4, c->pending_data[1]);
        for (unsigned i = 0; i < length; i++) {
            spmi_write_byte(&s->peripheral[apid - ARB_TEST_FIRST],
                             offset + i, data[i]);
        }
        spmi_propagate_peripheral(s, apid - ARB_TEST_FIRST);
    }
    return ARB_DONE;
}

static void spmi_schedule(QcomSpmiPmicArbState *s)
{
    int64_t next = INT64_MAX;

    for (unsigned i = 0; i < ARB_APIDS; i++) {
        if (s->rw[i].busy) {
            next = MIN(next, s->rw[i].deadline);
        }
    }
    for (unsigned i = 0; i < ARB_OBSERVERS; i++) {
        if (s->obs[i].busy) {
            next = MIN(next, s->obs[i].deadline);
        }
    }
    if (next == INT64_MAX) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static void spmi_finish(QcomSpmiPmicArbState *s, SpmiChannel *c,
                        unsigned apid, bool observer, unsigned ee, int64_t now)
{
    if (c->busy && c->deadline <= now) {
        c->status = spmi_execute(s, c, apid, observer, ee);
        c->busy = false;
        c->deadline = -1;
        if (c->status != ARB_DONE) {
            qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SPMI_PMIC_ARB
                          ": %s APID %u command 0x%08x status 0x%x"
                          " (unimplemented, invalid or denied transaction)\n",
                          observer ? "observer" : "write", apid,
                          c->command, c->status);
        }
    }
}

static void spmi_timer(void *opaque)
{
    QcomSpmiPmicArbState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    for (unsigned i = 0; i < ARB_APIDS; i++) {
        spmi_finish(s, &s->rw[i], i, false, s->execution_environment, now);
    }
    for (unsigned i = 0; i < ARB_OBSERVERS; i++) {
        spmi_finish(s, &s->obs[i], i % ARB_APIDS, true, i / ARB_APIDS, now);
    }
    spmi_schedule(s);
}

static void spmi_start(QcomSpmiPmicArbState *s, SpmiChannel *c, uint32_t value)
{
    if (c->busy) {
        /* Reference collision policy: drop both, with no peripheral write. */
        c->busy = false;
        c->deadline = -1;
        c->status = ARB_DONE | ARB_DROPPED;
        memset(c->rdata, 0, sizeof(c->rdata));
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SPMI_PMIC_ARB
                      ": command written while busy; transaction dropped\n");
        spmi_schedule(s);
        return;
    }
    c->command = value;
    c->status = 0;
    memset(c->rdata, 0, sizeof(c->rdata));
    memcpy(c->pending_data, c->wdata, sizeof(c->pending_data));
    c->busy = true;
    c->deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + s->transaction_delay_ns;
    spmi_schedule(s);
}

static MemTxResult spmi_bad_access(SpmiWindow *w, hwaddr offset,
                                   unsigned size, bool write)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SPMI_PMIC_ARB
                  ": unsupported %s region %u offset 0x%" HWADDR_PRIx
                  " size %u\n", write ? "write" : "read", w->index,
                  offset, size);
    return MEMTX_ERROR;
}

static SpmiChannel *spmi_channel(SpmiWindow *w, hwaddr offset,
                                 unsigned *apid, unsigned *reg)
{
    QcomSpmiPmicArbState *s = w->s;

    if (w->index == QCOM_SPMI_PMIC_ARB_CHNLS) {
        *apid = offset / ARB_RW_STRIDE;
        *reg = offset % ARB_RW_STRIDE;
        return *apid < ARB_APIDS ? &s->rw[*apid] : NULL;
    }
    if (w->index == QCOM_SPMI_PMIC_ARB_OBSRVR) {
        unsigned ee = offset / ARB_RW_STRIDE;

        *apid = (offset % ARB_RW_STRIDE) / ARB_OBS_STRIDE;
        *reg = offset % ARB_OBS_STRIDE;
        return ee < ARB_EES && *apid < ARB_APIDS ?
               &s->obs[ee * ARB_APIDS + *apid] : NULL;
    }
    return NULL;
}

static MemTxResult spmi_read(void *opaque, hwaddr offset, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    SpmiWindow *w = opaque;
    QcomSpmiPmicArbState *s = w->s;
    const SpmiReferenceEntry *entry;
    SpmiChannel *c;
    unsigned apid = 0, reg = 0;

    *value = 0;
    if (size != 4 || (offset & 3)) {
        return spmi_bad_access(w, offset, size, false);
    }
    switch (w->index) {
    case QCOM_SPMI_PMIC_ARB_CORE:
        if (offset == 0) {
            *value = ARB_VERSION;
        } else if (offset == 4) {
            *value = ARB_APIDS;
        } else if (offset >= ARB_MAP_BASE &&
                   offset < ARB_MAP_BASE + ARB_APIDS * 4) {
            entry = spmi_entry(s, (offset - ARB_MAP_BASE) / 4);
            *value = entry ? ((uint32_t)entry->ppid << 8) |
                              ((uint32_t)entry->irq_owner << 24) : 0;
        } else {
            return spmi_bad_access(w, offset, size, false);
        }
        return MEMTX_OK;
    case QCOM_SPMI_PMIC_ARB_CNFG:
        if (offset < ARB_OWNER_BASE ||
            offset >= ARB_OWNER_BASE + ARB_APIDS * 4) {
            return spmi_bad_access(w, offset, size, false);
        }
        entry = spmi_entry(s, (offset - ARB_OWNER_BASE) / 4);
        *value = entry ? entry->owner : 0;
        return MEMTX_OK;
    case QCOM_SPMI_PMIC_ARB_INTR: {
        unsigned ee = offset / ARB_RW_STRIDE;
        unsigned word = (offset % ARB_RW_STRIDE) / 4;

        if (ee >= ARB_EES || word >= ARB_APIDS / 32) {
            return spmi_bad_access(w, offset, size, false);
        }
        for (unsigned bit = 0; bit < 32; bit++) {
            apid = word * 32 + bit;
            entry = spmi_entry(s, apid);
            if (entry && entry->irq_owner && entry->owner == ee &&
                spmi_visible_irq(s, apid)) {
                *value |= 1u << bit;
            }
        }
        return MEMTX_OK;
    }
    default:
        break;
    }
    c = spmi_channel(w, offset, &apid, &reg);
    if (!c) {
        return spmi_bad_access(w, offset, size, false);
    }
    switch (reg) {
    case ARB_CMD:
        *value = c->command;
        break;
    case ARB_STATUS:
        *value = c->status;
        break;
    case ARB_WDATA0:
    case ARB_WDATA1:
        *value = c->wdata[(reg - ARB_WDATA0) / 4];
        break;
    case ARB_RDATA0:
    case ARB_RDATA1:
        *value = c->rdata[(reg - ARB_RDATA0) / 4];
        break;
    case ARB_ACC_ENABLE:
    case ARB_IRQ_STATUS:
        if (w->index != QCOM_SPMI_PMIC_ARB_CHNLS) {
            return spmi_bad_access(w, offset, size, false);
        }
        *value = reg == ARB_ACC_ENABLE ? s->access_enable[apid] :
                                         s->irq_pending[apid];
        break;
    default:
        return spmi_bad_access(w, offset, size, false);
    }
    return MEMTX_OK;
}

static MemTxResult spmi_write(void *opaque, hwaddr offset, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    SpmiWindow *w = opaque;
    QcomSpmiPmicArbState *s = w->s;
    const SpmiReferenceEntry *entry;
    SpmiChannel *c;
    unsigned apid = 0, reg = 0;

    if (size != 4 || (offset & 3)) {
        return spmi_bad_access(w, offset, size, true);
    }
    c = spmi_channel(w, offset, &apid, &reg);
    if (!c) {
        return spmi_bad_access(w, offset, size, true);
    }
    switch (reg) {
    case ARB_CMD:
        spmi_start(s, c, value);
        break;
    case ARB_WDATA0:
    case ARB_WDATA1:
        if (w->index != QCOM_SPMI_PMIC_ARB_CHNLS) {
            return spmi_bad_access(w, offset, size, true);
        }
        c->wdata[(reg - ARB_WDATA0) / 4] = value;
        break;
    case ARB_ACC_ENABLE:
    case ARB_IRQ_CLEAR:
        entry = spmi_entry(s, apid);
        if (w->index != QCOM_SPMI_PMIC_ARB_CHNLS || !entry ||
            !entry->irq_owner || entry->owner != s->execution_environment ||
            (reg == ARB_ACC_ENABLE ? value & ~1u : value & ~0xffu)) {
            return spmi_bad_access(w, offset, size, true);
        }
        if (reg == ARB_ACC_ENABLE) {
            s->access_enable[apid] = value;
        } else {
            s->irq_pending[apid] &= ~value;
        }
        spmi_update_output(s);
        break;
    default:
        return spmi_bad_access(w, offset, size, true);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps spmi_ops = {
    .read_with_attrs = spmi_read,
    .write_with_attrs = spmi_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Keep malformed accesses intact so the callback rejects all of them. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void spmi_reset(Object *obj, ResetType type)
{
    QcomSpmiPmicArbState *s = QCOM_SPMI_PMIC_ARB(obj);

    timer_del(s->timer);
    memset(s->rw, 0, sizeof(s->rw));
    memset(s->obs, 0, sizeof(s->obs));
    memset(s->access_enable, 0, sizeof(s->access_enable));
    memset(s->irq_pending, 0, sizeof(s->irq_pending));
    for (unsigned i = 0; i < ARB_APIDS; i++) {
        s->rw[i].deadline = -1;
    }
    for (unsigned i = 0; i < ARB_OBSERVERS; i++) {
        s->obs[i].deadline = -1;
    }
    for (unsigned i = 0; i < ARB_TEST_COUNT; i++) {
        uint8_t raw = s->peripheral[i].raw;

        memset(&s->peripheral[i], 0, sizeof(s->peripheral[i]));
        s->peripheral[i].raw = raw; /* A held external input survives reset. */
    }
    spmi_update_output(s);
}

static bool spmi_channel_valid(const SpmiChannel *c)
{
    return !(c->status & ~0xfu) &&
           (c->busy ? c->deadline >= 0 && c->status == 0 : c->deadline == -1);
}

static int spmi_post_load(void *opaque, int version_id)
{
    QcomSpmiPmicArbState *s = opaque;

    for (unsigned i = 0; i < ARB_APIDS; i++) {
        if (!spmi_channel_valid(&s->rw[i]) || s->access_enable[i] > 1) {
            return -EINVAL;
        }
    }
    for (unsigned i = 0; i < ARB_OBSERVERS; i++) {
        if (!spmi_channel_valid(&s->obs[i])) {
            return -EINVAL;
        }
    }
    spmi_schedule(s);
    spmi_update_output(s);
    return 0;
}

static const VMStateDescription vmstate_spmi_channel = {
    .name = TYPE_QCOM_SPMI_PMIC_ARB "/channel",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(command, SpmiChannel),
        VMSTATE_UINT32(status, SpmiChannel),
        VMSTATE_UINT32_ARRAY(wdata, SpmiChannel, 2),
        VMSTATE_UINT32_ARRAY(rdata, SpmiChannel, 2),
        VMSTATE_UINT32_ARRAY(pending_data, SpmiChannel, 2),
        VMSTATE_INT64(deadline, SpmiChannel),
        VMSTATE_BOOL(busy, SpmiChannel),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_spmi_peripheral = {
    .name = TYPE_QCOM_SPMI_PMIC_ARB "/synthetic-irq",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT8(raw, SpmiPeripheralIrq),
        VMSTATE_UINT8(type, SpmiPeripheralIrq),
        VMSTATE_UINT8(polarity_high, SpmiPeripheralIrq),
        VMSTATE_UINT8(polarity_low, SpmiPeripheralIrq),
        VMSTATE_UINT8(enabled, SpmiPeripheralIrq),
        VMSTATE_UINT8(latched, SpmiPeripheralIrq),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_spmi = {
    .name = TYPE_QCOM_SPMI_PMIC_ARB,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = spmi_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile_key, QcomSpmiPmicArbState),
        VMSTATE_UINT64_EQUAL(transaction_delay_ns, QcomSpmiPmicArbState),
        VMSTATE_STRUCT_ARRAY(rw, QcomSpmiPmicArbState, ARB_APIDS, 1,
                             vmstate_spmi_channel, SpmiChannel),
        VMSTATE_STRUCT_ARRAY(obs, QcomSpmiPmicArbState, ARB_OBSERVERS, 1,
                             vmstate_spmi_channel, SpmiChannel),
        VMSTATE_STRUCT_ARRAY(peripheral, QcomSpmiPmicArbState, ARB_TEST_COUNT, 1,
                             vmstate_spmi_peripheral, SpmiPeripheralIrq),
        VMSTATE_UINT8_ARRAY(access_enable, QcomSpmiPmicArbState, ARB_APIDS),
        VMSTATE_UINT8_ARRAY(irq_pending, QcomSpmiPmicArbState, ARB_APIDS),
        VMSTATE_TIMER_PTR(timer, QcomSpmiPmicArbState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property spmi_properties[] = {
    DEFINE_PROP_BOOL("reference-map", QcomSpmiPmicArbState, reference_map, false),
    DEFINE_PROP_BOOL("test-peripheral", QcomSpmiPmicArbState,
                     test_peripheral, false),
    DEFINE_PROP_UINT32("execution-environment", QcomSpmiPmicArbState,
                       execution_environment, 0),
    DEFINE_PROP_UINT64("transaction-delay-ns", QcomSpmiPmicArbState,
                       transaction_delay_ns, 1000),
};

static void spmi_realize(DeviceState *dev, Error **errp)
{
    QcomSpmiPmicArbState *s = QCOM_SPMI_PMIC_ARB(dev);

    if (!s->reference_map) {
        error_setg(errp, TYPE_QCOM_SPMI_PMIC_ARB
                    " requires explicit reference-map=on; native VERSION"
                    " and APID ownership are not captured");
        return;
    }
    if (s->execution_environment >= ARB_EES || !s->transaction_delay_ns ||
        s->transaction_delay_ns > 1000000000) {
        error_setg(errp, TYPE_QCOM_SPMI_PMIC_ARB
                    " requires execution-environment 0..7 and"
                    " transaction-delay-ns 1..1000000000");
        return;
    }
    s->profile_key = 0x50000000u | s->execution_environment |
                     ((uint32_t)s->test_peripheral << 8);
    if (s->test_peripheral) {
        qdev_init_gpio_in_named(dev, spmi_test_input, "test-irq",
                               ARB_TEST_COUNT * 8);
    }
}

static void spmi_init(Object *obj)
{
    QcomSpmiPmicArbState *s = QCOM_SPMI_PMIC_ARB(obj);
    static const hwaddr sizes[] = { 0x1100, 0x2000000, 0x100000, 0xa0000, 0x26000 };
    static const char * const names[] = {
        "core", "chnls", "obsrvr", "intr", "cnfg",
    };

    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, spmi_timer, s);
    for (unsigned i = 0; i < QCOM_SPMI_PMIC_ARB_NUM_REGIONS; i++) {
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_SPMI_PMIC_ARB "/%s",
                                               names[i]);

        s->windows[i].s = s;
        s->windows[i].index = i;
        memory_region_init_io(&s->regions[i], obj, &spmi_ops, &s->windows[i],
                              name, sizes[i]);
        sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->regions[i]);
    }
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
}

static void spmi_finalize(Object *obj)
{
    timer_free(QCOM_SPMI_PMIC_ARB(obj)->timer);
}

static void spmi_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm SPMI PMIC arbiter v5 explicit reference subset";
    dc->realize = spmi_realize;
    dc->vmsd = &vmstate_spmi;
    device_class_set_props(dc, spmi_properties);
    rc->phases.hold = spmi_reset;
}

static const TypeInfo spmi_type = {
    .name = TYPE_QCOM_SPMI_PMIC_ARB,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSpmiPmicArbState),
    .instance_init = spmi_init,
    .instance_finalize = spmi_finalize,
    .class_init = spmi_class_init,
};

static void spmi_register_types(void)
{
    type_register_static(&spmi_type);
}

type_init(spmi_register_types)
