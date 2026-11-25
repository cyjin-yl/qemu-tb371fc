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
TAG = 'spss-54ec744-diagnostic-f093'
PINS = {
    'own-lane-spss-coordinator-f093b2c8-gitmeta.py': '69429d845b65162617ee8253fc7762764952c853318b6cc37f36520362baf930',
    'spss-54ec744-binding.json': 'efdcb0f739c52904a1a6204b57dfe6f6d297b1a84c7c20214467330958d16a47',
    'spss-54ec744-qualification.json': '0a52a79598ff4f57e1697b7e8327d5229b3b2491e8947f7142c1e2bbc97d5206',
}
assert Path('/proc/sys/kernel/random/boot_id').read_text().strip() == 'f093b2c8-08ac-413b-92ea-f2c1efd77c5a'
assert os.environ.get('TMUX') and os.environ.get('TMUX_PANE')
for name, expected in PINS.items():
    assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
command = ['/home/ezra/.omp/agent/bin/agent-heavy', 'python3', '-I', '-S',
           str(ROOT / 'own-lane-spss-coordinator-f093b2c8-gitmeta.py'), 'host', '--host',
           '--commit', '54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7', '--run-id', '45521455233',
           '--tag', TAG, '--binding', str(ROOT / 'spss-54ec744-binding.json'),
           '--binding-sha256', PINS['spss-54ec744-binding.json'],
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
