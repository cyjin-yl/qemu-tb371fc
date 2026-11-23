# XBL DAL v3: explicit compact property candidate

## Status and preserved evidence

This version is an independently written static-format reader. The v1 run in CT102 actually passed 34 synthetic tests and found zero accepted DAL candidates in the pinned XBL backup. The v2 run actually passed 40 tests and also found zero accepted candidates. Its added numerical failure context preserved the v1 observation exactly after those diagnostic fields were removed. Those sources, stages and original receipts remain unchanged.

This v3 source and its 62 authored test methods have not yet been compiled or executed. Forty original legacy/context tests are retained as the original text, and 22 new tests exercise compact-format behavior. Root must review this version before its independent CT compile/test/scan stage. An authored test count is not a test result.

## Why another explicit format is being considered

The v2 rejection occurred at the bounded local list's first item. Its raw word was 0x028000e6; the following word was 0x000c0000. Interpreting the former only as a compact-format hypothesis gives type 2, the bit-23 name marker and offset 0xe6. Adding that offset to the already bounded name section maps exactly to the previously observed complete allowlisted string smem_vers. This arithmetic motivated a separately named format candidate; it did not establish the next word as a selected native version.

The CLI takes either no arguments, for the preserved legacy interpretation, or exactly --compact-property-candidate. It never retries a rejected candidate under another format, accepts an arbitrary input path or selects a format by plausible scalar values.

## Fixed source evidence

All format clues come from the fixed public source mirror Rivko/android-firmware-qti-sdm670 at commit 20bb8ae36c93fc16bbadda0e0a83f930c0c8a271. They are cross-platform source evidence, not an attestation of this XBL's build flags, platform dispatch or firmware origin. The reader is independently implemented; no vendor code or default SMEM configuration is copied into it.

The v1/v2 source hashes for the legacy boot-library layouts remain embedded. The new compact clues are:

| Path within the fixed source | SHA256 | Relevant rule |
| --- | --- | --- |
| adsp_proc/core/kernel/devcfg/service/src/DALProps.c | a0619ff07b088833e66479bb3c1957ef06210526e71c8b389204c1ac429025cc | GetNameOffsetOrIdVal, lookup, variable-length skip, structure-table access and hardware-version selection |
| adsp_proc/core/kernel/devcfg/scripts/propgen.py | cf96e18be30d2ea75e29b7b530e28d152121f89e809749ef2dbf861c1b7442ac | Normal compact producer records and the separate StructSize option |
| adsp_proc/core/api/kernel/devcfg/devcfgTypes.h | fe29d28a882159ab31aeaafd79762ab9b993183586729529fae493e16d3495c5 | Separate plain-pointer and uint32-size-plus-pointer table types |
| adsp_proc/core/api/dal/DALPropDef.h | 88a2b8dc2fe8d1b82c00b00ab9e6d6fc507da7e50fa98c9595239429e99aa622 | Type identifiers and full raw end marker |

The independent source review is Git blob bdf2e2f5a1225d845beb4085084b8431648845f1 in cyjin-yl/qemu-tb371fc, SHA256 d76f11e2cf627706afd5599d3fbd3c82cde0b983cfc1710d2e3ed7c9fc5c46d0. It also records the matching normal TZ-generator branch, differing command-line switches, source paths and the unresolved version prologue. That source review did not run this parser or the private firmware.

## Input, bounds and privacy

The CLI opens only /work/device-assets/xbl-capture-20261123-01/xbl_a.bin. It rejects a symlink, non-regular file, permissions other than 0600, size other than 3,670,016 bytes or SHA256 other than eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55. File identity, size and timestamps must remain stable across the read.

A whole-file read is necessary for the hash. Semantic scanning still reads only non-executable, file-backed LOAD indices 4, 5, 11 and 14. It retains the known descriptor VA 0x1487a1f0 / file offset 425856 and the exact /core/mproc/smem name pointer at VA 0x14874f29. Descriptor-array, name/hash, collision lookup, directory, list-boundary and count validations are unchanged. The parser does not read blocks, execute firmware, disassemble, open firmware volumes, decode partition-table contents or write files.

The only setting names emitted are smem_vers, smem_toc_vers, smem_partitions and smem_max_items. Other names are used internally only for bounded name/hash or list traversal. DevCfgVersionInfo is recognized solely as a rejection control. Output never contains other names, instruction bytes, structure contents, certificates or firmware payload. Only numeric structure/pointer metadata and hashes are added.

JSON remains bounded to 12 KiB. At most four candidate records and four failure-context records are initially retained, and output-size truncation may reduce those details further. Exact total/omission counts are preserved. A zero count, truncated details or an unresolved layout is never replaced by a default.

## Compact normal-record contract

The compact reader is chosen explicitly. Its ordinary record begins with one little-endian u32: bits 31:24 are the type, bit 23 is the name marker and the remaining 23 bits are the name offset when that marker is set. The cited reader returns the low 24 bits when the marker is clear, and its named lookup still uses that value against the name section. V3 preserves and reports the marker but does not invent a guard absent from that reader.

The complete raw word 0xff00ff00 is checked as the terminator before type decoding. Scalar, string-offset, sequence-offset and structure-index payloads are one u32 following the 4-byte header. Inline BYTE_SEQ type 8 instead uses its source-defined length-prefix and 4-byte alignment; it is not assumed to be an eight-byte record. Length-minus-one values 254 and 255 remain rejected at the independently reviewed uint8 skip boundary. Unknown types reject the candidate.

Unlike the legacy boot reader, the compact reader does not interpret raw bit 31 as legacy visibility denial. For example, compact high-byte type 0x82 is unsupported, not a denied type-2 scalar. Legacy visibility behavior is preserved only in explicit legacy mode.

A scalar is reported as a static_uint32_candidate only after its bounded list parses. The source loader's max_items u16 conversion is a separate derived value. Unexpected types stay marked unexpected; they are never coerced to the four expected setting types.

## Structure-table alternatives are independent

Property compaction and DEVCFG__STRUCT_SIZE are separate source options. A compact struct property still supplies a u32 table index, not a target address. The existing index limit and non-null table-base guard remain.

V3 observes both ordinary AAPCS64/LP64 layout hypotheses independently:

| Hypothesis | Entry stride | Size field | Pointer slot |
| --- | --- | --- | --- |
| Plain const void * array | 8 | None | base + 8 * index |
| uint32 dwSize; const void *pStruct | 16 | LE32 at base + 16 * index | base + 16 * index + 8 |

Only the indexed record or pointer slot is read; the structure target is not dereferenced. A mapped target is reported only if an unambiguous known LOAD mapping exists. Unmapped alternate values are not printed as target addresses. Each layout records its own fixed failure code or observed slot/target mapping. Padding in the sized form is not required to be zero.

The sized generator can emit sizeof(void*) for an externally referenced object. Consequently dwSize, even when equal to 8, is not an array length or proof of an entire partition table. A declared target extent may be checked arithmetically against known LOAD geometry, but its contents are never read and the layout remains unselected.

mapped_layout_count is an observation count, not a vote to select a layout. Even if only one branch maps, native_build_flag_proven and struct_layout_selected remain false. If neither maps, the bounded DAL item can still report its validated index together with two explicit mapping failures, but it exposes no resolved structure target. accepted_candidate_count therefore counts bounded DAL-format candidates; it is not a count of resolved partition tables or working native configurations. The legacy pointer-resolution rejection behavior remains unchanged.

## Hardware-version handling is deliberately unresolved

The source reader may inspect a leading type-0x14 property named DevCfgVersionInfo and select a version-specific list from gtcsrSOCHWVersion.hw_version. Neither that native field nor its selected branch has been established here.

The reviewed ADSP and TZ generators also contain an inconsistent special prologue: the compact-enabled branch emits an old three-word record while offsets are still adjusted by a fixed 12. V3 does not repair this inconsistency. In compact mode, a first raw word 0x14 is rejected as unresolved_devcfg_version_prologue, and a decoded DevCfgVersionInfo control name anywhere in the bounded list is rejected as unresolved_devcfg_version_selection. Recognizing one later in the list is a conservative scope restriction rather than an assertion that the source reader would select it there. No version sequence payload is followed.

## Fixture design and execution contract

All fixtures construct a small self-authored ELF in memory. Their values 0x31415926, 0x27182818 and 0x12345 are neither native settings nor copied defaults. The target strings are obvious fixture markers, not a real partition table.

The new tests check packed scalar values and independent record addresses; explicit cross-format rejection; complete raw termination; the cited marker-clear lookup; unsupported high-byte types; both, single and absent structure mappings; nonzero padding and misleading size fields; bounded index/base/slot behavior; both version prologues and a later control marker; global fallback ordering; variable inline-sequence alignment and rejected wrap values; crossing the next list boundary; numerical failure context; target-content redaction and honest truncation; and an unknown mode with no fallback.

After review, use a new private CT102 directory. Preserve v1/v2 and all extraction stages. Compile the exact new sources inside CT, then run all 62 authored test methods with python3 -I -B -S. Only a real zero exit and exact unittest success permit the pinned CLI with --compact-property-candidate. The fixture process and scan process each have a 60-second limit, a dedicated process group and retained stdout/stderr/exit. Record source and XBL identity before and after. No fixture may silently skip on a missing prerequisite.

A successful static scan does not prove native platform selection, reachability from XBL entry, a chosen structure layout, initialized SMEM, or complete TB371FC/Android emulation. Those claims remain false regardless of scalar plausibility or the number of candidates. Any subsequent accessor disassembly or partition decoding needs its own reviewed bounds after these observations exist.
