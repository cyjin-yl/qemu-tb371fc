#!/usr/bin/env python3
"""Independent LLCC ZIP audit; no QEMU, guest, extraction, test import or model import.

Every 783-byte LLCC payload is built from a separately specified offset map.
QMP/QTest protocols, all one-bit stream differences and exact source pins are
checked against actual archive members. Cross-file ordering uses retained
counts plus the pinned harness order; no shared log timestamps are invented.
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

CHUNK, MAX_SMALL = 1024*1024, 64*1024*1024
AUDIT_SCHEMA = "tb371fc-llcc-independent-artifact-audit-v1"
TEST_SHA = "4472734a675049a4a0f18e4699617ceecde6b293e6b8ede7f4a0825f403d48e3"
MODEL_SHA = "1312474d66959cc70188a76769765014779b999602b8885f3c9658927d563f04"
TYPE, DEVICE = "qcom-sm8250-llcc", "/machine/llcc"
BASE, BCAST, RAM = 0x09200000, 0x09600000, 0x80010000
PAYLOAD, GETTERS = 783, 313
PRIMARY = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
UPSTREAM = "adc218676eef25575469234709c2d87185ca223a"

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

def encode_at(raw, offset, fmt, values, where, bools=False):
    """Independent explicit offset placement, never a copy of suite serializer."""
    widths = {"I": 32, "Q": 64, "q": 64, "B": 8}
    vals = []
    for i, value in enumerate(values):
        if bools:
            vals.append(int(boolean(value, f"{where}[{i}]")))
        else:
            vals.append(uint(value, widths[fmt], f"{where}[{i}]", signed=fmt == "q"))
    struct.pack_into(">" + str(len(vals)) + fmt, raw, offset, *vals)

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


HEAD = "3bd583f2079634d01c12160ab3e3f0c5a3e47e86"
CAD, PCB, WRSC = 0x21f00, 0x21f04, 0x21f20
COMMON, HWINFO, TRP, DRP = 0x3000c, 0x30000, 0x20480, 0x41000
# Independent raw SCT inputs: usecase, SID, KiB, priority, fixed, bonus,
# reserved, mode, probe, disable-allocation, write-cache, retention, init.
SCT = (
    (1,1,3072,1,1,4095,0,0,0,0,0,1,1), (2,2,512,3,1,4095,0,0,0,0,0,1,0),
    (6,6,1024,1,0,4095,0,0,0,0,0,0,0), (10,10,1024,1,0,4095,0,0,0,0,0,0,0),
    (11,11,1024,1,1,4095,0,0,0,0,0,1,0), (12,12,1024,1,0,4095,0,0,0,0,1,1,0),
    (13,13,1024,1,1,4095,0,0,0,0,0,0,1), (15,15,1024,1,0,4095,0,0,0,0,0,1,0),
    (16,16,3072,1,1,4095,0,0,0,0,0,1,0), (22,22,1024,1,1,4095,0,0,0,0,0,1,0),
    (23,23,3072,1,1,4095,0,0,0,0,0,1,0), (24,24,1024,1,0,4095,0,0,0,0,0,1,0),
    (28,28,256,3,1,4095,0,0,0,0,0,1,0), (30,30,128,3,0,0,3,1,0,0,0,1,0),
    (31,31,256,1,1,4095,0,0,0,0,0,0,1), (32,17,512,1,0,4095,0,0,0,0,0,1,0),
)
SIDS = tuple(row[1] for row in SCT)
SID_MASK = sum(1 << sid for sid in SIDS)
ATTRS = {row[1]: ((row[6] & 0xfff) | (row[5] << 16),
          ((row[2]*1024//4//64) << 16) | row[7] | (row[8] << 2) |
          (row[4] << 3) | (row[3] << 4)) for row in SCT}
FLAG_BITS = {row[1]: row[10] | (row[9] << 1) | (row[11] << 2) for row in SCT}
REGS = tuple(sorted([v for sid in SIDS for v in
                    (sid*4096,sid*4096+4,0x21000+sid*8,0x21004+sid*8)] +
                   [CAD,PCB,WRSC,HWINFO,COMMON,TRP,DRP]))
KEYS = tuple(f"{offset:05x}" for offset in REGS)
GLOBALS = ("enabled-profile","reference-identity","bank-count","slice-count",
           "register-count","sparse-window-count","mapped-bytes","region-count",
           "bank-stride","bank-resource-bytes","transition-delay-ns",
           "reference-common-status","reference-hardware-info")
GLOBAL_VALUES = (1,0x4c4c4332,4,16,71,139,1396,2,0x80000,0x50000,1000,0x40000000,0)
FIELDS = ("active","state-known","recipe-valid","status","phase","captured-attr0",
          "captured-attr1","captured-flags","deadline-ns")
SCOPE = ("enabled-reference","hardware-version-known","memory-controller-count-known",
         "cache-data-modelled","performance-counters-modelled","ecc-error-injection-modelled",
         "bank-count-known","mapped-word-count","timer-count","vmstate-bytes")
SCENARIOS = ("steady","armed","activate-pending","deactivate-pending","disabled")
COMPLETED = ("migration_steady_control_state","migration_armed_edge",
             "migration_pending_activation","migration_pending_deactivation")
CORRUPT = ("physical_base","captured_attr","written","pending_deadline")
ORDINARY = (
    "cold_geometry_identity_and_pure_getters",
    "original_kona_probe_order_and_all_bank_mirrors",
    "all_sixteen_slices_activate_and_deactivate",
    "trigger_high_is_armed_until_matching_falling_edge",
    "deactivation_preserves_active_until_deadline",
    "explicit_zero_write_credit_is_required",
    "incomplete_or_wrong_slice_recipe_cannot_activate",
    "related_configuration_change_cancels_armed_and_pending",
    "unrelated_slice_and_other_global_bits_preserve_transaction",
    "own_global_bit_change_cancels_but_restore_does_not_restart",
    "wrong_or_duplicate_falling_edges_cancel_without_replay",
    "concurrent_slice_deadlines_are_independent",
    "all_bank_words_are_readonly_views",
    "dynamic_status_and_reserved_bits_are_not_writable",
    "exact_sparse_ownership_and_true_holes",
    "subword_unaligned_and_wide_accesses_reject_atomically",
    "polling_and_qom_reads_do_not_advance_time",
    "active_state_survives_bad_configuration_and_can_deactivate",
    "reset_clears_armed_pending_active_and_all_programming",
    "unknown_version_mc_and_observers_remain_readonly",
)
CHECK_NAMES = ORDINARY + (
    "default_off_rejects_every_physical_word",
    "paused_tcg_exposes_no_mutating_fixture_or_guest_execution",
) + COMPLETED + ("profile_on_to_off_rejected","profile_off_to_on_rejected") + tuple(
    "single_bit_"+name+"_rejected" for name in CORRUPT)


def mask(offset):
    if offset in (CAD, PCB, WRSC):
        return SID_MASK
    for sid in SIDS:
        if offset == sid*4096:
            return 7
        if offset == 0x21000+sid*8:
            return 0x0fff0fff
        if offset == 0x21004+sid*8:
            return 0xffff003d
    return 0


def flags(registers, sid):
    return ((registers[f"{WRSC:05x}"] >> sid) & 1) | \
           (((registers[f"{CAD:05x}"] >> sid) & 1) << 1) | \
           (((registers[f"{PCB:05x}"] >> sid) & 1) << 2)


def recipe(world, sid):
    regs, written = world["registers"], world["written"]
    a, b = f"{0x21000+sid*8:05x}", f"{0x21004+sid*8:05x}"
    return bool(world["globals"]["enabled-profile"] and
                all(written[f"{o:05x}"] for o in (0x21000+sid*8,0x21004+sid*8,CAD,PCB,WRSC)) and
                (regs[a],regs[b]) == ATTRS[sid] and flags(regs,sid) == FLAG_BITS[sid])


def check_world(world, profile, where, now=None):
    same(set(world), {"globals","region_bases","region_bytes","registers","written","slices","scope"}, where+" keys")
    expected_globals = dict(zip(GLOBALS,(int(profile),)+GLOBAL_VALUES[1:]))
    same(world["globals"],expected_globals,where+" metadata")
    same(world["region_bases"],[BASE,BCAST],where+" bases")
    same(world["region_bytes"],[0x1d0000,0x50000],where+" resources")
    same(set(world["registers"]),set(KEYS),where+" registers")
    same(set(world["written"]),set(KEYS),where+" written")
    same(set(world["slices"]),set(map(str,SIDS)),where+" SIDs")
    same(world["scope"],dict(zip(SCOPE,[bool(profile),False,False,False,False,False,
                                     bool(profile),349,1,783])),where+" honest unknown scope")
    for offset,key in zip(REGS,KEYS):
        value = uint(world["registers"][key],32,where+" reg "+key)
        credit = boolean(world["written"][key],where+" written "+key)
        need(not value & ~mask(offset),where+": unsupported register bits "+key)
        control = offset in [sid*4096 for sid in SIDS]
        if control:
            need(value in (0,2,3,4,5),where+": illegal control")
        if not mask(offset) or control:
            same(credit,False,where+": non-programming word credit")
        elif value:
            same(credit,True,where+": nonzero register needs credit")
        if not profile:
            same((value,credit),(0,False),where+": disabled register state")
    for sid in SIDS:
        state = world["slices"][str(sid)]
        same(set(state),set(FIELDS),where+" slice fields")
        active = boolean(state["active"],where+" active")
        phase = uint(state["phase"],8,where+" phase")
        need(phase <= 4,where+": phase domain")
        same(state["state-known"],bool(profile),where+" state known")
        same(state["recipe-valid"],recipe(world,sid),where+" independent recipe")
        same(state["status"],(1 if active else 2) if profile else 3,where+" one-hot status")
        deadline = uint(state["deadline-ns"],64,where+" deadline",signed=True)
        if active:
            need(all(world["written"][f"{o:05x}"] for o in
                     (0x21000+sid*8,0x21004+sid*8,CAD,PCB,WRSC)),where+": active without historical programming")
        if not profile:
            same((active,phase),(False,0),where+": disabled state")
        captured = [uint(state[n],32,where+" "+n) for n in
                    ("captured-attr0","captured-attr1","captured-flags")]
        if phase == 0:
            same(captured,[0,0,0],where+" idle capture")
            same(deadline,-1,where+" idle deadline")
        else:
            regs = world["registers"]
            command = {1:3,2:5,3:2,4:4}[phase]
            same(regs[f"{sid*4096:05x}"],command,where+" captured command")
            same(captured,[regs[f"{0x21000+sid*8:05x}"],regs[f"{0x21004+sid*8:05x}"],flags(regs,sid)],where+" capture versus live configuration")
            if phase in (1,3):
                same(recipe(world,sid),True,where+" qualified activation")
            if phase in (1,2):
                same(deadline,-1,where+" armed without timer")
            else:
                need(0 <= deadline < (1 << 63)-1,where+": valid finite deadline")
                if now is not None:
                    need(now <= deadline <= now+1000,where+": remaining transition bound")


def world_wire(world):
    """Explicit literal offsets, independently placed rather than append order."""
    raw = bytearray(783)
    encode_at(raw,0,"I",[world["globals"][n] for n in GLOBALS],"globals")
    encode_at(raw,52,"Q",world["region_bases"],"physical bases")
    encode_at(raw,68,"I",world["region_bytes"],"resource sizes")
    encode_at(raw,76,"I",[world["registers"][k] for k in KEYS],"registers")
    encode_at(raw,360,"B",[world["written"][k] for k in KEYS],"written",bools=True)
    encode_at(raw,431,"B",[world["slices"][str(s)]["active"] for s in SIDS],"active",bools=True)
    encode_at(raw,447,"B",[world["slices"][str(s)]["phase"] for s in SIDS],"phase")
    for offset,field in ((463,"captured-attr0"),(527,"captured-attr1"),(591,"captured-flags"),(655,"deadline-ns")):
        encode_at(raw,offset,"q" if offset==655 else "I",[world["slices"][str(s)][field] for s in SIDS],field)
    return bytes(raw)


def flat_world(world):
    result = []
    def add(name,value):
        result.append(({"path":DEVICE,"property":name},value))
    for name in GLOBALS:
        add(name,world["globals"][name])
    for i in range(2):
        add(f"region{i}.physical-base",world["region_bases"][i])
    for i in range(2):
        add(f"region{i}.resource-bytes",world["region_bytes"][i])
    for key in KEYS:
        add("reg."+key,world["registers"][key])
    for key in KEYS:
        add("written."+key,world["written"][key])
    for sid in SIDS:
        for field in FIELDS:
            add(f"slice{sid}.{field}",world["slices"][str(sid)][field])
    for name in SCOPE:
        add(name,world["scope"][name])
    same(len(result),GETTERS,"Independent first-observer count")
    return result


def expected_world(scenario):
    profile = scenario != "disabled"
    regs = {key:0 for key in KEYS}
    written = {key:False for key in KEYS}
    active = {sid:False for sid in SIDS}
    phase = {sid:0 for sid in SIDS}
    if profile:
        for sid in SIDS:
            for offset,value in zip((0x21000+sid*8,0x21004+sid*8),ATTRS[sid]):
                regs[f"{offset:05x}"],written[f"{offset:05x}"] = value,True
        for offset,value in ((CAD,0),(PCB,0x51c39806),(WRSC,0x1000)):
            regs[f"{offset:05x}"],written[f"{offset:05x}"] = value,True
        if scenario != "prewarm":
            for sid in (1,13,31):
                active[sid],regs[f"{sid*4096:05x}"] = True,2
        if scenario == "steady":
            active[30],regs["1e000"],regs["210f0"] = True,2,0
        elif scenario == "armed":
            phase[12],regs["0c000"] = 1,3
        elif scenario == "activate-pending":
            phase[12],regs["0c000"] = 3,2
        elif scenario == "deactivate-pending":
            active[30],phase[30],regs["1e000"] = True,4,4
        elif scenario == "prewarm":
            phase[6],regs["06000"] = 3,2
    world = {"globals":dict(zip(GLOBALS,(int(profile),)+GLOBAL_VALUES[1:])),
             "region_bases":[BASE,BCAST],"region_bytes":[0x1d0000,0x50000],
             "registers":regs,"written":written,"slices":{},
             "scope":dict(zip(SCOPE,[profile,False,False,False,False,False,profile,349,1,783]))}
    for sid in SIDS:
        capture = [0,0,0] if not phase[sid] else [regs[f"{0x21000+sid*8:05x}"],regs[f"{0x21004+sid*8:05x}"],flags(regs,sid)]
        deadline = 301000 if scenario=="activate-pending" and sid==12 else \
                   401000 if scenario=="deactivate-pending" and sid==30 else -1
        world["slices"][str(sid)] = dict(zip(FIELDS,[active[sid],profile,False,
            (1 if active[sid] else 2) if profile else 3,phase[sid],*capture,deadline]))
    for sid in SIDS:
        world["slices"][str(sid)]["recipe-valid"] = recipe(world,sid)
    return world


def scan_stream(store,path):
    needle = bytes([len(TYPE)])+TYPE.encode()+struct.pack(">II",0,1)
    candidates, h, total, tail, magic = {}, hashlib.sha256(), 0, b"", b""
    with store.open(path) as stream:
        for chunk in iter(lambda:stream.read(CHUNK),b""):
            if not magic:
                magic=chunk[:8]
            h.update(chunk)
            block,start=tail+chunk,total-len(tail)
            at=0
            while True:
                at=block.find(needle,at)
                if at < 0:
                    break
                header,payload=at-5,at+len(needle)
                if header>=0 and start+header>=8 and block[header] in (1,4):
                    absolute=start+header
                    previous=candidates.setdefault(absolute,None)
                    end=payload+PAYLOAD
                    if end+5<=len(block):
                        identity=struct.unpack_from(">I",block,header+1)[0]
                        footer=b"\x7e"+struct.pack(">I",identity)
                        same(block[end:end+5],footer,path+" footer")
                        section={"name":TYPE,"instance_id":0,"version":1,
                                 "header_offset":absolute,"section_id":identity,
                                 "payload_offset":start+payload,"payload_bytes":783,
                                 "footer_offset":start+end,"footer_hex":footer.hex()}
                        item=(section,bytes(block[payload:end]))
                        if previous is not None:
                            same(item,previous,path+" overlap scan")
                        candidates[absolute]=item
                at+=1
            total+=len(chunk)
            tail=block[-4096:]
    same(magic,b"QEVM\x00\x00\x00\x03",path+" stream magic/version")
    same(total,store.size(path),path+" full bytes")
    same(len(candidates),1,path+" unique LLCC section")
    item=next(iter(candidates.values()))
    need(item is not None,path+" complete section")
    store.observed[path]={"bytes":total,"sha256":h.hexdigest()}
    return {"bytes":total,"sha256":h.hexdigest(),"section":item[0],"payload":item[1]}


def is_mmio(call):
    parts=call["command"].split()
    if parts[0] not in ("readb","readw","readl","readq","writeb","writew","writel","writeq"):
        return False
    address=int(parts[1],0)
    return BASE<=address<BASE+0x1d0000 or BCAST<=address<BCAST+0x50000


def command_contract(store,directory,report,profile,incoming=False,accel="qtest",default=False):
    command=store.json(directory+"/command.json")
    need(type(command) is list and all(type(s) is str for s in command),directory+" argv")
    same(command[0],report["qemu"],directory+" QEMU path")
    same(command.count("-S"),1,directory+" stopped startup")
    for option in ("-kernel","-initrd","-append","-drive","-blockdev","-device"):
        need(option not in command,directory+" no guest input "+option)
    def value(option):
        same(command.count(option),1,directory+" option "+option)
        return command[command.index(option)+1]
    same(value("-accel"),accel,directory+" accelerator")
    same(value("-m"),"128M",directory+" memory")
    same(value("-smp"),"8",directory+" CPU count")
    same(value("-machine"),report["machine"],directory+" machine")
    machine=report["machine"].split(",")
    same(machine[0],"tb371fc",directory+" board")
    need(all(x in machine for x in ("gcc-boot-configured=off","ufs-enabled=off","rpmh-profile=off","rpmh-boot-configured=off")),directory+" no provider boot fixture")
    profiles=[command[i+1] for i,x in enumerate(command) if x=="-global"]
    same(profiles,[] if default else [TYPE+".enabled-reference="+("on" if profile else "off")],directory+" exact reference")
    same("-incoming" in command,incoming,directory+" incoming")
    if incoming:
        same(value("-incoming"),"defer",directory+" incoming defer")
    if accel=="qtest":
        value("-qtest")
    else:
        need("-qtest" not in command,directory+" no QTest in TCG fixture")
    value("-qmp")
    return store.text(directory+"/qemu.stderr")


def source_trace(store, directory, saved, report, profile):
    command_contract(store, directory, report, profile)
    before = store.json(directory + "/source-before-save.json")
    after = store.json(directory + "/source-after-save.json")
    same(before, saved["snapshot"], directory + ".saved source QOM")
    same(after, before, directory + ".source QOM unchanged across serialization")
    sent = store.json(directory + "/source-query-migrate.json")
    same(sent, saved["source_query_migrate"], directory + ".raw source migration response")
    same(sent.get("status"), "completed", directory + ".source migration completed")
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    same(qtest_time(tc, directory), saved["source_virtual_ns"], directory + ".source virtual epoch")
    exports = [i for i, call in enumerate(qc) if operation(call) == "migrate"]
    same(len(exports), 1, directory + ".one exported stream")
    at = exports[0]
    migration_argument(qc[at], saved["file"])
    reply(qc[at])
    qom_block(qc[at - GETTERS:at], flat_world(before), directory + ".stopped source getters")
    status_at = at - GETTERS - 1
    same(operation(qc[status_at]), "query-status", directory + ".pre-save stopped query")
    same(reply(qc[status_at]).get("running"), False, directory + ".source stopped")
    same(operation(qc[status_at - 1]), "stop", directory + ".explicit stop before source QOM")
    need(not any(operation(c) == "cont" for c in qc[status_at - 1:]), directory + ": source resumed after stop")
    need(not any(operation(c) in ("qom-set", "device_add", "human-monitor-command") for c in qc),
         directory + ": unsupported mutating QMP command")
    completions = [i for i in range(at + 1, len(qc)) if operation(qc[i]) == "query-migrate" and
                   qc[i]["response"] is not None and reply(qc[i]).get("status") == "completed"]
    need(completions, directory + ": no actual completed migration response")
    completed = completions[0]
    same(reply(qc[completed]), sent, directory + ".actual source completed response")
    same(operation(qc[completed + 1]), "query-status", directory + ".post-save status query")
    same(reply(qc[completed + 1]).get("status"), "postmigrate", directory + ".postmigrate state")
    qom_block(qc[completed + 2:completed + 2 + GETTERS], flat_world(after),
              directory + ".postmigrate source getters")
    need(tc, directory + ": source QTest trace absent")
    last = tc[-1]["command"].split()
    same(last[0], "writel", directory + ".last source QTest operation is RAM witness")
    same(int(last[1], 0), RAM, directory + ".RAM witness address")
    same(int(last[2], 0), saved["ram_witness"], directory + ".RAM witness value")
    return {"actual_source_getters": 2 * GETTERS, "source_stop_and_postmigrate_verified": True,
            "virtual_epoch": saved["source_virtual_ns"], "qmp_requests": len(qc),
            "qtest_commands": len(tc), "no_resume_after_source_stop": True}


def rejection_trace(store, directory, row, report, filename, epoch, diagnostic, profile):
    stderr = command_contract(store, directory, report, profile, incoming=True)
    same(row.get("destination_exit"), 1, directory + ".actual rejected process exit")
    same(row.get("expected_diagnostic"), diagnostic, directory + ".specific expected diagnostic")
    same(row.get("specific_load_failure_observed"), True, directory + ".reported migration rejection")
    same(row.get("target_directory"), PurePosixPath(directory).name, directory + ".actual target directory")
    for literal in (TYPE, diagnostic, "load of migration failed", "error while loading state"):
        need(literal in stderr, directory + ": missing actual stderr diagnostic " + literal)
    qc, tc = qmp_calls(store, directory), qtest_calls(store, directory)
    imports = [c for c in qc if operation(c) == "migrate-incoming"]
    same(len(imports), 1, directory + ".one actual rejected incoming")
    migration_argument(imports[0], filename)
    need(not any(operation(c) == "cont" for c in qc), directory + ": rejected target resumed")
    need(not any(operation(c) == "query-migrate" and c["response"] is not None and
                 c["response"].get("return", {}).get("status") == "completed" for c in qc),
         directory + ": purported rejection actually completed")
    same(qtest_time(tc, directory), epoch, directory + ".rejected target virtual epoch")
    same(len(tc), 1, directory + ".rejection trace performs only one clock-set")
    parts = tc[0]["command"].split()
    same(parts[0], "clock_set", directory + ".rejected target QTest command")
    same(int(parts[1], 0), epoch, directory + ".rejected clock-set epoch")
    need(not any(is_mmio(c) for c in tc), directory + ": device MMIO in rejection target")
    return {"exit_code": 1, "specific_stderr_verified": True,
            "incoming_file": filename, "device_mmio_commands": 0, "cont_commands": 0,
            "process_exit_evidence": "reviewed harness process.wait receipt; actual stderr independently checked",
            "query_migrate_failed_response_required": False}


def tokens(call):
    parts=call["command"].split()
    return (parts[0],)+tuple(int(value,0) for value in parts[1:])


def literal_program():
    commands=[]
    cad=pcb=wrsc=0
    for row in SCT:
        sid=row[1]
        attr0,attr1=ATTRS[sid]
        cad|=row[9]<<sid
        wrsc|=row[10]<<sid
        pcb|=row[11]<<sid
        commands.extend(("writel",BCAST+offset,value) for offset,value in
                        ((0x21004+sid*8,attr1),(0x21000+sid*8,attr0),
                         (WRSC,wrsc),(CAD,cad),(PCB,pcb)))
        if row[12]:
            commands.extend((("writel",BCAST+sid*4096,3),
                             ("writel",BCAST+sid*4096,2),("clock_step",1000)))
    return commands


def scenario_values(name):
    return {"steady":(100000,0x4c4c4301),"armed":(205000,0x4c4c4302),
            "activate-pending":(300300,0x4c4c4303),
            "deactivate-pending":(400300,0x4c4c4304),
            "disabled":(500000,0x4c4c4300)}[name]


def source_commands(name,witness):
    commands=[] if name=="disabled" else literal_program()
    if name in ("steady","deactivate-pending"):
        commands.extend((("writel",BCAST+30*4096,3),("writel",BCAST+30*4096,2),("clock_step",1000)))
    if name=="steady":
        commands.extend((("writel",BCAST+0x210f0,0),("clock_set",100000)))
    elif name=="armed":
        commands.extend((("clock_set",200000),("writel",BCAST+12*4096,3),("clock_step",5000)))
    elif name=="activate-pending":
        commands.extend((("clock_set",300000),("writel",BCAST+12*4096,3),
                         ("writel",BCAST+12*4096,2),("clock_step",300)))
    elif name=="deactivate-pending":
        commands.extend((("clock_set",400000),("writel",BCAST+30*4096,5),
                         ("writel",BCAST+30*4096,4),("clock_step",300)))
    else:
        commands.append(("clock_set",500000))
    commands.append(("writel",RAM,witness))
    return commands


def scenario_contract(saved,name):
    epoch,witness=scenario_values(name)
    same(saved["source_virtual_ns"],epoch,name+" source epoch")
    same(saved["ram_witness"],witness,name+" witness")
    same(saved["snapshot"],expected_world(name),name+" independently reconstructed source world")
    check_world(saved["snapshot"],name!="disabled",name,epoch)


def query(name,value):
    return ({"path":DEVICE,"property":name},value)


def state_queries(sid,active,phase=0,deadline=-1):
    values=[("active",active),("state-known",True),("status",1 if active else 2),
            ("phase",phase),("deadline-ns",deadline)]
    if phase==0:
        values += [("captured-attr0",0),("captured-attr1",0),("captured-flags",0)]
    return [query(f"slice{sid}.{field}",value) for field,value in values]


def target_queries(saved,name):
    if name=="steady":
        return flat_world(saved["snapshot"])*2 + [query("slice30.active",True)] + \
            state_queries(30,True,4,101000)+state_queries(30,False)+state_queries(1,True)+\
            [query("slice1.active",True)]+state_queries(1,True,4,112000)+state_queries(1,False)
    if name=="armed":
        return state_queries(12,False,1)+state_queries(12,False,3,216000)+\
            state_queries(12,True)+[query("slice12.active",True)]+\
            state_queries(12,True,4,217000)+state_queries(12,False)
    if name=="activate-pending":
        return state_queries(12,False,3,301000)+state_queries(12,True)
    return state_queries(30,True,4,401000)+state_queries(30,False)


def target_commands(saved,name):
    result=[("readl",RAM)]
    if name=="steady":
        for bank in range(5):
            for offset in REGS:
                if (bank<4 and offset==HWINFO) or (bank==4 and offset in (TRP,DRP)):
                    continue
                result.append(("readl",(BASE+bank*0x80000 if bank<4 else BCAST)+offset))
        result.extend((("writel",BCAST+30*4096,5),("writel",BCAST+30*4096,4),("clock_step",1000),
                       ("writel",BCAST+4096,5),("writel",BCAST+4096,4),("writel",BCAST+4096,0),
                       ("clock_step",10000),("writel",BCAST+4096,5),("writel",BCAST+4096,4),
                       ("clock_step",1000)))
    elif name=="armed":
        result.extend((("clock_step",10000),("writel",BCAST+12*4096,2),("clock_step",999),
                       ("clock_step",1),("writel",BCAST+12*4096,5),("writel",BCAST+12*4096,4),
                       ("clock_step",1000)))
    else:
        sid=12 if name=="activate-pending" else 30
        result.extend((("clock_step",699),("readl",BCAST+sid*4096+4),("clock_step",1)))
        result.extend(("readl",BASE+bank*0x80000+sid*4096+4) for bank in range(4))
    return result


def read_result(call):
    parts=call["reply"].split()
    same(len(parts),2,"QTest read reply shape")
    return int(parts[1],0)


def target_trace(store,directory,saved,completed,report,name):
    command_contract(store,directory,report,True,incoming=True)
    first=store.json(directory+"/destination-before-first-device-mmio.json")
    stale=store.json(directory+"/destination-before-incoming.json")
    same(first,completed["first_target_observation"],directory+" actual first sidecar")
    same(first["snapshot"],saved["snapshot"],directory+" complete restored world")
    same(first["first_observation_qom_get_count"],313,directory+" pure getter count")
    for key in ("device_mmio_since_incoming","qmp_cont_since_incoming","guest_cpu_executed","shared_log_timestamp_claimed"):
        same(first[key],False,directory+" "+key)
    check_world(first["snapshot"],True,directory+" restored",saved["source_virtual_ns"])
    expected_stale=expected_world("prewarm")
    expected_stale["slices"]["6"]["deadline-ns"]=saved["source_virtual_ns"]+1000
    same(stale["snapshot"],expected_stale,directory+" independent dirty target")
    need(stale["snapshot"] != first["snapshot"],directory+" destination really dirty")
    same(stale["source_epoch_ns"],saved["source_virtual_ns"],directory+" prewarm epoch")
    same(stale["stale_pending_sid"],6,directory+" stale transaction")
    check_world(stale["snapshot"],True,directory+" prewarm",saved["source_virtual_ns"])
    qc,tc=qmp_calls(store,directory),qtest_calls(store,directory)
    imports=[i for i,c in enumerate(qc) if operation(c)=="migrate-incoming"]
    same(len(imports),1,directory+" one incoming")
    at=imports[0]
    migration_argument(qc[at],saved["file"])
    reply(qc[at])
    qom_block(qc[at-313:at],flat_world(expected_stale),directory+" raw prewarm getters")
    finishes=[i for i in range(at+1,len(qc)) if operation(qc[i])=="query-migrate" and
              qc[i]["response"] is not None and reply(qc[i]).get("status")=="completed"]
    need(finishes,directory+" completed incoming")
    finish=finishes[0]
    same(reply(qc[finish]),first["query_migrate"],directory+" raw completed query")
    same(operation(qc[finish+1]),"query-status",directory+" stopped status query")
    same(reply(qc[finish+1]).get("running"),False,directory+" target stopped")
    begin=finish+2
    qom_block(qc[begin:begin+313],flat_world(saved["snapshot"]),directory+" first actual 313 pure getters")
    same(operation(qc[begin+313]),"cont",directory+" first cont occurs after all pure getters")
    reply(qc[begin+313])
    need(not any(operation(c)=="cont" for c in qc[:begin+313]),directory+" no early cont")
    qom_block(qc[begin+314:],target_queries(saved,name),directory+" exact post-resume state protocol")
    need(not any(operation(c) in ("qom-set","human-monitor-command","device_add") for c in qc),directory+" no QMP state repair")
    qom_before=sum(operation(c)=="qom-get" for c in qc[:begin])
    same(first["qom_get_count_before_first_observation"],qom_before,directory+" actual before getter count")
    same(first["qom_get_count_after_first_observation"],qom_before+313,directory+" actual after getter count")
    same(stale["qom_get_count"],qom_before,directory+" prewarm getter receipt")
    clocks=[i for i,c in enumerate(tc) if tokens(c)==("clock_set",saved["source_virtual_ns"])]
    same(len(clocks),2,directory+" two explicit source-epoch markers")
    boundary=clocks[-1]
    pre=tc[:boundary]
    mmio=sum(is_mmio(c) for c in pre)
    same(mmio,82,directory+" independent prewarm MMIO count")
    same(stale["device_mmio_count"],mmio,directory+" actual dirty MMIO count")
    same(first["device_mmio_count_before_incoming"],mmio,directory+" actual before-incoming MMIO count")
    same(first["device_mmio_count_after_first_observation"],mmio,directory+" unchanged MMIO count")
    for key in ("qmp_cont_count_before_incoming","qmp_cont_count_after_first_observation"):
        same(first[key],0,directory+" "+key)
    same(stale["qmp_cont_count"],0,directory+" stale cont count")
    expected_pre=[("clock_set",saved["source_virtual_ns"])] + [c for c in literal_program()
        if not (c[0]=="clock_step" or (c[0]=="writel" and (c[1]-BCAST) in [sid*4096 for sid in (1,13,31)]))]
    expected_pre.extend((("writel",BCAST+6*4096,3),("writel",BCAST+6*4096,2),
                         ("writel",RAM,saved["ram_witness"] ^ 0xffffffff)))
    same([tokens(c) for c in pre],expected_pre,directory+" exact dirty prewarm protocol")
    post=tc[boundary+1:]
    same([tokens(c) for c in post],target_commands(saved,name),directory+" exact restoration and resume QTest protocol")
    same(read_result(post[0]),saved["ram_witness"],directory+" actual restored RAM witness")
    same(first["ram_witness"],saved["ram_witness"],directory+" witness receipt")
    if name=="steady":
        reads=post[1:350]
        for call in reads:
            address=tokens(call)[1]
            offset=address-BCAST if address>=BCAST else (address-BASE)%0x80000
            value=saved["snapshot"]["registers"][f"{offset:05x}"]
            if offset in [sid*4096+4 for sid in SIDS]:
                value=saved["snapshot"]["slices"][str(offset//4096)]["status"]
            elif offset==COMMON:
                value=0x40000000
            same(read_result(call),value,directory+" actual restored mirror read")
    elif name in ("activate-pending","deactivate-pending"):
        reads=[c for c in post[1:] if tokens(c)[0]=="readl"]
        expected=[2,1,1,1,1] if name=="activate-pending" else [1,2,2,2,2]
        same([read_result(c) for c in reads],expected,directory+" opposite poll bit before/after exact deadline")
    final_epoch=qtest_time(tc,directory)
    same(final_epoch,{"steady":112000,"armed":217000,"activate-pending":301000,"deactivate-pending":401000}[name],directory+" final virtual epoch")
    return {"first_actual_getters":313,"prewarm_actual_mmio":82,"first_mmio_delta":0,
            "first_cont_delta":0,"full_source_world_restored":True,
            "post_resume_protocol_and_timer_verified":True,"final_virtual_ns":final_epoch,
            "shared_timestamps_claimed":False,
            "ordering_basis":"Actual per-file protocol, counter receipts and exact pinned harness order."}


def report_contract(report,qemu_sha):
    expected={
        "schema":"tb371fc-llcc-sm8250-v1","suite":"llcc-sm8250-reference",
        "test_script_sha256":TEST_SHA,"expected_checks":32,"expected_completed_migrations":4,
        "expected_rejected_migrations":2,"expected_corrupted_stream_rejections":4,
        "expected_unmodified_streams":5,"expected_stream_files":9,
        "vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":783,
        "reference_identity":0x4c4c4332,"physical_base":BASE,"broadcast_base":BCAST,
        "region_count":2,"banks_resource_bytes":0x1d0000,"broadcast_resource_bytes":0x50000,
        "bank_count":4,"bank_stride":0x80000,"bank_resource_bytes":0x50000,
        "slice_count":16,"register_count":71,"mapped_word_count":349,
        "sparse_window_count":139,"mapped_bytes":1396,"timer_count":1,
        "input_clock_count":0,"output_clock_count":0,"provider_count":0,
        "transition_delay_ns":1000,"first_observation_qom_get_count":313,
        "source_commit":PRIMARY,"geometry_source_commit":UPSTREAM,
        "actual_dt_sha256":"5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "reference_enabled_by_default":False,"bank_geometry_is_reference":True,
        "physical_bank_count_measured":False,"hardware_version_known":False,
        "memory_controller_count_known":False,"cache_data_modeled":False,
        "cache_performance_modeled":False,"ecc_error_injection_modeled":False,
        "runtime_provider_revocation_tested":False,"guest_cpu_executed":False,
        "native_kernel_probe_observed":False,"android_boot_passed":False,
        "binary_unchanged":True,"qemu_sha256_before":qemu_sha,"qemu_sha256_after":qemu_sha,"passed":True,
    }
    need(type(report) is dict and not any(k in report for k in ("error","traceback")),"LLCC report has no exception")
    for key,value in expected.items():
        same(report.get(key),value,"LLCC result."+key)
    same([c["name"] for c in report["checks"]],list(CHECK_NAMES),"Exact independent 32-check inventory")
    for i,row in enumerate(report["checks"]):
        same(row["passed"],True,"Check "+row["name"])
        if i<20:
            same(row["fixture"],f"ordinary-{i:02d}-"+row["name"],"Actual ordinary fixture")
    for key,count in (("migrations",4),("rejected_migrations",2),("corrupted_stream_rejections",4),
                      ("migration_sources",5),("actual_stream_files",9)):
        need(type(report.get(key)) is list,"Missing result collection "+key)
        same(len(report[key]),count,"Collection "+key)
    same([r["name"] for r in report["migrations"]],list(COMPLETED),"Four completion cases")
    same([r["name"] for r in report["rejected_migrations"]],["on_to_off","off_to_on"],"Both profile directions")
    same([r["name"] for r in report["corrupted_stream_rejections"]],list(CORRUPT),"Four independent mutations")
    same([r["file"] for r in report["migration_sources"]],["llcc-"+s+".migration" for s in SCENARIOS],"Five original streams")


def select_case(store,args,receipt):
    names=[name for name in store.entries if re.search(r"(?:^|/)components/[0-9]+-llcc-sm8250-reference/case\.json$",name)]
    same(len(names),1,"Unique actual LLCC component case")
    case_path=names[0]
    root=case_path.rsplit("/",1)[0]
    case=store.json(case_path)
    report=store.json(root+"/fixtures/data/result.json")
    same(case.get("name"),"llcc-sm8250-reference","LLCC case name")
    same(case.get("passed"),True,"LLCC actual case verdict")
    same(case.get("exit_code"),0,"LLCC actual process exit")
    need(not any(k in case for k in ("error","artifact_error","timed_out")),"LLCC case complete")
    need(type(case.get("completed_utc")) is str and case["completed_utc"],"Actual completion timestamp retained")
    report_contract(report,args.expected_qemu_sha256)
    for name in ("stdout.log","stderr.log"):
        raw=store.read(root+"/"+name)
        same(case["log_sha256"][name],sha(raw),"Actual harness log hash "+name)
        if name=="stdout.log":
            same(json_value(raw,name),report,"Actual stdout and saved report")
    verified=case["verified"]
    same(verified["verdict"],True,"CI verdict")
    same(verified["verdict_field"],"passed","CI verdict field")
    same(verified["check_count"],32,"CI actual check count")
    same(verified["verified_lengths"],{"checks":32,"migrations":4,"rejected_migrations":2,
         "corrupted_stream_rejections":4,"migration_sources":5,"actual_stream_files":9},"CI collection checks")
    argv=case["command"]
    need(type(argv) is list and len(argv)>=6 and "-O" not in argv and "-OO" not in argv,"Harness assertions enabled")
    same(PurePosixPath(argv[1]).name,"test_llcc_sm8250.py","Actual harness path")
    for option,value in (("--qemu",report["qemu"]),("--out",report["work_dir"])):
        same(argv.count(option),1,"Harness option "+option)
        same(argv[argv.index(option)+1],value,"Harness value "+option)
    same(PurePosixPath(report["work_dir"]),PurePosixPath(case["fixture"])/"data","Actual fixture output path")
    prefix=root.split("/components/",1)[0]
    source_path=prefix+"/source-commit.txt"
    same(store.text(source_path).strip(),args.expected_head,"Exact archived source commit")
    receipt["component"]={"case":case_path,"report":root+"/fixtures/data/result.json",
                          "source_commit_file":source_path,"head":args.expected_head,
                          "check_count":32,"exit_code":0,"test_sha256":TEST_SHA}
    return root+"/fixtures/data",report


def check_stream_record(store,path,saved,world):
    stream=scan_stream(store,path)
    same(stream["bytes"],saved["bytes"],path+" saved bytes")
    same(stream["sha256"],saved["sha256"],path+" saved whole stream hash")
    same(saved["stream_modified"],False,path+" unmodified original")
    same(saved["source_status"],"completed",path+" successful source")
    same(stream["payload"],world_wire(world),path+" every independently encoded 783 bytes")
    sidecar=store.json(path+".sections.json")
    expected=dict(stream["section"])
    expected.update(payload_file=PurePosixPath(path).name+".llcc-v1.payload.bin",
                    payload_sha256=sha(stream["payload"]),all_bytes_match_stopped_source_qom=True)
    same(saved["sections"],{"llcc":expected},path+" saved section/footer metadata")
    same(sidecar,{"llcc":expected},path+" actual section/footer sidecar")
    same(store.read(path+".llcc-v1.payload.bin"),stream["payload"],path+" retained payload bytes")
    return stream


def audit_migrations(store,data,report,receipt):
    names=sorted(name[len(data)+1:] for name in store.entries if name.startswith(data+"/") and
                 "/" not in name[len(data)+1:] and name.endswith(".migration"))
    expected=sorted(["llcc-"+s+".migration" for s in SCENARIOS]+["llcc-corrupt-"+s+".migration" for s in CORRUPT])
    same(names,expected,"Exact nine real migration files")
    listed={row["name"]:row for row in report["actual_stream_files"]}
    same(set(listed),set(expected),"Report stream names are unique and complete")
    saved_by_scenario={}
    streams={}
    receipt["sources"],receipt["completed"],receipt["profile_rejections"],receipt["corruption_rejections"] = [],[],[],[]
    for scenario,saved in zip(SCENARIOS,report["migration_sources"]):
        scenario_contract(saved,scenario)
        name="llcc-"+scenario+".migration"
        same(saved["source_directory"],"migration-"+scenario+"-source",scenario+" source directory")
        path=data+"/"+name
        stream=check_stream_record(store,path,saved,saved["snapshot"])
        streams[name]=stream
        same(listed[name],{"name":name,"bytes":stream["bytes"],"sha256":stream["sha256"]},name+" actual file inventory")
        directory=data+"/"+saved["source_directory"]
        trace=source_trace(store,directory,saved,report,scenario!="disabled")
        same([tokens(c) for c in qtest_calls(store,directory)],source_commands(scenario,saved["ram_witness"]),scenario+" literal source programming trace")
        receipt["sources"].append({"scenario":scenario,"file":name,"bytes":stream["bytes"],
            "sha256":stream["sha256"],"payload_sha256":sha(stream["payload"]),
            "all_783_bytes_match":True,"all_source_fields_and_derived_state_verified":True,**trace})
        saved_by_scenario[scenario]=saved
    for scenario,row,case_name in zip(SCENARIOS[:4],report["migrations"],COMPLETED):
        saved=saved_by_scenario[scenario]
        for key,value in (("name",case_name),("passed",True),("source_status","completed"),
                          ("target_status","completed"),("snapshot_file",saved["file"]),
                          ("snapshot_sha256",saved["sha256"]),("stream_modified",False),
                          ("continued_after_restore",True),("all_783_payload_bytes_verified",True),
                          ("source_directory",saved["source_directory"]),
                          ("target_directory","migration-"+scenario+"-target")):
            same(row[key],value,case_name+" "+key)
        trace=target_trace(store,data+"/"+row["target_directory"],saved,row,report,scenario)
        receipt["completed"].append({"name":case_name,"file":saved["file"],"passed":True,**trace})
    for row,scenario,profile,diagnostic in zip(report["rejected_migrations"],
            ("steady","disabled"),(False,True),("0 != 1","1 != 0")):
        saved=saved_by_scenario[scenario]
        same(row["passed"],True,"Profile rejected verdict")
        same(row["snapshot_file"],saved["file"],"Profile original filename")
        same(row["snapshot_sha256"],saved["sha256"],"Profile original hash")
        same(row["stream_modified"],False,"Profile unmodified stream")
        expected_dir="reject-profile-"+row["name"]
        same(row["target_directory"],expected_dir,"Profile target path")
        trace=rejection_trace(store,data+"/"+expected_dir,row,report,saved["file"],
                              saved["source_virtual_ns"],diagnostic,profile)
        receipt["profile_rejections"].append({"name":row["name"],"passed":True,**trace})
    mutations=(
        ("physical_base","steady",52,8,BASE,4,f"{BASE:x} != {BASE ^ 4:x}"),
        ("captured_attr","activate-pending",483,4,0x0fff0000,1,TYPE+": invalid migrated capture"),
        ("written","steady",360+REGS.index(COMMON),1,0,1,TYPE+": invalid migrated written"),
        ("pending_deadline","deactivate-pending",759,8,401000,1<<63,TYPE+": invalid migrated transition"),
    )
    for row,(name,scenario,relative,width,old,xor,diagnostic) in zip(report["corrupted_stream_rejections"],mutations):
        saved=saved_by_scenario[scenario]
        original=streams[saved["file"]]
        filename="llcc-corrupt-"+name+".migration"
        changed=scan_stream(store,data+"/"+filename)
        absolute=original["section"]["payload_offset"]+relative
        expected_byte=absolute+width-1-(xor.bit_length()-1)//8
        for key,value in (("name",name),("passed",True),("source_file",saved["file"]),
                          ("source_sha256",saved["sha256"]),("snapshot_file",filename),
                          ("snapshot_sha256",changed["sha256"]),("stream_modified",True),
                          ("field_offset",relative),("field_bytes",width),("old_value",old),("xor",xor),
                          ("changed_byte_offsets",[expected_byte]),("changed_bits",1),
                          ("original_stream_unchanged",True),("target_directory","reject-corrupt-"+name)):
            same(row[key],value,"Mutation "+name+" "+key)
        same(changed["section"],original["section"],name+" complete headers and footers unchanged")
        same(int.from_bytes(original["payload"][relative:relative+width],"big"),old,name+" actual source field")
        same(int.from_bytes(changed["payload"][relative:relative+width],"big"),old^xor,name+" actual mutated field")
        diff=one_bit_difference(store,data+"/"+saved["file"],data+"/"+filename,expected_byte,
                                1 << ((xor.bit_length()-1)%8),saved["sha256"],changed["sha256"])
        same(listed[filename],{"name":filename,"bytes":changed["bytes"],"sha256":changed["sha256"]},name+" actual file inventory")
        trace=rejection_trace(store,data+"/reject-corrupt-"+name,row,report,filename,
                              saved["source_virtual_ns"],diagnostic,True)
        receipt["corruption_rejections"].append({"name":name,"passed":True,"file":filename,
            "sha256":changed["sha256"],"payload_field_offset":relative,**diff,**trace})


def expected_leaves():
    result=[]
    for bank in range(5):
        offsets=[o for o in REGS if not ((bank<4 and o==HWINFO) or (bank==4 and o in (TRP,DRP)))]
        base=BASE+bank*0x80000 if bank<4 else BCAST
        runs=[]
        for offset in offsets:
            if runs and runs[-1][1]+4==offset:
                runs[-1]=(runs[-1][0],offset)
            else:
                runs.append((offset,offset))
        view=f"bank{bank}" if bank<4 else "broadcast"
        result.extend((base+a,base+b+3,f"{TYPE}.{view}.{a:05x}") for a,b in runs)
    return result


def cold_world(profile):
    world=expected_world("disabled")
    if profile:
        world["globals"]["enabled-profile"]=1
        for key in ("enabled-reference","bank-count-known"):
            world["scope"][key]=True
        for state in world["slices"].values():
            state["state-known"],state["status"]=True,2
    return world


def audit_ordinary(store,data,report,receipt):
    summaries=[]
    for index,name in enumerate(ORDINARY):
        directory=data+f"/ordinary-{index:02d}-"+name
        stderr=command_contract(store,directory,report,True)
        qc,tc=qmp_calls(store,directory),qtest_calls(store,directory)
        need(qc and tc,directory+" actual protocols exist")
        same(operation(qc[0]),"qmp_capabilities",directory+" QMP negotiation")
        same(sum(operation(c)=="cont" for c in qc),1,directory+" only initial QTest cont")
        for call in qc:
            if operation(call)!="qom-set":
                reply(call)
        qtest_time(tc,directory)
        summaries.append({"name":name,"qmp_requests":len(qc),"qtest_commands":len(tc),
                          "actual_device_mmio":sum(is_mmio(c) for c in tc)})
        if index==1:
            same([tokens(c) for c in tc[:len(literal_program())]],literal_program(),name+" exact original probe order")
            need(len(tc)==len(literal_program())+349,name+" all 349 mirror reads")
        if index==14:
            tree=store.text(directory+"/mtree.txt")
            pattern=r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ("+re.escape(TYPE)+r"\.(?:bank[0-3]|broadcast)\.[0-9a-f]{5})(?:\s|$)"
            found={(int(a,16),int(b,16),n) for a,b,n in re.findall(pattern,tree)}
            same(found,set(expected_leaves()),"Exact actual 139 sparse ownership leaves")
            hmp=[reply(c) for c in qc if operation(c)=="human-monitor-command"]
            same(hmp,[tree],"Retained mtree is actual QMP reply")
            holes=[BASE+b*0x80000+o for b in range(4) for o in (0,0x1008,HWINFO,0x3100c,0x50000)]
            holes += [BCAST+o for o in (0,TRP,DRP,0x30004,0x3100c,0x50000)]
            diagnostics=re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), region '([^']+)', reason: (.+)",stderr)
            actual=[(op,int(a,16),int(size),region,reason) for op,a,size,region,reason in diagnostics if int(a,16) in holes]
            same(actual,[(op,a,4,"(null)","rejected") for a in holes for op in ("read","write")],"Actual 26 true-hole rejection pairs")
    directory=data+"/default-off"
    stderr=command_contract(store,directory,report,False,default=True)
    qc,tc=qmp_calls(store,directory),qtest_calls(store,directory)
    world=cold_world(False)
    check_world(world,False,"Independent default-off world")
    getters=[c for c in qc if operation(c)=="qom-get"]
    qom_block(getters,flat_world(world)*2,"Actual default-off pure worlds")
    expected=[]
    for bank in range(5):
        view=f"bank{bank}" if bank<4 else "broadcast"
        base=BASE+bank*0x80000 if bank<4 else BCAST
        for offset in REGS:
            if (bank<4 and offset==HWINFO) or (bank==4 and offset in (TRP,DRP)):
                continue
            expected.extend((("readl",base+offset),("writel",base+offset,0)))
            for op in ("read","write"):
                line=f"{TYPE}: rejected {op} {view}+0x{offset:05x} size 4 (disabled-or-invalid-access)"
                same(stderr.splitlines().count(line),1,"Default-off exact diagnostic "+line)
    expected.append(("clock_step",100000))
    same([tokens(c) for c in tc],expected,"Actual all 349 words disabled read/write protocol")
    qtest_time(tc,directory)
    directory=data+"/paused-tcg"
    command_contract(store,directory,report,True,accel="tcg")
    qc,tc=qmp_calls(store,directory),qtest_calls(store,directory)
    same(tc,[],"Paused TCG has no QTest operations")
    need(not any(operation(c)=="cont" for c in qc),"Paused TCG never continued")
    for c in qc:
        if operation(c)=="query-status":
            same(reply(c).get("running"),False,"Actual TCG remains paused")
    qom_block([c for c in qc if operation(c)=="qom-get"],flat_world(cold_world(True))*2,"Actual paused TCG pure worlds")
    setters=[c for c in qc if operation(c)=="qom-set"]
    same(len(setters),1,"One actual absent fixture probe")
    need("error" in setters[0]["response"] and "not found" in setters[0]["response"]["error"]["desc"],"Paused TCG fixture really absent")
    receipt["ordinary"]={"cases":summaries,"count":20,"default_off_physical_words":349,
                         "paused_tcg_no_cont_or_mmio":True,"exact_139_leaves":True,
                         "true_hole_count":26,"all_actual_protocol_files_read":True}


PINS = (
    ("primary-r11/drivers/soc/qcom/llcc-slice.c",10965,"a933f377db535f12f47942eff31fffe214af0912","5a49480bebfa84ca94240202abc3fc3bc8eaf6379df9f4b71a92640e140eb1c5"),
    ("primary-r11/drivers/soc/qcom/llcc-kona.c",3764,"a155d6365246ceaf2e8bca5a289ed72772cac74e","140c5774139c0827003754bb083d0b6945fbd9982bc5538105084614eac1b2a9"),
    ("primary-r11/include/linux/soc/qcom/llcc-qcom.h",5789,"f0e21085aec911e1f9d854fefd77418666e77d4d","1d348978460ba7a32087af7567c80ddeaa98399855c719e7c0c2b1fcc4d7f093"),
    ("primary-r11/drivers/soc/qcom/llcc_perfmon.c",37150,"99278c99cc311838d0b612bd5a96e908a5ec03ed","38f1df996a946b0a6ce8f4cd71559367d1f4498d475605bc9089311d63defeeb"),
    ("primary-r11/drivers/soc/qcom/llcc_perfmon.h",9820,"4a3accda43e9a2f681c118f43b502e13a52d2eba","536de10d400093fa406b88f7a3ebfaf30966b43e15f88533be98c85ac5baebfd"),
    ("primary-r11/drivers/edac/qcom_edac.c",11972,"560c96c1e5b88dc8b9b622ce4d3d590370470d7a","ae3c0da558345d3c471fd35a5ae7f878a84c61d21093056d4ac66359f2486a24"),
    ("primary-r11/arch/arm64/boot/dts/vendor/qcom/kona.dtsi",132499,"e9651f09bd579616787894dc8514429063623b2d","12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55"),
    ("primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi",176883,"630f4eff20bf810e347685066dce74ab92a23b56","8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499"),
)
OUTPUT_PINS = (
    ("hw/misc/qcom-sm8250-llcc-data.inc",10514,"f3f7979c7973b2fbf9d8d398c064b8a404a290fbdd9d5a141407c0b28f974a46"),
    ("tb371fc/docs/llcc-sm8250-registers.json",84889,"9b9989a04b3719665439dab6287be8ced18511021c58ef5c7011f36a587b4772"),
)


def audit_sources(store,receipt):
    paths=[name for name in store.entries if re.search(r"(?:^|/)components/[0-9]+-llcc-source-generation/case\.json$",name)]
    same(len(paths),1,"Unique LLCC generation component")
    case_path=paths[0]
    root=case_path.rsplit("/",1)[0]
    data=root+"/fixtures/data"
    case,report=store.json(case_path),store.json(data+"/result.json")
    same(case.get("passed"),True,"Actual source-generation gate")
    same(case.get("exit_code"),0,"Actual source-generation exit")
    for key,value in (("schema","tb371fc-llcc-generation-v1"),("passed",True),
                      ("source_revision",PRIMARY),("geometry_source_revision",UPSTREAM),
                      ("source_pins_verified",True),("generator_exit",0),("source_table_bytes",640),
                      ("source_table_sha256","1ae851c29537ec405b15db1bd151710c25ad0f063a74970253f9d16754dc5d55")):
        same(report.get(key),value,"Generation report "+key)
    need("error" not in report,"No generation exception")
    same(len(report["inputs"]),8,"Eight source inputs")
    same(len(report["checks"]),2,"Two generated outputs")
    for name in ("stdout.log","stderr.log"):
        raw=store.read(root+"/"+name)
        same(case["log_sha256"][name],sha(raw),"Generation raw log "+name)
    store.read(data+"/generation.stdout")
    store.read(data+"/generation.stderr")
    sources={}
    for entry,(path,size,blob,expected_sha) in zip(report["inputs"],PINS):
        raw=store.read(data+"/inputs/"+path)
        same(len(raw),size,path+" complete source bytes")
        same(sha(raw),expected_sha,path+" actual source sha256")
        actual_blob=hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
        same(actual_blob,blob,path+" actual Git blob")
        repository="torvalds/linux" if path.startswith("primary-upstream/") else "OnePlusOSS/android_kernel_oneplus_sm8250"
        revision=UPSTREAM if path.startswith("primary-upstream/") else PRIMARY
        for key,value in (("path",path),("source_path",path.split("/",1)[1]),("repository",repository),
                          ("revision",revision),("bytes",size),("sha256",expected_sha),
                          ("expected_sha256",expected_sha),("git_blob_sha",blob),("expected_git_blob_sha",blob)):
            same(entry.get(key),value,path+" receipt "+key)
        sources[path]=raw.decode("utf-8")
    outputs={}
    for entry,(path,size,expected_sha) in zip(report["checks"],OUTPUT_PINS):
        raw=store.read(data+"/generated/"+path)
        same((len(raw),sha(raw)),(size,expected_sha),path+" actual generated bytes")
        for key,value in (("name",path),("passed",True),("generated_bytes",size),("committed_bytes",size),
                          ("generated_sha256",expected_sha),("committed_sha256",expected_sha)):
            same(entry.get(key),value,path+" generation receipt "+key)
        outputs[path]=raw
    # Parse the independent full-source table without executing the generator.
    header=sources["primary-r11/include/linux/soc/qcom/llcc-qcom.h"]
    ids={name:int(value,0) for name,value in re.findall(r"(?m)^#define\s+(LLCC_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|[0-9]+)\s*$",header)}
    kona=sources["primary-r11/drivers/soc/qcom/llcc-kona.c"]
    actual=[]
    for body in re.findall(r"SCT_ENTRY\((LLCC_[A-Z0-9_]+\s*,[^)]*)\)",kona):
        fields=[v.strip() for v in body.split(",")]
        actual.append((ids[fields[0]],)+tuple(int(v,0) for v in fields[1:]))
    same(actual,list(SCT),"Independent complete Kona source rows")
    table=b"".join(struct.pack("<IIIIB3xIIII4B",*row) for row in actual)
    same((len(table),sha(table)),(640,"1ae851c29537ec405b15db1bd151710c25ad0f063a74970253f9d16754dc5d55"),"Exact original production table fingerprint including padding")
    upstream=sources["primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi"]
    node=re.search(r"system-cache-controller@9200000\s*\{([^{}]+)\}",upstream,re.S)
    need(node is not None,"Upstream named-bank node")
    regs=re.search(r"\breg\s*=\s*(.*?);",node.group(1),re.S)
    names=re.search(r"\breg-names\s*=\s*(.*?);",node.group(1),re.S)
    same(re.findall(r'"([^"]+)"',names.group(1)),["llcc0_base","llcc1_base","llcc2_base","llcc3_base","llcc_broadcast_base"],"Explicit four-bank names")
    actual_cells=[[int(v,0) for v in re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b",body)] for body in re.findall(r"<([^>]+)>",regs.group(1))]
    same(actual_cells,[[0,a,0,0x50000] for a in (BASE,BASE+0x80000,BASE+0x100000,BASE+0x180000,BCAST)],"Explicit upstream bank geometry")
    manifest=json_value(outputs[OUTPUT_PINS[1][0]],"LLCC manifest")
    expected_contract={"source_input_count":8,"bank_count":4,"slice_count":16,"register_count":71,
                       "region_count":2,"mapped_register_count":349,"window_count":139,"mapped_bytes":1396}
    same(report["generated_contract"],expected_contract,"Generation inventory contract")
    for key,value in expected_contract.items():
        same(manifest[key],value,"Generated manifest "+key)
    same(manifest["source_init_slice_ids"],[1,13,31],"Independent init slices")
    same(manifest["source_final_masks"],{"CAD":0,"PCB":0x51c39806,"WRSC":0x1000},"Independent final flags")
    same(manifest["supported_sid_mask"],SID_MASK,"Independent supported SID mask")
    same([row["offset"] for row in manifest["registers"]],list(REGS),"Independent canonical word inventory")
    same([row["write_mask"] for row in manifest["registers"]],[mask(o) for o in REGS],"Reference policy write masks")
    for row,source in zip(manifest["slices"],SCT):
        same((row["slice_id"],row["attr0"],row["attr1"]),(source[1],*ATTRS[source[1]]),"Independent capacity/attribute arithmetic")
    actual_windows=[]
    for row in manifest["windows"]:
        base=BASE if row["region"]==0 else BCAST
        offset=row["offset"]-(row["bank"]*0x80000 if row["region"]==0 else 0)
        view=f"bank{row['bank']}" if row["region"]==0 else "broadcast"
        actual_windows.append((base+row["offset"],base+row["offset"]+row["size"]-1,f"{TYPE}.{view}.{offset:05x}"))
    same(actual_windows,expected_leaves(),"Independent generated physical leaves")
    receipt["source_generation"]={"case":case_path,"inputs":8,"outputs":2,"all_actual_bytes_and_git_blobs_verified":True,
        "source_table_bytes":640,"source_table_sha256":sha(table),"literal_source_capacity_arithmetic_verified":True,
        "four_named_banks_verified":True,"physical_bank_count_measured":False,
        "output_sha256":{path:expected_sha for path,size,expected_sha in OUTPUT_PINS}}


def hash_member(store,path):
    h,total=hashlib.sha256(),0
    with store.open(path) as stream:
        for chunk in iter(lambda:stream.read(CHUNK),b""):
            h.update(chunk)
            total+=len(chunk)
    same(total,store.size(path),path+" complete read")
    result={"bytes":total,"sha256":h.hexdigest()}
    store.observed[path]=result
    return result


def static_self_check():
    same((len(SIDS),len(REGS),len(expected_leaves())),(16,71,139),"Independent static inventory")
    same(sum(b-a+1 for a,b,n in expected_leaves()),1396,"Independent mapped bytes")
    for scenario in SCENARIOS:
        world=expected_world(scenario)
        check_world(world,scenario!="disabled",scenario,scenario_values(scenario)[0])
        raw=world_wire(world)
        same(len(raw),783,"Independent literal payload length")
        same(struct.unpack_from(">2Q",raw,52),(BASE,BCAST),"Independent fixed base offsets")
        same(struct.unpack_from(">2I",raw,68),(0x1d0000,0x50000),"Independent fixed size offsets")
        same(len(flat_world(world)),313,"Independent pure getter count")
    # A one-bit historical-credit violation must fail the independent oracle.
    bad=expected_world("steady")
    bad["written"]["21008"]=False
    try:
        check_world(bad,True,"negative historical credit",100000)
    except AuditFailure:
        pass
    else:
        raise AuditFailure("Independent active/written negative did not reject")


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip",type=Path,required=True)
    parser.add_argument("--expected-head",required=True)
    parser.add_argument("--expected-zip-sha256",required=True)
    parser.add_argument("--expected-qemu-sha256",required=True)
    parser.add_argument("--test-source",type=Path,required=True)
    parser.add_argument("--model-source",type=Path,required=True)
    parser.add_argument("--out",type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():
        raise SystemExit("Refusing to overwrite an existing audit receipt: "+str(args.out))
    receipt={"schema":AUDIT_SCHEMA,"passed":False,"observed_utc":datetime.now(timezone.utc).isoformat(),
             "auditor_sha256":file_hash(__file__),"head":args.expected_head,
             "read_only_archive":True,"qemu_executed":False,"guest_executed":False,
             "extracted_members":[],"test_serializer_imported":False,"model_imported":False,
             "whole_workflow_verdict_claimed":False,"native_android_passed":False,
             "shared_log_timestamp_claimed":False}
    store=None
    try:
        static_self_check()
        same(args.expected_head,HEAD,"Auditor bound exact source head")
        for value,width,name in ((args.expected_head,40,"head"),(args.expected_zip_sha256,64,"ZIP hash"),(args.expected_qemu_sha256,64,"QEMU hash")):
            need(re.fullmatch("[0-9a-f]{"+str(width)+"}",value) is not None,"Invalid "+name)
        same(file_hash(args.test_source),TEST_SHA,"Exact reviewed acceptance source")
        same(file_hash(args.model_source),MODEL_SHA,"Exact reviewed C source")
        compile(args.test_source.read_bytes(),str(args.test_source),"exec")
        receipt["reviewed_source_pins"]={"test":{"path":str(args.test_source.resolve()),"sha256":TEST_SHA},
                                          "model":{"path":str(args.model_source.resolve()),"sha256":MODEL_SHA}}
        same(file_hash(args.zip),args.expected_zip_sha256,"Actual complete ZIP SHA256")
        receipt["archive"]={"path":str(args.zip.resolve()),"bytes":args.zip.stat().st_size,"sha256":args.expected_zip_sha256}
        store=Store(zip_path=args.zip)
        qemu_path="qemu-build/qemu-system-aarch64"
        qemu=hash_member(store,qemu_path)
        same(qemu["sha256"],args.expected_qemu_sha256,"Actual complete QEMU SHA256")
        with store.open(qemu_path) as binary:
            header=binary.read(20)
        same(header[:6],b"\x7fELF\x02\x01","Actual QEMU ELF64 little-endian")
        receipt["qemu"]={"member":qemu_path,**qemu,"executed":False,
                         "host_elf_machine":int.from_bytes(header[18:20],"little")}
        data,report=select_case(store,args,receipt)
        audit_sources(store,receipt)
        audit_ordinary(store,data,report,receipt)
        audit_migrations(store,data,report,receipt)
        receipt["verified_members"]=dict(sorted(store.observed.items()))
        receipt["verified_member_count"]=len(store.observed)
        store.finish()
        store=None
        receipt["passed"]=True
    except BaseException as error:
        receipt["error"]=str(error)
        receipt["traceback"]=traceback.format_exc()
        if store is not None:
            receipt["verified_members"]=dict(sorted(store.observed.items()))
            receipt["verified_member_count"]=len(store.observed)
    finally:
        if store is not None:
            try:
                store.finish()
            except BaseException as error:
                receipt["archive_finish_error"]=str(error)
                receipt["passed"]=False
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open("x") as output:
        json.dump(receipt,output,indent=2)
        output.write("\n")
    print(json.dumps({"passed":receipt["passed"],"out":str(args.out),"receipt_sha256":file_hash(args.out),
                      "verified_member_count":receipt.get("verified_member_count",0),"error":receipt.get("error")}))
    return 0 if receipt["passed"] else 1


if __name__=="__main__":
    raise SystemExit(main())
