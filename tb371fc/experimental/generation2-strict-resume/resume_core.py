#!/usr/bin/env python3
"""Offline-review core for generation2 recovery; no production command entry.

The pinned collector supplies the wire protocol and the mandatory live rereads.
This module supplies preservation under the *existing* capture lock, a narrow
lock-fd adapter, a stop-on-first-error coordinator, and count-only barrier checks.
It neither writes generation2-complete.json/ready.json nor starts assembly.
"""
from __future__ import annotations

import ast
import datetime
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import types

PLAN_SHA = "a90c1161c6cbce7d0fada8e1901354f0a58b5707e02231d206a0c1f663a1a77a"
COLLECTOR_SHA = "eb1258cb2604cfd99540b2320b28bde0a14284d940258b4f95bff3980c0595be"
PRODUCER_SHA = "cff183ad6a78024e83aa3e9fcc188d519a168d3bf51a92467f9dca5eee233587"
BINDING_SHA = "78fa6806272f40e906dabbf9c75bb6553642c2fbe9e1deb7d0f20a784cc8b131"
CHUNK_BYTES = 64 * 1024**2
EXPECTED_JOBS, EXPECTED_MANIFESTS = 107, 108
EXPECTED_NEW_CHUNKS, RETAINED_SUPER_CHUNKS = 1794, 48
EXPECTED_ASSEMBLY_CHUNKS = 1842
FALSE_FLAGS = ("snapshot_consistent", "source_immutable_claim_verified",
               "pristine_factory_images", "restore_tested", "android_boot_tested")


class RecoveryError(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise RecoveryError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def same_json(a, b):
    return json.dumps(a, sort_keys=True, separators=(",", ":")) == json.dumps(
        b, sort_keys=True, separators=(",", ":"))


def exclusive_json(path, value):
    """New private audit file only; no replace/unlink recovery behavior."""
    data = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(Path(path).parent)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def private_directory(path):
    info = Path(path).lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700, "directory is not private/owned")
    return info


def fingerprint(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_mode, info.st_uid, info.st_nlink)


def regular_info(path):
    info = Path(path).lstat()
    require(stat.S_ISREG(info.st_mode), "non-regular or symlink capture file")
    return info


def load_pinned_collector(path):
    path = Path(path)
    raw = path.read_bytes()
    require(digest(raw) == COLLECTOR_SHA, "collector digest changed")
    tree = ast.parse(raw)
    receive = next(node for node in tree.body
                   if isinstance(node, ast.FunctionDef) and node.name == "receive")
    # Review fact, not an override: this exact implementation closes its fd;
    # it never calls LOCK_UN. fcntl is not replaced or intercepted below.
    require(not any(isinstance(node, ast.Attribute) and node.attr == "LOCK_UN"
                    for node in ast.walk(receive)), "collector unlock behavior needs review")
    module = types.ModuleType("pinned_generation2_capture")
    module.__file__ = str(path)
    exec(compile(raw, str(path), "exec"), module.__dict__)
    return module


def audit_frozen_contract(plan_path, producer_path, collector_path):
    raw = Path(plan_path).read_bytes()
    require(digest(raw) == PLAN_SHA, "frozen generation2 plan changed")
    require(digest(Path(producer_path).read_bytes()) == PRODUCER_SHA,
            "original producer changed")
    cap = load_pinned_collector(collector_path)
    plan = json.loads(raw)
    require(plan["collector_sha256"] == COLLECTOR_SHA, "collector binding changed")
    require(all(plan.get(key) is False for key in FALSE_FLAGS), "qualification changed")
    jobs = plan["jobs"]
    require(len(jobs) == plan["expected_jobs"] == EXPECTED_JOBS, "job barrier changed")
    require(plan["expected_assembly_manifests"] == len(plan["assembly_manifest_paths"]) ==
            EXPECTED_MANIFESTS, "manifest barrier changed")
    require(plan["chunk_bytes"] == CHUNK_BYTES, "chunk size changed")
    chunks = 0
    coverage = [(plan["retained_super"]["offset"], plan["retained_super"]["length"])]
    for job in jobs:
        cap.validate_ranges(job["ranges"], plan["source"]["bytes"], 4096)
        count = sum((r["length"] + CHUNK_BYTES - 1) // CHUNK_BYTES for r in job["ranges"])
        require(count == job["expected_chunks"], "job chunk geometry changed")
        chunks += count
        coverage.extend((r["offset"], r["length"]) for r in job["ranges"])
    cursor = 0
    for offset, length in sorted(coverage):
        require(offset == cursor, "coverage has a gap/overlap")
        cursor += length
    require(cursor == plan["source"]["bytes"], "coverage is incomplete")
    require(chunks == plan["expected_new_chunks"] == EXPECTED_NEW_CHUNKS,
            "new-chunk barrier changed")
    require(plan["retained_super"]["length"] // (256 * 1024**2) == RETAINED_SUPER_CHUNKS,
            "retained super geometry changed")
    require(chunks + RETAINED_SUPER_CHUNKS == EXPECTED_ASSEMBLY_CHUNKS,
            "assembly chunk barrier changed")
    return plan


def load_bound_source(binding_path, plan):
    raw = Path(binding_path).read_bytes()
    require(digest(raw) == BINDING_SHA, "historical source binding changed")
    binding = json.loads(raw)
    require(binding["jobs_plan_sha256"] == PLAN_SHA and
            binding["collector_sha256"] == COLLECTOR_SHA, "binding plan/tool mismatch")
    require(all(binding.get(key) is False for key in FALSE_FLAGS + ("all_backups_complete",)),
            "binding qualification changed")
    require(binding["reference_manifest"] == plan["retained_super"]["manifest"],
            "retained manifest binding changed")
    return binding["source"]


def expected_capture_plan(cap, job, bound_source):
    return {"schema": cap.SCHEMA, "source": bound_source, "ranges": job["ranges"],
            "chunk_bytes": CHUNK_BYTES, "gpt": None, "source_open_mode": "O_RDONLY",
            "snapshot_consistent": False}


def narrow_recovery_schedule(plan, observations):
    """Require the reviewed 82-history / 6-chunk-tail081 / 24-absent layout.

    Observations must come from a locked, byte-verifying receiver audit. This
    pure function is not that audit and is not authority to start a sender.
    A changed inventory requires a new review, not an automatic fallback.
    """
    require(len(observations) == EXPECTED_JOBS, "inventory job count differs")
    result = []
    for index, (job, seen) in enumerate(zip(plan["jobs"], observations)):
        require(seen["name"] == job["name"], "inventory order differs")
        if index < 82:
            require(seen["status"] == "historical-complete" and
                    seen["chunks_verified"] == job["expected_chunks"],
                    "historical segment lacks locked byte-verification")
            action = "retain-historical"
        elif index == 82:
            require(job["name"] == "lun0-tail-081" and job["expected_chunks"] == 16 and
                    seen["status"] == "incomplete" and
                    seen["completed_indices"] == list(range(6)),
                    "tail081 no longer matches the reviewed incomplete inventory")
            action = "strict-resume"
        else:
            require(seen["status"] == "absent", "future target is not exclusively absent")
            action = "fresh-absent-only"
        result.append({"name": job["name"], "action": action,
                       "expected_chunks": job["expected_chunks"], "manifest": job["manifest"]})
    return result


def run_pending_once(schedule, expected_source, read_live_identity, run_job):
    """Dependency-injected draft coordinator; exceptions stop before next job.

    No transport implementation or production entry point is supplied. A future
    reviewed adapter must use the pinned send(), not manufacture skip messages.
    There is intentionally no retry, source-binding replacement, or fallback.
    """
    results = []
    for step in schedule:
        if step["action"] == "retain-historical":
            continue
        require(step["action"] in ("strict-resume", "fresh-absent-only"), "unknown action")
        require(same_json(read_live_identity(), expected_source), "live source identity changed")
        result = run_job(step)
        require(result.get("complete") is True and result.get("chunks") == step["expected_chunks"] and
                result.get("manifest") == step["manifest"] and
                result.get("snapshot_consistent") is False, "capture acknowledgement incomplete")
        require(same_json(read_live_identity(), expected_source), "live source identity changed")
        results.append(result)
    return results


def count_barriers_only(plan, job_records, retained_chunks):
    require(len(job_records) == EXPECTED_JOBS, "107 completed jobs required")
    for job, record in zip(plan["jobs"], job_records):
        require(record["name"] == job["name"] and record["chunks"] == job["expected_chunks"],
                "completion job set/geometry differs")
    require(retained_chunks == RETAINED_SUPER_CHUNKS, "48 retained super chunks required")
    total = sum(record["chunks"] for record in job_records) + retained_chunks
    require(len(job_records) + 1 == EXPECTED_MANIFESTS and total == EXPECTED_ASSEMBLY_CHUNKS,
            "108-manifest/1842-chunk barrier incomplete")
    return {"count_barriers_passed": True, "jobs": EXPECTED_JOBS,
            "manifests": EXPECTED_MANIFESTS, "chunks": total,
            "byte_verification_performed_by_this_check": False,
            "capture_completion_written": False, "assembly_started": False, "ready_written": False}


def _assert_lock(path, held_fd, identity):
    current, held = regular_info(path), os.fstat(held_fd)
    require((current.st_dev, current.st_ino) == identity == (held.st_dev, held.st_ino) and
            current.st_nlink == held.st_nlink == 1, "original capture.lock inode changed")


def _inspect_incomplete(cap, out, expected_plan, expected_indices):
    require(not (out / "manifest.json").exists(), "completed segment cannot enter this resume adapter")
    require(same_json(cap.load_json_file(out / "plan.json"), expected_plan), "stored plan/source changed")
    rows = cap.validate_plan(expected_plan)
    committed, debris, protected = [], [], {"plan.json": digest((out / "plan.json").read_bytes())}
    allowed_top = {"capture.lock", "plan.json", "plan.json.partial", "manifest.json.partial"}
    expected_dirs = {f"chunk-{row['index']:06d}" for row in rows}
    for path in out.iterdir():
        require(path.name in allowed_top | expected_dirs, "unknown capture entry")
        if path.name in expected_dirs:
            private_directory(path)
        else:
            regular_info(path)
    for name in ("plan.json.partial", "manifest.json.partial"):
        if (out / name).exists():
            debris.append(out / name)
    for row in rows:
        directory = out / f"chunk-{row['index']:06d}"
        if not directory.exists():
            continue
        files = {path.name: path for path in directory.iterdir()}
        require(set(files) <= {"receipt.json", "receipt.json.partial", "data.gz", "data.gz.partial"},
                "unknown chunk entry")
        for path in files.values():
            regular_info(path)
        if "receipt.json" in files:
            record = cap.load_json_file(files["receipt.json"])
            require(all(type(record.get(k)) is type(v) and record[k] == v for k, v in row.items()) and
                    record.get("source_rechecked") is True, "completed chunk geometry/proof changed")
            cap.verify_chunk(directory / "data.gz", record)
            committed.append(row["index"])
            protected[str(files["receipt.json"].relative_to(out))] = digest(files["receipt.json"].read_bytes())
            protected[str((directory / "data.gz").relative_to(out))] = fingerprint(regular_info(directory / "data.gz"))
            debris.extend(path for name, path in files.items() if name.endswith(".partial"))
        else:
            debris.extend(files.values())
    require(committed == list(expected_indices), "completed chunk set changed")
    return sorted(set(debris)), protected


def _copy_preserved(source, destination):
    before = regular_info(source)
    fd = os.open(source, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        require(fingerprint(os.fstat(fd)) == fingerprint(before), "partial changed before preservation")
        target_fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        copied, checksum = 0, hashlib.sha256()
        with os.fdopen(target_fd, "wb") as target:
            while data := os.read(fd, 1024 * 1024):
                copied += len(data)
                require(copied <= before.st_size, "partial grew during preservation")
                checksum.update(data)
                target.write(data)
            target.flush()
            os.fsync(target.fileno())
        require(copied == before.st_size and fingerprint(os.fstat(fd)) == fingerprint(before) ==
                fingerprint(regular_info(source)), "partial changed during preservation")
        with destination.open("rb") as stream:
            verify = hashlib.file_digest(stream, "sha256").hexdigest()
        require(verify == checksum.hexdigest(), "preserved copy hash differs")
        sync_directory(destination.parent)
        return {"bytes": copied, "sha256": verify, "source_fingerprint": list(fingerprint(before))}
    finally:
        os.close(fd)


class _ReplayHeader:
    def __init__(self, header, stream):
        self.header, self.stream = io.BytesIO(header), stream

    def readline(self, size=-1):
        line = self.header.readline(size)
        return line if line else self.stream.readline(size)

    def read(self, size=-1):
        data = self.header.read(size)
        if size >= 0:
            return data + self.stream.read(size - len(data)) if len(data) < size else data
        return data + self.stream.read()


class _DupLockOS:
    """Private module-local os facade; fcntl/LOCK_UN are never intercepted."""
    def __init__(self, lock_path, fd, identity, phase):
        self.path, self.fd, self.identity, self.phase = lock_path, fd, identity, phase
        self.duplicates = 0

    def __getattr__(self, name):
        return getattr(os, name)

    def open(self, path, flags, mode=0o777, *, dir_fd=None):
        if dir_fd is None and isinstance(path, (str, bytes, os.PathLike)) and Path(path).absolute() == self.path:
            require(flags == os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW and mode == 0o600,
                    "collector lock open behavior changed")
            require(self.duplicates == 0, "collector opened the lock twice")
            _assert_lock(self.path, self.fd, self.identity)
            duplicate = os.dup(self.fd)
            self.duplicates += 1
            self.phase("collector-lock-duplicated")
            return duplicate
        return os.open(path, flags, mode, dir_fd=dir_fd)


class _ObservedOutput:
    def __init__(self, stream, phase):
        self.stream, self.phase = stream, phase

    def write(self, data):
        value = json.loads(data)
        if value.get("ready") is True:
            self.phase("collector-ready-after-lock")
        return self.stream.write(data)

    def flush(self):
        self.stream.flush()


def receive_preserving(cap, incoming, outgoing, output_dir, preservation_dir,
                       expected_plan, *, expected_indices=range(6), phase_observer=None):
    """Run only strict resume, retaining the original lock through preservation.

    Use a separately loaded collector module for each invocation. The caller
    supplies the bound plan; a production caller/transport is intentionally
    absent from this draft. Original partial paths may be cleaned by the pinned
    collector only after all their copies and the preservation receipt are durable.
    """
    require(cap.os is os, "collector module is already adapted/in use")
    expected_indices = tuple(expected_indices)
    received = cap.get_json(incoming)
    require(same_json(received, expected_plan), "incoming source/ranges/geometry changed")
    cap.validate_plan(expected_plan)
    header = json.dumps(received, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    out, archive = Path(output_dir).absolute(), Path(preservation_dir).absolute()
    require(out != archive and out not in archive.parents and archive not in out.parents,
            "preservation directory must be separate from the capture")
    private_directory(out)
    private_directory(archive.parent)
    require(not archive.exists() and not archive.is_symlink(), "preservation target already exists")
    lock_path = out / "capture.lock"
    initial = regular_info(lock_path)
    require(initial.st_uid == os.geteuid() and stat.S_IMODE(initial.st_mode) == 0o600 and initial.st_nlink == 1,
            "capture lock is not the original private regular file")
    identity = (initial.st_dev, initial.st_ino)
    # No O_CREAT: a missing lock is evidence requiring review, not a fresh run.
    fd = os.open(lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
    archive_created = False
    acquired = False
    protected = None
    original_os = cap.os

    def phase(name):
        _assert_lock(lock_path, fd, identity)
        if phase_observer is not None:
            phase_observer(name, lock_path, identity)

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        acquired = True
        phase("outer-lock-acquired")
        debris, protected = _inspect_incomplete(cap, out, expected_plan, expected_indices)
        archive.mkdir(mode=0o700, exist_ok=False)
        archive_created = True
        sync_directory(archive.parent)
        records = []
        preservation_directories = {archive}
        for source in debris:
            phase("preserving-uncommitted-file")
            relative = source.relative_to(out)
            target = archive / "files" / relative
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory = target.parent
            while directory != archive:
                preservation_directories.add(directory)
                directory = directory.parent
            record = _copy_preserved(source, target)
            records.append({"relative_path": str(relative), **record})
        # A durable leaf file/leaf directory does not make intervening new
        # directory entries durable. Sync every parent, bottom up, before the
        # receipt can authorize the collector's cleanup of canonical partials.
        for directory in sorted(preservation_directories, key=lambda p: len(p.parts), reverse=True):
            sync_directory(directory)
        exclusive_json(archive / "preservation.json", {
            "schema": "generation2-resume-preservation-draft-v1", "status": "durably-preserved",
            "collector_sha256": COLLECTOR_SHA, "capture_lock_identity": list(identity),
            "stored_plan_sha256": digest((out / "plan.json").read_bytes()),
            "completed_indices": list(expected_indices), "files": records,
            "live_source_reread_performed_by_preservation": False,
            **{key: False for key in FALSE_FLAGS}})
        phase("preservation-durable-before-collector")
        # Every cleanup candidate must still be the file that was preserved.
        for record in records:
            require(list(fingerprint(regular_info(out / record["relative_path"]))) ==
                    record["source_fingerprint"], "preserved original changed before collector")
        facade = _DupLockOS(lock_path, fd, identity, phase)
        cap.os = facade
        cap.receive(_ReplayHeader(header, incoming), _ObservedOutput(outgoing, phase), out, resume=True)
        require(facade.duplicates == 1, "collector did not consume the locked descriptor")
        # No canonical writes after original receive() returns. Its finally
        # closed only the duplicate fd; the exact pinned code has no LOCK_UN.
        phase("collector-returned-outer-still-held")
        exclusive_json(archive / "outcome.json", {
            "result": "segment-resume-returned", "fresh_fallback": False,
            "capture_completion_written": False, "assembly_started": False, "ready_written": False})
    except BaseException as error:
        if archive_created:
            exclusive_json(archive / "stopped.json", {
                "result": "stopped", "error_type": type(error).__name__, "error": str(error),
                "fresh_fallback": False, "automatic_retry": False,
                "capture_completion_written": False, "assembly_started": False, "ready_written": False})
        raise
    finally:
        cap.os = original_os
        try:
            _assert_lock(lock_path, fd, identity)
            if protected is not None:
                for relative, before in protected.items():
                    path = out / relative
                    after = fingerprint(regular_info(path)) if isinstance(before, tuple) else digest(path.read_bytes())
                    require(after == before, "committed historical chunk/plan changed")
            if acquired:
                phase("outer-before-close")
        finally:
            os.close(fd)
