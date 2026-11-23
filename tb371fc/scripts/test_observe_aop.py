#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Negative observer unit checks; these are not native execution receipts."""
from pathlib import Path
import subprocess
import sys
import unittest

import observe_aop as observer


class Monitor:
    def __init__(self):
        self.calls = []
        self.statuses = [{"running": False, "status": "paused"},
                         {"running": False, "status": "paused"}]
        self.fields = {
            "state": "connected", "accepted-messages": 1,
            "rejected-requests": 0, "last-error": "none",
            "qdss-vote": True, "qdss-ao-vote": False, "notifications": 4,
            "work-pending": False, "notification-pending": False,
        }
        self.missing = None

    def __call__(self, name, arguments=None):
        self.calls.append((name, arguments))
        if name == "query-status":
            return self.statuses.pop(0)
        if name == "qom-get":
            if arguments["path"] != observer.PEER_PATH:
                raise AssertionError("Unexpected QOM path")
            field = arguments["property"]
            if self.missing == field:
                raise RuntimeError("Device or property not found")
            return self.fields[field]
        raise AssertionError("Observer attempted a mutating/unexpected command")


class AopObservationTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Monitor()
        self.machine = "tb371fc,smem-profile=reference-empty,aop-profile=reference-qdss-vote"

    def capture(self):
        return observer.capture_aop_peer(self.monitor, self.machine)

    def assert_inconclusive(self, result):
        self.assertEqual(result["verdict"], "observation-inconclusive")
        self.assertIs(result["reference_exchange_observed"], False)
        self.assertIs(result["guest_memory_written"], False)
        self.assertIs(result["aop_firmware_executed"], False)
        self.assertIs(result["android_boot_passed"], False)
        self.assertLessEqual({name for name, _ in self.monitor.calls},
                             {"query-status", "qom-get"})

    def test_valid_exchange_reads_nine_fields_between_two_stop_checks(self):
        result = self.capture()
        self.assertEqual(result["verdict"], "reference-exchange-observed")
        self.assertIs(result["reference_exchange_observed"], True)
        self.assertEqual(len(result["observations"]), 9)
        self.assertEqual(self.monitor.calls[0], ("query-status", None))
        self.assertEqual(self.monitor.calls[-1], ("query-status", None))
        self.assertEqual(len(self.monitor.calls), 11)
        self.assertEqual([args["property"] for name, args in self.monitor.calls
                          if name == "qom-get"], list(observer.FIELDS))
        self.assertEqual({name for name, _ in self.monitor.calls},
                         {"query-status", "qom-get"})
        for name in ("aop_firmware_executed", "android_boot_passed",
                     "guest_memory_written"):
            self.assertIs(result[name], False)

    def test_off_votes_do_not_erase_observed_command_history(self):
        self.monitor.fields["qdss-vote"] = False
        self.monitor.fields["accepted-messages"] = 2
        self.monitor.fields["notifications"] = 5
        self.assertIs(self.capture()["reference_exchange_observed"], True)

    def test_handshake_or_vote_state_alone_is_not_an_exchange(self):
        self.monitor.fields["accepted-messages"] = 0
        result = self.capture()
        self.assertEqual(result["verdict"], "reference-exchange-not-observed")
        self.assertIs(result["reference_exchange_observed"], False)
        self.assertIn("no supported command", result["reason"])

    def test_disconnected_or_unnotified_state_is_not_observed(self):
        for name, value in (("state", "link-up"), ("notifications", 0),
                            ("notification-pending", True)):
            with self.subTest(field=name):
                self.monitor = Monitor()
                self.monitor.fields[name] = value
                result = self.capture()
                self.assertEqual(result["verdict"], "reference-exchange-not-observed")
                self.assertIs(result["reference_exchange_observed"], False)

    def test_invalid_counters_never_pass(self):
        for name in observer.COUNT_FIELDS:
            for value in (True, -1, 1.0, "1", None, 1 << 64):
                with self.subTest(field=name, value=value):
                    self.monitor = Monitor()
                    self.monitor.fields[name] = value
                    self.assert_inconclusive(self.capture())

    def test_invalid_boolean_fields_never_pass(self):
        for name in observer.BOOL_FIELDS:
            for value in (0, 1, "false", None):
                with self.subTest(field=name, value=value):
                    self.monitor = Monitor()
                    self.monitor.fields[name] = value
                    self.assert_inconclusive(self.capture())

    def test_unknown_or_wrong_type_state_never_passes(self):
        for name, value in (("state", "ready"), ("state", True),
                            ("last-error", "unsupported-new-kind"),
                            ("last-error", 0)):
            with self.subTest(field=name, value=value):
                self.monitor = Monitor()
                self.monitor.fields[name] = value
                self.assert_inconclusive(self.capture())

    def test_running_or_unstable_guest_is_not_read(self):
        for status in ({"running": True, "status": "running"},
                       {"running": 0, "status": "paused"},
                       {"running": False, "status": "running"},
                       {"running": False, "status": "inmigrate"}, {}, None):
            with self.subTest(status=status):
                self.monitor = Monitor()
                self.monitor.statuses[0] = status
                self.assert_inconclusive(self.capture())
                self.assertEqual(self.monitor.calls, [("query-status", None)])

    def test_guest_resuming_during_snapshot_is_inconclusive(self):
        self.monitor.statuses[-1] = {"running": True, "status": "running"}
        result = self.capture()
        self.assert_inconclusive(result)
        self.assertEqual(len(result["observations"]), 9)

    def test_changed_stop_state_is_inconclusive(self):
        self.monitor.statuses[-1] = {"running": False, "status": "prelaunch"}
        self.assert_inconclusive(self.capture())

    def test_missing_qom_device_or_field_is_inconclusive(self):
        for name in ("state", "notifications"):
            with self.subTest(field=name):
                self.monitor = Monitor()
                self.monitor.missing = name
                self.assert_inconclusive(self.capture())

    def test_wrong_or_shadowed_profile_is_rejected_before_qmp(self):
        for machine in (
                "tb371fc", "virt,aop-profile=reference-qdss-vote",
                "tb371fc,aop-profile=none",
                "tb371fc,aop-profile=reference-qdss-vote-extra",
                "tb371fc,aop-profile=reference-qdss-vote,aop-profile=none",
                "tb371fc,aop-profile=none,aop-profile=reference-qdss-vote"):
            with self.subTest(machine=machine):
                self.monitor = Monitor()
                result = observer.capture_aop_peer(self.monitor, machine)
                self.assert_inconclusive(result)
                self.assertEqual(self.monitor.calls, [])

    def test_initial_report_never_claims_an_observation(self):
        result = observer.initial_observation(self.machine)
        self.assert_inconclusive(result)
        self.assertEqual(result["observations"], {})

    def test_diagnostic_cli_requires_explicit_reference_profile(self):
        script = Path(__file__).with_name("diagnose_kernel.py")
        command = [sys.executable, str(script), "--qemu", "/missing/qemu",
                   "--kernel", "/missing/kernel", "--initrd", "/missing/initrd",
                   "--append", "nokaslr", "--out", "/missing/output",
                   "--observe-aop-peer"]
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--observe-aop-peer requires", result.stderr)
        self.assertNotIn("not a regular file", result.stderr)


if __name__ == "__main__":
    unittest.main()
