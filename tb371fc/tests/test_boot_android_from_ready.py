#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Host tests of six-LUN handoff using real boot/DT/preparation formats.

All bytes and generation receipts in this module are synthetic fixtures.  The
actual input parser and prepared-manifest validator are exercised; only the
unavailable QEMU execution boundary is replaced.  No fixture proves that an
original Android image boots or that a live acquisition is snapshot-consistent.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import android_boot_inputs as inputs
import boot_android_from_ready as ready_boot
from test_android_boot_inputs import boot_fixture, fdt_fixture


FALSE_QUALIFICATIONS = (
    "snapshot_consistent", "source_immutable_claim_verified",
    "pristine_factory_images", "restore_tested", "android_boot_tested",
)
RUNNER_NAMES = (
    "boot_android.py", "android_boot_inputs.py", "boot_qmp.py", "legacy_printk.py",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def false_flags() -> dict:
    return {key: False for key in FALSE_QUALIFICATIONS}


class ReadyBootTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="six-lun-ready-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.generation = self.root / "generation2"
        self.generation.mkdir()
        self.out = self.root / "attempt"
        self.lock = self.root / "native-run.lock"
        self.boot = self.write("boot.img", boot_fixture()[0])
        self.dtb = self.write("full.dtb", fdt_fixture())
        self.lun_paths = [self.write(f"raw/lun{n}.raw", bytes([0x10 + n]) * 8192)
                          for n in range(6)]
        self.sources = [{"path": "/dev/block/sd" + chr(ord("a") + n),
                         "bytes": 8192, "logical_block_size": 4096,
                         "kind": "block-device", "stat_device": 17,
                         "inode": 1000 + n, "rdev": 2048 + 16 * n}
                        for n in range(6)]
        self.storage = {
            "schema": inputs.STORAGE_SCHEMA, "slot": "_a",
            "luns": [{"lun": n, "path": str(path), "format": "raw",
                      "logical_block_size": 4096, "logical_block_count": 2,
                      "descriptor": {"logical_block_size_exponent": 12,
                                     "logical_block_count": 2},
                      "sha256": digest(path)}
                     for n, path in enumerate(self.lun_paths)],
            **false_flags(), "all_backups_complete": False,
        }
        self.storage_path = self.json_file("storage.json", self.storage)
        self.prepared = inputs.prepare_android_inputs(
            self.boot, self.dtb, self.generation / "prepared",
            storage_manifest=self.storage_path, verify_lun_hashes=True)
        self.manifest_path = self.generation / "prepared/manifest.json"
        self.plan_hash = hashlib.sha256(b"synthetic generation2 plan").hexdigest()
        self.source_binding_hash = hashlib.sha256(b"synthetic source binding").hexdigest()
        self.super_manifest = self.json_file("captures/super/manifest.json", {
            "source": self.sources[0], "ranges": [
                {"label": "super", "offset": 0, "length": 4096,
                 "classification": "live-mutable"}]})
        self.tail_manifest = self.json_file("captures/lun0-tail/manifest.json", {
            "source": self.sources[0], "ranges": [
                {"label": "tail", "offset": 4096, "length": 4096,
                 "classification": "live-mutable"}]})
        self.host_tail_manifest = self.root / "host-captures-generation2/lun0-tail/manifest.json"
        self.host_tail_manifest.parent.mkdir(parents=True)
        self.host_tail_manifest.write_bytes(self.tail_manifest.read_bytes())
        self.baseline = {
            "schema": "tb371fc-reboot-resume-verification-v1", "verified": True,
            "plan_sha256": hashlib.sha256(b"synthetic baseline plan").hexdigest(),
            "captures": {"super": {"source": self.sources[0], "chunks": 1,
                                   "manifest_sha256": digest(self.super_manifest),
                                   "ranges": [{"label": "super", "offset": 0,
                                               "length": 4096,
                                               "classification": "live-mutable"}]}},
            "raws": [{**copy.deepcopy(row),
                      "verification_receipt_sha256": hashlib.sha256(
                          f"baseline LUN {row['lun']}".encode()).hexdigest()}
                     for row in self.storage["luns"][1:]],
            **false_flags(),
        }
        for n in range(1, 6):
            ranges = [{"label": f"lun{n}", "offset": 0, "length": 8192,
                       "classification": "live-mutable"}]
            capture_path = self.json_file(f"captures/lun{n}/manifest.json",
                                         {"source": self.sources[n], "ranges": ranges})
            self.baseline["captures"][f"lun{n}"] = {
                "manifest_sha256": digest(capture_path), "source": self.sources[n],
                "chunks": 1, "ranges": ranges}
        self.baseline_path = self.json_file("baseline-result.json", self.baseline)
        self.assembly = {
            "schema": "tb371fc-whole-lun-assembly-v1",
            "status": "verified-whole-lun-assembly",
            "source": self.sources[0],
            "raw": {"path": str(self.lun_paths[0]), "format": "raw", "bytes": 8192,
                    "sha256": digest(self.lun_paths[0])},
            "whole_source_bytes_captured": True,
            "coverage_nonoverlapping": True, "raw_readback_verified": True,
            "pristine_factory": False, "chunks_verified": 2,
            "source_manifests": [
                {**self.reference(self.super_manifest),
                 "ranges": [{"label": "super", "offset": 0, "length": 4096,
                             "classification": "live-mutable"}]},
                {**self.reference(self.tail_manifest),
                 "ranges": [{"label": "tail", "offset": 4096, "length": 4096,
                             "classification": "live-mutable"}]}], **false_flags(),
        }
        self.assembly_path = self.json_file("lun0.raw.manifest.json", self.assembly)
        self.completion = {
            "generation2_capture_complete": True, "completed_jobs": 1,
            "assembly_manifests": 2, "jobs_plan_sha256": self.plan_hash,
            "source_binding_sha256": self.source_binding_hash,
            "source": self.sources[0], "jobs": [{"name": "lun0-tail",
                "manifest": str(self.host_tail_manifest),
                "manifest_sha256": digest(self.tail_manifest), "chunks": 1}],
            "chunk_bytes_rehashed_at_finalization": False,
            **false_flags(), "all_backups_complete": False,
        }
        self.completion_path = self.json_file("generation2-complete.json", self.completion)
        self.ready = {
            "prepared_manifest": str(self.manifest_path),
            "all_six_original_luns_assembled": True,
            "generation2_jobs_verified": 1, "assembly_manifests": 2,
            "generation_plan_sha256": self.plan_hash,
            "source_binding_sha256": self.source_binding_hash,
            "generation_completion_sha256": digest(self.completion_path),
            "new_lun0_assembly_receipt_sha256": digest(self.assembly_path),
            "baseline_verification": str(self.baseline_path),
            "old_partials_preserved": True,
            **false_flags(), "all_backups_complete": False,
        }
        self.ready_path = self.json_file("ready.json", self.ready)
        self.qemu = self.write("qemu-sentinel", b"#!/bin/sh\nexit 93\n", executable=True)
        self.qemu_img = self.write("qemu-img-sentinel", b"#!/bin/sh\nexit 94\n", executable=True)
        self.aop = self.write("aop-cmd-db.bin", b"explicit synthetic AOP input")
        self.symbols = self.write("symbols.json", b"{}\n")
        self.binding = {
            "schema": "tb371fc-full-lun-boot-binding-v1",
            "ready": self.reference(self.ready_path),
            "prepared_manifest": self.reference(self.manifest_path),
            "lun0_assembly": self.reference(self.assembly_path),
            "baseline_verification": self.reference(self.baseline_path),
            "generation_completion": self.reference(self.completion_path),
            "generation_plan_sha256": self.plan_hash,
            "source_binding_sha256": self.source_binding_hash,
            "capture_counts": {"jobs": 1, "assembly_manifests": 2, "chunks": 2},
            "inputs": {name: self.file_record(Path(self.prepared[name]["path"]))
                       for name in ("kernel", "initrd", "dtb", "bootargs")},
            "luns": [{"lun": n, "path": str(path), "sha256": digest(path),
                      "bytes": 8192, "source": self.sources[n],
                      "logical_block_size": 4096, "logical_block_count": 2}
                     for n, path in enumerate(self.lun_paths)],
            "executables": {"qemu": self.reference(self.qemu),
                            "qemu_img": self.reference(self.qemu_img)},
            "runner_dependencies": {name: digest(SCRIPTS / name) for name in RUNNER_NAMES},
            "machine": f"tb371fc,rpmh-profile=file,rpmh-cmd-db={self.aop}",
            "global_properties": ["qcom-camera-gdsc.enabled-reference=on"],
            "resources": [self.file_record(self.aop)],
            "observation": {"seconds": 120, "max_log_mib": 128,
                            "snapshot_interval": 15, "stop_on_kernel_failure": False,
                            "trace_interrupts": False},
            "printk": None,
        }
        self.binding["inputs"]["boot_image"] = self.file_record(self.boot)
        self.binding_path = self.json_file("binding.json", self.binding)
        self.original_bytes = {p: p.read_bytes() for p in
                               [self.boot, self.dtb, self.manifest_path, self.aop,
                                self.ready_path, *self.lun_paths]}

    def write(self, name, data, *, executable=False):
        path = self.generation / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        if executable:
            path.chmod(0o700)
        return path

    def json_file(self, name, value):
        return self.write(name, (json.dumps(value, indent=2) + "\n").encode())

    @staticmethod
    def reference(path):
        return {"path": str(path), "sha256": digest(path)}

    @staticmethod
    def file_record(path):
        return {"path": str(path), "sha256": digest(path), "size": path.stat().st_size}

    def save_binding(self):
        self.binding_path.write_text(json.dumps(self.binding) + "\n")

    def save_ready(self):
        self.ready_path.write_text(json.dumps(self.ready) + "\n")
        self.binding["ready"] = self.reference(self.ready_path)
        self.save_binding()

    def save_manifest(self):
        self.manifest_path.write_text(json.dumps(self.prepared) + "\n")
        self.binding["prepared_manifest"] = self.reference(self.manifest_path)
        self.save_binding()

    def invoke(self, *, dry_run=False, out=None):
        argv = ["--ready", str(self.ready_path), "--binding", str(self.binding_path),
                "--out", str(out or self.out), "--lock-file", str(self.lock)]
        if dry_run:
            argv.append("--dry-run")
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            result = ready_boot.main(argv)
        self.last_output = captured.getvalue()
        return result

    def assert_preserved(self):
        for path, before in self.original_bytes.items():
            self.assertEqual(path.read_bytes(), before, str(path))

    def assert_rejected_without_runner(self):
        with patch.object(ready_boot, "invoke_runner") as runner:
            self.assertNotEqual(self.invoke(), 0)
            runner.assert_not_called()

    def synthetic_runner(self, argv, dependency_hashes, *, passed=False,
                         returncode=2, alter_report=None, alter_plan=None):
        """Use real runner planning/hashing, then supply an explicit fake outcome.

        The inner call is dry-run only.  Postprocessing its result emulates the
        unavailable observation boundary, never execution of the placeholder
        QEMU executables or a physical Android source.
        """
        import boot_android as runner
        self.assertEqual(dependency_hashes, self.binding["runner_dependencies"])
        with self.lock.open("a+") as contender:
            with self.assertRaises(BlockingIOError):
                fcntl.flock(contender.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.assertEqual(runner.main(argv + ["--dry-run"]), 0)
        output = Path(argv[argv.index("--out") + 1])
        report_path, plan_path = output / "result.json", output / "plan.json"
        report = json.loads(report_path.read_text())
        plan = json.loads(plan_path.read_text())
        serial = ("[1.0] init: init first stage started!\n"
                  "[2.0] init: init second stage started!\n"
                  "[3.0] init: starting service 'zygote64'...\n"
                  "[9.0] init: processing action (sys.boot_completed=1) "
                  "from (/system/etc/init/hw/init.rc:1)\n") if passed else "Linux version synthetic-test\n"
        (output / "serial.log").write_text(serial)
        log_text, log_identities = runner.collect_logs(output, 1024 * 1024)
        ack = {"return": {}, "id": 7}
        shutdown = {"event": "SHUTDOWN", "data": {"guest": False, "reason": "host-qmp-quit"}}
        report.update(dry_run=False, executed=True,
                      stock_android_boot_passed=passed,
                      outcome="android-boot-complete-observed" if passed else "no-android-userspace-evidence",
                      base_files_opened_read_only=True, qemu_unchanged=True,
                      qemu_returncode=0, preservation=runner.verify_preserved(plan),
                      qemu_sha256_after=digest(self.qemu), stop_reason="time-bound",
                      final_status={"running": False, "status": "paused"},
                      qmp_events=[shutdown], logs=log_identities,
                      qmp_quit={"request_id": 7, "message_start": 0, "acknowledged": True,
                                "response": ack, "messages": [ack, shutdown]},
                      qmp_drain={"terminal": "eof", "errno": None, "error": None,
                                 "closed": True, "residual_bytes": 0, "message_count": 2,
                                 "prior_command_error": None},
                      observation=runner.analyze_logs(log_text),
                      fixture_scope="synthetic boundary result; no VM executed")
        self.assertEqual(runner.stock_boot_passed(report, plan), passed)
        if alter_report is not None:
            alter_report(report)
        if alter_plan is not None:
            alter_plan(plan)
        report_path.write_text(json.dumps(report))
        plan_path.write_text(json.dumps(plan))
        return returncode

    def test_dry_run_uses_all_six_luns_without_starting_qemu(self):
        with patch.object(ready_boot, "invoke_runner") as runner:
            self.assertEqual(self.invoke(dry_run=True), 0, self.last_output)
            runner.assert_not_called()
        self.assert_preserved()
        report = json.loads((self.out / "result.json").read_text())
        self.assertFalse(report["executed"])
        self.assertFalse(report["stock_android_boot_passed"])
        plan = json.loads((self.out / "plan.json").read_text())
        self.assertEqual([row["lun"] for row in plan["luns"]], list(range(6)))
        self.assertFalse(plan["raw_lun_digests_checked_in_preflight"])
        self.assertTrue(plan["full_lun_digest_verification_delegated_to_runner"])
        self.assertEqual(plan["qualifications"], false_flags())

    def test_six_luns_reach_real_planning_without_upgrading_boot_verdict(self):
        with patch.object(ready_boot, "invoke_runner", side_effect=self.synthetic_runner) as invoke:
            self.assertEqual(self.invoke(), 2, self.last_output)
            invoke.assert_called_once()
        self.assert_preserved()
        result = json.loads((self.out / "result.json").read_text())
        self.assertEqual(result["outcome"], "no-android-userspace-evidence")
        self.assertFalse(result["stock_android_boot_passed"])
        self.assertFalse(result["execution_state_unknown"])
        plan = json.loads((self.out / "runner/plan.json").read_text())
        self.assertEqual([item["lun"] for item in plan["overlays"]], list(range(6)))
        self.assertEqual(plan["command"][plan["command"].index("-append") + 1],
                         Path(self.prepared["bootargs"]["path"]).read_text())
        self.assertFalse(plan["test_init_injected"])
        self.assertFalse(plan["bootargs_modified"])
        nodes = [json.loads(plan["command"][i + 1])
                 for i, item in enumerate(plan["command"]) if item == "-blockdev"]
        bases = [item for item in nodes if item["node-name"].startswith("ufs-base")]
        self.assertEqual(len(bases), 12)
        self.assertTrue(all(item["read-only"] is True for item in bases))

    def test_same_size_raw_corruption_fails_real_runner_hashing_before_execution(self):
        changed = self.lun_paths[3]
        damaged = bytearray(changed.read_bytes())
        damaged[4096] ^= 1
        changed.write_bytes(damaged)
        self.assertEqual(changed.stat().st_size, self.binding["luns"][3]["bytes"])
        self.assertNotEqual(digest(changed), self.binding["luns"][3]["sha256"])
        with patch.object(ready_boot, "invoke_runner", side_effect=self.synthetic_runner) as runner:
            self.assertEqual(self.invoke(), 1)
            runner.assert_called_once()
        report = json.loads((self.out / "result.json").read_text())
        self.assertIn("LUN 3 SHA-256 mismatch", report["error"])
        self.assertFalse(report["stock_android_boot_passed"])
        self.assertIsNot(report["executed"], True)
        self.assertFalse((self.out / "runner").exists())
        self.assertEqual(changed.read_bytes(), damaged)
        for path, before in self.original_bytes.items():
            if path != changed:
                self.assertEqual(path.read_bytes(), before, str(path))

    def test_frozen_capture_path_differs_from_host_source_and_remains_valid(self):
        self.assertNotEqual(self.assembly["source_manifests"][1]["path"],
                            self.completion["jobs"][0]["manifest"])
        self.assertEqual(self.assembly["source_manifests"][1]["sha256"],
                         self.completion["jobs"][0]["manifest_sha256"])
        with patch.object(ready_boot, "invoke_runner") as runner:
            self.assertEqual(self.invoke(dry_run=True), 0, self.last_output)
            runner.assert_not_called()

    def test_consistent_synthetic_success_preserves_capture_qualifications(self):
        def execute(argv, hashes):
            return self.synthetic_runner(argv, hashes, passed=True, returncode=0)
        with patch.object(ready_boot, "invoke_runner", side_effect=execute) as runner:
            self.assertEqual(self.invoke(), 0, self.last_output)
            runner.assert_called_once()
        report = json.loads((self.out / "result.json").read_text())
        self.assertTrue(report["stock_android_boot_passed"])
        self.assertFalse(report["execution_state_unknown"])
        self.assert_preserved()
        ready = json.loads(self.ready_path.read_text())
        self.assertTrue(all(ready[key] is False for key in
                            FALSE_QUALIFICATIONS + ("all_backups_complete",)))

    def test_surface_success_cannot_hide_kernel_or_qmp_failure(self):
        changes = (
            lambda report: report["observation"]["kernel_failures"].append(
                {"kind": "kernel-panic", "text": "synthetic panic"}),
            lambda report: report["qmp_events"].append({"event": "GUEST_PANICKED"}),
            lambda report: report["qmp_drain"].update(residual_bytes=1),
            lambda report: report["final_status"].update(running=True, status="running"),
            lambda report: report.update(qemu_returncode=1),
        )
        for index, mutation in enumerate(changes):
            with self.subTest(contradiction=index):
                self.out = self.root / f"attempt-contradiction-{index}"
                def execute(argv, hashes):
                    return self.synthetic_runner(argv, hashes, passed=True, returncode=0,
                                                 alter_report=mutation)
                with patch.object(ready_boot, "invoke_runner", side_effect=execute) as runner:
                    self.assertEqual(self.invoke(), 1)
                    runner.assert_called_once()
                report = json.loads((self.out / "result.json").read_text())
                self.assertFalse(report["stock_android_boot_passed"])

    def test_changed_job_hash_cannot_be_hidden_by_rebinding_completion(self):
        self.completion["jobs"][0]["manifest_sha256"] = "f" * 64
        self.completion_path.write_text(json.dumps(self.completion))
        self.binding["generation_completion"] = self.reference(self.completion_path)
        self.ready["generation_completion_sha256"] = digest(self.completion_path)
        self.save_ready()
        self.assert_rejected_without_runner()

    def test_return_code_and_runner_verdict_must_agree(self):
        for index, (code, passed) in enumerate(((0, False), (2, True), (1, True),
                                                (True, True), (42, False))):
            with self.subTest(returncode=code, passed=passed):
                self.out = self.root / f"attempt-return-{index}"
                def execute(argv, hashes):
                    return self.synthetic_runner(argv, hashes, passed=passed, returncode=code)
                with patch.object(ready_boot, "invoke_runner", side_effect=execute) as runner:
                    self.assertEqual(self.invoke(), 1)
                    runner.assert_called_once()
                report = json.loads((self.out / "result.json").read_text())
                self.assertFalse(report["stock_android_boot_passed"])
                self.assertTrue(report["execution_state_unknown"])

    def test_runner_report_must_bind_actual_inputs_and_all_luns(self):
        changes = (
            lambda report: report.update(manifest_sha256="f" * 64),
            lambda report: report.update(qemu_sha256="f" * 64),
            lambda report: report.update(runner_sha256="f" * 64),
            lambda report: report["input_provenance"]["luns"].pop(),
            lambda report: report["input_provenance"]["luns"][2].update(sha256_verified=False),
        )
        for index, mutation in enumerate(changes):
            with self.subTest(mutation=index):
                self.out = self.root / f"attempt-report-{index}"
                def execute(argv, hashes):
                    return self.synthetic_runner(argv, hashes, alter_report=mutation)
                with patch.object(ready_boot, "invoke_runner", side_effect=execute) as runner:
                    self.assertEqual(self.invoke(), 1)
                    runner.assert_called_once()

    def test_generation_job_path_cannot_be_replaced_by_another_capture(self):
        replacement = self.write("other-generation/tail-manifest.json",
                                 self.tail_manifest.read_bytes())
        self.assembly["source_manifests"][1]["path"] = str(replacement)
        self.assembly_path.write_text(json.dumps(self.assembly))
        self.binding["lun0_assembly"] = self.reference(self.assembly_path)
        self.ready["new_lun0_assembly_receipt_sha256"] = digest(self.assembly_path)
        self.save_ready()
        self.assert_rejected_without_runner()

    def test_old_no_lun_manifest_cannot_be_consumed(self):
        self.prepared["luns"] = []
        self.save_manifest()
        self.assert_rejected_without_runner()

    def test_missing_and_duplicate_luns_are_rejected(self):
        original = copy.deepcopy(self.binding["luns"])
        for rows in (original[:-1], original + [copy.deepcopy(original[0])],
                     original[:-1] + [copy.deepcopy(original[0])]):
            with self.subTest(luns=[r["lun"] for r in rows]):
                self.binding["luns"] = rows
                self.save_binding()
                self.out = self.root / ("attempt-" + str(len(list(self.root.iterdir()))))
                self.assert_rejected_without_runner()

    def test_ready_qualifications_cannot_be_upgraded(self):
        original = copy.deepcopy(self.ready)
        for key in FALSE_QUALIFICATIONS + ("all_backups_complete",):
            with self.subTest(flag=key):
                self.ready = copy.deepcopy(original)
                self.ready[key] = True
                self.save_ready()
                self.out = self.root / ("attempt-" + key)
                self.assert_rejected_without_runner()

    def test_ready_must_affirm_six_luns_and_preserve_old_partials(self):
        original = copy.deepcopy(self.ready)
        for key in ("all_six_original_luns_assembled", "old_partials_preserved"):
            with self.subTest(flag=key):
                self.ready = copy.deepcopy(original)
                self.ready[key] = False
                self.save_ready()
                self.out = self.root / ("attempt-" + key)
                self.assert_rejected_without_runner()

    def test_ready_requires_explicit_boolean_false_qualification(self):
        original = copy.deepcopy(self.ready)
        for index, value in enumerate((None, 0, "false")):
            with self.subTest(value=value):
                self.ready = copy.deepcopy(original)
                if value is None:
                    del self.ready["snapshot_consistent"]
                else:
                    self.ready["snapshot_consistent"] = value
                self.save_ready()
                self.out = self.root / ("attempt-qualification-" + str(index))
                self.assert_rejected_without_runner()

    def test_changed_generation_and_source_bindings_are_rejected(self):
        for key in ("generation_plan_sha256", "source_binding_sha256"):
            with self.subTest(binding=key):
                old = self.binding[key]
                self.binding[key] = "f" * 64
                self.save_binding()
                self.out = self.root / ("attempt-" + key)
                self.assert_rejected_without_runner()
                self.binding[key] = old

    def test_prepared_lun_from_another_generation_is_rejected(self):
        different = self.write("other-generation/lun0.raw", b"Z" * 8192)
        self.prepared["luns"][0].update(path=str(different), sha256=digest(different))
        self.save_manifest()
        self.assert_rejected_without_runner()

    def test_changed_source_identity_in_verified_baseline_is_rejected(self):
        self.baseline["captures"]["lun3"]["source"] = {
            **self.sources[3], "inode": self.sources[3]["inode"] + 1}
        self.baseline_path.write_text(json.dumps(self.baseline))
        self.binding["baseline_verification"] = self.reference(self.baseline_path)
        self.save_binding()
        self.assert_rejected_without_runner()

    def test_incomplete_generation_or_mixed_completion_is_rejected(self):
        original = copy.deepcopy(self.completion)
        changes = (("generation2_capture_complete", False),
                   ("completed_jobs", 2), ("source_binding_sha256", "f" * 64),
                   ("jobs", []))
        for key, value in changes:
            with self.subTest(field=key):
                self.completion = copy.deepcopy(original)
                self.completion[key] = value
                self.completion_path.write_text(json.dumps(self.completion))
                self.binding["generation_completion"] = self.reference(self.completion_path)
                self.ready["generation_completion_sha256"] = digest(self.completion_path)
                self.save_ready()
                self.out = self.root / ("attempt-" + key)
                self.assert_rejected_without_runner()

    def test_lun0_coverage_gap_and_overlap_cannot_qualify(self):
        for offset in (0, 8192):
            with self.subTest(tail_offset=offset):
                self.assembly["source_manifests"][1]["ranges"][0]["offset"] = offset
                self.assembly_path.write_text(json.dumps(self.assembly))
                self.binding["lun0_assembly"] = self.reference(self.assembly_path)
                self.ready["new_lun0_assembly_receipt_sha256"] = digest(self.assembly_path)
                self.save_ready()
                self.out = self.root / ("attempt-offset-" + str(offset))
                self.assert_rejected_without_runner()

    def test_changed_core_files_are_rejected_before_runner(self):
        for name in ("kernel", "initrd", "dtb", "bootargs", "boot_image"):
            with self.subTest(input=name):
                path = Path(self.binding["inputs"][name]["path"])
                original = path.read_bytes()
                path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
                self.out = self.root / ("attempt-" + name)
                self.assert_rejected_without_runner()
                path.write_bytes(original)

    def test_changed_qemu_and_qemu_img_are_rejected_before_runner(self):
        for name, path in (("qemu", self.qemu), ("qemu_img", self.qemu_img)):
            with self.subTest(binary=name):
                original = path.read_bytes()
                path.write_bytes(original + b"# changed\n")
                self.out = self.root / ("attempt-" + name)
                self.assert_rejected_without_runner()
                path.write_bytes(original)

    def test_all_four_runner_dependency_hashes_are_enforced(self):
        for name in RUNNER_NAMES:
            with self.subTest(dependency=name):
                original = self.binding["runner_dependencies"][name]
                self.binding["runner_dependencies"][name] = "f" * 64
                self.save_binding()
                self.out = self.root / ("attempt-" + name)
                self.assert_rejected_without_runner()
                self.binding["runner_dependencies"][name] = original

    def test_changed_aop_resource_is_rejected_before_runner(self):
        self.aop.write_bytes(b"different AOP source")
        self.assert_rejected_without_runner()

    def test_relative_aop_filename_cannot_evade_resource_binding(self):
        self.binding["machine"] = "tb371fc,rpmh-profile=file,rpmh-cmd-db=relative-aop.bin"
        self.binding["resources"] = []
        self.save_binding()
        self.assert_rejected_without_runner()

    def test_busy_lock_never_starts_a_runner(self):
        with self.lock.open("a+") as locked:
            fcntl.flock(locked.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assert_rejected_without_runner()
        self.assert_preserved()

    def test_existing_attempt_directory_is_never_reused(self):
        self.out.mkdir()
        marker = self.out / "earlier-failure.txt"
        marker.write_bytes(b"retain this failed attempt")
        self.assert_rejected_without_runner()
        self.assertEqual(marker.read_bytes(), b"retain this failed attempt")

    def test_runner_exception_is_not_retried(self):
        with patch.object(ready_boot, "invoke_runner", side_effect=RuntimeError("synthetic runner failure")) as runner:
            self.assertNotEqual(self.invoke(), 0)
            runner.assert_called_once()
        failed = json.loads((self.out / "result.json").read_text())
        self.assertTrue(failed["execution_state_unknown"])
        self.assertFalse(failed["automatic_retry_attempted"])
        self.assert_rejected_without_runner()
        self.assertEqual(json.loads((self.out / "result.json").read_text()), failed)
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
