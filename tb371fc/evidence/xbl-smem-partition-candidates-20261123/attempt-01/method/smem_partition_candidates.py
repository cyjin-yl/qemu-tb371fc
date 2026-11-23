#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DRAFT: bounded SMEM configuration candidates; never creates a handoff.

Original analysis implementation based on fixed public source contracts.
No native DAL pointer is accepted until its receipt and adapter are reviewed.
The pure core accepts immutable synthetic ELF bytes and a synthetic pointer pin.
Do not infer platform selection, entry reachability or initialization from output.
"""

from dataclasses import dataclass
import argparse
import hashlib
import json
import re
import struct
import sys
from typing import Optional

SCHEMA = "tb371fc.smem-partition-candidates.draft-v1"
U32_MAX = (1 << 32) - 1
U64_LIMIT = 1 << 64
MAX_IMAGE_BYTES = 128 * 1024 * 1024
MAX_PROGRAM_HEADERS = 128
MAX_RECORD_CAP = 128
INVALID_HOST = 0xFFFF
IGNORE_PARTITION = 0x4
KNOWN_FLAGS = 0x7
PF_X = 0x1

SOURCE_CONTRACTS = {
    "mirror_commit": "20bb8ae36c93fc16bbadda0e0a83f930c0c8a271",
    "smem_boot_sha256":
        "dbccac1b77e33972517fc49486f63b63cee64b5bcba91e1cd04b98e9c93c50b0",
    "smem_toc_sha256":
        "16559f7f4731449378aef09c4bc75b4af53365b96e6e81b701fd0337db9440a7",
    "smem_type_sha256":
        "be5b89dfa479cf23610db2dc46dcc7a72c47773deef31c3dbeb321de518672af",
    "aapcs64_commit": "c51addc3dc03e73a016a1e4edf25440bcac76431",
    "aapcs64_sha256":
        "5862a326f202ed68e21e27b35716481ff610825484d2a088191759b76fddcc03",
}

@dataclass(frozen=True)
class PointerPin:
    image_sha256: str
    dal_report_sha256: str
    property_index: int
    pointer_slot_va: int
    pointer_slot_file_offset: int
    pointer_slot_load_index: int
    target_va: int
    target_file_offset: int
    target_load_index: int


# No actual accepted DAL smem_partitions target exists at drafting time.
# Setting this alone must not enable CLI use: a receipt adapter is also pending.
NATIVE_PIN: Optional[PointerPin] = None


@dataclass(frozen=True)
class Layout:
    name: str
    enum_bytes: int
    stride: int
    host0_offset: int
    host1_offset: int
    cacheline_offset: int
    exclusions_offset: int
    abi_assumption: str


LAYOUTS = (
    Layout("enum32_stride36", 4, 36, 8, 12, 16, 20,
           "ordinary_aapcs64_word_enum_conditional"),
    Layout("enum16_stride32", 2, 32, 8, 10, 12, 16,
           "counterhypothesis_not_ordinary_aapcs64"),
)


@dataclass(frozen=True)
class Load:
    index: int
    file_offset: int
    va: int
    pa: int
    filesz: int
    memsz: int
    flags: int


class Rejected(Exception):
    """Only fixed codes and allowlisted integers can enter a report."""

    def __init__(self, code, *, address=None, index=None):
        super().__init__(code)
        self.code = code
        self.address = address
        self.index = index

    def report(self):
        result = {"code": self.code}
        if self.address is not None:
            result["address"] = self.address
        if self.index is not None:
            result["index"] = self.index
        return result


def _integer(value, limit, code):
    if type(value) is not int or value < 0 or value >= limit:
        raise Rejected(code)
    return value


def _range(start, length, limit, code):
    if (type(start) is not int or type(length) is not int or
            start < 0 or length < 0 or start > limit or length > limit - start):
        raise Rejected(code)


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _load_metadata(load):
    return {
        "program_header_index": load.index,
        "va": load.va, "pa": load.pa, "file_offset": load.file_offset,
        "filesz": load.filesz, "memsz": load.memsz, "flags": load.flags,
    }


class ElfImage:
    def __init__(self, image):
        if type(image) is not bytes:
            raise Rejected("immutable_bytes_required")
        if len(image) < 64 or len(image) > MAX_IMAGE_BYTES:
            raise Rejected("image_length_outside_analysis_bound")
        if (image[:4] != b"\x7fELF" or image[4] != 2 or
                image[5] != 1 or image[6] != 1):
            raise Rejected("elf64_little_endian_version1_required")
        elf_type, machine, version = struct.unpack_from("<HHI", image, 16)
        if elf_type not in (2, 3) or machine != 183 or version != 1:
            raise Rejected("aarch64_executable_elf_required")
        phoff = struct.unpack_from("<Q", image, 32)[0]
        ehsize, phentsize, phnum = struct.unpack_from("<HHH", image, 52)
        if ehsize != 64 or phentsize != 56:
            raise Rejected("unsupported_elf_header_geometry")
        if not 1 <= phnum <= MAX_PROGRAM_HEADERS:
            raise Rejected("program_header_count_outside_analysis_bound")
        _range(phoff, phnum * phentsize, len(image),
               "program_headers_outside_file")
        loads = []
        for index in range(phnum):
            fields = struct.unpack_from("<IIQQQQQQ", image, phoff + index * 56)
            kind, flags, offset, va, pa, filesz, memsz, alignment = fields
            if kind != 1:
                continue
            if filesz > memsz:
                raise Rejected("load_filesz_exceeds_memsz", index=index)
            _range(offset, filesz, len(image), "load_outside_file")
            _range(va, memsz, U64_LIMIT, "load_va_overflow")
            _range(pa, memsz, U64_LIMIT, "load_pa_overflow")
            loads.append(Load(index, offset, va, pa, filesz, memsz, flags))
        if not loads:
            raise Rejected("no_load_segments")
        self.image = image
        self.loads = tuple(loads)

    def span(self, address, length, expected_load_index=None):
        _integer(address, U64_LIMIT, "invalid_read_address")
        if type(length) is not int or length <= 0:
            raise Rejected("invalid_read_length")
        _range(address, length, U64_LIMIT, "read_address_overflow")
        end = address + length
        overlapping = [
            load for load in self.loads
            if address < load.va + load.memsz and load.va < end
        ]
        if len(overlapping) > 1:
            raise Rejected("ambiguous_load_mapping", address=address)
        if not overlapping:
            raise Rejected("address_outside_load", address=address)
        load = overlapping[0]
        if address < load.va or end > load.va + load.memsz:
            raise Rejected("read_crosses_load_boundary", address=address)
        if expected_load_index is not None and load.index != expected_load_index:
            raise Rejected("read_left_pinned_load", address=address)
        if load.flags & PF_X:
            raise Rejected("executable_load_rejected", address=address)
        relative = address - load.va
        if relative > load.filesz or length > load.filesz - relative:
            raise Rejected("read_enters_unbacked_load_tail", address=address)
        offset = load.file_offset + relative
        _range(offset, length, len(self.image), "read_outside_file")
        return load, offset

    def read(self, address, length, expected_load_index=None):
        load, offset = self.span(address, length, expected_load_index)
        return self.image[offset:offset + length]


def _validate_pin(memory, pin):
    if type(pin) is not PointerPin:
        raise Rejected("pointer_pin_required")
    if not _digest(pin.image_sha256) or not _digest(pin.dal_report_sha256):
        raise Rejected("invalid_pin_hash")
    for name in ("property_index", "pointer_slot_load_index", "target_load_index"):
        _integer(getattr(pin, name), 1 << 32, "invalid_pin_index")
    for name in ("pointer_slot_va", "pointer_slot_file_offset",
                 "target_va", "target_file_offset"):
        _integer(getattr(pin, name), U64_LIMIT, "invalid_pin_address")
    if hashlib.sha256(memory.image).hexdigest() != pin.image_sha256:
        raise Rejected("image_sha256_mismatch")
    if pin.pointer_slot_va & 7:
        raise Rejected("pointer_slot_not_8byte_aligned")
    if pin.target_va & 3:
        raise Rejected("configuration_pointer_not_4byte_aligned")
    slot_load, slot_offset = memory.span(
        pin.pointer_slot_va, 8, pin.pointer_slot_load_index)
    if slot_offset != pin.pointer_slot_file_offset:
        raise Rejected("pointer_slot_file_offset_mismatch")
    target = struct.unpack("<Q", memory.read(
        pin.pointer_slot_va, 8, pin.pointer_slot_load_index))[0]
    if target != pin.target_va:
        raise Rejected("pointer_slot_value_mismatch")
    target_load, target_offset = memory.span(
        pin.target_va, 1, pin.target_load_index)
    if target_offset != pin.target_file_offset:
        raise Rejected("target_file_offset_mismatch")
    return {
        "property_index": pin.property_index,
        "pointer_slot_va": pin.pointer_slot_va,
        "pointer_slot_file_offset": slot_offset,
        "pointer_slot_width": 8,
        "pointer_slot_load": _load_metadata(slot_load),
        "target_va": target,
        "target_file_offset": target_offset,
        "target_load": _load_metadata(target_load),
        "array_total_length_known": False,
    }


def _decode_layout(memory, pin, layout, record_cap):
    result = {
        "layout": layout.name, "stride": layout.stride,
        "enum_bytes": layout.enum_bytes, "abi_assumption": layout.abi_assumption,
        "status": "bounded_without_terminator",
        "source_iteration_terminated": False,
        "array_total_length_known": False,
        "entries": [], "terminator": None, "anomalies": [],
        "reserved_bytes_contribution": 0, "toc_entries_contribution": 0,
        "field_bytes_read": 0, "highest_read_end_va": pin.target_va,
        "selection_proven": False, "abi_confirmed": False,
    }

    def read_uint(address, width):
        raw = memory.read(address, width, pin.target_load_index)
        result["field_bytes_read"] += width
        result["highest_read_end_va"] = max(
            result["highest_read_end_va"], address + width)
        return int.from_bytes(raw, "little")

    def anomaly(index, code):
        result["anomalies"].append({"record_index": index, "code": code})

    try:
        for index in range(record_cap):
            address = pin.target_va + index * layout.stride
            _integer(address, U64_LIMIT,
                     "configuration_record_address_overflow")

            # Preserve the source condition's logical short circuit.
            host0 = read_uint(address + layout.host0_offset, layout.enum_bytes)
            if host0 == INVALID_HOST:
                result["terminator"] = {
                    "record_index": index, "record_va": address,
                    "host0": host0, "host1": None, "stop_field": "host0",
                }
                result["source_iteration_terminated"] = True
                result["status"] = "terminated"
                break
            host1 = read_uint(address + layout.host1_offset, layout.enum_bytes)
            if host1 == INVALID_HOST:
                result["terminator"] = {
                    "record_index": index, "record_va": address,
                    "host0": host0, "host1": host1, "stop_field": "host1",
                }
                result["source_iteration_terminated"] = True
                result["status"] = "terminated"
                break

            size = read_uint(address, 4)
            entry = {
                "record_index": index, "record_va": address,
                "host0": host0, "host1": host1, "size": size,
                "flags": None, "size_cacheline": None, "exclusion_sizes": None,
                "disposition": "size_zero_skipped",
                "reserved_bytes_contribution": 0, "toc_entries_contribution": 0,
            }
            result["entries"].append(entry)
            if host0 > 0xFFFF or host1 > 0xFFFF:
                anomaly(index, "host_value_would_narrow_in_toc_u16")
            if size == 0:
                continue

            flags = read_uint(address + 4, 4)
            entry["flags"] = flags
            entry["reserved_bytes_contribution"] = size
            result["reserved_bytes_contribution"] += size
            if flags & ~KNOWN_FLAGS:
                anomaly(index, "flags_outside_fixed_source_named_mask")
            if result["reserved_bytes_contribution"] > U32_MAX:
                anomaly(index, "reservation_contribution_exceeds_uint32")
            if flags & IGNORE_PARTITION:
                entry["disposition"] = "ignored_space_reserved"
                continue

            entry["size_cacheline"] = read_uint(
                address + layout.cacheline_offset, 4)
            entry["exclusion_sizes"] = [
                read_uint(address + layout.exclusions_offset + 4 * n, 4)
                for n in range(4)
            ]
            if sum(entry["exclusion_sizes"]) > size:
                anomaly(index, "exclusions_exceed_partition_size")
            entry["disposition"] = "conditional_toc_entry"
            entry["toc_entries_contribution"] = 1
            result["toc_entries_contribution"] += 1
        else:
            result["rejection"] = {"code": "record_cap_without_terminator"}
    except Rejected as error:
        result["status"] = "rejected"
        result["rejection"] = error.report()
    return result


def _base_report():
    return {
        "schema": SCHEMA,
        "status": "refused",
        "native_pointer_pin_available": NATIVE_PIN is not None,
        "native_dal_adapter_reviewed": False,
        "platform_static_selection_proven": False,
        "entry_reachable": False,
        "smem_initialized": False,
        "handoff_generated": False,
        "abi_confirmed": False,
        "selected_layout": None,
        "array_total_length_known": False,
        "source_contracts": SOURCE_CONTRACTS.copy(),
    }


def analyze_bytes(image, pin, *, record_cap=64, include_enum16_control=True):
    """Analyze immutable synthetic ELF bytes; no I/O and no native selection.

    record_cap is an analysis safety bound, not a target partition count.
    The caller-supplied pin is a synthetic fixture contract in this draft.
    Receipt-byte verification and native JSON schema adaptation are not enabled.
    """
    result = _base_report()
    try:
        if type(record_cap) is not int or not 1 <= record_cap <= MAX_RECORD_CAP:
            raise Rejected("record_cap_outside_analysis_bound")
        if type(include_enum16_control) is not bool:
            raise Rejected("invalid_layout_control_option")
        memory = ElfImage(image)
        result["pointer"] = _validate_pin(memory, pin)
        result["image_sha256"] = pin.image_sha256
        result["dal_report_sha256"] = pin.dal_report_sha256
        layouts = LAYOUTS if include_enum16_control else LAYOUTS[:1]
        result["candidates"] = [
            _decode_layout(memory, pin, layout, record_cap)
            for layout in layouts
        ]
        count = sum(c["source_iteration_terminated"]
                    for c in result["candidates"])
        result["terminated_candidate_count"] = count
        result["status"] = "candidate_analysis"
        result["ambiguity"] = (
            "multiple_terminated_candidates" if count > 1 else
            "one_terminated_candidate_not_selected" if count == 1 else
            "no_terminated_candidate")
    except Rejected as error:
        result["status"] = "rejected"
        result["rejection"] = error.report()
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", help="Reserved for a reviewed native adapter.")
    parser.add_argument("--dal-report", help="Reserved for a pinned DAL receipt.")
    parser.parse_args(argv)
    result = _base_report()
    result["rejection"] = {
        "code": ("native_pointer_pin_unavailable" if NATIVE_PIN is None
                 else "native_dal_adapter_not_reviewed")
    }
    # No input path is opened or echoed while the evidence pin/adapter is absent.
    json.dump(result, sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
