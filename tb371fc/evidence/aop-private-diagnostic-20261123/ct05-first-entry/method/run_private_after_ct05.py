#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DRAFT: CT-only bounded private reference diagnostics, never a boot verdict."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import traceback

# Fill only after independently reading the successful CT attempt-05 receipts.
# This draft refuses to read firmware or start a worker while either is unset.
EXPECTED_CT_QEMU_SHA256 = "c6bed2c465e0b771d6e40d60225b9f89636f6d213bf78b3c174510c24f3f4493"
EXPECTED_CT_CERTIFICATE_SHA256 = "c67e18406ee1c577483a93fa6fad969c7b1b4b6befbf28caecceb791d8734966"
CT = Path("/work/aop-reference-watchpoint-attempt-05/results")
QEMU = CT / "qemu-system-aarch64"
RUNNER = CT / "test_aop_entry_reference.py"
PROOF = CT / "preflight/result.json"
CERTIFICATE = CT / "result.json"
FIRMWARE = Path("/work/device-assets/current-aop_a-20261123.bin")
ROOT = Path("/work/aop-reference-private-after-ct05")
SOURCE_COMMIT = "fe89e5a553fe038babe07abb279a0975d4306715"
RUNNER_SHA = "18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b"
FIRMWARE_SHA = "3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671"
PROFILES = ("elf-physical-entry", "local-alias-vector",
            "local-alias-dtcm32k-reference")
PROFILE_SECONDS = 10
TOTAL_SECONDS = 45
MAX_STEPS = 4096
ACTIVE = None


class GlobalDeadline(Exception):
    pass


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")


def no_symlink(path):
    for item in (path, *path.parents):
        require(not item.is_symlink(), "Symlink path refused: " + str(item))


def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True


def deadline_handler(signum, frame):
    raise GlobalDeadline("Private diagnostic exceeded its total wall deadline")


def spawn(command, log, deadline):
    """Register the sole worker PG before an alarm can interrupt the parent."""
    global ACTIVE
    require(ACTIVE is None, "A prior worker is still registered")
    old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGALRM})
    try:
        require(time.monotonic() < deadline, "Global deadline reached before spawn")

        def restore_child_mask():
            # Single-threaded supervisor; leave no changed signal mask in QEMU.
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)

        env = os.environ.copy()
        env.pop("PYTHONOPTIMIZE", None)
        ACTIVE = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                  start_new_session=True, env=env,
                                  preexec_fn=restore_child_mask)
        return ACTIVE
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)


def stop_registered():
    """Only signal the registered worker session, whose QEMU inherits its PG."""
    global ACTIVE
    process = ACTIVE
    if process is None:
        return {"registered_group": None, "group_absent": True}
    status = {"registered_group": process.pid}
    if group_exists(process.pid):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            status["own_group_sigkill_sent"] = True
        except ProcessLookupError:
            status["group_exited_before_signal"] = True
    try:
        status["exit_code"] = process.wait(timeout=2)
    except subprocess.TimeoutExpired as exc:
        status["reap_failure"] = str(exc)
        status["exit_code"] = process.poll()
    status["group_absent"] = not group_exists(process.pid)
    if status["group_absent"] and process.poll() is not None:
        ACTIVE = None
    return status


def verify_worker(path, profile, expected):
    raw = path.read_bytes()
    data = json.loads(raw)
    require(data.get("diagnostic_completed") is True,
            "Worker did not complete a bounded diagnostic")
    require(data["profile"] == profile and data["reference_cpu"] == "cortex-m3",
            "Reference profile differs")
    for key in ("qemu_sha256", "runner_sha256", "input_sha256", "preflight_sha256"):
        require(data[key] == expected[key], "Worker identity differs: " + key)
    require(data["actual_aop_cpu"] == "unknown" and
            data["full_firmware_start"] is False and
            data["hardware_reset_verified"] is False and
            data["aop_startup_validated"] is False,
            "Worker claimed unsupported hardware or startup knowledge")
    require(data["maximum_steps"] == MAX_STEPS and
            type(data["steps_attempted"]) is int and
            0 <= data["steps_attempted"] <= MAX_STEPS and
            type(data["completed_single_steps"]) is int and
            0 <= data["completed_single_steps"] <= data["steps_attempted"],
            "Worker exceeded its instruction budget")
    require(data["unsupported_bus_counts_before"] ==
            data["unsupported_bus_counts_after"] and
            data["invalid_backing_padding_unchanged"] is True,
            "Unsupported callback or hidden-padding invariant failed")
    require(data["host_hooks_replacing_firmware_functions"] is False and
            data["successful_peripheral_responses"] == 0,
            "Unexpected instruction replacement or peripheral response")
    require(data["stop_reason"] in {
        "unsupported-access-before-bus", "unsupported-instruction-fetch",
        "instruction-crosses-executable-file-boundary",
        "blocking-or-exception-instruction", "architectural-exception-after-step",
        "instruction-budget"}, "Unknown diagnostic outcome")
    return {"result_sha256": hashlib.sha256(raw).hexdigest(),
            "result_bytes": len(raw), "diagnostic_completed": True,
            "stop_reason": data["stop_reason"],
            "steps_attempted": data["steps_attempted"],
            "completed_single_steps": data["completed_single_steps"],
            "internal_exception_before_access_proven": False}


def main():
    require(len(sys.argv) == 1, "This draft accepts no arguments")
    require(__debug__ and sys.flags.optimize == 0, "Optimized Python refused")
    require(isinstance(EXPECTED_CT_QEMU_SHA256, str) and
            len(EXPECTED_CT_QEMU_SHA256) == 64 and
            isinstance(EXPECTED_CT_CERTIFICATE_SHA256, str) and
            len(EXPECTED_CT_CERTIFICATE_SHA256) == 64,
            "Draft blocked until actual successful CT binary/certificate are pinned")
    require(threading.active_count() == 1, "Supervisor must be single-threaded")
    require(signal.SIGALRM not in signal.pthread_sigmask(signal.SIG_BLOCK, []),
            "Global-deadline signal is already blocked")
    os.umask(0o077)
    for path in (ROOT, QEMU, RUNNER, PROOF, CERTIFICATE, FIRMWARE, Path(__file__)):
        no_symlink(path)
    ROOT.mkdir(mode=0o700, exist_ok=False)
    (ROOT / "profiles").mkdir(mode=0o700)
    record = {
        "schema": "aop-reference-private-supervision-v1",
        "diagnostic_completed": False, "supervision_completed": False,
        "source_commit": SOURCE_COMMIT,
        "firmware_read": False, "firmware_execution_requested": False,
        "full_firmware_boot": False, "full_firmware_start": False,
        "full_tablet_equivalence": False, "aop_startup_validated": False,
        "actual_aop_core": "unknown", "actual_reset_mapping": "unknown",
        "hardware_reset_verified": False, "reference_cpu": "cortex-m3",
        "reference_clock_hz": 1000000,
        "internal_exception_before_access_proven": False,
        "ordinary_tcg_access_preflight_passed": False,
        "maximum_steps_per_profile": MAX_STEPS,
        "maximum_seconds_per_profile": PROFILE_SECONDS,
        "global_execution_deadline_seconds": TOTAL_SECONDS,
        "raw_results_private": True, "profiles": [], "started_utc": now()}
    tracked = {"qemu": QEMU, "runner": RUNNER, "proof": PROOF,
               "certificate": CERTIFICATE, "firmware": FIRMWARE,
               "supervisor": Path(__file__)}
    before = {}
    previous_handler = None
    timer_armed = False
    started = time.monotonic()
    try:
        require(digest(CERTIFICATE) == EXPECTED_CT_CERTIFICATE_SHA256,
                "CT certificate identity differs")
        certificate = json.loads(CERTIFICATE.read_text())
        require(certificate["passed"] is True and certificate["all_passed"] is True,
                "CT did not pass")
        require(certificate["source_commit"] == SOURCE_COMMIT and
                certificate["xml_tests_passed"] == 11 and
                certificate["passed_checks"] == 14,
                "CT source or exact gates differ")
        require((CT / "job.exit").read_text().strip() == "0", "CT was not finalized")
        require(certificate["qemu_sha256_before"] ==
                certificate["qemu_sha256_after"] == digest(QEMU) ==
                EXPECTED_CT_QEMU_SHA256, "CT binary identity differs")
        require(digest(RUNNER) == RUNNER_SHA ==
                certificate["runner_sha256_before"] ==
                certificate["runner_sha256_after"], "CT runner identity differs")
        require(digest(PROOF) == certificate["preflight_result_sha256"],
                "CT preflight identity differs")
        proof = json.loads(PROOF.read_text())
        require(proof["passed"] is True and proof["all_passed"] is True and
                proof["expected_checks"] == len(proof["checks"]) == 14 and
                all(item["passed"] is True for item in proof["checks"]),
                "Original fourteen-case gate is incomplete")
        require(proof["qemu_sha256"] == EXPECTED_CT_QEMU_SHA256 and
                proof["runner_sha256"] == RUNNER_SHA and
                proof["firmware_read"] is False and
                proof["firmware_executed"] is False, "CT proof scope differs")
        record["ordinary_tcg_access_preflight_passed"] = True
        require(FIRMWARE.stat().st_size == 524288, "Captured image size differs")
        record["firmware_read"] = True
        require(digest(FIRMWARE) == FIRMWARE_SHA, "Captured image hash differs")
        before = {name: digest(path) for name, path in tracked.items()}
        save(ROOT / "inputs-before.json", before)
        expected = {"qemu_sha256": before["qemu"], "runner_sha256": RUNNER_SHA,
                    "input_sha256": FIRMWARE_SHA, "preflight_sha256": before["proof"]}
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0),
                "An unrelated process timer is already active")
        previous_handler = signal.signal(signal.SIGALRM, deadline_handler)
        deadline = time.monotonic() + TOTAL_SECONDS
        signal.setitimer(signal.ITIMER_REAL, TOTAL_SECONDS)
        timer_armed = True
        for index, profile in enumerate(PROFILES):
            target = ROOT / "profiles" / ("%02d" % index)
            command = [sys.executable, "-E", "-B", str(RUNNER), "_worker",
                       "--qemu", str(QEMU), "--out", str(target),
                       "--firmware", str(FIRMWARE), "--preflight", str(PROOF),
                       "--profile", profile]
            info = {"profile": profile, "command": command, "started_utc": now(),
                    "diagnostic_completed": False, "output": str(target),
                    "internal_exception_before_access_proven": False}
            record["profiles"].append(info)
            save(ROOT / ("%02d-launch.json" % index), info)
            process_started = time.monotonic()
            try:
                with (ROOT / ("%02d.log" % index)).open("xb") as log:
                    process = spawn(command, log, deadline)
                    info["process_group"] = process.pid
                    record["firmware_execution_requested"] = True
                    seconds = min(deadline, process_started + PROFILE_SECONDS) - time.monotonic()
                    require(seconds > 0, "Worker startup consumed the execution budget")
                    try:
                        info["exit_code"] = process.wait(timeout=seconds)
                    except subprocess.TimeoutExpired:
                        info["hard_timeout_seconds"] = PROFILE_SECONDS
                        raise
                    info["group_present_after_wait"] = group_exists(process.pid)
                    require(not info["group_present_after_wait"],
                            "Worker left its session alive")
                    require(info["exit_code"] == 0, "Bounded worker failed")
                info.update(verify_worker(target / "result.json", profile, expected))
            except BaseException:
                info["exception"] = traceback.format_exc()
                raise
            finally:
                info["cleanup"] = stop_registered()
                info["wall_seconds"] = time.monotonic() - process_started
                info["finished_utc"] = now()
                save(ROOT / ("%02d-execution.json" % index), info)
            require(info["cleanup"]["group_absent"] is True,
                    "Owned worker group remains after cleanup")
        record["supervision_completed"] = True
        record["diagnostic_completed"] = (
            len(record["profiles"]) == 3 and
            all(item["diagnostic_completed"] is True for item in record["profiles"]))
    except BaseException:
        record["exception"] = traceback.format_exc()
        record["diagnostic_completed"] = False
    finally:
        if timer_armed:
            signal.setitimer(signal.ITIMER_REAL, 0)
        if previous_handler is not None:
            signal.signal(signal.SIGALRM, previous_handler)
        record["final_cleanup"] = stop_registered()
        try:
            if before:
                after = {name: digest(path) for name, path in tracked.items()}
                save(ROOT / "inputs-after.json", after)
                record["inputs_unchanged"] = before == after
            record["diagnostic_completed"] = (
                record["diagnostic_completed"] is True and
                record.get("inputs_unchanged") is True and
                record["final_cleanup"]["group_absent"] is True and
                "exception" not in record)
        except BaseException:
            record["final_verification_exception"] = traceback.format_exc()
            record["diagnostic_completed"] = False
        record["not_run_profiles"] = [
            profile for profile in PROFILES
            if profile not in {item["profile"] for item in record["profiles"]}]
        record["wall_seconds"] = time.monotonic() - started
        record["finished_utc"] = now()
        save(ROOT / "result.json", record)
        with (ROOT / "job.exit").open("x") as stream:
            stream.write("0\n" if record["diagnostic_completed"] else "1\n")
    # Deliberately omit PCs, register values, disassembly and raw logs here.
    print(json.dumps({"diagnostic_completed": record["diagnostic_completed"],
                      "profiles_attempted": len(record["profiles"]),
                      "full_firmware_boot": False,
                      "internal_exception_before_access_proven": False}))
    return 0 if record["diagnostic_completed"] else 1


if __name__ == "__main__":
    sys.exit(main())
