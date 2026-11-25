/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * SM8250 SPMI debug availability fuse aperture. Disabled by default.
 *
 * Live DT locates one word at 0x7820a8; the pinned vendor driver reads it
 * before registering the debug controller and tests disable bit 24. Neither
 * driver absence nor a missing log message establishes this word's value.
 * An enabled instance therefore requires an explicit complete fuse-word.
 * No default word, physical-fuse claim, debug controller, or SPMI transaction
 * is supplied here. The ordinary SPMI model and its migration ABI are separate.
 *
 * A 16-byte decode guard keeps 8-byte and unaligned accesses intact for
 * rejection. Only the single aligned 4-byte read at +8 is implemented;
 * neighboring addresses have no backing storage or readable fuse contents.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-spmi-debug-fuse.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"

struct QcomSm8250SpmiDebugFuseState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    bool enabled;
    char *fuse_word;
    /* Immutable configuration, not writable guest state. */
    uint32_t profile;
    uint32_t word;
};

static bool spmi_debug_fuse_parse(const char *text, uint32_t *word)
{
    uint32_t value = 0;

    /* Canonical full-width hexadecimal: no signs, truncation or whitespace. */
    if (!text || strlen(text) != 10 || text[0] != '0' || text[1] != 'x') {
        return false;
    }
    for (unsigned i = 2; i < 10; i++) {
        unsigned digit;

        if (text[i] >= '0' && text[i] <= '9') {
            digit = text[i] - '0';
        } else if (text[i] >= 'a' && text[i] <= 'f') {
            digit = text[i] - 'a' + 10;
        } else {
            return false;
        }
        value = (value << 4) | digit;
    }
    *word = value;
    return true;
}

static MemTxResult spmi_debug_fuse_reject(hwaddr offset, unsigned size,
                                         bool write)
{
    qemu_log_mask(LOG_UNIMP | LOG_GUEST_ERROR,
                  TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE
                  ": rejected %s offset=0x%" HWADDR_PRIx " size=%u\n",
                  write ? "write" : "read", offset, size);
    return MEMTX_ERROR;
}

static MemTxResult spmi_debug_fuse_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    QcomSm8250SpmiDebugFuseState *s = opaque;

    *value = 0;
    if (!s->enabled || size != 4 ||
        offset != QCOM_SPMI_DEBUG_FUSE_WORD_OFFSET) {
        return spmi_debug_fuse_reject(offset, size, false);
    }
    *value = s->word;
    return MEMTX_OK;
}

static MemTxResult spmi_debug_fuse_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    return spmi_debug_fuse_reject(offset, size, true);
}

static const MemoryRegionOps spmi_debug_fuse_ops = {
    .read_with_attrs = spmi_debug_fuse_read,
    .write_with_attrs = spmi_debug_fuse_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void spmi_debug_fuse_reset(Object *obj, ResetType type)
{
    QcomSm8250SpmiDebugFuseState *s = QCOM_SM8250_SPMI_DEBUG_FUSE(obj);

    /* Reset preserves the explicit immutable word. There is no mutable state. */
    memory_region_set_enabled(&s->iomem, s->enabled);
}

static const VMStateDescription vmstate_spmi_debug_fuse = {
    .name = TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        /* Equality rejects off/on or word mismatches without replacing config. */
        VMSTATE_UINT32_EQUAL(profile, QcomSm8250SpmiDebugFuseState),
        VMSTATE_UINT32_EQUAL(word, QcomSm8250SpmiDebugFuseState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property spmi_debug_fuse_properties[] = {
    DEFINE_PROP_BOOL("enabled", QcomSm8250SpmiDebugFuseState, enabled, false),
    DEFINE_PROP_STRING("fuse-word", QcomSm8250SpmiDebugFuseState, fuse_word),
};

static char *spmi_debug_fuse_bound_word(Object *obj, Error **errp)
{
    QcomSm8250SpmiDebugFuseState *s = QCOM_SM8250_SPMI_DEBUG_FUSE(obj);

    return g_strdup(s->enabled && s->fuse_word ? s->fuse_word : "UNBOUND");
}

static void spmi_debug_fuse_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250SpmiDebugFuseState *s = QCOM_SM8250_SPMI_DEBUG_FUSE(dev);

    if (s->enabled) {
        if (!spmi_debug_fuse_parse(s->fuse_word, &s->word)) {
            error_setg(errp, TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE
                       ": enabled profile requires explicit fuse-word=0xhhhhhhhh"
                       " (eight lowercase hex digits); no word is bound by default");
            return;
        }
    } else if (s->fuse_word) {
        error_setg(errp, TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE
                   ": fuse-word requires enabled=on");
        return;
    }
    s->profile = UINT32_C(0x53444600) | s->enabled;
    memory_region_set_enabled(&s->iomem, s->enabled);
}

static void spmi_debug_fuse_init(Object *obj)
{
    QcomSm8250SpmiDebugFuseState *s = QCOM_SM8250_SPMI_DEBUG_FUSE(obj);

    memory_region_init_io(&s->iomem, obj, &spmi_debug_fuse_ops, s,
                          TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE ".decode-guard",
                          QCOM_SPMI_DEBUG_FUSE_GUARD_SIZE);
    memory_region_set_enabled(&s->iomem, false);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->iomem);
    object_property_add_str(obj, "bound-word", spmi_debug_fuse_bound_word, NULL);
}

static void spmi_debug_fuse_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 SPMI debug availability fuse, explicit immutable word";
    dc->realize = spmi_debug_fuse_realize;
    dc->vmsd = &vmstate_spmi_debug_fuse;
    device_class_set_props(dc, spmi_debug_fuse_properties);
    rc->phases.hold = spmi_debug_fuse_reset;
}

static const TypeInfo spmi_debug_fuse_type = {
    .name = TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250SpmiDebugFuseState),
    .instance_init = spmi_debug_fuse_init,
    .class_init = spmi_debug_fuse_class_init,
};

static void spmi_debug_fuse_register_types(void)
{
    type_register_static(&spmi_debug_fuse_type);
}

type_init(spmi_debug_fuse_register_types)
