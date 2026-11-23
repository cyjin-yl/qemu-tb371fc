# Conditional SMEM partition observations from the current XBL backup

This record preserves a completed bounded analysis in doesvm CT102 on 2026-11-23. Fourteen self-authored synthetic adapter tests passed. A subsequent scan of the fixed current active-slot XBL backup produced two conditional configuration-table interpretations. No native ABI, selected platform configuration, initialized SMEM, or successful Android boot was established.

## Actual execution

The job finished at 15:27:42 UTC in `/work/smem-observed-slot-20261123-01`. The fixture and scan processes both exited 0, were reaped, and did not time out. All seven source files and both input files had matching before/after hashes. The original scan is 9,152 bytes, below the 12,288-byte output limit.

The source package, original outputs, installation receipt, and transport source/receipt are preserved by byte identity in [manifest.json](manifest.json). The command required the explicit `--conditional-observed-slot` mode. The partition core remains byte-for-byte SHA256 `17ce02aae0f4c24d68824d69e3421408e1bf58ab03ad08b28c3059c4ca4caf18`, with `NATIVE_PIN = None`.

The input is a current partition backup, not an independently authenticated pristine factory image. The preceding DAL compact-format observation is pinned to published commit `1ec5285e71af7db3b8766ea8523e9b219c10cc96` and exact scan SHA256 `839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053`.

## Observed slot and bounded interpretations

DAL property index 58 has two separately retained structure-table layout hypotheses. The plain-pointer slot's target mapping remains unvalidated. The size-plus-pointer AAPCS64 hypothesis identifies an eight-byte pointer slot at VA `0x148798b8`, file offset 423496, in non-executable LOAD 4, pointing to VA `0x14878590`, file offset 418592. Its declared size 8 is not evidence of the destination array's total length.

The adapter bound this observed slot to the image and DAL receipt hashes. It did not promote that binding to a native pointer pin or select a structure-table layout. It then applied the unchanged partition core to two explicit enum-width hypotheses with a maximum of 16 records each. The maximum permitted forward range was 576 bytes; the observed upper read bounds were target + 300 and target + 298 bytes.

Under the ordinary AAPCS64 enum32 / 36-byte hypothesis, the eight records below were observed. Every listed record has flags 3, cacheline size 32, and four zero exclusion sizes. Host numbers are retained without inventing runtime ownership.

| Record | Host 0 | Host 1 | Size in bytes |
|---:|---:|---:|---:|
| 0 | 0 | 2 | 196608 |
| 1 | 0 | 5 | 131072 |
| 2 | 0 | 3 | 196608 |
| 3 | 2 | 3 | 49152 |
| 4 | 0 | 8 | 32768 |
| 5 | 7 | 11 | 16384 |
| 6 | 0 | 10 | 65536 |
| 7 | 10 | 5 | 65536 |

The source-contract interpretation gives a conditional reservation contribution of 753664 bytes and eight TOC entries. It found no recorded anomalies in this hypothesis and stopped at record 8 because host0 was 65535.

The enum16 / 32-byte counterhypothesis also terminated, at record 9, with four recorded anomalies and a different conditional contribution. Both interpretations read their terminating host0 at the same underlying address. Reaching a terminator therefore does not independently select an ABI. The actual output explicitly records `multiple_terminated_candidates` and `selected_layout: null`; all entries and anomalies fit in the public output, with no omitted details.

## Evidence limits

The table values are conditional static observations. The analysis did not execute XBL, prove call reachability from the firmware entry, establish the selected native DAL build flags, or observe SBL writing shared memory. It did not construct a SMEM header, TOC, partition directory, socinfo item, or handoff image. The scalar candidates 12 << 16, 1, and 625 remain tied to the earlier unselected DAL configuration candidate.

The next discriminating evidence is the actual accessor and source-table iteration code: which structure-table slot it loads, how it advances the configuration cursor, and how it reads host fields. Data plausibility alone does not provide those facts.

## Reproduction and privacy

The exact approved seven-file package and deployment wrappers are under `method/`. The fixed Git blob transport verified response metadata, payload size, SHA256, and Git object identity before passing the package as data to the reviewed installer. Installation used new exclusive paths and retained all files. The receipt collector opened only the named result files read-only; its source also contains the separately authorized boot-header test receipt paths.

Only original safe metadata, synthetic tests, and our source are included. The XBL payload, tar archive, extracted firmware, private instruction bytes, and unrelated device information are not included. Original receipt text is retained without edits. The boot-header test's five original receipts were delivered separately to the root agent and are not folded into this SMEM result.
