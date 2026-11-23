#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Actual QTest coverage of typed RPMh services and explicit reference command-db.

Runs real QEMU MMIO/QMP/socket operations. The fixture addresses/aux data are
synthetic and are never described as captured SM8250 firmware data. Component
migration requires ufs-enabled=off; it does not validate full-board migration.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
import traceback

if not __debug__:
    raise RuntimeError('QTest dependencies require Python without -O')

from test_gcc_sm8250 import GccRig

TCS_BASE = 0xd00
TCS_STRIDE = 0x2a0
MSG = 0x10108
ISSUED = 0x100
COMPLETE = 0x10000
FIXTURE = Path(__file__).resolve().parents[1] / 'fixtures/rpmh-reference.json'
PROFILE = json.loads(FIXTURE.read_text())
RESOURCES = {r['name']: r for r in PROFILE['resources']}


def check(condition, detail):
    if not condition:
        raise AssertionError(detail)


class RpmhRig(GccRig):
    def __init__(self, args, directory, incoming=False, intercept='sysbus-irq'):
        super().__init__(args, directory, incoming=incoming, intercept=intercept)
        self.initial_xo = self.resource('xo.lvl', 'value')
        self.boot_configured = self.monitor('qom-get', {
            'path': args.qom_path, 'property': 'boot-configured'})
        if not incoming and self.initial_xo:
            self.run_one('xo.lvl', 0)
            self.clear()

    def resource(self, name, field='value'):
        return self.monitor('qom-get', {'path': self.args.qom_path,
                                       'property': f'res.{name}.{field}'})

    def dread(self, offset, drv=2):
        return self.read(drv * 0x10000 + offset)

    def dwrite(self, offset, value, drv=2):
        self.write(drv * 0x10000 + offset, value)

    def tcs(self, tcs, offset):
        return TCS_BASE + tcs * TCS_STRIDE + offset

    def program(self, name, value, field=0, drv=2, tcs=0, slot=0, msg=MSG,
                address=None):
        address = RESOURCES[name]['address'] + field * 4 if address is None else address
        base = self.tcs(tcs, 0x30 + slot * 0x14)
        self.dwrite(base, msg, drv)
        self.dwrite(base + 4, address, drv)
        self.dwrite(base + 8, value, drv)

    def trigger(self, mask=1, drv=2, tcs=0, wait=0):
        self.dwrite(self.tcs(tcs, 0x10), wait, drv)
        self.dwrite(self.tcs(tcs, 0x1c), mask, drv)
        self.dwrite(self.tcs(tcs, 0x14), 0x10000, drv)
        self.dwrite(self.tcs(tcs, 0x14), 0x1010000, drv)

    def status(self, slot=0, drv=2, tcs=0):
        return self.dread(self.tcs(tcs, 0x3c + slot * 0x14), drv)

    def clear(self, drv=2, tcs=0):
        self.dwrite(self.tcs(tcs, 0x14), 0, drv)
        self.dwrite(self.tcs(tcs, 0x1c), 0, drv)
        self.dwrite(TCS_BASE + 8, 1 << tcs, drv)

    def run_one(self, name, value, field=0, drv=2, tcs=0, msg=MSG,
                address=None):
        self.clear(drv, tcs)
        self.program(name, value, field, drv, tcs, msg=msg, address=address)
        self.trigger(drv=drv, tcs=tcs)
        self.step(1000)

    def irq_level(self, drv, expected):
        self.dread(TCS_BASE + 4, drv)
        check(self.irq[drv] == expected, (drv, expected, self.irq))

    def db(self):
        response = self.command(f'read {self.args.cmd_db_base:#x} 0x20000')[0]
        return bytes.fromhex(response.removeprefix('0x'))

    def expect_stall(self, before, name, drv=2, tcs=0):
        check(self.status(drv=drv, tcs=tcs) == ISSUED,
              'unsupported command was acknowledged or never issued')
        check(not self.dread(TCS_BASE + 4, drv) & (1 << tcs),
              'unsupported command raised completion IRQ')
        check(self.resource(name) == before, 'unsupported command changed resource')
        self.irq_level(drv, False)


def identity_geometry_and_explicit_handoff(r):
    check(r.initial_xo == (3 if r.boot_configured else 0), 'unexpected initial XO vote')
    for drv in range(3):
        check(r.dread(0, drv) == 0x20700, '2.7 protocol revision')
        check(r.dread(4, drv) == 0, 'autonomous solver must not be advertised')
        check(r.dread(0xc, drv) == 0x80008208, 'TCS geometry')
        check(r.dread(TCS_BASE, drv) == 0, 'reset interrupt mask')
    r.expect_hz('bi_tcxo', 0)


def exact_reference_command_db_and_read_only_mapping(r):
    blob = r.db()
    check(hashlib.sha256(blob).hexdigest() == PROFILE['command_db_sha256'],
          'C-built database differs from independent Python fixture')
    entries = {}
    for group in range(3):
        slave, head, aux, count, version, _, _, _ = struct.unpack_from('<8H', blob, 8 + 16 * group)
        check(slave == group + 3 and version == 0x100, 'database slave/header')
        for i in range(count):
            name, _, _, address, length, offset = struct.unpack_from('<8sIIIHH', blob, 144 + head + i * 24)
            name = name.rstrip(b'\0').decode()
            entries[name] = (address, blob[144 + aux + offset:144 + aux + offset + length].hex())
    check(len(entries) == len(RESOURCES), 'database resource count')
    for name, spec in RESOURCES.items():
        check(entries[name] == (spec['address'], spec['aux_hex']), name)
        check(r.resource(name, 'present') == 1, name + ' not bound')
    r.command(f'writel {r.args.cmd_db_base + 4:#x} 0')
    check(int(r.command(f'readl {r.args.cmd_db_base + 4:#x}')[0], 0) == 0x0c0330db,
          'guest modified command-db')


def timer_not_early_and_mask_pending_w1c(r):
    r.program('xo.lvl', 3)
    r.trigger()
    check(r.status() == ISSUED, 'issue indication')
    r.step(999)
    check(r.status() == ISSUED and r.resource('xo.lvl') == 0, 'early completion')
    r.step(1)
    # QOM read before any MMIO proves the actual virtual timer dispatched.
    r.expect_hz('bi_tcxo', 19_200_000)
    check(r.status() == ISSUED | COMPLETE, 'completion status')
    check(r.dread(TCS_BASE + 4) == 1, 'pending bit')
    r.irq_level(2, False)
    r.dwrite(TCS_BASE, 1)
    r.irq_level(2, True)
    r.dwrite(TCS_BASE + 8, 2)
    r.irq_level(2, True)
    r.dwrite(TCS_BASE + 8, 1)
    r.irq_level(2, False)


def sparse_multicommand_ordered_vrm_state(r):
    r.program('ldoa12', 1800)
    r.program('ldoa12', 7, field=2, slot=3)
    r.program('ldoa12', 1, field=1, slot=15)
    r.trigger(mask=(1 << 0) | (1 << 3) | (1 << 15))
    r.step(1000)
    check(r.resource('ldoa12') == 1800, 'voltage request did not apply')
    check(r.resource('ldoa12', 'mode') == 0, 'mode applied too early')
    check(r.resource('ldoa12', 'enabled') == 0, 'enabled before ordered command')
    r.step(1000)
    check(r.resource('ldoa12', 'mode') == 7, 'mode request')
    check(r.status(slot=15) == ISSUED, 'sparse next command')
    check(r.dread(TCS_BASE + 4) == 0, 'TCS completed before last command')
    r.step(1000)
    check(r.resource('ldoa12', 'enabled') == 1, 'enable request')
    check(r.status(slot=15) == ISSUED | COMPLETE, 'last command completion')
    check(r.status(slot=1) == 0, 'disabled command slot executed')


def independent_drv_vote_aggregation_and_release(r):
    r.run_one('xo.lvl', 1, drv=0)
    r.run_one('xo.lvl', 3, drv=2)
    check(r.resource('xo.lvl') == 3, 'maximum DRV vote')
    r.run_one('xo.lvl', 0, drv=2)
    check(r.resource('xo.lvl') == 1, 'other DRV vote lost')
    r.expect_hz('bi_tcxo', 19_200_000)
    r.run_one('xo.lvl', 0, drv=0)
    r.expect_hz('bi_tcxo', 0)


def vrm_clock_enable_offset_and_input_divider(r):
    r.run_one('lnbclka1', 1, field=1)
    r.expect_hz('ln_bb_clk1', 19_200_000)
    r.run_one('rfclka3', 1, field=1)
    r.expect_hz('rf_clk3', 38_400_000)
    r.run_one('lnbclka1', 0, field=1)
    r.expect_hz('ln_bb_clk1', 0)


def bcm_staging_commit_and_release(r):
    r.run_one('IP0', 0x20000000 | 123)
    r.expect_hz('ipa', 0)
    r.run_one('CE0', 0x60000000 | (10 << 14) | 5)
    r.expect_hz('ipa', 123_000_000)
    check(r.resource('CE0', 'vote-x') == 10 and r.resource('CE0', 'vote-y') == 5,
          'BCM committed X/Y payload')
    r.run_one('IP0', 0x60000000 | 200, drv=0)
    r.expect_hz('ipa', 200_000_000)
    r.run_one('IP0', 0x40000000, drv=0)
    r.expect_hz('ipa', 123_000_000)
    r.run_one('IP0', 0x40000000, drv=2)
    r.expect_hz('ipa', 0)


def explicit_sleep_wake_tcs_do_not_run_on_programming(r):
    r.run_one('xo.lvl', 3)
    r.clear()
    r.program('xo.lvl', 0, tcs=2)
    r.dwrite(r.tcs(2, 0x1c), 1)
    r.program('xo.lvl', 3, tcs=5)
    r.dwrite(r.tcs(5, 0x1c), 1)
    r.step(10000)
    r.expect_hz('bi_tcxo', 19_200_000)
    r.gpio('sleep', 2, True)
    r.step(1000)
    r.expect_hz('bi_tcxo', 0)
    r.gpio('sleep', 2, False)
    r.step(1000)
    r.expect_hz('bi_tcxo', 19_200_000)


def cancellation_retains_only_completed_commands(r):
    r.program('ldoa12', 1800)
    r.program('ldoa12', 1, field=1, slot=1)
    r.trigger(mask=3)
    r.step(1000)
    r.dwrite(r.tcs(0, 0x14), 0)
    r.step(10000)
    check(r.resource('ldoa12') == 1800, 'cancel rolled back completed command')
    check(r.resource('ldoa12', 'enabled') == 0, 'cancelled command executed')
    check(r.dread(TCS_BASE + 4) == 0, 'cancel produced completion IRQ')
    r.run_one('ldoa12', 1, field=1)
    check(r.resource('ldoa12', 'enabled') == 1, 'TCS not reusable after cancel')


def busy_programming_is_rejected(r):
    r.program('xo.lvl', 3)
    r.trigger()
    r.dwrite(r.tcs(0, 0x38), 0)
    check(r.dread(r.tcs(0, 0x38)) == 3, 'busy command data was overwritten')
    r.step(1000)
    check(r.resource('xo.lvl') == 3, 'busy mutation changed issued command')


def unknown_resource_has_no_false_ack(r):
    r.dwrite(TCS_BASE, 1)
    r.run_one('xo.lvl', 3, address=0x3dead)
    r.step(100000)
    r.expect_stall(0, 'xo.lvl')
    check('no completion' in r.log_path.read_text(), 'missing unsupported diagnostic')
    r.run_one('xo.lvl', 3)
    check(r.resource('xo.lvl') == 3, 'cancel did not recover from stalled request')


def unsupported_read_opcode_has_no_ack(r):
    r.run_one('xo.lvl', 3, msg=0x108)
    r.expect_stall(0, 'xo.lvl')


def malformed_message_length_has_no_ack(r):
    r.run_one('xo.lvl', 3, msg=0x10104)
    r.expect_stall(0, 'xo.lvl')


def arc_level_outside_aux_mapping_has_no_ack(r):
    check(r.resource('cx.lvl', 'level-count') == 12, 'reference ARC map length')
    r.run_one('cx.lvl', 12)
    r.expect_stall(0, 'cx.lvl')


def invalid_vrm_field_value_and_mode_have_no_ack(r):
    for field, value in ((3, 1), (1, 2), (2, 6), (0, 65000)):
        r.run_one('ldoa12', value, field=field)
        r.expect_stall(0, 'ldoa12')
    r.run_one('lnbclka1', 0, field=0)
    r.expect_stall(0, 'lnbclka1')


def reserved_bcm_bits_have_no_ack(r):
    r.run_one('IP0', 0x80000001)
    r.expect_stall(0, 'IP0')
    r.expect_hz('ipa', 0)


def no_enabled_commands_and_unknown_mmio_are_diagnosed(r):
    r.dwrite(r.tcs(0, 0x14), 0x1010000)
    r.step(10000)
    check(r.dread(TCS_BASE + 4) == 0, 'empty TCS claimed completion')
    r.dwrite(TCS_BASE, 0x100)
    check(r.dread(TCS_BASE) == 0, 'reserved IRQ enable bit retained')
    r.dwrite(0x200, 0x1234)
    r.dread(0x200)
    r.dread(r.tcs(0, 0x18))
    r.dwrite(0x38, 0x80000000)
    check(r.dread(0x38) == 0, 'unsupported PDC VALID silently accepted')
    check('unsupported' in r.log_path.read_text(), 'missing MMIO diagnostic')


def response_not_requested_completes_tcs_after_issue(r):
    r.run_one('xo.lvl', 3, msg=0x10008)
    check(r.status() == ISSUED, 'non-response command fabricated response flag')
    check(r.dread(TCS_BASE + 4) == 1, 'asynchronous accepted write not completed')
    check(r.resource('xo.lvl') == 3, 'async write had no effect')


def guest_reset_clears_votes_pending_irq_and_execution(r):
    r.run_one('ldoa12', 1800)
    r.run_one('ldoa12', 1, field=1)
    r.dwrite(TCS_BASE, 1)
    r.irq_level(2, True)
    r.program('xo.lvl', 3, tcs=1)
    r.trigger(tcs=1)
    r.monitor('system_reset')
    r.wait_for(lambda: r.dread(TCS_BASE) == 0 and r.status(tcs=1) == 0,
               'RPMh reset')
    r.step(10000)
    check(r.dread(TCS_BASE + 4) == 0, 'pre-reset timer completed after reset')
    check(r.resource('ldoa12') == 0 and r.resource('ldoa12', 'enabled') == 0,
          'reset retained regulator votes')
    check(r.resource('xo.lvl') == (3 if r.boot_configured else 0),
          'reset did not restore configured firmware handoff')
    r.irq_level(2, False)



def database_image(rows, split_aux=False):
    blob = bytearray(0x20000)
    struct.pack_into('<II', blob, 0, 1, 0x0c0330db)
    cursor = 144
    groups = [(3, [r for r in rows if r['slave'] == 3 and
                    (not split_aux or r['address'])])]
    if split_aux:
        groups.append((3, [r for r in rows if r['slave'] == 3 and not r['address']]))
    groups.extend((slave, [r for r in rows if r['slave'] == slave]) for slave in (4, 5))
    for index, (slave, entries) in enumerate(groups):
        headers = cursor
        cursor += 24 * len(entries)
        aux = cursor
        for i, row in enumerate(entries):
            data = bytes.fromhex(row['aux_hex'])
            priority = row.get('priority', [0, 0])
            struct.pack_into('<8sIIIHH', blob, headers + i * 24,
                             row['name'].encode(), *priority, row['address'], len(data), cursor - aux)
            blob[cursor:cursor + len(data)] = data
            cursor += len(data)
        struct.pack_into('<8H', blob, 8 + index * 16, slave, headers - 144,
                         aux - 144, len(entries), 0x100, 0, 0, 0)
    return blob


def file_arguments(r, filename):
    args = copy.copy(r.args)
    check('rpmh-profile=reference' in args.machine, 'file tests require explicit reference base')
    args.machine = args.machine.replace('rpmh-profile=reference', 'rpmh-profile=file')
    args.machine += ',rpmh-cmd-db=' + str(filename)
    return args


def external_file_profile_uses_loaded_resource_address(r):
    rows = copy.deepcopy(PROFILE['resources'])
    next(x for x in rows if x['name'] == 'lnbclka1')['address'] = 0x41000
    filename = r.directory / 'relocated-synthetic-db.bin'
    filename.write_bytes(database_image(rows))
    other = None
    try:
        other = RpmhRig(file_arguments(r, filename), r.directory / 'file-mode')
        check(other.resource('lnbclka1', 'address') == 0x41000, 'file address ignored')
        other.run_one('lnbclka1', 1, field=1)
        other.expect_stall(0, 'lnbclka1')
        other.run_one('lnbclka1', 1, address=0x41004)
        check(other.status() == ISSUED | COMPLETE, 'external file address not serviced')
        other.expect_hz('ln_bb_clk1', 19_200_000)
    finally:
        if other:
            other.close()


def external_aux_only_alias_and_zero_width_bcm_are_valid(r):
    rows = copy.deepcopy(PROFILE['resources'])
    rows.append(dict(name='cx.mol', slave=3, address=0, aux_hex='4000'))
    rows.append(dict(name='vrm.xob', slave=4,
                     address=RESOURCES['lnbclka1']['address'], aux_hex=''))
    next(x for x in rows if x['name'] == 'ACV')['aux_hex'] = struct.pack('<IHBB', 1, 0, 0, 0).hex()
    # The original AOP insertion routine emits parent records again during
    # ordinary registration. Distinct auxiliary offsets are semantically equal.
    clock = next(x for x in rows if x['name'] == 'lnbclka1')
    clock['aux_hex'] = '01000000'
    clock['priority'] = [1, 2]
    for name in ('lnbclka1', 'vrm.xob', 'cx.lvl', 'ACV'):
        rows.append(copy.deepcopy(next(x for x in rows if x['name'] == name)))
    filename = r.directory / 'abi-variants-synthetic-db.bin'
    filename.write_bytes(database_image(rows, split_aux=True))
    other = None
    try:
        other = RpmhRig(file_arguments(r, filename), r.directory / 'file-mode')
        check(other.resource('lnbclka1', 'present') == 1, 'valid alias prevented binding')
        other.run_one('lnbclka1', 1, field=1)
        other.expect_hz('ln_bb_clk1', 19_200_000)
        other.run_one('ACV', 0x60000001)
        check(other.resource('ACV', 'enabled') == 1, 'zero-width enable-mask BCM failed')
        other.run_one('xo.lvl', 1, address=0)
        other.expect_stall(0, 'xo.lvl')
    finally:
        if other:
            other.close()


def malformed_external_databases_are_rejected(r):
    original = database_image(PROFILE['resources'])
    arc_head = 144 + struct.unpack_from('<H', original, 10)[0]
    vrm_head = 144 + struct.unpack_from('<H', original, 26)[0]
    cases = []
    data = bytearray(original)
    struct.pack_into('<I', data, 4, 0)
    cases.append(('bad-magic', data))
    data = bytearray(original)
    struct.pack_into('<H', data, 14, 65535)
    cases.append(('entry-bounds', data))
    data = bytearray(original)
    struct.pack_into('<H', data, arc_head + 24 + 20, 3)
    cases.append(('odd-arc-aux', data))
    data = bytearray(original)
    struct.pack_into('<I', data, arc_head + 24 + 16, 0)
    cases.append(('addressable-resource-zero-address', data))
    data = bytearray(original)
    data[vrm_head:vrm_head + 8] = b'xo.lvl' + bytes(2)
    cases.append(('duplicate-name', data))
    data = bytearray(original)
    struct.pack_into('<I', data, vrm_head + 24 + 16, RESOURCES['lnbclka1']['address'])
    cases.append(('two-modeled-resources-one-address', data))
    for label, fields in (
            ('duplicate-address-conflict', {'address': 0x41000}),
            ('duplicate-priority-conflict', {'priority': [0, 1]}),
            ('duplicate-aux-length-conflict', {'aux_hex': '01'})):
        rows = copy.deepcopy(PROFILE['resources'])
        duplicate = copy.deepcopy(next(x for x in rows if x['name'] == 'lnbclka1'))
        duplicate.update(fields)
        rows.append(duplicate)
        cases.append((label, database_image(rows)))
    rows = copy.deepcopy(PROFILE['resources'])
    clock = next(x for x in rows if x['name'] == 'lnbclka1')
    clock['aux_hex'] = '01000000'
    duplicate = copy.deepcopy(clock)
    duplicate['aux_hex'] = '02000000'
    rows.append(duplicate)
    cases.append(('duplicate-aux-content-conflict', database_image(rows)))
    rows = copy.deepcopy(PROFILE['resources'])
    rows.append(dict(name='cx.mol', slave=3, address=0, aux_hex='4000'))
    data = database_image(rows, split_aux=True)
    # The fifth header repeats the auxiliary-only ARC group but conflicts in
    # its resource schema version. Equal bytes alone do not make this safe.
    data[72:88] = data[24:40]
    struct.pack_into('<H', data, 80, 0x101)
    cases.append(('duplicate-version-conflict', data))
    cases.append(('over-size', original + bytes(1)))
    for label, data in cases:
        filename = r.directory / (label + '.bin')
        filename.write_bytes(data)
        args = file_arguments(r, filename)
        command = [args.qemu, '-M', args.machine, '-accel', 'qtest', '-m', '128M',
                   '-display', 'none', '-monitor', 'none', '-serial', 'null', '-S']
        logfile = r.directory / (label + '.stderr')
        process = None
        with logfile.open('wb') as log:
            try:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.DEVNULL, stderr=log)
                code = process.wait(timeout=8)
            finally:
                if process is not None and process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        check(code > 0, (label, 'malformed database accepted or process crashed', code))
        diagnostic = logfile.read_text()
        check('RPMh' in diagnostic, (label, 'no parser diagnostic'))
        if label.startswith('duplicate-'):
            check('conflicting duplicate' in diagnostic,
                  (label, 'duplicate conflict was not diagnosed', diagnostic))


FILE_TESTS = [external_file_profile_uses_loaded_resource_address,
              external_aux_only_alias_and_zero_width_bcm_are_valid,
              malformed_external_databases_are_rejected]

TESTS = [identity_geometry_and_explicit_handoff,
         exact_reference_command_db_and_read_only_mapping,
         timer_not_early_and_mask_pending_w1c,
         sparse_multicommand_ordered_vrm_state,
         independent_drv_vote_aggregation_and_release,
         vrm_clock_enable_offset_and_input_divider,
         bcm_staging_commit_and_release,
         explicit_sleep_wake_tcs_do_not_run_on_programming,
         cancellation_retains_only_completed_commands,
         busy_programming_is_rejected,
         unknown_resource_has_no_false_ack,
         unsupported_read_opcode_has_no_ack,
         malformed_message_length_has_no_ack,
         arc_level_outside_aux_mapping_has_no_ack,
         invalid_vrm_field_value_and_mode_have_no_ack,
         reserved_bcm_bits_have_no_ack,
         no_enabled_commands_and_unknown_mmio_are_diagnosed,
         response_not_requested_completes_tcs_after_issue,
         guest_reset_clears_votes_pending_irq_and_execution] + FILE_TESTS


def migrate(args, directory):
    checks = []
    source = target = None
    try:
        source = RpmhRig(args, directory / 'source')
        source.run_one('xo.lvl', 3)
        source.dwrite(TCS_BASE, 1)
        source.run_one('IP0', 0x20000000 | 77, tcs=1)
        source.program('ldoa12', 1800, tcs=2)
        source.program('ldoa12', 1, field=1, tcs=2, slot=1)
        source.trigger(mask=3, tcs=2)
        source.step(1000)
        source.program('xo.lvl', 1, tcs=3, address=0x3dead)
        source.trigger(tcs=3)
        source.step(1000)
        # A fresh in-flight command remains pending during the transfer.
        source.clear(tcs=2)
        source.program('ldoa12', 0, field=1, tcs=2)
        source.trigger(tcs=2)
        now = source.last_clock
        source.monitor('stop')
        target = RpmhRig(args, directory / 'target', incoming=True)
        target.step(now)
        uri = 'unix:' + str(directory / 'migration.sock')
        target.monitor('migrate-incoming', {'uri': uri})
        source.monitor('migrate', {'uri': uri})
        source.wait_for(lambda: source.monitor('query-migrate').get('status')
                        in ('completed', 'failed'), 'RPMh migration', timeout=30)
        state = source.monitor('query-migrate')
        check(state['status'] == 'completed', state)
        target.wait_for(lambda: target.monitor('query-status')['status'] != 'inmigrate',
                        'destination loading state', timeout=30)
        check(not target.monitor('query-status')['running'], 'destination resumed unexpectedly')
        checks.append({'check': 'actual_process_to_process_transfer', 'status': 'pass'})
        check(target.resource('xo.lvl') == 3 and target.resource('ldoa12') == 1800,
              'committed resources lost')
        check(target.resource('ldoa12', 'enabled') == 1, 'in-flight disable applied during load')
        check(target.dread(TCS_BASE) == source.dread(TCS_BASE) and
              target.dread(TCS_BASE + 4) == source.dread(TCS_BASE + 4), 'IRQ state lost')
        target.irq_level(2, True)
        checks.append({'check': 'votes_pending_irq_and_mask_preserved', 'status': 'pass'})
        target.expect_hz('ipa', 0)
        check(target.status(tcs=3) == ISSUED, 'stalled unsupported request changed')
        checks.append({'check': 'staged_bcm_and_stalled_request_not_falsely_completed', 'status': 'pass'})
        target.monitor('cont')
        target.step(999)
        check(target.resource('ldoa12', 'enabled') == 1, 'in-flight deadline fired early')
        target.step(1)
        check(target.resource('ldoa12', 'enabled') == 0, 'in-flight deadline not resumed')
        checks.append({'check': 'remaining_command_resumes_at_controlled_deadline', 'status': 'pass'})
        target.run_one('CE0', 0x60000001)
        target.expect_hz('ipa', 77_000_000)
        target.run_one('xo.lvl', 0, tcs=3)
        target.expect_hz('bi_tcxo', 0)
        checks.append({'check': 'staged_vote_commit_and_stall_recovery_continue', 'status': 'pass'})
        return dict(success=True, expected_checks=5, checks=checks, migration=state,
                    qtest_virtual_ns_at_transfer=now)
    except Exception:
        return dict(success=False, expected_checks=5, checks=checks,
                    error=traceback.format_exc())
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--machine', default='tb371fc,rpmh-profile=reference,rpmh-boot-configured=off,ufs-enabled=off')
    parser.add_argument('--base', type=lambda v: int(v, 0), default=0x18200000)
    parser.add_argument('--cmd-db-base', type=lambda v: int(v, 0), default=0x80860000)
    parser.add_argument('--qom-path', default='/machine/rpmh')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--migration-only', action='store_true')
    parser.add_argument('--file-tests-only', action='store_true')
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    directory = args.work_dir or Path(tempfile.mkdtemp(prefix='tb371fc-rpmh-'))
    directory.mkdir(parents=True, exist_ok=True)
    with open(args.qemu, 'rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    checks = []
    report = dict(schema_version=1, suite='SM8250 RPMh actual QTest',
                  qemu=args.qemu, qemu_sha256=digest, machine=args.machine,
                  work_dir=str(directory), native_command_db=False,
                  reference_command_db_sha256=PROFILE['command_db_sha256'],
                  scope='Typed digital TCS/ARC/VRM/BCM behavior; synthetic addresses and aux; no AOP firmware, analog PMIC or full-board equivalence',
                  checks=checks)
    if args.migration_only:
        report.update(migrate(args, directory))
        report['suite'] = 'SM8250 RPMh actual component process migration'
    else:
        selected = FILE_TESTS if args.file_tests_only else TESTS
        # Keep nested Unix socket names below sun_path's 108-byte limit.
        # Full test names remain in the report, which maps each numeric fixture.
        for index, test in enumerate(selected):
            fixture = directory / f'{index:02d}'
            started = time.monotonic()
            rig = None
            try:
                rig = RpmhRig(args, fixture)
                test(rig)
                checks.append(dict(test=test.__name__, status='pass',
                                   fixture=str(fixture), seconds=round(time.monotonic() - started, 4)))
            except Exception:
                checks.append(dict(test=test.__name__, status='fail', fixture=str(fixture),
                                   error=traceback.format_exc()))
                break
            finally:
                if rig:
                    rig.close()
        report.update(expected_checks=len(selected),
                      success=len(checks) == len(selected) and all(c['status'] == 'pass' for c in checks))
    print(json.dumps(report, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
