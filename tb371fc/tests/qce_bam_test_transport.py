#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QMP/QTest transport; no model oracle and no work on import.

Every child is created by this object. Cleanup never searches for or signals an
unrelated process. A forced cleanup of its own child is retained and fails the
acceptance gate. Socket availability polling is not a test retry.
"""
import hashlib
import json
from pathlib import Path
import select
import shutil
import socket
import subprocess
import tempfile
import time

TYPE = 'qcom-sm8250-qce-bam'
PATH = '/machine/qce-bam'
BASE, SIZE = 0x01dc4000, 0x3c000


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def put_json(path, obj):
    Path(path).write_text(json.dumps(obj, indent=2, sort_keys=True) + '\n')


def file_channels(path):
    return {'channels': [{'channel-type': 'main', 'addr': {
        'transport': 'file', 'filename': str(Path(path).resolve()), 'offset': 0}}]}


class Lines:
    """Unbuffered socket owner with explicit retained EOF/drain evidence."""
    def __init__(self, peer, record, label):
        self.peer, self.record, self.label = peer, record, label
        self.pending = bytearray()
        self.terminal = None
        self.received_bytes = 0

    def send(self, data):
        self.peer.sendall(data)

    def receive(self, timeout=10):
        end = time.monotonic() + timeout
        while b'\n' not in self.pending:
            if self.terminal:
                raise EOFError(f'{self.label}: {self.terminal}')
            remaining = end - time.monotonic()
            if remaining <= 0 or not select.select([self.peer], [], [], remaining)[0]:
                raise TimeoutError(f'{self.label}: no complete protocol line')
            try:
                raw = self.peer.recv(65536)
            except ConnectionResetError:
                self.terminal = 'reset'
                self.record({'channel': self.label, 'terminal': self.terminal})
                raise EOFError(f'{self.label}: reset')
            if not raw:
                self.terminal = 'eof'
                self.record({'channel': self.label, 'terminal': self.terminal})
                raise EOFError(f'{self.label}: eof')
            self.received_bytes += len(raw)
            self.pending.extend(raw)
        line, _, rest = self.pending.partition(b'\n')
        self.pending = bytearray(rest)
        return line

    def drain(self, timeout=5):
        chunks = []
        if self.pending:
            chunks.append(bytes(self.pending))
            self.pending.clear()
        end = time.monotonic() + timeout
        error = None
        while self.terminal is None:
            remaining = end - time.monotonic()
            if remaining <= 0 or not select.select([self.peer], [], [], remaining)[0]:
                error = 'timeout-before-terminal-read'
                break
            try:
                data = self.peer.recv(65536)
            except ConnectionResetError:
                self.terminal = 'reset'
                break
            except OSError as exc:
                error = f'{type(exc).__name__}: {exc}'
                break
            if not data:
                self.terminal = 'eof'
                break
            self.received_bytes += len(data)
            chunks.append(data)
        result = {'channel': self.label, 'terminal': self.terminal,
                  'drain_hex': b''.join(chunks).hex(), 'drain_bytes': sum(map(len, chunks)),
                  'total_received_bytes': self.received_bytes, 'error': error}
        self.record({'drain': result})
        return result


class Rig:
    def __init__(self, args, directory, *, reference=None, incoming=False,
                 expect_realize_failure=False, accel='qtest'):
        assert reference in (None, False, True)
        assert accel in ('qtest', 'tcg')
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sockets = Path(tempfile.mkdtemp(prefix='qcb-'))
        self.process = None
        self.peers = {}
        self.closed = False
        self.expected_failure = expect_realize_failure
        self.expected_exit = 1 if expect_realize_failure else 0
        self.request_id = 0
        self.count = dict(qom_get=0, qom_set=0, qtest=0, memory=0,
                          device_mmio=0, ram=0, cont=0)
        self.events, self.irq_events, self.waits = [], [], []
        self.irq = {0: False}
        self.intercepted = False
        self.logs = {name: (self.directory / name).open('wb')
                     for name in ('qemu.stdout', 'qemu.stderr')}
        self.protocol = (self.directory / 'protocol.jsonl').open('w')
        command = [str(Path(args.qemu).resolve()), '-machine', args.machine,
                   '-accel', accel, '-m', '128M', '-smp', '8', '-S',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null',
                   '-serial', 'null', '-d', 'unimp,guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.sockets}/m,server=on,wait=off']
        if reference is not None:
            command += ['-global', TYPE + '.enabled-reference=' +
                        ('on' if reference else 'off')]
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
            if expect_realize_failure:
                self.wait_exit(15)
                return
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

    def record(self, value):
        self.protocol.write(json.dumps(value, sort_keys=True) + '\n')
        self.protocol.flush()

    def connect(self, leaf, label):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            if self.process.poll() is not None:
                raise RuntimeError(f'Child exited before {label}: {self.process.returncode}')
            peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                peer.connect(str(self.sockets / leaf))
                return Lines(peer, self.record, label)
            except (FileNotFoundError, ConnectionRefusedError):
                peer.close()
                time.sleep(0.01)
        raise TimeoutError(f'Child {label} socket not ready')

    def raw_monitor(self, execute, arguments=None):
        self.request_id += 1
        request = {'execute': execute, 'id': self.request_id}
        if arguments is not None:
            request['arguments'] = arguments
        if execute in ('qom-get', 'qom-set', 'cont'):
            self.count[{'qom-get': 'qom_get', 'qom-set': 'qom_set', 'cont': 'cont'}[execute]] += 1
        self.record({'qmp_send': request})
        peer = self.peers['qmp']
        peer.send((json.dumps(request) + '\n').encode())
        while True:
            response = json.loads(peer.receive())
            self.record({'qmp_receive': response})
            if 'event' in response:
                self.events.append(response)
                continue
            assert response.get('id') == self.request_id, response
            return response

    def monitor(self, execute, arguments=None):
        response = self.raw_monitor(execute, arguments)
        assert 'error' not in response, response
        return response.get('return')

    def qom(self, prop):
        return self.monitor('qom-get', {'path': PATH, 'property': prop})

    def qt(self, command):
        fields = command.split()
        self.count['qtest'] += 1
        if fields[0] in ('readb', 'readw', 'readl', 'readq', 'read',
                         'writeb', 'writew', 'writel', 'writeq', 'write'):
            address = int(fields[1], 0)
            self.count['memory'] += 1
            if BASE <= address < BASE + SIZE:
                self.count['device_mmio'] += 1
            if 0x80000000 <= address < 0x88000000:
                self.count['ram'] += 1
        self.record({'qtest_send': command})
        peer = self.peers['qtest']
        peer.send((command + '\n').encode())
        while True:
            line = peer.receive().decode().strip()
            self.record({'qtest_receive': line})
            if line.startswith('IRQ '):
                _, action, number = line.split()
                self.irq[int(number)] = action == 'raise'
                self.irq_events.append({'action': action, 'number': int(number)})
                continue
            assert line.startswith('OK'), (command, line)
            return line.split()[1:]

    def read(self, offset, width='l'):
        return int(self.qt(f'read{width} {BASE + offset:#x}')[0], 0)

    def write(self, offset, value, width='l'):
        self.qt(f'write{width} {BASE + offset:#x} {value:#x}')

    def read_ram(self, address, size):
        raw = self.qt(f'read {address:#x} {size:#x}')[0]
        assert raw.startswith('0x'), raw
        result = bytes.fromhex(raw[2:])
        assert len(result) == size
        return result

    def write_ram(self, address, data):
        self.qt(f'write {address:#x} {len(data):#x} 0x{data.hex()}')

    def intercept(self):
        assert not self.intercepted
        self.qt('irq_intercept_out ' + PATH + ' sysbus-irq')
        self.intercepted = True

    def irq_fence(self):
        assert self.qt('endianness') == ['little']
        assert self.qom('irq-level') == 0 and not self.irq[0]
        assert not any(x['action'] == 'raise' for x in self.irq_events)

    def stderr(self):
        return (self.directory / 'qemu.stderr').read_text(errors='replace')

    def migrate_wait(self, timeout=30):
        end = time.monotonic() + timeout
        result = None
        while time.monotonic() < end:
            result = self.monitor('query-migrate')
            if result.get('status') == 'completed':
                return result
            assert result.get('status') not in ('failed', 'cancelled'), result
            time.sleep(0.01)
        raise TimeoutError(f'Migration did not complete: {result}')

    def wait_exit(self, timeout=10):
        # poll() alone is not used as the cleanup proof.
        result = self.process.wait(timeout=timeout)
        self.waits.append({'called': 'Popen.wait', 'returned_exit_code': result,
                           'timeout_seconds': timeout})
        self.record({'actual_wait': self.waits[-1]})
        return result

    def close(self):
        if self.closed:
            return
        self.closed = True
        result = {'quit_sent': False, 'quit_acknowledged': False, 'quit_error': None,
                  'quit_not_applicable': None, 'forced_terminate': False,
                  'forced_kill': False, 'drains': {}, 'expected_exit_code': self.expected_exit,
                  'signals_to_preexisting_processes': False}
        p = self.process
        if p is not None:
            if p.poll() is None and 'qmp' in self.peers:
                result['quit_sent'] = True
                try:
                    self.monitor('quit')
                    result['quit_acknowledged'] = True
                except (OSError, EOFError, ValueError, AssertionError, TimeoutError) as exc:
                    result['quit_error'] = f'{type(exc).__name__}: {exc}'
            else:
                result['quit_not_applicable'] = ('already-exited' if p.poll() is not None
                                                  else 'qmp-unavailable-startup-failure')
            try:
                self.wait_exit(10)
            except subprocess.TimeoutExpired:
                # Only this newly-created child can be signalled. This cleanup
                # path is an acceptance failure, never a successful test retry.
                result['forced_terminate'] = True
                p.terminate()
                try:
                    self.wait_exit(5)
                except subprocess.TimeoutExpired:
                    result['forced_kill'] = True
                    p.kill()
                    self.wait_exit(5)
            result['pid'] = p.pid
            result['exit_code'] = p.returncode
            result['wait_calls'] = list(self.waits)
            result['actually_waited_and_reaped'] = bool(self.waits) and p.returncode is not None
        else:
            result['exit_code'] = None
            result['wait_calls'] = []
            result['actually_waited_and_reaped'] = False
        for label, peer in self.peers.items():
            result['drains'][label] = peer.drain()
            peer.peer.close()
        result['unexpected_qtest_drain_lines'] = []
        if 'qtest' in self.peers:
            drained = bytes.fromhex(result['drains']['qtest']['drain_hex'])
            for line in drained.decode(errors='replace').splitlines():
                fields = line.split()
                if len(fields) == 3 and fields[0] == 'IRQ' and fields[1] in ('raise', 'lower'):
                    self.irq_events.append({'action': fields[1], 'number': int(fields[2])})
                else:
                    result['unexpected_qtest_drain_lines'].append(line)
        for label in ('qmp', 'qtest'):
            if label not in result['drains']:
                result['drains'][label] = {'channel': label, 'not_opened': True,
                                            'terminal': None, 'drain_bytes': 0,
                                            'reason': 'not-connected-before-child-exit-or-tcg'}
        result['counters'] = dict(self.count)
        result['irq_events'] = list(self.irq_events)
        result['stdio'] = {}
        for name, stream in self.logs.items():
            stream.close()
            path = self.directory / name
            result['stdio'][name] = {'bytes': path.stat().st_size, 'sha256': sha(path),
                                     'complete_after_actual_wait': bool(self.waits)}
        self.record({'cleanup': result})
        self.protocol.close()
        put_json(self.directory / 'cleanup.json', result)
        shutil.rmtree(self.sockets)


def verify_cleanup(out):
    records = []
    for path in sorted(Path(out).glob('*/cleanup.json')):
        value = json.loads(path.read_text())
        assert value['actually_waited_and_reaped'], value
        assert value['wait_calls'] and value['exit_code'] == value['expected_exit_code'], value
        assert not value['forced_terminate'] and not value['forced_kill'], value
        assert value['signals_to_preexisting_processes'] is False, value
        assert value['unexpected_qtest_drain_lines'] == [], value
        assert not any(event['action'] == 'raise' for event in value['irq_events']), value
        if value['quit_sent']:
            assert value['quit_acknowledged'] and value['quit_error'] is None, value
        else:
            assert value['quit_not_applicable'] == 'already-exited', value
        for channel in value['drains'].values():
            if not channel.get('not_opened'):
                assert channel['error'] is None, value
                assert channel['terminal'] in ('eof', 'reset'), value
                if value['expected_exit_code'] == 0:
                    assert channel['terminal'] == 'eof', value
        records.append({'fixture': path.parent.name, **value})
    assert records, 'No actual QEMU child cleanup records'
    return records
