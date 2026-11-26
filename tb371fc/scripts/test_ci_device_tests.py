#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise CI verdict handling with local synthetic subprocesses only."""
import copy
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
        self.assertEqual(len(plan), 109)
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
                     "spss-irq-mask-sm8250-reference", "rpm-stats-offset-source-generation",
                     "rpm-stats-offset-sm8250-reference",
                     "ddr-stats-offset-source-generation",
                     "ddr-stats-offset-sm8250-reference", "geni-hs-source-generation-unit",
                     "geni-hs-source-generation", "geni-hs-sm8250-reference"):
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
        self.assertEqual(len(cases), 114)
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
        # Exact normalized plan at parent 7372681; omit the SPSS and later offset additions.
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        retained = []
        cases = ci.plan(args, Path("/test/runtime"))
        for case in cases:
            if case.name in {"spss-irq-mask-source-generation", "spss-irq-mask-sm8250-reference",
                             "rpm-stats-offset-source-generation", "rpm-stats-offset-sm8250-reference",
                             "ddr-stats-offset-source-generation", "ddr-stats-offset-sm8250-reference",
                                   "geni-hs-source-generation-unit", "geni-hs-source-generation",
                                   "geni-hs-sm8250-reference", "qce-bam-source-generation",
                                   "qce-bam-safety", "qce-bam-reference", "spmi-debug-fuse-reference", "geni-i2c-reference", "npu-gdsc-pending-reference", "ras-zero-records-compat"}:
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



    def test_rpm_stats_offset_contract_rejects_missing_or_false_boundaries(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = {item.name: item for item in ci.plan(args, Path("/test/runtime"))}
        generation = cases["rpm-stats-offset-source-generation"]
        self.assertEqual(generation.expected_count, 2)
        self.assertEqual(generation.expected_lengths, {"inputs": 4})
        self.assertEqual(generation.expected_values,
                         {"schema": "tb371fc-rpm-stats-offset-generation-v1",
                          "source_pins_verified": True})
        case = cases["rpm-stats-offset-sm8250-reference"]
        self.assertEqual(case.expected_count, 20)
        self.assertEqual(case.expected_values, {'schema': 'tb371fc-rpm-stats-offset-sm8250-v1',
                          'suite': 'rpm-stats-offset-sm8250-reference',
                          'expected_checks': 20,
                          'expected_completed_migrations': 2,
                          'expected_rejected_migrations': 2,
                          'expected_corrupted_stream_rejections': 6,
                          'expected_unmodified_streams': 2,
                          'expected_stream_files': 8,
                          'vmstate_version': 1,
                          'vmstate_minimum_version': 1,
                          'vmstate_payload_bytes': 24,
                          'reference_identity': 1381191473,
                          'physical_base': 205455364,
                          'register_count': 1,
                          'sparse_window_count': 1,
                          'mapped_bytes': 4,
                          'source_offset_value': 983112,
                          'first_observation_qom_get_count': 17,
                          'irq_output_count': 0,
                          'input_clock_count': 0,
                          'output_clock_count': 0,
                          'timer_count': 0,
                          'reference_enabled_by_default': False,
                          'readonly_offset_register': True,
                          'mutable_device_state': False,
                          'migration_destination_device_state_dirtied': False,
                          'migration_destination_ram_witness_dirtied': True,
                          'stats_payload_modeled': False,
                          'ddr_offset_modeled': False,
                          'firmware_modeled': False,
                          'ready_modeled': False,
                          'guest_cpu_executed': False,
                          'native_kernel_probe_observed': False,
                          'android_boot_passed': False,
                          'binary_unchanged': True})
        self.assertEqual(case.expected_lengths, {'checks': 20,
                          'migrations': 2,
                          'rejected_migrations': 2,
                          'corrupted_stream_rejections': 6,
                          'migration_sources': 2,
                          'actual_stream_files': 8})
        with tempfile.TemporaryDirectory(prefix="tbci-rpmoff-", dir="/tmp") as directory:
            path = Path(directory) / "result.json"
            case.result = str(path)
            valid = {**case.expected_values, "passed": True,
                     "checks": [{"name": f"synthetic_{i}", "passed": True} for i in range(20)],
                     "migrations": [{} for _ in range(2)],
                     "rejected_migrations": [{} for _ in range(2)],
                     "corrupted_stream_rejections": [{} for _ in range(6)],
                     "migration_sources": [{} for _ in range(2)],
                     "actual_stream_files": [{} for _ in range(8)]}
            path.write_text(json.dumps(valid))
            ci.judge(case, path)
            for key, value in (
                    ("checks", valid["checks"][:-1]),
                    ("checks", [{"name": "duplicate", "passed": True}] * 20),
                    ("migrations", [{}]), ("rejected_migrations", [{}]),
                    ("corrupted_stream_rejections", [{}] * 5),
                    ("migration_sources", [{}]), ("actual_stream_files", [{}] * 7),
                    ("vmstate_payload_bytes", 25), ("reference_identity", 0x52534f30),
                    ("source_offset_value", 0x000f0049), ("mapped_bytes", 0x1000),
                    ("first_observation_qom_get_count", 16),
                    ("first_observation_qom_get_count", True),
                    ("readonly_offset_register", False), ("mutable_device_state", True),
                    ("migration_destination_device_state_dirtied", True),
                    ("migration_destination_ram_witness_dirtied", False),
                    ("stats_payload_modeled", True), ("ddr_offset_modeled", True),
                    ("firmware_modeled", True), ("ready_modeled", True),
                    ("native_kernel_probe_observed", True), ("android_boot_passed", True),
                    ("binary_unchanged", False)):
                with self.subTest(field=key, value=value):
                    path.write_text(json.dumps(dict(valid, **{key: value})))
                    with self.assertRaises(ValueError):
                        ci.judge(case, path)

    def test_rpm_stats_offset_preserves_all_prior_100_contracts_and_order(self):
        # Every field and order position in the exact merged SPSS baseline.
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        retained = []
        cases = ci.plan(args, Path("/test/runtime"))
        for case in cases:
            if case.name in {"rpm-stats-offset-source-generation", "rpm-stats-offset-sm8250-reference",
                             "ddr-stats-offset-source-generation", "ddr-stats-offset-sm8250-reference",
                                   "geni-hs-source-generation-unit", "geni-hs-source-generation",
                                   "geni-hs-sm8250-reference", "qce-bam-source-generation",
                                   "qce-bam-safety", "qce-bam-reference", "spmi-debug-fuse-reference", "geni-i2c-reference", "npu-gdsc-pending-reference", "ras-zero-records-compat"}:
                continue
            def normalize(value):
                if isinstance(value, Path):
                    value = str(value)
                if isinstance(value, str):
                    for other in cases:
                        physical, token = str(other.fixture), "@FIXTURE:" + other.name + "@"
                        value = token if value == physical else value.replace(physical + "/", token + "/")
                    return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
                if isinstance(value, list):
                    return [normalize(item) for item in value]
                if isinstance(value, dict):
                    return {key: normalize(item) for key, item in value.items()}
                return value
            retained.append(normalize(vars(case)))
        self.assertEqual(len(retained), 100)
        fingerprint = hashlib.sha256(json.dumps(retained, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "c3a85b57e4a88b6c4378e479df0c3e1d6e083a21a6abec0ed432c40d2950c1af")


    def test_ddr_stats_offset_contract_and_prior_102_contracts(self):
        args = SimpleNamespace(suite="components", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = {item.name: item for item in ci.plan(args, Path("/test/runtime"))}
        generation = cases["ddr-stats-offset-source-generation"]
        self.assertEqual(generation.expected_count, 2)
        self.assertEqual(generation.expected_lengths, {"inputs": 4})
        self.assertEqual(generation.expected_values,
                         {"schema": "tb371fc-ddr-stats-offset-generation-v1",
                          "source_pins_verified": True})
        case = cases["ddr-stats-offset-sm8250-reference"]
        self.assertEqual(case.expected_count, 20)
        self.assertEqual(case.expected_values, {'schema': 'tb371fc-ddr-stats-offset-sm8250-v1',
                          'suite': 'ddr-stats-offset-sm8250-reference',
                          'expected_checks': 20,
                          'expected_completed_migrations': 2,
                          'expected_rejected_migrations': 2,
                          'expected_corrupted_stream_rejections': 6,
                          'expected_unmodified_streams': 2,
                          'expected_stream_files': 8,
                          'vmstate_version': 1,
                          'vmstate_minimum_version': 1,
                          'vmstate_payload_bytes': 24,
                          'reference_identity': 1146310449,
                          'physical_base': 205455388,
                          'register_count': 1,
                          'sparse_window_count': 1,
                          'mapped_bytes': 4,
                          'reference_window_mapped_only_when_enabled': True,
                          'default_off_mapped_bytes': 0,
                          'source_offset_value': 983224,
                          'first_observation_qom_get_count': 17,
                          'irq_output_count': 0,
                          'input_clock_count': 0,
                          'output_clock_count': 0,
                          'timer_count': 0,
                          'reference_enabled_by_default': False,
                          'readonly_offset_register': True,
                          'mutable_device_state': False,
                          'migration_destination_device_state_dirtied': False,
                          'migration_destination_ram_witness_dirtied': True,
                          'stats_payload_modeled': False,
                          'rpm_offset_modeled': False,
                          'firmware_modeled': False,
                          'ready_modeled': False,
                          'guest_cpu_executed': False,
                          'native_kernel_probe_observed': False,
                          'android_boot_passed': False,
                          'binary_unchanged': True})
        self.assertEqual(case.expected_lengths, {'checks': 20,
                          'migrations': 2,
                          'rejected_migrations': 2,
                          'corrupted_stream_rejections': 6,
                          'migration_sources': 2,
                          'actual_stream_files': 8})
        with tempfile.TemporaryDirectory(prefix="tbci-ddroff-", dir="/tmp") as directory:
            path = Path(directory) / "result.json"
            case.result = str(path)
            valid = {**case.expected_values, "passed": True,
                     "checks": [{"name": f"synthetic_{i}", "passed": True} for i in range(20)],
                     "migrations": [{} for _ in range(2)],
                     "rejected_migrations": [{} for _ in range(2)],
                     "corrupted_stream_rejections": [{} for _ in range(6)],
                     "migration_sources": [{} for _ in range(2)],
                     "actual_stream_files": [{} for _ in range(8)]}
            path.write_text(json.dumps(valid))
            ci.judge(case, path)
            for key, value in (
                    ("checks", valid["checks"][:-1]),
                    ("checks", [{"name": "duplicate", "passed": True}] * 20),
                    ("migrations", [{}]), ("rejected_migrations", [{}]),
                    ("corrupted_stream_rejections", [{}] * 5),
                    ("migration_sources", [{}]), ("actual_stream_files", [{}] * 7),
                    ("vmstate_payload_bytes", 25), ("reference_identity", 0x44534f30),
                    ("source_offset_value", 0x000f00b9), ("mapped_bytes", 0x1000),
                    ("physical_base", 0x0c3f0004),
                    ("reference_window_mapped_only_when_enabled", False),
                    ("default_off_mapped_bytes", 4),
                    ("first_observation_qom_get_count", 16),
                    ("first_observation_qom_get_count", True),
                    ("readonly_offset_register", False), ("mutable_device_state", True),
                    ("migration_destination_device_state_dirtied", True),
                    ("migration_destination_ram_witness_dirtied", False),
                    ("stats_payload_modeled", True), ("rpm_offset_modeled", True),
                    ("firmware_modeled", True), ("ready_modeled", True),
                    ("native_kernel_probe_observed", True), ("android_boot_passed", True),
                    ("binary_unchanged", False)):
                with self.subTest(field=key, value=value):
                    path.write_text(json.dumps(dict(valid, **{key: value})))
                    with self.assertRaises(ValueError):
                        ci.judge(case, path)
            for key in case.expected_values:
                with self.subTest(missing_required_field=key):
                    incomplete = dict(valid)
                    del incomplete[key]
                    path.write_text(json.dumps(incomplete))
                    with self.assertRaises(ValueError):
                        ci.judge(case, path)

        # Exact 102-case plan at the merged, qualified RPM parent 420819d.
        # Fixture numbering alone may shift after inserting the two DDR cases.
        args.suite = "all"
        all_cases = ci.plan(args, Path("/test/runtime"))
        retained = []
        for prior_case in all_cases:
            if prior_case.name in {"ddr-stats-offset-source-generation",
                                   "ddr-stats-offset-sm8250-reference",
                                   "geni-hs-source-generation-unit", "geni-hs-source-generation",
                                   "geni-hs-sm8250-reference", "qce-bam-source-generation",
                                   "qce-bam-safety", "qce-bam-reference", "spmi-debug-fuse-reference", "geni-i2c-reference", "npu-gdsc-pending-reference", "ras-zero-records-compat"}:
                continue
            def normalize(value):
                if isinstance(value, Path):
                    value = str(value)
                if isinstance(value, str):
                    for other in all_cases:
                        physical, token = str(other.fixture), "@FIXTURE:" + other.name + "@"
                        value = token if value == physical else value.replace(physical + "/", token + "/")
                    return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
                if isinstance(value, list):
                    return [normalize(item) for item in value]
                if isinstance(value, dict):
                    return {key: normalize(item) for key, item in value.items()}
                return value
            retained.append(normalize(vars(prior_case)))
        self.assertEqual(len(retained), 102)
        fingerprint = hashlib.sha256(json.dumps(retained, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "5ed8bc26826b07347d21ea3bf6dc5c392e449acb241e8007b20debe0f21ba1ea")


class GeniContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="tbci-geni-", dir="/tmp"))
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        self.plan = ci.plan(args, self.root / "runtime")
        self.cases = {case.name: case for case in self.plan}

    def check(self, case, data, *, reject=False):
        path = Path(case.result)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        if reject:
            with self.assertRaises(ValueError):
                ci.judge(case, path)
        else:
            return ci.judge(case, path)

    def baseline(self, case):
        value = copy.deepcopy(case.expected_values)
        value.update(passed=True, checks=[{"name": f"check-{i}", "passed": True}
                                          for i in range(case.expected_count)])
        for key, count in case.expected_lengths.items():
            if key != "checks":
                value[key] = [{} for _ in range(count)]
        return value

    def test_source_contract_rejects_false_missing_and_duplicated_negatives(self):
        case = self.cases["geni-hs-source-generation"]
        unit = self.cases["geni-hs-source-generation-unit"]
        self.assertEqual(unit.result, "exit")
        self.assertTrue(unit.command[-1].endswith("/test_geni_hs_sm8250_generation.py"))
        self.assertEqual(case.expected_count, 2)
        self.assertEqual(case.expected_lengths,
                         {"checks": 2, "inputs": 19, "negative_checks": 51, "reference_inputs": 2})
        self.assertEqual(case.expected_values["generated_contract"]["constants"]["GENI_HS_MAIN_FW_REVISION"], 0x201)
        self.assertEqual(case.expected_values["generated_contract"]["constants"]["GENI_HS_WRAPPER_VERSION"], 0x20050000)
        value = self.baseline(case)
        value["negative_checks"] = [
            {"category": category, "name": f"negative-{i}", "rejected": True,
             "rejection_reason": "Rejected at the intended fixture boundary"}
            for category, count in value["negative_counts"].items() for i in range(count)]
        self.assertEqual(self.check(case, value)["check_count"], 2)
        for key in case.expected_values:
            changed = copy.deepcopy(value)
            del changed[key]
            with self.subTest(missing=key):
                self.check(case, changed, reject=True)
        for key in case.expected_lengths:
            changed = copy.deepcopy(value)
            changed[key].pop()
            with self.subTest(short=key):
                self.check(case, changed, reject=True)
        for wrong in (False, 1, "true", None):
            changed = copy.deepcopy(value)
            changed["negative_checks"][0]["rejected"] = wrong
            with self.subTest(rejected=wrong):
                self.check(case, changed, reject=True)
        for field, wrong in (("category", "unknown"), ("name", ""), ("rejection_reason", "")):
            changed = copy.deepcopy(value)
            changed["negative_checks"][0][field] = wrong
            with self.subTest(field=field):
                self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["negative_checks"][1] = copy.deepcopy(changed["negative_checks"][0])
        self.check(case, changed, reject=True)
        for field in ("error", "traceback"):
            changed = copy.deepcopy(value)
            changed[field] = ""
            self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["generated_contract"]["constant_count"] = True
        self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["private_binary_review_publicly_reproduced"] = True
        self.check(case, changed, reject=True)

    def test_compiled_contract_rejects_early_io_partial_and_repaired_migrations(self):
        case = self.cases["geni-hs-sm8250-reference"]
        self.assertEqual(case.expected_count, 55)
        self.assertEqual(case.timeout, 900)
        self.assertEqual(case.expected_values["vmstate_payload_bytes"], 16608)
        self.assertEqual(case.expected_values["first_observation_qom_get_count"], 49)
        value = self.baseline(case)
        first = {"first_qom_get_count": 49, "repeated_qom_get_count": 49,
                 "device_mmio_delta_before_observations": 0,
                 "any_memory_delta_before_observations": 0, "cont_delta_before_observations": 0,
                 "first_world_includes_both_engines": True,
                 "no_backend_consumption_or_output_observed": True,
                 "ram_loaded_after_two_worlds_before_cont": True}
        value["migrations"] = [{"passed": True, "source_status": "completed", "target_status": "completed",
                                "continued_after_restore": True, "complete_hs_wire_bytes_verified": 16608,
                                "complete_legacy_wire_bytes_verified": 16587,
                                "first_target_observation": copy.deepcopy(first)} for _ in range(2)]
        rejected = {"passed": True, "specific_failure_observed": True,
                    "no_early_backend_output": True, "no_destination_memory_commands": True,
                    "no_cont_command": True, "accepted_after_state_repair": False,
                    "changed_bits": 1, "original_stream_unchanged": True}
        for key in ("rejected_migrations", "corrupted_stream_rejections", "structural_stream_rejections"):
            value[key] = [copy.deepcopy(rejected) for _ in range(case.expected_lengths[key])]
        value["process_cleanup"] = [
            {"fixture": f"fixture-{index}", "expected_exit_code": int(index >= 28),
             "exit_code": int(index >= 28), "forced_terminate": False, "forced_kill": False,
             "quit_acknowledged": index < 28} for index in range(58)]
        self.assertEqual(self.check(case, value)["check_count"], 55)
        for key in case.expected_values:
            changed = copy.deepcopy(value)
            del changed[key]
            with self.subTest(missing=key):
                self.check(case, changed, reject=True)
        for key in case.expected_lengths:
            changed = copy.deepcopy(value)
            changed[key].pop()
            with self.subTest(short=key):
                self.check(case, changed, reject=True)
        for key, expected in first.items():
            changed = copy.deepcopy(value)
            changed["migrations"][0]["first_target_observation"][key] = (
                not expected if type(expected) is bool else expected + 1)
            with self.subTest(first_observation=key):
                self.check(case, changed, reject=True)
        for key in ("rejected_migrations", "corrupted_stream_rejections", "structural_stream_rejections"):
            for field in ("passed", "specific_failure_observed", "no_early_backend_output",
                          "no_destination_memory_commands", "no_cont_command", "accepted_after_state_repair"):
                changed = copy.deepcopy(value)
                changed[key][0][field] = not changed[key][0][field]
                with self.subTest(list=key, boundary=field):
                    self.check(case, changed, reject=True)
        for wrong in (True, 0, 2, "1"):
            changed = copy.deepcopy(value)
            changed["corrupted_stream_rejections"][0]["changed_bits"] = wrong
            with self.subTest(changed_bits=wrong):
                self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["migrations"][0]["target_status"] = "active"
        self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["migrations"][0]["complete_legacy_wire_bytes_verified"] -= 1
        self.check(case, changed, reject=True)
        for field, wrong in (("forced_terminate", True), ("forced_kill", True),
                             ("exit_code", 1), ("quit_acknowledged", False),
                             ("expected_exit_code", False)):
            changed = copy.deepcopy(value)
            changed["process_cleanup"][0][field] = wrong
            with self.subTest(cleanup=field):
                self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["process_cleanup"][1] = copy.deepcopy(changed["process_cleanup"][0])
        self.check(case, changed, reject=True)
        changed = copy.deepcopy(value)
        changed["process_cleanup"][0].update(expected_exit_code=1, exit_code=1, quit_acknowledged=False)
        self.check(case, changed, reject=True)

    def test_geni_additions_preserve_every_prior_104_contract_in_order(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        cases = ci.plan(args, Path("/test/runtime"))
        additions = {"geni-hs-source-generation-unit", "geni-hs-source-generation", "geni-hs-sm8250-reference",
                     "qce-bam-source-generation", "qce-bam-safety", "qce-bam-reference", "spmi-debug-fuse-reference", "geni-i2c-reference", "npu-gdsc-pending-reference", "ras-zero-records-compat"}
        self.assertEqual(len(cases), 114)
        def normalize(value):
            if isinstance(value, Path):
                value = str(value)
            if isinstance(value, str):
                for other in cases:
                    physical, token = str(other.fixture), "@FIXTURE:" + other.name + "@"
                    value = token if value == physical else value.replace(physical + "/", token + "/")
                return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            return value
        retained = [normalize(vars(case)) for case in cases if case.name not in additions]
        self.assertEqual(len(retained), 104)
        fingerprint = hashlib.sha256(json.dumps(retained, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "2420779f94ded1adff2751ae2424764557a421b25f10784db9c69df4a4e5286a")

    def test_source_cache_is_scoped_to_the_run_and_recorded(self):
        fixture = self.root / "cache-run" / "01"
        code = ('import os,json; print(json.dumps({"passed":True,'
                '"cache":os.environ["TB371FC_PUBLIC_SOURCE_CACHE"]}))')
        expected = str(fixture.parent / "public-source-cache")
        case = ci.Case("subprocess", [sys.executable, "-c", code], fixture,
                       expected_values={"cache": expected})
        report = ci.run_case(case, self.root / "cache-artifacts", Path(sys.executable))
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["environment"]["TB371FC_PUBLIC_SOURCE_CACHE"], expected)


class QceContractTests(unittest.TestCase):
    """Synthetic receipt-verdict tests, never an emulated-device result."""
    def setUp(self):
        self.args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                                    build_dir=Path("/test/build"), kernel=None)
        self.plan = ci.plan(self.args, Path("/test/runtime"))

    def test_qce_additions_preserve_all_107_prior_contracts_in_order(self):
        additions = {"qce-bam-source-generation", "qce-bam-safety", "qce-bam-reference", "spmi-debug-fuse-reference", "geni-i2c-reference", "npu-gdsc-pending-reference", "ras-zero-records-compat"}
        self.assertEqual(len(self.plan), 114)
        self.assertEqual({c.name for c in self.plan[-7:-4]}, additions - {"spmi-debug-fuse-reference", "geni-i2c-reference", "npu-gdsc-pending-reference", "ras-zero-records-compat"})
        def normalize(value):
            if isinstance(value, Path):
                value = str(value)
            if isinstance(value, str):
                for other in self.plan:
                    physical, token = str(other.fixture), "@FIXTURE:" + other.name + "@"
                    value = token if value == physical else value.replace(physical + "/", token + "/")
                return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
            if isinstance(value, list):
                return [normalize(item) for item in value]
            if isinstance(value, dict):
                return {key: normalize(item) for key, item in value.items()}
            return value
        retained = [normalize(vars(case)) for case in self.plan if case.name not in additions]
        self.assertEqual(len(retained), 107)
        fingerprint = hashlib.sha256(json.dumps(retained, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "53489eaf9a4141bc48c69b910ac86fae384065c637829c2ce7f990da0b5365f8")
        self.args.suite = "linux"
        self.assertEqual(len(ci.plan(self.args, Path("/test/runtime"))), 5)

    def test_qce_gate_counts_and_explicit_scope_are_strict(self):
        cases = {case.name: case for case in self.plan}
        safety, reference = cases['qce-bam-safety'], cases['qce-bam-reference']
        source = cases['qce-bam-source-generation']
        self.assertEqual(source.expected_count, 2)
        self.assertEqual(source.expected_lengths,
                         {'inputs': 12, 'outputs': 2, 'negative_checks': 127,
                          'parser_positive_checks': 2})
        self.assertEqual(source.expected_values['negative_count'], 127)
        self.assertEqual(sum(source.expected_values['negative_category_counts'].values()), 127)
        self.assertIs(source.expected_values['production_pins_modified_by_tests'], False)
        self.assertEqual(safety.expected_count, 4)
        self.assertEqual(reference.expected_count, 74)
        self.assertEqual(reference.expected_lengths,
                         {'migrations': 4, 'rejected_migrations': 2,
                          'corrupted_stream_rejections': 58, 'structural_stream_rejections': 3,
                          'actual_stream_files': 65, 'process_cleanup': 76})
        for gate in (safety, reference):
            self.assertIs(gate.expected_values['profile_bound'], True)
            self.assertEqual(gate.expected_values['reference_profile_sha256'],
                             'f9809a8de0e6b91c57e7baf3d675dfd16c4ce1bc6e2797a0d866ec42bebf1c3b')
            self.assertEqual(gate.expected_values['reference_profile_file_sha256'],
                             '6bb5bff5a445a73fab54261376ef73a820c6b2ac050048e5fca45715755f76be')
            self.assertEqual(gate.expected_values['vmstate_payload_bytes'], 248)
            self.assertEqual(gate.expected_values['pure_observation_getters'], 70)
            self.assertIs(gate.expected_values['qtest_ok_is_memtx_result'], False)
            self.assertIs(gate.expected_values['native_kernel_probe_observed'], False)
            self.assertIs(gate.expected_values['test_oracle_reads_device_implementation'], False)
            self.assertEqual(gate.expected_values['automatic_test_retries'], 0)

    def safety_receipt(self):
        rows = []
        for index, name in enumerate(('default-off', 'explicit-off',
                                     'paused-tcg-pure-observation', 'unbound-enable-fails-closed')):
            code = int(index == 3)
            channel = {'terminal': 'eof', 'error': None}
            drains = {'qmp': dict(channel), 'qtest': dict(channel)}
            if index == 2:
                drains['qtest'] = {'not_opened': True}
            if index == 3:
                drains = {'qmp': {'not_opened': True}, 'qtest': {'not_opened': True}}
            rows.append({'fixture': name, 'expected_exit_code': code, 'exit_code': code,
                         'actually_waited_and_reaped': True,
                         'wait_calls': [{'called': 'Popen.wait', 'returned_exit_code': code}],
                         'forced_terminate': False, 'forced_kill': False,
                         'signals_to_preexisting_processes': False,
                         'quit_sent': not code, 'quit_acknowledged': not code,
                         'quit_not_applicable': 'already-exited' if code else None,
                         'quit_error': None, 'counters': {'cont': 0}, 'irq_events': [],
                         'unexpected_qtest_drain_lines': [], 'drains': drains,
                         'stdio': {name: {'complete_after_actual_wait': True, 'bytes': 0, 'sha256': '0' * 64}
                                   for name in ('qemu.stdout', 'qemu.stderr')}})
        return {'profile': {'bound': False}, 'process_cleanup': rows}

    def test_qce_cleanup_cannot_be_replaced_by_poll_or_aggregate_true(self):
        from qce_bam_ci_contract import judge_qce_details
        case = next(c for c in self.plan if c.name == 'qce-bam-safety')
        valid = self.safety_receipt()
        judge_qce_details(case, valid)
        for field, bad in (('actually_waited_and_reaped', False), ('wait_calls', []),
                           ('forced_terminate', True), ('forced_kill', True),
                           ('signals_to_preexisting_processes', True), ('quit_sent', False),
                           ('quit_acknowledged', False), ('exit_code', True),
                           ('expected_exit_code', False), ('irq_events', [{'action': 'raise', 'number': 0}])):
            changed = copy.deepcopy(valid)
            changed['process_cleanup'][0][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                judge_qce_details(case, changed)
        for terminal in (None, 'timeout', 'reset'):
            changed = copy.deepcopy(valid)
            changed['process_cleanup'][0]['drains']['qmp']['terminal'] = terminal
            with self.subTest(terminal=terminal), self.assertRaises(ValueError):
                judge_qce_details(case, changed)
        changed = copy.deepcopy(valid)
        changed['process_cleanup'][1] = changed['process_cleanup'][0]
        with self.assertRaises(ValueError):
            judge_qce_details(case, changed)



class SpmiDebugFuseContractTests(unittest.TestCase):
    """Synthetic receipt fixtures test verdicts; they never claim QEMU execution."""
    def setUp(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        self.plan = ci.plan(args, Path("/test/runtime"))
        self.case = self.plan[-4]

    def test_all_110_prior_contracts_remain_the_exact_ordered_prefix(self):
        self.assertEqual(len(self.plan), 114)
        self.assertEqual(self.case.name, 'spmi-debug-fuse-reference')
        def normalize(value):
            if isinstance(value, Path):value = str(value)
            if isinstance(value, str):
                for other in self.plan:
                    physical, token = str(other.fixture), '@FIXTURE:' + other.name + '@'
                    value = token if value == physical else value.replace(physical + '/', token + '/')
                return value.replace(str(ci.ROOT), '@ROOT@').replace(sys.executable, '@PYTHON@')
            if isinstance(value, list):return [normalize(x) for x in value]
            if isinstance(value, dict):return {k:normalize(v) for k,v in value.items()}
            return value
        normalized = [normalize(vars(case)) for case in self.plan]
        fingerprint = lambda rows: hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(fingerprint(normalized[:110]), '28eac5e25ea2c4720b47628c30d8b3b08407c96f476f75dc60f175912326b5a6')
        self.assertEqual(fingerprint(normalized[:111]), '3bb17209275609d01562d59528c01b1000b011e84a7377f5114d408b5e22ddee')
        self.assertEqual(self.case.expected_count, 22)
        self.assertEqual(self.case.expected_lengths, {'admission_rejections':8, 'access_rejections':21,
                         'migrations':3, 'rejected_migrations':4, 'corrupted_stream_rejections':2,
                         'actual_stream_files':5, 'process_cleanup':22})

    def receipt(self):
        import struct
        import spmi_debug_fuse_ci_contract as f
        def state(word):return {'enabled':word is not None, 'bound-word':'UNBOUND' if word is None else f'{word:#010x}'}
        def counts():return dict(qom_get=0,qom_set=0,qtest=0,memory=0,device_mmio=0,ram=0,cont=0)
        def pure(word):return {'first':state(word),'repeated':state(word),'first_getters':2,'repeated_getters':2,
                              'counters_before':counts(),'counters_after':dict(counts(),qom_get=4),'no_memory_or_cont':True}
        def read(word):return {'command':'readl 0x7820a8','value':word}
        def unmapped(address,write):
            op='write' if write else 'read'
            return {'command':f'{op}l {address:#x}'+(' 0' if write else ''),
                    'core_diagnostic':[[op,address,4,'(null)','rejected']],
                    'state_unchanged':True,'qtest_ok_is_memtx_result':False}
        def rejection(name,word,message):
            return {'name':name,'passed':True,'exit_code':1,'specific_diagnostic':message,
                    'specific_diagnostic_observed':True,'preload':pure(word),
                    'counters_before_incoming':dict(counts(),qom_get=4),'counters_after_exit':dict(counts(),qom_get=4),
                    'no_device_or_ram_commands':True,'no_cont':True,'failed_process_postload_qom_claimed':False,
                    'whole_machine_ram_not_loaded_claimed':False,'stderr_sha256':'f'*64}
        report = dict(copy.deepcopy(self.case.expected_values), passed=True, qemu_sha256_before='f'*64,
                      qemu_sha256_after='f'*64, checks=[{'name':n,'passed':True} for n in f.CHECKS])
        for key in self.case.expected_lengths:report[key]=[]
        for row,word in zip(report['checks'][:5], (None,None,f.A,f.B,f.POLICY)):
            row.update(pure=pure(word),reset_preserved_configuration=True,
                       mapping={'active_leaves':[[f.BASE,f.BASE+15,f.TYPE+'.decode-guard']] if word is not None else [],
                                'decode_guard_bytes':16 if word is not None else 0,'readable_bytes':4 if word is not None else 0})
            if word is None:row['unmapped']=[unmapped(f.BASE+o,w) for o in (0,8,12) for w in (False,True)]
            else:row['first_read']=read(word)
        report['checks'][4].update(reset_read=read(f.POLICY),virtual_policy_fixture_only=True)
        report['checks'][2]['immutable_properties']=[{'path':p,'property':k,'error':{'class':'GenericError','desc':k},'state_unchanged':True}
            for p,k in ((f.PATH,'enabled'),(f.PATH,'fuse-word'),(f.PATH,'bound-word'),('/machine','spmi-debug-fuse-enabled'),('/machine','spmi-debug-fuse-word'))]
        report['checks'][2]['unmapped_neighbors_and_debug_core']=[unmapped(a,w) for a,w in
            ((f.BASE-4,False),(f.BASE-4,True),(f.BASE+16,False),(f.BASE+16,True),(0x06b0f000,False))]
        for write,items in ((False,[('b',8),('w',8),('q',8),('l',0),('l',4),('l',12),('l',9),('l',10),('l',11),('q',1)]),
                            (True,[('b',8),('w',8),('l',8),('q',8),('l',0),('l',4),('l',12),('l',9),('l',10),('l',11),('q',1)])):
            for width,offset in items:
                size={'b':1,'w':2,'l':4,'q':8}[width];op='write' if write else 'read';value=0x12345678abcdef90&((1<<(8*size))-1)
                report['access_rejections'].append({'command':f'{op}{width} {f.BASE+offset:#x}'+(f' {value:#x}' if write else ''),
                    'diagnostic':[f'{f.TYPE}: rejected {op} offset={offset:#x} size={size}'],'before':state(f.A),'after':state(f.A),
                    'preserved_read':read(f.A),'state_unchanged':True,'qtest_ok_is_memtx_result':False,'read_error_bytes_used_as_identity':False})
        for name,enabled,word in f.ADMISSION:
            message=(f.TYPE+': enabled profile requires explicit fuse-word=0xhhhhhhhh (eight lowercase hex digits); no word is bound by default') if enabled else f.TYPE+': fuse-word requires enabled=on'
            report['admission_rejections'].append({'name':name,'passed':True,'enabled':enabled,'word':word,'exit_code':1,
                'specific_diagnostic':message,'specific_diagnostic_observed':True})
        for key,word in (('off',None),('a',f.A),('b',f.B)):
            payload=struct.pack('>II',0x53444600|(word is not None),word or 0)
            section={'section_name':f.TYPE,'section_type':4,'instance_id':0,'version':1,'payload_bytes':8,'mutable_bytes':0,
                     'payload_offset':80,'payload_hex':payload.hex(),'payload_sha256':hashlib.sha256(payload).hexdigest(),
                     'all_eight_bytes_verified':True,'whole_machine_payload_oracle_claimed':False}
            source={'file':key+'.mig','bytes':100,'sha256':'a'*64,'snapshot':state(word),'section':section,
                    'query_migrate':{'status':'completed'},'query_status':{'running':False,'status':'postmigrate'}}
            target={'preload':pure(word),'first_postload':pure(word),'query_migrate':{'status':'completed'},'query_status':{'running':False},
                    'reset_preserved_configuration':True,'whole_machine_ram_not_loaded_claimed':False}
            report['migrations'].append({'name':'migration-'+key,'passed':True,'source':source,'observation':target})
            report['actual_stream_files'].append({'file':'streams/'+key+'.mig','bytes':100,'sha256':'a'*64})
        for name,word,file,message in [('off-to-on',f.A,'off.mig','53444601 != 53444600'),('on-to-off',None,'a.mig','53444600 != 53444601'),
                                      ('a-to-b',f.B,'a.mig',f'{f.B:x} != {f.A:x}'),('b-to-a',f.A,'b.mig',f'{f.A:x} != {f.B:x}')]:
            report['rejected_migrations'].append(dict(rejection(name,word,message),source_file=file))
        for field,delta,old in (('profile',0,0x53444601),('word',4,f.A)):
            name='corrupt-'+field
            report['corrupted_stream_rejections'].append(dict(rejection(name,f.A,f'{old:x} != {old^1:x}'),field=field,field_offset=80+delta,
                changed_bits=1,source_unchanged=True,source_sha256='a'*64,mutated_sha256='b'*64))
            report['actual_stream_files'].append({'file':'streams/'+name+'.mig','bytes':100,'sha256':'b'*64})
        for name in sorted(f.CHECKS):
            code=int(name not in f.NORMAL);admission=name in {x[0] for x in f.ADMISSION}
            report['process_cleanup'].append({'fixture':name,'expected_exit_code':code,'exit_code':code,'actually_waited_and_reaped':True,
                'wait_calls':[{'called':'Popen.wait','returned_exit_code':code}],'forced_terminate':False,'forced_kill':False,
                'signals_to_preexisting_processes':False,'quit_sent':not code,'quit_acknowledged':not code,'quit_error':None,
                'quit_not_applicable':'already-exited' if code else None,'counters':counts(),'unexpected_qtest_drain_lines':[],'irq_events':[],
                'drains':{c:({'not_opened':True} if admission else {'terminal':'eof','error':None}) for c in ('qmp','qtest')},
                'stdio':{c:{'complete_after_actual_wait':True,'bytes':0,'sha256':'f'*64} for c in ('qemu.stdout','qemu.stderr')}})
        return report

    def test_complete_synthetic_receipt_and_real_boundary_negatives(self):
        valid=self.receipt()
        with tempfile.TemporaryDirectory(prefix='tbci-fuse-',dir='/tmp') as d:
            path=Path(d)/'result.json';case=copy.copy(self.case);case.result=str(path)
            path.write_text(json.dumps(valid));ci.judge(case,path)
            changes=[(('process_cleanup',0,'actually_waited_and_reaped'),False),
                     (('process_cleanup',0,'wait_calls'),[]),
                     (('process_cleanup',0,'forced_terminate'),True),
                     (('process_cleanup',0,'exit_code'),True),
                     (('checks',0,'pure','counters_before','memory'),1),
                     (('checks',4,'first_read','value'),0),
                     (('access_rejections',0,'diagnostic'),[]),
                     (('access_rejections',0,'after','bound-word'),'0x00000000'),
                     (('migrations',0,'source','section','payload_hex'),'0000000000000000'),
                     (('rejected_migrations',0,'no_cont'),False),
                     (('corrupted_stream_rejections',0,'changed_bits'),2),
                     (('physical_fuse_measured',),True),
                     (('actual_stream_files',),valid['actual_stream_files'][:-1])]
            normal_index=next(i for i,r in enumerate(valid['process_cleanup']) if r['fixture']=='default-off')
            changes.append((('process_cleanup',normal_index,'drains','qmp','terminal'),'reset'))
            for keys,value in changes:
                changed=copy.deepcopy(valid);target=changed
                for key in keys[:-1]:target=target[key]
                target[keys[-1]]=value;path.write_text(json.dumps(changed))
                with self.subTest(path=keys),self.assertRaises((ValueError,KeyError)):
                    ci.judge(case,path)



class GeniI2cContractTests(unittest.TestCase):
    """Bounded synthetic reader fixtures; no QEMU or aggregate success fixture."""
    def setUp(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        self.plan = ci.plan(args, Path("/test/runtime"))
        self.case = self.plan[-3]

    def test_all_111_prior_contracts_remain_the_exact_ordered_prefix(self):
        self.assertEqual(len(self.plan), 114)
        self.assertEqual(self.case.name, "geni-i2c-reference")
        self.assertEqual(sum(c.name == self.case.name for c in self.plan), 1)
        def normalize(value):
            if isinstance(value, Path):
                value = str(value)
            if isinstance(value, str):
                for other in self.plan:
                    physical, token = str(other.fixture), "@FIXTURE:" + other.name + "@"
                    value = token if value == physical else value.replace(physical + "/", token + "/")
                return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
            if isinstance(value, list):
                return [normalize(x) for x in value]
            if isinstance(value, dict):
                return {k: normalize(v) for k, v in value.items()}
            return value
        prefix = [normalize(vars(case)) for case in self.plan[:111]]
        fingerprint = hashlib.sha256(json.dumps(prefix, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "3bb17209275609d01562d59528c01b1000b011e84a7377f5114d408b5e22ddee")
        for suite, count in (("components", 109), ("linux", 5)):
            args = SimpleNamespace(suite=suite, qemu=Path("/test/qemu"),
                                   build_dir=Path("/test/build"), kernel=None)
            cases = ci.plan(args, Path("/test/runtime"))
            self.assertEqual(len(cases), count)
            self.assertEqual(any(c.name == "geni-i2c-reference" for c in cases), suite == "components")

    def test_i2c_gate_and_bounded_reader_evidence_are_strict(self):
        import struct
        import geni_i2c_ci_contract as f
        self.assertEqual(self.case.name, f.NAME)
        self.assertEqual(self.case.expected_count, 76)
        self.assertEqual(self.case.timeout, 600)
        self.assertEqual(self.case.expected_values, f.EXPECTED_VALUES)
        self.assertEqual(self.case.expected_lengths, {
            "processes": 76, "roundtrips": 2, "migrations": 4,
            "rejected_migrations": 57, "actual_stream_files": 60})
        self.assertEqual(self.case.expected_lengths, f.EXPECTED_LENGTHS)
        self.assertEqual(self.case.command, [
            sys.executable, str(ci.ROOT / "tb371fc/tests/test_geni_i2c_sm8250.py"),
            "--qemu", "/test/qemu", "--out", str(self.case.fixture / "data")])
        self.assertEqual(self.case.result, str(self.case.fixture / "data/result.json"))
        self.assertEqual((len(f.NORMAL), len(f.MIGRATION), len(f.NEGATIVE), len(f.CHECKS)),
                         (12, 7, 57, 76))
        self.assertEqual((len(f.access_cases()), len(f.masked_write_cases())), (69, 17))

        # Independently written literal profile and dynamic state; not read from C.
        state = {
            "reference-identity": 0x47493231, "instance-id-readonly": 1,
            "instance-id": 1, "physical-base": 0x984000, "window-bytes": 0x4000,
            "interrupt-spi": 602, "wrapper-index": 0,
            "main-fw-revision": 0x300, "secondary-fw-revision": 0x300,
            "main-config-revision": 0, "secondary-config-revision": 0,
            "fifo-words": 16, "fifo-word-bits": 32, "ios-idle": 3,
            "tx-count": 0, "se-clock-period": 0, "m-ahb-clock-period": 0,
            "s-ahb-clock-period": 0, "vmstate-bytes": 305, "profile-wire-bytes": 64,
            "attached-slaves": 0, "tx-payload-bytes": 0, "rx-payload-bytes": 0,
            "dma-read-bytes": 0, "dma-write-bytes": 0,
            "enabled-reference": True, "irq-level": False, "reset-held": False,
            "bus-available": False, "engine-available": False, "command-active": False,
            "firmware-executed": False, "physical-state-captured": False,
            "dma-data-modeled": False, "address-phase-modeled": True,
            "profile": "empty-bus-v1", "registers-hex": "00" * 148,
            "tx-fifo-hex": "00" * 64,
        }
        self.assertEqual(len(state), 38)
        self.assertEqual(set(state), set(f.GETTERS))
        cold = f.state(state, 1, True)
        self.assertEqual(len(cold), 37)
        self.assertFalse(any(cold.values()))
        values = [0] * 37
        values[16], values[17], values[35] = 0x408, 8, 4
        state.update({"registers-hex": struct.pack(">37I", *values).hex(),
                      "tx-fifo-hex": "44332211" + "00" * 60, "tx-count": 1,
                      "se-clock-period": 11, "m-ahb-clock-period": 22,
                      "s-ahb-clock-period": 33, "bus-available": True,
                      "engine-available": True, "irq-level": True})
        actual_registers = f.state(state, 1, True)
        self.assertEqual((actual_registers[0x610], actual_registers[0x614],
                          actual_registers[0xe1c]), (0x408, 8, 4))
        profile = bytes.fromhex(
            "00000001 47493231 00000001 0000000000984000 00004000 "
            "0000025a 00000000 00000300 00000300 00000000 00000000 "
            "00000010 00000020 00000003 00000000")
        expected_wire = (profile + bytes.fromhex(state["registers-hex"])
                         + bytes.fromhex(state["tx-fifo-hex"])
                         + bytes.fromhex("00000001 00 000000000000000b "
                                         "0000000000000016 0000000000000021"))
        self.assertEqual(len(profile), 64)
        self.assertEqual(len(expected_wire), 305)
        self.assertEqual(f.wire(1, True, state), expected_wire)
        for field, bad in (("tx-count", True), ("irq-level", False),
                           ("dma-read-bytes", 1), ("registers-hex", "00" * 147),
                           ("tx-fifo-hex", "44332211" + "00" * 56 + "00000001")):
            changed = copy.deepcopy(state)
            changed[field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                f.state(changed, 1, True)
        changed = copy.deepcopy(state)
        del changed["profile-wire-bytes"]
        with self.assertRaises(ValueError):
            f.state(changed, 1, True)
        for index, bad in ((0, 2), (8, 1)):
            changed = copy.deepcopy(state)
            registers = values.copy()
            registers[index] = bad
            changed["registers-hex"] = struct.pack(">37I", *registers).hex()
            with self.subTest(register_index=index), self.assertRaises(ValueError):
                f.state(changed, 1, True)

        # These are toy cleanup records, not artifact receipts or process evidence.
        empty_sha = hashlib.sha256(b"").hexdigest()
        rows = []
        raised = {"fifo-address-nack", "dma-address-nack", "unsupported-commands",
                  "irq-mask-se-clock", "ahb-clock-gates", "shared-wrapper-reset",
                  "migration-target-on", "migration-return-on"}
        for index, name in enumerate(f.CHECKS):
            code = int(name in f.NEGATIVE)
            counts = {key: 0 for key in f.COUNTERS}
            counts.update(qom_get=684, cont=int(name in f.RUNNING))
            if name in ("default-off", "explicit-off"):
                counts.update(memory=18, device_mmio=18)
            events = ([{"action": "raise", "number": 0},
                       {"action": "lower", "number": 0}] if name in raised else [])
            row = {"fixture": name, "expected_exit_code": code, "exit_code": code,
                   "pid": 1000 + index, "actually_waited_and_reaped": True,
                   "wait_calls": [{"called": "Popen.wait", "returned_exit_code": code}
                                  for _ in range(2 if code else 1)],
                   "forced_terminate": False, "forced_kill": False,
                   "signals_to_preexisting_processes": False, "quit_sent": code == 0,
                   "quit_acknowledged": code == 0, "quit_error": None,
                   "quit_not_applicable": "already-exited" if code else None,
                   "counters": counts, "unexpected_qtest_drain_lines": [],
                   "irq_events": events,
                   "drains": {key: {"terminal": "eof", "error": None,
                                    "drain_hex": "", "drain_bytes": 0}
                              for key in ("qmp", "qtest")},
                   "stdio": {key: {"complete_after_actual_wait": True,
                                   "bytes": 0, "sha256": empty_sha}
                             for key in ("qemu.stdout", "qemu.stderr")}}
            if name == "paused-tcg-observer":
                row["drains"]["qtest"] = {"not_opened": True}
            rows.append(row)
        f.cleanup(rows)
        for field, bad in (("actually_waited_and_reaped", False), ("wait_calls", []),
                           ("quit_acknowledged", False)):
            changed = copy.deepcopy(rows)
            changed[0][field] = bad
            with self.subTest(cleanup=field), self.assertRaises(ValueError):
                f.cleanup(changed)
        changed = copy.deepcopy(rows)
        changed[0]["drains"]["qmp"]["terminal"] = "reset"
        with self.assertRaises(ValueError):
            f.cleanup(changed)

        # All top-level count and summary gates pass, but missing per-case evidence
        # must still fail through the newly connected independent judge.
        summary = {**copy.deepcopy(self.case.expected_values), "passed": True,
                   "synthetic_contract_fixture": True,
                   "checks": [{"name": name, "passed": True} for name in f.CHECKS]}
        for key, count in self.case.expected_lengths.items():
            summary[key] = [{} for _ in range(count)]
        with tempfile.TemporaryDirectory(prefix="tbci-i2c-unit-", dir="/tmp") as directory:
            path = Path(directory) / "summary-only.json"
            path.write_text(json.dumps(summary))
            case = copy.deepcopy(self.case)
            case.result = str(path)
            with self.assertRaises((KeyError, ValueError)):
                ci.judge(case, path)



class NpuGdscPendingContractTests(unittest.TestCase):
    """Only pure synthetic contracts; no native or QEMU subprocess is started."""
    def setUp(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        self.plan = ci.plan(args, Path("/test/runtime"))
        self.case = self.plan[-2]

    def test_all_112_prior_contracts_remain_the_exact_ordered_prefix(self):
        self.assertEqual(len(self.plan), 114)
        self.assertEqual(self.case.name, "npu-gdsc-pending-reference")
        self.assertEqual(sum(c.name == self.case.name for c in self.plan), 1)
        def normalize(value):
            if isinstance(value, Path):
                value = str(value)
            if isinstance(value, str):
                for other in self.plan:
                    physical, token = str(other.fixture), "@FIXTURE:" + other.name + "@"
                    value = token if value == physical else value.replace(physical + "/", token + "/")
                return value.replace(str(ci.ROOT), "@ROOT@").replace(sys.executable, "@PYTHON@")
            if isinstance(value, list):
                return [normalize(x) for x in value]
            if isinstance(value, dict):
                return {k: normalize(v) for k, v in value.items()}
            return value
        prefix = [normalize(vars(case)) for case in self.plan[:112]]
        fingerprint = hashlib.sha256(json.dumps(prefix, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(fingerprint, "23920b243e9ca1830658dd5800125136795cd7e0aa3b598f1046895d81c3dfd5")
        for suite, count in (("components", 109), ("linux", 5)):
            args = SimpleNamespace(suite=suite, qemu=Path("/test/qemu"),
                                   build_dir=Path("/test/build"), kernel=None)
            cases = ci.plan(args, Path("/test/runtime"))
            self.assertEqual(len(cases), count)
            self.assertEqual(any(c.name == self.case.name for c in cases), suite == "components")
        strict = next(c for c in self.plan if c.name == "npu-gdsc-reference")
        self.assertEqual(strict.expected_count, 21)
        self.assertEqual(strict.expected_values["vmstate_payload_bytes"], 64)
        self.assertEqual(strict.expected_values["reference_identity"], 0x4e504731)

    def test_pending_gate_requires_typed_state_wire_wait_and_complete_evidence(self):
        import struct
        import npu_gdsc_pending_ci_contract as f
        self.assertEqual(self.case.name, f.NAME)
        self.assertEqual(self.case.expected_count, 26)
        self.assertEqual(self.case.timeout, 360)
        self.assertEqual(self.case.expected_values, f.EXPECTED_VALUES)
        self.assertEqual(self.case.expected_lengths, {
            "migration_sources": 6, "migrations": 4, "rejected_migrations": 4,
            "corrupted_streams": 3, "corrupted_stream_rejections": 3,
            "actual_stream_files": 9, "process_cleanup": 26})
        self.assertEqual(self.case.expected_lengths, f.EXPECTED_LENGTHS)
        self.assertEqual(self.case.command, [sys.executable,
            str(ci.ROOT / "tb371fc/tests/test_npu_gdsc_pending.py"),
            "--qemu", "/test/qemu", "--out", str(self.case.fixture / "data")])
        self.assertEqual((len(f.NORMAL), len(f.NEGATIVE), len(f.CHECKS)), (19, 7, 26))
        sample = {
            "enabled-reference": True, "unknown-feedback-pending": True,
            "enabled-profile": 2, "reference-identity": 0x4e504731,
            "domain-count": 1, "register-bytes": 4, "mapped-bytes": 4,
            "parent-resource-index": 0xffffffff, "cold-control": 1,
            "control-write-mask": 0xf803, "power-status-mask": 0x80000000,
            "parent-known": False, "parent-on": True,
            "domain0.name": "npu-core", "domain0.address": 0x09981004,
            "domain0.dt-flags": 1, "domain0.timeout-us": 100,
            "domain0.control": 0x5800, "domain0.request-known": True,
            "domain0.requested": True, "domain0.state-known": False,
            "domain0.powered": False, "domain0.hw-request-known": True,
            "domain0.hw-request": True,
        }
        self.assertEqual(len(sample), 24)
        self.assertEqual(f.state(sample), sample)
        raw = bytes.fromhex(
            "00000002 4e504731 00000001 00000004 00000004 ffffffff "
            "00000001 0000f803 80000000 00 01 "
            "00000000 0000000009981004 00000001 00000064 00005800 01 01")
        self.assertEqual(len(raw), 64)
        self.assertEqual(f.wire(sample), raw)
        for key, value in (("enabled-profile", 1), ("domain0.state-known", True),
                           ("domain0.powered", True), ("domain0.control", 0x5802),
                           ("parent-known", 0)):
            changed = copy.deepcopy(sample);changed[key] = value
            with self.subTest(field=key), self.assertRaises(ValueError):
                f.state(changed)
        name = f.TYPE.encode()
        stream = (b"QEVM" + struct.pack(">I", 3) + b"\x04" + struct.pack(">I", 77)
                  + bytes([len(name)]) + name + struct.pack(">II", 0, 1)
                  + raw + b"\x7e" + struct.pack(">I", 77))
        section = f.section(stream)
        self.assertEqual(section["payload_bytes"], 64)
        for changed in (stream[:-1], stream + stream[8:], stream[:8] + b"\x01" + stream[9:]):
            with self.subTest(damage=len(changed)), self.assertRaises(ValueError):
                f.section(changed)
        # A deliberately synthetic cleanup record exercises wait/EOF refusal;
        # it is neither a process receipt nor an aggregate success fixture.
        empty = hashlib.sha256(b"").hexdigest()
        toy = {"fixture": "pending-identity", "pid": 1234,
               "expected_exit_code": 0, "exit_code": 0,
               "actually_waited_and_reaped": True,
               "wait_calls": [{"called": "Popen.wait", "returned_exit_code": 0, "timeout_seconds": 10}],
               "forced_terminate": False, "forced_kill": False,
               "signals_to_preexisting_processes": False, "unexpected_qtest_drain_lines": [],
               "quit_sent": True, "quit_acknowledged": True, "quit_error": None,
               "quit_not_applicable": None, "counters": dict.fromkeys(f.COUNTERS, 0),
               "drains": {k: {"error": None, "terminal": "eof", "drain_hex": "", "drain_bytes": 0}
                          for k in ("qmp", "qtest")},
               "stdio": {k: {"bytes": 0, "sha256": empty, "complete_after_actual_wait": True}
                         for k in ("qemu.stdout", "qemu.stderr")}}
        f.cleanup(toy, "pending-identity")
        for key, value in (("wait_calls", []), ("actually_waited_and_reaped", False),
                           ("quit_acknowledged", False)):
            changed = copy.deepcopy(toy);changed[key] = value
            with self.subTest(cleanup=key), self.assertRaises(ValueError):
                f.cleanup(changed, "pending-identity")
        summary = {**f.EXPECTED_VALUES, "passed": True,
                   "checks": [{"name": name, "passed": True} for name in f.CHECKS],
                   "binary_before_sha256": "0" * 64, "binary_after_sha256": "0" * 64}
        for key, count in f.EXPECTED_LENGTHS.items():
            summary[key] = [{} for _ in range(count)]
        with tempfile.TemporaryDirectory(prefix="tbci-npu-pending-unit-", dir="/tmp") as directory:
            path = Path(directory) / "summary-only.json"
            path.write_text(json.dumps(summary))
            case = copy.deepcopy(self.case);case.result = str(path)
            with self.assertRaises((KeyError, ValueError)):
                ci.judge(case, path)




class RasZeroRecordsContractTests(unittest.TestCase):
    """Pure acceptance-boundary checks; generated guest execution is a CI gate."""
    def setUp(self):
        args = SimpleNamespace(suite="all", qemu=Path("/test/qemu"),
                               build_dir=Path("/test/build"), kernel=None)
        self.plan = ci.plan(args, Path("/test/runtime"))
        self.case = self.plan[-1]

    def test_all_113_prior_gate_contracts_remain_the_ordered_prefix(self):
        import ras_zero_records_ci_contract as f
        self.assertEqual(len(self.plan), 114)
        self.assertEqual(self.case.name, "ras-zero-records-compat")
        self.assertEqual(sum(c.name == self.case.name for c in self.plan), 1)
        fields = ("name", "verdict", "expected_count", "count_field", "expected_values",
                  "expected_lengths", "tap_count", "guest_errno")
        prefix = []
        for case in self.plan[:113]:
            row = {key: getattr(case, key) for key in fields}
            row["kind"] = case.result if case.result in ("exit", "stdout", "tap") else "file"
            prefix.append(row)
        actual = hashlib.sha256(json.dumps(prefix, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self.assertEqual(actual, "019195770f0c505817a1b4c8d09aab81ec3b44956c772ea57a1cdfbd80eb383b")
        for suite, count in (("components", 109), ("linux", 5)):
            args = SimpleNamespace(suite=suite, qemu=Path("/test/qemu"),
                                   build_dir=Path("/test/build"), kernel=None)
            cases = ci.plan(args, Path("/test/runtime"))
            self.assertEqual(len(cases), count)
            self.assertEqual(any(c.name == f.NAME for c in cases), suite == "components")
        self.assertEqual((self.case.expected_count, self.case.timeout), (35, 600))
        self.assertEqual(self.case.expected_values, f.EXPECTED_VALUES)
        self.assertEqual(self.case.expected_lengths, f.EXPECTED_LENGTHS)
        self.assertEqual(self.case.command, [sys.executable,
            str(ci.ROOT / "tb371fc/tests/test_ras_zero_records.py"),
            "--qemu", "/test/qemu", "--out", str(self.case.fixture / "data")])
        self.assertEqual((len(f.ORDER), len(f.NORMAL), len(f.INVALID), len(f.MUTANTS)), (35, 11, 2, 20))
        self.assertEqual(f.SUMMARY["actual_Popen_wait_calls"], 59)
        self.assertEqual(f.SUMMARY["guest_instruction_operations"], 8960)
        self.assertEqual(f.SUMMARY["actual_stream_files"], 24)

    def test_exact_guest_instruction_witness_and_missing_raw_rejection(self):
        import struct
        import ras_zero_records_ci_contract as f
        # One small synthetic ELF, not a QEMU result or compiled guest claim.
        names = ["_start", "secondary_entry", "enter_el1", "enter_el0", "el1_return_hvc",
                 "el0_return_svc", "el2_return", "el1_return"]
        registers = ("erridr", "errselr", "erxfr", "erxctlr", "erxstatus", "erxaddr",
                     "erxmisc0", "erxmisc1", "unknown0", "unknown1")
        encodings = (0xc298, 0xc299, 0xc2a0, 0xc2a1, 0xc2a2, 0xc2a3, 0xc2a8, 0xc2a9, 0xc2a4, 0xc2af)
        names += [prefix + name for name in registers for prefix in ("read_", "write_")]
        strings = bytearray(b"\0"); offsets = {}
        for name in names:
            offsets[name] = len(strings); strings += name.encode() + b"\0"
        code = bytearray(160)
        for reg, encoding in enumerate(encodings):
            for write in (0, 1):
                struct.pack_into("<II", code, (reg * 2 + write) * 8,
                    (0xd5000000 if write else 0xd5200000) | encoding << 5, 0xd65f03c0)
        symbols = bytearray(24)
        for i, name in enumerate(names):
            address = 0x80010000 if i < 8 else 0x80010000 + (i - 8) * 8
            symbols += struct.pack("<IBBHQQ", offsets[name], 0x12, 0, 1, address, 8)
        code_at, sym_at = 0x100, 0x200
        strings_at = sym_at + len(symbols); shoff = (strings_at + len(strings) + 7) & ~7
        raw = bytearray(shoff + 4 * 64)
        ident = b"\x7fELF\x02\x01\x01" + bytes(9)
        struct.pack_into("<16sHHIQQQIHHHHHH", raw, 0, ident, 2, 183, 1,
                         0x80010000, 64, shoff, 0, 64, 56, 1, 64, 4, 0)
        struct.pack_into("<IIQQQQQQ", raw, 64, 1, 5, code_at, 0x80010000,
                         0x80010000, len(code), len(code), 4)
        raw[code_at:code_at + len(code)] = code
        raw[sym_at:sym_at + len(symbols)] = symbols
        raw[strings_at:strings_at + len(strings)] = strings
        for index, kind, flags, address, offset, size, link, align, entry in (
                (1, 1, 6, 0x80010000, code_at, len(code), 0, 4, 0),
                (2, 2, 0, 0, sym_at, len(symbols), 3, 8, 24),
                (3, 3, 0, 0, strings_at, len(strings), 0, 1, 0)):
            struct.pack_into("<IIQQQQIIQQ", raw, shoff + index * 64,
                             0, kind, flags, address, offset, size, link, 0, align, entry)
        witness = f.elf_probe(bytes(raw))
        self.assertEqual(len(witness["instructions"]), 20)
        self.assertEqual(witness["instructions"]["write_errselr"]["instruction_u32"], 0xd5185320)
        damaged = bytearray(raw); damaged[code_at] ^= 1
        for invalid in (bytes(damaged), bytes(raw[:-1])):
            with self.subTest(damage=len(invalid)), self.assertRaises(ValueError):
                f.elf_probe(invalid)
        with self.assertRaises(ValueError):
            f.equal(True, 1, "typed profile")
        # Even all expected aggregate values cannot substitute for missing raw
        # protocol, full guest bytes, actual waits and migration streams.
        summary = {**copy.deepcopy(f.EXPECTED_VALUES), "passed": True,
                   "checks": [{"name": name, "fixture": name, "passed": True} for name in f.ORDER]}
        for key, count in f.EXPECTED_LENGTHS.items():
            if key != "checks": summary[key] = [{} for _ in range(count)]
        with tempfile.TemporaryDirectory(prefix="tbci-ras-unit-", dir="/tmp") as directory:
            path = Path(directory) / "summary-only.json"; path.write_text(json.dumps(summary))
            case = copy.deepcopy(self.case); case.result = str(path)
            with self.assertRaises((KeyError, ValueError)):
                ci.judge(case, path)


if __name__ == "__main__":
    unittest.main()
