#!/usr/bin/env python3
"""Small persistent synthetic fixtures; no SSH, block devices, QEMU or assembly."""
import contextlib
import datetime
import errno
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import resume_core

from resume_core import (CHUNK_BYTES, PLAN_SHA, RecoveryError,
                         audit_frozen_contract, count_barriers_only, digest,
                         exclusive_json, load_pinned_collector, narrow_recovery_schedule,
                         receive_preserving, run_pending_once)

HERE = Path(__file__).resolve().parent
HISTORY = Path(os.environ["TB371FC_RESUME_HISTORY"])
COLLECTOR = HISTORY / "qemu-runner-shutdown-work/tb371fc/scripts/acquire_block_ranges.py"
PLAN = HISTORY / "generation2-capture-work/generation2-jobs-plan.json"
PRODUCER = HISTORY / "generation2-capture-work/capture_generation2.py"
ARTIFACTS = HERE / "test-artifacts" / (datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])


def new_fixture(name, partial=True):
    root = ARTIFACTS / name
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.chmod(root, 0o700)
    cap = load_pinned_collector(COLLECTOR)
    source = root / "source.bin"
    data = b"".join(bytes([index + 1]) * 4096 for index in range(16))
    source.write_bytes(data)
    os.chmod(source, 0o600)
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        identity = {"path": str(source), **cap.geometry(fd, allow_regular=True)}
        ranges = [{"label": f"userdata-fixture-{index:03d}", "offset": index * 4096,
                   "length": 4096, "classification": "live-mutable"} for index in range(16)]
        plan = {"schema": cap.SCHEMA, "source": identity, "ranges": ranges,
                "chunk_bytes": CHUNK_BYTES, "gpt": None,
                "source_open_mode": "O_RDONLY", "snapshot_consistent": False}
        out = root / "capture"
        out.mkdir(mode=0o700)
        lockfd = os.open(out / "capture.lock", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.close(lockfd)
        cap.write_json_exclusive(out / "plan.json", plan)
        rows = cap.validate_plan(plan)
        for row in rows[:6]:
            stream = io.BytesIO()
            cap.send_chunk(fd, row, stream)
            stream.seek(0)
            begin = cap.get_json(stream)
            directory = out / f"chunk-{row['index']:06d}"
            directory.mkdir(mode=0o700)
            cap.receive_chunk(row, begin, stream, directory)
        directory = out / "chunk-000006"
        directory.mkdir(mode=0o700)
        if partial:
            (directory / "data.gz.partial").write_bytes(b"synthetic interrupted gzip evidence\x00\x01\x02")
            os.chmod(directory / "data.gz.partial", 0o600)
        exclusive_json(root / "expected-plan.json", plan)
        exclusive_json(root / "fixture-only.json", {"synthetic_regular_files_only": True})
        return root, cap, plan
    finally:
        os.close(fd)


def protected_bytes(root):
    return {str(path.relative_to(root / "capture")): path.read_bytes()
            for path in (root / "capture").glob("chunk-*/*")
            if path.is_file() and path.name in ("data.gz", "receipt.json")}


def original_send(root, cap, plan, reads):
    original_hash = cap.hash_range
    def observed_hash(fd, offset, size):
        reads.append((offset, size))
        return original_hash(fd, offset, size)
    cap.hash_range = observed_hash
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            return cap.send(root / "source.bin", plan["source"]["bytes"], plan["ranges"],
                            [sys.executable, str(HERE / "fixture_receiver.py"),
                             "--fixture-root", str(root), "--collector", str(COLLECTOR)],
                            chunk_bytes=CHUNK_BYTES, allow_regular=True)
    finally:
        cap.hash_range = original_hash


def probe(lock):
    info = lock.stat()
    run = subprocess.run([sys.executable, str(HERE / "lock_probe.py"), str(lock),
                          str(info.st_dev), str(info.st_ino)], capture_output=True, text=True,
                         timeout=5, check=True)
    return json.loads(run.stdout)


class ResumeTests(unittest.TestCase):
    def test_frozen_plan_counts_and_no_edit(self):
        before = PLAN.read_bytes()
        plan = audit_frozen_contract(PLAN, PRODUCER, COLLECTOR)
        observations = []
        for index, job in enumerate(plan["jobs"]):
            seen = {"name": job["name"]}
            if index < 82:
                seen.update(status="historical-complete", chunks_verified=job["expected_chunks"])
            elif index == 82:
                seen.update(status="incomplete", completed_indices=list(range(6)))
            else:
                seen.update(status="absent")
            observations.append(seen)
        schedule = narrow_recovery_schedule(plan, observations)
        self.assertEqual([sum(x["action"] == action for x in schedule) for action in
                          ("retain-historical", "strict-resume", "fresh-absent-only")], [82, 1, 24])
        records = [{"name": job["name"], "chunks": job["expected_chunks"]} for job in plan["jobs"]]
        result = count_barriers_only(plan, records, 48)
        self.assertEqual((result["jobs"], result["manifests"], result["chunks"]), (107, 108, 1842))
        self.assertFalse(result["ready_written"])
        with self.assertRaises(RecoveryError):
            count_barriers_only(plan, records[:-1], 48)
        with self.assertRaises(RecoveryError):
            count_barriers_only(plan, records, 47)
        observations[83]["status"] = "incomplete"
        with self.assertRaises(RecoveryError):
            narrow_recovery_schedule(plan, observations)
        self.assertEqual(before, PLAN.read_bytes())
        self.assertEqual(digest(before), PLAN_SHA)

    def test_same_source_preserves_partial_and_rechecks_six(self):
        root, cap, plan = new_fixture(self._testMethodName)
        out = root / "capture"
        before = protected_bytes(root)
        partial = (out / "chunk-000006/data.gz.partial").read_bytes()
        lock_before = (out / "capture.lock").stat()
        reads = []
        result = original_send(root, cap, plan, reads)
        self.assertTrue(result["complete"])
        self.assertEqual(reads[:6], [(i * 4096, 4096) for i in range(6)])
        self.assertEqual(sum(offset < 6 * 4096 for offset, _ in reads), 6)
        self.assertEqual({key: (out / key).read_bytes() for key in before}, before)
        manifest = json.loads((out / "manifest.json").read_bytes())
        self.assertEqual(manifest["resume_source_revalidated_chunks"], list(range(6)))
        self.assertEqual(len(manifest["chunks"]), 16)
        self.assertEqual((root / "preserved/files/chunk-000006/data.gz.partial").read_bytes(), partial)
        lock_after = (out / "capture.lock").stat()
        self.assertEqual((lock_before.st_dev, lock_before.st_ino), (lock_after.st_dev, lock_after.st_ino))
        probes = [json.loads(line) for line in (root / "lock-probes.jsonl").read_text().splitlines()]
        self.assertTrue(all(not row["acquired"] for row in probes))
        phases = {row["phase"] for row in probes}
        self.assertTrue({"preserving-uncommitted-file", "collector-ready-after-lock",
                         "collector-returned-outer-still-held", "outer-before-close"} <= phases)
        driver = json.loads((root / "driver-result.json").read_bytes())
        self.assertTrue(driver["lock_after_wrapper"]["acquired"])
        self.assertEqual(driver["returncode"], 0)
        self.assertFalse((root / "generation2-complete.json").exists())
        self.assertFalse((root / "ready.json").exists())

    def test_changed_sixth_source_chunk_stops_without_fresh_fallback(self):
        root, cap, plan = new_fixture(self._testMethodName)
        before = protected_bytes(root)
        partial = (root / "capture/chunk-000006/data.gz.partial").read_bytes()
        # Only a newly created synthetic regular source is changed.
        with (root / "source.bin").open("r+b") as stream:
            stream.seek(5 * 4096)
            stream.write(b"\xff")
        calls, reads = [], []
        steps = [{"name": "synthetic-tail081", "action": "strict-resume", "expected_chunks": 16,
                  "manifest": str(root / "capture/manifest.json")},
                 {"name": "must-not-start", "action": "fresh-absent-only", "expected_chunks": 16,
                  "manifest": str(root / "never-started/manifest.json")}]
        def run_job(step):
            calls.append(step["action"])
            self.assertEqual(step["action"], "strict-resume")
            return original_send(root, cap, plan, reads)
        with self.assertRaisesRegex(ValueError, "source changed since completed chunk was captured") as stopped_source:
            run_pending_once(steps, plan["source"], lambda: plan["source"], run_job)
        exclusive_json(root / "sender-stopped.json", {
            "error": str(stopped_source.exception), "sender_calls": calls,
            "live_hash_ranges": reads, "fresh_fallback": False, "synthetic_fixture_only": True})
        self.assertEqual(calls, ["strict-resume"])
        self.assertEqual(reads, [(i * 4096, 4096) for i in range(6)])
        self.assertEqual({key: (root / "capture" / key).read_bytes() for key in before}, before)
        self.assertEqual((root / "preserved/files/chunk-000006/data.gz.partial").read_bytes(), partial)
        self.assertFalse((root / "capture/manifest.json").exists())
        self.assertFalse((root / "capture/chunk-000007").exists())
        stopped = json.loads((root / "preserved/stopped.json").read_bytes())
        self.assertFalse(stopped["fresh_fallback"])
        self.assertFalse(stopped["automatic_retry"])
        self.assertFalse(stopped["ready_written"])
        self.assertEqual(json.loads((root / "driver-result.json").read_bytes())["returncode"], 2)

    def test_source_identity_mismatch_never_invokes_sender(self):
        calls = []
        with self.assertRaisesRegex(RecoveryError, "live source identity changed"):
            run_pending_once([{"action": "strict-resume"}], {"inode": 1}, lambda: {"inode": 2},
                             lambda step: calls.append(step))
        self.assertEqual(calls, [])

    def test_busy_original_lock_prevents_preservation_and_cleanup(self):
        root, cap, plan = new_fixture(self._testMethodName)
        path = root / "capture/capture.lock"
        held = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = (root / "capture/chunk-000006/data.gz.partial").read_bytes()
        try:
            self.assertFalse(probe(path)["acquired"])
            with self.assertRaises(BlockingIOError):
                receive_preserving(cap, io.BytesIO(json.dumps(plan).encode() + b"\n"), io.BytesIO(),
                                   root / "capture", root / "preserved", plan)
            self.assertFalse((root / "preserved").exists())
            self.assertEqual((root / "capture/chunk-000006/data.gz.partial").read_bytes(), before)
        finally:
            os.close(held)
        self.assertTrue(probe(path)["acquired"])

    def test_symlink_debris_is_rejected_without_cleanup(self):
        root, cap, plan = new_fixture(self._testMethodName, partial=False)
        external = root / "must-remain.txt"
        external.write_bytes(b"synthetic untouched evidence")
        link = root / "capture/chunk-000006/data.gz.partial"
        link.symlink_to(external)
        with self.assertRaisesRegex(RecoveryError, "non-regular or symlink"):
            receive_preserving(cap, io.BytesIO(json.dumps(plan).encode() + b"\n"), io.BytesIO(),
                               root / "capture", root / "preserved", plan)
        self.assertTrue(link.is_symlink())
        self.assertEqual(external.read_bytes(), b"synthetic untouched evidence")
        self.assertFalse((root / "preserved").exists())

    def test_intermediate_directory_sync_failure_stops_before_collector(self):
        root, cap, plan = new_fixture(self._testMethodName)
        partial_path = root / "capture/chunk-000006/data.gz.partial"
        before = partial_path.read_bytes()
        real_sync = resume_core.sync_directory
        real_receive = cap.receive
        calls = []
        def sync_with_failure(path):
            calls.append("sync:" + str(Path(path).relative_to(root)))
            if Path(path) == root / "preserved/files":
                raise OSError(errno.EIO, "synthetic intermediate-directory sync failure")
            return real_sync(path)
        def observe_receive(*args, **kwargs):
            calls.append("collector-entry")
            return real_receive(*args, **kwargs)
        cap.receive = observe_receive
        with patch("resume_core.sync_directory", sync_with_failure):
            with self.assertRaisesRegex(OSError, "intermediate-directory sync failure"):
                receive_preserving(cap, io.BytesIO(json.dumps(plan).encode() + b"\n"), io.BytesIO(),
                                   root / "capture", root / "preserved", plan)
        self.assertIn("sync:preserved/files/chunk-000006", calls)
        self.assertIn("sync:preserved/files", calls)
        self.assertNotIn("collector-entry", calls)
        self.assertEqual(partial_path.read_bytes(), before)
        self.assertFalse((root / "preserved/preservation.json").exists())
        self.assertFalse((root / "capture/manifest.json").exists())
        exclusive_json(root / "sync-failure-observation.json", {"events": calls,
                       "canonical_partial_unchanged": True, "collector_entered": False})

    def test_all_preservation_directories_sync_before_collector_entry(self):
        root, cap, plan = new_fixture(self._testMethodName)
        real_sync, real_receive = resume_core.sync_directory, cap.receive
        events = []
        def observe_sync(path):
            events.append("sync:" + str(Path(path).relative_to(root)))
            return real_sync(path)
        def observe_receive(*args, **kwargs):
            events.append("collector-entry")
            return real_receive(*args, **kwargs)
        cap.receive = observe_receive
        with patch("resume_core.sync_directory", observe_sync):
            # A disconnected synthetic sender stops after the ordinary hello;
            # the assertion concerns preservation ordering, not a transfer.
            with self.assertRaisesRegex(ValueError, "truncated/oversized protocol JSON"):
                receive_preserving(cap, io.BytesIO(json.dumps(plan).encode() + b"\n"), io.BytesIO(),
                                   root / "capture", root / "preserved", plan)
        entry = events.index("collector-entry")
        self.assertLess(events.index("sync:preserved/files/chunk-000006"),
                        events.index("sync:preserved/files"))
        self.assertLess(events.index("sync:preserved/files"), entry)
        self.assertIn("sync:preserved", events[:entry])
        self.assertTrue((root / "preserved/preservation.json").exists())
        exclusive_json(root / "sync-order-observation.json", {"events": events,
                       "all_parent_directories_synced_before_collector": True})

    def test_replaced_lock_path_is_detected_before_collector_cleanup(self):
        root, cap, plan = new_fixture(self._testMethodName)
        partial_path = root / "capture/chunk-000006/data.gz.partial"
        before = partial_path.read_bytes()
        lock_path = root / "capture/capture.lock"
        original_inode = lock_path.stat().st_ino
        def simulate_external_replacement(phase, path, identity):
            if phase == "preservation-durable-before-collector":
                # Preserve both synthetic lock files; this is an adversarial
                # fixture action, never a coordinator recovery operation.
                path.rename(root / "original-capture.lock")
                newfd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                os.close(newfd)
        with self.assertRaisesRegex(RecoveryError, "original capture.lock inode changed"):
            receive_preserving(cap, io.BytesIO(json.dumps(plan).encode() + b"\n"), io.BytesIO(),
                               root / "capture", root / "preserved", plan,
                               phase_observer=simulate_external_replacement)
        self.assertEqual((root / "original-capture.lock").stat().st_ino, original_inode)
        self.assertNotEqual(lock_path.stat().st_ino, original_inode)
        self.assertEqual(partial_path.read_bytes(), before)
        self.assertEqual((root / "preserved/files/chunk-000006/data.gz.partial").read_bytes(), before)
        self.assertFalse((root / "capture/manifest.json").exists())


if __name__ == "__main__":
    original_hashes = {str(path): digest(path.read_bytes()) for path in (PLAN, PRODUCER, COLLECTOR)}
    ARTIFACTS.mkdir(mode=0o700, parents=True, exist_ok=False)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ResumeTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    unchanged = all(digest(Path(path).read_bytes()) == checksum for path, checksum in original_hashes.items())
    exclusive_json(ARTIFACTS / "test-receipt.json", {
        "synthetic_regular_files_only": True, "tests_run": result.testsRun,
        "failures": len(result.failures), "errors": len(result.errors),
        "historical_inputs_unchanged": unchanged, "historical_input_sha256": original_hashes,
        "physical_device_access": False, "ssh_started": False, "assembly_started": False,
        "artifacts_retained": True})
    print("fixture_artifacts=" + str(ARTIFACTS))
    raise SystemExit(0 if result.wasSuccessful() and unchanged else 1)
