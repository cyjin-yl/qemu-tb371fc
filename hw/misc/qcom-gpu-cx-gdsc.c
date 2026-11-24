/*
 * Qualcomm GPU CX GDSC normal software/votable digital controller.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Register evidence: Qualcomm gdsc-regulator.c, Linux gpucc-sm8250.c and
 * captured TB371FC DT. This does not emulate GPU execution or analog rails.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-gpu-cx-gdsc.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define GDSCR_PWR_ON       (1U << 31)
#define GDSCR_COLLAPSE     (1U << 0)
#define GDSCR_RETAIN_FF    (1U << 11)
#define GDSCR_CLK_DIS_WAIT 0x0000f000U
#define GDSCR_WRITABLE     (GDSCR_COLLAPSE | GDSCR_RETAIN_FF | GDSCR_CLK_DIS_WAIT)

typedef enum GdscTruth {
    GDSC_UNKNOWN = -1,
    GDSC_FALSE = 0,
    GDSC_TRUE = 1,
} GdscTruth;

typedef struct QcomGpuCxGdscWindow {
    QcomGpuCxGdscState *owner;
    MemoryRegion iomem;
    bool status_only;
} QcomGpuCxGdscWindow;

struct QcomGpuCxGdscState {
    SysBusDevice parent_obj;
    QcomGpuCxGdscWindow window[2];
    qemu_irq power_output;
    qemu_irq known_output;
    QEMUTimer *timer;
    uint32_t control;
    uint64_t transition_ns;
    uint64_t saved_transition_ns;
    int64_t deadline;
    bool control_known;
    bool power_known;
    bool power_on;
    bool pending;
    bool pending_target;
    bool parent_known;
    bool parent_on;
    bool external_known;
    bool external_vote;
    bool external_driven;
    bool boot_collapsed_reference;
    bool saved_boot_reference;
    bool ready;
    bool loading;
};

static GdscTruth gdsc_or(GdscTruth a, GdscTruth b)
{
    if (a == GDSC_TRUE || b == GDSC_TRUE) {
        return GDSC_TRUE;
    }
    if (a == GDSC_FALSE && b == GDSC_FALSE) {
        return GDSC_FALSE;
    }
    return GDSC_UNKNOWN;
}

static GdscTruth gdsc_and(GdscTruth a, GdscTruth b)
{
    if (a == GDSC_FALSE || b == GDSC_FALSE) {
        return GDSC_FALSE;
    }
    if (a == GDSC_TRUE && b == GDSC_TRUE) {
        return GDSC_TRUE;
    }
    return GDSC_UNKNOWN;
}

static GdscTruth gdsc_desired(QcomGpuCxGdscState *s)
{
    GdscTruth parent = s->parent_known ? s->parent_on : GDSC_UNKNOWN;
    GdscTruth external = s->external_known ? s->external_vote : GDSC_UNKNOWN;
    GdscTruth software = s->control_known ?
                         !(s->control & GDSCR_COLLAPSE) : GDSC_UNKNOWN;

    return gdsc_and(parent, gdsc_or(software, external));
}

static void gdsc_outputs(QcomGpuCxGdscState *s)
{
    if (!s->ready || s->loading) {
        return;
    }
    if (!s->power_known) {
        qemu_set_irq(s->known_output, 0);
        qemu_set_irq(s->power_output, 0);
    } else {
        qemu_set_irq(s->power_output, s->power_on);
        qemu_set_irq(s->known_output, 1);
    }
}

static void gdsc_cancel(QcomGpuCxGdscState *s)
{
    timer_del(s->timer);
    s->pending = false;
    s->pending_target = false;
    s->deadline = -1;
}

static void gdsc_recompute(QcomGpuCxGdscState *s)
{
    GdscTruth desired;
    int64_t now, delay;

    if (!s->ready || s->loading) {
        return;
    }
    desired = gdsc_desired(s);
    if (desired == GDSC_UNKNOWN) {
        gdsc_cancel(s);
        s->power_known = false;
        s->power_on = false;
        gdsc_outputs(s);
        return;
    }
    if (s->power_known && s->power_on == desired) {
        gdsc_cancel(s);
        gdsc_outputs(s);
        return;
    }
    if (s->pending && s->pending_target == desired) {
        /* Repeated votes or config updates must not postpone completion. */
        return;
    }
    gdsc_cancel(s);
    now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->pending = true;
    s->pending_target = desired;
    delay = s->transition_ns;
    s->deadline = now > INT64_MAX - delay ? INT64_MAX : now + delay;
    timer_mod_ns(s->timer, s->deadline);
    gdsc_outputs(s);
}

static void gdsc_timer(void *opaque)
{
    QcomGpuCxGdscState *s = opaque;
    GdscTruth desired;

    if (!s->pending || s->loading) {
        return;
    }
    if (qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) < s->deadline) {
        timer_mod_ns(s->timer, s->deadline);
        return;
    }
    desired = gdsc_desired(s);
    if (desired == GDSC_UNKNOWN || desired != s->pending_target) {
        /* Never complete an old request after availability or votes changed. */
        gdsc_recompute(s);
        return;
    }
    gdsc_cancel(s);
    s->power_on = desired;
    s->power_known = true;
    gdsc_outputs(s);
}

static void gdsc_parent_on(void *opaque, int line, int level)
{
    QcomGpuCxGdscState *s = opaque;

    s->parent_on = !!level;
    gdsc_recompute(s);
}

static void gdsc_parent_known(void *opaque, int line, int level)
{
    QcomGpuCxGdscState *s = opaque;

    s->parent_known = !!level;
    gdsc_recompute(s);
}

static void gdsc_external_vote(void *opaque, int line, int level)
{
    QcomGpuCxGdscState *s = opaque;

    s->external_driven = true;
    s->external_vote = !!level;
    gdsc_recompute(s);
}

static void gdsc_external_known(void *opaque, int line, int level)
{
    QcomGpuCxGdscState *s = opaque;

    s->external_driven = true;
    s->external_known = !!level;
    gdsc_recompute(s);
}

static MemTxResult gdsc_bad_access(QcomGpuCxGdscWindow *window, hwaddr addr,
                                   unsigned size, bool write, uint64_t value)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_GPU_CX_GDSC
                  ": %s unsupported %s offset=0x%" HWADDR_PRIx
                  " size=%u value=0x%" PRIx64 "\n",
                  window->status_only ? "GDS_HW_CTRL" : "GDSCR",
                  write ? "write" : "read", addr, size, value);
    return MEMTX_ERROR;
}

static MemTxResult gdsc_read(void *opaque, hwaddr addr, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomGpuCxGdscWindow *window = opaque;
    QcomGpuCxGdscState *s = window->owner;

    *value = 0;
    if (addr != 0 || size != 4) {
        return gdsc_bad_access(window, addr, size, false, 0);
    }
    /*
     * Parent availability is a digital transition input. It is not used
     * to invent an MMIO bus gate. A partial unknown register is still
     * rejected rather than returning a guessed status/configuration bit.
     */
    if (!s->power_known || (!window->status_only && !s->control_known)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_GPU_CX_GDSC
                      ": %s unknown modeled state; refusing read"
                      " (control-known=%d status-known=%d)\n",
                      window->status_only ? "GDS_HW_CTRL" : "GDSCR",
                      s->control_known, s->power_known);
        return MEMTX_ERROR;
    }
    *value = s->power_on ? GDSCR_PWR_ON : 0;
    if (!window->status_only) {
        *value |= s->control;
    }
    return MEMTX_OK;
}

static MemTxResult gdsc_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomGpuCxGdscWindow *window = opaque;
    QcomGpuCxGdscState *s = window->owner;

    /*
     * Allow the read-only PWR_ON bit to be carried through a driver RMW.
     * HW_CONTROL, SW_OVERRIDE and other unimplemented bits are rejected
     * before changing any request, config, timer or output.
     */
    if (addr != 0 || size != 4 || window->status_only ||
        (value & ~(uint64_t)(GDSCR_WRITABLE | GDSCR_PWR_ON))) {
        return gdsc_bad_access(window, addr, size, true, value);
    }
    s->control = value & GDSCR_WRITABLE;
    s->control_known = true;
    gdsc_recompute(s);
    return MEMTX_OK;
}

static const MemoryRegionOps gdsc_ops = {
    .read_with_attrs = gdsc_read,
    .write_with_attrs = gdsc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /*
     * Explicitly diagnose subword operations. Cross-region transactions
     * may already have been split by the address-space layer; no wider
     * atomicity or rejection guarantee is claimed for these 4-byte windows.
     */
    .valid = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
};

static bool gdsc_get_control_known(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->control_known;
}

static bool gdsc_get_power_known(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->power_known;
}

static bool gdsc_get_power_on(Object *obj, Error **errp)
{
    QcomGpuCxGdscState *s = QCOM_GPU_CX_GDSC(obj);

    return s->power_known && s->power_on;
}

static bool gdsc_get_pending(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->pending;
}

static bool gdsc_get_parent_known(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->parent_known;
}

static bool gdsc_get_parent_on(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->parent_on;
}

static bool gdsc_get_external_known(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->external_known;
}

static bool gdsc_get_external_vote(Object *obj, Error **errp)
{
    return QCOM_GPU_CX_GDSC(obj)->external_vote;
}

static void gdsc_get_control(Object *obj, Visitor *visitor, const char *name,
                              void *opaque, Error **errp)
{
    uint64_t value = QCOM_GPU_CX_GDSC(obj)->control;

    visit_type_uint64(visitor, name, &value, errp);
}

static void gdsc_get_deadline(Object *obj, Visitor *visitor, const char *name,
                               void *opaque, Error **errp)
{
    int64_t value = QCOM_GPU_CX_GDSC(obj)->deadline;

    visit_type_int64(visitor, name, &value, errp);
}

static char *gdsc_get_state(Object *obj, Error **errp)
{
    QcomGpuCxGdscState *s = QCOM_GPU_CX_GDSC(obj);

    if (s->pending) {
        return g_strdup(s->pending_target ? "pending-on" : "pending-off");
    }
    if (!s->power_known) {
        return g_strdup("unknown");
    }
    return g_strdup(s->power_on ? "on" : "off");
}

static void gdsc_reset(DeviceState *dev)
{
    QcomGpuCxGdscState *s = QCOM_GPU_CX_GDSC(dev);

    gdsc_cancel(s);
    s->control_known = s->boot_collapsed_reference;
    s->control = s->control_known ? GDSCR_COLLAPSE : 0;
    s->power_known = false;
    s->power_on = false;
    /*
     * Device reset changes neither the external votes nor parent inputs.
     * Reference initialization of undriven external inputs occurs only
     * at realize, so a later reset cannot erase a real held external vote.
     */
    gdsc_outputs(s);
    gdsc_recompute(s);
}

static int gdsc_pre_save(void *opaque)
{
    QcomGpuCxGdscState *s = opaque;

    s->saved_boot_reference = s->boot_collapsed_reference;
    s->saved_transition_ns = s->transition_ns;
    return 0;
}

static int gdsc_pre_load(void *opaque)
{
    QcomGpuCxGdscState *s = opaque;

    s->loading = true;
    timer_del(s->timer);
    return 0;
}

static int gdsc_post_load(void *opaque, int version_id)
{
    QcomGpuCxGdscState *s = opaque;
    GdscTruth desired = gdsc_desired(s);

    if (s->saved_boot_reference != s->boot_collapsed_reference ||
        s->saved_transition_ns != s->transition_ns) {
        error_report(TYPE_QCOM_GPU_CX_GDSC
                     ": reset profile or transition-ns differs"
                     " between source and destination");
        return -EINVAL;
    }
    if ((s->control & ~GDSCR_WRITABLE) ||
        (!s->control_known && s->control != 0) ||
        (s->boot_collapsed_reference && !s->control_known) ||
        (!s->power_known && s->power_on) ||
        (s->pending != (s->deadline >= 0)) ||
        (!s->pending && s->pending_target)) {
        goto invalid;
    }
    if (s->pending) {
        if (desired == GDSC_UNKNOWN || desired != s->pending_target ||
            (s->power_known && s->power_on == desired)) {
            goto invalid;
        }
    } else if (desired == GDSC_UNKNOWN) {
        if (s->power_known) {
            goto invalid;
        }
    } else if (!s->power_known || s->power_on != desired) {
        goto invalid;
    }
    s->loading = false;
    gdsc_outputs(s);
    if (s->pending) {
        /*
         * Restore the source deadline, not a new full delay. Upstream
         * GPIO restoration may re-evaluate inputs later; the timer also
         * rechecks the desired state before completing.
         */
        timer_mod_ns(s->timer, s->deadline);
    }
    return 0;

invalid:
    error_report(TYPE_QCOM_GPU_CX_GDSC ": invalid migrated controller state");
    return -EINVAL;
}

static const VMStateDescription vmstate_gdsc = {
    .name = TYPE_QCOM_GPU_CX_GDSC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = gdsc_pre_save,
    .pre_load = gdsc_pre_load,
    .post_load = gdsc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, QcomGpuCxGdscState),
        VMSTATE_BOOL(control_known, QcomGpuCxGdscState),
        VMSTATE_BOOL(power_known, QcomGpuCxGdscState),
        VMSTATE_BOOL(power_on, QcomGpuCxGdscState),
        VMSTATE_BOOL(pending, QcomGpuCxGdscState),
        VMSTATE_BOOL(pending_target, QcomGpuCxGdscState),
        VMSTATE_INT64(deadline, QcomGpuCxGdscState),
        VMSTATE_BOOL(parent_known, QcomGpuCxGdscState),
        VMSTATE_BOOL(parent_on, QcomGpuCxGdscState),
        VMSTATE_BOOL(external_known, QcomGpuCxGdscState),
        VMSTATE_BOOL(external_vote, QcomGpuCxGdscState),
        VMSTATE_BOOL(saved_boot_reference, QcomGpuCxGdscState),
        VMSTATE_UINT64(saved_transition_ns, QcomGpuCxGdscState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property gdsc_properties[] = {
    DEFINE_PROP_BOOL("boot-collapsed-reference", QcomGpuCxGdscState,
                     boot_collapsed_reference, false),
    DEFINE_PROP_UINT64("transition-ns", QcomGpuCxGdscState,
                       transition_ns, 1000),
};

static void gdsc_realize(DeviceState *dev, Error **errp)
{
    QcomGpuCxGdscState *s = QCOM_GPU_CX_GDSC(dev);

    if (!s->transition_ns || s->transition_ns > 1000000000ULL) {
        error_setg(errp, TYPE_QCOM_GPU_CX_GDSC
                   ": transition-ns must be in 1..1000000000");
        return;
    }
    if (s->boot_collapsed_reference && !s->external_driven) {
        /* Explicit reference environment, never sampled TZ/HYP vote state. */
        s->external_known = true;
        s->external_vote = false;
    }
    s->ready = true;
    gdsc_reset(dev);
}

static void gdsc_init(Object *obj)
{
    QcomGpuCxGdscState *s = QCOM_GPU_CX_GDSC(obj);
    DeviceState *dev = DEVICE(obj);
    unsigned i;

    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, gdsc_timer, s);
    s->deadline = -1;
    for (i = 0; i < 2; i++) {
        QcomGpuCxGdscWindow *window = &s->window[i];

        window->owner = s;
        window->status_only = i != 0;
        memory_region_init_io(&window->iomem, obj, &gdsc_ops, window,
                              i ? TYPE_QCOM_GPU_CX_GDSC ".status" :
                                  TYPE_QCOM_GPU_CX_GDSC ".control",
                              QCOM_GPU_CX_GDSC_WINDOW_SIZE);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &window->iomem);
    }
    qdev_init_gpio_in_named(dev, gdsc_parent_known, "parent-known", 1);
    qdev_init_gpio_in_named(dev, gdsc_parent_on, "parent-on", 1);
    qdev_init_gpio_in_named(dev, gdsc_external_known, "external-known", 1);
    qdev_init_gpio_in_named(dev, gdsc_external_vote, "external-vote", 1);
    qdev_init_gpio_out_named(dev, &s->known_output, "state-known", 1);
    qdev_init_gpio_out_named(dev, &s->power_output, "power-on", 1);
    object_property_add_bool(obj, "control-known", gdsc_get_control_known, NULL);
    object_property_add_bool(obj, "state-known", gdsc_get_power_known, NULL);
    object_property_add_bool(obj, "power-on", gdsc_get_power_on, NULL);
    object_property_add_bool(obj, "transition-pending", gdsc_get_pending, NULL);
    object_property_add_bool(obj, "parent-known", gdsc_get_parent_known, NULL);
    object_property_add_bool(obj, "parent-on", gdsc_get_parent_on, NULL);
    object_property_add_bool(obj, "external-known", gdsc_get_external_known, NULL);
    object_property_add_bool(obj, "external-vote", gdsc_get_external_vote, NULL);
    object_property_add(obj, "control", "uint64", gdsc_get_control,
                        NULL, NULL, NULL);
    object_property_add(obj, "transition-deadline-ns", "int64",
                        gdsc_get_deadline, NULL, NULL, NULL);
    object_property_add_str(obj, "state", gdsc_get_state, NULL);
}

static void gdsc_finalize(Object *obj)
{
    timer_free(QCOM_GPU_CX_GDSC(obj)->timer);
}

static void gdsc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm GPU CX GDSC normal software/votable digital controller";
    dc->realize = gdsc_realize;
    dc->vmsd = &vmstate_gdsc;
    device_class_set_props(dc, gdsc_properties);
    device_class_set_legacy_reset(dc, gdsc_reset);
}

static const TypeInfo gdsc_info = {
    .name = TYPE_QCOM_GPU_CX_GDSC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomGpuCxGdscState),
    .instance_init = gdsc_init,
    .instance_finalize = gdsc_finalize,
    .class_init = gdsc_class_init,
};

static void gdsc_register_types(void)
{
    type_register_static(&gdsc_info);
}
type_init(gdsc_register_types)
