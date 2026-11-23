# 2026-11-23 public CI and native-diagnostic evidence

This archive preserves selected original results from source commit
`e47e361d760d528bc99201db1c8b1eefae3cd27e` and
[workflow run 45169333977](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45169333977).
The source tree is `5fe9d4cffbbdd398c240c4708410ac08a5acd45c`.
The archive is evidence-only: no model or test was rerun while preparing it.

## Actual public results

| Result | Observed outcome | Receipt |
| --- | --- | --- |
| Components | 52 of 52 gates passed | [Original JSON](gpu/tb371fc-results/components/result.json) |
| Clean upstream Linux | Four of four gates passed | [Original JSON](gpu/tb371fc-results/linux/result.json) |
| GPU CX GDSC | 26 behavior checks; eight migration assertions | [Original device JSON](gpu/tb371fc-results/components/39-gpu-cx-gdsc/fixtures/data/result.json) and [gate JSON](gpu/tb371fc-results/components/39-gpu-cx-gdsc/case.json) |
| Actual GDSC migration attempts | Nine attempts: six completed, three expected configuration-mismatch rejections | Same original device JSON |
| Cortex-A77 | Nine variants; 852 guest instruction/ID checks and two rejected invalid configurations | [Original JSON](a77/a77-probe/result.json) |
| PCI UFS compatibility | 15 of 15 checks passed | [Original JSON](a77/ufs-pci-results/result.json) |

The 56 public gates are the component and Linux gates together. The GDSC
assertions are inside their component gate; they must not be added to 56 as
independent top-level gates. A77/PCI are a separate job and build.

The component/Linux/GDSC binary SHA-256 is
`4be01de351f13b81e72d4cfbaee477ab5df4f66012a35df723bc2e2a8730b422`.
The separate A77/PCI binary SHA-256 is
`cfe3b6a38d43b51eafef6efa8c82a4b68a9d23c5d3dd4c91613af536c58b48d1`.
Both artifacts identify the source commit above.

## Integrity and original bytes

[manifest.json](manifest.json) maps every included receipt to its source and
records byte count, SHA-256 and Git blob ID. [SHA256SUMS](SHA256SUMS) covers the
archive files, including the manifest; it excludes itself.

The six result/case JSON files were compared byte-for-byte with their exact
members in the two already-downloaded public artifact ZIPs. Both complete ZIP
sizes and SHA-256 digests were checked again during archive preparation:

| Artifact | ID | ZIP size | ZIP SHA-256 |
| --- | ---: | ---: | --- |
| Public board gates | 14816377510 | 32983070 | `5f371cfc783942109fb65f2411c6a2c854993f2998bc98128dc1ffe39ccc19cb` |
| A77 / PCI gates | 14816537411 | 28317476 | `0864a0469672fc304bb4d926d42beaa4ce2bc1f717749dac6da1f9985861f639` |

The original JSON files retain their original CI paths, commands and result
fields. They were not replaced by `result.derived.json`.
The ZIPs, built binaries, signed download URLs and `downloads.json` are not
part of this archive.

[public-readback-summary.json](public-readback-summary.json) is the unchanged
receipt of the earlier artifact inspection, not a CI-produced test result.
It also records an AOP reference preflight failure from another artifact.
That failure remains a failure and is not included in the 56-gate result.
The [GPU inspection](gpu/inspection.json) and
[A77 inspection](a77/inspection.json) record the selected original members.

## Two native full-DT diagnostics: capture completed, boot failed

[native-progression.json](native-progression.json) is a separate, deliberately
limited public summary of two actual CT102 diagnostics with the same board
binary. Its private execution receipt is pinned by hash. Original firmware,
complete logs and memory dumps are not published here.

Both 12-second observations completed capture without observer/printk errors;
both kernels panicked and did not complete boot. Their shared assumptions
include a partial command database, SPMI reference and REFGEN disabled
reference. Input and binary digests remained unchanged.

| GPU CX input | Printk records | Observed result |
| --- | ---: | --- |
| Default unknown | 337 | CX unknown-state read refused; `gdsc_probe` abort |
| Explicit collapsed reference | 339 | CX supply message observed; the last GDSC probe before the abort is the GPU GX node |

The GX node is `3d9100c.qcom,gdsc`. The selected QEMU logs contain no physical
fault-address line. The exact failing physical transaction is therefore not
independently established by this summary. Both experiments still report
SMEM error `-22` and an init panic.

Observer completion, QEMU quit success and public clean-Linux success do not
establish original Android boot or full-tablet equivalence. GPU CX tests cover
a digital contract, not rendering, GPU GX, analog supply-good or silicon timing.

## Preserved history and branch behavior

The [first CT checkpoint](../gpu-cx-ci-20261123/checkpoint.json) and
[first public CI failure](../gpu-cx-ci-20261123/attempt-01/manifest.json) remain
unchanged. The new successful receipts do not rewrite their verdicts.

The proposed archive branch `tb371fc-ci-evidence-20261123-1208` does not match
the pinned workflow's push patterns `tb371fc` or `tb371fc-verified-*`.
This archival update does not itself claim a new CI run. Existing source,
model, test and workflow files remain those of the pinned base tree.
