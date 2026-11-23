#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check overflow, ceil/floor boundaries and legacy compatibility with big integers."""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import random
import subprocess

if not __debug__:
    raise SystemExit('Acceptance checks require assertions; do not use -O.')

NS = 1000000000
MAX = (1 << 63) - 1
UMAX = (1 << 64) - 1


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--build-config', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--cc', default='cc')
    args = p.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / 'result.json').exists():
        p.error('use a new output directory')
    root = Path(__file__).resolve().parents[2]
    source = root / 'tb371fc/tests/counter-conversion.c'
    header = root / 'include/hw/timer/arm-gt-counter.h'
    elf = out / 'counter-conversion'
    flags = subprocess.check_output(
        ['pkg-config', '--cflags', 'glib-2.0'], text=True).split()
    command = [args.cc, '-O2', '-Wall', '-Wextra', '-Werror',
               '-I' + str(root / 'include'), '-I' + str(args.build_config.resolve()),
               *flags, str(source), '-o', str(elf)]
    subprocess.run(command, check=True)
    cases = []
    randomizer = random.Random(0x371fc)
    for freq in (1, 32768, 19200000, 24000000, 62500000, 1000000000):
        max_tick = (MAX * freq) // NS
        stamps = (0, 1, 51, 52, 53, NS - 1, NS, MAX - 1, MAX)
        ticks = (0, 1, 2, freq - 1, freq, freq + 1,
                 max_tick - 1, max_tick, max_tick + 1, UMAX)
        for exact in (0, 1):
            cases += [(ns, tick, freq, exact) for ns in stamps for tick in ticks]
            cases += [(randomizer.randrange(MAX + 1),
                       randomizer.randrange(UMAX + 1), freq, exact)
                      for _ in range(64)]
    data = ''.join(f'{ns} {tick} {freq} {exact}\n'
                   for ns, tick, freq, exact in cases)
    run = subprocess.run([str(elf)], input=data, text=True,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    rows = run.stdout.splitlines()
    assert len(rows) == len(cases)
    errors = []
    for case, row in zip(cases, rows):
        ns, tick, freq, exact = case
        actual = tuple(map(int, row.split()))
        if exact:
            count = Fraction(ns * freq, NS)
            expiry = Fraction(tick * NS, freq)
            expected = (count.numerator // count.denominator,
                        min(MAX, -(-expiry.numerator // expiry.denominator)))
        else:
            period = max(1, NS // freq)
            expected = (ns // period, min(MAX, tick * period))
        if actual != expected:
            errors.append({'case': case, 'actual': actual, 'expected': expected})
    sha = lambda f: hashlib.sha256(Path(f).read_bytes()).hexdigest()
    report = {
        'schema_version': 1, 'suite': 'Counter conversion arithmetic boundaries',
        'cases': len(cases), 'passed': not errors, 'errors': errors,
        'reference': 'Python Fraction and arbitrary-precision integers',
        'covers': ['fractional-period ceil', 'floor', 'INT64_MAX saturation',
                   'UINT64_MAX tick input', 'legacy integer-period compatibility'],
        'source_sha256': sha(source), 'header_sha256': sha(header),
        'elf_sha256': sha(elf), 'compile_command': command,
    }
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
