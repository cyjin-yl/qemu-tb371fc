#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Assemble one complete LUN from verified, read-only range captures.

Repeat --manifest for captures of the SAME source. --source is the recorded
remote block-device path, never a device opened by this program. Every byte in
[0, --source-bytes) must occur exactly once; no GPT, missing bytes or padding is
invented. Inputs remain untouched. An exclusive OUT.partial is retained on
failure. Success publishes OUT and OUT.manifest.json after a full raw readback.
Use a dedicated output directory with no other writers during assembly.
The result is a byte-complete assembly, not an atomic snapshot or factory image.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import errno
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

import acquire_block_ranges as capture

SCHEMA = "tb371fc-whole-lun-assembly-v1"
IO = capture.IO
require = capture.require
# Linux <linux/fs.h>: RENAME_NOREPLACE = (1 << 0). There is deliberately no
# link/unlink or overwriting-rename fallback: some destination stores prohibit
# deleting even our own temporary files.
_renameat2 = getattr(ctypes.CDLL(None, use_errno=True), "renameat2", None)
if _renameat2 is not None:
    _renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    _renameat2.restype = ctypes.c_int


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def open_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        require(stat.S_ISREG(os.fstat(fd).st_mode), "input must be a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def unchanged(path, expected):
    require(identity(path.lstat()) == expected, f"input changed during assembly: {path}")


def read_json(path):
    with open_regular(path) as stream:
        before = identity(os.fstat(stream.fileno()))
        require(before[3] <= capture.JSON_LIMIT, "input JSON exceeds size limit")
        raw = stream.read(capture.JSON_LIMIT + 1)
        require(len(raw) == before[3] and identity(os.fstat(stream.fileno())) == before,
                "input JSON changed while reading")
    unchanged(path, before)
    value = json.loads(raw)
    require(isinstance(value, dict), "input JSON must be an object")
    return value, {"path": path, "identity": before, "sha256": capture.sha(raw)}


def complete_coverage(rows, source_bytes):
    cursor = 0
    for row in sorted(rows, key=lambda r: r["offset"]):
        start = row["offset"]
        require(start >= cursor, "overlapping LUN ranges/chunks")
        require(start == cursor, f"incomplete LUN coverage: gap [{cursor}, {start})")
        cursor += row["length"]
    require(cursor == source_bytes,
            f"incomplete LUN coverage: captured through {cursor}, expected {source_bytes}")


def inspect_inputs(manifests, expected_source, expected_bytes):
    """Validate identities/coverage/receipts before creating any output file."""
    require(isinstance(expected_source, str) and expected_source.startswith("/") and
            "\0" not in expected_source, "an explicit absolute recorded source path is required")
    require(type(expected_bytes) is int and expected_bytes > 0 and expected_bytes % 4096 == 0,
            "expected LUN size must be a positive multiple of 4096")
    require(1 <= len(manifests) <= 128, "one to 128 capture manifests required")
    sources, chunks, ranges, tracked, roots = [], [], [], [], []
    source_identity, seen = None, set()
    for supplied in manifests:
        path = Path(supplied).absolute()
        require(not path.is_symlink(), "manifest must not be a symlink")
        path = path.resolve(strict=True)
        require(path not in seen, "duplicate capture manifest")
        seen.add(path)
        root = path.parent
        value, manifest_file = read_json(path)
        tracked.append(manifest_file)
        require(value.get("schema") == capture.SCHEMA and
                value.get("status") == "verified-range-copies", "capture is not complete/verified")
        require(value.get("snapshot_consistent") is False and
                value.get("pristine_factory_images") is False,
                "capture must retain non-snapshot/non-factory qualification")
        plan = value.get("plan")
        require(isinstance(plan, dict), "capture plan missing")
        expected_rows = capture.validate_plan(plan)
        source = plan["source"]
        require(source.get("path") == expected_source and source["bytes"] == expected_bytes,
                "wrong LUN: source path or byte size differs from explicit expectation")
        require(source.get("kind") in ("block-device", "regular-file-test-only"),
                "source kind missing or unsupported")
        if source_identity is None:
            source_identity = dict(source)
        require(source == source_identity, "source identity/geometry differs across captures")
        saved_plan, plan_file = read_json(root / "plan.json")
        require(saved_plan == plan, "saved plan contradicts capture manifest")
        tracked.append(plan_file)
        records = value.get("chunks")
        require(isinstance(records, list) and len(records) == len(expected_rows),
                "capture chunk count differs from plan")
        for row, record in zip(expected_rows, records):
            require(isinstance(record, dict) and
                    all(type(record.get(k)) is type(v) and record[k] == v for k, v in row.items()),
                    "chunk geometry/order differs from plan")
            require(record.get("source_rechecked") is True, "missing before/after source reread evidence")
            for key in ("source_sha256", "source_before_sha256", "source_after_sha256", "transport_sha256"):
                require(isinstance(record.get(key), str) and
                        re.fullmatch(r"[0-9a-f]{64}", record[key]), "invalid chunk SHA-256")
            require(type(record.get("compressed_bytes")) is int and
                    0 < record["compressed_bytes"] <= row["length"] + row["length"] // 100 + 65536,
                    "compressed chunk size outside bounds")
            directory = root / f"chunk-{row['index']:06d}"
            require(directory.is_dir() and not directory.is_symlink(), "unsafe/missing chunk directory")
            saved, receipt_file = read_json(directory / "receipt.json")
            require(saved == record, "saved chunk receipt contradicts manifest")
            tracked.append(receipt_file)
            chunks.append({"record": record, "path": directory / "data.gz", "manifest": str(path)})
        roots.append(root)
        ranges.extend(plan["ranges"])
        sources.append({"path": str(path), "sha256": manifest_file["sha256"],
                        "bytes": manifest_file["identity"][3], "completed_utc": value.get("completed_utc"),
                        "ranges": plan["ranges"]})
    complete_coverage(ranges, expected_bytes)
    complete_coverage([c["record"] for c in chunks], expected_bytes)
    return {"source": source_identity, "manifests": sources,
            "chunks": sorted(chunks, key=lambda c: c["record"]["offset"]),
            "tracked": tracked, "roots": roots}


def copy_chunk(item, destination, full_digest):
    """Verify first, then copy while rechecking the decoded bytes and identity."""
    path, record = item["path"], item["record"]
    before = identity(path.lstat())
    capture.verify_chunk(path, record)
    unchanged(path, before)
    digest, length = hashlib.sha256(), 0
    require(destination.tell() == record["offset"], "unexpected output offset")
    destination.seek(record["offset"])
    with open_regular(path) as source:
        require(identity(os.fstat(source.fileno())) == before, "chunk replaced before copy")
        with gzip.GzipFile(fileobj=source, mode="rb") as decoded:
            while data := decoded.read(min(IO, record["length"] + 1 - length)):
                length += len(data)
                require(length <= record["length"], "decoded chunk grew after validation")
                destination.write(data)
                digest.update(data)
                full_digest.update(data)
        require(identity(os.fstat(source.fileno())) == before, "chunk changed while copying")
    require(length == record["length"] and digest.hexdigest() == record["source_sha256"],
            "decoded chunk changed after validation")
    unchanged(path, before)
    return {"path": path, "identity": before}


def rename_noreplace(source, target):
    """Same-directory Linux atomic rename; EEXIST preserves both files."""
    require(source.parent == target.parent, "publication must remain in one directory/filesystem")
    if _renameat2 is None:
        raise OSError(errno.ENOSYS, "libc renameat2 is required; no deleting fallback")
    directory = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if _renameat2(directory, os.fsencode(source.name), directory, os.fsencode(target.name), 1):
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    finally:
        os.close(directory)


def publish(files):
    """Publish held descriptors; failure attempts identity-checked renames back.

    Each item is (partial_path, final_path, held_fd, verified_fstat_identity).
    The raw precedes its receipt. A kill between the two renames can leave a raw
    without a receipt; that is not a completed assembly. A conflicting path is
    never overwritten or deleted, including if it prevents rollback.
    The caller must keep other writers out of the output directory: rollback's
    identity check and pathname rename are not one inode-conditional operation.
    """
    published = []
    try:
        for source, target, fd, expected in files:
            require(identity(os.fstat(fd)) == expected, "verified output changed before publication")
            unchanged(source, expected)
        for source, target, fd, expected in files:
            for _, saved_path, saved_fd, saved_identity in published:
                require(identity(os.fstat(saved_fd)) == saved_identity, "published output changed")
                unchanged(saved_path, saved_identity)
            require(identity(os.fstat(fd)) == expected, "verified output changed before rename")
            unchanged(source, expected)
            rename_noreplace(source, target)
            current = identity(os.fstat(fd))
            published.append((source, target, fd, current))
            # rename can change ctime; device, inode, mode, size and mtime must
            # still be those of the descriptor whose bytes were read back.
            require(current[:5] == expected[:5], "verified output changed during rename")
            unchanged(target, current)
        capture.sync_directory(files[0][1].parent)
        for _, target, fd, expected in published:
            require(identity(os.fstat(fd)) == expected, "published output changed before completion")
            unchanged(target, expected)
    except BaseException:
        # Inspect all held files as an interruption can land just after a
        # successful rename and before it was appended to `published`.
        for source, target, fd, _ in reversed(files):
            with contextlib.suppress(OSError):
                if identity(target.lstat())[:2] == identity(os.fstat(fd))[:2]:
                    rename_noreplace(target, source)
        with contextlib.suppress(OSError):
            capture.sync_directory(files[0][1].parent)
        raise


def assemble(manifests, expected_source, expected_bytes, output, *, progress=None):
    require(_renameat2 is not None, "Linux libc renameat2 is required for non-deleting publication")
    inputs = inspect_inputs(manifests, expected_source, expected_bytes)
    supplied = Path(output).absolute()
    require(supplied.name not in ("", ".", ".."), "invalid output name")
    output = supplied.parent.resolve(strict=True) / supplied.name
    require(not any(output == root or root in output.parents for root in inputs["roots"]),
            "output must be outside every original capture directory")
    partial = output.with_name(output.name + ".partial")
    receipt = output.with_name(output.name + ".manifest.json")
    receipt_partial = receipt.with_name(receipt.name + ".partial")
    require(not any(p.exists() or p.is_symlink() for p in (output, partial, receipt, receipt_partial)),
            "exclusive new output required; output/receipt/partial already exists")
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    digest, written = hashlib.sha256(), 0
    with contextlib.ExitStack() as held:
        destination = held.enter_context(os.fdopen(fd, "w+b"))
        for number, item in enumerate(inputs["chunks"], 1):
            inputs["tracked"].append(copy_chunk(item, destination, digest))
            written += item["record"]["length"]
            if progress:
                progress({"event": "chunk-verified-written", "chunk": number,
                          "chunks": len(inputs["chunks"]), "bytes": written, "total_bytes": expected_bytes,
                          "offset": item["record"]["offset"], "length": item["record"]["length"],
                          "source_manifest": item["manifest"], "source_chunk": item["record"]["index"]})
        destination.flush()
        os.fsync(destination.fileno())
        require(written == expected_bytes and os.fstat(destination.fileno()).st_size == expected_bytes,
                "assembled raw length differs from full LUN size")
        raw_identity = identity(os.fstat(destination.fileno()))
        unchanged(partial, raw_identity)
        destination.seek(0)
        readback, total = hashlib.sha256(), 0
        while data := destination.read(IO):
            readback.update(data)
            total += len(data)
        require(total == expected_bytes and readback.digest() == digest.digest(),
                "final raw readback length/SHA-256 mismatch")
        require(identity(os.fstat(destination.fileno())) == raw_identity, "raw changed during readback")
        unchanged(partial, raw_identity)
        for entry in inputs["tracked"]:
            unchanged(entry["path"], entry["identity"])
        result = {"schema": SCHEMA, "status": "verified-whole-lun-assembly", "completed_utc": capture.utc(),
                  "source": inputs["source"],
                  "raw": {"path": str(output), "format": "raw", "bytes": expected_bytes,
                          "sha256": digest.hexdigest()},
                  "source_manifests": inputs["manifests"], "chunks_verified": len(inputs["chunks"]),
                  "whole_source_bytes_captured": True, "coverage_nonoverlapping": True,
                  "raw_readback_verified": True, "snapshot_consistent": False,
                  "source_immutable_claim_verified": False, "pristine_factory": False,
                  "pristine_factory_images": False, "restore_tested": False, "android_boot_tested": False,
                  "live_mutable_ranges": [{"source_manifest": m["path"], **r}
                                          for m in inputs["manifests"] for r in m["ranges"]
                                          if r["classification"] == "live-mutable"],
                  "qualification": "Complete byte coverage assembled from per-chunk copies at separate read times; "
                                   "not an atomic snapshot or proof of pristine factory firmware"}
        receipt_raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
        receipt_fd = os.open(receipt_partial, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        stream = held.enter_context(os.fdopen(receipt_fd, "w+b"))
        stream.write(receipt_raw)
        stream.flush()
        os.fsync(stream.fileno())
        receipt_identity = identity(os.fstat(stream.fileno()))
        stream.seek(0)
        require(stream.read() == receipt_raw and identity(os.fstat(stream.fileno())) == receipt_identity,
                "assembly receipt changed during readback")
        unchanged(receipt_partial, receipt_identity)
        capture.sync_directory(output.parent)
        publish([(partial, output, destination.fileno(), raw_identity),
                 (receipt_partial, receipt, stream.fileno(), receipt_identity)])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, action="append", required=True,
                        help="verified capture manifest.json; repeat for nonoverlapping parts")
    parser.add_argument("--source", required=True, help="exact recorded remote source path (identity only)")
    parser.add_argument("--source-bytes", type=lambda s: int(s, 0), required=True)
    parser.add_argument("--out", type=Path, required=True, help="exclusive new full-LUN raw output")
    args = parser.parse_args(argv)

    def progress(value):
        print(json.dumps(value, sort_keys=True), file=sys.stderr, flush=True)

    try:
        result = assemble(args.manifest, args.source, args.source_bytes, args.out, progress=progress)
    except (ValueError, OSError, EOFError, KeyError, TypeError) as exc:
        progress({"event": "assembly-failed", "error": str(exc)})
        return 1
    print(json.dumps({"complete": True, "raw": result["raw"],
                      "manifest": result["raw"]["path"] + ".manifest.json",
                      "snapshot_consistent": False, "pristine_factory": False}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
