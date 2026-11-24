#!/usr/bin/env python3
"""Synthetic format and qualification tests; no captured firmware or real keys."""

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

import inspect_vbmeta_metadata as v


KEY = struct.pack('>II', 2048, 1) + bytes(512)  # Intentionally not a valid RSA key.


def desc(tag, body):
    body += bytes((-len(body)) % 8)
    return struct.pack('>QQ', tag, len(body)) + body


def prop(key=b'private-test-key', value=b'private-test-value'):
    return desc(0, struct.pack('>QQ', len(key), len(value)) + key + b'\0' + value + b'\0')


def hashed(name=b'boot', flags=0, digest_bytes=32):
    return desc(2, struct.pack('>Q32sIIII60s', 4096, b'sha256', len(name), 3,
                              digest_bytes, flags, bytes(60)) + name + b'abc' + bytes(digest_bytes))


def hashtree(name=b'system', tree_offset=4096, data_block=4096, fec_roots=0):
    return desc(1, struct.pack('>IQQQIIIQQ32sIIII60s', 1, 4096, tree_offset, 4096,
                              data_block, 4096, fec_roots, 0, 0, b'sha256',
                              len(name), 3, 32, 0, bytes(60)) + name + b'abc' + bytes(32))


def chain(name=b'vbmeta_system', flags=0, location=2):
    return desc(4, struct.pack('>IIII60s', location, len(name), len(KEY), flags,
                              bytes(60)) + name + KEY)


def image(table=b'', algorithm=1, **updates):
    key = KEY if algorithm else b''
    aux = table + key
    aux += bytes((-len(aux)) % 64)
    h = dict.fromkeys(v.HEADER_FIELDS, 0)
    h.update(magic=b'AVB0', major=1, auth_bytes=320 if algorithm else 0,
             aux_bytes=len(aux), algorithm=algorithm, hash_bytes=32 if algorithm else 0,
             signature_offset=32 if algorithm else 0, signature_bytes=256 if algorithm else 0,
             public_key_offset=len(table), public_key_bytes=len(key),
             key_metadata_offset=len(table)+len(key), descriptors_bytes=len(table),
             rollback_index=123, release=b'SYNTHETIC TEST INPUT'.ljust(48, b'\0'), reserved=bytes(80))
    h.update(updates)
    header = v.HEADER.pack(*(h[k] for k in v.HEADER_FIELDS))
    auth = hashlib.sha256(header+aux).digest()+b'\xa3'*256+bytes(32) if algorithm else b''
    return header + auth + aux


class VBMetaTests(unittest.TestCase):
    def inspect(self, raw, **kw):
        return v.inspect_bytes(raw, kw.get('label', 'vbmeta_a'),
                               kw.get('suffix', '_a'), kw.get('role', 'root'))

    def rejects(self, raw, code=None, **kw):
        with self.assertRaises(v.InvalidVBMeta) as caught:
            self.inspect(raw, **kw)
        if code:
            self.assertEqual(str(caught.exception), code)

    def test_all_five_types_and_redaction(self):
        command = b'private-test-kernel-command'
        table = prop()+hashed()+hashtree()+chain()+desc(3, struct.pack('>II', 1, len(command))+command)
        r = self.inspect(image(table))
        self.assertTrue(r['internal_hash_matches'])
        self.assertEqual([x['kind'] for x in r['descriptors']],
                         ['property', 'hash', 'hashtree', 'chain', 'kernel_cmdline'])
        self.assertEqual([x['selected_partition_label'] for x in r['descriptors']
                          if 'selected_partition_label' in x], ['boot_a', 'system_a', 'vbmeta_system_a'])
        for text in ['private-test-key', 'private-test-value', command.decode(), 'SYNTHETIC TEST INPUT']:
            self.assertNotIn(text, json.dumps(r))
        self.assertNotIn('rollback_index', r)
        for field in ['signature_checked', 'trusted_key_verified', 'rollback_protection_verified',
                      'vbmeta_authenticated', 'partition_contents_verified', 'android_boot_verified']:
            self.assertFalse(r[field])

    def test_signature_mutation_is_not_authentication(self):
        raw = bytearray(image(hashed()))
        original = self.inspect(raw)
        raw[288] ^= 1
        altered = self.inspect(raw)
        self.assertTrue(altered['internal_hash_matches'])
        self.assertNotEqual(original['signature_sha256'], altered['signature_sha256'])
        self.assertFalse(altered['signature_checked'])
        self.assertFalse(altered['vbmeta_authenticated'])

    def test_internal_hash_mismatch(self):
        raw = bytearray(image(prop()))
        raw[-1] ^= 1
        self.rejects(raw, 'internal_hash_mismatch')

    def test_short_oversize_and_trailing_partition_padding(self):
        good = image()
        for raw in [good[:255], good[:-1], good+b'\0', bytes(65537)]:
            self.rejects(raw)

    def test_magic_version_algorithm(self):
        for changes in [dict(magic=b'AVB1'), dict(major=2), dict(minor=4), dict(algorithm=7)]:
            self.rejects(image(**changes))

    def test_blocks_and_offset_overflow(self):
        for changes in [dict(auth_bytes=321), dict(aux_bytes=2**64-64),
                        dict(hash_offset=2**64-1), dict(descriptors_bytes=2**64-1),
                        dict(public_key_offset=2**64-1), dict(key_metadata_bytes=2**64-1)]:
            self.rejects(image(**changes))

    def test_overlap_and_algorithm_shape(self):
        self.rejects(image(signature_offset=0), 'region_overlap')
        self.rejects(image(hash_bytes=31), 'algorithm_sizes')
        self.rejects(image(signature_bytes=128), 'algorithm_sizes')
        self.rejects(image(public_key_bytes=512), 'public_key_shape')

    def test_reserved_flags_and_chained_role(self):
        self.rejects(image(reserved=b'\x01'+bytes(79)), 'header_reserved')
        self.rejects(image(release=b'a'*48), 'header_reserved')
        self.rejects(image(flags=4), 'header_flags')
        for changes in [dict(flags=1), dict(rollback_index_location=2)]:
            self.rejects(image(**changes), 'chained_header', role='chained')
        r = self.inspect(image(flags=3))
        self.assertTrue(r['declares_verification_disabled'])
        self.assertFalse(r['vbmeta_authenticated'])

    def test_unsigned_has_no_digest_claim(self):
        r = self.inspect(image(prop(), algorithm=0))
        self.assertIsNone(r['internal_hash_matches'])
        self.assertIsNone(r['public_key'])
        self.assertFalse(r['vbmeta_authenticated'])
        self.rejects(image(prop(), algorithm=0, public_key_bytes=1))

    def test_descriptor_bounds_alignment_unknown_tag(self):
        for table in [bytes(8), struct.pack('>QQ', 0, 2**64-8),
                      struct.pack('>QQ', 0, 1)+b'\0', desc(5, b'')]:
            self.rejects(image(table))

    def test_property_terminators_padding_and_duplicates(self):
        self.assertEqual(self.inspect(image(prop()+prop()))['descriptor_count'], 2)
        bad = bytearray(prop(b'key', b'value'));bad[35] = 1
        self.rejects(image(bad), 'property_terminator')
        self.rejects(image(desc(0, struct.pack('>QQ', 2**64-1, 0))), 'region_bounds')
        bad = bytearray(prop(b'k', b'v'));bad[-1] = 1
        self.rejects(image(bad), 'descriptor_padding')

    def test_partition_names_flags_and_duplicate_targets(self):
        for name in [b'../boot', b'.', b'..', b'', b'bad\0name', b'\xff']:
            self.rejects(image(hashed(name)), 'partition_label')
        self.rejects(image(hashed(flags=2)), 'hash_descriptor_fields')
        self.rejects(image(hashed()+hashed()), 'duplicate_partition_descriptor')
        self.assertEqual(self.inspect(image(hashed(flags=1)))['descriptors'][0]['selected_partition_label'], 'boot')
        self.assertEqual(self.inspect(image(hashed()), suffix='_b')['descriptors'][0]['selected_partition_label'], 'boot_b')

    def test_chain_shape_and_explicit_slot(self):
        for raw in [chain(location=0), chain(flags=2)]:
            self.rejects(image(raw), 'chain_fields')
        r = self.inspect(image(chain()))
        self.assertEqual(r['descriptors'][0]['public_key']['sha256'], v.sha(KEY))
        self.assertFalse(r['selected_slot_is_runtime_verified'])
        self.assertFalse(r['trusted_key_verified'])

    def test_hash_and_tree_geometry(self):
        self.rejects(image(hashed(digest_bytes=20)), 'digest_size')
        self.assertTrue(self.inspect(image(hashed(digest_bytes=0)))['descriptors'][0]['persistent_digest_required'])
        self.rejects(image(hashtree(tree_offset=0)), 'hashtree_geometry')
        self.rejects(image(hashtree(data_block=1000)), 'hashtree_block_size')
        self.rejects(image(hashtree(fec_roots=2)), 'fec_geometry')

    def test_regular_only_and_actual_second_read(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'input';p.write_bytes(image())
            link = Path(td)/'link';link.symlink_to(p)
            with self.assertRaisesRegex(v.InvalidVBMeta, 'input_type'):
                v.inspect_file(link, 'vbmeta_a', '_a', 'root')
            fifo = Path(td)/'fifo';os.mkfifo(fifo)
            with self.assertRaisesRegex(v.InvalidVBMeta, 'input_type'):
                v.inspect_file(fifo, 'vbmeta_a', '_a', 'root')
            original, calls = os.pread, []
            def mutate(fd, count, offset):
                raw = original(fd, count, offset);calls.append(count)
                if len(calls) == 1:
                    data = bytearray(p.read_bytes());data[-1] ^= 1;p.write_bytes(data)
                return raw
            with mock.patch.object(v.os, 'pread', side_effect=mutate):
                with self.assertRaisesRegex(v.InvalidVBMeta, 'input_changed'):
                    v.inspect_file(p, 'vbmeta_a', '_a', 'root')
            self.assertEqual(len(calls), 2)

    def test_cli_new_receipt_and_input_preservation(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)/'input';p.write_bytes(image(prop()))
            output = Path(td)/'receipt.json'
            args = ['--input', str(p), '--label', 'vbmeta_a', '--slot-suffix', '_a',
                    '--role', 'root', '--output', str(output)]
            self.assertEqual(v.main(args), 0)
            saved, before = output.read_bytes(), p.read_bytes()
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                v.main(args)
            self.assertEqual(output.read_bytes(), saved)
            self.assertEqual(p.read_bytes(), before)
            p.write_bytes(bytes(256));out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(v.main(args[:-2]), 1)
            self.assertEqual(json.loads(out.getvalue())['status'], 'rejected')


if __name__ == '__main__':
    unittest.main()
