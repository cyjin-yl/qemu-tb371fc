#!/usr/bin/env python3
"""Inspect a bounded metadata-only prefix captured from a raw super partition.

The caller supplies the independently measured full partition size. This tool
does not read logical partition contents, expand sparse files, modify metadata,
authenticate AVB or infer Android boot. It requires both geometry copies and
both copies of the selected metadata slot to validate and agree, rather than
using liblp's recovery fallback. See docs/android-super-metadata.md.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
from typing import Any


SCHEMA = "tb371fc-android-super-metadata-v1"
AOSP_REVISION = "f53626de0f57e5ba91931d8c2154a83407c1d570"
GEOMETRY_MAGIC = 0x616C4467
HEADER_MAGIC = 0x414C5030
SECTOR_SIZE = 512  # liblp extent units, independent of the UFS logical block.
GEOMETRY_SIZE = 4096
METADATA_START = 3 * GEOMETRY_SIZE
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
TABLE_FORMATS = {
    "partitions": struct.Struct("<36sIIII"),
    "extents": struct.Struct("<QIQI"),
    "groups": struct.Struct("<36sIQ"),
    "block_devices": struct.Struct("<QIIQ36sI"),
}


class MetadataError(ValueError):
    """Malformed, inconsistent, changed or unsupported metadata."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise MetadataError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def integer(value: Any, name: str, minimum: int = 0) -> int:
    require(type(value) is int and minimum <= value <= (1 << 64) - 1,
            f"{name} must be a bounded nonnegative integer")
    return value


def parse_geometry(block: bytes) -> dict[str, int]:
    require(len(block) == GEOMETRY_SIZE, "truncated geometry block")
    magic, size = struct.unpack_from("<II", block)
    require(magic == GEOMETRY_MAGIC and size == 52,
            "unsupported geometry magic or structure size")
    hashed = block[:8] + bytes(32) + block[40:52]
    require(hashlib.sha256(hashed).digest() == block[8:40],
            "geometry SHA-256 mismatch")
    maximum, count, logical = struct.unpack_from("<III", block, 40)
    require(maximum >= 512 and maximum % SECTOR_SIZE == 0,
            "invalid metadata maximum size")
    require(count > 0, "zero metadata slot count")
    total = METADATA_START + 2 * maximum * count
    require(total <= MAX_CAPTURE_BYTES, "metadata prefix exceeds capture limit")
    require(logical >= SECTOR_SIZE and logical % SECTOR_SIZE == 0,
            "invalid logical block size")
    return {"metadata_max_size": maximum, "metadata_slot_count": count,
            "logical_block_size": logical, "metadata_region_bytes": total}


def name_field(raw: bytes, kind: str, suffix: bool, slot: int) -> str:
    name, sep, padding = raw.partition(b"\0")
    require(not sep or not any(padding), f"{kind} has nonzero name padding")
    # libsnapshot::GetCowName appends "-cow" to real LP partition names.
    # This supported form is narrower than accepting arbitrary punctuation.
    pattern = rb"[A-Za-z0-9_]+(?:-cow)?" if kind == "partition" else rb"[A-Za-z0-9_]+"
    require(1 <= len(name) <= 36 and re.fullmatch(pattern, name) is not None,
            f"invalid {kind} name")
    result = name.decode("ascii")
    if suffix:
        require(not result.endswith("-cow"), "slot-suffixed COW names are unsupported")
        require(slot in (0, 1), "slot-suffixed names support only slots 0 and 1")
        result += "_a" if slot == 0 else "_b"
        require(len(result) <= 36, f"{kind} name overflows after slot suffix")
    return result


def unique_names(items: list[dict[str, Any]], kind: str) -> None:
    names = [item["name"] for item in items]
    require(len(names) == len(set(names)), f"duplicate {kind} name")


def parse_slot(raw: bytes, geometry: dict[str, int], partition_size: int,
               slot: int) -> dict[str, Any]:
    require(len(raw) == geometry["metadata_max_size"], "truncated metadata slot")
    magic, major, minor, header_size = struct.unpack_from("<IHHI", raw)
    require(magic == HEADER_MAGIC and major == 10 and minor in (0, 1, 2),
            "unsupported metadata version or magic")
    require(header_size == (256 if minor >= 2 else 128),
            "metadata header size does not match version")
    header = raw[:header_size]
    expected = hashlib.sha256(header[:12] + bytes(32) + header[44:]).digest()
    require(expected == header[12:44], "metadata header SHA-256 mismatch")
    table_size = struct.unpack_from("<I", header, 44)[0]
    require(header_size + table_size <= len(raw), "tables escape metadata slot")
    tables = raw[header_size:header_size + table_size]
    require(hashlib.sha256(tables).digest() == header[48:80],
            "metadata table SHA-256 mismatch")
    flags = struct.unpack_from("<I", header, 128)[0] if minor >= 2 else 0
    if minor >= 2:
        require(not any(header[132:]), "nonzero expanded-header reserved bytes")

    records: dict[str, list[tuple]] = {}
    ranges = []
    for index, (kind, fmt) in enumerate(TABLE_FORMATS.items()):
        offset, count, entry_size = struct.unpack_from("<III", header, 80 + index * 12)
        require(entry_size == fmt.size, f"unsupported {kind} entry size")
        size = count * entry_size
        require(size <= 0x7fffffff and offset <= len(tables) and
                size <= len(tables) - offset, f"{kind} table is out of bounds")
        if size:
            ranges.append((offset, offset + size))
        records[kind] = [fmt.unpack_from(tables, offset + n * entry_size)
                         for n in range(count)]
    end = 0
    for start, stop in sorted(ranges):
        require(start == end, "metadata tables overlap or contain gaps")
        end = stop
    require(end == len(tables), "unowned bytes in metadata tables")

    logical = geometry["logical_block_size"]
    devices = []
    for first, alignment, align_offset, size, name, device_flags in records["block_devices"]:
        require(device_flags & ~1 == 0, "unknown block-device flags")
        require(size > 0 and size % logical == 0 and first * SECTOR_SIZE < size,
                "invalid block-device bounds")
        require(alignment % SECTOR_SIZE == 0 and align_offset % SECTOR_SIZE == 0,
                "invalid block-device alignment fields")
        require(not alignment or align_offset < alignment,
                "block-device alignment offset is out of bounds")
        devices.append({"name": name_field(name, "block-device", bool(device_flags & 1), slot),
                        "size": size, "first_logical_sector": first,
                        "alignment": alignment, "alignment_offset": align_offset,
                        "flags": device_flags})
    require(len(devices) == 1, "this inspector supports one physical super partition only")
    require(devices[0]["size"] == partition_size,
            "metadata block-device size differs from measured partition size")
    require(devices[0]["first_logical_sector"] * SECTOR_SIZE >=
            geometry["metadata_region_bytes"], "logical contents overlap metadata region")
    unique_names(devices, "block-device")

    groups = []
    for name, group_flags, maximum in records["groups"]:
        require(group_flags & ~1 == 0, "unknown group flags")
        groups.append({"name": name_field(name, "group", bool(group_flags & 1), slot),
                       "flags": group_flags, "maximum_size": maximum, "used_bytes": 0})
    unique_names(groups, "group")

    extents = []
    physical_ranges = []
    for index, (sectors, target_type, target_data, target_source) in enumerate(records["extents"]):
        size = sectors * SECTOR_SIZE
        require(0 < size <= (1 << 64) - 1 and size % logical == 0,
                "invalid extent length or logical-block alignment")
        item = {"index": index, "num_sectors": sectors, "size": size,
                "target_type": target_type, "target_source": target_source,
                "target_sector": target_data}
        if target_type == 0:
            require(target_source < len(devices), "extent has invalid target device")
            device = devices[target_source]
            start = target_data * SECTOR_SIZE
            require(start % logical == 0 and target_data >= device["first_logical_sector"] and
                    start <= device["size"] and size <= device["size"] - start,
                    "linear extent escapes block-device data region")
            physical_ranges.append((target_source, start, start + size))
            item["physical_offset"] = start
        else:
            require(target_type == 1 and target_data == 0 and target_source == 0,
                    "unknown extent target or invalid zero extent")
        extents.append(item)
    previous: tuple[int, int, int] | None = None
    for current in sorted(physical_ranges):
        require(previous is None or current[0] != previous[0] or current[1] >= previous[2],
                "linear extents overlap physically")
        previous = current

    owners: set[int] = set()
    partitions = []
    attributes_mask = 3 if minor == 0 else 15
    for name, attributes, first, count, group_index in records["partitions"]:
        require(attributes & ~attributes_mask == 0, "unknown partition attributes")
        require(first <= len(extents) and count <= len(extents) - first,
                "partition extent range is out of bounds")
        require(group_index < len(groups), "partition group index is out of bounds")
        indices = range(first, first + count)
        require(not any(index in owners for index in indices), "extent is owned by multiple partitions")
        owners.update(indices)
        size = sum(extents[index]["size"] for index in indices)
        require(size <= (1 << 64) - 1, "partition size overflows")
        groups[group_index]["used_bytes"] += size
        partitions.append({"name": name_field(name, "partition", bool(attributes & 2), slot),
                           "attributes": attributes, "disabled": bool(attributes & 8),
                           "size": size, "group": groups[group_index]["name"],
                           "extent_indices": list(indices), "empty": count == 0})
    unique_names(partitions, "partition")
    partition_names = {item["name"] for item in partitions}
    for item in partitions:
        item["snapshot_cow_partition"] = item["name"].endswith("-cow")
        if item["snapshot_cow_partition"]:
            require(item["group"] == "cow" and item["name"][:-4] in partition_names,
                    "COW partition lacks its base partition or cow group")
    require(owners == set(range(len(extents))), "unowned extent records")
    for group in groups:
        require(not group["maximum_size"] or group["used_bytes"] <= group["maximum_size"],
                "partition group exceeds its maximum size")

    return {"version": f"{major}.{minor}", "header_size": header_size,
            "tables_size": table_size, "header_flags": flags,
            "unknown_informational_header_flags": flags & ~1,
            "snapshot_related_metadata_present": bool(flags & 1 or any(
                item["snapshot_cow_partition"] or item["attributes"] & 4 for item in partitions)),
            "metadata_sha256": digest(raw[:header_size + table_size]),
            "header_sha256": digest(header), "tables_sha256": digest(tables),
            "block_devices": devices, "groups": groups,
            "partitions": partitions, "extents": extents}


def inspect_prefix(raw: bytes, *, partition_size: int, slot: int = 0) -> dict[str, Any]:
    integer(partition_size, "partition_size", 1)
    integer(slot, "slot")
    require(METADATA_START <= len(raw) <= MAX_CAPTURE_BYTES, "invalid metadata prefix length")
    require(raw[:4] not in (b"QFI\xfb", b"\x3a\xff\x26\xed"),
            "metadata capture must be raw bytes, not qcow2 or Android sparse")
    first = parse_geometry(raw[4096:8192])
    second = parse_geometry(raw[8192:12288])
    require(first == second and raw[4096:4148] == raw[8192:8244],
            "primary and backup geometry disagree")
    require(len(raw) == first["metadata_region_bytes"],
            "capture must contain exactly the complete metadata prefix")
    require(partition_size % first["logical_block_size"] == 0 and len(raw) < partition_size,
            "measured partition size contradicts metadata geometry")
    require(slot < first["metadata_slot_count"], "selected metadata slot is out of bounds")
    maximum = first["metadata_max_size"]
    primary_offset = METADATA_START + slot * maximum
    backup_offset = METADATA_START + (first["metadata_slot_count"] + slot) * maximum
    primary = parse_slot(raw[primary_offset:primary_offset + maximum], first, partition_size, slot)
    backup = parse_slot(raw[backup_offset:backup_offset + maximum], first, partition_size, slot)
    require(primary == backup, "primary and backup selected-slot metadata disagree")
    return {"schema": SCHEMA, "status": "selected_slot_metadata_verified",
            "scope": "bounded captured metadata; no partition payload or Android runtime",
            "reference": {"repository": "aosp-mirror/platform_system_core",
                          "revision": AOSP_REVISION, "component": "fs_mgr/liblp"},
            "source": {"kind": "metadata_prefix_capture", "bytes": len(raw),
                       "sha256": digest(raw), "partition_size_supplied_by_caller": partition_size,
                       "whole_partition_sha256_verified": False},
            "geometry": first, "metadata_slot": slot,
            "primary_metadata_offset": primary_offset, "backup_metadata_offset": backup_offset,
            "geometry_copies_verified": 2, "selected_metadata_copies_verified": 2,
            "other_metadata_slots_verified": False, "metadata": primary,
            "partition_payloads_verified": False, "avb_verified": False,
            "snapshot_state_verified": False, "android_boot_verified": False}


def inspect_capture(path: Path, *, partition_size: int, slot: int = 0,
                    expected_sha256: str | None = None) -> dict[str, Any]:
    if expected_sha256 is not None:
        require(re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is not None,
                "expected SHA-256 must be lowercase hexadecimal")
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode), "capture must be a regular file")
        require(METADATA_START <= before.st_size <= MAX_CAPTURE_BYTES,
                "capture file exceeds metadata-only size bounds")
        raw = os.pread(fd, before.st_size, 0)
        require(len(raw) == before.st_size, "truncated capture read")
        require(expected_sha256 is None or digest(raw) == expected_sha256,
                "capture SHA-256 differs from supplied receipt")
        result = inspect_prefix(raw, partition_size=partition_size, slot=slot)
        after = os.fstat(fd)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        require(identity(before) == identity(after) == identity(os.stat(path, follow_symlinks=False)),
                "capture file changed during inspection")
        require(os.pread(fd, before.st_size, 0) == raw, "capture bytes changed during inspection")
        result["capture_unchanged"] = True
        result["capture_receipt_sha256_verified"] = expected_sha256 is not None
        return result
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--partition-size", required=True, type=int)
    parser.add_argument("--slot", type=int, default=0)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    try:
        result = inspect_capture(args.capture, partition_size=args.partition_size,
                                 slot=args.slot, expected_sha256=args.expected_sha256)
    except (MetadataError, OSError) as exc:
        print(json.dumps({"schema": SCHEMA, "status": "rejected", "error": str(exc),
                          "android_boot_verified": False}, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
