#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned-child QCE transport reused unchanged; TCG also exposes RAM QTest."""
import json
from pathlib import Path
import subprocess
import tempfile

from qce_bam_test_transport import Rig, put_json, sha
from ras_zero_records_test_contract import CPU_PROP, PROFILE_PROP, require


class RasRig(Rig):
    def __init__(self, args, directory, *, mode=None, incoming=False,
                 accel='tcg', invalid_cpu=None):
        assert mode in (None, False, True) and accel in ('tcg', 'qtest')
        assert invalid_cpu in (None, 'cortex-a55', 'cortex-a77')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sockets = Path(tempfile.mkdtemp(prefix='rzr-'))
        self.process = None
        self.peers = {}
        self.closed = False
        self.expected_failure = invalid_cpu is not None
        self.expected_exit = 1 if self.expected_failure else 0
        self.request_id = 0
        self.count = dict(qom_get=0, qom_set=0, qtest=0, memory=0,
                          device_mmio=0, ram=0, cont=0)
        self.events, self.irq_events, self.waits = [], [], []
        self.irq = {0: False}
        self.intercepted = False
        self.logs = {name: (self.directory / name).open('wb')
                     for name in ('qemu.stdout', 'qemu.stderr')}
        self.protocol = (self.directory / 'protocol.jsonl').open('w')
        machine = args.machine
        if mode is not None:
            machine += ',ras-zero-records-compat=' + ('on' if mode else 'off')
        if invalid_cpu:
            machine = 'none'
        command = [str(Path(args.qemu).resolve()), '-machine', machine,
                   '-accel', 'tcg,thread=single' if accel == 'tcg' else 'qtest',
                   '-m', '128M', '-smp', '1' if invalid_cpu else '8', '-S',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null',
                   '-serial', 'null', '-d', 'guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.sockets}/m,server=on,wait=off',
                   '-qtest', f'unix:{self.sockets}/t,server=on,wait=off',
                   '-qtest-log', str(self.directory / 'qtest-server.log')]
        if invalid_cpu:
            command += ['-cpu', invalid_cpu + ',' + CPU_PROP + '=on,has_el2=on,has_el3=off']
        else:
            command += ['-device', 'loader,file=' + str(Path(args.probe).resolve()) + ',cpu-num=0']
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
            if invalid_cpu:
                self.wait_exit(15)
                return
            self.peers['qmp'] = self.connect('m', 'qmp')
            greeting = json.loads(self.peers['qmp'].receive())
            self.record({'qmp_receive': greeting})
            require('QMP' in greeting, 'QMP greeting')
            self.monitor('qmp_capabilities')
            self.peers['qtest'] = self.connect('t', 'qtest')
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        result = []
        for cpu in range(8):
            path = f'/machine/cpu{cpu}'
            result.append({p: self.monitor('qom-get', {'path': path, 'property': p})
                           for p in (CPU_PROP, PROFILE_PROP)})
        return result

    def pure_profiles(self, mode):
        before = dict(self.count)
        first, second = self.snapshot(), self.snapshot()
        expected = [{CPU_PROP: mode, PROFILE_PROP: int(mode)} for _ in range(8)]
        require(first == second == expected, 'two exact typed eight-CPU profiles')
        for row in first + second:
            require(type(row[CPU_PROP]) is bool and type(row[PROFILE_PROP]) is int, 'QOM primitive types')
        require(self.count['qom_get'] - before['qom_get'] == 32, '32 pure getter calls')
        require(all(self.count[k] == before[k] for k in ('qtest', 'memory', 'ram', 'device_mmio', 'cont', 'qom_set')),
                'profile observation changed guest/RAM/runstate')
        return {'first': first, 'second': second, 'before': before, 'after': dict(self.count)}


def cleanup_records(out, names):
    paths = {p.parent.name: p for p in Path(out).glob('*/cleanup.json')}
    require(set(paths) == set(names), 'one cleanup per actual fixture')
    result = []
    for name in names:
        c = json.loads(paths[name].read_bytes())
        require(c['actually_waited_and_reaped'] and c['exit_code'] == c['expected_exit_code'], 'actual child wait/exit')
        require(c['exit_code'] in (0, 1), 'unexpected QEMU exit')
        require(not c['forced_terminate'] and not c['forced_kill'] and not c['signals_to_preexisting_processes'], 'forced cleanup')
        require(len(c['wait_calls']) == (1 if c['exit_code'] == 0 else 2), 'actual Popen.wait count')
        for w in c['wait_calls']:
            require(w['called'] == 'Popen.wait' and w['returned_exit_code'] == c['exit_code'], 'wait journal')
        if c['exit_code'] == 0:
            require(c['quit_sent'] and c['quit_acknowledged'] and c['quit_error'] is None, 'normal quit ACK')
        else:
            require(not c['quit_sent'] and c['quit_not_applicable'] == 'already-exited', 'fatal exit closure')
        require(set(c['drains']) == {'qmp', 'qtest'} and not c['irq_events'] and not c['unexpected_qtest_drain_lines'], 'transport channel/IRQ set')
        opened = not name.startswith('invalid-')
        for label, d in c['drains'].items():
            require(d['channel'] == label and bool(d.get('not_opened')) != opened, 'actual opened channel drain')
            if not opened: continue
            require(d['error'] is None and d['terminal'] in ('eof', 'reset'), 'channel terminal')
            require(d['terminal'] == 'eof' or c['exit_code'] == 1, 'normal socket reset')
            raw = bytes.fromhex(d['drain_hex'])
            require(len(raw) == d['drain_bytes'], 'drain complete bytes')
            if label == 'qtest': require(raw == b'', 'unconsumed QTest data')
            else:
                for line in raw.splitlines():
                    event = json.loads(line)
                    require('event' in event and event['event'] in ('SHUTDOWN', 'STOP', 'RESUME', 'MIGRATION'), 'unconsumed QMP response')
        require(c['counters']['device_mmio'] == 0, 'no device MMIO in guest register test')
        for file, pin in c['stdio'].items():
            p = paths[name].parent / file
            require(pin['complete_after_actual_wait'] is True and p.stat().st_size == pin['bytes'] and sha(p) == pin['sha256'], 'full stdio after wait')
            if c['exit_code'] == 0 or file == 'qemu.stdout': require(pin['bytes'] == 0, 'unexpected guest/normal stdio')
        result.append({'fixture': name, **c})
    return result
