#!/usr/bin/env python3
"""Inspect one bounded, previously captured vbmeta object; never authenticate it.

Only regular files containing exactly header + authentication + auxiliary data
are accepted. No device, descriptor-named path, partition, key file or footer is
opened. A matching internal hash is not a signature or a trusted-key check.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct


AOSP_COMMIT = '761178607206f4cb2af79ed9eec52d8cbd814adb'
AOSP_BASE = 'https://android.googlesource.com/platform/external/avb/+/' + AOSP_COMMIT
MAX_METADATA_BYTES = 65536
HEADER = struct.Struct('>4sIIQQI11QII48s80s')
HEADER_FIELDS = ('magic major minor auth_bytes aux_bytes algorithm hash_offset '
                 'hash_bytes signature_offset signature_bytes public_key_offset '
                 'public_key_bytes key_metadata_offset key_metadata_bytes '
                 'descriptors_offset descriptors_bytes rollback_index flags '
                 'rollback_index_location release reserved').split()
ALGORITHMS = {0: ('NONE', None, 0),
              1: ('SHA256_RSA2048', 'sha256', 2048),
              2: ('SHA256_RSA4096', 'sha256', 4096),
              3: ('SHA256_RSA8192', 'sha256', 8192),
              4: ('SHA512_RSA2048', 'sha512', 2048),
              5: ('SHA512_RSA4096', 'sha512', 4096),
              6: ('SHA512_RSA8192', 'sha512', 8192)}
TAGS = {0: 'property', 1: 'hashtree', 2: 'hash', 3: 'kernel_cmdline', 4: 'chain'}


class InvalidVBMeta(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise InvalidVBMeta(code)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def region(data, offset, size):
    require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset,
            'region_bounds')
    return data[offset:offset + size]


def label(raw):
    require(0 < len(raw) <= 127 and
            re.fullmatch(rb'[A-Za-z0-9_.-]+', raw) and raw not in (b'.', b'..'),
            'partition_label')
    return raw.decode('ascii')


def utf8(raw):
    try:
        raw.decode('utf-8')
    except UnicodeDecodeError:
        raise InvalidVBMeta('utf8') from None
    require(b'\0' not in raw, 'embedded_nul')


def variable_fields(data, fixed, lengths):
    result = []
    at = fixed
    for size in lengths:
        result.append(region(data, at, size))
        at += size
    require(len(data) == (at + 7) // 8 * 8 and not any(data[at:]),
            'descriptor_padding')
    return result


def public_key(data, expected_bits=None):
    require(len(data) >= 8, 'public_key_length')
    bits = struct.unpack_from('>I', data)[0]
    require(bits in (2048, 4096, 8192) and len(data) == 8 + 2 * (bits // 8),
            'public_key_shape')
    require(expected_bits is None or bits == expected_bits, 'public_key_algorithm')
    return {'bits': bits, 'bytes': len(data), 'sha256': sha(data)}


def descriptor(data, offset, slot_suffix):
    tag, following = struct.unpack_from('>QQ', data)
    require(tag in TAGS, 'unsupported_descriptor_tag')
    require(following == len(data) - 16 and following % 8 == 0, 'descriptor_size')
    out = {'tag': tag, 'kind': TAGS[tag], 'offset_bytes': offset,
           'size_bytes': len(data), 'sha256': sha(data)}
    if tag == 0:
        require(len(data) >= 32, 'property_size')
        key_len, value_len = struct.unpack_from('>QQ', data, 16)
        key, key_nul, value, value_nul = variable_fields(
            data, 32, [key_len, 1, value_len, 1])
        require(key_nul == value_nul == b'\0', 'property_terminator')
        utf8(key)
        out.update(key_bytes=key_len, value_bytes=value_len,
                   key_sha256=sha(key), value_sha256=sha(value))
    elif tag == 3:
        require(len(data) >= 24, 'cmdline_size')
        flags, length = struct.unpack_from('>II', data, 16)
        require(flags & ~3 == 0, 'cmdline_flags')
        command, = variable_fields(data, 24, [length])
        utf8(command)
        out.update(flags=flags, command_bytes=length, command_sha256=sha(command),
                   command_interpreted=False)
    elif tag == 4:
        require(len(data) >= 92, 'chain_size')
        location, name_len, key_len, flags = struct.unpack_from('>IIII', data, 16)
        require(location > 0 and flags & ~1 == 0 and not any(data[32:92]),
                'chain_fields')
        name, key = variable_fields(data, 92, [name_len, key_len])
        out.update(rollback_index_location=location, flags=flags,
                   public_key=public_key(key))
    else:
        fixed = 132 if tag == 2 else 180
        require(len(data) >= fixed, 'hash_descriptor_size')
        if tag == 2:
            (image, algorithm, name_len, salt_len, digest_len, flags,
             reserved) = struct.unpack_from('>Q32sIIII60s', data, 16)
        else:
            (version, image, tree_offset, tree_size, data_block, hash_block,
             fec_roots, fec_offset, fec_size, algorithm, name_len, salt_len,
             digest_len, flags, reserved) = struct.unpack_from(
                 '>IQQQIIIQQ32sIIII60s', data, 16)
            require(version == 1, 'hashtree_version')
            require(all(512 <= n <= 65536 and n & (n - 1) == 0
                        for n in (data_block, hash_block)), 'hashtree_block_size')
            require(image % data_block == 0 and tree_offset % hash_block == 0 and
                    tree_size % hash_block == 0, 'hashtree_alignment')
            require(tree_offset >= image and tree_offset + tree_size < 1 << 64,
                    'hashtree_geometry')
            require((fec_roots == 0 and fec_size == 0) or
                    (0 < fec_roots <= 255 and fec_size > 0 and
                     fec_offset >= tree_offset + tree_size and
                     fec_offset + fec_size < 1 << 64), 'fec_geometry')
            out.update(dm_verity_version=version, tree_offset_bytes=tree_offset,
                       tree_size_bytes=tree_size, data_block_bytes=data_block,
                       hash_block_bytes=hash_block, fec_roots=fec_roots,
                       fec_offset_bytes=fec_offset, fec_size_bytes=fec_size)
        require(not any(reserved) and flags & ~(1 if tag == 2 else 3) == 0,
                'hash_descriptor_fields')
        require(b'\0' in algorithm and not any(algorithm[algorithm.index(0):]),
                'hash_algorithm_encoding')
        algorithm = algorithm.split(b'\0', 1)[0]
        require(algorithm in (b'sha1', b'sha256', b'sha512'), 'hash_algorithm')
        hash_name = algorithm.decode('ascii')
        require(digest_len in (0, hashlib.new(hash_name).digest_size), 'digest_size')
        name, salt, digest = variable_fields(data, fixed, [name_len, salt_len, digest_len])
        out.update(image_size_bytes=image, hash_algorithm=hash_name, flags=flags,
                   salt_bytes=salt_len, salt_sha256=sha(salt), digest_bytes=digest_len,
                   digest_sha256=sha(digest), persistent_digest_required=digest_len == 0)
    if tag in (1, 2, 4):
        name = label(name)
        out.update(partition_name=name,
                   selected_partition_label=name if flags & 1 else name + slot_suffix)
    return out


def inspect_bytes(data, image_label, slot_suffix, role):
    label(image_label.encode('ascii'))
    require(slot_suffix in ('', '_a', '_b') and role in ('root', 'chained'), 'context')
    require(256 <= len(data) <= MAX_METADATA_BYTES, 'input_size')
    h = dict(zip(HEADER_FIELDS, HEADER.unpack_from(data)))
    require(h['magic'] == b'AVB0', 'magic')
    require(h['major'] == 1 and h['minor'] <= 3, 'unsupported_version')
    require(h['algorithm'] in ALGORITHMS, 'unsupported_algorithm')
    require(h['auth_bytes'] % 64 == h['aux_bytes'] % 64 == 0 and
            256 + h['auth_bytes'] + h['aux_bytes'] == len(data), 'block_sizes')
    require(not any(h['reserved']) and h['release'][-1] == 0, 'header_reserved')
    require(h['flags'] & ~3 == 0, 'header_flags')
    if role == 'chained':
        require(h['flags'] == h['rollback_index_location'] == 0, 'chained_header')
    auth = data[256:256 + h['auth_bytes']]
    aux_offset = 256 + len(auth)
    aux = data[aux_offset:]
    values = {}
    for name, block in [('hash', auth), ('signature', auth), ('public_key', aux),
                        ('key_metadata', aux), ('descriptors', aux)]:
        values[name] = region(block, h[name + '_offset'], h[name + '_bytes'])
    for names in [('hash', 'signature'), ('public_key', 'key_metadata', 'descriptors')]:
        ranges = sorted((h[n + '_offset'], h[n + '_offset'] + h[n + '_bytes'])
                        for n in names if h[n + '_bytes'])
        require(all(a[1] <= b[0] for a, b in zip(ranges, ranges[1:])), 'region_overlap')
    algorithm, hash_name, bits = ALGORITHMS[h['algorithm']]
    key = None
    internal_hash_matches = None
    if hash_name is None:
        require(not any(values[n] for n in ('hash', 'signature', 'public_key')),
                'unsigned_fields')
    else:
        require(len(values['hash']) == hashlib.new(hash_name).digest_size and
                len(values['signature']) == bits // 8, 'algorithm_sizes')
        key = public_key(values['public_key'], bits)
        require(hashlib.new(hash_name, data[:256] + aux).digest() == values['hash'],
                'internal_hash_mismatch')
        internal_hash_matches = True
    descriptors = []
    seen = set()
    table = values['descriptors']
    at = 0
    while at < len(table):
        require(len(table) - at >= 16, 'descriptor_header')
        following = struct.unpack_from('>Q', table, at + 8)[0]
        require(following % 8 == 0 and following <= len(table) - at - 16,
                'descriptor_bounds')
        entry = descriptor(table[at:at + 16 + following],
                           aux_offset + h['descriptors_offset'] + at, slot_suffix)
        if 'selected_partition_label' in entry:
            target = entry['selected_partition_label']
            require(target not in seen, 'duplicate_partition_descriptor')
            seen.add(target)
        descriptors.append(entry)
        at += 16 + following
    return {'schema_version': 1, 'kind': 'bounded-vbmeta-metadata-inspection',
            'status': 'metadata_checked_not_authenticated', 'label': image_label,
            'role': role, 'selected_slot_suffix': slot_suffix,
            'selected_slot_is_runtime_verified': False,
            'input_bytes': len(data), 'input_sha256': sha(data),
            'header_sha256': sha(data[:256]), 'required_libavb_version': [h['major'], h['minor']],
            'algorithm_type': h['algorithm'], 'algorithm': algorithm,
            'flags': h['flags'], 'declares_hashtree_disabled': bool(h['flags'] & 1),
            'declares_verification_disabled': bool(h['flags'] & 2),
            'authentication_block_bytes': len(auth), 'auxiliary_block_bytes': len(aux),
            'auxiliary_block_offset': aux_offset, 'public_key': key,
            'signature_bytes': len(values['signature']), 'signature_sha256': sha(values['signature']),
            'key_metadata_bytes': len(values['key_metadata']),
            'key_metadata_sha256': sha(values['key_metadata']),
            'rollback_index_location': h['rollback_index_location'],
            'rollback_index_sha256': sha(data[112:120]),
            'release_field_sha256': sha(h['release']),
            'internal_hash_matches': internal_hash_matches,
            'descriptor_count': len(descriptors), 'descriptors': descriptors,
            'signature_checked': False, 'trusted_key_verified': False,
            'rollback_protection_verified': False, 'vbmeta_authenticated': False,
            'partition_contents_verified': False, 'android_boot_verified': False,
            'source_commit': AOSP_COMMIT,
            'sources': [AOSP_BASE + '/libavb/' + f for f in
                        ('avb_vbmeta_image.h', 'avb_vbmeta_image.c', 'avb_descriptor.h',
                         'avb_descriptor.c', 'avb_crypto.h', 'avb_chain_partition_descriptor.h',
                         'avb_hash_descriptor.h', 'avb_hashtree_descriptor.h',
                         'avb_property_descriptor.h', 'avb_kernel_cmdline_descriptor.h')]}


def inspect_file(path, image_label, slot_suffix, role):
    def identity(s):
        return (s.st_dev, s.st_ino, s.st_mode, s.st_size, s.st_mtime_ns, s.st_ctime_ns)

    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), 'input_type')
    require(256 <= before.st_size <= MAX_METADATA_BYTES, 'input_size')
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        require(identity(os.fstat(fd)) == identity(before), 'input_changed')
        raw = os.pread(fd, before.st_size, 0)
        require(len(raw) == before.st_size, 'short_read')
        require(os.pread(fd, before.st_size, 0) == raw and
                identity(os.fstat(fd)) == identity(before) and
                identity(path.lstat()) == identity(before), 'input_changed')
    finally:
        os.close(fd)
    result = inspect_bytes(raw, image_label, slot_suffix, role)
    result.update(input_kind='regular_metadata_file', input_observed_unchanged=True,
                  file_read_passes=2, source_device_read_by_this_tool=False)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--label', required=True)
    parser.add_argument('--slot-suffix', required=True, choices=('', '_a', '_b'))
    parser.add_argument('--role', required=True, choices=('root', 'chained'))
    parser.add_argument('--output', type=Path, help='New receipt; never overwrite an existing path')
    args = parser.parse_args(argv)
    if args.output and (args.output.exists() or args.output.is_symlink()):
        parser.error('Output already exists')
    try:
        report = inspect_file(args.input, args.label, args.slot_suffix, args.role)
        status = 0
    except (InvalidVBMeta, OSError, UnicodeError) as exc:
        report = {'schema_version': 1, 'status': 'rejected',
                  'error_code': str(exc) if isinstance(exc, InvalidVBMeta) else 'input_io',
                  'vbmeta_authenticated': False, 'partition_contents_verified': False,
                  'android_boot_verified': False}
        status = 1
    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        with args.output.open('x', encoding='utf-8') as target:
            target.write(text)
    else:
        print(text, end='')
    return status


if __name__ == '__main__':
    raise SystemExit(main())
