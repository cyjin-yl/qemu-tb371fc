#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real compiled QMP/QTest checks for explicit NPU unknown-feedback pending.

All CPUs remain paused. GPIO inputs are synthetic fixtures, never a board power
connection. A bounded host-driven polling sequence is not guest driver execution.
Every owned child retains complete protocols, stdio, quit/drain and actual wait.
"""
import argparse
import hashlib
import itertools
import json
from pathlib import Path
import re
import time
import traceback

from qce_bam_test_transport import file_channels, put_json, sha
from npu_gdsc_pending_test_contract import (
    TYPE, PATH, ADDRESS, REGION, POWER, RAM, WITNESS, GETTERS, ORDERED,
    ORDINARY, SOURCES, NEGATIVE, expected, payload, locate, mappings, core_diagnostics,
)
from npu_gdsc_pending_test_transport import NpuPendingRig, collect_cleanup

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use Python -O.')

DEFAULT_MACHINE = ('tb371fc,gcc-boot-configured=off,ufs-enabled=off,'
                   'rpmh-profile=off,rpmh-boot-configured=off')


def snapshot(r, want):
    actual = r.snapshot()
    assert actual.keys() == want.keys()
    assert all(type(actual[key]) is type(want[key]) for key in want)
    assert actual == want, (actual, want)
    return actual


def pure_twice(r, want, label, *, first=False):
    before = dict(r.count)
    one, two = snapshot(r, want), snapshot(r, want)
    after = dict(r.count)
    assert after['qom_get'] - before['qom_get'] == 48
    assert all(before[key] == after[key] for key in before if key != 'qom_get')
    if first:
        assert before['memory'] == before['cont'] == before['qtest'] == 0
    assert after['cont'] == 0
    row = {'first': one, 'repeated': two, 'counters_before': before,
           'counters_after': after, 'getters_each': 24, 'first_fixture_observation': first,
           'no_mmio_or_cont_during_observation': True}
    put_json(r.directory / (label + '.json'), row)
    return row


def diag(reason, *, write=False, value=0):
    return (f'{TYPE}: rejected {"write" if write else "read"} domain=0 '
            f'offset=0x0 size=4 value=0x{value:x} reason={reason}')


def reject(r, want, reason, *, write=False, value=0):
    before = snapshot(r, want)
    start = len(r.stderr())
    returned = None
    if write:
        r.write(value)
    else:
        returned = r.read()
        assert returned == 0
    delta = r.stderr()[start:]
    lines = [line for line in delta.splitlines() if line.startswith(TYPE + ': rejected ')]
    assert lines == [diag(reason, write=write, value=value)], delta
    assert core_diagnostics(delta) == []
    after = snapshot(r, want)
    assert before == after
    return {'operation': 'write' if write else 'read', 'value': value,
            'reason': reason, 'diagnostics': lines, 'returned': returned,
            'before': before, 'after': after, 'state_atomic': True}


def accepted_read(r, want):
    before = snapshot(r, want)
    start = len(r.stderr())
    word = r.read()
    assert word == want['domain0.control'] | (POWER if want['domain0.powered'] else 0)
    assert TYPE + ': rejected ' not in r.stderr()[start:]
    assert core_diagnostics(r.stderr()[start:]) == []
    assert snapshot(r, want) == before
    return word


def case_ordinary(name, r):
    cold = expected(enabled=r.enabled, pending=r.pending)
    if name in ('default-strict', 'explicit-strict'):
        r.intercept()
        assert accepted_read(r, cold) == 1
        r.parent(False, True)
        r.write(0x5800)
        want = expected(pending=False, control=0x5800, parent_on=True)
        rejected = reject(r, want, 'power-feedback-unknown')
        return {'profile': 1, 'unknown_read_rejection': rejected, 'line': r.line(False)}
    if name in ('default-disabled', 'disabled-pending'):
        r.intercept()
        rejections = [reject(r, cold, 'reference-disabled'),
                      reject(r, cold, 'reference-disabled', write=True, value=0)]
        r.parent(True, True)
        r.hardware(True, True)
        supplied = expected(enabled=False, pending=r.pending, parent_known=True,
                            parent_on=True, hw_known=True, hw_request=True)
        assert snapshot(r, supplied)['enabled-profile'] == 0
        return {'profile': 0, 'access_rejections': rejections, 'after_inputs': supplied,
                'line': r.line(False)}
    if name == 'paused-tcg-pending':
        before = r.monitor('query-status')
        assert before['running'] is False
        again = pure_twice(r, cold, 'tcg-repeated')
        after = r.monitor('query-status')
        assert after['running'] is False
        assert r.count['qtest'] == r.count['memory'] == r.count['cont'] == 0
        return {'status_before': before, 'status_after': after, 'pure': again,
                'qtest_channel_opened': False, 'guest_cpu_executed': False}
    r.intercept('state-known' if name == 'pending-known-output' else 'power')
    if name == 'pending-identity':
        text = r.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
        (r.directory / 'active-memory-map.txt').write_bytes(text.encode())
        mapping = mappings(text)
        readonly = []
        for key in GETTERS:
            response = r.raw_monitor('qom-set', {'path': PATH, 'property': key, 'value': cold[key]})
            assert 'error' in response and response['error']['class'] == 'GenericError'
            readonly.append({'property': key, 'response': response})
        assert snapshot(r, cold) == cold
        assert accepted_read(r, cold) == 1
        return {'mapping': mapping, 'readonly': readonly, 'line': r.line(False)}
    if name == 'pending-poll-rmw':
        r.parent(False, True)
        r.hardware(True, True)
        r.write(0x5801)
        collapsed = expected(control=0x5801, parent_on=True, hw_known=True, hw_request=True)
        initial = accepted_read(r, collapsed)
        r.write(initial & ~1)
        want = expected(control=0x5800, parent_on=True, hw_known=True, hw_request=True)
        start = len(r.stderr())
        rows = []
        previous_clock = None
        for index in range(100):
            word = r.read()
            assert word == 0x5800 and not word & POWER
            state = snapshot(r, want)
            assert state['domain0.state-known'] is False and state['domain0.powered'] is False
            clock = int(r.qt('clock_step 1000')[0], 0)
            if previous_clock is not None:
                assert clock - previous_clock == 1000
            previous_clock = clock
            rows.append({'index': index, 'word': word, 'virtual_ns': clock,
                         'state': state, 'line': r.line(False)})
        r.write(POWER | 0x7800)
        final = expected(control=0x7800, parent_on=True, hw_known=True, hw_request=True)
        assert accepted_read(r, final) == 0x7800
        final_line = r.line(False)
        # A successful zero read must be distinguished from QTest swallowing a
        # MEMTX_ERROR that also returns zero. Retain the exact empty diagnostics.
        r.write(0)
        zero = expected(control=0, parent_on=True, hw_known=True, hw_request=True)
        zero_read = accepted_read(r, zero)
        assert zero_read == 0
        zero_line = r.line(False)
        assert TYPE + ': rejected ' not in r.stderr()[start:]
        assert core_diagnostics(r.stderr()[start:]) == []
        return {'polls': rows, 'rmw_before': initial, 'rmw_after': 0x5800,
                'status_carry_write': POWER | 0x7800, 'final': final,
                'final_line': final_line, 'zero_state': zero,
                'zero_read': zero_read, 'zero_line': zero_line, 'poll_limit': 100,
                'step_ns': 1000, 'driver_executed': False, 'guest_timeout_observed': False}
    if name == 'pending-power-truth':
        rows = []
        for known, on, collapse, carry in itertools.product((False, True), repeat=4):
            r.parent(known, on)
            r.hardware(not known, on)
            control = 0x6800 | int(collapse)
            r.write(control | (POWER if carry else 0))
            want = expected(control=control, parent_known=known, parent_on=on,
                            hw_known=not known, hw_request=on)
            rows.append({'inputs': [known, on, collapse, carry], 'state': want,
                         'read': accepted_read(r, want), 'line': r.line(want['domain0.powered'])})
        return {'truth_rows': rows}
    if name == 'pending-known-output':
        rows = []
        for known, on, collapse in ((False, True, False), (True, True, False),
                                    (True, False, False), (False, False, False),
                                    (False, True, True)):
            r.parent(known, on)
            r.write(0x4800 | int(collapse))
            want = expected(control=0x4800 | int(collapse), parent_known=known, parent_on=on)
            rows.append({'inputs': [known, on, collapse], 'state': want,
                         'read': accepted_read(r, want),
                         'line': r.line(want['domain0.state-known'])})
        return {'known_output_rows': rows}
    if name == 'pending-reset':
        r.parent(True, True)
        r.hardware(True, True)
        r.write(0x5800)
        before = expected(control=0x5800, parent_known=True, parent_on=True,
                          hw_known=True, hw_request=True)
        assert accepted_read(r, before) == POWER | 0x5800
        r.line(True)
        r.write_ram(RAM, WITNESS)
        r.monitor('system_reset')
        end = time.monotonic() + 10
        while r.qom('domain0.control') != 1:
            assert time.monotonic() < end, 'system_reset did not complete'
            time.sleep(0.01)
        after = expected(parent_known=True, parent_on=True, hw_known=True, hw_request=True)
        assert accepted_read(r, after) == 1
        assert any(event.get('event') == 'RESET' for event in r.events)
        assert r.read_ram(RAM, len(WITNESS)) == WITNESS
        return {'before': before, 'after': after, 'line': r.line(False),
                'reset_event_seen': True, 'ram_address': RAM, 'ram_hex': WITNESS.hex(),
                'provider_inputs_preserved': True}
    assert name == 'pending-strict-access'
    r.parent(False, True)
    r.write(0x5800)
    want = expected(control=0x5800, parent_on=True)
    writes = [reject(r, want, 'unsupported-bits', write=True, value=0x5800 | (1 << bit))
              for bit in (2, 3, 10, 16, 30)]
    writes.append(reject(r, want, 'hw-mode-not-in-reference', write=True, value=0x5802))
    core = []
    invalid = 'invalid size (min:4 max:4)'
    requests = []
    for write in (False, True):
        for width, size in (('b', 1), ('w', 2)):
            requests.append((write, width, 0, [(0, size)]))
        requests += [(write, 'l', 1, [(1, 1), (2, 2), (4, 1)]),
                     (write, 'l', 2, [(2, 2), (4, 2)]),
                     (write, 'l', 4, [(4, 4)]), (write, 'l', 8, [(8, 4)])]
    for write, width, offset, pieces in requests:
        before = snapshot(r, want)
        start = len(r.stderr())
        if write:
            r.write(0, offset, width)
        else:
            r.read(offset, width)
        delta = r.stderr()[start:]
        op = 'write' if write else 'read'
        diagnostics = [(op, off if off < 4 else ADDRESS + off, size,
                        REGION if off < 4 else '(null)', invalid if off < 4 else 'rejected')
                       for off, size in pieces]
        assert core_diagnostics(delta) == diagnostics, (delta, diagnostics)
        assert TYPE + ': rejected ' not in delta
        assert snapshot(r, want) == before
        core.append({'write': write, 'width': width, 'offset': offset,
                     'diagnostics': [list(x) for x in diagnostics], 'state_atomic': True})
    # QEMU decomposes this 8-byte scalar into two 4-byte transactions. Preserve
    # the documented successful low prefix; never claim the whole write atomic.
    start = len(r.stderr())
    value = (0x12345678 << 32) | 0x2001
    r.write(value, width='q')
    wide_diags = [('write', ADDRESS + 4, 4, '(null)', 'rejected')]
    assert core_diagnostics(r.stderr()[start:]) == wide_diags
    final = expected(control=0x2001, parent_on=True)
    assert accepted_read(r, final) == 0x2001
    return {'model_write_rejections': writes, 'core_access_rejections': core,
            'wide_write': {'value': value, 'diagnostics': [list(x) for x in wide_diags],
                           'low_word_committed': True, 'whole_request_atomic': False,
                           'after': final}, 'line': r.line(False)}


def inspect_stream(path, state):
    raw = path.read_bytes()
    section = locate(raw)
    wire = raw[section['payload_offset']:section['payload_offset'] + 64]
    assert wire == payload(state), (wire.hex(), payload(state).hex())
    return {'file': path.name, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
            'section': section, 'payload_hex': wire.hex(),
            'payload_sha256': hashlib.sha256(wire).hexdigest(),
            'snapshot': state, 'stream_modified': False}


def save(r, path, want):
    r.write_ram(RAM, WITNESS)
    r.monitor('stop')
    assert r.monitor('query-status')['running'] is False
    before = pure_twice(r, want, 'before-save')
    r.monitor('migrate', file_channels(path))
    sent = r.migrate_wait()
    assert r.monitor('query-status')['status'] == 'postmigrate'
    after = pure_twice(r, want, 'after-save')
    row = inspect_stream(path, want)
    row.update(source_fixture=r.directory.name, query_migrate=sent,
               source_before=before, source_after=after)
    put_json(r.directory / 'saved-stream.json', row)
    return row


def receive(r, path, saved):
    # Deliberately contradict the incoming mutable state before load.
    source = saved['snapshot']
    warm_known = not source['parent-known']
    r.parent(warm_known, True)
    r.hardware(not source['domain0.hw-request-known'], not source['domain0.hw-request'])
    r.write(0x2800)
    warm = expected(control=0x2800, parent_known=warm_known, parent_on=True,
                    hw_known=not source['domain0.hw-request-known'],
                    hw_request=not source['domain0.hw-request'])
    preload = pure_twice(r, warm, 'before-incoming')
    r.line(warm['domain0.powered'])
    counters = dict(r.count)
    r.monitor('migrate-incoming', file_channels(path))
    received = r.migrate_wait()
    assert r.monitor('query-status')['running'] is False
    first = pure_twice(r, source, 'incoming-before-mmio')
    assert r.count['memory'] == counters['memory'] and r.count['cont'] == counters['cont'] == 0
    line = r.line(source['domain0.powered'])
    assert r.read_ram(RAM, len(WITNESS)) == WITNESS
    word = accepted_read(r, source)
    assert sha(path) == saved['sha256']
    return {'source_file': path.name, 'target_fixture': r.directory.name,
            'source_sha256': saved['sha256'], 'preload': preload,
            'first_after_incoming': first, 'counters_at_incoming': counters,
            'query_migrate': received, 'line': line, 'word': word,
            'ram_address': RAM, 'ram_hex': WITNESS.hex(), 'stream_modified': False,
            'device_mmio_before_first_observation': False, 'cont_before_first_observation': False}


def load_failure(r, path, diagnostic):
    if r.enabled:
        r.parent(True, True)
        r.write(0)
        warm = expected(pending=r.pending, control=0, parent_known=True, parent_on=True)
        r.line(True)
    else:
        warm = expected(enabled=False, pending=r.pending)
        r.line(False)
    preload = pure_twice(r, warm, 'rejected-incoming-preload')
    mark = len(r.irq_events)
    counters = dict(r.count)
    r.expected_exit = 1
    command_error = None
    try:
        r.monitor('migrate-incoming', file_channels(path))
    except (AssertionError, OSError, EOFError, RuntimeError) as exc:
        command_error = f'{type(exc).__name__}: {exc}'
    assert r.wait_exit(20) == 1
    text = r.stderr()
    assert TYPE in text and diagnostic in text
    assert 'load of migration failed' in text and 'error while loading state' in text
    assert all(r.count[key] == counters[key] for key in counters)
    r.close()  # Include late IRQ events from the complete socket drain.
    cleanup = json.loads((r.directory / 'cleanup.json').read_bytes())
    tail = cleanup['irq_events'][mark:]
    expected_tail = [{'action': 'lower', 'number': 0}] if r.enabled else []
    assert tail == expected_tail, tail
    return {'file': path.name, 'sha256': sha(path), 'target_fixture': r.directory.name,
            'expected_diagnostic': diagnostic, 'actual_exit': 1, 'preload': preload,
            'counters_at_incoming': counters, 'counters_after_exit': dict(r.count),
            'incoming_irq_event_index': mark, 'complete_drained_irq_tail': tail,
            'command_error': command_error, 'post_exit_qom_available': False,
            'guard_scope': 'this-device-before-successful-feedback-publication'}


def run(args, out, report):
    def fixture(name, action, **kwargs):
        assert name == ORDERED[len(report['checks'])]
        r = NpuPendingRig(args, out / name, **kwargs)
        try:
            initial = expected(enabled=r.enabled, pending=r.pending)
            first = pure_twice(r, initial, 'first-pure', first=True)
            details = action(r)
        finally:
            r.close()
        report['checks'].append({'name': name, 'passed': True, 'first_pure': first, 'details': details})
        return details

    for name in ORDINARY:
        options = {}
        if name == 'default-strict':
            options = {'pending': None}
        elif name == 'explicit-strict':
            options = {'pending': False}
        elif name == 'default-disabled':
            options = {'reference': None, 'pending': None}
        elif name == 'disabled-pending':
            options = {'reference': False}
        elif name == 'paused-tcg-pending':
            options = {'accel': 'tcg'}
        fixture(name, lambda r, name=name: case_ordinary(name, r), **options)

    originals = {}
    for kind in ('unknown', 'known'):
        state = expected(control=0x5800 if kind == 'unknown' else 0x4800,
                         parent_known=kind == 'known', parent_on=True,
                         hw_known=kind == 'known', hw_request=kind == 'unknown')
        path = out / f'pending-{kind}.bin'
        def source(r, state=state, path=path):
            r.intercept()
            r.parent(state['parent-known'], state['parent-on'])
            r.hardware(state['domain0.hw-request-known'], state['domain0.hw-request'])
            r.write(state['domain0.control'])
            assert accepted_read(r, state) == state['domain0.control'] | (POWER if state['domain0.powered'] else 0)
            r.line(state['domain0.powered'])
            saved = save(r, path, state)
            report['migration_sources'].append(saved)
            return saved
        saved = fixture(f'pending-{kind}-source', source)
        originals[kind] = saved
        returned = out / f'pending-{kind}-return.bin'
        def target(r, path=path, saved=saved, returned=returned):
            r.intercept()
            received = receive(r, path, saved)
            report['migrations'].append(received)
            back = save(r, returned, saved['snapshot'])
            report['migration_sources'].append(back)
            return {'incoming': received, 'saved_return': back}
        loaded = fixture(f'pending-{kind}-incoming', target, incoming=True)
        def roundtrip(r, returned=returned, back=loaded['saved_return'], saved=saved):
            r.intercept()
            received = receive(r, returned, back)
            report['migrations'].append(received)
            assert payload(back['snapshot']) == payload(saved['snapshot'])
            return {'incoming': received, 'original_file': saved['file'],
                    'returned_file': back['file'], 'complete_64_bytes_equal': True}
        fixture(f'pending-{kind}-roundtrip', roundtrip, incoming=True)

    for name, enabled in (('strict-source', True), ('disabled-source', False)):
        path = out / (name + '.bin')
        def other_source(r, path=path):
            r.intercept()
            state = expected(enabled=r.enabled, pending=False)
            r.line(False)
            saved = save(r, path, state)
            report['migration_sources'].append(saved)
            return saved
        originals[name] = fixture(name, other_source, reference=enabled, pending=False)

    cross = (
        ('strict-to-pending', originals['strict-source'], True, True, '2 != 1'),
        ('pending-to-strict', originals['known'], True, False, '1 != 2'),
        ('disabled-to-pending', originals['disabled-source'], True, True, '2 != 0'),
        ('pending-to-disabled', originals['known'], False, True, '0 != 2'))
    for name, saved, enabled, pending, diagnostic in cross:
        def rejection(r, saved=saved, diagnostic=diagnostic):
            r.intercept()
            row = load_failure(r, out / saved['file'], diagnostic)
            row['stream_modified'] = False
            report['rejected_migrations'].append(row)
            return row
        fixture(name, rejection, reference=enabled, pending=pending, incoming=True)

    known = originals['known']
    original = (out / known['file']).read_bytes()
    for name, offset, width, old, mask, diagnostic in (
        ('corrupt-profile', 0, 4, 2, 1, '2 != 3'),
        ('corrupt-domain-base', 42, 8, ADDRESS, 4, f'{ADDRESS:x} != {ADDRESS ^ 4:x}'),
        ('corrupt-hw-mode', 58, 4, 0x4800, 2, TYPE + ': invalid migrated npu domain 0')):
        absolute = known['section']['payload_offset'] + offset
        assert int.from_bytes(original[absolute:absolute + width], 'big') == old
        changed = bytearray(original)
        changed[absolute:absolute + width] = (old ^ mask).to_bytes(width, 'big')
        positions = [i for i, pair in enumerate(zip(original, changed)) if pair[0] != pair[1]]
        assert positions == [absolute + width - 1]
        assert (original[positions[0]] ^ changed[positions[0]]).bit_count() == 1
        path = out / (name + '.bin')
        path.write_bytes(changed)
        section = locate(changed)
        stream = {'file': path.name, 'bytes': len(changed), 'sha256': sha(path),
                  'section': section, 'payload_hex': bytes(changed[section['payload_offset']:section['payload_offset'] + 64]).hex(),
                  'source_file': known['file'], 'source_sha256': known['sha256'],
                  'stream_modified': True, 'field_offset': offset,
                  'changed_byte_offsets': positions, 'changed_bits': 1}
        report['corrupted_streams'].append(stream)
        def damaged(r, path=path, diagnostic=diagnostic, stream=stream):
            r.intercept()
            row = load_failure(r, path, diagnostic)
            row.update(stream_modified=True, mutation=stream)
            report['corrupted_stream_rejections'].append(row)
            return row
        fixture(name, damaged, incoming=True)
    assert all(sha(out / row['file']) == row['sha256'] for row in report['migration_sources'])
    streams = report['migration_sources'] + report['corrupted_streams']
    assert {p.name for p in out.glob('*.bin')} == {row['file'] for row in streams}
    report['actual_stream_files'] = [{'file': row['file'], 'bytes': (out / row['file']).stat().st_size,
                                      'sha256': sha(out / row['file'])} for row in streams]
    report['process_cleanup'] = collect_cleanup(out, ORDERED)
    report['all_actual_wait_calls'] = sum(len(row['wait_calls']) for row in report['process_cleanup'])
    assert len(report['checks']) == 26 and report['all_actual_wait_calls'] == 33


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--machine', default=DEFAULT_MACHINE)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    assert args.machine == DEFAULT_MACHINE, 'fixed bounded machine contract'
    args.out.mkdir(parents=True, exist_ok=False)
    binary = Path(args.qemu)
    before = sha(binary)
    report = {'schema': 'tb371fc-npu-gdsc-pending-v1', 'passed': False,
              'expected_checks': 26, 'expected_processes': 26, 'normal_exit0': 19,
              'expected_exit1': 7, 'all_actual_wait_calls': 0,
              'vmstate_version': 1, 'vmstate_payload_bytes': 64,
              'same_profile_roundtrips': 2, 'successful_incoming': 4,
              'reference_identity': 0x4e504731, 'pending_profile': 2,
              'strict_profile': 1, 'disabled_profile': 0,
              'domain_count': 1, 'mapped_bytes': 4, 'first_pure_getters': 48,
              'parent_power_connected': False, 'guest_cpu_executed': False,
              'native_kernel_probe_observed': False, 'guest_driver_timeout_observed': False,
              'analog_power_modeled': False, 'retention_effect_modeled': False,
              'npu_execution_observed': False, 'android_boot_passed': False,
              'automatic_test_retries': 0, 'qtest_ok_is_memtx_result': False,
              'test_oracle_reads_device_implementation': False,
              'binary_before_sha256': before, 'binary_unchanged': False,
              'process_cleanup_verified': False, 'checks': [], 'migration_sources': [],
              'migrations': [], 'rejected_migrations': [], 'corrupted_streams': [],
              'corrupted_stream_rejections': [], 'actual_stream_files': [], 'process_cleanup': []}
    try:
        run(args, args.out, report)
        report['binary_after_sha256'] = sha(binary)
        report['binary_unchanged'] = report['binary_after_sha256'] == before
        assert report['binary_unchanged']
        report['process_cleanup_verified'] = True
        report['passed'] = True
    except BaseException:
        report['error'] = traceback.format_exc()
    put_json(args.out / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'result': str(args.out / 'result.json')}))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
