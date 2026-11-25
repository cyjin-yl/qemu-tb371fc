#!/usr/bin/env python3
"""Independent review of qualification source; do not execute it or open artifacts."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path('/dev/shm/ddrstats-recovery-6a5')
NEW = ROOT / 'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_001.py'
OLD = Path('/dev/shm/rpmstats-ci-publication-001/payload/qualification/assemble_rpm_stats_offset_66d9e20_qualification.py')
OUT = ROOT / 'independent-qualification-static-review-recovery-001.json'


def pin(path, expected=None):
    raw = path.read_bytes()
    value = {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if expected is not None:
        assert (value['bytes'], value['sha256']) == expected
    return raw, value


def syntax(text):
    return ast.dump(ast.parse(text), include_attributes=False)


assert not OUT.exists()
new_raw, new_pin = pin(NEW, (50742, '775651269ec9efcac8d7a5602bc66a1a3ae6ea4a00d95dd2aac373b91c442034'))
old_raw, old_pin = pin(OLD, (32939, 'd44250698293d93c3ddcd481f7db108e9b756d243cd6aa18e647265cb8a2488f'))
new, old = new_raw.decode(), old_raw.decode()
compile(new, str(NEW), 'exec')
old_legacy = old[old.index("rpm = reports['rpm_stats_offset']"):old.index('gh_path = ')]
new_legacy = new[new.index("rpm = reports['rpm_stats_offset']"):new.index('gh_path = ')]
addition = "assert rpm['expected_tree'] == TREE\n"
assert new_legacy.count(addition) == 1
assert syntax(new_legacy.replace(addition, '')) == syntax(old_legacy)
old_meta = old[old.index('gh_path = '):old.index('current_publication_path = ')]
new_meta = new[new.index('gh_path = '):new.index('# The recovery download')]
replacements = [
    ("REVIEW/'rpmstats-66d9e20-final-ci-github-metadata.json'", "asset_path('github_metadata')"),
    ("EV/'rpmstats-66d9e20-pr-base-identity.json'", "asset_path('pr_base_identity')"),
    ("EV/'rpmstats-push-artifacts-final.json'", "asset_path('artifact_api')"),
    ("REVIEW/'rpmstats-66d9e20-a77-rawlog-audit-001.json'", "asset_path('a77_receipt')"),
    ("REVIEW/'rpmstats-66d9e20-board-rawlog-observation-001.json'", "asset_path('board_receipt')"),
    ("EV/'ufs-66d9e20-final-observation-001.json'", "asset_path('ufs_receipt')"),
    ("r['component_gates'] == 97", "r['component_gates'] == 99"),
    ("[15, 35, 28, 43, 16, 15]", "[15, 36, 28, 43, 16, 15]"),
    ("('ci_device_tests', 35, 'OK')", "('ci_device_tests', 36, 'OK')"),
]
for before, after in replacements:
    assert old_meta.count(before) == 1
    old_meta = old_meta.replace(before, after)
assert syntax(new_meta) == syntax(old_meta)
t = ast.parse(new)
assigns = {n.targets[0].id: n.value for n in t.body if isinstance(n, ast.Assign) and
           len(n.targets) == 1 and isinstance(n.targets[0], ast.Name)}
expected_summary = ast.literal_eval(assigns['expected_ddr_summary'])
auditor_raw, auditor_pin = pin(ROOT / 'audit_ddr_stats_offset_artifact_7d01e5e_recovery_001.py',
                               (95924, '4a347b0f0962b21e8653b7ef39db66fadce5b6f8385bded8fb7ea12ddefc521b'))
at = ast.parse(auditor_raw)
main = next(n for n in at.body if isinstance(n, ast.FunctionDef) and n.name == 'main')
summary = next(n.value for n in ast.walk(main) if isinstance(n, ast.Assign) and len(n.targets) == 1
               and ast.unparse(n.targets[0]) == "receipt['summary']")


class Constants(ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id in ('PAYLOAD', 'GETTERS'):
            return ast.Constant({'PAYLOAD': 24, 'GETTERS': 17}[node.id])
        return node


assert expected_summary == ast.literal_eval(Constants().visit(summary))
assert set(ast.literal_eval(assigns['AUDIT_NAMES'])) == {
    'integration', 'ddr_stats_offset', 'rpm_stats_offset', 'spss', 'llcc', 'dp', 'dsi', 'dispcc'}
assert new.count("BINDING_SHA256 = 'UNBOUND_RECOVERY_BINDING_SHA256'") == 1
assert new.index('Recovery qualification binding is UNBOUND') < new.index('def read(') < new.index('binding_pin = pin(')
assert ast.literal_eval(assigns['HEAD']) == '7d01e5e294b788f97a8120c2d34122ddb25e6343'
assert ast.literal_eval(assigns['TREE']) == '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4'
assert ast.literal_eval(assigns['ARTIFACT_ID']) == 14991304675
assert ast.literal_eval(assigns['ZIP_SHA']) == '1e6c37d205a9e130e4f197c068c93c756488b6b579ab06eadd429190b9b0bd6f'
assert ast.literal_eval(assigns['QEMU_SHA']) == '935549d9f359d04254f5627bdd8807fd8d1800ab2223ab52dea80c0cf7edecb6'
assert not any(isinstance(n, ast.Import) and any(x.name in ('subprocess', 'zipfile') for x in n.names) for n in t.body)
assert "with out.open('x')" in new
fixed = ast.literal_eval(assigns['FIXED_ASSET_IDENTITIES'])
for key, path in [
    ('source_recovery', Path('/dev/shm/ddrstats-source-recovery-001/fresh-source-recovery-001.json')),
    ('dependency_recovery', Path('/dev/shm/ddrstats-source-recovery-001/fresh-dependency-recovery-001.json')),
    ('new_ddr_independent_review', ROOT / 'independent-ddr-auditor-static-review-recovery-001.json'),
]:
    pin(path, fixed[key])
diff_raw, diff_pin = pin(ROOT / 'qualification-recovery-001.diff',
                         (64960, '7517fed78cf920d7e5ddbb3c7faf4b62e6cc55d8c5727315844f293b4e33a253'))
derivation_raw, derivation_pin = pin(ROOT / 'qualification-recovery-derivation-001.json')
derivation = json.loads(derivation_raw)
assert derivation['new_source']['sha256'] == new_pin['sha256']
assert derivation['metadata_section_exact_substitutions'] == [list(x) for x in replacements]
assert derivation['all_six_legacy_semantic_ASTs_preserved'] is True

receipt = {
    'schema': 'tb371fc-ddrstats-recovery-qualification-independent-static-review-v1',
    'observed_utc': datetime.now(timezone.utc).isoformat(), 'reviewer': '/root/rpmstats_frontier',
    'passed': True, 'static_source_review_passed': True,
    'qualifier': new_pin, 'predecessor': old_pin, 'derivation': derivation_pin, 'complete_diff': diff_pin,
    'review_program': pin(Path(__file__))[1], 'new_ddr_auditor': auditor_pin,
    'independently_verified_AST_preservation': {
        'legacy_sections': ['rpm_stats_offset', 'spss', 'llcc', 'dp', 'dsi', 'dispcc'],
        'all_six_identical_after_removing_one_added_current_RPM_tree_assertion': True,
        'metadata_section_identical_after_explicit_path_and_count_substitutions': True,
        'metadata_substitutions': replacements,
        'full_DDR_summary_equals_frozen_independent_auditor_summary': True},
    'manual_review_findings': [
        {'scope': 'fail-closed binding', 'passed': True,
         'finding': 'UNBOUND guard precedes evidence reads. Only a separately reviewed binding SHA256 may change in the final source. Manifest requires exact source/artifact identity, all eight audit names and the complete asset allowlist; every regular file is fully rehashed with exact length. Existing qualification output is refused and written exclusively only after all checks.'},
        {'scope': 'legacy and aggregate checks', 'passed': True,
         'finding': 'All six retained device validator sections are AST-identical to the immutable public RPM predecessor, plus a stricter retained RPM tree assertion. Aggregate requires 99 components, 5 Linux gates, 36 actual host units and seven generation contracts with input counts 10/13/17/8/2/4/4, two outputs each. Earlier device wire and migration scopes are not weakened.'},
        {'scope': 'DDR complete wire and first observations', 'passed': True,
         'finding': 'Full exact independent summary and DSO1 enabled/disabled 24-byte payloads are checked. Both completed directions require 17+17 pure first getters, 187 total, RAM-only prewarm, all memory/MMIO/cont deltas zero, restored witness and exact post-restore/post-reset active mapping. Two profile failures and six full-stream one-bit variants require exact names, bytes, positions, xor values, exits and no memory/cont commands.'},
        {'scope': 'disabled mapping', 'passed': True,
         'finding': 'Default-off group requires 22 worlds/374 getters, ten requested operations, twelve generic chunks, zero device callbacks, no override and both empty FlatViews. Disabled incoming target requires three generic chunks, zero callbacks and no active leaf. Enabled incoming target keeps one readonly callback and four active bytes. No failed-read value becomes a data oracle.'},
        {'scope': 'current source proof', 'passed': True,
         'finding': 'Fixed fresh recovery receipt identity, complete 41 publication files and five current runners, all file SHA256/Git blobs, 41 delta rows, 33 new/8 modified counts, 1091 unchanged entries and 73 raw API record files are required and rehashed. The 15 DDR source pins plus inherited transport are tied to this current proof; historical runner behavior reviews alone cannot establish current bytes.'},
        {'scope': 'exact job, checkout and artifact metadata', 'passed': True,
         'finding': 'Exact source head/tree/sole parent and PR merge tree/parents, push and PR runs attempt1, four exact job IDs with terminal successful steps, and named artifact run/head/length/digest are required. Raw A77/board log hashes are rechecked. Push checkout uses source head and PR uses the actual merge commit. A77 852/17/two-invalid, board 99+5, host suite counts and UFS39/61 remain required; push ZIP unit proof is distinct from PR raw-log unit proof.'},
        {'scope': 'honest recovery and failure retention', 'passed': True,
         'finding': 'New auditor derivation/source-only validation/independent reviews are bound to current auditor bytes. Historical 75cc compile failure, prior RPM import/socket failures, current DDR 36-run/35-pass/socket-error local result, and prior RPM native kernel failure are retained separately. Lost original audit/qualifier bytes are not claimed recovered; old PASS receipts cannot be selected as current audit receipts.'},
        {'scope': 'qualification versus native outcome', 'passed': True,
         'finding': 'Output claims only compiled CI qualification of the immutable artifact. Native started/executed, deployment, userspace, UI and Android boot remain false. No payload data, firmware readiness, AOP execution, real reset/readback or successful future native outcome is inferred.'},
    ],
    'material_defects_found': [],
    'allowed_final_source_mutation': 'Replace only UNBOUND_RECOVERY_BINDING_SHA256 with the SHA256 of the root-reviewed actual complete binding manifest; preserve this unbound source.',
    'requires_final_binding_review': True, 'actual_qualification_not_executed_by_reviewer': True,
    'qualifier_executed_or_imported': False, 'actual_artifact_opened_by_reviewer': False,
    'auditor_QEMU_native_or_device_execution': False, 'GitHub_or_source_branch_mutation': False,
    'original_lost_review_recreated': False, 'runtime_qualification_pass_claimed_by_this_receipt': False,
}
with OUT.open('x') as f:
    json.dump(receipt, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps(pin(OUT)[1]))
