/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * SM8250 RPM statistics: explicit immutable initialization reference.
 *
 * The pinned current active-slot AOP image is not asserted pristine. Static
 * analysis identifies a directory offset and three records, each cleared
 * before its type tag is stored. This leaf represents that initialization
 * point only. It neither executes AOP nor reports live residency statistics.
 * Counts and timestamps remain their proved initialized zero values; there
 * are no runtime updates, fabricated events, READY state, IRQs or timers.
 * The separate APSS QMP page and other message-RAM directory entries retain
 * their existing owners. No host or physical-device MMIO is accessed.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-rpm-stats.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

/* Reference interface version, not a silicon or AOP firmware version. */
#define RPM_STATS_REFERENCE_ABI_VERSION 1u
#define RPM_STATS_VMSTATE_BYTES 40u

struct QcomSm8250RpmStatsState {
    SysBusDevice parent_obj;
    MemoryRegion offset_mr;
    MemoryRegion records_mr;
    bool enabled_reference;

    /* VMState v1: profile u32, reference ABI u32, complete contract SHA256. */
    uint32_t reference_profile;
    uint32_t reference_abi;
    uint8_t source_contract[QCOM_SM8250_RPM_STATS_CONTRACT_DIGEST_BYTES];
};

static const uint8_t expected_contract[] =
    QCOM_SM8250_RPM_STATS_CONTRACT_SHA256_BYTES;
static const uint32_t record_tags[] = {
    QCOM_SM8250_RPM_STATS_TAG0,
    QCOM_SM8250_RPM_STATS_TAG1,
    QCOM_SM8250_RPM_STATS_TAG2,
};

G_STATIC_ASSERT(QCOM_SM8250_RPM_STATS_CONTRACT_DIGEST_BYTES == 32);
G_STATIC_ASSERT(sizeof(expected_contract) == 32);
G_STATIC_ASSERT(ARRAY_SIZE(record_tags) == QCOM_SM8250_RPM_STATS_RECORD_COUNT);
G_STATIC_ASSERT(QCOM_SM8250_RPM_STATS_RECORD_STRIDE == 32);
G_STATIC_ASSERT(QCOM_SM8250_RPM_STATS_RECORD_WINDOW_BYTES == 96);

static MemTxResult rpm_stats_reject(const char *region, hwaddr addr,
                                    unsigned size, bool write,
                                    const char *reason)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_SM8250_RPM_STATS
                  ": rejected %s region=%s offset=0x%" HWADDR_PRIx
                  " size %u (%s)\n", write ? "write" : "read",
                  region, addr, size, reason);
    return MEMTX_ERROR;
}

static MemTxResult rpm_stats_offset_read(void *opaque, hwaddr addr,
                                        uint64_t *value, unsigned size,
                                        MemTxAttrs attrs)
{
    QcomSm8250RpmStatsState *s = opaque;

    *value = 0;
    if (!s->enabled_reference || addr != 0 || size != 4) {
        return rpm_stats_reject("offset", addr, size, false,
                                "disabled-or-invalid-access");
    }
    *value = QCOM_SM8250_RPM_STATS_OFFSET_WORD_VALUE;
    return MEMTX_OK;
}

static uint32_t rpm_stats_record_word(hwaddr addr)
{
    if (addr % QCOM_SM8250_RPM_STATS_RECORD_STRIDE ==
        QCOM_SM8250_RPM_STATS_FIELD_STAT_TYPE_OFFSET) {
        return record_tags[addr / QCOM_SM8250_RPM_STATS_RECORD_STRIDE];
    }
    return 0;
}

static MemTxResult rpm_stats_records_read(void *opaque, hwaddr addr,
                                         uint64_t *value, unsigned size,
                                         MemTxAttrs attrs)
{
    QcomSm8250RpmStatsState *s = opaque;

    *value = 0;
    if (!s->enabled_reference || (size != 4 && size != 8) ||
        (addr & (size - 1)) ||
        addr > QCOM_SM8250_RPM_STATS_RECORD_WINDOW_BYTES - size) {
        return rpm_stats_reject("records", addr, size, false,
                                "disabled-or-invalid-access");
    }
    *value = rpm_stats_record_word(addr);
    if (size == 8) {
        *value |= (uint64_t)rpm_stats_record_word(addr + 4) << 32;
    }
    return MEMTX_OK;
}

static MemTxResult rpm_stats_offset_write(void *opaque, hwaddr addr,
                                         uint64_t value, unsigned size,
                                         MemTxAttrs attrs)
{
    return rpm_stats_reject("offset", addr, size, true,
                            "read-only-reference");
}

static MemTxResult rpm_stats_records_write(void *opaque, hwaddr addr,
                                          uint64_t value, unsigned size,
                                          MemTxAttrs attrs)
{
    return rpm_stats_reject("records", addr, size, true,
                            "read-only-reference");
}

static const MemoryRegionOps rpm_stats_offset_ops = {
    .read_with_attrs = rpm_stats_offset_read,
    .write_with_attrs = rpm_stats_offset_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Preserve scalar width/alignment for explicit callback rejection. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static const MemoryRegionOps rpm_stats_records_ops = {
    .read_with_attrs = rpm_stats_records_read,
    .write_with_attrs = rpm_stats_records_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void rpm_stats_set_identity(QcomSm8250RpmStatsState *s)
{
    s->reference_profile = s->enabled_reference;
    s->reference_abi = RPM_STATS_REFERENCE_ABI_VERSION;
    memcpy(s->source_contract, expected_contract, sizeof(s->source_contract));
}

static void rpm_stats_reset_enter(Object *obj, ResetType type)
{
    /* Contents are immutable; reset preserves the same initialization point. */
    rpm_stats_set_identity(QCOM_SM8250_RPM_STATS(obj));
}

static int rpm_stats_post_load(void *opaque, int version_id)
{
    QcomSm8250RpmStatsState *s = opaque;

    if (s->reference_profile != (uint32_t)s->enabled_reference) {
        error_report(TYPE_QCOM_SM8250_RPM_STATS ": migrated profile mismatch");
        return -EINVAL;
    }
    if (s->reference_abi != RPM_STATS_REFERENCE_ABI_VERSION) {
        error_report(TYPE_QCOM_SM8250_RPM_STATS ": migrated reference ABI mismatch");
        return -EINVAL;
    }
    if (memcmp(s->source_contract, expected_contract,
               sizeof(s->source_contract))) {
        error_report(TYPE_QCOM_SM8250_RPM_STATS ": migrated source contract mismatch");
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_rpm_stats = {
    .name = TYPE_QCOM_SM8250_RPM_STATS,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = rpm_stats_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(reference_profile, QcomSm8250RpmStatsState),
        VMSTATE_UINT32(reference_abi, QcomSm8250RpmStatsState),
        VMSTATE_BUFFER(source_contract, QcomSm8250RpmStatsState),
        VMSTATE_END_OF_LIST()
    },
};

static void rpm_stats_uint_get(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    QcomSm8250RpmStatsState *s = QCOM_SM8250_RPM_STATS(obj);
    uint64_t values[] = {
        s->reference_profile, s->reference_abi,
        QCOM_SM8250_RPM_STATS_OFFSET_PHYS_BASE,
        QCOM_SM8250_RPM_STATS_OFFSET_BYTES,
        QCOM_SM8250_RPM_STATS_OFFSET_WORD_VALUE,
        QCOM_SM8250_RPM_STATS_RECORD_PHYS_BASE,
        QCOM_SM8250_RPM_STATS_RECORD_COUNT,
        QCOM_SM8250_RPM_STATS_RECORD_STRIDE,
        QCOM_SM8250_RPM_STATS_RECORD_WINDOW_BYTES,
        QCOM_SM8250_RPM_STATS_OFFSET_BYTES +
            QCOM_SM8250_RPM_STATS_RECORD_WINDOW_BYTES,
        RPM_STATS_VMSTATE_BYTES, 0, 0, 0, 0,
    };
    unsigned index = GPOINTER_TO_UINT(opaque);

    g_assert(index < ARRAY_SIZE(values));
    visit_type_uint64(v, name, &values[index], errp);
}

static void rpm_stats_bool_get(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    bool value = GPOINTER_TO_UINT(opaque) != 0;

    visit_type_bool(v, name, &value, errp);
}

static char *rpm_stats_contract_get(Object *obj, Error **errp)
{
    QcomSm8250RpmStatsState *s = QCOM_SM8250_RPM_STATS(obj);
    char *text = g_malloc(sizeof(s->source_contract) * 2 + 1);
    unsigned i;

    for (i = 0; i < sizeof(s->source_contract); i++) {
        snprintf(text + i * 2, 3, "%02x", s->source_contract[i]);
    }
    return text;
}

static void rpm_stats_realize(DeviceState *dev, Error **errp)
{
    rpm_stats_set_identity(QCOM_SM8250_RPM_STATS(dev));
}

static void rpm_stats_init(Object *obj)
{
    QcomSm8250RpmStatsState *s = QCOM_SM8250_RPM_STATS(obj);
    static const char *const uint_properties[] = {
        "enabled-profile", "reference-abi", "offset-physical-base",
        "offset-window-bytes", "offset-word-value", "record-physical-base",
        "record-count", "record-stride", "record-window-bytes", "mapped-bytes",
        "vmstate-bytes", "irq-output-count", "clock-input-count",
        "clock-output-count", "timer-count",
    };
    static const char *const unsupported[] = {
        "aop-executed", "live-statistics", "runtime-updates-modeled",
        "ready-modeled",
    };
    unsigned i;

    rpm_stats_set_identity(s);
    for (i = 0; i < ARRAY_SIZE(uint_properties); i++) {
        object_property_add(obj, uint_properties[i], "uint64",
                            rpm_stats_uint_get, NULL, NULL,
                            GUINT_TO_POINTER(i));
    }
    object_property_add_str(obj, "contract-sha256", rpm_stats_contract_get,
                            NULL);
    object_property_add(obj, "immutable-reference", "bool",
                        rpm_stats_bool_get, NULL, NULL, GUINT_TO_POINTER(1));
    for (i = 0; i < ARRAY_SIZE(unsupported); i++) {
        object_property_add(obj, unsupported[i], "bool", rpm_stats_bool_get,
                            NULL, NULL, GUINT_TO_POINTER(0));
    }
    memory_region_init_io(&s->offset_mr, obj, &rpm_stats_offset_ops, s,
                          TYPE_QCOM_SM8250_RPM_STATS ".offset",
                          QCOM_SM8250_RPM_STATS_OFFSET_BYTES);
    memory_region_init_io(&s->records_mr, obj, &rpm_stats_records_ops, s,
                          TYPE_QCOM_SM8250_RPM_STATS ".records",
                          QCOM_SM8250_RPM_STATS_RECORD_WINDOW_BYTES);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->offset_mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->records_mr);
}

static const Property rpm_stats_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250RpmStatsState,
                     enabled_reference, false),
};

static void rpm_stats_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 RPM statistics immutable initialization reference";
    dc->realize = rpm_stats_realize;
    dc->vmsd = &vmstate_rpm_stats;
    device_class_set_props(dc, rpm_stats_properties);
    rc->phases.enter = rpm_stats_reset_enter;
}

static const TypeInfo rpm_stats_info = {
    .name = TYPE_QCOM_SM8250_RPM_STATS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250RpmStatsState),
    .instance_init = rpm_stats_init,
    .class_init = rpm_stats_class_init,
};

static void rpm_stats_register_types(void)
{
    type_register_static(&rpm_stats_info);
}
type_init(rpm_stats_register_types)
