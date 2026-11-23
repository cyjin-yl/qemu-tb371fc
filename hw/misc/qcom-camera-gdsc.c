/*
 * Qualcomm camera GDSC: bounded explicit digital power reference.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * OnePlusOSS/android_kernel_oneplus_sm8250 at
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae, gdsc-regulator.c;
 * six actual TB371FC DT resources and their common mmcx.lvl parent.
 * See tb371fc/docs/camera-gdsc-reference.md for evidence and limitations.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-camera-gdsc.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/bitops.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define GDSCR_COLLAPSE       BIT(0)
#define GDSCR_HW_CONTROL     BIT(1)
#define GDSCR_RETAIN_FF      BIT(11)
#define GDSCR_CLK_DIS_WAIT   (0xfU << 12)
#define GDSCR_POWER_ON       BIT(31)
#define GDSCR_WRITE_MASK     (GDSCR_COLLAPSE | GDSCR_HW_CONTROL | \
                             GDSCR_RETAIN_FF | GDSCR_CLK_DIS_WAIT)
#define DT_RETAIN_REGS       BIT(0)
#define DT_HW_MODE_DECLARED  BIT(1)
#define REFERENCE_IDENTITY  UINT32_C(0x43474431) /* "CGD1" */
#define MMCX_RESOURCE_INDEX 3

const QcomCameraGdscDomainDesc
qcom_camera_gdsc_domains[QCOM_CAMERA_GDSC_DOMAINS] = {
    { "bps",       0x0ad07004, DT_RETAIN_REGS | DT_HW_MODE_DECLARED, 100 },
    { "ipe",       0x0ad08004, DT_RETAIN_REGS | DT_HW_MODE_DECLARED, 100 },
    { "sbi",       0x0ad09004, DT_RETAIN_REGS, 100 },
    { "ife0",      0x0ad0a004, DT_RETAIN_REGS, 100 },
    { "ife1",      0x0ad0b004, DT_RETAIN_REGS, 100 },
    { "titan-top", 0x0ad0c144, DT_RETAIN_REGS, 500 },
};

typedef struct CameraGdscDomain {
    /* These four fields bind a migrated state to the exact domain tuple. */
    uint32_t domain_id;
    uint64_t base;
    uint32_t dt_flags;
    uint32_t timeout_us;
    uint32_t control;
    bool hw_request_known;
    bool hw_request;
} CameraGdscDomain;

typedef struct CameraGdscWindow {
    QcomCameraGdscState *state;
    unsigned domain;
} CameraGdscWindow;

struct QcomCameraGdscState {
    SysBusDevice parent_obj;
    MemoryRegion mmio[QCOM_CAMERA_GDSC_DOMAINS];
    CameraGdscWindow window[QCOM_CAMERA_GDSC_DOMAINS];
    qemu_irq power[QCOM_CAMERA_GDSC_DOMAINS];
    qemu_irq state_known[QCOM_CAMERA_GDSC_DOMAINS];
    bool enabled_reference;
    bool ready;

    /* 36 immutable wire bytes precede all runtime and per-domain state. */
    uint32_t enabled_profile;
    uint32_t reference_identity;
    uint32_t domain_count;
    uint32_t register_bytes;
    uint32_t mapped_bytes;
    uint32_t parent_resource_index;
    uint32_t cold_control;
    uint32_t control_write_mask;
    uint32_t power_status_mask;

    bool parent_known;
    bool parent_on;
    CameraGdscDomain domain[QCOM_CAMERA_GDSC_DOMAINS];
};

static bool request_known(const CameraGdscDomain *d)
{
    return !(d->control & GDSCR_HW_CONTROL) || d->hw_request_known;
}

static bool requested(const CameraGdscDomain *d)
{
    if (d->control & GDSCR_HW_CONTROL) {
        return d->hw_request_known && d->hw_request;
    }
    return !(d->control & GDSCR_COLLAPSE);
}

static bool power_known(const QcomCameraGdscState *s,
                        const CameraGdscDomain *d)
{
    /* A known zero resolves the AND without guessing its other input. */
    return s->enabled_reference &&
           ((s->parent_known && !s->parent_on) ||
            (request_known(d) && !requested(d)) ||
            (s->parent_known && request_known(d)));
}

static bool powered(const QcomCameraGdscState *s, const CameraGdscDomain *d)
{
    return s->enabled_reference && s->parent_known && s->parent_on &&
           request_known(d) && requested(d);
}

static void camera_publish(QcomCameraGdscState *s)
{
    unsigned i;

    if (!s->ready) {
        return;
    }
    for (i = 0; i < QCOM_CAMERA_GDSC_DOMAINS; i++) {
        CameraGdscDomain *d = &s->domain[i];

        if (!power_known(s, d)) {
            qemu_set_irq(s->state_known[i], 0);
            qemu_set_irq(s->power[i], 0);
        } else {
            qemu_set_irq(s->power[i], powered(s, d));
            qemu_set_irq(s->state_known[i], 1);
        }
    }
}

static void camera_parent_known(void *opaque, int input, int level)
{
    QcomCameraGdscState *s = opaque;

    s->parent_known = !!level;
    camera_publish(s);
}

static void camera_parent_on(void *opaque, int input, int level)
{
    QcomCameraGdscState *s = opaque;

    s->parent_on = !!level;
    camera_publish(s);
}

static void camera_hw_known(void *opaque, int input, int level)
{
    QcomCameraGdscState *s = opaque;

    assert(input >= 0 && input < QCOM_CAMERA_GDSC_DOMAINS);
    s->domain[input].hw_request_known = !!level;
    camera_publish(s);
}

static void camera_hw_request(void *opaque, int input, int level)
{
    QcomCameraGdscState *s = opaque;

    assert(input >= 0 && input < QCOM_CAMERA_GDSC_DOMAINS);
    s->domain[input].hw_request = !!level;
    camera_publish(s);
}

static MemTxResult camera_reject(CameraGdscWindow *w, hwaddr offset,
                                 unsigned size, bool write, uint64_t value,
                                 const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_CAMERA_GDSC
                  ": rejected %s domain=%u offset=0x%" HWADDR_PRIx
                  " size=%u value=0x%" PRIx64 " reason=%s\n",
                  write ? "write" : "read", w->domain, offset,
                  size, value, reason);
    return MEMTX_ERROR;
}

static MemTxResult camera_read(void *opaque, hwaddr offset, uint64_t *value,
                               unsigned size, MemTxAttrs attrs)
{
    CameraGdscWindow *w = opaque;
    QcomCameraGdscState *s = w->state;
    CameraGdscDomain *d = &s->domain[w->domain];

    *value = 0;
    if (!s->enabled_reference) {
        return camera_reject(w, offset, size, false, 0, "reference-disabled");
    }
    if (offset || size != QCOM_CAMERA_GDSC_REGISTER_BYTES) {
        return camera_reject(w, offset, size, false, 0, "access-shape");
    }
    if (!power_known(s, d)) {
        return camera_reject(w, offset, size, false, 0,
                             "power-feedback-unknown");
    }
    *value = d->control | (powered(s, d) ? GDSCR_POWER_ON : 0);
    return MEMTX_OK;
}

static MemTxResult camera_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size, MemTxAttrs attrs)
{
    CameraGdscWindow *w = opaque;
    QcomCameraGdscState *s = w->state;
    CameraGdscDomain *d = &s->domain[w->domain];

    if (!s->enabled_reference) {
        return camera_reject(w, offset, size, true, value, "reference-disabled");
    }
    if (offset || size != QCOM_CAMERA_GDSC_REGISTER_BYTES) {
        return camera_reject(w, offset, size, true, value, "access-shape");
    }
    if (value & ~((uint64_t)GDSCR_WRITE_MASK | GDSCR_POWER_ON)) {
        return camera_reject(w, offset, size, true, value, "unsupported-bits");
    }
    if ((value & GDSCR_HW_CONTROL) && !(d->dt_flags & DT_HW_MODE_DECLARED)) {
        return camera_reject(w, offset, size, true, value,
                             "hw-mode-not-in-reference");
    }
    /* The read-only power bit may be carried through an ordinary driver RMW. */
    d->control = value & GDSCR_WRITE_MASK;
    camera_publish(s);
    return MEMTX_OK;
}

static const MemoryRegionOps camera_ops = {
    .read_with_attrs = camera_read,
    .write_with_attrs = camera_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4,
              .unaligned = false },
};

static void camera_reset_enter(Object *obj, ResetType type)
{
    QcomCameraGdscState *s = QCOM_CAMERA_GDSC(obj);
    unsigned i;

    for (i = 0; i < QCOM_CAMERA_GDSC_DOMAINS; i++) {
        s->domain[i].control = GDSCR_COLLAPSE;
    }
    /* Inputs belong to providers; reset does not fabricate their deassertion. */
}

static void camera_reset_hold(Object *obj, ResetType type)
{
    camera_publish(QCOM_CAMERA_GDSC(obj));
}

static int camera_pre_load(void *opaque)
{
    QcomCameraGdscState *s = opaque;
    unsigned i;

    s->ready = false;
    /* An unsuccessful incoming stream must not leave a stale success output. */
    for (i = 0; i < QCOM_CAMERA_GDSC_DOMAINS; i++) {
        qemu_set_irq(s->state_known[i], 0);
        qemu_set_irq(s->power[i], 0);
    }
    return 0;
}

static int camera_post_load(void *opaque, int version_id)
{
    QcomCameraGdscState *s = opaque;
    unsigned i;

    for (i = 0; i < QCOM_CAMERA_GDSC_DOMAINS; i++) {
        CameraGdscDomain *d = &s->domain[i];

        if ((d->control & ~GDSCR_WRITE_MASK) ||
            ((d->control & GDSCR_HW_CONTROL) &&
             !(d->dt_flags & DT_HW_MODE_DECLARED)) ||
            (!s->enabled_reference && d->control != GDSCR_COLLAPSE)) {
            error_report(TYPE_QCOM_CAMERA_GDSC
                         ": invalid migrated camera domain %u", i);
            return -EINVAL;
        }
    }
    /* All domains are validated before any successful feedback is published. */
    s->ready = true;
    camera_publish(s);
    return 0;
}

static const VMStateDescription vmstate_camera_domain = {
    .name = TYPE_QCOM_CAMERA_GDSC "/domain",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(domain_id, CameraGdscDomain),
        VMSTATE_UINT64_EQUAL(base, CameraGdscDomain),
        VMSTATE_UINT32_EQUAL(dt_flags, CameraGdscDomain),
        VMSTATE_UINT32_EQUAL(timeout_us, CameraGdscDomain),
        VMSTATE_UINT32(control, CameraGdscDomain),
        VMSTATE_BOOL(hw_request_known, CameraGdscDomain),
        VMSTATE_BOOL(hw_request, CameraGdscDomain),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_camera = {
    .name = TYPE_QCOM_CAMERA_GDSC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = camera_pre_load,
    .post_load = camera_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(enabled_profile, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(domain_count, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(register_bytes, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(parent_resource_index, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(cold_control, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(control_write_mask, QcomCameraGdscState),
        VMSTATE_UINT32_EQUAL(power_status_mask, QcomCameraGdscState),
        VMSTATE_BOOL(parent_known, QcomCameraGdscState),
        VMSTATE_BOOL(parent_on, QcomCameraGdscState),
        VMSTATE_STRUCT_ARRAY(domain, QcomCameraGdscState,
                             QCOM_CAMERA_GDSC_DOMAINS, 1,
                             vmstate_camera_domain, CameraGdscDomain),
        VMSTATE_END_OF_LIST()
    },
};

enum {
    Q_PROFILE, Q_IDENTITY, Q_COUNT, Q_REGISTER_BYTES, Q_MAPPED_BYTES,
    Q_PARENT_RESOURCE, Q_COLD_CONTROL, Q_WRITE_MASK, Q_POWER_MASK,
    Q_PARENT_KNOWN, Q_PARENT_ON,
};

enum {
    D_NAME, D_ADDRESS, D_FLAGS, D_TIMEOUT, D_CONTROL, D_REQUEST_KNOWN,
    D_REQUESTED, D_STATE_KNOWN, D_POWERED, D_HW_KNOWN, D_HW_REQUEST,
    D_FIELDS,
};

static void camera_global_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomCameraGdscState *s = QCOM_CAMERA_GDSC(obj);
    unsigned field = GPOINTER_TO_UINT(opaque);
    uint64_t value;
    bool flag;

    switch (field) {
    case Q_PROFILE: value = s->enabled_profile; break;
    case Q_IDENTITY: value = s->reference_identity; break;
    case Q_COUNT: value = s->domain_count; break;
    case Q_REGISTER_BYTES: value = s->register_bytes; break;
    case Q_MAPPED_BYTES: value = s->mapped_bytes; break;
    case Q_PARENT_RESOURCE: value = s->parent_resource_index; break;
    case Q_COLD_CONTROL: value = s->cold_control; break;
    case Q_WRITE_MASK: value = s->control_write_mask; break;
    case Q_POWER_MASK: value = s->power_status_mask; break;
    case Q_PARENT_KNOWN:
    case Q_PARENT_ON:
        flag = field == Q_PARENT_KNOWN ? s->parent_known : s->parent_on;
        visit_type_bool(v, name, &flag, errp);
        return;
    default:
        g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
}

static void camera_domain_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    QcomCameraGdscState *s = QCOM_CAMERA_GDSC(obj);
    unsigned key = GPOINTER_TO_UINT(opaque);
    unsigned index = key / D_FIELDS, field = key % D_FIELDS;
    CameraGdscDomain *d = &s->domain[index];
    uint64_t value;
    bool flag;

    switch (field) {
    case D_NAME: {
        char *text = (char *)qcom_camera_gdsc_domains[index].name;

        visit_type_str(v, name, &text, errp);
        return;
    }
    case D_ADDRESS: value = d->base; break;
    case D_FLAGS: value = d->dt_flags; break;
    case D_TIMEOUT: value = d->timeout_us; break;
    case D_CONTROL: value = d->control; break;
    case D_REQUEST_KNOWN: flag = request_known(d); goto boolean;
    case D_REQUESTED: flag = requested(d); goto boolean;
    case D_STATE_KNOWN: flag = power_known(s, d); goto boolean;
    case D_POWERED: flag = powered(s, d); goto boolean;
    case D_HW_KNOWN: flag = d->hw_request_known; goto boolean;
    case D_HW_REQUEST: flag = d->hw_request; goto boolean;
    default:
        g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
    return;
boolean:
    visit_type_bool(v, name, &flag, errp);
}

static void camera_realize(DeviceState *dev, Error **errp)
{
    QcomCameraGdscState *s = QCOM_CAMERA_GDSC(dev);

    s->enabled_profile = s->enabled_reference;
    s->ready = true;
    camera_reset_enter(OBJECT(s), RESET_TYPE_COLD);
    camera_publish(s);
}

static void camera_init(Object *obj)
{
    QcomCameraGdscState *s = QCOM_CAMERA_GDSC(obj);
    DeviceState *dev = DEVICE(obj);
    static const struct {
        const char *name, *type;
    } globals[] = {
        { "enabled-profile", "uint64" },
        { "reference-identity", "uint64" },
        { "domain-count", "uint64" },
        { "register-bytes", "uint64" },
        { "mapped-bytes", "uint64" },
        { "parent-resource-index", "uint64" },
        { "cold-control", "uint64" },
        { "control-write-mask", "uint64" },
        { "power-status-mask", "uint64" },
        { "parent-known", "bool" },
        { "parent-on", "bool" },
    };
    static const struct {
        const char *name, *type;
    } fields[D_FIELDS] = {
        { "name", "str" }, { "address", "uint64" },
        { "dt-flags", "uint64" }, { "timeout-us", "uint64" },
        { "control", "uint64" }, { "request-known", "bool" },
        { "requested", "bool" }, { "state-known", "bool" },
        { "powered", "bool" }, { "hw-request-known", "bool" },
        { "hw-request", "bool" },
    };
    unsigned i, field;

    s->reference_identity = REFERENCE_IDENTITY;
    s->domain_count = QCOM_CAMERA_GDSC_DOMAINS;
    s->register_bytes = QCOM_CAMERA_GDSC_REGISTER_BYTES;
    s->mapped_bytes = QCOM_CAMERA_GDSC_DOMAINS * QCOM_CAMERA_GDSC_REGISTER_BYTES;
    s->parent_resource_index = MMCX_RESOURCE_INDEX;
    s->cold_control = GDSCR_COLLAPSE;
    s->control_write_mask = GDSCR_WRITE_MASK;
    s->power_status_mask = GDSCR_POWER_ON;
    for (i = 0; i < QCOM_CAMERA_GDSC_DOMAINS; i++) {
        const QcomCameraGdscDomainDesc *desc = &qcom_camera_gdsc_domains[i];
        CameraGdscDomain *d = &s->domain[i];
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_CAMERA_GDSC
                                                ".%s-control", desc->name);

        d->domain_id = i;
        d->base = desc->address;
        d->dt_flags = desc->dt_flags;
        d->timeout_us = desc->timeout_us;
        d->control = GDSCR_COLLAPSE;
        s->window[i].state = s;
        s->window[i].domain = i;
        memory_region_init_io(&s->mmio[i], obj, &camera_ops, &s->window[i],
                              name, QCOM_CAMERA_GDSC_REGISTER_BYTES);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio[i]);
        for (field = 0; field < D_FIELDS; field++) {
            g_autofree char *prop = g_strdup_printf("domain%u.%s", i,
                                                    fields[field].name);

            object_property_add(obj, prop, fields[field].type, camera_domain_get,
                                NULL, NULL, GUINT_TO_POINTER(i * D_FIELDS + field));
        }
    }
    for (i = 0; i < ARRAY_SIZE(globals); i++) {
        object_property_add(obj, globals[i].name, globals[i].type,
                            camera_global_get, NULL, NULL, GUINT_TO_POINTER(i));
    }
    qdev_init_gpio_in_named(dev, camera_parent_known, "parent-known", 1);
    qdev_init_gpio_in_named(dev, camera_parent_on, "parent-on", 1);
    qdev_init_gpio_in_named(dev, camera_hw_known, "hw-request-known",
                            QCOM_CAMERA_GDSC_DOMAINS);
    qdev_init_gpio_in_named(dev, camera_hw_request, "hw-request",
                            QCOM_CAMERA_GDSC_DOMAINS);
    qdev_init_gpio_out_named(dev, s->power, "power", QCOM_CAMERA_GDSC_DOMAINS);
    qdev_init_gpio_out_named(dev, s->state_known, "state-known",
                             QCOM_CAMERA_GDSC_DOMAINS);
}

static const Property camera_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomCameraGdscState,
                     enabled_reference, false),
};

static void camera_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm camera GDSC explicit digital reference";
    dc->realize = camera_realize;
    dc->vmsd = &vmstate_camera;
    device_class_set_props(dc, camera_properties);
    rc->phases.enter = camera_reset_enter;
    rc->phases.hold = camera_reset_hold;
}

static const TypeInfo camera_info = {
    .name = TYPE_QCOM_CAMERA_GDSC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomCameraGdscState),
    .instance_init = camera_init,
    .class_init = camera_class_init,
};

static void camera_register_types(void)
{
    type_register_static(&camera_info);
}
type_init(camera_register_types)
