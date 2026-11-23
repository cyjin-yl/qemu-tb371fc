#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe real CPU timer output lines at their GIC redistributor inputs."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    parser.add_argument('--icount', action='store_true')
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        parser.error('use a fresh output directory')
    source = Path(__file__).resolve().parent / 'mmio-timer'
    elf = out / 'probe.elf'
    compile_command = [
        args.cc, '-nostdlib', '-static', '-fno-pie', '-no-pie', '-Os',
        '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-fno-stack-protector', '-mgeneral-regs-only', '-Wl,--build-id=none',
        '-Wl,-T,' + str(source / 'link.ld'), str(source / 'start.S'),
        str(source / 'cpu-irq.c'), '-o', str(elf),
    ]
    subprocess.run(compile_command, check=True)
    command = [
        str(args.qemu.resolve()), '-M', args.machine,
        '-accel', 'tcg,thread=single', '-m', '128M', '-display', 'none',
        '-serial', 'none', '-monitor', 'none', '-no-reboot',
        '-semihosting-config', 'enable=on,target=native', '-kernel', str(elf),
    ]
    if args.icount:
        command += ['-icount', 'shift=0,align=off,sleep=off']
    report = {
        'schema_version': 1, 'suite': 'CPU timer output into GIC PPIs',
        'qemu_sha256': sha(args.qemu), 'elf_sha256': sha(elf),
        'source_sha256': {name: sha(source / name) for name in
                          ('start.S', 'link.ld', 'cpu-irq.c')},
        'compile_command': compile_command, 'command': command,
        'passed': False, 'physical_intid': 30, 'virtual_intid': 27,
        'gicr_ispendr0': '0x17a70200',
        'scope': ('16 GIC input-line observations using guest MMIO after '
                  'real MRS/MSR; masked expiry, unmask, mask, disable, '
                  'past compare and future compare. No CTL read before '
                  'the line observation; no interrupt exception handler.'),
    }
    start = time.monotonic()
    try:
        run = subprocess.run(command, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=15)
        (out / 'probe.log').write_bytes(run.stdout)
        report['exit_code'] = run.returncode
        report['passed'] = (run.returncode == 0 and
                            b'TB371FC-CPU-TIMER-IRQ:PASS 16 line observations'
                            in run.stdout)
    except subprocess.TimeoutExpired as error:
        (out / 'probe.log').write_bytes(error.stdout or b'')
        report['timed_out'] = True
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
