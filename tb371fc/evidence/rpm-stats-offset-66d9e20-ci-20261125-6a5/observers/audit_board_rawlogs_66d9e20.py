#!/usr/bin/env python3
"""Read-only count/identity observation of the two exact RPM statistics CI board logs.

This does not execute QEMU and does not replace the independent artifact audits.
"""
import argparse
import datetime
import hashlib
import json
import re
import zipfile
from pathlib import Path
ROOT = Path('/dev/shm/rpmstats-ci-review')
HEAD = '66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b'
MERGE = 'c7ba198d6bf9bd5e3255e207d07efd85a026a1a9'
TREE = '7c13a3b85075e0eaefcc14d4f55c160bed699a08'
BASE = '8d0bc64d8da6da18bf43b52a2a9547f28f44c3e9'
JOBS = {'push': (45533031476, 138932791819, HEAD), 'pr': (45533037101, 138932812241, MERGE)}
WORKFLOW_SHA = '6f0545c1893f7b0914c4ab5b90e6f43cb90cfa8abc708a2edb12b51fae2e3be0'
HOST_COMMANDS = ['python3 tb371fc/scripts/test_source_download.py', 'python3 tb371fc/scripts/test_ci_device_tests.py', 'python3 tb371fc/tests/test_android_boot_inputs.py', 'python3 tb371fc/tests/test_boot_android.py', 'python3 tb371fc/tests/test_acquire_block_ranges.py']

def require(value, message):
    if not value:
        raise AssertionError(message)

def pin(path):
    data = path.read_bytes()
    return {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

def decode_logs(path):
    data = path.read_bytes()
    text = data.decode('utf-8', errors='strict')
    require('�' not in text, 'replacement characters in raw log')
    return [re.sub('^\\ufeff?\\d{4}-\\d\\d-\\d\\dT\\d\\d:\\d\\d:\\d\\d\\.\\d+Z ', '', line) for line in text.splitlines()]

def json_objects(lines):
    output = []
    decoder = json.JSONDecoder()
    for i, line in enumerate(lines):
        if line != '{':
            continue
        try:
            value, _ = decoder.raw_decode('\n'.join(lines[i:]))
        except ValueError:
            continue
        if isinstance(value, dict):
            output.append((i + 1, value))
    return output

def host_suites(lines):
    starts = [i for i, line in enumerate(lines) if line.startswith('##[group]Run ') and (HOST_COMMANDS[0] in line or HOST_COMMANDS[0] in '\n'.join(lines[i:i + 15]))]
    require(len(starts) == 1, 'unique host validation command group')
    start = starts[0]
    command_end = next((i for i in range(start + 1, len(lines)) if lines[i] == '##[endgroup]'))
    command_text = '\n'.join(lines[start:command_end])
    previous = -1
    for command in HOST_COMMANDS:
        position = command_text.find(command)
        require(position > previous, 'ordered host workflow commands')
        previous = position
    stop = next((i for i in range(command_end + 1, len(lines)) if lines[i].startswith('##[group]Run ')))
    observed = []
    for i in range(command_end + 1, stop):
        match = re.fullmatch('Ran (\\d+) tests in ([0-9.]+)s', lines[i])
        if match:
            following = [line for line in lines[i + 1:stop] if line]
            require(following and following[0] == 'OK', 'host unittest OK follows count')
            observed.append({'tests': int(match[1]), 'elapsed_seconds': float(match[2]), 'verdict': 'OK', 'line_number': i + 1})
    require([item['tests'] for item in observed] == [15, 35, 28, 43, 16, 15], 'all six host suite actual counts and order')
    for item, name in zip(observed[:2], ['source_download', 'ci_device_tests']):
        item['suite'] = name
        item['command'] = HOST_COMMANDS[0 if name == 'source_download' else 1]
    require(not any((re.match('^(?:FAILED\\b|ERROR\\b|FAIL\\b|SKIP\\b|##\\[error\\])', line) for line in lines[command_end + 1:stop])), 'host suite failure marker')
    return {'scope': 'Actual board step 4 raw log; additional host suites, outside the 97/5 gate counts.', 'step_number': 4, 'first_two_suites': observed[:2], 'all_host_unittest_counts_in_order': [item['tests'] for item in observed]}

def ufs_report(line_number, report, suspended):
    for key in ['passed', 'inputs_unchanged', 'source_exited_before_target_started']:
        require(report[key] is True, 'UFS report true ' + key)
    for key in ['runtime_suspended_migration_requested', 'runtime_suspended_migration_tested']:
        require(report[key] is suspended, 'UFS suspend mode ' + key)
    for key in ['guest_shutdown_tested', 'inflight_ufs_migration_tested', 'external_disk_bytes_migrated', 'system_suspend_tested']:
        require(report[key] is False, 'UFS bounded scope ' + key)
    source, target = (report['guests']['source'], report['guests']['target'])
    require(source['exit_code'] == target['exit_code'] == 0, 'UFS exit codes')
    require(source['exited_monotonic'] < target['started_monotonic'], 'UFS sequential guests')
    expected_counts = (34, 27) if suspended else (21, 18)
    for role, guest, count in [('source', source, expected_counts[0]), ('target', target, expected_counts[1])]:
        checks = guest['guest_checks']
        require(len(checks) == count, 'UFS check count ' + role)
        require(all((isinstance(check, list) and len(check) == 2 and (check[0] == 'PASS') for check in checks)), 'UFS actual PASS ' + role)
        names = [check[1] for check in checks]
        expected = report['expected_' + role + '_checks']
        require(len(set(names)) == count and len(expected) == count and (set(names) == set(expected)), 'UFS expected names ' + role)
    require(source['guest_verdicts'] == [], 'UFS source has no final target verdict')
    require(len(target['guest_verdicts']) == 1, 'UFS one final target verdict')
    verdict = target['guest_verdicts'][0]
    total = sum(expected_counts)
    require(len(verdict) == 5 and verdict[0] == 'PASS' and (verdict[1] == report['nonce']) and (re.fullmatch('[0-9a-f-]{36}', verdict[2]) is not None) and (verdict[3:] == [str(total), '0']), 'UFS target final verdict')
    migration_sha = report['migration_sha256']
    require(re.fullmatch('[0-9a-f]{64}', migration_sha) is not None and migration_sha == report['migration_sha256_before_load'] == report['migration_sha256_after_load'], 'UFS migration hash preservation')
    return {'mode': 'runtime-suspended' if suspended else 'active', 'line_number': line_number, 'passed': True, 'source_checks': expected_counts[0], 'target_checks': expected_counts[1], 'guest_total': total, 'all_actual_guest_check_verdicts_PASS': True, 'names_match_report_expected_lists': True, 'source_exit': 0, 'target_exit': 0, 'source_exited_before_target_started': True, 'final_guest_verdict': 'PASS', 'inputs_unchanged': True, 'runtime_suspended_migration_tested': suspended, 'system_suspend_tested': False, 'inflight_ufs_migration_tested': False, 'external_disk_bytes_migrated': False, 'qemu_sha256': report['qemu_sha256'], 'reported_migration_sha256': migration_sha}

def audit_run(role, metadata):
    run_id, job_id, checkout = JOBS[role]
    run = metadata[role]['run']
    require(run['id'] == run_id and run['head_sha'] == HEAD and (run['run_attempt'] == 1), role + ' exact run identity')
    require(run['status'] == 'completed' and run['conclusion'] == 'success', role + ' terminal run')
    require(run['event'] == ('push' if role == 'push' else 'pull_request'), role + ' event')
    jobs = metadata[role]['jobs']['jobs']
    selected = [job for job in jobs if job['id'] == job_id]
    require(len(selected) == 1, role + ' unique board job')
    job = selected[0]
    require(job['status'] == 'completed' and job['conclusion'] == 'success', role + ' terminal board')
    require(all((step['status'] == 'completed' and step['conclusion'] == 'success' for step in job['steps'])), role + ' all board steps success')
    path = ROOT / f'rpmstats-66d9e20-{role}-board-{job_id}.log'
    lines = decode_logs(path)
    checkout_indices = [i for i, line in enumerate(lines) if line == '[command]/usr/bin/git log -1 --format=%H']
    require(len(checkout_indices) == 1 and lines[checkout_indices[0] + 1] == checkout, role + ' actual checkout')
    forbidden = [line for line in lines if re.match('^(?:FAIL\\b|SKIP\\b|Bail out!|##\\[error\\])', line)]
    require(not forbidden, role + ' no failure/skip markers')
    pass_names = [match[1] for line in lines if (match := re.fullmatch('PASS ([a-z0-9-]+)', line))]
    require(len(pass_names) == len(set(pass_names)) == 102, role + ' 102 distinct PASS lines')
    categories = {97: [], 5: []}
    for i, line in enumerate(lines):
        match = re.fullmatch('\\[(\\d+)/(97|5)\\] ([a-z0-9-]+)', line)
        if match:
            index, count, name = (int(match[1]), int(match[2]), match[3])
            require(lines[i + 1] == 'PASS ' + name, role + ' progress PASS adjacency')
            categories[count].append((index, name))
    for count, values in categories.items():
        require([i for i, _ in values] == list(range(1, count + 1)), role + ' ordered progress counts')
    require(set(pass_names) == {name for values in categories.values() for _, name in values}, role + ' progress/PASS exact correspondence')
    objects = json_objects(lines)
    summaries = [value for _, value in objects if 'passed_gates' in value]
    require(len(summaries) == 2, role + ' two gate summaries')
    for summary, count in zip(summaries, [97, 5]):
        require(summary['passed'] is True and summary['qemu_unchanged'] is True and (summary['passed_gates'] == summary['expected_gates'] == count), role + ' gate summary')
    extra = [(line, value) for line, value in objects if 'runtime_suspended_migration_requested' in value]
    require(len(extra) == 2, role + ' two extra UFS reports')
    reports = [ufs_report(line, value, suspended) for (line, value), suspended in zip(extra, [False, True])]
    require(reports[0]['qemu_sha256'] == reports[1]['qemu_sha256'], role + ' same extra-UFS binary')
    return {'role': role, 'run_id': run_id, 'run_attempt': 1, 'job_id': job_id, 'completed_success': True, 'actual_checkout': checkout, 'component_gates': 97, 'linux_gates': 5, 'extra_ufs_checks': [39, 61], 'raw_log': pin(path), 'component_summary': summaries[0], 'linux_summary': summaries[1], 'distinct_PASS_gate_lines': 102, 'FAIL_gate_lines': 0, 'component_gate_names': [name for _, name in categories[97]], 'linux_gate_names': [name for _, name in categories[5]], 'extra_ufs_reports': reports, 'host_unit_suites': host_suites(lines)}

def audit_push_unit(args):
    path = Path(args.archive)
    archive_pin = pin(path)
    require(archive_pin['bytes'] == args.archive_bytes and archive_pin['sha256'] == args.archive_sha256, 'exact push archive identity')
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'unique ZIP members')
        cases = [name for name in names if re.search('(?:^|/)components/\\d+-ci-verdict-unit/case\\.json$', name)]
        require(len(cases) == 1, 'one CI unit case')

        def small(name):
            require(archive.getinfo(name).file_size <= 4 * 1024 * 1024, 'bounded ZIP member')
            return archive.read(name)
        case_name = cases[0]
        case_raw = small(case_name)
        case = json.loads(case_raw)
        require(case['passed'] is True and case['exit_code'] == 0 and (case['name'] == 'ci-verdict-unit'), 'CI unit case passed')
        folder = case_name.rsplit('/', 1)[0] + '/'
        stderr, stdout = (small(folder + 'stderr.log'), small(folder + 'stdout.log'))
        require(hashlib.sha256(stderr).hexdigest() == case['log_sha256']['stderr.log'] and hashlib.sha256(stdout).hexdigest() == case['log_sha256']['stdout.log'], 'CI unit log hashes')
        text = stderr.decode('utf-8', errors='strict')
        counts = re.findall('^Ran (\\d+) tests in ([0-9.]+)s$', text, re.M)
        require(len(counts) == 1 and counts[0][0] == '35' and (len(re.findall('^OK$', text, re.M)) == 1), 'CI unit actual 35 OK')
        require('FAILED' not in text and 'skipped=' not in text, 'CI unit no failed or skipped tests')
        prefix = case_name.split('/components/')[0] + '/'
        require(small(prefix + 'source-commit.txt').decode().strip() == HEAD, 'archived source exact head')
        qhash = small(prefix + 'qemu.sha256').decode().split()[0]
        require(qhash == args.qemu_sha256, 'archived QEMU SHA')
    return {'scope': 'Exact push ZIP CI unit count; PR artifact was not downloaded. Both host job logs separately show 35 tests.', 'run_id': JOBS['push'][0], 'artifact_id': args.artifact_id, 'archive': dict(archive_pin, downloaded_by_this_reviewer=False), 'case_member': case_name, 'case_sha256': hashlib.sha256(case_raw).hexdigest(), 'passed': True, 'exit_code': 0, 'unittest_count': 35, 'unittest_verdict': 'OK', 'elapsed_seconds': float(counts[0][1]), 'stderr_bytes': len(stderr), 'stderr_sha256': hashlib.sha256(stderr).hexdigest(), 'stdout_bytes': len(stdout), 'stdout_sha256': hashlib.sha256(stdout).hexdigest(), 'archived_source_head': HEAD, 'archived_qemu_sha256': qhash, 'binary_stream_not_rehashed_by_this_count_observer': True}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', required=True)
    parser.add_argument('--archive-bytes', type=int, required=True)
    parser.add_argument('--archive-sha256', required=True)
    parser.add_argument('--artifact-id', type=int, required=True)
    parser.add_argument('--qemu-sha256', required=True)
    args = parser.parse_args()
    out = ROOT / 'rpmstats-66d9e20-board-rawlog-observation-001.json'
    receipt = {'schema': 'tb371fc-rpm-stats-offset-board-rawlog-observation-v2', 'passed': False, 'source_head': HEAD, 'matching_push_pr_tree': TREE, 'observed_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'observer': pin(Path(__file__))}
    try:
        metadata_path = ROOT / 'rpmstats-66d9e20-final-ci-github-metadata.json'
        metadata = json.loads(metadata_path.read_bytes())
        require(metadata['head']['sha'] == HEAD and metadata['head']['tree']['sha'] == TREE, 'source commit and tree metadata')
        require(metadata['merge']['sha'] == MERGE and metadata['merge']['tree']['sha'] == TREE and ([item['sha'] for item in metadata['merge']['parents']] == [BASE, HEAD]), 'PR merge commit tree and parents')
        workflow = ROOT / 'source/.github/workflows/tb371fc.yml'
        require(pin(workflow)['sha256'] == WORKFLOW_SHA, 'exact workflow pin')
        receipt['github_metadata'] = pin(metadata_path)
        receipt['workflow'] = pin(workflow)
        receipt['runs'] = [audit_run(role, metadata) for role in ['push', 'pr']]
        receipt['push_unit'] = audit_push_unit(args)
        receipt['scope'] = {'read_only_logs_and_small_archive_members': True, 'qemu_executed': False, 'device_rpc': False, 'whole_artifact_qualified': False, 'original_android_native_executed': False, 'PR_artifact_unit_count_directly_observed': False, 'PR_host_unit_count_directly_observed': True, 'PR_unit_observation_scope': 'Host validation job log only; not PR artifact.', 'mutable_PR_mergeability_not_claimed': True}
        receipt['passed'] = True
    except Exception as error:
        receipt['error'] = repr(error)
    with out.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(json.dumps({'passed': receipt['passed'], 'receipt': pin(out), 'error': receipt.get('error')}))
    return 0 if receipt['passed'] else 1
if __name__ == '__main__':
    raise SystemExit(main())
