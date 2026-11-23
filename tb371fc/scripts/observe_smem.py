#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read a stopped, exact production kernel's SMEM probe publication.

This observer never writes guest memory. Its private-object layout is limited
to the image whose probe was disassembled during the TB371FC bring-up. Shared
memory fixtures alone cannot satisfy this observation.
"""
import hashlib
import re
from pathlib import Path


KERNEL_SHA256 = "ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f"
SYMBOLS = {
    "_text": 0xffffff8008080000,
    "qcom_smem_probe": 0xffffff8008676af0,
    "__smem": 0xffffff800a4cab30,
}


def capture_smem_probe(command, symbols_path, kernel_path):
    """Return a bounded diagnostic; successful probe is not a boot verdict."""
    report = {
        "schema_version": 1,
        "scope": "Stopped guest SMEM driver publication and reference layout",
        "success_path_observed": False,
        "native_sbl_captured": False,
        "android_boot_passed": False,
        "guest_memory_written": False,
        "virtual_memory_cpu_index": 0,
        "observations": [],
        "checks": [],
    }
    try:
        image = Path(kernel_path).read_bytes()
        report["kernel_sha256"] = hashlib.sha256(image).hexdigest()
        if report["kernel_sha256"] != KERNEL_SHA256:
            raise ValueError("Private driver layout is unverified for this kernel")
        raw_symbols = Path(symbols_path).read_bytes()
        report["symbols_sha256"] = hashlib.sha256(raw_symbols).hexdigest()
        found = {}
        for line in raw_symbols.decode().splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[2] in SYMBOLS:
                if fields[2] in found:
                    raise ValueError("Ambiguous driver symbol: " + fields[2])
                found[fields[2]] = int(fields[0], 16)
        if found != SYMBOLS:
            raise ValueError("Driver symbols differ from the disassembled image")

        # Verified production success tail: materialize __smem's page, select
        # return value zero, publish x19, then branch to the common return.
        offset = 0xffffff800867714c - SYMBOLS["_text"]
        success_tail = bytes.fromhex("88f200f0 f4031f2a 139905f9 b5ffff17")
        if image[offset:offset + len(success_tail)] != success_tail:
            raise ValueError("Production SMEM success-tail instructions differ")
        report["publication_instructions_verified"] = True
        del image

        status = command("query-status")
        report["stopped_status"] = status
        if status.get("running") is not False:
            raise ValueError("Guest must be stopped before reading driver state")
        for path, prop in (("/machine/smem-handoff", "reference-empty"),
                           ("/machine/tcsr-mutex", "boot-unlocked-reference")):
            if command("qom-get", {"path": path, "property": prop}) is not True:
                raise ValueError("Observer requires explicit SMEM and mutex references")

        def read(address, width, field):
            if width not in (4, 8) or address % width:
                raise ValueError("Unaligned observer field: " + field)
            size = "g" if width == 8 else "w"
            answer = command("human-monitor-command", {
                "command-line": f"x /1{size}x 0x{address:x}", "cpu-index": 0})
            match = re.fullmatch(r"\s*([0-9a-fA-F]+):\s*(0x[0-9a-fA-F]+)\s*",
                                 answer)
            if not match or int(match.group(1), 16) != address:
                raise ValueError("Cannot decode guest virtual field: " + field)
            value = int(match.group(2), 16)
            if value >= 1 << (width * 8):
                raise ValueError("Out-of-width field: " + field)
            report["observations"].append({
                "field": field, "virtual_address": hex(address),
                "width": width, "value": hex(value)})
            return value

        def require(name, condition):
            report["checks"].append({"name": name, "passed": bool(condition)})
            if not condition:
                raise ValueError("Driver object check failed: " + name)

        def pointer(address, field):
            value = read(address, 8, field)
            require(field + "_kernel_pointer",
                    value >> 48 == 0xffff and value % 8 == 0)
            return value

        smem = read(SYMBOLS["__smem"], 8, "__smem")
        if smem == 0:
            report["verdict"] = "success-path-not-observed"
            return report
        require("published_driver_pointer", smem >> 48 == 0xffff and smem % 8 == 0)
        pointer(smem, "dev")
        pointer(smem + 8, "hwlock")
        require("item_count", read(smem + 0x10, 4, "item_count") == 512)
        ptable = pointer(smem + 0x18, "ptable_base")
        partition = pointer(smem + 0x20, "global_virtual_base")
        require("global_physical_base",
                read(smem + 0x28, 4, "global_physical_base") == 0x80903000)
        require("global_cacheline", read(smem + 0x2c, 4, "global_cacheline") == 64)
        size = read(smem + 0x30, 4, "global_size")
        require("global_size", size == 0x1fc000)
        require("region_count", read(smem + 0x140, 4, "region_count") in (1, 2))
        require("region_physical_base",
                read(smem + 0x148, 4, "region_physical_base") == 0x80900000)
        header = pointer(smem + 0x150, "region_virtual_base")
        require("region_size", read(smem + 0x158, 8, "region_size") == 0x200000)
        for offset, name, expected in ((0, "ptable_magic", 0x434f5424),
                                       (4, "ptable_version", 1),
                                       (8, "ptable_entries", 1)):
            require(name, read(ptable + offset, 4, name) == expected)
        for offset, name, expected in ((0, "partition_magic", 0x54525024),
                                       (4, "partition_hosts", 0xfffefffe),
                                       (8, "partition_size", size)):
            require(name, read(partition + offset, 4, name) == expected)
        uncached = read(partition + 12, 4, "uncached_frontier")
        cached = read(partition + 16, 4, "cached_frontier")
        require("allocation_frontiers", 32 <= uncached <= cached <= size)
        for offset, name, expected in ((0x5c, "header_version", 12 << 16),
                                       (0xc0, "header_initialized", 1),
                                       (0xcc, "header_reserved", 0)):
            require(name, read(header + offset, 4, name) == expected)
        report["success_path_observed"] = True
        report["verdict"] = "production-driver-success-path-observed"
    except Exception as exc:
        report["verdict"] = "observation-inconclusive"
        report["error"] = type(exc).__name__ + ": " + str(exc)
    return report
