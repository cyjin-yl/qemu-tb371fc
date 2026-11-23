#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Self-authored inputs only; no device or firmware fixture access."""
import importlib.util
import io
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

FILE = Path(__file__).resolve().with_name("observe_xbl_accessor.py")
SPEC = importlib.util.spec_from_file_location("_accessor_fixture_module", FILE)
if SPEC is None or SPEC.loader is None:
    raise ImportError("fixed sibling observer missing")
m = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = m
SPEC.loader.exec_module(m)

def lines(*instructions):
    return [(i * 4, *instruction) for i, instruction in enumerate(instructions)]

def mapped(target, size):
    if not (0 <= target < 0x100000 and size == 4):
        raise m.Reject("synthetic_mapping")
    return {"va": target, "size": size, "file_offset": target, "load_index": 0}

def observe(instructions, refs=None):
    return m.observe_rows(instructions, 0, len(instructions) * 4,
                          {0x1000: "smem_vers"} if refs is None else refs, mapped)

def call_rows():
    return lines(("adr", "x1, 0x1000"), ("mov", "x0, sp"),
                 ("mov", "w2, #0"), ("add", "x3, sp, #16"),
                 ("bl", "0x80"))

def fake_elf(overlap=False):
    raw = bytearray(0x200)
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    count = 2 if overlap else 1
    struct.pack_into("<16sHHIQQQIHHHHHH", raw, 0, ident, 2, 183, 1,
                     0x1000, 64, 0, 0, 64, 56, count, 0, 0, 0)
    for i in range(count):
        struct.pack_into("<IIQQQQQQ", raw, 64 + i * 56,
                         1, 5, 0x100, 0x1000, 0x1000, 0x80, 0x100, 4)
    return bytes(raw)

class AccessorTests(unittest.TestCase):
    def test_gnu_and_llvm_address_formats_have_exact_slot_coverage(self):
        text = "00000000 <fixture>:\n  0: adr x1, 0x1000\n  4: bl 80 <target>\n"
        rows = m.rows_from_text(text, 0, 8)
        self.assertEqual([r[0] for r in rows], [0, 4])
        self.assertEqual(m.number("80 <target>", address=True), 0x80)
        self.assertEqual(m.number("#16"), 16)
        self.assertEqual(m.number("#0x10"), 16)

    def test_missing_duplicate_and_out_of_range_rows_reject(self):
        for text in ("0: nop\n", "0: nop\n0: nop\n", "0: nop\n8: nop\n"):
            with self.subTest(text=text), self.assertRaises(m.Reject):
                m.rows_from_text(text, 0, 8)

    def test_exact_reference_arguments_qualify_only_real_direct_call(self):
        result = observe(call_rows())
        call = result["call_candidates"][0]
        self.assertTrue(call["eligible_for_follow"])
        self.assertEqual(call["target_va"], 0x80)
        self.assertEqual(call["name_reference"], "smem_vers")
        self.assertEqual(call["argument_witness_pcs"], [0, 4, 8, 12])
        self.assertFalse(call["native_function_identity_proven"])

    def test_wrong_reference_register_and_wrong_api_argument_do_not_qualify(self):
        wrong_pair = lines(("adrp", "x4, 0x1000"), ("add", "x1, x5, #0"),
                           ("mov", "x0, sp"), ("mov", "w2, #0"),
                           ("add", "x3, sp, #16"), ("bl", "0x80"))
        wrong_role = lines(("adr", "x0, 0x1000"), ("add", "x1, sp, #16"), ("bl", "0x80"))
        for rows in (wrong_pair, wrong_role):
            with self.subTest(rows=rows):
                self.assertFalse(observe(rows)["call_candidates"][0]["eligible_for_follow"])

    def test_clobber_and_unknown_instruction_invalidate_old_reference(self):
        for op in (("mov", "x1, xzr"), (".inst", "0xffffffff")):
            rows = call_rows()[:-1] + [(16, *op), (20, "bl", "0x80")]
            self.assertFalse(observe(rows)["call_candidates"][0]["eligible_for_follow"])

    def test_conditional_edge_is_a_frontier_and_does_not_preserve_provenance(self):
        rows = call_rows()[:-1] + [(16, "b.eq", "0x40"), (20, "bl", "0x80")]
        result = observe(rows)
        self.assertFalse(result["call_candidates"][0]["eligible_for_follow"])
        self.assertEqual(result["edges"][0]["fallthrough_va"], 20)
        self.assertFalse(result["edges"][0]["runtime_taken_proven"])
        self.assertTrue(any(f.get("target_va") == 0x40 for f in result["frontiers"]))

    def test_plain_and_sized_pointer_loads_preserve_same_base_and_index(self):
        rows = lines(("ldr", "w2, [x3]"), ("ldr", "x1, [x0]"),
                     ("ldr", "x4, [x1, x2, lsl #3]"),
                     ("add", "x5, x1, x2, lsl #4"),
                     ("ldr", "x6, [x5, #8]"), ("ldr", "w7, [x5]"))
        facts = {f["pc"]: f for f in observe(rows)["facts"]}
        plain, sized = facts[8], facts[16]
        self.assertEqual(plain["access_bytes"], 8)
        self.assertEqual(sized["access_bytes"], 8)
        self.assertEqual(sorted(t["scale"] for t in plain["address"]["terms"]), [1, 8])
        self.assertEqual(sorted(t["scale"] for t in sized["address"]["terms"]), [1, 16])
        self.assertEqual({t["source"]["pc"] for t in plain["address"]["terms"]}, {0, 4})
        self.assertEqual({t["source"]["pc"] for t in sized["address"]["terms"]}, {0, 4})
        self.assertEqual(sized["address"]["displacement"], 8)
        self.assertEqual(facts[20]["access_bytes"], 4)

    def test_redefined_index_is_not_reported_as_the_old_payload_index(self):
        rows = lines(("ldr", "w2, [x3]"), ("ldr", "x1, [x0]"),
                     ("mov", "x2, xzr"), ("ldr", "x4, [x1, x2, lsl #3]"))
        address = observe(rows)["facts"][-1]["address"]
        self.assertEqual(len(address["terms"]), 1)
        self.assertEqual(address["terms"][0]["source"]["pc"], 4)
        self.assertEqual(address["terms"][0]["scale"], 1)

    def test_memory_width_is_separate_from_logical_halfword_width(self):
        rows = lines(("ldr", "w8, [x9, #8]"), ("uxth", "w10, w8"),
                     ("lsr", "w11, w8, #16"), ("ldrh", "w12, [x9, #10]"))
        facts = {f["pc"]: f for f in observe(rows)["facts"]}
        self.assertEqual(facts[0]["access_bytes"], 4)
        self.assertEqual((facts[4]["first_bit"], facts[4]["logical_bits"]), (0, 16))
        self.assertEqual((facts[8]["first_bit"], facts[8]["logical_bits"]), (16, 16))
        self.assertEqual(facts[12]["access_bytes"], 2)
        self.assertEqual(facts[12]["address"]["displacement"], 10)

    def test_cursor_steps_remain_unassigned_to_source_or_toc(self):
        rows = lines(("add", "x12, x12, #36"), ("add", "x13, x13, #32"),
                     ("add", "x14, x14, #48"))
        result = observe(rows)
        self.assertEqual([f["step"] for f in result["facts"]], [36, 32, 48])
        self.assertEqual(len({f["before"]["terms"][0]["source"]["register"]
                              for f in result["facts"]}), 3)
        self.assertTrue(all(f["source_or_destination_role"] == "unresolved" for f in result["facts"]))
        self.assertFalse(result["native_configuration_abi_selected"])

    def test_executable_file_mapping_rejects_bss_overlap_and_alignment(self):
        view = m.ELF(fake_elf())
        self.assertEqual(view.code(0x1000, 4)[1]["file_offset"], 0x100)
        for va, size in ((0x107c, 8), (0x1080, 4), (0x1001, 4)):
            with self.subTest(va=va, size=size), self.assertRaises(m.Reject):
                view.code(va, size)
        with self.assertRaises(m.Reject):
            m.ELF(fake_elf(True)).code(0x1000, 4)

    def test_follow_uses_qualified_observed_targets_with_dedup_and_two_cap(self):
        calls = []
        for i, target in enumerate((0x80, 0x80, 0x90, 0xa0)):
            call = observe(call_rows())["call_candidates"][0].copy()
            call.update(pc=i * 4, target_va=target)
            calls.append(call)
        calls.append({"eligible_for_follow": False, "target_va": 0xb0})
        selected = m.follow_candidates([{"call_candidates": calls}], set())
        self.assertEqual([x["target_va"] for x in selected], [0x80, 0x90])
        self.assertEqual([x["target_va"] for x in
                          m.follow_candidates([{"call_candidates": calls}], {0x80})], [0x90, 0xa0])

    def test_output_has_no_operands_and_truncation_never_claims_complete_witness(self):
        rows = lines(("mov", "x0, #0xdeadbeef"), ("ldr", "x1, [x0]"))
        result = observe(rows)
        self.assertTrue(result["facts"][0]["address"]["constant_omitted"])
        report = {"observations": [result], "complete_witnesses_exported": True,
                  "native_layout_selected": False}
        result["facts"] = result["facts"] * 100
        result["fact_count"] = 100
        raw = m.bounded_json(report)
        self.assertLessEqual(len(raw), 12288)
        public = json.loads(raw)
        self.assertFalse(public["complete_witnesses_exported"])
        self.assertFalse(public["native_layout_selected"])
        self.assertTrue(public["public_details_truncated"])
        self.assertGreater(public["details_omitted"], 0)
        self.assertNotIn(b"deadbeef", raw)
        self.assertNotIn(b"3735928559", raw)
        self.assertNotIn(b"operands", raw)
        self.assertNotIn(b"mnemonic", raw)

    def test_missing_tool_pin_refuses_before_any_file_or_process_access(self):
        stdout = SimpleNamespace(buffer=io.BytesIO())
        with patch.object(m, "TOOLCHAIN_PIN", None), patch.object(m.sys, "stdout", stdout):
            with patch.object(m.os, "open", side_effect=RuntimeError("forbidden file read")):
                with patch.object(m.subprocess, "Popen", side_effect=RuntimeError("forbidden process")):
                    rc = m.main(["--observe-fixed"])
        self.assertEqual(rc, 2)
        report = json.loads(stdout.buffer.getvalue())
        self.assertEqual(report["error_code"], "toolchain_pin_unavailable")
        self.assertFalse(report["firmware_input_read"])
        self.assertFalse(report["analysis_completed"])


    def test_low32_identity_is_stable_only_until_register_redefinition(self):
        rows = lines(("ldr", "x2, [x3]"), ("ldr", "x1, [x0]"),
                     ("ldr", "x4, [x1, w2, uxtw #3]"),
                     ("ldr", "x6, [x1, w2, uxtw #3]"),
                     ("ldr", "x2, [x5]"), ("ldr", "x7, [x1, w2, uxtw #3]"))
        facts = {f["pc"]: f for f in observe(rows)["facts"]}
        def index_at(pc):
            return next(t["source"] for t in facts[pc]["address"]["terms"]
                        if t["source"]["kind"] == "unresolved_low32_input")
        first, repeated, redefined = (index_at(pc) for pc in (8, 12, 20))
        self.assertEqual(first, repeated)
        self.assertNotEqual(first["source_id"], redefined["source_id"])
        self.assertNotEqual(first["definition_generation"], redefined["definition_generation"])
        self.assertEqual(first["input_source_ids"], ["load_0"])
        self.assertEqual(redefined["input_source_ids"], ["load_10"])
        self.assertEqual(first["input_witness_pcs"], [0])
        self.assertEqual(redefined["input_witness_pcs"], [16])

    def test_read_address_provenance_is_recursive_and_not_pointer_validity(self):
        for base, complete in (("x17", False), ("sp", True)):
            rows = lines(("ldr", "x4, [%s]" % base), ("ldr", "x0, [x4, #8]"),
                         ("adr", "x1, 0x1000"), ("mov", "w2, #0"),
                         ("add", "x3, sp, #16"), ("bl", "0x80"))
            result = observe(rows)
            call = result["call_candidates"][0]
            with self.subTest(base=base):
                self.assertEqual(call["eligible_for_follow"], complete)
                self.assertEqual(call["argument_witness_complete"], complete)
                self.assertEqual(call["other_argument_sources_known"], complete)
                self.assertFalse(call["argument_pointer_validity_proven"])
                first_read = result["facts"][1]["address"]["terms"][0]["source"]
                self.assertEqual(first_read["address_provenance_complete"], complete)
                self.assertFalse(first_read["loaded_pointer_validity_proven"])
                self.assertEqual(first_read["pc"], 0)
                self.assertIn(0, call["argument_witness_pcs"])
                self.assertIn(4, call["argument_witness_pcs"])

    def test_known_forward_and_backward_join_reset_textual_predecessor(self):
        forward = lines(("b.eq", "0x14"), ("adr", "x1, 0x1000"),
                        ("mov", "x0, sp"), ("mov", "w2, #0"),
                        ("add", "x3, sp, #16"), ("bl", "0x80"))
        backward = call_rows() + [(20, "b", "0x10")]
        for rows, target in ((forward, 20), (backward, 16)):
            with self.subTest(target=target):
                result = observe(rows)
                self.assertFalse(result["call_candidates"][0]["eligible_for_follow"])
                entries = [f for f in result["frontiers"]
                           if f["kind"] == "known_control_target_entry"]
                self.assertTrue(any(f["pc"] == target for f in entries))
                self.assertTrue(all(not f["all_predecessors_known"] for f in entries))

    def test_arguments_reestablished_after_known_join_can_qualify_locally(self):
        rows = lines(("b.eq", "0x8"), ("nop", ""),
                     ("adr", "x1, 0x1000"), ("mov", "x0, sp"),
                     ("mov", "w2, #0"), ("add", "x3, sp, #16"), ("bl", "0x80"))
        result = observe(rows)
        call = result["call_candidates"][0]
        self.assertTrue(call["eligible_for_follow"])
        self.assertEqual(call["argument_witness_pcs"], [8, 12, 16, 20])
        self.assertFalse(call["argument_pointer_validity_proven"])
        self.assertFalse(result["entry_reachability_proven"])

if __name__ == "__main__":
    unittest.main()
