#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only tests of stock-input preservation and bounded Android orchestration."""
import copy
import errno
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import boot_android as boot


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Process:
    pid = 4242

    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        if self.returncode is None:
            raise subprocess.TimeoutExpired("fake-qemu", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.killed = True
        self.returncode = -9


class Monitor:
    def __init__(self, process, clock, overlays, readonly=True, guest_shutdown=False):
        self.process, self.clock, self.overlays = process, clock, overlays
        self.commands, self.messages = [], []
        self.identifier = 0
        self.quit_event = True
        self.running, self.closed, self.readonly = False, False, readonly
        self.guest_shutdown_pending, self.shutdown = guest_shutdown, False
        self.sock = SimpleNamespace(close=lambda: None)

    def command(self, name, arguments=None):
        self.identifier += 1
        self.commands.append(name)
        if name == "query-status":
            return {"running": self.running,
                    "status": "shutdown" if self.shutdown else "running" if self.running else "paused"}
        if name == "query-named-block-nodes":
            result = []
            for item in self.overlays:
                n = item["lun"]
                result += [{"node-name": f"ufs-base-file-{n}", "ro": self.readonly},
                           {"node-name": f"ufs-base-{n}", "ro": self.readonly},
                           {"node-name": f"ufs-disk-{n}", "ro": False}]
            return result
        if name == "cont":
            self.running = True
        if name == "stop":
            self.running = False
        if name == "quit":
            self.messages.append({"return": {}, "id": self.identifier})
            if self.quit_event:
                self.messages.append(
                    {"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}})
            self.process.returncode = 0
            self.closed = True
        return {}

    def receive(self, timeout):
        self.clock.now += max(timeout, .01)
        if self.guest_shutdown_pending:
            self.guest_shutdown_pending = False
            self.shutdown, self.running = True, False
            events = [{"event": "WATCHDOG", "data": {"action": "reset"}},
                      {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-reset"}},
                      {"event": "STOP"}]
            self.messages.extend(events)
            return events
        return []

    def drain(self):
        return {"terminal": "eof", "errno": None, "error": None, "closed": True,
                "residual_bytes": 0, "message_count": len(self.messages), "prior_command_error": None}


class AndroidRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="android-runner-test-")
        self.root = Path(self.temp.name)
        self.out = self.root / "run"
        self.out.mkdir()
        self.manifest = {"schema": "tb371fc-android-boot-inputs-v1", "luns": [],
                         "metadata": {"scope": "original-android-direct-kernel-inputs"}}
        for name, value in (("kernel", b"original factory kernel"),
                            ("initrd", b"original factory ramdisk"),
                            ("dtb", b"captured full device tree"),
                            ("bootargs", b"androidboot.slot_suffix=_a skip_initramfs=0 rootwait")):
            p = self.root / name
            p.write_bytes(value)
            self.manifest[name] = {"path": str(p), "size": len(value),
                                   "sha256": hashlib.sha256(value).hexdigest()}

    def tearDown(self):
        self.temp.cleanup()

    def add_lun(self, n):
        path = self.root / f"factory,lun {n}.raw"
        path.write_bytes(bytes([n]) * 8192)
        self.manifest["luns"].append({"lun": n, "path": str(path), "format": "raw",
                                     "size": 8192, "logical_block_size": 4096,
                                     "logical_block_count": 2, "readonly_original": True})

    def plan(self):
        return boot.build_plan(self.manifest, qemu="/known/qemu", qemu_img="/known/qemu-img",
                               out=self.out, qmp_socket="/tmp/fake-qmp",
                               machine="tb371fc,rpmh-profile=off",
                               global_properties=["qcom-camera-gdsc.enabled-reference=on"])

    def test_exact_original_boot_inputs_and_arguments_are_preserved(self):
        plan = self.plan()
        cmd = plan["command"]
        self.assertEqual(cmd[cmd.index("-append") + 1],
                         "androidboot.slot_suffix=_a skip_initramfs=0 rootwait")
        for flag, key in (("-kernel", "kernel"), ("-initrd", "initrd"), ("-dtb", "dtb")):
            self.assertEqual(cmd[cmd.index(flag) + 1], self.manifest[key]["path"])
        self.assertFalse(plan["test_init_injected"])
        self.assertFalse(plan["bootargs_modified"])
        self.assertNotIn("nokaslr", cmd[cmd.index("-append") + 1])
        self.assertNotIn("rdinit=", cmd[cmd.index("-append") + 1])
        self.assertEqual(list(self.out.iterdir()), [])

    def test_multiple_luns_use_explicit_readonly_backing_nodes(self):
        self.add_lun(2)
        self.add_lun(0)
        plan = self.plan()
        self.assertEqual([x["lun"] for x in plan["overlays"]], [0, 2])
        cmd = plan["command"]
        nodes = [json.loads(cmd[i + 1]) for i, token in enumerate(cmd) if token == "-blockdev"]
        for n in (0, 2):
            by_name = {x["node-name"]: x for x in nodes}
            self.assertTrue(by_name[f"ufs-base-file-{n}"]["read-only"])
            self.assertTrue(by_name[f"ufs-base-{n}"]["read-only"])
            self.assertEqual(by_name[f"ufs-disk-{n}"]["backing"], f"ufs-base-{n}")
            self.assertEqual(by_name[f"ufs-base-file-{n}"]["filename"],
                             str(self.root / f"factory,lun {n}.raw"))
            self.assertIn(f"ufs-lu,bus=ufs0,drive=ufs-disk-{n},lun={n}", cmd)
        self.assertTrue(boot.verify_preserved(plan)["preserved"])

    def test_rejects_changed_kernel_before_execution(self):
        Path(self.manifest["kernel"]["path"]).write_bytes(b"different factory bytes")
        with self.assertRaisesRegex(ValueError, "kernel (size|digest) changed"):
            self.plan()

    def test_rejects_duplicate_lun_wrong_geometry_and_nonraw_backing(self):
        self.add_lun(0)
        original = copy.deepcopy(self.manifest)
        self.manifest["luns"].append(copy.deepcopy(self.manifest["luns"][0]))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.plan()
        self.manifest = copy.deepcopy(original)
        self.manifest["luns"][0]["logical_block_size"] = 512
        with self.assertRaisesRegex(ValueError, "4096"):
            self.plan()
        self.manifest = copy.deepcopy(original)
        self.manifest["luns"][0]["logical_block_count"] = 3
        with self.assertRaisesRegex(ValueError, "geometry"):
            self.plan()
        self.manifest = copy.deepcopy(original)
        self.manifest["luns"][0]["format"] = "qcow2"
        with self.assertRaisesRegex(ValueError, "raw"):
            self.plan()

    def test_rejects_nul_bootargs_and_implicit_machine(self):
        p = Path(self.manifest["bootargs"]["path"])
        p.write_bytes(b"androidboot=x\0init=/other")
        self.manifest["bootargs"].update(size=p.stat().st_size, sha256=boot.digest(p))
        with self.assertRaisesRegex(ValueError, "NUL-free"):
            self.plan()

    def test_overlay_creation_checks_real_info_and_never_reuses_output(self):
        self.add_lun(0)
        plan, calls = self.plan(), []

        def run(command, **kwargs):
            calls.append(command)
            self.assertNotIn("shell", kwargs)
            if command[1] == "create":
                Path(command[-1]).write_bytes(b"fake qcow2 header")
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "format": "qcow2", "virtual-size": 8192,
                "backing-filename-format": "raw",
                "full-backing-filename": self.manifest["luns"][0]["path"]}))

        receipt = boot.prepare_overlays(plan, run=run)
        self.assertEqual(len(receipt), 1)
        self.assertIn("-F", calls[0])
        with self.assertRaises(FileExistsError):
            boot.prepare_overlays(plan, run=run)
        self.assertTrue(boot.verify_preserved(plan)["preserved"])

    def test_rejects_overlay_with_wrong_backing(self):
        self.add_lun(0)
        plan = self.plan()

        def run(command, **kwargs):
            if command[1] == "create":
                Path(command[-1]).write_bytes(b"overlay")
                return SimpleNamespace(returncode=0, stderr="", stdout="")
            return SimpleNamespace(stdout=json.dumps({"format": "qcow2", "virtual-size": 8192,
                "backing-filename-format": "raw", "full-backing-filename": "/wrong/image"}))

        with self.assertRaisesRegex(ValueError, "unexpected backing"):
            boot.prepare_overlays(plan, run=run)

    def test_kernel_exit_or_property_text_does_not_claim_android_boot(self):
        report = boot.analyze_logs([("serial.log", "Linux version 4.19\n"
                    "Kernel command line: sys.boot_completed=1\n"
                    "Run /init as init process\n"
                    "QEMU exited 0\nsys.boot_completed=1\n")])
        self.assertFalse(report["android_userspace_observed"])
        self.assertFalse(report["android_boot_completed_observed"])
        self.assertNotIn("boot_completed_property", report["stages"])

    def test_android_init_and_actual_boot_action_supply_distinct_evidence(self):
        report = boot.analyze_logs([("printk.log", "[ 1.2] init: init first stage started!\n"
                    "[ 2.3] init: init second stage started!\n"
                    "[ 3.4] init: starting service 'zygote64'...\n"
                    "[ 9.0] init: processing action (sys.boot_completed=1) from (/system/etc/init/hw/init.rc:1)\n")])
        self.assertTrue(report["android_userspace_observed"])
        self.assertTrue(report["android_boot_completed_observed"])
        self.assertFalse(report["interactive_display_verified"])
        self.assertIn("zygote_service_start", report["stages"])

    def test_records_real_kernel_fault_without_calling_all_diagnostics_fatal(self):
        report = boot.analyze_logs([("printk.log", "[ 4.1] Internal error: synchronous external abort: 96000210 [#1]\n"
                    "[ 9.2] Kernel panic - not syncing: Attempted to kill init!\n"),
                    ("qemu.log", "device: unsupported read at 0x0ad07004\n")])
        self.assertEqual(report["first_kernel_failure"]["line"], 1)
        self.assertEqual(len(report["kernel_failures"]), 2)
        self.assertEqual(len(report["hardware_diagnostics"]), 2)

    def test_first_failure_uses_guest_time_and_deduplicates_ring_snapshots(self):
        early = "[ 4.1] Internal error: synchronous external abort: 96000210 [#1]\n"
        late = "[ 9.2] Kernel panic - not syncing: Attempted to kill init!\n"
        report = boot.analyze_logs([("serial.log", late), ("printk-1.log", early),
                                    ("printk-2.log", early + late)])
        self.assertEqual(report["first_kernel_failure"]["guest_time_seconds"], 4.1)
        self.assertEqual(report["first_kernel_failure"]["source"], "printk-1.log")
        self.assertEqual(len(report["kernel_failures"]), 2)

    def test_boot_completion_is_rejected_after_guest_panic_or_shutdown(self):
        self.add_lun(0)
        plan = self.plan()
        report = {"observation": {"android_boot_completed_observed": True, "kernel_failures": []},
                  "preservation": {"preserved": True}, "base_files_opened_read_only": True,
                  "qemu_returncode": 0, "qemu_unchanged": True,
                  "stop_reason": "time-bound", "logs": [],
                  "final_status": {"running": False, "status": "paused"},
                  "qmp_events": [{"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}}]}
        ack = {"return": {}, "id": 1}
        report["qmp_quit"] = {"request_id": 1, "message_start": 0, "acknowledged": True,
                              "response": ack, "messages": [ack] + report["qmp_events"]}
        report["qmp_drain"] = {"terminal": "eof", "errno": None, "error": None,
                               "closed": True, "residual_bytes": 0, "message_count": 2,
                               "prior_command_error": None}
        self.assertTrue(boot.stock_boot_passed(report, plan))
        for event in ({"event": "GUEST_PANICKED", "data": {"action": "pause"}},
                      {"event": "SHUTDOWN", "data": {"guest": True}},
                      {"event": "SHUTDOWN", "data": {}}):
            bad = copy.deepcopy(report)
            bad["qmp_events"].insert(0, event)
            self.assertFalse(boot.stock_boot_passed(bad, plan))
        for field, value in (("qemu_unchanged", False), ("qemu_returncode", -9),
                             ("stop_reason", "log-size-bound"),
                             ("stop_reason", "kernel-failure"),
                             ("early_kernel_failure", {"text": "Oops: retained snapshot evidence"}),
                             ("stop_reason", "qmp-closed"), ("stop_reason", "process-exit"),
                             ("qmp_drain_error", "malformed final event"),
                             ("host_terminated_qemu", True),
                             ("final_status", {"running": False, "status": "shutdown"}),
                             ("final_status", {"running": True}), ("final_status", {})):
            bad = copy.deepcopy(report)
            bad[field] = value
            self.assertFalse(boot.stock_boot_passed(bad, plan))
        bad = copy.deepcopy(report)
        bad["logs"] = [{"analysis_truncated": True}]
        self.assertFalse(boot.stock_boot_passed(bad, plan))
        no_storage = copy.deepcopy(plan)
        no_storage["overlays"] = []
        self.assertFalse(boot.stock_boot_passed(report, no_storage))

    def test_bounded_observation_pauses_before_printk_and_quits_cleanly(self):
        self.add_lun(0)
        plan, process, clock = self.plan(), Process(), Clock()
        monitor = Monitor(process, clock, plan["overlays"])
        capture_count = []

        def capture(command, symbols, out):
            self.assertFalse(monitor.running)
            capture_count.append(out)
            (out / "kernel-printk.log").write_text("Linux version 4.19\n")
            return {"records": 1}

        observed = boot.observe(plan, seconds=1, max_log_bytes=4096,
                    symbols="/matching/runtime-symbols", snapshot_interval=.4,
                    popen=lambda *a, **k: process,
                    qmp_open=lambda *a: monitor, clock=clock, capture=capture)
        self.assertTrue(observed["executed"])
        self.assertTrue(observed["base_files_opened_read_only"])
        self.assertEqual(observed["stop_reason"], "time-bound")
        self.assertEqual(observed["qemu_returncode"], 0)
        self.assertGreaterEqual(len(capture_count), 2)
        self.assertEqual(monitor.commands[-1], "quit")
        self.assertFalse(process.terminated)
        self.assertFalse(observed.get("stock_android_boot_passed", False))

    def run_main_with_printk(self, text, *, early_stop, parse_failure=None,
                            serial_text=None, expected_status=2, guest_shutdown=False):
        """Exercise the CLI and real observation/finalization flow with fake I/O."""
        self.add_lun(0)
        process, clock = Process(), Clock()
        monitor = Monitor(process, clock, self.plan()["overlays"], guest_shutdown=guest_shutdown)
        monitor.quit_event = False  # This fixture delivers SHUTDOWN during final drain.
        captures, cleanup = [], []
        final_event = {"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}}
        guest_events = ([{"event": "WATCHDOG", "data": {"action": "reset"}},
                         {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-reset"}},
                         {"event": "STOP"}] if guest_shutdown else [])

        def drain():
            cleanup.append("drain")
            monitor.messages.append(final_event)
            return {"terminal": "eof", "errno": None, "error": None, "closed": True,
                    "residual_bytes": 0, "message_count": len(monitor.messages),
                    "prior_command_error": None}

        monitor.drain = drain
        monitor.sock = SimpleNamespace(close=lambda: cleanup.append("close"))

        def capture(command, symbols, out):
            self.assertFalse(monitor.running)
            captures.append(out)
            if parse_failure == "all" or (parse_failure == "first" and len(captures) == 1):
                # Exercise the real symbol parser, not an injected error field or
                # a failed QMP command. The fixture symbol file is intentionally
                # incomplete; the QMP transport remains healthy.
                return boot.capture_kernel_log(command, symbols, out)
            (out / "kernel-printk.log").write_text(text)
            return {"records": len(text.splitlines())}

        observe = boot.observe

        def launch(*args, **kwargs):
            if serial_text is not None:
                kwargs["stdout"].write(serial_text.encode())
                kwargs["stdout"].flush()
            return process

        def run_observation(plan, **kwargs):
            return observe(plan, **kwargs, popen=launch,
                           qmp_open=lambda *a: monitor, clock=clock, capture=capture)

        manifest = self.root / "prepared-manifest.json"
        manifest.write_text(json.dumps(self.manifest))
        symbols = self.root / "runtime-symbols"
        symbols.write_text("fixture: capture is supplied by the test\n")
        out = self.root / "observed-run"
        argv = ["--manifest", str(manifest), "--qemu", sys.executable,
                "--qemu-img", sys.executable, "--out", str(out),
                "--machine", "tb371fc,rpmh-profile=off",
                "--legacy-printk-symbols", str(symbols),
                "--symbols-kernel-sha256", self.manifest["kernel"]["sha256"]]
        if early_stop:
            argv.append("--stop-on-kernel-failure")
        with (patch("android_boot_inputs.load_prepared_manifest", return_value=self.manifest),
              patch.object(boot, "prepare_overlays", return_value=[]) as overlays,
              patch.object(boot, "observe", side_effect=run_observation),
              patch.object(boot, "verify_preserved", wraps=boot.verify_preserved) as preserved,
              patch("builtins.print")):
            status = boot.main(argv)
        report = json.loads((out / "result.json").read_text())
        self.assertEqual(status, expected_status)
        overlays.assert_called_once()
        preserved.assert_called_once()
        self.assertTrue(report["preservation"]["preserved"])
        self.assertEqual(set(report["preservation"]["core_input_digests_unchanged"]),
                         {"kernel", "initrd", "dtb", "bootargs"})
        self.assertTrue(report["preservation"]["original_luns"][0]["stat_unchanged"])
        self.assertTrue(report["base_files_opened_read_only"])
        self.assertLess(monitor.commands.index("query-named-block-nodes"), monitor.commands.index("cont"))
        self.assertTrue(report["qemu_unchanged"])
        self.assertEqual(report["final_status"],
                         {"running": False, "status": "shutdown" if guest_shutdown else "paused"})
        self.assertEqual(monitor.commands[-1], "quit")
        self.assertEqual(cleanup, ["drain", "close"])
        saved = json.loads((out / "qmp-messages.json").read_text())
        self.assertEqual(saved, guest_events +
                         [{"return": {}, "id": monitor.identifier}, final_event])
        self.assertIn(final_event, report["qmp_events"])
        self.assertTrue(report["qmp_shutdown_normal"])
        self.assertNotIn("qmp_drain_error", report)
        self.assertEqual(report["qemu_returncode"], 0)
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)
        self.assertEqual(report["stock_android_boot_passed"], expected_status == 0)
        self.assertEqual(report["stop_on_kernel_failure"], early_stop)
        if parse_failure != "all":
            self.assertTrue(any(row["path"].startswith("printk-") for row in report["logs"]))
        return report, monitor, captures

    def test_printk_symbol_parse_failure_rejects_serial_boot_completion(self):
        serial = ("[ 4.0] init: init second stage started!\n"
                  "[ 5.0] init: processing action (sys.boot_completed=1) from (/system/etc/init/hw/init.rc:1)\n")
        report, monitor, captures = self.run_main_with_printk(
            "Linux version 4.19\n", early_stop=True, parse_failure="all",
            serial_text=serial, expected_status=1)
        self.assertTrue(report["observation"]["android_boot_completed_observed"])
        self.assertEqual(report["outcome"], "diagnostic-failure")
        self.assertEqual(report["elapsed_seconds"], 120)
        self.assertEqual(report["stop_reason"], "time-bound")
        self.assertNotIn("early_kernel_failure", report)
        self.assertNotIn("error", report)
        self.assertIsNone(report["qmp_drain"]["prior_command_error"])
        self.assertGreater(len(captures), 2)
        self.assertTrue(all("matching canonical kernel symbols" in row["error"]
                            for row in report["printk"]))

    def test_later_printk_success_does_not_erase_symbol_parse_failure(self):
        report, monitor, captures = self.run_main_with_printk(
            "[ 4.0] init: init second stage started!\n"
            "[ 5.0] init: processing action (sys.boot_completed=1) from (/system/etc/init/hw/init.rc:1)\n",
            early_stop=False, parse_failure="first", expected_status=1)
        self.assertEqual(report["outcome"], "diagnostic-failure")
        self.assertTrue(report["observation"]["android_boot_completed_observed"])
        self.assertEqual(report["elapsed_seconds"], 120)
        self.assertIn("matching canonical kernel symbols", report["printk"][0]["error"])
        self.assertGreater(len(captures), 2)
        self.assertTrue(all("capture" in row and "error" not in row
                            for row in report["printk"][1:]))
        self.assertIsNone(report["qmp_drain"]["prior_command_error"])

    def test_successful_printk_capture_allows_normal_boot_observation(self):
        report, monitor, captures = self.run_main_with_printk(
            "[ 4.0] init: init second stage started!\n"
            "[ 5.0] init: processing action (sys.boot_completed=1) from (/system/etc/init/hw/init.rc:1)\n",
            early_stop=True, expected_status=0)
        self.assertEqual(report["outcome"], "android-boot-complete-observed")
        self.assertTrue(all("capture" in row and "error" not in row
                            for row in report["printk"]))

    def test_opt_in_kernel_failure_runs_complete_finalization(self):
        report, monitor, captures = self.run_main_with_printk(
            "[ 3.5] Internal error: synchronous external abort: 96000210 [#1]\n"
            "[ 4.0] init: init second stage started!\n"
            "[ 5.0] init: processing action (sys.boot_completed=1) from (/system/etc/init/hw/init.rc:1)\n",
            early_stop=True)
        self.assertLess(report["elapsed_seconds"], 120)
        self.assertEqual(report["stop_reason"], "kernel-failure")
        self.assertEqual(report["outcome"], "kernel-failure")
        self.assertEqual(report["early_kernel_failure"]["guest_time_seconds"], 3.5)
        self.assertTrue(report["observation"]["android_boot_completed_observed"])
        self.assertEqual(len(captures), 2)  # Scheduled capture, then normal final capture.
        self.assertEqual(monitor.commands.count("cont"), 1)

    def test_guest_reset_shutdown_keeps_kernel_failure_after_clean_host_quit(self):
        report, monitor, captures = self.run_main_with_printk(
            "[ 3.5] Internal error: synchronous external abort: 96000210 [#1]\n"
            "[ 3.6] CPU: 0 PID: 1 Comm: init\n"
            "[ 3.7] Kernel panic - not syncing: Attempted to kill init! exitcode=0x0000000b\n"
            "[ 3.9] init: init second stage started!\n"
            "[ 4.0] init: processing action (sys.boot_completed=1) from (/system/etc/init/hw/init.rc:1)\n",
            early_stop=True, guest_shutdown=True, expected_status=2)
        self.assertEqual(report["outcome"], "kernel-failure")
        self.assertEqual(report["stop_reason"], "guest-shutdown-or-panic-event")
        self.assertEqual(report["final_status"], {"running": False, "status": "shutdown"})
        self.assertNotIn("error", report)
        self.assertNotIn("host_terminated_qemu", report)
        self.assertTrue(report["qmp_shutdown_normal"])
        self.assertFalse(report["stock_android_boot_passed"])
        self.assertEqual(monitor.commands.count("cont"), 1)
        self.assertEqual(len(captures), 1)
        self.assertTrue(report["observation"]["android_boot_completed_observed"])
        self.assertEqual(len(report["observation"]["kernel_failures"]), 2)
        self.assertTrue(any("Attempted to kill init!" in row["text"]
                            for row in report["observation"]["kernel_failures"]))
        guest_reset = {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-reset"}}
        self.assertIn(guest_reset, report["qmp_events"])
        self.assertNotIn(guest_reset, report["qmp_quit"]["messages"])

    def test_shutdown_does_not_hide_printk_symbol_failure(self):
        report, monitor, captures = self.run_main_with_printk(
            "", early_stop=True, parse_failure="all", guest_shutdown=True,
            serial_text="[ 4.0] init: init second stage started!\n"
                        "[ 5.0] init: processing action (sys.boot_completed=1) from (fixture:1)\n",
            expected_status=1)
        self.assertTrue(report["qmp_shutdown_normal"])
        self.assertTrue(boot.printk_capture_failed(report))
        self.assertEqual(report["outcome"], "diagnostic-failure")
        self.assertFalse(report["stock_android_boot_passed"])
        self.assertEqual(len(captures), 1)
        self.assertEqual(monitor.commands.count("cont"), 1)

    def test_opt_in_warning_continues_for_default_observation_time(self):
        report, monitor, captures = self.run_main_with_printk(
            "[ 2.0] WARNING: CPU: 0 PID: 1 at drivers/clk/clk-branch.c:123\n"
            "[ 3.0] device: unsupported read at 0x09981004\n", early_stop=True)
        self.assertEqual(report["elapsed_seconds"], 120)
        self.assertEqual(report["stop_reason"], "time-bound")
        self.assertNotIn("early_kernel_failure", report)
        self.assertEqual(report["observation"]["kernel_failures"], [])
        self.assertGreater(len(captures), 2)
        self.assertGreater(monitor.commands.count("cont"), 1)

    def test_kernel_failure_does_not_stop_early_by_default(self):
        report, monitor, captures = self.run_main_with_printk(
            "[ 3.5] Oops: synchronous external abort [#1]\n", early_stop=False)
        self.assertEqual(report["elapsed_seconds"], 120)
        self.assertEqual(report["stop_reason"], "time-bound")
        self.assertEqual(report["outcome"], "kernel-failure")
        self.assertNotIn("early_kernel_failure", report)
        self.assertGreater(len(captures), 2)
        self.assertGreater(monitor.commands.count("cont"), 1)

    def test_writable_factory_node_is_rejected_before_guest_runs(self):
        self.add_lun(0)
        plan, process, clock = self.plan(), Process(), Clock()
        monitor = Monitor(process, clock, plan["overlays"], readonly=False)
        observed = boot.observe(plan, seconds=1, max_log_bytes=4096,
                    popen=lambda *a, **k: process,
                    qmp_open=lambda *a: monitor, clock=clock)
        self.assertEqual(observed["stop_reason"], "runner-error")
        self.assertNotIn("cont", monitor.commands)
        self.assertTrue(process.terminated)
        self.assertFalse(observed["base_files_opened_read_only"])

    def test_monitor_connection_failure_cleans_only_owned_process(self):
        process, clock = Process(), Clock()

        def fail(*args):
            raise TimeoutError("monitor connection timeout")

        observed = boot.observe(self.plan(), seconds=1, max_log_bytes=4096,
                    popen=lambda *a, **k: process, qmp_open=fail, clock=clock)
        self.assertTrue(process.terminated)
        self.assertEqual(observed["qemu_returncode"], -15)
        self.assertEqual(observed["stop_reason"], "runner-error")

    def test_unstructured_drain_result_still_saves_messages_and_closes(self):
        plan, process, clock = self.plan(), Process(), Clock()
        monitor = Monitor(process, clock, plan["overlays"])
        monitor.drain = lambda: None
        closed = []
        monitor.sock.close = lambda: closed.append(True)
        report = boot.observe(plan, seconds=1, max_log_bytes=4096,
                              popen=lambda *a, **k: process,
                              qmp_open=lambda *a: monitor, clock=clock)
        self.assertFalse(report["qmp_shutdown_normal"])
        self.assertIn("terminal receipt", report["qmp_drain_error"])
        self.assertEqual(json.loads((self.out / "qmp-messages.json").read_text()), monitor.messages)
        self.assertEqual(closed, [True])

    def test_preservation_reports_modified_original_even_without_a_supplied_lun_hash(self):
        self.add_lun(0)
        plan = self.plan()
        original = Path(self.manifest["luns"][0]["path"])
        before = original.stat()
        original.write_bytes(b"x" * 8192)
        # Post-run LU preservation compares file identity and timestamps, not a
        # full digest. Make this real write observable even on coarse clocks.
        os.utime(original, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
        result = boot.verify_preserved(plan)
        self.assertFalse(result["preserved"])
        self.assertIsNone(result["original_luns"][0]["base_sha256_supplied"])
        self.assertFalse(result["original_luns"][0]["full_base_digest_rechecked_after_run"])


class QMPShutdownTests(unittest.TestCase):
    """Real local AF_UNIX transport, synthetic process/Android evidence; no VM."""

    HOST_QUIT = {"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}}
    GUEST_RESET = {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-reset"}}

    @staticmethod
    def wire(message):
        return json.dumps(message).encode() + b"\n"

    def observe_peer(self, *, reset=False, ack="valid", tail=None, event_before_ack=False,
                     early_reset=False, exit_code=0, forced=False, old_host_event=False,
                     pause_tail=b"", capture_failure=False, guest_shutdown=False,
                     guest_events=None, shutdown_running=False, final_status=None):
        client, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        finish = threading.Event()
        quit_received = threading.Event()
        errors = []

        class PeerProcess(Process):
            def wait(self, timeout):
                finish.set()
                if not quit_received.is_set() and self.returncode is None:
                    raise subprocess.TimeoutExpired("synthetic-qmp-peer", timeout)
                worker.join(timeout)
                if worker.is_alive() or (forced and not self.terminated):
                    raise subprocess.TimeoutExpired("synthetic-qmp-peer", timeout)
                return super().wait(timeout)

            def terminate(self):
                super().terminate()
                try:
                    peer.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        process = PeerProcess()

        def serve():
            running = False
            shutdown = False
            status_queries = 0
            try:
                while True:
                    frame = b""
                    while True:
                        byte = peer.recv(1)
                        if not byte:
                            return
                        frame += byte
                        if byte == b"\n":
                            request = json.loads(frame)
                            break
                        # Leaving only the request's newline unread makes Linux
                        # AF_UNIX close report ECONNRESET after queued replies.
                        # This is a controlled transport fixture, not a claim
                        # about why the actual QEMU process reset its socket.
                        if (reset or early_reset) and byte == b"}":
                            try:
                                request = json.loads(frame)
                            except ValueError:
                                continue
                            if early_reset or request.get("execute") == "quit":
                                break
                    name, identifier = request["execute"], request["id"]
                    if early_reset:
                        return
                    if name == "query-status":
                        status_queries += 1
                        if old_host_event and status_queries == 1:
                            peer.sendall(self.wire(self.HOST_QUIT))
                        returned = (copy.deepcopy(final_status)
                                    if status_queries > 1 and final_status is not None else
                                    {"running": shutdown_running, "status": "shutdown"} if shutdown else
                                    {"running": running, "status": "running" if running else "paused"})
                    elif name == "query-named-block-nodes":
                        returned = [{"node-name": "ufs-base-file-0", "ro": True},
                                    {"node-name": "ufs-base-0", "ro": True},
                                    {"node-name": "ufs-disk-0", "ro": False}]
                    elif name == "cont":
                        running, returned = True, {}
                        shutdown = guest_shutdown
                    elif name == "stop":
                        running, returned = False, {}
                    elif name == "query-version" and capture_failure:
                        peer.sendall(b"[]\n")
                        continue
                    elif name == "quit":
                        quit_received.set()
                        response = {"return": {}, "id": identifier}
                        if ack == "missing-return":
                            del response["return"]
                        elif ack == "wrong-id":
                            response["id"] += 1
                        elif ack == "boolean-id":
                            response["id"] = True
                        elif ack == "list-return":
                            response["return"] = []
                        elif ack == "mixed-envelope":
                            response["event"] = "STOP"
                        if event_before_ack:
                            peer.sendall(self.wire(self.HOST_QUIT))
                        if ack != "absent":
                            peer.sendall(self.wire(response))
                        if ack in ("absent", "wrong-id"):
                            return
                        if not finish.wait(2):
                            raise TimeoutError("test never reached process wait")
                        if tail is None:
                            if not event_before_ack:
                                peer.sendall(self.wire(self.HOST_QUIT))
                        else:
                            peer.sendall(tail)
                        if ack == "duplicate-tail":
                            peer.sendall(self.wire(response))
                        return
                    else:
                        raise AssertionError(name)
                    reply = self.wire({"return": returned, "id": identifier})
                    if name == "cont" and guest_shutdown:
                        events = guest_events if guest_events is not None else [
                            {"event": "WATCHDOG", "data": {"action": "reset"}},
                            self.GUEST_RESET,
                            {"event": "STOP"}]
                        reply += b"".join(self.wire(event) for event in events)
                    if name == "query-status" and status_queries == 2:
                        reply += pause_tail
                    peer.sendall(reply)
            except Exception as exc:
                errors.append(repr(exc))
            finally:
                peer.close()
                if not forced and not process.terminated:
                    process.returncode = exit_code

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory(prefix="qmp-shutdown-test-") as directory:
                plan = {"out": directory, "command": ["synthetic-qmp-peer"],
                        "qmp_socket": "unused", "overlays": [{"lun": 0}]}
                report = boot.observe(plan, seconds=.01, max_log_bytes=4096,
                                      popen=lambda *a, **k: process,
                                      qmp_open=lambda *a: boot.BoundedQMP(client),
                                      symbols="synthetic-symbols" if capture_failure else None,
                                      capture=lambda command, *args: command("query-version"))
                saved = json.loads((Path(directory) / "qmp-messages.json").read_text())
                if "qmp_quit" in report:
                    quit_info = report["qmp_quit"]
                    self.assertEqual(quit_info["messages"], saved[quit_info["message_start"]:])
                # These are explicitly synthetic eligibility inputs, not
                # observations of Android or a claim that Android booted.
                report.update(observation={"android_boot_completed_observed": True,
                                           "kernel_failures": []},
                              preservation={"preserved": True}, qemu_unchanged=True, logs=[])
                return report, plan
        finally:
            finish.set()
            client.close()
            peer.close()
            worker.join(2)
            self.assertFalse(worker.is_alive(), "socket fixture did not finish")
            self.assertEqual(errors, [])

    def test_complete_quit_tail_eof_and_real_reset_qualify(self):
        for reset in (False, True):
            with self.subTest(reset=reset):
                report, plan = self.observe_peer(reset=reset, event_before_ack=not reset)
                self.assertTrue(report["qmp_quit"]["acknowledged"])
                self.assertEqual(report["qmp_drain"]["terminal"], "reset" if reset else "eof")
                self.assertEqual(report["qmp_drain"]["errno"], errno.ECONNRESET if reset else None)
                if reset:
                    self.assertIn("Connection reset", report["qmp_drain"]["transport_error"])
                self.assertEqual(report["qmp_drain"]["residual_bytes"], 0)
                self.assertTrue(report["qmp_shutdown_normal"])
                self.assertNotIn("qmp_drain_error", report)
                self.assertTrue(boot.stock_boot_passed(report, plan))

    def test_guest_shutdown_stop_noop_still_requires_complete_host_quit(self):
        for reset in (False, True):
            with self.subTest(reset=reset):
                report, plan = self.observe_peer(guest_shutdown=True, reset=reset,
                                                  event_before_ack=not reset)
                self.assertEqual(report["final_status"], {"running": False, "status": "shutdown"})
                self.assertTrue(report["qmp_quit"]["acknowledged"])
                self.assertIn(self.HOST_QUIT, report["qmp_quit"]["messages"])
                self.assertEqual(report["qmp_drain"]["terminal"], "reset" if reset else "eof")
                self.assertEqual(report["qmp_drain"]["residual_bytes"], 0)
                self.assertIsNone(report["qmp_drain"]["prior_command_error"])
                self.assertEqual(report["qemu_returncode"], 0)
                self.assertIn(self.GUEST_RESET, report["qmp_events"])
                self.assertNotIn(self.GUEST_RESET, report["qmp_quit"]["messages"])
                self.assertNotIn("error", report)
                self.assertNotIn("host_terminated_qemu", report)
                self.assertNotIn("qmp_drain_error", report)
                self.assertTrue(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_shutdown_cannot_reuse_old_guest_event_as_quit_confirmation(self):
        report, plan = self.observe_peer(guest_shutdown=True, tail=b"")
        self.assertEqual(report["final_status"]["status"], "shutdown")
        self.assertTrue(report["qmp_quit"]["acknowledged"])
        self.assertEqual(report["qmp_drain"]["terminal"], "eof")
        self.assertIn(self.GUEST_RESET, report["qmp_events"])
        self.assertNotIn(self.GUEST_RESET, report["qmp_quit"]["messages"])
        self.assertNotIn(self.HOST_QUIT, report["qmp_quit"]["messages"])
        self.assertFalse(report["qmp_shutdown_normal"])
        self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_shutdown_without_current_guest_evidence_is_rejected(self):
        guest = {"event": "SHUTDOWN", "data": {"guest": True, "reason": "guest-reset"}}
        sequences = [[], [self.HOST_QUIT],
                     [{"event": "SHUTDOWN", "data": {"guest": True, "reason": "host-signal"}}],
                     [guest, {"event": "RESUME"}], [guest, {"event": "RESET"}],
                     [guest, {"event": "GUEST_PANICKED", "data": {"action": "pause"}}],
                     [guest, {"event": "WATCHDOG", "data": {"action": "reset"}}]]
        for events in sequences:
            with self.subTest(events=events):
                report, plan = self.observe_peer(guest_shutdown=True, guest_events=events)
                self.assertNotIn("qmp_quit", report)
                self.assertIn("confirmed stopped state", report["error"])
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_running_or_inconsistent_final_status_never_sends_quit(self):
        for status in ({"status": "shutdown", "running": True},
                       {"status": "running", "running": False},
                       {"status": "paused", "running": True},
                       {"status": "shutdown", "running": 0}, {"status": "shutdown"}):
            with self.subTest(status=status):
                report, plan = self.observe_peer(guest_shutdown=True, final_status=status)
                self.assertEqual(report["final_status"], status)
                self.assertIn("confirmed stopped state", report["error"])
                self.assertNotIn("qmp_quit", report)
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_guest_shutdown_does_not_excuse_incomplete_or_failed_cleanup(self):
        for options in ({"ack": "absent"},
                        {"tail": self.wire({"event": "SHUTDOWN",
                                            "data": {"guest": False, "reason": "host-signal"}})},
                        {"tail": self.wire(self.HOST_QUIT) + b'{"event":"STOP"'},
                        {"capture_failure": True}):
            with self.subTest(options=options):
                report, plan = self.observe_peer(guest_shutdown=True, **options)
                self.assertEqual(report["final_status"]["status"], "shutdown")
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))
                self.assertTrue(report.get("error") or report.get("qmp_drain_error"))
                if options.get("capture_failure"):
                    self.assertIn("not an object", report["printk"][0]["error"])
                    self.assertIsNotNone(report["qmp_drain"]["prior_command_error"])

    def test_partial_event_after_ack_rejected_at_eof_and_reset(self):
        tail = self.wire(self.HOST_QUIT) + b'{"event":"GUEST_PANICKED"'
        for reset in (False, True):
            with self.subTest(reset=reset):
                report, plan = self.observe_peer(reset=reset, tail=tail)
                self.assertTrue(report["qmp_quit"]["acknowledged"])
                self.assertEqual(report["qmp_drain"]["terminal"], "reset" if reset else "eof")
                self.assertGreater(report["qmp_drain"]["residual_bytes"], 0)
                self.assertIn("partial QMP frame", report["qmp_drain_error"])
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_quit_ack_must_be_unique_matching_and_empty_object(self):
        for ack in ("absent", "missing-return", "wrong-id", "boolean-id", "list-return",
                    "mixed-envelope", "duplicate-tail"):
            with self.subTest(ack=ack):
                report, plan = self.observe_peer(ack=ack)
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))
                self.assertTrue(report.get("error") or report.get("qmp_drain_error"))

    def test_malformed_or_contradictory_quit_tail_is_rejected(self):
        tails = [b'{invalid JSON}\n', self.wire([])]
        tails += [self.wire({"event": name}) for name in ("RESUME", "RESET", "GUEST_PANICKED")]
        tails.append(self.wire({"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-error"}}))
        for tail in tails:
            with self.subTest(tail=tail):
                report, plan = self.observe_peer(tail=self.wire(self.HOST_QUIT) + tail)
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_old_host_shutdown_event_cannot_qualify_this_quit(self):
        report, plan = self.observe_peer(old_host_event=True, tail=b"")
        self.assertIn(self.HOST_QUIT, report["qmp_events"])
        self.assertNotIn(self.HOST_QUIT, report["qmp_quit"]["messages"])
        self.assertFalse(report["qmp_shutdown_normal"])
        self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_pause_reply_followed_by_resume_in_same_read_is_rejected(self):
        report, plan = self.observe_peer(pause_tail=self.wire({"event": "RESUME"}))
        self.assertFalse(report["qmp_shutdown_normal"])
        self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_capture_command_failure_cannot_be_erased_by_later_clean_quit(self):
        report, plan = self.observe_peer(capture_failure=True)
        self.assertIn("not an object", report["printk"][0]["error"])
        self.assertNotIn("error", report)
        self.assertTrue(report["qmp_quit"]["acknowledged"])
        self.assertEqual(report["qmp_drain"]["terminal"], "eof")
        self.assertEqual(report["qmp_drain"]["prior_command_error"]["type"], "ValueError")
        self.assertFalse(report["qmp_shutdown_normal"])
        self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_reset_before_quit_is_not_a_normal_shutdown(self):
        report, plan = self.observe_peer(early_reset=True)
        self.assertIn("ConnectionResetError", report["error"])
        self.assertNotIn("qmp_quit", report)
        self.assertFalse(report["qmp_shutdown_normal"])
        self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_nonzero_and_forced_process_exit_are_rejected(self):
        for options in ({"exit_code": 7}, {"forced": True}):
            with self.subTest(options=options):
                report, plan = self.observe_peer(**options)
                self.assertTrue(report["qmp_quit"]["acknowledged"])
                self.assertNotEqual(report["qemu_returncode"], 0)
                self.assertFalse(report["qmp_shutdown_normal"])
                self.assertFalse(boot.stock_boot_passed(report, plan))

    def test_open_empty_socket_drain_times_out(self):
        client, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            result = boot.BoundedQMP(client).drain(timeout=.01)
            self.assertEqual(result["terminal"], "timeout")
            self.assertEqual(result["residual_bytes"], 0)
            self.assertFalse(result["closed"])
            self.assertIn("TimeoutError", result["error"])
        finally:
            client.close()
            peer.close()

    def test_frame_bound_checked_before_final_fragment_is_split(self):
        client, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            monitor = boot.BoundedQMP(client)
            payload = self.wire({"event": "STOP", "data": {"padding": "x" * 64}})
            with patch.object(boot, "MAX_QMP_BUFFER", 64):
                peer.sendall(payload[:60])
                self.assertEqual(monitor.receive(.1), [])
                peer.sendall(payload[60:])
                peer.close()
                result = monitor.drain()
            self.assertEqual(result["terminal"], "error")
            self.assertIn("bound exceeded", result["error"])
            self.assertEqual(monitor.messages, [])
        finally:
            client.close()
            peer.close()


if __name__ == "__main__":
    unittest.main()
