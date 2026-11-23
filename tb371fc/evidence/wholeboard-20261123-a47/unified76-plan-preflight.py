from pathlib import Path
import ast
import dataclasses
import hashlib
import json
import sys
import types

oldroot = Path('/run/tb371fc-root75-a47')
root = Path('/run/tb371fc-root76-a47')
relative = Path('tb371fc/scripts/ci_device_tests.py')
old = (oldroot / relative).read_text()
assert hashlib.sha256(old.encode()).hexdigest() == '8313465a893efd6adf9c6b6403cecdc2fe8d9345abfaab670390c2d04f2f362f'
addition = '''        device("noc-sbm-reference", "test_noc_sbm.py",
               output="--out", result="file", count=28,
               expected={"schema": "tb371fc-noc-sbm-v1",
                         "expected_checks": 28,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes_per_device": 152,
                         "device_count": 1, "port_count": 8,
                         "register_count": 3, "sparse_window_count": 3,
                         "mapped_bytes": 12,
                         "reference_latency_ns": 1000,
                         "supported_port_mask": 255,
                         "reference_boot_disconnected_mask": 0,
                         "reference_profile_is_default": False,
                         "default_profile_leaves_mmio_unmapped": True,
                         "diagnostic_counters_are_hardware": False,
                         "hardware_latency_measured": False,
                         "traffic_blocking_implemented": False,
                         "traffic_drain_observed": False,
                         "clock_control_implemented": False,
                         "irq_implemented": False,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
'''
anchor = '        device("smmuv500-reference", "test_smmuv500.py",\n'
assert old.count(anchor) == 1
new = old.replace(anchor, addition + anchor)
assert new.replace(addition, '') == old
(root / relative).parent.mkdir(parents=True, exist_ok=True)
(root / relative).write_text(new)
unitrel = Path('tb371fc/scripts/test_ci_device_tests.py')
unitold = (oldroot / unitrel).read_text()
assert unitold.count('self.assertEqual(len(cases), 75)') == 1
unitnew = unitold.replace('self.assertEqual(len(cases), 75)',
                          'self.assertEqual(len(cases), 76)')
(root / unitrel).write_text(unitnew)
for label, content in [('runner', new), ('unit', unitnew)]:
    compile(ast.parse(content), label, 'exec')
test_path = Path('/run/tb371fc-sbm-tests-a47/tb371fc/tests/test_noc_sbm.py')
test = test_path.read_text()
test_sha = hashlib.sha256(test.encode()).hexdigest()
assert test_sha == 'ebba6a0a4cc2139bb6657636f6160975f3bd070034d769c66e25c4ed5ba6563f'
compile(ast.parse(test), 'test_noc_sbm.py', 'exec')


def extract_plan(content, name):
    tree = ast.parse(content)
    nodes = [n for n in tree.body
             if isinstance(n, (ast.FunctionDef, ast.ClassDef))
             and n.name in ('Case', 'plan')]
    assert len(nodes) == 2
    module = types.ModuleType(name)
    module.__dict__.update(
        dataclass=dataclasses.dataclass, field=dataclasses.field, Path=Path,
        sys=types.SimpleNamespace(executable='/usr/bin/python3'),
        ROOT=Path('/source'))
    sys.modules[name] = module
    exec(compile(ast.Module(body=nodes, type_ignores=[]), name, 'exec'),
         module.__dict__)
    return module.plan


def normalize(case, cases):
    data = dataclasses.asdict(case)

    def norm(value):
        if isinstance(value, Path):
            value = str(value)
        if isinstance(value, str):
            for other in reversed(cases):
                value = value.replace(str(other.fixture),
                                      '/fixture/' + other.name)
            return value
        if isinstance(value, list):
            return [norm(x) for x in value]
        if isinstance(value, dict):
            return {k: norm(v) for k, v in value.items()}
        return value
    return norm(data)


plan_old = extract_plan(old, 'plan75only')
plan_new = extract_plan(new, 'plan76only')
receipt = {
    'schema': 'tb371fc-unified-76-plan-preflight-v1', 'suites': {},
    'prior_75_contracts_unchanged': True,
    'qemu_executed': False, 'gates_executed': False,
    'subprocess_or_socket_calls': [],
    'validation_kind': 'AST/compile plus isolated Case/plan evaluation only',
    'runner_sha256': hashlib.sha256(new.encode()).hexdigest(),
    'test_sha256': test_sha,
    'test_commit': '844f4292c28e87c9d711a86a19741ad08a25e27c',
    'model_commit': '8acb748b8626ef8d574e0dc72a95e8318a2def0d',
    'model_doc_clarification_commit': '0f9fd426d1ff9be2ef776c921b868cdd26cc25cd',
    'test_script_status': 'Final source AST/compile inspected; execution awaits formal CI',
}
for suite, count in [('components', 72), ('linux', 4), ('all', 76)]:
    args = types.SimpleNamespace(
        suite=suite, qemu=Path('/test/qemu'), build_dir=Path('/test/build'),
        kernel=None)
    before = plan_old(args, Path('/test/runtime'))
    after = plan_new(args, Path('/test/runtime'))
    assert len(after) == count
    assert [x.name for x in after if x.name != 'noc-sbm-reference'] == [
        x.name for x in before]
    oldby = {x.name: x for x in before}
    for current in after:
        if current.name in oldby:
            assert normalize(current, after) == normalize(
                oldby[current.name], before), current.name
    receipt['suites'][suite] = {
        'count': len(after), 'names': [x.name for x in after]}
    if suite == 'components':
        chosen = next(x for x in after if x.name == 'noc-sbm-reference')
        receipt['noc_sbm_gate'] = normalize(chosen, after)
(root / 'plan76-preflight.json').write_text(
    json.dumps(receipt, indent=2) + '\n')
print(json.dumps({
    'counts': {k: v['count'] for k, v in receipt['suites'].items()},
    'prior_75_contracts_unchanged': True,
    'runner_sha256': receipt['runner_sha256'],
    'qemu_executed': False, 'gates_executed': False}))
