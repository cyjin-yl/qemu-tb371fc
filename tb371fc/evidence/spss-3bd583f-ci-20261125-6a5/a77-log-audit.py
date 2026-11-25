#!/usr/bin/env python3
"""Bounded read-only observation of complete A77 job logs; no guest execution."""
from pathlib import Path
import hashlib
import json
import re
import traceback

ROOT = Path('/dev/shm/spss-ci-review')
HEAD = '3bd583f2079634d01c12160ab3e3f0c5a3e47e86'
MERGE = '92d8915bfdc8f1506500f6833ab36a4049e14e99'
VARIANTS = ('default', 'observed-revidr', 'l2-128k', 'l2-256k', 'l3-512k',
            'l3-1m', 'l3-2m', 'l3-4m', 'crypto-off')
PCI = {'reg-read', 'init', 'legacy-read-write', 'mcq-read-write', 'no-prdt',
       'sync-cache', 'long-prdt', 'short-prdt', 'mcq-cq-wraparound',
       'query-flag', 'query-attribute', 'query-desciptor', 'wb-init',
       'wb-read-write', 'hid', 'legacy-crypto-rejected', 'mcq-crypto-rejected'}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def observe(run, job, event, checkout):
    metadata = json.loads((ROOT / f'a77-run-{run}-metadata.json').read_text())
    require(metadata['head_sha'] == HEAD and metadata['event'] == event,
            'Run head/event identity mismatch')
    jobs = json.loads((ROOT / f'a77-{job}-jobs.json').read_text())['jobs']
    matched = [x for x in jobs if x['id'] == job]
    require(len(matched) == 1, 'Missing unique expected job')
    actual = matched[0]
    require(actual['run_id'] == run and actual['name'] == 'cortex-a77-architecture',
            'Job run/name mismatch')
    require(actual['status'] == 'completed' and actual['conclusion'] == 'success',
            'A77 job is not completed success')
    require(all(s['status'] == 'completed' and s['conclusion'] == 'success'
                for s in actual['steps']), 'A77 job contains an incomplete/failed/skipped step')
    path = ROOT / f'a77-{job}.log'
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    require('\ufffd' not in text, 'Decoded log has replacement characters')
    raw_lines = text.splitlines()
    lines = [re.sub(r'^\ufeff?\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z ', '', l)
             for l in raw_lines]
    checkout_indices = [i for i, l in enumerate(lines)
                        if l == '[command]/usr/bin/git log -1 --format=%H']
    require(len(checkout_indices) == 1, 'No unique checkout SHA observation')
    require(lines[checkout_indices[0] + 1] == checkout, 'Actual checkout SHA differs')
    guest = []
    pci = []
    invalid = []
    verified = []
    for i, line in enumerate(lines):
        match = re.fullmatch(r'PASS ([a-z0-9-]+): ([0-9]+) guest checks', line)
        if match:
            guest.append({'name': match[1], 'guest_checks': int(match[2]),
                          'line_number': i + 1, 'raw_line': raw_lines[i]})
        match = re.fullmatch(r'PASS /aarch64/virt/generic-pcihost/pci-bus-generic/pci-bus/ufs/ufs-tests/([a-z0-9-]+)', line)
        if match:
            pci.append({'name': match[1], 'line_number': i + 1, 'raw_line': raw_lines[i]})
        if line.startswith('PASS rejected '):
            invalid.append({'options': line[len('PASS rejected '):],
                            'line_number': i + 1, 'raw_line': raw_lines[i]})
        if line == 'Verified 852 real guest checks and 2 rejected CPU configurations':
            verified.append({'line_number': i + 1, 'raw_line': raw_lines[i]})
    require(tuple(x['name'] for x in guest) == VARIANTS, 'Nine exact configurations absent or duplicated')
    require(all(x['guest_checks'] >= 75 for x in guest), 'Variant minimum check count failed')
    require(sum(x['guest_checks'] for x in guest) == 852, 'Actual variant counts do not total 852')
    require(len(pci) == 17 and {x['name'] for x in pci} == PCI,
            'Seventeen exact PCI cases absent or duplicated')
    require([x['options'] for x in invalid] == [',l2-cache-size=1M', ',l3-cache-size=128K'],
            'Invalid configuration rejection evidence differs')
    require(len(verified) == 1, 'No unique executed final verification line')
    first, last = pci[0]['line_number'] - 1, verified[0]['line_number']
    forbidden = [raw_lines[i] for i in range(first, last)
                 if re.match(r'^(FAIL\b|SKIP\b|not ok\b|Bail out!|##\[error\])', lines[i])]
    require(not forbidden, 'Failure/skip marker in test output interval')
    return {'run_id': run, 'job_id': job, 'event': event, 'requested_source_head': HEAD,
            'actual_checkout': checkout, 'job_completed_success': True,
            'all_job_steps_completed_success': True,
            'log_path': str(path), 'log_bytes': len(raw), 'log_sha256': sha(raw),
            'log_representation': 'Complete UTF-8 job text as returned by GitHub connector; BOM and newlines retained',
            'no_utf8_replacement_characters': True, 'checkout_raw_line': raw_lines[checkout_indices[0] + 1],
            'variants': guest, 'guest_checks': 852, 'invalid_configurations': invalid,
            'pci_ufs_cases': pci, 'shared_pci_ufs_case_count': 17,
            'executed_final_verification': verified[0], 'runtime_failure_or_skip_markers': forbidden}


def main():
    out = ROOT / 'spss-3bd583f-a77-rawlog-audit-001.json'
    result = {'schema': 'tb371fc-spss-a77-rawlog-observation-v1', 'passed': False,
              'auditor_sha256': sha(Path(__file__).read_bytes()), 'source_head': HEAD,
              'runs': [], 'scope': 'Bounded raw job-log evidence, not a re-execution or a full CPU artifact audit.'}
    try:
        head = json.loads((ROOT / f'git-commit-{HEAD}.json').read_text())
        merge = json.loads((ROOT / f'git-commit-{MERGE}.json').read_text())
        require(head['tree']['sha'] == merge['tree']['sha'], 'PR merge tree differs from qualified head')
        require([p['sha'] for p in merge['parents']] ==
                ['7372681f546a7ac1b816377480601ddd8e7c16f5', HEAD], 'PR merge parents differ')
        result['matching_push_pr_tree'] = head['tree']['sha']
        result['runs'].append(observe(45513996787, 138868602626, 'push', HEAD))
        result['runs'].append(observe(45514004265, 138868625818, 'pull_request', MERGE))
        result['source_files'] = []
        for path, expected in [(ROOT / 'test_a77_cpu.py', '1969cdb5553abfac6e7647a2543537a8fda7482f92b370646c79b19777938489'),
                               (ROOT / 'run_ufs_pci_compat.py', '4be8b5fbc9f9a7bd21cf19c317f261eae2fb7a3919ffe80e5adb6faade1061c2'),
                               (Path('/dev/shm/spss-ci-review/tb371fc-workflow.yml'), '8e041c6d627801d0f3de2523bcdf6057ad4c3b90e58696b0aa9ccebd73f1daab')]:
            raw = path.read_bytes()
            require(sha(raw) == expected, 'Reviewed source bytes changed')
            result['source_files'].append({'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)})
        result['limitations'] = [
            '852 is independently summed from nine actual runner output lines; individual guest PASS labels reside in the separate CPU artifact and were not downloaded here.',
            'Both invalid configurations have actual runner rejection lines; the job log does not include their complete captured stderr.',
            'This does not qualify the board/Linux job, physical cache capacities, timing, DSU, performance or original Android boot.'
        ]
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    with out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'passed': result['passed'], 'receipt': str(out),
                      'receipt_bytes': out.stat().st_size, 'receipt_sha256': sha(out.read_bytes()),
                      'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
