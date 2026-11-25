#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled QEMU acceptance for the explicitly limited QCE/BAM cold model.

No QEMU implementation is imported, parsed or used to choose expected values.
The safety mode exercises default/explicit off and profile admission. Reference
mode requires a separately reviewed bound policy; UNBOUND is a hard failure,
never a skip or synthetic successful enabled test. No test issues QMP cont.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import traceback

from qce_bam_test_contract import (
    BASE, SIZE, CRYPTO, TYPE, PATH, REG, PIPE_REG, PIPE_FIELDS, IDENTITIES,
    OBSERVATION_FIELDS, READONLY_FIELDS, BOOL_FIELDS, STRING_FIELDS,
    GUARD_FIELDS, MUTABLE_FIELDS, FIELD_MUTATIONS, PAYLOAD_BYTES, GUARD_BYTES,
    cold_expected, pipe_reg, read_profile, snapshot, encode_snapshot, locate_section)
from qce_bam_test_transport import Rig, file_channels, put_json, sha, verify_cleanup

DEFAULT_MACHINE = 'tb371fc,ufs-enabled=off,gcc-boot-configured=off'
DEFAULT_PROFILE = Path(__file__).resolve().parents[1] / 'docs/qce-bam-sm8250-reference-profile.json'
GUARD_ERROR = TYPE + ': migrated identity, profile or geometry mismatch'
MUTABLE_ERROR = TYPE + ': invalid migrated control state'
# With no -kernel, the ARM boot loader restores the board DTB at RAM base
# on every system reset.  Keep artificial rings/output witnesses away from
# that ROM-backed range; their complete bytes must remain unchanged.
RAM = 0x81010000


def pure_twice(r, expected, label):
    before = dict(r.count)
    first = snapshot(r)
    middle = dict(r.count)
    repeated = snapshot(r)
    after = dict(r.count)
    assert first == repeated == expected, (label, first, expected)
    assert middle['qom_get'] - before['qom_get'] == 70
    assert after['qom_get'] - middle['qom_get'] == 70
    for key in before:
        if key != 'qom_get':
            assert before[key] == middle[key] == after[key], (key, before, middle, after)
    assert before['memory'] == before['device_mmio'] == before['cont'] == 0
    result = {'first': first, 'repeated': repeated, 'counters_before': before,
              'counters_after': after, 'first_getters': 70, 'repeated_getters': 70,
              'before_first_device_or_ram_access': True, 'before_any_cont': True}
    put_json(r.directory / (label + '.json'), result)
    return result


def mappings(r, enabled):
    tree = r.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    (r.directory / 'active-memory-map.txt').write_text(tree)
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(
        r'([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (' + re.escape(TYPE) + r'[^\s]*)(?:\s|$)', tree)}
    expected = {(BASE, BASE + SIZE - 1, TYPE + '.union')} if enabled else set()
    assert found == expected, (found, expected)
    return {'active_leaves': sorted(found), 'active_mapped_bytes': SIZE if enabled else 0}


def device_diagnostics(text):
    return [line for line in text.splitlines() if line.startswith(TYPE + ': rejected ')]


def core_diagnostics(text):
    return [(op, int(address, 16), int(size), region, reason)
            for op, address, size, region, reason in re.findall(
                r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)", text)]


def witnesses(r, large=False):
    # These bytes are artificial test inputs. They are not keys, physical
    # descriptor captures, firmware or crypto results. Every allocated byte is
    # compared after unsupported operations, with no automatic repair.
    ring_size = 0x9000 if large else 16
    result = {}
    for pipe in range(4, 8):
        address = RAM + (pipe - 4) * 0x10000
        result[address] = bytes(((n * 29 + pipe * 17) & 255) for n in range(ring_size))
    result[RAM + 0x50000] = bytes(((n * 31 + 7) & 255) for n in range(256))
    for address, raw in result.items():
        r.write_ram(address, raw)
        assert r.read_ram(address, len(raw)) == raw
    put_json(r.directory / 'ram-witnesses.json', [
        {'address': address, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
        for address, raw in result.items()])
    return result


def verify_witnesses(r, expected):
    for address, raw in expected.items():
        assert r.read_ram(address, len(raw)) == raw, hex(address)


def rejection(r, offset, *, write=False, value=0, width='l', reason=None, ram=None):
    before = snapshot(r)
    oldlog = r.stderr()
    old_irq = list(r.irq_events)
    size = {'b': 1, 'w': 2, 'l': 4, 'q': 8}[width]
    operation = 'write' if write else 'read'
    if reason is None:
        reason = ('disabled-width-or-alignment' if size != 4 or offset & 3 else
                  'unsupported-or-invalid-pipe-state' if write and
                  0x17000 <= offset < 0x1b000 else
                  'read-only-or-unsupported-register' if write else
                  'unsupported-or-write-only-register')
    command = f'{operation}{width} {BASE + offset:#x}' + (f' {value:#x}' if write else '')
    reply = r.qt(command)
    # QTest's textual OK acknowledges protocol processing. It does not expose
    # MemTxResult. Rejected read bytes therefore are never a register oracle.
    r.irq_fence()
    after = snapshot(r)
    newlog = r.stderr()
    added = device_diagnostics(newlog)[len(device_diagnostics(oldlog)):]
    expected = f'{TYPE}: rejected {operation} offset={offset:#x} size {size} ({reason})'
    assert added == [expected], (command, added, expected)
    assert core_diagnostics(newlog)[len(core_diagnostics(oldlog)):] == []
    assert before == after, (command, before, after)
    assert r.irq_events == old_irq, (command, old_irq, r.irq_events)
    if ram:
        verify_witnesses(r, ram)
    detail = {'command': command, 'protocol_reply': reply, 'diagnostic': added,
              'complete_state_unchanged': True, 'all_ram_witnesses_unchanged': bool(ram),
              'irq_events_unchanged': True, 'qtest_ok_is_memtx_result': False,
              'read_error_bytes_used_as_identity': False}
    with (r.directory / 'rejections.jsonl').open('a') as f:
        f.write(json.dumps(detail, sort_keys=True) + '\n')
    return detail


def unmapped(r, offset):
    before, old = snapshot(r), r.stderr()
    for write in (False, True):
        command = f"{'writel' if write else 'readl'} {BASE + offset:#x}" + (' 0' if write else '')
        previous = r.stderr()
        r.qt(command)
        added = core_diagnostics(r.stderr())[len(core_diagnostics(previous)):]
        assert added == [('write' if write else 'read', BASE + offset, 4, '(null)', 'rejected')], added
    assert snapshot(r) == before
    assert device_diagnostics(r.stderr())[len(device_diagnostics(old)):] == []


def safety_cases(args, profile, out, report):
    for name, reference, accel in (('default-off', None, 'qtest'),
                                    ('explicit-off', False, 'qtest'),
                                    ('paused-tcg-pure-observation', False, 'tcg')):
        r = Rig(args, out / name, reference=reference, accel=accel)
        try:
            pure_twice(r, cold_expected(profile, False), 'before-any-memory')
            status = r.monitor('query-status')
            assert status['running'] is False
            mapping = mappings(r, False)
            if accel == 'qtest':
                for offset in (REG['qce-version'], REG['bam-config'], pipe_reg(4, 'control'),
                               0x1c000, SIZE - 4):
                    unmapped(r, offset)
            report['checks'].append({'name': name, 'passed': True, **mapping})
        finally:
            r.close()
    name = 'bound-profile-admission' if profile['bound'] else 'unbound-enable-fails-closed'
    r = Rig(args, out / name, reference=True, expect_realize_failure=not profile['bound'])
    try:
        if profile['bound']:
            pure_twice(r, cold_expected(profile, True), 'before-any-memory')
            mappings(r, True)
        else:
            assert r.process.returncode == 1, r.stderr()
            assert TYPE + ': reference profile is UNBOUND or invalid' in r.stderr()
            assert r.count['memory'] == r.count['cont'] == r.count['qom_get'] == 0
        report['checks'].append({'name': name, 'passed': True,
                                  'profile_bound': profile['bound']})
    finally:
        r.close()


class ColdSequence:
    """Exact source order, including every RMW read before its write."""
    def __init__(self, r, profile):
        self.r, self.profile = r, profile
        self.state = cold_expected(profile, True)
        self.trace = []
        self.shared_bam_enabled = False

    def read(self, name, pipe=None):
        offset = REG[name] if pipe is None else pipe_reg(pipe, name)
        key = name if pipe is None else f'pipe-{pipe}-{name}'
        expected = 0 if name in ('irq-sources', 'irq-unmasked', 'software-offset') else self.state[key]
        actual = self.r.read(offset)
        assert actual == expected, (name, pipe, actual, expected)
        self.trace.append({'op': 'readl', 'offset': offset, 'value': actual})
        return actual

    def write(self, name, value, pipe=None):
        offset = REG[name] if pipe is None else pipe_reg(pipe, name)
        self.r.write(offset, value)
        key = name if pipe is None else f'pipe-{pipe}-{name}'
        if pipe is not None and name == 'reset' and value == 1:
            for field in PIPE_FIELDS:
                self.state[f'pipe-{pipe}-{field}'] = int(field == 'reset')
        else:
            self.state[key] = value
        self.trace.append({'op': 'writel', 'offset': offset, 'value': value})
        assert snapshot(self.r) == self.state

    def rmw(self, name, mask, value, pipe=None):
        previous = self.read(name, pipe)
        assert value & ~mask == 0
        self.write(name, (previous & ~mask) | value, pipe)

    def connect(self, pipe, descriptor):
        producer = pipe in (5, 7)
        group = 2 if pipe in (4, 5) else 3
        # sps_bam_pipe_connect calls sps_bam_enable only before the shared BAM
        # has BAM_STATE_ENABLED. It is then cached across the other pipes and
        # the second QCE client. qce_sps_get_bam does not ENABLE_AT_BOOT.
        if not self.shared_bam_enabled:
            self.read('bam-ee-attr')
            self.read('bam-revision')
            self.read('bam-num-pipes')
            self.shared_bam_enabled = True
        assert self.read('control', pipe) & 2 == 0
        self.write('reset', 1, pipe)
        self.write('reset', 0, pipe)
        self.rmw('bam-ee-irq-mask', 1 << pipe, 1 << pipe)
        self.write('irq-enable', 0, pipe)
        self.rmw('control', 8, 8 if producer else 0, pipe)
        self.rmw('control', 0x20, 0x20, pipe)
        self.write('threshold', 0x10, pipe)
        self.write('descriptor-lo', descriptor & 0xffffffff, pipe)
        self.rmw('fifo-size', 0xffff, 0x9000, pipe)
        self.rmw('control', 0x10, 0, pipe)
        if descriptor >> 32:
            self.write('descriptor-hi', descriptor >> 32, pipe)
        self.rmw('control', 0x1f0000, group << 16, pipe)
        self.rmw('control', 2, 2, pipe)
        self.write('irq-enable', 1 if producer else 0, pipe)
        self.rmw('bam-ee-irq-mask', 1 << pipe, (1 << pipe) if producer else 0)
        assert self.read('control', pipe) == (group << 16) | 0x22 | (8 if producer else 0)


def cold_order(r, profile, client_order):
    seq = ColdSequence(r, profile)
    for index, client in enumerate(client_order):
        seq.read('qce-version')
        seq.read('qce-engines')
        if index == 0:
            seq.read('bam-config')
        pair = (7, 6) if client == 'qcedev' else (5, 4)
        for pipe in pair:
            # Both low-only and source-conditional nonzero upper36-bit paths.
            address = RAM + (pipe - 4) * 0x10000
            if pipe in (5, 7):
                address += (pipe - 3) << 32
            seq.connect(pipe, address)
    expected_mask = (profile['identity']['bam-ee-initial-irq-mask'] & ~0xf0) | 0xa0
    assert seq.state['bam-ee-irq-mask'] == expected_mask
    # These are extra test observations, excluded from the source-order trace.
    observed_sources = r.read(REG['irq-sources'])
    observed_unmasked = r.read(REG['irq-unmasked'])
    assert observed_sources == observed_unmasked == 0
    r.irq_fence()
    assert not device_diagnostics(r.stderr())
    result = {'client_order': client_order, 'trace': seq.trace,
              'final': seq.state, 'final_ee_mask': expected_mask,
              'initial_ee_mask': profile['identity']['bam-ee-initial-irq-mask'],
              'outside_owned_mask_preserved': True,
              'ring_configuration_is_dma_submission': False,
              'shared_bam_check_count': 1,
              'client_order_and_descriptor_addresses_are_test_inputs': True,
              'observed_original_kernel_trace_claimed': False,
              'extra_observations_outside_source_trace': {
                  'irq-sources': observed_sources, 'irq-unmasked': observed_unmasked}}
    put_json(r.directory / 'exact-cold-sequence.json', result)
    return result


def seed_pipes(r, profile, *, mixed=False):
    expected = cold_expected(profile, True)
    for pipe in range(4, 8):
        values = {'descriptor-lo': RAM + (pipe - 4) * 0x10000,
                  'descriptor-hi': 0, 'fifo-size': 16,
                  'threshold': pipe * 17, 'irq-enable': pipe + 1,
                  'control': ((pipe - 2) << 16) | 0x22 | (8 if pipe & 1 else 0)}
        if mixed and pipe == 4:
            values.update({'descriptor-lo': 0xfffffff8, 'descriptor-hi': 15,
                           'fifo-size': 0xfff8, 'threshold': 0xffff,
                           'irq-enable': 0xff, 'control': 0x1f002a})
        if mixed and pipe == 6:
            values.update({'fifo-size': 0, 'descriptor-hi': 7, 'threshold': 0,
                           'control': 0x30028})
        if mixed and pipe == 7:
            r.write(pipe_reg(pipe, 'reset'), 1)
            expected[f'pipe-{pipe}-reset'] = 1
            continue
        for field in ('descriptor-lo', 'descriptor-hi', 'fifo-size', 'threshold',
                      'irq-enable', 'control'):
            r.write(pipe_reg(pipe, field), values[field])
            expected[f'pipe-{pipe}-{field}'] = values[field]
    mask = (expected['bam-ee-irq-mask'] & ~0xf0) | 0x50
    r.write(REG['bam-ee-irq-mask'], mask)
    expected['bam-ee-irq-mask'] = mask
    assert snapshot(r) == expected
    return expected


def strict_shapes(r, profile):
    seed_pipes(r, profile)
    ram = witnesses(r)
    cases = []
    for offset in (REG['qce-version'], REG['bam-ee-irq-mask'], pipe_reg(4, 'control'),
                   pipe_reg(7, 'descriptor-lo')):
        for width in ('b', 'w', 'q'):
            for write in (False, True):
                cases.append(rejection(r, offset, width=width, write=write,
                                       value=0xa5a5a5a5a5a5a5a5 & ((1 << ({'b':1,'w':2,'q':8}[width]*8))-1),
                                       ram=ram))
        for delta in (1, 2, 3):
            for write in (False, True):
                cases.append(rejection(r, offset + delta, write=write, value=0x12345678, ram=ram))
    return {'rejections': len(cases), 'exact_single_callback_required': True}


def readonly_unknown_and_crypto(r, profile):
    seed_pipes(r, profile)
    ram = witnesses(r)
    count = 0
    for name in ('qce-version', 'qce-engines', 'bam-config', 'bam-revision',
                 'bam-num-pipes', 'bam-ee-attr', 'irq-sources', 'irq-unmasked'):
        rejection(r, REG[name], write=True, value=0xffffffff, ram=ram)
        count += 1
    for pipe in range(4, 8):
        for name in ('irq-status', 'software-offset'):
            rejection(r, pipe_reg(pipe, name), write=True, value=1, ram=ram)
            count += 1
        for name in ('irq-clear', 'doorbell'):
            rejection(r, pipe_reg(pipe, name), ram=ram)
            count += 1
        for value in (0, 8, 0xffff, 0xffffffff):
            rejection(r, pipe_reg(pipe, 'doorbell'), write=True, value=value,
                      reason='unsupported-DMA-submit', ram=ram)
            count += 1
    # Unknown globals, extra EE/pipe, resource-overlap hole, and union tail.
    for offset in (0, 0x1004, 0x3010, 0x4004, 0x16000, 0x1b000, 0x1c000, 0x23ffc, SIZE - 4):
        for write in (False, True):
            rejection(r, offset, write=write, value=0, ram=ram)
            count += 1
    # qcryptohw_50.h: all three GO selectors; data in/out; status/config;
    # ordinary and XTS keys, pipe keys and auth/IV ranges. Synthetic zero and
    # public arithmetic patterns only; these are not real secrets.
    crypto = {0x1a120, 0x1b000, 0x1c000, 0x1a100, 0x1a104,
              0x1a10c, 0x1a110, 0x1a200, 0x1a204, 0x1a208}
    crypto.update(range(0x1a010, 0x1a030, 4))
    crypto.update(range(0x1d000, 0x1d040, 4))
    crypto.update(range(0x1e000, 0x1e080, 4))
    crypto.update(range(0x1d040, 0x1d080, 4))
    crypto.update(range(0x1a20c, 0x1a220, 4))
    crypto.update(range(0x1a310, 0x1a360, 4))
    for pipe in range(4):
        crypto.update(range(0x1e800 + pipe * 0x80, 0x1e840 + pipe * 0x80, 4))
    for offset in sorted(crypto):
        for write in (False, True):
            rejection(r, CRYPTO + offset, write=write, value=0, ram=ram)
            count += 1
    r.irq_fence()
    return {'rejections': count, 'zero_doorbell_rejected': True,
            'complete_descriptor_and_output_witness_bytes_verified': sum(map(len, ram.values())),
            'successful_crypto_or_dma_claimed': False}


def descriptor_size_threshold(r, profile):
    count = 0
    for pipe in range(4, 8):
        for low, high in ((0, 0), (0xfffffff8, 0xf), (RAM, 1)):
            r.write(pipe_reg(pipe, 'descriptor-lo'), low)
            r.write(pipe_reg(pipe, 'descriptor-hi'), high)
            assert r.read(pipe_reg(pipe, 'descriptor-lo')) == low
            assert r.read(pipe_reg(pipe, 'descriptor-hi')) == high
        for value in (1, 7, 0xffffffff):
            rejection(r, pipe_reg(pipe, 'descriptor-lo'), write=True, value=value)
            count += 1
        for value in (0x10, 0xffffffff):
            rejection(r, pipe_reg(pipe, 'descriptor-hi'), write=True, value=value)
            count += 1
        for value in (0, 8, 16, 0xfff8):
            r.write(pipe_reg(pipe, 'fifo-size'), value)
            assert r.read(pipe_reg(pipe, 'fifo-size')) == value
        for value in (1, 7, 15, 0xffff, 0x10000, 0xffff0000):
            rejection(r, pipe_reg(pipe, 'fifo-size'), write=True, value=value)
            count += 1
        for value in (0, 1, 0xffff):
            r.write(pipe_reg(pipe, 'threshold'), value)
            assert r.read(pipe_reg(pipe, 'threshold')) == value
        for value in (0x10000, 0xffffffff):
            rejection(r, pipe_reg(pipe, 'threshold'), write=True, value=value)
            count += 1
        r.write(pipe_reg(pipe, 'fifo-size'), 8)
        rejection(r, pipe_reg(pipe, 'control'), write=True, value=0x22)
        r.write(pipe_reg(pipe, 'fifo-size'), 16)
        for value in (2, 0x32, 0x23, 0x200022):
            rejection(r, pipe_reg(pipe, 'control'), write=True, value=value)
            count += 1
        r.write(pipe_reg(pipe, 'control'), 0x1f002a)
        for value in (0, 8):
            rejection(r, pipe_reg(pipe, 'fifo-size'), write=True, value=value)
            count += 1
    return {'rejections': count + 4, 'descriptor_address_bits': 36,
            'zero_descriptor_address_accepted_as_state': True,
            'enabled_fifo_min_bytes': 16, 'fifo_alignment': 8}


def reset_isolation_and_masks(r, profile):
    seed_pipes(r, profile)
    ram = witnesses(r)
    for pipe in range(4, 8):
        prior = snapshot(r)
        r.write(pipe_reg(pipe, 'reset'), 1)
        expected = copy.deepcopy(prior)
        for field in PIPE_FIELDS:
            expected[f'pipe-{pipe}-{field}'] = int(field == 'reset')
        assert snapshot(r) == expected
        for name in ('control', 'descriptor-lo', 'descriptor-hi', 'fifo-size',
                     'threshold', 'irq-enable', 'irq-clear'):
            rejection(r, pipe_reg(pipe, name), write=True, value=0, ram=ram)
        rejection(r, pipe_reg(pipe, 'reset'), write=True, value=2, ram=ram)
        r.write(pipe_reg(pipe, 'reset'), 0)
        expected[f'pipe-{pipe}-reset'] = 0
        assert snapshot(r) == expected
    initial = profile['identity']['bam-ee-initial-irq-mask']
    for bits in range(16):
        value = (initial & ~0xf0) | (bits << 4)
        r.write(REG['bam-ee-irq-mask'], value)
        assert r.read(REG['bam-ee-irq-mask']) == value
        assert r.read(REG['irq-sources']) == r.read(REG['irq-unmasked']) == 0
        for pipe in range(4, 8):
            r.write(pipe_reg(pipe, 'irq-enable'), 0xff)
            r.write(pipe_reg(pipe, 'irq-clear'), 0xff)
            assert r.read(pipe_reg(pipe, 'irq-status')) == 0
    for bit in (0, 3, 8, 31):
        rejection(r, REG['bam-ee-irq-mask'], write=True,
                  value=r.qom('bam-ee-irq-mask') ^ (1 << bit),
                  reason='unmodeled-EE-mask-bits', ram=ram)
    for pipe in range(4, 8):
        for name in ('irq-enable', 'irq-clear'):
            rejection(r, pipe_reg(pipe, name), write=True, value=0x100, ram=ram)
    r.monitor('system_reset')
    assert snapshot(r) == cold_expected(profile, True)
    verify_witnesses(r, ram)
    r.irq_fence()
    return {'pipes_reset_independently': 4, 'owned_ee_mask_combinations': 16,
            'initial_mask_restored': initial, 'outside_owned_mask_preserved': True,
            'w1c_did_not_invent_status': True}


def immutable_properties(r, profile):
    before = snapshot(r)
    for name in READONLY_FIELDS:
        old = before[name]
        value = (not old if type(old) is bool else old ^ 1 if type(old) is int else 'changed')
        response = r.raw_monitor('qom-set', {'path': PATH, 'property': name, 'value': value})
        assert 'error' in response, (name, response)
        assert snapshot(r) == before
    return {'readonly_properties': len(READONLY_FIELDS), 'all_mutation_attempts_rejected': True}


REFERENCE_CASES = (
    ('exact-cold-qcedev-first', lambda r, p: cold_order(r, p, ['qcedev', 'qcrypto'])),
    ('exact-cold-qcrypto-first', lambda r, p: cold_order(r, p, ['qcrypto', 'qcedev'])),
    ('strict-width-and-alignment', strict_shapes),
    ('readonly-unknown-doorbell-key-data', readonly_unknown_and_crypto),
    ('descriptor-36bit-size-threshold', descriptor_size_threshold),
    ('pipe-reset-ee-m0-and-idle-irq', reset_isolation_and_masks),
    ('immutable-qom-properties', immutable_properties))


def save_stream(r, path, expected):
    before = snapshot(r)
    assert before == expected
    r.monitor('migrate', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False and status['status'] == 'postmigrate', status
    assert snapshot(r) == before
    raw = path.read_bytes()
    section = locate_section(raw, before)
    Path(str(path) + '.payload.bin').write_bytes(encode_snapshot(before))
    result = {'file': path.name, 'sha256': sha(path), 'bytes': len(raw),
              'snapshot': before, 'section': section, 'query_migrate': migration,
              'query_status': status, 'cont_count': r.count['cont']}
    assert result['cont_count'] == 0
    put_json(Path(str(path) + '.section.json'), result)
    return result


def load_stream(r, path, expected, profile, enabled):
    pre = pure_twice(r, cold_expected(profile, enabled), 'preload-pure-observation')
    before = dict(r.count)
    r.monitor('migrate-incoming', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False, status
    post = pure_twice(r, expected, 'first-postload-pure-observation')
    assert r.count['memory'] == r.count['device_mmio'] == r.count['cont'] == 0
    result = {'preload': pre, 'first_postload': post, 'query_migrate': migration,
              'query_status': status, 'before_incoming_counters': before,
              'qce_device_mmio_before_first_observation': 0,
              'any_memory_commands_before_first_observation': 0,
              'cont_before_first_observation': 0,
              'whole_machine_ram_not_loaded_claimed': False}
    put_json(r.directory / 'load-observation.json', result)
    return result


def expect_load_failure(r, path, profile, enabled, diagnostic):
    pre = pure_twice(r, cold_expected(profile, enabled), 'preload-pure-observation')
    # Observation of the device's output only; qtest interception replaces its
    # GIC connection and is not evidence of guest interrupt handling.
    r.intercept()
    r.expected_exit = 1
    before = dict(r.count)
    command_error = None
    try:
        r.monitor('migrate-incoming', file_channels(path))
    except (AssertionError, OSError, EOFError, TimeoutError) as exc:
        command_error = f'{type(exc).__name__}: {exc}'
    exit_code = r.wait_exit(30)
    stderr = r.stderr()
    assert exit_code == 1, (exit_code, stderr)
    assert diagnostic in stderr, (diagnostic, stderr)
    assert 'load of migration failed' in stderr.lower(), stderr
    assert r.count['memory'] == r.count['device_mmio'] == r.count['cont'] == 0
    assert r.count == before, (r.count, before)
    assert not r.irq_events
    result = {'preload': pre, 'incoming_command_error': command_error,
              'exit_code': exit_code, 'specific_diagnostic': diagnostic,
              'specific_diagnostic_observed': True, 'counters_before_incoming': before,
              'counters_after_exit': dict(r.count), 'no_device_or_ram_commands': True,
              'no_cont': True, 'failed_process_postload_qom_claimed': False,
              'whole_machine_ram_not_loaded_claimed': False,
              'device_guard_precedes_own_mutable_fields_only': True,
              'stderr_sha256': sha(r.directory / 'qemu.stderr')}
    put_json(r.directory / 'expected-load-failure.json', result)
    return result


def changed_stream(source, target, offset, width, xor):
    old = source.read_bytes()
    new = bytearray(old)
    previous = int.from_bytes(new[offset:offset + width], 'big')
    new[offset:offset + width] = (previous ^ xor).to_bytes(width, 'big')
    changed = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
    assert len(changed) == 1 and (old[changed[0]] ^ new[changed[0]]).bit_count() == 1
    target.write_bytes(new)
    assert source.read_bytes() == old
    return {'field_absolute_offset': offset, 'field_bytes': width, 'xor': xor,
            'changed_byte_offsets': changed, 'changed_bits': 1,
            'source_sha256': sha(source), 'mutated_sha256': sha(target),
            'source_unchanged': True}


def migration_cases(args, profile, out, report):
    saved, streams = {}, {}
    for enabled in (False, True):
        label = 'enabled' if enabled else 'disabled'
        path = out / (label + '-forward.migration')
        reverse = out / (label + '-reverse.migration')
        source = Rig(args, out / (label + '-source'), reference=enabled)
        try:
            pure_twice(source, cold_expected(profile, enabled), 'before-any-memory')
            expected = seed_pipes(source, profile, mixed=True) if enabled else cold_expected(profile, False)
            record = save_stream(source, path, expected)
            saved[label], streams[label] = record, path
        finally:
            source.close()
        target = Rig(args, out / (label + '-forward-target'), reference=enabled, incoming=True)
        try:
            observed = load_stream(target, path, expected, profile, enabled)
            reverse_record = save_stream(target, reverse, expected)
            assert encode_snapshot(record['snapshot']) == encode_snapshot(reverse_record['snapshot'])
            report['checks'].append({'name': label + '-forward-roundtrip', 'passed': True,
                                     'payload_bytes_verified': 248})
            report['migrations'].append({'direction': 'forward', 'enabled': enabled,
                                          'source': record, 'observation': observed})
        finally:
            target.close()
        back = Rig(args, out / (label + '-reverse-target'), reference=enabled, incoming=True)
        try:
            observed = load_stream(back, reverse, expected, profile, enabled)
            report['checks'].append({'name': label + '-reverse-roundtrip', 'passed': True,
                                     'payload_bytes_verified': 248})
            report['migrations'].append({'direction': 'reverse', 'enabled': enabled,
                                          'source': reverse_record, 'observation': observed})
        finally:
            back.close()
    for label, enabled in (('enabled', False), ('disabled', True)):
        name = label + '-profile-cross-rejected'
        r = Rig(args, out / name, reference=enabled, incoming=True)
        try:
            detail = expect_load_failure(r, streams[label], profile, enabled, GUARD_ERROR)
            report['rejected_migrations'].append({'name': name, 'unmodified_stream': True, **detail})
            report['checks'].append({'name': name, 'passed': True})
        finally:
            r.close()
    source, record = streams['enabled'], saved['enabled']
    body = record['section']['payload_offset']
    for name, relative, width, xor, stage in FIELD_MUTATIONS:
        path = out / ('corrupt-' + name + '.migration')
        mutation = changed_stream(source, path, body + relative, width, xor)
        r = Rig(args, out / ('reject-' + name), reference=True, incoming=True)
        try:
            detail = expect_load_failure(r, path, profile, True,
                                         GUARD_ERROR if stage == 'guard' else MUTABLE_ERROR)
            item = {'name': name, 'relative_offset': relative, 'stage': stage, **mutation, **detail}
            report['corrupted_stream_rejections'].append(item)
            report['checks'].append({'name': 'reject-' + name, 'passed': True})
        finally:
            r.close()
    # Cross-field validation, not just out-of-range scalar values. Each stream
    # is a fresh copy of the same intact source; no target state is repaired.
    extra = (
        ('enabled-without-system', 'enabled', 120, 4, 0x20),
        ('stream-mode', 'enabled', 120, 4, 0x10),
        ('reset-held-with-control', 'enabled', 216, 4, 0x20),
        ('disabled-ee-mutable', 'disabled', 116, 4, 0x10),
        ('disabled-reset-mutable', 'disabled', 124, 4, 1))
    for name, label, relative, width, xor in extra:
        source, record = streams[label], saved[label]
        path = out / ('corrupt-' + name + '.migration')
        mutation = changed_stream(source, path, record['section']['payload_offset'] + relative, width, xor)
        r = Rig(args, out / ('reject-' + name), reference=label == 'enabled', incoming=True)
        try:
            detail = expect_load_failure(r, path, profile, label == 'enabled', MUTABLE_ERROR)
            report['corrupted_stream_rejections'].append({'name': name, **mutation, **detail})
            report['checks'].append({'name': 'reject-' + name, 'passed': True})
        finally:
            r.close()
    source, record = streams['enabled'], saved['enabled']
    original = source.read_bytes()
    for name in ('section-name', 'version-too-old', 'missing-guard'):
        data = bytearray(original)
        section = record['section']
        if name == 'section-name':
            at = section['name_offset'] + len(TYPE) - 1
            data[at] ^= 1
            diagnostic = 'Unknown section or instance'
        elif name == 'version-too-old':
            data[section['version_offset'] + 3] ^= 1
            diagnostic = TYPE + ': incoming version_id 0 is too old for local minimum version_id 1'
        else:
            at = section['payload_offset']
            del data[at:at + GUARD_BYTES]
            diagnostic = GUARD_ERROR
        path = out / ('structural-' + name + '.migration')
        path.write_bytes(data)
        assert source.read_bytes() == original
        r = Rig(args, out / ('reject-structural-' + name), reference=True, incoming=True)
        try:
            detail = expect_load_failure(r, path, profile, True, diagnostic)
            report['structural_stream_rejections'].append({'name': name,
                'sha256': sha(path), 'bytes': len(data), 'source_sha256': sha(source), **detail})
            report['checks'].append({'name': 'reject-structural-' + name, 'passed': True})
        finally:
            r.close()
    assert all(sha(streams[label]) == saved[label]['sha256'] for label in streams)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--machine', default=DEFAULT_MACHINE)
    parser.add_argument('--profile', type=Path, default=DEFAULT_PROFILE)
    parser.add_argument('--mode', choices=('safety', 'reference'), required=True)
    args = parser.parse_args()
    if not __debug__:
        parser.error('Python optimization disables acceptance assertions')
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'tb371fc-qce-bam-sm8250-v1', 'suite': 'qce-bam-' + args.mode,
              'passed': False, 'checks': [], 'migrations': [], 'rejected_migrations': [],
              'corrupted_stream_rejections': [], 'structural_stream_rejections': [],
              'process_cleanup': [], 'vmstate_version': 1, 'vmstate_minimum_version': 1,
              'vmstate_payload_bytes': 248, 'profile_guard_bytes': 116,
              'pure_observation_getters': 70, 'readonly_properties': 69,
              'guest_cpu_executed': False, 'native_kernel_probe_observed': False,
              'android_boot_passed': False, 'dma_execution_modeled': False,
              'crypto_execution_modeled': False, 'secure_firmware_executed': False,
              'physical_identity_measured': False, 'gic_handling_verified': False,
              'test_oracle_reads_device_implementation': False,
              'qtest_ok_is_memtx_result': False, 'automatic_test_retries': 0}
    exit_code = 1
    try:
        profile = read_profile(args.profile)
        report['profile'] = profile
        report['profile_bound'] = profile['bound']
        report['reference_profile_sha256'] = profile['digest']
        report['reference_profile_file_sha256'] = profile['file_sha256']
        report['binary_sha256_before'] = sha(args.qemu)
        if args.mode == 'reference' and not profile['bound']:
            report['blocked'] = 'UNBOUND: complete reference acceptance requires a reviewed bound policy'
            raise RuntimeError(report['blocked'])
        if args.mode == 'safety':
            report['expected_checks'] = 4
            safety_cases(args, profile, out, report)
        else:
            report['expected_checks'] = len(REFERENCE_CASES) + 4 + 2 + len(FIELD_MUTATIONS) + 5 + 3
            for name, action in REFERENCE_CASES:
                r = Rig(args, out / name, reference=True)
                try:
                    pure_twice(r, cold_expected(profile, True), 'before-any-memory')
                    mappings(r, True)
                    r.intercept()
                    result = action(r, profile)
                    assert r.count['cont'] == 0
                    report['checks'].append({'name': name, 'passed': True, 'detail': result})
                finally:
                    r.close()
            migration_cases(args, profile, out, report)
        report['process_cleanup'] = verify_cleanup(out)
        report['process_cleanup_verified'] = True
        report['binary_unchanged'] = sha(args.qemu) == report['binary_sha256_before']
        assert report['binary_unchanged']
        assert len(report['checks']) == report['expected_checks']
        assert all(item['passed'] for item in report['checks'])
        report['actual_stream_files'] = [{'file': p.name, 'bytes': p.stat().st_size, 'sha256': sha(p)}
                                          for p in sorted(out.glob('*.migration'))]
        report['passed'] = True
        exit_code = 0
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc),
                              'traceback': traceback.format_exc()}
        report['process_cleanup'] = [json.loads(p.read_text()) for p in sorted(out.glob('*/cleanup.json'))]
    finally:
        put_json(out / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'suite': report['suite'],
                      'checks': len(report['checks']), 'result': str(out / 'result.json')}))
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
