#!/usr/bin/env python3
"""Assemble exact observed RPM offset qualification; unbound artifacts cannot qualify."""
from pathlib import Path
import datetime
import hashlib
import json
import re

if not __debug__:
    raise SystemExit("Qualification refuses optimized Python: required assertions must remain active")

BASE = Path('/workspace/scratch/6a5ea697094c')
EV = Path('/dev/shm/rpmstats-ci-evidence')
REVIEW = Path('/dev/shm/rpmstats-ci-review')
SOURCE_EVIDENCE = Path('/dev/shm/rpmstats-candidate-6a5/tb371fc/evidence/rpm-stats-offset-20261125-6a5')
HEAD = '66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b'
TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'
SOURCE_PARENT = '75cc1dc83600784afa06e81c9998d8d39aba6af2'
BASE_HEAD = '8d0bc64d8da6da18bf43b52a2a9547f28f44c3e9'
MERGE = 'c7ba198d6bf9bd5e3255e207d07efd85a026a1a9'
PR_NUMBER = 36
PUSH_RUN_ID = 45533031476
PR_RUN_ID = 45533037101
PUSH_BOARD_JOB = 138932791819
PUSH_A77_JOB = 138932792020
PR_BOARD_JOB = 138932812241
PR_A77_JOB = 138932812391
ZIP_SHA = '809199bd6f39181dfe9340a60bf58fde4544b536d752736f0f475fbc89e46ec4'
QEMU_SHA = '019c56322a1dfd64a5db9d82719b93d49f7e6c7a5891e708f6f9f4c535aa703e'
ZIP_BYTES = 56393004
QEMU_BYTES = 63700664
ARTIFACT_ID = 14986446678
A77_RECEIPT_SHA = '60b88c09dcc0ffbbeb34c8dd06a324d7056597b4af81499bc8a8caccedd3ef5f'
UFS_RECEIPT_SHA = '23f7b329245c7f31fee50550c110b243124261f2d5c43b35c4a43e2d2ff56d9e'
BOARD_RECEIPT_SHA = 'b23a22ab95a94866a0f68de198283615d36cec7958ce7369f49fa99ae2d10410'
RPM_AUDITOR_SHA = '7eb65a626ce253f3f8469948ddb0669d6bb5256f186f0ef9ffbb3c76b55b10f0'
RPM_RECEIPT_CONTRACT_BOUND = True
RPM_GENERATED_CONTRACT = {'source_input_count': 4, 'register_count': 1, 'window_count': 1,
                          'mapped_bytes': 4, 'physical_base': 0x0c3f0004, 'offset_value': 0x000f0048}

def pin(path, expected=None):
    path = Path(path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if expected is not None:
        assert digest == expected, (str(path), digest, expected)
    return {'path': str(path.resolve()), 'bytes': len(raw), 'sha256': digest}

def read(path):
    return json.loads(Path(path).read_bytes())

audit_specs = [
    ('integration', EV/'audit_rpm_stats_offset_integration_artifact_66d9e20.py',
     'd52cf3026c23d82fae252557877ff80bbd8eafa126ffee0aa7029de4830214df',
     EV/'rpmstats-66d9e20-final-integration-audit-001.json',
     '8209ae60185d804eddb7e44c409c49b900f9a62a82ee3d07c820313946c7330b'),
    ('rpm_stats_offset', Path('/dev/shm/rpmstats-independent-audit/audit_rpm_stats_offset_artifact.py'),
     RPM_AUDITOR_SHA,
     EV/'rpmstats-66d9e20-final-independent-audit-001.json',
     '017ad61643fbbd545712e0dbeab60af4a9216161c68038665e0c7a55d1988971'),
    ('spss', EV/'audit_spss_irq_mask_artifact_rpm_stats_offset_66d9e20.py',
     '8d709f46f2220dbd526bc5b052137693983b3f4ce8fc843b08ea338b669cfa5d',
     EV/'spss-66d9e20-final-independent-audit-001.json',
     '1f8e4e504e44334903c93bfcfb35b396a9df944517d3c243c42f31c9dce6d232'),
    ('llcc', EV/'audit_llcc_artifact_rpm_stats_offset_66d9e20.py',
     'b5e358b0d3a9f319403c8541ef26a1d5a6a455052f74535967d89fd58f99171d',
     EV/'llcc-66d9e20-final-independent-audit-001.json',
     '4f900a65fa640a6b67d50e4adfc8ff7f17d77d0abc7fa6d1e67e54ef24c9bee6'),
    ('dp', EV/'audit_dp_pll_artifact_rpm_stats_offset_66d9e20.py',
     '14ca84d703148349b9f401f5ce1475cd194d5c726a343e06d193a7c3726c30f5',
     EV/'dp-66d9e20-final-independent-audit-001.json',
     '945e692b9ae378b1c9b1a2abd1c94d2eec9b4476c469ddaddafe89abc67c217c'),
    ('dsi', BASE/'dsi-pll-candidate/.work/audit_dsi_pll_artifact.py',
     '0e0b7e6ebe3a9486383c2946669629a97d4d037414be9d6e5ea28970462954fe',
     EV/'dsi-66d9e20-final-independent-audit-001.json',
     '6d5b3d0948c239f73e403f79be45122fc2eef2ea0a0a799d564e6148e2b8a5c3'),
    ('dispcc', BASE/'dsi-pll-candidate/.work/audit_dispcc_wide_artifact_rootpath.py',
     '62837675b808db7ed5671e9cff7653f7b76d4c295d668919ffff10a4134fc4c8',
     EV/'dispcc-66d9e20-final-independent-audit-001.json',
     '7ba3ea521028af94c2923eced6b74fcc8b5fa60a252cb3794ef9872cef1b6e9e'),
]
assert all(type(x) is str and re.fullmatch('[0-9a-f]{64}', x) for x in
    [ZIP_SHA, QEMU_SHA, A77_RECEIPT_SHA, UFS_RECEIPT_SHA, BOARD_RECEIPT_SHA] +
    [value for row in audit_specs for value in (row[2], row[4])]), 'Actual artifact/auditor/receipt pins are UNBOUND; qualification is prohibited'
assert all(type(x) is int and x > 0 for x in [ZIP_BYTES, QEMU_BYTES, ARTIFACT_ID,
    PR_NUMBER, PUSH_RUN_ID, PR_RUN_ID, PUSH_BOARD_JOB, PUSH_A77_JOB, PR_BOARD_JOB, PR_A77_JOB]), 'Actual artifact/run geometry is UNBOUND'
assert all(type(x) is str and re.fullmatch('[0-9a-f]{40}', x) for x in
    [HEAD, TREE, SOURCE_PARENT, BASE_HEAD, MERGE]), 'Created source/merge identities are UNBOUND'
assert RPM_RECEIPT_CONTRACT_BOUND is True, 'RPM actual auditor schema review is not bound'
assert [row[0] for row in audit_specs] == ['integration', 'rpm_stats_offset', 'spss', 'llcc', 'dp', 'dsi', 'dispcc']
audits, reports = [], {}
for name, auditor, auditor_hash, receipt, receipt_hash in audit_specs:
    auditor_pin, receipt_pin = pin(auditor, auditor_hash), pin(receipt, receipt_hash)
    obj = read(receipt)
    assert obj['passed'] is True and obj['auditor_sha256'] == auditor_hash
    archive = obj.get('zip', obj.get('archive', obj.get('input')))
    assert archive['bytes'] == ZIP_BYTES and archive['sha256'] == ZIP_SHA
    head = obj.get('expected_head', obj.get('head', obj.get('component', {}).get('head')))
    assert head == HEAD
    binary = obj.get('qemu', {}).get('sha256', obj.get('component', {}).get('qemu_sha256_from_separately_audited_binary'))
    assert binary == QEMU_SHA
    audits.append({'name': name, 'auditor': auditor_pin, 'receipt': receipt_pin, 'passed': True})
    reports[name] = obj

audit_by_name = {row['name']: row for row in audits}
assert len(audit_by_name) == len(audits) == 7
aggregate = reports['integration']
assert aggregate['components']['count'] == 97 and aggregate['linux']['count'] == 5
assert aggregate['ci_harness_unittests'] == 35
assert aggregate['qemu']['bytes'] == QEMU_BYTES
source_gen = {}
for key, count in [('generation', 10), ('dsi_generation', 13), ('dp_generation', 17), ('llcc_generation', 8), ('spss_generation', 2), ('rpm_stats_offset_generation', 4)]:
    row = aggregate[key]
    assert row['inputs_verified_from_actual_bytes'] == count and row['outputs_verified_from_actual_bytes'] == 2
    source_gen[key] = {k: v for k, v in row.items() if k in (
        'inputs_verified_from_actual_bytes', 'outputs_verified_from_actual_bytes',
        'committed_output_pins', 'source_revision', 'geometry_source_revision')}
assert aggregate['rpm_stats_offset_generation']['generated_contract'] == RPM_GENERATED_CONTRACT
assert aggregate['rpm_stats_offset_generation']['all_primary_git_blob_identities_verified'] is True
assert aggregate['rpm_stats_offset_generation']['statistics_payload_implemented'] is False
assert aggregate['rpm_stats_offset_generation']['native_reset_value_known'] is False
assert aggregate['rpm_stats_offset_generation']['captured_runtime_word_claimed'] is False
assert aggregate['rpm_stats_offset_generation']['aop_execution_claimed'] is False
audit_by_name['integration']['scope'] = {'description': aggregate['scope'], 'component_gates': 97,
    'linux_gates': 5, 'ci_harness_unittests_from_actual_push_artifact': 35,
    'actual_archive_and_elf_bytes_hashed': True, 'source_generation': source_gen,
    'videocc': {k: v for k, v in aggregate['videocc'].items() if not isinstance(v, list)},
    'supplementary_wire_audits': aggregate['requires_supplementary_wire_audits']}
rpm = reports['rpm_stats_offset']
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

gh_path = REVIEW/'rpmstats-66d9e20-final-ci-github-metadata.json'
base_path = EV/'rpmstats-66d9e20-pr-base-identity.json'
gh, refs = read(gh_path), read(base_path)
assert gh['head']['sha'] == HEAD and gh['head']['tree']['sha'] == TREE
assert [r['sha'] for r in gh['head']['parents']] == [SOURCE_PARENT]
assert gh['merge']['sha'] == MERGE and gh['merge']['tree']['sha'] == TREE
assert [r['sha'] for r in gh['merge']['parents']] == [BASE_HEAD, HEAD]
assert refs['main_ref']['object']['sha'] == refs['pr']['base']['sha'] == BASE_HEAD
assert refs['pr']['head']['sha'] == HEAD and refs['pr']['number'] == PR_NUMBER
assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE
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
artifact_path = EV/'rpmstats-push-artifacts-final.json'
artifact_rows = [a for a in read(artifact_path)['artifacts'] if a['id'] == ARTIFACT_ID]
assert len(artifact_rows) == 1
artifact = artifact_rows[0]
assert artifact['name'] == 'tb371fc-public-gates-'+HEAD and artifact['expired'] is False
assert artifact['size_in_bytes'] == ZIP_BYTES and artifact['digest'] == 'sha256:'+ZIP_SHA
assert artifact['workflow_run']['id'] == PUSH_RUN_ID and artifact['workflow_run']['head_sha'] == HEAD

a77_path = REVIEW/'rpmstats-66d9e20-a77-rawlog-audit-001.json'
a77 = read(a77_path)
assert a77['passed'] is True and a77['source_head'] == HEAD and a77['matching_push_pr_tree'] == TREE
assert {r['job_id'] for r in a77['runs']} == {PUSH_A77_JOB, PR_A77_JOB}
assert len(a77['runs']) == 2 and all(r['guest_checks'] == 852 and r['shared_pci_ufs_case_count'] == 17 for r in a77['runs'])
for r in a77['runs']:
    assert (r['run_id'], r['event'], r['actual_checkout']) == ({PUSH_A77_JOB: (PUSH_RUN_ID, 'push', HEAD), PR_A77_JOB: (PR_RUN_ID, 'pull_request', MERGE)}[r['job_id']])
    assert r['job_completed_success'] is True and r['all_job_steps_completed_success'] is True
    assert len(r['invalid_configurations']) == 2
    assert pin(r['log_path'], r['log_sha256'])['bytes'] == r['log_bytes']
board_path = REVIEW/'rpmstats-66d9e20-board-rawlog-observation-001.json'
board = read(board_path)
assert board['passed'] is True and board['source_head'] == HEAD and board['matching_push_pr_tree'] == TREE
assert {r['job_id'] for r in board['runs']} == {PUSH_BOARD_JOB, PR_BOARD_JOB}
assert len(board['runs']) == 2
for r in board['runs']:
    assert (r['run_id'], r['job_id']) == {'push': (PUSH_RUN_ID, PUSH_BOARD_JOB), 'pr': (PR_RUN_ID, PR_BOARD_JOB)}[r['role']]
    assert r['component_gates'] == 97 and r['linux_gates'] == 5 and r['extra_ufs_checks'] == [39, 61]
    assert r['completed_success'] is True and r['run_attempt'] == 1
    assert r['actual_checkout'] == {'push': HEAD, 'pr': MERGE}[r['role']]
    assert r['host_unit_suites']['step_number'] == 4
    assert r['host_unit_suites']['all_host_unittest_counts_in_order'] == [15, 35, 28, 43, 16, 15]
    assert [(x['suite'], x['tests'], x['verdict']) for x in r['host_unit_suites']['first_two_suites']] == [('source_download', 15, 'OK'), ('ci_device_tests', 35, 'OK')]
    assert pin(r['raw_log']['path'], r['raw_log']['sha256'])['bytes'] == r['raw_log']['bytes']
ufs_path = EV/'ufs-66d9e20-final-observation-001.json'
ufs = read(ufs_path)
assert ufs['passed'] is True and ufs['head'] == HEAD and ufs['archive']['sha256'] == ZIP_SHA and ufs['qemu']['sha256'] == QEMU_SHA
assert ufs['run_id'] == PUSH_RUN_ID and ufs['artifact_id'] == ARTIFACT_ID
assert ufs['archive']['bytes'] == ZIP_BYTES and ufs['qemu']['bytes'] == QEMU_BYTES
assert [(r['check_count'], r['source_checks'], r['target_checks'], r['passed']) for r in ufs['cases']] == [(39, 21, 18, True), (61, 34, 27, True)]

current_publication_path = REVIEW/'rpmstats-66d9e20-independent-publication-review-001.json'
previous_publication_path = REVIEW/'rpmstats-75cc1dc-independent-publication-review-001.json'
current_publication_pin = pin(current_publication_path, 'b552802ae54df79c685b2d4e396bf72113477946392f6176f62eab68a5a49b18')
previous_publication_pin = pin(previous_publication_path, '18463921f775a9dea9ae4a2f8f29ab2e3ee8dff08a36a1b1574c32cd6b8e77de')
current_publication = read(current_publication_path)
previous_publication = read(previous_publication_path)
assert current_publication['schema'] == 'tb371fc-rpmstats-66d9e20-independent-source-review-v1'
assert current_publication['source_publication_review_passed'] is True
assert current_publication['source_commit'] == HEAD and current_publication['source_tree'] == TREE
assert current_publication['parent'] == SOURCE_PARENT and current_publication['pr_number'] == PR_NUMBER
assert current_publication['pr_merge'] == MERGE and current_publication['pr_base'] == BASE_HEAD
assert current_publication['push_and_PR_tree_identical'] is True
assert current_publication['all_other_tree_entries_unchanged'] is True
assert current_publication['unchanged_tree_roots_compared'] == 494
assert current_publication['old75cc_compile_failure_retained'] is True
assert current_publication['final_publication_path_count'] == 38
assert current_publication['runtime_CI_pass_claimed'] is False
assert current_publication['QEMU_or_guest_executed'] is False
assert current_publication['device_RPC_or_SSH_used'] is False
assert current_publication['prior36file_review'] == {'bytes': 17639,
    'sha256': '18463921f775a9dea9ae4a2f8f29ab2e3ee8dff08a36a1b1574c32cd6b8e77de',
    'source': SOURCE_PARENT}
assert previous_publication['schema'] == 'tb371fc-rpm-stats-offset-independent-publication-review-v1'
assert previous_publication['source_publication_review_passed'] is True
assert previous_publication['source_commit'] == SOURCE_PARENT
assert previous_publication['source_tree'] == '5ba0440e3aea2545169f95e684ede61b9d372c2d'
assert previous_publication['sole_parent'] == BASE_HEAD
assert previous_publication['file_count'] == len(previous_publication['files']) == 36
assert previous_publication['total_bytes'] == 617133
assert previous_publication['all_published_file_bytes_match_frozen_candidate_manifest'] is True
assert previous_publication['all_tree_modes_types_sizes_blob_ids_match'] is True
assert previous_publication['complete_compare_exactly_manifest_paths'] is True
assert previous_publication['unlisted_base_files_unchanged_by_complete_compare'] is True
assert previous_publication['runtime_CI_pass_claimed'] is False
current_published_files = {}
for row in previous_publication['files']:
    assert row['path'] not in current_published_files
    assert row['mode'] == '100644' and row['type'] == 'blob'
    assert row['full_remote_blob_readback'] is True and row['frozen_candidate_manifest_matched'] is True
    local_path = REVIEW/'source-readback-001'/row['path']
    actual = pin(local_path, row['sha256'])
    assert actual['bytes'] == row['bytes']
    raw = local_path.read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['blob_sha']
    current_published_files[row['path']] = {key: row[key] for key in ('path', 'bytes', 'sha256', 'blob_sha')}
changed_readbacks = current_publication['three_actual_changed_files_fully_readback']
assert [row['path'] for row in changed_readbacks] == [
    'include/hw/misc/qcom-sm8250-rpm-stats-offset.h',
    'tb371fc/evidence/rpm-stats-offset-20261125-6a5/ci-include-path-correction.json',
    'tb371fc/evidence/rpm-stats-offset-20261125-6a5/include-path-correction-review.json']
current_changed_source_pins = []
for row in changed_readbacks:
    assert row['full_exact_head_readback'] is True
    local_path = REVIEW/'source-readback-66d9e20'/row['path']
    actual = pin(local_path, row['sha256'])
    assert actual['bytes'] == row['bytes']
    raw = local_path.read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_sha1'] == row['blob_sha']
    current_changed_source_pins.append(actual)
    current_published_files[row['path']] = {key: row[key] for key in ('path', 'bytes', 'sha256', 'blob_sha')}
assert len(current_published_files) == 38
assert sum(row['bytes'] for row in current_published_files.values()) == 622404
expected_runner_identities = {'tb371fc/scripts/boot_android.py': {'bytes': 40941, 'sha256': '61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1', 'git_blob_sha1': '1614a9c6032457fecf370559ed66c45823f16a3f'}, 'tb371fc/scripts/boot_android_from_ready.py': {'bytes': 31692, 'sha256': 'b2ef676a99c7064b9d31035d1b3e1e471d613500cba6373a0992b4c4f4e4154d', 'git_blob_sha1': 'e2b4d59c0460286ba1f948bfe9afca73f51f05fc'}, 'tb371fc/scripts/android_boot_inputs.py': {'bytes': 36402, 'sha256': '08affe8ef5c40c7350be9e4c0f0482c4c5e594a3dcff7e3a88cf7887ea1eb5d8', 'git_blob_sha1': '4291d8d096f0cafddb58c201030ccf210c50250c'}, 'tb371fc/scripts/boot_qmp.py': {'bytes': 2822, 'sha256': 'b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c', 'git_blob_sha1': '54e845229266a212d0a192276e5d0228c3cf716f'}, 'tb371fc/scripts/legacy_printk.py': {'bytes': 4275, 'sha256': '6b7d18188ef8987c32135fef179f271dcf7284766a233626d09269696cada27d', 'git_blob_sha1': '055df2101bd15268ab55189795fdaa1b78c473ff'}}
runner_rows = current_publication['five_runners_fully_readback_unchanged_from_54ec']
assert len(runner_rows) == 5 and {row['path'] for row in runner_rows} == set(expected_runner_identities)
current_runner_file_pins = []
for row in runner_rows:
    assert row['full_exact_head_readback'] is True
    assert {key: row[key] for key in ('bytes', 'sha256', 'git_blob_sha1')} == expected_runner_identities[row['path']]
    local_path = REVIEW/'source-readback-66d9e20'/row['path']
    actual = pin(local_path, row['sha256'])
    assert actual['bytes'] == row['bytes']
    raw = local_path.read_bytes()
    assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == row['git_blob_sha1']
    current_runner_file_pins.append(actual)

local_initial_path = SOURCE_EVIDENCE/'ci-local-unit-001.json'
local_actual_path = SOURCE_EVIDENCE/'ci-local-unit-002.json'
local_initial_pin = pin(local_initial_path, '865984016a4bf612a93203e0728d9ec1a619a88e478b61a32ef68146c7176739')
local_actual_pin = pin(local_actual_path, '8fd999774fba3548d99a2a58326ce4f697f21e61ed0b8ebf8f142444e9a38273')
local_initial, local_actual = read(local_initial_path), read(local_actual_path)
assert local_initial['exit_code'] == local_actual['exit_code'] == 1
assert local_initial['formal_ci_pass_claimed'] is False and local_actual['formal_ci_pass_claimed'] is False
local_initial_stderr = SOURCE_EVIDENCE/'ci-unit-all.stderr.log'
local_actual_stderr = SOURCE_EVIDENCE/'ci-unit-all-002.stderr.log'
local_initial_log_pin = pin(local_initial_stderr, '614fb81526194c64cd5944903501ef47b541523f31a36c0ad3f5c6b8c202a1b6')
local_actual_log_pin = pin(local_actual_stderr, 'e266c0df006bda9b3035bd24b2b9216d258589e2e3efa85b5dcaa6f12db5de64')
assert local_initial['stderr_bytes'] == local_initial_log_pin['bytes'] and local_initial['stderr_sha256'] == local_initial_log_pin['sha256']
assert local_actual['stderr_bytes'] == local_actual_log_pin['bytes'] and local_actual['stderr_sha256'] == local_actual_log_pin['sha256']
assert "ModuleNotFoundError: No module named 'ci_device_tests'" in local_initial_stderr.read_text()
assert 'Ran 35 tests in ' in local_actual_stderr.read_text() and 'FAILED (errors=1)' in local_actual_stderr.read_text()
assert 'PermissionError: [Errno 1] Operation not permitted' in local_actual_stderr.read_text()
assert len(re.findall(r'^test_.* \.\.\. ok$', local_actual_stderr.read_text(), re.M)) == 34
failed_head_path = SOURCE_EVIDENCE/'ci-include-path-correction.json'
failed_head_review_path = SOURCE_EVIDENCE/'include-path-correction-review.json'
failed_head_pin = pin(failed_head_path, 'c83c59a3d4076007c26a02b39e21707bbe42fe9a994fe8e327541cc4a9fd4468')
failed_head_review_pin = pin(failed_head_review_path, '2ded18bc3ddb68f4960c707fbbd0b345af3887ff3f81173f5d32606f2c22a44c')
failed_head, failed_head_review = read(failed_head_path), read(failed_head_review_path)
assert failed_head['failed_source_commit'] == failed_head_review['failed_head'] == SOURCE_PARENT
assert failed_head['push_run'] == 45532378559 and failed_head['push_board_job'] == 138930599415
assert failed_head['compiled_device_pass_claimed'] is False and failed_head_review['compiled_pass_claimed'] is False
assert failed_head['header']['new_sha256'] == '946df3bee216839b46001d4ea3065bda6563413a0c753a7b221fffea3b77ad0b'

failed_ci_path = REVIEW/'rpmstats-75cc1dc-failed-ci-observation-001.json'
failed_ci_pin = pin(failed_ci_path, '0ef5b40ffe9fb06aba132c310bc8dabf94aabcb708a9f3388c8f7b0484878a6a')
failed_ci = read(failed_ci_path)
assert failed_ci['schema'] == 'tb371fc-rpmstats-75cc1dc-failed-ci-observation-v1'
assert failed_ci['source_commit'] == SOURCE_PARENT and failed_ci['corrected_source_head'] == HEAD
assert failed_ci['run_id'] == 45532378559 and failed_ci['job_id'] == 138930599415
assert failed_ci['run_attempt'] == 1 and failed_ci['event'] == 'push'
assert failed_ci['run_status'] == failed_ci['job_status'] == 'completed'
assert failed_ci['run_conclusion'] == failed_ci['job_conclusion'] == 'failure'
assert failed_ci['failed_step']['conclusion'] == 'failure'
assert failed_ci['runtime_CI_pass_claimed'] is False and failed_ci['failure_receipt_superseded_or_erased'] is False
assert failed_ci['corrected_head_requires_its_own_actual_CI'] is True
for source_row in [failed_ci['actual_log'], *failed_ci['metadata']]:
    actual = pin(REVIEW/source_row['file'], source_row['sha256'])
    assert actual['bytes'] == source_row['bytes']

result = {'schema': 'tb371fc-rpm-stats-offset-66d9e20-qualified-artifact-v1', 'passed': True,
    'qualification': 'Exact-head push and PR CI plus seven independent actual-push-ZIP audits passed',
    'qualification_evidence_observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'repository': 'cyjin-yl/qemu-tb371fc', 'commit': HEAD, 'tree': TREE,
    'artifact': {'id': ARTIFACT_ID, 'artifact_name': artifact['name'], 'run_id': PUSH_RUN_ID,
        'api_url': artifact['url'], 'archive_bytes': ZIP_BYTES, 'archive_sha256': ZIP_SHA,
        'qemu_member': aggregate['qemu']['member'], 'qemu_bytes': QEMU_BYTES, 'qemu_sha256': QEMU_SHA},
    'workflow_runs': workflow_runs, 'github_evidence': pin(gh_path, '3e3fd7a7e073efbbf0f8a58d45116e8bdb877d564b3387ff2dd8f32d4b75130d'), 'pr_base_evidence': pin(base_path, '36e1361a46dd2bf548448cb22593025d91106b1ba7fa14d6011791bcfecb8f03'),
    'artifact_api_evidence': pin(artifact_path, 'a82d527490a4e7b5721d4f908b3e15be787d5e8c2b9e9979aa660d0326f5a86b'), 'assembler': pin(__file__),
    'bounded_artifact_download_evidence': pin(EV/'artifact-download-001.json', '290bce66d3acc9512c743bbd987df38be49c3aff4bd99ac1a285e3a9850e853a'),
    'all_seven_independent_audits_passed': True, 'audits': audits,
    'a77_actual_raw_log_audit': pin(a77_path, A77_RECEIPT_SHA),
    'a77_each_job_executed_in_this_attempt': True,
    'independent_board_log_observation': pin(board_path, BOARD_RECEIPT_SHA),
    'extra_linux_ufs_observation': {'receipt': pin(ufs_path, UFS_RECEIPT_SHA),
        'checks': [39, 61], 'boundary': ufs['boundary']},
    'additional_host_unit_suites_each_board_job': {'source_download': 15, 'ci_device_tests': 35, 'boot_android': 43, 'included_in_component_gate_count': False},
    'runner_source_sha256': '61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1',
    'historical_runner_behavior_review': pin('/dev/shm/spss-54ec744-ci-evidence/publication-readback-001/pr32-runner-source-review.json', 'a3583e1cce51b346ec8c952f8d3bc95613dd1f2d004da6b9bbb7854b6faf4d73'),
    'historical_runner_template_review': pin('/dev/shm/spss-54ec744-ci-evidence/publication-readback-001/pr32-runner-template-review.json', '65079790c5026729b3010c862ef98c883ca3221876e5688df63605efe85ad2db'),
    'current_head_publication_review': {'receipt': current_publication_pin,
        'prior36_full_readback_receipt': previous_publication_pin,
        'source_commit': HEAD, 'source_tree': TREE, 'final_publication_path_count': 38,
        'total_published_bytes': 622404, 'exact_current_changed_file_readbacks': current_changed_source_pins,
        'other_35_original_blobs_bound_by_unchanged_tree_identity': True,
        'all_38_actual_retained_readback_bytes_and_git_blobs_rechecked': True},
    'current_head_runner_readback': {'receipt': current_publication_pin, 'source_commit': HEAD,
        'source_tree': TREE, 'exact_file_count': 5, 'files': current_runner_file_pins,
        'current_head_bytes_established_by_historical_review_alone': False,
        'runner_behavior_review_is_historical_and_byte_identity_is_current': True},
    'ci_attempts_observed': 1, 'ci_failure_observed_for_this_head': False,
    'local_unit_limitation': {'initial_import_failure_receipt': local_initial_pin,
        'initial_import_failure_stderr': local_initial_log_pin, 'initial_test_count': 0,
        'actual_local_run_receipt': local_actual_pin, 'actual_local_run_stderr': local_actual_log_pin,
        'actual_local_test_count': 35, 'local_pass_count': 34, 'local_environment_error_count': 1,
        'local_all_pass_claimed': False, 'local_socket_eperm_retained': True,
        'formal_push_actual_35_pass_0_skip': True},
    'retained_failed_source_head': {'source_commit': SOURCE_PARENT,
        'push_run_id': 45532378559, 'pr_run_id': 45532462773, 'push_board_job_id': 138930599415,
        'actual_terminal_failure_observation': failed_ci_pin,
        'failure_and_correction_receipt': failed_head_pin, 'independent_correction_review': failed_head_review_pin,
        'earlier_failed_build_reclassified_as_pass': False, 'earlier_head_native_qualified': False},
    'retained_prior_head_ci': pin('/dev/shm/spss-54ec744-ci-evidence/spss-54ec744-qualification.json', '0a52a79598ff4f57e1697b7e8327d5229b3b2491e8947f7142c1e2bbc97d5206'),
    'retained_earlier_head_ci': pin('/dev/shm/spss-a3a-ci-evidence/spss-a3a6183-reviewed-ci-before-runner-advance.json', '41dd65062d5be0e68c8f7d0ad2cdb404fc6419a2920e5ed7f1f61a755b5922f9'),
    'native_started': False, 'native_executed': False, 'coordinator_deployed': False,
    'host_boot_guard_bound': False, 'device_rpc_used_for_qualification': False,
    'android_userspace_established': False, 'ui_established': False, 'android_boot_established': False,
    'read_only_qualification_assembly': True, 'additional_optional_tests_run': False,
    'scope': 'Compiled CI artifact qualification for one later original-input native attempt. Does not establish original Android userspace, UI, boot completion, SPSS firmware/PAS/READY events, RPM statistics payload/current runtime data, AOP execution, or physical hardware readback/reset behavior.'}
out = EV/'rpmstats-66d9e20-qualification.json'
with out.open('x') as f:
    json.dump(result, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps({'qualification': pin(out), 'native_executed': False, 'passed': True}))
