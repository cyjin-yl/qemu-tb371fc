#!/usr/bin/env python3
"""Source-only reconstruction as a new, manifest-bound recovery qualifier."""
import ast
import difflib
import hashlib
import json
from pathlib import Path

ROOT = Path('/dev/shm/ddrstats-recovery-6a5')
BASE = Path('/dev/shm/rpmstats-ci-publication-001/payload/qualification/assemble_rpm_stats_offset_66d9e20_qualification.py')
OUT = ROOT/'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_001.py'
base_bytes = BASE.read_bytes()
assert len(base_bytes) == 32939
assert hashlib.sha256(base_bytes).hexdigest() == 'd44250698293d93c3ddcd481f7db108e9b756d243cd6aa18e647265cb8a2488f'
base = base_bytes.decode()
legacy = base[base.index("rpm = reports['rpm_stats_offset']"):base.index('gh_path = ')]
legacy_new = legacy.replace("rpm = reports['rpm_stats_offset']\n", "rpm = reports['rpm_stats_offset']\nassert rpm['expected_tree'] == TREE\n", 1)
assert legacy_new.replace("assert rpm['expected_tree'] == TREE\n", '') == legacy
assert ast.dump(ast.parse(legacy_new.replace("assert rpm['expected_tree'] == TREE\n", '')), include_attributes=False) == ast.dump(ast.parse(legacy), include_attributes=False)
metadata = base[base.index('gh_path = '):base.index('current_publication_path = ')]
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
for old, new in replacements:
    assert metadata.count(old) == 1, old
    metadata = metadata.replace(old, new)
parts = [ROOT/'qualification-header-001.py.txt', ROOT/'qualification-ddr-001.py.txt', ROOT/'qualification-source-history-result-001.py.txt']
header, ddr, tail = [p.read_text() for p in parts]
source = header + '\n' + ddr + '\n' + legacy_new + '\n' + metadata + '\n' + tail
assert source.count("'UNBOUND_RECOVERY_BINDING_SHA256'") == 1
assert 'push_artifact_ci_unit' not in source
compile(source, str(OUT), 'exec')
with OUT.open('x') as f:
    f.write(source)
diff = ''.join(difflib.unified_diff(base.splitlines(True), source.splitlines(True),
                                  fromfile='public-d103750c/assemble_rpm_stats_offset_66d9e20_qualification.py',
                                  tofile=OUT.name))
with (ROOT/'qualification-recovery-001.diff').open('x') as f:
    f.write(diff)
def pin(p):
    b=p.read_bytes();return {'path':str(p),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
receipt = {'schema':'tb371fc-ddr-qualification-new-source-recovery-derivation-v1',
    'predecessor_commit':'d103750ca2a191559daeb9eff4341b8c80013dc6','predecessor':pin(BASE),
    'new_source':pin(OUT),'fragments':[pin(p) for p in parts],'diff':pin(ROOT/'qualification-recovery-001.diff'),
    'original_lost_source_sha256':'130334bf01b28ab41616ddf9f051f28b6db0c574426f557e8377b7cbb5e1ba3b',
    'original_source_bytes_recovered':False,'legacy_model_sections':['rpm_stats_offset','spss','llcc','dp','dsi','dispcc'],
    'all_six_legacy_semantic_ASTs_preserved':True,'only_additional_legacy_check':'retained RPM expected_tree equals current TREE',
    'metadata_section_exact_substitutions':replacements,'current_source_files_required':41,'current_runners_required':5,
    'raw_source_API_records_required':73,'current_plan':[99,5],'current_host_unit_count':36,
    'current_audits_required':8,'binding_interface':'Separately reviewed complete file identity manifest; its SHA256 is the only final source substitution.',
    'binding_status':'UNBOUND','unbound_guard_precedes_any_evidence_read':True,
    'syntax_compiled':True,'qualifier_executed':False,'actual_current_audit_receipts_read':False,
    'artifact_opened':False,'QEMU_or_native_or_CI_rerun':False,'runtime_or_artifact_qualification_claimed':False}
with (ROOT/'qualification-recovery-derivation-001.json').open('x') as f:
    json.dump(receipt,f,sort_keys=True,indent=2);f.write('\n')
module = ast.parse(header)
assets = ast.literal_eval(next(n.value for n in module.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ASSET_NAMES' for t in n.targets)))
audits = ast.literal_eval(next(n.value for n in module.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='AUDIT_NAMES' for t in n.targets)))
def unbound(name):return {'path':'UNBOUND_PATH_'+name,'bytes':'UNBOUND_BYTES_'+name,'sha256':'UNBOUND_SHA256_'+name}
template={'schema':'tb371fc-ddrstats-recovery-qualification-binding-v1',
    'source_head':'7d01e5e294b788f97a8120c2d34122ddb25e6343','source_tree':'5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4',
    'artifact':{'id':14991304675,'run_id':45544186469,'zip_bytes':57340476,
        'zip_sha256':'1e6c37d205a9e130e4f197c068c93c756488b6b579ab06eadd429190b9b0bd6f','qemu_bytes':63727664,
        'qemu_sha256':'935549d9f359d04254f5627bdd8807fd8d1800ab2223ab52dea80c0cf7edecb6','qemu_member':'qemu-build/qemu-system-aarch64'},
    'audits':[{'name':n,'auditor':unbound(n+'_auditor'),'receipt':unbound(n+'_receipt')} for n in audits],
    'assets':{n:unbound(n) for n in assets},
    'output':'/dev/shm/ddrstats-recovery-6a5/ddrstats-7d01e5e-qualification-recovery-001.json',
    'recovery':{'original_lost_receipt_bytes_recreated':False,'same_immutable_artifact':True,
        'new_read_only_audit_receipts_required':True,'CI_rerun':False,'QEMU_or_native_rerun':False}}
with (ROOT/'qualification-recovery-binding-template-001.json').open('x') as f:
    json.dump(template,f,sort_keys=True,indent=2);f.write('\n')
print(json.dumps({'source':pin(OUT),'derivation':pin(ROOT/'qualification-recovery-derivation-001.json'),
                  'diff':pin(ROOT/'qualification-recovery-001.diff'),'binding_template':pin(ROOT/'qualification-recovery-binding-template-001.json')}))
