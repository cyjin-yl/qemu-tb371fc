#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict RAM-record validation for the separately identified full production DT.

The 3120-byte decoder and every non-model condition are copied unchanged from
boot_smoke_gdb.py. This variant does not alter the record or suppress errors.
It is not an Android-boot or guest-shutdown verdict.
"""
from __future__ import annotations
import json
import struct

from boot_verdict import CHECKS, validate_counter_probe

EXPECTED_MODEL = "Qualcomm Technologies, Inc. kona QRD"

MAGIC = 0x3153455243464254


RECORD_SIZE = 3120


PROBE_CAPACITY = 2048


FIELDS = ("magic", "version", "structure_size", "completed",
          "checks_completed", "pass_mask", "fail_mask", "failures",
          "elapsed_ns", "counter_probe_bytes", "counter_probe_wait_status",
          "probe_truncated", "start_monotonic_ns", "finish_monotonic_ns")


STRINGS = (("uname_sysname", 128), ("uname_nodename", 128),
           ("uname_release", 128), ("uname_version", 128),
           ("uname_machine", 128), ("uname_domainname", 128),
           ("online", 64), ("model", 128), ("probe_stdout", 2048))


def decode_record(blob):
    if len(blob) != RECORD_SIZE:
        raise ValueError(f"result length {len(blob)} != {RECORD_SIZE}")
    record = dict(zip(FIELDS, struct.unpack_from("<14Q", blob)))
    for name in ("elapsed_ns", "counter_probe_wait_status"):
        if record[name] >= 1 << 63:
            record[name] -= 1 << 64
    offset = 112
    for name, length in STRINGS:
        raw = blob[offset:offset + length]
        if b"\0" not in raw:
            raise ValueError(f"{name} is not NUL terminated")
        record[name] = raw.split(b"\0", 1)[0].decode("utf-8", errors="strict")
        offset += length
    return record


def validate_record(record, native):
    errors = []
    required = {
        "magic": MAGIC, "version": 1, "structure_size": RECORD_SIZE,
        "completed": 1, "checks_completed": 0xff, "pass_mask": 0xff,
        "fail_mask": 0, "failures": 0, "probe_truncated": 0,
        "counter_probe_wait_status": 0, "uname_sysname": "Linux",
        "uname_machine": "aarch64", "online": "0-7\n",
    }
    for name, value in required.items():
        if record.get(name) != value:
            errors.append(f"{name}: expected {value!r}, got {record.get(name)!r}")
    if not record.get("uname_release"):
        errors.append("empty uname release")
    if record.get("model") != EXPECTED_MODEL:
        errors.append(f"device tree model does not equal {EXPECTED_MODEL!r}")
    if record.get("elapsed_ns", -1) < 20_000_000:
        errors.append("nanosleep/monotonic interval shorter than 20ms")
    if not (0 < record.get("start_monotonic_ns", 0) <
            record.get("finish_monotonic_ns", 0)):
        errors.append("invalid monotonic start/finish timestamps")
    raw = record.get("probe_stdout", "").encode()
    if not (0 < len(raw) < PROBE_CAPACITY and
            len(raw) == record.get("counter_probe_bytes")):
        errors.append("probe stdout byte count mismatch")
    probe = None
    try:
        # Exactly one complete JSON document; extra non-whitespace is rejected.
        probe = json.loads(record.get("probe_stdout", ""))
        validate_counter_probe(probe, native)
    except (ValueError, TypeError, KeyError) as error:
        errors.append(f"probe JSON: {error}")
    return errors, probe

