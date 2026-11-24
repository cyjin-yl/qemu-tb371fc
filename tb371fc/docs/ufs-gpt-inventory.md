# Read-only UFS GPT inventory

`scripts/inspect_ufs_gpt.py` checks the partition metadata of one **complete raw
LUN** or an explicitly allowed Linux/Android block device. It reads the protective
MBR, both GPT headers and both declared entry arrays, then reads the same five
regions again. It does not read partition contents or repair either GPT.

This closes a storage-input question for original Android work: whether the
supplied LUN geometry has consistent partition locations and requested labels.
It does not establish that `super` contains valid logical partitions, that AVB
accepts an image, that encrypted data is usable, or that Android boots.

The [2026-11-24 real-device evidence](../evidence/ufs-gpt-inventory-20261124/README.md)
preserves three original attempts. The final attempt verified both GPT copies
on six live 4096-byte-block LUNs, with 91 active entries, using the two explicit
policies below. The earlier strict failures remain failures. The evidence
includes the observed SCSI-to-LUN descriptor mapping and exact frozen driver
hashes; it does not provide complete raw LUN images.

## Usage

For an existing raw backup, supply its independently known logical block size:

```sh
python3 tb371fc/scripts/inspect_ufs_gpt.py \
  --input /private/backups/lun0.raw --logical-block-size 4096 \
  --require-partition super --require-partition vbmeta_system_a \
  --require-partition metadata --output /private/receipts/lun0-gpt.json
```

Each required label applies only to this one LUN. Choose it from the actual
device mapping; the example does not establish a partition-to-LUN assignment.

For a deliberately selected device, explicit permission is required on the
command line; open flags are `O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK`:

```sh
python3 tb371fc/scripts/inspect_ufs_gpt.py \
  --input /dev/block/sda --allow-block-device --logical-block-size 4096 \
  --output /private/receipts/live-lun0-gpt.json
```

Block devices use [`BLKSSZGET` and `BLKGETSIZE64`][linux-ioctl] on the open descriptor. If a
logical block size is also supplied, it must match. Device geometry is queried
again at the end. Input identity and filesystem metadata are checked before and
after; final-component symlinks, FIFOs and other input types are rejected.
Parent-directory symlinks are not prohibited. The ioctl constants
target the 64-bit Linux/Android ABI used by this project.

Use one fresh receipt per attempt. An existing output is never overwritten.
Rejection produces exit code 1 and a receipt with all acceptance flags false.
The tool never writes to the source, opens a partition filesystem or mounts it.

## Checked contract

The on-disk format follows [UEFI 2.11 chapter 5][uefi]; the paired-header and
array checks were also compared with [Linux v6.12.114 `efi.c`][linux]. Both GPT
copies must be valid and agree; fallback/recovery behavior is deliberately absent.

- Protective MBR signature, a single `0xee` record, exact capacity coverage and
  no hybrid records. Exact capacity coverage is the default policy; the narrowly
  scoped, explicit saturated-size exception is described below. CHS and the
  boot flag are not used as addresses.
- Revision 1.0 headers, CRCs, reserved bytes, self/alternate positions, matching
  disk identity, usable ranges, entry sizes and counts.
- Bounded entry arrays, each CRC and byte-for-byte agreement between copies.
  Arrays must be outside usable data and inside the corresponding header bounds.
- Every used entry's type, unique identity, inclusive extent, reserved fields,
  UTF-16 label and non-overlap. Duplicate nonempty labels are rejected because
  they make by-name lookup ambiguous; unnamed partitions remain permitted.

The bounded inventory profile accepts 512- or 4096-byte logical blocks, entry
widths of `128 * 2^n`, and arrays up to 16 MiB each. Unknown revisions, nonzero
extended/reserved fields, malformed used-entry labels, sparse-image containers
and QCOW2 are rejected. Nonzero unused entries are rejected by default, with the
explicit opaque classification described below. These are input-profile limits;
the tool is not a general-purpose disk recovery utility.

**A 16 KiB reservation does not require 128 entries.** For example, 32 entries
of 128 bytes occupy 4096 bytes, while GPT reserves a larger area. The checker
validates both quantities separately and hashes only the declared table bytes,
not unrelated reserved gaps. GPT LBAs use the declared logical block size;
Linux sysfs `start`/`size` counters instead use 512-byte sectors.
For the observed 4096-byte devices, a GPT LBA is eight of those legacy sectors:
`offset_bytes = gpt_lba * 4096 = linux_sector * 512`. A UFS unit descriptor's
`logical_block_size` is a base-2 exponent; `0x0c` means 4096 bytes, not 12 bytes.
The real evidence's descriptor count and ioctl capacity were compared before
publishing its mapping. See the [Linux UFS sysfs ABI][linux-ufs] for this encoding.

### Explicit saturated protective-size compatibility

The default `--pmbr-policy strict` requires the protective record's starting LBA
to be 1 and its size to equal `min(total_logical_blocks - 1, 0xffffffff)`.
UEFI defines that extent. The fixed [Linux parser][linux] also accepts a
protective size of `0xffffffff` on a smaller disk; it even treats other size
mismatches as diagnostic-only. This tool does **not** adopt the latter behavior.

When actual metadata has been observed to use the saturated value, a new attempt
may explicitly select:

```sh
python3 tb371fc/scripts/inspect_ufs_gpt.py \
  --input /dev/block/sda --allow-block-device --logical-block-size 4096 \
  --pmbr-policy allow-saturated-size --output /private/receipts/live-lun0-gpt-v2.json
```

This policy adds exactly one alternative extent: starting LBA 1 with size
`0xffffffff`. All other mismatched sizes and starts still fail. Signature,
single-record, reserved-byte, GPT/header/array/geometry and partition checks are
unchanged. The LUN bounds still come from the file or actual device ioctls and
must match both GPT headers; the protective size never supplies those bounds.

The receipt records `actual_size_lbas`, `expected_size_lbas` and `extent_policy`.
If the exception was needed, `pmbr_standard_extent` is false and
`sentinel_compatibility` is true. A saturated size that is already standard for
a sufficiently large disk remains `pmbr_standard_extent=true` and is not marked
as an exception. A compatible receipt does not assert fully UEFI-conforming
protective metadata. Preserve a failed strict attempt separately; changing the
policy does not rewrite its result or prove any partition content.

### Explicit classification of type-zero residue

[UEFI table 5.6][uefi] defines a zero `PartitionTypeGUID` as an unused entry.
The fixed [Linux `is_pte_valid()`][linux] similarly rejects type zero before
publishing a partition or its label. Nonzero fields in such an entry therefore
do not establish another active partition.

The default `--unused-entry-policy strict` retains the original requirement that
an unused entry be entirely zero. If actual metadata contains type-zero residue,
a new attempt may explicitly select `--unused-entry-policy allow-type-zero-residue`.
This classifies the standard 128-byte entry body as opaque residue; the extension
after byte 128, if present, must still be zero. It does not exempt any nonzero
type GUID from the existing used-entry checks.

The receipt lists each residue's 1-based `entry_index`, full `record_sha256`, and
the position, length and SHA-256 of each nonzero field. It does not decode or
publish its stale name, unique GUID, attributes or LBA values. Such fields are
not used as addresses and cannot satisfy `--require-partition`. No record is
added to `partitions`, even if its residue looks like a valid partition.

`unused_entry_count` includes entirely zero entries and type-zero residue;
`unused_zero_entry_count` and `unused_type_zero_residue_count` show the split.
Both arrays' full CRCs, byte-for-byte comparison and the actual second read
continue to cover every residue byte. The old strict failure remains a separate
attempt; opaque classification does not prove full-medium content or restore a
deleted partition.

## Receipt and limits

The JSON records the input kind and capacity, both decoded headers, partition
labels/types/ranges, and each observed region's offset, byte length and SHA-256.
Disk and per-partition unique GUIDs are emitted only as hashes. Type GUIDs are
shared format identifiers and are retained. Raw metadata and payload bytes are
not exported.

`metadata_sha256` uses a versioned domain prefix, capacity and logical block
size, followed by each observed region's offset, length and original bytes.
It is **not the SHA-256 of the LUN**. Do not put this value into a storage manifest
field that expects a complete raw-image SHA-256.

`gpt_metadata_verified` and `metadata_observed_unchanged` describe only these
reads. `required_partition_labels_verified` is true only when an explicit,
nonempty required-label set was supplied and found. These flags always remain
false:

```text
partition_contents_verified
full_lun_content_hashed
complete_backup_verified
atomic_snapshot_verified
android_boot_verified
```

Even a live device with unchanged GPT metadata can have changing partition
contents. The result is not an atomic snapshot or a coherent backup. Keep
existing Android input/runner qualification fields unchanged; this inventory
is a separate receipt, not a replacement for their storage manifest.

## Regression entry point

```sh
python3 -m unittest discover -s tb371fc/scripts -p test_inspect_ufs_gpt.py -v
```

Fixtures cover the target's 4 KiB/32-entry and 4 KiB/64-entry geometries, 512-byte
blocks, extended records, corrupt headers/tables, actual CRC-colliding unequal
arrays, bounds and overlap, required labels, changed input, and explicit block
device policy. Block ioctl tests use a simulated descriptor geometry; a real
device receipt must be recorded separately.

The original 31-test suite remains intact. Five additional tests cover explicit
saturated-size selection, continued default rejection, all other mismatches,
standard large-disk saturation, and unchanged downstream rejection gates.
Six more tests cover type-zero classification, exclusion from labels/partitions,
continued checks on nonzero types and reserved extensions, and CRC/copy/reread
coverage of residue bytes. The policies have independent CLI selections.

[uefi]: https://uefi.org/specs/UEFI/2.11/05_GUID_Partition_Table_Format.html
[linux]: https://github.com/gregkh/linux/blob/v6.12.114/block/partitions/efi.c
[linux-ioctl]: https://github.com/gregkh/linux/blob/v6.12.114/include/uapi/linux/fs.h
[linux-ufs]: https://github.com/gregkh/linux/blob/v6.12.114/Documentation/ABI/testing/sysfs-driver-ufs
