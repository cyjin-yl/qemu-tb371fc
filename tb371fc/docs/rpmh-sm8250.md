# SM8250 RPMh RSC and typed digital resource service

`qcom-sm8250-rpmh` executes the Qualcomm RSC TCS protocol and maintains typed
ARC, VRM and BCM resource requests. It does not execute AOP firmware. A
successful supported command changes the modeled resource state; a command
outside the supported contract remains issued without a completion interrupt.
There is no generic SCM success handler.

## Choosing a command database

The board exposes `rpmh-profile=off|reference|file`, `rpmh-cmd-db=<path>` and
`rpmh-boot-configured=on|off`. The profile defaults to **off**. `reference`
must be selected explicitly; `file` requires an external command-db image.
The device itself requires exactly one of `reference-cmd-db=on` or
`cmd-db-file=<path>` and otherwise refuses realization.

The reference database has 98 typed resources. Linux 6.12.114 provides their
resource names, command encodings and regulator type contracts. The addresses
starting at `0x3e000`, `0x4e000` and `0x5e000`, ARC level maps and BCM auxiliary
values are **synthetic test data**. They are deliberately documented as such in
[`../fixtures/rpmh-reference.json`](../fixtures/rpmh-reference.json). The
reproducible Python generator pins its Linux input hashes. The C model builds
the same database; the QTest suite compares the complete 128 KiB byte image
against the independently generated fixture SHA256.

The optional external file is parsed as the standard little-endian command-db
ABI. The model binds only resource names for which it has an explicit typed
implementation. A file containing additional names does not create an
all-purpose successful write service. Read-only QOM `res.<name>.present` and
`res.<name>.address` properties show which resources were actually bound.
`cmd-db-sha256` identifies the effective, zero-padded 128 KiB image.

An external file is not automatically a native dump. The actual AOP-derived
partial reconstruction described below has firmware evidence for its entries,
but is neither a complete table nor a capture of live command-db RAM.

## Board interface and observed layout

| Interface | Mapping or meaning |
|---|---|
| MMIO indices 0, 1, 2 | DRV0, DRV1, DRV2, each 64 KiB |
| TB371FC DRV bases | `0x18200000`, `0x18210000`, `0x18220000` |
| Interrupt outputs 0, 1, 2 | Corresponding DRV completion interrupt; board SPIs 3, 4, 5 |
| MMIO index 3 | Read-only command-db, 128 KiB; board maps explicitly at `0x80860000` |
| `xo-board` clock input | Board oscillator; observed DT specifies 38.4 MHz |
| Clock outputs | `bi_tcxo`, `ln_bb_clk1`, `ln_bb_clk2`, `ln_bb_clk3`, `rf_clk1`, `rf_clk3`, `ipa` |
| `power[98]` output GPIOs | Aggregate resource enable requests, indexed by the reference manifest; not physical PMIC rail measurements |
| `sleep[3]` input GPIOs | Explicit DRV sleep/wake request; executes the saved sleep or wake TCS bank |

The captured DT selects DRV2, TCS offset `0xd00`, and groups of two active,
three sleep, three wake and one control TCS. The Linux driver omits the control
entry when allocating ordinary TCS IDs. The model provides eight ordinary TCS
slots per DRV and sixteen commands per TCS. Geometry for DRV0/1, the revision
value `0x00020700`, and reset contents are reference configurations rather
than sampled RSC registers. Qualcomm's downstream 4.19 driver uses the 2.7
layout: TCS stride `0x2a0`, command stride `0x14`.

`rpmh-boot-configured=on` seeds only the AP/DRV2 XO request with value 3. It
represents an explicit firmware handoff and can be replaced or released by
subsequent Linux commands. It is not a claim about cold silicon reset state.
Cold component tests use `rpmh-boot-configured=off`. The board connects the
38.4 MHz input through RPMh's `bi_tcxo` divide-by-two output to GCC when an RPMh
profile is enabled.

The board must reserve the command-db region and avoid overlapping its kernel
image. A real-tablet `/proc/iomem` sample reports kernel code beginning at
`0xa0080000`; blindly loading the stock Image at `0x80080000` can overlap real
reserved-memory regions. This device does not relocate, delete or overwrite
those regions on its own.

## Command and resource state

The implemented registers include driver configuration, IRQ enable/status/W1C
clear, command wait/enable masks, active-mode control and all sixteen command
message/address/data/status/response slots. The supported message is the
Linux eight-byte write opcode, with optional response request.

A rising AMC trigger starts the lowest enabled command slot. Its issued bit
appears immediately; a virtual timer applies the command and then starts the
next enabled slot. A response-requested or wait-marked command gets a completed
bit after application. An accepted asynchronous write can complete the TCS
without pretending that a response was requested. Completion status is
latched while its interrupt is masked. Clearing a different pending bit does
not acknowledge the original event.

`command-latency-ns` defaults to 1000. This delay exists to make ordering,
cancellation and migration testable; it is not measured RSC/AOP timing.
Programming command data while its TCS is running is rejected. Clearing AMC
enable cancels the remaining commands without rolling back requests that
already completed. Reset discards pending execution, votes and interrupts,
then restores the explicitly chosen firmware handoff state.

| Resource kind | Implemented effect |
|---|---|
| ARC | Validates the hardware-level index against that resource's auxiliary map; aggregates maximum requests across the three DRVs. XO drives the divide-by-two clock output. Other levels are exposed as requests, without voltage or frequency calibration claims. |
| VRM clock | Accepts only enable offset `+4`, values 0 or 1. LN clocks divide the oscillator by two; RF outputs use the oscillator rate. |
| VRM regulator | Keeps separate requested millivolts, enable and PMIC5 mode fields. Enforces the catalogued voltage ceiling and allowed mode encoding. This is a digital request model, not an analogue PMIC, settling-time or voltage-accuracy simulation. |
| BCM | Decodes valid/commit and 14-bit X/Y votes. An uncommitted write stages a request. Commit applies all staged resources in the same DRV and VCD; independent DRV requests aggregate by maximum. `IP0` supplies the IPA clock according to its auxiliary unit. Other BCMs expose requested votes rather than claiming actual interconnect throughput. |

Sleep and wake banks execute only after the corresponding explicit input
transition. They do not run merely because software programmed their buffers.
CPU WFI, PDC wake routing, power collapse, retention and autonomous solver
behavior are not inferred from this input. The architectural-counter wake
VALID bit is currently unsupported and diagnosed. `RSC_DRV_STATUS` has no
invented idle polarity: neither cited driver defines or uses that bit meaning,
so its access remains explicitly unimplemented.

Unknown MMIO, invalid message encodings, unknown resources, unsupported VRM
fields, out-of-range ARC levels and reserved BCM bits are diagnosed. Invalid
resource commands do not raise completion IRQs. This matters because the
mainline completion handler treats a TCS interrupt as success without checking
each command's error status. An always-success stub would hide missing
hardware and permit a misleading Linux probe.

## Actual AOP-derived static metadata

A read-only backup of the active tablet `aop_a` partition is privately retained
with SHA256:

`3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671`

The image is 524288 bytes and is an ELF32 ARM firmware. It is an active-slot
backup, not a verified pristine factory distribution. The public derived
receipt is [`../evidence/expansion-20261123/aop-command-db-static.json`](../evidence/expansion-20261123/aop-command-db-static.json).
The original firmware and the generated partial binary remain private.

The inspector validates ELF PT_LOAD mappings and three static descriptors:
37 BCM entries, 22 ARC level/timer entries, and 10 ARC minimum-operating-level
entries. Each header matches the public 24-byte command-db entry ABI. Each
auxiliary extent is checked against its descriptor. Known constructor call
sites independently pass slave 3/3/5 and version `0x1000`. The script refuses
other firmware hashes rather than extrapolating these offsets to a new image.

Examples of directly decoded facts:

| Resource | TCS address | Auxiliary data |
|---|---:|---|
| `cx.lvl` | `0x30000` | Levels 0, 16, 64, 128, 192, 256, 320, 384, 416, followed by zero padding |
| `mx.lvl` | `0x30010` | Levels 0, 16, 128, 192, 224, 256, 320, 384, 416, followed by zero padding |
| `mmcx.lvl` | `0x30080` | Levels 0, 64, 128, 192, 256, 384, followed by zero padding |
| `xo.lvl` | `0x300a0` | Levels 0, 32, 80, 128, followed by zero padding |
| `IP0` | `0x50070` | Unit 40, width 8, VCD 7 |

The `.mol` records are valid auxiliary-only entries with address zero. Some
BCM enable-mask resources have width zero. The static metadata is stronger
than guessing values from Linux resource names, but it does not establish
which later runtime registrations or modifications are present in live RAM.
The VRM name/alias table is being investigated separately; the 69-entry
reconstruction intentionally omits unresolved dynamic VRM registrations.

## Validation and migration boundary

Run the actual QTest suite and component migration suite with:

```sh
python3 tb371fc/tests/test_rpmh_sm8250.py \
  --qemu /absolute/path/qemu-system-aarch64 --work-dir /work/rpmh-qtest
python3 tb371fc/tests/test_rpmh_sm8250.py \
  --qemu /absolute/path/qemu-system-aarch64 --migration-only \
  --work-dir /work/rpmh-migration
```

Their default machine explicitly enables the reference profile, disables the
firmware XO handoff for cold tests, and sets `ufs-enabled=off`. Tests operate
real QEMU sockets; no register handler is mocked. Reports include the binary
SHA256, machine options and synthetic database hash. Tests check complete
database bytes, timer boundaries, masked/pending/W1C IRQs, sparse command
ordering, DRV aggregation, BCM commit, sleep/wake inputs, cancellation,
invalid operations, reset and destination continuation after an actual
process-to-process migration. Actual run results are recorded separately;
source code or a VMState description alone is not a passing test receipt.

Migration serializes command slots, cursor/deadlines, stalled requests,
resource votes, staged BCM requests and interrupts. Incoming state is checked
for register masks, valid cursor/status combinations and resource constraints.
The destination must have the same effective command-db digest and latency
configuration. The file path itself is not a replacement for that content
check. This is a component capability: the board's UFS controller currently
prevents a claim of complete board migration with in-flight storage I/O.

## External-file ABI compatibility checks

The external-file parser accepts the actual ABI features found in the AOP
static metadata: auxiliary-only records with address zero, multiple headers
for the same slave, aliases for an address, and zero-width BCM enable-mask
resources. Unknown aliases can refer to an existing typed hardware address;
the model does not create independent state for those aliases. Repeated names
are accepted only when slave type, group version, priorities, address and
auxiliary length/content agree; the auxiliary bytes may occupy different file
offsets. This matches the original AOP insertion routine re-registering a
parent during ordinary resource registration. Two distinct catalogued resources
claiming the same hardware address are still rejected until an explicit
shared-state implementation exists.

The extended suite adds an external file with a relocated clock address and
verifies that the old address no longer receives an ACK while the file's
address does. A second file exercises the legal ABI features together,
including repeated ARC, VRM, alias and BCM records. Twelve malformed inputs
check bad magic, escaped entry bounds, odd ARC aux length, zero address for a
known addressable resource, conflicting duplicate names/slaves, ambiguous
modeled aliases, an oversized file, and duplicate conflicts in address,
priority, auxiliary length, auxiliary bytes or group version. These remain
subcases within the 22-test suite. Files and stderr diagnostics are kept.
The earlier 22/22 receipt below predates the identical-duplicate extension;
its additional regression result is recorded separately.

An additional optional test consumes only the privately reconstructed AOP
static table:

```sh
python3 tb371fc/tests/test_rpmh_aop_static.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --cmd-db-file /private/path/aop-static-partial-cmd-db.bin \
  --work-dir /work/rpmh-aop-static
```

It checks the effective DB hash, firmware-derived XO address, actual CX level
bounds, IP0 unit and VCD data, and zero-width ACV requests. It also requires the
missing VRM clock to remain absent and unacknowledged. This test expressly
does not establish a live database capture, a complete VRM table, execution of
AOP firmware, or successful Linux provider probe. Its run result is separate
from the synthetic reference profile result.

## Executed validation receipt

Integrated binary SHA256
`a70862bfa8aa957ea26e362b5defd9ae60b4cc4bb53f2ac2c70c0b4fe3b67e2a`
passed the actual 19-case reference suite and all five process-migration checks,
both with exit status 0. The receipts are
[`rpmh-qtest.json`](../evidence/expansion-20261123/rpmh-qtest.json) and
[`rpmh-migration.json`](../evidence/expansion-20261123/rpmh-migration.json).
The command-db byte comparison, timer dispatch before a polling MMIO read,
invalid-command non-completion and destination continuation were exercised.
These receipts precede subsequent external-file parser compatibility changes
for auxiliary-only records, aliases and zero-width BCMs; they do not validate
those later changes or a native firmware database.

## Executed external-file and AOP-partial validation

The later integrated binary SHA256
`99dfb4fc3ff472624f0a079c67ba26b99e60935a4afc859d1e66afc8942e8c4c`
passed all **22 reference/external-file tests**, **5 actual process migration
checks**, and **6 AOP-derived partial-table checks**, each with process exit
status 0. The new receipts are retained separately in
[`revalidation-5/rpmh-summary.json`](../evidence/expansion-20261123/revalidation-5/rpmh-summary.json),
with individual reports for
[reference/file ABI behavior](../evidence/expansion-20261123/revalidation-5/rpmh-qtest.json),
[migration](../evidence/expansion-20261123/revalidation-5/rpmh-migration.json), and
[AOP static partial data](../evidence/expansion-20261123/revalidation-5/rpmh-aop-static-partial.json).

The first external-file run reached 20 passing checks before the next nested
fixture exceeded Linux's Unix socket path length. Its failure report is kept;
short numeric fixture directories fixed the harness. The complete 22-case
suite was then executed successfully with the same binary. This required no
device change and did not weaken a register, timer or negative-test assertion.

The AOP-derived run used the privately reconstructed 69-entry image with
SHA256 `7ddf6f1f7f1a711c3e34d96a8f349eb995d08220b440ef2965c4d4e8b1de480d`.
Its checks prove that this file's addresses and auxiliary data drive the
implemented digital resource behavior. Missing VRM resources remained absent
and their commands did not complete. These results still do not establish a
live command-db capture, a complete native resource table, AOP execution,
physical regulator behavior, or a successful Linux RPMh provider probe.

## Executed Linux provider, clock and interrupt integration

The unpatched Linux 6.12.114 kernel now probes the real `rpmh` and
`clk-rpmh` drivers on the generated functional-subset device tree. The
2026-11-23 run passed **23 guest checks**, **three independent host RPMh
observations**, and the host's comparison of the bytes written through
UFS/SMMU. The public receipt, console output and input provenance are in
[`rpmh-linux-20261123`](../evidence/rpmh-linux-20261123/result.json).

| Observation | Actual result |
| --- | --- |
| QEMU SHA-256 | `f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b` |
| Unpatched kernel Image SHA-256 | `c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40` |
| Machine configuration | `tb371fc,rpmh-profile=reference`; 8 CPUs and 8 GiB RAM |
| Linux driver bindings | `18200000.rsc` → `rpmh`; its clock child → `clk-rpmh` |
| Linux clock report | `bi_tcxo` = 19,200,000 Hz |
| Linux RSC completion IRQ count | 1, GIC hardware IRQ 37 |
| Completed active command | message `0x10108`, XO address `0x3e000`, value 3, status `0x10100` |
| RSC IRQ enable / pending after Linux handled it | `3 / 0` |
| Applied XO request | value 3, enabled |
| UFS | All 19 original driver/I/O checks passed; host payload comparison matched |

The guest's clock-rate check alone would not prove a working request path:
Linux calculates that rate from the clock parent and divider, and the machine
seeds an explicit XO firmware handoff. This gate also requires a real
completion interrupt reported by Linux. After the guest finishes, the host
stops the VM and reads the RSC registers and QOM resource state without writing
MMIO or guest memory. The completed command and drained pending status prove
that Linux's request and interrupt acknowledgement ran.

The first frozen-7 run exposed a harness error: the guest looked for the C
object's divider suffix in the debugfs directory. Upstream `clk-rpmh.c`
registers the public clock as `bi_tcxo`. Correcting that pathname produced the
measured pass without modifying the kernel or RPMh model. The failed receipt
is preserved as `before-clock-name-fix.json`; the frozen-5 control, whose DT
lacked the new clock provider, is preserved as `before-clock-provider.json`.

Reproduce in the development container with the public smoke kernel:

```sh
python3 tb371fc/scripts/boot_ufs.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --kernel /absolute/path/smoke/artifacts/Image \
  --machine tb371fc,rpmh-profile=reference --require-rpmh \
  --out /work/a-new-rpmh-linux-directory
```

This validates the synthetic reference command database, the real Linux XO
clock consumer chain through GCC/UFS, and one completed active TCS request.
It does not validate all resource types from Linux, suspend/resume, full
native command-db discovery, AOP firmware execution, analogue regulators,
the complete production DT, or Android user space. UFS power supply nodes
are still absent and Linux reports dummy/assumed-enabled regulators; this
run does not claim PMIC rail sequencing. The test ends by host QMP quit,
not guest-requested shutdown.

## Conditional AOP clock reconstruction and file-profile Linux gate

The original AOP allocator and command-db insertion functions have now been
replayed under explicit enumeration assumptions. The conservative 75-entry
partial file contains the separately validated 69 static rows and six
conditional clock/alias rows. Its actual QTest gate passed 6/6; the later
Linux file-profile gate passed 23 guest checks, three host RPMh observations
and independent UFS payload verification. The completed Linux XO request
used the statically extracted address `0x300a0`, not the reference address.
The new same-name parser regression also passed all 22 cases and five process
migration checks. See [the conditional reconstruction and its separate
receipts](aop-vrm-derived.md) for hashes, assumptions and the distinction
between static data, derived VRM clocks and actual Linux requests. This does
not promote the partial reconstruction to a live or complete native table.

## Primary sources

- [Linux 6.12.114 RSC driver](https://github.com/gregkh/linux/blob/v6.12.114/drivers/soc/qcom/rpmh-rsc.c)
- [Linux 6.12.114 command-db ABI](https://github.com/gregkh/linux/blob/v6.12.114/drivers/soc/qcom/cmd-db.c)
- [Linux RPMh clocks](https://github.com/gregkh/linux/blob/v6.12.114/drivers/clk/qcom/clk-rpmh.c)
- [Linux RPMh power domains](https://github.com/gregkh/linux/blob/v6.12.114/drivers/pmdomain/qcom/rpmhpd.c)
- [Linux RPMh regulator contracts](https://github.com/gregkh/linux/blob/v6.12.114/drivers/regulator/qcom-rpmh-regulator.c)
- [Linux SM8250 interconnect resource names](https://github.com/gregkh/linux/blob/v6.12.114/drivers/interconnect/qcom/sm8250.c)
- [Qualcomm downstream RSC implementation in the examined Lenovo kernel tree](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/rpmh-rsc.c)

The generated reference catalogue contains Linux-derived GPL-2.0-only data;
that license is retained for the catalogue and the integrating model. The
firmware inspector emits derived numeric metadata without embedding firmware
payload or disassembly in the repository.
