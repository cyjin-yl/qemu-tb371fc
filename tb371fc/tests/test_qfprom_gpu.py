#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled QEMU checks for two explicit, immutable GPU nvmem identities.

The unchanged owned-process QMP/QTest transport supplies actual protocol,
diagnostic, wait and drain evidence. This test does not read model source as an
oracle, attach native inputs, or execute guest CPUs. A/B are synthetic raw values.
The separate virtual-gpu-bin2-uncalibrated-v1 fixture is a declared virtual policy, not a
measurement of physical fuses or a claim of calibrated/working GPU hardware.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

TYPE = 'qcom-sm8250-qfprom-gpu'
PATH = '/machine/qfprom-gpu'
GUARD_SIZE = 32
CELLS = (('speed-bin', 0x00784190, (11,)),
         ('isense-slope', 0x007845c0, (8, 9, 10, 11)))
SPEED_ADDRESS, SLOPE_ADDRESS = 0x0078419b, 0x007845c8
A, B = (0xa5, 0x91c35a2d), (0x5a, 0x6e24b7d0)
POLICY, POLICY_NAME = (0x40, 0x00000000), 'virtual-gpu-bin2-uncalibrated-v1'
PROFILE = 0x51474600
SPMI_TYPE, SPMI_PATH = 'qcom-sm8250-spmi-debug-fuse', '/machine/spmi-debug-fuse'
SPMI_ADDRESS, SPMI_WORD = 0x007820a8, 0x01000000
DEFAULT_MACHINE = 'tb371fc,ufs-enabled=off,gcc-boot-configured=off'
SIZE_BY_WIDTH = {'b': 1, 'w': 2, 'l': 4, 'q': 8}
HELPER_SHA256 = 'a53f97b52fde52558943fd61f61ffff36c78fafa54815dfc7eb91b44a3555f3c'

ADMISSION_CASES = (
    ('enabled-no-values', True, None, None, 'speed'),
    ('enabled-missing-speed', True, None, A[1], 'speed'),
    ('enabled-missing-slope', True, A[0], None, 'slope'),
    ('speed-unbound', True, 'UNBOUND', A[1], 'speed'),
    ('speed-short', True, '0xa', A[1], 'speed'),
    ('speed-overflow', True, '0x1a5', A[1], 'speed'),
    ('speed-uppercase-prefix', True, '0Xa5', A[1], 'speed'),
    ('speed-uppercase-digit', True, '0xaF', A[1], 'speed'),
    ('speed-invalid-digit', True, '0xg5', A[1], 'speed'),
    ('speed-negative', True, '-1', A[1], 'speed'),
    ('speed-whitespace', True, ' 0xa5', A[1], 'speed'),
    ('slope-unbound', True, A[0], 'UNBOUND', 'slope'),
    ('slope-short', True, A[0], '0x123', 'slope'),
    ('slope-overflow', True, A[0], '0x123456789', 'slope'),
    ('slope-uppercase-prefix', True, A[0], '0X12345678', 'slope'),
    ('slope-uppercase-digit', True, A[0], '0x1234567A', 'slope'),
    ('slope-invalid-digit', True, A[0], '0x1234567g', 'slope'),
    ('slope-negative', True, A[0], '-1', 'slope'),
    ('slope-whitespace', True, A[0], '0x12345678 ', 'slope'),
    ('speed-while-off', False, A[0], None, 'off'),
    ('slope-while-off', False, None, A[1], 'off'),
    ('both-while-off', False, A[0], A[1], 'off'))

# Every otherwise readable start receives each forbidden scalar width. The
# 32-byte guards keep even readq at offset 11 in one callback. Every invalid byte
# and every byte write is covered; unaligned and overlapping starts are separate.
ACCESS_CASES = tuple(
    (write, width, cell, base, offset)
    for cell, base, readable in CELLS
    for write, width, offset in (
        [(False, 'b', offset) for offset in range(GUARD_SIZE) if offset not in readable] +
        [(True, 'b', offset) for offset in range(GUARD_SIZE)] +
        [(write, width, offset) for offset in readable
         for width in ('w', 'l', 'q') for write in (False, True)] +
        [(write, width, offset) for offset in (1, min(readable) - 1)
         for width in ('w', 'l', 'q') for write in (False, True)]))

INITIAL_CASES = ('default-off', 'explicit-off', 'enabled-a', 'enabled-b',
                 POLICY_NAME, 'spmi-only-independence', 'combined-spmi-independence')
MIGRATION_CASES = ('migration-off', 'migration-a', 'migration-b')
MISMATCH_CASES = ('off-to-on', 'on-to-off', 'speed-identity-mismatch',
                  'slope-identity-mismatch')
CORRUPTION_CASES = ('corrupt-profile', 'corrupt-speed-bin', 'corrupt-isense-slope')
EXPECTED_CHECK_NAMES = (INITIAL_CASES + tuple(case[0] for case in ADMISSION_CASES) +
                        MIGRATION_CASES + MISMATCH_CASES + CORRUPTION_CASES)


def identity(pair):
    if pair is None:
        return {'enabled': False, 'speed_bin_byte': 'UNBOUND',
                'isense_slope_word': 'UNBOUND'}
    return {'enabled': True, 'speed_bin_byte': f'0x{pair[0]:02x}',
            'isense_slope_word': f'0x{pair[1]:08x}',
            'decoded_speed_bin': (pair[0] >> 5) & 7}


def expected(pair):
    return {'enabled': pair is not None,
            'bound-speed-bin-byte': 'UNBOUND' if pair is None else f'0x{pair[0]:02x}',
            'bound-isense-slope-word': 'UNBOUND' if pair is None else f'0x{pair[1]:08x}'}


def snapshot(r, pair):
    result = {key: r.monitor('qom-get', {'path': PATH, 'property': key})
              for key in ('enabled', 'bound-speed-bin-byte', 'bound-isense-slope-word')}
    assert type(result['enabled']) is bool
    assert type(result['bound-speed-bin-byte']) is str
    assert type(result['bound-isense-slope-word']) is str
    assert result == expected(pair), (result, expected(pair))
    return result


def pure_twice(r, pair, label):
    before = dict(r.count)
    first, repeated = snapshot(r, pair), snapshot(r, pair)
    after = dict(r.count)
    assert first == repeated and after['qom_get'] - before['qom_get'] == 6
    assert all(before[key] == after[key] for key in before if key != 'qom_get')
    assert before['memory'] == after['memory'] == before['cont'] == after['cont'] == 0
    result = {'first': first, 'repeated': repeated, 'counters_before': before,
              'counters_after': after, 'first_getters': 3, 'repeated_getters': 3,
              'no_memory_or_cont': True}
    put_json(r.directory / (label + '.json'), result)
    return result


def machine_args(args, enabled=None, speed=None, slope=None, *, spmi=False):
    value = copy.copy(args)
    assert 'qfprom-gpu' not in args.machine and 'spmi-debug-fuse' not in args.machine
    value.machine = args.machine
    if enabled is not None:
        value.machine += ',qfprom-gpu-enabled=' + ('on' if enabled else 'off')
    for key, raw, digits in (('speed-bin-byte', speed, 2), ('isense-slope-word', slope, 8)):
        if raw is not None:
            text = f'0x{raw:0{digits}x}' if type(raw) is int else raw
            value.machine += ',qfprom-gpu-' + key + '=' + text
    if spmi:
        value.machine += ',spmi-debug-fuse-enabled=on,spmi-debug-fuse-word=0x01000000'
    return value


def configured(args, pair, *, spmi=False):
    return machine_args(args, pair is not None, *(pair or (None, None)), spmi=spmi)


def mappings(r, enabled):
    text = r.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    (r.directory / 'active-memory-map.txt').write_bytes(text.encode())
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(
        r'([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (' + re.escape(TYPE) + r'[^\s]*)(?:\s|$)', text)}
    wanted = {(base, base + GUARD_SIZE - 1, TYPE + '.' + cell + '-guard')
              for cell, base, _ in CELLS} if enabled else set()
    assert found == wanted, (found, wanted)
    return {'active_leaves': sorted(found), 'decode_guard_bytes': 64 if enabled else 0,
            'readable_bytes': 5 if enabled else 0}


def diagnostics(text, device=TYPE):
    return [line for line in text.splitlines() if line.startswith(device + ': rejected ')]


def core_diagnostics(text):
    return [(op, int(address, 16), int(size), region, reason)
            for op, address, size, region, reason in re.findall(
                r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)", text)]


def read_cells(r, pair):
    assert pair is not None
    old = r.stderr()
    values, commands = [], []
    for address in (SPEED_ADDRESS,) + tuple(SLOPE_ADDRESS + i for i in range(4)):
        command = f'readb {address:#x}'
        reply = r.qt(command)
        assert len(reply) == 1, (command, reply)
        value = int(reply[0], 0)
        assert 0 <= value <= 255
        values.append(value)
        commands.append({'command': command, 'value': value})
    wanted = [pair[0]] + list(pair[1].to_bytes(4, 'little'))
    assert values == wanted, (values, wanted)
    assert int.from_bytes(bytes(values[1:]), 'little') == pair[1]
    assert (values[0] >> 5) & 7 == (pair[0] >> 5) & 7
    current = r.stderr()
    assert diagnostics(current) == diagnostics(old)
    assert diagnostics(current, SPMI_TYPE) == diagnostics(old, SPMI_TYPE)
    assert core_diagnostics(current) == core_diagnostics(old)
    return {'reads': commands, 'raw_speed_bin_byte': values[0],
            'decoded_speed_bin': (values[0] >> 5) & 7,
            'raw_isense_slope_word': int.from_bytes(bytes(values[1:]), 'little'),
            'isense_slope_bytes_le': values[1:], 'no_rejection_diagnostic': True,
            'physical_identity_claimed': False}


def unmapped(r, address, pair, write=False):
    before = snapshot(r, pair)
    old = r.stderr()
    operation = 'write' if write else 'read'
    command = f'{operation}b {address:#x}' + (' 0x7d' if write else '')
    reply = r.qt(command)
    current = r.stderr()
    added = core_diagnostics(current)[len(core_diagnostics(old)):]
    assert added == [(operation, address, 1, '(null)', 'rejected')], (command, added)
    assert diagnostics(current) == diagnostics(old)
    assert diagnostics(current, SPMI_TYPE) == diagnostics(old, SPMI_TYPE)
    assert snapshot(r, pair) == before
    return {'command': command, 'protocol_reply': reply, 'core_diagnostic': added,
            'state_unchanged': True, 'qtest_ok_is_memtx_result': False,
            'read_error_bytes_used_as_identity': False}


def reject(r, write, width, cell, base, offset):
    before = snapshot(r, A)
    old = r.stderr()
    size = SIZE_BY_WIDTH[width]
    assert 0 <= offset and offset + size <= GUARD_SIZE
    value = 0x12345678abcdef90 & ((1 << (8 * size)) - 1)
    operation = 'write' if write else 'read'
    command = f'{operation}{width} {base + offset:#x}' + (f' {value:#x}' if write else '')
    reply = r.qt(command)
    current = r.stderr()
    wanted = f'{TYPE}: rejected {operation} cell={cell} offset={offset:#x} size={size}'
    added = diagnostics(current)[len(diagnostics(old)):]
    assert added == [wanted], (command, added, wanted)
    assert core_diagnostics(current) == core_diagnostics(old)
    assert diagnostics(current, SPMI_TYPE) == diagnostics(old, SPMI_TYPE)
    after = snapshot(r, A)
    assert after == before
    preserved = read_cells(r, A)
    return {'command': command, 'diagnostic': added, 'protocol_reply': reply,
            'before': before, 'after': after, 'preserved_reads': preserved,
            'state_unchanged': True, 'single_device_rejection_observed': True,
            'qtest_ok_is_memtx_result': False, 'read_error_bytes_used_as_identity': False}


def immutable(r):
    result = []
    for path, key, value in (
            (PATH, 'enabled', False), (PATH, 'speed-bin-byte', f'0x{B[0]:02x}'),
            (PATH, 'isense-slope-word', f'0x{B[1]:08x}'),
            (PATH, 'bound-speed-bin-byte', f'0x{B[0]:02x}'),
            (PATH, 'bound-isense-slope-word', f'0x{B[1]:08x}'),
            ('/machine', 'qfprom-gpu-enabled', False),
            ('/machine', 'qfprom-gpu-speed-bin-byte', f'0x{B[0]:02x}'),
            ('/machine', 'qfprom-gpu-isense-slope-word', f'0x{B[1]:08x}')):
        before = snapshot(r, A)
        reply = r.raw_monitor('qom-set', {'path': path, 'property': key, 'value': value})
        assert 'return' not in reply and reply.get('error', {}).get('class') == 'GenericError', reply
        assert key in reply['error']['desc'], reply
        assert snapshot(r, A) == before
        result.append({'path': path, 'property': key, 'error': reply['error'],
                       'state_unchanged': True, 'preserved_reads': read_cells(r, A)})
    return result


def read_spmi(r):
    state = {key: r.monitor('qom-get', {'path': SPMI_PATH, 'property': key})
             for key in ('enabled', 'bound-word')}
    assert type(state['enabled']) is bool
    assert state == {'enabled': True, 'bound-word': '0x01000000'}, state
    old = r.stderr()
    reply = r.qt(f'readl {SPMI_ADDRESS:#x}')
    assert len(reply) == 1 and int(reply[0], 0) == SPMI_WORD, reply
    current = r.stderr()
    assert diagnostics(current, SPMI_TYPE) == diagnostics(old, SPMI_TYPE)
    assert diagnostics(current) == diagnostics(old)
    assert core_diagnostics(current) == core_diagnostics(old)
    return {'qom': state, 'address': SPMI_ADDRESS, 'value': int(reply[0], 0)}


def spmi_independence(args, out, report):
    for name, pair in (('spmi-only-independence', None), ('combined-spmi-independence', A)):
        r = Rig(configured(args, pair, spmi=True), out / name)
        try:
            pure = pure_twice(r, pair, 'initial-pure')
            mapping = mappings(r, pair is not None)
            status = r.monitor('query-status')
            assert status['running'] is False
            first_spmi = read_spmi(r)
            row = {'name': name, 'passed': True, 'pure': pure, 'mapping': mapping,
                   'original_spmi_first_read': first_spmi}
            if pair is None:
                row['gpu_cells_unmapped'] = [
                    unmapped(r, address, None, write)
                    for address in (SPEED_ADDRESS,) + tuple(SLOPE_ADDRESS + i for i in range(4))
                    for write in (False, True)]
            else:
                row['gpu_first_reads'] = read_cells(r, A)
                row['gpu_rejections'] = [reject(r, True, 'b', 'speed-bin', CELLS[0][1], 11),
                                         reject(r, False, 'q', 'isense-slope', CELLS[1][1], 11)]
                assert read_spmi(r) == first_spmi
                before = snapshot(r, A)
                old = r.stderr()
                reply = r.qt(f'readb {SPMI_ADDRESS:#x}')
                current = r.stderr()
                added = diagnostics(current, SPMI_TYPE)[len(diagnostics(old, SPMI_TYPE)):]
                wanted = f'{SPMI_TYPE}: rejected read offset=0x8 size=1'
                assert added == [wanted], added
                assert diagnostics(current) == diagnostics(old)
                assert core_diagnostics(current) == core_diagnostics(old)
                assert snapshot(r, A) == before
                row['original_spmi_byte_rejected'] = {
                    'protocol_reply': reply, 'diagnostic': added,
                    'gpu_preserved_reads': read_cells(r, A),
                    'qtest_ok_is_memtx_result': False}
            assert read_spmi(r) == first_spmi
            r.monitor('system_reset')
            assert snapshot(r, pair) == expected(pair)
            assert mappings(r, pair is not None) == mapping
            assert read_spmi(r) == first_spmi
            if pair is not None:
                row['reset_gpu_reads'] = read_cells(r, pair)
            row['reset_preserved_both_configurations'] = True
            assert r.count['cont'] == 0
            report['checks'].append(row)
        finally:
            r.close()


def wire(pair):
    return struct.pack('>IBI', PROFILE | (pair is not None), *(pair or (0, 0)))


def locate_section(raw, pair):
    # Locate and verify this complete ordinary FULL section only; this is not a
    # whole-machine migration decoder and makes no claim about other RAM state.
    assert raw[:8] == b'QEVM\x00\x00\x00\x03'
    name = TYPE.encode()
    needle = bytes([len(name)]) + name
    matches, pos = [], 0
    while True:
        at = raw.find(needle, pos)
        if at < 0:
            break
        pos = at + 1
        header, body = at - 5, at + len(needle) + 8
        if header >= 8 and raw[header] in (1, 4):
            matches.append((header, body))
    assert len(matches) == 1, matches
    header, body = matches[0]
    assert raw[header] == 4 and struct.unpack_from('>II', raw, body - 8) == (0, 1)
    section_id = struct.unpack_from('>I', raw, header + 1)[0]
    payload = wire(pair)
    assert len(payload) == 9 and raw[body:body + 9] == payload
    assert raw[body + 9:body + 14] == b'\x7e' + struct.pack('>I', section_id)
    return {'section_name': TYPE, 'section_type': 4, 'instance_id': 0, 'version': 1,
            'payload_offset': body, 'payload_bytes': 9, 'mutable_bytes': 0,
            'payload_hex': payload.hex(), 'payload_sha256': hashlib.sha256(payload).hexdigest(),
            'fields': [{'name': 'profile', 'offset': 0, 'bytes': 4},
                       {'name': 'speed_bin', 'offset': 4, 'bytes': 1},
                       {'name': 'isense_slope', 'offset': 5, 'bytes': 4}],
            'all_nine_bytes_verified': True, 'whole_machine_payload_oracle_claimed': False}


def save_stream(r, path, pair):
    before = snapshot(r, pair)
    r.monitor('migrate', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False and status['status'] == 'postmigrate'
    assert snapshot(r, pair) == before and r.count['cont'] == 0
    raw = path.read_bytes()
    result = {'file': path.name, 'bytes': len(raw), 'sha256': sha(path), 'snapshot': before,
              'section': locate_section(raw, pair), 'query_migrate': migration,
              'query_status': status}
    put_json(Path(str(path) + '.section.json'), result)
    return result


def load_stream(r, path, pair):
    pre = pure_twice(r, pair, 'preload-pure')
    r.monitor('migrate-incoming', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False
    post = pure_twice(r, pair, 'first-postload-pure')
    assert r.count['memory'] == r.count['cont'] == 0
    mapping = mappings(r, pair is not None)
    reads = read_cells(r, pair) if pair is not None else [
        unmapped(r, SPEED_ADDRESS, None), unmapped(r, SLOPE_ADDRESS, None)]
    r.monitor('system_reset')
    assert snapshot(r, pair) == expected(pair)
    assert mappings(r, pair is not None) == mapping
    reset_reads = read_cells(r, pair) if pair is not None else [
        unmapped(r, SPEED_ADDRESS, None), unmapped(r, SLOPE_ADDRESS, None)]
    return {'preload': pre, 'first_postload': post, 'query_migrate': migration,
            'query_status': status, 'mapping': mapping, 'postload_reads': reads,
            'reset_reads': reset_reads, 'reset_preserved_configuration': True,
            'whole_machine_ram_not_loaded_claimed': False}


def reject_stream(r, path, pair, diagnostic):
    pre = pure_twice(r, pair, 'preload-pure')
    r.expected_exit = 1
    before = dict(r.count)
    command_error = None
    try:
        r.monitor('migrate-incoming', file_channels(path))
    except (AssertionError, OSError, EOFError, TimeoutError) as exc:
        command_error = f'{type(exc).__name__}: {exc}'
    code = r.wait_exit(30)
    text = r.stderr()
    assert code == 1 and diagnostic in text and 'load of migration failed' in text.lower(), text
    assert r.count == before and r.count['memory'] == r.count['cont'] == 0
    return {'preload': pre, 'incoming_command_error': command_error, 'exit_code': code,
            'specific_diagnostic': diagnostic, 'specific_diagnostic_observed': True,
            'counters_before_incoming': before, 'counters_after_exit': dict(r.count),
            'no_device_or_ram_commands': True, 'no_cont': True,
            'failed_process_postload_qom_claimed': False,
            'whole_machine_ram_not_loaded_claimed': False,
            'stderr_sha256': sha(r.directory / 'qemu.stderr')}


def run_cases(args, out, report):
    streams = out / 'streams'
    streams.mkdir()
    saved = {}
    for name, enabled, pair in (('default-off', None, None), ('explicit-off', False, None),
                                 ('enabled-a', True, A), ('enabled-b', True, B),
                                 (POLICY_NAME, True, POLICY)):
        r = Rig(machine_args(args, enabled, *(pair or (None, None))), out / name)
        try:
            pure = pure_twice(r, pair, 'initial-pure')
            mapping = mappings(r, pair is not None)
            status = r.monitor('query-status')
            assert status['running'] is False
            detail = {'name': name, 'passed': True, 'pure': pure, 'mapping': mapping,
                      'identity': identity(pair)}
            if pair is None:
                detail['unmapped'] = [
                    unmapped(r, address, None, write)
                    for address in (SPEED_ADDRESS,) + tuple(SLOPE_ADDRESS + i for i in range(4))
                    for write in (False, True)]
            else:
                detail['first_reads'] = read_cells(r, pair)
                if pair == A:
                    report['access_rejections'] = [reject(r, *case) for case in ACCESS_CASES]
                    detail['immutable_properties'] = immutable(r)
                    detail['unmapped_neighbors_and_gap'] = [
                        unmapped(r, address, A, write)
                        for address in (CELLS[0][1] - 1, CELLS[0][1] + GUARD_SIZE,
                                        CELLS[1][1] - 1, CELLS[1][1] + GUARD_SIZE,
                                        0x00784300, SPMI_ADDRESS)
                        for write in (False, True)]
                if name == POLICY_NAME:
                    detail['virtual_policy_fixture_only'] = True
                    detail['physical_fuse_measured'] = False
                    detail['low_five_speed_bits_are_synthetic_zero'] = True
                    detail['calibration_or_gpu_functionality_claimed'] = False
                    assert detail['first_reads']['decoded_speed_bin'] == 2
            r.monitor('system_reset')
            assert snapshot(r, pair) == expected(pair)
            assert mappings(r, pair is not None) == mapping
            if pair is not None:
                detail['reset_reads'] = read_cells(r, pair)
            else:
                detail['reset_unmapped'] = [unmapped(r, SPEED_ADDRESS, None),
                                           unmapped(r, SLOPE_ADDRESS, None)]
            detail['reset_preserved_configuration'] = True
            if name in ('default-off', 'enabled-a', 'enabled-b'):
                key = {'default-off': 'off', 'enabled-a': 'a', 'enabled-b': 'b'}[name]
                path = streams / (key + '.mig')
                saved[key] = (path, save_stream(r, path, pair), pair)
            assert r.count['cont'] == 0
            report['checks'].append(detail)
        finally:
            r.close()
    spmi_independence(args, out, report)
    messages = {
        'speed': TYPE + ': enabled profile requires explicit speed-bin-byte=0xhh '
                 '(two lowercase hex digits); no value is bound by default',
        'slope': TYPE + ': enabled profile requires explicit isense-slope-word=0xhhhhhhhh '
                 '(eight lowercase hex digits); no value is bound by default',
        'off': TYPE + ': speed-bin-byte and isense-slope-word require enabled=on'}
    for name, enabled, speed, slope, field in ADMISSION_CASES:
        r = Rig(machine_args(args, enabled, speed, slope), out / name,
                expect_realize_failure=True)
        try:
            message, text = messages[field], r.stderr()
            assert text.count(message) == 1 and r.process.returncode == 1, text
            assert r.peers == {} and r.count['memory'] == r.count['cont'] == 0
            row = {'name': name, 'passed': True, 'enabled': enabled,
                   'speed_bin_byte_argument': speed, 'isense_slope_word_argument': slope,
                   'exit_code': 1, 'specific_diagnostic': message,
                   'specific_diagnostic_observed': True, 'protocols_not_opened': True}
            report['admission_rejections'].append(row)
            report['checks'].append(row)
        finally:
            r.close()
    for key in ('off', 'a', 'b'):
        path, source, pair = saved[key]
        name = 'migration-' + key
        r = Rig(configured(args, pair), out / name, incoming=True)
        try:
            row = {'name': name, 'passed': True, 'source': source,
                   'observation': load_stream(r, path, pair)}
            report['migrations'].append(row)
            report['checks'].append({'name': name, 'passed': True})
        finally:
            r.close()
    pairs = (('off-to-on', 'off', A, 'profile'), ('on-to-off', 'a', None, 'profile'),
             ('speed-identity-mismatch', 'a', (B[0], A[1]), 'speed_bin'),
             ('slope-identity-mismatch', 'a', (A[0], B[1]), 'isense_slope'))
    for name, key, target, field in pairs:
        path, source, incoming_pair = saved[key]
        if field == 'profile':
            actual, incoming = PROFILE | (target is not None), PROFILE | (incoming_pair is not None)
        else:
            index = 0 if field == 'speed_bin' else 1
            actual, incoming = target[index], incoming_pair[index]
            assert target[1 - index] == incoming_pair[1 - index]
        r = Rig(configured(args, target), out / name, incoming=True)
        try:
            row = {'name': name, 'passed': True, 'source_file': path.name,
                   'source_sha256': source['sha256'], 'mismatched_field': field,
                   'incoming_identity': identity(incoming_pair), 'target_identity': identity(target),
                   **reject_stream(r, path, target, f'{actual:x} != {incoming:x}')}
            report['rejected_migrations'].append(row)
            report['checks'].append({'name': name, 'passed': True})
        finally:
            r.close()
    path, source, pair = saved['a']
    original = path.read_bytes()
    for field, delta, width, old in (('profile', 0, 4, PROFILE | 1),
                                    ('speed-bin', 4, 1, A[0]),
                                    ('isense-slope', 5, 4, A[1])):
        name = 'corrupt-' + field
        target = streams / (name + '.mig')
        changed = bytearray(original)
        offset = source['section']['payload_offset'] + delta
        assert original[offset:offset + width] == old.to_bytes(width, 'big')
        changed[offset:offset + width] = (old ^ 1).to_bytes(width, 'big')
        differing = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
        assert len(changed) == len(original) and len(differing) == 1
        assert (original[differing[0]] ^ changed[differing[0]]).bit_count() == 1
        target.write_bytes(changed)
        assert path.read_bytes() == original
        r = Rig(configured(args, A), out / name, incoming=True)
        try:
            row = {'name': name, 'passed': True, 'field': field, 'field_offset': offset,
                   'field_bytes': width, 'changed_bits': 1, 'changed_byte_offsets': differing,
                   'source_unchanged': True, 'source_sha256': sha(path),
                   'mutated_sha256': sha(target),
                   **reject_stream(r, target, A, f'{old:x} != {old ^ 1:x}')}
            report['corrupted_stream_rejections'].append(row)
            report['checks'].append({'name': name, 'passed': True})
        finally:
            r.close()
    report['actual_stream_files'] = [
        {'file': str(p.relative_to(out)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
        for p in sorted(streams.glob('*.mig'))]
    assert len(report['actual_stream_files']) == 6


def closed_children(out):
    records = verify_cleanup(out)
    assert {row['fixture'] for row in records} == set(EXPECTED_CHECK_NAMES)
    assert len(records) == len(EXPECTED_CHECK_NAMES)
    normal = set(INITIAL_CASES + MIGRATION_CASES)
    admission = {case[0] for case in ADMISSION_CASES}
    for row in records:
        status = 0 if row['fixture'] in normal else 1
        assert row['expected_exit_code'] == row['exit_code'] == status
        assert row['counters']['cont'] == row['counters']['ram'] == 0
        assert row['wait_calls'] and all(wait['called'] == 'Popen.wait' and
                                         wait['returned_exit_code'] == status
                                         for wait in row['wait_calls'])
        if row['fixture'] in admission:
            assert all(channel.get('not_opened') is True for channel in row['drains'].values())
        else:
            assert set(row['drains']) == {'qmp', 'qtest'}
            assert all(channel.get('not_opened') is not True for channel in row['drains'].values())
        if status == 0:
            assert row['quit_sent'] is True and row['quit_acknowledged'] is True
        assert all(item['complete_after_actual_wait'] is True for item in row['stdio'].values())
    return records


def raw_artifacts(out):
    result = []
    admission = {case[0] for case in ADMISSION_CASES}
    for name in EXPECTED_CHECK_NAMES:
        required = ['command.json', 'child.json', 'protocol.jsonl', 'qemu.stdout',
                    'qemu.stderr', 'cleanup.json']
        if name not in admission:
            required.append('qtest-server.log')
        records = []
        for leaf in required:
            path = out / name / leaf
            assert path.is_file(), path
            records.append({'file': str(path.relative_to(out)), 'bytes': path.stat().st_size,
                            'sha256': sha(path)})
        result.append({'fixture': name, 'files': records})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--machine', default=DEFAULT_MACHINE)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:
        parser.error('Assertions are required; -O is not supported')
    if not args.qemu.is_file() or not os.access(args.qemu, os.X_OK):
        parser.error('actual executable compiled QEMU binary is required')
    helper = Path(__file__).with_name('qce_bam_test_transport.py')
    assert sha(helper) == HELPER_SHA256, 'the existing transport must be unchanged'
    assert len(ACCESS_CASES) == len(set(ACCESS_CASES)) == 177
    assert len(EXPECTED_CHECK_NAMES) == len(set(EXPECTED_CHECK_NAMES)) == 39
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    before = sha(args.qemu)
    self_before = sha(Path(__file__))
    report = {
        'schema': 'tb371fc-qfprom-gpu-v1', 'passed': False, 'expected_checks': 39,
        'expected_check_names': list(EXPECTED_CHECK_NAMES), 'expected_access_rejections': 177,
        'scope': 'two-explicit-immutable-gpu-nvmem-identities-only',
        'synthetic_identities': [identity(A), identity(B)],
        'virtual_policy_fixture': {'name': POLICY_NAME, **identity(POLICY),
                                   'low_five_speed_bits_are_synthetic_zero': True,
                                   'physical_identity_claimed': False},
        'vmstate_version': 1, 'vmstate_payload_bytes': 9, 'mutable_state_bytes': 0,
        'decode_guard_bytes': 64, 'readable_bytes': 5,
        'speed_bin_address': SPEED_ADDRESS, 'isense_slope_address': SLOPE_ADDRESS,
        'isense_slope_read_order': 'four consecutive bytes, little endian',
        'original_spmi_independence_word': '0x01000000',
        'guest_cpu_executed': False, 'native_kernel_executed': False,
        'physical_fuse_measured': False, 'gpu_modeled': False,
        'gpu_calibration_or_functionality_claimed': False,
        'other_qfprom_cells_supplied': False, 'normal_spmi_abi_changed': False,
        'automatic_test_retries': 0, 'qtest_ok_is_memtx_result': False,
        'test_oracle_reads_device_implementation': False,
        'checks': [], 'admission_rejections': [], 'access_rejections': [],
        'migrations': [], 'rejected_migrations': [], 'corrupted_stream_rejections': [],
        'actual_stream_files': [], 'raw_artifacts': [], 'process_cleanup': [],
        'process_cleanup_verified': False, 'binary_unchanged': False,
        'test_sources_unchanged': False, 'qemu_sha256_before': before,
        'test_sha256_before': self_before, 'transport_sha256_before': sha(helper)}
    try:
        run_cases(args, out, report)
        report['process_cleanup'] = closed_children(out)
        assert [row['name'] for row in report['checks']] == list(EXPECTED_CHECK_NAMES)
        assert all(row['passed'] is True for row in report['checks'])
        assert len(report['admission_rejections']) == 22
        assert len(report['access_rejections']) == 177
        assert len(report['migrations']) == 3 and len(report['rejected_migrations']) == 4
        assert len(report['corrupted_stream_rejections']) == 3
        report['raw_artifacts'] = raw_artifacts(out)
        report['process_cleanup_verified'] = True
        report['qemu_sha256_after'] = sha(args.qemu)
        report['test_sha256_after'] = sha(Path(__file__))
        report['transport_sha256_after'] = sha(helper)
        assert report['qemu_sha256_after'] == before
        assert report['test_sha256_after'] == self_before
        assert report['transport_sha256_after'] == report['transport_sha256_before'] == HELPER_SHA256
        report['binary_unchanged'] = report['test_sources_unchanged'] = True
        report['passed'] = True
    except BaseException:
        report['failure'] = traceback.format_exc()
        try:
            report['process_cleanup'] = verify_cleanup(out)
        except BaseException:
            report['cleanup_failure'] = traceback.format_exc()
        for key, path in (('qemu_sha256_after', args.qemu), ('test_sha256_after', Path(__file__)),
                          ('transport_sha256_after', helper)):
            try:
                report[key] = sha(path)
            except OSError as exc:
                report[key + '_error'] = f'{type(exc).__name__}: {exc}'
    put_json(out / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'result': str(out / 'result.json')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
