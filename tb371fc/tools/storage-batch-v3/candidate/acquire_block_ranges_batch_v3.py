#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, no-delete continuation of the pinned read-only range collector.

Self-contained sender and receiver. A mandatory versioned hello precedes the
original v1 plan; each invocation adds an explicitly agreed one or four chunks. A clean pause has its
own receipt and never creates a completion manifest. Every old committed chunk
is rehashed/decompressed at the receiver and reread from the source on resume.
All host commits use atomic renameat2(RENAME_NOREPLACE); partial bytes are moved
whole into retention, never deleted. The source is opened O_RDONLY. Existing
before/stream/after SHA-256 evidence and the v1 final manifest are retained.
This bounds new work, not elapsed time. Inspect a timed-out attempt before any
retry. No source snapshot, factory authenticity or restored Android is claimed.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
import struct
import subprocess
import sys
import zlib

SCHEMA = "tb371fc-readonly-range-stream-v1"
IO = 1024 * 1024
JSON_LIMIT = 2 * IO
BLKGETSIZE64, BLKSSZGET = 0x80081272, 0x1268
PAUSE_PROTOCOL = "tb371fc-readonly-range-stream-batch-v3"
PAUSE_SCHEMA = "tb371fc-readonly-range-paused-v3"
BASELINE_COLLECTOR_SHA256 = "eb1258cb2604cfd99540b2320b28bde0a14284d940258b4f95bff3980c0595be"
# Explicitly bounded candidates. Existing v2 is kept frozen; API defaults stay one.
ALLOWED_NEW_CHUNKS = (1, 4)
PRODUCTION_CHUNK_BYTES = 64 * IO


def require(value, message):
    if not value:
        raise ValueError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def write_all(stream, data):
    """Support blocking unbuffered pipes whose writes can return short counts."""
    view = memoryview(data)
    while view:
        count = stream.write(view)
        require(type(count) is int and 0 < count <= len(view), "short/blocked protocol write")
        view = view[count:]


def send_json(stream, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    require(len(raw) <= JSON_LIMIT, "protocol JSON exceeds limit")
    write_all(stream, raw)
    stream.flush()


def unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def reject_json_constant(value):
    raise ValueError("non-finite JSON constant is forbidden")


def strict_json(data):
    return json.loads(data, object_pairs_hook=unique_json_object, parse_constant=reject_json_constant)


def get_json(stream):
    raw = stream.readline(JSON_LIMIT + 1)
    require(raw.endswith(b"\n") and len(raw) <= JSON_LIMIT, "truncated/oversized protocol JSON")
    value = strict_json(raw)
    require(isinstance(value, dict), "protocol JSON must be an object")
    return value


def read_exact(stream, size):
    blocks = bytearray()
    while len(blocks) < size:
        data = stream.read(size - len(blocks))
        require(data, "truncated transfer frame")
        blocks.extend(data)
    return bytes(blocks)


def read_range(fd, offset, size):
    while size:
        data = os.pread(fd, min(IO, size), offset)
        require(data, "unexpected source EOF")
        offset += len(data)
        size -= len(data)
        yield data


def hash_range(fd, offset, size):
    digest = hashlib.sha256()
    for data in read_range(fd, offset, size):
        digest.update(data)
    return digest.hexdigest()


def geometry(fd, allow_regular=False):
    info = os.fstat(fd)
    if stat.S_ISBLK(info.st_mode):
        size = struct.unpack("=Q", fcntl.ioctl(fd, BLKGETSIZE64, b"\0" * 8))[0]
        sector = struct.unpack("=I", fcntl.ioctl(fd, BLKSSZGET, b"\0" * 4))[0]
        kind = "block-device"
    else:
        require(allow_regular and stat.S_ISREG(info.st_mode), "source must be an explicit block device")
        size, sector, kind = info.st_size, 4096, "regular-file-test-only"
    return {"bytes": size, "logical_block_size": sector, "kind": kind,
            "stat_device": info.st_dev, "inode": info.st_ino, "rdev": info.st_rdev}


def parse_range(text):
    label, offset, length, classification = text.split(":")
    return {"label": label, "offset": int(offset, 0), "length": int(length, 0),
            "classification": classification}


def ssh_transport(prefix, receiver):
    for value in (prefix, receiver):
        require(isinstance(value, list) and value and
                all(isinstance(a, str) and a and "\0" not in a for a in value),
                "SSH prefix and receiver command must be explicit argv arrays")
    return prefix + [shlex.join(receiver)]


def validate_ranges(ranges, source_bytes, sector):
    require(ranges and len(ranges) <= 128, "one to 128 explicit ranges required")
    labels, occupied = set(), []
    for row in ranges:
        label = row.get("label")
        require(isinstance(label, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", label), "invalid range label")
        require(label not in labels, "duplicate range label")
        labels.add(label)
        start, size = row.get("offset"), row.get("length")
        require(type(start) is int and type(size) is int and start >= 0 and size > 0,
                "range offset/length must be nonnegative/positive integers")
        require(start % sector == 0 and size % sector == 0 and start + size <= source_bytes,
                "range is unaligned or outside the recorded source")
        require(row.get("classification") in ("immutable", "live-mutable"), "explicit source classification required")
        if re.search(r"(^|[-_])(metadata|userdata)($|[-_])", label.lower()):
            require(row["classification"] == "live-mutable", "metadata/userdata must be marked live-mutable")
        occupied.append((start, start + size))
    occupied.sort()
    require(all(a[1] <= b[0] for a, b in zip(occupied, occupied[1:])), "overlapping capture ranges")


def gpt_ranges(fd, source_bytes, sector=4096):
    """Locate both actual GPT copies using bounded reads and CRC verification."""
    require(sector == 4096 and source_bytes % sector == 0, "GPT requires recorded 4096-byte source geometry")
    blocks = source_bytes // sector
    require(blocks >= 6, "source too small for GPT")
    copies = []
    for lba in (1, blocks - 1):
        header = b"".join(read_range(fd, lba * sector, sector))
        require(header[:8] == b"EFI PART", "missing actual GPT signature; no GPT is synthesized")
        revision, size, crc, reserved = struct.unpack_from("<4I", header, 8)
        require(revision == 0x10000 and 92 <= size <= sector and reserved == 0, "unsupported GPT header")
        check = bytearray(header[:size])
        check[16:20] = b"\0" * 4
        require(zlib.crc32(check) == crc, "GPT header CRC mismatch")
        current, other, first, last = struct.unpack_from("<4Q", header, 24)
        entries_lba, count, entry_size, table_crc = struct.unpack_from("<QIII", header, 72)
        table_bytes = count * entry_size
        table_span = (table_bytes + sector - 1) // sector * sector
        require(current == lba and other == (blocks - 1 if lba == 1 else 1), "GPT reciprocal header locations disagree")
        require(2 <= first <= last < blocks - 1, "invalid GPT usable range")
        require(count > 0 and entry_size >= 128 and entry_size % 128 == 0 and
                0 < table_bytes <= 16 * IO, "unsupported/unbounded GPT entry array")
        start, end = entries_lba * sector, entries_lba * sector + table_span
        require(2 * sector <= start < end <= source_bytes, "GPT table out of bounds")
        require((end <= first * sector) if lba == 1 else
                (start > last * sector and end <= lba * sector), "GPT table overlaps usable data or its header")
        table = b"".join(read_range(fd, start, table_span))
        require(zlib.crc32(table[:table_bytes]) == table_crc, "GPT entry-array CRC mismatch")
        copies.append({"header": header, "table": table, "table_offset": start,
                       "array": table[:table_bytes], "count": count, "entry_size": entry_size,
                       "first": first, "last": last, "guid": header[56:72].hex()})
    p, b = copies
    require(all(p[key] == b[key] for key in ("array", "count", "entry_size", "first", "last", "guid")),
            "primary and backup GPT copies disagree")
    partitions = []
    for index in range(p["count"]):
        entry = p["array"][index * p["entry_size"]:(index + 1) * p["entry_size"]]
        if not any(entry[:16]):
            continue
        start, last = struct.unpack_from("<QQ", entry, 32)
        require(p["first"] <= start <= last <= p["last"], "partition outside GPT usable region")
        name = entry[56:128].decode("utf-16-le", errors="strict").split("\0", 1)[0]
        partitions.append({"index": index + 1, "name": name, "offset": start * sector,
                           "length": (last - start + 1) * sector})
    ordered = sorted(partitions, key=lambda x: x["offset"])
    require(all(a["offset"] + a["length"] <= b["offset"] for a, b in zip(ordered, ordered[1:])),
            "overlapping GPT partitions")
    mbr = b"".join(read_range(fd, 0, sector))
    raw_ranges = [("gpt-mbr", 0, mbr), ("gpt-primary-header", sector, p["header"]),
                  ("gpt-primary-table", p["table_offset"], p["table"]),
                  ("gpt-backup-table", b["table_offset"], b["table"]),
                  ("gpt-backup-header", (blocks - 1) * sector, b["header"])]
    ranges = [{"label": label, "offset": offset, "length": len(raw),
               "classification": "immutable", "expected_sha256": sha(raw)}
              for label, offset, raw in raw_ranges]
    return ranges, {"both_headers_and_arrays_crc_verified": True, "copies_match": True,
                    "disk_guid": p["guid"], "partitions": partitions,
                    "source_snapshot_consistent": False}


def chunks_for(plan):
    result = []
    for row in plan["ranges"]:
        if "expected_sha256" in row:
            require(row["length"] <= plan["chunk_bytes"] and
                    isinstance(row["expected_sha256"], str) and
                    re.fullmatch(r"[0-9a-f]{64}", row["expected_sha256"]),
                    "an expected-digest range must fit one chunk and have a valid SHA-256")
        for relative in range(0, row["length"], plan["chunk_bytes"]):
            chunk = {"index": len(result), "label": row["label"],
                     "offset": row["offset"] + relative,
                     "length": min(plan["chunk_bytes"], row["length"] - relative),
                     "classification": row["classification"]}
            if "expected_sha256" in row:
                chunk["expected_sha256"] = row["expected_sha256"]
            result.append(chunk)
    require(len(result) <= 16384, "too many capture chunks")
    return result


def validate_plan(plan):
    require(plan.get("schema") == SCHEMA, "wrong acquisition schema")
    source = plan.get("source", {})
    require(type(source.get("bytes")) is int and source["bytes"] > 0 and
            source.get("logical_block_size") == 4096, "invalid source geometry")
    require(type(plan.get("chunk_bytes")) is int and 4096 <= plan["chunk_bytes"] <= 256 * IO and
            plan["chunk_bytes"] % 4096 == 0, "invalid chunk size")
    validate_ranges(plan.get("ranges"), source["bytes"], 4096)
    require(plan.get("source_open_mode") == "O_RDONLY" and plan.get("snapshot_consistent") is False,
            "acquisition cannot claim a source snapshot")
    return chunks_for(plan)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def same_json(left, right):
    """Canonical JSON comparison also distinguishes booleans and integers."""
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(right, sort_keys=True, separators=(",", ":"))


def check_budget(value):
    require(type(value) is int and value in ALLOWED_NEW_CHUNKS,
            "this protocol requires explicit max_new_chunks of 1 or 4")


def hello_message(max_new_chunks):
    check_budget(max_new_chunks)
    return {"protocol": PAUSE_PROTOCOL, "message": "hello",
            "max_new_chunks": max_new_chunks, "final_manifest_schema": SCHEMA}


def hello_ack(max_new_chunks):
    return {"protocol": PAUSE_PROTOCOL, "message": "hello-ack",
            "max_new_chunks": max_new_chunks, "final_manifest_schema": SCHEMA,
            "no_delete": True, "atomic_commit": "renameat2-RENAME_NOREPLACE"}


def require_no_symlink_components(path):
    path = Path(path).absolute()
    for component in [*reversed(path.parents), path]:
        if component.exists() or component.is_symlink():
            require(not component.is_symlink(), "symlink path component is forbidden")
    return path


def require_private_directory(path):
    require_no_symlink_components(path)
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700,
            "receiver directory must be private and owned by this user")


def rename_noreplace(source, destination):
    """Linux atomic rename, no overwrite and no link/unlink fallback."""
    source, destination = Path(source), Path(destination)
    require_no_symlink_components(source.parent)
    require_no_symlink_components(destination.parent)
    import ctypes  # Receiver-only: pure in-memory senders never import ctypes.
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    require(function is not None, "renameat2 is required; no unsafe fallback")
    function.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
                         ctypes.c_char_p, ctypes.c_uint)
    function.restype = ctypes.c_int
    result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))
    sync_directory(destination.parent)
    if source.parent != destination.parent:
        sync_directory(source.parent)


def file_identity(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), "evidence must be a regular file")
        digest, size = hashlib.sha256(), 0
        while data := os.read(fd, IO):
            digest.update(data)
            size += len(data)
        after = os.fstat(fd)
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
                size == before.st_size, "file changed during full read")
        return {"bytes": size, "sha256": digest.hexdigest(),
                "mode": stat.S_IMODE(before.st_mode)}
    finally:
        os.close(fd)


def inventory_entry(path):
    require(not path.is_symlink(), "cannot preserve a symlink as capture data")
    if path.is_file():
        return [{"path": path.name, **file_identity(path)}]
    require(path.is_dir(), "unsupported retained entry type")
    result = []
    for child in sorted(path.rglob("*")):
        require(not child.is_symlink(), "retained directory contains a symlink")
        if child.is_file():
            result.append({"path": str(child.relative_to(path)), **file_identity(child)})
        else:
            require(child.is_dir(), "retained directory contains a nonregular entry")
    return result


def private_child_directory(parent, name):
    path = parent / name
    if not path.exists():
        path.mkdir(mode=0o700, exist_ok=False)
        sync_directory(parent)
    require_private_directory(path)
    return path


def preserve_entry(path, out, reason):
    """Move complete uncommitted entries; preserve every byte and old name in a receipt."""
    original = str(path.relative_to(out))
    before = inventory_entry(path)
    retained = private_child_directory(out, "retained-partials")
    sequence = 0
    while True:
        sequence += 1
        name = f"retained-{sequence:06d}-{path.name}"
        destination = retained / name
        receipt = retained / (name + ".retention.json")
        if not destination.exists() and not destination.is_symlink() and not receipt.exists() and not receipt.is_symlink():
            break
    kind = "directory" if path.is_dir() else "file"
    rename_noreplace(path, destination)
    after = inventory_entry(destination)
    # File inventory names change when renamed; byte identities and modes do not.
    require([{k: v for k, v in row.items() if k != "path"} for row in before] ==
            [{k: v for k, v in row.items() if k != "path"} for row in after],
            "retained bytes changed across rename")
    write_json_exclusive(receipt, {"schema": "tb371fc-retained-capture-partial-v1",
        "original_relative_path": original, "retained_relative_path": str(destination.relative_to(out)),
        "kind": kind, "reason": reason, "retained_utc": utc(), "files": before,
        "original_bytes_preserved": True, "deleted_files": 0})
    return destination


def validate_utc(value, description):
    require(isinstance(value, str), description + " must be a string")
    parsed = datetime.datetime.fromisoformat(value)
    require(parsed.utcoffset() == datetime.timedelta(0), description + " must be UTC")
    return parsed


def validate_record(record, row=None):
    require(isinstance(record, dict), "chunk receipt must be an object")
    required = {"index", "label", "offset", "length", "classification", "source_sha256",
                "source_before_sha256", "source_after_sha256", "compressed_bytes",
                "transport_sha256", "source_rechecked", "source_read_started_utc",
                "source_read_finished_utc"}
    if "expected_sha256" in record:
        required.add("expected_sha256")
    require(set(record) == required, "chunk receipt fields disagree")
    require(type(record["index"]) is int and record["index"] >= 0 and
            type(record["offset"]) is int and record["offset"] >= 0 and
            type(record["length"]) is int and record["length"] > 0,
            "invalid chunk receipt geometry")
    require(isinstance(record["label"], str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", record["label"])
            and record["classification"] in ("immutable", "live-mutable"), "invalid chunk receipt label/classification")
    require(type(record["compressed_bytes"]) is int and 0 < record["compressed_bytes"] <=
            record["length"] + record["length"] // 100 + 65536, "invalid compressed chunk bound")
    for field in ("source_sha256", "source_before_sha256", "source_after_sha256", "transport_sha256"):
        require(isinstance(record[field], str) and re.fullmatch(r"[0-9a-f]{64}", record[field]), "invalid receipt digest")
    require(record["source_rechecked"] is True, "source reread evidence required")
    validate_utc(record["source_read_started_utc"], "chunk start time")
    validate_utc(record["source_read_finished_utc"], "chunk finish time")
    if row is not None:
        require(all(key in record and type(record[key]) is type(value) and record[key] == value
                    for key, value in row.items()), "chunk receipt geometry mismatch")
        require(("expected_sha256" in record) == ("expected_sha256" in row), "unexpected expected-digest field")


def completion_result(plan, rows, records, resumed, completed_utc=None):
    return {"schema": SCHEMA, "status": "verified-range-copies", "completed_utc": completed_utc or utc(),
            "plan": plan, "chunks": [records[i] for i in sorted(records)],
            "resume_source_revalidated_chunks": resumed, "snapshot_consistent": False,
            "source_immutable_claim_verified": False, "pristine_factory_images": False,
            "restore_tested": False, "android_boot_tested": False,
            "live_mutable_ranges": [r["label"] for r in plan["ranges"] if r["classification"] == "live-mutable"],
            "whole_source_bytes_captured": sum(r["length"] for r in plan["ranges"]) == plan["source"]["bytes"],
            "qualification": "Per-chunk current-byte copies; not an atomic snapshot or factory authenticity proof"}


def verify_completion(previous, plan, rows, records):
    require(len(records) == len(rows), "completed manifest has missing chunks")
    expected = completion_result(plan, rows, records, [])
    require(isinstance(previous, dict) and set(previous) == set(expected),
            "existing completion manifest fields disagree")
    validate_utc(previous["completed_utc"], "existing completion time")
    indices = previous["resume_source_revalidated_chunks"]
    require(isinstance(indices, list) and all(type(i) is int and 0 <= i < len(rows) for i in indices)
            and indices == sorted(set(indices)), "existing completion manifest has invalid resume indices")
    expected["completed_utc"] = previous["completed_utc"]
    expected["resume_source_revalidated_chunks"] = indices
    require(same_json(previous, expected), "existing completion manifest disagrees")


def pause_result(out, plan, rows, records, resumed, new_chunks, max_new_chunks):
    check_budget(max_new_chunks)
    require(new_chunks == max_new_chunks and len(records) < len(rows), "invalid pause boundary")
    require(not (out / "manifest.json").exists(), "cannot pause an existing completed capture")
    pause_dir = private_child_directory(out, "pause-receipts")
    sequence = 1
    while (pause_dir / f"pause-{sequence:06d}.json").exists() or (pause_dir / f"pause-{sequence:06d}.json.partial").exists():
        sequence += 1
    path = pause_dir / f"pause-{sequence:06d}.json"
    value = {"schema": PAUSE_SCHEMA, "status": "paused-not-complete", "paused": True,
             "complete": False, "paused_utc": utc(), "protocol": PAUSE_PROTOCOL,
             "plan_sha256": file_identity(out / "plan.json")["sha256"],
             "max_new_chunks": max_new_chunks, "new_chunks": new_chunks,
             "verified_chunks": len(records), "total_chunks": len(rows), "next_index": len(records),
             "committed_receipts": [{"index": i, **file_identity(out / f"chunk-{i:06d}" / "receipt.json")} for i in sorted(records)],
             "resume_source_revalidated_chunks": resumed,
             "resume_source_revalidations": [{"index": i, "source_reread_sha256": records[i]["source_sha256"]} for i in resumed],
             "snapshot_consistent": False, "final_manifest_created": False}
    write_json_exclusive(path, value)
    return {"paused": True, "complete": False, "chunks": len(records), "total_chunks": len(rows),
            "next_index": len(records), "new_chunks": new_chunks, "max_new_chunks": max_new_chunks,
            "pause_receipt": str(path), "snapshot_consistent": False}


def write_json_exclusive(path, value):
    partial = path.with_name(path.name + ".partial")
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    rename_noreplace(partial, path)


def load_json_file(path):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_size <= JSON_LIMIT, "invalid saved JSON file")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(JSON_LIMIT + 1)
        require(len(data) == info.st_size and len(data) <= JSON_LIMIT, "saved JSON changed or exceeds limit")
        return strict_json(data)
    finally:
        os.close(fd)


def verify_chunk(path, record):
    validate_record(record)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_size == record["compressed_bytes"],
                "missing/unsafe stored chunk or compressed size changed")
        with os.fdopen(fd, "rb", closefd=False) as source:
            digest = hashlib.sha256()
            for data in iter(lambda: source.read(IO), b""):
                digest.update(data)
            require(digest.hexdigest() == record["transport_sha256"], "stored transport SHA-256 mismatch")
            source.seek(0)
            digest, total = hashlib.sha256(), 0
            with gzip.GzipFile(fileobj=source, mode="rb") as stream:
                while data := stream.read(min(IO, record["length"] + 1 - total)):
                    total += len(data)
                    require(total <= record["length"], "decoded chunk exceeds expected length")
                    digest.update(data)
        after = os.fstat(fd)
        require((before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), "stored chunk changed during verification")
        require(total == record["length"] and digest.hexdigest() == record["source_sha256"],
                "stored decoded length/SHA-256 mismatch")
        require(record["source_sha256"] == record["source_before_sha256"] == record["source_after_sha256"],
                "source observations disagree")
        if "expected_sha256" in record:
            require(record["source_sha256"] == record["expected_sha256"], "stored source bytes contradict expected range digest")
    finally:
        os.close(fd)


class FramedWriter:
    def __init__(self, stream):
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def write(self, data):
        for start in range(0, len(data), IO):
            part = data[start:start + IO]
            write_all(self.stream, struct.pack(">I", len(part)))
            write_all(self.stream, part)
            self.digest.update(part)
            self.size += len(part)
        return len(data)

    def flush(self):
        self.stream.flush()


def send_chunk(fd, row, output):
    started = utc()
    before = hash_range(fd, row["offset"], row["length"])
    if "expected_sha256" in row:
        require(before == row["expected_sha256"], "source changed from expected range digest")
    send_json(output, {"begin": row["index"], "source_before_sha256": before})
    writer, raw = FramedWriter(output), hashlib.sha256()
    with gzip.GzipFile(fileobj=writer, filename="", mode="wb", compresslevel=1, mtime=0) as zipped:
        for data in read_range(fd, row["offset"], row["length"]):
            raw.update(data)
            zipped.write(data)
    write_all(output, struct.pack(">I", 0))
    output.flush()
    after = hash_range(fd, row["offset"], row["length"])
    require(before == raw.hexdigest() == after, "source changed across before/stream/after reads")
    record = {**row, "source_sha256": raw.hexdigest(), "source_before_sha256": before,
              "source_after_sha256": after, "compressed_bytes": writer.size,
              "transport_sha256": writer.digest.hexdigest(), "source_rechecked": True,
              "source_read_started_utc": started, "source_read_finished_utc": utc()}
    send_json(output, record)
    return record


def receive_chunk(row, begin, incoming, directory):
    partial, final = directory / "data.gz.partial", directory / "data.gz"
    digest, size = hashlib.sha256(), 0
    limit = row["length"] + row["length"] // 100 + 65536
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        while length := struct.unpack(">I", read_exact(incoming, 4))[0]:
            require(length <= IO and size + length <= limit, "compressed transfer exceeds frame/chunk bound")
            data = read_exact(incoming, length)
            stream.write(data)
            digest.update(data)
            size += length
        stream.flush()
        os.fsync(stream.fileno())
    record = get_json(incoming)
    validate_record(record, row)
    require(record["compressed_bytes"] == size and record["transport_sha256"] == digest.hexdigest(),
            "received compressed stream SHA-256/length mismatch")
    require(record["source_before_sha256"] == begin.get("source_before_sha256"), "missing source reread evidence")
    verify_chunk(partial, record)
    rename_noreplace(partial, final)
    write_json_exclusive(directory / "receipt.json", record)
    return record


def receive(incoming, outgoing, output_dir, resume=False, *, max_new_chunks=1):
    """One bounded invocation. Pause returns a distinct result and creates no manifest."""
    check_budget(max_new_chunks)
    # This precedes even output-path resolution. An old receiver rejects this
    # hello as a non-plan before its destructive resume implementation runs.
    require(same_json(get_json(incoming), hello_message(max_new_chunks)), "pause protocol hello required")
    send_json(outgoing, hello_ack(max_new_chunks))
    plan = get_json(incoming)
    rows = validate_plan(plan)
    if plan["source"].get("kind") == "block-device":
        require(plan["chunk_bytes"] == PRODUCTION_CHUNK_BYTES,
                "production batch requires 64 MiB chunks")
    out = require_no_symlink_components(Path(output_dir).absolute())
    if resume:
        require(out.is_dir(), "resume needs an existing capture directory")
    else:
        out.mkdir(mode=0o700, parents=False, exist_ok=False)
    require_private_directory(out)
    lock_fd = os.open(out / "capture.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        require(stat.S_ISREG(os.fstat(lock_fd).st_mode), "capture lock must be regular")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        allowed_root = {"capture.lock", "plan.json", "plan.json.partial", "manifest.json", "manifest.json.partial", "retained-partials", "pause-receipts"}
        row_directories = {f"chunk-{r['index']:06d}" for r in rows}
        for entry in out.iterdir():
            require(entry.name in allowed_root | row_directories and not entry.is_symlink(), "unexpected capture directory entry")
        for name in ("retained-partials", "pause-receipts"):
            if (out / name).exists():
                require_private_directory(out / name)
        plan_path = out / "plan.json"
        pending = []
        if plan_path.exists():
            require(resume and same_json(load_json_file(plan_path), plan), "resume source/ranges/geometry changed")
        else:
            require(not resume or all(p.name in ("capture.lock", "plan.json.partial", "retained-partials") for p in out.iterdir()),
                    "missing committed plan with existing capture files")
        for name in ("plan.json.partial", "manifest.json.partial"):
            if (out / name).exists():
                require(resume and (out / name).is_file(), "unexpected partial output")
                pending.append((out / name, "uncommitted top-level JSON preserved before retry"))
        records = {}
        for row in rows:
            directory = out / f"chunk-{row['index']:06d}"
            if not directory.exists():
                continue
            require(resume, "existing chunk without resume")
            require_private_directory(directory)
            for entry in directory.iterdir():
                require(entry.name in ("data.gz.partial", "data.gz", "receipt.json.partial", "receipt.json") and
                        entry.is_file() and not entry.is_symlink(), "unexpected partial chunk contents")
            if (directory / "receipt.json").exists():
                record = load_json_file(directory / "receipt.json")
                validate_record(record, row)
                verify_chunk(directory / "data.gz", record)
                records[row["index"]] = record
                for name in ("data.gz.partial", "receipt.json.partial"):
                    if (directory / name).exists():
                        pending.append((directory / name, "residual partial beside a fully verified committed chunk"))
            else:
                pending.append((directory, "complete uncommitted chunk directory preserved before retry"))
        require(sorted(records) == list(range(len(records))), "completed chunks must form a contiguous prefix")
        manifest_path = out / "manifest.json"
        if manifest_path.exists():
            verify_completion(load_json_file(manifest_path), plan, rows, records)
        # No capture metadata is changed before existing plan, complete chunks
        # and any existing final manifest have all passed their validations.
        for path, reason in pending:
            preserve_entry(path, out, reason)
        if not plan_path.exists():
            write_json_exclusive(plan_path, plan)
        send_json(outgoing, {"ready": True, "protocol": PAUSE_PROTOCOL, "max_new_chunks": max_new_chunks,
                             "total_chunks": len(rows), "completed": [records[i] for i in sorted(records)]})
        resumed, new_chunks = [], 0
        initial_completed = len(records)
        for row in rows:
            command = get_json(incoming)
            index = row["index"]
            if command.get("pause") is True:
                require(same_json(command, {"pause": True, "max_new_chunks": max_new_chunks,
                            "new_chunks": new_chunks, "next_index": index}), "invalid pause request")
                require(index == len(records) and resumed == list(range(initial_completed)), "pause before old source bytes revalidated")
                result = pause_result(out, plan, rows, records, resumed, new_chunks, max_new_chunks)
                send_json(outgoing, result)
                return result
            if index in records:
                require(same_json(command, {"skip": index, "source_reread_sha256": records[index]["source_sha256"]}),
                        "resume source reread mismatch")
                resumed.append(index)
            else:
                require(new_chunks < max_new_chunks, "new-chunk budget exhausted; explicit pause required")
                require(resumed == list(range(initial_completed)), "new chunk before old source bytes revalidated")
                require(set(command) == {"begin", "source_before_sha256"} and type(command["begin"]) is int and command["begin"] == index,
                        "unexpected chunk sequence")
                directory = out / f"chunk-{index:06d}"
                directory.mkdir(mode=0o700, exist_ok=False)
                require_private_directory(directory)
                records[index] = receive_chunk(row, command, incoming, directory)
                new_chunks += 1
                sync_directory(out)
            send_json(outgoing, {"verified": index})
        require(same_json(get_json(incoming), {"finish": True}), "source did not complete the capture")
        require(resumed == list(range(initial_completed)), "completion before old source bytes revalidated")
        result = completion_result(plan, rows, records, resumed)
        if manifest_path.exists():
            verify_completion(load_json_file(manifest_path), plan, rows, records)
        else:
            write_json_exclusive(manifest_path, result)
        sync_directory(out)
        acknowledgement = {"complete": True, "chunks": len(rows), "manifest": str(manifest_path), "snapshot_consistent": False}
        send_json(outgoing, acknowledgement)
        return acknowledgement
    finally:
        os.close(lock_fd)


@contextlib.contextmanager
def source_session(source_path, source_bytes, ranges, *, chunk_bytes, include_gpt, allow_regular):
    require(allow_regular or chunk_bytes == 64 * IO, "production continuation requires 64 MiB chunks")
    path = Path(source_path).resolve(strict=True)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        identity = geometry(fd, allow_regular)
        require(identity["bytes"] == source_bytes and identity["logical_block_size"] == 4096,
                "actual source geometry differs from explicit expected size/4096-byte blocks")
        ranges = [dict(row) for row in ranges]
        gpt = None
        if include_gpt:
            derived, gpt = gpt_ranges(fd, source_bytes)
            for row in ranges:
                for part in gpt["partitions"]:
                    if part["name"] == row["label"]:
                        require((part["offset"], part["length"]) == (row["offset"], row["length"]), "named range contradicts actual GPT")
                    overlap = max(part["offset"], row["offset"]) < min(part["offset"] + part["length"], row["offset"] + row["length"])
                    if overlap and part["name"] in ("metadata", "userdata"):
                        require(row["classification"] == "live-mutable", "range contains live metadata/userdata")
            ranges.extend(derived)
        plan = {"schema": SCHEMA, "source": {"path": str(path), **identity}, "ranges": ranges,
                "chunk_bytes": chunk_bytes, "gpt": gpt, "source_open_mode": "O_RDONLY", "snapshot_consistent": False}
        yield fd, identity, plan, validate_plan(plan)
    finally:
        os.close(fd)


def send_session(fd, identity, plan, rows, incoming, outgoing, *, allow_regular, max_new_chunks):
    check_budget(max_new_chunks)
    send_json(outgoing, hello_message(max_new_chunks))
    require(same_json(get_json(incoming), hello_ack(max_new_chunks)), "receiver does not support exact no-delete pause protocol")
    send_json(outgoing, plan)
    ready = get_json(incoming)
    require(set(ready) == {"ready", "protocol", "max_new_chunks", "total_chunks", "completed"} and
            ready["ready"] is True and ready["protocol"] == PAUSE_PROTOCOL and
            type(ready["max_new_chunks"]) is int and ready["max_new_chunks"] == max_new_chunks and
            type(ready["total_chunks"]) is int and ready["total_chunks"] == len(rows) and isinstance(ready["completed"], list),
            "receiver readiness contract mismatch")
    complete = {}
    for record in ready["completed"]:
        require(isinstance(record, dict) and type(record.get("index")) is int and
                record["index"] not in complete and 0 <= record["index"] < len(rows), "receiver returned invalid completed chunk")
        validate_record(record, rows[record["index"]])
        require(record["source_sha256"] == record["source_before_sha256"] == record["source_after_sha256"],
                "receiver returned inconsistent source observations")
        complete[record["index"]] = record
    require(list(complete) == list(range(len(complete))), "receiver completed chunks are not an ordered prefix")
    new_chunks = 0
    for row in rows:
        index = row["index"]
        if index not in complete and new_chunks == max_new_chunks:
            require(geometry(fd, allow_regular) == identity, "source identity or geometry changed")
            send_json(outgoing, {"pause": True, "max_new_chunks": max_new_chunks, "new_chunks": new_chunks, "next_index": index})
            result = get_json(incoming)
            require(set(result) == {"paused", "complete", "chunks", "total_chunks", "next_index", "new_chunks", "max_new_chunks", "pause_receipt", "snapshot_consistent"}, "invalid pause acknowledgement fields")
            expected = {"paused": True, "complete": False, "chunks": index, "total_chunks": len(rows), "next_index": index,
                        "new_chunks": new_chunks, "max_new_chunks": max_new_chunks, "snapshot_consistent": False}
            require(all(type(result[k]) is type(v) and result[k] == v for k, v in expected.items()) and
                    isinstance(result["pause_receipt"], str) and result["pause_receipt"], "receiver did not cleanly pause")
            return result
        if index in complete:
            current = hash_range(fd, row["offset"], row["length"])
            require(current == complete[index]["source_sha256"], "source changed since completed chunk was captured")
            send_json(outgoing, {"skip": index, "source_reread_sha256": current})
        else:
            send_chunk(fd, row, outgoing)
            new_chunks += 1
        require(same_json(get_json(incoming), {"verified": index}), "receiver did not verify chunk")
        print(json.dumps({"event": "chunk-verified", "chunk": index + 1, "chunks": len(rows), "label": row["label"],
                          "source_offset": row["offset"], "bytes": row["length"], "resumed": index in complete}), file=sys.stderr, flush=True)
    require(geometry(fd, allow_regular) == identity, "source identity or geometry changed")
    send_json(outgoing, {"finish": True})
    result = get_json(incoming)
    require(set(result) == {"complete", "chunks", "manifest", "snapshot_consistent"} and result["complete"] is True and
            type(result["chunks"]) is int and result["chunks"] == len(rows) and result["snapshot_consistent"] is False and
            isinstance(result["manifest"], str) and result["manifest"], "receiver did not complete")
    return result


def send_stream(source_path, source_bytes, ranges, *, incoming, outgoing, chunk_bytes=64 * IO,
                include_gpt=False, allow_regular=False, max_new_chunks=1):
    """Read-only in-memory sender API; caller owns pipes and process supervision."""
    check_budget(max_new_chunks)
    with source_session(source_path, source_bytes, ranges, chunk_bytes=chunk_bytes,
                        include_gpt=include_gpt, allow_regular=allow_regular) as (fd, identity, plan, rows):
        return send_session(fd, identity, plan, rows, incoming, outgoing, allow_regular=allow_regular, max_new_chunks=max_new_chunks)


def send(source_path, source_bytes, ranges, transport, *, chunk_bytes=64 * IO,
         include_gpt=False, allow_regular=False, max_new_chunks=1):
    require(isinstance(transport, list) and transport and all(isinstance(a, str) and a and "\0" not in a for a in transport),
            "transport must be an explicit nonempty argv array")
    process = None
    try:
        with source_session(source_path, source_bytes, ranges, chunk_bytes=chunk_bytes,
                            include_gpt=include_gpt, allow_regular=allow_regular) as (fd, identity, plan, rows):
            process = subprocess.Popen(transport, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, shell=False)
            result = send_session(fd, identity, plan, rows, process.stdout, process.stdin, allow_regular=allow_regular, max_new_chunks=max_new_chunks)
            process.stdin.close()
            require(process.wait(timeout=30) == 0, "transport failed")
            return result
    finally:
        if process is not None:
            if process.stdin and not process.stdin.closed:
                with contextlib.suppress(BrokenPipeError):
                    process.stdin.close()
            if process.poll() is None:
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
            if process.stdout:
                process.stdout.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    sender = modes.add_parser("send")
    sender.add_argument("--source", type=Path, required=True)
    sender.add_argument("--source-bytes", type=lambda s: int(s, 0), required=True)
    sender.add_argument("--range", action="append", type=parse_range, default=[])
    sender.add_argument("--gpt", action="store_true")
    sender.add_argument("--chunk-mib", type=int, choices=(64,), default=64)
    sender.add_argument("--ssh-argv-json", type=Path, required=True)
    sender.add_argument("--receiver-argv-json", type=Path, required=True)
    sender.add_argument("--regular-file-test-only", action="store_true", help=argparse.SUPPRESS)
    sender.add_argument("--max-new-chunks", type=int, choices=ALLOWED_NEW_CHUNKS, required=True)
    receiver = modes.add_parser("receive")
    receiver.add_argument("--out", type=Path, required=True)
    receiver.add_argument("--resume", action="store_true")
    receiver.add_argument("--max-new-chunks", type=int, choices=ALLOWED_NEW_CHUNKS, required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.mode == "receive":
            receive(sys.stdin.buffer, sys.stdout.buffer, args.out, args.resume, max_new_chunks=args.max_new_chunks)
        else:
            transport = ssh_transport(load_json_file(args.ssh_argv_json), load_json_file(args.receiver_argv_json))
            result = send(args.source, args.source_bytes, args.range, transport, chunk_bytes=args.chunk_mib * IO,
                          include_gpt=args.gpt, allow_regular=args.regular_file_test_only, max_new_chunks=args.max_new_chunks)
            print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, EOFError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(f"acquisition failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
