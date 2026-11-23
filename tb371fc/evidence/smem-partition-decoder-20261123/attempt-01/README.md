# SMEM partition decoder: 28 actual synthetic tests passed

The exact reviewed decoder and fixtures ran in doesvm CT102 on 2026-11-23. The task completed at 14:51:35 UTC with exit 0. All **28 synthetic unittest methods passed** in the single permitted fixture phase; no test was skipped. The process was reaped, no process group remained, no timeout occurred, and all six installed source hashes matched before and after.

The original [job result](job.result.json), [installation receipt](install-receipt.json), [test stderr](results/fixtures.stderr) and [job exit](job.exit) are preserved byte for byte. [manifest.json](manifest.json) records their identities, the exact package, installation/test windows and the separate raw tool audit, including the earlier read-only preflight timeout.

## What is implemented

The pure decoder checks an ELF64 AArch64 image and an explicit pointer-slot/target pin, then evaluates two producer configuration layouts independently:

| Candidate | Host enum width | Configuration entry stride |
| --- | ---: | ---: |
| Ordinary word enum | 4 bytes | 36 bytes |
| Short-enum counterhypothesis | 2 bytes | 32 bytes |

It preserves the producer's host-sentinel short circuit, zero-size skip and IGNORE semantics. These configuration entries are distinct from the 48-byte consumer TOC entries. The pinned primary sources and their hashes are recorded in [source-contract.json](source-contract.json).

The fixtures cover bounds, ambiguous LOAD mappings, non-file-backed or executable targets, pointer identity, both candidate layouts, terminators and malformed or truncated data. They operate on authored in-memory images.

## Actual scope

**No real firmware input was opened by this job.** The native pointer pin remains unset and the native CLI refuses before opening a file. No structure-table layout or producer enum ABI has been selected for TB371FC. No SMEM handoff was generated and no initialization or Android boot was established.

The exact executed files are under `method/`. Their package-creation text correctly retains the earlier unexecuted status; the actual subsequent execution is established by the original result files above. The only test-source change from the initial draft was the fixed sibling import required by isolated Python; all original 28 test method bodies were retained.

A native adapter still needs a separately checked DAL result, an explicitly justified pointer slot and target, and bounded output. A synthetic pass must not fill in a native pointer, silently choose one layout, or substitute a default partition table.
