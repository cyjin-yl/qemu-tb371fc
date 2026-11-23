#!/usr/bin/env python3
"""Verify and install an approved transparent source package in a new CT directory."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

ROOT = Path("/work/xbl-dal-candidates-20261123-01")
NAMES = {"install.py", "run-job.py", "host-launch.py", "dal_smem_candidates.py",
         "test_dal_smem_candidates.py", "METHOD.md"}
APPROVED = {
    "dal_smem_candidates.py": "d112e764b5c984d23d45cc4a92311f64ae0f1c5374d50233655475ddc0604d7f",
    "test_dal_smem_candidates.py": "196d5215c4988c5806754482334d13ebe7c239b26aa56e6a03cccec0387f9d3a",
    "METHOD.md": "61b503960e9f5d24bd68adee935700fcdc0445eebef5aecdefe018d1e23b9d24",
}

class InstallError(Exception):
    pass

def require(ok, code):
    if not ok:
        raise InstallError(code)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def write_new(path, data):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        remaining = memoryview(data)
        while remaining:
            count = os.write(fd, remaining)
            require(count > 0, "short_write")
            remaining = remaining[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        state = os.fstat(fd)
        require(stat.S_ISREG(state.st_mode)
                and stat.S_IMODE(state.st_mode) == 0o600
                and state.st_size == len(data), "installed_file_stat")
        actual = bytearray()
        while len(actual) < len(data) + 1:
            block = os.read(fd, min(65536, len(data) + 1 - len(actual)))
            if not block:
                break
            actual.extend(block)
        require(bytes(actual) == data, "installed_file_readback")
    finally:
        os.close(fd)

def json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()

def main():
    os.umask(0o077)
    created = False
    started = utc()
    try:
        require(len(sys.argv) == 2 and re.fullmatch(r"[0-9a-f]{64}", sys.argv[1]),
                "expected_package_sha_required")
        raw = sys.stdin.buffer.read(262145)
        require(len(raw) <= 262144 and sha(raw) == sys.argv[1],
                "package_size_or_hash")
        pack = json.loads(raw)
        require(type(pack) is dict and set(pack) == {"schema", "root", "files"},
                "package_fields")
        require(pack["schema"] == "tb371fc-xbl-dal-install-package-v1"
                and pack["root"] == str(ROOT), "package_scope")
        require(type(pack["files"]) is list and len(pack["files"]) == 6,
                "package_file_count")
        files = {}
        for row in pack["files"]:
            require(type(row) is dict and set(row) == {"name", "bytes", "sha256", "content"},
                    "package_file_fields")
            name = row["name"]
            require(name in NAMES and name not in files and type(row["content"]) is str,
                    "package_file_name")
            data = row["content"].encode("utf-8")
            require(len(data) == row["bytes"] and sha(data) == row["sha256"],
                    "package_file_identity")
            files[name] = (data, row["sha256"])
        require(set(files) == NAMES, "package_name_set")
        for name, expected in APPROVED.items():
            require(files[name][1] == expected, "approved_source_changed")
        original_args = getattr(sys, "orig_argv", ())
        require("-c" in original_args, "installer_source_not_available")
        code_arg = original_args[list(original_args).index("-c") + 1]
        require(code_arg.encode("utf-8") == files["install.py"][0],
                "executed_installer_source_identity")
        for name, (data, _hash) in files.items():
            if name.endswith(".py"):
                compile(data, str(ROOT / name), "exec")
        require(not os.path.islink("/work") and os.path.isdir("/work"),
                "unexpected_work_parent")
        os.mkdir(ROOT, 0o700)
        created = True
        write_new(ROOT / "package.json", raw)
        identities = []
        for name in sorted(files):
            data, hashed = files[name]
            write_new(ROOT / name, data)
            identities.append({"name": name, "bytes": len(data), "sha256": hashed,
                               "syntax_compiled": name.endswith(".py")})
        receipt = {
            "schema": "tb371fc-xbl-dal-installation-v1",
            "root": str(ROOT), "started_utc": started, "finished_utc": utc(),
            "installed": True, "package_sha256": sha(raw), "files": identities,
            "compile_succeeded": True, "compile_only": True,
            "on_disk_readback_verified": True, "all_created_files_retained": True,
            "firmware_input_read": False, "fixtures_executed": False,
            "scanner_executed": False, "firmware_executed": False,
        }
        receipt_raw = json_bytes(receipt)
        write_new(ROOT / "install-receipt.json", receipt_raw)
        print(json.dumps({"installed": True, "root": str(ROOT),
                          "package_sha256": sha(raw),
                          "install_receipt_sha256": sha(receipt_raw),
                          "compile_succeeded": True, "scanner_executed": False},
                         sort_keys=True))
        return 0
    except Exception as error:
        failure = {"installed": False, "root": str(ROOT),
                   "stage_created": created, "all_created_files_retained": True,
                   "error_type": type(error).__name__,
                   "error_code": str(error) if isinstance(error, InstallError)
                   else "installation_io_or_syntax_error",
                   "finished_utc": utc()}
        if created:
            try:
                write_new(ROOT / "install-failure.json", json_bytes(failure))
            except Exception:
                failure["failure_receipt_saved"] = False
        print(json.dumps(failure, sort_keys=True))
        return 1

if __name__ == "__main__":
    sys.exit(main())
