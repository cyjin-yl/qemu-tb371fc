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
#include "qemu/main-loop.h"
#include "qemu/module.h"
#include "hw/core/boards.h"
#include "hw/core/clock.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/arm/armv7m.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "system/address-spaces.h"
#include "system/runstate.h"
#include "target/arm/cpu.h"

#define TYPE_AOP_ENTRY_REFERENCE MACHINE_TYPE_NAME("aop-entry-reference")
OBJECT_DECLARE_SIMPLE_TYPE(AOPReferenceState, AOP_ENTRY_REFERENCE)

#define AOP_REF_REGIONS 5
#define AOP_REF_PADDING 0xa5
#define AOP_REF_BACKING_ALIGN 4096
#define AOP_REF_PPB_BASE 0xe0000000
#define AOP_REF_PPB_SIZE 0x00100000
#define AOP_REF_CLOCK_HZ 1000000
#define AOP_REF_SCS_BASE 0xe000e000
#define AOP_REF_SCS_PROFILE "local-alias-dtcm32k-scs-reference"
#define AOP_REF_SCS_READ 1
#define AOP_REF_SCS_WRITE 2
#define AOP_REF_SCS_WINDOWS 14

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

typedef struct AOPReferenceSCSRegister {
    const char *name;
    uint16_t offset;
    uint16_t size;
    uint8_t access;
    bool subword;
} AOPReferenceSCSRegister;

/*
 * A deliberately small Cortex-M3 architectural window set. These accesses
 * are forwarded to QEMU's real NVIC; this table supplies no register values.
 * IRQ count, priority bits and the clock remain reference assumptions.
 * Unlisted SCS, SysTick, MPU, debug and other PPB addresses stay guarded.
 */
static const AOPReferenceSCSRegister scs_registers[AOP_REF_SCS_WINDOWS] = {
    { "ictr",  0x004,  4, AOP_REF_SCS_READ, false },
    { "iser0", 0x100,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "icer0", 0x180,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "ispr0", 0x200,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "icpr0", 0x280,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "iabr0", 0x300,  4, AOP_REF_SCS_READ, false },
    { "ipr",   0x400, 32, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, true },
    { "cpuid", 0xd00,  4, AOP_REF_SCS_READ, false },
    { "icsr",  0xd04,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "vtor",  0xd08,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "aircr", 0xd0c,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "ccr",   0xd14,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
    { "shpr",  0xd18, 12, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, true },
    { "shcsr", 0xd24,  4, AOP_REF_SCS_READ | AOP_REF_SCS_WRITE, false },
};

typedef struct AOPReferenceSCSWindow {
    MemoryRegion mr;
    AOPReferenceState *machine;
    const AOPReferenceSCSRegister *reg;
} AOPReferenceSCSWindow;

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
    AOPReferenceSCSWindow scs_windows[AOP_REF_SCS_WINDOWS];
    MemoryRegion *nvic_sysregs;
    uint64_t scs_reads;
    uint64_t scs_writes;
    uint64_t scs_failed_reads;
    uint64_t scs_failed_writes;
    bool scs_reference;
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

static bool aop_ref_scs_access_valid(AOPReferenceSCSWindow *window,
                                     hwaddr addr, unsigned size, bool write)
{
    const AOPReferenceSCSRegister *r = window->reg;
    unsigned access = write ? AOP_REF_SCS_WRITE : AOP_REF_SCS_READ;

    if (!(r->access & access) || !(size == 1 || size == 2 || size == 4) ||
        addr >= r->size || size > r->size - addr || (addr & (size - 1))) {
        return false;
    }
    return r->subword || size == 4;
}

static MemTxResult aop_ref_scs_read(void *opaque, hwaddr addr,
                                  uint64_t *value, unsigned size,
                                  MemTxAttrs attrs)
{
    AOPReferenceSCSWindow *window = opaque;
    AOPReferenceState *s = window->machine;
    MemTxResult result;

    if (!aop_ref_scs_access_valid(window, addr, size, false)) {
        s->scs_failed_reads++;
        return aop_ref_bad_read(&s->ppb,
                                AOP_REF_SCS_BASE - AOP_REF_PPB_BASE +
                                window->reg->offset + addr,
                                value, size, attrs);
    }
    /*
     * Dispatch directly to the realized NVIC MR, never through the
     * ARMv7M container/PPB overlay. Preserve the CPU's privilege attrs.
     */
    result = memory_region_dispatch_read(s->nvic_sysregs,
                                         window->reg->offset + addr, value,
                                         size_memop(size) | MO_LE, attrs);
    if (result == MEMTX_OK) {
        s->scs_reads++;
    } else {
        s->scs_failed_reads++;
    }
    return result;
}

static MemTxResult aop_ref_scs_write(void *opaque, hwaddr addr,
                                   uint64_t value, unsigned size,
                                   MemTxAttrs attrs)
{
    AOPReferenceSCSWindow *window = opaque;
    AOPReferenceState *s = window->machine;
    MemTxResult result;

    if (!aop_ref_scs_access_valid(window, addr, size, true) ||
        /*
         * Cortex-M3 reserves AIRCR[1:0] for Debug use. QEMU logs their
         * UNPREDICTABLE use but otherwise returns success; do not treat
         * that as a supported operation in this diagnostic profile.
         * A wrong VECTKEY is instead ignored by the real NVIC.
         */
        (window->reg->offset == 0xd0c && (value >> 16) == 0x5fa &&
         (value & 3))) {
        s->scs_failed_writes++;
        return aop_ref_bad_write(&s->ppb,
                                 AOP_REF_SCS_BASE - AOP_REF_PPB_BASE +
                                 window->reg->offset + addr,
                                 value, size, attrs);
    }
    result = memory_region_dispatch_write(s->nvic_sysregs,
                                          window->reg->offset + addr, value,
                                          size_memop(size) | MO_LE, attrs);
    if (result == MEMTX_OK) {
        s->scs_writes++;
    } else {
        s->scs_failed_writes++;
    }
    return result;
}

/* Keep original transaction widths intact so invalid accesses are rejected. */
static const MemoryRegionOps aop_ref_scs_ops = {
    .read_with_attrs = aop_ref_scs_read,
    .write_with_attrs = aop_ref_scs_write,
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

static char *aop_ref_get_scs_windows(Object *obj, Error **errp)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);
    GString *result = g_string_new("[");
    unsigned i;

    if (s->scs_reference) {
        for (i = 0; i < AOP_REF_SCS_WINDOWS; i++) {
            const AOPReferenceSCSRegister *r = &scs_registers[i];

            g_string_append_printf(
                result,
                "%s{\"name\":\"%s\",\"base\":%u,\"size\":%u,"
                "\"read\":%s,\"write\":%s,\"subword\":%s}",
                i ? "," : "", r->name, AOP_REF_SCS_BASE + r->offset, r->size,
                (r->access & AOP_REF_SCS_READ) ? "true" : "false",
                (r->access & AOP_REF_SCS_WRITE) ? "true" : "false",
                r->subword ? "true" : "false");
        }
    }
    g_string_append_c(result, ']');
    return g_string_free(result, false);
}

static char *aop_ref_get_scs_state(Object *obj, Error **errp)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);

    if (!s->initialized || !bql_locked() ||
        !CPU(s->armv7m.cpu)->stopped || runstate_is_running()) {
        error_setg(errp, "SCS observation requires initialized/stopped CPU/BQL");
        return NULL;
    }
    return g_strdup_printf(
        "{\"enabled\":%s,\"external_irqs\":%u,\"priority_bits\":%u,"
        "\"reference_clock_hz\":%u,\"successful_reads\":%" G_GUINT64_FORMAT
        ",\"successful_writes\":%" G_GUINT64_FORMAT
        ",\"failed_reads\":%" G_GUINT64_FORMAT
        ",\"failed_writes\":%" G_GUINT64_FORMAT "}",
        s->scs_reference ? "true" : "false",
        s->armv7m.nvic.num_irq - NVIC_INTERNAL_VECTORS,
        s->armv7m.nvic.num_prio_bits, AOP_REF_CLOCK_HZ,
        s->scs_reads, s->scs_writes,
        s->scs_failed_reads, s->scs_failed_writes);
}

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
        strcmp(value, "local-alias-dtcm32k-reference") &&
        strcmp(value, AOP_REF_SCS_PROFILE)) {
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

/*
 * Host-only debugger evidence. GDB's stop packet reports wp->vaddr and clears
 * CPUState::watchpoint_hit; the hit-marked entries retain the overlap address.
 * Never read hitaddr from an unmarked entry: insertion does not initialize it.
 * This observer neither consumes hits nor changes CPU or memory behaviour.
 */
static char *aop_ref_get_watchpoint_hits(Object *obj, Error **errp)
{
    AOPReferenceState *s = AOP_ENTRY_REFERENCE(obj);
    CPUState *cpu;
    CPUWatchpoint *wp;
    GString *result;
    bool first = true;

    if (!s->initialized || !bql_locked()) {
        error_setg(errp, "Watchpoint evidence requires initialized machine/BQL");
        return NULL;
    }
    cpu = CPU(s->armv7m.cpu);
    if (!cpu->stopped || runstate_is_running()) {
        error_setg(errp, "Watchpoint evidence requires a stopped vCPU");
        return NULL;
    }
    result = g_string_new("[");
    QTAILQ_FOREACH(wp, &cpu->watchpoints, entry) {
        const char *rsp_type;

        if (!(wp->flags & BP_GDB) || !(wp->flags & BP_WATCHPOINT_HIT)) {
            continue;
        }
        switch (wp->flags & BP_MEM_ACCESS) {
        case BP_MEM_READ:
            rsp_type = "rwatch";
            break;
        case BP_MEM_WRITE:
            rsp_type = "watch";
            break;
        case BP_MEM_ACCESS:
            rsp_type = "awatch";
            break;
        default:
            rsp_type = "invalid";
            break;
        }
        g_string_append_printf(
            result,
            "%s{\"vaddr\":%" G_GUINT64_FORMAT ",\"len\":%" G_GUINT64_FORMAT
            ",\"hitaddr\":%" G_GUINT64_FORMAT ",\"flags\":%u"
            ",\"rsp_type\":\"%s\",\"gdb\":%s,\"cpu\":%s"
            ",\"stop_before_access\":%s,\"read_hit\":%s,\"write_hit\":%s}",
            first ? "" : ",", (guint64)wp->vaddr, (guint64)wp->len,
            (guint64)wp->hitaddr, (unsigned)wp->flags, rsp_type,
            (wp->flags & BP_GDB) ? "true" : "false",
            (wp->flags & BP_CPU) ? "true" : "false",
            (wp->flags & BP_STOP_BEFORE_ACCESS) ? "true" : "false",
            (wp->flags & BP_WATCHPOINT_HIT_READ) ? "true" : "false",
            (wp->flags & BP_WATCHPOINT_HIT_WRITE) ? "true" : "false");
        first = false;
    }
    g_string_append_c(result, ']');
    return g_string_free(result, false);
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
    s->scs_reference = !strcmp(s->profile, AOP_REF_SCS_PROFILE);
    if (!strcmp(s->profile, "local-alias-dtcm32k-reference") ||
        s->scs_reference) {
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
    if (s->scs_reference) {
        /* Explicit reference configuration, not an observed Kona priority. */
        qdev_prop_set_uint8(DEVICE(&s->armv7m.nvic), "num-prio-bits", 8);
    }
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
     * The original three profiles have no verified AOP PPB model, so their
     * entire PPB stays unsupported. Only the separate SCS reference profile
     * adds explicit architectural children inside this guard below.
     */
    s->ppb.machine = s;
    s->ppb.base = AOP_REF_PPB_BASE;
    memory_region_init_io(&s->ppb.mr, OBJECT(s), &aop_ref_guard_ops, &s->ppb,
                          "aop-ref-unsupported-ppb", AOP_REF_PPB_SIZE);
    memory_region_add_subregion_overlap(&s->armv7m.container, AOP_REF_PPB_BASE,
                                        &s->ppb.mr, INT_MAX);

    if (s->scs_reference) {
        s->nvic_sysregs = sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->armv7m.nvic),
                                                 0);
        for (i = 0; i < AOP_REF_SCS_WINDOWS; i++) {
            AOPReferenceSCSWindow *window = &s->scs_windows[i];
            const AOPReferenceSCSRegister *r = &scs_registers[i];
            g_autofree char *name = g_strdup_printf("aop-ref-scs.%s", r->name);

            window->machine = s;
            window->reg = r;
            memory_region_init_io(&window->mr, OBJECT(s), &aop_ref_scs_ops,
                                  window, name, r->size);
            /*
             * Only these exact children override the surrounding guard.
             * Dispatch targets the distinct original NVIC MR, so this does
             * not recurse through the guarded ARMv7M address space.
             */
            memory_region_add_subregion(&s->ppb.mr,
                                        AOP_REF_SCS_BASE - AOP_REF_PPB_BASE +
                                        r->offset, &window->mr);
        }
    }

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

    mc->desc = "Private AOP entry reference (Cortex-M3; guarded MMIO)";
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
    object_class_property_add_str(oc, "watchpoint-hits",
                                  aop_ref_get_watchpoint_hits, NULL);
    object_class_property_add_str(oc, "scs-reference-windows",
                                  aop_ref_get_scs_windows, NULL);
    object_class_property_add_str(oc, "scs-reference-state",
                                  aop_ref_get_scs_state, NULL);
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
