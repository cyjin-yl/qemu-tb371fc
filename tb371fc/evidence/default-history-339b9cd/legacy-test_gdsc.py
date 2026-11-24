#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Qualcomm GDSC digital votes, register errors, reset and actual migration.

This suite verifies the captured GPU CX control and status addresses. It uses
real QTest transactions and intercepted output GPIOs. It does not claim GPU
operation, physical power behavior, or a captured silicon reset value.
"""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
import traceback

GDSCR = 0x03d9106c
HW_CTRL = 0x03d91540
GX_GDSCR = 0x03d9100c
GX_DOMAIN = 0x03d91508
GX_RESET = 0x03d91008
GX_PATH = '/machine/gpu-gx-gdsc'
PWR_ON = 1 << 31
COLLAPSE = 1
HW_CONTROL = 1 << 1
SW_OVERRIDE = 1 << 2
RETAIN = 1 << 11
WAIT = 0xf << 12
PROFILE_DIAGNOSTIC = 'reference-on differs between source and destination'
ENABLED_PROFILE_DIAGNOSTIC = 'enabled-reference differs between source and destination'
UNSUPPORTED_DIAGNOSTIC = 'unsupported'



@contextmanager
def retained_socket_directory(prefix):
    # Preserve paths referenced by the transcripts, including on failure.
    yield tempfile.mkdtemp(prefix=prefix)


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def args_with_profile(args, profile):
    values = vars(args).copy()
    parts = [part for part in args.machine.split(',')
             if not part.startswith('gdsc-profile=')]
    parts.append('gdsc-profile=' + profile)
    values['machine'] = ','.join(parts)
    return argparse.Namespace(**values)


class GdscRig:
    def __init__(self, args, directory, reference=None, incoming=False,
                 intercept='power-on'):
        self.directory = directory
        self.directory.mkdir(parents=True, exist_ok=False)
        self.proc = None
        self.qmp_socket = None
        self.qtest_socket = None
        self.qmp_buffer = b''
        self.qtest_buffer = b''
        self.qmp_log = None
        self.qtest_log = None
        self.stderr_stream = None
        self.socket_dir = None
        self.qmp_id = 0
        self.events = []
        self.irq = {}
        self.last_clock = 0
        self.path = args.qom_path
        self.intercept = intercept
        self.log_path = directory / 'qemu.log'
        self.stderr_path = directory / 'qemu.stderr'
        try:
            self.socket_dir = Path(tempfile.mkdtemp(prefix='qgdsc-'))
            qmp_path = str(self.socket_dir / 'm')
            qtest_path = str(self.socket_dir / 't')
            check(max(len(qmp_path.encode()), len(qtest_path.encode())) < 104,
                  'TMPDIR is too long for Unix sockets')
            machine = args.machine
            if reference is not None:
                parts = [part for part in machine.split(',')
                         if not part.startswith('gdsc-profile=')]
                parts.append('gdsc-profile=reference-' + ('on' if reference else 'off'))
                machine = ','.join(parts)
            command = [
                args.qemu, '-machine', machine, '-accel', 'qtest',
                '-m', '128M', '-smp', '8', '-display', 'none', '-monitor', 'none',
                '-chardev', 'null,id=gdsc-serial',
                '-serial', 'chardev:gdsc-serial', '-S',
                '-qmp', 'unix:' + qmp_path + ',server=on,wait=off',
                '-qtest', 'unix:' + qtest_path + ',server=on,wait=off',
                '-d', 'unimp,guest_errors', '-D', str(self.log_path),
            ]
            if incoming:
                command += ['-incoming', 'defer']
            (directory / 'command.json').write_text(
                json.dumps(command, indent=2) + '\n')
            self.stderr_stream = self.stderr_path.open('wb')
            self.qmp_log = (directory / 'qmp.log').open('w', buffering=1)
            self.qtest_log = (directory / 'qtest.log').open('w', buffering=1)
            self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                         stdout=self.stderr_stream,
                                         stderr=self.stderr_stream)
            deadline = time.monotonic() + 15
            self.qmp_socket = self._connect(qmp_path, deadline)
            self.qtest_socket = self._connect(qtest_path, deadline)
            greeting = json.loads(self._line('qmp'))
            check('QMP' in greeting, greeting)
            self.monitor('qmp_capabilities')
            self.command(f'irq_intercept_out {self.path} {intercept}')
            if not incoming:
                self.reset()
        except Exception:
            self.close()
            raise

    def _connect(self, path, deadline):
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError('QEMU exited at startup: ' +
                                   self.stderr_path.read_text(errors='replace')[-6000:])
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(1)
            try:
                sock.connect(path)
                return sock
            except (FileNotFoundError, ConnectionRefusedError, socket.timeout):
                sock.close()
                time.sleep(0.02)
        raise TimeoutError('QEMU socket startup: ' + path)

    def _line(self, kind, timeout=10):
        sock = getattr(self, kind + '_socket')
        buffer = getattr(self, kind + '_buffer')
        deadline = time.monotonic() + timeout
        while b'\n' not in buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(kind + ' reply')
            sock.settimeout(remaining)
            chunk = sock.recv(65536)
            if not chunk:
                raise ConnectionError(kind + ' connection closed')
            buffer += chunk
        line, buffer = buffer.split(b'\n', 1)
        setattr(self, kind + '_buffer', buffer)
        text = line.decode('utf-8')
        getattr(self, kind + '_log').write('< ' + text + '\n')
        return text

    def monitor(self, command, arguments=None):
        self.qmp_id += 1
        ident = self.qmp_id
        request = dict(execute=command, id=ident)
        if arguments is not None:
            request['arguments'] = arguments
        encoded = json.dumps(request)
        self.qmp_log.write('> ' + encoded + '\n')
        self.qmp_socket.sendall((encoded + '\n').encode())
        deadline = time.monotonic() + 10
        while True:
            result = json.loads(self._line(
                'qmp', max(0.001, deadline - time.monotonic())))
            if 'event' in result:
                self.events.append(result)
                if time.monotonic() >= deadline:
                    raise TimeoutError('QMP response after events: ' + command)
                continue
            check(result.get('id') == ident, ('unexpected QMP response', result))
            check('error' not in result, result)
            return result['return']

    def command(self, line):
        self.qtest_log.write('> ' + line + '\n')
        self.qtest_socket.sendall((line + '\n').encode())
        deadline = time.monotonic() + 10
        while True:
            reply = self._line('qtest', max(0.001, deadline - time.monotonic()))
            fields = reply.split()
            if fields[:1] == ['IRQ']:
                check(len(fields) == 3 and fields[1] in ('raise', 'lower'), reply)
                self.irq[int(fields[2])] = fields[1] == 'raise'
                if time.monotonic() >= deadline:
                    raise TimeoutError('QTest response after IRQs: ' + line)
                continue
            check(fields[:1] == ['OK'], ('QTest failure', line, reply))
            return fields[1:]

    def wait_for(self, predicate, label, timeout=15):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.02)
        raise TimeoutError(label)

    def reset(self):
        first_event = len(self.events)
        self.monitor('system_reset')

        def observed():
            self.monitor('query-status')
            return any(event.get('event') == 'RESET'
                       for event in self.events[first_event:])

        self.wait_for(observed, 'guest-visible system reset', timeout=10)

    def step(self, delta):
        check(delta > 0, ('clock step must be strictly positive', delta))
        response = self.command(f'clock_step {delta}')
        check(len(response) == 1, response)
        self.last_clock = int(response[0], 0)
        return self.last_clock

    def read(self, address=GDSCR):
        return int(self.command(f'readl {address:#x}')[0], 0)

    def write(self, value, address=GDSCR):
        self.command(f'writel {address:#x} {value:#x}')

    def input(self, name, level):
        self.command(f'set_irq_in {self.path} {name} 0 {int(level)}')

    def expect_gpio(self, level):
        # Positive advancement flushes asynchronous output messages and avoids
        # QTest's special clock_step 0 semantics.
        self.step(1)
        if level:
            check(0 in self.irq, 'power output was never raised')
        check(self.irq.get(0, False) is level, ('power output', level, self.irq))

    def expect(self, control, powered):
        check(self.read() == (control | (PWR_ON if powered else 0)),
              ('GDSCR', hex(self.read()), hex(control), powered))
        check(self.read(HW_CTRL) == (PWR_ON if powered else 0),
              ('HW_CTRL', hex(self.read(HW_CTRL)), powered))
        self.expect_gpio(powered)

    def diagnostic_count(self, text=UNSUPPORTED_DIAGNOSTIC):
        return self.log_path.read_text(errors='replace').count(text)

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        for attribute in ('qmp_socket', 'qtest_socket', 'qmp_log', 'qtest_log',
                          'stderr_stream'):
            item = getattr(self, attribute, None)
            if item is not None:
                item.close()
                setattr(self, attribute, None)
        if self.socket_dir is not None:
            self.socket_dir = None


def default_reference_starts_collapsed(rig):
    rig.expect(COLLAPSE, False)


def explicit_on_reference_starts_powered(rig):
    rig.expect(0, True)


def downstream_software_enable_disable_read_modify_write(rig):
    rig.expect(COLLAPSE, False)
    value = rig.read()
    rig.write(value & ~COLLAPSE)
    rig.expect(0, True)
    value = rig.read()
    rig.write(value | COLLAPSE)
    rig.expect(COLLAPSE, False)


def read_only_power_bit_cannot_override_votes(rig):
    rig.write(PWR_ON | COLLAPSE)
    rig.expect(COLLAPSE, False)
    rig.write(0)
    rig.expect(0, True)
    # A write omitting the RO bit cannot clear power while SW still votes on.
    rig.write(RETAIN)
    rig.expect(RETAIN, True)


def retain_ff_and_clock_wait_survive_power_changes(rig):
    fields = RETAIN | (0xa << 12)
    rig.write(fields | COLLAPSE)
    rig.expect(fields | COLLAPSE, False)
    rig.write(rig.read() & ~COLLAPSE)
    rig.expect(fields, True)
    rig.write(rig.read() | COLLAPSE)
    rig.expect(fields | COLLAPSE, False)
    rig.write((rig.read() & ~WAIT) | (5 << 12))
    rig.expect(RETAIN | (5 << 12) | COLLAPSE, False)


def hardware_control_follows_external_input(rig):
    rig.write(HW_CONTROL | COLLAPSE)
    rig.expect(HW_CONTROL | COLLAPSE, False)
    rig.input('hw-enable', True)
    rig.expect(HW_CONTROL | COLLAPSE, True)
    rig.input('hw-enable', False)
    rig.expect(HW_CONTROL | COLLAPSE, False)
    # Under hardware control, clearing SW_COLLAPSE alone cannot power up.
    rig.write(HW_CONTROL)
    rig.expect(HW_CONTROL, False)
    rig.input('hw-enable', True)
    rig.expect(HW_CONTROL, True)


def hardware_input_is_ignored_in_software_mode(rig):
    rig.input('hw-enable', True)
    rig.expect(COLLAPSE, False)
    rig.write(0)
    rig.expect(0, True)
    rig.input('hw-enable', False)
    rig.expect(0, True)
    rig.write(HW_CONTROL)
    rig.expect(HW_CONTROL, False)
    rig.write(0)
    rig.expect(0, True)


def independent_vote_can_keep_software_collapsed_domain_powered(rig):
    rig.input('external-vote', True)
    rig.expect(COLLAPSE, True)
    rig.write(0)
    rig.expect(0, True)
    rig.input('external-vote', False)
    rig.expect(0, True)
    rig.write(rig.read() | COLLAPSE)
    rig.expect(COLLAPSE, False)


def independent_vote_and_hardware_vote_release_independently(rig):
    rig.write(HW_CONTROL | COLLAPSE)
    rig.input('external-vote', True)
    rig.expect(HW_CONTROL | COLLAPSE, True)
    rig.input('hw-enable', True)
    rig.input('external-vote', False)
    rig.expect(HW_CONTROL | COLLAPSE, True)
    rig.input('external-vote', True)
    rig.input('hw-enable', False)
    rig.expect(HW_CONTROL | COLLAPSE, True)
    rig.input('external-vote', False)
    rig.expect(HW_CONTROL | COLLAPSE, False)


def unknown_and_override_bits_are_rejected_without_partial_updates(rig):
    value = RETAIN | (3 << 12) | COLLAPSE
    rig.write(value)
    before = rig.diagnostic_count()
    for bad in (SW_OVERRIDE, 1 << 3, 1 << 10, 1 << 16, 1 << 30):
        # Clearing collapse in the rejected transaction must not power up.
        rig.write(RETAIN | bad)
        rig.expect(value, False)
    check(rig.diagnostic_count() - before == 5,
          'reserved-field writes were not individually diagnosed')


def hardware_status_writes_do_not_change_control_or_status(rig):
    before = rig.diagnostic_count()
    rig.write(PWR_ON, HW_CTRL)
    rig.expect(COLLAPSE, False)
    rig.write(0)
    rig.expect(0, True)
    rig.write(0, HW_CTRL)
    rig.expect(0, True)
    check(rig.diagnostic_count() - before == 2,
          'read-only HW status writes were not diagnosed')


def subword_accesses_are_rejected_without_changing_registers(rig):
    control = RETAIN | (6 << 12) | COLLAPSE
    rig.write(control)
    before = rig.diagnostic_count()
    operations = [
        f'writeb {GDSCR:#x} 0',
        f'writew {GDSCR:#x} 0',
        f'writew {GDSCR + 1:#x} 0',
        f'writeb {GDSCR + 3:#x} 0',
        f'readb {GDSCR:#x}',
        f'readw {GDSCR + 1:#x}',
        f'writew {HW_CTRL:#x} 1',
        f'readb {HW_CTRL:#x}',
    ]
    for operation in operations:
        rig.command(operation)
        rig.expect(control, False)
    check(rig.diagnostic_count() - before >= len(operations),
          'subword or alignment rejection diagnostics missing')


def reset_restores_off_reference_but_preserves_external_inputs(rig):
    rig.write(RETAIN | WAIT | HW_CONTROL | COLLAPSE)
    rig.input('hw-enable', True)
    rig.input('external-vote', True)
    rig.expect(RETAIN | WAIT | HW_CONTROL | COLLAPSE, True)
    rig.reset()
    rig.expect(COLLAPSE, True)
    rig.input('external-vote', False)
    rig.expect(COLLAPSE, False)
    rig.write(HW_CONTROL | COLLAPSE)
    rig.expect(HW_CONTROL | COLLAPSE, True)
    rig.input('hw-enable', False)
    rig.expect(HW_CONTROL | COLLAPSE, False)


def reset_restores_on_reference_and_clears_retention_fields(rig):
    rig.expect(0, True)
    rig.write(RETAIN | WAIT | COLLAPSE)
    rig.expect(RETAIN | WAIT | COLLAPSE, False)
    rig.reset()
    rig.expect(0, True)


def elapsed_time_does_not_invent_a_hardware_vote(rig):
    rig.write(HW_CONTROL)
    rig.step(1_000_000)
    rig.expect(HW_CONTROL, False)
    rig.input('hw-enable', True)
    rig.step(1_000_000)
    rig.expect(HW_CONTROL, True)
    rig.input('hw-enable', False)
    rig.step(1_000_000)
    rig.expect(HW_CONTROL, False)


def gx_and_cx_domains_have_independent_votes_and_control(rig):
    rig.expect(COLLAPSE, False)
    check(rig.read(GX_GDSCR) == COLLAPSE, 'GX initial collapse')
    rig.write(0, GX_GDSCR)
    check(rig.read(GX_GDSCR) == PWR_ON, 'GX software enable')
    rig.expect(COLLAPSE, False)
    rig.write(0)
    rig.expect(0, True)
    rig.write(COLLAPSE, GX_GDSCR)
    check(rig.read(GX_GDSCR) == COLLAPSE, 'GX software disable')
    rig.expect(0, True)
    rig.command(f'set_irq_in {GX_PATH} external-vote 0 1')
    check(rig.read(GX_GDSCR) == (PWR_ON | COLLAPSE), 'GX independent vote')
    rig.write(COLLAPSE)
    rig.expect(COLLAPSE, False)
    rig.command(f'set_irq_in {GX_PATH} external-vote 0 0')
    check(rig.read(GX_GDSCR) == COLLAPSE, 'GX independent vote release')


def gx_clamp_and_two_reset_sources_have_digital_readback(rig):
    def output(name):
        return rig.monitor('qom-get', dict(path=GX_PATH, property=name))

    check(rig.read(GX_DOMAIN) == 1, 'reference-off GX starts clamped')
    check(rig.read(GX_RESET) == 0, 'GX software reset initially deasserted')
    check(output('clamp-io') is True, 'initial clamp output')
    check(output('reset-asserted') is False, 'initial reset output')
    rig.write(0, GX_GDSCR)
    rig.write(0x11, GX_DOMAIN)
    check(rig.read(GX_DOMAIN) == 0x11, 'GX domain register readback')
    check(output('reset-asserted') is True, 'domain reset assertion')
    rig.write(1, GX_RESET)
    rig.write(1, GX_DOMAIN)
    check(output('reset-asserted') is True,
          'software reset must survive clearing domain reset')
    rig.write(0, GX_RESET)
    check(output('reset-asserted') is False, 'last reset source release')
    rig.write(0, GX_DOMAIN)
    check(output('clamp-io') is False, 'clamp release')
    check(rig.read(GX_GDSCR) == PWR_ON,
          'digital isolation/reset controls incorrectly changed power request')
    rig.expect(COLLAPSE, False)


def gx_reserved_writes_are_atomic_and_reset_restores_reference(rig):
    rig.write(0x11, GX_DOMAIN)
    rig.write(1, GX_RESET)
    before = rig.diagnostic_count()
    for address, value in [(GX_DOMAIN, 2), (GX_DOMAIN, 0x20),
                           (GX_RESET, 2), (GX_RESET, 0xffffffff)]:
        rig.write(value, address)
        check(rig.read(GX_DOMAIN) == 0x11, 'bad write changed GX domain')
        check(rig.read(GX_RESET) == 1, 'bad write changed GX reset')
    check(rig.diagnostic_count() - before == 4,
          'GX reserved writes were not individually diagnosed')
    rig.reset()
    check(rig.read(GX_DOMAIN) == 1, 'GX reset did not restore reference clamp')
    check(rig.read(GX_RESET) == 0, 'GX reset did not clear software reset')
    rig.expect(COLLAPSE, False)


BEHAVIOR = [
    (default_reference_starts_collapsed, None),
    (explicit_on_reference_starts_powered, True),
    (downstream_software_enable_disable_read_modify_write, False),
    (read_only_power_bit_cannot_override_votes, False),
    (retain_ff_and_clock_wait_survive_power_changes, False),
    (hardware_control_follows_external_input, False),
    (hardware_input_is_ignored_in_software_mode, False),
    (independent_vote_can_keep_software_collapsed_domain_powered, False),
    (independent_vote_and_hardware_vote_release_independently, False),
    (unknown_and_override_bits_are_rejected_without_partial_updates, False),
    (hardware_status_writes_do_not_change_control_or_status, False),
    (subword_accesses_are_rejected_without_changing_registers, False),
    (reset_restores_off_reference_but_preserves_external_inputs, False),
    (reset_restores_on_reference_and_clears_retention_fields, True),
    (elapsed_time_does_not_invent_a_hardware_vote, False),
    (gx_and_cx_domains_have_independent_votes_and_control, False),
    (gx_clamp_and_two_reset_sources_have_digital_readback, False),
    (gx_reserved_writes_are_atomic_and_reset_restores_reference, False),
]


def actual_migration(args, directory, reference, control, hw_enable,
                     external_vote, powered, gx_registers=False):
    source = target = None
    try:
        source = GdscRig(args, directory / 'source', reference=reference)
        source.write(control)
        source.input('hw-enable', hw_enable)
        source.input('external-vote', external_vote)
        source.expect(control, powered)
        if gx_registers:
            source.write(RETAIN, GX_GDSCR)
            source.write(0x11, GX_DOMAIN)
            source.write(1, GX_RESET)
        source.step(1)
        now = source.last_clock
        source.monitor('stop')
        target = GdscRig(args, directory / 'target', reference=reference,
                         incoming=True)
        if now > target.last_clock:
            target.step(now - target.last_clock)
        check(target.last_clock == now, 'source and destination time differ')
        with retained_socket_directory(prefix='qgdsc-migrate-') as socket_dir:
            uri = 'unix:' + str(Path(socket_dir) / 'm')
            target.monitor('migrate-incoming', dict(uri=uri))
            source.monitor('migrate', dict(uri=uri))
            source.wait_for(
                lambda: source.monitor('query-migrate').get('status')
                in ('completed', 'failed'), 'source migration', timeout=30)
            result = source.monitor('query-migrate')
            check(result['status'] == 'completed', result)
            target.wait_for(
                lambda: target.monitor('query-status')['status'] != 'inmigrate',
                'destination migration', timeout=30)
        check(not target.monitor('query-status')['running'],
              'destination resumed unexpectedly')
        target.expect(control, powered)
        if gx_registers:
            check(target.read(GX_GDSCR) == (PWR_ON | RETAIN),
                  'GX control did not migrate independently from CX')
            check(target.read(GX_DOMAIN) == 0x11, 'GX domain control migration')
            check(target.read(GX_RESET) == 1, 'GX software reset migration')
            check(target.monitor('qom-get', dict(path=GX_PATH,
                       property='clamp-io')) is True, 'GX migrated clamp output')
            check(target.monitor('qom-get', dict(path=GX_PATH,
                       property='reset-asserted')) is True, 'GX migrated reset output')
            target.write(1, GX_DOMAIN)
            check(target.monitor('qom-get', dict(path=GX_PATH,
                       property='reset-asserted')) is True, 'GX migrated second reset source')
            target.write(0, GX_RESET)
            check(target.monitor('qom-get', dict(path=GX_PATH,
                       property='reset-asserted')) is False, 'GX migrated reset release')
        for name, expected in [('hw-enable-state', hw_enable),
                               ('external-vote-state', external_vote)]:
            value = target.monitor('qom-get', dict(path=target.path, property=name))
            check(value is expected, (name, expected, value))
        # Verify retained inputs as behavior, including inputs invisible to the
        # initial state because another vote or mode dominated the output.
        target.input('external-vote', False)
        target.write(HW_CONTROL | COLLAPSE)
        target.expect(HW_CONTROL | COLLAPSE, hw_enable)
        target.input('hw-enable', False)
        target.expect(HW_CONTROL | COLLAPSE, False)
        target.write(RETAIN)
        target.expect(RETAIN, True)
        target.reset()
        target.expect(0 if reference else COLLAPSE, bool(reference))
        if gx_registers:
            check(target.read(GX_DOMAIN) == (0 if reference else 1),
                  'GX post-migration reference clamp reset')
            check(target.read(GX_RESET) == 0, 'GX post-migration software reset')
        return dict(migration=result, initial_control=control,
                    initial_hw_enable=hw_enable, initial_external_vote=external_vote,
                    initial_powered=powered, qtest_virtual_ns_at_transfer=now,
                    reference_on=reference, gx_registers=gx_registers)
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


def rejected_migration(args, directory, source_reference, target_reference,
                       source_profile=None, target_profile=None,
                       diagnostic=PROFILE_DIAGNOSTIC):
    source = target = None
    try:
        source_args = (args_with_profile(args, source_profile)
                       if source_profile is not None else args)
        target_args = (args_with_profile(args, target_profile)
                       if target_profile is not None else args)
        source = GdscRig(source_args, directory / 'source',
                         reference=source_reference)
        # Equal current control states still have incompatible future resets.
        if source_profile == 'none':
            check(source.monitor('qom-get', dict(path=source.path,
                       property='state-known')) is False, 'none source is known')
            source.expect_gpio(False)
        else:
            source.write(COLLAPSE)
            source.expect(COLLAPSE, False)
        source.step(1)
        now = source.last_clock
        source.monitor('stop')
        target = GdscRig(target_args, directory / 'target', reference=target_reference,
                         incoming=True)
        if now > target.last_clock:
            target.step(now - target.last_clock)
        check(target.last_clock == now, 'source and destination time differ')
        with retained_socket_directory(prefix='qgdsc-reject-') as socket_dir:
            uri = 'unix:' + str(Path(socket_dir) / 'm')
            target.monitor('migrate-incoming', dict(uri=uri))
            source.monitor('migrate', dict(uri=uri))
            target.wait_for(lambda: target.proc.poll() is not None,
                            'destination profile rejection', timeout=30)
            check(target.proc.returncode != 0, 'destination exited successfully')
            stderr = target.stderr_path.read_text(errors='replace')
            check(diagnostic in stderr,
                  'destination failed for a reason other than profile mismatch')
            source.wait_for(
                lambda: source.monitor('query-migrate').get('status')
                in ('completed', 'failed'), 'source transfer termination', timeout=30)
            source_result = source.monitor('query-migrate')
        return dict(source_reference=source_reference,
                    destination_reference=target_reference,
                    source_profile=source_profile,
                    destination_profile=target_profile,
                    source_transfer=source_result,
                    destination_exit=target.proc.returncode,
                    rejection_diagnostic=diagnostic,
                    qtest_virtual_ns_at_transfer=now)
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


def default_none_profile_has_no_mapping_or_invented_power(args, directory):
    # Omit the selector entirely to verify the machine's default policy.
    values = vars(args).copy()
    values['machine'] = ','.join(part for part in args.machine.split(',')
                                 if not part.startswith('gdsc-profile='))
    rig = GdscRig(argparse.Namespace(**values), directory, reference=None)
    try:
        for path in (rig.path, GX_PATH):
            check(rig.monitor('qom-get', dict(path=path,
                       property='state-known')) is False, ('none known state', path))
            check(rig.monitor('qom-get', dict(path=path,
                       property='power-on')) is False, ('none power output', path))
        flatview = rig.monitor('human-monitor-command',
                               {'command-line': 'info mtree -f'})
        (directory / 'flatview.txt').write_text(flatview)
        check('qcom-gdsc.' not in flatview,
              'default none unexpectedly maps a GDSC register window')
        rig.input('hw-enable', True)
        rig.input('external-vote', True)
        rig.expect_gpio(False)
        check(rig.monitor('qom-get', dict(path=rig.path,
                   property='power-on')) is False, 'disabled model invented power')
        return dict(profile='none (machine default)', mapped_windows=False,
                    state_known=False, intercepted_output='power-on')
    finally:
        rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', type=Path, required=True,
                        help='fresh directory; existing directories are rejected')
    parser.add_argument('--machine',
                        default='tb371fc,ufs-enabled=off,gdsc-profile=reference-off')
    parser.add_argument('--qom-path', default='/machine/gpu-cx-gdsc')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    report = dict(
        schema_version=1, suite='GDSC digital votes, reset and actual migration',
        expected_checks=29, passed=False, all_passed=False, success=False,
        qemu=args.qemu, qemu_sha256=sha256_file(args.qemu),
        test_sha256=sha256_file(__file__), machine=args.machine,
        qom_path=args.qom_path, output=str(args.out), checks=[],
        migrations=[], migration_expected_checks=10,
        socket_directories_retained=True,
        scope='GPU CX GDSCR/HW status and GX control/isolation/reset software '
              'contract with explicit reference '
              'reset and injected hardware/external votes; no analog, GPU, '
              'TrustZone execution, or whole-board migration claim',
    )
    cases = []
    for function, reference in BEHAVIOR:
        def run_case(directory, function=function, reference=reference):
            rig = GdscRig(args, directory, reference=reference)
            try:
                function(rig)
                return dict(reference_on=reference, intercepted_output='power-on')
            finally:
                rig.close()
        cases.append((function.__name__, run_case, False))
    cases.append(('default_none_has_no_mapping_or_invented_power',
                  lambda directory: default_none_profile_has_no_mapping_or_invented_power(
                      args, directory), False))
    for name, reference, control, hw_enable, external_vote, powered in [
        ('migration_software_on_with_retention_and_wait', False,
         RETAIN | (9 << 12), False, False, True),
        ('migration_hardware_mode_and_active_hardware_vote', False,
         HW_CONTROL | COLLAPSE | RETAIN, True, False, True),
        ('migration_independent_vote_while_hardware_vote_is_low', False,
         HW_CONTROL | COLLAPSE, False, True, True),
        ('migration_hidden_hardware_input_in_collapsed_software_mode', False,
         COLLAPSE | (4 << 12), True, False, False),
        ('migration_on_reference_preserves_later_reset_policy', True,
         COLLAPSE | WAIT | RETAIN, False, False, False),
    ]:
        def run_case(directory, reference=reference, control=control,
                     hw_enable=hw_enable, external_vote=external_vote, powered=powered):
            return actual_migration(args, directory, reference, control, hw_enable,
                                    external_vote, powered)
        cases.append((name, run_case, True))
    for name, source_profile, target_profile in [
        ('migration_none_to_reference_off_rejected', 'none', 'reference-off'),
        ('migration_reference_off_to_none_rejected', 'reference-off', 'none'),
    ]:
        def run_case(directory, source_profile=source_profile,
                     target_profile=target_profile):
            return rejected_migration(
                args, directory, None, None, source_profile, target_profile,
                diagnostic=ENABLED_PROFILE_DIAGNOSTIC)
        cases.append((name, run_case, True))
    def gx_migration(directory):
        return actual_migration(args, directory, False, COLLAPSE,
                                False, False, False, gx_registers=True)
    cases.append(('migration_gx_isolation_reset_and_control_independence',
                  gx_migration, True))
    for name, source_reference, target_reference in [
        ('migration_off_reference_to_on_reference_rejected', False, True),
        ('migration_on_reference_to_off_reference_rejected', True, False),
    ]:
        def run_case(directory, source_reference=source_reference,
                     target_reference=target_reference):
            return rejected_migration(args, directory, source_reference, target_reference)
        cases.append((name, run_case, True))
    check(len(cases) == report['expected_checks'], 'test plan count mismatch')
    for index, (name, function, migration) in enumerate(cases):
        directory = args.out / f'{index:02d}'
        started = time.monotonic()
        try:
            detail = function(directory)
            result = dict(test=name, status='pass', passed=True, detail=detail)
        except Exception:
            result = dict(test=name, status='fail', passed=False,
                          error=traceback.format_exc())
        result['seconds'] = round(time.monotonic() - started, 4)
        result['directory'] = str(directory)
        report['checks'].append(result)
        if migration:
            report['migrations'].append(result.copy())
        report['completed_checks'] = len(report['checks'])
        report['passed_checks'] = sum(item['passed'] for item in report['checks'])
        report['passed'] = report['all_passed'] = report['success'] = (
            len(report['checks']) == report['expected_checks'] and
            len(report['migrations']) == report['migration_expected_checks'] and
            all(item['passed'] for item in report['checks']))
        (args.out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        if not result['passed']:
            break
    print(json.dumps(report, indent=2))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
