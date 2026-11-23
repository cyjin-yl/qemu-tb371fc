#!/usr/bin/env python3
"""One-shot CT orchestration: frozen extraction, then metadata equality."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

STAGE = Path("/work/xbl-completion-v3-stage-20261123-01")
RESULTS = Path("/work/smem-sbl-analysis-20261123-01/results-v1")
FROZEN_COMPLETION_SHA = "97732923da196da2b8656694b93c8ee047fe1cd2b932501f2fdb21f877f2865e"
TABLET_SHA = "b90cbf0373fcfb08b4b8cb0c54aa2eda302f784922dfc23b23de304cdc04eff4"
NAMES = {"install.py", "completion-v3.py", "compare-metadata.py",
         "run-job.py", "host-launch.py", "tablet-metadata.json"}

class JobError(Exception):
    pass

def require(ok, code):
    if not ok:
        raise JobError(code)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(b):
    return hashlib.sha256(b).hexdigest()

def unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, "duplicate_json_key")
        out[key] = value
    return out

def no_constant(_value):
    raise JobError("non_finite_json_number")

def read_stage(name, limit=262144):
    require("/" not in name and name not in ("", ".", ".."), "bad_stage_name")
    parent = os.open(str(STAGE), os.O_RDONLY | os.O_DIRECTORY |
                     os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                     dir_fd=parent)
        try:
            st = os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600,
                    "stage_file_type_or_mode")
            require(0 <= st.st_size <= limit, "stage_file_size")
            data = bytearray()
            while len(data) <= st.st_size:
                part = os.read(fd, min(65536, st.st_size + 1 - len(data)))
                if not part:
                    break
                data.extend(part)
            after = os.fstat(fd)
            require((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                    == (after.st_dev, after.st_ino, after.st_size,
                        after.st_mtime_ns, after.st_ctime_ns)
                    and len(data) == st.st_size, "stage_file_changed")
            return bytes(data)
        finally:
            os.close(fd)
    finally:
        os.close(parent)

def read_json(name):
    return json.loads(read_stage(name).decode("utf-8"),
                      object_pairs_hook=unique, parse_constant=no_constant)

def new_file(name, raw):
    require("/" not in name and name not in ("", ".", ".."), "bad_output_name")
    fd = os.open(str(STAGE / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)

def save_json(name, value):
    new_file(name, (json.dumps(value, sort_keys=True, indent=2, allow_nan=False)
                   + "\n").encode("utf-8"))

def group_exists(pid):
    try:
        os.killpg(pid, 0)
    except ProcessLookupError:
        return False
    return True

def execute(name, script, seconds, phases):
    output_name = name + ".stdout.json"
    error_name = name + ".stderr.log"
    argv = [sys.executable, "-I", "-B", "-S", str(STAGE / script)]
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    phase = {"name": name, "command": argv, "started_utc": utc(),
             "timeout_seconds": seconds, "timeout": False,
             "process_group_cleanup": False}
    phases.append(phase)
    started = time.monotonic()
    outfd = os.open(str(STAGE / output_name), os.O_WRONLY | os.O_CREAT |
                    os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        errfd = os.open(str(STAGE / error_name), os.O_WRONLY | os.O_CREAT |
                        os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except Exception:
        os.close(outfd)
        raise
    process = None
    with os.fdopen(outfd, "wb") as out, os.fdopen(errfd, "wb") as err:
        os.fchmod(out.fileno(), 0o600)
        os.fchmod(err.fileno(), 0o600)
        try:
            process = subprocess.Popen(argv, cwd=str(STAGE), env=env,
                                       stdout=out, stderr=err,
                                       start_new_session=True)
            phase["process_group"] = process.pid
            try:
                process.wait(timeout=seconds)
            except subprocess.TimeoutExpired:
                phase["timeout"] = True
            phase["group_present_after_wait"] = group_exists(process.pid)
        finally:
            if process is not None:
                if group_exists(process.pid):
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    else:
                        phase["process_group_cleanup"] = True
                try:
                    process.wait(timeout=10)
                    phase["cleanup_reaped"] = True
                except subprocess.TimeoutExpired:
                    phase["cleanup_reaped"] = False
                phase["exit_code"] = process.returncode
            out.flush()
            err.flush()
            os.fsync(out.fileno())
            os.fsync(err.fileno())
            phase["finished_utc"] = utc()
            phase["elapsed_seconds"] = round(time.monotonic() - started, 6)
    for key, filename in (("stdout", output_name), ("stderr", error_name)):
        raw = read_stage(filename)
        phase[key + "_path"] = str(STAGE / filename)
        phase[key + "_sha256"] = sha(raw)
    require(phase.get("cleanup_reaped") is True, name + "_cleanup_not_reaped")
    require(not phase["timeout"], name + "_timeout")
    require(not phase.get("group_present_after_wait"), name + "_lingering_child")
    return phase.get("exit_code")

def verify_sources(installation):
    require(installation.get("schema") == "tb371fc-xbl-stage-installation-v1"
            and installation.get("stage") == str(STAGE)
            and installation.get("source_compile_succeeded") is True
            and installation.get("syntax_only") is True
            and installation.get("completion_executed") is False
            and installation.get("on_disk_readback_verified") is True,
            "installation_receipt")
    rows = installation.get("files")
    require(type(rows) is list and len(rows) == len(NAMES), "installation_file_count")
    values = {}
    for row in rows:
        require(type(row) is dict and row.get("name") in NAMES
                and row["name"] not in values, "installation_file_names")
        raw = read_stage(row["name"])
        require(len(raw) == row.get("bytes") and sha(raw) == row.get("sha256"),
                "installed_source_changed")
        values[row["name"]] = sha(raw)
    require(set(values) == NAMES
            and values["completion-v3.py"] == FROZEN_COMPLETION_SHA
            and values["tablet-metadata.json"] == TABLET_SHA,
            "frozen_input_identity")
    return values

def main():
    os.umask(0o077)
    if sys.argv[1:] != ["--run-after-confirmed-transfer"]:
        print(json.dumps({"started": False, "error": "explicit_run_flag_required"}))
        return 64
    report = {"schema": "tb371fc-xbl-completion-job-v1", "started_utc": utc(),
              "overall_complete": False, "phases": [],
              "firmware_or_model_execution": False,
              "smem_handoff_generated": False,
              "scope": "Private transfer/extraction and static metadata equality only."}
    try:
        save_json("job.started.json", report)
    except FileExistsError:
        print(json.dumps({"started": False, "error": "existing_job_marker_preserved"}))
        return 73
    code = 1
    try:
        installation = read_json("install-receipt.json")
        source_hashes = verify_sources(installation)
        report["installed_source_sha256"] = source_hashes
        require(not os.path.lexists(str(RESULTS)), "existing_results_v1_preserved")
        print(json.dumps({"started": True, "stage": str(STAGE),
                          "phase": "frozen_completion"}), flush=True)
        rc = execute("completion", "completion-v3.py", 150, report["phases"])
        summary = read_json("completion.stdout.json")
        report["completion_summary"] = summary
        require(rc == 0 and summary.get("exit_code") == 0
                and summary.get("transfer_complete") is True
                and summary.get("overall_complete") is True
                and summary.get("metadata_state") == "passed",
                "frozen_completion_failed")
        print(json.dumps({"phase": "canonical_metadata_comparison"}), flush=True)
        rc = execute("comparison", "compare-metadata.py", 20, report["phases"])
        comparison = read_json("canonical-comparison.json")
        report["comparison_sha256"] = sha(read_stage("canonical-comparison.json"))
        report["canonical_comparison"] = comparison
        require(rc == 0 and comparison.get("passed") is True
                and len(comparison.get("comparisons", [])) == 2
                and all(row.get("equal") is True for row in comparison["comparisons"]),
                "canonical_comparison_failed")
        require(verify_sources(installation) == source_hashes,
                "installed_source_changed_after_execution")
        report["overall_complete"] = True
        code = 0
    except Exception as error:
        report.update(error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, JobError)
                      else "job_io_or_process_error")
    report.update(finished_utc=utc(), exit_code=code,
                  all_created_files_retained=True)
    try:
        save_json("job.result.json", report)
        new_file("job.exit", (str(code) + "\n").encode("ascii"))
    except Exception as error:
        print(json.dumps({"overall_complete": False, "exit_code": 1,
                          "receipt_error_type": type(error).__name__}), flush=True)
        return 1
    print(json.dumps({"overall_complete": report["overall_complete"],
                      "exit_code": code,
                      "result_path": str(STAGE / "job.result.json"),
                      "error_code": report.get("error_code")}, sort_keys=True),
          flush=True)
    return code

if __name__ == "__main__":
    sys.exit(main())
