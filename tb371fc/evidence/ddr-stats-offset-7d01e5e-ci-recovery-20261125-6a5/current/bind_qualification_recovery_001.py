#!/usr/bin/env python3
"""Bind actual complete recovery evidence without executing the qualifier."""
import ast
import difflib
import hashlib
import json
from pathlib import Path

R=Path('/dev/shm/ddrstats-recovery-6a5')
P=Path('/dev/shm/rpmstats-ci-publication-001/payload')
L=Path('/dev/shm/ddrstats-legacy-recovery-001/auditors')
C=Path('/dev/shm/ddrstats-ci-review')
S=Path('/dev/shm/ddrstats-source-recovery-001')
unbound=R/'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_unbound_001.py'
raw=unbound.read_bytes()
assert len(raw)==50742 and hashlib.sha256(raw).hexdigest()=='775651269ec9efcac8d7a5602bc66a1a3ae6ea4a00d95dd2aac373b91c442034'
template=json.loads((R/'qualification-recovery-binding-template-001.json').read_text())
HEAD=template['source_head']; artifact=template['artifact']
def pin(p):
    p=Path(p);assert p.is_file() and not p.is_symlink(),str(p)
    b=p.read_bytes();return {'path':str(p.resolve()),'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
def read(p):return json.loads(Path(p).read_text())
assets={
 'github_metadata':R/'ddrstats-7d01e5e-recovery-ci-github-metadata-001.json',
 'pr_base_identity':R/'ddrstats-7d01e5e-recovery-pr-base-identity-001.json',
 'artifact_api':R/'ddrstats-7d01e5e-recovery-artifacts-api-001.json',
 'artifact_download':R/'artifact-download-recovery-001.json',
 'a77_observer':R/'audit_a77_rawlogs_7d01e5e.py',
 'a77_receipt':C/'ddrstats-7d01e5e-a77-rawlog-audit-001.json',
 'board_observer':R/'audit_board_rawlogs_7d01e5e.py',
 'board_receipt':C/'ddrstats-7d01e5e-board-rawlog-observation-001.json',
 'ufs_observer':L/'observe_extra_ufs_artifact.py',
 'ufs_receipt':R/'ufs-7d01e5e-recovery-audit-001.json',
 'source_recovery':S/'fresh-source-recovery-001.json',
 'dependency_recovery':S/'fresh-dependency-recovery-001.json',
 'historical_rpm_qualification':P/'qualification/rpmstats-66d9e20-qualification.json',
 'historical_rpm_native':P/'native/independent-review.json',
 'historical_failed_ci':P/'failures/rpmstats-75cc1dc-failed-ci-observation-001.json',
 'historical_include_correction':P/'failures/ci-include-path-correction.json',
 'historical_include_review':P/'failures/include-path-correction-review.json',
 'historical_rpm_local_initial':P/'failures/ci-local-unit-001.json',
 'historical_rpm_local_initial_stderr':R/'historical-source/ci-unit-all.stderr.log',
 'historical_rpm_local_actual':P/'failures/ci-local-unit-002.json',
 'historical_rpm_local_actual_stderr':R/'historical-source/ci-unit-all-002.stderr.log',
 'historical_runner_source_review':P/'historical/pr32-runner-source-review.json',
 'historical_runner_template_review':P/'historical/pr32-runner-template-review.json',
 'historical_prior_spss_qualification':P/'historical/spss-54ec744-qualification.json',
 'historical_earlier_spss_qualification':P/'historical/spss-a3a6183-reviewed-ci-before-runner-advance.json',
 'new_ddr_derivation':R/'ddr-auditor-recovery-derivation-001.json',
 'new_ddr_source_validation':R/'source-only-validation-001.json',
 'new_ddr_independent_review':R/'independent-ddr-auditor-static-review-recovery-001.json',
 'new_aggregate_review':R/'aggregate-recovery-independent-static-review-001.json',
 'source_materialization_failure':R/'source-materialization-failure-001.json',
 'source_derivation_failure':R/'derivation-failure-001.json',
}
assert set(assets)==set(template['assets'])
for name,p in assets.items():template['assets'][name]=pin(p)
nodes=ast.parse(raw).body
for key in ['FIXED_ASSET_IDENTITIES','HISTORICAL_HASHES']:
    expected=ast.literal_eval(next(n.value for n in nodes if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==key for t in n.targets)))
    for name,want in expected.items():
        got=template['assets'][name]
        assert ((got['bytes'],got['sha256'])==want if key=='FIXED_ASSET_IDENTITIES' else got['sha256']==want),(name,got,want)
audit_paths={
 'integration':('integration',R/'audit_ddr_stats_offset_integration_artifact_7d01e5e_recovery_001.py'),
 'ddr_stats_offset':('ddr',R/'audit_ddr_stats_offset_artifact_7d01e5e_recovery_001.py'),
 'rpm_stats_offset':('rpm',L/'audit_rpm_stats_offset_artifact_ddr_stats_offset_7d01e5e_recovery_001.py'),
 'spss':('spss',L/'audit_spss_irq_mask_artifact_ddr_stats_offset_7d01e5e.py'),
 'llcc':('llcc',L/'audit_llcc_artifact_ddr_stats_offset_7d01e5e.py'),
 'dp':('dp',L/'audit_dp_pll_artifact_ddr_stats_offset_7d01e5e.py'),
 'dsi':('dsi',L/'audit_dsi_pll_artifact.py'),
 'dispcc':('dispcc',L/'audit_dispcc_wide_artifact_rootpath.py'),
}
executions=[]
for row in template['audits']:
    short,source=audit_paths[row['name']]
    path=R/f'{short}-7d01e5e-recovery-audit-001.json'
    row['auditor'],row['receipt']=pin(source),pin(path)
    obj=read(path)
    assert obj['passed'] is True and obj['auditor_sha256']==row['auditor']['sha256']
    archive=obj.get('zip',obj.get('archive',obj.get('input')))
    assert archive['bytes']==artifact['zip_bytes'] and archive['sha256']==artifact['zip_sha256']
    head=obj.get('expected_head',obj.get('head',obj.get('component',{}).get('head')))
    assert head==HEAD
    binary=obj.get('qemu',{}).get('sha256',obj.get('component',{}).get('qemu_sha256_from_separately_audited_binary'))
    assert binary==artifact['qemu_sha256']
    ep=R/f'{short}-recovery-audit-execution-001.json';e=read(ep)
    assert e['schema']=='tb371fc-ddr-read-only-recovery-audit-execution-v1'
    assert e['exit_code']==0 and e['passed'] is True and e['error'] is None
    assert e['qemu_executed'] is False and e['native_executed'] is False and e['original_lost_receipt_restored'] is False
    assert (e['out'],e['bytes'],e['sha256'])==(str(path),row['receipt']['bytes'],row['receipt']['sha256'])
    assert e['auditor_sha256']==row['auditor']['sha256']
    assert str(source) in e['argv'] and e['argv'][e['argv'].index('--expected-head')+1]==HEAD
    assert e['argv'][e['argv'].index('--expected-zip-sha256')+1]==artifact['zip_sha256']
    logs=[]
    for stream in ['stdout','stderr']:
        lp=R/f'{short}-recovery-audit-001.{stream}.log';pp=pin(lp);assert pp['bytes']==e[stream+'_bytes'];logs.append(pp)
    executions.append({'name':row['name'],'execution':pin(ep),'logs':logs,'actual_exit':0,'receipt_and_source_pins_match':True})
for short in ['ufs']:
    epath=R/f'{short}-recovery-audit-execution-001.json';e=read(epath);got=template['assets']['ufs_receipt'];source=template['assets']['ufs_observer']
    assert e['passed'] is True and e['exit_code']==0 and e['error'] is None
    assert (e['out'],e['bytes'],e['sha256'])==(got['path'],got['bytes'],got['sha256'])
    assert e['auditor_sha256']==source['sha256'] and e['qemu_executed'] is False and e['native_executed'] is False
    executions.append({'name':short,'execution':pin(epath),'actual_exit':0,'receipt_and_source_pins_match':True})
for short in ['a77','board']:
    ep=R/f'{short}-recovery-observer-execution-001.json';e=read(ep);got=template['assets'][short+'_receipt'];source=template['assets'][short+'_observer']
    assert e['passed'] is True and e['exit_code']==0 and e['error'] is None
    assert (e['receipt'],e['receipt_bytes'],e['receipt_sha256'])==(got['path'],got['bytes'],got['sha256'])
    assert e['source_sha256']==source['sha256'] and e['qemu_executed'] is False and e['native_executed'] is False
    assert e['original_receipt_bytes_recovered'] is False
    executions.append({'name':short,'execution':pin(ep),'actual_exit':0,'receipt_and_source_pins_match':True})

binding=R/'qualification-recovery-binding-001.json'
with binding.open('x') as f:json.dump(template,f,sort_keys=True,indent=2);f.write('\n')
bp=pin(binding)
text=raw.decode();old="BINDING_SHA256 = 'UNBOUND_RECOVERY_BINDING_SHA256'";new="BINDING_SHA256 = '"+bp['sha256']+"'"
assert text.count(old)==1
bound_text=text.replace(old,new)
assert bound_text.replace(new,old)==text
def without_binding(source):
    t=ast.parse(source);n,=[n for n in t.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='BINDING_SHA256' for x in n.targets)];n.value=ast.Constant(value='NORMALIZED_BINDING');return ast.dump(t,include_attributes=False)
assert without_binding(bound_text)==without_binding(text)
bound=R/'assemble_ddr_stats_offset_7d01e5e_recovery_qualification_001.py'
compile(bound_text,str(bound),'exec')
with bound.open('x') as f:f.write(bound_text)
diff=''.join(difflib.unified_diff(text.splitlines(True),bound_text.splitlines(True),fromfile=unbound.name,tofile=bound.name))
dp=R/'qualification-recovery-final-binding-001.diff'
with dp.open('x') as f:f.write(diff)
receipt={'schema':'tb371fc-ddr-recovery-qualification-final-binding-preparation-v1',
 'unbound_source':pin(unbound),'binding':bp,'bound_source':pin(bound),'source_diff':pin(dp),
 'exact_one_binding_SHA256_literal_changed':True,'all_other_AST_nodes_identical':True,
 'byte_reversal_matches_frozen_unbound_source':True,'bound_source_syntax_compiled':True,
 'auditor_receipt_pairs':template['audits'],'asset_count':len(template['assets']),
 'all_named_assets_present_and_complete':True,'missing_named_assets':[],
 'actual_execution_receipts':executions,'actual_execution_exit_codes_checked':len(executions),
 'historical_RPM_small_stderr_files_freshly_recovered_from_d103750c':[
     template['assets']['historical_rpm_local_initial_stderr'],template['assets']['historical_rpm_local_actual_stderr']],
 'qualifier_executed':False,'qualification_file_created':False,'artifact_opened_by_binder':False,
 'QEMU_or_device_or_SSH_or_CI_execution':False,'old_lost_receipt_bytes_recreated':False,
 'requires_root_review_before_qualifier_execution':True}
rp=R/'qualification-recovery-final-binding-preparation-001.json'
with rp.open('x') as f:json.dump(receipt,f,sort_keys=True,indent=2);f.write('\n')
print(json.dumps({'binding':bp,'bound_source':pin(bound),'diff':pin(dp),'preparation':pin(rp),'missing':[]}))
