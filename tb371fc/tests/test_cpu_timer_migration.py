#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Migrate real pending CPU generic timers at a read-only GDB checkpoint."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from boot_smoke_gdb import QMP, GDB, connect

if not __debug__:
    raise SystemExit('Acceptance checks require assertions; do not use -O.')

FIELDS = ('magic', 'before', 'offset', 'physical_compare', 'virtual_compare',
          'resumed', 'physical_expired', 'virtual_expired', 'passed',
          'hyp_compare', 'hyp_pending_before', 'hyp_expired',
          'hyp_pending_after', 'hyp_pending_disabled')
RECORD = struct.Struct('<' + 'Q' * len(FIELDS))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def stopped(gdb, address, deadline):
    reply = gdb.receive(deadline)
    assert reply.startswith(('T05', 'S05')), reply
    match = re.search(r'thread:([^;]+)', reply)
    if match:
        assert gdb.request('Hg' + match.group(1)) == 'OK'
    pc = int.from_bytes(bytes.fromhex(gdb.request('p20')), 'little')
    ps = int.from_bytes(bytes.fromhex(gdb.request('p21')), 'little')
    allowed = address if isinstance(address, tuple) else (address,)
    assert pc in allowed and ps & 15 == 9, (hex(pc), hex(ps), allowed)
    return {'reply': reply, 'pc': pc, 'pstate': ps}


def wait_migration(qmp, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = qmp.command('query-migrate')
        if state.get('status') == 'completed':
            return state
        if state.get('status') in ('failed', 'cancelled'):
            raise AssertionError(state)
        if process.poll() is not None:
            raise AssertionError(f'QEMU exited during migration: {process.returncode}')
        time.sleep(.01)
    raise TimeoutError('CPU migration did not complete')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    parser.add_argument('--elf', type=Path,
                        help='Reuse this exact probe ELF for byte-identical controls')
    parser.add_argument('--without-migration', action='store_true',
                        help='Run the identical guest ELF without GDB or migration')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        parser.error('use a fresh output directory')
    src = Path(__file__).resolve().parent / 'mmio-timer'
    elf = out / 'probe.elf'
    compile_command = [
        args.cc, '-nostdlib', '-static', '-fno-pie', '-no-pie', '-Os',
        '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-fno-stack-protector', '-mgeneral-regs-only', '-Wl,--build-id=none',
        '-Wl,-T,' + str(src / 'link.ld'), str(src / 'start.S'),
        str(src / 'cpu-migration.c'), '-o', str(elf),
    ]
    if args.elf:
        elf = args.elf.resolve()
        compile_command = None
    else:
        subprocess.run(compile_command, check=True)
    table = {}
    for row in subprocess.check_output(['aarch64-linux-gnu-nm', '-n', str(elf)],
                                       text=True).splitlines():
        parts = row.split()
        if len(parts) == 3:
            table[parts[2]] = int(parts[0], 16)
    checkpoint = table['timer_migration_checkpoint']
    done = table['timer_migration_done']
    finish = table['finish']
    record = table['timer_migration_result']
    report = {
        'schema_version': 2, 'suite': 'Real CPU physical/virtual/hypervisor timer migration',
        'qemu_sha256': sha(args.qemu), 'elf_sha256': sha(elf),
        'machine': args.machine, 'ram_per_process': '128 MiB',
        'compile_command': compile_command, 'passed': False,
        'provided_elf': str(elf) if args.elf else None,
        'scope': 'Read-only GDB checkpoint, actual QMP migration and guest timer expiry',
        'hyp_timer_intid': 28,
        'hyp_timer_observation': ('GICR pending before and after expiry, then '
                                  'clear after disable; no CNTHP CTL read '
                                  'or CNTHP write between restore and expiry'),
        'symbols': {k: table[k] for k in
                    ('timer_migration_checkpoint', 'timer_migration_done',
                     'timer_migration_result')},
    }
    if args.without_migration:
        report['suite'] = 'Real CPU timer expiry without migration'
        report['scope'] = 'Identical guest ELF, no debugger and no migration'
        command = [
            str(args.qemu.resolve()), '-M', args.machine,
            '-accel', 'tcg,thread=single', '-m', '128M',
            '-display', 'none', '-serial', 'none', '-monitor', 'none',
            '-semihosting-config', 'enable=on,target=native',
            '-kernel', str(elf), '-no-reboot',
        ]
        report['command'] = command
        start = time.monotonic()
        try:
            run = subprocess.run(command, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=15)
            (out / 'probe.log').write_bytes(run.stdout)
            report['exit_code'] = run.returncode
            report['passed'] = (run.returncode == 0 and
                               b'TB371FC-CPU-TIMER-MIGRATION:PASS' in run.stdout)
        except subprocess.TimeoutExpired as error:
            (out / 'probe.log').write_bytes(error.stdout or b'')
            report['timed_out'] = True
        report['elapsed_seconds'] = round(time.monotonic() - start, 3)
        (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        return 0 if report['passed'] else 1

    processes, streams, controls, debuggers = [], [], [], []
    start = time.monotonic()
    try:
        for name, incoming in (('source', False), ('target', True)):
            d = out / name
            d.mkdir()
            log = (d / 'qemu.log').open('wb')
            streams.append(log)
            cmd = [
                str(args.qemu.resolve()), '-M', args.machine,
                '-accel', 'tcg,thread=single', '-m', '128M',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-semihosting-config', 'enable=on,target=native',
                '-kernel', str(elf), '-no-reboot', '-S',
                '-qmp', 'unix:' + str(d / 'qmp') + ',server=on,wait=off',
                '-gdb', 'unix:' + str(d / 'gdb') + ',server=on,wait=off',
            ]
            if incoming:
                cmd += ['-incoming', 'defer']
            report[name + '_command'] = cmd
            proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
            processes.append(proc)
            qmp = QMP(connect(d / 'qmp', proc, time.monotonic() + 5))
            qmp.command('qmp_capabilities')
            controls.append(qmp)
            gdb = GDB(connect(d / 'gdb', proc, time.monotonic() + 5))
            gdb.request('qSupported:multiprocess+')
            debuggers.append(gdb)
        sq, tq = controls
        sg, tg = debuggers
        assert sg.request(f'Z0,{checkpoint:x},4') == 'OK'
        sg.send('c')
        report['source_stop'] = stopped(sg, checkpoint, time.monotonic() + 10)
        before = RECORD.unpack(sg.memory(record, RECORD.size))
        report['source_result'] = dict(zip(FIELDS, before))
        assert before[0] == 0x54494d45524d4947 and before[8] == 0
        assert sg.request(f'z0,{checkpoint:x},4') == 'OK'
        # QMP stop leaves an already-debug-stopped VM in DEBUG, which is
        # not a legal incoming runstate. Resume into the guest's bounded
        # settling loop, then request the supported PAUSED state.
        sg.send('c')
        sq.command('stop')
        report['source_runstate'] = sq.command('query-status')
        assert report['source_runstate']['status'] == 'paused'
        reply = sg.receive(time.monotonic() + 5)
        assert reply.startswith(('T02', 'S02', 'T05', 'S05')), reply
        report['source_pause_reply'] = reply
        before = RECORD.unpack(sg.memory(record, RECORD.size))
        report['source_result'] = dict(zip(FIELDS, before))
        assert before[5:9] == (0, 0, 0, 0), before
        assert before[9] == before[1] + 3840000 and before[10:] == (0, 0, 0, 0)
        assert tg.request(f'Z0,{done:x},4') == 'OK'
        assert tg.request(f'Z0,{finish:x},4') == 'OK'
        uri = 'unix:' + str(out / 'migration.sock')
        tq.command('migrate-incoming', {'uri': uri})
        sq.command('migrate', {'uri': uri})
        report['source_migration'] = wait_migration(sq, processes[0])
        report['target_migration'] = wait_migration(tq, processes[1])
        tg.send('c')
        report['target_stop'] = stopped(tg, (done, finish), time.monotonic() + 15)
        after = RECORD.unpack(tg.memory(record, RECORD.size))
        report['target_result'] = dict(zip(FIELDS, after))
        if report['target_stop']['pc'] == finish:
            report['guest_failure_code'] = int.from_bytes(
                bytes.fromhex(tg.request('p0')), 'little')
            report['failure_registers'] = {
                'x' + str(i): int.from_bytes(bytes.fromhex(tg.request(f'p{i:x}')),
                                            'little')
                for i in range(10)}
        assert before[:5] == after[:5], (before, after)
        assert before[1] <= after[5] < before[3]
        assert after[6] >= before[3] and after[7] >= before[4]
        assert after[8] == 1
        assert after[9:11] == before[9:11]
        assert after[9] <= after[11] < after[9] + 1920000
        assert after[12:] == (1, 0)
        assert tg.request(f'z0,{done:x},4') == 'OK'
        assert tg.request(f'z0,{finish:x},4') == 'OK'
        tg.send('c')
        processes[1].wait(timeout=5)
        report['target_exit_code'] = processes[1].returncode
        assert processes[1].returncode == 0
        assert b'TB371FC-CPU-TIMER-MIGRATION:PASS' in (
            out / 'target/qemu.log').read_bytes()
        report['passed'] = True
    except Exception:
        report['error'] = traceback.format_exc()
    finally:
        report['process_exit_codes_before_cleanup'] = [p.poll() for p in processes]
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        for stream in streams:
            stream.close()
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
