#!/usr/bin/env python3
"""Real-format fixtures for original Android input preparation (no QEMU needed).

Run from the repository root:
    python3 -m unittest discover -s tb371fc/tests -p test_android_boot_inputs.py -v
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "android_boot_inputs.py"
SPEC = importlib.util.spec_from_file_location("android_boot_inputs", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
inputs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inputs)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pad(data: bytes, page: int) -> bytes:
    return data + b"\0" * (-len(data) % page)


def boot_fixture(version: int = 2, page: int = 4096, *,
                 second: bytes = b"second!", recovery: bytes = b"dtbo-data",
                 tail: bytes = b"") -> tuple[bytes, dict[str, bytes]]:
    """Packed AOSP boot.img, deliberately crossing a kernel page boundary."""
    parts = {"kernel": b"K" * (page + 1), "ramdisk": b"\x1f\x8b\x08",
             "second": second,
             "recovery_dtbo": recovery if version >= 1 else b"",
             "dtb": b"embedded-board-dtb" if version >= 2 else b""}
    header = bytearray(page)
    header[:8] = b"ANDROID!"
    struct.pack_into("<10I", header, 8,
                     len(parts["kernel"]), 0x80008000,
                     len(parts["ramdisk"]), 0x84000000,
                     len(parts["second"]), 0x80F00000,
                     0x80000100, page, version, 0x2E004158)
    header[48:55] = b"TB371FC"
    header[64:76] = b"header-only "
    header[608:615] = b"cmdline"
    header[576:608] = bytes(range(32))
    if version >= 1:
        recovery_offset = page + sum(len(pad(parts[name], page))
                                     for name in ("kernel", "ramdisk", "second"))
        struct.pack_into("<IQI", header, 1632,
                         len(parts["recovery_dtbo"]),
                         recovery_offset if parts["recovery_dtbo"] else 0,
                         1648 if version == 1 else 1660)
    if version >= 2:
        struct.pack_into("<IQ", header, 1648, len(parts["dtb"]), 0x83000000)
    return bytes(header) + b"".join(pad(part, page) for part in parts.values()) + tail, parts


def fdt_fixture(bootargs: bytes | None = b"console=ttyMSM0,115200n8 androidboot.slot_suffix=_a\0",
                *, duplicate_bootargs: bool = False, tail: bytes = b"") -> bytes:
    """FDT v17 with root, chosen and an additional board node/property."""
    def word(number: int) -> bytes:
        return struct.pack(">I", number)

    def node(name: bytes) -> bytes:
        return word(1) + pad(name + b"\0", 4)

    def prop(name_offset: int, value: bytes) -> bytes:
        return word(3) + struct.pack(">II", len(value), name_offset) + pad(value, 4)

    strings = b"compatible\0bootargs\0reg\0"
    structure = node(b"") + prop(0, b"lenovo,tb371fc\0qcom,sm8250\0")
    structure += node(b"chosen")
    if bootargs is not None:
        structure += prop(11, bootargs)
        if duplicate_bootargs:
            structure += prop(11, bootargs)
    structure += word(2)
    structure += node(b"memory@80000000") + prop(20, struct.pack(">QQ", 0x80000000, 0x200000000))
    structure += word(2) + word(2) + word(9)
    reserve = struct.pack(">QQQQ", 0x80000000, 0x1000, 0, 0)
    structure_offset = 40 + len(reserve)
    strings_offset = structure_offset + len(structure)
    total = strings_offset + len(strings)
    header = struct.pack(">10I", 0xD00DFEED, total, structure_offset,
                         strings_offset, 40, 17, 16, 0, len(strings), len(structure))
    return header + reserve + structure + strings + tail


def owner_padding_shape_fixture() -> bytes:
    """Synthetic tree with the two observed padding offsets, not owner data.

    The original 973052-byte live-Android FDT report records 3-byte padding
    regions at offsets 821109 and 821537, each containing one nonzero byte.
    Values and property names here are invented test data; no firmware is
    embedded. Published evidence (numeric metadata only):
    https://github.com/cyjin-yl/TB371FC-6.12/blob/
    87bc3dc1fe6ed24f91f77bec4fa28c90aef8ec24/docs/research/2026-11-24/
    evidence/native-dt/live-android.json
    """
    word = lambda value: struct.pack(">I", value)
    strings = b"first\0second\0bootargs\0"
    structure = word(1) + b"\0" * 4
    for offset, name_offset, padding_bytes in ((821109, 0, b"\x01\0\0"),
                                              (821537, 6, b"\0\x02\0")):
        size = offset - (56 + len(structure) + 12)
        structure += word(3) + struct.pack(">II", size, name_offset)
        structure += b"F" * size + padding_bytes
    structure += word(1) + b"chosen\0\0"
    command = b"console=ttyMSM0 androidboot.slot_suffix=_a\0"
    structure += word(3) + struct.pack(">II", len(command), 13) + pad(command, 4)
    structure += word(2) + word(2) + word(9)
    header = struct.pack(">10I", 0xD00DFEED, 973052,
                         56, 56 + len(structure), 40, 17, 16, 0,
                         len(strings), len(structure))
    raw = header + b"\0" * 16 + structure + strings
    return raw + b"\0" * (973052 - len(raw))


class FixtureCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write(self, name: str, data: bytes) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def write_json(self, name: str, value: dict) -> Path:
        return self.write(name, json.dumps(value).encode())

    def prepare(self, *, storage: Path | None = None,
                bootargs: bytes | None = None, chosen: bytes | None = None) -> tuple[dict, Path]:
        boot, _ = boot_fixture(tail=b"opaque signature/padding")
        boot_path = self.write("boot.img", boot)
        dtb_path = self.write("device.dtb", fdt_fixture() if chosen is None else fdt_fixture(chosen))
        args_path = self.write("captured.cmdline", bootargs) if bootargs is not None else None
        result = inputs.prepare_android_inputs(
            boot_path, dtb_path, self.root / "prepared",
            storage_manifest=storage, bootargs_file=args_path)
        return result, self.root / "prepared" / "manifest.json"

    def storage(self, **overrides: object) -> tuple[dict, Path]:
        self.write("disks/lun0.raw", b"\0" * 8192)
        self.write("disks/lun4.raw", b"original-super-data" + b"\0" * (4096 - 19))
        entry = {"lun": 0, "path": "disks/lun0.raw", "format": "raw",
                 "logical_block_size": 4096, "logical_block_count": 2,
                 "descriptor": {"logical_block_size_exponent": 12,
                                "logical_block_count": 2}}
        entry.update(overrides)
        value = {"schema": inputs.STORAGE_SCHEMA, "slot": "_a", "luns": [entry]}
        return value, self.write_json("storage.json", value)


class BootHeaderTests(FixtureCase):
    def test_published_owner_boot_and_recovery_v2_numeric_layouts(self) -> None:
        # Metadata copied from this published receipt; image bodies are sparse
        # zero-filled fixture bytes, not original firmware or a boot proof.
        # https://github.com/cyjin-yl/TB371FC-6.12/blob/
        # 87bc3dc1fe6ed24f91f77bec4fa28c90aef8ec24/docs/research/2026-11-23/
        # evidence/boot-header-inspection.json
        cases = (
            ("boot_a", 1298323, 1608875, 0, 0, 38752256, 40361984, 100663296),
            ("boot_b", 1149483, 1609107, 0, 0, 38604800, 40214528, 100663296),
            ("recovery_a", 8576757, 1609107, 4299963, 46030848, 50331648, 51941376, 104857600),
            ("recovery_b", 8576742, 1609107, 4299963, 46030848, 50331648, 51941376, 104857600),
        )
        for name, ramdisk, dtb, dtbo, dtbo_offset, dtb_offset, payload_end, partition in cases:
            with self.subTest(image=name):
                raw = bytearray(4096)
                raw[:8] = b"ANDROID!"
                struct.pack_into("<10I", raw, 8, 37445648, 32768, ramdisk, 16777216,
                                 0, 0, 256, 4096, 2, 369099138)
                struct.pack_into("<IQI", raw, 1632, dtbo, dtbo_offset, 1660)
                struct.pack_into("<IQ", raw, 1648, dtb, 32505856)
                path = self.write(name + ".img", raw)
                with path.open("r+b") as fixture:
                    fixture.truncate(partition)
                parsed = inputs.parse_boot_image(path)
                self.assertEqual(parsed["components"]["kernel"]["offset"], 4096)
                self.assertEqual(parsed["components"]["ramdisk"]["offset"], 37453824)
                self.assertEqual(parsed["components"]["dtb"]["offset"], dtb_offset)
                self.assertEqual(parsed["header"]["recovery_dtbo_offset"], dtbo_offset)
                self.assertEqual(parsed["payload_padded_end"], payload_end)
                self.assertEqual(parsed["trailing_bytes"], partition - payload_end)
                self.assertFalse(parsed["physical_load_addresses_verified"])

    def test_all_three_header_versions_have_exact_component_bytes(self) -> None:
        for version, page in ((0, 2048), (1, 4096), (2, 4096)):
            with self.subTest(version=version):
                raw, parts = boot_fixture(version, page, tail=b"opaque footer")
                path = self.write(f"v{version}.img", raw)
                parsed = inputs.parse_boot_image(path)
                self.assertEqual(parsed["header"]["header_version"], version)
                self.assertEqual(parsed["header"]["header_size"], [1632, 1648, 1660][version])
                self.assertEqual(parsed["header"]["cmdline"], "header-only cmdline")
                self.assertEqual(parsed["trailing_bytes"], len(b"opaque footer"))
                for name, expected in parts.items():
                    part = parsed["components"][name]
                    self.assertEqual(raw[part["offset"]:part["end"]], expected)

    def test_v2_offsets_and_unaligned_64_bit_header_fields(self) -> None:
        raw, _ = boot_fixture()
        path = self.write("boot.img", raw)
        parsed = inputs.parse_boot_image(path)
        expected = {"kernel": 4096, "ramdisk": 12288, "second": 16384,
                    "recovery_dtbo": 20480, "dtb": 24576}
        self.assertEqual({key: value["offset"] for key, value in parsed["components"].items()}, expected)
        self.assertEqual(parsed["payload_padded_end"], 28672)
        self.assertEqual(parsed["header"]["recovery_dtbo_offset"], 20480)
        self.assertEqual(parsed["header"]["dtb_addr"], 0x83000000)

    def test_optional_second_and_recovery_are_not_fabricated(self) -> None:
        raw, _ = boot_fixture(second=b"", recovery=b"")
        parsed = inputs.parse_boot_image(self.write("boot.img", raw))
        self.assertEqual(parsed["components"]["dtb"]["offset"], 16384)
        self.assertEqual(parsed["header"]["recovery_dtbo_offset"], 0)
        self.assertEqual(parsed["components"]["second"]["size"], 0)

    def test_header_mutations_are_rejected(self) -> None:
        raw, _ = boot_fixture()
        cases = [("magic", 0, b"WRONG!!!"),
                 ("unsupported-version", 40, struct.pack("<I", 3)),
                 ("legacy-dt-size-is-not-a-version", 40, struct.pack("<I", 2048)),
                 ("invalid-page", 36, struct.pack("<I", 3000)),
                 ("zero-page", 36, struct.pack("<I", 0)),
                 ("empty-kernel", 8, struct.pack("<I", 0)),
                 ("empty-ramdisk", 16, struct.pack("<I", 0)),
                 ("kernel-size-overflow", 8, struct.pack("<I", 0xFFFFFFFF)),
                 ("wrong-header-size", 1644, struct.pack("<I", 1648)),
                 ("overlapping-dtbo", 1636, struct.pack("<Q", 4096)),
                 ("unaligned-dtbo", 1636, struct.pack("<Q", 20481)),
                 ("noncanonical-dtbo-gap", 1636, struct.pack("<Q", 24576)),
                 ("empty-v2-dtb", 1648, struct.pack("<I", 0)),
                 ("unterminated-product-name", 48, b"N" * 16),
                 ("unterminated-cmdline", 64, b"A" * 512),
                 ("invalid-utf8-cmdline", 64, b"\xff\0")]
        for name, offset, value in cases:
            with self.subTest(name=name):
                changed = bytearray(raw)
                changed[offset:offset + len(value)] = value
                with self.assertRaises(inputs.InputValidationError):
                    inputs.parse_boot_image(self.write(f"{name}.img", changed))

    def test_truncated_header_payload_and_last_page_are_rejected(self) -> None:
        raw, _ = boot_fixture()
        for length in (7, 1631, 1647, 1659, 4095, 12288, len(raw) - 1):
            with self.subTest(length=length), self.assertRaises(inputs.InputValidationError):
                inputs.parse_boot_image(self.write(f"truncated-{length}.img", raw[:length]))

    def test_absent_dtbo_requires_zero_offset(self) -> None:
        raw, _ = boot_fixture(recovery=b"")
        changed = bytearray(raw)
        struct.pack_into("<Q", changed, 1636, 20480)
        with self.assertRaisesRegex(inputs.InputValidationError, "recovery_dtbo_offset"):
            inputs.parse_boot_image(self.write("contradiction.img", changed))


class FdtTests(FixtureCase):
    def test_complete_tree_and_padding_are_retained(self) -> None:
        raw = fdt_fixture(tail=b"\0opaque-capture-tail\xff")
        info = inputs.inspect_full_dtb(self.write("full.dtb", raw))
        self.assertEqual(info["node_count"], 3)
        self.assertEqual(info["bootargs"], "console=ttyMSM0,115200n8 androidboot.slot_suffix=_a")
        self.assertEqual(info["trailing_bytes_preserved"], 21)
        self.assertEqual(info["source"]["sha256"], digest(raw))
        self.assertFalse(info["complete_board_coverage_verified"])
        self.assertTrue(info["format_compliant"])

    def test_owner_nonzero_padding_is_preserved_and_labeled_forensic(self) -> None:
        raw = owner_padding_shape_fixture()
        dtb = self.write("owner-shape.dtb", raw)
        info = inputs.inspect_full_dtb(dtb)
        self.assertEqual(info["totalsize"], 973052)
        self.assertTrue(info["structure_ranges_validated"])
        self.assertFalse(info["format_compliant"])
        self.assertTrue(info["padding_nonzero_tolerated_for_diagnostics"])
        self.assertEqual(info["nonzero_padding_bytes"], 2)
        self.assertEqual([(p["offset"], p["length"], p["nonzero_bytes"])
                          for p in info["nonzero_padding_regions"]],
                         [(821109, 3, 1), (821537, 3, 1)])
        boot, _ = boot_fixture()
        prepared = inputs.prepare_android_inputs(self.write("boot.img", boot), dtb,
                                                  self.root / "prepared")
        self.assertEqual(Path(prepared["dtb"]["path"]).read_bytes(), raw)
        self.assertEqual(prepared["dtb"]["sha256"], digest(raw))
        manifest = self.root / "prepared/manifest.json"
        inputs.load_prepared_manifest(manifest)
        prepared["provenance"]["full_dtb"]["format_compliant"] = True
        manifest.write_text(json.dumps(prepared))
        with self.assertRaisesRegex(inputs.InputValidationError, "format_compliant provenance"):
            inputs.load_prepared_manifest(manifest)

    def test_forensic_padding_does_not_relax_structure_bounds(self) -> None:
        raw = bytearray(owner_padding_shape_fixture())
        # Make the first property cross the structure boundary while retaining
        # both known nonzero padding bytes elsewhere in the supplied capture.
        struct.pack_into(">I", raw, 68, len(raw))
        with self.assertRaisesRegex(inputs.InputValidationError, "property is out of bounds"):
            inputs.inspect_full_dtb(self.write("bad-owner-shape.dtb", raw))

    def test_structural_mutations_are_rejected(self) -> None:
        raw = fdt_fixture()
        cases = [("magic", 0, 0), ("length", 4, len(raw) + 1),
                 ("structure-overlap", 8, 40), ("strings-overlap", 12, 72),
                 ("reserve-overlap", 16, 32), ("old-version", 20, 16),
                 ("unknown-compatibility", 24, 18),
                 ("structure-out-of-bounds", 36, len(raw)),
                 ("unknown-token", 72, 7)]
        for name, offset, value in cases:
            with self.subTest(name=name):
                changed = bytearray(raw)
                struct.pack_into(">I", changed, offset, value)
                with self.assertRaises(inputs.InputValidationError):
                    inputs.inspect_full_dtb(self.write(name + ".dtb", changed))

    def test_ambiguous_or_unusable_bootargs_are_rejected(self) -> None:
        for name, args in (("missing-nul", b"console=tty0"),
                           ("multiple-strings", b"foo\0bar\0"),
                           ("newline", b"foo\nbar\0"), ("utf8", b"foo=\xff\0")):
            with self.subTest(name=name), self.assertRaises(inputs.InputValidationError):
                inputs.inspect_full_dtb(self.write(name + ".dtb", fdt_fixture(args)))
        with self.assertRaisesRegex(inputs.InputValidationError, "duplicate FDT property"):
            inputs.inspect_full_dtb(self.write("duplicate.dtb", fdt_fixture(duplicate_bootargs=True)))


class StorageTests(FixtureCase):
    def test_explicit_luns_and_descriptor_geometry_without_gpt_guessing(self) -> None:
        value, manifest = self.storage()
        value["luns"].insert(0, {"lun": 4, "path": "disks/lun4.raw",
                                  "logical_block_size": 4096, "logical_block_count": 1})
        manifest.write_text(json.dumps(value))
        parsed = inputs.validate_storage_manifest(manifest)
        self.assertEqual([item["lun"] for item in parsed["luns"]], [0, 4])
        self.assertEqual(parsed["slot"], "_a")
        for item in parsed["luns"]:
            self.assertTrue(Path(item["path"]).is_absolute())
            self.assertTrue(item["readonly_original"])
            self.assertFalse(item["partition_layout_verified"])
            self.assertFalse(item["sha256_verified"])
            self.assertNotIn("partitions", item)
            self.assertNotIn("sha256", item)

    def test_large_luns_are_not_hashed_without_opt_in(self) -> None:
        _, manifest = self.storage(sha256="0" * 64)
        with mock.patch.object(inputs, "sha256_file", side_effect=AssertionError("unexpected full read")):
            result = inputs.validate_storage_manifest(manifest)
        self.assertFalse(result["luns"][0]["sha256_verified"])
        with self.assertRaisesRegex(inputs.InputValidationError, "SHA-256 mismatch"):
            inputs.validate_storage_manifest(manifest, verify_hashes=True)

    def test_supplied_digest_is_verified_when_requested(self) -> None:
        _, manifest = self.storage(sha256=digest(b"\0" * 8192))
        result = inputs.validate_storage_manifest(manifest, verify_hashes=True)
        self.assertTrue(result["luns"][0]["sha256_verified"])

    def test_geometry_format_and_lun_contradictions_are_rejected(self) -> None:
        mutations = [{"logical_block_size": 512}, {"logical_block_count": 1},
                     {"logical_block_count": True}, {"lun": 32}, {"lun": -1},
                     {"format": "qcow2"}, {"readonly_original": False},
                     {"descriptor": {"logical_block_size_exponent": 9, "logical_block_count": 2}},
                     {"descriptor": {"logical_block_size_exponent": 12, "logical_block_count": 3}},
                     {"sha256": "not-a-hash"}]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                _, manifest = self.storage(**mutation)
                with self.assertRaises(inputs.InputValidationError):
                    inputs.validate_storage_manifest(manifest)

    def test_duplicates_sparse_and_qcow_files_are_rejected(self) -> None:
        value, manifest = self.storage()
        value["luns"].append(dict(value["luns"][0]))
        manifest.write_text(json.dumps(value))
        with self.assertRaisesRegex(inputs.InputValidationError, "duplicate"):
            inputs.validate_storage_manifest(manifest)
        for magic in (b"QFI\xfb", b"\x3a\xff\x26\xed"):
            _, manifest = self.storage()
            (self.root / "disks/lun0.raw").write_bytes(magic + b"\0" * 8188)
            with self.assertRaisesRegex(inputs.InputValidationError, "not a raw disk"):
                inputs.validate_storage_manifest(manifest)

    def test_duplicate_json_key_is_rejected(self) -> None:
        path = self.write("duplicate.json", b'{"schema":"x","schema":"y"}')
        with self.assertRaisesRegex(inputs.InputValidationError, "duplicate JSON key"):
            inputs.validate_storage_manifest(path)


class PreparationTests(FixtureCase):
    def test_preparation_preserves_inputs_components_and_captured_command(self) -> None:
        raw, parts = boot_fixture(tail=b"signature bytes")
        boot = self.write("boot.img", raw)
        dtb_raw = fdt_fixture(b"  console=ttyMSM0\tandroidboot.slot_suffix=_a  \0", tail=b"capture padding")
        dtb = self.write("captured.dtb", dtb_raw)
        boot.chmod(0o444)
        dtb.chmod(0o444)
        _, storage = self.storage()
        originals = {path: (path.read_bytes(), path.stat().st_mode)
                     for path in (boot, dtb, self.root / "disks/lun0.raw", storage)}
        result = inputs.prepare_android_inputs(boot, dtb, self.root / "prepared", storage_manifest=storage)
        self.assertEqual(Path(result["kernel"]["path"]).read_bytes(), parts["kernel"])
        self.assertEqual(Path(result["initrd"]["path"]).read_bytes(), parts["ramdisk"])
        self.assertEqual(Path(result["dtb"]["path"]).read_bytes(), dtb_raw)
        self.assertEqual(Path(result["bootargs"]["path"]).read_bytes(),
                         b"  console=ttyMSM0\tandroidboot.slot_suffix=_a  ")
        for name in ("second", "recovery_dtbo", "dtb"):
            self.assertEqual(Path(result["additional_boot_components"][name]["path"]).read_bytes(), parts[name])
        for path, (content, mode) in originals.items():
            self.assertEqual(path.read_bytes(), content)
            self.assertEqual(path.stat().st_mode, mode)
        loaded = inputs.load_prepared_manifest(self.root / "prepared/manifest.json")
        self.assertTrue(loaded["initrd"]["sha256_verified"])
        self.assertFalse(loaded["luns"][0]["sha256_verified"])
        self.assertFalse(loaded["metadata"]["android_boot_verified"])
        self.assertTrue(loaded["metadata"]["second_stage_present_but_not_executed"])
        self.assertEqual(loaded["provenance"]["boot_image"]["source"]["sha256"], digest(raw))

    def test_explicit_command_is_preserved_and_only_one_newline_removed(self) -> None:
        result, manifest = self.prepare(bootargs=b"  console=tty0 custom=1  \r\n")
        self.assertEqual(Path(result["bootargs"]["path"]).read_bytes(), b"  console=tty0 custom=1  ")
        source = result["provenance"]["bootargs"]
        self.assertEqual(source["kind"], "explicit-file")
        self.assertTrue(source["terminal_newline_removed"])
        self.assertFalse(source["matches_captured_dtb_bootargs"])
        inputs.load_prepared_manifest(manifest)

    def test_missing_captured_bootargs_never_falls_back_to_header_guess(self) -> None:
        raw, _ = boot_fixture()
        boot = self.write("boot.img", raw)
        dtb = self.write("no-args.dtb", fdt_fixture(None))
        with self.assertRaisesRegex(inputs.InputValidationError, "supply an explicit"):
            inputs.prepare_android_inputs(boot, dtb, self.root / "failed")
        self.assertFalse((self.root / "failed").exists())
        args = self.write("bootargs.txt", b"console=tty0\n")
        result = inputs.prepare_android_inputs(boot, dtb, self.root / "prepared", bootargs_file=args)
        self.assertFalse(result["provenance"]["bootargs"]["captured_dtb_bootargs_present"])
        inputs.load_prepared_manifest(self.root / "prepared/manifest.json")

    def test_existing_output_is_not_overwritten(self) -> None:
        _, manifest = self.prepare()
        before = manifest.read_bytes()
        with self.assertRaises(FileExistsError):
            inputs.prepare_android_inputs(self.root / "boot.img", self.root / "device.dtb", manifest.parent)
        self.assertEqual(manifest.read_bytes(), before)

    def test_input_change_aborts_before_manifest_publication(self) -> None:
        raw, _ = boot_fixture()
        boot = self.write("boot.img", raw)
        dtb = self.write("full.dtb", fdt_fixture())
        real_copy = inputs._copy_slice

        def changing_copy(*args: object, **kwargs: object) -> dict:
            result = real_copy(*args, **kwargs)
            with boot.open("ab") as source:
                source.write(b"mutation")
            return result

        with mock.patch.object(inputs, "_copy_slice", side_effect=changing_copy):
            with self.assertRaisesRegex(inputs.InputValidationError, "input changed"):
                inputs.prepare_android_inputs(boot, dtb, self.root / "prepared")
        self.assertFalse((self.root / "prepared/manifest.json").exists())

    def test_cli_reports_preparation_without_claiming_android_boot(self) -> None:
        raw, _ = boot_fixture()
        boot = self.write("boot.img", raw)
        dtb = self.write("full.dtb", fdt_fixture())
        result = subprocess.run([sys.executable, str(SCRIPT), "--boot-image", str(boot),
                                 "--dtb", str(dtb), "--out", str(self.root / "prepared")],
                                check=True, capture_output=True, text=True)
        summary = json.loads(result.stdout)
        self.assertFalse(summary["android_boot_verified"])
        self.assertEqual(summary["header_version"], 2)
        self.assertEqual(summary["lun_count"], 0)
        self.assertNotIn("console=", result.stdout)
        inputs.load_prepared_manifest(summary["manifest"])


class PreparedManifestTests(FixtureCase):
    def test_changed_artifact_hash_or_size_is_rejected(self) -> None:
        result, manifest = self.prepare()
        kernel = Path(result["kernel"]["path"])
        original = kernel.read_bytes()
        kernel.write_bytes(b"X" + original[1:])
        with self.assertRaisesRegex(inputs.InputValidationError, "SHA-256 mismatch"):
            inputs.load_prepared_manifest(manifest)
        kernel.write_bytes(original + b"X")
        with self.assertRaisesRegex(inputs.InputValidationError, "size changed"):
            inputs.load_prepared_manifest(manifest)

    def test_missing_provenance_relative_paths_and_success_claims_are_rejected(self) -> None:
        original, manifest = self.prepare()
        changes = [("relative-path", lambda d: d["kernel"].update(path="kernel.bin")),
                   ("missing-provenance", lambda d: d.pop("provenance")),
                   ("invalid-provenance-type", lambda d: d.update(provenance=[])),
                   ("changed-dt-source", lambda d: d["provenance"]["full_dtb"]["source"].update(sha256="0" * 64)),
                   ("unknown-bootargs-source", lambda d: d["provenance"]["bootargs"].update(kind="guessed")),
                   ("wrong-source-offset", lambda d: d["initrd"].update(source_offset=4096)),
                   ("boot-success-claim", lambda d: d["metadata"].update(android_boot_verified=True)),
                   ("avb-success-claim", lambda d: d["metadata"].update(avb_verified=True)),
                   ("factory-success-claim", lambda d: d["metadata"].update(factory_provenance_verified=True)),
                   ("gpt-success-claim", lambda d: d["metadata"].update(partition_layout_verified=True)),
                   ("wrong-scope", lambda d: d["metadata"].update(scope="smoke-test"))]
        for name, change in changes:
            with self.subTest(name=name):
                changed = copy.deepcopy(original)
                change(changed)
                manifest.write_text(json.dumps(changed))
                with self.assertRaises(inputs.InputValidationError):
                    inputs.load_prepared_manifest(manifest)

    def test_changed_captured_command_is_rejected_even_with_updated_hash(self) -> None:
        result, manifest = self.prepare()
        changed = b"console=tty0 init=/bin/fake-success"
        Path(result["bootargs"]["path"]).write_bytes(changed)
        result["bootargs"].update(size=len(changed), sha256=digest(changed))
        manifest.write_text(json.dumps(result))
        with self.assertRaisesRegex(inputs.InputValidationError, "captured /chosen/bootargs"):
            inputs.load_prepared_manifest(manifest)

    def test_lun_digest_is_rechecked_by_runner_loader(self) -> None:
        _, storage = self.storage(sha256=digest(b"\0" * 8192))
        result, manifest = self.prepare(storage=storage)
        loaded = inputs.load_prepared_manifest(manifest)
        self.assertTrue(loaded["luns"][0]["sha256_verified"])
        Path(result["luns"][0]["path"]).write_bytes(b"X" + b"\0" * 8191)
        with self.assertRaisesRegex(inputs.InputValidationError, "LUN 0 SHA-256 mismatch"):
            inputs.load_prepared_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
