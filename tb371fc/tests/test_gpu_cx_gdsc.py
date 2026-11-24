#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""GPU CX GDSC real QTest, three-valued votes, delayed feedback and migration.

The transition delay and boot-collapsed profile are explicit digital reference
contracts. No analogue power-good, GPU execution, or full-board migration is
claimed. Invalid QTest accesses require diagnostics and unchanged state because
the memory command does not return the bus transaction status.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback

import test_refgen as transport
from test_refgen import RefgenRig, check, sha256_file

CONTROL = 0x03d9106c
STATUS = 0x03d91540
POWER = 1 << 31
COLLAPSE = 1
RETAIN = 1 << 11
CONFIG = 0xf800
UNKNOWN = 'unknown modeled state; refusing read'
MISMATCH = 'reset profile or transition-ns differs between source and destination'
MIGRATION_ATTEMPTS = []


def fixture_machine(machine, rpmh):
    fields = machine.split(',')
    fields = [field for field in fields
              if not field.startswith(('rpmh-profile=', 'rpmh-boot-configured=',
                                       'rpmh-cmd-db='))]
    fields += ['rpmh-profile=reference' if rpmh else 'rpmh-profile=off',
               'rpmh-boot-configured=off']
    return ','.join(fields)


class GdscRig(RefgenRig):
    def __init__(self, args, directory, reference=None, incoming=False,
                 intercept='power-on', transition_ns=1000, rpmh=False):
        self.directory = directory
        self.transition_ns = transition_ns
        self.rpmh_fixture = rpmh
        self.reference = reference
        self.active_migration_attempt = None
        self.migration_expected_outcome = 'completed'
        self.directory.mkdir(parents=True, exist_ok=False)
        self.proc = None
        self.qmp_socket = None
        self.qtest_socket = None
        self.qmp_buffer = b''
        self.qtest_buffer = b''
        self.qmp_log = None
        self.qtest_log = None
        self.stderr_stream = None
        self.socket_dir = None
        self.qmp_id = 0
        self.events = []
        self.irq = {}
        self.last_clock = 0
        self.path = args.qom_path
        self.intercept = intercept
        self.log_path = directory / 'qemu.log'
        self.stderr_path = directory / 'qemu.stderr'
        try:
            self.socket_dir = Path(tempfile.mkdtemp(prefix='qgdsc-'))
            qmp_path = str(self.socket_dir / 'm')
            qtest_path = str(self.socket_dir / 't')
            check(max(len(qmp_path.encode()), len(qtest_path.encode())) < 104,
                  'TMPDIR is too long for Unix sockets')
            command = [
                args.qemu, '-machine', fixture_machine(args.machine, rpmh), '-accel', 'qtest',
                '-m', '128M', '-smp', '8', '-display', 'none', '-monitor', 'none',
                '-chardev', 'null,id=gdsc-serial',
                '-serial', 'chardev:gdsc-serial', '-S',
                '-qmp', 'unix:' + qmp_path + ',server=on,wait=off',
                '-qtest', 'unix:' + qtest_path + ',server=on,wait=off',
                '-d', 'unimp,guest_errors', '-D', str(self.log_path),
            ]
            if reference is not None:
                command += [
                    '-global',
                    'qcom-gpu-cx-gdsc.boot-collapsed-reference=' +
                    ('on' if reference else 'off'),
                ]
            command += ['-global',
                        f'qcom-gpu-cx-gdsc.transition-ns={transition_ns}']
            self.socket_paths = dict(qmp=qmp_path, qtest=qtest_path)
            (directory / 'socket-paths.json').write_text(
                json.dumps(self.socket_paths, indent=2) + '\n')
            if incoming:
                command += ['-incoming', 'defer']
            (directory / 'command.json').write_text(
                json.dumps(command, indent=2) + '\n')
            self.stderr_stream = self.stderr_path.open('wb')
            self.qmp_log = (directory / 'qmp.log').open('w', buffering=1)
            self.qtest_log = (directory / 'qtest.log').open('w', buffering=1)
            self.proc = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                         stdout=self.stderr_stream,
                                         stderr=self.stderr_stream)
            deadline = time.monotonic() + 15
            self.qmp_socket = self._connect(qmp_path, deadline)
            self.qtest_socket = self._connect(qtest_path, deadline)
            greeting = json.loads(self._line('qmp'))
            check('QMP' in greeting, greeting)
            self.monitor('qmp_capabilities')
            self.command(f'irq_intercept_out {self.path} {intercept}')
            if not incoming:
                self.reset()
                # QTest still needs a running VM event loop for virtual timers.
                self.monitor('cont')
        except Exception:
            self.close()
            raise


    def monitor(self, command, arguments=None):
        if command == 'migrate':
            attempt = dict(
                attempt=len(MIGRATION_ATTEMPTS) + 1,
                source=str(self.directory),
                uri=arguments['uri'], request_sent=True, request_accepted=False,
                expected_receiver_outcome=self.migration_expected_outcome,
                receiver_outcome=None)
            MIGRATION_ATTEMPTS.append(attempt)
            self.active_migration_attempt = attempt
        result = super().monitor(command, arguments)
        if command == 'migrate':
            self.active_migration_attempt['request_accepted'] = True
        elif command == 'query-migrate' and self.active_migration_attempt is not None:
            self.active_migration_attempt['sender_status'] = result.get('status')
        return result

    def raw_read(self, address):
        return int(self.command(f'readl {address:#x}')[0], 0)

    def raw_write(self, address, value):
        self.command(f'writel {address:#x} {value:#x}')

    def read(self, status=False):
        return self.raw_read(STATUS if status else CONTROL)

    def write(self, value):
        self.raw_write(CONTROL, value)

    def gpio(self, name, value):
        self.command(f'set_irq_in {self.path} {name} 0 {int(value)}')

    def parent(self, state):
        if state is None:
            self.gpio('parent-known', False)
        else:
            self.gpio('parent-on', state)
            self.gpio('parent-known', True)

    def external(self, state):
        if state is None:
            self.gpio('external-known', False)
        else:
            self.gpio('external-vote', state)
            self.gpio('external-known', True)

    def snapshot(self):
        fields = ('control', 'control-known', 'state-known', 'power-on',
                  'transition-pending', 'transition-deadline-ns',
                  'parent-known', 'parent-on', 'external-known', 'external-vote')
        return {name: self.get(name) for name in fields}

    def expect_gpio(self, value):
        # Read unrelated RAM to drain IRQ messages without advancing a timer.
        self.command('readb 0x80000000')
        if value:
            check(0 in self.irq, ('no observed GPIO raise', self.intercept))
        check(self.irq.get(0, False) is value,
              (self.intercept, value, self.irq))

    def expect_feedback(self, value, control=True):
        check(self.get('state-known') is (value is not None), self.snapshot())
        check(self.get('power-on') is bool(value), self.snapshot())
        if value is None:
            before = self.diagnostic_count(UNKNOWN)
            self.read(status=True)
            check(self.diagnostic_count(UNKNOWN) == before + 1,
                  'unknown feedback was silently fabricated')
        else:
            expected = POWER if value else 0
            check(self.read(status=True) == expected, 'separate status feedback')
            if control:
                check(self.read() == self.get('control') | expected,
                      'GDSCR and separate feedback disagree')
        self.expect_gpio((value is not None) if self.intercept == 'state-known'
                         else bool(value))

    def settle(self):
        self.step(self.transition_ns)

    def settle_reference(self):
        self.settle()
        check(self.get('control-known') is True, self.snapshot())
        check(self.get('control') == COLLAPSE, self.snapshot())
        self.expect_feedback(False)

    def run_to(self, when):
        delta = when - self.last_clock
        check(delta > 0, ('non-positive boundary advance', when, self.last_clock))
        self.step(delta)

    def rpmh(self, field, name='cx.lvl'):
        return self.monitor('qom-get',
                            dict(path='/machine/rpmh',
                                 property=f'res.{name}.{field}'))

    def start_cx(self, level, display=False):
        check(self.rpmh_fixture, 'RPMh component fixture required')
        if display:
            base = 0x0af20000
            irq_base, tcs, tcs_base = 0x1c00, 1, 0x1c00 + 0x2a0
        else:
            base = 0x18220000
            irq_base, tcs, tcs_base = 0xd00, 0, 0xd00
        self.raw_write(base + tcs_base + 0x14, 0)
        self.raw_write(base + tcs_base + 0x1c, 0)
        self.raw_write(base + irq_base + 8, 1 << tcs)
        self.raw_write(base + tcs_base + 0x30, 0x10108)
        self.raw_write(base + tcs_base + 0x34, self.rpmh('address'))
        self.raw_write(base + tcs_base + 0x38, level)
        self.raw_write(base + tcs_base + 0x10, 0)
        self.raw_write(base + tcs_base + 0x1c, 1)
        self.raw_write(base + tcs_base + 0x14, 0x10000)
        self.raw_write(base + tcs_base + 0x14, 0x1010000)
        check(self.raw_read(base + tcs_base + 0x3c) == 0x100,
              'RPMh request was not issued')
        return base + tcs_base + 0x3c

    def run_cx(self, level, display=False):
        status = self.start_cx(level, display)
        self.step(1000)
        check(self.raw_read(status) == 0x10100, 'RPMh command did not complete')
        return status


def default_unknown_is_not_initialized_by_time(r):
    check(r.get('boot-collapsed-reference') is False, 'default profile changed')
    r.step(100000)
    check(r.get('control-known') is False, r.snapshot())
    r.expect_feedback(None, control=False)
    before = r.diagnostic_count(UNKNOWN)
    r.read()
    check(r.diagnostic_count(UNKNOWN) == before + 1, 'unknown control read accepted')
    check(r.get('transition-pending') is False, 'unknown inputs invented a transition')


def explicit_reference_does_not_assume_parent_on(r):
    check(r.get('parent-known') is False, 'reference invented a parent')
    check(r.get('external-known') is True and r.get('external-vote') is False,
          'reference external environment')
    r.step(r.transition_ns - 1)
    r.expect_feedback(None)
    r.step(1)
    r.expect_feedback(False)
    check(r.read() == COLLAPSE, 'reference collapsed request')
    r.write(0)
    r.expect_feedback(None)
    check(r.get('parent-known') is False, 'software request invented parent power')


def all_three_valued_and_or_combinations(r):
    rows = []
    for parent in (None, False, True):
        for external in (None, False, True):
            for software in (None, False, True):
                r.reset()
                r.parent(parent)
                r.external(external)
                if software is not None:
                    r.write(0 if software else COLLAPSE)
                request = (True if True in (software, external) else
                           False if software is False and external is False else None)
                expected = (False if parent is False or request is False else
                            True if parent is True and request is True else None)
                r.step(r.transition_ns)
                r.expect_feedback(expected, control=software is not None)
                check(r.get('control-known') is (software is not None),
                      'truth table altered request validity')
                rows.append(dict(parent=parent, external=external,
                                 software=software, expected=expected))
    check(len(rows) == 27, 'truth table incomplete')
    return dict(truth_table=rows)


def two_windows_change_only_at_feedback_deadline(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    deadline = r.get('transition-deadline-ns')
    check(deadline == r.last_clock + r.transition_ns, 'reference deadline')
    r.expect_feedback(False)
    r.run_to(deadline - 1)
    r.expect_feedback(False)
    r.step(1)
    r.expect_feedback(True)
    check(r.get('transition-pending') is False, 'completed transition stayed pending')


def parent_off_preserves_request_and_mmio_access(r):
    r.settle_reference()
    r.parent(False)
    r.write(0x8800)
    r.step(2 * r.transition_ns)
    check(r.read() == 0x8800, 'parent-off discarded or blocked request/config')
    r.expect_feedback(False)
    r.parent(True)
    check(r.get('control') == 0x8800, 'parent return reset programmed request')
    r.settle()
    r.expect_feedback(True)


def unknown_parent_cancels_pending_on_without_late_success(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    old = r.get('transition-deadline-ns')
    r.step(r.transition_ns // 2)
    r.parent(None)
    check(r.get('transition-pending') is False, 'unknown parent retained an on timer')
    r.expect_feedback(None)
    r.run_to(old + r.transition_ns)
    r.expect_feedback(None)
    r.parent(True)
    fresh = r.get('transition-deadline-ns')
    check(fresh == r.last_clock + r.transition_ns, 'resumed stale deadline')
    r.run_to(fresh - 1)
    r.expect_feedback(None)
    r.step(1)
    r.expect_feedback(True)


def known_parent_off_cancels_pending_enable(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    old = r.get('transition-deadline-ns')
    r.step(r.transition_ns // 2)
    r.parent(False)
    check(r.get('transition-pending') is False, 'known-off parent kept enable timer')
    check(r.get('control') == 0, 'parent-off removed software request')
    r.run_to(old + r.transition_ns)
    r.expect_feedback(False)
    check(r.read() == 0, 'parent-off falsely blocked known register read')


def repeated_votes_and_config_do_not_postpone_deadline(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    deadline = r.get('transition-deadline-ns')
    r.step(r.transition_ns // 2)
    r.write(0)
    r.gpio('parent-on', True)
    r.gpio('parent-known', True)
    r.gpio('external-known', True)
    r.write(0x8800)
    check(r.get('transition-deadline-ns') == deadline, 'repeated request delayed feedback')
    r.run_to(deadline - 1)
    r.expect_feedback(False)
    r.step(1)
    r.expect_feedback(True)


def reverse_and_reenable_cancel_old_transition(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    first = r.get('transition-deadline-ns')
    r.step(r.transition_ns // 2)
    r.write(COLLAPSE)
    check(r.get('transition-pending') is False, 'reverse did not cancel')
    r.write(0)
    second = r.get('transition-deadline-ns')
    check(second > first, 'new request reused cancelled deadline')
    r.run_to(first)
    r.expect_feedback(False)
    r.run_to(second)
    r.expect_feedback(True)


def external_vote_keeps_power_without_hlos_vote(r):
    r.settle_reference()
    r.parent(True)
    r.external(True)
    r.settle()
    r.expect_feedback(True)
    check(r.read() & COLLAPSE, 'external vote changed HLOS request')
    # Driver is_enabled tests both power and the absence of HLOS collapse.
    check(not (r.read() & POWER and not r.read() & COLLAPSE),
          'external vote was misreported as an HLOS vote')
    r.write(0)
    r.write(COLLAPSE)
    check(r.get('transition-pending') is False, 'HLOS disable removed external hold')
    r.expect_feedback(True)
    r.external(False)
    r.settle()
    r.expect_feedback(False)


def validity_tracks_unknown_votes_with_hlos_dominance(r):
    r.settle_reference()
    r.parent(True)
    r.external(None)
    r.write(0)
    r.settle()
    r.expect_feedback(True)
    r.write(COLLAPSE)
    r.expect_feedback(None)
    check(r.get('control-known') is True, 'unknown feedback erased request')
    r.external(False)
    r.settle()
    r.expect_feedback(False)


def retain_and_clock_wait_config_are_latched_without_new_power_vote(r):
    r.settle_reference()
    r.parent(True)
    r.write(0xf801)
    r.step(2 * r.transition_ns)
    r.expect_feedback(False)
    check(r.read() == 0xf801, 'retention/wait configuration readback')
    r.write(0xf800)
    deadline = r.get('transition-deadline-ns')
    r.step(r.transition_ns // 2)
    r.write(0x1000)
    check(r.get('transition-deadline-ns') == deadline,
          'config-only change altered reference completion time')
    r.run_to(deadline)
    r.expect_feedback(True)
    r.write(r.read() | COLLAPSE)
    r.settle()
    check(r.read() == 0x1001, 'collapse discarded controller configuration')
    r.expect_feedback(False)


def status_window_is_read_only_and_power_bit_cannot_be_forged(r):
    r.settle_reference()
    before = r.snapshot()
    diagnostic = r.diagnostic_count('unsupported')
    r.raw_write(STATUS, POWER)
    r.raw_write(STATUS, 0)
    check(r.snapshot() == before, 'read-only status changed model state')
    check(r.diagnostic_count('unsupported') == diagnostic + 2,
          'status writes not explicitly rejected')
    r.write(POWER | COLLAPSE)
    check(r.read() == COLLAPSE, 'write forged read-only PWR_ON')
    r.parent(True)
    r.write(0)
    r.settle()
    r.expect_feedback(True)
    # An actual downstream RMW carries bit31 back while changing bit0.
    r.write(r.read() | COLLAPSE)
    r.expect_feedback(True)
    r.settle()
    r.expect_feedback(False)


def unsupported_modes_and_bits_preserve_request_timer_and_feedback(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    r.step(r.transition_ns // 2)
    before = r.snapshot()
    diagnostic = r.diagnostic_count('unsupported')
    for value in (2, 4, 0x100, 0x10000, 0x100000, 0xffffffff):
        r.write(value)
        check(r.snapshot() == before, ('partial unsupported write', value))
    check(r.diagnostic_count('unsupported') == diagnostic + 6,
          'unsupported mode/config writes lacked diagnostics')
    r.run_to(before['transition-deadline-ns'])
    r.expect_feedback(True)


def invalid_subword_accesses_do_not_initialize_or_mutate(r):
    before = r.snapshot()
    count = r.diagnostic_count('unsupported')
    for command in [
        f'writeb {CONTROL:#x} 1',
        f'writew {CONTROL:#x} 1',
        f'writew {CONTROL + 1:#x} 1',
        f'writeb {CONTROL + 3:#x} 1',
        f'readw {CONTROL + 1:#x}',
        f'writeb {STATUS:#x} 1',
    ]:
        r.command(command)
        check(r.snapshot() == before, 'invalid subword access changed controller')
    check(r.diagnostic_count('unsupported') - count >= 6, 'invalid-access diagnostics')


def reset_cancels_old_timer_and_preserves_held_external_input(r):
    r.settle_reference()
    r.parent(True)
    r.write(0)
    r.external(True)
    old = r.get('transition-deadline-ns')
    r.step(r.transition_ns // 2)
    r.reset()
    check(r.get('control') == COLLAPSE, 'reset did not restore collapsed request')
    check(r.get('external-known') is True and r.get('external-vote') is True,
          'reference reset erased a driven external vote')
    fresh = r.get('transition-deadline-ns')
    check(fresh > old, 'reset retained old transition deadline')
    r.run_to(old)
    r.expect_feedback(None)
    r.run_to(fresh)
    r.expect_feedback(True)
    check(r.read() & COLLAPSE, 'external-only post-reset power is not independent')


def default_reset_restores_unknown_control_without_erasing_inputs(r):
    r.parent(True)
    r.external(False)
    r.write(0)
    r.settle()
    r.expect_feedback(True)
    r.reset()
    check(r.get('control-known') is False, 'default reset guessed control')
    check(r.get('parent-known') is True and r.get('parent-on') is True,
          'reset erased parent input')
    check(r.get('external-known') is True and r.get('external-vote') is False,
          'reset erased external input')
    r.step(2 * r.transition_ns)
    r.expect_feedback(None, control=False)


def actual_shared_rpmh_cx_votes_drive_parent_and_feedback(r):
    r.settle_reference()
    check(r.rpmh('present') == 1 and r.rpmh('enabled') == 0,
          'explicit RPMh fixture did not start with CX off')
    check(r.get('parent-known') is True and r.get('parent-on') is False,
          'board did not initialize modeled-resource availability')
    r.write(0)
    r.expect_feedback(False)
    status = r.start_cx(1)
    r.step(999)
    check(r.rpmh('enabled') == 0 and r.get('parent-on') is False,
          'parent changed before RPMh command application')
    r.expect_feedback(False)
    r.step(1)
    check(r.raw_read(status) == 0x10100 and r.rpmh('enabled') == 1,
          'RPMh typed CX command failed')
    check(r.get('parent-on') is True, 'actual RPMh power[1] did not reach GDSC')
    deadline = r.get('transition-deadline-ns')
    r.run_to(deadline - 1)
    r.expect_feedback(False)
    r.step(1)
    r.expect_feedback(True)
    r.run_cx(2, display=True)
    check(r.rpmh('value') == 2, 'display vote did not share CX resource')
    r.run_cx(0)
    r.step(r.transition_ns)
    check(r.rpmh('enabled') == 1 and r.get('parent-on') is True,
          'APPS removal incorrectly removed display hold')
    r.expect_feedback(True)
    r.run_cx(0, display=True)
    check(r.rpmh('enabled') == 0 and r.get('parent-on') is False,
          'last vote removal did not reach parent input')
    r.settle()
    r.expect_feedback(False)
    return dict(cx_address=r.rpmh('address'), rpmh_power_gpio=1,
                apps_and_display_share_resource=True)


BEHAVIOR = [
    (default_unknown_is_not_initialized_by_time, None, 'state-known', False),
    (explicit_reference_does_not_assume_parent_on, True, 'state-known', False),
    (all_three_valued_and_or_combinations, False, 'state-known', False),
    (two_windows_change_only_at_feedback_deadline, True, 'power-on', False),
    (parent_off_preserves_request_and_mmio_access, True, 'power-on', False),
    (unknown_parent_cancels_pending_on_without_late_success, True, 'state-known', False),
    (known_parent_off_cancels_pending_enable, True, 'power-on', False),
    (repeated_votes_and_config_do_not_postpone_deadline, True, 'power-on', False),
    (reverse_and_reenable_cancel_old_transition, True, 'power-on', False),
    (external_vote_keeps_power_without_hlos_vote, True, 'power-on', False),
    (validity_tracks_unknown_votes_with_hlos_dominance, True, 'state-known', False),
    (retain_and_clock_wait_config_are_latched_without_new_power_vote, True, 'power-on', False),
    (status_window_is_read_only_and_power_bit_cannot_be_forged, True, 'power-on', False),
    (unsupported_modes_and_bits_preserve_request_timer_and_feedback, True, 'power-on', False),
    (invalid_subword_accesses_do_not_initialize_or_mutate, False, 'state-known', False),
    (reset_cancels_old_timer_and_preserves_held_external_input, True, 'power-on', False),
    (default_reset_restores_unknown_control_without_erasing_inputs, False, 'state-known', False),
    (actual_shared_rpmh_cx_votes_drive_parent_and_feedback, True, 'power-on', True),
]


def transfer(source, target, directory):
    source.step(1)
    now = source.last_clock
    source.monitor('stop')
    if now > target.last_clock:
        target.step(now - target.last_clock)
    check(target.last_clock == now, 'migration clocks differ')
    sockets = Path(tempfile.mkdtemp(prefix='qgdsc-migration-'))
    uri = 'unix:' + str(sockets / 'm')
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'uri.txt').write_text(uri + '\n')
    before = source.snapshot()
    target.monitor('migrate-incoming', dict(uri=uri))
    source.monitor('migrate', dict(uri=uri))
    source.wait_for(lambda: source.monitor('query-migrate').get('status')
                    in ('completed', 'failed'), 'outgoing GDSC migration', timeout=30)
    result = source.monitor('query-migrate')
    check(result['status'] == 'completed', result)
    target.wait_for(lambda: target.monitor('query-status')['status'] != 'inmigrate',
                    'incoming GDSC migration', timeout=30)
    check(not target.monitor('query-status')['running'], 'destination resumed itself')
    check(target.snapshot() == before, ('controller state changed on load',
                                       before, target.snapshot()))
    source.active_migration_attempt['receiver_outcome'] = 'completed'
    source.active_migration_attempt['receiver_state_verified'] = True
    return dict(source_state=before, source_transfer=result, virtual_ns=now,
                migration_uri=uri, source_sockets=source.socket_paths,
                destination_sockets=target.socket_paths)


def migration_case(args, directory, mode):
    source = target = None
    reference = mode != 'unknown'
    intercept = 'state-known' if mode == 'unknown' else 'power-on'
    try:
        source = GdscRig(args, directory / 'source', reference=reference,
                         intercept=intercept)
        if reference:
            source.settle_reference()
            source.parent(True)
            if mode == 'external':
                source.external(True)
                source.settle()
            else:
                source.write(0)
                if mode == 'pending-off':
                    source.settle()
                    source.write(COLLAPSE)
                source.step(source.transition_ns // 2)
        target = GdscRig(args, directory / 'target', reference=reference,
                         incoming=True, intercept=intercept)
        receipt = transfer(source, target, directory / 'transfer')
        saved = receipt['source_state']
        if mode == 'unknown':
            target.expect_feedback(None, control=False)
            target.monitor('cont')
            target.parent(True)
            target.external(False)
            target.write(0)
            target.settle()
            target.expect_feedback(True)
            target.reset()
            target.step(target.transition_ns)
            target.expect_feedback(None, control=False)
        elif mode in ('pending-on', 'pending-off'):
            old_power = mode == 'pending-off'
            target.expect_feedback(old_power)
            check(saved['transition-pending'] is True, 'source was not pending')
            target.monitor('cont')
            target.run_to(saved['transition-deadline-ns'] - 1)
            target.expect_feedback(old_power)
            target.step(1)
            target.expect_feedback(not old_power)
            target.step(2 * target.transition_ns)
            target.expect_feedback(not old_power)
        else:
            target.expect_feedback(True)
            target.monitor('cont')
            target.reset()
            check(target.get('external-known') is True and
                  target.get('external-vote') is True,
                  'held external input lost on post-load reset')
            target.settle()
            target.expect_feedback(True)
            check(target.read() & COLLAPSE, 'reset did not restore HLOS collapse')
            target.external(False)
            target.settle()
            target.expect_feedback(False)
        return receipt
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


def migration_actual_rpmh_and_gdsc_pending_in_two_stages(args, directory):
    rigs = []
    try:
        first = GdscRig(args, directory / 'first', reference=True, rpmh=True)
        rigs.append(first)
        first.settle_reference()
        first.write(0)
        first.expect_feedback(False)
        rpmh_deadline = first.last_clock + 1000
        status = first.start_cx(1)
        first.step(400)
        check(first.raw_read(status) == 0x100 and first.rpmh('enabled') == 0,
              'first source must have an in-flight RPMh command')
        second = GdscRig(args, directory / 'second', reference=True,
                         rpmh=True, incoming=True)
        rigs.append(second)
        receipt1 = transfer(first, second, directory / 'transfer-rpmh')
        second.expect_feedback(False)
        check(second.rpmh('enabled') == 0 and second.get('parent-on') is False,
              'load ordering invented parent availability')
        second.monitor('cont')
        second.run_to(rpmh_deadline - 1)
        check(second.raw_read(status) == 0x100, 'RPMh completed early after migration')
        second.step(1)
        check(second.raw_read(status) == 0x10100 and second.rpmh('enabled') == 1,
              'restored RPMh command did not complete')
        check(second.get('parent-on') is True, 'restored actual parent GPIO missing')
        second.expect_feedback(False)
        deadline = second.get('transition-deadline-ns')
        check(deadline == rpmh_deadline + second.transition_ns,
              'GDSC did not start at RPMh completion')
        second.step(400)
        third = GdscRig(args, directory / 'third', reference=True,
                        rpmh=True, incoming=True)
        rigs.append(third)
        receipt2 = transfer(second, third, directory / 'transfer-gdsc')
        check(third.rpmh('enabled') == 1 and third.get('parent-on') is True,
              'restored RPMh and GDSC parent states disagree')
        third.expect_feedback(False)
        third.monitor('cont')
        third.run_to(deadline - 1)
        third.expect_feedback(False)
        third.step(1)
        third.expect_feedback(True)
        third.run_cx(0)
        check(third.get('parent-on') is False, 'post-load actual parent release missing')
        third.settle()
        third.expect_feedback(False)
        return dict(transfers=[receipt1, receipt2],
                    scope='actual shared RPMh output; two process transfers')
    finally:
        for rig in rigs:
            rig.close()


def migration_rejected(args, directory, source_reference, target_reference,
                       source_delay=1000, target_delay=1000):
    source = target = None
    try:
        source = GdscRig(args, directory / 'source', reference=source_reference,
                         transition_ns=source_delay)
        source.external(False)
        source.write(COLLAPSE)
        source.settle()
        source.expect_feedback(False)
        source.step(1)
        now = source.last_clock
        source.monitor('stop')
        target = GdscRig(args, directory / 'target', reference=target_reference,
                         transition_ns=target_delay, incoming=True)
        if now > target.last_clock:
            target.step(now - target.last_clock)
        sockets = Path(tempfile.mkdtemp(prefix='qgdsc-reject-'))
        uri = 'unix:' + str(sockets / 'm')
        (directory / 'migration-uri.txt').write_text(uri + '\n')
        target.monitor('migrate-incoming', dict(uri=uri))
        source.migration_expected_outcome = 'rejected'
        source.monitor('migrate', dict(uri=uri))
        target.wait_for(lambda: target.proc.poll() is not None,
                        'GDSC configuration rejection', timeout=30)
        code = target.proc.returncode
        check(code != 0, 'destination did not reject migration')
        check(MISMATCH in target.stderr_path.read_text(errors='replace'),
              'destination failed for a different reason')
        source.wait_for(lambda: source.monitor('query-migrate').get('status')
                        in ('completed', 'failed'), 'sender termination', timeout=30)
        source.active_migration_attempt['receiver_outcome'] = 'rejected'
        source.active_migration_attempt['destination_exit'] = code
        source.active_migration_attempt['rejection_diagnostic'] = MISMATCH
        return dict(source_reference=source_reference,
                    destination_reference=target_reference,
                    source_delay_ns=source_delay, destination_delay_ns=target_delay,
                    source_transfer=source.monitor('query-migrate'),
                    destination_exit=code, rejection_diagnostic=MISMATCH,
                    migration_uri=uri, source_sockets=source.socket_paths,
                    destination_sockets=target.socket_paths)
    finally:
        if source is not None:
            source.close()
        if target is not None:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', default='tb371fc,ufs-enabled=off')
    parser.add_argument('--qom-path', default='/machine/gpu-cx-gdsc')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    report = dict(
        schema_version=1, suite='GPU CX GDSC normal/votable actual QTest',
        expected_checks=26, migration_expected_checks=8,
        expected_migration_attempts=9, migration_attempts=MIGRATION_ATTEMPTS,
        passed=False, all_passed=False, success=False, checks=[], migrations=[],
        qemu=args.qemu, qemu_sha256=sha256_file(args.qemu),
        test_sha256=sha256_file(__file__),
        transport_sha256=sha256_file(transport.__file__),
        machine=args.machine, qom_path=args.qom_path,
        output=str(args.out), socket_directories_retained=True,
        scope='Two four-byte register windows; digital reference feedback; '
              'three-valued parent/votes; UFS disabled for component migration; '
              'no analogue/GPU/full-board claim')
    cases = []
    for function, reference, intercept, rpmh in BEHAVIOR:
        def run(directory, function=function, reference=reference,
                intercept=intercept, rpmh=rpmh):
            rig = GdscRig(args, directory, reference=reference,
                           intercept=intercept, rpmh=rpmh)
            try:
                detail = function(rig) or {}
                detail.update(reference=reference, intercepted_output=intercept,
                              rpmh_fixture=rpmh, sockets=rig.socket_paths)
                return detail
            finally:
                rig.close()
        cases.append((function.__name__, run))
    for mode in ('unknown', 'pending-on', 'pending-off', 'external'):
        def run(directory, mode=mode):
            return migration_case(args, directory, mode)
        cases.append(('migration_' + mode.replace('-', '_'), run))
    cases.append(('migration_actual_rpmh_and_gdsc_pending_in_two_stages',
                  lambda directory:
                  migration_actual_rpmh_and_gdsc_pending_in_two_stages(args, directory)))
    for name, source_reference, target_reference, source_delay, target_delay in [
        ('migration_unknown_to_reference_profile_rejected', False, True, 1000, 1000),
        ('migration_reference_to_unknown_profile_rejected', True, False, 1000, 1000),
        ('migration_transition_delay_mismatch_rejected', True, True, 1000, 2000),
    ]:
        def run(directory, sr=source_reference, tr=target_reference,
                sd=source_delay, td=target_delay):
            return migration_rejected(args, directory, sr, tr, sd, td)
        cases.append((name, run))
    check(len(BEHAVIOR) == 18 and len(cases) == report['expected_checks'],
          'test plan count mismatch')
    for index, (name, function) in enumerate(cases):
        directory = args.out / f'{index:02d}'
        started = time.monotonic()
        try:
            detail = function(directory)
            result = dict(test=name, status='pass', passed=True, detail=detail)
        except Exception:
            result = dict(test=name, status='fail', passed=False,
                          error=traceback.format_exc())
        result.update(seconds=round(time.monotonic() - started, 4),
                      directory=str(directory))
        report['checks'].append(result)
        if index >= len(BEHAVIOR):
            report['migrations'].append(result.copy())
        report['completed_migration_attempts'] = sum(
            item['receiver_outcome'] == 'completed' for item in MIGRATION_ATTEMPTS)
        report['rejected_migration_attempts'] = sum(
            item['receiver_outcome'] == 'rejected' for item in MIGRATION_ATTEMPTS)
        report['completed_checks'] = len(report['checks'])
        report['passed_checks'] = sum(item['passed'] for item in report['checks'])
        report['passed'] = report['all_passed'] = report['success'] = (
            len(report['checks']) == report['expected_checks'] and
            len(report['migrations']) == report['migration_expected_checks'] and
            len(MIGRATION_ATTEMPTS) == report['expected_migration_attempts'] and
            all(item['request_accepted'] and item['receiver_outcome'] ==
                item['expected_receiver_outcome'] for item in MIGRATION_ATTEMPTS) and
            report['completed_migration_attempts'] == 6 and
            report['rejected_migration_attempts'] == 3 and
            all(item['passed'] for item in report['checks']))
        (args.out / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
        if not result['passed']:
            break
    print(json.dumps(report, indent=2))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
