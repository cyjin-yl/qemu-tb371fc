#!/usr/bin/env python3
"""Read and cross-check both GPTs of one raw UFS LUN; never read partitions.

Only metadata consistency is qualified. No repair, sparse expansion, partition
content/AVB verification, complete backup or Android boot claim is made.
See ../docs/ufs-gpt-inventory.md for the bounded profile and source references.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import struct
import sys
import uuid
import zlib


MAX_ENTRY_ARRAY_BYTES = 16 * 1024 * 1024
MIN_RESERVED_ARRAY_BYTES = 16384
BLKSSZGET = 0x1268
BLKGETSIZE64 = 0x80081272
HEADER = struct.Struct('<8sIIIIQQQQ16sQIII')
ZERO_GUID = bytes(16)
UEFI_SOURCE = ('https://uefi.org/specs/UEFI/2.11/'
               '05_GUID_Partition_Table_Format.html')
LINUX_SOURCE = ('https://github.com/gregkh/linux/blob/v6.12.114/'
                'block/partitions/efi.c')


class InvalidGPT(ValueError):
    """A rejected input, including unsupported formats and changed inputs."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise InvalidGPT(code, message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def stat_identity(info):
    # atime is deliberately excluded: a read can update it.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_rdev,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def block_geometry(fd):
    """Linux/Android block device geometry, queried from this open descriptor."""
    import fcntl
    block_bytes = struct.unpack('=I', fcntl.ioctl(fd, BLKSSZGET, bytes(4)))[0]
    disk_bytes = struct.unpack('=Q', fcntl.ioctl(fd, BLKGETSIZE64, bytes(8)))[0]
    return block_bytes, disk_bytes


def read_exact(fd, offset, length, disk_bytes):
    require(0 <= offset <= disk_bytes and 0 <= length <= disk_bytes - offset,
            'outside_input', 'Metadata read lies outside the declared LUN')
    pieces = []
    got = 0
    while got < length:
        part = os.pread(fd, length - got, offset + got)
        require(bool(part), 'short_read', 'Metadata read was truncated')
        pieces.append(part)
        got += len(part)
    return b''.join(pieces)


def read_region(fd, regions, name, offset, length, disk_bytes):
    raw = read_exact(fd, offset, length, disk_bytes)
    regions.append((name, offset, raw))
    return raw


def check_pmbr(raw, total_lbas, extent_policy='strict'):
    require(extent_policy in ('strict', 'allow-saturated-size'), 'pmbr_policy',
            'Unknown protective MBR extent policy')
    require(raw[:4] != b'\x3a\xff\x26\xed', 'android_sparse',
            'Android sparse input must be independently expanded and verified')
    require(raw[:4] != b'QFI\xfb', 'qcow2',
            'QCOW2 input is not a raw LUN')
    require(raw[510:512] == b'\x55\xaa', 'pmbr_signature',
            'Missing protective MBR signature')
    entries = [raw[446+i*16:462+i*16] for i in range(4)]
    present = [item for item in entries if any(item)]
    require(len(present) == 1 and present[0][4] == 0xee,
            'pmbr_records', 'Require exactly one protective record and no hybrid MBR')
    item = present[0]
    start, size = struct.unpack_from('<II', item, 8)
    expected_size = min(total_lbas - 1, 0xffffffff)
    standard_extent = start == 1 and size == expected_size
    saturated_exception = (extent_policy == 'allow-saturated-size' and start == 1
                           and size == 0xffffffff and size != expected_size)
    require(standard_extent or saturated_exception,
            'pmbr_extent', 'Protective MBR does not cover this LUN geometry')
    require(not any(raw[440:446]) and not any(raw[512:]),
            'pmbr_reserved', 'Protective MBR reserved bytes must be zero')
    # CHS and BootIndicator are not interpreted as partition addresses.
    return {'partition_type': 0xee, 'start_lba': start, 'size_lbas': size,
            'actual_size_lbas': size, 'expected_size_lbas': expected_size,
            'extent_policy': extent_policy, 'pmbr_standard_extent': standard_extent,
            'sentinel_compatibility': saturated_exception,
            'boot_indicator': item[0], 'hybrid_mbr': False}


def read_header(fd, regions, side, lba, other_lba, block_bytes, disk_bytes):
    raw = read_region(fd, regions, side+'_header', lba*block_bytes,
                      block_bytes, disk_bytes)
    (signature, revision, size, crc, reserved, current, alternate,
     first, last, disk_guid, array_lba, count, entry_bytes,
     array_crc) = HEADER.unpack_from(raw)
    require(signature == b'EFI PART', 'header_signature', side+' GPT signature')
    require(revision == 0x10000, 'header_revision', 'Only GPT revision 1.0 is supported')
    require(HEADER.size <= size <= block_bytes, 'header_size',
            side+' GPT header size is invalid')
    require(reserved == 0 and not any(raw[HEADER.size:]), 'header_reserved',
            side+' GPT reserved header bytes must be zero')
    crc_bytes = bytearray(raw[:size])
    crc_bytes[16:20] = bytes(4)
    require(zlib.crc32(crc_bytes) == crc, 'header_crc', side+' GPT header CRC mismatch')
    require(current == lba and alternate == other_lba, 'header_lbas',
            side+' GPT current/alternate LBA does not match the physical layout')
    require(disk_guid != ZERO_GUID, 'disk_guid', 'Zero disk GUID')
    require(count > 0 and entry_bytes >= 128 and entry_bytes & (entry_bytes-1) == 0,
            'entry_geometry', 'Entry count must be positive; entry size must be 128 * 2^n')
    array_bytes = count * entry_bytes
    require(array_bytes <= MAX_ENTRY_ARRAY_BYTES, 'array_limit',
            'Declared partition array exceeds the bounded 16 MiB profile')
    sectors = (array_bytes + block_bytes - 1) // block_bytes
    reserve = (max(array_bytes, MIN_RESERVED_ARRAY_BYTES) + block_bytes - 1) // block_bytes
    total_lbas = disk_bytes // block_bytes
    require(2 + reserve <= first <= last < total_lbas - 1 - reserve,
            'usable_geometry', 'Usable range leaves insufficient space for both GPT arrays')
    if side == 'primary':
        require(1 < array_lba and array_lba + sectors <= first,
                'array_geometry', 'Primary GPT array overlaps a header or usable data')
    else:
        require(last < array_lba and array_lba + sectors <= lba,
                'array_geometry', 'Backup GPT array overlaps usable data or its header')
    return {'revision': revision, 'header_size': size, 'header_crc32': crc,
            'current_lba': current, 'alternate_lba': alternate,
            'first_usable_lba': first, 'last_usable_lba': last,
            'disk_guid': disk_guid, 'array_lba': array_lba,
            'entry_count': count, 'entry_bytes': entry_bytes,
            'array_bytes': array_bytes, 'array_crc32': array_crc}


def read_array(fd, regions, side, header, block_bytes, disk_bytes):
    raw = read_region(fd, regions, side+'_entries', header['array_lba']*block_bytes,
                      header['array_bytes'], disk_bytes)
    require(zlib.crc32(raw) == header['array_crc32'], 'array_crc',
            side+' GPT partition entry array CRC mismatch')
    return raw


def partitions_from_array(raw, header, block_bytes, unused_policy='strict'):
    require(unused_policy in ('strict', 'allow-type-zero-residue'), 'unused_entry_policy',
            'Unknown unused partition entry policy')
    parts, unique_ids, names = [], set(), set()
    residues = []
    width = header['entry_bytes']
    for index in range(header['entry_count']):
        entry = raw[index*width:(index+1)*width]
        type_guid, unique_guid = entry[:16], entry[16:32]
        if type_guid == ZERO_GUID:
            if any(entry):
                require(unused_policy == 'allow-type-zero-residue', 'unused_entry',
                        'Nonzero unused entry is outside the strict inventory profile')
                require(not any(entry[128:]), 'entry_reserved',
                        'Reserved extended partition entry bytes must be zero')
                # Type zero is unused. Hash opaque residue, without interpreting
                # stale addresses, names, IDs or attributes as a partition.
                fields = {}
                for name, start, end in (('unique_guid', 16, 32), ('first_lba', 32, 40),
                                         ('last_lba', 40, 48), ('attributes', 48, 56),
                                         ('name_utf16', 56, 128)):
                    if any(entry[start:end]):
                        fields[name] = {'offset_bytes': start, 'size_bytes': end-start,
                                        'sha256': sha(entry[start:end])}
                residues.append({'entry_index': index+1, 'entry_bytes': width,
                                 'classification': 'unused_type_zero_residue',
                                 'record_sha256': sha(entry), 'nonzero_fields': fields,
                                 'stale_fields_interpreted': False,
                                 'included_in_partitions': False})
            continue
        require(unique_guid != ZERO_GUID and unique_guid not in unique_ids,
                'partition_guid', 'Partition GUID is zero or duplicated within the LUN')
        unique_ids.add(unique_guid)
        first, last, attributes = struct.unpack_from('<QQQ', entry, 32)
        require(header['first_usable_lba'] <= first <= last <= header['last_usable_lba'],
                'partition_bounds', 'Partition lies outside the GPT usable range')
        require(attributes & (((1 << 48)-1) ^ 7) == 0, 'partition_attributes',
                'Reserved GPT attribute bits 3 through 47 must be zero')
        require(not any(entry[128:]), 'entry_reserved',
                'Reserved extended partition entry bytes must be zero')
        units = struct.unpack_from('<36H', entry, 56)
        require(0 in units, 'partition_name', 'Partition name lacks a UTF-16 terminator')
        end = units.index(0)
        require(not any(units[end:]), 'partition_name',
                'Nonzero name data follows its terminator')
        try:
            name = entry[56:56+end*2].decode('utf-16-le', errors='strict')
        except UnicodeDecodeError as exc:
            raise InvalidGPT('partition_name', 'Malformed UTF-16 partition name') from exc
        require(not name or name.isprintable(), 'partition_name',
                'Control characters in partition name')
        require(not name or name not in names, 'duplicate_name',
                'Duplicate nonempty label would make a by-name lookup ambiguous')
        names.add(name)
        parts.append({'entry_index': index+1, 'name': name,
                      'type_guid': str(uuid.UUID(bytes_le=type_guid)),
                      'unique_guid_sha256': sha(unique_guid),
                      'first_lba': first, 'last_lba': last,
                      'offset_bytes': first*block_bytes,
                      'size_bytes': (last-first+1)*block_bytes,
                      'attributes': f'0x{attributes:016x}'})
    ordered = sorted(parts, key=lambda p: p['first_lba'])
    for left, right in zip(ordered, ordered[1:]):
        require(left['last_lba'] < right['first_lba'], 'partition_overlap',
                'Defined partitions overlap')
    return parts, residues


def metadata_digest(regions, disk_bytes, block_bytes):
    digest = hashlib.sha256(b'tb371fc-gpt-metadata-v1\0')
    digest.update(struct.pack('<QI', disk_bytes, block_bytes))
    for _, offset, raw in regions:
        digest.update(struct.pack('<QQ', offset, len(raw)))
        digest.update(raw)
    return digest.hexdigest()


def inspect(path, logical_block_bytes=None, allow_block_device=False, required_names=(),
            pmbr_policy='strict', unused_entry_policy='strict'):
    """Return a qualified metadata-only report, or raise without changing input."""
    path = Path(path)
    before_path = path.lstat()
    regular = stat.S_ISREG(before_path.st_mode)
    block = stat.S_ISBLK(before_path.st_mode)
    require(regular or (block and allow_block_device), 'input_type',
            'Require a regular raw file, or an explicitly allowed block device; no symlinks')
    require(hasattr(os, 'O_NOFOLLOW'), 'open_policy', 'O_NOFOLLOW is required')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat_identity(before_path) == stat_identity(before), 'input_changed',
                'Input changed while opening')
        if block:
            block_bytes, disk_bytes = block_geometry(fd)
            require(logical_block_bytes is None or logical_block_bytes == block_bytes,
                    'block_size_mismatch', 'Requested block size differs from BLKSSZGET')
        else:
            require(logical_block_bytes is not None, 'block_size_required',
                    'Regular raw files require an explicit logical block size')
            block_bytes, disk_bytes = logical_block_bytes, before.st_size
        require(block_bytes in (512, 4096), 'block_size',
                'The supported raw inventory profile has 512 or 4096 byte logical blocks')
        require(disk_bytes >= block_bytes*3 and disk_bytes % block_bytes == 0,
                'input_geometry', 'LUN size is truncated or not a multiple of its logical block size')
        total_lbas = disk_bytes // block_bytes
        regions = []
        pmbr = check_pmbr(read_region(fd, regions, 'protective_mbr', 0,
                                     block_bytes, disk_bytes), total_lbas, pmbr_policy)
        primary = read_header(fd, regions, 'primary', 1, total_lbas-1, block_bytes, disk_bytes)
        backup = read_header(fd, regions, 'backup', total_lbas-1, 1, block_bytes, disk_bytes)
        for field in ('revision', 'header_size', 'first_usable_lba', 'last_usable_lba',
                      'disk_guid', 'entry_count', 'entry_bytes', 'array_crc32'):
            require(primary[field] == backup[field], 'header_disagreement',
                    'Primary and backup GPT disagree on '+field)
        array = read_array(fd, regions, 'primary', primary, block_bytes, disk_bytes)
        backup_array = read_array(fd, regions, 'backup', backup, block_bytes, disk_bytes)
        require(array == backup_array, 'array_disagreement',
                'Primary and backup GPT arrays differ byte for byte')
        parts, residues = partitions_from_array(array, primary, block_bytes, unused_entry_policy)
        required_names = sorted(set(required_names))
        require(all(name and name in {p['name'] for p in parts} for name in required_names),
                'missing_partition', 'One or more required partition labels are absent')
        # A second actual read checks all observed bytes. It is not an atomic
        # snapshot and cannot establish anything about unread partition data.
        for name, offset, raw in regions:
            require(read_exact(fd, offset, len(raw), disk_bytes) == raw,
                    'metadata_changed', 'Metadata changed during inspection: '+name)
        if block:
            require(block_geometry(fd) == (block_bytes, disk_bytes), 'input_changed',
                    'Block device geometry changed during inspection')
        require(stat_identity(os.fstat(fd)) == stat_identity(before) and
                stat_identity(path.lstat()) == stat_identity(before), 'input_changed',
                'Input identity or filesystem metadata changed during inspection')
        public_headers = {}
        for side, header in (('primary', primary), ('backup', backup)):
            public_headers[side] = {k: v for k, v in header.items() if k != 'disk_guid'}
        return {'schema_version': 1, 'kind': 'ufs-gpt-metadata-inventory',
                'status': 'gpt_metadata_verified', 'input_name': path.name,
                'input_kind': 'block_device' if block else 'regular_raw_file',
                'geometry_source': 'BLKSSZGET+BLKGETSIZE64' if block else 'explicit_block_size+fstat',
                'input_bytes': disk_bytes, 'logical_block_bytes': block_bytes,
                'total_lbas': total_lbas, 'protective_mbr': pmbr,
                'headers': public_headers, 'disk_guid_sha256': sha(primary['disk_guid']),
                'metadata_sha256': metadata_digest(regions, disk_bytes, block_bytes),
                'observed_regions': [{'name': n, 'offset_bytes': o, 'size_bytes': len(r),
                                      'sha256': sha(r)} for n, o, r in regions],
                'metadata_bytes_per_pass': sum(len(r) for _, _, r in regions),
                'metadata_read_passes': 2, 'metadata_observed_unchanged': True,
                'gpt_metadata_verified': True, 'partition_count': len(parts),
                'unused_entry_count': primary['entry_count']-len(parts),
                'unused_zero_entry_count': primary['entry_count']-len(parts)-len(residues),
                'unused_entry_policy': unused_entry_policy,
                'unused_type_zero_residue_count': len(residues),
                'unused_type_zero_residues': residues,
                'partitions': parts, 'required_partition_labels': required_names,
                'required_partition_labels_verified': bool(required_names),
                'partition_contents_verified': False, 'full_lun_content_hashed': False,
                'complete_backup_verified': False, 'atomic_snapshot_verified': False,
                'android_boot_verified': False,
                'sources': [UEFI_SOURCE, LINUX_SOURCE],
                'limitations': ['Only the five listed GPT metadata regions were read, twice.',
                                'No partition content, super metadata, AVB signature or encrypted data was read.',
                                'Stable metadata during these reads does not prove a coherent full-LUN backup.',
                                'This bounded profile requires both GPT copies; it does not repair or recover either.']}
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='One raw LUN file or explicitly allowed block device')
    parser.add_argument('--logical-block-size', type=int, choices=(512, 4096),
                        help='Required for raw files; block devices use their ioctl value and must match if supplied')
    parser.add_argument('--allow-block-device', action='store_true')
    parser.add_argument('--pmbr-policy', choices=('strict', 'allow-saturated-size'),
                        default='strict',
                        help='Strict by default; opt in only to the observed 0xffffffff size sentinel')
    parser.add_argument('--unused-entry-policy', choices=('strict', 'allow-type-zero-residue'),
                        default='strict',
                        help='Strict by default; opt in to classifying opaque residue in type-zero entries')
    parser.add_argument('--require-partition', action='append', default=[],
                        help='Require this exact label in this LUN; repeat for more labels')
    parser.add_argument('--output', type=Path, help='New JSON receipt; existing paths are never overwritten')
    args = parser.parse_args(argv)
    if args.output and (args.output.exists() or args.output.is_symlink()):
        parser.error('Output already exists; choose a new receipt path')
    try:
        report = inspect(args.input, args.logical_block_size,
                         args.allow_block_device, args.require_partition, args.pmbr_policy,
                         args.unused_entry_policy)
        exit_code = 0
    except (InvalidGPT, OSError) as exc:
        report = {'schema_version': 1, 'kind': 'ufs-gpt-metadata-inventory',
                  'status': 'rejected', 'error_code': getattr(exc, 'code', 'input_io'),
                  'error': str(exc), 'gpt_metadata_verified': False,
                  'complete_backup_verified': False, 'android_boot_verified': False}
        exit_code = 1
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=True)+'\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as output:
            output.write(text)
    else:
        sys.stdout.write(text)
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
