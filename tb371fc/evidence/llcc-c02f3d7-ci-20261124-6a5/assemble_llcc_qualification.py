#!/usr/bin/env python3
"""Assemble exact observed evidence; no network, device, QEMU or guest execution."""
from pathlib import Path
import datetime
import hashlib
import json

BASE = Path('/workspace/scratch/6a5ea697094c')
EV = Path('/dev/shm/llcc-ci-evidence')
HEAD = 'c02f3d794e85cb5bc95d1aca62780542e806d4dc'
TREE = 'e16fa89f92cb997ca013415c8ced5adcc1db8659'
ZIP_SHA = '40015b5451fcdd45aef215637eb76979638d3d36d5b967a97bc9048107834678'
QEMU_SHA = '10d21844e6c411e64af7a6aade7354d90c0aeffb82643d9676986fc0a160c214'

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
    ('integration', BASE/'llcc-candidate/.work/audit_llcc_integration_artifact_c02f3d7.py',
     '16e3db8f18055edfa869838e73b8d95206449472e6b5685c0d9991943e4febe5',
     EV/'llcc-c02f3d7-final-integration-audit-001.json',
     'e85e8e1f885129f98ed517655401bce8048589adca342bc5670326f3aed835fb'),
    ('llcc', Path('/dev/shm/llcc-independent-audit/audit_llcc_artifact_c02f3d7.py'),
     '7f135d9e94509c348f27c8621cf58f2b319ae5cd8684a1ec4a54acda5d38f475',
     Path('/dev/shm/llcc-independent-audit/llcc-c02f3d7-final-independent-audit-001.json'),
     'a27d4060154d9c160838d9684096385bfdaa0d9500d7aab41d267830b4664828'),
    ('dp', EV/'audit_dp_pll_artifact_c02f3d7.py',
     '82d1e11863f4e7dcf768e26c7208fb64658f251596bb1d3482bd3504045eb1de',
     EV/'dp-c02f3d7-final-independent-audit-001.json',
     '4f0049e21478af711d2c4d3e3231ae4531abd88b4818ef805e4fae06b6db7500'),
    ('dsi', BASE/'dsi-pll-candidate/.work/audit_dsi_pll_artifact.py',
     '0e0b7e6ebe3a9486383c2946669629a97d4d037414be9d6e5ea28970462954fe',
     EV/'dsi-c02f3d7-final-independent-audit-001.json',
     'c7d67e6135408971c53c716e4eb3d45c9e876d711d4d00f6dc80de64da0b6097'),
    ('dispcc', BASE/'dsi-pll-candidate/.work/audit_dispcc_wide_artifact_rootpath.py',
     '62837675b808db7ed5671e9cff7653f7b76d4c295d668919ffff10a4134fc4c8',
     EV/'dispcc-c02f3d7-final-independent-audit-001.json',
     '77b95b9387610fd8618168c1fe03ff8282e773c3f1ebc29ef2dbee95662b2f1c')]
audits, reports = [], {}
for name, auditor, auditor_hash, receipt, receipt_hash in audit_specs:
    a, r = pin(auditor, auditor_hash), pin(receipt, receipt_hash)
    obj = read(receipt)
    assert obj['passed'] is True and obj['auditor_sha256'] == auditor_hash
    archive = obj.get('zip', obj.get('archive', obj.get('input')))
    assert archive['bytes'] == 54476107 and archive['sha256'] == ZIP_SHA
    head = obj.get('expected_head', obj.get('head', obj.get('component', {}).get('head')))
    assert head == HEAD
    binary = obj.get('qemu', {}).get('sha256', obj.get('component', {}).get('qemu_sha256_from_separately_audited_binary'))
    assert binary == QEMU_SHA
    audits.append({'name': name, 'auditor': a, 'receipt': r, 'passed': True})
    reports[name] = obj

aggregate = reports['integration']
assert aggregate['components']['count'] == 93 and aggregate['linux']['count'] == 5
assert aggregate['ci_harness_unittests'] == 31
assert aggregate['qemu']['bytes'] == 63645720
source_gen = {}
for key, count in [('generation', 10), ('dsi_generation', 13), ('dp_generation', 17), ('llcc_generation', 8)]:
    row = aggregate[key]
    assert row['inputs_verified_from_actual_bytes'] == count and row['outputs_verified_from_actual_bytes'] == 2
    source_gen[key] = {k: v for k, v in row.items() if k in (
        'inputs_verified_from_actual_bytes', 'outputs_verified_from_actual_bytes',
        'committed_output_pins', 'source_revision', 'geometry_source_revision')}
audits[0]['scope'] = {'description': aggregate['scope'], 'component_gates': 93,
    'linux_gates': 5, 'ci_harness_unittests_from_actual_push_artifact': 31,
    'actual_archive_and_elf_bytes_hashed': True, 'source_generation': source_gen,
    'videocc': {k: v for k, v in aggregate['videocc'].items() if not isinstance(v, list)},
    'supplementary_wire_audits': aggregate['requires_supplementary_wire_audits']}
llcc = reports['llcc']
assert llcc['component']['check_count'] == 32
assert [len(llcc[k]) for k in ['sources', 'completed', 'profile_rejections', 'corruption_rejections']] == [5, 4, 2, 4]
assert llcc['source_generation']['source_table_bytes'] == 640
audits[1]['scope'] = {'checks': 32, 'unmodified_streams': 5, 'stream_files': 9,
    'completed_migrations': 4, 'profile_rejections': 2, 'single_bit_rejections': 4,
    'bytes_per_original_payload': 783, 'first_target_qom_getters_each': 313,
    'first_target_actual_mmio_delta_each': 0, 'first_target_actual_cont_count_each': 0,
    'source_generation': llcc['source_generation'],
    'ordinary': {k: v for k, v in llcc['ordinary'].items() if k != 'cases'}}
audits[2]['scope'] = reports['dp']['summary']
audits[3]['scope'] = reports['dsi']['summary']
audits[4]['scope'] = {k: v for k, v in reports['dispcc']['dispcc'].items()
                       if k not in ('sources', 'rejections')}

github_path = EV/'llcc-c02-qualification-github-evidence.json'
gh = read(github_path)
assert gh['source_commit']['sha'] == HEAD and gh['source_commit']['tree']['sha'] == TREE
assert gh['pr_checkout_commit']['sha'] == '17f4616f928dc0b0aeb815d7e09f4fec0e143be5'
assert gh['pr_checkout_commit']['tree']['sha'] == TREE
assert gh['main_ref']['object']['sha'] == gh['pr']['base']['sha'] == '470834014ab97c58d8bc836eb8bd9b6ded238b40'
assert gh['pr']['head']['sha'] == HEAD and gh['pr']['number'] == 28
workflow_runs = []
for prefix, run_id, event, attempt in [('push', 45501927279, 'push', 1), ('pr', 45501932082, 'pull_request', 2)]:
    run = gh[prefix+'_run']
    jobs = gh[prefix+'_jobs']['jobs']
    assert run['id'] == run_id and run['head_sha'] == HEAD and run['event'] == event
    assert run['status'] == 'completed' and run['conclusion'] == 'success' and run['run_attempt'] == attempt
    assert run['path'] == '.github/workflows/tb371fc.yml'
    assert {j['name'] for j in jobs} == {'board-components-and-linux', 'cortex-a77-architecture'}
    for j in jobs:
        assert j['head_sha'] == HEAD and j['status'] == 'completed' and j['conclusion'] == 'success'
        assert all(s['status'] == 'completed' and s['conclusion'] == 'success' for s in j['steps'])
    workflow_runs.append({k: run[k] for k in ('id', 'event', 'head_branch', 'head_sha', 'run_attempt', 'status', 'conclusion', 'updated_at', 'html_url')})
    workflow_runs[-1]['jobs'] = [{k: j[k] for k in ('id', 'name', 'head_sha', 'status', 'conclusion', 'run_attempt', 'started_at', 'completed_at')} for j in jobs]
artifact = [a for a in gh['push_artifacts']['artifacts'] if a['id'] == 14973051830]
assert len(artifact) == 1
artifact = artifact[0]
assert artifact['name'] == 'tb371fc-public-gates-'+HEAD and artifact['expired'] is False
assert artifact['size_in_bytes'] == 54476107 and artifact['digest'] == 'sha256:'+ZIP_SHA
assert artifact['workflow_run']['id'] == 45501927279 and artifact['workflow_run']['head_sha'] == HEAD

a77_path = EV/'llcc-c02f3d7-a77-rawlog-audit-001.json'
a77 = read(a77_path)
assert a77['passed'] is True and a77['source_head'] == HEAD and a77['matching_push_pr_tree'] == TREE
assert len(a77['runs']) == 2 and all(r['guest_checks'] == 852 and r['shared_pci_ufs_case_count'] == 17 for r in a77['runs'])
carry_path = EV/'llcc-c02f3d7-a77-attempt2-carryforward-observation-001.json'
carry = read(carry_path)
assert carry['passed'] is True and carry['source_head'] == HEAD and carry['new_execution_observed'] is False
assert carry['new_job_id'] == 138835643987 and carry['original_job_id'] == 138827170296
ufs_path = EV/'ufs-c02f3d7-final-observation-001.json'
ufs = read(ufs_path)
assert ufs['passed'] is True and ufs['head'] == HEAD and ufs['archive']['sha256'] == ZIP_SHA and ufs['qemu']['sha256'] == QEMU_SHA
assert [(r['check_count'], r['source_checks'], r['target_checks'], r['passed']) for r in ufs['cases']] == [(39, 21, 18, True), (61, 34, 27, True)]
count_paths = [Path('/dev/shm/llcc-ci-review/c02f3d-push-counts-independent-observation.json'),
               Path('/dev/shm/llcc-ci-review/c02f3d-pr-attempt2-counts-independent-observation.json')]
assert all(p.is_file() for p in count_paths), 'Complete independent board log observations are required'

result = {'schema': 'tb371fc-llcc-c02f3d7-qualified-artifact-v1', 'passed': True,
    'qualification': 'Exact-head push and PR CI plus five independent actual-push-ZIP audits passed',
    'qualification_evidence_observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'repository': 'cyjin-yl/qemu-tb371fc', 'commit': HEAD, 'tree': TREE,
    'artifact': {'id': 14973051830, 'artifact_name': artifact['name'], 'run_id': 45501927279,
        'api_url': artifact['url'], 'archive_bytes': 54476107, 'archive_sha256': ZIP_SHA,
        'qemu_member': aggregate['qemu']['member'], 'qemu_bytes': 63645720, 'qemu_sha256': QEMU_SHA},
    'workflow_runs': workflow_runs, 'github_evidence': pin(github_path),
    'all_five_independent_audits_passed': True, 'audits': audits,
    'a77_actual_raw_log_audit': pin(a77_path, 'f94048bb892e1aa136127eb79727e43a499deb0dd2c61c0794c565b40dda5cf8'),
    'a77_attempt2_carryforward': pin(carry_path, '3458b1517c6833238b79be7f92efd0591624d1d2fd0533ae527e1553f1d3391b'),
    'a77_attempt2_executed_again': False,
    'independent_board_log_observations': [pin(p) for p in count_paths],
    'extra_linux_ufs_observation': {'receipt': pin(ufs_path, '0d8d30afb104a5af39be4122c7e07e0356adc301de91d729788ea61d7aec730d'),
        'checks': [39, 61], 'boundary': ufs['boundary']},
    'retained_initial_ci_failure': {'commit': '50836513e30bb1d122bd3fce49d70c509b3efb1f',
        'qualified': False, 'native_used': False, 'cause': 'Final test path join used PosixPath indexing; only that expression was repaired.',
        'receipt': pin(BASE/'llcc-candidate/.work/llcc-initial-5083651-failure-observation.json', 'b83d915a33151c826fb726d59ef09549cdeba696ed607e3fa5da107a616ee5d9')},
    'retained_pr_attempt1_failure': {'run_id': 45501932082, 'attempt': 1, 'qualified': False,
        'native_used': False, 'cause': 'HTTP 429 before downloading the eighth fixed source input; generator did not run.',
        'receipt': pin(EV/'llcc-c02-pr-attempt1-http429-failure.json', 'abbddaed6a404c45ff350191c5b16c584d42bf95808b32500a929a8d9c4026dd'),
        'rerun_changed_source_or_acceptance': False},
    'audit_evidence_commit': '0258a0b9b0ac374f294f3373f7ceb092809c42d9',
    'native_started': False, 'native_executed': False, 'coordinator_deployed': False,
    'host_boot_guard_bound': False, 'device_rpc_used_for_qualification': False,
    'android_userspace_established': False, 'ui_established': False, 'android_boot_established': False,
    'read_only_qualification_assembly': True, 'additional_optional_tests_run': False,
    'scope': 'Compiled CI artifact qualification for one later original-input native attempt. This receipt does not establish original Android userspace, UI, boot completion, physical LLCC geometry, or cache performance.'}
out = BASE/'llcc-candidate/.work/llcc-c02f3d7-qualification.json'
with out.open('x') as f:
    json.dump(result, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps({'qualification': pin(out), 'native_executed': False, 'passed': True}))
