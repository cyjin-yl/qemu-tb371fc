# Real UFS GPT inventory, 2026-11-24

The third read-only attempt on the owner's running **Lenovo TB371FC** verified
both GPT copies of all six observed UFS LUNs: **91 active partition entries**.
It used two explicitly selected compatibility policies. Every report records
`metadata_observed_unchanged=true` after a second read of the same metadata.
These results establish the observed GPT layout, not a complete disk backup or
an Android boot in QEMU.

The earlier attempts remain here with their original failures. No failed
receipt was replaced with a successful result, and the three frozen driver
versions remain identifiable by their exact source hashes.

## Original attempts

Each attempt directory contains six JSON reports, six stdout files, six stderr
files and one run receipt. All 36 stream files are empty: the checker wrote its
report through `--output`. They are retained as zero-byte originals.

| Attempt | UTC interval on 2026-11-24 | Driver | Explicit compatibility policy | Actual result |
| --- | --- | --- | --- | --- |
| [001](attempt-001/run-receipt.json) | 12:58:00.958214–12:58:02.003914 | v1 | None; original strict checks | All six rejected with `pmbr_extent`; downstream GPT checks were not reached |
| [002](attempt-002/run-receipt.json) | 13:01:50.600497–13:01:51.589769 | v2 | `allow-saturated-size` | `sda` passed with 15 partitions; the other five rejected with `unused_entry` |
| [003](attempt-003/run-receipt.json) | 13:07:25.095553–13:07:26.160402 | v3 | `allow-saturated-size` and `allow-type-zero-residue` | All six passed; 91 active entries, five opaque type-zero residues |

The run receipts bind each report's original byte length and SHA-256 to its
exit code, driver hash and observation time. All three record
`driver_unchanged=true`, `read_only_source=true`, `complete_backup_verified=false`
and `android_boot_verified=false`. These are recorded execution observations,
not signed or remote attestations.

[export-manifest.json](export-manifest.json) is the unchanged manifest supplied
with the export. All **57 files, totalling 84,758 bytes**, were independently
matched against its byte lengths and SHA-256 values before being copied here.
Its top-level `json_*` and `gzip_*` fields describe the transport envelope, not
the aggregate report bytes; that envelope is not needed to read these files.
The repository copies preserve the original bytes, including whitespace and
the empty streams. [SHA256SUMS](SHA256SUMS) additionally covers this bundle's
documentation, derived mapping, historical patches and original export manifest.

## Final observed geometry

The input kind in every final report is `block_device`. The checker bound
capacity and logical block size to the open read-only descriptor using
`BLKGETSIZE64` and `BLKSSZGET`, then queried them again at the end. All six
logical block sizes were **4096 bytes**.

The LUN numbers below follow the observed SCSI addresses and UFS unit
descriptors. They are not inferred from alphabetic `sdX` naming. The
[derived descriptor mapping](lun-descriptor-mapping.json) preserves the
allowlisted source fields and the private source container's hash. It does not
publish the container's unrelated OTA state. Each descriptor has block-size
exponent `0x0c`, meaning `2^12 = 4096` bytes; its block count agrees with the
corresponding final report. The descriptor and inventory observations were
sequential, not an atomic device snapshot.

| LUN / device | Observed SCSI address | Capacity in bytes | 4096-byte logical blocks | Equivalent 512-byte sectors, derived | Active / declared GPT entries | Type-zero residues |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 0 / [sda](attempt-003/sda.json) | `0:0:0:0` | 125720068096 | 30693376 | 245547008 | 15 / 32 | 0 |
| 1 / [sdb](attempt-003/sdb.json) | `0:0:0:1` | 8388608 | 2048 | 16384 | 2 / 32 | 1 |
| 2 / [sdc](attempt-003/sdc.json) | `0:0:0:2` | 8388608 | 2048 | 16384 | 2 / 32 | 1 |
| 3 / [sdd](attempt-003/sdd.json) | `0:0:0:3` | 33554432 | 8192 | 65536 | 4 / 32 | 1 |
| 4 / [sde](attempt-003/sde.json) | `0:0:0:4` | 2147483648 | 524288 | 4194304 | 59 / 64 | 1 |
| 5 / [sdf](attempt-003/sdf.json) | `0:0:0:5` | 33554432 | 8192 | 65536 | 9 / 32 | 1 |

**Keep the two sector units separate.** GPT LBAs and UFS descriptor block counts
use the device's 4096-byte logical blocks. Legacy Linux sysfs block `start` and
`size` counters use 512-byte sectors; [the Linux GPT parser][linux-gpt] converts
by `logical_block_size / 512` before publishing partition ranges. On this
device, multiply a GPT LBA by 4096 for its byte offset, or multiply it by 8 for
the equivalent Linux sector number. The table's 512-byte counts are explicitly
derived from the ioctl-verified capacity, not a replacement sysfs capture.

All twelve headers have revision `0x10000`, header size 92 and entry size 128.
Primary headers are at LBA 1, their arrays start at LBA 2 and the first usable
LBA is 6. Backup headers are at the final logical block, their arrays begin
four blocks before that header, and the last usable LBA is five blocks before
it. The 32-entry arrays contain 4096 bytes; `sde`'s 64-entry arrays contain 8192
bytes. The reserved space is at least 16 KiB on each side. A 16 KiB reservation
does not imply that 128 entries must be declared.

The separate, earlier
[header-only observation](live-ufs-header-metadata-20261124.json) is also copied
unchanged. Its twelve header-block hashes agree with the final reports. It
does not replace the later full metadata checks and did not by itself qualify
either entry array or the protective MBR.

### Required labels actually requested

The final `sda` report explicitly requested and found `metadata`, `super` and
`vbmeta_system_a`. The final `sde` report explicitly requested and found
`boot_a`. The other four reports requested no labels and correctly leave
`required_partition_labels_verified=false`; they still verified the GPT.

The partition records include every active entry's label, shared type GUID,
inclusive LBA range and byte offset/length. Unique disk and partition GUIDs
are represented only by SHA-256. A label's presence establishes its place in
the GPT; it says nothing about the partition's contents or whether it can boot.

## Why the explicit policies were necessary

### Protective MBR size

All six observed protective MBR blocks have SHA-256
`e53182c76db6385b5bb8e9235f35c193fa0dd2fd2941a0fd86a641dec8db9920`.
Their only record has type `0xee`, starting LBA 1 and size `0xffffffff`.
These LUNs are smaller than that protective extent. Consequently every final
report records both the actual and expected size, with
`pmbr_standard_extent=false` and `sentinel_compatibility=true`.

[UEFI 2.11][uefi] specifies the smaller of the remaining device blocks and
`0xffffffff`. The fixed [Linux implementation][linux-gpt] accepts the saturated
value on a smaller disk and is also tolerant of other size mismatches. This
tool adopts only the explicitly requested saturated-value exception. Wrong
starts, arbitrary mismatched sizes, hybrid records, bad reserved bytes and all
GPT/header/array/bounds failures remain rejections. These results therefore do
not assert fully UEFI-conforming protective metadata.

### Unused entry residue

Each LUN except `sda` has exactly one entry with a zero type GUID but nonzero
standard fields. Their 1-based entry indices are 3 (`sdb`, `sdc`), 5 (`sdd`),
60 (`sde`) and 10 (`sdf`). UEFI table 5.6 defines the zero type GUID as unused;
Linux's `is_pte_valid()` similarly excludes it before publishing a partition.

The explicit v3 policy classifies these as opaque residues. Reports preserve
each whole-record hash and nonzero field position/size/hash without decoding
stale names, unique GUIDs, attributes or addresses. They are excluded from the
91 active partitions and cannot satisfy a required label. Reserved entry
extensions must still be zero. Full-array CRCs, byte-for-byte comparison of
the two arrays and the second read all include these residue bytes.

Both policies default to `strict`. The final attempt selected them explicitly
after the original failures and the actual field forms had been examined. No
nonzero type GUID receives an exception, and neither policy supplies or changes
the physical device bounds.

## Frozen drivers and local verification

[driver-versions.json](driver-versions.json) records the exact source commits,
byte sizes, hashes and policy selection for each attempt. The development
regression runs passed 31, 36 and 42 tests respectively; their historical test
stdout is not part of this export.

| Driver | Bytes | SHA-256 |
| --- | ---: | --- |
| v1 | 17369 | `bfffbb8285fd390d9b748fea1759b3882e4badef1a70432741b5f5507cb5de14` |
| v2 | 18294 | `e9036483a2d8733a833c6ad3ef14007ab7da89e393370cee4b8433c87df390fa` |
| v3 | 20344 | `e8c249951969648435729be19191ec91b7ca0ce992a5e05bf7f584ce1e09dab8` |

The checked-in [script](../../scripts/inspect_ufs_gpt.py) is the frozen v3 used
for attempt 003. The script-only patches in [driver-history](driver-history)
allow exact reconstruction of v2 and v1 even if later repository integration
squashes the development commits. Reverse `driver-v2-to-v3.patch` from a copy
of v3, then reverse `driver-v1-to-v2.patch` from that copy of v2. Both restorations
were checked against the full hashes above; no device access is involved.

From this evidence directory, verify the published files with:

```sh
sha256sum -c SHA256SUMS
```

For the parser regression suite, run from the repository root:

```sh
python3 -m unittest discover -s tb371fc/scripts -p test_inspect_ufs_gpt.py -v
```

The [tool documentation](../../docs/ufs-gpt-inventory.md) describes its full
contract and command-line options. For a separately authorized future read,
the v3 invocation for the observed LUN 0 would be:

```sh
python3 tb371fc/scripts/inspect_ufs_gpt.py \
  --input /dev/block/sda --allow-block-device --logical-block-size 4096 \
  --pmbr-policy allow-saturated-size \
  --unused-entry-policy allow-type-zero-residue \
  --require-partition metadata --require-partition super \
  --require-partition vbmeta_system_a --output /private/new-lun0-receipt.json
```

This is a reference invocation, not an additional run or a verbatim acquisition
shell transcript. Confirm the device mapping again for a new acquisition and
choose a fresh output path; an existing receipt cannot be overwritten.

## Qualification boundary

The final inventory read only five regions per LUN, twice: protective MBR,
primary header, backup header, primary array and backup array. The declared
regions total 20 KiB per pass for five LUNs and 28 KiB for `sde`, or 128 KiB
per pass across the six. Unrelated reserved gaps and all partition contents
were outside those reads. The second observation establishes equality of the
observed metadata, not atomic consistency of data on a running Android device.

The final reports deliberately keep all of these flags false:

```text
partition_contents_verified
full_lun_content_hashed
complete_backup_verified
atomic_snapshot_verified
android_boot_verified
```

`metadata_sha256` is the domain-separated digest of the five observed regions
plus their geometry; it is not a whole-LUN SHA-256. Neither it nor an individual
header/array hash may populate `luns[].sha256` in an Android storage manifest.
This evidence bundle and its descriptor mapping are not that manifest. They
cannot turn a partition slice or a partly acquired image into a complete raw
LUN, and they do not change existing Android preparation/runner qualification
fields. Super contents, AVB, encrypted userdata and an actual Android boot each
require separate evidence.

[uefi]: https://uefi.org/specs/UEFI/2.11/05_GUID_Partition_Table_Format.html
[linux-gpt]: https://github.com/gregkh/linux/blob/v6.12.114/block/partitions/efi.c

UFS descriptor exponent and count semantics are documented by
[Linux's UFS sysfs ABI](https://github.com/gregkh/linux/blob/v6.12.114/Documentation/ABI/testing/sysfs-driver-ufs).
