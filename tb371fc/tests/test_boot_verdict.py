#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Negative boot-acceptance cases; these do not claim to execute a guest."""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from boot_qmp import QMP, has_guest_shutdown
from boot_verdict import CHECKS, validate_console, validate_counter_probe


class ConsoleVerdictTests(unittest.TestCase):
    def setUp(self):
        self.native = {"probe_result": {"counter_frequency_hz": 19200000}}
        self.probe = {"schema_version": 1, "kind": "tb371fc-el0-counter-probe",
                      "counter_frequency_hz": 19200000, "counter_start": 10,
                      "counter_end": 20, "counter_delta": 10,
                      "loop_iterations": 65536, "increased": True}
        self.lines = ["TB371FC_GUEST_BEGIN"]
        self.lines += [f"TB371FC_CHECK PASS {name}" for name in CHECKS]
        self.lines += [json.dumps(self.probe), "TB371FC_GUEST_RESULT PASS failures=0"]

    def validate(self, lines):
        return validate_console("\n".join(lines) + "\n", self.native)

    def test_complete_unique_guest_record(self):
        result = self.validate(self.lines)
        self.assertEqual(result["validation_errors"], [])
        self.assertEqual(len(result["guest_checks"]), 8)
        self.assertTrue(result["arch_counter_matches_native_frequency_and_progress"])

    def test_duplicate_check_cannot_supply_coverage(self):
        for lines in (self.lines + [self.lines[1]],
                      self.lines[:2] + [self.lines[1]] + self.lines[3:]):
            with self.subTest(lines=lines):
                self.assertTrue(self.validate(lines)["validation_errors"])

    def test_failed_missing_and_malformed_check_rejected(self):
        for bad in ("TB371FC_CHECK FAIL procfs", "TB371FC_CHECK PASS procfs extra",
                    "unrelated output", "TB371FC_CHECK SKIP procfs"):
            lines = self.lines.copy()
            lines[1] = bad
            with self.subTest(bad=bad):
                self.assertTrue(self.validate(lines)["validation_errors"])

    def test_failed_or_repeated_completion_rejected(self):
        for line in (self.lines[-1], "TB371FC_GUEST_RESULT FAIL failures=1",
                     "TB371FC_GUEST_RESULT PASS failures=0junk"):
            with self.subTest(line=line):
                self.assertTrue(self.validate(self.lines + [line])["validation_errors"])

    def test_start_record_required_once(self):
        self.assertTrue(self.validate(self.lines[1:])["validation_errors"])
        self.assertTrue(self.validate([self.lines[0]] + self.lines)["validation_errors"])

    def test_kernel_failure_after_pass_is_not_a_pass(self):
        for failure in ("Kernel panic - not syncing", "Internal error: Oops",
                        "Oops: 96000004", "BUG: unable to handle page fault"):
            with self.subTest(failure=failure):
                self.assertTrue(self.validate(self.lines + [failure])["validation_errors"])

    def test_counter_probe_requires_one_record(self):
        lines = [x for x in self.lines if "tb371fc-el0-counter-probe" not in x]
        self.assertFalse(self.validate(lines)["arch_counter_matches_native_frequency_and_progress"])
        self.assertTrue(self.validate(self.lines + [json.dumps(self.probe)])["validation_errors"])

    def test_counter_probe_endpoint_and_schema_contract(self):
        changes = (("schema_version", True), ("schema_version", 2),
                   ("counter_frequency_hz", 19200000.0),
                   ("counter_start", False), ("counter_start", -1),
                   ("counter_end", 1 << 64), ("counter_delta", True),
                   ("counter_delta", "10"), ("counter_delta", 1.5),
                   ("counter_delta", 0), ("counter_end", 21),
                   ("loop_iterations", 1), ("loop_iterations", 65536.0),
                   ("increased", 1))
        for field, value in changes:
            probe = dict(self.probe, **{field: value})
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                validate_counter_probe(probe, self.native)

    def test_missing_counter_fields_rejected(self):
        for field in self.probe:
            probe = copy.deepcopy(self.probe)
            del probe[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_counter_probe(probe, self.native)


class ShutdownAndQMPTests(unittest.TestCase):
    def test_only_guest_poweroff_qualifies(self):
        for record in (None, {"exit_code": 0}, {"event": "RESET"},
                       {"event": "SHUTDOWN", "data": None},
                       {"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}},
                       {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-reset"}},
                       {"event": "SHUTDOWN", "data": {"guest": 1, "reason": "guest-shutdown"}}):
            with self.subTest(record=record):
                self.assertFalse(has_guest_shutdown([record]))
        self.assertTrue(has_guest_shutdown([
            {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-shutdown"}}]))

    def test_drain_consumes_all_ready_fragments(self):
        event = {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-shutdown"}}
        payload = json.dumps(event).encode() + b"\n"

        class SocketFixture:
            def __init__(self):
                self.fragments = [payload[:17], payload[17:], b""]

            def recv(self, size):
                return self.fragments.pop(0)

        sock = SocketFixture()
        qmp = QMP(sock)
        with patch("boot_qmp.select.select", return_value=([sock], [], [])):
            qmp.drain()
        self.assertTrue(qmp.closed)
        self.assertEqual(sock.fragments, [])
        self.assertTrue(has_guest_shutdown(qmp.messages))

    def test_fragmented_shutdown_and_eof_retained(self):
        event = {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-shutdown"}}
        payload = json.dumps(event).encode() + b"\n"

        class SocketFixture:
            def __init__(self):
                self.fragments = [payload[:17], payload[17:], b""]

            def recv(self, size):
                return self.fragments.pop(0)

        sock = SocketFixture()
        qmp = QMP(sock)
        with patch("boot_qmp.select.select", return_value=([sock], [], [])):
            self.assertEqual(qmp.receive(0), [])
            self.assertEqual(qmp.receive(0), [event])
            self.assertIsNone(qmp.receive(0))
            qmp.drain()
        self.assertTrue(has_guest_shutdown(qmp.messages))


if __name__ == "__main__":
    unittest.main()
