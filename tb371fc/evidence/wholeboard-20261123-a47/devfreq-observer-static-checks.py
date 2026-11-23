"""Isolated AST/mock checks only; never import the wrapper or call QEMU."""
import ast
import copy
import hashlib
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = Path('/run/tb371fc-cpufreq-observer-a47/native-cpufreq-observer.py')
MODEL = Path('/run/tb371fc-devfreq-model-a47/hw/misc/qcom-devfreq-fw.c')
OUTPUT = HERE / 'native-devfreq-observer.py'
base, source, model = BASE.read_bytes(), OUTPUT.read_bytes(), MODEL.read_bytes()
assert hashlib.sha256(base).hexdigest() == '56fad0f32aec8d344782f62c0e731b68424f62571ed900d01e26f8d1125fe0c5'
assert hashlib.sha256(model).hexdigest() == 'ce529e9ce637958039c7072512628b1ccd757a5b7aaf80ac59a04edb398d21fe'
insert = (HERE / 'devfreq-insertion.py').read_bytes()
call = (HERE / 'devfreq-call-insertion.txt').read_bytes()
assert source.count(insert) == source.count(call) == 1
assert source.replace(insert, b'', 1).replace(call, b'', 1) == base
tree = ast.parse(source, filename=str(OUTPUT))
compile(tree, str(OUTPUT), 'exec')
node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'capture_devfreq_fw')
assert not any(isinstance(n, (ast.Import, ast.ImportFrom, ast.AsyncFunctionDef)) for n in ast.walk(node))
for n in ast.walk(node):
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
        assert n.func.id not in {'open', 'exec', 'eval', 'compile', '__import__', 'input'}
        if n.func.id == 'command':
            assert isinstance(n.args[0], ast.Constant)
            assert n.args[0].value in {'query-status', 'qom-get'}
tables = {}
for n in ast.walk(node):
    if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
        if n.targets[0].id in {'config_fields', 'state_fields'}:
            tables[n.targets[0].id] = ast.literal_eval(n.value)
assert len(tables['config_fields']) == 17 and len(tables['state_fields']) == 4
getter_types = dict(re.findall(r'\{ "([a-z0-9-]+)", "(uint32|uint64)", Q_[A-Z_]+ \}', model.decode()))
getter_types.update({'enabled-reference': 'bool', 'reference-boot-perf-index': 'uint32', 'firmware-enabled': 'bool'})
getter_types.update({f'lut{i}-frequency-word': 'uint32' for i in range(40)})
getter_types.update({f'lut{i}-nominal-frequency-hz': 'uint64' for i in range(40)})
observer_types = dict(tables['config_fields'], **tables['state_fields'])
observer_types.update({f'lut{i}-frequency-word': 'uint32' for i in range(40)})
observer_types.update({f'lut{i}-nominal-frequency-hz': 'uint64' for i in range(40)})
assert getter_types == observer_types and len(getter_types) == 101

# Execute only the extracted function declaration with no wrapper imports/main.
namespace = {}
isolated = ast.Module(body=[node], type_ignores=[])
exec(compile(ast.fix_missing_locations(isolated), '<devfreq-pure-mock>', 'exec'), namespace)
capture = namespace['capture_devfreq_fw']

def values(perf=0):
    result = {
        'enabled-reference': True, 'reference-boot-perf-index': 0,
        'instance-id': 0, 'enable-address': 0x18590000,
        'frequency-table-address': 0x18590100, 'performance-address': 0x18590320,
        'enable-resource-size': 4, 'frequency-table-resource-size': 160,
        'performance-resource-size': 4, 'lut-row-size': 4,
        'lut-max-entries': 40, 'valid-perf-count': 4,
        'reference-lut-identity': 0x44463031, 'reference-nominal-xo-hz': 19200000,
        'mapped-window-count': 3, 'mapped-register-count': 42, 'mapped-bytes': 168,
        'firmware-enabled': True, 'performance-index': perf,
        'requested-lval': (16, 32, 64, 96)[perf],
        'requested-frequency-hz': (307200000, 614400000, 1228800000, 1843200000)[perf],
    }
    for i in range(40):
        result[f'lut{i}-frequency-word'] = (0x40000010, 0x40000020, 0x40000040, 0x40000060)[min(i, 3)]
        result[f'lut{i}-nominal-frequency-hz'] = (307200000, 614400000, 1228800000, 1843200000)[min(i, 3)]
    return result

class Mock:
    def __init__(self, data, before=None, after=None, perf_after=None, missing=None):
        self.data = copy.deepcopy(data)
        self.before = {'running': False, 'status': 'paused'} if before is None else before
        self.after = {'running': False, 'status': 'paused'} if after is None else after
        self.perf_after, self.missing = perf_after, missing
        self.calls = []
        self.status_count = self.perf_count = 0

    def __call__(self, name, args=None):
        self.calls.append((name, copy.deepcopy(args)))
        if name == 'query-status':
            assert args is None
            self.status_count += 1
            assert self.status_count <= 2
            return copy.deepcopy(self.before if self.status_count == 1 else self.after)
        assert name == 'qom-get' and set(args) == {'path', 'property'}
        assert args['path'] == '/machine/devfreq-l3'
        key = args['property']
        assert key in observer_types
        if key == self.missing:
            raise RuntimeError('mock missing QOM property')
        if key == 'performance-index':
            self.perf_count += 1
            if self.perf_count == 2 and self.perf_after is not None:
                return self.perf_after
        return self.data[key]

records = []

def check(name, expected, data=None, **kwargs):
    mock = Mock(values() if data is None else data, **kwargs)
    saved = copy.deepcopy(mock.data)
    try:
        result = capture(mock)
    except RuntimeError:
        assert expected == 'error', name
        observed = 'error'
    else:
        observed = result['observation_status']
        assert observed == expected, (name, observed, expected)
        for field in ('guest_memory_written', 'device_mmio_read', 'device_property_written',
                      'host_environment_modified_by_observer', 'guest_cpu_executed_by_observer',
                      'factory_lut_observed', 'firmware_execution_proven',
                      'applied_cache_frequency_proven', 'request_writer_assessed',
                      'native_driver_success_assessed', 'userspace_success_assessed', 'android_boot_passed'):
            assert result[field] is False
        if observed == 'complete':
            assert len(result['lut_rows']) == 40
            assert result['decoded_first_adjacent_duplicate_row'] == 4
            assert mock.status_count == 2
            assert sum(c[0] == 'qom-get' for c in mock.calls) == 102
    assert mock.data == saved
    records.append({'case': name, 'observed': observed,
                    'qom_get_calls': sum(c[0] == 'qom-get' for c in mock.calls),
                    'query_status_calls': mock.status_count})
    return mock

check('reference_boot_request', 'complete')
check('nonzero_request_does_not_claim_driver_success', 'complete', data=values(3))
unknown = values(); unknown['enabled-reference'] = False
m = check('unknown_profile_without_runtime_reads', 'inconclusive', data=unknown)
assert m.status_count == 2 and sum(c[0] == 'qom-get' for c in m.calls) == 17
for key, bad in (
    ('enabled-reference', 1), ('reference-boot-perf-index', True),
    ('enable-address', float(0x18590000)), ('performance-index', True),
    ('requested-frequency-hz', 307200000.0), ('lut0-frequency-word', True),
    ('lut0-nominal-frequency-hz', -1), ('performance-index', 4),
):
    data = values(); data[key] = bad
    check(f'type_or_range_reject_{key}_{type(bad).__name__}_{bad}', 'error', data=data)
for key, bad in (
    ('enable-address', 0x18591000), ('reference-boot-perf-index', 1),
    ('reference-lut-identity', 0x44463030), ('reference-nominal-xo-hz', 32000000),
    ('mapped-register-count', 43), ('mapped-window-count', 4), ('mapped-bytes', 172),
    ('lut-max-entries', 39), ('valid-perf-count', 5), ('lut-row-size', 8),
    ('firmware-enabled', False), ('requested-lval', 32), ('requested-frequency-hz', 307200001),
    ('lut39-frequency-word', 0x80000060), ('lut39-nominal-frequency-hz', 1843200001),
):
    data = values(); data[key] = bad
    check(f'inconsistent_value_{key}', 'inconclusive', data=data)
early = values(); early['lut2-frequency-word'] = early['lut1-frequency-word']; early['lut2-nominal-frequency-hz'] = early['lut1-nominal-frequency-hz']
check('wrong_terminal_duplicate', 'inconclusive', data=early)
check('perf_changed_during_capture', 'inconclusive', perf_after=1)
m = check('running_before', 'error', before={'running': True, 'status': 'running'})
assert not any(c[0] == 'qom-get' for c in m.calls)
check('nonboolean_running_before', 'error', before={'running': 0, 'status': 'paused'})
check('empty_status_before', 'error', before={'running': False, 'status': ''})
check('running_after', 'error', after={'running': True, 'status': 'running'})
check('changed_stopped_status', 'error', after={'running': False, 'status': 'shutdown'})
m = check('missing_device_or_property', 'error', missing='enabled-reference')
assert m.status_count == 2
assert len({(r['case'], r['observed']) for r in records}) == len(records)

result = {
    'base_wrapper_sha256': hashlib.sha256(base).hexdigest(),
    'wrapper_bytes': len(source), 'wrapper_sha256': hashlib.sha256(source).hexdigest(),
    'model_c_sha256': hashlib.sha256(model).hexdigest(),
    'old_wrapper_exact_after_removing_two_insertions': True,
    'full_script_ast_and_compile_only_passed': True,
    'model_getter_names_and_types_match': True, 'unique_property_count': len(observer_types),
    'allowed_added_command_names': ['qom-get', 'query-status'],
    'mock_scope': 'Only extracted capture_devfreq_fw executed against in-memory dictionaries; no wrapper imports/main, subprocess, QEMU, sockets or public test scripts.',
    'isolated_mock_cases_passed': len(records), 'cases': records,
    'qemu_executed': False, 'public_test_scripts_executed': False,
    'remote_calls_made': False, 'native_validation_performed': False,
}
(HERE / 'observer-validation.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps({k: v for k, v in result.items() if k != 'cases'}))
