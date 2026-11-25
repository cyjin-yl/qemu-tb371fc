#!/usr/bin/env python3
"""One RAM-only source attempt controller; no automatic retry or next attempt.

The root coordinator launches this in a separate session after inspecting the
specific /dev tmpfs mount. This process waits for its actual worker child. A
250-second overdue observation is not a completion or termination assertion.
"""
import argparse
import base64
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import resource
import stat
import subprocess
import sys

RAM_ROOT = Path('/dev/tb371fc-support-f74b9-batch-v3-f8e0-20261125-001')
BOOT = 'f8e0ba9a-2f6e-47c0-9e98-8ca907ec7a2f'
MOUNT_ID, DEVICE, DEV_INODE = 53066, 17, 12087
CALLER_BYTES, CALLER_SHA = 13400, '34e318dd214f2e8b55d6ed88aca5a0da7dc2097d90b253b59974104a4204faf6'
COLLECTOR_BYTES, COLLECTOR_SHA = 45801, '89bd7f0c88d4f9853b5d92e2b70cbdcb635f98fd01a1622f72ea2a5f74b4032d'
LOG_LIMIT, JSON_LIMIT = 512 * 1024, 2 * 1024 * 1024


def need(value, message):
    if not value:
        raise ValueError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            need(key not in out, 'duplicate JSON key')
            out[key] = value
        return out
    def nonfinite(value):
        raise ValueError('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=nonfinite)


def process_identity(pid):
    try:
        parts = Path('/proc', str(pid), 'stat').read_text().rsplit(') ', 1)[1].split()
    except FileNotFoundError:
        return None
    return {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
            'pid': pid, 'start_ticks': int(parts[19]), 'state': parts[0], 'parent_pid': int(parts[1])}


def running(expected):
    now = process_identity(expected['pid'])
    return now is not None and all(now[k] == expected[k] for k in ('boot_id', 'pid', 'start_ticks')) and now['state'] != 'Z'


def tmpfs_guard():
    need(os.geteuid() == 0, 'root controller required')
    need(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == BOOT, 'source boot changed')
    rows = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, right = line.split(' - ', 1)
        fields, filesystem = left.split(), right.split()
        if fields[4] == '/dev':
            rows.append((int(fields[0]), fields[2], filesystem[0]))
    need(rows == [(MOUNT_ID, '0:17', 'tmpfs')], 'approved /dev mount identity changed')
    info = os.lstat('/dev')
    need(stat.S_ISDIR(info.st_mode) and info.st_dev == DEVICE and info.st_ino == DEV_INODE and
         info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o755, 'approved /dev inode/permissions changed')
    need(os.statvfs('/dev').f_bavail * os.statvfs('/dev').f_frsize >= 16 * 1024 * 1024, 'insufficient bounded RAM capacity')


def directory(path):
    path = Path(path)
    need(path == RAM_ROOT or path.parent == RAM_ROOT, 'outside approved RAM namespace')
    for q in (RAM_ROOT, path):
        info = q.lstat()
        need(stat.S_ISDIR(info.st_mode) and not q.is_symlink() and info.st_dev == DEVICE and
             info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe RAM directory')
    return path


def read_file(path, limit=JSON_LIMIT):
    path = Path(path)
    directory(path.parent)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_dev == DEVICE and
             before.st_nlink == 1 and stat.S_IMODE(before.st_mode) == 0o600 and before.st_size <= limit, 'unsafe RAM file')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(fd)
        need(len(raw) == before.st_size and all(getattr(before, k) == getattr(after, k)
             for k in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')), 'RAM file changed during complete read')
        return raw
    finally:
        os.close(fd)


def save(path, raw):
    path = Path(path)
    directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def worker_code(packet):
    if packet['mode'] == 'probe':
        return b"import json,time\ntime.sleep(5)\nprint(json.dumps({'probe_only':True,'block_source_opened':False,'ssh_started':False}),flush=True)\n"
    need(packet['mode'] == 'capture', 'unknown controller mode')
    caller = base64.b64decode(packet['caller_base64'], validate=True)
    collector = base64.b64decode(packet['collector_base64'], validate=True)
    need((len(caller), sha(caller)) == (CALLER_BYTES, CALLER_SHA), 'unreviewed batch caller')
    need((len(collector), sha(collector)) == (COLLECTOR_BYTES, COLLECTOR_SHA), 'unreviewed collector')
    payload = packet['payload']
    need(type(payload['max_new_chunks']) is int and payload['max_new_chunks'] in (1, 4), 'invalid explicit batch budget')
    need(payload['schema'] == 'tb371fc-storage-memory-sender-call-v2', 'wrong batch source payload schema')
    need(type(payload['source_deadline_seconds']) is int and payload['source_deadline_seconds'] == 240, 'source deadline must be explicit 240 seconds')
    code = 'import base64,hashlib,json,sys\n'
    for name, raw in (('caller', caller), ('collector', collector)):
        code += '%s=base64.b64decode(%r);assert len(%s)==%d and hashlib.sha256(%s).hexdigest()==%r\n' % (
            name, base64.b64encode(raw).decode(), name, len(raw), name, sha(raw))
    code += "ns={'__name__':'memory_call','__file__':'<memory-call>'};exec(compile(caller,'<memory-call>','exec'),ns)\n"
    code += "result=ns['run'](%r,collector)\nprint(json.dumps(result,sort_keys=True),flush=True)\nsys.exit(0 if result['source_call_passed'] else 2)\n" % payload
    return code.encode()


def supervise_worker(run_dir, packet, code, overdue_seconds=250):
    """Called only after production guards; local tests exercise this child/wait boundary."""
    controller = process_identity(os.getpid())
    start = {'schema': 'tb371fc-storage-ram-controller-start-v1', 'mode': packet['mode'],
             'attempt_id': packet['attempt_id'], 'controller_process': controller, 'started_utc': utc(),
             'host_request_sha256': packet.get('payload', {}).get('host_request', {}).get('sha256'),
             'overdue_seconds': overdue_seconds, 'automatic_retry': False,
             'persistent_output_files_written': False, 'log_limit_per_file': LOG_LIMIT}
    save(run_dir / 'controller-start.json', encoded(start))
    log_fds = []
    try:
        for name in ('worker.stdout', 'worker.stderr'):
            fd = os.open(run_dir / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
            log_fds.append(fd)
        # The worker applies resource limits before importing the memory caller.
        # Pipes carrying capture data are unaffected by the regular-file limit.
        prefix = ('import resource\nresource.setrlimit(resource.RLIMIT_CORE,(0,0))\n'
                  'resource.setrlimit(resource.RLIMIT_FSIZE,(%d,%d))\n' % (LOG_LIMIT, LOG_LIMIT)).encode()
        process = subprocess.Popen([sys.executable, '-I', '-S', '-B', '-',
                                    'tb371fc-storage-memory-worker-' + packet['attempt_id']],
                                   stdin=subprocess.PIPE, stdout=log_fds[0], stderr=log_fds[1],
                                   start_new_session=True, close_fds=True, bufsize=0)
        worker = process_identity(process.pid)
        need(worker is not None, 'worker identity unavailable before wait')
        save(run_dir / 'worker-start.json', encoded({'schema': 'tb371fc-storage-ram-worker-start-v1',
             'attempt_id': packet['attempt_id'], 'controller_process': controller, 'worker_process': worker,
             'host_request_sha256': start['host_request_sha256'], 'started_utc': utc()}))
        try:
            process.communicate(input=prefix + code, timeout=overdue_seconds)
        except subprocess.TimeoutExpired:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            save(run_dir / 'overdue.json', encoded({'schema': 'tb371fc-storage-ram-controller-overdue-v1',
                 'attempt_id': packet['attempt_id'], 'utc': utc(), 'controller_process': controller,
                 'worker_process': worker, 'worker_same_identity_running': running(worker),
                 'worker_reaped': False, 'completion_claimed': False, 'signal_sent': False,
                 'automatic_retry': False}))
            # This is the actual child wait, not a time-based termination claim.
            process.wait()
        need(process.returncode is not None, 'worker has not been reaped')
    finally:
        for fd in log_fds:
            os.close(fd)
    stdout = read_file(run_dir / 'worker.stdout', LOG_LIMIT)
    stderr = read_file(run_dir / 'worker.stderr', LOG_LIMIT)
    try:
        result = strict_json(stdout)
    except (ValueError, UnicodeError):
        result = None
    outer = {'schema': 'tb371fc-storage-memory-call-outer-v1', 'attempt_id': packet['attempt_id'],
             'mode': packet['mode'], 'started_utc': start['started_utc'], 'finished_utc': utc(),
             'root_process_wait_returncode': process.returncode, 'worker_reaped': True,
             'controller_process': controller, 'worker_process': worker, 'source_result': result,
             'ram_control_directory': str(run_dir), 'persistent_output_files_written': False,
             'automatic_retry': False, 'controller_exit_must_be_observed_separately': True,
             'output_limit_reached': len(stdout) == LOG_LIMIT or len(stderr) == LOG_LIMIT}
    for name, raw in (('stdout', stdout), ('stderr', stderr)):
        outer.update({name + '_bytes': len(raw), name + '_sha256': sha(raw),
                      name + '_base64': base64.b64encode(raw).decode()})
    save(run_dir / 'source-outer-result.json', encoded(outer))
    passed = (process.returncode == 0 and not outer['output_limit_reached'] and
              (result == {'probe_only': True, 'block_source_opened': False, 'ssh_started': False}
               if packet['mode'] == 'probe' else type(result) is dict and result.get('source_call_passed') is True))
    save(run_dir / 'controller-terminal.json', encoded({'schema': 'tb371fc-storage-ram-controller-terminal-v1',
         'attempt_id': packet['attempt_id'], 'mode': packet['mode'], 'utc': utc(),
         'controller_process': controller, 'worker_process': worker, 'worker_reaped': True,
         'actual_worker_returncode': process.returncode, 'passed': passed,
         'source_outer_sha256': sha(encoded(outer)), 'automatic_retry': False,
         'controller_exit_must_be_observed_separately': True}))
    return 0 if passed else 2


def run(packet_path, digest):
    tmpfs_guard()
    path = Path(packet_path); run_dir = directory(path.parent)
    raw = read_file(path)
    need(sha(raw) == digest, 'packet identity mismatch')
    packet = strict_json(raw)
    need(packet['schema'] == 'tb371fc-storage-ram-source-packet-v1' and packet['boot_id'] == BOOT and
         packet['attempt_id'] == run_dir.name, 'packet namespace binding differs')
    code = worker_code(packet)
    compile(code, '<verified-memory-worker>', 'exec')
    lock_path = RAM_ROOT / 'controller.lock'
    try:
        fd = os.open(lock_path, os.O_RDONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        fd = os.open(lock_path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_dev == DEVICE and
             info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600, 'unsafe controller lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return supervise_worker(run_dir, packet, code)
    finally:
        os.close(fd)


def status(run_dir):
    tmpfs_guard(); run_dir = directory(run_dir)
    result = {'schema': 'tb371fc-storage-ram-controller-status-v1', 'observed_utc': utc(),
              'ram_control_directory': str(run_dir), 'read_only': True, 'files': [], 'processes': []}
    for name in ('controller-start.json', 'worker-start.json', 'overdue.json', 'source-outer-result.json', 'controller-terminal.json'):
        path = run_dir / name
        if path.exists():
            raw = read_file(path); value = strict_json(raw)
            result['files'].append({'name': name, 'bytes': len(raw), 'sha256': sha(raw)})
            if name == 'controller-start.json':
                result['mode'] = value['mode']
                result['processes'].append({'role': 'controller', 'expected': value['controller_process'],
                                            'same_identity_running': running(value['controller_process'])})
            elif name == 'worker-start.json':
                result['processes'].append({'role': 'worker', 'expected': value['worker_process'],
                                            'same_identity_running': running(value['worker_process'])})
            elif name == 'source-outer-result.json':
                source = value.get('source_result')
                result['actual_worker_returncode'] = value['root_process_wait_returncode']
                result['worker_reaped'] = value['worker_reaped']
                if isinstance(source, dict) and 'ssh_pid' in source:
                    ident = {'boot_id': BOOT, 'pid': source['ssh_pid'], 'start_ticks': source['ssh_start_ticks']}
                    result['processes'].append({'role': 'ssh', 'expected': ident, 'same_identity_running': running(ident)})
    result['all_recorded_identities_ended'] = len(result['processes']) >= 2 and not any(p['same_identity_running'] for p in result['processes'])
    result['all_required_identities_ended'] = (result.get('worker_reaped') is True and
        len(result['processes']) == (2 if result.get('mode') == 'probe' else 3) and
        result['all_recorded_identities_ended'])
    print(json.dumps(result, sort_keys=True))
    return 0


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (JSON_LIMIT, JSON_LIMIT))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    one = sub.add_parser('run');one.add_argument('--packet', required=True);one.add_argument('--sha256', required=True)
    observe = sub.add_parser('status');observe.add_argument('--directory', required=True)
    args = parser.parse_args()
    if args.mode == 'run':
        return run(args.packet, args.sha256)
    return status(args.directory)


if __name__ == '__main__':
    raise SystemExit(main())
