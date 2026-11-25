#!/usr/bin/env python3
"""Prepare, but never execute, the documentation-advance qualifier successor."""
from pathlib import Path
import ast
import copy
import datetime
import difflib
import hashlib
import json

R = Path('/dev/shm/ddrstats-recovery-6a5')
OLD = R/'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_001.py'
NEW = R/'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_002.py'
OLD_BINDING = R/'qualification-recovery-binding-001.json'
NEW_BINDING = R/'qualification-recovery-binding-002.json'
FRAGMENT = R/'qualification-default-ref-proof-002.py.txt'

def pin(path, expected=None):
    assert path.is_file() and not path.is_symlink()
    raw = path.read_bytes()
    got = {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if expected is not None:
        assert (got['bytes'], got['sha256']) == expected, (got, expected)
    return got

old_pin = pin(OLD, (50742, '775651269ec9efcac8d7a5602bc66a1a3ae6ea4a00d95dd2aac373b91c442034'))
old_binding_pin = pin(OLD_BINDING, (12413, '1d7854e9c0fc0a9981fa2d80256def314dd31053ff969e91bcdf52aec057ba14'))
fragment_pin = pin(FRAGMENT, (9716, '7cc1aaa305ba65c145d2fffc25f39596aa7a6debed13a95eae51695d36ba5a4a'))
review_pin = pin(R/'independent-default-ref-advance-proof-review-001.json',
                 (2579, '00710f0bd84f8a34d5fa0a7cd4bd3bc40de356664a78e8e5a5491b229dd621ca'))

new_assets = {
    'default_ref_advance_proof': ('default-ref-documentation-advance-proof-001.json', 3040, '7807ca018aa4dbaa251f02cb36c7fa02bf5292b5f2d29a15d1c2a033e58c57fc'),
    'default_ref_advance_raw': ('default-ref-documentation-advance-raw-001.json', 157269, 'dd80202e1dd5ef0e1cda5fc51695cb365b61f7f768b42953e6733b736e4f64c9'),
    'failed_qualification_execution': ('qualification-recovery-execution-001.json', 1103, 'a83e04ba10ab5f32eb709fc57930103eda636313d588026cf0a72a4658106d47'),
    'failed_qualification_stdout': ('qualification-recovery-execution-001.stdout.log', 0, 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'),
    'failed_qualification_stderr': ('qualification-recovery-execution-001.stderr.log', 351, '916e692c50a3dfdf9242da1581209349dd7864e09f4eb24931c03f2eda9b86f0'),
    'failed_qualification_independent_observation': ('qualification-main-ref-failure-independent-observation-001.json', 2839, '103730ee9e9deb644823b502f1601e301c3721d7ed7e7b9f3409bd703cc1a449'),
}
new_asset_pins = {name: pin(R/file, (size, digest)) for name, (file, size, digest) in new_assets.items()}
old = OLD.read_text()
source = old
operations = []

def replace_once(label, previous, current):
    global source
    assert source.count(previous) == 1, (label, source.count(previous))
    source = source.replace(previous, current, 1)
    operations.append((label, previous, current))

replace_once('three distinct documentation-reference constants',
    "PARENT_TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'\n",
    "PARENT_TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'\n"
    "OBSERVED_DEFAULT_HEAD = 'b5819654a07b2048bb1088cb0c7dfe3625d474ef'\n"
    "OBSERVED_DEFAULT_TREE = '8571f7d3b099dcb02e29a0e84ed4503ba9ebdb2e'\n"
    "DOCUMENTATION_COMMIT = 'cfec387558972fdd82bb166a913d18e4e11da100'\n")
replace_once('append six named proof and failure assets',
    "    'source_materialization_failure', 'source_derivation_failure',\n]",
    "    'source_materialization_failure', 'source_derivation_failure',\n"
    + ''.join('    ' + repr(name) + ',\n' for name in new_assets) + ']')
replace_once('pin all six added assets by complete length and SHA256',
    "FIXED_ASSET_IDENTITIES = {\n",
    "FIXED_ASSET_IDENTITIES = {\n"
    + ''.join('    ' + repr(name) + ': ' + repr((size, digest)) + ',\n'
              for name, (_, size, digest) in new_assets.items()))
replace_once('insert bounded complete proof and retained failure validators',
    'parser = argparse.ArgumentParser(description=__doc__)\n',
    FRAGMENT.read_text() + 'parser = argparse.ArgumentParser(description=__doc__)\n')
replace_once('separate immutable PR base and mutable observed ref; require open draft unmerged PR',
    "assert refs['main_ref']['object']['sha'] == refs['pr']['base']['sha'] == BASE_HEAD\n",
    "assert refs['pr']['base']['sha'] == BASE_HEAD\n"
    "assert refs['main_ref']['object']['sha'] == OBSERVED_DEFAULT_HEAD\n"
    "assert refs['pr']['state'] == 'open'\n"
    "assert refs['pr']['draft'] is True and refs['pr']['merged'] is False\n")
replace_once('invoke complete documentation proof and original failure checks',
    "assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE\n",
    "assert refs['pr']['base']['ref'] == 'tb371fc' and refs['pr']['merge_commit_sha'] == MERGE\n"
    'default_ref_advance_observation = validate_documentation_advance(refs, gh)\n'
    'retained_failed_qualification = retain_failed_qualification()\n')
replace_once('expose honest default-ref and failed-qualification result fields',
    "    'assembler': pin(__file__), 'qualification_binding': binding_pin,\n",
    "    'assembler': pin(__file__), 'qualification_binding': binding_pin,\n"
    "    'mutable_default_ref_observation': default_ref_advance_observation,\n"
    "    'retained_failed_qualification': retained_failed_qualification,\n")

# The complete predecessor must be recovered byte for byte by reversing only
# the seven explicitly permitted changes. This also preserves all existing
# eight audit/legacy/source/metadata validators, not merely their names.
reverse = source
for label, previous, current in reversed(operations):
    assert reverse.count(current) == 1, label
    reverse = reverse.replace(current, previous, 1)
assert reverse == old
assert ast.dump(ast.parse(reverse), include_attributes=False) == ast.dump(ast.parse(old), include_attributes=False)
compile(source, str(NEW), 'exec')  # Syntax only; no generated code is executed.
old_functions = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(old).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
new_functions = {n.name: ast.dump(n, include_attributes=False) for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
assert all(new_functions[name] == body for name, body in old_functions.items())
assert set(new_functions) - set(old_functions) == {'validate_documentation_advance', 'retain_failed_qualification'}
for start, end in [
    ('audits, reports = [], {}\n', "gh_path = asset_path('github_metadata')\n"),
    ('workflow_runs = []\n', 'result = {\n'),
]:
    assert old[old.index(start):old.index(end)] == source[source.index(start):source.index(end)]

binding_before = json.loads(OLD_BINDING.read_text())
assert len(binding_before['assets']) == 31 and len(binding_before['audits']) == 8
binding_after = copy.deepcopy(binding_before)
assert not (set(new_assets) & set(binding_before['assets']))
binding_after['assets'].update(new_asset_pins)
binding_after['output'] = str(R/'ddrstats-7d01e5e-qualification-recovery-002.json')
assert not Path(binding_before['output']).exists() and not Path(binding_after['output']).exists()
assert len(binding_after['assets']) == 37
assert binding_after['audits'] == binding_before['audits']
assert all(binding_after['assets'][name] == value for name, value in binding_before['assets'].items())
restored_binding = copy.deepcopy(binding_after)
for name in new_assets:
    del restored_binding['assets'][name]
restored_binding['output'] = binding_before['output']
assert restored_binding == binding_before
binding_text = json.dumps(binding_after, indent=2, sort_keys=True) + '\n'
source_diff = ''.join(difflib.unified_diff(old.splitlines(True), source.splitlines(True), fromfile=OLD.name, tofile=NEW.name))
binding_diff = ''.join(difflib.unified_diff(OLD_BINDING.read_text().splitlines(True), binding_text.splitlines(True), fromfile=OLD_BINDING.name, tofile=NEW_BINDING.name))
outputs = {
    NEW: source,
    NEW_BINDING: binding_text,
    R/'qualification-recovery-source-002.diff': source_diff,
    R/'qualification-recovery-binding-002.diff': binding_diff,
}
receipt_path = R/'qualification-recovery-successor-preparation-002.json'
assert all(not p.exists() for p in [*outputs, receipt_path])
for path, text in outputs.items():
    with path.open('x') as stream:
        stream.write(text)

receipt = {
    'schema': 'tb371fc-ddrstats-qualification-documentation-advance-source-successor-v1',
    'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'prepared': True,
    'predecessor_unbound_source': old_pin,
    'predecessor_actual_binding': old_binding_pin,
    'validator_fragment': fragment_pin,
    'independent_documentation_advance_proof_review': review_pin,
    'successor_unbound_source': pin(NEW),
    'successor_actual_binding': pin(NEW_BINDING),
    'source_diff': pin(R/'qualification-recovery-source-002.diff'),
    'binding_diff': pin(R/'qualification-recovery-binding-002.diff'),
    'derivation_script': pin(Path(__file__)),
    'explicit_source_change_count': len(operations),
    'explicit_source_changes': [
        {'label': label, 'previous_bytes': len(previous.encode()),
         'previous_sha256': hashlib.sha256(previous.encode()).hexdigest(),
         'replacement_bytes': len(current.encode()),
         'replacement_sha256': hashlib.sha256(current.encode()).hexdigest()}
        for label, previous, current in operations
    ],
    'reversing_exact_allowlisted_changes_recovers_entire_predecessor_bytes': True,
    'reversed_source_AST_identical': True,
    'all_existing_function_ASTs_unchanged': sorted(old_functions),
    'exact_new_validator_functions': sorted(set(new_functions) - set(old_functions)),
    'eight_audit_and_six_legacy_semantic_blocks_byte_identical': True,
    'workflow_observer_source_history_validators_byte_identical': True,
    'original_named_assets_retained_exact': 31,
    'added_named_assets': new_asset_pins,
    'new_named_asset_total': 37,
    'all_eight_auditor_and_receipt_bindings_unchanged': True,
    'artifact_binding_unchanged': True,
    'immutable_source_PR_base_head_merge_tree_run_job_checks_unchanged': True,
    'new_observed_default_head': 'b5819654a07b2048bb1088cb0c7dfe3625d474ef',
    'current_default_head_qualified': False,
    'new_PR_state_checks': {'state': 'open', 'draft': True, 'merged': False},
    'failed_qualification_001_retained_as_failure': True,
    'failed_qualification_001_output_absent': True,
    'new_exclusive_output': binding_after['output'],
    'new_output_exists': False,
    'binding_digest_in_successor_source': 'UNBOUND_RECOVERY_BINDING_SHA256',
    'successor_source_executed': False,
    'qualification_executed_or_created': False,
    'artifact_opened_or_audited': False,
    'device_RPC_SSH_QEMU_CI_native_or_publication_used': False,
    'review_required_before_any_binding_digest_substitution_or_qualification_execution': True,
}
with receipt_path.open('x') as stream:
    json.dump(receipt, stream, indent=2, sort_keys=True)
    stream.write('\n')
print(json.dumps({'source': pin(NEW), 'binding': pin(NEW_BINDING), 'receipt': pin(receipt_path),
                  'source_diff': pin(R/'qualification-recovery-source-002.diff'),
                  'binding_diff': pin(R/'qualification-recovery-binding-002.diff'),
                  'qualification_executed': False}, indent=2))
