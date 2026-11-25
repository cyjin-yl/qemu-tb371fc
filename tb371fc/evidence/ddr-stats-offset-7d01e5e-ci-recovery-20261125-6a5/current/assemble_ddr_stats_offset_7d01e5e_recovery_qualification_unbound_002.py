#!/usr/bin/env python3
"""Assemble new read-only recovery qualification for the same immutable DDR CI artifact.

The original local qualifier and actual audit receipts were lost with the executor.
This new source uses exact public predecessors and fresh complete evidence. It
cannot qualify from recorded hash strings or old PASS reports alone. Binding is
separate and reviewed; an unbound source refuses before reading any evidence.
"""
from pathlib import Path, PurePosixPath
import argparse
import datetime
import hashlib
import json
import re

if not __debug__:
    raise SystemExit("Qualification refuses optimized Python: required assertions must remain active")

BINDING_SHA256 = 'UNBOUND_RECOVERY_BINDING_SHA256'
if re.fullmatch('[0-9a-f]{64}', BINDING_SHA256) is None:
    raise SystemExit('Recovery qualification binding is UNBOUND; no evidence read and no qualification written')

HEAD = '7d01e5e294b788f97a8120c2d34122ddb25e6343'
TREE = '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4'
SOURCE_PARENT = BASE_HEAD = '420819df9cfd76dc98cb4705987c269047d7f214'
PARENT_TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'
OBSERVED_DEFAULT_HEAD = 'b5819654a07b2048bb1088cb0c7dfe3625d474ef'
OBSERVED_DEFAULT_TREE = '8571f7d3b099dcb02e29a0e84ed4503ba9ebdb2e'
DOCUMENTATION_COMMIT = 'cfec387558972fdd82bb166a913d18e4e11da100'
MERGE = 'da1fd3e190faeb5e2cf4dfb3634c54c18987fae8'
PR_NUMBER, PUSH_RUN_ID, PR_RUN_ID = 38, 45544186469, 45544202260
PUSH_BOARD_JOB, PUSH_A77_JOB, PR_BOARD_JOB, PR_A77_JOB = 138969695072, 138969695369, 138969745149, 138969744777
ARTIFACT_ID = 14991304675
ZIP_BYTES, QEMU_BYTES = 57340476, 63727664
ZIP_SHA = '1e6c37d205a9e130e4f197c068c93c756488b6b579ab06eadd429190b9b0bd6f'
QEMU_SHA = '935549d9f359d04254f5627bdd8807fd8d1800ab2223ab52dea80c0cf7edecb6'
QEMU_MEMBER = 'qemu-build/qemu-system-aarch64'
HISTORICAL_RPM_HEAD = '66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b'
HISTORICAL_FAILED_HEAD = '75cc1dc83600784afa06e81c9998d8d39aba6af2'
SOURCE_ROOT = Path('/dev/shm/ddrstats-candidate-6a5')
SOURCE_EVIDENCE = SOURCE_ROOT/'tb371fc/evidence/ddr-stats-offset-20261125-6a5'
RPM_GENERATED_CONTRACT = {'source_input_count': 4, 'register_count': 1, 'window_count': 1,
                          'mapped_bytes': 4, 'physical_base': 0x0c3f0004, 'offset_value': 0x000f0048}
DDR_GENERATED_CONTRACT = {'source_input_count': 4, 'register_count': 1, 'window_count': 1,
                          'mapped_bytes': 4, 'physical_base': 0x0c3f001c, 'offset_value': 0x000f00b8}
AUDIT_NAMES = ['integration', 'ddr_stats_offset', 'rpm_stats_offset', 'spss', 'llcc', 'dp', 'dsi', 'dispcc']
ASSET_NAMES = [
    'github_metadata', 'pr_base_identity', 'artifact_api', 'artifact_download',
    'a77_observer', 'a77_receipt', 'board_observer', 'board_receipt', 'ufs_observer', 'ufs_receipt',
    'source_recovery', 'dependency_recovery',
    'historical_rpm_qualification', 'historical_rpm_native', 'historical_failed_ci',
    'historical_include_correction', 'historical_include_review',
    'historical_rpm_local_initial', 'historical_rpm_local_initial_stderr',
    'historical_rpm_local_actual', 'historical_rpm_local_actual_stderr',
    'historical_runner_source_review', 'historical_runner_template_review',
    'historical_prior_spss_qualification', 'historical_earlier_spss_qualification',
    'new_ddr_derivation', 'new_ddr_source_validation', 'new_ddr_independent_review', 'new_aggregate_review',
    'source_materialization_failure', 'source_derivation_failure',
    'default_ref_advance_proof',
    'default_ref_advance_raw',
    'failed_qualification_execution',
    'failed_qualification_stdout',
    'failed_qualification_stderr',
    'failed_qualification_independent_observation',
]
FIXED_ASSET_IDENTITIES = {
    'default_ref_advance_proof': (3040, '7807ca018aa4dbaa251f02cb36c7fa02bf5292b5f2d29a15d1c2a033e58c57fc'),
    'default_ref_advance_raw': (157269, 'dd80202e1dd5ef0e1cda5fc51695cb365b61f7f768b42953e6733b736e4f64c9'),
    'failed_qualification_execution': (1103, 'a83e04ba10ab5f32eb709fc57930103eda636313d588026cf0a72a4658106d47'),
    'failed_qualification_stdout': (0, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
    'failed_qualification_stderr': (351, '916e692c50a3dfdf9242da1581209349dd7864e09f4eb24931c03f2eda9b86f0'),
    'failed_qualification_independent_observation': (2839, '103730ee9e9deb644823b502f1601e301c3721d7ed7e7b9f3409bd703cc1a449'),
    'source_recovery': (48999, '63f5efb9a14a6b82d3be01849a9d32360864fd32068a50ce1fdd67bd115ada68'),
    'dependency_recovery': (1176, 'c075d77ce31fad923b4c5ab9e2d2ca390daa87f4858bfd4d7a4c9fa9294d07c9'),
    'historical_rpm_qualification': (31589, '4272efca70e43f31de1ed0ef9db20e3478d8157b64fbb44f84fa853dabf576d6'),
    'historical_rpm_native': (20263, '18c4cfb5554c3286531f6aa3a5694e92ff36896471fac63e7002c02b3de0a366'),
    'new_ddr_derivation': (27500, '5f4193bdcc93346606ed48d0e794e9ee2eb63cf6818224269cc126da8c03f80e'),
    'new_ddr_source_validation': (5282, '9a296acb3f955c790f65a8710b0c9d30cd39d740ddccf04c984d5576d0f20b5f'),
    'new_ddr_independent_review': (16363, '9c4f8aa5e9a93ce9b9180d81a502e1a72ac516fbb3cf58cf848775f19f7d28c6'),
    'new_aggregate_review': (3714, 'fb43132599e2a56b47bb0234e812cd2838b971d58e134ccef92609dc17efa4d4'),
    'source_materialization_failure': (411, 'cb8ed6e2b939ca5b57c091fa3c0a25c3655e2ebcfdf414bac6e0d0929137774e'),
    'source_derivation_failure': (1086, '541ed1babaabe18424fb4027532cae55947060bc5e9ee4a5030ff6c5ae0cc61c'),
}
HISTORICAL_HASHES = {
    'historical_failed_ci': '0ef5b40ffe9fb06aba132c310bc8dabf94aabcb708a9f3388c8f7b0484878a6a',
    'historical_include_correction': 'c83c59a3d4076007c26a02b39e21707bbe42fe9a994fe8e327541cc4a9fd4468',
    'historical_include_review': '2ded18bc3ddb68f4960c707fbbd0b345af3887ff3f81173f5d32606f2c22a44c',
    'historical_rpm_local_initial': '865984016a4bf612a93203e0728d9ec1a619a88e478b61a32ef68146c7176739',
    'historical_rpm_local_initial_stderr': '614fb81526194c64cd5944903501ef47b541523f31a36c0ad3f5c6b8c202a1b6',
    'historical_rpm_local_actual': '8fd999774fba3548d99a2a58326ce4f697f21e61ed0b8ebf8f142444e9a38273',
    'historical_rpm_local_actual_stderr': 'e266c0df006bda9b3035bd24b2b9216d258589e2e3efa85b5dcaa6f12db5de64',
    'historical_runner_source_review': 'a3583e1cce51b346ec8c952f8d3bc95613dd1f2d004da6b9bbb7854b6faf4d73',
    'historical_runner_template_review': '65079790c5026729b3010c862ef98c883ca3221876e5688df63605efe85ad2db',
    'historical_prior_spss_qualification': '0a52a79598ff4f57e1697b7e8327d5229b3b2491e8947f7142c1e2bbc97d5206',
    'historical_earlier_spss_qualification': '41dd65062d5be0e68c8f7d0ad2cdb404fc6419a2920e5ed7f1f61a755b5922f9',
}

def unique_pairs(pairs):
    out = {}
    for key, value in pairs:
        assert key not in out, ('duplicate JSON key', key)
        out[key] = value
    return out

def read(path):
    def nonfinite(value):
        raise ValueError('Nonfinite JSON: ' + value)
    return json.loads(Path(path).read_bytes(), object_pairs_hook=unique_pairs, parse_constant=nonfinite)

def pin(path, expected=None):
    path = Path(path)
    assert path.is_file() and not path.is_symlink(), str(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        assert type(expected) is str and re.fullmatch('[0-9a-f]{64}', expected)
        assert digest == expected, (str(path), digest, expected)
    return {'path': str(path.resolve()), 'bytes': len(raw), 'sha256': digest}

def checked_spec(spec):
    assert type(spec) is dict and set(spec) == {'path', 'bytes', 'sha256'}
    assert type(spec['path']) is str and Path(spec['path']).is_absolute()
    assert type(spec['bytes']) is int and spec['bytes'] >= 0
    actual = pin(spec['path'], spec['sha256'])
    assert actual['bytes'] == spec['bytes']
    return actual

def exact(value, expected, label):
    assert type(value) is type(expected), (label, type(value), type(expected))
    if type(expected) is dict:
        assert set(value) == set(expected), (label, set(value), set(expected))
        for key in expected:
            exact(value[key], expected[key], label + '.' + str(key))
    elif type(expected) is list:
        assert len(value) == len(expected), label
        for index, (a, b) in enumerate(zip(value, expected)):
            exact(a, b, label + '.' + str(index))
    else:
        assert value == expected, (label, value, expected)

def validate_documentation_advance(refs, gh):
    """Verify the observed mutable reference without treating it as the CI source."""
    proof = read(asset_path('default_ref_advance_proof'))
    raw = read(asset_path('default_ref_advance_raw'))
    assert proof['schema'] == 'tb371fc-default-ref-documentation-advance-v1' and proof['passed'] is True
    expected = {
        'immutable_CI_source_head': HEAD, 'immutable_CI_source_tree': TREE,
        'immutable_CI_PR_base': BASE_HEAD, 'immutable_CI_PR_base_tree': PARENT_TREE,
        'observed_default_head': OBSERVED_DEFAULT_HEAD, 'observed_default_tree': OBSERVED_DEFAULT_TREE,
        'default_commit_parents': [BASE_HEAD, DOCUMENTATION_COMMIT],
        'evidence_commit': DOCUMENTATION_COMMIT, 'evidence_commit_parent': BASE_HEAD,
        'default_commit_time': '2026-11-25T04:33:05Z', 'evidence_commit_time': '2026-11-25T04:32:03Z',
        'tree_objects_recomputed': 7, 'unchanged_file_or_subtree_entries': 235,
        'added_file_count': 3, 'modified_files': 0, 'deleted_files': 0,
        'all_changes_are_three_public_evidence_documents': True,
        'executable_model_runner_workflow_changes': False,
        'original_PR_source_or_CI_merge_changed': False, 'current_main_is_qualified_source_claimed': False,
        'qualification_scope_is_exact_original_artifact': True,
        'failed_qualification_not_reclassified_as_pass': True,
        'CI_rerun': False, 'QEMU_executed': False, 'native_executed': False,
    }
    for key, value in expected.items():
        exact(proof[key], value, 'documentation-advance.' + key)
    exact(proof['raw_evidence'], asset_pins['default_ref_advance_raw'], 'documentation-advance.raw identity')
    assert raw['default_ref']['ref'] == refs['main_ref']['ref'] == 'refs/heads/tb371fc'
    assert raw['default_ref']['object']['type'] == refs['main_ref']['object']['type'] == 'commit'
    assert raw['default_ref']['object']['sha'] == refs['main_ref']['object']['sha'] == OBSERVED_DEFAULT_HEAD
    for key, commit, tree, parents in [
        ('default_merge', OBSERVED_DEFAULT_HEAD, OBSERVED_DEFAULT_TREE, [BASE_HEAD, DOCUMENTATION_COMMIT]),
        ('evidence_commit', DOCUMENTATION_COMMIT, OBSERVED_DEFAULT_TREE, [BASE_HEAD]),
        ('source_base_commit', BASE_HEAD, PARENT_TREE, None),
    ]:
        row = raw[key]
        assert row['sha'] == commit and row['tree']['sha'] == tree
        if parents is not None:
            exact([p['sha'] for p in row['parents']], parents, 'documentation-advance.' + key + '.parents')
    assert raw['default_merge']['committer']['date'] == proof['default_commit_time']
    assert raw['evidence_commit']['committer']['date'] == proof['evidence_commit_time']
    parse_time = lambda value: datetime.datetime.fromisoformat(value.replace('Z', '+00:00'))
    assert parse_time(proof['evidence_commit_time']) > max(parse_time(gh[role]['run']['updated_at']) for role in ['push', 'pr'])
    assert parse_time(proof['default_commit_time']) > parse_time(proof['evidence_commit_time'])

    # Recompute the complete Git tree objects and then compare every changed
    # subtree. Identical subtrees keep their immutable identities; no unseen
    # subtree is assumed to have unchanged contents merely from a PASS flag.
    trees = raw['trees']
    assert type(trees) is dict and len(trees) == 7
    for identity, tree in trees.items():
        assert tree['sha'] == identity and tree['truncated'] is False
        rows = tree['tree']
        assert type(rows) is list and len({r['path'] for r in rows}) == len(rows)
        encoded = bytearray()
        for row in sorted(rows, key=lambda r: (r['path'] + ('/' if r['type'] == 'tree' else '')).encode()):
            assert '/' not in row['path'] and '\0' not in row['path']
            assert re.fullmatch('[0-9a-f]{40}', row['sha'])
            assert row['mode'] in ['040000', '100644', '100755', '120000', '160000']
            encoded += row['mode'].lstrip('0').encode() + b' ' + row['path'].encode() + b'\0' + bytes.fromhex(row['sha'])
        digest = hashlib.sha1(b'tree ' + str(len(encoded)).encode() + b'\0' + encoded).hexdigest()
        assert digest == identity, ('documentation-advance actual Git tree', identity, digest)
    delta, unchanged = [], 0
    def walk(previous, current, prefix=''):
        nonlocal unchanged
        before = {r['path']: r for r in trees[previous]['tree']} if previous is not None else {}
        after = {r['path']: r for r in trees[current]['tree']} if current is not None else {}
        for name in sorted(set(before) | set(after)):
            a, b = before.get(name), after.get(name)
            path = prefix + name
            if a is not None and b is not None and all(a[k] == b[k] for k in ['mode', 'type', 'sha']):
                unchanged += 1
            elif (a is None or a['type'] == 'tree') and (b is None or b['type'] == 'tree'):
                walk(a['sha'] if a is not None else None, b['sha'] if b is not None else None, path + '/')
            else:
                delta.append({'path': path, 'previous': a, 'current': b})
    walk(PARENT_TREE, OBSERVED_DEFAULT_TREE)
    assert unchanged == 235
    exact(delta, proof['added_files'], 'documentation-advance complete independently compared delta')
    exact(raw['reported_delta'], {'diff': delta, 'same': unchanged}, 'documentation-advance recorded versus recomputed delta')
    expected_added = {
        'tb371fc/evidence/rpm-stats-8e8eeb-native-20261125/README.md': (10895, '2dda3fba351e9290bfe53ec693838bf6194102a7'),
        'tb371fc/evidence/rpm-stats-8e8eeb-native-20261125/manifest.json': (15800, '8f136785976cdfa4c53cabf535ce4c44f629438f'),
        'tb371fc/evidence/rpm-stats-8e8eeb-native-20261125/native-audit-completion.json': (29270, '806e2516f09b9b559540d0b712867dff17bcf13a'),
    }
    assert len(delta) == 3 and {r['path'] for r in delta} == set(expected_added)
    for row in delta:
        assert row['previous'] is None
        assert row['current']['type'] == 'blob' and row['current']['mode'] == '100644'
        assert (row['current']['size'], row['current']['sha']) == expected_added[row['path']]
    return {
        'proof': asset_pins['default_ref_advance_proof'], 'raw_evidence': asset_pins['default_ref_advance_raw'],
        'observed_default_ref': 'refs/heads/tb371fc', 'observed_default_head': OBSERVED_DEFAULT_HEAD,
        'observed_default_tree': OBSERVED_DEFAULT_TREE, 'immutable_PR_base': BASE_HEAD,
        'immutable_qualified_head': HEAD, 'immutable_qualified_tree': TREE,
        'documentation_commit': DOCUMENTATION_COMMIT, 'documentation_added_after_completed_CI': True,
        'actual_Git_tree_objects_recomputed': 7, 'unchanged_file_or_subtree_entries': unchanged,
        'exact_added_paths': sorted(expected_added), 'modified_files': 0, 'deleted_files': 0,
        'executable_model_runner_workflow_changes': False, 'original_PR_source_or_CI_merge_changed': False,
        'PR_state_observed': 'open', 'PR_draft_observed': True, 'PR_merged_observed': False,
        'current_default_head_is_qualified_source': False, 'current_default_head_CI_or_mergeability_claimed': False,
    }


def retain_failed_qualification():
    failure = read(asset_path('failed_qualification_execution'))
    assert failure['schema'] == 'tb371fc-ddr-recovery-qualification-execution-v1'
    assert failure['exit_code'] == 1 and failure['qualification'] is None
    for key in ['CI_rerun', 'QEMU_executed', 'native_executed', 'original_lost_qualification_recreated']:
        assert failure[key] is False
    assert failure['argv'][1:4] == ['-I', '-S', '-B']
    assert failure['argv'][5] == '--binding' and len(failure['argv']) == 7
    old_source = pin(failure['argv'][4], '41f801c970a26e3e6e161d919c6922c71569a74caf32723f63c2a4616b1ae5e6')
    old_binding = pin(failure['argv'][6], '1d7854e9c0fc0a9981fa2d80256def314dd31053ff969e91bcdf52aec057ba14')
    assert old_source['bytes'] == 50775 and old_binding['bytes'] == 12413
    exact(failure['logs'], [asset_pins['failed_qualification_stdout'], asset_pins['failed_qualification_stderr']],
          'failed qualification complete retained logs')
    assert asset_path('failed_qualification_stdout').read_bytes() == b''
    assert "assert refs['main_ref']['object']['sha'] == refs['pr']['base']['sha'] == BASE_HEAD" in asset_path('failed_qualification_stderr').read_text()
    observation = read(asset_path('failed_qualification_independent_observation'))
    assert observation['schema'] == 'tb371fc-ddr-qualification-main-ref-failure-independent-observation-v1'
    assert observation['mutable_main_ref_observed'] == OBSERVED_DEFAULT_HEAD
    assert observation['qualification_output_absent_at_review'] is True
    assert observation['assertion_failure_is_expected_for_observed_values'] is True
    assert observation['source_changed_by_reviewer'] is False and observation['qualifier_reexecuted_by_reviewer'] is False
    assert observation['old_failure_reclassified_or_erased'] is False
    return {
        'execution': asset_pins['failed_qualification_execution'],
        'stdout': asset_pins['failed_qualification_stdout'], 'stderr': asset_pins['failed_qualification_stderr'],
        'independent_observation': asset_pins['failed_qualification_independent_observation'],
        'source': old_source, 'binding': old_binding, 'exit_code': 1, 'qualification_created': False,
        'failure_reason': 'The prior guard equated the mutable default ref with the immutable CI PR base.',
        'failed_execution_reclassified_as_pass': False, 'failed_evidence_overwritten': False,
        'scope_of_correction': 'Separate the observed documentation-only default-ref advance from unchanged exact artifact and PR/CI identities.',
    }


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--binding', type=Path, required=True)
args = parser.parse_args()
binding_pin = pin(args.binding, BINDING_SHA256)
binding = read(args.binding)
assert set(binding) == {'schema', 'source_head', 'source_tree', 'artifact', 'audits', 'assets', 'output', 'recovery'}
assert binding['schema'] == 'tb371fc-ddrstats-recovery-qualification-binding-v1'
assert binding['source_head'] == HEAD and binding['source_tree'] == TREE
exact(binding['artifact'], {'id': ARTIFACT_ID, 'run_id': PUSH_RUN_ID, 'zip_bytes': ZIP_BYTES,
                          'zip_sha256': ZIP_SHA, 'qemu_bytes': QEMU_BYTES,
                          'qemu_sha256': QEMU_SHA, 'qemu_member': QEMU_MEMBER}, 'binding.artifact')
exact(binding['recovery'], {'original_lost_receipt_bytes_recreated': False,
                            'same_immutable_artifact': True,
                            'new_read_only_audit_receipts_required': True,
                            'CI_rerun': False, 'QEMU_or_native_rerun': False}, 'binding.recovery')
assert set(binding['assets']) == set(ASSET_NAMES)
assert [row['name'] for row in binding['audits']] == AUDIT_NAMES
out = Path(binding['output'])
assert out.is_absolute() and not out.exists(), 'Exclusive output must not already exist'
asset_pins = {}
for name in ASSET_NAMES:
    asset_pins[name] = checked_spec(binding['assets'][name])
    if name in FIXED_ASSET_IDENTITIES:
        assert (asset_pins[name]['bytes'], asset_pins[name]['sha256']) == FIXED_ASSET_IDENTITIES[name]
    if name in HISTORICAL_HASHES:
        assert asset_pins[name]['sha256'] == HISTORICAL_HASHES[name]

def asset_path(name):
    return Path(asset_pins[name]['path'])

audits, reports = [], {}
for spec in binding['audits']:
    assert set(spec) == {'name', 'auditor', 'receipt'}
    name = spec['name']
    auditor_pin, receipt_pin = checked_spec(spec['auditor']), checked_spec(spec['receipt'])
    obj = read(receipt_pin['path'])
    assert obj['passed'] is True and obj['auditor_sha256'] == auditor_pin['sha256']
    archive = obj.get('zip', obj.get('archive', obj.get('input')))
    assert archive['bytes'] == ZIP_BYTES and archive['sha256'] == ZIP_SHA
    head = obj.get('expected_head', obj.get('head', obj.get('component', {}).get('head')))
    assert head == HEAD
    binary = obj.get('qemu', {}).get('sha256', obj.get('component', {}).get('qemu_sha256_from_separately_audited_binary'))
    assert binary == QEMU_SHA
    for field in ('qemu_executed', 'guest_executed', 'native_android_passed'):
        if field in obj:
            assert obj[field] is False
    audits.append({'name': name, 'auditor': auditor_pin, 'receipt': receipt_pin, 'passed': True})
    reports[name] = obj

audit_by_name = {row['name']: row for row in audits}
assert len(audit_by_name) == len(audits) == 8
aggregate = reports['integration']
assert aggregate['schema'] == 'tb371fc-ddr-stats-offset-integration-independent-zip-audit-v1'
assert aggregate['components']['count'] == 99 and aggregate['linux']['count'] == 5
assert aggregate['ci_harness_unittests'] == 36
assert aggregate['qemu']['bytes'] == QEMU_BYTES and aggregate['qemu']['member'] == QEMU_MEMBER
source_gen = {}
for key, count in [('generation', 10), ('dsi_generation', 13), ('dp_generation', 17), ('llcc_generation', 8),
                   ('spss_generation', 2), ('rpm_stats_offset_generation', 4), ('ddr_stats_offset_generation', 4)]:
    row = aggregate[key]
    assert row['inputs_verified_from_actual_bytes'] == count and row['outputs_verified_from_actual_bytes'] == 2
    source_gen[key] = {k: v for k, v in row.items() if k in (
        'inputs_verified_from_actual_bytes', 'outputs_verified_from_actual_bytes',
        'committed_output_pins', 'source_revision', 'geometry_source_revision')}
for key, contract in [('rpm_stats_offset_generation', RPM_GENERATED_CONTRACT),
                      ('ddr_stats_offset_generation', DDR_GENERATED_CONTRACT)]:
    row = aggregate[key]
    exact(row['generated_contract'], contract, key + '.generated_contract')
    assert row['all_primary_git_blob_identities_verified'] is True
    assert all(row[field] is False for field in ['statistics_payload_implemented', 'native_reset_value_known',
                                               'captured_runtime_word_claimed', 'aop_execution_claimed'])
assert aggregate['ddr_stats_offset_generation']['reference_window_mapped_only_when_enabled'] is True
assert type(aggregate['ddr_stats_offset_generation']['default_off_mapped_bytes']) is int
assert aggregate['ddr_stats_offset_generation']['default_off_mapped_bytes'] == 0
audit_by_name['integration']['scope'] = {'description': aggregate['scope'], 'component_gates': 99,
    'linux_gates': 5, 'ci_harness_unittests_from_actual_push_artifact': 36,
    'actual_archive_and_elf_bytes_hashed': True, 'source_generation': source_gen,
    'videocc': {k: v for k, v in aggregate['videocc'].items() if not isinstance(v, list)},
    'supplementary_wire_audits': aggregate['requires_supplementary_wire_audits']}


# DDR-specific checks are new; the six retained model sections below remain the
# public RPM predecessor's semantic validators, with a strict current RPM tree.
ddr = reports['ddr_stats_offset']
assert ddr['schema'] == 'tb371fc-ddr-stats-offset-independent-artifact-audit-v1'
assert ddr['expected_tree'] == TREE and ddr['component']['checks'] == 20
expected_ddr_summary = {
    'checks': 20, 'ordinary_and_special_groups': 10, 'original_streams': 2,
    'completed_migrations': 2, 'profile_rejections': 2, 'one_bit_rejections': 6,
    'total_stream_files': 8, 'payload_bytes': 24, 'first_getters': 17, 'repeated_getters': 17,
    'prewarm_device_mmio': 0, 'prewarm_ram_commands': 2, 'first_memory_command_delta': 0,
    'first_device_mmio_delta': 0, 'first_cont_delta': 0, 'device_state_dirtied': False,
    'ram_witness_dirtied': True, 'source_generation_checks': 2, 'source_generation_inputs': 4,
    'enabled_active_mapped_bytes': 4, 'default_off_active_mapped_bytes': 0,
    'default_off_device_callback_count': 0, 'default_off_generic_rejection_count': 12,
    'disabled_target_device_callback_count': 0, 'disabled_target_generic_rejection_count': 3,
    'active_mapping_queries_verified': 7, 'target_pure_getters_total_per_direction': 187,
    'actual_archive_evidence_verified': True, 'native_probe_claimed': False, 'android_boot_claimed': False,
}
exact(ddr['summary'], expected_ddr_summary, 'DDR full independent summary')
assert [len(ddr[k]) for k in ['original_streams', 'completed_migrations', 'profile_rejections', 'one_bit_rejections']] == [2, 2, 2, 6]
assert len(ddr['ordinary_and_special']) == 10
assert ddr['source_recovery']['new_derivation'] is True
assert ddr['source_recovery']['original_source_byte_identity_claimed'] is False
assert ddr['source_recovery']['lost_actual_receipts_recreated'] is False
assert ddr['source_recovery']['original_lost_auditor_sha256'] == 'e083e95d5087a71416afecf87c921255433306bb6c7b2c0ba10ae586f528a024'
assert ddr['source_recovery']['predecessor_commit'] == 'd103750ca2a191559daeb9eff4341b8c80013dc6'
assert ddr['source_recovery']['predecessor_auditor_sha256'] == '7eb65a626ce253f3f8469948ddb0669d6bb5256f186f0ef9ffbb3c76b55b10f0'
assert [row['payload_hex'] for row in ddr['original_streams']] == [
    '0000000144534f31000000000c3f001c00000004000f00b8',
    '0000000044534f31000000000c3f001c0000000400000000']
assert [row['name'] for row in ddr['original_streams']] == ['ddr-stats-offset-enabled.migration', 'ddr-stats-offset-disabled.migration']
for index, row in enumerate(ddr['original_streams']):
    assert row['section']['payload_bytes'] == 24 and row['section']['version'] == 1
    assert row['trace']['actual_device_mmio_commands'] == 0 and row['trace']['actual_ram_commands'] == 2
    assert row['trace']['ram_witness'] == 0x44534f00 + index
    assert row['trace']['source_stop_and_postmigrate_verified'] is True
for index, row in enumerate(ddr['completed_migrations']):
    enabled = index == 0
    assert row['name'] == ['migration_enabled', 'migration_disabled'][index]
    expected_counts = {'first_pure_getters': 17, 'repeated_pure_getters': 17,
        'actual_qom_getters_total': 187, 'qtest_commands': 9, 'prewarm_actual_device_mmio': 0,
        'prewarm_actual_memory_commands': 2, 'prewarm_actual_ram_commands': 2,
        'first_device_mmio_delta': 0, 'first_memory_command_delta': 0, 'first_cont_delta': 0,
        'repeated_device_mmio_delta': 0, 'repeated_memory_command_delta': 0, 'repeated_cont_delta': 0,
        'payload_bytes_verified': 24, 'actual_device_rejections': 1 if enabled else 0,
        'actual_hole_rejections': 0 if enabled else 3, 'actual_ram_witness': 0x44534f00 + index}
    assert all(type(row[key]) is int and row[key] == value for key, value in expected_counts.items())
    expected_flags = {'device_state_mutable': False, 'destination_device_state_dirtied': False,
        'destination_ram_witness_dirtied': True, 'ram_witness_checked_after_both_observations': True,
        'ram_witness_checked_before_cont': True, 'source_snapshot_restored_before_cont': True,
        'post_restore_layout_and_read_semantics_checked': True, 'post_restore_reset_retains_profile': True,
        'shared_timestamp_used': False, 'first_flatview_after_both_qom_snapshots_and_restored_ram_witness': True,
        'first_flatview_before_cont': True, 'second_flatview_after_final_post_reset_device_read': True}
    assert all(row[key] is value for key, value in expected_flags.items())
    assert row['source_status'] == row['target_status'] == 'completed'
    assert row['ram_witness_before'] == ((0x44534f00 + index) ^ 0xffffffff)
    for key, filename in [('active_mapping_after_restore', 'mtree-after-restore.txt'),
                           ('active_mapping_after_reset', 'mtree-after-reset.txt')]:
        exact(row[key], {'active_leaf_count': int(enabled), 'active_mapped_bytes': 4 if enabled else 0,
                         'mtree_file': filename, 'configured_profile_enabled': enabled}, 'DDR.' + key)
off, = [row for row in ddr['ordinary_and_special'] if row['name'] == 'default_off_keeps_unknown_offset_and_rejects_all_access']
assert off['actual_qom_snapshots'] == 22 and off['actual_pure_getters'] == 374
assert off['actual_rejections'] == 10 and off['actual_qtest_commands'] == 11
assert off['actual_device_rejections'] == 0 and off['actual_hole_rejections'] == 12
assert off['default_off_mapped_bytes'] == 0 and off['no_global_profile_override'] is True
assert off['unknown_zero_not_treated_as_valid_read'] is True
for key, filename in [('active_mapping_before_reset', 'mtree-before-reset.txt'), ('active_mapping_after_reset', 'mtree-after-reset.txt')]:
    exact(off[key], {'active_leaf_count': 0, 'active_mapped_bytes': 0, 'mtree_file': filename,
                     'configured_profile_enabled': False}, 'DDR.off.' + key)
assert [row['name'] for row in ddr['profile_rejections']] == ['on_to_off', 'off_to_on']
assert [row['name'] for row in ddr['one_bit_rejections']] == ['profile', 'identity', 'physical_base', 'window_bytes', 'enabled_offset', 'disabled_offset']
for row in ddr['profile_rejections'] + ddr['one_bit_rejections']:
    assert row['destination_exit'] == 1
    assert all(type(row[key]) is int and row[key] == 0 for key in ['device_mmio_commands', 'memory_commands', 'cont_commands'])
    assert row['query_migrate_failed_response_required'] is False
    assert row['actual_stderr_diagnostic'].startswith('qcom-sm8250-ddr-stats-offset: migrated ')
for index, row in enumerate(ddr['one_bit_rejections']):
    original = ddr['original_streams'][1 if index == 5 else 0]
    comparison = row['whole_stream_comparison']
    expected_byte, expected_xor = [3, 7, 15, 19, 23, 23][index], [2, 1, 4, 1, 1, 1][index]
    assert row['original'] == original['name'] and row['bytes'] == original['bytes']
    assert comparison['changed_bits'] == 1
    assert comparison['all_other_stream_bytes_equal'] is True and comparison['original_stream_sha256_unchanged'] is True
    assert comparison['byte_offset'] == original['section']['payload_offset'] + expected_byte
    source_payload = bytes.fromhex(original['payload_hex'])
    changed_payload = bytearray(source_payload)
    changed_payload[expected_byte] ^= expected_xor
    assert comparison['before'] == source_payload[expected_byte] and comparison['after'] == changed_payload[expected_byte]
    assert row['payload_hex'] == changed_payload.hex()
audit_by_name['ddr_stats_offset']['scope'] = {
    'independent_summary': ddr['summary'], 'checks': 20, 'unmodified_streams': 2,
    'stream_files': 8, 'completed_migrations': 2, 'profile_rejections': 2, 'single_bit_rejections': 6,
    'bytes_per_original_payload': 24, 'first_target_qom_getters_each': 17,
    'repeated_target_qom_getters_each': 17, 'target_getters_each': 187,
    'first_target_actual_mmio_delta_each': 0, 'first_target_actual_cont_count_each': 0,
    'physical_base': 0x0c3f001c, 'mapped_bytes_when_enabled': 4, 'default_off_mapped_bytes': 0,
    'offset_value': 0x000f00b8, 'statistics_payload_implemented': False,
    'native_reset_value_known': False, 'captured_runtime_word_claimed': False,
    'aop_execution_claimed': False, 'rpm_offset_behavior_modeled': False,
    'destination_ram_witness_dirtied': True, 'destination_device_state_dirtied': False,
    'new_source_recovery_audit': True}


rpm = reports['rpm_stats_offset']
assert rpm['expected_tree'] == TREE
assert rpm['component']['checks'] == 20
expected_rpm_summary = {'checks': 20, 'ordinary_and_special_groups': 10, 'original_streams': 2, 'completed_migrations': 2, 'profile_rejections': 2, 'one_bit_rejections': 6, 'total_stream_files': 8, 'payload_bytes': 24, 'first_getters': 17, 'repeated_getters': 17, 'prewarm_device_mmio': 0, 'prewarm_ram_commands': 2, 'first_memory_command_delta': 0, 'first_device_mmio_delta': 0, 'first_cont_delta': 0, 'device_state_dirtied': False, 'ram_witness_dirtied': True, 'source_generation_checks': 2, 'source_generation_inputs': 4, 'actual_archive_evidence_verified': True, 'native_probe_claimed': False, 'android_boot_claimed': False}
assert rpm['summary'] == expected_rpm_summary
assert all(type(rpm['summary'][key]) is type(value) for key, value in expected_rpm_summary.items())
assert [len(rpm[k]) for k in ['original_streams', 'completed_migrations', 'profile_rejections', 'one_bit_rejections']] == [2, 2, 2, 6]
assert len(rpm['ordinary_and_special']) == 10
for row in rpm['completed_migrations']:
    expected_counts = {'first_pure_getters': 17, 'repeated_pure_getters': 17,
        'prewarm_actual_device_mmio': 0, 'prewarm_actual_memory_commands': 2,
        'prewarm_actual_ram_commands': 2, 'first_device_mmio_delta': 0,
        'first_memory_command_delta': 0, 'first_cont_delta': 0,
        'repeated_device_mmio_delta': 0, 'repeated_memory_command_delta': 0,
        'repeated_cont_delta': 0, 'payload_bytes_verified': 24}
    assert all(type(row[key]) is int and row[key] == value for key, value in expected_counts.items())
    expected_flags = {'device_state_mutable': False, 'destination_device_state_dirtied': False,
        'destination_ram_witness_dirtied': True, 'ram_witness_checked_after_both_observations': True,
        'ram_witness_checked_before_cont': True, 'source_snapshot_restored_before_cont': True,
        'post_restore_layout_and_read_semantics_checked': True,
        'post_restore_reset_retains_profile': True, 'shared_timestamp_used': False}
    assert all(row[key] is value for key, value in expected_flags.items())
    assert row['source_status'] == row['target_status'] == 'completed'
assert rpm['schema'] == 'tb371fc-rpm-stats-offset-independent-artifact-audit-v1'
audit_by_name['rpm_stats_offset']['scope'] = {
    'independent_summary': rpm['summary'],
    'checks': 20, 'ordinary_and_special_cases': 10, 'unmodified_streams': 2,
    'stream_files': 8, 'completed_migrations': 2, 'profile_rejections': 2,
    'single_bit_rejections': 6, 'bytes_per_original_payload': 24,
    'first_target_qom_getters_each': 17, 'repeated_target_qom_getters_each': 17,
    'first_target_actual_mmio_delta_each': 0, 'first_target_actual_cont_count_each': 0,
    'destination_ram_witness_dirtied': True, 'destination_device_state_dirtied': False,
    'physical_base': 0x0c3f0004, 'mapped_bytes': 4, 'offset_value': 0x000f0048,
    'statistics_payload_implemented': False, 'native_reset_value_known': False,
    'captured_runtime_word_claimed': False, 'aop_execution_claimed': False,
    'ready_or_ddr_offset_modeled': False}

spss = reports['spss']
assert spss['component']['checks'] == 21
assert [len(spss[k]) for k in ['original_streams', 'completed_migrations', 'profile_rejections', 'one_bit_rejections']] == [4, 3, 2, 6]
assert len(spss['ordinary_and_special']) == 10
for row in spss['completed_migrations']:
    assert [row[k] for k in ['first_pure_getters', 'actual_qom_getters_total', 'prewarm_actual_device_mmio', 'first_device_mmio_delta', 'first_cont_delta']] == [18, 144, 1, 0, 0]
    assert row['source_snapshot_restored_before_cont'] is True
audit_by_name['spss']['scope'] = {'checks': 21, 'ordinary_and_special_cases': 10,
    'unmodified_streams': 4, 'stream_files': 10, 'completed_migrations': 3,
    'profile_rejections': 2, 'single_bit_rejections': 6,
    'bytes_per_original_payload': 25, 'first_target_qom_getters_each': 18,
    'first_target_actual_mmio_delta_each': 0, 'first_target_actual_cont_count_each': 0,
    'target_qom_getters_total_each': 144, 'prewarm_actual_device_mmio_each': 1,
    'programmed_zero_distinct_from_unknown': True,
    'readback_supported': False, 'ready_firmware_pas_effects_modeled': False,
    'buffered_five_byte_write_commits_valid_four_byte_prefix': True,
    'buffered_write_whole_transfer_atomicity_claimed': False}
llcc = reports['llcc']
assert llcc['component']['check_count'] == 32
assert [len(llcc[k]) for k in ['sources', 'completed', 'profile_rejections', 'corruption_rejections']] == [5, 4, 2, 4]
assert llcc['source_generation']['source_table_bytes'] == 640
audit_by_name['llcc']['scope'] = {'checks': 32, 'unmodified_streams': 5, 'stream_files': 9,
    'completed_migrations': 4, 'profile_rejections': 2, 'single_bit_rejections': 4,
    'bytes_per_original_payload': 783, 'first_target_qom_getters_each': 313,
    'first_target_actual_mmio_delta_each': 0, 'first_target_actual_cont_count_each': 0,
    'source_generation': llcc['source_generation']}
audit_by_name['dp']['scope'] = reports['dp']['summary']
audit_by_name['dsi']['scope'] = reports['dsi']['summary']
audit_by_name['dispcc']['scope'] = {k: v for k, v in reports['dispcc']['dispcc'].items() if k not in ('sources', 'rejections')}


gh_path = asset_path('github_metadata')
base_path = asset_path('pr_base_identity')
gh, refs = read(gh_path), read(base_path)
assert gh['head']['sha'] == HEAD and gh['head']['tree']['sha'] == TREE
assert [r['sha'] for r in gh['head']['parents']] == [SOURCE_PARENT]
assert gh['merge']['sha'] == MERGE and gh['merge']['tree']['sha'] == TREE
assert [r['sha'] for r in gh['merge']['parents']] == [BASE_HEAD, HEAD]
assert refs['pr']['base']['sha'] == BASE_HEAD
assert refs['main_ref']['object']['sha'] == OBSERVED_DEFAULT_HEAD
assert refs['pr']['state'] == 'open'
assert refs['pr']['draft'] is True and refs['pr']['merged'] is False
assert refs['pr']['head']['sha'] == HEAD and refs['pr']['number'] == PR_NUMBER
assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE
default_ref_advance_observation = validate_documentation_advance(refs, gh)
retained_failed_qualification = retain_failed_qualification()
workflow_runs = []
for prefix, run_id, event, board_job, cpu_job in [
    ('push', PUSH_RUN_ID, 'push', PUSH_BOARD_JOB, PUSH_A77_JOB),
    ('pr', PR_RUN_ID, 'pull_request', PR_BOARD_JOB, PR_A77_JOB)]:
    run, jobs = gh[prefix]['run'], gh[prefix]['jobs']['jobs']
    assert run['id'] == run_id and run['head_sha'] == HEAD and run['event'] == event
    assert run['status'] == 'completed' and run['conclusion'] == 'success' and run['run_attempt'] == 1
    assert run['path'] == '.github/workflows/tb371fc.yml'
    assert {j['name'] for j in jobs} == {'board-components-and-linux', 'cortex-a77-architecture'}
    assert {j['id'] for j in jobs} == {board_job, cpu_job}
    for j in jobs:
        assert j['head_sha'] == HEAD and j['status'] == 'completed' and j['conclusion'] == 'success' and j['run_attempt'] == 1
        assert all(s['status'] == 'completed' and s['conclusion'] == 'success' for s in j['steps'])
    workflow_runs.append({k: run[k] for k in ('id', 'event', 'head_branch', 'head_sha', 'run_attempt', 'status', 'conclusion', 'updated_at', 'html_url')})
    workflow_runs[-1]['jobs'] = [{k: j[k] for k in ('id', 'name', 'head_sha', 'status', 'conclusion', 'run_attempt', 'started_at', 'completed_at')} for j in jobs]
artifact_path = asset_path('artifact_api')
artifact_rows = [a for a in read(artifact_path)['artifacts'] if a['id'] == ARTIFACT_ID]
assert len(artifact_rows) == 1
artifact = artifact_rows[0]
assert artifact['name'] == 'tb371fc-public-gates-'+HEAD and artifact['expired'] is False
assert artifact['size_in_bytes'] == ZIP_BYTES and artifact['digest'] == 'sha256:'+ZIP_SHA
assert artifact['workflow_run']['id'] == PUSH_RUN_ID and artifact['workflow_run']['head_sha'] == HEAD

a77_path = asset_path('a77_receipt')
a77 = read(a77_path)
assert a77['passed'] is True and a77['source_head'] == HEAD and a77['matching_push_pr_tree'] == TREE
assert {r['job_id'] for r in a77['runs']} == {PUSH_A77_JOB, PR_A77_JOB}
assert len(a77['runs']) == 2 and all(r['guest_checks'] == 852 and r['shared_pci_ufs_case_count'] == 17 for r in a77['runs'])
for r in a77['runs']:
    assert (r['run_id'], r['event'], r['actual_checkout']) == ({PUSH_A77_JOB: (PUSH_RUN_ID, 'push', HEAD), PR_A77_JOB: (PR_RUN_ID, 'pull_request', MERGE)}[r['job_id']])
    assert r['job_completed_success'] is True and r['all_job_steps_completed_success'] is True
    assert len(r['invalid_configurations']) == 2
    assert pin(r['log_path'], r['log_sha256'])['bytes'] == r['log_bytes']
board_path = asset_path('board_receipt')
board = read(board_path)
assert board['passed'] is True and board['source_head'] == HEAD and board['matching_push_pr_tree'] == TREE
assert {r['job_id'] for r in board['runs']} == {PUSH_BOARD_JOB, PR_BOARD_JOB}
assert len(board['runs']) == 2
for r in board['runs']:
    assert (r['run_id'], r['job_id']) == {'push': (PUSH_RUN_ID, PUSH_BOARD_JOB), 'pr': (PR_RUN_ID, PR_BOARD_JOB)}[r['role']]
    assert r['component_gates'] == 99 and r['linux_gates'] == 5 and r['extra_ufs_checks'] == [39, 61]
    assert r['completed_success'] is True and r['run_attempt'] == 1
    assert r['actual_checkout'] == {'push': HEAD, 'pr': MERGE}[r['role']]
    assert r['host_unit_suites']['step_number'] == 4
    assert r['host_unit_suites']['all_host_unittest_counts_in_order'] == [15, 36, 28, 43, 16, 15]
    assert [(x['suite'], x['tests'], x['verdict']) for x in r['host_unit_suites']['first_two_suites']] == [('source_download', 15, 'OK'), ('ci_device_tests', 36, 'OK')]
    assert pin(r['raw_log']['path'], r['raw_log']['sha256'])['bytes'] == r['raw_log']['bytes']
ufs_path = asset_path('ufs_receipt')
ufs = read(ufs_path)
assert ufs['passed'] is True and ufs['head'] == HEAD and ufs['archive']['sha256'] == ZIP_SHA and ufs['qemu']['sha256'] == QEMU_SHA
assert ufs['run_id'] == PUSH_RUN_ID and ufs['artifact_id'] == ARTIFACT_ID
assert ufs['archive']['bytes'] == ZIP_BYTES and ufs['qemu']['bytes'] == QEMU_BYTES
assert [(r['check_count'], r['source_checks'], r['target_checks'], r['passed']) for r in ufs['cases']] == [(39, 21, 18, True), (61, 34, 27, True)]


# The recovery download is a new observation of the same immutable artifact.
# The original download receipt bytes and original local audit outputs are not recreated.
download = read(asset_path('artifact_download'))
assert download['schema'] == 'tb371fc-ddrstats-exact-artifact-download-recovery-v1'
for key, expected in {'head': HEAD, 'run_id': PUSH_RUN_ID, 'artifact_id': ARTIFACT_ID,
                      'zip_bytes': ZIP_BYTES, 'zip_sha256': ZIP_SHA, 'qemu_bytes': QEMU_BYTES,
                      'qemu_sha256': QEMU_SHA, 'qemu_member': QEMU_MEMBER}.items():
    exact(download[key], expected, 'download.' + key)
for key in ['complete_hashes_verified', 'structural_preflight_passed', 'recovery_of_same_immutable_artifact']:
    assert download[key] is True
for key in ['actual_audits_completed', 'qemu_executed', 'native_executed', 'original_receipt_bytes_recovered']:
    assert download[key] is False
assert aggregate['qemu']['member'] == download['qemu_member']

# Observers are bound to their actual sources, complete raw logs and push unit artifact.
assert a77['schema'] == 'tb371fc-ddr-stats-offset-a77-rawlog-observation-v1'
assert a77['auditor_sha256'] == asset_pins['a77_observer']['sha256']
assert board['schema'] == 'tb371fc-ddr-stats-offset-board-rawlog-observation-v1'
assert board['observer']['sha256'] == asset_pins['board_observer']['sha256']
assert board['observer']['bytes'] == asset_pins['board_observer']['bytes']
assert ufs['schema'] == 'tb371fc-extra-linux-ufs-artifact-observation-v2'
assert ufs['auditor_sha256'] == asset_pins['ufs_observer']['sha256']
archived_units = board['push_unit']
assert archived_units['passed'] is True and archived_units['exit_code'] == 0
assert archived_units['unittest_count'] == 36 and archived_units['unittest_verdict'] == 'OK'
assert archived_units['archived_source_head'] == HEAD and archived_units['archived_qemu_sha256'] == QEMU_SHA
assert archived_units['run_id'] == PUSH_RUN_ID and archived_units['artifact_id'] == ARTIFACT_ID
assert archived_units['archive']['bytes'] == ZIP_BYTES and archived_units['archive']['sha256'] == ZIP_SHA
assert board['scope']['PR_artifact_unit_count_directly_observed'] is False
assert board['scope']['PR_host_unit_count_directly_observed'] is True
assert board['scope']['qemu_executed'] is False and board['scope']['device_rpc'] is False

source = read(asset_path('source_recovery'))
assert source['schema'] == 'tb371fc-ddrstats-fresh-source-recovery-v1'
assert source['passed'] is True
assert source['source_commit'] == HEAD and source['source_tree'] == TREE
assert source['sole_parent'] == SOURCE_PARENT and source['parent_tree'] == PARENT_TREE
assert source['publication_file_count'] == 41 and source['runner_file_count'] == 5
assert source['publication_bytes'] == 645747 and source['all_recovered_bytes'] == 761879
assert source['new_paths'] == 33 and source['modified_paths'] == 8
assert source['unchanged_file_or_subtree_entries'] == 1091 and source['git_tree_objects_recomputed'] == 25
assert source['all_other_tree_entries_unchanged'] is True
assert source['five_runners_equal_parent_and_recorded_hashes'] is True
assert source['lost_original_manifest_or_readback_receipts_recreated'] is False
for key in ['CI_rerun', 'artifact_audit_run', 'device_or_SSH_used', 'QEMU_or_native_run', 'GitHub_mutation']:
    assert source[key] is False
assert len(source['files']) == 46 and len(source['raw_api_records']) == 73
expected_runner_identities = {'tb371fc/scripts/boot_android.py': {'bytes': 40941, 'sha256': '61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1', 'git_blob_sha1': '1614a9c6032457fecf370559ed66c45823f16a3f'}, 'tb371fc/scripts/boot_android_from_ready.py': {'bytes': 31692, 'sha256': 'b2ef676a99c7064b9d31035d1b3e1e471d613500cba6373a0992b4c4f4e4154d', 'git_blob_sha1': 'e2b4d59c0460286ba1f948bfe9afca73f51f05fc'}, 'tb371fc/scripts/android_boot_inputs.py': {'bytes': 36402, 'sha256': '08affe8ef5c40c7350be9e4c0f0482c4c5e594a3dcff7e3a88cf7887ea1eb5d8', 'git_blob_sha1': '4291d8d096f0cafddb58c201030ccf210c50250c'}, 'tb371fc/scripts/boot_qmp.py': {'bytes': 2822, 'sha256': 'b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c', 'git_blob_sha1': '54e845229266a212d0a192276e5d0228c3cf716f'}, 'tb371fc/scripts/legacy_printk.py': {'bytes': 4275, 'sha256': '6b7d18188ef8987c32135fef179f271dcf7284766a233626d09269696cada27d', 'git_blob_sha1': '055df2101bd15268ab55189795fdaa1b78c473ff'}}
current_published_files, current_runner_file_pins, current_sources = {}, [], {}
for row in source['files']:
    relative = PurePosixPath(row['path'])
    assert not relative.is_absolute() and '..' not in relative.parts and row['path'] not in current_sources
    assert row['mode'] == '100644' and row['type'] == 'blob'
    assert row['fresh_full_exact_commit_read'] is True and row['disk_readback_exact'] is True
    actual = pin(SOURCE_ROOT / row['path'], row['sha256'])
    assert actual['bytes'] == row['bytes']
    raw = (SOURCE_ROOT / row['path']).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_sha1']
    current_sources[row['path']] = row
    if row['role'] == 'publication':
        current_published_files[row['path']] = row
    else:
        assert row['role'] == 'runner' and row['path'] in expected_runner_identities
        assert {k: row[k] for k in ['bytes', 'sha256', 'git_blob_sha1']} == expected_runner_identities[row['path']]
        current_runner_file_pins.append(actual)
assert len(current_published_files) == 41 and sum(r['bytes'] for r in current_published_files.values()) == 645747
assert {r['path'] for r in source['files'] if r['role'] == 'runner'} == set(expected_runner_identities)
assert len(current_runner_file_pins) == 5
assert len(source['tree_delta']) == 41 and {r['path'] for r in source['tree_delta']} == set(current_published_files)
assert sum(r['previous_blob'] is None for r in source['tree_delta']) == 33
assert sum(r['previous_blob'] is not None for r in source['tree_delta']) == 8
for row in source['tree_delta']:
    assert row['current_blob'] == current_sources[row['path']]['git_blob_sha1']
    assert row['bytes'] == current_sources[row['path']]['bytes']
raw_source_pins = []
for row in source['raw_api_records']:
    actual = pin(row['path'], row['sha256'])
    assert actual['bytes'] == row['bytes']
    raw = Path(row['path']).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_sha1']
    raw_source_pins.append(actual)

deps = read(asset_path('dependency_recovery'))
assert deps['schema'] == 'tb371fc-ddrstats-fresh-source-dependency-recovery-v1'
assert deps['passed'] is True and deps['source_commit'] == HEAD and deps['source_tree'] == TREE
assert deps['dependency_count'] == len(deps['files']) == 2
assert deps['executed_or_imported'] is False and deps['QEMU_CI_or_native_rerun'] is False
assert {row['path'] for row in deps['files']} == {'tb371fc/scripts/source_download.py', 'tb371fc/tests/test_camera_gdsc.py'}
for row in deps['files']:
    assert all(row[key] is True for key in ['exact_head_full_read', 'matched_current_tree', 'disk_readback_exact'])
    actual = pin(SOURCE_ROOT / row['path'], row['sha256'])
    assert actual['bytes'] == row['bytes']
    raw = (SOURCE_ROOT / row['path']).read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_sha1']
    current_sources[row['path']] = row
assert len(ddr['reviewed_source_pins']) == 16
for relative, row in ddr['reviewed_source_pins'].items():
    key = 'tb371fc/tests/test_camera_gdsc.py' if relative == 'inherited-test_camera_gdsc.py' else relative
    assert row['bytes'] == current_sources[key]['bytes'] and row['sha256'] == current_sources[key]['sha256']

new_derivation = read(asset_path('new_ddr_derivation'))
assert new_derivation['schema'] == 'tb371fc-ddr-auditor-new-source-derivation-v1'
assert new_derivation['original_byte_identity_recovered'] is False
assert new_derivation['artifact_opened'] is False and new_derivation['auditor_executed'] is False
assert new_derivation['runtime_or_artifact_pass_claimed'] is False
assert new_derivation['new_auditor']['sha256'] == audit_by_name['ddr_stats_offset']['auditor']['sha256']
assert new_derivation['new_auditor']['bytes'] == audit_by_name['ddr_stats_offset']['auditor']['bytes']
new_source_check = read(asset_path('new_ddr_source_validation'))
assert new_source_check['passed'] is True and new_source_check['artifact_opened'] is False
assert new_source_check['main_invoked'] is False and new_source_check['artifact_audit_executed'] is False
assert new_source_check['auditor']['sha256'] == audit_by_name['ddr_stats_offset']['auditor']['sha256']
assert new_source_check['exact_contract_constants_match'] is True
ddr_review = read(asset_path('new_ddr_independent_review'))
assert ddr_review['schema'] == 'tb371fc-ddrstats-recovery-independent-auditor-static-review-v1'
assert ddr_review['passed'] is True and ddr_review['static_review_passed'] is True
assert ddr_review['auditor']['sha256'] == audit_by_name['ddr_stats_offset']['auditor']['sha256']
assert ddr_review['artifact_opened'] is False and ddr_review['artifact_audit_executed'] is False
assert ddr_review['original_lost_auditor_byte_identity_claimed'] is False and ddr_review['original_lost_review_recreated'] is False
aggregate_review = read(asset_path('new_aggregate_review'))
assert aggregate_review['passed'] is True
assert aggregate_review['new_source']['sha256'] == audit_by_name['integration']['auditor']['sha256']
assert aggregate_review['limits']['artifact_opened_by_reviewer'] is False
assert aggregate_review['limits']['artifact_audit_run'] is False

# These immutable public historical proofs retain earlier failures and scope.
# They are never selected as a current-head audit receipt.
old_qualification = read(asset_path('historical_rpm_qualification'))
assert old_qualification['schema'] == 'tb371fc-rpm-stats-offset-66d9e20-qualified-artifact-v1'
assert old_qualification['passed'] is True and old_qualification['commit'] == HISTORICAL_RPM_HEAD
assert old_qualification['tree'] == PARENT_TREE
assert old_qualification['artifact']['id'] == 14986446678
assert old_qualification['all_seven_independent_audits_passed'] is True
assert old_qualification['native_executed'] is False and old_qualification['android_boot_established'] is False
failed_ci = read(asset_path('historical_failed_ci'))
assert failed_ci['schema'] == 'tb371fc-rpmstats-75cc1dc-failed-ci-observation-v1'
assert failed_ci['source_commit'] == HISTORICAL_FAILED_HEAD
assert failed_ci['corrected_source_head'] == HISTORICAL_RPM_HEAD
assert failed_ci['run_id'] == 45532378559 and failed_ci['job_id'] == 138930599415
assert failed_ci['run_attempt'] == 1 and failed_ci['event'] == 'push'
assert failed_ci['run_status'] == failed_ci['job_status'] == 'completed'
assert failed_ci['run_conclusion'] == failed_ci['job_conclusion'] == 'failure'
assert failed_ci['failed_step']['conclusion'] == 'failure'
assert failed_ci['runtime_CI_pass_claimed'] is False and failed_ci['failure_receipt_superseded_or_erased'] is False
assert failed_ci['corrected_head_requires_its_own_actual_CI'] is True
failed_head = read(asset_path('historical_include_correction'))
failed_head_review = read(asset_path('historical_include_review'))
assert failed_head['failed_source_commit'] == failed_head_review['failed_head'] == HISTORICAL_FAILED_HEAD
assert failed_head['push_run'] == 45532378559 and failed_head['push_board_job'] == 138930599415
assert failed_head['compiled_device_pass_claimed'] is False and failed_head_review['compiled_pass_claimed'] is False
assert failed_head['header']['new_sha256'] == '946df3bee216839b46001d4ea3065bda6563413a0c753a7b221fffea3b77ad0b'
local_initial, local_actual = read(asset_path('historical_rpm_local_initial')), read(asset_path('historical_rpm_local_actual'))
assert local_initial['exit_code'] == local_actual['exit_code'] == 1
assert local_initial['formal_ci_pass_claimed'] is False and local_actual['formal_ci_pass_claimed'] is False
for name, row in [('historical_rpm_local_initial_stderr', local_initial), ('historical_rpm_local_actual_stderr', local_actual)]:
    assert row['stderr_bytes'] == asset_pins[name]['bytes'] and row['stderr_sha256'] == asset_pins[name]['sha256']
assert "ModuleNotFoundError: No module named 'ci_device_tests'" in asset_path('historical_rpm_local_initial_stderr').read_text()
old_local_text = asset_path('historical_rpm_local_actual_stderr').read_text()
assert 'Ran 35 tests in ' in old_local_text and 'FAILED (errors=1)' in old_local_text
assert 'PermissionError: [Errno 1] Operation not permitted' in old_local_text
assert len(re.findall(r'^test_.* \.\.\. ok$', old_local_text, re.M)) == 34

local_ddr_path = SOURCE_EVIDENCE/'ci-local-unit-001.json'
local_ddr_pin = pin(local_ddr_path, '9e1c7f2e4604a106bc3da29c59e98cf3e83222cde2430076f866c9b97317ccc8')
local_ddr = read(local_ddr_path)
assert local_ddr['schema'] == 'tb371fc-ddr-stats-offset-local-host-unit-v1'
assert local_ddr['tests_run'] == 36 and local_ddr['errors'] == 1 and local_ddr['failures'] == 0
assert local_ddr['exit_code'] == 1 and local_ddr['passed'] is False
assert local_ddr['test_assertions_modified'] is False and local_ddr['runtime_ci_pass_claimed'] is False
local_ddr_logs = []
for row in local_ddr['logs']:
    assert row['file'] in ['ci-unit-all-001.stdout.log', 'ci-unit-all-001.stderr.log']
    actual = pin(SOURCE_EVIDENCE/row['file'], row['sha256'])
    assert actual['bytes'] == row['bytes']
    local_ddr_logs.append(actual)
local_ddr_text = (SOURCE_EVIDENCE/'ci-unit-all-001.stderr.log').read_text()
assert 'Ran 36 tests in ' in local_ddr_text and 'FAILED (errors=1)' in local_ddr_text
assert 'PermissionError: [Errno 1] Operation not permitted' in local_ddr_text
assert len(re.findall(r'^test_.* \.\.\. ok$', local_ddr_text, re.M)) == 35

result = {
    'schema': 'tb371fc-ddr-stats-offset-7d01e5e-qualified-artifact-v1', 'passed': True,
    'qualification': 'Exact-head push and PR CI plus eight new read-only recovery audits of the same immutable push ZIP passed',
    'qualification_evidence_observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'repository': 'cyjin-yl/qemu-tb371fc', 'commit': HEAD, 'tree': TREE,
    'artifact': {'id': ARTIFACT_ID, 'artifact_name': artifact['name'], 'run_id': PUSH_RUN_ID,
        'api_url': artifact['url'], 'archive_bytes': ZIP_BYTES, 'archive_sha256': ZIP_SHA,
        'qemu_member': QEMU_MEMBER, 'qemu_bytes': QEMU_BYTES, 'qemu_sha256': QEMU_SHA},
    'workflow_runs': workflow_runs, 'github_evidence': asset_pins['github_metadata'],
    'pr_base_evidence': asset_pins['pr_base_identity'], 'artifact_api_evidence': asset_pins['artifact_api'],
    'assembler': pin(__file__), 'qualification_binding': binding_pin,
    'mutable_default_ref_observation': default_ref_advance_observation,
    'retained_failed_qualification': retained_failed_qualification,
    'bounded_artifact_download_evidence': asset_pins['artifact_download'],
    'all_eight_independent_audits_passed': True, 'audits': audits,
    'a77_actual_raw_log_audit': asset_pins['a77_receipt'], 'a77_each_job_executed_in_this_attempt': True,
    'independent_board_log_observation': asset_pins['board_receipt'],
    'extra_linux_ufs_observation': {'receipt': asset_pins['ufs_receipt'], 'checks': [39, 61], 'boundary': ufs['boundary']},
    'additional_host_unit_suites_each_board_job': {'source_download': 15, 'ci_device_tests': 36,
        'boot_android': 43, 'included_in_component_gate_count': False},
    'runner_source_sha256': expected_runner_identities['tb371fc/scripts/boot_android.py']['sha256'],
    'historical_runner_behavior_review': asset_pins['historical_runner_source_review'],
    'historical_runner_template_review': asset_pins['historical_runner_template_review'],
    'current_head_publication_review': {'receipt': asset_pins['source_recovery'],
        'source_commit': HEAD, 'source_tree': TREE, 'final_publication_path_count': 41,
        'total_published_bytes': 645747, 'all_41_actual_retained_readback_bytes_and_git_blobs_rechecked': True,
        'unchanged_file_or_subtree_entries': 1091, 'git_tree_objects_recomputed': 25,
        'all_other_tree_entries_unchanged': True, 'fresh_recovery_proof': True,
        'original_lost_readback_receipt_recreated': False, 'raw_API_record_count': len(raw_source_pins)},
    'current_head_runner_readback': {'receipt': asset_pins['source_recovery'], 'source_commit': HEAD,
        'source_tree': TREE, 'exact_file_count': 5, 'files': current_runner_file_pins,
        'current_head_bytes_established_by_historical_review_alone': False,
        'runner_behavior_review_is_historical_and_byte_identity_is_current': True},
    'ci_attempts_observed': 1, 'ci_failure_observed_for_this_head': False,
    'local_unit_limitation': {'actual_local_run_receipt': local_ddr_pin, 'actual_local_logs': local_ddr_logs,
        'actual_local_test_count': 36, 'local_pass_count': 35, 'local_environment_error_count': 1,
        'local_all_pass_claimed': False, 'local_socket_eperm_retained': True,
        'formal_push_actual_36_pass_0_skip': True,
        'historical_RPM_import_failure_receipt': asset_pins['historical_rpm_local_initial'],
        'historical_RPM_socket_failure_receipt': asset_pins['historical_rpm_local_actual']},
    'retained_failed_source_head': {'source_commit': HISTORICAL_FAILED_HEAD,
        'push_run_id': 45532378559, 'pr_run_id': 45532462773, 'push_board_job_id': 138930599415,
        'actual_terminal_failure_observation': asset_pins['historical_failed_ci'],
        'failure_and_correction_receipt': asset_pins['historical_include_correction'],
        'independent_correction_review': asset_pins['historical_include_review'],
        'earlier_failed_build_reclassified_as_pass': False, 'earlier_head_native_qualified': False},
    'retained_prior_RPM_CI': asset_pins['historical_rpm_qualification'],
    'retained_prior_RPM_native': {'receipt': asset_pins['historical_rpm_native'],
        'source_head': HISTORICAL_RPM_HEAD, 'runner_exit': 2, 'qemu_exit': 0,
        'outcome': 'kernel-failure', 'android_boot_established': False,
        'current_DDR_outcome_inferred_from_prior_native': False},
    'retained_prior_head_ci': asset_pins['historical_prior_spss_qualification'],
    'retained_earlier_head_ci': asset_pins['historical_earlier_spss_qualification'],
    'evidence_recovery': {'new_qualification_source_and_receipt': True,
        'same_immutable_artifact': True, 'original_lost_receipt_bytes_recreated': False,
        'original_lost_qualifier_source_sha256_from_conversation': '130334bf01b28ab41616ddf9f051f28b6db0c574426f557e8377b7cbb5e1ba3b',
        'original_lost_DDR_auditor_sha256_from_conversation': 'e083e95d5087a71416afecf87c921255433306bb6c7b2c0ba10ae586f528a024',
        'original_lost_source_byte_identity_claimed': False, 'fresh_current_read_only_audits': 8,
        'recovery_CI_rerun': False, 'recovery_QEMU_or_native_rerun': False,
        'new_source_review_assets': {name: asset_pins[name] for name in ['new_ddr_derivation', 'new_ddr_source_validation',
            'new_ddr_independent_review', 'new_aggregate_review']},
        'preparation_failures_retained': {name: asset_pins[name] for name in ['source_materialization_failure', 'source_derivation_failure']}},
    'native_started': False, 'native_executed': False, 'coordinator_deployed': False,
    'host_boot_guard_bound': False, 'device_rpc_used_for_qualification': False,
    'android_userspace_established': False, 'ui_established': False, 'android_boot_established': False,
    'read_only_qualification_assembly': True, 'additional_optional_tests_run': False,
    'scope': 'Compiled CI artifact qualification for one later original-input native attempt. No original Android userspace, UI or boot completion, DDR/RPM statistics payload or live data, firmware/PAS/READY events, AOP execution, or physical reset/readback behavior is established.'}
out.parent.mkdir(parents=True, exist_ok=True)
with out.open('x') as f:
    json.dump(result, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps({'qualification': pin(out), 'native_executed': False, 'passed': True}))
