#!/usr/bin/env python3
"""One foreground invocation in the dedicated tmux pane; retain its output."""
from pathlib import Path
import datetime
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path('/tank/shared/projects/qemu-tb371fc/.git/tb371fc-support-6a5-control')
TAG = 'llcc-c02f3d7-diagnostic-f093'
PINS = {
    'own-lane-llcc-coordinator-f093b2c8-gitmeta.py': 'd38832149efe0e1c872a214d1c1430fc6ad20ee668ab1e6d3a26d03275c3d90e',
    'binding-c02f3d7-qualified-artifact.json': '4c2bf8cc78a5f493faa01e8f351e213cae62c1e0aefc4bc39940880e607686f0',
    'llcc-c02f3d7-qualification.json': '6c3ca7cfabe3c3992c9a5795bda3fe7c40ecfbf6d1b547e09089a76a1f8461ea',
}
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == 'f093b2c8-08ac-413b-92ea-f2c1efd77c5a'
assert os.environ.get('TMUX') and os.environ.get('TMUX_PANE')
for name, expected in PINS.items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
command = ['/home/ezra/.omp/agent/bin/agent-heavy', 'python3', '-I', '-S',
           str(ROOT / 'own-lane-llcc-coordinator-f093b2c8-gitmeta.py'), 'host', '--host',
           '--commit', 'c02f3d794e85cb5bc95d1aca62780542e806d4dc', '--run-id', '45501927279',
           '--tag', TAG, '--binding', str(ROOT / 'binding-c02f3d7-qualified-artifact.json'),
           '--binding-sha256', PINS['binding-c02f3d7-qualified-artifact.json'],
           '--ci-wait-seconds', '120']
os.umask(0o077)
with (ROOT / (TAG + '-terminal.log')).open('xb', buffering=0) as log:
    header = (json.dumps({'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
                         'tag': TAG, 'foreground': True, 'automatic_retry': False}) + '\n').encode()
    log.write(header)
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.flush()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    while True:
        block = process.stdout.read(8192)
        if not block:
            break
        log.write(block)
        sys.stdout.buffer.write(block)
        sys.stdout.buffer.flush()
    code = process.wait()
    trailer = (json.dumps({'foreground_exit_code': code,
                          'utc': datetime.datetime.now(datetime.timezone.utc).isoformat()}) + '\n').encode()
    log.write(trailer)
    os.fsync(log.fileno())
    sys.stdout.buffer.write(trailer)
    sys.stdout.buffer.flush()
raise SystemExit(code)
