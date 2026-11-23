#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Synthetic format vectors; no private firmware or filesystem payload reads."""
import importlib.util
import io
import json
from pathlib import Path
import stat
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(__file__).with_name("inspect_android_boot_header.py")
spec = importlib.util.spec_from_file_location("android_boot_geometry", MODULE)
parser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(parser)


def put32(data, offset, value):
    struct.pack_into("<I", data, offset, value)


def legacy(version=0):
    data = bytearray((1632, 1648, 1660)[version])
    data[:8] = b"ANDROID!"
    put32(data, 8, 2051)
    put32(data, 16, 501)
    put32(data, 24, 17)
    put32(data, 36, 2048)
    put32(data, 40, version)
    if version:
        put32(data, 1632, 11)
        struct.pack_into("<Q", data, 1636, 10240)
        put32(data, 1644, (1648, 1660)[version - 1])
    if version == 2:
        put32(data, 1648, 1)
        struct.pack_into("<Q", data, 1652, 0x100000000)
    return data


def modern(version=3):
    data = bytearray(1580 if version == 3 else 1584)
    data[:8] = b"ANDROID!"
    put32(data, 8, 5000 if version == 3 else 0)
    put32(data, 12, 31 if version == 3 else 300)
    put32(data, 20, len(data))
    put32(data, 40, version)
    if version == 4:
        put32(data, 1580, 32)
    return data


def vendor(version=3):
    data = bytearray(2112 if version == 3 else 2128)
    data[:8] = b"VNDRBOOT"
    put32(data, 8, version)
    put32(data, 12, 2048)
    put32(data, 24, 3)
    put32(data, 2096, len(data))
    put32(data, 2100, 4)
    if version == 4:
        put32(data, 2112, 216)
        put32(data, 2116, 2)
        put32(data, 2120, 108)
        put32(data, 2124, 5)
    return data


class BootHeaderGeometryTests(unittest.TestCase):
    def inspect(self, data, size):
        return parser.inspect_header(bytes(data), size)

    def rows(self, result):
        return [(v["name"], v["offset"], v["bytes"],
                 v["end_exclusive"], v["padded_end_exclusive"])
                for v in result["sections"]]

    def test_legacy_zero_one_two_have_exact_page_geometry(self):
        base = [("kernel", 2048, 2051, 4099, 6144),
                ("ramdisk", 6144, 501, 6645, 8192),
                ("second", 8192, 17, 8209, 10240)]
        for version, size, suffix in (
            (0, 12288, []),
            (1, 12288, [("recovery_dtbo", 10240, 11, 10251, 12288)]),
            (2, 14336, [("recovery_dtbo", 10240, 11, 10251, 12288),
                        ("dtb", 12288, 1, 12289, 14336)]),
        ):
            with self.subTest(version=version):
                result = self.inspect(legacy(version), size)
                self.assertEqual(self.rows(result), base + suffix)
                self.assertEqual(result["header_version"], version)
                self.assertEqual(result["trailing_bytes_not_decoded"],
                                 2048 if version == 0 else 0)
                self.assertFalse(result["android_boot_passed"])
                if version == 2:
                    self.assertEqual(
                        result["header_load_addresses_not_validated"]["dtb"],
                        0x100000000)

    def test_modern_boot_and_init_boot_do_not_require_nonzero_kernel(self):
        result = self.inspect(modern(3), 16384)
        self.assertEqual(self.rows(result), [
            ("kernel", 4096, 5000, 9096, 12288),
            ("ramdisk", 12288, 31, 12319, 16384)])
        result = self.inspect(modern(4), 12288)
        self.assertEqual(self.rows(result), [
            ("kernel", 4096, 0, 4096, 4096),
            ("ramdisk", 4096, 300, 4396, 8192),
            ("boot_signature", 8192, 32, 8224, 12288)])
        self.assertFalse(result["sections"][0]["present"])
        self.assertFalse(result["signature_verified"])

    def test_vendor_header_can_span_two_pages_and_v4_table_is_separate(self):
        result = self.inspect(vendor(3), 8192)
        self.assertEqual(self.rows(result), [
            ("vendor_ramdisk", 4096, 3, 4099, 6144),
            ("dtb", 6144, 4, 6148, 8192)])
        result = self.inspect(vendor(4), 12288)
        self.assertEqual(self.rows(result), [
            ("vendor_ramdisk", 4096, 3, 4099, 6144),
            ("dtb", 6144, 4, 6148, 8192),
            ("vendor_ramdisk_table", 8192, 216, 8408, 10240),
            ("bootconfig", 10240, 5, 10245, 12288)])
        self.assertEqual(result["vendor_ramdisk_table_entries"], 2)
        self.assertFalse(result["vendor_ramdisk_entries_decoded"])

    def test_private_text_and_image_id_are_never_returned(self):
        for data, size, slots in (
            (legacy(), 12288, [(48, b"PRIVATE_PRODUCT"),
                               (64, b"PRIVATE_CMDLINE"),
                               (576, b"PRIVATE_IMAGE_ID"),
                               (608, b"PRIVATE_EXTRA")]),
            (modern(3), 16384, [(44, b"PRIVATE_MODERN_CMD")]),
            (vendor(4), 12288, [(28, b"PRIVATE_VENDOR_CMD"),
                               (2080, b"PRIVATE_VENDOR")]),
        ):
            for offset, secret in slots:
                data[offset:offset + len(secret)] = secret
            result = self.inspect(data, size)
            text = json.dumps(result)
            self.assertNotIn("PRIVATE_", text)
            self.assertFalse(result["private_text_fields_exported"])
            self.assertFalse(result["image_id_exported"])
            self.assertFalse(result["payloads_read"])
            self.assertFalse(result["full_image_digest_computed"])
            self.assertFalse(result["avb_verified"])
            self.assertLess(len(text.encode()), 8192)

    def test_truncation_bad_sizes_and_unknown_versions_are_rejected(self):
        cases = []
        data = legacy()
        data[:8] = b"NOTBOOT!"
        cases.append((data, 12288, "unrecognized_magic"))
        cases.append((bytes(43), 12288, "short_prefix"))
        data = legacy()
        put32(data, 40, 5)
        cases.append((data, 12288, "unsupported_boot_version"))
        data = vendor()
        put32(data, 8, 2)
        cases.append((data, 8192, "unsupported_vendor_version"))
        cases.append((legacy(2)[:-1], 14336, "short_header"))
        cases.append((modern(), 1580, "truncated_header_page"))
        cases.append((legacy(), 8209, "section_or_padding_outside_image"))
        cases.append((legacy(), True, "image_size_outside_contract"))
        cases.append((legacy(), 0, "image_size_outside_contract"))
        cases.append((legacy(), (1 << 30) + 1, "image_size_outside_contract"))
        for data, size, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(parser.HeaderError, "^" + code + "$"):
                    self.inspect(data, size)

    def test_conflicting_header_and_recovery_offsets_are_not_repaired(self):
        cases = []
        for page in (0, 1024, 4095, 32768):
            data = legacy()
            put32(data, 36, page)
            cases.append((data, 12288, "unsupported_page_size"))
        for data, at, size in ((legacy(1), 1644, 12288),
                               (modern(), 20, 16384),
                               (vendor(), 2096, 8192)):
            put32(data, at, 999)
            cases.append((data, size, "declared_header_size_mismatch"))
        data = legacy(1)
        struct.pack_into("<Q", data, 1636, 8192)
        cases.append((data, 12288, "recovery_dtbo_offset_mismatch"))
        data = modern()
        data[24] = 1
        cases.append((data, 16384, "nonzero_reserved_fields"))
        for data, size, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(parser.HeaderError, "^" + code + "$"):
                    self.inspect(data, size)

    def test_vendor_table_extent_must_agree_with_count_and_supported_stride(self):
        data = vendor(4)
        put32(data, 2120, 104)
        with self.assertRaisesRegex(parser.HeaderError,
                                    "^unsupported_vendor_table_stride$"):
            self.inspect(data, 12288)
        data = vendor(4)
        put32(data, 2116, 3)
        with self.assertRaisesRegex(parser.HeaderError,
                                    "^vendor_table_size_mismatch$"):
            self.inspect(data, 12288)
        data = vendor(4)
        put32(data, 2116, 0)
        put32(data, 2112, 0)
        result = self.inspect(data, 12288)
        self.assertFalse(result["sections"][2]["present"])
        self.assertEqual(result["sections"][3]["offset"], 8192)

    def test_file_reader_never_prefetches_payload_past_the_header(self):
        data = bytes(legacy()) + bytes(12288 - 1632)

        class CountingRaw(io.RawIOBase):
            def __init__(self):
                super().__init__()
                self.position = 0
                self.spans = []

            def readable(self):
                return True

            def seekable(self):
                return True

            def fileno(self):
                return 317

            def seek(self, offset, whence=0):
                if whence != 0:
                    raise ValueError("fixture supports absolute seek only")
                self.position = offset
                return offset

            def tell(self):
                return self.position

            def readinto(self, target):
                count = min(len(target), len(data) - self.position)
                target[:count] = data[self.position:self.position + count]
                self.spans.append((self.position, self.position + count))
                self.position += count
                return count

        # A real BufferedReader over this synthetic source demonstrates the
        # old read(44) prefetch error; it reaches well beyond the header.
        control = CountingRaw()
        with io.BufferedReader(control) as buffered:
            self.assertEqual(len(buffered.read(44)), 44)
        self.assertGreater(max(end for _, end in control.spans), 1632)

        observed = CountingRaw()

        def fdopen(_fd, _mode, buffering=-1):
            return observed if buffering == 0 else io.BufferedReader(observed)

        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600,
                               st_size=len(data), st_dev=1, st_ino=2,
                               st_mtime_ns=3, st_ctime_ns=4)
        with patch.object(parser.Path, "is_symlink", return_value=False), \
                patch.object(parser.os, "open", return_value=317), \
                patch.object(parser.os, "fdopen", side_effect=fdopen), \
                patch.object(parser.os, "fstat", return_value=info):
            result = parser.inspect_file("/synthetic/boot.img")
        self.assertTrue(result["header_reread_equal"])
        self.assertFalse(result["payloads_read"])
        self.assertEqual(sum(end - start for start, end in observed.spans), 3264)
        self.assertTrue(all(0 <= start <= end <= 1632
                            for start, end in observed.spans))

    def test_empty_recovery_file_preserves_both_producer_offset_forms(self):
        for version, size in ((1, 10240), (2, 12288)):
            for offset in (0, 10240):
                with self.subTest(version=version, offset=offset):
                    data = legacy(version)
                    put32(data, 1632, 0)
                    struct.pack_into("<Q", data, 1636, offset)
                    result = self.inspect(data, size)
                    recovery = result["sections"][3]
                    self.assertFalse(recovery["present"])
                    self.assertEqual(recovery["offset"], 10240)
                    self.assertEqual(recovery["end_exclusive"], 10240)
                    self.assertEqual(
                        result["recovery_dtbo_declared_offset"], offset)
                    if version == 2:
                        self.assertEqual(result["sections"][4]["offset"], 10240)
            data = legacy(version)
            put32(data, 1632, 0)
            struct.pack_into("<Q", data, 1636, 8192)
            with self.assertRaisesRegex(parser.HeaderError,
                                        "^recovery_dtbo_offset_mismatch$"):
                self.inspect(data, size)


if __name__ == "__main__":
    unittest.main(verbosity=2)
