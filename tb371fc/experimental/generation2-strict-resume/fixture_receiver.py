#!/usr/bin/env python3
"""Only synthetic regular files; not a deployable capture receiver command."""
import argparse
import json
import os
from pathlib import Path
import stat
import subprocess
import sys

from resume_core import exclusive_json, load_pinned_collector, receive_preserving, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", required=True, type=Path)
    parser.add_argument("--collector", required=True, type=Path)
    args = parser.parse_args()
    root = args.fixture_root.absolute()
    require(json.loads((root / "fixture-only.json").read_bytes()) == {"synthetic_regular_files_only": True},
            "synthetic fixture marker missing")
    source = root / "source.bin"
    require(stat.S_ISREG(source.lstat().st_mode), "fixture source is not a regular file")
    plan = json.loads((root / "expected-plan.json").read_bytes())
    require(plan["source"]["path"] == str(source) and
            plan["source"]["kind"] == "regular-file-test-only", "production source forbidden")
    cap = load_pinned_collector(args.collector)
    lock = root / "capture/capture.lock"
    identity = (lock.stat().st_dev, lock.stat().st_ino)
    probe_log = (root / "lock-probes.jsonl").open("x")

    def observe(phase, path, expected):
        run = subprocess.run([sys.executable, str(Path(__file__).with_name("lock_probe.py")),
                              str(path), str(expected[0]), str(expected[1])],
                             text=True, capture_output=True, timeout=5, check=True)
        result = json.loads(run.stdout)
        require(result["pid"] != os.getpid(), "lock probe must be an independent process")
        result["phase"] = phase
        probe_log.write(json.dumps(result) + "\n")
        probe_log.flush()
        os.fsync(probe_log.fileno())
        require(result["acquired"] is False, "critical section was not locked")

    rc, error = 0, None
    try:
        receive_preserving(cap, sys.stdin.buffer, sys.stdout.buffer, root / "capture", root / "preserved",
                           plan, expected_indices=range(6), phase_observer=observe)
    except BaseException as exc:
        rc, error = 2, type(exc).__name__ + ": " + str(exc)
    finally:
        probe_log.close()
    run = subprocess.run([sys.executable, str(Path(__file__).with_name("lock_probe.py")),
                          str(lock), str(identity[0]), str(identity[1])],
                         text=True, capture_output=True, timeout=5, check=True)
    released = json.loads(run.stdout)
    require(released["acquired"] is True, "wrapper left original inode locked")
    exclusive_json(root / "driver-result.json", {"returncode": rc, "error": error,
                   "lock_after_wrapper": released, "synthetic_fixture_only": True})
    if error:
        print(error, file=sys.stderr)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
