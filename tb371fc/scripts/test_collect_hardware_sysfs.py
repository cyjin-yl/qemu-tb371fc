#!/usr/bin/env python3
import gzip
import json
from pathlib import Path
import tempfile
import unittest

import collect_hardware_sysfs as collector


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.sys = self.root / 'sys'
        self.proc = self.root / 'proc'
        self.sys.mkdir()
        self.proc.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)

    def test_features_discard_serial_and_unrelated_lines(self):
        self.write(self.proc / 'cpuinfo', 'Features : fp asimd cpuid\nSerial : private-secret\n')
        data = collector.collect_features(self.proc)
        self.assertEqual(data['per_cpu_features_lines'], [['fp', 'asimd', 'cpuid']])
        self.assertNotIn('private-secret', json.dumps(data))

    def test_ufs_does_not_collect_string_descriptors(self):
        base = self.sys / 'bus/platform/devices/1d84000.ufshc'
        self.write(base / 'device_descriptor/specification_version', '0x0310\n')
        self.write(base / 'string_descriptors/serial_number', 'private-secret\n')
        self.write(base / 'device_descriptor/manufacturing_date', 'private-date\n')
        data = collector.collect_ufs(self.sys)
        self.assertEqual(data['descriptors']['device_descriptor']['specification_version']['value'], '0x0310')
        self.assertNotIn('private-secret', json.dumps(data))
        self.assertNotIn('private-date', json.dumps(data))

    def test_missing_cache_sizes_are_explicit(self):
        base = self.sys / 'devices/system/cpu/cpu0'
        self.write(base / 'cache/index0/level', '1\n')
        self.write(base / 'cache/index0/type', 'Data\n')
        data = collector.collect_cpus(self.sys)
        self.assertEqual(data['0']['caches']['index0']['size']['reason'], 'absent')
        self.assertFalse(data['1']['available'])

    def test_midr_keeps_raw_text_and_hash(self):
        path = self.sys / 'devices/system/cpu/cpu4/regs/identification/midr_el1'
        self.write(path, '0x00000000411fd0d0\n')
        data = collector.collect_cpus(self.sys)['4']['identification']['midr_el1']
        self.assertEqual(data['value'], '0x00000000411fd0d0')
        self.assertEqual(len(data['sha256']), 64)

    def test_sysfs_nonnumeric_identification_is_rejected(self):
        path = self.root / 'attribute'
        self.write(path, 'not-a-register-value')
        self.assertFalse(collector.read_text(path, numeric=True)['available'])

    def test_existing_clock_summary_filters_names_only(self):
        self.write(self.proc / 'mounts', 'debug /sys/kernel/debug debugfs ro 0 0\n')
        self.write(self.sys / 'kernel/debug/clk/clk_summary',
                   'gcc_usb30_prim_master_clk 1 1 0 240000000\n'
                   'private_unrelated_clock 0 0 0 123\n'
                   'bi_tcxo 3 3 0 19200000\n')
        data = collector.collect_clocks(self.sys, self.proc)
        self.assertTrue(data['available'])
        self.assertEqual([row[0] for row in data['rows']], ['gcc_usb30_prim_master_clk', 'bi_tcxo'])
        self.assertNotIn('private_unrelated_clock', json.dumps(data))
        self.assertFalse(data['writes_or_mounts_performed'])

    def test_debugfs_absence_does_not_trigger_mount(self):
        self.write(self.proc / 'mounts', 'root / ext4 rw 0 0\n')
        data = collector.collect_clocks(self.sys, self.proc)
        self.assertFalse(data['available'])
        self.assertEqual(data['debugfs_mount_count'], 0)
        self.assertFalse(data['writes_or_mounts_performed'])

    def test_bindings_only_include_real_dt_targets(self):
        target = self.sys / 'firmware/devicetree/base/soc/uart'
        target.mkdir(parents=True)
        driver = self.sys / 'bus/platform/drivers/qcom_geni_serial'
        driver.mkdir(parents=True)
        device = self.sys / 'bus/platform/devices/a90000.uart'
        device.mkdir(parents=True)
        (device / 'of_node').symlink_to(target)
        (device / 'driver').symlink_to(driver)
        other = device.parent / 'private-device'
        other.mkdir()
        (other / 'of_node').symlink_to(self.proc)
        data = collector.collect_bindings(self.sys)
        self.assertEqual(data, [{'bus': 'platform', 'device': 'a90000.uart',
                                'dt_node': '/soc/uart', 'bound_driver': 'qcom_geni_serial'}])

    def test_kernel_config_preserves_off_and_missing_distinction(self):
        with gzip.open(self.proc / 'config.gz', 'wt') as handle:
            handle.write('# CONFIG_DEBUG_FS is not set\nCONFIG_MODULES=y\nCONFIG_PRIVATE_KEY="secret"\n')
        data = collector.collect_kernel_config(self.proc)
        self.assertEqual(data['symbols']['CONFIG_DEBUG_FS']['value'], 'n')
        self.assertEqual(data['symbols']['CONFIG_MODULES']['value'], 'y')
        self.assertFalse(data['symbols']['CONFIG_SERIAL_EARLYCON']['available'])
        self.assertNotIn('secret', json.dumps(data))


if __name__ == '__main__':
    unittest.main()
