#!/usr/bin/env python3
"""Read UFS LU descriptor geometry without opening a block device or partition."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import re

import collect_hardware_sysfs as safe

UNIT_FIELDS = ('logical_block_size', 'logical_block_count', 'boot_lun_id',
               'lun_queue_depth', 'lun_memory_type', 'lun_write_protect',
               'provisioning_type', 'context_capabilities', 'data_reliability',
               'erase_block_size', 'wb_buf_alloc_units')


def collect(sys_root=Path('/sys')):
    units = []
    for device in sorted((sys_root / 'class/scsi_device').glob('0:0:0:*')):
        if not re.fullmatch(r'0:0:0:[0-9]+', device.name):
            continue
        path = device / 'device/unit_descriptor'
        fields = {name: safe.read_text(path / name, numeric=True) for name in UNIT_FIELDS}
        units.append({'scsi_address': device.name, 'descriptor_fields': fields})
    paths = [
        'bus/platform/devices/1d84000.ufshc/ufshci_version',
        'bus/platform/devices/1d84000.ufshc/hci_version',
        'bus/platform/devices/1d84000.ufshc/controller_version',
        'bus/platform/devices/1d84000.ufshc/version',
        'class/scsi_host/host0/ufshci_version',
    ]
    version = {name: safe.read_text(sys_root / name, numeric=True) for name in paths}
    return {
        'kind': 'tb371fc-read-only-ufs-unit-descriptors', 'schema_version': 1,
        'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'units': units, 'hci_version_interfaces': version,
        'hci_version_observed': any(v['available'] for v in version.values()),
        'interpretation': {
            'logical_block_size': 'Raw bLogicalBlockSize exponent, not a byte count',
            'logical_block_count': 'Raw qLogicalBlockCount count of logical blocks',
            'boot_lun_id': 'Raw bBootLunID descriptor; not a claim about the active boot slot',
            'hci_version': 'Distinct from UFS device specification_version and device_version',
            'state': 'LU configuration at capture time; not maximum controller/device capability',
        },
        'not_read': ['block device contents', 'partition tables', 'filesystems',
                     'serial numbers', 'SCSI unique_id', 'MMIO', 'health descriptors'],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = collect()
    result['source'] = {'collector_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                        'read_helper_sha256': hashlib.sha256(Path(safe.__file__).read_bytes()).hexdigest()}
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
