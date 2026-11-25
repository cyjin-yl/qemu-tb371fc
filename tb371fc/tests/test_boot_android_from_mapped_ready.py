#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure mapped-receipt tests. All evidence is generated synthetic metadata.

No raw LUN, compressed chunk, host-origin path, capture runtime or QEMU process
is opened/executed. Logical byte geometry deliberately matches the nonuniform
107-job shape, without allocating corresponding raw or sparse LUN files.
"""
from __future__ import annotations

import base64
import ast
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import boot_android_from_ready as consumer


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def flags():
    return {key: False for key in consumer.FALSE_FLAGS}


class MappedFixture:
    def __init__(self, root, *, gap=False):
        self.root = root
        self.out = root / "mapped-generation2"
        self.origin = Path("/synthetic-host/original/generation2")
        self.namespace = Path("/synthetic-host/continuation")
        self.replacements = self.namespace / "captures"
        self.evidence = {}
        self.docs = {}
        self.workers = {}
        self.runtime_profiles = {}
        self.source_boot = "synthetic-source-boot"
        self.host_boot = "synthetic-host-boot"
        total = 125720068096
        self.sources = [{"path": "/dev/block/synthetic%d" % n, "bytes": total if n == 0 else 8192,
                         "logical_block_size": 4096, "kind": "block-device", "stat_device": 17,
                         "inode": 1000 + n, "rdev": 2048 + 16 * n} for n in range(6)]
        self.luns = [{"lun": n, "source": source, "path": str(root / "raw" / ("lun%d.raw" % n)),
                      "sha256": digest(("synthetic-raw-%d" % n).encode()), "format": "raw",
                      "bytes": source["bytes"], "logical_block_size": 4096,
                      "logical_block_count": source["bytes"] // 4096} for n, source in enumerate(self.sources)]
        self.super_ranges = [self.range("super", 35684352, 12884901888)]
        self.super_manifest = {"schema": "tb371fc-readonly-range-stream-v1", "status": "verified-range-copies",
            "plan": {"source": self.sources[0], "ranges": self.super_ranges, "chunk_bytes": 256 * 1024**2},
            "chunks": [{"index": n} for n in range(48)], **flags()}
        self.super_pin = self.put("/synthetic-host/original/captures/super/manifest.json", self.super_manifest)
        jobs = []
        cursor = 35684352 + 12884901888
        for n in range(107):
            name = "lun0-prefix" if n == 0 else "lun0-tail-%03d" % (n - 1)
            start, length = (0, 35684352) if n == 0 else (cursor, 56590336 if n == 106 else 1024**3)
            ranges = [self.range(name + "-%03d" % i, start + i * 8 * 1024**2, 8 * 1024**2)
                      for i in range(128)] if n == 1 else [self.range(name, start, length)]
            if gap and n == 2:
                ranges[0]["offset"] += 4096
            count = sum((row["length"] + 67108863) // 67108864 for row in ranges)
            target = self.origin / name
            jobs.append({"name": name, "target": str(target), "manifest": str(target / "manifest.json"),
                         "device": self.sources[0]["path"], "source_bytes": total, "offset": start,
                         "length": length, "ranges": ranges, "expected_chunks": count})
            if n:
                cursor += length
        assert cursor == total and sum(j["expected_chunks"] for j in jobs) == 1794
        self.original = {"schema": "tb371fc-generation2-segmented-capture-v1", "generation": 2,
            "source_open_mode": "O_RDONLY", "chunk_bytes": 67108864, "chunk_mib": 64,
            "collector_sha256": digest(b"synthetic original collector"), "expected_jobs": 107,
            "expected_assembly_manifests": 108, "expected_new_chunks": 1794, "source": self.sources[0],
            "retained_super": {"manifest": self.super_pin["path"], "offset": 35684352,
                               "length": 12884901888, "reacquire": False}, "jobs": jobs,
            "assembly_manifest_paths": [self.super_pin["path"]] + [j["manifest"] for j in jobs], **flags()}
        original_pin = self.put(self.origin / "jobs-plan.json", self.original)
        self.source_binding = {"source": self.sources[0], "jobs_plan_sha256": original_pin["sha256"],
            "collector_sha256": self.original["collector_sha256"], "reference_manifest": self.super_pin["path"],
            "reference_manifest_sha256": self.super_pin["sha256"], "all_backups_complete": False, **flags()}
        source_pin = self.put(self.origin / "source-binding.json", self.source_binding)
        self.selection = {"schema": "tb371fc-generation2-selected-captures-v1", "original_plan": original_pin,
            "source_binding": source_pin, "retained_super": self.super_pin,
            "replacement_root": str(self.replacements), "source_boot_id": self.source_boot,
            "source": self.sources[0], "jobs": [], **flags()}
        self.completion = {"schema": "tb371fc-generation2-mapped-verification-v1", "original_plan": original_pin,
            "source_binding": source_pin, "source": self.sources[0], "jobs": [], "selected_jobs_verified": 107,
            "assembly_manifests": 108, "new_job_chunks_verified": 1794, "retained_super_chunks_expected": 48,
            "assembly_chunks_expected": 1842, "original_generation_completion_used": False,
            "selected_compressed_and_decoded_bytes_verified": True, "all_backups_complete": False, **flags()}
        assembly_sources = [{"path": str(root / "baseline/captures/super/manifest.json"),
                             "sha256": self.super_pin["sha256"], "ranges": self.super_ranges}]
        for number, job in enumerate(jobs):
            name = job["name"]
            replacement = number >= 82
            directory = self.replacements / (name + "-fresh-001") if replacement else Path(job["target"])
            frozen = self.out / "captures" / name
            plan = {"schema": "tb371fc-readonly-range-stream-v1", "source": self.sources[0],
                    "ranges": job["ranges"], "chunk_bytes": 67108864, "gpt": None,
                    "source_open_mode": "O_RDONLY", "snapshot_consistent": False}
            manifest = {"schema": "tb371fc-readonly-range-stream-v1", "status": "verified-range-copies",
                "plan": plan, "chunks": [{"index": n} for n in range(job["expected_chunks"])],
                "whole_source_bytes_captured": False, "live_mutable_ranges": [r["label"] for r in job["ranges"]], **flags()}
            actual_pin = self.write(frozen / "manifest.json", manifest)
            manifest_pin = {**actual_pin, "path": str(directory / "manifest.json")}
            self.write(frozen / "plan.json", plan)
            self.write(frozen / "frozen-copy.json", {"manifest_sha256": manifest_pin["sha256"],
                "original_capture": str(directory), "compressed_copies_verified": True,
                "assembly_tested": False, "android_boot_tested": False})
            entry = {"name": name, "selection": "replacement" if replacement else "retained",
                     "capture_directory": str(directory), "manifest": manifest_pin, "provenance": None}
            self.selection["jobs"].append(entry)
            worker = {"schema": "tb371fc-reboot-resume-verification-v1", "verified": True, "raws": [],
                "captures": {name: {"manifest_sha256": manifest_pin["sha256"], "source": self.sources[0],
                                    "chunks": job["expected_chunks"], "ranges": job["ranges"]}}, **flags()}
            worker_plan = {"tools": "/synthetic-frozen-tools", "raws": [],
                "result": str(self.out / ("job-" + name + "-result.json")), "captures": [{"name": name,
                    "source": self.sources[0]["path"], "bytes": total, "ranges": [list(r.values()) for r in job["ranges"]],
                    "path": str(frozen), "manifest_sha256": manifest_pin["sha256"], "original_capture": str(directory)}]}
            self.workers[name] = (worker, worker_plan)
            self.completion["jobs"].append({"name": name, "selection": entry["selection"],
                "original_logical_manifest": job["manifest"], "selected_manifest": manifest_pin,
                "chunks": job["expected_chunks"], "worker_receipt": None})
            self.persist_worker(number)
            assembly_sources.append({"path": str(frozen / "manifest.json"), "sha256": actual_pin["sha256"],
                                     "ranges": job["ranges"]})
            if replacement:
                self.make_proof(number, entry, plan)
        self.baseline = {"schema": "tb371fc-reboot-resume-verification-v1", "verified": True,
            "plan_sha256": digest(b"synthetic baseline plan"), "captures": {},
            "raws": [{**lun, "verification_receipt_sha256": digest(("baseline%d" % lun["lun"]).encode())}
                     for lun in self.luns[1:]], **flags()}
        for n, source in enumerate(self.sources):
            self.baseline["captures"]["super" if n == 0 else "lun%d" % n] = {
                "source": source, "chunks": 48 if n == 0 else 1,
                "manifest_sha256": self.super_pin["sha256"] if n == 0 else digest(("baseline%d" % n).encode()),
                "ranges": self.super_ranges if n == 0 else [self.range("lun%d" % n, 0, 8192)]}
        self.assembly = {"schema": "tb371fc-whole-lun-assembly-v1", "status": "verified-whole-lun-assembly",
            "source": self.sources[0], "chunks_verified": 1842, "whole_source_bytes_captured": True,
            "coverage_nonoverlapping": True, "raw_readback_verified": True, "pristine_factory": False,
            "raw": {key: self.luns[0][key] for key in ("path", "format", "bytes", "sha256")},
            "source_manifests": assembly_sources, **flags()}
        dependency = self.put("/synthetic-host/prepare_batch_attempt_v3.py",
                              b"raise AssertionError('frozen acquisition source must never execute')\n")
        self.binding = {"schema": consumer.MAPPED_SCHEMA, "generation_plan_sha256": original_pin["sha256"],
            "source_binding_sha256": source_pin["sha256"], "capture_counts": {
                "jobs": 107, "assembly_manifests": 108, "chunks": 1842}, "evidence_dependencies": [dependency],
            "prepared_manifest": {"path": str(root / "prepared/manifest.json"), "sha256": digest(b"prepared")}}
        self.ready = {"schema": "tb371fc-mapped-generation2-ready-v1", "original_generation_completion_used": False,
            "all_six_original_luns_assembled": True, "old_partials_preserved": True,
            "generation2_jobs_verified": 107, "assembly_manifests": 108,
            "generation_plan_sha256": original_pin["sha256"], "source_binding_sha256": source_pin["sha256"],
            "prepared_manifest": self.binding["prepared_manifest"]["path"], "all_backups_complete": False, **flags()}
        self.finalize()

    @staticmethod
    def range(label, offset, length):
        return {"label": label, "offset": offset, "length": length, "classification": "live-mutable"}

    @staticmethod
    def pin(path, raw):
        return {"path": str(path), "bytes": len(raw), "sha256": digest(raw)}

    def put(self, path, value):
        raw = value if type(value) is bytes else encoded(value)
        self.evidence[str(path)] = raw
        return self.pin(path, raw)

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value if type(value) is bytes else encoded(value)
        path.write_bytes(raw)
        return self.pin(path, raw)

    def make_proof(self, number, entry, plan):
        name = entry["name"]
        legacy = number == 82
        budget = 1 if legacy else 4
        count = self.original["jobs"][number]["expected_chunks"]
        attempt = "attempt-%06d" % number
        suffix = "" if legacy else "_batch_v3"
        runtime = {}
        for key in ("collector", "interface", "storage_supervision" + suffix + ".py",
                    "host_supervisor" + suffix + ".py", "ssh_stdio_relay" + suffix + ".py"):
            if key == "interface":
                path = self.namespace / "bindings" / ("interface-contract-002.json" if legacy else "interface-contract-batch-v3.json")
            else:
                filename = ("acquire_block_ranges_resume_v2.py" if legacy else "acquire_block_ranges_batch_v3.py") if key == "collector" else key
                path = self.namespace / "code" / filename
            runtime[key] = self.put(path, ("raise AssertionError('data only: " + key + "')\n").encode())
        self.runtime_profiles["v1" if legacy else "v2"] = {
            name: (pin["bytes"], pin["sha256"]) for name, pin in runtime.items()}
        request = {"schema": "tb371fc-storage-supervised-attempt-v1" if legacy else "tb371fc-storage-supervised-attempt-v2",
            "namespace": str(self.namespace), "attempt_id": attempt, "capture_directory": entry["capture_directory"],
            "authorization": {"job_name": name, "jobs_plan_sha256": self.selection["original_plan"]["sha256"],
                              "source_binding_sha256": self.selection["source_binding"]["sha256"]},
            "runtime": runtime, "expected_plan": self.put(self.namespace / "bindings" / (attempt + "-plan.json"), plan),
            "host": {"boot_id": self.host_boot, "uid": 1000}, "expected_completed_chunks": max(0, count - budget)}
        if not legacy:
            request["max_new_chunks"] = budget
        ack = {"complete": True, "chunks": count, "manifest": entry["manifest"]["path"], "snapshot_consistent": False}
        receiver_process = {"boot_id": self.host_boot, "pid": 10000 + 2 * number, "start_ticks": 1000 + number}
        relay_process = {**receiver_process, "pid": receiver_process["pid"] + 1}
        common = {"exit_code": 0, "error": None, "host_boot_id": self.host_boot,
                  "snapshot_consistent": False, "whole_storage_backup_complete_claimed": False}
        receiver = {**common, "schema": "tb371fc-storage-receiver-terminal-v1", "outcome": "host-complete",
            "process": receiver_process, "clean_host_protocol": True, "capture_directory": entry["capture_directory"],
            "acknowledgement": ack, "actual_new_chunks": min(budget, count), "max_new_chunks": budget,
            "max_new_bytes": budget * 67108864}
        relay = {**common, "schema": "tb371fc-storage-relay-terminal-v1", "outcome": "drained", "process": relay_process,
            "stdin_eof": True, "response_eof": True, "supervisor_termination_confirmed_before_exit0": True,
            "undelivered_to_receiver_bytes": 0, "undelivered_to_source_bytes": 0,
            "counts": {key: 10 for key in ("source_read", "receiver_written", "receiver_read", "source_written")},
            "sha256": {key: digest(b"synthetic relay bytes") for key in ("source_read", "receiver_written", "receiver_read", "source_written")}}
        source = {"schema": "tb371fc-storage-memory-sender-result-v2", "attempt_id": attempt, "job_name": name,
            "collector_sha256": runtime["collector"]["sha256"], "source_boot_id": self.source_boot,
            "source_call_passed": True, "ssh_child_reaped": True, "ssh_exit_code": 0, "acknowledgement": ack,
            "source_open_mode": "O_RDONLY", "control_output_filesystem": "tmpfs", "automatic_retry_permitted": False,
            "tablet_persistent_files_created_or_written": False, "tablet_block_source_written": False,
            "ssh_terminate_sent": False, "ssh_kill_sent": False, "alarm_fired": False,
            "source_pid": 20000 + 3 * number, "source_start_ticks": 2000 + 3 * number,
            "ssh_pid": 20001 + 3 * number, "ssh_start_ticks": 2001 + 3 * number}
        if not legacy:
            source.update(max_new_chunks=budget, max_new_bytes=budget * 67108864)
        worker = {"boot_id": self.source_boot, "pid": source["source_pid"], "start_ticks": source["source_start_ticks"]}
        controller = {"boot_id": self.source_boot, "pid": source["ssh_pid"] + 1, "start_ticks": source["ssh_start_ticks"] + 1}
        outer = {"schema": "tb371fc-storage-memory-call-outer-v1", "attempt_id": attempt,
            "worker_reaped": True, "output_limit_reached": False, "persistent_output_files_written": False,
            "root_process_wait_returncode": 0, "source_result": source, "worker_process": worker,
            "controller_process": controller, "stderr_base64": "", "stderr_bytes": 0, "stderr_sha256": digest(b"")}
        stop = {"schema": "tb371fc-storage-source-termination-and-environment-v1", "attempt_id": attempt,
            "boot_id": self.source_boot, "uid": 0, "source_lock_available": True, "processes": [
                {"pid": p["pid"], "expected_start_ticks": p["start_ticks"], "present": False, "same_identity_running": False}
                for p in (worker, controller, {"pid": source["ssh_pid"], "start_ticks": source["ssh_start_ticks"]})]}
        retained = {"receiver-terminal.json": receiver, "relay-terminal.json": relay,
            "receiver-start.json": {"process": receiver_process, "admission": {
                "verified": True, "background_daemon": False, "tmux_pane": "%9"}},
            "relay-start.json": {"process": relay_process}}
        host = {"schema": "tb371fc-storage-host-attempt-stop-review-v1", "receiver_identity_running": False,
            "relay_identity_running": False, "source_result_passed": True, "signal_sent": False,
            "expected_identity": receiver_process, "relay_identity": relay_process,
            "panes": "@1|%9|1|0|" + str(receiver_process["pid"]) + "\n"}
        self.docs[name] = {"request": request, "receiver_terminal": receiver, "relay_terminal": relay,
            "source_outer": outer, "source_termination": stop, "retained": retained, "host_result": host}
        self.persist_proof(number)

    def persist_proof(self, number):
        entry = self.selection["jobs"][number]
        docs = self.docs[entry["name"]]
        request = docs["request"]
        attempt = request["attempt_id"]
        proof = {"request": self.put(self.namespace / "bindings" / (attempt + "-request.json"), request)}
        docs["source_outer"]["source_result"]["host_request_sha256"] = proof["request"]["sha256"]
        raw = encoded(docs["source_outer"]["source_result"])
        docs["source_outer"].update(stdout_base64=base64.b64encode(raw).decode(), stdout_bytes=len(raw), stdout_sha256=digest(raw))
        rows = []
        for n, (name, value) in enumerate(sorted(docs["retained"].items())):
            value["request_sha256"] = proof["request"]["sha256"]
            pin = self.put(self.namespace / "attempts" / attempt / name, value)
            rows.append({"name": name, "kind": "regular", "inode": n + 1, "mode": "0o600",
                         "bytes": pin["bytes"], "sha256": pin["sha256"], "json": value})
            if name in ("receiver-terminal.json", "relay-terminal.json"):
                proof[name[:-5].replace("-", "_")] = pin
        for name in ("source-to-receiver.fifo", "receiver-to-source.fifo"):
            rows.append({"name": name, "kind": "fifo", "inode": len(rows) + 1, "mode": "0o600", "bytes": 0})
        docs["host_result"]["files"] = rows
        packet = {"result": docs["host_result"], "evidence": self.put(
            self.namespace / "preparations" / (attempt + "-host-evidence.json"), docs["host_result"])}
        proof["host_termination"] = self.put(self.namespace / "preparations" / (attempt + "-host-packet.json"), packet)
        for key in ("source_outer", "source_termination"):
            proof[key] = self.put(self.namespace / "bindings" / (attempt + "-" + key + ".json"), docs[key])
        entry["provenance"] = proof

    def persist_worker(self, number):
        job = self.completion["jobs"][number]
        worker, plan = self.workers[job["name"]]
        plan_pin = self.write(self.out / ("job-" + job["name"] + "-plan.json"), plan)
        worker["plan_sha256"] = plan_pin["sha256"]
        job["worker_receipt"] = self.write(self.out / ("job-" + job["name"] + "-result.json"), worker)

    def finalize(self):
        selection_pin = self.put("/synthetic-host/maps/selected.json", self.selection)
        self.completion["selection_map"] = selection_pin
        self.index = {"files": []}
        for n, (path, raw) in enumerate(sorted(self.evidence.items())):
            frozen = self.write(self.out / "selected-evidence" / ("%04d.bin" % n), raw)
            self.index["files"].append({"original": self.pin(path, raw), "frozen_path": frozen["path"]})
            if path == selection_pin["path"]:
                self.binding["selection_map"] = frozen
        self.persist_index()

    def persist_index(self):
        self.binding["frozen_evidence_index"] = self.write(self.out / "selected-evidence-index.json", self.index)
        self.completion["frozen_evidence_index_sha256"] = self.binding["frozen_evidence_index"]["sha256"]
        self.binding["mapped_generation_completion"] = self.write(self.out / "mapped-generation2-complete.json", self.completion)
        self.binding["baseline_verification"] = self.write(self.root / "baseline-result.json", self.baseline)
        self.binding["lun0_assembly"] = self.write(self.out / "raw/lun0.raw.manifest.json", self.assembly)
        self.ready.update(selection_map_sha256=self.binding["selection_map"]["sha256"],
            mapped_generation_completion_sha256=self.binding["mapped_generation_completion"]["sha256"],
            baseline_verification=self.binding["baseline_verification"]["path"],
            new_lun0_assembly_receipt_sha256=self.binding["lun0_assembly"]["sha256"])
        self.binding["ready"] = self.write(self.out / "ready.json", self.ready)

    def check(self):
        tracked = consumer.Inputs()
        consumer.validate_receipts(self.binding, self.ready, tracked, self.luns)
        return tracked


class MappedReadyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mapped-ready-metadata-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = self.enterContext(patch.object(consumer, "invoke_runner",
            side_effect=AssertionError("runner invocation is prohibited in mapped metadata tests")))
        self.modules = self.enterContext(patch.object(consumer, "bound_modules",
            side_effect=AssertionError("source execution is prohibited in mapped metadata tests")))
        self.f = MappedFixture(self.root)
        # Only fixture byte identities are substituted. Production constants
        # are separately compared with the merged preparer as literal data.
        self.enterContext(patch.object(consumer, "MAPPED_RUNTIME_PROFILES", self.f.runtime_profiles))
        validator = self.f.binding["evidence_dependencies"][0]
        self.enterContext(patch.object(consumer, "MAPPED_PROVENANCE_VALIDATOR", (validator["bytes"], validator["sha256"])))

    def reject(self, pattern):
        with self.assertRaisesRegex(ValueError, pattern):
            self.f.check()
        self.runner.assert_not_called()
        self.modules.assert_not_called()

    def test_full_mapping_binds_nonuniform_ranges_and_complete_last_batch(self):
        tracked = self.f.check()
        self.assertEqual([self.f.original["jobs"][n]["expected_chunks"] for n in (0, 1, 82, 106)], [1, 128, 16, 1])
        self.assertEqual(self.f.super_manifest["plan"]["chunk_bytes"], 256 * 1024**2)
        self.assertEqual(sum(j["chunks"] for j in self.f.completion["jobs"]), 1794)
        self.assertEqual(self.f.docs["lun0-tail-105"]["receiver_terminal"]["actual_new_chunks"], 1)
        self.assertEqual(self.f.docs["lun0-tail-105"]["receiver_terminal"]["max_new_chunks"], 4)
        self.assertFalse((self.root / "raw").exists())
        self.assertTrue(all(not path.startswith("/synthetic-host/") for path in tracked.files))
        tracked.recheck()
        self.runner.assert_not_called()
        self.modules.assert_not_called()

    def test_missing_selected_tail_is_rejected(self):
        self.f.selection["jobs"].pop()
        self.f.finalize()
        self.reject("jobs are incomplete")

    def test_worker_plan_cannot_refer_to_old_physical_capture(self):
        self.f.workers["lun0-tail-081"][1]["captures"][0]["original_capture"] = str(self.f.origin / "lun0-tail-081")
        self.f.persist_worker(82)
        self.f.finalize()
        self.reject("worker plan provenance")

    def test_worker_result_cannot_change_source_identity(self):
        worker = self.f.workers["lun0-tail-081"][0]
        worker["captures"]["lun0-tail-081"]["source"] = {**self.f.sources[0], "inode": 9999}
        self.f.persist_worker(82)
        self.f.finalize()
        self.reject("worker result differs")

    def test_selected_pin_cannot_cross_wire_completion(self):
        self.f.completion["jobs"][82]["selected_manifest"] = self.f.selection["jobs"][83]["manifest"]
        self.f.finalize()
        self.reject("selected job provenance")

    def test_mapped_binding_rejects_legacy_ready_schema(self):
        self.f.ready["schema"] = "tb371fc-generation2-ready-v1"
        self.f.persist_index()
        self.reject("mapped ready schema")

    def test_mapped_binding_rejects_legacy_completion_marker(self):
        self.f.completion["generation2_capture_complete"] = True
        self.f.finalize()
        self.reject("completion schema/verification")

    def test_mapped_binding_cannot_mix_legacy_binding_fields(self):
        self.f.binding["generation_completion"] = self.f.binding["mapped_generation_completion"]
        self.reject("mix legacy completion")

    def test_exact_chunk_counts_cannot_be_boolean_or_incomplete(self):
        self.f.completion["new_job_chunks_verified"] = 1793
        self.f.finalize()
        self.reject("completion counts")
        self.f.completion["new_job_chunks_verified"] = True
        self.f.finalize()
        self.reject("must be an integer")

    def test_coherent_but_gapped_ranges_fail_shared_full_lun_coverage(self):
        self.f = MappedFixture(self.root / "gapped", gap=True)
        self.reject("coverage has a gap or overlap")

    def test_assembly_range_metadata_cannot_drift_from_selected_manifests(self):
        self.f.assembly["source_manifests"][2]["ranges"] = [self.f.range("wrong", 0, 4096)]
        self.f.persist_index()
        self.reject("selected job range metadata")

    def test_assembly_requires_all_108_manifests(self):
        self.f.assembly["source_manifests"].pop()
        self.f.persist_index()
        self.reject("exact super and generation jobs")

    def test_missing_index_entry_cannot_fall_back_to_host_path(self):
        pin = self.f.selection["jobs"][82]["provenance"]["source_outer"]
        self.f.evidence.pop(pin["path"])
        self.f.finalize()
        self.reject("missing or conflicting original evidence pin")

    def test_extra_index_entry_requires_an_explicit_dependency(self):
        self.f.put("/synthetic-host/unreferenced.json", {"unrelated": True})
        self.f.finalize()
        self.reject("unreferenced extra frozen evidence")

    def test_duplicate_original_index_path_is_rejected(self):
        self.f.index["files"][1]["original"] = self.f.index["files"][0]["original"]
        self.f.persist_index()
        self.reject("duplicate or misplaced frozen evidence")

    def test_duplicate_frozen_index_target_is_rejected(self):
        self.f.index["files"][1]["frozen_path"] = self.f.index["files"][0]["frozen_path"]
        self.f.persist_index()
        self.reject("duplicate or misplaced frozen evidence")

    def test_evidence_dependencies_are_full_typed_pins(self):
        self.f.binding["evidence_dependencies"][0]["bytes"] = True
        self.reject("must be an integer")

    def test_changed_frozen_evidence_is_rechecked_after_preflight(self):
        tracked = self.f.check()
        path = Path(self.f.index["files"][0]["frozen_path"])
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "bound input changed"):
            tracked.recheck()

    def test_dead_unrelated_pids_do_not_release_selected_source(self):
        self.f.docs["lun0-tail-081"]["source_termination"]["processes"][0]["pid"] += 999
        self.f.persist_proof(82)
        self.f.finalize()
        self.reject("termination does not cover exact source identities")

    def test_source_stdout_must_match_embedded_result(self):
        entry = self.f.selection["jobs"][82]
        outer = copy.deepcopy(self.f.docs[entry["name"]]["source_outer"])
        outer["stdout_sha256"] = "0" * 64
        old_pin = entry["provenance"]["source_outer"]
        entry["provenance"]["source_outer"] = self.f.put(old_pin["path"], outer)
        self.f.finalize()
        self.reject("source stream differs")

    def test_batch_budget_and_request_downgrade_are_rejected(self):
        docs = self.f.docs["lun0-tail-082"]
        docs["request"]["schema"] = "tb371fc-storage-supervised-attempt-v1"
        self.f.persist_proof(83)
        self.f.finalize()
        self.reject("batch budget differs")

    def test_rebuilt_request_and_index_cannot_substitute_unreviewed_runtime(self):
        docs = self.f.docs["lun0-tail-082"]
        previous = docs["request"]["runtime"]["collector"]
        docs["request"]["runtime"]["collector"] = self.f.put(previous["path"], b"substituted collector bytes\n")
        self.f.persist_proof(83)
        self.f.finalize()
        self.reject("unreviewed mapped runtime profile")

    def test_rebuilt_dependency_pin_cannot_substitute_unreviewed_validator(self):
        previous = self.f.binding["evidence_dependencies"][0]
        self.f.binding["evidence_dependencies"][0] = self.f.put(previous["path"], b"substituted validator bytes\n")
        self.f.finalize()
        self.reject("unreviewed mapped provenance-validator")

    def test_cli_v2_dry_run_dispatch_retains_result_without_runner(self):
        binding = self.f.write(self.root / "binding.json", self.f.binding)
        out = self.root / "attempt"
        # This test isolates main's schema dispatch. The full receipt preflight
        # still runs; legacy tests cover shared boot-input and executable gates.
        def metadata_only(binding_doc, ready_path, tracked, output):
            self.assertEqual(binding_doc["schema"], consumer.MAPPED_SCHEMA)
            ready = tracked.file(binding_doc["ready"], json_file=True)
            consumer.validate_receipts(binding_doc, ready, tracked, self.f.luns)
            return {"schema": "tb371fc-full-lun-boot-plan-v2", "metadata_test_only": True, "runner_argv": []}
        with patch.object(consumer, "validate", side_effect=metadata_only), contextlib.redirect_stdout(io.StringIO()):
            result = consumer.main(["--binding", binding["path"],
                "--ready", self.f.binding["ready"]["path"], "--out", str(out), "--lock-file", str(self.root / "run.lock"), "--dry-run"])
        self.assertEqual(result, 0)
        final = json.loads((out / "result.json").read_text())
        self.assertEqual(final["schema"], "tb371fc-full-lun-boot-result-v2")
        self.assertEqual(final["binding_schema"], consumer.MAPPED_SCHEMA)
        self.assertFalse(final["runner_invocation_started"])
        self.runner.assert_not_called()
        self.modules.assert_not_called()


class MappedProductionPinsTests(unittest.TestCase):
    def test_approved_profile_constants_match_merged_preparer_literal_data(self):
        path = Path(__file__).resolve().parents[1] / "tools/storage-batch-v3/candidate/prepare_batch_attempt_v3.py"
        raw = path.read_bytes()
        self.assertEqual((len(raw), digest(raw)), consumer.MAPPED_PROVENANCE_VALIDATOR)
        values = {}
        for statement in ast.parse(raw).body:
            if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
                if statement.targets[0].id in ("RUNTIME", "LEGACY_RUNTIME"):
                    values[statement.targets[0].id] = ast.literal_eval(statement.value)
        self.assertEqual(consumer.MAPPED_RUNTIME_PROFILES, {"v1": values["LEGACY_RUNTIME"], "v2": values["RUNTIME"]})


if __name__ == "__main__":
    unittest.main()
