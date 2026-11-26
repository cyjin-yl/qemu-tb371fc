#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict receipt checks for the separate immutable debug-availability aperture."""
import hashlib
import re
import struct

NAME = 'spmi-debug-fuse-reference'
TYPE = 'qcom-sm8250-spmi-debug-fuse'
PATH = '/machine/spmi-debug-fuse'
BASE, A, B, POLICY = 0x007820a0, 0xa5c3124f, 0x6a24c591, 0x01000000
ADMISSION = (
    ('enabled-no-word', True, None), ('enabled-unbound', True, 'UNBOUND'),
    ('enabled-short', True, '0x123'), ('enabled-overflow', True, '0x123456789'),
    ('enabled-uppercase-prefix', True, '0X12345678'),
    ('enabled-uppercase-digit', True, '0x1234567A'),
    ('enabled-invalid-digit', True, '0x1234567g'),
    ('word-while-off', False, '0xa5c3124f'))
NORMAL = ('default-off', 'explicit-off', 'enabled-a', 'enabled-b', 'reference-policy',
          'migration-off', 'migration-a', 'migration-b')
NEGATIVE = ('off-to-on', 'on-to-off', 'a-to-b', 'b-to-a', 'corrupt-profile', 'corrupt-word')
CHECKS = NORMAL[:5] + tuple(row[0] for row in ADMISSION) + NORMAL[5:] + NEGATIVE


def require(value, label):
    if not value:
        raise ValueError('SPMI debug fuse contract: ' + label)


def number(value, expected, label):
    require(type(value) is int and value == expected, label)


def digest(value):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value), 'complete SHA256')


def state(value, word):
    require(isinstance(value, dict) and set(value) == {'enabled', 'bound-word'}, 'complete state')
    require(value['enabled'] is (word is not None), 'enabled bool')
    require(value['bound-word'] == ('UNBOUND' if word is None else f'{word:#010x}'), 'bound word')


def observation(value, word):
    state(value['first'], word);state(value['repeated'], word)
    number(value['first_getters'], 2, 'first getters');number(value['repeated_getters'], 2, 'repeat getters')
    require(value['no_memory_or_cont'] is True, 'pure observation boundary')
    before, after = value['counters_before'], value['counters_after']
    fields = {'qom_get', 'qom_set', 'qtest', 'memory', 'device_mmio', 'ram', 'cont'}
    require(set(before) == set(after) == fields, 'complete command counters')
    for key in fields:
        require(type(before[key]) is int and before[key] >= 0, 'initial counter')
        number(after[key], before[key] + (4 if key == 'qom_get' else 0), 'pure counter delta')
    for key in ('memory', 'cont'):
        number(before[key], 0, 'no memory or cont before first observation')


def read(value, word):
    require(value['command'] == 'readl 0x7820a8', 'exact readable word command')
    number(value['value'], word, 'full immutable word')


def unmapped(value, address, write):
    operation = 'write' if write else 'read'
    require(value['command'] == f'{operation}l {address:#x}' + (' 0' if write else ''), 'unmapped command')
    require(value['core_diagnostic'] == [[operation, address, 4, '(null)', 'rejected']], 'one unmapped diagnostic')
    require(value['state_unchanged'] is True and value['qtest_ok_is_memtx_result'] is False, 'unmapped rejection')


def cleanup(rows):
    require(len(rows) == 22 and {r['fixture'] for r in rows} == set(CHECKS), 'all distinct owned processes')
    for row in rows:
        name = row['fixture'];code = 0 if name in NORMAL else 1
        number(row['expected_exit_code'], code, 'scenario exit');number(row['exit_code'], code, 'actual exit')
        require(row['actually_waited_and_reaped'] is True and row['wait_calls'], 'actual wait/reap')
        for wait in row['wait_calls']:
            require(wait['called'] == 'Popen.wait', 'wait API');number(wait['returned_exit_code'], code, 'wait exit')
        for key in ('forced_terminate', 'forced_kill', 'signals_to_preexisting_processes'):
            require(row[key] is False, key)
        require(row['quit_sent'] is (code == 0), 'quit applies only to live process')
        if code == 0:
            require(row['quit_acknowledged'] is True and row['quit_error'] is None, 'acknowledged normal quit')
        else:
            require(row['quit_not_applicable'] == 'already-exited', 'fatal exit before cleanup')
        number(row['counters']['cont'], 0, 'no cont')
        require(row['unexpected_qtest_drain_lines'] == [] and row['irq_events'] == [], 'quiet protocol drain')
        for channel in ('qmp', 'qtest'):
            item = row['drains'][channel]
            unopened = name in {x[0] for x in ADMISSION}
            require(bool(item.get('not_opened')) is unopened, 'only admission failures have unopened channel')
            if not unopened:
                require(item['terminal'] in (('eof',) if code == 0 else ('eof', 'reset')), 'actual channel terminal')
                require(item['error'] is None, 'no drain error')
        for stream in ('qemu.stdout', 'qemu.stderr'):
            item = row['stdio'][stream]
            require(item['complete_after_actual_wait'] is True, 'complete stdio after wait')
            require(type(item['bytes']) is int and item['bytes'] >= 0, 'stdio size');digest(item['sha256'])


def rejected(value, word, diagnostic):
    require(value['passed'] is True, 'negative scenario passed')
    number(value['exit_code'], 1, 'fatal VMState rejection')
    require(value['specific_diagnostic'] == diagnostic and value['specific_diagnostic_observed'] is True, 'exact guard mismatch')
    observation(value['preload'], word)
    require(value['counters_before_incoming'] == value['preload']['counters_after'] == value['counters_after_exit'], 'no postload commands')
    for key in ('no_device_or_ram_commands', 'no_cont'):
        require(value[key] is True, key)
    for key in ('failed_process_postload_qom_claimed', 'whole_machine_ram_not_loaded_claimed'):
        require(value[key] is False, 'local-device rejection scope')
    digest(value['stderr_sha256'])


def judge_spmi_debug_fuse_details(case, data):
    if case.name != NAME:
        return
    for key in ('failure', 'error', 'traceback', 'blocked', 'cleanup_failure'):
        require(key not in data, 'unexpected ' + key)
    require([r['name'] for r in data['checks']] == list(CHECKS), 'complete ordered scenario matrix')
    require(all(r['passed'] is True for r in data['checks']), 'all individual scenarios passed')
    cleanup(data['process_cleanup'])
    digest(data['qemu_sha256_before']);require(data['qemu_sha256_after'] == data['qemu_sha256_before'], 'same binary')
    for row, word in zip(data['checks'][:5], (None, None, A, B, POLICY)):
        observation(row['pure'], word)
        wanted = [[BASE, BASE + 15, TYPE + '.decode-guard']] if word is not None else []
        require(row['mapping']['active_leaves'] == wanted, 'exact enabled mapping')
        number(row['mapping']['decode_guard_bytes'], 16 if word is not None else 0, 'mapped size')
        number(row['mapping']['readable_bytes'], 4 if word is not None else 0, 'readable size')
        require(row['reset_preserved_configuration'] is True, 'reset retains configuration')
        if word is None:
            require(len(row['unmapped']) == 6, 'off aperture unmapped')
            for item, (offset, write) in zip(row['unmapped'], [(o, w) for o in (0, 8, 12) for w in (False, True)]):
                unmapped(item, BASE + offset, write)
        else:
            read(row['first_read'], word)
    require(data['checks'][4]['virtual_policy_fixture_only'] is True, 'virtual policy scope')
    read(data['checks'][4]['reset_read'], POLICY)
    imm = data['checks'][2]['immutable_properties']
    require([(x['path'], x['property']) for x in imm] == [
        (PATH, 'enabled'), (PATH, 'fuse-word'), (PATH, 'bound-word'),
        ('/machine', 'spmi-debug-fuse-enabled'), ('/machine', 'spmi-debug-fuse-word')], 'five immutable properties')
    for row in imm:
        require(row['state_unchanged'] is True and row['error']['class'] == 'GenericError' and
                row['property'] in row['error']['desc'], 'immutable property rejection')
    neighbors = data['checks'][2]['unmapped_neighbors_and_debug_core']
    require(len(neighbors) == 5, 'neighbors and absent debug controller')
    for row, (address, write) in zip(neighbors, ((BASE-4, False), (BASE-4, True), (BASE+16, False), (BASE+16, True), (0x06b0f000, False))):
        unmapped(row, address, write)
    accesses = [(False,w,o) for w,o in [('b',8),('w',8),('q',8),('l',0),('l',4),('l',12),('l',9),('l',10),('l',11),('q',1)]]
    accesses += [(True,w,o) for w,o in [('b',8),('w',8),('l',8),('q',8),('l',0),('l',4),('l',12),('l',9),('l',10),('l',11),('q',1)]]
    require(len(data['access_rejections']) == len(accesses), 'all 21 invalid accesses')
    for row, (write,width,offset) in zip(data['access_rejections'], accesses):
        size = {'b':1,'w':2,'l':4,'q':8}[width];operation = 'write' if write else 'read'
        value = 0x12345678abcdef90 & ((1 << (8*size))-1)
        command = f'{operation}{width} {BASE+offset:#x}' + (f' {value:#x}' if write else '')
        require(row['command'] == command, 'exact invalid access order/width/address')
        require(row['diagnostic'] == [f'{TYPE}: rejected {operation} offset={offset:#x} size={size}'], 'exact single device diagnostic')
        state(row['before'], A);state(row['after'], A);read(row['preserved_read'], A)
        require(row['state_unchanged'] is True and row['qtest_ok_is_memtx_result'] is False and
                row['read_error_bytes_used_as_identity'] is False, 'atomic rejected access')
    require(len(data['admission_rejections']) == len(ADMISSION), 'all admission negatives')
    for row, (name, enabled, word) in zip(data['admission_rejections'], ADMISSION):
        require(row['name'] == name and row['enabled'] is enabled and row['word'] == word and row['passed'] is True, 'admission input')
        number(row['exit_code'], 1, 'failed admission exit')
        message = (TYPE + ': enabled profile requires explicit fuse-word=0xhhhhhhhh '
                   '(eight lowercase hex digits); no word is bound by default') if enabled else TYPE + ': fuse-word requires enabled=on'
        require(row['specific_diagnostic'] == message and row['specific_diagnostic_observed'] is True, 'admission diagnostic')
    require([x['name'] for x in data['migrations']] == list(NORMAL[5:]), 'three same-profile roundtrips')
    for row, word, key in zip(data['migrations'], (None,A,B), ('off','a','b')):
        source, target = row['source'], row['observation'];state(source['snapshot'], word)
        require(source['file'] == key+'.mig' and source['query_migrate']['status'] == 'completed', 'complete source migration')
        require(source['query_status']['status'] == 'postmigrate' and source['query_status']['running'] is False, 'source stopped')
        section = source['section'];payload = struct.pack('>II', 0x53444600 | (word is not None), word or 0)
        require(section['section_name'] == TYPE, 'section name')
        for field, val in (('section_type',4),('instance_id',0),('version',1),('payload_bytes',8),('mutable_bytes',0)):
            number(section[field], val, 'VMState '+field)
        require(type(section['payload_offset']) is int and section['payload_offset'] >= 8, 'stream payload offset')
        require(section['payload_hex'] == payload.hex() and section['payload_sha256'] == hashlib.sha256(payload).hexdigest(), 'all eight wire bytes')
        require(section['all_eight_bytes_verified'] is True and section['whole_machine_payload_oracle_claimed'] is False, 'wire oracle scope')
        require(target['query_migrate']['status'] == 'completed' and target['query_status']['running'] is False, 'completed stopped target')
        observation(target['preload'], word);observation(target['first_postload'], word)
        require(target['reset_preserved_configuration'] is True and target['whole_machine_ram_not_loaded_claimed'] is False, 'postload reset/scope')
    pairs = [('off-to-on',A,'off.mig','53444601 != 53444600'), ('on-to-off',None,'a.mig','53444600 != 53444601'),
             ('a-to-b',B,'a.mig',f'{B:x} != {A:x}'), ('b-to-a',A,'b.mig',f'{A:x} != {B:x}')]
    require(len(data['rejected_migrations']) == 4, 'four cross-profile rejects')
    for row, (name,word,file,message) in zip(data['rejected_migrations'], pairs):
        require(row['name'] == name and row['source_file'] == file, 'cross-profile scenario');rejected(row, word, message)
    require(len(data['corrupted_stream_rejections']) == 2, 'both equal fields corrupted')
    source = data['migrations'][1]['source']
    for row, (field,delta,old) in zip(data['corrupted_stream_rejections'], (('profile',0,0x53444601),('word',4,A))):
        require(row['name'] == 'corrupt-'+field and row['field'] == field, 'single-field corruption')
        number(row['field_offset'], source['section']['payload_offset']+delta, 'exact field offset')
        number(row['changed_bits'], 1, 'one-bit corruption')
        require(row['source_unchanged'] is True and row['source_sha256'] == source['sha256'], 'original stream preserved')
        digest(row['mutated_sha256']);require(row['mutated_sha256'] != row['source_sha256'], 'changed stream digest')
        rejected(row, A, f'{old:x} != {old ^ 1:x}')
    streams = data['actual_stream_files']
    require(len(streams) == 5 and {r['file'] for r in streams} == {'streams/'+n+'.mig' for n in ('off','a','b','corrupt-profile','corrupt-word')}, 'five complete stream files')
    for row in streams:
        require(type(row['bytes']) is int and row['bytes'] > 8, 'whole stream size');digest(row['sha256'])
    stream_map = {r['file']:r for r in streams}
    for row in data['migrations']:
        source = row['source'];saved = stream_map['streams/'+source['file']]
        require(source['bytes'] == saved['bytes'] and source['sha256'] == saved['sha256'], 'source stream identity')
    for row in data['corrupted_stream_rejections']:
        saved = stream_map['streams/'+row['name']+'.mig']
        require(saved['sha256'] == row['mutated_sha256'] and saved['bytes'] == stream_map['streams/a.mig']['bytes'], 'full mutated stream identity')
