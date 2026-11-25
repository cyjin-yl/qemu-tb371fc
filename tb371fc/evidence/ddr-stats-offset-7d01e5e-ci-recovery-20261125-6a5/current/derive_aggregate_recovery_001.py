import ast,hashlib,json,pprint,runpy,types,difflib
from pathlib import Path
ROOT=Path('/dev/shm/ddrstats-candidate-6a5')
OLD=Path('/dev/shm/rpmstats-ci-publication-001/payload/auditors/audit_rpm_stats_offset_integration_artifact_66d9e20.py')
OUT=Path('/dev/shm/ddrstats-recovery-6a5/audit_ddr_stats_offset_integration_artifact_7d01e5e_recovery_001.py')
old=OLD.read_text();assert hashlib.sha256(old.encode()).hexdigest()=='d52cf3026c23d82fae252557877ff80bbd8eafa126ffee0aa7029de4830214df'
def node(name):
    return next(n for n in ast.parse(old).body if (isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name==name) or (isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in n.targets)))
def segment(name):return ast.get_source_segment(old,node(name))
oldplan=json.loads(ast.literal_eval(node('PLAN').value.args[0]))
harness=ROOT/'tb371fc/scripts/ci_device_tests.py'
assert hashlib.sha256(harness.read_bytes()).hexdigest()=='c1478cab27f3937d2935571a0727233ae23bb392380762db10c76cda978d4a90'
ns=runpy.run_path(str(harness),run_name='recovery_source_plan_only')
plans={}
for suite in ['components','linux']:
    args=types.SimpleNamespace(qemu=Path('/not-executed/qemu'),build_dir=Path('/not-executed/build'),kernel=Path('/not-executed/Image'),suite=suite)
    cases=ns['plan'](args,Path('/not-created/source-plan-only'))
    plans[suite]=[dict(name=c.name,kind=c.result if c.result in ('stdout','tap','exit') else 'file',verdict=c.verdict,expected_count=c.expected_count,count_field=c.count_field,expected_values=c.expected_values,expected_lengths=c.expected_lengths,tap_count=c.tap_count,guest_errno=c.guest_errno) for c in cases]
assert [c for c in plans['components'] if not c['name'].startswith('ddr-stats-offset-')]==oldplan['components']
assert plans['linux']==oldplan['linux'] and len(plans['components'])==99 and len(plans['linux'])==5
manifest_path=ROOT/'tb371fc/docs/ddr-stats-offset-sm8250-registers.json'
assert hashlib.sha256(manifest_path.read_bytes()).hexdigest()=='682074c1299455fa482b870a4b2f841ea1c1c74aee9ed6dac131cc9dcff33b7d'
manifest=json.loads(manifest_path.read_text())
summary={k:manifest[k] for k in ['source_input_count','register_count','window_count','mapped_bytes','physical_base','offset_value']}
assert summary==dict(source_input_count=4,register_count=1,window_count=1,mapped_bytes=4,physical_base=0xc3f001c,offset_value=0xf00b8)
pins={x['path']:x for x in manifest['source_pins']}
outputs={}
for name in ['hw/misc/qcom-sm8250-ddr-stats-offset-data.inc','tb371fc/docs/ddr-stats-offset-sm8250-registers.json']:
    b=(ROOT/name).read_bytes();outputs[name]={'bytes':len(b),'sha256':hashlib.sha256(b).hexdigest()}
constants='\n\nDDR_STATS_OFFSET_SOURCE_PINS = '+pprint.pformat(pins,sort_dicts=False)+'\nDDR_STATS_OFFSET_GENERATED_PINS = '+pprint.pformat(outputs,sort_dicts=False)+'\nDDR_STATS_OFFSET_GENERATED_SUMMARY = '+pprint.pformat(summary,sort_dicts=False)+'\nDDR_STATS_OFFSET_GENERATED_MANIFEST = json.loads('+repr(json.dumps(manifest,separators=(',',':')))+')\n\n'
newfun=segment('audit_rpm_stats_offset_generation').replace('rpm_stats_offset','ddr_stats_offset').replace('RPM_STATS_OFFSET','DDR_STATS_OFFSET').replace('rpm-stats-offset','ddr-stats-offset').replace('RPM offset','DDR offset').replace('adc218676eef25575469234709c2d87185ca223a','75f2c0b3690702c90863c2e138cb5520670845ea').replace('0x0c3f0004','0x0c3f001c').replace('0x000f0048','0x000f00b8').replace('["stats_offset"]','["ddr_stats_offset"]')
newfun=newfun.replace('"captured_runtime_word_claimed": False, "aop_execution_claimed": False}', '"captured_runtime_word_claimed": False, "aop_execution_claimed": False,\n            "reference_window_mapped_only_when_enabled": True, "default_off_mapped_bytes": 0}')
s=old.replace(segment('PLAN'),'PLAN = json.loads('+repr(json.dumps(plans,separators=(',',':')))+')',1)
s=s.replace("REVIEWED_HEAD = '66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b'","REVIEWED_HEAD = '7d01e5e294b788f97a8120c2d34122ddb25e6343'",1)
s=s.replace("RUNNER_SHA256 = 'a69c1916407867d25b5484b45d25b427bb4921adc04b15b1f7c7589a758c1faa'","RUNNER_SHA256 = 'c1478cab27f3937d2935571a0727233ae23bb392380762db10c76cda978d4a90'",1)
s=s.replace("['35'], \"Actual CI harness test count\"","['36'], \"Actual CI harness test count\"",1)
s=s.replace('"spss-", "rpm-stats-offset-"))','"spss-", "rpm-stats-offset-", "ddr-stats-offset-"))',1)
s=s.replace('\ndef main():',constants+newfun+'\n\n\ndef main():',1)
s=s.replace('tb371fc-rpm-stats-offset-integration-independent-zip-audit-v1','tb371fc-ddr-stats-offset-integration-independent-zip-audit-v1',1)
s=s.replace('All 102 gate contracts','All 104 gate contracts',1).replace('SPSS and RPM offset source regeneration','SPSS, RPM offset and DDR offset source regeneration',1)
s=s.replace('"rpm_stats_offset_wire_audit_performed_by_this_script": False,','"rpm_stats_offset_wire_audit_performed_by_this_script": False,\n              "ddr_stats_offset_wire_audit_performed_by_this_script": False,',1)
s=s.replace('"RPM offset 24"]','"RPM offset 24", "DDR offset 24"]',1)
s=s.replace('"components", 97, args.expected_head','"components", 99, args.expected_head',1)
s=s.replace('result["ci_harness_unittests"] = 35','result["ci_harness_unittests"] = 36',1)
anchor='        print("Auditing actual migration bytes, source QOM and first target observations", file=sys.stderr, flush=True)'
s=s.replace(anchor,'        result["ddr_stats_offset_generation"] = audit_ddr_stats_offset_generation(\n            artifact, *selected["ddr-stats-offset-source-generation"])\n'+anchor,1)
s=s.replace('Read-only aggregate and VIDEOCC audit of the RPM statistics offset integration CI ZIP.','Read-only aggregate and VIDEOCC audit of the DDR statistics offset integration CI ZIP.\n\nRecovery derivation from the immutable published RPM auditor; this is new source,\nnot a claim to have reconstructed the lost original DDR auditor bytes.',1)
s=s.replace('reviewed RPM statistics offset integration source','reviewed DDR statistics offset integration source',1)
ot={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(old).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
nt={n.name:ast.dump(n,include_attributes=False) for n in ast.parse(s).body if isinstance(n,(ast.FunctionDef,ast.ClassDef))}
changed=[name for name in ot if ot[name]!=nt[name]]
assert changed==['audit_suite','main'],changed
assert len(ot)==40 and len(nt)==41
assert not OUT.exists()
OUT.write_text(s)
diff=''.join(difflib.unified_diff(old.splitlines(True),s.splitlines(True),fromfile=OLD.name,tofile=OUT.name))
(OUT.parent/'aggregate-recovery-001.diff').write_text(diff)
r={'schema':'tb371fc-ddr-aggregate-source-recovery-derivation-v1','base':{'path':str(OLD),'bytes':len(old.encode()),'sha256':hashlib.sha256(old.encode()).hexdigest()},'new':{'path':str(OUT),'bytes':len(s.encode()),'sha256':hashlib.sha256(s.encode()).hexdigest()},'original_lost_auditor_bytes_recovered':False,'old_functions_and_classes':len(ot),'new_functions_and_classes':len(nt),'unchanged_semantic_functions':len(ot)-len(changed),'changed_functions':changed,'new_function':'audit_ddr_stats_offset_generation','legacy_102_contracts_preserved':True,'new_gate_count':104,'current_harness_plan_only_called':True,'qemu_executed':False,'artifact_opened':False,'scope':'Source derivation before recovery audit; root already separately performed ZIP structural recovery and complete binary hashing.'}
(OUT.parent/'aggregate-recovery-derivation-001.json').write_text(json.dumps(r,indent=2)+'\n')
print(json.dumps(r))
