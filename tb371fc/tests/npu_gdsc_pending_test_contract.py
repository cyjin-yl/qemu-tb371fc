#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Literal bounded NPU reference contract; no device source or work on import."""
import re
import struct

TYPE = 'qcom-npu-gdsc'
PATH = '/machine/npu-gdsc'
ADDRESS, SIZE = 0x09981004, 4
REGION = TYPE + '.npu-core-control'
IDENTITY, WRITE_MASK, POWER = 0x4e504731, 0xf803, 0x80000000
RAM = 0x81010000
WITNESS = bytes.fromhex('4e50553261373c94de08b251f60a83c75d19e042b3a6518c902def7436a8250b')
GLOBALS = ('enabled-profile', 'reference-identity', 'domain-count',
           'register-bytes', 'mapped-bytes', 'parent-resource-index',
           'cold-control', 'control-write-mask', 'power-status-mask')
DOMAIN = ('name', 'address', 'dt-flags', 'timeout-us', 'control',
          'request-known', 'requested', 'state-known', 'powered',
          'hw-request-known', 'hw-request')
GETTERS = ('enabled-reference', 'unknown-feedback-pending') + GLOBALS + (
    'parent-known', 'parent-on') + tuple('domain0.' + key for key in DOMAIN)
ORDINARY = ('default-strict', 'explicit-strict', 'default-disabled',
            'disabled-pending', 'pending-identity', 'pending-poll-rmw',
            'pending-power-truth', 'pending-known-output', 'pending-reset',
            'pending-strict-access', 'paused-tcg-pending')
SOURCES = ('pending-unknown-source', 'pending-unknown-incoming',
           'pending-unknown-roundtrip', 'pending-known-source',
           'pending-known-incoming', 'pending-known-roundtrip',
           'strict-source', 'disabled-source')
NEGATIVE = ('strict-to-pending', 'pending-to-strict', 'disabled-to-pending',
            'pending-to-disabled', 'corrupt-profile', 'corrupt-domain-base',
            'corrupt-hw-mode')
ORDERED = ORDINARY + SOURCES + NEGATIVE


def expected(*, enabled=True, pending=True, control=1, parent_known=False,
             parent_on=False, hw_known=False, hw_request=False):
    assert type(enabled) is type(pending) is bool
    assert type(control) is int and control & ~WRITE_MASK == 0 and not control & 2
    # Enumerate unknown parent supply possibilities independently of C formulas.
    parents = (parent_on,) if parent_known else (False, True)
    requested = not bool(control & 1)
    states = {bool(parent and requested) for parent in parents}
    values = (2 if enabled and pending else int(enabled), IDENTITY, 1, 4, 4,
              0xffffffff, 1, WRITE_MASK, POWER)
    domain = ('npu-core', ADDRESS, 1, 100, control, True, requested,
              enabled and len(states) == 1, enabled and states == {True},
              bool(hw_known), bool(hw_request))
    return {'enabled-reference': enabled, 'unknown-feedback-pending': pending,
            **dict(zip(GLOBALS, values)), 'parent-known': bool(parent_known),
            'parent-on': bool(parent_on),
            **{'domain0.' + k: v for k, v in zip(DOMAIN, domain)}}


def payload(snapshot):
    raw = struct.pack('>9I', *(snapshot[k] for k in GLOBALS))
    raw += struct.pack('>BB', snapshot['parent-known'], snapshot['parent-on'])
    raw += struct.pack('>IQIIIBB', 0, snapshot['domain0.address'],
                       snapshot['domain0.dt-flags'], snapshot['domain0.timeout-us'],
                       snapshot['domain0.control'], snapshot['domain0.hw-request-known'],
                       snapshot['domain0.hw-request'])
    assert len(raw) == 64
    return raw


def locate(raw):
    assert raw[:4] == b'QEVM'
    name = TYPE.encode()
    needle = bytes([len(name)]) + name
    hits, start = [], 0
    while True:
        pos = raw.find(needle, start)
        if pos < 0:
            break
        head = pos - 5
        if head >= 8 and raw[head] in (1, 4):
            hits.append((head, pos + len(needle)))
        start = pos + 1
    assert len(hits) == 1, hits
    head, meta = hits[0]
    assert raw[head] == 4
    assert raw[meta:meta + 8] == struct.pack('>II', 0, 1)
    begin = meta + 8
    section_id = int.from_bytes(raw[head + 1:head + 5], 'big')
    footer = b'\x7e' + struct.pack('>I', section_id)
    assert raw[begin + 64:begin + 69] == footer
    return {'name': TYPE, 'section_type': 4, 'instance_id': 0, 'version': 1,
            'section_id': section_id, 'header_offset': head, 'payload_offset': begin,
            'payload_bytes': 64, 'footer_offset': begin + 64, 'footer_hex': footer.hex()}


def core_diagnostics(text):
    return [(op, int(addr, 16), int(size), region, reason) for op, addr, size, region, reason in
            re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
                       r"size ([0-9]+), region '([^']+)', reason: (.+)", text)]


def mappings(text):
    """Parse all system-memory FlatView leaves, preserving complete names."""
    sections, section = [], []
    for line in text.splitlines():
        if line.startswith('FlatView #'):
            if section:
                sections.append(section)
            section = []
        section.append(line)
    if section:
        sections.append(section)
    memory = [part for part in sections if any(
        re.fullmatch(r'\s*AS "memory", root: system\s*', line) for line in part)]
    assert len(memory) == 1
    rows = []
    for line in memory[0]:
        match = re.fullmatch(r'\s*([0-9a-fA-F]+)-([0-9a-fA-F]+) '
                             r'\(prio -?[0-9]+, (?:romd|rom|ram|i/o|container)\): (\S(?:.*\S)?)\s*', line)
        if match:
            a, b, name = match.groups()
            rows.append((int(a, 16), int(b, 16), name))
        elif re.match(r'\s*(?:0[xX])?[0-9a-fA-F]+\s*-\s*(?:0[xX])?[0-9a-fA-F]+', line):
            raise AssertionError('unsupported hexadecimal-range leaf')
    overlap = [row for row in rows if row[0] <= ADDRESS + 3 and row[1] >= ADDRESS]
    assert overlap == [(ADDRESS, ADDRESS + 3, REGION)], overlap
    named = [row for row in rows if row[2].startswith(TYPE)]
    assert named == overlap, named
    return {'active_leaves': [list(row) for row in overlap], 'mapped_bytes': 4}
