#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the actual TMC APB model against Arm DDI 0461B contracts.

No guest/DT changes, synthetic trace producer, oracle QOM writes or migration
payload edits. The ETR check proves its authentication denial, not AXI tracing.
"""
import argparse
import json
from pathlib import Path
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

ETF = 0x06b05000
ETR = 0x06048000
UNLOCK = 0xc5acce55
RSZ, STS, RRD, RRP, RWP, TRG, CTL, RWD, MODE = (
    0x004, 0x00c, 0x010, 0x014, 0x018, 0x01c, 0x020, 0x024, 0x028)
FFSR, FFCR, ITCTRL = 0x300, 0x304, 0xf00
CLAIMSET, CLAIMCLR, LAR, LSR = 0xfa0, 0xfa4, 0xfb0, 0xfb4
AUTH, DEVID, DEVTYPE = 0xfb8, 0xfc8, 0xfcc
FLUSH, STOP = 1 << 6, 1 << 12
REQUIRED = [
    'original-dt-amba-component-identification',
    'etf-probe-size-width-and-etr-authentication-denial',
    'reset-software-lock-blocks-side-effects',
    'claim-mask-set-clear-and-relock',
    'real-ram-two-lane-alignment-and-wrap',
    'ram-writes-require-disabled-integration-state',
    'strict-width-alignment-holes-and-readonly-registers',
    'all-capture-modes-remain-empty-without-atb-input',
    'disabled-flush-request-is-ignored',
    'manual-flush-is-timed-and-stop-retains-capture-enable',
    'capture-rearm-and-disable-cancels-flush',
    'system-reset-cancels-pending-work-and-clears-reference-ram',
    'disabled-etr-never-starts-or-writes-system-memory',
    'ordinary-checkpoint-preserves-ram-and-both-partial-lanes',
    'ordinary-checkpoint-preserves-pending-flush-and-lock',
    'every-created-child-is-actually-waited-and-reaped',
]


if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')


class TmcRig(Rig):
    # A fresh QTest process starts at zero. Store only actual clock responses.
    clock_ns = 0

    def read_tmc(self, offset, *, base=ETF, width='l'):
        self.count['device_mmio'] += 1
        return int(self.qt(f'read{width} {base + offset:#x}')[0], 0)

    def write_tmc(self, offset, value, *, base=ETF, width='l'):
        self.count['device_mmio'] += 1
        self.qt(f'write{width} {base + offset:#x} {value:#x}')

    def unlock(self, base=ETF):
        self.write_tmc(LAR, UNLOCK, base=base)
        assert self.read_tmc(LSR, base=base) == 1

    def step(self, ns):
        assert ns > 0, 'clock_step requires a strictly positive advance'
        actual = int(self.qt(f'clock_step {ns}')[0], 0)
        assert actual == self.clock_ns + ns
        self.clock_ns = actual
        return actual

    def set_clock(self, ns):
        actual = int(self.qt(f'clock_set {ns}')[0], 0)
        assert actual == ns
        self.clock_ns = actual
        return actual


def save(rig, path):
    clock_ns = rig.clock_ns
    rig.monitor('stop')
    assert rig.monitor('query-status')['running'] is False
    rig.monitor('migrate', file_channels(path))
    assert rig.migrate_wait()['status'] == 'completed'
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path),
                qtest_clock_ns=clock_ns)


def load(rig, path, clock_ns):
    # QTest owns a per-process synthetic clock. Align it before loading the
    # actual VMState absolute timer deadline; do not edit the stream.
    assert rig.set_clock(0) == 0
    assert rig.set_clock(clock_ns) == clock_ns
    rig.monitor('migrate-incoming', file_channels(path))
    assert rig.migrate_wait()['status'] == 'completed'


def reject(rig, offset, *, write=False, width='l', base=ETF):
    """QTest hides MemTxResult: require the model's diagnostic AND no mutation."""
    before = rig.stderr().count('arm-coresight-tmc: rejected')
    if write:
        rig.write_tmc(offset, 0x12345678, width=width, base=base)
    else:
        rig.read_tmc(offset, width=width, base=base)
    diagnostic = rig.stderr()
    assert diagnostic.count('arm-coresight-tmc: rejected') == before + 1
    expected = (f"rejected {'write' if write else 'read'} "
                f"offset={offset:#x} size=")
    last = [line for line in diagnostic.splitlines()
            if 'arm-coresight-tmc: rejected' in line][-1]
    assert expected in last, last
    return dict(base=hex(base), offset=hex(offset), write=write, width=width)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.machine = 'tb371fc,ufs-enabled=off'
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'streams').mkdir()
    report = dict(schema='tb371fc-coresight-tmc-v1', passed=False,
                  qemu_sha256=sha(args.qemu), test_sha256=sha(__file__),
                  checks=[], processes=[],
                  migrations=[], access_rejections=[],
                  original_android_executed=False, guest_cpu_executed=False,
                  atb_producer_present=False, axi_trace_implemented=False,
                  etr_authentication_disabled=True,
                  silicon_ram_geometry_verified=False,
                  migration_payloads_modified=False)

    def check(name, **evidence):
        assert name == REQUIRED[len(report['checks'])], name
        report['checks'].append(dict(name=name, passed=True, **evidence))

    try:
        source = TmcRig(args, out / 'source')
        try:
            r, w = source.read_tmc, source.write_tmc
            for base, devtype in ((ETF, 0x32), (ETR, 0x21)):
                pid = sum(r(0xfe0 + 4 * i, base=base) << (8 * i)
                          for i in range(4))
                cid = sum(r(0xff0 + 4 * i, base=base) << (8 * i)
                          for i in range(4))
                assert pid == 0xbb961 and cid == 0xb105900d
                assert r(0xfd0, base=base) == 4
                assert r(DEVTYPE, base=base) == devtype
            check(REQUIRED[0], etf_address=hex(ETF), etr_address=hex(ETR),
                  periphid=hex(0xbb961))
            assert (r(DEVID) >> 6) & 3 == 2
            assert (r(DEVID) >> 8) & 7 == 3  # 64-bit memory, 32-bit ATB.
            assert r(RSZ) * 4 == 4096 and r(AUTH) == 0
            assert (r(DEVID, base=ETR) >> 6) & 3 == 1
            assert (r(DEVID, base=ETR) >> 8) & 7 == 2
            assert (r(DEVID, base=ETR) >> 11) & 7 == 2  # legal four-beat WBUF.
            assert r(AUTH, base=ETR) == 0x22
            assert r(AUTH, base=ETR) & 3 != 3  # vendor tmc_etr_setup_caps fails.
            check(REQUIRED[1], native_fault_register=hex(ETF + DEVID),
                  etf_memory_width_bits=64, etf_ram_bytes=4096,
                  etr_debug_authorized=False)
            assert r(STS) == 0xc and r(LSR) == 3 and r(CLAIMSET) == 0xf
            assert r(FFSR) == 2 and r(0x308) == 0xa
            for reg, val in ((CLAIMSET, 0xf), (RWP, 0x20), (ITCTRL, 1),
                             (RWD, 0x12345678), (CTL, 1), (FFCR, STOP | FLUSH)):
                w(reg, val)
            assert r(CLAIMCLR) == r(RWP) == r(ITCTRL) == r(CTL) == r(FFCR) == 0
            assert r(RRD) == 0xffffffff and r(RRP) == 0
            w(LAR, UNLOCK ^ 1)
            assert r(LSR) == 3
            check(REQUIRED[2])
            source.unlock()
            w(CLAIMSET, 0x103)
            w(CLAIMSET, 4)
            assert r(CLAIMSET) == 0xf and r(CLAIMCLR) == 7
            w(CLAIMCLR, 0x102)
            assert r(CLAIMCLR) == 5 and r(CLAIMCLR, base=ETR) == 0
            w(LAR, 0)
            w(CLAIMCLR, 0xf)
            assert r(CLAIMCLR) == 5 and r(LSR) == 3
            source.unlock()
            check(REQUIRED[3])
            w(ITCTRL, 1)
            w(RWP, 0x1fff)  # implemented address bits, 16-byte software alignment.
            assert r(RWP) == 0xff0
            pattern = [0x11223344, 0xaabbccdd, 0xffffffff, 0x01234567,
                       0xdeadbeef, 0x0badcafe]
            pointers = [0xff0, 0xff8, 0xff8, 0, 0, 8]
            for word, pointer in zip(pattern, pointers):
                w(RWD, word)
                assert r(RWP) == pointer
            w(RRP, 0x1fff)
            actual = []
            for pointer in pointers:
                actual.append(r(RRD))
                assert r(RRP) == pointer
            assert actual == pattern
            assert r(STS) == 0xd and r(0x02c) == r(0x030) == 0
            # A partial write is not visible. An in-progress read latches the
            # whole memory beat, even if a write commits between its two reads.
            w(RWP, 0x80)
            w(RWD, 0xaaaaaaaa)
            assert r(RWP) == 0x80
            w(RRP, 0x80)
            assert r(RRD) == 0 and r(RRP) == 0x80
            w(RWD, 0xbbbbbbbb)
            assert r(RWP) == 0x88
            assert r(RRD) == 0 and r(RRP) == 0x88
            w(RRP, 0x80)
            assert [r(RRD), r(RRD)] == [0xaaaaaaaa, 0xbbbbbbbb]
            # Reprogramming a pointer discards an incomplete port beat.
            w(RWP, 0xc0)
            w(RWD, 0x55555555)
            w(RWP, 0xd0)
            w(RWD, 0x11111111)
            w(RWD, 0x22222222)
            w(RRP, 0xc0)
            assert [r(RRD), r(RRD)] == [0, 0]
            w(RRP, 0xd0)
            assert [r(RRD), r(RRD)] == [0x11111111, 0x22222222]
            # Locking does not consume either partially transferred beat.
            w(RWP, 0xe0)
            w(RWD, 0x33333333)
            w(LAR, 0)
            w(RWD, 0x44444444)
            source.unlock()
            assert r(RWP) == 0xe0
            w(RWD, 0x44444444)
            w(RRP, 0xe0)
            assert r(RRD) == 0x33333333
            w(LAR, 0)
            assert r(RRD) == 0xffffffff and r(RRP) == 0xe0
            source.unlock()
            assert r(RRD) == 0x44444444 and r(RRP) == 0xe8
            check(REQUIRED[4], words=actual, byte_pointers=pointers,
                  whole_beat_commit=True, latched_read_beat=True,
                  full_latch_after_write_wrap=True)
            w(ITCTRL, 0)
            w(RWP, 0x40)
            w(RWD, 0x76543210)
            assert r(RWP) == 0x40
            w(RRP, 0x40)
            assert r(RRD) == 0
            w(ITCTRL, 1)
            w(CTL, 1)  # integration mode cannot start an ATB capture.
            assert r(CTL) == 0
            check(REQUIRED[5])
            before = (r(RWP), r(RRP), r(CLAIMCLR), r(CTL), r(ITCTRL))
            for offset, write, width in (
                    (DEVID, False, 'b'), (DEVID, False, 'w'),
                    (DEVID, False, 'q'), (DEVID + 1, False, 'l'),
                    (0x008, False, 'l'), (0x100, True, 'l'),
                    (RWD, True, 'b'), (RWD, True, 'w'), (RWD, True, 'q'),
                    (RWD + 1, True, 'l'), (DEVID, True, 'l'),
                    (RSZ, True, 'l'), (STS, True, 'l'),
                    (FFCR, True, 'l')):  # includes unsupported DrainBuffer.
                report['access_rejections'].append(
                    reject(source, offset, write=write, width=width))
            assert (r(RWP), r(RRP), r(CLAIMCLR), r(CTL), r(ITCTRL)) == before
            check(REQUIRED[6], rejected_accesses=len(report['access_rejections']))
            source.monitor('cont')
            w(ITCTRL, 0)
            for mode in (0, 1, 2):
                w(MODE, mode)
                w(RWP, 0x88)
                w(TRG, 0x7ff)
                assert r(TRG) == 0x3ff
                w(FFCR, 3)
                w(CTL, 1)
                start = 0x80 if mode == 0 else 0
                assert r(CTL) == 1 and r(STS) == 0x10 and r(FFSR) == 0
                assert r(RRD) == 0xffffffff and r(RRP) == r(RWP) == start
                w(FFCR, 0)
                assert r(FFCR) == 3  # EnFt/EnTI only change while Disabled.
                w(RWD, 0x55555555)
                w(RRP, 0x100)
                w(RWP, 0x100)
                w(MODE, (mode + 1) % 3)
                assert r(MODE) == mode and r(RRP) == r(RWP) == start
                assert r(0x02c) == r(0x030) == 0
                source.step(10000)
                assert r(STS) == 0x10 and r(TRG) == 0x3ff
                w(CTL, 0)
                assert r(TRG) == 0 and r(STS) == 0xc
            check(REQUIRED[7], modes=[0, 1, 2], captured_words=0)
            w(FFCR, STOP | FLUSH)
            assert r(FFCR) == STOP and r(FFSR) == 2 and r(STS) == 0xc
            source.step(10000)
            assert r(FFSR) == 2 and r(STS) == 0xc
            check(REQUIRED[8])
            w(MODE, 0)
            w(RWP, 0)
            w(TRG, 123)
            w(FFCR, 0x133)
            w(CTL, 1)
            w(FFCR, r(FFCR) | FLUSH)
            source.step(1000)
            assert r(STS) == 0x10 and r(FFSR) == 0 and r(TRG) == 123
            # The vendor and TRM sequence sets StopOnFl before ManualFlush.
            w(FFCR, r(FFCR) | STOP)
            w(FFCR, r(FFCR) | FLUSH)
            assert r(FFCR) & FLUSH and r(FFSR) == 1 and r(STS) == 0x10
            source.step(999)
            assert r(FFCR) & FLUSH and r(FFSR) == 1
            source.step(1)
            assert r(FFCR) == 0x133 | STOP and r(FFSR) == 2
            assert r(STS) == 0x1c and r(CTL) == 1 and r(TRG) == 0
            assert r(RRD) == 0xffffffff and r(RRP) == 0
            w(CTL, 1)
            assert r(STS) == 0x1c  # Stopped is not restarted by rewriting one.
            check(REQUIRED[9], flush_virtual_ns=1000)
            w(CTL, 0)
            w(CTL, 1)
            assert r(STS) == 0x10
            w(FFCR, STOP | FLUSH)
            w(CTL, 0)
            assert r(FFSR) == 2 and r(FFCR) & FLUSH == 0
            w(CTL, 1)
            source.step(10000)
            assert r(STS) == 0x10
            check(REQUIRED[10])
            w(FFCR, STOP | FLUSH)
            source.monitor('system_reset')
            assert r(LSR) == 3 and r(CTL) == r(FFCR) == r(CLAIMCLR) == 0
            assert r(0x308) == 0xa and r(FFSR) == 2
            source.step(10000)
            assert r(STS) == 0xc
            source.unlock()
            w(RRP, 0xff0)
            assert [r(RRD) for _ in range(4)] == [0] * 4
            check(REQUIRED[11])
            canary = bytes(range(64))
            source.write_ram(0x80001000, canary)
            source.unlock(ETR)
            source.write_tmc(CTL, 1, base=ETR)
            assert source.read_tmc(CTL, base=ETR) == 0
            assert source.read_tmc(STS, base=ETR) == 0xc
            assert source.read_tmc(RRD, base=ETR) == 0xffffffff
            for reg in (RWD, 0x110, 0x118):
                report['access_rejections'].append(
                    reject(source, reg, write=True, base=ETR))
            source.step(10000)
            assert source.read_ram(0x80001000, 64) == canary
            assert source.read_tmc(AUTH, base=ETR) == 0x22
            check(REQUIRED[12], system_ram_canary_unchanged=True)
            # Save independent read and write lane positions within 64-bit beats.
            w(ITCTRL, 1)
            w(RWP, 0x20)
            w(RWD, 0x13579bdf)
            w(RWD, 0x2468ace0)
            w(RRP, 0x20)
            assert r(RRD) == 0x13579bdf and r(RRP) == 0x20
            w(RWP, 0x100)
            w(RWD, 0xfeedface)
            assert r(RWP) == 0x100
            w(CLAIMSET, 9)
            first = out / 'streams/partial-lanes.mig'
            report['migrations'].append(save(source, first))
            assert r(RRD) == 0x2468ace0 and r(RRP) == 0x28
            w(RWD, 0xc001d00d)
            assert r(RWP) == 0x108
        finally:
            source.close()

        target = TmcRig(args, out / 'partial-target', incoming=True)
        try:
            load(target, first, report['migrations'][0]['qtest_clock_ns'])
            r, w = target.read_tmc, target.write_tmc
            assert r(LSR) == 1 and r(CLAIMCLR) == 9 and r(ITCTRL) == 1
            assert r(RRP) == 0x20 and r(RWP) == 0x100
            assert r(RRD) == 0x2468ace0 and r(RRP) == 0x28
            w(RWD, 0xc001d00d)
            assert r(RWP) == 0x108
            w(RRP, 0x100)
            assert [r(RRD), r(RRD)] == [0xfeedface, 0xc001d00d]
            assert sha(first) == report['migrations'][0]['sha256']
            check(REQUIRED[13], source_and_destination_continuation_equal=True)
            w(ITCTRL, 0)
            w(CTL, 1)
            target.monitor('cont')
            w(FFCR, STOP | FLUSH)
            target.step(400)
            w(LAR, 0)
            assert r(LSR) == 3 and r(FFSR) == 1
            second = out / 'streams/pending-flush.mig'
            report['migrations'].append(save(target, second))
        finally:
            target.close()

        final = TmcRig(args, out / 'flush-target', incoming=True)
        try:
            load(final, second, report['migrations'][1]['qtest_clock_ns'])
            r, w = final.read_tmc, final.write_tmc
            assert r(LSR) == 3 and r(CTL) == 1 and r(FFSR) == 1
            assert r(STS) == 0x10
            final.monitor('cont')
            final.step(599)
            assert r(FFSR) == 1
            final.step(1)
            assert r(FFSR) == 2 and r(FFCR) == STOP
            assert r(CTL) == 1 and r(STS) == 0x1c and r(LSR) == 3
            w(CTL, 0)
            assert r(CTL) == 1  # lock itself survived.
            final.unlock()
            w(CTL, 0)
            assert r(CTL) == 0
            assert sha(second) == report['migrations'][1]['sha256']
            check(REQUIRED[14], remaining_flush_virtual_ns=600)
        finally:
            final.close()
        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 3
        assert all(p['counters']['qom_set'] == 0 for p in report['processes'])
        assert sha(args.qemu) == report['qemu_sha256']
        assert sha(__file__) == report['test_sha256']
        check(REQUIRED[15], processes=3, forced_cleanup=False)
        assert [x['name'] for x in report['checks']] == REQUIRED
        report['passed'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()
    finally:
        put_json(out / 'result.json', report)
    if not report['passed']:
        print(report.get('traceback', report.get('error', 'Unknown failure')))
    print(json.dumps(dict(passed=report['passed'], checks=len(report['checks']),
                          error=report.get('error'), report=str(out / 'result.json'))))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
