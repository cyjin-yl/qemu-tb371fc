#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Inspect the authorized private active-slot AOP image's static command tables.

No firmware bytes/code are emitted. This version deliberately pins the one
examined image. It validates ELF segments, table descriptors, entry/aux bounds,
resource types, and the decoded initialization call sites. The resulting
command-db file is PARTIAL and RECONSTRUCTED, not a dump of live command-db RAM.
Only --private-output receives that generated binary; --receipt gets derived
resource metadata suitable for review. VRM resources are not reconstructed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

FIRMWARE_SHA256 = '3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671'
GROUPS = [
    dict(kind='ARC levels/timers', slave=3, descriptor=0x1868c,
         expected_count=22, expected_aux_size=0x140, call=0x2f3b6),
    dict(kind='ARC minimum operating levels', slave=3, descriptor=0x18698,
         expected_count=10, expected_aux_size=20, call=0x2f3c2),
    dict(kind='BCM', slave=5, descriptor=0x18680,
         expected_count=37, expected_aux_size=0x128, call=0x2f3d6),
]


def require(test, message):
    if not test:
        raise ValueError(message)


def inspect(firmware, private_output, receipt):
    raw = firmware.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    require(sha == FIRMWARE_SHA256, 'Unexamined firmware revision: do not infer offsets')
    require(raw[:7] == b'\x7fELF\x01\x01\x01', 'Expected little-endian ELF32')
    h = struct.unpack_from('<16sHHIIIIIHHHHHH', raw)
    require(h[2] == 40 and h[9] == 32 and h[10] == 7, 'Unexpected ARM ELF headers')
    segments = [struct.unpack_from('<8I', raw, h[5] + i * h[9]) for i in range(h[10])]

    def physical_to_file(addr, length):
        found = [off + addr - phys for kind, off, _, phys, file_size, _, _, _ in segments
                 if kind == 1 and addr >= phys and addr + length <= phys + file_size]
        require(len(found) == 1, f'No unique loaded segment for {addr:#x}+{length:#x}')
        require(found[0] + length <= len(raw), 'ELF mapping exceeds file')
        return found[0]

    parsed = []
    resources = []
    names = set()
    for group in GROUPS:
        count, aux_size, aux_ptr, headers_ptr = struct.unpack_from('<HHII', raw, group['descriptor'])
        require((count, aux_size) == (group['expected_count'], group['expected_aux_size']),
                'Static group descriptor changed')
        aux_file = physical_to_file(aux_ptr, aux_size)
        header_file = physical_to_file(headers_ptr, count * 24)
        # Thumb: movs r0,#slave ; mov.w r1,#0x1000, at known constructor calls.
        require(raw[group['call']:group['call'] + 6] ==
                bytes([group['slave'], 0x20, 0x4f, 0xf4, 0x80, 0x51]),
                'Constructor no longer passes this slave and version 16.0')
        entries = []
        for i in range(count):
            off = header_file + i * 24
            ident, priority0, priority1, address, length, offset = struct.unpack_from('<8sIIIHH', raw, off)
            name = ident.rstrip(b'\0').decode('ascii')
            require(re.fullmatch(r'[A-Za-z][A-Za-z0-9_.]{1,7}', name), 'Invalid resource name')
            require(name not in names, 'Duplicate resource ID')
            names.add(name)
            require(offset + length <= aux_size, 'Auxiliary data escapes its descriptor')
            require(address == 0 or address >> 16 == group['slave'], 'Resource/slave mismatch')
            require(not address & 3, 'Unaligned resource address')
            aux_data = raw[aux_file + offset:aux_file + offset + length]
            row = dict(name=name, address=address, address_hex=hex(address),
                       slave=group['slave'], entry_file_offset=off,
                       aux_file_offset=aux_file + offset, aux_size=length,
                       aux_hex=aux_data.hex(), priority=[priority0, priority1])
            if group['slave'] == 5:
                require(length == 8, 'BCM ABI requires eight aux bytes')
                unit, width, vcd, reserved = struct.unpack('<IHBB', aux_data)
                row['bcm'] = dict(unit=unit, width=width, vcd=vcd, reserved=reserved)
            elif length:
                require(not length & 1, 'ARC aux must contain little-endian u16 values')
                row['arc_levels_or_mol'] = list(struct.unpack('<' + 'H' * (length // 2), aux_data))
            entries.append(row)
            resources.append(row)
        parsed.append(dict(**group, count=count, aux_size=aux_size,
                           aux_physical=aux_ptr, headers_physical=headers_ptr,
                           aux_file_offset=aux_file, headers_file_offset=header_file,
                           version=0x1000, entries=entries))

    # Canonical ABI reconstruction. The unobserved live header/checksum and
    # dynamic VRM group are NOT represented as captured data.
    image = bytearray(0x20000)
    struct.pack_into('<II', image, 0, 1, 0x0c0330db)
    cursor = 144
    for group_index, slave in enumerate((3, 5)):
        rows = [r for r in resources if r['slave'] == slave]
        headers = cursor
        cursor += len(rows) * 24
        aux = cursor
        for i, row in enumerate(rows):
            data = bytes.fromhex(row['aux_hex'])
            struct.pack_into('<8sIIIHH', image, headers + i * 24,
                             row['name'].encode(), *row['priority'], row['address'],
                             len(data), cursor - aux)
            image[cursor:cursor + len(data)] = data
            cursor += len(data)
        struct.pack_into('<8H', image, 8 + group_index * 16, slave,
                         headers - 144, aux - 144, len(rows), 0x1000, 0, 0, 0)
    private_output.mkdir(parents=True, exist_ok=True)
    output = private_output / 'aop-static-partial-cmd-db.bin'
    output.write_bytes(image)
    result = dict(schema_version=1, source='authorized active-slot aop_a partition backup',
                  firmware_sha256=sha, firmware_size=len(raw), firmware_is_pristine_stock=False,
                  live_command_db_captured=False, reconstructed_binary_is_complete=False,
                  known_missing=['dynamic VRM resource registration and aliases',
                                 'live command-db header/checksum and later runtime changes'],
                  constructor_file_offset=0x2f38c, constructor_add_group_file_offset=0x2f200,
                  version_argument=0x1000, public_abi='Linux 6.12.114 drivers/soc/qcom/cmd-db.c',
                  descriptor_validation='Counts and lengths, ELF PT_LOAD mapping, all entry/aux bounds, slave IDs, uniqueness, fixed constructor opcode witnesses',
                  reconstructed_sha256=hashlib.sha256(image).hexdigest(),
                  reconstructed_used_bytes=cursor, groups=parsed)
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(dict(resources=len(resources), static_groups=len(parsed),
                          reconstructed_file=str(output), reconstructed_sha256=result['reconstructed_sha256'],
                          complete=False, live_ram_dump=False)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--private-output', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    args = parser.parse_args()
    inspect(args.firmware, args.private_output, args.receipt)
