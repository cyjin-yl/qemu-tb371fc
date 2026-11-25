#!/usr/bin/env python3
"""Assemble exact observed SPSS qualification; no network, QEMU or device calls."""
from pathlib import Path
import datetime
import hashlib
import json

BASE = Path('/workspace/scratch/6a5ea697094c')
EV = Path('/dev/shm/spss-54ec744-ci-evidence')
REVIEW = Path('/dev/shm/spss-54ec744-ci-review')
HEAD = '54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7'
TREE = '6602490b518ab849dc9a0fd2636045c26954c145'
BASE_HEAD = '28def523ecd39c90bdedee7a2b38ba71353b5f88'
MERGE = 'b2b59958b5ea2b76d1eaff24c5490cd47c8ef60d'
ZIP_SHA = '9fdd4ad25d9b9f272456144de43f124381e03571c7d0d91d022d151ea82d687e'
QEMU_SHA = 'b50345fe7dba411426779f27963ecb1b08d1ae6fa4d83e50e7cea4351cbf7d25'
ZIP_BYTES = 55527863
QEMU_BYTES = 63675200
ARTIFACT_ID = 14982026237
A77_RECEIPT_SHA = '69ce21bc51711f06b61fa9bbaff96c2d430148752935ab1aa7241aad2a90cfa8'
UFS_RECEIPT_SHA = '203cab9c3fb3a437d167c8ca9b3d26b58cb32839bac9c6aef092fbe6f2c8f868'
BOARD_RECEIPT_SHA = 'a74727c94a7f6c1ac69055d299a07e350280a425a73dc4d35cd23881a3005514'

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
    ('integration', EV/'audit_spss_integration_artifact_54ec744.py',
     '96009fea60641a72a5095c1c28edc39eeb3bbe1bc67fe9d6d27a1607235e54e2',
     EV/'spss-54ec744-final-integration-audit-001.json',
     '96e740e8499cf70be06f3168d06ad6dc3a6259619219530dd9f83c71898b4bd8'),
    ('spss', Path('/dev/shm/spss-54ec744-independent-audit/audit_spss_irq_mask_artifact_54ec744.py'),
     'a8274e31f901a6e5a30b1b3680f805f7e11e7a9f9188d363de12ef54ef0a1356',
     Path('/dev/shm/spss-54ec744-independent-audit/spss-54ec744-final-independent-audit-001.json'),
     '8f3bc256d9aeb502bf1465f7cf06fdb96c4904abc13c0eeded72fc11e8c09c2b'),
    ('llcc', EV/'audit_llcc_artifact_spss_54ec744.py',
     '59ce7e1c342b6c54685bf1b6fdd761f67523c88765cd108631e68751dc84410d',
     EV/'llcc-54ec744-final-independent-audit-001.json',
     '76e357f9dc772cb9e5cbd0125d84e97dc8f7a01cef2227fa586a00c354ff9919'),
    ('dp', EV/'audit_dp_pll_artifact_spss_54ec744.py',
     'eb3f75fe867fc50f0238e298cc1cf7e27d4d8400191b6b35d2e78917c35d9b35',
     EV/'dp-54ec744-final-independent-audit-001.json',
     'c066fa4eab09cf4d79f93e958bf335769aedfd0303b57ecc5ffefb5c08016399'),
    ('dsi', BASE/'dsi-pll-candidate/.work/audit_dsi_pll_artifact.py',
     '0e0b7e6ebe3a9486383c2946669629a97d4d037414be9d6e5ea28970462954fe',
     EV/'dsi-54ec744-final-independent-audit-001.json',
     '7b26efdfdfe23fcb0be4b0628137372943225d5d66ee5904e50f7a48d16a082e'),
    ('dispcc', BASE/'dsi-pll-candidate/.work/audit_dispcc_wide_artifact_rootpath.py',
     '62837675b808db7ed5671e9cff7653f7b76d4c295d668919ffff10a4134fc4c8',
     EV/'dispcc-54ec744-final-independent-audit-001.json',
     '7a668ddaaff9d8c17c987f8e1125190b75e42113a4bcf440a4e0790008efedda'),
]
assert all(isinstance(x, str) and len(x) == 64 for x in [ZIP_SHA, QEMU_SHA, A77_RECEIPT_SHA, UFS_RECEIPT_SHA, BOARD_RECEIPT_SHA] + [a[4] for a in audit_specs]), 'Actual artifact and receipt pins are unset; qualification is prohibited'
assert all(type(x) is int and x > 0 for x in [ZIP_BYTES, QEMU_BYTES, ARTIFACT_ID]), 'Actual artifact geometry is unset'
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

aggregate = reports['integration']
assert aggregate['components']['count'] == 95 and aggregate['linux']['count'] == 5
assert aggregate['ci_harness_unittests'] == 33
assert aggregate['qemu']['bytes'] == QEMU_BYTES
source_gen = {}
for key, count in [('generation', 10), ('dsi_generation', 13), ('dp_generation', 17), ('llcc_generation', 8), ('spss_generation', 2)]:
    row = aggregate[key]
    assert row['inputs_verified_from_actual_bytes'] == count and row['outputs_verified_from_actual_bytes'] == 2
    source_gen[key] = {k: v for k, v in row.items() if k in (
        'inputs_verified_from_actual_bytes', 'outputs_verified_from_actual_bytes',
        'committed_output_pins', 'source_revision', 'geometry_source_revision')}
audits[0]['scope'] = {'description': aggregate['scope'], 'component_gates': 95,
    'linux_gates': 5, 'ci_harness_unittests_from_actual_push_artifact': 33,
    'actual_archive_and_elf_bytes_hashed': True, 'source_generation': source_gen,
    'videocc': {k: v for k, v in aggregate['videocc'].items() if not isinstance(v, list)},
    'supplementary_wire_audits': aggregate['requires_supplementary_wire_audits']}
spss = reports['spss']
assert spss['component']['checks'] == 21
assert [len(spss[k]) for k in ['original_streams', 'completed_migrations', 'profile_rejections', 'one_bit_rejections']] == [4, 3, 2, 6]
assert len(spss['ordinary_and_special']) == 10
for row in spss['completed_migrations']:
    assert [row[k] for k in ['first_pure_getters', 'actual_qom_getters_total', 'prewarm_actual_device_mmio', 'first_device_mmio_delta', 'first_cont_delta']] == [18, 144, 1, 0, 0]
    assert row['source_snapshot_restored_before_cont'] is True
audits[1]['scope'] = {'checks': 21, 'ordinary_and_special_cases': 10,
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
audits[2]['scope'] = {'checks': 32, 'unmodified_streams': 5, 'stream_files': 9,
    'completed_migrations': 4, 'profile_rejections': 2, 'single_bit_rejections': 4,
    'bytes_per_original_payload': 783, 'first_target_qom_getters_each': 313,
    'first_target_actual_mmio_delta_each': 0, 'first_target_actual_cont_count_each': 0,
    'source_generation': llcc['source_generation']}
audits[3]['scope'] = reports['dp']['summary']
audits[4]['scope'] = reports['dsi']['summary']
audits[5]['scope'] = {k: v for k, v in reports['dispcc']['dispcc'].items() if k not in ('sources', 'rejections')}

gh_path = REVIEW/'spss-54ec744-final-ci-github-metadata.json'
base_path = REVIEW/'spss-54ec744-pr-base-identity.json'
gh, refs = read(gh_path), read(base_path)
assert gh['head']['sha'] == HEAD and gh['head']['tree']['sha'] == TREE
assert [r['sha'] for r in gh['head']['parents']] == ['a3a6183d4e9842b2e61d3d1c9cd7b69b44165187', BASE_HEAD]
assert gh['merge']['sha'] == MERGE and gh['merge']['tree']['sha'] == TREE
assert [r['sha'] for r in gh['merge']['parents']] == [BASE_HEAD, HEAD]
assert refs['main_ref']['object']['sha'] == refs['pr']['base']['sha'] == BASE_HEAD
assert refs['pr']['head']['sha'] == HEAD and refs['pr']['number'] == 30
assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE
workflow_runs = []
for prefix, run_id, event, board_job, cpu_job in [
    ('push', 45521455233, 'push', 138894068975, 138894069284),
    ('pr', 45521459728, 'pull_request', 138894085689, 138894085286)]:
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
artifact_path = EV/'spss-push-artifacts-final.json'
artifact_rows = [a for a in read(artifact_path)['artifacts'] if a['id'] == ARTIFACT_ID]
assert len(artifact_rows) == 1
artifact = artifact_rows[0]
assert artifact['name'] == 'tb371fc-public-gates-'+HEAD and artifact['expired'] is False
assert artifact['size_in_bytes'] == ZIP_BYTES and artifact['digest'] == 'sha256:'+ZIP_SHA
assert artifact['workflow_run']['id'] == 45521455233 and artifact['workflow_run']['head_sha'] == HEAD

a77_path = REVIEW/'spss-54ec744-a77-rawlog-audit-001.json'
a77 = read(a77_path)
assert a77['passed'] is True and a77['source_head'] == HEAD and a77['matching_push_pr_tree'] == TREE
assert {r['job_id'] for r in a77['runs']} == {138894069284, 138894085286}
assert len(a77['runs']) == 2 and all(r['guest_checks'] == 852 and r['shared_pci_ufs_case_count'] == 17 for r in a77['runs'])
for r in a77['runs']:
    assert len(r['invalid_configurations']) == 2
    assert pin(r['log_path'], r['log_sha256'])['bytes'] == r['log_bytes']
board_path = REVIEW/'spss-54ec744-board-rawlog-observation-001.json'
board = read(board_path)
assert board['passed'] is True and board['source_head'] == HEAD and board['matching_push_pr_tree'] == TREE
assert {r['job_id'] for r in board['runs']} == {138894068975, 138894085689}
assert len(board['runs']) == 2
for r in board['runs']:
    assert r['component_gates'] == 95 and r['linux_gates'] == 5 and r['extra_ufs_checks'] == [39, 61]
    assert r['completed_success'] is True and r['run_attempt'] == 1
    assert r['actual_checkout'] == {'push': HEAD, 'pr': MERGE}[r['role']]
    assert r['host_unit_suites']['step_number'] == 4
    assert r['host_unit_suites']['all_host_unittest_counts_in_order'] == [15, 33, 28, 43, 16, 15]
    assert [(x['suite'], x['tests'], x['verdict']) for x in r['host_unit_suites']['first_two_suites']] == [('source_download', 15, 'OK'), ('ci_device_tests', 33, 'OK')]
    assert pin(r['raw_log']['path'], r['raw_log']['sha256'])['bytes'] == r['raw_log']['bytes']
ufs_path = EV/'ufs-54ec744-final-observation-001.json'
ufs = read(ufs_path)
assert ufs['passed'] is True and ufs['head'] == HEAD and ufs['archive']['sha256'] == ZIP_SHA and ufs['qemu']['sha256'] == QEMU_SHA
assert [(r['check_count'], r['source_checks'], r['target_checks'], r['passed']) for r in ufs['cases']] == [(39, 21, 18, True), (61, 34, 27, True)]

result = {'schema': 'tb371fc-spss-54ec744-qualified-artifact-v1', 'passed': True,
    'qualification': 'Exact-head push and PR CI plus six independent actual-push-ZIP audits passed',
    'qualification_evidence_observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'repository': 'cyjin-yl/qemu-tb371fc', 'commit': HEAD, 'tree': TREE,
    'artifact': {'id': ARTIFACT_ID, 'artifact_name': artifact['name'], 'run_id': 45521455233,
        'api_url': artifact['url'], 'archive_bytes': ZIP_BYTES, 'archive_sha256': ZIP_SHA,
        'qemu_member': aggregate['qemu']['member'], 'qemu_bytes': QEMU_BYTES, 'qemu_sha256': QEMU_SHA},
    'workflow_runs': workflow_runs, 'github_evidence': pin(gh_path), 'pr_base_evidence': pin(base_path),
    'artifact_api_evidence': pin(artifact_path), 'assembler': pin(__file__),
    'all_six_independent_audits_passed': True, 'audits': audits,
    'a77_actual_raw_log_audit': pin(a77_path, A77_RECEIPT_SHA),
    'a77_each_job_executed_in_this_attempt': True,
    'independent_board_log_observation': pin(board_path, BOARD_RECEIPT_SHA),
    'extra_linux_ufs_observation': {'receipt': pin(ufs_path, UFS_RECEIPT_SHA),
        'checks': [39, 61], 'boundary': ufs['boundary']},
    'additional_host_unit_suites_each_board_job': {'source_download': 15, 'ci_device_tests': 33, 'boot_android': 43, 'included_in_component_gate_count': False},
    'runner_source_sha256': '61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1',
    'runner_source_review': pin('/dev/shm/native-spss-coordinator-review-6a5/pr32-runner-independent-source-review-001.json', 'a3583e1cce51b346ec8c952f8d3bc95613dd1f2d004da6b9bbb7854b6faf4d73'),
    'ci_attempts_observed': 1, 'ci_failure_observed_for_this_head': False,
    'local_unit_limitation': {'receipt': pin('/dev/shm/pil-tz-candidate-6a5/.work/spss-ci-local-unit-001.json', '6b1630730252f932102ea17413981e1c1d21ad33c53e460c2698b9473c8b5d64'),
        'local_32_pass_1_environment_error_retained': True, 'formal_push_actual_33_pass_0_skip': True},
    'retained_prior_head_ci': pin('/dev/shm/spss-a3a-ci-evidence/spss-a3a6183-reviewed-ci-before-runner-advance.json', '41dd65062d5be0e68c8f7d0ad2cdb404fc6419a2920e5ed7f1f61a755b5922f9'),
    'native_started': False, 'native_executed': False, 'coordinator_deployed': False,
    'host_boot_guard_bound': False, 'device_rpc_used_for_qualification': False,
    'android_userspace_established': False, 'ui_established': False, 'android_boot_established': False,
    'read_only_qualification_assembly': True, 'additional_optional_tests_run': False,
    'scope': 'Compiled CI artifact qualification for one later original-input native attempt. Does not establish original Android userspace, UI, boot completion, SPSS firmware/PAS/READY events, or physical hardware readback/reset behavior.'}
out = EV/'spss-54ec744-qualification.json'
with out.open('x') as f:
    json.dump(result, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps({'qualification': pin(out), 'native_executed': False, 'passed': True}))
