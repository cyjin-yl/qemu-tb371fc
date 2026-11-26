#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""NPU addressing and construction around unchanged owned-child QCE transport."""
import json
from pathlib import Path
import subprocess
import tempfile

from qce_bam_test_transport import Rig, put_json
from npu_gdsc_pending_test_contract import TYPE, PATH, ADDRESS, GETTERS


class NpuPendingRig(Rig):
    def __init__(self, args, directory, *, reference=True, pending=True,
                 incoming=False, accel='qtest'):
        assert reference in (None, False, True) and pending in (None, False, True)
        assert accel in ('qtest', 'tcg')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sockets = Path(tempfile.mkdtemp(prefix='npp-'))
        self.process = None
        self.peers = {}
        self.closed = False
        self.expected_failure = False
        self.expected_exit = 0
        self.request_id = 0
        self.count = dict(qom_get=0, qom_set=0, qtest=0, memory=0,
                          device_mmio=0, ram=0, cont=0)
        self.events, self.irq_events, self.waits = [], [], []
        self.irq = {0: False}
        self.intercepted = False
        self.group = None
        self.enabled, self.pending = reference is True, pending is True
        self.logs = {name: (self.directory / name).open('wb')
                     for name in ('qemu.stdout', 'qemu.stderr')}
        self.protocol = (self.directory / 'protocol.jsonl').open('w')
        command = [str(Path(args.qemu).resolve()), '-machine', args.machine,
                   '-accel', accel, '-m', '128M', '-smp', '8', '-S',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null',
                   '-serial', 'null', '-d', 'unimp,guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.sockets}/m,server=on,wait=off']
        for key, value in (('enabled-reference', reference),
                           ('unknown-feedback-pending', pending)):
            if value is not None:
                command += ['-global', TYPE + '.' + key + '=' + ('on' if value else 'off')]
        if accel == 'qtest':
            command += ['-qtest', f'unix:{self.sockets}/t,server=on,wait=off',
                        '-qtest-log', str(self.directory / 'qtest-server.log')]
        if incoming:
            command += ['-incoming', 'defer']
        self.command_line = command
        put_json(self.directory / 'command.json', command)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                             stdout=self.logs['qemu.stdout'],
                                             stderr=self.logs['qemu.stderr'])
            put_json(self.directory / 'child.json', {'pid': self.process.pid,
                     'created_by_this_fixture': True, 'signals_to_preexisting_processes': False})
            self.peers['qmp'] = self.connect('m', 'qmp')
            greeting = json.loads(self.peers['qmp'].receive())
            self.record({'qmp_receive': greeting})
            assert 'QMP' in greeting
            self.monitor('qmp_capabilities')
            if accel == 'qtest':
                self.peers['qtest'] = self.connect('t', 'qtest')
        except BaseException:
            self.close()
            raise

    def qom(self, prop):
        return self.monitor('qom-get', {'path': PATH, 'property': prop})

    def snapshot(self):
        return {key: self.qom(key) for key in GETTERS}

    def qt(self, command):
        fields = command.split()
        if fields[0] in ('readb', 'readw', 'readl', 'readq', 'read',
                         'writeb', 'writew', 'writel', 'writeq', 'write'):
            if ADDRESS <= int(fields[1], 0) < ADDRESS + 4:
                self.count['device_mmio'] += 1
        return super().qt(command)

    def read(self, offset=0, width='l'):
        return int(self.qt(f'read{width} {ADDRESS + offset:#x}')[0], 0)

    def write(self, value, offset=0, width='l'):
        self.qt(f'write{width} {ADDRESS + offset:#x} {value:#x}')

    def input(self, name, value):
        assert name in ('parent-known', 'parent-on', 'hw-request-known', 'hw-request')
        assert type(value) is bool
        self.qt(f'set_irq_in {PATH} {name} 0 {int(value)}')

    def parent(self, known, on):
        self.input('parent-on', on)
        self.input('parent-known', known)

    def hardware(self, known, value):
        self.input('hw-request', value)
        self.input('hw-request-known', known)

    def intercept(self, group='power'):
        assert not self.intercepted and group in ('power', 'state-known')
        self.qt('irq_intercept_out ' + PATH + ' ' + group)
        self.intercepted, self.group = True, group

    def line(self, level):
        assert type(level) is bool and self.intercepted
        assert self.qt('endianness') == ['little']
        assert self.irq[0] is level, (self.group, self.irq, level)
        return {'group': self.group, 'actual_level': self.irq[0],
                'event_count': len(self.irq_events)}


def collect_cleanup(out, ordered):
    paths = {p.parent.name: p for p in Path(out).glob('*/cleanup.json')}
    assert set(paths) == set(ordered)
    result = []
    for name in ordered:
        value = json.loads(paths[name].read_bytes())
        assert value['actually_waited_and_reaped'] is True
        assert value['wait_calls'] and value['exit_code'] == value['expected_exit_code']
        assert not value['forced_terminate'] and not value['forced_kill']
        assert value['signals_to_preexisting_processes'] is False
        assert value['unexpected_qtest_drain_lines'] == []
        if value['expected_exit_code'] == 0:
            assert value['quit_sent'] and value['quit_acknowledged']
            assert value['quit_error'] is None and len(value['wait_calls']) == 1
        else:
            assert value['expected_exit_code'] == 1 and len(value['wait_calls']) == 2
            assert not value['quit_sent'] and value['quit_not_applicable'] == 'already-exited'
        for wait in value['wait_calls']:
            assert wait['called'] == 'Popen.wait' and wait['returned_exit_code'] == value['exit_code']
        for channel in value['drains'].values():
            if not channel.get('not_opened'):
                assert channel['error'] is None and channel['terminal'] in ('eof', 'reset')
                if value['exit_code'] == 0:
                    assert channel['terminal'] == 'eof'
        assert value['counters']['cont'] == 0
        assert all(row['complete_after_actual_wait'] for row in value['stdio'].values())
        result.append({'fixture': name, **value})
    return result
