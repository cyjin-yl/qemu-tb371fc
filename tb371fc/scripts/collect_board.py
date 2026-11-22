#!/usr/bin/env python3
"""Collect a bounded, passive hardware reference from a rooted Android tablet.

Reads only an explicit DT node/property allowlist, CPU MIDR registers exported
by sysfs, a SoC ID and four product properties. Never reads /dev/mem, input
events, bootargs, serial numbers, arbitrary debugfs, logs or credentials. It
does not alter drivers, tracing, frequencies, power policy or device storage.
"""

from __future__ import annotations

import argparse
import base64
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys

SCHEMA_VERSION = 1
MAX_PROPERTY_BYTES = 4096
DT_ROOT = Path('/sys/firmware/devicetree/base')
SYSFS_CPU_ROOT = Path('/sys/devices/system/cpu')
IDENTITY_PROPERTIES = (
    'ro.product.model', 'ro.product.device', 'ro.board.platform', 'ro.soc.model',
)
COMMON_PROPERTIES = frozenset({
    '#address-cells', '#size-cells', '#interrupt-cells', 'compatible',
    'device_type', 'reg', 'status', 'interrupts', 'interrupts-extended',
    'interrupt-parent', 'interrupt-controller', 'redistributor-stride',
    'clock-frequency', 'clock-names', 'clocks', 'ranges', 'enable-method',
    'method', 'phandle', 'linux,phandle',
})
STRING_PROPERTIES = frozenset({
    'compatible', 'model', 'device_type', 'status', 'clock-names',
    'enable-method', 'method', 'stdout-path',
})
CORE_NODE = re.compile(
    r'^(?:/|/soc|/cpus|/chosen|/psci|/memory(?:@[0-9a-fA-F]+)?|'
    r'/cpus/cpu@[0-9a-fA-F]+|'
    r'/soc/(?:interrupt-controller|intc)@[0-9a-fA-F]+|'
    r'/soc/(?:qcom,qup_uart|serial)@[0-9a-fA-F]+|'
    r'/soc/timer(?:@[0-9a-fA-F]+)?)$'
)


def read_bounded(path: Path, maximum: int = MAX_PROPERTY_BYTES) -> bytes:
    with path.open('rb') as source:
        raw = source.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError('property exceeds capture size limit')
    return raw


def encode_property(name: str, raw: bytes) -> dict:
    result = {
        'size': len(raw),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'base64': base64.b64encode(raw).decode('ascii'),
        'be32': list(struct.unpack(f'>{len(raw) // 4}I', raw))
        if len(raw) % 4 == 0 else None,
    }
    if name in STRING_PROPERTIES:
        if not raw or not raw.endswith(b'\0'):
            raise ValueError('DT string is not NUL terminated')
        result['strings'] = raw[:-1].decode('ascii').split('\0')
    return result


def allowed_properties(path: str) -> frozenset[str]:
    if path == '/chosen':
        return frozenset({'stdout-path'})
    if path == '/':
        return COMMON_PROPERTIES | {'model'}
    return COMMON_PROPERTIES


def collect_dt(root: Path) -> dict:
    result = {}
    # Listing passive DT directories is safe; values are read only after both
    # the full node path and individual property pass the allowlists.
    for current, directories, files in os.walk(root, followlinks=False):
        relative = '/' + str(Path(current).relative_to(root))
        if relative == '/.':
            relative = '/'
        if not CORE_NODE.fullmatch(relative):
            continue
        allowed = allowed_properties(relative)
        values, unavailable = {}, {}
        for name in sorted(allowed):
            path = Path(current) / name
            if name not in files:
                unavailable[name] = 'absent'
                continue
            try:
                values[name] = encode_property(name, read_bounded(path))
            except (OSError, ValueError, UnicodeError) as error:
                unavailable[name] = f'{type(error).__name__}: {error}'
        result[relative] = {
            'properties': values,
            'unavailable': unavailable,
            # Names only; omitted values are never opened or hashed.
            'omitted_property_names': sorted(set(files) - allowed),
        }
    return dict(sorted(result.items()))


def collect_identity() -> tuple[dict, dict]:
    identity, unavailable = {}, {}
    for name in IDENTITY_PROPERTIES:
        try:
            process = subprocess.run(
                ['/system/bin/getprop', name], check=True,
                capture_output=True, timeout=3,
            )
            if len(process.stdout) > 256:
                raise ValueError('identity exceeds capture size limit')
            value = process.stdout.decode('utf-8').strip()
            if not value:
                raise ValueError('empty identity property')
            identity[name] = value
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            unavailable[name] = type(error).__name__
    try:
        identity['soc_id'] = read_bounded(
            Path('/sys/devices/soc0/soc_id'), 64).decode('ascii').strip()
    except (OSError, ValueError, UnicodeError) as error:
        unavailable['soc_id'] = type(error).__name__
    return identity, unavailable


def collect_cpu_midr(root: Path) -> tuple[dict, dict]:
    values, unavailable = {}, {}
    for cpu in range(8):
        name = f'cpu{cpu}'
        path = root / name / 'regs/identification/midr_el1'
        try:
            text = read_bounded(path, 64).decode('ascii').strip()
            value = int(text, 0)
            if not 0 <= value <= 0xffffffff:
                raise ValueError('MIDR is outside a 32-bit register')
            values[name] = {'midr_el1': f'0x{value:08x}'}
        except (OSError, ValueError, UnicodeError) as error:
            unavailable[name] = type(error).__name__
    return values, unavailable


def collect(source_sha256: str | None = None) -> dict:
    identity, identity_unavailable = collect_identity()
    midr, midr_unavailable = collect_cpu_midr(SYSFS_CPU_ROOT)
    return {
        'schema_version': SCHEMA_VERSION,
        'kind': 'tb371fc-passive-arm-platform-reference',
        'captured_at_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'collector_sha256': source_sha256,
        'collector_hash_source': 'operator-supplied source digest'
        if source_sha256 else 'not available',
        'scope': 'Sequential read-only DT/sysfs snapshot; no behavioral hardware test',
        'identity': identity,
        'identity_unavailable': identity_unavailable,
        'dt_nodes': collect_dt(DT_ROOT),
        'cpu_midr': midr,
        'cpu_midr_unavailable': midr_unavailable,
        'exclusions': [
            'bootargs', 'serial numbers', 'MAC addresses', 'credentials',
            'input events', 'firmware binaries', 'debugfs', 'raw MMIO',
            'device state writes',
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha256', help='Digest of this exact collector source')
    args = parser.parse_args()
    if args.source_sha256 and not re.fullmatch(r'[0-9a-f]{64}', args.source_sha256):
        parser.error('--source-sha256 must be a lowercase SHA-256 digest')
    result = collect(args.source_sha256)
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0 if result['dt_nodes'] and result['identity'].get('ro.product.model') else 1


if __name__ == '__main__':
    raise SystemExit(main())
