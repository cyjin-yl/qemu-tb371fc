# TB371FC machine contract

`-machine tb371fc` creates an independent, experimental ARM machine based on
the owner's Lenovo TB371FC device-tree capture. It is a functional subset for
kernel and device-model development. It is not a completed tablet emulator or
a way to boot the stock Android firmware.

The machine is implemented in `hw/arm/lenovo-tb371fc.c`, against QEMU **11.1.3**.
It does not inherit QEMU's `virt` memory map. The GENI device's own contract is
documented in [geni-uart.md](geni-uart.md).

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

CPUs 0–3 use upstream QEMU's `cortex-a55`. CPUs 4–7 use `cortex-a76` as an
architectural surrogate because upstream QEMU 11.1.3 has no `cortex-a77` model.
The physical device reported MIDRs `0x51df805e` for CPUs 0–3 and `0x411fd0d0`
for CPUs 4–7. The model does not falsify its emulated MIDR to match those values.
Its generated CPU `compatible` properties describe the actual emulated CPU
types, rather than repeating the Android DT's `qcom,kryo` strings.

All eight CPUs are required. The first CPU starts; the remaining seven wait
for PSCI `CPU_ON`. This is an instruction-level TCG model, without measured
cache behavior, CPU frequencies, power management, or cycle timing. It cannot
establish Snapdragon performance, thermal, or battery behavior. `-cpu` is not
a supported way to change the heterogeneous configuration.

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
  -append 'earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 keep_bootcon rdinit=/init loglevel=7'
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
