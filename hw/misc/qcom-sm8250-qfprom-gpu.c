/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Explicit immutable GPU nvmem cells from the original SM8250 full DT.
 *
 * qfprom_reg_read() performs byte reads. The speed_bin cell is the one byte
 * at 0x78419b (the guest nvmem core extracts bits 5..7); isense_slope is the
 * four little-endian bytes at 0x7845c8. No other QFPROM cell is supplied.
 * Neither value is inferred from a DT layout, a successful read, or zero.
 * An enabled instance requires both complete raw values explicitly.
 *
 * Two disabled-by-default decode guards preserve wide/unaligned accesses
 * for strict rejection. They do not map backing RAM or a whole fuse bank.
 * This is configuration storage, with no GPU, calibration, power or execution
 * completion. See tb371fc/docs/qfprom-gpu.md for evidence and policy limits.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-qfprom-gpu.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"

struct QcomSm8250QfpromGpuState {
    SysBusDevice parent_obj;
    MemoryRegion speed_iomem;
    MemoryRegion slope_iomem;
    bool enabled;
    char *speed_bin_byte;
    char *isense_slope_word;
    /* Immutable configuration, never writable guest state. */
    uint32_t profile;
    uint8_t speed_bin;
    uint32_t isense_slope;
};

static bool qfprom_gpu_parse(const char *text, unsigned digits, uint32_t *value)
{
    uint32_t parsed = 0;

    if (!text || strlen(text) != digits + 2 ||
        text[0] != '0' || text[1] != 'x') {
        return false;
    }
    for (unsigned i = 2; i < digits + 2; i++) {
        unsigned digit;

        if (text[i] >= '0' && text[i] <= '9') {
            digit = text[i] - '0';
        } else if (text[i] >= 'a' && text[i] <= 'f') {
            digit = text[i] - 'a' + 10;
        } else {
            return false;
        }
        parsed = (parsed << 4) | digit;
    }
    *value = parsed;
    return true;
}

static MemTxResult qfprom_gpu_reject(const char *cell, hwaddr offset,
                                    unsigned size, bool write)
{
    qemu_log_mask(LOG_UNIMP | LOG_GUEST_ERROR,
                  TYPE_QCOM_SM8250_QFPROM_GPU
                  ": rejected %s cell=%s offset=0x%" HWADDR_PRIx " size=%u\n",
                  write ? "write" : "read", cell, offset, size);
    return MEMTX_ERROR;
}

static MemTxResult qfprom_gpu_speed_read(void *opaque, hwaddr offset,
                                        uint64_t *value, unsigned size,
                                        MemTxAttrs attrs)
{
    QcomSm8250QfpromGpuState *s = opaque;

    *value = 0;
    if (!s->enabled || size != 1 || offset != QCOM_QFPROM_GPU_SPEED_OFFSET) {
        return qfprom_gpu_reject("speed-bin", offset, size, false);
    }
    *value = s->speed_bin;
    return MEMTX_OK;
}

static MemTxResult qfprom_gpu_speed_write(void *opaque, hwaddr offset,
                                         uint64_t value, unsigned size,
                                         MemTxAttrs attrs)
{
    return qfprom_gpu_reject("speed-bin", offset, size, true);
}

static MemTxResult qfprom_gpu_slope_read(void *opaque, hwaddr offset,
                                        uint64_t *value, unsigned size,
                                        MemTxAttrs attrs)
{
    QcomSm8250QfpromGpuState *s = opaque;

    *value = 0;
    if (!s->enabled || size != 1 || offset < QCOM_QFPROM_GPU_SLOPE_OFFSET ||
        offset >= QCOM_QFPROM_GPU_SLOPE_OFFSET + 4) {
        return qfprom_gpu_reject("isense-slope", offset, size, false);
    }
    *value = (s->isense_slope >>
              ((offset - QCOM_QFPROM_GPU_SLOPE_OFFSET) * 8)) & 0xff;
    return MEMTX_OK;
}

static MemTxResult qfprom_gpu_slope_write(void *opaque, hwaddr offset,
                                         uint64_t value, unsigned size,
                                         MemTxAttrs attrs)
{
    return qfprom_gpu_reject("isense-slope", offset, size, true);
}

static const MemoryRegionOps qfprom_gpu_speed_ops = {
    .read_with_attrs = qfprom_gpu_speed_read,
    .write_with_attrs = qfprom_gpu_speed_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static const MemoryRegionOps qfprom_gpu_slope_ops = {
    .read_with_attrs = qfprom_gpu_slope_read,
    .write_with_attrs = qfprom_gpu_slope_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void qfprom_gpu_reset(Object *obj, ResetType type)
{
    QcomSm8250QfpromGpuState *s = QCOM_SM8250_QFPROM_GPU(obj);

    memory_region_set_enabled(&s->speed_iomem, s->enabled);
    memory_region_set_enabled(&s->slope_iomem, s->enabled);
}

static const VMStateDescription vmstate_qfprom_gpu = {
    .name = TYPE_QCOM_SM8250_QFPROM_GPU,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        /* Reject mismatches; never replace destination configuration. */
        VMSTATE_UINT32_EQUAL(profile, QcomSm8250QfpromGpuState),
        VMSTATE_UINT8_EQUAL(speed_bin, QcomSm8250QfpromGpuState),
        VMSTATE_UINT32_EQUAL(isense_slope, QcomSm8250QfpromGpuState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property qfprom_gpu_properties[] = {
    DEFINE_PROP_BOOL("enabled", QcomSm8250QfpromGpuState, enabled, false),
    DEFINE_PROP_STRING("speed-bin-byte", QcomSm8250QfpromGpuState,
                       speed_bin_byte),
    DEFINE_PROP_STRING("isense-slope-word", QcomSm8250QfpromGpuState,
                       isense_slope_word),
};

static char *qfprom_gpu_bound_speed(Object *obj, Error **errp)
{
    QcomSm8250QfpromGpuState *s = QCOM_SM8250_QFPROM_GPU(obj);

    return g_strdup(s->enabled && s->speed_bin_byte ?
                     s->speed_bin_byte : "UNBOUND");
}

static char *qfprom_gpu_bound_slope(Object *obj, Error **errp)
{
    QcomSm8250QfpromGpuState *s = QCOM_SM8250_QFPROM_GPU(obj);

    return g_strdup(s->enabled && s->isense_slope_word ?
                     s->isense_slope_word : "UNBOUND");
}

static void qfprom_gpu_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250QfpromGpuState *s = QCOM_SM8250_QFPROM_GPU(dev);
    uint32_t speed;

    if (s->enabled) {
        if (!qfprom_gpu_parse(s->speed_bin_byte, 2, &speed)) {
            error_setg(errp, TYPE_QCOM_SM8250_QFPROM_GPU
                       ": enabled profile requires explicit speed-bin-byte=0xhh"
                       " (two lowercase hex digits); no value is bound by default");
            return;
        }
        if (!qfprom_gpu_parse(s->isense_slope_word, 8, &s->isense_slope)) {
            error_setg(errp, TYPE_QCOM_SM8250_QFPROM_GPU
                       ": enabled profile requires explicit isense-slope-word="
                       "0xhhhhhhhh (eight lowercase hex digits);"
                       " no value is bound by default");
            return;
        }
        s->speed_bin = speed;
    } else if (s->speed_bin_byte || s->isense_slope_word) {
        error_setg(errp, TYPE_QCOM_SM8250_QFPROM_GPU
                   ": speed-bin-byte and isense-slope-word require enabled=on");
        return;
    }
    s->profile = UINT32_C(0x51474600) | s->enabled;
    memory_region_set_enabled(&s->speed_iomem, s->enabled);
    memory_region_set_enabled(&s->slope_iomem, s->enabled);
}

static void qfprom_gpu_init(Object *obj)
{
    QcomSm8250QfpromGpuState *s = QCOM_SM8250_QFPROM_GPU(obj);

    memory_region_init_io(&s->speed_iomem, obj, &qfprom_gpu_speed_ops, s,
                          TYPE_QCOM_SM8250_QFPROM_GPU ".speed-bin-guard",
                          QCOM_QFPROM_GPU_GUARD_SIZE);
    memory_region_init_io(&s->slope_iomem, obj, &qfprom_gpu_slope_ops, s,
                          TYPE_QCOM_SM8250_QFPROM_GPU ".isense-slope-guard",
                          QCOM_QFPROM_GPU_GUARD_SIZE);
    memory_region_set_enabled(&s->speed_iomem, false);
    memory_region_set_enabled(&s->slope_iomem, false);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->speed_iomem);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->slope_iomem);
    object_property_add_str(obj, "bound-speed-bin-byte",
                            qfprom_gpu_bound_speed, NULL);
    object_property_add_str(obj, "bound-isense-slope-word",
                            qfprom_gpu_bound_slope, NULL);
}

static void qfprom_gpu_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 GPU nvmem cells, explicit immutable raw identities";
    dc->realize = qfprom_gpu_realize;
    dc->vmsd = &vmstate_qfprom_gpu;
    device_class_set_props(dc, qfprom_gpu_properties);
    rc->phases.hold = qfprom_gpu_reset;
}

static const TypeInfo qfprom_gpu_type = {
    .name = TYPE_QCOM_SM8250_QFPROM_GPU,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250QfpromGpuState),
    .instance_init = qfprom_gpu_init,
    .class_init = qfprom_gpu_class_init,
};

static void qfprom_gpu_register_types(void)
{
    type_register_static(&qfprom_gpu_type);
}

type_init(qfprom_gpu_register_types)
