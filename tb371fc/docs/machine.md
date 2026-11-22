# TB371FC machine contract

`-machine tb371fc` creates an independent, experimental ARM machine based on
the owner's Lenovo TB371FC device-tree capture. It is a functional subset for
kernel and device-model development. It is not a completed tablet emulator or
a way to boot the stock Android firmware.

The machine is implemented in `hw/arm/lenovo-tb371fc.c`, against QEMU **11.1.3**.
It does not inherit QEMU's `virt` memory map. The GENI device's own contract is
documented in [geni-uart.md](geni-uart.md).

## Recorded Linux boot acceptance

On **2026-11-22 at 22:47:01 UTC**, the independently built upstream
`6.12.114-tb371fc-qemu-smoke` kernel booted with `-m 8G -smp 8`, ran the static
initramfs PID 1, passed all eight guest checks, and powered QEMU off with exit
status 0. The recorded host elapsed time was 3.554 seconds; it is a run record,
not a tablet-performance measurement. The exact command and hashes are in
[`linux-smoke.json`](../evidence/linux-smoke.json), with the full
[serial log](../evidence/linux-smoke.log).

| Observed result | What the evidence establishes |
| --- | --- |
| The two expected memory segments and `firmware-hole@bbb00000` appear in Linux's boot log | The complete 8 GiB backing configuration boots with the 69 MiB DT exclusion; no reduced-RAM workaround was used |
| PSCI 1.1 over SMC, all eight processors activated, all started at EL2 | The direct-boot PSCI `CPU_ON` path and GIC redistributor wiring bring up this SMP configuration |
| GICv3 initialized; architected physical timer runs at 19.20 MHz | Linux uses the implemented interrupt/timer path; PID 1 also checks a successful sleep of at least 20 ms and monotonic-clock progress |
| `a90000.serial: ttyMSM0 ...`, then `legacy console [ttyMSM0] enabled` | The full upstream GENI console driver probes with the synthetic wrapper/clocks |
| `legacy bootconsole [qcom_geni0] disabled`, followed by PID 1 output | The successful run omits `keep_bootcon`; userspace output survives the transition away from earlycon |
| Eight `TB371FC_CHECK PASS` records and `failures=0` | procfs, sysfs, AArch64 identity, eight CPUs online, timer/scheduler progress, fork/wait, board-model lookup, and execution of the counter-probe ELF passed |
| The probe binary matches the tablet-side ELF hash, reports 19,200,000 Hz, and its counter increases | This specific EL0 counter interface agrees with the physical-device observation; its timing and loop duration are not performance comparisons |
| PID 1 requests power-off; QEMU exits 0 without timeout | The QEMU PSCI system-off path completes this run |

This acceptance uses kernel SHA256
`0d5bb19ab2104c8352acb1fbaa2c66d76716ec2303bbceaa764c1b9715f628ee` and QEMU
SHA256 `baea240b28c1428dfd42a27ad9248455dc3095f18ddb0ab0e4c7261085282a5e`.
The identical tablet/guest counter-probe ELF is SHA256
`0ed51cb6c6dd24685beb3f77056d17d47c5c2f697df96d87eef4a678d4c5cb1b`.
The retained serial log is SHA256
`3758638a6934bba241318bf5ceb4c1796e91cb33ce8f06254112f2d2a2d7e7ef`.

### Diagnostics and limits of the successful run

The captured log has no kernel panic, Oops, BUG report, or failed guest check.
It does contain the following nonfatal diagnostics, which are not suppressed
or reclassified as an error-free hardware validation:

- `nokaslr` is an unknown boot argument because this minimal kernel already
  has `CONFIG_RANDOMIZE_BASE=n`; Linux passes the unused argument to PID 1.
- Spectre-BHB mitigation is disabled by the test kernel's compile-time config.
  This small smoke kernel is not a production security configuration.
- Linux's firmware-device-link handling reports and fixes the GIC node's
  self-dependency cycle. The interrupt controller and all redistributors then
  initialize successfully; no unresolved driver-probe failure is reported.
- Console enable/disable messages briefly appear twice during handoff, while
  both console registrations exist. After earlycon is disabled, ordinary
  printk output appears once. Buffered userspace serial output and the kernel's
  power-off message can interleave; their byte order is not a timing model.

The run establishes Linux driver probe and userspace serial TX, but does not
exercise Linux serial RX, measure UART interrupt counts/latency, stress the
serial driver, scan every RAM page, test suspend/resume, or boot Android. The
command did not enable QEMU's `-d unimp` tracing, so absence of errors in this
log is not an exhaustive audit of unimplemented MMIO accesses. The independent
GENI QTest/migration suites cover their own explicitly listed register-state
scenarios; they do not extend this Linux run's scope by implication.

## What comes from the tablet

The recorded evidence is `tb371fc/evidence/board-profile.json`, derived from a
read-only Android device-tree/sysfs capture. The profile establishes addresses
and configuration; it does not prove register behavior or emulator fidelity.

| Component | Captured configuration used by the machine |
| --- | --- |
| Identity | TB371FC; Android platform `kona`; SoC `SM8250` |
| CPU affinity | Eight CPU nodes, MPIDRs `0x000` through `0x700` in steps of `0x100` |
| Visible DRAM | `0x80000000` + `0x3bb00000` bytes and `0xc0000000` + `0x1c0000000` bytes |
| GIC distributor | `0x17a00000`, size `0x10000` |
| GIC redistributors | `0x17a60000`, size `0x100000`; stride `0x20000` |
| Architected timer | 19.2 MHz; secure physical PPI 13, nonsecure physical PPI 14, virtual PPI 11, hypervisor PPI 12 |
| Active GENI console | `0x00a90000`, size `0x4000`, SPI 357 |
| PSCI transport | SMC |

PPI and SPI numbers in the table are interrupt-specifier numbers: the GIC
architectural interrupt ID is PPI + 16 or SPI + 32. The UART is therefore
architectural interrupt ID 389. The GIC model offers 960 SPIs; that capacity is
a model choice, not a measured `GICD_TYPER` value. GIC virtualization maintenance
uses PPI 9. ITS and LPIs are not implemented in this machine.

### CPU substitution is explicit

QEMU CPU indices 0–3, with MPIDRs `0x000`–`0x300`, use upstream QEMU's
`cortex-a55`. QEMU indices 4–7, with MPIDRs `0x400`–`0x700`, use `cortex-a76`
as an architectural surrogate because upstream QEMU 11.1.3 has no
`cortex-a77` model.
The physical device reported MIDRs `0x51df805e` for CPUs 0–3 and `0x411fd0d0`
for CPUs 4–7. The model does not falsify its emulated MIDR to match those values.
Its generated CPU `compatible` properties describe the actual emulated CPU
types, rather than repeating the Android DT's `qcom,kryo` strings.

All eight CPUs are required. The first CPU starts; the remaining seven wait
for PSCI `CPU_ON`. This is an instruction-level TCG model, without measured
cache behavior, CPU frequencies, power management, or cycle timing. It cannot
establish Snapdragon performance, thermal, or battery behavior. `-cpu` is not
a supported way to change the heterogeneous configuration.

Linux assigns its own logical CPU numbers while enumerating the generated DT.
In the recorded smoke boot, its order was MPIDR `0x000`, then `0x700`, `0x600`,
`0x500`, `0x400`, `0x300`, `0x200`, `0x100`. Consequently Linux CPU0 and CPUs
5–7 were the A55 models, while Linux CPUs1–4 were A76 models. This is DT node
enumeration order, not an MPIDR/type mismatch. Compare physical affinity values
when relating a Linux logical CPU to the QEMU index or Android capture; the
`0-7` online-CPU check by itself does not verify matching logical numbering.

### DRAM backing and the Android exclusion

The default backing RAM is 8 GiB at physical address `0x80000000`. The Android
DT excludes `[0xbbb00000, 0xc0000000)`, a 69 MiB interval. The model preserves
this exclusion in the guest's `/memory` property and adds a `no-map` reserved
memory node. This does **not** assert that the physical DRAM is absent there:
the backing RAM remains contiguous. No secure firmware is emulated in that
interval, and it does not reproduce the tablet's complete reserved-memory map.

The normal ARM loader rewrites `/memory` while loading a kernel. A board DT
fixup reapplies the two visible segments afterward, so an actual boot uses the
same geometry as the machine's generated DT. Smaller explicit RAM sizes from
128 MiB through 8 GiB are accepted for tests; the visible segments and exclusion
are clipped to that smaller aperture. They are testing configurations, not
additional observed TB371FC hardware profiles.

## Boot contract

Use a Linux AArch64 `Image`, an appropriate initramfs, and the generated DT:

```sh
qemu-system-aarch64 \
  -machine tb371fc -accel tcg -smp 8 -m 8G \
  -display none -monitor none -serial stdio \
  -kernel /path/to/Image -initrd /path/to/initramfs.cpio.gz \
  -append 'earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 rdinit=/init loglevel=7'
```

The example specifies the intended interface; record the actual boot result
for the kernel/initramfs and QEMU build under test. A printed early console
message alone does not prove that SMP, interrupts, the full serial driver,
userspace, or Android has booted.

The kernel enters at nonsecure EL2. QEMU implements PSCI calls over SMC; there
is no Qualcomm TrustZone, EL3 firmware, PBL, XBL, ABL, AVB verification, or OEM
boot chain. `-bios` is rejected. The standard ARM loader also accepts an ELF
payload through `-kernel`, but the payload must meet that loader's entry-point
contract rather than expecting Qualcomm boot firmware.

TCG is required for guest execution. QTest is also accepted for direct device
tests. The default RAM is intentionally large enough to match this tablet;
the device tests can pass `-m 128M` to avoid an unnecessary 8 GiB migration
scan. KVM and CPU hotplug are outside this machine's contract.

The built-in DT is available through the standard machine dump property:

```sh
qemu-system-aarch64 \
  -machine tb371fc,dumpdtb=/tmp/tb371fc.dtb \
  -accel tcg -display none -monitor none -serial null
```

Adding the same `-kernel`, `-initrd`, and `-append` options used for boot checks
the DT after the ARM loader has updated it. An external `-dtb` is supported by
the loader, with a required root address/size cell count of 2/2 and the same RAM
fixup. A stock tablet DT advertises many devices that are absent in this
machine; supplying it does not implement those devices.

## Reproducible smoke kernel input

[`build_smoke_kernel.sh`](../scripts/build_smoke_kernel.sh) builds a separate,
unpatched Linux 6.12.114 tree from the official
[kernel.org source archive](https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.114.tar.xz).
The archive is pinned to SHA256
`7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3`.
It uses [`linux-smoke.config`](../configs/linux-smoke.config) as
`KCONFIG_ALLCONFIG` input to `allnoconfig`, checks the effective requirements,
then builds only the AArch64 `Image`:

```sh
SMOKE_JOBS=4 tb371fc/scripts/build_smoke_kernel.sh /work/clean-linux
```

The builder needs GCC and Make for host tools, the
`aarch64-linux-gnu-gcc`/binutils cross toolchain, `bc`, `bison`, `flex`,
`libssl-dev`, `libelf-dev`, Python 3, tar, xz, and SHA256 tools. Curl is used
only if the pinned archive is not already in the work directory's download
cache. Sources, configuration, and output stay in the chosen work directory;
the script does not alter another tablet-kernel checkout.

Artifacts are written to `/work/clean-linux/artifacts`: `Image`, the input
fragment, effective `kernel.config`, `build.json`, and `SHA256SUMS`.
`build.json` records source/config/compiler/linker/Image hashes, the actual
commands, and deterministic Kbuild identity variables. The kernel release is
`6.12.114-tb371fc-qemu-smoke`. A successful build is recorded separately from
the subsequent guest boot checks.

The config includes eight-CPU SMP, PSCI, the GIC/timer, GENI console,
procfs/sysfs, ELF execution, an external gzip initramfs, and the syscalls needed
by the static libc PID 1. It retains strict kernel RWX and full read-only
mapping policy. The full GENI driver calls OPP clock helpers even when no OPP
table is described in DT; `PM_DEVFREQ` selects the otherwise hidden `PM_OPP`
framework in upstream Kconfig. This enables Linux's clock helper code, without
adding fictitious voltage/frequency hardware to the machine.

### Research kernel result remains separate

The earlier `6.12.114-tb371fc-research1` input reached the early console but
panicked in `paging_init()` with `TB371FC embedded early page-table pool
exhausted`. Its result and log remain in
[`linux-research-kernel-failure.json`](../evidence/linux-research-kernel-failure.json)
and [the failure log](../evidence/linux-research-kernel-failure.log).
That input's vendor-kernel kexec workaround used a 256-page static pool and
matched the `lenovo,tb371fc` fallback compatible. A direct QEMU boot does not
reproduce the vendor handoff for which the workaround was written.

[`linux-6.12-qemu-early-pgtable.patch`](../patches/linux-6.12-qemu-early-pgtable.patch)
is an optional, **unapplied** proposal to exclude the explicit `qemu,tb371fc`
compatible from that research-kernel workaround. The successful clean-kernel
input uses the unpatched upstream allocator instead. Its success does not
validate the proposed patch or resolve the original physical-tablet handoff
issue. The research kernel's failed result has not been relabeled as a pass.

## Deliberate Linux compatibility choices

Only the CPUs, GIC, timer, UART, its minimal wrapper, and fixed clocks are
advertised. In addition to the CPU substitution, the following choices are
deliberately different from the captured Android DT:

| Item | Generated DT / model choice | Reason and limit |
| --- | --- | --- |
| Root compatibility | `qemu,tb371fc`, `lenovo,tb371fc`, `qcom,sm8250` | Identifies the emulator explicitly |
| Bus encoding | Root, `/soc`, and QUP wrapper use 2 address cells and 2 size cells | Normalizes 64-bit addresses; Android `/soc` used 1/1 |
| Timer flags | Level-low flag `8` | Omits Android's legacy GICv2 CPU-mask bits from `0xff08` |
| UART binding | `qcom,geni-debug-uart`, `serial0` | Selects the upstream Linux FIFO console driver; no high-speed DMA support |
| QUP wrapper | `0x00ac0000`, size `0x6000`; only offset `0x4` is readable | Supplies a **synthetic** QUP 2.5 version value, `0x20050000`; other wrapper accesses return a bus error |
| AHB clocks | One synthetic fixed 19.2 MHz provider for `m-ahb` and `s-ahb` | No GCC, RPMh, voltage voting, or clock/power gating |
| SE clock | Synthetic fixed 7.3728 MHz | Gives the Linux 2.5-compatible UART path an exact 115200-baud divisor; no physical baud timing |
| Optional resources | No invented pinctrl, power-domain, interconnect, or OPP providers | Linux must tolerate their absence; this does not emulate their hardware |

The QUP wrapper and fixed clocks are small compatibility aids for the
upstream Linux GENI driver. They must not be used as a reference for actual
SM8250 register reset values, clock rates, or subsystem behavior. The UART's
16-word FIFO geometry and protocol identity are model choices described in
its separate document.

## Important missing hardware

There is no UFS boot/storage controller, Qualcomm SMMU, Adreno GPU, display
pipeline, touchscreen, USB controller/PHY, Wi-Fi, Bluetooth, audio/DSP, camera,
sensors, modem, PMIC, regulator, RPMh/RSC, AOSS, SMEM, SCM/TZ firmware interface,
or battery/charging model. No virtio storage, network, or other diagnostic
peripheral is implicitly inserted. Drivers for absent devices cannot be
validated against this machine. The unimplemented regions are not a collection
of successful dummy devices.

A complete stock-Android or tablet model requires substantial additional
device/firmware work and independent validation. Passing the profile, UART,
migration, or Linux initramfs checks establishes only what those checks execute.

## Primary implementation references

- [QEMU 11.1.3 ARM boot loader](https://github.com/qemu/qemu/blob/v11.1.3/hw/arm/boot.c): kernel/DT/initrd loading and QEMU PSCI boot setup.
- [QEMU 11.1.3 SBSA reference board](https://github.com/qemu/qemu/blob/v11.1.3/hw/arm/sbsa-ref.c): upstream GICv3 and ARM CPU wiring patterns.
- [QEMU 11.1.3 AArch64 CPU definitions](https://github.com/qemu/qemu/blob/v11.1.3/target/arm/tcg/cpu64.c): emulated CPU types and identification.
- [Linux v6.12 GENI wrapper](https://github.com/torvalds/linux/blob/v6.12/drivers/soc/qcom/qcom-geni-se.c): wrapper version, clock/resource setup, and FIFO initialization.
- [Linux v6.12 GENI serial driver](https://github.com/torvalds/linux/blob/v6.12/drivers/tty/serial/qcom_geni_serial.c): console probe, FIFO operations, and baud-rate selection.
- [Linux v6.12 SM8250 DT](https://github.com/torvalds/linux/blob/v6.12/arch/arm64/boot/dts/qcom/sm8250.dtsi): SoC-level reference, not a replacement for this tablet's captured configuration.
