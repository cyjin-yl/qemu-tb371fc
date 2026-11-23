#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Negative acceptance tests for the RAM observer; no QEMU/tablet access."""
import copy
import json
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from boot_smoke_gdb import (FIELDS, MAGIC, RECORD_SIZE, STRINGS,
                            decode_record, has_guest_shutdown, validate_record)


class GuestResultTest(unittest.TestCase):
    def setUp(self):
        self.native = {"probe_result": {"counter_frequency_hz": 19200000}}
        probe = {
            "schema_version": 1, "kind": "tb371fc-el0-counter-probe",
            "counter_frequency_hz": 19200000, "counter_start": 10,
            "counter_end": 20, "counter_delta": 10, "increased": True,
            "loop_iterations": 65536,
        }
        raw = json.dumps(probe) + "\n"
        self.record = {
            "magic": MAGIC, "version": 1, "structure_size": RECORD_SIZE,
            "completed": 1, "checks_completed": 255, "pass_mask": 255,
            "fail_mask": 0, "failures": 0, "probe_truncated": 0,
            "counter_probe_wait_status": 0, "uname_sysname": "Linux",
            "uname_machine": "aarch64", "uname_release": "test-kernel",
            "online": "0-7\n", "model": "TB371FC validation fixture",
            "elapsed_ns": 20000000, "start_monotonic_ns": 1,
            "finish_monotonic_ns": 20000001, "probe_stdout": raw,
            "counter_probe_bytes": len(raw.encode()),
        }

    def test_valid_record(self):
        self.assertEqual(validate_record(self.record, self.native)[0], [])

    def test_binary_abi_and_exact_stdout(self):
        blob = bytearray(struct.pack("<14Q", *(
            self.record.get(key, 0) for key in FIELDS)))
        for key, length in STRINGS:
            raw = self.record.get(key, "").encode()
            blob.extend(raw + bytes(length-len(raw)))
        self.assertEqual(len(blob), RECORD_SIZE)
        decoded = decode_record(blob)
        self.assertEqual(decoded["probe_stdout"], self.record["probe_stdout"])
        self.assertEqual(validate_record(decoded, self.native)[0], [])

    def test_incomplete_or_failed_checks_rejected(self):
        for key, value in (("completed", 0), ("pass_mask", 127),
                           ("checks_completed", 127), ("pass_mask", 511),
                           ("fail_mask", 1), ("failures", 1)):
            with self.subTest(key=key, value=value):
                record = dict(self.record, **{key: value})
                self.assertTrue(validate_record(record, self.native)[0])

    def test_bad_payload_and_count_rejected(self):
        for key, value in (("probe_stdout", "{}"), ("probe_truncated", 1),
                           ("counter_probe_bytes", 0),
                           ("counter_probe_wait_status", 256)):
            with self.subTest(key=key):
                self.assertTrue(validate_record(
                    dict(self.record, **{key: value}), self.native)[0])

    def test_wrong_actual_kernel_state_rejected(self):
        for key, value in (("uname_machine", "x86_64"), ("online", "0-3\n"),
                           ("model", "virt"), ("elapsed_ns", 19999999),
                           ("finish_monotonic_ns", 0)):
            with self.subTest(key=key):
                self.assertTrue(validate_record(
                    dict(self.record, **{key: value}), self.native)[0])

    def test_counter_mismatch_rejected(self):
        for key, value in (("counter_frequency_hz", 62500000),
                           ("counter_delta", 0), ("counter_end", 21),
                           ("increased", False), ("loop_iterations", 1)):
            with self.subTest(key=key):
                record = copy.deepcopy(self.record)
                probe = json.loads(record["probe_stdout"])
                probe[key] = value
                record["probe_stdout"] = json.dumps(probe)
                record["counter_probe_bytes"] = len(record["probe_stdout"])
                self.assertTrue(validate_record(record, self.native)[0])

    def test_exit_or_host_shutdown_is_not_guest_shutdown(self):
        self.assertFalse(has_guest_shutdown([]))
        self.assertFalse(has_guest_shutdown([
            {"event": "SHUTDOWN",
             "data": {"guest": False, "reason": "host-qmp-quit"}}]))
        self.assertFalse(has_guest_shutdown([
            {"event": "SHUTDOWN",
             "data": {"guest": True, "reason": "guest-reset"}}]))
        self.assertTrue(has_guest_shutdown([
            {"event": "SHUTDOWN",
             "data": {"guest": True, "reason": "guest-shutdown"}}]))

    def test_truncated_binary_rejected(self):
        with self.assertRaises(ValueError):
            decode_record(bytes(RECORD_SIZE - 1))


if __name__ == "__main__":
    unittest.main()
