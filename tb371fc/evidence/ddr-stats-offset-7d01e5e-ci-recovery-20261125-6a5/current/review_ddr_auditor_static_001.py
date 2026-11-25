#!/usr/bin/env python3
"""New independent source-only review; never opens an artifact or imports an auditor."""
import ast
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct

HERE = Path('/dev/shm/ddrstats-recovery-6a5')
ROOT = Path('/dev/shm/ddrstats-candidate-6a5')
AUDITOR = HERE / 'audit_ddr_stats_offset_artifact_7d01e5e_recovery_001.py'
BASELINE = HERE / 'predecessor/audit_rpm_stats_offset_artifact.py'
OUT = HERE / 'independent-ddr-auditor-static-review-recovery-001.json'
HEAD = '7d01e5e294b788f97a8120c2d34122ddb25e6343'
TREE = '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4'


def pin(path, expected=None):
    assert path.is_file() and not path.is_symlink(), path
    raw = path.read_bytes()
    result = {'path': str(path), 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    if expected is not None:
        assert (result['bytes'], result['sha256']) == expected, (path, result)
    return raw, result


def simple(node, env):
    """Evaluate only literal containers and fixed names, never any Python call."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.Tuple):
        return tuple(simple(n, env) for n in node.elts)
    if isinstance(node, ast.List):
        return [simple(n, env) for n in node.elts]
    if isinstance(node, ast.Dict):
        assert all(k is not None for k in node.keys)
        return {simple(k, env): simple(v, env) for k, v in zip(node.keys, node.values)}
    raise ValueError(ast.dump(node))


def constants(tree):
    env = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1:
            try:
                value = simple(n.value, env)
            except (ValueError, KeyError):
                continue
            target = n.targets[0]
            if isinstance(target, ast.Name):
                env[target.id] = value
            elif isinstance(target, ast.Tuple):
                assert len(target.elts) == len(value)
                for key, val in zip(target.elts, value):
                    assert isinstance(key, ast.Name)
                    env[key.id] = val
    return env


assert not OUT.exists(), 'Never overwrite a review receipt'
new_raw, new_pin = pin(AUDITOR, (95924, '4a347b0f0962b21e8653b7ef39db66fadce5b6f8385bded8fb7ea12ddefc521b'))
old_raw, old_pin = pin(BASELINE, (87901, '7eb65a626ce253f3f8469948ddb0669d6bb5256f186f0ef9ffbb3c76b55b10f0'))
new_tree, old_tree = ast.parse(new_raw), ast.parse(old_raw)
compile(new_tree, str(AUDITOR), 'exec')
new_defs = {n.name: n for n in new_tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
old_defs = {n.name: n for n in old_tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
dump = lambda n: ast.dump(n, include_attributes=False)
same = sorted(k for k in old_defs if k in new_defs and dump(old_defs[k]) == dump(new_defs[k]))
changed = sorted(k for k in old_defs if k in new_defs and dump(old_defs[k]) != dump(new_defs[k]))
added, removed = sorted(new_defs.keys() - old_defs.keys()), sorted(old_defs.keys() - new_defs.keys())
assert (len(old_defs), len(new_defs), len(same), len(changed)) == (59, 61, 40, 19)
assert added == ['active_mapping', 'disabled_core_expected'] and removed == []
imports = lambda t: [dump(n) for n in t.body if isinstance(n, (ast.Import, ast.ImportFrom))]
assert imports(new_tree) == imports(old_tree)
env = constants(new_tree)
assert (env['HEAD'], env['TREE'], env['BASE'], env['OFFSET_VALUE'], env['IDENTITY']) == (
    HEAD, TREE, 0x0c3f001c, 0x000f00b8, 0x44534f31)
assert len(env['FIELDS']) == len(set(env['FIELDS'])) == env['GETTERS'] == 17
assert env['PAYLOAD'] == 24

fresh_path = Path('/dev/shm/ddrstats-source-recovery-001/fresh-source-recovery-001.json')
fresh_raw, fresh_pin = pin(fresh_path, (48999, '63f5efb9a14a6b82d3be01849a9d32360864fd32068a50ce1fdd67bd115ada68'))
fresh = json.loads(fresh_raw)
dep_raw, dep_pin = pin(fresh_path.with_name('fresh-dependency-recovery-001.json'),
                      (1176, 'c075d77ce31fad923b4c5ab9e2d2ca390daa87f4858bfd4d7a4c9fa9294d07c9'))
deps = json.loads(dep_raw)
for result in (fresh, deps):
    assert result['passed'] and result['source_commit'] == HEAD and result['source_tree'] == TREE
readback = {row['path']: row for row in fresh['files'] + deps['files']}
source_pins = {}
assert len(env['SOURCE_PINS']) == 15
for relative, identity in env['SOURCE_PINS'].items():
    raw, p = pin(ROOT / relative, identity)
    independent = readback[relative]
    blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    assert (p['bytes'], p['sha256'], blob) == (
        independent['bytes'], independent['sha256'], independent['git_blob_sha1'])
    source_pins[relative] = {**p, 'git_blob_sha1': blob, 'fresh_exact_head_readback_matched': True}
helper_rel = 'tb371fc/tests/test_camera_gdsc.py'
helper_raw, helper_pin = pin(ROOT / helper_rel, (74782, 'e187b3de97b5ae2d53376bd41d1f82099053c69bce9034af572830f8e1c1e619'))
assert helper_pin['sha256'] == readback[helper_rel]['sha256']

contract = json.loads((ROOT / 'tb371fc/evidence/ddr-stats-offset-20261125-6a5/test-ci-contract.json').read_bytes())
returns = [n for n in new_defs['report_constants'].body if isinstance(n, ast.Return)]
assert len(returns) == 1
assert simple(returns[0].value, env) == contract['expected_values']
assert env['LENGTHS'] == contract['expected_lengths']
manifest = json.loads((ROOT / 'tb371fc/docs/ddr-stats-offset-sm8250-registers.json').read_bytes())
public_inputs = []
for local, row, fields in zip(('vendor', 'vendor_dt', 'upstream', 'upstream_dt'), manifest['source_pins'], env['PUBLIC_INPUTS']):
    prefix, repo, revision, relative, size, blob, digest = fields
    raw, p = pin(HERE / ('public-inputs/' + local + '.txt'), (size, digest))
    actual_blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
    assert actual_blob == blob
    assert row == {'path': prefix + '/' + relative, 'source_path': relative, 'repository': repo,
                   'revision': revision, 'bytes': size, 'git_blob_sha': blob, 'sha256': digest,
                   'url': 'https://raw.githubusercontent.com/' + repo + '/' + revision + '/' + relative}
    public_inputs.append({**p, 'repository': repo, 'revision': revision, 'source_path': relative,
                          'git_blob_sha1': actual_blob, 'exact_published_manifest_identity': True})
assert len(env['PUBLIC_INPUTS']) == len(manifest['source_pins']) == len(public_inputs) == 4
wire_on = struct.pack('>IIQII', 1, 0x44534f31, 0x0c3f001c, 4, 0x000f00b8)
wire_off = struct.pack('>IIQII', 0, 0x44534f31, 0x0c3f001c, 4, 0)
assert wire_on.hex() == '0000000144534f31000000000c3f001c00000004000f00b8'
assert wire_off.hex() == '0000000044534f31000000000c3f001c0000000400000000'

diff_raw, diff_pin = pin(HERE / 'ddr-auditor-recovery-001.diff',
                         (50355, '83fe5d15aa5e7d94c88fa2c92c12ae9f62d6f796a0541919397ae41020daa337'))
derivation_raw, derivation_pin = pin(HERE / 'ddr-auditor-recovery-derivation-001.json')
derivation = json.loads(derivation_raw)
assert derivation['new_auditor']['sha256'] == new_pin['sha256']
assert sorted(derivation['functions_classes_identical_to_predecessor']) == same
assert sorted(derivation['changed_functions_classes']) == changed
assert sorted(derivation['added_functions']) == added and derivation['removed_functions'] == []
validation_raw, validation_pin = pin(HERE / 'source-only-validation-001.json',
                                     (5282, '9a296acb3f955c790f65a8710b0c9d30cd39d740ddccf04c984d5576d0f20b5f'))
validation = json.loads(validation_raw)
assert validation['passed'] and not validation['artifact_opened'] and not validation['main_invoked']

receipt = {
    'schema': 'tb371fc-ddrstats-recovery-independent-auditor-static-review-v1',
    'observed_utc': datetime.now(timezone.utc).isoformat(),
    'reviewer': '/root/rpmstats_frontier', 'passed': True, 'static_review_passed': True,
    'source_commit': HEAD, 'source_tree': TREE,
    'auditor': new_pin, 'predecessor': {**old_pin, 'immutable_commit': 'd103750ca2a191559daeb9eff4341b8c80013dc6'},
    'review_script': pin(Path(__file__))[1], 'derivation': derivation_pin, 'complete_diff': diff_pin,
    'fresh_exact_head_readback': fresh_pin, 'fresh_dependency_readback': dep_pin,
    'author_source_only_validation': validation_pin,
    'independently_rehashed_source_pins': source_pins, 'inherited_transport': helper_pin,
    'independently_rehashed_complete_public_inputs': public_inputs,
    'ast_comparison': {'predecessor_count': 59, 'current_count': 61, 'unchanged_count': 40,
                       'changed_count': 19, 'added': added, 'removed': removed,
                       'unchanged': same, 'changed': changed, 'imports_unchanged': True,
                       'strict_zip_json_protocol_and_whole_stream_parsers_preserved': True},
    'manual_semantic_review': [
        {'area': 'public-source derivation', 'passed': True,
         'finding': 'All four complete pinned files are rehashed before parsing. Vendor qcom,ddr-stats@c3f0000 has c300000/1000 and c3f001c/4 resources; its bounded probe has one offset read, zero-offset ENODEV and base-plus-offset. Its sysfs construction registers but does not call the payload reader. Upstream rpmh-stats selects rpmh_data.ddr_stats_offset=b8. Arithmetic yields f00b8. Header 8, record 16, maximum 20/328 bytes are explicitly maximum ABI bounds; no runtime record count, mapped payload or reset value is asserted.'},
        {'area': 'enabled versus disabled mapping', 'passed': True,
         'finding': 'Every saved FlatView is bound to the exact info mtree -f QMP reply. Enabled requires exactly the c3f001c..c3f001f offset leaf; disabled requires no active leaf. Seven observations cover enabled holes, default-off before/after reset and both migration directions before cont/after final reset access. Adjacent DDR/payload holes avoid the separately mapped RPM word.'},
        {'area': 'default-off transport', 'passed': True,
         'finding': 'Ten typed operations require 12 exact generic unassigned chunks: qword read/write each split into two four-byte chunks. Zero DDR callbacks, zero active mapped bytes, no global enable override, all 22 QOM worlds and reset preservation are checked. Failed numeric read values are never data oracles.'},
        {'area': 'completed target transport and ordering', 'passed': True,
         'finding': 'Disabled target read/write/read requires exactly three generic four-byte rejections and zero callbacks. Enabled target retains the one read-only write callback and valid read checks. Each target has 187 getters, 17 first plus 17 repeated pure getters, RAM-only prewarm, one cont, full QMP/QTest consumption and actual restored RAM witness. Pinned producer places the first FlatView after both snapshots and restored witness but before cont; second FlatView follows final post-reset read. No shared timestamp is invented.'},
        {'area': 'wire, original streams and negative migrations', 'passed': True,
         'finding': 'DSO1 five-field 24-byte wire is reconstructed independently. Both complete original streams and six one-bit variants are hashed; exact framing, bit location, original preservation, specific load errors and absence of MMIO/cont are retained. Two cross-profile failures remain required, with no state repair.'},
        {'area': 'ordinary accesses and raw boundaries', 'passed': True,
         'finding': 'All prior typed width/alignment, readonly writes, reset, immutable QOM setters and paused-TCG checks remain. Raw-five-byte accesses separately validate the exact successful four-byte prefix and failed one-byte hole; no whole-buffer success, atomicity or failed suffix data is claimed.'},
        {'area': 'source and output binding', 'passed': True,
         'finding': 'All 15 current-head source pins plus inherited transport match fresh full-byte GitHub recovery. Exact contract values and lengths agree. Four input lengths/SHA256/Git blobs and both generated output bytes are independently checked; the auditor does not run the generator or producer.'},
        {'area': 'fail-closed main and honest recovery identity', 'passed': True,
         'finding': 'Published head/tree and source pins are checked before archive access. ZIP identity is checked before and after audit; passed is written only after complete checks. Existing output is refused. New recovery identity expressly disclaims recovery of the lost e083 source and lost actual receipts. ZIP/tree evidence limits, former compile failure and native/Android exclusions remain explicit.'},
    ],
    'independent_wire': {'enabled_hex': wire_on.hex(), 'disabled_hex': wire_off.hex(),
                         'payload_bytes': 24, 'physical_fields': 5, 'mutation_cases': 6},
    'exact_contract_constants_and_lengths_match': True,
    'material_defects_found': [], 'blocking_conditions_before_artifact_review': [],
    'suitable_for_separately_authorized_actual_artifact_audit': True,
    'review_limits': 'Source-only review of a newly derived auditor. No actual ZIP contents, build result, runtime qualification, guest progress, device readiness or Android boot is established.',
    'auditor_executed_or_imported': False, 'producer_executed_or_imported': False,
    'artifact_opened': False, 'artifact_audit_executed': False, 'CI_or_QEMU_or_native_run': False,
    'device_RPC_or_SSH_used': False, 'source_branch_mutated': False,
    'original_lost_review_recreated': False, 'original_lost_auditor_byte_identity_claimed': False,
    'new_review_identity': True,
}
with OUT.open('x') as f:
    json.dump(receipt, f, indent=2, sort_keys=True)
    f.write('\n')
print(json.dumps(pin(OUT)[1]))
