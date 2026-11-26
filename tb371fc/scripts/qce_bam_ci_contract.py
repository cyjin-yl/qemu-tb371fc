#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Additional strict CI verdict checks for QCE receipts only."""
import re

NAMES = {'qce-bam-source-generation', 'qce-bam-safety', 'qce-bam-reference'}
SOURCE_NEGATIVE_COUNTS = {
    'profile-policy': 36, 'profile-json': 3,
    'complete-input-identity': 24, 'complete-input-container': 3,
    'parser-fixture': 42, 'integer-expression': 12,
    'derived-profile-comparison': 7,
}


def require(test, message):
    if not test:
        raise ValueError('QCE contract: ' + message)


def number(actual, expected, label):
    require(type(actual) is int and actual == expected, label)


def boolean(actual, expected, label):
    require(actual is expected, label)


def observation(row):
    # This imports only the independent external test contract, never QEMU C.
    import sys
    from pathlib import Path
    tests = str(Path(__file__).resolve().parents[1] / 'tests')
    if tests not in sys.path:
        sys.path.insert(0, tests)
    from qce_bam_test_contract import OBSERVATION_FIELDS
    require(isinstance(row, dict), 'observation object')
    number(row.get('first_getters'), 70, 'first 70 pure getters')
    number(row.get('repeated_getters'), 70, 'repeated 70 pure getters')
    first, repeated = row.get('first'), row.get('repeated')
    require(isinstance(first, dict) and set(first) == set(OBSERVATION_FIELDS) and first == repeated,
            'complete repeated pure state')
    for stage in ('counters_before', 'counters_after'):
        counts = row.get(stage, {})
        for field in ('memory', 'device_mmio', 'ram', 'cont'):
            number(counts.get(field), 0, 'pure boundary ' + stage + '.' + field)
    number(row['counters_after']['qom_get'] - row['counters_before']['qom_get'], 140,
           'two observation deltas')
    boolean(row.get('before_first_device_or_ram_access'), True, 'pure memory boundary')
    boolean(row.get('before_any_cont'), True, 'pure execution boundary')


def judge_qce_details(case, data):
    if case.name not in NAMES:
        return
    for field in ('failure', 'error', 'traceback', 'blocked'):
        require(field not in data, 'unexpected ' + field)
    if case.name == 'qce-bam-source-generation':
        names, categories = set(), {}
        for item in data.get('negative_checks', []):
            require(isinstance(item, dict) and item.get('rejected') is True, 'source negative rejected')
            name = item.get('name')
            require(isinstance(name, str) and name and name not in names, 'unique source negative')
            require(isinstance(item.get('rejection_reason'), str) and item['rejection_reason'], 'negative reason')
            names.add(name)
            category = item.get('category')
            require(category in SOURCE_NEGATIVE_COUNTS, 'source negative category')
            categories[category] = categories.get(category, 0) + 1
            if category == 'parser-fixture':
                boolean(item.get('fixture_only_repin'), True, 'parser fixture identity is explicit')
                boolean(item.get('production_pins_modified'), False, 'parser fixture cannot change production pins')
                boolean(item.get('production_generation_invoked'), False, 'parser fixture cannot emit production output')
        count = 127
        number(len(names), count, 'complete source negative matrix')
        number(data.get('negative_count'), count, 'negative_count')
        require(categories == SOURCE_NEGATIVE_COUNTS, 'complete source negative categories')
        require(data.get('negative_category_counts') == SOURCE_NEGATIVE_COUNTS,
                'reported source negative category counts')
        boolean(data.get('profile_bound'), True, 'source profile bound')
        require(data.get('reference_scope') == 'cold-control-only', 'scope')
        boolean(data.get('public_source_pins_verified'), True, 'full input pins')
        boolean(data.get('all_source_identities_verified_before_source_parse'), True,
                'source identity must precede parsing')
        boolean(data.get('production_pins_modified_by_tests'), False, 'production source pins unchanged')
        number(len(data.get('inputs', [])), 12, 'complete primary source count')
        require(data['inputs'] == data.get('profile', {}).get('document', {}).get('source_inputs'),
                'report inputs equal reviewed profile pins')
        number(sum(item['bytes'] for item in data['inputs']), 770745, 'complete primary source bytes')
        positives = data.get('parser_positive_checks', [])
        number(len(positives), 2, 'two selected-scope parser positive controls')
        require(len({x.get('name') for x in positives}) == 2 and
                all(x.get('passed') is True for x in positives), 'parser positives passed independently')
        for item in positives:
            boolean(item.get('fixture_only_repin'), True, 'positive parser fixture identity')
            boolean(item.get('production_pins_modified'), False, 'positive parser fixture retains production pins')
        return
    reference = case.name == 'qce-bam-reference'
    profile = data.get('profile', {})
    require(type(profile.get('bound')) is bool, 'profile bool')
    if reference:
        boolean(profile.get('bound'), True, 'complete reference cannot be UNBOUND')
    cleanup = data.get('process_cleanup', [])
    number(len(cleanup), 76 if reference else 4, 'complete process count')
    names, exits = set(), {0: 0, 1: 0}
    for row in cleanup:
        name, expected = row.get('fixture'), row.get('expected_exit_code')
        require(isinstance(name, str) and name and name not in names, 'unique process fixture')
        names.add(name)
        require(type(expected) is int and expected in (0, 1), 'expected exit integer')
        number(row.get('exit_code'), expected, 'actual exit')
        exits[expected] += 1
        boolean(row.get('actually_waited_and_reaped'), True, 'actual wait/reap')
        waits = row.get('wait_calls')
        require(isinstance(waits, list) and waits, 'actual wait calls')
        for wait in waits:
            require(wait.get('called') == 'Popen.wait', 'wait API')
            number(wait.get('returned_exit_code'), expected, 'wait return')
        for field in ('forced_terminate', 'forced_kill', 'signals_to_preexisting_processes'):
            boolean(row.get(field), False, field)
        if expected == 0:
            boolean(row.get('quit_sent'), True, 'normal QMP quit sent')
            boolean(row.get('quit_acknowledged'), True, 'normal QMP quit acknowledged')
            require(row.get('quit_error') is None, 'normal quit error')
        else:
            boolean(row.get('quit_sent'), False, 'fatal process already exited')
            require(row.get('quit_not_applicable') == 'already-exited', 'fatal quit reason')
        number(row.get('counters', {}).get('cont'), 0, 'no guest execution')
        require(row.get('unexpected_qtest_drain_lines') == [], 'qtest drain protocol')
        require(not any(x.get('action') == 'raise' for x in row.get('irq_events', [])), 'no completion IRQ')
        for channel in ('qmp', 'qtest'):
            terminal = row.get('drains', {}).get(channel, {})
            if terminal.get('not_opened'):
                require(not reference, 'reference process channels connected')
                require(name in ('paused-tcg-pure-observation', 'unbound-enable-fails-closed'),
                        'unopened channel only in explicit startup/TCG case')
            else:
                require(terminal.get('terminal') in (('eof',) if expected == 0 else ('eof', 'reset')),
                        'observed protocol EOF/drain')
                require(terminal.get('error') is None, 'drain error')
        for stream in ('qemu.stdout', 'qemu.stderr'):
            item = row.get('stdio', {}).get(stream, {})
            boolean(item.get('complete_after_actual_wait'), True, 'stdio completed after wait')
            require(type(item.get('bytes')) is int and item['bytes'] >= 0, 'stdio byte count')
            require(isinstance(item.get('sha256'), str) and re.fullmatch(r'[0-9a-f]{64}', item['sha256']),
                    'complete stdio digest')
    required_exits = ({0: 13, 1: 63} if reference else
                      {0: 4, 1: 0} if profile['bound'] else {0: 3, 1: 1})
    require(exits == required_exits, 'normal/fatal scenario process counts')
    if not reference:
        return
    for item in data['migrations']:
        require(item['source']['query_migrate']['status'] == 'completed', 'source migration completed')
        number(item['source']['section']['payload_bytes'], 248, 'full wire bytes')
        number(item['source']['section']['section_type'], 4, 'complete ordinary VMState section')
        number(item['source']['section']['guard_bytes'], 116, 'guard wire bytes')
        boolean(item['source']['section']['all_payload_bytes_match_pure_qom'], True, 'full payload oracle')
        observed = item['observation']
        require(observed['query_migrate']['status'] == 'completed', 'target migration completed')
        boolean(observed['query_status']['running'], False, 'paused migration target')
        observation(observed['preload'])
        observation(observed['first_postload'])
        require(observed['first_postload']['first'] == item['source']['snapshot'], 'source/target equality')
        boolean(observed['whole_machine_ram_not_loaded_claimed'], False, 'scope of observation')
    for category in ('rejected_migrations', 'corrupted_stream_rejections', 'structural_stream_rejections'):
        names = set()
        for item in data[category]:
            require(item.get('name') not in names, 'unique rejection')
            names.add(item.get('name'))
            number(item.get('exit_code'), 1, 'fatal migration rejection')
            boolean(item.get('specific_diagnostic_observed'), True, 'specific migration diagnostic')
            boolean(item.get('no_device_or_ram_commands'), True, 'rejection no memory')
            boolean(item.get('no_cont'), True, 'rejection no cont')
            boolean(item.get('failed_process_postload_qom_claimed'), False, 'no dead-process observation claim')
            observation(item.get('preload'))
            require(item.get('counters_before_incoming') == item.get('counters_after_exit'),
                    'rejection counters unchanged')
            if category == 'corrupted_stream_rejections':
                number(item.get('changed_bits'), 1, 'single-bit negative')
                boolean(item.get('source_unchanged'), True, 'original stream preserved')
