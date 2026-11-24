#!/usr/bin/env python3
"""Synthetic regular-file and local-pipe tests; no device/SSH access."""
import copy
import gzip
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zlib

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/acquire_block_ranges.py"
SPEC = importlib.util.spec_from_file_location("capture", SCRIPT)
capture = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(capture)


def gpt_fixture():
    """Invented 256-KiB disk, never original owner GPT/GUID/firmware bytes."""
    block, count = 4096, 64
    raw = bytearray(block * count)
    entries = bytearray(512)
    for index, name, first, last in ((0, "super", 8, 23), (1, "metadata", 24, 25)):
        start = index * 128
        entries[start:start + 16] = bytes([index + 1]) * 16
        entries[start + 16:start + 32] = bytes([index + 3]) * 16
        struct.pack_into("<QQQ", entries, start + 32, first, last, 0)
        encoded = name.encode("utf-16-le")
        entries[start + 56:start + 56 + len(encoded)] = encoded
    for current, other, table in ((1, 63, 2), (63, 1, 62)):
        header = bytearray(block)
        header[:8] = b"EFI PART"
        struct.pack_into("<4I4Q", header, 8, 0x10000, 92, 0, 0, current, other, 3, 61)
        header[56:72] = b"synthetic-guid!!"
        struct.pack_into("<QIII", header, 72, table, 4, 128, zlib.crc32(entries))
        struct.pack_into("<I", header, 16, zlib.crc32(header[:92]))
        raw[current * block:(current + 1) * block] = header
        raw[table * block:table * block + len(entries)] = entries
    raw[510:512] = b"\x55\xaa"
    return bytes(raw)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic-source.raw"
        self.original = bytes(range(256)) * 128
        self.source.write_bytes(self.original)
        self.out = self.root / "capture"
        self.ranges = [{"label": "super", "offset": 4096, "length": 12288,
                        "classification": "immutable"}]

    def transport(self, resume=False):
        command = [sys.executable, str(SCRIPT), "receive", "--out", str(self.out)]
        return command + (["--resume"] if resume else [])

    def run_send(self, resume=False, **kwargs):
        return capture.send(self.source, len(self.original), self.ranges,
                            self.transport(resume), chunk_bytes=4096, allow_regular=True, **kwargs)

    def plan(self):
        fd = os.open(self.source, os.O_RDONLY)
        try:
            identity = capture.geometry(fd, True)
        finally:
            os.close(fd)
        return {"schema": capture.SCHEMA, "source": {"path": str(self.source), **identity},
                "ranges": self.ranges, "chunk_bytes": 4096, "gpt": None,
                "source_open_mode": "O_RDONLY", "snapshot_consistent": False}

    def test_local_pipe_exact_ranges_hashes_and_readonly_source(self):
        self.source.chmod(0o444)
        result = self.run_send()
        self.assertTrue(result["complete"])
        manifest = json.loads((self.out / "manifest.json").read_bytes())
        self.assertEqual(len(manifest["chunks"]), 3)
        reconstructed = b""
        for row in manifest["chunks"]:
            path = self.out / f"chunk-{row['index']:06d}/data.gz"
            raw = gzip.decompress(path.read_bytes())
            self.assertEqual(len(raw), 4096)
            self.assertEqual(row["source_sha256"], capture.sha(raw))
            self.assertEqual(row["source_sha256"], row["source_before_sha256"])
            self.assertEqual(row["source_sha256"], row["source_after_sha256"])
            self.assertEqual(row["transport_sha256"], capture.sha(path.read_bytes()))
            reconstructed += raw
        self.assertEqual(reconstructed, self.original[4096:16384])
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o444)
        self.assertFalse(manifest["snapshot_consistent"])
        self.assertFalse(manifest["whole_source_bytes_captured"])
        self.assertFalse(manifest["pristine_factory_images"])

    def test_resume_revalidates_existing_files_and_current_source(self):
        self.run_send()
        before = (self.out / "manifest.json").read_bytes()
        files_before = {p: p.stat().st_mtime_ns for p in self.out.glob("chunk-*/data.gz")}
        with mock.patch.object(capture, "hash_range", wraps=capture.hash_range) as hashed:
            result = self.run_send(resume=True)
        self.assertTrue(result["complete"])
        self.assertEqual(hashed.call_count, 3)
        self.assertEqual((self.out / "manifest.json").read_bytes(), before)
        self.assertEqual({p: p.stat().st_mtime_ns for p in files_before}, files_before)

    def test_resume_rejects_changed_completion_qualifications(self):
        self.run_send()
        manifest_path = self.out / "manifest.json"
        original = json.loads(manifest_path.read_bytes())
        self.assertFalse(original["whole_source_bytes_captured"])
        changes = [
            ("whole_source_bytes_captured", True),
            ("source_immutable_claim_verified", True),
            ("pristine_factory_images", True),
            ("restore_tested", True),
            ("android_boot_tested", True),
            ("live_mutable_ranges", ["invented"]),
            ("qualification", "INVALID full-source qualification"),
            ("whole_source_bytes_captured", 0),  # False and 0 must not be interchangeable.
            ("unknown_qualification", True),
            ("completed_utc", "invalid"),
            ("resume_source_revalidated_chunks", [0, 0]),
            ("resume_source_revalidated_chunks", [len(original["chunks"])]),
        ]
        for field, value in changes + [("missing_field", None)]:
            with self.subTest(field=field, value=value):
                changed = copy.deepcopy(original)
                if field == "missing_field":
                    del changed["whole_source_bytes_captured"]
                else:
                    changed[field] = value
                saved = json.dumps(changed).encode()
                manifest_path.write_bytes(saved)
                incoming, outgoing = io.BytesIO(), io.BytesIO()
                capture.send_json(incoming, original["plan"])
                for row in original["chunks"]:
                    capture.send_json(incoming, {"skip": row["index"],
                                                 "source_reread_sha256": row["source_sha256"]})
                capture.send_json(incoming, {"finish": True})
                incoming.seek(0)
                with self.assertRaises(ValueError):
                    capture.receive(incoming, outgoing, self.out, resume=True)
                self.assertNotIn(b'"complete":true', outgoing.getvalue())
                self.assertEqual(manifest_path.read_bytes(), saved)

    def test_resume_rejects_changed_source_even_with_same_size(self):
        self.run_send()
        changed = bytearray(self.original)
        changed[4096] ^= 1
        self.source.write_bytes(changed)
        with self.assertRaisesRegex(ValueError, "source changed since completed"):
            self.run_send(resume=True)

    def test_resume_rejects_corrupt_stored_chunk(self):
        self.run_send()
        path = self.out / "chunk-000000/data.gz"
        raw = bytearray(path.read_bytes())
        raw[len(raw) // 2] ^= 1
        path.write_bytes(raw)
        with self.assertRaises(ValueError):
            self.run_send(resume=True)

    def test_source_change_during_capture_has_no_success_manifest(self):
        original_hash = capture.hash_range
        calls = 0

        def changing(fd, start, length):
            nonlocal calls
            calls += 1
            if calls == 2:
                changed = bytearray(self.original)
                changed[start] ^= 1
                self.source.write_bytes(changed)
            return original_hash(fd, start, length)

        with mock.patch.object(capture, "hash_range", side_effect=changing):
            with self.assertRaisesRegex(ValueError, "before/stream/after"):
                self.run_send()
        self.assertFalse((self.out / "manifest.json").exists())
        self.assertFalse((self.out / "chunk-000000/receipt.json").exists())
        self.assertTrue((self.out / "chunk-000000/data.gz.partial").exists())
        # Interrupted uncommitted bytes can be discarded; no prior block is
        # silently reused. This remains explicitly a non-atomic capture.
        self.source.write_bytes(self.original)
        self.assertTrue(self.run_send(resume=True)["complete"])

    def test_truncated_transfer_and_partial_json_resume(self):
        stream = io.BytesIO()
        capture.send_json(stream, self.plan())
        capture.send_json(stream, {"begin": 0, "source_before_sha256": "0" * 64})
        stream.write(struct.pack(">I", 100) + b"truncated")
        stream.seek(0)
        with self.assertRaisesRegex(ValueError, "truncated transfer frame"):
            capture.receive(stream, io.BytesIO(), self.out)
        self.assertFalse((self.out / "manifest.json").exists())
        (self.out / "chunk-000000/receipt.json.partial").write_bytes(b'{"partial":')
        (self.out / "manifest.json.partial").write_bytes(b'{"partial":')
        self.assertTrue(self.run_send(resume=True)["complete"])
        self.assertFalse(list(self.out.rglob("*.partial")))

    def test_exclusive_json_never_exposes_truncated_success(self):
        path = self.root / "record.json"
        with mock.patch.object(capture.json, "dump", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                capture.write_json_exclusive(path, {"success": True})
        self.assertFalse(path.exists())
        self.assertTrue(self.root.joinpath("record.json.partial").exists())

    def test_receiver_rejects_wrong_raw_length_or_after_hash(self):
        row = {"index": 0, "label": "super", "offset": 4096, "length": 4096,
               "classification": "immutable"}
        fd = os.open(self.source, os.O_RDONLY)
        try:
            wire = io.BytesIO()
            good = capture.send_chunk(fd, row, wire)
        finally:
            os.close(fd)
        for field, value in (("source_after_sha256", "0" * 64), ("length", 8192),
                             ("transport_sha256", "0" * 64), ("compressed_bytes", 1)):
            with self.subTest(field=field):
                incoming = io.BytesIO(wire.getvalue())
                begin = capture.get_json(incoming)
                framed = io.BytesIO()
                while True:
                    header = capture.read_exact(incoming, 4)
                    length = struct.unpack(">I", header)[0]
                    framed.write(header)
                    if not length:
                        break
                    framed.write(capture.read_exact(incoming, length))
                record = capture.get_json(incoming)
                record[field] = value
                capture.send_json(framed, record)
                framed.seek(0)
                directory = self.root / field
                directory.mkdir()
                with self.assertRaises(ValueError):
                    capture.receive_chunk(row, begin, framed, directory)
                self.assertFalse((directory / "receipt.json").exists())
                self.assertFalse((directory / "data.gz").exists())
        self.assertTrue(good["source_rechecked"])

    def test_expected_digest_binds_actual_sent_bytes_not_an_earlier_probe(self):
        row = {"index": 0, "label": "gpt-primary-header", "offset": 4096,
               "length": 4096, "classification": "immutable",
               "expected_sha256": capture.sha(self.original[4096:8192])}
        changed = bytearray(self.original)
        changed[4096] ^= 1
        self.source.write_bytes(changed)
        fd = os.open(self.source, os.O_RDONLY)
        wire = io.BytesIO()
        try:
            with self.assertRaisesRegex(ValueError, "expected range digest"):
                capture.send_chunk(fd, row, wire)
        finally:
            os.close(fd)
        self.assertEqual(wire.getvalue(), b"")

    def test_final_short_chunk_and_real_super_geometry(self):
        capture.send(self.source, len(self.original), self.ranges, self.transport(),
                     chunk_bytes=8192, allow_regular=True)
        manifest = json.loads((self.out / "manifest.json").read_bytes())
        self.assertEqual([r["length"] for r in manifest["chunks"]], [8192, 4096])
        plan = self.plan()
        plan["source"]["bytes"] = 125720068096
        plan["ranges"] = [{"label": "super", "offset": 8712 * 4096,
                           "length": 12884901888, "classification": "immutable"}]
        for mib, count in ((64, 192), (256, 48)):
            plan["chunk_bytes"] = mib * capture.IO
            rows = capture.validate_plan(plan)
            self.assertEqual(len(rows), count)
            self.assertEqual(rows[0]["offset"], 35684352)
            self.assertEqual(sum(r["length"] for r in rows), 12884901888)
            self.assertEqual(rows[-1]["offset"] + rows[-1]["length"], 12920586240)

    def test_range_geometry_mutability_and_whole_source_bounds(self):
        changes = [{"offset": -4096}, {"offset": 1}, {"length": 0},
                   {"length": 4095}, {"offset": len(self.original)},
                   {"label": "../x"}, {"label": "userdata"}, {"label": "metadata"}]
        for update in changes:
            with self.subTest(update=update), self.assertRaises(ValueError):
                capture.validate_ranges([{**self.ranges[0], **update}], len(self.original), 4096)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            capture.validate_ranges([self.ranges[0], {**self.ranges[0], "label": "another"}], len(self.original), 4096)
        self.ranges[0].update(label="userdata", classification="live-mutable")
        self.run_send()
        manifest = json.loads((self.out / "manifest.json").read_bytes())
        self.assertEqual(manifest["live_mutable_ranges"], ["userdata"])
        self.assertFalse(manifest["snapshot_consistent"])

    def test_wrong_expected_device_size_fails_before_transport(self):
        with mock.patch.object(capture.subprocess, "Popen", side_effect=AssertionError("transport must not start")):
            with self.assertRaisesRegex(ValueError, "geometry differs"):
                capture.send(self.source, len(self.original) + 4096, self.ranges,
                             ["not-executed"], allow_regular=True)
            with self.assertRaisesRegex(ValueError, "block device"):
                capture.send(self.source, len(self.original), self.ranges, ["not-executed"])

    def test_ssh_remote_argv_is_one_safely_quoted_command(self):
        remote = ["python3", "/private/path with spaces/capture.py", "receive", "--out",
                  "/private/$(touch not-executed);quoted'capture"]
        command = capture.ssh_transport(["/approved/dbclient", "-i", "/approved/key", "owner@approved"], remote)
        self.assertEqual(shlex.split(command[-1]), remote)
        self.assertEqual(len(command), 5)
        self.assertEqual(command[-2], "owner@approved")

    def test_actual_gpt_locations_come_from_matching_crc_checked_headers(self):
        raw = gpt_fixture()
        path = self.root / "invented-gpt.raw"
        path.write_bytes(raw)
        fd = os.open(path, os.O_RDONLY)
        try:
            ranges, metadata = capture.gpt_ranges(fd, len(raw))
        finally:
            os.close(fd)
        self.assertTrue(metadata["copies_match"])
        self.assertEqual([(p["name"], p["offset"], p["length"]) for p in metadata["partitions"]],
                         [("super", 32768, 65536), ("metadata", 98304, 8192)])
        self.assertEqual([(r["offset"], r["length"]) for r in ranges],
                         [(0, 4096), (4096, 4096), (8192, 4096), (253952, 4096), (258048, 4096)])
        for row in ranges:
            self.assertEqual(row["expected_sha256"], capture.sha(raw[row["offset"]:row["offset"] + row["length"]]))

    def test_gpt_crc_truncation_and_declared_array_bounds_rejected(self):
        raw = gpt_fixture()
        for label, offset in (("header", 4096 + 20), ("array", 8192 + 80), ("backup", 258048 + 16)):
            with self.subTest(label=label):
                changed = bytearray(raw)
                changed[offset] ^= 1
                self.source.write_bytes(changed)
                fd = os.open(self.source, os.O_RDONLY)
                try:
                    with self.assertRaises(ValueError):
                        capture.gpt_ranges(fd, len(raw))
                finally:
                    os.close(fd)
        changed = bytearray(raw)
        struct.pack_into("<Q", changed, 4096 + 72, 8)  # Tables cannot overlap usable data.
        changed[4096 + 16:4096 + 20] = b"\0" * 4
        struct.pack_into("<I", changed, 4096 + 16, zlib.crc32(changed[4096:4096 + 92]))
        self.source.write_bytes(changed)
        fd = os.open(self.source, os.O_RDONLY)
        try:
            with self.assertRaisesRegex(ValueError, "overlaps usable"):
                capture.gpt_ranges(fd, len(raw))
        finally:
            os.close(fd)


if __name__ == "__main__":
    unittest.main()
