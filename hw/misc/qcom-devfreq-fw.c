/*
 * Qualcomm firmware devfreq: bounded explicit L3 request reference.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * OnePlusOSS/android_kernel_oneplus_sm8250,
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae,
 * drivers/devfreq/devfreq_qcom_fw.c; actual TB371FC DT supplies three resources.
 *
 * This is an enabled firmware-handoff reference with a synthetic request LUT.
 * It does not execute firmware, change cache timing, or publish an actual Clock.
 * See tb371fc/docs/devfreq-qcom-fw-reference.md.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-devfreq-fw.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define LUT_ENTRIES 40
#define LUT_STRIDE 4
#define VALID_PERF_STATES 4
#define NOMINAL_XO_HZ 19200000
#define LUT_IDENTITY UINT32_C(0x44463031) /* "DF01": explicit synthetic table */

enum {
    BANK_ENABLE,
    BANK_FREQUENCY_TABLE,
    BANK_PERFORMANCE,
};

/* Exact resource tuple order in /soc/qcom,devfreq-l3, not a 4 KiB aperture. */
const QcomDevfreqFwRegion
qcom_devfreq_fw_regions[QCOM_DEVFREQ_FW_WINDOWS] = {
    { 0x18590000, 4, "enable" },
    { 0x18590100, 0xa0, "frequency-table" },
    { 0x18590320, 4, "performance" },
};

static const uint32_t reference_lval[VALID_PERF_STATES] = { 16, 32, 64, 96 };

typedef struct DevfreqWindow {
    QcomDevfreqFwState *state;
    unsigned bank;
} DevfreqWindow;

struct QcomDevfreqFwState {
    SysBusDevice parent_obj;
    MemoryRegion mmio[QCOM_DEVFREQ_FW_WINDOWS];
    DevfreqWindow window[QCOM_DEVFREQ_FW_WINDOWS];
    bool enabled_reference;
    uint32_t reference_boot_perf_index;

    /* First 68 wire bytes are immutable identity, checked before runtime. */
    uint32_t profile_marker, instance_id;
    uint64_t enable_base, table_base, perf_base;
    uint32_t enable_size, table_size, perf_size, row_stride;
    uint32_t lut_identity, lut_entries, valid_states, nominal_xo_hz;

    uint32_t perf_index;
};

static uint32_t row_lval(unsigned row)
{
    return reference_lval[MIN(row, VALID_PERF_STATES - 1)];
}

static uint32_t lut_word(unsigned row)
{
    /* Source=1; no CPU core-count or voltage bits exist in this contract. */
    return (1U << 30) | row_lval(row);
}

static uint64_t nominal_hz(unsigned row)
{
    /*
     * The driver decodes this literal nominal value without a Clock provider.
     * This is a requested table rate, not measured/effective cache frequency.
     */
    return (uint64_t)NOMINAL_XO_HZ * row_lval(row);
}

static MemTxResult reject_access(DevfreqWindow *w, hwaddr offset,
                                 uint64_t value, unsigned size, bool write,
                                 const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  TYPE_QCOM_DEVFREQ_FW ": %s %s %s+0x%" HWADDR_PRIx
                  " width=%u value=0x%" PRIx64 "\n",
                  reason, write ? "write" : "read",
                  qcom_devfreq_fw_regions[w->bank].suffix,
                  offset, size, value);
    return MEMTX_ERROR;
}

static MemTxResult devfreq_read(void *opaque, hwaddr offset, uint64_t *value,
                                unsigned size, MemTxAttrs attrs)
{
    DevfreqWindow *w = opaque;
    QcomDevfreqFwState *s = w->state;

    *value = 0;
    if (!s->enabled_reference) {
        return reject_access(w, offset, 0, size, false, "unknown profile");
    }
    if (size != 4 || (offset & 3) ||
        offset >= qcom_devfreq_fw_regions[w->bank].byte_count) {
        return reject_access(w, offset, 0, size, false, "unsupported register");
    }
    switch (w->bank) {
    case BANK_ENABLE:
        *value = 1; /* Explicit reference handoff, not a firmware execution ACK. */
        break;
    case BANK_FREQUENCY_TABLE:
        *value = lut_word(offset / LUT_STRIDE);
        break;
    case BANK_PERFORMANCE:
        *value = s->perf_index;
        break;
    default:
        g_assert_not_reached();
    }
    return MEMTX_OK;
}

static MemTxResult devfreq_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size, MemTxAttrs attrs)
{
    DevfreqWindow *w = opaque;
    QcomDevfreqFwState *s = w->state;

    if (!s->enabled_reference) {
        return reject_access(w, offset, value, size, true, "unknown profile");
    }
    if (size != 4 || (offset & 3) ||
        offset >= qcom_devfreq_fw_regions[w->bank].byte_count) {
        return reject_access(w, offset, value, size, true, "unsupported register");
    }
    if (w->bank != BANK_PERFORMANCE) {
        return reject_access(w, offset, value, size, true, "read-only register");
    }
    if (value >= VALID_PERF_STATES) {
        return reject_access(w, offset, value, size, true,
                             "invalid performance index");
    }
    /*
     * The guest aggregates child votes in software. Store the actual complete
     * write, including a lower index; no invented hardware maximum or ACK.
     */
    s->perf_index = value;
    return MEMTX_OK;
}

static const MemoryRegionOps devfreq_ops = {
    .read_with_attrs = devfreq_read,
    .write_with_attrs = devfreq_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .valid.unaligned = false,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static void devfreq_reset_enter(Object *obj, ResetType type)
{
    QcomDevfreqFwState *s = QCOM_DEVFREQ_FW(obj);

    s->perf_index = s->enabled_reference ? s->reference_boot_perf_index : 0;
}

static int devfreq_post_load(void *opaque, int version)
{
    QcomDevfreqFwState *s = opaque;

    if (s->perf_index >= VALID_PERF_STATES) {
        error_report(TYPE_QCOM_DEVFREQ_FW ": invalid migrated performance index");
        return -EINVAL;
    }
    if (!s->enabled_reference && s->perf_index) {
        error_report(TYPE_QCOM_DEVFREQ_FW ": invalid migrated unknown state");
        return -EINVAL;
    }
    /* No timers, interrupts, external Clock state or derived mutable state. */
    return 0;
}

static const VMStateDescription vmstate_devfreq = {
    .name = TYPE_QCOM_DEVFREQ_FW,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = devfreq_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile_marker, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(instance_id, QcomDevfreqFwState),
        VMSTATE_UINT64_EQUAL(enable_base, QcomDevfreqFwState),
        VMSTATE_UINT64_EQUAL(table_base, QcomDevfreqFwState),
        VMSTATE_UINT64_EQUAL(perf_base, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(enable_size, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(table_size, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(perf_size, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(row_stride, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(reference_boot_perf_index, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(lut_identity, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(lut_entries, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(valid_states, QcomDevfreqFwState),
        VMSTATE_UINT32_EQUAL(nominal_xo_hz, QcomDevfreqFwState),
        VMSTATE_UINT32(perf_index, QcomDevfreqFwState),
        VMSTATE_END_OF_LIST()
    },
};

enum {
    Q_INSTANCE,
    Q_ENABLE_BASE,
    Q_TABLE_BASE,
    Q_PERF_BASE,
    Q_ENABLE_SIZE,
    Q_TABLE_SIZE,
    Q_PERF_SIZE,
    Q_STRIDE,
    Q_ENTRIES,
    Q_VALID,
    Q_IDENTITY,
    Q_NOMINAL_XO,
    Q_WINDOWS,
    Q_REGISTERS,
    Q_BYTES,
    Q_PERF = 100,
    Q_REQUESTED_LVAL,
    Q_REQUESTED_HZ,
    Q_LUT = 200,
};

static void scalar_get(Object *obj, Visitor *v, const char *name,
                       void *opaque, Error **errp)
{
    QcomDevfreqFwState *s = QCOM_DEVFREQ_FW(obj);
    unsigned key = GPOINTER_TO_UINT(opaque);
    uint64_t value = 0;
    uint32_t value32;
    bool wide = false;

    if (key >= Q_PERF && !s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_DEVFREQ_FW ": unknown profile");
        return;
    }
    if (key >= Q_LUT) {
        unsigned row = (key - Q_LUT) / 2;

        if ((key - Q_LUT) & 1) {
            value = nominal_hz(row);
            wide = true;
        } else {
            value = lut_word(row);
        }
    } else {
        switch (key) {
        case Q_INSTANCE: value = s->instance_id; break;
        case Q_ENABLE_BASE: value = s->enable_base; wide = true; break;
        case Q_TABLE_BASE: value = s->table_base; wide = true; break;
        case Q_PERF_BASE: value = s->perf_base; wide = true; break;
        case Q_ENABLE_SIZE: value = s->enable_size; break;
        case Q_TABLE_SIZE: value = s->table_size; break;
        case Q_PERF_SIZE: value = s->perf_size; break;
        case Q_STRIDE: value = s->row_stride; break;
        case Q_ENTRIES: value = s->lut_entries; break;
        case Q_VALID: value = s->valid_states; break;
        case Q_IDENTITY: value = s->lut_identity; break;
        case Q_NOMINAL_XO: value = s->nominal_xo_hz; break;
        case Q_WINDOWS: value = QCOM_DEVFREQ_FW_WINDOWS; break;
        case Q_REGISTERS: value = LUT_ENTRIES + 2; break;
        case Q_BYTES: value = LUT_ENTRIES * LUT_STRIDE + 8; break;
        case Q_PERF: value = s->perf_index; break;
        case Q_REQUESTED_LVAL: value = row_lval(s->perf_index); break;
        case Q_REQUESTED_HZ:
            value = nominal_hz(s->perf_index);
            wide = true;
            break;
        default:
            g_assert_not_reached();
        }
    }
    if (wide) {
        visit_type_uint64(v, name, &value, errp);
    } else {
        value32 = value;
        visit_type_uint32(v, name, &value32, errp);
    }
}

static void firmware_enabled_get(Object *obj, Visitor *v, const char *name,
                                 void *opaque, Error **errp)
{
    QcomDevfreqFwState *s = QCOM_DEVFREQ_FW(obj);
    bool value = true;

    if (!s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_DEVFREQ_FW ": unknown profile");
        return;
    }
    visit_type_bool(v, name, &value, errp);
}

static void devfreq_realize(DeviceState *dev, Error **errp)
{
    QcomDevfreqFwState *s = QCOM_DEVFREQ_FW(dev);
    unsigned i;

    if (s->reference_boot_perf_index >= VALID_PERF_STATES ||
        (!s->enabled_reference && s->reference_boot_perf_index)) {
        error_setg(errp, TYPE_QCOM_DEVFREQ_FW
                   ": reference-boot-perf-index requires 0..3 and"
                   " must be zero in the unknown profile");
        return;
    }
    s->profile_marker = s->enabled_reference ? 1 : 0;
    s->instance_id = 0;
    s->enable_base = qcom_devfreq_fw_regions[BANK_ENABLE].physical_base;
    s->table_base = qcom_devfreq_fw_regions[BANK_FREQUENCY_TABLE].physical_base;
    s->perf_base = qcom_devfreq_fw_regions[BANK_PERFORMANCE].physical_base;
    s->enable_size = qcom_devfreq_fw_regions[BANK_ENABLE].byte_count;
    s->table_size = qcom_devfreq_fw_regions[BANK_FREQUENCY_TABLE].byte_count;
    s->perf_size = qcom_devfreq_fw_regions[BANK_PERFORMANCE].byte_count;
    s->row_stride = LUT_STRIDE;
    s->lut_identity = LUT_IDENTITY;
    s->lut_entries = LUT_ENTRIES;
    s->valid_states = VALID_PERF_STATES;
    s->nominal_xo_hz = NOMINAL_XO_HZ;
    s->perf_index = s->enabled_reference ? s->reference_boot_perf_index : 0;
    for (i = 0; i < QCOM_DEVFREQ_FW_WINDOWS; i++) {
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_DEVFREQ_FW "-%s",
                                                qcom_devfreq_fw_regions[i].suffix);

        s->window[i].state = s;
        s->window[i].bank = i;
        memory_region_init_io(&s->mmio[i], OBJECT(s), &devfreq_ops,
                              &s->window[i], name,
                              qcom_devfreq_fw_regions[i].byte_count);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio[i]);
    }
}

static void devfreq_init(Object *obj)
{
    static const struct {
        const char *name, *type;
        unsigned key;
    } props[] = {
        { "instance-id", "uint32", Q_INSTANCE },
        { "enable-address", "uint64", Q_ENABLE_BASE },
        { "frequency-table-address", "uint64", Q_TABLE_BASE },
        { "performance-address", "uint64", Q_PERF_BASE },
        { "enable-resource-size", "uint32", Q_ENABLE_SIZE },
        { "frequency-table-resource-size", "uint32", Q_TABLE_SIZE },
        { "performance-resource-size", "uint32", Q_PERF_SIZE },
        { "lut-row-size", "uint32", Q_STRIDE },
        { "lut-max-entries", "uint32", Q_ENTRIES },
        { "valid-perf-count", "uint32", Q_VALID },
        { "reference-lut-identity", "uint32", Q_IDENTITY },
        { "reference-nominal-xo-hz", "uint32", Q_NOMINAL_XO },
        { "mapped-window-count", "uint32", Q_WINDOWS },
        { "mapped-register-count", "uint32", Q_REGISTERS },
        { "mapped-bytes", "uint32", Q_BYTES },
        { "performance-index", "uint32", Q_PERF },
        { "requested-lval", "uint32", Q_REQUESTED_LVAL },
        { "requested-frequency-hz", "uint64", Q_REQUESTED_HZ },
    };
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(props); i++) {
        object_property_add(obj, props[i].name, props[i].type, scalar_get,
                            NULL, NULL, GUINT_TO_POINTER(props[i].key));
    }
    for (i = 0; i < LUT_ENTRIES; i++) {
        g_autofree char *word = g_strdup_printf("lut%u-frequency-word", i);
        g_autofree char *rate = g_strdup_printf("lut%u-nominal-frequency-hz", i);

        object_property_add(obj, word, "uint32", scalar_get, NULL, NULL,
                            GUINT_TO_POINTER(Q_LUT + i * 2));
        object_property_add(obj, rate, "uint64", scalar_get, NULL, NULL,
                            GUINT_TO_POINTER(Q_LUT + i * 2 + 1));
    }
    object_property_add(obj, "firmware-enabled", "bool",
                        firmware_enabled_get, NULL, NULL, NULL);
}

static const Property devfreq_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomDevfreqFwState,
                     enabled_reference, false),
    DEFINE_PROP_UINT32("reference-boot-perf-index", QcomDevfreqFwState,
                      reference_boot_perf_index, 0),
};

static void devfreq_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm firmware devfreq explicit L3 request reference";
    dc->realize = devfreq_realize;
    dc->vmsd = &vmstate_devfreq;
    device_class_set_props(dc, devfreq_properties);
    rc->phases.enter = devfreq_reset_enter;
}

static const TypeInfo devfreq_info = {
    .name = TYPE_QCOM_DEVFREQ_FW,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomDevfreqFwState),
    .instance_init = devfreq_init,
    .class_init = devfreq_class_init,
};

static void devfreq_register_types(void)
{
    type_register_static(&devfreq_info);
}
type_init(devfreq_register_types)
