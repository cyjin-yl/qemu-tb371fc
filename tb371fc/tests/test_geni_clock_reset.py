#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check real GCC -> GENI clock/reset wiring, FIFO pause and migration.

Uses the board's explicit firmware-handoff state, including its documented
100 MHz bootstrap AHB assumption. This does not measure silicon timing.
Logs and intermediate migration evidence are retained in --work-dir.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import select
import tempfile
import time
import traceback

from test_geni_uart import (Rig, STATUS, TX_LEN, M_CMD, M_STATUS, M_EN,
                           M_CLEAR, M_CTRL, S_CMD, S_STATUS, S_EN, S_CLEAR,
                           TX_FIFO, RX_FIFO, TX_STATUS, RX_STATUS, TX_WM,
                           GLOBAL_EN, START, DONE, RX_LAST, TX_WATERMARK)
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit('Run these checks without Python -O.')

VOTE, BCR = 0x100000 + 0x52010, 0x100000 + 0x1e000
SE, M_AHB, S_AHB = 1 << 8, 1 << 2, 1 << 1
PERIOD_UNIT = 1_000_000_000 << 32


class WiredRig(Rig):
    def gcc_read(self, address):
        return int(self.command(f'readl {address:#x}')[0], 0)

    def gcc_write(self, address, value):
        self.command(f'writel {address:#x} {value:#x}')

    def gate(self, mask, enable):
        value = self.gcc_read(VOTE)
        self.gcc_write(VOTE, value | mask if enable else value & ~mask)

    def period(self, name):
        return self.monitor('qom-get', {'path': self.args.qom_path + '/' + name,
                                       'property': 'qtest-clock-period'})

    def expect_no_serial(self):
        assert not select.select([self.serial], [], [], 0.03)[0], 'UART emitted bytes while paused/reset'


def handoff_clocks_deliver_console(r):
    assert r.period('se-clk') == PERIOD_UNIT // 7_372_800
    assert r.period('m-ahb') == r.period('s-ahb') == PERIOD_UNIT // 100_000_000
    r.tx(b'GCC-wired')
    assert r.serial_read(9) == b'GCC-wired'
    assert r.read(M_STATUS) & DONE


def serial_engine_pause_preserves_fifo_and_pending_irq(r):
    r.write(GLOBAL_EN, 4)
    r.write(M_EN, DONE | TX_WATERMARK)
    r.write(TX_WM, 2)
    r.write(TX_LEN, 5)
    r.write(M_CMD, START)
    r.assert_irq(True)
    r.gate(SE, False)
    assert r.period('se-clk') == 0
    r.assert_irq(False)
    r.write(TX_FIFO, 0x44434241)
    r.write(TX_FIFO, 0x45)
    assert r.read(STATUS) & 1
    assert r.read(TX_STATUS) == 2
    assert r.read(0x910) == 0
    assert not r.read(M_STATUS) & DONE
    r.expect_no_serial()
    r.gate(SE, True)
    assert r.serial_read(5) == b'ABCDE'
    assert r.read(0x910) == 5
    assert r.read(M_STATUS) & DONE
    r.assert_irq(True)
    r.gate(SE, False)
    r.assert_irq(False)
    assert r.read(M_STATUS) & DONE
    r.gate(SE, True)
    r.assert_irq(True)
    r.write(M_CLEAR, DONE)
    assert not r.read(M_STATUS) & DONE
    r.assert_irq(True)  # The idle FIFO still satisfies its watermark.
    r.write(TX_WM, 0)
    r.write(M_CLEAR, TX_WATERMARK)
    r.assert_irq(False)


def rx_backpressure_while_clock_stopped(r):
    r.write(GLOBAL_EN, 8)
    r.write(S_EN, RX_LAST)
    r.write(S_CMD, START)
    r.gate(SE, False)
    r.serial.sendall(b'xyz')
    time.sleep(0.03)
    assert r.read(RX_STATUS) == 0
    assert r.read(0x914) == 0
    r.assert_irq(False)
    r.gate(SE, True)
    r.wait_for(lambda: r.read(RX_STATUS) == 0xb0000001, 'RX after clock restore')
    r.assert_irq(True)
    assert r.read(RX_FIFO) == 0x007a7978
    r.write(S_CLEAR, RX_LAST)
    r.write(M_CLEAR, RX_LAST)
    r.assert_irq(False)


def ahb_gates_reject_mmio_without_mutation(r):
    for mask, name in [(M_AHB, 'm-ahb'), (S_AHB, 's-ahb')]:
        r.write(TX_LEN, 0x1234)
        r.gate(mask, False)
        assert r.period(name) == 0
        r.write(TX_LEN, 0x5678)
        assert r.read(TX_LEN) == 0  # QTest does not expose MemTxResult.
        r.gate(mask, True)
        assert r.read(TX_LEN) == 0x1234
    log = r.log_path.read_text(errors='replace')
    assert 'MMIO write while AHB is gated or reset is asserted' in log, log
    assert 'MMIO read while AHB is gated or reset is asserted' in log, log


def wrapper_bcr_flushes_fifo_and_holds_reset(r):
    r.write(GLOBAL_EN, 12)
    r.write(M_EN, DONE | TX_WATERMARK)
    r.write(S_EN, RX_LAST)
    r.write(TX_WM, 2)
    r.write(TX_LEN, 8)
    r.write(M_CMD, START)
    r.write(TX_FIFO, 0x41544144)
    assert r.serial_read(4) == b'DATA'
    r.write(S_CMD, START)
    r.serial.sendall(b'xyz')
    r.wait_for(lambda: bool(r.read(RX_STATUS)), 'RX before wrapper reset')
    r.gate(SE, False)
    r.write(TX_FIFO, 0x48474645)
    assert r.read(TX_STATUS) == 1
    r.gcc_write(BCR, 1)
    r.assert_irq(False)
    # Held reset rejects guest writes and cannot restart the serial engine.
    r.write(TX_LEN, 1)
    r.write(M_CMD, START)
    r.write(TX_FIFO, ord('Z'))
    r.expect_no_serial()
    r.gcc_write(BCR, 0)
    r.gate(SE, True)
    for register in (STATUS, TX_LEN, M_STATUS, S_STATUS, M_EN, S_EN,
                     GLOBAL_EN, TX_STATUS, RX_STATUS, 0x910, 0x914):
        assert r.read(register) == 0, (hex(register), r.read(register))
    r.expect_no_serial()
    r.tx(b'OK')
    assert r.serial_read(2) == b'OK'


def zero_length_completion_waits_for_engine_clock(r):
    r.write(GLOBAL_EN, 4)
    r.write(M_EN, DONE)
    r.gate(SE, False)
    r.write(TX_LEN, 0)
    r.write(M_CMD, START)
    assert r.read(STATUS) & 1
    assert not r.read(M_STATUS) & DONE
    r.assert_irq(False)
    r.gate(SE, True)
    assert not r.read(STATUS) & 1
    assert r.read(M_STATUS) & DONE
    r.assert_irq(True)
    r.expect_no_serial()


TESTS = [handoff_clocks_deliver_console,
         serial_engine_pause_preserves_fifo_and_pending_irq,
         rx_backpressure_while_clock_stopped,
         ahb_gates_reject_mmio_without_mutation,
         wrapper_bcr_flushes_fifo_and_holds_reset,
         zero_length_completion_waits_for_engine_clock]


def migration(args, directory):
    source = target = None
    checks = []
    try:
        srcdir, dstdir = directory / 'source', directory / 'target'
        srcdir.mkdir(parents=True, exist_ok=True)
        dstdir.mkdir(parents=True, exist_ok=True)
        source = WiredRig(args, str(srcdir))
        target = WiredRig(args, str(dstdir), incoming=True)
        source.write(GLOBAL_EN, 12)
        source.write(M_EN, DONE | RX_LAST)
        source.write(S_EN, RX_LAST)
        source.write(TX_LEN, 8)
        source.write(M_CMD, START)
        source.write(TX_FIFO, 0x44434241)
        assert source.serial_read(4) == b'ABCD'
        source.write(S_CMD, START)
        source.serial.sendall(b'xyz')
        source.wait_for(lambda: bool(source.read(RX_STATUS)), 'RX before paused migration')
        source.gate(SE, False)
        source.write(TX_FIFO, 0x48474645)
        assert source.read(TX_STATUS) == 1
        source.assert_irq(False)
        source.expect_no_serial()
        registers = (STATUS, TX_LEN, TX_STATUS, RX_STATUS, M_STATUS, S_STATUS,
                     M_EN, S_EN, GLOBAL_EN, 0x910, 0x914)
        before = {hex(reg): source.read(reg) for reg in registers}
        checks.append({'check': 'source_queued_tx_rx_and_clock_gated_irq', 'status': 'pass'})
        uri = f'unix:{directory}/migration.sock'
        target.monitor('migrate-incoming', {'uri': uri})
        source.monitor('migrate', {'uri': uri})
        migrations = {'source': wait_migration(source), 'target': wait_migration(target)}
        assert source.monitor('query-status')['status'] == 'postmigrate'
        assert not target.monitor('query-status')['running']
        checks.append({'check': 'actual_qmp_migration_completed', 'status': 'pass'})
        after = {hex(reg): target.read(reg) for reg in registers}
        assert before == after, {'before': before, 'after': after}
        assert target.period('se-clk') == 0
        assert not target.gcc_read(VOTE) & SE
        target.assert_irq(False)
        target.expect_no_serial()
        checks.append({'check': 'clock_gate_fifo_and_latched_status_preserved', 'status': 'pass'})
        target.gate(SE, True)
        assert target.serial_read(4) == b'EFGH'
        assert target.read(0x910) == 8
        assert target.read(RX_FIFO) == 0x007a7978
        assert target.read(0x914) == 3
        target.assert_irq(True)
        target.write(M_CLEAR, 0xffffffff)
        target.write(S_CLEAR, 0xffffffff)
        target.assert_irq(False)
        target.expect_no_serial()
        checks.append({'check': 'destination_clock_restore_continues_without_duplicate_bytes', 'status': 'pass'})
        return {'success': True, 'checks': checks, 'migration': migrations}
    except Exception:
        return {'success': False, 'checks': checks, 'error': traceback.format_exc()}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--qemu', required=True)
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0xa90000)
    ap.add_argument('--qom-path', default='/machine/uart')
    ap.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    ap.add_argument('--work-dir', type=Path)
    ap.add_argument('--migration-only', action='store_true')
    args = ap.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.extra_qemu_args = ['-d', 'guest_errors']
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='tb371fc-geni-clocks-'))
    directory.mkdir(parents=True, exist_ok=True)
    with open(args.qemu, 'rb') as binary:
        digest = hashlib.file_digest(binary, 'sha256').hexdigest()
    report = {'schema_version': 1, 'suite': 'GCC-wired GENI actual QTest',
              'qemu': args.qemu, 'machine': args.machine, 'qemu_sha256': digest, 'work_dir': str(directory),
              'ram_per_process': '128 MiB',
              'scope': 'Actual device clock/reset wiring and FIFO continuity; no silicon electrical or baud timing equivalence'}
    if args.migration_only:
        report.update(migration(args, directory))
        report['suite'] = 'GCC-wired GENI actual process-to-process migration'
        report['expected_checks'] = 4
    else:
        checks = []
        for test in TESTS:
            r = None
            path = directory / test.__name__
            path.mkdir(parents=True, exist_ok=True)
            try:
                r = WiredRig(args, str(path))
                test(r)
                checks.append({'test': test.__name__, 'status': 'pass'})
            except Exception:
                checks.append({'test': test.__name__, 'status': 'fail',
                               'error': traceback.format_exc()})
                break
            finally:
                if r:
                    r.close()
        report.update(checks=checks, expected_checks=len(TESTS),
                      success=len(checks) == len(TESTS) and all(c['status'] == 'pass' for c in checks))
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
