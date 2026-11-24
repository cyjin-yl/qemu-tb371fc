# Real, bounded vbmeta metadata observations

The two captured metadata objects have consistent headers, descriptor bounds
and internal SHA-256 values. The root `vbmeta_a` declares a chain to
`vbmeta_system`; its declared child-key bytes equal the embedded public key in
`vbmeta_system_a`. The five first-stage logical partitions have corresponding
hashtree descriptors whose declared ranges fit the previously observed logical
partition sizes. **No RSA signature, trusted key, rollback store, hashtree or
partition payload was authenticated by this work.**

The capture was read-only on the owner's TB371FC. Analysis used local copies
only. The original binary metadata objects, signatures, public-key bytes and
raw rollback fields remain private and are not included in this bundle.

## Capture binding and minimum read scope

The original [capture receipt](capture-receipt.json) is copied byte-for-byte.
It records the source devices, parent SCSI addresses, exact ranges, repeated
reads and input hashes. These match the previously verified
[GPT inventory](../ufs-gpt-inventory-20261124/README.md).

| Object | LUN / partition device | Offset in full LUN, bytes | GPT partition capacity | Captured object bytes |
| --- | --- | ---: | ---: | ---: |
| `vbmeta_a` | 4 / `/dev/block/sde16` | 636723200 | 65536 | 4992 |
| `vbmeta_system_a` | 0 / `/dev/block/sda9` | 13130301440 | 65536 | 3072 |

The coordinator first read each 256-byte header twice. After validating the
declared lengths, the full metadata reads were `[0, 4992)` and `[0, 3072)`
within the two partitions, each performed twice. The full reads retained the
original header hashes and were byte-identical across their respective passes.
The full-object capture therefore read **16,128 bytes** from the device. It
did not read the remainder of either 64 KiB partition, a footer, any described
filesystem/hash-tree region, userdata or a key file.

| Object | Captured SHA-256 |
| --- | --- |
| `vbmeta_a` | `8aaf4e2241f869cb20eeae40c18e9dff4a0067dce6081842c32bc51536df9b38` |
| `vbmeta_system_a` | `ed39af24beee5e5c2e7228411becb07e8be899f39c600b610fc0a5af412738a6` |

These hashes cover the extracted metadata objects, not complete 64 KiB
partitions or full LUNs.

Both are AVB0 with required libavb version 1.0, header flags 0, correctly
bounded 64-byte-aligned blocks and zero reserved header fields. The root
declares SHA256_RSA4096, with 576 authentication bytes and 4160 auxiliary
bytes. The child declares SHA256_RSA2048, with 320 and 2496 bytes respectively.
The algorithm names are declarations; they are not evidence of a valid signature.

## Actual offline run

[attempt-001/run-receipt.json](attempt-001/run-receipt.json) binds the unchanged
analysis script to the two original metadata files and complete output reports:

- [vbmeta_a.json](attempt-001/vbmeta_a.json): 18 descriptors — one chain,
  twelve properties, three hashes and two hashtrees.
- [vbmeta_system_a.json](attempt-001/vbmeta_system_a.json): 14 descriptors —
  eleven properties and three hashtrees.

Both CLI executions returned 0 with `metadata_checked_not_authenticated`.
Their stdout and stderr files are retained as empty originals because the
reports were written using `--output`. The local file rereads are separate
from the source-device rereads in the capture receipt.

The frozen script was 14,631 bytes with SHA-256
`caabb3c1b40e4bd98c966cf34b76835dd384882288b3edb53de8d10a2009817c`.
Its sixteen synthetic regression tests passed. The reference source is
[AOSP libavb 1.3.0 at commit 761178607206f4cb2af79ed9eec52d8cbd814adb](aosp-source-reference.json).
The source-reference file distinguishes Gitiles-declared blob IDs from hashes
actually computed over captured inputs and outputs.

For each input, the checker hashed the 256-byte header followed by the entire
auxiliary block, including its padding, and compared that SHA-256 to the stored
32-byte digest in the authentication block. Both comparisons matched. The
authentication block itself is excluded from this digest; an altered RSA
signature would not change this result. Signature verification was not run.

## Declared chain and first-stage partition mapping

[bindings.json](bindings.json) records the derived comparisons, their source
hashes and the unverified boundaries. The chain record in `vbmeta_a` names
`vbmeta_system`, has flags 0 and rollback-index location 2. Under the explicit
`_a` analysis choice, it selects `vbmeta_system_a`. Its 520 public-key bytes
were compared directly to the child's embedded key and were identical, with
SHA-256 `22de3994532196f61c039e90260d78a93a4c57362c7e789be928036e80b77c8c`.
This confirms the relationship declared by these two untrusted metadata objects;
it does not supply a trusted root key.

The mapping uses the coordinator's first-stage storage map, identified by
SHA-256 `6155e3fbd284c6be57f7e9f4e0841b4265cfc9cbaef8b119d75ae579200c9643`.
That map joins the earlier sanitized `fstab.qcom`, GPT attempt 003 and super
metadata slot 0. `_a` is the selected run slot; it was not inferred to be the
running device's active slot. The source map also leaves effective snapshot
mapping and consistency with the older captured boot image unverified.

| Selected logical partition | Descriptor owner | Declared filesystem bytes | End of declared tree/FEC ranges | Observed logical partition bytes |
| --- | --- | ---: | ---: | ---: |
| `system_a` | `vbmeta_system_a` | 6063558656 | 6159626240 | 6160855040 |
| `system_ext_a` | `vbmeta_system_a` | 508215296 | 516280320 | 516452352 |
| `product_a` | `vbmeta_system_a` | 599121920 | 608620544 | 608817152 |
| `vendor_a` | `vbmeta_a` | 791560192 | 804106240 | 804339712 |
| `odm_a` | `vbmeta_a` | 991232 | 1011712 | 1097728 |

All five records declare SHA-256, 4096-byte data/hash blocks, dm-verity version
1 and FEC with two roots. The table compares **declared** byte ranges with the
previously observed LP capacities. No filesystem, Merkle tree, root digest or
FEC bytes were checked. It also does not establish the live device-mapper
snapshot mapping when COW state may be involved.

The root's three hash descriptors cover `boot`, `dtbo` and `recovery`, selecting
the `_a` labels in this analysis. Their declared image lengths are 40,214,528,
4,299,963 and 51,941,376 bytes respectively, each within its GPT partition.
Those lengths describe the region to be hashed by an AVB content verifier;
they are not the full partition capacities and have not been checked against
the stored payloads or the older boot input.

## Publication and qualification limits

The public reports contain labels, ranges, algorithm/flag values and summaries.
Property keys/values, command lines, release strings, signatures, public-key
bytes and raw rollback-index values are not printed. Raw binary captures and
their transport containers are excluded. This report does not read the fstab's
redacted `avb_keys` file paths or infer which external key is trusted.

All report fields for signature checks, trusted-key checks, rollback protection,
vbmeta authentication, partition-content verification and Android boot remain
false. The binding report also leaves cross-capture atomic consistency and
effective snapshot mapping false. Nothing here changes the Android input
preparation/runner qualification flags or creates a complete raw-LUN manifest.

From this directory, `sha256sum -c SHA256SUMS` verifies the published evidence.
The [tool documentation](../../docs/vbmeta-metadata.md) explains invocation,
the deliberately bounded input profile and the regression entry point.
