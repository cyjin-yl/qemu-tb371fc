from pathlib import Path
import hashlib
import json
import subprocess
import sys

root = Path('/tank/shared/projects/tb371fc-wholeboard-a47')
label = 'native-09c638'
source = '09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e'
artifact = json.loads((root / (label + '-artifact.json')).read_text())
summary = json.loads((root / (label + '-summary.json')).read_text())
assert artifact['source'] == summary['source_commit'] == source
assert artifact['workflow_run'] == 45264653096
assert summary['qemu_unchanged'] is True
qemu = artifact['qemu_sha256']
assert summary['qemu_sha256'] == qemu
collector = (root / (label + '-extra-collector.py')).read_bytes()
assert hashlib.sha256(collector).hexdigest() == '4be2a5901bdf9919fb2c416308c4c6bd6c0290fe9f65e0a0a8a5fb7df528bd33'
base = ['sudo', '-n', '/usr/sbin/pct', 'exec', '102', '--', 'docker',
        'exec', '-i', 'qemu-tb371fc-dev', 'python3', '-B', '-c']
subprocess.run(base + [collector.decode(), label, qemu, source], check=True, timeout=40)
copy = "from pathlib import Path;import sys;p=Path('/work/wholeboard-a47')/(sys.argv[1]+'-extra.json');sys.stdout.buffer.write(p.read_bytes())"
raw = subprocess.check_output(base + [copy, label], timeout=40)
result = json.loads(raw)
assert result['source_commit'] == source and result['qemu_sha256'] == qemu
path = root / (label + '-extra.json')
with path.open('xb') as stream:
    stream.write(raw)
print(json.dumps({'path': str(path), 'bytes': len(raw),
                  'sha256': hashlib.sha256(raw).hexdigest(),
                  'invalid_mem_count': result['invalid_mem_count'],
                  'device_error_count': result['device_error_count'],
                  'kernel_fatal_observations': result['kernel_fatal_observations'],
                  'wait_masked_log_count': result['rpmh_reference_wait_write_count_from_log'],
                  'kernel_last_milestones': result['kernel_milestones'][-18:]}), flush=True)
