/*
 * Bounded AOP entry reference platform.
 *
 * The memory geometry describes one captured ELF's PT_LOAD ranges, not a
 * verified SM8250 AOP physical memory map.  Cortex-M3, reset-vector placement,
 * the clock, and optional aliases are explicit diagnostic assumptions.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/module.h"
#include "hw/core/boards.h"
#include "hw/core/clock.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/arm/armv7m.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "system/address-spaces.h"
#include "target/arm/cpu.h"

#define TYPE_AOP_ENTRY_REFERENCE MACHINE_TYPE_NAME("aop-entry-reference")
OBJECT_DECLARE_SIMPLE_TYPE(AOPReferenceState, AOP_ENTRY_REFERENCE)

#define AOP_REF_REGIONS 5
#define AOP_REF_PADDING 0xa5
#define AOP_REF_BACKING_ALIGN 4096
#define AOP_REF_PPB_BASE 0xe0000000
#define AOP_REF_PPB_SIZE 0x00100000
#define AOP_REF_CLOCK_HZ 1000000

typedef struct AOPReferenceRegion {
    hwaddr base;
    uint32_t size;
    unsigned flags; /* ELF PF_X, PF_W and PF_R low bits. */
} AOPReferenceRegion;

/* PT_LOAD only: file offsets and memory sizes were independently recorded. */
static const AOPReferenceRegion captured_regions[AOP_REF_REGIONS] = {
    { 0x0b000000, 0x14f08, 5 },
    { 0x0b0e0000, 0x0604c, 6 },
    { 0x80800000, 0x0dfc8, 6 },
    { 0x80810000, 0x01384, 6 },
    { 0x80818000, 0x047b0, 7 },
};

typedef struct AOPReferenceGuard {
    MemoryRegion mr;
    AOPReferenceState *machine;
    hwaddr base;
} AOPReferenceGuard;

struct AOPReferenceState {
    MachineState parent_obj;
    ARMv7MState armv7m;
    Clock *clock;
    MemoryRegion backing[AOP_REF_REGIONS];
    MemoryRegion visible[AOP_REF_REGIONS];
    MemoryRegion aliases[2];
    AOPReferenceRegion regions[AOP_REF_REGIONS];
    AOPReferenceGuard unmapped;
    AOPReferenceGuard ppb;
    uint64_t unsupported_reads;
    uint64_t unsupported_writes;
    uint64_t last_unsupported_address;
    char *profile;
    bool initialized;
    bool local_aliases;
};

static MemTxResult aop_ref_bad_read(void *opaque, hwaddr addr, uint64_t *value,
                                   unsigned size, MemTxAttrs attrs)
{
    AOPReferenceGuard *guard = opaque;
    AOPReferenceState *s = guard->machine;

    s->unsupported_reads++;
    s->last_unsupported_address = guard->base + addr;
    /* This value must never be treated as a successful peripheral response. */
    *value = 0;
    return MEMTX_ERROR;
}

static MemTxResult aop_ref_bad_write(void *opaque, hwaddr addr, uint64_t value,
                                    unsigned size, MemTxAttrs attrs)
{
    AOPReferenceGuard *guard = opaque;
    AOPReferenceState *s = guard->machine;

    s->unsupported_writes++;
    s->last_unsupported_address = guard->base + addr;
    return MEMTX_ERROR;
}

static const MemoryRegionOps aop_ref_guard_ops = {
    .read_with_attrs = aop_ref_bad_read,
    .write_with_attrs = aop_ref_bad_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 1,
        .max_access_size = 8,
        .unaligned = true,
    },
    .impl = {
        .min_access_size = 1,
        .max_access_size = 8,
        .unaligned = true,
    },
};

static char *aop_ref_get_profile(Object *obj, Error **errp)
{
    return g_strdup(AOP_ENTRY_REFERENCE(obj)->profile);
}

static void aop_ref_set_profile(Object *obj, const char *value, Error **errp)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);

    if (s->initialized) {
        error_setg(errp, "AOP reference profile is fixed after initialization");
        return;
    }
    if (strcmp(value, "elf-physical-entry") &&
        strcmp(value, "local-alias-vector") &&
        strcmp(value, "local-alias-dtcm32k-reference")) {
        error_setg(errp, "Unknown AOP reference profile '%s'", value);
        return;
    }
    g_free(s->profile);
    s->profile = g_strdup(value);
}

static bool aop_ref_padding_pristine(Object *obj, Error **errp)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);
    unsigned i;

    if (!s->initialized) {
        return false;
    }
    for (i = 0; i < AOP_REF_REGIONS; i++) {
        const uint8_t *bytes = memory_region_get_ram_ptr(&s->backing[i]);
        uint32_t logical = s->regions[i].size;
        uint32_t backing = ROUND_UP(logical, AOP_REF_BACKING_ALIGN);
        uint32_t j;

        for (j = logical; j < backing; j++) {
            if (bytes[j] != AOP_REF_PADDING) {
                return false;
            }
        }
    }
    return true;
}

static char *aop_ref_get_layout(Object *obj, Error **errp)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);
    GString *layout = g_string_new(NULL);
    unsigned i;

    for (i = 0; i < AOP_REF_REGIONS; i++) {
        const AOPReferenceRegion *r = &s->regions[i];

        g_string_append_printf(layout, "%s0x%" HWADDR_PRIx ":0x%x:%u",
                               i ? ";" : "", r->base, r->size, r->flags);
    }
    if (s->local_aliases) {
        g_string_append_printf(layout, ";0x0:0x%x:5;0xe0000:0x%x:6",
                               s->regions[0].size, s->regions[1].size);
    }
    return g_string_free(layout, false);
}

static void aop_ref_init(MachineState *machine)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(machine);
    MemoryRegion *memory = get_system_memory();
    DeviceState *armv7m;
    unsigned i;

    if (machine->kernel_filename || machine->initrd_filename) {
        error_report("aop-entry-reference is loaded by the bounded debugger "
                     "runner; -kernel and -initrd are not supported");
        exit(EXIT_FAILURE);
    }
    memcpy(s->regions, captured_regions, sizeof(s->regions));
    s->local_aliases = strcmp(s->profile, "elf-physical-entry") != 0;
    if (!strcmp(s->profile, "local-alias-dtcm32k-reference")) {
        s->regions[1].size = 0x8000;
    }

    s->unmapped.machine = s;
    memory_region_init_io(&s->unmapped.mr, OBJECT(s), &aop_ref_guard_ops,
                          &s->unmapped, "aop-ref-unsupported",
                          UINT64_C(1) << 32);
    memory_region_add_subregion_overlap(memory, 0, &s->unmapped.mr, -100);

    for (i = 0; i < AOP_REF_REGIONS; i++) {
        AOPReferenceRegion *r = &s->regions[i];
        g_autofree char *backing_name = g_strdup_printf("aop-ref.backing%u", i);
        g_autofree char *visible_name = g_strdup_printf("aop-ref.visible%u", i);
        uint32_t backing_size = ROUND_UP(r->size, AOP_REF_BACKING_ALIGN);
        uint8_t *bytes;

        /*
         * A MachineState is not a DeviceState RAM migration owner.  These
         * uniquely named RAM blocks are registered globally with NULL owner.
         * Only exact-size aliases are visible to the CPU: backing padding is
         * private diagnostic storage, never additional guest RAM.
         */
        memory_region_init_ram(&s->backing[i], NULL, backing_name,
                               backing_size, &error_fatal);
        bytes = memory_region_get_ram_ptr(&s->backing[i]);
        memset(bytes + r->size, AOP_REF_PADDING, backing_size - r->size);
        memory_region_set_readonly(&s->backing[i], !(r->flags & 2));
        memory_region_init_alias(&s->visible[i], OBJECT(s), visible_name,
                                 &s->backing[i], 0, r->size);
        memory_region_add_subregion(memory, r->base, &s->visible[i]);
    }
    if (s->local_aliases) {
        memory_region_init_alias(&s->aliases[0], OBJECT(s), "aop-ref.code-alias",
                                 &s->backing[0], 0, s->regions[0].size);
        memory_region_add_subregion(memory, 0, &s->aliases[0]);
        memory_region_init_alias(&s->aliases[1], OBJECT(s), "aop-ref.data-alias",
                                 &s->backing[1], 0, s->regions[1].size);
        memory_region_add_subregion(memory, 0xe0000, &s->aliases[1]);
    }

    s->clock = clock_new(OBJECT(s), "reference-clock");
    clock_set_hz(s->clock, AOP_REF_CLOCK_HZ);
    object_initialize_child(OBJECT(s), "armv7m", &s->armv7m, TYPE_ARMV7M);
    armv7m = DEVICE(&s->armv7m);
    qdev_prop_set_string(armv7m, "cpu-type", ARM_CPU_TYPE_NAME("cortex-m3"));
    qdev_prop_set_uint32(armv7m, "num-irq", 32);
    qdev_prop_set_bit(armv7m, "enable-bitband", false);
    /*
     * Avoid an implicit reset read from an unmapped vector address.
     * The runner loads bytes and explicitly selects entry/SP while stopped.
     * This vector placement is reference configuration, not a measured ABI.
     */
    qdev_prop_set_uint32(armv7m, "init-nsvtor",
                        s->local_aliases ? 0 : captured_regions[0].base);
    qdev_connect_clock_in(armv7m, "cpuclk", s->clock);
    object_property_set_link(OBJECT(armv7m), "memory",
                             OBJECT(memory), &error_abort);
    sysbus_realize(SYS_BUS_DEVICE(armv7m), &error_fatal);

    /*
     * ARMv7M normally implements SCS/SysTick and privileged PPB RAZ/WI.
     * This diagnostic has no verified AOP PPB model; every such access must
     * be unsupported, even when an upstream ARMv7M device could answer it.
     */
    s->ppb.machine = s;
    s->ppb.base = AOP_REF_PPB_BASE;
    memory_region_init_io(&s->ppb.mr, OBJECT(s), &aop_ref_guard_ops, &s->ppb,
                          "aop-ref-unsupported-ppb", AOP_REF_PPB_SIZE);
    memory_region_add_subregion_overlap(&s->armv7m.container, AOP_REF_PPB_BASE,
                                        &s->ppb.mr, INT_MAX);

    assert(!CPU(s->armv7m.cpu)->ignore_memory_transaction_failures);
    assert(CPU_GET_CLASS(s->armv7m.cpu)->gdb_stop_before_watchpoint);
    /* Also registers the required ARMv7M CPU reset handler. */
    armv7m_load_kernel(s->armv7m.cpu, NULL, 0, 0);
    s->initialized = true;
}

static void aop_ref_instance_init(Object *obj)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);

    s->profile = g_strdup("elf-physical-entry");
    object_property_add_uint64_ptr(obj, "unsupported-read-count",
                                  &s->unsupported_reads, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "unsupported-write-count",
                                  &s->unsupported_writes, OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "last-unsupported-address",
                                  &s->last_unsupported_address,
                                  OBJ_PROP_FLAG_READ);
}

static void aop_ref_instance_finalize(Object *obj)
{
    g_free(AOP_ENTRY_REFERENCE(obj)->profile);
}

static void aop_ref_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);
    static const char *const valid_cpus[] = {
        ARM_CPU_TYPE_NAME("cortex-m3"),
        NULL,
    };

    mc->desc = "Private AOP entry reference (Cortex-M3; no peripheral responses)";
    mc->init = aop_ref_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-m3");
    mc->valid_cpu_types = valid_cpus;
    mc->min_cpus = mc->max_cpus = mc->default_cpus = 1;
    mc->default_ram_size = 0;
    object_class_property_add_str(oc, "profile", aop_ref_get_profile,
                                  aop_ref_set_profile);
    object_class_property_add_str(oc, "reference-layout", aop_ref_get_layout,
                                  NULL);
    object_class_property_add_bool(oc, "padding-pristine",
                                   aop_ref_padding_pristine, NULL);
}

static const TypeInfo aop_ref_info = {
    .name = TYPE_AOP_ENTRY_REFERENCE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(AOPReferenceState),
    .instance_init = aop_ref_instance_init,
    .instance_finalize = aop_ref_instance_finalize,
    .class_init = aop_ref_class_init,
    .interfaces = arm_machine_interfaces,
};

static void aop_ref_register_types(void)
{
    type_register_static(&aop_ref_info);
}

type_init(aop_ref_register_types)
