#!/usr/bin/env python3
"""Offline negative tests for runtime-DT comparison; synthetic fixtures are explicit."""

import copy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from validate_machine import FdtReader, ValidationError, compare, observe_dt, parent, reg_entries

ROOT = Path(__file__).resolve().parent.parent
PROFILE = json.loads((ROOT / 'evidence/board-profile.json').read_text())


def encoded_reg(entries):
    result = []
    for entry in entries:
        address, size = entry['address'], entry['size']
        result += [address >> 32, address & 0xffffffff, size >> 32, size & 0xffffffff]
    return result


class FakeFdtReader:
    """Synthetic DT only for comparison tests; production always uses fdtget."""

    def __init__(self, nodes):
        self.data = copy.deepcopy(nodes)

    def nodes(self):
        return list(self.data)

    def has(self, node, prop):
        return prop in self.data.get(node, {})

    def cells(self, node, prop, default=None):
        if self.has(node, prop):
            return self.data[node][prop]
        if default is not None:
            return default
        raise ValidationError(f'missing {node}:{prop}')

    strings = cells


def synthetic_reader():
    nodes = {
        '/': {'#address-cells': [2], '#size-cells': [2], 'interrupt-parent': [10],
              'compatible': ['qemu,tb371fc', 'lenovo,tb371fc', 'qcom,sm8250']},
        '/chosen': {'stdout-path': ['/soc/geniqup@ac0000/serial@a90000:115200n8']},
        '/cpus': {'#address-cells': [2], '#size-cells': [0]},
        '/memory@80000000': {'device_type': ['memory'], 'reg': encoded_reg(PROFILE['memory'])},
        '/reserved-memory': {'#address-cells': [2], '#size-cells': [2], 'ranges': []},
        '/reserved-memory/gap@bbb00000': {'reg': [0, 0xbbb00000, 0, 0x4500000], 'no-map': []},
        '/psci': {'compatible': ['arm,psci-1.0'], 'method': ['smc']},
        '/soc': {'#address-cells': [2], '#size-cells': [2], 'ranges': [], 'compatible': ['simple-bus']},
        '/soc/intc@17a00000': {'compatible': ['arm,gic-v3'],
                             'reg': encoded_reg(PROFILE['interrupt_controllers'][0]['regions']),
                             '#interrupt-cells': [3], 'phandle': [10],
                             'redistributor-stride': [0, 0x20000]},
        '/timer': {'compatible': ['arm,armv8-timer'], 'clock-frequency': [19200000],
                   'interrupts': [1, 13, 8, 1, 14, 8, 1, 11, 8, 1, 12, 8]},
        '/soc/geniqup@ac0000': {'#address-cells': [2], '#size-cells': [2], 'ranges': []},
        '/soc/geniqup@ac0000/serial@a90000': {
            'compatible': ['qcom,geni-debug-uart'], 'reg': [0, 0xa90000, 0, 0x4000],
            'interrupts': [0, 357, 4], 'status': ['okay']},
    }
    for index in range(8):
        nodes[f'/cpus/cpu@{index * 0x100:x}'] = {
            'device_type': ['cpu'], 'compatible': ['arm,cortex-a55' if index < 4 else 'arm,cortex-a76'],
            'reg': [0, index * 0x100], 'enable-method': ['psci'],
        }
    return FakeFdtReader(nodes)


def synthetic_runtime():
    return {'version': {'qemu': {'major': 11, 'minor': 1, 'micro': 3}, 'package': 'synthetic-test-fixture'},
            'status': {'running': False, 'status': 'prelaunch'},
            'cpus': [{'cpu-index': index, 'mp-affinity': index * 0x100,
                      'qom-type': 'cortex-a55-arm-cpu' if index < 4 else 'cortex-a76-arm-cpu',
                      'midr': 0x412fd050 if index < 4 else 0x414fd0b1, 'cntfrq': 19200000}
                     for index in range(8)]}


class RuntimeComparisonTests(unittest.TestCase):
    def result(self, reader=None, runtime=None):
        return compare(PROFILE, observe_dt(reader or synthetic_reader()), runtime or synthetic_runtime())

    def assert_failed_check(self, result, name):
        self.assertFalse(result['passed'])
        self.assertFalse(next(item for item in result['checks'] if item['name'] == name)['passed'])

    def test_configuration_pass_does_not_claim_hardware_equivalence(self):
        result = self.result()
        self.assertTrue(result['passed'])
        self.assertFalse(result['hardware_equivalent'])
        self.assertFalse(result['complete_emulation'])
        self.assertEqual({item['name'] for item in result['deviations']}, {
            'hardware.cpu_midr', 'hardware.timer_raw_interrupt_flags', 'hardware.console_driver_binding'})
        self.assertTrue(all(not item['passed'] for item in result['deviations']))

    def test_wrong_console_address_fails(self):
        reader = synthetic_reader()
        reader.data['/soc/geniqup@ac0000/serial@a90000']['reg'][1] = 0x988000
        self.assert_failed_check(self.result(reader), 'dt.console_regions')

    def test_wrong_console_irq_fails(self):
        reader = synthetic_reader()
        reader.data['/soc/geniqup@ac0000/serial@a90000']['interrupts'][1] = 603
        self.assert_failed_check(self.result(reader), 'dt.console_interrupt')

    def test_runtime_affinity_mismatch_cannot_hide_behind_correct_dt(self):
        runtime = synthetic_runtime()
        runtime['cpus'][7]['mp-affinity'] = 0
        self.assert_failed_check(self.result(runtime=runtime), 'qmp.cpu_mpidrs')

    def test_runtime_counter_mismatch_cannot_hide_behind_correct_dt(self):
        runtime = synthetic_runtime()
        runtime['cpus'][0]['cntfrq'] = 1000000000
        self.assert_failed_check(self.result(runtime=runtime), 'qmp.cpu_counter_frequency')

    def test_contiguous_ram_without_exclusion_fails(self):
        reader = synthetic_reader()
        reader.data['/memory@80000000']['reg'] = [0, 0x80000000, 2, 0]
        del reader.data['/reserved-memory/gap@bbb00000']
        self.assert_failed_check(self.result(reader), 'dt.os_visible_memory_banks')

    def test_contiguous_ram_with_explicit_no_map_gap_is_transformed(self):
        reader = synthetic_reader()
        reader.data['/memory@80000000']['reg'] = [0, 0x80000000, 2, 0]
        result = self.result(reader)
        self.assertTrue(result['passed'])
        gap = next(item for item in result['checks'] if item['name'] == 'dt.captured_memory_gap')
        self.assertTrue(gap['observed'][0]['explicit_no_map'])

    def test_split_ram_without_extra_reservation_is_still_excluded(self):
        reader = synthetic_reader()
        del reader.data['/reserved-memory/gap@bbb00000']
        result = self.result(reader)
        self.assertTrue(result['passed'])
        gap = next(item for item in result['checks'] if item['name'] == 'dt.captured_memory_gap')
        self.assertFalse(gap['observed'][0]['explicit_no_map'])

    def test_gic_region_error_fails(self):
        reader = synthetic_reader()
        reader.data['/soc/intc@17a00000']['reg'][5] += 0x20000
        self.assert_failed_check(self.result(reader), 'dt.gic_regions')

    def test_timer_trigger_error_is_not_normalized_away(self):
        reader = synthetic_reader()
        reader.data['/timer']['interrupts'][2] = 4
        self.assert_failed_check(self.result(reader), 'dt.timer_ppi_semantics')

    def test_wrong_interrupt_parent_fails(self):
        reader = synthetic_reader()
        reader.data['/soc/geniqup@ac0000/serial@a90000']['interrupt-parent'] = [999]
        self.assert_failed_check(self.result(reader), 'dt.console_interrupt_parent')

    def test_unreported_version_fails(self):
        runtime = synthetic_runtime()
        runtime['version'] = {}
        self.assert_failed_check(self.result(runtime=runtime), 'qmp.version_observed')

    def test_alias_stdout_path_selects_real_console(self):
        reader = synthetic_reader()
        reader.data['/chosen']['stdout-path'] = ['serial0:115200n8']
        reader.data['/aliases'] = {'serial0': ['/soc/geniqup@ac0000/serial@a90000']}
        self.assertTrue(self.result(reader)['passed'])


class DtDecodingTests(unittest.TestCase):
    def test_bus_cell_widths_may_differ_from_android(self):
        reader = synthetic_reader()
        reader.data['/soc/geniqup@ac0000']['#address-cells'] = [1]
        reader.data['/soc/geniqup@ac0000']['#size-cells'] = [1]
        reader.data['/soc/geniqup@ac0000/serial@a90000']['reg'] = [0xa90000, 0x4000]
        self.assertEqual(reg_entries(reader, '/soc/geniqup@ac0000/serial@a90000'),
                         [{'address': 0xa90000, 'size': 0x4000}])

    def test_missing_ranges_not_assumed_to_be_identity(self):
        reader = synthetic_reader()
        del reader.data['/soc/geniqup@ac0000']['ranges']
        with self.assertRaisesRegex(ValidationError, 'missing'):
            observe_dt(reader)

    def test_nonidentity_ranges_are_translated(self):
        reader = synthetic_reader()
        reader.data['/soc/geniqup@ac0000']['ranges'] = [0, 0x100000, 0, 0xa90000, 0, 0x4000]
        reader.data['/soc/geniqup@ac0000/serial@a90000']['reg'] = [0, 0x100000, 0, 0x4000]
        self.assertEqual(reg_entries(reader, '/soc/geniqup@ac0000/serial@a90000'),
                         [{'address': 0xa90000, 'size': 0x4000}])

    def test_partial_reg_tuple_rejected(self):
        reader = synthetic_reader()
        reader.data['/soc/geniqup@ac0000/serial@a90000']['reg'] = [0, 0xa90000, 0]
        with self.assertRaisesRegex(ValidationError, 'partial'):
            observe_dt(reader)


class CommandIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('dtc') and shutil.which('fdtget'), 'dtc and fdtget are required')
    def test_real_fdtget_decodes_a_compiled_synthetic_dtb(self):
        fixture = synthetic_reader()

        def emit(path, depth=0):
            indent = '    ' * depth
            lines = [indent + ('/' if path == '/' else path.rsplit('/', 1)[1]) + ' {']
            for key, values in fixture.data[path].items():
                if not values:
                    encoded = ''
                elif isinstance(values[0], int):
                    encoded = ' = <' + ' '.join(f'0x{value:x}' for value in values) + '>'
                else:
                    encoded = ' = ' + ', '.join(json.dumps(value) for value in values)
                lines.append(indent + '    ' + key + encoded + ';')
            for child in fixture.data:
                if child != path and parent(child) == path:
                    lines += emit(child, depth + 1)
            lines.append(indent + '};')
            return lines

        with tempfile.TemporaryDirectory() as temporary:
            dts = Path(temporary) / 'synthetic.dts'
            dtb = Path(temporary) / 'synthetic.dtb'
            dts.write_text('/dts-v1/;\n' + '\n'.join(emit('/')))
            subprocess.run(['dtc', '-q', '-I', 'dts', '-O', 'dtb', '-o', str(dtb), str(dts)],
                           check=True, capture_output=True, timeout=10)
            observed = observe_dt(FdtReader(dtb))
            self.assertEqual(observed, observe_dt(fixture))
            self.assertTrue(compare(PROFILE, observed, synthetic_runtime())['passed'])

    def test_missing_emulator_returns_failed_json_not_a_pass(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/validate_machine.py'),
                                 '--qemu', '/tb371fc-test-deliberately-missing/qemu-system-aarch64'],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        payload = json.loads(result.stdout)
        self.assertFalse(payload['passed'])
        self.assertFalse(payload['hardware_equivalent'])
        self.assertIn('FileNotFoundError', payload['error'])


if __name__ == '__main__':
    unittest.main()
