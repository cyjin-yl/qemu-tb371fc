# RPM statistics offset: compiled CI evidence for `66d9e20`

The exact push artifact for source commit `66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b` passed push and pull-request CI, seven independent audits of the actual downloaded ZIP, and the supplementary log and UFS observations recorded here. The [final qualification](qualification/rpmstats-66d9e20-qualification.json) has `passed: true` and was assembled at `2026-11-25T02:52:43.316545+00:00`.

**The original Android native attempt had not started at this snapshot.** This evidence establishes compiled CI behavior within the stated model boundaries. It does not establish original Android boot completion, userspace, UI, firmware execution, or physical hardware behavior. The qualification retains `native_started`, `native_executed`, `android_boot_established`, `android_userspace_established`, and `ui_established` as false.

## Exact identities

| Item | Identity |
|---|---|
| Repository | `cyjin-yl/qemu-tb371fc` |
| Qualified source | `66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b` |
| Source tree | `7c13a3b85075e0eaefcc14d4f55c160bed699a08` |
| Sole source parent | `75cc1dc83600784afa06e81c9998d8d39aba6af2` |
| PR | [36](https://github.com/cyjin-yl/qemu-tb371fc/pull/36) |
| Recorded PR base | `8d0bc64d8da6da18bf43b52a2a9547f28f44c3e9` |
| Actual PR merge checkout | `c7ba198d6bf9bd5e3255e207d07efd85a026a1a9` |
| Push run, attempt 1 | [45533031476](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45533031476) |
| PR run, attempt 1 | [45533037101](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45533037101) |
| Push board / A77 jobs | `138932791819` / `138932792020` |
| PR board / A77 jobs | `138932812241` / `138932812391` |
| Audited push artifact | `14986446678` |
| Actual ZIP | 56,393,004 bytes; SHA256 `809199bd6f39181dfe9340a60bf58fde4544b536d752736f0f475fbc89e46ec4` |
| Actual QEMU ELF | 63,700,664 bytes; SHA256 `019c56322a1dfd64a5db9d82719b93d49f7e6c7a5891e708f6f9f4c535aa703e` |

The source and PR merge have the same tree. The final [GitHub metadata](metadata/rpmstats-66d9e20-final-ci-github-metadata.json) records both runs and all four jobs as completed successfully. The separate [PR/base record](metadata/rpmstats-66d9e20-pr-base-identity.json) is a captured identity observation, not a claim that the mutable branch or PR will remain unchanged.

## Measured results

| Scope | Actual evidence |
|---|---|
| Each board job | 97 component gates and 5 Linux gates; 102 distinct PASS records, ordered progress, successful summaries, and every job step successful |
| CI harness unit tests | 35 tests, OK, zero skips in the actual push ZIP; both board job host logs also show 35 tests |
| Host suites in each board job | Counts in order: 15, 35, 28, 43, 16, 15; these additional host runs are separate from the 97 component / 5 Linux gate totals |
| Each A77 job | Nine configurations totaling 852 guest checks, two rejected CPU configurations, and seventeen PCI UFS cases |
| Extra Linux UFS observations | Active checkpoint: 39 checks; runtime-suspended checkpoint: 61 checks |
| RPM statistics source generation | Four complete pinned public inputs and two generated outputs |
| RPM statistics compiled test | Twenty groups; two completed same-profile migrations, two profile mismatch directions, and six single-bit mutant rejections |

The [board observation](observations/rpmstats-66d9e20-board-rawlog-observation-001.json) checks the exact checkout, all job steps, actual gate records, host counts, extra UFS summaries, and the small CI-unit members of the actual push ZIP. The [A77 observation](observations/rpmstats-66d9e20-a77-rawlog-audit-001.json) independently sums the actual runner output. Its scope is the complete job logs; the individual CPU artifact's guest PASS labels and the full stderr of the two rejected configurations were not downloaded by that observer.

The [extra UFS observation](observations/ufs-66d9e20-final-observation-001.json) retains the archive's actual reports and host media-comparison receipts. The workflow excludes those extra UFS raw disk and migration files from its artifact, so this observation does not claim to rehash the excluded media or decode its complete migration wire. Its recorded process ordering uses host monotonic observations. Inflight UFS migration, system suspend, and transfer of external disk bytes remain outside its scope.

## Seven actual ZIP audits

All seven receipts are complete, unchanged copies. Each receipt's auditor hash was matched to the corresponding frozen source under `auditors/` before this publication bundle was assembled.

| Audit | Receipt |
|---|---|
| Integration, all component/Linux gates, CI unit tests and generated inputs | [Integration audit](audits/rpmstats-66d9e20-final-integration-audit-001.json) |
| DISPCC | [DISPCC audit](audits/dispcc-66d9e20-final-independent-audit-001.json) |
| DSI PLL | [DSI PLL audit](audits/dsi-66d9e20-final-independent-audit-001.json) |
| DP PLL | [DP PLL audit](audits/dp-66d9e20-final-independent-audit-001.json) |
| LLCC | [LLCC audit](audits/llcc-66d9e20-final-independent-audit-001.json) |
| SPSS IRQ mask | [SPSS audit](audits/spss-66d9e20-final-independent-audit-001.json) |
| RPM statistics offset | [RPM statistics audit](audits/rpmstats-66d9e20-final-independent-audit-001.json) |

These auditors read actual archived evidence. They did not execute QEMU, a guest, a native boot, device RPC, or SSH while auditing. The [qualification assembler](qualification/assemble_rpm_stats_offset_66d9e20_qualification.py) and its [successful execution receipt](qualification/qualification-assembly-execution-001.json) are also preserved exactly.

The minimal RPM statistics device maps only the four-byte offset word at `0x0c3f0004`. The opt-in reference layout supplies `0x000f0048` for the permitted aligned 32-bit read. Default-off reads, all writes, and the tested narrow, unaligned and 64-bit access forms are rejected with the actual recorded diagnostics. Adjacent space, DDR offset, and statistics payload remain unmapped. A valid four-byte prefix in a five-byte raw read is distinguished from its rejected unmapped suffix.

The device has immutable configured metadata. Migration prewarming changes a RAM witness only; no mutable or dirtied device state is claimed. The RPM audit checks two original streams and six complete one-bit-mutated streams, the 24-byte version-1 payload, first and repeated sets of seventeen pure QOM getters, and the restored RAM witness before continuation. This does not supply live statistics records, firmware progress, readiness, or native probe completion.

## Source provenance and retained failures

The [corrected-head publication review](source/rpmstats-66d9e20-independent-publication-review-001.json) verifies the three correction files, the resulting 38 published paths, unchanged remaining tree entries, and five full runner-file readbacks at the exact corrected head. The [earlier 36-file review](source/rpmstats-75cc1dc-independent-publication-review-001.json) and both source manifests preserve the publication chain. The source itself is available at the immutable [qualified commit](https://github.com/cyjin-yl/qemu-tb371fc/tree/66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b).

The earlier `75cc1dc83600784afa06e81c9998d8d39aba6af2` build failed because its header included `hw/sysbus.h`. The [actual failure observation](failures/rpmstats-75cc1dc-failed-ci-observation-001.json), failed run/job metadata, [correction receipt](failures/ci-include-path-correction.json), and [independent correction review](failures/include-path-correction-review.json) retain that result. The corrected include is `hw/core/sysbus.h`. The failed earlier run has not been reclassified as a pass.

Two local unit attempts also remain failures. [Attempt 001](failures/ci-local-unit-001.json) used Python `-I`, could not import the sibling module, and ran no tests. [Attempt 002](failures/ci-local-unit-002.json) ran 35 tests: 34 passed and one socket-related test errored with `PermissionError: [Errno 1] Operation not permitted`. Both exited 1 and retain `formal_ci_pass_claimed: false`. The later actual CI result of 35 OK is a separate observation. This bundle includes the bounded local failure receipts; it does not add raw local host logs.

Files under `historical/` retain earlier CI and PR32 runner behavior/template reviews referenced by the qualification. They are historical evidence. Current runner byte identity comes from the five corrected-head readbacks, and current CI qualification comes from the actual runs and artifact above.

## Complete public CI logs

[The public log archive](logs/rpmstats-66d9e20-public-ci-rawlogs-001.json) preserves all four complete public GitHub job texts: 2,694,024 original bytes in 392,022 gzip bytes. The JSON includes each role, run, job, actual checkout, original byte count/SHA256, gzip byte count/SHA256, and base64 payload. It retains the connector-returned UTF-8 bytes, including BOM and newlines; no lines were normalized. Gzip uses level 9, an empty filename, and mtime 0.

The [post-write readback](logs/rpmstats-66d9e20-public-ci-rawlogs-readback-001.json) decoded and decompressed the saved JSON and compared every recovered byte with the original files. The [builder](logs/build_public_ci_log_archive_66d9e20.py) is included. To restore the logs from this directory:

```python
import base64, gzip, hashlib, json
from pathlib import Path

archive = json.loads(Path("logs/rpmstats-66d9e20-public-ci-rawlogs-001.json").read_bytes())
out = Path("restored-ci-logs")
out.mkdir(exist_ok=False)
for item in archive["logs"]:
    packed = base64.b64decode(item["gzip_base64"], validate=True)
    assert len(packed) == item["gzip_bytes"]
    assert hashlib.sha256(packed).hexdigest() == item["gzip_sha256"]
    raw = gzip.decompress(packed)
    assert len(raw) == item["original_bytes"]
    assert hashlib.sha256(raw).hexdigest() == item["original_sha256"]
    assert Path(item["filename"]).name == item["filename"]
    (out / item["filename"]).write_bytes(raw)
```

## Inventory and practical limits

[`manifest.json`](manifest.json) pins the bytes, SHA256 and Git blob SHA1 of every other file in this evidence directory. The Git commit identifies the manifest itself; the external publication/readback receipt also pins that file. The selection was explicit, with no directory glob used to choose publication contents.

The board ZIP and QEMU ELF are identified by their complete-byte hashes; they are not embedded in this evidence directory. Re-executing the ZIP audits requires that exact retained artifact. The frozen auditor and assembler sources preserve the actual execution inputs and include original working-path references; paths must be reconstructed or supported source arguments supplied when reproducing the audits. Editing a frozen source changes its auditor hash.

The separate A77 metadata files were captured after the A77 jobs completed while board jobs were still running. The final combined GitHub metadata records the completed state of both workflows. The initial artifact-download receipt likewise predates the independent audits and honestly retains `independent_audits_completed: false`; the later final qualification establishes their completion.

This directory contains public code, actual public CI evidence and bounded review receipts. It excludes signed artifact redirect URLs/configuration, private original firmware, raw native or private host logs, host preflight/AGENTS data, coordinators, bindings, and launchers. No native Android result is added to this CI snapshot.
