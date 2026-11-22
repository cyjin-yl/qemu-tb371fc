#!/usr/bin/env python3
"""Compare a running TB371FC QEMU machine's exported DT/QOM with live evidence.

This checks the implemented platform's configuration. It never claims complete
emulation, cycle accuracy, a booted Android image or physical-device behavior.
The expected map comes from the checked-in passive capture, not C constants.
Requires Python 3, fdtget and the newly built qemu-system-aarch64 binary.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time

from board_profile import normalize

ROOT = Path(__file__).resolve().parent.parent


class ValidationError(RuntimeError):
    pass


def number(cells: list[int]) -> int:
    value = 0
    for cell in cells:
        if not isinstance(cell, int) or not 0 <= cell <= 0xffffffff:
            raise ValidationError('invalid 32-bit DT cell')
        value = (value << 32) | cell
    return value


def parent(path: str) -> str:
    return path.rsplit('/', 1)[0] or '/'


class FdtReader:
    """Use libfdt's fdtget rather than parsing generated source or constants."""

    def __init__(self, filename: Path, executable: str = 'fdtget'):
        self.filename, self.executable = filename, executable
        self._properties = {}
        self._nodes = None

    def run(self, *arguments: str) -> str:
        result = subprocess.run(
            [self.executable, *arguments], capture_output=True, text=True,
            timeout=10,
        )
        if result.returncode:
            raise ValidationError(f'fdtget failed: {result.stderr.strip()}')
        return result.stdout

    def nodes(self) -> list[str]:
        if self._nodes is None:
            self._nodes = ['/']
            for node in self._nodes:
                children = self.run('-l', str(self.filename), node).splitlines()
                self._nodes.extend(node.rstrip('/') + '/' + child for child in children)
        return self._nodes

    def has(self, node: str, prop: str) -> bool:
        if node not in self.nodes():
            return False
        if node not in self._properties:
            self._properties[node] = set(self.run('-p', str(self.filename), node).splitlines())
        return prop in self._properties[node]

    def cells(self, node: str, prop: str, default=None):
        if not self.has(node, prop):
            if default is not None:
                return default
            raise ValidationError(f'missing DT property {node}:{prop}')
        return [int(value, 16) for value in self.run('-t', 'x', str(self.filename), node, prop).split()]

    def strings(self, node: str, prop: str, default=None):
        if not self.has(node, prop):
            if default is not None:
                return default
            raise ValidationError(f'missing DT property {node}:{prop}')
        # Only DT bindings/paths/methods without embedded whitespace are read.
        return self.run('-t', 's', str(self.filename), node, prop).split()


def scalar(reader, node: str, prop: str, default=None) -> int:
    values = reader.cells(node, prop, [default] if default is not None else None)
    if len(values) != 1:
        raise ValidationError(f'{node}:{prop} must have exactly one cell')
    return values[0]


def widths(reader, bus: str) -> tuple[int, int]:
    # DT specification defaults apply to the immediate parent; the widths
    # must not be inherited from a grandparent bus.
    address = scalar(reader, bus, '#address-cells', 2)
    size = scalar(reader, bus, '#size-cells', 1)
    if address not in (1, 2) or size not in (0, 1, 2):
        raise ValidationError(f'unsupported cell widths on {bus}')
    return address, size


def reg_entries(reader, node: str, translate: bool = True) -> list[dict]:
    bus = parent(node)
    ac, sc = widths(reader, bus)
    cells = reader.cells(node, 'reg')
    stride = ac + sc
    if not cells or len(cells) % stride:
        raise ValidationError(f'partial or empty reg tuple at {node}')
    result = []
    for index in range(0, len(cells), stride):
        address = number(cells[index:index + ac])
        size = number(cells[index + ac:index + stride]) if sc else None
        current = bus
        while translate and current != '/':
            higher = parent(current)
            child_ac, child_sc = widths(reader, current)
            parent_ac, _ = widths(reader, higher)
            ranges = reader.cells(current, 'ranges')
            if ranges:
                range_stride = child_ac + parent_ac + child_sc
                if len(ranges) % range_stride:
                    raise ValidationError(f'partial ranges tuple at {current}')
                matches = []
                for offset in range(0, len(ranges), range_stride):
                    child_base = number(ranges[offset:offset + child_ac])
                    parent_base = number(ranges[offset + child_ac:offset + child_ac + parent_ac])
                    span = number(ranges[offset + child_ac + parent_ac:offset + range_stride])
                    if child_base <= address and address + (size or 1) <= child_base + span:
                        matches.append(parent_base + address - child_base)
                if len(matches) != 1:
                    raise ValidationError(f'{node}: ranges lacks one unambiguous translation')
                address = matches[0]
            current = higher
        entry = {'address': address}
        if size is not None:
            entry['size'] = size
        result.append(entry)
    return result


def inherited_cell(reader, node: str, prop: str) -> int:
    while True:
        if reader.has(node, prop):
            return scalar(reader, node, prop)
        if node == '/':
            raise ValidationError(f'no inherited {prop}')
        node = parent(node)


def observe_dt(reader) -> dict:
    nodes = reader.nodes()
    compatible = {node: reader.strings(node, 'compatible', []) for node in nodes}
    cpus, memory, reserved, gics, timers, uarts = [], [], [], [], [], []
    for node in nodes:
        dtype = reader.strings(node, 'device_type', [])
        if dtype == ['cpu']:
            reg = reg_entries(reader, node, translate=False)
            if len(reg) != 1 or 'size' in reg[0]:
                raise ValidationError('CPU reg must contain one MPIDR and no size')
            cpus.append({'mpidr': reg[0]['address'], 'compatible': compatible[node],
                         'enable_method': reader.strings(node, 'enable-method'), 'node': node})
        elif dtype == ['memory']:
            memory.extend(reg_entries(reader, node))
        if parent(node) == '/reserved-memory' and reader.has(node, 'reg'):
            reserved.extend({**entry, 'no_map': reader.has(node, 'no-map'), 'node': node}
                            for entry in reg_entries(reader, node))
        if 'arm,gic-v3' in compatible[node]:
            phandle = scalar(reader, node, 'phandle')
            gics.append({'regions': reg_entries(reader, node),
                         'redistributor_stride': number(reader.cells(node, 'redistributor-stride')),
                         'interrupt_cells': scalar(reader, node, '#interrupt-cells'),
                         'phandle': phandle, 'node': node})
        if 'arm,armv8-timer' in compatible[node]:
            timers.append({'clock_frequency_hz': scalar(reader, node, 'clock-frequency'),
                           'interrupt_cells': reader.cells(node, 'interrupts'), 'node': node})
        if any(value in compatible[node] for value in
               ('qcom,geni-debug-uart', 'qcom,msm-geni-console', 'qcom,geni-uart')):
            status = reader.strings(node, 'status', ['okay'])[0]
            if status in ('ok', 'okay'):
                irq = reader.cells(node, 'interrupts')
                if len(irq) != 3:
                    raise ValidationError('UART interrupt is not one GIC tuple')
                uarts.append({'regions': reg_entries(reader, node), 'compatible': compatible[node],
                              'interrupt': {'type': irq[0], 'number': irq[1], 'flags': irq[2]},
                              'interrupt_parent': inherited_cell(reader, node, 'interrupt-parent'),
                              'node': node})
    stdout = reader.strings('/chosen', 'stdout-path', [])
    stdout_node = stdout[0].split(':', 1)[0] if stdout else None
    if stdout_node and not stdout_node.startswith('/'):
        resolved = reader.strings('/aliases', stdout_node, [])
        stdout_node = resolved[0] if len(resolved) == 1 else None
    selected = [uart for uart in uarts if uart['node'] == stdout_node] if stdout_node else uarts
    return {'cpus': sorted(cpus, key=lambda cpu: cpu['mpidr']),
            'memory': memory, 'reserved_memory': reserved, 'gics': gics, 'timers': timers,
            'uarts': uarts, 'console': selected[0] if len(selected) == 1 else None,
            'chosen_stdout_path': stdout, 'root_compatible': compatible['/'],
            'psci': {'compatible': reader.strings('/psci', 'compatible', []),
                     'method': reader.strings('/psci', 'method', [])}}


def interval_list(entries: list[dict]) -> list[tuple[int, int]]:
    intervals = sorted((item['address'], item['address'] + item['size']) for item in entries)
    for start, end in intervals:
        if start < 0 or start >= end or end > 2**64:
            raise ValidationError('invalid memory interval')
    for first, second in zip(intervals, intervals[1:]):
        if first[1] > second[0]:
            raise ValidationError('overlapping memory intervals')
    return intervals


def subtract_intervals(memory, reservations):
    result = list(memory)
    for reserve_start, reserve_end in reservations:
        updated = []
        for start, end in result:
            if reserve_end <= start or end <= reserve_start:
                updated.append((start, end))
            else:
                if start < reserve_start:
                    updated.append((start, reserve_start))
                if reserve_end < end:
                    updated.append((reserve_end, end))
        result = updated
    # Adjacent ranges are equivalent when their union is identical.
    merged = []
    for start, end in sorted(result):
        if merged and merged[-1][1] == start:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


def compare(profile: dict, observed: dict, runtime: dict) -> dict:
    checks = []

    def check(name, expected, actual, matched=None, *, required=True, detail=None):
        passed = expected == actual if matched is None else bool(matched)
        checks.append({'name': name, 'required': required, 'expected': expected,
                       'observed': actual, 'passed': passed,
                       'classification': 'matched' if passed else
                       ('mismatch' if required else 'declared_deviation'), 'detail': detail})

    expected_mpidrs = [cpu['mpidr'] for cpu in profile['cpus']]
    check('dt.cpu_mpidrs', expected_mpidrs, [cpu['mpidr'] for cpu in observed['cpus']])
    qcpus = sorted(runtime['cpus'], key=lambda cpu: cpu['cpu-index'])
    check('qmp.cpu_indexes', list(range(len(expected_mpidrs))), [cpu['cpu-index'] for cpu in qcpus])
    check('qmp.cpu_mpidrs', expected_mpidrs, [cpu.get('mp-affinity') for cpu in qcpus])
    check('dt.cpu_enable_method', [['psci']] * len(expected_mpidrs),
          [cpu['enable_method'] for cpu in observed['cpus']])
    expected_models = ['cortex-a55'] * 4 + ['cortex-a76'] * 4
    actual_models = [cpu.get('qom-type', '').removesuffix('-arm-cpu') for cpu in qcpus]
    check('model.declared_cpu_surrogates', expected_models, actual_models,
          detail='This is an explicit model choice, not a comparison with real CPU identities.')
    check('dt.cpu_compatible_matches_qom', [['arm,' + model] for model in actual_models],
          [cpu['compatible'] for cpu in observed['cpus']])
    live_midr = [int(profile['cpu_midr'][f'cpu{index}']['midr_el1'], 0)
                 for index in range(len(expected_mpidrs))]
    check('hardware.cpu_midr', [f'0x{value:08x}' for value in live_midr],
          [f"0x{cpu['midr']:08x}" if isinstance(cpu.get('midr'), int) else None for cpu in qcpus],
          required=False,
          detail='QEMU A55/A76 CPU models are architectural surrogates; they do not reproduce Kryo/A77 identity or timing.')
    expected_gic = profile['interrupt_controllers'][0]
    gic = observed['gics'][0] if len(observed['gics']) == 1 else {}
    check('dt.gic_regions', expected_gic['regions'], gic.get('regions'))
    check('dt.gic_redistributor_stride', expected_gic['redistributor_stride'], gic.get('redistributor_stride'))
    timer = observed['timers'][0] if len(observed['timers']) == 1 else {}
    expected_timer = profile['timers'][0]
    check('dt.timer_frequency', expected_timer['clock_frequency_hz'], timer.get('clock_frequency_hz'))
    check('qmp.cpu_counter_frequency', [expected_timer['clock_frequency_hz']] * len(expected_mpidrs),
          [cpu.get('cntfrq') for cpu in qcpus])
    expected_irq = expected_timer['interrupt_cells']
    actual_irq = timer.get('interrupt_cells', [])
    normalized = lambda values: [value & 0xf if index % 3 == 2 else value
                                 for index, value in enumerate(values)]
    check('dt.timer_ppi_semantics', normalized(expected_irq), normalized(actual_irq),
          detail='GICv3 PPI flags compare trigger bits; the downstream legacy CPU-mask bits are recorded separately.')
    check('hardware.timer_raw_interrupt_flags', expected_irq, actual_irq, required=False,
          detail='Downstream GICv2-style CPU mask bits are not copied to the GICv3 generated DT.')
    candidates = profile.get('enabled_console_candidates', [])
    expected_uart = next((uart for uart in profile['uarts'] if uart['source'] in candidates), None)
    if expected_uart is None:
        raise ValidationError('live evidence does not identify one enabled console')
    console = observed['console'] or {}
    check('dt.console_regions', expected_uart['regions'], console.get('regions'))
    check('dt.console_interrupt', expected_uart['interrupt'], console.get('interrupt'))
    check('dt.console_interrupt_parent', {'phandle': gic.get('phandle'), 'cells': 3},
          {'phandle': console.get('interrupt_parent'), 'cells': gic.get('interrupt_cells')},
          matched=bool(gic) and console.get('interrupt_parent') == gic.get('phandle') and gic.get('interrupt_cells') == 3)
    check('hardware.console_driver_binding', expected_uart['compatible'], console.get('compatible'), required=False,
          detail='The model advertises the upstream Linux GENI debug-UART binding, not the downstream Android compatible string.')
    check('dt.psci_method', profile['psci']['method'], observed['psci']['method'])
    check('dt.psci_version_binding', profile['psci']['compatible'], observed['psci']['compatible'],
          matched=set(profile['psci']['compatible']).issubset(observed['psci']['compatible']))
    expected_banks = interval_list(profile['memory'])
    banks = interval_list(observed['memory'])
    no_map = interval_list([entry for entry in observed['reserved_memory'] if entry['no_map']])
    effective = subtract_intervals(banks, no_map)
    check('dt.os_visible_memory_banks', expected_banks, effective,
          detail='Subtract explicit no-map reservations before comparison; this does not prove that excluded addresses lack physical RAM.')
    holes = [(left[1], right[0]) for left, right in zip(expected_banks, expected_banks[1:]) if left[1] < right[0]]
    gap_observation = [{'range': gap, 'excluded_from_effective_memory': all(end <= gap[0] or start >= gap[1] for start, end in effective),
                        'explicit_no_map': any(start <= gap[0] and end >= gap[1] for start, end in no_map)} for gap in holes]
    check('dt.captured_memory_gap', holes, gap_observation,
          matched=bool(holes) and all(gap['excluded_from_effective_memory'] for gap in gap_observation),
          detail='The captured 69 MiB gap is preserved as an OS-visible exclusion. Backing RAM can still be contiguous.')
    check('qmp.machine_paused', False, runtime['status'].get('running'))
    version = runtime.get('version', {}).get('qemu', {})
    check('qmp.version_observed', 'major/minor/micro reported by running QEMU', version,
          matched=all(isinstance(version.get(field), int) for field in ('major', 'minor', 'micro')))
    required_checks = [item for item in checks if item['required']]
    return {
        'schema_version': 1, 'gate': 'implemented-platform-configuration',
        'passed': all(item['passed'] for item in required_checks),
        'complete_emulation': False, 'hardware_equivalent': False,
        'checks': checks, 'deviations': [item for item in checks if not item['required'] and not item['passed']],
        'summary': {'required_passed': sum(item['passed'] for item in required_checks),
                    'required_total': len(required_checks)},
        'limitations': [
            'DT/QOM agreement is not peripheral behavioral equivalence or an Android boot result.',
            'CPU microarchitecture, caches, timing and real CPU identity are not reproduced.',
            'The PSCI SMC interface is served by QEMU, not captured Qualcomm secure firmware.',
            'This gate does not test GPU, display, UFS, Wi-Fi, input, charging, suspend or power behavior.',
            'Only generated DT memory visibility is checked; no claim is made about the physical meaning of Android carveouts.',
        ],
        'observed': {'dt': observed, 'qmp': runtime},
    }


class QMP:
    def __init__(self, path: Path, process, timeout: float):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.stream = None
        self.sequence = 0
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.socket.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if process.poll() is not None:
                    raise ValidationError(f'QEMU exited before QMP was ready (code {process.returncode})')
                if time.monotonic() >= deadline:
                    raise ValidationError('QMP startup timed out')
                time.sleep(0.05)
        self.stream = self.socket.makefile('rb')
        greeting = self.receive()
        if 'QMP' not in greeting:
            raise ValidationError('missing QMP greeting')
        self.command('qmp_capabilities')

    def receive(self):
        line = self.stream.readline(1048577)
        if not line or len(line) > 1048576:
            raise ValidationError('QMP disconnected or returned an oversized message')
        return json.loads(line)

    def command(self, name, arguments=None):
        self.sequence += 1
        message = {'execute': name, 'id': self.sequence}
        if arguments is not None:
            message['arguments'] = arguments
        self.socket.sendall((json.dumps(message) + '\n').encode())
        while True:
            reply = self.receive()
            if 'event' in reply:
                continue
            if reply.get('id') != self.sequence:
                raise ValidationError('unexpected QMP reply ID')
            if 'error' in reply:
                raise ValidationError(f"QMP {name}: {reply['error']}")
            return reply['return']

    def close(self):
        if self.stream:
            self.stream.close()
        self.socket.close()


def runtime_capture(qemu: Path, fdtget: str, timeout: float, dtb_output: Path | None):
    with tempfile.TemporaryDirectory(prefix='tb371fc-qmp-') as temporary:
        directory = Path(temporary)
        socket_path, dtb = directory / 'qmp.sock', directory / 'machine.dtb'
        command = [str(qemu), '-machine', 'tb371fc', '-accel', 'tcg', '-m', '8G', '-smp', '8',
                   '-S', '-display', 'none', '-serial', 'null', '-monitor', 'none',
                   '-qmp', f'unix:{socket_path},server=on,wait=off']
        qmp = None
        with (directory / 'stderr.log').open('wb') as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log)
            try:
                qmp = QMP(socket_path, process, timeout)
                runtime = {'version': qmp.command('query-version'),
                           'status': qmp.command('query-status'), 'cpus': qmp.command('query-cpus-fast')}
                for cpu in runtime['cpus']:
                    for prop in ('mp-affinity', 'midr', 'cntfrq'):
                        cpu[prop] = qmp.command('qom-get', {'path': cpu['qom-path'], 'property': prop})
                qmp.command('dumpdtb', {'filename': str(dtb)})
                if not dtb.is_file():
                    raise ValidationError('QMP reported success but produced no DTB')
                observed = observe_dt(FdtReader(dtb, fdtget))
                dtb_hash = hashlib.sha256(dtb.read_bytes()).hexdigest()
                if dtb_output:
                    dtb_output.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(dtb, dtb_output)
                return observed, runtime, dtb_hash
            finally:
                if qmp:
                    try:
                        qmp.command('quit')
                    except (OSError, ValueError, ValidationError):
                        pass
                    qmp.close()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                error_log = (directory / 'stderr.log').read_text(errors='replace')
                if error_log:
                    print(error_log[-8192:], file=sys.stderr, end='')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--profile', type=Path, default=ROOT / 'evidence/board-profile.json')
    parser.add_argument('--capture', type=Path, default=ROOT / 'evidence/android-board.json')
    parser.add_argument('--fdtget', default='fdtget')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--dtb-output', type=Path)
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    result = {'schema_version': 1, 'gate': 'implemented-platform-configuration',
              'passed': False, 'complete_emulation': False, 'hardware_equivalent': False}
    try:
        if not 0 < args.timeout <= 60:
            raise ValidationError('--timeout must be between 0 and 60 seconds')
        profile_bytes, capture_bytes = args.profile.read_bytes(), args.capture.read_bytes()
        profile = json.loads(profile_bytes)
        if profile != normalize(json.loads(capture_bytes)):
            raise ValidationError('board-profile.json differs from the verified raw hardware capture')
        observed, runtime, dtb_hash = runtime_capture(args.qemu.resolve(), args.fdtget, args.timeout, args.dtb_output)
        result = compare(profile, observed, runtime)
        result['sources'] = {'profile_sha256': hashlib.sha256(profile_bytes).hexdigest(),
                             'capture_sha256': hashlib.sha256(capture_bytes).hexdigest(),
                             'qmp_dumpdtb_sha256': dtb_hash, 'qemu': str(args.qemu.resolve())}
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, ValidationError) as error:
        result['error'] = f'{type(error).__name__}: {error}'
    result['validated_at_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    text = json.dumps(result, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
