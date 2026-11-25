#!/usr/bin/env python3
"""Read-only independent DP artifact audit; never launches QEMU or a guest.

DP and MDSS wire data are compared against raw QOM replies using explicit
literal offsets. Migration files are read in bounded chunks without extracting
ZIP members. Cross-file timestamps are not invented: QMP ordering, QTest
sequences, actual counters and a pinned reviewed harness jointly establish
first-observation evidence. The caller audits the overall 91+5 gates and ELF.
"""
import argparse
import ast
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import traceback
from zipfile import ZipFile

AUDIT_SCHEMA = "tb371fc-dp-pll-independent-artifact-audit-v1"
PRIMARY = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
MODEL_SHA = "9a876a06787f1b847bf732801283a03963dd6a482fdcc5c57c0b77af8af860b2"
TEST_SHA = "1a04d27e40f81360a23c53dfa075f3baf80573912ba2e68d8aef859d0e482f14"
HEAD = "a3a6183d4e9842b2e61d3d1c9cd7b69b44165187"
TYPE, DEVICE = "qcom-sm8250-dp-pll", "/machine/dp-pll"
MDSS_TYPE, MDSS, DISPCC = "qcom-mdss-gdsc", "/machine/mdss-gdsc", "/machine/dispcc"
BASE, RAM = 0x088EA000, 0x80010000
U, XO = 1_000_000_000 << 32, 19_200_000
XO_PERIOD = U // XO
DP_BYTES, MDSS_BYTES, GETTERS = 791, 64, 304
CHUNK, MAX_SMALL = 1024 * 1024, 64 * 1024 * 1024

PLL_OFFSETS = (0x00c,0x044,0x048,0x04c,0x050,0x058,0x074,0x07c,0x084,0x094,
               0x09c,0x0a4,0x0ac,0x0b0,0x0bc,0x0cc,0x0d0,0x0d4,0x0ec,0x0f0,
               0x108,0x10c,0x140,0x154,0x158,0x168,0x174,0x178,0x17c,0x184)
PHY_OFFSETS = (0x010,0x014,0x018,0x01c,0x024,0x028,0x070,0x078,0x09c,0x0c8,0x0dc)
TX_OFFSETS = (0x008,0x00c,0x014,0x01c,0x020,0x024,0x02c,0x03c,0x040,0x054,
              0x058,0x05c,0x060,0x0b8,0x0bc,0x0e8)
REGIONS = (("pll",0x088EA000,PLL_OFFSETS),("phy",0x088EAA00,PHY_OFFSETS),
           ("tx0",0x088EA200,TX_OFFSETS),("tx1",0x088EA600,TX_OFFSETS))
KEYS = tuple(f"{name}.{o:03x}" for name,base,offsets in REGIONS for o in offsets)
STATUS = {"pll.140","pll.178","phy.0dc"}
EXTRA = {f"{tx}.{o:03x}" for tx in ("tx0","tx1") for o in (0xc,0x14,0x54,0x58,0x5c)}
REQUIRED = tuple(k for k in KEYS if k not in STATUS|EXTRA|{"pll.09c","phy.010"})
NAMES = ("dp_vco_clk","dp_link_clk_divsel_ten","dp_vco_divsel_two_clk_src",
         "dp_vco_divsel_four_clk_src","dp_vco_divsel_six_clk_src","dp_vco_divided_clk_src_mux")
CARRIERS = (NAMES[1],NAMES[5])
ROOTS = ("disp_cc_mdss_dp_link_clk_src","disp_cc_mdss_dp_pixel_clk_src")
GLOBALS = ("enabled-profile","reference-identity","region-count","register-count",
           "sparse-window-count","mapped-bytes","output-clock-count","input-clock-count",
           "timer-count","ccf-rate-scale","pll-delay-xo-cycles","phy-delay-xo-cycles",
           "resource-bytes-per-region")
GLOBAL_VALUES = (1,0x44503731,4,73,42,292,6,1,1,1000,64,64,0x200)
RUNTIME = ("power-known","power-on","phase","sequence-index","deadline-ns",
           "captured-xo-period","last-xo-period","captured-rate-khz","pll-locked",
           "phy-ready","pll-pending","phy-pending","late-phy-pending","recipe-valid",
           "raw-rate-known","raw-ccf-rate-khz","lane-count","orientation")
FLAGS = {"power-known","power-on","pll-locked","phy-ready","pll-pending",
         "phy-pending","late-phy-pending","recipe-valid","raw-rate-known"}
GDSC_GLOBALS = ("enabled-profile","reference-identity","domain-count","register-bytes",
                "mapped-bytes","parent-resource-index","cold-control",
                "control-write-mask","power-status-mask")
GDSC_VALUES = (1,0x4D445331,1,4,4,3,1,0xF803,0x80000000)
GDSC_FIELDS = ("name","address","dt-flags","timeout-us","control","request-known",
               "requested","state-known","powered","hw-request-known","hw-request")
SCENARIOS = ("steady", "pll-pending", "phy-pending", "late-phy-wait", "disabled")
COMPLETED = ("migration_steady_high_carriers", "migration_pending_pll",
             "migration_pending_phy", "migration_late_phy_wait")
CORRUPT = ("physical_base", "captured_recipe", "last_xo_period", "late_deadline")
ORDINARY = (
    "cold_identity_probe_reads_and_pure_observers",
    "four_rates_all_lane_orientation_literal_sequences",
    "explicit_required_zero_writes_are_not_reset_defaults",
    "unprogrammed_start_does_not_fabricate_lock",
    "pll_deadline_separates_internal_views_from_final_carriers",
    "phy_deadline_is_required_after_pll_lock",
    "skipped_or_early_enable_commands_cancel_qualification",
    "pending_recipe_change_cancels_without_late_resurrection",
    "live_required_change_revokes_but_same_value_preserves_capture",
    "mdss_power_loss_clears_written_but_retains_raw",
    "unknown_or_off_power_programming_cannot_qualify",
    "real_rpmh_xo_votes_revoke_pending_and_ready_captures",
    "live_pixel_mux_all_encodings_and_high_carrier_boundary",
    "raw_ccf_units_and_unsupported_selectors_do_not_grant_clocks",
    "tx_aliases_are_single_words_and_banks_are_independent",
    "catalog_tx_storage_does_not_retune_or_revoke_capture",
    "readonly_status_and_reserved_bits_reject_atomically",
    "exact_sparse_leaves_holes_widths_and_shared_owner",
    "two_real_dispcc_carriers_use_source_names_and_selectors",
    "connected_reference_and_state_observers_are_readonly",
    "system_reset_cancels_transaction_and_replays_mdss_collapse",
    "late_phy_wait_enforces_two_ms_and_rejects_early_restart",
    "invalid_lane_orientation_and_mixed_rate_recipe_never_lock",
    "polling_cannot_advance_or_replace_the_finite_timer",
)
CHECK_NAMES = ORDINARY + (
    "default_off_rejects_all_words_with_sibling_profiles_enabled",
    "paused_tcg_has_no_mutating_fixture_or_guest_execution",
) + COMPLETED + ("profile_on_to_off_rejected", "profile_off_to_on_rejected") + tuple(
    "single_bit_" + name + "_rejected" for name in CORRUPT)

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


def mdss_wire(snapshot):
    raw, domain = bytearray(MDSS_BYTES), snapshot["domains"][0]
    encode_at(raw, 0, "I", [snapshot["globals"][n] for n in GDSC_GLOBALS], "mdss globals")
    encode_at(raw, 36, "B", [snapshot["parent-known"], snapshot["parent-on"]], "mdss parents", bools=True)
    encode_at(raw, 38, "I", [0], "mdss domain id")
    encode_at(raw, 42, "Q", [domain["address"]], "mdss address")
    encode_at(raw, 50, "I", [domain["dt-flags"], domain["timeout-us"], domain["control"]], "mdss domain")
    encode_at(raw, 62, "B", [domain["hw-request-known"], domain["hw-request"]], "mdss hardware request", bools=True)
    return bytes(raw)


def scan_stream(store, path):
    """Hash the whole stream, locate real full sections and check their footers."""
    contracts = (("dp", TYPE, DP_BYTES), ("mdss", MDSS_TYPE, MDSS_BYTES))
    needles = {label: bytes([len(name)]) + name.encode("ascii") + struct.pack(">II", 0, 1)
               for label, name, size in contracts}
    candidates = {label: {} for label, name, size in contracts}
    h, total, tail, magic = hashlib.sha256(), 0, b"", b""
    with store.open(path) as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            if not magic:
                magic = chunk[:8]
            h.update(chunk)
            window, start = tail + chunk, total - len(tail)
            for label, name, size in contracts:
                needle, at = needles[label], 0
                while True:
                    at = window.find(needle, at)
                    if at < 0:
                        break
                    header, payload = at - 5, at + len(needle)
                    if header >= 0 and start + header >= 8 and window[header] in (1, 4):
                        absolute = start + header
                        entry = candidates[label].setdefault(absolute, None)
                        end = payload + size
                        if end + 5 <= len(window):
                            section_id = struct.unpack_from(">I", window, header + 1)[0]
                            footer = b"\x7e" + struct.pack(">I", section_id)
                            same(window[end:end + 5], footer, path + "." + label + " complete footer")
                            raw = bytes(window[payload:end])
                            info = {"name": name, "instance_id": 0, "version": 1,
                                    "header_offset": absolute, "section_id": section_id,
                                    "payload_offset": start + payload, "payload_bytes": size,
                                    "footer_offset": start + end, "footer_hex": footer.hex()}
                            if entry is not None:
                                same(entry, (info, raw), path + ": unstable overlapping scan")
                            candidates[label][absolute] = (info, raw)
                    at += 1
            total += len(chunk)
            tail = window[-4096:]
    same(magic, b"QEVM\x00\x00\x00\x03", path + ".QEMU magic/version")
    same(total, store.size(path), path + ".actual full stream bytes")
    result = {"bytes": total, "sha256": h.hexdigest(), "sections": {}, "payloads": {}}
    for label, name, size in contracts:
        found = candidates[label]
        same(len(found), 1, path + "." + label + " unique section")
        item = next(iter(found.values()))
        need(item is not None, path + ": truncated section")
        info, raw = item
        result["sections"][label], result["payloads"][label] = info, raw
    store.observed[path] = {"bytes": total, "sha256": result["sha256"]}
    return result


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


def register_mask(key):
    if key in STATUS:
        return 0
    return {"phy.010": 0x1f, "phy.014": 0x0f, "phy.018": 0x7f,
            "phy.01c": 0x7f, "phy.070": 3}.get(key, 0xff)


def source_recipe(words, written):
    """Source literal values; independent of the generated table and test code."""
    spare = words["phy.0c8"]
    lanes, orientation = spare & 15, spare >> 4
    if lanes not in (1, 2, 4) or orientation not in (1, 2):
        return 0
    if not all(written[k] for k in REQUIRED):
        return 0
    fixed = {
        "pll.00c": 10, "pll.044": 0x17, "pll.048": 0x0c, "pll.04c": 2,
        "pll.050": 6, "pll.058": 15, "pll.074": 6, "pll.07c": 0x16,
        "pll.084": 0x36, "pll.094": 0x3b, "pll.0cc": 0, "pll.0ec": 0x3f,
        "pll.0f0": 0, "pll.108": 0, "pll.10c": 0, "pll.154": 0x30,
        "pll.168": 10, "pll.174": 0x1f, "pll.17c": 2, "pll.184": 5,
        "phy.014": 15, "phy.024": 0x13, "phy.028": 0xa4,
        "phy.078": 5, "phy.09c": 5,
    }
    for tx in ("tx0", "tx1"):
        fixed.update({f"{tx}.{o:03x}": value for o, value in
                      ((8, 15), (0x1c, 3), (0x20, 0x30), (0x24, 4), (0x2c, 0x3b),
                       (0x3c, 0x11), (0x40, 0x11), (0x60, 0), (0xb8, 15),
                       (0xbc, 0), (0xe8, 0x40))})
    same(len(fixed), 47, "Independent fixed recipe cardinality")
    if any(words[k] != value for k, value in fixed.items()):
        return 0
    if words["phy.01c"] != (0x5c if orientation == 1 else 0x4c):
        return 0
    if words["phy.018"] != (0x7d if lanes == 4 else 0x75 if orientation == 1 else 0x6d):
        return 0
    # Seven varying registers, deliberately ordered by physical address here.
    fields = ("pll.0a4", "pll.0ac", "pll.0b0", "pll.0bc",
              "pll.0d0", "pll.0d4", "pll.158")
    cases = (
        (1620000, (4, 0x6f, 0x08, 0x69, 0x80, 7, 5)),
        (2700000, (8, 0x0f, 0x0e, 0x69, 0x80, 7, 3)),
        (5400000, (8, 0x1f, 0x1c, 0x8c, 0x00, 10, 1)),
        (8100000, (8, 0x2f, 0x2a, 0x69, 0x80, 7, 0)),
    )
    for rate, values in cases:
        if tuple(words[k] for k in fields) == values:
            return rate
    return 0


def bounded_raw_rate(words):
    hs, aux = words["pll.158"] & 15, (words["phy.028"] >> 2) & 3
    rates = {5: 1620000, 3: 2700000, 1: 5400000, 0: 8100000}
    if hs not in rates or aux == 3:
        return False, 0
    return True, 2700000 if aux == 2 else rates[hs]


def dp_wire(snapshot):
    """Place each field at an independently specified absolute wire offset."""
    raw = bytearray(DP_BYTES)
    encode_at(raw, 0, "I", [snapshot["globals"][k] for k in GLOBALS], "DP metadata")
    encode_at(raw, 52, "Q", snapshot["region_bases"], "DP physical bases")
    encode_at(raw, 84, "I", [snapshot["registers"][k] for k in KEYS], "DP raw words")
    encode_at(raw, 376, "B", [snapshot["written"][k] for k in KEYS], "DP written flags", bools=True)
    encode_at(raw, 449, "I", [snapshot["captured"][k] for k in KEYS], "DP full capture")
    state = snapshot["runtime"]
    encode_at(raw, 741, "Q", [state["captured-xo-period"]], "captured XO")
    encode_at(raw, 749, "Q", [state["last-xo-period"]], "last XO")
    encode_at(raw, 757, "q", [state["deadline-ns"]], "virtual deadline")
    encode_at(raw, 765, "I", [state["phase"]], "phase")
    encode_at(raw, 769, "I", [state["sequence-index"]], "source sequence")
    encode_at(raw, 773, "Q", [state["captured-rate-khz"]], "captured kHz")
    encode_at(raw, 781, "B", [state["power-known"], state["power-on"]], "power pins", bools=True)
    encode_at(raw, 783, "Q", [snapshot["input_period"]], "input Clock period")
    return bytes(raw)


def wire_fields(raw):
    same(len(raw), DP_BYTES, "Complete DP payload length")
    fields = ((0, 13, "I", "globals"), (52, 4, "Q", "physical_bases"),
              (84, 73, "I", "raw_words"), (376, 73, "B", "written"),
              (449, 73, "I", "captured"), (741, 1, "Q", "captured_xo"),
              (749, 1, "Q", "last_xo"), (757, 1, "q", "deadline"),
              (765, 1, "I", "phase"), (769, 1, "I", "sequence"),
              (773, 1, "Q", "rate_khz"), (781, 2, "B", "power"),
              (783, 1, "Q", "input_clock_period"))
    result, end = {}, 0
    for offset, count, code, name in fields:
        same(offset, end, "No omitted or overlapping DP wire fields")
        result[name] = list(struct.unpack_from(">" + str(count) + code, raw, offset))
        end += count * struct.calcsize(">" + code)
    same(end, DP_BYTES, "All 791 DP payload bytes covered")
    return result


def check_world(world, profile, where, virtual_ns=None):
    same(set(world), {"dp", "mdss", "dispcc_carrier_periods", "dispcc_roots"}, where + ".world fields")
    s, m = world["dp"], world["mdss"]
    same(set(s), {"globals", "region_bases", "registers", "written", "captured",
                  "runtime", "input_period", "input_hz", "outputs", "output_hz",
                  "physical_outputs", "physical_input_period"}, where + ".DP fields")
    expected = dict(zip(GLOBALS, GLOBAL_VALUES))
    expected["enabled-profile"] = int(profile)
    same(s["globals"], expected, where + ".DP fixed metadata")
    same(s["region_bases"], [r[1] for r in REGIONS], where + ".four canonical bases")
    for group in ("registers", "written", "captured"):
        same(set(s[group]), set(KEYS), where + "." + group + " key inventory")
        for key in KEYS:
            value = s[group][key]
            if group == "written":
                boolean(value, where + ".written." + key)
                if key in STATUS:
                    same(value, False, where + ".status cannot have write credit")
            else:
                uint(value, 32, where + "." + group + "." + key)
                need(value & ~register_mask(key) == 0, where + ".unsupported stored bits " + key)
    rt, words, captured, written = s["runtime"], s["registers"], s["captured"], s["written"]
    same(set(rt), set(RUNTIME), where + ".runtime fields")
    for key in RUNTIME:
        if key in FLAGS:
            boolean(rt[key], where + "." + key)
        else:
            uint(rt[key], 64, where + "." + key, signed=key == "deadline-ns")
    input_period = uint(s["input_period"], 64, where + ".actual XO period")
    same(s["physical_input_period"], input_period, where + ".physical input Clock")
    same(s["input_hz"], U // input_period if input_period else 0, where + ".observed XO Hz")
    same(rt["last-xo-period"], input_period, where + ".last XO must track actual input")
    phase, sequence = rt["phase"], rt["sequence-index"]
    need(phase in range(6) and sequence in range(5), where + ": invalid transition encoding")
    if not (rt["power-known"] and rt["power-on"]):
        same(list(written.values()), [False] * 73, where + ".unpowered words cannot retain qualification")
    if not profile:
        same(list(words.values()), [0] * 73, where + ".disabled raw state")
        same(list(written.values()), [False] * 73, where + ".disabled qualification")
        same(phase, 0, where + ".disabled idle")
    recipe = source_recipe(words, written)
    same(rt["recipe-valid"], recipe != 0, where + ".literal recipe validity")
    raw_known, raw_rate = bounded_raw_rate(words)
    same(rt["raw-rate-known"], raw_known, where + ".bounded raw rate known")
    same(rt["raw-ccf-rate-khz"], raw_rate, where + ".bounded raw CCF kHz")
    same(rt["lane-count"], words["phy.0c8"] & 15, where + ".raw lanes")
    same(rt["orientation"], words["phy.0c8"] >> 4, where + ".raw orientation")
    for flag, truth in (("pll-locked", phase in (2, 3, 4, 5)),
                        ("phy-ready", phase == 4), ("pll-pending", phase == 1),
                        ("phy-pending", phase == 3), ("late-phy-pending", phase == 5)):
        same(rt[flag], truth, where + "." + flag)
    supplied = bool(profile and rt["power-known"] and rt["power-on"] and input_period == XO_PERIOD)
    if phase == 0:
        same(rt["deadline-ns"], -1, where + ".idle deadline")
        same(rt["captured-xo-period"], 0, where + ".idle captured XO")
        same(rt["captured-rate-khz"], 0, where + ".idle captured rate")
        same(list(captured.values()), [0] * 73, where + ".idle full capture")
        if sequence:
            need(supplied and written["phy.010"], where + ": unsupported partial source sequence")
            same(words["phy.010"], {1: 1, 2: 5, 3: 1, 4: 9}[sequence], where + ".partial sequence CFG")
    else:
        need(supplied and recipe, where + ": unqualified active transaction")
        same(rt["captured-xo-period"], XO_PERIOD, where + ".captured source period")
        same(rt["captured-rate-khz"], recipe, where + ".captured reference kHz")
        same(source_recipe(captured, written), recipe, where + ".captured source recipe")
        same(sequence, 4, where + ".complete source command sequence")
        same(captured["phy.010"], 9, where + ".capture was taken at CFG9")
        same(captured["pll.09c"], 0x20, where + ".capture was taken at RESET20")
        need(written["phy.010"] and written["pll.09c"], where + ": command write credit missing")
        same(words["pll.09c"], 0x20, where + ".current reset command")
        for key in REQUIRED:
            if key != "phy.070":
                same(words[key], captured[key], where + ".required capture consistency " + key)
        allowed_cfg = {1: {9}, 2: {9, 0x18}, 3: {0x19}, 4: {0x19}, 5: {0x18}}[phase]
        need(words["phy.010"] in allowed_cfg, where + ": phase/CFG mismatch")
        if phase in (1, 3, 5):
            need(rt["deadline-ns"] >= 0, where + ": missing pending deadline")
            if virtual_ns is not None:
                remaining = rt["deadline-ns"] - virtual_ns
                need(0 <= remaining <= (2000000 if phase == 5 else 3334),
                     where + ": pending deadline exceeds its source/reference budget")
        else:
            same(rt["deadline-ns"], -1, where + ".settled transaction deadline")
    hz = [0] * 6
    if phase in (2, 3, 4, 5):
        hs = rt["captured-rate-khz"] * 1000
        hz[0], hz[2], hz[3], hz[4] = hs, hs // 2, hs // 4, hs // 6
        if phase == 4:
            hz[1] = hs // 10
            divisor = {1: 2, 2: 4, 0: 6}.get(words["phy.070"])
            hz[5] = hs // divisor if divisor else 0
    periods = {name: U // value if value else 0 for name, value in zip(NAMES, hz)}
    same(s["outputs"], periods, where + ".six computed output periods")
    same(s["physical_outputs"], periods, where + ".six physical Clock periods")
    same(s["output_hz"], {name: U // p if p else 0 for name, p in periods.items()},
         where + ".six quantized observed Hz values")
    same(world["dispcc_carrier_periods"], {name: periods[name] for name in CARRIERS},
         where + ".two physically connected DISPCC inputs")
    same(set(world["dispcc_roots"]), set(ROOTS), where + ".DISPCC root inventory")
    for name, state in world["dispcc_roots"].items():
        same(set(state), {"period", "hz"}, where + ".DISPCC root fields")
        p = uint(state["period"], 64, where + ".DISPCC root period")
        same(state["hz"], U // p if p else 0, where + ".DISPCC quantized Hz")
    same(set(m), {"enabled-reference", "globals", "parent-known", "parent-on", "domains"},
         where + ".MDSS fields")
    same(m["enabled-reference"], True, where + ".MDSS reference supplier")
    same(m["globals"], dict(zip(GDSC_GLOBALS, GDSC_VALUES)), where + ".MDSS fixed metadata")
    need(type(m["domains"]) is list and len(m["domains"]) == 1, where + ": MDSS domain count")
    for key in ("parent-known", "parent-on"):
        boolean(m[key], where + ".MDSS." + key)
    d = m["domains"][0]
    same(set(d), set(GDSC_FIELDS), where + ".MDSS domain fields")
    for key, value in (("name", "mdss-core"), ("address", 0x0AF03000), ("dt-flags", 3), ("timeout-us", 100)):
        same(d[key], value, where + ".MDSS." + key)
    control = uint(d["control"], 32, where + ".MDSS control")
    need(control & ~0xf803 == 0, where + ".MDSS reserved control bits")
    for key in GDSC_FIELDS[5:]:
        boolean(d[key], where + ".MDSS." + key)
    requests = ({d["hw-request"]} if d["hw-request-known"] else {False, True}) if control & 2 else {not bool(control & 1)}
    parents = {m["parent-on"]} if m["parent-known"] else {False, True}
    states = {r and p for r in requests for p in parents}
    for key, value in (("request-known", len(requests) == 1), ("requested", requests == {True}),
                       ("state-known", len(states) == 1), ("powered", states == {True})):
        same(d[key], value, where + ".MDSS derived " + key)
    same(rt["power-known"], d["state-known"], where + ".real MDSS power-known")
    same(rt["power-on"], d["powered"], where + ".real MDSS power-on")
    return {"dp_payload_sha256": sha(dp_wire(s)), "mdss_payload_sha256": sha(mdss_wire(m)),
            "dp_complete_bytes": DP_BYTES, "mdss_complete_bytes": MDSS_BYTES,
            "physical_clock_periods": 6, "connected_dispcc_inputs": 2,
            "source_recipe_and_model_state_invariants": True}


def flat_world(world):
    s, m, result = world["dp"], world["mdss"], []
    def add(path, prop, value):
        result.append(({"path": path, "property": prop}, value))
    for name in GLOBALS:
        add(DEVICE, name, s["globals"][name])
    for region, value in enumerate(s["region_bases"]):
        add(DEVICE, f"region{region}.physical-base", value)
    for group, prefix in (("registers", "reg"), ("written", "written"), ("captured", "captured")):
        for key in KEYS:
            add(DEVICE, prefix + "." + key, s[group][key])
    for name in RUNTIME:
        add(DEVICE, name, s["runtime"][name])
    for key, prop in (("input_period", "period"), ("input_hz", "hz")):
        add(DEVICE, "input.ref_clk_src." + prop, s[key])
    for group, suffix in (("outputs", "period"), ("output_hz", "hz")):
        for name in NAMES:
            add(DEVICE, "clk." + name + "." + suffix, s[group][name])
    for name in NAMES:
        add(DEVICE + "/" + name, "qtest-clock-period", s["physical_outputs"][name])
    add(DEVICE + "/ref_clk_src", "qtest-clock-period", s["physical_input_period"])
    add(MDSS, "enabled-reference", m["enabled-reference"])
    for name in GDSC_GLOBALS:
        add(MDSS, name, m["globals"][name])
    for name in ("parent-known", "parent-on"):
        add(MDSS, name, m[name])
    for name in GDSC_FIELDS:
        add(MDSS, "domain0." + name, m["domains"][0][name])
    for name in CARRIERS:
        add(DISPCC + "/" + name, "qtest-clock-period", world["dispcc_carrier_periods"][name])
    for name in ROOTS:
        add(DISPCC + "/" + name, "qtest-clock-period", world["dispcc_roots"][name]["period"])
        add(DISPCC, "clk." + name + ".hz", world["dispcc_roots"][name]["hz"])
    same(len(result), GETTERS, "Independently enumerated complete first getter sequence")
    return result


def is_mmio(call):
    parts = call["command"].split()
    if not parts or parts[0] not in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq"):
        return False
    address = int(parts[1], 0)
    camera = (0xAD07004, 0xAD08004, 0xAD09004, 0xAD0A004, 0xAD0B004, 0xAD0C144)
    return (BASE <= address < BASE + 0xc00 or 0xAF00000 <= address < 0xAF20000 or
            0x100000 <= address < 0x2F0000 or 0x18200000 <= address < 0x18230000 or
            any(base <= address < base + 4 for base in camera))


def command_contract(store, directory, report, profile, incoming=False, rpmh=False):
    command = store.json(directory + "/command.json")
    need(type(command) is list and all(type(x) is str for x in command), directory + ": invalid argv")
    same(command[0], report["qemu"], directory + ".actual executable path")
    same(command.count("-S"), 1, directory + ".stopped startup")
    for forbidden in ("-kernel", "-initrd", "-append", "-drive", "-blockdev", "-device"):
        need(forbidden not in command, directory + ": unexpected guest input " + forbidden)
    def value(option):
        same(command.count(option), 1, directory + "." + option + " count")
        return command[command.index(option) + 1]
    same(value("-accel"), "qtest", directory + ".accelerator")
    same(value("-m"), "128M", directory + ".memory")
    same(value("-smp"), "8", directory + ".vCPU count")
    machine = value("-machine").split(",")
    same(machine[0], "tb371fc", directory + ".board")
    need(("rpmh-profile=" + ("reference" if rpmh else "off")) in machine and
         "rpmh-boot-configured=off" in machine and "ufs-enabled=off" in machine and
         "gcc-boot-configured=off" in machine, directory + ": board profile differs")
    profiles = [command[i + 1] for i, x in enumerate(command) if x == "-global"]
    same(profiles, [MDSS_TYPE + ".enabled-reference=on",
                    "qcom-sm8250-dispcc.enabled-reference=on",
                    TYPE + ".enabled-reference=" + ("on" if profile else "off")],
         directory + ".explicit reference profiles")
    same("-incoming" in command, incoming, directory + ".incoming option")
    if incoming:
        same(value("-incoming"), "defer", directory + ".deferred incoming")
    value("-qtest")
    value("-qmp")
    return store.text(directory + "/qemu.stderr")


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


def output_queries(rate=0, locked=False, ready=False, mux=0):
    """Literal physical rates, followed by the actual period/Clock/Hz getters."""
    hs, values = rate * 1000, [0] * 6
    if locked:
        values[0], values[2], values[3], values[4] = hs, hs // 2, hs // 4, hs // 6
    if ready:
        need(locked, "Cannot audit PHY-ready without PLL lock")
        values[1] = hs // 10
        divisor = {1: 2, 2: 4, 0: 6}.get(mux)
        values[5] = hs // divisor if divisor else 0
    result = []
    for name, hz in zip(NAMES, values):
        p = U // hz if hz else 0
        result.extend((({"path": DEVICE, "property": "clk." + name + ".period"}, p),
                       ({"path": DEVICE + "/" + name, "property": "qtest-clock-period"}, p),
                       ({"path": DEVICE, "property": "clk." + name + ".hz"}, U // p if p else 0)))
    for name in CARRIERS:
        hz = values[NAMES.index(name)]
        result.append(({"path": DISPCC + "/" + name, "property": "qtest-clock-period"}, U // hz if hz else 0))
    return result


def target_trace(store, directory, saved, completed, report):
    stderr = command_contract(store, directory, report, True, incoming=True)
    need("load of migration failed" not in stderr, directory + ": successful target load failed")
    before = store.json(directory + "/destination-before-incoming.json")
    first = store.json(directory + "/destination-before-first-device-mmio.json")
    same(first, completed["first_target_observation"], directory + ".saved/reported first observation")
    same(first["snapshot"], saved["snapshot"], directory + ".complete first target/source state")
    same(before["source_epoch_ns"], saved["source_virtual_ns"], directory + ".stale target epoch")
    same(before["stale_pending_phase"], 1, directory + ".stale target pending work")
    need(before["snapshot"] != first["snapshot"] and before["snapshot"]["mdss"] != first["snapshot"]["mdss"],
         directory + ": target was not independently dirtied")
    check_world(before["snapshot"], True, directory + ".stale target", saved["source_virtual_ns"])
    stale = before["snapshot"]["dp"]["runtime"]
    for key, value in (("phase", 1), ("captured-rate-khz", 1620000), ("lane-count", 1), ("orientation", 2),
                       ("deadline-ns", saved["source_virtual_ns"] + 3334)):
        same(stale[key], value, directory + ".independent prewarm " + key)
    same(before["snapshot"]["mdss"]["domains"][0]["control"], 0x1800, directory + ".dirty actual MDSS supplier")
    for key in ("device_mmio_since_incoming", "qmp_cont_since_incoming", "guest_cpu_executed", "shared_log_timestamp_claimed"):
        same(first[key], False, directory + "." + key)
    same(first["first_observation_qom_get_count"], GETTERS, directory + ".first getter count")
    same(first["device_mmio_count_before_incoming"], first["device_mmio_count_after_first_observation"], directory + ".MMIO delta")
    same(first["device_mmio_count_before_incoming"], before["device_mmio_count"], directory + ".prewarm MMIO count")
    same(first["qmp_cont_count_before_incoming"], 0, directory + ".no cont before incoming")
    same(first["qmp_cont_count_after_first_observation"], 0, directory + ".no cont before first observation")
    same(before["qmp_cont_count"], 0, directory + ".stale target cont count")
    calls = qmp_calls(store, directory)
    incoming = [i for i, c in enumerate(calls) if operation(c) == "migrate-incoming"]
    same(len(incoming), 1, directory + ".actual incoming count")
    ix = incoming[0]
    migration_argument(calls[ix], saved["file"])
    reply(calls[ix])
    qom_block(calls[ix-GETTERS:ix], flat_world(before["snapshot"]), directory + ".stale QMP snapshot")
    same(sum(operation(c) == "qom-get" for c in calls[:ix]), before["qom_get_count"], directory + ".actual prewarm getter count")
    same(sum(operation(c) == "cont" for c in calls[:ix]), 0, directory + ".no raw pre-incoming cont")
    at = ix + 1
    while at < len(calls) and operation(calls[at]) == "query-migrate":
        state = reply(calls[at])
        need(type(state) is dict and state.get("status") not in ("failed", "cancelled"), directory + ": incoming failed")
        if state.get("status") == "completed":
            break
        at += 1
    need(at < len(calls) and operation(calls[at]) == "query-migrate", directory + ": missing incoming completion")
    same(reply(calls[at]), first["query_migrate"], directory + ".actual saved completed response")
    same(reply(calls[at]).get("status"), "completed", directory + ".incoming status")
    same(operation(calls[at+1]), "query-status", directory + ".first status query")
    same(reply(calls[at+1]).get("running"), False, directory + ".incoming remained stopped")
    start, end = at+2, at+2+GETTERS
    qom_block(calls[start:end], flat_world(first["snapshot"]), directory + ".complete pure first target QOM")
    actual_before = sum(operation(c) == "qom-get" for c in calls[:start])
    same(first["qom_get_count_before_first_observation"], actual_before, directory + ".getter counter before")
    same(first["qom_get_count_after_first_observation"], actual_before+GETTERS, directory + ".getter counter after")
    same(operation(calls[end]), "cont", directory + ".first cont follows all pure getters")
    reply(calls[end])
    same(sum(operation(c) == "cont" for c in calls[:end]), 0, directory + ".raw QMP proves no preceding cont")
    need(not any(operation(c) in ("qom-set", "device_add", "human-monitor-command") for c in calls), directory + ": mutating QMP bypass")
    qt = qtest_calls(store, directory)
    qtest_time(qt, directory)
    reads = [i for i, c in enumerate(qt) if c["command"].split() == ["readl", hex(RAM)]]
    same(len(reads), 1, directory + ".one restored RAM witness read")
    ram_read = reads[0]
    same(int(qt[ram_read]["reply"].split()[1], 0), saved["ram_witness"], directory + ".actual RAM witness")
    same(first["ram_witness"], saved["ram_witness"], directory + ".RAM witness receipt")
    sets = [i for i, c in enumerate(qt[:ram_read]) if c["command"].split() == ["clock_set", str(saved["source_virtual_ns"])]]
    same(len(sets), 2, directory + ".prewarm/receive two actual epoch markers")
    boundary = sets[-1]
    same(boundary+1, ram_read, directory + ".no QTest commands during pure first observation")
    count_before, count_after = sum(is_mmio(c) for c in qt[:boundary]), sum(is_mmio(c) for c in qt[:ram_read])
    same(count_before, first["device_mmio_count_before_incoming"], directory + ".actual MMIO before incoming")
    same(count_after, first["device_mmio_count_after_first_observation"], directory + ".actual MMIO before RAM witness")
    same(qt[boundary]["virtual_ns_before"], saved["source_virtual_ns"], directory + ".target source epoch")
    dirty = [c for c in qt[:boundary] if c["command"].split()[:2] == ["writel", hex(RAM)]]
    same(len(dirty), 1, directory + ".one dirty target witness")
    same(int(dirty[0]["command"].split()[2], 0), saved["ram_witness"] ^ 0xffffffff, directory + ".dirty witness value")
    # Every post-continue output block is read from the raw QMP transcript.
    # The first output getter cannot be skipped to a later matching snapshot.
    scenario = saved["file"][3:-10]
    expected_blocks = {
        "steady": ((8100000, True, True, 1), (0, False, False, 0)),
        "pll-pending": ((0, False, False, 0), (5400000, True, False, 2), (5400000, True, True, 2)),
        "phy-pending": ((2700000, True, False, 1), (2700000, True, True, 1), (0, False, False, 0)),
        "late-phy-wait": ((5400000, True, False, 2), (5400000, True, False, 2), (5400000, True, True, 2)),
    }[scenario]
    expected_start = {"path": DEVICE, "property": "clk."+NAMES[0]+".period"}
    starts = [i for i in range(end+1, len(calls)) if operation(calls[i]) == "qom-get" and calls[i]["request"].get("arguments") == expected_start]
    same(len(starts), len(expected_blocks), directory + ".all actual post-restore output blocks")
    for number, (offset, values) in enumerate(zip(starts, expected_blocks)):
        queries = output_queries(*values)
        # ready checks without an explicit mux read the live mux once first.
        qom_block(calls[offset:offset+len(queries)], queries, directory + f".post-restore output block {number}")
    steps = [int(c["command"].split()[1], 0) for c in qt[ram_read+1:] if c["command"].startswith("clock_step ")]
    expected_steps = {"steady": [], "pll-pending": [2333, 1, 3334], "phy-pending": [2333, 1],
                      "late-phy-wait": [1499999, 1, 3333, 1]}
    same(steps, expected_steps[scenario], directory + ".actual remaining deadline steps")
    phase_values = [reply(c) for c in calls[end+1:] if operation(c) == "qom-get" and c["request"].get("arguments") == {"path": DEVICE, "property": "phase"}]
    same(phase_values, {"steady": [0], "pll-pending": [1, 2], "phy-pending": [3, 4, 0], "late-phy-wait": [5, 2, 3]}[scenario], directory + ".actual post-restore phases")
    if scenario == "phy-pending":
        # The source suite observes each of the 73 credit bits after real MDSS loss.
        getters = [(c["request"].get("arguments"), reply(c)) for c in calls[end+1:] if operation(c) == "qom-get"]
        for key in KEYS:
            matches = [value for args, value in getters if args == {"path": DEVICE, "property": "written."+key}]
            same(matches, [False], directory + ".power-loss erased actual credit " + key)
    return {"qmp_first_observation_getters": GETTERS, "all_first_values_match_stopped_source": True,
            "actual_mmio_count_before_incoming": count_before, "actual_mmio_count_before_first_ram_read": count_after,
            "actual_mmio_delta": count_after-count_before, "actual_qmp_cont_before_observation": 0,
            "qmp_sequence_completed_status_then_getters_then_cont": True, "actual_ram_witness_matches": True,
            "qtest_trace_has_no_intervening_commands": True, "shared_timestamp_inferred": False,
            "actual_post_restore_output_blocks": len(starts), "physical_clocks_per_block": 6,
            "actual_post_restore_deadline_steps_ns": steps, "actual_post_restore_phases": phase_values}


def scenario_contract(saved, scenario):
    same(saved["source_directory"], "migration-"+scenario+"-source", scenario+".source directory")
    same(saved["file"], "dp-"+scenario+".migration", scenario+".source filename")
    same(saved["stream_modified"], False, scenario+".unmodified source")
    same(saved["source_status"], "completed", scenario+".actual source completion")
    same(saved["source_query_migrate"].get("status"), "completed", scenario+".source query-migrate")
    epoch, phase, deadline, rate, lanes, orientation, mux, witness = {
        "steady": (108668, 4, -1, 8100000, 4, 1, 1, 0x44503034),
        "pll-pending": (201000, 1, 203334, 5400000, 2, 2, 2, 0x44503031),
        "phy-pending": (304334, 3, 306668, 2700000, 1, 1, 1, 0x44503033),
        "late-phy-wait": (900000, 5, 2400000, 5400000, 4, 1, 2, 0x44503035),
        "disabled": (1000000, 0, -1, 0, 0, 0, 0, 0x44503030),
    }[scenario]
    same(saved["source_virtual_ns"], epoch, scenario+".fixed source epoch")
    same(saved["ram_witness"], witness, scenario+".distinct RAM witness")
    world, state = saved["snapshot"], saved["snapshot"]["dp"]
    for key, value in (("phase", phase), ("deadline-ns", deadline), ("captured-rate-khz", rate),
                       ("lane-count", lanes), ("orientation", orientation)):
        same(state["runtime"][key], value, scenario+".source "+key)
    same(state["registers"]["phy.070"], mux, scenario+".live source mux")
    same(state["input_period"], XO_PERIOD, scenario+".source exact XO")
    for name in ("power-known", "power-on"):
        same(state["runtime"][name], True, scenario+".actual MDSS "+name)
    same(world["mdss"]["domains"][0]["control"], 0, scenario+".source MDSS enabled control")
    for root, carrier in zip(ROOTS, CARRIERS):
        parent_period = state["outputs"][carrier]
        hz = U // parent_period if scenario == "steady" and parent_period else 0
        p = U // hz if hz else 0
        same(world["dispcc_roots"][root], {"period": p, "hz": U // p if p else 0}, scenario+".real DISPCC root "+root)


def report_contract(report, expected_qemu):
    fields = {
        "schema": "tb371fc-dp-pll-7nm-v1", "suite": "dp-pll-7nm-reference",
        "test_script_sha256": TEST_SHA, "expected_checks": 36,
        "expected_completed_migrations": 4, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 4, "expected_unmodified_streams": 5,
        "expected_stream_files": 9, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": 791, "reference_identity": 0x44503731,
        "source_variant": "qcom,mdss_dp_pll_7nm", "physical_base": BASE,
        "region_count": 4, "resource_bytes_per_region": 512,
        "register_count": 73, "sparse_window_count": 42, "mapped_bytes": 292,
        "output_clock_count": 6, "input_clock_count": 1, "connected_input_clock_count": 1,
        "dispcc_carrier_connection_count": 2, "timer_count": 1, "ccf_rate_scale": 1000,
        "required_programmed_words": 58, "readonly_status_words": 3,
        "pll_reference_cycles": 64, "phy_reference_cycles": 64,
        "pll_reference_ns_ceiling": 3334, "phy_reference_ns_ceiling": 3334,
        "late_phy_wait_ns": 2000000, "mdss_vmstate_payload_bytes": 64,
        "mdss_vmstate_version": 1, "first_observation_qom_get_count": 304,
        "reference_enabled_by_default": False, "frequency_checks_use_exact_clock_period": True,
        "runtime_xo_change_tested": True, "source_commit": PRIMARY,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "stock_compiled_dp_source_identity_claimed": False, "guest_cpu_executed": False,
        "native_kernel_probe_observed": False, "analog_pll_behavior_modeled": False,
        "aux_protocol_modeled": False, "link_training_modeled": False,
        "android_boot_passed": False, "binary_unchanged": True, "passed": True,
        "qemu_sha256_before": expected_qemu, "qemu_sha256_after": expected_qemu,
    }
    need(type(report) is dict, "DP result is not an object")
    need(not any(k in report for k in ("error", "traceback")), "DP result recorded an exception")
    for key, value in fields.items():
        same(report.get(key), value, "DP result."+key)
    need(type(report.get("checks")) is list, "DP check inventory is not a list")
    same([c.get("name") for c in report["checks"]], list(CHECK_NAMES), "Independent exact 36-check inventory")
    for i, row in enumerate(report["checks"]):
        same(row.get("passed"), True, "DP check."+row["name"])
        if i < len(ORDINARY):
            same(row.get("fixture"), f"ordinary-{i:02d}-"+row["name"], "DP actual ordinary fixture")
    for key, count in (("migrations", 4), ("rejected_migrations", 2), ("corrupted_stream_rejections", 4),
                       ("migration_sources", 5), ("actual_stream_files", 9)):
        need(type(report.get(key)) is list, "DP collection missing: "+key)
        same(len(report[key]), count, "DP result."+key+" count")
    same([r["name"] for r in report["migrations"]], list(COMPLETED), "Four completed migration cases")
    same([r["name"] for r in report["rejected_migrations"]], ["on_to_off", "off_to_on"], "Both profile rejection directions")
    same([r["name"] for r in report["corrupted_stream_rejections"]], list(CORRUPT), "Four corruption cases")
    same([r["file"] for r in report["migration_sources"]],
         ["dp-"+s+".migration" for s in SCENARIOS], "Five original migration sources")


def select_component(store, args, receipt):
    cases = [name for name in store.entries if
             re.search(r"(?:^|/)components/[0-9]+-dp-pll-7nm-reference/case\.json$", name)]
    same(len(cases), 1, "Unique actual DP component case")
    case_path, root = cases[0], cases[0].rsplit("/", 1)[0]
    result_path = root+"/fixtures/data/result.json"
    case, report = store.json(case_path), store.json(result_path)
    same(case.get("name"), "dp-pll-7nm-reference", "Selected CI case name")
    same(case.get("passed"), True, "Selected CI case verdict")
    same(case.get("exit_code"), 0, "Actual DP harness process exit")
    need(not any(k in case for k in ("error", "artifact_error", "timed_out")), "Incomplete actual DP process")
    need(type(case.get("completed_utc")) is str and case["completed_utc"], "Missing process completion")
    report_contract(report, args.expected_qemu_sha256)
    for name in ("stdout.log", "stderr.log"):
        raw = store.read(root+"/"+name)
        same(case.get("log_sha256", {}).get(name), sha(raw), "Actual harness "+name+" hash")
        if name == "stdout.log":
            same(json_value(raw, root+"/"+name), report, "Actual stdout versus saved DP result")
    verified = case.get("verified")
    need(type(verified) is dict, "Missing CI case adjudication")
    same(verified.get("verdict"), True, "CI verdict result")
    same(verified.get("verdict_field"), "passed", "CI verdict field")
    same(verified.get("check_count"), 36, "CI adjudicated check count")
    same(verified.get("verified_lengths"), {"checks": 36, "migrations": 4, "rejected_migrations": 2,
                                           "corrupted_stream_rejections": 4,
                                           "migration_sources": 5, "actual_stream_files": 9},
         "CI adjudicated collection lengths")
    argv = case.get("command")
    need(type(argv) is list and len(argv) >= 6 and all(type(x) is str for x in argv), "Bad harness argv")
    need("-O" not in argv and "-OO" not in argv, "Optimized harness disabled assertions")
    same(PurePosixPath(argv[1]).name, "test_dp_pll_7nm.py", "Actual harness script")
    for option, expected in (("--qemu", report["qemu"]), ("--out", report["work_dir"])):
        same(argv.count(option), 1, "Actual harness "+option+" count")
        same(argv[argv.index(option)+1], expected, "Actual harness "+option)
    same(PurePosixPath(report["work_dir"]), PurePosixPath(case["fixture"])/"data", "Harness output belongs to case")
    source_prefix = root.split("/components/", 1)[0] if "/components/" in root else ""
    source_path = (source_prefix+"/" if source_prefix else "")+"source-commit.txt"
    same(store.text(source_path).strip(), args.expected_head, "Actual archived source commit")
    receipt["component"] = {"case": case_path, "result": result_path, "source_commit_file": source_path,
                            "head": args.expected_head, "test_sha256": TEST_SHA,
                            "qemu_sha256_from_separately_audited_binary": args.expected_qemu_sha256,
                            "harness_exit": 0, "exact_checks": 36}
    return root+"/fixtures/data", report


def audit_migrations(store, data_root, report, receipt):
    actual_names = sorted(name[len(data_root)+1:] for name in store.entries
                          if name.startswith(data_root+"/") and "/" not in name[len(data_root)+1:]
                          and name.endswith(".migration"))
    names = sorted(["dp-"+s+".migration" for s in SCENARIOS]+["dp-corrupt-"+s+".migration" for s in CORRUPT])
    same(actual_names, names, "Exactly five actual original streams and four single-bit mutants")
    same([r["name"] for r in report["actual_stream_files"]], names, "Actual stream inventory order")
    recorded = {r["name"]: r for r in report["actual_stream_files"]}
    streams, saved_by_name = {}, {}
    receipt["original_streams"], receipt["completed_migrations"] = [], []
    receipt["rejected_migrations"], receipt["single_bit_rejections"] = [], []
    for scenario, saved in zip(SCENARIOS, report["migration_sources"]):
        scenario_contract(saved, scenario)
        where = data_root+"/"+saved["source_directory"]
        first = store.json(where+"/source-before-save.json")
        after = store.json(where+"/source-after-save.json")
        same(first, saved["snapshot"], scenario+".actual source QOM")
        same(after, first, scenario+".source unchanged across save")
        world_check = check_world(first, scenario != "disabled", scenario, saved["source_virtual_ns"])
        full_path = data_root+"/"+saved["file"]
        stream = scan_stream(store, full_path)
        same(stream["sha256"], saved["sha256"], full_path+".actual source digest")
        same(stream["bytes"], saved["bytes"], full_path+".actual source size")
        same(recorded[saved["file"]], {"name": saved["file"], "bytes": stream["bytes"], "sha256": stream["sha256"]}, full_path+".inventory")
        sidecar = store.json(full_path+".sections.json")
        same(sidecar, saved["sections"], full_path+".reported/saved section metadata")
        same(set(sidecar), {"dp", "mdss"}, full_path+".section inventory")
        for label, expected_payload in (("dp", dp_wire(first["dp"])), ("mdss", mdss_wire(first["mdss"]))):
            same(stream["payloads"][label], expected_payload, full_path+"."+label+".complete actual payload bytes")
            payload_name = saved["file"]+"."+label+"-v1.payload.bin"
            same(store.read(data_root+"/"+payload_name, 4096), expected_payload, full_path+"."+label+".complete saved payload")
            expected_section = dict(stream["sections"][label], payload_file=payload_name,
                                    payload_sha256=sha(expected_payload), all_bytes_match_stopped_source_qom=True)
            same(sidecar[label], expected_section, full_path+"."+label+".actual header/footer")
        decoded = wire_fields(stream["payloads"]["dp"])
        same(decoded["input_clock_period"], [first["dp"]["input_period"]], full_path+".last eight Clock bytes")
        trace = source_trace(store, where, saved, report, scenario != "disabled")
        streams[saved["file"]], saved_by_name[saved["file"]] = stream, saved
        receipt["original_streams"].append({"name": saved["file"], "bytes": stream["bytes"], "sha256": stream["sha256"],
            "complete_dp_bytes": 791, "complete_mdss_bytes": 64, "sections": stream["sections"],
            "world": world_check, "trace": trace, "literal_field_groups_checked": list(decoded)})
    for scenario, item in zip(SCENARIOS[:4], report["migrations"]):
        filename = "dp-"+scenario+".migration"
        saved, stream = saved_by_name[filename], streams[filename]
        for key, value in (("passed", True), ("source_status", "completed"), ("target_status", "completed"),
                           ("snapshot_file", filename), ("snapshot_sha256", stream["sha256"]),
                           ("stream_modified", False), ("source_directory", "migration-"+scenario+"-source"),
                           ("target_directory", "migration-"+scenario+"-target"), ("continued_after_restore", True),
                           ("all_791_payload_bytes_verified", True), ("mdss_64_bytes_verified", True),
                           ("actual_two_dispcc_input_periods_restored", True)):
            same(item.get(key), value, item["name"]+"."+key)
        details = {
            "steady": {"high_hsclk_and_two_actual_dispcc_roots_restored": True, "post_restore_powerdown_revoked_carriers": True},
            "pll-pending": {"pll_completed_after_ns": 2334, "phy_required_new_command_after_restore": True},
            "phy-pending": {"phy_completed_after_ns": 2334, "actual_mdss_power_loss_revoked_capture": True},
            "late-phy-wait": {"late_wait_completed_after_ns": 1500000, "new_phy_command_and_delay_required": True},
        }[scenario]
        for key, value in details.items():
            same(item.get(key), value, item["name"]+"."+key)
        trace = target_trace(store, data_root+"/"+item["target_directory"], saved, item, report)
        receipt["completed_migrations"].append({"name": item["name"], "source_file": filename,
                                                "actual_stream_sha256": stream["sha256"], "trace": trace})
    for row, filename, profile, diagnostic in (
            (report["rejected_migrations"][0], "dp-steady.migration", False, "0 != 1"),
            (report["rejected_migrations"][1], "dp-disabled.migration", True, "1 != 0")):
        same(row["passed"], True, row["name"]+".reported rejection")
        same(row["snapshot_file"], filename, row["name"]+".unmodified rejected file")
        same(row["snapshot_sha256"], streams[filename]["sha256"], row["name"]+".actual unmodified digest")
        same(row["stream_modified"], False, row["name"]+".profile stream unmodified")
        trace = rejection_trace(store, data_root+"/reject-profile-"+row["name"], row, report, filename,
                                saved_by_name[filename]["source_virtual_ns"], diagnostic, profile)
        receipt["rejected_migrations"].append({"name": row["name"], "source_file": filename,
                                               "stream_sha256": streams[filename]["sha256"], "trace": trace})
    contracts = (
        ("physical_base", "dp-steady.migration", 52, 8, BASE, 4, f"{BASE:x} != {BASE^4:x}"),
        ("captured_recipe", "dp-pll-pending.migration", 505, 4, 0x8c, 1, TYPE+": invalid migrated capture"),
        ("last_xo_period", "dp-phy-pending.migration", 749, 8, XO_PERIOD, 1, TYPE+": invalid migrated reference clock"),
        ("late_deadline", "dp-late-phy-wait.migration", 757, 8, 2400000, 1 << 63, TYPE+": invalid migrated transition"),
    )
    for row, (name, source_file, relative, width, old, xor, diagnostic) in zip(report["corrupted_stream_rejections"], contracts):
        filename = "dp-corrupt-"+name+".migration"
        source, saved = streams[source_file], saved_by_name[source_file]
        stream = scan_stream(store, data_root+"/"+filename)
        same(row["name"], name, filename+".case name")
        same(recorded[filename], {"name": filename, "bytes": stream["bytes"], "sha256": stream["sha256"]}, filename+".actual inventory")
        same(stream["sections"], source["sections"], filename+".unchanged complete section headers and footers")
        same(stream["payloads"]["mdss"], source["payloads"]["mdss"], filename+".MDSS bytes unchanged")
        expected = bytearray(source["payloads"]["dp"])
        same(int.from_bytes(expected[relative:relative+width], "big"), old, filename+".independently checked old field")
        expected[relative:relative+width] = (old^xor).to_bytes(width, "big")
        same(stream["payloads"]["dp"], bytes(expected), filename+".exact actual mutated field")
        byte_in_field = width-1-(xor.bit_length()-1)//8
        absolute = source["sections"]["dp"]["payload_offset"]+relative+byte_in_field
        byte_xor = xor >> (8*(width-1-byte_in_field))
        for key, value in (("passed", True), ("source_file", source_file), ("source_sha256", source["sha256"]),
                           ("snapshot_file", filename), ("snapshot_sha256", stream["sha256"]), ("stream_modified", True),
                           ("field_offset", relative), ("field_bytes", width), ("old_value", old), ("xor", xor),
                           ("changed_byte_offsets", [absolute]), ("changed_bits", 1), ("original_stream_unchanged", True)):
            same(row.get(key), value, filename+"."+key)
        difference = one_bit_difference(store, data_root+"/"+source_file, data_root+"/"+filename,
                                        absolute, byte_xor, source["sha256"], stream["sha256"])
        trace = rejection_trace(store, data_root+"/reject-corrupt-"+name, row, report, filename,
                                saved["source_virtual_ns"], diagnostic, True)
        receipt["single_bit_rejections"].append({"name": name, "source_file": source_file,
            "source_sha256": source["sha256"], "file": filename, "bytes": stream["bytes"], "sha256": stream["sha256"],
            "actual_difference": difference, "trace": trace})


def runtime_xo_trace(store, data_root, report):
    name, row = ORDINARY[11], report["checks"][11]
    for key in ("runtime_xo_loss_exercised", "mdss_remained_on", "new_enable_sequence_required"):
        same(row.get(key), True, "Actual RPMh XO case."+key)
    same(row.get("cancelled_phases"), [1, 4, 5], "Actual RPMh cancellation cases")
    same(row.get("actual_xo_resource"), "xo.lvl", "Actual RPMh XO resource")
    same(row.get("actual_supply_resource"), "mmcx.lvl", "Actual RPMh MDSS supplier resource")
    directory = data_root+"/ordinary-11-"+name
    command_contract(store, directory, report, True, rpmh=True)
    calls = qmp_calls(store, directory)
    def actual(path, prop):
        return [reply(c) for c in calls if operation(c) == "qom-get" and
                c["request"].get("arguments") == {"path": path, "property": prop}]
    # This case explicitly samples the input period at initial-off, first-on
    # and first loss. It does not log an input-period getter after every vote.
    periods = actual(DEVICE+"/ref_clk_src", "qtest-clock-period")
    same(periods, [0, XO_PERIOD, 0], "Raw actual connected XO period samples")
    phases = actual(DEVICE, "phase")
    same(phases, [1, 0, 0, 1, 2, 3, 4, 0, 1, 2, 3, 4, 5, 0, 0, 1, 2, 3, 4],
         "Actual pending/ready/late cancellation and new-transaction phases")
    powers = actual(DEVICE, "power-on")
    need(powers and all(type(v) is bool and v for v in powers), "XO loss case did not retain real MDSS power")
    need(not any(operation(c) in ("qom-set", "device_add", "human-monitor-command") for c in calls),
         "Runtime XO case used a mutating QMP bypass")
    starts = [i for i, c in enumerate(calls) if operation(c) == "qom-get" and
              c["request"].get("arguments") == {"path": DEVICE, "property": "clk."+NAMES[0]+".period"}]
    blocks = ((0, False, False, 0), (0, False, False, 0),
              (5400000, True, False, 2), (5400000, True, True, 2), (0, False, False, 0),
              (5400000, True, False, 2), (5400000, True, True, 2), (0, False, False, 0),
              (0, False, False, 0), (5400000, True, False, 2), (5400000, True, True, 2))
    same(len(starts), len(blocks), "Actual RPMh case full output observation inventory")
    for number, (start, values) in enumerate(zip(starts, blocks)):
        expected = output_queries(*values)
        qom_block(calls[start:start+len(expected)], expected, f"Runtime XO actual output block {number}")
    qt = qtest_calls(store, directory)
    qtest_time(qt, directory)
    writes = []
    for call in qt:
        parts = call["command"].split()
        need(not (parts[0] == "set_irq_in" and parts[1] in (DEVICE, MDSS)),
             "Runtime XO/supply test overrode a connected provider")
        if parts[0] == "writel" and 0x18220000 <= int(parts[1], 0) < 0x18230000:
            writes.append(call)
    need(writes, "Runtime XO case contains no actual RPMh DRV2 writes")
    steps = [int(c["command"].split()[1], 0) for c in qt if c["command"].startswith("clock_step ")]
    same(steps, [1000, 1000, 1000, 1000, 10000, 3334, 3334, 1000, 1000,
                 3334, 3334, 1000, 1000, 2000000, 3334, 3334],
         "Actual RPMh vote completions and transaction deadlines")
    return {"actual_input_period_samples": periods, "input_sample_scope": "initial-off, first-on, first-loss",
            "actual_rpmh_drv2_write_count": len(writes), "no_connected_provider_override": True,
            "actual_phase_sequence": phases, "actual_output_blocks": len(starts),
            "physical_clocks_per_block": 6, "actual_mdss_power_observations": len(powers),
            "actual_clock_steps_ns": steps, "capture_cancellation_phases": [1, 4, 5],
            "new_source_sequences_required_after_loss": True}


def self_check():
    """Bounded synthetic parser checks; this never qualifies a CI artifact."""
    checks = []
    def rejected(name, fn):
        try:
            fn()
        except (AuditFailure, struct.error, ValueError):
            checks.append({"name": name, "expected_rejection_observed": True})
        else:
            raise AuditFailure("Synthetic negative unexpectedly accepted: "+name)
    same(len(KEYS), 73, "Literal register count")
    same(len(REQUIRED), 58, "Literal required credit count")
    same(len(CHECK_NAMES), 36, "Exact named case count")
    same(len(set(CHECK_NAMES)), 36, "Unique named cases")
    # Distinct sentinels at every field group make a missing final Clock or
    # shifted group observable independently from any production test encoder.
    s = {"globals": dict(zip(GLOBALS, GLOBAL_VALUES)), "region_bases": [r[1] for r in REGIONS],
         "registers": {k: i+1 for i, k in enumerate(KEYS)},
         "written": {k: bool(i % 2) for i, k in enumerate(KEYS)},
         "captured": {k: 0x100+i for i, k in enumerate(KEYS)},
         "runtime": {"captured-xo-period": 0x1122334455667788, "last-xo-period": 0x8877665544332211,
                     "deadline-ns": -7, "phase": 3, "sequence-index": 4, "captured-rate-khz": 5400000,
                     "power-known": True, "power-on": False},
         "input_period": 0x0102030405060708}
    payload = dp_wire(s)
    same(len(payload), 791, "Synthetic complete wire size")
    same(payload[783:791], b"\x01\x02\x03\x04\x05\x06\x07\x08", "Final Clock occupies the last eight bytes")
    same(int.from_bytes(payload[505:509], "big"), 0x100+14, "Independent captured-recipe field offset")
    same(int.from_bytes(payload[757:765], "big", signed=True), -7, "Signed deadline field offset")
    decoded = wire_fields(payload)
    same(decoded["input_clock_period"], [s["input_period"]], "Decoded final Clock")
    checks.append({"name": "all_791_bytes_and_independent_absolute_offsets", "passed": True})
    rejected("bool_not_accepted_as_integer", lambda: uint(True, 32, "synthetic"))
    rejected("integer_not_accepted_as_bool", lambda: boolean(1, "synthetic"))
    rejected("duplicate_json_keys_rejected", lambda: json_value('{"a":1,"a":2}', "synthetic"))
    rejected("nonfinite_json_rejected", lambda: json_value('{"a":NaN}', "synthetic"))
    rejected("truncated_790_byte_wire_rejected", lambda: wire_fields(payload[:-1]))
    def section(name, number, data):
        return (b"\x04"+struct.pack(">I", number)+bytes((len(name),))+name.encode()+
                struct.pack(">II", 0, 1)+data+b"\x7e"+struct.pack(">I", number))
    wire = b"QEVM\x00\x00\x00\x03"+section(TYPE, 7, payload)+section(MDSS_TYPE, 9, bytes(64))+b"\x00"
    class Memory:
        def __init__(self, entries):
            self.entries, self.observed = entries, {}
        def size(self, name):
            return len(self.entries[name])
        def open(self, name):
            return io.BytesIO(self.entries[name])
    memory = Memory({"source": wire})
    actual = scan_stream(memory, "source")
    same(actual["payloads"]["dp"], payload, "Full parsed synthetic payload")
    same(actual["sections"]["dp"]["payload_bytes"], 791, "Full parsed synthetic section size")
    checks.append({"name": "unique_full_sections_and_exact_footer_ids", "passed": True})
    footer = actual["sections"]["dp"]["footer_offset"]
    damaged = bytearray(wire)
    damaged[footer+4] ^= 1
    rejected("wrong_complete_footer_rejected", lambda: scan_stream(Memory({"bad": bytes(damaged)}), "bad"))
    rejected("duplicate_section_rejected", lambda: scan_stream(Memory({"bad": wire+section(TYPE, 8, payload)}), "bad"))
    rejected("incomplete_last_clock_or_footer_rejected", lambda: scan_stream(Memory({"bad": wire[:footer-1]}), "bad"))
    absolute = actual["sections"]["dp"]["payload_offset"]+757
    mutant = bytearray(wire)
    mutant[absolute] ^= 0x80
    memory.entries["mutant"] = bytes(mutant)
    difference = one_bit_difference(memory, "source", "mutant", absolute, 0x80, sha(wire), sha(mutant))
    same(difference["changed_bits"], 1, "Actual synthetic sign-bit mutation")
    checks.append({"name": "one_bit_sign_change_full_stream_comparison", "passed": True})
    mutant[absolute+3] ^= 1
    memory.entries["two"] = bytes(mutant)
    rejected("additional_changed_byte_rejected", lambda: one_bit_difference(memory, "source", "two", absolute, 0x80, sha(wire), sha(mutant)))
    return {"schema": AUDIT_SCHEMA+"-synthetic-self-check", "passed": True,
            "actual_ci_artifact_audited": False, "qemu_or_guest_executed": False,
            "checks": checks, "check_count": len(checks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--zip", type=Path, help="Actual CI ZIP; selective streaming reads only")
    inputs.add_argument("--result", type=Path, help="Existing collected CI result directory")
    parser.add_argument("--expected-head", help="Exact separately reviewed source commit")
    parser.add_argument("--expected-qemu-sha256", help="Actual ELF SHA256 from separate root integration audit")
    parser.add_argument("--expected-zip-sha256", help="Exact downloaded ZIP SHA256, required for ZIP mode")
    parser.add_argument("--test-source", type=Path, default=Path(__file__).resolve().parents[1]/"tb371fc/tests/test_dp_pll_7nm.py")
    parser.add_argument("--model-source", type=Path, default=Path(__file__).resolve().parents[1]/"hw/misc/qcom-sm8250-dp-pll.c")
    parser.add_argument("--out", type=Path, help="New JSON receipt path; existing receipt is never overwritten")
    parser.add_argument("--self-test", action="store_true", help="Bounded synthetic checks, never actual artifact qualification")
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    receipt = {"schema": AUDIT_SCHEMA, "passed": False, "started_utc": started,
               "auditor_sha256": file_hash(__file__),
               "compiled_qemu_executed_by_auditor": False, "native_guest_executed_by_auditor": False,
               "android_boot_established": False, "aggregate_and_qemu_bytes_audit_delegated": True,
               "shared_qmp_qtest_timestamp_claimed": False,
               "first_observation_evidence_boundary":
                   "Exact reviewed harness order plus raw QMP in-file order, actual QTest command counts, "
                   "saved before/after counters and complete state equality. Separate logs contain no shared "
                   "timestamps; this audit does not infer a cross-file total event order.",
               "stages_completed": []}
    store, output = None, None
    try:
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            output = args.out.open("x", encoding="utf-8")
        if args.self_test:
            need(not args.zip and not args.result, "Synthetic self-check does not accept an artifact")
            receipt = dict(self_check(), auditor_sha256=file_hash(__file__), started_utc=started)
        else:
            need(bool(args.zip) != bool(args.result), "Provide exactly one of --zip or --result")
            for value, size, label in ((args.expected_head, 40, "--expected-head"),
                                       (args.expected_qemu_sha256, 64, "--expected-qemu-sha256")):
                need(type(value) is str and re.fullmatch("[0-9a-f]{"+str(size)+"}", value), "Invalid "+label)
            same(args.expected_head, HEAD, "Auditor is bound to the reviewed source head")
            need(args.test_source.is_file() and args.model_source.is_file(), "Reviewed test/model sources must be locally available")
            same(file_hash(args.test_source), TEST_SHA, "Exact reviewed test source pin")
            ast.parse(args.test_source.read_text())
            same(file_hash(args.model_source), MODEL_SHA, "Exact reviewed model source pin")
            receipt["reviewed_sources"] = {
                "test": {"path": str(args.test_source.resolve()), "sha256": TEST_SHA},
                "model": {"path": str(args.model_source.resolve()), "sha256": MODEL_SHA},
                "primary_reference": PRIMARY,
                "primary_reference_is_not_entire_original_firmware_source_identity": True}
            if args.zip:
                need(args.zip.is_file(), "Actual ZIP does not exist")
                need(type(args.expected_zip_sha256) is str and re.fullmatch("[0-9a-f]{64}", args.expected_zip_sha256),
                     "ZIP mode requires exact --expected-zip-sha256")
                store = Store(zip_path=args.zip)
                digest = file_hash(args.zip)
                same(digest, args.expected_zip_sha256, "Actual complete ZIP SHA256")
                receipt["input"] = {"kind": "zip", "path": str(args.zip.resolve()), "bytes": args.zip.stat().st_size, "sha256": digest}
            else:
                need(args.expected_zip_sha256 is None, "ZIP hash cannot bind a result directory")
                store = Store(directory=args.result)
                receipt["input"] = {"kind": "collected-results", "path": str(args.result.resolve())}
            receipt["stages_completed"].append("exact_reviewed_source_and_artifact_identity")
            data_root, report = select_component(store, args, receipt)
            receipt["stages_completed"].append("actual_dp_process_logs_and_exact_36_check_contract")
            audit_migrations(store, data_root, report, receipt)
            receipt["stages_completed"].append("nine_actual_streams_complete_791_plus_64_payloads_and_protocol_evidence")
            receipt["runtime_xo_evidence"] = runtime_xo_trace(store, data_root, report)
            receipt["stages_completed"].append("actual_connected_rpmh_xo_loss_and_capture_cancellation_transcripts")
            receipt["members_read"] = store.observed
            store.finish()
            store = None
            receipt["summary"] = {"checks": 36, "unmodified_streams": 5, "stream_files": 9,
                "completed_migrations": 4, "profile_rejections": 2, "single_bit_rejections": 4,
                "dp_bytes_per_original": 791, "mdss_bytes_per_original": 64,
                "clock_views_per_source_and_first_target": 6, "first_target_qom_getters_each": 304,
                "first_target_actual_mmio_delta_each": 0, "first_target_actual_cont_count_each": 0,
                "power_loss_credit_invariant_checked_on_legal_states_and_actual_target_observation": True,
                "power_loss_credit_invariant_claimed_as_isolated_corruption_case": False}
            receipt["passed"] = True
    except BaseException as error:
        receipt["passed"], receipt["error"], receipt["error_type"] = False, str(error), type(error).__name__
        receipt["traceback"] = traceback.format_exc()
        if store is not None:
            receipt["members_read_before_failure"] = store.observed
    finally:
        if store is not None and store.z is not None:
            store.z.close()
        receipt["completed_utc"] = datetime.now(timezone.utc).isoformat()
        text = json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False)+"\n"
        if output is not None:
            output.write(text)
            output.close()
        print(text, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
