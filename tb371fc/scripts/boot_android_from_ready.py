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
RESULT_SCHEMA = "tb371fc-full-lun-boot-result-v1"
MAX_JSON = 16 * 1024 * 1024
DEPENDENCIES = ("boot_qmp.py", "legacy_printk.py", "android_boot_inputs.py", "boot_android.py")
CORE = ("kernel", "initrd", "dtb", "bootargs")
FALSE_FLAGS = ("snapshot_consistent", "source_immutable_claim_verified",
               "pristine_factory_images", "restore_tested", "android_boot_tested")


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


def validate_receipts(binding, ready, tracked, luns):
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
    for field, key in (("generation_completion_sha256", "generation_completion"),
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
    require(binding.get("schema") == SCHEMA, "unsupported full-LUN binding schema")
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
    return {"schema": "tb371fc-full-lun-boot-plan-v1", "ready_sha256": binding["ready"]["sha256"],
            "prepared_manifest_sha256": binding["prepared_manifest"]["sha256"], "luns": luns,
            "runner_argv": argv, "raw_lun_digests_checked_in_preflight": False,
            "full_lun_digest_verification_delegated_to_runner": True,
            "qualifications": {flag: False for flag in FALSE_FLAGS}}


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
        require(binding.get("schema") == SCHEMA and binding["ready"]["path"] == str(ready_path),
                "binding schema or ready path differs")
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
