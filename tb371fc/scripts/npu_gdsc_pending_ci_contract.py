#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict pending-profile report, complete stream and owned-process evidence gate.

No test producer or QEMU implementation is imported. Scalar expectations and the
64-byte layout are independently specified here. Runtime files are read only
after the compiled test has returned; this module never starts a process.
"""
import hashlib
import itertools
import json
from pathlib import Path
import re
import struct

NAME = 'npu-gdsc-pending-reference'
TYPE, DEVICE, ADDRESS = 'qcom-npu-gdsc', '/machine/npu-gdsc', 0x09981004
REGION = TYPE + '.npu-core-control'
POWER, RAM = 0x80000000, 0x81010000
WITNESS = '4e50553261373c94de08b251f60a83c75d19e042b3a6518c902def7436a8250b'
GLOBAL = ('enabled-profile', 'reference-identity', 'domain-count', 'register-bytes',
          'mapped-bytes', 'parent-resource-index', 'cold-control',
          'control-write-mask', 'power-status-mask')
DOMAIN = ('name', 'address', 'dt-flags', 'timeout-us', 'control', 'request-known',
          'requested', 'state-known', 'powered', 'hw-request-known', 'hw-request')
GETTERS = ('enabled-reference', 'unknown-feedback-pending') + GLOBAL + (
    'parent-known', 'parent-on') + tuple('domain0.' + key for key in DOMAIN)
NORMAL = ('default-strict', 'explicit-strict', 'default-disabled', 'disabled-pending',
          'pending-identity', 'pending-poll-rmw', 'pending-power-truth',
          'pending-known-output', 'pending-reset', 'pending-strict-access',
          'paused-tcg-pending', 'pending-unknown-source', 'pending-unknown-incoming',
          'pending-unknown-roundtrip', 'pending-known-source', 'pending-known-incoming',
          'pending-known-roundtrip', 'strict-source', 'disabled-source')
NEGATIVE = ('strict-to-pending', 'pending-to-strict', 'disabled-to-pending',
            'pending-to-disabled', 'corrupt-profile', 'corrupt-domain-base', 'corrupt-hw-mode')
CHECKS = NORMAL + NEGATIVE
COUNTERS = {'qom_get', 'qom_set', 'qtest', 'memory', 'device_mmio', 'ram', 'cont'}
FALSE_FIELDS = ('parent_power_connected', 'guest_cpu_executed',
                'native_kernel_probe_observed', 'guest_driver_timeout_observed',
                'analog_power_modeled', 'retention_effect_modeled', 'npu_execution_observed',
                'android_boot_passed', 'qtest_ok_is_memtx_result',
                'test_oracle_reads_device_implementation')
EXPECTED_VALUES = {'schema': 'tb371fc-npu-gdsc-pending-v1', 'expected_checks': 26,
                   'expected_processes': 26, 'normal_exit0': 19, 'expected_exit1': 7,
                   'all_actual_wait_calls': 33, 'vmstate_version': 1,
                   'vmstate_payload_bytes': 64, 'same_profile_roundtrips': 2,
                   'successful_incoming': 4, 'reference_identity': 0x4e504731,
                   'pending_profile': 2, 'strict_profile': 1, 'disabled_profile': 0,
                   'domain_count': 1, 'mapped_bytes': 4, 'first_pure_getters': 48,
                   'automatic_test_retries': 0, 'binary_unchanged': True,
                   'process_cleanup_verified': True, **{key: False for key in FALSE_FIELDS}}
EXPECTED_LENGTHS = {'migration_sources': 6, 'migrations': 4, 'rejected_migrations': 4,
                    'corrupted_streams': 3, 'corrupted_stream_rejections': 3,
                    'actual_stream_files': 9, 'process_cleanup': 26}


def need(condition, label):
    if not condition:
        raise ValueError('NPU pending contract: ' + label)


def equal(actual, wanted, label):
    need(type(actual) is type(wanted), label + ' type')
    if isinstance(wanted, dict):
        need(actual.keys() == wanted.keys(), label + ' keys')
        for key in wanted:
            equal(actual[key], wanted[key], label + '.' + key)
    elif isinstance(wanted, (list, tuple)):
        need(len(actual) == len(wanted), label + ' length')
        for index, pair in enumerate(zip(actual, wanted)):
            equal(pair[0], pair[1], f'{label}[{index}]')
    else:
        need(actual == wanted, label)


def digest(value):
    need(type(value) is str and re.fullmatch('[0-9a-f]{64}', value), 'complete SHA256')


def state(value):
    need(type(value) is dict and set(value) == set(GETTERS), '24 complete typed getters')
    bools = ('enabled-reference', 'unknown-feedback-pending', 'parent-known', 'parent-on',
             'domain0.request-known', 'domain0.requested', 'domain0.state-known',
             'domain0.powered', 'domain0.hw-request-known', 'domain0.hw-request')
    for key in bools:
        need(type(value[key]) is bool, 'boolean ' + key)
    for key in set(GETTERS) - set(bools) - {'domain0.name'}:
        need(type(value[key]) is int and 0 <= value[key] <= 0xffffffff, 'unsigned scalar ' + key)
    enabled, pending = value['enabled-reference'], value['unknown-feedback-pending']
    control = value['domain0.control']
    need(not control & ~0xf801, 'only supported software control bits')
    if not enabled:
        equal(control, 1, 'disabled control remains cold')
    profile = (2 if pending else 1) if enabled else 0
    static = dict(zip(GLOBAL, (profile, 0x4e504731, 1, 4, 4, 0xffffffff, 1, 0xf803, POWER)))
    static.update({'domain0.name': 'npu-core', 'domain0.address': ADDRESS,
                   'domain0.dt-flags': 1, 'domain0.timeout-us': 100})
    requested = not bool(control & 1)
    # A collapsed request or a known parent resolves feedback. The profile
    # controls read disposition only; it does not enter this knowledge rule.
    known = enabled and (not requested or value['parent-known'])
    on = enabled and requested and value['parent-known'] and value['parent-on']
    static.update({'domain0.request-known': True, 'domain0.requested': requested,
                   'domain0.state-known': known, 'domain0.powered': on})
    for key, wanted in static.items():
        equal(value[key], wanted, 'snapshot ' + key)
    return value


def wire(value):
    s = state(value)
    raw = struct.pack('>9I', *(s[key] for key in GLOBAL))
    raw += struct.pack('>BB', s['parent-known'], s['parent-on'])
    raw += struct.pack('>IQIIIBB', 0, ADDRESS, 1, 100, s['domain0.control'],
                       s['domain0.hw-request-known'], s['domain0.hw-request'])
    equal(len(raw), 64, 'entire fixed v1 payload')
    return raw


def pure(row, *, initial=False, wanted=None):
    one, two = state(row['first']), state(row['repeated'])
    equal(one, two, 'repeated pure state')
    if wanted is not None:
        equal(one, wanted, 'pure expected snapshot')
    a, b = row['counters_before'], row['counters_after']
    equal(set(a), COUNTERS, 'counter names before');equal(set(b), COUNTERS, 'counter names after')
    for value in [*a.values(), *b.values()]:
        need(type(value) is int and value >= 0, 'nonnegative actual operation count')
    equal(b, {**a, 'qom_get': a['qom_get'] + 48}, 'only 48 pure getter operations')
    equal(row['getters_each'], 24, '24 own getters')
    equal(row['first_fixture_observation'], initial, 'first pure boundary kind')
    equal(row['no_mmio_or_cont_during_observation'], True, 'pure observation claim')
    equal(b['cont'], 0, 'no continue')
    if initial:
        equal(a, dict.fromkeys(COUNTERS, 0), 'first observation before all operations')
    return one


def line(row, group, level):
    equal(row['group'], group, 'real intercepted GPIO group')
    equal(row['actual_level'], level, 'real intercepted GPIO level')
    need(type(row['event_count']) is int and row['event_count'] >= 0, 'complete event count')


def read_word(s):
    return s['domain0.control'] | (POWER if s['domain0.powered'] else 0)


def rejection(row, reason, *, write=False, value=0):
    before, after = state(row['before']), state(row['after'])
    equal(before, after, 'rejected access atomic state')
    equal(row['operation'], 'write' if write else 'read', 'rejected operation')
    equal(row['value'], value, 'rejected write value')
    equal(row['reason'], reason, 'precise rejection reason')
    text = (f'{TYPE}: rejected {"write" if write else "read"} domain=0 offset=0x0 '
            f'size=4 value=0x{value:x} reason={reason}')
    equal(row['diagnostics'], [text], 'exact one device callback diagnostic')
    equal(row['returned'], None if write else 0, 'no fabricated successful read data')
    equal(row['state_atomic'], True, 'single invalid transaction atomic')


def cleanup(row, name):
    code = int(name in NEGATIVE)
    for key, value in {'fixture': name, 'expected_exit_code': code, 'exit_code': code,
                       'actually_waited_and_reaped': True, 'forced_terminate': False,
                       'forced_kill': False, 'signals_to_preexisting_processes': False,
                       'unexpected_qtest_drain_lines': [], 'quit_error': None,
                       'quit_sent': code == 0, 'quit_acknowledged': code == 0,
                       'quit_not_applicable': 'already-exited' if code else None}.items():
        equal(row[key], value, 'cleanup ' + key)
    need(type(row['pid']) is int and row['pid'] > 0, 'owned child PID')
    expected_waits = ([{'called': 'Popen.wait', 'returned_exit_code': 1, 'timeout_seconds': 20}]
                      if code else [])
    expected_waits.append({'called': 'Popen.wait', 'returned_exit_code': code, 'timeout_seconds': 10})
    equal(row['wait_calls'], expected_waits, 'every actual foreground wait/reap')
    equal(set(row['counters']), COUNTERS, 'complete final counters')
    equal(row['counters']['cont'], 0, 'paused complete fixture')
    equal(set(row['drains']), {'qmp', 'qtest'}, 'both channel dispositions are mandatory')
    for key, channel in row['drains'].items():
        if key == 'qtest' and name == 'paused-tcg-pending':
            equal(channel['not_opened'], True, 'TCG no QTest channel')
        else:
            equal(channel['error'], None, 'full drain without errors')
            need(channel['terminal'] in (('eof', 'reset') if code else ('eof',)), 'real terminal read')
            equal(len(bytes.fromhex(channel['drain_hex'])), channel['drain_bytes'], 'entire drain bytes')
    for value in row['stdio'].values():
        equal(value['complete_after_actual_wait'], True, 'stdio complete after wait')
        digest(value['sha256']);need(type(value['bytes']) is int and value['bytes'] >= 0, 'stdio length')


def section(raw):
    equal(raw[:4], b'QEVM', 'actual complete migration header')
    name = TYPE.encode();needle = bytes([len(name)]) + name
    hits, pos = [], 0
    while True:
        pos = raw.find(needle, pos)
        if pos < 0:
            break
        if pos >= 13 and raw[pos - 5] in (1, 4):
            hits.append(pos)
        pos += 1
    equal(len(hits), 1, 'globally unique named START/FULL section')
    at = hits[0];head = at - 5;meta = at + len(needle);begin = meta + 8
    equal(raw[head], 4, 'ordinary full section')
    equal(raw[meta:begin], struct.pack('>II', 0, 1), 'exact instance/version')
    ident = int.from_bytes(raw[head + 1:head + 5], 'big')
    footer = b'\x7e' + struct.pack('>I', ident)
    equal(raw[begin + 64:begin + 69], footer, 'full payload footer')
    return {'name': TYPE, 'section_type': 4, 'instance_id': 0, 'version': 1,
            'section_id': ident, 'header_offset': head, 'payload_offset': begin,
            'payload_bytes': 64, 'footer_offset': begin + 64, 'footer_hex': footer.hex()}


def complete_process(root, check, claimed, qemu):
    """Bind reported cleanup and first observations to all retained raw bytes."""
    name = check['name'];directory = root / name
    command = json.loads((directory / 'command.json').read_bytes())
    prefix = [qemu, '-machine', 'tb371fc,gcc-boot-configured=off,ufs-enabled=off,'
              'rpmh-profile=off,rpmh-boot-configured=off', '-accel',
              'tcg' if name == 'paused-tcg-pending' else 'qtest', '-m', '128M',
              '-smp', '8', '-S', '-display', 'none', '-monitor', 'none',
              '-serial', 'null', '-serial', 'null', '-d',
              'unimp,guest_errors,invalid_mem', '-qmp']
    equal(command[:len(prefix)], prefix, 'exact paused compiled process argv')
    socket = re.fullmatch('unix:(' + re.escape(str(root.parent / 'tmp')) +
                          r'/npp-[A-Za-z0-9_-]+)/m,server=on,wait=off', command[len(prefix)])
    need(socket is not None, 'owned socket below this fixture TMPDIR')
    tail = []
    reference = None if name == 'default-disabled' else name not in ('disabled-pending', 'disabled-source', 'pending-to-disabled')
    pending_flag = None if name in ('default-strict', 'default-disabled') else name not in ('explicit-strict', 'strict-source', 'disabled-source', 'pending-to-strict')
    for key, value in (('enabled-reference', reference), ('unknown-feedback-pending', pending_flag)):
        if value is not None:
            tail += ['-global', TYPE + '.' + key + '=' + ('on' if value else 'off')]
    if name != 'paused-tcg-pending':
        tail += ['-qtest', 'unix:' + socket[1] + '/t,server=on,wait=off',
                 '-qtest-log', str(directory / 'qtest-server.log')]
    if name in NEGATIVE or name.endswith(('-incoming', '-roundtrip')):
        tail += ['-incoming', 'defer']
    equal(command[len(prefix) + 1:], tail, 'no additional image/boot/clock/power arguments')
    actual = json.loads((directory / 'cleanup.json').read_bytes())
    equal({'fixture': name, **actual}, claimed, 'raw cleanup/report equality')
    child = json.loads((directory / 'child.json').read_bytes())
    equal(child, {'pid': claimed['pid'], 'created_by_this_fixture': True,
                  'signals_to_preexisting_processes': False}, 'actual owned-child identity')
    for filename in ('qemu.stdout', 'qemu.stderr'):
        raw = (directory / filename).read_bytes()
        pin = claimed['stdio'][filename]
        equal(len(raw), pin['bytes'], 'complete stdio length')
        equal(hashlib.sha256(raw).hexdigest(), pin['sha256'], 'complete stdio SHA256')
    protocol = (directory / 'protocol.jsonl').read_bytes()
    need(protocol.endswith(b'\n'), 'complete JSONL ending')
    records = [json.loads(part) for part in protocol.splitlines()]
    counts = dict.fromkeys(COUNTERS, 0)
    pending = None;calls = [];qt_sent = [];qt_received = [];irqs = [];waits = [];drains = {};events = []
    next_id = 0;greeting = False;irq_level = False
    allowed = {'qmp_capabilities', 'qom-get', 'qom-set', 'query-status', 'human-monitor-command',
               'stop', 'migrate', 'query-migrate', 'migrate-incoming', 'system_reset', 'quit'}
    for index, row in enumerate(records):
        keys = set(row)
        if keys == {'qmp_send'}:
            need(pending is None, 'one outstanding request')
            req = row['qmp_send'];next_id += 1
            equal(req['id'], next_id, 'contiguous QMP IDs')
            need(req['execute'] in allowed, 'bounded paused QMP operations')
            pending = {'kind': 'qmp', 'request': req, 'reply': None, 'index': index,
                       'counts_before': dict(counts)}
            calls.append(pending)
            if req['execute'] in ('qom-get', 'qom-set'):
                counts['qom_get' if req['execute'] == 'qom-get' else 'qom_set'] += 1
                equal(req['arguments']['path'], DEVICE, 'QOM target only NPU')
                need(req['arguments']['property'] in GETTERS, 'declared own getter/property')
        elif keys == {'qmp_receive'}:
            reply = row['qmp_receive']
            if 'QMP' in reply:
                need(not greeting and not calls, 'single initial QMP greeting');greeting = True
            elif 'event' in reply:
                need(reply['event'] != 'RESUME', 'no guest/device continue event')
                events.append((index, reply))
            else:
                need(pending is not None and pending['kind'] == 'qmp', 'matched QMP response')
                equal(reply['id'], pending['request']['id'], 'reply ID match')
                need(('return' in reply) != ('error' in reply), 'one QMP disposition')
                if 'error' in reply:
                    need(pending['request']['execute'] == 'qom-set' or
                         name in NEGATIVE and pending['request']['execute'] == 'migrate-incoming',
                         'only deliberately rejected QMP operations')
                pending['reply'] = reply;pending['counts_after'] = dict(counts);pending = None
        elif keys == {'qtest_send'}:
            need(pending is None, 'one outstanding QTest request')
            command = row['qtest_send'];fields = command.split();op = fields[0]
            before = dict(counts);counts['qtest'] += 1;qt_sent.append(command)
            memory = op in ('readb', 'readw', 'readl', 'readq', 'writeb', 'writew', 'writel', 'writeq', 'read', 'write')
            if memory:
                address = int(fields[1], 0)
                need(ADDRESS <= address <= ADDRESS + 8 or address == RAM, 'only NPU/adjacency/RAM witness access')
                counts['memory'] += 1
                counts['device_mmio'] += int(ADDRESS <= address < ADDRESS + 4)
                counts['ram'] += int(address == RAM)
            elif op == 'set_irq_in':
                equal(fields[1], DEVICE, 'synthetic input own device')
                need(fields[2] in ('parent-known', 'parent-on', 'hw-request-known', 'hw-request'), 'declared input')
                need(fields[3] == '0' and fields[4] in ('0', '1'), 'exact GPIO shape')
            elif op == 'irq_intercept_out':
                need(fields == [op, DEVICE, 'state-known' if name == 'pending-known-output' else 'power'], 'exact output intercept')
            elif op == 'clock_step':
                equal(command, 'clock_step 1000', 'bounded polling quantum')
                equal(name, 'pending-poll-rmw', 'only polling fixture advances virtual time')
            else:
                equal(command, 'endianness', 'GPIO completion fence')
            pending = {'kind': 'qtest', 'command': command, 'reply': None,
                       'index': index, 'counts_before': before}
            calls.append(pending)
        elif keys == {'qtest_receive'}:
            reply = row['qtest_receive'];qt_received.append(reply)
            if reply.startswith('IRQ '):
                fields = reply.split();need(len(fields) == 3 and fields[1] in ('raise', 'lower'), 'IRQ line')
                equal(int(fields[2]), 0, 'one output line')
                irqs.append({'action': fields[1], 'number': 0})
                irq_level = fields[1] == 'raise'
            else:
                need(pending is not None and pending['kind'] == 'qtest' and reply.startswith('OK'), 'QTest request acknowledgement')
                pending['reply'] = reply;pending['counts_after'] = dict(counts)
                pending['irq_level'] = irq_level;pending['irq_count'] = len(irqs);pending = None
        elif keys == {'actual_wait'}:
            waits.append(row['actual_wait'])
        elif keys == {'channel', 'terminal'}:
            need(row['channel'] in ('qmp', 'qtest') and row['terminal'] in ('eof', 'reset'), 'actual terminal read')
        elif keys == {'drain'}:
            item = row['drain'];channel = item['channel'];need(channel not in drains, 'one complete drain per channel')
            drains[channel] = item
            raw = bytes.fromhex(item['drain_hex']);equal(len(raw), item['drain_bytes'], 'drain bytes')
            for text in raw.decode().splitlines():
                if channel == 'qtest':
                    fields = text.split();need(len(fields) == 3 and fields[0] == 'IRQ' and fields[1] in ('raise', 'lower'), 'only retained IRQ drain')
                    equal(int(fields[2]), 0, 'drained output index')
                    qt_received.append(text);irqs.append({'action': fields[1], 'number': 0})
                else:
                    event = json.loads(text);need('event' in event and event['event'] != 'RESUME', 'complete non-resume QMP drain event')
        elif keys == {'cleanup'}:
            equal(index, len(records) - 1, 'cleanup final complete journal record')
            equal(row['cleanup'], actual, 'complete raw journal cleanup')
        else:
            need(False, 'unknown protocol record')
    need(greeting, 'actual QMP greeting')
    equal(set(records[-1]), {'cleanup'}, 'mandatory final complete cleanup record')
    equal(set(drains), {'qmp'} if name == 'paused-tcg-pending' else {'qmp', 'qtest'},
          'actual complete drain records for every opened channel')
    if name == 'pending-reset':
        resets = [x['index'] for x in calls if x['kind'] == 'qmp' and x['request']['execute'] == 'system_reset']
        equal(len(resets), 1, 'one actual reset request')
        need(any(at > resets[0] and value.get('event') == 'RESET' for at, value in events),
             'reported reset event is actually received after system_reset')
    if pending is not None:
        need(name in NEGATIVE and pending['kind'] == 'qmp' and
             pending['request']['execute'] == 'migrate-incoming', 'only fatal incoming may lack ACK')
    equal(counts, claimed['counters'], 'all raw operation counts')
    equal(waits, claimed['wait_calls'], 'all raw actual waits')
    equal(irqs, claimed['irq_events'], 'complete parsed and drained GPIO events')
    for channel, row in drains.items():
        equal(row, claimed['drains'][channel], 'raw complete drain sidecar')
    equal(calls[0]['request']['execute'], 'qmp_capabilities', 'first negotiation')
    initial = calls[1:49]
    equal(len(initial), 48, '48 first pure calls')
    expected_values = [check['first_pure'][part][key] for part in ('first', 'repeated') for key in GETTERS]
    for call, key, value in zip(initial, GETTERS * 2, expected_values):
        equal(call['kind'], 'qmp', 'first observation never QTest')
        equal(call['request'], {'execute': 'qom-get', 'id': call['request']['id'],
                               'arguments': {'path': DEVICE, 'property': key}}, 'first own getter request')
        equal(call['reply']['return'], value, 'first actual typed QOM value')
    if name == 'paused-tcg-pending':
        equal(qt_sent, [], 'TCG no QTest requests')
        need(not (directory / 'qtest-server.log').exists(), 'TCG no QTest server log')
    else:
        sends, replies, controls = [], [], []
        for text in (directory / 'qtest-server.log').read_text().splitlines():
            match = re.fullmatch(r'\[([RSI]) \+?[0-9]+\.[0-9]+\] ?(.*)', text)
            need(match is not None, 'complete QTest server line')
            role, body = match.groups()
            (sends if role == 'R' else replies if role == 'S' else controls).append(body)
        equal(sends, qt_sent, 'all literal client/server QTest requests')
        equal(replies, qt_received, 'all literal client/server replies including IRQ')
        need(controls in (['OPENED'], ['OPENED', 'CLOSED']), 'single server connection')
        equal(drains['qtest']['total_received_bytes'], sum(len(x.encode()) + 1 for x in replies), 'all QTest response bytes')
    return calls


def fixture_state(initial, *, control=1, known=False, on=False, hw_known=False, hw=False):
    value = dict(initial)
    value.update({'domain0.control': control, 'parent-known': known, 'parent-on': on,
                  'domain0.hw-request-known': hw_known, 'domain0.hw-request': hw,
                  'domain0.request-known': True, 'domain0.requested': not bool(control & 1)})
    enabled = value['enabled-reference']
    value['domain0.state-known'] = enabled and (bool(control & 1) or known)
    value['domain0.powered'] = enabled and not bool(control & 1) and known and on
    return state(value)


def raw_mapping(text):
    parts, part = [], []
    for row in text.splitlines():
        if row.startswith('FlatView #'):
            if part:
                parts.append(part)
            part = []
        part.append(row)
    if part:
        parts.append(part)
    memory = [p for p in parts if any(re.fullmatch(r'\s*AS "memory", root: system\s*', x) for x in p)]
    equal(len(memory), 1, 'one actual complete system memory FlatView')
    leaves = []
    for text in memory[0]:
        match = re.fullmatch(r'\s*([0-9a-fA-F]+)-([0-9a-fA-F]+) \(prio -?[0-9]+, (?:romd|rom|ram|i/o|container)\): (\S(?:.*\S)?)\s*', text)
        if match:
            leaves.append((int(match[1], 16), int(match[2], 16), match[3]))
        else:
            need(re.match(r'\s*(?:0[xX])?[0-9a-fA-F]+\s*-\s*(?:0[xX])?[0-9a-fA-F]+', text) is None,
                 'unparsed range is never ignored')
    wanted = [(ADDRESS, ADDRESS + 3, REGION)]
    equal([x for x in leaves if x[0] <= ADDRESS + 3 and x[1] >= ADDRESS], wanted, 'entire 4-byte mapping')
    equal([x for x in leaves if x[2].startswith(TYPE)], wanted, 'complete NPU leaf name')


class RawFlow:
    """Consume every operation in one fixed fixture; no unordered matching."""
    def __init__(self, calls, check):
        self.calls, self.at, self.check = calls, 0, check
        self.qmp('qmp_capabilities', expected={})
        self.pure(check['first_pure'])

    def take(self, kind):
        need(self.at < len(self.calls), 'required literal operation is present')
        call = self.calls[self.at];self.at += 1
        equal(call['kind'], kind, 'exact QMP/QTest interleaving')
        return call

    def qmp(self, operation, arguments=None, *, expected=None, compare=False, fatal=False, error=None):
        call = self.take('qmp')
        request = {'execute': operation, 'id': call['request']['id']}
        if arguments is not None:
            request['arguments'] = arguments
        equal(call['request'], request, 'literal QMP command and arguments')
        reply = call['reply']
        if fatal:
            need(reply is None or ('return' in reply) != ('error' in reply), 'fatal migration disposition')
            return call
        need(reply is not None, 'required actual QMP reply')
        if error is not None:
            equal(reply, error, 'raw immutable property rejection/report')
            need(reply.get('error', {}).get('class') == 'GenericError', 'actual property rejection')
            need(arguments['property'] in reply['error']['desc'], 'error names the immutable property')
        else:
            need('return' in reply and 'error' not in reply, 'successful QMP result')
            if compare or expected is not None:
                equal(reply['return'], expected, 'actual QMP value')
        return call

    def gets(self, value):
        for key in GETTERS:
            self.qmp('qom-get', {'path': DEVICE, 'property': key}, expected=value[key], compare=True)

    def pure(self, record):
        start = self.at
        self.gets(record['first']);self.gets(record['repeated'])
        equal(self.calls[start]['counts_before'], record['counters_before'], 'raw pure before counters')
        equal(self.calls[self.at - 1]['counts_after'], record['counters_after'], 'raw pure after counters')

    def qt(self, command, *, word=None, raw=None, token=None):
        call = self.take('qtest');equal(call['command'], command, 'complete literal QTest command')
        fields = call['reply'].split();need(fields and fields[0] == 'OK', 'QTest protocol acknowledgement')
        op = command.split()[0]
        if word is not None:
            equal(len(fields), 2, 'complete scalar response');equal(int(fields[1], 0), word, 'actual scalar value')
        elif raw is not None:
            equal(len(fields), 2, 'complete memory response');need(fields[1].startswith('0x'), 'hex memory response')
            equal(bytes.fromhex(fields[1][2:]), raw, 'every RAM witness byte')
        elif token is not None:
            equal(fields, ['OK', token], 'exact fence response')
        elif op.startswith('read'):
            need(len(fields) == 2 and re.fullmatch(r'0x[0-9a-fA-F]+', fields[1]), 'rejected read protocol value, not success evidence')
        else:
            equal(fields, ['OK'], 'exact write/GPIO processing acknowledgement')
        return call

    def inputs(self, known, on, hw_known=None, hw=None):
        for name, value in [('parent-on', on), ('parent-known', known)]:
            self.qt(f'set_irq_in {DEVICE} {name} 0 {int(value)}')
        if hw_known is not None:
            for name, value in [('hw-request', hw), ('hw-request-known', hw_known)]:
                self.qt(f'set_irq_in {DEVICE} {name} 0 {int(value)}')

    def write(self, value, offset=0, width='l'):
        self.qt(f'write{width} {ADDRESS + offset:#x} {value:#x}')

    def accepted(self, value):
        self.gets(value);self.qt(f'readl {ADDRESS:#x}', word=read_word(value));self.gets(value)

    def rejected(self, row):
        self.gets(row['before'])
        if row['operation'] == 'write':
            self.write(row['value'])
        else:
            self.qt(f'readl {ADDRESS:#x}', word=0)
        self.gets(row['after'])

    def fence(self, row):
        call = self.qt('endianness', token='little')
        equal(call['irq_level'], row['actual_level'], 'actual intercepted GPIO/report value')
        equal(call['irq_count'], row['event_count'], 'actual parsed GPIO event count')

    def status(self, *, status=None, value=None):
        call = self.qmp('query-status', expected=value)
        actual = call['reply']['return'];equal(actual['running'], False, 'actual machine paused')
        if status is not None:
            equal(actual['status'], status, 'exact migration run state')

    def poll(self, wanted):
        values = []
        while self.at < len(self.calls) and self.calls[self.at]['kind'] == 'qmp' and self.calls[self.at]['request']['execute'] == 'query-migrate':
            value = self.qmp('query-migrate')['reply']['return'];values.append(value)
        need(bool(values), 'actual migration poll exists')
        equal(values[-1], wanted, 'reported complete migration query is actual last reply')
        equal(values[-1]['status'], 'completed', 'actual successful migration')
        need(all(x.get('status') not in ('completed', 'failed', 'cancelled') for x in values[:-1]), 'poll stops at first terminal status')

    def finish(self, fatal=False):
        if not fatal:
            self.qmp('quit', expected={})
        equal(self.at, len(self.calls), 'no omitted or unreviewed protocol operation')


def raw_channels(path):
    return {'channels': [{'channel-type': 'main', 'addr': {
        'transport': 'file', 'filename': str(path.resolve()), 'offset': 0}}]}


def raw_save(flow, root, saved):
    flow.qt(f'write {RAM:#x} 0x20 0x{WITNESS}')
    flow.qmp('stop', expected={});flow.status()
    flow.pure(saved['source_before'])
    flow.qmp('migrate', raw_channels(root / saved['file']), expected={})
    flow.poll(saved['query_migrate']);flow.status(status='postmigrate')
    flow.pure(saved['source_after'])


def raw_receive(flow, root, row):
    warm = row['preload']['first']
    incoming = row['first_after_incoming']['first']
    equal(warm, fixture_state(incoming, control=0x2800, known=not incoming['parent-known'],
          on=True, hw_known=not incoming['domain0.hw-request-known'], hw=not incoming['domain0.hw-request']),
          'literal conflicting target fixture before each incoming')
    flow.inputs(warm['parent-known'], warm['parent-on'], warm['domain0.hw-request-known'], warm['domain0.hw-request'])
    flow.write(0x2800);flow.pure(row['preload'])
    call = flow.qt('endianness', token='little')
    equal(call['irq_level'], warm['domain0.powered'], 'prewarmed actual output')
    call = flow.qmp('migrate-incoming', raw_channels(root / row['source_file']), expected={})
    equal(call['counts_before'], row['counters_at_incoming'], 'actual incoming first boundary')
    flow.poll(row['query_migrate']);flow.status()
    flow.pure(row['first_after_incoming'])
    flow.fence(row['line'])
    flow.qt(f'read {RAM:#x} 0x20', raw=bytes.fromhex(WITNESS))
    flow.accepted(row['first_after_incoming']['first'])


def raw_fixture(root, check, calls):
    """All 26 bounded source-defined paths, including every mutable observation."""
    name = check['name'];row = check['details'];initial = check['first_pure']['first']
    flow = RawFlow(calls, check);device_diags = [];core_diags = []
    def record_rejection(value):
        device_diags.extend(value['diagnostics']);flow.rejected(value)
    if name == 'paused-tcg-pending':
        flow.status(value=row['status_before']);flow.pure(row['pure']);flow.status(value=row['status_after'])
    else:
        flow.qt('irq_intercept_out ' + DEVICE + (' state-known' if name == 'pending-known-output' else ' power'))
        if name in ('default-strict', 'explicit-strict'):
            flow.accepted(initial);flow.inputs(False, True);flow.write(0x5800)
            equal(row['unknown_read_rejection']['before'], fixture_state(initial, control=0x5800, on=True), 'strict literal unknown state')
            record_rejection(row['unknown_read_rejection']);flow.fence(row['line'])
        elif name in ('default-disabled', 'disabled-pending'):
            for value in row['access_rejections']:
                equal(value['before'], initial, 'disabled rejection exact cold state');record_rejection(value)
            flow.inputs(True, True, True, True)
            equal(row['after_inputs'], fixture_state(initial, known=True, on=True, hw_known=True, hw=True), 'disabled fixture exact external inputs')
            flow.gets(row['after_inputs']);flow.fence(row['line'])
        elif name == 'pending-identity':
            text = flow.qmp('human-monitor-command', {'command-line': 'info mtree -f'})['reply']['return']
            raw_mapping(text)
            equal((root / name / 'active-memory-map.txt').read_bytes(), text.encode(), 'full exact HMP bytes')
            for entry in row['readonly']:
                prop = entry['property']
                flow.qmp('qom-set', {'path': DEVICE, 'property': prop, 'value': initial[prop]}, error=entry['response'])
            flow.gets(initial);flow.accepted(initial);flow.fence(row['line'])
        elif name == 'pending-poll-rmw':
            flow.inputs(False, True, True, True);flow.write(0x5801)
            flow.accepted(fixture_state(initial, control=0x5801, on=True, hw_known=True, hw=True))
            flow.write(0x5800)
            for value in row['polls']:
                wanted = fixture_state(initial, control=0x5800, on=True, hw_known=True, hw=True)
                equal(value['state'], wanted, 'literal poll unknown state')
                flow.qt(f'readl {ADDRESS:#x}', word=0x5800);flow.gets(wanted)
                flow.qt('clock_step 1000', word=value['virtual_ns']);flow.fence(value['line'])
            flow.write(POWER | 0x7800)
            equal(row['final'], fixture_state(initial, control=0x7800, on=True, hw_known=True, hw=True), 'literal carry RMW state')
            flow.accepted(row['final']);flow.fence(row['final_line'])
            flow.write(0)
            equal(row['zero_state'], fixture_state(initial, control=0, on=True, hw_known=True, hw=True), 'literal zero unknown state')
            equal(row['zero_read'], 0, 'successful pending zero read')
            flow.accepted(row['zero_state']);flow.fence(row['zero_line'])
        elif name == 'pending-power-truth':
            for value in row['truth_rows']:
                known, on, collapse, carry = value['inputs']
                flow.inputs(known, on, not known, on)
                control = 0x6800 | int(collapse);flow.write(control | (POWER if carry else 0))
                wanted = fixture_state(initial, control=control, known=known, on=on, hw_known=not known, hw=on)
                equal(value['state'], wanted, 'literal full power truth state')
                flow.accepted(wanted);flow.fence(value['line'])
        elif name == 'pending-known-output':
            for value in row['known_output_rows']:
                known, on, collapse = value['inputs'];flow.inputs(known, on)
                control = 0x4800 | int(collapse);flow.write(control)
                wanted = fixture_state(initial, control=control, known=known, on=on)
                equal(value['state'], wanted, 'literal knowledge truth state')
                flow.accepted(wanted);flow.fence(value['line'])
        elif name == 'pending-reset':
            flow.inputs(True, True, True, True);flow.write(0x5800);flow.accepted(row['before'])
            call = flow.qt('endianness', token='little');equal(call['irq_level'], True, 'actual pre-reset high')
            flow.qt(f'write {RAM:#x} 0x20 0x{WITNESS}');flow.qmp('system_reset', expected={})
            seen = False
            while not seen:
                value = flow.qmp('qom-get', {'path': DEVICE, 'property': 'domain0.control'})['reply']['return']
                need(type(value) is int and value in (0x5800, 1), 'bounded reset settlement state')
                seen = value == 1
            flow.accepted(row['after']);flow.qt(f'read {RAM:#x} 0x20', raw=bytes.fromhex(WITNESS));flow.fence(row['line'])
        elif name == 'pending-strict-access':
            flow.inputs(False, True);flow.write(0x5800)
            wanted = fixture_state(initial, control=0x5800, on=True)
            for value in row['model_write_rejections']:
                equal(value['before'], wanted, 'exact rejected control pre-state');record_rejection(value)
            for value in row['core_access_rejections']:
                flow.gets(wanted)
                op = 'write' if value['write'] else 'read'
                command = f'{op}{value["width"]} {ADDRESS + value["offset"]:#x}' + (' 0x0' if value['write'] else '')
                flow.qt(command);flow.gets(wanted);core_diags.extend(value['diagnostics'])
            flow.write(row['wide_write']['value'], width='q')
            core_diags.extend(row['wide_write']['diagnostics'])
            equal(row['wide_write']['after'], fixture_state(initial, control=0x2001, on=True), 'exact wide low-word committed state')
            flow.accepted(row['wide_write']['after']);flow.fence(row['line'])
        elif name in ('pending-unknown-source', 'pending-known-source'):
            known = name == 'pending-known-source'
            wanted = fixture_state(initial, control=0x4800 if known else 0x5800,
                                   known=known, on=True, hw_known=known, hw=not known)
            equal(row['snapshot'], wanted, 'literal migration source state')
            flow.inputs(known, True, known, not known);flow.write(wanted['domain0.control']);flow.accepted(wanted)
            call = flow.qt('endianness', token='little');equal(call['irq_level'], known, 'actual migration source power')
            raw_save(flow, root, row)
        elif name in ('strict-source', 'disabled-source'):
            equal(row['snapshot'], initial, 'source cold exact strict/disabled state')
            call = flow.qt('endianness', token='little');equal(call['irq_level'], False, 'cold source actual output')
            raw_save(flow, root, row)
        elif name.endswith(('-incoming', '-roundtrip')):
            raw_receive(flow, root, row['incoming'])
            if name.endswith('-incoming'):
                raw_save(flow, root, row['saved_return'])
        else:
            need(name in NEGATIVE, 'declared negative fixture')
            warm = row['preload']['first']
            if initial['enabled-reference']:
                flow.inputs(True, True);flow.write(0)
                wanted = fixture_state(initial, control=0, known=True, on=True)
                equal(warm, wanted, 'exact rejected-load warm target')
            else:
                equal(warm, initial, 'disabled rejected-load cold target')
            call = flow.qt('endianness', token='little');equal(call['irq_level'], initial['enabled-reference'], 'actual pre-failure output')
            equal(row['incoming_irq_event_index'], call['irq_count'], 'actual pre-load complete GPIO event boundary')
            flow.pure(row['preload'])
            call = flow.qmp('migrate-incoming', raw_channels(root / row['file']), fatal=True)
            equal(call['counts_before'], row['counters_at_incoming'], 'actual failure boundary counters')
    flow.finish(fatal=name in NEGATIVE)
    text = (root / name / 'qemu.stderr').read_text()
    equal([x for x in text.splitlines() if x.startswith(TYPE + ': rejected ')], device_diags, 'all exact device rejection diagnostics')
    actual_core = [[op, int(address, 16), int(size), region, reason] for op, address, size, region, reason in re.findall(
        r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)", text)]
    equal(actual_core, core_diags, 'all exact core rejection diagnostics')
    if name not in NEGATIVE:
        need('load of migration failed' not in text, 'no hidden failed normal migration')


def judge_npu_gdsc_pending_details(case, data):
    if case.name != NAME:
        return
    for key, wanted in EXPECTED_VALUES.items():
        equal(data.get(key), wanted, key)
    equal(data.get('passed'), True, 'complete gate verdict')
    for key, count in EXPECTED_LENGTHS.items():
        need(type(data.get(key)) is list and len(data[key]) == count, 'complete ' + key)
    equal([row['name'] for row in data['checks']], list(CHECKS), 'exact ordered 26 compiled fixtures')
    equal([row['fixture'] for row in data['process_cleanup']], list(CHECKS), 'exact complete child inventory')
    digest(data['binary_before_sha256']);equal(data['binary_after_sha256'], data['binary_before_sha256'], 'same binary after every child')
    root = Path(case.result).parent
    qemu = str(Path(case.command[case.command.index('--qemu') + 1]).resolve())
    equal(hashlib.sha256(Path(qemu).read_bytes()).hexdigest(), data['binary_before_sha256'], 'actual complete compiled ELF identity')
    checks = {row['name']: row for row in data['checks']}
    cleanup_rows = {row['fixture']: row for row in data['process_cleanup']}
    calls = {}
    for name in CHECKS:
        check = checks[name];equal(check['passed'], True, 'individual compiled check')
        first = pure(check['first_pure'], initial=True)
        enabled = name not in ('default-disabled', 'disabled-pending', 'disabled-source', 'pending-to-disabled')
        pending = name not in ('default-strict', 'explicit-strict', 'default-disabled', 'strict-source', 'disabled-source', 'pending-to-strict')
        equal(first['enabled-reference'], enabled, 'fixture exact enable profile')
        equal(first['unknown-feedback-pending'], pending, 'fixture explicit pending flag')
        equal(first['domain0.control'], 1, 'first cold control')
        equal(first['parent-known'], False, 'no supplied parent at first observation')
        equal(first['parent-on'], False, 'cold external level')
        cleanup(cleanup_rows[name], name)
        calls[name] = complete_process(root, check, cleanup_rows[name], qemu)
    equal(sum(len(row['wait_calls']) for row in cleanup_rows.values()), 33, '19 normal plus 7 twice-waited fatal children')
    for name in ('default-strict', 'explicit-strict'):
        row = checks[name]['details'];equal(row['profile'], 1, 'strict remains default')
        rejection(row['unknown_read_rejection'], 'power-feedback-unknown');line(row['line'], 'power', False)
        equal(row['unknown_read_rejection']['before']['enabled-profile'], 1, 'strict1 exact state')
    for name in ('default-disabled', 'disabled-pending'):
        row = checks[name]['details'];equal(row['profile'], 0, 'disabled profile unaffected by flag')
        equal(len(row['access_rejections']), 2, 'disabled read/write both reject')
        rejection(row['access_rejections'][0], 'reference-disabled')
        rejection(row['access_rejections'][1], 'reference-disabled', write=True)
        after = state(row['after_inputs']);equal(after['parent-known'], True, 'explicit known fixture')
        equal(after['parent-on'], True, 'explicit on fixture');equal(after['domain0.powered'], False, 'disabled never powers')
        line(row['line'], 'power', False)
    row = checks['pending-identity']['details']
    equal(row['mapping'], {'active_leaves': [[ADDRESS, ADDRESS + 3, REGION]], 'mapped_bytes': 4}, 'exact existing sparse word')
    equal([x['property'] for x in row['readonly']], list(GETTERS), 'every getter/configuration immutable after realize')
    for prop in row['readonly']:
        need(prop['response'].get('error', {}).get('class') == 'GenericError', 'real immutable property refusal')
    line(row['line'], 'power', False)
    row = checks['pending-poll-rmw']['details']
    for key, value in {'poll_limit': 100, 'step_ns': 1000, 'driver_executed': False,
                       'guest_timeout_observed': False, 'rmw_before': 0x5801, 'rmw_after': 0x5800,
                       'status_carry_write': POWER | 0x7800}.items():
        equal(row[key], value, 'finite poll ' + key)
    equal(len(row['polls']), 100, 'finite 100-read driver-style loop')
    clocks = []
    for index, item in enumerate(row['polls']):
        equal(item['index'], index, 'poll exact order');equal(item['word'], 0x5800, 'unknown read control only')
        s = state(item['state']);equal(s['domain0.control'], 0x5800, 'poll control')
        equal(s['parent-known'], False, 'poll never supplies parent')
        equal(s['parent-on'], True, 'stale on cannot create knowledge')
        equal(s['domain0.state-known'], False, 'poll remains unknown')
        line(item['line'], 'power', False)
        need(type(item['virtual_ns']) is int, 'actual virtual clock');clocks.append(item['virtual_ns'])
    equal([b - a for a, b in zip(clocks, clocks[1:])], [1000] * 99, 'exact virtual poll spacing')
    final = state(row['final']);equal(final['domain0.control'], 0x7800, 'POWER_ON not stored')
    equal(final['domain0.state-known'], False, 'RMW does not resolve unknown');line(row['final_line'], 'power', False)
    zero = state(row['zero_state']);equal(zero['domain0.control'], 0, 'literal zero control')
    equal(zero['domain0.state-known'], False, 'zero read remains unknown')
    equal(row['zero_read'], 0, 'successful zero read');line(row['zero_line'], 'power', False)
    poll_calls = [x for x in calls['pending-poll-rmw'] if x['kind'] == 'qtest' and x['command'] == 'clock_step 1000']
    equal(len(poll_calls), 100, 'all actual clock calls')
    equal([int(x['reply'].split()[1], 0) for x in poll_calls], clocks, 'raw QTest clock values')
    truth = checks['pending-power-truth']['details']['truth_rows']
    equal([r['inputs'] for r in truth], [list(x) for x in itertools.product((False, True), repeat=4)], 'full independent 16-combination truth table')
    for row in truth:
        known, on, collapsed, carry = row['inputs'];s = state(row['state'])
        equal([s['parent-known'], s['parent-on'], bool(s['domain0.control'] & 1)], [known, on, collapsed], 'truth input values')
        equal(row['read'], read_word(s), 'knowledge-confirmed power read');line(row['line'], 'power', s['domain0.powered'])
    known_rows = checks['pending-known-output']['details']['known_output_rows']
    equal([r['inputs'] for r in known_rows], [[False, True, False], [True, True, False],
          [True, False, False], [False, False, False], [False, True, True]], 'knowledge GPIO transitions')
    for row in known_rows:
        s = state(row['state']);equal(row['read'], read_word(s), 'knowledge fixture read')
        equal([s['parent-known'], s['parent-on'], bool(s['domain0.control'] & 1)], row['inputs'], 'knowledge fixture inputs')
        line(row['line'], 'state-known', s['domain0.state-known'])
    row = checks['pending-reset']['details'];before, after = state(row['before']), state(row['after'])
    for key in ('parent-known', 'parent-on', 'domain0.hw-request-known', 'domain0.hw-request'):
        equal(before[key], True, 'pre-reset external fixture');equal(after[key], before[key], 'provider input retained')
    equal(before['domain0.control'], 0x5800, 'warm reset source');equal(after['domain0.control'], 1, 'reset cold control')
    equal(row['reset_event_seen'], True, 'actual reset event');equal(row['provider_inputs_preserved'], True, 'reset provider boundary')
    equal(row['ram_address'], RAM, 'RAM outside DTB ROM reload');equal(row['ram_hex'], WITNESS, 'full RAM witness')
    line(row['line'], 'power', False)
    row = checks['pending-strict-access']['details']
    equal(len(row['model_write_rejections']), 6, 'all unsupported control writes')
    for entry, bit in zip(row['model_write_rejections'][:5], (2, 3, 10, 16, 30)):
        rejection(entry, 'unsupported-bits', write=True, value=0x5800 | 1 << bit)
    rejection(row['model_write_rejections'][5], 'hw-mode-not-in-reference', write=True, value=0x5802)
    invalid = 'invalid size (min:4 max:4)';expected_core = []
    for write in (False, True):
        for width, offset, pieces in (('b', 0, [(0, 1)]), ('w', 0, [(0, 2)]),
                                      ('l', 1, [(1, 1), (2, 2), (4, 1)]),
                                      ('l', 2, [(2, 2), (4, 2)]),
                                      ('l', 4, [(4, 4)]), ('l', 8, [(8, 4)])):
            expected_core.append({'write': write, 'width': width, 'offset': offset,
                'diagnostics': [['write' if write else 'read', a if a < 4 else ADDRESS + a,
                                 size, REGION if a < 4 else '(null)', invalid if a < 4 else 'rejected'] for a, size in pieces],
                'state_atomic': True})
    equal(row['core_access_rejections'], expected_core, 'exact core width/alignment/adjacent splits')
    wide = row['wide_write'];equal(wide['value'], (0x12345678 << 32) | 0x2001, 'actual wide scalar')
    equal(wide['diagnostics'], [['write', ADDRESS + 4, 4, '(null)', 'rejected']], 'wide second transaction rejects')
    equal(wide['low_word_committed'], True, 'documented first transaction commit')
    equal(wide['whole_request_atomic'], False, 'no false wide/bulk atomicity claim')
    equal(state(wide['after'])['domain0.control'], 0x2001, 'wide low prefix retained');line(row['line'], 'power', False)
    row = checks['paused-tcg-pending']['details'];equal(row['status_before']['running'], False, 'TCG before paused')
    equal(row['status_after']['running'], False, 'TCG after paused');pure(row['pure'])
    equal(row['qtest_channel_opened'], False, 'TCG pure only');equal(row['guest_cpu_executed'], False, 'no TCG CPU execution')

    originals = {}
    names = ['pending-unknown.bin', 'pending-unknown-return.bin', 'pending-known.bin',
             'pending-known-return.bin', 'strict-source.bin', 'disabled-source.bin']
    equal([s['file'] for s in data['migration_sources']], names, 'six original whole streams')
    for row in data['migration_sources']:
        raw = (root / row['file']).read_bytes();sec = section(raw);s = state(row['snapshot'])
        equal(row['section'], sec, 'independent exact section locator')
        wanted = wire(s);actual = raw[sec['payload_offset']:sec['payload_offset'] + 64]
        equal(actual, wanted, 'every one of 64 actual VMState bytes')
        equal(row['payload_hex'], wanted.hex(), 'reported whole payload')
        equal(row['payload_sha256'], hashlib.sha256(wanted).hexdigest(), 'complete payload digest')
        equal(row['bytes'], len(raw), 'complete original stream length')
        equal(row['sha256'], hashlib.sha256(raw).hexdigest(), 'complete original stream hash')
        equal(row['stream_modified'], False, 'unmodified source stream')
        equal(row['query_migrate']['status'], 'completed', 'source migrate completion')
        pure(row['source_before'], wanted=s);pure(row['source_after'], wanted=s)
        reported = checks[row['source_fixture']]['details']
        if row['source_fixture'].endswith('-incoming'):
            reported = reported['saved_return']
        equal(row, reported, 'source stream is the same complete per-fixture evidence')
        originals[row['file']] = (row, raw)
    for kind in ('unknown', 'known'):
        a, b = [originals[f'pending-{kind}{suffix}.bin'][0] for suffix in ('', '-return')]
        equal(a['payload_hex'], b['payload_hex'], 'all 64 roundtrip bytes')
    target_names = ['pending-unknown-incoming', 'pending-unknown-roundtrip', 'pending-known-incoming', 'pending-known-roundtrip']
    equal([r['target_fixture'] for r in data['migrations']], target_names, 'four complete incoming processes')
    for row in data['migrations']:
        equal(row, checks[row['target_fixture']]['details']['incoming'], 'incoming table/per-process equality')
        source = originals[row['source_file']][0]
        equal(row['source_sha256'], source['sha256'], 'incoming exact stream')
        warm = pure(row['preload']);s = pure(row['first_after_incoming'], wanted=source['snapshot'])
        need(warm != s, 'conflicting target state was actually replaced')
        equal(row['query_migrate']['status'], 'completed', 'incoming completed')
        for key in ('memory', 'device_mmio', 'cont'):
            equal(row['first_after_incoming']['counters_before'][key], row['counters_at_incoming'][key], 'first post-load pure boundary ' + key)
        equal(row['device_mmio_before_first_observation'], False, 'no post-load MMIO before getters')
        equal(row['cont_before_first_observation'], False, 'no post-load cont before getters')
        equal(row['stream_modified'], False, 'incoming never edits stream')
        equal(row['ram_address'], RAM, 'incoming RAM witness address');equal(row['ram_hex'], WITNESS, 'incoming full witness')
        equal(row['word'], read_word(s), 'restored MMIO');line(row['line'], 'power', s['domain0.powered'])
    corrupted = {}
    source, raw = originals['pending-known.bin']
    for row, name, offset, width, xor in zip(data['corrupted_streams'], NEGATIVE[4:], (0, 42, 58), (4, 8, 4), (1, 4, 2)):
        equal(row['file'], name + '.bin', 'one intended corrupt stream')
        equal(row['field_offset'], offset, 'fixed immutable/control field offset')
        damaged = (root / row['file']).read_bytes()
        equal(len(damaged), len(raw), 'full stream length unchanged')
        changed = bytearray(raw);at = source['section']['payload_offset'] + offset
        old = int.from_bytes(raw[at:at + width], 'big')
        changed[at:at + width] = (old ^ xor).to_bytes(width, 'big')
        equal(damaged, bytes(changed), 'only one intended bit differs in whole stream')
        equal(row['changed_byte_offsets'], [at + width - 1], 'exact changed byte')
        equal(row['changed_bits'], 1, 'one-bit corruption')
        equal(row['source_file'], 'pending-known.bin', 'corrupt source identity')
        equal(row['source_sha256'], source['sha256'], 'corrupt source hash')
        equal(row['stream_modified'], True, 'intentional damage labeled')
        equal(row['section'], section(damaged), 'corrupt structure still exact')
        equal(row['bytes'], len(damaged), 'corrupt full length')
        equal(row['sha256'], hashlib.sha256(damaged).hexdigest(), 'corrupt full digest')
        begin = row['section']['payload_offset']
        equal(row['payload_hex'], damaged[begin:begin + 64].hex(), 'all damaged wire bytes')
        corrupted[row['file']] = row
    failures = data['rejected_migrations'] + data['corrupted_stream_rejections']
    equal([r['target_fixture'] for r in failures], list(NEGATIVE), 'all seven expected process failures')
    diags = ('2 != 1', '1 != 2', '2 != 0', '0 != 2', '2 != 3',
             f'{ADDRESS:x} != {ADDRESS ^ 4:x}', TYPE + ': invalid migrated npu domain 0')
    for row, diagnostic, name in zip(failures, diags, NEGATIVE):
        equal(row, checks[name]['details'], 'failed stream table/per-process equality')
        equal(row['expected_diagnostic'], diagnostic, 'exact rejected field/profile')
        equal(row['actual_exit'], 1, 'actual fatal loader exit')
        warm = pure(row['preload'])
        equal(row['counters_at_incoming'], row['counters_after_exit'], 'no post-load MMIO/QOM/continue on dead process')
        equal(row['post_exit_qom_available'], False, 'no invented dead-process observation')
        equal(row['guard_scope'], 'this-device-before-successful-feedback-publication', 'device-local guard scope')
        tail = [{'action': 'lower', 'number': 0}] if warm['enabled-reference'] else []
        equal(row['complete_drained_irq_tail'], tail, 'failure withdraws stale output and never restores it')
        equal(cleanup_rows[name]['irq_events'][row['incoming_irq_event_index']:], tail, 'failure full final drain GPIO tail')
        text = (root / name / 'qemu.stderr').read_text()
        need(TYPE in text and diagnostic in text and 'load of migration failed' in text and 'error while loading state' in text,
             'complete actual loader diagnostics')
        full = originals.get(row['file'], (corrupted.get(row['file']),))[0]
        need(full is not None, 'rejection consumes a saved whole stream')
        equal(row['sha256'], full['sha256'], 'failed input full hash')
        if name.startswith('corrupt-'):
            equal(row['mutation'], corrupted[row['file']], 'complete mutation/per-process equality')
    all_streams = {**{name: row for name, (row, _) in originals.items()}, **corrupted}
    equal([r['file'] for r in data['actual_stream_files']], list(all_streams), 'all nine exact stream files')
    equal({p.name for p in root.glob('*.bin')}, set(all_streams), 'no missing or extra whole stream file')
    for row in data['actual_stream_files']:
        s = all_streams[row['file']]
        equal(row, {'file': row['file'], 'bytes': s['bytes'], 'sha256': s['sha256']}, 'full stream inventory pin')
    # Only after all independent typed state/stream checks, bind every detail to
    # the complete actual protocol in source-defined order. No summaries stand
    # in for an unconsumed operation, ordinary diagnostic or incoming boundary.
    for name in CHECKS:
        raw_fixture(root, checks[name], calls[name])
