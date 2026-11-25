# DDR statistics reference: one native diagnostic attempt

The reviewed CI artifact for source `7d01e5e294b788f97a8120c2d34122ddb25e6343` was executed once with the preserved original kernel, ramdisk, full device tree and command line. The native result was **`kernel-failure`**: the runner and host job exited **2**, while QEMU exited **0** after an acknowledged host QMP quit. This package records a failed diagnostic boot and does not claim Android userspace, completed boot or an interactive display.

## Exact identity and scope

| Item | Value |
|---|---|
| Source commit | `7d01e5e294b788f97a8120c2d34122ddb25e6343` |
| Source tree | `5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4` |
| Push CI run / attempt | `45544186469` / `1` |
| Artifact ID | `14991304675` |
| Artifact ZIP | `57,340,476` bytes; SHA256 `1e6c37d205a9e130e4f197c068c93c756488b6b579ab06eadd429190b9b0bd6f` |
| QEMU binary | `63,727,664` bytes; SHA256 `935549d9f359d04254f5627bdd8807fd8d1800ab2223ab52dea80c0cf7edecb6` |
| CI qualification SHA256 | `aacc8a4c439244e51e8b32a0cf8f4698c7cedbeab6aaafe02a6c9b91eaef9c28` |
| Native interval (UTC) | `2026-11-25T05:34:51.782663` to `2026-11-25T05:34:59.105705` |
| Native attempts represented | `1`; persistent once claim; automatic retry disabled |
| Original storage LUNs attached | `0` |
| Outcome | `kernel-failure`; runner `2`; host job `2`; QEMU `0` |

[The immutable CI recovery evidence](https://github.com/cyjin-yl/qemu-tb371fc/blob/6b11a65d4469486cd4ebb9be7283dee74f1b7c44/tb371fc/evidence/ddr-stats-offset-7d01e5e-ci-recovery-20261125-6a5/README.md) retains the successful reviewed qualification, its complete public dependencies and the failed first qualifier. This native evidence adds observations of the selected artifact; it does not change those records or the executable source.

The ready input set was absent at selection. The invocation therefore used the explicitly recorded diagnostic scope with no original storage LUNs, no block nodes and no overlays. Storage completeness, snapshot consistency and factory-image provenance remain unestablished. The input kernel, ramdisk, full device tree and command line matched their before/after hashes; the AOP data, symbols and QEMU binary also remained unchanged. No test init was injected and the original command line was not modified.

## What the actual run observed

Kernel entry and ramdisk unpack were observed. The first fatal kernel error occurred at guest time **3.216410 s** in **`get_se_proto+0x4/0x10`**, with the link register in **`msm_geni_serial_probe+0xa30/0xc20`**. The associated invalid read was **4 bytes at physical `0x00998068`**. The kernel panic followed at **3.219426 s**.

The complete QEMU diagnostic log contains **two** Data Abort records. The earlier CPU4 abort (`ESR 0x96000005`, `FAR 0`) returned from the exception; it is not described as the fatal GENI error. The later CPU6 abort has `ESR 0x96000010`, `FAR 0xffffff800b3d8068` and `ELR 0xffffff8008ce8194`. Across the complete log there is one invalid read and no invalid writes.

The earlier DDR fault (`ddr_stats_probe+0x88/0x180`) was not observed in this attempt. Complete-log checks found zero invalid-read matches for its diagnostic addresses `0x0c3f001c` and `0x1c`, zero `ddr_stats_probe` mentions and zero fatal DDR PC matches. Address matching was case-insensitive and allowed leading zeroes. The only current invalid physical read is the GENI address above. This is bounded evidence that the observed failure moved to GENI. **It does not validate a DDR statistics payload or demonstrate successful DDR driver probing.** Android userspace, completed Android boot and an interactive display were not observed.

The complete printk ring was independently decoded to **1,141 records**, reproducing the **76,194-byte** collected log exactly. QMP contains **22 messages**: one greeting, ordered replies 1–16 and five events. Guest watchdog/reset events precede the acknowledged host quit (request 16). The final transport reset is recorded as errno 104 with zero residual bytes and no prior command error. These facts explain QEMU's controlled exit 0; the guest result remains a kernel failure.

## Collection corrections and permissions

The first read-only inventory incorrectly treated the persistent once-claim directory as a regular file. It failed before emitting an inventory and was corrected to select the existing attempt record. The first transfer exceeded the shell's 65,536-byte stdout bound and was rejected. Five deterministic chunks then reproduced one complete bundle; all **31 files / 2,236,242 bytes** were verified by complete byte length and SHA256. These corrections did not invoke or retry the native workload.

The host terminal log was actually observed with mode **0664**, not 0600. A directory default ACL was directly observed, with default mask `rwx` and default other `r-x`; the terminal's access ACL had mask `rw-` and other `r--`. The parent directory's access ACL had mask `---` and other `---`. The inherited ACL explains the observed file permission result, while the parent directory remains restrictive. Named ACL identities are intentionally omitted. No permission repair or native rerun is asserted by this evidence.

During preparation, the strict private-path scanner stopped before staging because the public review's internal reviewer task identifier resembled an absolute path. A versioned successor replaced only that reviewer label with a semantic identifier. Exact reverse substitution reproduces every predecessor byte; the original review and findings are retained unchanged. The provenance file pins both versions, their derivations, the actual review truth source and the stopped preparation. This correction did not weaken the scanner or execute the native workload.

## Contents and verification limits

- `sanitized-observation.json` contains explicitly selected run facts and bounded conclusions.
- `fullhash-provenance.json` records full SHA256 and byte counts for the 31 retained private source files, collection/postrun/review receipts, original input pins, runtime pins and host control pins under semantic aliases.
- `independent-review.json` is the exact independently prepared public-safe review, including chronology, full-log boundaries, the prior fault comparison and retained inspection correction.
- `manifest.json` pins the four content files in this package. Its own identity is supplied by the containing Git commit and publication receipt, avoiding a recursive self-hash.

The source allowlist is explicit. No raw private command line, device tree, serial identity, local path, signed URL, raw private log, private source file or binary bytes are included. Original instruction bytes and device-tree extracts are also omitted. Full hashes bind retained private evidence but do not make those withheld inputs publicly reproducible. Preparation and collection were read-only with respect to the native workload.

Independent actual-result review SHA256: `419fe8aec5f66a0167acea497b3511c30eb111da3f81df2cd9c5f68708189307`.
