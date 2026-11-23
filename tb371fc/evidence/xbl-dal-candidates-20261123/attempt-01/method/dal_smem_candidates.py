#!/usr/bin/env python3
"""Bounded static DAL format candidate scan; never executes firmware.

This is an independent format reader, not copied DAL implementation code.
The format clue is a fixed SDM670 source mirror; a match does not establish
SM8250 platform selection, boot reachability, or initialized SMEM contents.
The CLI reads one pinned private XBL file and emits allowlisted JSON only.
"""
import collections
import hashlib
import json
import os
import stat
import struct
import sys

INPUT = "/work/device-assets/xbl-capture-20261123-01/xbl_a.bin"
INPUT_SIZE = 3670016
INPUT_SHA256 = "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55"
SCOPE_PHS = (4, 5, 11, 14)
DESCRIPTOR_VA = 0x1487a1f0
DESCRIPTOR_FILE_OFFSET = 425856
DEVICE_NAME_VA = 0x14874f29
DEVICE_NAME = b"/core/mproc/smem"
NAMES = ("smem_vers", "smem_toc_vers", "smem_partitions", "smem_max_items")
END = 0xff00ff00
FIXED_TYPES = (0x2, 0x11, 0x12, 0x14, 0x18)
MAX_DEVICES = 1024
MAX_NUMERIC = 1024
MAX_PROP_BIN = 1024 * 1024
MAX_LIST_BYTES = 65536
MAX_PROPERTIES = 512
MAX_NAME_BYTES = 256
MAX_STRUCT_INDEX = 4095
MAX_GEOMETRIC_CANDIDATES = 64
MAX_REPORTED_CANDIDATES = 4
MAX_OUTPUT = 12288
FORMAT_SOURCES = {
    "commit": "20bb8ae36c93fc16bbadda0e0a83f930c0c8a271",
    "repository": "Rivko/android-firmware-qti-sdm670",
    "qualification": "SDM670 format clue; not attested native SM8250 code",
    "DALSysTypes.h": "fc587afb1b30a1c42161fac09477fa3f00ac0a41c371d2af14e1a59982bf459b",
    "DALSysCmn.h": "b622a3d51c352f25a301b314aa6e8ced16df2f5286ca3da7f1c4461b33efd682",
    "DALProps.c": "3f32554b2260efb10729f284b3963ce8dc6b7008558ae886a58d496f33804973",
    "DALSYSLoader.c": "9d695c43227eddf9778d260b0e09f3a8c10f0ee00c9342149d6618e4631ef754",
    "DALPropDef.h": "8dc126161edde1add133a9e5cb20a4de3c9a2420e81df48be908213d0f5d41d2",
}

class Reject(Exception):
    """A fixed reason code, never private bytes or arbitrary exception text."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def need(condition, code):
    if not condition:
        raise Reject(code)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def djb2(name):
    # ASCII only avoids making an unsupported signed-char ABI assumption.
    need(name and all(0 < b < 128 for b in name), "non_ascii_or_empty_device_name")
    value = 5381
    for octet in name:
        value = ((value * 33) + octet) & 0xffffffff
    return value

def extent(start, size, end, code):
    need(isinstance(start, int) and isinstance(size, int)
         and 0 <= start <= end and 0 <= size <= end - start, code)

class ELFView:
    def __init__(self, data, scope):
        self.data = data
        need(len(data) >= 64 and data[:7] == b"\x7fELF\x02\x01\x01",
             "not_elf64_little_endian")
        h = struct.unpack_from("<HHIQQQIHHHHHH", data, 16)
        need(h[1] == 183 and h[2] == 1 and h[7] == 64
             and h[8] == 56 and 0 < h[9] <= 64, "unsupported_elf_header")
        phoff, phnum = h[4], h[9]
        extent(phoff, 56 * phnum, len(data), "program_table_out_of_bounds")
        self.loads = []
        for index in range(phnum):
            typ, flags, off, va, pa, fsz, msz, align = struct.unpack_from(
                "<IIQQQQQQ", data, phoff + index * 56)
            if typ != 1:
                continue
            extent(off, fsz, len(data), "load_file_extent")
            extent(va, msz, 1 << 64, "load_memory_extent")
            need(fsz <= msz, "load_filesz_exceeds_memsz")
            self.loads.append({"index": index, "flags": flags,
                               "offset": off, "va": va, "pa": pa,
                               "filesz": fsz, "memsz": msz})
        self.scope = tuple(scope)
        for index in self.scope:
            found = [p for p in self.loads if p["index"] == index]
            need(len(found) == 1 and found[0]["filesz"] > 0
                 and not (found[0]["flags"] & 1), "invalid_scan_scope")

    def mapping(self, va, size, scoped=True, file_backed=True):
        extent(va, size, 1 << 64, "pointer_overflow")
        need(size > 0, "empty_pointer_extent")
        matches = [p for p in self.loads
                   if p["va"] <= va
                   and size <= p["va"] + (p["filesz"] if file_backed else p["memsz"]) - va]
        need(len(matches) == 1, "missing_or_ambiguous_pointer_mapping")
        p = matches[0]
        if scoped:
            need(p["index"] in self.scope and not (p["flags"] & 1),
                 "pointer_outside_nonexecuting_scope")
        return p, p["offset"] + va - p["va"]

    def read(self, va, size):
        _p, off = self.mapping(va, size)
        return self.data[off:off + size]

    def cstring(self, va):
        p, off = self.mapping(va, 1)
        limit = min(MAX_NAME_BYTES, p["va"] + p["filesz"] - va)
        pos = self.data.find(b"\0", off, off + limit)
        need(pos >= off, "unterminated_or_overlong_name")
        value = self.data[off:pos]
        need(value and all(0 < b < 128 for b in value), "non_ascii_or_empty_name")
        return value

    def describe(self, va, size=1, scoped=False, file_backed=False):
        p, off = self.mapping(va, size, scoped, file_backed)
        backed = va + size <= p["va"] + p["filesz"]
        return {"va": va, "size": size, "load_index": p["index"],
                "load_va": p["va"], "load_memsz": p["memsz"],
                "load_filesz": p["filesz"], "flags": p["flags"],
                "file_backed": backed, "file_offset": off if backed else None}

    def scan_heads(self):
        for p in self.loads:
            if p["index"] not in self.scope:
                continue
            first = (p["va"] + 7) & ~7
            stop = p["va"] + p["filesz"] - 32
            for va in range(first, stop + 1, 8):
                yield va

def unpack_descriptor(view, va):
    raw = view.read(va, 40)
    name, hashed, offset, driver_info, collisions, collision_ptr = (
        struct.unpack("<QIIQI4xQ", raw))
    return {"va": va, "name_ptr": name, "hash": hashed, "offset": offset,
            "driver_info": driver_info, "collision_count": collisions,
            "collision_ptr": collision_ptr}

class PropBin:
    def __init__(self, view, va):
        self.view, self.va = view, va
        vals = struct.unpack("<6I", view.read(va, 24))
        self.length, self.name_off, self.string_off, self.byte_off, self.u32_off, numeric = vals
        need(24 <= self.length <= MAX_PROP_BIN, "propbin_length_limit")
        need(numeric <= MAX_NUMERIC, "numeric_device_limit")
        extent(24, numeric * 8, self.length, "numeric_directory_extent")
        self.raw = view.read(va, self.length)
        for off in (self.name_off, self.string_off, self.byte_off, self.u32_off):
            need(0 <= off <= self.length, "section_offset_out_of_bounds")
        self.numeric = [struct.unpack_from("<II", self.raw, 24 + 8 * i)
                        for i in range(numeric)]
        ids = [row[0] for row in self.numeric]
        need(len(ids) == len(set(ids)), "duplicate_numeric_device_id")
        self.header_size = 24 + 8 * numeric
        for _id, off in self.numeric:
            self.check_start(off)

    def get(self, rel, size):
        extent(rel, size, self.length, "property_extent_out_of_bounds")
        return self.raw[rel:rel + size]

    def u32(self, rel):
        return struct.unpack("<I", self.get(rel, 4))[0]

    def check_start(self, off):
        need(off % 4 == 0 and self.header_size <= off < self.length,
             "property_list_start_invalid")

    def prop_name(self, word):
        rel = self.name_off + (word & 0x7fffffff)
        extent(rel, 1, self.length, "property_name_offset")
        end = self.raw.find(b"\0", rel, min(self.length, rel + MAX_NAME_BYTES))
        need(end >= rel, "property_name_unterminated")
        value = self.raw[rel:end]
        need(value and all(0 < b < 128 for b in value), "property_name_unresolved")
        # Names other than this allowlist are not retained or emitted.
        text = value.decode("ascii")
        return text if text in NAMES else None

    def parse_list(self, start, all_starts):
        self.check_start(start)
        next_start = min((v for v in all_starts if v > start),
                         default=self.length)
        end = min(next_start, self.length, start + MAX_LIST_BYTES)
        pos, entries = start, 0
        selected = {}
        while True:
            extent(pos, 4, end, "missing_property_end_marker")
            typ = self.u32(pos)
            if typ == END:
                return {"allowlisted": selected, "entry_count": entries,
                        "start": start, "end_marker": pos}
            entries += 1
            need(entries <= MAX_PROPERTIES, "property_count_limit")
            extent(pos, 8, end, "property_prefix_extent")
            word = self.u32(pos + 4)
            name = self.prop_name(word)
            base_type, payload = typ & 0x7fffffff, pos + 8
            if base_type in FIXED_TYPES:
                payload_size = 4
            elif base_type == 0x8:
                extent(payload, 1, end, "inline_sequence_prefix")
                length_minus_one = self.raw[payload]
                # The cited producer-side reader's uint8 skip temporary wraps
                # for these values; do not silently "repair" its behavior.
                need(length_minus_one < 254, "inline_sequence_skip_abi_boundary")
                payload_size = (length_minus_one + 2 + 3) & ~3
            else:
                raise Reject("unsupported_property_type")
            extent(payload, payload_size, end, "property_payload_extent")
            if name is not None:
                need(name not in selected, "duplicate_allowlisted_property")
                selected[name] = {
                    "type": base_type, "visibility_denied": bool(typ & 0x80000000),
                    "name_highbit": bool(word & 0x80000000),
                    "entry_va": self.va + pos, "payload_rel": payload,
                    "payload_size": payload_size}
            pos = payload + payload_size

    def resolve(self, name, record, struct_base):
        public = {k: v for k, v in record.items()
                  if k not in ("payload_rel", "payload_size")}
        if record["visibility_denied"]:
            public["status"] = "access_denied_by_format"
            return public
        typ, rel = record["type"], record["payload_rel"]
        if typ == 0x2:
            public.update(status="static_uint32_candidate", value=self.u32(rel))
            if name == "smem_max_items":
                public["loader_u16_cast_candidate"] = public["value"] & 0xffff
        elif typ == 0x12:
            index = self.u32(rel)
            need(index <= MAX_STRUCT_INDEX, "structure_index_limit")
            need(struct_base != 0, "null_structure_pointer_array")
            slot_va = struct_base + index * 8
            slot = self.view.describe(slot_va, 8, scoped=True, file_backed=True)
            target = struct.unpack("<Q", self.view.read(slot_va, 8))[0]
            need(target != 0, "null_structure_target")
            public.update(status="static_structure_pointer_candidate",
                          index=index, pointer_slot=slot,
                          target=self.view.describe(target),
                          array_total_length_known=False,
                          target_contents_read=False)
        elif typ in (0x11, 0x14, 0x18, 0x8):
            # Do not reinterpret unexpected types as scalar SMEM settings.
            public["status"] = "unexpected_type_for_requested_smem_setting"
        else:
            raise Reject("unreachable_property_resolution_type")
        public["expected_setting_type"] = (
            "structure_pointer" if name == "smem_partitions" else "uint32")
        public["matches_expected_setting_type"] = (
            typ == (0x12 if name == "smem_partitions" else 0x2))
        return public

def validate_candidate(view, head_va, descriptor_va, device_name):
    prop_va, structs, count, devices = struct.unpack("<QQI4xQ", view.read(head_va, 32))
    need(0 < count <= MAX_DEVICES, "string_device_limit")
    need(devices <= descriptor_va and (descriptor_va - devices) % 40 == 0,
         "descriptor_not_integral_array_member")
    target_index = (descriptor_va - devices) // 40
    need(target_index < count, "descriptor_outside_array")
    array = view.read(devices, count * 40)
    prop = PropBin(view, prop_va)
    rows, names = [], []
    starts = {off for _id, off in prop.numeric}
    for i in range(count):
        row = unpack_descriptor(view, devices + i * 40)
        name = view.cstring(row["name_ptr"])
        need(djb2(name) == row["hash"], "string_device_hash_mismatch")
        # Some driver-only descriptors can carry offset zero. Their layout is
        # validated, but zero is never parsed as a property list.
        if row["offset"]:
            prop.check_start(row["offset"])
            starts.add(row["offset"])
        need(row["collision_count"] <= count, "collision_count_limit")
        if row["collision_count"]:
            raw = view.read(row["collision_ptr"], 4 * row["collision_count"])
            collision_indices = struct.unpack("<" + "I" * row["collision_count"], raw)
            need(all(index < count for index in collision_indices),
                 "collision_index_out_of_range")
            row["collision_indices"] = collision_indices
        else:
            row["collision_indices"] = ()
        rows.append(row)
        names.append(name)
    target = rows[target_index]
    need(names[target_index] == device_name, "target_name_mismatch")
    expected_hash = djb2(device_name)
    first_hash_index = next((i for i, row in enumerate(rows)
                             if row["hash"] == expected_hash), None)
    need(first_hash_index is not None, "target_hash_not_in_array")
    first = rows[first_hash_index]
    if not first["collision_count"]:
        selected_index = first_hash_index
    else:
        selected_index = next((i for i in first["collision_indices"]
                               if names[i] == device_name), None)
    need(selected_index == target_index, "format_lookup_does_not_select_known_descriptor")
    local = prop.parse_list(target["offset"], starts)
    zero_rows = [(i, off) for i, (ident, off) in enumerate(prop.numeric) if ident == 0]
    global_result = None
    global_state = "not_present"
    if zero_rows:
        if zero_rows[0][0] == 0:
            global_state = "device0_is_first_numeric_entry"
            if any(name not in local["allowlisted"] for name in NAMES):
                global_result = prop.parse_list(zero_rows[0][1], starts)
        else:
            # The cited DALProps.c checks device0 exists, but then jumps to
            # numeric entry 0 rather than using the returned device0 handle.
            global_state = "device0_not_first_cited_fallback_not_assumed"
    properties = {}
    for name in NAMES:
        if name in local["allowlisted"]:
            source, rec = "device", local["allowlisted"][name]
        elif global_result and name in global_result["allowlisted"]:
            source, rec = "global_device0", global_result["allowlisted"][name]
        else:
            properties[name] = {"status": (
                "global_fallback_layout_unmodeled" if zero_rows and global_result is None
                else "not_found_in_bounded_lists")}
            continue
        item = prop.resolve(name, rec, structs)
        item["list_source"] = source
        properties[name] = item
    driver_info_mapping = None
    if target["driver_info"]:
        driver_info_mapping = view.describe(target["driver_info"])
    return {
        "dalprops": view.describe(head_va, 32, scoped=True, file_backed=True),
        "device_array": {"base": devices, "count": count, "stride": 40,
                         "byte_extent": count * 40, "sha256": digest(array),
                         "target_index": target_index,
                         "format_lookup_selected_index": selected_index,
                         "all_names_and_hashes_validated": True,
                         "non_allowlisted_names_exported": False},
        "target_descriptor": {
            "va": descriptor_va, "property_offset": target["offset"],
            "name_ptr": target["name_ptr"], "name_hash": target["hash"],
            "driver_info_pointer": target["driver_info"],
            "driver_info_mapping": driver_info_mapping,
            "driver_info_is_direct_function_pointer": False,
            "collision_count": target["collision_count"]},
        "propbin": {"va": prop_va, "length": prop.length,
                    "sha256": digest(prop.raw),
                    "name_section_offset": prop.name_off,
                    "string_section_offset": prop.string_off,
                    "byte_section_offset": prop.byte_off,
                    "uint32_section_offset": prop.u32_off,
                    "numeric_device_count": len(prop.numeric),
                    "global_fallback_state": global_state},
        "properties": properties,
        "platform_selected": False, "reachable_from_entry_proven": False,
        "smem_initialized_proven": False, "firmware_executed": False,
    }

def scan_image(view, descriptor_va, expected_file_offset, device_name_va, device_name):
    p, off = view.mapping(descriptor_va, 40)
    need(off == expected_file_offset, "known_descriptor_file_mapping_mismatch")
    known = unpack_descriptor(view, descriptor_va)
    need(known["name_ptr"] == device_name_va
         and view.cstring(known["name_ptr"]) == device_name,
         "known_descriptor_name_pointer_mismatch")
    need(known["hash"] == djb2(device_name), "known_descriptor_hash_mismatch")
    accepted, rejected = [], collections.Counter()
    scanned_heads = geometric = bounded_geometry = processed = 0
    for va in view.scan_heads():
        scanned_heads += 1
        try:
            _prop, _structs, count, devices = struct.unpack("<QQI4xQ", view.read(va, 32))
        except Reject as exc:
            rejected["head_" + exc.code] += 1
            continue
        if count == 0 or devices > descriptor_va:
            continue
        delta = descriptor_va - devices
        if delta % 40 or delta // 40 >= count:
            continue
        geometric += 1
        if count > MAX_DEVICES:
            rejected["string_device_limit"] += 1
            continue
        try:
            view.mapping(devices, count * 40)
        except Reject as exc:
            rejected["array_" + exc.code] += 1
            continue
        bounded_geometry += 1
        if processed >= MAX_GEOMETRIC_CANDIDATES:
            continue
        processed += 1
        try:
            accepted.append(validate_candidate(view, va, descriptor_va, device_name))
        except Reject as exc:
            rejected[exc.code] += 1
    return {
        "schema": "tb371fc-xbl-dal-static-candidates-v1",
        "analysis_completed": True,
        "scope": {"load_indices": list(view.scope), "alignment": 8,
                  "known_descriptor_va": descriptor_va,
                  "known_descriptor_file_offset": expected_file_offset,
                  "device_name": device_name.decode("ascii"),
                  "firmware_data_regions_read": "bounded nonexecuting LOAD ranges",
                  "excluded": ["RX scan", "firmware-volume parsing", "disassembly",
                               "firmware execution", "partition-table contents",
                               "certificates", "identity or user data"]},
        "format_sources": FORMAT_SOURCES,
        "limits": {"max_devices": MAX_DEVICES, "max_numeric_devices": MAX_NUMERIC,
                   "max_propbin_bytes": MAX_PROP_BIN,
                   "max_property_list_bytes": MAX_LIST_BYTES,
                   "max_properties_per_list": MAX_PROPERTIES,
                   "max_name_bytes": MAX_NAME_BYTES,
                   "max_structure_index": MAX_STRUCT_INDEX,
                   "max_geometric_candidates_processed": MAX_GEOMETRIC_CANDIDATES},
        "scanned_heads": scanned_heads, "geometric_candidates": geometric,
        "bounded_geometric_candidates": bounded_geometry,
        "processed_candidates": processed,
        "candidate_processing_truncated": bounded_geometry > processed,
        "rejected_reasons": dict(sorted(rejected.items())),
        "accepted_candidate_count": len(accepted),
        "candidate_details_omitted": max(0, len(accepted) - MAX_REPORTED_CANDIDATES),
        "candidates": accepted[:MAX_REPORTED_CANDIDATES],
        "platform_selected": False, "reachable_from_entry_proven": False,
        "smem_initialized_proven": False, "firmware_executed": False,
    }

def bounded_json(report):
    while True:
        encoded = (json.dumps(report, sort_keys=True, separators=(",", ":"),
                              ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if len(encoded) <= MAX_OUTPUT:
            return encoded
        if report.get("candidates"):
            report["candidates"].pop()
            report["candidate_details_omitted"] += 1
            continue
        raise Reject("metadata_output_limit")

def read_pinned_input():
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fd = os.open(INPUT, flags)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and stat.S_IMODE(before.st_mode) == 0o600
             and before.st_size == INPUT_SIZE, "private_input_stat_mismatch")
        chunks, total = [], 0
        while total < INPUT_SIZE + 1:
            block = os.read(fd, min(131072, INPUT_SIZE + 1 - total))
            if not block:
                break
            chunks.append(block)
            total += len(block)
        after = os.fstat(fd)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
              before.st_ctime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
              after.st_ctime_ns), "private_input_changed")
        data = b"".join(chunks)
        need(len(data) == INPUT_SIZE and digest(data) == INPUT_SHA256,
             "private_input_size_or_hash_mismatch")
        return data
    finally:
        os.close(fd)

def main():
    need(len(sys.argv) == 1, "no_arguments_supported")
    data = read_pinned_input()
    view = ELFView(data, SCOPE_PHS)
    report = scan_image(view, DESCRIPTOR_VA, DESCRIPTOR_FILE_OFFSET,
                        DEVICE_NAME_VA, DEVICE_NAME)
    report["input"] = {"basename": "xbl_a.bin", "size": INPUT_SIZE,
                       "sha256": INPUT_SHA256}
    report["input_scope"] = "existing validated private backup; no block-device read"
    sys.stdout.buffer.write(bounded_json(report))
    return 0

if __name__ == "__main__":
    try:
        status = main()
    except Reject as exc:
        sys.stdout.write(json.dumps({"schema": "tb371fc-xbl-dal-static-candidates-v1",
                                    "analysis_completed": False, "error": exc.code,
                                    "firmware_executed": False},
                                   sort_keys=True, separators=(",", ":")) + "\n")
        status = 2
    except Exception as exc:
        # Avoid echoing arbitrary file contents or environment paths.
        sys.stdout.write(json.dumps({"schema": "tb371fc-xbl-dal-static-candidates-v1",
                                    "analysis_completed": False,
                                    "error": "unexpected_" + type(exc).__name__,
                                    "firmware_executed": False},
                                   sort_keys=True, separators=(",", ":")) + "\n")
        status = 3
    sys.exit(status)
