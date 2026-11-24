#!/usr/bin/env python3
"""Synthetic source files and the real local-pipe collector; no device or SSH."""
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("assemble_lun", SCRIPTS / "assemble_lun.py")
assembler = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(assembler)
capture = assembler.capture


class AssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "synthetic-lun.raw"
        self.original = bytes((i * 13 + i // 4096) % 256 for i in range(8 * 4096))
        self.source.write_bytes(self.original)
        self.source.chmod(0o444)
        self.out = self.root / "assembled.raw"

    def make_capture(self, name, start, length, classification="immutable", source=None):
        source = source or self.source
        out = self.root / name
        ranges = [{"label": name, "offset": start, "length": length, "classification": classification}]
        with contextlib.redirect_stderr(io.StringIO()):
            capture.send(source, len(self.original), ranges,
                         [sys.executable, str(SCRIPTS / "acquire_block_ranges.py"), "receive", "--out", str(out)],
                         chunk_bytes=8192, allow_regular=True)
        return out / "manifest.json"

    def three_parts(self):
        return [self.make_capture("super", 8192, 12288),
                self.make_capture("suffix-userdata", 20480, 12288, "live-mutable"),
                self.make_capture("prefix", 0, 8192)]

    def assemble(self, manifests, **kwargs):
        return assembler.assemble(manifests, str(self.source), len(self.original), self.out, **kwargs)

    def assert_no_success(self):
        self.assertFalse(self.out.exists())
        self.assertFalse(self.out.with_name(self.out.name + ".manifest.json").exists())

    def rewrite(self, path, change):
        value = json.loads(path.read_bytes())
        change(value)
        path.write_text(json.dumps(value))

    def test_three_captures_full_raw_readback_provenance_and_no_input_writes(self):
        manifests = self.three_parts()
        originals = {path: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode)
                     for manifest in manifests for path in manifest.parent.rglob("*") if path.is_file()}
        events = []
        with mock.patch.object(assembler.os, "unlink", side_effect=AssertionError("deletion is forbidden")):
            result = self.assemble(manifests, progress=events.append)
        self.assertEqual(self.out.read_bytes(), self.original)
        self.assertEqual(result["raw"]["sha256"], hashlib.sha256(self.original).hexdigest())
        self.assertEqual(result["raw"]["bytes"], len(self.original))
        self.assertEqual([e["offset"] for e in events], [0, 8192, 16384, 20480, 28672])
        self.assertEqual(events[-1]["bytes"], len(self.original))
        self.assertEqual(result["chunks_verified"], 5)
        self.assertTrue(result["raw_readback_verified"])
        self.assertFalse(result["snapshot_consistent"])
        self.assertFalse(result["pristine_factory"])
        self.assertFalse(result["pristine_factory_images"])
        self.assertFalse(result["android_boot_tested"])
        self.assertEqual([r["label"] for r in result["live_mutable_ranges"]], ["suffix-userdata"])
        for entry in result["source_manifests"]:
            self.assertEqual(entry["sha256"], capture.sha(Path(entry["path"]).read_bytes()))
        receipt = self.out.with_name(self.out.name + ".manifest.json")
        self.assertEqual(json.loads(receipt.read_bytes()), result)
        self.assertEqual(self.out.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.root.glob("*.partial")))
        for path, (raw, mtime, mode) in originals.items():
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_mode), (raw, mtime, mode))
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o444)

    def test_single_whole_lun_cli_reports_progress_and_completion(self):
        manifest = self.make_capture("whole", 0, len(self.original), "live-mutable")
        result = subprocess.run([sys.executable, str(SCRIPTS / "assemble_lun.py"),
                                 "--manifest", str(manifest), "--source", str(self.source),
                                 "--source-bytes", str(len(self.original)), "--out", str(self.out)],
                                capture_output=True, text=True, check=True)
        summary = json.loads(result.stdout)
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["raw"]["sha256"], capture.sha(self.original))
        self.assertEqual(len(result.stderr.splitlines()), 4)
        self.assertTrue(all(json.loads(line)["event"] == "chunk-verified-written"
                            for line in result.stderr.splitlines()))

    def test_super_only_hole_and_overlap_rejected_before_output_creation(self):
        super_manifest = self.make_capture("super", 8192, 12288)
        with self.assertRaisesRegex(ValueError, "incomplete LUN coverage"):
            self.assemble([super_manifest])
        prefix = self.make_capture("prefix", 0, 4096)
        suffix = self.make_capture("suffix", 20480, 12288, "live-mutable")
        with self.assertRaisesRegex(ValueError, "gap"):
            self.assemble([super_manifest, prefix, suffix])
        overlap = self.make_capture("overlap", 4096, 12288)
        with self.assertRaisesRegex(ValueError, "overlapping"):
            self.assemble([prefix, overlap, super_manifest, suffix])
        self.assert_no_success()
        self.assertFalse(self.out.with_name(self.out.name + ".partial").exists())

    def test_duplicate_manifest_and_wrong_expected_lun_rejected(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.assemble([manifest, manifest])
        for path, size in (("/dev/block/wrong-lun", len(self.original)),
                           (str(self.source), len(self.original) + 4096)):
            with self.assertRaisesRegex(ValueError, "wrong LUN"):
                assembler.assemble([manifest], path, size, self.out)
        self.assert_no_success()

    def test_mixed_lun_identity_rejected_even_at_same_size(self):
        prefix = self.make_capture("prefix", 0, 8192)
        other = self.root / "another-lun.raw"
        other.write_bytes(self.original)
        suffix = self.make_capture("suffix", 8192, len(self.original) - 8192, source=other)
        with self.assertRaisesRegex(ValueError, "wrong LUN"):
            self.assemble([prefix, suffix])
        # Matching a path string alone cannot merge changed device identities.
        for path in (suffix, suffix.parent / "plan.json"):
            self.rewrite(path, lambda obj: (obj["plan"] if "plan" in obj else obj)["source"].update(
                path=str(self.source)))
        with self.assertRaisesRegex(ValueError, "identity/geometry"):
            self.assemble([prefix, suffix])
        self.assert_no_success()

    def test_missing_or_contradictory_receipt_rejected(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        receipt = manifest.parent / "chunk-000000/receipt.json"
        self.rewrite(receipt, lambda obj: obj.update(offset=4096))
        with self.assertRaisesRegex(ValueError, "receipt contradicts"):
            self.assemble([manifest])
        receipt.unlink()
        with self.assertRaises(OSError):
            self.assemble([manifest])
        self.assert_no_success()

    def test_damaged_or_truncated_chunk_leaves_partial_without_success(self):
        for name, damage in (("corrupt", lambda raw: raw[:20] + bytes([raw[20] ^ 1]) + raw[21:]),
                             ("truncated", lambda raw: raw[:-4])):
            with self.subTest(name=name):
                manifest = self.make_capture(name, 0, len(self.original))
                chunk = manifest.parent / "chunk-000001/data.gz"
                chunk.write_bytes(damage(chunk.read_bytes()))
                self.out = self.root / (name + ".raw")
                with self.assertRaises((ValueError, EOFError, OSError)):
                    self.assemble([manifest])
                self.assert_no_success()
                partial = self.out.with_name(self.out.name + ".partial")
                self.assertEqual(partial.read_bytes(), self.original[:8192])

    def test_wrong_decoded_hash_and_length_rejected_even_with_valid_transport_hash(self):
        for name, raw in (("wrong-hash", b"!" * 8192), ("wrong-length", self.original[:4096])):
            with self.subTest(name=name):
                manifest = self.make_capture(name, 0, len(self.original))
                chunk = manifest.parent / "chunk-000000/data.gz"
                zipped = gzip.compress(raw, mtime=0)
                chunk.write_bytes(zipped)
                changed = {"compressed_bytes": len(zipped), "transport_sha256": capture.sha(zipped)}
                self.rewrite(manifest, lambda obj: obj["chunks"][0].update(changed))
                self.rewrite(chunk.parent / "receipt.json", lambda obj: obj.update(changed))
                self.out = self.root / (name + ".raw")
                with self.assertRaisesRegex(ValueError, "decoded length/SHA-256"):
                    self.assemble([manifest])
                self.assert_no_success()

    def test_input_change_between_verification_and_copy_rejected(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        original_verify = capture.verify_chunk

        def mutate_after_validation(path, record):
            original_verify(path, record)
            data = bytearray(path.read_bytes())
            data[-1] ^= 1
            path.write_bytes(data)

        with mock.patch.object(capture, "verify_chunk", side_effect=mutate_after_validation):
            with self.assertRaisesRegex(ValueError, "input changed"):
                self.assemble([manifest])
        self.assert_no_success()

    def test_existing_output_and_output_inside_capture_refused(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        self.out.write_bytes(b"must preserve")
        with self.assertRaisesRegex(ValueError, "exclusive new output"):
            self.assemble([manifest])
        self.assertEqual(self.out.read_bytes(), b"must preserve")
        self.out = manifest.parent / "new.raw"
        with self.assertRaisesRegex(ValueError, "outside every original"):
            self.assemble([manifest])
        self.assertFalse(self.out.exists())

    def test_publish_failure_renames_own_final_back_and_keeps_partial(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        actual_rename = assembler.rename_noreplace
        for interrupt_after_rename in (False, True):
            with self.subTest(interrupt_after_rename=interrupt_after_rename):
                self.out = self.root / (f"interrupted-{interrupt_after_rename}.raw")
                calls = 0

                def fail_receipt(source, target):
                    nonlocal calls
                    calls += 1
                    if calls == 2:
                        if interrupt_after_rename:
                            actual_rename(source, target)
                        raise OSError("simulated receipt publish failure")
                    return actual_rename(source, target)

                with mock.patch.object(assembler, "rename_noreplace", side_effect=fail_receipt), \
                        mock.patch.object(assembler.os, "unlink", side_effect=AssertionError("deletion is forbidden")):
                    with self.assertRaisesRegex(OSError, "receipt publish failure"):
                        self.assemble([manifest])
                self.assert_no_success()
                self.assertEqual(self.out.with_name(self.out.name + ".partial").read_bytes(), self.original)
                self.assertTrue(self.out.with_name(self.out.name + ".manifest.json.partial").exists())

    def test_rename_noreplace_eexist_preserves_both_files(self):
        partial, target = self.root / "new.partial", self.root / "existing"
        partial.write_bytes(b"new bytes must remain")
        target.write_bytes(b"existing bytes must remain")
        with self.assertRaises(FileExistsError):
            assembler.rename_noreplace(partial, target)
        self.assertEqual(partial.read_bytes(), b"new bytes must remain")
        self.assertEqual(target.read_bytes(), b"existing bytes must remain")

    def test_replaced_partial_is_rejected_and_preserved(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        partial = self.out.with_name(self.out.name + ".partial")
        moved = self.root / "moved-verified.raw"

        def replace_last(event):
            if event["chunk"] == event["chunks"]:
                partial.rename(moved)
                partial.write_bytes(b"not the verified raw\n")

        with self.assertRaisesRegex(ValueError, "input changed"):
            self.assemble([manifest], progress=replace_last)
        self.assert_no_success()
        self.assertEqual(partial.read_bytes(), b"not the verified raw\n")
        self.assertEqual(moved.read_bytes(), self.original)

    def test_inplace_edit_after_readback_is_rejected(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        actual_publish = assembler.publish

        def mutate_before_publish(files):
            partial = files[0][0]
            with partial.open("r+b") as stream:
                stream.write(b"changed!")
            return actual_publish(files)

        with mock.patch.object(assembler, "publish", side_effect=mutate_before_publish):
            with self.assertRaisesRegex(ValueError, "verified output changed"):
                self.assemble([manifest])
        self.assert_no_success()
        self.assertTrue(self.out.with_name(self.out.name + ".partial").exists())

    def test_publication_race_preserves_foreign_target_without_receipt(self):
        manifest = self.make_capture("whole", 0, len(self.original))
        actual_rename = assembler.rename_noreplace
        moved = self.root / "moved-verified.raw"

        def replace_at_rename(source, target):
            if target == self.out:
                source.rename(moved)
                source.write_bytes(b"foreign replacement")
            return actual_rename(source, target)

        with mock.patch.object(assembler, "rename_noreplace", side_effect=replace_at_rename):
            with self.assertRaisesRegex(ValueError, "input changed"):
                self.assemble([manifest])
        # This target is not our verified inode, so rollback must not move or
        # delete it. Absence of the completion receipt makes failure explicit.
        self.assertEqual(self.out.read_bytes(), b"foreign replacement")
        self.assertEqual(moved.read_bytes(), self.original)
        self.assertFalse(self.out.with_name(self.out.name + ".manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
