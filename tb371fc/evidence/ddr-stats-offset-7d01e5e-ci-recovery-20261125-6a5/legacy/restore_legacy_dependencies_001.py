#!/usr/bin/env python3
"""Restore only exact current-head public source dependencies, without executing them."""
import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

HERE = Path('/dev/shm/ddrstats-legacy-recovery-001')
PRIOR = Path('/dev/shm/ddrstats-source-recovery-001')
DEST = Path('/dev/shm/ddrstats-candidate-6a5')
HEAD = '7d01e5e294b788f97a8120c2d34122ddb25e6343'
TREE = '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4'
REPO = 'cyjin-yl/qemu-tb371fc'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def git(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def pin(path):
    raw = path.read_bytes()
    return {'path': str(path), 'bytes': len(raw), 'sha256': digest(raw)}


def api(path):
    rec = json.loads(path.read_bytes())
    assert not rec['result'].get('isError')
    data = rec['result']['structuredContent']
    if data.get('encoding') != 'base64':
        data = json.loads(data['content'])
    return rec, data


out = HERE / 'fresh-legacy-source-dependency-recovery-001.json'
assert not out.exists()
plan = json.loads((HERE / 'source-dependency-plan-001.json').read_bytes())
assert plan['source_head'] == HEAD and plan['source_tree'] == TREE
assert len(plan['files']) == len({r['path'] for r in plan['files']}) == 23
assert sum(not r['exists'] for r in plan['files']) == 16
rec, commit = api(PRIOR / ('git/commits/' + HEAD + '.json'))
assert rec['request']['url'] == f'https://api.github.com/repos/{REPO}/git/commits/{HEAD}'
assert commit['sha'] == HEAD and commit['tree']['sha'] == TREE
trees, raw_pins = {}, []


def tree(sha):
    if sha not in trees:
        path = HERE / ('git/trees/' + sha + '.json')
        if not path.exists():
            path = PRIOR / ('git/trees/' + sha + '.json')
        request, data = api(path)
        assert request['request']['url'] == f'https://api.github.com/repos/{REPO}/git/trees/{sha}'
        assert data['sha'] == sha and data['truncated'] is False
        assert len(data['tree']) == len({r['path'] for r in data['tree']})
        raw = bytearray()
        for row in sorted(data['tree'], key=lambda r: (r['path'] + ('/' if r['type'] == 'tree' else '')).encode()):
            assert '/' not in row['path'] and row['path'] not in ('', '.', '..')
            assert row['type'] in ('tree', 'blob', 'commit')
            raw += (format(int(row['mode'], 8), 'o') + ' ' + row['path']).encode() + b'\0' + bytes.fromhex(row['sha'])
        assert git('tree', bytes(raw)) == sha
        trees[sha] = {r['path']: r for r in data['tree']}
        raw_pins.append(pin(path))
    return trees[sha]


def lookup(path):
    sha, parts = TREE, path.split('/')
    for i, part in enumerate(parts):
        row = tree(sha)[part]
        if i == len(parts) - 1:
            return row
        assert row['type'] == 'tree'
        sha = row['sha']


files, index = [], 0
for expected in plan['files']:
    relative = expected['path']
    entry = lookup(relative)
    assert entry['type'] == 'blob' and entry['mode'] == '100644'
    target = DEST / relative
    if expected['exists']:
        assert target.is_file() and not target.is_symlink()
        data = target.read_bytes()
        basis = 'rehashed previous fresh exact-head recovery'
    else:
        raw_path = HERE / 'files' / f'file-{index:03d}.json'
        request, response = api(raw_path)
        index += 1
        assert request['request'] == {'repository_full_name': REPO, 'path': relative, 'ref': HEAD, 'encoding': 'base64'}
        assert response['encoding'] == 'base64'
        data = base64.b64decode(''.join(response['content'].split()), validate=True)
        assert git('blob', data) == response['sha'] == entry['sha']
        raw_pins.append(pin(raw_path))
        basis = 'new complete exact-head GitHub read'
    data.decode('utf-8', errors='strict')
    assert digest(data) == expected['sha256'], relative
    if 'bytes' in expected:
        assert len(data) == expected['bytes'], relative
    assert len(data) == entry['size'] and git('blob', data) == entry['sha'], relative
    if not expected['exists']:
        target.parent.mkdir(parents=True, exist_ok=True)
        assert target.parent.resolve(strict=True) == target.parent
        with target.open('xb') as f:
            f.write(data)
    assert target.read_bytes() == data
    files.append({'path': relative, 'scope': expected['scope'], 'bytes': len(data), 'sha256': digest(data),
                  'git_blob_sha1': entry['sha'], 'exact_current_tree_proved': True,
                  'basis': basis, 'disk_readback_exact': True})
assert index == 16
receipt = {'schema': 'tb371fc-ddrstats-fresh-legacy-source-dependency-recovery-v1',
           'observed_utc': datetime.now(timezone.utc).isoformat(), 'passed': True,
           'source_commit': HEAD, 'source_tree': TREE, 'destination': str(DEST),
           'dependency_count': 23, 'newly_fetched_files': 16, 'previously_recovered_rehashed_files': 7,
           'files': files, 'raw_api_records': raw_pins, 'git_tree_objects_recomputed': len(trees),
           'artifact_opened_or_executed': False, 'auditor_or_producer_executed': False,
           'QEMU_native_device_or_SSH_used': False, 'GitHub_mutated': False,
           'original_lost_receipts_recreated': False}
with out.open('x') as f:
    json.dump(receipt, f, indent=2)
    f.write('\n')
print(json.dumps({'receipt': pin(out), 'files': files}, indent=2))
