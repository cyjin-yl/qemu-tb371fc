#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Literal architectural/guest/wire contract; no QEMU or work on import.

The register oracle comes from the approved zero-records ABI and AArch64
encodings, not from parsing the candidate C implementation. The stream parser
walks the complete QEMU v3 framing and all declared fields before identifying
CPU v22 data. VMDESCRIPTION supplies sizes for unrelated devices, never the
expected RAS register IDs, values, CPU count, or profile layout.
"""
import hashlib
import json
import struct

SCHEMA = 'tb371fc-ras-zero-records-compiled-v1'
NAME = 'ras-zero-records-compat'
CPU_PROP = 'tb371fc-ras-zero-records-compat'
PROFILE_PROP = 'tb371fc-ras-zero-records-profile'
SUBSECTION = 'cpu/tb371fc-ras-zero-records-compat'
CONTROL = 0x80200000
BOX_BASE, BOX_STRIDE, BOX_BYTES = 0x80210000, 0x8000, 16512
MAGIC, SENTINEL, U64 = 0x525a524153505230, 0x52a5c33cdeadbeef, (1 << 64) - 1
NONCE = 0x5241530000000000
REGISTERS = (
    ('erridr', 3, 0, 5, 3, 0, False),
    ('errselr', 3, 0, 5, 3, 1, True),
    ('erxfr', 3, 0, 5, 4, 0, False),
    ('erxctlr', 3, 0, 5, 4, 1, True),
    ('erxstatus', 3, 0, 5, 4, 2, True),
    ('erxaddr', 3, 0, 5, 4, 3, True),
    ('erxmisc0', 3, 0, 5, 5, 0, True),
    ('erxmisc1', 3, 0, 5, 5, 1, True),
    ('unknown0', 3, 0, 5, 4, 4, False),
    ('unknown1', 3, 0, 5, 5, 7, False),
)
NEW_KVM_IDS = (0x603000000013c299, 0x603000000013c2a0,
               0x603000000013c2a1, 0x603000000013c2a2,
               0x603000000013c2a3, 0x603000000013c2a8,
               0x603000000013c2a9)
ERRIDR_KVM_ID = 0x603000000013c298
HEADER = ('magic', 'abi', 'cpu', 'mpidr', 'midr', 'pfr0', 'boot_el', 'epoch',
          'count', 'fatal', 'active', 'done', 'nonce', 'hvc_returns', 'svc_returns', 'reserved')
EVENT = ('seq', 'phase', 'el', 'reg', 'write', 'input', 'output', 'pc',
         'traps', 'trap_el', 'esr', 'elr', 'spsr', 'far', 'complete', 'reserved')


def require(ok, reason):
    if not ok:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def reg_encoding(reg):
    _, op0, op1, crn, crm, op2, _ = REGISTERS[reg]
    return (op0 << 14) | (op1 << 11) | (crn << 7) | (crm << 3) | op2


def expected_operations():
    result = []
    def add(phase, el, reg, wr=False, value=SENTINEL):
        result.append((phase, el, reg, int(wr), value))
    def full(phase, el):
        for reg in range(8): add(phase, el, reg)
        for reg in (1, 3, 4, 5, 6, 7):
            add(phase, el, reg, True, U64); add(phase, el, reg)
        for selector in (0, 1, 65535):
            add(phase, el, 1, True, selector); add(phase, el, 1)
        for reg, value in ((3, 0x10f), (6, 0x7f7f00000000)):
            add(phase, el, reg, True, value); add(phase, el, reg)
        for reg in (0, 2): add(phase, el, reg, True, U64)
        for reg in (8, 9):
            add(phase, el, reg); add(phase, el, reg, True, U64)
    full(1, 1)
    for reg in range(10):
        add(2, 0, reg); add(2, 0, reg, True, U64)
    full(3, 2)
    for reg in range(8): add(4, 1, reg)
    for reg in (1, 3, 4, 5, 6, 7, 0, 2): add(4, 1, reg, True, U64)
    for reg in (8, 9):
        add(4, 1, reg); add(4, 1, reg, True, U64)
    require(len(result) == 112, 'literal guest operation count')
    return result


def expected_exception(mode, phase, el, reg, wr):
    implemented = reg == 0 or (mode and 1 <= reg <= 7)
    legal = implemented and el >= 1 and (not wr or REGISTERS[reg][-1])
    if not legal:
        return (1 if el == 0 else el), 0x02000000
    if phase == 4:  # HCR_EL2.TERR set, legal EL1 access only
        _, op0, op1, crn, crm, op2, _ = REGISTERS[reg]
        esr = ((0x18 << 26) | (1 << 25) | (op0 << 20) | (op2 << 17) |
               (op1 << 14) | (crn << 10) | (crm << 1) | int(not wr))
        return 2, esr
    return None


def parse_probe_elf(raw):
    require(raw[:7] == b'\x7fELF\x02\x01\x01', 'ELF64 little-endian probe')
    h = struct.unpack_from('<16sHHIQQQIHHHHHH', raw)
    _, typ, machine, version, entry, phoff, shoff, flags, eh, phsize, phnum, shsize, shnum, shstr = h
    require((typ, machine, version, eh, phsize, shsize) == (2, 183, 1, 64, 56, 64), 'ELF identity')
    require(0 < phnum < 32 and 0 < shnum < 128, 'ELF bounded tables')
    require(phoff + phnum * phsize <= len(raw) and shoff + shnum * shsize <= len(raw), 'ELF table bounds')
    segments = []
    for i in range(phnum):
        t, fl, off, va, pa, size, mem, align = struct.unpack_from('<IIQQQQQQ', raw, phoff + i * phsize)
        if t != 1: continue
        require(va == pa and 0x80010000 <= pa < pa + mem <= CONTROL and size <= mem,
                'probe load segment outside test RAM')
        require(off + size <= len(raw), 'ELF segment truncation')
        segments.append(dict(address=pa, offset=off, file_bytes=size, memory_bytes=mem, flags=fl))
    require(segments, 'no ELF load segments')
    symbols = {}
    for i in range(shnum):
        sh = struct.unpack_from('<IIQQQQIIQQ', raw, shoff + i * shsize)
        if sh[1] != 2: continue
        _, _, _, _, off, size, link, _, _, entsize = sh
        require(entsize == 24 and size % 24 == 0 and link < shnum and off + size <= len(raw), 'symbol table')
        string_sh = struct.unpack_from('<IIQQQQIIQQ', raw, shoff + link * shsize)
        strings = raw[string_sh[4]:string_sh[4] + string_sh[5]]
        require(len(strings) == string_sh[5], 'string table truncation')
        for at in range(off, off + size, 24):
            name, info, other, index, value, extent = struct.unpack_from('<IBBHQQ', raw, at)
            require(name < len(strings), 'symbol name range')
            end = strings.find(b'\0', name)
            require(end >= name, 'symbol name terminator')
            key = strings[name:end].decode('ascii')
            if key in ('_start', 'secondary_entry', 'enter_el1', 'enter_el0', 'el1_return_hvc',
                       'el0_return_svc', 'el2_return', 'el1_return') or key.startswith(('read_', 'write_')):
                require(key not in symbols and index != 0, 'duplicate/undefined probe symbol')
                symbols[key] = value
    require(symbols.get('_start') == entry == 0x80010000, 'probe entry')
    instructions = {}
    for reg, row in enumerate(REGISTERS):
        for wr in (0, 1):
            key = ('write_' if wr else 'read_') + row[0]
            address = symbols.get(key)
            require(type(address) is int and address % 4 == 0, 'missing instruction symbol ' + key)
            matches = [s for s in segments if s['address'] <= address and address + 8 <= s['address'] + s['file_bytes']]
            require(len(matches) == 1 and matches[0]['flags'] & 1, 'instruction segment ' + key)
            s = matches[0]; off = s['offset'] + address - s['address']
            insn, ret = struct.unpack_from('<II', raw, off)
            expected = (0xd5000000 if wr else 0xd5200000) | (reg_encoding(reg) << 5)
            require(insn == expected and ret == 0xd65f03c0, 'ELF tested instruction differs: ' + key)
            instructions[key] = {'address': address, 'instruction_u32': insn, 'file_offset': off}
    return {'bytes': len(raw), 'sha256': digest(raw), 'entry': entry,
            'load_segments': segments, 'symbols': symbols, 'instructions': instructions}


def decode_guest(raw, cpu, mode, epoch, elf):
    require(type(mode) is bool and cpu in range(8) and epoch in (1, 2, 3), 'guest expectation identity')
    require(len(raw) == BOX_BYTES, 'complete CPU mailbox required')
    h = dict(zip(HEADER, struct.unpack_from('<16Q', raw)))
    expected = dict(magic=MAGIC, abi=1, cpu=cpu, mpidr=0x80000000 | (cpu << 8),
                    midr=0x412fd050 if cpu < 4 else 0x411fd0d0, boot_el=8, epoch=epoch,
                    count=112, fatal=0, active=U64, done=epoch, nonce=NONCE | epoch,
                    hvc_returns=2, svc_returns=1, reserved=0)
    for key, value in expected.items():
        require(h[key] == value, f'CPU{cpu} {key}: {h[key]:#x} != {value:#x}')
    require((h['pfr0'] >> 28) & 15 == 1, 'guest RASv1 feature field')
    events = []
    for i, (phase, el, reg, wr, value) in enumerate(expected_operations()):
        e = dict(zip(EVENT, struct.unpack_from('<16Q', raw, 128 + i * 128)))
        key = ('write_' if wr else 'read_') + REGISTERS[reg][0]
        pc = elf['instructions'][key]['address']
        fixed = dict(seq=i, phase=phase, el=el, reg=reg, write=wr, input=value, pc=pc, complete=1, reserved=0)
        for field, wanted in fixed.items():
            require(e[field] == wanted, f'CPU{cpu} event{i} {field}')
        exception = expected_exception(mode, phase, el, reg, wr)
        output = value if wr or exception else 0
        require(e['output'] == output, f'CPU{cpu} event{i} actual returned value')
        if exception is None:
            require(all(e[k] == 0 for k in ('traps', 'trap_el', 'esr', 'elr', 'spsr', 'far')),
                    f'CPU{cpu} event{i} unexpected exception')
        else:
            trap_el, esr = exception
            require((e['traps'], e['trap_el'], e['esr'], e['elr']) == (1, trap_el, esr, pc),
                    f'CPU{cpu} event{i} exact exception')
            require(e['spsr'] & 0x3cf == (0x3c0 if el == 0 else 0x3c5 if el == 1 else 0x3c9),
                    f'CPU{cpu} event{i} interrupted EL/DAIF')
            require(e['far'] == 0, f'CPU{cpu} event{i} unexpected data/instruction abort')
        events.append(e)
    require(raw[128 + 112 * 128:] == bytes(16 * 128), 'unused mailbox event bytes changed')
    return {'cpu': cpu, 'header': h, 'events': events, 'bytes': len(raw), 'sha256': digest(raw),
            'instruction_operations': 112, 'actual_exceptions': sum(e['traps'] for e in events)}


class Stream:
    """Complete file framing, RAM pages, fields, footer and trailer bounds."""
    def __init__(self, raw):
        self.raw, self.pos, self.cpus = raw, 0, []
        self.sections = {}
        self.ram_sizes = {}
        self.ram_name = None
        self.ram_pages = 0
        # A trailer candidate must end exactly at EOF, decode as JSON, and
        # later be reached by the forward parser. No CPU byte-pattern scan.
        candidates = []
        start = max(8, len(raw) - 16 * 1024 * 1024)
        at = raw.find(b'\x00\x06', start)
        while at >= 0:
            if at + 6 <= len(raw):
                size = int.from_bytes(raw[at + 2:at + 6], 'big')
                if at + 6 + size == len(raw):
                    try:
                        desc = json.loads(raw[at + 6:])
                        if type(desc) is dict and type(desc.get('devices')) is list:
                            candidates.append((at, desc))
                    except (ValueError, UnicodeDecodeError):
                        pass
            at = raw.find(b'\x00\x06', at + 1)
        require(len(candidates) == 1, 'one complete VMDESCRIPTION trailer')
        self.end, self.desc = candidates[0]
        require(self.desc.get('page_size') == 4096, 'migration page size')
        self.devices = {}
        for device in self.desc['devices']:
            key = (device['name'], device['instance_id'])
            require(key not in self.devices and type(device.get('fields')) is list, 'duplicate/invalid VMDESCRIPTION device')
            self.devices[key] = device

    def take(self, n):
        require(type(n) is int and n >= 0 and self.pos + n <= len(self.raw), 'stream truncation')
        value = self.raw[self.pos:self.pos + n]; self.pos += n
        return value

    def uint(self, n): return int.from_bytes(self.take(n), 'big')
    def string(self): return self.take(self.uint(1)).decode('ascii')

    def fields(self, description):
        result = {'start': self.pos, 'fields': [], 'subsections': []}
        for field in description['fields']:
            count = field.get('array_len', 1)
            require(type(count) is int and 0 <= count < (1 << 20), 'field array bound')
            for i in range(count):
                start = self.pos
                typ = field['type']
                nested = None
                if typ == 'struct':
                    nested = self.fields(field['struct'])
                elif typ == 'capability':
                    self.take(self.uint(1))
                else:
                    self.take(field['size'])
                result['fields'].append({'name': field['name'], 'type': typ,
                    'index': field.get('index', 0) + i, 'start': start,
                    'size': self.pos - start, 'nested': nested})
        for sub in description.get('subsections', []):
            start = self.pos
            require(self.uint(1) == 5, 'missing subsection marker')
            name = self.string(); version = self.uint(4)
            require(name == sub['vmsd_name'] and version == sub['version'], 'subsection header/description disagreement')
            body = self.fields(sub)
            result['subsections'].append({'name': name, 'version': version, 'start': start,
                                          'body_start': body['start'], 'end': self.pos, 'body': body})
        result['end'] = self.pos
        return result

    def ram(self):
        while True:
            word = self.uint(8); flags = word & 4095; addr = word & ~4095
            if flags & 4:
                remaining = addr
                while remaining:
                    name = self.string(); size = self.uint(8)
                    require(name not in self.ram_sizes and 0 < size <= remaining and size % 4096 == 0, 'RAM block size')
                    self.ram_sizes[name] = size; remaining -= size
                flags &= ~4
            if flags & (2 | 8):
                require((flags & (2 | 8)) in (2, 8), 'RAM encoding conflict')
                if flags & 32:
                    flags &= ~32
                else:
                    self.ram_name = self.string()
                require(self.ram_name in self.ram_sizes and addr + 4096 <= self.ram_sizes[self.ram_name], 'RAM page identity/range')
                self.take(1 if flags & 2 else 4096)
                self.ram_pages += 1
                flags &= ~(2 | 8)
            if flags == 0x200: continue
            if flags & 16:
                require(flags == 16, 'RAM EOS extra flags'); return
            require(flags == 0, 'unsupported migration RAM flag')

    def cpu(self, instance, version, body, section_start, sid):
        require(instance in range(8) and version == 22, 'CPU section identity/version')
        base = body['start']; fields = body['fields']
        byname = {}
        for f in fields: byname.setdefault(f['name'], []).append(f)
        require(len(byname.get('cpreg_vmstate_array_len', [])) == 1, 'CPU CPreg count field')
        count_field = byname['cpreg_vmstate_array_len'][0]
        require((count_field['start'] - base, count_field['size']) == (568, 4), 'CPU v22 prefix length')
        count = int.from_bytes(self.raw[base + 568:base + 572], 'big', signed=True)
        require(0 < count < 65536, 'CPU CPreg count bound')
        indexes = byname.get('cpreg_vmstate_indexes', []); values = byname.get('cpreg_vmstate_values', [])
        require(len(indexes) == len(values) == count, 'complete CPU CPreg arrays')
        actual_ids, actual_values, offsets = [], [], []
        for i, (ix, val) in enumerate(zip(indexes, values)):
            require((ix['start'], ix['size'], val['start'], val['size']) ==
                    (base + 572 + 8 * i, 8, base + 572 + 8 * count + 8 * i, 8), 'CPU CPreg offsets')
            actual_ids.append(int.from_bytes(self.raw[ix['start']:ix['start'] + 8], 'big'))
            actual_values.append(int.from_bytes(self.raw[val['start']:val['start'] + 8], 'big'))
            offsets.append(val['start'])
        require(actual_ids == sorted(set(actual_ids)), 'CPreg indexes sorted/unique')
        require(fields[-1]['start'] + fields[-1]['size'] == base + 637 + count * 16, 'CPU v22 complete base size')
        require(ERRIDR_KVM_ID in actual_ids and actual_values[actual_ids.index(ERRIDR_KVM_ID)] == 0, 'existing ERRIDR migration zero')
        ras = [s for s in body['subsections'] if s['name'] == SUBSECTION]
        require(len(ras) <= 1, 'duplicate RAS profile subsection')
        profile = None
        if ras:
            s = ras[0]
            require(s['version'] == 1 and s['end'] - s['body_start'] == 4, 'RAS subsection v1 exact four bytes')
            fs = s['body']['fields']
            require(len(fs) == 1 and (fs[0]['name'], fs[0]['size']) == ('tb371fc_ras_zero_records_profile', 4), 'RAS subsection field')
            profile = int.from_bytes(self.raw[s['body_start']:s['end']], 'big')
            require(profile == 1, 'RAS canonical profile word')
        entries = []
        for regid in NEW_KVM_IDS:
            if regid in actual_ids:
                i = actual_ids.index(regid)
                require(actual_values[i] == 0, 'nonzero outgoing RAS CONST register')
                entries.append({'id': regid, 'value': 0, 'value_offset': offsets[i]})
        require(len(entries) == (7 if ras else 0), 'mode and seven CPreg entries disagree')
        self.cpus.append({'instance': instance, 'version': version, 'section_id': sid,
            'section_start': section_start, 'body_start': base, 'body_end': body['end'],
            'base_bytes': 637 + count * 16, 'cpreg_count': count, 'cpreg_ids': actual_ids,
            'cpreg_values': actual_values, 'new_registers': entries, 'profile': profile,
            'subsections': [{k: s[k] for k in ('name', 'version', 'start', 'body_start', 'end')} for s in body['subsections']],
            'body_sha256': digest(self.raw[base:body['end']])})

    def parse(self):
        require(self.take(8) == b'QEVM\0\0\0\x03', 'migration magic/version3')
        last_sid = None
        while self.pos < self.end:
            start = self.pos; typ = self.uint(1)
            if typ == 7:
                require(not self.sections, 'configuration ordering')
                if 'configuration' in self.desc:
                    self.fields(self.desc['configuration'])
                else:
                    require(self.take(self.uint(4)) == b'tb371fc', 'machine configuration')
            elif typ in (1, 4):
                sid = self.uint(4); name = self.string(); instance = self.uint(4); version = self.uint(4)
                require(sid not in self.sections, 'duplicate section ID')
                key = (name, instance)
                self.sections[sid] = (key, version)
                if key == ('ram', 0):
                    require(typ == 1 and version == 4, 'RAM section header'); self.ram()
                else:
                    require(typ == 4 and key in self.devices, 'unknown full VMState section')
                    desc = self.devices[key]
                    require(desc['version'] == version, 'VMState section version disagreement')
                    body = self.fields(desc)
                    if name == 'cpu': self.cpu(instance, version, body, start, sid)
                last_sid = sid
            elif typ in (2, 3):
                sid = self.uint(4)
                require(self.sections.get(sid) == (('ram', 0), 4), 'unexpected iterative device')
                self.ram(); last_sid = sid
            elif typ == 0x7e:
                require(last_sid is not None and self.uint(4) == last_sid, 'section footer mismatch')
                last_sid = None
            elif typ == 8:
                require(self.uint(2) == 11 and self.uint(2) == 0, 'unexpected migration command')
            else:
                raise ValueError(f'unknown migration marker {typ} at {start}')
        require(self.pos == self.end and self.uint(1) == 0 and self.uint(1) == 6, 'forward parser did not reach exact trailer')
        size = self.uint(4); require(self.take(size) == self.raw[self.end + 6:] and self.pos == len(self.raw), 'complete trailer')
        require(sorted(c['instance'] for c in self.cpus) == list(range(8)), 'eight unique complete CPU sections')
        self.cpus.sort(key=lambda c: c['instance'])
        return {'bytes': len(self.raw), 'sha256': digest(self.raw), 'cpu_sections': self.cpus,
                'ram_blocks': self.ram_sizes, 'ram_pages_parsed': self.ram_pages,
                'complete_forward_parse': True, 'all_bytes_consumed': True}


def parse_stream(raw, mode):
    result = Stream(raw).parse()
    require(all(c['profile'] == (1 if mode else None) for c in result['cpu_sections']), 'all eight migration profiles')
    return result
