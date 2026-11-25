#!/usr/bin/env python3
"""Fresh exact-commit recovery. Does not reconstruct lost historical receipts."""
import base64
import datetime
import hashlib
import json
from pathlib import Path

ROOT = Path('/dev/shm/ddrstats-source-recovery-001')
DEST = Path('/dev/shm/ddrstats-candidate-6a5')
HEAD = '7d01e5e294b788f97a8120c2d34122ddb25e6343'
TREE = '5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4'
PARENT = '420819df9cfd76dc98cb4705987c269047d7f214'
BASE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'
REPO = 'cyjin-yl/qemu-tb371fc'

def sha(data):
    return hashlib.sha256(data).hexdigest()

def git(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()

def pin(path):
    data = path.read_bytes()
    return {'path': str(path), 'bytes': len(data), 'sha256': sha(data), 'git_blob_sha1': git('blob', data)}

def api_record(path):
    d = json.loads(path.read_bytes())
    assert not d['result'].get('isError', False)
    value = d['result']['structuredContent']
    if value.get('encoding') != 'base64':
        value = json.loads(value['content'])
    return d, value

raw_pins = []
for commit, tree, parents in [(HEAD, TREE, [PARENT]), (PARENT, BASE, None)]:
    path = ROOT / 'git/commits' / (commit + '.json')
    request, data = api_record(path)
    assert request['request']['url'] == f'https://api.github.com/repos/{REPO}/git/commits/{commit}'
    assert data['sha'] == commit and data['tree']['sha'] == tree
    if parents is not None:
        assert [p['sha'] for p in data['parents']] == parents
    raw_pins.append(pin(path))

cache = {}
def tree(ident):
    if ident not in cache:
        path = ROOT / 'git/trees' / (ident + '.json')
        request, data = api_record(path)
        assert request['request']['url'] == f'https://api.github.com/repos/{REPO}/git/trees/{ident}'
        assert data['sha'] == ident and data['truncated'] is False
        rows = data['tree']
        assert len(rows) == len({r['path'] for r in rows})
        raw = bytearray()
        for r in sorted(rows, key=lambda r:(r['path'] + ('/' if r['type'] == 'tree' else '')).encode()):
            assert '/' not in r['path'] and r['path'] not in ('', '.', '..')
            assert r['type'] in ('blob','tree','commit')
            raw += (format(int(r['mode'],8),'o') + ' ' + r['path']).encode() + b'\0' + bytes.fromhex(r['sha'])
        assert git('tree', bytes(raw)) == ident
        cache[ident] = {r['path']:r for r in rows}
        raw_pins.append(pin(path))
    return cache[ident]

def lookup(root, path):
    pieces = path.split('/')
    for i, name in enumerate(pieces):
        row = tree(root)[name]
        if i == len(pieces) - 1:
            return row
        assert row['type'] == 'tree'
        root = row['sha']
    raise AssertionError('empty path')

plan = json.loads((ROOT / 'explicit-recovery-paths.json').read_bytes())
assert plan['source_commit'] == HEAD and plan['source_tree'] == TREE
paths = plan['paths']
assert len(paths) == len({r['path'] for r in paths}) == 46
assert sum(r['role'] == 'publication' for r in paths) == 41
publication = {r['path'] for r in paths if r['role'] == 'publication'}
changed, unchanged = [], []
def compare(a, b, prefix=''):
    old, new = tree(a) if a else {}, tree(b)
    assert set(old).issubset(new), 'unexpected deletion'
    for name, current in sorted(new.items()):
        prior = old.get(name)
        path = prefix + name
        if prior and all(prior[k] == current[k] for k in ('mode','type','sha')):
            unchanged.append({'path':path,'type':current['type'],'sha':current['sha']})
        elif current['type'] == 'tree':
            assert prior is None or prior['type'] == 'tree'
            assert any(p.startswith(path + '/') for p in publication), path
            compare(prior['sha'] if prior else None, current['sha'], path + '/')
        else:
            assert current['type'] == 'blob' and path in publication, path
            changed.append({'path':path,'previous_blob':prior['sha'] if prior else None,
                            'current_blob':current['sha'],'bytes':current['size']})
compare(BASE, TREE)
assert {r['path'] for r in changed} == publication
assert sum(r['previous_blob'] is None for r in changed) == 33
assert sum(r['previous_blob'] is not None for r in changed) == 8
assert len(unchanged) == 1091

runner_pins = {
    'boot_android.py': (40941,'61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1'),
    'boot_android_from_ready.py': (31692,'b2ef676a99c7064b9d31035d1b3e1e471d613500cba6373a0992b4c4f4e4154d'),
    'android_boot_inputs.py': (36402,'08affe8ef5c40c7350be9e4c0f0482c4c5e594a3dcff7e3a88cf7887ea1eb5d8'),
    'boot_qmp.py': (2822,'b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c'),
    'legacy_printk.py': (4275,'6b7d18188ef8987c32135fef179f271dcf7284766a233626d09269696cada27d'),
}
files = []
for row in paths:
    rel = row['path']
    assert not rel.startswith('/') and '..' not in rel.split('/')
    raw_path = ROOT / 'files' / f"file-{row['index']:03d}.json"
    req, response = api_record(raw_path)
    assert req['request'] == {'repository_full_name': REPO,'path':rel,'ref':HEAD,'encoding':'base64'}
    assert response['encoding'] == 'base64'
    data = base64.b64decode(''.join(response['content'].split()),validate=True)
    data.decode('utf-8')
    blob = git('blob',data)
    entry = lookup(TREE,rel)
    assert entry['mode'] == '100644' and entry['type'] == 'blob'
    assert blob == response['sha'] == entry['sha'] and len(data) == entry['size']
    if row['role'] == 'runner':
        assert (len(data),sha(data)) == runner_pins[Path(rel).name]
        prior = lookup(BASE,rel)
        assert prior['mode'] == entry['mode'] and prior['sha'] == entry['sha']
    target = DEST / rel
    target.parent.mkdir(parents=True,exist_ok=True)
    assert target.parent.resolve(strict=True) == target.parent
    with target.open('xb') as stream:
        stream.write(data)
    assert target.read_bytes() == data
    files.append({'path':rel,'role':row['role'],'bytes':len(data),'sha256':sha(data),
                  'git_blob_sha1':blob,'mode':'100644','type':'blob',
                  'fresh_full_exact_commit_read':True,'disk_readback_exact':True})
    raw_pins.append(pin(raw_path))
assert sum(r['bytes'] for r in files if r['role'] == 'publication') == 645747
assert sum(r['bytes'] for r in files) == 761879
assert len(cache) == 25
receipt = {
    'schema':'tb371fc-ddrstats-fresh-source-recovery-v1',
    'recovered_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'passed':True,'source_commit':HEAD,'source_tree':TREE,'sole_parent':PARENT,'parent_tree':BASE,
    'destination':str(DEST),'publication_file_count':41,'runner_file_count':5,
    'publication_bytes':645747,'all_recovered_bytes':761879,
    'new_paths':33,'modified_paths':8,'unchanged_file_or_subtree_entries':1091,
    'all_other_tree_entries_unchanged':True,'git_tree_objects_recomputed':25,
    'five_runners_equal_parent_and_recorded_hashes':True,
    'prior_agent_caches_present':False,
    'lost_original_manifest_or_readback_receipts_recreated':False,
    'historical_receipt_hashes_from_conversation_only':{
        'original_manifest_sha256':'95039f6345448795d5b6c3c3a056a3bcf4ea74d8bdae321d908bc05fb2406314',
        'original_readback_sha256':'c12b378d4e3658af138bd2756d8cd52fda36b2e8fa93e12e14cb959f49721c3d'},
    'scope':'New remote source recovery and verification, not a reproduction of lost local evidence.',
    'CI_rerun':False,'artifact_audit_run':False,'device_or_SSH_used':False,
    'QEMU_or_native_run':False,'GitHub_mutation':False,
    'files':files,'tree_delta':changed,'raw_api_records':raw_pins,
}
out = ROOT / 'fresh-source-recovery-001.json'
with out.open('x') as stream:
    stream.write(json.dumps(receipt,indent=2) + '\n')
print(json.dumps({'receipt':pin(out),'restored_files':len(files),'bytes':761879,
                  'raw_api_records':len(raw_pins)},indent=2))
