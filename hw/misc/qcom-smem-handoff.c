/*
 * Explicit, empty SMEM v12 reference handoff for direct-kernel boot.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Shared-memory ABI cross-checked against torvalds/linux v6.12 and the
 * official OnePlusOSS/android_kernel_oneplus_sm8250 source at commit
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae, drivers/soc/qcom/smem.c.
 * The separately pinned TB371FC mirror agrees on these on-memory fields;
 * none of these sources establishes the tablet's original SBL handoff bytes.
 * See tb371fc/docs/smem-reference.md for provenance and reference choices.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-smem-handoff.h"
#include "hw/core/loader.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bswap.h"
#include "qemu/error-report.h"
#include "qemu/module.h"
#include "system/memory.h"

#define SMEM_HEADER_VERSION7 0x5c
#define SMEM_HEADER_INITIALIZED 0xc0
#define SMEM_LEGACY_HEADER_SIZE 0x20d0
#define SMEM_PTABLE_HEADER_SIZE 32
#define SMEM_PTABLE_ENTRY_SIZE 48
#define SMEM_PARTITION_HEADER_SIZE 32
#define SMEM_INFO_SIZE 20
#define SMEM_REFERENCE_CACHELINE 64
#define SMEM_REFERENCE_ITEMS 512
#define SMEM_GLOBAL_HOST 0xfffe

struct QcomSmemHandoffState {
    SysBusDevice parent_obj;

    MemoryRegion *ram;
    bool reference_empty;
    uint32_t saved_reference_empty;
};

static void smem_build_reference(uint8_t *image)
{
    uint8_t *ptable = image + QCOM_SMEM_PTABLE_OFFSET;
    uint8_t *entry = ptable + SMEM_PTABLE_HEADER_SIZE;
    uint8_t *part = image + QCOM_SMEM_GLOBAL_OFFSET;
    uint8_t *info = entry + SMEM_PTABLE_ENTRY_SIZE;

    QEMU_BUILD_BUG_ON(QCOM_SMEM_GLOBAL_OFFSET < SMEM_LEGACY_HEADER_SIZE);
    QEMU_BUILD_BUG_ON(QCOM_SMEM_GLOBAL_OFFSET % 0x1000);
    QEMU_BUILD_BUG_ON(QCOM_SMEM_GLOBAL_SIZE < SMEM_PARTITION_HEADER_SIZE);
    QEMU_BUILD_BUG_ON(QCOM_SMEM_GLOBAL_SIZE % SMEM_REFERENCE_CACHELINE);
    QEMU_BUILD_BUG_ON(SMEM_PTABLE_HEADER_SIZE + SMEM_PTABLE_ENTRY_SIZE +
                     SMEM_INFO_SIZE > 0x1000);
    QEMU_BUILD_BUG_ON(QCOM_SMEM_BASE > UINT32_MAX);

    /* Legacy free/available fields and all legacy item entries stay unused. */
    stl_le_p(image + SMEM_HEADER_VERSION7, 12U << 16);
    stl_le_p(image + SMEM_HEADER_INITIALIZED, 1);

    memcpy(ptable, "$TOC", 4);
    stl_le_p(ptable + 4, 1);
    stl_le_p(ptable + 8, 1);

    stl_le_p(entry, QCOM_SMEM_GLOBAL_OFFSET);
    stl_le_p(entry + 4, QCOM_SMEM_GLOBAL_SIZE);
    stw_le_p(entry + 12, SMEM_GLOBAL_HOST);
    stw_le_p(entry + 14, SMEM_GLOBAL_HOST);
    stl_le_p(entry + 16, SMEM_REFERENCE_CACHELINE);

    memcpy(part, "$PRT", 4);
    stw_le_p(part + 4, SMEM_GLOBAL_HOST);
    stw_le_p(part + 6, SMEM_GLOBAL_HOST);
    stl_le_p(part + 8, QCOM_SMEM_GLOBAL_SIZE);
    stl_le_p(part + 12, SMEM_PARTITION_HEADER_SIZE);
    stl_le_p(part + 16, QCOM_SMEM_GLOBAL_SIZE);

    memcpy(info, "SIII", 4);
    stl_le_p(info + 4, QCOM_SMEM_SIZE);
    stl_le_p(info + 8, QCOM_SMEM_BASE);
    stw_le_p(info + 16, SMEM_REFERENCE_ITEMS);
}

static int smem_pre_save(void *opaque)
{
    QcomSmemHandoffState *s = opaque;

    s->saved_reference_empty = s->reference_empty;
    return 0;
}

static int smem_post_load(void *opaque, int version_id)
{
    QcomSmemHandoffState *s = opaque;

    if (s->saved_reference_empty != s->reference_empty) {
        error_report("%s: reference-empty differs between source and "
                     "destination", TYPE_QCOM_SMEM_HANDOFF);
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_smem = {
    .name = TYPE_QCOM_SMEM_HANDOFF,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = smem_pre_save,
    .post_load = smem_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(saved_reference_empty, QcomSmemHandoffState),
        VMSTATE_END_OF_LIST()
    },
};

static void smem_realize(DeviceState *dev, Error **errp)
{
    QcomSmemHandoffState *s = QCOM_SMEM_HANDOFF(dev);
    const uint64_t offset = QCOM_SMEM_BASE - QCOM_SMEM_RAM_BASE;
    g_autofree uint8_t *image = NULL;

    if (!s->reference_empty) {
        return;
    }
    if (!s->ram || !memory_region_is_ram(s->ram) ||
        memory_region_is_rom(s->ram) ||
        memory_region_size(s->ram) < offset ||
        memory_region_size(s->ram) - offset < QCOM_SMEM_SIZE) {
        error_setg(errp, "%s: complete SMEM range requires ordinary board RAM",
                   TYPE_QCOM_SMEM_HANDOFF);
        return;
    }

    image = g_malloc0(QCOM_SMEM_SIZE);
    smem_build_reference(image);
    /*
     * ROM is a loader record, not a read-only memory mapping. The destination
     * remains MachineState.ram and is writable by the real guest SMEM driver.
     * The ROM loader also detects overlaps with kernel/initrd/other blobs.
     */
    rom_add_blob_fixed("tb371fc.smem-reference-empty", image,
                       QCOM_SMEM_SIZE, QCOM_SMEM_BASE);
}

static const Property smem_properties[] = {
    DEFINE_PROP_LINK("ram", QcomSmemHandoffState, ram,
                     TYPE_MEMORY_REGION, MemoryRegion *),
    DEFINE_PROP_BOOL("reference-empty", QcomSmemHandoffState,
                     reference_empty, false),
};

static void smem_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Explicit empty SMEM v12 reference loader handoff";
    dc->realize = smem_realize;
    dc->vmsd = &vmstate_smem;
    dc->user_creatable = false;
    device_class_set_props(dc, smem_properties);
}

static const TypeInfo smem_info = {
    .name = TYPE_QCOM_SMEM_HANDOFF,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSmemHandoffState),
    .class_init = smem_class_init,
};

static void smem_register_types(void)
{
    type_register_static(&smem_info);
}
type_init(smem_register_types)
