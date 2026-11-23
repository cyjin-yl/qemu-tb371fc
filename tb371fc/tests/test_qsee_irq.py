#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest for the two QSEE notification banks, not secure firmware.

All notifications use the device's standard notify GPIO adapter. The actual
GIC case leaves sysbus IRQ outputs connected. Migration is component-only
with UFS explicitly disabled.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import traceback

from test_gcc_sm8250 import GccRig

if not __debug__:
    raise RuntimeError('QTest dependencies require Python without -O')

BASES = (0x1886000, 0x1888000)
STATUS, CLEAR, MASK = 0, 8, 0x1c


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


class QseeRig(GccRig):
    def __init__(self, args, directory, incoming=False, intercept='sysbus-irq'):
        self.intercept_outputs = intercept is not None
        super().__init__(args, directory, incoming=incoming,
                         intercept=intercept or 'sysbus-irq')

    def command(self, line):
        if not self.intercept_outputs and line.startswith('irq_intercept_out '):
            return []
        return super().command(line)

    def bread(self, bank, offset):
        return self.raw_read(BASES[bank] + offset)

    def bwrite(self, bank, offset, value):
        self.raw_write(BASES[bank] + offset, value)

    def raw_read(self, address):
        return int(self.command(f'readl {address:#x}')[0], 0)

    def raw_write(self, address, value):
        self.command(f'writel {address:#x} {value:#x}')

    def level(self, bank, bit, value):
        self.gpio('notify', bank * 4 + bit, value)

    def pulse(self, bank, bit):
        self.level(bank, bit, False)
        self.level(bank, bit, True)
        self.level(bank, bit, False)

    def irq_level(self, bank, expected):
        self.bread(bank, STATUS)
        check(self.irq[bank] == expected, (bank, expected, self.irq))

    def snapshot(self):
        return [(self.bread(bank, STATUS), self.bread(bank, MASK))
                for bank in range(2)]

    def protocol_reset(self):
        self.monitor('system_reset')
        self.wait_for(lambda: self.snapshot() == [(0, 15), (0, 15)],
                      'QSEE protocol reset')


def reset_masks_all_and_has_no_pending_notification(r):
    check(r.snapshot() == [(0, 15), (0, 15)], 'reference reset state')
    r.irq_level(0, False)
    r.irq_level(1, False)


def masked_notification_is_retained_and_unmask_asserts(r):
    r.pulse(0, 2)
    check(r.bread(0, STATUS) == 4, 'masked event was lost')
    r.irq_level(0, False)
    r.bwrite(0, MASK, 0xb)
    r.irq_level(0, True)
    r.bwrite(0, MASK, 0xf)
    r.irq_level(0, False)
    check(r.bread(0, STATUS) == 4, 'masking discarded pending state')
    r.bwrite(0, MASK, 0xb)
    r.irq_level(0, True)


def w1c_clears_only_selected_pending_bits(r):
    r.bwrite(0, MASK, 0)
    for bit in (0, 1, 3):
        r.pulse(0, bit)
    check(r.bread(0, STATUS) == 0xb, 'notification combination')
    r.bwrite(0, CLEAR, 2)
    check(r.bread(0, STATUS) == 9, 'W1C cleared unrelated bits')
    r.bwrite(0, CLEAR, 0)
    check(r.bread(0, STATUS) == 9, 'zero clear changed state')
    r.irq_level(0, True)
    r.bwrite(0, CLEAR, 9)
    r.irq_level(0, False)


def both_banks_are_independent(r):
    r.bwrite(0, MASK, 0)
    r.bwrite(1, MASK, 0)
    r.pulse(0, 0)
    r.pulse(1, 3)
    check(r.snapshot() == [(1, 0), (8, 0)], 'banks aliased')
    r.bwrite(0, CLEAR, 1)
    r.irq_level(0, False)
    r.irq_level(1, True)
    check(r.bread(1, STATUS) == 8, 'bank0 clear changed bank1')
    r.bwrite(1, MASK, 15)
    check(r.bread(0, MASK) == 0, 'bank1 mask changed bank0')


def all_four_inputs_and_selective_mask(r):
    for bit in range(4):
        r.pulse(1, bit)
    check(r.bread(1, STATUS) == 15, 'not all four notification inputs work')
    r.bwrite(1, MASK, 0xe)
    r.irq_level(1, True)
    r.bwrite(1, CLEAR, 1)
    r.irq_level(1, False)
    check(r.bread(1, STATUS) == 14, 'masked pending bits were dropped')
    r.bwrite(1, MASK, 0xd)
    r.irq_level(1, True)
    r.bwrite(1, CLEAR, 14)
    r.irq_level(1, False)


def held_input_does_not_relatch_after_w1c(r):
    r.bwrite(0, MASK, 0xe)
    r.level(0, 0, True)
    r.irq_level(0, True)
    r.bwrite(0, CLEAR, 1)
    r.level(0, 0, True)
    r.step(10000)
    check(r.bread(0, STATUS) == 0, 'held reference input fabricated an edge')
    r.irq_level(0, False)
    r.level(0, 0, False)
    r.level(0, 0, True)
    check(r.bread(0, STATUS) == 1, 'fresh edge did not latch')


def reset_preserves_external_level_without_fake_edge(r):
    r.bwrite(1, MASK, 0xd)
    r.level(1, 1, True)
    r.irq_level(1, True)
    r.protocol_reset()
    r.bwrite(1, MASK, 0xd)
    r.level(1, 1, True)
    check(r.bread(1, STATUS) == 0, 'reset turned held input into a new edge')
    r.irq_level(1, False)
    r.level(1, 1, False)
    r.level(1, 1, True)
    r.irq_level(1, True)
    check(r.bread(1, STATUS) == 2, 'real edge after reset failed')


def read_only_status_write_only_clear_and_unknown_registers(r):
    r.pulse(0, 0)
    before = r.snapshot()
    diagnostic_before = r.log_path.read_text().count('unsupported')
    r.bwrite(0, STATUS, 0)
    r.bread(0, CLEAR)
    r.bread(0, 4)
    r.bwrite(0, 4, 1)
    r.bread(1, 0x10)
    check(r.snapshot() == before, 'unsupported MMIO changed valid registers')
    diagnostic_after = r.log_path.read_text().count('unsupported')
    check(diagnostic_after - diagnostic_before >= 5,
          'read-only/write-only/unknown MMIO was silently accepted')


def reserved_mask_and_clear_bits_are_rejected(r):
    r.pulse(0, 0)
    r.bwrite(0, MASK, 0x10)
    check(r.bread(0, MASK) == 15, 'reserved mask bit accepted')
    r.bwrite(0, CLEAR, 0x11)
    check(r.bread(0, STATUS) == 1, 'invalid W1C partly cleared a valid bit')
    r.bwrite(1, MASK, 0xffff)
    check(r.bread(1, MASK) == 15, 'bank1 mask exceeded four bits')
    check('unsupported' in r.log_path.read_text(), 'missing reserved-bit diagnostic')


def invalid_width_and_alignment_do_not_modify_registers(r):
    r.pulse(0, 0)
    r.pulse(0, 1)
    before = r.snapshot()
    diagnostic_before = r.log_path.read_text().count('unsupported')
    # Keep the 64-bit access wholly inside one 0x20-byte region.
    r.command(f'writeq {BASES[0] + CLEAR:#x} 0x3')
    r.command(f'writeb {BASES[0] + MASK:#x} 0')
    r.command(f'writew {BASES[0] + MASK:#x} 0')
    r.command(f'writel {BASES[0] + CLEAR + 1:#x} 0x3')
    r.command(f'readq {BASES[0]:#x}')
    check(r.snapshot() == before, 'invalid width/alignment changed the banks')
    check(r.log_path.read_text().count('unsupported') > diagnostic_before,
          'invalid accesses had no diagnostic')


def unmasking_does_not_invent_secure_firmware_events(r):
    r.bwrite(0, MASK, 0)
    r.bwrite(1, MASK, 0)
    r.step(1_000_000)
    check(r.snapshot() == [(0, 0), (0, 0)], 'model invented a notification')
    r.irq_level(0, False)
    r.irq_level(1, False)


def real_gic_spis348_349_pending_mask_clear_and_deassert(r):
    # No sysbus interception and no GICD_ISPENDR writes.
    gicd = 0x17a00000
    intids = (348 + 32, 349 + 32)
    bits = tuple(1 << (irq % 32) for irq in intids)
    pending = gicd + 0x200 + 4 * (intids[0] // 32)
    clear = gicd + 0x280 + 4 * (intids[0] // 32)
    enable = gicd + 0x100 + 4 * (intids[0] // 32)
    for irq in intids:
        config = gicd + 0xc00 + 4 * (irq // 16)
        shift = 2 * (irq % 16)
        r.raw_write(config, r.raw_read(config) & ~(3 << shift))
    both = bits[0] | bits[1]
    r.raw_write(enable, both)
    r.raw_write(clear, both)
    check(not r.raw_read(pending) & both, 'GIC initially pending')
    r.bwrite(0, MASK, 0xe)
    r.bwrite(1, MASK, 7)
    r.pulse(0, 0)
    r.pulse(1, 3)
    check(r.raw_read(pending) & both == both, 'one of the physical SPIs is missing')
    r.bwrite(0, CLEAR, 1)
    r.raw_write(clear, bits[0])
    check(r.raw_read(pending) & both == bits[1], 'bank0 clear changed bank1 route')
    r.bwrite(1, MASK, 15)
    r.raw_write(clear, bits[1])
    check(not r.raw_read(pending) & both, 'mask did not lower physical IRQ1')
    r.bwrite(1, MASK, 7)
    check(r.raw_read(pending) & bits[1], 'unmask did not reassert retained event')
    r.bwrite(1, CLEAR, 8)
    r.raw_write(clear, bits[1])
    check(not r.raw_read(pending) & both, 'W1C did not deassert physical IRQ1')


TESTS = [
    reset_masks_all_and_has_no_pending_notification,
    masked_notification_is_retained_and_unmask_asserts,
    w1c_clears_only_selected_pending_bits,
    both_banks_are_independent,
    all_four_inputs_and_selective_mask,
    held_input_does_not_relatch_after_w1c,
    reset_preserves_external_level_without_fake_edge,
    read_only_status_write_only_clear_and_unknown_registers,
    reserved_mask_and_clear_bits_are_rejected,
    invalid_width_and_alignment_do_not_modify_registers,
    unmasking_does_not_invent_secure_firmware_events,
    real_gic_spis348_349_pending_mask_clear_and_deassert,
]


def migrate(args, directory):
    checks, source, target = [], None, None
    try:
        source = QseeRig(args, directory / 'source')
        source.bwrite(0, MASK, 0xe)
        source.level(0, 0, True)
        source.level(1, 2, True)
        source.pulse(1, 3)
        source.irq_level(0, True)
        source.irq_level(1, False)
        before = source.snapshot()
        # QTest clock_step requires a strict increase, including on the target.
        source.step(1)
        now = source.last_clock
        source.monitor('stop')
        target = QseeRig(args, directory / 'target', incoming=True)
        target.step(now)
        uri = 'unix:' + str(directory / 'migration.sock')
        target.monitor('migrate-incoming', {'uri': uri})
        source.monitor('migrate', {'uri': uri})
        source.wait_for(lambda: source.monitor('query-migrate').get('status')
                        in ('completed', 'failed'), 'QSEE migration', timeout=30)
        state = source.monitor('query-migrate')
        check(state['status'] == 'completed', state)
        target.wait_for(lambda: target.monitor('query-status')['status'] != 'inmigrate',
                        'destination QSEE state', timeout=30)
        check(not target.monitor('query-status')['running'], 'destination resumed itself')
        checks.append(dict(check='actual_process_to_process_transfer', status='pass'))
        check(target.snapshot() == before == [(1, 14), (12, 15)],
              'pending masks did not survive')
        target.irq_level(0, True)
        target.irq_level(1, False)
        checks.append(dict(check='active_and_masked_pending_state_preserved', status='pass'))
        target.monitor('cont')
        target.bwrite(0, CLEAR, 1)
        target.level(0, 0, True)
        check(target.bread(0, STATUS) == 0, 'held bank0 input produced a false edge')
        target.bwrite(1, MASK, 0xb)
        target.irq_level(1, True)
        target.bwrite(1, CLEAR, 4)
        target.level(1, 2, True)
        check(target.bread(1, STATUS) == 8, 'held bank1 input produced a false edge')
        target.irq_level(1, False)
        checks.append(dict(check='held_input_levels_preserved_without_phantom_edges', status='pass'))
        target.bwrite(1, MASK, 3)
        target.irq_level(1, True)
        target.bwrite(1, CLEAR, 8)
        target.irq_level(1, False)
        checks.append(dict(check='masked_event_unmasks_and_w1c_deasserts_after_load', status='pass'))
        target.level(0, 0, False)
        target.level(0, 0, True)
        target.level(1, 2, False)
        target.level(1, 2, True)
        check(target.snapshot() == [(1, 14), (4, 3)], 'fresh post-load events failed')
        target.irq_level(0, True)
        target.irq_level(1, True)
        checks.append(dict(check='independent_new_edges_work_after_load', status='pass'))
        target.protocol_reset()
        target.bwrite(0, MASK, 0)
        target.bwrite(1, MASK, 0)
        target.level(0, 0, True)
        target.level(1, 2, True)
        check(target.snapshot() == [(0, 0), (0, 0)],
              'post-migration reset invented an event from a held input')
        target.irq_level(0, False)
        target.irq_level(1, False)
        checks.append(dict(check='reset_keeps_external_levels_after_migration', status='pass'))
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
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--base', type=lambda value: int(value, 0), default=BASES[0])
    parser.add_argument('--qom-path', default='/machine/qsee-irq')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--migration-only', action='store_true')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='qsee-irq-'))
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / 'result.json').exists():
        raise RuntimeError('refusing to replace an existing test receipt')
    with open(args.qemu, 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    report = dict(schema_version=1, suite='QSEE digital notification actual QTest',
                  qemu=args.qemu, qemu_sha256=digest, machine=args.machine,
                  test_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  work_dir=str(directory),
                  scope='Two four-bit notification banks; reference GPIO event adapter; no TEE/SCM or full-board migration')
    if args.migration_only:
        report.update(migrate(args, directory))
    else:
        checks = []
        for index, test in enumerate(TESTS):
            fixture = directory / f'{index:02d}'
            started = time.monotonic()
            rig = None
            try:
                intercept = None if test is real_gic_spis348_349_pending_mask_clear_and_deassert else 'sysbus-irq'
                rig = QseeRig(args, fixture, intercept=intercept)
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
