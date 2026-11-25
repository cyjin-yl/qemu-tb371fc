#!/usr/bin/env python3
"""Source-only DDR test/CI validation; never starts QEMU or a guest."""
import ast
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
BASELINE = ROOT / '.work/ddr-ci-baseline'
PARENT = '420819df9cfd76dc98cb4705987c269047d7f214'
BASELINE_SHA = '5ed8bc26826b07347d21ea3bf6dc5c392e449acb241e8007b20debe0f21ba1ea'
NEW_NAMES = {'ddr-stats-offset-source-generation', 'ddr-stats-offset-sm8250-reference'}

def record(path):
    data = path.read_bytes()
    return {'path': str(path.relative_to(ROOT)), 'bytes': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}

def write(name, data):
    path = OUT / name
    assert not path.exists(), path
    path.write_text(json.dumps(data, indent=2) + '\n')

scripts = [ROOT / 'tb371fc/tests/test_ddr_stats_offset_sm8250.py',
           ROOT / 'tb371fc/scripts/ci_device_tests.py',
           ROOT / 'tb371fc/scripts/test_ci_device_tests.py']
for path in scripts:
    compile(path.read_bytes(), str(path), 'exec')

# Execute only pure metadata and wire-packing functions selected by exact names.
# No test rig, subprocess, socket, migration or source-download call is imported.
tree = ast.parse(scripts[0].read_text())
pure_functions = {'expected_world', 'validate_world', 'offset_payload'}
constant_names = {'TYPE', 'DEVICE', 'BASE', 'RAM', 'IDENTITY', 'OFFSET_VALUE',
                  'VMSTATE_BYTES', 'FIRST_QOM_COUNT', 'EXPECTED_CHECKS', 'REGION',
                  'DEFAULT_MACHINE', 'UINT_FIELDS', 'BOOL_FIELDS', 'QOM_FIELDS',
                  'SCENARIOS', 'CORRUPT', 'REPORT_CONSTANTS'}
selected = []
for node in tree.body:
    if isinstance(node, ast.Assign):
        names = {item.id for target in node.targets for item in ast.walk(target)
                 if isinstance(item, ast.Name)}
        if names and names <= constant_names:
            selected.append(node)
    elif isinstance(node, ast.FunctionDef) and node.name in pure_functions:
        selected.append(node)
namespace = {'struct': struct}
exec(compile(ast.Module(body=selected, type_ignores=[]), '<pure-ddr-metadata>', 'exec'), namespace)
assert len(namespace['UINT_FIELDS']) == 11
assert len(namespace['BOOL_FIELDS']) == 6
assert len(set(namespace['QOM_FIELDS'])) == 17
assert namespace['BASE'] == 0x0c3f001c
assert namespace['IDENTITY'] == 0x44534f31
assert namespace['OFFSET_VALUE'] == 0xf00b8
assert namespace['SCENARIOS'] == ('enabled', 'disabled')
ordinary = next(node for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == 'ORDINARY'
                        for target in node.targets))
ordinary_names = [item.id for item in ordinary.value.elts]
assert len(ordinary_names) == len(set(ordinary_names)) == 8
assert 8 + 2 + 2 + 2 + 6 == namespace['EXPECTED_CHECKS'] == 20
wire = []
for enabled in (True, False):
    world = namespace['expected_world'](enabled)
    payload = namespace['offset_payload'](world)
    expected = struct.pack('>IIQII', int(enabled), 0x44534f31,
                           0x0c3f001c, 4, 0xf00b8 if enabled else 0)
    assert payload == expected and len(payload) == 24
    wire.append({'profile_enabled': enabled, 'payload_hex': payload.hex(),
                 'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()})
mutations_node = next(node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                      and any(isinstance(target, ast.Name) and target.id == 'mutations'
                              for target in node.targets))
mutations = eval(compile(ast.Expression(mutations_node.value), '<literal-wire-mutations>', 'eval'), namespace)
assert mutations == (
    ('profile', 'enabled', 0, 4, 1, 2, True, 'migrated profile mismatch'),
    ('identity', 'enabled', 4, 4, 0x44534f31, 1, True, 'migrated identity mismatch'),
    ('physical_base', 'enabled', 8, 8, 0x0c3f001c, 4, True, 'migrated geometry mismatch'),
    ('window_bytes', 'enabled', 16, 4, 4, 1, True, 'migrated geometry mismatch'),
    ('enabled_offset', 'enabled', 20, 4, 0xf00b8, 1, True, 'migrated offset mismatch'),
    ('disabled_offset', 'disabled', 20, 4, 0, 1, False, 'migrated offset mismatch'),
)
wire_mutants = []
for name, scenario, offset, width, old, xor, enabled, diagnostic in mutations:
    original = namespace['offset_payload'](namespace['expected_world'](enabled))
    assert int.from_bytes(original[offset:offset + width], 'big') == old
    changed = bytearray(original)
    changed[offset:offset + width] = (old ^ xor).to_bytes(width, 'big')
    differences = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
    assert len(differences) == 1
    assert sum((a ^ b).bit_count() for a, b in zip(original, changed)) == 1
    wire_mutants.append({'name': name, 'field_offset': offset, 'field_bytes': width,
                         'changed_byte_offsets': differences, 'changed_bits': 1,
                         'expected_diagnostic': diagnostic})

spec = importlib.util.spec_from_file_location('ddr_source_validation_ci', scripts[1])
ci = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ci
spec.loader.exec_module(ci)
args = SimpleNamespace(suite='all', qemu=Path('/test/qemu'),
                       build_dir=Path('/test/build'), kernel=None)
all_cases = ci.plan(args, Path('/test/runtime'))
assert len(all_cases) == len({case.name for case in all_cases}) == 104
def normalize(value):
    if isinstance(value, Path):
        value = str(value)
    if isinstance(value, str):
        for case in all_cases:
            physical, token = str(case.fixture), '@FIXTURE:' + case.name + '@'
            value = token if value == physical else value.replace(physical + '/', token + '/')
        return value.replace(str(ci.ROOT), '@ROOT@').replace(sys.executable, '@PYTHON@')
    if isinstance(value, list):
        return [normalize(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize(item) for key, item in value.items()}
    return value
retained = [normalize(vars(case)) for case in all_cases if case.name not in NEW_NAMES]
assert len(retained) == 102
baseline = json.loads((BASELINE / 'normalized-102-contracts.json').read_text())
assert retained == baseline
canonical = json.dumps(retained, sort_keys=True, separators=(',', ':')).encode()
assert hashlib.sha256(canonical).hexdigest() == BASELINE_SHA
cases = {case.name: case for case in all_cases}
contract_path = ROOT / 'ddr-stats-offset-test-ci-contract.json'
contract = json.loads(contract_path.read_text())
assert contract_path.read_bytes() == (OUT / 'test-ci-contract.json').read_bytes()
case = cases['ddr-stats-offset-sm8250-reference']
assert case.expected_count == contract['check_count'] == 20
assert case.expected_values == contract['expected_values'] == namespace['REPORT_CONSTANTS']
assert case.expected_lengths == contract['expected_lengths']
generation = cases['ddr-stats-offset-source-generation']
assert generation.expected_count == contract['source_generation']['check_count'] == 2
assert generation.expected_values == contract['source_generation']['expected_values']
assert generation.expected_lengths == contract['source_generation']['expected_lengths'] == {'inputs': 4}
args.suite = 'components'
assert len(ci.plan(args, Path('/test/runtime'))) == 99
args.suite = 'linux'
assert len(ci.plan(args, Path('/test/runtime'))) == 5
unit_tree = ast.parse(scripts[2].read_text())
units = [node.name for node in ast.walk(unit_tree)
         if isinstance(node, ast.FunctionDef) and node.name.startswith('test_')]
assert len(units) == len(set(units)) == 36
unchanged = []
for old in json.loads((BASELINE / 'receipt.json').read_text())['files']:
    if old['path'] in {'tb371fc/scripts/ci_device_tests.py', 'tb371fc/scripts/test_ci_device_tests.py'}:
        continue
    actual = record(ROOT / old['path'])
    assert actual == old
    unchanged.append(actual)

write('ci-contract-preservation.json', {
    'schema': 'tb371fc-ddr-stats-offset-ci-contract-preservation-v1',
    'passed': True, 'source_parent': PARENT,
    'prior_contract_count': 102, 'retained_contract_count': 102,
    'all_prior_contract_fields_and_order_unchanged': True,
    'normalization': 'Only repository root, Python executable, and numbered fixture paths are tokenized.',
    'prior_and_retained_normalized_sha256': BASELINE_SHA,
    'component_count': 99, 'linux_count': 5, 'total_count': 104,
    'host_unit_method_count': 36,
    'new_case_names': sorted(NEW_NAMES),
    'unchanged_rpm_and_source_download_files': unchanged,
    'changed_harness_files': [record(path) for path in scripts[1:]],
    'runtime_ci_pass_claimed': False,
})
write('source-only-test-validation.json', {
    'schema': 'tb371fc-ddr-stats-offset-source-only-test-validation-v1',
    'passed': True, 'source_parent': PARENT,
    'scope': 'Python syntax, pure metadata/wire packing, exact CI contract and prior-plan preservation only.',
    'validated_files': [record(path) for path in scripts] + [record(contract_path)],
    'ordinary_group_names': ordinary_names, 'total_group_count': 20,
    'pure_qom_property_count': 17, 'pure_wire_examples': wire,
    'planned_one_bit_mutants_checked': wire_mutants,
    'actual_qemu_migration_streams_created': False,
    'actual_mmio_executed': False, 'qemu_executed': False,
    'guest_cpu_executed': False, 'native_kernel_probe_observed': False,
    'android_boot_passed': False,
})
print(json.dumps({'passed': True, 'source_only': True, 'prior_contracts_unchanged': 102,
                  'components': 99, 'linux': 5, 'host_unit_methods': 36,
                  'ordinary_groups': 8, 'total_groups': 20, 'qom_getters': 17,
                  'wire_bytes': 24, 'planned_single_bit_mutants': 6}))
