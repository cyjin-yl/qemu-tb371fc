/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * SM8250 AOP DDR statistics offset reference.
 *
 * The vendor consumer reads one four-byte offset resource and adds it to
 * its message-RAM base. The pinned upstream SM8250 DT and matching driver
 * locate the statistics at 0x0c3f0000 + 0xb8, independently deriving the
 * relative offset 0x000f00b8. The original TB371FC DT/probe agree with the
 * consumer contract; a bounded static original-AOP producer analysis also
 * computes this word. This is an explicit reference layout, not a measured
 * live word or an assertion about silicon reset state.
 *
 * Only the APSS offset-word read is implemented. Statistics records,
 * neighboring offset fields, RPM data, firmware execution, readiness,
 * clocks, IRQs and timers remain unsupported. Rejecting APSS writes is this
 * model's consumer boundary, not a claim that real shared SRAM is read-only.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-ddr-stats-offset.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#include "qcom-sm8250-ddr-stats-offset-data.inc"

#define DDR_STATS_OFFSET_REFERENCE_ID UINT32_C(0x44534f31)
#define DDR_STATS_OFFSET_VMSTATE_BYTES 24

struct QcomSm8250DdrStatsOffsetState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    bool enabled_reference;

    /* VMState v1: immutable configured reference, exactly 24 bytes. */
    uint32_t reference_profile;
    uint32_t reference_identity;
    uint64_t physical_base;
    uint32_t window_bytes;
    uint32_t offset_value;
};

enum {
    DSO_PROP_PROFILE,
    DSO_PROP_IDENTITY,
    DSO_PROP_BASE,
    DSO_PROP_WINDOW_BYTES,
    DSO_PROP_OFFSET_VALUE,
    DSO_PROP_VMSTATE_BYTES,
    DSO_PROP_REGISTER_COUNT,
    DSO_PROP_IRQ_COUNT,
    DSO_PROP_CLOCK_INPUT_COUNT,
    DSO_PROP_CLOCK_OUTPUT_COUNT,
    DSO_PROP_TIMER_COUNT,
};

static MemTxResult dso_reject(hwaddr addr, unsigned size, bool write,
                             const char *reason)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_DDR_STATS_OFFSET
                  ": rejected %s offset=0x%" HWADDR_PRIx
                  " size %u (%s)\n", write ? "write" : "read", addr, size,
                  reason);
    return MEMTX_ERROR;
}

static MemTxResult dso_read(void *opaque, hwaddr addr, uint64_t *value,
                           unsigned size, MemTxAttrs attrs)
{
    QcomSm8250DdrStatsOffsetState *s = opaque;

    *value = 0;
    if (!s->enabled_reference || addr != 0 || size != 4) {
        return dso_reject(addr, size, false, "disabled-or-invalid-access");
    }
    *value = s->offset_value;
    return MEMTX_OK;
}

static MemTxResult dso_write(void *opaque, hwaddr addr, uint64_t value,
                            unsigned size, MemTxAttrs attrs)
{
    return dso_reject(addr, size, true, "read-only-register");
}

static const MemoryRegionOps dso_ops = {
    .read_with_attrs = dso_read,
    .write_with_attrs = dso_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Reject invalid delivered transactions without silently splitting them. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void dso_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250DdrStatsOffsetState *s = QCOM_SM8250_DDR_STATS_OFFSET(obj);

    /* Disabled zero is an unknown placeholder; offset-known remains false. */
    s->offset_value = s->enabled_reference ? DDR_STATS_OFFSET_VALUE : 0;
}

static int dso_post_load(void *opaque, int version_id)
{
    QcomSm8250DdrStatsOffsetState *s = opaque;

    if (s->reference_profile != (uint32_t)s->enabled_reference) {
        error_report(TYPE_QCOM_SM8250_DDR_STATS_OFFSET
                     ": migrated profile mismatch");
        return -EINVAL;
    }
    if (s->reference_identity != DDR_STATS_OFFSET_REFERENCE_ID) {
        error_report(TYPE_QCOM_SM8250_DDR_STATS_OFFSET
                     ": migrated identity mismatch");
        return -EINVAL;
    }
    if (s->physical_base != DDR_STATS_OFFSET_BASE ||
        s->window_bytes != DDR_STATS_OFFSET_WINDOW_BYTES) {
        error_report(TYPE_QCOM_SM8250_DDR_STATS_OFFSET
                     ": migrated geometry mismatch");
        return -EINVAL;
    }
    if (s->offset_value !=
        (s->enabled_reference ? DDR_STATS_OFFSET_VALUE : 0)) {
        error_report(TYPE_QCOM_SM8250_DDR_STATS_OFFSET
                     ": migrated offset mismatch");
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_ddr_stats_offset = {
    .name = TYPE_QCOM_SM8250_DDR_STATS_OFFSET,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = dso_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(reference_profile, QcomSm8250DdrStatsOffsetState),
        VMSTATE_UINT32(reference_identity, QcomSm8250DdrStatsOffsetState),
        VMSTATE_UINT64(physical_base, QcomSm8250DdrStatsOffsetState),
        VMSTATE_UINT32(window_bytes, QcomSm8250DdrStatsOffsetState),
        VMSTATE_UINT32(offset_value, QcomSm8250DdrStatsOffsetState),
        VMSTATE_END_OF_LIST()
    },
};

static void dso_uint_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250DdrStatsOffsetState *s = QCOM_SM8250_DDR_STATS_OFFSET(obj);
    uint64_t value;

    switch (GPOINTER_TO_UINT(opaque)) {
    case DSO_PROP_PROFILE:
        value = s->reference_profile;
        break;
    case DSO_PROP_IDENTITY:
        value = s->reference_identity;
        break;
    case DSO_PROP_BASE:
        value = s->physical_base;
        break;
    case DSO_PROP_WINDOW_BYTES:
        value = s->window_bytes;
        break;
    case DSO_PROP_OFFSET_VALUE:
        value = s->offset_value;
        break;
    case DSO_PROP_VMSTATE_BYTES:
        value = DDR_STATS_OFFSET_VMSTATE_BYTES;
        break;
    case DSO_PROP_REGISTER_COUNT:
        value = 1;
        break;
    case DSO_PROP_IRQ_COUNT:
    case DSO_PROP_CLOCK_INPUT_COUNT:
    case DSO_PROP_CLOCK_OUTPUT_COUNT:
    case DSO_PROP_TIMER_COUNT:
        value = 0;
        break;
    default:
        g_assert_not_reached();
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dso_bool_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250DdrStatsOffsetState *s = QCOM_SM8250_DDR_STATS_OFFSET(obj);
    bool value = GPOINTER_TO_UINT(opaque) ? s->enabled_reference : false;

    visit_type_bool(v, name, &value, errp);
}

static void dso_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250DdrStatsOffsetState *s = QCOM_SM8250_DDR_STATS_OFFSET(dev);

    s->reference_profile = s->enabled_reference;
    s->offset_value = s->enabled_reference ? DDR_STATS_OFFSET_VALUE : 0;
    /*
     * The fixed off profile leaves this address an unassigned hole. This
     * preserves the pre-existing RPM reference's neighboring-address contract.
     * The board maps the region only after realize; migration never enables it.
     */
    memory_region_set_enabled(&s->iomem, s->enabled_reference);
}

static void dso_init(Object *obj)
{
    QcomSm8250DdrStatsOffsetState *s = QCOM_SM8250_DDR_STATS_OFFSET(obj);
    static const char *const uint_properties[] = {
        "enabled-profile", "reference-identity", "physical-base",
        "window-bytes", "offset-value", "vmstate-bytes", "register-count",
        "irq-output-count", "clock-input-count", "clock-output-count",
        "timer-count",
    };
    static const char *const unsupported[] = {
        "stats-payload-modeled", "rpm-offset-modeled", "firmware-modeled",
        "ready-modeled",
    };
    unsigned i;

    s->reference_identity = DDR_STATS_OFFSET_REFERENCE_ID;
    s->physical_base = DDR_STATS_OFFSET_BASE;
    s->window_bytes = DDR_STATS_OFFSET_WINDOW_BYTES;
    for (i = 0; i < ARRAY_SIZE(uint_properties); i++) {
        object_property_add(obj, uint_properties[i], "uint64", dso_uint_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
    }
    object_property_add(obj, "offset-known", "bool", dso_bool_get,
                        NULL, NULL, GUINT_TO_POINTER(1));
    for (i = 0; i < ARRAY_SIZE(unsupported); i++) {
        object_property_add(obj, unsupported[i], "bool", dso_bool_get,
                            NULL, NULL, GUINT_TO_POINTER(0));
    }
    memory_region_init_io(&s->iomem, obj, &dso_ops, s,
                          TYPE_QCOM_SM8250_DDR_STATS_OFFSET ".offset",
                          DDR_STATS_OFFSET_WINDOW_BYTES);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static const Property dso_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250DdrStatsOffsetState,
                     enabled_reference, false),
};

static void dso_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 AOP DDR statistics offset reference";
    dc->realize = dso_realize;
    dc->vmsd = &vmstate_ddr_stats_offset;
    device_class_set_props(dc, dso_properties);
    rc->phases.enter = dso_reset_enter;
}

static const TypeInfo ddr_stats_offset_info = {
    .name = TYPE_QCOM_SM8250_DDR_STATS_OFFSET,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250DdrStatsOffsetState),
    .instance_init = dso_init,
    .class_init = dso_class_init,
};

static void ddr_stats_offset_register_types(void)
{
    type_register_static(&ddr_stats_offset_info);
}
type_init(ddr_stats_offset_register_types)
