#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check the original DT's two CoreSight dynamic-replicator APB controls.

Exercises the real MMIO device, the vendor claim/filter protocol and an ordinary
process checkpoint. No ATB producer, guest patch, QOM mutation or stream edit.
"""
import argparse
import json
from pathlib import Path
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

QDSS = 0x06046000
SWAO = 0x06b06000
BASES = (QDSS, SWAO)
IDFILTER0, IDFILTER1 = 0, 4
CLAIMSET, CLAIMCLR, LAR, LSR = 0xfa0, 0xfa4, 0xfb0, 0xfb4
AUTH, DEVID, DEVTYPE = 0xfb8, 0xfc8, 0xfcc
ITCTRL = 0xf00
UNLOCK = 0xc5acce55
CLAIM_MASK = 0xf
REQUIRED = [
    'both-original-dt-amba-identities',
    'reset-lock-and-filter-defaults',
    'vendor-probe-reset-claims-blocks-both-ports-and-relocks',
    'independent-eight-bit-filters-and-claim-masks',
    'external-claim-blocks-vendor-probe-and-first-enable',
    'vendor-two-port-enable-disable-claim-lifetime',
    'strict-width-alignment-holes-and-readonly-no-mutation',
    'system-reset-restores-both-independent-instances',
    'ordinary-checkpoint-preserves-filters-claims-lock-and-continuation',
    'every-created-child-is-actually-waited-and-reaped',
]

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')


class ReplicatorRig(Rig):
    def read_rep(self, offset, *, base=QDSS, width='l'):
        self.count['device_mmio'] += 1
        return int(self.qt(f'read{width} {base + offset:#x}')[0], 0)

    def write_rep(self, offset, value, *, base=QDSS, width='l'):
        self.count['device_mmio'] += 1
        self.qt(f'write{width} {base + offset:#x} {value:#x}')

    def unlock(self, base):
        self.write_rep(LAR, UNLOCK, base=base)
        assert self.read_rep(LSR, base=base) == 1

    def lock(self, base):
        self.write_rep(LAR, 0, base=base)
        assert self.read_rep(LSR, base=base) == 3

    def claim(self, base):
        # Same self-hosted claim protocol as the original kernel: refuse an
        # already claimed component, set bit 1, then read back ownership.
        if self.read_rep(CLAIMCLR, base=base):
            return False
        self.write_rep(CLAIMSET, 2, base=base)
        if self.read_rep(CLAIMCLR, base=base) == 2:
            return True
        self.write_rep(CLAIMCLR, 2, base=base)
        return False

    def probe_reset(self, base):
        self.unlock(base)
        owned = self.claim(base)
        if owned:
            self.write_rep(IDFILTER0, 0xff, base=base)
            self.write_rep(IDFILTER1, 0xff, base=base)
            assert self.read_rep(CLAIMCLR, base=base) == 2
            self.write_rep(CLAIMCLR, 2, base=base)
        self.lock(base)
        return owned

    def enable_port(self, base, port):
        assert port in (0, 1)
        self.unlock(base)
        both_disabled = all(self.read_rep(offset, base=base) == 0xff
                            for offset in (IDFILTER0, IDFILTER1))
        owned = self.claim(base) if both_disabled else True
        if owned:
            self.write_rep(port * 4, 0, base=base)
        self.lock(base)
        return owned

    def disable_port(self, base, port):
        assert port in (0, 1)
        self.unlock(base)
        self.write_rep(port * 4, 0xff, base=base)
        if all(self.read_rep(offset, base=base) == 0xff
               for offset in (IDFILTER0, IDFILTER1)):
            assert self.read_rep(CLAIMCLR, base=base) == 2
            self.write_rep(CLAIMCLR, 2, base=base)
        self.lock(base)

    def state(self):
        return [[self.read_rep(offset, base=base)
                 for offset in (IDFILTER0, IDFILTER1, CLAIMCLR, LSR)]
                for base in BASES]


def reject(rig, base, offset, *, write=False, width='l', value=0x12345678):
    before = rig.stderr().count('arm-coresight-replicator: rejected')
    if write:
        rig.write_rep(offset, value, base=base, width=width)
    else:
        rig.read_rep(offset, base=base, width=width)
    lines = rig.stderr()
    assert lines.count('arm-coresight-replicator: rejected') == before + 1
    last = [s for s in lines.splitlines()
            if 'arm-coresight-replicator: rejected' in s][-1]
    assert f"rejected {'write' if write else 'read'} offset={offset:#x} size=" in last
    return dict(base=hex(base), offset=hex(offset), write=write, width=width)


def continuation(rig):
    # The busy QDSS owner survives; only explicitly clearing it permits reset.
    assert rig.probe_reset(QDSS) is False
    rig.unlock(QDSS)
    rig.write_rep(CLAIMCLR, 5)
    assert rig.probe_reset(QDSS)
    assert rig.enable_port(QDSS, 0)
    assert rig.enable_port(SWAO, 1)
    return rig.state()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.machine = 'tb371fc,ufs-enabled=off'
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'streams').mkdir()
    report = dict(schema='tb371fc-coresight-replicator-v1', passed=False,
                  qemu_sha256=sha(args.qemu), test_sha256=sha(__file__),
                  checks=[], processes=[], migrations=[], access_rejections=[],
                  original_android_executed=False, guest_cpu_executed=False,
                  atb_producer_present=False, atb_routing_implemented=False,
                  migration_payloads_modified=False)

    def check(name, **evidence):
        assert name == REQUIRED[len(report['checks'])], name
        report['checks'].append(dict(name=name, passed=True, **evidence))

    try:
        source = ReplicatorRig(args, out / 'source')
        try:
            r, w = source.read_rep, source.write_rep
            for base in BASES:
                pid = sum(r(0xfe0 + 4 * i, base=base) << (8 * i)
                          for i in range(4))
                cid = sum(r(0xff0 + 4 * i, base=base) << (8 * i)
                          for i in range(4))
                assert pid == 0x1bb909 and cid == 0xb105900d
                assert pid & 0xfffff == 0xbb909  # DT override / AMBA match mask.
                assert r(0xfd0, base=base) == 4
                assert r(DEVTYPE, base=base) == 0x22
                assert r(DEVID, base=base) == 2 and r(AUTH, base=base) == 0
                assert r(ITCTRL, base=base) == 0
            check(REQUIRED[0], addresses=[hex(x) for x in BASES],
                  dt_periphid=hex(0xbb909), reference_periphid=hex(0x1bb909),
                  native_fault_address=hex(QDSS + LAR))
            assert source.state() == [[0, 0, 0, 3], [0, 0, 0, 3]]
            for base in BASES:
                assert r(CLAIMSET, base=base) == CLAIM_MASK
                for offset in (IDFILTER0, IDFILTER1, CLAIMSET, CLAIMCLR, ITCTRL):
                    w(offset, 0xffffffff, base=base)
                w(LAR, UNLOCK ^ 1, base=base)
            assert source.state() == [[0, 0, 0, 3], [0, 0, 0, 3]]
            check(REQUIRED[1])
            for base in BASES:
                assert source.probe_reset(base)
            assert source.state() == [[255, 255, 0, 3], [255, 255, 0, 3]]
            check(REQUIRED[2])
            source.unlock(QDSS)
            w(IDFILTER0, 0x123456a5)
            w(IDFILTER1, 0xffff005a)
            w(CLAIMSET, 0xffff0005)
            w(CLAIMSET, 2)
            assert r(CLAIMSET) == CLAIM_MASK and r(CLAIMCLR) == 7
            w(CLAIMCLR, 0xffff0002)
            source.lock(QDSS)
            w(IDFILTER0, 0)
            w(CLAIMCLR, 5)
            assert source.state() == [[165, 90, 5, 3], [255, 255, 0, 3]]
            check(REQUIRED[3])
            before = source.state()
            assert source.probe_reset(QDSS) is False
            assert source.state() == before
            source.unlock(QDSS)
            w(CLAIMCLR, 5)
            assert source.probe_reset(QDSS)
            source.unlock(QDSS)
            w(CLAIMSET, 1)
            source.lock(QDSS)
            before = source.state()
            assert source.enable_port(QDSS, 0) is False
            assert source.state() == before
            source.unlock(QDSS)
            w(CLAIMCLR, 1)
            source.lock(QDSS)
            check(REQUIRED[4])
            for base in BASES:
                assert source.enable_port(base, 0)
                assert [r(x, base=base) for x in (0, 4, CLAIMCLR, LSR)] == [0, 255, 2, 3]
                assert source.enable_port(base, 1)
                assert [r(x, base=base) for x in (0, 4, CLAIMCLR, LSR)] == [0, 0, 2, 3]
                source.disable_port(base, 0)
                assert [r(x, base=base) for x in (0, 4, CLAIMCLR, LSR)] == [255, 0, 2, 3]
                source.disable_port(base, 1)
                assert [r(x, base=base) for x in (0, 4, CLAIMCLR, LSR)] == [255, 255, 0, 3]
            check(REQUIRED[5])
            for base in BASES:
                source.unlock(base)
                w(ITCTRL, 0, base=base)
                assert r(ITCTRL, base=base) == 0
            before = source.state()
            for base in BASES:
                for offset, write, width in (
                        (IDFILTER0, True, 'b'), (IDFILTER0, True, 'w'),
                        (IDFILTER0, True, 'q'), (IDFILTER0 + 1, True, 'l'),
                        (LAR, True, 'q'), (LAR + 1, True, 'l'),
                        (LSR, False, 'b'), (LSR, False, 'w'), (LSR, False, 'q'),
                        (LSR + 1, False, 'l'), (LAR, False, 'l'),
                        (0x008, False, 'l'), (0x008, True, 'l'),
                        (0xef8, True, 'l'), (0xf00, True, 'l'),
                        (DEVTYPE, True, 'l'), (DEVID, True, 'l'),
                        (AUTH, True, 'l'), (0xfe0, True, 'l')):
                    report['access_rejections'].append(
                        reject(source, base, offset, write=write, width=width,
                               value=1 if offset == ITCTRL else 0x12345678))
            assert source.state() == before
            check(REQUIRED[6], rejected_accesses=len(report['access_rejections']))
            w(IDFILTER0, 0x17)
            w(CLAIMSET, 9)
            w(IDFILTER1, 0xe3, base=SWAO)
            w(CLAIMSET, 6, base=SWAO)
            source.monitor('system_reset')
            assert source.state() == [[0, 0, 0, 3], [0, 0, 0, 3]]
            check(REQUIRED[7])
            source.unlock(QDSS)
            w(IDFILTER0, 0x12)
            w(IDFILTER1, 0x34)
            w(CLAIMSET, 5)
            source.lock(QDSS)
            source.unlock(SWAO)
            w(IDFILTER0, 0xff, base=SWAO)
            w(IDFILTER1, 0xff, base=SWAO)
            saved = source.state()
            assert saved == [[18, 52, 5, 3], [255, 255, 0, 1]]
            stream = out / 'streams/replicator.mig'
            source.monitor('stop')
            assert source.monitor('query-status')['running'] is False
            source.monitor('migrate', file_channels(stream))
            assert source.migrate_wait()['status'] == 'completed'
            migration = dict(path=str(stream), bytes=stream.stat().st_size,
                             sha256=sha(stream), payload_modified=False)
            report['migrations'].append(migration)
            expected = continuation(source)
        finally:
            source.close()
        target = ReplicatorRig(args, out / 'target', incoming=True)
        try:
            target.monitor('migrate-incoming', file_channels(stream))
            assert target.migrate_wait()['status'] == 'completed'
            assert target.state() == saved
            assert continuation(target) == expected
            assert expected == [[0, 255, 2, 3], [255, 0, 2, 3]]
            assert sha(stream) == migration['sha256']
            check(REQUIRED[8], saved_state=saved, continued_state=expected,
                  ordinary_migration=True)
        finally:
            target.close()
        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 2
        assert all(p['counters']['qom_set'] == 0 for p in report['processes'])
        assert sha(args.qemu) == report['qemu_sha256']
        assert sha(__file__) == report['test_sha256']
        check(REQUIRED[9], processes=2, forced_cleanup=False)
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
