#!/usr/bin/env python3
"""Generate pinned 78-gate orchestration; no generated script is executed."""
from pathlib import Path
import argparse
import ast
import hashlib
import json
import re

ROOT = Path('/run/tb371fc-root78-a47')
OLDROOT = Path('/run/tb371fc-root77-a47')
OLD_SOURCE = '09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e'
OLD_RUN = 45264653096
OLD_LABEL = 'native-09c638'
RUNNER_SHA = 'dd0f64e234a4eb89e6131115a2d505bafc5c8227bf013892481e65bc6b0bd250'
OLD_OBSERVER_SHA = '20f589dd4e0b21748d7ddccbacd44980f9ccef28cf13134394c9648ee9370da9'
OBSERVER_SHA = '12566d0919dda9eac8239a9aca9890894eabcbc7c5856b9e7b248fa83f7b55ab'
GLOBAL = 'qcom-camera-gdsc.enabled-reference=on'
GATE = 'camera-gdsc-reference'
BASELINE_PINS = {
    'worker-template77.py': '79e699ca3442794ecd5ef762b4646d14088e2650bfa35927e7833cc8fe730b0f',
    'native-inner77.py': 'f515ea9674747b9db1e572d956c2704cdcffe2ab104379cb2a1dc4836f341412',
    'native-09c638-watcher.py': '7f36b4a7f6343ffd19f1a7e2f305d7bfaee9e70e5a749ec9294aa97cab0801d9',
    'launcher77.py': '78145ee818081eced762a159f9b0825c03451f38bc58d52119131562185520c2',
    'plan77-preflight.json': '72e61e8089d97191be3bcfb8ccfcaef211555a2ead1092440e252911ad66e00f',
}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def pinned(path, expected):
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == expected, 'input identity differs: ' + str(path))
    return raw.decode('utf-8')

def parsed(source, filename):
    tree = ast.parse(source, filename=str(filename))
    compile(tree, str(filename), 'exec')
    return tree

def replace_once(text, old, new, changes):
    require(text.count(old) == 1, 'replacement is not unique: ' + old[:100])
    changes.append((old, new))
    return text.replace(old, new)

def inverse(text, original, changes, label):
    for old, new in reversed(changes):
        require(text.count(new) == 1, 'inverse is not unique: ' + label)
        text = text.replace(new, old)
    require(text == original, 'pinned baseline not preserved: ' + label)
    return digest(text)

def function_source(text, name):
    nodes = [node for node in parsed(text, name).body
             if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(nodes) == 1, 'one verifier function required')
    node = nodes[0]
    require(not node.decorator_list and [x.arg for x in node.args.args] == ['z', 'info', 'data']
            and not node.args.posonlyargs and not node.args.kwonlyargs
            and node.args.vararg is None and node.args.kwarg is None,
            'verifier interface changed')
    require(isinstance(node.body[-1], ast.Return), 'unfinished verifier')
    return ast.get_source_segment(text, node) + '\n'

def make_worker(old, function):
    changes = []
    worker = old
    def alter(before, after):
        nonlocal worker
        worker = replace_once(worker, before, after, changes)
    alter('rpmh-wait-mask-reference|gpucc-sm8250|',
          'rpmh-wait-mask-reference|camera-gdsc-reference|gpucc-sm8250|')
    needle = ("   if '-noc-sbm-reference/' in info.filename:\n"
              "    assert data['qemu_sha256_before']==data['qemu_sha256_after'] and data['binary_unchanged'] is True\n"
              "    reported_hash=data['qemu_sha256_before']\n")
    alter(needle, needle + (
        "   if '-camera-gdsc-reference/' in info.filename:\n"
        "    assert data['qemu_sha256_before']==data['qemu_sha256_after'] and data['binary_unchanged'] is True\n"
        "    reported_hash=data['qemu_sha256_before']\n"))
    needle = ("   if '-rpmh-wait-mask-reference/' in info.filename:\n"
              "    compact.update(verify_rpmh_wait_streams(z,info,data))\n")
    alter(needle, needle + (
        "   if '-camera-gdsc-reference/' in info.filename:\n"
        "    compact.update(verify_camera_gdsc_streams(z,info,data))\n"))
    alter(' assert len(selected)==16,sorted(selected)\n',
          ' assert len(selected)==17,sorted(selected)\n')
    old_counts = (" assert stream_counts=={'counter_streams_from_zip':3,'noc_streams_from_zip':4,"
                  "'smmuv500_streams_from_zip':5,'tsens24xx_streams_from_zip':5,"
                  "'cpufreq_hw_streams_from_zip':5,'devfreq_fw_streams_from_zip':5,"
                  "'noc_sbm_streams_from_zip':5,'rpmh_wait_streams_from_zip':5},stream_counts\n")
    alter(old_counts, old_counts.replace("'rpmh_wait_streams_from_zip':5}",
            "'rpmh_wait_streams_from_zip':5,'camera_gdsc_streams_from_zip':5}"))
    alter(' assert sum(stream_counts.values())==37\n',
          ' assert sum(stream_counts.values())==42\n')
    alter('cfg=__CONFIG__', function + '\ncfg=__CONFIG__')
    proof = inverse(worker, old, changes, 'worker')
    parsed(worker, 'worker-template78.py')
    require(worker.count('__CONFIG__') == worker.count('__NATIVE__') == 1,
            'worker placeholder count changed')
    require(worker.index(' assert sum(stream_counts.values())==42\n') <
            worker.index(' fixture_stage=stage_actual_dt_fixture(') <
            worker.index(' subprocess.run(base+[install,'),
            'stage must follow all audits and precede binary installation')
    return worker, proof, changes

def make_inner(old, observer):
    tree = parsed(old, 'native-inner77.py')
    nodes = [node for node in tree.body if isinstance(node, ast.Expr)
             and isinstance(node.value, ast.Call) and len(node.value.args) == 1
             and isinstance(node.value.args[0], ast.Constant)
             and isinstance(node.value.args[0].value, str)
             and node.value.args[0].value.startswith('#!/usr/bin/env python3\n')]
    require(len(nodes) == 1 and nodes[0].lineno == nodes[0].end_lineno,
            'unique original embedded wrapper required')
    node = nodes[0]
    require(digest(ast.literal_eval(node.value.args[0])) == OLD_OBSERVER_SHA,
            'embedded prior wrapper differs')
    changes = []
    old_line = old.splitlines(True)[node.lineno - 1]
    new_line = "(scripts/'diagnose_private_aop.py').write_text(" + repr(observer) + ')\n'
    inner = replace_once(old, old_line, new_line, changes)
    inner = replace_once(inner, OLD_OBSERVER_SHA, OBSERVER_SHA, changes)
    needle = "globals+=['qcom-sm8250-rpmh.reference-wait-mask-reserved=on']\n"
    inner = replace_once(inner, needle, needle + 'globals+=[' + repr(GLOBAL) + ']\n', changes)
    proof = inverse(inner, old, changes, 'inner')
    parsed(inner, 'native-inner78.py')
    return inner, proof, changes

def make_watcher(old, cfg, worker, inner):
    lines = old.splitlines(True)
    require(lines[2].startswith('cfg='), 'watcher config position changed')
    original = ast.literal_eval(lines[2][4:].strip())
    require(original['source'] == OLD_SOURCE and original['run'] == OLD_RUN
            and original['components'] == 73 and original['label'] == OLD_LABEL,
            'old watcher provenance differs')
    changes = []
    watcher = replace_once(old, lines[2], 'cfg=' + repr(cfg) + '\n', changes)
    for suffix in ('-ci-worker-template.py', '-inner.py'):
        watcher = replace_once(watcher, repr(OLD_LABEL + suffix),
                               repr(cfg['label'] + suffix), changes)
    watcher = replace_once(watcher, BASELINE_PINS['worker-template77.py'], digest(worker), changes)
    watcher = replace_once(watcher, BASELINE_PINS['native-inner77.py'], digest(inner), changes)
    proof = inverse(watcher, old, changes, 'watcher')
    parsed(watcher, 'watcher78.py')
    return watcher, proof, changes

def make_launcher(old, cfg, watcher):
    changes = []
    launcher = old
    for suffix in ('-watcher.py', '.log', '-watcher.pid'):
        launcher = replace_once(launcher, repr(OLD_LABEL + suffix),
                                repr(cfg['label'] + suffix), changes)
    launcher = replace_once(launcher, BASELINE_PINS['native-09c638-watcher.py'], digest(watcher), changes)
    launcher = replace_once(launcher, repr(OLD_SOURCE), repr(cfg['source']), changes)
    launcher = replace_once(launcher, "'workflow_run':" + str(OLD_RUN),
                            "'workflow_run':" + str(cfg['run']), changes)
    proof = inverse(launcher, old, changes, 'launcher')
    parsed(launcher, 'launcher78.py')
    return launcher, proof, changes

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--run', required=True, type=int)
    parser.add_argument('--plan-sha256', required=True)
    parser.add_argument('--test', type=Path, default=Path('/run/tb371fc-camera-gdsc-tests-a47/tb371fc/tests/test_camera_gdsc.py'))
    parser.add_argument('--test-sha256', required=True)
    parser.add_argument('--camera-verifier', type=Path, default=ROOT/'camera-gdsc-artifact-verifier.py')
    parser.add_argument('--camera-verifier-sha256', required=True)
    parser.add_argument('--observer', type=Path, default=ROOT/'native-camera-gdsc-observer.py')
    parser.add_argument('--output-root', type=Path, default=ROOT)
    args = parser.parse_args()
    require(re.fullmatch('[0-9a-f]{40}', args.source) and args.source != OLD_SOURCE,
            'new exact source required')
    require(args.run > 0 and args.run != OLD_RUN, 'new actual workflow run required')
    for sha in (args.plan_sha256, args.test_sha256, args.camera_verifier_sha256):
        require(re.fullmatch('[0-9a-f]{64}', sha), 'exact independently supplied SHA256 required')
    label = 'native-' + args.source[:6]
    require(label != OLD_LABEL, 'source prefix collision')
    base = {name: pinned(OLDROOT/name, sha) for name, sha in BASELINE_PINS.items()}
    plan = json.loads(pinned(ROOT/'plan78-preflight.json', args.plan_sha256))
    old_plan = json.loads(base['plan77-preflight.json'])
    runner = pinned(ROOT/'tb371fc/scripts/ci_device_tests.py', RUNNER_SHA)
    parsed(runner, 'ci_device_tests.py')
    require(plan['runner_sha256'] == RUNNER_SHA and plan['test_sha256'] == args.test_sha256,
            'plan and final test identities differ')
    require({k:v['count'] for k,v in plan['suites'].items()} ==
            {'components':74,'linux':4,'all':78}, 'plan cardinalities differ')
    for key in ('components','linux','all'):
        names = plan['suites'][key]['names']
        require(len(names) == len(set(names)) == plan['suites'][key]['count'], 'duplicate plan name')
    names = plan['suites']['components']['names']
    require([x for x in names if x != GATE] == old_plan['suites']['components']['names'],
            'previous component order changed')
    require(names.index(GATE) == names.index('gpu-cx-gdsc') + 1, 'camera gate position changed')
    require(plan['suites']['linux'] == old_plan['suites']['linux'], 'old Linux gates changed')
    require(plan['suites']['all']['names'] == names + plan['suites']['linux']['names'], 'all plan differs')
    require(plan['prior_77_contracts_unchanged'] is True, 'prior contract preservation missing')
    gate = plan['camera_gdsc_gate']
    require(gate['verdict'] == 'passed' and gate['expected_count'] == 29 and
            gate['expected_lengths'] == {'migrations':2,'rejected_migrations':2,
                'corrupted_stream_rejections':2,'migration_sources':3}, 'camera gate contract differs')
    parsed(pinned(args.test, args.test_sha256), args.test)
    verifier = pinned(args.camera_verifier, args.camera_verifier_sha256)
    function = function_source(verifier, 'verify_camera_gdsc_streams')
    require(args.test_sha256 in function and 'camera_gdsc_streams_from_zip' in function,
            'verifier not bound to final test and stream inventory')
    observer = pinned(args.observer, OBSERVER_SHA)
    parsed(observer, args.observer)
    cfg = {'source':args.source,'run':args.run,'components':74,'label':label,
           'binary':'qemu-'+args.source[:6],'runner_sha256':RUNNER_SHA,
           'gate_order':{k:plan['suites'][k]['names'] for k in ('components','linux')}}
    worker, wr, wc = make_worker(base['worker-template77.py'], function)
    inner, ir, ic = make_inner(base['native-inner77.py'], observer)
    watcher, ar, ac = make_watcher(base['native-09c638-watcher.py'],cfg,worker,inner)
    launcher, lr, lc = make_launcher(base['launcher77.py'],cfg,watcher)
    out = args.output_root.absolute()
    out.mkdir(parents=True,exist_ok=True)
    require(out.is_dir() and not out.is_symlink(), 'real output directory required')
    files = {'worker-template78.py':worker,'native-inner78.py':inner,
             label+'-watcher.py':watcher,'launcher78.py':launcher}
    host = Path('/tank/shared/projects/tb371fc-wholeboard-a47')
    metadata = {
        'schema':'tb371fc-orchestration78-generation-v1','source':args.source,'run':args.run,'cfg':cfg,
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'baseline_input_sha256':BASELINE_PINS,'plan_sha256':args.plan_sha256,'runner_sha256':RUNNER_SHA,
        'worker_template_sha256':digest(worker),'native_inner_sha256':digest(inner),
        'watcher_sha256':digest(watcher),'launcher_sha256':digest(launcher),
        'camera_gdsc_verifier_sha256':digest(verifier),'embedded_camera_gdsc_verifier_sha256':digest(function),
        'camera_gdsc_test_sha256':args.test_sha256,'observer_sha256':OBSERVER_SHA,
        'selected_receipts_expected':17,'actual_stream_files_expected':42,
        'component_gates_expected':74,'linux_gates_expected':4,
        'additional_native_global':GLOBAL,'native_globals_expected_count':19,
        'native_global_count_observed':False,
        'prior_worker_guards_wait_verifier_and_actual_dt_stage_byte_preserved':True,
        'prior_inner_preserved_except_wrapper_hash_and_one_explicit_global':True,
        'stage_after_all_audits_before_binary_install':True,
        'inverse_sha256':{'worker':wr,'inner':ir,'watcher':ar,'launcher':lr},
        'checked_changes':{key:[{'old_bytes':len(a.encode()),'new_bytes':len(b.encode()),
                                'old_sha256':digest(a),'new_sha256':digest(b)} for a,b in value]
                           for key,value in [('worker',wc),('inner',ic),('watcher',ac),('launcher',lc)]},
        'deployment_files':[
            {'local':str(out/name),'host':str(host/(label+suffix)) if suffix else None,'sha256':digest(body)}
            for name,suffix,body in [('worker-template78.py','-ci-worker-template.py',worker),
                                    ('native-inner78.py','-inner.py',inner),
                                    (label+'-watcher.py','-watcher.py',watcher),
                                    ('launcher78.py',None,launcher)]],
        'generated_scripts_executed':False,'verifiers_executed':False,'remote_actions_executed':False,
        'native_executed':False,'public_tests_executed':False,'build_executed':False,
    }
    files['orchestration78-metadata.json'] = json.dumps(metadata,indent=2)+'\n'
    for name in files:
        require(not (out/name).exists() and not (out/name).is_symlink(), 'fresh output required: '+name)
    for name,body in files.items():
        with (out/name).open('x',encoding='utf-8',newline='') as stream:
            stream.write(body)
    print(json.dumps({k:v for k,v in metadata.items() if k not in ('cfg','checked_changes')},indent=2))

if __name__ == '__main__':
    main()
