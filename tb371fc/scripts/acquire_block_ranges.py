#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read explicit block ranges and stream verified gzip chunks to an SSH receiver.

No source writes, mount, freeze, discard, restore, GPT repair or snapshot claim.
The sender holds only 1 MiB buffers; chunk files exist only at the receiver.
--ssh-argv-json names an SSH argv prefix including the approved host; the remote
--receiver-argv-json is safely shell-quoted as one SSH command argument.
No local shell is invoked and no credentials or destinations are discovered.
Range syntax: LABEL:BYTE_OFFSET:BYTE_LENGTH:immutable|live-mutable.
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


def require(value, message):
    if not value:
        raise ValueError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def send_json(stream, value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    require(len(raw) <= JSON_LIMIT, "protocol JSON exceeds limit")
    stream.write(raw)
    stream.flush()


def get_json(stream):
    raw = stream.readline(JSON_LIMIT + 1)
    require(raw.endswith(b"\n") and len(raw) <= JSON_LIMIT, "truncated/oversized protocol JSON")
    value = json.loads(raw)
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


def discard_partial(path):
    if path.exists() or path.is_symlink():
        require(path.is_file() and not path.is_symlink(), "unsafe partial output")
        path.unlink()


def write_json_exclusive(path, value):
    partial = path.with_name(path.name + ".partial")
    fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.link(partial, path)
    partial.unlink()
    sync_directory(path.parent)


def load_json_file(path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= JSON_LIMIT,
            "invalid saved JSON file")
    return json.loads(path.read_bytes())


def verify_chunk(path, record):
    require(path.is_file() and not path.is_symlink(), "missing/unsafe stored chunk")
    require(path.stat().st_size == record["compressed_bytes"], "stored compressed size changed")
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for data in iter(lambda: stream.read(IO), b""):
            digest.update(data)
    require(digest.hexdigest() == record["transport_sha256"], "stored transport SHA-256 mismatch")
    digest, total = hashlib.sha256(), 0
    with gzip.open(path, "rb") as stream:
        while data := stream.read(min(IO, record["length"] + 1 - total)):
            total += len(data)
            require(total <= record["length"], "decoded chunk exceeds expected length")
            digest.update(data)
    require(total == record["length"] and digest.hexdigest() == record["source_sha256"],
            "stored decoded length/SHA-256 mismatch")
    require(record["source_sha256"] == record["source_before_sha256"] ==
            record["source_after_sha256"], "source observations disagree")
    if "expected_sha256" in record:
        require(record["source_sha256"] == record["expected_sha256"],
                "stored source bytes contradict the expected range digest")


class FramedWriter:
    def __init__(self, stream):
        self.stream, self.digest, self.size = stream, hashlib.sha256(), 0

    def write(self, data):
        for start in range(0, len(data), IO):
            part = data[start:start + IO]
            self.stream.write(struct.pack(">I", len(part)))
            self.stream.write(part)
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
    output.write(struct.pack(">I", 0))
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
    with partial.open("xb") as stream:
        os.chmod(partial, 0o600)
        while length := struct.unpack(">I", read_exact(incoming, 4))[0]:
            require(length <= IO and size + length <= limit, "compressed transfer exceeds frame/chunk bound")
            data = read_exact(incoming, length)
            stream.write(data)
            digest.update(data)
            size += length
        stream.flush()
        os.fsync(stream.fileno())
    record = get_json(incoming)
    require(all(record.get(key) == value for key, value in row.items()), "chunk receipt geometry mismatch")
    require(record.get("compressed_bytes") == size and record.get("transport_sha256") == digest.hexdigest(),
            "received compressed stream SHA-256/length mismatch")
    require(record.get("source_before_sha256") == begin.get("source_before_sha256") and
            record.get("source_rechecked") is True, "missing source reread evidence")
    verify_chunk(partial, record)
    # Link is exclusive, including after interruptions; never replace a file.
    os.link(partial, final)
    partial.unlink()
    write_json_exclusive(directory / "receipt.json", record)
    return record


def receive(incoming, outgoing, output_dir, resume=False):
    plan = get_json(incoming)
    rows = validate_plan(plan)
    out = Path(output_dir).absolute()
    require(not out.is_symlink(), "output directory must not be a symlink")
    if resume:
        require(out.is_dir(), "resume needs an existing capture directory")
    else:
        out.mkdir(mode=0o700, parents=False, exist_ok=False)
    info = out.stat()
    require(info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            "receiver directory must be private and owned by this user")
    lock_fd = os.open(out / "capture.lock", os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if resume:
            if not (out / "plan.json").exists():
                require(all(p.name in ("capture.lock", "plan.json.partial") for p in out.iterdir()),
                        "missing committed plan with existing capture files")
                discard_partial(out / "plan.json.partial")
                write_json_exclusive(out / "plan.json", plan)
            require(load_json_file(out / "plan.json") == plan, "resume source/ranges/geometry changed")
            discard_partial(out / "plan.json.partial")
            discard_partial(out / "manifest.json.partial")
        else:
            write_json_exclusive(out / "plan.json", plan)
        records = {}
        for row in rows:
            directory = out / f"chunk-{row['index']:06d}"
            require(not directory.is_symlink(), "chunk directory is a symlink")
            if (directory / "receipt.json").exists():
                require(resume, "existing completed chunk without resume")
                record = load_json_file(directory / "receipt.json")
                require(all(record.get(key) == value for key, value in row.items()), "saved chunk geometry mismatch")
                verify_chunk(directory / "data.gz", record)
                records[row["index"]] = record
                discard_partial(directory / "receipt.json.partial")
            elif directory.exists():
                # Only our interrupted, uncommitted files may be discarded.
                require(resume and directory.is_dir(), "unexpected chunk output")
                for file in directory.iterdir():
                    require(file.name in ("data.gz.partial", "data.gz", "receipt.json.partial") and
                            file.is_file() and not file.is_symlink(), "unexpected partial chunk contents")
                for file in directory.iterdir():
                    file.unlink()
                directory.rmdir()
        manifest_path = out / "manifest.json"
        require(not manifest_path.exists() or len(records) == len(rows), "completed manifest has missing chunks")
        send_json(outgoing, {"ready": True, "completed": [records[i] for i in sorted(records)]})
        resumed = []
        for row in rows:
            command = get_json(incoming)
            index = row["index"]
            if index in records:
                require(command.get("skip") == index and
                        command.get("source_reread_sha256") == records[index]["source_sha256"],
                        "resume source reread mismatch")
                resumed.append(index)
            else:
                require(command.get("begin") == index, "unexpected chunk sequence")
                directory = out / f"chunk-{index:06d}"
                directory.mkdir(mode=0o700, exist_ok=False)
                records[index] = receive_chunk(row, command, incoming, directory)
                sync_directory(out)
            send_json(outgoing, {"verified": index})
        require(get_json(incoming) == {"finish": True}, "source did not complete the capture")
        result = {"schema": SCHEMA, "status": "verified-range-copies", "completed_utc": utc(),
                  "plan": plan, "chunks": [records[i] for i in sorted(records)],
                  "resume_source_revalidated_chunks": resumed, "snapshot_consistent": False,
                  "source_immutable_claim_verified": False, "pristine_factory_images": False,
                  "restore_tested": False, "android_boot_tested": False,
                  "live_mutable_ranges": [r["label"] for r in plan["ranges"] if r["classification"] == "live-mutable"],
                  "whole_source_bytes_captured": sum(r["length"] for r in plan["ranges"]) == plan["source"]["bytes"],
                  "qualification": "Per-chunk current-byte copies; not an atomic snapshot or factory authenticity proof"}
        if not manifest_path.exists():
            write_json_exclusive(manifest_path, result)
        else:
            previous = load_json_file(manifest_path)
            # Schema v1 is closed: no unknown or missing completion fields.
            require(isinstance(previous, dict) and set(previous) == set(result),
                    "existing completion manifest fields disagree")
            # Keep the original run's annotations, not this resume's timestamp
            # and skip list. Every qualification is rederived above from the
            # validated plan and verified records, including full coverage.
            require(isinstance(previous["completed_utc"], str),
                    "existing completion manifest has invalid completion time")
            completed = datetime.datetime.fromisoformat(previous["completed_utc"])
            require(completed.utcoffset() == datetime.timedelta(0),
                    "existing completion manifest completion time must be UTC")
            previous_resumed = previous["resume_source_revalidated_chunks"]
            require(isinstance(previous_resumed, list) and
                    all(type(index) is int and 0 <= index < len(rows) for index in previous_resumed),
                    "existing completion manifest has invalid resume indices")
            require(previous_resumed == sorted(set(previous_resumed)),
                    "existing completion manifest has duplicate or unordered resume indices")
            expected = {**result, "completed_utc": previous["completed_utc"],
                        "resume_source_revalidated_chunks": previous_resumed}
            # Canonical JSON also distinguishes booleans from equal integers.
            require(json.dumps(previous, sort_keys=True) == json.dumps(expected, sort_keys=True),
                    "existing completion manifest disagrees")
        sync_directory(out)
        send_json(outgoing, {"complete": True, "chunks": len(rows), "manifest": str(manifest_path),
                             "snapshot_consistent": False})
    finally:
        os.close(lock_fd)


def send(source_path, source_bytes, ranges, transport, *, chunk_bytes=64 * IO,
         include_gpt=False, allow_regular=False):
    path = Path(source_path).resolve(strict=True)
    require(isinstance(transport, list) and transport and all(isinstance(a, str) and a and "\0" not in a for a in transport),
            "transport must be an explicit nonempty argv array")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    process = None
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
                        require((part["offset"], part["length"]) == (row["offset"], row["length"]),
                                "named requested range contradicts actual GPT")
                    overlap = max(part["offset"], row["offset"]) < min(part["offset"] + part["length"], row["offset"] + row["length"])
                    if overlap and part["name"] in ("metadata", "userdata"):
                        require(row["classification"] == "live-mutable", "range contains live metadata/userdata")
            ranges.extend(derived)
        plan = {"schema": SCHEMA, "source": {"path": str(path), **identity}, "ranges": ranges,
                "chunk_bytes": chunk_bytes, "gpt": gpt, "source_open_mode": "O_RDONLY",
                "snapshot_consistent": False}
        rows = validate_plan(plan)
        process = subprocess.Popen(transport, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=None, shell=False)
        send_json(process.stdin, plan)
        hello = get_json(process.stdout)
        require(hello.get("ready") is True, "receiver not ready")
        complete = {row["index"]: row for row in hello["completed"]}
        for row in rows:
            if row["index"] in complete:
                old = complete[row["index"]]
                require(all(old.get(key) == value for key, value in row.items()), "receiver returned wrong completed chunk")
                current = hash_range(fd, row["offset"], row["length"])
                require(current == old["source_sha256"], "source changed since completed chunk was captured")
                send_json(process.stdin, {"skip": row["index"], "source_reread_sha256": current})
            else:
                send_chunk(fd, row, process.stdin)
            require(get_json(process.stdout) == {"verified": row["index"]}, "receiver did not verify chunk")
            print(json.dumps({"event": "chunk-verified", "chunk": row["index"] + 1,
                              "chunks": len(rows), "label": row["label"],
                              "source_offset": row["offset"], "bytes": row["length"],
                              "resumed": row["index"] in complete}),
                  file=sys.stderr, flush=True)
        require(geometry(fd, allow_regular) == identity, "source identity or geometry changed")
        send_json(process.stdin, {"finish": True})
        result = get_json(process.stdout)
        require(result.get("complete") is True and result.get("chunks") == len(rows), "receiver did not complete")
        process.stdin.close()
        require(process.wait(timeout=30) == 0, "transport failed")
        return result
    finally:
        os.close(fd)
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
    sender.add_argument("--chunk-mib", type=int, choices=(64, 256), default=64)
    sender.add_argument("--ssh-argv-json", type=Path, required=True,
                        help="private JSON argv prefix: SSH executable/options and approved host")
    sender.add_argument("--receiver-argv-json", type=Path, required=True,
                        help="private JSON argv: remote python, this script, receive, --out, directory, [--resume]")
    sender.add_argument("--regular-file-test-only", action="store_true", help=argparse.SUPPRESS)
    receiver = modes.add_parser("receive")
    receiver.add_argument("--out", type=Path, required=True)
    receiver.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.mode == "receive":
            receive(sys.stdin.buffer, sys.stdout.buffer, args.out, args.resume)
        else:
            transport = ssh_transport(load_json_file(args.ssh_argv_json),
                                      load_json_file(args.receiver_argv_json))
            result = send(args.source, args.source_bytes, args.range, transport,
                          chunk_bytes=args.chunk_mib * IO, include_gpt=args.gpt,
                          allow_regular=args.regular_file_test_only)
            print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, EOFError, KeyError, subprocess.SubprocessError) as error:
        print(f"acquisition failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
