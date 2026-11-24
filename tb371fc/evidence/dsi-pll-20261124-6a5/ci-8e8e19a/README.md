# CI artifact qualification for DSI source 8e8e19a

This evidence package qualifies the exact runtime source **`8e8e19a97efc23c215b35ed4dd40120ffab0e40d`**, tree
**`1516df2c2b640688f2966b88ea8de4395a3b3f8f`**. It is intended for a separate evidence branch based on that
commit. A later commit that adds these files is an evidence publication only;
its commit ID is **not** the source tested by the retained CI runs. Keep the
runtime head of PR26 bound to the tested source when interpreting these receipts.

## Artifact and measured scope

- Push run: [45467596293](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45467596293).
- PR run: [45467533338](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45467533338).
- Both runs' board and Cortex-A77 jobs were observed completed with success.
- Reviewed board artifact: **14956272809**, **51,762,977 bytes**, SHA256 `ef6f842c1bd25411cd31e197819c1682548ffd79e14f82b444420e84fbeb6b44`.
- Actual ELF: **63,490,704 bytes**, SHA256 `f04e3250977c3ac8852bf1aa7692771835a657dc6a3af84c1fafe58c6c5dac2c`.
- Integration audit: **89 component + 5 clean-Linux gates**, 31 CI harness unit checks,
  complete VIDEOCC/VIDEO-GDSC streams, and source-pinned generation checks.
- DSI audit: **36 checks**, 3 completed migrations, 2 profile rejections, 4 single-bit
  rejections, 4 original / 8 total streams, all 2926 DSI + 64 MDSS bytes, and
  52 clock views. Each first target observation has 1163 pure getters and no
  device MMIO or `cont` before that complete observation.
- DISPCC audit: **42 checks**, 3 completed migrations, 2 profile rejections,
  3 single-bit rejections, 4 original / 7 total streams, all 1676 DISPCC +
  64 MDSS bytes, and 61 clocks. Each first target observation has 556 pure
  getters and no device MMIO or `cont` before that complete observation.

At qualification time, an original-firmware native attempt with this candidate
had not been started. **Android/native boot completion remains unestablished.**
The clean-Linux CI guests are separate evidence. Host recovery checks, a boot-ID
guard, deployment, and a later native attempt require their own records; these
artifact receipts do not establish them. The independent auditors read retained
ZIP evidence without extracting or executing QEMU. Separate QMP and QTest logs
do not have shared timestamps; the receipts state the actual ordering evidence
and its limits.

## Retained files and identity

`qualification.json` is byte-for-byte the local qualification file used by the
artifact binding. Its SHA256 is **`36a0a9107a217d58fed7e2c2d632d97d66c60af82eadc57a596ab22dfded8774`**. Do not reformat or rewrite it:
that digest is `reviewed_artifact.audit_receipt_sha256`.

The three successful receipts are `integration-audit.json`, `dsi-audit.json`,
and `dispcc-audit.json`. Their referenced auditors are included under their
original filenames. `github-evidence.json` retains the read-only API evidence
for the tested source/tree, both workflows/jobs, and artifact identity. Exact
bytes, SHA256, Git blob identities, original local filenames, and scope are
listed in `MANIFEST.json`.

Original receipts retain historical workspace paths and observation times.
The filename mapping in `MANIFEST.json` identifies their repository copies;
those paths have not been rewritten inside hashed receipts. Reproduced receipts
may consequently have different hashes even when all evidence checks pass.

## First DISPCC attempt and bounded correction

`dispcc-attempt1-path-failure.json` preserves the failed audit attempt. The full
artifact contains both a top-level `source-commit.txt` and a full-DTB fixture's
file of the same name. The old suffix search rejected that ambiguity before
component verification. This was an auditor path-selection failure, not a
reported device failure or a reason to weaken any wire/clock/protocol checks.

The only code change was `archive.text(archive.find("source-commit.txt"))` to
`archive.text("tb371fc-results/source-commit.txt")`; `dispcc-auditor-path-fix.patch`
records it. The old auditor SHA256 is `6e8c09c3c6c7c3690f62aac9282b31bd9756208411939e9b57030299ad4fc376`. It can be reconstructed
from a copy of the included corrected auditor by reversing exactly that one
expression. The successful receipt records the corrected auditor's own hash.

## Reproduce the three read-only audits

Use Python 3 and the original artifact ZIP. Set these paths explicitly; keep
`DSI_REVIEWED_SOURCE` at the exact qualified commit above. The DSI auditor's
source flags are mandatory here because its published location differs from
its original review workspace. Choose an output directory with new filenames.
All imported modules are from Python's standard library.

```sh
DSI_EVIDENCE_DIR=/absolute/path/to/tb371fc/evidence/dsi-pll-20261124-6a5/ci-8e8e19a
DSI_REVIEWED_SOURCE=/absolute/path/to/qemu-checkout-at-8e8e19a
DSI_ARTIFACT_ZIP=/absolute/path/to/artifact-14956272809.zip
DSI_AUDIT_OUT=/absolute/path/to/new-audit-output
mkdir -p "$DSI_AUDIT_OUT"
```

The commands below inspect existing evidence; they do not launch a guest.

```sh
python3 "$DSI_EVIDENCE_DIR/audit_dsi_integration_artifact.py" \
  --zip "$DSI_ARTIFACT_ZIP" \
  --expected-head 8e8e19a97efc23c215b35ed4dd40120ffab0e40d \
  --expected-zip-sha256 ef6f842c1bd25411cd31e197819c1682548ffd79e14f82b444420e84fbeb6b44 \
  --out "$DSI_AUDIT_OUT/integration-rerun.json"
```

```sh
python3 "$DSI_EVIDENCE_DIR/audit_dsi_pll_artifact.py" \
  --zip "$DSI_ARTIFACT_ZIP" \
  --expected-head 8e8e19a97efc23c215b35ed4dd40120ffab0e40d \
  --expected-zip-sha256 ef6f842c1bd25411cd31e197819c1682548ffd79e14f82b444420e84fbeb6b44 \
  --expected-qemu-sha256 f04e3250977c3ac8852bf1aa7692771835a657dc6a3af84c1fafe58c6c5dac2c \
  --expected-test-sha256 73c81336b97229657a19958068490102118def404a2afa4d97743ee26ffac36e \
  --test-source "$DSI_REVIEWED_SOURCE/tb371fc/tests/test_dsi_pll_7nm.py" \
  --model-source "$DSI_REVIEWED_SOURCE/hw/misc/qcom-sm8250-dsi-pll.c" \
  --out "$DSI_AUDIT_OUT/dsi-rerun.json"
```

```sh
python3 "$DSI_EVIDENCE_DIR/audit_dispcc_wide_artifact_rootpath.py" \
  --zip "$DSI_ARTIFACT_ZIP" \
  --expected-head 8e8e19a97efc23c215b35ed4dd40120ffab0e40d \
  --expected-zip-sha256 ef6f842c1bd25411cd31e197819c1682548ffd79e14f82b444420e84fbeb6b44 \
  --expected-qemu-sha256 f04e3250977c3ac8852bf1aa7692771835a657dc6a3af84c1fafe58c6c5dac2c \
  --out "$DSI_AUDIT_OUT/dispcc-rerun.json"
```

