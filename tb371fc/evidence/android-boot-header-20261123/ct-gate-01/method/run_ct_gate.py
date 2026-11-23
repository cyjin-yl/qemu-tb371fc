#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fresh CT-only installation and nine synthetic boot-header tests."""
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.request

ROOT = Path("/work/android-boot-header-20261123-01")
SOURCES = (
    ("inspect_android_boot_header.py", "ac1a6ff6d8f0e5ca9042e943035a4496019a1d56",
     8801, "7eb367183423fb6266b82e31e47d404285d9519aca6fc5ea9214e98c6961a584"),
    ("test_android_boot_header.py", "241627a2f963c557833f9bcb666779ef7e84d2ea",
     12404, "08f2c3ad4b07956f33f7068256a832e514044b5b9b527a8afa105d72f2abfeb0"),
)


def require(ok, code):
    if not ok:
        raise RuntimeError(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def write_new(path, raw):
    with path.open("xb") as stream:
        stream.write(raw)


def save(path, value):
    write_new(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())


def group_present(pgid):
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False


def fetch_source(name, git_blob, size, digest):
    url = "https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/" + git_blob
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "tb371fc-fixed-public-source-reader"})
    with urllib.request.urlopen(req, timeout=20) as response:
        require(response.status == 200, "public_blob_http_status")
        encoded = response.read(65537)
    require(len(encoded) <= 65536, "public_blob_response_limit")
    item = json.loads(encoded)
    require(item.get("sha") == git_blob and item.get("encoding") == "base64"
            and item.get("size") == size, "public_blob_metadata")
    raw = base64.b64decode("".join(item["content"].split()), validate=True)
    require(len(raw) == size and sha(raw) == digest, "source_byte_identity")
    actual = hashlib.sha1(b"blob " + str(size).encode() + b"\0" + raw).hexdigest()
    require(actual == git_blob, "source_git_identity")
    compile(raw, str(ROOT / name), "exec")
    return raw


def main():
    require(len(sys.argv) == 1, "unexpected_arguments")
    require(__debug__ and sys.flags.optimize == 0, "optimized_python")
    os.umask(0o077)
    for part in (ROOT, *ROOT.parents):
        require(not part.is_symlink(), "symlink_output_path")
    ROOT.mkdir(mode=0o700, exist_ok=False)
    start = time.monotonic()
    before_controller = sha(Path(__file__).read_bytes())
    record = {
        "schema": "tb371fc-android-boot-header-ct-gate-v1",
        "started_utc": utc(), "passed": False, "expected_tests": 9,
        "firmware_read": False, "firmware_executed": False,
        "android_boot_passed": False,
        "source_preparation_and_tests_in_ct": True,
        "all_created_files_retained": True,
        "controller_sha256_before": before_controller,
        "sources": [], "process": {},
    }
    process = None
    try:
        for name, blob, size, digest in SOURCES:
            raw = fetch_source(name, blob, size, digest)
            write_new(ROOT / name, raw)
            require((ROOT / name).read_bytes() == raw, "source_install_readback")
            record["sources"].append({
                "name": name, "git_blob": blob, "bytes": size,
                "sha256": digest, "compiled": True, "installed_mode": "0600"})
        save(ROOT / "installation.json", {
            "installed": True, "compile_succeeded": True,
            "firmware_read": False, "tests_executed": False,
            "sources": record["sources"], "finished_utc": utc()})
        command = [sys.executable, "-I", "-B", "-S",
                   str(ROOT / "test_android_boot_header.py")]
        info = record["process"]
        info.update(command=command, maximum_seconds=60, started_utc=utc())
        with (ROOT / "fixtures.stdout").open("xb") as output, \
                (ROOT / "fixtures.stderr").open("xb") as error:
            process = subprocess.Popen(command, stdout=output, stderr=error,
                                       start_new_session=True)
            info["process_group"] = process.pid
            try:
                info["exit_code"] = process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                info["timed_out"] = True
                raise
            finally:
                info["group_present_after_wait"] = group_present(process.pid)
                if info["group_present_after_wait"]:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                        info["own_group_sigkill_sent"] = True
                    except ProcessLookupError:
                        info["group_exited_before_signal"] = True
                info["reaped_exit_code"] = process.wait(timeout=3)
                info["group_present_after_cleanup"] = group_present(process.pid)
                info["finished_utc"] = utc()
        stdout = (ROOT / "fixtures.stdout").read_bytes()
        stderr = (ROOT / "fixtures.stderr").read_bytes()
        text = (stdout + b"\n" + stderr).decode("utf-8")
        require(info.get("timed_out") is not True and info["exit_code"] == 0
                and info["group_present_after_wait"] is False
                and info["group_present_after_cleanup"] is False,
                "synthetic_process_failure")
        require(re.findall(r"(?m)^Ran (\d+) tests? in ", text) == ["9"]
                and re.findall(r"(?m)^OK$", text) == ["OK"],
                "nine_test_success_receipt_missing")
        record["actual_tests_passed"] = 9
        record["passed"] = True
    except BaseException as error:
        record["exception_type"] = type(error).__name__
        if type(error) is RuntimeError:
            record["error_code"] = str(error)
        record["passed"] = False
    finally:
        after = {}
        for row in record["sources"]:
            raw = (ROOT / row["name"]).read_bytes()
            after[row["name"]] = sha(raw)
        record["source_sha256_after"] = after
        record["sources_unchanged"] = all(
            after[row["name"]] == row["sha256"] for row in record["sources"])
        record["controller_sha256_after"] = sha(Path(__file__).read_bytes())
        record["controller_unchanged"] = (
            before_controller == record["controller_sha256_after"])
        record["passed"] = bool(record["passed"] and record["sources_unchanged"]
                                and record["controller_unchanged"])
        record["original_outputs"] = []
        for name in ("fixtures.stdout", "fixtures.stderr"):
            path = ROOT / name
            if path.is_file():
                raw = path.read_bytes()
                record["original_outputs"].append(
                    {"name": name, "bytes": len(raw), "sha256": sha(raw)})
        record["finished_utc"] = utc()
        record["wall_seconds"] = time.monotonic() - start
        save(ROOT / "result.json", record)
        write_new(ROOT / "job.exit", b"0\n" if record["passed"] else b"1\n")
    print(json.dumps({"passed": record["passed"],
                      "actual_tests_passed": record.get("actual_tests_passed", 0),
                      "result_sha256": sha((ROOT / "result.json").read_bytes()),
                      "firmware_read": False}))
    return 0 if record["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
