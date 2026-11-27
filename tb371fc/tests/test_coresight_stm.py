#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise STM32 controls and the fixed authentication-denied stimulus path.

Uses real MMIO, original-vendor programming sequences and an ordinary process
checkpoint. No guest CPU, trace engine, ATB connection, QOM mutation or stream edit.
"""
import argparse
import json
from pathlib import Path
import re
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

BASE, STIM, STIM_SIZE = 0x06002000, 0x16280000, 0x180000
DMASTART, DMASTOP, DMASTAT, DMACTRL, DMAID = 0xc04, 0xc08, 0xc0c, 0xc10, 0xcfc
HEER, HETER, HEBSR, HEMCR = 0xd00, 0xd20, 0xd60, 0xd64
HEMASTR, HEFEAT1, HEID = 0xdf4, 0xdf8, 0xdfc
SPER, SPTER, PRIVMASK = 0xe00, 0xe20, 0xe40
SPSCR, SPMSCR, SPOVERRIDE, SPMOVERRIDE = 0xe60, 0xe64, 0xe68, 0xe6c
SPTRIGCSR, TCSR, TSSTIM, TSFREQ, SYNCR, AUXCR = 0xe70, 0xe80, 0xe84, 0xe8c, 0xe90, 0xe94
SPFEAT1, SPFEAT2, SPFEAT3 = 0xea0, 0xea4, 0xea8
ITCTRL, CLAIMSET, CLAIMCLR, LAR, LSR = 0xf00, 0xfa0, 0xfa4, 0xfb0, 0xfb4
AUTH, DEVID, DEVTYPE = 0xfb8, 0xfc8, 0xfcc
UNLOCK = 0xc5acce55
STATE_REGS = (HEER, HETER, HEMCR, SPER, SPTER, SPSCR, SPMSCR, SPOVERRIDE,
              SPMOVERRIDE, SPTRIGCSR, TCSR, TSFREQ, SYNCR, AUXCR, CLAIMCLR, LSR)
REQUIRED = [
    'original-dt-amba-identity-and-stm32-reference-capabilities',
    'reset-software-lock-and-fixed-four-input-authentication',
    'original-vendor-feature-read-and-thirty-two-channel-contract',
    'control-masks-claims-and-absent-privilege-bank-selectors',
    'invalid-selectors-trigger-clears-and-active-traceid-are-atomic',
    'vendor-enable-disable-programming-preserves-authentication-denial',
    'disabled-stimulus-window-accepts-drops-and-reads-zero',
    'enabled-config-and-force-timestamp-cannot-bypass-authentication',
    'strict-apb-access-and-unsupported-dma-integration-no-mutation',
    'system-reset-restores-reference-control-and-lock-state',
    'ordinary-checkpoint-preserves-controls-and-authentication-denied-stimulus',
    'every-created-child-is-actually-waited-and-reaped',
]

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use -O.')


class STMRig(Rig):
    def read_stm(self, offset, width='l'):
        self.count['device_mmio'] += 1
        return int(self.qt(f'read{width} {BASE + offset:#x}')[0], 0)

    def write_stm(self, offset, value, width='l'):
        self.count['device_mmio'] += 1
        self.qt(f'write{width} {BASE + offset:#x} {value:#x}')

    def stimulus(self, offset, value, width='l'):
        self.count['device_mmio'] += 2
        self.qt(f'write{width} {STIM + offset:#x} {value:#x}')
        result = int(self.qt(f'read{width} {STIM + offset:#x}')[0], 0)
        assert result == 0

    def unlock(self):
        self.write_stm(LAR, UNLOCK)
        assert self.read_stm(LSR) == 1

    def lock(self):
        self.write_stm(LAR, 0)
        assert self.read_stm(LSR) == 3

    def state(self):
        return {hex(offset): self.read_stm(offset) for offset in STATE_REGS}

    def reset_state(self):
        return {hex(offset): 4 if offset == TCSR else 3 if offset == LSR else 0
                for offset in STATE_REGS}

    def vendor_enable(self):
        # HWE configuration, port enable, then global enable as in the vendor.
        self.unlock()
        for offset, value in ((HEBSR, 0), (HETER, 3), (HEER, 3), (HEMCR, 5)):
            self.write_stm(offset, value)
        self.lock()
        self.unlock()
        for offset, value in ((SPTRIGCSR, 0x10), (SPSCR, 0), (SPER, 0xffffffff)):
            self.write_stm(offset, value)
        self.lock()
        self.unlock()
        self.write_stm(SYNCR, 0xfff)
        self.write_stm(TCSR, 0x100003)
        self.lock()

    def vendor_disable(self):
        self.unlock()
        self.write_stm(TCSR, self.read_stm(TCSR) & ~1)
        self.lock()
        self.unlock()
        self.write_stm(SPER, 0)
        self.write_stm(SPTRIGCSR, 0)
        self.lock()
        self.unlock()
        for offset in (HEMCR, HEER, HETER):
            self.write_stm(offset, 0)
        self.lock()


def reject(rig, offset, *, write=False, width='l', value=0x12345678):
    before = rig.stderr().count('arm-coresight-stm: rejected')
    if write:
        rig.write_stm(offset, value, width)
    else:
        rig.read_stm(offset, width)
    lines = rig.stderr()
    assert lines.count('arm-coresight-stm: rejected') == before + 1
    last = [line for line in lines.splitlines()
            if 'arm-coresight-stm: rejected' in line][-1]
    assert f"rejected {'write' if write else 'read'} offset={offset:#x} size=" in last
    return dict(offset=hex(offset), write=write, width=width, value=value if write else None)


def stimulus_matrix(rig):
    # Natural 8/16/32-bit writes, including the last port in the original DT
    # aperture. STM32 does not advertise a 64-bit fundamental data packet.
    before = rig.state()
    stderr = rig.stderr()
    accesses = []
    for port in (0, 31, 32, STIM_SIZE // 256 - 1):
        for packet in (0x18, 0x90, 0x98, 0xe8, 0xf8):
            for width, value in (('b', 0xa5), ('w', 0xbeef), ('l', 0x12345678)):
                offset = port * 256 + packet
                rig.stimulus(offset, value, width)
                accesses.append(dict(port=port, offset=hex(offset), width=width))
    rig.stimulus(STIM_SIZE - 1, 0x5a, 'b')
    accesses.append(dict(port=STIM_SIZE // 256 - 1,
                         offset=hex(STIM_SIZE - 1), width='b'))
    assert rig.state() == before
    assert rig.stderr() == stderr
    assert rig.read_stm(AUTH) == 0xaa
    assert not rig.read_stm(TCSR) & (1 << 23)
    assert not rig.read_stm(SPTRIGCSR) & 2
    assert not rig.read_stm(HEMCR) & 0x20
    return accesses


def continuation(rig):
    before = rig.state()
    rig.write_stm(SPER, 0)  # Migrated lock must still block APB writes.
    rig.stimulus(31 * 256 + 0x98, 0xabcdef12)
    assert rig.state() == before
    rig.unlock()
    rig.write_stm(CLAIMCLR, 8)
    rig.write_stm(TCSR, rig.read_stm(TCSR) & ~1)
    rig.write_stm(TCSR, 0x350003)
    rig.write_stm(SPER, 0xcccccccc)
    rig.write_stm(SPSCR, 0x00500001)
    rig.write_stm(SYNCR, 0xfff)
    rig.write_stm(SPTRIGCSR, 0x1d)
    rig.write_stm(HEMCR, 0xd7)
    rig.write_stm(TSSTIM, 1)
    rig.lock()
    rig.stimulus(STIM_SIZE - 8, 0x12345678)
    assert rig.read_stm(AUTH) == 0xaa
    assert rig.read_stm(TCSR) == 0x350007
    assert rig.read_stm(CLAIMCLR) == 2
    return rig.state()


def verify_apertures(rig, out):
    flat = rig.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    path = out / 'memory-map.txt'
    path.write_text(flat)
    mapped = {}
    for name, first, last in (('arm-coresight-stm', BASE, BASE + 0xfff),
                              ('arm-coresight-stm-stimulus', STIM,
                               STIM + STIM_SIZE - 1)):
        rows = []
        for line in flat.splitlines():
            if not line.rstrip().endswith(': ' + name):
                continue
            match = re.search(r'([0-9a-fA-F]+)-([0-9a-fA-F]+)', line)
            assert match, line
            rows.append((int(match[1], 16), int(match[2], 16)))
        assert rows and set(rows) == {(first, last)}, (name, rows)
        mapped[name] = [hex(first), hex(last)]
    # Reads immediately outside the retained DT stimulus aperture must take
    # the real AddressSpace unassigned path, not alias a stimulus callback.
    rejected = []
    for address in (STIM - 4, STIM + STIM_SIZE):
        prior = rig.stderr()
        rig.count['device_mmio'] += 1
        rig.qt(f'readl {address:#x}')
        added = rig.stderr()[len(prior):]
        assert f'Invalid read at addr {address:#x}'.lower() in added.lower(), added
        rejected.append(dict(address=hex(address), size=4, diagnostic=added.strip()))
    return dict(regions=mapped, boundary_rejections=rejected,
                flatmap=dict(path=str(path), bytes=path.stat().st_size,
                             sha256=sha(path)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    args.machine = 'tb371fc,ufs-enabled=off'
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'streams').mkdir()
    report = dict(schema='tb371fc-coresight-stm-v1', passed=False,
                  qemu_sha256=sha(args.qemu), test_sha256=sha(__file__),
                  checks=[], processes=[], migrations=[], access_rejections=[],
                  original_android_executed=False, guest_cpu_executed=False,
                  debug_authentication_inputs=[False, False, False, False],
                  authentication_status=0xaa, atb_output_implemented=False,
                  trace_engine_available=False, dma_implemented=False,
                  migration_payloads_modified=False)

    def check(name, **evidence):
        assert name == REQUIRED[len(report['checks'])], name
        report['checks'].append(dict(name=name, passed=True, **evidence))

    try:
        source = STMRig(args, out / 'source')
        try:
            r, w = source.read_stm, source.write_stm
            pid = sum(r(0xfe0 + 4 * i) << (8 * i) for i in range(4))
            cid = sum(r(0xff0 + 4 * i) << (8 * i) for i in range(4))
            assert pid == 0xbb962 and cid == 0xb105900d
            assert r(0xfd0) == 4 and r(DEVTYPE) == 0x63
            assert r(DEVID) == 0x10000
            assert [r(x) for x in (SPFEAT1, SPFEAT2, SPFEAT3)] == [
                0x006587d1, 0x000104f2, 0x7f]
            assert [r(x) for x in (HEMASTR, HEFEAT1, HEID)] == [
                0x80, 0x00100035, 1]
            assert r(DMAID) == 2 and r(DMASTAT) == r(DMACTRL) == 0
            check(REQUIRED[0], control=hex(BASE), stimulus=hex(STIM),
                  stimulus_bytes=STIM_SIZE, periphid=hex(pid),
                  reference_architectural_ports=65536)
            assert source.state() == source.reset_state()
            assert r(AUTH) == 0xaa and r(CLAIMSET) == 0xf
            before = source.state()
            for offset in STATE_REGS:
                if offset != LSR:
                    w(offset, 0xffffffff)
            for offset in (CLAIMSET, HEBSR, PRIVMASK, ITCTRL, TSSTIM):
                w(offset, 0xffffffff)
            w(LAR, UNLOCK ^ 1)
            assert source.state() == before
            check(REQUIRED[1], authentication_inputs_fixed_low=4)
            # Exactly the two hardware feature reads observed in the original
            # Image. The vendor subsequently uses 32 channels unconditionally.
            feat2, devid = r(SPFEAT2), r(DEVID)
            write_bytes = 8 if (feat2 >> 12) & 0xf else 4
            architectural_ports = (devid & 0x1ffff) or 32
            vendor_channels = 32
            assert write_bytes == 4 and architectural_ports == 65536
            assert vendor_channels == 32 and source.state() == before
            check(REQUIRED[2], write_bytes=write_bytes,
                  hardware_ports=architectural_ports, vendor_channels=vendor_channels,
                  actual_native_fault_register=hex(BASE + SPFEAT2))
            source.unlock()
            values = {
                HEER: 0x12345678, HETER: 0xa5a5a5a5, HEMCR: 0xffffffff,
                SPER: 0xffffffff, SPTER: 0x55555555, SPSCR: 0xfffffffd,
                SPMSCR: 0xffffffff, SPOVERRIDE: 0xfffffff6,
                SPMOVERRIDE: 0xffffffff, SPTRIGCSR: 0xffffffff,
                TCSR: 0xffffffff, TSFREQ: 0x12345678,
                SYNCR: 0xfff, AUXCR: 0xffffffff,
            }
            for offset, value in values.items():
                w(offset, value)
            expected = dict(values)
            expected.update({HEMCR: 0x97, SPSCR: 0xfff00001, SPMSCR: 0x007f8001,
                             SPOVERRIDE: 0xffff8006, SPMOVERRIDE: 0x007f8001,
                             SPTRIGCSR: 0x19, TCSR: 0x007f0027, AUXCR: 0x1f})
            for offset, value in expected.items():
                assert r(offset) == value, (hex(offset), hex(r(offset)), hex(value))
            for offset in (HEBSR, PRIVMASK):
                w(offset, 0xffffffff)
                assert r(offset) == 0
            w(CLAIMSET, 0xffff0005)
            w(CLAIMSET, 2)
            assert r(CLAIMSET) == 0xf and r(CLAIMCLR) == 7
            w(CLAIMCLR, 0xffff0002)
            assert r(CLAIMCLR) == 5
            check(REQUIRED[3], masks={hex(k): v for k, v in expected.items()})
            # OVERTS is explicitly independent of PORTSEL/OVERCTL.
            w(SPOVERRIDE, 4)
            assert r(SPOVERRIDE) == 4
            invalid = ((SPSCR, 0x00100002), (SPSCR, 1),
                       (SPMSCR, 1), (SPMOVERRIDE, 1),
                       (SPOVERRIDE, 0x8003), (SPOVERRIDE, 1),
                       (SYNCR, 0x1000 | (11 << 7)),
                       (SYNCR, 0x1000 | (28 << 7)),
                       (SPTRIGCSR, 4), (HEMCR, 0x40),
                       (TCSR, 0x100003))
            before = source.state()
            for offset, value in invalid:
                report['access_rejections'].append(
                    reject(source, offset, write=True, value=value))
                assert source.state() == before
            for count in (12, 27):
                value = 0x1000 | (count << 7) | 0x7f
                w(SYNCR, value)
                assert r(SYNCR) == value
            check(REQUIRED[4], rejected_combinations=len(invalid),
                  independent_timestamp_override=True, exponential_mode_limits=[12, 27])
            # Disable first before changing the TRACEID for the vendor sequence.
            w(TCSR, r(TCSR) & ~1)
            w(TCSR, 0)
            source.lock()
            source.vendor_enable()
            assert r(TCSR) == 0x100007 and r(SPER) == 0xffffffff
            assert r(SPTRIGCSR) == 0x10 and r(SYNCR) == 0xfff
            assert r(HEMCR) == 5 and r(AUTH) == 0xaa
            source.vendor_disable()
            assert r(TCSR) == 0x100006
            assert all(r(x) == 0 for x in (SPER, SPTRIGCSR, HEER, HETER, HEMCR))
            assert r(LSR) == 3 and r(AUTH) == 0xaa
            check(REQUIRED[5], trace_available=False)
            apertures = verify_apertures(source, out)
            disabled = stimulus_matrix(source)
            check(REQUIRED[6], access_pairs=disabled, natural_widths=[1, 2, 4],
                  apb_locked=True, apertures=apertures)
            source.unlock()
            w(TCSR, 0x100003)
            w(SPER, 0xffffffff)
            w(SPTRIGCSR, 0x19)
            w(HEER, 0xffffffff)
            w(HETER, 0xffffffff)
            w(HEMCR, 0x97)
            w(SYNCR, 0x1dff)
            before = source.state()
            w(TSSTIM, 1)
            assert source.state() == before
            # Stimulus acceptance is independent of the APB software lock.
            source.stimulus(0x98, 0xa5a5a5a5)
            source.lock()
            enabled = stimulus_matrix(source)
            assert r(TCSR) == 0x100007
            check(REQUIRED[7], access_pairs=enabled, timestamp_request_suppressed=True,
                  atb_output=False, busy=False,
                  stimulus_accesses_accepted_while_apb_locked=True)
            source.unlock()
            before = source.state()
            bad_access = (
                (SPFEAT2, False, 'b'), (SPFEAT2, False, 'w'),
                (SPFEAT2, False, 'q'), (SPFEAT2 + 1, False, 'l'),
                (LAR, False, 'l'), (DMASTART, False, 'l'), (TSSTIM, False, 'l'),
                (0x000, False, 'l'), (0xe88, False, 'l'), (0xe88, True, 'l'),
                (SPER, True, 'b'), (SPER, True, 'w'), (SPER, True, 'q'),
                (SPER + 1, True, 'l'), (LAR, True, 'q'), (LAR + 1, True, 'l'),
                (AUTH, True, 'l'), (DEVID, True, 'l'),
                (SPFEAT2, True, 'l'), (0xfe0, True, 'l'),
                (DMASTART, True, 'l'), (DMASTOP, True, 'l'), (DMACTRL, True, 'l'),
                (ITCTRL, True, 'l'), (0xee8, True, 'l'), (0xeec, True, 'l'),
                (0xef4, True, 'l'), (0xef8, True, 'l'), (0xef0, True, 'l'),
                (0xee8, False, 'l'),
            )
            for offset, write, width in bad_access:
                report['access_rejections'].append(
                    reject(source, offset, write=write, width=width))
                assert source.state() == before
            for offset in (DMASTART, DMASTOP, DMACTRL, ITCTRL,
                           0xee8, 0xeec, 0xef4, 0xef8):
                w(offset, 0)
            assert r(DMASTAT) == r(DMACTRL) == r(ITCTRL) == r(0xef0) == 0
            check(REQUIRED[8], rejected_accesses=len(bad_access),
                  total_rejected_accesses=len(report['access_rejections']),
                  unsupported_dma_nonzero_rejected=True,
                  unsupported_integration_nonzero_rejected=True)
            source.monitor('system_reset')
            assert source.state() == source.reset_state()
            assert r(AUTH) == 0xaa and r(SPFEAT2) == 0x104f2
            check(REQUIRED[9])
            source.unlock()
            saved_values = {
                HEER: 0x13579bdf, HETER: 0x2468ace0, HEMCR: 0x97,
                SPER: 0xa5a5a5a5, SPTER: 0x55555555,
                SPSCR: 0x00300003, SPMSCR: 0x00018001,
                SPOVERRIDE: 0x001f8006, SPMOVERRIDE: 0x00208001,
                SPTRIGCSR: 0x19, TCSR: 0x240023, TSFREQ: 19200000,
                SYNCR: 0x1dff, AUXCR: 0x1f, CLAIMSET: 0xa,
            }
            for offset, value in saved_values.items():
                w(offset, value)
            source.lock()
            saved = source.state()
            stream = out / 'streams/stm.mig'
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
        target = STMRig(args, out / 'target', incoming=True)
        try:
            target.monitor('migrate-incoming', file_channels(stream))
            assert target.migrate_wait()['status'] == 'completed'
            assert target.state() == saved
            assert continuation(target) == expected
            assert sha(stream) == migration['sha256']
            check(REQUIRED[10], saved_state=saved, continued_state=expected,
                  ordinary_migration=True, authentication_status=0xaa)
        finally:
            target.close()
        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 2
        assert all(p['counters']['qom_set'] == 0 for p in report['processes'])
        assert sha(args.qemu) == report['qemu_sha256']
        assert sha(__file__) == report['test_sha256']
        check(REQUIRED[11], processes=2, forced_cleanup=False)
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
