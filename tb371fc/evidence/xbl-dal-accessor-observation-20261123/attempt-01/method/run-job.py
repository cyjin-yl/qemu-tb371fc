#!/usr/bin/env python3
"""Gate 18 synthetic fixtures, then the fixed bounded XBL observer with 8 controls."""
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

ROOT = Path("/work/xbl-dal-accessor-observe-20261123-01")
RESULTS = ROOT / "results"
APPROVED = {
    "observe_xbl_accessor.py": "e0912c1c82c79c0ccab3f6e3313fd3ea1a0996e665d22bc7decf9df07313d8ae",
    "test_observe_xbl_accessor.py": "362040f9690cbff565cf793b48bb2df6165cd49b97f35893bd9b574bc63a5700",
    "METHOD.md": "d5c7298664b3b664fd23f836dbcd6e50da73ad0de7da5467f31b017c4e450fbf"
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
        require(name in {"observe_xbl_accessor.py", "test_observe_xbl_accessor.py",
                         "METHOD.md", "run-job.py", "install.py", "host-launch.py"}
                and name not in seen, "installation_source_name")
        raw = read_regular(ROOT / name, 131072)
        require(len(raw) == row.get("bytes") and sha(raw) == row.get("sha256"),
                "installed_source_identity")
        seen[name] = sha(raw)
    for name, expected in APPROVED.items():
        require(seen.get(name) == expected, "approved_source_identity")
    return seen

def group_present(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False

def execute(label, filename, timeout, phases, extra=()):
    stdout_path = RESULTS / (label + ".stdout")
    stderr_path = RESULTS / (label + ".stderr")
    stdout_fd = os.open(str(stdout_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    stderr_fd = os.open(str(stderr_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    command = [sys.executable, "-I", "-B", "-S", str(ROOT / filename)] + list(extra)
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
                process.wait(timeout=5)
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

def validate_observation(raw):
    require(len(raw) <= 12288, "observation_output_bound")
    result = json.loads(raw)
    require(type(result) is dict and
            result.get("schema") == "tb371fc-xbl-accessor-observation-v1",
            "observation_schema")
    for flag in ("firmware_executed", "native_layout_selected",
                 "native_configuration_abi_selected", "platform_selected",
                 "entry_reachability_proven", "smem_initialized_proven"):
        require(result.get(flag) is False, "observation_unknown_boundary")
    require(result.get("analysis_completed") is True and
            result.get("firmware_input_read") is True and
            result.get("firmware_input_read_attempted") is True and
            result.get("input_unchanged") is True, "observation_not_complete")
    require(result.get("input") == {
        "basename": "xbl_a.bin", "bytes": 3670016,
        "sha256": "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55"},
        "observation_input_identity")
    require(result.get("method_source", {}).get("sha256") ==
            APPROVED["observe_xbl_accessor.py"], "observed_method_identity")
    controls = result.get("controls", {})
    expected = {"plain_pointer_scale8", "sized_pointer_scale16_plus8",
                "size_field_word_read", "word_can_extract_two_halfwords",
                "distinct_cursor_steps", "clobbered_address_not_reused",
                "no_unmodeled_self_instruction", "direct_call_target"}
    checks = controls.get("checks")
    require(controls.get("passed") is True and controls.get("self_authored") is True and
            controls.get("firmware_executed") is False and
            type(checks) is dict and set(checks) == expected and
            all(value is True for value in checks.values()), "tool_controls_contract")
    observations = result.get("observations")
    require(type(observations) is list and 2 <= len(observations) <= 4,
            "observation_window_count")
    for obs, (va, size) in zip(observations[:2],
                               ((0x1481b8e8, 168), (0x1481ba40, 96))):
        require(obs.get("va") == va and obs.get("bytes") == size and
                obs.get("instruction_slots") == size // 4 and
                obs.get("all_slot_addresses_present") is True and
                obs.get("provenance", {}).get("kind") == "fixed_reference_neighborhood",
                "seed_observation_contract")
    follows = result.get("callee_follow_receipts")
    require(type(follows) is list and len(follows) == len(observations) - 2,
            "selected_follow_incomplete")
    targets = []
    for obs, followed in zip(observations[2:], follows):
        require(obs.get("bytes") == 512 and obs.get("instruction_slots") == 128 and
                obs.get("all_slot_addresses_present") is True and
                obs.get("provenance", {}).get("kind") == "observed_qualified_direct_call" and
                followed.get("observed") is True and followed.get("bytes_observed") == 512 and
                followed.get("target_va") == obs.get("va") and
                followed.get("source_pc") == obs["provenance"].get("source_pc"),
                "callee_observation_contract")
        targets.append(obs["va"])
    require(len(set(targets)) == len(targets) and
            result.get("total_bytes_observed") == 264 + 512 * len(targets),
            "native_observation_read_budget")
    for obs in observations:
        for flag in ("function_extent_proven", "entry_reachability_proven",
                     "native_layout_selected", "native_configuration_abi_selected"):
            require(obs.get(flag) is False, "window_unknown_boundary")
        for collection, count in (("facts", "fact_count"), ("edges", "edge_count"),
                                  ("frontiers", "frontier_count"),
                                  ("call_candidates", "call_candidate_count")):
            require(type(obs.get(collection)) is list and type(obs.get(count)) is int and
                    obs[count] >= len(obs[collection]), "window_original_counts")
    truncated = result.get("public_details_truncated")
    require(type(truncated) is bool and type(result.get("details_omitted")) is int and
            result["details_omitted"] >= 0 and
            result.get("complete_witnesses_exported") is (not truncated),
            "observation_truncation_contract")
    tool_rows = result.get("tool_receipts")
    require(type(tool_rows) is list and len(tool_rows) == 7 + len(targets),
            "tool_receipt_count")
    labels = {"objdump-version", "as-version", "assemble-self-control",
              "self-load-control", "self-branch-control", "seed-0", "seed-1"}
    labels.update("callee-%d" % i for i in range(len(targets)))
    require({row.get("label") for row in tool_rows} == labels, "tool_receipt_labels")
    for row in tool_rows:
        require(row.get("process_started") is True and row.get("exit_code") == 0 and
                row.get("timeout") is False and row.get("cleanup_reaped") is True and
                row.get("group_present_after_cleanup") is False and
                type(row.get("process_group")) is int and row["process_group"] > 0,
                "tool_cleanup_or_exit")
    return result

def main():
    os.umask(0o077)
    if sys.argv[1:] != ["--run-approved-accessor-observation"]:
        print(json.dumps({"started": False, "error": "explicit_run_flag_required"}))
        return 64
    report = {
        "schema": "tb371fc-xbl-accessor-observation-job-v1",
        "started_utc": utc(), "overall_complete": False,
        "fixtures_passed": False, "expected_fixture_count": 18, "phases": [],
        "observer_attempted": False, "observer_receipt_available": False,
        "observation_completed": False, "tool_controls_passed": False,
        "expected_tool_control_count": 8,
        "firmware_input_read": False, "firmware_input_read_attempted": False,
        "firmware_executed": False, "smem_handoff_generated": False,
        "native_layout_selected": False, "native_configuration_abi_selected": False,
        "platform_selected": False, "entry_reachability_proven": False,
        "smem_initialized_proven": False,
        "scope": "18 synthetic fixtures; 8 tool controls before bounded fixed-XBL observation.",
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
        report["runtime_flags"] = {
            "optimize": sys.flags.optimize, "isolated": sys.flags.isolated,
            "no_site": sys.flags.no_site,
            "dont_write_bytecode": sys.dont_write_bytecode,
        }
        require(sys.flags.optimize == 0 and sys.flags.isolated == 1
                and sys.flags.no_site == 1 and sys.dont_write_bytecode is True,
                "isolated_nonoptimised_runtime_required")
        before_sources = sources()
        report["source_sha256_before"] = before_sources
        print(json.dumps({"phase": "synthetic_fixtures", "started": True}), flush=True)
        rc, test_out, test_err = execute("fixtures", "test_observe_xbl_accessor.py",
                                        60, report["phases"])
        rendered = (test_out + b"\n" + test_err).decode("utf-8", errors="strict")
        counts = re.findall(r"(?m)^Ran (\d+) tests? in ", rendered)
        endings = re.findall(r"(?m)^OK(?: \([^\n]*\))?$", rendered)
        require(rc == 0 and counts == ["18"] and endings == ["OK"],
                "synthetic_fixtures_failed_or_incomplete")
        report.update(fixtures_passed=True, fixture_test_count=18)
        require(sources() == before_sources, "source_changed_after_fixtures")
        report.update(observer_attempted=True, firmware_input_read=None,
                      firmware_input_read_attempted=None)
        print(json.dumps({"phase": "fixed_observation", "started": True}), flush=True)
        rc, observer_out, observer_err = execute("observer", "observe_xbl_accessor.py",
            60, report["phases"], ("--observe-fixed",))
        actual = read_regular(ROOT / "observations.json", 12288)
        require(actual == observer_out, "observer_stdout_file_mismatch")
        observed = json.loads(actual)
        report.update(observer_receipt_available=True,
                      observation_path=str(ROOT / "observations.json"),
                      observation_sha256=sha(actual),
                      firmware_input_read=observed.get("firmware_input_read"),
                      firmware_input_read_attempted=observed.get("firmware_input_read_attempted"))
        require(rc == 0 and observer_err == b"" and
                read_regular(ROOT / "observer.exit", 16) == b"0\n", "observer_failed")
        observed = validate_observation(actual)
        report.update(observation_completed=True, tool_controls_passed=True,
                      tool_control_count=8, total_bytes_observed=observed["total_bytes_observed"],
                      callee_windows_observed=len(observed["observations"]) - 2,
                      input=observed["input"], input_unchanged=observed["input_unchanged"],
                      input_sha256_before=observed["input"]["sha256"],
                      input_after_hash_verified_equal=observed["input_unchanged"],
                      public_details_truncated=observed["public_details_truncated"],
                      complete_witnesses_exported=observed["complete_witnesses_exported"],
                      details_omitted=observed["details_omitted"],
                      all_observed_tool_groups_reaped=True)
        report["source_sha256_after"] = sources()
        require(report["source_sha256_after"] == before_sources,
                "source_changed_during_job")
        report["overall_complete"] = True
        code = 0
    except Exception as error:
        report.update(error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, GateError)
                      else "job_io_or_process_error")
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
                      "fixture_test_count": report.get("fixture_test_count"),
                      "tool_controls_passed": report["tool_controls_passed"],
                      "observation_completed": report["observation_completed"],
                      "firmware_input_read": report["firmware_input_read"],
                      "result_path": str(ROOT / "job.result.json"),
                      "result_sha256": result_sha,
                      "error_code": report.get("error_code")}, sort_keys=True), flush=True)
    return code

if __name__ == "__main__":
    sys.exit(main())
