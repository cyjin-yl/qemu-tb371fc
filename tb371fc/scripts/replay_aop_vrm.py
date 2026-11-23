#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Replay selected VRM functions from the owner's private, hash-pinned AOP.

This executes the allocator, address encoder, aux builder and command-db
insertion code. It assumes the normal enumeration branch and configured PMIC
prefixes; it does not boot AOP or read the tablet's live command-db RAM.
Only the two heap allocation functions are replaced. Unknown accesses and
firmware assertions fail. Requires the optional unicorn==2.1.4 package.
"""
import argparse
import hashlib
import json
import struct
from pathlib import Path

FIRMWARE_SHA = '3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671'
STATIC_DB_SHA = '7ddf6f1f7f1a711c3e34d96a8f349eb995d08220b440ef2965c4d4e8b1de480d'
REGIONS = [(0, 0x14f08, 0x3000), (0xe0000, 0x604c, 0x17f10),
           (0x80800000, 0xdfc8, 0x1df60), (0x80810000, 0x1384, 0x2bf28),
           (0x80818000, 0x47b0, 0x2d2ac)]
PROPERTIES = {8: 0x8081c68c, 9: 0x8081c750, 10: 0x8081c6d8,
              11: 0x8081c674, 12: 0x8081c768, 13: 0x8081c6f0,
              14: 0x8081c6bc, 15: 0x8081c780, 16: 0x8081c708,
              17: 0x8081c6a4, 18: 0x8081c798, 19: 0x8081c738}
KINDS = {1: (9, 10, 8), 2: (12, 13, 11), 3: (15, 16, 14), 5: (18, 19, 17)}
CLOCK_NAMES = ('lnbclka1', 'vrm.xob', 'lnbclka2', 'lnbclka3', 'rfclka1', 'rfclka3')


def require(condition, message):
    if not condition:
        raise ValueError(message)


class Firmware:
    def __init__(self, filename):
        self.raw = filename.read_bytes()
        require(len(self.raw) == 524288 and
                hashlib.sha256(self.raw).hexdigest() == FIRMWARE_SHA,
                'Expected the documented private active-slot AOP backup')
        require(self.raw[:7] == b'\x7fELF\x01\x01\x01', 'Expected ELF32 little endian')

    def offset(self, address, size=1):
        for base, length, offset in REGIONS:
            if base <= address and address + size <= base + length:
                return address - base + offset
        raise ValueError(f'Unmapped firmware data pointer {address:#x}, size {size}')

    def word(self, address):
        return struct.unpack_from('<I', self.raw, self.offset(address, 4))[0]

    def string(self, address):
        if not address:
            return None
        raw = self.raw[self.offset(address, 9):self.offset(address, 9) + 9]
        require(b'\0' in raw, 'Command-db name exceeds eight bytes')
        return raw.split(b'\0', 1)[0].decode('ascii')

    def pair(self, address):
        if not address:
            return None, None
        return self.string(self.word(address)), self.string(self.word(address + 4))

    def targets(self):
        # The three regulator count arrays are read from the firmware's target
        # table. Their use as the discovered normal-branch counts is an explicit
        # assumption. Clock prefixes are bounded by these static config arrays;
        # every enabled/parent-containing clock lies in the first seven slots.
        counts = {1: struct.unpack_from('<6I', self.raw, 0x19c70),
                  2: struct.unpack_from('<6I', self.raw, 0x19c58),
                  3: struct.unpack_from('<6I', self.raw, 0x19c88),
                  5: (11, 0, 0, 11, 0, 0)}
        require(counts[1] == (10, 0, 8, 0, 0, 2) and
                counts[2] == (18, 0, 11, 0, 0, 7) and
                counts[3] == (0, 0, 1, 0, 0, 0), 'Target count table mismatch')
        rows = []
        for pmic in range(6):
            for kind, property_ids in KINDS.items():
                names, deps, config = [self.word(PROPERTIES[x] + 4 * pmic)
                                       for x in property_ids]
                for index in range(counts[kind][pmic]):
                    pair = names + 8 * (index + 1) if names else 0
                    name, alias = self.pair(pair)
                    cfg = config + index * (12 if kind == 5 else 32) if config else 0
                    word = self.word(cfg) if cfg else 0
                    dep = deps + 16 * (index + 1) if deps else 0
                    parent = self.pair(self.word(dep))[0] if dep else None
                    child_ptr = self.word(dep + 4) if dep else 0
                    children = self.word(dep + 8) if child_ptr else 0
                    flags = self.word(dep + 12) if dep else 0
                    require(children <= 14, 'Unexpected child list extent')
                    child_names = [self.pair(self.word(child_ptr + 4 * n))[0]
                                   for n in range(children)]
                    enabled = bool(word & 1)
                    if enabled or children or flags:
                        require(name is not None, 'Configured resource lacks name')
                        require(kind != 5 or index < 7,
                                'Enabled clock exceeds documented prefix')
                    rows.append(dict(pmic_index=pmic, kind=kind, peripheral=index + 1,
                                     name=name, alias=alias, parent=parent,
                                     enabled=enabled, children=child_names,
                                     parent_count=children, parent_flags=flags,
                                     resource_kind=(word >> 1) & 15 if kind == 5 else 0,
                                     config_file_offset=self.offset(cfg) if cfg else None,
                                     name_pair_file_offset=self.offset(pair) if pair else None,
                                     dependency_file_offset=self.offset(dep) if dep else None))
        return rows


def replay(firmware, rows, disable_smpf2):
    import unicorn
    from unicorn import Uc, UC_ARCH_ARM, UC_MODE_THUMB, UC_HOOK_CODE, UC_HOOK_MEM_INVALID
    from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                   UC_ARM_REG_R3, UC_ARM_REG_PC, UC_ARM_REG_SP, UC_ARM_REG_LR)

    require(unicorn.__version__ == '2.1.4', 'Use the documented unicorn==2.1.4')
    vm = Uc(UC_ARCH_ARM, UC_MODE_THUMB)
    vm.mem_map(0, 0x20000)
    vm.mem_write(0, firmware.raw[0x3000:0x17f08])
    vm.mem_map(0xe0000, 0x10000)
    vm.mem_write(0xe0000, firmware.raw[0x17f10:0x1df5c])
    vm.mem_map(0x20000000, 0x100000)
    ctx, stop = 0xe2788, 0x200ff000
    heap, pool, instructions = 0x20080000, 0x20020000, 0
    trace, events, allocations = hashlib.sha256(), [], []

    def word(address):
        return struct.unpack('<I', vm.mem_read(address, 4))[0]

    def put(address, value):
        vm.mem_write(address, struct.pack('<I', value))

    def return_value(value):
        vm.reg_write(UC_ARM_REG_R0, value)
        vm.reg_write(UC_ARM_REG_PC, vm.reg_read(UC_ARM_REG_LR))

    def invalid(_vm, access, address, size, _value, _user):
        raise RuntimeError(f'Unexpected access {access} at {address:#x}, size {size}')

    def hook(_vm, address, _size, _user):
        nonlocal heap, instructions
        trace.update(struct.pack('<I', address))
        instructions += 1
        if address == stop:
            vm.emu_stop()
        elif address in (0x24ac, 0x49ea):
            size = vm.reg_read(UC_ARM_REG_R0)
            allowed = (2400, 2048) if address == 0x24ac else (12,)
            require(size in allowed and heap + size < 0x200f0000,
                    'Unexpected heap allocation')
            allocation = heap
            vm.mem_write(allocation, bytes(size))
            heap += (size + 63) & ~63
            allocations.append(dict(address=allocation, size=size))
            if address == 0x49ea:
                put(vm.reg_read(UC_ARM_REG_R1), allocation)
                return_value(0)
            else:
                return_value(allocation)
        elif address in (0x4896, 0x3010):
            raise RuntimeError(f'Firmware assertion at {address:#x}')

    vm.hook_add(UC_HOOK_CODE, hook)
    vm.hook_add(UC_HOOK_MEM_INVALID, invalid)

    def execute(address, arguments):
        vm.reg_write(UC_ARM_REG_SP, 0x20010000)
        vm.reg_write(UC_ARM_REG_LR, stop | 1)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1,
                                    UC_ARM_REG_R2, UC_ARM_REG_R3), arguments):
            vm.reg_write(register, value)
        vm.emu_start(address | 1, stop, timeout=1_000_000, count=100_000)
        require(vm.reg_read(UC_ARM_REG_PC) == stop, 'Firmware routine did not return')
        require(vm.reg_read(UC_ARM_REG_R0) == 0, 'Firmware routine returned an error')

    # Original descriptor, group initializer, address encoder and DB writer.
    require(word(0xe06b8) == 4 and word(0xe3e78) == 0, 'Initial data mismatch')
    put(ctx + 0x168c, 0xe06b8)
    execute(0x5bec, (4, 0x100))

    def allocate(row, parent):
        nonlocal pool
        address = pool
        pool += 0x100
        require(pool < 0x20070000, 'Input pool exhausted')
        for index, value in enumerate((row['name'], row['alias'],
                                       None if parent else row['parent'])):
            pointer = address + 16 + index * 32 if value else 0
            put(address + 4 * index, pointer)
            if value:
                vm.mem_write(pointer, value.encode('ascii') + b'\0')
        result = 0x2001ff00
        put(result, 0)
        put(0x20010000, row['parent_flags'] if parent else 0)
        put(0x20010004, result)
        before = instructions
        execute(0x3508, (ctx, row['resource_kind'], address,
                          row['parent_count'] if parent else 0))
        events.append(dict(stage='parent' if parent else 'resource',
                           name=row['name'], alias=row['alias'],
                           parent=None if parent else row['parent'],
                           reserved_children=row['parent_count'] if parent else 0,
                           resource_id=word(result), next_id=word(0xe3e78),
                           instructions=instructions - before))

    for row in rows:
        if row['parent_count'] or row['parent_flags']:
            allocate(row, True)
    for row in rows:
        if row['enabled'] and not (disable_smpf2 and row['name'] == 'smpf2'):
            allocate(row, False)
    count = struct.unpack('<H', vm.mem_read(0xe3fa6, 2))[0]
    require(count <= 100, 'Entry allocation exceeded')
    entries, names, duplicates = [], {}, []
    headers, auxiliary = word(0xe3f90), word(0xe3f98)
    for index in range(count):
        name, p0, p1, address, length, offset = struct.unpack(
            '<8sIIIHH', bytes(vm.mem_read(headers + 24 * index, 24)))
        require(offset + length <= 2048, 'Auxiliary allocation exceeded')
        row = dict(name=name.split(b'\0', 1)[0].decode('ascii'),
                   priority=[p0, p1], address=address,
                   aux_hex=bytes(vm.mem_read(auxiliary + offset, length)).hex())
        if row['name'] in names:
            require(names[row['name']] == row, 'Conflicting firmware duplicate')
            duplicates.append(row['name'])
        names[row['name']] = row
        entries.append(row)
    return dict(disable_smpf2=disable_smpf2, instruction_count=instructions,
                pc_trace_sha256=trace.hexdigest(), next_resource_id=word(0xe3e78),
                emitted_records=len(entries), unique_names=len(names),
                identical_duplicate_names=duplicates, allocations=allocations,
                events=events, entries=entries)


def clock_partial(static_db, output, rows):
    image = bytearray(static_db.read_bytes())
    require(hashlib.sha256(image).hexdigest() == STATIC_DB_SHA,
            'Static partial input differs from the separately validated 69-entry file')
    cursor, count, index = 144, 0, 0
    for index in range(8):
        slave, start, aux, length = struct.unpack_from('<4H', image, 8 + 16 * index)
        if not slave:
            break
        start += 144
        aux += 144
        cursor = max(cursor, start + 24 * length)
        count += length
        for entry in range(length):
            size, offset = struct.unpack_from('<2H', image, start + 24 * entry + 20)
            cursor = max(cursor, aux + offset + size)
    require(count == 69 and index < 7, 'Static table geometry mismatch')
    start = cursor
    cursor += 24 * len(rows)
    aux = cursor
    for entry, row in enumerate(rows):
        data = bytes.fromhex(row['aux_hex'])
        struct.pack_into('<8sIIIHH', image, start + 24 * entry,
                         row['name'].encode(), *row['priority'], row['address'],
                         len(data), cursor - aux)
        image[cursor:cursor + len(data)] = data
        cursor += len(data)
    require(cursor < len(image) and aux - 144 <= 65535, 'Partial DB exceeds bounds')
    struct.pack_into('<8H', image, 8 + 16 * index, 4, start - 144,
                     aux - 144, len(rows), 0x100, 0, 0, 0)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    return dict(sha256=hashlib.sha256(image).hexdigest(), records=75,
                bytes=len(image), complete=False, live_ram_dump=False,
                scope='69 static ARC/BCM/MOL records plus five clock names and vrm.xob alias')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--firmware', type=Path, required=True)
    parser.add_argument('--receipt', type=Path, required=True)
    parser.add_argument('--assume-normal-enumeration', action='store_true', required=True)
    parser.add_argument('--static-db', type=Path)
    parser.add_argument('--private-clock-output', type=Path)
    args = parser.parse_args()
    require(bool(args.static_db) == bool(args.private_clock_output),
            'Both --static-db and --private-clock-output are required together')
    firmware = Firmware(args.firmware)
    rows = firmware.targets()
    variants = [replay(firmware, rows, value) for value in (False, True)]
    clocks = []
    for name in CLOCK_NAMES:
        values = [next(x for x in variant['entries'] if x['name'] == name)
                  for variant in variants]
        require(values[0] == values[1], f'Clock {name} differs across known variants')
        clocks.append(values[0])
    report = dict(
        schema_version=1, firmware_sha256=FIRMWARE_SHA, firmware_bytes=len(firmware.raw),
        firmware_is_pristine_stock=False, live_command_db_captured=False, complete=False,
        method='Isolated execution of original Thumb allocator, address encoder, auxiliary builder and command-db insertion',
        engine='Unicorn 2.1.4, ARM Thumb; optional analysis dependency, not part of QEMU',
        assumptions=[
            'Normal PMIC enumeration branch; target PMIC indexes 0,2,3,5 are visited',
            'Discovered peripheral counts include every enabled or parent-containing static target entry',
            'Only the identified smpf2 platform mutation is varied; other runtime filtering is unmeasured'],
        excluded=['Complete AOP startup', 'SPMI/MMIO and physical PMIC execution',
                  'Miscellaneous target registration', 'Live RAM header/checksum and exact placement',
                  'Proof that the tablet took either particular assumed enumeration path'],
        replacement_functions=[
            dict(vma=0x24ac, purpose='Allocate zeroed private DB buffers, exactly 2400 or 2048 bytes'),
            dict(vma=0x49ea, purpose='Allocate zeroed private 12-byte parent metadata')],
        original_function_file_offsets={
            'normal_enumeration': 0x2def0, 'parent_registration': 0x2e028,
            'allocator': 0x6508, 'registration_wrapper': 0x17094,
            'address_encoder': 0x17828, 'auxiliary_builder': 0x1787c,
            'command_db_init': 0x8bec, 'command_db_insert': 0x8c6c,
            'platform_smpf2_mutation': 0x2da30},
        property_dispatch_file_offset=0x178f4,
        target_count_file_offsets={'smps': 0x19c70, 'ldo': 0x19c58, 'bob': 0x19c88},
        target_rows=[row for row in rows if row['enabled'] or row['parent_count'] or row['parent_flags']],
        variants=variants, clocks_equal_in_both_variants=clocks)
    if args.static_db:
        report['private_clock_partial'] = clock_partial(
            args.static_db, args.private_clock_output, clocks)
    report['analysis_script_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    args.receipt.parent.mkdir(parents=True, exist_ok=True)
    args.receipt.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(dict(success=True, variant_records=[v['emitted_records'] for v in variants],
                          stable_clock_names=len(clocks), complete=False,
                          clock_partial=report.get('private_clock_partial'))))


if __name__ == '__main__':
    main()
