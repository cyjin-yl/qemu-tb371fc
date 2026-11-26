#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent literal oracle and exact device-section wire contract.

Never imports, parses or generates QEMU device implementation. Identity values
are supplied by a separately reviewed reference-policy document. UNBOUND is an
explicit zero-placeholder profile, never a working emulated hardware identity.
"""
import hashlib
import json
from pathlib import Path
import re
import struct

TYPE = 'qcom-sm8250-qce-bam'
PATH = '/machine/qce-bam'
BASE, SIZE, CRYPTO = 0x01dc4000, 0x3c000, 0x1c000
GUARD_BYTES, PIPE_BYTES, PAYLOAD_BYTES = 116, 32, 248
IDENTITIES = ('qce-version', 'qce-engines', 'bam-config', 'bam-revision',
              'bam-num-pipes', 'bam-ee-attr', 'bam-ee-initial-irq-mask')
STATIC_UINT = {'abi-identity': 0x51434231, 'physical-base': BASE, 'window-bytes': SIZE,
               'crypto-base': 0x01de0000, 'crypto-window-bytes': 0x20000,
               'bam-base': BASE, 'bam-window-bytes': 0x24000,
               'first-pipe': 4, 'pipe-count': 4, 'ee-index': 0,
               'vmstate-guard-bytes': GUARD_BYTES, 'vmstate-pipe-bytes': PIPE_BYTES,
               'vmstate-payload-bytes': PAYLOAD_BYTES, 'irq-output-count': 1,
               'clock-input-count': 0, 'clock-output-count': 0, 'timer-count': 0}
PIPE_FIELDS = ('control', 'reset', 'irq-enable', 'irq-status', 'descriptor-lo',
               'descriptor-hi', 'fifo-size', 'threshold')
FALSE_CAPABILITIES = ('dma-modeled', 'crypto-operations-modeled', 'secure-key-modeled',
                      'firmware-modeled', 'physical-identity-measured',
                      'guest-interrupt-handling-verified')
GLOBAL_UINT = tuple(STATIC_UINT) + IDENTITIES + ('bam-ee-irq-mask', 'irq-level', 'enabled-profile')
BOOL_FIELDS = ('reference-bound', 'identity-known') + FALSE_CAPABILITIES
STRING_FIELDS = ('reference-profile-name', 'reference-profile-sha256')
PIPE_QOM = tuple(f'pipe-{pipe}-{field}' for pipe in range(4, 8) for field in PIPE_FIELDS)
READONLY_FIELDS = GLOBAL_UINT + PIPE_QOM + BOOL_FIELDS + STRING_FIELDS
OBSERVATION_FIELDS = READONLY_FIELDS + ('enabled-reference',)
assert len(GLOBAL_UINT) == 28 - 1  # 27 globals; 59 uint getters including four pipes.
assert len(READONLY_FIELDS) == 69 and len(OBSERVATION_FIELDS) == 70

REG = {'qce-version': 0x36000, 'qce-engines': 0x36108, 'bam-config': 0x7c,
       'bam-revision': 0x1000, 'bam-num-pipes': 0x1008,
       'irq-sources': 0x3000, 'bam-ee-irq-mask': 0x3004,
       'irq-unmasked': 0x3008, 'bam-ee-attr': 0x300c}
PIPE_REG = {'control': 0x000, 'reset': 0x004, 'irq-status': 0x010,
            'irq-clear': 0x014, 'irq-enable': 0x018, 'software-offset': 0x800,
            'doorbell': 0x818, 'descriptor-lo': 0x81c, 'fifo-size': 0x820,
            'threshold': 0x828, 'descriptor-hi': 0x914}

# Exact big-endian VMSTATE field widths. These are an external test contract,
# not native C offsetof()/sizeof(): native structure padding is irrelevant.
GUARD_FIELDS = (
    ('abi-identity', 0, 4), ('enabled-profile', 4, 4), ('profile-digest', 8, 32),
    ('crypto-base', 40, 8), ('crypto-window-bytes', 48, 4), ('bam-base', 52, 8),
    ('bam-window-bytes', 60, 4), ('physical-base', 64, 8), ('window-bytes', 72, 4),
    ('first-pipe', 76, 4), ('pipe-count', 80, 4), ('ee-index', 84, 4),
    ('qce-version', 88, 4), ('qce-engines', 92, 4), ('bam-config', 96, 4),
    ('bam-revision', 100, 4), ('bam-num-pipes', 104, 4), ('bam-ee-attr', 108, 4),
    ('bam-ee-initial-irq-mask', 112, 4))
MUTABLE_FIELDS = (('bam-ee-irq-mask', 116, 4),) + tuple(
    (f'pipe-{pipe}-{field}', 120 + (pipe - 4) * 32 + index * 4, 4)
    for pipe in range(4, 8) for index, field in enumerate(PIPE_FIELDS))
assert GUARD_FIELDS[-1][1] + GUARD_FIELDS[-1][2] == GUARD_BYTES
assert MUTABLE_FIELDS[-1][1] + MUTABLE_FIELDS[-1][2] == PAYLOAD_BYTES


def pipe_reg(pipe, name):
    assert 4 <= pipe <= 7
    return 0x13000 + pipe * 0x1000 + PIPE_REG[name]


def read_profile(path):
    raw = Path(path).read_bytes()
    obj = json.loads(raw)
    assert set(obj) == {'schema', 'bound', 'name', 'identity', 'policy', 'source_inputs'}, obj
    assert obj['schema'] == 'tb371fc-qce-bam-reference-profile-v1'
    assert type(obj['bound']) is bool
    assert isinstance(obj['name'], str) and re.fullmatch(r'[A-Za-z0-9._-]{1,96}', obj['name'])
    assert set(obj['identity']) == set(IDENTITIES)
    assert all(type(v) is int and 0 <= v <= 0xffffffff for v in obj['identity'].values())
    assert obj['policy']['physical_identity_measured'] is False
    assert obj['policy']['dma_execution_modeled'] is False
    assert obj['policy']['crypto_execution_modeled'] is False
    assert obj['policy']['secure_firmware_executed'] is False
    assert obj['policy']['guest_interrupt_handling_verified'] is False
    assert obj['policy']['cold_reset_is_reference_policy'] is True
    assert isinstance(obj['source_inputs'], list)
    if obj['bound']:
        assert obj['name'] != 'UNBOUND'
        assert obj['policy']['identity_selection_reviewed'] is True
        assert obj['policy']['initial_remote_state_reviewed'] is True
        assert isinstance(obj['policy']['basis'], str) and obj['policy']['basis'].strip()
        assert obj['source_inputs'], 'Bound profile must pin primary source inputs'
        for item in obj['source_inputs']:
            assert isinstance(item['path'], str) and not Path(item['path']).is_absolute()
            assert '..' not in Path(item['path']).parts
            assert type(item['bytes']) is int and item['bytes'] > 0
            assert re.fullmatch(r'[0-9a-f]{64}', item['sha256'])
        v = obj['identity']
        assert v['qce-version'] >> 24 == 5
        assert 2 < (v['bam-revision'] & 255) <= 0x2f
        assert 8 <= (v['bam-num-pipes'] & 255) <= 31
        assert v['bam-ee-attr'] & 0x800000f0 == 0x800000f0
        digest = hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'),
                                          ensure_ascii=True).encode()).hexdigest()
    else:
        assert obj['name'] == 'UNBOUND'
        assert not any(obj['identity'].values())
        assert obj['policy']['identity_selection_reviewed'] is False
        assert obj['policy']['initial_remote_state_reviewed'] is False
        digest = '0' * 64
    return {'document': obj, 'bound': obj['bound'], 'name': obj['name'],
            'identity': obj['identity'], 'digest': digest,
            'file_sha256': hashlib.sha256(raw).hexdigest(), 'file_bytes': len(raw)}


def cold_expected(profile, enabled):
    result = dict(STATIC_UINT)
    result.update({name: value if enabled else 0 for name, value in profile['identity'].items()})
    result.update({name: False for name in FALSE_CAPABILITIES})
    result.update({name: 0 for name in PIPE_QOM})
    result.update({'bam-ee-irq-mask': result['bam-ee-initial-irq-mask'], 'irq-level': 0,
                   'enabled-profile': int(enabled), 'enabled-reference': enabled,
                   'reference-bound': profile['bound'], 'identity-known': enabled and profile['bound'],
                   'reference-profile-name': profile['name'],
                   'reference-profile-sha256': profile['digest']})
    assert set(result) == set(OBSERVATION_FIELDS)
    return result


def snapshot(rig):
    result = {name: rig.qom(name) for name in OBSERVATION_FIELDS}
    for name in GLOBAL_UINT + PIPE_QOM:
        assert type(result[name]) is int and result[name] >= 0, (name, result[name])
    for name in BOOL_FIELDS + ('enabled-reference',):
        assert type(result[name]) is bool, (name, result[name])
    for name in STRING_FIELDS:
        assert type(result[name]) is str
    return result


def encode_snapshot(state):
    result = bytearray(PAYLOAD_BYTES)
    for name, offset, width in GUARD_FIELDS + MUTABLE_FIELDS:
        value = (bytes.fromhex(state['reference-profile-sha256']) if name == 'profile-digest'
                 else state[name].to_bytes(width, 'big'))
        assert len(value) == width
        result[offset:offset + width] = value
    return bytes(result)


def locate_section(data, snapshot_value):
    """Find one complete version-1 section and prove every payload byte.

    Does not attempt to parse unrelated QEMU device/RAM sections. A duplicate
    matching section, unexpected subsection, short payload or wrong footer is
    a failure. Never uses a guessed byte offset for stream corruption.
    """
    assert data[:4] == b'QEVM' and len(data) >= 8
    assert struct.unpack_from('>I', data, 4)[0] == 3
    name = TYPE.encode()
    # Count every named START/FULL candidate before checking instance/version.
    # A second same-name section must not disappear merely because its ABI
    # fields differ from the first valid instance.
    needle = bytes((len(name),)) + name
    matches, pos = [], 0
    while True:
        at = data.find(needle, pos)
        if at < 0:
            break
        pos = at + 1
        header, body = at - 5, at + len(needle) + 8
        if header >= 8 and data[header] in (1, 4):
            matches.append((header, body, at + 1))
    assert len(matches) == 1, matches
    header, body, name_at = matches[0]
    assert data[header] == 4, 'QCE/BAM uses an ordinary FULL section'
    assert len(data) >= body
    assert struct.unpack_from('>II', data, body - 8) == (0, 1), 'Instance/version mismatch'
    section_id = struct.unpack_from('>I', data, header + 1)[0]
    expected = encode_snapshot(snapshot_value)
    assert data[body:body + PAYLOAD_BYTES] == expected, 'Full QCE/BAM payload differs'
    footer = b'\x7e' + struct.pack('>I', section_id)
    assert data[body + PAYLOAD_BYTES:body + PAYLOAD_BYTES + 5] == footer
    return {'section_name': TYPE, 'instance_id': 0, 'version': 1, 'minimum_version': 1,
            'section_type': 4,
            'header_offset': header, 'name_offset': name_at, 'payload_offset': body,
            'version_offset': body - 4, 'section_id': section_id,
            'guard_bytes': GUARD_BYTES, 'mutable_bytes': PAYLOAD_BYTES - GUARD_BYTES,
            'payload_bytes': PAYLOAD_BYTES, 'footer_offset': body + PAYLOAD_BYTES,
            'payload_sha256': hashlib.sha256(expected).hexdigest(),
            'all_payload_bytes_match_pure_qom': True,
            'whole_machine_payload_oracle_claimed': False}


def mutation_fields():
    result = []
    # Digest first and last bytes are separate tests, so all guard fields and
    # both ends of the fixed 32-byte digest are checked.
    for name, offset, width in GUARD_FIELDS:
        if name == 'profile-digest':
            result += [(name + '-first', offset, 1, 1, 'guard'),
                       (name + '-last', offset + 31, 1, 1, 'guard')]
        else:
            result.append((name, offset, width, 1, 'guard'))
    result.append(('ee-outside-owned-mask', 116, 4, 1, 'mutable'))
    xor_by_field = {'control': 1, 'reset': 2, 'irq-enable': 0x100, 'irq-status': 1,
                    'descriptor-lo': 1, 'descriptor-hi': 0x10,
                    'fifo-size': 0x10000, 'threshold': 0x10000}
    for name, offset, width in MUTABLE_FIELDS[1:]:
        field = name.split('-', 2)[2]
        result.append((name, offset, width, xor_by_field[field], 'mutable'))
    return tuple(result)


FIELD_MUTATIONS = mutation_fields()
