/*
 * Experimental Lenovo TB371FC / Qualcomm SM8250 functional board model.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * This is a deliberately incomplete hardware model, not a virt-board alias.
 * The RAM aperture, MPIDRs, GIC, timer frequency/interrupts and active console
 * addresses come from an Android device-tree capture of a rooted TB371FC.
 *
 * CPU0..3 still use upstream Cortex-A55 as a Kryo surrogate. CPU4..7 use
 * the separately verified Cortex-A77 r1p0 model, with observed REVIDR 3.
 * Cache geometry and CPU timing are not tablet measurements. PSCI is QEMU's
 * SMC service, not Qualcomm secure firmware. GCC implements digital clock,
 * reset and power controls; its AHB parent is an explicit bootstrap input
 * until the RPMh/interconnect clock producers are implemented.
 *
 * The QUP wrapper's version register is a synthetic compatibility aid for the
 * upstream Linux GENI driver, not an emulation of the complete QUP wrapper.
 * Other wrapper register accesses return a bus error. All other absent SoC
 * peripherals remain unmapped.
 */

#include "qemu/osdep.h"
#include <libfdt.h>
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/units.h"
#include "qobject/qlist.h"
#include "qom/object.h"
#include "hw/arm/boot.h"
#include "hw/arm/arm-smmuv2.h"
#include "hw/arm/qcom-smmuv500.h"
#include "hw/arm/qcom-sm8250-tbu.h"
#include "hw/arm/machines-qom.h"
#include "hw/char/qcom-geni-uart.h"
#include "hw/core/boards.h"
#include "hw/core/clock.h"
#include "hw/core/irq.h"
#include "hw/core/split-irq.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/sysbus.h"
#include "hw/intc/arm_gicv3.h"
#include "hw/intc/arm_gicv3_common.h"
#include "hw/intc/qcom-pdc.h"
#include "hw/misc/qcom-sm8250-gcc.h"
#include "hw/misc/qcom-sm8250-geni-i2c.h"
#include "hw/misc/qcom-sm8250-gpucc.h"
#include "hw/misc/qcom-sm8250-npucc.h"
#include "hw/misc/qcom-sm8250-camcc.h"
#include "hw/misc/qcom-sm8250-dispcc.h"
#include "hw/misc/qcom-sm8250-videocc.h"
#include "hw/misc/qcom-sm8250-dsi-pll.h"
#include "hw/misc/qcom-sm8250-dp-pll.h"
#include "hw/misc/qcom-sm8250-llcc.h"
#include "hw/misc/qcom-noc-qos.h"
#include "hw/misc/qcom-pshold.h"
#include "hw/misc/qcom-dcc.h"
#include "hw/misc/arm-coresight-tmc.h"
#include "hw/misc/arm-coresight-replicator.h"
#include "hw/misc/qcom-ipcc.h"
#include "hw/misc/qcom-qsee-irq.h"
#include "hw/misc/qcom-sm8250-spss-irq-mask.h"
#include "hw/misc/qcom-sm8250-rpm-stats-offset.h"
#include "hw/misc/qcom-sm8250-ddr-stats-offset.h"
#include "hw/misc/qcom-sm8250-qce-bam.h"
#include "hw/misc/qcom-tcsr-mutex.h"
#include "hw/misc/qcom-tsens24xx.h"
#include "hw/misc/qcom-cpufreq-hw.h"
#include "hw/misc/qcom-devfreq-fw.h"
#include "hw/misc/qcom-noc-sbm.h"
#include "hw/misc/qcom-smem-handoff.h"
#include "hw/misc/qcom-aop-msgram.h"
#include "hw/misc/qcom-aop-qmp.h"
#include "hw/misc/qcom-refgen.h"
#include "hw/misc/qcom-gpu-cx-gdsc.h"
#include "hw/misc/qcom-gdsc.h"
#include "hw/misc/qcom-camera-gdsc.h"
#include "hw/misc/qcom-mdss-gdsc.h"
#include "hw/misc/qcom-npu-gdsc.h"
#include "hw/misc/qcom-video-gdsc.h"
#include "hw/misc/qcom-spmi-pmic-arb.h"
#include "hw/misc/qcom-sm8250-spmi-debug-fuse.h"
#include "hw/gpio/qcom-tb371fc-tlmm.h"
#include "hw/misc/qcom-sm8250-rpmh.h"
#include "hw/ufs/qcom-sm8250-ufs.h"
#include "hw/timer/arm-arch-timer-mmio.h"
#include "hw/timer/qcom-mpm2-sleep-counter.h"
#include "hw/watchdog/qcom-kpss.h"
#include "system/address-spaces.h"
#include "system/device_tree.h"
#include "system/qtest.h"
#include "system/system.h"
#include "system/tcg.h"
#include "target/arm/cpu.h"
#include "target/arm/gtimer.h"
#include "tb371fc-dsu-pmu.h"
#include "hw/misc/qcom-sm8250-prng.h"

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
#define TB371FC_GCC_BASE         UINT64_C(0x00100000)
#define TB371FC_PRNG_BASE        UINT64_C(0x00793000)
#define TB371FC_GCC_XO_HZ        19200000
#define TB371FC_SLEEP_HZ         32000
#define TB371FC_UFS_BASE         UINT64_C(0x01d84000)
#define TB371FC_UFS_PHY_BASE     UINT64_C(0x01d87000)
#define TB371FC_UFS_ICE_BASE     UINT64_C(0x01d90000)
#define TB371FC_UFS_ICE_SIZE     UINT64_C(0x8000)
#define TB371FC_UFS_SPI          265
#define TB371FC_WATCHDOG_BASE    UINT64_C(0x17c10000)
#define TB371FC_MMIO_TIMER_BASE  UINT64_C(0x17c20000)
#define TB371FC_APPS_SMMU_BASE   UINT64_C(0x15000000)
#define TB371FC_KGSL_SMMU_BASE   UINT64_C(0x03da0000)

/* Linux qcom,gcc-sm8250 binding IDs, not native rate measurements. */
#define TB371FC_GCC_UART_CLK     127
#define TB371FC_GCC_QUP_M_AHB    135
#define TB371FC_GCC_QUP_S_AHB    136
#define TB371FC_GCC_QUP_RESET    26

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

#define TB371FC_QUP_VERSION      0x20050000

#define TB371FC_MEMORY_NODE "/memory@80000000"
#define TB371FC_GCC_NODE "/soc/clock-controller@100000"
#define TB371FC_UFS_NODE "/soc/ufshc@1d84000"
#define TB371FC_UFS_PHY_NODE "/soc/phy@1d87000"
#define TB371FC_WATCHDOG_NODE "/soc/watchdog@17c10000"
#define TB371FC_MMIO_TIMER_NODE "/soc/timer@17c20000"
#define TB371FC_APPS_SMMU_NODE "/soc/iommu@15000000"
#define TB371FC_KGSL_SMMU_NODE "/soc/iommu@3da0000"
#define TB371FC_GIC_NODE "/soc/interrupt-controller@17a00000"
#define TB371FC_QUP_NODE "/soc/geniqup@ac0000"
#define TB371FC_UART_NODE TB371FC_QUP_NODE "/serial@a90000"

/* IRQ routing is captured in android-device-inventory.json, not sequential. */
static const unsigned tb371fc_apps_smmu_spis[] = {
    64, 65, 97, 98, 99, 100, 101, 102, 103, 104, 105, 106,
    107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118,
    181, 182, 183, 184, 185, 186, 187, 188, 189, 190, 191, 192,
    315, 316, 317, 318, 319, 320, 321, 322, 323, 324, 325, 326,
    327, 328, 329, 330, 331, 332, 333, 334, 335, 336, 337, 338,
    339, 340, 341, 342, 343, 344, 345, 395, 396, 397, 398, 399,
    400, 401, 402, 403, 404, 405, 406, 407, 408, 409, 412, 418,
    419, 421, 423, 424, 425, 690, 691, 692, 693, 694, 695, 696,
    697, 707,
};

static const unsigned tb371fc_kgsl_smmu_spis[] = {
    672, 673, 678, 679, 680, 681, 682, 683, 684, 685,
};

struct TB371FCMachineState {
    MachineState parent_obj;
    ARMCPU *cpu[TB371FC_CPUS];
    DeviceState *gic;
    DeviceState *dsu_pmu;
    DeviceState *prng;
    QcomGeniUartState uart;
    QcomGeniUartState uart_hs;
    DeviceState *geni_i2c[QCOM_GENI_I2C_INSTANCE_COUNT];
    bool geni_i2c_enabled;
    QcomSm8250GccState gcc;
    DeviceState *ufs;
    DeviceState *dsi_pll;
    DeviceState *dp_pll;
    DeviceState *llcc;
    DeviceState *apps_smmu, *kgsl_smmu, *mmio_timer, *rpmh, *watchdog;
    DeviceState *tlmm;
    DeviceState *pdc;
    MemoryRegion imem;
    Clock *board_xo;
    char *rpmh_profile, *rpmh_cmd_db;
    char *spmi_profile;
    bool spmi_debug_fuse_enabled;
    char *spmi_debug_fuse_word;
    char *smem_profile;
    char *aop_profile;
    char *tbu_profile;
    bool rpmh_boot_configured;
    MemoryRegion qtest_dma_alias;
    MemoryRegion qtest_tbu_dma_alias[QCOM_SM8250_TBU_COUNT];
    bool enable_ufs;
    bool counter_exact;
    bool ras_zero_records_compat;
    bool dsu_pmu_cycle_only;
    bool prng_virtual;
    uint64_t kernel_load_base;
    Clock *xo, *sleep, *fabric;
    bool gcc_boot_configured;
    uint64_t bootstrap_fabric_hz;
    MemoryRegion qup_version;
    MemoryRegion ufs_ice_probe;
    struct arm_boot_info bootinfo;
    int fdt_size;
};

static const char *tb371fc_cpu_type(unsigned int index)
{
    return index < 4 ? ARM_CPU_TYPE_NAME("cortex-a55") :
                       ARM_CPU_TYPE_NAME("cortex-a77");
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
 * Safe, immutable ICE probe profile, not an encryption engine. The native
 * DT establishes this window, but no native ICE register value was sampled.
 * VERSION major 255 is an unsupported sentinel, rejected by upstream and
 * downstream driver version checks. It does not identify the tablet's ICE.
 * FUSE and BIST cannot accidentally suggest usable keys or a successful BIST.
 * Keep UFS CAP.CS clear and reject encrypted descriptors in the UFS core.
 */
static MemTxResult tb371fc_ice_read(void *opaque, hwaddr offset,
                                  uint64_t *value, unsigned int size,
                                  MemTxAttrs attrs)
{
    *value = 0;
    if (size == 4 && !(offset & 3)) {
        switch (offset) {
        case 0x8: /* VERSION: unsupported reference major 255. */
            *value = 0xff000000;
            return MEMTX_OK;
        case 0x10: /* FUSE_SETTING: unavailable, not a native fuse reading. */
            *value = 1;
            return MEMTX_OK;
        case 0x70: /* BIST_STATUS: never claim a completed crypto self-test. */
            *value = 0xf0000000;
            return MEMTX_OK;
        }
    }
    qemu_log_mask(LOG_UNIMP, "tb371fc-ice: unsupported read offset=0x%"
                  HWADDR_PRIx " size=%u\n", offset, size);
    return MEMTX_ERROR;
}

static MemTxResult tb371fc_ice_write(void *opaque, hwaddr offset,
                                   uint64_t value, unsigned int size,
                                   MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR, "tb371fc-ice: read-only unsupported "
                  "crypto profile, write offset=0x%" HWADDR_PRIx " size=%u\n",
                  offset, size);
    return MEMTX_ERROR;
}

static const MemoryRegionOps tb371fc_ice_ops = {
    .read_with_attrs = tb371fc_ice_read,
    .write_with_attrs = tb371fc_ice_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
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
    TB371FCMachineState *s = container_of(info, TB371FCMachineState, bootinfo);
    uint64_t end = TB371FC_RAM_BASE + MACHINE(s)->ram_size;
    uint64_t low_size = MIN(end, TB371FC_RAM_HOLE_BASE) - TB371FC_RAM_BASE;
    const char *hole = "/reserved-memory/firmware-hole@bbb00000";
    int node;
    g_autoptr(GPtrArray) other_memory = g_ptr_array_new_with_free_func(g_free);

    if (qemu_fdt_getprop_cell(fdt, "/", "#address-cells", NULL,
                              &error_fatal) != 2 ||
        qemu_fdt_getprop_cell(fdt, "/", "#size-cells", NULL,
                              &error_fatal) != 2) {
        error_report("tb371fc requires a DT with two root address/size cells");
        exit(1);
    }

    /* boot.c synthesizes a bank at loader_start; retain one physical map. */
    fdt_for_each_subnode(node, fdt, 0) {
        int length;
        const char *type = fdt_getprop(fdt, node, "device_type", &length);
        const char *name = fdt_get_name(fdt, node, NULL);

        if (type && length == sizeof("memory") &&
            !memcmp(type, "memory", sizeof("memory")) &&
            strcmp(name, TB371FC_MEMORY_NODE + 1)) {
            g_ptr_array_add(other_memory, g_strdup_printf("/%s", name));
        }
    }
    for (unsigned i = 0; i < other_memory->len; i++) {
        qemu_fdt_nop_node(fdt, g_ptr_array_index(other_memory, i));
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

    if (!strcmp(s->smem_profile, "reference-empty")) {
        bool reserved = false;
        int count = fdt_num_mem_rsv(fdt);

        /* Preserve supplied DT content and add only the explicit reservation. */
        for (int i = 0; i < count; i++) {
            uint64_t address, size;

            if (!fdt_get_mem_rsv(fdt, i, &address, &size) &&
                address == QCOM_SMEM_BASE && size == QCOM_SMEM_SIZE) {
                reserved = true;
                break;
            }
        }
        if (!reserved) {
            int ret = fdt_add_mem_rsv(fdt, QCOM_SMEM_BASE, QCOM_SMEM_SIZE);

            if (ret < 0) {
                error_report("tb371fc: cannot reserve reference SMEM: %s",
                             fdt_strerror(ret));
                exit(1);
            }
        }
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


static void tb371fc_add_watchdog_fdt(TB371FCMachineState *s, void *fdt)
{
    uint32_t frequency = object_property_get_uint(OBJECT(s->watchdog),
                                                   "frequency", &error_fatal);
    uint32_t clock = tb371fc_add_fixed_clock(fdt, "/watchdog-clock", frequency);

    qemu_fdt_add_subnode(fdt, TB371FC_WATCHDOG_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_WATCHDOG_NODE, "compatible",
                            "qcom,kpss-wdt");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_WATCHDOG_NODE, "reg",
                                 2, TB371FC_WATCHDOG_BASE,
                                 2, QCOM_KPSS_WATCHDOG_SIZE);
    qemu_fdt_setprop_cells(fdt, TB371FC_WATCHDOG_NODE, "interrupts", 0, 0, 4);
    qemu_fdt_setprop_cell(fdt, TB371FC_WATCHDOG_NODE, "clocks", clock);
    qemu_fdt_setprop_string(fdt, TB371FC_WATCHDOG_NODE, "qemu,model-note",
                            "Vendor-driver reference frequency; native clock "
                            "rate unmeasured; bite uses QEMU watchdog action");
}

static void tb371fc_add_ufs_fdt(TB371FCMachineState *s, void *fdt,
                                 uint32_t gcc, uint32_t xo, uint32_t smmu,
                                 uint32_t rpmh, uint32_t tlmm)
{
    uint32_t phy = qemu_fdt_alloc_phandle(fdt);
    uint32_t ufs = qemu_fdt_alloc_phandle(fdt);
    const char compatible[] = "qcom,sm8250-ufshc\0qcom,ufshc\0jedec,ufs-2.0";
    const char names[] = "core_clk\0bus_aggr_clk\0iface_clk\0"
                         "core_clk_unipro\0ref_clk\0tx_lane0_sync_clk\0"
                         "rx_lane0_sync_clk\0rx_lane1_sync_clk";
    const char phy_names[] = "ref\0ref_aux\0qref";

    qemu_fdt_add_subnode(fdt, TB371FC_UFS_NODE);
    qemu_fdt_setprop(fdt, TB371FC_UFS_NODE, "compatible",
                     compatible, sizeof(compatible));
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_UFS_NODE, "reg",
                                 2, TB371FC_UFS_BASE,
                                 2, QCOM_SM8250_UFS_HCI_SIZE);
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "interrupts",
                           0, TB371FC_UFS_SPI, 4);
    qemu_fdt_setprop_cell(fdt, TB371FC_UFS_NODE, "phandle", ufs);
    qemu_fdt_setprop_cell(fdt, TB371FC_UFS_NODE, "#reset-cells", 1);
    qemu_fdt_setprop_cell(fdt, TB371FC_UFS_NODE, "lanes-per-direction", 2);
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "iommus",
                           smmu, 0xe0, smmu, 0x4e0);
    qemu_fdt_setprop_cell(fdt, TB371FC_UFS_NODE, "phys", phy);
    qemu_fdt_setprop_string(fdt, TB371FC_UFS_NODE, "phy-names", "ufsphy");
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "resets", gcc, 33);
    qemu_fdt_setprop_string(fdt, TB371FC_UFS_NODE, "reset-names", "rst");
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "power-domains", gcc, 4);
    qemu_fdt_setprop(fdt, TB371FC_UFS_NODE, "clock-names", names,
                     sizeof(names));
    if (rpmh) {
        qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "clocks",
                               gcc, 162, gcc, 6, gcc, 161, gcc, 171, rpmh, 0,
                               gcc, 170, gcc, 168, gcc, 169);
    } else {
        qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "clocks",
                               gcc, 162, gcc, 6, gcc, 161, gcc, 171, xo,
                               gcc, 170, gcc, 168, gcc, 169);
    }
    /* Upstream group 180 is the dedicated output-only UFS_RESET pad. */
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "reset-gpios", tlmm, 180, 1);
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_NODE, "freq-table-hz",
                           37500000, 300000000, 0, 0, 0, 0,
                           37500000, 300000000, 0, 0, 0, 0, 0, 0, 0, 0);
    qemu_fdt_setprop_string(fdt, TB371FC_UFS_NODE, "qemu,model-note",
                            "Digital UFS host/PHY profile; unknown native HCI "
                            "revision uses configurable reference default; "
                            "ICE probe reports unsupported; no inline "
                            "encryption, RPMB or analog calibration");

    qemu_fdt_add_subnode(fdt, TB371FC_UFS_PHY_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_UFS_PHY_NODE, "compatible",
                            "qcom,sm8250-qmp-ufs-phy");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_UFS_PHY_NODE, "reg",
                                 2, TB371FC_UFS_PHY_BASE,
                                 2, QCOM_SM8250_UFS_PHY_SIZE);
    qemu_fdt_setprop_cell(fdt, TB371FC_UFS_PHY_NODE, "phandle", phy);
    qemu_fdt_setprop_cell(fdt, TB371FC_UFS_PHY_NODE, "#phy-cells", 0);
    if (rpmh) {
        qemu_fdt_setprop_cells(fdt, TB371FC_UFS_PHY_NODE, "clocks",
                               rpmh, 0, gcc, 166, gcc, 148);
    } else {
        qemu_fdt_setprop_cells(fdt, TB371FC_UFS_PHY_NODE, "clocks",
                               xo, gcc, 166, gcc, 148);
    }
    qemu_fdt_setprop(fdt, TB371FC_UFS_PHY_NODE, "clock-names", phy_names,
                     sizeof(phy_names));
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_PHY_NODE, "resets", ufs, 0);
    qemu_fdt_setprop_string(fdt, TB371FC_UFS_PHY_NODE, "reset-names", "ufsphy");
    qemu_fdt_setprop_cells(fdt, TB371FC_UFS_PHY_NODE, "power-domains", gcc, 4);
}


static uint32_t tb371fc_add_smmu_fdt(void *fdt, const char *path,
                                    hwaddr base, hwaddr size,
                                    const unsigned *spis, unsigned count)
{
    const char compatible[] = "arm,smmu-v2";
    uint32_t phandle = qemu_fdt_alloc_phandle(fdt);
    g_autofree uint32_t *interrupts = g_new(uint32_t, count * 3);

    qemu_fdt_add_subnode(fdt, path);
    qemu_fdt_setprop(fdt, path, "compatible", compatible, sizeof(compatible));
    qemu_fdt_setprop_sized_cells(fdt, path, "reg", 2, base, 2, size);
    qemu_fdt_setprop_cell(fdt, path, "phandle", phandle);
    qemu_fdt_setprop_cell(fdt, path, "#iommu-cells", 1);
    qemu_fdt_setprop_cell(fdt, path, "#global-interrupts", 2);
    qemu_fdt_setprop(fdt, path, "dma-coherent", NULL, 0);
    for (unsigned i = 0; i < count; i++) {
        interrupts[i * 3] = cpu_to_be32(0);
        interrupts[i * 3 + 1] = cpu_to_be32(spis[i]);
        interrupts[i * 3 + 2] = cpu_to_be32(4);
    }
    qemu_fdt_setprop(fdt, path, "interrupts", interrupts,
                     count * 3 * sizeof(uint32_t));
    qemu_fdt_setprop_string(fdt, path, "qemu,model-note",
                            "Architectural SMMUv2 stage-1 AArch64 4K walks; "
                            "reference IDRs; apps TCU/TBU require explicit reference profile");
    return phandle;
}

static void tb371fc_add_mmio_timer_fdt(void *fdt)
{
    qemu_fdt_add_subnode(fdt, TB371FC_MMIO_TIMER_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_MMIO_TIMER_NODE, "compatible",
                            "arm,armv7-timer-mem");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_MMIO_TIMER_NODE, "reg",
                                 2, TB371FC_MMIO_TIMER_BASE, 2, 0x1000);
    qemu_fdt_setprop_cell(fdt, TB371FC_MMIO_TIMER_NODE, "clock-frequency",
                          TB371FC_TIMER_HZ);
    qemu_fdt_setprop_cell(fdt, TB371FC_MMIO_TIMER_NODE, "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, TB371FC_MMIO_TIMER_NODE, "#size-cells", 2);
    qemu_fdt_setprop(fdt, TB371FC_MMIO_TIMER_NODE, "ranges", NULL, 0);
    for (unsigned i = 0; i < 7; i++) {
        hwaddr base = TB371FC_MMIO_TIMER_BASE + 0x1000 + i * 0x2000;
        g_autofree char *path = g_strdup_printf(
            TB371FC_MMIO_TIMER_NODE "/frame@%" HWADDR_PRIx, base);

        qemu_fdt_add_subnode(fdt, path);
        qemu_fdt_setprop_cell(fdt, path, "frame-number", i);
        if (i == 0) {
            qemu_fdt_setprop_sized_cells(fdt, path, "reg",
                                         2, base, 2, 0x1000,
                                         2, base + 0x1000, 2, 0x1000);
            qemu_fdt_setprop_cells(fdt, path, "interrupts", 0, 8, 4, 0, 6, 4);
        } else {
            qemu_fdt_setprop_sized_cells(fdt, path, "reg", 2, base, 2, 0x1000);
            qemu_fdt_setprop_cells(fdt, path, "interrupts", 0, 8 + i, 4);
            qemu_fdt_setprop_string(fdt, path, "status", "disabled");
        }
    }
}


static uint32_t tb371fc_add_rpmh_fdt(TB371FCMachineState *s, void *fdt)
{
    const char *path = "/soc/rsc@18200000";
    const char *db = "/reserved-memory/command-db@80860000";
    const char *clocks = "/soc/rsc@18200000/clock-controller";
    uint32_t clock_phandle = qemu_fdt_alloc_phandle(fdt);
    uint32_t xo = tb371fc_add_fixed_clock(fdt, "/rpmh-xo", 38400000);
    const char names[] = "drv-0\0drv-1\0drv-2";

    qemu_fdt_add_path(fdt, "/reserved-memory");
    qemu_fdt_setprop_cell(fdt, "/reserved-memory", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/reserved-memory", "#size-cells", 2);
    qemu_fdt_setprop(fdt, "/reserved-memory", "ranges", NULL, 0);
    qemu_fdt_add_subnode(fdt, db);
    qemu_fdt_setprop_string(fdt, db, "compatible", "qcom,cmd-db");
    qemu_fdt_setprop_sized_cells(fdt, db, "reg",
                                 2, 0x80860000, 2, RPMH_CMD_DB_SIZE);
    qemu_fdt_setprop(fdt, db, "no-map", NULL, 0);
    qemu_fdt_setprop_string(fdt, db, "qemu,profile", s->rpmh_profile);

    qemu_fdt_add_subnode(fdt, path);
    qemu_fdt_setprop_string(fdt, path, "compatible", "qcom,rpmh-rsc");
    qemu_fdt_setprop_sized_cells(fdt, path, "reg",
                                 2, 0x18200000, 2, RPMH_DRV_SIZE,
                                 2, 0x18210000, 2, RPMH_DRV_SIZE,
                                 2, 0x18220000, 2, RPMH_DRV_SIZE);
    qemu_fdt_setprop(fdt, path, "reg-names", names, sizeof(names));
    qemu_fdt_setprop_cells(fdt, path, "interrupts",
                           0, 3, 4, 0, 4, 4, 0, 5, 4);
    qemu_fdt_setprop_cell(fdt, path, "qcom,drv-id", 2);
    qemu_fdt_setprop_cell(fdt, path, "qcom,tcs-offset", 0xd00);
    qemu_fdt_setprop_cells(fdt, path, "qcom,tcs-config",
                           2, 2, 0, 3, 1, 3, 3, 1);
    qemu_fdt_setprop_string(fdt, path, "qemu,model-note",
                            "Typed digital RSC resources; command-db profile "
                            "is explicit; AOP CPU/QMP and PDC are not modeled");
    qemu_fdt_add_subnode(fdt, clocks);
    qemu_fdt_setprop_string(fdt, clocks, "compatible", "qcom,sm8250-rpmh-clk");
    qemu_fdt_setprop_cell(fdt, clocks, "#clock-cells", 1);
    qemu_fdt_setprop_cell(fdt, clocks, "phandle", clock_phandle);
    qemu_fdt_setprop_cell(fdt, clocks, "clocks", xo);
    qemu_fdt_setprop_string(fdt, clocks, "clock-names", "xo");
    return clock_phandle;
}


static uint32_t tb371fc_add_tlmm_fdt(void *fdt)
{
    const char *path = "/soc/pinctrl@f100000";
    const char names[] = "west\0south\0north";
    uint32_t phandle = qemu_fdt_alloc_phandle(fdt);

    qemu_fdt_add_subnode(fdt, path);
    qemu_fdt_setprop_string(fdt, path, "compatible", "qcom,sm8250-pinctrl");
    qemu_fdt_setprop_sized_cells(fdt, path, "reg",
                                 2, 0x0f100000, 2, 0x300000,
                                 2, 0x0f500000, 2, 0x300000,
                                 2, 0x0f900000, 2, 0x300000);
    qemu_fdt_setprop(fdt, path, "reg-names", names, sizeof(names));
    qemu_fdt_setprop_cells(fdt, path, "interrupts", 0, 208, 4);
    qemu_fdt_setprop_cell(fdt, path, "phandle", phandle);
    qemu_fdt_setprop(fdt, path, "gpio-controller", NULL, 0);
    qemu_fdt_setprop_cell(fdt, path, "#gpio-cells", 2);
    qemu_fdt_setprop(fdt, path, "interrupt-controller", NULL, 0);
    qemu_fdt_setprop_cell(fdt, path, "#interrupt-cells", 2);
    qemu_fdt_setprop_cells(fdt, path, "gpio-ranges", phandle, 0, 0, 181);
    qemu_fdt_setprop_string(fdt, path, "qemu,model-note",
                            "180 GPIO pads plus upstream dedicated UFS_RESET "
                            "group 180; no PDC wakeup or alternate-function fabric");
    return phandle;
}


static void tb371fc_add_smem_fdt(TB371FCMachineState *s, void *fdt)
{
    const char *region = "/reserved-memory/smem_region@80900000";
    const char *syscon = "/soc/syscon@1f40000";
    const char *hwlock = "/soc/hwlock";
    const char *smem = "/soc/qcom,smem";
    uint32_t region_phandle, syscon_phandle, lock_phandle;

    if (strcmp(s->smem_profile, "reference-empty")) {
        return;
    }
    region_phandle = qemu_fdt_alloc_phandle(fdt);
    syscon_phandle = qemu_fdt_alloc_phandle(fdt);
    lock_phandle = qemu_fdt_alloc_phandle(fdt);
    qemu_fdt_add_path(fdt, "/reserved-memory");
    qemu_fdt_setprop_cell(fdt, "/reserved-memory", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/reserved-memory", "#size-cells", 2);
    qemu_fdt_setprop(fdt, "/reserved-memory", "ranges", NULL, 0);
    qemu_fdt_add_path(fdt, region);
    qemu_fdt_setprop_sized_cells(fdt, region, "reg",
                                2, QCOM_SMEM_BASE, 2, QCOM_SMEM_SIZE);
    qemu_fdt_setprop(fdt, region, "no-map", NULL, 0);
    qemu_fdt_setprop_cell(fdt, region, "phandle", region_phandle);
    qemu_fdt_add_path(fdt, syscon);
    qemu_fdt_setprop_string(fdt, syscon, "compatible", "syscon");
    qemu_fdt_setprop_sized_cells(fdt, syscon, "reg",
                                2, 0x01f40000, 2, 0x20000);
    qemu_fdt_setprop_cell(fdt, syscon, "phandle", syscon_phandle);
    qemu_fdt_add_path(fdt, hwlock);
    qemu_fdt_setprop_string(fdt, hwlock, "compatible", "qcom,tcsr-mutex");
    qemu_fdt_setprop_cell(fdt, hwlock, "#hwlock-cells", 1);
    qemu_fdt_setprop_cell(fdt, hwlock, "phandle", lock_phandle);
    qemu_fdt_setprop_cells(fdt, hwlock, "syscon", syscon_phandle, 0, 0x1000);
    qemu_fdt_add_path(fdt, smem);
    qemu_fdt_setprop_string(fdt, smem, "compatible", "qcom,smem");
    qemu_fdt_setprop_cell(fdt, smem, "memory-region", region_phandle);
    qemu_fdt_setprop_cells(fdt, smem, "hwlocks", lock_phandle, 3);
    qemu_fdt_setprop_string(fdt, smem, "qemu,model-note",
                            "Explicit empty SMEM v12 reference handoff; "
                            "no captured SBL, socinfo or remote services");
}

static void tb371fc_create_fdt(TB371FCMachineState *s)
{
    MachineState *ms = MACHINE(s);
    const char compatible[] = "qemu,tb371fc\0lenovo,tb371fc\0qcom,sm8250";
    const char qup_clock_names[] = "m-ahb\0s-ahb";
    const char gcc_clock_names[] = "bi_tcxo\0bi_tcxo_ao\0sleep_clk";
    uint32_t gic_phandle, gcc_phandle, xo_clock, sleep_clock, apps_smmu;
    uint32_t rpmh_clock = 0, tlmm;
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
                            "CPU0-3 Cortex-A55 Kryo surrogate; CPU4-7 "
                            "Cortex-A77 r1p0 REVIDR 3; reference cache "
                            "geometry; no timing fidelity");
    qemu_fdt_setprop_string(fdt, "/", "qemu,qup-model-note",
                            "GCC gates and reset connected to GENI UART; "
                            "synthetic QUP 2.5 version; FIFO only");
    qemu_fdt_setprop_string(fdt, "/", "qemu,clock-model-note",
                            "GCC XO 19.2 MHz from board XO divided by two; "
                            "AHB rate is configurable bootstrap fabric input, "
                            "not a measured tablet frequency");
    qemu_fdt_setprop_u64(fdt, "/", "qemu,bootstrap-fabric-hz",
                         s->bootstrap_fabric_hz);

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

    xo_clock = tb371fc_add_fixed_clock(fdt, "/gcc-bi-tcxo",
                                     TB371FC_GCC_XO_HZ);
    sleep_clock = tb371fc_add_fixed_clock(fdt, "/sleep-clock",
                                        TB371FC_SLEEP_HZ);

    qemu_fdt_add_subnode(fdt, "/soc");
    qemu_fdt_setprop_string(fdt, "/soc", "compatible", "simple-bus");
    qemu_fdt_setprop_cell(fdt, "/soc", "#address-cells", 2);
    qemu_fdt_setprop_cell(fdt, "/soc", "#size-cells", 2);
    qemu_fdt_setprop(fdt, "/soc", "ranges", NULL, 0);
    if (s->rpmh) {
        rpmh_clock = tb371fc_add_rpmh_fdt(s, fdt);
    }
    tlmm = tb371fc_add_tlmm_fdt(fdt);

    gcc_phandle = qemu_fdt_alloc_phandle(fdt);
    qemu_fdt_add_subnode(fdt, TB371FC_GCC_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_GCC_NODE, "compatible",
                            "qcom,gcc-sm8250");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_GCC_NODE, "reg",
                                 2, TB371FC_GCC_BASE,
                                 2, SM8250_GCC_MMIO_SIZE);
    qemu_fdt_setprop_cell(fdt, TB371FC_GCC_NODE, "#clock-cells", 1);
    qemu_fdt_setprop_cell(fdt, TB371FC_GCC_NODE, "#reset-cells", 1);
    qemu_fdt_setprop_cell(fdt, TB371FC_GCC_NODE, "#power-domain-cells", 1);
    qemu_fdt_setprop_cell(fdt, TB371FC_GCC_NODE, "phandle", gcc_phandle);
    qemu_fdt_setprop(fdt, TB371FC_GCC_NODE, "clock-names",
                     gcc_clock_names, sizeof(gcc_clock_names));
    if (rpmh_clock) {
        qemu_fdt_setprop_cells(fdt, TB371FC_GCC_NODE, "clocks",
                               rpmh_clock, 0, rpmh_clock, 1, sleep_clock);
    } else {
        qemu_fdt_setprop_cells(fdt, TB371FC_GCC_NODE, "clocks",
                               xo_clock, xo_clock, sleep_clock);
    }

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

    tb371fc_add_mmio_timer_fdt(fdt);
    tb371fc_add_watchdog_fdt(s, fdt);
    apps_smmu = tb371fc_add_smmu_fdt(fdt, TB371FC_APPS_SMMU_NODE,
        TB371FC_APPS_SMMU_BASE, 0x100000, tb371fc_apps_smmu_spis,
        ARRAY_SIZE(tb371fc_apps_smmu_spis));
    tb371fc_add_smmu_fdt(fdt, TB371FC_KGSL_SMMU_NODE,
        TB371FC_KGSL_SMMU_BASE, 0x10000, tb371fc_kgsl_smmu_spis,
        ARRAY_SIZE(tb371fc_kgsl_smmu_spis));

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
                           gcc_phandle, TB371FC_GCC_QUP_M_AHB,
                           gcc_phandle, TB371FC_GCC_QUP_S_AHB);

    qemu_fdt_add_subnode(fdt, TB371FC_UART_NODE);
    qemu_fdt_setprop_string(fdt, TB371FC_UART_NODE, "compatible",
                            "qcom,geni-debug-uart");
    qemu_fdt_setprop_sized_cells(fdt, TB371FC_UART_NODE, "reg",
                                 2, TB371FC_UART_BASE, 2, TB371FC_UART_SIZE);
    qemu_fdt_setprop_cells(fdt, TB371FC_UART_NODE, "interrupts",
                           0, TB371FC_UART_SPI, 4);
    qemu_fdt_setprop_string(fdt, TB371FC_UART_NODE, "clock-names", "se");
    qemu_fdt_setprop_cells(fdt, TB371FC_UART_NODE, "clocks",
                           gcc_phandle, TB371FC_GCC_UART_CLK);
    qemu_fdt_setprop_cell(fdt, TB371FC_UART_NODE, "current-speed", 115200);
    qemu_fdt_setprop_string(fdt, TB371FC_UART_NODE, "status", "okay");

    if (s->ufs) {
        tb371fc_add_ufs_fdt(s, fdt, gcc_phandle, xo_clock, apps_smmu,
                            rpmh_clock, tlmm);
    }

    tb371fc_add_smem_fdt(s, fdt);
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

static void tb371fc_create_dsu_pmu(TB371FCMachineState *s)
{
    /* Always present so migration can reject enabled/disabled mismatches. */
    s->dsu_pmu = qdev_new(TYPE_TB371FC_DSU_PMU);
    object_property_add_child(OBJECT(s), "dsu-pmu", OBJECT(s->dsu_pmu));
    qdev_prop_set_bit(s->dsu_pmu, "enabled", s->dsu_pmu_cycle_only);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(s->dsu_pmu), &error_fatal);
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
        object_property_set_bool(obj, "cntfrq-exact", s->counter_exact,
                                 &error_abort);
        object_property_set_bool(obj, "tb371fc-ras-zero-records-compat",
                                 s->ras_zero_records_compat, &error_abort);
        if (i >= 4) {
            object_property_set_int(obj, "revidr", 3, &error_abort);
        }
        object_property_set_bool(obj, "has_el3", false, &error_abort);
        object_property_set_bool(obj, "has_el2", true, &error_abort);
        object_property_set_bool(obj, "start-powered-off", i != 0,
                                 &error_abort);
        object_property_set_int(obj, "psci-conduit", QEMU_PSCI_CONDUIT_SMC,
                                &error_abort);
        object_property_set_link(obj, "memory", OBJECT(sysmem), &error_abort);
        qdev_realize(DEVICE(obj), NULL, &error_fatal);
        tb371fc_dsu_pmu_attach_cpu(s->dsu_pmu, s->cpu[i]);
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


static void tb371fc_create_rpmh(TB371FCMachineState *s)
{
    DeviceState *dev;

    if (!strcmp(s->rpmh_profile, "off")) {
        if (s->rpmh_cmd_db && *s->rpmh_cmd_db) {
            error_report("tb371fc: rpmh-cmd-db requires rpmh-profile=file");
            exit(1);
        }
        return;
    }
    if ((!strcmp(s->rpmh_profile, "file") &&
         (!s->rpmh_cmd_db || !*s->rpmh_cmd_db)) ||
        (!strcmp(s->rpmh_profile, "reference") &&
         s->rpmh_cmd_db && *s->rpmh_cmd_db)) {
        error_report("tb371fc: select either an explicit reference RPMh profile "
                     "or file profile with rpmh-cmd-db");
        exit(1);
    }
    dev = qdev_new(TYPE_QCOM_SM8250_RPMH);
    s->rpmh = dev;
    object_property_add_child(OBJECT(s), "rpmh", OBJECT(dev));
    qdev_prop_set_bit(dev, "reference-cmd-db",
                      !strcmp(s->rpmh_profile, "reference"));
    qdev_prop_set_bit(dev, "boot-configured", s->rpmh_boot_configured);
    if (s->rpmh_cmd_db && *s->rpmh_cmd_db) {
        qdev_prop_set_string(dev, "cmd-db-file", s->rpmh_cmd_db);
    }
    s->board_xo = clock_new(OBJECT(s), "xo-board");
    clock_set_hz(s->board_xo, 38400000);
    qdev_connect_clock_in(dev, "xo-board", s->board_xo);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < RPMH_DRVS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), i, 0x18200000 + i * RPMH_DRV_SIZE);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), i,
                           qdev_get_gpio_in(s->gic, 3 + i));
    }
    /* A read-only firmware-owned region overlays the reserved physical RAM. */
    sysbus_mmio_map_overlap(SYS_BUS_DEVICE(dev), 3, 0x80860000, 1);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), RPMH_DISPLAY_MMIO, 0x0af20000);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), RPMH_DISPLAY_DRV,
                       qdev_get_gpio_in(s->gic, 129));
    clock_set_source(s->xo, qdev_get_clock_out(dev, "bi_tcxo"));
}

static void tb371fc_create_gpu_cx_gdsc(TB371FCMachineState *s)
{
    DeviceState *gdsc = qdev_new(TYPE_QCOM_GPU_CX_GDSC);

    object_property_add_child(OBJECT(s), "gpu-cx-gdsc", OBJECT(gdsc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gdsc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), 0, 0x03d9106c);
    sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), 1, 0x03d91540);
    if (s->rpmh) {
        /* Resource power[1] is cx.lvl; this is digital ARC availability. */
        qdev_connect_gpio_out_named(s->rpmh, "power", 1,
            qdev_get_gpio_in_named(gdsc, "parent-on", 0));
        /* RPMh res.* QOM properties are uint64, including 0/1 states. */
        qemu_set_irq(qdev_get_gpio_in_named(gdsc, "parent-on", 0),
            object_property_get_uint(OBJECT(s->rpmh), "res.cx.lvl.enabled",
                                     &error_fatal) != 0);
        qemu_set_irq(qdev_get_gpio_in_named(gdsc, "parent-known", 0),
            object_property_get_uint(OBJECT(s->rpmh), "res.cx.lvl.present",
                                     &error_fatal) != 0);
    }
}

static void tb371fc_create_gpu_gx_gdsc(TB371FCMachineState *s)
{
    DeviceState *gdsc = qdev_new(TYPE_QCOM_GDSC);

    /* Only the captured GX windows; CX keeps its independent timed model. */
    object_property_add_child(OBJECT(s), "gpu-gx-gdsc", OBJECT(gdsc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gdsc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), QCOM_GDSC_CONTROL, 0x03d9100c);
    sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), QCOM_GDSC_DOMAIN_CONTROL, 0x03d91508);
    sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), QCOM_GDSC_SW_RESET, 0x03d91008);
    if (s->rpmh) {
        /* Kona GX parent is VDD_GFX_LEVEL, not CX. res.* has uint64 type. */
        qdev_connect_gpio_out_named(s->rpmh, "power", 4,
            qdev_get_gpio_in_named(gdsc, "parent-on", 0));
        qemu_set_irq(qdev_get_gpio_in_named(gdsc, "parent-on", 0),
            object_property_get_uint(OBJECT(s->rpmh), "res.gfx.lvl.enabled",
                                     &error_fatal) != 0);
        qemu_set_irq(qdev_get_gpio_in_named(gdsc, "parent-known", 0),
            object_property_get_uint(OBJECT(s->rpmh), "res.gfx.lvl.present",
                                     &error_fatal) != 0);
    }
}


static DeviceState *tb371fc_create_camera_gdsc(TB371FCMachineState *s)
{
    DeviceState *gdsc = qdev_new(TYPE_QCOM_CAMERA_GDSC);

    object_property_add_child(OBJECT(s), "camera-gdsc", OBJECT(gdsc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gdsc), &error_fatal);
    for (unsigned i = 0; i < QCOM_CAMERA_GDSC_DOMAINS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), i,
                       qcom_camera_gdsc_domains[i].address);
    }
    return gdsc;
}

static DeviceState *tb371fc_create_video_gdsc(TB371FCMachineState *s)
{
    DeviceState *gdsc = qdev_new(TYPE_QCOM_VIDEO_GDSC);

    object_property_add_child(OBJECT(s), "video-gdsc", OBJECT(gdsc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gdsc), &error_fatal);
    for (unsigned i = 0; i < QCOM_VIDEO_GDSC_DOMAINS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), i,
                       qcom_video_gdsc_domains[i].address);
    }
    return gdsc;
}

static DeviceState *tb371fc_create_mdss_and_video_gdsc(TB371FCMachineState *s,
                                                      DeviceState *camera)
{
    DeviceState *mdss = qdev_new(TYPE_QCOM_MDSS_GDSC);
    DeviceState *video;

    object_property_add_child(OBJECT(s), "mdss-gdsc", OBJECT(mdss));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(mdss), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(mdss), 0,
                   qcom_mdss_gdsc_domains[0].address);
    video = tb371fc_create_video_gdsc(s);
    if (s->rpmh) {
        DeviceState *splitter = qdev_new(TYPE_SPLIT_IRQ);
        bool present, enabled;

        /*
         * Camera, MDSS and VIDEO are siblings on DT phandle 104 (mmcx.lvl).
         * There is one RPMh output owner; never replace the camera wire with
         * a second qdev_connect_gpio_out call. The splitter has no VMState.
         * MDSS's self proxy is a software consumer, not a power-parent edge.
         */
        object_property_add_child(OBJECT(s), "mmcx-gdsc-fanout",
                                  OBJECT(splitter));
        qdev_prop_set_uint16(splitter, "num-lines", 3);
        qdev_realize_and_unref(splitter, NULL, &error_fatal);
        qdev_connect_gpio_out(splitter, 0,
            qdev_get_gpio_in_named(camera, "parent-on", 0));
        qdev_connect_gpio_out(splitter, 1,
            qdev_get_gpio_in_named(mdss, "parent-on", 0));
        qdev_connect_gpio_out(splitter, 2,
            qdev_get_gpio_in_named(video, "parent-on", 0));
        qdev_connect_gpio_out_named(s->rpmh, "power", 3,
            qdev_get_gpio_in(splitter, 0));
        enabled = object_property_get_uint(OBJECT(s->rpmh),
                                           "res.mmcx.lvl.enabled",
                                           &error_fatal) != 0;
        present = object_property_get_uint(OBJECT(s->rpmh),
                                           "res.mmcx.lvl.present",
                                           &error_fatal) != 0;
        qemu_set_irq(qdev_get_gpio_in(splitter, 0), enabled);
        qemu_set_irq(qdev_get_gpio_in_named(camera, "parent-known", 0),
                     present);
        qemu_set_irq(qdev_get_gpio_in_named(mdss, "parent-known", 0), present);
        qemu_set_irq(qdev_get_gpio_in_named(video, "parent-known", 0), present);
    }
    return mdss;
}

static void tb371fc_connect_display_pll_power(TB371FCMachineState *s,
                                              DeviceState *mdss)
{
    DeviceState *consumers[] = { s->dsi_pll, s->dp_pll };
    static const char *const fanout_names[] = {
        "mdss-pll-known-fanout", "mdss-pll-power-fanout",
    };
    static const char *const input_names[] = { "power-known", "power-on" };
    static const char *const output_names[] = { "state-known", "power" };
    qemu_irq inputs[2];
    bool state_known = object_property_get_bool(OBJECT(mdss),
                                               "domain0.state-known",
                                               &error_fatal);
    bool state_on = object_property_get_bool(OBJECT(mdss),
                                            "domain0.powered", &error_fatal);

    /*
     * DSI and DP share the existing MDSS owner. Each supplier output has one
     * stateless fanout; attaching DP must not replace the DSI connection.
     */
    for (unsigned signal = 0; signal < ARRAY_SIZE(inputs); signal++) {
        DeviceState *splitter = qdev_new(TYPE_SPLIT_IRQ);

        object_property_add_child(OBJECT(s), fanout_names[signal],
                                  OBJECT(splitter));
        qdev_prop_set_uint16(splitter, "num-lines", ARRAY_SIZE(consumers));
        qdev_realize_and_unref(splitter, NULL, &error_fatal);
        for (unsigned i = 0; i < ARRAY_SIZE(consumers); i++) {
            qdev_connect_gpio_out(splitter, i,
                qdev_get_gpio_in_named(consumers[i], input_names[signal], 0));
        }
        inputs[signal] = qdev_get_gpio_in(splitter, 0);
        qdev_connect_gpio_out_named(mdss, output_names[signal], 0,
                                    inputs[signal]);
    }
    /* Seed from pure supplier observations in mdss_publish() order. */
    if (state_known) {
        qemu_set_irq(inputs[1], state_on);
        qemu_set_irq(inputs[0], 1);
    } else {
        qemu_set_irq(inputs[0], 0);
        qemu_set_irq(inputs[1], 0);
    }
}

static void tb371fc_create_npu_gdsc(TB371FCMachineState *s)
{
    DeviceState *gdsc = qdev_new(TYPE_QCOM_NPU_GDSC);

    object_property_add_child(OBJECT(s), "npu-gdsc", OBJECT(gdsc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gdsc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gdsc), 0,
                   qcom_npu_gdsc_domains[0].address);
    /*
     * The actual NPU GDSC node declares no parent supply. Keep both parent
     * inputs unknown; neither mmcx, NPUCC nor a clock vote supplies evidence
     * that this rail is available. The collapsed reference still reads OFF.
     */
}

static void tb371fc_create_cpufreq(TB371FCMachineState *s)
{
    for (unsigned i = 0; i < QCOM_CPUFREQ_HW_DOMAINS; i++) {
        const QcomCpufreqHwDomain *d = &qcom_cpufreq_hw_domains[i];
        DeviceState *dev = qdev_new(TYPE_QCOM_CPUFREQ_HW_EPSS);
        g_autofree char *name = g_strdup_printf("cpufreq%u", i);

        object_property_add_child(OBJECT(s), name, OBJECT(dev));
        qdev_prop_set_uint32(dev, "domain-id", i);
        /*
         * Actual DT: RPMh CXO and GCC GPLL0 (vendor binding ID 209).
         * The explicit XO01 reference LUT uses only XO; alternate remains
         * a real connected input, not a fabricated fixed 600 MHz value.
         */
        qdev_connect_clock_in(dev, "xo", s->xo);
        qdev_connect_clock_in(dev, "alternate",
                              qdev_get_clock_out(DEVICE(&s->gcc), "gpll0"));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, d->base);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0,
                           qdev_get_gpio_in(s->gic, d->spi));
    }
}

static void tb371fc_create_devfreq(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_DEVFREQ_FW);
    unsigned i;

    object_property_add_child(OBJECT(s), "devfreq-l3", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (i = 0; i < QCOM_DEVFREQ_FW_WINDOWS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), i,
                       qcom_devfreq_fw_regions[i].physical_base);
    }
}

static void tb371fc_create_sbm(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_NOC_SBM);
    unsigned i;

    object_property_add_child(OBJECT(s), "noc-sbm-config", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (i = 0; i < QCOM_NOC_SBM_WINDOWS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), i,
                       qcom_noc_sbm_regions[i].physical_base);
    }
}

static void tb371fc_create_gpucc(TB371FCMachineState *s)
{
    DeviceState *gpucc = qdev_new(TYPE_QCOM_SM8250_GPUCC);
    unsigned i;

    object_property_add_child(OBJECT(s), "gpucc", OBJECT(gpucc));
    qdev_connect_clock_in(gpucc, "xo", s->xo);
    qdev_connect_clock_in(gpucc, "gcc_gpu_gpll0_clk_src",
        qdev_get_clock_out(DEVICE(&s->gcc), "gcc_gpu_gpll0_clk_src"));
    qdev_connect_clock_in(gpucc, "gcc_gpu_gpll0_div_clk_src",
        qdev_get_clock_out(DEVICE(&s->gcc), "gcc_gpu_gpll0_div_clk_src"));
    /*
     * PLL0, QDSS and branch parents not specified by the source remain
     * unwired. An AOP QDSS vote is not a clock frequency. The opt-in
     * enabled-reference property does not seed any PLL or clock vote.
     */
    sysbus_realize_and_unref(SYS_BUS_DEVICE(gpucc), &error_fatal);
    for (i = 0; i < GPUCC_WINDOWS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(gpucc), i,
                        0x03d90000 + qcom_sm8250_gpucc_window_offsets[i]);
    }
}

static void tb371fc_create_npucc(TB371FCMachineState *s)
{
    static const hwaddr bases[] = { 0x09980000, 0x09800000, 0x09810000 };
    DeviceState *npucc = qdev_new(TYPE_QCOM_SM8250_NPUCC);
    unsigned i;

    object_property_add_child(OBJECT(s), "npucc", OBJECT(npucc));
    /*
     * Both RPMh XO names use the same physical active carrier in the
     * vendor driver. Separate logical SLEEP votes are not modeled here.
     */
    qdev_connect_clock_in(npucc, "bi_tcxo", s->xo);
    qdev_connect_clock_in(npucc, "bi_tcxo_ao", s->xo);
    qdev_connect_clock_in(npucc, "gcc_npu_gpll0_clk_src",
        qdev_get_clock_out(DEVICE(&s->gcc), "gcc_npu_gpll0_clk_src"));
    qdev_connect_clock_in(npucc, "gcc_npu_gpll0_div_clk_src",
        qdev_get_clock_out(DEVICE(&s->gcc), "gcc_npu_gpll0_div_clk_src"));
    /*
     * Test, QDSS, NOC, S2P and LLM parents remain unknown until a real
     * provider is connected. No boot rates or remote votes are seeded.
     */
    sysbus_realize_and_unref(SYS_BUS_DEVICE(npucc), &error_fatal);
    for (i = 0; i < NPUCC_WINDOWS; i++) {
        const QcomNpuccWindowDef *w = &qcom_sm8250_npucc_windows[i];

        sysbus_mmio_map(SYS_BUS_DEVICE(npucc), i, bases[w->bank] + w->offset);
    }
    /* cc + 0x1004 (the separate NPU GDSC) is intentionally not mapped. */
}

static void tb371fc_create_camcc(TB371FCMachineState *s)
{
    DeviceState *camcc = qdev_new(TYPE_QCOM_SM8250_CAMCC);

    object_property_add_child(OBJECT(s), "camcc", OBJECT(camcc));
    /*
     * Fixed clk-rpmh.c names both bi_tcxo carriers as xo.lvl from xo_board.
     * Test, sleep and the unregistered PLL2 aux2 parent remain unknown.
     */
    qdev_connect_clock_in(camcc, "bi_tcxo", s->xo);
    qdev_connect_clock_in(camcc, "bi_tcxo_ao", s->xo);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(camcc), &error_fatal);
    /* Transparent container: only its 74 exact leaves respond to accesses. */
    sysbus_mmio_map(SYS_BUS_DEVICE(camcc), 0, CAMCC_PHYSICAL_BASE);
}

static void tb371fc_create_dsi_pll(TB371FCMachineState *s)
{
    DeviceState *pll = qdev_new(TYPE_QCOM_SM8250_DSI_PLL);

    s->dsi_pll = pll;
    object_property_add_child(OBJECT(s), "dsi-pll", OBJECT(pll));
    qdev_connect_clock_in(pll, "bi_tcxo", s->xo);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(pll), &error_fatal);
    for (unsigned bank = 0; bank < DSI_PLL_BANKS; bank++) {
        /* Each PHY container has only source-bound sparse PLL/PHY leaves. */
        sysbus_mmio_map(SYS_BUS_DEVICE(pll), bank,
                       DSI_PLL_PHYSICAL_BASE + bank * DSI_PLL_BANK_STRIDE);
    }
}

static void tb371fc_create_dp_pll(TB371FCMachineState *s, DeviceState *mdss)
{
    DeviceState *pll = qdev_new(TYPE_QCOM_SM8250_DP_PLL);

    s->dp_pll = pll;
    object_property_add_child(OBJECT(s), "dp-pll", OBJECT(pll));
    object_property_set_link(OBJECT(pll), "mdss-provider", OBJECT(mdss),
                              &error_fatal);
    /* The actual non-V2 PLL DT ref_clk_src is the existing RPMh XO carrier. */
    qdev_connect_clock_in(pll, "ref_clk_src", s->xo);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(pll), &error_fatal);
    for (unsigned region = 0; region < DP_PLL_REGIONS; region++) {
        /* 42 exact leaves; shared SS PHY holes and the MDSS owner stay open. */
        sysbus_mmio_map(SYS_BUS_DEVICE(pll), region,
                       qcom_sm8250_dp_pll_bases[region]);
    }
}

static void tb371fc_create_llcc(TB371FCMachineState *s)
{
    DeviceState *llcc = qdev_new(TYPE_QCOM_SM8250_LLCC);

    s->llcc = llcc;
    object_property_add_child(OBJECT(s), "llcc", OBJECT(llcc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(llcc), &error_fatal);
    for (unsigned region = 0; region < LLCC_REGIONS; region++) {
        /* Exact bank/broadcast leaves; the separate LLCC PMU stays unmapped. */
        sysbus_mmio_map(SYS_BUS_DEVICE(llcc), region,
                       qcom_sm8250_llcc_bases[region]);
    }
}

static void tb371fc_create_dispcc(TB371FCMachineState *s)
{
    DeviceState *dispcc = qdev_new(TYPE_QCOM_SM8250_DISPCC);
    static const char *const dsi_clocks[] = {
        "dsi0_phy_pll_out_byteclk", "dsi1_phy_pll_out_byteclk",
        "dsi0_phy_pll_out_dsiclk", "dsi1_phy_pll_out_dsiclk",
    };
    static const char *const dp_clocks[] = {
        "dp_link_clk_divsel_ten", "dp_vco_divided_clk_src_mux",
    };

    object_property_add_child(OBJECT(s), "dispcc", OBJECT(dispcc));
    /*
     * Fixed clk-rpmh.c proves bi_tcxo. Enabled DSI and non-V2 DP references
     * supply their same-name physical carriers before DISPCC is realized.
     */
    qdev_connect_clock_in(dispcc, "bi_tcxo", s->xo);
    if (object_property_get_bool(OBJECT(s->dsi_pll), "enabled-reference",
                                 &error_fatal)) {
        for (unsigned i = 0; i < ARRAY_SIZE(dsi_clocks); i++) {
            qdev_connect_clock_in(dispcc, dsi_clocks[i],
                qdev_get_clock_out(s->dsi_pll, dsi_clocks[i]));
        }
    }
    if (object_property_get_bool(OBJECT(s->dp_pll), "enabled-reference",
                                 &error_fatal)) {
        for (unsigned i = 0; i < ARRAY_SIZE(dp_clocks); i++) {
            qdev_connect_clock_in(dispcc, dp_clocks[i],
                qdev_get_clock_out(s->dp_pll, dp_clocks[i]));
        }
    }
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dispcc), &error_fatal);
    /* 39 exact leaves; the existing MDSS GDSCR at +0x3000 remains separate. */
    sysbus_mmio_map(SYS_BUS_DEVICE(dispcc), 0, DISPCC_PHYSICAL_BASE);
}

static void tb371fc_create_videocc(TB371FCMachineState *s)
{
    DeviceState *videocc = qdev_new(TYPE_QCOM_SM8250_VIDEOCC);

    object_property_add_child(OBJECT(s), "videocc", OBJECT(videocc));
    /* Both source-declared XO names use the established xo.lvl carrier. */
    qdev_connect_clock_in(videocc, "bi_tcxo", s->xo);
    qdev_connect_clock_in(videocc, "bi_tcxo_ao", s->xo);
    /* The sleep and test parents remain unknown until providers exist. */
    sysbus_realize_and_unref(SYS_BUS_DEVICE(videocc), &error_fatal);
    /* 26 exact leaves preserve all four independent VIDEO GDSC owners. */
    sysbus_mmio_map(SYS_BUS_DEVICE(videocc), 0, VIDEOCC_PHYSICAL_BASE);
}

static void tb371fc_create_noc_qos(TB371FCMachineState *s)
{
    /*
     * Original DT SHA256 5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207.
     * Physical AP-owned QoS ports only: display aliases name existing ports.
     * base + base-offset + port * qos-off is the vendor callback's geometry.
     */
    static const struct {
        const char *name;
        hwaddr base;
        uint32_t offset, stride, count;
        uint32_t ports[13];
    } fabrics[] = {
        { "aggre1", 0x016e0000, 0x2000, 0x1000, 9,
          { 0, 1, 2, 3, 4, 5, 6, 7, 8 } },
        { "aggre2", 0x01700000, 0x3000, 0x1000, 10,
          { 0, 1, 2, 3, 4, 7, 8, 9, 11, 12 } },
        { "compute", 0x01700000, 0x33000, 0x800, 2,
          { 6, 7 } },
        { "gem", 0x09100000, 0x21000, 0x1000, 13,
          { 0, 1, 2, 3, 64, 65, 66, 67, 127, 128, 129, 130, 131 } },
        { "mmss", 0x01740000, 0xa000, 0x800, 11,
          { 0, 1, 2, 4, 5, 6, 8, 10, 12, 13, 14 } },
        { "system", 0x01620000, 0x12000, 0x1000, 2,
          { 0, 1 } },
    };
    unsigned f, p, r;

    for (f = 0; f < ARRAY_SIZE(fabrics); f++) {
        for (p = 0; p < fabrics[f].count; p++) {
            uint32_t port = fabrics[f].ports[p];
            hwaddr address = fabrics[f].base + fabrics[f].offset +
                             port * fabrics[f].stride;
            DeviceState *dev = qdev_new(TYPE_QCOM_NOC_QOS);
            g_autofree char *name = g_strdup_printf("noc-qos-%s-%u",
                                                     fabrics[f].name, port);

            QCOM_NOC_QOS(dev)->port_address = address;
            object_property_add_child(OBJECT(s), name, OBJECT(dev));
            sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
            for (r = 0; r < QCOM_NOC_QOS_REGISTERS; r++) {
                sysbus_mmio_map(SYS_BUS_DEVICE(dev), r,
                                address + qcom_noc_qos_offsets[r]);
            }
        }
    }
    /*
     * No fabric container, legacy QoS or SBM windows are mapped. The
     * reference profile neither seeds DT policy nor supplies traffic/ACKs.
     */
}


static void tb371fc_prepare_geni_i2c(TB371FCMachineState *s)
{
    for (unsigned i = 0; i < qcom_geni_i2c_instance_count(); i++) {
        const QcomGeniI2CInstance *inst = qcom_geni_i2c_instance_at(i);
        DeviceState *dev = qdev_new(TYPE_QCOM_SM8250_GENI_I2C);
        g_autofree char *name = g_strdup_printf("geni-i2c-se%u", inst->id);

        s->geni_i2c[i] = dev;
        object_property_add_child(OBJECT(s), name, OBJECT(dev));
        dev->id = g_strdup(name);
        qdev_prop_set_uint32(dev, "instance-id", inst->id);
        qdev_prop_set_bit(dev, "enabled-reference", s->geni_i2c_enabled);
    }
}

/*
 * Each shared wrapper reset has one output owner. When the I2C profile is
 * off, keep the original legacy destination and create no new fanout.
 * The stateless splitters are the same core device already used above.
 */
static qemu_irq tb371fc_geni_reset_sink(TB371FCMachineState *s,
                                       unsigned wrapper,
                                       DeviceState *legacy)
{
    DeviceState *splitter;
    g_autofree char *name = NULL;
    unsigned count = legacy ? 1 : 0;
    unsigned output = 0;

    if (!s->geni_i2c_enabled) {
        return legacy ? qdev_get_gpio_in_named(legacy, "reset", 0) : NULL;
    }
    for (unsigned i = 0; i < qcom_geni_i2c_instance_count(); i++) {
        count += qcom_geni_i2c_instance_at(i)->wrapper == wrapper;
    }
    assert(count > 0);
    splitter = qdev_new(TYPE_SPLIT_IRQ);
    name = g_strdup_printf("geni-i2c-wrap%u-reset-fanout", wrapper);
    object_property_add_child(OBJECT(s), name, OBJECT(splitter));
    qdev_prop_set_uint16(splitter, "num-lines", count);
    qdev_realize_and_unref(splitter, NULL, &error_fatal);
    if (legacy) {
        qdev_connect_gpio_out(splitter, output++,
                              qdev_get_gpio_in_named(legacy, "reset", 0));
    }
    for (unsigned i = 0; i < qcom_geni_i2c_instance_count(); i++) {
        if (qcom_geni_i2c_instance_at(i)->wrapper == wrapper) {
            qdev_connect_gpio_out(splitter, output++,
                qdev_get_gpio_in_named(s->geni_i2c[i], "reset", 0));
        }
    }
    assert(output == count);
    return qdev_get_gpio_in(splitter, 0);
}

static void tb371fc_realize_geni_i2c(TB371FCMachineState *s)
{
    for (unsigned i = 0; i < qcom_geni_i2c_instance_count(); i++) {
        const QcomGeniI2CInstance *inst = qcom_geni_i2c_instance_at(i);
        DeviceState *dev = s->geni_i2c[i];

        qdev_connect_clock_in(dev, "se-clk",
            qdev_get_clock_out(DEVICE(&s->gcc), inst->se_clock));
        qdev_connect_clock_in(dev, "m-ahb",
            qdev_get_clock_out(DEVICE(&s->gcc), inst->m_ahb_clock));
        qdev_connect_clock_in(dev, "s-ahb",
            qdev_get_clock_out(DEVICE(&s->gcc), inst->s_ahb_clock));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, inst->base);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0,
                            qdev_get_gpio_in(s->gic, inst->spi));
    }
    if (s->geni_i2c_enabled) {
        /* Wrapper 0 and 2 also retain HS and console UART, respectively. */
        qdev_connect_gpio_out_named(DEVICE(&s->gcc), "reset", 25,
                                    tb371fc_geni_reset_sink(s, 1, NULL));
    }
}

static void tb371fc_create_prng(TB371FCMachineState *s)
{
    s->prng = qdev_new(TYPE_QCOM_SM8250_PRNG);
    object_property_add_child(OBJECT(s), "prng", OBJECT(s->prng));
    qdev_prop_set_bit(s->prng, "enabled", s->prng_virtual);
    qdev_connect_clock_in(s->prng, "iface",
        qdev_get_clock_out(DEVICE(&s->gcc), "gcc_prng_ahb_clk"));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(s->prng), &error_fatal);
    if (s->prng_virtual) {
        /* Original /soc/qrng@793000: only DATA/STATUS, no-qrng-config. */
        sysbus_mmio_map(SYS_BUS_DEVICE(s->prng), 0, TB371FC_PRNG_BASE);
    }
}

static void tb371fc_create_gcc(TB371FCMachineState *s)
{
    DeviceState *gcc;

    /* Immutable board input clocks; no separate migration state is needed. */
    s->xo = clock_new(OBJECT(s), "gcc-xo");
    s->sleep = clock_new(OBJECT(s), "sleep-clock");
    s->fabric = clock_new(OBJECT(s), "bootstrap-fabric");
    clock_set_hz(s->xo, TB371FC_GCC_XO_HZ);
    clock_set_hz(s->sleep, TB371FC_SLEEP_HZ);
    clock_set_hz(s->fabric, s->bootstrap_fabric_hz);
    tb371fc_create_rpmh(s);

    object_initialize_child(OBJECT(s), "gcc", &s->gcc,
                            TYPE_QCOM_SM8250_GCC);
    gcc = DEVICE(&s->gcc);
    qdev_prop_set_bit(gcc, "boot-configured", s->gcc_boot_configured);
    qdev_connect_clock_in(gcc, "xo", s->xo);
    qdev_connect_clock_in(gcc, "sleep", s->sleep);
    qdev_connect_clock_in(gcc, "gcc_qupv3_wrap_2_m_ahb_clk_parent",
                          s->fabric);
    qdev_connect_clock_in(gcc, "gcc_qupv3_wrap_2_s_ahb_clk_parent",
                          s->fabric);
    if (s->uart_hs.hs_enabled || s->geni_i2c_enabled) {
        qdev_connect_clock_in(gcc, "gcc_qupv3_wrap_0_m_ahb_clk_parent",
                              s->fabric);
        qdev_connect_clock_in(gcc, "gcc_qupv3_wrap_0_s_ahb_clk_parent",
                              s->fabric);
    }
    if (s->geni_i2c_enabled) {
        qdev_connect_clock_in(gcc, "gcc_qupv3_wrap_1_m_ahb_clk_parent",
                              s->fabric);
        qdev_connect_clock_in(gcc, "gcc_qupv3_wrap_1_s_ahb_clk_parent",
                              s->fabric);
    }
    if (s->ufs) {
        qdev_connect_clock_in(gcc, "gcc_ufs_phy_ahb_clk_parent", s->fabric);
        qdev_connect_clock_in(gcc, "gcc_ufs_1x_clkref_en_parent", s->xo);
        qdev_connect_clock_in(gcc, "gcc_ufs_phy_tx_symbol_0_clk_parent",
                              qdev_get_clock_out(s->ufs, "tx-lane0"));
        qdev_connect_clock_in(gcc, "gcc_ufs_phy_rx_symbol_0_clk_parent",
                              qdev_get_clock_out(s->ufs, "rx-lane0"));
        qdev_connect_clock_in(gcc, "gcc_ufs_phy_rx_symbol_1_clk_parent",
                              qdev_get_clock_out(s->ufs, "rx-lane1"));
    }
    if (s->prng_virtual) {
        /* Explicit virtual carrier; the silicon parent/rate is not known. */
        qdev_connect_clock_in(gcc, "gcc_prng_ahb_clk_parent", s->fabric);
    }
    sysbus_realize(SYS_BUS_DEVICE(gcc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(gcc), 0, TB371FC_GCC_BASE);

    /* An attached UART requests its bus clock when HWCG is enabled. */
    qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request",
                                       TB371FC_GCC_UART_CLK), 1);
    qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request",
                                       TB371FC_GCC_QUP_M_AHB), 1);
    qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request",
                                       TB371FC_GCC_QUP_S_AHB), 1);
    if (s->uart_hs.hs_enabled) {
        /* QEMU table indices, not the vendor DT clock IDs 100/132/133. */
        qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request", 99), 1);
        qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request", 131), 1);
        qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request", 132), 1);
    }
    if (s->geni_i2c_enabled) {
        for (unsigned i = 0; i < qcom_geni_i2c_instance_count(); i++) {
            const QcomGeniI2CInstance *inst = qcom_geni_i2c_instance_at(i);

            /* Current provider indices, not the vendor DT clock IDs. */
            qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request",
                                                inst->se_clock_index), 1);
            qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request",
                                                inst->m_ahb_clock_index), 1);
            qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request",
                                                inst->s_ahb_clock_index), 1);
        }
    }
}


static DeviceState *tb371fc_create_smmu(TB371FCMachineState *s,
                                       const char *name, hwaddr base,
                                       uint32_t size, unsigned cb,
                                       const unsigned *spis, unsigned count)
{
    DeviceState *dev = qdev_new(TYPE_ARM_SMMUV2);

    object_property_add_child(OBJECT(s), name, OBJECT(dev));
    qdev_prop_set_uint32(dev, "mmio-size", size);
    qdev_prop_set_uint32(dev, "num-context-banks", cb);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, base);
    assert(count == cb + 2);
    for (unsigned i = 0; i < count; i++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), i,
                           qdev_get_gpio_in(s->gic, spis[i]));
    }
    return dev;
}

static void tb371fc_create_smmuv500(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_SMMUV500);
    bool legacy_tbu = !strcmp(s->tbu_profile, "reference-ecats");

    /* Global properties have been applied by qdev's instance_post_init. */
    if (legacy_tbu && object_property_get_bool(OBJECT(dev),
                                               "enabled-reference",
                                               &error_fatal)) {
        error_report("tbu-profile=reference-ecats and "
                     "qcom-smmuv500.enabled-reference=on are mutually "
                     "exclusive: both own the APPS TCU/TBU MMIO windows");
        exit(EXIT_FAILURE);
    }
    object_property_add_child(OBJECT(s), "apps-smmu-v500", OBJECT(dev));
    object_property_set_link(OBJECT(dev), "smmu", OBJECT(s->apps_smmu),
                             &error_fatal);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    /* Both profile identities migrate, but exactly one provider owns MMIO. */
    if (!legacy_tbu) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, QCOM_SMMUV500_TCU_BASE);
    }
    if (qtest_enabled()) {
        /* A real external IOMMU cache client, absent from normal TCG. */
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1,
                       QCOM_SMMUV500_QTEST_CACHE_BASE);
    }
}

static void tb371fc_create_smmus(TB371FCMachineState *s)
{
    s->apps_smmu = tb371fc_create_smmu(s, "apps-smmu",
        TB371FC_APPS_SMMU_BASE, 0x100000, 96,
        tb371fc_apps_smmu_spis, ARRAY_SIZE(tb371fc_apps_smmu_spis));
    s->kgsl_smmu = tb371fc_create_smmu(s, "kgsl-smmu",
        TB371FC_KGSL_SMMU_BASE, 0x10000, 8,
        tb371fc_kgsl_smmu_spis, ARRAY_SIZE(tb371fc_kgsl_smmu_spis));
    if (qtest_enabled()) {
        /* Exercise real translating DMA from QTest, absent from normal TCG. */
        memory_region_init_alias(&s->qtest_dma_alias, OBJECT(s),
            "qtest-smmu-dma", arm_smmuv2_get_dma_mr(s->apps_smmu, 0x42),
            0, 16 * MiB);
        memory_region_add_subregion(get_system_memory(), 0x50000000,
                                     &s->qtest_dma_alias);
    }
    tb371fc_create_smmuv500(s);
}

static void tb371fc_create_tbu(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_SM8250_TBU);
    bool enabled = !strcmp(s->tbu_profile, "reference-ecats");
    static const hwaddr base[] = { 0x15185000, 0x15189000 };
    static const hwaddr status[] = { 0x15182200, 0x15182208 };
    unsigned i;

    /* Keep a VMState section in both profiles to reject incompatible loads. */
    object_property_add_child(OBJECT(s), "tbu-debug", OBJECT(dev));
    object_property_set_link(OBJECT(dev), "smmu", OBJECT(s->apps_smmu),
                             &error_fatal);
    qdev_prop_set_uint32(dev, "reference-profile", enabled);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    if (!enabled) {
        return;
    }

    /* Live DT addresses; the register protocol and timings are a reference. */
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x15182000);
    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1 + i * 2, base[i]);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2 + i * 2, status[i]);
        if (qtest_enabled()) {
            g_autofree char *name = g_strdup_printf("qtest-tbu%u-dma", i);

            /* Upstream UFS SID fixtures, not a measured native DMA trace. */
            memory_region_init_alias(&s->qtest_tbu_dma_alias[i], OBJECT(s),
                name, arm_smmuv2_get_dma_mr(s->apps_smmu, 0xe0 + i * 0x400),
                0, 16 * MiB);
            memory_region_add_subregion(get_system_memory(),
                0x52000000 + i * 0x01000000, &s->qtest_tbu_dma_alias[i]);
        }
    }
}



static void tb371fc_create_coresight_replicators(TB371FCMachineState *s)
{
    static const struct {
        const char *name;
        hwaddr base;
    } replicator[] = {
        { "replicator-qdss", 0x06046000 },
        { "replicator-swao", 0x06b06000 },
    };
    size_t i;

    /*
     * Original full-DTB AMBA instances, each with part ID 0x000bb909.
     * Model their independent APB control/claim state; no ATB source is
     * attached and no trace-routing capability is inferred from a probe.
     */
    for (i = 0; i < ARRAY_SIZE(replicator); i++) {
        DeviceState *dev = qdev_new(TYPE_ARM_CORESIGHT_REPLICATOR);

        object_property_add_child(OBJECT(s), replicator[i].name, OBJECT(dev));
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, replicator[i].base);
    }
}

static void tb371fc_create_coresight_tmc(TB371FCMachineState *s)
{
    static const struct {
        const char *name;
        hwaddr base;
        uint32_t configuration;
    } tmc[] = {
        { "tmc-etf", 0x06b05000, ARM_TMC_ETF },
        { "tmc-etr", 0x06048000, ARM_TMC_ETR },
    };
    unsigned i;

    /*
     * Original DT addresses and part 0x961. The ETF has a documented reference
     * RAM/control profile. ETR debug authentication is disabled until an AXI
     * trace data path exists. There is no fabricated ATB producer or IRQ.
     */
    for (i = 0; i < ARRAY_SIZE(tmc); i++) {
        DeviceState *dev = qdev_new(TYPE_ARM_CORESIGHT_TMC);

        object_property_add_child(OBJECT(s), tmc[i].name, OBJECT(dev));
        qdev_prop_set_uint32(dev, "configuration", tmc[i].configuration);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, tmc[i].base);
    }
}

static void tb371fc_create_boot_resources(TB371FCMachineState *s)
{
    DeviceState *pshold = qdev_new(TYPE_QCOM_PSHOLD);
    DeviceState *dcc = qdev_new(TYPE_QCOM_DCC);
    DeviceState *ipcc = qdev_new(TYPE_QCOM_IPCC);
    DeviceState *qsee = qdev_new(TYPE_QCOM_QSEE_IRQ);
    DeviceState *spss_mask = qdev_new(TYPE_QCOM_SM8250_SPSS_IRQ_MASK);
    DeviceState *rpm_stats_offset = qdev_new(TYPE_QCOM_SM8250_RPM_STATS_OFFSET);
    DeviceState *ddr_stats_offset = qdev_new(TYPE_QCOM_SM8250_DDR_STATS_OFFSET);
    DeviceState *qce_bam = qdev_new(TYPE_QCOM_SM8250_QCE_BAM);
    DeviceState *mutex = qdev_new(TYPE_QCOM_TCSR_MUTEX);
    DeviceState *smem = qdev_new(TYPE_QCOM_SMEM_HANDOFF);
    DeviceState *msgram = qdev_new(TYPE_QCOM_AOP_MSGRAM);
    DeviceState *refgen = qdev_new(TYPE_QCOM_REFGEN);

    /* Captured IMEM SRAM aperture, initially empty without firmware input. */
    /*
     * Machine-owned RAM is registered globally for migration: MachineState
     * is not a DeviceState and cannot be the auto-registration owner here.
     */
    memory_region_init_ram(&s->imem, NULL, "tb371fc.imem", 0x1000,
                           &error_fatal);
    memory_region_add_subregion(get_system_memory(), 0x146bf000, &s->imem);

    object_property_add_child(OBJECT(s), "pshold", OBJECT(pshold));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(pshold), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(pshold), 0, 0x0c264000);
    /* PMIC PON must consume ps-hold before this can implement power policy. */

    /* Captured DCC windows; map2 identity remains a reference profile. */
    object_property_add_child(OBJECT(s), "dcc", OBJECT(dcc));
    object_property_set_link(OBJECT(dcc), "memory",
                             OBJECT(get_system_memory()), &error_fatal);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dcc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dcc), 0, 0x01023000);
    sysbus_mmio_map(SYS_BUS_DEVICE(dcc), 1, 0x0103a000);

    object_property_add_child(OBJECT(s), "ipcc", OBJECT(ipcc));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(ipcc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(ipcc), 0, 0x00408000);
    sysbus_connect_irq(SYS_BUS_DEVICE(ipcc), 0,
                      qdev_get_gpio_in(s->gic, 229));

    object_property_add_child(OBJECT(s), "qsee-irq", OBJECT(qsee));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(qsee), &error_fatal);
    for (unsigned i = 0; i < QCOM_QSEE_IRQ_BANKS; i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(qsee), i, 0x01886000 + i * 0x2000);
        sysbus_connect_irq(SYS_BUS_DEVICE(qsee), i,
                           qdev_get_gpio_in(s->gic, 348 + i));
    }

    /* Actual SPSS mask word only; no status, IRQ, PAS or secure-firmware peer. */
    object_property_add_child(OBJECT(s), "spss-irq-mask", OBJECT(spss_mask));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(spss_mask), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(spss_mask), 0, 0x01881028);

    /* Exact reference offset word; no statistics payload or other AOP words. */
    object_property_add_child(OBJECT(s), "rpm-stats-offset",
                              OBJECT(rpm_stats_offset));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(rpm_stats_offset), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(rpm_stats_offset), 0, 0x0c3f0004);

    /* DDR reference offset word only; no DDR statistics payload. */
    object_property_add_child(OBJECT(s), "ddr-stats-offset",
                              OBJECT(ddr_stats_offset));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(ddr_stats_offset), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(ddr_stats_offset), 0, 0x0c3f001c);

    /* One strict shared QCE/BAM union; reference profile is default off. */
    object_property_add_child(OBJECT(s), "qce-bam", OBJECT(qce_bam));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(qce_bam), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(qce_bam), 0, QCOM_SM8250_QCE_BAM_BASE);
    sysbus_connect_irq(SYS_BUS_DEVICE(qce_bam), 0,
                       qdev_get_gpio_in(s->gic, 272));

    /* Captured SMEM hardware-lock geometry; initial owners remain unknown. */
    object_property_add_child(OBJECT(s), "tcsr-mutex", OBJECT(mutex));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(mutex), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(mutex), 0, 0x01f40000);

    /*
     * Do not silently change the mutex's unknown handoff. This synthetic SMEM
     * profile requires the caller to opt in to the separate reference lock.
     */
    if (!strcmp(s->smem_profile, "reference-empty") &&
        !object_property_get_bool(OBJECT(mutex), "boot-unlocked-reference",
                                  &error_fatal)) {
        error_report("tb371fc: smem-profile=reference-empty requires "
                     "-global qcom-tcsr-mutex.boot-unlocked-reference=on");
        exit(1);
    }
    object_property_add_child(OBJECT(s), "smem-handoff", OBJECT(smem));
    object_property_set_link(OBJECT(smem), "ram", OBJECT(MACHINE(s)->ram),
                             &error_fatal);
    qdev_prop_set_bit(smem, "reference-empty",
                      !strcmp(s->smem_profile, "reference-empty"));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(smem), &error_fatal);


    /* Actual shared storage, without an invented AOP descriptor or peer. */
    QCOM_AOP_MSGRAM(msgram)->reference_peer =
        !strcmp(s->aop_profile, "reference-qdss-vote");
    object_property_add_child(OBJECT(s), "aop-msgram", OBJECT(msgram));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(msgram), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(msgram), 0, 0x0c300000);

    if (!strcmp(s->aop_profile, "reference-qdss-vote")) {
        DeviceState *peer = qdev_new(TYPE_QCOM_AOP_QMP);

        object_property_add_child(OBJECT(s), "aop-peer", OBJECT(peer));
        object_property_set_link(OBJECT(peer), "msgram", OBJECT(msgram),
                                 &error_fatal);
        object_property_set_link(OBJECT(peer), "ipcc", OBJECT(ipcc),
                                 &error_fatal);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(peer), &error_fatal);
    }

    /* Kona CTRL5 software request; firmware handoff is unknown by default. */
    object_property_add_child(OBJECT(s), "refgen", OBJECT(refgen));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(refgen), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(refgen), 0,
                    0x088e7000 + QCOM_REFGEN_CTRL5_OFFSET);

    s->tlmm = qdev_new(TYPE_QCOM_TB371FC_TLMM);
    object_property_add_child(OBJECT(s), "tlmm", OBJECT(s->tlmm));
    if (s->enable_ufs) {
        qdev_prop_set_bit(s->tlmm, "boot-ufs-reset-high", true);
    }
    sysbus_realize_and_unref(SYS_BUS_DEVICE(s->tlmm), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->tlmm), 0, 0x0f000000);
    sysbus_connect_irq(SYS_BUS_DEVICE(s->tlmm), 0,
                        qdev_get_gpio_in(s->gic, 208));
}

static void tb371fc_create_pdc(TB371FCMachineState *s)
{
    static const struct {
        unsigned first, spi, count;
    } ranges[] = {
        { 0, 480, 94 },
        { 94, 609, 31 },
        { 125, 63, 1 },
        { 126, 716, 12 },
    };

    s->pdc = qdev_new(TYPE_QCOM_PDC);
    object_property_add_child(OBJECT(s), "pdc", OBJECT(s->pdc));
    /*
     * The captured vendor driver uses the legacy layout without reading
     * VERSION. Leave silicon identity unobserved by default; mainline
     * experiments may set the device's explicit reference-version property.
     */
    sysbus_realize_and_unref(SYS_BUS_DEVICE(s->pdc), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->pdc), 0, 0x0b220000);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->pdc), 1, 0x17c000f0);
    for (unsigned i = 0; i < ARRAY_SIZE(ranges); i++) {
        for (unsigned j = 0; j < ranges[i].count; j++) {
            sysbus_connect_irq(SYS_BUS_DEVICE(s->pdc), ranges[i].first + j,
                               qdev_get_gpio_in(s->gic, ranges[i].spi + j));
        }
    }
}

static void tb371fc_create_spmi_debug_fuse(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_SM8250_SPMI_DEBUG_FUSE);

    object_property_add_child(OBJECT(s), "spmi-debug-fuse", OBJECT(dev));
    qdev_prop_set_bit(dev, "enabled", s->spmi_debug_fuse_enabled);
    if (s->spmi_debug_fuse_word) {
        qdev_prop_set_string(dev, "fuse-word", s->spmi_debug_fuse_word);
    }
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, QCOM_SPMI_DEBUG_FUSE_GUARD_BASE);
}

static void tb371fc_create_spmi(TB371FCMachineState *s)
{
    static const hwaddr bases[QCOM_SPMI_PMIC_ARB_NUM_REGIONS] = {
        [QCOM_SPMI_PMIC_ARB_CORE]   = 0x0c440000,
        [QCOM_SPMI_PMIC_ARB_CHNLS]  = 0x0c600000,
        [QCOM_SPMI_PMIC_ARB_OBSRVR] = 0x0e600000,
        [QCOM_SPMI_PMIC_ARB_INTR]   = 0x0e700000,
        [QCOM_SPMI_PMIC_ARB_CNFG]   = 0x0c40a000,
    };
    DeviceState *dev;

    if (!strcmp(s->spmi_profile, "off")) {
        return;
    }

    /*
     * Only the apertures and PDC input are measured. APID assignment,
     * ownership and arbiter version require the explicit reference profile.
     */
    dev = qdev_new(TYPE_QCOM_SPMI_PMIC_ARB);
    object_property_add_child(OBJECT(s), "spmi", OBJECT(dev));
    qdev_prop_set_bit(dev, "reference-map", true);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    for (unsigned i = 0; i < ARRAY_SIZE(bases); i++) {
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), i, bases[i]);
    }
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0,
                       qdev_get_gpio_in_named(s->pdc, "irq-in", 1));
}

static void tb371fc_create_watchdog(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_KPSS_WATCHDOG);

    s->watchdog = dev;
    object_property_add_child(OBJECT(s), "watchdog", OBJECT(dev));
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, TB371FC_WATCHDOG_BASE);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0, qdev_get_gpio_in(s->gic, 0));
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1, qdev_get_gpio_in(s->gic, 1));
}

static void tb371fc_create_mpm2_sleep_counter(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_QCOM_MPM2_SLEEP_COUNTER);

    object_property_add_child(OBJECT(s), "mpm2-sleep-counter", OBJECT(dev));
    /*
     * Original DT declares 32768 Hz. This is a separate fixed reference
     * domain, not the existing 32000 Hz GCC sleep input. Only offset zero
     * is established by boot_stats.c; the rest of the DT's 4 KiB is absent.
     */
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, 0x0c221000);
}

static void tb371fc_create_tsens(TB371FCMachineState *s)
{
    static const struct {
        hwaddr srot, tm;
        unsigned upper_lower_spi, critical_spi;
    } controllers[] = {
        { 0x0c222000, 0x0c263000, 506, 508 },
        { 0x0c223000, 0x0c265000, 507, 509 },
    };

    for (unsigned i = 0; i < ARRAY_SIZE(controllers); i++) {
        DeviceState *dev = qdev_new(TYPE_QCOM_TSENS24XX);
        g_autofree char *name = g_strdup_printf("tsens%u", i);

        object_property_add_child(OBJECT(s), name, OBJECT(dev));
        qdev_prop_set_uint32(dev, "controller-id", i);
        sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
        /*
         * Actual DT lists a four-byte SROT resource, but the pinned driver
         * and observed abort establish a read at +4 after page-rounded
         * ioremap. Map only that control word; preserve the original DT.
         */
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, controllers[i].srot + 4);
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1, controllers[i].tm);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 0,
            qdev_get_gpio_in(s->gic, controllers[i].upper_lower_spi));
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1,
            qdev_get_gpio_in(s->gic, controllers[i].critical_spi));
    }
}

static void tb371fc_create_mmio_timer(TB371FCMachineState *s)
{
    DeviceState *dev = qdev_new(TYPE_ARM_ARCH_TIMER_MMIO);

    s->mmio_timer = dev;
    object_property_add_child(OBJECT(s), "mmio-timer", OBJECT(dev));
    qdev_prop_set_uint32(dev, "num-frames", 7);
    qdev_prop_set_uint64(dev, "frequency", TB371FC_TIMER_HZ);
    qdev_prop_set_bit(dev, "exact-frequency", s->counter_exact);
    qdev_prop_set_uint8(dev, "virtual-frame-mask", 1);
    qdev_prop_set_uint8(dev, "el0-frame-mask", 1);
    /* Direct-kernel boot assumes firmware granted NS access to frame 0. */
    qdev_prop_set_uint8(dev, "reset-ns-access", 1);
    sysbus_realize_and_unref(SYS_BUS_DEVICE(dev), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 0, TB371FC_MMIO_TIMER_BASE);
    for (unsigned i = 0; i < 7; i++) {
        hwaddr base = TB371FC_MMIO_TIMER_BASE + 0x1000 + i * 0x2000;
        sysbus_mmio_map(SYS_BUS_DEVICE(dev), 1 + i * 2, base);
        sysbus_connect_irq(SYS_BUS_DEVICE(dev), i * 2,
                           qdev_get_gpio_in(s->gic, 8 + i));
    }
    sysbus_mmio_map(SYS_BUS_DEVICE(dev), 2, TB371FC_MMIO_TIMER_BASE + 0x2000);
    sysbus_connect_irq(SYS_BUS_DEVICE(dev), 1, qdev_get_gpio_in(s->gic, 6));
}

static void tb371fc_realize_ufs(TB371FCMachineState *s)
{
    DeviceState *gcc = DEVICE(&s->gcc);
    static const struct {
        const char *input, *output;
    } clocks[] = {
        { "core-clk", "gcc_ufs_phy_axi_clk" },
        { "iface-clk", "gcc_ufs_phy_ahb_clk" },
        { "unipro-clk", "gcc_ufs_phy_unipro_core_clk" },
        { "phy-aux-clk", "gcc_ufs_phy_phy_aux_clk" },
        { "aggre-clk", "gcc_aggre_ufs_phy_axi_clk" },
        { "qref-clk", "gcc_ufs_1x_clkref_en" },
        { "tx-sync-clk", "gcc_ufs_phy_tx_symbol_0_clk" },
        { "rx0-sync-clk", "gcc_ufs_phy_rx_symbol_0_clk" },
        { "rx1-sync-clk", "gcc_ufs_phy_rx_symbol_1_clk" },
    };
    const unsigned requests[] = { 6, 148, 161, 162, 166, 168, 169, 170, 171 };
    unsigned i;

    qdev_prop_set_string(s->ufs, "serial", "QEMU-TB371FC-TEST");
    /* Mainline DT assigns e0/4e0; the native traffic-class split is unknown. */
    object_property_set_link(OBJECT(s->ufs), "dma-memory",
        OBJECT(arm_smmuv2_get_dma_mr(s->apps_smmu, 0xe0)), &error_fatal);
    for (i = 0; i < ARRAY_SIZE(clocks); i++) {
        qdev_connect_clock_in(s->ufs, clocks[i].input,
                              qdev_get_clock_out(gcc, clocks[i].output));
    }
    qdev_connect_clock_in(s->ufs, "ref-clk", s->xo);
    qdev_connect_gpio_out_named(gcc, "reset", 33,
                                qdev_get_gpio_in_named(s->ufs, "reset", 0));
    qdev_connect_gpio_out_named(gcc, "power", 4,
                                qdev_get_gpio_in_named(s->ufs, "power", 0));
    for (i = 0; i < ARRAY_SIZE(requests); i++) {
        qemu_set_irq(qdev_get_gpio_in_named(gcc, "hw-request", requests[i]), 1);
    }
    sysbus_realize_and_unref(SYS_BUS_DEVICE(s->ufs), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->ufs), 0, TB371FC_UFS_BASE);
    sysbus_mmio_map(SYS_BUS_DEVICE(s->ufs), 1, TB371FC_UFS_PHY_BASE);
    memory_region_init_io(&s->ufs_ice_probe, OBJECT(s), &tb371fc_ice_ops, s,
                          "tb371fc-ufs-ice-unsupported", TB371FC_UFS_ICE_SIZE);
    memory_region_add_subregion(get_system_memory(), TB371FC_UFS_ICE_BASE,
                               &s->ufs_ice_probe);
    sysbus_connect_irq(SYS_BUS_DEVICE(s->ufs), 0,
                       qdev_get_gpio_in(s->gic, TB371FC_UFS_SPI));
}

static void tb371fc_init(MachineState *ms)
{
    TB371FCMachineState *s = TB371FC_MACHINE(ms);
    MemoryRegion *sysmem = get_system_memory();
    DeviceState *mdss;
    SysBusDevice *uart;
    uint64_t load_base;

    if (!tcg_enabled() && !qtest_enabled()) {
        error_report("tb371fc supports TCG execution or qtest device testing");
        exit(1);
    }
    if (ms->smp.cpus != TB371FC_CPUS || ms->smp.max_cpus != TB371FC_CPUS) {
        error_report("tb371fc requires its fixed eight-CPU profile (-smp 8)");
        exit(1);
    }
    if (strcmp(ms->cpu_type, ARM_CPU_TYPE_NAME("cortex-a55"))) {
        error_report("tb371fc uses fixed A55/A77 CPU groups; omit -cpu");
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
    tb371fc_create_dsu_pmu(s);
    tb371fc_create_cpus(s, sysmem);
    tb371fc_create_gic(s);
    /* Original DT /soc/dsu_pmu@0: all eight CPUs, level-high SPI 50. */
    sysbus_connect_irq(SYS_BUS_DEVICE(s->dsu_pmu), 0,
                       qdev_get_gpio_in(s->gic, 50));
    tb371fc_create_pdc(s);
    tb371fc_create_spmi(s);
    tb371fc_create_spmi_debug_fuse(s);
    tb371fc_create_mmio_timer(s);
    tb371fc_create_mpm2_sleep_counter(s);
    tb371fc_create_tsens(s);
    tb371fc_create_watchdog(s);
    tb371fc_create_boot_resources(s);
    tb371fc_create_coresight_tmc(s);
    tb371fc_create_coresight_replicators(s);
    tb371fc_create_smmus(s);
    tb371fc_create_tbu(s);
    if (s->enable_ufs) {
        s->ufs = qdev_new(TYPE_QCOM_SM8250_UFS);
        object_property_add_child(OBJECT(s), "ufs", OBJECT(s->ufs));
        s->ufs->id = g_strdup("ufs0");
    }
    /* Apply the HS qdev global before conditional GCC parents are connected. */
    object_initialize_child(OBJECT(s), "uart-hs", &s->uart_hs,
                            TYPE_QCOM_SM8250_GENI_HS);
    tb371fc_prepare_geni_i2c(s);
    tb371fc_create_gcc(s);
    tb371fc_create_prng(s);
    tb371fc_create_cpufreq(s);
    tb371fc_create_devfreq(s);
    tb371fc_create_sbm(s);
    tb371fc_create_gpucc(s);
    tb371fc_create_npucc(s);
    tb371fc_create_camcc(s);
    tb371fc_create_videocc(s);
    tb371fc_create_npu_gdsc(s);
    tb371fc_create_noc_qos(s);
    tb371fc_create_gpu_cx_gdsc(s);
    tb371fc_create_gpu_gx_gdsc(s);
    mdss = tb371fc_create_mdss_and_video_gdsc(s,
                                            tb371fc_create_camera_gdsc(s));
    /*
     * Register provider VMState before its consumers. MDSS pre_load retracts
     * stale GPIO outputs; it must finish before incoming DSI/DP qualification
     * is restored. DISPCC then restores its saved physical input periods.
     */
    tb371fc_create_dsi_pll(s);
    tb371fc_create_dp_pll(s, mdss);
    tb371fc_connect_display_pll_power(s, mdss);
    tb371fc_create_dispcc(s);
    tb371fc_create_llcc(s);
    if (s->ufs) {
        tb371fc_realize_ufs(s);
        /* Native pinctrl states: output-low asserts the dedicated pad. */
        qdev_connect_gpio_out_named(s->tlmm, "ufs-reset", 0,
            qemu_irq_invert(qdev_get_gpio_in_named(s->ufs, "device-reset", 0)));
    }

    object_initialize_child(OBJECT(s), "uart", &s->uart, TYPE_QCOM_GENI_UART);
    qdev_prop_set_chr(DEVICE(&s->uart), "chardev", serial_hd(0));
    qdev_connect_clock_in(DEVICE(&s->uart), "se-clk",
                          qdev_get_clock_out(DEVICE(&s->gcc),
                                              "gcc_qupv3_wrap2_s4_clk"));
    qdev_connect_clock_in(DEVICE(&s->uart), "m-ahb",
                          qdev_get_clock_out(DEVICE(&s->gcc),
                                              "gcc_qupv3_wrap_2_m_ahb_clk"));
    qdev_connect_clock_in(DEVICE(&s->uart), "s-ahb",
                          qdev_get_clock_out(DEVICE(&s->gcc),
                                              "gcc_qupv3_wrap_2_s_ahb_clk"));
    qdev_connect_gpio_out_named(DEVICE(&s->gcc), "reset",
                                TB371FC_GCC_QUP_RESET,
                                tb371fc_geni_reset_sink(s, 2,
                                                       DEVICE(&s->uart)));
    uart = SYS_BUS_DEVICE(&s->uart);
    sysbus_realize(uart, &error_fatal);
    sysbus_mmio_map(uart, 0, TB371FC_UART_BASE);
    sysbus_connect_irq(uart, 0, qdev_get_gpio_in(s->gic, TB371FC_UART_SPI));

    memory_region_init_io(&s->qup_version, OBJECT(s), &tb371fc_qup_ops, s,
                          "tb371fc.synthetic-qup-version", TB371FC_QUP_SIZE);
    memory_region_add_subregion(sysmem, TB371FC_QUP_BASE, &s->qup_version);

    qdev_prop_set_chr(DEVICE(&s->uart_hs), "chardev", serial_hd(1));
    qdev_connect_clock_in(DEVICE(&s->uart_hs), "se-clk",
                          qdev_get_clock_out(DEVICE(&s->gcc),
                                              "gcc_qupv3_wrap0_s6_clk"));
    qdev_connect_clock_in(DEVICE(&s->uart_hs), "m-ahb",
                          qdev_get_clock_out(DEVICE(&s->gcc),
                                              "gcc_qupv3_wrap_0_m_ahb_clk"));
    qdev_connect_clock_in(DEVICE(&s->uart_hs), "s-ahb",
                          qdev_get_clock_out(DEVICE(&s->gcc),
                                              "gcc_qupv3_wrap_0_s_ahb_clk"));
    qdev_connect_gpio_out_named(DEVICE(&s->gcc), "reset", 24,
                                tb371fc_geni_reset_sink(s, 0,
                                                       DEVICE(&s->uart_hs)));
    uart = SYS_BUS_DEVICE(&s->uart_hs);
    sysbus_realize(uart, &error_fatal);
    sysbus_mmio_map(uart, 0, qcom_geni_hs_se_base());
    sysbus_mmio_map(uart, 1, qcom_geni_hs_wrapper_version_address());
    sysbus_connect_irq(uart, 0, qdev_get_gpio_in(s->gic, 607));
    tb371fc_realize_geni_i2c(s);

    s->bootinfo.ram_size = ms->ram_size;
    s->bootinfo.board_id = -1;
    /* Native /proc/iomem places Image text at a0080000, offset 80000. */
    load_base = s->kernel_load_base ? s->kernel_load_base :
                (ms->ram_size >= GiB ? UINT64_C(0xa0000000) : TB371FC_RAM_BASE);
    if ((load_base & ((2 * MiB) - 1)) || load_base < TB371FC_RAM_BASE ||
        load_base >= TB371FC_RAM_BASE + ms->ram_size ||
        (load_base >= TB371FC_RAM_HOLE_BASE &&
         load_base < TB371FC_RAM_HOLE_END)) {
        error_report("tb371fc: kernel-load-base must be a 2 MiB-aligned "
                     "address within usable board RAM");
        exit(1);
    }
    s->bootinfo.loader_start = load_base;
    s->bootinfo.ram_size = ms->ram_size - (load_base - TB371FC_RAM_BASE);
    s->bootinfo.psci_conduit = QEMU_PSCI_CONDUIT_SMC;
    s->bootinfo.preserve_psci_node = true;
    s->bootinfo.get_dtb = tb371fc_get_dtb;
    s->bootinfo.modify_dtb = tb371fc_fixup_memory;
    tb371fc_create_fdt(s);
    arm_load_kernel(s->cpu[0], ms, &s->bootinfo);
}

static bool tb371fc_get_ufs(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->enable_ufs;
}

static void tb371fc_set_ufs(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "ufs-enabled is fixed after machine creation");
        return;
    }
    s->enable_ufs = value;
}

static bool tb371fc_get_gcc_boot(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->gcc_boot_configured;
}

static void tb371fc_set_gcc_boot(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "gcc-boot-configured is fixed after machine creation");
        return;
    }
    s->gcc_boot_configured = value;
}

static void tb371fc_get_fabric_hz(Object *obj, Visitor *v, const char *name,
                                 void *opaque, Error **errp)
{
    uint64_t hz = TB371FC_MACHINE(obj)->bootstrap_fabric_hz;
    visit_type_uint64(v, name, &hz, errp);
}

static void tb371fc_set_fabric_hz(Object *obj, Visitor *v, const char *name,
                                 void *opaque, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);
    uint64_t hz;

    if (!visit_type_uint64(v, name, &hz, errp)) {
        return;
    }
    if (s->fabric || hz > 1000000000) {
        error_setg(errp, "bootstrap-fabric-hz requires 0..1000000000 "
                   "before machine creation");
        return;
    }
    s->bootstrap_fabric_hz = hz;
}


static bool tb371fc_get_counter_exact(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->counter_exact;
}

static void tb371fc_set_counter_exact(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "counter-exact is fixed after machine creation");
        return;
    }
    s->counter_exact = value;
}

static void tb371fc_get_kernel_load_base(Object *obj, Visitor *v,
                                         const char *name, void *opaque,
                                         Error **errp)
{
    uint64_t base = TB371FC_MACHINE(obj)->kernel_load_base;
    visit_type_uint64(v, name, &base, errp);
}

static void tb371fc_set_kernel_load_base(Object *obj, Visitor *v,
                                         const char *name, void *opaque,
                                         Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);
    uint64_t base;

    if (s->xo) {
        error_setg(errp, "kernel-load-base is fixed after machine creation");
        return;
    }
    if (visit_type_uint64(v, name, &base, errp)) {
        s->kernel_load_base = base;
    }
}


static char *tb371fc_get_tbu_profile(Object *obj, Error **errp)
{
    return g_strdup(TB371FC_MACHINE(obj)->tbu_profile);
}

static void tb371fc_set_tbu_profile(Object *obj, const char *value,
                                    Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "tbu-profile is fixed after machine creation");
        return;
    }
    if (!value || (strcmp(value, "off") && strcmp(value, "reference-ecats"))) {
        error_setg(errp, "tbu-profile must be off or reference-ecats");
        return;
    }
    g_free(s->tbu_profile);
    s->tbu_profile = g_strdup(value);
}

static char *tb371fc_get_aop_profile(Object *obj, Error **errp)
{
    return g_strdup(TB371FC_MACHINE(obj)->aop_profile);
}

static void tb371fc_set_aop_profile(Object *obj, const char *value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "aop-profile is fixed after machine creation");
        return;
    }
    if (!value || (strcmp(value, "none") &&
                   strcmp(value, "reference-qdss-vote"))) {
        error_setg(errp, "aop-profile must be none or reference-qdss-vote");
        return;
    }
    g_free(s->aop_profile);
    s->aop_profile = g_strdup(value);
}

static char *tb371fc_get_smem_profile(Object *obj, Error **errp)
{
    return g_strdup(TB371FC_MACHINE(obj)->smem_profile);
}

static void tb371fc_set_smem_profile(Object *obj, const char *value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "smem-profile is fixed after machine creation");
        return;
    }
    if (!value || (strcmp(value, "off") && strcmp(value, "reference-empty"))) {
        error_setg(errp, "smem-profile must be off or reference-empty");
        return;
    }
    g_free(s->smem_profile);
    s->smem_profile = g_strdup(value);
}


static bool tb371fc_get_ras_zero_records_compat(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->ras_zero_records_compat;
}

static void tb371fc_set_ras_zero_records_compat(Object *obj, bool value,
                                               Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->cpu[0]) {
        error_setg(errp, "ras-zero-records-compat is fixed after CPU creation");
        return;
    }
    s->ras_zero_records_compat = value;
}

static bool tb371fc_get_prng_virtual(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->prng_virtual;
}

static void tb371fc_set_prng_virtual(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->cpu[0]) {
        error_setg(errp, "prng-virtual is fixed after machine creation");
        return;
    }
    s->prng_virtual = value;
}

static bool tb371fc_get_dsu_pmu_cycle_only(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->dsu_pmu_cycle_only;
}

static void tb371fc_set_dsu_pmu_cycle_only(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->cpu[0]) {
        error_setg(errp, "dsu-pmu-cycle-only is fixed after CPU creation");
        return;
    }
    s->dsu_pmu_cycle_only = value;
}

static bool tb371fc_get_geni_i2c_enabled(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->geni_i2c_enabled;
}

static void tb371fc_set_geni_i2c_enabled(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "geni-i2c-enabled is fixed after machine creation");
        return;
    }
    s->geni_i2c_enabled = value;
}

static bool tb371fc_get_spmi_debug_fuse_enabled(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->spmi_debug_fuse_enabled;
}

static void tb371fc_set_spmi_debug_fuse_enabled(Object *obj, bool value,
                                              Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "spmi-debug-fuse-enabled is fixed after machine creation");
        return;
    }
    s->spmi_debug_fuse_enabled = value;
}

static char *tb371fc_get_spmi_debug_fuse_word(Object *obj, Error **errp)
{
    const char *word = TB371FC_MACHINE(obj)->spmi_debug_fuse_word;

    return g_strdup(word ? word : "");
}

static void tb371fc_set_spmi_debug_fuse_word(Object *obj, const char *value,
                                           Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "spmi-debug-fuse-word is fixed after machine creation");
        return;
    }
    g_free(s->spmi_debug_fuse_word);
    s->spmi_debug_fuse_word = g_strdup(value);
}

static char *tb371fc_get_spmi_profile(Object *obj, Error **errp)
{
    return g_strdup(TB371FC_MACHINE(obj)->spmi_profile);
}

static void tb371fc_set_spmi_profile(Object *obj, const char *value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "spmi-profile is fixed after machine creation");
        return;
    }
    if (!value || (strcmp(value, "off") && strcmp(value, "reference"))) {
        error_setg(errp, "spmi-profile must be off or reference");
        return;
    }
    g_free(s->spmi_profile);
    s->spmi_profile = g_strdup(value);
}

static char *tb371fc_get_rpmh_profile(Object *obj, Error **errp)
{
    return g_strdup(TB371FC_MACHINE(obj)->rpmh_profile);
}

static void tb371fc_set_rpmh_profile(Object *obj, const char *value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "rpmh-profile is fixed after machine creation");
        return;
    }
    if (!value || (strcmp(value, "off") && strcmp(value, "reference") &&
                   strcmp(value, "file"))) {
        error_setg(errp, "rpmh-profile must be off, reference, or file");
        return;
    }
    g_free(s->rpmh_profile);
    s->rpmh_profile = g_strdup(value);
}

static char *tb371fc_get_rpmh_cmd_db(Object *obj, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);
    return g_strdup(s->rpmh_cmd_db ? s->rpmh_cmd_db : "");
}

static void tb371fc_set_rpmh_cmd_db(Object *obj, const char *value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "rpmh-cmd-db is fixed after machine creation");
        return;
    }
    g_free(s->rpmh_cmd_db);
    s->rpmh_cmd_db = g_strdup(value);
}

static bool tb371fc_get_rpmh_boot(Object *obj, Error **errp)
{
    return TB371FC_MACHINE(obj)->rpmh_boot_configured;
}

static void tb371fc_set_rpmh_boot(Object *obj, bool value, Error **errp)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    if (s->xo) {
        error_setg(errp, "rpmh-boot-configured is fixed after machine creation");
        return;
    }
    s->rpmh_boot_configured = value;
}

static void tb371fc_instance_init(Object *obj)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);

    s->rpmh_profile = g_strdup("off");
    s->spmi_profile = g_strdup("off");
    s->spmi_debug_fuse_enabled = false;
    s->geni_i2c_enabled = false;
    s->spmi_debug_fuse_word = NULL;
    s->smem_profile = g_strdup("off");
    s->aop_profile = g_strdup("none");
    s->tbu_profile = g_strdup("off");
    s->rpmh_boot_configured = true;
    s->counter_exact = true;
    s->ras_zero_records_compat = false;
    s->dsu_pmu_cycle_only = false;
    s->prng_virtual = false;
    s->gcc_boot_configured = true;
    s->enable_ufs = true;
    s->bootstrap_fabric_hz = 100000000;
}

static void tb371fc_instance_finalize(Object *obj)
{
    TB371FCMachineState *s = TB371FC_MACHINE(obj);
    g_free(s->rpmh_profile);
    g_free(s->rpmh_cmd_db);
    g_free(s->spmi_profile);
    g_free(s->spmi_debug_fuse_word);
    g_free(s->smem_profile);
    g_free(s->aop_profile);
    g_free(s->tbu_profile);
}

static void tb371fc_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "Experimental Lenovo TB371FC (SM8250 subset; A55/A77)";
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

    object_class_property_add_str(oc, "aop-profile",
        tb371fc_get_aop_profile, tb371fc_set_aop_profile);
    object_class_property_set_description(oc, "aop-profile",
        "none (default) or explicit reference QMP peer for QDSS off/dynamic "
        "votes; no AOP firmware or physical clock-frequency model");
    object_class_property_add_str(oc, "tbu-profile",
        tb371fc_get_tbu_profile, tb371fc_set_tbu_profile);
    object_class_property_set_description(oc, "tbu-profile",
        "off (default) or reference-ecats ANOC1/2 HALT and debug translation; "
        "no silicon identity, physical transaction stall or clock-gate model");
    object_class_property_add_str(oc, "smem-profile",
        tb371fc_get_smem_profile, tb371fc_set_smem_profile);
    object_class_property_set_description(oc, "smem-profile",
        "off (default) or empty SMEM v12 reference handoff; requires "
        "explicit unlocked-reference TCSR profile");
    object_class_property_add_str(oc, "spmi-profile",
        tb371fc_get_spmi_profile, tb371fc_set_spmi_profile);
    object_class_property_set_description(oc, "spmi-profile",
        "off (default) or explicit reference APID/ownership/PMIC subset");
    object_class_property_add_bool(oc, "ras-zero-records-compat",
        tb371fc_get_ras_zero_records_compat,
        tb371fc_set_ras_zero_records_compat);
    object_class_property_set_description(oc, "ras-zero-records-compat",
        "Opt in to AArch64 zero-record RAS RES0/RAZ/WI compatibility on "
        "all eight CPUs (default off); no physical error records or IRQs");
    object_class_property_add_bool(oc, "prng-virtual",
        tb371fc_get_prng_virtual, tb371fc_set_prng_virtual);
    object_class_property_set_description(oc, "prng-virtual",
        "Explicit host-entropy PRNG with a synthetic AHB carrier (default off)");
    object_class_property_add_bool(oc, "dsu-pmu-cycle-only",
        tb371fc_get_dsu_pmu_cycle_only, tb371fc_set_dsu_pmu_cycle_only);
    object_class_property_set_description(oc, "dsu-pmu-cycle-only",
        "Opt in to a shared virtual 1 GHz DSU cycle counter with no "
        "programmable events (default off); not a physical DSU PMU");
    object_class_property_add_bool(oc, "geni-i2c-enabled",
        tb371fc_get_geni_i2c_enabled, tb371fc_set_geni_i2c_enabled);
    object_class_property_set_description(oc, "geni-i2c-enabled",
        "Enable the nine original-DT I2C controller instances with the "
        "virtual empty-bus policy (default off); address NACK, no slaves "
        "or successful payload/DMA traffic");
    object_class_property_add_bool(oc, "spmi-debug-fuse-enabled",
        tb371fc_get_spmi_debug_fuse_enabled, tb371fc_set_spmi_debug_fuse_enabled);
    object_class_property_set_description(oc, "spmi-debug-fuse-enabled",
        "Enable independent read-only debug fuse aperture (default off); "
        "requires explicit spmi-debug-fuse-word");
    object_class_property_add_str(oc, "spmi-debug-fuse-word",
        tb371fc_get_spmi_debug_fuse_word, tb371fc_set_spmi_debug_fuse_word);
    object_class_property_set_description(oc, "spmi-debug-fuse-word",
        "Explicit immutable 32-bit word as 0xhhhhhhhh; no value is bound "
        "by default and no physical fuse capture is implied");
    object_class_property_add_str(oc, "rpmh-profile",
        tb371fc_get_rpmh_profile, tb371fc_set_rpmh_profile);
    object_class_property_set_description(oc, "rpmh-profile",
        "off (default), explicit reference command-db, or file");
    object_class_property_add_str(oc, "rpmh-cmd-db",
        tb371fc_get_rpmh_cmd_db, tb371fc_set_rpmh_cmd_db);
    object_class_property_add_bool(oc, "rpmh-boot-configured",
        tb371fc_get_rpmh_boot, tb371fc_set_rpmh_boot);
    object_class_property_set_description(oc, "rpmh-boot-configured",
        "Firmware handoff DRV2 XO vote (default on); off for cold RSC tests");
    object_class_property_add_bool(oc, "counter-exact",
        tb371fc_get_counter_exact, tb371fc_set_counter_exact);
    object_class_property_set_description(oc, "counter-exact",
        "Exact 19.2 MHz rational counters (default on); off retains legacy "
        "integer-nanosecond timing for component compatibility tests");
    object_class_property_add(oc, "kernel-load-base", "uint64",
        tb371fc_get_kernel_load_base, tb371fc_set_kernel_load_base, NULL, NULL);
    object_class_property_set_description(oc, "kernel-load-base",
        "Linux boot allocation base; 0 selects measured a0000000 for >=1 GiB "
        "RAM, or 80000000 for small component fixtures");
    object_class_property_add_bool(oc, "ufs-enabled",
                                    tb371fc_get_ufs, tb371fc_set_ufs);
    object_class_property_set_description(oc, "ufs-enabled",
        "Create the SM8250 UFS controller/PHY (default on); off is an "
        "isolated component test profile, not the complete board");
    object_class_property_add_bool(oc, "gcc-boot-configured",
                                    tb371fc_get_gcc_boot,
                                    tb371fc_set_gcc_boot);
    object_class_property_set_description(oc, "gcc-boot-configured",
        "Initialize GCC for direct Linux entry (default on); off for cold "
        "register tests, not a firmware boot implementation");
    object_class_property_add(oc, "bootstrap-fabric-hz", "uint64",
                               tb371fc_get_fabric_hz, tb371fc_set_fabric_hz,
                               NULL, NULL);
    object_class_property_set_description(oc, "bootstrap-fabric-hz",
        "Explicit temporary AHB parent frequency (default 100 MHz), "
        "not a measured SM8250 fabric rate");
}

static const TypeInfo tb371fc_machine_type = {
    .name = TYPE_TB371FC_MACHINE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(TB371FCMachineState),
    .instance_init = tb371fc_instance_init,
    .instance_finalize = tb371fc_instance_finalize,
    .class_init = tb371fc_class_init,
    .interfaces = aarch64_machine_interfaces,
};

static void tb371fc_register_types(void)
{
    type_register_static(&tb371fc_machine_type);
}

type_init(tb371fc_register_types)
