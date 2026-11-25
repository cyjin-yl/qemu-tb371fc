#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bind a completed six-LUN capture to one original-Android observation.

The versioned binding is a technical input manifest, not an approval mechanism.
It must be constructed after the capture's hashes are known. This coordinator
does not collect device data, repair disks, modify boot inputs, or infer that an
assembled live capture is a consistent snapshot. It calls boot_android.main
once, under a caller-selected flock, and preserves all attempt evidence.

Preflight validates receipt chains and hashes small inputs and executables.
The original runner alone performs the full six-LUN digest read before boot.
A dry run does not call the runner or read every byte of the raw LUNs.
Generation2 freezes job manifests under ready.parent/captures/JOB/manifest.json;
their original host-side paths in the completion receipt may differ. The
retained super manifest continues to belong to the verified baseline capture.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import types

SCHEMA = "tb371fc-full-lun-boot-binding-v1"
MAPPED_SCHEMA = "tb371fc-full-lun-boot-binding-v2"
RESULT_SCHEMA = "tb371fc-full-lun-boot-result-v1"
MAX_JSON = 16 * 1024 * 1024
DEPENDENCIES = ("boot_qmp.py", "legacy_printk.py", "android_boot_inputs.py", "boot_android.py")
CORE = ("kernel", "initrd", "dtb", "bootargs")
FALSE_FLAGS = ("snapshot_consistent", "source_immutable_claim_verified",
               "pristine_factory_images", "restore_tested", "android_boot_tested")
# Literal data from the reviewed storage-batch-v3 preparer. These files remain
# evidence only: they are never imported or executed by this consumer.
MAPPED_PROVENANCE_VALIDATOR = (40425, "bc51a5f7342b88d788ef24d6cf52dcbe58f339682f40b5d2e3389471ed17ea69")
MAPPED_RUNTIME_PROFILES = {
    "v1": {
        "storage_supervision.py": (41853, "c7e0cf2ddac371c58b21112f2dc979bc53f28e0189cc80984e51fc73ad893255"),
        "host_supervisor.py": (613, "3ce70b95ef0f0df77f5282addbabbe3d79e589f14134cfc90cdef537f02fd59c"),
        "ssh_stdio_relay.py": (608, "70b32593fcc04995e2376dc6c35f95b8e8eb088c6e856093737f9cdcaa978e55"),
        "collector": (45483, "3158a48a1eb119b99465f573b464c86dc71647b8e23ecd87ca9d687ce889beb4"),
        "interface": (6100, "cb7f9b2572838f6758e9032d88ac691e3fca5d3a6706ad753f568e891e06abb0"),
    },
    "v2": {
        "storage_supervision_batch_v3.py": (42949, "8409e1ada6b41f6a1a5ec685cb267d890e3ed2151d5c6d265122d51c361a791a"),
        "host_supervisor_batch_v3.py": (622, "0fe349538fda3588ccc4f0735a6982a2adddcf215c87c3e84b056f55fe25976b"),
        "ssh_stdio_relay_batch_v3.py": (617, "fe011ad83ec50dec1486d39c0f2b46bf0d7de8ebe4a47492a490323bdd4a1dcc"),
        "collector": (45801, "89bd7f0c88d4f9853b5d92e2b70cbdcb635f98fd01a1622f72ea2a5f74b4032d"),
        "interface": (2340, "3e90eddc3b82a7a27de2916e7bba460b288bbd5b5d4b236a6aed82fec2962366"),
    },
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def integer(value, label, minimum=0):
    require(type(value) is int and value >= minimum, label + " must be an integer")
    return value


def sha_value(value, label):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value),
            label + " must be a lowercase SHA-256")
    return value


def path_value(value):
    require(isinstance(value, str) and value and not any(c in value for c in "\0\r\n"),
            "invalid path")
    path = Path(value)
    require(path.is_absolute() and path.resolve(strict=False) == path,
            "paths must be absolute, canonical and free of symlink aliases: " + value)
    return path


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def read_file(path, *, bounded=False):
    """Read/hash a stable regular file without following its final symlink."""
    path = path_value(str(path))
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), "not a regular file: " + str(path))
    require(not bounded or before.st_size <= MAX_JSON, "JSON exceeds size limit")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        require(identity(os.fstat(stream.fileno())) == identity(before), "file changed before open")
        digest, pieces = hashlib.sha256(), []
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            if bounded:
                pieces.append(chunk)
                require(sum(map(len, pieces)) <= MAX_JSON, "JSON grew beyond size limit")
        require(identity(os.fstat(stream.fileno())) == identity(before), "file changed while reading")
    require(identity(path.lstat()) == identity(before), "file changed after reading")
    return digest.hexdigest(), identity(before), b"".join(pieces) if bounded else None


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key: " + key)
        result[key] = value
    return result


def parse_json(raw):
    def bad_number(value):
        raise ValueError("non-finite JSON number: " + value)
    result = json.loads(raw, object_pairs_hook=unique_pairs, parse_constant=bad_number)
    require(type(result) is dict, "JSON root must be an object")
    return result


def write_json(path, value):
    raw = (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    directory = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class Inputs:
    """Keep raw LUN checks cheap; rehash every other bound input before/after."""
    def __init__(self):
        self.files = {}

    def file(self, record, *, raw_lun=False, json_file=False):
        require(type(record) is dict, "file binding must be an object")
        path = path_value(record.get("path"))
        expected = sha_value(record.get("sha256"), "file digest")
        if raw_lun:
            info = path.lstat()
            require(stat.S_ISREG(info.st_mode), "LUN must be a regular raw file")
            before, data = identity(info), None
        else:
            actual, before, data = read_file(path, bounded=json_file)
            require(actual == expected, "SHA-256 mismatch: " + str(path))
        if "size" in record:
            require(before[3] == integer(record["size"], "bound file size", 1),
                    "bound file size mismatch: " + str(path))
        previous = self.files.get(str(path))
        item = {"identity": before, "sha256": expected, "raw_lun": raw_lun}
        require(previous is None or previous == item, "conflicting bindings for one file")
        self.files[str(path)] = item
        return parse_json(data) if json_file else path

    def recheck(self):
        for name, record in self.files.items():
            path = path_value(name)
            require(identity(path.lstat()) == record["identity"], "bound input changed: " + name)
            if not record["raw_lun"]:
                actual, current, _ = read_file(path)
                require(actual == record["sha256"] and current == record["identity"],
                        "bound input digest changed: " + name)


def false_flags(value, fields=FALSE_FLAGS):
    for flag in fields:
        require(value.get(flag) is False, "qualification must remain false: " + flag)


def six_luns(rows):
    require(type(rows) is list and len(rows) == 6, "exactly six original LUNs are required")
    require(all(type(row) is dict and type(row.get("lun")) is int for row in rows), "invalid LUN entry")
    require(sorted(row["lun"] for row in rows) == list(range(6)), "missing, duplicate or mixed LUN set")
    return sorted(rows, key=lambda row: row["lun"])


def check_raw_record(actual, expected):
    for field in ("lun", "path", "sha256", "logical_block_size", "logical_block_count"):
        require(actual.get(field) == expected[field], "LUN identity/geometry mismatch: " + field)
    require(actual.get("format") == "raw", "only original raw LUNs are supported")


@contextlib.contextmanager
def bound_modules(hashes):
    """Execute verified source bytes, never a previously imported/pyc module.

    Existing runner modules use absolute sibling imports. Temporarily install
    this exact set for those imports, including the import inside main(), then
    restore the caller's module table. The CLI itself is a single-use process.
    """
    modules, previous = {}, {}
    try:
        for filename in DEPENDENCIES:
            path = Path(__file__).resolve().parent / filename
            actual, _, raw = read_file(path, bounded=True)
            require(actual == hashes[filename], "runner dependency changed before loading")
            name = path.stem
            previous[name] = sys.modules.get(name)
            module = types.ModuleType(name)
            module.__file__ = str(path)
            sys.modules[name] = modules[name] = module
            exec(compile(raw, str(path), "exec"), module.__dict__)
        yield modules
    finally:
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def invoke_runner(argv, dependency_hashes):
    """Single execution boundary; tests may replace only this function."""
    with bound_modules(dependency_hashes) as modules:
        return modules["boot_android"].main(argv)


def check_properties(binding, tracked):
    machine = binding.get("machine")
    globals_ = binding.get("global_properties")
    require(isinstance(machine, str) and machine.split(",", 1)[0] == "tb371fc",
            "an explicit tb371fc machine is required")
    require(type(globals_) is list and all(isinstance(x, str) for x in globals_), "invalid global properties")
    parts = machine.split(",")[1:] + globals_
    require(not any(c in machine for c in "\0\r\n"), "invalid machine text")
    references, seen = set(), set()
    for part in parts:
        require("=" in part and not any(c in part for c in "\0\r\n"), "invalid property")
        key, value = part.split("=", 1)
        require(key and not any(c.isspace() for c in key) and key not in seen, "duplicate/invalid property")
        seen.add(key)
        file_property = (key.split(".")[-1] in ("rpmh-cmd-db", "aop-cmd-db", "aop-command-db") or
                         re.search(r"(?:^|[_.-])(?:file|filename|path)$", key) is not None)
        if "/" in value or (value and file_property):
            references.add(str(path_value(value)))
    resources = binding.get("resources")
    require(type(resources) is list, "resources must explicitly list consumed files")
    paths = [str(tracked.file(record)) for record in resources]
    require(len(paths) == len(set(paths)) and set(paths) == references,
            "machine/global file references must exactly match bound resources")
    require("ufs-enabled=off" not in machine.split(","), "full storage needs the UFS controller")
    return machine, globals_


def origin_path(value):
    """Historical host paths are identifiers, never paths to open in this process."""
    require(type(value) is str and value and not any(c in value for c in "\0\r\n"),
            "invalid original evidence path")
    path = Path(value)
    require(path.is_absolute() and not value.startswith("//") and str(path) == value and ".." not in path.parts,
            "original evidence path must be canonical")
    return path


def metadata_pin(value):
    require(type(value) is dict and set(value) == {"path", "bytes", "sha256"},
            "mapped evidence requires full path/bytes/SHA-256 pins")
    origin_path(value["path"])
    require(integer(value["bytes"], "mapped evidence bytes", 1) <= 2 * 1024 * 1024,
            "mapped evidence exceeds size limit")
    sha_value(value["sha256"], "mapped evidence digest")
    return value


def bound_metadata(tracked, pin, *, json_file=True):
    metadata_pin(pin)
    return tracked.file({"path": pin["path"], "sha256": pin["sha256"], "size": pin["bytes"]},
                        json_file=json_file)


def linked_json(tracked, path):
    """Track a small sibling whose entire consumed content is checked by its caller."""
    digest, _, _ = read_file(path, bounded=True)
    return tracked.file({"path": str(path), "sha256": digest}, json_file=True)


def process_identity(value, boot):
    require(type(value) is dict and value.get("boot_id") == boot, "mapped process boot identity differs")
    return (boot, integer(value.get("pid"), "mapped process PID", 1),
            integer(value.get("start_ticks"), "mapped process start", 1))


def validate_mapped_source(outer, stop, source_boot, request, request_sha, ack, budget):
    """Read the RAM observation as data, including the exact three-process end proof."""
    attempt = request["attempt_id"]
    source = outer.get("source_result", {})
    require(outer.get("schema") == "tb371fc-storage-memory-call-outer-v1" and outer.get("attempt_id") == attempt and
            outer.get("worker_reaped") is True and outer.get("output_limit_reached") is False and
            outer.get("persistent_output_files_written") is False and
            integer(outer.get("root_process_wait_returncode"), "mapped source wait") == 0 and
            source.get("schema") == "tb371fc-storage-memory-sender-result-v2" and source.get("attempt_id") == attempt and
            source.get("host_request_sha256") == request_sha and source.get("job_name") == request["authorization"]["job_name"] and
            source.get("collector_sha256") == request["runtime"]["collector"]["sha256"] and
            source.get("source_boot_id") == source_boot and source.get("source_call_passed") is True and
            source.get("ssh_child_reaped") is True and integer(source.get("ssh_exit_code"), "mapped SSH exit") == 0 and
            source.get("acknowledgement") == ack and source.get("source_open_mode") == "O_RDONLY" and
            source.get("control_output_filesystem") == "tmpfs", "mapped source completion provenance differs")
    false_flags(source, ("automatic_retry_permitted", "tablet_persistent_files_created_or_written",
                         "tablet_block_source_written", "ssh_terminate_sent", "ssh_kill_sent", "alarm_fired"))
    if request["schema"].endswith("-v2"):
        require(integer(source.get("max_new_chunks"), "mapped source budget", 1) == budget and
                integer(source.get("max_new_bytes"), "mapped source byte bound", 1) == budget * 67108864,
                "mapped source budget differs")
    for stream in ("stdout", "stderr"):
        raw = base64.b64decode(outer[stream + "_base64"], validate=True)
        require(len(raw) == integer(outer.get(stream + "_bytes"), "mapped stream bytes") and
                hashlib.sha256(raw).hexdigest() == sha_value(outer.get(stream + "_sha256"), "mapped stream digest"),
                "mapped source stream differs")
        if stream == "stdout":
            require(parse_json(raw) == source, "mapped source JSON differs from retained stdout")
    worker = process_identity(outer["worker_process"], source_boot)
    controller = process_identity(outer["controller_process"], source_boot)
    sender = (source_boot, integer(source.get("source_pid"), "mapped sender PID", 1),
              integer(source.get("source_start_ticks"), "mapped sender start", 1))
    ssh = (source_boot, integer(source.get("ssh_pid"), "mapped SSH PID", 1),
           integer(source.get("ssh_start_ticks"), "mapped SSH start", 1))
    require(worker == sender and len({worker[1], controller[1], ssh[1]}) == 3,
            "mapped source/controller/SSH identities differ")
    require(stop.get("schema") == "tb371fc-storage-source-termination-and-environment-v1" and
            stop.get("attempt_id") == attempt and stop.get("boot_id") == source_boot and
            integer(stop.get("uid"), "mapped source UID") == 0 and stop.get("source_lock_available") is True,
            "mapped source termination context differs")
    processes = stop.get("processes")
    require(type(processes) is list and len(processes) == 3 and
            {(integer(p.get("pid"), "ended PID", 1), integer(p.get("expected_start_ticks"), "ended start", 1))
             for p in processes} == {(p[1], p[2]) for p in (worker, controller, ssh)},
            "mapped termination does not cover exact source identities")
    for proc in processes:
        require(proc.get("same_identity_running") is False and (proc.get("present") is False or
                (proc.get("present") is True and type(proc.get("observed_start_ticks")) is int and
                 type(proc.get("state")) is str and (proc["observed_start_ticks"] != proc["expected_start_ticks"] or
                                                     proc["state"] == "Z"))),
                "mapped source identity has not ended")


def validate_mapped_completion(binding, ready, tracked, luns, captures):
    """Validate frozen mapped receipts as data; never import acquisition code."""
    require(binding["capture_counts"] == {"jobs": 107, "assembly_manifests": 108, "chunks": 1842},
            "mapped capture counts must be exactly 107/108/1842")
    require("generation_completion" not in binding and "generation_completion_sha256" not in ready,
            "mapped binding must not mix legacy completion fields")
    require(ready.get("schema") == "tb371fc-mapped-generation2-ready-v1" and
            ready.get("original_generation_completion_used") is False,
            "mapped ready schema/qualification differs")
    root = path_value(binding["ready"]["path"]).parent
    complete_pin = metadata_pin(binding["mapped_generation_completion"])
    require(complete_pin["path"] == str(root / "mapped-generation2-complete.json"),
            "mapped completion is outside the selected assembly")
    completion = bound_metadata(tracked, complete_pin)
    require(completion.get("schema") == "tb371fc-generation2-mapped-verification-v1" and
            completion.get("original_generation_completion_used") is False and
            completion.get("selected_compressed_and_decoded_bytes_verified") is True and
            "generation2_capture_complete" not in completion and
            "chunk_bytes_rehashed_at_finalization" not in completion,
            "mapped completion schema/verification differs")
    false_flags(completion, FALSE_FLAGS + ("all_backups_complete",))
    for field, value in (("selected_jobs_verified", 107), ("assembly_manifests", 108),
                         ("new_job_chunks_verified", 1794), ("retained_super_chunks_expected", 48),
                         ("assembly_chunks_expected", 1842)):
        require(integer(completion.get(field), field, 1) == value, "mapped completion counts differ")
    require(completion.get("source") == luns[0]["source"], "mapped completion source differs")
    index_pin = metadata_pin(binding["frozen_evidence_index"])
    require(index_pin["path"] == str(root / "selected-evidence-index.json") and
            index_pin["sha256"] == completion.get("frozen_evidence_index_sha256"),
            "mapped frozen index binding differs")
    index = bound_metadata(tracked, index_pin)
    require(set(index) == {"files"} and type(index["files"]) is list and 0 < len(index["files"]) <= 4096,
            "invalid mapped frozen index")
    evidence, targets, used = {}, set(), set()
    for number, row in enumerate(index["files"]):
        require(type(row) is dict and set(row) == {"original", "frozen_path"}, "invalid frozen index row")
        pin = metadata_pin(row["original"])
        target = str(path_value(row["frozen_path"]))
        require(pin["path"] not in evidence and target not in targets and
                target == str(root / "selected-evidence" / ("%04d.bin" % number)),
                "duplicate or misplaced frozen evidence mapping")
        frozen = {**pin, "path": target}
        bound_metadata(tracked, frozen, json_file=False)
        evidence[pin["path"]] = (pin, frozen)
        targets.add(target)

    def resolve(pin, *, json_file=True):
        metadata_pin(pin)
        require(pin["path"] in evidence and evidence[pin["path"]][0] == pin,
                "missing or conflicting original evidence pin")
        used.add(pin["path"])
        return bound_metadata(tracked, evidence[pin["path"]][1], json_file=json_file)

    dependencies = binding.get("evidence_dependencies")
    require(type(dependencies) is list and len(dependencies) == 1, "exact frozen evidence dependency required")
    dependency_paths = set()
    for pin in dependencies:
        metadata_pin(pin)
        require(pin["path"] not in dependency_paths, "duplicate evidence dependency")
        require(origin_path(pin["path"]).name == "prepare_batch_attempt_v3.py" and
                (pin["bytes"], pin["sha256"]) == MAPPED_PROVENANCE_VALIDATOR,
                "unreviewed mapped provenance-validator dependency")
        dependency_paths.add(pin["path"])
        resolve(pin, json_file=False)
    selection = resolve(completion["selection_map"])
    selection_pin = metadata_pin(binding["selection_map"])
    require(selection_pin == evidence[completion["selection_map"]["path"]][1] and
            ready.get("selection_map_sha256") == selection_pin["sha256"],
            "mapped selection binding differs")
    require(set(selection) == {"schema", "original_plan", "source_binding", "retained_super",
                              "replacement_root", "source_boot_id", "source", "jobs", *FALSE_FLAGS} and
            selection["schema"] == "tb371fc-generation2-selected-captures-v1" and
            selection["source"] == luns[0]["source"], "mapped selection schema/source differs")
    false_flags(selection)
    require(type(selection["source_boot_id"]) is str and selection["source_boot_id"], "missing source boot identity")
    replacement_root = origin_path(selection["replacement_root"])
    for field, digest in (("original_plan", binding["generation_plan_sha256"]),
                          ("source_binding", binding["source_binding_sha256"])):
        require(completion[field] == selection[field] and selection[field]["sha256"] == digest,
                "mapped original metadata pin differs")
    original = resolve(selection["original_plan"])
    source_binding = resolve(selection["source_binding"])
    false_flags(original)
    false_flags(source_binding, FALSE_FLAGS + ("all_backups_complete",))
    require(original.get("schema") == "tb371fc-generation2-segmented-capture-v1" and
            integer(original.get("generation"), "generation", 1) == 2 and
            original.get("source_open_mode") == "O_RDONLY" and
            integer(original.get("chunk_bytes"), "chunk bytes", 1) == 67108864,
            "mapped original plan profile differs")
    job_chunk_bytes = original["chunk_bytes"]
    for field, value in (("expected_jobs", 107), ("expected_assembly_manifests", 108),
                         ("expected_new_chunks", 1794)):
        require(integer(original.get(field), field, 1) == value, "mapped plan counts differ")
    require(all(original["source"].get(key) == luns[0]["source"][key]
                for key in ("path", "bytes", "logical_block_size")) and
            source_binding.get("source") == luns[0]["source"] and
            source_binding.get("jobs_plan_sha256") == binding["generation_plan_sha256"] and
            source_binding.get("collector_sha256") == sha_value(original.get("collector_sha256"), "collector"),
            "mapped source binding differs")
    super_pin = selection["retained_super"]
    require(super_pin["path"] == source_binding.get("reference_manifest") == original["retained_super"]["manifest"] and
            super_pin["sha256"] == source_binding.get("reference_manifest_sha256") == captures["super"]["manifest_sha256"] and
            captures["super"]["chunks"] == 48, "mapped retained super differs")
    super_manifest = resolve(super_pin)
    false_flags(super_manifest)
    require(super_manifest.get("schema") == "tb371fc-readonly-range-stream-v1" and
            super_manifest.get("status") == "verified-range-copies" and
            super_manifest["plan"]["source"] == luns[0]["source"] and
            super_manifest["plan"]["ranges"] == captures["super"]["ranges"] and
            type(super_manifest["chunks"]) is list and len(super_manifest["chunks"]) == 48,
            "mapped super evidence differs")
    super_chunk_bytes = integer(super_manifest["plan"].get("chunk_bytes"), "retained super chunk bytes", 1)
    require(super_chunk_bytes % 4096 == 0 and
            sum((integer(row["length"], "super range length", 1) + super_chunk_bytes - 1) // super_chunk_bytes
                for row in super_manifest["plan"]["ranges"]) == 48, "mapped super chunk geometry differs")
    names = ["lun0-prefix"] + ["lun0-tail-%03d" % n for n in range(106)]
    jobs, selected, completed = original.get("jobs"), selection.get("jobs"), completion.get("jobs")
    require(all(type(rows) is list and len(rows) == 107 for rows in (jobs, selected, completed)),
            "mapped jobs are incomplete")
    require(original.get("assembly_manifest_paths") == [super_pin["path"]] + [job["manifest"] for job in jobs],
            "mapped original assembly ordering differs")
    views, physical_paths, chunk_total, replacement_chunks = [], set(), 0, 0
    for number, (name, job, entry, result) in enumerate(zip(names, jobs, selected, completed)):
        require(job.get("name") == entry.get("name") == result.get("name") == name,
                "mapped logical job order differs")
        require(set(entry) == {"name", "selection", "capture_directory", "manifest", "provenance"} and
                set(result) == {"name", "selection", "original_logical_manifest", "selected_manifest", "chunks", "worker_receipt"},
                "mapped job fields differ")
        target = origin_path(job["target"])
        directory = origin_path(entry["capture_directory"])
        pin = metadata_pin(entry["manifest"])
        count = integer(job.get("expected_chunks"), "expected job chunks", 1)
        require(job["manifest"] == str(target / "manifest.json") and
                pin["path"] == str(directory / "manifest.json") and pin["path"] not in physical_paths and
                result["original_logical_manifest"] == job["manifest"] and result["selected_manifest"] == pin and
                result["selection"] == entry["selection"] and integer(result["chunks"], "verified job chunks", 1) == count,
                "mapped selected job provenance differs")
        physical_paths.add(pin["path"])
        if number < 82:
            require(entry["selection"] == "retained" and directory == target and entry["provenance"] is None,
                    "mapped retained job was replaced")
        else:
            require(entry["selection"] == "replacement" and directory.parent == replacement_root and
                    re.fullmatch(re.escape(name) + r"-fresh-[0-9]{3}", directory.name),
                    "mapped replacement directory differs")
            replacement_chunks += count
        ranges = job["ranges"]
        require(type(ranges) is list and ranges and job["device"] == luns[0]["source"]["path"] and
                job["source_bytes"] == luns[0]["bytes"] and
                sum((integer(row["length"], "job range length", 1) + job_chunk_bytes - 1) // job_chunk_bytes for row in ranges) == count,
                "mapped job source/chunk geometry differs")
        frozen = root / "captures" / name
        manifest = bound_metadata(tracked, {**pin, "path": str(frozen / "manifest.json")})
        false_flags(manifest)
        require(manifest.get("schema") == "tb371fc-readonly-range-stream-v1" and
                manifest.get("status") == "verified-range-copies" and
                manifest["plan"]["source"] == luns[0]["source"] and manifest["plan"]["ranges"] == ranges and
                integer(manifest["plan"]["chunk_bytes"], "capture chunk bytes", 1) == job_chunk_bytes and
                manifest["plan"]["source_open_mode"] == "O_RDONLY" and
                manifest["plan"]["snapshot_consistent"] is False and
                manifest["plan"].get("gpt") is None and manifest.get("whole_source_bytes_captured") is False and
                manifest.get("live_mutable_ranges") == [row["label"] for row in ranges] and
                type(manifest["chunks"]) is list and len(manifest["chunks"]) == count,
                "mapped frozen capture differs")
        require(linked_json(tracked, frozen / "plan.json") == manifest["plan"], "mapped saved plan differs")
        copied = linked_json(tracked, frozen / "frozen-copy.json")
        require(copied == {"manifest_sha256": pin["sha256"], "original_capture": str(directory),
                           "compressed_copies_verified": True, "assembly_tested": False, "android_boot_tested": False},
                "mapped frozen-copy provenance differs")
        if entry["selection"] == "replacement":
            proof = entry["provenance"]
            require(type(proof) is dict and set(proof) == {"request", "receiver_terminal", "relay_terminal",
                    "source_outer", "source_termination", "host_termination"}, "mapped completion evidence is incomplete")
            require(all(origin_path(pin["path"]).is_relative_to(replacement_root.parent) for pin in proof.values()),
                    "mapped completion evidence is outside its namespace")
            docs = {key: resolve(value) for key, value in proof.items()}
            request = docs["request"]
            legacy = request.get("schema") == "tb371fc-storage-supervised-attempt-v1"
            require(legacy or request.get("schema") == "tb371fc-storage-supervised-attempt-v2",
                    "unsupported mapped acquisition request schema")
            budget = 1 if legacy else integer(request.get("max_new_chunks"), "mapped batch budget", 1)
            require(budget in (1, 4) and (not legacy or "max_new_chunks" not in request), "mapped batch budget differs")
            attempt = request.get("attempt_id")
            require(type(attempt) is str and re.fullmatch(r"attempt-[0-9]{6}", attempt) and
                    request["namespace"] == str(replacement_root.parent) and
                    request["capture_directory"] == str(directory) and request["authorization"] == {
                "job_name": name, "jobs_plan_sha256": binding["generation_plan_sha256"],
                "source_binding_sha256": binding["source_binding_sha256"]}, "mapped request provenance differs")
            host_boot = request["host"]["boot_id"]
            require(type(host_boot) is str and host_boot and integer(request["host"].get("uid"), "host UID") == 1000,
                    "mapped host identity differs")
            require(origin_path(request["expected_plan"]["path"]).is_relative_to(replacement_root.parent / "bindings"),
                    "mapped request plan is outside its binding directory")
            require(resolve(request["expected_plan"]) == manifest["plan"], "mapped request plan differs")
            profile = MAPPED_RUNTIME_PROFILES["v1" if legacy else "v2"]
            require(type(request["runtime"]) is dict and set(request["runtime"]) == set(profile),
                "mapped runtime profile names differ")
            for runtime_name, runtime_pin in request["runtime"].items():
                if runtime_name == "interface":
                    expected_path = replacement_root.parent / "bindings" / (
                        "interface-contract-002.json" if legacy else "interface-contract-batch-v3.json")
                else:
                    filename = ("acquire_block_ranges_resume_v2.py" if legacy else "acquire_block_ranges_batch_v3.py") \
                        if runtime_name == "collector" else runtime_name
                    expected_path = replacement_root.parent / "code" / filename
                metadata_pin(runtime_pin)
                require(runtime_pin["path"] == str(expected_path) and
                        (runtime_pin["bytes"], runtime_pin["sha256"]) == profile[runtime_name],
                        "unreviewed mapped runtime profile")
                resolve(runtime_pin, json_file=False)
            receiver, relay = docs["receiver_terminal"], docs["relay_terminal"]
            ack = {"complete": True, "chunks": count, "manifest": pin["path"], "snapshot_consistent": False}
            for role, terminal in (("receiver", receiver), ("relay", relay)):
                require(terminal.get("schema") == "tb371fc-storage-" + role + "-terminal-v1" and
                        terminal.get("request_sha256") == proof["request"]["sha256"] and
                        terminal.get("host_boot_id") == host_boot and
                        integer(terminal.get("exit_code"), "mapped endpoint exit") == 0 and
                        terminal.get("error") is None, "mapped endpoint request/exit differs")
                false_flags(terminal, ("snapshot_consistent", "whole_storage_backup_complete_claimed"))
                require(proof[role + "_terminal"]["path"] == str(replacement_root.parent / "attempts" / attempt / (role + "-terminal.json")),
                        "mapped terminal path differs from its request")
            before = integer(request.get("expected_completed_chunks"), "mapped capture prefix")
            added = integer(receiver.get("actual_new_chunks"), "mapped completed chunks")
            require(receiver.get("outcome") == "host-complete" and receiver.get("clean_host_protocol") is True and
                    receiver.get("capture_directory") == str(directory) and
                    receiver.get("acknowledgement") == ack and 0 <= before <= count and
                    added == count - before == min(budget, count - before) and
                    integer(receiver.get("max_new_chunks"), "mapped receiver budget", 1) == budget and
                    integer(receiver.get("max_new_bytes"), "mapped receiver byte bound", 1) == budget * 67108864 and
                    relay.get("outcome") == "drained" and relay.get("supervisor_termination_confirmed_before_exit0") is True,
                    "mapped endpoints did not complete the exact bounded capture")
            require(relay.get("stdin_eof") is True and relay.get("response_eof") is True and
                    integer(relay.get("undelivered_to_receiver_bytes"), "undelivered receiver bytes") == 0 and
                    integer(relay.get("undelivered_to_source_bytes"), "undelivered source bytes") == 0,
                    "mapped relay did not drain")
            for sent, received in (("source_read", "receiver_written"), ("receiver_read", "source_written")):
                require(integer(relay["counts"].get(sent), "relay sent bytes", 1) ==
                        integer(relay["counts"].get(received), "relay received bytes", 1) and
                        sha_value(relay["sha256"].get(sent), "relay sent digest") == relay["sha256"].get(received),
                        "mapped relay byte accounting differs")
            validate_mapped_source(docs["source_outer"], docs["source_termination"], selection["source_boot_id"],
                                   request, proof["request"]["sha256"], ack, budget)
            host = docs["host_termination"]
            require(set(host) == {"evidence", "result"} and resolve(host["evidence"]) == host["result"],
                    "mapped host packet differs from its retained observation")
            require(origin_path(host["evidence"]["path"]).parent == replacement_root.parent / "preparations",
                    "mapped host observation is outside its preparation directory")
            require(host["result"].get("schema") == "tb371fc-storage-host-attempt-stop-review-v1" and
                    host["result"].get("receiver_identity_running") is False and
                    host["result"].get("relay_identity_running") is False and
                    host["result"].get("source_result_passed") is True and host["result"].get("signal_sent") is False,
                    "mapped host completion observation differs")
            host_rows = host["result"]["files"]
            require(type(host_rows) is list and len({row["name"] for row in host_rows}) == len(host_rows),
                    "duplicate retained host file")
            for row in host_rows:
                require(type(row["name"]) is str and Path(row["name"]).name == row["name"] and
                        row["name"] not in (".", ".."), "invalid retained host file name")
                require(row.get("mode") == "0o600" and integer(row.get("inode"), "retained host inode", 1),
                        "invalid retained host file metadata")
                if row["kind"] == "regular":
                    original_path = origin_path(request["namespace"]) / "attempts" / request["attempt_id"] / row["name"]
                    require(resolve({"path": str(original_path), "bytes": row["bytes"], "sha256": row["sha256"]}) == row["json"],
                            "mapped retained host file differs")
                    if "request_sha256" in row["json"]:
                        require(row["json"]["request_sha256"] == proof["request"]["sha256"],
                                "mapped retained host request differs")
                else:
                    require(row["kind"] == "fifo" and row["name"] in ("source-to-receiver.fifo", "receiver-to-source.fifo"),
                            "unexpected retained host file kind")
            named = {row["name"]: row for row in host_rows}
            for role, identity_field in (("receiver", "expected_identity"), ("relay", "relay_identity")):
                start = named[role + "-start.json"]["json"]
                terminal_row = named[role + "-terminal.json"]
                terminal = terminal_row["json"]
                require(proof[role + "_terminal"] == {
                    "path": str(replacement_root.parent / "attempts" / attempt / terminal_row["name"]),
                    "bytes": terminal_row["bytes"], "sha256": terminal_row["sha256"]} and
                    terminal == docs[role + "_terminal"] and
                    process_identity(start["process"], host_boot) == process_identity(terminal["process"], host_boot) ==
                    process_identity(host["result"][identity_field], host_boot), "mapped host endpoint identity differs")
            admission = named["receiver-start.json"]["json"]["admission"]
            require(admission.get("verified") is True and admission.get("background_daemon") is False,
                    "mapped receiver lacks foreground admission")
            pane_rows = [line.split("|") for line in host["result"]["panes"].splitlines()]
            own_pane = [row for row in pane_rows if len(row) == 5 and row[1] == admission["tmux_pane"]]
            require(len(own_pane) == 1 and own_pane[0][2:4] == ["1", "0"] and own_pane[0][4].isdecimal(),
                    "mapped receiver pane has not ended cleanly")
        worker_pin = metadata_pin(result["worker_receipt"])
        require(worker_pin["path"] == str(root / ("job-" + name + "-result.json")), "mapped worker path differs")
        worker = bound_metadata(tracked, worker_pin)
        false_flags(worker, ("snapshot_consistent", "pristine_factory_images", "restore_tested", "android_boot_tested"))
        require(worker.get("schema") == "tb371fc-reboot-resume-verification-v1" and worker.get("verified") is True and
                worker.get("raws") == [] and worker.get("captures") == {name: {
                    "manifest_sha256": pin["sha256"], "source": luns[0]["source"], "chunks": count, "ranges": ranges}},
                "mapped worker result differs from selected capture")
        worker_plan = tracked.file({"path": str(root / ("job-" + name + "-plan.json")),
                                    "sha256": sha_value(worker.get("plan_sha256"), "mapped worker plan")}, json_file=True)
        require(set(worker_plan) == {"tools", "captures", "raws", "result"} and worker_plan["raws"] == [] and
                worker_plan["result"] == worker_pin["path"] and worker_plan["captures"] == [{
                    "name": name, "source": luns[0]["source"]["path"], "bytes": luns[0]["bytes"],
                    "ranges": [[row[key] for key in ("label", "offset", "length", "classification")] for row in ranges],
                    "path": str(frozen), "manifest_sha256": pin["sha256"], "original_capture": str(directory)}],
                "mapped worker plan provenance differs")
        views.append({"name": name, "manifest": str(frozen / "manifest.json"),
                      "manifest_sha256": pin["sha256"], "chunks": count, "ranges": ranges})
        chunk_total += count
    require(chunk_total == 1794 and replacement_chunks == 385, "mapped job chunk totals differ")
    require(set(evidence) == used, "unreferenced extra frozen evidence mapping")
    return views


def validate_receipts(binding, ready, tracked, luns):
    mapped = binding.get("schema") == MAPPED_SCHEMA
    counts = binding["capture_counts"]
    jobs = integer(counts.get("jobs"), "capture job count", 1)
    manifests = integer(counts.get("assembly_manifests"), "assembly manifest count", 2)
    chunks = integer(counts.get("chunks"), "capture chunk count", 1)
    require(manifests == jobs + 1 and manifests <= 128, "invalid capture counts")
    require(ready.get("all_six_original_luns_assembled") is True and
            ready.get("old_partials_preserved") is True and
            integer(ready.get("generation2_jobs_verified"), "ready jobs", 1) == jobs and
            integer(ready.get("assembly_manifests"), "ready manifests", 2) == manifests,
            "full-LUN ready receipt is incomplete")
    false_flags(ready, FALSE_FLAGS + ("all_backups_complete",))
    for field in ("generation_plan_sha256", "source_binding_sha256"):
        require(ready.get(field) == sha_value(binding.get(field), field), "ready generation/source binding differs")
    completion_link = (("mapped_generation_completion_sha256", "mapped_generation_completion") if mapped else
                       ("generation_completion_sha256", "generation_completion"))
    for field, key in (completion_link,
                       ("new_lun0_assembly_receipt_sha256", "lun0_assembly")):
        require(ready.get(field) == binding[key]["sha256"], "ready receipt hash differs: " + field)
    for field in ("prepared_manifest", "baseline_verification"):
        require(ready.get(field) == binding[field]["path"], "ready path differs: " + field)

    baseline = tracked.file(binding["baseline_verification"], json_file=True)
    require(baseline.get("schema") == "tb371fc-reboot-resume-verification-v1" and
            baseline.get("verified") is True, "baseline verification is incomplete")
    sha_value(baseline.get("plan_sha256"), "baseline verification plan")
    false_flags(baseline, ("snapshot_consistent", "pristine_factory_images", "restore_tested", "android_boot_tested"))
    captures = baseline["captures"]
    for lun in luns:
        name = "super" if lun["lun"] == 0 else "lun" + str(lun["lun"])
        require(captures[name]["source"] == lun["source"], "baseline capture source identity differs")
        sha_value(captures[name]["manifest_sha256"], "baseline capture manifest")
        integer(captures[name]["chunks"], "baseline chunk count", 1)
    require(type(baseline.get("raws")) is list and len(baseline["raws"]) == 5 and
            sorted(row.get("lun") for row in baseline["raws"]) == list(range(1, 6)), "baseline LUN set differs")
    for row in baseline["raws"]:
        check_raw_record(row, luns[row["lun"]])
        sha_value(row.get("verification_receipt_sha256"), "baseline raw receipt")

    if mapped:
        completed = validate_mapped_completion(binding, ready, tracked, luns, captures)
    else:
        completion = tracked.file(binding["generation_completion"], json_file=True)
        false_flags(completion, FALSE_FLAGS + ("all_backups_complete",))
        require(completion.get("generation2_capture_complete") is True and
                integer(completion.get("completed_jobs"), "completed jobs", 1) == jobs and
                integer(completion.get("assembly_manifests"), "completed manifests", 2) == manifests and
                completion.get("jobs_plan_sha256") == binding["generation_plan_sha256"] and
                completion.get("source_binding_sha256") == binding["source_binding_sha256"] and
                completion.get("source") == luns[0]["source"] and
                completion.get("chunk_bytes_rehashed_at_finalization") is False,
                "generation completion binding differs")
        completed = completion["jobs"]
    require(type(completed) is list and len(completed) == jobs, "generation has incomplete job list")
    names, source_paths = set(), set()
    expected_hashes = [captures["super"]["manifest_sha256"]]
    total_chunks = captures["super"]["chunks"]
    for job in completed:
        require(isinstance(job.get("name"), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", job["name"]) and
                job["name"] not in names,
                "duplicate or invalid generation job")
        names.add(job["name"])
        path = str(path_value(job.get("manifest")))
        require(path not in source_paths, "duplicate generation job manifest")
        source_paths.add(path)
        expected_hashes.append(sha_value(job.get("manifest_sha256"), "generation job manifest"))
        total_chunks += integer(job.get("chunks"), "generation job chunks", 1)
    require(total_chunks == chunks, "generation chunk total differs")

    assembly = tracked.file(binding["lun0_assembly"], json_file=True)
    false_flags(assembly, FALSE_FLAGS + ("pristine_factory",))
    require(assembly.get("schema") == "tb371fc-whole-lun-assembly-v1" and
            assembly.get("status") == "verified-whole-lun-assembly" and
            assembly.get("source") == luns[0]["source"] and
            integer(assembly.get("chunks_verified"), "assembly chunks", 1) == chunks,
            "LU0 assembly identity differs")
    for flag in ("whole_source_bytes_captured", "coverage_nonoverlapping", "raw_readback_verified"):
        require(assembly.get(flag) is True, "LU0 assembly lacks " + flag)
    raw = assembly["raw"]
    require(raw == {"path": luns[0]["path"], "format": "raw", "bytes": luns[0]["bytes"],
                    "sha256": luns[0]["sha256"]}, "LU0 raw differs from binding")
    sources = assembly["source_manifests"]
    require(type(sources) is list and len(sources) == manifests and
            [item.get("sha256") for item in sources] == expected_hashes,
            "LU0 assembly does not bind the exact super and generation jobs")
    require(sources[0].get("ranges") == captures["super"].get("ranges"),
            "LU0 assembly changed the retained super range metadata")
    require(len({str(path_value(item.get("path"))) for item in sources}) == manifests,
            "duplicate LU0 assembly source paths")
    frozen_root = path_value(binding["ready"]["path"]).parent / "captures"
    require([item["path"] for item in sources[1:]] ==
            [str(frozen_root / job["name"] / "manifest.json") for job in completed],
            "LU0 assembly job manifests differ from the generation2 frozen directory mapping")
    if mapped:
        require([item.get("ranges") for item in sources[1:]] == [job["ranges"] for job in completed],
                "LU0 assembly changed the selected job range metadata")
    ranges = []
    for source in sources:
        require(type(source.get("ranges")) is list and source["ranges"], "missing assembly ranges")
        for row in source["ranges"]:
            offset, length = integer(row.get("offset"), "range offset"), integer(row.get("length"), "range length", 1)
            require(offset % 4096 == length % 4096 == 0, "misaligned assembly range")
            ranges.append((offset, length))
    cursor = 0
    for offset, length in sorted(ranges):
        require(offset == cursor, "LU0 assembly coverage has a gap or overlap")
        cursor += length
    require(cursor == luns[0]["bytes"], "LU0 assembly coverage is incomplete")


def validate(binding, ready_path, tracked, out):
    require(binding.get("schema") in (SCHEMA, MAPPED_SCHEMA), "unsupported full-LUN binding schema")
    require(str(ready_path) == binding["ready"]["path"], "CLI ready path differs from binding")
    ready = tracked.file(binding["ready"], json_file=True)
    hashes = binding.get("runner_dependencies")
    require(type(hashes) is dict and set(hashes) == set(DEPENDENCIES), "bind all four runner dependencies")
    for filename in DEPENDENCIES:
        tracked.file({"path": str(Path(__file__).resolve().parent / filename), "sha256": hashes[filename]})
    prepared = tracked.file(binding["prepared_manifest"], json_file=True)
    with bound_modules(hashes) as modules:
        prepared = modules["android_boot_inputs"].load_prepared_manifest(
            binding["prepared_manifest"]["path"], verify_hashes=False)
    luns, actual_luns = six_luns(binding.get("luns")), six_luns(prepared.get("luns"))
    inodes, source_names = set(), set()
    for expected, actual in zip(luns, actual_luns):
        check_raw_record(actual, expected)
        size = integer(expected.get("bytes"), "full LUN bytes", 1)
        require(integer(expected.get("logical_block_size"), "LUN block size") == 4096 and
                integer(expected.get("logical_block_count"), "LUN block count", 1) * 4096 == size and
                actual.get("size") == size and actual.get("readonly_original") is True, "LUN byte geometry differs")
        source = expected.get("source")
        require(type(source) is dict and source.get("kind") == "block-device" and
                source.get("bytes") == size and source.get("logical_block_size") == 4096,
                "original LUN source identity is required")
        source_path = str(path_value(source.get("path")))
        require(source_path not in source_names, "LUN sources must be distinct")
        source_names.add(source_path)
        for key in ("stat_device", "inode", "rdev"):
            integer(source.get(key), "source " + key)
        path = tracked.file({"path": expected["path"], "sha256": expected["sha256"], "size": size}, raw_lun=True)
        physical = identity(path.stat())[:2]
        require(physical not in inodes, "LUN files must not alias each other")
        inodes.add(physical)
    validate_receipts(binding, ready, tracked, luns)

    inputs = binding["inputs"]
    require(set(inputs) == set(CORE) | {"boot_image"}, "bind all original boot inputs")
    for name in CORE:
        record = inputs[name]
        require(all(prepared[name].get(field) == record.get(field) for field in ("path", "sha256", "size")),
                "prepared " + name + " differs from binding")
        tracked.file(record)
    original = prepared["provenance"]["boot_image"]["source"]
    require(all(original.get(field) == inputs["boot_image"].get(field) for field in ("path", "sha256", "size")),
            "boot image provenance differs from binding")
    tracked.file(inputs["boot_image"])
    machine, globals_ = check_properties(binding, tracked)
    binaries = binding["executables"]
    require(set(binaries) == {"qemu", "qemu_img"}, "bind both QEMU executables")
    for record in binaries.values():
        require(os.access(tracked.file(record), os.X_OK), "bound QEMU file is not executable")

    observation = binding["observation"]
    require(type(observation) is dict and set(observation) == {
        "seconds", "max_log_mib", "snapshot_interval", "stop_on_kernel_failure", "trace_interrupts"},
        "observation settings must be explicit")
    seconds, interval = observation["seconds"], observation["snapshot_interval"]
    require(type(seconds) in (int, float) and math.isfinite(seconds) and 0 < seconds <= 3600 and
            type(interval) in (int, float) and math.isfinite(interval) and 1 <= interval <= 300 and
            seconds / interval <= 240, "invalid observation duration/snapshot interval")
    require(1 <= integer(observation["max_log_mib"], "max_log_mib", 1) <= 1024, "invalid log bound")
    for flag in ("stop_on_kernel_failure", "trace_interrupts"):
        require(type(observation[flag]) is bool, "observation flags must be booleans")
    printk = binding.get("printk")
    require(not observation["stop_on_kernel_failure"] or type(printk) is dict,
            "kernel-failure stopping requires exact printk symbols")
    if printk is not None:
        require(type(printk) is dict and printk.get("kernel_sha256") == inputs["kernel"]["sha256"],
                "printk symbols belong to another kernel")
        tracked.file(printk)
    argv = ["--manifest", binding["prepared_manifest"]["path"], "--qemu", binaries["qemu"]["path"],
            "--qemu-img", binaries["qemu_img"]["path"], "--out", str(out / "runner"), "--machine", machine,
            "--seconds", str(seconds), "--max-log-mib", str(observation["max_log_mib"]),
            "--snapshot-interval", str(interval)]
    for prop in globals_:
        argv += ["--global-property", prop]
    for flag in ("stop_on_kernel_failure", "trace_interrupts"):
        if observation[flag]:
            argv.append("--" + flag.replace("_", "-"))
    if printk is not None:
        argv += ["--legacy-printk-symbols", printk["path"], "--symbols-kernel-sha256", printk["kernel_sha256"]]
    plan = {"schema": "tb371fc-full-lun-boot-plan-v1", "ready_sha256": binding["ready"]["sha256"],
            "prepared_manifest_sha256": binding["prepared_manifest"]["sha256"], "luns": luns,
            "runner_argv": argv, "raw_lun_digests_checked_in_preflight": False,
            "full_lun_digest_verification_delegated_to_runner": True,
            "qualifications": {flag: False for flag in FALSE_FLAGS}}
    if binding["schema"] == MAPPED_SCHEMA:
        plan.update(schema="tb371fc-full-lun-boot-plan-v2", binding_schema=MAPPED_SCHEMA,
                    selection_map_sha256=binding["selection_map"]["sha256"],
                    mapped_generation_completion_sha256=binding["mapped_generation_completion"]["sha256"],
                    frozen_evidence_index_sha256=binding["frozen_evidence_index"]["sha256"])
    return plan


def verify_runner_result(binding, out, returncode, binding_path=None, binding_sha=None):
    report_sha, _, report_raw = read_file(out / "runner/result.json", bounded=True)
    plan_sha, _, plan_raw = read_file(out / "runner/plan.json", bounded=True)
    report, plan = parse_json(report_raw), parse_json(plan_raw)
    require(report.get("schema") == "tb371fc-android-boot-result-v1" and
            report.get("dry_run") is False and type(report.get("executed")) is bool,
            "missing real runner result")
    require(report.get("manifest_sha256") == binding["prepared_manifest"]["sha256"] and
            report.get("qemu_sha256") == binding["executables"]["qemu"]["sha256"] and
            report.get("qemu_img_sha256") == binding["executables"]["qemu_img"]["sha256"] and
            report.get("runner_sha256") == binding["runner_dependencies"]["boot_android.py"],
            "runner used different inputs/executables")
    if binding_path is not None:
        require(report.get("configuration_evidence") == {"path": str(binding_path), "sha256": binding_sha},
                "runner configuration evidence differs")
    if binding.get("printk") is not None:
        require(report.get("printk_symbols_sha256") == binding["printk"]["sha256"],
                "runner used different printk symbols")
    require(plan.get("schema") == "tb371fc-android-boot-plan-v1" and
            plan.get("machine") == binding["machine"] and plan.get("global_properties") == binding["global_properties"] and
            plan.get("bootargs_modified") is False and plan.get("test_init_injected") is False,
            "runner configuration differs from binding")
    for name in CORE:
        require(all(plan["inputs"][name].get(field) == binding["inputs"][name][field]
                    for field in ("path", "sha256", "size")), "runner used different original " + name)
    luns = six_luns(binding["luns"])
    for expected, actual in zip(luns, six_luns(plan["inputs"]["luns"])):
        check_raw_record(actual, expected)
        require(actual.get("sha256_verified") is True, "runner did not verify every raw LUN digest")
    for expected, overlay in zip(luns, six_luns(plan.get("overlays"))):
        require(overlay.get("base") == expected["path"] and
                overlay.get("path") == str(out / "runner" / ("ufs-lun-%d.qcow2" % expected["lun"])) and
                overlay.get("virtual_size") == expected["bytes"] and
                overlay.get("base_sha256_supplied") == expected["sha256"],
                "runner overlays do not bind the six original LUNs")
    provenance = six_luns(report["input_provenance"]["luns"])
    require(all(row.get("expected_sha256_supplied") is True and row.get("sha256_verified") is True
                for row in provenance), "runner lacks verified full-LUN provenance")
    require(type(returncode) is int and returncode in (0, 1, 2), "unknown runner return code")
    passed = report.get("stock_android_boot_passed")
    require(type(passed) is bool and (returncode == 0) == passed, "runner return code/boot verdict disagree")
    with bound_modules(binding["runner_dependencies"]) as modules:
        recomputed = modules["boot_android"].stock_boot_passed(report, plan)
    require(recomputed is passed, "runner boot verdict contradicts its original observation/QMP predicate")
    if passed:
        require(report["executed"] is True and report.get("base_files_opened_read_only") is True and
                report.get("qemu_unchanged") is True and report.get("error") is None and
                report.get("outcome") == "android-boot-complete-observed", "runner success is inconsistent")
        preservation = report["preservation"]
        require(preservation.get("preserved") is True and
                preservation.get("core_input_digests_unchanged") == {key: True for key in CORE},
                "runner did not preserve original inputs")
        for expected, actual in zip(luns, six_luns(preservation["original_luns"])):
            require(actual.get("stat_unchanged") is True and actual.get("base_sha256_supplied") == expected["sha256"],
                    "runner preservation does not bind the six original LUNs")
    return {"executed": report["executed"], "stock_android_boot_passed": passed,
            "runner_outcome": report.get("outcome"), "runner_result_sha256": report_sha,
            "runner_plan_sha256": plan_sha, "runner_returncode": returncode}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ready", required=True)
    parser.add_argument("--binding", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--lock-file", required=True)
    parser.add_argument("--wait-seconds", type=float, default=0)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not math.isfinite(args.wait_seconds) or not 0 <= args.wait_seconds <= 86400:
        parser.error("wait-seconds must be in [0,86400]")
    os.umask(0o077)
    out, lock_fd, tracked = None, None, Inputs()
    result = {"schema": RESULT_SCHEMA, "started_utc": utc(), "dry_run": args.dry_run,
              "runner_invocation_started": False, "executed": False, "execution_state_unknown": False,
              "stock_android_boot_passed": False, "automatic_retry_attempted": False,
              "capture_qualifications": {flag: False for flag in FALSE_FLAGS + ("all_backups_complete",)}}
    exit_code = 1
    try:
        candidate = path_value(args.out)
        require(candidate.parent.is_dir(), "output parent must already exist")
        candidate.mkdir(mode=0o700, exist_ok=False)
        out = candidate
        write_json(out / "attempt.json", {**result, "binding_path": args.binding, "ready_path": args.ready,
                                          "lock_file": args.lock_file})
        lock = path_value(args.lock_file)
        require(lock != out and out not in lock.parents, "lock must be shared outside this unique output directory")
        lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        require(stat.S_ISREG(os.fstat(lock_fd).st_mode), "lock must be a regular file")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding_path, ready_path = path_value(args.binding), path_value(args.ready)
        binding_sha, _, raw = read_file(binding_path, bounded=True)
        binding = tracked.file({"path": str(binding_path), "sha256": binding_sha}, json_file=True)
        result["binding_sha256"] = binding_sha
        require(binding.get("schema") in (SCHEMA, MAPPED_SCHEMA) and binding["ready"]["path"] == str(ready_path),
                "binding schema or ready path differs")
        if binding["schema"] == MAPPED_SCHEMA:
            result.update(schema="tb371fc-full-lun-boot-result-v2", binding_schema=MAPPED_SCHEMA)
        deadline = time.monotonic() + args.wait_seconds
        while not ready_path.exists():
            require(time.monotonic() < deadline, "ready receipt did not arrive within the bounded wait")
            time.sleep(min(1, max(0, deadline - time.monotonic())))
        plan = validate(binding, ready_path, tracked, out)
        plan["binding_sha256"] = binding_sha
        plan["runner_argv"] += ["--configuration-evidence", str(binding_path)]
        write_json(out / "plan.json", plan)
        tracked.recheck()
        if args.dry_run:
            result.update(outcome="dry-run", handoff_verified=True,
                          full_lun_digests_verified=False, bound_inputs_unchanged=True)
            exit_code = 0
        else:
            write_json(out / "execution-intent.json", {"started_utc": utc(), "binding_sha256": binding_sha,
                                                       "runner_argv": plan["runner_argv"], "result_unknown": True})
            result.update(runner_invocation_started=True, executed=None, execution_state_unknown=True)
            returncode = invoke_runner(plan["runner_argv"], binding["runner_dependencies"])
            checked = verify_runner_result(binding, out, returncode, binding_path, binding_sha)
            tracked.recheck()
            result.update(checked, handoff_verified=True, full_lun_digests_verified=True, bound_inputs_unchanged=True,
                          execution_state_unknown=False, outcome=checked["runner_outcome"])
            exit_code = returncode
    except BaseException as error:
        result.update(error=type(error).__name__ + ": " + str(error),
                      outcome="unknown-execution-state" if result["runner_invocation_started"] else "preflight-failed",
                      stock_android_boot_passed=False)
        exit_code = 1
    finally:
        result["finished_utc"] = utc()
        if out is not None:
            try:
                write_json(out / "result.json", result)
            except BaseException as error:
                result.update(outcome="result-publication-failed", stock_android_boot_passed=False,
                              publication_error=type(error).__name__ + ": " + str(error))
                exit_code = 1
        if lock_fd is not None:
            os.close(lock_fd)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
