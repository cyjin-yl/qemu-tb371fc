#!/usr/bin/env python3
"""Exercise the real migration harness with staged serial reads and no guest."""
# SPDX-License-Identifier: GPL-2.0-only
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import boot_ufs_migration as harness


RESULT = "TB371FC_UFS_MIGRATION_RESULT "
READY = "TB371FC_UFS_MIGRATION_READY"
CHECKPOINT = "TB371FC_UFS_MIGRATION_CHECKPOINT "
RESULT_LINE = (
    RESULT + "PASS nonce=" + "a" * 32 +
    " boot_id=12345678-1234-1234-1234-123456789abc checks=39 failures=0\n"
)


class FakeClock:
    def __init__(self):
        self.current = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += seconds


class FakeProcess:
    def __init__(self, exit_code):
        self.exit_code = exit_code
        self.returncode = None
        self.poll_calls = 0

    def poll(self):
        self.poll_calls += 1
        self.returncode = self.exit_code
        return self.returncode


@contextmanager
def serial_guest(texts, exit_code=None):
    # Skip Guest.__init__: no output directory, QMP socket or process is needed.
    guest = harness.Guest.__new__(harness.Guest)
    guest.name = "target"
    guest.process = FakeProcess(exit_code)
    guest.text = (mock.Mock(return_value=texts) if isinstance(texts, str)
                  else mock.Mock(side_effect=texts))
    clock = FakeClock()
    with mock.patch.object(harness, "time", clock):
        yield guest, clock


class WaitSerialTests(unittest.TestCase):
    def wait(self, guest, marker, timeout=1.0, raises=None):
        # Unexpected exceptions are assertion failures in the old-base control,
        # with their original traceback retained by exception chaining.
        try:
            result = harness.Guest.wait_serial(guest, marker, timeout)
        except Exception as error:
            if raises is None:
                self.fail(f"Unexpected {type(error).__name__}: {error}")
            self.assertIsInstance(error, raises)
            return error
        if raises is not None:
            self.fail(f"Expected {raises.__name__}, got successful marker read")
        return result

    def test_result_prefix_waits_for_complete_line(self):
        with serial_guest([RESULT, RESULT_LINE]) as (guest, clock):
            self.assertEqual(self.wait(guest, RESULT), RESULT_LINE)
            self.assertEqual(guest.text.call_count, 2)
            self.assertGreater(clock.current, 0)

    def test_requested_marker_has_priority_over_terminal_result(self):
        text = READY + "\n" + RESULT_LINE
        with serial_guest(text) as (guest, clock):
            self.assertEqual(self.wait(guest, READY), text)
            self.assertEqual(guest.text.call_count, 1)
            self.assertEqual(clock.sleeps, [])

    def test_complete_result_before_other_marker_fails(self):
        for marker in (READY, CHECKPOINT):
            with serial_guest(RESULT_LINE) as (guest, clock):
                error = self.wait(guest, marker, raises=harness.ValidationError)
                self.assertIn("ended before", str(error))
                self.assertEqual(guest.text.call_count, 1)
                self.assertEqual(clock.sleeps, [])

    def test_partial_result_before_other_marker_waits(self):
        with serial_guest([RESULT, RESULT_LINE]) as (guest, clock):
            error = self.wait(guest, READY, raises=harness.ValidationError)
            self.assertIn("ended before", str(error))
            self.assertEqual(guest.text.call_count, 2)
            self.assertGreater(clock.current, 0)

    def test_exit_final_read_finds_complete_result(self):
        with serial_guest([RESULT, RESULT_LINE], exit_code=0) as (guest, clock):
            self.assertEqual(self.wait(guest, RESULT), RESULT_LINE)
            self.assertEqual(guest.text.call_count, 2)
            self.assertEqual(guest.process.poll_calls, 1)
            self.assertEqual(clock.sleeps, [])

    def test_exit_with_incomplete_result_fails(self):
        with serial_guest(["", RESULT_LINE[:-1]], exit_code=0) as (guest, clock):
            error = self.wait(guest, RESULT, raises=harness.ValidationError)
            self.assertIn("exited before", str(error))
            self.assertEqual(guest.text.call_count, 2)
            self.assertEqual(guest.process.poll_calls, 1)
            self.assertEqual(clock.sleeps, [])

    def test_exit_without_marker_fails(self):
        with serial_guest(["", "unrelated output\n"], exit_code=1) as (guest, clock):
            error = self.wait(guest, RESULT, raises=harness.ValidationError)
            self.assertIn("exited before", str(error))
            self.assertEqual(guest.text.call_count, 2)
            self.assertEqual(guest.process.poll_calls, 1)
            self.assertEqual(clock.sleeps, [])

    def test_exit_with_other_complete_result_fails(self):
        with serial_guest(["", RESULT_LINE], exit_code=0) as (guest, clock):
            error = self.wait(guest, READY, raises=harness.ValidationError)
            self.assertIn("exited before", str(error))
            self.assertEqual(guest.text.call_count, 2)
            self.assertEqual(guest.process.poll_calls, 1)
            self.assertEqual(clock.sleeps, [])

    def test_alive_partial_result_times_out(self):
        timeout = 0.1
        with serial_guest(RESULT) as (guest, clock):
            error = self.wait(guest, RESULT, timeout=timeout, raises=TimeoutError)
            self.assertIn("did not emit", str(error))
            self.assertAlmostEqual(clock.current, timeout)
            self.assertGreater(guest.process.poll_calls, 0)


if __name__ == "__main__":
    unittest.main()
