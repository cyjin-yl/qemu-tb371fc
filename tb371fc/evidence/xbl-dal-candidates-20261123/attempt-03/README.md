# XBL compact DAL candidate: actual CT attempt 03

The fixed private XBL input was parsed inside doesvm CT102 Docker `qemu-tb371fc-dev` on 2026-11-23. All **62 synthetic parser tests passed**. The bounded static scan accepted **one compact-property candidate**. This is a data-format observation, not firmware execution or proof of the runtime-selected SMEM configuration.

## Observed result

| Field | Static observation |
| --- | --- |
| DALProps head | VA `0x14877e10` |
| String-device table | 17 entries, names and hashes checked; target index 11 |
| Property binary | VA `0x14879910`, declared length `0x724` |
| Device name | `/core/mproc/smem` |
| `smem_vers` | `0x000c0000` |
| `smem_toc_vers` | 1 |
| `smem_max_items` | 625 |
| `smem_partitions` | Type `0x12`, structure-array index 58 |
| Structure array base | `0x14879510` |

The complete bounded device property list supplied all four values locally.
No global-device fallback was used. Of 21,481 aligned heads, 184 passed the
initial geometry checks and 183 were rejected by the string-device count
limit. The one remaining candidate was processed without truncation.

## Unselected structure layouts

The scan reports both public-source layout hypotheses independently:

| Hypothesis | Slot geometry | Observation |
| --- | --- | --- |
| Plain AAPCS64 pointer table | 8-byte entries; slot `0x148796e0` | Pointer target did not map uniquely; raw failed pointer is not exported |
| Size plus pointer table | 16-byte entries; record `0x148798b0`, pointer at `0x148798b8` | Pointer maps to `0x14878590`; size field is 8 |

**Neither layout is selected.** A uniquely mapped candidate does not prove
the native build's structure-size flag, selected platform configuration or
call reachability. The source generator can use `sizeof(void *)` for an
external structure; the size field 8 is therefore not a partition-table
length. The target contents were not decoded or read by this scan.

The next evidence needed is the actual accessor's pointer calculation and
the producer's source-record stride and host-field extraction. The separate
partition decoder's synthetic tests do not supply that missing native ABI
evidence.

## Exact receipts and implementation

[manifest.json](manifest.json) records all seven original files, source
identities, the complete execution package identity and earlier results.
The source and method are preserved under [method/](method/).
The [job receipt](job.result.json), [fixture output](results/fixtures.stderr)
and [scan output](results/scan.stdout) are original bytes.

The input was 3,670,016 bytes with SHA256
`eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55`.
The original scan was 7,358 bytes with SHA256
`839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053`.
Both child process groups were reaped, neither phase timed out, and all
source and input hashes remained unchanged. The job completed at
`2026-11-23T14:55:05.940425+00:00`.

The original 40 fixture bodies are unchanged; 22 additional cases cover
the independently reviewed compact format and its rejection boundaries.
The scan explicitly used `--compact-property-candidate`; it did not
silently reinterpret a legacy-parser failure.
The [public format source contract](../../dal-property-format-public-20261123/sources-and-contract.json)
separates the two build flags and documents unresolved version-prologue
inconsistencies. Such prologues remain rejected.

[Attempt 01](../attempt-01/README.md) and
[attempt 02](../attempt-02/README.md) remain unchanged with their actual
zero-candidate legacy-format outcomes. No SMEM handoff, initialized RAM,
partition contents, firmware execution, or Android boot is claimed here.
Private firmware bytes are not included.
