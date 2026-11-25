#!/usr/bin/env python3
"""Bind preserved auditors to current source identities without opening any ZIP."""
import ast
from datetime import datetime, timezone
import difflib
import hashlib
import json
from pathlib import Path

HERE = Path('/dev/shm/ddrstats-legacy-recovery-001')
BASE = Path('/dev/shm/rpmstats-ci-publication-001/payload')
SOURCE = Path('/dev/shm/ddrstats-candidate-6a5')
OLD_HEAD = '66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b'
HEAD = '7d01e5e294b788f97a8120c2d34122ddb25e6343'
TREE = '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def defs(raw):
    return {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(raw).body
            if isinstance(n, (ast.FunctionDef, ast.ClassDef))}


def save(name, oldpath, expected, change):
    raw = oldpath.read_bytes()
    assert (len(raw), sha(raw)) == expected
    text = raw.decode()
    new, notes = change(text)
    data = new.encode()
    compile(new, name, 'exec')
    out = HERE / 'auditors' / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('xb') as f:
        f.write(data)
    diff = ''.join(difflib.unified_diff(text.splitlines(True), new.splitlines(True),
                                      fromfile=str(oldpath), tofile=str(out)))
    dpath = HERE / 'diffs' / (name + '.diff')
    dpath.parent.mkdir(parents=True, exist_ok=True)
    with dpath.open('x') as f:
        f.write(diff)
    before, after = defs(raw), defs(data)
    changed = sorted(k for k in before if before[k] != after[k])
    assert before.keys() == after.keys()
    if name.startswith('audit_rpm_stats_offset_'):
        assert changed == ['main']
        canonical = new.replace('/dev/shm/ddrstats-candidate-6a5', '/dev/shm/rpmstats-candidate-6a5')
        assert defs(canonical) == before
        notes['all_function_asts_identical_after_only_source_root_default_normalization'] = True
    else:
        assert before == after
    return {'old': pin(oldpath), 'new': pin(out), 'diff': pin(dpath), 'changes': notes,
            'function_class_count': len(before), 'literal_ast_changed_functions': changed,
            'added_or_removed_functions': [], 'compiled_without_execution': True}


def head_only(text):
    assert text.count(OLD_HEAD) == 1
    return text.replace(OLD_HEAD, HEAD), {'head_only_replacement_count': 1,
                                        'all_function_asts_unchanged': True}


def identical(text):
    return text, {'identical_bytes_copy': True, 'all_function_asts_unchanged': True}


dep_path = HERE / 'fresh-legacy-source-dependency-recovery-001.json'
dep = json.loads(dep_path.read_bytes())
assert dep['passed'] and dep['source_commit'] == HEAD and dep['source_tree'] == TREE
plan = json.loads((HERE / 'source-dependency-plan-001.json').read_bytes())
shared_changes = [r for r in plan['files'] if r['scope'] == 'retained-rpm' and r.get('previous_sha256') != r['sha256']]
assert len(shared_changes) == 6


def rpm_bind(text):
    assert text.count(OLD_HEAD) == 1
    assert text.count('7c13a3b85075e0eaefcc14d4f55c160bed699a08') == 1
    assert text.count('/dev/shm/rpmstats-candidate-6a5') == 1
    out = text.replace(OLD_HEAD, HEAD).replace('7c13a3b85075e0eaefcc14d4f55c160bed699a08', TREE)
    for row in shared_changes:
        before = '(' + str(row['previous_bytes']) + ', "' + row['previous_sha256'] + '")'
        after = '(' + str(row['bytes']) + ', "' + row['sha256'] + '")'
        assert out.count(before) == 1, (row['path'], before)
        out = out.replace(before, after)
    out = out.replace('/dev/shm/rpmstats-candidate-6a5', '/dev/shm/ddrstats-candidate-6a5')
    return out, {'head_rebound': True, 'tree_rebound': True,
                 'shared_source_pin_changes': shared_changes,
                 'source_root_default': str(SOURCE),
                 'model_test_generator_and_contract_pins_unchanged': True,
                 'semantic_audit_logic_unchanged': True}


records = []
for name, oldname, expected in [
    ('audit_spss_irq_mask_artifact_ddr_stats_offset_7d01e5e.py', 'audit_spss_irq_mask_artifact_rpm_stats_offset_66d9e20.py', (63293, '8d709f46f2220dbd526bc5b052137693983b3f4ce8fc843b08ea338b669cfa5d')),
    ('audit_llcc_artifact_ddr_stats_offset_7d01e5e.py', 'audit_llcc_artifact_rpm_stats_offset_66d9e20.py', (77024, 'b5e358b0d3a9f319403c8541ef26a1d5a6a455052f74535967d89fd58f99171d')),
    ('audit_dp_pll_artifact_ddr_stats_offset_7d01e5e.py', 'audit_dp_pll_artifact_rpm_stats_offset_66d9e20.py', (87087, '14ca84d703148349b9f401f5ce1475cd194d5c726a343e06d193a7c3726c30f5')),
]:
    records.append(save(name, BASE / 'auditors' / oldname, expected, head_only))
records.append(save('audit_rpm_stats_offset_artifact_ddr_stats_offset_7d01e5e_recovery_001.py',
                    BASE / 'auditors/audit_rpm_stats_offset_artifact.py',
                    (87901, '7eb65a626ce253f3f8469948ddb0669d6bb5256f186f0ef9ffbb3c76b55b10f0'), rpm_bind))
for name, expected in [
    ('audit_dsi_pll_artifact.py', (91462, '0e0b7e6ebe3a9486383c2946669629a97d4d037414be9d6e5ea28970462954fe')),
    ('audit_dispcc_wide_artifact_rootpath.py', (52957, '62837675b808db7ed5671e9cff7653f7b76d4c295d668919ffff10a4134fc4c8')),
]:
    records.append(save(name, BASE / 'auditors' / name, expected, identical))
ufs = BASE / 'observers/observe_extra_ufs_artifact.py'
ufs_identity = pin(ufs)
records.append(save('observe_extra_ufs_artifact.py', ufs,
                    (ufs_identity['bytes'], ufs_identity['sha256']), identical))
receipt = {'schema': 'tb371fc-ddrstats-recovery-legacy-auditor-binding-v1',
           'observed_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
           'source_head': HEAD, 'source_tree': TREE, 'immutable_predecessor_commit': 'd103750ca2a191559daeb9eff4341b8c80013dc6',
           'source_dependency_receipt': pin(dep_path), 'bindings': records,
           'new_receipt_identity': True, 'lost_actual_audit_receipts_recreated': False,
           'artifact_opened': False, 'auditor_executed_or_imported': False,
           'producer_QEMU_native_device_or_SSH_executed': False,
           'independent_review_required_before_actual_artifact_audit': True}
out = HERE / 'legacy-auditor-binding-recovery-001.json'
with out.open('x') as f:
    json.dump(receipt, f, indent=2)
    f.write('\n')
print(json.dumps({'receipt': pin(out), 'auditors': [r['new'] for r in records]}, indent=2))
