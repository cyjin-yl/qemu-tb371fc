#!/usr/bin/env python3
"""Review only the documentation-ref correction; no qualifier or artifact execution."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

R = Path('/dev/shm/ddrstats-recovery-6a5')
OLD = R / 'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_001.py'
NEW = R / 'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_002.py'
OUT = R / 'independent-qualification-successor-static-review-002.json'


def pin(p, expected=None):
    raw = p.read_bytes()
    result = {'path': str(p), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if expected is not None:
        assert (result['bytes'], result['sha256']) == expected
    return raw, result


assert not OUT.exists()
old_raw, old_pin = pin(OLD, (50742, '775651269ec9efcac8d7a5602bc66a1a3ae6ea4a00d95dd2aac373b91c442034'))
new_raw, new_pin = pin(NEW, (61972, '4631b3f09e7e7c39d31a617d7fcd9cdf2925ef5cd98cd011b8e04acc966358b1'))
fragment_raw, fragment_pin = pin(R / 'qualification-default-ref-proof-002.py.txt',
                                 (9716, '7cc1aaa305ba65c145d2fffc25f39596aa7a6debed13a95eae51695d36ba5a4a'))
old, new = old_raw.decode(), new_raw.decode()
compile(new, str(NEW), 'exec')
proof_review_raw, proof_review_pin = pin(R / 'independent-default-ref-advance-proof-review-001.json',
                                         (2579, '00710f0bd84f8a34d5fa0a7cd4bd3bc40de356664a78e8e5a5491b229dd621ca'))
assert json.loads(proof_review_raw)['passed'] is True
old_bind_raw, old_bind_pin = pin(R / 'qualification-recovery-binding-001.json',
                                 (12413, '1d7854e9c0fc0a9981fa2d80256def314dd31053ff969e91bcdf52aec057ba14'))
new_bind_raw, new_bind_pin = pin(R / 'qualification-recovery-binding-002.json',
                                 (13899, '5bd4895ccf4333e95b135e9d17c896b7114df922de4b8d8d828ecc613267b3a7'))
a, b = json.loads(old_bind_raw), json.loads(new_bind_raw)
added_names = ['default_ref_advance_proof', 'default_ref_advance_raw', 'failed_qualification_execution',
               'failed_qualification_stdout', 'failed_qualification_stderr', 'failed_qualification_independent_observation']
assert set(b['assets']) - set(a['assets']) == set(added_names)
assert len(a['assets']) == 31 and len(b['assets']) == 37
assert all(b['assets'][k] == v for k, v in a['assets'].items())
assert b['audits'] == a['audits'] and len(b['audits']) == 8
canonical_binding = {**b, 'assets': {k: v for k, v in b['assets'].items() if k not in added_names}, 'output': a['output']}
assert canonical_binding == a
assert b['output'] == str(R / 'ddrstats-7d01e5e-qualification-recovery-002.json')
assert not Path(a['output']).exists() and not Path(b['output']).exists()
added_pins = {}
for name in added_names:
    spec = b['assets'][name]
    _, actual = pin(Path(spec['path']), (spec['bytes'], spec['sha256']))
    assert actual == spec
    added_pins[name] = actual

changes = [
    ("PARENT_TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'\n",
     "PARENT_TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'\n"
     "OBSERVED_DEFAULT_HEAD = 'b5819654a07b2048bb1088cb0c7dfe3625d474ef'\n"
     "OBSERVED_DEFAULT_TREE = '8571f7d3b099dcb02e29a0e84ed4503ba9ebdb2e'\n"
     "DOCUMENTATION_COMMIT = 'cfec387558972fdd82bb166a913d18e4e11da100'\n"),
    ("    'source_materialization_failure', 'source_derivation_failure',\n]",
     "    'source_materialization_failure', 'source_derivation_failure',\n" + ''.join('    ' + repr(n) + ',\n' for n in added_names) + ']'),
    ('FIXED_ASSET_IDENTITIES = {\n', 'FIXED_ASSET_IDENTITIES = {\n' + ''.join(
        '    ' + repr(n) + ': ' + repr((added_pins[n]['bytes'], added_pins[n]['sha256'])) + ',\n' for n in added_names)),
    ('parser = argparse.ArgumentParser(description=__doc__)\n', fragment_raw.decode() + 'parser = argparse.ArgumentParser(description=__doc__)\n'),
    ("assert refs['main_ref']['object']['sha'] == refs['pr']['base']['sha'] == BASE_HEAD\n",
     "assert refs['pr']['base']['sha'] == BASE_HEAD\nassert refs['main_ref']['object']['sha'] == OBSERVED_DEFAULT_HEAD\n"
     "assert refs['pr']['state'] == 'open'\nassert refs['pr']['draft'] is True and refs['pr']['merged'] is False\n"),
    ("assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE\n",
     "assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE\n"
     'default_ref_advance_observation = validate_documentation_advance(refs, gh)\nretained_failed_qualification = retain_failed_qualification()\n'),
    ("    'assembler': pin(__file__), 'qualification_binding': binding_pin,\n",
     "    'assembler': pin(__file__), 'qualification_binding': binding_pin,\n"
     "    'mutable_default_ref_observation': default_ref_advance_observation,\n    'retained_failed_qualification': retained_failed_qualification,\n"),
]
expected = old
for before, after in changes:
    assert expected.count(before) == 1
    expected = expected.replace(before, after, 1)
assert expected == new
reverse = new
for before, after in reversed(changes):
    assert reverse.count(after) == 1
    reverse = reverse.replace(after, before, 1)
assert reverse.encode() == old_raw
defs = lambda s: {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(s).body if isinstance(n, ast.FunctionDef)}
old_defs, new_defs = defs(old), defs(new)
assert all(new_defs[k] == v for k, v in old_defs.items())
assert set(new_defs) - set(old_defs) == {'validate_documentation_advance', 'retain_failed_qualification'}
assert new.count("BINDING_SHA256 = 'UNBOUND_RECOVERY_BINDING_SHA256'") == 1
for start, end in [('audits, reports = [], {}\n', "gh_path = asset_path('github_metadata')\n"),
                   ('workflow_runs = []\n', 'result = {\n')]:
    assert old[old.index(start):old.index(end)] == new[new.index(start):new.index(end)]
failure = json.loads(Path(added_pins['failed_qualification_execution']['path']).read_bytes())
assert failure['exit_code'] == 1 and failure['qualification'] is None
assert failure['argv'][1:4] == ['-I', '-S', '-B'] and len(failure['argv']) == 7 and failure['argv'][5] == '--binding'
assert failure['logs'] == [added_pins['failed_qualification_stdout'], added_pins['failed_qualification_stderr']]
assert "assert refs['main_ref']['object']['sha'] == refs['pr']['base']['sha'] == BASE_HEAD" in Path(added_pins['failed_qualification_stderr']['path']).read_text()
gh = json.loads(Path(b['assets']['github_metadata']['path']).read_bytes())
proof = json.loads(Path(added_pins['default_ref_advance_proof']['path']).read_bytes())
date = lambda s: datetime.fromisoformat(s.replace('Z', '+00:00'))
assert date(proof['evidence_commit_time']) > max(date(gh[k]['run']['updated_at']) for k in ('push', 'pr'))
assert date(proof['default_commit_time']) > date(proof['evidence_commit_time'])

result = {
    'schema': 'tb371fc-ddrstats-qualification-documentation-successor-independent-static-review-v1',
    'observed_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
    'reviewer': '/root/rpmstats_frontier', 'predecessor_source': old_pin, 'successor_source': new_pin,
    'previous_source_review': pin(R / 'independent-qualification-static-review-recovery-001.json')[1],
    'independent_default_ref_proof_review': proof_review_pin, 'proof_validator_fragment': fragment_pin,
    'previous_binding': old_bind_pin, 'successor_binding': new_bind_pin,
    'added_full_file_pins': added_pins, 'review_program': pin(Path(__file__))[1],
    'mechanical_review': {'explicit_source_substitutions': 7, 'reverse_recovers_every_predecessor_byte': True,
        'all_six_original_helper_function_ASTs_unchanged': True,
        'only_new_helpers': ['validate_documentation_advance', 'retain_failed_qualification'],
        'all_eight_audit_and_legacy_semantic_blocks_byte_identical': True,
        'workflow_observer_source_and_history_validation_blocks_byte_identical': True,
        'all_31_previous_assets_and_eight_audit_bindings_unchanged': True,
        'six_added_assets_fully_rehashed': True, 'new_exclusive_output_only': True},
    'semantic_review': [
        'Immutable source head/tree/parent, PR base/head/merge/tree, all run/job/artifact checks remain exact. The formerly conflated default ref is now separately pinned to b581 and must be a proved documentation-only descendant of420819d.',
        'New proof code rehashes seven full Git tree objects, traverses all changed subtrees and requires the exact three new regular evidence documents,235 unchanged entries and zero modifications/deletions. Default and evidence commit parents, equal tree and after-CI chronology are checked from actual metadata.',
        'PR38 must remain open,draft,unmerged with the same immutable source/base/merge. No CI status or mergeability of the advanced default branch is claimed.',
        'The failed001 source,binding,exit1,null qualification,empty stdout,complete assertion stderr and independent failure observation are retained by exact pins. Its missing output is not manufactured and failure is not reclassified as pass.',
        'All DDR wire/getter/mapping checks, six older model scopes, current41+5source proof and37asset manifest remain fail-closed. UNBOUND refuses before evidence reads. This source-only review does not execute the successor or establish qualification.'
    ],
    'material_defects_found': [],
    'allowed_next_source_change': 'Only substitute the SHA256 of the reviewed actual binding002 into a new source copy; preserve unbound002 and all failed001 files.',
    'qualifier_executed_or_imported': False, 'artifact_opened_or_audited': False,
    'QEMU_native_device_SSH_or_CI_used': False, 'GitHub_or_source_branch_mutated': False,
    'current_default_head_qualified': False, 'failed_001_reclassified_as_pass': False,
}
with OUT.open('x') as f:
    json.dump(result, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps(pin(OUT)[1]))
