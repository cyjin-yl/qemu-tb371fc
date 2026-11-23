#!/usr/bin/env python3
"""Self-authored, in-memory format fixtures. No captured firmware is read."""
import importlib.util
import json
from pathlib import Path
import struct
import unittest

SOURCE = Path(__file__).with_name("dal_smem_candidates.py")
spec = importlib.util.spec_from_file_location("dal_smem_candidates", SOURCE)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)

BASE = 0x100000
PROP = 0x200000
HEAD = BASE + 0x400
DESC = BASE + 0x100
TARGET_NAME = BASE + 0x300
STRUCTS = BASE + 0x600
STRUCT_TARGET = BASE + 0x680
LOCAL = 0x180
GLOBAL = 0x1d0
SYNTHETIC_VALUES = {
    "smem_vers": 0x31415926,
    "smem_toc_vers": 0x27182818,
    "smem_max_items": 0x12345,
}

def fixture_hash(value):
    # Independent fixture construction of the documented recurrence.
    accum = 5381
    for byte in value:
        accum = ((accum << 5) + accum + byte) % (1 << 32)
    return accum

class Fixture:
    def __init__(self, omit=(), duplicate=None, denied=None, kind_override=None):
        self.data = bytearray(0x3000)
        self.data[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
        struct.pack_into("<HHIQQQIHHHHHH", self.data, 16,
                         2, 183, 1, BASE, 64, 0, 0, 64, 56, 2, 0, 0, 0)
        for i, (off, va) in enumerate(((0x1000, BASE), (0x2000, PROP))):
            struct.pack_into("<IIQQQQQQ", self.data, 64 + 56 * i,
                             1, 4, off, va, va, 0x1000, 0x1000, 0x1000)
        self.put(TARGET_NAME, scan.DEVICE_NAME + b"\0")
        self.put(BASE + 0x340, b"/fixture/aux\0")
        self.row(DESC, TARGET_NAME, fixture_hash(scan.DEVICE_NAME), LOCAL)
        self.row(DESC + 40, BASE + 0x340, fixture_hash(b"/fixture/aux"), 0)
        self.put(HEAD, struct.pack("<QQI4xQ", PROP, STRUCTS, 2, DESC))
        self.put(STRUCTS + 2 * 8, struct.pack("<Q", STRUCT_TARGET))
        self.put(STRUCT_TARGET, b"SELF_AUTHORED_FIXTURE_NOT_A_PMIC_OR_SMEM_TABLE\0")
        self.names = {}
        at = 0x40
        for name in scan.NAMES:
            self.names[name] = at - 0x40
            encoded = name.encode("ascii") + b"\0"
            self.put(PROP + at, encoded)
            at += len(encoded)
        self.put(PROP, struct.pack("<6I", 0x400, 0x40, 0x120, 0x130, 0x140, 1))
        self.put(PROP + 24, struct.pack("<II", 0, GLOBAL))
        records = []
        for name in scan.NAMES:
            if name in omit:
                continue
            typ = 0x12 if name == "smem_partitions" else 0x2
            value = 2 if name == "smem_partitions" else SYNTHETIC_VALUES[name]
            if kind_override and kind_override[0] == name:
                typ, value = kind_override[1:]
            if denied == name:
                typ |= 0x80000000
            records.append((name, typ, value))
            if duplicate == name:
                records.append((name, typ, value + 1))
        self.list(LOCAL, records)
        self.list(GLOBAL, [("smem_toc_vers", 0x2, 0x13579bdf)])
        self.descriptor = DESC

    @staticmethod
    def file_offset(va):
        if BASE <= va < BASE + 0x1000:
            return 0x1000 + va - BASE
        if PROP <= va < PROP + 0x1000:
            return 0x2000 + va - PROP
        raise ValueError("fixture write outside allocation")

    def put(self, va, raw):
        off = self.file_offset(va)
        if off + len(raw) > len(self.data):
            raise ValueError("fixture write exceeds allocation")
        self.data[off:off + len(raw)] = raw

    def row(self, va, name, hashed, offset, info=0, collisions=0, collision_ptr=0):
        self.put(va, struct.pack("<QIIQI4xQ", name, hashed, offset,
                                info, collisions, collision_ptr))

    def u32(self, va, value):
        self.put(va, struct.pack("<I", value))

    def u64(self, va, value):
        self.put(va, struct.pack("<Q", value))

    def list(self, rel, records):
        payload = bytearray()
        for name, typ, value in records:
            payload += struct.pack("<II", typ, 0x80000000 | self.names[name])
            payload += struct.pack("<I", value)
        payload += struct.pack("<I", scan.END)
        self.put(PROP + rel, payload)

    def view(self):
        return scan.ELFView(bytes(self.data), (0, 1))

    def validate(self):
        return scan.validate_candidate(self.view(), HEAD, self.descriptor,
                                       scan.DEVICE_NAME)

    def report(self):
        return scan.scan_image(self.view(), self.descriptor,
                               self.file_offset(self.descriptor),
                               TARGET_NAME, scan.DEVICE_NAME)

class StaticCandidateTests(unittest.TestCase):
    def rejected(self, fix, code, whole_scan=False):
        with self.assertRaises(scan.Reject) as error:
            fix.report() if whole_scan else fix.validate()
        self.assertEqual(error.exception.code, code)

    def test_known_hash_vector(self):
        self.assertEqual(scan.djb2(b"hello"), 0x0f923099)

    def test_complete_candidate_keeps_static_boundary(self):
        fix = Fixture()
        report = fix.report()
        candidates = [x for x in report["candidates"] if x["dalprops"]["va"] == HEAD]
        self.assertEqual(len(candidates), 1)
        record = candidates[0]
        for name, value in SYNTHETIC_VALUES.items():
            self.assertEqual(record["properties"][name]["value"], value)
        for key in ("platform_selected", "reachable_from_entry_proven",
                    "smem_initialized_proven", "firmware_executed"):
            self.assertIs(record[key], False)
        self.assertEqual(record["device_array"]["target_index"], 0)
        self.assertTrue(record["device_array"]["all_names_and_hashes_validated"])

    def test_struct_payload_is_array_index_not_address(self):
        prop = Fixture().validate()["properties"]["smem_partitions"]
        self.assertEqual(prop["index"], 2)
        self.assertEqual(prop["pointer_slot"]["va"], STRUCTS + 16)
        self.assertEqual(prop["target"]["va"], STRUCT_TARGET)
        self.assertFalse(prop["array_total_length_known"])
        self.assertFalse(prop["target_contents_read"])

    def test_max_items_cast_is_reported_separately(self):
        prop = Fixture().validate()["properties"]["smem_max_items"]
        self.assertEqual(prop["value"], 0x12345)
        self.assertEqual(prop["loader_u16_cast_candidate"], 0x2345)

    def test_wrong_known_descriptor_hash_fails(self):
        fix = Fixture()
        fix.u32(DESC + 8, 0)
        self.rejected(fix, "known_descriptor_hash_mismatch", whole_scan=True)

    def test_known_descriptor_file_mapping_must_match(self):
        fix = Fixture()
        with self.assertRaises(scan.Reject) as error:
            scan.scan_image(fix.view(), DESC, 0x1108, TARGET_NAME, scan.DEVICE_NAME)
        self.assertEqual(error.exception.code, "known_descriptor_file_mapping_mismatch")

    def test_entire_string_array_is_checked(self):
        fix = Fixture()
        fix.u32(DESC + 40 + 8, 1)
        self.rejected(fix, "string_device_hash_mismatch")

    def test_array_extent_must_be_file_backed(self):
        fix = Fixture()
        fix.u32(HEAD + 16, 200)
        self.rejected(fix, "missing_or_ambiguous_pointer_mapping")

    def test_device_count_bound(self):
        fix = Fixture()
        fix.u32(HEAD + 16, scan.MAX_DEVICES + 1)
        self.rejected(fix, "string_device_limit")

    def test_descriptor_requires_integral_stride(self):
        fix = Fixture()
        fix.u64(HEAD + 24, DESC - 1)
        self.rejected(fix, "descriptor_not_integral_array_member")

    def test_propbin_length_bound(self):
        fix = Fixture()
        fix.u32(PROP, scan.MAX_PROP_BIN + 1)
        self.rejected(fix, "propbin_length_limit")

    def test_propbin_whole_extent_must_map(self):
        fix = Fixture()
        fix.u32(PROP, 0x2000)
        self.rejected(fix, "missing_or_ambiguous_pointer_mapping")

    def test_section_offset_bound(self):
        fix = Fixture()
        fix.u32(PROP + 4, 0x401)
        self.rejected(fix, "section_offset_out_of_bounds")

    def test_struct_index_limit(self):
        fix = Fixture()
        fix.u32(PROP + LOCAL + 2 * 12 + 8, scan.MAX_STRUCT_INDEX + 1)
        self.rejected(fix, "structure_index_limit")

    def test_struct_slot_must_map(self):
        fix = Fixture()
        fix.u32(PROP + LOCAL + 2 * 12 + 8, 600)
        self.rejected(fix, "missing_or_ambiguous_pointer_mapping")

    def test_null_struct_target_is_not_success(self):
        fix = Fixture()
        fix.u64(STRUCTS + 16, 0)
        self.rejected(fix, "null_structure_target")

    def test_missing_property_does_not_get_a_default(self):
        record = Fixture(omit=("smem_partitions",)).validate()
        prop = record["properties"]["smem_partitions"]
        self.assertEqual(prop["status"], "not_found_in_bounded_lists")
        self.assertNotIn("value", prop)
        self.assertNotIn("target", prop)

    def test_denial_does_not_fall_back_or_expose_scalar(self):
        record = Fixture(denied="smem_toc_vers").validate()
        prop = record["properties"]["smem_toc_vers"]
        self.assertEqual(prop["status"], "access_denied_by_format")
        self.assertEqual(prop["list_source"], "device")
        self.assertNotIn("value", prop)

    def test_duplicate_allowed_name_is_rejected(self):
        self.rejected(Fixture(duplicate="smem_vers"),
                      "duplicate_allowlisted_property")

    def test_global_fallback_when_device0_is_first(self):
        prop = Fixture(omit=("smem_toc_vers",)).validate()["properties"]["smem_toc_vers"]
        self.assertEqual(prop["list_source"], "global_device0")
        self.assertEqual(prop["value"], 0x13579bdf)

    def test_global_fallback_order_is_not_repaired(self):
        fix = Fixture(omit=("smem_toc_vers",))
        fix.u32(PROP + 20, 2)
        fix.put(PROP + 24, struct.pack("<4I", 7, 0x200, 0, GLOBAL))
        fix.list(0x200, [])
        prop = fix.validate()["properties"]["smem_toc_vers"]
        self.assertEqual(prop["status"], "global_fallback_layout_unmodeled")
        self.assertNotIn("value", prop)

    def test_unneeded_global_contents_are_not_traversed(self):
        fix = Fixture()
        fix.u32(PROP + GLOBAL, 0xdeadbeef)
        self.assertEqual(fix.validate()["properties"]["smem_toc_vers"]["value"],
                         SYNTHETIC_VALUES["smem_toc_vers"])

    def test_unexpected_type_is_not_reinterpreted(self):
        fix = Fixture(kind_override=("smem_partitions", 0x2, 42))
        prop = fix.validate()["properties"]["smem_partitions"]
        self.assertFalse(prop["matches_expected_setting_type"])
        self.assertEqual(prop["value"], 42)
        self.assertNotIn("target", prop)

    def test_collision_resolution_uses_names_and_indices(self):
        fix = Fixture()
        alias = scan.DEVICE_NAME[:-2] + b"fL"
        self.assertNotEqual(alias, scan.DEVICE_NAME)
        self.assertEqual(fixture_hash(alias), fixture_hash(scan.DEVICE_NAME))
        fix.put(BASE + 0x350, alias + b"\0")
        fix.put(BASE + 0x700, struct.pack("<II", 0, 1))
        fix.row(DESC, BASE + 0x350, fixture_hash(alias), 0,
                collisions=2, collision_ptr=BASE + 0x700)
        fix.row(DESC + 40, TARGET_NAME, fixture_hash(scan.DEVICE_NAME), LOCAL)
        fix.descriptor = DESC + 40
        record = fix.validate()
        self.assertEqual(record["device_array"]["target_index"], 1)
        self.assertEqual(record["device_array"]["format_lookup_selected_index"], 1)

    def test_collision_index_cannot_escape_array(self):
        fix = Fixture()
        fix.u32(DESC + 24, 1)
        fix.u64(DESC + 32, BASE + 0x700)
        fix.put(BASE + 0x700, struct.pack("<I", 2))
        self.rejected(fix, "collision_index_out_of_range")

    def test_zero_collision_count_does_not_require_null_pointer(self):
        fix = Fixture()
        fix.u64(DESC + 32, 0xffffffffffffffff)
        self.assertEqual(fix.validate()["device_array"]["target_index"], 0)

    def test_direct_function_pointer_is_not_inferred(self):
        fix = Fixture()
        fix.u64(DESC + 16, BASE + 0x800)
        target = fix.validate()["target_descriptor"]
        self.assertFalse(target["driver_info_is_direct_function_pointer"])
        self.assertEqual(target["driver_info_mapping"]["flags"], 4)

    def test_executable_segment_cannot_be_scan_scope(self):
        fix = Fixture()
        struct.pack_into("<I", fix.data, 64 + 4, 5)
        with self.assertRaises(scan.Reject) as error:
            fix.view()
        self.assertEqual(error.exception.code, "invalid_scan_scope")

    def test_overlapping_address_mapping_is_rejected(self):
        fix = Fixture()
        struct.pack_into("<Q", fix.data, 64 + 56 + 16, BASE)
        self.rejected(fix, "missing_or_ambiguous_pointer_mapping", whole_scan=True)

    def test_filesz_cannot_exceed_memsz(self):
        fix = Fixture()
        struct.pack_into("<Q", fix.data, 64 + 40, 1)
        with self.assertRaises(scan.Reject) as error:
            fix.view()
        self.assertEqual(error.exception.code, "load_filesz_exceeds_memsz")

    def test_unknown_type_stops_list(self):
        fix = Fixture()
        fix.u32(PROP + LOCAL, 0x77)
        self.rejected(fix, "unsupported_property_type")

    def test_inline_byte_sequence_skip_boundary_rejected(self):
        fix = Fixture()
        fix.u32(PROP + LOCAL, 0x8)
        fix.put(PROP + LOCAL + 8, b"\xfe")
        self.rejected(fix, "inline_sequence_skip_abi_boundary")

    def test_other_device_names_and_contents_are_not_exported(self):
        encoded = scan.bounded_json(Fixture().report())
        self.assertNotIn(b"/fixture/aux", encoded)
        self.assertNotIn(b"SELF_AUTHORED_FIXTURE", encoded)
        self.assertNotIn(b"FIXTURE_NOT_A_PMIC", encoded)
        self.assertLessEqual(len(encoded), scan.MAX_OUTPUT)

    def test_output_truncation_keeps_real_total_and_count(self):
        report = Fixture().report()
        candidate = report["candidates"][0]
        report["candidates"] = [candidate for _ in range(20)]
        report["accepted_candidate_count"] = 20
        report["candidate_details_omitted"] = 0
        encoded = scan.bounded_json(report)
        loaded = json.loads(encoded)
        self.assertLessEqual(len(encoded), scan.MAX_OUTPUT)
        self.assertEqual(loaded["accepted_candidate_count"], 20)
        self.assertEqual(loaded["candidate_details_omitted"],
                         20 - len(loaded["candidates"]))
        self.assertGreater(loaded["candidate_details_omitted"], 0)

if __name__ == "__main__":
    unittest.main()
