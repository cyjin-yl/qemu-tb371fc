#!/usr/bin/env python3
"""One foreground receiver attempt and a separate SSH-stdio FIFO relay.

No endpoint discovery, SSH invocation, automatic continuation, deletion, or
background child exists here. Production entry points pin this module before
executing it. A returned receiver acknowledgement is not proof that the device
sender process has exited; the next attempt requires a separate operator release.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import datetime
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import signal
import stat
import sys
import time
import types

COLLECTOR_SHA256 = '89bd7f0c88d4f9853b5d92e2b70cbdcb635f98fd01a1622f72ea2a5f74b4032d'
COLLECTOR_BYTES = 45801
INTERFACE_SHA256 = '3e90eddc3b82a7a27de2916e7bba460b288bbd5b5d4b236a6aed82fec2962366'
INTERFACE_BYTES = 2340
WRAPPER_SHA256 = 'dc60ed4fdd2136273ef0fdd20ab3b5230140b99b5092e81c15d89dcfde057cc4'
WRAPPER_BYTES = 6681
REQUEST_SCHEMA = 'tb371fc-storage-supervised-attempt-v2'
ALLOWED_NEW_CHUNKS = (1, 4)
RELEASE_SCHEMA = 'tb371fc-storage-attempt-operator-release-v1'
CHUNK_BYTES = 67108864
JSON_LIMIT = 2 * 1024 * 1024
BUFFER_BYTES = 65536
TEARDOWN_SECONDS = 10
RUNTIME_NAMES = {'storage_supervision_batch_v3.py', 'host_supervisor_batch_v3.py', 'ssh_stdio_relay_batch_v3.py'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def strict_json(raw):
    def reject(value):
        raise ValueError('non-finite JSON value: ' + value)
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject)


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n').encode()


def same_json(left, right):
    return encoded(left) == encoded(right)


def safe_path(path):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(str(path)), 'absolute normalized path required')
    for part in [*reversed(path.parents), path]:
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        require(not stat.S_ISLNK(info.st_mode), 'symlink path component forbidden')
    return path


def private_directory(path):
    path = safe_path(path)
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and
            stat.S_IMODE(info.st_mode) == 0o700, 'directory must be owned and mode0700')
    return path


def read_regular(path, *, limit=JSON_LIMIT, private=True):
    path = safe_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and
                info.st_uid in ({os.geteuid()} if private else {0, os.geteuid()}),
                'owned single-link regular file required')
        require(stat.S_IMODE(info.st_mode) == 0o600 if private else not info.st_mode & 0o022,
                'unsafe file permissions')
        require(0 <= info.st_size <= limit, 'file exceeds read bound')
        data = bytearray()
        while len(data) <= limit:
            block = os.read(fd, min(BUFFER_BYTES, limit + 1 - len(data)))
            if not block:
                break
            data.extend(block)
        after = os.fstat(fd)
        require(len(data) == info.st_size <= limit and
                (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
                'file changed during bounded read')
        return bytes(data)
    finally:
        os.close(fd)


def pin_file(pin, *, limit=JSON_LIMIT, private=True):
    require(isinstance(pin, dict) and set(pin) == {'path', 'bytes', 'sha256'}, 'invalid file pin')
    require(type(pin['bytes']) is int and 0 <= pin['bytes'] <= limit and
            isinstance(pin['sha256'], str) and re.fullmatch('[0-9a-f]{64}', pin['sha256']), 'invalid file identity')
    raw = read_regular(pin['path'], limit=limit, private=private)
    require(len(raw) == pin['bytes'] and sha(raw) == pin['sha256'], 'complete file identity mismatch')
    return raw


def fsync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def rename_noreplace(source, target):
    require(sys.platform.startswith('linux'), 'Linux renameat2 is required')
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, 'renameat2', None)
    require(rename is not None, 'renameat2 capability missing')
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    parent = os.open(source.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        require(source.parent == target.parent, 'publication must stay within one private directory')
        if rename(parent, os.fsencode(source.name), parent, os.fsencode(target.name), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
        os.fsync(parent)
    finally:
        os.close(parent)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        require(count > 0, 'non-progressing write')
        view = view[count:]


def save(path, value):
    path = safe_path(path)
    private_directory(path.parent)
    pending = path.with_name(path.name + '.partial')
    fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        write_all(fd, encoded(value))
        os.fsync(fd)
    finally:
        os.close(fd)
    rename_noreplace(pending, path)


def load(path):
    return strict_json(read_regular(path))


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def process_identity(pid):
    require(type(pid) is int and pid > 0, 'invalid PID')
    try:
        raw = Path(f'/proc/{pid}/stat').read_text()
        parts = raw[raw.rfind(')') + 2:].split()
        return {'pid': pid, 'start_ticks': int(parts[19]), 'parent_pid': int(parts[1]),
                'state': parts[0], 'boot_id': boot_id()}
    except (FileNotFoundError, ProcessLookupError):
        return None


def running(identity):
    now = process_identity(identity['pid'])
    return now is not None and now['state'] != 'Z' and all(
        now[key] == identity[key] for key in ('pid', 'start_ticks', 'boot_id'))


def admission_proof(request):
    """Check the real pinned wrapper and immediate flock guardian; no fake env."""
    wrapper = request['host']['agent_heavy']
    require(wrapper['sha256'] == WRAPPER_SHA256 and wrapper['bytes'] == WRAPPER_BYTES,
            'wrong reviewed agent-heavy identity')
    pin_file(wrapper, private=False)
    require(os.environ.get('TMUX') and os.environ.get('TMUX_PANE'), 'foreground tmux context required')
    chain, pid = [], os.getppid()
    for _ in range(8):
        item = process_identity(pid)
        require(item is not None, 'ancestor disappeared during admission check')
        argv_raw = Path(f'/proc/{pid}/cmdline').read_bytes()
        argv = [os.fsdecode(x) for x in argv_raw.split(b'\0') if x]
        chain.append({**item, 'argv_sha256': sha(argv_raw), 'argv': argv})
        if pid == 1:
            break
        pid = item['parent_pid']
    first = chain[0]['argv']
    require(first and Path(first[0]).name == 'flock' and '--exclusive' in first,
            'immediate foreground flock guardian required')
    locks = {'/home/ezra/.omp/agent/locks/heavy-work.lock'} | {
        f'/home/ezra/.omp/agent/locks/heavy-work-{i}.lock' for i in range(2, 6)}
    require(sum(arg in locks for arg in first) == 1 and '--no-fork' not in first and '--close' not in first,
            'reviewed foreground heavy slot not observed')
    require(any(wrapper['path'] in item['argv'] for item in chain[1:]), 'pinned agent-heavy ancestor missing')
    return {'verified': True, 'wrapper': wrapper, 'tmux_pane': os.environ['TMUX_PANE'],
            'ancestors': chain, 'background_daemon': False}


def validate_production_plan(plan, cap):
    rows = cap.validate_plan(plan)
    require(set(plan) == {'schema', 'source', 'ranges', 'chunk_bytes', 'gpt', 'source_open_mode', 'snapshot_consistent'},
            'unexpected approved plan fields')
    require(plan['source'].get('kind') == 'block-device' and plan['chunk_bytes'] == CHUNK_BYTES and
            plan['gpt'] is None and all(r['classification'] == 'live-mutable' for r in plan['ranges']),
            'production plan must bind block source, 64MiB chunks and approved mutable ranges')
    require(isinstance(plan['source'].get('path'), str) and Path(plan['source']['path']).is_absolute(),
            'absolute original source identity required')
    return rows


def request_budget(request):
    budget = request['max_new_chunks']
    require(type(budget) is int and budget in ALLOWED_NEW_CHUNKS,
            'request must explicitly bind one or four new chunks')
    return budget


def request_context(path, digest):
    require(isinstance(digest, str) and re.fullmatch('[0-9a-f]{64}', digest), 'request SHA256 required')
    raw = read_regular(path)
    require(sha(raw) == digest, 'request digest mismatch')
    request = strict_json(raw)
    require(set(request) == {'schema', 'attempt_id', 'namespace', 'capture_directory', 'resume',
                            'expected_plan', 'expected_completed_chunks', 'timeout_seconds', 'host',
                            'runtime', 'authorization', 'copy_preparation', 'previous_release', 'max_new_chunks'}, 'invalid request fields')
    require(request['schema'] == REQUEST_SCHEMA and isinstance(request['attempt_id'], str) and
            re.fullmatch('attempt-[0-9]{6}', request['attempt_id']), 'invalid attempt identity')
    require(type(request['resume']) is bool and type(request['expected_completed_chunks']) is int and
            request['expected_completed_chunks'] >= 0 and type(request['timeout_seconds']) is int and
            1 <= request['timeout_seconds'] <= 7200, 'invalid attempt bounds')
    request_budget(request)
    namespace = private_directory(request['namespace'])
    require(namespace in safe_path(path).parents, 'request must be inside own namespace')
    capture = safe_path(request['capture_directory'])
    require(namespace in capture.parents and capture != namespace / 'attempts' and
            namespace / 'attempts' not in capture.parents, 'capture must be in a distinct own namespace child')
    private_directory(capture.parent)
    require(set(request['host']) == {'boot_id', 'uid', 'agent_heavy'} and
            type(request['host']['uid']) is int and request['host']['uid'] == os.geteuid() and
            request['host']['boot_id'] == boot_id(), 'host boot/UID binding mismatch')
    require(set(request['runtime']) == RUNTIME_NAMES | {'collector', 'interface'}, 'runtime pin set differs')
    for name in RUNTIME_NAMES:
        require(Path(request['runtime'][name]['path']).name == name, 'runtime basename differs')
        pin_file(request['runtime'][name], private=False)
    collector_pin = request['runtime']['collector']
    require(collector_pin['bytes'] == COLLECTOR_BYTES and collector_pin['sha256'] == COLLECTOR_SHA256,
            'unreviewed collector')
    collector_raw = pin_file(collector_pin, private=False)
    interface_pin = request['runtime']['interface']
    require(interface_pin['bytes'] == INTERFACE_BYTES and interface_pin['sha256'] == INTERFACE_SHA256,
            'unreviewed interface')
    interface = strict_json(pin_file(interface_pin, private=False))
    require(interface['schema'] == 'tb371fc-storage-batch-interface-v3' and
            interface['production_chunk_bytes'] == CHUNK_BYTES and
            same_json(interface['receiver']['allowed_max_new_chunks'], [1, 4]) and
            type(interface['receiver']['default_max_new_chunks']) is int and
            interface['receiver']['default_max_new_chunks'] == 1,
            'interface budget differs')
    cap = types.ModuleType('pinned_storage_collector')
    cap.__file__ = collector_pin['path']
    exec(compile(collector_raw, collector_pin['path'], 'exec'), cap.__dict__)
    require(namespace in safe_path(request['expected_plan']['path']).parents, 'approved plan must be retained in own namespace')
    plan_raw = pin_file(request['expected_plan'])
    plan = strict_json(plan_raw)
    rows = validate_production_plan(plan, cap)
    require(request['expected_completed_chunks'] <= len(rows), 'committed prefix exceeds approved plan')
    require(request['expected_completed_chunks'] < len(rows) or request['resume'],
            'zero-new complete-prefix verification requires resume')
    auth = request['authorization']
    require(isinstance(auth, dict) and set(auth) == {'job_name', 'jobs_plan_sha256', 'source_binding_sha256'} and
            isinstance(auth['job_name'], str) and re.fullmatch('[A-Za-z0-9_.-]{1,160}', auth['job_name']) and
            all(isinstance(auth[k], str) and re.fullmatch('[0-9a-f]{64}', auth[k])
                for k in ('jobs_plan_sha256', 'source_binding_sha256')), 'explicit approved job provenance required')
    return request, namespace, capture, plan, rows, cap


def held_lock(path):
    safe_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid() and
                stat.S_IMODE(info.st_mode) == 0o600, 'unsafe supervision lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def previous_attempts(namespace, request, digest):
    attempts = namespace / 'attempts'
    if not attempts.exists():
        attempts.mkdir(mode=0o700)
        fsync_directory(namespace)
    private_directory(attempts)
    entries = sorted(attempts.iterdir())
    require(all(p.is_dir() and not p.is_symlink() and re.fullmatch('attempt-[0-9]{6}', p.name) for p in entries),
            'unexpected attempts entry')
    require([p.name for p in entries] == [f'attempt-{i:06d}' for i in range(1, len(entries) + 1)],
            'attempt sequence has a gap')
    require(request['attempt_id'] == f'attempt-{len(entries) + 1:06d}', 'attempt identifier already used or out of order')
    if not entries:
        require(request['previous_release'] is None, 'first attempt cannot reuse a prior release')
        return attempts, []
    require(request['previous_release'] is not None, 'next attempt requires an explicit operator release')
    release = strict_json(pin_file(request['previous_release']))
    required = {'schema', 'previous_attempt_id', 'previous_request_sha256', 'next_attempt_id',
                'receiver_terminal', 'relay_terminal', 'source_termination_confirmed',
                'host_termination_confirmed', 'retained_files_reviewed', 'abnormal_termination_reviewed',
                'source_termination_evidence', 'host_termination_evidence'}
    require(set(release) == required and release['schema'] == RELEASE_SCHEMA and
            release['previous_attempt_id'] == entries[-1].name and release['next_attempt_id'] == request['attempt_id'],
            'wrong previous-attempt release')
    old = load(entries[-1] / 'receiver-start.json')
    require(release['previous_request_sha256'] == old['request_sha256'], 'release request binding differs')
    require(all(release[k] is True for k in ('source_termination_confirmed', 'host_termination_confirmed', 'retained_files_reviewed')),
            'both endpoint termination and retained evidence review are required')
    for key in ('source_termination_evidence', 'host_termination_evidence'):
        pin_file(release[key])
    for role in ('receiver', 'relay'):
        start_path = entries[-1] / f'{role}-start.json'
        terminal_path = entries[-1] / f'{role}-terminal.json'
        if start_path.exists():
            require(not running(load(start_path)['process']), 'previous attempt process is still alive')
        pin = release[role + '_terminal']
        if terminal_path.exists():
            require(pin is not None and Path(pin['path']) == terminal_path, 'terminal receipt pin missing')
            terminal = strict_json(pin_file(pin))
            require(terminal['request_sha256'] == old['request_sha256'], 'terminal request differs')
        else:
            require(pin is None and release['abnormal_termination_reviewed'] is True,
                    'missing terminal requires explicit abnormal-termination review')
    return attempts, entries


def capture_preconditions(capture, request, previous, cap):
    expected = request['expected_completed_chunks']
    if not request['resume']:
        require(expected == 0 and not capture.exists() and not capture.is_symlink(), 'fresh attempt requires absent capture and zero prefix')
        require(request['copy_preparation'] is None, 'fresh attempt cannot claim a historical copy')
        return
    private_directory(capture)
    require(cap.same_json(cap.load_json_file(capture / 'plan.json'), strict_json(pin_file(request['expected_plan']))),
            'existing capture does not match approved complete plan')
    completed = []
    for path in capture.glob('chunk-*/receipt.json'):
        require(re.fullmatch('chunk-[0-9]{6}', path.parent.name), 'unexpected completed directory')
        read_regular(path)
        completed.append(int(path.parent.name.split('-')[1]))
    require(sorted(completed) == list(range(expected)), 'actual committed prefix differs from request')
    owned_prior = any(load(p / 'receiver-start.json')['capture_directory'] == str(capture) for p in previous)
    if not owned_prior:
        require(request['copy_preparation'] is not None, 'initial resume requires independent historical-copy proof')
        proof = strict_json(pin_file(request['copy_preparation']))
        require(proof.get('schema') == 'tb371fc-incomplete-capture-copy-v1' and
                proof.get('copied') is True and proof.get('source_capture_unchanged') is True and
                proof.get('copy_files_are_new_inodes') is True and proof.get('new_namespace') == str(capture) and
                proof.get('source_plan_sha256') == request['expected_plan']['sha256'] and
                type(proof.get('completed_chunks_verified')) is int and proof['completed_chunks_verified'] == expected,
                'historical-copy receipt does not bind this prefix and new namespace')


class Stopped(Exception):
    pass


class Deadline:
    def __init__(self, end_ns, peer_abort=None, peer_identity=None):
        self.end_ns, self.peer_abort, self.peer_identity = end_ns, peer_abort, peer_identity

    def check(self):
        if time.monotonic_ns() >= self.end_ns:
            raise TimeoutError('attempt deadline expired')
        if self.peer_abort is not None and self.peer_abort.exists():
            raise Stopped('peer recorded an abort')
        if self.peer_identity is not None and not running(self.peer_identity):
            raise Stopped('peer process ended before protocol completion')

    def wait(self, readers=(), writers=()):
        self.check()
        ready = select.select(readers, writers, (), min(0.1, (self.end_ns-time.monotonic_ns())/1e9))
        self.check()
        return ready


class FifoInput:
    def __init__(self, fd, deadline):
        self.fd, self.deadline, self.buffer = fd, deadline, bytearray()
        self.count, self.digest = 0, hashlib.sha256()

    def _fill(self):
        while True:
            readable, _, _ = self.deadline.wait([self.fd])
            if not readable:
                continue
            try:
                block = os.read(self.fd, BUFFER_BYTES)
            except BlockingIOError:
                continue
            self.count += len(block)
            self.digest.update(block)
            self.buffer.extend(block)
            return bool(block)

    def read(self, size):
        require(type(size) is int and 0 <= size <= JSON_LIMIT, 'bounded read size required')
        if not size:
            return b''
        if not self.buffer and not self._fill():
            return b''
        result = bytes(self.buffer[:size])
        del self.buffer[:len(result)]
        return result

    def readline(self, limit):
        require(type(limit) is int and 0 < limit <= JSON_LIMIT + 1, 'bounded line required')
        while True:
            position = self.buffer.find(b'\n', 0, limit)
            if position >= 0 or len(self.buffer) >= limit:
                count = position + 1 if position >= 0 else limit
                result = bytes(self.buffer[:count]); del self.buffer[:count]
                return result
            if not self._fill():
                result = bytes(self.buffer); self.buffer.clear()
                return result


class PlanGuardInput:
    def __init__(self, stream, plan, cap, attempt, max_new_chunks=1):
        cap.check_budget(max_new_chunks)
        self.max_new_chunks = max_new_chunks
        self.stream, self.plan, self.cap, self.attempt, self.lines = stream, plan, cap, attempt, 0

    def readline(self, limit):
        raw = self.stream.readline(limit)
        if self.lines < 2:
            require(raw.endswith(b'\n') and len(raw) <= JSON_LIMIT, 'truncated initial protocol line')
            expected = self.cap.hello_message(self.max_new_chunks) if self.lines == 0 else self.plan
            require(self.cap.same_json(self.cap.strict_json(raw), expected),
                    'initial hello differs' if self.lines == 0 else 'source plan differs from approved exact identity')
            if self.lines == 1:
                save(self.attempt / 'approved-wire-plan.json', {'matched': True, 'wire_bytes': len(raw),
                     'wire_sha256': sha(raw), 'full_source_stat_ranges_geometry_types_compared': True})
        self.lines += 1
        return raw

    def read(self, size):
        require(self.lines >= 2, 'binary data before approved source plan')
        return self.stream.read(size)


class FifoOutput:
    def __init__(self, fd, deadline):
        self.fd, self.deadline, self.count, self.digest = fd, deadline, 0, hashlib.sha256()

    def write(self, data):
        while True:
            _, writable, _ = self.deadline.wait(writers=[self.fd])
            if not writable:
                continue
            try:
                count = os.write(self.fd, memoryview(data)[:BUFFER_BYTES])
            except BlockingIOError:
                continue
            self.count += count
            self.digest.update(memoryview(data)[:count])
            return count

    def flush(self):
        self.deadline.check()


def fifo_identity(path):
    safe_path(path)
    info = path.lstat()
    require(stat.S_ISFIFO(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o600,
            'owned mode0600 FIFO required')
    return {'device': info.st_dev, 'inode': info.st_ino, 'mode': stat.S_IMODE(info.st_mode)}


def open_fifo(path, mode):
    before = fifo_identity(path)
    fd = os.open(path, mode | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
    info = os.fstat(fd)
    require((info.st_dev, info.st_ino) == (before['device'], before['inode']), 'FIFO identity changed')
    return fd


def wait_file(path, deadline):
    while not path.exists():
        deadline.wait()
    return load(path)


def verify_marker(value, digest, identity=None):
    require(value.get('request_sha256') == digest and value.get('host_boot_id') == boot_id(), 'peer marker binding differs')
    if identity is not None:
        require(value.get('process') == identity, 'peer marker process differs')


def result_is_valid(result, request, plan, rows, cap):
    before = request['expected_completed_chunks']
    budget = request_budget(request)
    added = min(budget, len(rows) - before)
    require(result['snapshot_consistent'] is False and type(result['chunks']) is int and result['chunks'] == before + added,
            'receiver added a different number of committed chunks')
    capture = Path(request['capture_directory'])
    if result.get('paused') is True:
        require(set(result) == {'paused', 'complete', 'chunks', 'total_chunks', 'next_index', 'new_chunks',
                               'max_new_chunks', 'pause_receipt', 'snapshot_consistent'} and
                result['complete'] is False and type(result['new_chunks']) is int and
                type(result['max_new_chunks']) is int and
                result['new_chunks'] == result['max_new_chunks'] == budget and
                result['chunks'] == result['next_index'] < result['total_chunks'] == len(rows) and added == budget,
                'invalid pause result')
        pause = safe_path(result['pause_receipt'])
        require(pause.parent == capture / 'pause-receipts' and not (capture / 'manifest.json').exists(),
                'pause cannot create a final manifest')
        receipt = cap.load_json_file(pause)
        require(receipt['schema'] == cap.PAUSE_SCHEMA and receipt['complete'] is False, 'pause receipt differs')
        return 'host-clean-pause'
    require(set(result) == {'complete', 'chunks', 'manifest', 'snapshot_consistent'} and
            result['complete'] is True and result['chunks'] == len(rows) and
            Path(result['manifest']) == capture / 'manifest.json', 'invalid completion result')
    require(cap.load_json_file(capture / 'manifest.json')['status'] == 'verified-range-copies', 'manifest is not complete')
    return 'host-complete'


def terminal_common(digest, process):
    return {'request_sha256': digest, 'process': process, 'finished_utc': utc(), 'host_boot_id': boot_id(),
            'source_sender_termination_confirmed': False, 'operator_release_required_before_next_attempt': True,
            'automatic_retry': False, 'deleted_files': 0, 'snapshot_consistent': False,
            'whole_storage_backup_complete_claimed': False}


def supervise(request_path, digest):
    request, namespace, capture, plan, rows, cap = request_context(request_path, digest)
    admission = admission_proof(request)
    global_lock = held_lock(namespace / 'supervisor.lock')
    attempt = None
    process = process_identity(os.getpid())
    incoming_fd = outgoing_fd = None
    result, error, outcome, relay_drained = None, None, 'failed', None
    old_manifest = None
    old_handlers, old_timer = {}, None
    try:
        attempts, previous = previous_attempts(namespace, request, digest)
        capture_preconditions(capture, request, previous, cap)
        if request['expected_completed_chunks'] == len(rows) and (capture / 'manifest.json').exists():
            data = read_regular(capture / 'manifest.json')
            info = (capture / 'manifest.json').stat()
            old_manifest = {'bytes': len(data), 'sha256': sha(data), 'inode': info.st_ino,
                            'device': info.st_dev, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}
        attempt = attempts / request['attempt_id']
        attempt.mkdir(mode=0o700, exist_ok=False)
        fsync_directory(attempts)
        end_ns = time.monotonic_ns() + request['timeout_seconds'] * 1_000_000_000
        deadline = Deadline(end_ns, attempt / 'relay-abort.json')
        start = {'schema': 'tb371fc-storage-receiver-start-v1', 'request_sha256': digest,
                 'host_boot_id': boot_id(), 'process': process, 'started_utc': utc(),
                 'deadline_monotonic_ns': end_ns, 'capture_directory': str(capture),
                 'deadline_origin': 'receiver-start-after-admission-before-FIFO-ready',
                 'attempt_id': request['attempt_id'], 'admission': admission,
                 'expected_plan': request['expected_plan'], 'expected_completed_chunks': request['expected_completed_chunks'],
                 'max_new_chunks': request_budget(request), 'max_new_bytes': request_budget(request) * CHUNK_BYTES}
        save(attempt / 'receiver-start.json', start)
        for name in ('source-to-receiver.fifo', 'receiver-to-source.fifo'):
            os.mkfifo(attempt / name, 0o600)
            fifo_identity(attempt / name)
        fsync_directory(attempt)
        incoming_fd = open_fifo(attempt / 'source-to-receiver.fifo', os.O_RDONLY)
        save(attempt / 'receiver-ready.json', {**start, 'fifos': {
            name: fifo_identity(attempt / name) for name in ('source-to-receiver.fifo', 'receiver-to-source.fifo')}})
        def interrupt(signum, frame):
            raise Stopped('receiver signal/deadline ' + str(signum))
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            old_handlers[signum] = signal.signal(signum, interrupt)
        old_timer = signal.setitimer(signal.ITIMER_REAL, max(0.000001, (end_ns-time.monotonic_ns())/1e9))
        relay_start = wait_file(attempt / 'relay-start.json', deadline)
        verify_marker(relay_start, digest)
        deadline.peer_identity = relay_start['process']
        relay_connected = wait_file(attempt / 'relay-connected.json', deadline)
        verify_marker(relay_connected, digest, relay_start['process'])
        outgoing_fd = open_fifo(attempt / 'receiver-to-source.fifo', os.O_WRONLY)
        save(attempt / 'receiver-connected.json', {'request_sha256': digest, 'host_boot_id': boot_id(),
             'process': process, 'relay_process': relay_start['process'], 'connected_utc': utc()})
        incoming, outgoing = FifoInput(incoming_fd, deadline), FifoOutput(outgoing_fd, deadline)
        result = cap.receive(PlanGuardInput(incoming, plan, cap, attempt, request_budget(request)), outgoing, capture,
                             resume=request['resume'], max_new_chunks=request_budget(request))
        outcome = result_is_valid(result, request, plan, rows, cap)
        if old_manifest is not None:
            data = read_regular(capture / 'manifest.json')
            info = (capture / 'manifest.json').stat()
            require(old_manifest == {'bytes': len(data), 'sha256': sha(data), 'inode': info.st_ino,
                    'device': info.st_dev, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns},
                    'existing complete manifest was rewritten during zero-new verification')
        require(not incoming.buffer, 'unexpected extra source bytes after terminal protocol')
        save(attempt / 'receiver-ack.json', {'request_sha256': digest, 'host_boot_id': boot_id(),
             'acknowledgement': result, 'outcome': outcome, 'incoming_bytes': incoming.count,
             'incoming_sha256': incoming.digest.hexdigest(), 'outgoing_bytes': outgoing.count,
             'outgoing_sha256': outgoing.digest.hexdigest(), 'ack_written_before_close': True})
        os.close(outgoing_fd); outgoing_fd = None
        os.close(incoming_fd); incoming_fd = None
        # Relay waits for this process to finish before it may return SSH exit0.
        # It first proves that both wire directions reached EOF and drained.
        relay_drained = wait_file(attempt / 'relay-drained.json', deadline)
        verify_marker(relay_drained, digest, relay_start['process'])
        require(relay_drained['outcome'] == 'drained-awaiting-supervisor' and
                relay_drained['stdin_eof'] is True and relay_drained['response_eof'] is True,
                'relay did not drain and close cleanly')
        require(relay_drained['counts']['receiver_written'] == incoming.count and
                relay_drained['sha256']['receiver_written'] == incoming.digest.hexdigest() and
                relay_drained['counts']['receiver_read'] == outgoing.count and
                relay_drained['sha256']['receiver_read'] == outgoing.digest.hexdigest(),
                'receiver and relay complete wire accounts differ')
    except BaseException as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)}
        outcome = 'failed'
        if attempt is not None:
            with contextlib.suppress(Exception):
                save(attempt / 'receiver-abort.json', {'request_sha256': digest, 'host_boot_id': boot_id(),
                     'error': error, 'utc': utc()})
    finally:
        if old_timer is not None:
            signal.setitimer(signal.ITIMER_REAL, 0)
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        for fd in (incoming_fd, outgoing_fd):
            if fd is not None:
                os.close(fd)
        if attempt is not None:
            final = {**terminal_common(digest, process), 'schema': 'tb371fc-storage-receiver-terminal-v1',
                     'outcome': outcome, 'exit_code': 0 if outcome != 'failed' and relay_drained is not None else 2,
                     'acknowledgement': result, 'error': error, 'relay_host_process_ended': False,
                     'relay_process_still_awaits_supervisor_exit': relay_drained is not None,
                     'relay_drained_receipt_sha256': sha(read_regular(attempt / 'relay-drained.json')) if relay_drained else None,
                     'clean_host_protocol': outcome != 'failed' and relay_drained is not None,
                     'capture_directory': str(capture), 'max_new_chunks': request_budget(request),
                     'max_new_bytes': request_budget(request) * CHUNK_BYTES}
            final['actual_new_chunks'] = result['chunks'] - request['expected_completed_chunks'] if outcome != 'failed' else None
            final['existing_complete_manifest_preserved'] = old_manifest is not None and outcome != 'failed'
            save(attempt / 'receiver-terminal.json', final)
            print(json.dumps(final, sort_keys=True), flush=True)
        os.close(global_lock)
    if attempt is None:
        raise Stopped(str(error))
    return final['exit_code']


def relay(request_path, digest, input_fd=0, output_fd=1):
    request, namespace, capture, plan, rows, cap = request_context(request_path, digest)
    attempt = private_directory(namespace / 'attempts' / request['attempt_id'])
    ready = load(attempt / 'receiver-ready.json')
    verify_marker(ready, digest)
    require(running(ready['process']), 'receiver is not alive')
    end_ns = ready['deadline_monotonic_ns']
    require(type(end_ns) is int and time.monotonic_ns() < end_ns, 'receiver deadline expired')
    process = process_identity(os.getpid())
    # Exclusive start publication is the relay once-claim. Never reconnect it.
    save(attempt / 'relay-start.json', {'schema': 'tb371fc-storage-relay-start-v1',
         'request_sha256': digest, 'host_boot_id': boot_id(), 'process': process,
         'receiver_process': ready['process'], 'started_utc': utc()})
    deadline = Deadline(end_ns, attempt / 'receiver-abort.json', ready['process'])
    incoming_fd = outgoing_fd = None
    input_flags = output_flags = None
    stdin_eof = response_eof = False
    to_receiver, to_source = bytearray(), bytearray()
    counts = {'source_read': 0, 'receiver_written': 0, 'receiver_read': 0, 'source_written': 0}
    hashes = {key: hashlib.sha256() for key in counts}
    error, outcome = None, 'failed'
    old_handlers = {}
    try:
        require(not os.isatty(input_fd) and not os.isatty(output_fd), 'relay requires binary stdio without a PTY')
        def interrupt(signum, frame):
            raise Stopped('relay signal ' + str(signum))
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, interrupt)
        for name, identity in ready['fifos'].items():
            require(fifo_identity(attempt / name) == identity, 'FIFO replaced since ready')
        incoming_fd = open_fifo(attempt / 'receiver-to-source.fifo', os.O_RDONLY)
        outgoing_fd = open_fifo(attempt / 'source-to-receiver.fifo', os.O_WRONLY)
        save(attempt / 'relay-connected.json', {'request_sha256': digest, 'host_boot_id': boot_id(),
             'process': process, 'receiver_process': ready['process'], 'connected_utc': utc()})
        connected = wait_file(attempt / 'receiver-connected.json', deadline)
        verify_marker(connected, digest, ready['process'])
        require(connected['relay_process'] == process, 'receiver bound another relay')
        input_flags, output_flags = fcntl.fcntl(input_fd, fcntl.F_GETFL), fcntl.fcntl(output_fd, fcntl.F_GETFL)
        fcntl.fcntl(input_fd, fcntl.F_SETFL, input_flags | os.O_NONBLOCK)
        fcntl.fcntl(output_fd, fcntl.F_SETFL, output_flags | os.O_NONBLOCK)
        while True:
            if stdin_eof and not to_receiver and outgoing_fd is not None:
                os.close(outgoing_fd); outgoing_fd = None
            if stdin_eof and response_eof and not to_receiver and not to_source:
                break
            readers = ([] if stdin_eof or len(to_receiver) >= BUFFER_BYTES else [input_fd])
            readers += ([] if response_eof or len(to_source) >= BUFFER_BYTES else [incoming_fd])
            writers = ([outgoing_fd] if to_receiver and outgoing_fd is not None else [])
            writers += ([output_fd] if to_source else [])
            readable, writable, _ = deadline.wait(readers, writers)
            for fd in readable:
                is_source = fd == input_fd
                target = to_receiver if is_source else to_source
                try:
                    data = os.read(fd, BUFFER_BYTES - len(target))
                except BlockingIOError:
                    continue
                key = 'source_read' if is_source else 'receiver_read'
                counts[key] += len(data); hashes[key].update(data)
                if data:
                    target.extend(data)
                elif is_source:
                    stdin_eof = True
                else:
                    response_eof = True
                    require((attempt / 'receiver-ack.json').exists(), 'receiver EOF without retained acknowledgement')
            for fd in writable:
                is_receiver = fd == outgoing_fd
                pending = to_receiver if is_receiver else to_source
                try:
                    count = os.write(fd, pending)
                except BlockingIOError:
                    continue
                require(count > 0, 'relay write made no progress')
                key = 'receiver_written' if is_receiver else 'source_written'
                counts[key] += count; hashes[key].update(pending[:count]); del pending[:count]
        require(counts['source_read'] == counts['receiver_written'] and
                counts['receiver_read'] == counts['source_written'], 'relay byte accounting differs')
        require(hashes['source_read'].digest() == hashes['receiver_written'].digest() and
                hashes['receiver_read'].digest() == hashes['source_written'].digest(), 'relay stream digest differs')
        save(attempt / 'relay-drained.json', {'schema': 'tb371fc-storage-relay-drained-v1',
             'request_sha256': digest, 'host_boot_id': boot_id(), 'process': process,
             'outcome': 'drained-awaiting-supervisor', 'stdin_eof': stdin_eof, 'response_eof': response_eof,
             'counts': counts, 'sha256': {k: v.hexdigest() for k, v in hashes.items()}, 'drained_utc': utc()})
        for fd in (incoming_fd, outgoing_fd):
            if fd is not None:
                os.close(fd)
        incoming_fd = outgoing_fd = None
        deadline.peer_identity = None
        receiver_terminal = wait_file(attempt / 'receiver-terminal.json', deadline)
        verify_marker(receiver_terminal, digest, ready['process'])
        require(receiver_terminal['exit_code'] == 0 and receiver_terminal['clean_host_protocol'] is True and
                receiver_terminal['outcome'] in ('host-clean-pause', 'host-complete') and
                receiver_terminal['relay_drained_receipt_sha256'] == sha(read_regular(attempt / 'relay-drained.json')),
                'supervisor did not finish successfully')
        while running(ready['process']):
            deadline.wait()
        outcome = 'drained'
    except BaseException as exc:
        error = {'type': type(exc).__name__, 'message': str(exc)}
        with contextlib.suppress(Exception):
            save(attempt / 'relay-abort.json', {'request_sha256': digest, 'host_boot_id': boot_id(),
                 'error': error, 'utc': utc()})
    finally:
        for signum, handler in old_handlers.items():
            signal.signal(signum, handler)
        for fd in (incoming_fd, outgoing_fd):
            if fd is not None:
                os.close(fd)
        for fd, flags in ((input_fd, input_flags), (output_fd, output_flags)):
            if flags is not None:
                with contextlib.suppress(OSError):
                    fcntl.fcntl(fd, fcntl.F_SETFL, flags)
        final = {**terminal_common(digest, process), 'schema': 'tb371fc-storage-relay-terminal-v1',
                 'outcome': outcome, 'exit_code': 0 if outcome == 'drained' else 2, 'error': error,
                 'stdin_eof': stdin_eof, 'response_eof': response_eof, 'counts': counts,
                 'sha256': {k: v.hexdigest() for k, v in hashes.items()},
                 'undelivered_to_receiver_bytes': len(to_receiver), 'undelivered_to_source_bytes': len(to_source),
                 'buffer_limit_per_direction': BUFFER_BYTES, 'ssh_process_started_here': False}
        final['supervisor_termination_confirmed_before_exit0'] = outcome == 'drained'
        save(attempt / 'relay-terminal.json', final)
        # Control output goes to stderr. stdout is exclusively protocol bytes.
        print(json.dumps(final, sort_keys=True), file=sys.stderr, flush=True)
    return final['exit_code']


def entry(mode, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--request-sha256', required=True)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        return supervise(args.request, args.request_sha256) if mode == 'supervisor' else relay(args.request, args.request_sha256)
    except (Exception, KeyboardInterrupt) as exc:
        print(json.dumps({'schema': 'tb371fc-storage-entry-refusal-v1', 'role': mode, 'exit_code': 2,
                          'error_type': type(exc).__name__, 'error': str(exc), 'automatic_retry': False}, sort_keys=True),
              file=sys.stderr, flush=True)
        return 2
