# Default-branch continuation preserved at 339b9cd

This integration reads the complete, non-truncated Git trees for
`339b9cd277aeae84fba52b60ae34ede90b7ff101` (default branch snapshot) and
`d79052f306ec37a797d05c9e4b2f8f7ea69c0675` (development snapshot).
The default ref is not moved by this work. The comparison found **34
default-only files, 39 development-only files and 32 differing shared files**.
The [complete preservation plan](default-history-339b9cd/merge-plan.json)
records every path, original blob and resolution.

## Runtime integration

The default branch's SMMUv2 change adds VMState version 2 with equality checks
for MMIO aperture, context-bank count and SMR capacity before loading arrays.
Its [migration contract](../docs/smmuv2-migration.md), original 18-check test,
and original before/failure/after receipts are retained.
The combined source must pass its own public gates; none of the earlier
artifacts is relabeled as a build of this integration.

The development branch's later GCC source regeneration, 83 external parents
and VMState 3, AOP reference peer, separate GPU CX/GX contracts, GPUCC and
NPUCC remain in place. MPM2 adds its own fixed 32768-Hz four-byte counter and
independent tests. The public plan is 65 component gates plus four Linux boots.

## Original bytes retained

| Default material | Preserved location | Treatment |
| --- | --- | --- |
| GDSC/SMEM/GCC CI and native history, 23 blobs | [gdsc-smem-20261123](gdsc-smem-20261123/) | Entire original tree `18069438d6a9708098fcd67d2107eb70edacb76b` |
| SMMUv2 migration history, five blobs | [smmuv2-migration-20261123](smmuv2-migration-20261123/) | Entire original tree `4882058b6a394ef53af1737eccde1f0ac4b8da51` |
| Earlier generic GDSC document/test | [document](default-history-339b9cd/legacy-gdsc.md), [test](default-history-339b9cd/legacy-test_gdsc.py) | Archived original bytes; not the current CX/GX contract or registered test |
| Default validation ledger | [ledger](VALIDATION-default-339b9cd-20261123.md), [original checksum](default-history-339b9cd/VALIDATION-continued-20261123.sha256) | Separate byte archive; does not overwrite the development ledger |
| Two UFS private-input manifests | Existing `private-source-manifest.json` names | Blob-identical to the default's old `sha256.json` paths; corrected names retained |

The copied default ledger keeps its original evidence-directory depth, so
its relative receipt links remain usable. The archived generic GDSC files and
checksum retain their original bytes and paths inside the content; consult
[the fixed original source tree](https://github.com/cyjin-yl/qemu-tb371fc/tree/339b9cd277aeae84fba52b60ae34ede90b7ff101)
for their original context. The checksum names the original ledger filename.
These are frozen source material, not new execution receipts. The [earlier default-history index](DEFAULT-HISTORY-5fd52c1.md)
continues to identify the preceding frozen records.
