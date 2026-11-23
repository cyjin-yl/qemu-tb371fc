# TB371FC machine contract

`-machine tb371fc` creates an independent ARM machine against QEMU **11.1.3**.
It uses the owner's TB371FC device-tree evidence and does not inherit the
generic `virt` memory map. The implemented digital devices support actual
kernel and driver tests. Complete stock Android and full-tablet equivalence
remain unpassed goals.

Implementation lives in [lenovo-tb371fc.c](../../hw/arm/lenovo-tb371fc.c).

The [continued validation ledger](../evidence/VALIDATION-continued-20261123.md)
separates frozen-binary measurements from later source changes. The
[47-gate isolated result](../evidence/verified-20261123-0820/public-47/result.json)
passed on SHA256 `87f52240ff55e0b60a87e9a0dc57b76dff75e653f3dbe1d5dbdc64b2b7d9ac89`.
It includes PDC 18/10, IPCC 16, SPMI 25 and the additional eight-check
SPMI/PDC/GIC route with one actual migration. Separate tests passed
852 A77 guest checks, two invalid configurations and all 15 PCI UFS cases;
the [combined receipt](../evidence/verified-20261123-0820/final-verification.json)
retains the initial noncanonical-filename discovery failure and the later
byte-identical canonical-name success. The
[original ledger](../evidence/VALIDATION.md) remains an immutable historical record.
[The source index](../SOURCES.md) links each device's precise register contract,
primary references, assumptions and executed receipts.

## Current executed scope

These are separate, versioned receipts. Their QEMU, kernel and payload hashes
identify exactly what was exercised; a later source edit does not inherit a
passing result automatically.

| Gate | Recorded evidence |
| --- | --- |
| A77 architectural CPU | [852 guest checks across nine configurations and two invalid configurations](../evidence/cpu-a77-r1p0/result.json) |
| GENI and GCC | [14 GENI groups, 6 GENI migration checks, GCC 19/5 and wired clock/reset 6/4](../evidence/expansion-20261123/revalidation-4/summary.json) |
| CPU/MMIO timer | [Exact/legacy rates, strict CPU and MMIO migration, mode rejection, and direct GIC input checks](../evidence/mmio-timer-live-status-20261123/) |
| SMMUv2 | [23 architectural C QTests](../evidence/smmuv2-20261123/qtest-result.json) |
| UFS | [56 controller/PHY/DMA tests](../evidence/resume-20261123/reports/32-ufs-register-dma.json) and [19 real Linux driver/I/O checks](../evidence/ufs-bringup-20261123/linux-strict-iommu-19.json) |
| RPMh | [22 reference/file-ABI tests and five migrations](../evidence/expansion-20261123/revalidation-5/rpmh-summary.json) |
| Watchdog | [16 groups](../evidence/watchdog-20261123/qtest-16.json) and [actual pending-bite migration](../evidence/watchdog-20261123/migration.json) |
| TLMM | [25 C QTests](../evidence/tlmm-20261123/qtest-result.json) and [15 checks across four actual migrations](../evidence/tlmm-20261123/migration-result.json) |
| IMEM / PS_HOLD | [Seven groups including actual migration](../evidence/boot-resources-20261123/qtest-migration-7.json) |
| Linux RPMh providers | [23 guest and 3 host checks](../evidence/rpmh-linux-20261123/result.json), including CCF rate, real TCS interrupt activity, completed XO transaction and cleared pending IRQ |
| Production kernel with generated DT | [Eight PID 1 checks and actual guest shutdown](../evidence/production-gdb-exact-20261123/result.json), with the unmodified production Image |
| Loader layout | [13 paused-QEMU checks](../evidence/boot-layout-20261123/runtime-layout-frozen4.json), including the corrected memory-node fixup; not a guest boot verdict |

The production kernel run uses a test initramfs and the generated subset DT.
Its configuration has no usable serial console. A read-only GDB checkpoint
collects a completed result structure from ordinary guest RAM, then removes
the breakpoint and resumes the guest. The gate requires the eight checks,
the native counter-probe output, QMP `guest-shutdown` and exit 0.
It is not an Android userspace boot. See [guest-acceptance.md](guest-acceptance.md).

The clean Linux UFS gate uses real upstream drivers, a strict translated DMA
domain and a synthetic disk. It checks guest I/O and independently compares
the host backing-file bytes. That particular test ends with host QMP quit
after the guest verdict; it does not test guest shutdown.

Frozen build 7, SHA
`f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b`,
passed TLMM 25/15, the seven boot-resource groups, ordinary UFS Linux 19 and
the reference-profile Linux 23 gate. The latter requires real RSC and
clk-rpmh binding, CCF's 19.2 MHz clock and nonzero TCS interrupt activity,
as well as UFS/SMMU I/O and independent disk verification. Its kernel SHA is
`c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`.

The full native-DT run on frozen build7 passed the previous
`msm_restart_probe+0xec` external abort after IMEM was mapped. Its next
recorded fatal point was `dcc_probe+0x114`. Later frozen10 diagnosis passed
that probe and programmed lists 3/6/7, then faulted in IPCC and concurrent
SPMI probes. The [progression receipt](../evidence/native-dt-progression-20261123/progress.json)
records each boundary without proving complete DCC capture. This advances the diagnostic path;
it does not establish a complete native-DT boot or Android userspace.
The [current replay](../evidence/resume-20261123/result.json) records later
build-specific results without replacing earlier failed receipts.

## Tablet evidence and memory map

The initial [board profile](../evidence/board-profile.json) and later
[device inventory](../evidence/expansion-20261123/android-device-inventory.json)
come from read-only DT/sysfs observations. They establish addresses and
configuration, not all register contents or hardware behavior.

| Component | Address or captured configuration |
| --- | --- |
| Identity / affinity | TB371FC, Android platform `kona`, SM8250; MPIDR `0x000` through `0x700` in steps of `0x100` |
| Visible DRAM | `0x80000000 + 0x3bb00000` and `0xc0000000 + 0x1c0000000` |
| GIC distributor | `0x17a00000`, size `0x10000` |
| GIC redistributors | `0x17a60000`, size `0x100000`, stride `0x20000` |
| System timer | 19.2 MHz; secure physical PPI 13, nonsecure physical PPI 14, virtual PPI 11, hypervisor PPI 12 |
| MMIO timer | Control `0x17c20000`; seven CNTBase windows beginning at `0x17c21000` with stride `0x2000`; frame 0 has an EL0 alias at `0x17c22000` |
| GENI console | `0x00a90000/0x4000`, SPI 357 |
| GCC | `0x00100000/0x001f0000` |
| Apps / GPU SMMU | `0x15000000/0x100000` and `0x03da0000/0x10000` |
| UFS HCI / QMP PHY | `0x01d84000/0x3000` and `0x01d87000/0x1000`, SPI 265 |
| RPMh DRVs / cmd-db | `0x18200000`, `0x18210000`, `0x18220000`; command-db mapping `0x80860000` when an explicit profile is enabled |
| KPSS watchdog | `0x17c10000`; bark/bite SPIs 0 and 1 |
| TLMM | `0x0f000000/0x01000000`, SPI 208; 180 ordinary digital GPIOs plus a separate UFS reset output |
| IMEM / PS_HOLD | `0x146bf000/0x1000` writable SRAM and `0x0c264000/4` digital hold output; no initialized OEM firmware state |
| DCC register / SRAM | `0x01023000/0x1000` and `0x0103a000/0x6000`; internal word-address offset `0x1a000`; reference map2 identity and bounded read capture |
| PSCI | SMC |

PPI and SPI numbers are DT interrupt-specifier numbers. Architectural INTIDs
are PPI + 16 or SPI + 32; for example the console uses INTID 389.
The GIC capacity is 960 SPIs, an explicit model choice rather than a sampled
GICD_TYPER. Maintenance uses PPI 9. This machine has no ITS/LPI support.

### CPU models

QEMU indices 0–3, MPIDRs `0x000`–`0x300`, use `cortex-a55`.
They remain substitutes for the physical Qualcomm MIDR `0x51df805e` /
REVIDR `0x1f`. Their emulated MIDR is not altered to impersonate Kryo.

Indices 4–7 now use the newly implemented `cortex-a77` r1p0 model, with
MIDR `0x411fd0d0` and board REVIDR `3` matching the read-only identity
evidence. This is an architectural feature/ID/PMU implementation, not a renamed
A76. The [CPU contract](cpu.md) distinguishes Arm reference behavior, tested
instructions, configurable cache descriptions and remaining native uncertainty.

The captured cache topology has private L1/L2 and a shared L3, but DT/sysfs
did not expose capacity, line-size, ways or sets. The model's cache-size
options are not measured native capacities. Neither cache timing nor DSU,
CPU frequency scaling, thermal or battery behavior is modeled.

All eight CPUs are required. CPU0 starts and the other seven await PSCI
CPU_ON. `-cpu` does not replace the fixed heterogeneous board layout.
Linux logical CPU numbers may differ from QEMU indices because of DT
enumeration order; compare MPIDRs, not only the `0-7` online mask.
Older smoke receipts that list A76 belong to the earlier model.

### RAM and loader placement

Backing RAM is contiguous 8 GiB at `0x80000000`. The Android-visible DT
excludes `[0xbbb00000, 0xc0000000)`, a 69 MiB interval. The generated
memory segments and a no-map reservation preserve this exclusion. It does
not assert missing physical DRAM and does not implement the firmware that
owns all native reserved regions.

The ARM loader normally rewrites memory nodes. A board fixup reapplies the
correct segments and avoids duplicate memory nodes. The corrected runtime
layout, including the earlier failed duplicate-node case, has a separate
[receipt](../evidence/boot-layout-20261123/runtime-layout-frozen4.json).

For RAM of at least 1 GiB, automatic raw Image allocation starts at
`0xa0000000`. The usual `0x80000` text offset therefore places text at
`0xa0080000`, consistent with the native /proc/iomem evidence.
Small component tests use 128 MiB and allocation base `0x80000000`.
`kernel-load-base` is an explicit override; ELF sections retain their
own addresses. Accepted 128 MiB–8 GiB test sizes are not additional observed
tablet configurations, and no gate has scanned every RAM page.

### Counter semantics

The default `counter-exact=on` sets both CPU `cntfrq-exact` and MMIO
`exact-frequency`. The count is `floor(virtual_ns * 19200000 / 10^9)`;
deadline conversion uses ceiling, with overflow handling. Software writes to
CNTFRQ annotate a frequency and do not retime the actual clock.

`counter-exact=off` preserves the previous 52 ns integer-period behavior,
approximately 19.230769 MHz. It exists for explicit compatibility checks.
The default exact mode has independent one-second and instruction-count
rate tests, not merely a comparison between two equally wrong counters.

MMIO frames share the physical counter timebase, but their virtual offsets
are independent of CPU CNTVOFF_EL2. Frame capability masks are inferred from
the native DT's declared frame-0 register/IRQ interfaces; native CNTTIDR
and disabled-frame capabilities were not safely measured. See
[mmio-timer.md](mmio-timer.md) for access controls, wrap behavior, migration
and the retained live-ISTATUS regression.

## Machine properties

| Property | Default / meaning |
| --- | --- |
| `counter-exact` | `on`; exact rational physical/virtual count conversion for this board |
| `kernel-load-base` | `0` selects the size-dependent allocation policy above |
| `ufs-enabled` | `on`; use `off` for independent component migration tests |
| `gcc-boot-configured` | `on`; explicit GPLL0/UART firmware handoff state, not cold silicon reset |
| `bootstrap-fabric-hz` | `100000000`; explicit reference for presently unmodeled fabric parents, not a measured rate |
| `rpmh-profile` | `off`; `reference` or `file` must be requested explicitly |
| `rpmh-cmd-db` | External file path for `rpmh-profile=file` |
| `rpmh-boot-configured` | `on` seeds the explicit DRV2 XO handoff vote when RPMh is enabled |

A file profile supplies data, not new hardware implementations. Its content
is checked, only catalogued typed resources are bound, and an unknown
request remains uncompleted. The reference database's addresses and some
auxiliary values are synthetic. A privately derived AOP partial table is
not a complete live cmd-db. See [rpmh-sm8250.md](rpmh-sm8250.md).

## Boot and diagnostic interfaces

```sh
qemu-system-aarch64 \
  -machine tb371fc -accel tcg -smp 8 -m 8G \
  -display none -monitor none -serial stdio \
  -kernel /path/to/Image -initrd /path/to/initramfs.cpio.gz \
  -append 'earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 rdinit=/init loglevel=7'
```

This specifies the direct-boot interface. A printed banner alone does not
establish SMP, interrupts, full console handoff, user space or Android boot.

The entry state is nonsecure EL2. QEMU supplies PSCI via SMC; there is no
PBL/XBL/ABL, OEM verified-boot chain, Qualcomm TrustZone or EL3 firmware.
`-bios` is rejected. `-kernel` can also load an ELF with the ARM loader's
entry contract. TCG is required for guest execution; QTest is accepted for
device tests. KVM and CPU hotplug are outside the board contract.

The built-in DT is available using the standard dump property:

```sh
qemu-system-aarch64 -machine tb371fc,dumpdtb=/tmp/tb371fc.dtb \
  -accel tcg -display none -monitor none -serial null
```

Use the same kernel/initrd/append options to inspect post-loader fixups.
An external `-dtb` with root address/size cells 2/2 is supported; supplying
the full native DT does not implement the hardware it describes.
`-d unimp,guest_errors -D /path/to/qemu.log` exposes unimplemented accesses.
Errors are not converted into successful all-zero dummy devices.

### UFS media and migration

The default board creates a UFS controller, but a block backend/LU must be
attached explicitly for storage I/O. The public
[UFS gate](../scripts/boot_ufs.py) creates a new empty 64 MiB raw disk and
attaches LU0 to `ufs0`. No native tablet partition is required.

Whole-board migration with UFS is currently blocked by the upstream core.
GENI/GCC/timer/RPMh/watchdog/TLMM/boot-resource migration receipts use
`-M tb371fc,ufs-enabled=off` and establish only their declared component
continuity. SMMU state fields alone do not establish a passed SMMU migration.
See [ufs-sm8250.md](ufs-sm8250.md) for the remaining ICE/RPMB/LU/PHY limits.

## Generated-DT compatibility choices

| Item | Implemented choice and boundary |
| --- | --- |
| Root | `qemu,tb371fc`, `lenovo,tb371fc`, `qcom,sm8250` explicitly identify the emulator |
| CPU compatibles | Actual emulated A55/A77 types; not the native generic `qcom,kryo` string |
| Bus encoding | Root and generated buses use explicit 64-bit address/size cells where needed; the captured Android /soc encoding differs |
| SMMU binding | Architectural `arm,smmu-v2` with implemented stage-1 capabilities; native `qcom,qsmmu-v500`/TCU/TBU extensions are not advertised as implemented |
| UART | `qcom,geni-debug-uart` and `serial0` select the FIFO console path; DMA/GSI remains unsupported |
| QUP wrapper | `0xac0000/0x6000` with a synthetic QUP 2.5 version value `0x20050000` at offset 4; other wrapper accesses fail |
| UART clocks | Real model GCC outputs and reset connections replace the early fixed-clock-only setup; PLL/RCG/branch changes affect the device |
| Clock roots | Native DT supplies 38.4 MHz board XO and 32 kHz sleep evidence; the GCC 19.2 MHz source and remaining fabric bootstrap assumptions are separately documented |
| RPMh resources | Nodes and command-db appear only with an explicit profile; reference addresses/aux data remain synthetic |
| TLMM and device reset | Upstream west/south/north pinctrl ranges, 180 ordinary GPIOs plus UFS_RESET group 180; active-low UFS reset is connected separately from GCC controller reset |
| IMEM / PS_HOLD | Native physical mappings exist, but the generated DT does not advertise an incomplete PMIC poweroff path; lifecycle gates use PSCI |
| Optional services | No implicit all-success SCM, regulator, interconnect, firmware or absent-peripheral service |

Instantiating the GPU SMMU does not create Adreno or display engines.
TLMM alternate-function fields do not create the underlying I2C, SPI,
display or other peripheral. IRQ/wakeup, clock and power dependencies need
their own complete routes and integration evidence.

## Reproducible clean Linux and public CI

[build_smoke_kernel.sh](../scripts/build_smoke_kernel.sh) builds independent,
unpatched Linux 6.12.114 from the official archive, pinned to SHA256
`7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3`.
It uses [linux-smoke.config](../configs/linux-smoke.config) with
`KCONFIG_ALLCONFIG` / `allnoconfig`, checks required effective options,
then builds an AArch64 Image. Source, config, compiler, linker and Image hashes
are saved beside the artifact. It does not modify another tablet-kernel tree.

The current config includes SMP/PSCI/GIC/timer, GENI/GCC/UFS/QMP/SMMU,
TLMM/watchdog and RPMh-related drivers, initramfs, ELF, procfs/sysfs and the
static PID 1's syscall requirements. Each changed configuration has its own
Image/build receipt; earlier kernel hashes do not attest later driver additions.
Strict kernel RWX and read-only mapping policy remain enabled.

[ci.md](ci.md) describes the component and Linux acceptance commands. The
workflow also builds a separate A77 configuration for its bare-metal probes.
Public CI uses no production kernel/AOP input and reports actual failures
rather than treating source presence as success.

## Historical Linux acceptance and diagnostics

The first clean-kernel console handoff ran on **2026-11-22 22:47:01 UTC**:
eight PID 1 checks, actual PSCI power-off, exit 0, 3.554 s host elapsed.
The [receipt](../evidence/linux-smoke.json) and
[serial log](../evidence/linux-smoke.log) remain unchanged.
Its QEMU SHA was `baea240b28c1428dfd42a27ad9248455dc3095f18ddb0ab0e4c7261085282a5e`,
and its kernel SHA was
`0d5bb19ab2104c8352acb1fbaa2c66d76716ec2303bbceaa764c1b9715f628ee`.
That historical model used A76 and fixed-clock compatibility aids; those are
not the current A77/GCC implementation.

That log proves full `ttyMSM0` probe, removal of `qcom_geni0` bootconsole
without `keep_bootcon`, later user-space output, SMP and the counter probe.
It still contains the unused `nokaslr` argument, disabled compile-time
Spectre-BHB mitigation and a Linux-fixed GIC dependency cycle. These are
retained diagnostics, not a claim of an error-free hardware audit.
Later repeated-TTY output and translated UFS I/O have their own logs.

The earlier research kernel exhausted its 256-page early page-table pool.
Its [failed receipt](../evidence/linux-research-kernel-failure.json) and log
remain separate. The
[optional research patch](../patches/linux-6.12-qemu-early-pgtable.patch)
is still **unapplied**; a clean upstream boot does not validate it or resolve
the original physical-tablet handoff issue.

## Remaining complete-device requirements

Adreno/GMU, DPU/DSI/DSC/panel, touchscreen and pen, keyboard, USB, microSD,
Wi-Fi/BT/PCIe/MHI, audio/DSP, camera, sensors, charging, native PMIC behavior,
SMEM/SCM/TrustZone and complete low-power/wakeup contracts remain incomplete
or absent. SMMUv2, UFS and RPMh each implement their documented subsets.
No virtio substitute is silently inserted as proof of a native peripheral.

Full native-DT kernel boot and Android end-to-end operation require actual
new implementations and independent tests. The remaining gates are tracked
in [ROADMAP.md](../ROADMAP.md).
