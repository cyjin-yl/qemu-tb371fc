#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Review-draft adapter fixtures; self-authored bytes, no private backup I/O.

The small JSON document simulates the already verified DAL producer's receipt.
These tests validate the new adapter, not a second implementation of that DAL
producer. The immutable partition core is loaded from its fixed sibling/hash.
"""
from dataclasses import replace
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

PATH = Path(__file__).with_name("observe_smem_partition_candidate.py")
SPEC = importlib.util.spec_from_file_location("_tb371fc_observed_adapter_fixture", PATH)
if SPEC is None or SPEC.loader is None:
    raise ImportError("fixed adapter sibling unavailable")
adapter = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = adapter
SPEC.loader.exec_module(adapter)

BASE = 0x10000000
LOAD_OFFSET = 0x1000
STRUCT_BASE = BASE + 0x180
INDEX = 2
PLAIN_VA = STRUCT_BASE + 8 * INDEX
SIZED_VA = STRUCT_BASE + 16 * INDEX
SLOT_VA = SIZED_VA + 8
TARGET = BASE + 0x400
VALUES = (0x31415926, 0x27182818, 0x12345)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")


def file_offset(va):
    return LOAD_OFFSET + va - BASE


def record32(size=4096, flags=0, host0=0, host1=1, cacheline=64,
             exclusions=(0, 0, 0, 0)):
    return struct.pack("<9I", size, flags, host0, host1, cacheline, *exclusions)


def short_stop32():
    return struct.pack("<3I", 0, 0, 0xffff)


def mapping(va, size):
    return {"va": va, "size": size, "file_offset": file_offset(va),
            "load_index": 0, "file_backed": True, "flags": 4,
            "load_va": BASE, "load_memsz": 0x1000, "load_filesz": 0x1000}


class Fixture:
    def __init__(self, payload=None):
        self.image = bytearray(0x2000)
        self.image[:16] = b"\x7fELF\x02\x01\x01" + b"\0" * 9
        struct.pack_into("<HHIQQQIHHHHHH", self.image, 16,
                         2, 183, 1, BASE, 64, 0, 0, 64, 56, 1, 0, 0, 0)
        struct.pack_into("<IIQQQQQQ", self.image, 64,
                         1, 4, LOAD_OFFSET, BASE, BASE, 0x1000, 0x1000, 0x1000)
        # Under the plain-pointer hypothesis this value has no LOAD mapping.
        self.put(PLAIN_VA, struct.pack("<Q", 8))
        # The independent sized hypothesis: size=8, unconstrained nonzero padding.
        self.put(SIZED_VA, struct.pack("<IIQ", 8, 0x12345678, TARGET))
        if payload is None:
            payload = record32() + record32(size=8192, flags=4, host1=2) + short_stop32()
        self.put(TARGET, payload)
        self.put(BASE + 0xf00, b"NEVER_EXPORT_UNUSED_FIXTURE_PAYLOAD\0")
        self.contract = adapter.ObservationContract(
            image_size=len(self.image), image_sha256="0" * 64,
            receipt_size=0, receipt_sha256="0" * 64,
            scope_load_indices=(0,), dalprops_va=BASE + 0x40,
            descriptor_va=BASE + 0x80, descriptor_file_offset=file_offset(BASE + 0x80),
            device_count=3, device_index=1, struct_base=STRUCT_BASE,
            property_index=INDEX, plain_file_offset=file_offset(PLAIN_VA),
            sized_file_offset=file_offset(SIZED_VA),
            slot_file_offset=file_offset(SLOT_VA), target_va=TARGET,
            target_file_offset=file_offset(TARGET), load_index=0,
            declared_size=8, scalar_values=VALUES)
        common = {"layout_selected": False, "native_build_flag_proven": False,
                  "array_total_length_known": False, "target_contents_decoded": False}
        plain = {
            **common, "layout": "plain-pointer-aapcs64", "entry_stride": 8,
            "pointer_offset": 0, "record_va": PLAIN_VA,
            "record_mapping": mapping(PLAIN_VA, 8),
            "status": "layout_mapping_not_validated",
            "error_code": "missing_or_ambiguous_pointer_mapping",
        }
        sized = {
            **common, "layout": "size-plus-pointer-aapcs64", "entry_stride": 16,
            "pointer_offset": 8, "record_va": SIZED_VA,
            "record_mapping": mapping(SIZED_VA, 16),
            "pointer_slot": mapping(SLOT_VA, 8), "target": mapping(TARGET, 1),
            "declared_size": 8, "declared_size_is_array_total_length": False,
            "status": "slot_and_target_mapping_observed",
        }
        properties = {}
        for name, value in zip(("smem_vers", "smem_toc_vers", "smem_max_items"), VALUES):
            properties[name] = {
                "type": 2, "value": value, "status": "static_uint32_candidate",
                "property_header_format": "compact-memory-optimised-candidate",
                "list_source": "device", "matches_expected_setting_type": True,
                "visibility_denied": False,
            }
        properties["smem_max_items"]["loader_u16_cast_candidate"] = VALUES[2] & 0xffff
        properties["smem_partitions"] = {
            "type": 0x12, "index": INDEX, "struct_pointer_array_va": STRUCT_BASE,
            "status": "static_structure_index_with_unselected_layouts",
            "property_header_format": "compact-memory-optimised-candidate",
            "list_source": "device", "matches_expected_setting_type": True,
            "visibility_denied": False, "struct_layout_selected": False,
            "array_total_length_known": False, "target_contents_read": False,
            "mapped_layout_count": 1, "layout_candidates": [plain, sized],
        }
        item = {
            "property_format": "compact-memory-optimised-candidate",
            "platform_selected": False, "reachable_from_entry_proven": False,
            "smem_initialized_proven": False, "firmware_executed": False,
            "native_hw_version_used": False,
            "dalprops": {"va": BASE + 0x40}, "target_descriptor": {"va": BASE + 0x80},
            "device_array": {"count": 3, "target_index": 1,
                             "format_lookup_selected_index": 1, "stride": 40,
                             "all_names_and_hashes_validated": True,
                             "non_allowlisted_names_exported": False},
            "properties": properties,
        }
        self.report = {
            "schema": "tb371fc-xbl-dal-static-candidates-v3",
            "property_format": "compact-memory-optimised-candidate",
            "analysis_completed": True, "platform_selected": False,
            "reachable_from_entry_proven": False, "smem_initialized_proven": False,
            "firmware_executed": False, "candidate_processing_truncated": False,
            "accepted_candidate_count": 1, "candidate_details_omitted": 0,
            "bounded_geometric_candidates": 1, "processed_candidates": 1,
            "input": {"basename": "xbl_a.bin", "size": len(self.image), "sha256": ""},
            "scope": {"load_indices": [0], "device_name": "/core/mproc/smem",
                      "known_descriptor_va": BASE + 0x80,
                      "known_descriptor_file_offset": file_offset(BASE + 0x80)},
            "candidates": [item],
        }
        self.sign()

    def put(self, va, raw):
        off = file_offset(va)
        if off < LOAD_OFFSET or off + len(raw) > len(self.image):
            raise ValueError("synthetic write outside one LOAD")
        self.image[off:off + len(raw)] = raw

    @property
    def item(self):
        return self.report["candidates"][0]

    @property
    def prop(self):
        return self.item["properties"]["smem_partitions"]

    def sign(self):
        image_hash = digest(bytes(self.image))
        self.report["input"].update(size=len(self.image), sha256=image_hash)
        self.raw = encoded(self.report)
        self.contract = replace(self.contract,
                                image_size=len(self.image), image_sha256=image_hash,
                                receipt_size=len(self.raw), receipt_sha256=digest(self.raw))
        return self

    def observe(self):
        return adapter.observe_bytes(bytes(self.image), self.raw, self.contract)


class ObservedSlotAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.core = adapter.load_core()

    def rejected(self, fix, code):
        result = fix.observe()
        self.assertIs(result["analysis_completed"], False)
        self.assertEqual(result["rejection"]["code"], code)
        self.assertIs(result["smem_initialized"], False)
        self.assertIsNone(result["selected_layout"])
        self.assertIsNone(self.core.NATIVE_PIN)
        return result

    def test_fixed_slot_observation_keeps_both_enum_candidates_and_no_native_selection(self):
        result = Fixture().observe()
        self.assertIs(result["analysis_completed"], True)
        self.assertEqual(result["input_contract_kind"], "self_authored_synthetic_fixture_contract")
        pin = result["observed_candidate_slot_pin"]
        self.assertEqual(pin["identity_kind"], "observed_slot_only")
        self.assertEqual(pin["pointer_slot"]["va"], SLOT_VA)
        self.assertEqual(pin["target_mapping"]["va"], TARGET)
        self.assertIs(pin["native_layout_selected"], False)
        candidates = result["conditional_partition_candidates"]["candidates"]
        self.assertEqual([row["layout"] for row in candidates],
                         ["enum32_stride36", "enum16_stride32"])
        self.assertTrue(candidates[0]["source_iteration_terminated"])
        self.assertEqual(candidates[0]["terminator"]["record_index"], 2)
        self.assertEqual(candidates[0]["reserved_bytes_contribution"], 12288)
        self.assertEqual(candidates[0]["toc_entries_contribution"], 1)
        self.assertGreater(candidates[0]["highest_read_end_va"], TARGET + 8)
        # dwSize=8 is deliberately not used as the candidate array length.
        self.assertIs(result["array_total_length_known"], False)
        self.assertIs(result["abi_confirmed"], False)
        self.assertIsNone(result["selected_layout"])
        self.assertIsNone(self.core.NATIVE_PIN)

    def test_image_size_and_hash_are_checked_before_candidate_decoding(self):
        fix = Fixture()
        for raw in (bytes(fix.image[:-1]),
                    bytes(fix.image[:-1]) + bytes([fix.image[-1] ^ 1])):
            with self.subTest(length=len(raw)):
                result = adapter.observe_bytes(raw, fix.raw, fix.contract)
                self.assertIs(result["analysis_completed"], False)
                self.assertEqual(result["rejection"]["code"], "image_byte_identity")
                self.assertNotIn("conditional_partition_candidates", result)

    def test_receipt_exact_bytes_are_required(self):
        fix = Fixture()
        result = adapter.observe_bytes(bytes(fix.image), fix.raw + b" ", fix.contract)
        self.assertEqual(result["rejection"]["code"], "receipt_byte_identity")
        changed = fix.raw.replace(b'"analysis_completed":true', b'"analysis_completed":fals')
        result = adapter.observe_bytes(bytes(fix.image), changed, fix.contract)
        self.assertEqual(result["rejection"]["code"], "receipt_byte_identity")

    def test_duplicate_keys_and_nonfinite_values_are_not_hidden_by_json_decoder(self):
        fix = Fixture()
        alternatives = (
            (b'{"analysis_completed":false,' + fix.raw[1:], "receipt_duplicate_json_key"),
            (fix.raw.replace(b'"accepted_candidate_count":1', b'"accepted_candidate_count":NaN'),
             "receipt_nonfinite_json_number"),
        )
        for raw, reason in alternatives:
            with self.subTest(reason=reason):
                contract = replace(fix.contract, receipt_size=len(raw), receipt_sha256=digest(raw))
                result = adapter.observe_bytes(bytes(fix.image), raw, contract)
                self.assertEqual(result["rejection"]["code"], reason)
                self.assertNotIn("conditional_partition_candidates", result)

    def test_runtime_and_layout_selection_claims_are_rejected(self):
        changes = (
            ("top", "platform_selected"),
            ("item", "reachable_from_entry_proven"),
            ("property", "struct_layout_selected"),
            ("sized", "native_build_flag_proven"),
        )
        for where, key in changes:
            with self.subTest(where=where, key=key):
                fix = Fixture()
                target = {"top": fix.report, "item": fix.item, "property": fix.prop,
                          "sized": fix.prop["layout_candidates"][1]}[where]
                target[key] = True
                fix.sign()
                result = fix.observe()
                self.assertIs(result["analysis_completed"], False)
                self.assertNotIn("conditional_partition_candidates", result)
                self.assertIn(result["rejection"]["code"], (
                    "receipt_runtime_or_truncation_claim", "receipt_candidate_runtime_claim",
                    "receipt_partition_selection_claim", "receipt_layout_selection_claim"))

    def test_candidate_counts_truncation_and_bool_indices_cannot_pass_as_valid(self):
        mutations = (
            ("count", "receipt_candidate_count"),
            ("truncated", "receipt_runtime_or_truncation_claim"),
            ("extra", "receipt_candidate_list"),
            ("bool_scope", "receipt_scope"),
        )
        for kind, reason in mutations:
            with self.subTest(kind=kind):
                fix = Fixture()
                if kind == "count":
                    fix.report["accepted_candidate_count"] = True
                elif kind == "truncated":
                    fix.report["candidate_processing_truncated"] = True
                elif kind == "extra":
                    fix.report["candidates"].append(fix.item.copy())
                else:
                    fix.report["scope"]["load_indices"] = [False]
                fix.sign()
                self.rejected(fix, reason)

    def test_index_slot_geometry_and_pointer_metadata_are_not_reinterpreted(self):
        mutations = (
            ("index", "receipt_partition_identity"),
            ("base", "receipt_partition_identity"),
            ("stride", "receipt_layout_geometry"),
            ("slot_offset", "receipt_mapping_identity"),
            ("target", "receipt_mapping_identity"),
        )
        for kind, reason in mutations:
            with self.subTest(kind=kind):
                fix = Fixture()
                sized = fix.prop["layout_candidates"][1]
                if kind == "index":
                    fix.prop["index"] += 1
                elif kind == "base":
                    fix.prop["struct_pointer_array_va"] += 8
                elif kind == "stride":
                    sized["entry_stride"] = 8
                elif kind == "slot_offset":
                    sized["pointer_slot"]["file_offset"] += 8
                else:
                    sized["target"]["va"] += 4
                fix.sign()
                self.rejected(fix, reason)

    def test_plain_unknown_cannot_borrow_the_sized_target(self):
        fix = Fixture()
        result = fix.observe()
        plain = result["other_layout_observations"][0]
        self.assertEqual(plain["status"], "layout_mapping_not_validated")
        self.assertIsNone(plain["target"])
        fix.prop["layout_candidates"][0]["target"] = mapping(TARGET, 1)
        fix.sign()
        self.rejected(fix, "receipt_plain_unknown_must_be_preserved")

    def test_mapped_layout_count_and_scalar_values_are_bound_to_receipt_contract(self):
        fix = Fixture()
        fix.prop["mapped_layout_count"] = 2
        fix.sign()
        self.rejected(fix, "receipt_partition_identity")
        fix = Fixture()
        fix.item["properties"]["smem_max_items"]["value"] = 7
        fix.sign()
        self.rejected(fix, "receipt_scalar_value")

    def test_matching_hashes_do_not_hide_a_changed_pointer_slot_value(self):
        fix = Fixture()
        fix.put(SLOT_VA, struct.pack("<Q", TARGET + 4))
        fix.sign()
        result = self.rejected(fix, "core_observed_pointer_rejected")
        self.assertEqual(result["core_rejection"]["code"], "pointer_slot_value_mismatch")

    def test_actual_elf_mapping_checks_override_inconsistent_receipt_geometry(self):
        for which, reason in (("execute", "executable_load_rejected"),
                              ("bss", "read_enters_unbacked_load_tail")):
            with self.subTest(which=which):
                fix = Fixture()
                if which == "execute":
                    struct.pack_into("<I", fix.image, 64 + 4, 5)
                else:
                    struct.pack_into("<Q", fix.image, 64 + 32, 0x300)
                fix.sign()
                result = self.rejected(fix, "core_observed_pointer_rejected")
                self.assertEqual(result["core_rejection"]["code"], reason)

    def test_record_cap_and_output_omissions_do_not_select_a_plausible_layout(self):
        payload = record32(size=0x11111111, flags=0x11111110,
                           host0=0x11111111, host1=0x22222222,
                           cacheline=0x33333333,
                           exclusions=(0x1111, 0x2222, 0x3333, 0x4444)) * 16
        # A later terminator must not extend the authorized 16-record observation.
        fix = Fixture(payload + short_stop32())
        report = fix.observe()
        self.assertIs(report["analysis_completed"], True)
        original = report["conditional_partition_candidates"]["candidates"]
        self.assertFalse(original[0]["source_iteration_terminated"])
        self.assertEqual(original[0]["rejection"]["code"], "record_cap_without_terminator")
        self.assertEqual(len(original[0]["entries"]), 16)
        for row in original:
            self.assertLessEqual(len(row["entries"]), 16)
            self.assertLessEqual(row["highest_read_end_va"] - TARGET, 16 * row["stride"])
            self.assertIs(row["selection_proven"], False)
        raw = adapter.bounded_json(report)
        exported = json.loads(raw)
        self.assertLessEqual(len(raw), 12288)
        self.assertNotIn(b"NEVER_EXPORT_UNUSED_FIXTURE_PAYLOAD", raw)
        trimmed = exported["conditional_partition_candidates"]["candidates"]
        omitted = 0
        for before, after in zip(original, trimmed):
            self.assertEqual(after["entries_total_observed"], len(before["entries"]))
            self.assertEqual(after["entries_omitted"],
                             len(before["entries"]) - len(after["entries"]))
            self.assertEqual(after["anomalies_total_observed"], len(before["anomalies"]))
            self.assertEqual(after["anomalies_omitted"],
                             len(before["anomalies"]) - len(after["anomalies"]))
            self.assertEqual(after["reserved_bytes_contribution"], before["reserved_bytes_contribution"])
            omitted += after["entries_omitted"] + after["anomalies_omitted"]
        self.assertGreater(omitted, 0)
        self.assertIsNone(exported["selected_layout"])
        self.assertIs(exported["smem_initialized"], False)
        self.assertEqual(len(original[0]["entries"]), 16)  # serializer did not mutate source report

    def test_core_identity_and_native_pin_are_enforced_without_modifying_source(self):
        with patch.object(adapter, "_CORE", None), \
                patch.object(adapter, "read_regular", return_value=b"NATIVE_PIN = None\n"):
            with self.assertRaises(adapter.Rejected) as error:
                adapter.load_core()
            self.assertEqual(error.exception.code, "core_source_identity")
        with patch.object(self.core, "NATIVE_PIN", object()):
            with self.assertRaises(adapter.Rejected) as error:
                adapter.load_core()
            self.assertEqual(error.exception.code, "core_native_pin_must_remain_none")
        self.assertIsNone(self.core.NATIVE_PIN)

    def test_cli_requires_exact_conditional_mode_without_opening_private_inputs(self):
        class Sink:
            def __init__(self):
                self.buffer = io.BytesIO()
        for arguments in ([], ["--image", "/not-an-input"],
                          [adapter.MODE, "--image", "/not-an-input"]):
            with self.subTest(arguments=arguments):
                sink = Sink()
                with patch.object(adapter.sys, "stdout", sink), \
                        patch.object(adapter, "read_regular",
                                     side_effect=AssertionError("private input must stay unopened")) as reader:
                    code = adapter.main(arguments)
                self.assertEqual(code, 64)
                reader.assert_not_called()
                result = json.loads(sink.buffer.getvalue())
                self.assertEqual(result["rejection"]["code"], "explicit_conditional_mode_required")
                self.assertIs(result["analysis_completed"], False)


if __name__ == "__main__":
    unittest.main()
