#!/usr/bin/env python3
"""Bind the frozen coordinator to a separately qualified exact artifact, locally."""
from pathlib import Path
import argparse
import hashlib
import json
import runpy

HEAD = '54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7'
TREE = '6602490b518ab849dc9a0fd2636045c26954c145'
RUN = 45521455233
TAG = 'spss-54ec744-diagnostic-f093'
ROOT = Path('/dev/shm/native-spss-coordinator-review-6a5')
OUT = Path('/dev/shm/native-spss-54ec744-review-6a5')
FROZEN_CODE = ROOT/'spss-coordinator-runner-provenance-unbound.py'
FROZEN_SHA = '69429d845b65162617ee8253fc7762764952c853318b6cc37f36520362baf930'
FROZEN_LAUNCHER_SHA = 'cd7688f4460d2ce676195ee6dd09d6f97f6baae5494473da103018de35183f38'

def pin(path):
    path = Path(path)
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}

def write_json(path, value):
    with path.open('x') as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write('\n')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--qualification', type=Path, required=True)
    parser.add_argument('--expected-qualification-sha256', required=True)
    args = parser.parse_args()
    assert pin(args.qualification)['sha256'] == args.expected_qualification_sha256
    qualification = json.loads(args.qualification.read_bytes())
    assert qualification['passed'] is True
    assert qualification['commit'] == HEAD and qualification['tree'] == TREE
    assert qualification['all_six_independent_audits_passed'] is True
    assert {x['name'] for x in qualification['audits']} == {'integration', 'spss', 'llcc', 'dp', 'dsi', 'dispcc'}
    assert all(x['passed'] is True for x in qualification['audits'])
    assert qualification['native_started'] is False and qualification['native_executed'] is False
    assert qualification['coordinator_deployed'] is False
    assert qualification['runner_source_sha256'] == '61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1'
    artifact = qualification['artifact']
    assert artifact['run_id'] == RUN
    assert artifact['artifact_name'] == 'tb371fc-public-gates-'+HEAD
    assert pin(FROZEN_CODE)['sha256'] == FROZEN_SHA
    reviewed = {'id': artifact['id'], 'archive_bytes': artifact['archive_bytes'],
        'archive_sha256': artifact['archive_sha256'], 'qemu_sha256': artifact['qemu_sha256'],
        'audit_receipt_sha256': args.expected_qualification_sha256}
    namespace = runpy.run_path(str(FROZEN_CODE), run_name='local_binding_generation_only')
    binding = namespace['generate_binding'](HEAD, RUN, reviewed)
    assert binding['expected_components'] == 95 and binding['expected_linux'] == 5
    assert binding['spss_irq_mask_property'] == 'qcom-sm8250-spss-irq-mask.enabled-reference=on'
    assert len(namespace['ADDED_GLOBALS']) == 10
    assert namespace['ADDED_GLOBALS'][-1] == binding['spss_irq_mask_property']
    OUT.mkdir(mode=0o700, exist_ok=False)
    code_name = 'own-lane-spss-coordinator-f093b2c8-gitmeta.py'
    binding_name = 'spss-54ec744-binding.json'
    qualification_name = 'spss-54ec744-qualification.json'
    launcher_name = 'launch-spss-54ec744-f093.py'
    with (OUT/code_name).open('xb') as f:
        f.write(FROZEN_CODE.read_bytes())
    with (OUT/qualification_name).open('xb') as f:
        f.write(args.qualification.read_bytes())
    write_json(OUT/binding_name, binding)
    write_json(OUT/'spss-54ec744-reviewed-artifact.json', reviewed)
    binding_sha = pin(OUT/binding_name)['sha256']
    launcher_template = ROOT/'launch-spss-runner-provenance-unbound-f093.py'
    assert pin(launcher_template)['sha256'] == FROZEN_LAUNCHER_SHA
    template = launcher_template.read_text()
    substitutions = {
        "TAG = 'spss-unbound-diagnostic-f093'": "TAG = '"+TAG+"'",
        'spss-coordinator-runner-provenance-unbound.py': code_name,
        'binding-contract-frozen-unbound.json': binding_name,
        'spss-unbound-qualification.json': qualification_name,
        "'"+binding_name+"': None": "'"+binding_name+"': '"+binding_sha+"'",
        "'"+qualification_name+"': None": "'"+qualification_name+"': '"+args.expected_qualification_sha256+"'",
        "'--commit', 'UNBOUND', '--run-id', 'UNBOUND'": "'--commit', '"+HEAD+"', '--run-id', '"+str(RUN)+"'",
    }
    for before, after in substitutions.items():
        assert before in template, before
        template = template.replace(before, after)
    assert 'UNBOUND' not in template and ': None' not in template
    with (OUT/launcher_name).open('x') as f:
        f.write(template)
    paths = [OUT/x for x in (code_name, binding_name, qualification_name, launcher_name, 'spss-54ec744-reviewed-artifact.json')]
    for path in paths:
        path.chmod(0o600)
    receipt = {'schema': 'tb371fc-spss-54ec744-native-binding-preparation-v1',
        'commit': HEAD, 'tree': TREE, 'run_id': RUN, 'tag': TAG,
        'qualification_input': pin(args.qualification), 'builder': pin(__file__),
        'coordinator_identical_to_contract_frozen_review': pin(OUT/code_name)['sha256'] == FROZEN_SHA,
        'frozen_coordinator_sha256': FROZEN_SHA, 'files': [pin(p) for p in paths],
        'native_deployed': False, 'native_started': False,
        'device_rpc_used': False, 'binding_generation_local_only': True,
        'owner_boot_container_lock_once_guards_unchanged': True}
    write_json(OUT/'binding-preparation-receipt.json', receipt)
    print(json.dumps({'passed': True, 'receipt': pin(OUT/'binding-preparation-receipt.json'),
        'binding_sha256': binding_sha, 'native_started': False}))

if __name__ == '__main__':
    main()
