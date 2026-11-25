#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise CI verdict handling with local synthetic subprocesses only."""
import hashlib
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
        self.log.write_text(json.dumps({"passed": True, "checks": [
            {"name": "one", "passed": True},
            {"test": "two", "status": "pass"}]}))
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

    def test_contradictory_summary_verdicts_rejected(self):
        for field in ("success", "all_passed"):
            for value in (False, 1, "true"):
                self.log.write_text(json.dumps({"passed": True, field: value}))
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    ci.judge(self.case(), self.log)

    def test_placeholder_and_unattested_checks_rejected(self):
        for value in (None, True, 1, [], {}, {"name": "one"},
                      {"passed": True}, "PASS", "TB371FC_CHECK FAIL one"):
            self.log.write_text(json.dumps({"passed": True, "checks": [value]}))
            with self.subTest(value=value), self.assertRaises(ValueError):
                ci.judge(self.case(expected_count=1), self.log)

    def test_duplicate_check_names_rejected(self):
        self.log.write_text(json.dumps({"passed": True, "checks": [
            {"name": "same", "passed": True},
            {"test": "same", "status": "pass"}]}))
        with self.assertRaises(ValueError):
            ci.judge(self.case(expected_count=2), self.log)

    def test_guest_check_receipt_formats(self):
        for checks in (["TB371FC_CHECK PASS one", "TB371FC_CHECK PASS two"],
                       [["PASS", "one"], ["PASS", "two"]]):
            self.log.write_text(json.dumps({"passed": True, "guest_checks": checks}))
            result = ci.judge(self.case(expected_count=2, count_field="guest_checks"),
                              self.log)
            self.assertEqual(result["check_count"], 2)

    def test_failed_or_malformed_guest_checks_rejected(self):
        for check in (["FAIL", "one"], ["PASS"], ["PASS", "one", "extra"],
                      ["PASS", ""], "TB371FC_CHECK FAIL one",
                      "TB371FC_CHECK PASS one trailing", "arbitrary text"):
            self.log.write_text(json.dumps({"passed": True, "guest_checks": [check]}))
            with self.subTest(check=check), self.assertRaises(ValueError):
                ci.judge(self.case(expected_count=1, count_field="guest_checks"),
                         self.log)

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
    def test_integrated_plan_keeps_both_tbu_contracts_and_rpmh_v3(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        plan = ci.plan(args, Path("/test/runtime"))
        cases = {case.name: case for case in plan}
        self.assertEqual(len(plan), 95)
        self.assertEqual(len(cases), len(plan))
        self.assertEqual(cases["tbu-reference-ecats"].expected_count, 17)
        self.assertEqual(cases["smmuv500-reference"].expected_count, 36)
        self.assertIn("gpu-cx-gdsc", cases)
        self.assertIn("gpu-gx-gdsc", cases)
        self.assertNotIn("gpu-gdsc", cases)
        for name in ("ufs-crypto-rejection", "ufs-auto-hibern8",
                     "power-checkpoint", "camera-gdsc-reference",
                     "mdss-gdsc-reference", "npu-gdsc-reference",
                     "video-gdsc-reference", "camcc-sm8250-reference",
                     "camcc-source-generation", "dispcc-sm8250-reference",
                     "dispcc-source-generation", "videocc-sm8250-reference",
                     "videocc-source-generation", "dsi-pll-source-generation",
                     "dsi-pll-7nm-reference", "dp-pll-source-generation",
                     "dp-pll-7nm-reference", "llcc-source-generation",
                     "llcc-sm8250-reference", "spss-irq-mask-source-generation",
                     "spss-irq-mask-sm8250-reference"):
            self.assertIn(name, cases)
        npu = cases["npu-gdsc-reference"]
        self.assertEqual(npu.expected_count, 21)
        self.assertEqual(npu.expected_values["parent_resource_index"], 0xffffffff)
        self.assertIs(npu.expected_values["parent_supply_declared"], False)
        self.assertIs(npu.expected_values["parent_power_connected"], False)
        self.assertEqual(npu.expected_values["hardware_mode_domain_mask"], 0)
        self.assertEqual(npu.expected_lengths,
                         {"migrations": 3, "rejected_migrations": 2,
                          "corrupted_stream_rejections": 2,
                          "migration_sources": 4, "actual_stream_files": 6})
        video = cases["video-gdsc-reference"]
        self.assertEqual(video.expected_count, 22)
        self.assertEqual(video.expected_values["vmstate_payload_bytes"], 142)
        self.assertEqual(video.expected_values["domain_dt_flags"], [1, 1, 3, 3])
        self.assertEqual(video.expected_values["hardware_mode_domain_mask"], 12)
        self.assertEqual(video.expected_values["fanout_consumer_count"], 3)
        self.assertIs(video.expected_values["npu_parent_power_connected"], False)
        self.assertEqual(video.expected_values["gcc_video_ahb_write_mask"], 0x6001)
        self.assertEqual(video.expected_lengths, npu.expected_lengths)
        camcc = cases["camcc-sm8250-reference"]
        self.assertEqual(camcc.expected_count, 41)
        self.assertEqual(camcc.expected_values["vmstate_payload_bytes"], 2579)
        self.assertEqual(camcc.expected_values["register_count"], 236)
        self.assertEqual(camcc.expected_values["sparse_window_count"], 74)
        self.assertEqual(camcc.expected_values["lucid_pll_count"], 4)
        self.assertEqual(camcc.expected_values["zonda_pll_count"], 1)
        self.assertEqual(camcc.expected_values["unknown_external_input_count"], 3)
        self.assertIs(camcc.expected_values["reference_enabled_by_default"], False)
        self.assertIs(camcc.expected_values["sbi_divider_rounds_up"], True)
        self.assertEqual(camcc.expected_lengths, npu.expected_lengths)
        self.assertEqual(cases["camcc-source-generation"].expected_lengths,
                         {"inputs": 8})
        dispcc = cases["dispcc-sm8250-reference"]
        self.assertEqual(dispcc.expected_count, 42)
        self.assertEqual(dispcc.expected_values["vmstate_payload_bytes"], 1676)
        self.assertEqual(dispcc.expected_values["register_count"], 130)
        self.assertEqual(dispcc.expected_values["sparse_window_count"], 39)
        self.assertEqual(dispcc.expected_values["lucid_pll_count"], 2)
        self.assertEqual(dispcc.expected_values["mnd_width8_count"], 2)
        self.assertEqual(dispcc.expected_values["mnd_width16_count"], 4)
        self.assertEqual(dispcc.expected_values["unknown_external_input_count"], 16)
        self.assertEqual(dispcc.expected_values["remote_vote_count"], 2)
        self.assertEqual(dispcc.expected_values["writable_divider_count"], 2)
        self.assertEqual(dispcc.expected_values["read_only_divider_count"], 3)
        self.assertIs(dispcc.expected_values["generic_dividers_round_up"], True)
        self.assertIs(dispcc.expected_values["reference_enabled_by_default"], False)
        self.assertEqual(dispcc.expected_lengths,
                         {"migrations": 3, "rejected_migrations": 2,
                          "corrupted_stream_rejections": 3,
                          "migration_sources": 4, "actual_stream_files": 7})
        self.assertEqual(cases["dispcc-source-generation"].expected_lengths,
                         {"inputs": 9})
        videocc = cases["videocc-sm8250-reference"]
        self.assertEqual(videocc.expected_count, 41)
        self.assertEqual(videocc.expected_values["vmstate_payload_bytes"], 474)
        self.assertEqual(videocc.expected_values["register_count"], 55)
        self.assertEqual(videocc.expected_values["sparse_window_count"], 26)
        self.assertEqual(videocc.expected_values["lucid_pll_count"], 2)
        self.assertEqual(videocc.expected_values["unknown_external_input_count"], 2)
        self.assertEqual(videocc.expected_values["writable_divider_count"], 0)
        self.assertEqual(videocc.expected_values["read_only_divider_count"], 4)
        self.assertEqual(videocc.expected_values["shared_branch_ares_word_count"], 2)
        self.assertEqual(videocc.expected_values["reset_count"], 7)
        self.assertEqual(videocc.expected_values["remote_vote_count"], 4)
        self.assertEqual(videocc.expected_values["video_gdsc_vmstate_payload_bytes"], 142)
        self.assertIs(videocc.expected_values["pll1_v2_fixup_replayed"], True)
        self.assertIs(videocc.expected_values["ares_implies_clock_gating_assumed"], False)
        self.assertIs(videocc.expected_values["generic_dividers_round_up"], True)
        self.assertIs(videocc.expected_values["reference_enabled_by_default"], False)
        self.assertEqual(videocc.expected_lengths, npu.expected_lengths)
        self.assertEqual(cases["videocc-source-generation"].expected_lengths,
                         {"inputs": 10})
        dsi = cases["dsi-pll-7nm-reference"]
        self.assertEqual(dsi.expected_count, 36)
        self.assertEqual(dsi.expected_values["vmstate_payload_bytes"], 2926)
        self.assertEqual(dsi.expected_values["reference_identity"], 0x44534934)
        self.assertEqual(dsi.expected_values["register_count"], 308)
        self.assertEqual(dsi.expected_values["bank_count"], 2)
        self.assertEqual(dsi.expected_values["sparse_window_count"], 16)
        self.assertEqual(dsi.expected_values["mapped_bytes"], 1232)
        self.assertEqual(dsi.expected_values["output_clock_count"], 52)
        self.assertEqual(dsi.expected_values["connected_input_clock_count"], 1)
        self.assertEqual(dsi.expected_values["mdss_vmstate_payload_bytes"], 64)
        self.assertIs(dsi.expected_values["reference_enabled_by_default"], False)
        self.assertIs(dsi.expected_values["native_kernel_probe_observed"], False)
        self.assertIs(dsi.expected_values["runtime_xo_change_tested"], True)
        self.assertIs(dsi.expected_values["generic_dividers_round_up"], True)
        self.assertEqual(dsi.expected_lengths,
                         {"migrations": 3, "rejected_migrations": 2,
                          "corrupted_stream_rejections": 4,
                          "migration_sources": 4, "actual_stream_files": 8})
        self.assertEqual(cases["dsi-pll-source-generation"].expected_lengths,
                         {"inputs": 13})
        self.assertEqual(cases["dp-pll-source-generation"].expected_lengths,
                         {"inputs": 17})
        dp = cases["dp-pll-7nm-reference"]
        self.assertEqual(dp.expected_count, 36)
        self.assertEqual(dp.expected_values["vmstate_payload_bytes"], 791)
        self.assertEqual(dp.expected_values["reference_identity"], 0x44503731)
        self.assertEqual(dp.expected_values["register_count"], 73)
        self.assertEqual(dp.expected_values["region_count"], 4)
        self.assertEqual(dp.expected_values["sparse_window_count"], 42)
        self.assertEqual(dp.expected_values["mapped_bytes"], 292)
        self.assertEqual(dp.expected_values["output_clock_count"], 6)
        self.assertEqual(dp.expected_values["connected_input_clock_count"], 1)
        self.assertEqual(dp.expected_values["dispcc_carrier_connection_count"], 2)
        self.assertEqual(dp.expected_values["ccf_rate_scale"], 1000)
        self.assertEqual(dp.expected_values["late_phy_wait_ns"], 2000000)
        self.assertEqual(dp.expected_values["first_observation_qom_get_count"], 304)
        self.assertEqual(dp.expected_values["mdss_vmstate_payload_bytes"], 64)
        self.assertIs(dp.expected_values["reference_enabled_by_default"], False)
        self.assertIs(dp.expected_values["native_kernel_probe_observed"], False)
        self.assertIs(dp.expected_values["runtime_xo_change_tested"], True)
        self.assertIs(dp.expected_values["frequency_checks_use_exact_clock_period"], True)
        self.assertEqual(dp.expected_lengths,
                         {"checks": 36, "migrations": 4, "rejected_migrations": 2,
                          "corrupted_stream_rejections": 4,
                          "migration_sources": 5, "actual_stream_files": 9})
        self.assertEqual(cases["llcc-source-generation"].expected_lengths,
                         {"inputs": 8})
        llcc = cases["llcc-sm8250-reference"]
        self.assertEqual(llcc.expected_count, 32)
        self.assertEqual(llcc.expected_values["reference_identity"], 0x4c4c4332)
        self.assertEqual(llcc.expected_values["vmstate_payload_bytes"], 783)
        self.assertEqual(llcc.expected_values["bank_count"], 4)
        self.assertEqual(llcc.expected_values["slice_count"], 16)
        self.assertEqual(llcc.expected_values["register_count"], 71)
        self.assertEqual(llcc.expected_values["mapped_word_count"], 349)
        self.assertEqual(llcc.expected_values["sparse_window_count"], 139)
        self.assertEqual(llcc.expected_values["mapped_bytes"], 1396)
        self.assertEqual(llcc.expected_values["first_observation_qom_get_count"], 313)
        self.assertEqual(llcc.expected_values["transition_delay_ns"], 1000)
        self.assertEqual(llcc.expected_values["provider_count"], 0)
        self.assertIs(llcc.expected_values["reference_enabled_by_default"], False)
        self.assertIs(llcc.expected_values["physical_bank_count_measured"], False)
        self.assertIs(llcc.expected_values["hardware_version_known"], False)
        self.assertIs(llcc.expected_values["memory_controller_count_known"], False)
        self.assertIs(llcc.expected_values["native_kernel_probe_observed"], False)
        self.assertEqual(llcc.expected_lengths,
                         {"checks": 32, "migrations": 4, "rejected_migrations": 2,
                          "corrupted_stream_rejections": 4,
                          "migration_sources": 5, "actual_stream_files": 9})
        rpmh = cases["rpmh-wait-mask-reference"].expected_values
        self.assertEqual(rpmh["vmstate_version"], 3)
        self.assertEqual(rpmh["vmstate_minimum_version"], 2)
        self.assertEqual(rpmh["main_payload_bytes_with_default_clock"], 19424)
        self.assertEqual(rpmh["subsection_payload_bytes"], 24)

    def test_spmi_route_requires_real_unintercepted_transfer(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = ci.plan(args, Path("/test/runtime"))
        self.assertEqual(len(cases), 100)
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

    def test_aop_reference_gate_requires_actual_migration_outcomes(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        matches = [case for case in ci.plan(args, Path("/test/runtime"))
                   if case.name == "aop-reference-peer"]
        self.assertEqual(len(matches), 1)
        case = matches[0]
        self.assertEqual(case.expected_count, 30)
        self.assertEqual(case.expected_lengths,
                         {"migrations": 6, "rejected_migrations": 2})
        self.assertEqual(case.expected_values["completed_migrations"], 6)
        self.assertEqual(case.expected_values["rejected_migration_attempts"], 2)
        self.assertIs(case.expected_values["ipcc_output_intercepted"], False)
        self.assertIs(case.expected_values["guest_cpu_executed"], False)

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

    def test_console_gates_require_guest_initiated_shutdown(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = {case.name: case for case in ci.plan(args, Path("/test/runtime"))}
        self.assertIn("console-boot-verdict-unit", cases)
        for name in ("linux-console-userspace", "linux-console-empty-ufs"):
            self.assertIs(cases[name].expected_values["guest_shutdown_observed"], True)
            self.assertEqual(cases[name].expected_values["validation_errors"], [])

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

    def test_linux_runtime_pm_rejects_incomplete_wake_evidence(self):
        args = SimpleNamespace(suite="linux", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = ci.plan(args, Path("/test/runtime"))
        self.assertEqual(len(cases), 5)
        case = next(case for case in cases
                    if case.name == "linux-ufs-runtime-pm")
        self.assertEqual(case.expected_count, 40)
        self.assertEqual(case.count_field, "guest_checks")
        with tempfile.TemporaryDirectory(prefix="tbci-rpm-", dir="/tmp") as d:
            receipt = Path(d) / "result.json"
            case.result = str(receipt)
            valid = {
                "passed": True,
                "guest_checks": [["PASS", f"synthetic_{i}", "0"] for i in range(40)],
                "inputs_unchanged": True, "qemu_exit_code": 0,
                "kernel_failure_lines": [],
                "guest_verdicts": [["PASS", "40", "0"]],
                "host_sleep_checks": {
                    "vm_remains_running": True,
                    "ufs_link_hibern8": True,
                    "core_clock_gated": True,
                    "iface_clock_gated": True,
                    "unipro_clock_gated": True},
                "host_observations": [{}, {}, {}],
                "system_suspend_tested": False,
                "inflight_checkpoint_tested": False,
            }
            receipt.write_text(json.dumps(valid))
            ci.judge(case, receipt)
            for key, invalid in (
                    ("inputs_unchanged", False),
                    ("qemu_exit_code", 1),
                    ("kernel_failure_lines", ["I/O error"]),
                    ("guest_verdicts", [["PASS", "39", "0"]]),
                    ("host_sleep_checks", {}),
                    ("host_sleep_checks",
                     dict(valid["host_sleep_checks"], core_clock_gated=False)),
                    ("host_observations", [{}, {}]),
                    ("guest_checks", valid["guest_checks"][:-1]),
                    ("guest_checks", [["PASS", "duplicate", "0"]] * 40),
                    ("guest_checks", [["PASS", f"synthetic_{i}", "bad"] for i in range(40)]),
                    ("guest_checks", [["PASS", f"synthetic_{i}"] for i in range(40)])):
                with self.subTest(field=key, invalid=invalid):
                    receipt.write_text(json.dumps(dict(valid, **{key: invalid})))
                    with self.assertRaises(ValueError):
                        ci.judge(case, receipt)

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


    def test_spss_contract_requires_complete_bounded_receipts(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = {item.name: item for item in ci.plan(args, Path("/test/runtime"))}
        generation = cases["spss-irq-mask-source-generation"]
        self.assertEqual(generation.expected_count, 2)
        self.assertEqual(generation.expected_lengths, {"inputs": 2})
        self.assertEqual(generation.expected_values,
                         {"schema": "tb371fc-spss-irq-mask-generation-v1",
                          "source_pins_verified": True})
        case = cases["spss-irq-mask-sm8250-reference"]
        self.assertEqual(case.expected_count, 21)
        self.assertEqual(case.expected_values, {'schema': 'tb371fc-spss-irq-mask-sm8250-v1',
                          'suite': 'spss-irq-mask-sm8250-reference',
                          'expected_checks': 21,
                          'expected_completed_migrations': 3,
                          'expected_rejected_migrations': 2,
                          'expected_corrupted_stream_rejections': 6,
                          'expected_unmodified_streams': 4,
                          'expected_stream_files': 10,
                          'vmstate_version': 1,
                          'vmstate_minimum_version': 1,
                          'vmstate_payload_bytes': 25,
                          'reference_identity': 1397771569,
                          'physical_base': 25694248,
                          'register_count': 1,
                          'sparse_window_count': 1,
                          'mapped_bytes': 4,
                          'first_observation_qom_get_count': 18,
                          'irq_output_count': 0,
                          'input_clock_count': 0,
                          'output_clock_count': 0,
                          'timer_count': 0,
                          'reference_enabled_by_default': False,
                          'readback_supported': False,
                          'ready_modeled': False,
                          'firmware_modeled': False,
                          'pas_modeled': False,
                          'hardware_reset_value_known': False,
                          'programmed_zero_distinct_from_unknown': True,
                          'source_probe_mask': 4244635647,
                          'guest_cpu_executed': False,
                          'native_kernel_probe_observed': False,
                          'android_boot_passed': False,
                          'binary_unchanged': True})
        self.assertEqual(case.expected_lengths,
                         {"checks": 21, "migrations": 3, "rejected_migrations": 2,
                          "corrupted_stream_rejections": 6,
                          "migration_sources": 4, "actual_stream_files": 10})
        with tempfile.TemporaryDirectory(prefix="tbci-spss-", dir="/tmp") as directory:
            path = Path(directory)/"result.json"
            case.result = str(path)
            valid = {**case.expected_values, "passed": True,
                     "checks": [{"name": f"synthetic_{i}", "passed": True} for i in range(21)],
                     "migrations": [{} for _ in range(3)],
                     "rejected_migrations": [{} for _ in range(2)],
                     "corrupted_stream_rejections": [{} for _ in range(6)],
                     "migration_sources": [{} for _ in range(4)],
                     "actual_stream_files": [{} for _ in range(10)]}
            path.write_text(json.dumps(valid))
            ci.judge(case, path)
            for key, value in (
                    ("checks", valid["checks"][:-1]),
                    ("checks", [{"name": "duplicate", "passed": True}] * 21),
                    ("migrations", [{}, {}]),
                    ("rejected_migrations", [{}]),
                    ("corrupted_stream_rejections", [{}] * 5),
                    ("migration_sources", [{}] * 3),
                    ("actual_stream_files", [{}] * 9),
                    ("vmstate_payload_bytes", 24),
                    ("reference_identity", 0x53504d30),
                    ("first_observation_qom_get_count", 17),
                    ("first_observation_qom_get_count", True),
                    ("readback_supported", True), ("ready_modeled", True),
                    ("firmware_modeled", True), ("pas_modeled", True),
                    ("hardware_reset_value_known", True),
                    ("programmed_zero_distinct_from_unknown", False),
                    ("native_kernel_probe_observed", True), ("binary_unchanged", False)):
                with self.subTest(field=key, value=value):
                    path.write_text(json.dumps(dict(valid, **{key: value})))
                    with self.assertRaises(ValueError):
                        ci.judge(case, path)

    def test_spss_additions_preserve_all_prior_98_contracts_and_order(self):
        # Exact normalized plan at parent 7372681; omit only the two new cases.
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        retained = []
        cases = ci.plan(args, Path("/test/runtime"))
        for case in cases:
            if case.name in {"spss-irq-mask-source-generation", "spss-irq-mask-sm8250-reference"}:
                continue
            def normalize(value):
                if isinstance(value, Path):
                    value = str(value)
                if isinstance(value, str):
                    for other in cases:
                        physical, token = str(other.fixture), "@FIXTURE:"+other.name+"@"
                        value = token if value == physical else value.replace(physical+"/", token+"/")
                    return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
                if isinstance(value, list):
                    return [normalize(item) for item in value]
                if isinstance(value, dict):
                    return {key: normalize(item) for key, item in value.items()}
                return value
            retained.append(normalize(vars(case)))
        self.assertEqual(len(retained), 98)
        fingerprint = hashlib.sha256(json.dumps(retained, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "c37b44cd9cba85b593be878e97e98b4af8df15ff8bedae72ba001f00291c82c6")



if __name__ == "__main__":
    unittest.main()
