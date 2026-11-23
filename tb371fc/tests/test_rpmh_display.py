#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Real QTest coverage of APPS/display RSC sharing one RPMh resource backend.

Uses the explicit synthetic reference command-db. Component migration requires
ufs-enabled=off and does not validate whole-board migration or AOP firmware.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import traceback

from test_rpmh_sm8250 import (
    RpmhRig, RESOURCES, TCS_BASE, TCS_STRIDE, ISSUED, COMPLETE, check,
)

if not __debug__:
    raise RuntimeError('QTest dependencies require Python without -O')

DISPLAY = 3
DISPLAY_BASE = 0x0af20000
DISPLAY_TCS_BASE = 0x1c00


class DisplayRig(RpmhRig):
    def __init__(self, args, directory, incoming=False, intercept='sysbus-irq'):
        self.intercept_outputs = intercept is not None
        super().__init__(args, directory, incoming=incoming,
                         intercept=intercept or 'sysbus-irq')

    def command(self, line):
        # The real-GIC case deliberately preserves the board IRQ connection.
        # This skips setup only; all test MMIO still goes to the real process.
        if not self.intercept_outputs and line.startswith('irq_intercept_out '):
            return []
        return super().command(line)

    @staticmethod
    def display_offset(offset):
        return offset + DISPLAY_TCS_BASE - TCS_BASE if offset >= TCS_BASE else offset

    def dread(self, offset, drv=2):
        if drv == DISPLAY:
            return self.raw_read(DISPLAY_BASE + self.display_offset(offset))
        return super().dread(offset, drv)

    def dwrite(self, offset, value, drv=2):
        if drv == DISPLAY:
            self.raw_write(DISPLAY_BASE + self.display_offset(offset), value)
        else:
            super().dwrite(offset, value, drv)

    def raw_read(self, address):
        return int(self.command(f'readl {address:#x}')[0], 0)

    def raw_write(self, address, value):
        self.command(f'writel {address:#x} {value:#x}')

    def idle(self, drv=DISPLAY, tcs=1):
        return self.dread(self.tcs(tcs, 0x18), drv)


def frame_geometry_matches_independent_dt_layout(r):
    for drv in range(3):
        check(r.dread(0xc, drv) == 0x80008208, ('APPS geometry', drv))
    check(r.dread(0xc, DISPLAY) == 0x80000002,
          'display DRV0 must expose two TCS and sixteen commands')
    check(r.dread(0, DISPLAY) == 0x20700, 'reference protocol revision')
    check(r.dread(4, DISPLAY) == 0, 'autonomous solver is not implemented')
    check(r.dread(TCS_BASE, DISPLAY) == 0, 'display IRQ reset state')
    for tcs in (0, 1):
        check(r.idle(tcs=tcs) == 1, ('display idle status', tcs))
        check(r.status(drv=DISPLAY, tcs=tcs) == 0, 'unexpected completed command')


def active_request_borrows_display_wake_tcs(r):
    r.dwrite(TCS_BASE, 2, DISPLAY)
    r.program('xo.lvl', 3, drv=DISPLAY, tcs=1)
    r.dwrite(r.tcs(1, 0x1c), 1, DISPLAY)
    r.step(5000)
    check(r.resource('xo.lvl') == 0, 'programming a wake bank ran it')
    r.trigger(drv=DISPLAY, tcs=1)
    check(r.idle() == 0 and r.status(drv=DISPLAY, tcs=1) == ISSUED,
          'software active request did not enter busy state')
    r.step(999)
    check(r.resource('xo.lvl') == 0, 'display command completed early')
    r.step(1)
    check(r.status(drv=DISPLAY, tcs=1) == ISSUED | COMPLETE,
          'borrowed wake request was not acknowledged')
    check(r.resource('xo.lvl') == 3 and r.idle() == 1,
          'display command did not update the shared service')
    r.irq_level(DISPLAY, True)
    r.dwrite(TCS_BASE + 8, 2, DISPLAY)
    r.irq_level(DISPLAY, False)


def four_voter_aggregation_and_independent_release(r):
    r.run_one('xo.lvl', 3, drv=0)
    r.run_one('xo.lvl', 2, drv=2)
    r.run_one('xo.lvl', 1, drv=DISPLAY, tcs=1)
    check(r.resource('xo.lvl') == 3, 'shared ARC maximum')
    r.run_one('xo.lvl', 0, drv=0)
    check(r.resource('xo.lvl') == 2, 'APPS0 release removed another vote')
    r.run_one('xo.lvl', 0, drv=2)
    check(r.resource('xo.lvl') == 1, 'APPS release removed display vote')
    r.expect_hz('bi_tcxo', 19_200_000)
    r.run_one('xo.lvl', 0, drv=DISPLAY, tcs=1)
    r.expect_hz('bi_tcxo', 0)
    r.run_one('lnbclka1', 1, field=1, drv=0)
    r.run_one('lnbclka1', 1, field=1, drv=DISPLAY, tcs=1)
    r.run_one('lnbclka1', 0, field=1, drv=0)
    check(r.resource('lnbclka1', 'enabled') == 1,
          'APPS release removed display VRM clock vote')
    r.run_one('lnbclka1', 0, field=1, drv=DISPLAY, tcs=1)
    check(r.resource('lnbclka1', 'enabled') == 0, 'final VRM release did not disable')


def frame_irq_masks_pending_and_w1c_are_independent(r):
    r.run_one('xo.lvl', 3)
    r.run_one('xo.lvl', 1, drv=DISPLAY, tcs=1)
    check(r.dread(TCS_BASE + 4) == 1, 'APPS pending bit')
    check(r.dread(TCS_BASE + 4, DISPLAY) == 2, 'display pending bit')
    r.irq_level(2, False)
    r.irq_level(DISPLAY, False)
    r.dwrite(TCS_BASE, 1)
    r.irq_level(2, True)
    r.irq_level(DISPLAY, False)
    r.dwrite(TCS_BASE, 2, DISPLAY)
    r.irq_level(DISPLAY, True)
    r.dwrite(TCS_BASE + 8, 1, DISPLAY)
    r.irq_level(DISPLAY, True)
    r.dwrite(TCS_BASE + 8, 2, DISPLAY)
    r.irq_level(DISPLAY, False)
    r.irq_level(2, True)
    r.dwrite(TCS_BASE, 0)
    check(r.dread(TCS_BASE + 4) == 1, 'masking discarded APPS pending state')


def bcm_commits_are_per_voter_with_shared_aggregation(r):
    r.run_one('IP0', 0x20000000 | 77)
    r.run_one('IP0', 0x20000000 | 44, drv=DISPLAY, tcs=1)
    r.expect_hz('ipa', 0)
    r.run_one('CE0', 0x60000001, drv=DISPLAY, tcs=1)
    r.expect_hz('ipa', 44_000_000)
    r.run_one('CE0', 0x60000001)
    r.expect_hz('ipa', 77_000_000)
    r.run_one('IP0', 0x40000000)
    r.expect_hz('ipa', 44_000_000)
    r.run_one('IP0', 0x40000000, drv=DISPLAY, tcs=1)
    r.expect_hz('ipa', 0)


def explicit_display_sleep_wake_uses_only_two_banks(r):
    r.run_one('xo.lvl', 1)
    r.run_one('xo.lvl', 3, drv=DISPLAY, tcs=1)
    r.clear(drv=DISPLAY, tcs=1)
    r.program('xo.lvl', 0, drv=DISPLAY, tcs=0)
    r.dwrite(r.tcs(0, 0x1c), 1, DISPLAY)
    r.program('xo.lvl', 3, drv=DISPLAY, tcs=1)
    r.dwrite(r.tcs(1, 0x1c), 1, DISPLAY)
    r.step(10000)
    check(r.resource('xo.lvl') == 3, 'programming sleep/wake caused execution')
    r.gpio('sleep', DISPLAY, True)
    r.step(999)
    check(r.resource('xo.lvl') == 3, 'sleep deadline fired early')
    r.step(1)
    check(r.resource('xo.lvl') == 1, 'display sleep clobbered the APPS vote')
    r.run_one('xo.lvl', 0)
    r.expect_hz('bi_tcxo', 0)
    r.gpio('sleep', DISPLAY, False)
    r.step(1000)
    check(r.resource('xo.lvl') == 3, 'display wake did not use TCS1')
    r.expect_hz('bi_tcxo', 19_200_000)


def highest_command_and_invalid_frame_boundaries(r):
    r.program('xo.lvl', 3, drv=DISPLAY, tcs=1, slot=15)
    r.trigger(mask=1 << 15, drv=DISPLAY, tcs=1)
    r.step(1000)
    check(r.status(slot=15, drv=DISPLAY, tcs=1) == ISSUED | COMPLETE,
          'last valid command slot failed')
    check(r.status(slot=0, drv=DISPLAY, tcs=1) == 0, 'disabled command executed')
    before = r.log_path.read_text().count('unsupported')
    for offset in (0xd00, DISPLAY_TCS_BASE + 2 * TCS_STRIDE + 0x30, 0x7000):
        r.raw_write(DISPLAY_BASE + offset, 0x12345678)
        r.raw_read(DISPLAY_BASE + offset)
    r.dwrite(TCS_BASE, 4, DISPLAY)
    check(r.dread(TCS_BASE, DISPLAY) == 0, 'nonexistent TCS interrupt was enabled')
    check(r.resource('xo.lvl') == 3, 'out-of-frame access aliased a resource')
    after = r.log_path.read_text().count('unsupported')
    check(after - before >= 7, 'unknown SDE/old offset/extra TCS lacked diagnostics')


def cancel_preserves_completed_command_but_not_unfinished_one(r):
    r.program('ldoa12', 1800, drv=DISPLAY, tcs=1)
    r.program('ldoa12', 1, field=1, drv=DISPLAY, tcs=1, slot=1)
    r.trigger(mask=3, drv=DISPLAY, tcs=1)
    r.step(1000)
    r.dwrite(r.tcs(1, 0x14), 0, DISPLAY)
    r.step(10000)
    check(r.resource('ldoa12') == 1800, 'cancel rolled back completed voltage')
    check(r.resource('ldoa12', 'enabled') == 0, 'cancelled enable executed')
    check(r.status(slot=1, drv=DISPLAY, tcs=1) == ISSUED, 'cancel invented ACK')
    check(r.idle() == 1, 'cancel did not return idle')
    r.run_one('ldoa12', 1, field=1, drv=DISPLAY, tcs=1)
    check(r.resource('ldoa12', 'enabled') == 1, 'cancelled TCS was not reusable')


def unsupported_display_request_stalls_without_false_ack(r):
    r.dwrite(TCS_BASE, 2, DISPLAY)
    r.run_one('xo.lvl', 3, drv=DISPLAY, tcs=1, address=0x3dead)
    r.step(10000)
    check(r.status(drv=DISPLAY, tcs=1) == ISSUED, 'unsupported request was ACKed')
    check(r.idle() == 0, 'stalled request incorrectly became idle')
    check(r.resource('xo.lvl') == 0, 'unknown address changed the service')
    check(r.dread(TCS_BASE + 4, DISPLAY) == 0, 'stalled request completed')
    r.irq_level(DISPLAY, False)
    r.run_one('xo.lvl', 3, drv=DISPLAY, tcs=1)
    check(r.resource('xo.lvl') == 3 and r.idle() == 1, 'stall could not be cancelled')


def guest_reset_clears_both_frames_and_all_votes(r):
    r.run_one('xo.lvl', 3)
    r.dwrite(TCS_BASE, 1)
    r.run_one('xo.lvl', 2, drv=DISPLAY, tcs=1)
    r.run_one('IP0', 0x20000000 | 77, drv=DISPLAY, tcs=0)
    r.dwrite(TCS_BASE, 3, DISPLAY)
    r.clear(drv=DISPLAY, tcs=1)
    r.program('ldoa12', 1, field=1, drv=DISPLAY, tcs=1)
    r.trigger(drv=DISPLAY, tcs=1)
    r.monitor('system_reset')
    r.wait_for(lambda: r.dread(TCS_BASE, DISPLAY) == 0 and
               r.status(drv=DISPLAY, tcs=1) == 0, 'dual-controller reset')
    r.step(10000)
    for drv in range(4):
        check(r.dread(TCS_BASE, drv) == r.dread(TCS_BASE + 4, drv) == 0,
              ('reset IRQ state', drv))
        r.irq_level(drv, False)
    check(r.resource('xo.lvl') == 0, 'reset left a fourth-voter request')
    check(r.resource('ldoa12', 'enabled') == 0, 'reset command later executed')
    r.run_one('CE0', 0x60000001, drv=DISPLAY, tcs=1)
    r.expect_hz('ipa', 0)
    check(r.idle(tcs=0) == 1 and r.idle(tcs=1) == 1, 'reset idle state')


def real_gic_spi129_mask_pending_clear_and_deassert(r):
    # This fixture does not intercept sysbus IRQ output and never writes
    # GICD_ISPENDR. A pending bit must originate at the real board connection.
    gicd, intid = 0x17a00000, 129 + 32
    bit = 1 << (intid % 32)
    pending = gicd + 0x200 + 4 * (intid // 32)
    clear = gicd + 0x280 + 4 * (intid // 32)
    enable = gicd + 0x100 + 4 * (intid // 32)
    config = gicd + 0xc00 + 4 * (intid // 16)
    shift = 2 * (intid % 16)
    r.raw_write(config, r.raw_read(config) & ~(3 << shift))
    r.raw_write(enable, bit)
    r.raw_write(clear, bit)
    check(not r.raw_read(pending) & bit, 'GIC initially pending')
    r.dwrite(TCS_BASE, 2, DISPLAY)
    r.run_one('xo.lvl', 3, drv=DISPLAY, tcs=1)
    check(r.raw_read(pending) & bit, 'display IRQ did not reach SPI129')
    r.dwrite(TCS_BASE, 0, DISPLAY)
    r.raw_write(clear, bit)
    check(not r.raw_read(pending) & bit, 'mask did not lower the physical line')
    r.dwrite(TCS_BASE, 2, DISPLAY)
    check(r.raw_read(pending) & bit, 'pending display IRQ did not reassert')
    r.dwrite(TCS_BASE + 8, 2, DISPLAY)
    r.raw_write(clear, bit)
    check(not r.raw_read(pending) & bit, 'W1C did not deassert SPI129')


TESTS = [
    frame_geometry_matches_independent_dt_layout,
    active_request_borrows_display_wake_tcs,
    four_voter_aggregation_and_independent_release,
    frame_irq_masks_pending_and_w1c_are_independent,
    bcm_commits_are_per_voter_with_shared_aggregation,
    explicit_display_sleep_wake_uses_only_two_banks,
    highest_command_and_invalid_frame_boundaries,
    cancel_preserves_completed_command_but_not_unfinished_one,
    unsupported_display_request_stalls_without_false_ack,
    guest_reset_clears_both_frames_and_all_votes,
    real_gic_spi129_mask_pending_clear_and_deassert,
]


def migrate(args, directory):
    checks, source, target = [], None, None
    try:
        source = DisplayRig(args, directory / 'source')
        source.run_one('xo.lvl', 3)
        source.dwrite(TCS_BASE, 1)
        source.run_one('xo.lvl', 1, drv=DISPLAY, tcs=1)
        source.run_one('IP0', 0x20000000 | 77, tcs=1)
        source.run_one('IP0', 0x20000000 | 44, drv=DISPLAY, tcs=0)
        # Keep the old completion latch while starting an unsupported request.
        source.dwrite(source.tcs(0, 0x14), 0, DISPLAY)
        source.dwrite(source.tcs(0, 0x1c), 0, DISPLAY)
        source.program('xo.lvl', 3, drv=DISPLAY, tcs=0, address=0x3dead)
        source.trigger(drv=DISPLAY, tcs=0)
        source.step(1000)
        source.dwrite(TCS_BASE, 1, DISPLAY)
        source.clear(drv=DISPLAY, tcs=1)
        source.program('xo.lvl', 2, drv=DISPLAY, tcs=1)
        source.trigger(drv=DISPLAY, tcs=1)
        now = source.last_clock
        source.monitor('stop')
        target = DisplayRig(args, directory / 'target', incoming=True)
        target.step(now)
        uri = 'unix:' + str(directory / 'migration.sock')
        target.monitor('migrate-incoming', {'uri': uri})
        source.monitor('migrate', {'uri': uri})
        source.wait_for(lambda: source.monitor('query-migrate').get('status')
                        in ('completed', 'failed'), 'display RSC migration', timeout=30)
        state = source.monitor('query-migrate')
        check(state['status'] == 'completed', state)
        target.wait_for(lambda: target.monitor('query-status')['status'] != 'inmigrate',
                        'destination display state', timeout=30)
        check(not target.monitor('query-status')['running'], 'target resumed itself')
        checks.append(dict(check='actual_process_transfer_v2', status='pass'))
        for drv in range(4):
            for offset in (0xc, TCS_BASE, TCS_BASE + 4):
                check(target.dread(offset, drv) == source.dread(offset, drv),
                      ('frame state mismatch', drv, offset))
        target.irq_level(2, True)
        target.irq_level(DISPLAY, True)
        check(target.resource('xo.lvl') == 3, 'shared committed vote lost')
        check(target.status(drv=DISPLAY, tcs=0) == ISSUED,
              'stalled display command changed')
        check(target.status(drv=DISPLAY, tcs=1) == ISSUED,
              'in-flight display command completed during load')
        checks.append(dict(check='both_frames_pending_masks_and_votes_preserved', status='pass'))
        target.monitor('cont')
        target.step(999)
        check(target.status(drv=DISPLAY, tcs=1) == ISSUED, 'deadline fired early')
        target.step(1)
        check(target.status(drv=DISPLAY, tcs=1) == ISSUED | COMPLETE,
              'display in-flight command did not resume')
        target.run_one('xo.lvl', 0)
        check(target.resource('xo.lvl') == 2, 'display vote lost after APPS release')
        target.expect_hz('bi_tcxo', 19_200_000)
        checks.append(dict(check='deadline_and_shared_vote_release_after_load', status='pass'))
        target.run_one('CE0', 0x60000001, drv=DISPLAY, tcs=1)
        target.expect_hz('ipa', 44_000_000)
        target.run_one('CE0', 0x60000001)
        target.expect_hz('ipa', 77_000_000)
        target.run_one('IP0', 0x40000000)
        target.expect_hz('ipa', 44_000_000)
        target.run_one('IP0', 0x40000000, drv=DISPLAY, tcs=1)
        target.expect_hz('ipa', 0)
        checks.append(dict(check='independent_staged_bcm_votes_survive_and_commit', status='pass'))
        check(target.status(drv=DISPLAY, tcs=0) == ISSUED,
              'unknown command falsely completed after migration')
        target.run_one('xo.lvl', 3, drv=DISPLAY, tcs=0)
        check(target.resource('xo.lvl') == 3, 'stalled TCS recovery failed')
        checks.append(dict(check='stalled_request_is_not_acknowledged_and_recovers', status='pass'))
        target.clear(drv=DISPLAY, tcs=0)
        target.clear(drv=DISPLAY, tcs=1)
        target.program('xo.lvl', 0, drv=DISPLAY, tcs=0)
        target.dwrite(target.tcs(0, 0x1c), 1, DISPLAY)
        target.program('xo.lvl', 3, drv=DISPLAY, tcs=1)
        target.dwrite(target.tcs(1, 0x1c), 1, DISPLAY)
        target.gpio('sleep', DISPLAY, True)
        target.step(1000)
        target.expect_hz('bi_tcxo', 0)
        target.gpio('sleep', DISPLAY, False)
        target.step(1000)
        target.expect_hz('bi_tcxo', 19_200_000)
        checks.append(dict(check='display_sleep_wake_remains_operational_after_load', status='pass'))
        return dict(success=True, expected_checks=6, checks=checks, migration=state,
                    qtest_virtual_ns_at_transfer=now)
    except Exception:
        return dict(success=False, expected_checks=6, checks=checks,
                    error=traceback.format_exc())
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--machine', default='tb371fc,rpmh-profile=reference,rpmh-boot-configured=off,ufs-enabled=off')
    parser.add_argument('--base', type=lambda value: int(value, 0), default=0x18200000)
    parser.add_argument('--cmd-db-base', type=lambda value: int(value, 0), default=0x80860000)
    parser.add_argument('--qom-path', default='/machine/rpmh')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--migration-only', action='store_true')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='rsc-display-'))
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'result.json').exists():
        raise RuntimeError('refusing to replace an existing test receipt')
    with open(args.qemu, 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = dict(schema_version=1, suite='SM8250 shared APPS/display RSC actual QTest',
                  qemu=args.qemu, qemu_sha256=digest, machine=args.machine,
                  test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  work_dir=str(directory), native_command_db=False,
                  scope='Two controller geometries, four voters and one digital service; synthetic command-db; no whole-board migration, AOP firmware or SDE sequencer equivalence')
    if args.migration_only:
        report.update(migrate(args, directory))
    else:
        checks = []
        for index, test in enumerate(TESTS):
            fixture = directory / f'{index:02d}'
            started = time.monotonic()
            rig = None
            try:
                intercept = None if test is real_gic_spi129_mask_pending_clear_and_deassert else 'sysbus-irq'
                rig = DisplayRig(args, fixture, intercept=intercept)
                test(rig)
                checks.append(dict(test=test.__name__, status='pass',
                                   seconds=round(time.monotonic() - started, 4)))
            except Exception:
                checks.append(dict(test=test.__name__, status='fail',
                                   error=traceback.format_exc()))
                break
            finally:
                if rig:
                    rig.close()
        report.update(checks=checks, expected_checks=len(TESTS),
                      success=len(checks) == len(TESTS) and all(c['status'] == 'pass' for c in checks))
    output = json.dumps(report, indent=2) + '\n'
    (directory / 'result.json').write_text(output)
    print(output, end='')
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
