#!/usr/bin/env python3
"""Independent exact-head RPM statistics offset actual-ZIP audit.

Never extracts or executes archive members, QEMU, producer tests or generators.
The five-field 24-byte wire is reconstructed from explicit byte offsets. Generic
strict ZIP/JSON/protocol parsers come from the reviewed SPSS auditor; all RPM
state, ordinary-access and migration oracles are independently stated here.
Separate QMP/QTest files do not provide a shared timestamp: per-file order is
combined with exact producer source pins, counter receipts and a real RAM witness.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import traceback
from zipfile import ZipFile

if not __debug__:
    raise SystemExit("Independent audit requires assertions enabled; do not use Python -O.")

CHUNK, MAX_SMALL = 1024 * 1024, 64 * 1024 * 1024
HEAD = "7d01e5e294b788f97a8120c2d34122ddb25e6343"
TREE = "5c25fff35bb41c3a8b018f0be965b4a9d3e9d5c4"
FAILED_HEAD = "75cc1dc83600784afa06e81c9998d8d39aba6af2"
TYPE, DEVICE = "qcom-sm8250-rpm-stats-offset", "/machine/rpm-stats-offset"
BASE, RAM, IDENTITY, OFFSET_VALUE = 0x0c3f0004, 0x80010000, 0x52534f31, 0x000f0048
PAYLOAD, GETTERS = 24, 17
PRIMARY = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
UPSTREAM = "adc218676eef25575469234709c2d87185ca223a"
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
CASE = "rpm-stats-offset-sm8250-reference"
GENERATION_CASE = "rpm-stats-offset-source-generation"
TEST_SHA = "8e878e30ad978d7affb389573e4df4cc3bb3009843ac74b5d363ebd723eee39f"
MODEL_SHA = "5144319fb0856c107512edbdfe86d48adef1237846da7c312394ff8ccae5db09"
FIELDS = (
    "enabled-profile", "reference-identity", "physical-base", "window-bytes",
    "offset-value", "vmstate-bytes", "register-count", "irq-output-count",
    "clock-input-count", "clock-output-count", "timer-count", "offset-known",
    "stats-payload-modeled", "ddr-offset-modeled", "firmware-modeled",
    "ready-modeled", "enabled-reference",
)
ORDINARY = (
    "cold_identity_and_pure_getters", "enabled_reads_return_only_layout_offset",
    "all_aligned_writes_are_readonly", "narrow_unaligned_and_u64_reads_are_rejected",
    "invalid_write_shapes_and_buffered_write_never_change_layout",
    "reset_retains_enabled_layout_without_firmware_data",
    "exact_single_leaf_and_unmodeled_neighbors",
    "runtime_observers_are_readonly_and_time_has_no_effect",
)
SCENARIOS = ("enabled", "disabled")
MUTATIONS = (
    ("profile", "enabled", 0, 4, 1, 2, True, "migrated profile mismatch"),
    ("identity", "enabled", 4, 4, IDENTITY, 1, True, "migrated identity mismatch"),
    ("physical_base", "enabled", 8, 8, BASE, 4, True, "migrated geometry mismatch"),
    ("window_bytes", "enabled", 16, 4, 4, 1, True, "migrated geometry mismatch"),
    ("enabled_offset", "enabled", 20, 4, OFFSET_VALUE, 1, True, "migrated offset mismatch"),
    ("disabled_offset", "disabled", 20, 4, 0, 1, False, "migrated offset mismatch"),
)
LENGTHS = {"checks": 20, "migrations": 2, "rejected_migrations": 2,
           "corrupted_stream_rejections": 6, "migration_sources": 2, "actual_stream_files": 8}
SOURCE_PINS = {
    "hw/misc/qcom-sm8250-rpm-stats-offset.c": (9377, MODEL_SHA),
    "include/hw/misc/qcom-sm8250-rpm-stats-offset.h": (364, "946df3bee216839b46001d4ea3065bda6563413a0c753a7b221fffea3b77ad0b"),
    "hw/misc/qcom-sm8250-rpm-stats-offset-data.inc": (346, "dc38191138f43d396b94971b15b9b6617924131669b602cb1bccf185f75d22e5"),
    "hw/arm/lenovo-tb371fc.c": (103334, "51ee3224180a2e927572ee56f011b3d222f68fb97fef7e75ad188f972abaeaf1"),
    "hw/arm/Kconfig": (15508, "6cbe01a64aeea18706c4d255276819a6addf66bf6d4636381b75fbf12a6984af"),
    "hw/misc/Kconfig": (4239, "451eb84dce84cb9dde20e65220a39314b5acb8e36885efd172d878549b10fa5d"),
    "hw/misc/meson.build": (10935, "ca26e093985792bb0ece8c598cc83a384c455b8b693e950d83678327946f121a"),
    "tb371fc/docs/rpm-stats-offset-sm8250-registers.json": (4870, "df0c4dad7572a918f579e98174663496893039d27315db6427726de9641a6adb"),
    "tb371fc/tests/test_rpm_stats_offset_sm8250.py": (39266, TEST_SHA),
    "tb371fc/scripts/gen_rpm_stats_offset.py": (19192, "47f77997d2708da12235cde157b25228ef75b93bb689a92f1338769dcc99537c"),
    "tb371fc/scripts/verify_rpm_stats_offset_generation.py": (4965, "2673e6cc5457db0a1e281013c14d30941e7251bf7f40f2a4bf2265f40831630c"),
    "tb371fc/scripts/source_download.py": (4642, "4fde0300dc4c8777531402dc700fbed6364f16b0550fc852c3031fe641fe7dd7"),
    "tb371fc/scripts/ci_device_tests.py": (81555, "c1478cab27f3937d2935571a0727233ae23bb392380762db10c76cda978d4a90"),
    "tb371fc/scripts/test_ci_device_tests.py": (52408, "9945c4a3fb45cfabf08fc27271243be8a72487d872aecfc56c9332c61b36f761"),
    "tb371fc/evidence/rpm-stats-offset-20261125-6a5/test-ci-contract.json": (3391, "2d137f7cd52b52c281414fd2508a04fde7c5ae567a123f6c3a2c7a31128e032a"),
}
PUBLIC_INPUTS = (
    ("primary-r11", "OnePlusOSS/android_kernel_oneplus_sm8250", PRIMARY,
     "drivers/soc/qcom/rpm_stats.c", 11328,
     "3c493aed9975865203e5e3e2b2cbd95b8357bb47",
     "cb733f4433d6432187dd7faa31d8eff81e1b115ee20691eb53a3b1ca0eba0750"),
    ("primary-r11", "OnePlusOSS/android_kernel_oneplus_sm8250", PRIMARY,
     "arch/arm64/boot/dts/vendor/qcom/kona-pm.dtsi", 2817,
     "a5eba34468ee25209d8f07baf5efe9c53462ec2a",
     "c1f3a89f461cf1381335ffb2e3066026c28a27642389f9f92fccf540646e5231"),
    ("primary-upstream", "torvalds/linux", UPSTREAM,
     "drivers/soc/qcom/qcom_stats.c", 7641,
     "c429d5154aaec2c3115ccefd1c23d712bf6817f7",
     "b62608854c3dbdef9b43d69a0e5b51b577624c583a80da6eeb5cd176752dd6c5"),
    ("primary-upstream", "torvalds/linux", UPSTREAM,
     "arch/arm64/boot/dts/qcom/sm8250.dtsi", 176883,
     "630f4eff20bf810e347685066dce74ab92a23b56",
     "8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499"),
)
OUTPUTS = ("hw/misc/qcom-sm8250-rpm-stats-offset-data.inc",
           "tb371fc/docs/rpm-stats-offset-sm8250-registers.json")


class AuditFailure(RuntimeError):
    pass


def need(ok, message):
    if not ok:
        raise AuditFailure(message)


def same(actual, expected, where):
    """Type-sensitive comparison; bool is never accepted as an integer."""
    need(type(actual) is type(expected), f"{where}: type {type(actual).__name__} != {type(expected).__name__}")
    if type(expected) is dict:
        need(set(actual) == set(expected), f"{where}: object keys differ")
        for key in expected:
            same(actual[key], expected[key], where + "." + str(key))
    elif type(expected) in (tuple, list):
        need(len(actual) == len(expected), f"{where}: length {len(actual)} != {len(expected)}")
        for i, value in enumerate(expected):
            same(actual[i], value, f"{where}[{i}]")
    else:
        need(actual == expected, f"{where}: {str(actual)[:160]} != {str(expected)[:160]}")


def uint(value, bits, where, signed=False):
    need(type(value) is int, where + ": integer required")
    low, high = (-(1 << (bits - 1)), 1 << (bits - 1)) if signed else (0, 1 << bits)
    need(low <= value < high, where + ": integer out of range")
    return value


def boolean(value, where):
    need(type(value) is bool, where + ": boolean required")
    return value


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, "Duplicate JSON key " + key)
        result[key] = value
    return result


def json_value(raw, where):
    def bad_constant(value):
        raise AuditFailure(where + ": nonfinite JSON " + value)
    try:
        return json.loads(raw, object_pairs_hook=pairs_unique, parse_constant=bad_constant)
    except (ValueError, UnicodeError) as error:
        raise AuditFailure(where + ": invalid JSON: " + str(error)) from error


def safe_name(name):
    need(type(name) is str and name and "\\" not in name and "\x00" not in name,
         "Invalid archive path")
    p = PurePosixPath(name)
    need(not p.is_absolute() and all(part not in ("", ".", "..") for part in name.split("/")),
         "Unsafe archive path " + name)
    return name


class Store:
    """One ZIP or existing directory; paths are never extracted or executed."""
    def __init__(self, zip_path=None, directory=None):
        self.zip_path = Path(zip_path).resolve() if zip_path else None
        self.root = Path(directory).resolve() if directory else None
        self.z = ZipFile(self.zip_path) if self.zip_path else None
        self.entries, self.observed = {}, {}
        if self.z:
            self.initial = self.zip_path.stat()
            for entry in self.z.infolist():
                name = entry.filename.rstrip("/")
                if not name:
                    continue
                safe_name(name)
                need(name not in self.entries, "Duplicate ZIP member " + name)
                need(not stat.S_ISLNK(entry.external_attr >> 16), "Symlink ZIP member " + name)
                if not entry.is_dir():
                    self.entries[name] = entry
        else:
            need(self.root.is_dir(), "Result directory does not exist")
            for p in self.root.rglob("*"):
                need(not p.is_symlink(), "Symlink in result directory: " + str(p))
                if p.is_file():
                    self.entries[p.relative_to(self.root).as_posix()] = p
        need(len(self.entries) < 200000, "Unreasonable member count")

    def size(self, name):
        safe_name(name)
        need(name in self.entries, "Missing member " + name)
        entry = self.entries[name]
        return entry.file_size if self.z else entry.stat().st_size

    def open(self, name):
        self.size(name)
        return self.z.open(self.entries[name]) if self.z else self.entries[name].open("rb")

    def read(self, name, limit=MAX_SMALL):
        need(self.size(name) <= limit, "Member too large for bounded small-file read: " + name)
        with self.open(name) as f:
            raw = f.read(limit + 1)
        same(len(raw), self.size(name), name + ".actual size")
        self.observed[name] = {"bytes": len(raw), "sha256": sha(raw)}
        return raw

    def text(self, name):
        return self.read(name).decode("utf-8", errors="strict")

    def json(self, name):
        return json_value(self.read(name), name)

    def finish(self):
        if self.z:
            now = self.zip_path.stat()
            same((now.st_dev, now.st_ino, now.st_size, now.st_mtime_ns),
                 (self.initial.st_dev, self.initial.st_ino, self.initial.st_size, self.initial.st_mtime_ns),
                 "ZIP changed during audit")
            self.z.close()


def one_bit_difference(store, original, changed, expected_absolute, expected_xor, source_sha, changed_sha):
    same(store.size(original), store.size(changed), changed + ".whole stream length")
    offset, differences = 0, []
    a_hash, b_hash = hashlib.sha256(), hashlib.sha256()
    with store.open(original) as a, store.open(changed) as b:
        while True:
            left, right = a.read(CHUNK), b.read(CHUNK)
            same(len(left), len(right), changed + ".stream chunk length")
            if not left:
                break
            a_hash.update(left)
            b_hash.update(right)
            if left != right:
                for i, (x, y) in enumerate(zip(left, right)):
                    if x != y:
                        differences.append((offset + i, x, y))
                        need(len(differences) == 1, changed + ": more than one actual changed byte")
            offset += len(left)
    same(a_hash.hexdigest(), source_sha, original + ".unmodified after copying")
    same(b_hash.hexdigest(), changed_sha, changed + ".stable actual mutated bytes")
    same(len(differences), 1, changed + ".actual differing byte count")
    at, before, after = differences[0]
    same(at, expected_absolute, changed + ".actual changed byte offset")
    same(before ^ after, expected_xor, changed + ".actual one-bit xor")
    same((before ^ after).bit_count(), 1, changed + ".actual changed bit count")
    return {"byte_offset": at, "before": before, "after": after, "changed_bits": 1,
            "all_other_stream_bytes_equal": True, "original_stream_sha256_unchanged": True}


def qmp_calls(store, directory):
    path, calls, pending, last_id = directory + "/qmp.jsonl", [], None, 0
    greeting = 0
    for line_number, line in enumerate(store.text(path).splitlines(), 1):
        row = json_value(line, f"{path}:{line_number}")
        need(type(row) is dict and len(row) == 1, path + ": malformed QMP log entry")
        if "send" in row:
            need(pending is None, path + ": overlapping QMP sends")
            request = row["send"]
            need(type(request) is dict and type(request.get("execute")) is str, path + ": malformed QMP command")
            same(request.get("id"), last_id + 1, path + ": sequential request id")
            last_id += 1
            pending = {"request": request, "response": None, "line": line_number}
            calls.append(pending)
        else:
            need("receive" in row and type(row["receive"]) is dict, path + ": malformed QMP receive")
            response = row["receive"]
            if "QMP" in response:
                greeting += 1
                need(not calls, path + ": late QMP greeting")
                continue
            if "event" in response:
                continue
            need(pending is not None, path + ": unsolicited QMP response")
            same(response.get("id"), pending["request"]["id"], path + ": unmatched response id")
            need(("return" in response) != ("error" in response), path + ": malformed QMP verdict")
            pending["response"] = response
            pending = None
    same(greeting, 1, path + ".greeting count")
    # A failed incoming migration can close the socket before its reply.
    if pending is not None:
        same(pending["request"]["execute"], "migrate-incoming", path + ": truncated non-migration QMP request")
    return calls


def operation(call):
    return call["request"]["execute"]


def reply(call):
    response = call["response"]
    need(type(response) is dict and "return" in response and "error" not in response,
         "Missing successful QMP response for " + operation(call))
    return response["return"]


def qom_block(calls, expected, where):
    same(len(calls), len(expected), where + ".getter count")
    for i, (call, (arguments, value)) in enumerate(zip(calls, expected)):
        same(operation(call), "qom-get", where + f".getter {i}")
        same(call["request"].get("arguments"), arguments, where + f".arguments {i}")
        same(reply(call), value, where + f".actual QMP value {i}")


def migration_argument(call, expected):
    args = call["request"].get("arguments")
    need(type(args) is dict and set(args) == {"channels"}, "Migration arguments must use one reviewed file channel")
    channels = args["channels"]
    need(type(channels) is list and len(channels) == 1, "Wrong migration channel count")
    channel = channels[0]
    same(channel.get("channel-type"), "main", "Migration channel type")
    addr = channel.get("addr")
    need(type(addr) is dict and addr.get("transport") == "file", "Migration is not from a local file")
    filename = addr.get("filename")
    need(type(filename) is str and PurePosixPath(filename).name == expected,
         "Actual migration filename differs from reviewed stream")


def qtest_calls(store, directory):
    path, calls, pending = directory + "/qtest.jsonl", [], None
    for line_number, line in enumerate(store.text(path).splitlines(), 1):
        row = json_value(line, f"{path}:{line_number}")
        need(type(row) is dict, path + ": malformed QTest row")
        if "send" in row:
            same(set(row), {"send", "virtual_ns_before"}, path + ": QTest send fields")
            need(pending is None and type(row["send"]) is str, path + ": overlapping QTest sends")
            uint(row["virtual_ns_before"], 64, path + ": virtual time")
            pending = {"command": row["send"], "virtual_ns_before": row["virtual_ns_before"],
                       "reply": None, "line": line_number}
            calls.append(pending)
        else:
            same(set(row), {"receive"}, path + ": QTest receive fields")
            response = row["receive"]
            need(type(response) is str, path + ": non-text QTest response")
            if response.startswith("IRQ "):
                continue
            need(pending is not None and (response == "OK" or response.startswith("OK ")),
                 path + ": QTest failed or unsolicited response")
            pending["reply"] = response
            pending = None
    need(pending is None, path + ": truncated QTest command")
    return calls


def qtest_time(calls, where):
    now = 0
    for item in calls:
        same(item["virtual_ns_before"], now, where + ": local virtual-clock sequence")
        command = item["command"].split()
        need(command, where + ": empty QTest command")
        if command[0] in ("clock_set", "clock_step"):
            need(len(command) == 2, where + ": unbounded clock command")
            value = int(command[1], 0)
            need(value >= 0, where + ": negative virtual time")
            now = value if command[0] == "clock_set" else now + value
            parts = item["reply"].split()
            need(len(parts) == 2, where + ": malformed clock response")
            same(int(parts[1], 0), now, where + ": clock response value")
    return now


def hash_member(store, name):
    digest, size = hashlib.sha256(), 0
    with store.open(name) as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    same(size, store.size(name), name + ".full bytes")
    result = {"bytes": size, "sha256": digest.hexdigest()}
    store.observed[name] = result
    return result


def scan_stream(store, path):
    needle = bytes([len(TYPE)]) + TYPE.encode() + b"\0\0\0\0\0\0\0\1"
    candidates, digest, total, tail, magic = {}, hashlib.sha256(), 0, b"", b""
    with store.open(path) as source:
        for chunk in iter(lambda: source.read(CHUNK), b""):
            if not magic:
                magic = chunk[:8]
            digest.update(chunk)
            block, start = tail + chunk, total - len(tail)
            search_at = 0
            while True:
                at = block.find(needle, search_at)
                if at < 0:
                    break
                search_at = at + 1
                header, payload = at - 5, at + len(needle)
                if header < 0 or start + header < 8 or block[header] not in (1, 4):
                    continue
                absolute = start + header
                previous = candidates.setdefault(absolute, None)
                end = payload + PAYLOAD
                if end + 5 > len(block):
                    continue
                section_id = int.from_bytes(block[header + 1:header + 5], "big")
                footer = b"\x7e" + section_id.to_bytes(4, "big")
                same(block[end:end + 5], footer, path + ".complete footer")
                section = {"name": TYPE, "instance_id": 0, "version": 1,
                           "header_offset": absolute, "section_id": section_id,
                           "payload_offset": start + payload, "payload_bytes": PAYLOAD,
                           "footer_offset": start + end, "footer_hex": footer.hex()}
                item = (section, bytes(block[payload:end]))
                if previous is not None:
                    same(item, previous, path + ".overlap scan")
                candidates[absolute] = item
            total += len(chunk)
            tail = block[-4096:]
    same(magic, b"QEVM\x00\x00\x00\x03", path + ".stream magic/version")
    same(total, store.size(path), path + ".actual whole length")
    same(len(candidates), 1, path + ".unique complete RPM offset section")
    item = next(iter(candidates.values()))
    need(item is not None, path + ": truncated section")
    result = {"bytes": total, "sha256": digest.hexdigest(), "section": item[0], "payload": item[1]}
    store.observed[path] = {"bytes": total, "sha256": result["sha256"]}
    return result


def tokens(call):
    parts = call["command"].split()
    need(parts, "Empty QTest command")
    return (parts[0],) + tuple(int(value, 0) for value in parts[1:])


def is_mmio(call):
    parts = call["command"].split()
    return (parts[0] in ("readb", "readw", "readl", "readq", "writeb", "writew",
                         "writel", "writeq", "read", "write") and
            BASE <= int(parts[1], 0) < BASE + 4)


def command_contract(store, directory, report, profile, incoming=False, accel="qtest", default=False):
    command = store.json(directory + "/command.json")
    need(type(command) is list and all(type(x) is str for x in command), directory + ".argv")
    def option(name):
        same(command.count(name), 1, directory + ".single option " + name)
        at = command.index(name)
        need(at + 1 < len(command), directory + ": missing option value")
        return command[at + 1]
    qmp = option("-qmp")
    need(re.fullmatch(r"unix:/[^,]+/m,server=on,wait=off", qmp) is not None,
         directory + ".local QMP socket")
    expected = [report["qemu"], "-machine", DEFAULT_MACHINE, "-accel", accel,
                "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
                "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem", "-qmp", qmp]
    if not default:
        expected += ["-global", TYPE + ".enabled-reference=" + ("on" if profile else "off")]
    if accel == "qtest":
        qt = option("-qtest")
        same(qt, qmp.replace("/m,", "/t,"), directory + ".paired local QTest socket")
        expected += ["-qtest", qt, "-qtest-log", "/dev/null"]
    if incoming:
        expected += ["-incoming", "defer"]
    same(command, expected, directory + ".complete reviewed invocation")
    return store.text(directory + "/qemu.stderr")


def qmp_sequence(calls, sequence, where):
    """Each world requires 17 adjacent actual getter requests and responses."""
    pos = 0
    worlds = []
    for expected in sequence:
        if type(expected) is dict:
            qom_block(calls[pos:pos + GETTERS], queries(expected), where + f".world {len(worlds)}")
            worlds.append({"first_request_id": calls[pos]["request"]["id"], "snapshot": expected})
            pos += GETTERS
            continue
        need(pos < len(calls), where + ": missing QMP command")
        call = calls[pos]
        if type(expected) is str:
            same(operation(call), expected, where + f".operation {pos}")
            need("arguments" not in call["request"], where + ".unexpected QMP arguments")
            reply(call)
        elif expected[0] == "set-error":
            same(operation(call), "qom-set", where + ".readonly property write attempt")
            same(call["request"].get("arguments"),
                 {"path": DEVICE, "property": expected[1], "value": expected[2]}, where + ".qom-set arguments")
            need(call["response"] is not None and "error" in call["response"], where + ".QOM write rejected")
        elif expected[0] == "list":
            same(operation(call), "qom-list", where + ".actual property inventory")
            same(call["request"].get("arguments"), {"path": DEVICE}, where + ".list path")
            properties = reply(call)
            need(type(properties) is list, where + ".property array")
            names = [p["name"] for p in properties]
            same(len(names), len(set(names)), where + ".unique property names")
            need(not any(name.startswith("qtest-") for name in names), where + ": fixture override exposed")
            types = {p["name"]: p["type"] for p in properties}
            for name in FIELDS:
                same(types.get(name), "uint64" if name in FIELDS[:11] else "bool", where + ".type " + name)
        elif expected[0] == "hmp":
            same(operation(call), "human-monitor-command", where + ".mtree command")
            same(call["request"].get("arguments"), {"command-line": "info mtree -f"}, where + ".mtree arguments")
            same(reply(call), expected[1], where + ".actual mtree response")
        else:
            raise AuditFailure(where + ": unsupported auditor QMP expectation")
        pos += 1
    same(pos, len(calls), where + ".complete QMP sequence consumed")
    return worlds


def device_rejections(stderr):
    pattern = re.escape(TYPE) + r": rejected (read|write) offset=0x([0-9a-f]+) size ([0-9]+) \(([^)]+)\)"
    found = re.findall(pattern, stderr)
    same(len(found), stderr.count(TYPE + ": rejected"), "No malformed/unaccounted RPM offset diagnostic")
    return [(op, int(offset, 16), int(size), reason) for op, offset, size, reason in found]


def core_rejections(stderr):
    found = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: ([^\r\n]+)", stderr)
    same(len(found), stderr.count("Invalid read at addr ") + stderr.count("Invalid write at addr "),
         "No malformed/unaccounted generic invalid-memory diagnostic")
    return [(op, int(address, 16), int(size), region, reason) for op, address, size, region, reason in found]


def fields_equal(actual, expected, where):
    need(type(actual) is dict, where + ": object required")
    for key, value in expected.items():
        need(key in actual, where + ": missing " + key)
        same(actual[key], value, where + "." + key)


def completed_polls(calls, saved_reply, where):
    need(calls, where + ": no migration status response")
    for index, call in enumerate(calls):
        same(operation(call), "query-migrate", where + ".status polling only")
        need("arguments" not in call["request"], where + ".status arguments")
        status = reply(call)
        need(type(status) is dict and type(status.get("status")) is str, where + ".actual status")
        need(status["status"] not in ("failed", "cancelled"), where + ".failed migration")
        same(status["status"] == "completed", index == len(calls) - 1,
             where + ".first completed poll terminates polling")
    same(reply(calls[-1]), saved_reply, where + ".saved versus actual completion response")



def world(profile=True):
    boolean(profile, "profile")
    values = (int(profile), IDENTITY, BASE, 4, OFFSET_VALUE if profile else 0,
              PAYLOAD, 1, 0, 0, 0, 0, profile, False, False, False, False, profile)
    return dict(zip(FIELDS, values))


def scenario_world(name):
    need(name in SCENARIOS, "Unknown RPM scenario")
    return world(name == "enabled")


def check_world(value, where):
    need(type(value) is dict, where + ": object required")
    same(set(value), set(FIELDS), where + ".exact 17 QOM fields")
    for key in FIELDS[:11]:
        uint(value[key], 64, where + "." + key)
    for key in FIELDS[11:]:
        boolean(value[key], where + "." + key)
    same(value, world(value["enabled-reference"]), where + ".independent immutable invariants")


def wire(value):
    """Independent complete explicit byte map, never producer struct.pack."""
    check_world(value, "wire input")
    fields = ((0, 4, value["enabled-profile"]), (4, 4, IDENTITY),
              (8, 8, BASE), (16, 4, 4), (20, 4, value["offset-value"]))
    result, covered = bytearray(PAYLOAD), set()
    for offset, width, number in fields:
        span = set(range(offset, offset + width))
        need(not covered.intersection(span), "Overlapping independent wire fields")
        covered.update(span)
        result[offset:offset + width] = number.to_bytes(width, "big")
    same(covered, set(range(PAYLOAD)), "All 24 bytes independently covered")
    return bytes(result)


def queries(value):
    check_world(value, "QOM expectation")
    return [({"path": DEVICE, "property": name}, value[name]) for name in FIELDS]


def is_memory(call):
    return call["command"].split()[0] in (
        "readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq", "read", "write")


def is_ram(call):
    return is_memory(call) and RAM <= tokens(call)[1] < RAM + 4


def core_rows(rows):
    return [list(row) for row in rows]


def expected_rejection(command):
    op = "write" if command[0].startswith("write") else "read"
    width = {"b": 1, "w": 2, "l": 4, "q": 8}[command[0][-1]]
    reason = "read-only-register" if op == "write" else "disabled-or-invalid-access"
    return op, command[1] - BASE, width, reason


def probe_receipt(command):
    op, offset, width, reason = expected_rejection(command)
    text = command[0] + " " + hex(command[1])
    if op == "write":
        text += " " + hex(command[2])
    return {"command": text, "callback_offset": offset, "callback_size": width,
            "specific_diagnostic": f"{TYPE}: rejected {op} offset={offset:#x} size {width} ({reason})",
            "device_callback_rejection_count": 1, "actual_core_rejections": [],
            "state_unchanged": True, "error_numeric_value_used_as_oracle": False}


def accepted_receipt(raw=False):
    return {"command": f"read {BASE:#x} 4" if raw else f"readl {BASE:#x}",
            "actual_read_data_checked": True, "specific_rejection_absent": True,
            "state_unchanged": True,
            **({"returned_bytes_hex": "48000f00"} if raw else {"returned_u32": OFFSET_VALUE})}


def reply_value(call, where):
    parts = call["reply"].split()
    need(len(parts) == 2 and parts[0] == "OK", where + ": one numeric response required")
    need(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", parts[1]) is not None,
         where + ": malformed numeric response")
    return int(parts[1], 0)


def verify_qtest_responses(calls, where, *, valid_device_reads=False, ram_values=None):
    """Read success uses returned bytes; rejected read values are deliberately unused."""
    ram_values = ram_values or {}
    facts = {"valid_four_byte_reads_or_prefixes": 0, "raw_five_byte_read_count": 0,
             "rejected_read_numeric_values_interpreted": False}
    for index, call in enumerate(calls):
        cmd = tokens(call)
        op = cmd[0]
        if op.startswith("write"):
            same(call["reply"], "OK", where + f".write transport {index}")
        elif op in ("readb", "readw", "readl", "readq", "read"):
            parts = call["reply"].split()
            need(len(parts) == 2 and parts[0] == "OK", where + ".read transport shape")
            if index in ram_values:
                same(cmd, ("readl", RAM), where + ".RAM witness read shape")
                same(reply_value(call, where), ram_values[index], where + ".actual RAM witness value")
            elif valid_device_reads and cmd == ("readl", BASE):
                same(reply_value(call, where), OFFSET_VALUE, where + ".actual aligned offset value")
                facts["valid_four_byte_reads_or_prefixes"] += 1
            elif valid_device_reads and op == "read" and cmd[1] == BASE and cmd[2] in (4, 5):
                need(re.fullmatch(r"0x[0-9a-fA-F]+", parts[1]) is not None,
                     where + ".raw data format")
                returned = bytes.fromhex(parts[1][2:])
                same(len(returned), cmd[2], where + ".actual raw response length")
                same(returned[:4], bytes.fromhex("48000f00"), where + ".valid 4-byte prefix")
                facts["valid_four_byte_reads_or_prefixes"] += 1
                if cmd[2] == 5:
                    facts["raw_five_byte_read_count"] += 1
                    # Byte 4 is a failed-hole suffix and is intentionally not interpreted.
            else:
                # QTest reports transport OK even after MEMTX_ERROR. No numeric
                # value from these reads is used to establish register semantics.
                need(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", parts[1]) is not None,
                     where + ".failed read transport shape")
        else:
            need(op in ("clock_set", "clock_step"), where + ": unexpected QTest operation")
    return facts


def audit_ordinary(store, data, report, receipt):
    checks = {row["name"]: row for row in report["checks"]}
    state, results = world(), []
    shapes = [(width, delta) for width, offsets in
              (("b", range(4)), ("w", range(3)), ("l", (1, 2, 3)), ("q", (0,)))
              for delta in offsets]
    for index, name in enumerate(ORDINARY):
        directory = data + f"/ordinary-{index:02d}-" + name
        stderr = command_contract(store, directory, report, True)
        qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
        sequence = ["qmp_capabilities", "cont"]
        commands, rejected, holes, detail = [], [], [], {}
        if index == 0:
            sequence += [state, ("list",), state]
            detail = {"actual_pure_getters": GETTERS, "offset_known_from_enabled_layout": True,
                      "exact_qom_types": {f: "uint64" if f in FIELDS[:11] else "bool" for f in FIELDS}}
        elif index == 1:
            commands = [("readl", BASE)] * 3 + [("read", BASE, 4), ("read", BASE, 5)]
            sequence += [state] * 10
            holes = [("read", BASE + 4, 1, "(null)", "rejected")]
            detail = {"actual_reads": [accepted_receipt()] * 3 + [accepted_receipt(True)],
                      "byte_order": "little", "buffered_read_command": f"read {BASE:#x} 5",
                      "buffered_read_valid_prefix_hex": "48000f00",
                      "buffered_read_actual_core_rejections": core_rows(holes),
                      "buffered_read_failed_suffix_interpreted": False,
                      "buffered_read_whole_buffer_succeeded": False,
                      "whole_buffer_atomicity_claimed": False,
                      "offset_does_not_supply_statistics_records": True}
        elif index == 2:
            patterns = [0, OFFSET_VALUE, 0xffffffff, 0x80000000, 1, 0x55555555, 0xaaaaaaaa, 0x01020304]
            rejected = [("writel", BASE, value) for value in patterns]
            commands = rejected + [("readl", BASE)]
            sequence += [state] * 18
            detail = {"rejected_u32_patterns": patterns, "actual_rejections": [probe_receipt(c) for c in rejected],
                      "mutable_device_state": False}
        elif index == 3:
            rejected = [("read" + width, BASE + delta) for width, delta in shapes]
            commands = rejected + [("readl", BASE)]
            sequence += [state] * 24
            detail = {"actual_rejections": [probe_receipt(c) for c in rejected],
                      "u64_split_into_valid_u32_reads": False, "rejected_read_numeric_values_interpreted": False}
        elif index == 4:
            rejected = [("write" + width, BASE + delta, (1 << ({"b": 1, "w": 2, "l": 4, "q": 8}[width] * 8)) - 1)
                        for width, delta in shapes]
            commands = rejected + [("write", BASE, 5, 0x04030201ff), ("readl", BASE)]
            sequence += [state] * 26
            holes = [("write", BASE + 4, 1, "(null)", "rejected")]
            detail = {"actual_scalar_rejections": [probe_receipt(c) for c in rejected],
                      "typed_eight_byte_callback_rejected_without_split": True,
                      "buffered_write_command": f"write {BASE:#x} 5 0x04030201ff",
                      "buffered_write_actual_core_rejections": core_rows(holes),
                      "buffered_write_changed_device_state": False, "whole_buffer_atomicity_claimed": False}
        elif index == 5:
            rejected = [("writel", BASE, 0)]
            commands = [("readl", BASE), rejected[0], ("clock_step", 10000), ("readl", BASE)]
            sequence += [state] * 5 + ["system_reset"] + [state] * 4 + ["system_reset", state]
            detail = {"reset_preserves_configured_profile_and_offset": True,
                      "statistics_or_firmware_data_invented": False}
        elif index == 6:
            tree = store.text(directory + "/mtree.txt")
            pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + re.escape(TYPE) + r"\.[^\s]+)(?:\s|$)"
            leaves = {(int(a, 16), int(b, 16), n) for a, b, n in re.findall(pattern, tree)}
            same(leaves, {(BASE, BASE + 3, TYPE + ".offset")}, directory + ".one exact 4-byte leaf")
            addresses = [0x0c3f0000, 0x0c3f0008, 0x0c3f001c, 0x0c3f0048, 0x0c3f004c, 0x0c3f00a4]
            observations = []
            for address in addresses:
                commands += [("readl", address), ("writel", address, 0xa55a5aa5)]
                pair = [(op, address, 4, "(null)", "rejected") for op in ("read", "write")]
                holes += pair
                observations.append({"address": address, "actual_core_rejections": core_rows(pair)})
            sequence += [("hmp", tree)] + [state] * 7
            detail = {"exact_leaves": 1, "mapped_bytes": 4, "true_holes": addresses,
                      "hole_observations": observations, "ddr_offset_added": False, "statistics_payload_added": False}
        elif index == 7:
            commands = [("clock_step", 1000000)]
            sequence += [state] + [("set-error", field,
                        not state[field] if type(state[field]) is bool else state[field] ^ 1) for field in FIELDS]
            sequence += [state, state]
            detail = {"readonly_properties_checked": list(FIELDS), "timer_or_event_progress_invented": False}
        same([tokens(call) for call in tc], commands, directory + ".entire QTest trace")
        qtest_time(tc, directory)
        read_facts = verify_qtest_responses(tc, directory, valid_device_reads=True)
        worlds = qmp_sequence(qc, sequence, directory)
        expected_device = [expected_rejection(cmd) for cmd in rejected]
        if index == 4:
            expected_device += [("write", 0, 4, "read-only-register")]
        same(device_rejections(stderr), expected_device, directory + ".exact delivered rejection callbacks")
        same(core_rejections(stderr), holes, directory + ".only exact core hole rejections")
        same(checks[name], {"name": name, "passed": True, "fixture": PurePosixPath(directory).name, **detail},
             directory + ".complete report details backed by raw evidence")
        results.append({"name": name, "actual_qom_snapshots": len(worlds),
                        "actual_pure_getters": len(worlds) * GETTERS,
                        "actual_qtest_commands": len(tc), "actual_device_rejections": len(expected_device),
                        "actual_hole_rejections": len(holes), **read_facts,
                        "complete_command_response_sequences_checked": True})

    off, name = world(False), "default_off_keeps_unknown_offset_and_rejects_all_access"
    directory = data + "/default-off"
    stderr = command_contract(store, directory, report, False, default=True)
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    rejected = [cmd for width in ("b", "w", "l", "q")
                for cmd in (("read" + width, BASE), ("write" + width, BASE, 0))]
    rejected += [("writel", BASE, value) for value in (OFFSET_VALUE, 0xffffffff)]
    same([tokens(call) for call in tc], rejected + [("clock_step", 10000)], directory + ".complete QTest trace")
    qtest_time(tc, directory)
    verify_qtest_responses(tc, directory)
    worlds = qmp_sequence(qc, ["qmp_capabilities", "cont"] + [off] * 21 + ["system_reset", off], directory)
    same(device_rejections(stderr), [expected_rejection(cmd) for cmd in rejected], directory + ".10 disabled rejections")
    same(core_rejections(stderr), [], directory + ".no false core callback or hole diagnostics")
    same(checks[name], {"name": name, "passed": True, "default_profile_used": True,
                       "actual_rejections": [probe_receipt(c) for c in rejected],
                       "unknown_zero_is_not_a_valid_guest_offset": True}, directory + ".default-off report")
    results.append({"name": name, "actual_pure_getters": len(worlds) * GETTERS,
                    "actual_rejections": len(rejected), "no_global_profile_override": True,
                    "unknown_zero_not_treated_as_valid_read": True})

    name, directory = "paused_tcg_exposes_no_mutating_fixture_or_guest_execution", data + "/paused-tcg"
    stderr = command_contract(store, directory, report, True, accel="tcg")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    same(tc, [], directory + ".no QTest or memory commands")
    worlds = qmp_sequence(qc, ["qmp_capabilities", "query-status", state, ("list",),
                     ("set-error", "qtest-offset-value", 1), ("set-error", "offset-value", 1),
                     ("set-error", "offset-known", False), state, "query-status"], directory)
    statuses = [reply(call) for call in qc if operation(call) == "query-status"]
    same([status["running"] for status in statuses], [False, False], directory + ".paused TCG statuses")
    same(device_rejections(stderr), [], directory + ".no guest MMIO")
    same(core_rejections(stderr), [], directory + ".no guest invalid memory")
    same(checks[name], {"name": name, "passed": True, "fixture_override_absent": True,
                       "guest_cpu_executed": False}, directory + ".paused TCG report")
    results.append({"name": name, "actual_pure_getters": len(worlds) * GETTERS,
                    "actual_status_running": [False, False], "qtest_commands": 0, "cont_commands": 0})
    receipt["ordinary_and_special"] = results


def source_trace(store, directory, saved, report, scenario):
    state = scenario_world(scenario)
    stderr = command_contract(store, directory, report, scenario == "enabled")
    same(device_rejections(stderr), [], directory + ".no device access on source")
    same(core_rejections(stderr), [], directory + ".no invalid memory access on source")
    same(store.json(directory + "/source-before-save.json"), state, directory + ".stopped source snapshot")
    same(store.json(directory + "/source-after-save.json"), state, directory + ".postmigrate snapshot")
    sent = store.json(directory + "/source-query-migrate.json")
    same(sent, saved["source_query_migrate"], directory + ".retained source completion reply")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    migrate = [i for i, call in enumerate(qc) if operation(call) == "migrate"]
    same(len(migrate), 1, directory + ".one source export")
    at = migrate[0]
    qmp_sequence(qc[:at], ["qmp_capabilities", "cont", state, "stop", "query-status", state],
                 directory + ".source stopped before full snapshot")
    same(reply(qc[at - GETTERS - 1]).get("running"), False, directory + ".stopped actual status")
    migration_argument(qc[at], saved["file"])
    reply(qc[at])
    status_at = [i for i in range(at + 1, len(qc)) if operation(qc[i]) == "query-status"]
    same(len(status_at), 1, directory + ".one postmigrate status")
    end = status_at[0]
    completed_polls(qc[at + 1:end], sent, directory)
    qmp_sequence(qc[end:], ["query-status", state], directory + ".postmigrate tail")
    same(reply(qc[end]).get("status"), "postmigrate", directory + ".actual postmigrate status")
    index = SCENARIOS.index(scenario)
    epoch, witness = (index + 1) * 100000, 0x52534f00 + index
    same([tokens(call) for call in tc],
         [("clock_set", epoch), ("writel", RAM, witness), ("readl", RAM)],
         directory + ".complete source QTest trace")
    same(qtest_time(tc, directory), epoch, directory + ".actual source virtual epoch")
    verify_qtest_responses(tc, directory, ram_values={2: witness})
    same(sum(is_mmio(call) for call in tc), 0, directory + ".no device state preprogramming")
    same(sum(is_memory(call) for call in tc), 2, directory + ".RAM-only source commands")
    same(sum(is_ram(call) for call in tc), 2, directory + ".source RAM commands")
    return {"qmp_requests": len(qc), "qtest_commands": len(tc), "actual_pure_getters": 3 * GETTERS,
            "source_stop_and_postmigrate_verified": True, "virtual_epoch": epoch,
            "ram_witness": witness, "actual_device_mmio_commands": 0,
            "actual_ram_commands": 2, "full_source_snapshot_and_after_save_equal": True}


def target_trace(store, directory, saved, report, scenario, migration):
    state, profile = scenario_world(scenario), scenario == "enabled"
    epoch, witness = saved["source_virtual_ns"], saved["ram_witness"]
    dirty = witness ^ 0xffffffff
    stderr = command_contract(store, directory, report, profile, incoming=True)
    expected_rejections = [("write", 0, 4, "read-only-register")]
    if not profile:
        expected_rejections = [("read", 0, 4, "disabled-or-invalid-access")] + expected_rejections + [
            ("read", 0, 4, "disabled-or-invalid-access")]
    same(device_rejections(stderr), expected_rejections, directory + ".restored access diagnostics")
    same(core_rejections(stderr), [], directory + ".no generic callback or hole rejection")
    pre = store.json(directory + "/destination-before-incoming.json")
    same(pre, {"snapshot": state, "device_mmio_count": 0, "memory_command_count": 2,
               "ram_command_count": 2, "qmp_cont_count": 0, "qom_get_count": GETTERS,
               "source_epoch_ns": epoch, "ram_witness_before": dirty,
               "ram_witness_expected_after": witness, "ram_witness_dirtied": True,
               "device_state_mutable": False, "device_state_dirtied": False},
         directory + ".RAM-only prewarm and unchanged immutable device")
    first = store.json(directory + "/destination-before-first-device-mmio.json")
    expected_first = {
        "snapshot": state, "query_migrate": first["query_migrate"],
        "device_mmio_count_before_incoming": 0, "device_mmio_count_after_first_observation": 0,
        "memory_command_count_before_incoming": 2, "memory_command_count_after_first_observation": 2,
        "qmp_cont_count_before_incoming": 0, "qmp_cont_count_after_first_observation": 0,
        "qom_get_count_before_first_observation": GETTERS,
        "qom_get_count_after_first_observation": 2 * GETTERS,
        "first_observation_qom_get_count": GETTERS, "repeated_snapshot": state,
        "qom_get_count_after_repeated_observation": 3 * GETTERS,
        "repeated_observation_qom_get_count": GETTERS,
        "device_mmio_since_incoming": False,
        "any_memory_command_since_incoming_before_observations": False,
        "qmp_cont_since_incoming": False, "guest_cpu_executed": False,
        "shared_log_timestamp_claimed": False, "device_state_mutable": False,
        "device_state_dirtied": False, "ram_witness": witness,
        "ram_witness_before": dirty, "ram_witness_loaded": True,
        "ram_witness_checked_before_cont": True,
    }
    same(first, expected_first, directory + ".complete first and repeated observations")
    same(migration["first_target_observation"], first, directory + ".report versus raw snapshot")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    incoming = [i for i, call in enumerate(qc) if operation(call) == "migrate-incoming"]
    same(len(incoming), 1, directory + ".one incoming stream")
    at = incoming[0]
    qmp_sequence(qc[:at], ["qmp_capabilities", state], directory + ".prewarm QMP")
    migration_argument(qc[at], saved["file"])
    reply(qc[at])
    status_at = [i for i in range(at + 1, len(qc)) if operation(qc[i]) == "query-status"]
    same(len(status_at), 1, directory + ".one incoming status query")
    end = status_at[0]
    completed_polls(qc[at + 1:end], first["query_migrate"], directory)
    same(reply(qc[end]).get("running"), False, directory + ".target stays stopped")
    tail = ["query-status", state, state, "cont"] + [state] * 5 + ["system_reset"] + [state] * 3
    qmp_sequence(qc[end:], tail, directory + ".entire post-incoming QMP trace")
    first_calls = qc[end + 1:end + 1 + GETTERS]
    repeated_calls = qc[end + 1 + GETTERS:end + 1 + 2 * GETTERS]
    qom_block(first_calls, queries(state), directory + ".first 17 pure QOM responses")
    qom_block(repeated_calls, queries(state), directory + ".repeated 17 pure QOM responses")
    same(sum(operation(c) == "cont" for c in qc[:end + 1 + 2 * GETTERS]), 0,
         directory + ".both observations precede first cont")
    same(sum(operation(c) == "qom-get" for c in qc[:at]), GETTERS, directory + ".actual prewarm getters")
    same(sum(operation(c) == "qom-get" for c in qc[:end + 1 + GETTERS]), 2 * GETTERS,
         directory + ".actual first observation getter total")
    same(sum(operation(c) == "qom-get" for c in qc[:end + 1 + 2 * GETTERS]), 3 * GETTERS,
         directory + ".actual repeated observation getter total")
    same(sum(operation(c) == "qom-get" for c in qc), 11 * GETTERS, directory + ".all target getter responses")
    same(sum(operation(c) == "cont" for c in qc), 1, directory + ".exactly one final continuation")
    expected_qtest = [
        ("clock_set", epoch), ("writel", RAM, dirty), ("readl", RAM), ("readl", RAM),
        ("clock_step", 10000), ("readl", BASE), ("writel", BASE, OFFSET_VALUE ^ 0xffffffff),
        ("clock_step", 10000), ("readl", BASE),
    ]
    same([tokens(call) for call in tc], expected_qtest, directory + ".entire target QTest trace")
    same(qtest_time(tc, directory), epoch + 20000, directory + ".continued then reset virtual epoch")
    verify_qtest_responses(tc, directory, valid_device_reads=profile, ram_values={2: dirty, 3: witness})
    same(sum(is_mmio(c) for c in tc[:3]), 0, directory + ".prewarm has zero device MMIO")
    same(sum(is_memory(c) for c in tc[:3]), 2, directory + ".exact prewarm memory command count")
    same(sum(is_ram(c) for c in tc[:3]), 2, directory + ".both prewarm commands target RAM")
    same(sum(is_mmio(c) for c in tc[:4]), 0, directory + ".RAM witness before any device access")
    same(wire(first["snapshot"]), wire(saved["snapshot"]), directory + ".all 24 restored bytes represented")
    same(wire(first["repeated_snapshot"]), wire(saved["snapshot"]), directory + ".all 24 repeated bytes represented")
    return {
        "first_getter_request_id": first_calls[0]["request"]["id"],
        "last_first_getter_request_id": first_calls[-1]["request"]["id"],
        "first_pure_getters": GETTERS, "repeated_pure_getters": GETTERS,
        "actual_qom_getters_total": 11 * GETTERS, "prewarm_actual_device_mmio": 0,
        "prewarm_actual_memory_commands": 2, "prewarm_actual_ram_commands": 2,
        "first_device_mmio_delta": 0, "first_memory_command_delta": 0, "first_cont_delta": 0,
        "repeated_device_mmio_delta": 0, "repeated_memory_command_delta": 0, "repeated_cont_delta": 0,
        "device_state_mutable": False, "destination_device_state_dirtied": False,
        "destination_ram_witness_dirtied": True, "ram_witness_before": dirty,
        "actual_ram_witness": witness, "ram_witness_checked_after_both_observations": True,
        "ram_witness_checked_before_cont": True, "qmp_requests": len(qc), "qtest_commands": len(tc),
        "source_snapshot_restored_before_cont": True, "payload_bytes_verified": PAYLOAD,
        "post_restore_layout_and_read_semantics_checked": True, "post_restore_reset_retains_profile": True,
        "source_status": "completed", "target_status": "completed", "shared_timestamp_used": False,
        "cross_file_order_basis": "Pinned producer sequence plus actual before/after counters, RAM witness, and independent per-file request/response order; no shared timestamp is invented.",
    }


def rejection_trace(store, directory, row, report, filename, epoch, diagnostic, profile):
    stderr = command_contract(store, directory, report, profile, incoming=True)
    full = TYPE + ": " + diagnostic
    details = {"destination_exit": 1, "expected_diagnostic": full,
               "specific_load_failure_observed": True, "target_directory": PurePosixPath(directory).name,
               "device_mmio_commands": 0, "memory_commands": 0, "cont_commands": 0}
    fields_equal(row, details, directory)
    need(row["command_error"] is None or type(row["command_error"]) is str, directory + ".command error")
    for literal in (full, "load of migration failed", "error while loading state"):
        need(literal in stderr, directory + ": missing actual fatal load diagnostic " + literal)
    failures = re.findall(re.escape(TYPE) + r": (migrated (?:profile|identity|geometry|offset) mismatch)", stderr)
    same(failures, [diagnostic], directory + ".one exact device post-load failure")
    same(device_rejections(stderr), [], directory + ".no rejected device access before failure")
    same(core_rejections(stderr), [], directory + ".no invalid memory access before failure")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    same([operation(c) for c in qc], ["qmp_capabilities", "migrate-incoming"],
         directory + ".entire failure QMP trace")
    reply(qc[0])
    migration_argument(qc[1], filename)
    same([tokens(c) for c in tc], [("clock_set", epoch)], directory + ".clock only before failed load")
    same(qtest_time(tc, directory), epoch, directory + ".failure virtual epoch")
    same(sum(is_memory(c) for c in tc), 0, directory + ".no memory witness or device workaround")
    details["command_error"] = row["command_error"]
    return {
        "actual_stderr_diagnostic": full, "destination_exit": 1, "incoming_file": filename,
        "device_mmio_commands": 0, "memory_commands": 0, "cont_commands": 0,
        "process_exit_evidence": "Pinned producer process.wait receipt plus actual fatal load stderr; auditor never re-executes the process.",
        "query_migrate_failed_response_required": False, "details_for_report_check": details,
    }


def original_stream(store, path, saved, state):
    result = scan_stream(store, path)
    fields_equal(saved, {"bytes": result["bytes"], "sha256": result["sha256"],
                         "stream_modified": False, "source_status": "completed",
                         "device_state_mutable": False}, path)
    same(result["payload"], wire(state), path + ".all independently encoded 24 bytes")
    section = dict(result["section"])
    section.update(payload_file=PurePosixPath(path).name + ".rpm-stats-offset-v1.payload.bin",
                   payload_sha256=sha(result["payload"]), all_bytes_match_stopped_source_qom=True)
    expected = {"rpm_stats_offset": section}
    same(saved["sections"], expected, path + ".producer section metadata versus independent framing")
    same(store.json(path + ".sections.json"), expected, path + ".retained section sidecar")
    same(store.read(path + ".rpm-stats-offset-v1.payload.bin"), result["payload"], path + ".complete retained payload")
    return result


def audit_migrations(store, data, report, receipt):
    expected_names = ["rpm-stats-offset-" + name + ".migration" for name in SCENARIOS]
    expected_names += ["rpm-stats-offset-corrupt-" + m[0] + ".migration" for m in MUTATIONS]
    actual_names = sorted(PurePosixPath(path).name for path in store.entries
                          if path.startswith(data + "/") and "/" not in path[len(data) + 1:]
                          and path.endswith(".migration"))
    same(actual_names, sorted(expected_names), "Exactly two original and six mutant complete streams")
    same([r["name"] for r in report["actual_stream_files"]], sorted(expected_names), "Complete declared stream list")
    actual_index = {r["name"]: r for r in report["actual_stream_files"]}
    checks = {row["name"]: row for row in report["checks"]}
    originals, saved_by_name = {}, {}
    receipt["original_streams"] = []
    for index, (scenario, saved) in enumerate(zip(SCENARIOS, report["migration_sources"])):
        filename, state = "rpm-stats-offset-" + scenario + ".migration", scenario_world(scenario)
        fields_equal(saved, {"file": filename, "source_directory": "migration-" + scenario + "-source",
                             "source_virtual_ns": (index + 1) * 100000,
                             "ram_witness": 0x52534f00 + index, "snapshot": state}, scenario)
        result = original_stream(store, data + "/" + filename, saved, state)
        same(actual_index[filename], {"name": filename, "bytes": result["bytes"], "sha256": result["sha256"]},
             scenario + ".actual original stream inventory")
        trace = source_trace(store, data + "/" + saved["source_directory"], saved, report, scenario)
        receipt["original_streams"].append({"name": filename, "bytes": result["bytes"], "sha256": result["sha256"],
                                           "section": result["section"], "payload_hex": result["payload"].hex(),
                                           "source_qom": state, "trace": trace})
        originals[scenario], saved_by_name[scenario] = result, saved
    receipt["completed_migrations"] = []
    for scenario, row in zip(SCENARIOS, report["migrations"]):
        saved, name = saved_by_name[scenario], "migration_" + scenario
        fields_equal(row, {
            "name": name, "passed": True, "source_status": "completed", "target_status": "completed",
            "snapshot_file": saved["file"], "snapshot_sha256": saved["sha256"], "stream_modified": False,
            "source_directory": saved["source_directory"], "target_directory": "migration-" + scenario + "-target",
            "continued_after_restore": True, "all_24_payload_bytes_verified": True,
            "post_restore_layout_and_read_semantics_checked": True, "post_restore_reset_retains_profile": True,
            "destination_ram_witness_dirtied": True, "destination_device_state_dirtied": False}, name)
        same(checks[name], {"name": name, "passed": True, "first_observation_mmio_delta": 0,
                           "first_observation_cont_delta": 0, "ram_witness_loaded_before_cont": True,
                           "device_state_dirtied": False, "post_restore_reset_retains_profile": True}, name + ".check row")
        trace = target_trace(store, data + "/" + row["target_directory"], saved, report, scenario, row)
        receipt["completed_migrations"].append({"name": name, **trace})
    receipt["profile_rejections"] = []
    for row, (name, scenario, profile) in zip(report["rejected_migrations"],
                                            (("on_to_off", "enabled", False), ("off_to_on", "disabled", True))):
        saved = saved_by_name[scenario]
        fields_equal(row, {"name": name, "passed": True, "snapshot_file": saved["file"],
                           "snapshot_sha256": saved["sha256"], "stream_modified": False}, name)
        trace = rejection_trace(store, data + "/reject-profile-" + name, row, report, saved["file"],
                                saved["source_virtual_ns"], "migrated profile mismatch", profile)
        check_name = "profile_" + name + "_rejected"
        same(checks[check_name], {"name": check_name, "passed": True, **trace.pop("details_for_report_check")},
             check_name + ".same actual fatal-load evidence")
        receipt["profile_rejections"].append({"name": name, **trace})
    receipt["one_bit_rejections"] = []
    for row, (name, scenario, offset, width, old, xor, profile, diagnostic) in zip(report["corrupted_stream_rejections"], MUTATIONS):
        saved, original = saved_by_name[scenario], originals[scenario]
        filename = "rpm-stats-offset-corrupt-" + name + ".migration"
        result = scan_stream(store, data + "/" + filename)
        same(result["section"], original["section"], name + ".unchanged complete framing")
        same(int.from_bytes(original["payload"][offset:offset + width], "big"), old, name + ".actual old value")
        changed_payload = bytearray(original["payload"])
        changed_payload[offset:offset + width] = (old ^ xor).to_bytes(width, "big")
        same(result["payload"], bytes(changed_payload), name + ".entire independently constructed mutated payload")
        need(0 < xor < 256 and xor.bit_count() == 1, name + ".literal low-byte single-bit mutation")
        absolute = original["section"]["payload_offset"] + offset + width - 1
        difference = one_bit_difference(store, data + "/" + saved["file"], data + "/" + filename,
                                        absolute, xor, original["sha256"], result["sha256"])
        fields_equal(row, {
            "name": name, "passed": True, "source_file": saved["file"], "source_sha256": original["sha256"],
            "snapshot_file": filename, "snapshot_sha256": result["sha256"], "stream_modified": True,
            "field_offset": offset, "field_bytes": width, "old_value": old, "xor": xor,
            "changed_byte_offsets": [absolute], "changed_bits": 1,
            "destination_reference_enabled": profile, "original_stream_unchanged": True}, name)
        same(actual_index[filename], {"name": filename, "bytes": result["bytes"], "sha256": result["sha256"]},
             name + ".actual mutant stream inventory")
        trace = rejection_trace(store, data + "/reject-corrupt-" + name, row, report, filename,
                                saved["source_virtual_ns"], diagnostic, profile)
        check_name = "single_bit_" + name + "_rejected"
        same(checks[check_name], {"name": check_name, "passed": True, **trace.pop("details_for_report_check")},
             check_name + ".same actual fatal-load evidence")
        receipt["one_bit_rejections"].append({
            "name": name, "bytes": result["bytes"], "sha256": result["sha256"], "original": saved["file"],
            "payload_hex": result["payload"].hex(), "whole_stream_comparison": difference, **trace})


def one_match(pattern, source, where, flags=0):
    matches = list(re.finditer(pattern, source, flags))
    same(len(matches), 1, where + ".one source match")
    return matches[0]


def independent_public_derivation(texts):
    """Fixed pinned-source structural extraction, independent of generator imports."""
    vendor, vendor_dt, upstream, upstream_dt = texts
    node = one_match(r"\bqcom,rpm-stats@c3f0004\s*\{([^{}]*)\};", vendor_dt,
                     "vendor offset node", re.S).group(1)
    regs = one_match(r"\breg\s*=\s*([^;]+);", node, "vendor reg").group(1)
    cells = [int(n, 0) for n in re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b", regs)]
    same(cells, [0x0c300000, 0x1000, BASE, 4], "actual four pinned vendor resource cells")
    names = re.findall(r'"([^"]+)"', one_match(r"\breg-names\s*=\s*([^;]+);", node, "vendor reg-names").group(1))
    same(names, ["phys_addr_base", "offset_addr"], "actual vendor resource names")
    records = int(one_match(r"\bqcom,num-records\s*=\s*<\s*(\d+)\s*>;", node, "vendor records").group(1))
    same(records, 3, "vendor record count")
    probe = one_match(r"static int msm_rpmstats_probe\b(.*?)static int msm_rpmstats_remove\b",
                      vendor, "bounded vendor probe", re.S).group(1)
    need(re.search(r"offset_addr\s*=\s*readl_relaxed\s*\(\s*phys_ptr\s*\)", probe) is not None,
         "Vendor uint32 offset read absent")
    need(re.search(r"pdata->phys_addr_base\s*=\s*res->start\s*\+\s*offset_addr\s*;", probe) is not None,
         "Vendor base-plus-offset consumer absent")
    mmio = re.findall(r"\b((?:read|write)[bwlq](?:_relaxed)?|__raw_(?:read|write)[bwlq]|ioread\w*|iowrite\w*|memcpy_(?:from|to)io)\s*\(", probe)
    same(mmio, ["readl_relaxed"], "Only one direct MMIO operation in bounded vendor probe")
    upstream_node = one_match(r"\bsram@c3f0000\s*\{([^{}]*)\};", upstream_dt,
                              "upstream SM8250 stats node", re.S).group(1)
    compatible = one_match(r'\bcompatible\s*=\s*"([^"]+)"\s*;', upstream_node, "upstream compatible").group(1)
    same(compatible, "qcom,rpmh-stats", "upstream fixed layout compatible")
    regs = one_match(r"\breg\s*=\s*([^;]+);", upstream_node, "upstream reg").group(1)
    cells_upstream = [int(n, 0) for n in re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b", regs)]
    same(cells_upstream, [0, 0x0c3f0000, 0, 0x400], "upstream actual 64-bit address/size cells")
    config_name = one_match(r'\{\s*\.compatible\s*=\s*"qcom,rpmh-stats"\s*,\s*\.data\s*=\s*&(\w+)\s*\}',
                            upstream, "upstream compatible dispatch").group(1)
    same(config_name, "rpmh_data", "actual selected config")
    config = one_match(r"\bstatic const struct stats_config rpmh_data\s*=\s*\{([^{}]*)\};",
                       upstream, "upstream fixed RPMh config", re.S).group(1)
    stats_offset = int(one_match(r"\.stats_offset\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*,", config,
                                 "fixed stats offset").group(1), 0)
    count = int(one_match(r"\.num_records\s*=\s*(\d+)\s*,", config, "fixed record count").group(1))
    same((stats_offset, count), (0x48, 3), "upstream fixed offset and record count")
    for field, expected in (("dynamic_offset", "false"), ("appended_stats_avail", "false"),
                             ("subsystem_stats_in_smem", "true")):
        same(one_match(r"\." + field + r"\s*=\s*(true|false)\s*,", config, field).group(1),
             expected, "upstream config." + field)
    stat = one_match(r"\bstruct sleep_stats\s*\{([^{}]*)\};", upstream, "sleep_stats record", re.S).group(1)
    record_fields = re.findall(r"\b(u32|u64)\s+(\w+)\s*;", stat)
    same(record_fields, [("u32", "stat_type"), ("u32", "count"), ("u64", "last_entered_at"),
                         ("u64", "last_exited_at"), ("u64", "accumulated")], "actual upstream record layout")
    record_bytes = sum(4 if kind == "u32" else 8 for kind, name in record_fields)
    same(record_bytes, 32, "record size without appended stats")
    payload_address = cells_upstream[1] + stats_offset
    relative = payload_address - cells[0]
    same((payload_address, relative), (0x0c3f0048, OFFSET_VALUE), "independent cross-source pointer arithmetic")
    return {"vendor_resource_base": cells[0], "offset_word_address": cells[2], "offset_window_bytes": cells[3],
            "upstream_region_base": cells_upstream[1], "fixed_stats_offset": stats_offset,
            "reference_payload_address": payload_address, "relative_word": relative,
            "little_endian_word_hex": relative.to_bytes(4, "little").hex(),
            "reference_num_records": count, "reference_record_bytes": record_bytes,
            "reference_payload_bytes": count * record_bytes, "payload_mapped_by_this_device": False,
            "captured_runtime_word_claimed": False, "native_reset_value_claimed": False,
            "generic_firmware_to_apss_alias_claimed": False}


def locate_case(store, name):
    candidates = [p for p in store.entries
                  if re.search(r"(?:^|/)components/[0-9]+-" + re.escape(name) + r"/case\.json$", p)]
    same(len(candidates), 1, "Exactly one compiled case " + name)
    path = candidates[0]
    root = path.rsplit("/", 1)[0]
    case = store.json(path)
    fields_equal(case, {"name": name, "passed": True, "exit_code": 0}, name + ".actual harness verdict")
    need(not any(k in case for k in ("error", "artifact_error", "timed_out")), name + ": failed/incomplete case")
    need(type(case.get("completed_utc")) is str and case["completed_utc"], name + ": missing completion time")
    logs = {}
    for filename in ("stdout.log", "stderr.log"):
        raw = store.read(root + "/" + filename)
        same(case["log_sha256"][filename], sha(raw), name + ".actual harness log hash " + filename)
        logs[filename] = raw
    command = case["command"]
    need(type(command) is list and all(type(s) is str for s in command), name + ".actual harness argv")
    need(not set(command).intersection(("-O", "-OO", "-c")), name + ": assertion-disabled or inline producer")
    return path, root, case, logs


def source_head(store, case_root):
    prefix = case_root.split("/components/", 1)[0]
    path = prefix + "/source-commit.txt"
    same(store.text(path).strip(), HEAD, "Actual archived exact QEMU head")
    return path


def audit_source_generation(store, receipt, expected_source_head_file):
    case_path, root, case, logs = locate_case(store, GENERATION_CASE)
    same(source_head(store, root), expected_source_head_file, "Both new cases share exact source provenance")
    data = root + "/fixtures/data"
    report = store.json(data + "/result.json")
    fields_equal(report, {"schema": "tb371fc-rpm-stats-offset-generation-v1", "passed": True,
                         "source_revision": PRIMARY, "geometry_source_revision": UPSTREAM,
                         "source_pins_verified": True, "generator_exit": 0}, "Actual generation report")
    need("error" not in report, "Generation report recorded an exception")
    same(len(report["inputs"]), 4, "Exactly four actual public inputs")
    same(len(report["checks"]), 2, "Both actual generated outputs")
    fields_equal(case["verified"], {"verdict": True, "verdict_field": "passed", "check_count": 2,
                                   "verified_lengths": {"inputs": 4}}, "Generation strict harness contract")
    command = case["command"]
    same(len(command), 4, "Complete generation harness argv")
    same(PurePosixPath(command[1]).name, "verify_rpm_stats_offset_generation.py", "Generation producer path")
    same(command[2], "--out", "Generation output flag")
    same(PurePosixPath(command[3]), PurePosixPath(case["fixture"]) / "data", "Generation fixture path")
    same(json_value(logs["stdout.log"], "generation harness stdout"),
         {"passed": True, "inputs": 4, "checks": 2, "out": command[3]}, "Generation stdout summary")
    texts, inputs = [], []
    for row, (prefix, repo, revision, relative, size, blob, expected) in zip(report["inputs"], PUBLIC_INPUTS):
        local = prefix + "/" + relative
        raw = store.read(data + "/inputs/" + local, limit=256 * 1024)
        actual_blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        same((len(raw), sha(raw), actual_blob), (size, expected, blob), local + ".complete independent identity")
        want = {"path": local, "source_path": relative, "repository": repo, "revision": revision,
                "url": f"https://raw.githubusercontent.com/{repo}/{revision}/{relative}",
                "expected_bytes": size, "expected_sha256": expected, "expected_git_blob_sha": blob,
                "transport": "shared-source-download", "bytes": size, "sha256": expected, "git_blob_sha": blob}
        same(row, want, local + ".actual CI input provenance")
        texts.append(raw.decode("utf-8", errors="strict"))
        inputs.append({"path": local, "bytes": size, "sha256": expected, "git_blob_sha": blob,
                       "revision": revision, "complete_file_rehashed": True})
    derivation = independent_public_derivation(texts)
    outputs = []
    for row, relative in zip(report["checks"], OUTPUTS):
        size, expected = SOURCE_PINS[relative]
        raw = store.read(data + "/generated/" + relative)
        same((len(raw), sha(raw)), (size, expected), relative + ".actual generated bytes")
        same(row, {"name": relative, "passed": True, "generated_bytes": size, "committed_bytes": size,
                   "generated_sha256": expected, "committed_sha256": expected}, relative + ".actual output receipt")
        outputs.append({"path": relative, "bytes": size, "sha256": expected, "exact_source_output_bytes": True})
    manifest = store.json(data + "/generated/" + OUTPUTS[1])
    contract = {"source_input_count": 4, "register_count": 1, "window_count": 1,
                "mapped_bytes": 4, "physical_base": BASE, "offset_value": OFFSET_VALUE}
    same(report["generated_contract"], contract, "Actual source generation contract")
    fields_equal(manifest, contract, "Actual generated manifest")
    fields_equal(manifest["reference_scope"], {
        "default_enabled": False, "enabled_aligned_read32_supported": True, "writes_supported": False,
        "disabled_reads_supported": False, "statistics_payload_implemented": False,
        "statistics_counters_implemented": False, "statistics_names_implemented": False,
        "firmware_ready_implemented": False, "aop_execution_implemented": False,
        "irq_output_implemented": False, "clock_implemented": False, "timer_implemented": False,
        "ddr_statistics_pointer_implemented": False, "mailbox_aperture_modified": False,
        "native_reset_value_known": False, "whole_production_kernel_source_identity_claimed": False},
        "Generated unsupported behavior boundaries")
    fields_equal(manifest["upstream_layout"], {"payload_mapped_by_this_device": False,
                 "payload_base": 0x0c3f0048, "payload_bytes": 96, "record_bytes": 32, "num_records": 3},
                 "Generated reference payload remains unmapped")
    same(manifest["derivation"]["captured_runtime_word_claimed"], False, "No captured runtime word claim")
    generator_stdout = json_value(store.read(data + "/generation.stdout"), "generator stdout")
    same(generator_stdout, {"source_inputs": 4, "outputs": list(OUTPUTS),
                            "physical_base": BASE, "mapped_bytes": 4, "offset_value": OFFSET_VALUE},
         "Actual generator stdout")
    same(store.read(data + "/generation.stderr"), b"", "No generator stderr")
    receipt["source_generation"] = {"case": case_path, "report": data + "/result.json", "checks": 2,
                                    "inputs": inputs, "outputs": outputs, "independent_derivation": derivation,
                                    "actual_archived_inputs_and_outputs_rehashed": True,
                                    "generator_executed_by_auditor": False, "generator_imported": False}


def report_constants():
    return {
        "schema": "tb371fc-rpm-stats-offset-sm8250-v1", "suite": CASE, "expected_checks": 20,
        "expected_completed_migrations": 2, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 6, "expected_unmodified_streams": 2, "expected_stream_files": 8,
        "vmstate_version": 1, "vmstate_minimum_version": 1, "vmstate_payload_bytes": PAYLOAD,
        "reference_identity": IDENTITY, "physical_base": BASE, "register_count": 1, "sparse_window_count": 1,
        "mapped_bytes": 4, "source_offset_value": OFFSET_VALUE, "first_observation_qom_get_count": GETTERS,
        "irq_output_count": 0, "input_clock_count": 0, "output_clock_count": 0, "timer_count": 0,
        "reference_enabled_by_default": False, "readonly_offset_register": True,
        "mutable_device_state": False, "migration_destination_device_state_dirtied": False,
        "migration_destination_ram_witness_dirtied": True, "stats_payload_modeled": False,
        "ddr_offset_modeled": False, "firmware_modeled": False, "ready_modeled": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False, "android_boot_passed": False,
        "binary_unchanged": True,
    }


def report_contract(report, qemu_sha):
    expected = {
        **report_constants(), "passed": True, "machine": DEFAULT_MACHINE,
        "source_revision": PRIMARY, "geometry_source_revision": UPSTREAM, "test_script_sha256": TEST_SHA,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "qemu_sha256_before": qemu_sha, "qemu_sha256_after": qemu_sha,
        "scope": "One configured read-only RPM statistics offset word. No statistics payload, "
                 "DDR offset, firmware, READY, clock or timer behavior. No mutable device state "
                 "is claimed: incoming prewarming changes RAM only. No guest CPU, native probe "
                 "or Android boot claim.",
    }
    fields_equal(report, expected, "Producer report metadata and supported boundaries")
    same(set(report), set(expected) | set(LENGTHS) | {"qemu", "work_dir", "duration_seconds"},
         "Exact known producer report fields, no extra claims")
    need(type(report["duration_seconds"]) in (int, float) and report["duration_seconds"] >= 0,
         "Actual nonnegative producer duration")
    for key, length in LENGTHS.items():
        need(type(report[key]) is list, "Producer array required: " + key)
        same(len(report[key]), length, "Producer exact array length: " + key)
    names = list(ORDINARY) + ["default_off_keeps_unknown_offset_and_rejects_all_access",
                             "paused_tcg_exposes_no_mutating_fixture_or_guest_execution",
                             "migration_enabled", "migration_disabled",
                             "profile_on_to_off_rejected", "profile_off_to_on_rejected"]
    names += ["single_bit_" + mutation[0] + "_rejected" for mutation in MUTATIONS]
    same([check["name"] for check in report["checks"]], names, "Exact 20 checks in actual execution order")
    for check in report["checks"]:
        same(check.get("passed"), True, "Producer named check " + check["name"])
    for key in ("qemu", "work_dir"):
        need(type(report[key]) is str and PurePosixPath(report[key]).is_absolute(), "Absolute " + key + " required")


def select_component(store, receipt, qemu_sha):
    case_path, root, case, logs = locate_case(store, CASE)
    report = store.json(root + "/fixtures/data/result.json")
    report_contract(report, qemu_sha)
    same(json_value(logs["stdout.log"], "component harness stdout"), report, "Actual stdout equals retained report")
    fields_equal(case["verified"], {"verdict": True, "verdict_field": "passed",
                                   "check_count": 20, "verified_lengths": LENGTHS}, "Strict CI component contract")
    command = case["command"]
    same(len(command), 6, "Complete component harness argv")
    same(PurePosixPath(command[1]).name, "test_rpm_stats_offset_sm8250.py", "Exact component script name")
    same(command[2:], ["--qemu", report["qemu"], "--out", report["work_dir"]], "Exact compiled producer flags")
    same(PurePosixPath(report["work_dir"]), PurePosixPath(case["fixture"]) / "data", "Actual fixture directory")
    head_path = source_head(store, root)
    receipt["component"] = {"case": case_path, "report": root + "/fixtures/data/result.json",
                            "source_commit_file": head_path, "head": HEAD, "checks": 20,
                            "producer_test_sha256": TEST_SHA, "exit_code": 0}
    return root + "/fixtures/data", report, head_path


def validate_reviewed_sources(root):
    results = {}
    for relative, (size, expected) in SOURCE_PINS.items():
        path = root / relative
        need(path.is_file() and not path.is_symlink(), "Missing/nonregular reviewed source " + relative)
        raw = path.read_bytes()
        same((len(raw), sha(raw)), (size, expected), "Frozen exact source " + relative)
        results[relative] = {"bytes": size, "sha256": expected}
        if relative.endswith(".py"):
            compile(raw, str(path), "exec")
    contract = json_value((root / "tb371fc/evidence/rpm-stats-offset-20261125-6a5/test-ci-contract.json").read_bytes(),
                          "local pinned compiled-test contract")
    same(contract["expected_values"], report_constants(), "Independent constants equal pinned CI contract")
    same(contract["expected_lengths"], LENGTHS, "Independent lengths equal pinned CI contract")
    helper = root / "tb371fc/tests/test_camera_gdsc.py"
    if not helper.exists():
        helper = root / ".work/reviewed-base-test_camera_gdsc.py"
    raw = helper.read_bytes()
    same((len(raw), sha(raw)), (74782, "e187b3de97b5ae2d53376bd41d1f82099053c69bce9034af572830f8e1c1e619"),
         "Reviewed inherited CameraRig transport source")
    results["inherited-test_camera_gdsc.py"] = {
        "bytes": len(raw), "sha256": sha(raw), "reviewed_at_ancestor": "8d0bc64d8da6da18bf43b52a2a9547f28f44c3e9"}
    return results


def static_self_check():
    same(wire(world()).hex(), "0000000152534f31000000000c3f000400000004000f0048",
         "Literal independent enabled 24-byte wire")
    same(wire(world(False)).hex(), "0000000052534f31000000000c3f00040000000400000000",
         "Literal independent disabled 24-byte wire")
    same(len(FIELDS), GETTERS, "17 exact observer names")
    same(len(set(FIELDS)), GETTERS, "17 unique observer names")
    same(len(ORDINARY) + 2 + sum(LENGTHS[k] for k in
         ("migrations", "rejected_migrations", "corrupted_stream_rejections")), 20, "20 group arithmetic")
    same([offset + width - 1 for name, scenario, offset, width, old, xor, profile, reason in MUTATIONS],
         [3, 7, 15, 19, 23, 23], "Exact single-bit byte positions")
    same(0x0c3f0000 + 0x48 - 0x0c300000, OFFSET_VALUE, "Independent reference pointer arithmetic")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-tree", default=TREE)
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-qemu-sha256")
    parser.add_argument("--source-root", type=Path, default=Path("/dev/shm/ddrstats-candidate-6a5"))
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Refusing to overwrite existing independent audit evidence: " + str(args.out))
    receipt = {
        "schema": "tb371fc-rpm-stats-offset-independent-artifact-audit-v1", "passed": False,
        "observed_utc": datetime.now(timezone.utc).isoformat(), "auditor_sha256": file_hash(__file__),
        "expected_head": args.expected_head, "expected_tree": args.expected_tree,
        "expected_zip_sha256": args.expected_zip_sha256, "read_only_archive": True,
        "qemu_executed": False, "guest_executed": False, "extracted_members": [],
        "producer_serializer_imported": False, "model_imported": False, "generator_imported": False,
        "whole_workflow_verdict_claimed": False, "native_android_passed": False,
        "shared_log_timestamp_claimed": False,
        "scope": "Actual compiled RPM offset component: 20 groups, two original plus six complete one-bit streams, full 24-byte wire, raw QMP/QTest/log evidence, and the separate four-input source-generation case. Aggregate and retained legacy workflow audits remain separate.",
        "protocol_limit": "Separate QMP/QTest logs have no shared timestamp. Exact per-file order, actual counter receipts, the restored RAM witness and pinned producer order are combined without inventing a global timestamp.",
        "mmio_limit": "A typed invalid operation reaches one rejecting device callback. A raw five-byte read returns only a valid four-byte prefix; its failed hole suffix is not data. A raw five-byte write rejects its four-byte device prefix and its one-byte unmapped suffix. Whole-buffer success or atomicity is not claimed.",
        "state_limit": "The device is immutable configured metadata. Migration prewarm changes RAM only; no mutable device state or real firmware reset value is claimed.",
        "source_tree_binding_basis": "Exact published head/tree supplied by the coordinator; archive source-commit.txt is independently checked. An absent archive tree file is not invented.",
        "prior_failed_head": {"head": FAILED_HEAD, "board_job": 138930599415,
                              "qualified": False, "reason": "Actual compile failed on obsolete hw/sysbus.h include; corrected source uses hw/core/sysbus.h."},
    }
    store = None
    try:
        static_self_check()
        need(HEAD != "UNBOUND_HEAD" and TREE != "UNBOUND_TREE", "Auditor is not bound to published source")
        same(args.expected_head, HEAD, "Auditor bound exact corrected head")
        same(args.expected_tree, TREE, "Auditor bound exact corrected tree")
        for value, length, name in ((args.expected_head, 40, "head"), (args.expected_tree, 40, "tree"),
                                     (args.expected_zip_sha256, 64, "ZIP SHA256")):
            need(re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None, "Malformed " + name)
        if args.expected_qemu_sha256 is not None:
            need(re.fullmatch("[0-9a-f]{64}", args.expected_qemu_sha256) is not None, "Malformed QEMU SHA256")
        receipt["reviewed_source_pins"] = validate_reviewed_sources(args.source_root)
        same(file_hash(args.zip), args.expected_zip_sha256, "Actual full ZIP hash")
        receipt["archive"] = {"path": str(args.zip.resolve()), "bytes": args.zip.stat().st_size,
                              "sha256": args.expected_zip_sha256}
        store = Store(zip_path=args.zip)
        binary = "qemu-build/qemu-system-aarch64"
        qemu = hash_member(store, binary)
        if args.expected_qemu_sha256 is not None:
            same(qemu["sha256"], args.expected_qemu_sha256, "External actual QEMU hash pin")
        with store.open(binary) as source:
            header = source.read(20)
        same(header[:6], b"\x7fELF\x02\x01", "Actual QEMU ELF64 little endian")
        same(len(header), 20, "Complete actual QEMU ELF header prefix")
        receipt["qemu"] = {"member": binary, **qemu, "executed": False,
                           "host_elf_machine": int.from_bytes(header[18:20], "little"),
                           "externally_pinned": args.expected_qemu_sha256 is not None}
        data, report, head_path = select_component(store, receipt, qemu["sha256"])
        audit_source_generation(store, receipt, head_path)
        audit_ordinary(store, data, report, receipt)
        audit_migrations(store, data, report, receipt)
        receipt["summary"] = {
            "checks": 20, "ordinary_and_special_groups": 10, "original_streams": 2,
            "completed_migrations": 2, "profile_rejections": 2, "one_bit_rejections": 6,
            "total_stream_files": 8, "payload_bytes": PAYLOAD, "first_getters": GETTERS,
            "repeated_getters": GETTERS, "prewarm_device_mmio": 0, "prewarm_ram_commands": 2,
            "first_memory_command_delta": 0, "first_device_mmio_delta": 0, "first_cont_delta": 0,
            "device_state_dirtied": False, "ram_witness_dirtied": True,
            "source_generation_checks": 2, "source_generation_inputs": 4,
            "actual_archive_evidence_verified": True, "native_probe_claimed": False, "android_boot_claimed": False,
        }
        receipt["verified_members"] = dict(sorted(store.observed.items()))
        receipt["verified_member_count"] = len(store.observed)
        store.finish()
        store = None
        same(file_hash(args.zip), args.expected_zip_sha256, "Actual ZIP unchanged after full audit")
        receipt["passed"] = True
    except BaseException as error:
        receipt["error"], receipt["traceback"] = str(error), traceback.format_exc()
        if store is not None:
            receipt["verified_members"] = dict(sorted(store.observed.items()))
            receipt["verified_member_count"] = len(store.observed)
    finally:
        if store is not None:
            try:
                store.finish()
            except BaseException as error:
                receipt["archive_finish_error"] = str(error)
                receipt["passed"] = False
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("x") as output:
        json.dump(receipt, output, indent=2)
        output.write("\n")
    print(json.dumps({"passed": receipt["passed"], "out": str(args.out),
                      "receipt_sha256": file_hash(args.out),
                      "verified_member_count": receipt.get("verified_member_count", 0),
                      "error": receipt.get("error")}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
