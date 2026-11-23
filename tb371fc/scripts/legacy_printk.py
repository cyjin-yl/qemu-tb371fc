#!/usr/bin/env python3
"""Read a stopped Linux 4.x printk ring via QMP/HMP using matching symbols."""
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations
import hashlib
import json
import re
import struct
from pathlib import Path

NAMES = ("linux_banner", "log_buf", "log_buf_len", "log_first_idx", "log_next_idx")

def decode_ring(data, first, next_index):
    if len(data) < 16 or not 0 <= first < len(data) or not 0 <= next_index < len(data):
        raise ValueError("invalid printk ring geometry")
    rows = []
    index = first
    seen = set()
    while index != next_index:
        if index in seen or index + 16 > len(data):
            raise ValueError("printk ring loop or truncated header")
        seen.add(index)
        stamp, length, text_length, dict_length, facility, flags = struct.unpack_from("<QHHHBB", data, index)
        if length == 0:
            index = 0
            continue
        if length < 16 or length % 4 or index + length > len(data) or 16 + text_length + dict_length > length:
            raise ValueError(f"invalid printk record at {index:#x}")
        text = data[index + 16:index + 16 + text_length].decode("utf-8", errors="replace")
        rows.append({"timestamp_ns": stamp, "facility": facility,
                     "level": (flags >> 5) & 7, "text": text})
        index += length
        if index == len(data):
            index = 0
    return rows

def capture_kernel_log(command, symbols_path, directory):
    symbols_path, directory = Path(symbols_path), Path(directory)
    wanted = {}
    for line in symbols_path.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[2] in NAMES:
            if parts[2] in wanted:
                raise ValueError("ambiguous printk symbol")
            wanted[parts[2]] = int(parts[0], 16)
    if set(wanted) != set(NAMES) or any(x < 0xffff000000000000 for x in wanted.values()):
        raise ValueError("matching canonical kernel symbols are required")
    def hmp(text):
        return command("human-monitor-command", {"command-line": text})
    def word(address, bits):
        text = hmp(f"x /1{'g' if bits == 64 else 'w'}x {address:#x}")
        values = re.findall(r":\s*(?:0x)?([0-9a-fA-F]{1,16})\b", text)
        if len(values) != 1:
            raise ValueError("cannot read printk metadata: " + text)
        return int(values[0], 16)
    def memory(name, address, size):
        path = directory / name
        if path.exists():
            raise FileExistsError(path)
        response = hmp(f"memsave {address:#x} {size} {json.dumps(str(path))}")
        if response or not path.exists() or path.stat().st_size != size:
            raise ValueError("memory capture did not complete: " + str(response))
        return path.read_bytes()
    banner = memory("kernel-banner.bin", wanted["linux_banner"], 256).split(b"\0", 1)[0]
    if not banner.startswith(b"Linux version 4."):
        raise ValueError("legacy printk decoder requires the matching Linux 4.x image")
    base = word(wanted["log_buf"], 64)
    length = word(wanted["log_buf_len"], 32)
    first = word(wanted["log_first_idx"], 32)
    next_index = word(wanted["log_next_idx"], 32)
    if base < 0xffff000000000000 or length < 4096 or length > 16 * 1024 * 1024 or length & (length - 1):
        raise ValueError("invalid printk buffer address or size")
    data = memory("kernel-printk-ring.bin", base, length)
    rows = decode_ring(data, first, next_index)
    text = "".join(f"[{row['timestamp_ns'] / 1e9:12.6f}] {row['text']}\n" for row in rows)
    (directory / "kernel-printk.log").write_text(text)
    result = {"scope": "Stopped guest Linux 4.x printk ring; private diagnostic, not an Android boot verdict",
              "symbols_sha256": hashlib.sha256(symbols_path.read_bytes()).hexdigest(),
              "banner": banner.decode(errors="replace"), "buffer_address": hex(base),
              "buffer_bytes": length, "first_index": first, "next_index": next_index,
              "records": len(rows), "ring_sha256": hashlib.sha256(data).hexdigest(),
              "log_sha256": hashlib.sha256(text.encode()).hexdigest()}
    (directory / "kernel-printk-metadata.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
