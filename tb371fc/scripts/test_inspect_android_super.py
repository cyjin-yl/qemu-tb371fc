#!/usr/bin/env python3
"""Independent synthetic LP wire fixtures; no Android runtime is simulated."""
# SPDX-License-Identifier: GPL-2.0-or-later
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name("inspect_android_super.py")
SPEC = importlib.util.spec_from_file_location("inspect_android_super", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PARTITION_SIZE = 16 * 1024 * 1024
MAXIMUM = 4096
SLOTS = 3
PRIMARY = 12288
BACKUP = PRIMARY + MAXIMUM * SLOTS


def fixed_name(name):
    value = name.encode("ascii") if isinstance(name, str) else name
    return value.ljust(36, b"\0")


def fixture(*, partitions=None, extents=None, groups=None, devices=None,
            minor=2, flags=1):
    if partitions is None:
        partitions = [("system_a", 1, 0, 1, 1), ("vendor_a", 1, 1, 1, 1),
                      ("system_b", 0, 2, 0, 1)]
    if extents is None:
        extents = [(8, 0, 2048, 0), (8, 0, 2056, 0)]
    if groups is None:
        groups = [("default", 0, 0), ("main", 0, 8 * 1024 * 1024)]
    if devices is None:
        devices = [(2048, 1048576, 0, PARTITION_SIZE, "super", 0)]
    blocks = [b"".join(struct.pack("<36sIIII", fixed_name(n), a, f, c, g)
                        for n, a, f, c, g in partitions),
              b"".join(struct.pack("<QIQI", *e) for e in extents),
              b"".join(struct.pack("<36sIQ", fixed_name(n), f, maximum)
                        for n, f, maximum in groups),
              b"".join(struct.pack("<QIIQ36sI", first, align, off, size, fixed_name(n), f)
                        for first, align, off, size, n, f in devices)]
    tables = b"".join(blocks)
    header_size = 256 if minor >= 2 else 128
    header = bytearray(header_size)
    struct.pack_into("<IHHI", header, 0, 0x414c5030, 10, minor, header_size)
    struct.pack_into("<I", header, 44, len(tables))
    header[48:80] = hashlib.sha256(tables).digest()
    position = 0
    for i, (data, size) in enumerate(zip(blocks, [52, 24, 48, 64])):
        struct.pack_into("<III", header, 80 + i * 12, position, len(data) // size, size)
        position += len(data)
    if minor >= 2:
        struct.pack_into("<I", header, 128, flags)
    header[12:44] = hashlib.sha256(header).digest()
    body = bytes(header) + tables
    metadata = body.ljust(MAXIMUM, b"\0")
    geometry = bytearray(52)
    struct.pack_into("<II", geometry, 0, 0x616c4467, 52)
    struct.pack_into("<III", geometry, 40, MAXIMUM, SLOTS, 4096)
    geometry[8:40] = hashlib.sha256(geometry).digest()
    geo_block = bytes(geometry).ljust(4096, b"\0")
    return bytearray(bytes(4096) + geo_block * 2 + metadata * SLOTS * 2)


def rehash_slot(raw, base=PRIMARY):
    size = struct.unpack_from("<I", raw, base + 8)[0]
    count = struct.unpack_from("<I", raw, base + 44)[0]
    raw[base + 48:base + 80] = hashlib.sha256(raw[base + size:base + size + count]).digest()
    raw[base + 12:base + 44] = bytes(32)
    raw[base + 12:base + 44] = hashlib.sha256(raw[base:base + size]).digest()


class SuperMetadataTests(unittest.TestCase):
    def inspect(self, data=None, **kw):
        return MODULE.inspect_prefix(bytes(fixture() if data is None else data),
                                     partition_size=kw.pop("partition_size", PARTITION_SIZE), **kw)

    def reject(self, data, message, **kw):
        with self.assertRaisesRegex(MODULE.MetadataError, message):
            self.inspect(data, **kw)

    def test_realistic_4k_geometry_uses_512_byte_extent_sectors(self):
        result = self.inspect()
        self.assertEqual(result["geometry"]["logical_block_size"], 4096)
        self.assertEqual(result["metadata"]["extents"][0]["physical_offset"], 1048576)
        self.assertEqual(result["metadata"]["partitions"][0]["size"], 4096)
        self.assertEqual(result["geometry_copies_verified"], 2)
        self.assertEqual(result["selected_metadata_copies_verified"], 2)
        for field in ["partition_payloads_verified", "avb_verified", "snapshot_state_verified",
                      "android_boot_verified", "other_metadata_slots_verified"]:
            self.assertIs(result[field], False)

    def test_empty_inactive_partition_exported_by_aosp_is_retained(self):
        item = self.inspect()["metadata"]["partitions"][2]
        self.assertEqual((item["name"], item["size"], item["empty"]), ("system_b", 0, True))

    def test_cow_name_has_matching_base_and_group(self):
        data = fixture(partitions=[("system_a", 5, 0, 1, 1), ("system_a-cow", 0, 1, 1, 2)],
                       groups=[("default", 0, 0), ("main", 0, 8192), ("cow", 0, 0)])
        result = self.inspect(data)["metadata"]
        self.assertTrue(result["partitions"][1]["snapshot_cow_partition"])
        self.assertTrue(result["snapshot_related_metadata_present"])
        for name, group in [("other_a-cow", 2), ("system_a-cow", 1)]:
            invalid = fixture(partitions=[("system_a", 5, 0, 1, 1), (name, 0, 1, 1, group)],
                              groups=[("default", 0, 0), ("main", 0, 8192), ("cow", 0, 0)])
            self.reject(invalid, "COW partition lacks")

    def test_unknown_name_punctuation_and_nonzero_padding_rejected(self):
        for name in ["system-other", "../system", b"system\0secret"]:
            data = fixture(partitions=[(name, 1, 0, 2, 1)])
            self.reject(data, "name")

    def test_slot_suffixed_cow_form_is_explicitly_unsupported(self):
        raw = fixture(partitions=[("system_a", 5, 0, 1, 1), ("system-cow", 2, 1, 1, 1)])
        self.reject(raw, "slot-suffixed COW names")

    def test_geometry_checksums_are_sha256_on_both_copies(self):
        for off in [4096, 8192]:
            raw = fixture(); raw[off + 40] ^= 1
            self.reject(raw, "geometry SHA-256")

    def test_valid_but_disagreeing_geometry_is_rejected(self):
        raw = fixture()
        struct.pack_into("<I", raw, 8192 + 48, 8192)
        raw[8200:8232] = bytes(32)
        raw[8200:8232] = hashlib.sha256(raw[8192:8244]).digest()
        self.reject(raw, "geometry disagree")

    def test_header_and_table_checksum_corruption_in_either_copy_rejected(self):
        for base in [PRIMARY, BACKUP]:
            for off, message in [(12, "header SHA-256"), (256, "table SHA-256")]:
                raw = fixture(); raw[base + off] ^= 1
                self.reject(raw, message)

    def test_header_table_size_cannot_escape_selected_slot(self):
        raw = fixture(); struct.pack_into("<I", raw, PRIMARY + 44, MAXIMUM)
        raw[PRIMARY + 12:PRIMARY + 44] = bytes(32)
        raw[PRIMARY + 12:PRIMARY + 44] = hashlib.sha256(raw[PRIMARY:PRIMARY + 256]).digest()
        self.reject(raw, "tables escape")

    def test_tables_must_have_correct_entry_sizes_and_no_aliases(self):
        for index, offset, count, size, message in [
                (1, 0, 2, 24, "overlap or contain gaps"),
                (3, 0, 1, 128, "entry size"),
                (1, 0xffffff00, 2, 24, "out of bounds")]:
            raw = fixture()
            struct.pack_into("<III", raw, PRIMARY + 80 + index * 12, offset, count, size)
            rehash_slot(raw)
            self.reject(raw, message)

    def test_unknown_version_attributes_and_reserved_bytes_rejected(self):
        self.reject(fixture(minor=3), "version")
        self.reject(fixture(partitions=[("system_a", 16, 0, 2, 1)]), "attributes")
        self.reject(fixture(minor=0, partitions=[("system_a", 4, 0, 2, 1)]), "attributes")
        raw = fixture(); raw[PRIMARY + 132] = 1; rehash_slot(raw)
        self.reject(raw, "reserved bytes")

    def test_older_header_versions_and_informational_flags(self):
        self.assertEqual(self.inspect(fixture(minor=0))["metadata"]["header_size"], 128)
        self.assertEqual(self.inspect(fixture(minor=1))["metadata"]["version"], "10.1")
        result = self.inspect(fixture(flags=0x80000001))
        self.assertEqual(result["metadata"]["unknown_informational_header_flags"], 0x80000000)

    def test_nonmatching_valid_backup_is_not_silently_recovered(self):
        raw = fixture()
        other = fixture(flags=0)
        raw[BACKUP:BACKUP + MAXIMUM] = other[BACKUP:BACKUP + MAXIMUM]
        self.reject(raw, "selected-slot metadata disagree")

    def test_partition_and_extent_table_ownership_bounds(self):
        for parts, message in [
                ([("system_a", 1, 0, 3, 1)], "extent range"),
                ([("system_a", 1, 0, 2, 2)], "group index"),
                ([("system_a", 1, 0, 1, 1)], "unowned extent"),
                ([("system_a", 1, 0, 2, 1), ("vendor_a", 1, 1, 1, 1)], "multiple partitions"),
                ([("system_a", 1, 0, 1, 1), ("system_a", 1, 1, 1, 1)], "duplicate partition")]:
            self.reject(fixture(partitions=parts), message)

    def test_linear_extents_are_aligned_nonoverlapping_and_in_data_area(self):
        for extents, message in [
                ([(8, 0, 0, 0), (8, 0, 2056, 0)], "data region"),
                ([(8, 0, 32768, 0), (8, 0, 2056, 0)], "data region"),
                ([(8, 0, 2049, 0), (8, 0, 2056, 0)], "data region"),
                ([(8, 0, 2048, 0), (8, 0, 2048, 0)], "overlap physically"),
                ([(8, 0, 2048, 1), (8, 0, 2056, 0)], "target device"),
                ([(1, 0, 2048, 0), (8, 0, 2056, 0)], "extent length"),
                ([(8, 9, 2048, 0), (8, 0, 2056, 0)], "unknown extent")]:
            self.reject(fixture(extents=extents), message)

    def test_zero_extent_has_no_physical_address(self):
        result = self.inspect(fixture(extents=[(8, 1, 0, 0), (8, 0, 2056, 0)]))
        self.assertNotIn("physical_offset", result["metadata"]["extents"][0])
        self.reject(fixture(extents=[(8, 1, 1, 0), (8, 0, 2056, 0)]), "invalid zero")

    def test_measured_partition_size_and_metadata_region_are_binding(self):
        self.reject(fixture(), "measured partition size", partition_size=PARTITION_SIZE * 2)
        self.reject(fixture(devices=[(1, 1048576, 0, PARTITION_SIZE, "super", 0)]),
                    "overlap metadata region")
        self.reject(fixture(groups=[("default", 0, 0), ("main", 0, 4096)]), "maximum size")

    def test_multi_device_metadata_is_explicitly_unsupported(self):
        data = fixture(devices=[(2048, 1048576, 0, PARTITION_SIZE, "super", 0),
                                (2048, 1048576, 0, PARTITION_SIZE, "super_other", 0)])
        self.reject(data, "one physical super")

    def test_slot_suffixing_uses_explicit_selected_slot(self):
        raw = fixture(partitions=[("system", 3, 0, 2, 1)],
                      groups=[("default", 0, 0), ("main", 1, 8192)],
                      devices=[(2048, 1048576, 0, PARTITION_SIZE, "super", 1)])
        result = self.inspect(raw, slot=1)["metadata"]
        self.assertEqual(result["partitions"][0]["name"], "system_b")
        self.assertEqual(result["partitions"][0]["group"], "main_b")
        self.assertEqual(result["block_devices"][0]["name"], "super_b")
        self.reject(raw, "only slots 0 and 1", slot=2)
        self.reject(raw, "out of bounds", slot=3)

    def test_capture_truncation_excess_and_image_containers_rejected(self):
        self.reject(fixture()[:-1], "complete metadata prefix")
        self.reject(fixture() + b"\0", "complete metadata prefix")
        for magic in [b"QFI\xfb", b"\x3a\xff\x26\xed"]:
            data = fixture(); data[:4] = magic
            self.reject(data, "raw bytes")

    def test_readonly_capture_cli_receipt_and_no_runtime_promotion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.bin"
            raw = bytes(fixture()); path.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            result = MODULE.inspect_capture(path, partition_size=PARTITION_SIZE, expected_sha256=sha)
            self.assertTrue(result["capture_unchanged"])
            self.assertTrue(result["capture_receipt_sha256_verified"])
            self.assertEqual(path.read_bytes(), raw)
            process = subprocess.run([sys.executable, str(SCRIPT), str(path), "--partition-size",
                                      str(PARTITION_SIZE), "--expected-sha256", "0" * 64],
                                     capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 1)
            failure = json.loads(process.stdout)
            self.assertEqual(failure["status"], "rejected")
            self.assertIs(failure["android_boot_verified"], False)
            alias = path.with_name("alias.bin"); alias.symlink_to(path)
            with self.assertRaises(OSError):
                MODULE.inspect_capture(alias, partition_size=PARTITION_SIZE)

    def test_late_capture_mutation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.bin"; path.write_bytes(bytes(fixture()))
            original = MODULE.inspect_prefix
            def changed(raw, **kwargs):
                result = original(raw, **kwargs)
                with path.open("r+b") as stream:
                    stream.seek(-1, 2); stream.write(b"x")
                return result
            with patch.object(MODULE, "inspect_prefix", side_effect=changed):
                with self.assertRaisesRegex(MODULE.MetadataError, "changed"):
                    MODULE.inspect_capture(path, partition_size=PARTITION_SIZE)

    def test_fifo_is_rejected_without_waiting_for_a_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.fifo"; os.mkfifo(path)
            process = subprocess.run([sys.executable, str(SCRIPT), str(path), "--partition-size",
                                      str(PARTITION_SIZE)], capture_output=True, text=True, timeout=2)
            self.assertEqual(process.returncode, 1)
            self.assertIn("regular file", json.loads(process.stdout)["error"])


if __name__ == "__main__":
    unittest.main()
