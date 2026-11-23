#!/usr/bin/env python3
"""Read an explicit hardware-only sysfs allowlist on the owner's Android board.

No MMIO, register writes, driver rebinding, debugfs mounting, raw input, device
serials, network addresses, account data or firmware payloads are accessed.
Root is only needed where the running kernel restricts otherwise readable files.
"""
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re

MAX_TEXT = 8192
CPU_IDENTIFICATION = ('midr_el1', 'revidr_el1', 'aidr_el1')
CACHE_FIELDS = ('level', 'type', 'size', 'ways_of_associativity',
                'coherency_line_size', 'number_of_sets', 'shared_cpu_list')
CONFIG_FIELDS = ('CONFIG_SERIAL_MSM_GENI', 'CONFIG_SERIAL_MSM_GENI_CONSOLE',
                 'CONFIG_SERIAL_MSM_GENI_HALF_SAMPLING', 'CONFIG_SERIAL_EARLYCON',
                 'CONFIG_DEVMEM', 'CONFIG_MODULES', 'CONFIG_MODULE_SIG_FORCE',
                 'CONFIG_DEBUG_FS')
UFS_FIELDS = {
    'device_descriptor': (
        'device_version', 'manufacturer_id', 'device_sub_class',
        'ext_feature_sup', 'ufs_features', 'protocol', 'device_type',
        'number_of_luns', 'specification_version', 'device_class',
        'number_of_wluns', 'rtt_capability', 'queue_depth',
        'wb_type', 'wb_sup_red_type', 'wb_shared_alloc_units',
    ),
    'geometry_descriptor': (
        'segment_size', 'max_number_of_luns', 'raw_device_capacity',
        'allocation_unit_size', 'max_in_buffer_size', 'max_out_buffer_size',
        'min_addressable_block_size', 'optimal_read_block_size',
        'optimal_write_block_size', 'memory_types', 'data_ordering',
        'max_number_of_contexts', 'rpmb_rw_size',
        'wb_max_alloc_units', 'wb_max_wb_luns', 'wb_sup_wb_type',
    ),
    'interconnect_descriptor': ('mphy_version', 'unipro_version'),
    'attributes': ('reference_clock_frequency', 'max_number_of_rtt',
                   'max_data_in_size', 'max_data_out_size'),
    'qcom': ('bus_speed_mode', 'power_mode', 'clk_status'),
}
SAFE_NAME = re.compile(r'^[A-Za-z0-9._,:@+\-]+$')
CLOCK_NAME = re.compile(r'^(?:gcc_[A-Za-z0-9_]*|gpll[A-Za-z0-9_]*|bi_tcxo(?:_ao)?|sleep_clk)$')


def read_text(path: Path, *, numeric=False) -> dict:
    """Return only a bounded allowlisted value, or a value-free failure reason."""
    try:
        with path.open('rb') as handle:
            raw = handle.read(MAX_TEXT + 1)
        if len(raw) > MAX_TEXT:
            return {'available': False, 'reason': 'exceeds size bound'}
        value = raw.decode('ascii').strip()
        if any(ord(c) < 32 and c not in '\t\n' for c in value):
            return {'available': False, 'reason': 'unexpected control character'}
        if numeric and not re.fullmatch(r'(?:0x[0-9a-fA-F]+|[0-9]+)', value):
            return {'available': False, 'reason': 'unexpected numeric encoding'}
        return {'available': True, 'value': value, 'bytes': len(raw),
                'sha256': hashlib.sha256(raw).hexdigest()}
    except FileNotFoundError:
        return {'available': False, 'reason': 'absent'}
    except PermissionError:
        return {'available': False, 'reason': 'permission denied'}
    except (OSError, UnicodeError):
        return {'available': False, 'reason': 'read or encoding error'}


def collect_cpus(sys_root: Path) -> dict:
    result = {}
    for number in range(8):
        cpu = sys_root / 'devices/system/cpu' / f'cpu{number}'
        if not cpu.is_dir():
            result[str(number)] = {'available': False, 'reason': 'CPU directory absent'}
            continue
        item = {'identification': {
            name: read_text(cpu / 'regs/identification' / name, numeric=True)
            for name in CPU_IDENTIFICATION}, 'caches': {}}
        for index in sorted((cpu / 'cache').glob('index*')):
            if not re.fullmatch(r'index[0-9]+', index.name):
                continue
            item['caches'][index.name] = {name: read_text(index / name)
                                            for name in CACHE_FIELDS}
        result[str(number)] = item
    return result


def collect_features(proc_root: Path) -> dict:
    # /proc/cpuinfo can include a machine serial on some kernels. Do not retain
    # the raw file, raw-file hash, or any line except Features.
    try:
        lines = []
        with (proc_root / 'cpuinfo').open('r', encoding='ascii') as handle:
            for line in handle:
                key, separator, value = line.partition(':')
                if separator and key.strip() == 'Features':
                    features = value.split()
                    if any(not re.fullmatch(r'[a-z0-9_]+', f) for f in features):
                        return {'available': False, 'reason': 'unexpected feature name'}
                    lines.append(features)
        return {'available': bool(lines), 'per_cpu_features_lines': lines,
                'scope': 'Linux-reported EL0 feature names; not raw ID_AA64 registers'}
    except (OSError, UnicodeError):
        return {'available': False, 'reason': 'read or encoding error'}


def collect_kernel_config(proc_root: Path) -> dict:
    values = {name: {'available': False, 'reason': 'symbol absent'} for name in CONFIG_FIELDS}
    try:
        with gzip.open(proc_root / 'config.gz', 'rt', encoding='ascii') as handle:
            for line in handle:
                line = line.rstrip('\n')
                for name in CONFIG_FIELDS:
                    if line.startswith(name + '='):
                        value = line[len(name) + 1:]
                        if value in ('y', 'm', 'n'):
                            values[name] = {'available': True, 'value': value, 'line': line}
                    elif line == '# ' + name + ' is not set':
                        values[name] = {'available': True, 'value': 'n', 'line': line}
        return {'available': True, 'interface': '/proc/config.gz', 'symbols': values}
    except (OSError, UnicodeError):
        return {'available': False, 'reason': 'config interface unavailable'}


def collect_bindings(sys_root: Path) -> list:
    result = []
    dt_root = (sys_root / 'firmware/devicetree/base').resolve()
    for bus in ('platform', 'i2c', 'spi', 'sdio'):
        for device in sorted((sys_root / 'bus' / bus / 'devices').glob('*')):
            if not SAFE_NAME.fullmatch(device.name):
                continue
            try:
                target = (device / 'of_node').resolve(strict=True)
                node = '/' + str(target.relative_to(dt_root)).replace('\\', '/')
            except (OSError, ValueError):
                continue
            try:
                driver = (device / 'driver').resolve(strict=True).name
                if not SAFE_NAME.fullmatch(driver):
                    driver = None
            except OSError:
                driver = None
            result.append({'bus': bus, 'device': device.name, 'dt_node': node,
                           'bound_driver': driver})
    return result


def collect_ufs(sys_root: Path) -> dict:
    # Fixed board controller path, never arbitrary device/string descriptors.
    root = sys_root / 'bus/platform/devices/1d84000.ufshc'
    if not root.is_dir():
        return {'available': False, 'reason': 'controller directory absent'}
    descriptors = {}
    for group, fields in UFS_FIELDS.items():
        descriptors[group] = {name: read_text(root / group / name,
                                             numeric=(group != 'qcom'))
                              for name in fields}
    return {'available': True, 'controller': '1d84000.ufshc',
            'descriptors': descriptors,
            'omitted': ['string_descriptors', 'serial', 'manufacturing_date',
                        'health_descriptor', 'partition/LUN contents',
                        'device identifiers and credential-bearing state']}


def collect_clocks(sys_root: Path, proc_root: Path) -> dict:
    mounts = []
    try:
        # Preserve only debugfs mountpoints, never all mount sources/options.
        with (proc_root / 'mounts').open('r', encoding='ascii') as handle:
            for line in handle:
                fields = line.split()
                if len(fields) > 2 and fields[2] == 'debugfs':
                    mounts.append(fields[1].replace('\\040', ' '))
    except (OSError, UnicodeError):
        return {'available': False, 'reason': 'mount metadata unavailable'}
    candidates = [sys_root / 'kernel/debug/clk/clk_summary']
    if sys_root == Path('/sys'):
        candidates.extend(Path(p) / 'clk/clk_summary' for p in mounts)
    seen, rows, sources = set(), [], []
    for path in candidates:
        if str(path) in seen:
            continue
        seen.add(str(path))
        try:
            with path.open('r', encoding='ascii') as handle:
                for line in handle:
                    fields = line.split()
                    if fields and CLOCK_NAME.fullmatch(fields[0]):
                        if len(line) > 1024 or any(not re.fullmatch(r'[A-Za-z0-9_.+:/\-]+', f)
                                                  for f in fields):
                            continue
                        rows.append(fields)
            sources.append(str(path))
        except (OSError, UnicodeError):
            continue
    return {'available': bool(sources), 'debugfs_mount_count': len(mounts),
            'sources': sources, 'rows': rows,
            'reason': None if sources else 'no readable existing clock summary',
            'writes_or_mounts_performed': False,
            'column_interpretation': 'kernel clk_summary columns, raw tokens; no rate inferred'}


def collect(sys_root=Path('/sys'), proc_root=Path('/proc')) -> dict:
    return {
        'schema_version': 1, 'kind': 'tb371fc-allowlisted-sysfs-hardware',
        'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'source': {'interfaces': ['/sys/devices/system/cpu', '/proc/cpuinfo:Features',
                                  '/proc/config.gz: explicit console/module/debug symbols', '/proc/consoles',
                                  '/sys/bus/{platform,i2c,spi,sdio}/devices/*/{of_node,driver}',
                                  '/sys/bus/platform/devices/1d84000.ufshc',
                                  'existing debugfs clk/clk_summary: selected clock names']},
        'cpus': collect_cpus(sys_root), 'cpu_features': collect_features(proc_root),
        'kernel_config': collect_kernel_config(proc_root),
        'consoles': read_text(proc_root / 'consoles'),
        'dt_driver_bindings': collect_bindings(sys_root), 'ufs': collect_ufs(sys_root),
        'clocks': collect_clocks(sys_root, proc_root),
        'privacy': {'serials_network_addresses_user_data': 'not retained; no identifier-specific file reads',
                    'cpuinfo': 'Features lines only; other lines discarded without hashes',
                    'raw_input_registers_firmware': 'not read'},
        'scope': 'Read-only hardware configuration; does not establish emulation equivalence',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = collect()
    result['source']['collector_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    text = json.dumps(result, sort_keys=True, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
