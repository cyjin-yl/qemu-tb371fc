#!/usr/bin/env python3
"""Independent, read-only DSI CI payload/protocol audit; never starts QEMU.

Only the selected DSI component is adjudicated here. The caller separately
audits the QEMU bytes and the 89 component / 5 Linux aggregate gates. This tool
binds those caller-supplied source/binary identities to the DSI receipt, hashes
the actual ZIP and all eight migration files, and checks the complete DSI and
MDSS sections against raw QMP replies, independent literal wire offsets and a
source-derived clock graph. It imports no project/test/generator modules.

No cross-file timestamp order is invented: QMP order, actual QTest command
counts, saved counters and the exact reviewed harness ordering form the
first-observation evidence together. Individual logs have no shared timestamp.

ZIPs are read selectively and migration streams are scanned in chunks. No
archive member is extracted. --out is exclusive-create, including on failure.
"""

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import traceback
from zipfile import ZipFile


AUDIT_SCHEMA = "tb371fc-dsi-pll-independent-artifact-audit-v1"
TEST_SHA = "73c81336b97229657a19958068490102118def404a2afa4d97743ee26ffac36e"
MODEL_SHA = "75e6b773eb965077a99ecc23d5a250aa68e82ba95006ffd9872e6b0ad6e51b8b"
PRIMARY = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
TYPE, DEVICE = "qcom-sm8250-dsi-pll", "/machine/dsi-pll"
MDSS_TYPE, MDSS, DISPCC = "qcom-mdss-gdsc", "/machine/mdss-gdsc", "/machine/dispcc"
BASE, STRIDE, RAM = 0x0AE94400, 0x2000, 0x80010000
U, XO, FREF = 1_000_000_000 << 32, 19_200_000, 38_400_000
XO_PERIOD = U // XO
DSI_BYTES, MDSS_BYTES, GETTERS = 2926, 64, 1163
CHUNK = 1024 * 1024
MAX_SMALL = 64 * 1024 * 1024

# Literal v4.1 sparse geometry, not imported from a generated manifest.
WINDOWS = ((0x10, 0x10), (0x24, 4), (0x2C, 8), (0x3C, 4),
           (0x128, 4), (0x500, 0xCC), (0x5DC, 0x14), (0x600, 0x164))
OFFSETS = tuple(o for start, size in WINDOWS for o in range(start, start + size, 4))
ALL_OFFSETS = tuple(b * STRIDE + o for b in range(2) for o in OFFSETS)
KEYS = tuple(f"{o:04x}" for o in ALL_OFFSETS)
PHY_MASKS = {0x10: 0xFF, 0x14: 0x37, 0x18: 0x40, 0x1C: 1,
             0x24: 0x7F, 0x2C: 0x40, 0x30: 4, 0x3C: 1, 0x128: 1}
STATUS = set(range(0x6B0, 0x6D0, 4)) | set(range(0x700, 0x710, 4)) | {0x718, 0x71C, 0x720}
FACTOR_FIELDS = (0x5E0, 0x5E4, 0x5E8, 0x5EC)
SSC_FIELDS = (0x620, 0x624, 0x628, 0x62C, 0x630, 0x634, 0x650)
DYNAMIC = set(FACTOR_FIELDS + SSC_FIELDS + (0x740, 0x748, 0x758, 0x750))
NONREQUIRED = set(PHY_MASKS) | STATUS | {0x528, 0x654}
REQUIRED = tuple(o for o in OFFSETS if o not in NONREQUIRED)
FIXED = tuple(o for o in REQUIRED if o not in DYNAMIC)
# Final nonzero literals in source init + commit + v4.1 hzindep; the other
# explicitly programmed fixed fields are zero. Earlier 0x10/0x84/0x29/0x2a
# writes are overwritten by 0x12/0x82/0x2f/0x3f. Recipe != cold reset values.
FIXED_NONZERO = {
    0x504: 3, 0x508: 0x3F, 0x518: 1, 0x51C: 0x80, 0x524: 0x4E,
    0x530: 0x10, 0x534: 0x20, 0x538: 0x10, 0x53C: 2, 0x540: 0x1C,
    0x544: 0x40, 0x54C: 2, 0x550: 0x20, 0x558: 0xFF, 0x560: 0x0A,
    0x564: 0x25, 0x568: 0xBA, 0x56C: 0x4F, 0x570: 0x0A, 0x578: 0x0C,
    0x57C: 0x20, 0x584: 0xFF, 0x588: 0x10, 0x58C: 0x46, 0x590: 0x2F,
    0x594: 0x3F, 0x598: 0x54, 0x5A4: 0x40, 0x5AC: 4, 0x5BC: 0x12,
    0x5C4: 8, 0x5C8: 8, 0x600: 3, 0x658: 0x40, 0x65C: 0x40,
    0x660: 0x0A, 0x664: 0x0A, 0x668: 0xC0, 0x670: 0x82, 0x674: 0x54,
    0x678: 0x4C, 0x67C: 0x4C, 0x680: 3, 0x690: 0x80, 0x694: 6,
    0x698: 0x19, 0x6A4: 0x40, 0x6A8: 0x20, 0x6D4: 2, 0x6D8: 0x11,
    0x6E4: 0x80, 0x728: 0x1D, 0x72C: 0x1C, 0x730: 0xFF, 0x734: 0x22,
    0x738: 9, 0x754: 0x10, 0x75C: 3, 0x760: 0x22,
}

# The binding's 26 named CCF views share one physical PLL per bank.
NAMES0 = (
    "dsi0pll_vco_clk", "dsi0pll_pll_out_div", "dsi0pll_bitclk_src", "dsi0pll_byteclk_src",
    "dsi0pll_post_bit_div", "dsi0pll_post_vco_div", "dsi0_phy_pll_out_byteclk",
    "dsi0pll_pclk_src_mux", "dsi0pll_pclk_src", "dsi0_phy_pll_out_dsiclk",
    "dsi0pll_shadow_vco_clk", "dsi0pll_shadow_pll_out_div", "dsi0pll_shadow_bitclk_src",
    "dsi0pll_shadow_byteclk_src", "dsi0pll_shadow_post_bit_div", "dsi0pll_shadow_post_vco_div",
    "dsi0pll_shadow_pclk_src_mux", "dsi0pll_shadow_pclk_src", "dsi0pll_cphy_byteclk_src",
    "dsi0pll_post_vco_div3_5", "dsi0pll_cphy_pclk_src_mux", "dsi0pll_cphy_pclk_src",
    "dsi0pll_shadow_cphy_byteclk_src", "dsi0pll_shadow_post_vco_div3_5",
    "dsi0pll_shadow_cphy_pclk_src_mux", "dsi0pll_shadow_cphy_pclk_src",
)
NAMES1 = tuple(n.replace("dsi0", "dsi1").replace("dsi1pll_shadow_cphy_byteclk_src",
               "dsi1pll_cphy_shadow_byteclk_src") for n in NAMES0)
NAMES = NAMES0 + NAMES1
CARRIERS = ("dsi0_phy_pll_out_byteclk", "dsi1_phy_pll_out_byteclk",
            "dsi0_phy_pll_out_dsiclk", "dsi1_phy_pll_out_dsiclk")
GLOBALS = ("reference-profile", "reference-identity", "source-variant", "bank-count",
           "words-per-bank", "register-count", "clock-count", "input-count",
           "window-count", "mapped-bytes", "lock-reference-cycles", "bias-settle-ns",
           "physical-base", "bank-stride")
GLOBAL_VALUES = (1, 0x44534934, 0x401, 2, 154, 308, 52, 1, 16, 1232, 64, 250, BASE, STRIDE)
BANK_FLAGS = ("pll-locked", "lock-pending", "bias-settled", "reset-asserted", "digital-ready",
              "config-complete", "recipe-valid", "cphy", "external-selected", "external-known",
              "environment-qualified")
BANK_FIELDS = BANK_FLAGS + ("bias-deadline", "lock-deadline", "lock-qualify-after",
                            "captured-xo-period", "configured-vco-hz", "active-vco-hz")
GDSC_GLOBALS = ("enabled-profile", "reference-identity", "domain-count", "register-bytes",
                "mapped-bytes", "parent-resource-index", "cold-control",
                "control-write-mask", "power-status-mask")
GDSC_VALUES = (1, 0x4D445331, 1, 4, 4, 3, 1, 0xF803, 0x80000000)
GDSC_FIELDS = ("name", "address", "dt-flags", "timeout-us", "control", "request-known",
               "requested", "state-known", "powered", "hw-request-known", "hw-request")
ORDINARY = (
    "cold_identity_and_pure_observers", "literal_v4_1_programming_and_overwrites_both_banks",
    "bias_then_lock_has_exact_finite_boundaries", "start_before_bias_waits_for_both_independent_deadlines",
    "pending_stop_cancels_capture_without_late_ack", "pending_recipe_change_requires_a_new_start_edge",
    "required_zero_words_and_fixed_recipe_are_not_reset_defaults",
    "raw_request_threshold_triplets_and_five_ghz_use_exact_periods",
    "wrong_threshold_and_out_of_range_factor_do_not_lock", "source_default_ssc_downspread_center_and_off",
    "inconsistent_ssc_fields_do_not_gain_lock", "prelock_reset_pulse_cannot_prepare_final_carriers",
    "dphy_dividers_muxes_and_gates_keep_separate_roles", "cphy_clock_views_and_unknown_external_selection",
    "shared_phy_fields_and_bank_storage_are_independent", "power_loss_clears_qualification_but_retains_raw_words",
    "programming_with_unknown_or_off_supply_cannot_qualify", "live_outdiv_all_four_values_preserves_lock_and_capture",
    "qualification_changes_revoke_lock_and_final_carriers", "status_zero_writes_and_reserved_bits_are_atomic",
    "exact_sparse_leaves_holes_access_shapes_and_alias", "four_real_dispcc_carriers_and_high_pixel_rate",
    "connected_xo_and_all_state_observers_are_readonly", "system_reset_cancels_work_and_replays_supplier_collapse",
    "real_rpmh_xo_vote_loss_revokes_active_and_pending_banks",
)
SPECIAL = ("default_off_rejects_all_words_with_sibling_profiles_enabled",
           "paused_tcg_has_no_mutating_fixture_or_guest_execution")
SCENARIOS = ("steady", "pending-bias", "pending-lock", "disabled")
COMPLETED = ("migration_steady_high_carriers", "migration_pending_bias_and_lock", "migration_pending_lock")
CORRUPT = ("physical_base", "captured_factor", "captured_xo_period", "pending_digital_ready")
CHECK_NAMES = (ORDINARY + SPECIAL + COMPLETED + ("profile_on_to_off_rejected", "profile_off_to_on_rejected") +
               tuple("single_bit_" + n + "_rejected" for n in CORRUPT))


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


def mask(offset):
    if offset in PHY_MASKS:
        return PHY_MASKS[offset]
    if offset in STATUS:
        return 0
    return 3 if offset in (0x5EC, 0x650, 0x654) else 0xFF


def factor(words):
    return (words[0x5E0] << 18) | (words[0x5EC] << 16) | (words[0x5E8] << 8) | words[0x5E4]


def ceil_div(n, d):
    return (n + d - 1) // d if d else 0


def recipe(words):
    if any(words[o] & ~mask(o) for o in OFFSETS):
        return False
    if any(words[o] != FIXED_NONZERO.get(o, 0) for o in FIXED):
        return False
    if words[0x750] != (0 if words[0x18] & 0x40 else 0x10):
        return False
    f = factor(words)
    low, high = max(600_000_000, ceil_div(f * FREF, 1 << 18)), min(5_000_000_000, ceil_div((f + 1) * FREF, 1 << 18) - 1)
    intervals = (
        (0x748, {0xA0: (0, 1_000_000_000), 0x20: (1_000_000_001, 2_500_000_000),
                 0: (2_500_000_001, 3_020_000_000), 0x40: (3_020_000_001, 5_000_000_000)}),
        (0x740, {8: (0, 1_519_999_999), 1: (1_520_000_000, 2_989_999_999), 0: (2_990_000_000, 5_000_000_000)}),
        (0x758, {1: (0, 3_099_999_999), 3: (3_100_000_000, 5_000_000_000)}),
    )
    for field, choices in intervals:
        if words[field] not in choices:
            return False
        lo, hi = choices[words[field]]
        low, high = max(low, lo), min(high, hi)
    if low > high:
        return False
    if not words[0x650]:
        return all(not words[o] for o in SSC_FIELDS)
    if words[0x650] not in (2, 3):
        return False
    step = (f * 4800 * 3 // 303 + 500000) // 1000000
    return [words[o] for o in SSC_FIELDS[:-1]] == [step & 255, (step >> 8) & 255, 0x2E, 1, 2, 0]


def rates(words, captured, bank):
    """CCF graph: three programmable dividers ceil; fixed factors floor."""
    result = [0] * 26
    if not bank["pll-locked"]:
        return result
    vco = FREF * factor(captured) // (1 << 18)
    post = ceil_div(vco, 1 << words[0x654])
    is_cphy, internal = bool(words[0x18] & 0x40), not bool(words[0x14] & 4)
    bitdiv, pixdiv, selector = words[0x10] & 15, words[0x10] >> 4, words[0x14] & 3
    bitclk = ceil_div(post, bitdiv) if internal and (is_cphy or words[0x2C] == 0x40) else 0
    result[0], result[1], result[2], result[4], result[5] = vco, post, bitclk, bitclk // 2, post // 4
    if is_cphy:
        result[18], result[19] = bitclk // 7, post * 2 // 7
        if internal and selector == 3:
            result[20], result[21] = result[19], ceil_div(result[19], pixdiv)
    else:
        result[3] = bitclk // 8
        if selector in (0, 1):
            result[7] = bitclk if selector == 0 else bitclk // 2
            result[8] = ceil_div(result[7], pixdiv)
    # The PHY source clears CLK_EN_SEL bit4 after retime FIFO sync. Only bit5
    # is the sustained global enable; the other digital prerequisites remain.
    gate = bank["digital-ready"] and words[0x14] & 0x20 == 0x20 and words[0x30] == 4 and words[0x128] == 0
    if gate:
        result[6], result[9] = (result[18], result[21]) if is_cphy else (result[3], result[8])
    for dst, src in ((10, 0), (11, 1), (12, 2), (13, 3), (14, 4), (15, 5), (16, 7), (17, 8),
                     (22, 18), (23, 19), (24, 20), (25, 21)):
        result[dst] = result[src]
    return result


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


def dsi_wire(snapshot):
    raw = bytearray(DSI_BYTES)
    encode_at(raw, 0, "I", [snapshot["globals"][n] for n in GLOBALS[:12]], "dsi globals")
    encode_at(raw, 48, "Q", [snapshot["globals"]["physical-base"], snapshot["globals"]["bank-stride"]], "dsi geometry")
    encode_at(raw, 64, "I", [snapshot["registers"][k] for k in KEYS], "dsi registers")
    encode_at(raw, 1296, "B", [snapshot["written"][k] for k in KEYS], "dsi written", bools=True)
    encode_at(raw, 1604, "I", [snapshot["captured"][k] for k in KEYS], "dsi captures")
    banks = snapshot["banks"]
    encode_at(raw, 2836, "Q", [b["captured-xo-period"] for b in banks], "captured periods")
    encode_at(raw, 2852, "Q", [snapshot["last-xo-period"]], "last XO")
    for offset, field in ((2860, "bias-deadline"), (2876, "lock-deadline"), (2892, "lock-qualify-after")):
        encode_at(raw, offset, "q", [b[field] for b in banks], field)
    for offset, field in ((2908, "pll-locked"), (2910, "bias-settled"), (2912, "reset-asserted"), (2914, "digital-ready")):
        encode_at(raw, offset, "B", [b[field] for b in banks], field, bools=True)
    encode_at(raw, 2916, "B", [snapshot["power-known"], snapshot["power-on"]], "power", bools=True)
    encode_at(raw, 2918, "Q", [snapshot["input_period"]], "serialized input Clock period")
    return bytes(raw)


def mdss_wire(snapshot):
    raw, domain = bytearray(MDSS_BYTES), snapshot["domains"][0]
    encode_at(raw, 0, "I", [snapshot["globals"][n] for n in GDSC_GLOBALS], "mdss globals")
    encode_at(raw, 36, "B", [snapshot["parent-known"], snapshot["parent-on"]], "mdss parents", bools=True)
    encode_at(raw, 38, "I", [0], "mdss domain id")
    encode_at(raw, 42, "Q", [domain["address"]], "mdss address")
    encode_at(raw, 50, "I", [domain["dt-flags"], domain["timeout-us"], domain["control"]], "mdss domain")
    encode_at(raw, 62, "B", [domain["hw-request-known"], domain["hw-request"]], "mdss hardware request", bools=True)
    return bytes(raw)


def wire_fields(raw):
    """Literal field-order decode for diagnostics and complete offset coverage."""
    same(len(raw), DSI_BYTES, "DSI section length")
    fields = ((0, 12, "I", "globals"), (48, 2, "Q", "physical_geometry"),
              (64, 308, "I", "registers"), (1296, 308, "B", "written"),
              (1604, 308, "I", "captured"), (2836, 2, "Q", "captured_xo_period"),
              (2852, 1, "Q", "last_xo_period"), (2860, 2, "q", "bias_deadline"),
              (2876, 2, "q", "lock_deadline"), (2892, 2, "q", "lock_qualify_after"),
              (2908, 2, "B", "pll_locked"), (2910, 2, "B", "bias_settled"),
              (2912, 2, "B", "reset_asserted"), (2914, 2, "B", "digital_ready"),
              (2916, 2, "B", "power_known_on"), (2918, 1, "Q", "input_period"))
    out, end = {}, 0
    for offset, count, code, name in fields:
        same(offset, end, "DSI field coverage")
        out[name] = list(struct.unpack_from(">" + str(count) + code, raw, offset))
        end = offset + count * struct.calcsize(">" + code)
    same(end, DSI_BYTES, "DSI complete field coverage")
    return out


def check_world(world, profile, where):
    same(set(world), {"dsi", "mdss", "dispcc_carrier_periods", "dispcc_pixel_roots"}, where + ".world keys")
    s, m = world["dsi"], world["mdss"]
    same(set(s), {"globals", "registers", "written", "captured", "banks", "last-xo-period",
                  "power-known", "power-on", "outputs", "output_hz", "physical_outputs", "input_period"},
         where + ".dsi keys")
    g = dict(zip(GLOBALS, GLOBAL_VALUES))
    g["reference-profile"] = profile
    same(s["globals"], g, where + ".globals")
    for group in ("registers", "written", "captured"):
        same(set(s[group]), set(KEYS), where + "." + group + " offsets")
    need(type(s["banks"]) is list and len(s["banks"]) == 2, where + ": wrong bank inventory")
    for field in ("power-known", "power-on"):
        boolean(s[field], where + "." + field)
    uint(s["input_period"], 64, where + ".input period")
    same(s["last-xo-period"], s["input_period"], where + ".current XO observer")
    all_rates = []
    for b, bank in enumerate(s["banks"]):
        prefix = where + f".bank{b}"
        same(set(bank), set(BANK_FIELDS), prefix + ".keys")
        for field in BANK_FLAGS:
            boolean(bank[field], prefix + "." + field)
        for field in ("bias-deadline", "lock-deadline", "lock-qualify-after"):
            uint(bank[field], 64, prefix + "." + field, signed=True)
            need(bank[field] >= -1, prefix + ": noncanonical negative deadline")
        for field in ("captured-xo-period", "configured-vco-hz", "active-vco-hz"):
            uint(bank[field], 64, prefix + "." + field)
        words = {o: s["registers"][f"{b * STRIDE + o:04x}"] for o in OFFSETS}
        captured = {o: s["captured"][f"{b * STRIDE + o:04x}"] for o in OFFSETS}
        written = {o: s["written"][f"{b * STRIDE + o:04x}"] for o in OFFSETS}
        for offset in OFFSETS:
            uint(words[offset], 32, prefix + f".reg.{offset:04x}")
            uint(captured[offset], 32, prefix + f".captured.{offset:04x}")
            boolean(written[offset], prefix + f".written.{offset:04x}")
            need((words[offset] | captured[offset]) & ~mask(offset) == 0,
                 prefix + f": reserved or status bits at {offset:04x}")
            if offset in STATUS:
                same(written[offset], False, prefix + ": status write gained qualification")
        complete = all(written[o] for o in REQUIRED)
        valid = recipe(words)
        powered = bool(profile and s["power-known"] and s["power-on"])
        bias = powered and words[0x24] & 0x60 == 0x60
        environment = bias and s["input_period"] == XO_PERIOD and words[0x528] == 0xC0 and words[0x3C] == 1
        same(bank["config-complete"], complete, prefix + ".complete")
        same(bank["recipe-valid"], valid, prefix + ".recipe")
        same(bank["environment-qualified"], environment, prefix + ".environment")
        same(bank["cphy"], bool(words[0x18] & 0x40), prefix + ".cphy")
        same(bank["external-selected"], bool(words[0x14] & 4), prefix + ".external selection")
        same(bank["external-known"], False, prefix + ".external carrier must remain unknown")
        same(bank["lock-pending"], bank["lock-deadline"] >= 0, prefix + ".pending")
        same(bank["configured-vco-hz"], FREF * factor(words) // (1 << 18) if profile else 0,
             prefix + ".configured nominal rate")
        have_capture = bank["pll-locked"] or bank["lock-pending"]
        if have_capture:
            need(environment and complete and recipe(captured), prefix + ": unqualified lock transaction")
            same(bank["captured-xo-period"], XO_PERIOD, prefix + ".captured XO")
            need(bank["lock-qualify-after"] >= 0, prefix + ": missing qualify-after")
            for offset in OFFSETS:
                if offset >= 0x500 and offset not in STATUS and offset != 0x654:
                    same(captured[offset], words[offset], prefix + f".transaction reg {offset:04x}")
            same(captured[0x18], words[0x18], prefix + ".captured mode")
            same(captured[0x24] & 0x60, words[0x24] & 0x60, prefix + ".captured bias")
            same(captured[0x3C], words[0x3C], prefix + ".captured enable")
        else:
            same(list(captured.values()), [0] * 154, prefix + ".uncaptured words")
            same(bank["captured-xo-period"], 0, prefix + ".uncaptured XO")
            same(bank["lock-qualify-after"], -1, prefix + ".uncaptured qualify-after")
        if bank["pll-locked"]:
            need(bank["bias-settled"] and not bank["lock-pending"], prefix + ": premature lock")
            same(bank["lock-deadline"], -1, prefix + ".locked deadline")
        if bank["reset-asserted"] or bank["digital-ready"]:
            need(bank["pll-locked"], prefix + ": reset credit before qualified LOCK")
        if bank["reset-asserted"]:
            same(words[0x128], 1, prefix + ".asserted reset raw bit")
            same(bank["digital-ready"], False, prefix + ".asserted reset cannot be ready")
        if bank["digital-ready"]:
            same(words[0x128], 0, prefix + ".ready reset raw bit")
        if bank["bias-settled"]:
            need(bias and bank["bias-deadline"] == -1, prefix + ": stale bias qualification")
        same(bank["active-vco-hz"], FREF * factor(captured) // (1 << 18) if bank["pll-locked"] else 0,
             prefix + ".active nominal rate")
        if not profile:
            same(list(words.values()), [0] * 154, prefix + ".disabled registers")
            same(list(written.values()), [False] * 154, prefix + ".disabled qualification")
        all_rates += rates(words, captured, bank)
    expected_periods = {name: U // rate if rate else 0 for name, rate in zip(NAMES, all_rates)}
    expected_hz = {name: U // p if p else 0 for name, p in expected_periods.items()}
    same(s["outputs"], expected_periods, where + ".52 derived output periods")
    same(s["physical_outputs"], expected_periods, where + ".52 actual Clock periods")
    same(s["output_hz"], expected_hz, where + ".52 observed integer rates")
    same(world["dispcc_carrier_periods"], {name: expected_periods[name] for name in CARRIERS},
         where + ".four physically connected DISPCC carriers")
    same(set(world["dispcc_pixel_roots"]), {"disp_cc_mdss_pclk0_clk_src", "disp_cc_mdss_pclk1_clk_src"},
         where + ".DISPCC pixel root keys")
    for name, values in world["dispcc_pixel_roots"].items():
        same(set(values), {"period", "hz"}, where + ".pixel observer keys")
        p = uint(values["period"], 64, where + ".pixel period")
        same(values["hz"], U // p if p else 0, where + ".pixel observer integer rate")
    same(set(m), {"enabled-reference", "globals", "parent-known", "parent-on", "domains"}, where + ".mdss keys")
    same(m["enabled-reference"], True, where + ".MDSS reference supplier")
    same(m["globals"], dict(zip(GDSC_GLOBALS, GDSC_VALUES)), where + ".MDSS identity")
    need(type(m["domains"]) is list and len(m["domains"]) == 1, where + ": MDSS domain inventory")
    for field in ("parent-known", "parent-on"):
        boolean(m[field], where + ".mdss." + field)
    d = m["domains"][0]
    same(set(d), set(GDSC_FIELDS), where + ".MDSS domain fields")
    for key, value in (("name", "mdss-core"), ("address", 0x0AF03000), ("dt-flags", 3), ("timeout-us", 100)):
        same(d[key], value, where + ".MDSS." + key)
    control = uint(d["control"], 32, where + ".MDSS control")
    need(control & ~0xF803 == 0, where + ": MDSS reserved bits")
    for key in GDSC_FIELDS[5:]:
        boolean(d[key], where + ".MDSS." + key)
    requests = ({d["hw-request"]} if d["hw-request-known"] else {False, True}) if control & 2 else {not bool(control & 1)}
    parents = {m["parent-on"]} if m["parent-known"] else {False, True}
    states = {r and p for r in requests for p in parents}
    same(d["request-known"], len(requests) == 1, where + ".MDSS request known")
    same(d["requested"], requests == {True}, where + ".MDSS request")
    same(d["state-known"], len(states) == 1, where + ".MDSS state known")
    same(d["powered"], states == {True}, where + ".MDSS powered")
    same(s["power-known"], d["state-known"], where + ".connected power-known")
    same(s["power-on"], d["powered"], where + ".connected power-on")
    return {"dsi_payload_sha256": sha(dsi_wire(s)), "mdss_payload_sha256": sha(mdss_wire(m)),
            "clock_periods_checked": 52, "actual_dispcc_carriers_checked": 4,
            "global_identity_checked": True, "source_recipe_and_clock_math_checked": True}


def flat_world(world):
    """The actual sequence of 1163 getter arguments, independent of serializer."""
    s, m, result = world["dsi"], world["mdss"], []
    def add(path, prop, value):
        result.append(({"path": path, "property": prop}, value))
    for name in GLOBALS:
        add(DEVICE, name, s["globals"][name])
    for group, prefix in (("registers", "reg"), ("written", "written"), ("captured", "captured")):
        for key in KEYS:
            add(DEVICE, prefix + "." + key, s[group][key])
    for b in range(2):
        for name in BANK_FIELDS:
            add(DEVICE, f"bank{b}-" + name, s["banks"][b][name])
    for name in ("last-xo-period", "power-known", "power-on"):
        add(DEVICE, name, s[name])
    for name in NAMES:
        add(DEVICE, "clk." + name + ".period", s["outputs"][name])
    for name in NAMES:
        add(DEVICE, "clk." + name + ".hz", s["output_hz"][name])
    for name in NAMES:
        add(DEVICE + "/" + name, "qtest-clock-period", s["physical_outputs"][name])
    add(DEVICE + "/bi_tcxo", "qtest-clock-period", s["input_period"])
    add(MDSS, "enabled-reference", m["enabled-reference"])
    for name in GDSC_GLOBALS:
        add(MDSS, name, m["globals"][name])
    for name in ("parent-known", "parent-on"):
        add(MDSS, name, m[name])
    for name in GDSC_FIELDS:
        add(MDSS, "domain0." + name, m["domains"][0][name])
    for name in CARRIERS:
        add(DISPCC + "/" + name, "qtest-clock-period", world["dispcc_carrier_periods"][name])
    for b in range(2):
        name = f"disp_cc_mdss_pclk{b}_clk_src"
        add(DISPCC + "/" + name, "qtest-clock-period", world["dispcc_pixel_roots"][name]["period"])
        add(DISPCC, "clk." + name + ".hz", world["dispcc_pixel_roots"][name]["hz"])
    same(len(result), GETTERS, "Independent complete-world getter map")
    return result


def scan_stream(store, path):
    """Hash the whole stream, locate real full sections and check their footers."""
    contracts = (("dsi", TYPE, DSI_BYTES), ("mdss", MDSS_TYPE, MDSS_BYTES))
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


def is_mmio(call):
    parts = call["command"].split()
    if not parts or parts[0] not in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq"):
        return False
    address = int(parts[1], 0)
    camera = (0xAD07004, 0xAD08004, 0xAD09004, 0xAD0A004, 0xAD0B004, 0xAD0C144)
    return (BASE <= address < BASE + STRIDE + 0x800 or 0xAF00000 <= address < 0xAF20000 or
            0x100000 <= address < 0x2F0000 or 0x18200000 <= address < 0x18230000 or
            any(base <= address < base + 4 for base in camera))


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


def command_contract(store, directory, report, profile, incoming=False, rpmh=False):
    command = store.json(directory + "/command.json")
    need(type(command) is list and all(type(x) is str for x in command), directory + ": invalid command argv")
    same(command[0], report["qemu"], directory + ".actual executable path")
    need(command.count("-S") == 1, directory + ": stopped startup missing")
    for forbidden in ("-kernel", "-initrd", "-append", "-drive", "-blockdev", "-device"):
        need(forbidden not in command, directory + ": unexpected guest input " + forbidden)
    def value(option):
        same(command.count(option), 1, directory + "." + option + " count")
        return command[command.index(option) + 1]
    same(value("-accel"), "qtest", directory + ".actual accelerator")
    same(value("-m"), "128M", directory + ".memory")
    same(value("-smp"), "8", directory + ".vCPU count")
    machine = value("-machine").split(",")
    same(machine[0], "tb371fc", directory + ".board")
    need(("rpmh-profile=" + ("reference" if rpmh else "off")) in machine and
         "rpmh-boot-configured=off" in machine and "ufs-enabled=off" in machine and
         "gcc-boot-configured=off" in machine, directory + ": provider/guest board options changed")
    globals_ = [command[i + 1] for i, x in enumerate(command) if x == "-global"]
    expected = [MDSS_TYPE + ".enabled-reference=on", "qcom-sm8250-dispcc.enabled-reference=on",
                TYPE + ".enabled-reference=" + ("on" if profile else "off")]
    same(globals_, expected, directory + ".explicit device profiles")
    same("-incoming" in command, incoming, directory + ".incoming option")
    if incoming:
        same(value("-incoming"), "defer", directory + ".deferred incoming")
    value("-qtest")
    value("-qmp")
    stderr = store.text(directory + "/qemu.stderr")
    return stderr


def source_trace(store, directory, saved, report, profile):
    stderr = command_contract(store, directory, report, profile)
    need("load of migration failed" not in stderr, directory + ": source load failed")
    calls, expected = qmp_calls(store, directory), flat_world(saved["snapshot"])
    migrations = [i for i, call in enumerate(calls) if operation(call) == "migrate"]
    same(len(migrations), 1, directory + ".actual source migration count")
    end = migrations[0]
    migration_argument(calls[end], saved["file"])
    reply(calls[end])
    stops = [i for i, call in enumerate(calls[:end]) if operation(call) == "stop"]
    need(stops, directory + ": no actual source stop")
    stop = stops[-1]
    reply(calls[stop])
    same(operation(calls[stop + 1]), "query-status", directory + ".stopped status query")
    same(reply(calls[stop + 1]).get("running"), False, directory + ".source stopped")
    qom_block(calls[stop + 2:end], expected, directory + ".stopped source")
    tail = calls[end + 1:]
    i = 0
    while i < len(tail) and operation(tail[i]) == "query-migrate":
        migration = reply(tail[i])
        need(type(migration) is dict and migration.get("status") not in ("failed", "cancelled"),
             directory + ": source migration failed")
        if migration.get("status") == "completed":
            break
        i += 1
    need(i < len(tail) and operation(tail[i]) == "query-migrate", directory + ": no actual completed source query")
    same(reply(tail[i]), saved["source_query_migrate"], directory + ".completed query receipt")
    same(reply(tail[i]).get("status"), "completed", directory + ".migration status")
    same(store.json(directory + "/source-query-migrate.json"), reply(tail[i]), directory + ".saved query")
    same(operation(tail[i + 1]), "query-status", directory + ".postmigrate query")
    same(reply(tail[i + 1]).get("status"), "postmigrate", directory + ".postmigrate status")
    qom_block(tail[i + 2:], expected, directory + ".source after migration")
    same(sum(operation(c) == "cont" for c in calls[stop + 1:]), 0, directory + ".no post-stop source cont")
    qt = qtest_calls(store, directory)
    same(qtest_time(qt, directory), saved["source_virtual_ns"], directory + ".actual source epoch")
    need(qt, directory + ": absent source QTest")
    same(qt[-1]["command"].split(), ["writel", hex(RAM), hex(saved["ram_witness"])],
         directory + ".last actual source operation is RAM witness")
    return {"qmp_getters_before_save": GETTERS, "qmp_getters_after_save": GETTERS,
            "actual_completed_response": True, "stopped_and_postmigrate_observed": True,
            "source_virtual_ns": saved["source_virtual_ns"], "qtest_commands": len(qt)}


def target_trace(store, directory, saved, completed, report):
    stderr = command_contract(store, directory, report, 1, incoming=True)
    need("load of migration failed" not in stderr, directory + ": successful target has load failure")
    before = store.json(directory + "/destination-before-incoming.json")
    first = store.json(directory + "/destination-before-first-device-mmio.json")
    same(first, completed["first_target_observation"], directory + ".saved/reported first observation")
    same(first["snapshot"], saved["snapshot"], directory + ".complete first target/source state")
    same(before["source_epoch_ns"], saved["source_virtual_ns"], directory + ".stale target epoch")
    same(before["stale_pending_banks"], [0, 1], directory + ".stale target pending inventory")
    need(before["snapshot"] != first["snapshot"] and before["snapshot"]["mdss"] != first["snapshot"]["mdss"],
         directory + ": target was not independently dirtied")
    check_world(before["snapshot"], 1, directory + ".stale target")
    for b in before["snapshot"]["dsi"]["banks"]:
        need(b["lock-pending"] and not b["pll-locked"], directory + ": stale target did not contain pending work")
    for key in ("device_mmio_since_incoming", "qmp_cont_since_incoming", "guest_cpu_executed", "shared_log_timestamp_claimed"):
        same(first[key], False, directory + "." + key)
    same(first["first_observation_qom_get_count"], GETTERS, directory + ".first getter count")
    same(first["device_mmio_count_before_incoming"], first["device_mmio_count_after_first_observation"],
         directory + ".MMIO delta")
    same(first["device_mmio_count_before_incoming"], before["device_mmio_count"], directory + ".prewarm MMIO count")
    same(first["qmp_cont_count_before_incoming"], 0, directory + ".no target cont before incoming")
    same(first["qmp_cont_count_after_first_observation"], 0, directory + ".no target cont before first observation")
    same(before["qmp_cont_count"], 0, directory + ".stale target cont count")
    calls = qmp_calls(store, directory)
    incoming = [i for i, c in enumerate(calls) if operation(c) == "migrate-incoming"]
    same(len(incoming), 1, directory + ".actual incoming count")
    ix = incoming[0]
    migration_argument(calls[ix], saved["file"])
    reply(calls[ix])
    # Prewarm's full snapshot is the last contiguous getter block before incoming.
    qom_block(calls[ix - GETTERS:ix], flat_world(before["snapshot"]), directory + ".stale QMP snapshot")
    same(sum(operation(c) == "qom-get" for c in calls[:ix]), before["qom_get_count"],
         directory + ".actual prewarm QOM counter")
    same(sum(operation(c) == "cont" for c in calls[:ix]), 0, directory + ".raw no pre-incoming cont")
    at = ix + 1
    while at < len(calls) and operation(calls[at]) == "query-migrate":
        state = reply(calls[at])
        need(type(state) is dict and state.get("status") not in ("failed", "cancelled"),
             directory + ": incoming failed")
        if state.get("status") == "completed":
            break
        at += 1
    need(at < len(calls) and operation(calls[at]) == "query-migrate", directory + ": no actual incoming completed response")
    same(reply(calls[at]), first["query_migrate"], directory + ".saved actual completed response")
    same(reply(calls[at]).get("status"), "completed", directory + ".incoming status")
    same(operation(calls[at + 1]), "query-status", directory + ".first status query")
    same(reply(calls[at + 1]).get("running"), False, directory + ".incoming remained stopped")
    start, end = at + 2, at + 2 + GETTERS
    qom_block(calls[start:end], flat_world(first["snapshot"]), directory + ".pure first target QMP")
    actual_before = sum(operation(c) == "qom-get" for c in calls[:start])
    same(first["qom_get_count_before_first_observation"], actual_before, directory + ".actual first getter counter before")
    same(first["qom_get_count_after_first_observation"], actual_before + GETTERS, directory + ".actual first getter counter after")
    same(operation(calls[end]), "cont", directory + ".first cont occurs after complete observation")
    reply(calls[end])
    same(sum(operation(c) == "cont" for c in calls[:end]), 0, directory + ".QMP transcript proves zero preceding cont")
    qt = qtest_calls(store, directory)
    qtest_time(qt, directory)
    read_indices = [i for i, c in enumerate(qt) if c["command"].split() == ["readl", hex(RAM)]]
    same(len(read_indices), 1, directory + ".first target RAM witness read count")
    ram_read = read_indices[0]
    same(int(qt[ram_read]["reply"].split()[1], 0), saved["ram_witness"], directory + ".actual restored RAM witness")
    same(first["ram_witness"], saved["ram_witness"], directory + ".RAM witness receipt")
    clock_sets = [i for i, c in enumerate(qt[:ram_read]) if c["command"].split() == ["clock_set", str(saved["source_virtual_ns"])]]
    same(len(clock_sets), 2, directory + ".prewarm/receive actual epoch setting")
    boundary = clock_sets[-1]
    same(boundary + 1, ram_read, directory + ".no QTest commands in pure first observation interval")
    count_before = sum(is_mmio(c) for c in qt[:boundary])
    count_after = sum(is_mmio(c) for c in qt[:ram_read])
    same(count_before, first["device_mmio_count_before_incoming"], directory + ".actual MMIO before incoming")
    same(count_after, first["device_mmio_count_after_first_observation"], directory + ".actual MMIO before RAM witness")
    same(qt[boundary]["virtual_ns_before"], saved["source_virtual_ns"], directory + ".target already at source epoch")
    dirty_writes = [c for c in qt[:boundary] if c["command"].split()[:2] == ["writel", hex(RAM)]]
    same(len(dirty_writes), 1, directory + ".dirty target RAM write")
    same(int(dirty_writes[0]["command"].split()[2], 0), saved["ram_witness"] ^ 0xFFFFFFFF,
         directory + ".dirty witness was different")
    # Independently check the observed post-continue clocks, not just a
    # continued_after_restore flag. Generic /n and fixed-factor rules differ.
    cursor = end + 1
    for b in range(2):
        words = {o: saved["snapshot"]["dsi"]["registers"][f"{b * STRIDE + o:04x}"] for o in OFFSETS}
        capture = {o: saved["snapshot"]["dsi"]["captured"][f"{b * STRIDE + o:04x}"] for o in OFFSETS}
        words[0x14] |= 0x30
        words[0x30], words[0x128] = 4, 0
        bank = dict(saved["snapshot"]["dsi"]["banks"][b], **{"pll-locked": True, "digital-ready": True})
        for name, rate in zip((NAMES0, NAMES1)[b], rates(words, capture, bank)):
            p = U // rate if rate else 0
            queries = ((DEVICE, "clk." + name + ".period", p),
                       (DEVICE + "/" + name, "qtest-clock-period", p),
                       (DEVICE, "clk." + name + ".hz", U // p if p else 0))
            for path, prop, expected_value in queries:
                while cursor < len(calls):
                    call = calls[cursor]
                    cursor += 1
                    if operation(call) == "qom-get" and call["request"].get("arguments") == {"path": path, "property": prop}:
                        same(reply(call), expected_value, directory + ".post-restore " + prop)
                        break
                else:
                    raise AuditFailure(directory + ": missing post-restore actual clock query " + prop)
    after_steps = [int(c["command"].split()[1], 0) for c in qt[ram_read + 1:]
                   if c["command"].startswith("clock_step ")]
    expected_steps = {"migration_steady_high_carriers": [],
                      "migration_pending_bias_and_lock": [249, 1, 3333, 1],
                      "migration_pending_lock": [2333, 1]}
    same(after_steps, expected_steps[completed["name"]], directory + ".actual remaining deadline steps")
    return {"qmp_first_observation_getters": GETTERS, "all_first_values_match_stopped_source": True,
            "actual_mmio_count_before_incoming": count_before, "actual_mmio_count_before_first_ram_read": count_after,
            "actual_mmio_delta": count_after - count_before, "actual_qmp_cont_before_observation": 0,
            "qmp_sequence_completed_status_then_getters_then_cont": True, "actual_ram_witness_matches": True,
            "qtest_trace_has_no_intervening_commands": True, "shared_timestamp_inferred": False,
            "actual_post_restore_clock_views_checked": 52,
            "actual_post_restore_deadline_steps_ns": after_steps}


def rejection_trace(store, directory, row, report, filename, epoch, diagnostic, profile):
    same(row["destination_exit"], 1, directory + ".actual process exit receipt")
    same(row["specific_load_failure_observed"], True, directory + ".specific load failure receipt")
    same(row["expected_diagnostic"], diagnostic, directory + ".specific diagnostic contract")
    same(row["target_directory"], PurePosixPath(directory).name, directory + ".target directory")
    stderr = command_contract(store, directory, report, profile, incoming=True)
    for token in (TYPE, diagnostic, "load of migration failed", "error while loading state"):
        need(token in stderr, directory + ": actual stderr missing " + token)
    calls = qmp_calls(store, directory)
    incoming = [c for c in calls if operation(c) == "migrate-incoming"]
    same(len(incoming), 1, directory + ".actual rejected incoming count")
    migration_argument(incoming[0], filename)
    same(sum(operation(c) == "cont" for c in calls), 0, directory + ".rejected target cont count")
    need(not any(operation(c) == "query-migrate" and c["response"] is not None and
                 c["response"].get("return", {}).get("status") == "completed" for c in calls),
         directory + ": rejected migration actually completed")
    qt = qtest_calls(store, directory)
    same(len(qt), 1, directory + ".rejected target only sets virtual epoch")
    same(qt[0]["command"], f"clock_set {epoch}", directory + ".rejected target source epoch")
    same(qtest_time(qt, directory), epoch, directory + ".actual rejected epoch")
    same(sum(is_mmio(c) for c in qt), 0, directory + ".no rejected target MMIO")
    return {"target_directory": directory, "process_exit": 1, "specific_actual_diagnostic": diagnostic,
            "incoming_invocations": 1, "actual_cont_count": 0, "actual_mmio_count": 0,
            "process_exit_evidence": "reviewed harness process.wait return in receipt; actual failure stderr independently checked",
            "query_migrate_failed_response_required": False}


def scenario_contract(saved, scenario):
    s = saved["snapshot"]["dsi"]
    same(saved["source_directory"], "migration-" + scenario + "-source", scenario + ".source directory")
    same(saved["file"], "dsi-" + scenario + ".migration", scenario + ".original filename")
    same(saved["stream_modified"], False, scenario + ".source is unmodified")
    same(saved["source_status"], "completed", scenario + ".source status")
    same(s["input_period"], XO_PERIOD, scenario + ".real board XO")
    same(s["power-known"], True, scenario + ".MDSS supplier known")
    same(s["power-on"], True, scenario + ".MDSS supplier on")
    epoch = {"steady": 109168, "pending-bias": 200000, "pending-lock": 301250, "disabled": 400000}[scenario]
    witness = {"steady": 0x44534931, "pending-bias": 0x44534932,
               "pending-lock": 0x44534933, "disabled": 0x44534930}[scenario]
    same(saved["source_virtual_ns"], epoch, scenario + ".literal source epoch")
    same(saved["ram_witness"], witness, scenario + ".literal RAM witness")
    expected_requests = {"steady": (4_500_000_000, 5_000_000_000),
                         "pending-bias": (4_500_000_000, 3_100_000_000),
                         "pending-lock": (4_500_000_000, 4_400_000_000), "disabled": (0, 0)}[scenario]
    for b, state in enumerate(s["banks"]):
        same(state["pll-locked"], scenario == "steady", scenario + f".bank{b} LOCK")
        same(state["digital-ready"], scenario == "steady", scenario + f".bank{b} digital ready")
        same(state["reset-asserted"], False, scenario + f".bank{b} reset not asserted")
        same(state["lock-pending"], scenario in ("pending-bias", "pending-lock"), scenario + f".bank{b} pending")
        same(state["bias-settled"], scenario in ("steady", "pending-lock"), scenario + f".bank{b} bias")
        same(state["bias-deadline"], 200250 if scenario == "pending-bias" else -1, scenario + f".bank{b} bias deadline")
        same(state["lock-deadline"], {"steady": -1, "pending-bias": 203584, "pending-lock": 303584, "disabled": -1}[scenario],
             scenario + f".bank{b} lock deadline")
        qualify = {"steady": (100250, 103834), "pending-bias": (200250, 200250),
                   "pending-lock": (300250, 300250), "disabled": (-1, -1)}[scenario][b]
        same(state["lock-qualify-after"], qualify, scenario + f".bank{b} qualification epoch")
        same(state["cphy"], scenario == "pending-bias" and b == 1, scenario + f".bank{b} PHY mode")
        same(state["config-complete"], scenario != "disabled", scenario + f".bank{b} programmed-word qualification")
        same(state["recipe-valid"], scenario != "disabled", scenario + f".bank{b} literal source recipe")
        req = expected_requests[b]
        raw_words = {o: s["registers"][f"{b * STRIDE + o:04x}"] for o in OFFSETS}
        same(factor(raw_words), req * (1 << 18) // FREF, scenario + f".bank{b} requested quantized factor")
        root = saved["snapshot"]["dispcc_pixel_roots"][f"disp_cc_mdss_pclk{b}_clk_src"]
        if scenario == "steady":
            carrier_period = s["outputs"][f"dsi{b}_phy_pll_out_dsiclk"]
            expected_period = U // (U // carrier_period)
            same(root, {"period": expected_period, "hz": U // expected_period}, scenario + f".actual pixel root {b}")
        else:
            same(root, {"period": 0, "hz": 0}, scenario + f".inactive pixel root {b}")


def runtime_xo_trace(store, data_root, report):
    name = ORDINARY[-1]
    row = report["checks"][24]
    same(row["name"], name, "runtime XO case name")
    for key in ("runtime_xo_loss_exercised", "mdss_remained_on", "active_and_pending_capture_cancelled", "new_request_edge_required"):
        same(row.get(key), True, "runtime XO." + key)
    same(row.get("actual_xo_resource"), "xo.lvl", "runtime XO provider resource")
    same(row.get("actual_supply_resource"), "mmcx.lvl", "runtime XO supplier resource")
    fixture = "ordinary-24-" + name
    same(row.get("fixture"), fixture, "runtime XO actual fixture")
    directory = data_root + "/" + fixture
    command_contract(store, directory, report, 1, rpmh=True)
    calls = qmp_calls(store, directory)
    values = [reply(c) for c in calls if operation(c) == "qom-get" and
              c["request"].get("arguments") == {"path": DEVICE + "/bi_tcxo", "property": "qtest-clock-period"}]
    collapsed = []
    for value in values:
        if not collapsed or value != collapsed[-1]:
            collapsed.append(value)
    same(collapsed, [0, XO_PERIOD, 0, XO_PERIOD], "actual connected RPMh XO period transitions")
    need(not any(operation(c) == "qom-set" for c in calls), "Runtime XO must not use QOM clock overrides")
    qt = qtest_calls(store, directory)
    votes = []
    for c in qt:
        parts = c["command"].split()
        need(not (parts[0] == "set_irq_in" and parts[1] in (DEVICE, MDSS)),
             "Runtime XO/supply test bypassed connected providers")
        if parts[0] == "writel" and 0x18220000 <= int(parts[1], 0) < 0x18230000:
            votes.append(c)
    need(votes, "Runtime XO case has no actual RPMh DRV2 writes")
    for b in range(2):
        actual = [reply(c) for c in calls if operation(c) == "qom-get" and
                  c["request"].get("arguments") == {"path": DEVICE, "property": f"bank{b}-pll-locked"}]
        need(True in actual and False in actual, f"Runtime XO bank{b} lacks both locked/unlocked actual observations")
    return {"actual_connected_period_sequence": collapsed, "actual_rpmh_drv2_writes": len(votes),
            "no_connected_provider_override": True, "both_banks_observed_locked_and_unlocked": True}


def report_contract(report, expected_qemu, expected_test):
    fields = {
        "schema": "tb371fc-dsi-pll-7nm-v1", "suite": "dsi-pll-7nm-reference",
        "test_script_sha256": expected_test, "expected_checks": 36,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 4, "expected_unmodified_streams": 4,
        "expected_stream_files": 8, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": 2926, "reference_identity": 0x44534934, "source_variant": 0x401,
        "physical_base": BASE, "bank_stride": STRIDE, "bank_count": 2, "bank_container_bytes": 0x800,
        "words_per_bank": 154, "register_count": 308, "sparse_window_count": 16, "mapped_bytes": 1232,
        "output_clock_count": 52, "physical_pll_count": 2, "input_clock_count": 1,
        "connected_input_clock_count": 1, "dispcc_carrier_connection_count": 4,
        "required_programmed_words_per_bank": 128, "status_zero_only_words_per_bank": 15,
        "bias_settle_ns": 250, "lock_reference_cycles": 64, "lock_reference_ns_ceiling": 3334,
        "mdss_vmstate_payload_bytes": 64, "mdss_vmstate_version": 1, "reference_enabled_by_default": False,
        "generic_dividers_round_up": True, "fixed_factors_round_down": True,
        "frequency_checks_use_exact_clock_period": True, "first_observation_qom_get_count": 1163,
        "runtime_xo_change_tested": True, "source_commit": PRIMARY,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "stock_compiled_dsi_source_identity_claimed": False, "guest_cpu_executed": False,
        "native_kernel_probe_observed": False, "analog_pll_behavior_modeled": False,
        "android_boot_passed": False, "binary_unchanged": True, "passed": True,
        "qemu_sha256_before": expected_qemu, "qemu_sha256_after": expected_qemu,
    }
    need(type(report) is dict, "DSI result is not an object")
    need(not any(k in report for k in ("error", "traceback")), "DSI result recorded an exception")
    for key, value in fields.items():
        same(report.get(key), value, "DSI result." + key)
    need(type(report.get("checks")) is list, "DSI check inventory is not a list")
    same([c.get("name") for c in report["checks"]], list(CHECK_NAMES), "Independent exact 36-check inventory")
    for c in report["checks"]:
        same(c.get("passed"), True, "DSI check." + c["name"])
    for key, count in (("migrations", 3), ("rejected_migrations", 2), ("corrupted_stream_rejections", 4),
                       ("migration_sources", 4), ("actual_stream_files", 8)):
        need(type(report.get(key)) is list, "DSI list missing: " + key)
        same(len(report[key]), count, "DSI result." + key + " count")
    same([r["name"] for r in report["migrations"]], list(COMPLETED), "Completed migration inventory")
    same([r["name"] for r in report["rejected_migrations"]], ["on_to_off", "off_to_on"], "Profile rejection inventory")
    same([r["name"] for r in report["corrupted_stream_rejections"]], list(CORRUPT), "Corruption inventory")
    same([r["file"] for r in report["migration_sources"]],
         ["dsi-" + scenario + ".migration" for scenario in SCENARIOS], "Original migration inventory")


def select_component(store, args, receipt):
    cases = [name for name in store.entries if
             re.search(r"(?:^|/)components/[0-9]+-dsi-pll-7nm-reference/case\.json$", name)]
    same(len(cases), 1, "Unique actual DSI component case")
    case_path = cases[0]
    root = case_path.rsplit("/", 1)[0]
    result_path = root + "/fixtures/data/result.json"
    case, report = store.json(case_path), store.json(result_path)
    same(case.get("name"), "dsi-pll-7nm-reference", "Selected CI case name")
    same(case.get("passed"), True, "Selected CI case verdict")
    same(case.get("exit_code"), 0, "Actual DSI harness process exit")
    need(not any(k in case for k in ("error", "artifact_error", "timed_out")), "Incomplete actual DSI process")
    need(type(case.get("completed_utc")) is str and case["completed_utc"], "Missing DSI process completion")
    report_contract(report, args.expected_qemu_sha256, args.expected_test_sha256)
    for name in ("stdout.log", "stderr.log"):
        raw = store.read(root + "/" + name)
        same(case.get("log_sha256", {}).get(name), sha(raw), "Actual harness " + name + " hash")
        if name == "stdout.log":
            same(json_value(raw, root + "/" + name), report, "Actual stdout versus saved DSI result")
    verified = case.get("verified")
    need(type(verified) is dict, "Missing CI case adjudication")
    same(verified.get("verdict"), True, "CI verdict result")
    same(verified.get("verdict_field"), "passed", "CI verdict field")
    same(verified.get("check_count"), 36, "CI adjudicated check count")
    same(verified.get("verified_lengths"), {"migrations": 3, "rejected_migrations": 2,
                                           "corrupted_stream_rejections": 4,
                                           "migration_sources": 4, "actual_stream_files": 8},
         "CI adjudicated collection lengths")
    argv = case.get("command")
    need(type(argv) is list and len(argv) >= 6 and all(type(x) is str for x in argv), "Bad harness argv")
    need("-O" not in argv and "-OO" not in argv, "Optimized harness disabled assertions")
    same(PurePosixPath(argv[1]).name, "test_dsi_pll_7nm.py", "Actual harness script")
    for option, expected in (("--qemu", report["qemu"]), ("--out", report["work_dir"])):
        same(argv.count(option), 1, "Actual harness " + option + " count")
        same(argv[argv.index(option) + 1], expected, "Actual harness " + option)
    same(PurePosixPath(report["work_dir"]), PurePosixPath(case["fixture"]) / "data", "Harness output belongs to case")
    source_prefix = root.split("/components/", 1)[0] if "/components/" in root else ""
    source_path = (source_prefix + "/" if source_prefix else "") + "source-commit.txt"
    same(store.text(source_path).strip(), args.expected_head, "Actual archived source commit")
    receipt["component"] = {"case": case_path, "result": result_path, "source_commit_file": source_path,
                            "head": args.expected_head, "test_sha256": args.expected_test_sha256,
                            "qemu_sha256_from_separately_audited_binary": args.expected_qemu_sha256,
                            "harness_exit": 0, "exact_checks": 36}
    return root + "/fixtures/data", report


def audit_migrations(store, data_root, report, receipt):
    actual_names = sorted(name[len(data_root) + 1:] for name in store.entries
                          if name.startswith(data_root + "/") and "/" not in name[len(data_root) + 1:]
                          and name.endswith(".migration"))
    names = sorted(["dsi-" + s + ".migration" for s in SCENARIOS] + ["dsi-corrupt-" + s + ".migration" for s in CORRUPT])
    same(actual_names, names, "Actual archive has exactly four originals and four mutants")
    same([r["name"] for r in report["actual_stream_files"]], names, "Actual stream list order/inventory")
    recorded = {r["name"]: r for r in report["actual_stream_files"]}
    streams, saved_by_name = {}, {}
    receipt["original_streams"], receipt["completed_migrations"], receipt["rejected_migrations"], receipt["single_bit_rejections"] = [], [], [], []
    for scenario, saved in zip(SCENARIOS, report["migration_sources"]):
        scenario_contract(saved, scenario)
        where = data_root + "/" + saved["source_directory"]
        first, after = store.json(where + "/source-before-save.json"), store.json(where + "/source-after-save.json")
        same(first, saved["snapshot"], scenario + ".saved actual source QOM")
        same(after, first, scenario + ".source unchanged across save")
        world_check = check_world(first, int(scenario != "disabled"), scenario)
        full_path = data_root + "/" + saved["file"]
        stream = scan_stream(store, full_path)
        same(stream["sha256"], saved["sha256"], full_path + ".source report digest")
        same(stream["bytes"], saved["bytes"], full_path + ".source report size")
        same(recorded[saved["file"]], {"name": saved["file"], "bytes": stream["bytes"], "sha256": stream["sha256"]},
             full_path + ".actual inventory")
        sidecar = store.json(full_path + ".sections.json")
        same(sidecar, saved["sections"], full_path + ".reported/saved section metadata")
        same(set(sidecar), {"dsi", "mdss"}, full_path + ".section inventory")
        for label, expected_payload in (("dsi", dsi_wire(first["dsi"])), ("mdss", mdss_wire(first["mdss"]))):
            same(stream["payloads"][label], expected_payload, full_path + "." + label + ".all actual payload bytes")
            payload_name = saved["file"] + "." + label + "-v1.payload.bin"
            same(store.read(data_root + "/" + payload_name, 4096), expected_payload,
                 full_path + "." + label + ".complete saved payload bytes")
            expected_section = dict(stream["sections"][label], payload_file=payload_name,
                                    payload_sha256=sha(expected_payload), all_bytes_match_stopped_source_qom=True)
            same(sidecar[label], expected_section, full_path + "." + label + ".actual header/footer metadata")
        decoded = wire_fields(stream["payloads"]["dsi"])
        same(decoded["input_period"], [first["dsi"]["input_period"]], full_path + ".last eight Clock bytes")
        trace = source_trace(store, where, saved, report, int(scenario != "disabled"))
        streams[saved["file"]], saved_by_name[saved["file"]] = stream, saved
        receipt["original_streams"].append({"name": saved["file"], "bytes": stream["bytes"], "sha256": stream["sha256"],
            "complete_dsi_bytes": 2926, "complete_mdss_bytes": 64, "sections": stream["sections"],
            "world": world_check, "trace": trace, "literal_field_groups_checked": list(decoded)})
    for scenario, item in zip(SCENARIOS[:3], report["migrations"]):
        filename = "dsi-" + scenario + ".migration"
        saved, stream = saved_by_name[filename], streams[filename]
        for key, expected in (("passed", True), ("source_status", "completed"), ("target_status", "completed"),
                              ("snapshot_file", filename), ("snapshot_sha256", stream["sha256"]),
                              ("stream_modified", False), ("source_directory", "migration-" + scenario + "-source"),
                              ("target_directory", "migration-" + scenario + "-target"), ("continued_after_restore", True),
                              ("all_2926_payload_bytes_verified", True), ("mdss_64_bytes_verified", True),
                              ("actual_four_dispcc_input_periods_restored", True)):
            same(item.get(key), expected, item["name"] + "." + key)
        specific = {
            "steady": {"restored_high_pixel_rates": True, "post_restore_gate_withdrawal": True},
            "pending-bias": {"bias_completed_after_ns": 250, "lock_completed_after_ns": 3584},
            "pending-lock": {"lock_completed_after_ns": 2334, "actual_mdss_power_loss_revoked_both_banks": True},
        }[scenario]
        for key, value in specific.items():
            same(item.get(key), value, item["name"] + "." + key)
        trace = target_trace(store, data_root + "/" + item["target_directory"], saved, item, report)
        receipt["completed_migrations"].append({"name": item["name"], "source_file": filename,
                                                "actual_stream_sha256": stream["sha256"], "trace": trace})
    for row, filename, profile, diagnostic in (
            (report["rejected_migrations"][0], "dsi-steady.migration", 0, "0 != 1"),
            (report["rejected_migrations"][1], "dsi-disabled.migration", 1, "1 != 0")):
        same(row["passed"], True, row["name"] + ".rejection result")
        same(row["snapshot_file"], filename, row["name"] + ".unmodified rejected file")
        same(row["snapshot_sha256"], streams[filename]["sha256"], row["name"] + ".actual unmodified digest")
        same(row["stream_modified"], False, row["name"] + ".profile stream unmodified")
        trace = rejection_trace(store, data_root + "/reject-profile-" + row["name"], row, report, filename,
                                saved_by_name[filename]["source_virtual_ns"], diagnostic, profile)
        receipt["rejected_migrations"].append({"name": row["name"], "source_file": filename,
                                               "stream_sha256": streams[filename]["sha256"], "trace": trace})
    mutation_contracts = (
        ("physical_base", "dsi-steady.migration", 48, 8, BASE, 4, f"{BASE:x} != {BASE ^ 4:x}"),
        ("captured_factor", "dsi-pending-lock.migration", 1604 + 4 * OFFSETS.index(0x5E0), 4,
         saved_by_name["dsi-pending-lock.migration"]["snapshot"]["dsi"]["captured"]["05e0"], 1,
         TYPE + ": invalid migrated lock transaction 0"),
        ("captured_xo_period", "dsi-pending-bias.migration", 2836, 8, XO_PERIOD, 1,
         TYPE + ": invalid migrated lock transaction 0"),
        ("pending_digital_ready", "dsi-pending-lock.migration", 2915, 1, 0, 1,
         TYPE + ": invalid migrated PHY qualification 1"),
    )
    for row, (name, source_file, relative, width, old, xor, diagnostic) in zip(report["corrupted_stream_rejections"], mutation_contracts):
        filename = "dsi-corrupt-" + name + ".migration"
        source, saved = streams[source_file], saved_by_name[source_file]
        stream = scan_stream(store, data_root + "/" + filename)
        same(row["name"], name, filename + ".case name")
        same(recorded[filename], {"name": filename, "bytes": stream["bytes"], "sha256": stream["sha256"]},
             filename + ".actual inventory")
        same(stream["sections"], source["sections"], filename + ".unchanged section headers and footers")
        same(stream["payloads"]["mdss"], source["payloads"]["mdss"], filename + ".MDSS bytes unchanged")
        expected = bytearray(source["payloads"]["dsi"])
        same(int.from_bytes(expected[relative:relative + width], "big"), old, filename + ".independent old field")
        expected[relative:relative + width] = (old ^ xor).to_bytes(width, "big")
        same(stream["payloads"]["dsi"], bytes(expected), filename + ".exact actual mutated field")
        absolute = source["sections"]["dsi"]["payload_offset"] + relative + width - 1
        for key, value in (("passed", True), ("source_file", source_file), ("source_sha256", source["sha256"]),
                           ("snapshot_file", filename), ("snapshot_sha256", stream["sha256"]), ("stream_modified", True),
                           ("field_offset", relative), ("field_bytes", width), ("old_value", old), ("xor", xor),
                           ("changed_byte_offsets", [absolute]), ("changed_bits", 1), ("original_stream_unchanged", True)):
            same(row.get(key), value, filename + "." + key)
        difference = one_bit_difference(store, data_root + "/" + source_file, data_root + "/" + filename,
                                        absolute, xor, source["sha256"], stream["sha256"])
        trace = rejection_trace(store, data_root + "/reject-corrupt-" + name, row, report, filename,
                                saved["source_virtual_ns"], diagnostic, 1)
        receipt["single_bit_rejections"].append({"name": name, "source_file": source_file,
            "source_sha256": source["sha256"], "file": filename, "bytes": stream["bytes"], "sha256": stream["sha256"],
            "actual_difference": difference, "trace": trace})


def self_check():
    """Bounded in-memory auditor checks; these are NOT compiled-device results."""
    import io
    checks = []
    def record(name, action):
        action()
        checks.append({"name": name, "passed": True})
    def rejects(action):
        try:
            action()
        except AuditFailure:
            return
        raise AuditFailure("Self-check expected a fail-closed rejection")
    class Memory:
        def __init__(self, files):
            self.files, self.observed = files, {}
        def open(self, name):
            return io.BytesIO(self.files[name])
        def size(self, name):
            return len(self.files[name])
        def text(self, name):
            return self.files[name].decode()
    def add_section(name, payload, identity):
        encoded = name.encode()
        return (b"\x04" + identity.to_bytes(4, "big") + bytes([len(encoded)]) + encoded +
                bytes.fromhex("0000000000000001") + payload + b"\x7e" + identity.to_bytes(4, "big"))
    snapshot = {
        "globals": dict(zip(GLOBALS, GLOBAL_VALUES)),
        "registers": {k: i * 7 for i, k in enumerate(KEYS)},
        "written": {k: bool(i & 1) for i, k in enumerate(KEYS)},
        "captured": {k: i * 11 for i, k in enumerate(KEYS)},
        "banks": [
            {"captured-xo-period": 0x123456789ABCDE00 + b, "bias-deadline": -1 + b,
             "lock-deadline": 100 + b, "lock-qualify-after": 200 + b,
             "pll-locked": bool(b), "bias-settled": not bool(b),
             "reset-asserted": False, "digital-ready": bool(b)}
            for b in range(2)],
        "last-xo-period": 0x23456789ABCDEF01, "power-known": True, "power-on": False,
        "input_period": 0x3456789ABCDEF012,
    }
    raw = dsi_wire(snapshot)
    def layout():
        decoded = wire_fields(raw)
        same(len(OFFSETS), 154, "literal word count")
        same(len(REQUIRED), 128, "literal required count")
        same(len(FIXED), 113, "literal fixed count")
        need(set(FIXED_NONZERO) <= set(FIXED), "Nonzero fixed map contains a dynamic field")
        same(len(NAMES), 52, "literal clock count")
        same(raw[48:56], BASE.to_bytes(8, "big"), "literal base bytes")
        same(raw[1292:1296], (307 * 7).to_bytes(4, "big"), "last register offset")
        same(raw[1296:1300], b"\x00\x01\x00\x01", "written byte order")
        same(raw[2832:2836], (307 * 11).to_bytes(4, "big"), "last capture offset")
        same(raw[2836:2844], bytes.fromhex("123456789abcde00"), "captured period offset")
        same(raw[2852:2860], bytes.fromhex("23456789abcdef01"), "last XO offset")
        same(raw[2860:2868], b"\xff" * 8, "signed -1 deadline")
        same(raw[2908:2918], bytes([0, 1, 1, 0, 0, 0, 0, 1, 1, 0]), "boolean tail order")
        same(raw[2918:2926], bytes.fromhex("3456789abcdef012"), "complete Clock trailer")
        same(decoded["lock_deadline"], [100, 101], "literal signed array")
    record("literal_2926_byte_offset_layout_and_clock_tail", layout)
    mdss_snapshot = {"globals": dict(zip(GDSC_GLOBALS, GDSC_VALUES)),
                     "parent-known": True, "parent-on": False,
                     "domains": [{"address": 0xAF03000, "dt-flags": 3, "timeout-us": 100,
                                  "control": 0x1800, "hw-request-known": False, "hw-request": True}]}
    mdss_raw = mdss_wire(mdss_snapshot)
    def mdss_layout():
        same(len(mdss_raw), 64, "MDSS full length")
        same(mdss_raw[36:42], bytes.fromhex("010000000000"), "MDSS parents and id")
        same(mdss_raw[42:50], bytes.fromhex("000000000af03000"), "MDSS u64 address")
        same(mdss_raw[50:64], bytes.fromhex("0000000300000064000018000001"), "MDSS full domain trailer")
    record("literal_64_byte_mdss_layout", mdss_layout)
    original = b"QEVM\x00\x00\x00\x03" + b"\x00" * (CHUNK - 28)
    original += add_section(TYPE, raw, 5) + add_section(MDSS_TYPE, mdss_raw, 7) + b"\x00"
    memory = Memory({"original": original})
    scanned = scan_stream(memory, "original")
    def full_sections():
        same(scanned["sha256"], sha(original), "full stream hash")
        same(scanned["payloads"]["dsi"], raw, "DSI across scan boundary")
        same(scanned["payloads"]["mdss"], mdss_raw, "MDSS full payload")
    record("valid_complete_sections_cross_one_megabyte_scan_boundary", full_sections)
    at = scanned["sections"]["dsi"]["payload_offset"] + 2918 + 7
    changed = bytearray(original)
    changed[at] ^= 1
    memory.files["mutant"] = bytes(changed)
    record("actual_whole_stream_one_bit_difference", lambda: one_bit_difference(
        memory, "original", "mutant", at, 1, sha(original), sha(bytes(changed))))
    changed[at - 1] ^= 1
    memory.files["two_bits"] = bytes(changed)
    record("reject_two_changed_bytes", lambda: rejects(lambda: one_bit_difference(
        memory, "original", "two_bits", at, 1, sha(original), sha(bytes(changed)))))
    changed = bytearray(original)
    changed[at] ^= 3
    memory.files["two_bits_one_byte"] = bytes(changed)
    record("reject_two_bits_in_one_byte", lambda: rejects(lambda: one_bit_difference(
        memory, "original", "two_bits_one_byte", at, 3, sha(original), sha(bytes(changed)))))
    changed = bytearray(original)
    footer = scanned["sections"]["dsi"]["footer_offset"]
    changed[footer + 4] ^= 1
    memory.files["bad_footer"] = bytes(changed)
    record("reject_wrong_complete_section_footer_id", lambda: rejects(lambda: scan_stream(memory, "bad_footer")))
    memory.files["truncated_tail"] = original[:scanned["sections"]["dsi"]["payload_offset"] + 2920]
    record("reject_missing_last_clock_bytes_and_footer", lambda: rejects(lambda: scan_stream(memory, "truncated_tail")))
    memory.files["duplicate"] = original + add_section(TYPE, raw, 9)
    record("reject_duplicate_valid_device_sections", lambda: rejects(lambda: scan_stream(memory, "duplicate")))
    record("reject_boolean_integer_substitution", lambda: rejects(lambda: same(True, 1, "strict JSON value")))
    record("reject_duplicate_json_keys", lambda: rejects(lambda: json_value('{"x": 1, "x": 2}', "duplicate")))
    record("reject_nonfinite_json", lambda: rejects(lambda: json_value('{"x": NaN}', "nonfinite")))
    def clock_math():
        words = {o: 0 for o in OFFSETS}
        captured = dict(words)
        captured.update({0x5E0: 3, 0x5E4: 3})
        words.update({0x10: 0x33, 0x14: 0x20, 0x2C: 0x40, 0x30: 4, 0x654: 1})
        bank = {"pll-locked": True, "digital-ready": True}
        answer = rates(words, captured, bank)
        same(answer[0:10], [115200439, 57600220, 19200074, 2400009, 9600037,
                           14400055, 2400009, 19200074, 6400025, 6400025], "literal ceil/floor graph")
        words[0x14] = 0x30
        same(rates(words, captured, bank), answer, "bit4 does not sustain the global gate")
        words[0x14] = 0x10
        disabled = rates(words, captured, bank)
        same([disabled[6], disabled[9]], [0, 0], "bit5 withdraws final carriers")
        same(disabled[0:6], answer[0:6], "bit5 does not destroy PLL/internal views")
        words[0x14], words[0x18] = 0x23, 0x40
        cphy = rates(words, captured, bank)
        same(cphy[18:22], [2742867, 16457205, 16457205, 5485735], "literal CPHY fixed and generic factors")
        same(cphy[22:26], cphy[18:22], "CPHY shadow views share one PLL")
    record("literal_clock_ceil_floor_shadow_and_bit5_gate", clock_math)
    return {"schema": "tb371fc-dsi-pll-auditor-self-check-v1", "passed": True,
            "actual_ci_artifact_audited": False, "compiled_qemu_executed": False,
            "checks": checks, "count": len(checks)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group()
    inputs.add_argument("--zip", type=Path, help="Actual CI artifact ZIP; read selectively, never extracted")
    inputs.add_argument("--result", type=Path, help="Existing collected CI result directory with the same tree as the ZIP")
    parser.add_argument("--expected-head", help="Exact separately reviewed QEMU source commit (40 hex)")
    parser.add_argument("--expected-qemu-sha256", help="Actual QEMU bytes SHA-256 from the separate binary/aggregate audit")
    parser.add_argument("--expected-zip-sha256", help="Optional exact downloaded ZIP SHA-256")
    parser.add_argument("--expected-test-sha256", default=TEST_SHA, help="Explicit reviewed suite pin; default is frozen gate/profile fix")
    parser.add_argument("--test-source", type=Path, default=Path(__file__).resolve().parents[1] / "tb371fc/tests/test_dsi_pll_7nm.py")
    parser.add_argument("--model-source", type=Path, default=Path(__file__).resolve().parents[1] / "hw/misc/qcom-sm8250-dsi-pll.c")
    parser.add_argument("--out", type=Path, help="New audit JSON path; existing files are never overwritten")
    parser.add_argument("--self-test", action="store_true", help="Only bounded synthetic in-memory checks; never an artifact PASS")
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
                   "timestamps, so this audit does not infer a cross-file total event order.",
               "stages_completed": []}
    store, output = None, None
    try:
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            output = args.out.open("x", encoding="utf-8")
        if args.self_test:
            need(not args.zip and not args.result, "Self-test does not accept an actual artifact")
            receipt = dict(self_check(), auditor_sha256=file_hash(__file__), started_utc=started)
        else:
            need(bool(args.zip) != bool(args.result), "Provide exactly one of --zip or --result")
            for value, size, label in ((args.expected_head, 40, "--expected-head"),
                                       (args.expected_qemu_sha256, 64, "--expected-qemu-sha256"),
                                       (args.expected_test_sha256, 64, "--expected-test-sha256")):
                need(type(value) is str and re.fullmatch("[0-9a-f]{" + str(size) + "}", value), "Invalid " + label)
            need(args.test_source.is_file(), "Exact reviewed test source must be locally available")
            same(file_hash(args.test_source), args.expected_test_sha256, "Actual reviewed test source pin")
            ast.parse(args.test_source.read_text())
            need(args.model_source.is_file(), "Exact reviewed model source must be locally available")
            same(file_hash(args.model_source), MODEL_SHA, "Actual reviewed model source pin")
            receipt["reviewed_sources"] = {
                "test": {"path": str(args.test_source.resolve()), "sha256": args.expected_test_sha256},
                "model": {"path": str(args.model_source.resolve()), "sha256": MODEL_SHA},
                "primary_reference": PRIMARY,
                "primary_reference_is_not_a_claim_of_entire_original_firmware_source_identity": True,
            }
            if args.zip:
                need(args.zip.is_file(), "Actual ZIP does not exist")
                archive_hash = file_hash(args.zip)
                if args.expected_zip_sha256:
                    same(archive_hash, args.expected_zip_sha256, "Actual ZIP SHA-256")
                receipt["input"] = {"kind": "zip", "path": str(args.zip.resolve()),
                                    "bytes": args.zip.stat().st_size, "sha256": archive_hash}
                store = Store(zip_path=args.zip)
            else:
                need(args.expected_zip_sha256 is None, "ZIP hash cannot bind a result directory")
                receipt["input"] = {"kind": "collected-results", "path": str(args.result.resolve())}
                store = Store(directory=args.result)
            receipt["stages_completed"].append("exact_reviewed_source_and_artifact_identity")
            data_root, report = select_component(store, args, receipt)
            receipt["stages_completed"].append("actual_dsi_process_logs_and_exact_36_check_contract")
            audit_migrations(store, data_root, report, receipt)
            receipt["stages_completed"].append("eight_actual_streams_and_complete_payload_protocol_evidence")
            receipt["runtime_xo_evidence"] = runtime_xo_trace(store, data_root, report)
            receipt["stages_completed"].append("actual_connected_rpmh_xo_loss_transcript")
            receipt["members_read"] = store.observed
            store.finish()
            store = None
            receipt["summary"] = {"checks": 36, "unmodified_streams": 4, "stream_files": 8,
                "completed_migrations": 3, "profile_rejections": 2, "single_bit_rejections": 4,
                "dsi_bytes_per_original": 2926, "mdss_bytes_per_original": 64,
                "clock_views_per_source_and_first_target": 52, "first_target_qom_getters_each": 1163,
                "first_target_actual_mmio_delta_each": 0, "first_target_actual_cont_count_each": 0}
            receipt["passed"] = True
    except BaseException as error:
        receipt["passed"] = False
        receipt["error"] = str(error)
        receipt["error_type"] = type(error).__name__
        receipt["traceback"] = traceback.format_exc()
        if store is not None:
            receipt["members_read_before_failure"] = store.observed
    finally:
        if store is not None and store.z is not None:
            store.z.close()
        receipt["completed_utc"] = datetime.now(timezone.utc).isoformat()
        text = json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n"
        if output is not None:
            output.write(text)
            output.close()
        print(text, end="")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
