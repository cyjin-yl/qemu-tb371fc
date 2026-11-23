#!/usr/bin/env python3
"""Install an exact text package in a new CT stage; compile syntax only."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

STAGE = Path("/work/xbl-completion-v3-stage-20261123-01")
MAX_PACKAGE = 196608
NAMES = {"install.py", "completion-v3.py", "compare-metadata.py",
         "run-job.py", "host-launch.py", "tablet-metadata.json"}
COMPLETION_SHA = "97732923da196da2b8656694b93c8ee047fe1cd2b932501f2fdb21f877f2865e"
TABLET_SHA = "b90cbf0373fcfb08b4b8cb0c54aa2eda302f784922dfc23b23de304cdc04eff4"
ANALYSIS = Path("/work/smem-sbl-analysis-20261123-01")
DEPENDENCIES = {
    "xbl_metadata.py": "7d1e2a9cfaece31e3d5f6dbf4b4ffce84561f913f1b493e7110f930912493ad1",
    "test_xbl_metadata.py": "0160503de3a8a4a6bd0652b16fc2a2276d8e8f252a75b9f7639dce568c621cd0",
}

class InstallError(Exception):
    pass

def require(ok, code):
    if not ok:
        raise InstallError(code)

def sha(data):
    return hashlib.sha256(data).hexdigest()

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, "duplicate_package_json_key")
        out[key] = value
    return out

def reject_constant(_value):
    raise InstallError("non_finite_package_number")

def decode(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=unique,
                      parse_constant=reject_constant)

def read_dependency(name, bound):
    parent = os.open(str(ANALYSIS), os.O_RDONLY | os.O_DIRECTORY |
                     os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                     dir_fd=parent)
        try:
            st = os.fstat(fd)
            require(stat.S_ISREG(st.st_mode) and 0 <= st.st_size <= bound,
                    "dependency_type_or_bound")
            chunks = []
            left = st.st_size + 1
            while left:
                part = os.read(fd, min(left, 65536))
                if not part:
                    break
                chunks.append(part)
                left -= len(part)
            raw = b"".join(chunks)
            end = os.fstat(fd)
            require((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                    == (end.st_dev, end.st_ino, end.st_size,
                        end.st_mtime_ns, end.st_ctime_ns)
                    and len(raw) == st.st_size, "dependency_changed")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)

def write_new(dirfd, name, raw):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            require(count > 0, "short_stage_write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)


def verify_written(dirfd, name, expected):
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=dirfd)
    try:
        st = os.fstat(fd)
        require(stat.S_ISREG(st.st_mode) and stat.S_IMODE(st.st_mode) == 0o600
                and st.st_size == len(expected), "installed_type_mode_or_size")
        chunks = []
        remaining = len(expected) + 1
        while remaining:
            part = os.read(fd, min(remaining, 65536))
            if not part:
                break
            chunks.append(part)
            remaining -= len(part)
        actual = b"".join(chunks)
        end = os.fstat(fd)
        require((st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
                == (end.st_dev, end.st_ino, end.st_size,
                    end.st_mtime_ns, end.st_ctime_ns)
                and actual == expected, "installed_readback_mismatch")
    finally:
        os.close(fd)


def main():
    os.umask(0o077)
    report = {
        "schema": "tb371fc-xbl-stage-installation-v1", "stage": str(STAGE),
        "started_utc": utc(), "syntax_only": True,
        "source_compile_succeeded": False, "completion_executed": False,
        "archive_read": False, "firmware_or_model_executed": False,
        "prior_parser_tests_rerun": False, "files": [],
    }
    stagefd = None
    phase = "read_package"
    try:
        require(len(sys.argv) == 2 and len(sys.argv[1]) == 64
                and all(c in "0123456789abcdef" for c in sys.argv[1]),
                "expected_package_sha_required")
        raw = sys.stdin.buffer.read(MAX_PACKAGE + 1)
        require(0 < len(raw) <= MAX_PACKAGE and sha(raw) == sys.argv[1],
                "package_size_or_sha")
        doc = decode(raw)
        require(type(doc) is dict
                and doc.get("schema") == "tb371fc-xbl-completion-v3-text-package-v1"
                and doc.get("stage") == str(STAGE)
                and doc.get("installation_executes_completion") is False
                and doc.get("separate_root_run_lease_required") is True,
                "package_scope")
        files = doc.get("files")
        require(type(files) is list and len(files) == len(NAMES), "package_file_count")
        contents = {}
        phase = "verify_sources_and_compile_syntax"
        for row in files:
            require(type(row) is dict and row.get("name") in NAMES
                    and row["name"] not in contents
                    and type(row.get("content")) is str, "package_file_entry")
            name = row["name"]
            data = row["content"].encode("utf-8")
            require(type(row.get("bytes")) is int and len(data) == row["bytes"]
                    and sha(data) == row.get("sha256"), "source_size_or_sha")
            if name.endswith(".py"):
                compile(data, str(STAGE / name), "exec", dont_inherit=True, optimize=0)
            else:
                decode(data)
            contents[name] = data
            report["files"].append({"name": name, "bytes": len(data),
                                    "sha256": sha(data),
                                    "syntax_compiled": name.endswith(".py")})
        require(set(contents) == NAMES
                and sha(contents["completion-v3.py"]) == COMPLETION_SHA
                and sha(contents["tablet-metadata.json"]) == TABLET_SHA,
                "frozen_completion_or_reference_changed")
        original_argv = getattr(sys, "orig_argv", [])
        require("-c" in original_argv, "plain_source_c_invocation_required")
        index = original_argv.index("-c")
        require(index + 1 < len(original_argv)
                and original_argv[index + 1].encode("utf-8") == contents["install.py"],
                "executed_installer_source_mismatch")
        phase = "verify_existing_ct_parser"
        dependencies = []
        for name, expected in DEPENDENCIES.items():
            data = read_dependency(name, 65536)
            require(sha(data) == expected, "existing_parser_dependency_sha")
            compile(data, str(ANALYSIS / name), "exec", dont_inherit=True, optimize=0)
            dependencies.append({"name": name, "sha256": sha(data), "bytes": len(data)})
        unit_exit = read_dependency("parser-tests.exit", 32)
        require(unit_exit.strip() == b"0", "existing_parser_unit_gate_failed")
        report.update(source_compile_succeeded=True, dependencies=dependencies,
                      prior_parser_exit_sha256=sha(unit_exit), package_sha256=sha(raw))
        phase = "create_exclusive_stage"
        parent = os.open("/work", os.O_RDONLY | os.O_DIRECTORY |
                         os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            os.mkdir(STAGE.name, mode=0o700, dir_fd=parent)
            stagefd = os.open(STAGE.name, os.O_RDONLY | os.O_DIRECTORY |
                              os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent)
        finally:
            os.close(parent)
        require(stat.S_IMODE(os.fstat(stagefd).st_mode) == 0o700, "stage_mode")
        phase = "write_exact_private_sources"
        write_new(stagefd, "package.json", raw)
        for name, data in contents.items():
            write_new(stagefd, name, data)
        verify_written(stagefd, "package.json", raw)
        for name, data in contents.items():
            verify_written(stagefd, name, data)
        report.update(installed=True, finished_utc=utc(),
                      on_disk_readback_verified=True, all_created_files_retained=True)
        receipt = (json.dumps(report, indent=2, sort_keys=True) + "\n").encode()
        write_new(stagefd, "install-receipt.json", receipt)
        os.fsync(stagefd)
        print(json.dumps({"installed": True, "completion_executed": False,
                          "stage": str(STAGE), "package_sha256": sha(raw),
                          "install_receipt_sha256": sha(receipt)}, sort_keys=True))
        return 0
    except Exception as error:
        report.update(installed=False, phase=phase, finished_utc=utc(),
                      error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, InstallError)
                      else "installation_io_or_parse_error",
                      all_created_files_retained=True)
        if stagefd is not None:
            try:
                write_new(stagefd, "install-failure.json",
                          (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
                os.fsync(stagefd)
            except Exception:
                pass
        print(json.dumps({"installed": False, "phase": phase,
                          "error_type": report["error_type"],
                          "error_code": report["error_code"],
                          "completion_executed": False}, sort_keys=True))
        return 1
    finally:
        if stagefd is not None:
            os.close(stagefd)

if __name__ == "__main__":
    sys.exit(main())
