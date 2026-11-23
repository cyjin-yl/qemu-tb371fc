#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Migrate pending MMIO timer deadlines and IRQ state between real QEMU processes."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_mmio_timer import (TimerRig, CTRL, FRAME, EL0, PCT, VCT, EL0ACR, VOFF,
                             PCVAL, PTVAL, PCTL, VCVAL, VTVAL, VCTL, ACR,
                             OFFSET, PERIOD)
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit('Acceptance checks require assertions; do not use -O.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--counter-mode', choices=['exact', 'legacy'], default='exact')
    parser.add_argument('--target-machine')
    parser.add_argument('--expect-mode-rejection', action='store_true')
    args = parser.parse_args()
    args.qemu = str(Path(args.qemu).resolve())
    args.qom_path, args.base = '/machine/mmio-timer', CTRL
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / 'result.json').exists():
        parser.error('use a fresh output directory')
    checks = []
    report = {
        'schema_version': 1, 'suite': 'MMIO timer QMP process-to-process migration',
        'qemu_sha256': hashlib.sha256(Path(args.qemu).read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'machine': args.machine, 'counter_mode': args.counter_mode,
        'test_ram_per_process': '128 MiB',
        'checks': checks, 'passed': False,
        'clock_fixture': 'QTest external time is not migrated; destination aligned after incoming load and before timer assertions',
        'scope': 'Actual transfer of timer registers, absolute deadlines, permission and IRQ continuation',
    }
    source = target = None
    start = time.monotonic()
    try:
        sd, td = args.out / 'source', args.out / 'target'
        sd.mkdir()
        td.mkdir()
        source = TimerRig(args, sd)
        target_args = copy.copy(args)
        if args.target_machine:
            target_args.machine = args.target_machine
        report['target_machine'] = target_args.machine
        target = TimerRig(target_args, td, incoming=True)
        source.reset()
        source.enable_access()
        ns = source.step(1000000)
        source.put(FRAME + EL0ACR, 0x303)
        source.put(CTRL + OFFSET, 100, 8)
        source.put(FRAME + PTVAL, 500)
        source.put(FRAME + PCTL, 1)
        source.put(FRAME + VTVAL, 0)
        source.put(FRAME + VCTL, 1)
        source.level(0, False)
        source.level(1, True)
        registers = (PCT, VCT, VOFF, PCVAL, VCVAL)
        before = {hex(x): source.reg(FRAME + x, 8) for x in registers}
        before_ctl = {hex(x): source.reg(FRAME + x)
                      for x in (PCTL, VCTL, EL0ACR, PTVAL, VTVAL)}
        source.command('writel 0x80010000 0x54494d52')
        checks.append({'name': 'source_future_physical_and_asserted_virtual_irq',
                       'passed': True})
        # Pause for migration; QTest owns an external synthetic clock.
        # Its counter is not part of QEMU's migrated virtual-clock state.
        source.monitor('stop')
        report['qtest_timestamp_ns'] = ns
        uri = 'unix:' + str(args.out / 'migration.sock')
        target.monitor('migrate-incoming', {'uri': uri})
        source.monitor('migrate', {'uri': uri})
        if args.expect_mode_rejection:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                status = source.monitor('query-migrate')
                if status.get('status') in ('failed', 'cancelled', 'completed'):
                    break
                time.sleep(0.02)
            report['source_migration'] = status
            log = (td / 'qemu.stderr').read_text(errors='replace')
            report['target_stderr'] = log
            assert status.get('status') == 'failed', status
            assert ('cntfrq-exact' in log or
                    'Incoming CPU counter mode differs' in log), log
            checks.append({'name': 'counter_mode_mismatch_rejected',
                           'passed': True})
            report['passed'] = True
        else:
            report['source_migration'] = wait_migration(source)
            report['target_migration'] = wait_migration(target)
            assert source.monitor('query-status')['status'] == 'postmigrate'
            assert not target.monitor('query-status')['running']
            target.monitor('cont')
            # clock_set cannot advance a paused VM in QEMU 11. Align this
            # external test fixture before inspecting any timer/IRQ results.
            target.clock_ns = int(target.command(f'clock_set {ns}')[0], 0)
            assert target.clock_ns == ns
            checks.append({'name': 'actual_qmp_migration_completed', 'passed': True})
            after = {hex(x): target.reg(FRAME + x, 8) for x in registers}
            after_ctl = {hex(x): target.reg(FRAME + x)
                         for x in (PCTL, VCTL, EL0ACR, PTVAL, VTVAL)}
            assert before == after, (before, after)
            assert before_ctl == after_ctl, (before_ctl, after_ctl)
            assert target.reg(CTRL + ACR) == 0x3f
            assert target.reg(CTRL + OFFSET, 8) == 100
            assert target.reg(EL0 + PCT, 8) == target.reg(FRAME + PCT, 8)
            assert int(target.command('readl 0x80010000')[0], 0) == 0x54494d52
            target.level(0, False)
            target.level(1, True)
            checks.append({'name': 'registers_permissions_alias_ram_irq_preserved',
                           'passed': True})
            target.step(499)
            target.level(0, False)
            assert target.reg(FRAME + PTVAL) == 1
            target.step(1)
            target.level(0, True)
            assert target.reg(FRAME + PCTL) == 5
            target.put(FRAME + VCTL, 3)
            target.level(1, False)
            assert target.reg(FRAME + VCTL) == 7
            checks.append({'name': 'pending_deadline_fires_once_at_original_tick',
                           'passed': True})
            report['passed'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        if source:
            source.close()
        if target:
            target.close()
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (args.out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
