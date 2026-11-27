#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise all five original-DT TPDA management apertures through real QTest.

The fixed authentication-disabled profile must refuse the vendor predicate
before any TPDA configuration. This is not execution of the Linux probe, a
trace datapath test, or a malformed-migration-stream test.
"""
import argparse
import json
from pathlib import Path
import re
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

BASES = (0x06004000, 0x069c1000, 0x06ac1000, 0x06b08000, 0x07863000)
NAMES = ('main', 'dl-south', 'dl-north', 'swao', 'apss')
CLAIMSET, CLAIMCLR, LAR, LSR = 0xfa0, 0xfa4, 0xfb0, 0xfb4
AUTH, DEVARCH, DEVID, DEVTYPE = 0xfb8, 0xfbc, 0xfc8, 0xfcc
ITCTRL = 0xf00
UNLOCK = 0xc5acce55
AUTH_DISABLED = 0x88
UNSUPPORTED = (0x000, 0x004, 0x080, 0x084, 0x088, 0x08c,
               0x090, 0x094, 0x098, 0xefc, DEVID, DEVTYPE)
REQUIRED = [
    'five-original-dt-amba-identities-and-management-apertures',
    'reset-lock-claim-mask-and-fixed-noninvasive-authentication',
    'original-vendor-authentication-predicate-rejects-before-configuration',
    'unlocked-independent-claims-and-absent-integration',
    'software-lock-blocks-claims-controls-and-partial-wide-unlock',
    'strict-apb-unsupported-datapath-and-aperture-crossing-no-mutation',
    'system-reset-restores-five-management-instances',
    'ordinary-checkpoint-preserves-distinct-claims-locks-and-continuation',
    'every-created-child-is-actually-waited-and-reaped',
]

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')


class TPDARig(Rig):
    def read_tpda(self, offset, *, base=BASES[0], width='l'):
        self.count['device_mmio'] += 1
        return int(self.qt(f'read{width} {base + offset:#x}')[0], 0)

    def write_tpda(self, offset, value, *, base=BASES[0], width='l'):
        self.count['device_mmio'] += 1
        self.qt(f'write{width} {base + offset:#x} {value:#x}')

    def unlock(self, base):
        self.write_tpda(LAR, UNLOCK, base=base)
        assert self.read_tpda(LSR, base=base) == 1

    def lock(self, base):
        self.write_tpda(LAR, 0, base=base)
        assert self.read_tpda(LSR, base=base) == 3

    def state(self):
        return [[self.read_tpda(offset, base=base)
                 for offset in (CLAIMCLR, LSR, AUTH, DEVARCH)]
                for base in BASES]


def verify_apertures(rig, out):
    flat = rig.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    path = out / 'memory-map.txt'
    path.write_text(flat)
    rows = []
    for line in flat.splitlines():
        if not line.rstrip().endswith(': qcom-coresight-tpda'):
            continue
        match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
        assert match, line
        rows.append((int(match[1], 16), int(match[2], 16)))
    expected = {(base, base + 0xfff) for base in BASES}
    assert rows and set(rows) == expected, rows
    # Probe each side of the final bank separately. QEMU deliberately does
    # not clamp full-width MMIO transactions at MemoryRegion boundaries.
    before_state = rig.state()
    neighbors = []
    for address in (BASES[-1] - 4, BASES[-1] + 0x1000):
        prior = rig.stderr()
        rig.count['device_mmio'] += 1
        rig.qt(f'readl {address:#x}')
        added = rig.stderr()[len(prior):]
        assert f'Invalid read at addr {address:#x}'.lower() in added.lower(), added
        assert 'qcom-coresight-tpda: rejected' not in added
        assert rig.state() == before_state
        neighbors.append(dict(address=hex(address), size=4,
                              diagnostic=added.strip()))
    return dict(regions=[[hex(a), hex(b)] for a, b in sorted(expected)],
                boundary_rejections=neighbors,
                flatmap=dict(path=str(path), bytes=path.stat().st_size,
                             sha256=sha(path)))


def reject(rig, base, offset, *, write=False, width='l', value=0x12345678):
    state = rig.state()
    before = rig.stderr()
    prior = before.count('qcom-coresight-tpda: rejected')
    if write:
        rig.write_tpda(offset, value, base=base, width=width)
    else:
        rig.read_tpda(offset, base=base, width=width)
    after = rig.stderr()
    assert after.count('qcom-coresight-tpda: rejected') == prior + 1
    last = [line for line in after.splitlines()
            if 'qcom-coresight-tpda: rejected' in line][-1]
    assert f"rejected {'write' if write else 'read'} offset={offset:#x} size=" in last
    assert rig.state() == state
    return dict(base=hex(base), offset=hex(offset), write=write, width=width,
                diagnostic=after[len(before):].strip())


def vendor_auth_rejection(rig, base):
    # Actual device read, followed by the fixed vendor's exact four-field test.
    # This deliberately does not claim that qtest executed Linux probe code.
    before = rig.count['qtest']
    value = rig.read_tpda(AUTH, base=base)
    fields = [(value >> bit) & 3 for bit in (0, 2, 4, 6)]
    rejected = any(field == 2 for field in fields)
    assert rig.count['qtest'] == before + 1
    assert value == AUTH_DISABLED and fields == [0, 2, 0, 2] and rejected
    return dict(base=hex(base), authstatus=value, fields=fields,
                expected_probe_errno=-1, configuration_writes=0)


def continuation(rig):
    sets, clears = (4, 1, 8, 2, 2), (1, 4, 3, 8, 1)
    for base, set_mask, clear_mask in zip(BASES, sets, clears):
        assert vendor_auth_rejection(rig, base)['expected_probe_errno'] == -1
        rig.unlock(base)
        rig.write_tpda(CLAIMSET, set_mask, base=base)
        rig.write_tpda(CLAIMCLR, clear_mask, base=base)
        rig.lock(base)
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
    report = dict(schema='tb371fc-coresight-tpda-v1', passed=False,
                  qemu_sha256=sha(args.qemu), test_sha256=sha(__file__),
                  checks=[], processes=[], migrations=[], access_rejections=[],
                  original_android_executed=False, guest_cpu_executed=False,
                  authentication_status=AUTH_DISABLED,
                  authentication_fields=[0, 2, 0, 2],
                  authentication_is_model_configuration=True,
                  invasive_authentication_implemented=False,
                  noninvasive_authentication_enabled=False,
                  trace_aggregation_implemented=False, atb_output_implemented=False,
                  dma_implemented=False, integration_implemented=False,
                  migration_payloads_modified=False)

    def check(name, **evidence):
        assert name == REQUIRED[len(report['checks'])], name
        report['checks'].append(dict(name=name, passed=True, **evidence))

    try:
        source = TPDARig(args, out / 'source')
        try:
            r, w = source.read_tpda, source.write_tpda
            apertures = verify_apertures(source, out)
            for base in BASES:
                pid = sum(r(0xfe0 + 4 * i, base=base) << (8 * i)
                          for i in range(4))
                cid = sum(r(0xff0 + 4 * i, base=base) << (8 * i)
                          for i in range(4))
                assert pid == 0xbb969 and cid == 0xb105900d
                assert (pid & 0x3ffff) == 0x3b969
                assert r(0xfd0, base=base) == 4
                for offset in (0xfd4, 0xfd8, 0xfdc, 0xfec):
                    assert r(offset, base=base) == 0
                assert r(DEVARCH, base=base) == 0
            check(REQUIRED[0], addresses=[hex(x) for x in BASES],
                  instance_names=list(NAMES), periphid=hex(0xbb969),
                  vendor_match_id=hex(0x3b969), vendor_match_mask=hex(0x3ffff),
                  native_fault_address=hex(0x06b08000 + AUTH), apertures=apertures)
            reset = [[0, 3, AUTH_DISABLED, 0] for _ in BASES]
            assert source.state() == reset
            for base in BASES:
                assert r(CLAIMSET, base=base) == 0xf
                assert r(ITCTRL, base=base) == 0
                w(CLAIMSET, 0xffffffff, base=base)
                w(CLAIMCLR, 0xffffffff, base=base)
                w(LAR, UNLOCK ^ 1, base=base)
            assert source.state() == reset
            check(REQUIRED[1], authstatus=AUTH_DISABLED, auth_fields=[0, 2, 0, 2],
                  claim_implemented_bits=4, reset_lsr=3, immutable_authentication=True)
            probes = [vendor_auth_rejection(source, base) for base in BASES]
            assert source.state() == reset
            check(REQUIRED[2], predicate_reads=probes, probe_code_executed=False,
                  tpda_registration_claimed=False)

            for i, base in enumerate(BASES):
                source.unlock(base)
                w(CLAIMSET, 0xffff0000 | (i + 1), base=base)
                w(CLAIMSET, 8, base=base)
                assert r(CLAIMSET, base=base) == 0xf
                assert r(CLAIMCLR, base=base) == ((i + 1) | 8)
                w(CLAIMCLR, 0xffff0008, base=base)
                for value in (0, 1, 0xffffffff):
                    w(ITCTRL, value, base=base)
                    assert r(ITCTRL, base=base) == 0
            assert source.state() == [[i + 1, 1, AUTH_DISABLED, 0]
                                      for i in range(len(BASES))]
            check(REQUIRED[3], claims=[1, 2, 3, 4, 5],
                  integration_control_raz_wi=True)

            for base in BASES:
                source.lock(base)
            before = source.state()
            for base in BASES:
                for offset in (CLAIMSET, CLAIMCLR, AUTH, DEVARCH, ITCTRL) + UNSUPPORTED:
                    w(offset, 0xffffffff, base=base)
                w(LAR, UNLOCK ^ 0x100, base=base)
            assert source.state() == before
            report['access_rejections'].append(
                reject(source, BASES[0], LAR, write=True, width='q', value=UNLOCK))
            assert source.state() == before
            check(REQUIRED[4], known_control_writes_ignored_while_locked=True,
                  wide_unlock_rejected_without_partial_state=True)

            for base in BASES:
                source.unlock(base)
            cases = []
            for width in ('b', 'w', 'q'):
                cases.extend([(CLAIMSET, False, width), (CLAIMSET, True, width)])
            for offset in (CLAIMSET + 1, LAR + 2):
                cases.extend([(offset, False, 'l'), (offset, True, 'l')])
            for offset in UNSUPPORTED:
                cases.extend([(offset, False, 'l'), (offset, True, 'l')])
            for offset in (AUTH, DEVARCH, LSR, 0xfe0, 0xffc):
                cases.append((offset, True, 'l'))
            cases.append((LAR, False, 'l'))
            for offset in (0x09c, 0xf04, 0xfc0):
                cases.extend([(offset, False, 'l'), (offset, True, 'l')])
            assert len(cases) == 46
            for offset, write, width in cases:
                report['access_rejections'].append(
                    reject(source, BASES[0], offset, write=write, width=width))
            # Request eight bytes across the actual final APB word through
            # AddressSpace. MMIO translation retains the full width, so the
            # model must reject all eight bytes without partial state changes.
            # The independent neighbor reads above prove the unassigned edges.
            boundary = reject(source, BASES[-1], 0xffc, write=True, width='q',
                              value=0xfeedfacec5acce55)
            assert boundary['diagnostic'] == \
                'qcom-coresight-tpda: rejected write offset=0xffc size=8'
            assert r(0xffc, base=BASES[-1]) == 0xb1
            boundary['crosses_aperture'] = True
            boundary['dispatch_size'] = 8
            boundary['adjacent_region_dispatched'] = False
            report['access_rejections'].append(boundary)
            assert len(report['access_rejections']) == 48
            check(REQUIRED[5], strict_rejected_accesses=46,
                  total_rejected_accesses=48, aperture_crossing=boundary,
                  datapath_configuration_implemented=False, flush_completion_claimed=False)

            source.monitor('system_reset')
            assert source.state() == reset
            for base in BASES:
                assert r(CLAIMSET, base=base) == 0xf
                assert vendor_auth_rejection(source, base)['expected_probe_errno'] == -1
            check(REQUIRED[6])

            for i, base in enumerate(BASES):
                source.unlock(base)
                w(CLAIMSET, 1 + 3 * i, base=base)
                if i % 2 == 0:
                    source.lock(base)
            saved = source.state()
            assert saved == [[1, 3, 136, 0], [4, 1, 136, 0], [7, 3, 136, 0],
                             [10, 1, 136, 0], [13, 3, 136, 0]]
            stream = out / 'streams/tpda.mig'
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

        target = TPDARig(args, out / 'target', incoming=True)
        try:
            target.monitor('migrate-incoming', file_channels(stream))
            assert target.migrate_wait()['status'] == 'completed'
            assert target.state() == saved
            assert continuation(target) == expected
            assert expected == [[4, 3, 136, 0], [1, 3, 136, 0], [12, 3, 136, 0],
                                [2, 3, 136, 0], [14, 3, 136, 0]]
            assert sha(stream) == migration['sha256']
            check(REQUIRED[7], saved_state=saved, continued_state=expected,
                  ordinary_migration=True, authentication_status=AUTH_DISABLED)
        finally:
            target.close()

        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 2
        assert all(p['counters']['qom_set'] == 0 for p in report['processes'])
        assert sha(args.qemu) == report['qemu_sha256']
        assert sha(__file__) == report['test_sha256']
        check(REQUIRED[8], processes=2, forced_cleanup=False)
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
