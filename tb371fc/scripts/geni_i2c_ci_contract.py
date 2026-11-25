#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict report contract for the separately clocked virtual empty I2C buses.

This reader does not import the QEMU device implementation or the test runner.
Raw protocol, complete migration files and full process logs remain required
artifact members for the later independent artifact qualification reader.
"""
import hashlib
import re
import struct

NAME = 'geni-i2c-reference'
TYPE = 'qcom-sm8250-geni-i2c'
SCHEMA = 'tb371fc-geni-i2c-compiled-reference-v1'
INSTANCES = ((1, 0x984000, 602, 0), (3, 0x98c000, 604, 0),
             (5, 0x994000, 606, 0), (9, 0xa84000, 354, 1),
             (11, 0xa8c000, 356, 1), (13, 0xa94000, 358, 1),
             (15, 0x884000, 583, 2), (16, 0x888000, 584, 2),
             (19, 0x894000, 587, 2))
REGS = (0x20, 0x24, 0x28, 0x48, 0x4c, 0x7c, 0x254, 0x258, 0x260, 0x264,
        0x26c, 0x270, 0x278, 0x284, 0x288, 0x600, 0x610, 0x614, 0x644,
        0x80c, 0x810, 0x814, 0xc30, 0xc34, 0xc38, 0xc3c, 0xc40, 0xc48,
        0xd30, 0xd34, 0xd38, 0xd3c, 0xd40, 0xd48, 0xe18, 0xe1c, 0xe30)
MASKS = (1, 0x7f, 0x37f, 0xfff1, 0xfff1, 7, 0, 1, 0xfffff, 0xfffff,
         0xffffffff, 0xffffffff, 0x3fffffff, 0xfffff, 0xfffff, 0xffffffff,
         0x43c, 0xffc07fff, 0xfc07f3f, 0x3f, 0x3f, 0x3f,
         0xffffffff, 0xffffffff, 1, 0xffffffff, 8, 0x400f,
         0xffffffff, 0xffffffff, 0, 0xffffffff, 8, 0x77ff, 0, 15, 0x1ff)
UINTS = ('reference-identity', 'instance-id-readonly', 'physical-base', 'window-bytes',
         'interrupt-spi', 'wrapper-index', 'main-fw-revision', 'secondary-fw-revision',
         'main-config-revision', 'secondary-config-revision', 'fifo-words', 'fifo-word-bits',
         'ios-idle', 'tx-count', 'se-clock-period', 'm-ahb-clock-period', 's-ahb-clock-period',
         'vmstate-bytes', 'profile-wire-bytes', 'attached-slaves', 'tx-payload-bytes',
         'rx-payload-bytes', 'dma-read-bytes', 'dma-write-bytes')
BOOLS = ('enabled-reference', 'irq-level', 'reset-held', 'bus-available', 'engine-available',
         'command-active', 'firmware-executed', 'physical-state-captured',
         'dma-data-modeled', 'address-phase-modeled')
STRINGS = ('profile', 'registers-hex', 'tx-fifo-hex')
GETTERS = UINTS + ('instance-id',) + BOOLS + STRINGS
COUNTERS = {'qom_get', 'qom_set', 'qtest', 'memory', 'device_mmio', 'ram', 'cont', 'gcc_mmio'}
NORMAL = ('default-off', 'explicit-off', 'paused-tcg-observer', 'cold-nine-instances',
          'fifo-address-nack', 'dma-address-nack', 'fifo-capacity-control',
          'unsupported-commands', 'irq-mask-se-clock', 'strict-access', 'ahb-clock-gates',
          'shared-wrapper-reset')
MIGRATION = ('migration-source-off', 'migration-target-off', 'migration-return-off',
             'migration-source-on', 'migration-target-on', 'migration-return-on',
             'migration-source-on-cold')
GUARDS = (('enabled', 0, 4), ('identity', 4, 4), ('instance', 8, 4), ('base', 12, 8),
          ('size', 20, 4), ('spi', 24, 4), ('wrapper', 28, 4), ('main-fw', 32, 4),
          ('secondary-fw', 36, 4), ('main-config', 40, 4), ('secondary-config', 44, 4),
          ('fifo-words', 48, 4), ('fifo-bits', 52, 4), ('ios', 56, 4), ('slave-count', 60, 4))
MUTATIONS = tuple('guard-' + x[0] for x in GUARDS)
MUTATIONS += tuple(f'state-mask-{o:03x}' for o, m in zip(REGS, MASKS) if m != 0xffffffff)
MUTATIONS += tuple(f'state-format-{o:03x}' for o in (0x260, 0x264, 0x284, 0x288))
MUTATIONS += ('state-fifo-count', 'state-fifo-tail', 'state-reset-byte', 'state-reset-functional',
              'state-period-se', 'state-period-m-ahb', 'state-period-s-ahb', 'state-disabled-functional')
NEGATIVE = ('off-to-on', 'on-to-off') + MUTATIONS
CHECKS = NORMAL + MIGRATION + NEGATIVE
RUNNING = frozenset(NORMAL[3:] + ('migration-source-on', 'migration-target-on', 'migration-return-on'))
SOURCE_PROFILE = {'name': 'empty-bus-v1', 'identity': 0x47493231, 'protocol': 3,
                  'instances': [x[0] for x in INSTANCES], 'instance_count': 9,
                  'window_bytes': 0x4000, 'fifo_words': 16, 'fifo_word_bits': 32,
                  'ios_idle': 3, 'attached_slaves': 0, 'vmstate_bytes_per_instance': 305,
                  'profile_bytes_per_instance': 64, 'pure_getters_per_instance': 38,
                  'clocked_address_phase_nack': 0x408, 'dma_reset_done': 8, 'dma_data_done': 1}
FALSE_FIELDS = ('actual_guest_cpu_or_driver_executed', 'physical_values_captured',
                'firmware_executed', 'attached_original_dt_peripherals',
                'successful_i2c_payload_modeled', 'dma_data_modeled', 'native_executed',
                'android_success_claimed', 'arbitrary_bulk_buffer_atomicity_claimed',
                'canary_unchanged_proves_no_dma_reads', 'qtest_ok_is_memtx_result',
                'test_oracle_reads_device_implementation', 'whole_machine_ram_not_loaded_claimed')
EXPECTED_VALUES = {'schema': SCHEMA, 'expected_checks': 76, 'expected_processes': 76,
                   'scope': 'clocked-empty-i2c-bus-address-nack', 'source_profile': SOURCE_PROFILE,
                   'qemu_executed': True, 'automatic_retries': 0,
                   'process_cleanup_verified': True, 'binary_unchanged': True,
                   **{field: False for field in FALSE_FIELDS}}
EXPECTED_LENGTHS = {'processes': 76, 'roundtrips': 2, 'migrations': 4,
                    'rejected_migrations': 57, 'actual_stream_files': 60}


def require(value, label):
    if not value:
        raise ValueError('GENI I2C contract: ' + label)


def same(actual, expected, label):
    require(type(actual) is type(expected), label + ' type')
    if isinstance(expected, dict):
        require(actual.keys() == expected.keys(), label + ' keys')
        for key in expected:
            same(actual[key], expected[key], label + '.' + key)
    elif isinstance(expected, (list, tuple)):
        require(len(actual) == len(expected), label + ' length')
        for index, (a, b) in enumerate(zip(actual, expected)):
            same(a, b, f'{label}[{index}]')
    else:
        require(actual == expected, label)


def digest(value):
    require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'full SHA256')


def counters(value):
    require(type(value) is dict and set(value) == COUNTERS, 'complete counters')
    require(all(type(v) is int and v >= 0 for v in value.values()), 'counter integers')
    require(value['memory'] >= value['device_mmio'] + value['gcc_mmio'] + value['ram'], 'memory partition')


def state(value, number, enabled):
    require(type(value) is dict and set(value) == set(GETTERS), 'all 38 instance getters')
    for field in UINTS + ('instance-id',):
        require(type(value[field]) is int and 0 <= value[field] < 1 << 64, 'uint getter ' + field)
    for field in BOOLS:
        require(type(value[field]) is bool, 'bool getter ' + field)
    require(all(type(value[f]) is str for f in STRINGS), 'string getters')
    _, base, spi, wrapper = next(x for x in INSTANCES if x[0] == number)
    identity = {'profile': 'empty-bus-v1' if enabled else 'disabled', 'enabled-reference': enabled,
                'reference-identity': 0x47493231, 'instance-id': number, 'instance-id-readonly': number,
                'physical-base': base, 'window-bytes': 0x4000, 'interrupt-spi': spi,
                'wrapper-index': wrapper, 'main-fw-revision': 0x300, 'secondary-fw-revision': 0x300,
                'main-config-revision': 0, 'secondary-config-revision': 0, 'fifo-words': 16,
                'fifo-word-bits': 32, 'ios-idle': 3, 'vmstate-bytes': 305, 'profile-wire-bytes': 64,
                'attached-slaves': 0, 'tx-payload-bytes': 0, 'rx-payload-bytes': 0,
                'dma-read-bytes': 0, 'dma-write-bytes': 0, 'command-active': False,
                'firmware-executed': False, 'physical-state-captured': False,
                'dma-data-modeled': False, 'address-phase-modeled': True}
    same({k: value[k] for k in identity}, identity, 'immutable identity')
    require(re.fullmatch('[0-9a-f]{296}', value['registers-hex']), 'complete compact registers')
    require(re.fullmatch('[0-9a-f]{128}', value['tx-fifo-hex']), 'complete 16-word FIFO')
    reg = dict(zip(REGS, struct.unpack('>37I', bytes.fromhex(value['registers-hex']))))
    require(all(not (reg[o] & ~m) for o, m in zip(REGS, MASKS)), 'every register mask')
    for offset, allowed in ((0x260, 0x7f8fe), (0x264, 0xffefe), (0x284, 0x7f8fe), (0x288, 0xffefe)):
        require(reg[offset] in (0, allowed), 'canonical packing')
    count = value['tx-count']
    require(count <= 16, 'bounded FIFO count')
    fifo = struct.unpack('>16I', bytes.fromhex(value['tx-fifo-hex']))
    require(not any(fifo[count:]), 'canonical unused FIFO tail')
    bus = enabled and not value['reset-held'] and value['m-ahb-clock-period'] != 0 and value['s-ahb-clock-period'] != 0
    engine = bus and value['se-clock-period'] != 0
    same(value['bus-available'], bus, 'bus availability')
    same(value['engine-available'], engine, 'engine availability')
    irq = bool(engine and ((reg[0xe1c] & 4 and reg[0x610] & reg[0x614]) or
                           (reg[0xe1c] & 2 and reg[0xc40] & reg[0xc48]) or
                           (reg[0xe1c] & 1 and reg[0xd40] & reg[0xd48])))
    same(value['irq-level'], irq, 'derived interrupt level')
    if not enabled or value['reset-held']:
        require(not any(reg.values()) and count == 0 and not any(fifo), 'disabled/reset functional zero')
    return reg


def world(value, enabled):
    require(type(value) is dict and set(value) == {str(x[0]) for x in INSTANCES}, 'all nine instances')
    return {key: state(value[key], int(key), enabled) for key in value}


def pure(value, enabled, initial=False):
    world(value['first'], enabled);world(value['second'], enabled)
    same(value['first'], value['second'], 'two equal pure worlds')
    require(value['no_memory_or_cont'] is True, 'pure observation boundary')
    before, after = value['counters_before'], value['counters_after']
    counters(before);counters(after)
    for field in COUNTERS:
        same(after[field], before[field] + (684 if field == 'qom_get' else 0), 'pure counter delta')
    same(before['memory'], 0, 'no preceding memory')
    same(before['cont'], 0, 'no preceding cont')
    if initial:
        require(not any(before.values()), 'pure getters first after capabilities')


def mapping(value, enabled):
    expected = sorted([[base, base + 0x3fff, TYPE] for _, base, _, _ in INSTANCES]) if enabled else []
    same(value['mapped_instances'], expected, 'all exact ranges and full leaf names')
    same(value['mapped_bytes'], 9 * 0x4000 if enabled else 0, 'mapped bytes')


def trace(value, operations, number=1):
    require(type(value) is list and len(value) == len(operations), 'complete source operation sequence')
    base = next(x[1] for x in INSTANCES if x[0] == number)
    for row, (direction, offset, operand) in zip(value, operations):
        same(row['operation'], direction, 'operation direction')
        same(row['offset'], offset, 'operation offset');same(row['value'], operand, 'operation operand')
        command = ('readl' if direction == 'read' else 'writel') + f' {base + offset:#x}'
        if direction == 'write':
            command += f' {operand:#x}'
        same(row['command'], command, 'exact qtest command')
        require(type(row['source_step']) is str and row['source_step'], 'source step')
        if direction == 'read':
            require(len(row['reply']) == 1 and int(row['reply'][0], 0) == operand, 'actual read reply')
        else:
            same(row['reply'], [], 'write transport reply')


def cold_ops():
    return [('read', 0x68, 0x300), ('read', 0x64, 0), ('read', 0xe24, 0x20100000),
            ('read', 0x28, 0), ('read', 0xe30, 0), ('write', 0x28, 0x7f),
            ('write', 0xe30, 15), ('write', 0x24, 0x7f), ('write', 0x20, 1),
            ('read', 0xe1c, 0), ('read', 0x258, 0), ('write', 0xe1c, 15),
            ('write', 0x258, 0), ('write', 0xe18, 0), ('write', 0x810, 15),
            ('write', 0x814, 16), ('read', 0x614, 0), ('read', 0x644, 0),
            ('write', 0x614, 0x33c0007e), ('write', 0x644, 0x03003e3e),
            ('write', 0x260, 0x7f8fe), ('write', 0x264, 0xffefe),
            ('write', 0x284, 0x7f8fe), ('write', 0x288, 0xffefe),
            ('write', 0x254, 0), ('write', 0x7c, 0),
            ('write', 0x48, 0x21), ('write', 0x278, 0x503018)]


def initialization(value):
    same(set(value['cold_traces']), {str(x[0]) for x in INSTANCES}, 'nine cold traces')
    for number, *_ in INSTANCES:
        trace(value['cold_traces'][str(number)], cold_ops(), number)
    require(len(value['clock_changes']) == 2, 'two complete vote RMW groups')
    for row, offset in zip(value['clock_changes'], (0x52008, 0x52010)):
        same(row['offset'], offset, 'vote offset')
        require(type(row['mask']) is int and row['mask'] > 0 and row['after'] == row['before'] | row['mask'], 'vote RMW')


def access_rejection(value):
    require(value['state_unchanged'] is True and value['qtest_ok_is_memtx_result'] is False, 'atomic rejection boundary')
    world(value['before'], True);world(value['after'], True)
    same(value['before'], value['after'], 'all nine states unchanged')
    command = value['command'].split()
    require(command[0] in ('readb', 'readw', 'readl', 'readq', 'writeb', 'writew', 'writel', 'writeq'), 'typed transaction')
    address = int(command[1], 0)
    number, base, _, _ = next(x for x in INSTANCES if x[1] <= address < x[1] + 0x4000)
    direction = 'read' if command[0].startswith('read') else 'write'
    size = {'b': 1, 'w': 2, 'l': 4, 'q': 8}[command[0][-1]]
    prefix = TYPE + f': rejected {direction} instance={number} offset={address - base:#x} size={size} reason='
    require(value['expected_diagnostic'].startswith(prefix), 'exact original width/alignment diagnostic')


def rejection_contract(value, instance, offset, operand, width, reason):
    access_rejection(value)
    base = next(x[1] for x in INSTANCES if x[0] == instance)
    direction = 'read' if operand is None else 'write'
    command = f'{direction}{width} {base + offset:#x}' + (f' {operand:#x}' if operand is not None else '')
    same(value['command'], command, 'exact rejected operation')
    size = {'b': 1, 'w': 2, 'l': 4, 'q': 8}[width]
    same(value['expected_diagnostic'], TYPE + f': rejected {direction} instance={instance} '
         + f'offset={offset:#x} size={size} reason={reason}', 'one exact typed rejection diagnostic')


def access_cases():
    rows = []
    for offset in (0x26c, 0x600, 0x68):
        for width in ('b', 'w', 'q'):
            rows += [(1, offset, None, width, 'access-shape'), (1, offset, 0x11223344, width, 'access-shape')]
        for delta in (1, 2, 3):
            rows += [(1, offset + delta, None, 'l', 'access-shape'),
                     (1, offset + delta, 0x11223344, 'l', 'access-shape')]
    rows += [(1, offset, 1, 'l', 'read-only-or-unknown-register') for offset in
             (0, 4, 0x40, 0x64, 0x68, 0x6c, 0x630, 0x640, 0x800, 0x804,
              0x908, 0xc54, 0xd54, 0xe24, 0xe28, 0xe40)]
    rows += [(1, offset, 1, 'l', 'read-only-status') for offset in (0x610, 0xc40, 0xd40)]
    for offset in (0x10, 0x3ffc):
        rows += [(1, offset, None, 'l', 'unknown-register'),
                 (1, offset, 1, 'l', 'read-only-or-unknown-register')]
    rows += [(1, offset, value, 'l', 'unsupported-format') for offset, value in
             ((0x254, 1), (0x260, 1), (0x264, 1), (0x284, 1), (0x288, 1),
              (0xc38, 2), (0xd38, 1), (0xe18, 1))]
    rows += [(1, offset, 2, 'l', 'unsupported-dma-reset') for offset in (0xc58, 0xd58)]
    return rows


def masked_write_cases():
    rows = [(offset, value, offset, 'replace') for offset, value in
            ((0x20, 2), (0x24, 0x80), (0x28, 0x400), (0x48, 2), (0x4c, 2),
             (0x7c, 8), (0x258, 2), (0x278, 0x40000000), (0x614, 0x8000),
             (0x644, 0x40), (0x80c, 64), (0x810, 64), (0x814, 64))]
    return rows + [(0xc4c, 0x80000000, 0xc48, 'set'), (0xd50, 0x80000000, 0xd48, 'clear'),
                   (0xe1c, 0x10, 0xe1c, 'replace'), (0xe30, 0x200, 0xe30, 'replace')]


def cleanup(rows):
    require(type(rows) is list and len(rows) == 76, '76 actual child lifecycles')
    require(len({x['fixture'] for x in rows}) == 76 and {x['fixture'] for x in rows} == set(CHECKS), 'unique process set')
    for row in rows:
        name = row['fixture'];code = int(name in NEGATIVE)
        same(row['expected_exit_code'], code, 'expected exit');same(row['exit_code'], code, 'actual exit')
        require(type(row['pid']) is int and row['pid'] > 0, 'actual owned PID')
        require(row['actually_waited_and_reaped'] is True, 'wait and reap')
        require(len(row['wait_calls']) == (2 if code else 1), 'explicit waits including fatal-load wait')
        for wait in row['wait_calls']:
            same(wait['called'], 'Popen.wait', 'wait API');same(wait['returned_exit_code'], code, 'wait return')
        for key in ('forced_terminate', 'forced_kill', 'signals_to_preexisting_processes'):
            same(row[key], False, key)
        same(row['quit_sent'], code == 0, 'QMP quit applies to live process')
        if code == 0:
            same(row['quit_acknowledged'], True, 'quit ACK');require(row['quit_error'] is None, 'no quit error')
        else:
            same(row['quit_not_applicable'], 'already-exited', 'fatal process exited before cleanup')
        counters(row['counters']);same(row['counters']['cont'], int(name in RUNNING), 'scenario cont count')
        require(row['counters']['qom_get'] >= 684, 'all first pure getters actually counted')
        same(row['unexpected_qtest_drain_lines'], [], 'complete recognized qtest drain')
        for event in row['irq_events']:
            require(set(event) == {'action', 'number'} and event['action'] in ('raise', 'lower'), 'IRQ event')
            same(event['number'], 0, 'single intercepted output index')
            if code:
                same(event['action'], 'lower', 'invalid device cannot assert, including final drain')
        if name in ('fifo-address-nack', 'dma-address-nack', 'unsupported-commands',
                    'irq-mask-se-clock', 'ahb-clock-gates', 'shared-wrapper-reset',
                    'migration-target-on', 'migration-return-on'):
            require(any(x['action'] == 'raise' for x in row['irq_events']), 'actual intercepted raise')
        if name in ('fifo-address-nack', 'dma-address-nack', 'unsupported-commands',
                    'irq-mask-se-clock', 'ahb-clock-gates', 'shared-wrapper-reset'):
            require(any(x['action'] == 'lower' for x in row['irq_events']), 'actual intercepted lower')
        if name in ('default-off', 'explicit-off'):
            same(row['counters']['memory'], 18, 'off reads and writes count')
            same(row['counters']['device_mmio'], 18, 'off window command count')
        if name in ('paused-tcg-observer', 'migration-source-off', 'migration-source-on-cold') or code:
            same(row['counters']['memory'], 0, 'no memory on pure/source or failed-load fixture')
        for channel in ('qmp', 'qtest'):
            item = row['drains'][channel]
            unopened = name == 'paused-tcg-observer' and channel == 'qtest'
            same(bool(item.get('not_opened')), unopened, 'only TCG qtest channel is unopened')
            if not unopened:
                require(item['terminal'] in (('eof', 'reset') if code else ('eof',)), 'real channel terminal')
                require(item['error'] is None, 'no drain error')
                require(type(item['drain_hex']) is str and len(bytes.fromhex(item['drain_hex'])) == item['drain_bytes'], 'whole drain bytes')
        for stream in ('qemu.stdout', 'qemu.stderr'):
            item = row['stdio'][stream]
            require(item['complete_after_actual_wait'] is True and type(item['bytes']) is int and item['bytes'] >= 0, 'full waited stdio')
            digest(item['sha256'])


def wire(number, enabled, value):
    _, base, spi, wrapper = next(x for x in INSTANCES if x[0] == number)
    raw = struct.pack('>IIIQ11I', int(enabled), 0x47493231, number, base, 0x4000,
                      spi, wrapper, 0x300, 0x300, 0, 0, 16, 32, 3, 0)
    raw += bytes.fromhex(value['registers-hex']) + bytes.fromhex(value['tx-fifo-hex'])
    raw += struct.pack('>IB3Q', value['tx-count'], value['reset-held'], value['se-clock-period'],
                       value['m-ahb-clock-period'], value['s-ahb-clock-period'])
    return raw


def saved_stream(value, enabled):
    world(value['snapshot'], enabled)
    same(value['all_nine_payload_bytes'], 2745, 'all nine full payloads')
    same(value['whole_machine_payload_oracle_claimed'], False, 'device-local payload scope')
    require(type(value['bytes']) is int and value['bytes'] > 2745, 'complete machine stream size')
    digest(value['sha256'])
    same(value['query_migrate']['status'], 'completed', 'save completed')
    same(value['query_status']['status'], 'postmigrate', 'source stopped after save')
    same(value['query_status']['running'], False, 'source not running')
    require(len(value['sections']) == 9, 'all sections')
    seen = set();previous = 0
    for index, ((number, *_), row) in enumerate(zip(INSTANCES, value['sections'])):
        same(row['section_name'], TYPE, 'exact unprefixed migration idstr')
        same(row['section_type'], 4, 'FULL section');same(row['instance_id'], index, 'realize-order instance ID')
        same(row['profile_instance'], number, 'original SE instance ID');same(row['version'], 1, 'VMState version')
        same(row['payload_bytes'], 305, 'complete body');same(row['profile_bytes'], 64, 'profile before mutable state')
        require(type(row['section_id']) is int and row['section_id'] not in seen, 'unique section ID')
        seen.add(row['section_id'])
        require(type(row['payload_offset']) is int and row['payload_offset'] > previous, 'ordered nonoverlapping sections')
        previous = row['payload_offset'] + 309
        expected = wire(number, enabled, value['snapshot'][str(number)])
        same(row['payload_hex'], expected.hex(), 'all 305 bytes, not selected fields')
        same(row['payload_sha256'], hashlib.sha256(expected).hexdigest(), 'full payload digest')
        same(row['complete_payload_verified'], True, 'full payload verified')


def loaded(value, source, enabled):
    pure(value['preload'], enabled, True);pure(value['first_postload'], enabled)
    same(value['first_postload']['first'], source['snapshot'], 'complete restored world')
    same(value['query_migrate']['status'], 'completed', 'incoming completed')
    same(value['query_status']['running'], False, 'incoming paused')
    same(value['no_memory_or_cont_before_two_postload_worlds'], True, 'first postload observation is pure')
    same(value['whole_machine_ram_not_loaded_claimed'], False, 'whole-machine RAM boundary')
    same(value['restored_irq_before_cont'], source['snapshot']['1']['irq-level'], 'validated restored output level')
    events = value['actual_irq_events_before_cont']
    require(all(x['action'] in ('raise', 'lower') and x['number'] == 0 for x in events), 'restored output events')
    if source['snapshot']['1']['irq-level']:
        require(any(x['action'] == 'raise' for x in events), 'actual restore raise before cont')
    else:
        require(not any(x['action'] == 'raise' for x in events), 'no invented restore raise')
    if enabled:
        same(value['continuation'], {'accelerator': 'qtest', 'running': True,
                                    'guest_cpu_executed': False, 'cont_commands': 1}, 'continue device callbacks')
        digest(value['loaded_ram_witness_sha256'])


def rejected(value, enabled, kind, instance):
    pure(value['preload'], enabled, True)
    same(value['exit_code'], 1, 'rejected load exits')
    same(value['specific_diagnostic'], TYPE + ': invalid migration ' + kind, 'exact local rejection class')
    same(value['specific_diagnostic_observed'], True, 'observed rejection diagnostic')
    same(value['counters_before_incoming'], value['counters_after_exit'], 'no postload commands on failed process')
    counters(value['counters_after_exit'])
    for field in ('memory', 'device_mmio', 'ram', 'cont'):
        same(value['counters_after_exit'][field], 0, 'no failed-load ' + field)
    for field in ('no_device_or_ram_commands', 'no_cont'):
        same(value[field], True, field)
    for field in ('failed_process_postload_qom_claimed', 'whole_machine_ram_not_loaded_claimed', 'no_other_device_restore_claimed'):
        same(value[field], False, 'device-local fatal rejection scope')
    same(value['intercepted_rejected_instance'], instance, 'intercept failed device, not earlier valid device')
    digest(value['stderr_sha256'])


def mutation_rule(name):
    if name.startswith('guard-'):
        _, offset, size = next(x for x in GUARDS if 'guard-' + x[0] == name)
        return 'on', 1, offset, size, 'flip', None
    if name.startswith('state-mask-'):
        offset = int(name.removeprefix('state-mask-'), 16)
        mask = MASKS[REGS.index(offset)];bits = (~mask) & 0xffffffff
        return 'on', 1, 64 + 4 * REGS.index(offset), 4, 'or', bits & -bits
    if name.startswith('state-format-'):
        offset = int(name.removeprefix('state-format-'), 16)
        return 'on', 1, 64 + 4 * REGS.index(offset), 4, 'replace', 1
    if name.startswith('state-period-'):
        offset = {'state-period-se': 281, 'state-period-m-ahb': 289, 'state-period-s-ahb': 297}[name]
        return 'on', 1, offset, 8, 'flip', None
    return {
        'state-fifo-count': ('on', 1, 276, 4, 'replace', 17),
        'state-fifo-tail': ('on', 1, 272, 4, 'replace', 1),
        'state-reset-byte': ('on', 1, 280, 1, 'replace', 2),
        'state-reset-functional': ('on', 9, 108, 4, 'replace', 1),
        'state-disabled-functional': ('off', 1, 108, 4, 'replace', 1),
    }[name]


def judge_geni_i2c_details(case, data):
    if case.name != NAME:
        return
    for key in ('error', 'failure', 'traceback', 'blocked', 'cleanup_failure'):
        require(key not in data, 'unexpected ' + key)
    same(data['passed'], True, 'aggregate pass')
    for key, expected in EXPECTED_VALUES.items():
        same(data[key], expected, key)
    for key, length in EXPECTED_LENGTHS.items():
        require(type(data[key]) is list and len(data[key]) == length, 'complete ' + key)
    same([x['name'] for x in data['checks']], list(CHECKS), 'complete ordered matrix')
    by_name = {x['name']: x for x in data['checks']}
    off_names = {'default-off', 'explicit-off', 'migration-source-off',
                 'migration-target-off', 'migration-return-off', 'on-to-off', 'state-disabled-functional'}
    for row in data['checks']:
        name = row['name'];enabled = name not in off_names
        same(row['passed'], True, 'individual check pass');same(row['fixture'], name, 'fixture identity')
        same(row['enabled'], enabled, 'fixture enabled')
        same(row['accel'], 'tcg' if name == 'paused-tcg-observer' else 'qtest', 'fixture accelerator')
        same(row['hs_reference'], name == 'shared-wrapper-reset', 'only shared-reset HS override')
        pure(row['initial_pure'], enabled, True)
        same(row['initial_query_status']['running'], False, 'initial paused state')
        if name in NORMAL[3:] or name == 'migration-source-on':
            same(row['continuation'], {'accelerator': 'qtest', 'running': True,
                                      'guest_cpu_executed': False, 'cont_commands': 1}, 'explicit functional runstate')
        else:
            require(row['continuation'] is None, 'no premature continuation')
    cleanup(data['processes'])
    same(data['expected_cont_by_fixture'], {n: int(n in RUNNING) for n in CHECKS}, 'full cont plan')
    digest(data['binary']['sha256']);same(data['qemu_sha256_after'], data['binary']['sha256'], 'unchanged executed ELF')
    require(type(data['binary']['bytes']) is int and data['binary']['bytes'] > 0, 'full ELF size')
    same([x['path'] for x in data['test_source_files']], [
        'tb371fc/tests/' + name for name in ('test_geni_i2c_sm8250.py', 'geni_i2c_test_contract.py',
                                           'geni_i2c_test_transport.py', 'qce_bam_test_transport.py')], 'test and retained transport sources')
    for row in data['test_source_files']:
        require(type(row['bytes']) is int and row['bytes'] > 0, 'complete test source');digest(row['sha256'])
    for name in ('default-off', 'explicit-off'):
        detail = by_name[name]['details'];mapping(detail['mapping'], False)
        require(len(detail['rejected_unmapped_operations']) == 18, 'both off operations on nine windows')
        for row, (number, base, spi, wrapper) in zip(detail['rejected_unmapped_operations'][::2], INSTANCES):
            same(row['command'], f'readl {base + 0x68:#x}', 'off protocol read')
            same(row['core_diagnostic'], [['read', base + 0x68, 4, '(null)', 'rejected']], 'off core rejection')
        for row, (number, base, spi, wrapper) in zip(detail['rejected_unmapped_operations'][1::2], INSTANCES):
            same(row['command'], f'writel {base + 0x600:#x} 0', 'off command write')
            same(row['core_diagnostic'], [['write', base + 0x600, 4, '(null)', 'rejected']], 'off core rejection')
    mapping(by_name['paused-tcg-observer']['details'], True)
    cold = by_name['cold-nine-instances']['details'];mapping(cold['mapping'], True)
    initialization(cold['source_derived_initialization'])
    world(cold['initial'], True);world(cold['initialized'], True);world(cold['reset'], True)
    require(all(not any(bytes.fromhex(s['registers-hex'])) for s in cold['reset'].values()), 'system reset zeroes every instance')
    wanted_types = {**{k: 'uint64' for k in UINTS}, **{k: 'bool' for k in BOOLS},
                    **{k: 'string' for k in STRINGS}, 'instance-id': 'uint32'}
    same(cold['property_types'], {str(x[0]): wanted_types for x in INSTANCES}, 'declared full QOM types')
    for name in NORMAL[4:]:
        initialization(by_name[name]['details']['initialization'])
    for name, specifications in (('fifo-address-nack', [(False, 0), (True, 0), (False, 1), (True, 1), (False, 32), (True, 32)]),
                                  ('dma-address-nack', [(False, 33), (True, 33), (False, 4096), (True, 4096)])):
        detail = by_name[name]['details']
        same([(x['receive'], x['length']) for x in detail['transfers']], specifications, 'FIFO/DMA boundary matrix')
        for transfer in detail['transfers']:
            registers = world(transfer['nack_state'], True)['1']
            same(registers[0x610], 0x408, 'address NACK without DONE')
            same(registers[0xc40], 0, 'no TX data success');same(registers[0xd40], 0, 'no RX data success')
            commands = transfer['setup'];command_at = next(i for i, x in enumerate(commands) if x['offset'] == 0x600)
            same(commands[command_at]['value'] >> 27, 2 if transfer['receive'] else 1, 'address opcode')
            require(commands[command_at]['value'] & ~((31 << 27) | 0xfe04) == 0, 'bounded parameters')
            same([x['offset'] for x in transfer['irq_service'][:5]], [0x610, 0x804, 0xc40, 0xd40, 0x258], 'source IRQ read order')
            same(transfer['irq_service'][-1]['offset'], 0x618, 'W1C observed main status')
            same(transfer['irq_service'][-1]['value'], 0x408, 'clear exact NACK status')
            same(transfer['cleanup'][0]['offset'], 0x604, 'consumer cancel');same(transfer['cleanup'][0]['value'], 4, 'cancel opcode')
            if name == 'dma-address-nack':
                prep = 0xd4c if transfer['receive'] else 0xc4c
                require(command_at < next(i for i, x in enumerate(commands) if x['offset'] == prep), 'M_CMD precedes DMA preparation')
                same(commands[-1]['value'], transfer['length'], 'full DMA request length')
                reset = world(transfer['reset_state'], True)['1']
                same(reset[0xd40 if transfer['receive'] else 0xc40], 8, 'RESET_DONE is 8')
                same(reset[0xd48 if transfer['receive'] else 0xc48], 7, 'source enables data bits, not reset bit')
                same(transfer['reset_done_is_data_done'], False, 'reset is not data completion')
                same(transfer['witness_bytes'], transfer['length'], 'whole witness size')
                digest(transfer['witness_sha256'])
                same(transfer['whole_witness_after_sha256'], transfer['witness_sha256'], 'whole witness unchanged')
        if name == 'fifo-address-nack':
            same(detail['expected_consumer_error'], -107, 'ENOTCONN interpretation')
            same(detail['actual_guest_driver_executed'], False, 'qtest replay boundary')
        else:
            same(detail['no_dma_read_inference_from_canaries'], True, 'RAM cannot prove absence of reads')
            same(detail['reference_reset_wait_return_is_ignored'], True, 'real consumer wait boundary')
            same(detail['actual_guest_wait_executed'], False, 'no fabricated guest wait')
    strict = by_name['strict-access']['details']
    require(len(strict['rejections']) == 69, 'all strict MMIO rejection cases')
    for row, parameters in zip(strict['rejections'], access_cases()):
        rejection_contract(row, *parameters)
    same([x['property'] for x in strict['readonly_properties']],
         [x for x in GETTERS if x not in ('enabled-reference', 'instance-id')], 'all 36 explicit read-only properties')
    require(all(type(x['error']) is dict and x['error'] for x in strict['readonly_properties']), 'qom-set rejected')
    same(strict['typed_transaction_widths'], [1, 2, 4, 8], 'typed width scope')
    same(strict['arbitrary_bulk_buffer_atomicity_claimed'], False, 'no fragmented bulk claim')
    same(strict['all_nine_states_unchanged_per_rejection'], True, 'cross-instance atomicity')
    require(len(strict['reserved_bit_writes']) == 17, 'separate ordinary masked-write matrix')
    for row, (offset, operand, target, operation) in zip(strict['reserved_bit_writes'], masked_write_cases()):
        before = world(row['before'], True)['1'];world(row['after'], True)
        mask = MASKS[REGS.index(target)]
        same(row['command'], f'writel {0x984000 + offset:#x} {operand:#x}', 'exact masked write')
        same(row['offset'], offset, 'masked-write offset');same(row['target_register'], target, 'alias destination')
        same(row['value'], operand, 'original reserved-bit operand');same(row['mask'], mask, 'declared register mask')
        same(row['operation'], operation, 'replace/set/clear semantics');same(row['old_value'], before[target], 'actual previous register')
        accepted = operand & mask
        expected_value = accepted if operation == 'replace' else before[target] | accepted if operation == 'set' else before[target] & ~accepted
        same(row['expected_value'], expected_value, 'exact masked update')
        expected = {key: dict(value) for key, value in row['before'].items()}
        before[target] = expected_value
        expected['1']['registers-hex'] = struct.pack('>37I', *[before[o] for o in REGS]).hex()
        same(row['after'], expected, 'only declared target bits changed; other eight states unchanged')
        same(row['only_declared_mask_effect'], True, 'mask-effect evidence')
        same(row['rejection_diagnostic_added'], False, 'accepted masked write is not a rejection')
    fifo = by_name['fifo-capacity-control']['details']
    for field in ('filled', 'nack_preserved_fifo', 'cancelled', 'aborted'):
        state(fifo[field], 1, True)
    same(fifo['filled']['tx-count'], 16, 'FIFO capacity reached')
    same(fifo['filled']['tx-fifo-hex'], struct.pack('>16I', *[0xa5000000 | i for i in range(16)]).hex(), 'complete prefilled words')
    same(fifo['nack_preserved_fifo']['tx-fifo-hex'], fifo['filled']['tx-fifo-hex'], 'NACK cannot consume TX words')
    same(fifo['nack_preserved_fifo']['tx-count'], 16, 'NACK count retained')
    rejection_contract(fifo['overflow'], 1, 0x700, 0xfedcba98, 'l', 'tx-fifo-full')
    rejection_contract(fifo['empty_rx'], 1, 0x780, None, 'l', 'rx-fifo-empty')
    rejection_contract(fifo['bad_control'], 1, 0x604, 6, 'l', 'unsupported-control')
    for field, flag in (('cancelled', 0x10), ('aborted', 0x20)):
        reg = state(fifo[field], 1, True)
        require(reg[0x610] & flag, 'cancel/abort status')
        same(fifo[field]['tx-count'], 0, 'cancel/abort clears count')
        same(fifo[field]['tx-fifo-hex'], '0' * 128, 'cancel/abort clears complete FIFO')
    illegal = by_name['unsupported-commands']['details']
    commands = [x << 27 for x in (0, 5, 6, 7, 31)]
    commands += [(1 << 27) | x for x in (1, 2, 8, 16, 64, 128, 256, 1 << 16, 1 << 26)]
    same([x['command'] for x in illegal['semantic_command_errors']], commands, 'unsupported opcode/parameter matrix')
    for row in illegal['semantic_command_errors']:
        reg = world(row['state'], True)['1']
        same(reg[0x610], 12, 'ILLEGAL plus FAILURE, never DONE');same(row['status'], 12, 'semantic error result')
    same(illegal['unknown_register_memtx_errors_are_separate'], True, 'command errors remain controller semantics')
    irq = by_name['irq-mask-se-clock']['details']
    state(irq['masked_nack'], 1, True)
    same(state(irq['masked_nack'], 1, True)[0x610], 0x408, 'masked NACK retained')
    for field in ('pending_reset_irq', 'se_clock_off', 'restored'):
        world(irq[field], True)
    same(irq['pending_reset_irq'], irq['restored'], 'clock restore preserves all state')
    same(irq['se_clock_off']['1']['se-clock-period'], 0, 'SE clock actually stopped')
    same(irq['se_clock_off']['1']['bus-available'], True, 'AHB access remains available')
    same(irq['se_clock_off']['1']['registers-hex'], irq['pending_reset_irq']['1']['registers-hex'], 'gating retains all status')
    rejection_contract(irq['gated_command'], 1, 0x600, (1 << 27) | (0x28 << 9), 'l', 'engine-unavailable')
    rejection_contract(irq['gated_control'], 1, 0x604, 4, 'l', 'engine-unavailable')
    rejection_contract(irq['gated_dma_reset'], 1, 0xd58, 1, 'l', 'engine-unavailable')
    same(irq['qtest_intercepts_one_actual_sysbus_output'], True, 'actual line scope')
    same(irq['guest_isr_or_GIC_delivery_executed'], False, 'no claimed guest/GIC execution')
    ahb = by_name['ahb-clock-gates']['details']
    same([x['clock'] for x in ahb['gates']], ['m-ahb-clock-period', 's-ahb-clock-period'], 'both AHB inputs tested')
    for gate in ahb['gates']:
        world(gate['stopped'], True)
        require(all(gate['stopped'][str(n)][gate['clock']] == 0 for n in (1, 3, 5)), 'shared AHB provider stopped')
        same(gate['all_state_restored'], True, 'restoration equality')
        require(len(gate['rejections']) == 3, 'every member rejects gated accesses')
        for row, parameters in zip(gate['rejections'],
                ((1, 0x68, None, 'l', 'bus-unavailable'),
                 (3, 0x26c, 55, 'l', 'bus-unavailable'),
                 (5, 0x600, (1 << 27) | (0x28 << 9), 'l', 'bus-unavailable'))):
            rejection_contract(row, *parameters)
    same(ahb['pending_status_retained'], True, 'pending IRQ state retained')
    reset = by_name['shared-wrapper-reset']['details']
    same([x['wrapper'] for x in reset['resets']], [0, 1, 2], 'all wrapper resets')
    for row, members, legacy in zip(reset['resets'], ([1, 3, 5], [9, 11, 13], [15, 16, 19]),
                                    ('/machine/uart-hs', None, '/machine/uart')):
        same(row['members'], members, 'reset membership');same(row['preserved_legacy_destination'], legacy, 'old reset destination')
        world(row['before'], True);world(row['held'], True);world(row['released'], True)
        for number in {x[0] for x in INSTANCES} - set(members):
            same(row['before'][str(number)], row['held'][str(number)], 'other wrapper state unchanged')
        for number in members:
            same(row['held'][str(number)]['reset-held'], True, 'target reset held')
        access_rejection(row['held_access_rejection'])
    streams = {}
    for row, enabled in zip(data['roundtrips'], (False, True)):
        same(row['mode'], 'on' if enabled else 'off', 'roundtrip mode')
        for key in ('source', 'return_stream'):
            saved_stream(row[key], enabled);streams[row[key]['file']] = row[key]
        same(row['source']['file'], row['mode'] + '-forward.mig', 'original stream filename')
        same(row['return_stream']['file'], row['mode'] + '-return.mig', 'return stream filename')
        same(row['source']['snapshot'], row['return_stream']['snapshot'], 'forward full state equality')
        same(row['source']['snapshot'], row['final'], 'reverse full state equality')
        same(row['full_nine_section_roundtrip'], True, 'complete roundtrip')
        same(row['mutable_fields_and_provider_periods_preserved'], True, 'all mutable state and clocks')
    cold_source = by_name['migration-source-on-cold']['details'];saved_stream(cold_source, True)
    same(cold_source['file'], 'on-cold.mig', 'cold cross-profile source')
    require(all(not any(bytes.fromhex(s['registers-hex'])) and s['tx-count'] == 0 and
                s['se-clock-period'] == s['m-ahb-clock-period'] == s['s-ahb-clock-period'] == 0
                for s in cold_source['snapshot'].values()), 'cold outputs isolate enabled profile guard')
    streams[cold_source['file']] = cold_source
    same([x['target_fixture'] for x in data['migrations']],
         ['migration-target-off', 'migration-return-off', 'migration-target-on', 'migration-return-on'], 'both directions actually loaded')
    for row in data['migrations']:
        source = streams[row['source_file']]
        same(row['source_sha256'], source['sha256'], 'incoming complete source digest')
        loaded(row['observation'], source, row['target_fixture'].endswith('-on'))
    same([x['name'] for x in data['rejected_migrations']], list(NEGATIVE), 'all negative load classes')
    for row in data['rejected_migrations']:
        name = row['name'];kind = 'profile' if name in NEGATIVE[:2] or name.startswith('guard-') else 'state'
        instance = 9 if name == 'state-reset-functional' else 1
        enabled = name not in ('on-to-off', 'state-disabled-functional')
        rejected(row, enabled, kind, instance)
        if name in MUTATIONS:
            mutation = row['mutation'];source = streams[mutation['source_file']]
            mode, expected_instance, relative, width, operation, argument = mutation_rule(name)
            same(mutation['mode'], mode, 'mutation source mode')
            same(mutation['source_file'], mode + '-forward.mig', 'exact mutation source stream')
            same(mutation['file'], name + '.mig', 'unique mutated stream filename')
            same(mutation['field_offset'], relative, 'declared mutated field offset')
            same(mutation['field_bytes'], width, 'declared mutated field width')
            same(mutation['name'], name, 'mutation name');same(mutation['profile_instance'], instance, 'specific failed instance')
            section = next(x for x in source['sections'] if x['profile_instance'] == instance)
            same(mutation['section_instance_id'], section['instance_id'], 'target section ID')
            same(mutation['source_sha256'], source['sha256'], 'unchanged original source pin')
            same(mutation['bytes'], source['bytes'], 'full stream length retained')
            same(mutation['absolute_offset'], section['payload_offset'] + mutation['field_offset'], 'precise field byte offset')
            old = bytes.fromhex(section['payload_hex'])
            start, length = mutation['field_offset'], mutation['field_bytes']
            same(mutation['old_value'], int.from_bytes(old[start:start + length], 'big'), 'actual original field value')
            expected_new = mutation['old_value'] ^ 1 if operation == 'flip' else mutation['old_value'] | argument if operation == 'or' else argument
            same(mutation['new_value'], expected_new, 'exact independent field mutation')
            require(mutation['old_value'] != mutation['new_value'], 'actual mutation')
            replacement = mutation['new_value'].to_bytes(length, 'big')
            changed = [section['payload_offset'] + start + i for i, (a, b) in enumerate(zip(old[start:start + length], replacement)) if a != b]
            same(mutation['changed_byte_offsets'], changed, 'all changed bytes inside intended field')
            for field in ('source_unchanged', 'other_device_and_machine_bytes_unchanged'):
                same(mutation[field], True, field)
            digest(mutation['sha256']);streams[mutation['file']] = mutation
    same({x['file'] for x in data['actual_stream_files']}, {'streams/' + name for name in streams}, 'complete 60-stream inventory')
    require(len({x['file'] for x in data['actual_stream_files']}) == 60, 'no duplicate stream records')
    for row in data['actual_stream_files']:
        source = streams[row['file'].removeprefix('streams/')]
        same(row['bytes'], source['bytes'], 'full saved stream length');same(row['sha256'], source['sha256'], 'full saved stream digest')
