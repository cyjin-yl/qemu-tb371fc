/*
 * TB371FC opt-in DSU PMU: one cluster bank and an explicit virtual clock.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Register layout: Arm DSU TRM 100453_0401_04, chapter 13. Only CPU_CYCLES
 * is advertised and implemented. This is neither a physical clock model
 * nor a source of fabricated cache, bus, power or firmware observations.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/main-loop.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "hw/core/boards.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/sysbus.h"
#include "hw/misc/tb371fc-dsu-pmu.h"
#include "system/qtest.h"
#include "system/runstate.h"
#include "system/tcg.h"

OBJECT_DECLARE_SIMPLE_TYPE(TB371FCDSUPMUState, TB371FC_DSU_PMU)

struct TB371FCDSUPMUState {
    SysBusDevice parent_obj;
    TB371FCDSUPMULink link;
    TB371FCDSUPMUWire bank;
    TB371FCDSUPMUWire incoming;
    uint64_t frequency_hz;
    int64_t last_ns;
    int64_t deadline_ns;
    QEMUTimer *timer;
    qemu_irq irq;
    VMChangeStateEntry *vmchange;
    bool ready;
    bool irq_level;
    bool loading;
    bool incoming_valid;
    uint8_t load_started;
    uint8_t load_completed;
};

static TB371FCDSUPMUWire dsu_reset_bank(TB371FCDSUPMUState *s)
{
    return (TB371FCDSUPMUWire) {
        .magic = TB371FC_DSU_WIRE_MAGIC,
        .cpu_mask = TB371FC_DSU_CPU_MASK,
        .irq_intid = TB371FC_DSU_IRQ_INTID,
        .event_counters = TB371FC_DSU_EVENT_COUNTERS,
        .frequency_hz = s->frequency_hz,
    };
}

static bool dsu_event_counts(const TB371FCDSUPMUWire *w, unsigned i)
{
    uint32_t type = w->event_type[i];

    /*
     * This virtual reference classifies its event-0x11 ticks as Non-secure.
     * Apply the specified NS/S filter; this is a profile choice, not proof
     * of the security attribution of physical cluster-clock events.
     */
    return (type & 0xffff) == 0x11 &&
           ((type >> 29) & 1) == ((type >> 31) & 1);
}

static uint64_t dsu_ticks(uint64_t elapsed, uint64_t hz, uint64_t fraction,
                          uint64_t *next_fraction)
{
    uint64_t tail = (elapsed % 1000000000) * hz + fraction;

    /* elapsed <= INT64_MAX and hz <= 1 GHz keep both products in uint64. */
    *next_fraction = tail % 1000000000;
    return (elapsed / 1000000000) * hz + tail / 1000000000;
}

static void dsu_sync(TB371FCDSUPMUState *s)
{
    TB371FCDSUPMUWire *w = &s->bank;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    uint64_t ticks;
    unsigned i;

    g_assert(bql_locked());
    if (s->loading) {
        return;
    }
    g_assert(now >= s->last_ns);
    ticks = dsu_ticks(now - s->last_ns, s->frequency_hz, w->fraction,
                      &w->fraction);
    s->last_ns = now;
    if (!(w->pmcr & 1) || !ticks) {
        return;
    }
    if (w->count_enable & (UINT32_C(1) << 31)) {
        if (ticks > UINT64_MAX - w->cycle_count) {
            w->overflow |= UINT32_C(1) << 31;
        }
        w->cycle_count += ticks;
    }
    for (i = 0; i < TB371FC_DSU_EVENT_COUNTERS; i++) {
        if ((w->count_enable & (1U << i)) && dsu_event_counts(w, i)) {
            uint64_t total = w->event_count[i] + ticks;

            if (total > UINT32_MAX) {
                w->overflow |= 1U << i;
            }
            w->event_count[i] = total;
        }
    }
}

static void dsu_update_irq(TB371FCDSUPMUState *s)
{
    bool level = !s->loading &&
                 (s->bank.overflow & s->bank.interrupt_enable) != 0;

    s->irq_level = level;
    qemu_set_irq(s->irq, level);
}

static void dsu_schedule(TB371FCDSUPMUState *s)
{
    const TB371FCDSUPMUWire *w = &s->bank;
    uint64_t distance = UINT64_MAX, available, unused, delay, whole;
    int64_t tail;
    unsigned i;

    timer_del(s->timer);
    s->deadline_ns = 0;
    if (s->loading || !(w->pmcr & 1)) {
        return;
    }
    if ((w->count_enable & ~w->overflow & (UINT32_C(1) << 31)) &&
        w->cycle_count != 0) {
        distance = -w->cycle_count;
    }
    for (i = 0; i < TB371FC_DSU_EVENT_COUNTERS; i++) {
        if ((w->count_enable & ~w->overflow & (1U << i)) &&
            dsu_event_counts(w, i)) {
            distance = MIN(distance, (UINT64_C(1) << 32) - w->event_count[i]);
        }
    }
    available = INT64_MAX - s->last_ns;
    if (distance == UINT64_MAX ||
        distance > dsu_ticks(available, s->frequency_hz, w->fraction,
                             &unused)) {
        return;
    }
    /* ceil((distance * 1e9 - fraction) / hz), without a wide product. */
    whole = (distance / s->frequency_hz) * UINT64_C(1000000000);
    tail = (int64_t)((distance % s->frequency_hz) * UINT64_C(1000000000)) -
           (int64_t)w->fraction;
    if (tail < 0) {
        delay = whole - ((uint64_t)-tail / s->frequency_hz);
    } else {
        delay = whole + ((uint64_t)tail + s->frequency_hz - 1) /
                        s->frequency_hz;
    }
    g_assert(delay > 0 && delay <= available);
    s->deadline_ns = s->last_ns + delay;
    timer_mod_ns(s->timer, s->deadline_ns);
}

static void dsu_publish(TB371FCDSUPMUState *s)
{
    dsu_update_irq(s);
    dsu_schedule(s);
}

static void dsu_timer(void *opaque)
{
    TB371FCDSUPMUState *s = opaque;

    dsu_sync(s);
    dsu_publish(s);
}

static uint64_t dsu_read(void *opaque, unsigned reg)
{
    TB371FCDSUPMUState *s = opaque;
    TB371FCDSUPMUWire *w = &s->bank;
    uint64_t value;

    g_assert(bql_locked() && !s->loading);
    dsu_sync(s);
    switch (reg) {
    case TB371FC_DSU_PMCR:
        value = TB371FC_DSU_PMCR_FIXED | w->pmcr;
        break;
    case TB371FC_DSU_CNTENSET:
    case TB371FC_DSU_CNTENCLR:
        value = w->count_enable;
        break;
    case TB371FC_DSU_OVSSET:
    case TB371FC_DSU_OVSCLR:
        value = w->overflow;
        break;
    case TB371FC_DSU_SELR:
        value = w->selector;
        break;
    case TB371FC_DSU_INTENSET:
    case TB371FC_DSU_INTENCLR:
        value = w->interrupt_enable;
        break;
    case TB371FC_DSU_CCNTR:
        value = w->cycle_count;
        break;
    case TB371FC_DSU_XEVTYPER:
        value = w->selector < TB371FC_DSU_EVENT_COUNTERS ?
                w->event_type[w->selector] : 0;
        break;
    case TB371FC_DSU_XEVCNTR:
        value = w->selector < TB371FC_DSU_EVENT_COUNTERS ?
                w->event_count[w->selector] : 0;
        break;
    case TB371FC_DSU_CEID0_REG:
        value = TB371FC_DSU_CEID0;
        break;
    case TB371FC_DSU_CEID1_REG:
        value = 0;
        break;
    default:
        g_assert_not_reached();
    }
    dsu_publish(s);
    return value;
}

static void dsu_write(void *opaque, unsigned reg, uint64_t value)
{
    TB371FCDSUPMUState *s = opaque;
    TB371FCDSUPMUWire *w = &s->bank;
    uint32_t bits = value & TB371FC_DSU_COUNTER_MASK;

    g_assert(bql_locked() && !s->loading);
    dsu_sync(s);
    switch (reg) {
    case TB371FC_DSU_PMCR:
        w->pmcr = value & 1;
        if (value & 2) {
            memset(w->event_count, 0, sizeof(w->event_count));
        }
        if (value & 4) {
            w->cycle_count = 0;
        }
        break;
    case TB371FC_DSU_CNTENSET: w->count_enable |= bits; break;
    case TB371FC_DSU_CNTENCLR: w->count_enable &= ~bits; break;
    case TB371FC_DSU_OVSSET: w->overflow |= bits; break;
    case TB371FC_DSU_OVSCLR: w->overflow &= ~bits; break;
    case TB371FC_DSU_SELR: w->selector = value & 31; break;
    case TB371FC_DSU_INTENSET: w->interrupt_enable |= bits; break;
    case TB371FC_DSU_INTENCLR: w->interrupt_enable &= ~bits; break;
    case TB371FC_DSU_CCNTR: w->cycle_count = value; break;
    case TB371FC_DSU_XEVTYPER:
        if (w->selector < TB371FC_DSU_EVENT_COUNTERS) {
            w->event_type[w->selector] = value & TB371FC_DSU_EVENT_TYPE_MASK;
        }
        break;
    case TB371FC_DSU_XEVCNTR:
        if (w->selector < TB371FC_DSU_EVENT_COUNTERS) {
            w->event_count[w->selector] = value;
        }
        break;
    case TB371FC_DSU_CEID0_REG:
    case TB371FC_DSU_CEID1_REG:
        /* TRM: read-only/write-ignored, not a write-UNDEFINED encoding. */
        break;
    default:
        g_assert_not_reached();
    }
    dsu_publish(s);
}

static int dsu_snapshot(void *opaque, TB371FCDSUPMUWire *wire)
{
    TB371FCDSUPMUState *s = opaque;

    if (s->loading) {
        error_report(TYPE_TB371FC_DSU_PMU ": cannot save unvalidated state");
        return -EINVAL;
    }
    dsu_sync(s);
    dsu_publish(s);
    *wire = s->bank;
    return 0;
}

static int dsu_begin_load(void *opaque, unsigned index)
{
    TB371FCDSUPMUState *s = opaque;

    if (index == 0) {
        if (s->loading && s->load_started) {
            error_report(TYPE_TB371FC_DSU_PMU ": invalid incoming CPU order");
            return -EINVAL;
        }
        /* Before CPU0 reads incoming bytes, retract the old warm IRQ. */
        s->loading = true;
        s->incoming_valid = false;
        s->load_started = 0;
        s->load_completed = 0;
        timer_del(s->timer);
        s->deadline_ns = 0;
        dsu_update_irq(s);
    }
    if (index >= 8 || !s->loading ||
        s->load_started != (1U << index) - 1 ||
        s->load_completed != (1U << index) - 1) {
        error_report(TYPE_TB371FC_DSU_PMU ": invalid incoming CPU order");
        return -EINVAL;
    }
    s->load_started |= 1U << index;
    return 0;
}

static int dsu_stage_load(void *opaque, const TB371FCDSUPMUWire *wire)
{
    TB371FCDSUPMUState *s = opaque;
    unsigned i;

    if (!s->loading || s->load_started != 1 || s->load_completed != 0 ||
        s->incoming_valid || wire->magic != TB371FC_DSU_WIRE_MAGIC ||
        wire->cpu_mask != TB371FC_DSU_CPU_MASK ||
        wire->irq_intid != TB371FC_DSU_IRQ_INTID ||
        wire->event_counters != TB371FC_DSU_EVENT_COUNTERS ||
        wire->frequency_hz != s->frequency_hz ||
        (wire->pmcr & ~1U) ||
        ((wire->count_enable | wire->overflow | wire->interrupt_enable) &
         ~TB371FC_DSU_COUNTER_MASK) ||
        wire->selector > 31 || wire->fraction >= UINT64_C(1000000000)) {
        goto invalid;
    }
    for (i = 0; i < TB371FC_DSU_EVENT_COUNTERS; i++) {
        if (wire->event_type[i] & ~TB371FC_DSU_EVENT_TYPE_MASK) {
            goto invalid;
        }
    }
    s->incoming = *wire;
    s->incoming_valid = true;
    return 0;

invalid:
    error_report(TYPE_TB371FC_DSU_PMU ": invalid incoming shared bank");
    return -EINVAL;
}

static int dsu_complete_load(void *opaque, unsigned index)
{
    TB371FCDSUPMUState *s = opaque;

    if (index >= 8 || !s->loading || !s->incoming_valid ||
        s->load_started != (1U << (index + 1)) - 1 ||
        s->load_completed != (1U << index) - 1) {
        error_report(TYPE_TB371FC_DSU_PMU ": incomplete incoming CPU profile");
        return -EINVAL;
    }
    s->load_completed |= 1U << index;
    if (s->load_completed == TB371FC_DSU_CPU_MASK) {
        s->bank = s->incoming;
        s->last_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        s->loading = false;
        dsu_publish(s);
    }
    return 0;
}

static void dsu_reset(DeviceState *dev)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(dev);

    s->bank = dsu_reset_bank(s);
    s->last_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->loading = false;
    s->incoming_valid = false;
    s->load_started = s->load_completed = 0;
    memset(&s->incoming, 0, sizeof(s->incoming));
    dsu_publish(s);
}

static void dsu_vm_change(void *opaque, bool running, RunState state)
{
    TB371FCDSUPMUState *s = opaque;

    /*
     * Save a stable bank on pause; host wall time never enters the model.
     * Whether physical quiescent clocks stop is outside this profile.
     */
    if (s->ready && !s->loading) {
        dsu_sync(s);
        dsu_publish(s);
    }
}

enum {
    GET_PROFILE, GET_MAGIC, GET_CPU_MASK, GET_IRQ, GET_COUNTERS, GET_HZ,
    GET_PMCR, GET_ENABLE, GET_OVERFLOW, GET_INTEN, GET_SELECTOR,
    GET_CYCLE, GET_FRACTION, GET_LAST_NS, GET_WIRE_BYTES, GET_CEID0,
    GET_LOAD_STARTED, GET_LOAD_COMPLETED, GET_DEADLINE, GET_TRUE, GET_FALSE,
    GET_IRQ_LEVEL, GET_LOADING, GET_TIMER_PENDING,
};

static void dsu_get_uint(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(obj);
    const TB371FCDSUPMUWire *w = &s->bank;
    uint64_t value;

    /* Pure getters: no sync, timer reschedule, counter write or IRQ change. */
    switch ((uintptr_t)opaque) {
    case GET_PROFILE: value = TB371FC_DSU_PROFILE; break;
    case GET_MAGIC: value = TB371FC_DSU_WIRE_MAGIC; break;
    case GET_CPU_MASK: value = TB371FC_DSU_CPU_MASK; break;
    case GET_IRQ: value = TB371FC_DSU_IRQ_INTID; break;
    case GET_COUNTERS: value = TB371FC_DSU_EVENT_COUNTERS; break;
    case GET_HZ: value = s->frequency_hz; break;
    case GET_PMCR: value = TB371FC_DSU_PMCR_FIXED | w->pmcr; break;
    case GET_ENABLE: value = w->count_enable; break;
    case GET_OVERFLOW: value = w->overflow; break;
    case GET_INTEN: value = w->interrupt_enable; break;
    case GET_SELECTOR: value = w->selector; break;
    case GET_CYCLE: value = w->cycle_count; break;
    case GET_FRACTION: value = w->fraction; break;
    case GET_LAST_NS: value = s->last_ns; break;
    case GET_WIRE_BYTES: value = TB371FC_DSU_WIRE_BYTES; break;
    case GET_CEID0: value = TB371FC_DSU_CEID0; break;
    case GET_LOAD_STARTED: value = s->load_started; break;
    case GET_LOAD_COMPLETED: value = s->load_completed; break;
    case GET_DEADLINE: value = s->deadline_ns; break;
    default: g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dsu_get_bool(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(obj);
    bool value;

    switch ((uintptr_t)opaque) {
    case GET_TRUE: value = true; break;
    case GET_FALSE: value = false; break;
    case GET_IRQ_LEVEL: value = s->irq_level; break;
    case GET_LOADING: value = s->loading; break;
    case GET_TIMER_PENDING: value = timer_pending(s->timer); break;
    default: g_assert_not_reached();
    }
    visit_type_bool(v, name, &value, errp);
}

static char *dsu_event_counts_hex(Object *obj, Error **errp)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(obj);
    GString *text = g_string_sized_new(TB371FC_DSU_EVENT_COUNTERS * 8);
    unsigned i;

    for (i = 0; i < TB371FC_DSU_EVENT_COUNTERS; i++) {
        g_string_append_printf(text, "%08x", s->bank.event_count[i]);
    }
    return g_string_free(text, false);
}

static char *dsu_event_types_hex(Object *obj, Error **errp)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(obj);
    GString *text = g_string_sized_new(TB371FC_DSU_EVENT_COUNTERS * 8);
    unsigned i;

    for (i = 0; i < TB371FC_DSU_EVENT_COUNTERS; i++) {
        g_string_append_printf(text, "%08x", s->bank.event_type[i]);
    }
    return g_string_free(text, false);
}

static void dsu_add_properties(Object *obj)
{
    static const struct { const char *name; unsigned id; } ints[] = {
        { "enabled-profile", GET_PROFILE }, { "reference-identity", GET_MAGIC },
        { "cpu-mask", GET_CPU_MASK }, { "interrupt-intid", GET_IRQ },
        { "event-counter-count", GET_COUNTERS },
        { "virtual-reference-frequency-hz", GET_HZ },
        { "pmcr", GET_PMCR }, { "counter-enable", GET_ENABLE },
        { "overflow-status", GET_OVERFLOW }, { "interrupt-enable", GET_INTEN },
        { "selected-counter", GET_SELECTOR }, { "cycle-counter", GET_CYCLE },
        { "fractional-phase", GET_FRACTION }, { "last-sync-ns", GET_LAST_NS },
        { "shared-wire-bytes", GET_WIRE_BYTES }, { "ceid0", GET_CEID0 },
        { "incoming-started-cpus", GET_LOAD_STARTED },
        { "incoming-completed-cpus", GET_LOAD_COMPLETED },
        { "timer-deadline-ns", GET_DEADLINE },
    }, bools[] = {
        { "irq-level", GET_IRQ_LEVEL }, { "migration-quarantined", GET_LOADING },
        { "timer-pending", GET_TIMER_PENDING },
        { "virtual-cycle-model", GET_TRUE },
        { "physical-state-captured", GET_FALSE },
        { "cache-events-modeled", GET_FALSE }, { "bus-events-modeled", GET_FALSE },
    };
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(ints); i++) {
        object_property_add(obj, ints[i].name, "uint64", dsu_get_uint,
                            NULL, NULL, (void *)(uintptr_t)ints[i].id);
    }
    for (i = 0; i < ARRAY_SIZE(bools); i++) {
        object_property_add(obj, bools[i].name, "bool", dsu_get_bool,
                            NULL, NULL, (void *)(uintptr_t)bools[i].id);
    }
    object_property_add_str(obj, "event-counters-hex", dsu_event_counts_hex, NULL);
    object_property_add_str(obj, "event-types-hex", dsu_event_types_hex, NULL);
}

static void dsu_realize(DeviceState *dev, Error **errp)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(dev);
    Object *machine = qdev_get_machine();
    Error *local_err = NULL;

    if ((!tcg_enabled() && !qtest_enabled()) ||
        strcmp(object_get_typename(machine), MACHINE_TYPE_NAME("tb371fc")) ||
        s->frequency_hz == 0 || s->frequency_hz > TB371FC_DSU_MAX_HZ) {
        error_setg(errp, TYPE_TB371FC_DSU_PMU
                   ": requires TB371FC software emulation and 1..1000000000 Hz");
        return;
    }
    if (!object_property_get_bool(machine, "dsu-pmu", &local_err)) {
        if (local_err) {
            error_propagate(errp, local_err);
        } else {
            error_setg(errp, TYPE_TB371FC_DSU_PMU
                       ": requires the explicit machine dsu-pmu option");
        }
        return;
    }
    s->link = (TB371FCDSUPMULink) {
        .opaque = s,
        .profile = TB371FC_DSU_PROFILE,
        .cpu_mask = TB371FC_DSU_CPU_MASK,
        .irq_intid = TB371FC_DSU_IRQ_INTID,
        .frequency_hz = s->frequency_hz,
        .read = dsu_read, .write = dsu_write, .snapshot = dsu_snapshot,
        .begin_load = dsu_begin_load, .stage_load = dsu_stage_load,
        .complete_load = dsu_complete_load,
    };
    dsu_reset(dev);
    s->ready = true;
    s->vmchange = qemu_add_vm_change_state_handler(dsu_vm_change, s);
}

TB371FCDSUPMULink *tb371fc_dsu_pmu_get_link(DeviceState *dev)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(dev);

    g_assert(s->ready);
    return &s->link;
}

static void dsu_init(Object *obj)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(obj);

    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dsu_timer, s);
    dsu_add_properties(obj);
}

static void dsu_finalize(Object *obj)
{
    TB371FCDSUPMUState *s = TB371FC_DSU_PMU(obj);

    if (s->vmchange) {
        qemu_del_vm_change_state_handler(s->vmchange);
    }
    timer_free(s->timer);
}

static const Property dsu_properties[] = {
    DEFINE_PROP_UINT64("reference-frequency-hz", TB371FCDSUPMUState,
                       frequency_hz, TB371FC_DSU_DEFAULT_HZ),
};

static void dsu_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "TB371FC shared DSU PMU with virtual CPU_CYCLES reference clock";
    dc->realize = dsu_realize;
    dc->user_creatable = false;
    device_class_set_props(dc, dsu_properties);
    device_class_set_legacy_reset(dc, dsu_reset);
    /* The one bank is serialized inside CPU0's required profile subsection. */
}

static const TypeInfo dsu_type = {
    .name = TYPE_TB371FC_DSU_PMU,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(TB371FCDSUPMUState),
    .instance_init = dsu_init,
    .instance_finalize = dsu_finalize,
    .class_init = dsu_class_init,
};

static void dsu_register_types(void)
{
    type_register_static(&dsu_type);
}

type_init(dsu_register_types)
