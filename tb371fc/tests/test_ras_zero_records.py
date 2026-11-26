#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opt-in RAS zero-records: real eight-CPU guest and complete migration tests."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time
import traceback

from qce_bam_test_transport import file_channels, put_json, sha
from ras_zero_records_test_contract import (
    SCHEMA, CPU_PROP, PROFILE_PROP, SUBSECTION, CONTROL, BOX_BASE, BOX_STRIDE,
    BOX_BYTES, NONCE, NEW_KVM_IDS, parse_probe_elf, decode_guest, parse_stream,
    require, digest,
)
from ras_zero_records_test_transport import RasRig, cleanup_records

HERE = Path(__file__).resolve().parent
NORMAL_NAMES = ('qtest-default-off', 'qtest-on', 'default-off', 'explicit-off', 'on-reset',
                'on-source', 'on-target', 'on-return', 'off-source', 'off-target', 'off-return')
INVALID_NAMES = ('invalid-cortex-a55', 'invalid-cortex-a77')
MUTATIONS = (('profile0-value0', 'profile-value', 0, 0),
             ('profile7-value2', 'profile-value', 7, 2),
             ('profile0-version0', 'profile-version', 0, 0),
             ('profile7-version0', 'profile-version', 7, 0),
             ('profile0-absent', 'profile-absent', 0, None),
             ('profile7-absent', 'profile-absent', 7, None)) + tuple(
                 (f'cpreg-cpu{cpu}-{i}', 'cpreg-nonzero', cpu, i)
                 for cpu in (0, 7) for i in range(7))
ORDERED = NORMAL_NAMES[:5] + INVALID_NAMES + NORMAL_NAMES[5:] + (
    'cross-on-to-off', 'cross-off-to-on') + tuple(m[0] for m in MUTATIONS)


def pin(path, root):
    path = Path(path)
    raw = path.read_bytes()
    return {'file': path.relative_to(root).as_posix(), 'bytes': len(raw), 'sha256': digest(raw)}


def build_probe(args, out):
    out.mkdir(exist_ok=False)
    args.probe = out / 'probe.elf'
    command = [args.cc, '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding',
               '-fno-builtin', '-fno-stack-protector', '-fno-pie', '-mstrict-align',
               '-mgeneral-regs-only', '-march=armv8.2-a', '-nostdlib', '-static',
               '-Wl,--build-id=none', '-T', str(HERE / 'ras_zero_records/link.ld'),
               str(HERE / 'ras_zero_records/start.S'), str(HERE / 'ras_zero_records/probe.c'),
               '-o', str(args.probe)]
    put_json(out / 'command.json', command)
    with (out / 'stdout.log').open('wb') as stdout, (out / 'stderr.log').open('wb') as stderr:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
        try:
            code = process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait()
            put_json(out / 'execution.json', {'returncode': process.returncode, 'timed_out': True,
                'actually_waited_and_reaped': True, 'passed': False})
            raise
    put_json(out / 'execution.json', {'returncode': code, 'timed_out': False,
             'actually_waited_and_reaped': True, 'passed': code == 0})
    require(code == 0, 'guest probe compilation failed; complete compiler stderr retained')
    return parse_probe_elf(args.probe.read_bytes())


def qom_access(r, mode):
    types, rejected = [], []
    for cpu in range(8):
        path = f'/machine/cpu{cpu}'
        rows = r.monitor('qom-list', {'path': path})
        mapping = {row['name']: row['type'] for row in rows}
        require(mapping.get(CPU_PROP) == 'bool' and mapping.get(PROFILE_PROP) == 'uint32', 'QOM metadata types')
        types.append({'path': path, 'types': {key: mapping[key] for key in (CPU_PROP, PROFILE_PROP)}})
        for prop, value in ((CPU_PROP, not mode), (PROFILE_PROP, int(not mode))):
            arguments = {'path': path, 'property': prop, 'value': value}
            response = r.raw_monitor('qom-set', arguments)
            require(response.get('error', {}).get('class') == 'GenericError', 'realized CPU property became writable')
            rejected.append({'arguments': arguments, 'response': response})
    arguments = {'path': '/machine', 'property': 'ras-zero-records-compat', 'value': not mode}
    response = r.raw_monitor('qom-set', arguments)
    require(response.get('error', {}).get('class') == 'GenericError', 'machine option changed after CPU realization')
    rejected.append({'arguments': arguments, 'response': response})
    return {'types': types, 'rejected': rejected, 'unchanged': r.pure_profiles(mode)}


def guest_snapshot(r, mode, epoch, elf, label):
    require(r.monitor('query-status')['running'] is False, 'snapshot must be stopped')
    control = r.read_ram(CONTROL, 80)
    values = struct.unpack('<10Q', control)
    require(values == (epoch, NONCE | epoch) + (0,) * 8, 'guest CPU_ON actual eight results/control')
    cpus = []
    files = []
    for cpu in range(8):
        raw = r.read_ram(BOX_BASE + cpu * BOX_STRIDE, BOX_BYTES)
        path = r.directory / f'{label}-cpu{cpu}.bin'
        path.write_bytes(raw)
        cpus.append(decode_guest(raw, cpu, mode, epoch, elf))
        files.append({'file': path.name, 'bytes': len(raw), 'sha256': digest(raw)})
    path = r.directory / f'{label}-control.bin'; path.write_bytes(control)
    return {'label': label, 'mode': mode, 'epoch': epoch, 'control': {'file': path.name,
            'bytes': len(control), 'sha256': digest(control)}, 'files': files, 'cpus': cpus}


def run_epoch(r, mode, epoch, elf, *, cold=False):
    require(r.monitor('query-status')['running'] is False, 'guest epoch must start stopped')
    if cold:
        r.write_ram(CONTROL, bytes(80))
        for cpu in range(8):
            r.write_ram(BOX_BASE + cpu * BOX_STRIDE, bytes(BOX_BYTES))
    r.write_ram(CONTROL, struct.pack('<2Q', epoch, NONCE | epoch))
    r.monitor('cont')
    deadline = time.monotonic() + 30
    polls = []
    while True:
        headers = [struct.unpack('<16Q', r.read_ram(BOX_BASE + cpu * BOX_STRIDE, 128)) for cpu in range(8)]
        polls.append([{'cpu': cpu, 'done': h[11], 'fatal': h[9], 'count': h[8]} for cpu, h in enumerate(headers)])
        require(all(h[9] == 0 for h in headers), 'guest unexpected exception; partial RAM/protocol retained')
        if all(h[11] == epoch for h in headers): break
        require(time.monotonic() < deadline, 'eight-CPU guest epoch timed out')
        time.sleep(0.01)
    r.monitor('stop')
    require(r.monitor('query-status')['running'] is False, 'QMP stop did not stop guest')
    result = guest_snapshot(r, mode, epoch, elf, f'epoch-{epoch}')
    result['polls'] = polls
    return result


def save(r, path, mode):
    require(r.monitor('query-status')['running'] is False, 'save guest must be stopped')
    r.monitor('migrate', file_channels(path))
    result = r.migrate_wait()
    raw = path.read_bytes()
    return {'file': 'streams/' + path.name, 'migration': result, 'wire': parse_stream(raw, mode)}


def incoming(r, path, mode):
    before = r.pure_profiles(mode)
    r.monitor('migrate-incoming', file_channels(path))
    progress = r.migrate_wait()
    # The first actual target observation after completion is pure QOM.
    after = r.pure_profiles(mode)
    require(r.monitor('query-status')['running'] is False, 'incoming autostarted guest')
    return {'before': before, 'after': after, 'migration': progress,
            'cont_before_complete': r.count['cont'], 'ram_before_postload_profiles': r.count['ram']}


def mutate(raw, wire, kind, cpu, value):
    section = wire['cpu_sections'][cpu]
    require(section['instance'] == cpu, 'mutation CPU identity')
    if kind == 'cpreg-nonzero':
        entry = section['new_registers'][value]
        require(entry['id'] == NEW_KVM_IDS[value], 'mutation exact CPreg index')
        offset, old, new = entry['value_offset'], bytes(8), (1).to_bytes(8, 'big')
    else:
        subs = [s for s in section['subsections'] if s['name'] == SUBSECTION]
        require(len(subs) == 1, 'mutation profile locator')
        sub = subs[0]
        if kind == 'profile-value':
            offset, old, new = sub['body_start'], (1).to_bytes(4, 'big'), value.to_bytes(4, 'big')
        elif kind == 'profile-version':
            offset, old, new = sub['body_start'] - 4, (1).to_bytes(4, 'big'), bytes(4)
        else:
            require(kind == 'profile-absent', 'unknown mutation')
            offset, old, new = sub['start'], raw[sub['start']:sub['end']], b''
    require(raw[offset:offset + len(old)] == old and old != new, 'mutation original bytes')
    modified = raw[:offset] + new + raw[offset + len(old):]
    return modified, {'kind': kind, 'cpu': cpu, 'value': value, 'offset': offset,
                      'old_hex': old.hex(), 'new_hex': new.hex(),
                      'original_bytes': len(raw), 'original_sha256': digest(raw),
                      'bytes': len(modified), 'sha256': digest(modified)}


def rejected_load(args, out, name, path, mode, needle):
    r = RasRig(args, out / name, mode=mode, incoming=True)
    row = {'fixture': name, 'mode': mode, 'expected_exit_code': 1,
           'incoming_file': 'streams/' + path.name, 'diagnostic_needle': needle}
    try:
        row['initial_profiles'] = r.pure_profiles(mode)
        r.expected_exit = 1
        r.monitor('migrate-incoming', file_channels(path))
        require(r.wait_exit(30) == 1, 'invalid migration must terminate QEMU with exit1')
        require(r.count['cont'] == r.count['ram'] == r.count['memory'] == 0, 'negative load executed guest or test RAM')
        row['actual_stderr_contains_needle'] = needle in r.stderr()
        require(row['actual_stderr_contains_needle'], 'wrong migration rejection diagnostic')
        row['actual_exit_code'] = r.process.returncode
    finally:
        r.close()
    return row


def sources():
    paths = [HERE / 'ras_zero_records' / name for name in ('start.S', 'probe.c', 'link.ld')]
    paths += [HERE / name for name in ('ras_zero_records_test_contract.py',
               'ras_zero_records_test_transport.py', 'test_ras_zero_records.py', 'qce_bam_test_transport.py')]
    return [{'relative_path': 'tb371fc/tests/' + p.relative_to(HERE).as_posix(),
             'bytes': p.stat().st_size, 'sha256': sha(p)} for p in paths]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--machine', default='tb371fc')
    parser.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    parser.add_argument('--out', required=True)
    parser.add_argument('--report')
    args = parser.parse_args()
    out = Path(args.out).resolve(); out.mkdir(parents=True, exist_ok=False)
    args.report = str(Path(args.report).resolve()) if args.report else str(out / 'result.json')
    (out / 'streams').mkdir()
    require(args.machine == 'tb371fc', 'this contract uses the exact board profile')
    binary = sha(args.qemu)
    report = {'schema': SCHEMA, 'passed': False, 'qemu_sha256': binary,
              'source_files': sources(), 'checks': [], 'processes': [], 'streams': [],
              'roundtrips': [], 'rejected_migrations': [], 'guest_epochs': [],
              'native_executed': False, 'original_inputs_used': False,
              'physical_RAS_state_verified': False, 'error_injection_verified': False,
              'Android_or_UI_verified': False}
    names = []
    elf = None
    try:
        elf = build_probe(args, out / 'build')
        report['probe_elf'] = elf
        for name, mode, accel in (('qtest-default-off', None, 'qtest'), ('qtest-on', True, 'qtest'),
                                  ('default-off', None, 'tcg'), ('explicit-off', False, 'tcg'), ('on-reset', True, 'tcg')):
            names.append(name); r = RasRig(args, out / name, mode=mode, accel=accel)
            row = {'fixture': name, 'mode': mode is True, 'accel': accel, 'epochs': []}
            try:
                row['initial_profiles'] = r.pure_profiles(mode is True)
                if accel == 'qtest':
                    row['qom_access'] = qom_access(r, mode is True)
                    require(r.count['cont'] == r.count['ram'] == 0, 'qtest pure observation executed guest')
                else:
                    first = run_epoch(r, mode is True, 1, elf, cold=True)
                    row['epochs'].append(first); report['guest_epochs'].append({'fixture': name, 'epoch': 1})
                    if name == 'on-reset':
                        # Clear stale command, PSCI results and every mailbox
                        # while paused, before resetting CPU0/secondary state.
                        r.write_ram(CONTROL, bytes(80))
                        for cpu in range(8):
                            r.write_ram(BOX_BASE + cpu * BOX_STRIDE, bytes(BOX_BYTES))
                        count = len(r.events)
                        r.monitor('system_reset')
                        # query-status flushes the asynchronous RESET event.
                        require(r.monitor('query-status')['running'] is False, 'reset resumed paused guest')
                        require(any(e.get('event') == 'RESET' for e in r.events[count:]), 'actual RESET event missing')
                        row['reset_event_seen'] = True
                        row['reset_profiles'] = r.pure_profiles(True)
                        row['epochs'].append(run_epoch(r, True, 2, elf, cold=False))
                        report['guest_epochs'].append({'fixture': name, 'epoch': 2})
            finally: r.close()
            report['checks'].append(row)
        for cpu in ('cortex-a55', 'cortex-a77'):
            name = 'invalid-' + cpu; names.append(name)
            r = RasRig(args, out / name, invalid_cpu=cpu)
            try:
                needle = 'tb371fc-ras-zero-records-compat requires the TB371FC A55/A77 RASv1 software-emulated profile'
                require(r.process.returncode == 1 and needle in r.stderr(), 'generic machine CPU opt-in was accepted')
                row = {'fixture': name, 'actual_exit_code': 1, 'diagnostic_needle': needle}
            finally: r.close()
            report['checks'].append(row)
        saved = {}
        for mode, label in ((True, 'on'), (False, 'off')):
            loop = {'mode': mode, 'fixtures': [], 'streams': [], 'incoming': []}
            for leg in range(3):
                name = label + ('-source', '-target', '-return')[leg]
                names.append(name); loop['fixtures'].append(name)
                r = RasRig(args, out / name, mode=mode, incoming=leg != 0)
                row = {'fixture': name, 'mode': mode, 'leg': leg, 'epochs': []}
                try:
                    if leg == 0:
                        row['initial_profiles'] = r.pure_profiles(mode)
                    else:
                        source_path = out / 'streams' / (label + ('-initial.mig' if leg == 1 else '-return.mig'))
                        row['incoming'] = incoming(r, source_path, mode)
                        require(row['incoming']['cont_before_complete'] == 0 and row['incoming']['ram_before_postload_profiles'] == 0,
                                'target observed RAM/executed before pure postload profiles')
                        row['restored_guest'] = guest_snapshot(r, mode, leg, elf, 'restored')
                        loop['incoming'].append({'fixture': name, **row['incoming']})
                    epoch = leg + 1
                    row['epochs'].append(run_epoch(r, mode, epoch, elf, cold=leg == 0))
                    report['guest_epochs'].append({'fixture': name, 'epoch': epoch})
                    if leg < 2:
                        path = out / 'streams' / (label + ('-initial.mig' if leg == 0 else '-return.mig'))
                        stream = save(r, path, mode)
                        report['streams'].append(stream); loop['streams'].append(stream['file'])
                        if leg == 0: saved[label] = (path, stream['wire'])
                        row['outgoing'] = stream['file']
                finally: r.close()
                report['checks'].append(row)
            report['roundtrips'].append(loop)
        # The only CPreg-index delta must be the seven explicit AA64 entries.
        for cpu in range(8):
            old = saved['off'][1]['cpu_sections'][cpu]['cpreg_ids']
            new = saved['on'][1]['cpu_sections'][cpu]['cpreg_ids']
            require(new == sorted(old + list(NEW_KVM_IDS)), 'off/on unrelated CPreg index changed')
        report['off_wire_has_no_RAS_subsection_or_new_cpregs'] = True
        for label, mode in (('on', False), ('off', True)):
            name = 'cross-' + label + '-to-' + ('on' if mode else 'off'); names.append(name)
            needle = ('Incoming CPU RAS zero-records mode differs from destination' if mode else '0 != 1')
            row = rejected_load(args, out, name, saved[label][0], mode, needle)
            report['checks'].append(row); report['rejected_migrations'].append(row)
        original = saved['on'][0].read_bytes(); wire = saved['on'][1]
        for name, kind, cpu, value in MUTATIONS:
            names.append(name)
            data, mutation = mutate(original, wire, kind, cpu, value)
            path = out / 'streams' / (name + '.mig'); path.write_bytes(data)
            stream = {'file': 'streams/' + path.name, 'bytes': len(data), 'sha256': digest(data), 'mutation': mutation}
            report['streams'].append(stream)
            needle = ('Incoming CPU RAS zero-records mode differs from destination' if kind == 'profile-absent' else
                      SUBSECTION if kind == 'profile-version' else
                      f'1 != {value:x}' if kind == 'profile-value' else 'post load hook failed for: cpu, version_id: 22')
            row = rejected_load(args, out, name, path, True, needle)
            row['mutation'] = mutation
            report['checks'].append(row); report['rejected_migrations'].append(row)
        require(tuple(names) == ORDERED and len(names) == 35, 'complete ordered fixture suite')
        for check in report['checks']:
            check.update(name=check['fixture'], passed=True)
        report['processes'] = cleanup_records(out, names)
        report['summary'] = {'checks': 35, 'processes': 35, 'normal_exit_processes': 11,
            'rejected_processes': 24, 'actual_Popen_wait_calls': sum(len(p['wait_calls']) for p in report['processes']),
            'successful_incoming': 4, 'roundtrips': 2, 'rejected_migrations': 22,
            'actual_stream_files': len(list((out / 'streams').glob('*.mig'))),
            'guest_epochs': len(report['guest_epochs']), 'cpus_per_epoch': 8,
            'operations_per_cpu_epoch': 112, 'guest_instruction_operations': len(report['guest_epochs']) * 8 * 112,
            'whole_cpu_sections_per_saved_stream': 8, 'new_zero_const_cpregs_per_enabled_cpu': 7,
            'retained_cpu_vmstate_version': 22, 'profile_subsection_version': 1, 'profile_payload_bytes': 4}
        require(report['summary']['actual_Popen_wait_calls'] == 59 and report['summary']['actual_stream_files'] == 24 and
                report['summary']['guest_epochs'] == 10, 'derived final counts')
        require(sha(args.qemu) == binary, 'QEMU binary changed')
        report['binary_unchanged'] = True
        report['passed'] = True
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
        report['processes'] = [dict(fixture=p.parent.name, **json.loads(p.read_bytes())) for p in sorted(out.glob('*/cleanup.json'))]
    finally:
        report['actual_files'] = [pin(p, out) for p in sorted(out.rglob('*')) if p.is_file()]
        put_json(args.report, report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'report': str(Path(args.report).resolve())}, sort_keys=True))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
