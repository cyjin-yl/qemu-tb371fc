#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Supervise the public eighteen-case SCS gate; never open private firmware."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

BASE_SHA = "18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b"
SCS_SHA = "641cf68ad2443a77d21308335fabd7844d3c3eb84951e1d04f7458c454e2a60b"
LIMIT = 180

def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def group_exists(pgid):
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    return True

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-inputs", type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    qemu = args.qemu.resolve(strict=True)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    tests = Path(__file__).resolve().parents[1] / "tests"
    runner = tests / "test_aop_scs_reference.py"
    original = tests / "test_aop_entry_reference.py"
    inputs = json.loads(args.source_inputs.read_text())
    started = time.monotonic()
    record = {
        "schema": "tb371fc-aop-scs-public-gate-v1",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "passed": False, "expected_checks": 18, "maximum_seconds": LIMIT,
        "firmware_read": False, "firmware_executed": False,
        "actual_aop_core": "unknown", "actual_aop_irq_count": "unknown",
        "actual_reset_mapping": "unknown", "full_firmware_start": False,
        "full_tablet_equivalence": False,
        "internal_exception_before_access_proven": False,
        "irq_handler_delivery_claimed": False,
        "all_created_files_retained": True,
        "source_commit": inputs.get("source_commit"),
    }
    proc = None
    try:
        record["qemu_sha256_before"] = sha(qemu)
        record["runner_sha256_before"] = sha(runner)
        record["original_runner_sha256_before"] = sha(original)
        record["controller_sha256_before"] = sha(__file__)
        assert record["runner_sha256_before"] == SCS_SHA
        assert record["original_runner_sha256_before"] == BASE_SHA
        assert inputs["firmware_read"] is False and inputs["firmware_executed"] is False
        assert {name: sha(name) for name in inputs["sha256"]} == inputs["sha256"]
        command = [sys.executable, "-E", "-B", str(runner),
                   "--qemu", str(qemu), "--out", str(out / "cases")]
        record["command"] = command
        with (out / "runner.log").open("xb") as log:
            proc = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, start_new_session=True)
            record["process_group"] = proc.pid
            try:
                record["exit_code"] = proc.wait(timeout=LIMIT)
            except subprocess.TimeoutExpired:
                record["timed_out"] = True
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                record["exit_code"] = proc.wait(timeout=5)
        record["group_present_after_wait"] = group_exists(proc.pid)
        assert not record.get("timed_out", False)
        assert record["exit_code"] == 0 and not record["group_present_after_wait"]
        proof_path = out / "cases/result.json"
        proof = json.loads(proof_path.read_text())
        assert proof["schema"] == "aop-scs-reference-synthetic-v1"
        assert proof["passed"] is True and proof["all_passed"] is True
        assert proof["expected_checks"] == len(proof["checks"]) == 18
        assert all(row["passed"] is True for row in proof["checks"])
        for field in ("firmware_read", "firmware_executed", "full_firmware_start",
                      "full_tablet_equivalence", "internal_exception_before_access_proven",
                      "irq_handler_delivery_tested"):
            assert proof[field] is False, field
        assert proof["single_step_keeps_noirq_and_notimer"] is True
        assert proof["qemu_sha256"] == record["qemu_sha256_before"]
        assert proof["runner_sha256"] == SCS_SHA
        assert proof["original_runner_sha256"] == BASE_SHA
        assert proof["sources_and_binary_unchanged"] is True
        assert {name: sha(name) for name in inputs["sha256"]} == inputs["sha256"]
        record["source_inputs_unchanged"] = True
        record["qemu_sha256_after"] = sha(qemu)
        record["runner_sha256_after"] = sha(runner)
        record["original_runner_sha256_after"] = sha(original)
        record["controller_sha256_after"] = sha(__file__)
        for name in ("qemu", "runner", "original_runner", "controller"):
            assert record[name + "_sha256_before"] == record[name + "_sha256_after"]
        record["result_sha256"] = sha(proof_path)
        record["passed_checks"] = 18
        record["passed"] = True
    except BaseException:
        record["failure"] = traceback.format_exc()
        record["passed"] = False
    finally:
        if proc is not None:
            try:
                if group_exists(proc.pid):
                    os.killpg(proc.pid, signal.SIGKILL)
                    record["own_group_cleanup_sigkill_sent"] = True
                proc.wait(timeout=5)
                record["reaped_exit_code"] = proc.returncode
                record["group_present_after_cleanup"] = group_exists(proc.pid)
                if record["group_present_after_cleanup"]:
                    record["passed"] = False
            except BaseException:
                record["cleanup_failure"] = traceback.format_exc()
                record["passed"] = False
        record["finished_utc"] = datetime.now(timezone.utc).isoformat()
        record["wall_seconds"] = time.monotonic() - started
        with (out / "result.json").open("x") as stream:
            json.dump(record, stream, indent=2, sort_keys=True)
            stream.write("\n")
        with (out / "job.exit").open("x") as stream:
            stream.write("0\n" if record["passed"] else "1\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record["passed"] else 1

if __name__ == "__main__":
    sys.exit(main())
