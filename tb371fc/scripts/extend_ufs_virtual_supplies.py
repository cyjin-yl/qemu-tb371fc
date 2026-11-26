#!/usr/bin/env python3
"""Create an explicitly synthetic UFS supply extension of one pinned partial DB.

This tool executes no firmware, QEMU, guest, or migration operation.
"""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import struct
import sys
from dataclasses import dataclass

PROFILE = "virtual-ufs-supplies-v2"
BASE_SHA256 = "a971809c06fa1a3ff44c9d28463bc6ff53203d9375ffd0f76dc73d4c8a3ea6f9"
DB_SIZE = 131072
HEADER_SIZE = 144
ENTRY_SIZE = 24
NEW_TABLE_OFFSET = 0x2000
BINARY_NAME = PROFILE + ".bin"
BASE_GROUPS = (
    (3, 144, 912, 32, 0x1000),
    (5, 1252, 2140, 37, 0x1000),
    (4, 2436, 2580, 6, 0x0100),
)
CLOCK_NAMES = ("lnbclka1", "vrm.xob", "lnbclka2", "lnbclka3", "rfclka1", "rfclka3")
# Command addresses and limits from the explicit synthetic descriptor table.
# They are not physical PMIC addresses or measurements of live command-db RAM.
ADDITIONS = (
    ("smpa4", 0x4e080, 2040, 0xd8, "direct vccq2"),
    ("ldoa6", 0x4e140, 1304, 0x98, "direct vccq and ref clock"),
    ("ldoa17", 0x4e1f0, 3544, 0x98, "direct vcc"),
    ("smpc8", 0x4e280, 2040, 0xd8, "explicit vccq-parent"),
    ("ldoa5", 0x4e130, 1304, 0x98, "PHY vdda-phy"),
    ("ldoa9", 0x4e170, 1304, 0x98, "PHY vdda-pll"),
)
# Consumer dependencies are a separate contract from the generation inventory.
# The two PHY edges were absent from v1 even though its four digital votes passed.
DEPENDENCIES = {
    "ufs-controller": {"vcc": "ldoa17", "vccq": "ldoa6",
                       "vccq2": "smpa4", "qcom,vccq-parent": "smpc8"},
    "ufs-phy": {"vdda-phy": "ldoa5", "vdda-pll": "ldoa9"},
}
OPERATING_MV = {"smpa4": 1800, "ldoa6": 1200, "ldoa17": 2960,
                "smpc8": 1200, "ldoa5": 880, "ldoa9": 1200}
DESCRIPTOR_SHA256 = "6f69acb80c1032e29a581197d9a41f1a9ac67fa9d464f0e7bb55410b26c54542"
DEFAULT_DESCRIPTOR = Path(__file__).resolve().parents[2] / "hw/misc/qcom-sm8250-rpmh-data.inc"

SOURCE_PINS = {
    "qemu_descriptor_commit": "a5e57aeffe6679669de3b5b5eeafc3fe3facd9a1",
    "v1_generator_sha256": "e6fd818a78732dc36d9dd5e1fb1f40341066255834d722751da328542dac4487",
    "phy_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
    "phy_vendor_reference_commit": "98719830b0de5584f5588bbe9f4c8304d97a64c6",
    "rpmh_data_sha256": "6f69acb80c1032e29a581197d9a41f1a9ac67fa9d464f0e7bb55410b26c54542",
    "vendor_reference_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
    "vendor_rpmh_regulator_git_blob": "e6667c59818621a99bc53fc560e353d8c25e14e5",
    "vendor_ufs_qcom_git_blob": "63918b0cb642fce4c9b01b42c325aa72fde068e8",
}


class InvalidDB(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise InvalidDB(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


@dataclass(frozen=True)
class Group:
    index: int
    slave: int
    start: int
    aux: int
    count: int
    version: int

    def layout(self):
        return (self.slave, self.start, self.aux, self.count, self.version)


@dataclass(frozen=True)
class Record:
    slave: int
    version: int
    name: str
    priority: tuple
    address: int
    aux: bytes
    raw: bytes
    offset: int

    def meaning(self):
        return (self.slave, self.version, self.name, self.priority,
                self.address, self.aux)

    def address_range(self):
        return (self.address, self.address + (16 if self.slave == 4 else 4))


@dataclass(frozen=True)
class Database:
    groups: tuple
    records: tuple
    used_ranges: tuple


def parse_db(data):
    """Parse all active groups, records and auxiliary bytes without executing them."""
    require(isinstance(data, bytes), "DB must be bytes")
    require(HEADER_SIZE <= len(data) <= DB_SIZE, "DB size out of bounds")
    require(struct.unpack_from("<II", data) == (1, 0x0c0330db),
            "unsupported version or magic")
    require(data[136:HEADER_SIZE] == bytes(8), "unknown global reserved fields")
    groups, records, header_ranges, aux_ranges = [], [], [(0, HEADER_SIZE)], []
    names, slaves, ended = set(), set(), False
    for index in range(8):
        values = struct.unpack_from("<8H", data, 8 + 16 * index)
        slave, start, aux, count, version, *reserved = values
        if not slave:
            require(not any(values), "nonzero unused group")
            ended = True
            continue
        require(not ended, "active group after unused group")
        require(slave in (3, 4, 5) and slave not in slaves,
                "unknown or duplicate slave group")
        require(not any(reserved), "unknown group reserved fields")
        start += HEADER_SIZE
        aux += HEADER_SIZE
        end = start + count * ENTRY_SIZE
        require(count > 0 and start >= HEADER_SIZE and end <= len(data),
                "record table out of bounds")
        require(HEADER_SIZE <= aux <= len(data), "auxiliary base out of bounds")
        group = Group(index, slave, start, aux, count, version)
        groups.append(group)
        slaves.add(slave)
        header_ranges.append((start, end))
        for j in range(count):
            offset = start + j * ENTRY_SIZE
            raw = data[offset:offset + ENTRY_SIZE]
            name_raw, p0, p1, address, length, relative_aux = struct.unpack("<8sIIIHH", raw)
            split = name_raw.find(b"\0")
            if split < 0:
                split = 8
            require(split > 0 and name_raw[split:] == bytes(8 - split),
                    "empty or noncanonical name")
            require(all(0x21 <= v <= 0x7e for v in name_raw[:split]), "non-ASCII resource name")
            name = name_raw[:split].decode("ascii")
            require(name not in names, "duplicate resource name")
            names.add(name)
            span = 16 if slave == 4 else 4
            require(not address or ((address >> 16) & 15) == slave,
                    "resource address has wrong slave")
            require(address % span == 0, "unaligned resource address")
            require(address or length > 0, "zero address without auxiliary data")
            aux_start = aux + relative_aux
            aux_end = aux_start + length
            require(aux_start <= len(data) and aux_end <= len(data),
                    "auxiliary record out of bounds")
            if length:
                aux_ranges.append((aux_start, aux_end))
            records.append(Record(slave, version, name, (p0, p1), address,
                                  data[aux_start:aux_end], raw, offset))
    require(groups, "no groups")
    for i, current in enumerate(header_ranges):
        require(all(not overlaps(current, other) for other in header_ranges[:i]),
                "overlapping record tables")
    require(all(not overlaps(aux, header) for aux in aux_ranges for header in header_ranges),
            "auxiliary data overlaps a record table")
    # The pinned input contains auxiliary-only .mol rows at address zero and
    # the one pre-existing lnbclka1/vrm.xob alias. Neither creates new state.
    address_records = [r for r in records if r.address]
    for i, current in enumerate(address_records):
        for other in address_records[:i]:
            if overlaps(current.address_range(), other.address_range()):
                require({current.name, other.name} == {"lnbclka1", "vrm.xob"}
                        and current.address == other.address
                        and current.slave == other.slave == 4
                        and current.aux == other.aux,
                        "conflicting resource address ranges")
    return Database(tuple(groups), tuple(records), tuple(header_ranges + aux_ranges))


def validate_descriptor_contract(descriptor_bytes):
    """Bind the six selected digital resources to the existing QEMU catalogue."""
    require(isinstance(descriptor_bytes, bytes), "descriptor must be bytes")
    require(sha256(descriptor_bytes) == DESCRIPTOR_SHA256,
            "QEMU descriptor SHA256 mismatch")
    required = {resource for supplies in DEPENDENCIES.values()
                for resource in supplies.values()}
    require(len(ADDITIONS) == 6 and {row[0] for row in ADDITIONS} == required
            and set(OPERATING_MV) == required,
            "six UFS controller and PHY dependency resources are required")
    pattern = re.compile(
        r'\{\s*"([^"]+)"\s*,\s*(RPMH_[A-Z_]+)\s*,\s*(0x[0-9a-fA-F]+)\s*,'
        r'\s*(\d+)\s*,\s*(0x[0-9a-fA-F]+)\s*,')
    descriptors = {}
    for name, kind, address, maximum, modes in pattern.findall(descriptor_bytes.decode("ascii")):
        require(name not in descriptors, "duplicate descriptor resource")
        descriptors[name] = (kind, int(address, 16), int(maximum), int(modes, 16))
    for name, address, maximum, modes, _role in ADDITIONS:
        require(address != 0 and ((address >> 16) & 7) == 4 and address % 16 == 0,
                "new command address must encode VRM slave 4")
        require(descriptors.get(name) ==
                ("RPMH_VRM_REGULATOR", address, maximum, modes),
                "supply differs from existing QEMU descriptor: " + name)
        require(0 < OPERATING_MV[name] <= maximum,
                "selected digital voltage exceeds descriptor: " + name)
    return required


def extend_bytes(data, *, descriptor_bytes, expected_sha256=BASE_SHA256):
    """Pure transform. A fixture hash may be supplied by metadata-only unit tests.

    The command line exposes no hash override and always uses BASE_SHA256.
    """
    require(sha256(data) == expected_sha256, "input SHA256 mismatch")
    require(len(data) == DB_SIZE, "input must be the 128KiB partial DB")
    validate_descriptor_contract(descriptor_bytes)
    before = parse_db(data)
    require(tuple(g.layout() for g in before.groups) == BASE_GROUPS,
            "unknown input group layout")
    require(len(before.records) == 75, "expected 75 original records")
    vrm = before.groups[2]
    old_vrm = tuple(r for r in before.records if r.slave == 4)
    require(tuple(r.name for r in old_vrm) == CLOCK_NAMES, "unexpected original VRM inventory")
    old_names = {r.name for r in before.records}
    require(not old_names.intersection(x[0] for x in ADDITIONS), "target resource already exists")
    for name, address, _mv, _mask, _role in ADDITIONS:
        interval = (address, address + 16)
        require(all(not overlaps(interval, r.address_range())
                    for r in before.records if r.address),
                "synthetic resource collides with an original address")
    added_addresses = [r[1] for r in ADDITIONS]
    require(len(set(added_addresses)) == len(ADDITIONS), "duplicate synthetic address")
    new_count = vrm.count + len(ADDITIONS)
    replacement_range = (NEW_TABLE_OFFSET, NEW_TABLE_OFFSET + new_count * ENTRY_SIZE)
    require(0 <= NEW_TABLE_OFFSET - HEADER_SIZE <= 0xffff
            and replacement_range[1] <= len(data), "new table is not format-reachable")
    require(all(not overlaps(replacement_range, used) for used in before.used_ranges),
            "new table overlaps declared input data")
    require(data[replacement_range[0]:replacement_range[1]] ==
            bytes(replacement_range[1] - replacement_range[0]),
            "new table would overwrite nonzero bytes")
    result = bytearray(data)
    table = bytearray(data[vrm.start:vrm.start + vrm.count * ENTRY_SIZE])
    for name, address, _mv, _mask, _role in ADDITIONS:
        # VRM uses no ARC level-map auxiliary payload. Synthetic built-in
        # reference records use this zero-length form as well.
        table.extend(struct.pack("<8sIIIHH", name.encode("ascii"), 0, 0, address, 0, 0))
    result[replacement_range[0]:replacement_range[1]] = table
    group_offset = 8 + 16 * vrm.index
    struct.pack_into("<H", result, group_offset + 2, NEW_TABLE_OFFSET - HEADER_SIZE)
    struct.pack_into("<H", result, group_offset + 6, new_count)
    result = bytes(result)
    after = parse_db(result)
    require(len(after.groups) == 3 and len(after.records) == 81, "output inventory differs")
    require(tuple(r.meaning() for r in after.records[:75]) ==
            tuple(r.meaning() for r in before.records), "original record meaning changed")
    require(tuple(r.raw for r in after.records[:75]) ==
            tuple(r.raw for r in before.records), "original record header bytes changed")
    expected_new = tuple((4, vrm.version, name, (0, 0), address, b"")
                         for name, address, *_ in ADDITIONS)
    require(tuple(r.meaning() for r in after.records[75:]) == expected_new,
            "unexpected appended records")
    changed_ranges = ((group_offset + 2, group_offset + 4),
                      (group_offset + 6, group_offset + 8), replacement_range)
    cursor = 0
    for start, end in changed_ranges:
        require(data[cursor:start] == result[cursor:start],
                "bytes outside the approved changes differ")
        cursor = end
    require(data[cursor:] == result[cursor:], "unapproved trailing changes")
    manifest = {
        "schema": "tb371fc-virtual-ufs-supplies-v2",
        "profile": PROFILE,
        "input": {"sha256": expected_sha256, "bytes": len(data), "records": 75},
        "output": {"filename": BINARY_NAME, "sha256": sha256(result),
                   "bytes": len(result), "records": 81},
        "sources": SOURCE_PINS,
        "dependencies": DEPENDENCIES,
        "descriptor_sha256": sha256(descriptor_bytes),
        "synthetic_additions": [
            {"name": name, "command_address": hex(address), "role": role,
             "existing_model_max_request_mv": mv, "existing_model_mode_mask": hex(mask),
             "selected_operating_mv": OPERATING_MV[name], "auxiliary_bytes": 0}
            for name, address, mv, mask, role in ADDITIONS
        ],
        "preservation": {
            "original_75_record_headers_and_auxiliary_bytes_equal": True,
            "original_payload_bytes_remain_in_place": True,
            "unchanged_bytes_outside_reported_ranges": True,
            "single_slave4_group": True,
            "new_table_preimage_all_zero": True,
        },
        "changes": {
            "group_index": vrm.index, "old_vrm_count": vrm.count, "new_vrm_count": new_count,
            "old_vrm_table_offset": vrm.start, "new_vrm_table_offset": NEW_TABLE_OFFSET,
            "vrm_aux_base_unchanged": vrm.aux,
            "allowed_file_ranges": [list(r) for r in changed_ranges],
        },
        "limits": {
            "complete_pmic_database": False, "physical_addresses_verified": False,
            "aop_executed": False, "guest_or_android_boot_verified": False,
            "linux_driver_probe_tested": False, "ufs_link_or_luns_tested": False,
            "migration_qualified": False,
            "meaning": "Explicit virtual digital RPMh requests; no analog supply or live AOP claim.",
        },
    }
    return result, manifest


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def canonical_input(path):
    path = Path(path)
    require(path.is_absolute() and path == path.resolve(strict=True),
            "input must be an absolute canonical path without symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        first = os.fstat(fd)
        require(stat.S_ISREG(first.st_mode), "input must be a regular file")
        require(first.st_size == DB_SIZE, "input must be 128KiB")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(DB_SIZE + 1)
        require(identity(first) == identity(os.fstat(fd)), "input changed while reading")
    finally:
        os.close(fd)
    require(identity(first) == identity(os.stat(path, follow_symlinks=False)),
            "input path changed while reading")
    return path, first, data


def write_extension(input_path, output_directory, *,
                    descriptor_path=DEFAULT_DESCRIPTOR, expected_sha256=BASE_SHA256):
    """Complete all metadata checks before creating the exclusive output directory."""
    source, source_stat, data = canonical_input(input_path)
    descriptor_path = Path(descriptor_path)
    require(descriptor_path.is_absolute()
            and descriptor_path == descriptor_path.resolve(strict=True)
            and descriptor_path.is_file() and not descriptor_path.is_symlink(),
            "descriptor must be a canonical regular file")
    descriptor_stat = descriptor_path.stat()
    descriptor_bytes = descriptor_path.read_bytes()
    require(identity(descriptor_stat) == identity(descriptor_path.stat()),
            "descriptor changed while reading")
    result, manifest = extend_bytes(data, descriptor_bytes=descriptor_bytes,
                                    expected_sha256=expected_sha256)
    destination = Path(output_directory)
    require(destination.is_absolute()
            and destination.parent == destination.parent.resolve(strict=True),
            "output parent must be an existing canonical directory")
    require(not destination.exists() and not destination.is_symlink(),
            "output directory already exists")
    require(identity(source_stat) == identity(os.stat(source, follow_symlinks=False)),
            "input changed before output creation")
    manifest["input"]["path"] = str(source)
    manifest["input"]["opened_read_only"] = True
    manifest["tool_sha256"] = sha256(Path(__file__).read_bytes())
    manifest["descriptor_input"] = {
        "path": str(descriptor_path), "bytes": len(descriptor_bytes),
        "sha256": sha256(descriptor_bytes), "opened_read_only": True}
    require(identity(descriptor_stat) == identity(descriptor_path.stat()),
            "descriptor changed before output creation")
    encoded = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    # mkdir and xb writes prevent clobbering on races too. An I/O failure after
    # mkdir leaves only this new directory for inspection; nothing is retried.
    destination.mkdir(mode=0o700)
    for name, content in ((BINARY_NAME, result), ("manifest.json", encoded)):
        with (destination / name).open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    require(identity(source_stat) == identity(os.stat(source, follow_symlinks=False)),
            "input changed during output publication")
    require(identity(descriptor_stat) == identity(descriptor_path.stat()),
            "descriptor changed during output publication")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    parser.add_argument("--descriptor-file", type=Path, default=DEFAULT_DESCRIPTOR,
                        help="existing QEMU resource catalogue; must match the fixed descriptor pin")
    args = parser.parse_args(argv)
    try:
        manifest = write_extension(args.input, args.output_directory,
                                   descriptor_path=args.descriptor_file)
    except (InvalidDB, OSError) as exc:
        print(json.dumps({"success": False, "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"success": True, "profile": PROFILE, "output": manifest["output"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
