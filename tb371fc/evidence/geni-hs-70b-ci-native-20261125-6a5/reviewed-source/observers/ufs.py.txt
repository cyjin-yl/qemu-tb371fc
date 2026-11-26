#!/usr/bin/env python3
"""Read only the two extra Linux UFS continuation receipts and actual logs.

The workflow excludes disk.raw and checkpoint.migration. Their recorded hashes
and full-media comparison receipts are checked, never described as rehashed
archive bytes or a decoded migration payload. This script never starts QEMU.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import traceback
from zipfile import ZipFile

SOURCE = ('procfs', 'sysfs', 'devtmpfs', 'eight_online_cpus', 'qcom_host_driver',
          'qmp_phy_driver', 'gcc_driver', 'apps_smmu_driver',
          'ufs_translated_dma_domain_before', 'ufs_block_discovered',
          'logical_sector_4096_before', 'capacity_64_mib_before', 'aligned_buffers',
          'source_direct_write', 'source_flush', 'source_direct_readback',
          'source_close', 'source_boot_id', 'source_monotonic', 'control_tty_raw',
          'stored_host_nonce')
TARGET = ('resume_control_received', 'preserved_pid1', 'preserved_boot_id',
          'preserved_monotonic', 'eight_online_cpus_after',
          'ufs_translated_dma_domain_after', 'target_open',
          'logical_sector_4096_after', 'capacity_64_mib_after',
          'target_reads_source_payload', 'target_direct_write', 'target_flush',
          'target_direct_readback', 'target_close', 'target_reopen',
          'target_reopen_source_payload', 'target_reopen_target_payload',
          'target_final_close')
PM_SOURCE = ('pm_source_reopen', 'pm_source_block_identity', 'pm_host_control_on',
             'pm_lun_control_on', 'pm_host_active', 'pm_lun_active',
             'pm_target_hibern8', 'pm_scsi_autosuspend', 'pm_host_control_auto',
             'pm_source_host_suspended', 'pm_source_lun_suspended',
             'pm_source_wlun_suspended', 'pm_source_stays_suspended')
PM_TARGET = ('pm_target_host_suspended', 'pm_target_lun_suspended',
             'pm_target_wlun_suspended', 'pm_suspended_time_preserved',
             'pm_open_fd_preserved', 'pm_direct_read_wakes', 'pm_target_host_active',
             'pm_target_lun_active', 'pm_retained_fd_close')
EXPECTED_PINS = {
    'guest_source': '34ee8da7e1a44dbbee5bb8d0205307e66d8a6fd01dacd3e877590688c2bba419',
    'harness': '6491533155b69a2dc21ff1d9a7c92dfc9c51bba15e8cb5a078bcd0e2df9cc792',
    'boot_smoke_module': '4889f2cdd2e3fd3331cc57f3bf1a6b6ed8894f87503283c7f404766919540f7d',
    'qmp_module': 'e2cc9d8e671c82a77e34b22f4261c3c4c73a65baf68a0aad86aa2a2d92926106',
    'runtime_pm_module': 'c62bb0b377794033e1bd81ea0acccdd7b7eb8bfe641ebd5ae593cd439a8e55cd',
}
PHASES = ('source_baseline', 'source_suspended_running', 'source_suspended_paused',
          'target_loaded_paused', 'target_running_before_io', 'target_after_io')


def need(condition, label):
    if not condition:
        raise ValueError(label)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


class Archive:
    def __init__(self, path):
        self.zip = ZipFile(path)
        self.members = {i.filename: i for i in self.zip.infolist()}
        need(len(self.members) == len(self.zip.infolist()), 'Duplicate ZIP member')
        need(all(not PurePosixPath(n).is_absolute() and '..' not in PurePosixPath(n).parts
                 for n in self.members), 'Unsafe ZIP member name')
        self.observed = {}

    def read(self, name, limit=64 * 1024 * 1024):
        item = self.members[name]
        need(item.file_size <= limit, 'Selected member exceeds bound: ' + name)
        raw = self.zip.read(item)
        need(len(raw) == item.file_size, 'Selected ZIP member length mismatch')
        self.observed[name] = {'bytes': len(raw), 'sha256': sha(raw)}
        return raw

    def digest(self, name):
        item = self.members[name]
        count, h = 0, hashlib.sha256()
        with self.zip.open(item) as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                count += len(block)
                h.update(block)
        need(count == item.file_size, 'Streaming ZIP member length mismatch')
        self.observed[name] = {'bytes': count, 'sha256': h.hexdigest()}
        return self.observed[name]

    def json(self, name):
        return json.loads(self.read(name))


def qmp_pairs(items):
    result, pending, events = [], None, []
    for entry in items:
        if entry['direction'] == 'send':
            need(pending is None, 'Concurrent/unanswered QMP command')
            pending = entry
        else:
            need(entry['direction'] == 'receive', 'Unknown QMP direction')
            message = entry['message']
            if 'event' in message:
                events.append(message)
            elif 'QMP' in message:
                need(pending is None and not result, 'Late QMP greeting')
            else:
                need(pending is not None and 'return' in message and 'error' not in message,
                     'QMP error or unexpected response')
                need(message['id'] == len(result) + 1, 'Unexpected QMP response ID')
                result.append({'command': pending['command'], 'arguments': pending['arguments'],
                               'return': message['return']})
                pending = None
    need(pending is None, 'Unanswered final QMP command')
    need(result and result[-1]['command'] == 'quit' and result[-1]['return'] == {},
         'No acknowledged final host QMP quit')
    shutdown = [e for e in events if e.get('event') == 'SHUTDOWN']
    need(len(shutdown) == 1 and shutdown[0]['data'] ==
         {'guest': False, 'reason': 'host-qmp-quit'}, 'Shutdown is not own host QMP quit')
    return result


def full_media_receipt(value, final):
    need(value['passed'] is True and value['size_bytes'] == value['bytes_compared'] == 67108864,
         'Missing full 64 MiB comparison receipt')
    need(value['first_mismatch_offset'] is None and value['all_other_bytes_expected'] == 0,
         'Media comparison mismatch')
    expected = ('ffa6c370e3673bc23fe97c8f1c54be181d9bacb54999a44317cd5cd7f47376b4'
                if final else 'cd8fa520c10bf1662c14b1289d4cad803758599aa546452592319d5a9e1b93cd')
    need(value['sha256'] == value['expected_sha256'] == expected, 'Media comparison digest differs')
    payloads = [{'offset': 4194304, 'length': 32768,
                 'sha256': 'faf3511fec2180665df534811d7ed01fdf14127f1d0596c46b34fb9ba6ee466e'}]
    if final:
        payloads.append({'offset': 8388608, 'length': 32768,
                         'sha256': '3bc36b53bcd42aefb59c22a24a423f300ab98f3947656909c1eaa1a6a697d6e7'})
    need(value['payloads'] == payloads, 'Media payload offsets/digests differ')


def observe_case(archive, case, suspended, binary, kernel, build):
    root = 'tb371fc-results/' + case + '/'
    report = archive.json(root + 'result.json')
    need(report['passed'] is True and report['schema_version'] == 1, 'UFS report failed')
    need(not any(k in report for k in ('error', 'traceback', 'timed_out')), 'Incomplete UFS report')
    need(report.get('kernel_failure_lines', []) == [], 'Kernel failure receipt present')
    for key in ('guest_shutdown_tested', 'inflight_ufs_migration_tested',
                'external_disk_bytes_migrated', 'system_suspend_tested'):
        need(report[key] is False, 'Unexpected scope: ' + key)
    for key in ('runtime_suspended_migration_requested', 'runtime_suspended_migration_tested'):
        need(report[key] is suspended, 'Wrong runtime PM mode')
    source_names = SOURCE + PM_SOURCE if suspended else SOURCE
    target_names = TARGET[:6] + PM_TARGET + TARGET[6:] if suspended else TARGET
    need(report['expected_source_checks'] == list(source_names) and
         report['expected_target_checks'] == list(target_names), 'Declared guest check names differ')
    checks = len(source_names) + len(target_names)
    need(checks == (61 if suspended else 39), 'Wrong guest check count')
    need(report['qemu_sha256'] == binary['sha256'] and report['qemu_size_bytes'] == binary['bytes'],
         'UFS actual QEMU binding mismatch')
    need(report['kernel_sha256'] == kernel['sha256'] and report['kernel_size_bytes'] == kernel['bytes'],
         'UFS actual Linux binding mismatch')
    need(report['kernel_build_receipt'] == build, 'Embedded Linux build receipt differs')
    need(report['kernel_build_receipt_sha256'] == archive.observed['clean-linux/artifacts/build.json']['sha256'],
         'Linux build receipt hash differs')
    need(report['inputs_unchanged'] is True, 'Inputs changed during test')
    for name, before in report['input_sha256_before'].items():
        need(report[name + '_sha256'] == before, 'Input hash changed: ' + name)
    for name, expected in EXPECTED_PINS.items():
        if name == 'runtime_pm_module' and not suspended:
            continue
        need(report[name + '_sha256'] == expected, 'Previously reviewed harness/source pin changed: ' + name)
    for name, member in [('init', 'init'), ('initramfs', 'initramfs.cpio.gz')]:
        actual = archive.digest(root + member)
        need(actual == {'bytes': report[name + '_size_bytes'], 'sha256': report[name + '_sha256']},
             'Available input bytes do not match receipt: ' + member)
    if suspended:
        config = archive.digest('clean-linux/artifacts/kernel.config')
        need(config == {'bytes': report['kernel_config_size_bytes'], 'sha256': report['kernel_config_sha256']},
             'Actual Linux config binding differs')
    processes, pairs, raw_serial = {}, {}, {}
    for who, names in [('source', source_names), ('target', target_names)]:
        process = archive.json(root + who + '/receipt.json')
        need(report['guests'][who] == process, 'Per-process receipt differs from aggregate')
        need(process['exit_code'] == 0 and process['exited_monotonic'] > process['started_monotonic'],
             'Process did not exit cleanly')
        serial_bytes = archive.read(root + who + '/serial.log')
        debug_bytes = archive.read(root + who + '/qemu-debug.log')
        need(sha(serial_bytes) == process['serial.log_sha256'] and
             sha(debug_bytes) == process['qemu-debug.log_sha256'], 'Actual raw log digest differs')
        serial = serial_bytes.decode('utf-8')
        need(not re.search(r'Kernel panic|Internal error:|Unable to handle kernel|TB371FC_UFS_MIGRATION_CHECK FAIL', serial),
             'Raw serial contains a failure')
        observed = re.findall(r'^TB371FC_UFS_MIGRATION_CHECK (PASS|FAIL) (\S+)\r?$', serial, re.M)
        need(observed == [('PASS', n) for n in names], 'Actual serial PASS names/count/order differ')
        need(process['guest_checks'] == [list(x) for x in observed], 'Guest check receipt does not match serial')
        commands = process['command']
        for flag, value in [('-M', 'tb371fc'), ('-m', '8G'), ('-smp', '8')]:
            need(commands.count(flag) == 1 and commands[commands.index(flag) + 1] == value,
                 'Wrong real Linux guest topology')
        pairs[who] = qmp_pairs(process['qmp'])
        need(process['migration']['status'] == 'completed', 'Migration did not complete')
        need(any(p['command'] == 'query-migrate' and p['return'] == process['migration']
                 for p in pairs[who]), 'Completed migration lacks matching raw QMP response')
        migration_command = 'migrate' if who == 'source' else 'migrate-incoming'
        match = [p for p in pairs[who] if p['command'] == migration_command]
        need(len(match) == 1 and match[0]['arguments']['uri'].endswith('/' + case + '/checkpoint.migration'),
             'Wrong actual migration command/path')
        processes[who] = {'pid': process['pid'], 'started_monotonic': process['started_monotonic'],
                          'exited_monotonic': process['exited_monotonic'], 'exit_code': 0,
                          'serial_sha256': sha(serial_bytes), 'serial_bytes': len(serial_bytes),
                          'actual_pass_names': list(names), 'qmp_completed': True,
                          'qmp_request_count': len(pairs[who]), 'own_host_qmp_quit_ack_and_shutdown': True}
        raw_serial[who] = serial
    source, target = report['guests']['source'], report['guests']['target']
    need(source['pid'] != target['pid'] and source['exited_monotonic'] < target['started_monotonic'],
         'Source/target processes overlap or PID reused')
    need(report['source_exited_before_target_started'] is True, 'Process-order receipt is false')
    checkpoint = re.findall(r'^TB371FC_UFS_MIGRATION_CHECKPOINT nonce=([0-9a-f]+) boot_id=([0-9a-f-]+) monotonic_ns=([0-9]+) checks=([0-9]+) failures=([0-9]+)\r?$', raw_serial['source'], re.M)
    need(len(checkpoint) == 1 and [list(x) for x in checkpoint] == report['checkpoint_markers'],
         'Actual source checkpoint marker differs')
    nonce, boot_id, monotonic, source_count, failures = checkpoint[0]
    need(nonce == report['nonce'] and int(monotonic) > 0 and int(source_count) == len(source_names) and failures == '0',
         'Invalid source nonce/checkpoint verdict')
    final = re.findall(r'^TB371FC_UFS_MIGRATION_RESULT (PASS|FAIL) nonce=([0-9a-f]+) boot_id=([0-9a-f-]+) checks=([0-9]+) failures=([0-9]+)\r?$', raw_serial['target'], re.M)
    need(final == [('PASS', nonce, boot_id, str(checks), '0')] and
         target['guest_verdicts'] == [list(x) for x in final] and source['guest_verdicts'] == [],
         'Target does not preserve actual PID1 boot/nonce verdict')
    need(source['serial_input'] == ['STORE:' + nonce] and target['serial_input'] == ['CONTINUE'],
         'Wrong host serial control sequence')
    need(source['stopped_status'] == {'status': 'paused', 'running': False} and
         source['postmigration_status'] == {'status': 'postmigrate', 'running': False} and
         target['loaded_status'] == {'status': 'paused', 'running': False}, 'Wrong migration stop/restore state')
    target_commands = [p['command'] for p in pairs['target']]
    need(target_commands.count('cont') == 1 and
         target_commands.index('migrate-incoming') < target_commands.index('cont'), 'Wrong target continuation order')
    completed_index = next(i for i,p in enumerate(pairs['target'])
                           if p['command'] == 'query-migrate' and p['return'].get('status') == 'completed')
    need(completed_index < target_commands.index('cont'), 'Target continued before completed incoming')
    for final_disk in (False, True):
        full_media_receipt(report['target_disk' if final_disk else 'source_disk'], final_disk)
    need(report['disk_size_bytes'] == 67108864 and report['disk_sha256'] == report['target_disk']['sha256'],
         'Final media receipt differs')
    need(report['migration_sha256_before_load'] == report['migration_sha256_after_load'] == report['migration_sha256'] and
         re.fullmatch(r'[0-9a-f]{64}', report['migration_sha256']), 'Recorded migration identity changed')
    need(source['migration']['ram']['transferred'] == report['migration_size_bytes'] and
         source['migration']['ram']['remaining'] == 0, 'Raw QMP transfer does not match recorded stream size')
    for omitted in ('checkpoint.migration', 'disk.raw'):
        need(root + omitted not in archive.members, 'Workflow exclusion boundary unexpectedly changed')
    pm_observations = []
    observations = report['host_observations']
    if not suspended:
        need(observations == [], 'Unexpected PM observations in ordinary mode')
    else:
        need(report['runtime_pm_state_preserved'] is True and [o['phase'] for o in observations] == list(PHASES),
             'Six exact PM phases are missing')
        for who, subset in [('source', observations[:3]), ('target', observations[3:])]:
            records = pairs[who]
            indices = [i for i,p in enumerate(records) if p['command'] == 'human-monitor-command' and
                       p['arguments'] == {'command-line': 'info qtree'}]
            need(len(indices) == 3, 'Unexpected number of raw PM qtree replies')
            for item, index in zip(subset, indices):
                need(index >= 2, 'Missing PM query prefix')
                status, link, tree = records[index - 2:index + 1]
                need(status['command'] == 'query-status' and status['return'] == item['vm'], 'Raw PM status differs')
                need(link['command'] == 'qom-get' and link['arguments'] ==
                     {'path': '/machine/ufs', 'property': 'link-state'} and link['return'] == item['link_state'],
                     'Raw PM link-state differs')
                raw_tree = tree['return']
                need(sha(raw_tree.encode('utf-8')) == item['qtree_sha256'] and
                     item['qtree_raw_in_qmp_receipts'] is True, 'Raw CRLF qtree hash differs')
                block, tree_lines = item['ufs_qtree_block'], raw_tree.splitlines()
                matches = [i for i in range(len(tree_lines)-len(block)+1) if tree_lines[i:i+len(block)] == block]
                need(len(matches) == 1 and block[0] == '  dev: qcom-sm8250-ufs, id "ufs0"',
                     'Saved UFS block is not an exact contiguous raw qtree block')
                active = item['phase'] in ('source_baseline', 'target_after_io')
                paused = item['phase'] in ('source_suspended_paused', 'target_loaded_paused')
                need(item['link_state'] == ('active' if active else 'hibern8') and
                     item['vm'] == {'status': 'paused' if paused else 'running', 'running': not paused},
                     'Wrong PM phase state')
                expected_clocks = {'core-clk': '37.5 MHz' if active else '0 Hz',
                                   'iface-clk': '100 MHz' if active else '0 Hz',
                                   'unipro-clk': '37.5 MHz' if active else '0 Hz'}
                for name, value in expected_clocks.items():
                    need(item['clock_frequency_display'][name] == value and
                         block.count(f'    clock-in "{name}" freq_hz={value}') == 1,
                         'Raw PM clock does not match required gated/active state')
                pm_observations.append({'phase': item['phase'], 'vm': item['vm'], 'link_state': item['link_state'],
                                        'clocks': expected_clocks, 'qtree_sha256': item['qtree_sha256'],
                                        'raw_qtree_crlf_count': raw_tree.count('\r\n'),
                                        'exact_contiguous_block_lines': len(block), 'actual_raw_qmp_correlated': True})
    return {'name': case, 'passed': True, 'check_count': checks,
            'source_checks': len(source_names), 'target_checks': len(target_names),
            'same_pid1_boot_nonce_verified': True, 'nonce': nonce, 'boot_id': boot_id,
            'source_monotonic_guest_ns': int(monotonic),
            'source_exit_before_target_start_seconds': target['started_monotonic']-source['exited_monotonic'],
            'processes': processes, 'source_disk_comparison_receipt': report['source_disk'],
            'target_disk_comparison_receipt': report['target_disk'],
            'recorded_migration_bytes': report['migration_size_bytes'],
            'recorded_migration_sha256': report['migration_sha256'],
            'qmp_transferred_matches_recorded_migration_bytes': True,
            'migration_archive_bytes_available': False, 'disk_archive_bytes_available': False,
            'runtime_pm_observations': pm_observations}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--zip', type=Path, required=True)
    p.add_argument('--expected-head', required=True)
    p.add_argument('--expected-zip-sha256', required=True)
    p.add_argument('--expected-qemu-sha256', required=True)
    p.add_argument('--run-id', type=int, required=True)
    p.add_argument('--artifact-id', type=int, required=True)
    p.add_argument('--out', type=Path, required=True)
    args = p.parse_args()
    need(not args.out.exists(), 'Receipt must be a new file')
    result = {'schema': 'tb371fc-extra-linux-ufs-artifact-observation-v2', 'passed': False,
              'started_utc': datetime.now(timezone.utc).isoformat(),
              'auditor_sha256': file_hash(__file__), 'head': args.expected_head,
              'run_id': args.run_id, 'artifact_id': args.artifact_id,
              'qemu_executed_by_this_review': False, 'guest_executed_by_this_review': False,
              'read_only_archive': True, 'extracted_members': 0, 'cases': []}
    archive = None
    try:
        need(re.fullmatch('[0-9a-f]{40}', args.expected_head) and
             re.fullmatch('[0-9a-f]{64}', args.expected_zip_sha256) and
             re.fullmatch('[0-9a-f]{64}', args.expected_qemu_sha256), 'Invalid required identity')
        need(file_hash(args.zip) == args.expected_zip_sha256, 'Actual complete ZIP digest differs')
        result['archive'] = {'bytes': args.zip.stat().st_size, 'sha256': args.expected_zip_sha256}
        archive = Archive(args.zip)
        need(archive.read('tb371fc-results/source-commit.txt').decode().strip() == args.expected_head,
             'Actual artifact source head differs')
        binary = archive.digest('qemu-build/qemu-system-aarch64')
        need(binary['sha256'] == args.expected_qemu_sha256, 'Actual QEMU bytes differ')
        kernel = archive.digest('clean-linux/artifacts/Image')
        build = archive.json('clean-linux/artifacts/build.json')
        need(build['image_sha256'] == kernel['sha256'] and build['image_bytes'] == kernel['bytes'] and
             build['source_version'] == '6.12.114' and build['patches_applied_by_script'] == [],
             'Clean unpatched Linux identity differs')
        result['qemu'], result['clean_linux'] = binary, kernel
        for case, suspended in [('linux-ufs-migration', False), ('linux-ufs-pm-migration', True)]:
            result['cases'].append(observe_case(archive, case, suspended, binary, kernel, build))
        result['boundary'] = {'full_migration_wire_decoded': False,
            'external_disk_bytes_rehashed_from_archive': False,
            'host_full_media_comparison_receipts_reviewed': True,
            'migration_hash_immutability_is_receipt_evidence': True,
            'source_process_ordering_is_recorded_host_monotonic_evidence': True,
            'stock_android_boot_claimed': False, 'system_suspend_tested': False,
            'inflight_ufs_migration_tested': False,
            'workflow_git_blob': 'df5d3d9bca9c5908dfda73181932e18e8a0e2173',
            'workflow_exclusion_lines': [123, 124, 125, 126],
            'workflow_excluded_members': ['tb371fc-results/' + case + '/' + file
                for case in ('linux-ufs-migration', 'linux-ufs-pm-migration')
                for file in ('checkpoint.migration', 'disk.raw')]}
        need(file_hash(args.zip) == args.expected_zip_sha256, 'ZIP changed during read-only observation')
        result['passed'] = True
    except Exception:
        result['error'] = traceback.format_exc()
    finally:
        if archive:
            result['actual_members_read'] = archive.observed
            archive.zip.close()
    result['finished_utc'] = datetime.now(timezone.utc).isoformat()
    with args.out.open('x') as output:
        json.dump(result, output, indent=2)
        output.write('\n')
    print(json.dumps({'passed': result['passed'], 'out': str(args.out),
                      'bytes': args.out.stat().st_size, 'sha256': file_hash(args.out),
                      'error': result.get('error')}))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
