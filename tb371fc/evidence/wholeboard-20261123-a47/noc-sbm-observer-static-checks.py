from pathlib import Path
import ast
import copy
import hashlib
import json

root = Path('/run/tb371fc-root76-a47')
source = (root / 'capture_noc_sbm.py').read_text()
tree = ast.parse(source)
assert len(tree.body) == 1 and isinstance(tree.body[0], ast.FunctionDef)
assert tree.body[0].name == 'capture_noc_sbm'
assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) for n in ast.walk(tree))
direct_commands = sorted({n.args[0].value for n in ast.walk(tree)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    and n.func.id == 'command' and n.args
    and isinstance(n.args[0], ast.Constant)})
assert direct_commands == ['qom-get', 'query-status']
namespace = {}
exec(compile(tree, 'capture_noc_sbm-only', 'exec'), namespace)
capture = namespace['capture_noc_sbm']
base = {
    'enabled-reference': True, 'mmio-enabled': True, 'instance-id': 0,
    'clear-address': 0x01506080, 'set-address': 0x01506088,
    'sense-address': 0x01506100, 'supported-port-mask': 255, 'port-count': 8,
    'reference-latency-ns': 1000, 'reference-identity': 0x53424d31,
    'reference-boot-disconnected-mask': 0, 'mapped-window-count': 3,
    'mapped-register-count': 3, 'mapped-bytes': 12,
    'reference-peer-stall-mask': 0, 'requested-mask': 0,
    'sense-mask': 0, 'pending-mask': 0,
    'accepted-set-writes': 0, 'accepted-clear-writes': 0,
    'last-set-mask': 0, 'last-clear-mask': 0,
}
for port in range(8):
    base['port%d-remaining-ns' % port] = 0
    base['port%d-deadline-ns' % port] = -1
results = []


def exercise(name, updates=None, *, expected='complete', error=None,
             status_before=None, status_after=None, change=None):
    values = dict(base)
    values.update(updates or {})
    immutable = copy.deepcopy(values)
    calls, reads = [], {}
    statuses = [status_before or {'running': False, 'status': 'paused'},
                status_after or {'running': False, 'status': 'paused'}]

    def command(cmd, args=None):
        calls.append((cmd, copy.deepcopy(args)))
        if cmd == 'query-status':
            return statuses.pop(0)
        assert cmd == 'qom-get'
        assert args['path'] == '/machine/noc-sbm-config'
        key = args['property']
        reads[key] = reads.get(key, 0) + 1
        if change is not None and key == change[0] and reads[key] > 1:
            return change[1]
        return values[key]

    report = None
    caught = None
    try:
        report = capture(command)
    except RuntimeError as exc:
        caught = str(exc)
    assert values == immutable, name
    if error is not None:
        assert caught and error in caught, (name, caught)
    else:
        assert caught is None, (name, caught)
        assert report['observation_status'] == expected, name
        for flag in [
            'guest_memory_written', 'device_mmio_read',
            'device_property_written', 'host_environment_modified_by_observer',
            'guest_cpu_executed_by_observer', 'virtual_clock_advanced_by_observer',
            'handshake_completed_by_observer', 'hardware_latency_measured',
            'diagnostic_counters_are_hardware', 'traffic_drain_observed',
            'camera_display_venus_execution_proven', 'request_writer_assessed',
            'native_driver_success_assessed', 'userspace_success_assessed',
            'android_boot_passed',
        ]:
            assert report[flag] is False, (name, flag)
    results.append({
        'name': name, 'result': 'passed', 'qom_reads': sum(reads.values()),
        'unique_qom_properties': len(reads),
        'status_reads': sum(cmd == 'query-status' for cmd, _ in calls),
        'expected_observation': expected if error is None else None,
        'expected_error': error,
    })
    return report


exercise('cold connected reference')
pending = {
    'requested-mask': 1, 'pending-mask': 1,
    'accepted-set-writes': 1, 'last-set-mask': 1,
    'port0-remaining-ns': 600, 'port0-deadline-ns': 1600,
}
exercise('pending ordinary deadline', pending)
exercise('zero remaining pending before dispatch',
         {**pending, 'port0-remaining-ns': 0, 'port0-deadline-ns': 1000})
exercise('stalled zero remaining stays pending',
         {**pending, 'reference-peer-stall-mask': 1,
          'port0-remaining-ns': 0, 'port0-deadline-ns': -1})
exercise('disconnect response settled',
         {'requested-mask': 1, 'sense-mask': 1,
          'accepted-set-writes': 1, 'last-set-mask': 1})
exercise('reconnect pending with command history',
         {'sense-mask': 1, 'pending-mask': 1,
          'accepted-set-writes': 1, 'accepted-clear-writes': 1,
          'last-set-mask': 1, 'last-clear-mask': 1,
          'port0-remaining-ns': 1000, 'port0-deadline-ns': 2000})
unknown = exercise('disabled profile is inconclusive and avoids state reads',
                   {'enabled-reference': False, 'mmio-enabled': False},
                   expected='inconclusive')
assert unknown['raw_state_unavailable'] == 'unknown-profile'
assert results[-1]['qom_reads'] == results[-1]['unique_qom_properties'] == 14
exercise('wrong DT address', {'clear-address': 0x01506084},
         expected='inconclusive')
exercise('wrong reference latency', {'reference-latency-ns': 10},
         expected='inconclusive')
exercise('MMIO still disabled', {'mmio-enabled': False},
         expected='inconclusive')
exercise('reserved mask bit', {'reference-peer-stall-mask': 256},
         expected='inconclusive')
exercise('pending xor mismatch', {'pending-mask': 1},
         expected='inconclusive')
exercise('disconnect without set history', {'requested-mask': 1, 'sense-mask': 1},
         expected='inconclusive')
exercise('reconnect without clear history',
         {'sense-mask': 1, 'pending-mask': 1, 'accepted-set-writes': 1,
          'last-set-mask': 1, 'port0-remaining-ns': 1000,
          'port0-deadline-ns': 2000}, expected='inconclusive')
exercise('idle retains invalid remainder', {'port3-remaining-ns': 1},
         expected='inconclusive')
exercise('unstalled pending lacks deadline', {**pending, 'port0-deadline-ns': -1},
         expected='inconclusive')
exercise('stalled pending has active deadline',
         {**pending, 'reference-peer-stall-mask': 1}, expected='inconclusive')
exercise('remaining exceeds reference latency',
         {**pending, 'port0-remaining-ns': 1001}, expected='inconclusive')
exercise('second snapshot changes', change=('accepted-clear-writes', 1),
         expected='inconclusive')
exercise('numeric bool rejected', {'pending-mask': False},
         error='unexpected QOM type/range')
exercise('uint64 overflow rejected', {'accepted-set-writes': 1 << 64},
         error='unexpected QOM type/range')
exercise('signed deadline overflow rejected', {'port0-deadline-ns': 1 << 63},
         error='unexpected QOM type/range')
exercise('running guest rejected before QOM',
         status_before={'running': True, 'status': 'running'},
         error='requires a stopped guest')
exercise('changed stopped status rejected',
         status_after={'running': False, 'status': 'debug'},
         error='status changed during observation')
assert results[0]['qom_reads'] == 62
assert results[0]['unique_qom_properties'] == 38
assert results[0]['status_reads'] == 2
receipt = {
    'schema': 'tb371fc-noc-sbm-observer-memory-preflight-v1',
    'scope': 'Pure AST and in-memory command callback checks; no QEMU or native run',
    'source_sha256': hashlib.sha256(source.encode()).hexdigest(),
    'observer_sha256': hashlib.sha256(
        (root / 'native-noc-sbm-observer.py').read_bytes()).hexdigest(),
    'allowed_direct_commands': direct_commands,
    'cases': results, 'all_passed': True,
    'qemu_executed': False, 'guest_executed': False,
    'external_commands_or_sockets_used': False,
}
(root / 'sbm-observer-memory-preflight.json').write_text(
    json.dumps(receipt, indent=2) + '\n')
print(json.dumps({'cases': len(results), 'all_passed': True,
                  'cold_reference_qom_reads': 62,
                  'qemu_executed': False,
                  'observer_sha256': receipt['observer_sha256']}))
