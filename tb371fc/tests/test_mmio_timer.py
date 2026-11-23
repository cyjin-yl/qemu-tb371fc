#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest acceptance for the mapped Arm Generic Timer and its IRQs."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_geni_uart import Rig

if not __debug__:
    raise SystemExit('Acceptance checks require assertions; do not use -O.')

CTRL, FRAME, EL0 = 0x17c20000, 0x17c21000, 0x17c22000
PCT, VCT, FRQ, EL0ACR, VOFF = 0, 8, 0x10, 0x14, 0x18
PCVAL, PTVAL, PCTL = 0x20, 0x28, 0x2c
VCVAL, VTVAL, VCTL = 0x30, 0x38, 0x3c
ACR, OFFSET = 0x40, 0x80
PERIOD = 52  # Legacy QEMU mode, retained for comparison.
FREQUENCY = 19200000
NSEC = 1000000000


class TimerRig(Rig):
    def __init__(self, args, directory, incoming=False):
        super().__init__(args, directory, incoming=incoming)
        self.clock_ns = 0
        self.exact = getattr(args, 'counter_mode', 'exact') == 'exact'
        if not incoming:
            self.monitor('cont')

    def reset(self):
        self.monitor('system_reset')
        self.wait_for(lambda: self.reg(CTRL + ACR) == 0,
                      'MMIO timer reset')
        self.step()
        for number in range(14):
            self.level(number, False)

    def reg(self, address, width=4):
        suffix = {1: 'b', 2: 'w', 4: 'l', 8: 'q'}[width]
        return int(self.command(f'read{suffix} {address:#x}')[0], 0)

    def put(self, address, value, width=4):
        suffix = {1: 'b', 2: 'w', 4: 'l', 8: 'q'}[width]
        self.command(f'write{suffix} {address:#x} {value:#x}')

    def advance_ns(self, ns):
        self.clock_ns = int(self.command(f'clock_step {ns}')[0], 0)
        return self.clock_ns

    def count_at(self, ns):
        return ns * FREQUENCY // NSEC if self.exact else ns // PERIOD

    def time_at(self, tick):
        return ((tick * NSEC + FREQUENCY - 1) // FREQUENCY
                if self.exact else tick * PERIOD)

    def step(self, ticks=0):
        if not ticks:
            # QEMU 11 rejects clock_step 0 because time did not advance.
            # A read flushes pending IRQ messages without moving the clock.
            self.reg(CTRL + 8)
            return self.clock_ns
        goal = self.time_at(self.count_at(self.clock_ns) + ticks)
        return self.advance_ns(goal - self.clock_ns)

    def level(self, number, expected):
        self.step()
        assert self.irq.get(number, False) is expected, (number, self.irq)

    def enable_access(self):
        self.put(CTRL + ACR, 0x3f)
        assert self.reg(CTRL + ACR) == 0x3f


def capability_and_security(r):
    assert r.reg(CTRL + 8) == 0x1111117
    r.put(CTRL + 8, 0xffffffff)
    assert r.reg(CTRL + 8) == 0x1111117
    # QTest accesses are Non-secure. Secure control registers are RAZ/WI.
    assert r.reg(CTRL) == r.reg(CTRL + 4) == 0
    r.put(CTRL, 123)
    r.put(CTRL + 4, 0xff)
    r.put(CTRL + ACR + 4, 0x3f)
    assert r.reg(CTRL + ACR + 4) == 0
    assert r.reg(0x17c23000 + FRQ) == 0
    assert r.reg(FRAME + FRQ) == 0
    r.put(FRAME + PCVAL, 0x1122334455667788, 8)
    r.enable_access()
    assert r.reg(FRAME + FRQ) == 19200000
    assert r.reg(FRAME + PCVAL, 8) == 0
    r.put(CTRL + ACR, 0xffffffff)
    assert r.reg(CTRL + ACR) == 0x3f
    assert r.reg(CTRL + ACR + 7 * 4) == 0
    for irq in range(14):
        r.level(irq, False)


def common_counter_and_atomic_reads(r):
    r.enable_access()
    ns = r.step(100000)
    assert r.reg(FRAME + PCT, 8) == r.count_at(ns)
    assert r.reg(FRAME + VCT, 8) == r.count_at(ns)
    before = r.reg(FRAME + PCT, 8)
    r.step(321)
    after = r.reg(FRAME + PCT, 8)
    assert after - before == 321
    assert (r.reg(FRAME + PCT + 4) << 32 | r.reg(FRAME + PCT)) == after
    r.put(FRAME + PCT, 0, 8)
    r.put(FRAME + VCT, 0, 8)
    r.put(FRAME + FRQ, 1)
    assert r.reg(FRAME + PCT, 8) == after
    assert r.reg(FRAME + FRQ) == 19200000


def counter_rate_quantization(r):
    r.enable_access()
    start_ns = r.step()
    start_count = r.reg(FRAME + PCT, 8)
    finish_ns = r.advance_ns(1000000000)
    finish_count = r.reg(FRAME + PCT, 8)
    assert finish_ns - start_ns == 1000000000
    assert finish_count - start_count == r.count_at(finish_ns) - r.count_at(start_ns)
    expected = (19200000,) if r.exact else (19230769, 19230770)
    assert finish_count - start_count in expected
    numerator, denominator = (FREQUENCY, 1) if r.exact else (NSEC, PERIOD)
    r.rate_sample = {
        'virtual_elapsed_ns': finish_ns - start_ns,
        'counter_start': start_count, 'counter_end': finish_count,
        'observed_ticks': finish_count - start_count,
        'nominal_cntfrq_hz': r.reg(FRAME + FRQ),
        'effective_rate_numerator': numerator, 'effective_rate_denominator': denominator,
        'effective_rate_hz': numerator / denominator,
        'error_relative_to_nominal_ppm': (numerator / denominator / FREQUENCY - 1) * 1000000,
    }


def physical_deadline_mask_and_disable(r):
    r.enable_access()
    r.step(1000)
    now = r.reg(FRAME + PCT, 8)
    r.put(FRAME + PTVAL, 100)
    assert r.reg(FRAME + PCVAL, 8) == now + 100
    r.put(FRAME + PCTL, 1)
    r.step(99)
    r.level(0, False)
    assert r.reg(FRAME + PTVAL) == 1
    assert r.reg(FRAME + PCTL) == 1
    r.step(1)
    r.level(0, True)
    assert r.reg(FRAME + PCTL) == 5
    r.put(FRAME + PCTL, 3)
    r.level(0, False)
    assert r.reg(FRAME + PCTL) == 7  # Mask does not clear ISTATUS.
    r.put(FRAME + PCTL, 1)
    r.level(0, True)
    assert r.reg(FRAME + PCTL) == 5  # Writing zero to ISTATUS cannot clear it.
    r.put(FRAME + PCTL, 0)
    r.level(0, False)
    r.step(2)
    assert r.reg(FRAME + PCTL) == 0
    assert r.reg(FRAME + PTVAL) == 0xfffffffe


def first_nanosecond_of_deadline(r):
    r.enable_access()
    r.step(1000)
    count = r.reg(FRAME + PCT, 8)
    deadline = r.time_at(count + 1)
    r.put(FRAME + PTVAL, 1)
    r.put(FRAME + PCTL, 1)
    assert deadline - r.clock_ns > 1
    r.advance_ns(deadline - 1 - r.clock_ns)
    assert r.reg(FRAME + PCT, 8) == count
    r.level(0, False)
    r.advance_ns(1)
    assert r.reg(FRAME + PCT, 8) == count + 1
    r.level(0, True)


def negative_tval_and_split_compare(r):
    r.enable_access()
    r.step(1000)
    now = r.reg(FRAME + PCT, 8)
    r.put(FRAME + PTVAL, 0xffffffff)
    assert r.reg(FRAME + PCVAL, 8) == now - 1
    r.put(FRAME + PCTL, 0xffffffff)
    assert r.reg(FRAME + PCTL) == 7
    r.level(0, False)
    r.put(FRAME + PCTL, 1)
    r.level(0, True)
    r.put(FRAME + PCVAL, 0x89abcdef)
    r.put(FRAME + PCVAL + 4, 0x01234567)
    assert r.reg(FRAME + PCVAL, 8) == 0x0123456789abcdef
    r.level(0, False)
    # Invalid byte/halfword writes must not mutate an architected register.
    r.put(FRAME + PCVAL, 0, 1)
    r.put(FRAME + PCVAL, 0, 2)
    assert r.reg(FRAME + PCVAL, 8) == 0x0123456789abcdef


def el0_second_view_permissions(r):
    r.enable_access()
    r.step(1000)
    assert r.reg(EL0 + PCT, 8) == r.reg(EL0 + FRQ) == 0
    r.put(EL0 + EL0ACR, 0x303)
    assert r.reg(FRAME + EL0ACR) == 0
    r.put(FRAME + EL0ACR, 0xffffffff)
    assert r.reg(FRAME + EL0ACR) == 0x303
    assert r.reg(EL0 + EL0ACR) == 0
    assert r.reg(EL0 + PCT, 8) == r.reg(FRAME + PCT, 8)
    assert r.reg(EL0 + VCT, 8) == r.reg(FRAME + VCT, 8)
    assert r.reg(EL0 + FRQ) == 19200000
    r.put(EL0 + PCVAL, 0x123456789abcdef0, 8)
    assert r.reg(FRAME + PCVAL, 8) == 0x123456789abcdef0
    r.put(CTRL + OFFSET, 123, 8)
    assert r.reg(FRAME + VOFF, 8) == 123
    assert r.reg(EL0 + VOFF, 8) == 0
    r.put(EL0 + VOFF, 456, 8)
    assert r.reg(FRAME + VOFF, 8) == 123
    r.put(CTRL + ACR, 0x3e)
    assert r.reg(FRAME + PCT, 8) == r.reg(EL0 + PCT, 8) == 0
    r.put(FRAME + EL0ACR, 0x300)
    assert r.reg(EL0 + FRQ) == 0
    assert r.reg(FRAME + FRQ) == 19200000
    r.put(FRAME + EL0ACR, 0)
    r.put(EL0 + PCVAL, 0, 8)
    assert r.reg(FRAME + PCVAL, 8) == 0x123456789abcdef0


def virtual_offset_independence(r):
    r.enable_access()
    r.step(10000)
    count = r.reg(FRAME + PCT, 8)
    r.put(CTRL + OFFSET, 0x100000005, 8)
    assert r.reg(FRAME + PCT, 8) == count
    assert r.reg(FRAME + VCT, 8) == (count - 0x100000005) & ((1 << 64) - 1)
    assert r.reg(FRAME + VOFF, 8) == 0x100000005
    r.put(FRAME + VOFF, 0, 8)
    assert r.reg(CTRL + OFFSET, 8) == 0x100000005
    r.put(CTRL + OFFSET + 4, 0)
    r.put(CTRL + OFFSET, 1000)
    assert r.reg(FRAME + VOFF, 8) == 1000
    r.put(FRAME + PTVAL, 90)
    r.put(FRAME + VTVAL, 130)
    r.put(FRAME + PCTL, 1)
    r.put(FRAME + VCTL, 1)
    r.step(90)
    r.level(0, True)
    r.level(1, False)
    r.step(40)
    r.level(1, True)
    # Revoking MMIO access does not stop an already running timer.
    r.put(CTRL + ACR, 0)
    assert r.reg(FRAME + PCTL) == r.reg(FRAME + VCTL) == 0
    r.level(0, True)
    r.level(1, True)
    r.enable_access()
    r.put(FRAME + PCTL, 0)
    r.level(0, False)
    r.level(1, True)


def virtual_wraparound_transition(r):
    r.enable_access()
    r.step(1000)
    count = r.reg(FRAME + PCT, 8)
    r.put(CTRL + OFFSET, count + 100, 8)
    r.put(FRAME + VCVAL, 50, 8)
    r.put(FRAME + VCTL, 1)
    r.level(1, True)  # Unsigned subtraction wraps to a large virtual count.
    r.step(99)
    r.level(1, True)
    r.step(1)
    assert r.reg(FRAME + VCT, 8) == 0
    r.level(1, False)
    r.step(49)
    r.level(1, False)
    r.step(1)
    r.level(1, True)
    # A changed offset immediately reschedules an enabled timer.
    r.put(CTRL + OFFSET, r.reg(FRAME + PCT, 8), 8)
    r.level(1, False)


def reset_preserves_system_counter(r):
    r.enable_access()
    r.step(1000)
    r.put(FRAME + PTVAL, 0)
    r.put(FRAME + PCTL, 1)
    r.put(FRAME + EL0ACR, 0x303)
    r.put(CTRL + OFFSET, 77, 8)
    r.level(0, True)
    before = r.reg(FRAME + PCT, 8)
    r.reset()
    r.level(0, False)
    r.level(1, False)
    assert r.reg(CTRL + ACR) == 0
    r.enable_access()
    assert r.reg(FRAME + PCT, 8) == before
    assert r.reg(FRAME + FRQ) == 19200000
    for off in (PCTL, VCTL, EL0ACR, VOFF, PCVAL, VCVAL):
        assert r.reg(FRAME + off) == 0


TESTS = [capability_and_security, common_counter_and_atomic_reads,
         counter_rate_quantization,
         physical_deadline_mask_and_disable, first_nanosecond_of_deadline,
         negative_tval_and_split_compare,
         el0_second_view_permissions, virtual_offset_independence,
         virtual_wraparound_transition, reset_preserves_system_counter]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--counter-mode', choices=['exact', 'legacy'], default='exact')
    args = parser.parse_args()
    args.qemu = str(Path(args.qemu).resolve())
    args.qom_path = '/machine/mmio-timer'
    args.base = CTRL
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / 'result.json').exists():
        parser.error('use a fresh output directory')
    report = {
        'schema_version': 1, 'suite': 'Arm Generic Timer MMIO actual QTest',
        'qemu_sha256': hashlib.sha256(Path(args.qemu).read_bytes()).hexdigest(),
        'test_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'test_ram': '128 MiB', 'machine': args.machine, 'expected_groups': len(TESTS), 'checks': [],
        'scope': 'MMIO semantics, IRQs and virtual-time deadlines; no real silicon timing',
        'nominal_frequency_hz': 19200000, 'counter_mode': args.counter_mode,
        'qemu_counter_period_ns': None if args.counter_mode == 'exact' else PERIOD,
    }
    start = time.monotonic()
    rig = None
    try:
        rig = TimerRig(args, args.out)
        for test in TESTS:
            rig.reset()
            try:
                test(rig)
                report['checks'].append({'name': test.__name__, 'passed': True})
            except Exception:
                report['checks'].append({'name': test.__name__, 'passed': False,
                                         'error': traceback.format_exc()})
                break
    except Exception:
        report['startup_error'] = traceback.format_exc()
    finally:
        if rig:
            rig.close()
    report['passed'] = (len(report['checks']) == len(TESTS) and
                        all(c['passed'] for c in report['checks']))
    if rig and hasattr(rig, 'rate_sample'):
        report['rate_measurement'] = rig.rate_sample
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (args.out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
