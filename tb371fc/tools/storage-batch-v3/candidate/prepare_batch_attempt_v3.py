#!/usr/bin/env python3
"""Prepare one explicitly selected storage attempt. Never execute generated commands.

The original frozen runtime remains unchanged. This version prepares explicit
one-or-four chunk requests using new filenames in the same owner namespace,
with either the legacy or batch previous-attempt chain verified. It reads
complete prior observations, checks their bindings, and writes a new local packet
exclusively. The root coordinator alone runs its separate host/tablet commands.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import types
import zlib

LIMIT = 2 * 1024 * 1024
CHUNK = 67108864
ROOT = '/tank/shared/projects/qemu-tb371fc/.git/tb371fc-support-6a5-control/storage-continuation-f093-20261125-6a5-001'
HOST_BOOT = 'f093b2c8-08ac-413b-92ea-f2c1efd77c5a'
SOURCE_BOOT = 'f8e0ba9a-2f6e-47c0-9e98-8ca907ec7a2f'
JOBS_SHA = 'a90c1161c6cbce7d0fada8e1901354f0a58b5707e02231d206a0c1f663a1a77a'
BINDING_SHA = '78fa6806272f40e906dabbf9c75bb6553642c2fbe9e1deb7d0f20a784cc8b131'
CALLER_PIN = (13400, '34e318dd214f2e8b55d6ed88aca5a0da7dc2097d90b253b59974104a4204faf6')
RAM_CONTROLLER_PIN = (15238, 'f789623e4699845b10c375e94631c7a81bdcb39b1845514a575f8456eac249b3')
RAM_LAUNCHER_PIN = (7794, 'e74bae232a705d3f8122fe3561f6a3c924af39ca1d97d234c29fe6ea4a09f41e')
RUNTIME = {'storage_supervision_batch_v3.py': (42949, '8409e1ada6b41f6a1a5ec685cb267d890e3ed2151d5c6d265122d51c361a791a'), 'host_supervisor_batch_v3.py': (622, '0fe349538fda3588ccc4f0735a6982a2adddcf215c87c3e84b056f55fe25976b'), 'ssh_stdio_relay_batch_v3.py': (617, 'fe011ad83ec50dec1486d39c0f2b46bf0d7de8ebe4a47492a490323bdd4a1dcc'), 'collector': (45801, '89bd7f0c88d4f9853b5d92e2b70cbdcb635f98fd01a1622f72ea2a5f74b4032d'), 'interface': (2340, '3e90eddc3b82a7a27de2916e7bba460b288bbd5b5d4b236a6aed82fec2962366')}
LEGACY_RUNTIME = {'storage_supervision.py': (41853, 'c7e0cf2ddac371c58b21112f2dc979bc53f28e0189cc80984e51fc73ad893255'), 'host_supervisor.py': (613, '3ce70b95ef0f0df77f5282addbabbe3d79e589f14134cfc90cdef537f02fd59c'), 'ssh_stdio_relay.py': (608, '70b32593fcc04995e2376dc6c35f95b8e8eb088c6e856093737f9cdcaa978e55'), 'collector': (45483, '3158a48a1eb119b99465f573b464c86dc71647b8e23ecd87ca9d687ce889beb4'), 'interface': (6100, 'cb7f9b2572838f6758e9032d88ac691e3fca5d3a6706ad753f568e891e06abb0')}


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, 'duplicate JSON key')
            result[key] = value
        return result
    def reject(value):
        raise ValueError('nonfinite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)


def read_file(path):
    path = Path(path)
    need(path.is_absolute(), 'absolute local input required')
    for parent in [path, *path.parents]:
        need(not parent.is_symlink(), 'symlink input forbidden')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        need(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= LIMIT, 'unbounded/nonregular input')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            raw = stream.read(LIMIT + 1)
        after = os.fstat(fd)
        fields = ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        need(all(getattr(before, k) == getattr(after, k) for k in fields) and len(raw) == before.st_size,
             'input changed while reading')
        return raw
    finally:
        os.close(fd)


def file_pin(path, raw):
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def matches_pin(pin, raw):
    need(type(pin) is dict and set(pin) == {'path', 'bytes', 'sha256'}, 'invalid pin fields')
    need(type(pin['bytes']) is int and pin['bytes'] == len(raw) and pin['sha256'] == sha(raw), 'pin mismatch')
    need(type(pin['path']) is str and Path(pin['path']).is_absolute(), 'pin path is not absolute')


def same(left, right):
    return encoded(left) == encoded(right)


def identity(value, boot):
    need(type(value) is dict and value.get('boot_id') == boot and
         type(value.get('pid')) is int and value['pid'] > 0 and
         type(value.get('start_ticks')) is int and value['start_ticks'] > 0, 'invalid process identity')
    return (value['boot_id'], value['pid'], value['start_ticks'])



def request_budget(request):
    if request.get('schema') == 'tb371fc-storage-supervised-attempt-v1':
        need('max_new_chunks' not in request, 'legacy request cannot smuggle a new budget')
        return 1
    need(request.get('schema') == 'tb371fc-storage-supervised-attempt-v2', 'unknown request schema')
    budget = request.get('max_new_chunks')
    need(type(budget) is int and budget in (1, 4), 'invalid explicitly bound batch budget')
    return budget


def runtime_for_request(request):
    request_budget(request)
    return LEGACY_RUNTIME if request['schema'].endswith('-v1') else RUNTIME


def runtime_path(name, legacy=False):
    if name == 'interface':
        return ROOT + '/bindings/' + ('interface-contract-002.json' if legacy else 'interface-contract-batch-v3.json')
    filename = ('acquire_block_ranges_resume_v2.py' if legacy else 'acquire_block_ranges_batch_v3.py') if name == 'collector' else name
    return ROOT + '/code/' + filename


def candidate_runtime_pins():
    return {name: {'path': runtime_path(name), 'bytes': length, 'sha256': digest}
            for name, (length, digest) in RUNTIME.items()}


def parse_outer(raw, request, request_sha):
    budget = request_budget(request)
    previous_runtime = runtime_for_request(request)
    outer = strict_json(raw)
    need(outer['schema'] == 'tb371fc-storage-memory-call-outer-v1' and
         outer['attempt_id'] == request['attempt_id'], 'outer attempt mismatch')
    for kind in ('stdout', 'stderr'):
        data = base64.b64decode(outer[kind + '_base64'], validate=True)
        need(type(outer[kind + '_bytes']) is int and len(data) == outer[kind + '_bytes'] and
             sha(data) == outer[kind + '_sha256'], 'outer stream identity mismatch')
        if kind == 'stdout':
            need(same(strict_json(data), outer['source_result']), 'outer source JSON differs from stdout')
    source = outer['source_result']
    need(source['schema'] in ('tb371fc-storage-memory-sender-result-v1', 'tb371fc-storage-memory-sender-result-v2') and
         source['attempt_id'] == request['attempt_id'] and source['host_request_sha256'] == request_sha and
         source['job_name'] == request['authorization']['job_name'] and source['source_boot_id'] == SOURCE_BOOT,
         'source result binding differs')
    need(source['collector_sha256'] == previous_runtime['collector'][1] and source['source_open_mode'] == 'O_RDONLY' and
         source['automatic_retry_permitted'] is False, 'source safety contract differs')
    if request['schema'].endswith('-v2'):
        need(source['schema'] == 'tb371fc-storage-memory-sender-result-v2',
             'batch request requires RAM controller/worker termination evidence')
        need(type(source.get('max_new_chunks')) is int and source['max_new_chunks'] == budget and
             type(source.get('max_new_bytes')) is int and source['max_new_bytes'] == budget * CHUNK,
             'source result batch budget differs from pinned host request')
    if source['schema'].endswith('-v1'):
        need(source['tablet_files_created_or_written'] is False, 'legacy source output scope differs')
    else:
        need(source['tablet_persistent_files_created_or_written'] is False and
             source['tablet_block_source_written'] is False and source['control_output_filesystem'] == 'tmpfs',
             'RAM source output scope differs')
        need(outer['worker_reaped'] is True and outer['output_limit_reached'] is False and
             outer['persistent_output_files_written'] is False, 'RAM worker was not fully reaped/retained')
        worker = outer['worker_process']; controller = outer['controller_process']
        need(identity(worker, SOURCE_BOOT) == (SOURCE_BOOT, source['source_pid'], source['source_start_ticks']),
             'RAM worker differs from source identity')
        need(identity(controller, SOURCE_BOOT) != identity(worker, SOURCE_BOOT), 'controller/worker identities collide')
    need(type(source['source_call_passed']) is bool, 'source outcome must be boolean')
    need(source['ssh_child_reaped'] is True and type(source['ssh_exit_code']) is int,
         'SSH child lacks wait/exit evidence')
    need(type(outer['root_process_wait_returncode']) is int and
         outer['root_process_wait_returncode'] == (0 if source['source_call_passed'] is True else 2),
         'root source process lacks matching wait result')
    for role in ('source', 'ssh'):
        need(type(source[role + '_pid']) is int and source[role + '_pid'] > 0 and
             type(source[role + '_start_ticks']) is int and source[role + '_start_ticks'] > 0,
             'source/SSH exact identity missing')
    need(source['source_pid'] != source['ssh_pid'], 'source and SSH PID must differ')
    return outer, source


def validate_previous(request_raw, outer_raw, source_end_raw, host_end_raw, review, pane):
    request = strict_json(request_raw)
    budget = request_budget(request)
    previous_runtime = runtime_for_request(request)
    need(request['namespace'] == ROOT, 'prior request namespace differs')
    need(re.fullmatch(r'attempt-[0-9]{6}', request['attempt_id']) is not None, 'invalid prior attempt')
    need(request['host']['boot_id'] == HOST_BOOT and request['host']['uid'] == 1000, 'host identity differs')
    need(set(request['runtime']) == set(previous_runtime), 'runtime pin set differs')
    for name, (length, digest) in previous_runtime.items():
        p = request['runtime'][name]
        need(p['bytes'] == length and p['sha256'] == digest, 'unreviewed runtime')
        expected = runtime_path(name, legacy=request['schema'].endswith('-v1'))
        need(p['path'] == expected, 'runtime path differs')
    digest = sha(request_raw)
    outer, source = parse_outer(outer_raw, request, digest)
    end = strict_json(source_end_raw)
    need(end['schema'] == 'tb371fc-storage-source-termination-and-environment-v1' and
         end.get('attempt_id', request['attempt_id']) == request['attempt_id'] and
         end['boot_id'] == SOURCE_BOOT and end['uid'] == 0 and end['source_lock_available'] is True,
         'source termination context differs')
    expected = {(source[role + '_pid'], source[role + '_start_ticks']) for role in ('source', 'ssh')}
    processes = end['processes']
    if source['schema'].endswith('-v2'):
        controller = outer['controller_process']
        expected.add((controller['pid'], controller['start_ticks']))
        need(len(expected) == 3, 'controller/source/SSH identities must be distinct')
    need(type(processes) is list and len(processes) == len(expected) and
         {(p['pid'], p['expected_start_ticks']) for p in processes} == expected,
         'source termination does not cover exact source and SSH identities')
    for proc in processes:
        need(proc['same_identity_running'] is False, 'source/SSH identity still running')
        need(proc['present'] is False or (proc['present'] is True and
             type(proc.get('observed_start_ticks')) is int and type(proc.get('state')) is str and
             (proc.get('observed_start_ticks') != proc['expected_start_ticks'] or proc.get('state') == 'Z')),
             'source absence/zombie/PID-reuse evidence missing')
    host = strict_json(host_end_raw)
    need(set(host) == {'evidence', 'result'}, 'complete host termination packet required')
    hr = host['result']
    matches_pin(host['evidence'], encoded(hr))
    need(Path(host['evidence']['path']).parent == Path(ROOT) / 'preparations', 'host evidence outside namespace')
    need(hr['schema'] == 'tb371fc-storage-host-attempt-stop-review-v1' and
         hr['receiver_identity_running'] is False and hr['source_result_passed'] is source['source_call_passed'],
         'host termination result mismatch')
    files = hr['files']
    need(type(files) is list and len({f['name'] for f in files}) == len(files), 'duplicate host file rows')
    named = {f['name']: f for f in files}
    pins, identities = [], []
    for f in files:
        need(type(f['name']) is str and Path(f['name']).name == f['name'] and f['name'] not in ('.', '..'),
             'unsafe retained filename')
        need(f['mode'] == '0o600' and type(f['inode']) is int and f['inode'] > 0, 'unsafe retained file metadata')
        if f['kind'] == 'regular':
            p = {'path': ROOT + '/attempts/' + request['attempt_id'] + '/' + f['name'],
                 'bytes': f['bytes'], 'sha256': f['sha256']}
            matches_pin(p, encoded(f['json']))
            pins.append(p)
            if 'request_sha256' in f['json']:
                need(f['json']['request_sha256'] == digest, 'retained request binding mismatch')
        else:
            need(f['kind'] == 'fifo' and f['name'] in ('source-to-receiver.fifo', 'receiver-to-source.fifo'),
                 'unexpected retained file kind')
    for role, field in (('receiver', 'expected_identity'), ('relay', 'relay_identity')):
        start, terminal = named.get(role + '-start.json'), named.get(role + '-terminal.json')
        if start is not None:
            observed = start['json']['process']
            need(identity(observed, HOST_BOOT) == identity(hr[field], HOST_BOOT), 'host process identity mismatch')
            need(hr[role + '_identity_running'] is False, 'host endpoint still running')
            identities.append(observed)
            if terminal is not None:
                need(terminal['json']['schema'] == 'tb371fc-storage-' + role + '-terminal-v1', 'terminal schema mismatch')
                need(identity(terminal['json']['process'], HOST_BOOT) == identity(observed, HOST_BOOT),
                     'terminal process differs from start')
                need(terminal['json']['request_sha256'] == digest and terminal['json']['host_boot_id'] == HOST_BOOT,
                     'terminal request/boot mismatch')
        else:
            need(role == 'relay' and terminal is None, 'missing receiver start or orphan terminal')
    need(named['receiver-start.json']['json']['admission']['verified'] is True and
         named['receiver-start.json']['json']['admission']['background_daemon'] is False,
         'real foreground admission missing')
    pane_rows = [line.split('|') for line in hr['panes'].splitlines()]
    selected = [row for row in pane_rows if len(row) == 5 and row[1] == pane]
    need(len(selected) == 1 and selected[0][2] == '1' and selected[0][4].isdecimal(), 'own tmux pane not dead')
    need(named['receiver-start.json']['json']['admission']['tmux_pane'] == pane,
         'requested pane was not the observed own receiver pane')
    need(type(review) is dict and set(review) == {'outcome', 'reason', 'retained_files_reviewed'} and
         review['outcome'] in ('clean', 'abnormal') and review['retained_files_reviewed'] is True and
         type(review['reason']) is str and 0 < len(review['reason']) <= 2000, 'explicit prior review required')
    rt = named.get('receiver-terminal.json', {}).get('json')
    lt = named.get('relay-terminal.json', {}).get('json')
    clean = (source['source_call_passed'] is True and source['ssh_exit_code'] == 0 and
             source['ssh_terminate_sent'] is False and source['ssh_kill_sent'] is False and
             source['alarm_fired'] is False and rt is not None and lt is not None and
             rt['exit_code'] == lt['exit_code'] == 0 and rt['clean_host_protocol'] is True and
             lt['outcome'] == 'drained' and lt['supervisor_termination_confirmed_before_exit0'] is True and
             same(rt['acknowledgement'], source.get('acknowledgement')))
    need((review['outcome'] == 'clean') == clean, 'explicit clean/abnormal review contradicts actual endpoints')
    if clean:
        need(rt['outcome'] in ('host-clean-pause', 'host-complete') and selected[0][3] == '0',
             'clean endpoint outcome differs')
        ack = rt['acknowledgement']
        need(type(rt['actual_new_chunks']) is int and 0 <= rt['actual_new_chunks'] <= budget and
             ack['chunks'] == request['expected_completed_chunks'] + rt['actual_new_chunks'], 'invalid acknowledged budget')
        need(ack['snapshot_consistent'] is False and
             ((ack.get('paused') is True and ack['complete'] is False and rt['actual_new_chunks'] == budget and
               type(ack.get('max_new_chunks')) is int and ack['max_new_chunks'] == budget) or
              (ack.get('paused') is None and ack['complete'] is True)), 'pause/completion distinction differs')
    return request, host, source, pins, identities, '|'.join(selected[0])


def command(code):
    compile(code, '<generated-command>', 'exec')
    return ("python3 -I -S -B - <<'PY'\n" + code + '\nPY\n').encode()


def packed(value):
    raw = encoded(value)
    return "raw=zlib.decompress(base64.b64decode(%r));assert len(raw)==%d and hashlib.sha256(raw).hexdigest()==%r;p=json.loads(raw)\n" % (
        base64.b64encode(zlib.compress(raw, 9)).decode(), len(raw), sha(raw))


# These commands are emitted as text only. Importing/running this preparer never
# evaluates them and never starts subprocesses, tmux, SSH or the collector.
HOST_COMMON = '''import base64,hashlib,json,os,stat,zlib,types,fcntl,subprocess,shlex,time,datetime
from pathlib import Path
PACKET
assert os.geteuid()==1000 and Path('/proc/sys/kernel/random/boot_id').read_text().strip()==p['host_boot']
rp=p['runtime']['storage_supervision_batch_v3.py'];q=Path(rp['path'])
for x in [q,*q.parents]: assert not x.is_symlink()
fd=os.open(q,os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
with os.fdopen(fd,'rb') as f:
 s=os.fstat(f.fileno());assert stat.S_ISREG(s.st_mode) and s.st_uid==1000 and s.st_nlink==1 and stat.S_IMODE(s.st_mode)==0o600
 raw=f.read(2097153);assert len(raw)==rp['bytes'] and hashlib.sha256(raw).hexdigest()==rp['sha256']
m=types.ModuleType('verified_storage_supervision');m.__file__=str(q);exec(compile(raw,str(q),'exec'),m.__dict__)
r=m.private_directory(p['namespace'])
for directory in ('bindings','attempts','preparations','code','captures'):m.private_directory(r/directory)
def pincheck(x):return m.pin_file(x)
def save_bytes(path,data):
 m.safe_path(path);fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600)
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 m.fsync_directory(Path(path).parent)
 return {'path':str(path),'bytes':len(data),'sha256':m.sha(data)}
def dead_pane():
 rows=subprocess.run(['tmux','-L',p['tmux_socket'],'list-panes','-a','-F','#{window_id}|#{pane_id}|#{pane_dead}|#{pane_dead_status}|#{pane_pid}'],capture_output=True,text=True,check=True).stdout.splitlines()
 assert rows.count(p['previous_pane'])==1
 assert p['previous_pane'].split('|')[1:3]==[p['tmux_pane'],'1']
def previous_still_ended():
 for x in p['existing_pins']:pincheck(x)
 old=r/'attempts'/p['previous_attempt']
 assert sorted(x.name for x in old.iterdir())==p['previous_files']
 for ident in p['previous_processes']:assert not m.running(ident)
 dead_pane()
'''

HOST_PREPARE = '''previous_still_ended()
lock=os.open(r/'supervisor.lock',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
try:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert sorted(x.name for x in (r/'attempts').iterdir())==p['expected_prior_attempts']
 for x in p['files']:
  q=m.safe_path(x['path']);assert q.parent==r/'bindings' and not q.exists() and not q.is_symlink()
  b=base64.b64decode(x['base64'],validate=True);assert len(b)==x['bytes'] and m.sha(b)==x['sha256']
 for x in p['files']:save_bytes(x['path'],base64.b64decode(x['base64'],validate=True));pincheck({k:x[k] for k in ('path','bytes','sha256')})
 req,ns,cap,plan,rows,collector=m.request_context(p['request']['path'],p['request']['sha256'])
 attempts,previous=m.previous_attempts(ns,req,p['request']['sha256']);m.capture_preconditions(cap,req,previous,collector)
 out={'schema':'tb371fc-storage-single-attempt-prepared-v1','request':p['request'],'source_started':False,'attempt_directory_created':False,'files':[{k:x[k] for k in ('path','bytes','sha256')} for x in p['files']]}
 evidence=save_bytes(r/'preparations'/p['prepare_name'],m.encoded(out))
finally:os.close(lock)
print(json.dumps({'evidence':evidence,'result':out},sort_keys=True))
'''

HOST_LAUNCH = '''previous_still_ended()
lock=os.open(r/'supervisor.lock',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
try:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 req,ns,capture,plan,rows,collector=m.request_context(p['request']['path'],p['request']['sha256'])
 attempts,previous=m.previous_attempts(ns,req,p['request']['sha256']);m.capture_preconditions(capture,req,previous,collector)
 m.pin_file(req['host']['agent_heavy'],private=False)
 assert subprocess.run(['tmux','-L',p['tmux_socket'],'show-options','-gv','remain-on-exit'],capture_output=True,text=True,check=True).stdout.strip()=='on'
 for name in (p['once_name'],p['stdout_name'],p['stderr_name'],p['launch_name']):assert not (r/'preparations'/name).exists()
 argv=[req['host']['agent_heavy']['path'],'/usr/bin/python3','-I','-S','-B',req['runtime']['host_supervisor_batch_v3.py']['path'],'--request',p['request']['path'],'--request-sha256',p['request']['sha256']]
 once=save_bytes(r/'preparations'/p['once_name'],m.encoded({'schema':'tb371fc-storage-host-launch-once-v1','request_sha256':p['request']['sha256'],'argv':argv,'utc':m.utc(),'automatic_retry':False}))
finally:os.close(lock)
shell='set -C; umask 077; exec '+shlex.join(argv)+' >'+shlex.quote(str(r/'preparations'/p['stdout_name']))+' 2>'+shlex.quote(str(r/'preparations'/p['stderr_name']))
# No -k: a live pane is never killed/replaced, even if it changed after review.
launched=subprocess.run(['tmux','-L',p['tmux_socket'],'respawn-pane','-t',p['tmux_pane'],shlex.join(['/bin/bash','-c',shell])],capture_output=True,text=True)
result={'returncode':launched.returncode,'stdout':launched.stdout,'stderr':launched.stderr,'once':once,'request':p['request']}
save_bytes(r/'preparations'/p['launch_name'],m.encoded(result))
ready=r/'attempts'/p['attempt_id']/'receiver-ready.json'
if launched.returncode==0:
 for _ in range(160):
  if ready.exists():break
  time.sleep(.05)
result['ready']=m.load(ready) if ready.exists() else None
if result['ready'] is not None:assert result['ready']['request_sha256']==p['request']['sha256']
print(json.dumps(result,sort_keys=True))
'''

HOST_OBSERVE = '''pincheck(p['request']);attempt=r/'attempts'/p['attempt_id'];m.private_directory(attempt)
starts={role:m.load(attempt/(role+'-start.json')) for role in ('receiver','relay') if (attempt/(role+'-start.json')).exists()}
assert 'receiver' in starts
for value in starts.values():assert value['request_sha256']==p['request']['sha256'] and not m.running(value['process'])
panes=subprocess.run(['tmux','-L',p['tmux_socket'],'list-panes','-a','-F','#{window_id}|#{pane_id}|#{pane_dead}|#{pane_dead_status}|#{pane_pid}'],capture_output=True,text=True,check=True).stdout
selected=[x.split('|') for x in panes.splitlines() if len(x.split('|'))==5 and x.split('|')[1]==p['tmux_pane']]
assert len(selected)==1 and selected[0][2]=='1'
files=[]
for q in sorted(attempt.iterdir()):
 s=q.lstat();assert not q.is_symlink() and s.st_uid==1000 and stat.S_IMODE(s.st_mode)==0o600
 row={'name':q.name,'bytes':s.st_size,'inode':s.st_ino,'mode':oct(stat.S_IMODE(s.st_mode))}
 if stat.S_ISREG(s.st_mode):
  b=m.read_regular(q);row.update(kind='regular',sha256=m.sha(b),json=m.strict_json(b))
 else:assert stat.S_ISFIFO(s.st_mode);row['kind']='fifo'
 files.append(row)
outer_raw=base64.b64decode(p['outer_result_base64'],validate=True)
assert len(outer_raw)==p['outer_result_pin']['bytes'] and m.sha(outer_raw)==p['outer_result_pin']['sha256']
source_outer=m.strict_json(outer_raw)
assert source_outer['attempt_id']==p['attempt_id'] and source_outer['source_result']['host_request_sha256']==p['request']['sha256']
out={'schema':'tb371fc-storage-host-attempt-stop-review-v1','utc':m.utc(),'expected_identity':starts['receiver']['process'],'receiver_identity_running':False,'source_result_passed':source_outer['source_result']['source_call_passed'],'signal_sent':False,'reason':'read-only exact endpoint termination observation; no retry or signal','panes':panes,'files':files}
if 'relay' in starts:out.update(relay_identity=starts['relay']['process'],relay_identity_running=False)
lock=os.open(r/'supervisor.lock',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
finally:os.close(lock)
assert not Path(p['outer_result_pin']['path']).exists() and not (r/'preparations'/p['host_observation_name']).exists()
save_bytes(p['outer_result_pin']['path'],outer_raw)
evidence=save_bytes(r/'preparations'/p['host_observation_name'],m.encoded(out))
print(json.dumps({'evidence':evidence,'result':out},sort_keys=True))
'''


def source_command(payload, caller, collector):
    here = Path(__file__).resolve().parent
    controller = read_file(here / 'ram_source_controller_batch_v3.py')
    launcher = read_file(here / 'prepare_ram_launch_batch_v3.py')
    need((len(controller), sha(controller)) == RAM_CONTROLLER_PIN, 'RAM controller changed')
    need((len(launcher), sha(launcher)) == RAM_LAUNCHER_PIN, 'RAM command builder changed')
    module = types.ModuleType('verified_ram_command_builder')
    exec(compile(launcher, '<verified-ram-command-builder>', 'exec'), module.__dict__)
    files = module.build('capture', payload['attempt_id'], payload, caller, collector, controller)
    return {'tablet-ram-launch-command.txt': files['ram-launch-command.txt'],
            'tablet-ram-status-command.txt': files['ram-status-command.txt'],
            'ram-source-packet.json': files['source-packet.json'],
            'ram-preparation.json': files['ram-preparation.json']}


def write_outputs(out, files):
    out = Path(out)
    need(out.is_absolute(), 'absolute output required')
    for q in out.parents:
        need(not q.is_symlink(), 'symlink output parent')
    os.mkdir(out, 0o700)
    pins = []
    for name, raw in files.items():
        need(Path(name).name == name, 'output basename required')
        fd = os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        pins.append(file_pin(out / name, raw))
    print(json.dumps({'prepared_locally': True, 'commands_executed': False, 'files': pins}, sort_keys=True))
    return pins


def prepare(spec_path, out):
    raw = read_file(spec_path); spec = strict_json(raw)
    need(set(spec) == {'schema', 'previous', 'review', 'next', 'tmux', 'inputs'} and
         spec['schema'] == 'tb371fc-storage-batch-attempt-preparation-spec-v3', 'invalid preparation spec')
    need(set(spec['previous']) == {'request', 'outer_result', 'source_termination', 'host_termination'}, 'prior evidence set differs')
    need(spec['tmux'] == {'socket': 'qemu-support-6a5', 'pane': '%9'}, 'only the established own pane %9 is supported')
    prior_raw = {k: read_file(v) for k, v in spec['previous'].items()}
    prev, host, source, old_pins, processes, pane = validate_previous(
        prior_raw['request'], prior_raw['outer_result'], prior_raw['source_termination'],
        prior_raw['host_termination'], spec['review'], spec['tmux']['pane'])
    nxt = spec['next']
    need(set(nxt) == {'attempt_id', 'job_name', 'capture_name', 'resume', 'expected_completed_chunks',
                      'host_timeout_seconds', 'source_deadline_seconds', 'copy_preparation', 'max_new_chunks'}, 'explicit next fields differ')
    need(type(nxt['max_new_chunks']) is int and nxt['max_new_chunks'] in (1, 4), 'invalid next new-chunk budget')
    number = int(prev['attempt_id'].split('-')[1]) + 1
    need(nxt['attempt_id'] == f'attempt-{number:06d}' and number <= 999999, 'next attempt must be explicit consecutive identity')
    need(type(nxt['resume']) is bool and type(nxt['expected_completed_chunks']) is int and nxt['expected_completed_chunks'] >= 0,
         'explicit resume/prefix required')
    need(re.fullmatch(r'lun0-tail-(08[1-9]|09[0-9]|10[0-5])', nxt['job_name']) is not None and
         re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', nxt['capture_name']) is not None, 'invalid job or new capture name')
    need(type(nxt['host_timeout_seconds']) is int and nxt['host_timeout_seconds'] == 260 and
         type(nxt['source_deadline_seconds']) is int and nxt['source_deadline_seconds'] == 240,
         'RAM attempt requires explicit source240/host260 deadlines')
    capture = ROOT + '/captures/' + nxt['capture_name']
    if not nxt['resume']:
        need(nxt['expected_completed_chunks'] == 0 and nxt['copy_preparation'] is None and
             capture != prev['capture_directory'], 'fresh requires a distinct capture, zero prefix, no copy claim')
    need(set(spec['inputs']) == {'jobs_plan', 'source_binding', 'caller', 'collector'}, 'input set differs')
    inputs = {k: read_file(v) for k, v in spec['inputs'].items()}
    need(sha(inputs['jobs_plan']) == JOBS_SHA and sha(inputs['source_binding']) == BINDING_SHA, 'original source/job binding changed')
    need((len(inputs['caller']), sha(inputs['caller'])) == CALLER_PIN and
         (len(inputs['collector']), sha(inputs['collector'])) == RUNTIME['collector'], 'frozen sender/collector changed')
    jobs, binding = strict_json(inputs['jobs_plan']), strict_json(inputs['source_binding'])
    need(binding['jobs_plan_sha256'] == JOBS_SHA, 'source binding job mismatch')
    selected = [j for j in jobs['jobs'] if j['name'] == nxt['job_name']]
    need(len(selected) == 1, 'approved job missing or ambiguous')
    job = selected[0]
    need(job['source_bytes'] == binding['source']['bytes'] and job['device'] == binding['source']['path'], 'job source differs')
    plan = {'schema': 'tb371fc-readonly-range-stream-v1', 'source': binding['source'], 'ranges': job['ranges'],
            'chunk_bytes': CHUNK, 'gpt': None, 'source_open_mode': 'O_RDONLY', 'snapshot_consistent': False}
    cap = types.ModuleType('source_only_pinned_collector')
    exec(compile(inputs['collector'], '<pinned-collector>', 'exec'), cap.__dict__)
    rows = cap.validate_plan(plan)
    need(len(rows) == job['expected_chunks'] and nxt['expected_completed_chunks'] <= len(rows), 'prefix exceeds approved job')
    need(nxt['expected_completed_chunks'] < len(rows) or nxt['resume'], 'zero-new complete verification requires resume')
    files = []
    def retain(name, data):
        p = file_pin(ROOT + '/bindings/' + name, data)
        files.append({**p, 'base64': base64.b64encode(data).decode()})
        return p
    old, new = prev['attempt_id'], nxt['attempt_id']
    source_pin = retain(old + '-source-termination-for-' + new + '.json', prior_raw['source_termination'])
    retain(old + '-outer-result-for-' + new + '.json', prior_raw['outer_result'])
    retain(old + '-review-for-' + new + '.json', encoded({'schema': 'tb371fc-storage-attempt-explicit-review-v1',
           'previous_request_sha256': sha(prior_raw['request']), 'next_attempt_id': new, **spec['review']}))
    named = {f['name']: f for f in host['result']['files']}
    release = {'schema': 'tb371fc-storage-attempt-operator-release-v1', 'previous_attempt_id': old,
               'previous_request_sha256': sha(prior_raw['request']), 'next_attempt_id': new,
               'source_termination_confirmed': True, 'host_termination_confirmed': True,
               'retained_files_reviewed': True, 'abnormal_termination_reviewed': spec['review']['outcome'] == 'abnormal',
               'source_termination_evidence': source_pin, 'host_termination_evidence': host['evidence']}
    for role in ('receiver', 'relay'):
        name = role + '-terminal.json'; row = named.get(name)
        release[role + '_terminal'] = None if row is None else {'path': ROOT + '/attempts/' + old + '/' + name,
                                                             'bytes': row['bytes'], 'sha256': row['sha256']}
    release_pin = retain(old + '-release-to-' + new.split('-')[1] + '.json', encoded(release))
    plan_pin = retain(new + '-plan-' + nxt['job_name'] + '.json', encoded(plan))
    request = {**prev, 'schema': 'tb371fc-storage-supervised-attempt-v2',
               'runtime': candidate_runtime_pins(), 'max_new_chunks': nxt['max_new_chunks'],
               'attempt_id': new, 'capture_directory': capture, 'resume': nxt['resume'],
               'expected_plan': plan_pin, 'expected_completed_chunks': nxt['expected_completed_chunks'],
               'timeout_seconds': nxt['host_timeout_seconds'], 'copy_preparation': nxt['copy_preparation'],
               'previous_release': release_pin, 'authorization': {'job_name': nxt['job_name'],
               'jobs_plan_sha256': JOBS_SHA, 'source_binding_sha256': BINDING_SHA}}
    request_raw = encoded(request); request_pin = retain(new + '-request.json', request_raw)
    payload = {'schema': 'tb371fc-storage-memory-sender-call-v2', 'attempt_id': new, 'namespace': ROOT,
               'host_request': request_pin, 'relay': request['runtime']['ssh_stdio_relay_batch_v3.py'],
               'job_name': nxt['job_name'], 'expected_completed_chunks': nxt['expected_completed_chunks'],
               'source_deadline_seconds': nxt['source_deadline_seconds'], 'max_new_chunks': nxt['max_new_chunks']}
    packet = {'schema': 'tb371fc-storage-single-attempt-preparation-v1', 'namespace': ROOT, 'host_boot': HOST_BOOT,
              'runtime': request['runtime'], 'attempt_id': new, 'previous_attempt': old, 'request': request_pin,
              'previous_files': sorted(named), 'previous_processes': processes, 'previous_pane': pane,
              'tmux_socket': spec['tmux']['socket'], 'tmux_pane': spec['tmux']['pane'],
              'expected_prior_attempts': [f'attempt-{n:06d}' for n in range(1, number)],
              'existing_pins': [file_pin(ROOT + '/bindings/' + old + '-request.json', prior_raw['request']),
                                host['evidence'], *old_pins], 'files': files,
              'prepare_name': new + '-preparation.json', 'once_name': new + '-host-launch-once.json',
              'stdout_name': new + '-host-stdout.log', 'stderr_name': new + '-host-stderr.log',
              'launch_name': new + '-host-tmux-launch.json'}
    prefix = HOST_COMMON.replace('PACKET\n', packed(packet))
    outputs = {'spec.json': raw, 'request.json': request_raw, 'release.json': encoded(release),
               'approved-plan.json': encoded(plan), 'sender-payload.json': encoded(payload),
               'deployment-packet.json': encoded(packet),
               'host-prepare-command.txt': command(prefix + HOST_PREPARE),
               'host-launch-command.txt': command(prefix + HOST_LAUNCH),
               **source_command(payload, inputs['caller'], inputs['collector'])}
    outputs['preparation.json'] = encoded({'schema': 'tb371fc-storage-local-attempt-preparation-v1',
        'request': request_pin, 'release': release_pin, 'prior_outcome': spec['review']['outcome'],
        'source_result_passed': source['source_call_passed'], 'commands_executed': False,
        'automatic_retry': False, 'automatic_fresh_selection': False, 'runtime_modified': False,
        'input_pins': [file_pin(spec_path, raw)] + [file_pin(spec['previous'][k], v) for k, v in prior_raw.items()] +
                      [file_pin(spec['inputs'][k], v) for k, v in inputs.items()],
        'outputs': [file_pin(name, data) for name, data in outputs.items()],
        'next_observation_step': 'After the explicit source call, use observe --packet deployment-packet.json --outer FULL_OUTER.json --out NEW_DIRECTORY. It only generates two read-only observation commands; it does not run them.'})
    return write_outputs(out, outputs)


def observe(packet_path, outer_path, out):
    packet_raw, outer_raw = read_file(packet_path), read_file(outer_path)
    p = strict_json(packet_raw)
    need(p['schema'] == 'tb371fc-storage-single-attempt-preparation-v1', 'wrong packet schema')
    request_file = [f for f in p['files'] if f['path'] == p['request']['path']]
    need(len(request_file) == 1, 'packet request missing')
    request_raw = base64.b64decode(request_file[0]['base64'], validate=True);matches_pin(p['request'], request_raw)
    request = strict_json(request_raw)
    outer, source = parse_outer(outer_raw, request, p['request']['sha256'])
    observed_identities = [(source[role + '_pid'], source[role + '_start_ticks']) for role in ('source', 'ssh')]
    if source['schema'].endswith('-v2'):
        controller = outer['controller_process']
        observed_identities.append((controller['pid'], controller['start_ticks']))
    source_code = '''import json,os,fcntl,datetime
from pathlib import Path
assert os.geteuid()==0 and Path('/proc/sys/kernel/random/boot_id').read_text().strip()==BOOT
rows=[]
for pid,start in IDENTITIES:
 q=Path('/proc')/str(pid)/'stat';row={'pid':pid,'expected_start_ticks':start}
 try:
  v=q.read_text().rsplit(') ',1)[1].split();row.update(present=True,observed_start_ticks=int(v[19]),state=v[0],same_identity_running=int(v[19])==start and v[0]!='Z')
 except FileNotFoundError:row.update(present=False,same_identity_running=False)
 rows.append(row)
fd=os.open('/data/local/tmp/tb371fc-stockboot-e1a9/lun0-rest-recovery.lock',os.O_RDONLY|os.O_CLOEXEC|os.O_NOFOLLOW)
try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
finally:os.close(fd)
assert not any(x['same_identity_running'] for x in rows)
print(json.dumps({'schema':'tb371fc-storage-source-termination-and-environment-v1','attempt_id':ATTEMPT,'boot_id':BOOT,'uid':os.geteuid(),'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'processes':rows,'source_lock_available':True,'preserve_environment_option_used':True,'no_network_attempt':True,'no_source_data_read':True,'no_tablet_file_write':True},sort_keys=True))
'''.replace('BOOT', repr(SOURCE_BOOT)).replace('ATTEMPT', repr(request['attempt_id'])).replace('IDENTITIES', repr(observed_identities))
    compile(source_code, '<source-observation>', 'exec')
    source_outer = "import base64,subprocess,sys,shlex\ncode=base64.b64decode(%r)\nr=subprocess.run(['su','--preserve-environment','-c',shlex.quote(sys.executable)+' -I -S -B -'],input=code,capture_output=True,timeout=15)\nsys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr);sys.exit(r.returncode)\n" % base64.b64encode(source_code.encode()).decode()
    op = file_pin(ROOT + '/bindings/' + request['attempt_id'] + '-observed-outer.json', outer_raw)
    p = {**p, 'outer_result_pin': op, 'outer_result_base64': base64.b64encode(outer_raw).decode(),
         'host_observation_name': request['attempt_id'] + '-host-termination-evidence.json'}
    prefix = HOST_COMMON.replace('PACKET\n', packed(p))
    return write_outputs(out, {'source-termination-command.txt': command(source_outer),
        'host-termination-command.txt': command(prefix + HOST_OBSERVE),
        'observation-preparation.json': encoded({'schema': 'tb371fc-storage-termination-command-preparation-v1',
            'packet': file_pin(packet_path, packet_raw), 'outer': file_pin(outer_path, outer_raw),
            'commands_executed': False, 'signals_sent': False, 'automatic_retry': False,
            'source_endpoint_count': 3 if source['schema'].endswith('-v2') else 2})})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='mode', required=True)
    one = sub.add_parser('prepare');one.add_argument('--spec', required=True);one.add_argument('--out', required=True)
    obs = sub.add_parser('observe');obs.add_argument('--packet', required=True);obs.add_argument('--outer', required=True);obs.add_argument('--out', required=True)
    args = parser.parse_args()
    if args.mode == 'prepare':
        prepare(args.spec, args.out)
    else:
        observe(args.packet, args.outer, args.out)


if __name__ == '__main__':
    main()
