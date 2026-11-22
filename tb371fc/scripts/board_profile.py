#!/usr/bin/env python3
"""Verify captured DT bytes and derive an explicit, evidence-linked board profile."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
import re
import struct
import sys

from collect_board import (
    CORE_NODE, IDENTITY_PROPERTIES, MAX_PROPERTY_BYTES, SCHEMA_VERSION,
    STRING_PROPERTIES, allowed_properties,
)


class InvalidEvidence(ValueError):
    pass


def verify_property(name: str, value: dict) -> bytes:
    try:
        allowed = {'size', 'sha256', 'base64', 'be32'}
        if name in STRING_PROPERTIES:
            allowed.add('strings')
        if set(value) != allowed:
            raise InvalidEvidence('unexpected property fields')
        raw = base64.b64decode(value['base64'], validate=True)
        if len(raw) > MAX_PROPERTY_BYTES or len(raw) != value['size']:
            raise InvalidEvidence('invalid property size')
        if hashlib.sha256(raw).hexdigest() != value['sha256']:
            raise InvalidEvidence('property SHA-256 mismatch')
        cells = list(struct.unpack(f'>{len(raw) // 4}I', raw)) if len(raw) % 4 == 0 else None
        if value['be32'] != cells:
            raise InvalidEvidence('BE32 cells disagree with captured bytes')
        if name in STRING_PROPERTIES:
            if not raw or not raw.endswith(b'\0'):
                raise InvalidEvidence('DT string is not NUL terminated')
            if value.get('strings') != raw[:-1].decode('ascii').split('\0'):
                raise InvalidEvidence('decoded string disagrees with captured bytes')
        elif 'strings' in value:
            raise InvalidEvidence('unexpected string interpretation')
        return raw
    except (KeyError, TypeError, ValueError, UnicodeError) as error:
        if isinstance(error, InvalidEvidence):
            raise
        raise InvalidEvidence(f'invalid property record: {error}') from error


def verify_capture(capture: dict) -> None:
    expected = {
        'schema_version', 'kind', 'captured_at_utc', 'collector_sha256',
        'collector_hash_source', 'scope', 'identity', 'identity_unavailable',
        'dt_nodes', 'cpu_midr', 'cpu_midr_unavailable', 'exclusions',
    }
    if set(capture) != expected:
        raise InvalidEvidence('unexpected capture fields')
    if capture.get('schema_version') != SCHEMA_VERSION:
        raise InvalidEvidence('unknown schema version')
    if capture.get('kind') != 'tb371fc-passive-arm-platform-reference':
        raise InvalidEvidence('wrong capture kind')
    identity = capture.get('identity', {})
    if set(identity) - (set(IDENTITY_PROPERTIES) | {'soc_id'}):
        raise InvalidEvidence('identity outside explicit allowlist')
    for value in identity.values():
        if not isinstance(value, str) or not value or len(value.encode()) > 256:
            raise InvalidEvidence('invalid identity value')
    digest = capture['collector_sha256']
    if digest is not None and not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise InvalidEvidence('invalid collector source digest')
    for cpu, data in capture['cpu_midr'].items():
        if not re.fullmatch(r'cpu[0-7]', cpu) or set(data) != {'midr_el1'}:
            raise InvalidEvidence('unexpected CPU identification fields')
        if not re.fullmatch(r'0x[0-9a-f]{8}', data['midr_el1']):
            raise InvalidEvidence('invalid MIDR value')
    nodes = capture.get('dt_nodes', {})
    if '/' not in nodes or '/soc' not in nodes or '/cpus' not in nodes:
        raise InvalidEvidence('missing root, SoC or CPU bus metadata')
    for node, data in nodes.items():
        if not CORE_NODE.fullmatch(node):
            raise InvalidEvidence(f'node outside allowlist: {node}')
        if set(data) != {'properties', 'unavailable', 'omitted_property_names'}:
            raise InvalidEvidence('unexpected DT node fields')
        properties = data['properties']
        if set(properties) - allowed_properties(node):
            raise InvalidEvidence(f'property outside allowlist: {node}')
        if set(data['unavailable']) - allowed_properties(node):
            raise InvalidEvidence(f'unavailable property outside allowlist: {node}')
        for name, value in properties.items():
            verify_property(name, value)


def cells(value: dict) -> list[int]:
    raw = verify_property('reg', value)
    if len(raw) % 4:
        raise InvalidEvidence('cell property is not a multiple of four bytes')
    return list(struct.unpack(f'>{len(raw) // 4}I', raw))


def cell_number(parts: list[int]) -> int:
    result = 0
    for part in parts:
        result = (result << 32) | part
    return result


def decode_reg(value: dict, address_cells: int, size_cells: int) -> list[dict]:
    if address_cells not in (1, 2) or size_cells not in (0, 1, 2):
        raise InvalidEvidence('unsupported DT cell widths')
    values = cells(value)
    stride = address_cells + size_cells
    if not values or len(values) % stride:
        raise InvalidEvidence('reg has a partial or empty tuple')
    result = []
    for index in range(0, len(values), stride):
        entry = {'address': cell_number(values[index:index + address_cells])}
        if size_cells:
            entry['size'] = cell_number(values[index + address_cells:index + stride])
        result.append(entry)
    return result


def scalar(properties: dict, name: str) -> int:
    value = cells(properties[name])
    if len(value) != 1:
        raise InvalidEvidence(f'{name} must be exactly one cell')
    return value[0]


def source(path: str, prop: str) -> str:
    return path.rstrip('/') + '/' + prop


def inherited_interrupt_parent(nodes: dict, path: str) -> int:
    current = path
    while True:
        properties = nodes.get(current, {}).get('properties', {})
        if 'interrupt-parent' in properties:
            return scalar(properties, 'interrupt-parent')
        if current == '/':
            break
        current = current.rsplit('/', 1)[0] or '/'
    raise InvalidEvidence(f'interrupt parent is unavailable for {path}')


def normalize(capture: dict) -> dict:
    verify_capture(capture)
    if capture['identity'].get('ro.product.model') != 'TB371FC':
        raise InvalidEvidence('capture is not from a TB371FC')
    if capture['identity'].get('ro.board.platform') != 'kona':
        raise InvalidEvidence('capture is not from the kona platform')
    nodes = capture['dt_nodes']
    root = nodes['/']['properties']
    soc = nodes['/soc']['properties']
    cpus = nodes['/cpus']['properties']
    root_ac, root_sc = scalar(root, '#address-cells'), scalar(root, '#size-cells')
    soc_ac, soc_sc = scalar(soc, '#address-cells'), scalar(soc, '#size-cells')
    cpu_ac, cpu_sc = scalar(cpus, '#address-cells'), scalar(cpus, '#size-cells')
    result = {
        'schema_version': SCHEMA_VERSION,
        'identity': capture['identity'],
        'capture_utc': capture['captured_at_utc'],
        'scope': 'Observed hardware configuration; no emulation or hardware behavior attestation',
        'bus_cells': {'root': [root_ac, root_sc], 'soc': [soc_ac, soc_sc], 'cpus': [cpu_ac, cpu_sc]},
        'memory': [], 'cpus': [], 'interrupt_controllers': [], 'uarts': [],
        'timers': [], 'unresolved': [],
    }
    # A non-identity bus requires translating reg before using it as an MMIO
    # address. This board's captured /soc ranges is a single identity window.
    ranges = cells(soc['ranges']) if 'ranges' in soc else None
    if ranges is None:
        raise InvalidEvidence('SoC address translation is absent')
    if ranges:
        stride = soc_ac + root_ac + soc_sc
        if len(ranges) != stride:
            raise InvalidEvidence('SoC bus requires unsupported multiple ranges')
        child = cell_number(ranges[:soc_ac])
        parent = cell_number(ranges[soc_ac:soc_ac + root_ac])
        window = cell_number(ranges[soc_ac + root_ac:])
        if child != parent or window == 0:
            raise InvalidEvidence('SoC bus is not an identity map')
        result['soc_identity_range'] = {'address': child, 'size': window}
    gic_phandles = set()
    for data in nodes.values():
        properties = data['properties']
        if 'arm,gic-v3' in properties.get('compatible', {}).get('strings', []):
            if scalar(properties, '#interrupt-cells') != 3:
                raise InvalidEvidence('GIC requires three interrupt cells')
            for name in ('phandle', 'linux,phandle'):
                if name in properties:
                    gic_phandles.add(scalar(properties, name))
    for path, data in sorted(nodes.items()):
        props = data['properties']
        compatible = props.get('compatible', {}).get('strings', [])
        if path.startswith('/memory'):
            for item in decode_reg(props['reg'], root_ac, root_sc):
                result['memory'].append({**item, 'source': source(path, 'reg')})
        elif path.startswith('/cpus/cpu@'):
            reg = decode_reg(props['reg'], cpu_ac, cpu_sc)
            if len(reg) != 1 or cpu_sc != 0:
                raise InvalidEvidence('CPU reg must describe one MPIDR without a size')
            result['cpus'].append({
                'mpidr': reg[0]['address'], 'compatible': compatible,
                'enable_method': props.get('enable-method', {}).get('strings', []),
                'source': source(path, 'reg'),
            })
        elif path.startswith('/soc/') and 'arm,gic-v3' in compatible:
            entry = {'compatible': compatible, 'regions': decode_reg(props['reg'], soc_ac, soc_sc), 'source': path}
            if 'redistributor-stride' in props:
                entry['redistributor_stride'] = cell_number(cells(props['redistributor-stride']))
            result['interrupt_controllers'].append(entry)
        elif path.startswith('/soc/') and ('qup_uart@' in path or '/serial@' in path):
            entry = {
                'compatible': compatible, 'regions': decode_reg(props['reg'], soc_ac, soc_sc),
                'status': props.get('status', {}).get('strings', ['okay'])[0],
                'source': path,
            }
            if 'interrupts' in props:
                parent = inherited_interrupt_parent(nodes, path)
                if parent not in gic_phandles:
                    raise InvalidEvidence('UART interrupt parent is not the captured GIC')
                irq = cells(props['interrupts'])
                if len(irq) != 3:
                    raise InvalidEvidence('UART interrupt is not one GIC tuple')
                entry['interrupt'] = {'type': irq[0], 'number': irq[1], 'flags': irq[2]}
                entry['interrupt_parent_phandle'] = parent
            if 'interrupts-extended' in props:
                entry['interrupts_extended_raw_cells'] = cells(props['interrupts-extended'])
                result['unresolved'].append(f'{path}: interrupts-extended retained as raw phandle tuples')
            result['uarts'].append(entry)
        elif 'arm,armv8-timer' in compatible:
            result['timers'].append({
                'clock_frequency_hz': scalar(props, 'clock-frequency') if 'clock-frequency' in props else None,
                'interrupt_cells': cells(props['interrupts']), 'source': path,
            })
    if not result['memory'] or not result['cpus'] or not result['interrupt_controllers']:
        raise InvalidEvidence('missing memory, CPUs or GIC')
    mpidrs = [cpu['mpidr'] for cpu in result['cpus']]
    if len(set(mpidrs)) != len(mpidrs):
        raise InvalidEvidence('duplicate CPU MPIDR')
    banks = sorted(result['memory'], key=lambda item: item['address'])
    for item in banks:
        if item['size'] <= 0 or item['address'] + item['size'] > 2**64:
            raise InvalidEvidence('invalid memory region')
    for before, after in zip(banks, banks[1:]):
        if before['address'] + before['size'] > after['address']:
            raise InvalidEvidence('overlapping memory banks')
    result['visible_memory_bytes'] = sum(item['size'] for item in banks)
    result['cpu_midr'] = capture.get('cpu_midr', {})
    result['psci'] = {
        key: nodes.get('/psci', {}).get('properties', {}).get(key, {}).get('strings', [])
        for key in ('compatible', 'method')
    }
    chosen = nodes.get('/chosen', {}).get('properties', {}).get('stdout-path')
    result['chosen_stdout_path'] = chosen['strings'] if chosen else None
    result['enabled_console_candidates'] = [
        item['source'] for item in result['uarts']
        if item['status'] in ('ok', 'okay')
        and 'qcom,msm-geni-console' in item['compatible']
    ]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture', type=Path)
    args = parser.parse_args()
    try:
        result = normalize(json.loads(args.capture.read_text()))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f'invalid evidence: {error}', file=sys.stderr)
        return 1
    json.dump(result, sys.stdout, indent=2, sort_keys=True)
    print()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
