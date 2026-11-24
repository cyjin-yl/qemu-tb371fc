# Android super metadata inventory

`scripts/inspect_android_super.py` reads a bounded, metadata-only prefix captured
from a raw `super` partition. It binds the declared device size to a separately
measured partition size, validates both geometry copies and both copies of one
explicit metadata slot, and inventories logical partitions and extents. The
original prefix and all partition payloads remain private.

This complements the Android boot-input manifest's raw-LUN size and hash
checks. It does not prepare a usable disk, authenticate AVB, determine snapshot
merge state or run Android. The existing boot-input and runner files are not
changed by this increment.

## Fixed format sources

The implementation is an independent Python parser based on AOSP
`platform/system/core`, Android 14 revision
`f53626de0f57e5ba91931d8c2154a83407c1d570`:

- [metadata_format.h](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/liblp/include/liblp/metadata_format.h): packed field sizes, versions, table descriptors, attributes and 512-byte sector units.
- [reader.cpp](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/liblp/reader.cpp): geometry, header and table SHA-256 checks, table bounds and slot interpretation.
- [utility.cpp](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/liblp/utility.cpp): primary/backup offsets and total metadata-region size.
- [builder.cpp](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/liblp/builder.cpp): exported partitions may have zero extents, including empty inactive partitions.
- [libsnapshot/snapshot.cpp](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/libsnapshot/snapshot.cpp): `GetCowName` and snapshot creation place `<base>-cow` partitions in the `cow` group. The parser explicitly handles that form despite the older alphanumeric-only comment in `metadata_format.h`; it still rejects other punctuation, missing bases and incorrect groups.

The geometry checksum is SHA-256. An old `reader.cpp` comment calls it CRC32,
but the executed source calls `SHA256`. Header and table checksums are also
SHA-256. These source references describe the supported format; they do not
authenticate the OEM's installed binaries.

## Minimal read-only capture

All offsets below are bytes from the start of the physical `super` partition.
Do not confuse liblp's fixed 512-byte extent units with the 4,096-byte UFS
logical block size.

1. Measure the actual partition capacity independently, for example with the
   Linux `BLKGETSIZE64` ioctl on a read-only descriptor.
2. Read 4,096 bytes at offset 4,096 and again at offset 8,192. Each geometry
   structure occupies the first 52 bytes. Check magic, structure size and
   SHA-256, zeroing checksum bytes 8–39 while calculating it. Require both
   copies to agree.
3. Let `M = metadata_max_size` and `N = metadata_slot_count`. Bound the complete
   prefix length to `12,288 + 2*M*N`; this inspector caps it at 16 MiB.
4. Capture exactly that prefix starting at offset zero, then read it again
   and compare the bytes. Record the actual capacity and both-read result in
   a separate receipt. No logical partition contents are needed.
5. For selected slot `s`, primary metadata starts at `12,288 + s*M`; its backup
   starts at `12,288 + (N+s)*M`. Slot `_a` selects `0`. Neither the selected
   slot nor its capacity is inferred from the filename.

The prefix includes all slots' reserved regions because the geometry describes
their total layout. Only the explicitly selected slot's metadata is interpreted;
the report does not claim other-slot validation.

Example using the real metadata capture described below:

```sh
python3 tb371fc/scripts/inspect_android_super.py /private/super-prefix.bin \
  --partition-size 12884901888 --slot 0 \
  --expected-sha256 6bf1fbcdb6e63a6208aebd088cbe11ad0c6040fab6c3ec45fc15e547623e551c
```

The input must be a regular capture file containing exactly the metadata
prefix. The tool opens it read-only with `O_NOFOLLOW | O_NONBLOCK`, rejects
nonregular inputs without waiting for a FIFO writer, checks its identity and
bytes again after inspection, and emits JSON to stdout. It never opens an
entire device or writes a partition. This means the supplied partition size
is an external input supported by the capture receipt, not a size measured
by the offline parser itself.

## Checks and explicit scope

Supported metadata versions are 10.0, 10.1 and 10.2 with exact 128/256-byte
headers and the four fixed table entry sizes. Each table must be bounded,
nonoverlapping and collectively cover the table payload. Each extent has one
partition owner. Linear mappings must be aligned to the declared logical block,
inside the declared data region and physically nonoverlapping; zero extents
have no physical target. Group limits, names, attributes, indices, resolved slot
suffixes and the actual supplied physical-partition size are checked.

The current tool supports one physical `super` partition. Multi-device LP
layouts are rejected explicitly. Unknown informational header flags are
recorded because AOSP defines these flags as extensible information; unknown
partition/group/device semantics, target types and reserved bytes are rejected.
No arbitrary punctuation or path-like partition names are accepted. Combining
the supported `-cow` partition-name form with `SLOT_SUFFIXED` is explicitly
unsupported; otherwise the resolved name could escape the COW base/group checks.

Requiring both valid, identical selected-slot copies is stronger than liblp's
runtime fallback from a damaged primary to its backup. A rejected capture is
not a claim that Android's recovery reader could not choose a usable copy.

The following remain false even after a successful report:

- logical partition payload verification;
- full `super` or full-LUN byte identity;
- AVB authentication and first-stage mount verification;
- snapshot state/merge consistency;
- Android boot or QEMU device equivalence.

## Actual TB371FC metadata capture, 2026-11-24

The [capture receipt](../evidence/android-super-metadata-20261124/capture-receipt.json)
records a read-only capture from the running owner's TB371FC `super` partition,
`sda6`. The two prefix reads matched. This is current-device metadata, without
a claim that the current partitions are pristine factory images.

| Input | Actual value |
| --- | --- |
| Partition capacity | 12,884,901,888 bytes |
| Geometry logical block | 4,096 bytes |
| Maximum metadata size | 65,536 bytes per copy |
| Metadata slot count | 3 |
| Selected slot | 0 (`_a`) |
| Captured prefix | 405,504 bytes |
| Prefix SHA-256 | `6bf1fbcdb6e63a6208aebd088cbe11ad0c6040fab6c3ec45fc15e547623e551c` |

[Attempt 001](../evidence/android-super-metadata-20261124/attempt-001.json)
rejected the valid `-cow` names under the original strict name rule. The
failure remains unchanged. After the fixed AOSP snapshot naming source was
checked, [attempt 002](../evidence/android-super-metadata-20261124/attempt-002.json)
passed on the same input bytes. Its driver SHA-256 is
`ab3dffac437249709bf6b1f0b4a99822fc3154a605deb889a4e8bb0cc9404790`.

[Attempt 003](../evidence/android-super-metadata-20261124/attempt-003.json) reran
the unchanged capture after adding the two input-boundary checks above. Its
driver SHA-256 is
`d2a7f6afcb03fb10f873df75dba6c067c1e632aeab3869646b5a1cf0e6dba170`;
the successful report is byte-identical to attempt 002. The original attempts
remain unchanged. Reversing the [v2-to-v3 patch](../evidence/android-super-metadata-20261124/driver-v2-to-v3.patch)
and then the [v1-to-v2 patch](../evidence/android-super-metadata-20261124/driver-v1-to-v2.patch)
recovers the exact previous drivers; the [attempt index](../evidence/android-super-metadata-20261124/attempts.json)
binds every driver and report hash.

Both geometry copies and both selected-slot metadata copies passed. The LP
metadata is version 10.2 and has 8 partition records, 20 extents and 3 groups.

| Logical partition | Metadata size in bytes | Classification |
| --- | ---: | --- |
| `odm_a` | 1,097,728 | Base partition |
| `product_a` | 608,817,152 | Base partition |
| `system_a` | 6,160,855,040 | Base partition |
| `system_ext_a` | 516,452,352 | Base partition |
| `vendor_a` | 804,339,712 | Base partition |
| `odm_a-cow` | 135,168 | COW partition record |
| `product_a-cow` | 12,013,568 | COW partition record |
| `system_a-cow` | 4,753,719,296 | COW partition record |

The five base records have `READONLY | UPDATED` attributes. The COW records use
the `cow` group and total 4,765,868,032 bytes. Their presence means snapshot-related
metadata must be accounted for by the eventual boot inputs. It does not prove
that an OTA is active, that merging is in progress or that the stored COW
payloads match current snapshot status. `/metadata/ota` state and the actual
mapping still require their own observation; this tool does not read them.

## Snapshot state follow-up

The same pinned AOSP revision's
[`snapshotctl.cpp`](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/libsnapshot/snapshotctl.cpp)
routes `dump` through `SnapshotManager::Dump`. That method opens the metadata
directory read-only with no flock, reads state and snapshot records, and does
not itself mount metadata or write snapshot state. However, while the stored
state is `Merging`, it can call `EnsureSnapuserdConnected` to report merge
progress. The
[`snapuserd_client.cpp`](https://github.com/aosp-mirror/platform_system_core/blob/f53626de0f57e5ba91931d8c2154a83407c1d570/fs_mgr/libsnapshot/snapuserd/snapuserd_client.cpp)
startup helper can set `ctl.start=snapuserd` if the service is not running.
Consequently, `snapshotctl dump` is not unconditionally a read-only operation.
This source audit does not establish that the installed OEM binary is identical.

A separate snapshot observation should preserve the actual state-file bytes
and read/parse failures, then assess mapping and COW consistency. A missing or
unreadable state must not be upgraded to a clean OTA state: the AOSP state
reader can return `None` after a read failure. This metadata inspector neither
runs `snapshotctl` nor reads snapshot-state files.

## Verification

```sh
python3 tb371fc/scripts/test_inspect_android_super.py -v
```

The 23 tests exercise both-checksum copies, explicit slots, source mutation,
version/attribute and table-boundary rejection, extent ownership and physical
overlap, group limits, zero/empty extents, slot suffixes, COW naming and the
512-byte/4-KiB unit distinction, plus bounded rejection of a real FIFO.
Fixtures are self-authored synthetic metadata;
the separate real capture result above does not depend on those fixtures.
