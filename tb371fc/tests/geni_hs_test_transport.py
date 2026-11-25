#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest/QMP/socket transport for independent GENI HS acceptance.

This helper contains no device-value oracle and does not import or parse the
implementation. All requests, replies, synthetic peer bytes and cleanup results
are retained. Importing it performs no QEMU or host/device operation.
"""
import errno
import hashlib
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import tempfile
import threading
import time

HS_BASE, OLD_BASE, WRAPPER, GCC, RAM = 0x998000, 0xa90000, 0x9c0004, 0x100000, 0x80010000


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def connect_unix(path, process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f'QEMU exited before socket connection: {process.returncode}')
        peer = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        peer.settimeout(5)
        try:
            peer.connect(str(path))
            return peer
        except (FileNotFoundError, ConnectionRefusedError):
            peer.close()
            time.sleep(0.01)
    raise TimeoutError(f'QEMU socket not available: {path}')


def wait_migration(rig, timeout=30):
    deadline = time.monotonic() + timeout
    state = None
    while time.monotonic() < deadline:
        state = rig.monitor('query-migrate')
        if state.get('status') == 'completed':
            return state
        if state.get('status') in ('failed', 'cancelled'):
            raise AssertionError(state)
        time.sleep(0.01)
    raise TimeoutError(f'Migration did not complete: {state}')


def file_channels(path):
    return {'channels': [{'channel-type': 'main', 'addr': {'transport': 'file',
                                                         'filename': str(Path(path).resolve())}}]}


class HsRig:
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel='qtest', hs_backend='connected', boot_configured=True,
                 auto_cont=True):
        assert reference in (True, False, None)
        assert hs_backend in ('connected', 'disconnected', 'absent')
        self.args, self.reference, self.incoming, self.accel = args, bool(reference), incoming, accel
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.socket_directory = Path(tempfile.mkdtemp(prefix='ghs-'))
        self.connections, self.backend = [], {}
        self.events, self.irq = [], {0: False}
        self.irq_intercepted = False
        self.irq_events = []
        self.qom_get_count = self.memory_count = self.device_mmio_count = self.ram_count = 0
        self.cont_count = self.qtest_count = self.backend_send_count = self.backend_recv_count = 0
        self.backend_sent_bytes = {'hs': 0, 'legacy': 0}
        self.backend_received_bytes = {'hs': 0, 'legacy': 0}
        self.backend_terminal_reads = {'hs': 0, 'legacy': 0}
        self.failure_observer = None
        self.last_clock, self.request_id = 0, 0
        self.closed = False
        self.log_path = self.directory / 'qemu.stderr'
        self.log = self.log_path.open('wb')
        self.qtlog = (self.directory / 'qtest.jsonl').open('w')
        self.qmplog = (self.directory / 'qmp.jsonl').open('w')
        self.peerlog = (self.directory / 'backend.jsonl').open('w')
        machine = [x for x in args.machine.split(',') if not x.startswith('gcc-boot-configured=')]
        machine.append('gcc-boot-configured=' + ('on' if boot_configured else 'off'))
        command = [args.qemu, '-machine', ','.join(machine), '-accel', accel,
                   '-m', '128M', '-smp', '8', '-display', 'none', '-monitor', 'none',
                   '-S', '-d', 'unimp,guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.socket_directory}/m,server=on,wait=off',
                   '-chardev', f'socket,id=legacy,path={self.socket_directory}/legacy,server=on,wait=off',
                   '-serial', 'chardev:legacy']
        if hs_backend == 'absent':
            command += ['-serial', 'none']
        else:
            command += ['-chardev', f'socket,id=hs,path={self.socket_directory}/hs,server=on,wait=off',
                        '-serial', 'chardev:hs']
        if reference is not None:
            command += ['-global', 'qcom-sm8250-geni-hs.enabled-reference=' +
                        ('on' if reference else 'off')]
        if accel == 'qtest':
            command += ['-qtest', f'unix:{self.socket_directory}/t,server=on,wait=off',
                        '-qtest-log', '/dev/null']
        if incoming:
            command += ['-incoming', 'defer']
        write_json(self.directory / 'command.json', command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qmp = connect_unix(self.socket_directory / 'm', self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile('rwb', buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self.record(self.qmplog, {'receive': greeting})
            assert 'QMP' in greeting
            self.monitor('qmp_capabilities')
            if accel == 'qtest':
                self.qtest = connect_unix(self.socket_directory / 't', self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile('rwb', buffering=0)
                self.command('irq_intercept_out /machine/uart-hs sysbus-irq')
                self.irq_intercepted = True
            self.connect_backend('legacy')
            if hs_backend == 'connected':
                self.connect_backend('hs')
            if accel == 'qtest' and not incoming and auto_cont:
                self.monitor('cont')
        except BaseException:
            self.close()
            raise

    @staticmethod
    def record(stream, value):
        stream.write(json.dumps(value, sort_keys=True) + '\n')
        stream.flush()

    def counters(self):
        return {'qom_get': self.qom_get_count, 'memory': self.memory_count,
                'device_mmio': self.device_mmio_count, 'ram': self.ram_count,
                'cont': self.cont_count, 'qtest': self.qtest_count,
                'backend_send_calls': self.backend_send_count,
                'backend_receive_calls': self.backend_recv_count,
                'backend_sent_bytes': dict(self.backend_sent_bytes),
                'backend_received_bytes': dict(self.backend_received_bytes),
                'backend_terminal_reads': dict(self.backend_terminal_reads),
                'virtual_ns': self.last_clock}

    def command(self, command):
        assert self.accel == 'qtest', 'TCG fixture has no QTest channel'
        fields = command.split()
        if fields and fields[0] in ('readb', 'readw', 'readl', 'readq',
                                  'writeb', 'writew', 'writel', 'writeq', 'read', 'write'):
            address = int(fields[1], 0)
            self.memory_count += 1
            if RAM <= address < RAM + 4:
                self.ram_count += 1
            if (HS_BASE <= address < HS_BASE + 0x4000 or
                    OLD_BASE <= address < OLD_BASE + 0x4000 or
                    WRAPPER <= address < WRAPPER + 4 or
                    GCC <= address < GCC + 0x1f0000):
                self.device_mmio_count += 1
        self.qtest_count += 1
        self.record(self.qtlog, {'send': command, 'virtual_ns_before': self.last_clock})
        self.qfile.write((command + '\n').encode())
        while True:
            raw = self.qfile.readline()
            if not raw:
                raise RuntimeError('QTest connection closed')
            line = raw.decode().strip()
            self.record(self.qtlog, {'receive': line})
            if line.startswith('IRQ '):
                _, level, number = line.split()
                self.irq[int(number)] = level == 'raise'
                self.irq_events.append({'level': level, 'number': int(number)})
                continue
            assert line.startswith('OK'), (command, line)
            return line.split()[1:]

    def raw_monitor(self, execute, arguments=None):
        self.request_id += 1
        request = {'execute': execute, 'id': self.request_id}
        if arguments is not None:
            request['arguments'] = arguments
        if execute == 'qom-get':
            self.qom_get_count += 1
        if execute == 'cont':
            self.cont_count += 1
        self.record(self.qmplog, {'send': request})
        self.qmpfile.write((json.dumps(request) + '\n').encode())
        while True:
            raw = self.qmpfile.readline()
            if not raw:
                raise RuntimeError('QMP connection closed')
            response = json.loads(raw)
            self.record(self.qmplog, {'receive': response})
            if 'event' in response:
                self.events.append(response)
                continue
            assert response.get('id') == self.request_id, response
            return response

    def monitor(self, execute, arguments=None):
        response = self.raw_monitor(execute, arguments)
        assert 'error' not in response, response
        return response.get('return')

    def qom(self, name, path='/machine/uart-hs'):
        return self.monitor('qom-get', {'path': path, 'property': name})

    def read(self, offset, *, engine='hs', width='l'):
        base = HS_BASE if engine == 'hs' else OLD_BASE
        return self.read_absolute(base + offset, width=width)

    def write(self, offset, value, *, engine='hs', width='l'):
        base = HS_BASE if engine == 'hs' else OLD_BASE
        return self.write_absolute(base + offset, value, width=width)

    def read_absolute(self, address, *, width='l'):
        response = self.command(f'read{width} {address:#x}')
        assert len(response) == 1
        return int(response[0], 0)

    def write_absolute(self, address, value, *, width='l'):
        self.command(f'write{width} {address:#x} {value:#x}')

    def set_clock(self, ns):
        actual = int(self.command(f'clock_set {ns}')[0], 0)
        assert actual == ns
        self.last_clock = actual

    def step(self, ns):
        actual = int(self.command(f'clock_step {ns}')[0], 0)
        assert actual == self.last_clock + ns
        self.last_clock = actual

    def connect_backend(self, name):
        assert name in ('hs', 'legacy') and name not in self.backend
        peer = connect_unix(self.socket_directory / name, self.process)
        self.connections.append(peer)
        self.backend[name] = peer
        self.record(self.peerlog, {'event': 'connected', 'engine': name})
        return peer

    def disconnect_backend(self, name):
        peer = self.backend.pop(name)
        peer.close()
        self.record(self.peerlog, {'event': 'disconnected', 'engine': name})

    def send(self, data, name='hs'):
        assert isinstance(data, bytes) and data
        self.backend[name].sendall(data)
        self.backend_send_count += 1
        self.backend_sent_bytes[name] += len(data)
        self.record(self.peerlog, {'send': data.hex(), 'bytes': len(data), 'engine': name})

    def receive(self, size, name='hs', timeout=5):
        assert size > 0
        end, received = time.monotonic() + timeout, bytearray()
        while len(received) < size:
            remaining = end - time.monotonic()
            assert remaining > 0, (name, size, len(received), 'backend timeout')
            ready, _, _ = select.select([self.backend[name]], [], [], remaining)
            assert ready, (name, size, len(received), 'backend timeout')
            piece = self.backend[name].recv(size - len(received))
            assert piece, (name, 'backend disconnected')
            received.extend(piece)
        data = bytes(received)
        self.backend_recv_count += 1
        self.backend_received_bytes[name] += len(data)
        self.record(self.peerlog, {'receive': data.hex(), 'bytes': len(data), 'engine': name})
        return data

    def no_output(self, name='hs', timeout=0.02):
        if name not in self.backend:
            return
        ready, _, _ = select.select([self.backend[name]], [], [], timeout)
        assert not ready, (name, 'unexpected backend output or disconnect')
        self.record(self.peerlog, {'event': 'no-output', 'engine': name,
                                  'timeout_seconds': timeout, 'consumed_bytes': 0})

    def observe_failed_load_output(self):
        assert self.failure_observer is None
        self.failure_observer = FailureOutputObserver(self)
        return self.failure_observer

    def pending_output(self, name='hs'):
        ready, _, _ = select.select([self.backend[name]], [], [], 0)
        return bool(ready)

    def until(self, predicate, description, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError('Timed out: ' + description)

    def logs(self):
        return self.log_path.read_text(errors='replace')

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.failure_observer is not None:
            self.failure_observer.abort_if_running()
        outcome = {'forced_terminate': False, 'forced_kill': False,
                   'quit_acknowledged': False, 'exit_code': None}
        process = getattr(self, 'process', None)
        if process is not None and process.poll() is None:
            try:
                if not hasattr(self,'qmpfile'):
                    raise RuntimeError('QMP was not connected during startup failure')
                self.monitor('quit')
                outcome['quit_acknowledged'] = True
                process.wait(timeout=5)
            except (OSError, AssertionError, RuntimeError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    outcome['forced_terminate'] = True
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        outcome['forced_kill'] = True
                        process.kill()
                        process.wait(timeout=5)
        if process is not None:
            outcome['exit_code'] = process.poll()
        for name in ('qfile', 'qmpfile'):
            item = getattr(self, name, None)
            if item:
                item.close()
        for item in self.connections:
            item.close()
        for name in ('log', 'qtlog', 'qmplog', 'peerlog'):
            item = getattr(self, name, None)
            if item:
                item.close()
        write_json(self.directory / 'cleanup.json', outcome)
        shutil.rmtree(self.socket_directory, ignore_errors=True)


class FailureOutputObserver:
    """Observe both real peer outputs from before incoming through termination.

    EOF and ECONNRESET are recorded separately. They are accepted only after the
    caller verifies exit1 and the specific migration error, with zero data over
    the entire observation interval. Every other socket error remains a failure.
    """
    def __init__(self, rig):
        self.rig = rig
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.records = {name: {'engine': name, 'recv_calls': 0, 'data_bytes': 0,
                               'chunks_hex': [], 'termination': None, 'errno': None}
                        for name in ('hs', 'legacy')}
        assert all(name in rig.backend for name in self.records)
        self.thread_error = None
        self.started_before_qmp_request_id = rig.request_id + 1
        rig.record(rig.peerlog, {'event': 'failure-observation-start',
                                'before_qmp_request_id': self.started_before_qmp_request_id,
                                'engines': ['hs', 'legacy']})
        self.thread = threading.Thread(target=self._observe, daemon=True)
        self.thread.start()
        assert self.ready.wait(timeout=5), 'Backend observer did not start'

    def _observe(self):
        active = {self.rig.backend[name]: name for name in self.records}
        self.ready.set()
        try:
            while active and not self.stop.is_set():
                ready, _, _ = select.select(list(active), [], [], 0.01)
                for peer in ready:
                    name = active[peer]
                    item = self.records[name]
                    item['recv_calls'] += 1
                    try:
                        data = peer.recv(65536)
                    except OSError as exc:
                        item['termination'] = 'reset' if exc.errno == errno.ECONNRESET else 'socket-error'
                        item['errno'] = exc.errno
                        item['error_type'] = type(exc).__name__
                        self.rig.backend_terminal_reads[name] += 1
                        del active[peer]
                        continue
                    if data:
                        item['data_bytes'] += len(data)
                        item['chunks_hex'].append(data.hex())
                        self.rig.backend_recv_count += 1
                        self.rig.backend_received_bytes[name] += len(data)
                        if item['data_bytes'] > 1 << 20:
                            raise AssertionError('Unexpected migration output exceeded 1 MiB evidence bound')
                    else:
                        item['termination'] = 'eof'
                        self.rig.backend_terminal_reads[name] += 1
                        del active[peer]
        except BaseException as exc:
            self.thread_error = {'type': type(exc).__name__, 'message': str(exc)}

    def finish(self, *, specific_failure_verified):
        assert specific_failure_verified is True
        assert self.rig.process.poll() == 1
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.stop.set()
            self.thread.join(timeout=1)
        result = {'started_before_qmp_request_id': self.started_before_qmp_request_id,
                  'observed_from_before_incoming': True,
                  'expected_process_exit_code': 1,
                  'actual_process_exit_code': self.rig.process.poll(),
                  'specific_migration_failure_verified': specific_failure_verified,
                  'observer_thread_finished': not self.thread.is_alive(),
                  'thread_error': self.thread_error,
                  'backends': [self.records[name] for name in ('hs', 'legacy')]}
        self.rig.record(self.rig.peerlog, {'event': 'failure-observation-complete', **result})
        write_json(self.rig.directory / 'backend-failure-observation.json', result)
        assert result['observer_thread_finished'] and self.thread_error is None, result
        for item in result['backends']:
            assert item['data_bytes'] == 0 and item['chunks_hex'] == [], result
            assert item['termination'] in ('eof', 'reset'), result
            assert item['errno'] == (errno.ECONNRESET if item['termination'] == 'reset' else None), result
            assert item['recv_calls'] >= 1
        return result

    def abort_if_running(self):
        if self.thread.is_alive():
            self.stop.set()
            self.thread.join(timeout=1)
            self.rig.record(self.rig.peerlog, {'event': 'failure-observation-aborted',
                            'thread_error': self.thread_error,
                            'backends': [self.records[name] for name in ('hs', 'legacy')]})
