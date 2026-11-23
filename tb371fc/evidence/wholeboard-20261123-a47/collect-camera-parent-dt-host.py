from pathlib import Path
import hashlib
import json
import subprocess

host = Path('/tank/shared/projects/tb371fc-wholeboard-a47')
source = host / 'collect-camera-parent-dt-09c638.py'
raw = source.read_bytes()
expected = 'db7d781809cd3c431db6af9ae15162a8613336de0205cf302b77fe1ed54f43bf'
assert hashlib.sha256(raw).hexdigest() == expected
ct_script = '/work/wholeboard-a47/collect-camera-parent-dt-09c638.py'
ct_output = '/work/wholeboard-a47/camera-parent-dt-evidence-09c638.json'
base = ['sudo', '-n', '/usr/sbin/pct', 'exec', '102', '--', 'docker', 'exec', '-i', 'qemu-tb371fc-dev']
install = "from pathlib import Path;import sys,hashlib;raw=sys.stdin.buffer.read();assert hashlib.sha256(raw).hexdigest()==sys.argv[2];p=Path(sys.argv[1]);f=p.open('xb');f.write(raw);f.close();p.chmod(0o444);assert p.read_bytes()==raw"
subprocess.run(base + ['python3', '-B', '-c', install, ct_script, expected], input=raw, check=True, timeout=30)
run = subprocess.run(base + ['python3', '-B', ct_script, '--out', ct_output], capture_output=True, check=True, timeout=30)
receipt = json.loads(run.stdout)
assert receipt['output'] == ct_output and receipt['guest_executed'] is False
copy = "from pathlib import Path;import sys;sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())"
payload = subprocess.check_output(base + ['python3', '-B', '-c', copy, ct_output], timeout=30)
assert len(payload) == receipt['bytes'] and hashlib.sha256(payload).hexdigest() == receipt['sha256']
target = host / 'camera-parent-dt-evidence-09c638.json'
with target.open('xb') as stream:
    stream.write(payload)
target.chmod(0o444)
print(json.dumps({**receipt, 'host_output': str(target), 'lines': len(payload.splitlines()),
                  'collector_sha256': expected}), flush=True)
