#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise all 28 original-DT TPDM management apertures through real QTest.

The virtual profile exposes zero collection datasets. The original DT's AMBA
match overrides are distinct from the actual PIDR0 capability reads. This
does not execute Linux probe code, produce trace, or mutate migration payloads.
"""
import argparse
import json
from pathlib import Path
import re
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

BASES = (
    0x06830000, 0x06840000, 0x06844000, 0x0684c000, 0x06850000, 0x06870000, 0x06900000,
    0x06980000, 0x06981000, 0x069c0000, 0x069d0000, 0x06ac0000, 0x06b09000, 0x06b0a000,
    0x06c08000, 0x06c28000, 0x06c29000, 0x06c40000, 0x06c41000, 0x06c47000, 0x06c60000,
    0x06e00000, 0x06e10000, 0x06e20000, 0x07860000, 0x07861000, 0x078a0000, 0x078b0000,
)
NAMES = ["venus","vsense","lpass","prng","pimem","dcc","gpu","turing","turing-llm","dl-south","qm","dl-north","swao-0","swao-1","mm","dlct","ipcc","npu-llm","npu-dpm","npu","mdss","ddr","ddr-ch02","ddr-ch13","actpm","apss","llm-silver","llm-gold"]
DT_MATCH_IDS = (0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0x3b968, 0x3b968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968, 0xbb968)
DT_DISABLED_BASES = (0x6840000, 0x6981000, 0x6e00000)
CLAIMSET, CLAIMCLR, LAR, LSR = 0xfa0, 0xfa4, 0xfb0, 0xfb4
AUTH, DEVARCH, DEVID, DEVTYPE = 0xfb8, 0xfbc, 0xfc8, 0xfcc
ITCTRL = 0xf00
UNLOCK = 0xc5acce55
AUTH_DISABLED = 0x88
DEVID_VALUE = 0x15000000
UNSUPPORTED = (0x000, 0x220, 0x280, 0x3c8, 0x500, 0x58c, 0x700,
               0x780, 0xa00, 0xa74, 0xc7c, 0xef0, DEVTYPE)
REQUIRED = [
    'twenty-eight-original-dt-management-apertures-and-separate-amba-overrides',
    'reset-lock-claim-mask-and-fixed-zero-dataset-profile',
    'actual-mmio-vendor-pidr2-pidr0-devid-decode-advertises-no-datasets',
    'empty-dataset-software-enable-sequence-only-unlocks-and-relocks',
    'unlocked-independent-claims-and-absent-integration',
    'software-lock-and-absent-dataset-writes-have-distinct-contracts',
    'strict-apb-unsupported-datasets-and-aperture-crossing-no-mutation',
    'system-reset-restores-twenty-eight-management-instances',
    'ordinary-checkpoint-preserves-distinct-claims-locks-and-continuation',
    'every-created-child-is-actually-waited-and-reaped',
]

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')


class TPDMRig(Rig):
    def read_tpdm(self, offset, *, base=BASES[0], width='l'):
        self.count['device_mmio'] += 1
        return int(self.qt(f'read{width} {base + offset:#x}')[0], 0)

    def write_tpdm(self, offset, value, *, base=BASES[0], width='l'):
        self.count['device_mmio'] += 1
        self.qt(f'write{width} {base + offset:#x} {value:#x}')

    def unlock(self, base):
        self.write_tpdm(LAR, UNLOCK, base=base)
        assert self.read_tpdm(LSR, base=base) == 1

    def lock(self, base):
        self.write_tpdm(LAR, 0, base=base)
        assert self.read_tpdm(LSR, base=base) == 3

    def state(self):
        return [[self.read_tpdm(offset, base=base)
                 for offset in (CLAIMCLR, LSR, 0xfe0, DEVID)]
                for base in BASES]


def verify_apertures(rig, out):
    flat = rig.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    path = out / 'memory-map.txt'
    path.write_text(flat)
    rows = []
    for line in flat.splitlines():
        if not line.rstrip().endswith(': qcom-coresight-tpdm'):
            continue
        match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
        assert match, line
        rows.append((int(match[1], 16), int(match[2], 16)))
    expected = {(base, base + 0xfff) for base in BASES}
    assert rows and set(rows) == expected, rows
    assert all(not (start <= 0x06b26000 <= end) for start, end in rows)
    before_state = rig.state()
    neighbors = []
    for address in (BASES[-1] - 4, BASES[-1] + 0x1000):
        prior = rig.stderr()
        rig.count['device_mmio'] += 1
        rig.qt(f'readl {address:#x}')
        added = rig.stderr()[len(prior):]
        assert f'Invalid read at addr {address:#x}'.lower() in added.lower(), added
        assert 'qcom-coresight-tpdm: rejected' not in added
        assert rig.state() == before_state
        neighbors.append(dict(address=hex(address), size=4,
                              diagnostic=added.strip()))
    return dict(regions=[[hex(a), hex(b)] for a, b in sorted(expected)],
                boundary_rejections=neighbors, dummy_mmio_mapped=False,
                flatmap=dict(path=str(path), bytes=path.stat().st_size,
                             sha256=sha(path)))


def reject(rig, base, offset, *, write=False, width='l', value=0x12345678):
    state = rig.state()
    before = rig.stderr()
    prior = before.count('qcom-coresight-tpdm: rejected')
    if write:
        rig.write_tpdm(offset, value, base=base, width=width)
    else:
        rig.read_tpdm(offset, base=base, width=width)
    after = rig.stderr()
    assert after.count('qcom-coresight-tpdm: rejected') == prior + 1
    last = [line for line in after.splitlines()
            if 'qcom-coresight-tpdm: rejected' in line][-1]
    size = {'b': 1, 'w': 2, 'l': 4, 'q': 8}[width]
    assert last == (f"qcom-coresight-tpdm: rejected {'write' if write else 'read'} "
                    f"offset={offset:#x} size={size}"), last
    assert rig.state() == state
    return dict(base=hex(base), offset=hex(offset), write=write, width=width,
                diagnostic=after[len(before):].strip())


def vendor_probe_decode(rig, base):
    # Same three device reads and field decoding as the preserved vendor probe.
    # The DT override is consumed by AMBA enumeration, not used as this PIDR0.
    before = rig.count['qtest']
    pidr2 = rig.read_tpdm(0xfe8, base=base)
    pidr0 = rig.read_tpdm(0xfe0, base=base)
    devid = rig.read_tpdm(DEVID, base=base)
    assert rig.count['qtest'] == before + 3
    version = (pidr2 >> 4) & 0xf
    datasets = [i for i in range(32) if pidr0 & (1 << i)]
    support = [(devid >> bit) & 3 for bit in (27, 25, 23)]
    raw_counts = {'bc': ((devid >> 6) & 0x1f) + 1,
                  'tc': ((devid >> 4) & 3) + 1}
    assert pidr2 == 0xb and pidr0 == 0 and devid == DEVID_VALUE
    assert version == 0 and not datasets and support == [2, 2, 2]
    assert raw_counts == {'bc': 1, 'tc': 1}
    return dict(base=hex(base), pidr2=pidr2, pidr0=pidr0, devid=devid,
                revision=version, datasets=datasets, msr_support=False,
                trigger_and_gang_support=support,
                raw_counter_field_decode=raw_counts,
                counter_datasets_present=False, actual_mmio_reads=3)


def continuation(rig):
    for i, base in enumerate(BASES):
        assert vendor_probe_decode(rig, base)['datasets'] == []
        rig.unlock(base)
        rig.write_tpdm(CLAIMSET, 1 << (i % 4), base=base)
        rig.write_tpdm(CLAIMCLR, 1 << ((i + 1) % 4), base=base)
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
    report = dict(schema='tb371fc-coresight-tpdm-v1', passed=False,
                  qemu_sha256=sha(args.qemu), test_sha256=sha(__file__),
                  checks=[], processes=[], migrations=[], access_rejections=[],
                  original_android_executed=False, guest_cpu_executed=False,
                  authentication_status=AUTH_DISABLED,
                  authentication_is_model_configuration=True,
                  authentication_rejects_vendor_probe=False,
                  dataset_bitmap=0, collection_datasets_implemented=False,
                  atb_output_implemented=False, dma_implemented=False,
                  irq_implemented=False, integration_implemented=False,
                  migration_payloads_modified=False,
                  physical_dataset_configuration_measured=False)

    def check(name, **evidence):
        assert name == REQUIRED[len(report['checks'])], name
        report['checks'].append(dict(name=name, passed=True, **evidence))

    try:
        source = TPDMRig(args, out / 'source')
        try:
            r, w = source.read_tpdm, source.write_tpdm
            apertures = verify_apertures(source, out)
            for i, base in enumerate(BASES):
                pid = sum(r(0xfe0 + 4 * j, base=base) << (8 * j)
                          for j in range(4))
                cid = sum(r(0xff0 + 4 * j, base=base) << (8 * j)
                          for j in range(4))
                assert pid == 0xbb900 and cid == 0xb105900d
                assert (DT_MATCH_IDS[i] & 0x3ffff) == 0x3b968
                assert (pid & 0x3ffff) != 0x3b968
                assert r(0xfd0, base=base) == 4
                for offset in (0xfd4, 0xfd8, 0xfdc, 0xfec):
                    assert r(offset, base=base) == 0
                assert r(DEVARCH, base=base) == 0
                assert r(AUTH, base=base) == AUTH_DISABLED
            check(REQUIRED[0], addresses=[hex(x) for x in BASES],
                  instance_names=list(NAMES), periphid=hex(0xbb900),
                  original_dt_match_overrides=[hex(x) for x in DT_MATCH_IDS],
                  original_dt_disabled=[hex(x) for x in DT_DISABLED_BASES],
                  original_dt_enabled_nodes=25, dummy_nodes_without_reg=1,
                  vendor_match_id=hex(0x3b968), vendor_match_mask=hex(0x3ffff),
                  override_is_dataset_measurement=False,
                  native_fault_address=hex(0x06b09000 + 0xfe8), apertures=apertures)
            reset = [[0, 3, 0, DEVID_VALUE] for _ in BASES]
            assert source.state() == reset
            for base in BASES:
                assert r(CLAIMSET, base=base) == 0xf
                assert r(ITCTRL, base=base) == 0
                w(CLAIMSET, 0xffffffff, base=base)
                w(CLAIMCLR, 0xffffffff, base=base)
                w(LAR, UNLOCK ^ 1, base=base)
            assert source.state() == reset
            check(REQUIRED[1], authstatus=AUTH_DISABLED, auth_fields=[0, 2, 0, 2],
                  claim_implemented_bits=4, reset_lsr=3, dataset_bitmap=0,
                  immutable_dataset_capability=True)

            probes = [vendor_probe_decode(source, base) for base in BASES]
            assert source.state() == reset
            check(REQUIRED[2], probe_reads=probes, probe_code_executed=False,
                  tpdm_registration_claimed=False, authstatus_consulted=False)
            for base in BASES:
                source.unlock(base)
                source.lock(base)
            assert source.state() == reset
            check(REQUIRED[3], instances=len(BASES), collection_configuration_writes=0,
                  clock_control_writes=0, sample_or_trace_production=False)

            claims = [1 + i % 7 for i in range(len(BASES))]
            for i, base in enumerate(BASES):
                source.unlock(base)
                w(CLAIMSET, 0xffff0000 | claims[i], base=base)
                w(CLAIMSET, 8, base=base)
                assert r(CLAIMSET, base=base) == 0xf
                assert r(CLAIMCLR, base=base) == (claims[i] | 8)
                w(CLAIMCLR, 0xffff0008, base=base)
                for value in (0, 1, 0xffffffff):
                    w(ITCTRL, value, base=base)
                    assert r(ITCTRL, base=base) == 0
            assert source.state() == [[x, 1, 0, DEVID_VALUE] for x in claims]
            check(REQUIRED[4], claims=claims, integration_control_raz_wi=True)

            for base in BASES:
                source.lock(base)
            before = source.state()
            for base in BASES:
                for offset in (CLAIMSET, CLAIMCLR, AUTH, DEVARCH, DEVID, ITCTRL,
                               LSR, 0xfe0, 0xfe4, 0xfe8, 0xffc):
                    w(offset, 0xffffffff, base=base)
                w(LAR, UNLOCK ^ 0x100, base=base)
            assert source.state() == before
            for write in (False, True):
                report['access_rejections'].append(
                    reject(source, BASES[0], 0, write=write))
            report['access_rejections'].append(
                reject(source, BASES[0], LAR, write=True, width='q', value=UNLOCK))
            assert source.state() == before
            check(REQUIRED[5], implemented_management_writes_ignored_while_locked=True,
                  absent_dataset_accesses_rejected_even_while_locked=True,
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
            for offset in (AUTH, DEVARCH, DEVID, LSR, 0xfe0, 0xfe8, 0xffc):
                cases.append((offset, True, 'l'))
            cases.append((LAR, False, 'l'))
            for offset in (0x900, 0xf04, 0xfc0):
                cases.extend([(offset, False, 'l'), (offset, True, 'l')])
            # This 8-byte transaction stays wholly inside the APB aperture.
            cases.extend([(0xff8, False, 'q'), (0xff8, True, 'q')])
            assert len(cases) == 52
            for offset, write, width in cases:
                report['access_rejections'].append(
                    reject(source, BASES[0], offset, write=write, width=width))
            boundary = reject(source, BASES[-1], 0xffc, write=True, width='q',
                              value=0xfeedfacec5acce55)
            assert boundary['diagnostic'] == \
                'qcom-coresight-tpdm: rejected write offset=0xffc size=8'
            assert r(0xffc, base=BASES[-1]) == 0xb1
            boundary['crosses_aperture'] = True
            boundary['dispatch_size'] = 8
            boundary['adjacent_region_dispatched'] = False
            report['access_rejections'].append(boundary)
            assert len(report['access_rejections']) == 56
            check(REQUIRED[6], strict_rejected_accesses=52,
                  total_rejected_accesses=56, aperture_crossing=boundary,
                  collection_configuration_implemented=False)

            source.monitor('system_reset')
            assert source.state() == reset
            for base in BASES:
                assert r(CLAIMSET, base=base) == 0xf
                assert vendor_probe_decode(source, base)['datasets'] == []
            check(REQUIRED[7])

            for i, base in enumerate(BASES):
                source.unlock(base)
                w(CLAIMSET, (1 + 3 * i) & 0xf, base=base)
                if i % 2 == 0:
                    source.lock(base)
            saved = source.state()
            assert saved == [[(1 + 3 * i) & 0xf, 3 if i % 2 == 0 else 1,
                              0, DEVID_VALUE] for i in range(len(BASES))]
            stream = out / 'streams/tpdm.mig'
            source.monitor('stop')
            assert source.monitor('query-status')['running'] is False
            source.monitor('migrate', file_channels(stream))
            assert source.migrate_wait()['status'] == 'completed'
            migration = dict(path=str(stream), bytes=stream.stat().st_size,
                             sha256=sha(stream), payload_modified=False)
            report['migrations'].append(migration)
            expected = continuation(source)
            assert expected == [[(saved[i][0] | (1 << (i % 4))) &
                                 ~(1 << ((i + 1) % 4)), 3, 0, DEVID_VALUE]
                                for i in range(len(BASES))]
        finally:
            source.close()

        target = TPDMRig(args, out / 'target', incoming=True)
        try:
            target.monitor('migrate-incoming', file_channels(stream))
            assert target.migrate_wait()['status'] == 'completed'
            assert target.state() == saved
            assert continuation(target) == expected
            assert sha(stream) == migration['sha256']
            check(REQUIRED[8], saved_state=saved, continued_state=expected,
                  ordinary_migration=True, dataset_bitmap=0)
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
