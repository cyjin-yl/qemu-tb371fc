"""Optional fixed-file symbol evidence only; no DT/device/guest access."""
import hashlib
import json
from pathlib import Path

path = Path('/work/kernel-symbols/production-4.19.157.kallsyms')
raw = path.read_bytes()
digest = hashlib.sha256(raw).hexdigest()
assert digest == 'e32ef8e07019bd35105f7e2ddd6b7f47b9c23d828575afa6858b0ade37c49ab0'
pc = 0xffffff80086a6844
names = {'msm_bus_noc_sbm_config', 'msm_bus_dev_sbm_config',
         'msm_bus_commit_data', 'gdsc_probe'}
symbols = []
for line in raw.decode('ascii').splitlines():
    parts = line.split()
    if len(parts) >= 3:
        symbols.append((int(parts[0], 16), parts[1], parts[2]))
symbols.sort()
selected = []
for address, kind, name in symbols:
    if name not in names:
        continue
    successor = next((item for item in symbols if item[0] > address), None)
    selected.append({
        'name': name, 'type': kind, 'address': f'0x{address:016x}',
        'fault_pc_offset': pc - address,
        'next_distinct_symbol': None if successor is None else {
            'address': f'0x{successor[0]:016x}', 'type': successor[1],
            'name': successor[2], 'address_span': successor[0] - address},
    })
primary = [entry for entry in selected if entry['name'] == 'msm_bus_noc_sbm_config']
assert len(primary) == 1 and primary[0]['fault_pc_offset'] == 0x84
print(json.dumps({
    'scope': 'Read only the fixed existing production kallsyms; bounded named output. The next symbol span is not an independent ELF function-size measurement.',
    'input_symbols': {'path': str(path), 'bytes': len(raw), 'sha256': digest},
    'fault_pc': f'0x{pc:016x}', 'selected_symbols': selected,
    'dt_read': False, 'hardware_mmio_read': False, 'guest_memory_written': False,
    'qemu_started': False,
}, sort_keys=True))
