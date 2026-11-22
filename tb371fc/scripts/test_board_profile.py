#!/usr/bin/env python3
"""Offline evidence tests; no device access, no native register probing."""

import copy
import hashlib
import json
from pathlib import Path
import struct
import tempfile
import unittest

from collect_board import collect_dt, encode_property
from board_profile import InvalidEvidence, decode_reg, normalize, verify_capture

ROOT = Path(__file__).resolve().parent.parent
CAPTURE = ROOT / 'evidence/android-board.json'


def packed(*values):
    return encode_property('reg', struct.pack(f'>{len(values)}I', *values))


class RegTests(unittest.TestCase):
    def test_64_bit_banks_and_hole(self):
        actual = decode_reg(packed(0, 0x80000000, 0, 0x3bb00000,
                                   0, 0xc0000000, 1, 0xc0000000), 2, 2)
        self.assertEqual(actual, [
            {'address': 0x80000000, 'size': 0x3bb00000},
            {'address': 0xc0000000, 'size': 0x1c0000000},
        ])
        self.assertEqual(sum(item['size'] for item in actual), 8 * 2**30 - 69 * 2**20)

    def test_cpu_zero_size_cells(self):
        self.assertEqual(decode_reg(packed(0, 0x700), 2, 0), [{'address': 0x700}])

    def test_soc_32_bit_reg(self):
        self.assertEqual(decode_reg(packed(0xa90000, 0x4000), 1, 1),
                         [{'address': 0xa90000, 'size': 0x4000}])

    def test_partial_tuple_rejected(self):
        with self.assertRaisesRegex(InvalidEvidence, 'partial'):
            decode_reg(packed(0, 0x80000000, 0), 2, 2)

    def test_wrong_cell_width_rejected(self):
        with self.assertRaisesRegex(InvalidEvidence, 'cell widths'):
            decode_reg(packed(0, 0), 3, 1)


class CaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.captured = json.loads(CAPTURE.read_text())

    def snapshot(self):
        return copy.deepcopy(self.captured)

    def test_actual_snapshot_identity_and_cpu_order(self):
        result = normalize(self.captured)
        self.assertEqual(result['identity']['ro.product.model'], 'TB371FC')
        self.assertEqual([cpu['mpidr'] for cpu in result['cpus']],
                         list(range(0, 0x800, 0x100)))
        self.assertEqual(result['bus_cells'], {'root': [2, 2], 'soc': [1, 1], 'cpus': [2, 0]})

    def test_actual_console_differs_from_disabled_upstream_uart(self):
        result = normalize(self.captured)
        self.assertEqual(result['enabled_console_candidates'], ['/soc/qcom,qup_uart@a90000'])
        uart = next(x for x in result['uarts'] if x['source'].endswith('@a90000'))
        self.assertEqual(uart['regions'], [{'address': 0xa90000, 'size': 0x4000}])
        self.assertEqual(uart['interrupt'], {'type': 0, 'number': 357, 'flags': 4})
        disabled = next(x for x in result['uarts'] if x['source'].endswith('@988000'))
        self.assertEqual(disabled['status'], 'disabled')
        self.assertIsNone(result['chosen_stdout_path'])

    def test_actual_gic_and_counter_frequency(self):
        result = normalize(self.captured)
        self.assertEqual(result['interrupt_controllers'][0]['regions'], [
            {'address': 0x17a00000, 'size': 0x10000},
            {'address': 0x17a60000, 'size': 0x100000},
        ])
        self.assertEqual(result['interrupt_controllers'][0]['redistributor_stride'], 0x20000)
        self.assertEqual(result['timers'][0]['clock_frequency_hz'], 19200000)
        self.assertEqual(result['psci']['method'], ['smc'])

    def test_extended_irq_not_misparsed_as_gic_triples(self):
        result = normalize(self.captured)
        uart = next(x for x in result['uarts'] if x['source'].endswith('@998000'))
        self.assertEqual(uart['interrupts_extended_raw_cells'], [1, 0, 607, 4, 101, 19, 0])
        self.assertNotIn('interrupt', uart)
        self.assertTrue(any('@998000' in item for item in result['unresolved']))

    def test_wrong_raw_hash_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/memory']['properties']['reg']['sha256'] = '0' * 64
        with self.assertRaisesRegex(InvalidEvidence, 'SHA-256'):
            verify_capture(capture)

    def test_wrong_decoded_cells_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/memory']['properties']['reg']['be32'][1] = 0
        with self.assertRaisesRegex(InvalidEvidence, 'BE32'):
            verify_capture(capture)

    def test_duplicate_cpu_mpidr_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/cpus/cpu@100']['properties']['reg'] = packed(0, 0)
        with self.assertRaisesRegex(InvalidEvidence, 'duplicate CPU'):
            normalize(capture)

    def test_overlapping_ram_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/memory']['properties']['reg'] = packed(
            0, 0x80000000, 0, 0x80000000, 0, 0xc0000000, 1, 0xc0000000)
        with self.assertRaisesRegex(InvalidEvidence, 'overlapping'):
            normalize(capture)

    def test_nonidentity_soc_range_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/soc']['properties']['ranges'] = packed(0, 0, 0x1000, 0xffffffff)
        with self.assertRaisesRegex(InvalidEvidence, 'not an identity'):
            normalize(capture)

    def test_uart_interrupt_parent_is_verified(self):
        capture = self.snapshot()
        capture['dt_nodes']['/soc/qcom,qup_uart@a90000']['properties']['interrupt-parent'] = packed(999)
        with self.assertRaisesRegex(InvalidEvidence, 'parent is not the captured GIC'):
            normalize(capture)

    def test_sensitive_identity_rejected(self):
        capture = self.snapshot()
        capture['identity']['ro.boot.serialno'] = 'not-a-real-secret'
        with self.assertRaisesRegex(InvalidEvidence, 'identity outside'):
            verify_capture(capture)

    def test_bootargs_property_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/chosen']['properties']['bootargs'] = encode_property('bootargs', b'not-a-real-secret\0')
        with self.assertRaisesRegex(InvalidEvidence, 'property outside'):
            verify_capture(capture)

    def test_unknown_value_field_rejected(self):
        capture = self.snapshot()
        capture['dt_nodes']['/memory']['properties']['reg']['extra'] = 'not-a-real-secret'
        with self.assertRaisesRegex(InvalidEvidence, 'unexpected property'):
            verify_capture(capture)

    def test_collector_source_hash_is_current(self):
        current = hashlib.sha256((ROOT / 'scripts/collect_board.py').read_bytes()).hexdigest()
        self.assertEqual(self.captured['collector_sha256'], current)


class AllowlistTests(unittest.TestCase):
    def test_collector_never_captures_omitted_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'chosen').mkdir()
            (root / 'unrelated').mkdir()
            (root / 'serial-number').write_text('SENTINEL_PRIVATE_SERIAL')
            (root / 'chosen/bootargs').write_text('SENTINEL_PRIVATE_BOOTARGS')
            (root / 'unrelated/reg').write_text('SENTINEL_UNRELATED_REG')
            (root / '#address-cells').write_bytes(struct.pack('>I', 2))
            result = collect_dt(root)
            serialized = json.dumps(result)
            self.assertNotIn('SENTINEL', serialized)
            self.assertIn('serial-number', result['/']['omitted_property_names'])
            self.assertIn('bootargs', result['/chosen']['omitted_property_names'])
            self.assertNotIn('/unrelated', result)

    def test_oversized_property_is_unavailable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'compatible').write_bytes(b'A' * 4097)
            result = collect_dt(root)
            self.assertNotIn('compatible', result['/']['properties'])
            self.assertIn('ValueError', result['/']['unavailable']['compatible'])


if __name__ == '__main__':
    unittest.main()
