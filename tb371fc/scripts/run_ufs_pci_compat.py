#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the shared UFS core's PCI QTests, with no skipped or missing cases."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback


EXPECTED = {
    "reg-read", "init", "legacy-read-write", "mcq-read-write", "no-prdt",
    "sync-cache", "long-prdt", "short-prdt", "mcq-cq-wraparound",
    "query-flag", "query-attribute", "query-desciptor", "wb-init",
    "wb-read-write", "hid",
    "legacy-crypto-rejected", "mcq-crypto-rejected",
}


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--qos-test", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu, args.qos_test = args.qemu.resolve(), args.qos_test.resolve()
    for path in (args.qemu, args.qos_test):
        if not path.is_file() or not os.access(path, os.X_OK):
            parser.error(f"missing executable: {path}")
    args.out.mkdir(parents=True, exist_ok=False)
    env = os.environ | {"QTEST_QEMU_BINARY": str(args.qemu)}
    start = time.monotonic()
    report = {
        "schema_version": 1,
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "scope": "Shared UFS core and PCI transport on generic ARM virt; no full TB371FC attestation",
        "private_inputs_used": False,
        "qemu_sha256": sha(args.qemu),
        "qos_test_sha256": sha(args.qos_test),
        "runner_sha256": sha(__file__),
        "expected_tests": sorted(EXPECTED),
        "checks": [],
        "passed": False,
    }
    try:
        command = [str(args.qos_test), "-l"]
        listing = subprocess.run(command, env=env, capture_output=True,
                                 timeout=30, check=False)
        (args.out / "list.stdout").write_bytes(listing.stdout)
        (args.out / "list.stderr").write_bytes(listing.stderr)
        report["list_command"] = command
        report["list_exit_code"] = listing.returncode
        if listing.returncode:
            raise RuntimeError("Cannot list actual QTest graph")
        # GLib versions may emit list entries as TAP comments. Match only
        # the UFS test branch, excluding the generic PCI-device "nop" node.
        paths = re.findall(
            r"^(?:#\s*)?(/aarch64/virt/[^\r\n]*/ufs/ufs-tests/[^\r\n]+)$",
            listing.stdout.decode(), re.MULTILINE)
        names = [path.rsplit("/", 1)[1] for path in paths]
        report["discovered_tests"] = paths
        if len(names) != len(EXPECTED) or set(names) != EXPECTED:
            raise RuntimeError(f"Expected all {len(EXPECTED)} unique UFS PCI tests; found {names!r}")
        for index, path in enumerate(paths):
            case_dir = args.out / f"{index:02d}-{names[index]}"
            case_dir.mkdir()
            command = [str(args.qos_test), "--tap", "-p", path]
            case = {"name": names[index], "path": path,
                    "command": command, "passed": False}
            report["checks"].append(case)
            try:
                result = subprocess.run(command, env=env, capture_output=True,
                                        timeout=180, check=False)
                (case_dir / "stdout.log").write_bytes(result.stdout)
                (case_dir / "stderr.log").write_bytes(result.stderr)
                case["exit_code"] = result.returncode
                text = result.stdout.decode(errors="replace")
                ok = re.findall(r"^ok\s+\d+\b.*$", text, re.MULTILINE)
                failed = re.search(r"^(?:not ok\b|Bail out!)", text, re.MULTILINE)
                skipped = re.search(r"#\s*(?:SKIP|TODO)\b", text, re.IGNORECASE)
                if result.returncode or len(ok) != 1 or failed or skipped:
                    raise RuntimeError("QTest must execute exactly one passing, unskipped case")
                case["passed"] = True
            except subprocess.TimeoutExpired as exc:
                (case_dir / "stdout.log").write_bytes(exc.stdout or b"")
                (case_dir / "stderr.log").write_bytes(exc.stderr or b"")
                case["timed_out"] = True
                case["error"] = str(exc)
            except Exception:
                case["error"] = traceback.format_exc()
            print(("PASS " if case["passed"] else "FAIL ") + path, flush=True)
        report["qemu_unchanged"] = sha(args.qemu) == report["qemu_sha256"]
        report["qos_test_unchanged"] = sha(args.qos_test) == report["qos_test_sha256"]
        report["passed"] = (
            len(report["checks"]) == len(EXPECTED)
            and all(check["passed"] for check in report["checks"])
            and report["qemu_unchanged"] and report["qos_test_unchanged"]
        )
    except Exception:
        report["error"] = traceback.format_exc()
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    hashes = {str(p.relative_to(args.out)): sha(p)
              for p in sorted(args.out.rglob("*")) if p.is_file()}
    (args.out / "sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"],
                      "checks": len(report["checks"]),
                      "elapsed_seconds": report["elapsed_seconds"]}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
