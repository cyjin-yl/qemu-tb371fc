#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled Kona-v2 VIDEOCC register, clock and independent migration checks.

The literal inventories below were read from the pinned primary driver and
binding, independently of the model's generated table. Virtual deadlines are
a digital reference contract, not analog PLL or video execution evidence.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import traceback

from test_camera_gdsc import CameraRig, core_diagnostics, unique_section, write_json
from test_camera_gdsc import GLOBAL_FIELDS as GDSC_GLOBAL_FIELDS, DOMAIN_FIELDS as GDSC_DOMAIN_FIELDS
from test_video_gdsc import video_payload
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-videocc", "/machine/videocc"
CC, RAM, VIDEO = 0x0abf0000, 0x80010000, "/machine/video-gdsc"
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
XO, PERIOD_UNIT = 19_200_000, 1_000_000_000 << 32
OUTCTRL, RESET_N, UPDATE, UPDATE_BYPASS = 1, 4, 1 << 22, 1 << 23
PCAL, ACK, LOCK, OFF = 1 << 27, 1 << 29, 1 << 31, 1 << 31
ROOT, BUSY, HW_CLK = 2, 1, 1 << 20
LOCK_NS, LATCH_NS, RCG_NS = 5000, 1000, 1000
PLLS = ((0x042c, "video_pll0", 0x25, 0x8000, 0x029a699c),
        (0x07d0, "video_pll1", 0x2b, 0xc000, 0x329a699c))
PLL_OFFSETS = (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 56, 64)
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "core_bi_pll_test_se", "chip_sleep_clk")
GDSC_OFFSETS = (0x0bf8, 0x0c98, 0x0d18, 0x0d98)
GDSC_NAMES = ("mvs0c", "mvs1c", "mvs0", "mvs1")
# Binding order. ARES bit 2 shares the branch word, not its local request bit 0.
RESETS = ((0x0e54, 1), (0x0d14, 1), (0x0c34, 4), (0x0bf4, 1),
          (0x0d94, 1), (0x0cd4, 4), (0x0c94, 1))
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_SHA256 = "33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5"
BINDING_SHA256 = "0f51753e9eb497399d3c2d9a362e6d3ee7baf4f789b6e6628a7911b00f89d774"

OUTPUTS = ("video_cc_ahb_clk", "video_cc_ahb_clk_src", "video_cc_mvs0_clk",
           "video_cc_mvs0_clk_src", "video_cc_mvs0_div_clk_src", "video_cc_mvs0c_clk",
           "video_cc_mvs0c_div2_div_clk_src", "video_cc_mvs1_clk", "video_cc_mvs1_clk_src",
           "video_cc_mvs1_div2_clk", "video_cc_mvs1_div_clk_src", "video_cc_mvs1c_clk",
           "video_cc_mvs1c_div2_div_clk_src", "video_cc_sleep_clk", "video_cc_sleep_clk_src",
           "video_cc_xo_clk", "video_cc_xo_clk_src", "video_pll0", "video_pll1")

# RCG order follows exported binding IDs. All five have hid_width=5, mnd_width=0.
RCGS = ((0x0bd4, "video_cc_ahb_clk_src", True,
         ((0, "bi_tcxo_ao"), (7, "core_bi_pll_test_se"))),
        (0x0b94, "video_cc_mvs0_clk_src", True,
         ((0, "bi_tcxo"), (1, "video_pll0"), (7, "core_bi_pll_test_se"))),
        (0x0bb4, "video_cc_mvs1_clk_src", True,
         ((0, "bi_tcxo"), (1, "video_pll1"), (7, "core_bi_pll_test_se"))),
        (0x0ef0, "video_cc_sleep_clk_src", False,
         ((0, "chip_sleep_clk"), (7, "core_bi_pll_test_se"))),
        (0x0ecc, "video_cc_xo_clk_src", False,
         ((0, "bi_tcxo_ao"), (7, "core_bi_pll_test_se"))))

BRANCHES = ((0x0e58, "video_cc_ahb_clk", "video_cc_ahb_clk_src", False),
            (0x0d34, "video_cc_mvs0_clk", "video_cc_mvs0_div_clk_src", True),
            (0x0c34, "video_cc_mvs0c_clk", "video_cc_mvs0c_div2_div_clk_src", False),
            (0x0db4, "video_cc_mvs1_clk", "video_cc_mvs1_div_clk_src", True),
            (0x0df4, "video_cc_mvs1_div2_clk", "video_cc_mvs1c_div2_div_clk_src", True),
            (0x0cd4, "video_cc_mvs1c_clk", "video_cc_mvs1c_div2_div_clk_src", True),
            (0x0f10, "video_cc_sleep_clk", "video_cc_sleep_clk_src", False),
            (0x0eec, "video_cc_xo_clk", "video_cc_xo_clk_src", False))

DIVIDERS = ((0x0d54, "video_cc_mvs0_div_clk_src", "video_cc_mvs0_clk_src"),
            (0x0c54, "video_cc_mvs0c_div2_div_clk_src", "video_cc_mvs0_clk_src"),
            (0x0dd4, "video_cc_mvs1_div_clk_src", "video_cc_mvs1_clk_src"),
            (0x0cf4, "video_cc_mvs1c_div2_div_clk_src", "video_cc_mvs1_clk_src"))

REGISTERS = tuple(sorted(set(
    [base+offset for base, *_ in PLLS for offset in PLL_OFFSETS] +
    [base+offset for base, *_ in RCGS for offset in (0, 4)] +
    [offset for offset, *_ in BRANCHES] + [offset for offset, *_ in DIVIDERS] +
    [offset for offset, _mask in RESETS])))
WINDOWS = []
for offset in REGISTERS:
    if WINDOWS and offset == WINDOWS[-1][0] + WINDOWS[-1][1]:
        WINDOWS[-1] = (WINDOWS[-1][0], WINDOWS[-1][1]+4)
    else:
        WINDOWS.append((offset, 4))
WINDOWS = tuple(WINDOWS)
RCG_INDEX = {row[1]: i for i, row in enumerate(RCGS)}
HW_RCG = tuple(i for i, row in enumerate(RCGS) if row[2])
VOTED = tuple(i for i, row in enumerate(BRANCHES) if row[3])
assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(RCGS), len(BRANCHES),
        len(DIVIDERS), len(RESETS), len(HW_RCG), len(VOTED)) == (55, 26, 19, 5, 8, 4, 7, 3, 4)
assert not set(REGISTERS) & set(GDSC_OFFSETS)

IDENTITY, VMSTATE_BYTES, EXPECTED_CHECKS = 0x56494432, 474, 41
GLOBAL_FIELDS = ("reference-profile", "reference-identity", "register-count", "clock-count",
                 "input-count", "pll-count", "rcg-count", "branch-count", "divider-count",
                 "reset-count", "remote-vote-count", "window-count", "mapped-bytes", "physical-base")
PLL_FIELDS = ("active-l", "active-alpha", "pending-l", "pending-alpha", "locked", "ack",
              "calibrated", "lock-pending", "latch-pending", "lock-deadline", "latch-deadline")
RCG_FIELDS = ("active-cfg", "pending-cfg", "pending", "deadline")


class VideoCcRig(CameraRig):
    """Reuse existing socket/RPMh transport without changing module globals."""
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", rpmh=False, video=False, legacy=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.camera_mmio_operations = self.videocc_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="videocc-"))
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory / "qtest.jsonl").open("w")
        self.qmplog = (self.directory / "qmp.jsonl").open("w")
        fields = [f for f in args.machine.split(",") if not f.startswith(
            ("rpmh-profile=", "rpmh-boot-configured=", "rpmh-cmd-db="))]
        fields += [f"rpmh-profile={'reference' if rpmh else 'off'}", "rpmh-boot-configured=off"]
        command = [args.qemu, "-machine", ",".join(fields), "-accel", accel,
                   "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
                   "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off",
                   "-global", f"qcom-video-gdsc.enabled-reference={'on' if video else 'off'}"]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if legacy:
            command += ["-global", "qcom-sm8250-camcc.enabled-reference=on",
                        "-global", "qcom-sm8250-dispcc.enabled-reference=on",
                        "-global", "qcom-sm8250-npucc.enabled-reference=on",
                        "-global", "qcom-sm8250-gpucc.enabled-reference=on"]
        if accel == "qtest":
            command += ["-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
                        "-qtest-log", "/dev/null"]
        if incoming:
            command += ["-incoming", "defer"]
        write_json(self.directory / "command.json", command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qmp = connect_unix(self.socket_directory / "m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self._log(self.qmplog, {"receive": greeting})
            assert "QMP" in greeting
            self.monitor("qmp_capabilities")
            if accel == "qtest":
                self.qtest = connect_unix(self.socket_directory / "t", self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile("rwb", buffering=0)
                self.command(f"irq_intercept_out {DEVICE} reset")
                if not incoming:
                    self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, command):
        fields = command.split()
        if fields and fields[0] in ("readb", "readw", "readl", "readq",
                                    "writeb", "writew", "writel", "writeq"):
            if CC <= int(fields[1], 0) < CC+0x10000:
                self.videocc_mmio_operations += 1
                self.device_mmio_operations += 1
        return super().command(command)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def reg(self, offset):
        return self.qom(f"reg.{offset:04x}")

    def read(self, offset):
        return self.raw_read(CC+offset)

    def write(self, offset, value):
        self.raw_write(CC+offset, value)

    def dread(self, offset, drv=2):
        return self.raw_read(RPMH_BASE+drv*0x10000+offset)

    def dwrite(self, offset, value, drv=2):
        self.raw_write(RPMH_BASE+drv*0x10000+offset, value)

    def period(self, name):
        return self.qom("qtest-clock-period", DEVICE+"/"+name)

    def hz(self, name):
        return self.qom("clk."+name+".hz")

    def expect_hz(self, name, hz):
        assert self.hz(name) == hz, (name, self.hz(name), hz)
        assert self.period(name) == (PERIOD_UNIT//hz if hz else 0), (name, hz)

    def fixture(self, name, hz):
        self.monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": hz})

    def divider(self, index, value):
        assert index in range(4)
        self.monitor("qom-set", {"path": DEVICE, "property": f"qtest-divider{index}-value", "value": value})

    def vote(self, index, value):
        assert index in range(4)
        self.command(f"set_irq_in {DEVICE} remote-vote {index} {int(bool(value))}")

    def pll(self, index, field):
        return self.qom(f"pll{index}-{field}")

    def rcg(self, index, field):
        return self.qom(f"rcg{index}-{field}")

    def pure_snapshot(self, *, include_clock_periods=True):
        snapshot = {"globals": {n: self.qom(n) for n in GLOBAL_FIELDS},
                    "registers": {f"{o:04x}": self.reg(o) for o in REGISTERS},
                    "plls": [{n: self.pll(i, n) for n in PLL_FIELDS} for i in range(2)],
                    "rcgs": [{n: self.rcg(i, n) for n in RCG_FIELDS} for i in range(5)],
                    "last-xo-hz": self.qom("last-xo-hz"),
                    "dividers": [self.qom(f"divider{i}-value") for i in range(4)],
                    "remote-votes": [self.qom(f"remote-vote{i}") for i in range(4)],
                    "output_hz": {n: self.hz(n) for n in OUTPUTS}}
        if include_clock_periods:
            snapshot["outputs"] = {n: self.period(n) for n in OUTPUTS}
            snapshot["inputs"] = {n: self.period(n) for n in INPUTS}
        return snapshot

    def video_snapshot(self):
        return {"enabled-reference": self.qom("enabled-reference", VIDEO),
                "globals": {n: self.qom(n, VIDEO) for n in GDSC_GLOBAL_FIELDS},
                "parent-known": self.qom("parent-known", VIDEO),
                "parent-on": self.qom("parent-on", VIDEO),
                "domains": [{n: self.qom(f"domain{i}."+n, VIDEO) for n in GDSC_DOMAIN_FIELDS}
                            for i in range(4)]}

    def reject_write(self, offset, value):
        before = self.pure_snapshot()
        count = self.log_path.read_text(errors="replace").count(TYPE+":")
        self.write(offset, value)
        assert self.pure_snapshot() == before, (hex(offset), hex(value))
        assert self.log_path.read_text(errors="replace").count(TYPE+":") == count+1

    def reset_lines(self, expected, require_seen=False):
        self.raw_read(RAM)
        assert [self.irq.get(i, False) for i in range(7)] == list(expected)
        if require_seen:
            assert set(range(7)) <= self.seen_irq

    def reset_device(self):
        self.events.clear()
        self.monitor("system_reset")
        self.wait_for(lambda: all(self.reg(p[0]+4) == 0 for p in PLLS), "VIDEOCC reset witnesses")
        self.monitor("query-status")
        assert any(e.get("event") == "RESET" for e in self.events)


def pll_rate(index, l=None, alpha=None):
    row = PLLS[index]
    return XO*(row[2] if l is None else l)+((XO*(row[3] if alpha is None else alpha)) >> 16)


def configure_pll(r, index, *, legacy=False):
    base, _name, l, alpha, config_u1 = PLLS[index]
    if legacy:
        assert index == 1
        l, alpha, config_u1 = 0x29, 0xfaaa, 0x029a699c
    mode, opmode = r.read(base), r.read(base+56)
    if mode & OUTCTRL and opmode & 1:
        return
    # The pinned helper omits zero USER, USER_U1 and TEST fields.
    for offset, value in ((4, l), (8, 0x44), (64, alpha), (24, 0x20485699),
                          (28, 0x2261), (32, config_u1), (16, 0x805)):
        r.write(base+offset, value)
    r.write(base, r.read(base) | UPDATE_BYPASS)
    r.write(base, r.read(base) & ~OUTCTRL)
    r.write(base+56, 0)
    r.write(base, r.read(base) | RESET_N)


def configure_all(r):
    # videocc-kona.c:645-650 configures both legacy recipes, then applies v2.
    configure_pll(r, 0)
    configure_pll(r, 1, legacy=True)
    configure_pll(r, 1)


def output_on(r, index):
    base = PLLS[index][0]
    r.write(base+12, r.read(base+12) | 7)
    r.write(base, r.read(base) | OUTCTRL)


def enable_all(r):
    for base, *_ in PLLS:
        r.write(base+56, 1)
    r.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        assert r.pll(i, "locked") is True
        output_on(r, i)
        r.expect_hz(row[1], pll_rate(i))


def rcg_cfg(index, selector=0, hid=1, mode=0):
    return (HW_CLK if RCGS[index][2] else 0) | selector << 8 | hid | mode << 12


def configure_rcg(r, index, selector=0, hid=1, *, wait=True, root=True):
    offset = RCGS[index][0]
    r.write(offset+4, rcg_cfg(index, selector, hid))
    r.write(offset, (ROOT if root else 0) | BUSY)
    if wait:
        r.step(RCG_NS)
        assert not r.read(offset) & BUSY


def divided(hz, hid):
    return hz*2//(hid+1) if hid else hz


def source_rate(name):
    if name in INPUTS[:2]:
        return XO
    for i, row in enumerate(PLLS):
        if row[1] == name:
            return pll_rate(i)
    return 0


def expect_root(r, index, hz):
    offset, name, *_ = RCGS[index]
    r.expect_hz(name, hz)
    assert bool(r.read(offset) & OFF) == (hz == 0)


def run_defaults(r):
    configure_all(r)
    enable_all(r)
    for i in range(5):
        configure_rcg(r, i)


def cold_reference_has_exact_inventory_without_invented_carriers(r):
    values = (1, IDENTITY, 55, 19, 4, 2, 5, 8, 4, 7, 4, 26, 220, CC)
    assert {n: r.qom(n) for n in GLOBAL_FIELDS} == dict(zip(GLOBAL_FIELDS, values))
    status = {x[0] for x in RCGS+BRANCHES}
    for offset in REGISTERS:
        assert r.reg(offset) == 0
        assert r.read(offset) == (OFF if offset in status else 0), hex(offset)
    for name in OUTPUTS:
        r.expect_hz(name, 0)
    assert [r.period(n) for n in INPUTS] == [PERIOD_UNIT//XO]*2+[0]*2
    assert [r.qom(f"remote-vote{i}") for i in range(4)] == [False]*4
    r.reset_lines([False]*7)


def literal_probe_applies_legacy_then_v2_without_clearing_conditional_zeros(r):
    base = PLLS[1][0]
    r.write(base+12, 7)
    r.write(base+20, 0x1234)
    r.write(base+36, 0x5678)
    configure_pll(r, 0)
    configure_pll(r, 1, legacy=True)
    assert (r.reg(base+4), r.reg(base+64), r.reg(base+32)) == (0x29, 0xfaaa, 0x029a699c)
    assert not r.pll(1, "locked") and r.reg(base+56) == 0
    configure_pll(r, 1)
    assert (r.reg(base+4), r.reg(base+64), r.reg(base+32)) == (0x2b, 0xc000, 0x329a699c)
    assert (r.reg(base+12), r.reg(base+20), r.reg(base+36)) == (7, 0x1234, 0x5678)
    assert r.reg(base) == UPDATE_BYPASS | RESET_N
    assert pll_rate(0) == 720_000_000 and pll_rate(1) == 840_000_000
    r.write(base+20, 0)
    r.write(base+36, 0)

    # Negative profile challenge, not an extra native probe transaction.
    # Remove the zero-field witnesses above: a bad common recipe field or
    # missing carrier must not mask the legacy CONFIG_U1 qualification check.
    r.write(base, 0)
    configure_pll(r, 1, legacy=True)
    assert (r.reg(base+4), r.reg(base+64), r.reg(base+32)) == (0x29, 0xfaaa, 0x029a699c)
    assert r.reg(base+56) == 0 and r.reg(base) == UPDATE_BYPASS | RESET_N
    assert tuple(r.reg(base+o) for o in (8, 16, 20, 24, 28, 36, 40, 44)) == (
        0x44, 0x805, 0, 0x20485699, 0x2261, 0, 0, 0)
    assert 249_600_000 <= pll_rate(1, 0x29, 0xfaaa) <= 2_000_000_000
    assert r.period("bi_tcxo") == PERIOD_UNIT//XO
    output_on(r, 1)
    r.write(base+56, 1)
    for request_update in (False, True):
        if request_update:
            r.write(base, r.read(base) | UPDATE)
        r.step(20_000)
        assert r.reg(base+12) == 7 and r.reg(base) & OUTCTRL
        assert r.reg(base+56) == 1
        assert not any(r.pll(1, f) for f in (
            "locked", "ack", "calibrated", "lock-pending", "latch-pending"))
        assert r.pll(1, "lock-deadline") == r.pll(1, "latch-deadline") == -1
        assert not r.read(base) & (LOCK | ACK | PCAL)
        r.expect_hz("video_pll1", 0)
    # Return the challenged bank to standby before replaying the actual v2
    # helper; its OUTCTRL/RUN early return must not hide the required rewrite.
    r.write(base, r.read(base) & ~(UPDATE | OUTCTRL))
    r.write(base+56, 0)
    configure_pll(r, 1)
    assert (r.reg(base+4), r.reg(base+64), r.reg(base+32)) == (0x2b, 0xc000, 0x329a699c)
    enable_all(r)
    assert r.pll(1, "locked") and r.pll(1, "calibrated") and not r.pll(1, "ack")
    r.expect_hz("video_pll1", 840_000_000)
    before = r.pure_snapshot()
    configure_all(r)  # Running OUTCTRL/RUN banks are skipped by the vendor helper.
    assert r.pure_snapshot() == before
    return {"probe_configuration_order": ["pll0", "pll1-legacy", "pll1-v2"],
            "v2_pll1_config_ctl_u1": 0x329a699c, "zero_recipe_fields_are_omitted": True,
            "legacy_pll1_writes_and_standby_accepted": True,
            "legacy_pll1_lock_accepted": False, "legacy_pll1_update_ack_accepted": False}


def nonzero_pll_offsets_and_variant_recipes_remain_independent(r):
    configure_all(r)
    for i, (base, _name, l, alpha, recipe) in enumerate(PLLS):
        assert (r.reg(base+4), r.reg(base+8), r.reg(base+64), r.reg(base+32)) == (l, 0x44, alpha, recipe)
        assert r.reg(base+56) == 0 and not r.pll(i, "locked")
    before = {o: r.reg(o) for o in REGISTERS if 0x7d0 <= o <= 0x810}
    r.write(0x430, 0x26)
    r.write(0x46c, 0x2345)
    assert {o: r.reg(o) for o in before} == before
    r.write(0x434, 0x45)
    r.write(0x464, 1)
    r.step(20_000)
    assert not r.pll(0, "locked") and not r.pll(1, "locked")
    for name in ("video_pll0", "video_pll1"):
        r.expect_hz(name, 0)


def lock_deadlines_require_both_user_and_global_output_gates(r):
    configure_all(r)
    r.write(PLLS[0][0]+56, 1)
    first = r.last_clock
    r.step(700)
    r.write(PLLS[1][0]+56, 1)
    assert r.pll(0, "lock-deadline") == first+5000
    assert r.pll(1, "lock-deadline") == first+5700
    r.step(4299)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.step(1)
    assert r.pll(0, "locked") and not r.pll(1, "locked")
    r.step(700)
    for i, (base, name, *_rest) in enumerate(PLLS):
        assert r.pll(i, "locked") and r.pll(i, "calibrated")
        r.expect_hz(name, 0)
        r.write(base, r.read(base) | OUTCTRL)
        r.expect_hz(name, 0)
        r.write(base+12, 7)
        r.expect_hz(name, pll_rate(i))
        r.write(base+12, 0)
        r.expect_hz(name, 0)
        r.write(base+12, 7)
        r.write(base, r.read(base) & ~OUTCTRL)
        r.expect_hz(name, 0)


def invalid_recipes_and_quantized_vco_endpoints_cannot_invent_lock(r):
    configure_all(r)
    base = PLLS[0][0]
    upper_factor = (2_000_000_000*(1 << 16)+XO-1)//XO
    for l, alpha, cal, config in ((0, 0, 0x44, 0x029a699c),
            (12, 0xffff, 0x44, 0x029a699c), (105, 0, 0x44, 0x029a699c),
            ((upper_factor+1) >> 16, (upper_factor+1) & 0xffff, 0x44, 0x029a699c),
            (0x25, 0x8000, 0x45, 0x029a699c), (0x25, 0x8000, 0x44, 0x029a699d)):
        r.write(base+56, 0)
        for delta, value in ((4, l), (64, alpha), (8, cal), (32, config)):
            r.write(base+delta, value)
        r.write(base+56, 1)
        r.step(20_000)
        assert not r.pll(0, "locked") and not r.pll(0, "lock-pending")
        r.expect_hz("video_pll0", 0)
    for factor in (13 << 16, upper_factor):
        r.write(base+56, 0)
        for delta, value in ((4, factor >> 16), (64, factor & 0xffff),
                             (8, 0x44), (32, 0x029a699c)):
            r.write(base+delta, value)
        r.write(base+56, 1)
        r.step(5000)
        output_on(r, 0)
        r.expect_hz("video_pll0", pll_rate(0, factor >> 16, factor & 0xffff))
    assert not r.pll(1, "locked")
    # Restore nominal source recipes before testing live qualification. The
    # running driver's early-return check would otherwise keep the VCO vector.
    for base, *_rest in PLLS:
        r.write(base, r.read(base) & ~(OUTCTRL | UPDATE))
        r.write(base+56, 0)
    configure_all(r)
    enable_all(r)
    for index, (base, name, *_rest) in enumerate(PLLS):
        sibling = 1-index
        for delta in (8, 16, 32, 36):  # CAL_L, USER_U, CONFIG_U1, TEST.
            original = r.reg(base+delta)
            r.write(base+delta, original ^ 1)
            assert not r.pll(index, "locked") and r.pll(index, "lock-deadline") == -1
            r.expect_hz(name, 0)
            assert r.pll(sibling, "locked")
            r.expect_hz(PLLS[sibling][1], pll_rate(sibling))
            r.step(LOCK_NS)
            assert not r.pll(index, "locked")
            r.expect_hz(name, 0)
            r.expect_hz(PLLS[sibling][1], pll_rate(sibling))
            r.write(base+delta, original)
            assert r.pll(index, "lock-deadline") == r.last_clock+LOCK_NS
            r.step(LOCK_NS-1)
            assert not r.pll(index, "locked")
            r.expect_hz(name, 0)
            r.expect_hz(PLLS[sibling][1], pll_rate(sibling))
            r.step(1)
            assert r.pll(index, "locked")
            r.expect_hz(name, pll_rate(index))
            r.expect_hz(PLLS[sibling][1], pll_rate(sibling))


def both_lucid_updates_capture_shadow_once_then_ack_and_relock(r):
    configure_all(r)
    enable_all(r)
    captured = ((0x26, 0x1234), (0x2c, 0x5678))
    for i, (base, *_rest) in enumerate(PLLS):
        r.write(base+4, captured[i][0])
        r.write(base+64, captured[i][1])
        r.write(base, r.read(base) | UPDATE)
        r.write(base+4, 0x30+i)
        r.write(base+64, 0xabcd)
        r.write(base, r.read(base) | UPDATE)
        assert (r.pll(i, "pending-l"), r.pll(i, "pending-alpha")) == captured[i]
    r.step(999)
    for i, row in enumerate(PLLS):
        assert not r.pll(i, "ack")
        r.expect_hz(row[1], pll_rate(i))
    r.step(1)
    for i, row in enumerate(PLLS):
        assert r.pll(i, "ack") and not r.pll(i, "locked")
        assert (r.pll(i, "active-l"), r.pll(i, "active-alpha")) == captured[i]
        r.expect_hz(row[1], 0)
        r.write(row[0], r.read(row[0]) & ~UPDATE)
        assert not r.pll(i, "ack")
    r.step(4999)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.step(1)
    for i, row in enumerate(PLLS):
        r.expect_hz(row[1], pll_rate(i, *captured[i]))


def update_cancellation_standby_and_reset_keep_the_other_bank_independent(r):
    configure_all(r)
    enable_all(r)
    base = PLLS[0][0]
    for canceled_bit in (UPDATE, UPDATE_BYPASS):
        r.write(base, r.read(base) & ~UPDATE | UPDATE_BYPASS)
        r.write(base+4, 0x26)
        r.write(base, r.read(base) | UPDATE)
        r.step(500)
        r.write(base, r.read(base) & ~canceled_bit)
        assert not r.pll(0, "ack") and r.pll(0, "latch-deadline") == -1
        r.step(10_000)
        r.expect_hz("video_pll0", pll_rate(0))
        r.expect_hz("video_pll1", pll_rate(1))
    r.write(base+56, 0)
    r.expect_hz("video_pll0", 0)
    assert r.pll(0, "calibrated")
    r.write(base, r.read(base) & ~RESET_N)
    assert not any(r.pll(0, f) for f in ("locked", "ack", "calibrated", "active-l", "pending-l"))
    assert r.pll(0, "lock-deadline") == r.pll(0, "latch-deadline") == -1
    r.expect_hz("video_pll1", pll_rate(1))


def clock_jump_completes_captured_latches_without_device_mmio(r):
    configure_all(r)
    enable_all(r)
    for base, _name, l, _alpha, _recipe in PLLS:
        r.write(base+4, l+1)
        r.write(base, r.read(base) | UPDATE)
    count = r.device_mmio_operations
    r.set_clock(r.last_clock+20_000)
    for i, row in enumerate(PLLS):
        assert r.pll(i, "ack") and r.pll(i, "locked")
        assert r.pll(i, "lock-deadline") == r.pll(i, "latch-deadline") == -1
        r.expect_hz(row[1], pll_rate(i, row[2]+1, row[3]))
    assert r.device_mmio_operations == count


def five_rcgs_capture_configuration_once_at_original_deadlines(r):
    r.fixture("core_bi_pll_test_se", 99_000_001)
    start = r.last_clock
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, 7, 4+i, wait=False)
        r.write(offset+4, rcg_cfg(i, 0, 1))
        r.write(offset, ROOT | BUSY)
        assert r.rcg(i, "pending-cfg") == rcg_cfg(i, 7, 4+i)
        assert r.rcg(i, "deadline") == start+1000
    r.step(999)
    assert all(r.rcg(i, "pending") for i in range(5))
    r.step(1)
    for i, (offset, name, *_rest) in enumerate(RCGS):
        assert r.rcg(i, "active-cfg") == rcg_cfg(i, 7, 4+i)
        assert r.reg(offset+4) == rcg_cfg(i, 0, 1)
        assert not r.read(offset) & BUSY and not r.rcg(i, "pending")
        r.expect_hz(name, divided(99_000_001, 4+i))
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, 0, 7, wait=False)
        assert r.rcg(i, "pending")
        r.write(offset, ROOT)  # Withdraw UPDATE before its captured deadline.
        assert not r.rcg(i, "pending") and r.rcg(i, "deadline") == -1
    r.step(RCG_NS)
    for i, (_offset, name, *_rest) in enumerate(RCGS):
        assert r.rcg(i, "active-cfg") == rcg_cfg(i, 7, 4+i)
        assert not r.rcg(i, "pending")
        r.expect_hz(name, divided(99_000_001, 4+i))


def only_three_rcgs_accept_declared_hardware_control_bit(r):
    for i, (offset, _name, hw, _parents) in enumerate(RCGS):
        if hw:
            r.write(offset+4, HW_CLK | 1)
            assert r.reg(offset+4) == HW_CLK | 1
            r.write(offset+4, 1)
            assert r.reg(offset+4) == 1
        else:
            r.reject_write(offset+4, HW_CLK | 1)
        r.reject_write(offset+4, 1 << 21)
    return {"hw_control_rcg_indices": [0, 1, 2], "sleep_and_xo_hw_control_rejected": True}


def invalid_mux_and_fractional_modes_remain_busy_without_ack(r):
    for i, (offset, _name, _hw, parents) in enumerate(RCGS):
        valid = {p[0] for p in parents}
        vectors = [rcg_cfg(i, sel, 1) for sel in range(8) if sel not in valid]
        vectors += [rcg_cfg(i, 0, 1, mode) for mode in (1, 2, 3)]
        for cfg in vectors:
            before = r.rcg(i, "active-cfg")
            r.write(offset, 0)
            r.write(offset+4, cfg)
            r.write(offset, ROOT | BUSY)
            r.step(5000)
            assert r.read(offset) & BUSY
            assert not r.rcg(i, "pending") and r.rcg(i, "deadline") == -1
            assert r.rcg(i, "active-cfg") == before
        r.write(offset, 0)


def literal_v2_mvs_frequency_tables_route_the_correct_pll(r):
    configure_all(r)
    enable_all(r)
    tables = ((1, 0, (720_000_000, 1_014_000_000, 1_098_000_000, 1_332_000_000)),
              (2, 1, (840_000_000, 1_098_000_000, 1_332_000_000)))
    for rcg, pll, rates in tables:
        configure_rcg(r, rcg, 1)
        for requested in rates:
            factor = (requested*(1 << 16)+XO-1)//XO
            l, alpha = factor >> 16, factor & 0xffff
            base = PLLS[pll][0]
            r.write(base+4, l)
            r.write(base+64, alpha)
            r.write(base, r.read(base) | UPDATE)
            r.step(1000)
            assert r.pll(pll, "ack")
            r.write(base, r.read(base) & ~UPDATE)
            r.step(5000)
            actual = pll_rate(pll, l, alpha)
            assert requested <= actual <= requested+500
            expect_root(r, rcg, actual)
    return {"literal_v2_mvs_table_entries": 7, "legacy_806mhz_not_substituted_for_v2_840mhz": True}


def every_declared_mux_uses_its_named_parent(r):
    configure_all(r)
    enable_all(r)
    for i, (_offset, _name, _hw, parents) in enumerate(RCGS):
        for selector, parent in parents:
            configure_rcg(r, i, selector)
            expect_root(r, i, source_rate(parent))


def every_hid_value_covers_bypass_integer_and_half_division(r):
    r.fixture("core_bi_pll_test_se", 100_000_003)
    for i in range(5):
        for hid in range(32):
            configure_rcg(r, i, 7, hid)
            expect_root(r, i, divided(100_000_003, hid))


def unknown_sleep_and_test_inputs_require_explicit_carriers(r):
    for i in range(5):
        configure_rcg(r, i, 7)
        expect_root(r, i, 0)
    r.fixture("core_bi_pll_test_se", 77_000_003)
    for i in range(5):
        expect_root(r, i, 77_000_003)
    r.fixture("core_bi_pll_test_se", 0)
    configure_rcg(r, 3, 0)
    r.write(0xf10, 1)
    r.expect_hz("video_cc_sleep_clk", 0)
    r.fixture("chip_sleep_clk", 32_000)
    r.expect_hz("video_cc_sleep_clk", 32_000)
    assert r.period("core_bi_pll_test_se") == 0
    r.fixture("chip_sleep_clk", 0)
    r.expect_hz("video_cc_sleep_clk", 0)


def critical_xo_uses_declared_ao_parent_without_invented_safe_config(r):
    # This source has no enable_safe_config or FORCE_ENABLE_RCG; its enable
    # callback returns without ROOT/config/UPDATE writes (clk-rcg2.c:656-660).
    assert r.reg(0xecc) == r.reg(0xed0) == 0
    r.write(0xeec, 1)
    assert r.reg(0xecc) == r.reg(0xed0) == 0
    r.expect_hz("video_cc_xo_clk_src", XO)
    r.expect_hz("video_cc_xo_clk", XO)
    assert not r.read(0xecc) & OFF
    assert r.period("bi_tcxo_ao") == r.period("bi_tcxo") == PERIOD_UNIT//XO
    r.write(0xeec, 0)
    r.expect_hz("video_cc_xo_clk", 0)
    r.expect_hz("video_cc_xo_clk_src", 0)
    return {"critical_flag_treated_as_software_policy": True,
            "xo_safe_config_or_force_enable_invented": False}


def four_readonly_dividers_round_up_from_all_two_bit_values(r):
    r.fixture("core_bi_pll_test_se", 101_000_003)
    for index in (1, 2):
        configure_rcg(r, index, 7, 2, root=False)
    parent = divided(101_000_003, 2)
    for i, (offset, name, source) in enumerate(DIVIDERS):
        branch = next(row for row in BRANCHES if row[2] == name)
        r.expect_hz(source, 0)
        for value in range(4):
            r.divider(i, value)
            assert r.qom(f"divider{i}-value") == value
            assert r.read(offset) == value
            r.reject_write(offset, value)
            r.write(branch[0], 1)
            expected = (parent+value)//(value+1)
            r.expect_hz(source, parent)
            r.expect_hz(name, expected)
            r.expect_hz(branch[1], expected)
            r.write(branch[0], 0)
            r.expect_hz(source, 0)
            r.expect_hz(name, 0)
        r.divider(i, 0)
    assert parent % 4 != 0


def readonly_divider_mmio_rejects_even_unchanged_zero_writes(r):
    for i, (offset, *_rest) in enumerate(DIVIDERS):
        r.reject_write(offset, 0)
        r.divider(i, i)
        r.reject_write(offset, i)
        r.reject_write(offset, 3-i)
        assert r.reg(offset) == i


def shared_mvs1c_divider_fans_out_without_fixed_divide_two_assumption(r):
    r.fixture("core_bi_pll_test_se", 101_000_003)
    configure_rcg(r, 2, 7, 1, root=False)
    r.divider(3, 2)
    expected = (101_000_003+2)//3
    r.write(0xdf4, 1)
    r.expect_hz("video_cc_mvs1c_div2_div_clk_src", expected)
    r.expect_hz("video_cc_mvs1_div2_clk", expected)
    r.expect_hz("video_cc_mvs1c_clk", 0)
    r.write(0xcd4, 1)
    r.write(0xdf4, 0)
    r.expect_hz("video_cc_mvs1_div2_clk", 0)
    r.expect_hz("video_cc_mvs1c_clk", expected)
    r.write(0xcd4, 0)
    r.expect_hz("video_cc_mvs1c_div2_div_clk_src", 0)
    expect_root(r, 2, 0)


def all_eight_branches_have_independent_local_requests(r):
    r.fixture("chip_sleep_clk", 32_000)
    for i in range(5):
        configure_rcg(r, i, root=False)
    for offset, name, _parent, _voted in BRANCHES:
        r.write(offset, 1)
        expected = 32_000 if name == "video_cc_sleep_clk" else XO
        r.expect_hz(name, expected)
        assert r.read(offset) == 1
        for other in BRANCHES:
            if other[1] != name:
                r.expect_hz(other[1], 0)
        r.write(offset, 0)
        r.expect_hz(name, 0)
        assert r.read(offset) == OFF


def four_remote_votes_or_local_requests_without_supplying_unknown_parents(r):
    for i in (1, 2):
        configure_rcg(r, i, 7, root=False)
    for vote, branch in enumerate(VOTED):
        offset, name, _parent, _voted = BRANCHES[branch]
        r.vote(vote, True)
        assert r.qom(f"remote-vote{vote}") is True and r.reg(offset) == 0
        r.expect_hz(name, 0)
    r.fixture("core_bi_pll_test_se", 79_000_003)
    for vote, branch in enumerate(VOTED):
        offset, name, _parent, _voted = BRANCHES[branch]
        r.expect_hz(name, 79_000_003)
        assert r.read(offset) == 0
        r.write(offset, 1)
        r.vote(vote, False)
        r.expect_hz(name, 79_000_003)
        r.write(offset, 0)
        r.expect_hz(name, 0)


def remote_vote_indices_are_distinct_and_do_not_become_local_requests(r):
    for vote, branch in enumerate(VOTED):
        r.vote(vote, True)
        assert [r.qom(f"remote-vote{i}") for i in range(4)] == [i == vote for i in range(4)]
        for index, (offset, name, *_rest) in enumerate(BRANCHES):
            assert r.reg(offset) == 0
            r.expect_hz(name, XO if index == branch else 0)
        r.reset_lines([False]*7)
        r.vote(vote, False)


def seven_reset_bindings_preserve_plls_and_independent_video_domains(r):
    run_defaults(r)
    for offset, *_rest in BRANCHES:
        r.write(offset, 1)
    rates = {name: r.hz(name) for name in OUTPUTS}
    video = r.video_snapshot()
    for index, (offset, mask) in enumerate(RESETS):
        previous = r.reg(offset)
        r.write(offset, r.read(offset) | mask)
        assert r.reg(offset) == previous | mask
        r.reset_lines([i == index for i in range(7)])
        assert {name: r.hz(name) for name in OUTPUTS} == rates
        assert r.video_snapshot() == video
        r.write(offset, r.read(offset) & ~mask)
        assert r.reg(offset) == previous & ~mask
        assert {name: r.hz(name) for name in OUTPUTS} == rates
        assert r.video_snapshot() == video
    r.reset_lines([False]*7, require_seen=True)


def shared_ares_words_keep_reset_bit_two_separate_from_branch_bit_zero(r):
    for offset, name, reset in ((0xc34, "video_cc_mvs0c_clk", 2),
                                (0xcd4, "video_cc_mvs1c_clk", 5)):
        r.write(offset, 4)
        r.reset_lines([i == reset for i in range(7)])
        r.expect_hz(name, 0)
        assert r.read(offset) == OFF | 4
        r.write(offset, r.read(offset) | 1)
        assert r.reg(offset) == 5 and r.read(offset) == 5
        r.expect_hz(name, XO)
        r.reset_lines([i == reset for i in range(7)])
        r.reject_write(offset, 7)
        r.write(offset, r.read(offset) & ~4)
        r.reset_lines([False]*7)
        r.expect_hz(name, XO)
        r.write(offset, 0)
    return {"shared_branch_ares_words": [0xc34, 0xcd4], "reset_bit_is_not_clock_enable": True}


def read_only_status_cannot_forge_or_erase_actual_feedback(r):
    configure_all(r)
    for base, *_rest in PLLS:
        r.write(base, r.reg(base) | LOCK | ACK | PCAL)
        assert not r.read(base) & (LOCK | ACK | PCAL)
    for offset, *_rest in RCGS+BRANCHES:
        r.write(offset, OFF)
        assert r.read(offset) == OFF and r.reg(offset) == 0
    enable_all(r)
    for i, (base, name, *_rest) in enumerate(PLLS):
        r.write(base, r.read(base) & ~(LOCK | ACK | PCAL))
        assert r.read(base) & (LOCK | PCAL) == LOCK | PCAL
        r.expect_hz(name, pll_rate(i))


def reserved_bits_narrow_accesses_and_unowned_holes_reject_atomically(r):
    for offset, value in ((0x430, 0x10000), (0x434, 0x10000), (0x46c, 0x10000),
                          (0x464, 2), (0x7dc, 8), (0xbd8, 1 << 21),
                          (0xe54, 2), (0xd34, 2), (0xc34, 2), (0xcd4, 8)):
        r.reject_write(offset, value)
    for op in ("read", "write"):
        for width in ("b", "w"):
            count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
            if op == "read":
                r.read_absolute(CC+0x42c, width)
            else:
                r.write_absolute(CC+0x42c, 0xffffffff, width)
            new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
            assert len(new) == 1 and new[0][0] == op and "invalid size" in new[0][-1]
        before = (r.reg(0x42c), r.reg(0x430))
        count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
        if op == "read":
            r.read_absolute(CC+0x42d)
        else:
            r.write_absolute(CC+0x42d, 0xffffffff)
        new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
        assert new and all(item[0] == op and "invalid size" in item[-1] for item in new)
        assert (r.reg(0x42c), r.reg(0x430)) == before
    holes = (0, 0x428, 0x45c, 0x460, 0x468, 0x470, 0x800, 0x804, 0x80c, 0x814,
             0xbf0, 0xbfc, 0xc9c, 0xd1c, 0xd9c, 0xf14, 0xf4c, 0xfffc)
    assert not set(holes) & (set(REGISTERS) | set(GDSC_OFFSETS))
    count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    expected = []
    for offset in holes:
        r.read(offset)
        r.write(offset, 0xffffffff)
        expected.extend((op, CC+offset, 4, "(null)", "rejected") for op in ("read", "write"))
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[count:] == expected


def absent_mnd_words_and_observers_do_not_create_fractional_hardware(r):
    count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    expected = []
    for index, (offset, *_rest) in enumerate(RCGS):
        for delta in (8, 12, 16):
            assert offset+delta not in REGISTERS and offset+delta not in GDSC_OFFSETS
            r.read(offset+delta)
            r.write(offset+delta, 0xff)
            expected.extend((op, CC+offset+delta, 4, "(null)", "rejected") for op in ("read", "write"))
        for stage in ("active", "pending"):
            for field in ("m", "n", "d"):
                reply = r.raw_monitor("qom-get", {"path": DEVICE, "property": f"rcg{index}-{stage}-{field}"})
                assert "error" in reply and "not found" in reply["error"]["desc"]
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[count:] == expected


def system_reset_cancels_work_and_retains_external_inputs_and_remote_votes(r):
    configure_all(r)
    enable_all(r)
    for i, name in enumerate(INPUTS[2:]):
        r.fixture(name, 20_000_001+i)
    for base, _name, l, _alpha, _recipe in PLLS:
        r.write(base+4, l+1)
        r.write(base, r.read(base) | UPDATE)
    for i in range(5):
        configure_rcg(r, i, wait=False)
    for offset, mask in RESETS:
        r.write(offset, mask)
    for i in range(4):
        r.divider(i, 3)
        r.vote(i, True)
    inputs = {n: r.period(n) for n in INPUTS}
    r.reset_device()
    assert all(r.reg(o) == 0 for o in REGISTERS)
    for i in range(2):
        assert not any(r.pll(i, f) for f in ("locked", "ack", "calibrated"))
        assert r.pll(i, "lock-deadline") == r.pll(i, "latch-deadline") == -1
    assert all(not r.rcg(i, "pending") and r.rcg(i, "active-cfg") == 0 for i in range(5))
    assert {n: r.period(n) for n in INPUTS} == inputs
    assert [r.qom(f"remote-vote{i}") for i in range(4)] == [True]*4
    r.reset_lines([False]*7)
    r.step(20_000)
    for index in VOTED:
        r.expect_hz(BRANCHES[index][1], XO)
    for i in range(4):
        r.vote(i, False)
    for name in OUTPUTS:
        r.expect_hz(name, 0)


def pure_observers_and_fixture_type_and_quantization_limits_are_protected(r):
    before = r.pure_snapshot()
    for prop, value in [(n, r.qom(n)) for n in GLOBAL_FIELDS] + [
            ("reg.042c", 0), ("pll0-active-l", 0), ("rcg1-active-cfg", 0),
            ("divider0-value", 0), ("remote-vote0", False)]:
        reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": prop, "value": value})
        assert "error" in reply and "not writable" in reply["error"]["desc"]
    for name in INPUTS:
        for value in (-1, 1 << 32, "19200000", True):
            reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": value})
            assert "error" in reply
    for name in INPUTS[:2]:
        reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": 1})
        assert "error" in reply and "connected" in reply["error"]["desc"]
    for name in INPUTS[2:]:
        for value in range(4_294_967_292, 4_294_967_296):
            reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": value})
            assert "error" in reply and r.period(name) == 0
        r.fixture(name, 4_294_967_291)
        assert r.period(name) == 1_000_000_001
        assert r.qom("qtest-"+name+"-hz") == 4_294_967_291
        r.fixture(name, 0)
    for i in range(4):
        for value in (-1, 4, "1", True):
            reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": f"qtest-divider{i}-value", "value": value})
            assert "error" in reply
    assert r.pure_snapshot() == before


def real_rpmh_xo_vote_changes_both_named_carriers_without_inventing_sleep(r):
    assert r.has_rpmh and r.period("bi_tcxo") == r.period("bi_tcxo_ao") == 0
    configure_all(r)
    r.step(20_000)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.run_one("xo.lvl", 1)
    assert r.period("bi_tcxo") == r.period("bi_tcxo_ao") == PERIOD_UNIT//XO
    enable_all(r)
    r.write(0xeec, 1)
    r.expect_hz("video_cc_xo_clk", XO)
    r.run_one("xo.lvl", 0)
    assert r.period("bi_tcxo") == r.period("bi_tcxo_ao") == 0
    assert not any(r.pll(i, "locked") for i in range(2))
    r.expect_hz("video_cc_xo_clk", 0)
    r.run_one("xo.lvl", 1)
    for base, *_rest in PLLS:
        r.write(base+56, 0)
        r.write(base+56, 1)
    r.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        r.expect_hz(row[1], pll_rate(i))
    r.expect_hz("video_cc_xo_clk", XO)
    assert all(r.period(n) == 0 for n in INPUTS[2:])
    return {"resource": "xo.lvl", "connected_names": list(INPUTS[:2]), "sleep_carrier_invented": False}


def cold_ahb_safe_enable_sequence_and_branch_demand_are_source_bound(r):
    # Unlike the separate XO RCG, AHB declares enable_safe_config and HW control.
    cmd, cfg, branch = 0xbd4, 0xbd8, 0xe58
    assert r.reg(cmd) == r.reg(cfg) == r.reg(branch) == 0

    def update_bits(offset, mask, value):
        old = r.read(offset)
        new = (old & ~mask) | (value & mask)
        if new != old:
            r.write(offset, new)

    update_bits(cmd, ROOT, ROOT)
    expect_root(r, 0, XO)
    update_bits(cfg, 0x10371f, HW_CLK | 1)
    update_bits(cmd, BUSY, BUSY)
    r.step(999)
    assert r.rcg(0, "pending")
    r.step(1)
    assert not r.read(cmd) & BUSY and r.rcg(0, "active-cfg") == HW_CLK | 1
    update_bits(cmd, ROOT, 0)
    expect_root(r, 0, 0)
    update_bits(branch, 1, 1)
    assert r.reg(cmd) == 0 and r.read(branch) == 1
    r.expect_hz("video_cc_ahb_clk", XO)
    expect_root(r, 0, XO)
    update_bits(branch, 1, 0)
    expect_root(r, 0, 0)
    return {"literal_safe_enable_callback_order": True,
            "ccf_orphan_registration_sequence_claimed": False}


ORDINARY = [
    (cold_reference_has_exact_inventory_without_invented_carriers, {}),
    (literal_probe_applies_legacy_then_v2_without_clearing_conditional_zeros, {}),
    (nonzero_pll_offsets_and_variant_recipes_remain_independent, {}),
    (lock_deadlines_require_both_user_and_global_output_gates, {}),
    (invalid_recipes_and_quantized_vco_endpoints_cannot_invent_lock, {}),
    (both_lucid_updates_capture_shadow_once_then_ack_and_relock, {}),
    (update_cancellation_standby_and_reset_keep_the_other_bank_independent, {}),
    (clock_jump_completes_captured_latches_without_device_mmio, {}),
    (five_rcgs_capture_configuration_once_at_original_deadlines, {}),
    (only_three_rcgs_accept_declared_hardware_control_bit, {}),
    (invalid_mux_and_fractional_modes_remain_busy_without_ack, {}),
    (literal_v2_mvs_frequency_tables_route_the_correct_pll, {}),
    (every_declared_mux_uses_its_named_parent, {}),
    (every_hid_value_covers_bypass_integer_and_half_division, {}),
    (unknown_sleep_and_test_inputs_require_explicit_carriers, {}),
    (critical_xo_uses_declared_ao_parent_without_invented_safe_config, {}),
    (four_readonly_dividers_round_up_from_all_two_bit_values, {}),
    (readonly_divider_mmio_rejects_even_unchanged_zero_writes, {}),
    (shared_mvs1c_divider_fans_out_without_fixed_divide_two_assumption, {}),
    (all_eight_branches_have_independent_local_requests, {}),
    (four_remote_votes_or_local_requests_without_supplying_unknown_parents, {}),
    (remote_vote_indices_are_distinct_and_do_not_become_local_requests, {}),
    (seven_reset_bindings_preserve_plls_and_independent_video_domains, {"video": True}),
    (shared_ares_words_keep_reset_bit_two_separate_from_branch_bit_zero, {}),
    (read_only_status_cannot_forge_or_erase_actual_feedback, {}),
    (reserved_bits_narrow_accesses_and_unowned_holes_reject_atomically, {}),
    (absent_mnd_words_and_observers_do_not_create_fractional_hardware, {}),
    (system_reset_cancels_work_and_retains_external_inputs_and_remote_votes, {}),
    (pure_observers_and_fixture_type_and_quantization_limits_are_protected, {}),
    (real_rpmh_xo_vote_changes_both_named_carriers_without_inventing_sleep, {"rpmh": True}),
    (cold_ahb_safe_enable_sequence_and_branch_demand_are_source_bound, {}),
]


def default_profile_rejects_every_word_despite_other_enabled_profiles(args, out):
    for legacy in (False, True):
        rig = VideoCcRig(args, out/("default-legacy" if legacy else "default-alone"),
                         reference=None, video=legacy, legacy=legacy)
        try:
            assert rig.qom("enabled-reference") is False
            before = rig.pure_snapshot()
            count = rig.log_path.read_text(errors="replace").count(TYPE+":")
            for offset in REGISTERS:
                rig.read(offset)
                rig.write(offset, 1)
            assert rig.log_path.read_text(errors="replace").count(TYPE+":") == count+110
            assert rig.pure_snapshot() == before
            for name in INPUTS[2:]:
                rig.fixture(name, 96_000_001)
            for i in range(4):
                rig.vote(i, True)
            rig.step(20_000)
            assert all(rig.reg(o) == 0 for o in REGISTERS)
            assert not any(rig.pll(i, "locked") for i in range(2))
            for name in OUTPUTS:
                rig.expect_hz(name, 0)
        finally:
            rig.close()


def paused_tcg_has_only_pure_observers_and_does_not_execute_guest(args, out):
    rig = VideoCcRig(args, out/"paused-tcg", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        before = rig.pure_snapshot(include_clock_periods=False)
        names = {p["name"] for p in rig.monitor("qom-list", {"path": DEVICE})}
        assert not any(n.startswith("qtest-") for n in names)
        for name in ["qtest-"+n+"-hz" for n in INPUTS]+[f"qtest-divider{i}-value" for i in range(4)]:
            reply = rig.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": 1})
            assert "error" in reply and "not found" in reply["error"]["desc"]
        assert rig.pure_snapshot(include_clock_periods=False) == before
        assert rig.cont_commands == rig.device_mmio_operations == 0
        assert rig.monitor("query-status")["running"] is False
    finally:
        rig.close()


def sparse_26_leaves_preserve_all_four_enabled_video_gdsc_owners(args, out):
    rig = VideoCcRig(args, out/"sparse-and-video-gdsc-owners", video=True)
    try:
        tree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory/"mtree.txt").write_text(tree)
        matches = re.findall(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ([^\s]+)", tree)
        actual = {(int(a, 16)-CC, int(b, 16)-int(a, 16)+1)
                  for a, b, name in matches if name.startswith(TYPE+".")}
        assert actual == set(WINDOWS) and sum(n for _, n in actual) == 220
        for offset, domain in zip(GDSC_OFFSETS, GDSC_NAMES):
            ranges = {(int(a, 16), int(b, 16)) for a, b, name in matches
                      if name == f"qcom-video-gdsc.{domain}-control"}
            assert ranges == {(CC+offset, CC+offset+3)}
        before = rig.pure_snapshot()
        for i, offset in enumerate(GDSC_OFFSETS):
            assert rig.read(offset) == 1
            rig.write(offset, ((i+1) << 12) | 0x801)
            assert rig.read(offset) == ((i+1) << 12) | 0x801
        video = rig.video_snapshot()
        assert rig.pure_snapshot() == before
        configure_all(rig)
        enable_all(rig)
        for offset, mask in RESETS:
            rig.write(offset, mask)
        assert rig.video_snapshot() == video
        assert rig.qom("reference-identity", VIDEO) == 0x56444731
    finally:
        rig.close()


def videocc_payload(snapshot):
    """Independent complete v1 wire: factors/configs/deadlines/inputs, no MND."""
    raw = bytearray(struct.pack(">13IQ", *(snapshot["globals"][n] for n in GLOBAL_FIELDS)))
    raw.extend(struct.pack(">55I", *(snapshot["registers"][f"{o:04x}"] for o in REGISTERS)))
    for field in ("active-l", "active-alpha", "pending-l", "pending-alpha"):
        raw.extend(struct.pack(">2I", *(p[field] for p in snapshot["plls"])))
    for field in ("active-cfg", "pending-cfg"):
        raw.extend(struct.pack(">5I", *(r[field] for r in snapshot["rcgs"])))
    for field in ("lock-deadline", "latch-deadline"):
        raw.extend(struct.pack(">2q", *(p[field] for p in snapshot["plls"])))
    raw.extend(struct.pack(">5qQ", *(r["deadline"] for r in snapshot["rcgs"]), snapshot["last-xo-hz"]))
    for field in ("locked", "ack", "calibrated"):
        assert all(type(p[field]) is bool for p in snapshot["plls"])
        raw.extend(bytes(p[field] for p in snapshot["plls"]))
    assert all(type(v) is bool for v in snapshot["remote-votes"])
    raw.extend(bytes(snapshot["remote-votes"]))
    raw.extend(struct.pack(">4Q", *(snapshot["inputs"][name] for name in INPUTS)))
    assert len(raw) == VMSTATE_BYTES
    return bytes(raw)


def inspect_stream(path, snapshot, video):
    data, sections = path.read_bytes(), {}
    for label, name, expected in (("videocc", TYPE, videocc_payload(snapshot)),
                                  ("video", "qcom-video-gdsc", video_payload(video))):
        section = unique_section(data, name, 1, len(expected))
        offset = section["payload_offset"]
        raw = data[offset:offset+len(expected)]
        assert raw == expected, (label, [i for i, (a, b) in enumerate(zip(raw, expected)) if a != b][:32])
        payload = path.with_name(path.name+f".{label}-v1.payload.bin")
        payload.write_bytes(raw)
        section.update(payload_file=payload.name, payload_sha256=digest(payload),
                       all_bytes_match_stopped_source_qom=True)
        sections[label] = section
    write_json(path.with_name(path.name+".sections.json"), sections)
    return sections


def save_source(rig, path, witness):
    rig.raw_write(RAM, witness)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    snapshot, video = rig.pure_snapshot(), rig.video_snapshot()
    write_json(rig.directory/"source-before-save.json", {"videocc": snapshot, "video": video})
    rig.monitor("migrate", file_channels(path))
    sent = wait_migration(rig)
    assert sent["status"] == "completed"
    assert rig.monitor("query-status")["status"] == "postmigrate"
    assert rig.pure_snapshot() == snapshot and rig.video_snapshot() == video
    sections = inspect_stream(path, snapshot, video)
    write_json(rig.directory/"source-query-migrate.json", sent)
    return {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
            "source_directory": rig.directory.name, "source_virtual_ns": rig.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "video": video, "sections": sections,
            "ram_witness": witness, "stream_modified": False}


def prewarm_target(rig, saved):
    # Incoming-defer timers are stopped. Reads here intentionally dispatch
    # target preparation only; after incoming the first observations are pure.
    at_clock = saved["source_virtual_ns"]
    assert at_clock >= 10_000
    rig.set_clock(at_clock-10_000)
    configure_all(rig)
    for base, *_rest in PLLS:
        rig.write(base+56, 1)
    rig.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        rig.read(row[0])
        assert rig.pll(i, "locked")
        output_on(rig, i)
    for i in range(5):
        configure_rcg(rig, i)
    assert rig.last_clock == at_clock
    for i, name in enumerate(INPUTS[2:]):
        rig.fixture(name, 70_000_003+i)
    for i in range(4):
        rig.divider(i, 3)
        rig.vote(i, not saved["snapshot"]["remote-votes"][i])
    for offset, *_rest in BRANCHES:
        rig.write(offset, 1)
    for offset, mask in RESETS:
        rig.write(offset, rig.read(offset) | mask)
    for base, _name, l, _alpha, _recipe in PLLS:
        rig.write(base+4, l+3)
        rig.write(base, rig.read(base) | UPDATE)
    for i in range(5):
        configure_rcg(rig, i, 7, 7, wait=False)
    rig.command(f"set_irq_in {VIDEO} parent-known 0 1")
    rig.command(f"set_irq_in {VIDEO} parent-on 0 1")
    for i, offset in enumerate(GDSC_OFFSETS):
        rig.command(f"set_irq_in {VIDEO} hw-request-known {i} 1")
        rig.command(f"set_irq_in {VIDEO} hw-request {i} 1")
        rig.write(offset, 0x7800)
    assert all(d["powered"] for d in rig.video_snapshot()["domains"])
    assert rig.pure_snapshot() != saved["snapshot"]


def receive_source(rig, path, saved):
    rig.set_clock(saved["source_virtual_ns"])
    mmio, cont = rig.device_mmio_operations, rig.cont_commands
    rig.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(rig)
    assert received["status"] == "completed"
    assert rig.monitor("query-status")["running"] is False
    first, video = rig.pure_snapshot(), rig.video_snapshot()
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert video == saved["video"]
    assert (rig.device_mmio_operations, rig.cont_commands) == (mmio, cont)
    assert rig.raw_read(RAM) == saved["ram_witness"]
    rig.reset_lines([bool(first["registers"][f"{offset:04x}"] & mask) for offset, mask in RESETS])
    evidence = {"snapshot": first, "video": video, "device_mmio_since_incoming": False,
                "qmp_cont_since_incoming": False, "guest_cpu_executed": False,
                "ram_witness": saved["ram_witness"], "query_migrate": received}
    write_json(rig.directory/"destination-before-first-device-mmio.json", evidence)
    return evidence


def load_failure(rig, path, diagnostic):
    error = None
    try:
        rig.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as exc:
        error = str(exc)
    code = rig.process.wait(timeout=20)
    stderr = rig.log_path.read_text(errors="replace")
    assert code == 1, (code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr
    return {"destination_exit": code, "expected_diagnostic": diagnostic,
            "specific_load_failure_observed": True, "command_error": error,
            "target_directory": rig.directory.name}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **options):
        rig = VideoCcRig(args, out/name, video=True, **options)
        rigs.append(rig)
        return rig

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, source, target, first, **details):
        report["migrations"].append({
            "name": name, "passed": True, "source_status": "completed", "target_status": "completed",
            "snapshot_file": path.name, "snapshot_sha256": digest(path), "stream_modified": False,
            "source_directory": source.directory.name, "target_directory": target.directory.name,
            "first_target_observation": first, "continued_after_restore": True,
            "all_474_payload_bytes_verified": True, "video_142_bytes_verified": True, **details})
        passed(name, device_mmio_before_first_observation=False, **details)

    try:
        steady = new("migration-steady-source")
        run_defaults(steady)
        steady.fixture("core_bi_pll_test_se", 111_111_113)
        steady.fixture("chip_sleep_clk", 32_000)
        for i in range(5):
            configure_rcg(steady, i, 7, 3+i)
        for i, (offset, *_rest) in enumerate(BRANCHES):
            steady.write(offset, i & 1)
        for i in range(4):
            steady.divider(i, i)
            steady.vote(i, not bool(i & 1))
        for i, (offset, mask) in enumerate(RESETS):
            steady.write(offset, (steady.read(offset) & ~mask) | (mask if i & 1 else 0))
        steady.command(f"set_irq_in {VIDEO} parent-known 0 1")
        steady.command(f"set_irq_in {VIDEO} parent-on 0 1")
        for i, offset in enumerate(GDSC_OFFSETS):
            steady.write(offset, ((i+1) << 12) | 0x800 | (i & 1))
        # Keep one acknowledged update canceled by withdrawing BYPASS while
        # UPDATE remains high; this is reference-state consistency coverage.
        base = PLLS[0][0]
        steady.write(base+4, 0x26)
        steady.write(base, steady.read(base) | UPDATE)
        steady.step(LATCH_NS+LOCK_NS)
        assert steady.pll(0, "ack") and steady.pll(0, "locked")
        steady.write(base, steady.read(base) & ~UPDATE_BYPASS)
        assert not steady.pll(0, "ack") and steady.pll(0, "latch-deadline") == -1
        state_path = out/"videocc-v1-steady.migration"
        saved_state = save_source(steady, state_path, 0x56494431)
        report["migration_sources"].append(saved_state)
        restored = new("migration-steady-target", incoming=True)
        prewarm_target(restored, saved_state)
        first = receive_source(restored, state_path, saved_state)
        mmio = restored.device_mmio_operations
        restored.monitor("cont")
        restored.step(20_000)
        assert restored.pure_snapshot() == saved_state["snapshot"]
        assert restored.device_mmio_operations == mmio
        restored.fixture("core_bi_pll_test_se", 99_000_001)
        for i, row in enumerate(RCGS):
            restored.expect_hz(row[1], divided(99_000_001, 3+i))
        restored.vote(2, False)
        restored.expect_hz("video_cc_mvs1_div2_clk", 0)
        assert restored.video_snapshot() == saved_state["video"]
        completed("v1_steady_clocks_ro_dividers_votes_ares_and_video_replace_stale_target",
                  state_path, steady, restored, first, stale_target_deadlines_discarded=True,
                  canceled_ack_does_not_revive=True, clock_and_vote_changes_propagate_after_restore=True)

        locking = new("migration-two-locks-five-rcgs-source")
        locking.step(100_000)
        configure_all(locking)
        locking.fixture("core_bi_pll_test_se", 96_000_001)
        for i, row in enumerate(PLLS):
            output_on(locking, i)
            locking.write(row[0]+56, 1)
        start = locking.last_clock
        for i in range(5):
            configure_rcg(locking, i, 7, 3, wait=False)
        locking.vote(0, True)
        locking.vote(3, True)
        locking.write(0xc34, 4)
        locking.write(0xcd4, 4)
        locking.step(400)
        assert all(locking.pll(i, "lock-deadline") == start+5000 for i in range(2))
        assert all(locking.rcg(i, "deadline") == start+1000 for i in range(5))
        lock_path = out/"videocc-v1-two-locks-five-rcgs.migration"
        saved_lock = save_source(locking, lock_path, 0x56494432)
        report["migration_sources"].append(saved_lock)
        resumed = new("migration-two-locks-five-rcgs-target", incoming=True)
        prewarm_target(resumed, saved_lock)
        first = receive_source(resumed, lock_path, saved_lock)
        mmio = resumed.device_mmio_operations
        resumed.monitor("cont")
        resumed.step(599)
        assert all(resumed.rcg(i, "pending") for i in range(5))
        assert not any(resumed.pll(i, "locked") for i in range(2))
        resumed.step(1)
        for i, row in enumerate(RCGS):
            assert not resumed.rcg(i, "pending")
            resumed.expect_hz(row[1], divided(96_000_001, 3))
        resumed.step(3999)
        assert not any(resumed.pll(i, "locked") for i in range(2))
        resumed.step(1)
        for i, row in enumerate(PLLS):
            assert resumed.pll(i, "locked")
            resumed.expect_hz(row[1], pll_rate(i))
        assert resumed.device_mmio_operations == mmio
        assert resumed.video_snapshot() == saved_lock["video"]
        completed("v1_two_locks_five_rcg_deadlines_and_shared_ares_resume_without_mmio_repair",
                  lock_path, locking, resumed, first, remaining_rcg_ns=600,
                  remaining_pll_ns=4600, device_mmio_before_deadline_checks=False)

        latching = new("migration-two-latches-five-configs-source")
        run_defaults(latching)
        latching.fixture("core_bi_pll_test_se", 123_456_789)
        latching.vote(1, True)
        latching.vote(2, True)
        latching.divider(2, 1)
        latching.divider(3, 2)
        # A requested VIDEO GDSC with an unknown supplier must replace the
        # target's known-on GPIO state, independently of clock controller state.
        latching.write(GDSC_OFFSETS[0], 0x2800)
        assert not latching.video_snapshot()["domains"][0]["state-known"]
        captured = ((0x26, 0x2345), (0x2c, 0x6789))
        for i, (base, *_rest) in enumerate(PLLS):
            latching.write(base+4, captured[i][0])
            latching.write(base+64, captured[i][1])
            latching.write(base, latching.read(base) | UPDATE)
            latching.write(base+4, 0x30+i)
            latching.write(base+64, 0xabcd)
            latching.write(base, latching.read(base) | UPDATE)
        start = latching.last_clock
        for i, (offset, *_rest) in enumerate(RCGS):
            configure_rcg(latching, i, 0 if i == 3 else 7, 14, wait=False)
            latching.write(offset+4, rcg_cfg(i, 0, 1))
            latching.write(offset, ROOT | BUSY)
        latching.step(400)
        latch_path = out/"videocc-v1-two-latches-five-configs.migration"
        saved_latch = save_source(latching, latch_path, 0x56494433)
        report["migration_sources"].append(saved_latch)
        target = new("migration-two-latches-five-configs-target", incoming=True)
        prewarm_target(target, saved_latch)
        first = receive_source(target, latch_path, saved_latch)
        assert target.period("chip_sleep_clk") == 0
        assert not target.video_snapshot()["domains"][0]["state-known"]
        mmio = target.device_mmio_operations
        target.monitor("cont")
        target.step(599)
        assert all(target.pll(i, "latch-pending") for i in range(2))
        assert all(target.rcg(i, "pending") for i in range(5))
        for i, row in enumerate(PLLS):
            target.expect_hz(row[1], pll_rate(i))
        target.step(1)
        for i, row in enumerate(PLLS):
            assert target.pll(i, "ack") and not target.pll(i, "locked")
            assert (target.pll(i, "active-l"), target.pll(i, "active-alpha")) == captured[i]
            assert target.pll(i, "lock-deadline") == start+6000
            target.expect_hz(row[1], 0)
        for i, (offset, name, *_rest) in enumerate(RCGS):
            assert target.rcg(i, "active-cfg") == rcg_cfg(i, 0 if i == 3 else 7, 14)
            assert target.reg(offset+4) == rcg_cfg(i, 0, 1)
            target.expect_hz(name, 0 if i == 3 else divided(123_456_789, 14))
        target.step(4999)
        assert not any(target.pll(i, "locked") for i in range(2))
        target.step(1)
        for i, row in enumerate(PLLS):
            target.expect_hz(row[1], pll_rate(i, *captured[i]))
        assert target.device_mmio_operations == mmio
        assert target.video_snapshot() == saved_latch["video"]
        target.fixture("chip_sleep_clk", 32_000)
        target.expect_hz("video_cc_sleep_clk_src", divided(32_000, 14))
        completed("v1_two_latches_five_captured_configs_and_unknown_sources_replace_prewarm",
                  latch_path, latching, target, first, remaining_latch_ns=600,
                  device_mmio_before_deadline_checks=False,
                  unknown_input_and_video_parent_replace_prewarmed_state=True)

        disabled = new("migration-default-source", reference=None)
        disabled.fixture("chip_sleep_clk", 32_000)
        disabled.vote(1, True)
        disabled_path = out/"videocc-v1-default.migration"
        saved_disabled = save_source(disabled, disabled_path, 0x56494430)
        report["migration_sources"].append(saved_disabled)
        for name, path, saved, enabled, diagnostic in (
                ("enabled_to_disabled", state_path, saved_state, False, "0 != 1"),
                ("disabled_to_enabled", disabled_path, saved_disabled, True, "1 != 0")):
            rejected = new("reject-"+name, incoming=True, reference=enabled)
            rejected.set_clock(saved["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["rejected_migrations"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": False, **details})
            passed("profile_"+name+"_rejected", **details)
        original = state_path.read_bytes()
        section = saved_state["sections"]["videocc"]
        shared_index = REGISTERS.index(0xc34)
        shared_value = saved_state["snapshot"]["registers"]["0c34"]
        for name, relative, width, old, xor, diagnostic in (
                ("physical_base", 52, 8, CC, 4, f"{CC:x} != {CC ^ 4:x}"),
                ("shared_ares_reserved_control", 60+4*shared_index, 4, shared_value, 2,
                 TYPE+f": invalid migrated register {shared_index}")):
            absolute = section["payload_offset"]+relative
            assert int.from_bytes(original[absolute:absolute+width], "big") == old
            mutated = bytearray(original)
            mutated[absolute:absolute+width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, (a, b) in enumerate(zip(original, mutated)) if a != b]
            assert differences == [absolute+width-1]
            assert (original[differences[0]] ^ mutated[differences[0]]).bit_count() == 1
            path = out/f"videocc-corrupt-{name}.migration"
            path.write_bytes(mutated)
            rejected = new("reject-corrupt-"+name, incoming=True)
            rejected.set_clock(saved_state["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": True,
                "field_offset": relative, "changed_byte_offsets": differences,
                "changed_bits": 1, "original_stream_unchanged": True, **details})
            passed("single_bit_"+name+"_rejected", **details)
        assert all(digest(out/saved["file"]) == saved["sha256"] for saved in report["migration_sources"])
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--machine", default=DEFAULT_MACHINE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started, original_hash = time.monotonic(), digest(Path(args.qemu))
    report = {
        "schema": "tb371fc-videocc-sm8250-v1", "suite": "videocc-sm8250-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "expected_unmodified_streams": 4,
        "expected_stream_files": 6, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "physical_base": CC, "resource_bytes": 65536, "register_count": 55,
        "sparse_window_count": 26, "mapped_bytes": 220,
        "output_clock_count": 19, "input_clock_count": 4,
        "connected_input_clock_count": 2, "unknown_external_input_count": 2,
        "pll_count": 2, "lucid_pll_count": 2, "postdivider_count": 0,
        "rcg_count": 5, "mnd_rcg_count": 0, "hw_control_rcg_count": 3,
        "branch_count": 8, "divider_count": 4, "writable_divider_count": 0,
        "read_only_divider_count": 4, "reset_count": 7, "remote_vote_count": 4,
        "shared_branch_reset_count": 2, "modeled_timer_count": 1,
        "video_vmstate_version": 1, "video_vmstate_payload_bytes": 142,
        "actual_compatible": "qcom,videocc-kona-v2", "reference_enabled_by_default": False,
        # Accepted generic-to-fixup writes do not qualify legacy PLL1 locking.
        "legacy_then_v2_recipe_supported": True, "legacy_pll1_lock_accepted": False,
        "generic_dividers_round_up": True,
        "div2_name_assumed": False, "critical_requests_forced": False,
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "source_sha256": {
            "videocc-kona.c": SOURCE_SHA256, "qcom,videocc-kona.h": BINDING_SHA256,
            "clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
            "clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
            "clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
            "clk-branch.h": "da132bd1a093a650abf64073a4e695719e0612b67bb0166726737e0f2a3332d5",
            "clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
            "clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
            "clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2",
            "reset.c": "db88de0848c75cecff57623b94d63993a8f869a27686df4bd7b7be0765317f11"},
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "video_execution_observed": False, "analog_pll_behavior_modeled": False,
        "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP, fixed-source digital clock protocols, "
                 "real Clock propagation and independent migration; virtual deadlines "
                 "are reference ordering assumptions, not physical measurements",
        "binary_unchanged": False, "checks": [], "migrations": [],
        "rejected_migrations": [], "corrupted_stream_rejections": [],
        "migration_sources": [], "actual_stream_files": [], "passed": False,
    }
    try:
        assert len(ORDINARY) == 31
        assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(INPUTS), len(PLLS),
                len(RCGS), len(BRANCHES), len(DIVIDERS), len(RESETS)) == (55, 26, 19, 4, 2, 5, 8, 4, 7)
        for index, (test, options) in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = VideoCcRig(args, fixture, **options)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_profile_rejects_every_word_despite_other_enabled_profiles,
                     paused_tcg_has_only_pure_observers_and_does_not_execute_guest,
                     sparse_26_leaves_preserve_all_four_enabled_video_gdsc_owners):
            test(args, out)
            report["checks"].append({"name": test.__name__, "passed": True})
        migration_checks(args, out, report)
        report["actual_stream_files"] = [
            {"name": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in sorted(out.glob("*.migration"))]
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 3 and
            all(item["source_status"] == item["target_status"] == "completed" and
                item["continued_after_restore"] and item["all_474_payload_bytes_verified"] and
                item["video_142_bytes_verified"] for item in report["migrations"]) and
            len(report["rejected_migrations"]) == len(report["corrupted_stream_rejections"]) == 2 and
            all(item["passed"] is True and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"]+report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 4 and len(report["actual_stream_files"]) == 6 and
            len({item["name"] for item in report["actual_stream_files"]}) == 6 and
            all(item["bytes"] > 0 for item in report["actual_stream_files"]) and report["binary_unchanged"])
    except BaseException as error:
        report["error"], report["traceback"] = str(error), traceback.format_exc()
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
    report["duration_seconds"] = round(time.monotonic()-started, 6)
    write_json(out/"result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
