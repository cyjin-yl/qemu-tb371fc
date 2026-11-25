"""Execute one explicitly bound read-only source attempt entirely in memory.

This module is sent to the existing authorized tablet process over stdin. It
opens no persistent output files, launches only the pinned plan's existing SSH argv,
and sends control stdout/stderr only to the root controller's bounded tmpfs files.
It neither discovers destinations nor retries. Root separately verifies the
host receipts and process termination before authorizing the next attempt.
"""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import stat
import subprocess
import sys
import time

DEVICE_ROOT = Path('/data/local/tmp/tb371fc-stockboot-e1a9')
SOURCE_BOOT = 'f8e0ba9a-2f6e-47c0-9e98-8ca907ec7a2f'
ORIGINAL_PLAN_SHA = 'ab1000b9b079523df78b118abee624f5173939cbbd5e6393542041f7f1c66590'
JOBS_PLAN_SHA = 'a90c1161c6cbce7d0fada8e1901354f0a58b5707e02231d206a0c1f663a1a77a'
COLLECTOR_SHA = '89bd7f0c88d4f9853b5d92e2b70cbdcb635f98fd01a1622f72ea2a5f74b4032d'
HOST_ROOT = '/tank/shared/projects/qemu-tb371fc/.git/tb371fc-support-6a5-control/storage-continuation-f093-20261125-6a5-001'
# Owner namespace stays fixed. Only new versioned filenames may be added on adoption.
EXPECTED_GEOMETRY = {'bytes': 125720068096, 'logical_block_size': 4096, 'kind': 'block-device',
                     'stat_device': 17, 'inode': 2101, 'rdev': 2048}

def need(value, message):
    if not value:
        raise ValueError(message)

def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def start_ticks(pid):
    return int(Path('/proc', str(pid), 'stat').read_text().rsplit(') ', 1)[1].split()[19])

def read_pinned(path, expected, cap):
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 < before.st_size <= 2 * 1024 * 1024, 'unsafe bound plan')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        after = os.fstat(fd)
        need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
             (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'bound plan changed while reading')
        need(len(raw) == before.st_size and hashlib.sha256(raw).hexdigest() == expected, 'bound plan SHA mismatch')
        return cap['strict_json'](raw)
    finally:
        os.close(fd)

def validate_payload(payload):
    need(isinstance(payload, dict) and set(payload) == {'schema', 'attempt_id', 'host_request', 'relay', 'job_name',
         'expected_completed_chunks', 'source_deadline_seconds', 'namespace', 'max_new_chunks'}, 'memory sender payload fields differ')
    need(payload['schema'] == 'tb371fc-storage-memory-sender-call-v2', 'wrong memory sender schema')
    need(isinstance(payload['attempt_id'], str) and re.fullmatch(r'attempt-[0-9]{6}', payload['attempt_id']), 'invalid attempt identity')
    need(type(payload['max_new_chunks']) is int and payload['max_new_chunks'] in (1, 4), 'invalid new-chunk budget')
    need(payload['namespace'] == HOST_ROOT, 'unexpected independent host namespace')
    need(isinstance(payload['job_name'], str) and re.fullmatch(r'lun0-tail-(08[1-9]|09[0-9]|10[0-5])', payload['job_name']), 'job is outside unfinished original ranges')
    need(type(payload['expected_completed_chunks']) is int and 0 <= payload['expected_completed_chunks'] <= 16, 'invalid expected prefix')
    need(type(payload['source_deadline_seconds']) is int and 1 <= payload['source_deadline_seconds'] <= 240, 'invalid source deadline')
    for name in ('host_request', 'relay'):
        pin = payload[name]
        need(isinstance(pin, dict) and set(pin) == {'path', 'bytes', 'sha256'}, 'invalid host file pin')
        need(type(pin['bytes']) is int and 0 < pin['bytes'] <= 2 * 1024 * 1024 and
             isinstance(pin['sha256'], str) and re.fullmatch('[0-9a-f]{64}', pin['sha256']), 'invalid host file identity')
    request = payload['host_request']['path']
    need(request == HOST_ROOT + '/bindings/' + payload['attempt_id'] + '-request.json',
         'host request must be the separately pinned binding; only the supervisor creates the attempt directory')
    need(payload['relay']['path'] == HOST_ROOT + '/code/ssh_stdio_relay_batch_v3.py', 'unexpected relay path')

def reject_other_collectors():
    names = {'capture_original_luns.py', 'capture_original_luns_v2.py', 'resume_lun0_rest_recovery1.py',
             'capture_generation2.py', 'acquire_block_ranges_resume_v2.py'}
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal() or int(entry.name) == os.getpid():
            continue
        try:
            argv = [os.fsdecode(value) for value in (entry / 'cmdline').read_bytes().split(b'\0') if value]
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
        observed_names = {Path(value).name for value in argv}
        standalone = {'acquire_block_ranges.py', 'acquire_block_ranges-eb1258cb.py'}
        need(not names.intersection(observed_names) and not (standalone.intersection(observed_names) and 'send' in argv),
             'another known capture controller is active')

class PrefixBoundInput:
    def __init__(self, incoming, cap, expected):
        self.incoming, self.cap, self.expected, self.lines = incoming, cap, expected, 0
    def readline(self, limit):
        raw = self.incoming.readline(limit)
        self.lines += 1
        if self.lines == 2:
            row = self.cap['strict_json'](raw)
            need(isinstance(row, dict) and isinstance(row.get('completed'), list) and
                 len(row['completed']) == self.expected, 'host prefix differs from independently bound expectation')
        return raw

def run(payload, collector_code):
    validate_payload(payload)
    need(os.geteuid() == 0, 'authorized read-only root source process required')
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == SOURCE_BOOT, 'tablet boot identity changed')
    need(len(collector_code) == 45801 and hashlib.sha256(collector_code).hexdigest() == COLLECTOR_SHA, 'collector code identity changed')
    cap = {'__name__': 'tb371fc_verified_memory_collector', '__file__': '<verified-memory-collector>'}
    exec(compile(collector_code, '<verified-memory-collector>', 'exec'), cap)
    need('ctypes' not in sys.modules, 'unexpected receiver-only dependency in sender')
    original = read_pinned(DEVICE_ROOT / 'capture-plan.json', ORIGINAL_PLAN_SHA, cap)
    jobs = read_pinned(DEVICE_ROOT / 'generation2-jobs-plan.json', JOBS_PLAN_SHA, cap)
    need(jobs['original_plan_sha256'] == ORIGINAL_PLAN_SHA and len(jobs['jobs']) == 107, 'original capture authorization differs')
    matches = [job for job in jobs['jobs'] if job['name'] == payload['job_name']]
    need(len(matches) == 1, 'original job missing or ambiguous')
    job = matches[0]
    need(job['device'] == '/dev/block/sda' and job['source_bytes'] == EXPECTED_GEOMETRY['bytes'] and
         job['expected_chunks'] >= payload['expected_completed_chunks'], 'source job binding differs')
    need(all(row['classification'] == 'live-mutable' for row in job['ranges']), 'mutable source classification changed')
    ssh = original['ssh_argv']
    need(isinstance(ssh, list) and all(isinstance(value, str) and value and '\0' not in value for value in ssh), 'invalid existing SSH argv')
    need('-oStrictHostKeyChecking=yes' in ssh and '-I1800' in ssh, 'existing SSH policy changed')
    need(original['receiver_python'] == 'python3', 'receiver interpreter differs from observed host')
    lock = os.open(DEVICE_ROOT / 'lun0-rest-recovery.lock', os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    process = None
    previous_alarm = None
    result = {'schema': 'tb371fc-storage-memory-sender-result-v2', 'attempt_id': payload['attempt_id'],
              'job_name': payload['job_name'], 'host_request_sha256': payload['host_request']['sha256'],
              'collector_sha256': COLLECTOR_SHA, 'started_utc': utc(), 'source_boot_id': SOURCE_BOOT,
              'source_pid': os.getpid(), 'source_start_ticks': start_ticks(os.getpid()),
              'source_open_mode': 'O_RDONLY', 'tablet_persistent_files_created_or_written': False,
              'tablet_block_source_written': False, 'control_output_filesystem': 'tmpfs',
              'automatic_retry_permitted': False, 'snapshot_consistent': False, 'source_call_passed': False,
              'ssh_exit_code': None, 'ssh_terminate_sent': False, 'ssh_kill_sent': False,
              'source_deadline_seconds': payload['source_deadline_seconds'], 'alarm_fired': False,
              'max_new_chunks': payload['max_new_chunks'], 'max_new_bytes': 67108864 * payload['max_new_chunks']}
    def deadline(signum, frame):
        result['alarm_fired'] = True
        raise TimeoutError('bounded source attempt deadline reached')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reject_other_collectors()
        source = Path(job['device'])
        need(not source.is_symlink(), 'source block path became a symlink')
        # The remote guard verifies the relay code before it executes. The relay
        # separately pins the request and all supervised runtime dependencies.
        guard = 'import hashlib,pathlib,sys;p=pathlib.Path(sys.argv[1]);b=p.read_bytes();assert len(b)==int(sys.argv[2]) and hashlib.sha256(b).hexdigest()==sys.argv[3];sys.argv=[str(p)]+sys.argv[4:];exec(compile(b,str(p),"exec"),{"__name__":"__main__","__file__":str(p)})'
        remote = [original['receiver_python'], '-I', '-S', '-B', '-c', guard,
                  payload['relay']['path'], str(payload['relay']['bytes']), payload['relay']['sha256'],
                  '--request', payload['host_request']['path'], '--request-sha256', payload['host_request']['sha256']]
        transport = cap['ssh_transport'](ssh, remote)
        result['ssh_argv_sha256'] = hashlib.sha256(json.dumps(ssh, separators=(',', ':')).encode()).hexdigest()
        previous_alarm = signal.signal(signal.SIGALRM, deadline)
        signal.alarm(payload['source_deadline_seconds'])
        with cap['source_session'](job['device'], job['source_bytes'], job['ranges'],
                                  chunk_bytes=67108864, include_gpt=False, allow_regular=False) as (fd, identity, plan, rows):
            need(identity == EXPECTED_GEOMETRY and len(rows) == job['expected_chunks'], 'actual source identity or chunk count changed')
            process = subprocess.Popen(transport, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=None, shell=False, start_new_session=True, bufsize=0)
            result['ssh_pid'] = process.pid
            result['ssh_start_ticks'] = start_ticks(process.pid)
            incoming = PrefixBoundInput(process.stdout, cap, payload['expected_completed_chunks'])
            acknowledgement = cap['send_session'](fd, identity, plan, rows, incoming, process.stdin,
                                                allow_regular=False, max_new_chunks=payload['max_new_chunks'])
            process.stdin.close()
            result['ssh_exit_code'] = process.wait(timeout=5)
            need(result['ssh_exit_code'] == 0, 'SSH relay did not finish successfully')
            need(cap['geometry'](fd, False) == EXPECTED_GEOMETRY, 'source geometry changed after acknowledged capture')
        read_pinned(DEVICE_ROOT / 'capture-plan.json', ORIGINAL_PLAN_SHA, cap)
        read_pinned(DEVICE_ROOT / 'generation2-jobs-plan.json', JOBS_PLAN_SHA, cap)
        result['acknowledgement'] = acknowledgement
        result['source_call_passed'] = True
    except Exception as error:
        result['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if previous_alarm is not None:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, previous_alarm)
        if process is not None:
            if process.stdin is not None and not process.stdin.closed:
                with contextlib.suppress(BrokenPipeError, OSError):
                    process.stdin.close()
            if process.poll() is None:
                result['ssh_terminate_sent'] = True
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    result['ssh_kill_sent'] = True
                    with contextlib.suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
            result['ssh_exit_code'] = process.returncode
            if process.stdout is not None:
                process.stdout.close()
        result['ssh_child_reaped'] = process is not None and process.returncode is not None
        result['source_call_passed'] = bool(result['source_call_passed'] and result['ssh_child_reaped'] and
                                            result['ssh_exit_code'] == 0 and not result['ssh_terminate_sent'] and
                                            not result['ssh_kill_sent'] and not result['alarm_fired'])
        result['finished_utc'] = utc()
        os.close(lock)
    return result
