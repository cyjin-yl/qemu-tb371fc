#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the compiled GENI device through actual QEMU QTest + chardev I/O.

No tablet access. No mocked implementation. Python standard library only.
The default QOM path is the TB371FC board's stable /machine/uart child.
"""
import argparse
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import tempfile
import time
import traceback

if not __debug__:
    raise SystemExit("These acceptance checks require Python assertions; do not use -O.")

STATUS = 0x40
FW_REV = 0x68
DMA = 0x258
TX_LEN = 0x270
M_CMD, M_CTRL, M_STATUS, M_EN, M_CLEAR = 0x600, 0x604, 0x610, 0x614, 0x618
M_EN_SET, M_EN_CLEAR = 0x61c, 0x620
S_CMD, S_CTRL, S_STATUS, S_EN, S_CLEAR = 0x630, 0x634, 0x640, 0x644, 0x648
S_EN_SET, S_EN_CLEAR = 0x64c, 0x650
TX_FIFO, RX_FIFO = 0x700, 0x780
TX_STATUS, RX_STATUS = 0x800, 0x804
TX_WM, RX_WM, GLOBAL_EN = 0x80c, 0x810, 0xe1c
DONE, OVERRUN, ILLEGAL, FAILURE, CANCEL, ABORT = (1 << i for i in range(6))
RX_READ_ERR, RX_WATERMARK, RX_LAST, TX_WRITE_ERR, TX_WATERMARK = (
    1 << i for i in (24, 26, 27, 29, 30)
)
START = 1 << 27


def connect_unix(path, process, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited early: {process.returncode}")
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect(str(path))
            return s
        except (FileNotFoundError, ConnectionRefusedError):
            s.close()
            time.sleep(0.01)
    raise TimeoutError(f"QEMU socket unavailable: {path}")


class Rig:
    def __init__(self, args, directory, incoming=False):
        self.args = args
        self.directory = Path(directory)
        self.log_path = self.directory / 'qemu.stderr'
        self.log = self.log_path.open('wb')
        command = [
            args.qemu, '-machine', 'tb371fc', '-accel', 'qtest',
            '-m', '128M', '-display', 'none', '-monitor', 'none',
            '-qtest', f'unix:{directory}/qtest,server=on,wait=off',
            '-qtest-log', '/dev/null',
            '-qmp', f'unix:{directory}/qmp,server=on,wait=off',
            '-chardev', f'socket,id=testserial,path={directory}/serial,server=on,wait=off',
            '-serial', 'chardev:testserial', '-S',
        ]
        if incoming:
            command.extend(['-incoming', 'defer'])
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        self.connections = []
        try:
            self.qtest = connect_unix(self.directory / 'qtest', self.process)
            self.connections.append(self.qtest)
            self.qfile = self.qtest.makefile('rwb', buffering=0)
            self.qmp = connect_unix(self.directory / 'qmp', self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile('rwb', buffering=0)
            self.serial = connect_unix(self.directory / 'serial', self.process)
            self.connections.append(self.serial)
            self.events = []
            self.irq = {0: False}
            greeting = json.loads(self.qmpfile.readline())
            assert 'QMP' in greeting, greeting
            self.monitor('qmp_capabilities')
            self.command(f'irq_intercept_out {args.qom_path} sysbus-irq')
        except BaseException:
            self.close()
            raise

    def command(self, command):
        self.qfile.write((command + '\n').encode())
        while True:
            raw = self.qfile.readline()
            if not raw:
                raise RuntimeError('QTest connection closed')
            line = raw.decode().strip()
            if line.startswith('IRQ '):
                _, state, number = line.split()
                self.irq[int(number)] = state == 'raise'
                continue
            if not line.startswith('OK'):
                raise AssertionError(f'{command}: {line}')
            return line.split()[1:]

    def monitor(self, execute, arguments=None):
        request = {'execute': execute}
        if arguments is not None:
            request['arguments'] = arguments
        self.qmpfile.write((json.dumps(request) + '\n').encode())
        while True:
            raw = self.qmpfile.readline()
            if not raw:
                raise RuntimeError('QMP connection closed')
            response = json.loads(raw)
            if 'event' in response:
                self.events.append(response)
            else:
                if 'error' in response:
                    raise AssertionError(response)
                return response.get('return')

    def read(self, offset):
        return int(self.command(f'readl {self.args.base + offset:#x}')[0], 0)

    def write(self, offset, value):
        self.command(f'writel {self.args.base + offset:#x} {value:#x}')

    def assert_irq(self, level):
        self.read(STATUS)  # Drain asynchronous IRQ observations before assertion.
        assert self.irq[0] == level, (self.irq, level)

    def wait_for(self, predicate, description, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.005)
        raise AssertionError(f'Timed out: {description}')

    def reset(self):
        self.monitor('system_reset')
        self.wait_for(lambda: self.read(STATUS) == 0 and
                      self.read(M_EN) == 0 and self.read(GLOBAL_EN) == 0,
                      'system reset completion')
        self.assert_irq(False)
        while select.select([self.serial], [], [], 0)[0]:
            if not self.serial.recv(65536):
                break

    def serial_read(self, count):
        result = bytearray()
        while len(result) < count:
            part = self.serial.recv(count - len(result))
            if not part:
                raise AssertionError('Serial backend closed')
            result.extend(part)
        return bytes(result)

    def tx(self, data):
        self.write(TX_LEN, len(data))
        self.write(M_CMD, START)
        for pos in range(0, len(data), 4):
            self.write(TX_FIFO, int.from_bytes(data[pos:pos + 4], 'little'))

    def close(self):
        for name in ('qfile', 'qmpfile'):
            stream = getattr(self, name, None)
            if stream:
                stream.close()
        for conn in getattr(self, 'connections', []):
            conn.close()
        if getattr(self, 'process', None) and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if getattr(self, 'log', None):
            self.log.close()


def identity(r):
    assert r.read(FW_REV) & 0xff00 == 0x200
    assert r.read(0xe24) == 0x20100000
    assert r.read(0xe28) == 0x20100000
    assert r.read(STATUS) == r.read(TX_STATUS) == r.read(RX_STATUS) == 0


def exact_tx_and_padding(r):
    r.write(TX_LEN, 5)
    r.write(M_CMD, START)
    r.write(TX_FIFO, 0x44434241)
    assert r.read(STATUS) & 1
    r.write(TX_FIFO, 0x58585845)
    assert r.serial_read(5) == b'ABCDE'
    assert r.read(M_STATUS) & DONE
    assert not r.read(STATUS) & 1
    assert r.read(0x910) == 5
    assert not select.select([r.serial], [], [], 0.02)[0], 'TX emitted padding'


def latched_masked_w1c_irq(r):
    r.write(GLOBAL_EN, 4)
    r.tx(b'A')
    assert r.serial_read(1) == b'A'
    assert r.read(M_STATUS) & DONE
    r.assert_irq(False)
    r.write(M_EN, DONE)
    r.assert_irq(True)
    r.write(M_CLEAR, CANCEL)
    r.assert_irq(True)
    r.write(M_CLEAR, DONE)
    r.assert_irq(False)
    assert not r.read(M_STATUS) & DONE


def global_gate(r):
    r.write(M_EN, DONE)
    r.tx(b'G')
    assert r.serial_read(1) == b'G'
    r.assert_irq(False)
    r.write(GLOBAL_EN, 4)
    r.assert_irq(True)
    r.write(GLOBAL_EN, 0)
    r.assert_irq(False)
    assert r.read(M_STATUS) & DONE


def watermark_reassertion(r):
    r.write(GLOBAL_EN, 4)
    r.write(M_EN, TX_WATERMARK)
    r.write(TX_WM, 2)
    r.write(TX_LEN, 8)
    r.write(M_CMD, START)
    r.assert_irq(True)
    r.write(M_CLEAR, TX_WATERMARK)
    r.assert_irq(True)
    r.write(TX_FIFO, 0x44434241)
    r.write(TX_FIFO, 0x48474645)
    assert r.serial_read(8) == b'ABCDEFGH'
    r.assert_irq(False)
    assert r.read(M_STATUS) & DONE


def enable_aliases(r):
    r.write(M_EN, DONE)
    r.write(M_EN_SET, CANCEL)
    assert r.read(M_EN) == DONE | CANCEL
    r.write(M_EN_CLEAR, DONE)
    assert r.read(M_EN) == CANCEL
    r.write(S_EN_SET, RX_LAST | RX_READ_ERR)
    r.write(S_EN_CLEAR, RX_LAST)
    assert r.read(S_EN) == RX_READ_ERR


def cancel_abort_overrun(r):
    r.write(TX_LEN, 8)
    r.write(M_CMD, START)
    r.write(M_CMD, START)
    assert r.read(M_STATUS) & OVERRUN
    r.write(TX_FIFO, 0x41544144)
    assert r.serial_read(4) == b'DATA'
    r.write(M_CTRL, 4)
    assert not r.read(STATUS) & 1
    assert r.read(M_STATUS) & (DONE | CANCEL) == DONE | CANCEL
    assert r.read(M_CTRL) == 0
    r.write(M_CLEAR, 0xffffffff)
    r.write(M_CMD, START)
    r.write(M_CTRL, 2)
    assert r.read(M_STATUS) & (DONE | ABORT) == DONE | ABORT
    assert r.read(M_CTRL) == r.read(TX_STATUS) == 0


def rx_partial_word_irq(r):
    r.write(GLOBAL_EN, 8)
    r.write(S_EN, RX_LAST)
    r.write(RX_WM, 2)
    r.write(S_CMD, START)
    r.serial.sendall(b'abcdefg')
    r.wait_for(lambda: r.read(RX_STATUS) & 0x1ffffff == 2, '7 RX bytes')
    assert r.read(RX_STATUS) == 0xb0000002
    r.assert_irq(True)
    assert r.read(RX_FIFO) == 0x64636261
    assert r.read(RX_STATUS) == 0xb0000001
    assert r.read(RX_FIFO) == 0x00676665
    assert r.read(RX_STATUS) == 0
    r.write(S_CLEAR, RX_LAST)
    r.write(M_CLEAR, RX_LAST)
    r.assert_irq(False)
    assert r.read(0x914) == 7


def rx_underflow(r):
    r.write(GLOBAL_EN, 8)
    r.write(S_EN, RX_READ_ERR)
    assert r.read(RX_FIFO) == 0
    assert r.read(S_STATUS) & RX_READ_ERR
    r.assert_irq(True)
    r.write(S_CLEAR, RX_READ_ERR)
    r.assert_irq(False)


def rx_backpressure(r):
    r.write(S_CMD, START)
    data = bytes(range(80))
    r.serial.sendall(data)
    r.wait_for(lambda: r.read(RX_STATUS) & 0x1ffffff == 16, 'RX FIFO full')
    observed = bytearray()
    for _ in range(20):
        r.wait_for(lambda: bool(r.read(RX_STATUS) & 0x1ffffff), 'RX FIFO data')
        observed.extend(r.read(RX_FIFO).to_bytes(4, 'little'))
    assert bytes(observed) == data
    assert r.read(RX_STATUS) == 0


def rx_abort(r):
    r.write(S_CMD, START)
    r.serial.sendall(b'xyz')
    r.wait_for(lambda: r.read(RX_STATUS) & 0x1ffffff == 1, 'RX before abort')
    r.write(S_CTRL, 2)
    assert r.read(S_CTRL) == r.read(RX_STATUS) == 0
    assert not r.read(STATUS) & (1 << 12)
    assert r.read(S_STATUS) & (ABORT | DONE) == ABORT | DONE
    assert not r.read(S_STATUS) & RX_LAST


def unsupported_commands(r):
    assert r.read(0xc40) == r.read(0xd40) == 0
    r.write(DMA, 1)
    r.write(TX_LEN, 1)
    r.write(M_CMD, START)
    assert r.read(DMA) == 1
    assert r.read(M_STATUS) & FAILURE
    assert not r.read(M_STATUS) & DONE
    assert not r.read(STATUS) & 1
    r.write(TX_FIFO, 65)
    assert r.read(M_STATUS) & TX_WRITE_ERR
    assert not select.select([r.serial], [], [], 0.02)[0]
    r.write(DMA, 0)
    r.write(M_CMD, 2 << 27)
    r.write(S_CMD, 2 << 27)
    assert r.read(M_STATUS) & ILLEGAL
    assert r.read(S_STATUS) & ILLEGAL


def reset_clears_state(r):
    r.write(GLOBAL_EN, 12)
    r.write(M_EN, TX_WATERMARK)
    r.write(TX_WM, 2)
    r.write(TX_LEN, 7)
    r.write(M_CMD, START)
    r.write(S_CMD, START)
    r.serial.sendall(b'RST')
    r.wait_for(lambda: bool(r.read(RX_STATUS)), 'data before reset')
    r.assert_irq(True)
    r.reset()
    for register in (STATUS, M_STATUS, S_STATUS, M_EN, S_EN,
                     GLOBAL_EN, TX_STATUS, RX_STATUS, TX_LEN, 0x910, 0x914):
        assert r.read(register) == 0, (hex(register), hex(r.read(register)))


TESTS = [identity, exact_tx_and_padding, latched_masked_w1c_irq, global_gate,
         watermark_reassertion, enable_aliases, cancel_abort_overrun,
         rx_partial_word_irq, rx_underflow, rx_backpressure, rx_abort,
         unsupported_commands, reset_clears_state]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--base', type=lambda s: int(s, 0), default=0xa90000)
    parser.add_argument('--qom-path', default='/machine/uart')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    results = []
    with tempfile.TemporaryDirectory(prefix='tb371fc-geni-') as directory:
        rig = None
        try:
            rig = Rig(args, directory)
            for test in TESTS:
                started = time.monotonic()
                try:
                    rig.reset()
                    test(rig)
                    results.append({'test': test.__name__, 'status': 'pass',
                                    'seconds': round(time.monotonic() - started, 4)})
                except Exception:
                    results.append({'test': test.__name__, 'status': 'fail',
                                    'error': traceback.format_exc()})
                    break
        except Exception:
            results.append({'test': 'qemu_startup', 'status': 'fail',
                            'error': traceback.format_exc()})
        finally:
            if rig:
                rig.close()
        success = len(results) == len(TESTS) and all(r['status'] == 'pass' for r in results)
        report = {'schema_version': 1, 'suite': 'GENI actual QTest/chardev',
                  'success': success, 'qemu': args.qemu,
                  'checks': results, 'expected_checks': len(TESTS), 'test_ram': '128 MiB',
                  'scope': 'FIFO behavioral model; no silicon timing or DMA validation'}
        if not success:
            log = Path(directory, 'qemu.stderr')
            report['qemu_stderr_tail'] = log.read_text(errors='replace')[-8192:] if log.exists() else ''
        print(json.dumps(report, indent=2))
        return 0 if success else 1


if __name__ == '__main__':
    raise SystemExit(main())
