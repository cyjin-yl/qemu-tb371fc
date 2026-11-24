#!/usr/bin/env python3
"""DRAFT: bounded, metadata-only inspection of one already recorded boot image.

No extraction, subprocess, network, mounting, dependency installation, guest
execution, device access, or writes to inputs. Only a fresh summary.json is
written. This collector has not been invoked against the recorded inputs.
"""
import sys
sys.dont_write_bytecode = True

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct

INPUT_DIR = Path("/work/device-assets/android-startup-inputs-20261123-01")
BOOT_BYTES = 100663296
BOOT_SHA = "78ba0f4f75bec85a80155a9e9ea8fa19546bbd88598a7923a16b069404899a9a"
GEOMETRY_BYTES = 1824
GEOMETRY_SHA = "b7083522c0de0b2bfc73745a69f736be80b3924ab2435623f74fa3d994815fca"
RAMDISK_OFFSET, RAMDISK_BYTES = 37453824, 1298323
MAX_OUTPUT = 32 * 1024 * 1024
MAX_MEMBERS, MAX_ENTRIES, MAX_NAME, MAX_METADATA = 64, 65536, 4096, 2048
INIT_PATHS = {"init", "first_stage_ramdisk/init"}
SAFE_INIT_TARGETS = {
    "/init", "/first_stage_ramdisk/init", "/sbin/init", "/system/bin/init",
    "/system_ext/bin/init", "/vendor/bin/init", "/odm/bin/init", "/product/bin/init",
}
SAFE_INTERPRETERS = {
    "/system/bin/linker", "/system/bin/linker64",
    "/apex/com.android.runtime/bin/linker", "/apex/com.android.runtime/bin/linker64",
    "/lib/ld-linux.so.2", "/lib/ld-linux-armhf.so.3", "/lib/ld-linux-aarch64.so.1",
    "/lib64/ld-linux-aarch64.so.1", "/lib64/ld-linux-x86-64.so.2",
}


class Inconclusive(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Inconclusive(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_pinned(path, expected_bytes, expected_sha, capture_start, capture_bytes, evidence):
    """Hash the complete regular file while retaining only the approved slice."""
    evidence.update({"path": str(path), "expected_bytes": expected_bytes,
                     "expected_sha256": expected_sha, "opened_read_only": False})
    first = os.stat(path, follow_symlinks=False)
    require(stat.S_ISREG(first.st_mode), "input_is_not_a_regular_nonsymlink_file")
    require(first.st_size == expected_bytes, "input_size_mismatch")
    require(0 <= capture_start <= expected_bytes and
            0 <= capture_bytes <= expected_bytes - capture_start, "invalid_fixed_capture_range")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    evidence["opened_read_only"] = True
    chunks, total, hashed = [], 0, hashlib.sha256()
    with os.fdopen(descriptor, "rb") as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode) and
                (before.st_dev, before.st_ino, before.st_size) ==
                (first.st_dev, first.st_ino, expected_bytes), "input_changed_before_read")
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            require(total + len(block) <= expected_bytes, "input_grew_while_reading")
            hashed.update(block)
            left, right = max(total, capture_start), min(total + len(block), capture_start + capture_bytes)
            if left < right:
                chunks.append(block[left - total:right - total])
            total += len(block)
        after = os.fstat(stream.fileno())
    evidence.update({"actual_bytes": total, "actual_sha256": hashed.hexdigest(),
                     "stat_stable_during_read":
                     (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                     (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)})
    evidence["sha256_matches_record"] = evidence["actual_sha256"] == expected_sha
    require(total == expected_bytes and evidence["stat_stable_during_read"], "input_changed_during_read")
    require(evidence["sha256_matches_record"], "input_sha256_mismatch")
    retained = b"".join(chunks)
    require(len(retained) == capture_bytes, "captured_range_length_mismatch")
    return retained


def expand_ramdisk(raw, report):
    """Recognize raw newc or gzip members; never request unbounded inflation."""
    report.update({"complete": False, "total_output_limit_bytes": MAX_OUTPUT,
                   "member_limit": MAX_MEMBERS, "members": [], "padding_bytes": 0})
    leading = len(raw) - len(raw.lstrip(b"\0"))
    if raw[leading:leading + 6] in (b"070701", b"070702"):
        require(len(raw) <= MAX_OUTPUT, "raw_cpio_exceeds_output_limit")
        report.update({"compression": "none-recognized-newc", "complete": True,
                       "total_output_bytes": len(raw), "concatenated_output_sha256": digest(raw)})
        return [raw]
    require(raw[leading:leading + 2] == b"\x1f\x8b", "unknown_ramdisk_compression_or_format")
    report["compression"] = "gzip"
    try:
        import zlib
    except ImportError:
        raise Inconclusive("stdlib_zlib_unavailable_no_dependency_installation") from None
    remaining, outputs, total, output_hash = raw, [], 0, hashlib.sha256()
    while remaining:
        padding = len(remaining) - len(remaining.lstrip(b"\0"))
        report["padding_bytes"] += padding
        remaining = remaining[padding:]
        if not remaining:
            break
        require(remaining.startswith(b"\x1f\x8b"), "unsupported_non_gzip_trailing_member")
        require(len(outputs) < MAX_MEMBERS, "gzip_member_limit_reached")
        room = MAX_OUTPUT - total
        require(room > 0, "gzip_output_limit_reached_before_next_member")
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        try:
            decoded = inflater.decompress(remaining, room)
        except zlib.error:
            raise Inconclusive("gzip_invalid_or_checksum_mismatch") from None
        # max_length never exceeds the remaining 32 MiB budget. No flush()
        # or max_length=0 call may allocate an unbounded decompressed result.
        total += len(decoded)
        report["observed_output_bytes"] = total
        require(total <= MAX_OUTPUT, "gzip_output_limit_invariant")
        if not inflater.eof:
            reason = ("gzip_output_limit_reached_before_eof" if len(decoded) == room
                      else "gzip_member_incomplete")
            raise Inconclusive(reason)
        consumed = len(remaining) - len(inflater.unused_data)
        require(consumed > 0, "gzip_made_no_progress")
        outputs.append(decoded)
        output_hash.update(decoded)
        report["members"].append({"index": len(outputs) - 1, "compressed_bytes": consumed,
                                   "decompressed_bytes": len(decoded), "gzip_eof_and_checksum_verified": True})
        remaining = inflater.unused_data
    require(outputs, "no_gzip_member")
    report.update({"complete": True, "total_output_bytes": total,
                   "concatenated_output_sha256": output_hash.hexdigest()})
    return outputs


def canonical_name(raw):
    try:
        name = raw.decode("utf-8")
    except UnicodeError:
        return None
    while name.startswith("./"):
        name = name[2:]
    if not name or name == "." or name.startswith("/"):
        return None
    if any(part in ("", ".", "..") for part in name.split("/")):
        return None
    if re.fullmatch(r"[A-Za-z0-9_./+@=-]+", name) is None:
        return None
    return name


def allow_metadata(name):
    if name in INIT_PATHS:
        return True
    parent, _, leaf = name.rpartition("/")
    locations = ("first_stage_ramdisk", "system/etc", "system_ext/etc",
                 "vendor/etc", "odm/etc", "product/etc")
    permitted = not parent or any(parent == p or parent.startswith(p + "/") for p in locations)
    return permitted and (leaf == "fstab" or leaf.startswith("fstab.") or
                          leaf == "prop" or leaf.endswith(".prop") or
                          (leaf.startswith("init") and leaf.endswith(".rc")))


def system_path(raw, allowed, relative=False):
    try:
        value = bytes(raw).decode("utf-8")
    except UnicodeError:
        return None
    candidate = value if value.startswith("/") else "/" + value
    return value if (value.startswith("/") or relative) and candidate in allowed else None


def inspect_init(data, kind, link_count):
    result = {"type": kind, "bytes": len(data), "raw_member_only": True,
              "filesystem_hardlinks_or_overrides_resolved": False,
              "executed": False, "android_boot_proven": False}
    if kind == "symlink":
        target = system_path(data, SAFE_INIT_TARGETS, relative=True)
        result.update({"target_disclosed": target is not None,
                       "target_policy": "exact non-sensitive system init path allowlist"})
        if target is not None:
            result["target"] = target
        else:
            result["status"] = "inconclusive_target_outside_disclosure_allowlist"
        return result
    if kind != "regular":
        result["status"] = "not_a_regular_file_or_symlink"
        return result
    result["raw_member_sha256"] = hashlib.sha256(data).hexdigest()
    if not data and link_count > 1:
        result["status"] = "inconclusive_empty_hardlink_member_not_resolved"
        return result
    if bytes(data[:4]) != b"\x7fELF":
        result["status"] = "not_elf_no_content_disclosed"
        return result
    result.update({"format": "ELF", "status": "inconclusive_elf_header"})
    if len(data) < 16:
        return result
    elf_class, encoding = data[4], data[5]
    result.update({"class_code": elf_class, "data_encoding_code": encoding})
    if elf_class not in (1, 2) or encoding not in (1, 2) or data[6] != 1:
        return result
    order, header_size, ph_size = ("<" if encoding == 1 else ">"), (52 if elf_class == 1 else 64), (32 if elf_class == 1 else 56)
    result.update({"class_bits": 32 if elf_class == 1 else 64,
                   "endianness": "little" if encoding == 1 else "big"})
    if len(data) < header_size:
        return result
    result["e_machine"] = struct.unpack_from(order + "H", data, 18)[0]
    if struct.unpack_from(order + "I", data, 20)[0] != 1:
        return result
    if elf_class == 1:
        phoff = struct.unpack_from(order + "I", data, 28)[0]
        ehsize, phentsize, phnum = struct.unpack_from(order + "HHH", data, 40)
    else:
        phoff = struct.unpack_from(order + "Q", data, 32)[0]
        ehsize, phentsize, phnum = struct.unpack_from(order + "HHH", data, 52)
    result.update({"program_header_count": phnum, "pt_interp_present": None})
    if ehsize != header_size or phnum == 0xffff or phnum > 512:
        result["status"] = "inconclusive_unsupported_elf_header_geometry"
        return result
    if phnum and (phentsize < ph_size or phoff < header_size or phoff + phentsize * phnum > len(data)):
        result["status"] = "inconclusive_elf_program_headers_out_of_range"
        return result
    interpreters = []
    for index in range(phnum):
        at = phoff + index * phentsize
        if struct.unpack_from(order + "I", data, at)[0] != 3:
            continue
        if elf_class == 1:
            offset = struct.unpack_from(order + "I", data, at + 4)[0]
            size = struct.unpack_from(order + "I", data, at + 16)[0]
        else:
            offset = struct.unpack_from(order + "Q", data, at + 8)[0]
            size = struct.unpack_from(order + "Q", data, at + 32)[0]
        record = {"program_header_index": index, "bytes": size, "path_disclosed": False}
        if not (1 <= size <= MAX_NAME and offset <= len(data) and size <= len(data) - offset):
            record["status"] = "inconclusive_interp_bounds"
        else:
            raw = bytes(data[offset:offset + size])
            if not raw.endswith(b"\0") or b"\0" in raw[:-1]:
                record["status"] = "inconclusive_interp_string"
            else:
                safe = system_path(raw[:-1], SAFE_INTERPRETERS)
                record["path_disclosed"] = safe is not None
                record["status"] = "recognized_system_interpreter" if safe else "path_outside_disclosure_allowlist"
                if safe is not None:
                    record["path"] = safe
        interpreters.append(record)
    result.update({"status": "elf_header_and_program_headers_inspected",
                   "pt_interp_present": bool(interpreters), "pt_interp": interpreters,
                   "interpreter_target_presence_checked": False,
                   "elf_executability_or_loader_success_proven": False})
    if len(interpreters) > 1:
        result["status"] = "inconclusive_multiple_pt_interp"
    return result


def parse_newc(outputs, report):
    report.update({"format": "newc-or-crc", "complete": False, "entry_count": 0,
                   "header_count_including_trailers": 0, "trailer_count": 0,
                   "trailer_is_optional": True, "type_counts": {}, "allowlisted_entries": [],
                   "init_members": [], "omitted_entry_paths": 0,
                   "filesystem_materialized": False, "hardlinks_and_duplicate_paths_resolved": False})
    kinds = {stat.S_IFREG: "regular", stat.S_IFDIR: "directory", stat.S_IFLNK: "symlink",
             stat.S_IFCHR: "char-device-metadata-only", stat.S_IFBLK: "block-device-metadata-only",
             stat.S_IFIFO: "fifo", stat.S_IFSOCK: "socket"}
    for segment, payload in enumerate(outputs):
        offset = 0
        while offset < len(payload):
            while offset < len(payload) and payload[offset] == 0:
                offset += 1
            if offset == len(payload):
                break
            require(offset % 4 == 0, "cpio_header_not_four_byte_aligned")
            require(offset + 110 <= len(payload), "cpio_header_truncated")
            header = payload[offset:offset + 110]
            require(header[:6] in (b"070701", b"070702"), "unknown_cpio_header_format")
            require(re.fullmatch(b"[0-9A-Fa-f]{104}", header[6:]) is not None, "cpio_nonhex_header")
            values = [int(header[6 + i * 8:14 + i * 8], 16) for i in range(13)]
            mode, links, size, name_size, checksum = values[1], values[4], values[6], values[11], values[12]
            require(1 <= name_size <= MAX_NAME, "cpio_name_size_out_of_bounds")
            name_end = offset + 110 + name_size
            start, end = (name_end + 3) & ~3, ((name_end + 3) & ~3) + size
            require(name_end <= len(payload) and start <= len(payload) and end <= len(payload), "cpio_record_truncated")
            raw_name = payload[offset + 110:name_end]
            require(raw_name.endswith(b"\0"), "cpio_filename_not_nul_terminated")
            name = raw_name.rstrip(b"\0")
            require(name and b"\0" not in name, "cpio_filename_has_embedded_nul_or_is_empty")
            require(not any(payload[name_end:start]), "cpio_name_alignment_not_nul")
            body = memoryview(payload)[start:end]
            if header[:6] == b"070702":
                require((sum(body) & 0xffffffff) == checksum, "cpio_byte_sum_mismatch")
            else:
                require(checksum == 0, "newc_nonzero_checksum")
            report["header_count_including_trailers"] += 1
            require(report["header_count_including_trailers"] <= MAX_ENTRIES, "cpio_entry_limit_reached")
            if name == b"TRAILER!!!":
                require(size == 0, "cpio_trailer_has_payload")
                report["trailer_count"] += 1
            else:
                kind = kinds.get(stat.S_IFMT(mode), "unknown-mode-type")
                require(size == 0 or kind in ("regular", "symlink"), "cpio_nonfile_has_payload")
                require(kind != "symlink" or size != 0, "cpio_empty_symlink")
                report["entry_count"] += 1
                report["type_counts"][kind] = report["type_counts"].get(kind, 0) + 1
                path = canonical_name(name)
                if path is not None and allow_metadata(path):
                    require(len(report["allowlisted_entries"]) < MAX_METADATA, "allowlisted_metadata_limit_reached")
                    item = {"segment": segment, "entry_number": report["entry_count"],
                            "path": path, "type": kind, "bytes": size, "link_count": links}
                    report["allowlisted_entries"].append(item)
                    if path in INIT_PATHS:
                        report["init_members"].append({**item, "inspection": inspect_init(body, kind, links)})
                else:
                    report["omitted_entry_paths"] += 1
            offset = end
    require(report["header_count_including_trailers"] > 0, "no_cpio_records")
    report["complete"] = True


def collect(boot, geometry, summary):
    summary["stage"] = "verify-recorded-geometry"
    metadata = read_pinned(geometry, GEOMETRY_BYTES, GEOMETRY_SHA, 0, GEOMETRY_BYTES,
                           summary["inputs"]["geometry"])
    try:
        json.loads(metadata)
    except (ValueError, UnicodeError):
        raise Inconclusive("recorded_geometry_is_not_json") from None
    summary["geometry_contract"] = {
        "recorded_boot_header_version": 2, "ramdisk_offset": RAMDISK_OFFSET,
        "ramdisk_bytes": RAMDISK_BYTES, "source": "approved constants bound to exact geometry record hash",
        "header_fields_independently_redecoded": False, "geometry_contents_published": False,
    }
    summary["stage"] = "stream-hash-complete-boot-and-retain-only-ramdisk"
    raw = read_pinned(boot, BOOT_BYTES, BOOT_SHA, RAMDISK_OFFSET, RAMDISK_BYTES,
                      summary["inputs"]["boot"])
    summary["ramdisk"] = {"offset": RAMDISK_OFFSET, "actual_bytes": len(raw), "actual_sha256": digest(raw),
                           "stored_to_disk": False, "contents_published": False}
    summary["stage"] = "bounded-decompression"
    summary["decompression"] = {}
    outputs = expand_ramdisk(raw, summary["decompression"])
    summary["stage"] = "newc-metadata"
    summary["cpio"] = {}
    parse_newc(outputs, summary["cpio"])
    summary.update({"status": "complete_metadata_inspection", "stage": "complete"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boot", type=Path, default=INPUT_DIR / "boot_a.img",
                        help="a regular copy with the fixed recorded size and SHA256")
    parser.add_argument("--geometry", type=Path, default=INPUT_DIR / "header-geometry.json",
                        help="the exact hash-pinned existing geometry record")
    parser.add_argument("--out", type=Path, required=True, help="fresh directory; parent must already exist")
    args = parser.parse_args()
    require(args.boot.is_absolute() and args.geometry.is_absolute() and args.out.is_absolute(), "paths_must_be_absolute")
    # Refuse an existing directory or symlink; write exactly one new JSON file.
    args.out.mkdir(mode=0o700, parents=False, exist_ok=False)
    summary = {
        "schema": "tb371fc-recorded-boot-ramdisk-metadata-v1", "status": "inconclusive",
        "inputs": {"boot": {}, "geometry": {}},
        "limits": {"total_decompressed_bytes": MAX_OUTPUT, "gzip_members": MAX_MEMBERS,
                   "cpio_headers": MAX_ENTRIES, "cpio_name_bytes": MAX_NAME,
                   "published_allowlisted_entries": MAX_METADATA},
        "scope": {
            "input_files_opened_read_only": True, "android_system_or_userdata_inputs_read": False,
            "physical_device_access": False, "input_modified": False,
            "image_or_ramdisk_copy_written": False, "cpio_extracted": False,
            "file_payloads_published": False, "dependencies_installed": False,
            "only_allowlisted_system_path_strings_may_be_disclosed": True,
            "guest_or_elf_executed": False, "mount_or_network_or_subprocess_used": False,
            "avb_or_signature_authenticated": False, "android_boot_proven": False,
            "effective_filesystem_init_selected": False,
            "complete_means_metadata_only": True,
        },
        "format_scope": "Raw newc/crc or gzip members containing newc/crc; mixed raw/compressed layouts and other algorithms are inconclusive.",
        "path_disclosure_policy": "Only init, first_stage_ramdisk/init and startup fstab/prop/init*.rc metadata under the declared system-prefix allowlist; no contents.",
        "primary_format_references": [
            "https://github.com/torvalds/linux/blob/v6.12/Documentation/driver-api/early-userspace/buffer-format.rst",
            "https://github.com/torvalds/linux/blob/v6.12/include/uapi/linux/elf.h",
            "https://docs.python.org/3/library/zlib.html#zlib.Decompress.decompress",
        ],
    }
    try:
        collect(args.boot, args.geometry, summary)
    except Inconclusive as error:
        summary["reason"] = str(error)
    except OSError as error:
        summary.update({"reason": "input_or_output_io_error", "errno": error.errno})
    except Exception as error:
        # Do not disclose archive strings or file contents in exception text.
        summary.update({"reason": "collector_internal_error", "exception_type": type(error).__name__})
    target = args.out / "summary.json"
    with target.open("x", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    print(json.dumps({"summary": str(target), "status": summary["status"]}))
    return 0 if summary["status"] == "complete_metadata_inspection" else 2


if __name__ == "__main__":
    raise SystemExit(main())
