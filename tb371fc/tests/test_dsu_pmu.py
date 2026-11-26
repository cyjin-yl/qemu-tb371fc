#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Eight-CPU DSU cycle-only register, privilege, IRQ and migration regression."""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

HERE = Path(__file__).resolve().parent
BOX = 0x80210000
CONTROL = 0x80200000
STRIDE = 0x1000
MAGIC = 0x44535550524f4245
PMCR = 0x51000040
CYCLE = 1 << 31
GIC_PENDING = 1 << 18  # SPI 50 is GIC INTID 82.
MASK64 = (1 << 64) - 1
PROFILE = 0x44535501


class DsuRig(Rig):
    def __init__(self, args, directory, *, enabled=True, incoming=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sockets = Path(tempfile.mkdtemp(prefix='dsu-'))
        self.process = None
        self.peers = {}
        self.closed = False
        self.expected_failure = False
        self.expected_exit = 0
        self.request_id = 0
        self.count = dict(qom_get=0, qom_set=0, qtest=0, memory=0,
                          device_mmio=0, ram=0, cont=0)
        self.events, self.irq_events, self.waits = [], [], []
        self.irq = {0: False}
        self.intercepted = False
        self.seq = [0] * 8
        self.operations = []
        self.logs = {name: (self.directory / name).open('wb')
                     for name in ('qemu.stdout', 'qemu.stderr')}
        self.protocol = (self.directory / 'protocol.jsonl').open('w')
        machine = 'tb371fc,ufs-enabled=off,dsu-pmu-cycle-only=' + ('on' if enabled else 'off')
        command = [str(Path(args.qemu).resolve()), '-M', machine,
                   '-accel', 'tcg,thread=single', '-m', '128M', '-smp', '8', '-S',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null',
                   '-serial', 'null', '-d', 'guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.sockets}/m,server=on,wait=off',
                   '-qtest', f'unix:{self.sockets}/t,server=on,wait=off',
                   '-qtest-log', str(self.directory / 'qtest-server.log'),
                   '-device', f'loader,file={args.probe},cpu-num=0']
        if incoming:
            command += ['-incoming', 'defer']
        put_json(self.directory / 'command.json', command)
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                stdout=self.logs['qemu.stdout'], stderr=self.logs['qemu.stderr'])
            self.peers['qmp'] = self.connect('m', 'qmp')
            greeting = json.loads(self.peers['qmp'].receive())
            self.record({'qmp_receive': greeting})
            assert 'QMP' in greeting
            self.monitor('qmp_capabilities')
            self.peers['qtest'] = self.connect('t', 'qtest')
            assert self.snapshot()['profile-id'] == (PROFILE if enabled else 0)
        except BaseException:
            self.close()
            raise

    def snapshot(self):
        return {key: self.monitor('qom-get', {'path': '/machine/dsu-pmu', 'property': key})
                for key in ('profile-id', 'cycle-count', 'counter-enable', 'interrupt-enable',
                            'overflow', 'control', 'frequency-hz')}

    def start(self):
        self.monitor('cont')
        deadline = time.monotonic() + 15
        while True:
            rows = [struct.unpack('<32Q', self.read_ram(BOX + n * STRIDE, 256)) for n in range(8)]
            assert all(row[16] == 0 for row in rows), rows
            if (all(row[0] == MAGIC and row[1] == n and row[2] == 8
                    for n, row in enumerate(rows)) and
                    struct.unpack('<8Q', self.read_ram(CONTROL, 64)) == (1,) + (0,) * 7):
                break
            assert time.monotonic() < deadline, 'eight actual CPUs did not start'
            time.sleep(.005)
        assert struct.unpack('<8Q', self.read_ram(CONTROL, 64)) == (1,) + (0,) * 7
        self.seq = [row[4] for row in rows]

    def stop(self):
        self.monitor('stop')
        assert self.monitor('query-status')['running'] is False

    def op(self, reg, value=None, *, cpu=0, el=2, trap=None):
        self.seq[cpu] += 1
        seq = self.seq[cpu]
        address = BOX + cpu * STRIDE
        self.write_ram(address + 6 * 8,
                       struct.pack('<4Q', el, reg, int(value is not None),
                                   0 if value is None else value & MASK64))
        self.write_ram(address + 4 * 8, struct.pack('<Q', seq))
        deadline = time.monotonic() + 10
        while True:
            row = struct.unpack('<32Q', self.read_ram(address, 256))
            assert row[16] == 0, ('unexpected guest exception', cpu, row)
            if row[5] == seq:
                break
            assert time.monotonic() < deadline, ('guest command timeout', cpu, reg, seq)
            time.sleep(.002)
        assert row[0:3] == (MAGIC, cpu, 8)
        assert row[4:10] == (seq, seq, el, reg, int(value is not None),
                             0 if value is None else value & MASK64)
        record = dict(cpu=cpu, sequence=seq, el=el, reg=reg,
                      write=value is not None, input=value, output=row[10],
                      traps=row[11], trap_el=row[12], esr=row[13], elr=row[14], pc=row[15])
        self.operations.append(record)
        put_json(self.directory / 'operations.json', self.operations)
        if trap is None:
            assert row[11] == 0, record
        else:
            assert row[11] == 1 and (row[12], row[13] >> 26) == trap, record
            assert row[14] == row[15], record
        return row[10]

    def gic_pending(self):
        return bool(self.op(14) & GIC_PENDING)

    def clear_gic_pending(self):
        self.op(14, GIC_PENDING)


def compile_probe(args, out):
    root = HERE / 'dsu-pmu'
    args.probe = str(out / 'probe.elf')
    command = [args.cc, '-O2', '-Wall', '-Wextra', '-Werror', '-ffreestanding',
               '-fno-builtin', '-fno-stack-protector', '-fno-pie', '-mstrict-align',
               '-mgeneral-regs-only', '-march=armv8.2-a', '-nostdlib', '-static',
               '-Wl,--build-id=none', '-T', str(root / 'link.ld'),
               str(root / 'start.S'), str(root / 'probe.c'), '-o', args.probe]
    put_json(out / 'compile-command.json', command)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
    (out / 'compile.log').write_bytes(result.stdout)
    assert result.returncode == 0, 'guest probe compile failed; see compile.log'


def exact_probe_and_shared(r):
    assert r.op(0, el=1) == PMCR
    assert r.op(11, el=1) == r.op(12, el=1) == 0
    pending = r.op(4, el=1)
    r.op(4, pending, el=1)
    for cpu in range(8):
        value = 0x100000000 + cpu
        r.op(8, value, cpu=cpu, el=1)
        for reader in (0, 7):
            assert r.op(8, cpu=reader, el=1) == value
    assert r.snapshot()['frequency-hz'] == 1_000_000_000


def register_semantics_and_privilege(r):
    r.op(0, 0)
    for set_reg, clear_reg in ((1, 2), (6, 7), (3, 4)):
        r.op(set_reg, MASK64)
        assert r.op(set_reg) == r.op(clear_reg) == CYCLE
        r.op(clear_reg, 0x7fffffff)
        assert r.op(set_reg) == CYCLE
        r.op(clear_reg, CYCLE)
        assert r.op(set_reg) == 0
    r.op(8, 0x123456789abcdef0)
    for selector in (0, 6, 31, MASK64):
        r.op(5, selector)
        assert r.op(5) == selector & 31
        r.op(9, MASK64)
        r.op(10, MASK64)
        assert r.op(9) == r.op(10) == 0
        assert r.op(8) == 0x123456789abcdef0
    r.op(3, CYCLE)
    r.op(0, 2)  # P is ineffective with N=0, and must not clear overflow.
    assert r.op(8) == 0x123456789abcdef0 and r.op(4) == CYCLE
    r.op(0, 4)  # C is self-clearing, independent of sticky overflow.
    assert r.op(8) == 0 and r.op(4) == CYCLE and r.op(0) == PMCR
    r.op(0, MASK64)
    assert r.op(0) == PMCR | 1
    r.op(0, 0)
    r.op(4, CYCLE)
    for reg in (11, 12):
        r.op(reg, MASK64, el=1, trap=(1, 0))
    r.op(15, trap=(2, 0))  # EL1-encoded PMMDCR is not implemented.
    for reg in range(13):
        r.op(reg, el=0, trap=(1, 0))
        r.op(reg, MASK64, el=0, trap=(1, 0))
    for cpu in range(8):
        assert r.op(13, cpu=cpu) == 1 << 12
    r.op(13, 0)
    assert r.op(13) == 0 and r.op(13, cpu=1) == 1 << 12
    assert r.op(0, el=1) == PMCR  # ACTLR gates writes, not reads.
    r.op(1, CYCLE, el=1, trap=(2, 0x18))
    expected_esr = (0x18 << 26) | (1 << 25) | (3 << 20) | (1 << 17) | (15 << 10) | (5 << 1)
    assert r.operations[-1]['esr'] == expected_esr
    assert r.op(1) == 0
    for reg in (11, 12):  # Read-only failure takes priority over write gating.
        r.op(reg, 0, el=1, trap=(1, 0))
    r.op(13, MASK64)
    assert r.op(13) == 1 << 12
    r.op(1, CYCLE, el=1)
    assert r.op(1) == CYCLE
    r.op(2, CYCLE)


def cycles_pause_and_irq(r):
    r.op(8, 100)
    r.op(1, CYCLE)
    r.op(0, 1)
    first = r.op(8)
    time.sleep(.025)
    second = r.op(8)
    assert second > first >= 100
    r.stop()
    before = r.snapshot()
    time.sleep(.05)
    assert r.snapshot() == before, 'paused diagnostic state changed'
    r.start()
    assert r.op(8) >= second
    r.op(0, 0)
    r.op(2, CYCLE)
    r.op(7, CYCLE)
    r.op(4, CYCLE)
    r.clear_gic_pending()
    assert not r.gic_pending()
    r.op(8, MASK64 - 100_000_000)  # 100ms at the declared virtual 1GHz.
    r.op(6, CYCLE)
    r.op(1, CYCLE)
    r.op(0, 1)
    deadline = time.monotonic() + 5
    # Read the actual GIC SPI input state, not a DSU getter that could synthesize
    # the overflow lazily. No interrupt-output interception is installed.
    while not r.gic_pending():
        assert time.monotonic() < deadline, 'cycle overflow did not signal GIC SPI50'
        time.sleep(.005)
    assert r.op(4) == CYCLE
    r.op(0, 0)
    r.op(7, CYCLE)
    r.clear_gic_pending()
    assert not r.gic_pending()
    assert r.op(4) == CYCLE
    r.op(6, CYCLE)
    assert r.gic_pending(), 'pending overflow did not reassert on unmask'
    r.op(4, CYCLE)
    r.clear_gic_pending()
    assert not r.gic_pending()


def reset_device(r):
    r.stop()
    r.write_ram(CONTROL, bytes(64))
    for cpu in range(8):
        r.write_ram(BOX + cpu * STRIDE, bytes(256))
    r.monitor('system_reset')
    assert r.monitor('query-status')['running'] is False
    r.start()
    assert r.op(0) == PMCR
    assert all(r.op(reg) == 0 for reg in (1, 3, 5, 6, 8))
    assert all(r.op(13, cpu=cpu) == 1 << 12 for cpu in range(8))
    r.clear_gic_pending()
    assert not r.gic_pending()


def save(r, path):
    r.stop()
    r.monitor('migrate', file_channels(path))
    status = r.migrate_wait()
    assert status['status'] == 'completed'
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))


def restore(r, path, expected):
    r.monitor('migrate-incoming', file_channels(path))
    assert r.migrate_wait()['status'] == 'completed'
    assert r.monitor('query-status')['running'] is False
    assert r.snapshot() == expected, 'DSU state changed across migration'
    r.start()
    assert r.op(8, cpu=7) == 0x123456789abcdef0
    assert r.op(3) == CYCLE and r.gic_pending()
    assert r.op(13) == 0 and r.op(13, cpu=7) == 1 << 12
    r.op(1, 0, el=1, trap=(2, 0x18))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'streams').mkdir()
    report = dict(schema='tb371fc-dsu-pmu-cycle-only-test-v1', passed=False,
                  checks=[], processes=[], migrations=[], rejected_migrations=[],
                  cpu_count=8, profile_id=PROFILE, programmable_counters=0,
                  irq_intid=82, output_interception_installed=False,
                  original_android_executed=False, qemu_sha256=sha(args.qemu))
    def check(name):
        report['checks'].append(dict(name=name, passed=True))
    try:
        compile_probe(args, out)
        report['probe_sha256'] = sha(args.probe)
        off = DsuRig(args, out / 'off', enabled=False)
        try:
            off.start()
            for reg in range(13):
                off.op(reg, trap=(2, 0))
            off_stream = out / 'streams/off.mig'
            report['off_stream'] = save(off, off_stream)
        finally:
            off.close()
        check('default-off-system-registers-undefined')

        source = DsuRig(args, out / 'source')
        try:
            source.start()
            exact_probe_and_shared(source)
            check('original-probe-and-eight-cpu-shared-state')
            register_semantics_and_privilege(source)
            check('masks-selector-reset-commands-and-privilege')
            cycles_pause_and_irq(source)
            check('cycle-progress-paused-state-and-real-gic-overflow')
            reset_device(source)
            check('system-reset-clears-shared-device-state')
            source.op(8, 0x123456789abcdef0)
            source.op(1, CYCLE)
            source.op(5, 31)
            source.op(3, CYCLE)
            source.op(6, CYCLE)
            assert source.gic_pending()
            source.op(13, 0)
            on_stream = out / 'streams/on.mig'
            report['migrations'].append(save(source, on_stream))
            expected = source.snapshot()
        finally:
            source.close()
        for name in ('target', 'return'):
            target = DsuRig(args, out / name, incoming=True)
            try:
                restore(target, on_stream, expected)
                if name == 'target':
                    on_stream = out / 'streams/return.mig'
                    report['migrations'].append(save(target, on_stream))
            finally:
                target.close()
        check('enabled-migration-roundtrip-preserves-counter-irq-and-actlr')

        for name, mode, stream in (('on-to-off', False, out / 'streams/on.mig'),
                                   ('off-to-on', True, off_stream)):
            target = DsuRig(args, out / name, enabled=mode, incoming=True)
            try:
                target.expected_exit = 1
                target.monitor('migrate-incoming', file_channels(stream))
                assert target.wait_exit(30) == 1
                diagnostic = target.stderr()
                assert 'tb371fc-dsu-pmu' in diagnostic or 'DSU' in diagnostic, diagnostic
                assert target.count['cont'] == target.count['ram'] == 0
                report['rejected_migrations'].append(dict(name=name, exit_code=1,
                                                         diagnostic=diagnostic))
            finally:
                target.close()
        check('migration-profile-mismatch-rejected-in-both-directions')
        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 6
        assert len(report['checks']) == 7
        assert sha(args.qemu) == report['qemu_sha256']
        report['passed'] = True
    except BaseException as error:
        report['error'] = f'{type(error).__name__}: {error}'
        report['traceback'] = traceback.format_exc()
    finally:
        put_json(out / 'result.json', report)
    print(json.dumps(dict(passed=report['passed'], checks=len(report['checks']),
                          report=str(out / 'result.json'))))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
