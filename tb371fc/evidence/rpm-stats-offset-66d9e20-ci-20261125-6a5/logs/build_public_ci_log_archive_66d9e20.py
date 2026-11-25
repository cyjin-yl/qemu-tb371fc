#!/usr/bin/env python3
"""Archive exact public GitHub job-log bytes after both real log audits pass."""
from pathlib import Path
import base64
import gzip
import hashlib
import io
import json
import zlib

ROOT = Path('/dev/shm/rpmstats-ci-review')
HEAD = '66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b'
TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'
MERGE = 'c7ba198d6bf9bd5e3255e207d07efd85a026a1a9'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path):
    raw = path.read_bytes()
    return {'file': path.name, 'bytes': len(raw), 'sha256': digest(raw)}


def compress(raw):
    stream = io.BytesIO()
    with gzip.GzipFile(filename='', fileobj=stream, mode='wb', compresslevel=9, mtime=0) as writer:
        writer.write(raw)
    return stream.getvalue()


def main():
    board_path = ROOT / 'rpmstats-66d9e20-board-rawlog-observation-001.json'
    a77_path = ROOT / 'rpmstats-66d9e20-a77-rawlog-audit-001.json'
    metadata_path = ROOT / 'rpmstats-66d9e20-final-ci-github-metadata.json'
    board, a77, metadata = [json.loads(p.read_bytes()) for p in (board_path, a77_path, metadata_path)]
    for audit in (board, a77):
        assert audit['passed'] is True and audit['source_head'] == HEAD
        assert audit['matching_push_pr_tree'] == TREE and len(audit['runs']) == 2
    assert board['schema'] == 'tb371fc-rpm-stats-offset-board-rawlog-observation-v2'
    assert a77['schema'] == 'tb371fc-rpm-stats-offset-a77-rawlog-observation-v1'
    assert metadata['head']['sha'] == HEAD and metadata['head']['tree']['sha'] == TREE
    assert metadata['merge']['sha'] == MERGE and metadata['merge']['tree']['sha'] == TREE
    assert board['github_metadata']['sha256'] == pin(metadata_path)['sha256']
    jobs = [
        ('push', 'board', 45533031476, 138932791819, HEAD, 'rpmstats-66d9e20-push-board-138932791819.log'),
        ('push', 'a77', 45533031476, 138932792020, HEAD, 'a77-138932792020.log'),
        ('pr', 'board', 45533037101, 138932812241, MERGE, 'rpmstats-66d9e20-pr-board-138932812241.log'),
        ('pr', 'a77', 45533037101, 138932812391, MERGE, 'a77-138932812391.log'),
    ]
    rows = []
    for role, kind, run_id, job_id, checkout, filename in jobs:
        run = metadata[role]['run']
        assert run['id'] == run_id and run['head_sha'] == HEAD and run['run_attempt'] == 1
        assert run['status'] == 'completed' and run['conclusion'] == 'success'
        assert run['event'] == ('push' if role == 'push' else 'pull_request')
        matching_jobs = [j for j in metadata[role]['jobs']['jobs'] if j['id'] == job_id]
        assert len(matching_jobs) == 1
        job = matching_jobs[0]
        assert job['run_id'] == run_id and job['status'] == 'completed' and job['conclusion'] == 'success'
        assert all(s['status'] == 'completed' and s['conclusion'] == 'success' for s in job['steps'])
        audit = board if kind == 'board' else a77
        observed = [r for r in audit['runs'] if r['run_id'] == run_id and r['job_id'] == job_id]
        assert len(observed) == 1 and observed[0]['actual_checkout'] == checkout
        observed = observed[0]
        raw = (ROOT / filename).read_bytes()
        expected_pin = observed['raw_log'] if kind == 'board' else {
            'bytes': observed['log_bytes'], 'sha256': observed['log_sha256']}
        assert len(raw) == expected_pin['bytes'] and digest(raw) == expected_pin['sha256']
        raw.decode('utf-8', errors='strict')
        packed = compress(raw)
        assert packed[:10] == bytes.fromhex('1f8b08000000000002ff')
        assert packed == compress(raw) and gzip.decompress(packed) == raw
        encoded = base64.b64encode(packed).decode('ascii')
        assert base64.b64decode(encoded, validate=True) == packed
        rows.append({
            'role': role + '-' + kind, 'event': run['event'], 'run_id': run_id,
            'run_attempt': 1, 'job_id': job_id, 'job_name': job['name'],
            'source_commit': HEAD, 'actual_checkout': checkout,
            'job_completed_at': job['completed_at'], 'job_completed_success': True,
            'public_job_url': job['html_url'], 'filename': filename,
            'original_bytes': len(raw), 'original_sha256': digest(raw),
            'gzip_mtime': 0, 'gzip_bytes': len(packed), 'gzip_sha256': digest(packed),
            'gzip_header_hex': packed[:10].hex(), 'payload_encoding': 'base64',
            'gzip_base64': encoded,
        })
    archive = {
        'schema': 'tb371fc-rpmstats-public-ci-rawlogs-v1',
        'repository': 'cyjin-yl/qemu-tb371fc', 'source_commit': HEAD,
        'source_tree': TREE, 'pull_request': 36, 'pr_merge_commit': MERGE,
        'log_count': 4, 'original_bytes_total': sum(row['original_bytes'] for row in rows),
        'gzip_bytes_total': sum(row['gzip_bytes'] for row in rows),
        'representation': 'Complete UTF-8 public GitHub job text returned by the connector, with its original BOM and newlines retained; no line normalization.',
        'compression': {'format': 'gzip', 'level': 9, 'mtime': 0, 'filename': '',
                        'zlib_version': zlib.ZLIB_VERSION, 'header_os_byte': 255},
        'restore_and_verify': 'For each log: base64-decode gzip_base64, verify gzip_bytes and gzip_sha256, gzip-decompress, then verify original_bytes and original_sha256. Save the resulting bytes without text decoding or newline conversion.',
        'audit_receipts': [pin(board_path), pin(a77_path)],
        'github_metadata': pin(metadata_path),
        'all_four_actual_job_log_audits_passed': True,
        'scope': {'public_GitHub_CI_job_logs_only': True, 'logs_normalized': False,
                  'private_host_or_native_data_included': False, 'native_result_claimed': False,
                  'whole_CI_artifact_qualification_claimed_by_archive': False},
        'logs': rows,
    }
    output = ROOT / 'rpmstats-66d9e20-public-ci-rawlogs-001.json'
    with output.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(archive, stream, indent=2, ensure_ascii=True)
        stream.write('\n')
    print(json.dumps({'archive': pin(output), 'logs': [
        {key: value for key, value in row.items() if key != 'gzip_base64'} for row in rows]}))


if __name__ == '__main__':
    main()
