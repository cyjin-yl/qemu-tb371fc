/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * SM8250 SPSS SP2SOC interrupt-mask write-control reference.
 *
 * The fixed vendor subsys-pil-tz.c mask_scsr_irqs() and original TB371FC DT
 * identify one 32-bit store at 0x01881028. The original kernel performs that
 * store before registering the subsystem or starting secure firmware.
 *
 * This device retains the complete guest-programmed control word. There is
 * no architectural reset value or MMIO readback evidence in this contract.
 * Cold/reset mask_value == 0 is an internal placeholder with written == 0;
 * a guest write of zero is a distinct, known programmed value. The source
 * supplies no silicon reserved-bit mask, so all 32 written bits are retained.
 *
 * No IRQ, READY, PAS, firmware, clock, timer or power behavior is implied.
 * Other SPSS/SCSR words and the adjacent QSEE banks retain their own owners.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-spss-irq-mask.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#include "qcom-sm8250-spss-irq-mask-data.inc"

#define SPSS_IRQ_MASK_REFERENCE_ID UINT32_C(0x53504d31)
#define SPSS_IRQ_MASK_VMSTATE_BYTES 25

struct QcomSm8250SpssIrqMaskState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    bool enabled_reference;

    /* VMState v1, exactly 25 bytes; written remains an explicitly checked u8. */
    uint32_t reference_profile;
    uint32_t reference_identity;
    uint64_t physical_base;
    uint32_t window_bytes;
    uint32_t mask_value;
    uint8_t written;
};

enum {
    SPSS_PROP_PROFILE,
    SPSS_PROP_IDENTITY,
    SPSS_PROP_BASE,
    SPSS_PROP_WINDOW_BYTES,
    SPSS_PROP_MASK_VALUE,
    SPSS_PROP_VMSTATE_BYTES,
    SPSS_PROP_REGISTER_COUNT,
    SPSS_PROP_IRQ_COUNT,
    SPSS_PROP_CLOCK_INPUT_COUNT,
    SPSS_PROP_CLOCK_OUTPUT_COUNT,
    SPSS_PROP_TIMER_COUNT,
};

static MemTxResult spss_reject(hwaddr addr, unsigned size, bool write,
                              const char *reason)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                  ": rejected %s offset=0x%" HWADDR_PRIx
                  " size %u (%s)\n", write ? "write" : "read", addr, size,
                  reason);
    return MEMTX_ERROR;
}

static MemTxResult spss_read(void *opaque, hwaddr addr, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    *value = 0;
    return spss_reject(addr, size, false, "readback-unimplemented");
}

static MemTxResult spss_write(void *opaque, hwaddr addr, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomSm8250SpssIrqMaskState *s = opaque;

    if (!s->enabled_reference || addr != 0 || size != 4 ||
        value > UINT32_MAX) {
        return spss_reject(addr, size, true, "disabled-or-invalid-access");
    }

    s->mask_value = value;
    s->written = 1;
    return MEMTX_OK;
}

static const MemoryRegionOps spss_ops = {
    .read_with_attrs = spss_read,
    .write_with_attrs = spss_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Reject invalid transactions delivered to this callback explicitly. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void spss_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250SpssIrqMaskState *s = QCOM_SM8250_SPSS_IRQ_MASK(obj);

    s->mask_value = 0;
    s->written = 0;
}

static int spss_post_load(void *opaque, int version_id)
{
    QcomSm8250SpssIrqMaskState *s = opaque;

    if (s->reference_profile != (uint32_t)s->enabled_reference) {
        error_report(TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                     ": migrated profile mismatch");
        return -EINVAL;
    }
    if (s->reference_identity != SPSS_IRQ_MASK_REFERENCE_ID) {
        error_report(TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                     ": migrated identity mismatch");
        return -EINVAL;
    }
    if (s->physical_base != SPSS_IRQ_MASK_BASE ||
        s->window_bytes != SPSS_IRQ_MASK_WINDOW_BYTES) {
        error_report(TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                     ": migrated geometry mismatch");
        return -EINVAL;
    }
    if (s->written > 1) {
        error_report(TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                     ": invalid migrated programmed flag");
        return -EINVAL;
    }
    if (!s->enabled_reference && (s->written || s->mask_value)) {
        error_report(TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                     ": invalid migrated disabled state");
        return -EINVAL;
    }
    if (!s->written && s->mask_value) {
        error_report(TYPE_QCOM_SM8250_SPSS_IRQ_MASK
                     ": invalid migrated unknown mask");
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_spss_irq_mask = {
    .name = TYPE_QCOM_SM8250_SPSS_IRQ_MASK,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = spss_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(reference_profile, QcomSm8250SpssIrqMaskState),
        VMSTATE_UINT32(reference_identity, QcomSm8250SpssIrqMaskState),
        VMSTATE_UINT64(physical_base, QcomSm8250SpssIrqMaskState),
        VMSTATE_UINT32(window_bytes, QcomSm8250SpssIrqMaskState),
        VMSTATE_UINT32(mask_value, QcomSm8250SpssIrqMaskState),
        VMSTATE_UINT8(written, QcomSm8250SpssIrqMaskState),
        VMSTATE_END_OF_LIST()
    },
};

static void spss_uint_get(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    QcomSm8250SpssIrqMaskState *s = QCOM_SM8250_SPSS_IRQ_MASK(obj);
    uint64_t value;

    switch (GPOINTER_TO_UINT(opaque)) {
    case SPSS_PROP_PROFILE:
        value = s->reference_profile;
        break;
    case SPSS_PROP_IDENTITY:
        value = s->reference_identity;
        break;
    case SPSS_PROP_BASE:
        value = s->physical_base;
        break;
    case SPSS_PROP_WINDOW_BYTES:
        value = s->window_bytes;
        break;
    case SPSS_PROP_MASK_VALUE:
        value = s->mask_value;
        break;
    case SPSS_PROP_VMSTATE_BYTES:
        value = SPSS_IRQ_MASK_VMSTATE_BYTES;
        break;
    case SPSS_PROP_REGISTER_COUNT:
        value = 1;
        break;
    case SPSS_PROP_IRQ_COUNT:
    case SPSS_PROP_CLOCK_INPUT_COUNT:
    case SPSS_PROP_CLOCK_OUTPUT_COUNT:
    case SPSS_PROP_TIMER_COUNT:
        value = 0;
        break;
    default:
        g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
}

static void spss_bool_get(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    QcomSm8250SpssIrqMaskState *s = QCOM_SM8250_SPSS_IRQ_MASK(obj);
    bool value = GPOINTER_TO_UINT(opaque) ? s->written != 0 : false;

    visit_type_bool(v, name, &value, errp);
}

static void spss_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250SpssIrqMaskState *s = QCOM_SM8250_SPSS_IRQ_MASK(dev);

    s->reference_profile = s->enabled_reference;
}

static void spss_init(Object *obj)
{
    QcomSm8250SpssIrqMaskState *s = QCOM_SM8250_SPSS_IRQ_MASK(obj);
    static const char *const uint_properties[] = {
        "enabled-profile", "reference-identity", "physical-base",
        "window-bytes", "mask-value", "vmstate-bytes", "register-count",
        "irq-output-count", "clock-input-count", "clock-output-count",
        "timer-count",
    };
    static const char *const unsupported[] = {
        "readback-supported", "ready-modeled", "firmware-modeled",
        "pas-modeled",
    };
    unsigned i;

    s->reference_identity = SPSS_IRQ_MASK_REFERENCE_ID;
    s->physical_base = SPSS_IRQ_MASK_BASE;
    s->window_bytes = SPSS_IRQ_MASK_WINDOW_BYTES;
    for (i = 0; i < ARRAY_SIZE(uint_properties); i++) {
        object_property_add(obj, uint_properties[i], "uint64", spss_uint_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
    }
    object_property_add(obj, "programmed", "bool", spss_bool_get,
                        NULL, NULL, GUINT_TO_POINTER(1));
    object_property_add(obj, "mask-known", "bool", spss_bool_get,
                        NULL, NULL, GUINT_TO_POINTER(1));
    for (i = 0; i < ARRAY_SIZE(unsupported); i++) {
        object_property_add(obj, unsupported[i], "bool", spss_bool_get,
                            NULL, NULL, GUINT_TO_POINTER(0));
    }
    memory_region_init_io(&s->iomem, obj, &spss_ops, s,
                          TYPE_QCOM_SM8250_SPSS_IRQ_MASK ".mask",
                          SPSS_IRQ_MASK_WINDOW_BYTES);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static const Property spss_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250SpssIrqMaskState,
                     enabled_reference, false),
};

static void spss_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 SPSS interrupt-mask write-control reference";
    dc->realize = spss_realize;
    dc->vmsd = &vmstate_spss_irq_mask;
    device_class_set_props(dc, spss_properties);
    rc->phases.enter = spss_reset_enter;
}

static const TypeInfo spss_irq_mask_info = {
    .name = TYPE_QCOM_SM8250_SPSS_IRQ_MASK,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250SpssIrqMaskState),
    .instance_init = spss_init,
    .class_init = spss_class_init,
};

static void spss_irq_mask_register_types(void)
{
    type_register_static(&spss_irq_mask_info);
}
type_init(spss_irq_mask_register_types)
