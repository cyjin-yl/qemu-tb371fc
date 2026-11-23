#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Execute real MRS/MMIO reads to check the common counter and separate offsets."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess
import time


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--qemu', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    p.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    p.add_argument('--icount-rate', action='store_true')
    p.add_argument('--counter-mode', choices=['exact', 'legacy'], default='exact')
    args = p.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        p.error('use a fresh output directory')
    source = Path(__file__).resolve().parent / 'mmio-timer'
    elf = out / 'probe.elf'
    compile_command = [
        args.cc, '-nostdlib', '-static', '-fno-pie', '-no-pie', '-Os',
        '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-fno-stack-protector', '-mgeneral-regs-only', '-Wl,--build-id=none',
        '-Wl,-T,' + str(source / 'link.ld'),
        str(source / 'start.S'), str(source / 'probe.c'), '-o', str(elf),
    ]
    if args.icount_rate:
        compile_command.insert(1, '-DICOUNT_RATE_TEST')
    subprocess.run(compile_command, check=True)
    command = [
        str(args.qemu.resolve()), '-M', args.machine, '-accel', 'tcg,thread=single',
        '-m', '128M', '-display', 'none', '-serial', 'none', '-monitor', 'none',
        '-semihosting-config', 'enable=on,target=native',
        '-kernel', str(elf), '-no-reboot',
    ]
    if args.icount_rate:
        command.extend(['-icount', 'shift=0,align=off,sleep=off'])
    start = time.monotonic()
    timed_out = False
    try:
        run = subprocess.run(command, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, timeout=30)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        run = subprocess.CompletedProcess(command, None, error.stdout or b'')
    (out / 'probe.log').write_bytes(run.stdout)
    report = {
        'schema_version': 1, 'suite': 'CPU MRS vs MMIO architectural counter',
        'qemu_sha256': sha(args.qemu), 'elf_sha256': sha(elf),
        'source_sha256': {f.name: sha(f) for f in source.iterdir() if f.is_file()},
        'compile_command': compile_command, 'command': command,
        'exit_code': run.returncode, 'timed_out': timed_out, 'elapsed_seconds': round(time.monotonic()-start, 3),
        'passed': run.returncode == 0 and
                  b'TB371FC-MMIO-MRS:PASS 1024 comparisons' in run.stdout,
        'scope': '256 reads of each counter/offset; common timebase, independent virtual offsets',
    }
    if args.icount_rate:
        match = re.search(rb'TB371FC-ICOUNT-RATE:0x([0-9a-f]{16})', run.stdout)
        ticks = int(match.group(1), 16) if match else None
        instructions = 65536 * 18 + 2
        # At most one tick of endpoint quantization; allow another tick
        # for the implementation's exact MRS instruction-count boundary.
        reference = (instructions * 19200000 // 1000000000
                     if args.counter_mode == 'exact' else instructions // 52)
        report['rate_measurement'] = {
            'instruction_iterations': 65536, 'instructions_per_iteration': 18,
            'bracketing_instructions': 2, 'icount_nanoseconds_per_instruction': 1,
            'reference_interval_ns': instructions, 'observed_counter_delta': ticks,
            'reference_delta_floor': reference, 'endpoint_tolerance_ticks': 2,
            'counter_mode': args.counter_mode,
            'independent_reference': 'QEMU icount instruction count, not CNTFRQ or MMIO',
        }
        report['cpu_timer_probe_passed'] = (
            b'TB371FC-CPU-TIMERS:PASS physical virtual signed-tval wrap' in run.stdout)
        report['passed'] = (report['passed'] and ticks is not None and
                            abs(ticks - reference) <= 2 and
                            report['cpu_timer_probe_passed'])
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
