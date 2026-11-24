"""Bounded read-only production Image and fixed native-log evidence."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess

ROOT = Path('/work/wholeboard-a47')
OUT = ROOT / 'rpmh-production-3b31e8'
IMAGE = Path('/work/device-assets/current-boot_a-kernel.Image')
SYMS = Path('/work/kernel-symbols/production-4.19.157.kallsyms')

def sha(data):
    return hashlib.sha256(data).hexdigest()

def read_checked(path, expected, limit):
    before = path.lstat()
    assert stat.S_ISREG(before.st_mode) and before.st_size <= limit
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        data = stream.read(limit + 1)
    after = path.lstat()
    assert (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
    assert len(data) == before.st_size and len(data) <= limit
    digest = sha(data)
    assert expected is None or digest == expected
    return data, {'path': str(path), 'bytes': len(data), 'sha256': digest, 'unchanged_across_read': True}

image, image_id = read_checked(IMAGE, 'ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f', 64 * 1024 * 1024)
symbols_raw, symbols_id = read_checked(SYMS, 'e32ef8e07019bd35105f7e2ddd6b7f47b9c23d828575afa6858b0ade37c49ab0', 32 * 1024 * 1024)
symbols = []
for line in symbols_raw.decode('ascii').splitlines():
    fields = line.split()
    if len(fields) >= 3:
        symbols.append((int(fields[0], 16), fields[1], fields[2]))
symbols.sort()
by_name = {}
for address, kind, name in symbols:
    by_name.setdefault(name, []).append((address, kind))
assert len(by_name.get('_text', [])) == 1
base = by_name['_text'][0][0]
assert image[56:60] == b'ARM\x64'
header = {'magic_hex': image[56:60].hex(), 'text_offset': struct.unpack_from('<Q', image, 8)[0], 'image_size': struct.unpack_from('<Q', image, 16)[0], 'flags': struct.unpack_from('<Q', image, 24)[0], 'symbol_mapping_base': f'0x{base:016x}', 'mapping': 'Image file offset = symbol virtual address minus _text; independently checked against exact fault instruction and preceding four instructions.'}
specs = [('__tcs_buffer_write', 0x268), ('rpmh_write', 0x198), ('msm_bus_dev_init_qos', 0x620), ('rpmh_rsc_send_data', 0x3e0), ('__rpmh_write', 0x3e8)]
fault_address = by_name['__tcs_buffer_write'][0][0] + 0x220
fault_offset = fault_address - base
expected_words = [0xf9400a68, 0xf85f83aa, 0x8b0a0108, 0x91004108, 0xb9000114]
assert list(struct.unpack_from('<5I', image, fault_offset - 16)) == expected_words
OUT.mkdir(exist_ok=False)
objdump = shutil.which('aarch64-linux-gnu-objdump')
tool = None
if objdump:
    version = subprocess.run([objdump, '--version'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10).stdout.decode().splitlines()[0]
    tool = {'path': objdump, 'version_first_line': version}
functions = []
for name, size in specs:
    found = by_name.get(name, [])
    assert len(found) == 1
    address, kind = found[0]
    offset = address - base
    assert 0 <= offset and offset + size <= len(image)
    following = next(item for item in symbols if item[0] > address)
    assert following[0] - address >= size
    raw = image[offset:offset + size]
    path = OUT / (name + '.bin')
    path.write_bytes(raw)
    item = {'name': name, 'symbol_type': kind, 'virtual_address': f'0x{address:016x}', 'image_offset': offset, 'captured_bytes': size, 'sha256': sha(raw), 'next_distinct_symbol': {'name': following[2], 'address': f'0x{following[0]:016x}', 'span': following[0] - address}, 'raw_hex': raw.hex()}
    if objdump:
        args = [objdump, '-D', '-b', 'binary', '-m', 'aarch64', '--adjust-vma=' + hex(address), str(path)]
        run = subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        text = run.stdout.decode('utf-8')
        assert not run.stderr
        (OUT / (name + '.disassembly.txt')).write_text(text)
        item['disassembly'] = text
    functions.append(item)
optional_names = ['bcm_commit_single_req', 'tcs_cmd_gen', 'vote_qos_bcms', 'rm_vote_qos_bcms', '_stext', 'stext']
optional_symbols = {name: [{'address': f'0x{address:016x}', 'type': kind} for address, kind in by_name.get(name, [])] for name in optional_names}
log, log_id = read_checked(ROOT / 'native-3b31e8/qemu.log', 'cc79143e896ee0533cd8eab0eb900ff61b81186ba7a2cf9898cef30f2a847f11', 4 * 1024 * 1024)
lines = log.decode('utf-8', 'replace').splitlines()
matches = [i for i, line in enumerate(lines) if 'DRV2 unsupported write @0x0d10 value=0x778bd800' in line]
assert len(matches) == 1
i = matches[0]
report = {'schema': 'tb371fc-production-rpmh-disassembly-v1', 'captured_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'source_commit': '3b31e83dc412962826ada872e361a1d32ea4e463', 'image': image_id, 'symbols': symbols_id, 'image_header': header, 'fault': {'virtual_address': f'0x{fault_address:016x}', 'image_offset': fault_offset, 'words_including_four_preceding': [f'0x{w:08x}' for w in expected_words], 'opcode': 'str w20, [x8]', 'matched_actual_native_code_dump': True}, 'disassembler': tool, 'functions': functions, 'optional_symbols': optional_symbols, 'qemu_log': log_id, 'log_context': [{'line': n + 1, 'text': lines[n]} for n in range(max(0, i - 24), min(len(lines), i + 25))], 'scope': {'original_assets_written': False, 'qemu_started': False, 'guest_memory_written': False, 'hardware_accessed': False, 'built_code': False, 'function_sizes_from_native_backtrace': True, 'next_symbol_span_is_not_elf_size': True, 'source_path_equivalence_already_proved': False, 'reserved_bit_silicon_semantics_proved': False}}
output = OUT / 'report.json'
output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
print(json.dumps({'report_path': str(output), 'bytes': output.stat().st_size, 'sha256': sha(output.read_bytes()), 'functions': len(functions), 'captured_code_bytes': sum(x['captured_bytes'] for x in functions), 'objdump_available': objdump is not None, 'fault_words_matched': True}))
