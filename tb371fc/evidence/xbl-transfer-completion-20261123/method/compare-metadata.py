#!/usr/bin/env python3
"""Compare CT static metadata with the already-published tablet fingerprints."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

STAGE = Path("/work/xbl-completion-v3-stage-20261123-01")
BASE = Path("/work/device-assets/xbl-capture-20261123-01")
RESULTS = Path("/work/smem-sbl-analysis-20261123-01/results-v1")
TABLET_SHA = "b90cbf0373fcfb08b4b8cb0c54aa2eda302f784922dfc23b23de304cdc04eff4"
PARSER_SHA = "7d1e2a9cfaece31e3d5f6dbf4b4ffce84561f913f1b493e7110f930912493ad1"
ARCHIVE_SHA = "30a30d8670bd69579d2a250ddcc542b99f3140d062a8b95cb61c09bfd8f1671e"
EXPECTED = {
    "xbl_a": (3670016,
        "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55",
        "255aaf0cb2718ac10e81bd0bb11a0a98ea4e31cfa2bc9addec51b0966e2a4789"),
    "xbl_config_a": (131072,
        "bb16d00d0f7581caa641d2f1c736adf5cc6505403c67250c47e8a893cbda93ec",
        "eb568b572d79d80a2ab3e1a2377ddb9efc6f33211205326fcba68071e07a7d3f"),
}
FACT_KEYS = ("elf", "firmware_volumes", "allowlisted_markers",
             "memory_map_literal_candidates")

class CheckError(Exception):
    pass

def require(ok, code):
    if not ok:
        raise CheckError(code)

def sha(b):
    return hashlib.sha256(b).hexdigest()

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def pairs_unique(pairs):
    out = {}
    for key, value in pairs:
        require(key not in out, "duplicate_json_key")
        out[key] = value
    return out

def reject_constant(_value):
    raise CheckError("non_finite_json_number")

def decode(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs_unique,
                      parse_constant=reject_constant)

def read_file(path, limit):
    parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY |
                     os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        fd = os.open(path.name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                     dir_fd=parent)
        try:
            first = os.fstat(fd)
            require(stat.S_ISREG(first.st_mode), "input_not_regular")
            require(0 <= first.st_size <= limit, "input_size_limit")
            chunks = []
            remaining = first.st_size + 1
            while remaining:
                data = os.read(fd, min(remaining, 65536))
                if not data:
                    break
                chunks.append(data)
                remaining -= len(data)
            raw = b"".join(chunks)
            last = os.fstat(fd)
            fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
            require(all(getattr(first, k) == getattr(last, k) for k in fields)
                    and len(raw) == first.st_size, "input_changed")
            return raw
        finally:
            os.close(fd)
    finally:
        os.close(parent)

def inputs_by_name(doc):
    values = doc.get("inputs")
    require(type(values) is list and len(values) == 2, "input_count")
    out = {}
    for item in values:
        require(type(item) is dict, "input_not_object")
        name = item.get("name")
        require(name in EXPECTED and name not in out, "input_name_or_duplicate")
        out[name] = item
    require(set(out) == set(EXPECTED), "input_set")
    return out

def save_new(report):
    raw = (json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
           + "\n").encode("utf-8")
    fd = os.open(str(STAGE / "canonical-comparison.json"),
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)
    return sha(raw)

def main():
    os.umask(0o077)
    report = {
        "schema": "tb371fc-xbl-cross-environment-metadata-comparison-v1",
        "started_utc": utc(), "passed": False, "comparisons": [],
        "firmware_payload_read_by_this_comparator": False,
        "firmware_or_model_execution": False,
        "smem_handoff_generated": False,
        "scope": "Equality of canonical static metadata only; not firmware behavior.",
    }
    code = 1
    try:
        tablet_raw = read_file(STAGE / "tablet-metadata.json", 12288)
        require(sha(tablet_raw) == TABLET_SHA, "tablet_reference_hash")
        tablet = decode(tablet_raw)
        require(tablet.get("metadata_complete") is True
                and tablet.get("archive_and_members_verified") is True
                and tablet.get("archive_sha256") == ARCHIVE_SHA
                and tablet.get("parser_source_sha256") == PARSER_SHA,
                "tablet_reference_scope")
        summary = decode(read_file(STAGE / "completion.stdout.json", 32768))
        require(summary.get("exit_code") == 0
                and summary.get("transfer_complete") is True
                and summary.get("metadata_state") == "passed"
                and summary.get("overall_complete") is True,
                "completion_not_successful")
        info = summary.get("receipt")
        require(type(info) is dict and type(info.get("path")) is str,
                "completion_receipt_reference")
        receipt_path = Path(info["path"])
        require(receipt_path.parent == BASE
                and re.fullmatch(r"completion-\d{8}T\d{12}Z-[0-9a-f]{8}\.json",
                                 receipt_path.name) is not None,
                "completion_receipt_path")
        completion_raw = read_file(receipt_path, 131072)
        require(sha(completion_raw) == info.get("sha256"),
                "completion_receipt_hash")
        completion = decode(completion_raw)
        require(completion.get("overall_complete") is True
                and completion.get("transfer_complete") is True
                and completion.get("whole_archive_verified") is True
                and completion.get("exact_three_regular_members_verified") is True
                and completion.get("archive_sha256") == ARCHIVE_SHA,
                "completion_receipt_verdict")
        metadata_status = completion.get("metadata")
        require(type(metadata_status) is dict
                and metadata_status.get("state") == "passed"
                and metadata_status.get("result_dir") == str(RESULTS),
                "completion_metadata_status")
        raw = read_file(RESULTS / "metadata.json", 2 * 1024 * 1024)
        require(sha(raw) == metadata_status.get("metadata_sha256"),
                "ct_metadata_receipt_hash")
        ct = decode(raw)
        require(ct.get("schema") == "tb371fc-xbl-metadata-v1"
                and ct.get("script_sha256") == PARSER_SHA
                and ct.get("firmware_payload_exported") is False
                and ct.get("runtime_or_profile_validation") is False,
                "ct_metadata_scope")
        require(read_file(RESULTS / "run.exit", 32).strip() == b"0",
                "ct_metadata_exit")
        ct_inputs = inputs_by_name(ct)
        tablet_inputs = inputs_by_name(tablet)
        for name, (size, capture_sha, canonical_sha) in EXPECTED.items():
            current, reference = ct_inputs[name], tablet_inputs[name]
            require(current.get("size") == reference.get("size") == size
                    and current.get("sha256") == reference.get("sha256") == capture_sha
                    and current.get("expected_capture_match") is True,
                    "input_capture_provenance")
            require(reference.get("canonical_pure_metadata_sha256") == canonical_sha,
                    "tablet_canonical_anchor")
            facts = {key: current[key] for key in FACT_KEYS}
            canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"),
                                   allow_nan=False).encode("utf-8")
            actual = sha(canonical)
            report["comparisons"].append({
                "name": name, "capture_sha256": capture_sha,
                "canonical_keys": list(FACT_KEYS),
                "tablet_canonical_sha256": canonical_sha,
                "ct_canonical_sha256": actual, "equal": actual == canonical_sha,
            })
        report.update(tablet_metadata_sha256=sha(tablet_raw),
                      ct_metadata_sha256=sha(raw),
                      completion_receipt_sha256=sha(completion_raw))
        require(len(report["comparisons"]) == 2
                and all(row["equal"] for row in report["comparisons"]),
                "canonical_metadata_mismatch")
        report["passed"] = True
        code = 0
    except Exception as error:
        report.update(error_type=type(error).__name__,
                      error_code=str(error) if isinstance(error, CheckError)
                      else "comparison_io_or_parse_error")
    report["finished_utc"] = utc()
    report["exit_code"] = code
    try:
        result_sha = save_new(report)
    except Exception as error:
        print(json.dumps({"passed": False, "exit_code": 1,
                          "receipt_error_type": type(error).__name__}))
        return 1
    print(json.dumps({"passed": report["passed"], "exit_code": code,
                      "result_path": str(STAGE / "canonical-comparison.json"),
                      "result_sha256": result_sha}, sort_keys=True))
    return code

if __name__ == "__main__":
    sys.exit(main())
