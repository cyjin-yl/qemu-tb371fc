#!/usr/bin/env python3
"""Independent SPSS actual-ZIP audit; never extracts or executes QEMU.

The 25-byte wire is reconstructed from an independent explicit offset map.
Protocol parsers are reused from the previously reviewed LLCC auditor; no
producer serializer, test module or model module is imported. There is no
shared timestamp between QMP/QTest logs: the audit combines per-file order,
actual counter receipts and the pinned producer call sequence.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import traceback
from zipfile import ZipFile

CHUNK, MAX_SMALL = 1024 * 1024, 64 * 1024 * 1024
HEAD = "54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7"
TEST_SHA = "c0a74b8ad19b621ebb748bcb388e5f02248c212912c1f19d9de0cbb8d546e180"
MODEL_SHA = "9b8e0b32fa58b703726932f587db175e9ca511edacb46c7b3e86b667e83edc0f"
TYPE, DEVICE = "qcom-sm8250-spss-irq-mask", "/machine/spss-irq-mask"
BASE, RAM, IDENTITY, DRIVER_MASK = 0x01881028, 0x80010000, 0x53504d31, 0xfcffffff
PAYLOAD, GETTERS = 25, 18
PRIMARY = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
CASE = "spss-irq-mask-sm8250-reference"
FIELDS = (
    "enabled-profile", "reference-identity", "physical-base", "window-bytes",
    "mask-value", "vmstate-bytes", "register-count", "irq-output-count",
    "clock-input-count", "clock-output-count", "timer-count", "programmed",
    "mask-known", "readback-supported", "ready-modeled", "firmware-modeled",
    "pas-modeled", "enabled-reference",
)
ORDINARY = (
    "cold_identity_unknown_and_pure_getters", "literal_original_probe_mask",
    "full_u32_writes_replace_entire_mask", "zero_programming_provenance_and_reset",
    "every_guest_read_is_rejected", "invalid_width_and_alignment_never_program",
    "exact_single_leaf_and_surrounding_holes",
    "runtime_observers_are_readonly_and_time_has_no_effect",
)
SCENARIOS = ("unknown", "driver", "known-zero", "disabled")
MUTATIONS = (
    ("identity", "driver", 4, 4, IDENTITY, 1, True, "migrated identity mismatch"),
    ("physical_base", "driver", 8, 8, BASE, 4, True, "migrated geometry mismatch"),
    ("window_bytes", "driver", 16, 4, 4, 1, True, "migrated geometry mismatch"),
    ("programmed_flag", "unknown", 24, 1, 0, 2, True, "invalid migrated programmed flag"),
    ("unknown_mask", "unknown", 20, 4, 0, 1, True, "invalid migrated unknown mask"),
    ("disabled_programmed", "disabled", 24, 1, 0, 1, False, "invalid migrated disabled state"),
)
LENGTHS = {"checks": 21, "migrations": 3, "rejected_migrations": 2,
           "corrupted_stream_rejections": 6, "migration_sources": 4, "actual_stream_files": 10}


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


def world(profile=True, mask=0, written=False):
    boolean(profile, "profile")
    boolean(written, "written")
    uint(mask, 32, "mask")
    need(written or mask == 0, "Unknown mask must have zero placeholder")
    need(profile or (not written and mask == 0), "Disabled state must be unknown zero")
    values = (int(profile), IDENTITY, BASE, 4, mask, 25, 1, 0, 0, 0, 0,
              written, written, False, False, False, False, profile)
    return dict(zip(FIELDS, values))


def scenario_world(name):
    need(name in SCENARIOS, "Unknown scenario")
    return world(name != "disabled", DRIVER_MASK if name == "driver" else 0,
                 name in ("driver", "known-zero"))


def check_world(value, where):
    need(type(value) is dict, where + ": object required")
    same(set(value), set(FIELDS), where + ".exact QOM fields")
    for key in FIELDS[:11]:
        uint(value[key], 64, where + "." + key)
    for key in FIELDS[11:]:
        boolean(value[key], where + "." + key)
    same(value, world(value["enabled-reference"], value["mask-value"], value["programmed"]),
         where + ".independent state invariants")


def wire(value):
    """Explicit byte offsets, not the producer's struct format/serializer."""
    check_world(value, "wire input")
    result = bytearray(25)
    fields = ((0, 4, value["enabled-profile"]), (4, 4, IDENTITY),
              (8, 8, BASE), (16, 4, 4), (20, 4, value["mask-value"]),
              (24, 1, int(value["programmed"])))
    covered = set()
    for offset, length, number in fields:
        need(not covered.intersection(range(offset, offset + length)), "Overlapping wire fields")
        covered.update(range(offset, offset + length))
        result[offset:offset + length] = number.to_bytes(length, "big")
    same(covered, set(range(25)), "Complete independent wire layout")
    return bytes(result)


def queries(value):
    check_world(value, "QOM expectation")
    return [({"path": DEVICE, "property": name}, value[name]) for name in FIELDS]


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
                end = payload + 25
                if end + 5 > len(block):
                    continue
                section_id = int.from_bytes(block[header + 1:header + 5], "big")
                footer = b"\x7e" + section_id.to_bytes(4, "big")
                same(block[end:end + 5], footer, path + ".complete footer")
                section = {"name": TYPE, "instance_id": 0, "version": 1,
                           "header_offset": absolute, "section_id": section_id,
                           "payload_offset": start + payload, "payload_bytes": 25,
                           "footer_offset": start + end, "footer_hex": footer.hex()}
                item = (section, bytes(block[payload:end]))
                if previous is not None:
                    same(item, previous, path + ".overlap scan")
                candidates[absolute] = item
            total += len(chunk)
            tail = block[-4096:]
    same(magic, b"QEVM\x00\x00\x00\x03", path + ".stream magic/version")
    same(total, store.size(path), path + ".actual whole length")
    same(len(candidates), 1, path + ".unique complete SPSS section")
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
    """Each world requires 18 adjacent actual getter requests and responses."""
    pos = 0
    worlds = []
    for expected in sequence:
        if type(expected) is dict:
            qom_block(calls[pos:pos + 18], queries(expected), where + f".world {len(worlds)}")
            worlds.append({"first_request_id": calls[pos]["request"]["id"], "snapshot": expected})
            pos += 18
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
    same(len(found), stderr.count(TYPE + ": rejected"), "No malformed/unaccounted SPSS diagnostic")
    return [(op, int(offset, 16), int(size), reason) for op, offset, size, reason in found]


def core_rejections(stderr):
    found = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: ([^\r\n]+)", stderr)
    return [(op, int(address, 16), int(size), region, reason) for op, address, size, region, reason in found]


def expected_rejection(command):
    op = "write" if command[0].startswith("write") else "read"
    width = {"b": 1, "w": 2, "l": 4, "q": 8}[command[0][-1]]
    reason = "disabled-or-invalid-access" if op == "write" else "readback-unimplemented"
    return op, command[1] - BASE, width, reason


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


def source_trace(store, directory, saved, report, scenario):
    state = scenario_world(scenario)
    stderr = command_contract(store, directory, report, scenario != "disabled")
    same(device_rejections(stderr), [], directory + ".no rejected source MMIO")
    same(core_rejections(stderr), [], directory + ".no invalid source MMIO")
    same(store.json(directory + "/source-before-save.json"), state, directory + ".actual stopped QOM")
    same(store.json(directory + "/source-after-save.json"), state, directory + ".actual postmigrate QOM")
    sent = store.json(directory + "/source-query-migrate.json")
    same(sent, saved["source_query_migrate"], directory + ".saved completion response")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    migrate = [i for i, call in enumerate(qc) if operation(call) == "migrate"]
    same(len(migrate), 1, directory + ".one source export")
    at = migrate[0]
    qmp_sequence(qc[:at], ["qmp_capabilities", "cont", state, "stop", "query-status", state],
                 directory + ".source stopped before save")
    same(reply(qc[at - 19]).get("running"), False, directory + ".actual stopped status")
    migration_argument(qc[at], saved["file"])
    reply(qc[at])
    post = [i for i in range(at + 1, len(qc)) if operation(qc[i]) == "query-status"]
    same(len(post), 1, directory + ".one postmigrate status")
    end = post[0]
    completed_polls(qc[at + 1:end], sent, directory)
    qmp_sequence(qc[end:], ["query-status", state], directory + ".complete postmigrate tail")
    same(reply(qc[end]).get("status"), "postmigrate", directory + ".actual postmigrate status")
    index = SCENARIOS.index(scenario)
    epoch, witness = 100000 * (index + 1), 0x53504d00 + index
    expected = []
    if scenario in ("driver", "known-zero"):
        expected.append(("writel", BASE, state["mask-value"]))
    expected += [("clock_set", epoch), ("writel", RAM, witness)]
    same([tokens(call) for call in tc], expected, directory + ".complete source QTest sequence")
    same(qtest_time(tc, directory), epoch, directory + ".source epoch")
    return {"qmp_requests": len(qc), "qtest_commands": len(tc),
            "actual_pure_getters": 54, "source_stop_and_postmigrate_verified": True,
            "virtual_epoch": epoch, "ram_witness": witness,
            "full_source_snapshot_and_after_save_equal": True}


def target_trace(store, directory, saved, report, scenario, migration):
    state, dirty = scenario_world(scenario), world(True, saved["snapshot"]["mask-value"] ^ 0xffffffff, True)
    epoch, witness = saved["source_virtual_ns"], saved["ram_witness"]
    stderr = command_contract(store, directory, report, True, incoming=True)
    same(device_rejections(stderr), [("read", 0, 4, "readback-unimplemented")],
         directory + ".one actual rejected read after restore")
    need(not [r for r in core_rejections(stderr) if r[3] == "(null)"], directory + ": unexpected target hole access")
    pre = store.json(directory + "/destination-before-incoming.json")
    same(pre, {"snapshot": dirty, "device_mmio_count": 1, "qmp_cont_count": 0,
               "qom_get_count": 18, "source_epoch_ns": epoch, "actual_dirty_mask": dirty["mask-value"]},
         directory + ".actual prewarm evidence")
    first = store.json(directory + "/destination-before-first-device-mmio.json")
    expected_first = {
        "snapshot": state, "query_migrate": first["query_migrate"],
        "device_mmio_count_before_incoming": 1, "device_mmio_count_after_first_observation": 1,
        "qmp_cont_count_before_incoming": 0, "qmp_cont_count_after_first_observation": 0,
        "qom_get_count_before_first_observation": 18, "qom_get_count_after_first_observation": 36,
        "first_observation_qom_get_count": 18, "device_mmio_since_incoming": False,
        "qmp_cont_since_incoming": False, "guest_cpu_executed": False,
        "shared_log_timestamp_claimed": False, "ram_witness": witness,
    }
    same(first, expected_first, directory + ".first observation receipt")
    same(migration["first_target_observation"], first, directory + ".report and retained first observation")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    incoming = [i for i, call in enumerate(qc) if operation(call) == "migrate-incoming"]
    same(len(incoming), 1, directory + ".one actual incoming")
    at = incoming[0]
    qmp_sequence(qc[:at], ["qmp_capabilities", dirty], directory + ".prewarm QMP")
    migration_argument(qc[at], saved["file"])
    reply(qc[at])
    post = [i for i in range(at + 1, len(qc)) if operation(qc[i]) == "query-status"]
    same(len(post), 1, directory + ".one incoming status query")
    end = post[0]
    completed_polls(qc[at + 1:end], first["query_migrate"], directory)
    same(reply(qc[end]).get("running"), False, directory + ".target remains stopped")
    qmp_sequence(qc[end:], ["query-status", state, "cont", state, state, state, dirty,
                            "system_reset", world(), world(True, 0, True)],
                 directory + ".first observation and complete restored behavior")
    # The first complete world must precede the only cont in this QMP file.
    first_calls = qc[end + 1:end + 19]
    qom_block(first_calls, queries(state), directory + ".first 18 pure commands/responses")
    same(sum(operation(call) == "cont" for call in qc[:end + 19]), 0, directory + ".no early cont")
    same(sum(operation(call) == "qom-get" for call in qc[:at]), 18, directory + ".actual prewarm getters")
    same(sum(operation(call) == "qom-get" for call in qc[:end + 19]), 36, directory + ".actual first getter total")
    expected_qtest = [
        ("clock_set", epoch), ("writel", BASE, dirty["mask-value"]),
        ("writel", RAM, witness ^ 0xffffffff), ("clock_set", epoch), ("readl", RAM),
        ("clock_step", 10000), ("readl", BASE), ("writel", BASE, dirty["mask-value"]),
        ("writel", BASE, 0),
    ]
    same([tokens(call) for call in tc], expected_qtest, directory + ".entire target QTest trace")
    same(qtest_time(tc, directory), epoch + 10000, directory + ".resumed virtual epoch")
    same(sum(is_mmio(call) for call in tc[:4]), 1, directory + ".actual prewarm MMIO")
    same(sum(is_mmio(call) for call in tc[4:5]), 0, directory + ".only RAM drain after incoming")
    same(int(tc[4]["reply"].split()[1], 0), witness, directory + ".actual restored RAM witness")
    same(wire(first["snapshot"]), wire(saved["snapshot"]), directory + ".all 25 restored bytes represented")
    return {"first_getter_request_id": first_calls[0]["request"]["id"],
            "last_first_getter_request_id": first_calls[-1]["request"]["id"],
            "first_pure_getters": 18, "actual_qom_getters_total": 144,
            "prewarm_actual_device_mmio": 1, "first_device_mmio_delta": 0, "first_cont_delta": 0,
            "actual_ram_witness": witness, "qmp_requests": len(qc), "qtest_commands": len(tc),
            "source_snapshot_restored_before_cont": True,
            "post_restore_read_rejected_reset_unknown_and_written_zero_verified": True,
            "shared_timestamp_used": False,
            "cross_file_order_basis": "Pinned producer sequence plus actual before/after counters and independent per-file command/response order; no synthetic global timestamp."}


def rejection_trace(store, directory, row, report, filename, epoch, diagnostic, profile):
    stderr = command_contract(store, directory, report, profile, incoming=True)
    full = TYPE + ": " + diagnostic
    fields_equal(row, {"destination_exit": 1, "expected_diagnostic": full,
                      "specific_load_failure_observed": True,
                      "target_directory": PurePosixPath(directory).name,
                      "device_mmio_commands": 0, "cont_commands": 0}, directory)
    need(row["command_error"] is None or type(row["command_error"]) is str, directory + ".command error receipt")
    for literal in (full, "load of migration failed", "error while loading state"):
        need(literal in stderr, directory + ": missing actual stderr " + literal)
    same(device_rejections(stderr), [], directory + ".no rejected MMIO before migration failure")
    same(core_rejections(stderr), [], directory + ".no MMIO holes before migration failure")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    same([operation(call) for call in qc], ["qmp_capabilities", "migrate-incoming"],
         directory + ".complete rejection QMP sequence")
    reply(qc[0])
    migration_argument(qc[1], filename)
    same([tokens(call) for call in tc], [("clock_set", epoch)], directory + ".clock only, no MMIO")
    same(qtest_time(tc, directory), epoch, directory + ".rejection epoch")
    return {"actual_stderr_diagnostic": full, "destination_exit": 1,
            "process_exit_evidence": "Reviewed producer process.wait receipt, backed by actual fatal load stderr; process was not re-executed by auditor.",
            "incoming_file": filename, "device_mmio_commands": 0, "cont_commands": 0,
            "query_migrate_failed_response_required": False}


def original_stream(store, path, saved, state):
    result = scan_stream(store, path)
    fields_equal(saved, {"bytes": result["bytes"], "sha256": result["sha256"],
                         "stream_modified": False, "source_status": "completed"}, path)
    same(result["payload"], wire(state), path + ".all independently encoded 25 bytes")
    section = dict(result["section"])
    section.update(payload_file=PurePosixPath(path).name + ".spss-mask-v1.payload.bin",
                   payload_sha256=sha(result["payload"]), all_bytes_match_stopped_source_qom=True)
    expected = {"spss_irq_mask": section}
    same(saved["sections"], expected, path + ".producer section metadata versus independent scan")
    same(store.json(path + ".sections.json"), expected, path + ".actual section sidecar")
    same(store.read(path + ".spss-mask-v1.payload.bin"), result["payload"], path + ".complete retained payload")
    return result


def audit_migrations(store, data, report, receipt):
    expected_names = ["spss-mask-" + name + ".migration" for name in SCENARIOS]
    expected_names += ["spss-mask-corrupt-" + item[0] + ".migration" for item in MUTATIONS]
    actual_names = sorted(PurePosixPath(path).name for path in store.entries
                          if path.startswith(data + "/") and "/" not in path[len(data) + 1:]
                          and path.endswith(".migration"))
    same(actual_names, sorted(expected_names), "Exactly 4 original and 6 mutant actual streams")
    same([row["name"] for row in report["actual_stream_files"]], sorted(expected_names), "Declared complete stream list")
    actual_index = {row["name"]: row for row in report["actual_stream_files"]}
    originals, saved_by_name = {}, {}
    receipt["original_streams"] = []
    for index, (scenario, saved) in enumerate(zip(SCENARIOS, report["migration_sources"])):
        filename, state = "spss-mask-" + scenario + ".migration", scenario_world(scenario)
        fields_equal(saved, {"file": filename, "source_directory": "migration-" + scenario + "-source",
                             "source_virtual_ns": (index + 1) * 100000, "ram_witness": 0x53504d00 + index,
                             "snapshot": state}, scenario)
        result = original_stream(store, data + "/" + filename, saved, state)
        same(actual_index[filename], {"name": filename, "bytes": result["bytes"], "sha256": result["sha256"]},
             scenario + ".actual stream inventory")
        trace = source_trace(store, data + "/" + saved["source_directory"], saved, report, scenario)
        receipt["original_streams"].append({"name": filename, "bytes": result["bytes"], "sha256": result["sha256"],
                                           "section": result["section"], "payload_hex": result["payload"].hex(),
                                           "source_qom": state, "trace": trace})
        originals[scenario], saved_by_name[scenario] = result, saved
    # This difference is the provenance bit, not a fabricated readable reset value.
    same(originals["unknown"]["payload"][:-1], originals["known-zero"]["payload"][:-1],
         "Unknown and known zero have same first 24 bytes")
    same((originals["unknown"]["payload"][-1], originals["known-zero"]["payload"][-1]), (0, 1),
         "Unknown versus explicitly programmed zero persists in byte 24")
    receipt["completed_migrations"] = []
    for scenario, row in zip(SCENARIOS[:3], report["migrations"]):
        saved = saved_by_name[scenario]
        name = "migration_" + scenario.replace("-", "_")
        fields_equal(row, {"name": name, "passed": True, "source_status": "completed", "target_status": "completed",
                           "snapshot_file": saved["file"], "snapshot_sha256": saved["sha256"], "stream_modified": False,
                           "source_directory": saved["source_directory"], "target_directory": "migration-" + scenario + "-target",
                           "continued_after_restore": True, "all_25_payload_bytes_verified": True,
                           "post_restore_replacement": saved["snapshot"]["mask-value"] ^ 0xffffffff,
                           "post_restore_reset_and_known_zero": True}, name)
        trace = target_trace(store, data + "/" + row["target_directory"], saved, report, scenario, row)
        receipt["completed_migrations"].append({"name": name, **trace})
    receipt["profile_rejections"] = []
    for row, (name, scenario, profile) in zip(report["rejected_migrations"],
                                            (("on_to_off", "driver", False), ("off_to_on", "disabled", True))):
        saved = saved_by_name[scenario]
        fields_equal(row, {"name": name, "passed": True, "snapshot_file": saved["file"],
                           "snapshot_sha256": saved["sha256"], "stream_modified": False}, name)
        trace = rejection_trace(store, data + "/reject-profile-" + name, row, report, saved["file"],
                                saved["source_virtual_ns"], "migrated profile mismatch", profile)
        receipt["profile_rejections"].append({"name": name, **trace})
    receipt["one_bit_rejections"] = []
    for row, (name, scenario, offset, width, old, xor, profile, diagnostic) in zip(report["corrupted_stream_rejections"], MUTATIONS):
        saved, original = saved_by_name[scenario], originals[scenario]
        filename = "spss-mask-corrupt-" + name + ".migration"
        result = scan_stream(store, data + "/" + filename)
        same(result["section"], original["section"], name + ".unchanged complete section framing")
        same(int.from_bytes(original["payload"][offset:offset + width], "big"), old, name + ".actual pre-mutation value")
        changed_payload = bytearray(original["payload"])
        changed_payload[offset:offset + width] = (old ^ xor).to_bytes(width, "big")
        same(result["payload"], bytes(changed_payload), name + ".entire mutated payload")
        absolute = original["section"]["payload_offset"] + offset + width - 1
        difference = one_bit_difference(store, data + "/" + saved["file"], data + "/" + filename,
                                        absolute, xor, original["sha256"], result["sha256"])
        fields_equal(row, {"name": name, "passed": True, "source_file": saved["file"], "source_sha256": original["sha256"],
                           "snapshot_file": filename, "snapshot_sha256": result["sha256"], "stream_modified": True,
                           "field_offset": offset, "field_bytes": width, "old_value": old, "xor": xor,
                           "changed_byte_offsets": [absolute], "changed_bits": 1,
                           "destination_reference_enabled": profile, "original_stream_unchanged": True}, name)
        same(actual_index[filename], {"name": filename, "bytes": result["bytes"], "sha256": result["sha256"]},
             name + ".actual mutant inventory")
        trace = rejection_trace(store, data + "/reject-corrupt-" + name, row, report, filename,
                                saved["source_virtual_ns"], diagnostic, profile)
        receipt["one_bit_rejections"].append({"name": name, "bytes": result["bytes"], "sha256": result["sha256"],
                                             "original": saved["file"], "payload_hex": result["payload"].hex(),
                                             "whole_stream_comparison": difference, **trace})


def probe_receipt(command):
    op, offset, size, reason = expected_rejection(command)
    text = command[0] + " " + hex(command[1])
    if op == "write":
        text += " " + hex(command[2])
    return {"command": text, "callback_offset": offset, "callback_size": size,
            "specific_diagnostic": f"{TYPE}: rejected {op} offset={offset:#x} size {size} ({reason})",
            "state_unchanged": True}


def audit_ordinary(store, data, report, receipt):
    checks = {row["name"]: row for row in report["checks"]}
    cold, driver, zero = world(), world(True, DRIVER_MASK, True), world(True, 0, True)
    results = []
    for index, name in enumerate(ORDINARY):
        directory = data + f"/ordinary-{index:02d}-" + name
        stderr = command_contract(store, directory, report, True)
        qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
        sequence = ["qmp_capabilities", "cont"]
        commands, rejected, holes, detail = [], [], [], {}
        if index == 0:
            sequence += [cold, ("list",), cold]
            detail = {"actual_pure_getters": 18, "mask_zero_is_unknown": True,
                      "exact_qom_types": {field: "uint64" if field in FIELDS[:11] else "bool" for field in FIELDS}}
        elif index == 1:
            # Independently derive the real probe word from the two allowed IRQ bits.
            same(0xffffffff & ~(1 << 24) & ~(1 << 25), DRIVER_MASK, "Literal probe formula")
            commands = [("writel", BASE, DRIVER_MASK)]
            sequence += [driver]
            detail = {"physical_address": BASE, "actual_guest_word": DRIVER_MASK,
                      "original_bit_positions": [24, 25], "modeled_remote_events": 0}
        elif index == 2:
            patterns = [0xffffffff, 0, 0x80000000, 1, 0x03000000, DRIVER_MASK,
                        0x55555555, 0xaaaaaaaa, 0x01020304, 0x04030201]
            commands = [("writel", BASE, value) for value in patterns]
            sequence += [world(True, value, True) for value in patterns]
            detail = {"full_u32_patterns": patterns, "replacement_not_or_accumulation": True}
        elif index == 3:
            commands = [("writel", BASE, 0), ("writel", BASE, 0), ("writel", BASE, DRIVER_MASK),
                        ("clock_step", 10000), ("writel", BASE, 0)]
            sequence += [cold, zero, zero, driver, "system_reset", cold, cold, zero, "system_reset", cold]
            detail = {"programmed_zero_distinct": True, "reset_clears_value_and_provenance": True,
                      "hardware_reset_value_claimed": False}
        elif index == 4:
            rejected = [("readb", BASE + offset) for offset in (0, 1, 2, 3)]
            rejected += [("readw", BASE + offset) for offset in (0, 1, 2)]
            rejected += [("readl", BASE)]
            commands = [("writel", BASE, DRIVER_MASK)] + rejected
            sequence += [driver] * 17
            detail = {"rejected_read_probes": [probe_receipt(cmd) for cmd in rejected],
                      "read_error_numeric_value_used_as_oracle": False}
        elif index == 5:
            rejected = [("writeb", BASE + offset, 0xff) for offset in (0, 3)]
            rejected += [("writew", BASE + offset, 0xffff) for offset in (0, 1, 2)]
            rejected += [("writel", BASE + 1, 0xffffffff), ("writeq", BASE, 0xffffffffffffffff), ("readq", BASE)]
            commands = [("writel", BASE, DRIVER_MASK)] + rejected + [("write", BASE, 5, 0x04030201ff)]
            sequence += [driver] * 18 + [world(True, 0x01020304, True)]
            holes = [("write", BASE + 4, 1, "(null)", "rejected")]
            detail = {"rejected_scalar_probes": [probe_receipt(cmd) for cmd in rejected],
                      "typed_eight_byte_callback_rejected_without_hole": True,
                      "buffered_write_command": "write 0x1881028 5 0x04030201ff",
                      "buffered_write_prefix_value": 0x01020304, "buffered_write_hole_address": BASE + 4,
                      "buffered_write_hole_size": 1, "buffered_write_valid_prefix_committed": True,
                      "whole_buffer_atomicity_claimed": False}
        elif index == 6:
            tree = store.text(directory + "/mtree.txt")
            pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + re.escape(TYPE) + r"\.[^\s]+)(?:\s|$)"
            leaves = {(int(start, 16), int(end, 16), leaf) for start, end, leaf in re.findall(pattern, tree)}
            same(leaves, {(BASE, BASE + 3, TYPE + ".mask")}, directory + ".exact actual sparse leaf")
            addresses = (0x0188101c, 0x01881024, 0x0188102c, 0x0188103c, 0x0188200c, 0x01882010, 0x01882014)
            commands = [("writel", BASE, DRIVER_MASK)]
            for address in addresses:
                commands += [("readl", address), ("writel", address, 0xa55a5aa5)]
                holes += [(op, address, 4, "(null)", "rejected") for op in ("read", "write")]
            sequence += [("hmp", tree)] + [driver] * 8
            detail = {"exact_leaves": 1, "mapped_bytes": 4, "true_holes": list(addresses),
                      "neighboring_source_resources_not_added": True}
        elif index == 7:
            state = world(True, 0x89abcdef, True)
            commands = [("writel", BASE, 0x89abcdef), ("clock_step", 1000000)]
            sequence += [state]
            sequence += [("set-error", field, not state[field] if type(state[field]) is bool else state[field] ^ 1)
                         for field in FIELDS]
            sequence += [state, state]
            detail = {"readonly_properties_checked": list(FIELDS), "timer_or_event_progress_invented": False}
        same([tokens(call) for call in tc], commands, directory + ".complete actual QTest sequence")
        qtest_time(tc, directory)
        worlds = qmp_sequence(qc, sequence, directory)
        same(device_rejections(stderr), [expected_rejection(cmd) for cmd in rejected],
             directory + ".complete actual device rejection sequence")
        actual_core = core_rejections(stderr)
        same([item for item in actual_core if item[3] == "(null)"], holes, directory + ".actual hole failures")
        # Core log entries at the delivered callback may accompany MEMTX_ERROR;
        # their presence is not used as a fabricated readable value or rollback.
        for op, address, size, region, reason in actual_core:
            if region != "(null)":
                need(region == TYPE + ".mask" and BASE <= address < BASE + 4 and reason == "rejected",
                     directory + ": unexpected invalid-access region")
                need(any((x[0], BASE + x[1], x[2]) == (op, address, size)
                         for x in [expected_rejection(cmd) for cmd in rejected]),
                     directory + ": unaccounted core callback rejection")
        fields_equal(checks[name], {"passed": True, "fixture": PurePosixPath(directory).name, **detail},
                     directory + ".reported details versus actual evidence")
        results.append({"name": name, "actual_qom_snapshots": len(worlds), "actual_pure_getters": len(worlds) * 18,
                        "actual_qtest_commands": len(tc), "actual_device_rejections": len(rejected),
                        "actual_hole_rejections": len(holes), "complete_command_response_sequences_checked": True})
    off = world(False)
    directory = data + "/default-off"
    stderr = command_contract(store, directory, report, False, default=True)
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    rejected = [("readl", BASE)] + [("writel", BASE, value) for value in (0, DRIVER_MASK, 0xffffffff)]
    same([tokens(call) for call in tc], rejected + [("clock_step", 10000)], directory + ".complete QTest trace")
    qtest_time(tc, directory)
    qmp_sequence(qc, ["qmp_capabilities", "cont"] + [off] * 9 + ["system_reset", off], directory)
    same(device_rejections(stderr), [expected_rejection(cmd) for cmd in rejected], directory + ".default-off errors")
    same([item for item in core_rejections(stderr) if item[3] == "(null)"], [], directory + ".no unintended hole")
    fields_equal(checks["default_off_rejects_control_without_programming"],
                 {"default_profile_used": True, "zero_and_nonzero_writes_rejected": True,
                  "programmed": False, "mask_known": False}, directory)
    results.append({"name": "default_off_rejects_control_without_programming", "actual_pure_getters": 180,
                    "actual_rejections": 4, "no_global_profile_override": True})
    directory = data + "/paused-tcg"
    stderr = command_contract(store, directory, report, True, accel="tcg")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    same(tc, [], directory + ".no QTest command")
    qmp_sequence(qc, ["qmp_capabilities", "query-status", cold, ("list",),
                     ("set-error", "qtest-mask-value", 1), ("set-error", "mask-value", 1),
                     ("set-error", "programmed", True), cold, "query-status"], directory)
    statuses = [reply(call) for call in qc if operation(call) == "query-status"]
    same([status["running"] for status in statuses], [False, False], directory + ".actual paused TCG statuses")
    same(device_rejections(stderr), [], directory + ".no guest MMIO")
    same(core_rejections(stderr), [], directory + ".no guest invalid access")
    fields_equal(checks["paused_tcg_exposes_no_mutating_fixture_or_guest_execution"],
                 {"fixture_override_absent": True, "guest_cpu_executed": False}, directory)
    results.append({"name": "paused_tcg_exposes_no_mutating_fixture_or_guest_execution",
                    "actual_pure_getters": 36, "actual_status_running": [False, False],
                    "qtest_commands": 0, "cont_commands": 0})
    receipt["ordinary_and_special"] = results


def report_contract(report, qemu_sha):
    expected = {
        "schema": "tb371fc-spss-irq-mask-sm8250-v1", "suite": CASE, "expected_checks": 21,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 6, "expected_unmodified_streams": 4, "expected_stream_files": 10,
        "vmstate_version": 1, "vmstate_minimum_version": 1, "vmstate_payload_bytes": 25,
        "reference_identity": IDENTITY, "physical_base": BASE, "register_count": 1,
        "sparse_window_count": 1, "mapped_bytes": 4, "first_observation_qom_get_count": 18,
        "irq_output_count": 0, "input_clock_count": 0, "output_clock_count": 0, "timer_count": 0,
        "reference_enabled_by_default": False, "readback_supported": False, "ready_modeled": False,
        "firmware_modeled": False, "pas_modeled": False, "hardware_reset_value_known": False,
        "programmed_zero_distinct_from_unknown": True, "source_probe_mask": DRIVER_MASK,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False, "android_boot_passed": False,
        "binary_unchanged": True, "passed": True, "machine": DEFAULT_MACHINE,
        "source_commit": PRIMARY, "test_script_sha256": TEST_SHA,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "qemu_sha256_before": qemu_sha, "qemu_sha256_after": qemu_sha,
    }
    fields_equal(report, expected, "Producer metadata and pinned boundaries")
    need(not any(key in report for key in ("error", "traceback")), "Producer recorded a failure")
    for key, length in LENGTHS.items():
        need(type(report[key]) is list, "Producer array required: " + key)
        same(len(report[key]), length, "Producer exact array length: " + key)
    names = list(ORDINARY) + ["default_off_rejects_control_without_programming",
                             "paused_tcg_exposes_no_mutating_fixture_or_guest_execution"]
    names += ["migration_unknown", "migration_driver", "migration_known_zero",
              "profile_on_to_off_rejected", "profile_off_to_on_rejected"]
    names += ["single_bit_" + mutation[0] + "_rejected" for mutation in MUTATIONS]
    same([check["name"] for check in report["checks"]], names, "Exact 21 named checks in executed order")
    for check in report["checks"]:
        same(check.get("passed"), True, "Producer check " + check["name"])
    for key in ("qemu", "work_dir"):
        need(type(report[key]) is str and PurePosixPath(report[key]).is_absolute(), "Absolute " + key + " path required")


def select_case(store, args, receipt, qemu_sha):
    candidates = [name for name in store.entries
                  if re.search(r"(?:^|/)components/[0-9]+-spss-irq-mask-sm8250-reference/case\.json$", name)]
    same(len(candidates), 1, "Exactly one compiled SPSS component case")
    case_path = candidates[0]
    root = case_path.rsplit("/", 1)[0]
    case, report = store.json(case_path), store.json(root + "/fixtures/data/result.json")
    fields_equal(case, {"name": CASE, "passed": True, "exit_code": 0}, "Actual compiled component")
    need(not any(key in case for key in ("error", "artifact_error", "timed_out")), "Incomplete or failed case")
    need(type(case.get("completed_utc")) is str and case["completed_utc"], "Actual completion timestamp required")
    report_contract(report, qemu_sha)
    for filename in ("stdout.log", "stderr.log"):
        raw = store.read(root + "/" + filename)
        same(case["log_sha256"][filename], sha(raw), "Actual harness log hash: " + filename)
        if filename == "stdout.log":
            same(json_value(raw, filename), report, "Actual producer stdout equals retained result")
    verified = case["verified"]
    fields_equal(verified, {"verdict": True, "verdict_field": "passed", "check_count": 21,
                           "verified_lengths": LENGTHS}, "CI strict case contract")
    command = case["command"]
    need(type(command) is list and len(command) >= 6 and all(type(s) is str for s in command),
         "Actual harness argv required")
    need("-O" not in command and "-OO" not in command and "-c" not in command, "Assertions must be enabled")
    same(PurePosixPath(command[1]).name, "test_spss_irq_mask_sm8250.py", "Actual producer script")
    for option, value in (("--qemu", report["qemu"]), ("--out", report["work_dir"])):
        same(command.count(option), 1, "Single harness option " + option)
        same(command[command.index(option) + 1], value, "Actual harness value " + option)
    same(PurePosixPath(report["work_dir"]), PurePosixPath(case["fixture"]) / "data", "Actual fixture directory")
    prefix = root.split("/components/", 1)[0]
    head_path = prefix + "/source-commit.txt"
    same(store.text(head_path).strip(), args.expected_head, "Actual archived QEMU source head")
    receipt["component"] = {"case": case_path, "report": root + "/fixtures/data/result.json",
                            "source_commit_file": head_path, "head": args.expected_head,
                            "producer_test_sha256": TEST_SHA, "exit_code": 0, "checks": 21}
    return root + "/fixtures/data", report


def static_self_check():
    same(wire(world(True, DRIVER_MASK, True)).hex(),
         "0000000153504d31000000000188102800000004fcffffff01", "Literal independent 25-byte wire vector")
    same(wire(world()).hex(), "0000000153504d310000000001881028000000040000000000", "Unknown vector")
    same(wire(world(True, 0, True)).hex(), "0000000153504d310000000001881028000000040000000001", "Known-zero vector")
    same(wire(world(False)).hex(), "0000000053504d310000000001881028000000040000000000", "Disabled vector")
    same(len(FIELDS), 18, "Literal first QOM count")
    same(len(set(FIELDS)), 18, "Unique literal QOM fields")
    same(sum(LENGTHS[key] for key in ("migrations", "rejected_migrations", "corrupted_stream_rejections")), 11,
         "Migration-related cases")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expected-qemu-sha256")
    parser.add_argument("--test-source", type=Path, default=Path("/dev/shm/pil-tz-candidate-6a5/tb371fc/tests/test_spss_irq_mask_sm8250.py"))
    parser.add_argument("--model-source", type=Path, default=Path("/dev/shm/pil-tz-candidate-6a5/hw/misc/qcom-sm8250-spss-irq-mask.c"))
    args = parser.parse_args()
    if args.out.exists():
        raise SystemExit("Refusing to overwrite existing audit evidence: " + str(args.out))
    receipt = {
        "schema": "tb371fc-spss-irq-mask-independent-artifact-audit-v1", "passed": False,
        "observed_utc": datetime.now(timezone.utc).isoformat(), "auditor_sha256": file_hash(__file__),
        "expected_head": args.expected_head, "expected_zip_sha256": args.expected_zip_sha256,
        "read_only_archive": True, "qemu_executed": False, "guest_executed": False,
        "extracted_members": [], "producer_serializer_imported": False, "model_imported": False,
        "whole_workflow_verdict_claimed": False, "native_android_passed": False,
        "shared_log_timestamp_claimed": False,
        "scope": "Actual compiled SPSS component, 21 groups, four original and six complete one-bit streams, full 25-byte wire and raw QMP/QTest protocol. Aggregate workflow and source-generation audits are separate.",
        "protocol_limit": "Separate QMP/QTest JSONL files have no shared timestamp. This audit combines exact per-file order, actual counter receipts, restored RAM witness and pinned producer order; it does not invent a cross-file total order.",
        "mmio_limit": "Only delivered scalar callbacks are rejected atomically. A raw five-byte write commits its legal four-byte prefix before the final byte reaches the unmapped hole.",
    }
    store = None
    try:
        static_self_check()
        same(args.expected_head, HEAD, "Auditor bound exact reviewed QEMU head")
        for value, length, name in ((args.expected_head, 40, "head"), (args.expected_zip_sha256, 64, "ZIP SHA256")):
            need(re.fullmatch("[0-9a-f]{" + str(length) + "}", value) is not None, "Malformed " + name)
        if args.expected_qemu_sha256 is not None:
            need(re.fullmatch("[0-9a-f]{64}", args.expected_qemu_sha256) is not None, "Malformed QEMU SHA256")
        same(file_hash(args.test_source), TEST_SHA, "Exact reviewed producer source bytes")
        same(file_hash(args.model_source), MODEL_SHA, "Exact reviewed model source bytes")
        compile(args.test_source.read_bytes(), str(args.test_source), "exec")
        receipt["reviewed_source_pins"] = {
            "test": {"path": str(args.test_source.resolve()), "bytes": args.test_source.stat().st_size, "sha256": TEST_SHA},
            "model": {"path": str(args.model_source.resolve()), "bytes": args.model_source.stat().st_size, "sha256": MODEL_SHA},
            "vendor_reference_commit": PRIMARY,
        }
        same(file_hash(args.zip), args.expected_zip_sha256, "Actual complete ZIP SHA256")
        receipt["archive"] = {"path": str(args.zip.resolve()), "bytes": args.zip.stat().st_size,
                              "sha256": args.expected_zip_sha256}
        store = Store(zip_path=args.zip)
        binary = "qemu-build/qemu-system-aarch64"
        qemu = hash_member(store, binary)
        if args.expected_qemu_sha256 is not None:
            same(qemu["sha256"], args.expected_qemu_sha256, "Externally pinned actual QEMU SHA256")
        with store.open(binary) as source:
            header = source.read(20)
        same(header[:6], b"\x7fELF\x02\x01", "Actual QEMU ELF64 little endian")
        need(len(header) == 20, "Truncated actual QEMU ELF")
        receipt["qemu"] = {"member": binary, **qemu, "executed": False,
                           "host_elf_machine": int.from_bytes(header[18:20], "little"),
                           "externally_pinned": args.expected_qemu_sha256 is not None}
        data, report = select_case(store, args, receipt, qemu["sha256"])
        audit_ordinary(store, data, report, receipt)
        audit_migrations(store, data, report, receipt)
        receipt["verified_members"] = dict(sorted(store.observed.items()))
        receipt["verified_member_count"] = len(store.observed)
        store.finish()
        store = None
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
                      "receipt_sha256": file_hash(args.out), "verified_member_count": receipt.get("verified_member_count", 0),
                      "error": receipt.get("error")}))
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
