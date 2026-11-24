#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reject false SMEM probe claims from fixtures or inconsistent observations.

These are observer unit tests, not production-kernel execution receipts.
The fixture kernel hash is explicitly patched only within each test.
"""
import hashlib
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import observe_smem as observer


class Monitor:
    """Bounded HMP/QMP responses for one stopped, published driver object."""

    def __init__(self):
        self.calls = []
        self.running = False
        self.reference = True
        self.unlocked = True
        self.responses = {}
        self.smem = 0xffffffc012340000
        self.ptable = 0xffffff800b100000
        self.partition = 0xffffff800b200000
        self.header = 0xffffff800b500000
        self.memory = {observer.SYMBOLS["__smem"]: self.smem}
        for offset, value in (
                (0, 0xffffffc012350000), (8, 0xffffffc012360000),
                (0x10, 512), (0x18, self.ptable), (0x20, self.partition),
                (0x28, 0x80903000), (0x2c, 64), (0x30, 0x1fc000),
                (0x140, 1), (0x148, 0x80900000),
                (0x150, self.header), (0x158, 0x200000)):
            self.memory[self.smem + offset] = value
        for base, fields in (
                (self.ptable, {0: 0x434f5424, 4: 1, 8: 1}),
                (self.partition, {0: 0x54525024, 4: 0xfffefffe,
                                  8: 0x1fc000, 12: 32, 16: 0x1fc000}),
                (self.header, {0x5c: 12 << 16, 0xc0: 1, 0xcc: 0})):
            self.memory.update({base + offset: value
                                for offset, value in fields.items()})

    def __call__(self, name, arguments=None):
        self.calls.append((name, arguments))
        if name == "query-status":
            return {"running": self.running, "status": "paused"}
        if name == "qom-get":
            key = (arguments["path"], arguments["property"])
            return {
                ("/machine/smem-handoff", "reference-empty"): self.reference,
                ("/machine/tcsr-mutex", "boot-unlocked-reference"): self.unlocked,
            }[key]
        if name == "human-monitor-command":
            if arguments.get("cpu-index") != 0:
                raise AssertionError("Unexpected virtual-memory CPU context")
            match = re.fullmatch(r"x /1([gw])x (0x[0-9a-f]+)",
                                 arguments["command-line"])
            if not match:
                raise AssertionError("Only bounded guest reads are allowed")
            address = int(match[2], 16)
            if address in self.responses:
                return self.responses[address]
            value = self.memory[address]
            return f"{address:016x}: 0x{value:x}\r\n"
        raise AssertionError("Observer attempted an unexpected QMP command")


class SmemObservationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="smem-observer-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.kernel = self.root / "fixture-image"
        self.symbols = self.root / "fixture-symbols"
        self.tail_offset = 0xffffff800867714c - observer.SYMBOLS["_text"]
        image = bytearray(self.tail_offset + 16)
        image[self.tail_offset:] = bytes.fromhex(
            "88f200f0 f4031f2a 139905f9 b5ffff17")
        self.kernel.write_bytes(image)
        self.symbols.write_text("".join(
            f"{address:016x} T {name}\n"
            for name, address in observer.SYMBOLS.items()))
        self.monitor = Monitor()

    def capture(self, expected_hash=None):
        if expected_hash is None:
            expected_hash = hashlib.sha256(self.kernel.read_bytes()).hexdigest()
        with patch.object(observer, "KERNEL_SHA256", expected_hash):
            return observer.capture_smem_probe(
                self.monitor, self.symbols, self.kernel)

    def assert_inconclusive(self, result, message):
        self.assertEqual(result["verdict"], "observation-inconclusive")
        self.assertIs(result["success_path_observed"], False)
        self.assertIn(message, result["error"])

    def test_published_object_observed_read_only(self):
        result = self.capture()
        self.assertEqual(result["verdict"],
                         "production-driver-success-path-observed")
        self.assertIs(result["success_path_observed"], True)
        self.assertIs(result["guest_memory_written"], False)
        self.assertIs(result["native_sbl_captured"], False)
        self.assertIs(result["android_boot_passed"], False)
        self.assertEqual(len(result["checks"]), 23)
        self.assertGreater(len(result["observations"]), 20)
        self.assertTrue(all(item["passed"] for item in result["checks"]))
        self.assertEqual(
            {name for name, _ in self.monitor.calls},
            {"query-status", "qom-get", "human-monitor-command"})

    def test_seeded_memory_alone_does_not_prove_driver_probe(self):
        self.monitor.memory[observer.SYMBOLS["__smem"]] = 0
        result = self.capture()
        self.assertEqual(result["verdict"], "success-path-not-observed")
        self.assertIs(result["success_path_observed"], False)
        self.assertEqual([item["field"] for item in result["observations"]],
                         ["__smem"])

    def test_unverified_kernel_rejected_before_qmp(self):
        result = self.capture(expected_hash="0" * 64)
        self.assert_inconclusive(result, "unverified for this kernel")
        self.assertEqual(self.monitor.calls, [])

    def test_changed_success_instructions_rejected_before_qmp(self):
        image = bytearray(self.kernel.read_bytes())
        image[self.tail_offset] ^= 1
        self.kernel.write_bytes(image)
        result = self.capture()
        self.assert_inconclusive(result, "success-tail instructions differ")
        self.assertEqual(self.monitor.calls, [])

    def test_ambiguous_symbols_rejected_before_qmp(self):
        self.symbols.write_text(self.symbols.read_text() +
                                "ffffff800a4cab30 B __smem\n")
        result = self.capture()
        self.assert_inconclusive(result, "Ambiguous driver symbol")
        self.assertEqual(self.monitor.calls, [])

    def test_shifted_symbols_rejected_before_qmp(self):
        self.symbols.write_text(self.symbols.read_text().replace(
            "ffffff800a4cab30", "ffffff800a4cbb30"))
        result = self.capture()
        self.assert_inconclusive(result, "Driver symbols differ")
        self.assertEqual(self.monitor.calls, [])

    def test_missing_symbols_rejected_before_qmp(self):
        self.symbols.write_text("\n".join(
            line for line in self.symbols.read_text().splitlines()
            if "__smem" not in line))
        result = self.capture()
        self.assert_inconclusive(result, "Driver symbols differ")
        self.assertEqual(self.monitor.calls, [])

    def test_running_or_nonboolean_status_rejected_without_memory_reads(self):
        for running in (True, 0, "false", None):
            with self.subTest(running=running):
                self.monitor = Monitor()
                self.monitor.running = running
                result = self.capture()
                self.assert_inconclusive(result, "Guest must be stopped")
                self.assertEqual(self.monitor.calls, [("query-status", None)])

    def test_unknown_or_nonboolean_profiles_rejected_without_memory_reads(self):
        for profile in ("reference", "unlocked"):
            for value in (False, 1, "true", None):
                with self.subTest(profile=profile, value=value):
                    self.monitor = Monitor()
                    setattr(self.monitor, profile, value)
                    result = self.capture()
                    self.assert_inconclusive(result, "requires explicit SMEM")
                    self.assertFalse(any(name == "human-monitor-command"
                                         for name, _ in self.monitor.calls))

    def test_virtual_read_error_is_not_a_zero_pointer(self):
        self.monitor.responses[observer.SYMBOLS["__smem"]] = (
            "Cannot access memory\n")
        result = self.capture()
        self.assert_inconclusive(result, "Cannot decode guest virtual field")

    def test_wrong_returned_virtual_address_is_rejected(self):
        self.monitor.responses[observer.SYMBOLS["__smem"]] = (
            f"{observer.SYMBOLS['__smem'] + 8:016x}: 0x{self.monitor.smem:x}\n")
        result = self.capture()
        self.assert_inconclusive(result, "Cannot decode guest virtual field")

    def test_out_of_width_field_is_rejected(self):
        self.monitor.memory[self.monitor.smem + 0x10] = 1 << 32
        result = self.capture()
        self.assert_inconclusive(result, "Out-of-width field: item_count")

    def test_published_pointer_must_be_aligned_kernel_address(self):
        for value in (0x80900000, self.monitor.smem + 1):
            with self.subTest(pointer=value):
                self.monitor = Monitor()
                self.monitor.memory[observer.SYMBOLS["__smem"]] = value
                result = self.capture()
                self.assert_inconclusive(result, "published_driver_pointer")

    def test_wrong_driver_or_shared_headers_are_inconclusive(self):
        for address, value, field in (
                (self.monitor.smem + 0x10, 511, "item_count"),
                (self.monitor.smem + 0x28, 0x80904000, "global_physical_base"),
                (self.monitor.ptable, 0, "ptable_magic"),
                (self.monitor.partition, 0, "partition_magic"),
                (self.monitor.header + 0xc0, 0, "header_initialized")):
            with self.subTest(field=field):
                self.monitor = Monitor()
                self.monitor.memory[address] = value
                result = self.capture()
                self.assert_inconclusive(result, field)

    def test_allocated_heap_remains_valid(self):
        self.monitor.memory[self.monitor.partition + 12] = 0x80
        self.monitor.memory[self.monitor.partition + 16] = 0x1fbf00
        result = self.capture()
        self.assertIs(result["success_path_observed"], True)

    def test_invalid_allocation_frontiers_are_inconclusive(self):
        for uncached, cached in ((16, 0x1fc000), (0x100, 0x80),
                                 (32, 0x1fc001)):
            with self.subTest(uncached=uncached, cached=cached):
                self.monitor = Monitor()
                self.monitor.memory[self.monitor.partition + 12] = uncached
                self.monitor.memory[self.monitor.partition + 16] = cached
                result = self.capture()
                self.assert_inconclusive(result, "allocation_frontiers")

    def test_diagnostic_requires_symbols_and_nokaslr_before_launch(self):
        script = Path(__file__).with_name("diagnose_kernel.py")
        for extra, append in (([], "nokaslr"),
                              (["--legacy-printk-symbols", str(self.symbols)],
                               "console=tty0")):
            with self.subTest(extra=extra, append=append):
                out = self.root / "must-not-be-created"
                result = subprocess.run([
                    sys.executable, str(script), "--observe-smem-probe",
                    "--qemu", str(self.root / "missing-qemu"),
                    "--kernel", str(self.kernel), "--initrd", str(self.kernel),
                    "--append", append, "--out", str(out), *extra,
                ], capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2, result)
                self.assertIn("requires matching symbols and nokaslr",
                              result.stderr)
                self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
