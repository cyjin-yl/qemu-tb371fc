#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test the explicit legacy STM DEVARCH virtual compatibility choice.

Real QMP/QTest accesses and unmodified process migration streams. No guest CPU,
new trace capability, QOM state injection or physical identity claim. The old
STM gate is independent and unchanged.
"""
import argparse
import json
from pathlib import Path
import traceback
from types import SimpleNamespace

from qce_bam_test_transport import file_channels, put_json, sha, verify_cleanup
from test_coresight_stm import (STMRig, BASE, AUTH, CLAIMSET, CLAIMCLR,
                               DEVID, DEVTYPE, LSR, SPER)

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')

DEVARCH = 0xfbc
MACHINE_PROPERTY = 'stm-legacy-devarch-reference'
DEVICE_PROPERTY = 'legacy-devarch-reference'
PROFILE_OFF, PROFILE_ON = 0x53544d01, 0x53544d02
REQUIRED = [
    'default-off-retains-legacy-identity-and-devarch-rejection',
    'explicit-selection-is-typed-and-immutable-on-machine-and-device',
    'linux-amba-order-reads-explicit-virtual-identity-in-both-lock-states',
    'all-devarch-writes-are-rejected-before-locked-write-suppression',
    'wrong-width-unaligned-and-neighbor-accesses-remain-atomic-rejections',
    'reset-preserves-the-immutable-selection-and-resets-existing-controls',
    'ordinary-same-profile-migration-preserves-selection-and-controls',
    'opt-in-to-default-profile-migration-is-rejected',
    'default-to-opt-in-profile-migration-is-rejected',
    'every-created-child-is-actually-waited-and-reaped',
]


def selected(rig, expected):
    machine = rig.monitor('qom-get', {'path': '/machine',
                                     'property': MACHINE_PROPERTY})
    device = rig.monitor('qom-get', {'path': '/machine/stm',
                                    'property': DEVICE_PROPERTY})
    assert type(machine) is bool and type(device) is bool
    assert machine is expected and device is expected, (machine, device, expected)
    return dict(machine=machine, device=device)


def immutable(rig, expected):
    result = []
    for path, prop in (('/machine', MACHINE_PROPERTY),
                       ('/machine/stm', DEVICE_PROPERTY)):
        response = rig.raw_monitor('qom-set', {'path': path, 'property': prop,
                                              'value': not expected})
        assert 'error' in response and 'return' not in response, response
        description = response['error'].get('desc', '')
        assert prop in description, response
        if path == '/machine':
            assert 'fixed after machine creation' in description, response
        else:
            assert 'after it was realized' in description, response
        result.append(dict(path=path, property=prop, attempted_value=not expected,
                           error=response['error'], selection_after=selected(rig, expected)))
    return result


def reject(rig, offset, *, write=False, width='l', value=0):
    before = rig.state()
    stderr_before = rig.stderr()
    size = {'b': 1, 'w': 2, 'l': 4, 'q': 8}[width]
    command = f"{'write' if write else 'read'}{width} {BASE + offset:#x}"
    if write:
        command += f' {value:#x}'
    rig.count['device_mmio'] += 1
    response = rig.qt(command)
    added = rig.stderr()[len(stderr_before):]
    expected = (f"arm-coresight-stm: rejected {'write' if write else 'read'} "
                f'offset={offset:#x} size={size}')
    lines = [line for line in added.splitlines() if 'arm-coresight-stm: rejected' in line]
    assert lines == [expected], (command, added)
    assert rig.state() == before, (command, before, rig.state())
    return dict(command=command, response=response, offset=hex(offset), size=size,
                write=write, value=value if write else None, diagnostic=expected,
                state_before=before, state_after=rig.state())


def linux_identity(rig):
    before, stderr_before = rig.state(), rig.stderr()
    offsets = [0xfe0 + 4 * i for i in range(4)]
    offsets += [0xff0 + 4 * i for i in range(4)]
    offsets += [DEVARCH, DEVTYPE]
    values = [rig.read_stm(offset) for offset in offsets]
    assert values == [0x62, 0xb9, 0x0b, 0, 0x0d, 0x90, 0x05, 0xb1, 0, 0x63], values
    assert rig.stderr() == stderr_before
    assert rig.state() == before
    assert rig.read_stm(AUTH) == 0xaa and rig.read_stm(DEVID) == 0x10000
    return dict(offsets=[hex(x) for x in offsets], values=values,
                no_rejection_diagnostic=True, state_before=before,
                state_after=rig.state(), trace_capability_added=False)


def save_stream(rig, stream, profile):
    rig.monitor('stop')
    assert rig.monitor('query-status')['running'] is False
    rig.monitor('migrate', file_channels(stream))
    state = rig.migrate_wait()
    assert state['status'] == 'completed'
    return dict(path=str(stream), bytes=stream.stat().st_size, sha256=sha(stream),
                source_profile=hex(profile), source_status=state,
                payload_modified=False, source_fixture=rig.directory.name)


def reject_migration(rig, stream, target_profile, source_profile):
    # Only the expected process created by this fixture can exit here.
    # An arbitrary exception is not a pass: actual wait(1), exact profile
    # equality error and the device load failure must all be observed.
    rig.expected_exit = 1
    response = None
    command_error = None
    try:
        response = rig.raw_monitor('migrate-incoming', file_channels(stream))
    except (OSError, EOFError, ValueError, AssertionError) as error:
        command_error = f'{type(error).__name__}: {error}'
    code = rig.wait_exit(20)
    stderr = rig.stderr()
    expected = f'{target_profile:x} != {source_profile:x}'
    assert code == 1, (code, stderr)
    assert expected in stderr and 'arm-coresight-stm' in stderr, stderr
    assert 'load of migration failed' in stderr and 'error while loading state' in stderr, stderr
    return dict(target_profile=hex(target_profile), source_profile=hex(source_profile),
                target_fixture=rig.directory.name, expected_diagnostic=expected,
                actual_exit_code=code, response=response, command_error=command_error,
                actual_wait=True, stream_sha256=sha(stream), stream_modified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'streams').mkdir()
    here = Path(__file__).resolve().parent
    report = dict(schema='tb371fc-stm-legacy-devarch-v1', passed=False,
                  qemu_sha256=sha(args.qemu), test_sha256=sha(__file__),
                  dependency_sha256={name: sha(here / name) for name in
                                     ('test_coresight_stm.py', 'qce_bam_test_transport.py')},
                  checks=[], processes=[], access_rejections=[], qom_rejections=[],
                  migrations=[], rejected_migrations=[], streams=[],
                  expected_checks=10, expected_access_rejections=26,
                  expected_qom_rejections=4, expected_processes=5,
                  guest_cpu_executed=False, original_android_executed=False,
                  physical_devarch_known=False, reference_value=0,
                  trace_capability_added=False, migration_payloads_modified=False)
    rigs = []

    def new(name, enabled, incoming=False):
        machine = 'tb371fc,ufs-enabled=off'
        if enabled:
            machine += ',stm-legacy-devarch-reference=on'
        rig = STMRig(SimpleNamespace(qemu=args.qemu, machine=machine),
                     out / name, incoming=incoming)
        rigs.append(rig)
        return rig

    def check(name, **evidence):
        assert name == REQUIRED[len(report['checks'])], name
        report['checks'].append(dict(name=name, passed=True, **evidence))

    try:
        off = new('default-off', False)
        try:
            off_selection = selected(off, False)
            report['qom_rejections'] += immutable(off, False)
            pid = sum(off.read_stm(0xfe0 + 4 * i) << (8 * i) for i in range(4))
            cid = sum(off.read_stm(0xff0 + 4 * i) << (8 * i) for i in range(4))
            assert pid == 0xbb962 and cid == 0xb105900d
            assert off.read_stm(DEVTYPE) == 0x63
            report['access_rejections'].append(reject(off, DEVARCH))
            report['access_rejections'].append(reject(off, DEVARCH, write=True))
            off.unlock()
            report['access_rejections'].append(reject(off, DEVARCH, write=True))
            off.lock()
            assert off.state() == off.reset_state()
            check(REQUIRED[0], selection=off_selection, pid=hex(pid), cid=hex(cid),
                  devtype=0x63, rejection_count=3)
            off_stream = out / 'streams/default-off.mig'
            report['streams'].append(save_stream(off, off_stream, PROFILE_OFF))
        finally:
            off.close()

        source = new('source', True)
        try:
            on_selection = selected(source, True)
            report['qom_rejections'] += immutable(source, True)
            check(REQUIRED[1], selection=on_selection,
                  qom_rejections=list(report['qom_rejections']))
            locked_identity = linux_identity(source)
            source.unlock()
            unlocked_identity = linux_identity(source)
            check(REQUIRED[2], locked=locked_identity, unlocked=unlocked_identity)

            writes = ((DEVARCH, 'b', 0), (DEVARCH, 'w', 0),
                      (DEVARCH, 'l', 0), (DEVARCH, 'l', 0xffffffff),
                      (DEVARCH, 'q', 0), (DEVARCH + 1, 'l', 0),
                      (0xfb8, 'q', 0))
            for locked in (True, False):
                source.lock() if locked else source.unlock()
                for offset, width, value in writes:
                    row = reject(source, offset, write=True, width=width, value=value)
                    report['access_rejections'].append(dict(lock_state=locked, **row))
            check(REQUIRED[3], rejections=14, both_lock_states=True,
                  zero_write_rejected=True, whole_64bit_access_rejected=True)
            bad_reads = ((DEVARCH, 'b'), (DEVARCH, 'w'), (DEVARCH, 'q'),
                         (DEVARCH + 1, 'l'), (0xfb8, 'q'))
            for offset, width in bad_reads:
                report['access_rejections'].append(reject(source, offset, width=width))
            for offset in (0xfc0, 0xfc4):
                report['access_rejections'].append(reject(source, offset))
                report['access_rejections'].append(reject(source, offset, write=True))
            assert len(report['access_rejections']) == 26
            check(REQUIRED[4], rejected_reads_and_neighbors=9,
                  total_rejections=26, neighboring_reserved_offsets=['0xfc0', '0xfc4'])

            source.write_stm(SPER, 0x12345678)
            source.write_stm(CLAIMSET, 5)
            source.lock()
            assert source.read_stm(CLAIMCLR) == 5
            source.monitor('system_reset')
            assert source.state() == source.reset_state()
            assert selected(source, True) == on_selection
            reset_identity = linux_identity(source)
            check(REQUIRED[5], selection=on_selection, identity=reset_identity)
            source.unlock()
            source.write_stm(SPER, 0xa5a5a5a5)
            source.write_stm(CLAIMSET, 0xa)
            source.lock()
            saved = source.state()
            on_stream = out / 'streams/explicit-reference.mig'
            on_migration = save_stream(source, on_stream, PROFILE_ON)
            report['streams'].append(on_migration)
        finally:
            source.close()

        target = new('target', True, incoming=True)
        try:
            assert selected(target, True) == on_selection
            target.monitor('migrate-incoming', file_channels(on_stream))
            status = target.migrate_wait()
            assert status['status'] == 'completed'
            assert selected(target, True) == on_selection
            assert target.state() == saved
            restored_identity = linux_identity(target)
            target.unlock()
            target.write_stm(CLAIMCLR, 2)
            target.write_stm(CLAIMSET, 1)
            assert target.read_stm(CLAIMCLR) == 9
            target.lock()
            assert target.read_stm(LSR) == 3
            assert sha(on_stream) == on_migration['sha256']
            report['migrations'].append(dict(source_fixture='source', target_fixture='target',
                                              source_status='completed', target_status=status,
                                              stream_sha256=sha(on_stream), stream_modified=False))
            check(REQUIRED[6], saved_state=saved, restored_identity=restored_identity,
                  continued_claim=9, selection=on_selection)
        finally:
            target.close()

        for label, enabled, stream, target_profile, source_profile, check_index in (
                ('reject-on-to-off', False, on_stream, PROFILE_OFF, PROFILE_ON, 7),
                ('reject-off-to-on', True, off_stream, PROFILE_ON, PROFILE_OFF, 8)):
            rejected_target = new(label, enabled, incoming=True)
            try:
                selected(rejected_target, enabled)
                failure = reject_migration(rejected_target, stream, target_profile, source_profile)
                report['rejected_migrations'].append(failure)
                check(REQUIRED[check_index], **failure)
            finally:
                rejected_target.close()

        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 5
        expected_exits = {'default-off': 0, 'source': 0, 'target': 0,
                          'reject-on-to-off': 1, 'reject-off-to-on': 1}
        assert {p['fixture']: p['exit_code'] for p in report['processes']} == expected_exits
        assert {p['fixture']: p['counters']['qom_set'] for p in report['processes']} == {
            'default-off': 2, 'source': 2, 'target': 0,
            'reject-on-to-off': 0, 'reject-off-to-on': 0}
        assert all(p['counters']['cont'] == 0 for p in report['processes'])
        assert all(sha(x['path']) == x['sha256'] for x in report['streams'])
        assert len(report['migrations']) == 1 and len(report['rejected_migrations']) == 2
        assert sha(args.qemu) == report['qemu_sha256'] and sha(__file__) == report['test_sha256']
        assert all(sha(here / name) == value for name, value in report['dependency_sha256'].items())
        check(REQUIRED[9], processes=5, forced_cleanup=False,
              expected_exit_codes=expected_exits)
        assert [x['name'] for x in report['checks']] == REQUIRED
        report['passed'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()
    finally:
        for rig in reversed(rigs):
            rig.close()
        report['retained_cleanup_files'] = [dict(path=str(p), bytes=p.stat().st_size,
                                                sha256=sha(p)) for p in sorted(out.glob('*/cleanup.json'))]
        put_json(out / 'result.json', report)
    if not report['passed']:
        print(report.get('traceback', report.get('error', 'Unknown failure')))
    print(json.dumps(dict(passed=report['passed'], checks=len(report['checks']),
                          error=report.get('error'), report=str(out / 'result.json'))))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
