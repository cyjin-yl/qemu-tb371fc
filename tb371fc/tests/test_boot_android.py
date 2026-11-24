#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host-only tests of stock-input preservation and bounded Android orchestration."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
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
    def __init__(self, process, clock, overlays, readonly=True):
        self.process, self.clock, self.overlays = process, clock, overlays
        self.commands, self.messages = [], []
        self.running, self.closed, self.readonly = False, False, readonly
        self.sock = SimpleNamespace(close=lambda: None)

    def command(self, name, arguments=None):
        self.commands.append(name)
        if name == "query-status":
            return {"running": self.running, "status": "running" if self.running else "paused"}
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
            self.process.returncode = 0
            self.closed = True
        return {}

    def receive(self, timeout):
        self.clock.now += max(timeout, .01)
        return []

    def drain(self):
        pass


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

    def run_main_with_printk(self, text, *, early_stop):
        """Exercise the CLI and real observation/finalization flow with fake I/O."""
        self.add_lun(0)
        process, clock = Process(), Clock()
        monitor = Monitor(process, clock, self.plan()["overlays"])
        captures, cleanup = [], []
        final_event = {"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}}

        def drain():
            cleanup.append("drain")
            monitor.messages.append(final_event)

        monitor.drain = drain
        monitor.sock = SimpleNamespace(close=lambda: cleanup.append("close"))

        def capture(command, symbols, out):
            self.assertFalse(monitor.running)
            captures.append(out)
            (out / "kernel-printk.log").write_text(text)
            return {"records": len(text.splitlines())}

        observe = boot.observe

        def run_observation(plan, **kwargs):
            return observe(plan, **kwargs, popen=lambda *a, **k: process,
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
        self.assertEqual(status, 2)
        overlays.assert_called_once()
        preserved.assert_called_once()
        self.assertTrue(report["preservation"]["preserved"])
        self.assertEqual(set(report["preservation"]["core_input_digests_unchanged"]),
                         {"kernel", "initrd", "dtb", "bootargs"})
        self.assertTrue(report["preservation"]["original_luns"][0]["stat_unchanged"])
        self.assertTrue(report["base_files_opened_read_only"])
        self.assertLess(monitor.commands.index("query-named-block-nodes"), monitor.commands.index("cont"))
        self.assertTrue(report["qemu_unchanged"])
        self.assertFalse(report["final_status"]["running"])
        self.assertEqual(monitor.commands[-1], "quit")
        self.assertEqual(cleanup, ["drain", "close"])
        self.assertEqual(json.loads((out / "qmp-messages.json").read_text()), [final_event])
        self.assertIn(final_event, report["qmp_events"])
        self.assertEqual(report["qemu_returncode"], 0)
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)
        self.assertFalse(report["stock_android_boot_passed"])
        self.assertEqual(report["stop_on_kernel_failure"], early_stop)
        self.assertTrue(any(row["path"].startswith("printk-") for row in report["logs"]))
        return report, monitor, captures

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

    def test_preservation_reports_modified_original_even_without_a_supplied_lun_hash(self):
        self.add_lun(0)
        plan = self.plan()
        Path(self.manifest["luns"][0]["path"]).write_bytes(b"x" * 8192)
        result = boot.verify_preserved(plan)
        self.assertFalse(result["preserved"])
        self.assertIsNone(result["original_luns"][0]["base_sha256_supplied"])
        self.assertFalse(result["original_luns"][0]["full_base_digest_rechecked_after_run"])


if __name__ == "__main__":
    unittest.main()
