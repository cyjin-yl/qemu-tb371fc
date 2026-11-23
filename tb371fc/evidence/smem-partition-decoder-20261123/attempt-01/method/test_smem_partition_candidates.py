#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DRAFT synthetic-only tests. Authored for review; not executed by the author."""

from dataclasses import replace
import contextlib
import hashlib
import io
import json
import struct
import unittest
from unittest.mock import patch

# Isolated Python (-I -B -S) does not add this directory to sys.path.
# Resolve only the known sibling; register it before exec for dataclasses.
import importlib.util
from pathlib import Path
import sys

_DECODER_PATH = Path(__file__).resolve().with_name("smem_partition_candidates.py")
_DECODER_NAME = "_tb371fc_smem_partition_candidates_fixture"
_SPEC = importlib.util.spec_from_file_location(_DECODER_NAME, _DECODER_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError("fixed sibling decoder could not be loaded")
decoder = importlib.util.module_from_spec(_SPEC)
sys.modules[_DECODER_NAME] = decoder
_SPEC.loader.exec_module(decoder)

BASE = 0x10000000
DATA_OFFSET = 0x100
SLOT_OFFSET = 0x120
TABLE_OFFSET = 0x200
TABLE_VA = BASE + 0x100


def entry32(size=4096, flags=0, host0=0, host1=1, cacheline=64,
            exclusions=(0, 0, 0, 0)):
    return struct.pack("<9I", size, flags, host0, host1, cacheline, *exclusions)


def entry16(size=4096, flags=0, host0=0, host1=1, cacheline=64,
            exclusions=(0, 0, 0, 0)):
    return struct.pack("<IIHHI4I", size, flags, host0, host1,
                       cacheline, *exclusions)


def short_terminator32():
    # The source can stop after host0; no host1 or trailing fields are present.
    return struct.pack("<III", 0, 0, 0xFFFF)


def _header(image, phnum=1):
    ident = b"\x7fELF" + bytes((2, 1, 1, 0)) + b"\0" * 8
    struct.pack_into("<16sHHIQQQIHHHHHH", image, 0,
                     ident, 2, 183, 1, 0, 64, 0, 0, 64, 56, phnum,
                     0, 0, 0)


def fixture(payload, *, flags=4, memsz_extra=0):
    image = bytearray(TABLE_OFFSET + len(payload))
    _header(image)
    filesz = len(image) - DATA_OFFSET
    struct.pack_into("<IIQQQQQQ", image, 64,
                     1, flags, DATA_OFFSET, BASE, BASE,
                     filesz, filesz + memsz_extra, 1)
    struct.pack_into("<Q", image, SLOT_OFFSET, TABLE_VA)
    image[TABLE_OFFSET:] = payload
    image = bytes(image)
    pin = decoder.PointerPin(
        image_sha256=hashlib.sha256(image).hexdigest(),
        dal_report_sha256="a" * 64,
        property_index=7,
        pointer_slot_va=BASE + SLOT_OFFSET - DATA_OFFSET,
        pointer_slot_file_offset=SLOT_OFFSET,
        pointer_slot_load_index=0,
        target_va=TABLE_VA,
        target_file_offset=TABLE_OFFSET,
        target_load_index=0,
    )
    return image, pin


def two_load_fixture(payload, *, slot_flags=4, target_flags=4):
    image = bytearray(TABLE_OFFSET + len(payload))
    _header(image, 2)
    target = BASE + 0x1000
    struct.pack_into("<IIQQQQQQ", image, 64,
                     1, slot_flags, DATA_OFFSET, BASE, BASE,
                     0x80, 0x80, 1)
    struct.pack_into("<IIQQQQQQ", image, 120,
                     1, target_flags, TABLE_OFFSET, target, target,
                     len(payload), len(payload), 1)
    struct.pack_into("<Q", image, SLOT_OFFSET, target)
    image[TABLE_OFFSET:] = payload
    image = bytes(image)
    pin = decoder.PointerPin(
        hashlib.sha256(image).hexdigest(), "b" * 64, 7,
        BASE + 0x20, SLOT_OFFSET, 0,
        target, TABLE_OFFSET, 1)
    return image, pin


def resign(image, pin, **changes):
    image = bytes(image)
    return image, replace(pin, image_sha256=hashlib.sha256(image).hexdigest(),
                          **changes)


def candidate(report, name="enum32_stride36"):
    return next(item for item in report["candidates"] if item["layout"] == name)


class CandidateDraftTests(unittest.TestCase):
    def assert_not_handoff(self, report):
        for name in ("platform_static_selection_proven", "entry_reachable",
                     "smem_initialized", "handoff_generated", "abi_confirmed"):
            self.assertIs(report[name], False)
        self.assertIsNone(report["selected_layout"])
        self.assertIs(report["array_total_length_known"], False)

    def assert_rejected(self, report, code):
        self.assertEqual(report["status"], "rejected")
        self.assertEqual(report["rejection"]["code"], code)
        self.assert_not_handoff(report)

    def test_enum32_offsets_and_counts(self):
        image, pin = fixture(
            entry32(size=8192, cacheline=128, exclusions=(1, 2, 3, 4)) +
            short_terminator32())
        report = decoder.analyze_bytes(image, pin)
        item = candidate(report)
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(item["stride"], 36)
        self.assertEqual(item["entries"][0]["size_cacheline"], 128)
        self.assertEqual(item["entries"][0]["exclusion_sizes"], [1, 2, 3, 4])
        self.assertEqual(item["reserved_bytes_contribution"], 8192)
        self.assertEqual(item["toc_entries_contribution"], 1)
        self.assert_not_handoff(report)

    def test_enum16_is_retained_as_counterhypothesis(self):
        image, pin = fixture(entry16(cacheline=32) +
                             entry16(size=0, host0=0xFFFF, cacheline=0))
        report = decoder.analyze_bytes(image, pin)
        item = candidate(report, "enum16_stride32")
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(item["stride"], 32)
        self.assertEqual(item["entries"][0]["host1"], 1)
        self.assertEqual(item["entries"][0]["size_cacheline"], 32)
        self.assertEqual(item["abi_assumption"],
                         "counterhypothesis_not_ordinary_aapcs64")
        self.assert_not_handoff(report)

    def test_host0_sentinel_short_circuits_at_file_end(self):
        image, pin = fixture(short_terminator32())
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(item["field_bytes_read"], 4)
        self.assertIsNone(item["terminator"]["host1"])
        self.assertEqual(item["terminator"]["stop_field"], "host0")

    def test_host1_sentinel_alone_terminates(self):
        image, pin = fixture(struct.pack("<4I", 123, 456, 7, 0xFFFF))
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(item["field_bytes_read"], 8)
        self.assertEqual(item["terminator"]["host0"], 7)
        self.assertEqual(item["terminator"]["stop_field"], "host1")
        self.assertEqual(item["entries"], [])

    def test_zero_size_skips_unused_fields(self):
        image, pin = fixture(entry32(size=0, flags=0xFFFFFFFF,
                                     cacheline=0xDEADBEEF) +
                             short_terminator32())
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(item["field_bytes_read"], 16)
        self.assertIsNone(item["entries"][0]["flags"])
        self.assertIsNone(item["entries"][0]["size_cacheline"])
        self.assertIsNone(item["entries"][0]["exclusion_sizes"])
        self.assertEqual(item["reserved_bytes_contribution"], 0)
        self.assertEqual(item["toc_entries_contribution"], 0)

    def test_ignore_reserves_space_without_toc_or_unused_reads(self):
        image, pin = fixture(entry32(size=8192, flags=4,
                                     cacheline=0xDEADBEEF,
                                     exclusions=(0xFFFFFFFF,) * 4) +
                             short_terminator32())
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(item["field_bytes_read"], 20)
        self.assertEqual(item["reserved_bytes_contribution"], 8192)
        self.assertEqual(item["toc_entries_contribution"], 0)
        self.assertEqual(item["entries"][0]["disposition"],
                         "ignored_space_reserved")
        self.assertIsNone(item["entries"][0]["exclusion_sizes"])

    def test_mixed_active_zero_and_ignore_contributions(self):
        image, pin = fixture(entry32(size=4096) +
                             entry32(size=0) +
                             entry32(size=8192, flags=4) +
                             short_terminator32())
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "terminated")
        self.assertEqual(len(item["entries"]), 3)
        self.assertEqual(item["reserved_bytes_contribution"], 12288)
        self.assertEqual(item["toc_entries_contribution"], 1)

    def test_record_cap_is_incomplete_not_success(self):
        image, pin = fixture(entry32(size=0) * 3)
        report = decoder.analyze_bytes(image, pin, record_cap=2)
        item = candidate(report)
        self.assertEqual(item["status"], "bounded_without_terminator")
        self.assertIs(item["source_iteration_terminated"], False)
        self.assertEqual(item["rejection"]["code"],
                         "record_cap_without_terminator")
        self.assert_not_handoff(report)

    def test_record_truncation_is_rejected_without_zero_fill(self):
        image, pin = fixture(entry32()[:-1])
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "rejected")
        self.assertEqual(item["rejection"]["code"], "read_crosses_load_boundary")
        self.assertIs(item["source_iteration_terminated"], False)

    def test_missing_terminator_does_not_read_bss(self):
        image, pin = fixture(entry32(), memsz_extra=64)
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["status"], "rejected")
        self.assertEqual(item["rejection"]["code"],
                         "read_enters_unbacked_load_tail")

    def test_target_in_bss_is_rejected_before_field_decode(self):
        image, pin = fixture(b"", memsz_extra=64)
        self.assert_rejected(decoder.analyze_bytes(image, pin),
                             "read_enters_unbacked_load_tail")

    def test_executable_pointer_slot_load_is_rejected(self):
        image, pin = two_load_fixture(short_terminator32(), slot_flags=5)
        self.assert_rejected(decoder.analyze_bytes(image, pin),
                             "executable_load_rejected")

    def test_executable_target_load_is_rejected(self):
        image, pin = two_load_fixture(short_terminator32(), target_flags=5)
        self.assert_rejected(decoder.analyze_bytes(image, pin),
                             "executable_load_rejected")

    def test_overlapping_loads_are_ambiguous(self):
        image, pin = fixture(short_terminator32())
        changed = bytearray(image)
        struct.pack_into("<H", changed, 56, 2)
        changed[120:176] = changed[64:120]
        image, pin = resign(changed, pin)
        self.assert_rejected(decoder.analyze_bytes(image, pin),
                             "ambiguous_load_mapping")

    def test_slot_value_must_match_pinned_target(self):
        image, pin = fixture(short_terminator32())
        changed = bytearray(image)
        struct.pack_into("<Q", changed, SLOT_OFFSET, TABLE_VA + 4)
        image, pin = resign(changed, pin)
        self.assert_rejected(decoder.analyze_bytes(image, pin),
                             "pointer_slot_value_mismatch")

    def test_whole_image_digest_is_required(self):
        image, pin = fixture(short_terminator32())
        changed = bytearray(image)
        changed[-1] ^= 1
        self.assert_rejected(decoder.analyze_bytes(bytes(changed), pin),
                             "image_sha256_mismatch")

    def test_pin_file_offsets_must_match_elf_geometry(self):
        image, pin = fixture(short_terminator32())
        for field, code in (
                ("pointer_slot_file_offset", "pointer_slot_file_offset_mismatch"),
                ("target_file_offset", "target_file_offset_mismatch")):
            with self.subTest(field=field):
                bad = replace(pin, **{field: getattr(pin, field) + 1})
                self.assert_rejected(decoder.analyze_bytes(image, bad), code)

    def test_unaligned_and_boolean_pin_fields_are_rejected(self):
        image, pin = fixture(short_terminator32())
        variations = (
            (replace(pin, pointer_slot_va=pin.pointer_slot_va + 1),
             "pointer_slot_not_8byte_aligned"),
            (replace(pin, target_va=pin.target_va + 1),
             "configuration_pointer_not_4byte_aligned"),
            (replace(pin, property_index=True), "invalid_pin_index"),
        )
        for bad, code in variations:
            with self.subTest(code=code):
                self.assert_rejected(decoder.analyze_bytes(image, bad), code)

    def test_malformed_elf_header_and_load_bounds(self):
        image, pin = fixture(short_terminator32())
        cases = []
        wrong_class = bytearray(image)
        wrong_class[4] = 1
        cases.append((wrong_class, "elf64_little_endian_version1_required"))
        truncated_ph = bytearray(image)
        struct.pack_into("<Q", truncated_ph, 32, len(image) - 1)
        cases.append((truncated_ph, "program_headers_outside_file"))
        bad_size = bytearray(image)
        struct.pack_into("<Q", bad_size, 64 + 40, 0)
        cases.append((bad_size, "load_filesz_exceeds_memsz"))
        for changed, code in cases:
            with self.subTest(code=code):
                changed, updated = resign(changed, pin)
                self.assert_rejected(decoder.analyze_bytes(changed, updated), code)

    def test_two_empty_terminated_candidates_remain_ambiguous(self):
        image, pin = fixture(short_terminator32())
        report = decoder.analyze_bytes(image, pin)
        self.assertEqual(report["terminated_candidate_count"], 2)
        self.assertEqual(report["ambiguity"], "multiple_terminated_candidates")
        self.assert_not_handoff(report)

    def test_two_nonempty_candidates_remain_ambiguous(self):
        payload = bytearray(48)
        struct.pack_into("<I", payload, 12, 1)
        struct.pack_into("<H", payload, 40, 0xFFFF)
        struct.pack_into("<I", payload, 44, 0xFFFF)
        image, pin = fixture(bytes(payload))
        report = decoder.analyze_bytes(image, pin)
        self.assertEqual(report["terminated_candidate_count"], 2)
        self.assertTrue(all(len(c["entries"]) == 1
                            for c in report["candidates"]))
        self.assert_not_handoff(report)

    def test_single_terminated_candidate_does_not_prove_abi(self):
        image, pin = fixture(entry32() + short_terminator32())
        report = decoder.analyze_bytes(image, pin)
        self.assertEqual(report["terminated_candidate_count"], 1)
        self.assertEqual(report["ambiguity"],
                         "one_terminated_candidate_not_selected")
        self.assert_not_handoff(report)

    def test_ffffffff_is_not_the_source_sentinel(self):
        image, pin = fixture(entry32(host0=0xFFFFFFFF) +
                             short_terminator32())
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(len(item["entries"]), 1)
        self.assertEqual(item["terminator"]["record_index"], 1)
        self.assertIn("host_value_would_narrow_in_toc_u16",
                      [a["code"] for a in item["anomalies"]])

    def test_arithmetic_and_unknown_flags_are_reported_not_selected(self):
        image, pin = fixture(entry32(size=0xFFFFFFFF, flags=0x84) +
                             entry32(size=1, flags=4) +
                             short_terminator32())
        report = decoder.analyze_bytes(image, pin)
        item = candidate(report)
        self.assertEqual(item["reserved_bytes_contribution"], 1 << 32)
        codes = [a["code"] for a in item["anomalies"]]
        self.assertIn("flags_outside_fixed_source_named_mask", codes)
        self.assertIn("reservation_contribution_exceeds_uint32", codes)
        self.assert_not_handoff(report)

    def test_exclusions_larger_than_partition_are_not_normalized(self):
        image, pin = fixture(entry32(size=16, exclusions=(17, 0, 0, 0)) +
                             short_terminator32())
        item = candidate(decoder.analyze_bytes(image, pin))
        self.assertEqual(item["entries"][0]["exclusion_sizes"], [17, 0, 0, 0])
        self.assertIn("exclusions_exceed_partition_size",
                      [a["code"] for a in item["anomalies"]])

    def test_no_raw_payload_strings_are_emitted(self):
        marker = b"PRIVATE_FIXTURE_PAYLOAD"
        image, pin = fixture(short_terminator32() + marker)
        serialized = json.dumps(decoder.analyze_bytes(image, pin))
        self.assertNotIn(marker.decode("ascii"), serialized)
        self.assertNotIn(marker.hex(), serialized)

    def test_invalid_analysis_options_are_rejected(self):
        image, pin = fixture(short_terminator32())
        for cap in (0, True, 129):
            with self.subTest(cap=cap):
                self.assert_rejected(
                    decoder.analyze_bytes(image, pin, record_cap=cap),
                    "record_cap_outside_analysis_bound")

    def test_cli_refuses_without_opening_input_paths(self):
        output = io.StringIO()
        with patch.object(decoder, "NATIVE_PIN", None):
            with patch("builtins.open", side_effect=RuntimeError("unexpected I/O")):
                with contextlib.redirect_stdout(output):
                    code = decoder.main(["--image", "synthetic-do-not-open",
                                         "--dal-report", "synthetic-do-not-open"])
        self.assertEqual(code, 2)
        report = json.loads(output.getvalue())
        self.assertEqual(report["rejection"]["code"],
                         "native_pointer_pin_unavailable")
        self.assert_not_handoff(report)


if __name__ == "__main__":
    unittest.main()
