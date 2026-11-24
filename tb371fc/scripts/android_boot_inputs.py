#!/usr/bin/env python3
"""Prepare byte-preserving Android v0/v1/v2 inputs for direct-kernel boot.

Only caller-supplied regular files are opened, read-only.  This module does not
create partitions, select a slot, rewrite a ramdisk, authenticate AVB, or claim
that Android booted.  The supplied DT is kept whole; its board coverage and
firmware provenance cannot be inferred from a valid FDT header.

Header/layout reference (AOSP format, retained by LineageOS):
https://github.com/LineageOS/android_system_tools_mkbootimg/blob/
90054adc0017398ae868e990e85fd8dc58020c75/include/bootimg/bootimg.h
The paired mkbootimg.py specifies 2048/4096/8192/16384-byte pages and places
recovery_dtbo immediately after the page-aligned second-stage component.

Public API:
  parse_boot_image(path) -> source/header/components metadata, no full hash
  inspect_full_dtb(path) -> validated FDT layout and /chosen/bootargs
  validate_storage_manifest(path, verify_hashes=False) -> normalized raw LUs
  prepare_android_inputs(boot_image, full_dtb, output_dir, *,
      storage_manifest=None, bootargs_file=None, verify_lun_hashes=False)
  load_prepared_manifest(path, verify_hashes=True) -> validated runner inputs

Prepared manifests and bootargs may contain private device metadata; they are
runtime artifacts, not source files to publish.  Hashes prove byte identity,
not factory provenance.  Large LU contents are hashed only when requested or
when load_prepared_manifest verifies a supplied expected digest.
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


SCHEMA = "tb371fc-android-boot-inputs-v1"
STORAGE_SCHEMA = "tb371fc-android-storage-v1"
BOOT_MAGIC = b"ANDROID!"
HEADER_SIZES = {0: 1632, 1: 1648, 2: 1660}
PAGE_SIZES = {2048, 4096, 8192, 16384}
FDT_MAGIC = 0xD00DFEED
MAX_FDT_BYTES = 32 * 1024 * 1024
MAX_MANIFEST_BYTES = 4 * 1024 * 1024
MAX_BOOTARGS_BYTES = 64 * 1024
COPY_CHUNK = 1024 * 1024


class InputValidationError(ValueError):
    """An input is malformed, contradictory, changed, or unsupported."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InputValidationError(message)


def _integer(value: Any, name: str, minimum: int = 0) -> int:
    _require(type(value) is int and value >= minimum,
             f"{name} must be an integer >= {minimum}")
    return value


def _sha(value: Any, name: str) -> str:
    _require(isinstance(value, str) and
             re.fullmatch(r"[0-9a-fA-F]{64}", value) is not None,
             f"{name} must be a SHA-256 hex digest")
    return value.lower()


def _regular(path: os.PathLike[str] | str) -> tuple[Path, os.stat_result]:
    path = Path(path).resolve(strict=True)
    info = path.stat()
    _require(stat.S_ISREG(info.st_mode), f"not a regular file: {path}")
    return path, info


def _identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _unchanged(path: Path, before: os.stat_result) -> None:
    _require(_identity(path.stat()) == _identity(before),
             f"input changed during preparation: {path}")


def sha256_file(path: os.PathLike[str] | str) -> str:
    path, before = _regular(path)
    digest = hashlib.sha256()
    with path.open("rb") as source:
        _require(_identity(os.fstat(source.fileno())) == _identity(before),
                 f"input changed while opening: {path}")
        for data in iter(lambda: source.read(COPY_CHUNK), b""):
            digest.update(data)
        _require(_identity(os.fstat(source.fileno())) == _identity(before),
                 f"input changed while hashing: {path}")
    _unchanged(path, before)
    return digest.hexdigest()


def _read_bounded(path: os.PathLike[str] | str, limit: int) -> tuple[Path, bytes]:
    path, before = _regular(path)
    _require(before.st_size <= limit, f"input exceeds {limit} bytes: {path}")
    with path.open("rb") as source:
        _require(_identity(os.fstat(source.fileno())) == _identity(before),
                 f"input changed while opening: {path}")
        data = source.read(limit + 1)
        _require(len(data) == before.st_size, f"short or changed input: {path}")
        _require(_identity(os.fstat(source.fileno())) == _identity(before),
                 f"input changed while reading: {path}")
    _unchanged(path, before)
    return path, data


def _json_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _read_json(path: os.PathLike[str] | str) -> tuple[Path, dict[str, Any], bytes]:
    path, raw = _read_bounded(path, MAX_MANIFEST_BYTES)
    try:
        value = json.loads(raw, object_pairs_hook=_json_pairs)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise InputValidationError(f"invalid JSON: {path}: {error}") from error
    _require(isinstance(value, dict), f"manifest must be a JSON object: {path}")
    return path, value, raw


def _align(size: int, alignment: int) -> int:
    return (size + alignment - 1) // alignment * alignment


def _c_string(raw: bytes, name: str) -> str:
    _require(b"\0" in raw, f"{name} is not NUL terminated")
    try:
        return raw.split(b"\0", 1)[0].decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise InputValidationError(f"{name} is not UTF-8") from error


def _command_line(raw: bytes, name: str) -> str:
    _require(len(raw) <= MAX_BOOTARGS_BYTES, f"{name} is too long")
    _require(not any(byte == 0 or byte in (10, 13) for byte in raw),
             f"{name} contains NUL or a line break")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise InputValidationError(f"{name} is not UTF-8") from error


def parse_boot_image(path: os.PathLike[str] | str) -> dict[str, Any]:
    """Parse the canonical packed v0/v1/v2 layout without loading its payload.

    All occupied component pages must fit the supplied file.  Additional bytes
    after the last component may be partition padding/signatures and remain
    opaque.  This parser does not treat the old vendor dt_size word as a header
    version, and does not infer physical load-address safety from file offsets.
    """
    path, before = _regular(path)
    with path.open("rb") as source:
        _require(_identity(os.fstat(source.fileno())) == _identity(before),
                 "boot image changed while opening")
        raw = source.read(HEADER_SIZES[2])
    _unchanged(path, before)
    _require(len(raw) >= HEADER_SIZES[0], "truncated Android boot v0 header")
    _require(raw[:8] == BOOT_MAGIC, "not an Android boot image")
    values = struct.unpack_from("<10I", raw, 8)
    names = ("kernel_size", "kernel_addr", "ramdisk_size", "ramdisk_addr",
             "second_size", "second_addr", "tags_addr", "page_size",
             "header_version", "os_version")
    header = dict(zip(names, values))
    version, page = header["header_version"], header["page_size"]
    _require(version in HEADER_SIZES,
             f"unsupported boot header version {version}; expected 0, 1, or 2")
    required = HEADER_SIZES[version]
    _require(len(raw) >= required, f"truncated Android boot v{version} header")
    _require(page in PAGE_SIZES, f"unsupported boot page_size {page}")
    _require(required <= page <= before.st_size, "header page is out of bounds")
    _require(header["kernel_size"] > 0, "boot kernel must not be empty")
    _require(header["ramdisk_size"] > 0, "boot ramdisk must not be empty")
    header["header_size"] = required
    header["name"] = _c_string(raw[48:64], "boot product name")
    first = _c_string(raw[64:576], "boot cmdline")
    extra = _c_string(raw[608:1632], "boot extra_cmdline")
    header["cmdline"] = _command_line((first + extra).encode(), "boot cmdline")
    header["image_id_hex"] = raw[576:608].hex()
    header["recovery_dtbo_size"] = 0
    header["recovery_dtbo_offset"] = 0
    header["dtb_size"] = 0
    header["dtb_addr"] = 0
    if version >= 1:
        dtbo_size, dtbo_offset, reported_size = struct.unpack_from("<IQI", raw, 1632)
        _require(reported_size == required,
                 f"v{version} header_size {reported_size} != {required}")
        header["recovery_dtbo_size"] = dtbo_size
        header["recovery_dtbo_offset"] = dtbo_offset
    if version == 2:
        header["dtb_size"], header["dtb_addr"] = struct.unpack_from("<IQ", raw, 1648)
        _require(header["dtb_size"] > 0, "v2 DTB component must not be empty")

    components: dict[str, dict[str, int]] = {}
    cursor = page
    for name in ("kernel", "ramdisk", "second", "recovery_dtbo", "dtb"):
        size = header[f"{name}_size"]
        if name == "recovery_dtbo":
            explicit = header["recovery_dtbo_offset"]
            _require((size == 0 and explicit == 0) or
                     (size > 0 and explicit == cursor and explicit % page == 0),
                     "recovery_dtbo_offset contradicts the page layout or overlaps another component")
        end = cursor + size
        padded_end = cursor + _align(size, page)
        _require(end <= before.st_size and padded_end <= before.st_size,
                 f"{name} component or its page padding is outside the boot image")
        components[name] = {"offset": cursor, "size": size, "end": end,
                            "padded_end": padded_end}
        cursor = padded_end
    occupied = [(item["offset"], item["end"], name)
                for name, item in components.items() if item["size"]]
    for previous, current in zip(occupied, occupied[1:]):
        _require(previous[1] <= current[0],
                 f"overlapping components: {previous[2]} and {current[2]}")
    return {
        "source": {"path": str(path), "size": before.st_size},
        "header": header, "components": components,
        "payload_padded_end": cursor, "trailing_bytes": before.st_size - cursor,
        "header_sha256": hashlib.sha256(raw[:required]).hexdigest(),
        "avb_verified": False, "factory_provenance_verified": False,
        "physical_load_addresses_verified": False,
    }


def _inspect_fdt(raw: bytes) -> dict[str, Any]:
    _require(len(raw) >= 40, "truncated FDT header")
    (magic, total, structure, strings, reserve, version, compatible,
     boot_cpu, strings_size, structure_size) = struct.unpack_from(">10I", raw)
    _require(magic == FDT_MAGIC, "not an FDT blob")
    _require(version == 17 and compatible <= 17, "only FDT v17 layout is supported")
    _require(40 <= total <= len(raw), "FDT totalsize exceeds the supplied file")
    _require(structure % 4 == 0 and reserve % 8 == 0,
             "misaligned FDT structure or reserve-map offset")
    regions = [(0, 40, "header")]
    for start, size, name in ((structure, structure_size, "structure"),
                              (strings, strings_size, "strings")):
        _require(start >= 40 and start + size <= total,
                 f"FDT {name} block is out of bounds")
        regions.append((start, start + size, name))
    _require(reserve >= 40, "FDT reserve map overlaps its header")
    position = reserve
    while True:
        _require(position + 16 <= total, "unterminated FDT reserve map")
        address, length = struct.unpack_from(">QQ", raw, position)
        position += 16
        if address == 0 and length == 0:
            break
        _require(address + length <= (1 << 64), "FDT reservation address overflow")
    regions.append((reserve, position, "reserve map"))
    regions.sort()
    for previous, current in zip(regions, regions[1:]):
        _require(previous[1] <= current[0],
                 f"FDT blocks overlap: {previous[2]} and {current[2]}")

    names = raw[strings:strings + strings_size]
    stop, position = structure + structure_size, structure
    stack: list[str] = []
    paths: set[str] = set()
    properties: set[tuple[str, str]] = set()
    chosen: bytes | None = None
    nonzero_padding: list[dict[str, Any]] = []

    def inspect_padding(start: int, end: int, kind: str, path: str,
                        property_name: str | None = None) -> None:
        # Live Android's captured FDT has two nonzero structure-padding regions.
        # They are forensic evidence, not property data. Retain every byte and
        # report the format violation while keeping all layout checks strict.
        padding = raw[start:end]
        if any(padding):
            record: dict[str, Any] = {
                "offset": start, "length": end - start, "kind": kind,
                "node": path, "nonzero_bytes": sum(byte != 0 for byte in padding),
                "sha256": hashlib.sha256(padding).hexdigest(),
            }
            if property_name is not None:
                record["property"] = property_name
            nonzero_padding.append(record)

    ended = False
    while position < stop:
        _require(position + 4 <= stop, "truncated FDT token")
        token = struct.unpack_from(">I", raw, position)[0]
        position += 4
        if token == 1:  # FDT_BEGIN_NODE
            end = raw.find(b"\0", position, stop)
            _require(end >= 0, "unterminated FDT node name")
            try:
                node = raw[position:end].decode("utf-8", errors="strict")
            except UnicodeError as error:
                raise InputValidationError("invalid FDT node name") from error
            if not paths:
                _require(not stack and node == "", "FDT root must have an empty name")
            else:
                _require(bool(stack) and node != "" and "/" not in node,
                         "invalid second root or child node name")
            stack.append(node)
            path = "/".join(stack) or "/"
            _require(path not in paths, f"duplicate FDT node: {path}")
            paths.add(path)
            position = _align(end + 1, 4)
            _require(position <= stop, "FDT node name crosses structure boundary")
            inspect_padding(end + 1, position, "node-name-padding", path)
        elif token == 2:  # FDT_END_NODE
            _require(bool(stack), "unbalanced FDT END_NODE")
            stack.pop()
        elif token == 3:  # FDT_PROP
            _require(bool(stack) and position + 8 <= stop, "FDT property outside node or truncated")
            length, name_offset = struct.unpack_from(">II", raw, position)
            position += 8
            end = position + length
            _require(end <= stop and name_offset < len(names), "FDT property is out of bounds")
            name_end = names.find(b"\0", name_offset)
            _require(name_end >= 0 and name_end > name_offset, "invalid FDT property name")
            try:
                name = names[name_offset:name_end].decode("ascii", errors="strict")
            except UnicodeError as error:
                raise InputValidationError("non-ASCII FDT property name") from error
            path = "/".join(stack) or "/"
            _require((path, name) not in properties, f"duplicate FDT property {path}/{name}")
            properties.add((path, name))
            if path == "/chosen" and name == "bootargs":
                chosen = raw[position:end]
            position = _align(end, 4)
            _require(position <= stop, "FDT property padding crosses structure boundary")
            inspect_padding(end, position, "property-padding", path, name)
        elif token == 4:  # FDT_NOP
            continue
        elif token == 9:  # FDT_END
            _require(not stack and bool(paths), "FDT END before balanced root completion")
            _require(not any(raw[position:stop]), "unexpected data after FDT END")
            ended = True
            break
        else:
            raise InputValidationError(f"unknown FDT token {token}")
    _require(ended, "FDT structure has no END token")
    command_line = None
    if chosen is not None:
        _require(chosen.endswith(b"\0"), "FDT bootargs is not NUL terminated")
        text, remainder = chosen.split(b"\0", 1)
        _require(not any(remainder), "FDT bootargs contains multiple strings")
        command_line = _command_line(text, "FDT /chosen/bootargs")
    return {"version": version, "last_compatible_version": compatible,
            "totalsize": total, "file_size": len(raw),
            "trailing_bytes_preserved": len(raw) - total, "boot_cpu_id": boot_cpu,
            "node_count": len(paths), "bootargs_present": chosen is not None,
            "bootargs": command_line,
            "bootargs_property_sha256": hashlib.sha256(chosen).hexdigest()
            if chosen is not None else None,
            "structure_ranges_validated": True,
            "format_compliant": not nonzero_padding,
            "format_validation_scope": "v17 blocks, tokens, boundaries, uniqueness and structure padding; not board schema",
            "nonzero_padding_regions": nonzero_padding,
            "nonzero_padding_bytes": sum(item["nonzero_bytes"] for item in nonzero_padding),
            "padding_nonzero_tolerated_for_diagnostics": bool(nonzero_padding),
            "complete_board_coverage_verified": False}


def inspect_full_dtb(path: os.PathLike[str] | str) -> dict[str, Any]:
    path, raw = _read_bounded(path, MAX_FDT_BYTES)
    result = _inspect_fdt(raw)
    result["source"] = {"path": str(path), "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()}
    return result


def _raw_luns(records: Any, base: Path, verify_hashes: bool,
              *, absolute_required: bool) -> list[dict[str, Any]]:
    _require(isinstance(records, list), "luns must be a list")
    seen: set[int] = set()
    result = []
    for entry in records:
        _require(isinstance(entry, dict), "each LUN must be an object")
        number = _integer(entry.get("lun"), "lun")
        _require(number < 32 and number not in seen, "duplicate or unsupported LUN number")
        seen.add(number)
        _require(entry.get("format", "raw") == "raw", "only raw original LU images are supported")
        _require(entry.get("readonly_original", True) is True,
                 "original LU image must be treated as read-only")
        value = entry.get("path")
        _require(isinstance(value, str) and bool(value), "LUN path is required")
        path = Path(value)
        _require(not absolute_required or path.is_absolute(), "prepared LUN paths must be absolute")
        path, info = _regular(path if path.is_absolute() else base / path)
        block_size = _integer(entry.get("logical_block_size"), "logical_block_size", 1)
        count = _integer(entry.get("logical_block_count"), "logical_block_count", 1)
        _require(block_size == 4096, "this TB371FC UFS model requires 4096-byte logical blocks")
        _require(info.st_size == count * block_size,
                 f"LUN {number} length does not match declared block geometry")
        if "size" in entry:
            _require(_integer(entry["size"], "LUN size", 1) == info.st_size,
                     "LUN size does not match the supplied file")
        descriptor = entry.get("descriptor")
        if descriptor is not None:
            _require(isinstance(descriptor, dict), "LUN descriptor must be an object")
            exponent = _integer(descriptor.get("logical_block_size_exponent"),
                                "descriptor logical_block_size_exponent", 1)
            described_count = _integer(descriptor.get("logical_block_count"),
                                       "descriptor logical_block_count", 1)
            _require(exponent == 12 and described_count == count,
                     "LUN descriptor contradicts manifest geometry")
        with path.open("rb") as source:
            magic = source.read(4)
        _require(magic not in (b"QFI\xfb", b"\x3a\xff\x26\xed"),
                 "LUN is qcow2 or Android sparse data, not a raw disk")
        item = {"lun": number, "path": str(path), "format": "raw",
                "logical_block_size": block_size, "logical_block_count": count,
                "size": info.st_size, "readonly_original": True,
                "sha256_verified": False, "partition_layout_verified": False}
        if descriptor is not None:
            item["descriptor"] = descriptor
        if entry.get("sha256") is not None:
            item["sha256"] = _sha(entry["sha256"], "LUN sha256")
            if verify_hashes:
                _require(sha256_file(path) == item["sha256"], f"LUN {number} SHA-256 mismatch")
                item["sha256_verified"] = True
        _unchanged(path, info)
        result.append(item)
    return sorted(result, key=lambda item: item["lun"])


def validate_storage_manifest(path: os.PathLike[str] | str,
                              verify_hashes: bool = False) -> dict[str, Any]:
    path, value, raw = _read_json(path)
    _require(value.get("schema") == STORAGE_SCHEMA, "unsupported Android storage manifest schema")
    slot = value.get("slot")
    _require(slot is None or (isinstance(slot, str) and 0 < len(slot) <= 128 and
                             not any(char.isspace() or ord(char) < 32 for char in slot)),
             "slot must be an explicit single token or null")
    return {"schema": STORAGE_SCHEMA, "slot": slot,
            "luns": _raw_luns(value.get("luns"), path.parent, verify_hashes,
                               absolute_required=False),
            "source": {"path": str(path), "size": len(raw),
                       "sha256": hashlib.sha256(raw).hexdigest()}}


def _copy_slice(source: Path, offset: int, size: int, destination: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    remaining = size
    with source.open("rb") as reader, destination.open("xb") as writer:
        os.chmod(destination, 0o600)
        reader.seek(offset)
        while remaining:
            data = reader.read(min(COPY_CHUNK, remaining))
            _require(bool(data), f"short read while extracting {source}")
            writer.write(data)
            digest.update(data)
            remaining -= len(data)
    return {"path": str(destination.resolve()), "size": size,
            "sha256": digest.hexdigest()}


def _write_bytes(destination: Path, raw: bytes) -> dict[str, Any]:
    with destination.open("xb") as target:
        os.chmod(destination, 0o600)
        target.write(raw)
    return {"path": str(destination.resolve()), "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest()}


def prepare_android_inputs(boot_image: os.PathLike[str] | str,
                           full_dtb: os.PathLike[str] | str,
                           output_dir: os.PathLike[str] | str, *,
                           storage_manifest: os.PathLike[str] | str | None = None,
                           bootargs_file: os.PathLike[str] | str | None = None,
                           verify_lun_hashes: bool = False) -> dict[str, Any]:
    """Write an exclusive new directory; publish manifest.json only on success.

    A failed extraction may leave a partial output directory, never a success
    manifest.  It must be inspected or a new output directory selected on retry.
    There is intentionally no fallback from a missing captured command line to
    guessed Android flags.  An explicit bootargs file may contain one final
    LF/CRLF; its input digest and that sole normalization are recorded.
    """
    boot_path, boot_stat = _regular(boot_image)
    boot = parse_boot_image(boot_path)
    dtb_path, dtb_raw = _read_bounded(full_dtb, MAX_FDT_BYTES)
    dtb_info = _inspect_fdt(dtb_raw)
    dtb_source = {"path": str(dtb_path), "size": len(dtb_raw),
                  "sha256": hashlib.sha256(dtb_raw).hexdigest()}
    storage = validate_storage_manifest(storage_manifest, verify_lun_hashes) \
        if storage_manifest is not None else None
    command = dtb_info["bootargs"]
    argument_source: dict[str, Any] = {
        "kind": "captured-dtb-chosen", "dtb": dtb_source,
        "property_sha256": dtb_info["bootargs_property_sha256"],
        "content_changed": False,
    }
    if bootargs_file is not None:
        supplied_path, supplied = _read_bounded(bootargs_file, MAX_BOOTARGS_BYTES)
        without_newline = supplied[:-2] if supplied.endswith(b"\r\n") else \
            supplied[:-1] if supplied.endswith(b"\n") else supplied
        command = _command_line(without_newline, "explicit bootargs")
        argument_source = {
            "kind": "explicit-file", "path": str(supplied_path),
            "size": len(supplied), "sha256": hashlib.sha256(supplied).hexdigest(),
            "terminal_newline_removed": supplied != without_newline,
            "captured_dtb_bootargs_present": dtb_info["bootargs_present"],
            "matches_captured_dtb_bootargs": command == dtb_info["bootargs"],
        }
    _require(command is not None and command != "",
             "captured DT has no nonempty bootargs; supply an explicit --bootargs-file")
    command_raw = command.encode("utf-8")
    _command_line(command_raw, "selected bootargs")
    _unchanged(boot_path, boot_stat)
    out = Path(output_dir).resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    artifacts: dict[str, dict[str, Any]] = {}
    filenames = {"kernel": "kernel.bin", "ramdisk": "ramdisk.bin",
                 "second": "second.bin", "recovery_dtbo": "recovery-dtbo.bin",
                 "dtb": "boot-embedded-dtb.bin"}
    for name, component in boot["components"].items():
        if component["size"]:
            artifact = _copy_slice(boot_path, component["offset"], component["size"],
                                   out / filenames[name])
            artifact["source_offset"] = component["offset"]
            artifacts[name] = artifact
    _unchanged(boot_path, boot_stat)
    boot["source"]["sha256"] = sha256_file(boot_path)
    _unchanged(boot_path, boot_stat)
    dtb = _write_bytes(out / "full.dtb", dtb_raw)
    arguments = _write_bytes(out / "bootargs.txt", command_raw)
    result = {
        "schema": SCHEMA,
        "kernel": artifacts["kernel"], "initrd": artifacts["ramdisk"],
        "dtb": dtb, "bootargs": arguments,
        "luns": storage["luns"] if storage else [],
        "metadata": {
            "slot": storage["slot"] if storage else None,
            "scope": "original-android-direct-kernel-inputs",
            "factory_provenance_verified": False, "avb_verified": False,
            "android_boot_verified": False, "partition_layout_verified": False,
            "second_stage_present_but_not_executed": bool(boot["header"]["second_size"]),
            "storage_supplied": storage is not None,
        },
        "provenance": {"boot_image": boot, "full_dtb": {**dtb_info, "source": dtb_source},
                       "bootargs": argument_source,
                       "storage_manifest": storage["source"] if storage else None},
        "additional_boot_components": {name: value for name, value in artifacts.items()
                                       if name not in ("kernel", "ramdisk")},
    }
    # No image source is opened for writing, and publication is the final step.
    with (out / "manifest.json").open("x", encoding="utf-8") as target:
        os.chmod(out / "manifest.json", 0o600)
        json.dump(result, target, indent=2, ensure_ascii=False)
        target.write("\n")
    return result


def _source_record(value: Any, name: str) -> dict[str, Any]:
    """Check recorded provenance without rereading potentially offline originals."""
    _require(isinstance(value, dict), f"{name} source record is required")
    path = value.get("path")
    _require(isinstance(path, str) and Path(path).is_absolute(),
             f"{name} source path must be absolute")
    _integer(value.get("size"), f"{name} source size", 1)
    _sha(value.get("sha256"), f"{name} source sha256")
    return value


def load_prepared_manifest(path: os.PathLike[str] | str,
                           verify_hashes: bool = True) -> dict[str, Any]:
    """Validate prepared paths, bytes and declared storage for the boot runner.

    A missing LU digest remains unknown; this function never invents one or
    interprets a matching digest as authenticated factory firmware.  Validation
    checks the current snapshot, so the caller must still keep originals
    read-only and use new overlays for all guest writes.
    """
    path, result, _ = _read_json(path)
    _require(result.get("schema") == SCHEMA, "unsupported prepared-input schema")
    for name in ("kernel", "initrd", "dtb", "bootargs"):
        item = result.get(name)
        _require(isinstance(item, dict), f"missing prepared {name} artifact")
        value = item.get("path")
        _require(isinstance(value, str) and Path(value).is_absolute(),
                 f"prepared {name} path must be absolute")
        artifact, info = _regular(value)
        size = _integer(item.get("size"), f"{name} size", 1)
        digest = _sha(item.get("sha256"), f"{name} sha256")
        _require(info.st_size == size, f"prepared {name} size changed")
        if verify_hashes:
            _require(sha256_file(artifact) == digest, f"prepared {name} SHA-256 mismatch")
        item.update(path=str(artifact), sha256=digest, sha256_verified=verify_hashes)
    dtb = inspect_full_dtb(result["dtb"]["path"])
    _, args = _read_bounded(result["bootargs"]["path"], MAX_BOOTARGS_BYTES)
    _command_line(args, "prepared bootargs")
    _require(bool(args), "prepared bootargs must not be empty")
    result["luns"] = _raw_luns(result.get("luns"), path.parent, verify_hashes,
                               absolute_required=True)
    metadata = result.get("metadata")
    _require(isinstance(metadata, dict), "prepared metadata is required")
    _require(metadata.get("scope") == "original-android-direct-kernel-inputs",
             "prepared manifest has an incompatible scope")
    # Input preparation cannot itself contain an Android success claim.
    for flag in ("android_boot_verified", "avb_verified", "factory_provenance_verified",
                 "partition_layout_verified"):
        _require(metadata.get(flag, False) is False,
                 f"input preparation cannot establish {flag}")

    provenance = result.get("provenance")
    _require(isinstance(provenance, dict), "prepared provenance is required")
    full_dtb = provenance.get("full_dtb")
    _require(isinstance(full_dtb, dict), "full DT provenance is required")
    original_dtb = _source_record(full_dtb.get("source"), "full DT")
    _require(original_dtb["size"] == result["dtb"]["size"] and
             original_dtb["sha256"].lower() == result["dtb"]["sha256"],
             "prepared DT does not preserve the recorded full DT bytes")
    _require(full_dtb.get("bootargs") == dtb["bootargs"] and
             full_dtb.get("bootargs_property_sha256") == dtb["bootargs_property_sha256"],
             "full DT bootargs provenance is inconsistent")
    for field in ("structure_ranges_validated", "format_compliant",
                  "nonzero_padding_regions", "nonzero_padding_bytes",
                  "padding_nonzero_tolerated_for_diagnostics"):
        _require(full_dtb.get(field) == dtb[field],
                 f"full DT {field} provenance is inconsistent")

    boot = provenance.get("boot_image")
    _require(isinstance(boot, dict), "boot image provenance is required")
    original_boot = _source_record(boot.get("source"), "boot image")
    header = boot.get("header")
    components = boot.get("components")
    _require(isinstance(header, dict) and isinstance(components, dict),
             "boot image header/components provenance is required")
    version = _integer(header.get("header_version"), "recorded boot header_version")
    _require(version in HEADER_SIZES and header.get("header_size") == HEADER_SIZES[version],
             "inconsistent recorded boot header version/size")
    for component_name, artifact_name in (("kernel", "kernel"), ("ramdisk", "initrd")):
        component = components.get(component_name)
        _require(isinstance(component, dict), f"missing {component_name} provenance")
        offset = _integer(component.get("offset"), f"{component_name} offset")
        size = _integer(component.get("size"), f"{component_name} size", 1)
        artifact = result[artifact_name]
        _require(offset + size <= original_boot["size"] and
                 size == artifact["size"] == header.get(f"{component_name}_size") and
                 offset == artifact.get("source_offset"),
                 f"prepared {artifact_name} contradicts its boot component provenance")

    source = provenance.get("bootargs")
    _require(isinstance(source, dict), "bootargs provenance is required")
    command = args.decode("utf-8")
    if source.get("kind") == "captured-dtb-chosen":
        source_dtb = _source_record(source.get("dtb"), "bootargs DT")
        _require(source_dtb == original_dtb and source.get("content_changed") is False and
                 source.get("property_sha256") == dtb["bootargs_property_sha256"],
                 "captured bootargs provenance is inconsistent")
        _require(dtb["bootargs"] == args.decode("utf-8"),
                 "prepared bootargs no longer matches captured /chosen/bootargs")
    elif source.get("kind") == "explicit-file":
        _source_record(source, "explicit bootargs")
        removed = source.get("terminal_newline_removed")
        _require(type(removed) is bool and
                 source["size"] - len(args) in ((1, 2) if removed else (0,)),
                 "explicit bootargs newline provenance is inconsistent")
        if not removed:
            _require(source["sha256"].lower() == result["bootargs"]["sha256"],
                     "explicit bootargs content digest is inconsistent")
        _require(source.get("captured_dtb_bootargs_present") == dtb["bootargs_present"] and
                 source.get("matches_captured_dtb_bootargs") == (command == dtb["bootargs"]),
                 "explicit bootargs DT comparison is inconsistent")
    else:
        raise InputValidationError("unsupported or missing bootargs provenance kind")
    supplied = metadata.get("storage_supplied")
    _require(type(supplied) is bool, "storage_supplied metadata is required")
    if supplied:
        _source_record(provenance.get("storage_manifest"), "storage manifest")
    else:
        _require(not result["luns"] and provenance.get("storage_manifest") is None,
                 "LUNs present without supplied storage provenance")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot-image", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True,
                        help="captured or explicitly supplied complete DTB; never generated here")
    parser.add_argument("--out", type=Path, required=True, help="exclusive new output directory")
    parser.add_argument("--storage-manifest", type=Path)
    parser.add_argument("--bootargs-file", type=Path,
                        help="explicit bootargs override; default is supplied DT /chosen/bootargs")
    parser.add_argument("--hash-luns", action="store_true",
                        help="verify any supplied LU SHA-256 digests (may read large disk images)")
    args = parser.parse_args(argv)
    try:
        prepared = prepare_android_inputs(
            args.boot_image, args.dtb, args.out, storage_manifest=args.storage_manifest,
            bootargs_file=args.bootargs_file, verify_lun_hashes=args.hash_luns)
    except (InputValidationError, OSError) as error:
        parser.exit(2, f"android input preparation failed: {error}\n")
    # Keep raw bootargs and private source metadata out of routine terminal output.
    print(json.dumps({"manifest": str(args.out.resolve() / "manifest.json"),
                      "header_version": prepared["provenance"]["boot_image"]["header"]["header_version"],
                      "lun_count": len(prepared["luns"]),
                      "scope": prepared["metadata"]["scope"],
                      "android_boot_verified": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
