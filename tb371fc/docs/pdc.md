# Qualcomm PDC interrupt conversion subset

The TB371FC PDC model converts enabled input events into actual GIC
interrupts using the pin-to-SPI mapping captured from the tablet. It models
the legacy register layout used by the native driver. Power-domain wake,
firmware execution and physical pulse timing remain outside this contract.

## Evidence and selected profile

The allowlisted [native DT capture](../evidence/power-topology-20261123/power-device-tree.json)
records `qcom,kona-pdc`, the register window at
`0x0b220000/0x30000`, and the firmware SPI-type bitmap at
`0x17c000f0/0x60`. Its four `qcom,pdc-ranges` tuples give:

| PDC input/output pins | Parent GIC SPI numbers |
| --- | --- |
| 0–93 | 480–573 |
| 94–124 | 609–639 |
| 125 | 63 |
| 126–137 | 716–727 |

SPI numbers exclude the 32 architectural SGI/PPI IDs. PDC pin 1 therefore
routes to SPI 481, architectural INTID 513. The native SPMI arbiter uses
that PDC input. Connecting the arbiter's IRQ output requires the named
GPIO input `irq-in`, index 1, on `/machine/pdc`.

Neither the native DT nor the existing PMIC/sysfs captures supplies a PDC
VERSION register value. The model's `reference-version=0` default therefore
makes VERSION reads fail. The native vendor driver does not read VERSION.
Experiments with mainline Linux, which does read it, must explicitly select
a legacy reference identity, for example:

```sh
-global qcom-pdc.reference-version=0x20000
```

This value is a test profile, not a measurement of the tablet. Values at or
above `0x30200` are rejected because the newer per-pin enable layout is not
implemented. The default `num-pins=138` is the DT-mapped interval, not a
measurement of every physical PDC input.

## Register and interrupt behavior

| Region/offset | Implemented behavior |
| --- | --- |
| Main `0x10 + 4 * bank` | Read/write enable bitmap, 32 pins per bank; last native bank retains only bits 0–9 |
| Main `0x110 + 4 * pin` | Exact legacy encodings: 0 active-low, 2 falling edge, 4 active-high, 6 rising edge, 7 both edges |
| Main `0x1000` | Read-only explicit reference VERSION when selected; otherwise a failed access |
| SPI-type region `0x00..0x5c` | 24 read/write words used by the vendor driver to record per-SPI level/edge configuration for firmware |

Enables are ordinary read/write fields, so a driver updates one bit using
read/modify/write. Undefined trigger encodings and extra trigger-register
bits fail without changing the previous mode. In particular, bit 3 is not
silently treated as the enable bit from the unsupported v3.2 layout.
The supported MMIO accesses are aligned 32-bit operations; unknown offsets
and unsupported accesses return a failed memory transaction.

An enabled level input continuously drives its active polarity. Disabling
it deasserts the output; enabling a still-active level asserts it again.
The low-level mode inverts the external input.

An enabled edge input emits a functional pulse only when the input changes
in the selected direction. Repeating the same level produces no event.
Input history continues to update while disabled, so enabling a pin cannot
replay an earlier disabled edge. Changing an edge configuration also does
not synthesize an event.

The GIC owns edge pending state and acknowledgement. PDC has no invented
pending, EOI or acknowledge register. Disabling PDC after a delivered edge
does not clear the GIC pending bit. Clearing the GIC pending bit does not
replay that edge while the input stays unchanged.

The SPI-type bitmap stores the vendor driver's configuration. It does not
claim that secure firmware consumed that configuration or completed a
power transition. Physical polarity-reconfiguration phantom IRQ quirks
mentioned by the upstream driver are not modeled.

## Reset and migration

Reset clears enables, trigger configurations and the SPI-type bitmap,
disabling outputs. External input levels remain present across reset, as
they belong to the connected source device.

Migration saves configuration, enable banks, the SPI-type bitmap and input
history. It rejects a different destination pin count or reference version,
invalid trigger encodings, out-of-range enable bits and invalid input
levels. The destination reconstructs level outputs without replaying edge
pulses. GIC migration independently preserves already-pending interrupts.

The migration tests include an enabled edge input that is high while its
GIC pending bit has already been acknowledged before transfer. That case
detects an unwanted new pulse at load time, which an already-pending edge
alone would hide.

## Acceptance commands and scope

The compiled C suite contains 18 cases after the unsupported-config-bit
regression was added. The Python runner expects ten checks across two
actual process-to-process migrations. These are test definitions; successful
acceptance requires the specific binary's execution receipt.

```sh
ninja -C /work/build -j2 tests/qtest/tb371fc-pdc-test
QTEST_QEMU_BINARY=/work/build/qemu-system-aarch64 \
  /work/build/tests/qtest/tb371fc-pdc-test

python3 tb371fc/tests/test_pdc_migration.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/pdc-migration-new
```

The C suite checks actual GIC pending bits for all four routing ranges and
rising/falling/both-edge conversion. Separate intercepted-wire cases verify
level polarity and enable behavior. Intercepting a QTest output disconnects
its original parent, so the GIC-routing tests deliberately do not intercept.

The migration runner likewise performs one transfer with the real GIC
connection intact and one with separate output-wire observations. It saves
`result.json`, exact QEMU/test/helper hashes, both migration responses and
failure logs. Acceptance requires `all_passed=true`, ten passing checks
and two completed migrations. UFS is disabled in these component transfers;
no whole-board storage migration or firmware wake equivalence is claimed.

## Primary source contract

- [Linux v6.12.114, drivers/irqchip/qcom-pdc.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/irqchip/qcom-pdc.c?h=v6.12.114),
  SHA256 `5e99a638043a99294c46b8cf936255536e0a8f5aa10c65bfedbc789f9ed3489e`.
  It defines the legacy bank layout, all five trigger encodings, polarity
  conversion, GIC ownership of pending state, and the newer v3.2 boundary.
- [Public TB371FC source mirror at commit 9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/irqchip/qcom-pdc.c),
  SHA256 `3f967d66d4090e53393c8ae07570017a728ec4af46332d9b35faacac9f82efea`.
  This source contains the native compatible and secondary SPI-type bitmap.
  It is a pinned public mirror, not asserted to be the exact source of the
  running production kernel.
- [Capture provenance and limits](../evidence/power-topology-20261123/collection-provenance.json)
  separate source-derived register semantics from read-only DT/sysfs
  observations. No live PDC register read or physical wake experiment is
  claimed here.


## Preserved first acceptance run

The [historical 17/9 receipt](../evidence/pdc-20261123/README.md) passed on frozen
QEMU SHA-256 5a5c76062c5345d2be5960932a3a7ccefc73c6523e1531ea1b65088f5cdad555
at 2026-11-23 08:02:49-08:02:52 UTC. Its exact source hashes are retained.
The subsequent 18/10 definitions above require a separate execution receipt;
the earlier result is not evidence that a later source revision was tested.
