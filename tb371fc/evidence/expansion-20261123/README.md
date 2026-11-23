# Hardware dependency expansion — 2026-11-23

This directory records read-only observations from the owner's running TB371FC.
It extends the original CPU/GIC/timer/UART profile to peripheral configuration,
driver bindings and UFS descriptors. It does not attest that those peripherals
are emulated, that a driver binding proves a working peripheral, or that the
current boot backup is a pristine factory image.

## Evidence and provenance

| File | Observation and interpretation |
| --- | --- |
| [android-device-inventory.json](android-device-inventory.json) | All 4,276 node paths from the private running DTB; 10,747 allowlisted properties and 1,930 phandle providers. Properties retain original bytes as Base64, SHA-256 and decoded big-endian cells/strings. |
| [android-sysfs-hardware.json](android-sysfs-hardware.json) | Actual CPU identification/cache interfaces, Linux feature names, 654 DT device entries and their driver bindings, nonunique UFS capabilities, selected kernel configuration, and clock-summary availability. Captured at 05:29:03 UTC. |
| [android-ufs-units.json](android-ufs-units.json) | Current LU descriptor geometry, boot-LU fields and explicit HCI-version interface failures. Captured at 05:35:39 UTC. No block device, partition table or filesystem was opened. |
| [selected-device-dependencies.json](selected-device-dependencies.json) | A reproducible join of representative controller nodes and the sysfs bindings. Includes clocks, resets, regulators, interrupt domains, IOMMUs, mailboxes and firmware-name references where exposed. |
| [private-input-provenance.json](private-input-provenance.json) | Sizes/hashes of private boot, kernel, DTB and firmware inputs. Payloads remain outside the source repository. The active `_a` image comes from a current partition backup. |
| [publication-review.json](publication-review.json) | Checks each published DT property's bytes against the private input, verifies collector hashes, and checks sensitive-value omission. This is a focused publication review, not a firmware audit. |
| [inventory-tests.json](inventory-tests.json), [inventory-tests.log](inventory-tests.log) | Actual container run of 30 parser/collector tests. Includes variable cell widths, mixed IRQ providers, SPMI parent domains, GPIO/reference distinctions, disabled ancestors and privacy exclusions. |

The live DTB input has SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
It is a different input from the concatenated DTB component in `boot_a` and
already reflects the bootloader's tree selection and runtime additions.
The archive was reused from the existing private research workspace; firmware
and partition contents were not copied to GitHub.

## Observed device dependencies

The following rows select nodes from the inventory and corresponding bindings
from the sysfs capture. Register ranges, individual clock/reset IDs, regulator
providers and interrupt cells are preserved in the linked JSON files.

| Subsystem | Observed nodes and bindings | Dependencies relevant to emulation |
| --- | --- | --- |
| Application/GPU IOMMUs | `apps-smmu@15000000`, `kgsl-smmu@3da0000`; both bind `arm-smmu` and advertise `qcom,qsmmu-v500` | Stream/context-bank programming, TBU behavior, page-table walks, faults and IRQs; configuration alone does not establish those behaviors. |
| GPU/GMU | KGSL at `0x03d00000`, GMU at `0x03d6a000`, GPUCC at `0x03d90000` | GPUCC/GCC clocks, GDSC supplies, GPU IOMMU, AOP mailbox and private `a650_gmu`, SQE and ZAP firmware. A missing standalone GMU driver link does not prove the parent KGSL driver is not managing it. |
| Display | MDP/SDE at `0x0ae00000`, DSI controllers at `0x0ae94000` and `0x0ae96000`; `msm_drm` and `drm_dsi_ctrl` bindings | SMMU contexts, DSI PLL/PHY, clocks, display RSC, regulators and panel references. The primary panel reference list includes multiple candidates; it does not identify the runtime-selected panel by itself. |
| UFS | Host `0x01d84000`, PHY `0x01d87000`, ICE `0x01d90000`; `ufshcd-qcom`, `ufs_qcom_phy_qmp_v4`, `qcom_ice` | GIC SPI 265, GCC clocks/reset, PHY, pinctrl reset and RPMh regulators. UFS device spec reports `0x0310`; HCI controller version remains unobserved. |
| RPMh/GCC/secure services | GCC `0x00100000`, RSC `0x18200000`, display RSC `0x0af20000`, QMP AOP `0x0c300000`, QSEECom `0x82400000` | TCS command/response behavior, command database, clocks and rail votes, mailbox delivery and secure calls. No compatible string containing `scm` was found; that is not evidence that SCM services are absent. |
| USB | DWC3 instances at `0x0a600000` and `0x0a800000`, with Qualcomm wrappers and PHYs | GCC clocks/resets, GDSCs, application SMMU, GIC/PDC interrupts and PHY/Type-C state. |
| Audio | LPASS/ADSP, Kona ASoC sound card, WCD938x and TFA98xx devices at I2C addresses `0x34`–`0x37` | ADSP firmware, APR/SMP2P/mailboxes, soundwire/codec control, clock votes, regulators and DMA. Seven vendor audio GPIO-like properties remain explicitly unresolved rather than being assigned guessed provider semantics. |
| Touch/pen/accessory input | SPI `novatek@0` binds `NVT-ts`; I2C `ctn730@28` binds `ctn730`; `mouse@60` binds `i2c_hid` | SPI/I2C controllers, TLMM reset/IRQ lines and firmware. The HID node's interrupt is named `keyboard_irq`. No input event data was captured. |
| Wi-Fi/Bluetooth | `qcom,cnss-qca6390` binds `cnss2`; `bt_qca6390` binds `bt_power`; PCIe controller references are present | PCIe/MHI/IOMMU paths, enable GPIOs, RPMh rails, clocks, UART/firmware protocols. No MAC/Bluetooth addresses or association data were captured. |

## CPU, clocks and current production kernel

CPU0–3 expose MIDR `0x51df805e`, REVIDR `0x1f`; CPU4–7 expose MIDR
`0x411fd0d0`, REVIDR `0x3`. All eight DT CPU nodes say `qcom,kryo` with
MPIDRs `0x000` through `0x700`. The tree links private L1/L2 caches to a shared
L3; neither the inspected sysfs interfaces nor the DT supply cache capacity,
line size, ways or set counts. Those dimensions remain unconfirmed.
Linux's `Features` lines are reported as such; they are not raw per-core
`ID_AA64*` register values.

The fixed `xo_board` DT clock is **38.4 MHz**, and `chip_sleep_clk` is
**32,000 Hz**. GCC has no `clocks`/`clock-names` properties in this vendor DT;
its driver uses its provider-specific clock relationships. A 19.2 MHz derived
clock must be supported by the relevant driver/divider logic or a measurement;
it must not be inferred by silently changing the fixed-clock evidence.

The running kernel reports `CONFIG_SERIAL_MSM_GENI=y` but
`CONFIG_SERIAL_MSM_GENI_CONSOLE=n`, and `/proc/consoles` is empty.
`CONFIG_SERIAL_EARLYCON` is absent from the exposed config. This explains why
absence of serial text from this particular production kernel cannot alone
be used as a UART-model failure verdict. `CONFIG_DEVMEM=n`,
`CONFIG_DEBUG_FS=n`, `CONFIG_MODULES=y`, and `CONFIG_MODULE_SIG_FORCE=y`.
There were no existing debugfs mounts or readable clock summaries. No mount,
module load, register write, policy change or permission workaround was used.

## UFS descriptor interpretation

All six ordinary LUs report raw `bLogicalBlockSize=0x0c` (an exponent).
Their block counts are `0x1d45800`, `0x800`, `0x800`, `0x2000`, `0x80000`
and `0x2000`; boot-LU IDs are `0,1,2,0,0,0`. These describe the current LU
configuration. They do not describe maximum device capacity, and a boot-LU ID
does not identify Android's currently active A/B slot.

The descriptor reports manufacturer `0x01ce`, queue depth `0x20`, M-PHY
version `0x0410`, and UniPro version `0x0180`. The separately captured current
link state was Gear 1, two lanes, FAST mode, Rate A in both directions. That
snapshot is not a maximum-speed claim. Five candidate HCI-version sysfs
interfaces were absent; no MMIO read was substituted. Two well-known LUs
returned descriptor-read errors, which remain recorded in the evidence.

## Reproduction and limits

`../../scripts/inventory_dtb.py` reads a private DTB and publishes only its
allowlist. It omits 11,011 property values and preserves the omitted names.
`/chosen`, aliases and symbol-tree values are always excluded. Unknown values
are not published as opaque blobs or individual hashes. Original allowed
properties retain byte-level provenance; failed decodes remain explicit.

`../../scripts/collect_hardware_sysfs.py` and `collect_ufs_units.py` perform
the bounded hardware-only reads. `summarize_hardware.py` joins their outputs.
Use the command recorded in `inventory-tests.json` to rerun the 30 tests.
Raw DTB/boot inputs and proprietary firmware belong in a private working
directory, outside Git. `SHA256SUMS` covers this new evidence set and its
source scripts; the earlier validation receipt remains unchanged.

Group counts in the full JSON are classification tags, not physical-device
counts. The tree contains disabled nodes, alternative panels, pinctrl states,
audio routes and templates. Address translation through a DT bus is recorded
only where cell widths and `ranges` support it. A bus address is not silently
called a physical MMIO address. This evidence establishes configuration and
observed bindings; behavioral fidelity needs execution, transactions, fault
tests and working guest drivers for each implemented peripheral.
