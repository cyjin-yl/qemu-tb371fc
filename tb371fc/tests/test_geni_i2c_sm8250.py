#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled acceptance for the explicitly virtual, empty GENI I2C buses.

The tests execute a compiled QEMU through QMP/QTest. No guest CPU, native
kernel, external slave, successful payload, electrical timing or DMA engine is
claimed. Source-derived operations and declared policy values are independent
of the implementation C. Failed observations are retained without retry.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys
import traceback

from geni_i2c_test_contract import *
from geni_i2c_test_transport import I2CRig, file_channels, put_json, sha, verify_i2c_cleanup

if not __debug__:
    raise SystemExit('Acceptance requires assertions; do not use Python -O.')

DEFAULT_MACHINE = 'tb371fc,ufs-enabled=off,gcc-boot-configured=off'
RAM = 0x81020000
WIDTHS = {'b': 1, 'w': 2, 'l': 4, 'q': 8}
LEGACY_DYNAMIC = ('registers-hex', 'tx-fifo-hex', 'rx-fifo-hex',
                  'tx-count', 'rx-count', 'tx-remaining', 'tx-sent', 'rx-received',
                  'se-clock-period', 'm-ahb-clock-period', 's-ahb-clock-period',
                  'fifo-bytes', 'tx-active', 'rx-active', 'tx-zero-pending',
                  'reset-held', 'irq-level')


def identity(instance, enabled):
    number, base, spi, wrapper, *_ = instance_row(instance)
    return {'profile': PROFILE if enabled else 'disabled', 'enabled-reference': enabled,
            'reference-identity': PROFILE_ID, 'instance-id': number,
            'instance-id-readonly': number,
            'physical-base': base, 'window-bytes': WINDOW_BYTES,
            'interrupt-spi': spi, 'wrapper-index': wrapper,
            'main-fw-revision': FW_REVISION, 'secondary-fw-revision': FW_REVISION,
            'main-config-revision': 0, 'secondary-config-revision': 0,
            'fifo-words': FIFO_WORDS, 'fifo-word-bits': FIFO_WORD_BITS,
            'ios-idle': IOS_IDLE, 'vmstate-bytes': VMSTATE_BYTES,
            'profile-wire-bytes': GUARD_BYTES, 'attached-slaves': 0,
            'tx-payload-bytes': 0, 'rx-payload-bytes': 0,
            'dma-read-bytes': 0, 'dma-write-bytes': 0,
            'command-active': False, 'firmware-executed': False,
            'physical-state-captured': False, 'dma-data-modeled': False,
            'address-phase-modeled': True}


def state(r, instance):
    result = {key: r.qom(instance, key) for key in GETTERS}
    assert set(result) == set(GETTERS)
    assert all(type(result[k]) is int and 0 <= result[k] < (1 << 64)
               for k in INTEGER_FIELDS + CONFIG_INTEGER_FIELDS)
    assert all(type(result[k]) is bool for k in BOOLEAN_FIELDS)
    assert all(type(result[k]) is str for k in STRING_FIELDS)
    assert {k: result[k] for k in identity(instance, r.enabled)} == identity(instance, r.enabled)
    assert re.fullmatch('[0-9a-f]{296}', result['registers-hex'])
    assert re.fullmatch('[0-9a-f]{128}', result['tx-fifo-hex'])
    registers = register_values(result)
    assert all(not (registers[o] & ~mask) for o, mask in zip(REGISTER_ORDER, REGISTER_MASKS))
    assert result['tx-count'] <= FIFO_WORDS
    words = struct.unpack('>16I', bytes.fromhex(result['tx-fifo-hex']))
    assert not any(words[result['tx-count']:])
    bus = (r.enabled and not result['reset-held'] and
           result['m-ahb-clock-period'] != 0 and result['s-ahb-clock-period'] != 0)
    assert result['bus-available'] is bus
    assert result['engine-available'] is (bus and result['se-clock-period'] != 0)
    irq = expected_irq(enabled=r.enabled, reset=result['reset-held'],
                       se_period=result['se-clock-period'], m_ahb_period=result['m-ahb-clock-period'],
                       s_ahb_period=result['s-ahb-clock-period'], global_enable=registers[GLOBAL_EN],
                       main_status=registers[M_STATUS], main_enable=registers[M_EN],
                       tx_status=registers[DMA_TX_STATUS], tx_enable=registers[DMA_TX_EN],
                       rx_status=registers[DMA_RX_STATUS], rx_enable=registers[DMA_RX_EN])
    assert result['irq-level'] is irq
    if not r.enabled or result['reset-held']:
        assert not any(registers.values()) and result['tx-count'] == 0 and not any(words)
    return result


def world(r):
    return {str(row[0]): state(r, row[0]) for row in INSTANCES}


def pure_twice(r, label='first-pure-observation'):
    before = dict(r.count)
    first, second = world(r), world(r)
    after = dict(r.count)
    assert first == second
    assert after['qom_get'] - before['qom_get'] == 2 * len(GETTERS) * len(INSTANCES)
    assert {k: v for k, v in before.items() if k != 'qom_get'} == {
        k: v for k, v in after.items() if k != 'qom_get'}
    assert before['memory'] == after['memory'] == before['cont'] == after['cont'] == 0
    evidence = {'first': first, 'second': second, 'counters_before': before,
                'counters_after': after, 'no_memory_or_cont': True}
    put_json(r.directory / (label + '.json'), evidence)
    return evidence


def legacy_world(r):
    return {path: {key: r.monitor('qom-get', {'path': path, 'property': key})
                   for key in LEGACY_DYNAMIC}
            for path in ('/machine/uart', '/machine/uart-hs')}


def strict_leaves(text):
    assert type(text) is str
    sections, section = [], []
    for line in text.splitlines():
        if line.startswith('FlatView #'):
            if section:
                sections.append(section)
            section = []
        section.append(line)
    if section:
        sections.append(section)
    memory = [part for part in sections if any(
        re.fullmatch(r'\s*AS "memory", root: system\s*', line) for line in part)]
    assert len(memory) == 1
    leaves = []
    for line in memory[0]:
        match = re.fullmatch(r'\s+([0-9a-fA-F]+)-([0-9a-fA-F]+) \(prio -?[0-9]+, (?:i/o|ram|rom|container)\): (.+)', line)
        if match:
            low, high = int(match[1], 16), int(match[2], 16)
            assert low <= high < (1 << 64) and match[3].strip()
            leaves.append((low, high, match[3]))
        else:
            assert re.match(r'\s*(?:0[xX])?[0-9a-fA-F]+\s*-\s*(?:0[xX])?[0-9a-fA-F]+', line) is None
    return leaves


def mappings(r, label='active-memory-map.txt'):
    text = r.monitor('human-monitor-command', {'command-line': 'info mtree -f'})
    (r.directory / label).write_bytes(text.encode())
    leaves = strict_leaves(text)
    # Exact declared region-name interface; not inferred from a prefix match.
    expected = [(base, base + WINDOW_BYTES - 1, TYPE)
                for number, base, *_ in INSTANCES] if r.enabled else []
    observed = [x for x in leaves if x[2].startswith(TYPE)]
    assert sorted(observed) == sorted(expected), (observed, expected)
    for number, base, *_ in INSTANCES:
        overlapping = [x for x in leaves if x[0] < base + WINDOW_BYTES and x[1] >= base]
        wanted = [x for x in expected if x[0] == base]
        assert overlapping == wanted, (number, overlapping, wanted)
    return {'mapped_instances': sorted(observed), 'mapped_bytes': len(expected) * WINDOW_BYTES}


def device_diagnostics(text):
    return [line for line in text.splitlines() if line.startswith(TYPE + ': rejected ')]


def core_diagnostics(text):
    return [(op, int(a, 16), int(n), region, reason) for op, a, n, region, reason in re.findall(
        r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)", text)]


def reject(r, instance, offset, *, value=None, width='l', reason):
    before, old = world(r), r.stderr()
    direction = 'read' if value is None else 'write'
    base = instance_row(instance)[1]
    command = f'{direction}{width} {base + offset:#x}'
    if value is not None:
        command += f' {value:#x}'
    r.qt(command)
    after, new = world(r), r.stderr()
    assert after == before
    expected = (TYPE + f': rejected {direction} instance={instance} offset={offset:#x}'
                + f' size={WIDTHS[width]} reason={reason}')
    added = device_diagnostics(new)[len(device_diagnostics(old)):]
    assert added == [expected], (command, added, expected)
    assert core_diagnostics(new) == core_diagnostics(old)
    return {'command': command, 'expected_diagnostic': expected, 'state_unchanged': True,
            'before': before, 'after': after, 'qtest_ok_is_memtx_result': False}


def unmapped(r, address, write=False):
    before, old = world(r), r.stderr()
    command = ('writel' if write else 'readl') + f' {address:#x}' + (' 0' if write else '')
    r.qt(command)
    assert world(r) == before
    assert device_diagnostics(r.stderr()) == device_diagnostics(old)
    added = core_diagnostics(r.stderr())[len(core_diagnostics(old)):]
    assert added == [('write' if write else 'read', address, 4, '(null)', 'rejected')]
    return {'command': command, 'core_diagnostic': added, 'state_unchanged': True}


def masked_write(r, instance, offset, value, target=None, operation='replace'):
    """Declared ordinary-register masking, distinct from format rejection."""
    target = offset if target is None else target
    assert target in REGISTER_ORDER and operation in ('replace', 'set', 'clear')
    mask = REGISTER_MASKS[REGISTER_ORDER.index(target)]
    before, old_log = world(r), r.stderr()
    values = register_values(before[str(instance)])
    old_value = values[target]
    accepted = value & mask
    updated = (accepted if operation == 'replace' else old_value | accepted
               if operation == 'set' else old_value & ~accepted)
    values[target] = updated
    expected = {key: dict(item) for key, item in before.items()}
    expected[str(instance)]['registers-hex'] = struct.pack('>37I', *[values[o] for o in REGISTER_ORDER]).hex()
    r.write(instance, offset, value)
    after = world(r)
    assert after == expected
    assert device_diagnostics(r.stderr()) == device_diagnostics(old_log)
    assert core_diagnostics(r.stderr()) == core_diagnostics(old_log)
    return {'command': f'writel {instance_row(instance)[1] + offset:#x} {value:#x}',
            'offset': offset, 'target_register': target, 'value': value, 'mask': mask,
            'operation': operation, 'old_value': old_value, 'expected_value': updated,
            'before': before, 'after': after, 'only_declared_mask_effect': True,
            'rejection_diagnostic_added': False}


def absolute_read(r, address):
    return int(r.qt(f'readl {address:#x}')[0], 0)


def absolute_write(r, address, value):
    r.qt(f'writel {address:#x} {value:#x}')


def set_vote(r, offset, mask, enabled):
    before = absolute_read(r, GCC_BASE + offset)
    value = before | mask if enabled else before & ~mask
    absolute_write(r, GCC_BASE + offset, value)
    assert absolute_read(r, GCC_BASE + offset) == value
    return {'offset': offset, 'mask': mask, 'before': before, 'after': value}


def clocks_on(r):
    masks = {}
    for number, base, spi, wrapper, index, offset, mask in INSTANCES:
        masks[offset] = masks.get(offset, 0) | mask
        wo, m, s, _, _ = WRAPPER_CLOCKS[wrapper]
        masks[wo] = masks.get(wo, 0) | m | s
    changes = [set_vote(r, offset, mask, True) for offset, mask in sorted(masks.items())]
    active = world(r)
    assert all(s['bus-available'] and s['engine-available'] for s in active.values())
    return changes


def run_operations(r, instance, operations):
    rows = []
    before_log = r.stderr()
    base = instance_row(instance)[1]
    for op in operations:
        reply = r.qt(op.command(base))
        if op.direction == 'read':
            assert len(reply) == 1 and int(reply[0], 0) == op.value, (op, reply)
        rows.append({**op.as_dict(base), 'reply': reply})
    assert device_diagnostics(r.stderr()) == device_diagnostics(before_log)
    assert core_diagnostics(r.stderr()) == core_diagnostics(before_log)
    return rows


def init_all(r):
    changes = clocks_on(r)
    traces = {}
    for instance, *_ in INSTANCES:
        ops = cold_operations()
        assert len(ops) == 28
        traces[str(instance)] = run_operations(r, instance, ops)
        values = {offset: 0 for offset in REGISTER_ORDER}
        for op in ops:
            if op.direction == 'write':
                values[op.offset] = op.value
        assert register_values(state(r, instance)) == values
    return {'clock_changes': changes, 'cold_traces': traces}


def assert_only_changed(before, after, allowed):
    for key in before:
        if int(key) not in allowed:
            assert after[key] == before[key], key


def case_off(r):
    mapping = mappings(r)
    operations = []
    for _, base, *_ in INSTANCES:
        operations += [unmapped(r, base + FW_MAIN), unmapped(r, base + M_CMD, True)]
    return {'mapping': mapping, 'rejected_unmapped_operations': operations}


def case_cold_nine(r):
    mapping = mappings(r)
    initial = world(r)
    assert all(not any(register_values(s).values()) for s in initial.values())
    detail = init_all(r)
    property_types = {}
    for instance, *_ in INSTANCES:
        props = {x['name']: x['type'] for x in r.monitor('qom-list', {'path': device_path(instance)})}
        wanted = {**{k: 'uint64' for k in INTEGER_FIELDS},
                  **{k: 'bool' for k in BOOLEAN_FIELDS},
                  **{k: 'string' for k in STRING_FIELDS}, 'instance-id': 'uint32'}
        assert {k: props[k] for k in GETTERS} == wanted
        property_types[str(instance)] = wanted
    after = world(r)
    assert all(not s['irq-level'] for s in after.values())
    holes = [unmapped(r, 0x984000 - 4), unmapped(r, 0x988000)]
    r.monitor('system_reset')
    reset = world(r)
    assert all(not any(register_values(s).values()) and s['tx-count'] == 0 for s in reset.values())
    return {'mapping': mapping, 'initial': initial, 'initialized': after,
            'source_derived_initialization': detail, 'property_types': property_types,
            'neighbor_holes': holes, 'reset': reset}


def case_fifo_nack(r):
    init = init_all(r)
    r.intercept(1)
    r.check_irq(False)
    cases = []
    for receive, length in ((False, 0), (True, 0), (False, 1), (True, 1), (False, 32), (True, 32)):
        before = world(r)
        current = register_values(before['1'])
        trace = run_operations(r, 1, transfer_setup_operations(receive, length, 0x28,
                    stretch=length == 1, main=current[M_EN], secondary=current[S_EN],
                    prior_dma=current[DMA_MODE]))
        failed = world(r)
        assert_only_changed(before, failed, {1})
        regs = register_values(failed['1'])
        assert regs[M_STATUS] == NACK and not (regs[M_STATUS] & DONE)
        assert regs[DMA_TX_STATUS] == regs[DMA_RX_STATUS] == 0
        assert failed['1']['tx-count'] == 0 and failed['1']['command-active'] is False
        r.check_irq(True)
        irq = run_operations(r, 1, error_irq_operations(False))
        r.check_irq(False)
        cleanup = run_operations(r, 1, error_cleanup_operations(receive, False))
        r.check_irq(False)
        final = world(r)
        assert register_values(final['1'])[M_STATUS] == 0 and final['1']['tx-count'] == 0
        cases.append({'receive': receive, 'length': length, 'setup': trace,
                      'nack_state': failed, 'irq_service': irq, 'cleanup': cleanup, 'final': final})
    return {'initialization': init, 'transfers': cases,
            'payload_success': False, 'expected_consumer_error': -107,
            'actual_guest_driver_executed': False}


def case_dma_nack(r):
    init = init_all(r)
    r.intercept(1)
    cases = []
    for receive, length in ((False, 33), (True, 33), (False, 4096), (True, 4096)):
        witness = bytes((17 * i + 91) & 255 for i in range(length))
        r.write_ram(RAM, witness)
        before = world(r)
        current = register_values(before['1'])
        trace = run_operations(r, 1, transfer_setup_operations(receive, length, 0x28, pointer=RAM,
                    main=current[M_EN], secondary=current[S_EN], prior_dma=current[DMA_MODE]))
        failed = world(r)
        assert_only_changed(before, failed, {1})
        regs = register_values(failed['1'])
        assert regs[M_STATUS] == NACK and regs[DMA_TX_STATUS] == regs[DMA_RX_STATUS] == 0
        assert not (regs[M_EN] & DONE), '987 and original-kernel SE_DMA branch clears DONE enable'
        assert regs[DMA_RX_LEN if receive else DMA_TX_LEN] == length
        assert regs[DMA_RX_PTR_L if receive else DMA_TX_PTR_L] == RAM
        assert r.read(1, DMA_RX_LEN_IN if receive else DMA_TX_LEN_IN) == 0
        r.check_irq(True)
        irq = run_operations(r, 1, error_irq_operations(True))
        r.check_irq(False)
        cleanup = run_operations(r, 1, error_cleanup_operations(receive, True))
        r.check_irq(False)
        reset = world(r)
        reg = register_values(reset['1'])
        status_offset = DMA_RX_STATUS if receive else DMA_TX_STATUS
        assert reg[status_offset] == DMA_RESET_DONE
        assert not (reg[status_offset] & (DMA_DONE | DMA_EOT | DMA_SBE))
        assert reg[DMA_RX_EN if receive else DMA_TX_EN] == 7
        assert reg[M_STATUS] == 0
        actual = r.read_ram(RAM, length)
        assert actual == witness
        cases.append({'receive': receive, 'length': length, 'setup': trace,
                      'nack_state': failed, 'irq_service': irq, 'cleanup': cleanup,
                      'reset_state': reset, 'witness_bytes': len(witness),
                      'witness_sha256': hashlib.sha256(witness).hexdigest(),
                      'whole_witness_after_sha256': hashlib.sha256(actual).hexdigest(),
                      'reset_done_is_data_done': False})
    return {'initialization': init, 'transfers': cases,
            'guest_memory_write_to_witness_detected': False,
            'no_dma_read_inference_from_canaries': True,
            'reference_reset_wait_return_is_ignored': True,
            'actual_guest_wait_executed': False}


def case_fifo_capacity_and_controls(r):
    init = init_all(r)
    words = [0xa5000000 | i for i in range(16)]
    for value in words:
        r.write(1, TX_FIFO, value)
    filled = state(r, 1)
    assert filled['tx-count'] == 16
    assert bytes.fromhex(filled['tx-fifo-hex']) == struct.pack('>16I', *words)
    overflow = reject(r, 1, TX_FIFO, value=0xfedcba98, reason='tx-fifo-full')
    r.write(1, M_CMD, command_value(1, 0x28))
    failed = state(r, 1)
    assert failed['tx-fifo-hex'] == filled['tx-fifo-hex'] and failed['tx-count'] == 16
    assert register_values(failed)[M_STATUS] == NACK
    r.write(1, M_CTRL, 4)
    cancelled = state(r, 1)
    assert cancelled['tx-count'] == 0 and cancelled['tx-fifo-hex'] == '0' * 128
    assert register_values(cancelled)[M_STATUS] & CANCEL
    r.write(1, M_CLEAR, CLEAR_ALL)
    r.write(1, TX_FIFO, 0xdeadbeef)
    r.write(1, M_CTRL, 2)
    aborted = state(r, 1)
    assert aborted['tx-count'] == 0 and register_values(aborted)[M_STATUS] == ABORT
    empty_rx = reject(r, 1, RX_FIFO, reason='rx-fifo-empty')
    bad_control = reject(r, 1, M_CTRL, value=6, reason='unsupported-control')
    return {'initialization': init, 'filled': filled, 'overflow': overflow,
            'nack_preserved_fifo': failed, 'cancelled': cancelled, 'aborted': aborted,
            'empty_rx': empty_rx, 'bad_control': bad_control}


def case_unsupported_commands(r):
    init = init_all(r)
    r.intercept(1)
    commands = [op << 27 for op in (0, 5, 6, 7, 31)]
    commands += [(1 << 27) | value for value in (1, 2, 8, 16, 64, 128, 256, 1 << 16, 1 << 26)]
    rows = []
    for value in commands:
        before = world(r)
        r.write(1, M_CMD, value)
        after = world(r)
        assert_only_changed(before, after, {1})
        assert expected_command_status(value) == UNSUPPORTED_COMMAND
        assert register_values(after['1'])[M_STATUS] == UNSUPPORTED_COMMAND
        assert not (register_values(after['1'])[M_STATUS] & DONE)
        r.check_irq(True)
        r.write(1, M_CLEAR, CLEAR_ALL)
        r.check_irq(False)
        rows.append({'command': value, 'state': after, 'status': UNSUPPORTED_COMMAND})
    return {'initialization': init, 'semantic_command_errors': rows,
            'unknown_register_memtx_errors_are_separate': True}


def case_irq_masks_and_clocks(r):
    init = init_all(r)
    r.intercept(1)
    r.write(1, M_EN, 0)
    r.write(1, M_CMD, command_value(2, 0x28))
    r.check_irq(False)
    masked = state(r, 1)
    assert register_values(masked)[M_STATUS] == NACK
    r.write(1, M_EN, FAILURE)
    r.check_irq(True)
    r.write(1, GLOBAL_EN, 0)
    r.check_irq(False)
    r.write(1, GLOBAL_EN, 4)
    r.check_irq(True)
    r.write(1, M_CLEAR, NACK_GP)
    assert r.read(1, M_STATUS) == FAILURE
    r.check_irq(True)
    r.write(1, M_CLEAR, FAILURE)
    r.check_irq(False)
    r.write(1, DMA_TX_RESET, 1)
    r.write(1, DMA_TX_EN_SET, 7)
    r.write(1, GLOBAL_EN, 6)
    r.check_irq(False)
    r.write(1, DMA_TX_EN_SET, DMA_RESET_DONE)
    r.check_irq(True)
    pending = world(r)
    r.write(1, DMA_TX_EN_CLEAR, DMA_RESET_DONE)
    r.check_irq(False)
    r.write(1, DMA_TX_EN_SET, DMA_RESET_DONE)
    r.check_irq(True)
    number, base, spi, wrapper, index, vote, mask = instance_row(1)
    set_vote(r, vote, mask, False)
    r.check_irq(False)
    se_off = world(r)
    assert se_off['1']['se-clock-period'] == 0 and se_off['1']['bus-available'] is True
    assert register_values(se_off['1']) == register_values(pending['1'])
    assert r.read(1, FW_MAIN) == FW_REVISION
    gated = reject(r, 1, M_CMD, value=command_value(1, 0x28), reason='engine-unavailable')
    gated_control = reject(r, 1, M_CTRL, value=4, reason='engine-unavailable')
    gated_reset = reject(r, 1, DMA_RX_RESET, value=1, reason='engine-unavailable')
    set_vote(r, vote, mask, True)
    r.check_irq(True)
    restored = world(r)
    assert restored == pending
    r.write(1, DMA_TX_CLEAR, DMA_RESET_DONE)
    r.check_irq(False)
    return {'initialization': init, 'masked_nack': masked, 'pending_reset_irq': pending,
            'se_clock_off': se_off, 'gated_command': gated,
            'gated_control': gated_control, 'gated_dma_reset': gated_reset, 'restored': restored,
            'qtest_intercepts_one_actual_sysbus_output': True,
            'guest_isr_or_GIC_delivery_executed': False}


def case_strict_access(r):
    init = init_all(r)
    # Seed all nine functional states so a rejected transaction cannot pass by
    # accidentally clearing an already-zero register/FIFO or another instance.
    for number, *_ in INSTANCES:
        r.write(number, TX_LEN, 0x10203000 | number)
        r.write(number, TX_FIFO, 0xa5000000 | number)
    rows = []
    for offset in (TX_LEN, M_CMD, FW_MAIN):
        for width in ('b', 'w', 'q'):
            rows.append(reject(r, 1, offset, width=width, reason='access-shape'))
            rows.append(reject(r, 1, offset, value=0x11223344, width=width, reason='access-shape'))
        for delta in (1, 2, 3):
            rows.append(reject(r, 1, offset + delta, reason='access-shape'))
            rows.append(reject(r, 1, offset + delta, value=0x11223344, reason='access-shape'))
    readonly = (CFG, S_CFG, STATUS, FIFO_DISABLE, FW_MAIN, FW_SECONDARY,
                S_CMD, S_STATUS, TX_STATUS, RX_STATUS, IOS, DMA_TX_LEN_IN,
                DMA_RX_LEN_IN, HW_PARAM0, HW_PARAM1, DMA_DEBUG)
    for offset in readonly:
        rows.append(reject(r, 1, offset, value=1, reason='read-only-or-unknown-register'))
    for offset in (M_STATUS, DMA_TX_STATUS, DMA_RX_STATUS):
        rows.append(reject(r, 1, offset, value=1, reason='read-only-status'))
    for offset in (0x10, 0x3ffc):
        rows.append(reject(r, 1, offset, reason='unknown-register'))
        rows.append(reject(r, 1, offset, value=1, reason='read-only-or-unknown-register'))
    bad_formats = ((BYTE_GRAN, 1), (TX_PACK0, 1), (TX_PACK1, 1),
                   (RX_PACK0, 1), (RX_PACK1, 1), (DMA_TX_ATTR, 2),
                   (DMA_RX_ATTR, 1), (GSI_EN, 1))
    for offset, value in bad_formats:
        rows.append(reject(r, 1, offset, value=value, reason='unsupported-format'))
    for offset in (DMA_TX_RESET, DMA_RX_RESET):
        rows.append(reject(r, 1, offset, value=2, reason='unsupported-dma-reset'))
    masked = [masked_write(r, 1, offset, value) for offset, value in
              ((FORCE_DEFAULT, 2), (OUTPUT_CTRL, 0x80), (CGC, 0x400),
               (M_CLK, 2), (S_CLK, 2), (CLK_SEL, 8), (DMA_MODE, 2),
               (SCL, 0x40000000), (M_EN, 1 << 15), (S_EN, 1 << 6),
               (TX_WM, 64), (RX_WM, 64), (RX_RFR, 64))]
    masked += [masked_write(r, 1, DMA_TX_EN_SET, 1 << 31, DMA_TX_EN, 'set'),
               masked_write(r, 1, DMA_RX_EN_CLEAR, 1 << 31, DMA_RX_EN, 'clear'),
               masked_write(r, 1, GLOBAL_EN, 0x10),
               masked_write(r, 1, DMA_GENERAL, 0x200)]
    # Frozen interface includes 36 explicit read-only observations. The two
    # qdev configuration properties are separately typed above; this is not a
    # request to mutate their post-realize configuration.
    readonly_properties = [x for x in GETTERS if x not in ('enabled-reference', 'instance-id')]
    before = world(r)
    property_errors = []
    for prop in readonly_properties:
        response = r.raw_monitor('qom-set', {'path': device_path(1), 'property': prop,
                                            'value': before['1'][prop]})
        assert 'error' in response and 'return' not in response
        property_errors.append({'property': prop, 'error': response['error']})
    assert world(r) == before
    return {'initialization': init, 'rejections': rows, 'reserved_bit_writes': masked,
            'readonly_properties': property_errors,
            'typed_transaction_widths': [1, 2, 4, 8],
            'arbitrary_bulk_buffer_atomicity_claimed': False,
            'all_nine_states_unchanged_per_rejection': True}


def case_ahb_gating(r):
    init = init_all(r)
    r.intercept(1)
    r.write(1, M_CMD, command_value(1, 0x28))
    r.check_irq(True)
    original = world(r)
    gated = []
    vote, m_ahb, s_ahb, _, _ = WRAPPER_CLOCKS[0]
    for mask, field in ((m_ahb, 'm-ahb-clock-period'), (s_ahb, 's-ahb-clock-period')):
        set_vote(r, vote, mask, False)
        r.check_irq(False)
        stopped = world(r)
        assert_only_changed(original, stopped, {1, 3, 5})
        for number in (1, 3, 5):
            assert stopped[str(number)][field] == 0
            assert register_values(stopped[str(number)]) == register_values(original[str(number)])
            assert stopped[str(number)]['tx-fifo-hex'] == original[str(number)]['tx-fifo-hex']
        checks = [reject(r, 1, FW_MAIN, reason='bus-unavailable'),
                  reject(r, 3, TX_LEN, value=55, reason='bus-unavailable'),
                  reject(r, 5, M_CMD, value=command_value(1, 0x28), reason='bus-unavailable')]
        set_vote(r, vote, mask, True)
        r.check_irq(True)
        assert world(r) == original
        gated.append({'clock': field, 'stopped': stopped, 'rejections': checks,
                      'all_state_restored': True})
    r.write(1, M_CLEAR, CLEAR_ALL)
    r.check_irq(False)
    return {'initialization': init, 'gates': gated, 'pending_status_retained': True}


def case_shared_reset(r):
    init = init_all(r)
    set_vote(r, 0x52008, 1 << 16, True)  # existing HS SE clock
    set_vote(r, 0x52010, 1 << 8, True)   # existing console UART SE clock
    legacy_cold = legacy_world(r)
    r.intercept(1)
    rows = []
    for wrapper in (0, 1, 2):
        for number, *_ in INSTANCES:
            r.write(number, TX_LEN, 100 + number)
            r.write(number, TX_FIFO, 0xbeef0000 | number)
            r.write(number, M_CMD, command_value(1, (0x20 + number) & 0x7f))
        # Deliberately change the old consumers' configuration, never start a
        # payload transfer. Their exact observed fresh state is the reset oracle.
        for base in (0x998000, 0xa90000):
            absolute_write(r, base + GLOBAL_EN, 0)
            absolute_write(r, base + M_EN, 0)
            absolute_write(r, base + TX_WM, 3)
        before, old_before = world(r), legacy_world(r)
        assert old_before != legacy_cold
        members = {x[0] for x in INSTANCES if x[3] == wrapper}
        bcr = GCC_BASE + WRAPPER_CLOCKS[wrapper][3]
        absolute_write(r, bcr, 1)
        held, old_held = world(r), legacy_world(r)
        assert_only_changed(before, held, members)
        for number in members:
            s = held[str(number)]
            assert s['reset-held'] and s['tx-count'] == 0
            assert not any(register_values(s).values()) and s['tx-fifo-hex'] == '0' * 128
        if wrapper == 0:
            r.check_irq(False)
        selected = '/machine/uart-hs' if wrapper == 0 else '/machine/uart' if wrapper == 2 else None
        for path in old_before:
            if path != selected:
                assert old_held[path] == old_before[path]
            else:
                assert old_held[path]['reset-held'] is True
                assert {k: v for k, v in old_held[path].items() if k != 'reset-held'} == {
                    k: v for k, v in legacy_cold[path].items() if k != 'reset-held'}
        rejected = reject(r, min(members), FW_MAIN, reason='bus-unavailable')
        absolute_write(r, bcr, 0)
        released, old_released = world(r), legacy_world(r)
        for number in members:
            assert not released[str(number)]['reset-held']
            assert not any(register_values(released[str(number)]).values())
        if selected:
            assert old_released[selected] == legacy_cold[selected]
        for path in old_before:
            if path != selected:
                assert old_released[path] == old_before[path]
        # The next iteration only needs the valid cold configuration again.
        for number in members:
            run_operations(r, number, cold_operations())
        rows.append({'wrapper': wrapper, 'bcr': bcr, 'members': sorted(members),
                     'before': before, 'held': held, 'released': released,
                     'old_before': old_before, 'old_held': old_held,
                     'old_released': old_released, 'preserved_legacy_destination': selected,
                     'held_access_rejection': rejected})
    return {'initialization': init, 'legacy_cold': legacy_cold, 'resets': rows,
            'all_three_wrapper_outputs_tested': True,
            'existing_uart_and_hs_byte_or_backend_transfer_claimed': False}


def locate_sections(raw, enabled, expected):
    """Locate every FULL section for this exact type, not other machine data.

    qdev + sysbus + parent BusClass + savevm prove the ordinary vmsd idstr and
    auto instance IDs 0..8. dev->id is not used as an assumed migration prefix.
    Same-name START or different-version/instance duplicates are not ignored.
    """
    assert type(raw) is bytes and raw[:8] == b'QEVM\x00\x00\x00\x03'
    name = TYPE.encode()
    needle = bytes((len(name),)) + name
    locations, start = [], 0
    while True:
        position = raw.find(needle, start)
        if position < 0:
            break
        start = position + 1
        header = position - 5
        if header >= 8 and raw[header] in (1, 4):
            locations.append((header, position + len(needle)))
    assert len(locations) == len(INSTANCES), locations
    result, ids = [], set()
    for order, ((header, fields), (instance, *_)) in enumerate(zip(locations, INSTANCES)):
        assert raw[header] == 4, 'This ordinary vmsd must use SECTION_FULL'
        section_id = struct.unpack_from('>I', raw, header + 1)[0]
        saved_instance, version = struct.unpack_from('>II', raw, fields)
        assert section_id not in ids and saved_instance == order and version == 1
        ids.add(section_id)
        body = fields + 8
        wire = expected_wire(instance, enabled, expected[str(instance)])
        assert raw[body:body + VMSTATE_BYTES] == wire
        footer = body + VMSTATE_BYTES
        assert raw[footer:footer + 5] == b'\x7e' + struct.pack('>I', section_id)
        if result:
            assert header >= result[-1]['payload_offset'] + VMSTATE_BYTES + 5
        result.append({'section_name': TYPE, 'section_type': 4, 'section_id': section_id,
                       'instance_id': saved_instance, 'version': version,
                       'profile_instance': instance, 'payload_offset': body,
                       'payload_bytes': VMSTATE_BYTES, 'profile_bytes': GUARD_BYTES,
                       'payload_hex': wire.hex(), 'payload_sha256': hashlib.sha256(wire).hexdigest(),
                       'complete_payload_verified': True})
    return result


def save_stream(r, path):
    before = world(r)
    # Stop explicitly so the source snapshot, provider periods and VMState are
    # compared at one settled state; no guest instructions run under qtest.
    r.monitor('stop')
    status = r.monitor('query-status')
    assert status['running'] is False
    assert world(r) == before
    r.monitor('migrate', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False and status['status'] == 'postmigrate'
    assert world(r) == before
    raw = path.read_bytes()
    sections = locate_sections(raw, r.enabled, before)
    result = {'file': path.name, 'bytes': len(raw), 'sha256': sha(path),
              'snapshot': before, 'sections': sections, 'query_migrate': migration,
              'query_status': status, 'all_nine_payload_bytes': 9 * VMSTATE_BYTES,
              'whole_machine_payload_oracle_claimed': False}
    put_json(Path(str(path) + '.sections.json'), result)
    return result


def seed_migration_state(r):
    initial = init_all(r)
    for number, *_ in INSTANCES:
        r.write(number, OUTPUT_CTRL, number & 0x7f)
        r.write(number, CGC, 0x17f)
        r.write(number, S_CLK, 0x31)
        r.write(number, CLK_SEL, 1)
        r.write(number, TX_LEN, 0x10000 + number)
        r.write(number, RX_LEN, 0x20000 + number)
        r.write(number, TX_WM, 3)
        for i in range(5):
            r.write(number, TX_FIFO, 0x50100000 | (number << 8) | i)
        r.write(number, DMA_MODE, 1)
        r.write(number, DMA_TX_PTR_L, RAM)
        r.write(number, DMA_TX_PTR_H, 0x1234)
        r.write(number, DMA_TX_ATTR, 1)
        r.write(number, DMA_TX_LEN, 65 + number)
        r.write(number, DMA_RX_PTR_L, RAM + 4096)
        r.write(number, DMA_RX_PTR_H, 0x5678)
        r.write(number, DMA_RX_ATTR, 0)
        r.write(number, DMA_RX_LEN, 129 + number)
        r.write(number, DMA_TX_EN_SET, 7)
        r.write(number, DMA_RX_EN_SET, 7)
        r.write(number, M_CMD, command_value(1, 0x28, True))
    # Distinct canonical state classes in a single nine-instance save.
    r.write(3, M_CLEAR, CLEAR_ALL)
    r.write(3, DMA_TX_RESET, 1)
    r.write(3, DMA_TX_EN_SET, DMA_RESET_DONE)
    r.write(3, DMA_RX_RESET, 1)
    r.write(3, DMA_RX_EN_SET, DMA_RESET_DONE)
    set_vote(r, instance_row(5)[5], instance_row(5)[6], False)
    absolute_write(r, GCC_BASE + WRAPPER_CLOCKS[1][3], 1)
    set_vote(r, WRAPPER_CLOCKS[2][0], WRAPPER_CLOCKS[2][2], False)
    saved = world(r)
    assert saved['1']['irq-level'] is True and saved['3']['irq-level'] is True
    assert saved['5']['se-clock-period'] == 0
    assert all(saved[str(n)]['reset-held'] for n in (9, 11, 13))
    assert all(saved[str(n)]['s-ahb-clock-period'] == 0 for n in (15, 16, 19))
    witness = bytes((i * 29 + 7) & 255 for i in range(4096))
    r.write_ram(RAM, witness)
    assert r.read_ram(RAM, len(witness)) == witness
    return {'initialization': initial, 'snapshot': saved,
            'witness_address': RAM, 'witness_bytes': len(witness),
            'witness_sha256': hashlib.sha256(witness).hexdigest()}


def load_stream(r, path, source, witness=None, pre=None):
    pre = pure_twice(r, 'preload-pure') if pre is None else pre
    before = dict(r.count)
    r.intercept(1)
    r.check_irq(False)
    r.monitor('migrate-incoming', file_channels(path))
    migration = r.migrate_wait()
    status = r.monitor('query-status')
    assert status['running'] is False
    post = pure_twice(r, 'first-postload-pure')
    assert post['first'] == source['snapshot']
    assert r.count['memory'] == r.count['cont'] == 0
    # The source's validated level is restored immediately by post_load. A
    # successful restore may assert the line before cont; failure must not.
    restored_level = source['snapshot']['1']['irq-level']
    r.check_irq(restored_level)
    restored_events = list(r.irq_events)
    result = {'preload': pre, 'first_postload': post,
              'counters_before_incoming': before, 'query_migrate': migration,
              'query_status': status, 'no_memory_or_cont_before_two_postload_worlds': True,
              'restored_irq_before_cont': restored_level,
              'actual_irq_events_before_cont': restored_events,
              'whole_machine_ram_not_loaded_claimed': False}
    if witness is not None:
        actual = r.read_ram(RAM, witness['witness_bytes'])
        assert hashlib.sha256(actual).hexdigest() == witness['witness_sha256']
        result['loaded_ram_witness_sha256'] = hashlib.sha256(actual).hexdigest()
    if r.enabled:
        result['continuation'] = r.start_qtest()
        assert world(r) == source['snapshot']
        r.check_irq(restored_level)
    return result


def reject_stream(r, path, diagnostic, pre=None, instance=1):
    pre = pure_twice(r, 'preload-pure') if pre is None else pre
    r.intercept(instance)
    r.check_irq(False)
    before = dict(r.count)
    r.expected_exit = 1
    command_error = None
    try:
        r.monitor('migrate-incoming', file_channels(path))
    except (AssertionError, OSError, EOFError, TimeoutError) as exc:
        command_error = f'{type(exc).__name__}: {exc}'
    code = r.wait_exit(30)
    text = r.stderr()
    assert code == 1 and text.count(diagnostic) == 1
    assert 'load of migration failed' in text.lower()
    assert r.count == before and r.count['memory'] == r.count['cont'] == 0
    assert not any(x['action'] == 'raise' for x in r.irq_events)
    return {'preload': pre, 'incoming_command_error': command_error,
            'exit_code': code, 'specific_diagnostic': diagnostic,
            'specific_diagnostic_observed': True,
            'counters_before_incoming': before, 'counters_after_exit': dict(r.count),
            'no_device_or_ram_commands': True, 'no_cont': True,
            'failed_process_postload_qom_claimed': False,
            'whole_machine_ram_not_loaded_claimed': False,
            'intercepted_rejected_instance': instance,
            'no_other_device_restore_claimed': False,
            'stderr_sha256': sha(r.directory / 'qemu.stderr')}


def mutation_specs():
    rows = [('guard-' + name, 'on', offset, size, 'flip-one-bit', None, 'profile')
            for name, offset, size in GUARD_FIELD_OFFSETS]
    for index, (offset, mask) in enumerate(zip(REGISTER_ORDER, REGISTER_MASKS)):
        if mask != 0xffffffff:
            lowest_forbidden = ((~mask) & 0xffffffff) & -((~mask) & 0xffffffff)
            rows.append((f'state-mask-{offset:03x}', 'on', 64 + 4 * index, 4,
                         'or-forbidden-bit', lowest_forbidden, 'state'))
    for offset in (TX_PACK0, TX_PACK1, RX_PACK0, RX_PACK1):
        rows.append((f'state-format-{offset:03x}', 'on', 64 + 4 * REGISTER_ORDER.index(offset),
                     4, 'replace', 1, 'state'))
    rows += [('state-fifo-count', 'on', 276, 4, 'replace', 17, 'state'),
             ('state-fifo-tail', 'on', 212 + 15 * 4, 4, 'replace', 1, 'state'),
             ('state-reset-byte', 'on', 280, 1, 'replace', 2, 'state'),
             ('state-reset-functional', 'on', 64 + 4 * REGISTER_ORDER.index(RX_LEN),
              4, 'replace', 1, 'state')]
    rows += [('state-period-' + name, 'on', 281 + 8 * i, 8, 'flip-one-bit', None, 'state')
             for i, name in enumerate(('se', 'm-ahb', 's-ahb'))]
    rows += [('state-disabled-functional', 'off', 64 + 4 * REGISTER_ORDER.index(RX_LEN),
              4, 'replace', 1, 'state')]
    assert len(rows) == 55 and len({x[0] for x in rows}) == 55
    return [(*row, 9 if row[0] == 'state-reset-functional' else 1) for row in rows]


def mutate_stream(source_path, source, output_path, spec):
    name, mode, offset, size, operation, argument, kind, instance = spec
    original = source_path.read_bytes()
    changed = bytearray(original)
    section = next(s for s in source['sections'] if s['profile_instance'] == instance)
    if name == 'state-reset-functional':
        assert source['snapshot'][str(instance)]['reset-held'] is True
        assert not any(register_values(source['snapshot'][str(instance)]).values())
    absolute = section['payload_offset'] + offset
    old = int.from_bytes(original[absolute:absolute + size], 'big')
    new = old ^ 1 if operation == 'flip-one-bit' else old | argument if operation == 'or-forbidden-bit' else argument
    assert new != old
    changed[absolute:absolute + size] = new.to_bytes(size, 'big')
    differing = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
    assert differing and all(absolute <= i < absolute + size for i in differing)
    output_path.write_bytes(changed)
    assert source_path.read_bytes() == original
    return {'name': name, 'mode': mode, 'profile_instance': instance,
            'section_instance_id': section['instance_id'],
            'field_offset': offset, 'absolute_offset': absolute,
            'field_bytes': size, 'old_value': old, 'new_value': new,
            'changed_byte_offsets': differing, 'source_file': source_path.name,
            'file': output_path.name, 'bytes': len(changed),
            'sha256': sha(output_path), 'source_sha256': sha(source_path),
            'source_unchanged': True, 'expected_error_class': kind,
            'other_device_and_machine_bytes_unchanged': True}


NORMAL_CASES = (
    ('default-off', None, False, 'qtest', False, case_off),
    ('explicit-off', False, False, 'qtest', False, case_off),
    ('paused-tcg-observer', True, False, 'tcg', False, mappings),
    ('cold-nine-instances', True, True, 'qtest', False, case_cold_nine),
    ('fifo-address-nack', True, True, 'qtest', False, case_fifo_nack),
    ('dma-address-nack', True, True, 'qtest', False, case_dma_nack),
    ('fifo-capacity-control', True, True, 'qtest', False, case_fifo_capacity_and_controls),
    ('unsupported-commands', True, True, 'qtest', False, case_unsupported_commands),
    ('irq-mask-se-clock', True, True, 'qtest', False, case_irq_masks_and_clocks),
    ('strict-access', True, True, 'qtest', False, case_strict_access),
    ('ahb-clock-gates', True, True, 'qtest', False, case_ahb_gating),
    ('shared-wrapper-reset', True, True, 'qtest', True, case_shared_reset),
)


def expected_case_names():
    names = [x[0] for x in NORMAL_CASES]
    names += ['migration-source-' + mode for mode in ('off',)]
    names += ['migration-target-off', 'migration-return-off',
              'migration-source-on', 'migration-target-on', 'migration-return-on',
              'migration-source-on-cold', 'off-to-on', 'on-to-off']
    names += [row[0] for row in mutation_specs()]
    assert len(names) == 76 and len(set(names)) == 76
    return names


def run_cases(args, out, report):
    streams = out / 'streams'
    streams.mkdir()
    expected_cleanup = {}

    def fixture(name, callback, *, enabled=True, incoming=False, running=False,
                accel='qtest', hs=False):
        assert name not in expected_cleanup
        expected_cleanup[name] = 0
        r = I2CRig(args, out / name, enabled=enabled, hs=hs, incoming=incoming, accel=accel)
        try:
            report['qemu_executed'] = True
            pure = pure_twice(r, 'preload-pure' if incoming else 'initial-pure')
            status = r.monitor('query-status')
            assert status['running'] is False and r.count['memory'] == r.count['cont'] == 0
            continuation = r.start_qtest() if running else None
            detail = callback(r, pure)
            row = {'name': name, 'passed': True, 'fixture': name,
                   'enabled': enabled is True, 'accel': accel, 'hs_reference': hs,
                   'initial_pure': pure, 'initial_query_status': status,
                   'continuation': continuation, 'details': detail}
            report['checks'].append(row)
            return detail
        finally:
            expected_cleanup[name] = r.expected_cont
            r.close()

    for name, enabled, running, accel, hs, function in NORMAL_CASES:
        fixture(name, lambda r, p, function=function: function(r),
                enabled=enabled, running=running, accel=accel, hs=hs)

    saved = {}
    for mode, enabled in (('off', False), ('on', True)):
        source_path = streams / (mode + '-forward.mig')
        return_path = streams / (mode + '-return.mig')

        def source_case(r, pure):
            witness = seed_migration_state(r) if enabled else None
            source = save_stream(r, source_path)
            return {'source': source, 'witness': witness}

        seed = fixture('migration-source-' + mode, source_case,
                       enabled=enabled, running=enabled)
        saved[mode] = (source_path, seed['source'])

        def target_case(r, pure):
            observation = load_stream(r, source_path, seed['source'], seed['witness'], pure)
            back = save_stream(r, return_path)
            assert back['snapshot'] == seed['source']['snapshot']
            assert [x['payload_hex'] for x in back['sections']] == [
                x['payload_hex'] for x in seed['source']['sections']]
            report['migrations'].append({'source_file': source_path.name,
                                         'source_sha256': sha(source_path),
                                         'target_fixture': 'migration-target-' + mode,
                                         'observation': observation})
            return {'observation': observation, 'return_stream': back}

        target = fixture('migration-target-' + mode, target_case,
                         enabled=enabled, incoming=True)

        def return_case(r, pure):
            observation = load_stream(r, return_path, target['return_stream'], seed['witness'], pure)
            final = world(r)
            assert final == seed['source']['snapshot']
            report['migrations'].append({'source_file': return_path.name,
                                         'source_sha256': sha(return_path),
                                         'target_fixture': 'migration-return-' + mode,
                                         'observation': observation})
            return {'observation': observation, 'final': final}

        returned = fixture('migration-return-' + mode, return_case,
                           enabled=enabled, incoming=True)
        report['roundtrips'].append({'mode': mode, 'source': seed['source'],
                                     'return_stream': target['return_stream'],
                                     'final': returned['final'],
                                     'full_nine_section_roundtrip': True,
                                     'mutable_fields_and_provider_periods_preserved': True})

    # Cross-mode rejection uses a genuinely saved cold enabled source, with no
    # MMIO/cont and all clock outputs zero. This isolates the enabled guard from
    # pending states in the mixed source and from old consumers' clocks.
    cold_path = streams / 'on-cold.mig'
    cold = fixture('migration-source-on-cold', lambda r, p: save_stream(r, cold_path))
    saved['on-cold'] = (cold_path, cold)
    for name, source_mode, target_enabled in (('off-to-on', 'off', True),
                                               ('on-to-off', 'on-cold', False)):
        path, source = saved[source_mode]
        diagnostic = TYPE + ': invalid migration profile'
        detail = fixture(name, lambda r, p, path=path: reject_stream(r, path, diagnostic, p),
                         enabled=target_enabled, incoming=True)
        report['rejected_migrations'].append({'name': name, 'kind': 'cross-profile',
                                              'source_file': path.name,
                                              'source_sha256': sha(path), **detail})

    for spec in mutation_specs():
        name, mode, offset, size, operation, argument, kind, instance = spec
        original_path, source = saved[mode]
        changed_path = streams / (name + '.mig')
        mutation = mutate_stream(original_path, source, changed_path, spec)
        diagnostic = TYPE + ': invalid migration ' + kind
        detail = fixture(name, lambda r, p, path=changed_path, diagnostic=diagnostic, instance=instance:
                         reject_stream(r, path, diagnostic, p, instance),
                         enabled=mode == 'on', incoming=True)
        report['rejected_migrations'].append({'name': name, 'kind': kind,
                                              'mutation': mutation, **detail})

    report['processes'] = verify_i2c_cleanup(out, expected_cleanup)
    negative_names = {x['name'] for x in report['rejected_migrations']}
    for process in report['processes']:
        if process['fixture'] in negative_names:
            assert process['exit_code'] == 1 and not any(
                event['action'] == 'raise' for event in process['irq_events'])
        else:
            assert process['exit_code'] == 0
    assert [x['name'] for x in report['checks']] == expected_case_names()
    assert len(report['processes']) == 76
    assert len(report['roundtrips']) == 2 and len(report['migrations']) == 4
    assert len(report['rejected_migrations']) == 57
    report['actual_stream_files'] = [
        {'file': str(p.relative_to(out)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
        for p in sorted(streams.glob('*.mig'))]
    assert len(report['actual_stream_files']) == 60
    assert {x['fixture'] for x in report['processes']} == set(expected_case_names())
    report['expected_cont_by_fixture'] = expected_cleanup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qemu', required=True)
    parser.add_argument('--machine', default=DEFAULT_MACHINE)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    qemu = Path(args.qemu).resolve()
    report = {
        'schema': 'tb371fc-geni-i2c-compiled-reference-v1', 'passed': False,
        'expected_checks': 76, 'expected_processes': 76,
        'scope': 'clocked-empty-i2c-bus-address-nack',
        'checks': [], 'processes': [], 'roundtrips': [], 'migrations': [],
        'rejected_migrations': [], 'actual_stream_files': [],
        'machine': args.machine,
        'binary': {'path': str(qemu), 'bytes': qemu.stat().st_size, 'sha256': sha(qemu)},
        'source_profile': {
            'name': PROFILE, 'identity': PROFILE_ID, 'protocol': 3,
            'instances': [x[0] for x in INSTANCES], 'instance_count': 9,
            'window_bytes': WINDOW_BYTES, 'fifo_words': FIFO_WORDS,
            'fifo_word_bits': FIFO_WORD_BITS, 'ios_idle': IOS_IDLE,
            'attached_slaves': 0, 'vmstate_bytes_per_instance': VMSTATE_BYTES,
            'profile_bytes_per_instance': GUARD_BYTES, 'pure_getters_per_instance': len(GETTERS),
            'clocked_address_phase_nack': NACK, 'dma_reset_done': DMA_RESET_DONE,
            'dma_data_done': DMA_DONE,
        },
        'qemu_executed': False, 'actual_guest_cpu_or_driver_executed': False,
        'physical_values_captured': False, 'firmware_executed': False,
        'attached_original_dt_peripherals': False, 'successful_i2c_payload_modeled': False,
        'dma_data_modeled': False, 'native_executed': False, 'android_success_claimed': False,
        'arbitrary_bulk_buffer_atomicity_claimed': False,
        'canary_unchanged_proves_no_dma_reads': False,
        'qtest_ok_is_memtx_result': False,
        'test_oracle_reads_device_implementation': False,
        'whole_machine_ram_not_loaded_claimed': False,
        'automatic_retries': 0, 'process_cleanup_verified': False,
        'binary_unchanged': False,
    }
    here = Path(__file__).resolve().parent
    report['test_source_files'] = [
        {'path': 'tb371fc/tests/' + name, 'bytes': (here / name).stat().st_size,
         'sha256': sha(here / name)}
        for name in ('test_geni_i2c_sm8250.py', 'geni_i2c_test_contract.py',
                     'geni_i2c_test_transport.py', 'qce_bam_test_transport.py')]
    code = 1
    try:
        run_cases(args, out, report)
        report['process_cleanup_verified'] = True
        report['qemu_sha256_after'] = sha(qemu)
        assert report['qemu_sha256_after'] == report['binary']['sha256']
        report['binary_unchanged'] = True
        report['passed'] = True
        code = 0
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        report['traceback'] = traceback.format_exc()
        report['partial_cleanup_files'] = [str(p.relative_to(out)) for p in sorted(out.glob('*/cleanup.json'))]
    finally:
        put_json(out / 'result.json', report)
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                      'processes': len(report['processes']),
                      'error': report.get('error')}, sort_keys=True))
    return code


if __name__ == '__main__':
    sys.exit(main())
