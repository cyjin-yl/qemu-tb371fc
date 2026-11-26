#!/usr/bin/env python3
"""Exercise the explicit virtual PRNG with real and finite entropy backends.

Ordinary QTest only: no guest CPU execution, original Android, physical MMIO,
forged migration payloads, or entropy-quality claim.
"""
import argparse
import json
from pathlib import Path
import re
import hashlib
import os
import subprocess
import tempfile
import time
import traceback

from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

BASE = 0x793000
VOTE = 0x152000
CBCR = 0x134004
ENABLE = 1 << 13
OFF = 1 << 31
HOST_PROFILE = 0x50524e01
EXTERNAL_PROFILE = 0x50524e02


class PrngRig(Rig):
    """Reuse the existing QMP/QTest transport and actual child cleanup."""
    def __init__(self, args, directory, *, enabled=True, backend=None,
                 incoming=False, missing=False):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.sockets = Path(tempfile.mkdtemp(prefix='prng-'))
        self.process = None
        self.peers = {}
        self.closed = False
        self.expected_failure = missing
        self.expected_exit = 1 if missing else 0
        self.request_id = 0
        self.count = dict(qom_get=0, qom_set=0, qtest=0, memory=0,
                          device_mmio=0, ram=0, cont=0, prng_mmio=0, gcc_mmio=0)
        self.events, self.irq_events, self.waits = [], [], []
        self.irq = {0: False}
        self.intercepted = False
        self.logs = {name: (self.directory / name).open('wb')
                     for name in ('qemu.stdout', 'qemu.stderr')}
        self.protocol = (self.directory / 'protocol.jsonl').open('w')
        machine = ('tb371fc,ufs-enabled=off,gcc-boot-configured=off,'
                   'prng-virtual=' + ('on' if enabled else 'off'))
        command = [str(Path(args.qemu).resolve()), '-M', machine,
                   '-accel', 'qtest', '-m', '128M', '-smp', '8', '-S',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null',
                   '-serial', 'null', '-d', 'guest_errors,invalid_mem',
                   '-qmp', f'unix:{self.sockets}/m,server=on,wait=off',
                   '-qtest', f'unix:{self.sockets}/t,server=on,wait=off',
                   '-qtest-log', str(self.directory / 'qtest-server.log')]
        if backend is not None:
            assert ',' not in str(backend)
            command += ['-object', f'rng-random,id=prng-test,filename={backend}',
                        '-global', 'qcom-sm8250-prng.rng=prng-test']
        if incoming:
            command += ['-incoming', 'defer']
        self.command_line = command
        put_json(self.directory / 'command.json', command)
        try:
            self.process = subprocess.Popen(
                command, stdin=subprocess.DEVNULL,
                stdout=self.logs['qemu.stdout'], stderr=self.logs['qemu.stderr'])
            put_json(self.directory / 'child.json',
                     dict(pid=self.process.pid, created_by_this_fixture=True,
                          signals_to_preexisting_processes=False))
            if missing:
                assert self.wait_exit(10) == 1
                return
            self.peers['qmp'] = self.connect('m', 'qmp')
            greeting = json.loads(self.peers['qmp'].receive())
            self.record({'qmp_receive': greeting})
            assert 'QMP' in greeting
            self.monitor('qmp_capabilities')
            self.peers['qtest'] = self.connect('t', 'qtest')
            profile = 0 if not enabled else (
                EXTERNAL_PROFILE if backend is not None else HOST_PROFILE)
            assert self.prop('profile-id') == profile
        except BaseException:
            self.close()
            raise

    def qt(self, command):
        fields = command.split()
        if fields[0] in ('readb', 'readw', 'readl', 'readq',
                         'writeb', 'writew', 'writel', 'writeq'):
            address = int(fields[1], 0)
            if BASE <= address < BASE + 0x1000:
                self.count['prng_mmio'] += 1
                self.count['device_mmio'] += 1
            elif address in (VOTE, CBCR):
                self.count['gcc_mmio'] += 1
                self.count['device_mmio'] += 1
        return super().qt(command)

    def prop(self, name):
        return self.monitor('qom-get', {'path': '/machine/prng',
                                       'property': name})

    def mmio_read(self, address, width='l'):
        return int(self.qt(f'read{width} {address:#x}')[0], 0)

    def mmio_write(self, address, value, width='l'):
        self.qt(f'write{width} {address:#x} {value:#x}')

    def status(self):
        return self.mmio_read(BASE + 4)

    def word(self):
        return self.mmio_read(BASE)

    def vote(self, enabled):
        current = self.mmio_read(VOTE)
        self.mmio_write(VOTE, current | ENABLE if enabled else current & ~ENABLE)
        assert bool(self.mmio_read(VOTE) & ENABLE) == enabled
        assert bool(self.mmio_read(CBCR) & OFF) != (
            enabled and self.prop('profile-id') != 0)

    def wait_for(self, predicate, description):
        end = time.monotonic() + 5
        while not predicate():
            assert self.process.poll() is None, self.stderr()
            assert time.monotonic() < end, description
            time.sleep(.005)


def save(r, path):
    r.monitor('stop')
    assert r.monitor('query-status')['running'] is False
    r.monitor('migrate', file_channels(path))
    assert r.migrate_wait()['status'] == 'completed'
    assert r.prop('request-pending') == 0
    return dict(path=str(path), bytes=path.stat().st_size, sha256=sha(path))


def readonly_aperture(r):
    r.wait_for(lambda: r.prop('fifo-bytes') == 64 and
               r.prop('request-pending') == 0, 'FIFO not full')
    before = (r.prop('fifo-bytes'), r.prop('words-read'),
              r.prop('backend-requests'))
    rejected = []
    def reject(write, width, offset):
        old = [line for line in r.stderr().splitlines()
               if line.startswith('qcom-sm8250-prng:')]
        if write:
            r.mmio_write(BASE + offset, 0, width)
        else:
            r.mmio_read(BASE + offset, width)
        new = [line for line in r.stderr().splitlines()
               if line.startswith('qcom-sm8250-prng:')][len(old):]
        size = dict(b=1, w=2, l=4, q=8)[width]
        kind = 'write' if write else 'read'
        wanted = f'qcom-sm8250-prng: rejected {kind} offset={offset:#x} size={size}'
        assert new == [wanted], (kind, offset, size, new)
        rejected.append(dict(operation=kind, offset=offset, size=size,
                             actual_diagnostic=wanted))
    for offset in (0, 4):
        reject(True, 'l', offset)
        for width in ('b', 'w', 'q'):
            reject(False, width, offset)
            reject(True, width, offset)
    for write in (False, True):
        reject(write, 'l', 1)
    for offset in (8, 0x100, 0x104, 0xffc):
        reject(False, 'l', offset)
        reject(True, 'l', offset)
    assert (r.prop('fifo-bytes'), r.prop('words-read'),
            r.prop('backend-requests')) == before
    immutable = r.raw_monitor('qom-set', {
        'path': '/machine', 'property': 'prng-virtual', 'value': False})
    assert 'error' in immutable and r.prop('profile-id') == HOST_PROFILE
    assert len(rejected) == 24
    return rejected


def pending_cancellation(args, out, report):
    pipe_root = Path(tempfile.mkdtemp(prefix='prng-entropy-'))
    pipe = pipe_root / 'owned-entropy.fifo'
    os.mkfifo(pipe, 0o600)
    writer = os.open(pipe, os.O_RDWR | os.O_NONBLOCK)
    r = None
    evidence = dict(owned_fifo=str(pipe), physical_device=False)
    try:
        r = PrngRig(args, out / 'pending', backend=pipe)
        r.monitor('cont')
        r.vote(True)
        r.wait_for(lambda: r.prop('request-pending') == 1 and
                   r.prop('fifo-bytes') == 0, 'empty FIFO did not hold request')
        evidence['clock_off_before'] = dict(pending=r.prop('request-pending'),
                                           fifo_bytes=r.prop('fifo-bytes'))
        r.vote(False)
        assert r.prop('request-pending') == 0 and r.prop('fifo-bytes') == 0
        assert os.write(writer, bytes.fromhex('11223344')) == 4
        time.sleep(.02)
        assert r.prop('fifo-bytes') == 0 and r.prop('request-pending') == 0
        assert r.status() == 0
        evidence['clock_off_after'] = dict(pending=r.prop('request-pending'),
                                          fifo_bytes=r.prop('fifo-bytes'))
        r.vote(True)
        r.wait_for(lambda: r.prop('fifo-bytes') == 4 and
                   r.prop('request-pending') == 0, 're-enabled clock lost bytes')
        assert r.word() == 0x44332211
        r.wait_for(lambda: r.prop('request-pending') == 1, 'reset lacks pending request')
        evidence['reset_before'] = dict(pending=r.prop('request-pending'),
                                       words_read=r.prop('words-read'))
        r.monitor('stop')
        r.monitor('system_reset')
        assert r.prop('request-pending') == r.prop('fifo-bytes') == 0
        assert r.prop('words-read') == 0
        assert r.mmio_read(VOTE) & ENABLE == 0 and r.status() == 0
        evidence['reset_after'] = dict(pending=r.prop('request-pending'),
            fifo_bytes=r.prop('fifo-bytes'), words_read=r.prop('words-read'),
            guest_vote=r.mmio_read(VOTE) & ENABLE)
        r.monitor('cont')
        r.vote(True)
        r.wait_for(lambda: r.prop('request-pending') == 1, 'save lacks pending request')
        evidence['save_before'] = dict(pending=r.prop('request-pending'))
        report['migrations'].append(save(r, out / 'streams/pending.mig'))
        assert r.prop('request-pending') == 0
        evidence['save_after'] = dict(pending=r.prop('request-pending'),
                                     fifo_bytes=r.prop('fifo-bytes'))
        assert r.status() == 0
        r.wait_for(lambda: r.prop('request-pending') == 1,
                   'source did not resume requests after pre-save cancellation')
        assert os.write(writer, bytes.fromhex('55667788')) == 4
        r.wait_for(lambda: r.prop('fifo-bytes') == 4 and
                   r.prop('request-pending') == 0, 'resumed source lost entropy')
        resumed_word = r.word()
        assert resumed_word == 0x88776655 and r.prop('words-read') == 1
        evidence['source_resumed_word'] = resumed_word
        r.vote(False)
        return evidence
    finally:
        try:
            if r is not None:
                r.close()
        finally:
            os.close(writer)
            pipe.unlink()
            pipe_root.rmdir()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    (out / 'streams').mkdir()
    report = dict(schema='tb371fc-prng-virtual-test-v1', passed=False,
                  checks=[], processes=[], migrations=[], rejected_migrations=[],
                  qemu_sha256=sha(args.qemu), real_host_backend_observed=False,
                  guest_cpu_executed=False, original_android_executed=False,
                  physical_rng_verified=False, entropy_quality_verified=False,
                  migration_payloads_modified=False, access_rejections=[], host_backend=None)
    def check(name, **evidence):
        report['checks'].append(dict(name=name, passed=True, **evidence))
    try:
        off = PrngRig(args, out / 'off', enabled=False)
        try:
            assert off.prop('backend-type') == 'none'
            off.vote(True)
            assert off.prop('clock-hz') == off.prop('fifo-bytes') == 0
            off.mmio_read(BASE + 4)
            assert off.prop('backend-requests') == off.prop('words-read') == 0
            mtree = off.monitor('human-monitor-command',
                                {'command-line': 'info mtree -f'})
            assert 'qcom-sm8250-prng' not in mtree
            off_stream = out / 'streams/off.mig'
            report['migrations'].append(save(off, off_stream))
        finally:
            off.close()
        check('default-off-unmapped-no-parent-no-entropy-request')

        source = PrngRig(args, out / 'host-source')
        try:
            assert source.prop('backend-type') == 'rng-random'
            mapping = source.monitor('human-monitor-command',
                                     {'command-line': 'info mtree -f'})
            leaves = {(int(a, 16), int(b, 16)) for a, b in re.findall(
                r'([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: qcom-sm8250-prng\.decode-guard(?:\s|$)',
                mapping)}
            assert leaves == {(BASE, BASE + 0xfff)}, leaves
            filename = source.monitor('qom-get', {
                'path': '/machine/prng/default-backend', 'property': 'filename'})
            assert filename == '/dev/urandom'
            opened = source.monitor('qom-get', {
                'path': '/machine/prng/default-backend', 'property': 'opened'})
            assert opened is True
            report['host_backend'] = dict(profile_id=source.prop('profile-id'),
                type=source.prop('backend-type'), filename=filename, opened=opened,
                object_path='/machine/prng/default-backend')
            assert source.prop('clock-hz') == source.prop('fifo-bytes') == 0
            assert source.status() == 0
            source.monitor('cont')
            source.vote(True)
            source.wait_for(lambda: source.status() == 1, 'host entropy unavailable')
            assert source.prop('clock-hz') == 100000000
            report['real_host_backend_observed'] = True
            check('real-host-backend-and-original-gcc-enable-poll',
                  backend=report['host_backend'])
            report['access_rejections'] = readonly_aperture(source)
            check('readonly-width-alignment-holes-and-immutable-profile')
            source.wait_for(lambda: source.prop('fifo-bytes') == 64 and
                            source.prop('request-pending') == 0, 'FIFO not full')
            before = source.prop('fifo-bytes')
            source.vote(False)
            assert source.status() == 0 and source.prop('request-pending') == 0
            old_words = source.prop('words-read')
            source.word()
            assert source.prop('words-read') == old_words
            assert source.prop('fifo-bytes') == before
            source.vote(True)
            assert source.status() == 1
            check('clock-gating-retains-fifo-and-rejects-unready-data')
            sample = [source.word() for _ in range(7)]
            put_json(out / 'host-first-seven-words.json', sample)
            assert source.prop('words-read') == 7
            on_stream = out / 'streams/host.mig'
            report['migrations'].append(save(source, on_stream))
            expected_bytes = source.prop('fifo-bytes')
            expected_words = source.prop('words-read')
            assert expected_bytes >= 4 and expected_bytes % 4 == 0
            expected = [source.word() for _ in range(expected_bytes // 4)]
            put_json(out / 'retained-host-words.json', expected)
        finally:
            source.close()
        target = PrngRig(args, out / 'host-target', incoming=True)
        try:
            target.monitor('migrate-incoming', file_channels(on_stream))
            assert target.migrate_wait()['status'] == 'completed'
            assert target.prop('fifo-bytes') == expected_bytes
            assert target.prop('words-read') == expected_words
            assert target.prop('request-pending') == 0
            actual = [target.word() for _ in expected]
            assert actual == expected, 'unconsumed real entropy changed in migration'
            target.monitor('cont')
            target.wait_for(lambda: target.status() == 1,
                            'destination did not request fresh entropy')
        finally:
            target.close()
        check('ordinary-migration-preserves-unconsumed-real-host-words',
              retained_words=len(expected), words_read_before=expected_words)

        payload = bytes(range(67))
        fixture = out / 'finite-entropy.bin'
        fixture.write_bytes(payload)
        finite = PrngRig(args, out / 'finite', backend=fixture)
        try:
            finite.monitor('cont')
            finite.vote(True)
            finite.wait_for(lambda: finite.prop('fifo-bytes') >= 64,
                            'finite backend did not fill FIFO')
            actual = [finite.word() for _ in range(16)]
            assert actual == [int.from_bytes(payload[i:i + 4], 'little')
                              for i in range(0, 64, 4)]
            finite.wait_for(lambda: finite.prop('request-pending') == 0 and
                            finite.prop('fifo-bytes') == 3, 'short entropy lost')
            assert finite.status() == 0
            finite.wait_for(lambda: finite.prop('request-pending') == 0,
                            'EOF request remained pending')
            assert finite.prop('empty-responses') > 0
            finite.word()
            assert finite.prop('words-read') == 16
            assert finite.prop('fifo-bytes') == 3
            requests = finite.prop('backend-requests')
            time.sleep(.02)
            assert finite.prop('backend-requests') == requests
            assert sha(fixture) == hashlib.sha256(payload).hexdigest()
            check('finite-backend-exact-consumption-partial-word-and-eof',
                  fixture_bytes=67, consumed_words=16, retained_bytes=3)
            finite.monitor('stop')
            finite.monitor('system_reset')
            assert finite.prop('fifo-bytes') == finite.prop('words-read') == 0
            assert finite.prop('request-pending') == 0
            assert finite.status() == 0
            assert finite.mmio_read(VOTE) & ENABLE == 0
            assert finite.mmio_read(CBCR) & OFF
            check('system-reset-clears-fifo-and-guest-clock-vote')
        finally:
            finite.close()

        check('pending-request-cancel-clock-reset-save-and-source-resume',
              **pending_cancellation(args, out, report))

        empty = PrngRig(args, out / 'empty', backend=Path('/dev/null'))
        try:
            empty.monitor('cont')
            empty.vote(True)
            for _ in range(6):
                assert empty.status() == 0
                empty.wait_for(lambda: empty.prop('request-pending') == 0,
                               'empty backend request did not finish')
            assert empty.prop('empty-responses') >= 1
            assert empty.prop('fifo-bytes') == empty.prop('words-read') == 0
            empty.word()
            assert empty.prop('words-read') == 0
        finally:
            empty.close()
        check('empty-backend-never-reports-data-ready-or-consumes-word')

        missing_path = out / 'does-not-exist'
        missing = PrngRig(args, out / 'missing', backend=missing_path, missing=True)
        try:
            diagnostic = missing.stderr()
            assert str(missing_path) in diagnostic
        finally:
            missing.close()
        check('unavailable-backend-fails-realization-without-guest-execution')

        for name, enabled, stream in (
                ('host-to-off', False, on_stream),
                ('off-to-host', True, off_stream)):
            reject = PrngRig(args, out / name, enabled=enabled, incoming=True)
            try:
                reject.expected_exit = 1
                command_error = None
                try:
                    reject.monitor('migrate-incoming', file_channels(stream))
                except (AssertionError, OSError, EOFError, TimeoutError) as error:
                    command_error = f'{type(error).__name__}: {error}'
                assert reject.wait_exit(30) == 1
                diagnostic = reject.stderr()
                assert 'qcom-sm8250-prng' in diagnostic, diagnostic
                assert 'load of migration failed' in diagnostic.lower(), diagnostic
                report['rejected_migrations'].append(
                    dict(name=name, exit_code=1, diagnostic=diagnostic,
                         incoming_command_error=command_error))
            finally:
                reject.close()
        check('ordinary-profile-mismatch-rejected-both-directions')
        report['processes'] = verify_cleanup(out)
        assert len(report['processes']) == 9
        assert len(report['checks']) == 11
        assert len(report['migrations']) == 3
        assert len(report['rejected_migrations']) == 2
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
