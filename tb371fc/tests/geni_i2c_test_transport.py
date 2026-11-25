#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Owned-child I2C fixture using the unchanged QCE protocol and cleanup code.

Only construction, addressing and IRQ assertion policy differ. The explicit
optional HS global is needed to test shared reset fanout with both consumers.
No existing transport, native runner or gate is changed by this module.
"""
import json
from pathlib import Path
import subprocess
import tempfile

from qce_bam_test_transport import Rig, file_channels, put_json, sha
from geni_i2c_test_contract import INSTANCES, WINDOW_BYTES, device_path, instance_row


class I2CRig(Rig):
    def __init__(self, args, directory, *, enabled=None, hs=False,
                 incoming=False, accel='qtest'):
        assert enabled in (None, False, True)
        assert type(hs) is bool and accel in ('qtest', 'tcg')
        assert 'geni-i2c-enabled' not in args.machine
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sockets = Path(tempfile.mkdtemp(prefix='gi2-'))
        self.process = None
        self.peers = {}
        self.closed = False
        self.expected_failure = False
        self.expected_exit = 0
        self.request_id = 0
        self.count = dict(qom_get=0, qom_set=0, qtest=0, memory=0,
                          device_mmio=0, ram=0, cont=0, gcc_mmio=0)
        self.events, self.irq_events, self.waits = [], [], []
        self.irq = {0: False}
        self.intercepted = False
        self.intercepted_instance = None
        self.enabled = enabled is True
        self.accel = accel
        self.expected_cont = 0
        self.logs = {name: (self.directory / name).open('wb')
                     for name in ('qemu.stdout', 'qemu.stderr')}
        self.protocol = (self.directory / 'protocol.jsonl').open('w')
        machine = args.machine
        if enabled is not None:
            machine += ',geni-i2c-enabled=' + ('on' if enabled else 'off')
        command = [str(Path(args.qemu).resolve()), '-machine', machine,
                   '-accel', accel, '-m', '128M', '-smp', '8', '-S',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null',
                   '-serial', 'null', '-d', 'unimp,guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.sockets}/m,server=on,wait=off']
        if hs:
            command += ['-global', 'qcom-sm8250-geni-hs.enabled-reference=on']
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
            assert 'QMP' in greeting, greeting
            self.monitor('qmp_capabilities')
            if accel == 'qtest':
                self.peers['qtest'] = self.connect('t', 'qtest')
        except BaseException:
            self.close()
            raise

    def qom(self, instance, prop):
        return self.monitor('qom-get', {'path': device_path(instance), 'property': prop})

    def qt(self, command):
        fields = command.split()
        if fields[0] in ('readb', 'readw', 'readl', 'readq', 'read',
                         'writeb', 'writew', 'writel', 'writeq', 'write'):
            address = int(fields[1], 0)
            if any(base <= address < base + WINDOW_BYTES for _, base, *_ in INSTANCES):
                self.count['device_mmio'] += 1
            if 0x100000 <= address < 0x2f0000:
                self.count['gcc_mmio'] += 1
        return super().qt(command)

    def read(self, instance, offset, width='l'):
        base = instance_row(instance)[1]
        return int(self.qt(f'read{width} {base + offset:#x}')[0], 0)

    def write(self, instance, offset, value, width='l'):
        base = instance_row(instance)[1]
        self.qt(f'write{width} {base + offset:#x} {value:#x}')

    def intercept(self, instance):
        assert not self.intercepted
        self.qt('irq_intercept_out ' + device_path(instance) + ' sysbus-irq')
        self.intercepted = True
        self.intercepted_instance = instance

    def check_irq(self, level):
        assert type(level) is bool and self.intercepted
        assert self.qt('endianness') == ['little']
        actual = self.qom(self.intercepted_instance, 'irq-level')
        assert type(actual) is bool and actual is level
        assert self.irq[0] is level

    def start_qtest(self):
        """Run QEMU device callbacks; the qtest accelerator executes no guest CPU."""
        assert self.accel == 'qtest' and self.count['cont'] == 0
        self.monitor('cont')
        self.expected_cont = 1
        status = self.monitor('query-status')
        assert status['running'] is True and status['status'] == 'running'
        return {'accelerator': 'qtest', 'running': True,
                'guest_cpu_executed': False, 'cont_commands': 1}


def verify_i2c_cleanup(out, expected_fixtures):
    """Keep all existing wait/quit/EOF requirements; IRQ raises are expected."""
    records = []
    paths = sorted(Path(out).glob('*/cleanup.json'))
    assert {p.parent.name for p in paths} == set(expected_fixtures)
    for path in paths:
        value = json.loads(path.read_bytes())
        assert value['actually_waited_and_reaped'] is True
        assert value['wait_calls'] and value['exit_code'] == value['expected_exit_code']
        assert not value['forced_terminate'] and not value['forced_kill']
        assert value['signals_to_preexisting_processes'] is False
        assert value['unexpected_qtest_drain_lines'] == []
        if value['quit_sent']:
            assert value['quit_acknowledged'] and value['quit_error'] is None
        else:
            assert value['quit_not_applicable'] == 'already-exited'
            assert value['expected_exit_code'] != 0
        for channel in value['drains'].values():
            if not channel.get('not_opened'):
                assert channel['error'] is None and channel['terminal'] in ('eof', 'reset')
                if value['expected_exit_code'] == 0:
                    assert channel['terminal'] == 'eof'
        assert value['counters']['cont'] == expected_fixtures[path.parent.name]
        records.append({'fixture': path.parent.name, **value})
    assert records
    return records
