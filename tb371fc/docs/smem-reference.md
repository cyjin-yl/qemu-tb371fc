# Explicit empty SMEM v12 handoff

The `smem-profile=reference-empty` machine option creates a functional,
synthetic empty SMEM allocator handoff for direct-kernel boot. It initializes
ordinary board RAM. It does not emulate SBL execution, import a tablet SMEM
snapshot, provide socinfo, create a remote processor, or answer firmware IPC.

The default is `smem-profile=off`. The off profile does not write a handoff
blob or reinterpret existing RAM.

## Invocation and lock policy

Select both the handoff and its separate mutex assumption explicitly:

```sh
qemu-system-aarch64 \
  -M tb371fc,smem-profile=reference-empty \
  -global qcom-tcsr-mutex.boot-unlocked-reference=on \
  -m 8G -smp 8 -kernel Image -initrd initramfs.cpio.gz
```

The handoff option does not silently change TCSR's unknown initial owner state.
Starting the reference handoff without the unlocked-reference TCSR option is a
configuration error. The TCSR owner is an execution-environment token, not a
CPU identifier; Linux still supplies local CPU exclusion in its hwspinlock
core.

Eight GiB is shown to retain the board's normal `0xa0000000` kernel loading
base. Smaller test RAM configurations remain possible, but a large kernel
loaded near `0x80000000` may overlap the SMEM window. QEMU's ROM loader must
reject that overlap; moving or shrinking SMEM silently is not allowed.

## Evidence and reference choices

The SMEM range, `0x80900000` through `0x80afffff`, and TCSR lock index 3
come from [captured geometry](../evidence/smem-mutex-20261123/geometry.json).

The on-memory ABI has been checked against two primary source trees:

- [Mainline Linux v6.12 SMEM driver](https://github.com/torvalds/linux/blob/v6.12/drivers/soc/qcom/smem.c),
  SHA256 `0736029ee4505f23cf13d5f9b6d3e407eb1ed07f1a806cc8f24a7a4b18da2b75`.
- [Official OnePlus SM8250 SMEM driver, fixed commit](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/smem.c),
  SHA256 `b0eef6fc4ba901731936ef8b5c1c91d4b709ee714350e1a86c6c44c379f933c2`.

Both define the same fields and sizes for `smem_header`, `smem_ptable`,
`smem_ptable_entry`, `smem_partition_header`, `smem_private_entry` and
`smem_info` used here. These are shared-memory formats; the driver's private
`struct qcom_smem` changes between kernel versions and must not be treated as
a stable ABI. The official OEM source includes the Qualcomm change that maps
the directory and accessible partitions separately, matching the corresponding
data structure in the TB371FC mirror.

The separately pinned public TB371FC kernel mirror provides board-adjacent
corroboration:

- [SMEM driver](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/smem.c),
  SHA256 `3ba12c3fdeca5fea8e0df97d30a7c13d8ce6a7553610b9a3700b036bd48af380`.
- [Hardware-lock driver](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/hwspinlock/qcom_hwspinlock.c),
  SHA256 `19722f9e4e3a8ddeb11500185745e8bf72911be1a3e751f5d62986d9d66e6778`.

This public mirror is not asserted to be an official complete Lenovo source
release or proven byte-identical production source.

Layout version 12, one global partition, cacheline 64, item count 512, empty
allocations, and unlocked-reference TCSR are explicit model choices. They are
not captured native SBL values.

The v12 probe path validates the SBL initialized/reserved words, discovers the
global entry in `$TOC`, checks its `$PRT` header and free offset, reads the item
count, enumerates local private partitions, and requests the DT's hardware
lock. It does not require an allocated socinfo or IPC item to accept the SMEM
allocator itself. The sole `0xfffe:0xfffe` global entry is skipped by local-host
private-partition enumeration. Empty allocation lists are represented by
uncached offset 32 and cached offset equal to partition size; no item canary
is read until an item exists. This source-based ABI argument is separate
from runtime evidence. The separate [408d82a](../evidence/gdsc-smem-20261123/native-ci-408.json)
and [0a617bab](../evidence/gdsc-smem-20261123/native-ci-0a.json) production-driver
observations each record acceptance of this reference handoff and 23
live-state consistency checks on the unmodified production kernel, using
their respective verified CI binaries. Consumers can still fail when they
request absent firmware-populated items.

## Image layout

All numeric fields are little endian. The initialization image is exactly
`0x200000` bytes. It is copied into the existing machine RAM through QEMU's
ROM loader; there is no separate read-only memory mapping.

| Region or field | SMEM-relative offset | Value or extent |
| --- | --- | --- |
| SBL version word, `version[7]` | `0x5c` | `12 << 16` |
| Initialized flag | `0xc0` | 1 |
| Legacy free/available and reserved words | `0xc4`–`0xcf` | Zero, legacy allocator unused |
| Legacy item table | `0xd0`–`0x20cf` | 512 empty entries |
| Global partition | `0x3000` | Size `0x1fc000`, ending before the directory |
| Partition header | `0x3000`–`0x301f` | `$PRT`, hosts `0xfffe:0xfffe`, matching size |
| Initial uncached free offset | Global + `0xc` | `0x20` |
| Initial cached free offset | Global + `0x10` | `0x1fc000` |
| Directory | `0x1ff000` | `$TOC`, version 1, one entry |
| Directory entry | Directory + `0x20` | Global offset/size, zero flags, hosts, cacheline 64 |
| Optional region info | Directory + `0x50` | `SIII`, 2 MiB, physical base, 512 item IDs |

The remaining bytes start as zero. There are no fabricated socinfo, SMD/GLINK
channel, restart-state or remote-identity payloads. The real guest driver owns
subsequent allocation and lookup operations; QEMU supplies RAM and the TCSR
MMIO contract. A request for an absent item must remain an ordinary absence
result in the guest.

The C builder checks fixed layout relationships at compile time and verifies
that the complete destination lies in ordinary board RAM at realization.
The QTest gate independently decodes the resulting image and checks magic,
version, alignment, bounds, hosts, directory/partition agreement and the
allocation frontier.

## Device tree and loader integrity

For the generated DT, the explicit profile adds the captured SMEM reserved
region, TCSR syscon/hwlock and SMEM consumer nodes. It labels the generated SMEM
node as an empty reference handoff.

For a caller-supplied DT, the board retains the caller's device nodes and adds
the SMEM range to the FDT memory-reservation map. The fixed board SMEM geometry
is not inferred from an arbitrary replacement DT. Supplying an incompatible DT
does not make its consumers valid.

The reserved range is checked by the same ROM overlap machinery used for
kernel and initramfs loading. This keeps a collision visible instead of
overwriting guest boot inputs.

## Reset and migration

A `qcom-smem-handoff` marker device exists in both profiles. It migrates only
the selected profile and rejects both off-to-reference and reference-to-off
migration. Keeping the marker present when off is necessary: omitting it could
allow an incoming stream without a marker to leave a different destination
reset policy unvalidated.

Normal system reset restores the complete empty reference image when enabled.
This discards prior reference-heap allocations as part of the declared direct
loader reset convention. Off does not reseed or erase the region.

During incoming migration, QEMU's ROM loader skips initialization writes.
Ordinary machine-RAM migration restores allocations, data and free offsets.
The device post-load hook compares profiles and does not rewrite RAM. A later
normal reset on the destination restores its matching empty reference image.

The separate TCSR VMState also requires matching initial-owner profiles.
The SMEM mismatch tests deliberately use matching TCSR profiles, ensuring the
handoff marker itself rejects the mismatch.

## Validation command and scope

```sh
python3 tb371fc/tests/test_smem_reference.py \
  --qemu /absolute/path/to/qemu-system-aarch64 \
  --out /absolute/path/to/new-smem-result
```

The gate requires 22 groups. It covers default/off behavior, invalid and
inconsistent configuration rejection, image structure, writable RAM, two
Linux-format allocation fixtures using the actual mutex, full-region RAM
preservation across migration, continued allocations, exact reset restoration,
neighbor-RAM preservation, immutable profile selection, off-to-off migration
and both incompatible migration directions.

Allocation fixtures are written through QTest; the gate does not execute a
guest CPU or claim that Linux's SMEM driver bound successfully. The caller must
run the real-driver/full-native-DT diagnostic separately and record its selected
profiles, input hashes, observed progress and remaining failure.

The [real-driver observation procedure](smem-driver-observer.md) explains how
to distinguish successful execution of `qcom_smem_probe` from the presence of
the reference image alone. It requires validating the production kernel's
symbols and instruction offsets before inspecting its live driver state.

The [408d82a public CI receipt](../evidence/gdsc-smem-20261123/ci-408d82a.json)
records the successful 22-group component gate. The separate
[original-kernel observation](../evidence/gdsc-smem-20261123/native-ci-408.json)
uses the same board executable as both public CI suite invocations and
records `qcom_smem_probe`'s successful publication with 23 consistency checks.
The observer made no guest-memory writes. These are different tests: the
component gate uses QTest allocation fixtures, while the native observation
does not claim later guest allocation operations.

The native run selected the explicit reference profiles, passed the previous
GDSC access fault and then aborted at the GCC halt-status read `0x00148198`.
It did not complete full native-DT or Android boot. The subsequent GCC
implementation has [separate passing 0a617bab CI evidence](../evidence/gdsc-smem-20261123/ci-0a617ba.json)
and a [verified downloaded executable](../evidence/gdsc-smem-20261123/artifact-0a617ba.json).
A [separate 0a617bab production diagnostic](../evidence/gdsc-smem-20261123/native-ci-0a.json)
on that exact `a2e19b18…` executable again observed SMEM probe success and
passed all 23 state checks without observer writes to guest memory. It
passed the preceding GCC abort, then panicked on an unsupported read at
GCC offset `0xf060`, physical `0x0010f060`, through `mux_get_parent` during
`gcc_kona_probe` (ESR `0x96000210`). The fixed official driver identifies
this register as the [USB3 primary PIPE source mux](gcc-sm8250.md#next-observed-boundary-usb3-primary-pipe-source-mux),
whose production selector decoding and external parents remain unresolved.
Full native-DT and Android boot remain unpassed. Neither probe acceptance nor the component suites capture native
SBL state or supply missing firmware-owned items and services.

## Future original-byte import

There is no `file` profile in this change. A later importer must retain input
bytes and provenance, distinguish runtime snapshots from pre-kernel handoffs,
and validate directory and item-chain bounds, overlap, host identities,
padding, item IDs and auxiliary regions before loading. It must not repair
inputs or insert missing firmware records to turn an invalid capture into a
successful boot.
