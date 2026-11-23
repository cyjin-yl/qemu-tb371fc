/*
 * Qualcomm Kona / SM8250 REFGEN software request register.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Register evidence: downstream refgen_kona_ops and upstream
 * drivers/regulator/qcom-refgen-regulator.c. No analog circuit is modeled.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-refgen.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

struct QcomRefgenState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq software_enable;
    qemu_irq state_known;
    uint32_t control;
    bool known;
    bool boot_disabled_reference;
    bool saved_boot_reference;
    bool ready;
};

static void refgen_update(QcomRefgenState *s)
{
    if (!s->ready) {
        return;
    }
    /*
     * Consumers must honor validity. Invalidate before changing the request
     * on an unknown transition; establish a request before asserting validity.
     */
    if (!s->known) {
        qemu_set_irq(s->state_known, 0);
        qemu_set_irq(s->software_enable, 0);
    } else {
        qemu_set_irq(s->software_enable, s->control);
        qemu_set_irq(s->state_known, 1);
    }
}

static MemTxResult refgen_bad_access(hwaddr addr, unsigned size,
                                     bool write, uint64_t value)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_REFGEN
                  ": unsupported %s CTRL5-window offset=0x%" HWADDR_PRIx
                  " size=%u value=0x%" PRIx64 "\n",
                  write ? "write" : "read", addr, size, value);
    return MEMTX_ERROR;
}

static MemTxResult refgen_read(void *opaque, hwaddr addr, uint64_t *value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomRefgenState *s = opaque;

    *value = 0;
    if (addr != 0 || size != 4) {
        return refgen_bad_access(addr, size, false, 0);
    }
    if (!s->known) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_REFGEN
                      ": CTRL5 initial state is unknown; refusing read"
                      " (boot-disabled-reference=off)\n");
        return MEMTX_ERROR;
    }
    *value = s->control;
    return MEMTX_OK;
}

static MemTxResult refgen_write(void *opaque, hwaddr addr, uint64_t value,
                                unsigned size, MemTxAttrs attrs)
{
    QcomRefgenState *s = opaque;

    if (addr != 0 || size != 4 || value > 1) {
        return refgen_bad_access(addr, size, true, value);
    }
    /*
     * A full supported write establishes the software request even if its
     * previous value was unknown. Unknown reserved fields are not accepted.
     */
    s->control = value;
    s->known = true;
    refgen_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps refgen_ops = {
    .read_with_attrs = refgen_read,
    .write_with_attrs = refgen_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /*
     * Deliver subword accesses to the handlers for explicit rejection.
     * This four-byte window cannot observe an original transaction size
     * that the system memory layer has already split across region edges.
     */
    .valid = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
};

static bool refgen_get_known(Object *obj, Error **errp)
{
    return QCOM_REFGEN(obj)->known;
}

static bool refgen_get_enable(Object *obj, Error **errp)
{
    QcomRefgenState *s = QCOM_REFGEN(obj);

    return s->known && s->control;
}

static char *refgen_get_state(Object *obj, Error **errp)
{
    QcomRefgenState *s = QCOM_REFGEN(obj);

    if (!s->known) {
        return g_strdup("unknown");
    }
    return g_strdup(s->control ? "enabled-request" : "disabled-request");
}

static void refgen_reset(DeviceState *dev)
{
    QcomRefgenState *s = QCOM_REFGEN(dev);

    s->control = 0;
    s->known = s->boot_disabled_reference;
    refgen_update(s);
}

static int refgen_pre_save(void *opaque)
{
    QcomRefgenState *s = opaque;

    /*
     * Keep the destination configuration separate from the serialized value
     * so a migration cannot silently alter the meaning of a later reset.
     */
    s->saved_boot_reference = s->boot_disabled_reference;
    return 0;
}

static int refgen_post_load(void *opaque, int version_id)
{
    QcomRefgenState *s = opaque;

    if (s->saved_boot_reference != s->boot_disabled_reference) {
        error_report(TYPE_QCOM_REFGEN
                     ": boot-disabled-reference differs between source"
                     " and destination");
        return -EINVAL;
    }
    if (s->control > 1 || (!s->known && s->control != 0) ||
        (s->boot_disabled_reference && !s->known)) {
        error_report(TYPE_QCOM_REFGEN ": invalid migrated software-request state");
        return -EINVAL;
    }
    refgen_update(s);
    return 0;
}

static const VMStateDescription vmstate_refgen = {
    .name = TYPE_QCOM_REFGEN,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = refgen_pre_save,
    .post_load = refgen_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, QcomRefgenState),
        VMSTATE_BOOL(known, QcomRefgenState),
        VMSTATE_BOOL(saved_boot_reference, QcomRefgenState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property refgen_properties[] = {
    DEFINE_PROP_BOOL("boot-disabled-reference", QcomRefgenState,
                     boot_disabled_reference, false),
};

static void refgen_realize(DeviceState *dev, Error **errp)
{
    QcomRefgenState *s = QCOM_REFGEN(dev);

    s->ready = true;
    refgen_reset(dev);
}

static void refgen_init(Object *obj)
{
    QcomRefgenState *s = QCOM_REFGEN(obj);

    memory_region_init_io(&s->iomem, obj, &refgen_ops, s,
                          TYPE_QCOM_REFGEN ".ctrl5", QCOM_REFGEN_CTRL5_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    qdev_init_gpio_out_named(DEVICE(s), &s->software_enable, "software-enable", 1);
    qdev_init_gpio_out_named(DEVICE(s), &s->state_known, "state-known", 1);
    object_property_add_bool(obj, "software-enable", refgen_get_enable, NULL);
    object_property_add_bool(obj, "state-known", refgen_get_known, NULL);
    object_property_add_str(obj, "state", refgen_get_state, NULL);
}

static void refgen_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm Kona REFGEN software request with explicit validity";
    dc->realize = refgen_realize;
    dc->vmsd = &vmstate_refgen;
    device_class_set_props(dc, refgen_properties);
    device_class_set_legacy_reset(dc, refgen_reset);
}

static const TypeInfo refgen_info = {
    .name = TYPE_QCOM_REFGEN,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomRefgenState),
    .instance_init = refgen_init,
    .class_init = refgen_class_init,
};

static void refgen_register_types(void)
{
    type_register_static(&refgen_info);
}
type_init(refgen_register_types)
