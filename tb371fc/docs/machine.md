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
primary references, assumptions and executed receipts. The
[default-branch history index](../evidence/DEFAULT-HISTORY-5fd52c1.md) also retains
frozen12 and generic-build receipts published in `5fd52c1`; these remain
measurements of their original binaries, not results for a later commit.

## Explicit boot-resource profiles

The current development branch adds the following opt-in properties. They
describe reference contracts, not captured SBL state or AOP firmware execution.

| Option | Contract |
| --- | --- |
| `qcom-cpufreq-hw-epss.enabled-reference=on` | Explicit four-state all-XO LUT, PERF, limit IRQ and fractional virtual-cycle model on three actual-DT domains; [contract](cpufreq-hw-reference.md). Default unknown rejects MMIO. `reference-boot-perf-index=0` explicitly selects the reference initial row. |
| `qcom-devfreq-fw.enabled-reference=on` | Explicit L3 firmware-handoff reference with four synthetic nominal request indices; 42 words in three actual DT windows. `reference-boot-perf-index=0` selects reset request zero. Default unknown rejects MMIO; no physical cache Clock, IRQ, timer, counter or firmware execution is modeled. [Contract](devfreq-qcom-fw-reference.md). |
| `qcom-noc-qos.enabled-reference=on` | Explicit four-word configuration reference at 47 actual-DT ports; default off rejects reads and writes. No traffic, power or SBM acknowledgement is synthesized. |
| `smem-profile=off` or `smem-profile=reference-empty` | Default off; the reference initializes the documented empty SMEM v12 handoff. |
| `aop-profile=none` or `aop-profile=reference-qdss-vote` | Default none retains raw SRAM. The reference adds real IPCC link/channel processing and only QDSS/QDSS_AO off/dynamic votes. |
| `qcom-sm8250-npucc.enabled-reference=on` | Explicit three-bank digital PLL/RCG/branch/reset profile; [contract](npucc-sm8250.md). Default MMIO errors, unknown parents remain stopped; no NPU/DSP/GDSC execution claim. |
| `qcom-sm8250-gpucc.enabled-reference=on` | Explicit source-bounded digital PLL1/GMU/branch/reset model; [contract](gpucc-sm8250.md). Default MMIO errors, no seeded GPU frequency or GPU execution. |
| `qcom-gpu-cx-gdsc.boot-collapsed-reference=on` | Explicit collapsed CX handoff; connected RPMh `cx.lvl` remains its parent input. |
| `qcom-gdsc.enabled-reference=on` | Explicit GX digital reference state; `gfx.lvl` is its distinct parent. |
| `qcom-refgen.boot-disabled-reference=on` | Explicit REFGEN disabled handoff. |
| `qcom-tcsr-mutex.boot-unlocked-reference=on` | Explicit unlocked mutex handoff. |

The `qcom-...` entries are device `-global` properties. See [SMEM](smem-reference.md),
[AOP reference peer](aop-reference-peer.md), [CX](gpu-cx-gdsc.md) and
[GX](gpu-gx-gdsc.md) for exact state, unsupported inputs and migration scope.
GCC v3 deliberately rejects older GCC migration streams; UFS still prevents
a full-board migration claim.

## NoC QoS configuration increment

The [NoC QoS reference](noc-qos-reference.md) adds one SysBus device per physical QoS port: 47 instances and 188 individual four-byte windows. Only the actual-DT ports and four source-defined offsets are mapped. Display aliases share the same physical state; the deferred IPA port is available but not preprogrammed. Legal writes replace complete words, including the pinned vendor driver's destructive RMW effects; unsupported bits reject atomically. Reset-to-zero is an explicit reference policy. VMState 1 binds the selected profile and immutable physical identity, and checks the restored masks. Raw QOM register observations are read-only. The combined source actually passed the 70-gate plan and [NoC's 20-check/two-transfer/two-profile/two-corruption gate](../evidence/wholeboard-20261123-a47/noc-qos-99ef.json). The exact native run observed all 47 addresses and matching stored configuration on all 46 nondeferred ports; deferred IPA remained zero. This does not establish traffic or SBM operation.

## New MPM counter and SMMUv2 integration

The board now creates an independent [MPM2 sleep counter](mpm2-sleep-counter.md) at `0x0c221000..0x0c221003`. It uses the actual DT's fixed 32768-Hz declaration, with overflow-safe virtual-time arithmetic and preserved fractional phase in VMState 1. Cold zero and continuity across software reset are explicit reference policies. The remaining DT window is unmapped, and the 32000-Hz GCC sleep input is independent.

The integrated [SMMUv2 migration contract](smmuv2-migration.md) from default commit `339b9cd277aeae84fba52b60ae34ede90b7ff101` requires version 2 and matching MMIO aperture, context-bank count and SMR capacity. Earlier unguarded version-1 streams are rejected. This is component migration with UFS disabled, not a claim that the complete running board migrates. The combined source actually passed [65 component gates and four Linux boots](../evidence/wholeboard-20261123-a47/public-ci-0508.json), plus the independent A77/UFS job. MPM passed 17 checks, two completed transfers and one deliberate stream-corruption rejection; SMMUv2 passed 18 checks, two completed transfers and two real capacity-mismatch rejections.

## Frozen unified 99ef native checkpoint

The [70-gate binary](../evidence/wholeboard-20261123-a47/public-ci-99ef.json) with SHA256 `226f826f02a97978f9f0afbcc1241dbcd6c25faca4840f8e1f72cee22f3d0000` ran the same unmodified production Image and full DT. [Its native receipt](../evidence/wholeboard-20261123-a47/native-99ef.json) records the stopped 47-port NoC snapshot, 46 nondeferred configuration matches, SMEM 23/23, supported AOP exchange and GCC/GPUCC/NPUCC registration. MPM printed timestamp 125417 and frequency 32768 Hz. The next actual fatal is `qsmmuv500_arch_init+0x88`, with QEMU directly identifying physical read `0x15182018`. It is apps TCU `0x15182000 + 0x18`; the [source/DT contract](smmuv500-source-contract.md) distinguishes VERSION, HALT, ECATS, debug interfaces and unmeasured hardware assumptions. This binary does not implement the vendor TCU/TBU extension. The earlier KGSL SMMU probe returned -16 after its GCC memory-NoC clock stayed off, and VIDEOCC remained deferred. No clock parent or firmware handoff was fabricated to suppress those observations. Native userspace, Android and native SMMU DMA still did not pass.

## Earlier unified 0508 native checkpoint

The exact `0508fe9d5382b3e94ee0664012ff1e6f0457e5b7` CI artifact's [production Image/full-DT run](../evidence/wholeboard-20261123-a47/native-0508.json) registered GCC, GPUCC and NPUCC, observed SMEM success (23/23) and the supported AOP reference exchange, then completed the boot-stat counter read: timestamp 112563 and frequency 32768 Hz. No bootloader values were fabricated; the four existing IMEM KPI values remained zero. The next fatal is a 32-bit read in `msm_bus_noc_qos_init+0x48`. QEMU `invalid_mem` directly records physical address `0x016e9008`, followed by the CPU5 DataAbort with matching FAR and ELR. The [bounded original-DT evidence](../evidence/wholeboard-20261123-a47/noc-dt-evidence-0508.json) and embedded `CONFIG_QCOM_BUS_CONFIG_RPMH=y` identify Aggre1 port 7 QoS MAINCTL. NoC is not implemented in this binary; its [47-port layout](../evidence/wholeboard-20261123-a47/noc-layout-0508.json) is the next bounded contract. VIDEOCC remains deferred for the unavailable bus driver. Full native-DT userspace and Android remain unpassed.

## Earlier unified NPUCC checkpoint

Commit `d79052f306ec37a797d05c9e4b2f8f7ea69c0675` passed [63 component gates and four Linux boots](../evidence/wholeboard-20261123-a47/public-ci-d790.json), plus the separate A77/UFS job. The exact artifact binary's [production Image and full native-DT run](../evidence/wholeboard-20261123-a47/native-d790.json) registered GCC, GPUCC and NPUCC, observed SMEM success (23/23) and a supported AOP reference exchange, then faulted on a 32-bit read in `boot_stats_init+0x14c`. Pinned source and the actual DT correlate that read with the MPM sleep-counter resource at `0x0c221000`, declared 32768 Hz. The fault log contains a virtual address; no separate VA-to-PA translation was captured. This frequency is distinct from the 32000-Hz `chip_sleep_clk`. VIDEOCC was deferred because its bandwidth-voting bus driver was not ready.

The later minidump/PIL warnings match item 602 exceeding the reference SIII item count of 512. Socinfo item 137 is in range but absent in the empty global partition, producing a separate dummy-data fallback. The pinned sources and exact model contract are linked in the native receipt; the production Image's exact public source tree is unproven. No records were invented to suppress these messages. These successful controller probes do not establish full native-DT userspace or GPU/NPU execution. GCC's NPU configuration-AHB parent remains unproven and its warning is retained. The preceding [df667 receipt](../evidence/wholeboard-20261123-a47/native-df667.json) preserves its own NPUCC MMIO failure.

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
| Display RSC | `0x0af20000` / `0x10000`, TCS offset `0x1c00`, two banks, SPI 129; shares the enabled RPMh profile |
| KPSS watchdog | `0x17c10000`; bark/bite SPIs 0 and 1 |
| TLMM | `0x0f000000/0x01000000`, SPI 208; 180 ordinary digital GPIOs plus a separate UFS reset output |
| IMEM / PS_HOLD | `0x146bf000/0x1000` writable SRAM and `0x0c264000/4` digital hold output; no initialized OEM firmware state |
| DCC register / SRAM | `0x01023000/0x1000` and `0x0103a000/0x6000`; internal word-address offset `0x1a000`; reference map2 identity with bounded READ, single-word WRITE, flat LOOP and single-register RMW |
| PDC | `0x0b220000/0x30000`, SPI-type bitmap `0x17c000f0/0x60`; 138 DT-mapped pins, including pin 1 → SPI 481 |
| IPCC APPS | `0x00408000/0x1000`, SPI 229; local client ID 8 from the captured APPS self-ping node |
| SPMI arbiter | Five native windows below, mapped only with `spmi-profile=reference`; IRQ output through PDC input 1 |
| PSCI | SMC |

The explicit SPMI reference profile maps these captured CPU physical ranges:

| SPMI region | Base | Size |
| --- | --- | --- |
| core | `0x0c440000` | `0x1100` |
| chnls | `0x0c600000` | `0x02000000` |
| obsrvr | `0x0e600000` | `0x00100000` |
| intr | `0x0e700000` | `0x000a0000` |
| cnfg | `0x0c40a000` | `0x00026000` |

The window addresses are DT observations; arbiter v5 identity, APID numbering
and ownership are explicit reference inputs. They are not measured reset values.
The [SPMI contract](spmi-pmic-arb.md) distinguishes the native-address controller
from its reference identities and optional synthetic peripheral fixture.

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
| `spmi-profile` | `off`; `reference` explicitly enables the bounded v5 arbiter/identity model; it does not enable a complete PMIC or fabricate generated-DT PMIC children |
| `rpmh-profile` | `off`; `reference` or `file` must be requested explicitly |
| `rpmh-cmd-db` | External file path for `rpmh-profile=file` |
| `rpmh-boot-configured` | `on` seeds the explicit DRV2 XO handoff vote when RPMh is enabled |

A file profile supplies data, not new hardware implementations. Its content
is checked, only catalogued typed resources are bound, and an unknown
request remains uncompleted. The reference database's addresses and some
auxiliary values are synthetic. A privately derived AOP partial table is
not a complete live cmd-db. See [rpmh-sm8250.md](rpmh-sm8250.md).

### DCC, PDC, IPCC and SPMI boundaries

[DCC](dcc.md) implements reference-map2 SRAM capture and propagates source
transaction failures. Its [operation extension](dcc-operations.md) also performs
bounded single-word WRITE, flat finite LOOP and single-register RMW operations
through the actual address space. CRC/ATB/CTI, nested loops, broader write/RMW
formats and complete execution of the native three-list workload remain
unsupported or unverified.

[PDC](pdc.md) converts the five legacy level/edge modes into the captured GIC
routes. Its default VERSION read fails because native identity is unmeasured;
mainline experiments can explicitly select
`-global qcom-pdc.reference-version=0x20000`. The parent GIC owns pending/EOI.

[IPCC](ipcc.md) implements pending latches, masks, clear-on-read and APPS self
routing. An absent remote endpoint produces no invented incoming message.
Its priority ordering and sparse capacity are explicit model choices.

The [SPMI reference model](spmi-pmic-arb.md) performs bounded 1–8-byte
transactions with status/error and ownership checks. Known or source-derived
primary PMIC identity bytes are separate from unknown revision fields, which
fail. Native APID/ownership and raw chip versions remain unmeasured. The
optional SID15 IRQ peripheral is an explicit synthetic controller fixture;
its tests prove the SPMI→PDC→GIC route, not real regulator, ADC, charger or PON
behavior. No power-domain wake or secure-firmware completion is implied.

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
GENI/GCC/timer/RPMh/watchdog/TLMM/boot-resource/DCC/PDC/IPCC/SPMI migration receipts use
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
| DCC / PDC / IPCC / SPMI | Native-address component models and explicit test profiles do not advertise nonexistent PMIC children or remote firmware services; reference SPMI is off by default |
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

## Display RSC follow-up

The four-voter APPS/display model passes [49 public gates](../evidence/display-rsc-20261123/public-49/result.json) and [53 targeted checks](../evidence/display-rsc-20261123/tests-final.json). Version 2 RPMh migration is required; legacy three-voter streams are rejected. See [display RSC behavior and reference assumptions](rpmh-display-rsc.md). That native run failed later in qsee_irq_probe; the subsequent run below records the next faults.

## Historical QSEE notification and DCC operation follow-up

The [52-gate frozen result](../evidence/qsee-dcc-20261123/public-52/result.json) and [51 targeted checks](../evidence/qsee-dcc-20261123/workflow.json) pass. QSEE maps only 0x20-byte banks at 0x01886000 and 0x01888000, with real GIC SPI348/349 outputs; the rest of that syscon is not a success stub. DCC executes the documented bounded operation subset as actual bus transactions. See [QSEE](qsee-irq.md) and [DCC operations](dcc-operations.md). That frozen binary's production-kernel/full-DT run progressed past QSEE but failed at AOP shared-memory and REFGEN reads. The later explicit AOP reference peer has its own separately scoped validation; TEE/SCM, native firmware handoff and Android userspace remain unimplemented or unverified.

## Default boot-resource contracts and earlier validation

| QOM device | Mapped resource | Contract |
| --- | --- | --- |
| /machine/tcsr-mutex | 0x01f40000 / 0x20000 | 32 owner words, 0x1000 stride; initial owners unknown unless explicit unlocked reference |
| /machine/aop-msgram | 0x0c300000 / 0x1000 | Migrating RAM; reference cold zero and reset retention under default `aop-profile=none`; no peer in that profile |
| /machine/refgen | 0x088e7080 / 4 | CTRL5 bit 0 software request with validity; initial request unknown unless explicit disabled reference |

The [50 targeted checks and 55 public gates](../evidence/boot-resources-20261123-1005/publication.json) passed on one frozen binary.
[Native observations](../evidence/boot-resources-20261123-1005/native-progression.json) separate the default REFGEN read abort from the later GDSC abort under explicit reference state.
The three raw/default resource models above do not claim SMEM initialization, secure firmware, QMP remote service, analog reference voltage, or native reset values. The explicitly enabled [SMEM handoff](smem-reference.md) and [AOP reference peer](aop-reference-peer.md) are separate models with the bounded contracts described above.

## Apps SMMUv500 reference companion

The [bounded companion](smmuv500-reference.md) adds apps TCU VERSION at `0x15182018` and ten original-DT TBU debug/status interfaces. It requires both `-global qcom-smmuv500.enabled-reference=on` and `-global qcom-smmuv500.reference-hw-version=0x02000000`. The value identifies an explicit modeled protocol, not measured silicon. Default accesses fail as unknown; KGSL vendor windows remain unmapped. HALT denies new ordinary DMA and invalidates real external IOMMU caches. ECATS uses the existing core's SID matching, page walk, permissions and faults while bypassing only HALT. Its 1000-ns delays, cold state and synchronous DMA-denial policy are declared reference assumptions. No native firmware contexts or unknown power dependencies are synthesized. This increment actually passed the 67-component/four-Linux plan on `3ef98a8afb49a873d677288693d6491c50fd75e1`; [the receipt](../evidence/wholeboard-20261123-a47/public-ci-3ef98.json) records its own binary. The same-binary [native run](../evidence/wholeboard-20261123-a47/native-3ef98.json) passed the former TCU abort and continued through TBU resource registration to SCSI/USB, then stopped at the TSENS read `0x0c222004`. Its cold TBU snapshot does not prove native DMA. Four MNOC GDSC control words already belong to GCC power domains 7–10; they are not new unmapped holes. The companion does not couple TBU traffic to those domain outputs.

## TSENS24xx digital reference

The [TSENS24xx model](tsens24xx-reference.md) creates `/machine/tsens0` and `/machine/tsens1` at the two actual-DT controller locations, with upper/lower SPIs 506/507 and critical SPIs 508/509. It maps only sourced 32-bit registers: SROT +4, TM +4..+dc and TM +e4. The original DT SROT length of four bytes is preserved as evidence; its driver accesses +4 through a page-rounded mapping. Unspecified words remain holes.

The default model rejects MMIO as unknown. A diagnostic must explicitly select `-global qcom-tsens24xx.enabled-reference=on`, and any chosen initial sensor mask and temperature require `reference-boot-enable-mask` and `reference-temperature-millicelsius`. The actual 52492a native comparison selected `0xffff` and `25000`, respectively; neither value is measured tablet handoff. Host per-sensor temperature/valid properties are external reference stimuli available during normal TCG. A 1-ms virtual sampling round captures those inputs, later publishes signed 12-bit samples with VALID, and establishes vendor TRDY only after a complete known round. Threshold status, clear/mask behavior, IRQ output, reset and pending migration are stateful. No analog calibration, 0C/min, SCM reset, watchdog or cycle-monitor producer is synthesized.

The 68-component/four-Linux plan actually passed on source `52492a797d2acd9c918a8639564e8ff3e7beab3f`; [the receipt](../evidence/wholeboard-20261123-a47/public-ci-52492a.json) identifies its exact binary. TSENS passed 34 checks, two completed process transfers, two profile mismatches and two single-field corruption rejections. In the same-binary [full native-DT diagnostic](../evidence/wholeboard-20261123-a47/native-52492a.json), both controllers completed 16670 reference sample rounds and all 32 modeled channels had coherent valid samples. Two virtual thermal-zone EINVAL messages remain. The former TSENS abort disappeared; the next actual fatal reads physical `0x18591100` in `qcom_cpufreq_hw_driver_probe+0x408/0x980`. This is progress through reference digital interfaces, not calibrated temperature, native userspace or Android acceptance. The preceding `3ef98` failure and all older receipts retain their original scope.

## Concurrent SMMUv2 granule integration

Default branch `69ca28dec6add998d252fa6436dfcc9b9cab6e36` adds explicit 16/64 KiB architecture profiles and core VMState 3. A three-way merge preserves this branch's shared ECATS walker, HALT/DMA blocker and real cache invalidation hooks. The [independent source review](../evidence/wholeboard-20261123-a47/smmuv2-granule-merge-review.json) records a conflict-free byte-identical merge. Both board SMMUs still default to mask 1 (4 KiB). The companion keeps VMState 1 and its 744-byte payload; its migration test now also reads both actual core v3 headers, geometry and mask 1. All 36 companion checks and the 26-check granule gate actually pass in the new 1fe4e1 unified combination; its five companion streams also passed independent core-v3 prefix checks. Prior 3ef98/52492a companion results used core v2 and remain scoped to those binaries. The parallel branch's original CI and legacy-v2 receipts are retained unchanged; neither establishes optional-granule native hardware support or complete-board migration.

## CPUFreq hardware EPSS reference

The board creates `/machine/cpufreq0..2` at `0x18591000`, `0x18592000` and `0x18593000`, using the actual CPU masks and SPI30/31/19. Only 263 sourced words in 24 windows are mapped. The source's max_cores argument is 4 for every domain, while counter slots follow the actual 4/3/1 related CPUs. With the explicit profile, four synthetic all-XO LUT states drive a real output Clock, requested PERF is distinct from a host-supplied frequency cap, and an edge-latched status is acknowledged through W1C. The unsupported interrupt-enable register and unused aggregate counter address remain holes. The [contract](cpufreq-hw-reference.md) states the synthetic voltages, cold state, all-core reference activity and counter/migration policies. The [read-only observer review](../evidence/wholeboard-20261123-a47/cpufreq-observer-review.json) covers stopped QOM observation, not native driver success. The [1fe4e1 formal run](../evidence/wholeboard-20261123-a47/public-ci-1fe4e1.json) actually passes this 30-check gate (2 completed, 2 profile and 2 corruption cases), all 70 components and four Linux gates. The exact board binary is `4edcf24e59963b2e33942c042cdd04f8a96c2d6faf6891dfd372b8eb219f5b95`. In its [full-DT native diagnostic](../evidence/wholeboard-20261123-a47/native-1fe4e1.json), printk explicitly reports CPUFreq up and running. All three stopped reference domains are coherent; actual physical CPU speed/voltage, native IRQ delivery and userspace remain unproved. The next fatal is the first L3 devfreq enable read at `0x18590000`, in `devfreq_qcom_fw_driver_probe+0x94/0x4e8`.

## L3 firmware devfreq request reference

The board creates `/machine/devfreq-l3` with only `0x18590000/4`, `0x18590100/160` and `0x18590320/4`. Four actual-DT child voters are guest software consumers of the one parent request register. The [contract](devfreq-qcom-fw-reference.md) defines the enabled handoff, synthetic LUT, exact access boundaries, reset policy, pure read-only QOM observations and 72-byte VMState v1. The 20-check public gate and same-binary native follow-up are pending for this newly integrated source. The last actual native result remains the 1fe4e1 L3 enable-read abort recorded above.
