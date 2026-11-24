# Bounded, offline vbmeta metadata inspection

`scripts/inspect_vbmeta_metadata.py` inspects a previously captured **exact
vbmeta object**: its 256-byte header, authentication block and auxiliary block.
It accepts regular files of at most 64 KiB and does not open devices, partition
payloads, footers, key files or paths named by descriptors. This is a small
diagnostic tool for the storage-input work, not an AVB authentication engine.

The [real-device evidence](../evidence/vbmeta-metadata-20261124/README.md) binds
two captures to GPT locations, verifies their internal hashes and explains the
declared `vbmeta_a` → `vbmeta_system_a` relationship. It keeps signature,
trusted-key, rollback-store, partition-content and Android-boot checks separate.

## Source and bounded profile

The reference is AOSP `external/avb` commit
[`761178607206f4cb2af79ed9eec52d8cbd814adb`][aosp], libavb 1.3.0. The evidence's
[source reference](../evidence/vbmeta-metadata-20261124/aosp-source-reference.json)
records fixed file links and the blob identifiers declared by official Gitiles.

The [header][header] is big-endian. Its authentication and auxiliary sizes are
multiples of 64; their sum plus 256 must equal this tool's input length. The
checker validates version, algorithm, ranges, reserved header bytes and flags.
Nonempty authentication subranges must not overlap; the same restriction
applies to auxiliary descriptor, public-key and public-key-metadata ranges.
For an explicitly selected chained image, header flags and the header's own
rollback-index location must be zero.

For signed algorithms, [libavb hashes the header and complete auxiliary
block][verify], excluding the authentication block. This tool recomputes that
digest and rejects a mismatch. It checks declared RSA key/signature lengths,
but **does not check the RSA signature or RSA key parameters**. For algorithm
`NONE`, the internal-hash result is `null`; unsigned data is not reported as
having a matching digest.

[Descriptors][descriptors] have a 16-byte header and an eight-byte-aligned
length. Each declared record must fit the descriptor area exactly. The five
recognized tags are property, hashtree, hash, kernel command line and chain.
Type-specific sizes, bounds, flags and reserved bytes are checked before
variable data is accessed. Hash/hashtree records report their declared image,
tree and FEC geometry without reading or hashing those regions.

Additional input-profile limits are explicit: partition labels use at most
127 ASCII letters, digits, underscores, periods or hyphens; `.` and `..` are
rejected. Descriptor padding and reserved fields must be zero. Recognized
digest names are `sha1`, `sha256` and `sha512`; a zero-length descriptor digest
is marked as requiring a persistent digest, which is not read. The hashtree
profile accepts dm-verity version 1 and power-of-two blocks from 512 through
65536 bytes. Unknown tags/flags/algorithms, overlapping fields, duplicate
partition descriptors and trailing partition padding are rejected. Duplicate
property records remain permissible. These limits are not a claim that every
other encoding would be rejected by every libavb consumer.

## Invocation

With a separately captured, bounded file:

```sh
python3 tb371fc/scripts/inspect_vbmeta_metadata.py \
  --input /private/metadata/vbmeta_a.vbmeta --label vbmeta_a \
  --slot-suffix _a --role root --output /private/new-vbmeta-a-report.json

python3 tb371fc/scripts/inspect_vbmeta_metadata.py \
  --input /private/metadata/vbmeta_system_a.vbmeta --label vbmeta_system_a \
  --slot-suffix _a --role chained --output /private/new-vbmeta-system-a-report.json
```

The slot suffix is an explicit analysis choice, not a reading of the device's
active slot. Descriptor names acquire that suffix unless their `DO_NOT_USE_AB`
flag is set. No derived label is opened as a filesystem path. Chained children
are not discovered or fetched automatically.

Regular inputs are opened read-only with final-component symlinks prohibited.
Two actual `pread` calls, plus file identity/size/timestamp checks, establish
that the captured file was observed unchanged. That result concerns the local
file; live-device capture stability must come from a separate capture receipt.
An output must be new. Rejection returns exit code 1 and false authentication,
partition-content and boot qualifications without printing untrusted payloads.

## Output and limits

The report contains metadata/input hashes, algorithms, flags, descriptor
labels and geometry. Public keys, signatures, property keys and values,
kernel command lines, release fields and raw rollback-index values are
represented by lengths and/or SHA-256 instead of their original bytes or text.
Rollback-index locations are retained because they describe chain routing;
no persistent rollback storage is queried.

A success status is `metadata_checked_not_authenticated`. In particular,
changing a signature can leave the internal digest unchanged because the
authentication block is excluded from that digest. A regression test exercises
exactly that case and ensures that it never produces an authentication claim.
Matching a parent's declared child-key bytes to a child's embedded key also
does not authenticate either image. A trustworthy chain additionally requires
signature verification, an independently trusted root key and the relevant
rollback and partition-content checks.

```sh
python3 -m unittest discover -s tb371fc/scripts -p test_inspect_vbmeta_metadata.py -v
```

The sixteen synthetic tests exercise all five tags, malformed ranges and
lengths, an altered internal digest, signature-only mutation, unsigned data,
header roles and flags, privacy, label/slot behavior, file mutation between
actual reads, and refusal to overwrite existing receipts. Synthetic keys are
deliberately not valid RSA signing keys: these tests do not simulate successful
signature authentication.

[aosp]: https://android.googlesource.com/platform/external/avb/+/761178607206f4cb2af79ed9eec52d8cbd814adb
[header]: https://android.googlesource.com/platform/external/avb/+/761178607206f4cb2af79ed9eec52d8cbd814adb/libavb/avb_vbmeta_image.h
[verify]: https://android.googlesource.com/platform/external/avb/+/761178607206f4cb2af79ed9eec52d8cbd814adb/libavb/avb_vbmeta_image.c
[descriptors]: https://android.googlesource.com/platform/external/avb/+/761178607206f4cb2af79ed9eec52d8cbd814adb/libavb/avb_descriptor.h
