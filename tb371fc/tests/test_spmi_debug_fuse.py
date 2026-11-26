#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled QEMU checks for the explicitly configured immutable availability word.

Uses the existing owned-process QMP/QTest transport unchanged. No implementation
source is read as an oracle, no native input is attached and no cont is issued.
The A/B words are synthetic; the separate policy fixture is virtual, not measured.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

TYPE = 'qcom-sm8250-spmi-debug-fuse'
PATH = '/machine/spmi-debug-fuse'
BASE, SIZE, WORD_OFFSET = 0x007820a0, 16, 8
A, B, POLICY = 0xa5c3124f, 0x6a24c591, 0x01000000
DEFAULT_MACHINE = 'tb371fc,ufs-enabled=off,gcc-boot-configured=off'
ADMISSION_CASES = (
    ('enabled-no-word', True, None), ('enabled-unbound', True, 'UNBOUND'),
    ('enabled-short', True, '0x123'), ('enabled-overflow', True, '0x123456789'),
    ('enabled-uppercase-prefix', True, '0X12345678'),
    ('enabled-uppercase-digit', True, '0x1234567A'),
    ('enabled-invalid-digit', True, '0x1234567g'),
    ('word-while-off', False, f'{A:#010x}'))
ACCESS_CASES = tuple(
    [(False, width, offset) for width, offset in
     [('b', 8), ('w', 8), ('q', 8), ('l', 0), ('l', 4), ('l', 12),
      ('l', 9), ('l', 10), ('l', 11), ('q', 1)]] +
    [(True, width, offset) for width, offset in
     [('b', 8), ('w', 8), ('l', 8), ('q', 8), ('l', 0), ('l', 4),
      ('l', 12), ('l', 9), ('l', 10), ('l', 11), ('q', 1)]])
SIZE_BY_WIDTH = {'b': 1, 'w': 2, 'l': 4, 'q': 8}


def expected(word):
    return {'enabled': word is not None,
            'bound-word': 'UNBOUND' if word is None else f'{word:#010x}'}


def snapshot(r, word):
    result = {key: r.monitor('qom-get', {'path': PATH, 'property': key})
              for key in ('enabled', 'bound-word')}
    assert type(result['enabled']) is bool and type(result['bound-word']) is str
    assert result == expected(word), (result, expected(word))
    return result


def pure_twice(r, word, label):
    before = dict(r.count)
    first = snapshot(r, word)
    repeated = snapshot(r, word)
    after = dict(r.count)
    assert first == repeated
    assert after['qom_get'] - before['qom_get'] == 4
    for key in before:
        if key != 'qom_get':
            assert before[key] == after[key]
    assert before['memory'] == after['memory'] == before['cont'] == after['cont'] == 0
    result = {'first': first, 'repeated': repeated, 'counters_before': before,
              'counters_after': after, 'first_getters': 2, 'repeated_getters': 2,
              'no_memory_or_cont': True}
    put_json(r.directory / (label + '.json'), result)
    return result


def machine_args(args, enabled=None, word=None):
    value = copy.copy(args)
    assert 'spmi-debug-fuse' not in args.machine
    value.machine = args.machine
    if enabled is not None:
        value.machine += ',spmi-debug-fuse-enabled=' + ('on' if enabled else 'off')
    if word is not None:
        value.machine += ',spmi-debug-fuse-word=' + (f'{word:#010x}' if type(word) is int else word)
    return value


def mappings(r, enabled):
    text = r.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    (r.directory / 'active-memory-map.txt').write_bytes(text.encode())
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(
        r'([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (' + re.escape(TYPE) + r'[^\s]*)(?:\s|$)', text)}
    wanted = {(BASE, BASE + SIZE - 1, TYPE + '.decode-guard')} if enabled else set()
    assert found == wanted, (found, wanted)
    return {'active_leaves': sorted(found), 'decode_guard_bytes': SIZE if enabled else 0,
            'readable_bytes': 4 if enabled else 0}


def diagnostics(text):
    return [line for line in text.splitlines() if line.startswith(TYPE + ': rejected ')]


def core_diagnostics(text):
    return [(op, int(address, 16), int(size), region, reason)
            for op, address, size, region, reason in re.findall(
                r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)", text)]


def read_word(r, word):
    old = r.stderr()
    reply = r.qt(f'readl {BASE + WORD_OFFSET:#x}')
    assert len(reply) == 1 and int(reply[0], 0) == word
    assert diagnostics(r.stderr()) == diagnostics(old)
    assert core_diagnostics(r.stderr()) == core_diagnostics(old)
    return {'command': f'readl {BASE + WORD_OFFSET:#x}', 'value': int(reply[0], 0)}


def unmapped(r, address, word, write=False):
    before = snapshot(r, word)
    old = r.stderr()
    command = f"{'writel' if write else 'readl'} {address:#x}" + (' 0' if write else '')
    reply = r.qt(command)
    current = r.stderr()
    added = core_diagnostics(current)[len(core_diagnostics(old)):]
    assert added == [('write' if write else 'read', address, 4, '(null)', 'rejected')], added
    assert diagnostics(current) == diagnostics(old)
    assert snapshot(r, word) == before
    return {'command': command, 'protocol_reply': reply, 'core_diagnostic': added,
            'state_unchanged': True, 'qtest_ok_is_memtx_result': False}


def reject(r, write, width, offset):
    before = snapshot(r, A)
    old = r.stderr()
    size = SIZE_BY_WIDTH[width]
    value = 0x12345678abcdef90 & ((1 << (8 * size)) - 1)
    operation = 'write' if write else 'read'
    command = f'{operation}{width} {BASE + offset:#x}' + (f' {value:#x}' if write else '')
    reply = r.qt(command)
    current = r.stderr()
    wanted = f'{TYPE}: rejected {operation} offset={offset:#x} size={size}'
    added = diagnostics(current)[len(diagnostics(old)):]
    assert added == [wanted], (command, added, wanted)
    assert core_diagnostics(current) == core_diagnostics(old)
    after = snapshot(r, A)
    assert before == after
    preserved = read_word(r, A)
    return {'command': command, 'diagnostic': added, 'protocol_reply': reply,
            'before': before, 'after': after, 'preserved_read': preserved,
            'state_unchanged': True, 'qtest_ok_is_memtx_result': False,
            'read_error_bytes_used_as_identity': False}


def immutable(r, word):
    result = []
    for path, key, value in ((PATH, 'enabled', False), (PATH, 'fuse-word', f'{B:#010x}'),
                             (PATH, 'bound-word', f'{B:#010x}'),
                             ('/machine', 'spmi-debug-fuse-enabled', False),
                             ('/machine', 'spmi-debug-fuse-word', f'{B:#010x}')):
        before = snapshot(r, word)
        reply = r.raw_monitor('qom-set', {'path': path, 'property': key, 'value': value})
        assert 'return' not in reply and reply.get('error', {}).get('class') == 'GenericError', reply
        assert key in reply['error']['desc'], reply
        assert snapshot(r, word) == before
        result.append({'path': path, 'property': key, 'error': reply['error'],
                       'state_unchanged': True})
    return result


def wire(word):
    return struct.pack('>II', 0x53444600 | (word is not None), 0 if word is None else word)


def locate_section(raw, word):
    # A locator for this one ordinary FULL section, not a whole-machine parser.
    assert raw[:8] == b'QEVM\x00\x00\x00\x03'
    name = TYPE.encode();needle = bytes([len(name)]) + name
    matches, pos = [], 0
    while True:
        at = raw.find(needle, pos)
        if at < 0: break
        pos = at + 1
        header, body = at - 5, at + len(needle) + 8
        if header >= 8 and raw[header] in (1, 4):matches.append((header, body))
    assert len(matches) == 1, matches
    header, body = matches[0]
    assert raw[header] == 4 and struct.unpack_from('>II', raw, body - 8) == (0, 1)
    section_id = struct.unpack_from('>I', raw, header + 1)[0]
    assert raw[body:body + 8] == wire(word)
    assert raw[body + 8:body + 13] == b'\x7e' + struct.pack('>I', section_id)
    return {'section_name': TYPE, 'section_type': 4, 'instance_id': 0, 'version': 1,
            'payload_offset': body, 'payload_bytes': 8, 'mutable_bytes': 0,
            'payload_hex': wire(word).hex(), 'payload_sha256': hashlib.sha256(wire(word)).hexdigest(),
            'all_eight_bytes_verified': True, 'whole_machine_payload_oracle_claimed': False}


def save_stream(r, path, word):
    before = snapshot(r, word)
    r.monitor('migrate', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False and status['status'] == 'postmigrate'
    assert snapshot(r, word) == before and r.count['cont'] == 0
    raw = path.read_bytes();section = locate_section(raw, word)
    result = {'file': path.name, 'bytes': len(raw), 'sha256': sha(path), 'snapshot': before,
              'section': section, 'query_migrate': migration, 'query_status': status}
    put_json(Path(str(path) + '.section.json'), result)
    return result


def load_stream(r, path, word):
    pre = pure_twice(r, word, 'preload-pure')
    r.monitor('migrate-incoming', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status');assert status['running'] is False
    post = pure_twice(r, word, 'first-postload-pure')
    assert r.count['memory'] == r.count['cont'] == 0
    r.monitor('system_reset');assert snapshot(r, word) == expected(word)
    if word is not None:read_word(r, word)
    return {'preload': pre, 'first_postload': post, 'query_migrate': migration,
            'query_status': status, 'reset_preserved_configuration': True,
            'whole_machine_ram_not_loaded_claimed': False}


def reject_stream(r, path, word, diagnostic):
    pre = pure_twice(r, word, 'preload-pure')
    r.expected_exit = 1
    before = dict(r.count);command_error = None
    try:r.monitor('migrate-incoming', file_channels(path))
    except (AssertionError, OSError, EOFError, TimeoutError) as exc:
        command_error = f'{type(exc).__name__}: {exc}'
    code = r.wait_exit(30);text = r.stderr()
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
    streams = out / 'streams';streams.mkdir()
    saved = {}
    for name, enabled, word in (('default-off', None, None), ('explicit-off', False, None),
                                 ('enabled-a', True, A), ('enabled-b', True, B)):
        r = Rig(machine_args(args, enabled, word), out / name)
        try:
            pure = pure_twice(r, word, 'initial-pure')
            mapping = mappings(r, word is not None)
            status = r.monitor('query-status');assert status['running'] is False
            detail = {'name': name, 'passed': True, 'pure': pure, 'mapping': mapping}
            if word is None:
                detail['unmapped'] = [unmapped(r, BASE + offset, None, write)
                                     for offset in (0, 8, 12) for write in (False, True)]
            else:
                detail['first_read'] = read_word(r, word)
                if word == A:
                    report['access_rejections'] = [reject(r, *case) for case in ACCESS_CASES]
                    detail['immutable_properties'] = immutable(r, A)
                    detail['unmapped_neighbors_and_debug_core'] = [
                        unmapped(r, address, A, write)
                        for address, write in ((BASE - 4, False), (BASE - 4, True),
                                               (BASE + SIZE, False), (BASE + SIZE, True),
                                               (0x06b0f000, False))]
            r.monitor('system_reset');assert snapshot(r, word) == expected(word)
            assert mappings(r, word is not None) == mapping
            if word is not None:read_word(r, word)
            detail['reset_preserved_configuration'] = True
            if name != 'explicit-off':
                key = 'off' if word is None else 'a' if word == A else 'b'
                path = streams / (key + '.mig')
                saved[key] = (path, save_stream(r, path, word), word)
            report['checks'].append(detail)
        finally:r.close()
    r = Rig(machine_args(args, True, POLICY), out / 'reference-policy')
    try:
        pure = pure_twice(r, POLICY, 'initial-pure')
        mapping = mappings(r, True)
        status = r.monitor('query-status');assert status['running'] is False
        assert r.count['memory'] == r.count['cont'] == 0
        first_read = read_word(r, POLICY)
        r.monitor('system_reset');assert snapshot(r, POLICY) == expected(POLICY)
        reset_read = read_word(r, POLICY)
        assert mappings(r, True) == mapping
        report['checks'].append({'name': 'reference-policy', 'passed': True, 'pure': pure,
                                 'mapping': mapping, 'virtual_policy_fixture_only': True,
                                 'first_read': first_read, 'reset_read': reset_read,
                                 'reset_preserved_configuration': True})
    finally:r.close()
    for name, enabled, value in ADMISSION_CASES:
        r = Rig(machine_args(args, enabled, value), out / name, expect_realize_failure=True)
        try:
            message = (TYPE + ': enabled profile requires explicit fuse-word=0xhhhhhhhh '
                       '(eight lowercase hex digits); no word is bound by default') if enabled else (
                       TYPE + ': fuse-word requires enabled=on')
            text = r.stderr();assert text.count(message) == 1 and r.process.returncode == 1, text
            row = {'name': name, 'passed': True, 'enabled': enabled, 'word': value,
                   'exit_code': 1, 'specific_diagnostic': message, 'specific_diagnostic_observed': True}
            report['admission_rejections'].append(row);report['checks'].append(row)
        finally:r.close()
    for key in ('off', 'a', 'b'):
        path, source, word = saved[key];name = 'migration-' + key
        r = Rig(machine_args(args, word is not None, word), out / name, incoming=True)
        try:
            observation = load_stream(r, path, word)
            row = {'name': name, 'passed': True, 'source': source, 'observation': observation}
            report['migrations'].append(row);report['checks'].append({'name': name, 'passed': True})
        finally:r.close()
    pairs = [('off-to-on', 'off', A), ('on-to-off', 'a', None),
             ('a-to-b', 'a', B), ('b-to-a', 'b', A)]
    for name, key, target in pairs:
        path, source, word = saved[key]
        profile_change = (target is None) != (word is None)
        actual = (0x53444600 | (target is not None)) if profile_change else target
        incoming = (0x53444600 | (word is not None)) if profile_change else word
        r = Rig(machine_args(args, target is not None, target), out / name, incoming=True)
        try:
            row = {'name': name, 'passed': True, 'source_file': path.name,
                   **reject_stream(r, path, target, f'{actual:x} != {incoming:x}')}
            report['rejected_migrations'].append(row);report['checks'].append({'name': name, 'passed': True})
        finally:r.close()
    path, source, word = saved['a'];original = path.read_bytes()
    for field, delta, old in (('profile', 0, 0x53444601), ('word', 4, A)):
        name = 'corrupt-' + field;target = streams / (name + '.mig')
        changed = bytearray(original);offset = source['section']['payload_offset'] + delta
        changed[offset:offset + 4] = struct.pack('>I', old ^ 1)
        differing = [i for i,(a,b) in enumerate(zip(original, changed)) if a != b]
        assert len(differing) == 1 and (original[differing[0]] ^ changed[differing[0]]).bit_count() == 1
        target.write_bytes(changed);assert path.read_bytes() == original
        r = Rig(machine_args(args, True, A), out / name, incoming=True)
        try:
            row = {'name': name, 'passed': True, 'field': field, 'field_offset': offset,
                   'changed_bits': 1, 'source_unchanged': True,
                   'source_sha256': sha(path), 'mutated_sha256': sha(target),
                   **reject_stream(r, target, A, f'{old:x} != {old ^ 1:x}')}
            report['corrupted_stream_rejections'].append(row);report['checks'].append({'name': name, 'passed': True})
        finally:r.close()
    report['actual_stream_files'] = [{'file': str(p.relative_to(out)), 'bytes': p.stat().st_size,
                                     'sha256': sha(p)} for p in sorted(streams.glob('*.mig'))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--machine', default=DEFAULT_MACHINE)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    if not __debug__:parser.error('Assertions are required; -O is not supported')
    if not args.qemu.is_file():parser.error('actual compiled QEMU binary is required')
    out = args.out.resolve();out.mkdir(parents=True, exist_ok=False)
    before = sha(args.qemu)
    report = {'schema': 'tb371fc-spmi-debug-fuse-v1', 'passed': False, 'expected_checks': 22,
              'scope': 'immutable-availability-word-only', 'synthetic_words': [f'{A:#010x}', f'{B:#010x}'],
              'virtual_policy_fixture_word': f'{POLICY:#010x}', 'vmstate_version': 1,
              'vmstate_payload_bytes': 8, 'mutable_state_bytes': 0, 'decode_guard_bytes': 16,
              'readable_bytes': 4, 'word_address': BASE + WORD_OFFSET,
              'guest_cpu_executed': False, 'native_kernel_executed': False,
              'physical_fuse_measured': False, 'debug_controller_modeled': False,
              'normal_spmi_abi_changed': False, 'automatic_test_retries': 0,
              'qtest_ok_is_memtx_result': False, 'test_oracle_reads_device_implementation': False,
              'checks': [], 'admission_rejections': [], 'access_rejections': [],
              'migrations': [], 'rejected_migrations': [], 'corrupted_stream_rejections': [],
              'actual_stream_files': [], 'process_cleanup': [],
              'process_cleanup_verified': False, 'binary_unchanged': False,
              'qemu_sha256_before': before}
    try:
        run_cases(args, out, report)
        report['process_cleanup'] = verify_cleanup(out)
        assert len(report['process_cleanup']) == 22
        assert len(report['checks']) == 22 and all(row['passed'] is True for row in report['checks'])
        assert len(report['actual_stream_files']) == 5
        report['process_cleanup_verified'] = True
        report['qemu_sha256_after'] = sha(args.qemu)
        assert report['qemu_sha256_after'] == before
        report['binary_unchanged'] = True
        report['passed'] = True
    except BaseException:
        report['failure'] = traceback.format_exc()
        try:report['process_cleanup'] = verify_cleanup(out)
        except BaseException:report['cleanup_failure'] = traceback.format_exc()
        report['qemu_sha256_after'] = sha(args.qemu)
    put_json(out / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'result': str(out / 'result.json')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':raise SystemExit(main())
