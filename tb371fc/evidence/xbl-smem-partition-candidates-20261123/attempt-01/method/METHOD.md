# Conditional SMEM partition observation adapter -- review draft

## Current evidence and purpose

DAL03 is an actual completed CT run: 62 synthetic tests passed, then one explicit compact-format scan produced one bounded DAL candidate. Its original scan is 7,358 bytes, SHA256 839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053, archived at commit 1ec5285e71af7db3b8766ea8523e9b219c10cc96 under tb371fc/evidence/xbl-dal-candidates-20261123/attempt-03/.

That candidate reports static local values smem_vers=0x000c0000, smem_toc_vers=1, smem_max_items=625 and smem_partitions index 58. These are observations of a bounded static table; native platform selection, entry reachability and SMEM initialization remain unproven.

The new adapter is a separate review draft. Its source and 14 authored fixture methods have not been Python-compiled or executed. It has not read the actual structure target. It must receive its own approved execution scope before a CT run. No test result or firmware observation is implied by this document.

## Unchanged analysis core

The adapter reuses the exact 16,046-byte smem_partition_candidates.py source with SHA256 17ce02aae0f4c24d68824d69e3421408e1bf58ab03ad08b28c3059c4ca4caf18, Git blob 05cef8961c00d6c14bb0b1786da82ec0d47f4953. Its independent synthetic run actually passed 28 tests. The core, its CLI refusal and NATIVE_PIN=None remain unchanged.

The adapter loads only this fixed sibling source, checks its size/mode/hash and executes the exact hashed Python bytes. This is execution of our own analysis implementation, not execution of XBL or AOP. It registers the own-source module before exec because the existing core uses dataclasses. It checks NATIVE_PIN is still None when loading and after analysis, and never assigns to it.

The old core's draft-time comments describe its original synthetic-only scope. This new reviewed adapter is the proposed additional input binding. It does not turn its local PointerPin value into a selected native pin, alter the original core's claims, or enable the old core CLI.

## Explicit mode and fixed inputs

The new CLI accepts exactly --conditional-observed-slot. No input path, alternate report, record count or ABI-selection option is accepted. Missing or extra arguments return a fixed rejection before opening either private input.

The fixed inputs are:

| Input | Size | SHA256 |
| --- | ---: | --- |
| /work/device-assets/xbl-capture-20261123-01/xbl_a.bin | 3,670,016 | eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55 |
| /work/xbl-dal-candidates-20261123-03/results/scan.stdout | 7,358 | 839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053 |

Both must be regular 0600 files with no final-component symlink. File identity, size and modification/change timestamps must remain stable while read. Whole-image reading is needed to check its SHA; it is not permission for unrelated semantic scanning. The code writes no files itself and never reads a block device.

Byte hashes identify the reviewed artifacts. They are not hardware attestation, and a caller cannot cause the CLI to label an arbitrary hash as captured or native. The known report's provenance is the actual DAL03 job and its immutable archive, not merely a hash-shaped string.

## Receipt validation before target interpretation

The standard JSON decoder is used with duplicate-key and nonfinite-number rejection. Required counters and indices must be integers rather than Python booleans. The adapter requires the exact v3 schema and explicit compact format, one accepted/unomitted candidate, one bounded/processed candidate, and no candidate-processing truncation.

Both top-level and candidate runtime/selection assertions must remain false. The original descriptor location, 17-entry device array, target index 11, descriptor lookup validation, scope, local scalar values and structure index/base must agree with the fixed observation contract. The four properties are not supplied from defaults.

Both structure-layout records must be present under distinct known labels. All native-layout, ABI, total-length and target-content claims remain false. A missing or failed alternative is not repaired from the other layout.

The relevant observations are:

| Role | VA | File offset | Interpretation |
| --- | --- | ---: | --- |
| Structure table base | 0x14879510 | Derived base metadata | Not an array-length declaration |
| Plain index-58 record / hypothesized pointer slot | 0x148796e0 | 423024 | 8-byte record mapped, target mapping failed |
| Sized index-58 record | 0x148798b0 | 423488 | 16-byte record mapped, dwSize=8 |
| Sized record's pointer slot | 0x148798b8 | 423496 | 8-byte pointer slot mapped |
| Sized interpretation's target | 0x14878590 | 418592 | A mapped LOAD4 candidate; contents not yet observed by this adapter |

The plain-layout failure stays visible as unknown with no target. The sized layout is bound only as observed_candidate_slot_pin with identity_kind=observed_slot_only and native_layout_selected=false. The fact that its target maps does not select a native layout. dwSize=8 is not used as a target-array length; the public generator can emit sizeof(void*) for external objects.

The core independently verifies the real ELF geometry and that the pinned pointer-slot bytes equal the target, so JSON mapping claims cannot substitute for file-backed, non-executable, unambiguous LOAD geometry.

## Authorized target scope proposed for review

The proposed first observation uses record_cap=16 and leaves both core layout hypotheses enabled:

- enum32_stride36: the ordinary AAPCS64 enum candidate.
- enum16_stride32: an explicit counterhypothesis.

The cap is an analysis bound, not a native partition count. The maximum forward target span is 576 bytes; the second interpretation reaches at most 512 bytes. The actual source iteration can stop sooner. The producer's short-circuit invalid-host handling, size-zero skip, IGNORE reservation behavior and conditional TOC contribution calculations are retained from the reviewed core.

Only source-defined integer fields are interpreted. No strings, instruction bytes, target payload hex/base64, certificates or whole firmware data are exported. No runtime SMEM region or kernel interface is read.

Reaching the cap without a terminator stays bounded_without_terminator with the original rejection code. A rejected or non-terminating alternative is not discarded to select the other. A single terminating layout is still not selected. No output is converted into a SMEM header, TOC, boot handoff or default machine profile.

Independent native accessor/cursor analysis is a separate evidence path. Even a plausible terminated data table cannot replace a proof of the native slot calculation, enum width, record stride or branch selection.

## Bounded output

The CLI emits at most 12 KiB of JSON. It retains the input identities, observed slot and alternate unknown, conditional statuses, terminators, per-layout read extents, contributions and false selection/runtime assertions.

When detailed entries or anomalies do not fit, their observed totals and exact omission counts are added before details are removed. Aggregate contributions still refer to all bounded observations, including omitted details. No source report is mutated by serialization. If metadata cannot fit even without those details, output is a fixed rejection.

An analysis_completed=true result means this conditional, bounded analysis completed. It does not mean native ABI selection, SMEM initialization, successful Android boot or complete TB371FC emulation.

## Fourteen authored adapter tests

These tests construct an independent miniature ELF and a simulated prior DAL receipt in memory. They contain no copied firmware bytes, target table or device data. The preexisting 28-test core is not rewritten or represented as new coverage.

1. The bound slot produces both enum candidates, source reservation/IGNORE behavior, no native selection and no global NATIVE_PIN assignment; dwSize=8 does not truncate the candidate array.
2. Wrong image size/hash rejects before target decoding.
3. Receipt byte identity is exact, including whitespace.
4. Duplicate JSON keys and nonfinite values reject even after a synthetic contract is rehashed.
5. Runtime, platform or native-layout claims in the receipt reject.
6. Counts, truncation, duplicate candidates and boolean indices cannot masquerade as valid integer facts.
7. Index/base/stride/slot/target metadata cannot be reinterpreted to another geometry.
8. The unknown plain layout cannot borrow the sized target.
9. Mapped-layout count and static scalar identities are bound to the reviewed contract.
10. Rehashed synthetic inputs with changed pointer-slot bytes still fail the independent core pointer check.
11. Actual executable or unbacked ELF mapping overrides a contradictory simulated receipt.
12. A terminator beyond record 16 does not extend the observation; output counts, redaction and omissions remain correct and no ABI is selected.
13. A modified core source or non-None global native pin is rejected; original source bytes are not changed.
14. Missing/extra CLI arguments do not open private inputs.

## Planned CT execution after review

Install the unchanged core, this adapter and its new tests in a fresh private stage. Preserve all previous DAL, extraction and partition-core stages. Compile only in CT. Run the 14 adapter tests there with isolated Python, then allow the fixed conditional CLI only if the actual unittest result is an exact full success.

The job should use separate process groups with the same 60-second limits and retained logs as prior reviewed static jobs. Record all source and both input identities before and after. Do not execute any firmware, modify any model/worktree or write a device partition. Preserve failures and existing stages rather than retrying over them.
