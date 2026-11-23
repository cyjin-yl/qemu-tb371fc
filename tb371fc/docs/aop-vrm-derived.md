# AOP VRM registration: conditional function replay

The original resource allocator, address encoder, auxiliary-data builder and
command-db insertion routine in the tablet's privately retained AOP image have
now been executed in an isolated Thumb instruction engine. Two explicitly
configured input variants emitted **70 records / 61 unique names** and
**69 records / 60 unique names**. The five RPMh clock addresses and their
`vrm.xob` alias agree between those variants.

This is conditional reconstruction. It does not establish which PMIC
enumeration path the tablet used, and it is not a capture of live command-db
RAM. The firmware image, disassembly and reconstructed binary remain private.
The repository contains the analysis program and derived metadata:

- [Optional replay program](../scripts/replay_aop_vrm.py)
- [Function replay and per-record provenance](../evidence/expansion-20261123/aop-vrm-allocator-replay.json)
- [Execution receipt](../evidence/expansion-20261123/aop-vrm-analysis-run.json)
- [Earlier static ARC/BCM/MOL extraction](../evidence/expansion-20261123/aop-command-db-static.json)

## Input and scope

The input is the authorized, read-only backup of the active `aop_a` partition,
524,288 bytes, SHA256
`3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671`.
It is an active-slot image; it has not been established to be pristine stock.
The program checks the exact hash and ELF format before using the documented
mappings.

The replay uses Unicorn 2.1.4 in ARM Thumb mode. It maps the firmware's ITCM and
DTCM data and supplies private RAM for input strings, stack and allocations.
The original command-db group initializer runs with the firmware descriptor's
VRM slave ID 4 and version `0x100`. Only two heap routines are replaced:
the one allocating the 2,400-byte entry array and 2,048-byte auxiliary array,
and the one allocating 12-byte parent metadata. Name packing, parent matching,
ID allocation, address construction, auxiliary generation and entry insertion
execute as firmware instructions. An unexpected unmapped memory access,
allocation size, firmware assertion, timeout or nonzero routine result fails
the replay.

The ordinary variant executed 73,295 instructions; the `smpf2`-disabled
variant executed 72,250. Their instruction-address trace hashes, allocation
records, assigned IDs and emitted entries are in the receipt. These are
measurements of the isolated function replay, not AOP execution time or
hardware timing.

## Source anchors and enumeration assumptions

Offsets below refer to this hash-pinned private file. The replay program does
not contain the firmware bytes or disassembly.

| Function or data | File offset | Role in the analysis |
| --- | --- | --- |
| Normal initialization loop | `0x2def0` | Establishes two passes over PMICs: parents first, then enabled resources |
| Parent registration | `0x2e028` | Supplies names, aliases and reserved child counts |
| Resource allocator | `0x6508` | Executed; allocates and reuses resource IDs |
| Registration wrapper | `0x17094` | Executed; supplies names and aliases to the DB path |
| Address encoder | `0x17828` | Executed; encodes the VRM slave and resource ID |
| Auxiliary builder | `0x1787c` | Executed; emits the resource-class auxiliary bytes |
| Command-db initialization / insertion | `0x8bec` / `0x8c6c` | Executed; creates the group and writes records |
| Property dispatcher | `0x178f4` | Locates the target configuration, dependency and name tables |
| SMPS / LDO / BOB count arrays | `0x19c70` / `0x19c58` / `0x19c88` | Read and checked against the pinned target data |
| Platform-dependent `smpf2` edit | `0x2da30` | Identifies the configuration difference varied by the replay |

The invocation sequence assumes the normal initialization branch and that
PMIC indexes 0, 2, 3 and 5 are visited through every enabled or parent-containing
target entry. Regulator count arrays come from the firmware; their equality
to the counts discovered on the tablet is an assumption. Clock configuration
prefixes are bounded by their static arrays. Every enabled or parent-containing
clock in those arrays lies within the first seven slots.

The firmware's PMIC discovery logic advances the chip index after two SIDs.
That supports the mapping between its chip indexes and SPMI SID pairs; it does
not independently prove each chip's presence or revision. The Android PMIC
bindings and the firmware-reported PM8150 identity are recorded separately in
[the power topology evidence](../evidence/power-topology-20261123/README.md).
No absent device-tree node is treated as proof that an AOP-only peripheral is
absent.

The platform edit disables `smpf2` when two fields of the firmware's platform
structure equal 8 and 3. Those live fields were not captured. The device tree's
`qcom,board-id = <11 0>` is a different interface and does not resolve this
condition. The replay therefore varies the effect of this edit on the
allocator's input; it does not claim to have executed the live platform
selection or to know which variant the tablet selected. Miscellaneous resource
registration, other runtime filtering and the final live header/checksum
remain outside this reconstruction.

## What changes between the two variants

Parent resources reserve child-ID blocks before ordinary resource registration.
Both variants emitted nine identical repeated names, including parent
registrations that appear again when the ordinary resource is registered.
The original insertion routine permits these repetitions. The emitted VRM
records have auxiliary bytes `01 00 00 00` and priorities `[0, 0]`.

Disabling `smpf2` removes its record and shifts the later `ldof1` and
`ldof3` addresses down by `0x100`. This is why publishing the entire
ordinary-variant output as an unconditional native table would be incorrect.

| Resource | Ordinary variant | `smpf2` disabled |
| --- | --- | --- |
| `smpf2` | `0x43200` | Absent |
| `ldof1` | `0x43300` | `0x43200` |
| `ldof3` | `0x43400` | `0x43300` |
| `ldoa12` | `0x42100` | `0x42100` |

The RPMh file parser accepts repeated names only when the slave, group version,
priorities, address, auxiliary length and auxiliary bytes agree. Auxiliary
file offsets may differ. Any semantic conflict still prevents realization.
A new positive case fails on the old parser with its explicit
`invalid/duplicate resource` diagnostic; the failed result is retained in
[the old-parser reproduction](../evidence/expansion-20261123/rpmh-identical-duplicate-old-failure.json).
The later integrated parser regression result must be considered separately
from the previous 22/22 suite receipt.

## Conservative 75-entry clock partial file

The private candidate used for the next driver gate contains the separately
validated **69 static ARC/BCM/MOL records**, plus only the following **six
conditional VRM records**. It does not include the candidate regulator map or
fill missing resources with the synthetic reference profile.

| Name | Resource base | Enable field | Relationship |
| --- | --- | --- | --- |
| `lnbclka1` | `0x42500` | `0x42504` | LN BB clock 1 |
| `vrm.xob` | `0x42500` | `0x42504` | Alias of the same resource |
| `lnbclka2` | `0x42600` | `0x42604` | LN BB clock 2 |
| `lnbclka3` | `0x42700` | `0x42704` | LN BB clock 3 |
| `rfclka1` | `0x42800` | `0x42804` | RF clock 1 |
| `rfclka3` | `0x42900` | `0x42904` | RF clock 3 |

All six records agree between the two allocator input variants. Their normal
enumeration assumptions still apply. The output uses the public command-db ABI
and a canonical reconstructed layout; it is not byte-for-byte live RAM.
Its SHA256 is
`a971809c06fa1a3ff44c9d28463bc6ff53203d9375ffd0f76dc73d4c8a3ea6f9`.

The original 69-entry image and its earlier six-test receipt remain unchanged.
The new six-test suite separately checks the 75-entry file's exact guest-visible
bytes, all five clock gates and dividers, votes across DRVs, rejection of old
synthetic addresses, invalid fields/values, and the absence of excluded
regulators. It passed **6/6, exit 0**, on QEMU SHA256
`f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b`.
The result is
[the clock partial-file QTest receipt](../evidence/expansion-20261123/revalidation-7/rpmh-aop-clock-partial.json).

The measured output rates in that fixture are 19.2 MHz for LN clocks and
38.4 MHz for RF clocks, calculated by the digital model from its configured
38.4 MHz input and the Linux clock contracts. They are not measurements of
the tablet's active clock signals. This QTest pass also does not validate
the inferred addresses against live hardware, or establish Linux provider
probe, analog regulation, complete command-db discovery or full-board
migration.

## Reproduction with private input

Install the optional `unicorn==2.1.4` analysis dependency into an isolated
Python environment. It is not a QEMU build dependency and is not required by
the public synthetic-fixture CI.

```sh
python3 tb371fc/scripts/replay_aop_vrm.py \
  --firmware /private/active-aop-image.bin \
  --assume-normal-enumeration \
  --receipt /private/vrm-replay.json \
  --static-db /private/aop-static-partial-cmd-db.bin \
  --private-clock-output /private/aop-static-plus-clock-partial.bin

python3 tb371fc/tests/test_rpmh_aop_clocks.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --cmd-db-file /private/aop-static-plus-clock-partial.bin \
  --work-dir /work/a-new-clock-partial-run
```

The first command requires the exact documented private firmware hash and the
earlier 69-entry static image hash. No firmware download or live device access
is performed by either command.

## Public ABI sources

- [Linux 6.12.114 command-db reader and entry layout](https://github.com/gregkh/linux/blob/v6.12.114/drivers/soc/qcom/cmd-db.c)
- [Linux RPMh clock address fields and dividers](https://github.com/gregkh/linux/blob/v6.12.114/drivers/clk/qcom/clk-rpmh.c)
- [Unicorn instruction-engine API](https://www.unicorn-engine.org/docs/tutorial.html)

## Executed Linux file-profile gate and parser regression

The subsequent frozen binary
`fe71b5c9a65761910294e6cf690f615ca3c8c0da132207860dc4550b5f28668d`
passed the **22-case RPMh suite**, including the identical-duplicate positive
case and all 12 malformed-file subcases, and **5 actual process migration
checks**, with exit status 0. The migration fixture explicitly disables UFS.
This closes the old-parser failure documented above without weakening any
conflicting-record rejection.

The same binary then ran the unpatched Linux 6.12.114 smoke kernel
(`c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`)
using the explicit 75-entry `rpmh-profile=file` configuration. It passed
**23 guest checks**, **three independent host RPMh observations**, and the
host's independent check of the 32 KiB UFS disk payload. The process exited
0; the measured guest run lasted 2.145 seconds.

| Observation | Result |
| --- | --- |
| Effective DB digest | `a971809c06fa1a3ff44c9d28463bc6ff53203d9375ffd0f76dc73d4c8a3ea6f9` |
| Synthetic reference profile enabled | False |
| Linux bindings | Real `rpmh` RSC and `clk-rpmh` drivers |
| Completed XO request address | `0x300a0`, from the original 69-entry static extraction |
| Message / data / completion | `0x10108` / 3 / `0x10100` |
| RSC IRQ enable / pending after guest handling | 3 / 0 |
| Linux XO clock report | 19,200,000 Hz |
| UFS/SMMU data path | All 19 original driver/I/O checks and host payload comparison passed |

The guest's completion interrupt and the host's subsequent read-only
observation of the completed command and cleared pending bit verify actual
request/acknowledgement flow. The host did not inject an RSC completion or
change guest MMIO. The file gate uses the generated functional-subset DT;
it is separate from the production kernel/full-DT diagnostics.

This Linux run establishes a request to the statically extracted XO address.
It does **not** establish that Linux issued requests to all five conditional
VRM clock addresses, or validate those inferred addresses against tablet
hardware. Those five clock behaviors have the independent QTest evidence
above. The private file remains incomplete, and it contains no inferred
regulator rows or synthetic fallback rows.

Receipts and logs are retained under
[revalidation-10](../evidence/expansion-20261123/revalidation-10/rpmh-summary.json):
[22-case suite](../evidence/expansion-20261123/revalidation-10/rpmh-qtest.json),
[migration](../evidence/expansion-20261123/revalidation-10/rpmh-migration.json),
[Linux file gate](../evidence/expansion-20261123/revalidation-10/rpmh-linux-file.json),
and [Linux console](../evidence/expansion-20261123/revalidation-10/rpmh-linux-file.log).

```sh
python3 tb371fc/scripts/boot_ufs.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --kernel /absolute/path/linux-rpmh/artifacts/Image \
  --machine tb371fc,rpmh-profile=file,rpmh-cmd-db=/private/aop-static-plus-clock-partial.bin \
  --require-rpmh --out /work/a-new-rpmh-file-linux-run
```
