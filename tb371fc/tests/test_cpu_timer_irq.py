#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe CPU timer PPIs, GIC acknowledgements and generated DT routing."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from validate_machine import QMP, ValidationError

PASS = b'TB371FC-CPU-TIMER-IRQ:PASS 24 line observations, 3 IAR/EOI cycles'
INVALID_VALUES = ('0', '9', '11', '13', '14', '15', '16', '4294967295',
                  '-1', '10.5', 'ten')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValidationError(message)


def run_route(args, source, directory, name, machine, ppi, intid):
    directory.mkdir()
    elf = directory / 'probe.elf'
    compile_command = [
        args.cc, '-nostdlib', '-static', '-fno-pie', '-no-pie', '-Os',
        '-Wall', '-Wextra', '-Werror', '-ffreestanding', '-fno-builtin',
        '-fno-stack-protector', '-mgeneral-regs-only', '-Wl,--build-id=none',
        '-DHYP_TIMER_INTID=' + str(intid),
        '-Wl,-T,' + str(source / 'link.ld'), str(source / 'start.S'),
        str(source / 'cpu-irq.c'), '-o', str(elf),
    ]
    report = {
        'name': name, 'machine': machine, 'hyp_timer_ppi': ppi,
        'hyp_timer_intid': intid, 'expected_iar': [30, 27, intid],
        'compile_command': compile_command, 'passed': False,
        'generated_dt_verified': False, 'creation_immutable': False,
        'irq_acknowledged': False, 'guest_executed_at_el2': False,
    }
    start = time.monotonic()
    process = qmp = None
    try:
        subprocess.run(compile_command, check=True)
        report['elf_sha256'] = sha(elf)
        # Keep the UNIX socket path short even when --out is deeply nested.
        with tempfile.TemporaryDirectory(prefix='tb-timer-qmp-') as temporary:
            socket_path = Path(temporary) / 'qmp.sock'
            command = [
                str(args.qemu.resolve()), '-M', machine,
                '-accel', 'tcg,thread=single', '-m', '128M',
                '-display', 'none', '-serial', 'none', '-monitor', 'none',
                '-no-reboot', '-S',
                '-qmp', f'unix:{socket_path},server=on,wait=off',
                '-semihosting-config', 'enable=on,target=native',
                '-kernel', str(elf),
            ]
            if args.icount:
                command += ['-icount', 'shift=0,align=off,sleep=off']
            report['command'] = command
            with (directory / 'probe.log').open('wb') as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=log, stderr=subprocess.STDOUT)
                try:
                    qmp = QMP(socket_path, process, 5)
                    observed = qmp.command('qom-get', {
                        'path': '/machine', 'property': 'hyp-timer-ppi'})
                    report['qom_hyp_timer_ppi'] = observed
                    require(type(observed) is int and observed == ppi,
                            'QOM HYP PPI does not match this route contract')
                    try:
                        qmp.command('qom-set', {
                            'path': '/machine', 'property': 'hyp-timer-ppi',
                            'value': 10 if ppi == 12 else 12})
                    except ValidationError as error:
                        report['mutation_rejection'] = str(error)
                        report['creation_immutable'] = (
                            'hyp-timer-ppi is fixed after machine creation'
                            in str(error))
                    require(report['creation_immutable'],
                            'machine creation did not freeze HYP timer routing')
                    require(qmp.command('qom-get', {
                        'path': '/machine', 'property': 'hyp-timer-ppi'}) == ppi,
                        'rejected mutation changed HYP timer routing')

                    dtb = directory / 'generated.dtb'
                    qmp.command('dumpdtb', {'filename': str(dtb)})
                    expected_cells = [1, 13, 8, 1, 14, 8, 1, 11, 8, 1, ppi, 8]
                    dt_command = [args.fdtget, '-t', 'x', str(dtb),
                                  '/timer', 'interrupts']
                    dt_run = subprocess.run(dt_command, check=True,
                                            capture_output=True, text=True,
                                            timeout=5)
                    actual_cells = [int(x, 16) for x in dt_run.stdout.split()]
                    report['generated_dt'] = {
                        'path': str(dtb), 'sha256': sha(dtb),
                        'command': dt_command, 'interrupt_cells': actual_cells,
                        'expected_interrupt_cells': expected_cells,
                    }
                    report['generated_dt_verified'] = actual_cells == expected_cells
                    require(report['generated_dt_verified'],
                            'generated timer DT contradicts the route contract')
                    qmp.command('cont')
                    report['exit_code'] = process.wait(timeout=15)
                finally:
                    if process.poll() is None:
                        if qmp:
                            try:
                                qmp.command('quit')
                            except (OSError, ValueError, ValidationError):
                                pass
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            report['forced_stop'] = True
                            process.kill()
                            process.wait(timeout=5)
                    if qmp:
                        qmp.close()
                        qmp = None
                    report['process_reaped'] = process.poll() is not None
            output = (directory / 'probe.log').read_bytes()
            report['probe_log_sha256'] = sha(directory / 'probe.log')
            report['iar_observed'] = [
                int(x) for x in re.findall(
                    rb'^TB371FC-CPU-TIMER-IRQ:IAR ([0-9]+) EOI-CLEAR$',
                    output, re.M)]
            report['guest_executed_at_el2'] = (
                output.count(b'TB371FC-CPU-TIMER-IRQ:EL2 E2H=0\n') == 1)
            report['irq_acknowledged'] = (
                report['iar_observed'] == report['expected_iar'])
            report['passed'] = (
                report['exit_code'] == 0 and output.count(PASS) == 1
                and b'TB371FC-CPU-TIMER-IRQ:FAIL' not in output
                and report['guest_executed_at_el2']
                and report['irq_acknowledged']
                and report['generated_dt_verified']
                and report['creation_immutable']
                and report['process_reaped']
                and not report.get('forced_stop', False))
    except (OSError, ValueError, ValidationError, subprocess.SubprocessError) as error:
        report['error'] = f'{type(error).__name__}: {error}'
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (directory / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def reject_invalid(args, out, value, index):
    command = [
        str(args.qemu.resolve()), '-M', args.machine + ',hyp-timer-ppi=' + value,
        '-accel', 'tcg,thread=single', '-m', '128M', '-display', 'none',
        '-serial', 'none', '-monitor', 'none', '-S',
    ]
    report = {'value': value, 'command': command, 'rejected': False}
    log = out / f'invalid-{index:02d}.log'
    try:
        run = subprocess.run(command, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             timeout=5)
        log.write_bytes(run.stdout)
        report['exit_code'] = run.returncode
        report['diagnostic'] = run.stdout.decode(errors='replace')
        expected = (b'hyp-timer-ppi must be 10 or 12'
                    if value.isdecimal() else b'hyp-timer-ppi')
        report['rejected'] = run.returncode != 0 and expected in run.stdout
    except subprocess.TimeoutExpired as error:
        log.write_bytes(error.stdout or b'')
        report['timed_out'] = True
    except OSError as error:
        report['error'] = str(error)
    if log.is_file():
        report['log_sha256'] = sha(log)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off',
                        help='Base machine options; both HYP routes are tested')
    parser.add_argument('--cc', default='aarch64-linux-gnu-gcc')
    parser.add_argument('--fdtget', default='fdtget')
    parser.add_argument('--icount', action='store_true')
    args = parser.parse_args()
    if any(part.split('=', 1)[0] == 'hyp-timer-ppi'
           for part in args.machine.split(',')[1:]):
        parser.error('--machine must leave hyp-timer-ppi unset; both routes are tested')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(out.iterdir()):
        parser.error('use a fresh output directory')
    source = Path(__file__).resolve().parent / 'mmio-timer'
    scripts = Path(__file__).resolve().parents[1] / 'scripts'
    report = {
        'schema_version': 2, 'suite': 'CPU timer output into GIC PPIs',
        'route_contract': 'stock12-linux10', 'qemu_sha256': sha(args.qemu),
        'source_sha256': {name: sha(source / name) for name in
                          ('start.S', 'link.ld', 'cpu-irq.c')},
        'helper_sha256': {name: sha(scripts / name) for name in
                          ('validate_machine.py', 'board_profile.py')},
        'passed': False, 'physical_intid': 30, 'virtual_intid': 27,
        'gicr_ispendr0': '0x17a70200',
        'scope': ('Each route executes at EL2 with E2H=0, preserving the CNTP '
                  'and CNTV bank checks and adding CNTHP: 24 GIC line lifecycle '
                  'observations, then 3 real IAR/EOIR/DIR cycles. The inactive '
                  'HYP alternative must remain clear. This is not an EL1 '
                  'execution test, VHE-alias execution test or silicon measurement.'),
        'routes': [], 'invalid_values': [],
    }
    start = time.monotonic()
    for name, machine, ppi, intid in (
            ('stock-default', args.machine, 12, 28),
            ('linux-dt-ppi10', args.machine + ',hyp-timer-ppi=10', 10, 26)):
        report['routes'].append(run_route(
            args, source, out / name, name, machine, ppi, intid))
    for index, value in enumerate(INVALID_VALUES):
        report['invalid_values'].append(reject_invalid(args, out, value, index))
    for key in ('generated_dt_verified', 'creation_immutable',
                'irq_acknowledged', 'guest_executed_at_el2'):
        report[key] = len(report['routes']) == 2 and all(
            route[key] for route in report['routes'])
    report['invalid_values_rejected'] = (
        len(report['invalid_values']) == len(INVALID_VALUES)
        and all(case['rejected'] for case in report['invalid_values']))
    report['passed'] = (
        len(report['routes']) == 2
        and all(route['passed'] for route in report['routes'])
        and report['invalid_values_rejected'])
    report['elapsed_seconds'] = round(time.monotonic() - start, 3)
    (out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
