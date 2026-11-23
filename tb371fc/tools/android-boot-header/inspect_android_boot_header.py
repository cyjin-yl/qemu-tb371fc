#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inspect Android boot-header geometry without exporting private text fields.

Format references: AOSP system/tools/mkbootimg, mkbootimg.py at
41681b26562d133877d2e9d856685ee6c1d837d2, and generated layout checks in
rust/bootimg_priv.rs at 1628ef64f6527293bbd4f4b4c1e44f2dc3534db3.
This module does not extract payloads or verify AVB, signatures, or bootability.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct

MAX_IMAGE_BYTES = 1 << 30
PAGE_SIZES = (2048, 4096, 8192, 16384)
BOOT_HEADER_SIZES = (1632, 1648, 1660, 1580, 1584)
VENDOR_HEADER_SIZES = {3: 2112, 4: 2128}


class HeaderError(ValueError):
    """A fixed error code; no raw private values are interpolated."""


def require(condition, code):
    if not condition:
        raise HeaderError(code)


def u32(data, offset):
    require(offset >= 0 and offset + 4 <= len(data), "short_header")
    return struct.unpack_from("<I", data, offset)[0]


def u64(data, offset):
    require(offset >= 0 and offset + 8 <= len(data), "short_header")
    return struct.unpack_from("<Q", data, offset)[0]


def header_identity(prefix):
    require(isinstance(prefix, bytes) and len(prefix) >= 44, "short_prefix")
    if prefix[:8] == b"ANDROID!":
        version = u32(prefix, 40)
        require(version < len(BOOT_HEADER_SIZES), "unsupported_boot_version")
        return "boot", version, BOOT_HEADER_SIZES[version]
    if prefix[:8] == b"VNDRBOOT":
        version = u32(prefix, 8)
        require(version in VENDOR_HEADER_SIZES, "unsupported_vendor_version")
        return "vendor_boot", version, VENDOR_HEADER_SIZES[version]
    raise HeaderError("unrecognized_magic")


def inspect_header(data, image_bytes):
    """Return only fixed numeric geometry from a supplied header byte string."""
    require(type(image_bytes) is int and 0 < image_bytes <= MAX_IMAGE_BYTES,
            "image_size_outside_contract")
    kind, version, header_size = header_identity(data)
    require(len(data) >= header_size and image_bytes >= header_size,
            "short_header")
    # Deliberately ignore product name, command lines, IDs and other text.
    sections = []
    load_addresses = {}
    extra = {}
    if kind == "boot":
        if version <= 2:
            page = u32(data, 36)
            require(page in PAGE_SIZES and header_size <= page,
                    "unsupported_page_size")
            if version:
                require(u32(data, 1644) == header_size,
                        "declared_header_size_mismatch")
            sizes = [("kernel", u32(data, 8)),
                     ("ramdisk", u32(data, 16)),
                     ("second", u32(data, 24))]
            load_addresses = {"kernel": u32(data, 12),
                              "ramdisk": u32(data, 20),
                              "second": u32(data, 28),
                              "tags": u32(data, 32)}
            if version:
                recovery_size = u32(data, 1632)
                recovery_offset = u64(data, 1636)
                sizes.append(("recovery_dtbo", recovery_size))
                extra["recovery_dtbo_declared_offset"] = recovery_offset
            if version == 2:
                sizes.append(("dtb", u32(data, 1648)))
                load_addresses["dtb"] = u64(data, 1652)
        else:
            page = 4096
            require(u32(data, 20) == header_size,
                    "declared_header_size_mismatch")
            require(data[24:40] == bytes(16), "nonzero_reserved_fields")
            sizes = [("kernel", u32(data, 8)),
                     ("ramdisk", u32(data, 12))]
            if version == 4:
                sizes.append(("boot_signature", u32(data, 1580)))
            extra["separate_vendor_boot_format"] = True
    else:
        page = u32(data, 12)
        require(page in PAGE_SIZES, "unsupported_page_size")
        require(u32(data, 2096) == header_size,
                "declared_header_size_mismatch")
        sizes = [("vendor_ramdisk", u32(data, 24)),
                 ("dtb", u32(data, 2100))]
        load_addresses = {"kernel": u32(data, 16),
                          "ramdisk": u32(data, 20),
                          "tags": u32(data, 2076),
                          "dtb": u64(data, 2104)}
        if version == 4:
            table_size = u32(data, 2112)
            entries = u32(data, 2116)
            entry_size = u32(data, 2120)
            require(entry_size == 108, "unsupported_vendor_table_stride")
            require(table_size == entries * entry_size,
                    "vendor_table_size_mismatch")
            sizes += [("vendor_ramdisk_table", table_size),
                      ("bootconfig", u32(data, 2124))]
            extra["vendor_ramdisk_table_entries"] = entries
            extra["vendor_ramdisk_table_entry_size"] = entry_size
            extra["vendor_ramdisk_entries_decoded"] = False

    def aligned(size):
        return (size + page - 1) // page * page

    cursor = aligned(header_size)
    require(cursor <= image_bytes, "truncated_header_page")
    for name, size in sizes:
        if name == "recovery_dtbo":
            # A supplied empty recovery file still has a producer offset.
            allowed_offsets = (cursor,) if size else (0, cursor)
            require(extra["recovery_dtbo_declared_offset"] in allowed_offsets,
                    "recovery_dtbo_offset_mismatch")
        end = cursor + size
        padded_end = cursor + aligned(size)
        require(end <= image_bytes and padded_end <= image_bytes,
                "section_or_padding_outside_image")
        sections.append({"name": name, "offset": cursor, "bytes": size,
                         "present": size != 0, "end_exclusive": end,
                         "padded_end_exclusive": padded_end})
        cursor = padded_end
    result = {
        "schema": "tb371fc-android-boot-header-geometry-v1",
        "header_geometry_valid": True, "image_kind": kind,
        "header_version": version, "header_bytes": header_size,
        "header_sha256": hashlib.sha256(data[:header_size]).hexdigest(),
        "page_size": page, "image_bytes": image_bytes,
        "sections": sections, "layout_end_exclusive": cursor,
        "trailing_bytes_not_decoded": image_bytes - cursor,
        "header_load_addresses_not_validated": load_addresses,
        "payloads_read": False, "payload_contents_verified": False,
        "private_text_fields_exported": False, "image_id_exported": False,
        "full_image_digest_computed": False,
        "avb_verified": False, "signature_verified": False,
        "android_boot_passed": False,
    }
    result.update(extra)
    require(len(json.dumps(result).encode("utf-8")) < 8192,
            "metadata_output_limit")
    return result


def inspect_file(path):
    """Read only a regular file's header twice; do not follow symlinks."""
    path = Path(path)
    for part in (path, *path.parents):
        require(not part.is_symlink(), "symlink_path")
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW |
                 os.O_NONBLOCK)
    with os.fdopen(fd, "rb", buffering=0) as stream:
        before = os.fstat(stream.fileno())
        require(stat.S_ISREG(before.st_mode), "not_regular_file")
        require(0 < before.st_size <= MAX_IMAGE_BYTES,
                "image_size_outside_contract")
        prefix = stream.read(44)
        _, _, count = header_identity(prefix)
        header = prefix + stream.read(count - len(prefix))
        result = inspect_header(header, before.st_size)
        stream.seek(0)
        require(stream.read(count) == header, "header_changed_during_read")
        after = os.fstat(stream.fileno())
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        require(all(getattr(before, name) == getattr(after, name)
                    for name in fields), "file_metadata_changed_during_read")
        result["header_reread_equal"] = True
        result["file_metadata_unchanged_during_read"] = True
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = inspect_file(args.image)
    raw = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    require(len(raw) <= 8192, "metadata_output_limit")
    os.umask(0o077)
    with args.output.open("xb") as stream:
        stream.write(raw)
    print(json.dumps({"header_geometry_valid": True, "bytes": len(raw),
                      "result_sha256": hashlib.sha256(raw).hexdigest(),
                      "android_boot_passed": False}))


if __name__ == "__main__":
    main()
