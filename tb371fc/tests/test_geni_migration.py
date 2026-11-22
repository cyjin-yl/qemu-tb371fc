#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Transfer real pending GENI state between two QEMU QTest processes.

Uses 128 MiB per process (a board-supported device-test configuration) rather
than scanning the board's default 8 GiB. No tablet access and no firmware.
"""
import argparse
import json
import os
from pathlib import Path
import select
import tempfile
import time
import traceback

from test_geni_uart import (Rig, STATUS, TX_LEN, M_CMD, M_STATUS, M_EN,
                           M_CLEAR, S_CMD, S_STATUS, S_EN, S_CLEAR,
                           TX_FIFO, RX_FIFO, TX_STATUS, RX_STATUS,
                           TX_WM, RX_WM, GLOBAL_EN, START, DONE,
                           RX_LAST, TX_WATERMARK)

if not __debug__:
    raise SystemExit('These acceptance checks require Python assertions; do not use -O.')

STATE_REGISTERS = (STATUS, TX_LEN, M_STATUS, M_EN, S_STATUS, S_EN,
                   TX_STATUS, RX_STATUS, TX_WM, RX_WM, GLOBAL_EN, 0x910, 0x914)


def wait_migration(rig, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = rig.monitor('query-migrate')
        if state.get('status') == 'completed':
            return state
        if state.get('status') in ('failed', 'cancelled'):
            raise AssertionError(state)
        time.sleep(0.02)
    raise TimeoutError(f'Migration did not complete: {state}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--base', type=lambda s: int(s, 0), default=0xa90000)
    parser.add_argument('--qom-path', default='/machine/uart')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    checks = []
    report = {'schema_version': 1, 'suite': 'GENI QMP process-to-process migration',
              'qemu': args.qemu, 'test_ram_per_process': '128 MiB', 'checks': checks,
              'scope': 'Actual paused machine migration and GENI FIFO/IRQ continuation; no hardware timing equivalence'}
    with tempfile.TemporaryDirectory(prefix='tb371fc-migrate-') as directory:
        source_dir, target_dir = Path(directory, 'src'), Path(directory, 'dst')
        source_dir.mkdir()
        target_dir.mkdir()
        source = target = None
        try:
            source = Rig(args, str(source_dir))
            target = Rig(args, str(target_dir), incoming=True)
            source.reset()
            source.write(GLOBAL_EN, 12)
            source.write(M_EN, TX_WATERMARK | RX_LAST | DONE)
            source.write(S_EN, RX_LAST)
            source.write(TX_WM, 2)
            source.write(RX_WM, 2)
            source.write(TX_LEN, 8)
            source.write(M_CMD, START)
            source.write(TX_FIFO, 0x44434241)
            assert source.serial_read(4) == b'ABCD'
            source.write(S_CMD, START)
            source.serial.sendall(b'abcdefg')
            source.wait_for(lambda: source.read(RX_STATUS) == 0xb0000002,
                            'source RX partial word')
            source.assert_irq(True)
            before = {hex(reg): source.read(reg) for reg in STATE_REGISTERS}
            assert before[hex(STATUS)] == 0x1001
            assert before[hex(0x910)] == 4
            source.command('writel 0x80010000 0x67454d49')
            checks.append({'check': 'source_active_tx_rx_and_pending_irq', 'status': 'pass'})

            uri = f'unix:{directory}/migration.sock'
            target.monitor('migrate-incoming', {'uri': uri})
            source.monitor('migrate', {'uri': uri})
            report['source_migration'] = wait_migration(source)
            report['target_migration'] = wait_migration(target)
            assert source.monitor('query-status')['status'] == 'postmigrate'
            assert not target.monitor('query-status')['running']
            checks.append({'check': 'qmp_migration_completed', 'status': 'pass'})

            after = {hex(reg): target.read(reg) for reg in STATE_REGISTERS}
            assert before == after, {'before': before, 'after': after}
            assert int(target.command('readl 0x80010000')[0], 0) == 0x67454d49
            target.assert_irq(True)
            assert not select.select([target.serial], [], [], 0.02)[0], 'Already sent TX bytes were replayed'
            checks.append({'check': 'registers_ram_and_pending_irq_preserved', 'status': 'pass'})

            target.write(TX_FIFO, 0x48474645)
            assert target.serial_read(4) == b'EFGH'
            assert target.read(0x910) == 8
            assert target.read(M_STATUS) & DONE
            assert not target.read(STATUS) & 1
            checks.append({'check': 'unfinished_tx_continues_without_duplicate_prefix', 'status': 'pass'})

            assert target.read(RX_FIFO) == 0x64636261
            assert target.read(RX_FIFO) == 0x00676665
            assert target.read(RX_STATUS) == 0
            target.write(M_CLEAR, 0xffffffff)
            target.write(S_CLEAR, 0xffffffff)
            target.assert_irq(False)
            checks.append({'check': 'unread_rx_partial_word_and_w1c_irq_restored', 'status': 'pass'})

            target.serial.sendall(b'Z')
            target.wait_for(lambda: bool(target.read(RX_STATUS)), 'new RX after migration')
            target.assert_irq(True)
            assert target.read(RX_FIFO) == ord('Z')
            assert target.read(0x914) == 8
            target.write(M_CLEAR, 0xffffffff)
            target.write(S_CLEAR, 0xffffffff)
            target.assert_irq(False)
            checks.append({'check': 'destination_chardev_receives_and_interrupts', 'status': 'pass'})
            report['success'] = True
        except Exception:
            report['success'] = False
            report['error'] = traceback.format_exc()
        finally:
            if source:
                source.close()
            if target:
                target.close()
        if not report.get('success'):
            report['qemu_stderr_tails'] = {}
            for name, path in [('source', source_dir), ('target', target_dir)]:
                log = path / 'qemu.stderr'
                report['qemu_stderr_tails'][name] = log.read_text(errors='replace')[-8192:] if log.exists() else ''
        print(json.dumps(report, indent=2))
        return 0 if report.get('success') else 1


if __name__ == '__main__':
    raise SystemExit(main())
