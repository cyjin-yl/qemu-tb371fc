#!/usr/bin/env python3
"""Independent raw GPT fixtures and fail-closed metadata inventory checks."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock
import uuid
import zlib

import inspect_ufs_gpt as inv


DISK_ID = uuid.UUID('11111111-2222-3333-4444-555555555555')
TYPE_ID = uuid.UUID('ebd0a0a2-b9e5-4433-87c0-68b6b72699c7')
PART_IDS = [uuid.UUID('11111111-2222-3333-4444-555555555556'),
            uuid.UUID('11111111-2222-3333-4444-555555555557')]


def header_crc(raw, block_bytes, lba):
    start = block_bytes*lba
    size = struct.unpack_from('<I', raw, start+12)[0]
    struct.pack_into('<I', raw, start+16, 0)
    struct.pack_into('<I', raw, start+16, zlib.crc32(raw[start:start+size]))


def table_locations(raw, block_bytes):
    return [struct.unpack_from('<Q', raw, lba*block_bytes+72)[0]*block_bytes
            for lba in (1, len(raw)//block_bytes-1)]


def update_arrays(raw, block_bytes, array):
    for start in table_locations(raw, block_bytes):
        raw[start:start+len(array)] = array
    for lba in (1, len(raw)//block_bytes-1):
        struct.pack_into('<I', raw, lba*block_bytes+88, zlib.crc32(array))
        header_crc(raw, block_bytes, lba)


def fixture(block_bytes=4096, count=32, entry_bytes=128, total_lbas=128):
    # Literal on-disk offsets come from UEFI chapter 5. No parser helper builds
    # these bytes. Small arrays retain the required 16 KiB reservation.
    raw = bytearray(block_bytes*total_lbas)
    reserve = (max(16384, count*entry_bytes)+block_bytes-1)//block_bytes
    first, last = 2+reserve, total_lbas-2-reserve
    raw[446+1:446+4] = b'\x00\x02\x00'
    raw[446+4] = 0xee
    raw[446+5:446+8] = b'\xff\xff\xff'
    struct.pack_into('<II', raw, 446+8, 1, total_lbas-1)
    raw[510:512] = b'\x55\xaa'
    entries = bytearray(count*entry_bytes)
    for index, (name, start, end) in enumerate((('super', first, first+5),
                                               ('vbmeta_system_a', first+6, first+7))):
        offset = index*entry_bytes
        entries[offset:offset+16] = TYPE_ID.bytes_le
        entries[offset+16:offset+32] = PART_IDS[index].bytes_le
        struct.pack_into('<QQQ', entries, offset+32, start, end, 1 << 60)
        name_bytes = name.encode('utf-16-le')
        entries[offset+56:offset+56+len(name_bytes)] = name_bytes
    for lba, alt, table_lba in ((1, total_lbas-1, 2),
                               (total_lbas-1, 1, total_lbas-1-reserve)):
        start = lba*block_bytes
        raw[start:start+8] = b'EFI PART'
        struct.pack_into('<III', raw, start+8, 0x10000, 92, 0)
        struct.pack_into('<QQQQ', raw, start+24, lba, alt, first, last)
        raw[start+56:start+72] = DISK_ID.bytes_le
        struct.pack_into('<QIII', raw, start+72, table_lba, count, entry_bytes,
                         zlib.crc32(entries))
        raw[table_lba*block_bytes:table_lba*block_bytes+len(entries)] = entries
        header_crc(raw, block_bytes, lba)
    return raw


def crc_collision_patch(data, offset, target):
    """Set four fixture bytes using GF(2); exercise byte comparison past CRCs."""
    data[offset:offset+4] = bytes(4)
    baseline = zlib.crc32(data)
    basis = {}
    for bit in range(32):
        probe = bytearray(data)
        probe[offset+bit//8] ^= 1 << (bit % 8)
        vector, mask = zlib.crc32(probe)^baseline, 1 << bit
        while vector:
            pivot = vector.bit_length()-1
            if pivot in basis:
                vector ^= basis[pivot][0]
                mask ^= basis[pivot][1]
            else:
                basis[pivot] = vector, mask
                break
    vector, solution = target^baseline, 0
    while vector:
        pivot = vector.bit_length()-1
        row, mask = basis[pivot]
        vector ^= row
        solution ^= mask
    data[offset:offset+4] = solution.to_bytes(4, 'little')
    assert zlib.crc32(data) == target


class GPTInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root/'lun.raw'

    def inspect(self, raw, block_bytes=4096, **kwargs):
        self.path.write_bytes(raw)
        return inv.inspect(self.path, block_bytes, **kwargs)

    def reject(self, raw, code, block_bytes=4096, **kwargs):
        with self.assertRaises(inv.InvalidGPT) as raised:
            self.inspect(raw, block_bytes, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def change_header(self, raw, offset, fmt, value, side='primary', block_bytes=4096):
        lba = 1 if side == 'primary' else len(raw)//block_bytes-1
        struct.pack_into(fmt, raw, lba*block_bytes+offset, value)
        header_crc(raw, block_bytes, lba)

    def change_entry(self, raw, offset, payload, block_bytes=4096):
        primary = table_locations(raw, block_bytes)[0]
        count, width = struct.unpack_from('<II', raw, block_bytes+80)
        array = bytearray(raw[primary:primary+count*width])
        array[offset:offset+len(payload)] = payload
        update_arrays(raw, block_bytes, array)

    def test_actual_target_4k_geometry_with_32_and_64_entries(self):
        for count in (32, 64):
            with self.subTest(count=count):
                report = self.inspect(fixture(count=count), required_names=['super', 'vbmeta_system_a'])
                self.assertEqual(report['headers']['primary']['first_usable_lba'], 6)
                self.assertEqual(report['headers']['backup']['array_lba'], 123)
                self.assertEqual(report['partition_count'], 2)
                self.assertEqual(report['metadata_bytes_per_pass'], 3*4096+2*128*count)
                self.assertTrue(report['gpt_metadata_verified'])
                self.assertTrue(report['required_partition_labels_verified'])
                self.assertEqual(report['partitions'][0]['offset_bytes'], 6*4096)
                self.assertEqual(report['partitions'][0]['size_bytes'], 6*4096)
                self.assertEqual(report['partitions'][0]['type_guid'], str(TYPE_ID))
                self.assertTrue(report['metadata_observed_unchanged'])
                for flag in ('full_lun_content_hashed', 'partition_contents_verified',
                             'complete_backup_verified', 'atomic_snapshot_verified', 'android_boot_verified'):
                    self.assertFalse(report[flag])

    def test_512_byte_logical_blocks(self):
        report = self.inspect(fixture(512), 512)
        self.assertEqual(report['headers']['primary']['first_usable_lba'], 34)
        self.assertEqual(report['partitions'][0]['offset_bytes'], 34*512)

    def test_extended_entry_and_unaligned_array_byte_count(self):
        report = self.inspect(fixture(count=65, entry_bytes=256))
        self.assertEqual(report['headers']['primary']['array_bytes'], 65*256)
        self.assertEqual(report['headers']['primary']['first_usable_lba'], 7)

    def test_unused_regions_and_partition_bytes_are_not_read_or_certified(self):
        raw = fixture()
        report = self.inspect(raw)
        raw[8*4096:8*4096+20] = b'private-user-content'
        changed = self.inspect(raw)
        self.assertEqual(report['metadata_sha256'], changed['metadata_sha256'])
        self.assertNotIn('private-user-content', json.dumps(changed))
        self.assertFalse(changed['full_lun_content_hashed'])

    def test_only_five_declared_metadata_regions_are_read_twice(self):
        raw = fixture()
        self.path.write_bytes(raw)
        original = inv.os.pread
        with mock.patch.object(inv.os, 'pread', wraps=original) as reads:
            report = inv.inspect(self.path, 4096)
        expected = [(x['offset_bytes'], x['size_bytes']) for x in report['observed_regions']]
        self.assertEqual([(c.args[2], c.args[1]) for c in reads.call_args_list], expected*2)
        self.assertEqual(self.path.read_bytes(), raw)

    def test_unique_guids_never_appear_in_report(self):
        report = self.inspect(fixture())
        text = json.dumps(report)
        for guid in (DISK_ID, *PART_IDS):
            self.assertNotIn(str(guid), text)
            self.assertNotIn(guid.bytes_le.hex(), text)
        self.assertEqual(report['disk_guid_sha256'], hashlib.sha256(DISK_ID.bytes_le).hexdigest())

    def test_required_labels_are_not_inferred_from_valid_gpt(self):
        report = self.inspect(fixture())
        self.assertFalse(report['required_partition_labels_verified'])
        self.reject(fixture(), 'missing_partition', required_names=['super', 'metadata'])

    def test_explicit_raw_file_block_size_and_type_are_required(self):
        self.path.write_bytes(fixture())
        with self.assertRaises(inv.InvalidGPT) as raised:
            inv.inspect(self.path)
        self.assertEqual(raised.exception.code, 'block_size_required')
        link = self.root/'symlink.raw'
        link.symlink_to(self.path)
        with self.assertRaises(inv.InvalidGPT) as raised:
            inv.inspect(link, 4096)
        self.assertEqual(raised.exception.code, 'input_type')
        fifo = self.root/'fifo.raw'
        inv.os.mkfifo(fifo)
        with self.assertRaises(inv.InvalidGPT):
            inv.inspect(fifo, 4096)

    def test_container_formats_are_rejected_without_expansion(self):
        for magic, code in ((b'\x3a\xff\x26\xed', 'android_sparse'), (b'QFI\xfb', 'qcow2')):
            raw = fixture()
            raw[:4] = magic
            self.reject(raw, code)

    def test_truncated_or_wrong_logical_geometry_is_rejected(self):
        self.reject(fixture()[:-1], 'input_geometry')
        self.reject(fixture()[:-4096], 'pmbr_extent')
        self.reject(fixture(), 'pmbr_extent', 512)

    def test_pmbr_signature_extent_and_hybrid_are_rejected(self):
        for mutate, code in ((lambda r: r.__setitem__(510, 0), 'pmbr_signature'),
                             (lambda r: struct.pack_into('<I', r, 454, 2), 'pmbr_extent'),
                             (lambda r: r.__setitem__(462+4, 0x83), 'pmbr_records'),
                             (lambda r: r.__setitem__(444, 1), 'pmbr_reserved')):
            raw = fixture()
            mutate(raw)
            self.reject(raw, code)

    def test_saturated_pmbr_requires_explicit_compatibility_and_records_extent(self):
        raw = fixture()
        struct.pack_into('<I', raw, 458, 0xffffffff)
        self.reject(raw, 'pmbr_extent')
        report = self.inspect(raw, pmbr_policy='allow-saturated-size')
        pmbr = report['protective_mbr']
        self.assertEqual(pmbr['actual_size_lbas'], 0xffffffff)
        self.assertEqual(pmbr['expected_size_lbas'], 127)
        self.assertEqual(pmbr['extent_policy'], 'allow-saturated-size')
        self.assertFalse(pmbr['pmbr_standard_extent'])
        self.assertTrue(pmbr['sentinel_compatibility'])
        self.assertTrue(report['gpt_metadata_verified'])
        self.assertFalse(report['complete_backup_verified'])
        self.assertFalse(report['android_boot_verified'])

    def test_saturated_policy_rejects_other_mismatches_and_wrong_start(self):
        for value in (0, 1, 126, 128, 0xfffffffe):
            raw = fixture()
            struct.pack_into('<I', raw, 458, value)
            self.reject(raw, 'pmbr_extent', pmbr_policy='allow-saturated-size')
        raw = fixture()
        struct.pack_into('<II', raw, 454, 2, 0xffffffff)
        self.reject(raw, 'pmbr_extent', pmbr_policy='allow-saturated-size')
        self.reject(fixture(), 'pmbr_policy', pmbr_policy='anything')

    def test_saturated_policy_preserves_all_other_metadata_gates(self):
        cases = ((lambda r: r.__setitem__(510, 0), 'pmbr_signature'),
                 (lambda r: r.__setitem__(462+4, 0x83), 'pmbr_records'),
                 (lambda r: r.__setitem__(4096+16, r[4096+16]^1), 'header_crc'),
                 (lambda r: self.change_header(r, 72, '<Q', 127, 'backup'), 'array_geometry'),
                 (lambda r: r.__setitem__(8192+56, r[8192+56]^1), 'array_crc'),
                 (lambda r: self.change_entry(r, 128+32, struct.pack('<Q', 11)), 'partition_overlap'))
        for mutate, code in cases:
            raw = fixture()
            struct.pack_into('<I', raw, 458, 0xffffffff)
            mutate(raw)
            self.reject(raw, code, pmbr_policy='allow-saturated-size')

    def test_standard_extent_is_not_reported_as_a_compatibility_exception(self):
        for policy in ('strict', 'allow-saturated-size'):
            pmbr = self.inspect(fixture(), pmbr_policy=policy)['protective_mbr']
            self.assertTrue(pmbr['pmbr_standard_extent'])
            self.assertFalse(pmbr['sentinel_compatibility'])
        raw = fixture()[:4096]
        struct.pack_into('<I', raw, 458, 0xffffffff)
        for total in (0x100000000, 0x100000001):
            pmbr = inv.check_pmbr(raw, total)
            self.assertTrue(pmbr['pmbr_standard_extent'])
            self.assertFalse(pmbr['sentinel_compatibility'])

    def test_cli_saturated_policy_is_explicit_and_default_stays_strict(self):
        raw = fixture()
        struct.pack_into('<I', raw, 458, 0xffffffff)
        self.path.write_bytes(raw)
        args = ['--input', str(self.path), '--logical-block-size', '4096']
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inv.main(args), 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'pmbr_extent')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inv.main(args+['--pmbr-policy', 'allow-saturated-size']), 0)
        self.assertTrue(json.loads(output.getvalue())['protective_mbr']['sentinel_compatibility'])

    def add_type_zero_residue(self, raw, width=128):
        entry = bytearray(width)
        entry[16:32] = PART_IDS[0].bytes_le  # Opaque; not a duplicate active ID.
        struct.pack_into('<QQQ', entry, 32, 934, 2042, 0x1000000000000000)
        entry[56:76] = 'stale-part'.encode('utf-16-le')
        self.change_entry(raw, 2*width, entry)
        return entry

    def test_type_zero_residue_requires_opt_in_and_is_not_a_partition(self):
        raw = fixture()
        entry = self.add_type_zero_residue(raw)
        self.reject(raw, 'unused_entry')
        report = self.inspect(raw, unused_entry_policy='allow-type-zero-residue')
        self.assertEqual(report['partition_count'], 2)
        self.assertEqual(report['unused_entry_count'], 30)
        self.assertEqual(report['unused_zero_entry_count'], 29)
        self.assertEqual(report['unused_type_zero_residue_count'], 1)
        residue = report['unused_type_zero_residues'][0]
        self.assertEqual(residue['entry_index'], 3)
        self.assertEqual(residue['record_sha256'], hashlib.sha256(entry).hexdigest())
        self.assertEqual(set(residue['nonzero_fields']),
                         {'unique_guid', 'first_lba', 'last_lba', 'attributes', 'name_utf16'})
        self.assertEqual(residue['nonzero_fields']['first_lba'],
                         {'offset_bytes': 32, 'size_bytes': 8,
                          'sha256': hashlib.sha256(struct.pack('<Q', 934)).hexdigest()})
        self.assertFalse(residue['stale_fields_interpreted'])
        self.assertFalse(residue['included_in_partitions'])
        self.assertNotIn('stale-part', json.dumps(report))
        self.assertNotIn(str(PART_IDS[0]), json.dumps(report))
        self.reject(raw, 'missing_partition', unused_entry_policy='allow-type-zero-residue',
                    required_names=['stale-part'])
        self.assertTrue(self.inspect(raw, unused_entry_policy='allow-type-zero-residue',
                                     required_names=['super'])['required_partition_labels_verified'])

    def test_nonzero_type_never_gets_the_residue_exception(self):
        raw = fixture()
        entry = self.add_type_zero_residue(raw)
        entry[0] = 1
        entry[16:32] = uuid.UUID('11111111-2222-3333-4444-555555555558').bytes_le
        self.change_entry(raw, 256, entry)
        self.reject(raw, 'partition_bounds', unused_entry_policy='allow-type-zero-residue')
        raw = fixture()
        self.change_entry(raw, 128+32, struct.pack('<Q', 11))
        self.reject(raw, 'partition_overlap', unused_entry_policy='allow-type-zero-residue')

    def test_unused_standard_fields_are_opaque_but_extended_reserved_is_not_exempt(self):
        raw = fixture()
        entry = self.add_type_zero_residue(raw)
        struct.pack_into('<QQQ', entry, 32, 0xffffffffffffffff, 0, 0xffffffffffffffff)
        entry[56:128] = b'\x00\xd8'*36  # No valid UTF-16 label; still unused bytes.
        self.change_entry(raw, 256, entry)
        report = self.inspect(raw, unused_entry_policy='allow-type-zero-residue')
        self.assertEqual(report['partition_count'], 2)
        self.assertEqual(report['unused_type_zero_residue_count'], 1)
        raw = fixture(entry_bytes=256)
        entry = self.add_type_zero_residue(raw, 256)
        entry[128] = 1
        self.change_entry(raw, 512, entry)
        self.reject(raw, 'entry_reserved', unused_entry_policy='allow-type-zero-residue')

    def test_residue_bytes_still_require_crc_and_identical_array_copies(self):
        raw = fixture()
        self.add_type_zero_residue(raw)
        start, backup_start = table_locations(raw, 4096)
        bad_crc = bytearray(raw)
        bad_crc[start+256+56] ^= 1
        self.reject(bad_crc, 'array_crc', unused_entry_policy='allow-type-zero-residue')
        backup = bytearray(raw[backup_start:backup_start+4096])
        backup[256+56] ^= 1
        crc_collision_patch(backup, 4092, zlib.crc32(raw[start:start+4096]))
        raw[backup_start:backup_start+4096] = backup
        self.reject(raw, 'array_disagreement', unused_entry_policy='allow-type-zero-residue')

    def test_residue_is_included_in_actual_second_read(self):
        raw = fixture()
        self.add_type_zero_residue(raw)
        self.path.write_bytes(raw)
        original = inv.partitions_from_array
        def mutate(*args):
            result = original(*args)
            with self.path.open('r+b') as output:
                output.seek(8192+256+56)
                output.write(b'X')
            return result
        with mock.patch.object(inv, 'partitions_from_array', side_effect=mutate):
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, 4096, unused_entry_policy='allow-type-zero-residue')
        self.assertEqual(raised.exception.code, 'metadata_changed')

    def test_cli_combines_two_explicit_policies_without_changing_defaults(self):
        raw = fixture()
        struct.pack_into('<I', raw, 458, 0xffffffff)
        self.add_type_zero_residue(raw)
        self.path.write_bytes(raw)
        args = ['--input', str(self.path), '--logical-block-size', '4096',
                '--pmbr-policy', 'allow-saturated-size']
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inv.main(args), 1)
        self.assertEqual(json.loads(output.getvalue())['error_code'], 'unused_entry')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(inv.main(args+['--unused-entry-policy', 'allow-type-zero-residue']), 0)
        report = json.loads(output.getvalue())
        self.assertTrue(report['protective_mbr']['sentinel_compatibility'])
        self.assertEqual(report['unused_type_zero_residue_count'], 1)
        self.assertFalse(report['android_boot_verified'])
        self.reject(fixture(), 'unused_entry_policy', unused_entry_policy='anything')

    def test_each_header_crc_and_signature_is_checked(self):
        for side in (1, 127):
            raw = fixture()
            raw[side*4096+16] ^= 1
            self.reject(raw, 'header_crc')
            raw = fixture()
            raw[side*4096] ^= 1
            self.reject(raw, 'header_signature')

    def test_header_revision_reserved_and_self_locations(self):
        for offset, fmt, value, code in ((8, '<I', 0x20000, 'header_revision'),
                                         (20, '<I', 1, 'header_reserved'),
                                         (24, '<Q', 2, 'header_lbas'),
                                         (32, '<Q', 126, 'header_lbas')):
            raw = fixture()
            self.change_header(raw, offset, fmt, value)
            self.reject(raw, code)
        raw = fixture()
        raw[4096+100] = 1
        self.reject(raw, 'header_reserved')

    def test_future_header_sizes_and_zero_reserved_bytes(self):
        raw = fixture()
        for side in ('primary', 'backup'):
            self.change_header(raw, 12, '<I', 128, side=side)
        self.assertTrue(self.inspect(raw)['gpt_metadata_verified'])
        raw[4096+100] = 1
        header_crc(raw, 4096, 1)
        self.reject(raw, 'header_reserved')

    def test_small_or_huge_header_size_is_rejected_before_crc(self):
        for size in (0, 91, 4097, 0xffffffff):
            raw = fixture()
            struct.pack_into('<I', raw, 4096+12, size)
            self.reject(raw, 'header_size')

    def test_entry_count_size_and_resource_limit(self):
        for offset, value, code in ((80, 0, 'entry_geometry'),
                                     (84, 120, 'entry_geometry'),
                                     (84, 384, 'entry_geometry'),
                                     (80, 0xffffffff, 'array_limit')):
            raw = fixture()
            self.change_header(raw, offset, '<I', value)
            self.reject(raw, code)

    def test_usable_and_array_geometry_cannot_alias_data_or_headers(self):
        for offset, value, side, code in ((40, 3, 'primary', 'usable_geometry'),
                                          (48, 126, 'backup', 'usable_geometry'),
                                          (72, 1, 'primary', 'array_geometry'),
                                          (72, 6, 'primary', 'array_geometry'),
                                          (72, 122, 'backup', 'array_geometry'),
                                          (72, 127, 'backup', 'array_geometry')):
            raw = fixture()
            self.change_header(raw, offset, '<Q', value, side)
            self.reject(raw, code)

    def test_header_copies_must_agree_even_when_both_crcs_pass(self):
        for offset, fmt, value in ((40, '<Q', 7), (48, '<Q', 121), (80, '<I', 31)):
            raw = fixture()
            self.change_header(raw, offset, fmt, value, 'backup')
            self.reject(raw, 'header_disagreement')

    def test_each_array_crc_is_checked(self):
        for array_start in table_locations(fixture(), 4096):
            raw = fixture()
            raw[array_start+56] ^= 1
            self.reject(raw, 'array_crc')

    def test_real_crc_collision_still_fails_byte_comparison(self):
        raw = fixture()
        a, b = table_locations(raw, 4096)
        primary = raw[a:a+4096]
        backup = bytearray(primary)
        backup[56] ^= 1
        crc_collision_patch(backup, len(backup)-4, zlib.crc32(primary))
        self.assertNotEqual(primary, backup)
        self.assertEqual(zlib.crc32(primary), zlib.crc32(backup))
        raw[b:b+4096] = backup
        self.reject(raw, 'array_disagreement')

    def test_partition_bounds_and_inclusive_overlap(self):
        for offset, value, code in ((32, 5, 'partition_bounds'),
                                     (40, 123, 'partition_bounds'),
                                     (40, 5, 'partition_bounds'),
                                     (128+32, 11, 'partition_overlap')):
            raw = fixture()
            self.change_entry(raw, offset, struct.pack('<Q', value))
            self.reject(raw, code)

    def test_zero_duplicate_ids_and_nonzero_unused_entries_fail(self):
        for offset, payload, code in ((16, bytes(16), 'partition_guid'),
                                      (128+16, PART_IDS[0].bytes_le, 'partition_guid'),
                                      (256+16, b'x', 'unused_entry')):
            raw = fixture()
            self.change_entry(raw, offset, payload)
            self.reject(raw, code)

    def test_zero_disk_guid_fails(self):
        raw = fixture()
        raw[4096+56:4096+72] = bytes(16)
        header_crc(raw, 4096, 1)
        self.reject(raw, 'disk_guid')

    def test_names_are_strict_utf16_and_nonempty_labels_are_unique(self):
        for offset, payload, code in ((56, b'A\x00'*36, 'partition_name'),
                                      (56, b'\x00\xd8\x00\x00'+bytes(68), 'partition_name'),
                                      (56, b'A\x00\x00\x00B\x00'+bytes(66), 'partition_name'),
                                      (56, b'\x0a\x00'+bytes(70), 'partition_name'),
                                      (128+56, 'super'.encode('utf-16-le')+bytes(62), 'duplicate_name')):
            raw = fixture()
            self.change_entry(raw, offset, payload)
            self.reject(raw, code)

    def test_reserved_attributes_and_extended_entry_bytes_fail(self):
        raw = fixture()
        self.change_entry(raw, 48, struct.pack('<Q', 1 << 5))
        self.reject(raw, 'partition_attributes')
        raw = fixture(entry_bytes=256)
        self.change_entry(raw, 128, b'x')
        self.reject(raw, 'entry_reserved')

    def test_late_real_metadata_mutation_fails(self):
        self.path.write_bytes(fixture())
        original = inv.partitions_from_array
        def mutate(*args):
            result = original(*args)
            with self.path.open('r+b') as output:
                output.seek(510)
                output.write(b'\x00')
            return result
        with mock.patch.object(inv, 'partitions_from_array', side_effect=mutate):
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, 4096)
        self.assertEqual(raised.exception.code, 'metadata_changed')

    def test_path_replacement_fails_even_with_identical_metadata(self):
        self.path.write_bytes(fixture())
        original = inv.partitions_from_array
        def replace(*args):
            result = original(*args)
            replacement = self.root/'replacement.raw'
            replacement.write_bytes(fixture())
            replacement.replace(self.path)
            return result
        with mock.patch.object(inv, 'partitions_from_array', side_effect=replace):
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, 4096)
        self.assertEqual(raised.exception.code, 'input_changed')

    def test_short_pread_is_completed_and_eof_is_rejected(self):
        raw = fixture()
        self.path.write_bytes(raw)
        original = inv.os.pread
        with mock.patch.object(inv.os, 'pread', side_effect=lambda fd, n, off: original(fd, min(n, 37), off)):
            self.assertTrue(inv.inspect(self.path, 4096)['gpt_metadata_verified'])
        with mock.patch.object(inv.os, 'pread', return_value=b''):
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, 4096)
        self.assertEqual(raised.exception.code, 'short_read')

    def test_block_devices_require_opt_in_and_use_both_ioctls_twice(self):
        import fcntl
        self.path.write_bytes(fixture())
        def ioctl(fd, request, buffer):
            self.assertGreaterEqual(fd, 0)
            return struct.pack('=I', 4096) if request == 0x1268 else struct.pack('=Q', self.path.stat().st_size)
        with mock.patch.object(inv.stat, 'S_ISREG', return_value=False), \
                mock.patch.object(inv.stat, 'S_ISBLK', return_value=True), \
                mock.patch.object(fcntl, 'ioctl', side_effect=ioctl) as calls:
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, 4096)
            self.assertEqual(raised.exception.code, 'input_type')
            self.assertEqual(calls.call_count, 0)
            report = inv.inspect(self.path, allow_block_device=True)
            self.assertEqual(report['geometry_source'], 'BLKSSZGET+BLKGETSIZE64')
            self.assertEqual([c.args[1] for c in calls.call_args_list],
                             [0x1268, 0x80081272, 0x1268, 0x80081272])
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, 512, allow_block_device=True)
            self.assertEqual(raised.exception.code, 'block_size_mismatch')

    def test_block_geometry_change_is_rejected(self):
        self.path.write_bytes(fixture())
        with mock.patch.object(inv.stat, 'S_ISREG', return_value=False), \
                mock.patch.object(inv.stat, 'S_ISBLK', return_value=True), \
                mock.patch.object(inv, 'block_geometry', side_effect=[(4096, 524288), (4096, 528384)]):
            with self.assertRaises(inv.InvalidGPT) as raised:
                inv.inspect(self.path, allow_block_device=True)
            self.assertEqual(raised.exception.code, 'input_changed')

    def test_cli_preserves_existing_receipts_and_records_failure(self):
        self.path.write_bytes(fixture())
        output = self.root/'report.json'
        code = inv.main(['--input', str(self.path), '--logical-block-size', '4096',
                         '--require-partition', 'metadata', '--output', str(output)])
        self.assertEqual(code, 1)
        report = json.loads(output.read_text())
        self.assertEqual(report['error_code'], 'missing_partition')
        self.assertFalse(report['gpt_metadata_verified'])
        original = output.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            inv.main(['--input', str(self.path), '--logical-block-size', '4096', '--output', str(output)])
        self.assertEqual(output.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
