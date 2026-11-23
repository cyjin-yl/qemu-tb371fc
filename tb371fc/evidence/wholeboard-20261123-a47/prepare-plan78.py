from pathlib import Path
import ast
import dataclasses
import hashlib
import json
import pprint
import sys
import types

OLD = Path('/run/tb371fc-root77-a47')
ROOT = Path('/run/tb371fc-root78-a47')
REL = Path('tb371fc/scripts/ci_device_tests.py')
old = (OLD / REL).read_text()
assert hashlib.sha256(old.encode()).hexdigest() == '491e94f0a8c81efe62c5e62acb0f07a52ec4140ba31d166a420524cdeae555fc'
contract_path = Path('/run/tb371fc-camera-gdsc-tests-a47/camera-gdsc-test-contract.json')
contract_raw = contract_path.read_bytes()
assert hashlib.sha256(contract_raw).hexdigest() == '6c201c1ef2a072f0200b6736c23a1eb30dd11343a3bec5af13a91e6045250a64'
contract = json.loads(contract_raw)
expected = {'schema': contract['result_schema'], **contract['scalar_expectations']}
expected.update({key: True for key in contract['required_true_fields']})
lengths = {key: value for key, value in contract['array_lengths'].items() if key != 'checks'}
addition = ('        device("camera-gdsc-reference", "test_camera_gdsc.py",\n'
            '               output="--out", result="file", count=29,\n'
            '               expected=' + pprint.pformat(expected, sort_dicts=False, width=90).replace('\n', '\n                        ') + ',\n'
            '               expected_lengths=' + pprint.pformat(lengths, sort_dicts=False, width=90).replace('\n', '\n                                ') + ')\n')
anchor = '        device("aop-reference-peer", "test_aop_reference.py",\n'
assert old.count(anchor) == 1
new = old.replace(anchor, addition + anchor)
assert new.replace(addition, '') == old
(ROOT / REL).parent.mkdir(parents=True, exist_ok=True)
(ROOT / REL).write_text(new)
unit_rel = Path('tb371fc/scripts/test_ci_device_tests.py')
unit_old = (OLD / unit_rel).read_text()
assert unit_old.count('self.assertEqual(len(cases), 77)') == 1
unit = unit_old.replace('self.assertEqual(len(cases), 77)', 'self.assertEqual(len(cases), 78)')
(ROOT / unit_rel).write_text(unit)
for label, content in [('runner', new), ('unit', unit)]:
    compile(ast.parse(content), label, 'exec')
test_hash = None
if len(sys.argv) == 2:
    test = Path(sys.argv[1]).read_text()
    compile(ast.parse(test), sys.argv[1], 'exec')
    test_hash = hashlib.sha256(test.encode()).hexdigest()

def isolated_plan(content, name):
    nodes = [n for n in ast.parse(content).body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in ('Case', 'plan')]
    assert len(nodes) == 2
    module = types.ModuleType(name)
    module.__dict__.update(dataclass=dataclasses.dataclass, field=dataclasses.field,
                           Path=Path, sys=types.SimpleNamespace(executable='/usr/bin/python3'),
                           ROOT=Path('/source'))
    sys.modules[name] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), name, 'exec'), module.__dict__)
    return module.plan

def normalized(case, cases):
    def normalize(value):
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            for other in reversed(cases):
                value = value.replace(str(other.fixture), '/fixture/' + other.name)
            return value
        if isinstance(value, list):
            return [normalize(x) for x in value]
        if isinstance(value, dict):
            return {k: normalize(v) for k, v in value.items()}
        return value
    return normalize(dataclasses.asdict(case))

before_plan = isolated_plan(old, 'only_plan77')
after_plan = isolated_plan(new, 'only_plan78')
receipt = {'schema': 'tb371fc-unified-78-plan-preflight-v1', 'suites': {},
           'prior_77_contracts_unchanged': True, 'qemu_executed': False,
           'gates_executed': False, 'subprocess_or_socket_calls': [],
           'validation_kind': 'AST/compile plus isolated Case/plan evaluation only',
           'runner_sha256': hashlib.sha256(new.encode()).hexdigest(),
           'test_sha256': test_hash,
           'test_script_status': 'AST/compile inspected only' if test_hash else 'Final test not supplied yet; runner plan only',
           'fixture_build_is_an_additional_build_step_not_a_fifth_linux_gate': True,
           'gcc_source_generation_still_two_outputs_with_four_pinned_inputs': True,
           'camera_test_contract_sha256': hashlib.sha256(contract_raw).hexdigest()}
for suite, count in [('components', 74), ('linux', 4), ('all', 78)]:
    args = types.SimpleNamespace(suite=suite, qemu=Path('/test/qemu'), build_dir=Path('/test/build'), kernel=None)
    before, after = before_plan(args, Path('/test/runtime')), after_plan(args, Path('/test/runtime'))
    assert len(after) == count
    assert [x.name for x in after if x.name != 'camera-gdsc-reference'] == [x.name for x in before]
    old_by = {x.name: x for x in before}
    for current in after:
        if current.name in old_by:
            assert normalized(current, after) == normalized(old_by[current.name], before), current.name
    receipt['suites'][suite] = {'count': len(after), 'names': [x.name for x in after]}
    if suite == 'components':
        receipt['camera_gdsc_gate'] = normalized(next(x for x in after if x.name == 'camera-gdsc-reference'), after)
(ROOT / 'plan78-preflight.json').write_text(json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'counts': {k: v['count'] for k, v in receipt['suites'].items()},
                  'prior_77_contracts_unchanged': True, 'runner_sha256': receipt['runner_sha256'],
                  'test_sha256': test_hash, 'qemu_executed': False, 'gates_executed': False}))
