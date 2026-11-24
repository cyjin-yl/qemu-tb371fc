#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise CI verdict handling with local synthetic subprocesses only."""
import json
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
import unittest

import ci_device_tests as ci


class VerdictTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tbci-unit-", dir="/tmp"))
        self.log = self.root / "stdout.log"

    def case(self, **kwargs):
        return ci.Case("fixture", [], self.root / "runtime", **kwargs)

    def test_true_json_and_expected_count(self):
        self.log.write_text(json.dumps({"passed": True, "checks": [1, 2]}))
        result = ci.judge(self.case(expected_count=2), self.log)
        self.assertEqual(result["check_count"], 2)

    def test_false_missing_and_nonboolean_verdict_rejected(self):
        for report in ({}, {"passed": False}, {"passed": 1}, {"passed": "true"}):
            with self.subTest(report=report):
                self.log.write_text(json.dumps(report))
                with self.assertRaises(ValueError):
                    ci.judge(self.case(), self.log)

    def test_partial_check_list_rejected(self):
        self.log.write_text(json.dumps({"passed": True, "checks": [1]}))
        with self.assertRaises(ValueError):
            ci.judge(self.case(expected_count=2), self.log)

    def test_required_evidence_field_rejected(self):
        self.log.write_text(json.dumps({"passed": True, "disk": False}))
        with self.assertRaises(ValueError):
            ci.judge(self.case(expected_values={"disk": True}), self.log)

    def test_secondary_list_evidence_rejects_missing_wrong_type_and_count(self):
        case = self.case(expected_lengths={"migrations": 1})
        for invalid in (None, 1, "completed", {}, [], [{}, {}]):
            self.log.write_text(json.dumps({"passed": True, "migrations": invalid}))
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                ci.judge(case, self.log)
        self.log.write_text(json.dumps({"passed": True, "migrations": [{}]}))
        self.assertEqual(ci.judge(case, self.log)["verified_lengths"],
                         {"migrations": 1})

    def test_tap_passes(self):
        self.log.write_text("TAP version 13\n1..2\nok 1 /one\nok 2 /two\n")
        result = ci.judge(self.case(result="tap", tap_count=2), self.log)
        self.assertEqual(result["tap_passed"], 2)

    def test_tap_skip_and_failure_rejected(self):
        for ending in ("ok 2 /two # SKIP unavailable", "not ok 2 /two"):
            self.log.write_text("1..2\nok 1 /one\n" + ending + "\n")
            with self.subTest(ending=ending), self.assertRaises(ValueError):
                ci.judge(self.case(result="tap", tap_count=2), self.log)

    def test_tap_invalid_plan_duplicate_and_todo_rejected(self):
        for text in ("1..3\nok 1 /one\nok 2 /two\n",
                     "1..2\nok 1 /one\nok 1 /one\n",
                     "ok 1 /one\nok 2 /two\n",
                     "1..2\nok 1 /one\nok 2 /two # TODO unfinished\n"):
            self.log.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                ci.judge(self.case(result="tap", tap_count=2), self.log)

    def test_required_boolean_and_nested_types_are_strict(self):
        for actual, expected in ((1, True), (False, 0),
                                 ({"populated": 0}, {"populated": False}),
                                 ([1], [True])):
            self.log.write_text(json.dumps({"passed": True, "field": actual}))
            with self.subTest(actual=actual), self.assertRaises(ValueError):
                ci.judge(self.case(expected_values={"field": expected}), self.log)

    def test_contradictory_individual_checks_rejected(self):
        for check in ({"passed": False}, {"success": 1}, {"all_passed": False},
                      {"status": "fail"}, {"status": "skip"}):
            self.log.write_text(json.dumps({"passed": True, "checks": [check]}))
            with self.subTest(check=check), self.assertRaises(ValueError):
                ci.judge(self.case(expected_count=1), self.log)

    def run_process(self, code, timeout=5):
        case = ci.Case("subprocess", [sys.executable, "-c", code],
                       self.root / "runtime", timeout=timeout)
        return ci.run_case(case, self.root / "artifacts", Path(sys.executable))

    def test_subprocess_logs_and_receipt_retained(self):
        report = self.run_process('print(\'{"passed": true}\')')
        self.assertTrue(report["passed"])
        self.assertTrue((self.root / "artifacts/case.json").is_file())
        self.assertIn("stdout.log", report["log_sha256"])

    def test_nonzero_exit_overrides_true_verdict(self):
        report = self.run_process(
            'import sys; print(\'{"passed": true}\'); sys.exit(7)')
        self.assertFalse(report["passed"])
        self.assertEqual(report["exit_code"], 7)

    def test_timeout_cannot_pass(self):
        report = self.run_process(
            'import time; print(\'{"passed": true}\', flush=True); time.sleep(10)',
            timeout=1)
        self.assertFalse(report["passed"])
        self.assertTrue(report["timed_out"])

    def test_fixture_copy_ignores_sockets_and_symlinks(self):
        source = self.root / "source"
        source.mkdir()
        (source / "regular").write_text("retained")
        (source / "link").symlink_to(self.root / "outside")
        control = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            control.bind(str(source / "socket"))
            ci.preserve_fixture(source, self.root / "copy")
        finally:
            control.close()
        self.assertEqual((self.root / "copy/regular").read_text(), "retained")
        self.assertEqual(sorted(p.name for p in (self.root / "copy").iterdir()),
                         ["regular"])


class PlanTests(unittest.TestCase):
    def test_spmi_route_requires_real_unintercepted_transfer(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = ci.plan(args, Path("/test/runtime"))
        self.assertEqual(len(cases), 63)
        matches = [case for case in cases if case.name == "spmi-pdc-gic-route"]
        self.assertEqual(len(matches), 1)
        case = matches[0]
        self.assertEqual(case.expected_count, 8)
        self.assertEqual(case.expected_lengths, {"migrations": 1})
        self.assertEqual(case.expected_values,
                         {"all_passed": True, "expected_checks": 8,
                          "output_interception_installed": False})
        self.assertIn(str(ci.ROOT / "tb371fc/tests/test_spmi_pdc_route.py"),
                      case.command)

    def test_ipcc_gate_requires_sixteen_real_checks(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        matches = [case for case in ci.plan(args, Path("/test/runtime"))
                   if case.name == "ipcc-messages"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].expected_count, 16)
        self.assertEqual(matches[0].verdict, "passed")

    def test_pdc_gates_require_eighteen_tests_and_ten_migration_checks(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = {case.name: case for case in ci.plan(args, Path("/test/runtime"))}
        self.assertEqual(cases["pdc-c-qtest"].tap_count, 18)
        self.assertEqual(cases["pdc-c-qtest"].result, "tap")
        self.assertEqual(cases["pdc-migration"].expected_count, 10)
        self.assertEqual(cases["pdc-migration"].verdict, "all_passed")

    def test_dcc_gate_requires_all_seventeen_real_checks(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        matches = [case for case in ci.plan(args, Path("/test/runtime"))
                   if case.name == "dcc-capture"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].expected_count, 17)
        self.assertEqual(matches[0].verdict, "passed")
        self.assertIn(str(ci.ROOT / "tb371fc/tests/test_dcc.py"),
                      matches[0].command)

    def test_linux_empty_ufs_gate_keeps_controller_and_omits_lun(self):
        args = SimpleNamespace(suite="linux", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        matches = [case for case in ci.plan(args, Path("/test/runtime"))
                   if case.name == "linux-console-empty-ufs"]
        self.assertEqual(len(matches), 1)
        case = matches[0]
        self.assertIn("--without-ufs-lu", case.command)
        self.assertNotIn("ufs-enabled=off", " ".join(case.command))
        self.assertEqual(case.expected_count, 8)
        self.assertEqual(case.expected_values["ufs_fixture"],
                         {"populated": False, "kind": "none", "bytes": 0})

    def test_linux_plans_require_rpmh_driver_and_host_evidence(self):
        for suite in ("linux", "all"):
            with self.subTest(suite=suite):
                args = SimpleNamespace(suite=suite, qemu=Path("/test/qemu"),
                                       build_dir=Path("/test/build"), kernel=None)
                matches = [case for case in ci.plan(args, Path("/test/runtime"))
                           if case.name == "linux-ufs-rpmh"]
                self.assertEqual(len(matches), 1)
                case = matches[0]
                self.assertIn("--require-rpmh", case.command)
                self.assertEqual(case.command[case.command.index("--machine") + 1],
                                 "tb371fc,rpmh-profile=reference")
                self.assertEqual(case.expected_count, 23)
                self.assertEqual(case.count_field, "guest_checks")
                self.assertEqual(case.expected_values,
                                 {"host_verified_disk_payload": True,
                                  "require_rpmh": True, "expected_guest_checks": 23})

    def test_counter_plan_accepts_empty_error_list_and_rejects_errors(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        case = next(case for case in ci.plan(args, Path("/test/runtime"))
                    if case.name == "counter-arithmetic")
        self.assertEqual(case.expected_values, {"cases": 1848, "errors": []})
        with tempfile.TemporaryDirectory(prefix="tbci-counter-", dir="/tmp") as d:
            receipt = Path(d) / "result.json"
            case.result = str(receipt)
            receipt.write_text(json.dumps({
                "passed": True, "cases": 1848, "errors": []}))
            ci.judge(case, receipt)
            receipt.write_text(json.dumps({
                "passed": True, "cases": 1848, "errors": [{"case": "mismatch"}]}))
            with self.assertRaises(ValueError):
                ci.judge(case, receipt)

    def test_component_plans_require_boot_resources_receipt(self):
        for suite in ("components", "all"):
            with self.subTest(suite=suite):
                args = SimpleNamespace(suite=suite, qemu=Path("/test/qemu"),
                                       build_dir=Path("/test/build"), kernel=None)
                matches = [case for case in ci.plan(args, Path("/test/runtime"))
                           if case.name == "boot-resources"]
                self.assertEqual(len(matches), 1)
                case = matches[0]
                self.assertIn(str(ci.ROOT / "tb371fc/tests/test_boot_resources.py"),
                              case.command)
                self.assertEqual(case.expected_count, 7)
                self.assertEqual(case.count_field, "checks")
                self.assertEqual(case.verdict, "passed")
                output = Path(case.command[case.command.index("--out") + 1])
                self.assertEqual(Path(case.result), output / "result.json")
                qemu = case.command[case.command.index("--qemu") + 1]
                self.assertEqual(qemu, str(args.qemu))


if __name__ == "__main__":
    unittest.main()
