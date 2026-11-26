#!/usr/bin/env python3
"""Small synthetic metadata tests; no captured firmware, DB, LUN or QEMU input."""
import contextlib
import io
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tb371fc/scripts"))
import extend_ufs_virtual_supplies as subject

DESCRIPTOR = REPO / "hw/misc/qcom-sm8250-rpmh-data.inc"
DESCRIPTOR_BYTES = DESCRIPTOR.read_bytes()

# Independent consumer/provider fixture for the observed missing PHY edges.
# These are metadata names and phandles, not a redistributed original DT blob.
PHY_CONSUMER = {"vdda-phy-supply": 0x7c, "vdda-pll-supply": 0x7d}
PHY_PROVIDERS = {0x7c: ("ldoa5", 0x4e130, 880),
                 0x7d: ("ldoa9", 0x4e170, 1200)}


def fixture():
    """Independent 128KiB regular-byte fixture with the pinned input layout."""
    image = bytearray(131072)
    struct.pack_into("<II", image, 0, 1, 0x0c0330db)
    layouts = ((3, 144, 912, 32, 0x1000),
               (5, 1252, 2140, 37, 0x1000),
               (4, 2436, 2580, 6, 0x100))
    for index, (slave, start, aux, count, version) in enumerate(layouts):
        struct.pack_into("<8H", image, 8 + 16 * index,
                         slave, start - 144, aux - 144, count, version, 0, 0, 0)
    for i in range(32):
        address = 0 if i >= 30 else 0x30000 + 4 * i
        payload = struct.pack("<HH", 0, 10 + i)
        struct.pack_into("<8sIIIHH", image, 144 + 24 * i,
                         ("arc%02d" % i).encode(), i, 100 + i,
                         address, len(payload), 4 * i)
        image[912 + 4 * i:916 + 4 * i] = payload
    for i in range(37):
        struct.pack_into("<8sIIIHH", image, 1252 + 24 * i,
                         ("bcm%02d" % i).encode(), i + 4, i + 5,
                         0x50000 + 4 * i, 8, 8 * i)
        image[2140 + 8 * i:2148 + 8 * i] = struct.pack("<IHBB", 1000 + i, 16, i % 10, 0)
    names = ("lnbclka1", "vrm.xob", "lnbclka2", "lnbclka3", "rfclka1", "rfclka3")
    addresses = (0x42500, 0x42500, 0x42600, 0x42700, 0x42800, 0x42900)
    for i, (name, address) in enumerate(zip(names, addresses)):
        struct.pack_into("<8sIIIHH", image, 2436 + 24 * i,
                         name.encode(), 0, 0, address, 4, 4 * i)
        image[2580 + 4 * i:2584 + 4 * i] = b"\x01\0\0\0"
    return bytes(image)


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.original = fixture()

    def transform(self, data=None):
        data = self.original if data is None else bytes(data)
        # This fixture hash is supplied only to the pure test API.
        # The command-line interface does not expose it.
        return subject.extend_bytes(data, descriptor_bytes=DESCRIPTOR_BYTES,
                                    expected_sha256=subject.sha256(data))

    def test_complete_original_records_priorities_and_aux_preserved(self):
        result, manifest = self.transform()
        old, new = subject.parse_db(self.original), subject.parse_db(result)
        self.assertEqual(75, len(old.records))
        self.assertEqual(81, len(new.records))
        self.assertEqual([r.meaning() for r in old.records],
                         [r.meaning() for r in new.records[:75]])
        self.assertEqual([r.raw for r in old.records], [r.raw for r in new.records[:75]])
        self.assertEqual(["smpa4", "ldoa6", "ldoa17", "smpc8", "ldoa5", "ldoa9"],
                         [r.name for r in new.records[75:]])
        self.assertTrue(all(r.aux == b"" for r in new.records[75:]))
        self.assertEqual(subject.sha256(result), manifest["output"]["sha256"])

    def test_only_two_group_fields_and_new_zero_table_change(self):
        result, manifest = self.transform()
        # The permitted byte mask is an independent format expectation, not
        # whatever range the generated manifest happens to report.
        allowed = [[42, 44], [46, 48], [8192, 8480]]
        self.assertEqual(allowed, manifest["changes"]["allowed_file_ranges"])
        self.assertEqual(131072, len(result))
        for i, (before, after) in enumerate(zip(self.original, result)):
            if before != after:
                self.assertTrue(any(start <= i < end for start, end in allowed), i)
        self.assertEqual(self.original[2436:2604], result[2436:2604])
        self.assertEqual(bytes(288), self.original[8192:8480])
        self.assertEqual(3, len(subject.parse_db(result).groups))
        self.assertEqual(12, subject.parse_db(result).groups[2].count)

    def test_deterministic_transform_and_honest_scope(self):
        a, ma = self.transform()
        b, mb = self.transform()
        self.assertEqual((a, ma), (b, mb))
        self.assertEqual("virtual-ufs-supplies-v2", ma["profile"])
        for key in ("complete_pmic_database", "physical_addresses_verified",
                    "aop_executed", "guest_or_android_boot_verified", "migration_qualified",
                    "linux_driver_probe_tested", "ufs_link_or_luns_tested"):
            self.assertFalse(ma["limits"][key])

    def test_cli_production_pin_rejects_synthetic_fixture(self):
        with self.assertRaisesRegex(subject.InvalidDB, "SHA256"):
            subject.extend_bytes(self.original, descriptor_bytes=DESCRIPTOR_BYTES)

    def test_wrong_size_rejected_even_with_matching_fixture_hash(self):
        data = self.original[:-1]
        with self.assertRaisesRegex(subject.InvalidDB, "128KiB"):
            self.transform(data)

    def test_unknown_version_or_magic(self):
        for offset in (0, 4):
            with self.subTest(offset=offset):
                data = bytearray(self.original)
                struct.pack_into("<I", data, offset, 0)
                with self.assertRaisesRegex(subject.InvalidDB, "version or magic"):
                    self.transform(data)

    def test_reserved_global_or_group_fields(self):
        for offset in (136, 18):
            with self.subTest(offset=offset):
                data = bytearray(self.original)
                data[offset] = 1
                with self.assertRaisesRegex(subject.InvalidDB, "reserved"):
                    self.transform(data)

    def test_duplicate_slave_group(self):
        data = bytearray(self.original)
        struct.pack_into("<H", data, 40, 5)
        with self.assertRaisesRegex(subject.InvalidDB, "duplicate slave"):
            self.transform(data)

    def test_group_hole_not_silently_ignored(self):
        data = bytearray(self.original)
        data[24:40] = bytes(16)
        with self.assertRaisesRegex(subject.InvalidDB, "after unused"):
            self.transform(data)

    def test_unknown_layout(self):
        data = bytearray(self.original)
        struct.pack_into("<H", data, 16, 0x2000)
        with self.assertRaisesRegex(subject.InvalidDB, "unknown input group layout"):
            self.transform(data)

    def test_record_and_aux_bounds(self):
        data = bytearray(self.original)
        struct.pack_into("<H", data, 14, 65535)
        with self.assertRaisesRegex(subject.InvalidDB, "record table out of bounds"):
            self.transform(data)
        data = bytearray(self.original)
        struct.pack_into("<HH", data, 144 + 20, 65535, 65535)
        with self.assertRaisesRegex(subject.InvalidDB, "auxiliary record out of bounds"):
            self.transform(data)

    def test_noncanonical_name_and_duplicate_name(self):
        data = bytearray(self.original)
        data[144:152] = b"a\0x\0\0\0\0\0"
        with self.assertRaisesRegex(subject.InvalidDB, "noncanonical"):
            self.transform(data)
        data = bytearray(self.original)
        data[1252:1260] = data[144:152]
        with self.assertRaisesRegex(subject.InvalidDB, "duplicate resource name"):
            self.transform(data)

    def test_wrong_slave_address_or_alignment(self):
        for address in (0x40000, 0x30001):
            with self.subTest(address=address):
                data = bytearray(self.original)
                struct.pack_into("<I", data, 144 + 16, address)
                with self.assertRaises(subject.InvalidDB):
                    self.transform(data)

    def test_unexpected_target_inventory(self):
        data = bytearray(self.original)
        data[2436 + 2 * 24:2444 + 2 * 24] = b"smpa4\0\0\0"
        with self.assertRaisesRegex(subject.InvalidDB, "VRM inventory"):
            self.transform(data)

    def test_existing_resource_collision_with_new_synthetic_address(self):
        data = bytearray(self.original)
        struct.pack_into("<I", data, 2436 + 3 * 24 + 16, 0x4e080)
        with self.assertRaisesRegex(subject.InvalidDB, "synthetic resource collides"):
            self.transform(data)

    def test_only_original_clock_alias_is_allowed(self):
        data = bytearray(self.original)
        struct.pack_into("<I", data, 2436 + 2 * 24 + 16, 0x42500)
        with self.assertRaisesRegex(subject.InvalidDB, "conflicting resource address"):
            self.transform(data)

    def test_nonzero_destination_bytes_rejected(self):
        data = bytearray(self.original)
        data[8192 + 287] = 1
        with self.assertRaisesRegex(subject.InvalidDB, "nonzero bytes"):
            self.transform(data)

    def test_zero_bytes_still_cannot_overwrite_declared_aux_data(self):
        data = bytearray(self.original)
        struct.pack_into("<HH", data, 1252 + 20, 8, 8192 - 2140)
        with self.assertRaisesRegex(subject.InvalidDB, "declared input data"):
            self.transform(data)


    def test_independent_phy_consumer_graph_is_covered_by_generated_db(self):
        old = {row.name: row for row in subject.parse_db(self.original).records}
        self.assertTrue(all(PHY_PROVIDERS[handle][0] not in old
                            for handle in PHY_CONSUMER.values()))
        output, manifest = self.transform()
        parsed = {row.name: row for row in subject.parse_db(output).records}
        declared = {row["name"]: row for row in manifest["synthetic_additions"]}
        for supply, handle in PHY_CONSUMER.items():
            with self.subTest(supply=supply):
                name, address, operating_mv = PHY_PROVIDERS[handle]
                resource = parsed[name]
                self.assertEqual((4, address, b""), (resource.slave, resource.address, resource.aux))
                self.assertEqual(operating_mv, declared[name]["selected_operating_mv"])
                self.assertEqual(1304, declared[name]["existing_model_max_request_mv"])
                self.assertEqual("0x98", declared[name]["existing_model_mode_mask"])
        self.assertEqual({"vdda-phy": "ldoa5", "vdda-pll": "ldoa9"},
                         manifest["dependencies"]["ufs-phy"])

    def test_prior_four_supply_headers_are_unchanged(self):
        output, _ = self.transform()
        expected = [
            struct.pack("<8sIIIHH", name, 0, 0, address, 0, 0)
            for name, address in ((b"smpa4", 0x4e080), (b"ldoa6", 0x4e140),
                                  (b"ldoa17", 0x4e1f0), (b"smpc8", 0x4e280))
        ]
        self.assertEqual(expected, [row.raw for row in subject.parse_db(output).records[75:79]])

    def test_phy_records_are_reachable_vrm_addresses_in_raw_guest_layout(self):
        output, _ = self.transform()
        # Decode independently of the generator parser and its inventory constants.
        headers = [struct.unpack_from("<8H", output, 8 + 16 * i) for i in range(8)]
        self.assertEqual([3, 5, 4, 0, 0, 0, 0, 0], [h[0] for h in headers])
        slave, relative, auxiliary, count, version, *reserved = headers[2]
        self.assertEqual((4, 8192 - 144, 2580 - 144, 12, 0x100, [0, 0, 0]),
                         (slave, relative, auxiliary, count, version, reserved))
        rows = [struct.unpack_from("<8sIIIHH", output, relative + 144 + i * 24)
                for i in range(count)]
        for name, address, _mv in PHY_PROVIDERS.values():
            row = next(r for r in rows if r[0] == name.encode().ljust(8, b"\0"))
            self.assertEqual((0, 0, address, 0, 0), row[1:])
            self.assertEqual(4, (row[3] >> 16) & 7)
        self.assertEqual(12, len({row[0] for row in rows}))

    def test_removing_either_phy_resource_is_rejected(self):
        # Reproduces the v1 omission without weakening the independent consumer contract.
        for missing in ("ldoa5", "ldoa9"):
            with self.subTest(missing=missing):
                reduced = tuple(row for row in subject.ADDITIONS if row[0] != missing)
                with mock.patch.object(subject, "ADDITIONS", reduced):
                    with self.assertRaisesRegex(subject.InvalidDB, "dependency resources"):
                        self.transform()

    def test_changed_phy_address_type_limit_or_mode_is_rejected(self):
        for name, address, _operating_mv in PHY_PROVIDERS.values():
            for wrong_address, maximum, modes, error in (
                (address + 0x10000, 1304, 0x98, "slave 4"),
                (address - 0x10, 1304, 0x98, "descriptor"),
                (address, 1800, 0x98, "descriptor"),
                (address, 1304, 0xd8, "descriptor"),
            ):
                with self.subTest(resource=name, address=wrong_address,
                                  maximum=maximum, modes=modes):
                    changed = tuple(
                        (name, wrong_address, maximum, modes, row[4])
                        if row[0] == name else row for row in subject.ADDITIONS)
                    with mock.patch.object(subject, "ADDITIONS", changed):
                        with self.assertRaisesRegex(subject.InvalidDB, error):
                            self.transform()

    def test_phy_descriptor_cannot_be_a_clock_with_the_same_address(self):
        for name, _address, _operating_mv in PHY_PROVIDERS.values():
            with self.subTest(resource=name):
                regulator = ('"' + name + '", RPMH_VRM_REGULATOR').encode()
                clock = ('"' + name + '", RPMH_VRM_CLOCK').encode()
                self.assertEqual(1, DESCRIPTOR_BYTES.count(regulator))
                changed = DESCRIPTOR_BYTES.replace(regulator, clock, 1)
                # Admit only this local negative fixture past the pin check,
                # so the independent resource-kind check must reject it.
                with mock.patch.object(subject, "DESCRIPTOR_SHA256", subject.sha256(changed)):
                    with self.assertRaisesRegex(subject.InvalidDB, "differs from existing QEMU descriptor"):
                        subject.extend_bytes(
                            self.original, descriptor_bytes=changed,
                            expected_sha256=subject.sha256(self.original))

    def test_selected_phy_voltage_must_fit_the_existing_descriptor(self):
        for name, _address, _operating_mv in PHY_PROVIDERS.values():
            for operating_mv in (0, 1305):
                with self.subTest(resource=name, operating_mv=operating_mv):
                    with mock.patch.dict(subject.OPERATING_MV, {name: operating_mv}):
                        with self.assertRaisesRegex(subject.InvalidDB, "selected digital voltage"):
                            self.transform()

    def test_descriptor_byte_change_is_rejected(self):
        with self.assertRaisesRegex(subject.InvalidDB, "descriptor SHA256"):
            subject.extend_bytes(self.original, descriptor_bytes=DESCRIPTOR_BYTES + b" ",
                                 expected_sha256=subject.sha256(self.original))

    def test_existing_resource_collision_with_phy_address_is_rejected(self):
        data = bytearray(self.original)
        struct.pack_into("<I", data, 2436 + 3 * 24 + 16, 0x4e130)
        with self.assertRaisesRegex(subject.InvalidDB, "synthetic resource collides"):
            self.transform(data)


class FileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = fixture()
        self.source = self.root / "synthetic-input.bin"
        self.source.write_bytes(self.data)
        self.source.chmod(0o444)
        self.destination = self.root / "new-output"

    def write_fixture(self, **kwargs):
        return subject.write_extension(
            kwargs.get("source", self.source), kwargs.get("destination", self.destination),
            descriptor_path=DESCRIPTOR, expected_sha256=subject.sha256(self.data))

    def test_valid_exclusive_output_preserves_read_only_input(self):
        before = self.source.stat()
        manifest = self.write_fixture()
        self.assertEqual(self.data, self.source.read_bytes())
        self.assertEqual(before.st_mtime_ns, self.source.stat().st_mtime_ns)
        saved = json.loads((self.destination / "manifest.json").read_text())
        self.assertEqual(manifest, saved)
        output = (self.destination / subject.BINARY_NAME).read_bytes()
        self.assertEqual(saved["output"]["sha256"], subject.sha256(output))
        self.assertEqual(81, len(subject.parse_db(output).records))

    def test_bad_hash_fails_before_output_creation(self):
        with self.assertRaisesRegex(subject.InvalidDB, "SHA256"):
            subject.write_extension(self.source, self.destination, descriptor_path=DESCRIPTOR)
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.data, self.source.read_bytes())

    def test_changed_descriptor_fails_before_any_output_is_created(self):
        descriptor = self.root / "different-descriptor.inc"
        descriptor.write_bytes(DESCRIPTOR_BYTES + b" ")
        with self.assertRaisesRegex(subject.InvalidDB, "descriptor SHA256"):
            subject.write_extension(self.source, self.destination,
                                    descriptor_path=descriptor,
                                    expected_sha256=subject.sha256(self.data))
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.data, self.source.read_bytes())

    def test_existing_output_is_not_clobbered(self):
        self.destination.mkdir()
        sentinel = self.destination / "keep"
        sentinel.write_text("unchanged")
        with self.assertRaisesRegex(subject.InvalidDB, "already exists"):
            self.write_fixture()
        self.assertEqual("unchanged", sentinel.read_text())
        self.assertEqual(["keep"], [x.name for x in self.destination.iterdir()])

    def test_input_symlink_is_rejected_without_output(self):
        link = self.root / "link.bin"
        link.symlink_to(self.source)
        with self.assertRaisesRegex(subject.InvalidDB, "canonical"):
            self.write_fixture(source=link)
        self.assertFalse(self.destination.exists())

    def test_output_symlink_parent_is_rejected(self):
        parent = self.root / "link-parent"
        parent.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(subject.InvalidDB, "canonical"):
            self.write_fixture(destination=parent / "uncreated")
        self.assertFalse((self.root / "uncreated").exists())

    def test_non_regular_fifo_is_rejected_without_blocking(self):
        fifo = self.root / "fifo"
        os.mkfifo(fifo)
        with self.assertRaisesRegex(subject.InvalidDB, "regular file"):
            self.write_fixture(source=fifo)
        self.assertFalse(self.destination.exists())

    def test_cli_has_no_fixture_hash_override(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                subject.main(["--input", str(self.source), "--output-directory",
                              str(self.destination), "--expected-sha256", "0" * 64])
        self.assertEqual(2, caught.exception.code)
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
