# SPMI PMIC arbiter v5 reference subset

This model implements a bounded SPMI transaction engine and an explicitly enabled reference
peripheral interrupt path. Its version, APID numbering and ownership table
are an **explicit reference configuration**. The native TB371FC arbiter
VERSION and APID/ownership registers have not been captured. Selecting this
profile does not establish analog PMIC, regulator, charger, GPIO, ADC or RTC
fidelity.

The board defaults to `spmi-profile=off`. Select
`-machine tb371fc,spmi-profile=reference` to instantiate the model. The board
sets the device's `reference-map=on` property explicitly; an independently
created device without that property rejects realization. Generated subset
DTs do not acquire synthetic PMIC nodes. Native diagnostic boots must record
the reference profile in their provenance.

## Inputs and provenance

The register protocol comes from these primary driver sources:

- Linux 6.12.114,
  [drivers/spmi/spmi-pmic-arb.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/spmi/spmi-pmic-arb.c?h=v6.12.114),
  particularly the v5 APID map, offset, command formatting, ownership,
  completion polling and QPNP interrupt operations.
- The [pinned public vendor source mirror](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/spmi/spmi-pmic-arb.c),
  SHA-256 `bedee16415c7290c2c07414623edc8af16b185f475e3512bb963e7c67076754c`.
  Its v5 geometry agrees with the upstream driver and scans the entire
  native 512-entry core mapping aperture. This mirror is not asserted to
  be an official OEM release or the exact source of the running kernel.
- Linux 6.12.114
  [drivers/mfd/qcom-spmi-pmic.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/mfd/qcom-spmi-pmic.c?h=v6.12.114)
  and
  [include/soc/qcom/qcom-spmi-pmic.h](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/include/soc/qcom/qcom-spmi-pmic.h?h=v6.12.114)
  for the primary PMIC type/subtype and revision register definitions.
- The allowlisted native DT capture
  [power-device-tree.json](../evidence/power-topology-20261123/power-device-tree.json)
  for register windows, execution environment, SID names and interrupt
  wiring. This capture is not a PMIC register dump.

No raw arbiter table was collected from the tablet. Its captured configuration
disables debugfs, and the safe identity exploration did not perform selector
writes or arbitrary PMIC/MMIO reads.

| Region | Native base | Length | SysBus region |
| --- | ---: | ---: | ---: |
| core | 0x0c440000 | 0x1100 | 0 |
| chnls | 0x0c600000 | 0x2000000 | 1 |
| obsrvr | 0x0e600000 | 0x100000 | 2 |
| intr | 0x0e700000 | 0xa0000 | 3 |
| cnfg | 0x0c40a000 | 0x26000 | 4 |

The DT specifies `qcom,ee=0` and `qcom,channel=0`. Its peripheral interrupt
is PDC input 1, level high. The captured PDC range maps that input to GIC
SPI 481. The model exposes one SysBus IRQ output and the board connects it
to PDC's named `irq-in` input 1. SPMI transaction completion itself is
polled through STATUS; that completion does not generate a peripheral IRQ.

## Reference APID table and identity boundary

VERSION is 0x50000000, selecting the driver's v5 layout. FEATURES reports
512 supported APID slots. The map is read-only at core + 0x900 + 4 * APID;
the ownership table is read-only at cnfg + 0x700 + 4 * APID. Unassigned
map entries are zero, so both upstream and vendor drivers skip them.

| Reference APID | PPID | SID / peripheral | Write EE | IRQ owner | Implemented data |
| ---: | ---: | --- | ---: | --- | --- |
| 0 | 0x001 | 0 / 0x01 | 0 | none | PM8150 identity subset |
| 1 | 0x201 | 2 / 0x01 | 0 | none | PM8150B type/subtype |
| 2 | 0x401 | 4 / 0x01 | 0 | none | PM8150L type/subtype |
| 3 | 0xa01 | 10 / 0x01 | 0 | none | PM8009 type/subtype |
| 4 | 0xffe | 15 / 0xfe | 0 | EE0 | opt-in synthetic IRQ peripheral |
| 5 | 0xffd | 15 / 0xfd | 1 | EE1 | opt-in synthetic IRQ peripheral |

APIDs 4 and 5 exist only with
`-global qcom-spmi-pmic-arb.test-peripheral=on`. They are not present in
the native or generated DT. The property is an explicit opt-in and is not
restricted by qtest_enabled(); it can also be selected deliberately in TCG.
APID numbering and write/IRQ owners in this table
are model assumptions, not measured silicon data.

All four primary identity endpoints expose PMIC_TYPE at address 0x104 as
0x51 and PMIC_SUBTYPE at 0x105 as 0x1e, 0x20, 0x1f and 0x24 respectively.
The subtype values are inferred by combining captured DT names with the
primary Linux definitions. They are not native register captures.

The firmware-derived PM8150 revision 3.0 supplies reference bytes 0x102=0
and 0x103=3 at SID0. REV1, REV2, fabrication IDs and the other PMICs'
revision bytes remain unknown and fail access. No revision zero is invented
for them. The unresolved pmxprairie SID8/9 pair is not aliased to PMK8002.
The secondary SID identity/peripheral banks are not populated.

This boundary matters to guest behavior: a revid driver that asks for an
unknown byte receives an ordinary failed SPMI transaction, and may decline
to probe. Passing the arbiter's version probe does not imply successful
PMIC child-driver probes.

## Transaction behavior

The current execution environment defaults to EE0 and is configurable in
the range 0..7. The model decodes the source-defined v5 layout:

- Read/observer channel: obsrvr + 0x10000 * EE + 0x80 * APID.
- Write channel: chnls + 0x10000 * APID.
- CMD +0x00; STATUS +0x08; WDATA0/1 +0x10/+0x14;
  RDATA0/1 +0x18/+0x1c.
- Command word: opcode << 27 | peripheral-byte-offset << 4 | (length - 1).
  Only EXT_WRITEL opcode 0 on a write channel and EXT_READL opcode 1 on
  an observer channel are implemented.

A command snapshots its write data, clears previous completion/read data,
and remains pending for 1,000 virtual nanoseconds by default. The configurable
`transaction-delay-ns` must be in 1..1,000,000,000. The delay is a model
parameter and is not a native timing measurement.

A transaction addresses 1..8 bytes and cannot cross a 256-byte peripheral
boundary. All requested bytes are validated before any peripheral mutation
or received data is published. Unknown registers, absent APIDs, unimplemented
opcodes, malformed command bits, writes to identities and invalid spans
finish with DONE | FAILURE (0x3). Wrong execution-environment/channel ownership
finishes with DONE | DENIED (0x5). A successful transaction returns DONE
(0x1) only after accessing implemented peripheral state.

Observer reads do not require ownership of the target's write channel, but
the observer EE must match the configured CPU execution environment. A write
requires the reference APID's owner. This models the driver-visible channel
contract, not a complete TrustZone/secure-world interconnect.

Writing a second command while its channel is busy cancels the pending
transaction and returns DONE | DROPPED (0x9), without mutating the peripheral.
That collision policy is a deterministic reference behavior; native collision
timing has not been measured.

MMIO accesses must be aligned 32-bit transfers. Unknown MMIO registers,
read-only register writes and malformed widths return MEMTX_ERROR and a
diagnostic. Defined inactive table or status entries can read zero; unsupported
peripheral transactions always carry a failure status. Failed reads do not
publish partial or stale RDATA.

## Stateful synthetic IRQ peripheral

Each opt-in synthetic endpoint contains a diagnostic ROM
(`TB371FC` followed by byte 1) at offsets 0x00..0x07 and a bounded QPNP
IRQ register subset. The ROM exists only to verify multi-byte transaction
packing. It is not a claimed production PMIC register layout.

| Peripheral offset | Function | Behavior |
| ---: | --- | --- |
| 0x10 | RT_STS | Read physical input levels |
| 0x11 | SET_TYPE | Read/write; one bit per IRQ, 1 means edge |
| 0x12 | POLARITY_HIGH | Read/write; high-level or rising-edge selection |
| 0x13 | POLARITY_LOW | Read/write; low-level or falling-edge selection |
| 0x14 | LATCHED_CLR | Write-one-to-clear peripheral latch |
| 0x15 | EN_SET | Set enable bits; reads current enables |
| 0x16 | EN_CLR | Clear enable bits; reads current enables |
| 0x18 | LATCHED_STS | Read peripheral latch |

The named `test-irq` GPIO inputs 0..7 feed APID4 and inputs 8..15 feed APID5.
They are created only when the synthetic peripheral is enabled. There is no
test-only command that directly declares a transaction or interrupt successful.

Arbiter access enable, pending status and W1C are at each write channel's
+0x100, +0x104 and +0x108. Owner aggregation is at
intr + 0x10000 * EE + 4 * (APID / 32). The output is gated by the peripheral
enable, arbiter access enable and the configured IRQ owner's EE.

Peripheral and arbiter latches are separate. The tests use Linux's actual
ordering: write arbiter IRQ_CLEAR, then perform an SPMI LATCHED_CLR write.
An edge disappears after both acknowledgements. An asserted level relatches
when acknowledged and remains actionable until its source deasserts.
The unmask sequence writes LATCHED_CLR and EN_SET together to discard a stale
disabled edge before enabling it. Per-IRQ W1C and masking leave other bits
intact.

This is a functional synthetic interrupt fixture. It does not establish that
any native PMIC GPIO, power key, charger or ADC can generate a matching event.
PDC and GIC routing require their own integration evidence.

## Reset and migration

Reset cancels queued transactions, clears transaction registers, masks and
both interrupt latches. External physical input levels are retained, as a
held input is not a device configuration register. Reference identity/map
data remain immutable.

VMState preserves all channel state, command-time write snapshots, absolute
virtual deadlines, input levels, interrupt configuration and both latch
layers. Loading requires the same execution environment, synthetic-endpoint
profile and transaction delay. The positive migration test transfers a
raised IRQ and an incomplete enable-clear transaction, and checks that it
finishes only at the remaining deadline after resume.

## Validation

The independent QTest script is
[test_spmi_pmic_arb.py](../tests/test_spmi_pmic_arb.py). It drives the real
QEMU memory regions and peripheral state, and uses actual QEMU migration.
It does not substitute a fake guest kernel or make a Linux driver success
claim.

The script currently defines 25 checks covering geometry/identity provenance,
byte packing, data snapshots, ownership, strict errors, atomic failure,
malformed MMIO, IRQ configuration/ACK/masking/polarity/level handling,
reset, the opt-in boundary, EE1 isolation and in-flight migration.
The [actual 25/25 receipt](../evidence/verified-20261123-0820/public-47/39-spmi-pmic-arb/fixtures/data/result.json)
records successful execution against frozen QEMU SHA-256
`87f52240ff55e0b60a87e9a0dc57b76dff75e653f3dbe1d5dbdc64b2b7d9ac89`.
The [unified acceptance record](../evidence/verified-20261123-0820/final-verification.json)
also records the complete 47-gate run. These results cover the explicit
reference configuration described above; they do not establish a native
VERSION, APID table or complete PMIC implementation.

The separate historical frozen12 run on 2026-11-23 passed **25/25 checks** in 1.616 seconds, including
actual migration of the pending transaction and asserted IRQ. The binary SHA256
was `178bace28c84bced9471b824d413feb18e76ddb15212ec32412ad7a7ec32a675`.
The checks cover geometry/identity provenance, byte packing, data snapshots,
ownership, strict errors, atomic failure, malformed MMIO, IRQ
configuration/ACK/masking/polarity/level handling, reset, the opt-in boundary,
EE1 isolation and in-flight migration.

The [sanitized result](../evidence/spmi-reference-20261123/result.json),
[allowlisted source hashes](../evidence/spmi-reference-20261123/source-sha256.json)
and [receipt manifest](../evidence/spmi-reference-20261123/sha256.json)
preserve the individual outcomes and provenance. All peripheral data in this
receipt is synthetic or explicitly qualified reference data. This passing
component gate does not establish native PMIC operation or an unmodified Linux
SPMI guest gate.

Run inside the development container, against a frozen binary:

```sh
python3 /src/tb371fc/tests/test_spmi_pmic_arb.py \
  --qemu /work/<frozen-build>/qemu-system-aarch64 \
  --out /work/<new-exclusive-result-directory>
```

The result records binary and test hashes, each individual check, and
failures without converting them into success. Public receipts may include
these synthetic QTest JSON/stderr files and source hashes. Private firmware,
production DTBs, kernel images and native serial logs must not be copied into
this evidence directory.

## Whole interrupt-route verification

The separate [SPMI/PDC/GIC runner](../tests/test_spmi_pdc_route.py) defines
eight checks and one actual QMP process migration. It suppresses the shared
test harness's output-interception request, preserving the board's real
SPMI -> PDC input 1 -> GIC SPI 481 connection. It configures the synthetic
peripheral using actual byte transactions, observes GIC pending bits, checks
PDC masking and Linux acknowledgement order, and transfers an active IRQ
with an incomplete enable-clear transaction to another QEMU process.

The [actual route receipt](../evidence/verified-20261123-0820/public-47/40-spmi-pdc-gic-route/fixtures/data/result.json)
passed **8/8 checks and one real process-to-process migration** on the same
frozen binary at 2026-11-23 08:39:11 UTC. Its output interception flag is
false: the board connection remains intact. The test restores an active IRQ,
RAM and PDC configuration, completes the pending SPMI write after its remaining
600 virtual nanoseconds, and checks new events and acknowledgement afterward.

The test does not read or assign a PDC VERSION and does not execute a guest
CPU interrupt handler. This establishes the modeled distributor connection;
it does not establish native PMIC electrical timing or power-state wake.

~~~sh
python3 tb371fc/tests/test_spmi_pdc_route.py \
  --qemu /path/to/frozen/qemu-system-aarch64 --out /new/result/directory
~~~

The current device accepts only the explicit built-in reference table. It
does not yet load a captured native APID/ownership profile or expose a
general external PMIC peripheral-bus API. Those capabilities require future
implementation and separate evidence; no reference table is silently
substituted for an absent native capture.
