#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""REFGEN software-request, validity, and actual migration checks.

No analog behavior is inferred. Invalid QTest reads are checked through device
diagnostics and unchanged state because the QTest memory command does not
propagate MemTxResult to its client. Native CPU abort behavior is a separate gate.
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

CTRL5 = 0x088e7080
UNKNOWN_DIAGNOSTIC = 'CTRL5 initial state is unknown; refusing read'
PROFILE_DIAGNOSTIC = 'boot-disabled-reference differs between source and destination'


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


class RefgenRig:
    def __init__(self, args, directory, reference=None, incoming=False,
                 intercept='state-known'):
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
            self.socket_dir = Path(tempfile.mkdtemp(prefix='qref-'))
            qmp_path = str(self.socket_dir / 'm')
            qtest_path = str(self.socket_dir / 't')
            check(max(len(qmp_path.encode()), len(qtest_path.encode())) < 104,
                  'TMPDIR is too long for Unix sockets')
            command = [
                args.qemu, '-machine', args.machine, '-accel', 'qtest',
                '-m', '128M', '-smp', '8', '-display', 'none', '-monitor', 'none',
                '-chardev', 'null,id=refgen-serial',
                '-serial', 'chardev:refgen-serial', '-S',
                '-qmp', 'unix:' + qmp_path + ',server=on,wait=off',
                '-qtest', 'unix:' + qtest_path + ',server=on,wait=off',
                '-d', 'unimp,guest_errors', '-D', str(self.log_path),
            ]
            if reference is not None:
                command += [
                    '-global',
                    'qcom-refgen.boot-disabled-reference=' +
                    ('on' if reference else 'off'),
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

    def get(self, prop):
        return self.monitor('qom-get', dict(path=self.path, property=prop))

    def snapshot(self):
        return dict(state=self.get('state'), known=self.get('state-known'),
                    enabled=self.get('software-enable'))

    def read(self):
        return int(self.command(f'readl {CTRL5:#x}')[0], 0)

    def write(self, value):
        self.command(f'writel {CTRL5:#x} {value:#x}')

    def expect_state(self, state):
        values = {
            'unknown': dict(state='unknown', known=False, enabled=False),
            'disabled-request': dict(state='disabled-request', known=True,
                                     enabled=False),
            'enabled-request': dict(state='enabled-request', known=True,
                                    enabled=True),
        }
        check(self.snapshot() == values[state], (state, self.snapshot()))

    def expect_gpio(self, level):
        # A positive clock step flushes preceding asynchronous GPIO messages.
        self.step(1)
        # QTest starts its observer at low and emits only transitions.
        # Once raised, a low expectation still requires a real lower event.
        if level:
            check(0 in self.irq, ('GPIO output was never raised', self.intercept))
        check(self.irq.get(0, False) is level,
              (self.intercept, level, self.irq))

    def diagnostic_count(self, text):
        return self.log_path.read_text(errors='replace').count(text)

    def expect_unknown_read_rejected(self):
        before = self.diagnostic_count(UNKNOWN_DIAGNOSTIC)
        self.read()
        self.expect_state('unknown')
        check(self.diagnostic_count(UNKNOWN_DIAGNOSTIC) == before + 1,
              'unknown CTRL5 read was not explicitly rejected')

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


def default_state_is_unknown_and_read_is_rejected(rig):
    rig.expect_state('unknown')
    rig.expect_unknown_read_rejected()
    rig.expect_gpio(False)


def explicit_disabled_reference_drives_known_high(rig):
    rig.expect_state('disabled-request')
    check(rig.read() == 0, 'reference reset register')
    rig.expect_gpio(True)


def direct_zero_write_establishes_known_disabled_state(rig):
    rig.expect_state('unknown')
    rig.expect_gpio(False)
    rig.write(0)
    check(rig.read() == 0, 'direct zero write')
    rig.expect_state('disabled-request')
    rig.expect_gpio(True)


def direct_one_write_establishes_enabled_request(rig):
    rig.expect_state('unknown')
    rig.expect_gpio(False)
    rig.write(1)
    check(rig.read() == 1, 'direct one write')
    rig.expect_state('enabled-request')
    rig.expect_gpio(True)


def downstream_masked_read_modify_write_and_readback(rig):
    value = rig.read()
    rig.write((value & ~1) | 1)
    check(rig.read() & 1 == 1, 'vendor enable/is_enabled sequence')
    rig.expect_state('enabled-request')
    rig.expect_gpio(True)
    value = rig.read()
    rig.write((value & ~1) | 0)
    check(rig.read() & 1 == 0, 'vendor disable/is_enabled sequence')
    rig.expect_state('disabled-request')
    rig.expect_gpio(False)


def unsupported_bits_do_not_partially_modify_valid_state(rig):
    rig.write(1)
    rig.expect_gpio(True)
    before = rig.diagnostic_count('unsupported')
    for value in (2, 3, 0x80000000, 0xffffffff):
        rig.write(value)
        check(rig.read() == 1, ('invalid write changed request', value))
        rig.expect_state('enabled-request')
    check(rig.diagnostic_count('unsupported') - before == 4,
          'reserved-bit writes were not all diagnosed')
    rig.expect_gpio(True)


def subword_and_unaligned_accesses_do_not_initialize_unknown(rig):
    before = rig.diagnostic_count('unsupported')
    operations = [
        f'writeb {CTRL5:#x} 1',
        f'writew {CTRL5:#x} 1',
        f'writew {CTRL5 + 1:#x} 1',
        f'writeb {CTRL5 + 3:#x} 1',
        f'readb {CTRL5:#x}',
        f'readw {CTRL5 + 1:#x}',
    ]
    for operation in operations:
        rig.command(operation)
        rig.expect_state('unknown')
    check(rig.diagnostic_count('unsupported') - before >= len(operations),
          'subword/alignment rejection diagnostics missing')
    rig.expect_gpio(False)
    rig.expect_unknown_read_rejected()


def reset_without_reference_discards_known_state(rig):
    rig.write(1)
    rig.expect_state('enabled-request')
    rig.expect_gpio(True)
    rig.reset()
    rig.expect_state('unknown')
    rig.expect_gpio(False)
    rig.expect_unknown_read_rejected()


def reset_with_explicit_reference_restores_disabled_request(rig):
    rig.write(1)
    rig.expect_gpio(True)
    rig.reset()
    rig.expect_state('disabled-request')
    check(rig.read() == 0, 'reference reset did not restore zero')
    rig.expect_gpio(False)


def elapsed_time_does_not_invent_initialization_or_power_good(rig):
    rig.step(1_000_000)
    rig.expect_state('unknown')
    rig.expect_unknown_read_rejected()
    rig.expect_gpio(False)
    rig.write(1)
    rig.step(1_000_000)
    rig.expect_state('enabled-request')
    check(rig.read() == 1, 'software request changed without a write')
    rig.expect_gpio(True)


BEHAVIOR = [
    (default_state_is_unknown_and_read_is_rejected, None, 'state-known'),
    (explicit_disabled_reference_drives_known_high, True, 'state-known'),
    (direct_zero_write_establishes_known_disabled_state, False, 'state-known'),
    (direct_one_write_establishes_enabled_request, False, 'software-enable'),
    (downstream_masked_read_modify_write_and_readback, True, 'software-enable'),
    (unsupported_bits_do_not_partially_modify_valid_state, True, 'software-enable'),
    (subword_and_unaligned_accesses_do_not_initialize_unknown, False, 'state-known'),
    (reset_without_reference_discards_known_state, False, 'state-known'),
    (reset_with_explicit_reference_restores_disabled_request, True, 'software-enable'),
    (elapsed_time_does_not_invent_initialization_or_power_good, False, 'state-known'),
]


def actual_migration(args, directory, reference, initial, intercept):
    source = target = None
    try:
        source = RefgenRig(args, directory / 'source', reference=reference,
                           intercept=intercept)
        if initial is not None:
            source.write(initial)
        before = source.snapshot()
        # A transfer initiated at virtual time zero must not issue clock_step 0.
        source.step(1)
        now = source.last_clock
        source.monitor('stop')
        target = RefgenRig(args, directory / 'target', reference=reference,
                           incoming=True, intercept=intercept)
        if now > target.last_clock:
            target.step(now - target.last_clock)
        check(target.last_clock == now, 'source and destination time differ')
        with retained_socket_directory(prefix='qref-migrate-') as sock_dir:
            uri = 'unix:' + str(Path(sock_dir) / 'm')
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
              'destination resumed without permission')
        check(target.snapshot() == before, ('migrated state', before, target.snapshot()))
        if before['known']:
            check(target.read() == int(before['enabled']), 'migrated readback')
        else:
            target.expect_unknown_read_rejected()
        target.expect_gpio(before['known'] if intercept == 'state-known'
                           else before['enabled'])
        # Exercise the restored object, not just its serialized fields.
        target.write(1)
        target.expect_state('enabled-request')
        target.expect_gpio(True)
        target.write(0)
        target.expect_state('disabled-request')
        target.expect_gpio(intercept == 'state-known')
        target.reset()
        target.expect_state('disabled-request' if reference else 'unknown')
        target.expect_gpio(bool(reference) if intercept == 'state-known' else False)
        if not reference:
            target.expect_unknown_read_rejected()
        return dict(migration=result, before=before,
                    qtest_virtual_ns_at_transfer=now,
                    restored_reset_reference=reference)
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


def rejected_migration(args, directory, source_reference, target_reference):
    source = target = None
    try:
        source = RefgenRig(args, directory / 'source', reference=source_reference)
        source.write(0)
        source.expect_state('disabled-request')
        source.step(1)
        now = source.last_clock
        source.monitor('stop')
        target = RefgenRig(args, directory / 'target', reference=target_reference,
                           incoming=True)
        if now > target.last_clock:
            target.step(now - target.last_clock)
        check(target.last_clock == now, 'source and destination time differ')
        with retained_socket_directory(prefix='qref-reject-') as sock_dir:
            uri = 'unix:' + str(Path(sock_dir) / 'm')
            target.monitor('migrate-incoming', dict(uri=uri))
            source.monitor('migrate', dict(uri=uri))
            # QEMU terminates an incoming process on loadvm failure. A sender
            # may still report completed without a return path; that does not
            # establish destination acceptance.
            target.wait_for(lambda: target.proc.poll() is not None,
                            'destination rejection', timeout=30)
            destination_exit = target.proc.returncode
            check(destination_exit != 0, 'destination exited successfully')
            stderr = target.stderr_path.read_text(errors='replace')
            check(PROFILE_DIAGNOSTIC in stderr,
                  'destination failed for a reason other than profile mismatch')
            source.wait_for(
                lambda: source.monitor('query-migrate').get('status')
                in ('completed', 'failed'), 'source transfer termination', timeout=30)
            source_result = source.monitor('query-migrate')
        return dict(source_reference=source_reference,
                    destination_reference=target_reference,
                    source_transfer=source_result,
                    destination_exit=destination_exit,
                    rejection_diagnostic=PROFILE_DIAGNOSTIC,
                    qtest_virtual_ns_at_transfer=now)
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', type=Path, required=True,
                        help='fresh directory; existing directories are rejected')
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--qom-path', default='/machine/refgen')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    report = dict(
        schema_version=1,
        suite='REFGEN software request and validity actual QTest',
        expected_checks=16, passed=False, all_passed=False, success=False,
        qemu=args.qemu, qemu_sha256=sha256_file(args.qemu),
        test_sha256=sha256_file(__file__), machine=args.machine,
        qom_path=args.qom_path, output=str(args.out), checks=[],
        migrations=[], migration_expected_checks=6,
        socket_directories_retained=True,
        scope='Kona CTRL5 bit 0; explicit unknown/default and disabled/reference; '
              'component migration with UFS disabled; no analog, voltage, power-good, '
              'or whole-board migration claim',
    )
    cases = []
    for function, reference, intercept in BEHAVIOR:
        def run_case(directory, function=function, reference=reference,
                     intercept=intercept):
            rig = RefgenRig(args, directory, reference=reference, intercept=intercept)
            try:
                function(rig)
                return dict(reference=reference, intercepted_output=intercept)
            finally:
                rig.close()
        cases.append((function.__name__, run_case))
    for name, reference, initial, intercept in [
        ('migration_unknown_state_and_validity', False, None, 'state-known'),
        ('migration_known_disabled_and_validity', False, 0, 'state-known'),
        ('migration_enabled_request_without_boot_reference', False, 1, 'software-enable'),
        ('migration_enabled_request_with_boot_reference', True, 1, 'software-enable'),
    ]:
        def run_case(directory, reference=reference, initial=initial,
                     intercept=intercept):
            return actual_migration(args, directory, reference, initial, intercept)
        cases.append((name, run_case))
    for name, source_reference, target_reference in [
        ('migration_unknown_profile_to_reference_profile_rejected', False, True),
        ('migration_reference_profile_to_unknown_profile_rejected', True, False),
    ]:
        def run_case(directory, source_reference=source_reference,
                     target_reference=target_reference):
            return rejected_migration(args, directory, source_reference, target_reference)
        cases.append((name, run_case))
    check(len(cases) == report['expected_checks'], 'test plan count mismatch')
    for index, (name, function) in enumerate(cases):
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
        if index >= len(BEHAVIOR):
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
    output = json.dumps(report, indent=2) + '\n'
    print(output, end='')
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
