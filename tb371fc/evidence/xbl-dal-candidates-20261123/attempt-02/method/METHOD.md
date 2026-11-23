# XBL DAL format candidate scan -- review draft

This is an independently written, bounded static parser. No Python in this draft has been compiled or executed yet. The v1's 34 self-authored fixtures passed in CT before its actual pinned XBL scan returned zero accepted candidates. This v2 retains those checks and adds six diagnostic checks; its 40 authored test methods have not yet run. Compilation and tests belong in CT102 after review; the actual pinned XBL scan also requires the completed v3 handoff and a separate narrow execution scope.

## Inputs and output

The CLI accepts no paths or arguments. It opens only the existing private XBL backup at `/work/device-assets/xbl-capture-20261123-01/xbl_a.bin`, rejects a symlink/non-regular/non-0600 file, verifies size 3,670,016 and SHA256 `eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55`, and checks stable file identity/size/timestamps around the read. Whole-file reading is required for the hash; semantic scanning is limited to non-executable, file-backed LOAD indices 4, 5, 11 and 14. It does not open block devices, run firmware, disassemble code, parse firmware volumes or write files.

Only the four allowlisted settings (`smem_vers`, `smem_toc_vers`, `smem_partitions`, `smem_max_items`) and bounded structure/pointer/hash metadata are emitted. No other device names, strings, structure contents, certificates, instruction bytes or firmware payload are emitted. JSON is capped at 12 KiB; any omitted candidate details retain exact total and omission counts.

## Validation chain

1. The known descriptor at VA 0x1487a1f0 must map to file offset 425856, refer to the exact /core/mproc/smem string at VA 0x14874f29, and contain its matching 32-bit DJB2 hash.
2. Aligned 32-byte candidate DALProps records are sought only in the four permitted LOADs. Their pDevices/count must describe a fully file-backed array of 40-byte StringDevice records containing the known descriptor at an integral index. Up to 1,024 descriptors are permitted; all names are bounded internally and all stored name hashes are checked.
3. Nonzero descriptor property offsets and the complete numeric directory must be inside the bounded propbin. Collision indices must be bounded. The cited lookup algorithm must select the already-known descriptor; a mere pDevices reference does not suffice.
4. Target property records are bounded by the nearest known subsequent property-list start, the propbin end and a 64 KiB limit. Unknown types, malformed extents, duplicate allowlisted property names and unsupported inline-sequence skip boundaries reject the candidate.
5. A needed numeric device-0 fallback is used only when device 0 is the first numeric entry. The cited implementation otherwise checks one handle but jumps through entry zero; the parser reports this unsupported layout instead of silently correcting it.
6. A struct-pointer payload is a u32 index into an 8-byte pointer-slot array. Only that slot and the target's known LOAD mapping are inspected; array total length and partition-structure layout remain unknown. The target is not dereferenced. StringDevice.pFunctionName is recorded as a driver-info structure pointer, not a direct function entry.
7. Type visibility denial is preserved and does not trigger fallback. The max_items u16 cast, when applicable, is a separate derived static value.

A matching candidate does not prove this format variant is the one executed on the captured device, that the table was selected for this platform, that a call is reachable from XBL entry, or that any SMEM bytes were initialized. These four runtime assertions stay false. Multiple candidates are retained, not selected by plausibility.

## Source basis

The format clues are from the fixed public mirror `Rivko/android-firmware-qti-sdm670@20bb8ae36c93fc16bbadda0e0a83f930c0c8a271`. The implementation records exact hashes of DALSysTypes.h, DALSysCmn.h, DALProps.c, DALSYSLoader.c and DALPropDef.h. The mirror supplies a candidate ABI, not evidence that SM8250 used its default settings. No source implementation or default SMEM configuration was copied into this parser.

The test module constructs a small synthetic ELF and property data in memory. Its scalar values 0x31415926, 0x27182818 and 0x12345 are deliberately self-authored fixtures, not captured or platform defaults. The structure-pointer target is a self-authored marker, not a partition table. Positive and negative cases cover mapping/extent/hash/collision selection, visibility, absent settings, global fallback, index-vs-pointer semantics, ambiguous mapping, rejected types, output redaction and truncation accounting.

## Planned execution after review

Install both files in a new private CT directory as `dal_smem_candidates.py` and `test_dal_smem_candidates.py`, preserving exact source hashes and all prior stages. Compile them there with Python's compile(), then run the test script with `python3 -I -B -S`; record its real exit and output. Only after the fixtures pass should the fixed CLI run against the validated XBL file. Preserve analysis stdout, stderr, exit and source/input hashes under a new result directory. No existing receipt, model, tree or firmware file is modified.

## v2 diagnostic-only changes

The v1 directory and its successful 34-fixture log and zero-candidate scan remain unchanged. Version 2 preserves every acceptance/rejection check. It attaches numerical structure context only when the existing property_name_offset rejection occurs: DALProps head, propbin base/length/name-section offset, local/global list origin, entry address, type/name words, calculated relative name offset, and permitted LOAD scope. It never exports the rejected string or payload contents. At most four details are retained, with total and omitted counts; the existing 12 KiB output ceiling also truncates details while preserving those counts. The six new fixtures check local/global context, preserved rejection/acceptance behavior, omission counts, output bounds and redaction. A future v2 run must use a new directory and preserve the v1 artifacts.
