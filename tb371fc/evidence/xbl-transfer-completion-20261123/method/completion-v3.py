#!/usr/bin/env python3
"""Complete this specific private TB371FC XBL transfer without replacing files.
Run only after execution access is restored and the transfer is believed complete.
No network, tablet access, firmware execution, or source-tree modification occurs.
"""
import datetime
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import sys
import tarfile

BASE = Path("/work/device-assets/xbl-capture-20261123-01")
ARCHIVE_NAME = "transfer.part"
ARCHIVE_SIZE = 2849209
ARCHIVE_SHA = "30a30d8670bd69579d2a250ddcc542b99f3140d062a8b95cb61c09bfd8f1671e"
CAPTURE_SHA = "610c04eb3ef5dc5599721a778d6fba8b1804e154f20672b11dd5a60fb6d75f0a"
CAPTURE_SOURCE_SHA = "8d17a0820cd36f00fb3897367a8e2c99c40ce3a944aa6c9c604875d121ed1e24"
EXPECTED = {
    "xbl_a.bin": {
        "size": 3670016,
        "sha256": "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55",
        "name": "xbl_a", "path": "/dev/block/by-name/xbl_a",
        "real": "/dev/block/sdb1", "major": 8, "minor": 17,
    },
    "xbl_config_a.bin": {
        "size": 131072,
        "sha256": "bb16d00d0f7581caa641d2f1c736adf5cc6505403c67250c47e8a893cbda93ec",
        "name": "xbl_config_a", "path": "/dev/block/by-name/xbl_config_a",
        "real": "/dev/block/sdb2", "major": 8, "minor": 18,
    },
}
ANALYSIS = Path("/work/smem-sbl-analysis-20261123-01")
PARSER = ANALYSIS / "xbl_metadata.py"
PARSER_SHA = "7d1e2a9cfaece31e3d5f6dbf4b4ffce84561f913f1b493e7110f930912493ad1"
TEST_SOURCE = ANALYSIS / "test_xbl_metadata.py"
TEST_SHA = "0160503de3a8a4a6bd0652b16fc2a2276d8e8f252a75b9f7639dce568c621cd0"
RESULTS = ANALYSIS / "results-v1"
MAX_TAR_BYTES = 4 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024

class CompletionError(Exception):
    pass

def require(condition, code):
    if not condition:
        raise CompletionError(code)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def stable_key(st):
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)

def read_regular_at(dirfd, name, expected_size=None, maximum=None, private_mode=False):
    fd = os.open(name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=dirfd)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), "non_regular_input")
        if private_mode:
            require(stat.S_IMODE(before.st_mode) == 0o600, "existing_output_not_0600")
        if expected_size is not None:
            require(before.st_size == expected_size, "input_size_mismatch")
        limit = before.st_size if maximum is None else maximum
        require(0 <= before.st_size <= limit, "input_exceeds_bound")
        chunks = []
        remaining = before.st_size + 1
        while remaining:
            data = os.read(fd, min(remaining, 1024 * 1024))
            if not data:
                break
            chunks.append(data)
            remaining -= len(data)
        result = b"".join(chunks)
        after = os.fstat(fd)
        require(stable_key(before) == stable_key(after), "input_changed_during_read")
        require(len(result) == before.st_size, "short_or_growing_input")
        return result, before
    finally:
        os.close(fd)

def read_regular_path(path, maximum):
    parentfd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        return read_regular_at(parentfd, path.name, maximum=maximum)[0]
    finally:
        os.close(parentfd)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate_capture_json_key")
        result[key] = value
    return result

def reject_json_constant(_value):
    raise CompletionError("non_finite_capture_json_number")

def verify_capture_json(data):
    require(sha(data) == CAPTURE_SHA, "capture_json_hash_mismatch")
    document = json.loads(data.decode("utf-8"), object_pairs_hook=unique_object,
                          parse_constant=reject_json_constant)
    require(type(document) is dict, "capture_json_not_object")
    require(document.get("schema") == "tb371fc-private-boot-firmware-capture-v1",
            "capture_schema_mismatch")
    require(document.get("capture_source_sha256") == CAPTURE_SOURCE_SHA,
            "capture_source_hash_mismatch")
    properties = {"ro.product.model": "TB371FC", "ro.boot.slot_suffix": "_a"}
    require(document.get("properties_before") == properties and
            document.get("properties_after") == properties, "capture_model_or_slot_mismatch")
    entries = document.get("entries")
    require(type(entries) is list and len(entries) == 2, "capture_entry_count_mismatch")
    seen = set()
    for entry in entries:
        require(type(entry) is dict, "capture_entry_not_object")
        member = entry.get("member")
        require(type(member) is str and member in EXPECTED and member not in seen,
                "capture_member_not_exact_unique_allowlist")
        seen.add(member)
        expected = EXPECTED[member]
        for key in ("name", "path", "real", "major", "minor", "size", "sha256"):
            require(type(entry.get(key)) is type(expected[key]) and
                    entry.get(key) == expected[key], "capture_entry_field_mismatch")
        require(type(entry.get("captured_bytes")) is int and
                entry["captured_bytes"] == expected["size"], "capture_byte_count_mismatch")
        require(entry.get("verification_read_sha256") == expected["sha256"] and
                entry.get("two_read_passes_equal") is True, "capture_stability_hash_mismatch")
    require(seen == set(EXPECTED), "capture_entries_incomplete")
    scope = document.get("scope")
    require(type(scope) is dict and scope.get("read_passes") == 2 and
            scope.get("device_unique_identifiers_read") is False and
            scope.get("userdata_read") is False and
            scope.get("arbitrary_ram_read") is False and
            scope.get("device_configuration_changed") is False,
            "capture_scope_mismatch")
    return document

def validate_archive(archive):
    require(len(archive) == ARCHIVE_SIZE and sha(archive) == ARCHIVE_SHA,
            "whole_archive_size_or_hash_mismatch")
    with gzip.GzipFile(fileobj=io.BytesIO(archive), mode="rb") as compressed:
        unpacked = compressed.read(MAX_TAR_BYTES + 1)
    require(len(unpacked) <= MAX_TAR_BYTES, "decompressed_tar_exceeds_bound")
    require(len(unpacked) % 512 == 0, "unaligned_tar_length")
    contents = {}
    required = set(EXPECTED) | {"capture.json"}
    physical_cursor = 0
    with tarfile.open(fileobj=io.BytesIO(unpacked), mode="r:") as tf:
        members = tf.getmembers()
        require(len(members) == 3 and not tf.pax_headers, "tar_member_count_or_global_pax")
        for member in members:
            require(type(member.name) is str and member.name in required and
                    member.name not in contents, "tar_member_not_exact_unique_allowlist")
            require(member.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and
                    member.isreg() and not member.linkname and
                    not member.pax_headers and not member.sparse, "tar_non_regular_or_link_member")
            require(member.offset == physical_cursor and
                    member.offset_data == member.offset + 512,
                    "tar_hidden_or_noncontiguous_header")
            require(unpacked[member.offset + 257:member.offset + 265] == b"ustar\x0000",
                    "tar_not_expected_ustar_format")
            require(type(member.size) is int and member.size >= 0, "tar_bad_size")
            if member.name in EXPECTED:
                require(member.size == EXPECTED[member.name]["size"], "tar_payload_size_mismatch")
            else:
                require(0 < member.size <= MAX_CAPTURE_BYTES, "capture_json_size_out_of_bounds")
            physical_cursor = member.offset_data + ((member.size + 511) // 512) * 512
            require(physical_cursor <= len(unpacked), "tar_member_outside_archive")
            require(not any(unpacked[member.offset_data + member.size:physical_cursor]),
                    "nonzero_member_padding")
            stream = tf.extractfile(member)
            require(stream is not None, "regular_member_stream_missing")
            with stream:
                payload = stream.read(member.size + 1)
            require(len(payload) == member.size, "tar_member_short_or_oversized")
            expected_sha = CAPTURE_SHA if member.name == "capture.json" else EXPECTED[member.name]["sha256"]
            require(sha(payload) == expected_sha, "tar_member_hash_mismatch")
            contents[member.name] = payload
    require(set(contents) == required, "tar_members_incomplete")
    require(len(unpacked) - physical_cursor >= 1024 and
            not any(unpacked[physical_cursor:]), "tar_nonzero_trailing_content_or_missing_end")
    capture = verify_capture_json(contents["capture.json"])
    return contents, capture, len(unpacked)

def create_exclusive(dirfd, name, data):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                 0o600, dir_fd=dirfd)
    try:
        os.fchmod(fd, 0o600)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            require(written > 0, "short_output_write")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)

def verify_existing_output(dirfd, name, data):
    actual, st = read_regular_at(dirfd, name, expected_size=len(data),
                                 maximum=len(data), private_mode=True)
    require(sha(actual) == sha(data), "existing_output_content_conflict")
    return {"name": name, "size": len(data), "sha256": sha(actual),
            "mode": "0600", "disposition": "reused_exact_existing_file",
            "inode": st.st_ino}

def publish_member(dirfd, name, data, attempt):
    try:
        os.stat(name, dir_fd=dirfd, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        return verify_existing_output(dirfd, name, data)
    # Keep the private stage after no-overwrite hard-link publication.
    stage = "stage-" + attempt + "-" + name
    create_exclusive(dirfd, stage, data)
    staged, staged_stat = read_regular_at(dirfd, stage, expected_size=len(data),
                                          maximum=len(data), private_mode=True)
    require(sha(staged) == sha(data), "staged_output_hash_mismatch")
    try:
        os.link(stage, name, src_dir_fd=dirfd, dst_dir_fd=dirfd, follow_symlinks=False)
    except FileExistsError:
        # Preserve this stage too if another completed run won.
        existing = verify_existing_output(dirfd, name, data)
        existing.update(stage_path=str(BASE / stage), stage_retained=True)
        return existing
    final = verify_existing_output(dirfd, name, data)
    require(final["inode"] == staged_stat.st_ino, "published_inode_mismatch")
    os.fsync(dirfd)
    final["disposition"] = "created_exact_private_file"
    final.update(stage_path=str(BASE / stage), stage_retained=True)
    return final

def new_receipt(dirfd, prefix, attempt, document):
    name = prefix + "-" + attempt + ".json"
    data = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode()
    create_exclusive(dirfd, name, data)
    os.fsync(dirfd)
    return {"path": str(BASE / name), "sha256": sha(data)}

def run_metadata(dirfd, attempt):
    result = {"requested": True, "state": "not_started", "result_dir": str(RESULTS)}
    try:
        parser_bytes = read_regular_path(PARSER, 64 * 1024)
        test_bytes = read_regular_path(TEST_SOURCE, 64 * 1024)
        require(sha(parser_bytes) == PARSER_SHA and sha(test_bytes) == TEST_SHA,
                "metadata_source_hash_mismatch")
        result["phase"] = "verify_existing_unit_gate"
        result["parser_sha256"] = PARSER_SHA
        result["test_source_sha256"] = TEST_SHA
        unit_exit = read_regular_path(ANALYSIS / "parser-tests.exit", 32)
        require(unit_exit.strip() == b"0", "metadata_parser_unit_gate_not_successful")
        require(not os.path.lexists(str(RESULTS)), "existing_metadata_result_preserved")
        result["phase"] = "run_fresh_metadata"
        log_name = "metadata-run-" + attempt + ".log"
        logfd = os.open(log_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                        os.O_CLOEXEC | os.O_NOFOLLOW, 0o600, dir_fd=dirfd)
        argv = [sys.executable, str(PARSER), "--xbl", str(BASE / "xbl_a.bin"),
                "--xbl-config", str(BASE / "xbl_config_a.bin"),
                "--out", str(RESULTS)]
        result.update(state="running", command=argv, started_utc=utc(),
                      log_path=str(BASE / log_name))
        with os.fdopen(logfd, "wb") as log:
            os.fchmod(log.fileno(), 0o600)
            completed = subprocess.run(argv, cwd=str(ANALYSIS), stdout=log,
                                       stderr=subprocess.STDOUT, timeout=60, check=False)
            log.flush()
            os.fsync(log.fileno())
        result["phase"] = "verify_metadata_result"
        result["exit_code"] = completed.returncode
        require(completed.returncode == 0, "metadata_process_failed")
        require(sha(read_regular_path(PARSER, 64 * 1024)) == PARSER_SHA,
                "metadata_source_changed_during_run")
        require(read_regular_path(RESULTS / "run.exit", 32).strip() == b"0",
                "metadata_internal_exit_failed")
        raw = read_regular_path(RESULTS / "metadata.json", 2 * 1024 * 1024)
        doc = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                         parse_constant=reject_json_constant)
        require(doc.get("script_sha256") == PARSER_SHA and
                doc.get("firmware_payload_exported") is False and
                doc.get("runtime_or_profile_validation") is False,
                "metadata_provenance_or_scope_mismatch")
        inputs = doc.get("inputs")
        require(type(inputs) is list and len(inputs) == 2, "metadata_input_count_mismatch")
        by_name = {x.get("name"): x for x in inputs if type(x) is dict}
        require(set(by_name) == {"xbl_a", "xbl_config_a"}, "metadata_input_names_mismatch")
        for spec in EXPECTED.values():
            item = by_name[spec["name"]]
            require(item.get("sha256") == spec["sha256"] and item.get("size") == spec["size"],
                    "metadata_input_provenance_mismatch")
        result.update(state="passed", metadata_sha256=sha(raw))
    except subprocess.TimeoutExpired:
        result.update(state="failed", error_code="metadata_process_timeout")
    except Exception as error:
        result.update(state="failed", error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, CompletionError) else "metadata_io_or_parse_error")
    result["finished_utc"] = utc()
    return result

def main():
    attempt = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + secrets.token_hex(4)
    report = {"schema": "tb371fc-private-xbl-completion-v1", "attempt": attempt,
              "started_utc": utc(), "archive_path": str(BASE / ARCHIVE_NAME),
              "archive_expected_size": ARCHIVE_SIZE, "archive_expected_sha256": ARCHIVE_SHA,
              "transfer_complete": False, "metadata": {"state": "not_started"},
              "overall_complete": False, "pre_existing_files_preserved": True,
              "transfer_part_preserved": True,
              "firmware_execution_performed": False, "public_payload_output": False}
    dirfd = None
    code = 1
    receipt = None
    try:
        dirfd = os.open(str(BASE), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
        archive, original_stat = read_regular_at(dirfd, ARCHIVE_NAME,
                                                 expected_size=ARCHIVE_SIZE, maximum=ARCHIVE_SIZE)
        members, capture, unpacked_size = validate_archive(archive)
        report.update(archive_sha256=sha(archive), tar_size=unpacked_size,
                      capture_json_sha256=sha(members["capture.json"]),
                      capture_started_utc=capture.get("started_utc"),
                      capture_finished_utc=capture.get("finished_utc"),
                      capture_source_sha256=CAPTURE_SOURCE_SHA,
                      whole_archive_verified=True, exact_three_regular_members_verified=True)
        report["outputs"] = []
        for name in ("xbl_a.bin", "xbl_config_a.bin", "capture.json"):
            published = publish_member(dirfd, name, members[name], attempt)
            published.pop("inode", None)
            published["path"] = str(BASE / name)
            report["outputs"].append(published)
        unchanged = os.stat(ARCHIVE_NAME, dir_fd=dirfd, follow_symlinks=False)
        require(stable_key(original_stat) == stable_key(unchanged),
                "archive_changed_after_validation")
        report["transfer_complete"] = True
        # This separate receipt establishes extraction even if metadata fails.
        transfer_receipt = new_receipt(dirfd, "transfer-completion", attempt, {
            **report, "finished_utc": utc(),
            "scope": "Verified transfer and extraction only; no SMEM initialization or firmware behavior has been validated.",
        })
        report["transfer_receipt"] = transfer_receipt
        report["metadata"] = run_metadata(dirfd, attempt)
        report["overall_complete"] = report["metadata"].get("state") == "passed"
        code = 0 if report["overall_complete"] else 2
    except Exception as error:
        report.update(error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, CompletionError) else "completion_io_or_parse_error",
                      newly_created_incomplete_files_preserved=True)
    finally:
        report["finished_utc"] = utc()
        report["exit_code"] = code
        if dirfd is not None:
            try:
                receipt = new_receipt(dirfd, "completion", attempt, report)
            except Exception as error:
                report["completion_receipt_error_type"] = type(error).__name__
                report["overall_complete"] = False
                report["exit_code"] = 1
                code = 1
            os.close(dirfd)
    # This summary contains paths/hashes/state only, never archive payload.
    print(json.dumps({"exit_code": code, "transfer_complete": report["transfer_complete"],
                      "metadata_state": report["metadata"].get("state"),
                      "overall_complete": report["overall_complete"],
                      "receipt": receipt, "error_code": report.get("error_code"),
                      "metadata_error_code": report["metadata"].get("error_code")}, sort_keys=True))
    return code

if __name__ == "__main__":
    sys.exit(main())
