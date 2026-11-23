/*
 * Qualcomm config NoC SBM: an explicit, bounded reference handshake peer.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Interface: OnePlusOSS/android_kernel_oneplus_sm8250, commit
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae,
 * drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c.
 *
 * The actual DT assigns low ports 0..7. Connected-at-cold-reset, the 1000 ns
 * latency and external peer stalls are reference policies, not measured
 * silicon timing, a firmware handoff dump, or real camera/display/venus drain.
 * No DMA path, IRQ, Clock or voltage is controlled by this component.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-noc-sbm.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/bitops.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define PORT_COUNT 8
#define PORT_MASK UINT32_C(0xff)
#define LATENCY_NS UINT32_C(1000)
#define REFERENCE_ID UINT32_C(0x53424d31) /* SBM1 */

enum {
    BANK_CLEAR,
    BANK_SET,
    BANK_SENSE,
};

const QcomNocSbmRegion qcom_noc_sbm_regions[QCOM_NOC_SBM_WINDOWS] = {
    { 0x01506080, 4, "clear" },
    { 0x01506088, 4, "set" },
    { 0x01506100, 4, "sense" },
};

typedef struct QcomNocSbmWindow {
    QcomNocSbmState *state;
    unsigned bank;
} QcomNocSbmWindow;

struct QcomNocSbmState {
    SysBusDevice parent_obj;
    MemoryRegion mmio[QCOM_NOC_SBM_WINDOWS];
    QcomNocSbmWindow window[QCOM_NOC_SBM_WINDOWS];
    QEMUTimer *timer;
    bool enabled_reference;
    bool ready;

    /* Immutable 48-byte VMState prefix, including the fixed cold policy. */
    uint32_t profile_marker;
    uint32_t instance_id;
    uint64_t clear_address;
    uint64_t set_address;
    uint64_t sense_address;
    uint32_t supported_port_mask;
    uint32_t reference_latency_ns;
    uint32_t reference_identity;
    uint32_t boot_disconnected_mask;

    uint32_t peer_stall_mask;
    uint32_t requested_mask;
    uint32_t sense_mask;
    uint32_t pending_mask;
    uint64_t remaining_ns[PORT_COUNT];

    /* Model MMIO diagnostics, not additional hardware registers. */
    uint64_t accepted_set_writes;
    uint64_t accepted_clear_writes;
    uint32_t last_set_mask;
    uint32_t last_clear_mask;

    /* Destination-local virtual deadlines; never serialized. */
    int64_t deadline_ns[PORT_COUNT];
};

static bool deadline_fits(int64_t now, uint64_t delay)
{
    return now >= 0 && delay <= (uint64_t)(INT64_MAX - now);
}

static uint64_t remaining_at(QcomNocSbmState *s, unsigned port, int64_t now)
{
    uint32_t bit = BIT(port);

    if (!(s->pending_mask & bit)) {
        return 0;
    }
    if (s->peer_stall_mask & bit) {
        return s->remaining_ns[port];
    }
    if (s->deadline_ns[port] <= now) {
        return 0;
    }
    return (uint64_t)s->deadline_ns[port] - (uint64_t)now;
}

static void schedule_pending(QcomNocSbmState *s)
{
    uint32_t active = s->pending_mask & ~s->peer_stall_mask;
    int64_t earliest = INT64_MAX;
    unsigned i;

    if (!s->ready || !s->enabled_reference || !active) {
        timer_del(s->timer);
        return;
    }
    for (i = 0; i < PORT_COUNT; i++) {
        if (active & BIT(i)) {
            earliest = MIN(earliest, s->deadline_ns[i]);
        }
    }
    timer_mod_ns(s->timer, earliest);
}

static void sbm_timer(void *opaque)
{
    QcomNocSbmState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (!s->ready || !s->enabled_reference) {
        return;
    }
    for (i = 0; i < PORT_COUNT; i++) {
        uint32_t bit = BIT(i);

        if ((s->pending_mask & bit) && !(s->peer_stall_mask & bit) &&
            s->deadline_ns[i] <= now) {
            s->sense_mask = (s->sense_mask & ~bit) | (s->requested_mask & bit);
            s->pending_mask &= ~bit;
            s->remaining_ns[i] = 0;
            s->deadline_ns[i] = -1;
        }
    }
    schedule_pending(s);
}

static MemTxResult reject_access(QcomNocSbmWindow *w, hwaddr offset,
                                 uint64_t value, unsigned size, bool write,
                                 const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_NOC_SBM
                  ": %s %s %s+0x%" HWADDR_PRIx " width=%u value=0x%" PRIx64 "\n",
                  reason, write ? "write" : "read",
                  qcom_noc_sbm_regions[w->bank].suffix, offset, size, value);
    return MEMTX_ERROR;
}

static MemTxResult sbm_read(void *opaque, hwaddr offset, uint64_t *value,
                           unsigned size, MemTxAttrs attrs)
{
    QcomNocSbmWindow *w = opaque;
    QcomNocSbmState *s = w->state;

    *value = 0;
    if (!s->enabled_reference) {
        return reject_access(w, offset, 0, size, false, "unknown profile");
    }
    if (offset || size != 4) {
        return reject_access(w, offset, 0, size, false, "unsupported register");
    }
    if (w->bank != BANK_SENSE) {
        return reject_access(w, offset, 0, size, false, "write-only register");
    }
    /* Pure read: only the virtual timer commits a pending peer response. */
    *value = s->sense_mask;
    return MEMTX_OK;
}

static MemTxResult sbm_write(void *opaque, hwaddr offset, uint64_t value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomNocSbmWindow *w = opaque;
    QcomNocSbmState *s = w->state;
    uint32_t mask, requested, pending, starting;
    int64_t now;
    unsigned i;

    if (!s->enabled_reference) {
        return reject_access(w, offset, value, size, true, "unknown profile");
    }
    if (offset || size != 4) {
        return reject_access(w, offset, value, size, true, "unsupported register");
    }
    if (w->bank == BANK_SENSE) {
        return reject_access(w, offset, value, size, true, "read-only register");
    }
    if (value & ~((uint64_t)PORT_MASK)) {
        return reject_access(w, offset, value, size, true, "unsupported port bits");
    }
    mask = value;
    requested = w->bank == BANK_SET ? s->requested_mask | mask
                                    : s->requested_mask & ~mask;
    pending = requested ^ s->sense_mask;
    starting = pending & ~s->pending_mask;
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    /* Check the whole multi-port transaction before changing any state. */
    if ((starting & ~s->peer_stall_mask) && !deadline_fits(now, LATENCY_NS)) {
        return reject_access(w, offset, value, size, true,
                             "virtual deadline overflow");
    }
    for (i = 0; i < PORT_COUNT; i++) {
        uint32_t bit = BIT(i);

        if (!(pending & bit)) {
            /* Returning to current sense cancels an outstanding request. */
            s->remaining_ns[i] = 0;
            s->deadline_ns[i] = -1;
        } else if (starting & bit) {
            s->remaining_ns[i] = LATENCY_NS;
            s->deadline_ns[i] = s->peer_stall_mask & bit ? -1 : now + LATENCY_NS;
        }
        /* Repeated requests preserve the original deadline/paused remainder. */
    }
    s->requested_mask = requested;
    s->pending_mask = pending;
    if (w->bank == BANK_SET) {
        if (s->accepted_set_writes != UINT64_MAX) {
            s->accepted_set_writes++;
        }
        s->last_set_mask = mask;
    } else {
        if (s->accepted_clear_writes != UINT64_MAX) {
            s->accepted_clear_writes++;
        }
        s->last_clear_mask = mask;
    }
    schedule_pending(s);
    return MEMTX_OK;
}

static const MemoryRegionOps sbm_ops = {
    .read_with_attrs = sbm_read,
    .write_with_attrs = sbm_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .valid.unaligned = false,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static void sbm_reset_enter(Object *obj, ResetType type)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);
    unsigned i;

    timer_del(s->timer);
    s->requested_mask = s->sense_mask = s->pending_mask = 0;
    s->accepted_set_writes = s->accepted_clear_writes = 0;
    s->last_set_mask = s->last_clear_mask = 0;
    for (i = 0; i < PORT_COUNT; i++) {
        s->remaining_ns[i] = 0;
        s->deadline_ns[i] = -1;
    }
    /* External reference-peer input survives system reset, like an environment. */
}

static bool state_valid(QcomNocSbmState *s)
{
    uint32_t all = s->peer_stall_mask | s->requested_mask | s->sense_mask |
                   s->pending_mask | s->last_set_mask | s->last_clear_mask;
    unsigned i;

    if ((all & ~PORT_MASK) ||
        s->pending_mask != (s->requested_mask ^ s->sense_mask) ||
        (!s->accepted_set_writes &&
         (s->last_set_mask || s->requested_mask || s->sense_mask)) ||
        (!s->accepted_clear_writes &&
         (s->last_clear_mask || (s->sense_mask & ~s->requested_mask)))) {
        return false;
    }
    for (i = 0; i < PORT_COUNT; i++) {
        if (s->remaining_ns[i] > LATENCY_NS ||
            (!(s->pending_mask & BIT(i)) && s->remaining_ns[i])) {
            return false;
        }
    }
    if (!s->enabled_reference &&
        (all || s->accepted_set_writes || s->accepted_clear_writes)) {
        return false;
    }
    return true;
}

static int sbm_pre_save(void *opaque)
{
    QcomNocSbmState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    /* One clock sample; do not acknowledge an expired but undispatched timer. */
    for (i = 0; i < PORT_COUNT; i++) {
        s->remaining_ns[i] = remaining_at(s, i, now);
    }
    if (!state_valid(s)) {
        error_report(TYPE_QCOM_NOC_SBM ": invalid reference state before save");
        return -EINVAL;
    }
    return 0;
}

static int sbm_pre_load(void *opaque)
{
    QcomNocSbmState *s = opaque;

    s->ready = false;
    timer_del(s->timer);
    return 0;
}

static int sbm_post_load(void *opaque, int version)
{
    QcomNocSbmState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (!state_valid(s)) {
        error_report(TYPE_QCOM_NOC_SBM ": invalid migrated reference state");
        return -EINVAL;
    }
    for (i = 0; i < PORT_COUNT; i++) {
        uint32_t bit = BIT(i);

        if ((s->pending_mask & bit) && !(s->peer_stall_mask & bit) &&
            !deadline_fits(now, s->remaining_ns[i])) {
            error_report(TYPE_QCOM_NOC_SBM
                         ": migrated handshake cannot fit target virtual time");
            return -EINVAL;
        }
    }
    for (i = 0; i < PORT_COUNT; i++) {
        uint32_t bit = BIT(i);

        s->deadline_ns[i] = (s->pending_mask & bit) && !(s->peer_stall_mask & bit)
                           ? now + s->remaining_ns[i] : -1;
    }
    s->ready = true;
    schedule_pending(s);
    return 0;
}

static const VMStateDescription vmstate_sbm = {
    .name = TYPE_QCOM_NOC_SBM,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = sbm_pre_save,
    .pre_load = sbm_pre_load,
    .post_load = sbm_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile_marker, QcomNocSbmState),
        VMSTATE_UINT32_EQUAL(instance_id, QcomNocSbmState),
        VMSTATE_UINT64_EQUAL(clear_address, QcomNocSbmState),
        VMSTATE_UINT64_EQUAL(set_address, QcomNocSbmState),
        VMSTATE_UINT64_EQUAL(sense_address, QcomNocSbmState),
        VMSTATE_UINT32_EQUAL(supported_port_mask, QcomNocSbmState),
        VMSTATE_UINT32_EQUAL(reference_latency_ns, QcomNocSbmState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomNocSbmState),
        VMSTATE_UINT32_EQUAL(boot_disconnected_mask, QcomNocSbmState),
        VMSTATE_UINT32(peer_stall_mask, QcomNocSbmState),
        VMSTATE_UINT32(requested_mask, QcomNocSbmState),
        VMSTATE_UINT32(sense_mask, QcomNocSbmState),
        VMSTATE_UINT32(pending_mask, QcomNocSbmState),
        VMSTATE_UINT64_ARRAY(remaining_ns, QcomNocSbmState, PORT_COUNT),
        VMSTATE_UINT64(accepted_set_writes, QcomNocSbmState),
        VMSTATE_UINT64(accepted_clear_writes, QcomNocSbmState),
        VMSTATE_UINT32(last_set_mask, QcomNocSbmState),
        VMSTATE_UINT32(last_clear_mask, QcomNocSbmState),
        VMSTATE_END_OF_LIST()
    },
};

enum {
    Q_INSTANCE,
    Q_CLEAR_ADDRESS,
    Q_SET_ADDRESS,
    Q_SENSE_ADDRESS,
    Q_PORT_MASK,
    Q_PORT_COUNT,
    Q_LATENCY,
    Q_IDENTITY,
    Q_BOOT_MASK,
    Q_WINDOWS,
    Q_REGISTERS,
    Q_BYTES,
    Q_REQUESTED = 100,
    Q_SENSE,
    Q_PENDING,
    Q_SET_COUNT,
    Q_CLEAR_COUNT,
    Q_LAST_SET,
    Q_LAST_CLEAR,
    Q_REMAINING = 200,
    Q_DEADLINE = 300,
};

static void scalar_get(Object *obj, Visitor *v, const char *name,
                       void *opaque, Error **errp)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);
    unsigned key = GPOINTER_TO_UINT(opaque);
    uint64_t value = 0;
    uint32_t value32;
    bool wide = false;

    if (key >= Q_REQUESTED && !s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_NOC_SBM ": unknown profile");
        return;
    }
    if (key >= Q_DEADLINE) {
        unsigned port = key - Q_DEADLINE;
        int64_t deadline = s->deadline_ns[port];

        visit_type_int64(v, name, &deadline, errp);
        return;
    }
    if (key >= Q_REMAINING) {
        value = remaining_at(s, key - Q_REMAINING,
                             qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
        wide = true;
    } else {
        switch (key) {
        case Q_INSTANCE: value = s->instance_id; break;
        case Q_CLEAR_ADDRESS: value = s->clear_address; wide = true; break;
        case Q_SET_ADDRESS: value = s->set_address; wide = true; break;
        case Q_SENSE_ADDRESS: value = s->sense_address; wide = true; break;
        case Q_PORT_MASK: value = s->supported_port_mask; break;
        case Q_PORT_COUNT: value = PORT_COUNT; break;
        case Q_LATENCY: value = s->reference_latency_ns; break;
        case Q_IDENTITY: value = s->reference_identity; break;
        case Q_BOOT_MASK: value = s->boot_disconnected_mask; break;
        case Q_WINDOWS: value = QCOM_NOC_SBM_WINDOWS; break;
        case Q_REGISTERS: value = QCOM_NOC_SBM_WINDOWS; break;
        case Q_BYTES: value = QCOM_NOC_SBM_WINDOWS * 4; break;
        case Q_REQUESTED: value = s->requested_mask; break;
        case Q_SENSE: value = s->sense_mask; break;
        case Q_PENDING: value = s->pending_mask; break;
        case Q_SET_COUNT: value = s->accepted_set_writes; wide = true; break;
        case Q_CLEAR_COUNT: value = s->accepted_clear_writes; wide = true; break;
        case Q_LAST_SET: value = s->last_set_mask; break;
        case Q_LAST_CLEAR: value = s->last_clear_mask; break;
        default: g_assert_not_reached();
        }
    }
    if (wide) {
        visit_type_uint64(v, name, &value, errp);
    } else {
        value32 = value;
        visit_type_uint32(v, name, &value32, errp);
    }
}

static void mmio_enabled_get(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);
    bool value = s->enabled_reference;

    visit_type_bool(v, name, &value, errp);
}

static void stall_get(Object *obj, Visitor *v, const char *name,
                      void *opaque, Error **errp)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);
    uint32_t value = s->peer_stall_mask;

    visit_type_uint32(v, name, &value, errp);
}

static void stall_set(Object *obj, Visitor *v, const char *name,
                      void *opaque, Error **errp)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);
    uint64_t remaining[PORT_COUNT];
    uint32_t value;
    int64_t now;
    unsigned i;

    if (!visit_type_uint32(v, name, &value, errp)) {
        return;
    }
    if (!DEVICE(obj)->realized || !s->ready || !s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_NOC_SBM
                   ": peer stall input requires a realized reference profile");
        return;
    }
    if (value & ~PORT_MASK) {
        error_setg(errp, TYPE_QCOM_NOC_SBM
                   ": reference-peer-stall-mask supports only ports 0..7");
        return;
    }
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    for (i = 0; i < PORT_COUNT; i++) {
        uint32_t bit = BIT(i);

        remaining[i] = remaining_at(s, i, now);
        if ((s->pending_mask & s->peer_stall_mask & ~value & bit) &&
            !deadline_fits(now, remaining[i])) {
            error_setg(errp, TYPE_QCOM_NOC_SBM
                       ": peer stall change would overflow the virtual deadline");
            return;
        }
    }
    for (i = 0; i < PORT_COUNT; i++) {
        uint32_t bit = BIT(i);

        if (!(s->pending_mask & bit)) {
            continue;
        }
        if (value & bit) {
            s->remaining_ns[i] = remaining[i];
            s->deadline_ns[i] = -1;
        } else if (s->peer_stall_mask & bit) {
            s->remaining_ns[i] = remaining[i];
            s->deadline_ns[i] = now + remaining[i];
        }
    }
    s->peer_stall_mask = value;
    /* Even an unstalled zero remainder is committed by the timer, not here. */
    schedule_pending(s);
}

static void sbm_realize(DeviceState *dev, Error **errp)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(dev);
    unsigned i;

    s->profile_marker = s->enabled_reference ? 1 : 0;
    s->instance_id = 0;
    s->clear_address = qcom_noc_sbm_regions[BANK_CLEAR].physical_base;
    s->set_address = qcom_noc_sbm_regions[BANK_SET].physical_base;
    s->sense_address = qcom_noc_sbm_regions[BANK_SENSE].physical_base;
    s->supported_port_mask = PORT_MASK;
    s->reference_latency_ns = LATENCY_NS;
    s->reference_identity = REFERENCE_ID;
    s->boot_disconnected_mask = 0;
    for (i = 0; i < QCOM_NOC_SBM_WINDOWS; i++) {
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_NOC_SBM "-%s",
                                                qcom_noc_sbm_regions[i].suffix);

        s->window[i].state = s;
        s->window[i].bank = i;
        memory_region_init_io(&s->mmio[i], OBJECT(s), &sbm_ops,
                              &s->window[i], name, 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio[i]);
        /* Applied after global qdev properties: off retains actual holes. */
        memory_region_set_enabled(&s->mmio[i], s->enabled_reference);
    }
    s->ready = true;
}

static void sbm_unrealize(DeviceState *dev)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(dev);

    s->ready = false;
    timer_del(s->timer);
}

static void sbm_init(Object *obj)
{
    static const struct {
        const char *name, *type;
        unsigned key;
    } properties[] = {
        { "instance-id", "uint32", Q_INSTANCE },
        { "clear-address", "uint64", Q_CLEAR_ADDRESS },
        { "set-address", "uint64", Q_SET_ADDRESS },
        { "sense-address", "uint64", Q_SENSE_ADDRESS },
        { "supported-port-mask", "uint32", Q_PORT_MASK },
        { "port-count", "uint32", Q_PORT_COUNT },
        { "reference-latency-ns", "uint32", Q_LATENCY },
        { "reference-identity", "uint32", Q_IDENTITY },
        { "reference-boot-disconnected-mask", "uint32", Q_BOOT_MASK },
        { "mapped-window-count", "uint32", Q_WINDOWS },
        { "mapped-register-count", "uint32", Q_REGISTERS },
        { "mapped-bytes", "uint32", Q_BYTES },
        { "requested-mask", "uint32", Q_REQUESTED },
        { "sense-mask", "uint32", Q_SENSE },
        { "pending-mask", "uint32", Q_PENDING },
        { "accepted-set-writes", "uint64", Q_SET_COUNT },
        { "accepted-clear-writes", "uint64", Q_CLEAR_COUNT },
        { "last-set-mask", "uint32", Q_LAST_SET },
        { "last-clear-mask", "uint32", Q_LAST_CLEAR },
    };
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);
    unsigned i;

    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, sbm_timer, s);
    for (i = 0; i < ARRAY_SIZE(properties); i++) {
        object_property_add(obj, properties[i].name, properties[i].type,
                            scalar_get, NULL, NULL,
                            GUINT_TO_POINTER(properties[i].key));
    }
    for (i = 0; i < PORT_COUNT; i++) {
        g_autofree char *remaining = g_strdup_printf("port%u-remaining-ns", i);
        g_autofree char *deadline = g_strdup_printf("port%u-deadline-ns", i);

        s->deadline_ns[i] = -1;
        object_property_add(obj, remaining, "uint64", scalar_get, NULL, NULL,
                            GUINT_TO_POINTER(Q_REMAINING + i));
        object_property_add(obj, deadline, "int64", scalar_get, NULL, NULL,
                            GUINT_TO_POINTER(Q_DEADLINE + i));
    }
    object_property_add(obj, "mmio-enabled", "bool", mmio_enabled_get,
                        NULL, NULL, NULL);
    object_property_add(obj, "reference-peer-stall-mask", "uint32", stall_get,
                        stall_set, NULL, NULL);
}

static void sbm_finalize(Object *obj)
{
    QcomNocSbmState *s = QCOM_NOC_SBM(obj);

    timer_free(s->timer);
}

static const Property sbm_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomNocSbmState,
                     enabled_reference, false),
};

static void sbm_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm config NoC SBM explicit reference handshake peer";
    dc->realize = sbm_realize;
    dc->unrealize = sbm_unrealize;
    dc->vmsd = &vmstate_sbm;
    device_class_set_props(dc, sbm_properties);
    rc->phases.enter = sbm_reset_enter;
}

static const TypeInfo sbm_info = {
    .name = TYPE_QCOM_NOC_SBM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomNocSbmState),
    .instance_init = sbm_init,
    .instance_finalize = sbm_finalize,
    .class_init = sbm_class_init,
};

static void sbm_register_types(void)
{
    type_register_static(&sbm_info);
}
type_init(sbm_register_types)
