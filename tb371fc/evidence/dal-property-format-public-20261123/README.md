# DAL property format: independent fixed-source contract

This is a read-only source review, not a parser test or native boot result. The sources are the third-party Rivko/android-firmware-qti-sdm670 mirror at **20bb8ae36c93fc16bbadda0e0a83f930c0c8a271**. They are not verified TB371FC OEM source. No device, generator, reader, test or parser was executed for this review. The v3 parser implementation was not used to infer source rules.

Exact paths, Git blobs, source sizes and SHA256 hashes are in [sources-and-contract.json](sources-and-contract.json).

## Paired generator and reader

The same-component pair is:

- adsp_proc/core/kernel/devcfg/scripts/propgen.py
- adsp_proc/core/kernel/devcfg/service/src/DALProps.c

The regular branch of trustzone_images/core/kernel/devcfg/scripts/propgen.py independently implements the same compact representation. The old boot_images/QcomPkg/Tools/propgen.py and boot_images/QcomPkg/Library/DALCommonLib/DALProps.c implement the older three-word scalar representation.

| Regular scalar representation | Word 0 | Word 1 | Word 2 |
|---|---|---|---|
| Legacy boot | type | name offset or numeric ID | payload |
| DevCfg memory-optimized | packed type and name/ID | payload | absent |

For compact regular properties, the high eight bits are the type. The low 24 bits contain the name/ID component; bit 23 marks a string-name offset and is removed to obtain the 23-bit name offset. A scalar uint32 has type 2. A structure property has type 0x12 and carries a uint32 table index, not an inline native pointer.

The raw end marker is 0xff00ff00. It must be checked before interpreting its high byte as a normal property type. The reader advances over the packed header once; the legacy reader separately consumes type and name words.

These fixed scalar rules do not make every record eight bytes: type 0x08 has an inline, aligned byte-sequence payload and needs independently bounded length handling. Other supported scalar/offset/index payloads consume one uint32.

### Precise source locations

- ADSP generator: 505–506 name marker; 633–653 packing and regular item emission; 428–431 regular 8/12-byte choice.
- ADSP reader: 218–234 name/ID extraction; 524–564 type/end/header handling; 328–351 payload skipping.
- Legacy boot generator: 345–361 regular size and bit-31 name marker.
- Legacy boot reader: 120–130 name-word decoding; 335–356 separate type/name handling.

The first decoded word supplied by the existing v2 receipt, 0x028000e6, is consistent with the compact regular rule: type 2, name marker set, name offset 0xe6. Adding the already-reported name-section base resolves to the already-allowlisted smem_vers string location. The next word could therefore be a uint32 payload under that format. This does not prove a complete accepted table, the selected native configuration, an initialized SMEM version, or a partitions pointer.

## The two independent feature switches

Property compaction is controlled by DEVCFG__PROPS_MEMORY_OPTIMISE. The structure pointer table is independently controlled by DEVCFG__STRUCT_SIZE.

In the TZ build path, devcfg_builder.py reads props_memory_optimisation and size_of_struct from image configuration; dalconfig_builder.py passes them to propgen.py; SConscript supplies the matching C preprocessor definitions. The SDM670 TZ image configuration sets both, but this is not evidence of either setting in the tablet's XBL.

The generator CLI forms also differ by component. The TZ script accepts explicit true/false option values. The ADSP script uses boolean store_true options (-p for compaction and -s for structure sizes). These forms must not be interchanged.

## Structure pointer table contract

devcfgTypes.h defines a size-bearing entry as a uint32 dwSize followed by a native const void pointer. With ordinary, unpacked AAPCS64 LP64 layout:

| Table mode | Entry stride | Pointer location |
|---|---:|---:|
| Plain pointer array | 8 bytes | +0 |
| Size and pointer array | 16 bytes | +8; size at +0 |

The reader's _PopulateStructPtrProperty selects the corresponding table type using DEVCFG__STRUCT_SIZE. A compact property header alone cannot determine this table layout.

The generator can emit sizeof(void*) for externally referenced objects, while locally generated typed objects can use sizeof(object). A size value of eight is therefore not proof of the complete pointee array length.

The pending partition decoder's eight-byte pointer-slot read is compatible with either a plain slot or the pointer field inside a size-bearing entry. The DAL evidence must first establish the correct slot address; the decoder must not derive it by assuming index times eight.

## Version-dependent properties: unresolved source inconsistency

The reader recognizes a leading type-0x14 DevCfgVersionInfo property. Its sequence entries encode a hardware-version ID in the upper 16 bits and a device-relative property offset in the lower 16 bits. It compares the ID with gtcsrSOCHWVersion.hw_version. On a match it searches the version-specific list, then common properties, then global properties once.

However, both stored generators contain a special-prologue inconsistency:

- TZ propgen.py 627–630 emits a three-word prologue when memory_optimised_props is true and a packed prologue otherwise; its version offset calculation at 596 adds 12.
- ADSP propgen.py 710–713 contains the same reversed forms; its version offset calculation at 679 also adds 12.

These special branches disagree with the generators' ordinary property rules and the reader's compact-header path. This review does not repair them or claim knowledge of the intended build variant. **A new parser must reject or mark version-related candidates undetermined until an independently supported representation is established.** Regular scalar matching does not resolve this discrepancy.

## Audit requirements for the separate v3 implementation

1. Preserve explicit legacy and compact candidates. Do not relax legacy name bounds to accommodate the observed failure.
2. Bound the complete walk, names and payload/index accesses; recognize the raw terminator first.
3. Keep structure-table layout separate from the compact-header hypothesis.
4. Keep native platform/version selection and entry reachability unproven.
5. Do not infer initialized SMEM, complete partition-array length or a handoff from a structurally plausible table.
6. Report unresolved version-related layouts instead of silently correcting the stored generator.

The independent source analysis supports a new bounded compact-format candidate. It does not constitute acceptance of that candidate or verification of the v3 implementation.
