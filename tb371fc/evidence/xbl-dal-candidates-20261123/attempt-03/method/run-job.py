#!/usr/bin/env python3
"""Run approved synthetic fixtures, then one pinned read-only static analysis."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time

ROOT = Path("/work/xbl-dal-candidates-20261123-03")
RESULTS = ROOT / "results"
INPUT = Path("/work/device-assets/xbl-capture-20261123-01/xbl_a.bin")
INPUT_SIZE = 3670016
INPUT_SHA = "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55"
APPROVED = {
    "dal_smem_candidates.py": "23fcc56faaaccce7d179b606d2414db79a8c1f2e9f160885129209d2d8f67b68",
    "test_dal_smem_candidates.py": "b3746b16843190088a49d6ad5a448efa68683ce02bce3185fea38849a1f8f027",
    "METHOD.md": "918508a9437c63c3ccee35f06a75e3bbd69a82ec6ae2816a0c41d5eb7a07c101",
}

class GateError(Exception):
    pass

def require(ok, code):
    if not ok:
        raise GateError(code)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read_regular(path, limit):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size <= limit,
                "regular_file_size")
        raw = bytearray()
        while len(raw) < limit + 1:
            part = os.read(fd, min(131072, limit + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns), "file_changed_during_read")
        require(len(raw) == before.st_size, "read_length")
        return bytes(raw)
    finally:
        os.close(fd)

def new_file(path, raw):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            require(written > 0, "short_write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)

def save_json(path, value):
    raw = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    new_file(path, raw)
    return sha(raw)

def sources():
    receipt = json.loads(read_regular(ROOT / "install-receipt.json", 32768))
    require(receipt.get("installed") is True and receipt.get("compile_succeeded") is True,
            "installation_not_valid")
    rows = receipt.get("files")
    require(isinstance(rows, list) and len(rows) == 6, "installation_source_count")
    seen = {}
    for row in rows:
        name = row.get("name")
        require(name in {"dal_smem_candidates.py", "test_dal_smem_candidates.py",
                         "METHOD.md", "run-job.py", "install.py", "host-launch.py"}
                and name not in seen, "installation_source_name")
        raw = read_regular(ROOT / name, 131072)
        require(len(raw) == row.get("bytes") and sha(raw) == row.get("sha256"),
                "installed_source_identity")
        seen[name] = sha(raw)
    for name, expected in APPROVED.items():
        require(seen.get(name) == expected, "approved_source_identity")
    return seen

def input_identity():
    raw = read_regular(INPUT, INPUT_SIZE)
    require(len(raw) == INPUT_SIZE and sha(raw) == INPUT_SHA, "pinned_input_identity")
    return {"path": str(INPUT), "size": len(raw), "sha256": sha(raw)}

def group_present(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False

def execute(label, filename, timeout, phases, arguments=()):
    stdout_path = RESULTS / (label + ".stdout")
    stderr_path = RESULTS / (label + ".stderr")
    stdout_fd = os.open(str(stdout_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    stderr_fd = os.open(str(stderr_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    command = [sys.executable, "-I", "-B", "-S", str(ROOT / filename), *arguments]
    record = {"name": label, "command": command, "timeout_seconds": timeout,
              "started_utc": utc(), "timeout": False, "cleanup_reaped": False}
    phases.append(record)
    started = time.monotonic()
    try:
        process = subprocess.Popen(command, stdout=stdout_fd, stderr=stderr_fd,
                                   cwd=str(ROOT), start_new_session=True)
        record["process_group"] = process.pid
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            record["timeout"] = True
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        record["cleanup_reaped"] = process.poll() is not None
        record["exit_code"] = process.returncode
        record["group_present_after_wait"] = group_present(process.pid)
        if record["group_present_after_wait"]:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        os.fsync(stdout_fd)
        os.fsync(stderr_fd)
    finally:
        os.close(stdout_fd)
        os.close(stderr_fd)
        record["elapsed_seconds"] = round(time.monotonic() - started, 6)
        record["finished_utc"] = utc()
    stdout = read_regular(stdout_path, 131072)
    stderr = read_regular(stderr_path, 131072)
    record.update(stdout_path=str(stdout_path), stdout_sha256=sha(stdout),
                  stderr_path=str(stderr_path), stderr_sha256=sha(stderr))
    require(record.get("cleanup_reaped") is True and not record["timeout"]
            and not record.get("group_present_after_wait"), label + "_process_lifetime")
    return record.get("exit_code"), stdout, stderr

def main():
    os.umask(0o077)
    if sys.argv[1:] != ["--run-approved-static-analysis"]:
        print(json.dumps({"started": False, "error": "explicit_run_flag_required"}))
        return 64
    report = {
        "schema": "tb371fc-xbl-dal-candidate-job-v3",
        "started_utc": utc(), "overall_complete": False,
        "fixtures_passed": False, "static_scan_completed": False, "phases": [],
        "firmware_executed": False, "smem_handoff_generated": False,
        "platform_selected": False, "reachable_from_entry_proven": False,
        "scope": "Synthetic format fixtures and fixed private XBL static metadata only.",
        "all_created_files_retained": True,
    }
    try:
        save_json(ROOT / "job.started.json", report)
    except FileExistsError:
        print(json.dumps({"started": False, "error": "existing_job_preserved"}))
        return 73
    code = 1
    try:
        os.mkdir(RESULTS, 0o700)
        before_sources = sources()
        report["source_sha256_before"] = before_sources
        print(json.dumps({"phase": "synthetic_fixtures", "started": True}), flush=True)
        rc, test_out, test_err = execute("fixtures", "test_dal_smem_candidates.py", 60, report["phases"])
        rendered = (test_out + b"\n" + test_err).decode("utf-8", errors="strict")
        counts = re.findall(r"(?m)^Ran (\d+) tests? in ", rendered)
        endings = re.findall(r"(?m)^OK(?: \([^\n]*\))?$", rendered)
        require(rc == 0 and counts == ["62"] and endings == ["OK"],
                "synthetic_fixtures_failed_or_incomplete")
        report.update(fixtures_passed=True, fixture_test_count=62)
        report["input_before"] = input_identity()
        require(sources() == before_sources, "source_changed_before_scan")
        print(json.dumps({"phase": "pinned_static_scan", "fixtures_passed": True}), flush=True)
        rc, scan_out, scan_err = execute("scan", "dal_smem_candidates.py", 60, report["phases"],
                                         ("--compact-property-candidate",))
        require(rc == 0 and len(scan_out) <= 12288 and not scan_err,
                "static_scan_exit_or_output_contract")
        metadata = json.loads(scan_out)
        require(metadata.get("schema") == "tb371fc-xbl-dal-static-candidates-v3"
                and metadata.get("property_format") == "compact-memory-optimised-candidate"
                and metadata.get("analysis_completed") is True
                and metadata.get("firmware_executed") is False
                and metadata.get("platform_selected") is False
                and metadata.get("reachable_from_entry_proven") is False
                and metadata.get("smem_initialized_proven") is False,
                "static_scan_boundary")
        require(metadata.get("input") == {"basename": "xbl_a.bin", "size": INPUT_SIZE,
                                          "sha256": INPUT_SHA},
                "static_scan_input_receipt")
        require(metadata.get("scope", {}).get("load_indices") == [4, 5, 11, 14],
                "static_scan_region_scope")
        report["input_after"] = input_identity()
        report["source_sha256_after"] = sources()
        require(report["input_after"] == report["input_before"]
                and report["source_sha256_after"] == before_sources,
                "input_or_source_changed")
        report.update(static_scan_completed=True,
                      accepted_candidate_count=metadata.get("accepted_candidate_count"),
                      candidate_processing_truncated=metadata.get("candidate_processing_truncated"),
                      candidate_details_omitted=metadata.get("candidate_details_omitted"),
                      static_metadata_path=str(RESULTS / "scan.stdout"),
                      static_metadata_sha256=sha(scan_out),
                      overall_complete=True)
        code = 0
    except Exception as error:
        report.update(error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, GateError)
                      else "job_io_or_process_error")
    # Preserve post-run identity evidence even when a fixture or scan failed.
    if "source_sha256_before" in report and "source_sha256_after" not in report:
        try:
            report["source_sha256_after"] = sources()
            if report["source_sha256_after"] != report["source_sha256_before"]:
                report.update(overall_complete=False, identity_error="sources_changed_after_failure")
                code = 1
        except Exception as error:
            report.update(overall_complete=False,
                          identity_error="source_identity_unavailable_after_failure",
                          identity_error_type=type(error).__name__)
            code = 1
    if "input_before" in report and "input_after" not in report:
        try:
            report["input_after"] = input_identity()
            if report["input_after"] != report["input_before"]:
                report.update(overall_complete=False, input_identity_error="input_changed_after_failure")
                code = 1
        except Exception as error:
            report.update(overall_complete=False,
                          input_identity_error="input_identity_unavailable_after_failure",
                          input_identity_error_type=type(error).__name__)
            code = 1
    report.update(finished_utc=utc(), exit_code=code)
    try:
        result_sha = save_json(ROOT / "job.result.json", report)
        new_file(ROOT / "job.exit", (str(code) + "\n").encode("ascii"))
    except Exception as error:
        print(json.dumps({"overall_complete": False, "exit_code": 1,
                          "receipt_error_type": type(error).__name__}), flush=True)
        return 1
    print(json.dumps({"overall_complete": report["overall_complete"], "exit_code": code,
                      "fixtures_passed": report["fixtures_passed"],
                      "static_scan_completed": report["static_scan_completed"],
                      "result_path": str(ROOT / "job.result.json"),
                      "result_sha256": result_sha,
                      "error_code": report.get("error_code")}, sort_keys=True), flush=True)
    return code

if __name__ == "__main__":
    sys.exit(main())
