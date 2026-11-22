/*
 * Experimental Lenovo TB371FC / Qualcomm SM8250 functional board model.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * This is a deliberately incomplete hardware model, not a virt-board alias.
 * The RAM aperture, MPIDRs, GIC, timer frequency/interrupts and active console
 * addresses come from an Android device-tree capture of a rooted TB371FC.
 *
 * CPU0..3 use upstream Cortex-A55. CPU4..7 use Cortex-A76 as an explicitly
 * advertised architectural surrogate: QEMU 11.1.3 has no Cortex-A77 model.
 * Neither the Qualcomm MIDRs nor unimplemented peripherals are fabricated.
 * PSCI is QEMU's SMC service, not Qualcomm's secure firmware. The generated
 * DT advertises only the implemented devices and two synthetic fixed clocks.
 *
 * The QUP wrapper's version register is a synthetic compatibility aid for the
 * upstream Linux GENI driver, not an emulation of the complete QUP wrapper.
 * Other wrapper register accesses return a bus error. All other absent SoC
 * peripherals remain unmapped.
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/units.h"
#include "qobject/qlist.h"
#include "qom/object.h"
#include "hw/arm/boot.h"
#include "hw/arm/machines-qom.h"
#include "hw/char/qcom-geni-uart.h"
#include "hw/core/boards.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/sysbus.h"
#include "hw/intc/arm_gicv3.h"
#include "hw/intc/arm_gicv3_common.h"
#include "system/address-spaces.h"
#include "system/device_tree.h"
#include "system/qtest.h"
#include "system/system.h"
#include "system/tcg.h"
#include "target/arm/cpu.h"
#include "target/arm/gtimer.h"

#define TYPE_TB371FC_MACHINE MACHINE_TYPE_NAME("tb371fc")
OBJECT_DECLARE_SIMPLE_TYPE(TB371FCMachineState, TB371FC_MACHINE)

#define TB371FC_CPUS             8
#define TB371FC_RAM_BASE         UINT64_C(0x80000000)
#define TB371FC_RAM_MAX          (8 * GiB)
#define TB371FC_RAM_HOLE_BASE    UINT64_C(0xbbb00000)
#define TB371FC_RAM_HOLE_END     UINT64_C(0xc0000000)
#define TB371FC_GIC_DIST_BASE    UINT64_C(0x17a00000)
#define TB371FC_GIC_DIST_SIZE    UINT64_C(0x10000)
#define TB371FC_GIC_REDIST_BASE  UINT64_C(0x17a60000)
#define TB371FC_GIC_REDIST_SIZE  UINT64_C(0x100000)
#define TB371FC_UART_BASE        UINT64_C(0x00a90000)
#define TB371FC_UART_SIZE        UINT64_C(0x4000)
#define TB371FC_UART_SPI         357
#define TB371FC_QUP_BASE         UINT64_C(0x00ac0000)
#define TB371FC_QUP_SIZE         UINT64_C(0x6000)
#define TB371FC_TIMER_HZ         19200000

/*
 * Model capacity, not a captured GICD_TYPER value. 960 SPIs cover the SM8250
 * interrupt space without advertising the architecturally reserved IDs.
 */
#define TB371FC_NUM_SPI          960
#define TB371FC_PPI_SEC_TIMER    13
#define TB371FC_PPI_PHYS_TIMER   14
#define TB371FC_PPI_VIRT_TIMER   11
#define TB371FC_PPI_HYP_TIMER    12
#define TB371FC_PPI_MAINT        9

/* A fixed serial clock chosen to represent 115200 baud exactly. */
#define TB371FC_UART_CLOCK_HZ    7372800
#define TB371FC_QUP_VERSION      0x20050000

#define TB371FC_MEMORY_NODE "/memory@80000000"
#define TB371FC_GIC_NODE "/soc/interrupt-controller@17a00000"
#define TB371FC_QUP_NODE "/soc/geniqup@ac0000"
#define TB371FC_UART_NODE TB371FC_QUP_NODE "/serial@a90000"

struct TB371FCMachineState {
    MachineState parent_obj;
    ARMCPU *cpu[TB371FC_CPUS];
    DeviceState *gic;
    QcomGeniUartState uart;
    MemoryRegion qup_version;
    struct arm_boot_info bootinfo;
    int fdt_size;
};

static const char *tb371fc_cpu_type(unsigned int index)
{
    return index < 4 ? ARM_CPU_TYPE_NAME("cortex-a55") :
                       ARM_CPU_TYPE_NAME("cortex-a76");
}

static MemTxResult tb371fc_qup_read(void *opaque, hwaddr offset,
                                  uint64_t *value, unsigned int size,
                                  MemTxAttrs attrs)
{
    if (offset == 4 && size == 4) {
        *value = TB371FC_QUP_VERSION;
        return MEMTX_OK;
    }

    qemu_log_mask(LOG_UNIMP,
                  "tb371fc: unimplemented QUP read at 0x%" HWADDR_PRIx "\n",
                  TB371FC_QUP_BASE + offset);
    *value = 0;
    return MEMTX_ERROR;
}

static MemTxResult tb371fc_qup_write(void *opaque, hwaddr offset,
                                   uint64_t value, unsigned int size,
                                   MemTxAttrs attrs)
{
    qemu_log_mask(LOG_UNIMP,
                  "tb371fc: unimplemented QUP write at 0x%" HWADDR_PRIx "\n",
                  TB371FC_QUP_BASE + offset);
    return MEMTX_ERROR;
}

static const MemoryRegionOps tb371fc_qup_ops = {
    .read_with_attrs = tb371fc_qup_read,
    .write_with_attrs = tb371fc_qup_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .valid.unaligned = false,
};

/*
 * arm_load_dtb() normally replaces memory nodes with one contiguous bank.
 * Reapply the observed exclusion after that step, so the final guest DT and
 * -machine dumpdtb output agree. Smaller explicitly requested RAM sizes are
 * clipped to the same physical aperture and reserve only the intersecting
 * part of the captured hole. Backing memory remains physically contiguous.
 */
static void tb371fc_fixup_memory(const struct arm_boot_info *info, void *fdt)
{
    uint64_t end = TB371FC_RAM_BASE + info->ram_size;
    uint64_t low_size = MIN(end, TB371FC_RAM_HOLE_BASE) - TB371FC_RAM_BASE;
    const char *hole = "/reserved-memory/firmware-hole@bbb00000";

    if (qemu_fdt_getprop_cell(fdt, "/", "#address-cells", NULL,
                              &error_fatal) != 2 ||
        qemu_fdt_getprop_cell(fdt, "/", "#size-cells", NULL,
                              &error_fatal) != 2) {
        error_report("tb371fc requires a DT with two root address/size cells");
        exit(1);
    }

    qemu_fdt_add_path(fdt, TB371FC_MEMORY_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_MEMORY_NODE, "device_type", "memory");
    if (end > TB371FC_RAM_HOLE_END) {
        qemu_fdt_setprop_sized_cells(fdt, TB371FC_MEMORY_NODE, "reg",
                                     2, TB371FC_RAM_BASE, 2, low_size,
                                     2, TB371FC_RAM_HOLE_END,
                                     2, end - TB371FC_RAM_HOLE_END);
    } else {
        qemu_fdt_setprop_sized_cells(fdt, TB371FC_MEMORY_NODE, "reg",
                                     2, TB371FC_RAM_BASE, 2, low_size);
    }

    if (end > TB371FC_RAM_HOLE_BASE) {
        qemu_fdt_add_path(fdt, "/reserved-memory");
        qemu_fdt_setprop_cell(fdt, "/reserved-memory", "#address-cells", 2);
        qemu_fdt_setprop_cell(fdt, "/reserved-memory", "#size-cells", 2);
        qemu_fdt_setprop(fdt, "/reserved-memory", "ranges", NULL, 0);
        qemu_fdt_add_path(fdt, hole);
        qemu_fdt_setprop_sized_cells(fdt, hole, "reg",
                                     2, TB371FC_RAM_HOLE_BASE,
                                     2, MIN(end, TB371FC_RAM_HOLE_END) -
                                        TB371FC_RAM_HOLE_BASE);
        qemu_fdt_setprop(fdt, hole, "no-map", NULL, 0);
    }
}

static uint32_t tb371fc_add_fixed_clock(void *fdt, const char *path,
                                      uint32_t frequency)
{
    uint32_t phandle = qemu_fdt_alloc_phandle(fdt);

    qemu_fdt_add_subnode(fdt, path);
    qemu_fdt_setprop_string(fdt, path, "compatible", "fixed-clock");
    qemu_fdt_setprop_cell(fdt, path, "#clock-cells", 0);
    qemu_fdt_setprop_cell(fdt, path, "clock-frequency", frequency);
    qemu_fdt_setprop_cell(fdt, path, "phandle", phandle);
    return phandle;
}

static void tb371fc_create_fdt(TB371FCMachineState *s)
{
    MachineState *ms = MACHINE(s);
    const char compatible[] = "qemu,tb371fc\0lenovo,tb371fc\0qcom,sm8250";
    const char qup_clock_names[] = "m-ahb\0s-ahb";
    uint32_t gic_phandle, ahb_clock, se_clock;
    void *fdt = create_device_tree(&s->fdt_size);

    if (!fdt) {
        error_report("tb371fc: could not allocate device tree");
        exit(1);
    }
    ms->fdt = fdt;
    qemu_fdt_setprop_cell(fdt, "/", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/", "#size-cells", 2);
    qemu_fdt_setprop(fdt, "/", "compatible", compatible, sizeof(compatible));
    qemu_fdt_setprop_string(fdt, "/", "model",
                            "QEMU experimental Lenovo TB371FC functional subset");
    qemu_fdt_setprop_string(fdt, "/", "qemu,cpu-model-note",
                            "CPU0-3 Cortex-A55; CPU4-7 Cortex-A76 surrogate "
                            "for Cortex-A77; no timing fidelity");
    qemu_fdt_setprop_string(fdt, "/", "qemu,qup-model-note",
                            "Synthetic fixed clocks and QUP 2.5 version; "
                            "GENI UART FIFO only");

    qemu_fdt_add_subnode(fdt, "/chosen");
    qemu_fdt_setprop_string(fdt, "/chosen", "stdout-path",
                            TB371FC_UART_NODE ":115200n8");
    qemu_fdt_add_subnode(fdt, "/aliases");
    qemu_fdt_setprop_string(fdt, "/aliases", "serial0", TB371FC_UART_NODE);

    qemu_fdt_add_subnode(fdt, "/cpus");
    qemu_fdt_setprop_cell(fdt, "/cpus", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/cpus", "#size-cells", 0);
    for (unsigned int i = 0; i < TB371FC_CPUS; i++) {
        g_autofree char *path = g_strdup_printf("/cpus/cpu@%x", i << 8);

        qemu_fdt_add_subnode(fdt, path);
        qemu_fdt_setprop_string(fdt, path, "device_type", "cpu");
        qemu_fdt_setprop_string(fdt, path, "compatible",
                                s->cpu[i]->dtb_compatible);
        qemu_fdt_setprop_u64(fdt, path, "reg", i << 8);
        qemu_fdt_setprop_string(fdt, path, "enable-method", "psci");
    }

    /* arm_load_kernel() also updates this with the actual PSCI version. */
    qemu_fdt_add_subnode(fdt, "/psci");
    qemu_fdt_setprop_string(fdt, "/psci", "compatible", "arm,psci-1.0");
    qemu_fdt_setprop_string(fdt, "/psci", "method", "smc");

    ahb_clock = tb371fc_add_fixed_clock(fdt, "/qemu-ahb-clock",
                                      TB371FC_TIMER_HZ);
    se_clock = tb371fc_add_fixed_clock(fdt, "/qemu-uart-clock",
                                     TB371FC_UART_CLOCK_HZ);

    qemu_fdt_add_subnode(fdt, "/soc");
    qemu_fdt_setprop_string(fdt, "/soc", "compatible", "simple-bus");
    qemu_fdt_setprop_cell(fdt, "/soc", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/soc", "#size-cells", 2);
    qemu_fdt_setprop(fdt, "/soc", "ranges", NULL, 0);

    gic_phandle = qemu_fdt_alloc_phandle(fdt);
    qemu_fdt_setprop_cell(fdt, "/", "interrupt-parent", gic_phandle);
    qemu_fdt_add_subnode(fdt, TB371FC_GIC_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_GIC_NODE, "compatible", "arm,gic-v3");
    qemu_fdt_setprop(fdt, TB371FC_GIC_NODE, "interrupt-controller", NULL, 0);
    qemu_fdt_setprop_cell(fdt, TB371FC_GIC_NODE, "#interrupt-cells", 3);
    qemu_fdt_setprop_cell(fdt, TB371FC_GIC_NODE, "#address-cells", 0);
    qemu_fdt_setprop_cell(fdt, TB371FC_GIC_NODE, "#redistributor-regions", 1);
    qemu_fdt_setprop_u64(fdt, TB371FC_GIC_NODE, "redistributor-stride",
                         0x20000);
    qemu_fdt_setprop_cell(fdt, TB371FC_GIC_NODE, "phandle", gic_phandle);
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_GIC_NODE, "reg",
                                 2, TB371FC_GIC_DIST_BASE,
                                 2, TB371FC_GIC_DIST_SIZE,
                                 2, TB371FC_GIC_REDIST_BASE,
                                 2, TB371FC_GIC_REDIST_SIZE);
    qemu_fdt_setprop_cells(fdt, TB371FC_GIC_NODE, "interrupts",
                           1, TB371FC_PPI_MAINT, 4);

    qemu_fdt_add_subnode(fdt, "/timer");
    qemu_fdt_setprop_string(fdt, "/timer", "compatible", "arm,armv8-timer");
    qemu_fdt_setprop_cell(fdt, "/timer", "clock-frequency", TB371FC_TIMER_HZ);
    qemu_fdt_setprop_cells(fdt, "/timer", "interrupts",
                           1, TB371FC_PPI_SEC_TIMER, 8,
                           1, TB371FC_PPI_PHYS_TIMER, 8,
                           1, TB371FC_PPI_VIRT_TIMER, 8,
                           1, TB371FC_PPI_HYP_TIMER, 8);
    qemu_fdt_setprop(fdt, "/timer", "always-on", NULL, 0);

    qemu_fdt_add_subnode(fdt, TB371FC_QUP_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_QUP_NODE, "compatible",
                            "qcom,geni-se-qup");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_QUP_NODE, "reg",
                                 2, TB371FC_QUP_BASE, 2, TB371FC_QUP_SIZE);
    qemu_fdt_setprop_cell(fdt, TB371FC_QUP_NODE, "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, TB371FC_QUP_NODE, "#size-cells", 2);
    qemu_fdt_setprop(fdt, TB371FC_QUP_NODE, "ranges", NULL, 0);
    qemu_fdt_setprop(fdt, TB371FC_QUP_NODE, "clock-names", qup_clock_names,
                     sizeof(qup_clock_names));
    qemu_fdt_setprop_cells(fdt, TB371FC_QUP_NODE, "clocks",
                           ahb_clock, ahb_clock);

    qemu_fdt_add_subnode(fdt, TB371FC_UART_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_UART_NODE, "compatible",
                            "qcom,geni-debug-uart");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_UART_NODE, "reg",
                                 2, TB371FC_UART_BASE, 2, TB371FC_UART_SIZE);
    qemu_fdt_setprop_cells(fdt, TB371FC_UART_NODE, "interrupts",
                           0, TB371FC_UART_SPI, 4);
    qemu_fdt_setprop_string(fdt, TB371FC_UART_NODE, "clock-names", "se");
    qemu_fdt_setprop_cell(fdt, TB371FC_UART_NODE, "clocks", se_clock);
    qemu_fdt_setprop_cell(fdt, TB371FC_UART_NODE, "current-speed", 115200);
    qemu_fdt_setprop_string(fdt, TB371FC_UART_NODE, "status", "okay");

    tb371fc_fixup_memory(&s->bootinfo, fdt);
}

static void *tb371fc_get_dtb(const struct arm_boot_info *info, int *size)
{
    TB371FCMachineState *s = container_of(info, TB371FCMachineState, bootinfo);

    *size = s->fdt_size;
    return MACHINE(s)->fdt;
}

static const CPUArchIdList *tb371fc_possible_cpu_arch_ids(MachineState *ms)
{
    if (!ms->possible_cpus) {
        ms->possible_cpus = g_malloc0(sizeof(CPUArchIdList) +
                                     TB371FC_CPUS * sizeof(CPUArchId));
        ms->possible_cpus->len = TB371FC_CPUS;
        for (unsigned int i = 0; i < TB371FC_CPUS; i++) {
            CPUArchId *id = &ms->possible_cpus->cpus[i];

            id->type = tb371fc_cpu_type(i);
            id->arch_id = i << 8;
            id->props.has_socket_id = true;
            id->props.socket_id = 0;
            id->props.has_core_id = true;
            id->props.core_id = i;
            id->props.has_thread_id = true;
            id->props.thread_id = 0;
        }
    }
    return ms->possible_cpus;
}

static CpuInstanceProperties tb371fc_cpu_index_to_props(MachineState *ms,
                                                       unsigned int index)
{
    const CPUArchIdList *cpus = tb371fc_possible_cpu_arch_ids(ms);

    assert(index < cpus->len);
    return cpus->cpus[index].props;
}

static void tb371fc_create_cpus(TB371FCMachineState *s, MemoryRegion *sysmem)
{
    const CPUArchIdList *cpus = tb371fc_possible_cpu_arch_ids(MACHINE(s));

    for (unsigned int i = 0; i < TB371FC_CPUS; i++) {
        g_autofree char *name = g_strdup_printf("cpu%u", i);
        Object *obj = object_new(cpus->cpus[i].type);

        s->cpu[i] = ARM_CPU(obj);
        CPU(obj)->cpu_index = i;
        object_property_add_child(OBJECT(s), name, obj);
        object_property_set_int(obj, "mp-affinity", cpus->cpus[i].arch_id,
                                &error_abort);
        object_property_set_int(obj, "cntfrq", TB371FC_TIMER_HZ, &error_abort);
        object_property_set_bool(obj, "has_el3", false, &error_abort);
        object_property_set_bool(obj, "has_el2", true, &error_abort);
        object_property_set_bool(obj, "start-powered-off", i != 0,
                                 &error_abort);
        object_property_set_int(obj, "psci-conduit", QEMU_PSCI_CONDUIT_SMC,
                                &error_abort);
        object_property_set_link(obj, "memory", OBJECT(sysmem), &error_abort);
        qdev_realize(DEVICE(obj), NULL, &error_fatal);
        object_unref(obj);
    }
}

static void tb371fc_create_gic(TB371FCMachineState *s)
{
    SysBusDevice *busdev;
    QList *redists = qlist_new();
    const struct {
        unsigned int output;
        unsigned int ppi;
    } timer_irqs[] = {
        { GTIMER_PHYS, TB371FC_PPI_PHYS_TIMER },
        { GTIMER_VIRT, TB371FC_PPI_VIRT_TIMER },
        { GTIMER_HYP, TB371FC_PPI_HYP_TIMER },
        { GTIMER_SEC, TB371FC_PPI_SEC_TIMER },
    };

    s->gic = qdev_new(TYPE_ARM_GICV3);
    object_property_add_child(OBJECT(s), "gic", OBJECT(s->gic));
    qdev_prop_set_uint32(s->gic, "revision", 3);
    qdev_prop_set_uint32(s->gic, "num-cpu", TB371FC_CPUS);
    qdev_prop_set_uint32(s->gic, "num-irq", TB371FC_NUM_SPI + 32);
    qdev_prop_set_bit(s->gic, "has-security-extensions", false);
    qdev_prop_set_bit(s->gic, "has-lpi", false);
    qlist_append_int(redists, TB371FC_CPUS);
    qdev_prop_set_array(s->gic, "redist-region-count", redists);
    busdev = SYS_BUS_DEVICE(s->gic);
    sysbus_realize_and_unref(busdev, &error_fatal);
    sysbus_mmio_map(busdev, 0, TB371FC_GIC_DIST_BASE);
    sysbus_mmio_map(busdev, 1, TB371FC_GIC_REDIST_BASE);

    for (unsigned int i = 0; i < TB371FC_CPUS; i++) {
        DeviceState *cpu = DEVICE(s->cpu[i]);
        unsigned int ppi_base = TB371FC_NUM_SPI + i * GIC_INTERNAL + 16;

        for (unsigned int j = 0; j < ARRAY_SIZE(timer_irqs); j++) {
            qemu_irq irq = qdev_get_gpio_in(s->gic,
                                            ppi_base + timer_irqs[j].ppi);

            qdev_connect_gpio_out(cpu, timer_irqs[j].output,
                                  irq);
        }
        qdev_connect_gpio_out_named(cpu, "gicv3-maintenance-interrupt", 0,
                                    qdev_get_gpio_in(s->gic, ppi_base +
                                                     TB371FC_PPI_MAINT));
        sysbus_connect_irq(busdev, i, qdev_get_gpio_in(cpu, ARM_CPU_IRQ));
        sysbus_connect_irq(busdev, i + TB371FC_CPUS,
                           qdev_get_gpio_in(cpu, ARM_CPU_FIQ));
        sysbus_connect_irq(busdev, i + 2 * TB371FC_CPUS,
                           qdev_get_gpio_in(cpu, ARM_CPU_VIRQ));
        sysbus_connect_irq(busdev, i + 3 * TB371FC_CPUS,
                           qdev_get_gpio_in(cpu, ARM_CPU_VFIQ));
    }
}

static void tb371fc_init(MachineState *ms)
{
    TB371FCMachineState *s = TB371FC_MACHINE(ms);
    MemoryRegion *sysmem = get_system_memory();
    SysBusDevice *uart;

    if (!tcg_enabled() && !qtest_enabled()) {
        error_report("tb371fc supports TCG execution or qtest device testing");
        exit(1);
    }
    if (ms->smp.cpus != TB371FC_CPUS || ms->smp.max_cpus != TB371FC_CPUS) {
        error_report("tb371fc requires its fixed eight-CPU profile (-smp 8)");
        exit(1);
    }
    if (strcmp(ms->cpu_type, ARM_CPU_TYPE_NAME("cortex-a55"))) {
        error_report("tb371fc uses fixed A55/A76 CPU groups; omit -cpu");
        exit(1);
    }
    if (ms->ram_size < 128 * MiB || ms->ram_size > TB371FC_RAM_MAX) {
        error_report("tb371fc RAM must be between 128 MiB and 8 GiB");
        exit(1);
    }
    if (ms->firmware) {
        error_report("tb371fc does not emulate PBL/XBL/ABL firmware boot; "
                     "use -kernel with a Linux Image or ELF payload");
        exit(1);
    }

    memory_region_add_subregion(sysmem, TB371FC_RAM_BASE, ms->ram);
    tb371fc_create_cpus(s, sysmem);
    tb371fc_create_gic(s);

    object_initialize_child(OBJECT(s), "uart", &s->uart, TYPE_QCOM_GENI_UART);
    qdev_prop_set_chr(DEVICE(&s->uart), "chardev", serial_hd(0));
    uart = SYS_BUS_DEVICE(&s->uart);
    sysbus_realize(uart, &error_fatal);
    sysbus_mmio_map(uart, 0, TB371FC_UART_BASE);
    sysbus_connect_irq(uart, 0, qdev_get_gpio_in(s->gic, TB371FC_UART_SPI));

    memory_region_init_io(&s->qup_version, OBJECT(s), &tb371fc_qup_ops, s,
                          "tb371fc.synthetic-qup-version", TB371FC_QUP_SIZE);
    memory_region_add_subregion(sysmem, TB371FC_QUP_BASE, &s->qup_version);

    s->bootinfo.ram_size = ms->ram_size;
    s->bootinfo.board_id = -1;
    s->bootinfo.loader_start = TB371FC_RAM_BASE;
    s->bootinfo.psci_conduit = QEMU_PSCI_CONDUIT_SMC;
    s->bootinfo.get_dtb = tb371fc_get_dtb;
    s->bootinfo.modify_dtb = tb371fc_fixup_memory;
    tb371fc_create_fdt(s);
    arm_load_kernel(s->cpu[0], ms, &s->bootinfo);
}

static void tb371fc_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "Experimental Lenovo TB371FC (SM8250 subset; A76 surrogate)";
    mc->init = tb371fc_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a55");
    mc->default_cpus = TB371FC_CPUS;
    mc->min_cpus = TB371FC_CPUS;
    mc->max_cpus = TB371FC_CPUS;
    mc->default_ram_size = TB371FC_RAM_MAX;
    mc->default_ram_id = "tb371fc.ram";
    mc->minimum_page_bits = 12;
    mc->no_cdrom = true;
    mc->no_floppy = true;
    mc->no_parallel = true;
    mc->possible_cpu_arch_ids = tb371fc_possible_cpu_arch_ids;
    mc->cpu_index_to_instance_props = tb371fc_cpu_index_to_props;
}

static const TypeInfo tb371fc_machine_type = {
    .name = TYPE_TB371FC_MACHINE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(TB371FCMachineState),
    .class_init = tb371fc_class_init,
    .interfaces = aarch64_machine_interfaces,
};

static void tb371fc_register_types(void)
{
    type_register_static(&tb371fc_machine_type);
}

type_init(tb371fc_register_types)
