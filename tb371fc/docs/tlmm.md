# TB371FC TLMM GPIO subset

The model implements digital GPIO register behavior for the 180 ordinary pins,
a separate UFS reset output, and shared SDC2 pad-control fields. It does not
implement the complete pin-multiplexed peripheral fabric, PDC wakeup, analog
voltages, drive current, contention, or pad timing. Reset values are explicit
virtual reset state; they have not been measured on the tablet.

## Hardware and source evidence

The native DT describes `/soc/pinctrl@f000000`, compatible
`qcom,kona-pinctrl`, one physical region `0x0f000000 + 0x01000000`, and
level-high GIC SPI 208. Native GPIO sysfs reports 180 pins for
`f000000.pinctrl`. See
[power-device-tree.json](../evidence/power-topology-20261123/power-device-tree.json)
and [android-spmi-gpio-bindings.json](../evidence/power-topology-20261123/android-spmi-gpio-bindings.json).
No TLMM MMIO register was read or written on the tablet.

Linux v6.12.114 supplies the internal layout and driver-visible protocol.
These primary implementation sources are distinct from measured silicon values:

| Primary source | Evidence |
| --- | --- |
| [pinctrl-sm8250.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/pinctrl/qcom/pinctrl-sm8250.c?h=v6.12.114) | Per-pin tile, 0x1000 stride, fields, KPSS target 3, separate UFS_RESET and SDC2 groups |
| [sm8250.dtsi](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/arch/arm64/boot/dts/qcom/sm8250.dtsi?h=v6.12.114) | WEST/SOUTH/NORTH tile bases and SPI 208 |
| [pinctrl-msm.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/pinctrl/qcom/pinctrl-msm.c?h=v6.12.114) | Two-bit trigger encoding, raw versus summary enable, write-zero acknowledgement, and level re-latching |

The generator checks the pinned pinctrl source hash and rejects missing,
duplicate, mismatched, or out-of-range indices. It emits exactly 180 offsets.
[tlmm-map-source.json](../evidence/power-topology-20261123/tlmm-map-source.json)
records source, generator and header hashes.

| Region | Physical address | Meaning |
| --- | --- | --- |
| WEST | `0x0f100000` | Assigned pin block at tile base + pin number × 0x1000 |
| SOUTH | `0x0f500000` | Same layout, only pins assigned to this tile decode |
| NORTH | `0x0f900000` | Same layout, only pins assigned to this tile decode |
| UFS_RESET control / IO | `0x0f5b8000` / `0x0f5b8004` | Dedicated output-only pad |
| SDC2 control | `0x0f9b7000` | Shared CLK/CMD/DATA pull and drive fields |

Independent golden examples are GPIO 0 at `0x0f500000`, GPIO 4 at
`0x0f904000`, GPIO 133 at `0x0f185000` and GPIO 179 at `0x0f1b3000`.
A pin never aliases into the other two tiles.

Upstream Linux assigns UFS_RESET group index 180 and includes it in a
181-entry GPIO abstraction. The device still has exactly **180 ordinary GPIO
inputs/outputs**, plus one separate output-only pad. UFS_RESET has neither an
input bit, output-enable field, nor interrupt configuration.

The native UFS node selects `dev-reset-assert` and `dev-reset-deassert`
pinctrl states. Their target is `ufs_reset`: assert programs `output-low`,
deassert programs `output-high`; both specify pull-down and 8 mA.
This establishes an active-low device reset signal, distinct from the
GCC `core_reset` line. See
[ufs-reset-device-tree.json](../evidence/power-topology-20261123/ufs-reset-device-tree.json).
This is a pin-state declaration; it does not measure reset pulse timing or
prove that the whole UFS reset behavior is implemented.

## Device interface

Header `include/hw/gpio/qcom-tb371fc-tlmm.h` exports
`TYPE_QCOM_TB371FC_TLMM`, QOM type `qcom-tb371fc-tlmm`.
The board realizes it at `/machine/tlmm`, maps MMIO 0 at `0x0f000000`,
and connects SysBus IRQ 0 to GIC SPI 208.

| Interface | Count | Semantics |
| --- | ---: | --- |
| MMIO region 0 | 16 MiB | Native enclosing window; aligned 32-bit accesses |
| SysBus IRQ 0 | 1 | OR of enabled pending KPSS-targeted GPIO interrupts |
| Input `gpio-in` | 180 | Externally supplied digital pad levels |
| Output `gpio-out` | 180 | Output latch value; use together with `gpio-oe` |
| Output `gpio-oe` | 180 | GPIO mux selected and output enabled |
| Output `ufs-reset` | 1 | Dedicated output latch; physical low asserts reset |

An enabled GPIO output reads back its driven digital level. Otherwise the
input register reports the external input. This is a functional rule without
analog contention. Alternate-function selection is stored and disables GPIO
drive; it does not create UART, I2C, SPI, display or other peripheral logic.

A generic mainline DT uses three ranges of size `0x300000`, named
`west`, `south`, `north`, and compatible `qcom,sm8250-pinctrl`.
Those ranges fit the single modeled physical window. PDC wakeup should only
be declared when a PDC model is connected. Ordinary summary IRQs are separate.

## GPIO registers and interrupts

| Pin offset | Implemented fields |
| --- | --- |
| `0x0` | Pull [1:0], mux [5:2], drive [8:6], OE bit 9 |
| `0x4` | Read-only input bit 0, writable output latch bit 1 |
| `0x8` | Summary enable bit 0, polarity bit 1, detection [3:2], raw enable bit 4, target [7:5] |
| `0xc` | Pending status bit 0, acknowledged by writing zero |

The two-bit encodings programmed by `msm_gpio_irq_set_type` are: detection
0 plus low/high polarity for levels, and detection 1/2/3 for rising/falling/both
edges. Linux sets polarity to one for the edge encodings. Other edge/polarity
combinations have not been independently validated against silicon.

`RAW_STATUS_EN` controls latching; summary enable controls forwarding
already latched events. Masked edges remain pending and unmasking delivers
them. Configuration changes neither invent an earlier edge nor discard a
pending event.

Status is **write-zero-to-clear**, following the SM8250 default
`intr_ack_high = false`. Writing one cannot clear or inject status.
An active level re-latches after ACK while raw status remains enabled.
Linux masks both raw and summary enable for level IRQs, but leaves raw
enabled when masking edge IRQs. Only target 3 routes to this KPSS IRQ output.

The silicon's spurious-edge workaround when toggling raw enable is not
reproduced. Unknown offsets produce `MEMTX_ERROR` through
`read_with_attrs` / `write_with_attrs` and emit `LOG_UNIMP`. Known
registers retain their documented read-only and reserved-bit behavior.
The device does not supply success-valued generic storage for unimplemented
blocks or fabricate hardware identity registers.

## Reset, migration and acceptance

Virtual reset clears control, ordinary output and pending state. The explicit
boolean property `boot-ufs-reset-high` (device default false) sets the dedicated
UFS pad high on reset when requested by a direct-kernel board handoff. It does
not claim a measured power-on-reset value; cold device fixtures keep it false. External digital
input levels survive controller reset. VMState preserves register state,
external inputs and pending flags. Post-load validates field bounds, restores
output wires and IRQ state, and does not create input edges.

The 25 C QTest cases use actual input injection and output interception.
They cover all tiles, final pin, output direction, read-only inputs, all five
trigger types, raw/summary masking, routing, two pending pins, write-zero ACK,
reset, UFS_RESET, its explicit boot-handoff property, and SDC2. They do not include the model-generated map header.
QTest raw MMIO access does not expose MemTxResult; invalid-offset checks
therefore assert absence of aliasing/state corruption and do not claim that
the returned data value represents real unimplemented hardware.

The migration script performs four real transfers between paused QEMU
processes, separately observing summary IRQ, output latch, OE and UFS reset
wire groups. It checks pending masked edges, level re-latching, new events,
output continuation and a RAM marker, rather than only comparing registers.

~~~sh
python3 tb371fc/scripts/generate_tlmm_map.py \
  --linux-source /work/clean-linux/source/linux-6.12.114 \
  --header hw/gpio/qcom-tb371fc-tlmm-map.h \
  --receipt tb371fc/evidence/power-topology-20261123/tlmm-map-source.json \
  --check
python3 -m unittest discover -s tb371fc/tests -p test_tlmm_map.py -v
QTEST_QEMU_BINARY=/path/to/qemu-system-aarch64 \
  /path/to/build/tests/qtest/tb371fc-tlmm-test
python3 tb371fc/tests/test_tlmm_migration.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/tlmm-migration-fresh
~~~

Results apply to the recorded binary hash and functional subset. They do not
establish native wakeup, touchscreen/pen operation, complete Android support,
or full TB371FC equivalence.

## Recorded validation

Frozen 7 passed 25/25 C QTests and 15/15 migration checks. The map parser passed 9/9 tests and the generated header matched pinned source. [Results and exact hashes](../evidence/tlmm-20261123/README.md) retain the earlier startup failure separately. [Vendor crosscheck](../evidence/tlmm-20261123/vendor-crosscheck.json) confirms all ordinary pin offsets and identifies unimplemented EGPIO, wake and I3C fields. The current virtual control register reports no EGPIO capability; this is not a measurement that those silicon bits are absent.
