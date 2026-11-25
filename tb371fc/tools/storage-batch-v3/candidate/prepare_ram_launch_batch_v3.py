#!/usr/bin/env python3
"""Generate, never run, one RAM controller launch and its read-only status command."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re

RAM_ROOT = '/dev/tb371fc-support-f74b9-batch-v3-f8e0-20261125-001'
BOOT = 'f8e0ba9a-2f6e-47c0-9e98-8ca907ec7a2f'
CALLER = (13400, '34e318dd214f2e8b55d6ed88aca5a0da7dc2097d90b253b59974104a4204faf6')
COLLECTOR = (45801, '89bd7f0c88d4f9853b5d92e2b70cbdcb635f98fd01a1622f72ea2a5f74b4032d')
CONTROLLER = (15238, 'f789623e4699845b10c375e94631c7a81bdcb39b1845514a575f8456eac249b3')


def encoded(value):
    return (json.dumps(value, sort_keys=True, indent=2) + '\n').encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pin(path, raw):
    return {'path': str(path), 'bytes': len(raw), 'sha256': sha(raw)}


def shell(code):
    compile(code, '<RAM-root-command>', 'exec')
    wrapper = "import base64,subprocess,sys,shlex\ncode=base64.b64decode(%r)\nr=subprocess.run(['su','--preserve-environment','-c',shlex.quote(sys.executable)+' -I -S -B -'],input=code,capture_output=True,timeout=25)\nsys.stdout.buffer.write(r.stdout);sys.stderr.buffer.write(r.stderr);sys.exit(r.returncode)\n" % base64.b64encode(code.encode()).decode()
    compile(wrapper, '<RAM-su-wrapper>', 'exec')
    return ("python3 -I -S -B - <<'PY'\n" + wrapper + '\nPY\n').encode()


def build(mode, attempt, payload, caller, collector, controller):
    if (len(controller), sha(controller)) != CONTROLLER:
        raise ValueError('unreviewed batch RAM controller')
    if mode not in ('probe', 'capture'):
        raise ValueError('unknown mode')
    if not re.fullmatch(r'probe-[0-9]{6}' if mode == 'probe' else r'attempt-[0-9]{6}', attempt):
        raise ValueError('invalid attempt name')
    packet = {'schema': 'tb371fc-storage-ram-source-packet-v1', 'mode': mode, 'attempt_id': attempt, 'boot_id': BOOT}
    if mode == 'capture':
        if (len(caller), sha(caller)) != CALLER or (len(collector), sha(collector)) != COLLECTOR:
            raise ValueError('frozen memory source changed')
        if (payload['schema'] != 'tb371fc-storage-memory-sender-call-v2' or
            type(payload['max_new_chunks']) is not int or payload['max_new_chunks'] not in (1, 4) or
            payload['attempt_id'] != attempt or type(payload['source_deadline_seconds']) is not int or
            payload['source_deadline_seconds'] != 240):
            raise ValueError('explicit payload/deadline mismatch')
        packet.update(payload=payload, caller_base64=base64.b64encode(caller).decode(),
                      collector_base64=base64.b64encode(collector).decode())
    run_dir = RAM_ROOT + '/' + attempt
    code_pin = pin(run_dir + '/ram_source_controller_batch_v3.py', controller)
    packet_raw = encoded(packet); packet_pin = pin(run_dir + '/source-packet.json', packet_raw)
    common = "import base64,hashlib,json,os,stat,sys,types,subprocess,datetime\nfrom pathlib import Path\n"
    common += "raw=base64.b64decode(%r);assert len(raw)==%d and hashlib.sha256(raw).hexdigest()==%r\n" % (
        base64.b64encode(controller).decode(), len(controller), sha(controller))
    common += "m=types.ModuleType('verified_ram_controller');m.__file__=%r;exec(compile(raw,m.__file__,'exec'),m.__dict__);m.tmpfs_guard()\n" % code_pin['path']
    launch = common + "assert not Path('/AGENTS.md').exists() and not Path('/dev/AGENTS.md').exists()\n"
    launch += "root=Path(%r)\nif not root.exists():root.mkdir(mode=0o700)\nm.directory(root)\nassert not (root/'AGENTS.md').exists()\nrun_dir=root/%r;run_dir.mkdir(mode=0o700);m.directory(run_dir)\n" % (RAM_ROOT, attempt)
    launch += "m.save(run_dir/'ram_source_controller_batch_v3.py',raw)\npacket=base64.b64decode(%r);assert len(packet)==%d and m.sha(packet)==%r\nm.save(run_dir/'source-packet.json',packet)\n" % (
        base64.b64encode(packet_raw).decode(), len(packet_raw), sha(packet_raw))
    launch += "m.save(run_dir/'launch-once.json',m.encoded({'schema':'tb371fc-storage-ram-launch-once-v1','attempt_id':%r,'mode':%r,'utc':m.utc(),'automatic_retry':False}))\n" % (attempt, mode)
    launch += "fds=[]\nfor name in ('controller.stdout','controller.stderr'):fds.append(os.open(run_dir/name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_CLOEXEC|os.O_NOFOLLOW,0o600))\n"
    guard = 'import hashlib,pathlib,sys;p=pathlib.Path(sys.argv[1]);b=p.read_bytes();assert len(b)==int(sys.argv[2]) and hashlib.sha256(b).hexdigest()==sys.argv[3];sys.argv=[str(p)]+sys.argv[4:];exec(compile(b,str(p),"exec"),{"__name__":"__main__","__file__":str(p)})'
    argv_tail = ['-I', '-S', '-B', '-c', guard, code_pin['path'], str(code_pin['bytes']), code_pin['sha256'],
                 'run', '--packet', packet_pin['path'], '--sha256', packet_pin['sha256']]
    launch += "try:\n p=subprocess.Popen([sys.executable]+%r,stdin=subprocess.DEVNULL,stdout=fds[0],stderr=fds[1],start_new_session=True,close_fds=True)\n ident=m.process_identity(p.pid);assert ident is not None and os.getsid(p.pid)==p.pid\nfinally:\n for fd in fds:os.close(fd)\n" % argv_tail
    launch += "out={'schema':'tb371fc-storage-ram-controller-launch-v1','attempt_id':%r,'mode':%r,'controller_process':ident,'controller_session_id':p.pid,'packet':%r,'controller_code':%r,'stdin':'DEVNULL','stdout_stderr':'private tmpfs files','tool_pipes_inherited':False,'automatic_retry':False,'completed':False}\nm.save(run_dir/'launch-result.json',m.encoded(out))\nprint(json.dumps(out,sort_keys=True))\n" % (attempt, mode, packet_pin, code_pin)
    status = common + "m.status(%r)\n" % run_dir
    return {'source-packet.json': packet_raw, 'ram-launch-command.txt': shell(launch),
            'ram-status-command.txt': shell(status),
            'ram-preparation.json': encoded({'schema': 'tb371fc-storage-ram-launch-preparation-v1',
                'mode': mode, 'attempt_id': attempt, 'packet': packet_pin, 'controller': code_pin,
                'commands_executed': False, 'private_ram_directory': run_dir,
                'source_deadline_seconds': 240 if mode == 'capture' else None,
                'controller_overdue_seconds': 250, 'hard_termination_claimed': False,
                'first_gate': 'Root must verify a separate probe survives its launch tool return and is actually reaped before a capture launch.'})}


def write_outputs(out, files):
    out = Path(out)
    if not out.is_absolute():
        raise ValueError('absolute new output directory required')
    for parent in out.parents:
        if parent.is_symlink():raise ValueError('symlink output ancestor')
    out.mkdir(mode=0o700)
    pins = []
    for name, raw in files.items():
        fd = os.open(out / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        pins.append(pin(out / name, raw))
    print(json.dumps({'files': pins, 'commands_executed': False}, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('probe', 'capture'), required=True)
    parser.add_argument('--attempt', required=True);parser.add_argument('--controller', type=Path, required=True)
    parser.add_argument('--payload', type=Path);parser.add_argument('--caller', type=Path);parser.add_argument('--collector', type=Path)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    payload = json.loads(args.payload.read_bytes()) if args.payload else None
    files = build(args.mode, args.attempt, payload, args.caller.read_bytes() if args.caller else b'',
                  args.collector.read_bytes() if args.collector else b'', args.controller.read_bytes())
    write_outputs(args.out, files)


if __name__ == '__main__':
    main()
