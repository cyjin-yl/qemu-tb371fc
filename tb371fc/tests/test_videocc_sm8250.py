#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled Kona-v2 VIDEOCC sparse MMIO, clock protocols and actual migrations.

The literal inventory is read independently from the pinned vendor driver,
not imported from the model generator. Optional digital reference timings do
not establish physical video decoding, a native probe, or Android boot.
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
RESOURCE_BYTES = 0x10000
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
XO, PERIOD_UNIT = 19_200_000, 1_000_000_000 << 32
OUTCTRL, RESET_N, UPDATE, UPDATE_BYPASS = 1, 4, 1 << 22, 1 << 23
PCAL, ACK, LOCK, OFF = 1 << 27, 1 << 29, 1 << 31, 1 << 31
ROOT, BUSY, HW_CLK, ARES = 2, 1, 1 << 20, 4
LOCK_NS, LATCH_NS, RCG_NS = 5000, 1000, 1000
PLLS = ((0x042c, "video_pll0", 0x25, 0x8000),
        (0x07d0, "video_pll1", 0x2b, 0xc000))
PLL_OFFSETS = (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 56, 64)
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "core_bi_pll_test_se", "chip_sleep_clk")
RESETS = ((0xe54, 1), (0xd14, 1), (0xc34, ARES), (0xbf4, 1),
          (0xd94, 1), (0xcd4, ARES), (0xc94, 1))
GDSC_OFFSETS = (0xbf8, 0xc98, 0xd18, 0xd98)
GDSC_NAMES = ("mvs0c", "mvs1c", "mvs0", "mvs1")
SHARED = (0xc34, 0xcd4)
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_SHA256 = "33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5"
BINDING_SHA256 = "0f51753e9eb497399d3c2d9a362e6d3ee7baf4f789b6e6628a7911b00f89d774"
OUTPUTS = ("video_cc_ahb_clk", "video_cc_ahb_clk_src", "video_cc_mvs0_clk",
           "video_cc_mvs0_clk_src", "video_cc_mvs0_div_clk_src", "video_cc_mvs0c_clk",
           "video_cc_mvs0c_div2_div_clk_src", "video_cc_mvs1_clk", "video_cc_mvs1_clk_src",
           "video_cc_mvs1_div2_clk", "video_cc_mvs1_div_clk_src", "video_cc_mvs1c_clk",
           "video_cc_mvs1c_div2_div_clk_src", "video_cc_sleep_clk", "video_cc_sleep_clk_src",
           "video_cc_xo_clk", "video_cc_xo_clk_src", "video_pll0", "video_pll1")
# Declaration order, not address or binding order. All five have MND width 0.
RCGS = ((0xbd4, "video_cc_ahb_clk_src", True,
         ((0, "bi_tcxo_ao"), (7, "core_bi_pll_test_se"))),
        (0xb94, "video_cc_mvs0_clk_src", True,
         ((0, "bi_tcxo"), (1, "video_pll0"), (7, "core_bi_pll_test_se"))),
        (0xbb4, "video_cc_mvs1_clk_src", True,
         ((0, "bi_tcxo"), (1, "video_pll1"), (7, "core_bi_pll_test_se"))),
        (0xef0, "video_cc_sleep_clk_src", False,
         ((0, "chip_sleep_clk"), (7, "core_bi_pll_test_se"))),
        (0xecc, "video_cc_xo_clk_src", False,
         ((0, "bi_tcxo_ao"), (7, "core_bi_pll_test_se"))))
BRANCHES = ((0xe58, "video_cc_ahb_clk", "video_cc_ahb_clk_src", False),
            (0xd34, "video_cc_mvs0_clk", "video_cc_mvs0_div_clk_src", True),
            (0xc34, "video_cc_mvs0c_clk", "video_cc_mvs0c_div2_div_clk_src", False),
            (0xdb4, "video_cc_mvs1_clk", "video_cc_mvs1_div_clk_src", True),
            (0xdf4, "video_cc_mvs1_div2_clk", "video_cc_mvs1c_div2_div_clk_src", True),
            (0xcd4, "video_cc_mvs1c_clk", "video_cc_mvs1c_div2_div_clk_src", True),
            (0xf10, "video_cc_sleep_clk", "video_cc_sleep_clk_src", False),
            (0xeec, "video_cc_xo_clk", "video_cc_xo_clk_src", False))
DIVIDERS = ((0xd54, "video_cc_mvs0_div_clk_src", "video_cc_mvs0_clk_src"),
            (0xc54, "video_cc_mvs0c_div2_div_clk_src", "video_cc_mvs0_clk_src"),
            (0xdd4, "video_cc_mvs1_div_clk_src", "video_cc_mvs1_clk_src"),
            (0xcf4, "video_cc_mvs1c_div2_div_clk_src", "video_cc_mvs1_clk_src"))
REGISTERS = tuple(sorted(set(
    [base + offset for base, *_ in PLLS for offset in PLL_OFFSETS] +
    [base + offset for base, *_ in RCGS for offset in (0, 4)] +
    [offset for offset, *_ in BRANCHES] + [offset for offset, *_ in DIVIDERS] +
    [offset for offset, _mask in RESETS])))
WINDOWS = []
for offset in REGISTERS:
    if WINDOWS and offset == WINDOWS[-1][0] + WINDOWS[-1][1]:
        WINDOWS[-1] = (WINDOWS[-1][0], WINDOWS[-1][1] + 4)
    else:
        WINDOWS.append((offset, 4))
WINDOWS = tuple(WINDOWS)
RCG_INDEX = {row[1]: i for i, row in enumerate(RCGS)}
BRANCH_INDEX = {row[1]: i for i, row in enumerate(BRANCHES)}
HW_RCG = tuple(i for i, row in enumerate(RCGS) if row[2])
VOTED = tuple(i for i, row in enumerate(BRANCHES) if row[3])
READ_ONLY_DIVIDERS = tuple(range(4))
assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(RCGS), len(BRANCHES),
        len(DIVIDERS), len(HW_RCG), len(VOTED)) == (55, 26, 19, 5, 8, 4, 3, 4)
assert not set(REGISTERS) & set(GDSC_OFFSETS)

IDENTITY, VMSTATE_BYTES, EXPECTED_CHECKS = 0x56494332, 474, 36
GLOBAL_FIELDS = ("reference-profile", "reference-identity", "register-count", "clock-count",
                 "input-count", "pll-count", "rcg-count", "branch-count", "divider-count",
                 "reset-count", "remote-vote-count", "window-count", "mapped-bytes", "physical-base")
PLL_FIELDS = ("active-l", "active-alpha", "pending-l", "pending-alpha", "locked", "ack",
              "calibrated", "lock-pending", "latch-pending", "lock-deadline", "latch-deadline")
RCG_FIELDS = ("active-cfg", "pending-cfg", "pending", "deadline")


class VideoCCRig(CameraRig):
    """Existing socket/RPMh transport, with entirely independent VIDEOCC accessors."""
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
            if CC <= int(fields[1], 0) < CC + RESOURCE_BYTES:
                self.videocc_mmio_operations += 1
                self.device_mmio_operations += 1
        return super().command(command)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def reg(self, offset):
        return self.qom(f"reg.{offset:04x}")

    def read(self, offset):
        return self.raw_read(CC + offset)

    def write(self, offset, value):
        self.raw_write(CC + offset, value)

    def dread(self, offset, drv=2):
        return self.raw_read(RPMH_BASE + drv * 0x10000 + offset)

    def dwrite(self, offset, value, drv=2):
        self.raw_write(RPMH_BASE + drv * 0x10000 + offset, value)

    def period(self, name):
        return self.qom("qtest-clock-period", DEVICE + "/" + name)

    def hz(self, name):
        return self.qom("clk." + name + ".hz")

    def expect_hz(self, name, hz):
        assert self.hz(name) == hz, (name, self.hz(name), hz)
        assert self.period(name) == (PERIOD_UNIT // hz if hz else 0), (name, hz)

    def fixture(self, name, hz):
        self.monitor("qom-set", {"path": DEVICE, "property": "qtest-" + name + "-hz", "value": hz})

    def divider(self, index, value):
        assert index in READ_ONLY_DIVIDERS
        self.monitor("qom-set", {"path": DEVICE, "property": f"qtest-divider{index}-value", "value": value})

    def vote(self, index, value):
        assert 0 <= index < 4
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
                "domains": [{n: self.qom(f"domain{i}." + n, VIDEO) for n in GDSC_DOMAIN_FIELDS} for i in range(4)]}

    def reject_write(self, offset, value):
        before = self.reg(offset)
        count = self.log_path.read_text(errors="replace").count(TYPE + ":")
        self.write(offset, value)
        assert self.reg(offset) == before, (hex(offset), hex(value))
        assert self.log_path.read_text(errors="replace").count(TYPE + ":") == count + 1

    def reset_lines(self, expected, require_seen=False):
        self.raw_read(RAM)
        assert [self.irq.get(i, False) for i in range(7)] == list(expected)
        if require_seen:
            assert set(range(7)) <= self.seen_irq

    def reset_device(self):
        self.events.clear()
        self.monitor("system_reset")
        self.wait_for(lambda: all(self.reg(p[0] + 4) == 0 for p in PLLS), "VIDEOCC reset witnesses")
        self.monitor("query-status")
        assert any(e.get("event") == "RESET" for e in self.events)


def pll_rate(index, l=None, alpha=None):
    row = PLLS[index]
    return XO * (row[2] if l is None else l) + ((XO * (row[3] if alpha is None else alpha)) >> 16)


def configure_pll(r, index, *, legacy=False):
    base, _name, l, alpha = PLLS[index]
    if index == 1 and legacy:
        l, alpha = 0x29, 0xfaaa
    mode, opmode = r.read(base), r.read(base + 56)
    if mode & OUTCTRL and opmode & 1:
        return
    high1 = 0x029a699c if index == 0 or legacy else 0x329a699c
    # Fixed helper skips zero fields: do not clear retained USER/TEST storage.
    for offset, value in ((4, l), (8, 0x44), (64, alpha), (24, 0x20485699),
                          (28, 0x2261), (32, high1), (16, 0x805)):
        r.write(base + offset, value)
    r.write(base, r.read(base) | UPDATE_BYPASS)
    r.write(base, r.read(base) & ~OUTCTRL)
    r.write(base + 56, 0)
    r.write(base, r.read(base) | RESET_N)


def configure_all(r):
    # Actual v2 probe order: two base recipes, followed by PLL1 v2 fixup.
    configure_pll(r, 0, legacy=True)
    configure_pll(r, 1, legacy=True)
    configure_pll(r, 1)


def output_on(r, index):
    base = PLLS[index][0]
    r.write(base + 12, r.read(base + 12) | 7)
    r.write(base, r.read(base) | OUTCTRL)


def enable_all(r):
    for base, *_ in PLLS:
        r.write(base + 56, 1)
    r.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        assert r.pll(i, "locked") is True
        output_on(r, i)
        r.expect_hz(row[1], pll_rate(i))


def rcg_cfg(index, selector=0, hid=1, mode=0):
    return (HW_CLK if RCGS[index][2] else 0) | selector << 8 | hid | mode << 12


def configure_rcg(r, index, selector=0, hid=1, *, wait=True, root=True):
    offset = RCGS[index][0]
    r.write(offset + 4, rcg_cfg(index, selector, hid))
    r.write(offset, (ROOT if root else 0) | BUSY)
    if wait:
        r.step(RCG_NS)
        assert not r.read(offset) & BUSY


def divided(hz, hid):
    return hz * 2 // (hid + 1) if hid else hz


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


def reset_bit(r, index, asserted):
    offset, mask = RESETS[index]
    value = r.read(offset)
    r.write(offset, value | mask if asserted else value & ~mask)


def cold_inventory_has_only_two_evidenced_inputs(r):
    values = (1, IDENTITY, 55, 19, 4, 2, 5, 8, 4, 7, 4, 26, 220, CC)
    assert {n: r.qom(n) for n in GLOBAL_FIELDS} == dict(zip(GLOBAL_FIELDS, values))
    status_words = {row[0] for row in RCGS} | {row[0] for row in BRANCHES}
    for offset in REGISTERS:
        assert r.reg(offset) == 0
        assert r.read(offset) == (OFF if offset in status_words else 0), hex(offset)
    for name in OUTPUTS:
        r.expect_hz(name, 0)
    assert {n: r.period(n) for n in INPUTS} == {
        n: PERIOD_UNIT // XO if i < 2 else 0 for i, n in enumerate(INPUTS)}
    before = r.pure_snapshot()
    r.step(100_000)
    assert r.pure_snapshot() == before


def literal_v2_probe_overwrites_pll1_base_recipe_but_skips_zero_writes(r):
    configure_pll(r, 0, legacy=True)
    configure_pll(r, 1, legacy=True)
    base = PLLS[1][0]
    assert (r.reg(base+4), r.reg(base+64), r.reg(base+32)) == (0x29, 0xfaaa, 0x029a699c)
    assert not any(r.pll(i, "lock-pending") for i in range(2))
    configure_pll(r, 1)
    assert (r.reg(base+4), r.reg(base+64), r.reg(base+32)) == (0x2b, 0xc000, 0x329a699c)
    for i, (base, _name, l, alpha) in enumerate(PLLS):
        expected = {0: RESET_N | UPDATE_BYPASS, 4: l, 8: 0x44, 12: 0, 16: 0x805,
                    20: 0, 24: 0x20485699, 28: 0x2261,
                    32: 0x029a699c if i == 0 else 0x329a699c,
                    36: 0, 40: 0, 44: 0, 56: 0, 64: alpha}
        assert {o: r.read(base+o) for o in PLL_OFFSETS} == expected
        r.write(base+12, 7)
        r.write(base+20, 0x1234)
        r.write(base+36, 0x5678)
        configure_pll(r, i)
        assert (r.reg(base+12), r.reg(base+20), r.reg(base+36)) == (7, 0x1234, 0x5678)
    assert all(r.hz(n) == 0 for n in OUTPUTS)


def cold_critical_ahb_and_xo_require_requests_and_the_ao_provider(r):
    # Critical is a CCF policy, not a hardware reset enable. CFG zero selects AO.
    for name in ("video_cc_ahb_clk", "video_cc_xo_clk"):
        offset, _name, parent, _voted = BRANCHES[BRANCH_INDEX[name]]
        r.expect_hz(name, 0)
        r.write(offset, 1)
        r.expect_hz(name, XO)
        r.expect_hz(parent, XO)
        assert not r.read(offset) & OFF
        r.write(offset, 0)
        r.expect_hz(name, 0)
        r.expect_hz(parent, 0)
    xo = RCG_INDEX["video_cc_xo_clk_src"]
    r.write(RCGS[xo][0], ROOT)
    expect_root(r, xo, XO)
    r.expect_hz("video_cc_xo_clk", 0)
    r.write(RCGS[xo][0], 0)
    expect_root(r, xo, 0)
    assert all(not r.pll(i, "locked") for i in range(2))


def only_the_v2_pll1_recipe_can_lock_and_banks_are_independent(r):
    configure_pll(r, 0, legacy=True)
    configure_pll(r, 1, legacy=True)
    for i, row in enumerate(PLLS):
        output_on(r, i)
        r.write(row[0]+56, 1)
    r.step(20_000)
    assert r.pll(0, "locked") and not r.pll(1, "locked")
    r.expect_hz("video_pll0", 720_000_000)
    r.expect_hz("video_pll1", 0)
    base = PLLS[1][0]
    r.write(base+56, 0)
    configure_pll(r, 1)
    r.write(base+56, 1)
    r.step(LOCK_NS)
    output_on(r, 1)
    r.expect_hz("video_pll1", 840_000_000)
    for i, (base, name, l, _alpha) in enumerate(PLLS):
        r.write(base+4, l+1)
        r.write(base+64, 0x1234+i)
        r.expect_hz(name, pll_rate(i))
        r.expect_hz(PLLS[1-i][1], pll_rate(1-i))
        r.reject_write(base+12, 0x107)


def independent_lock_deadlines_require_both_output_gates(r):
    configure_all(r)
    r.write(PLLS[0][0]+56, 1)
    r.step(137)
    r.write(PLLS[1][0]+56, 1)
    r.step(4862)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.step(1)
    assert r.pll(0, "locked") and not r.pll(1, "locked")
    r.step(136)
    assert not r.pll(1, "locked")
    r.step(1)
    for i, (base, name, *_rest) in enumerate(PLLS):
        assert r.pll(i, "locked") and r.pll(i, "calibrated")
        r.expect_hz(name, 0)
        r.write(base+12, 7)
        r.expect_hz(name, 0)
        r.write(base, r.read(base) | OUTCTRL)
        r.expect_hz(name, pll_rate(i))
        r.write(base+12, 0)
        r.expect_hz(name, 0)
        r.write(base+12, 7)
        r.reject_write(base+12, 1)
        r.write(base, r.read(base) & ~OUTCTRL)
        r.expect_hz(name, 0)
        assert r.pll(i, "locked")


def recipe_and_quantized_vco_bounds_never_invent_lock(r):
    run_defaults(r)
    for i, (base, name, *_rest) in enumerate(PLLS):
        for offset in (8, 16, 32, 36):
            good = r.reg(base+offset)
            r.write(base+offset, good ^ 1)
            r.step(10_000)
            assert not r.pll(i, "locked")
            r.expect_hz(name, 0)
            r.expect_hz(PLLS[1-i][1], pll_rate(1-i))
            r.write(base+offset, good)
            r.step(LOCK_NS)
            r.expect_hz(name, pll_rate(i))
    for i in range(2):
        for l, alpha, valid in ((13, 0, True), (104, 10923, True),
                                (12, 0, False), (104, 10924, False)):
            r.reset_device()
            configure_pll(r, i)
            base = PLLS[i][0]
            r.write(base+4, l)
            r.write(base+64, alpha)
            r.write(base+56, 1)
            output_on(r, i)
            r.step(LOCK_NS)
            assert r.pll(i, "locked") is valid
            r.expect_hz(PLLS[i][1], pll_rate(i, l, alpha) if valid else 0)


def two_lucid_updates_capture_once_then_ack_and_relock(r):
    configure_all(r)
    enable_all(r)
    captured = ((0x48, 0x2345), (0x20, 0x6789))
    for i, (base, *_rest) in enumerate(PLLS):
        r.write(base + 4, captured[i][0])
        r.write(base + 64, captured[i][1])
        r.write(base, r.read(base) | UPDATE)
        r.write(base + 4, 0x30 + i)
        r.write(base + 64, 0xabcd)
        r.write(base, r.read(base) | UPDATE)
        assert (r.pll(i, "pending-l"), r.pll(i, "pending-alpha")) == captured[i]
    r.step(999)
    for i in range(2):
        assert not r.pll(i, "ack")
        r.expect_hz(PLLS[i][1], pll_rate(i))
    r.step(1)
    for i, (base, name, *_rest) in enumerate(PLLS):
        assert r.pll(i, "ack") and not r.pll(i, "locked")
        assert (r.pll(i, "active-l"), r.pll(i, "active-alpha")) == captured[i]
        r.expect_hz(name, 0)
        r.write(base, r.read(base) & ~UPDATE)
        assert not r.pll(i, "ack")
    r.step(4999)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.step(1)
    for i, row in enumerate(PLLS):
        r.expect_hz(row[1], pll_rate(i, *captured[i]))



def update_cancel_standby_and_reset_keep_the_other_pll_independent(r):
    configure_all(r)
    enable_all(r)
    for i, (base, name, *_rest) in enumerate(PLLS):
        for cancel in (UPDATE, UPDATE_BYPASS):
            r.write(base, r.read(base) | UPDATE_BYPASS)
            r.write(base + 4, PLLS[i][2] + 1)
            r.write(base, r.read(base) | UPDATE)
            r.step(500)
            r.write(base, r.read(base) & ~cancel)
            r.step(10_000)
            assert not r.pll(i, "ack") and not r.pll(i, "latch-pending")
            r.expect_hz(name, pll_rate(i))
            r.write(base, r.read(base) & ~UPDATE)
        r.write(base + 56, 0)
        assert r.pll(i, "active-l") == 0 and not r.pll(i, "locked")
        assert r.pll(i, "calibrated") is True
        r.expect_hz(PLLS[1-i][1], pll_rate(1-i))
        r.write(base + 4, PLLS[i][2])
        r.write(base + 56, 1)
        r.step(LOCK_NS)
        r.write(base, r.read(base) & ~RESET_N)
        assert not r.pll(i, "calibrated") and r.pll(i, "active-l") == 0
        assert r.pll(i, "pending-l") == 0 and r.pll(i, "lock-deadline") == -1
        r.write(base, r.read(base) | RESET_N)
        r.step(LOCK_NS)



def clock_jump_finishes_both_latches_and_relocks_without_mmio_repair(r):
    configure_all(r)
    enable_all(r)
    start = r.last_clock
    for base, _name, l, _alpha in PLLS:
        r.write(base + 4, l + 1)
        r.write(base, r.read(base) | UPDATE)
    r.set_clock(start + 20_000)
    for i, row in enumerate(PLLS):
        assert r.pll(i, "ack") and r.pll(i, "locked")
        assert r.pll(i, "latch-deadline") == r.pll(i, "lock-deadline") == -1
        r.expect_hz(row[1], pll_rate(i, row[2]+1, row[3]))



def five_rcgs_capture_cfg_once_and_cancel_without_shadow_recapture(r):
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, wait=False)
        assert r.rcg(i, "deadline") == r.last_clock + RCG_NS
        r.write(offset+4, rcg_cfg(i, 0, 3))
        r.write(offset, ROOT | BUSY)
        assert r.rcg(i, "pending-cfg") == rcg_cfg(i)
    r.step(999)
    assert all(r.rcg(i, "active-cfg") == 0 for i in range(5))
    r.step(1)
    for i, (_offset, _name, _hw, parents) in enumerate(RCGS):
        assert r.rcg(i, "active-cfg") == rcg_cfg(i)
        assert not r.rcg(i, "pending")
        expect_root(r, i, source_rate(dict(parents)[0]))
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, 0, 7, wait=False)
        r.write(offset, ROOT)
    r.step(RCG_NS)
    assert all(r.rcg(i, "active-cfg") == rcg_cfg(i) for i in range(5))


def integer_rcg_masks_distinguish_hw_control_and_have_no_mnd_words(r):
    names = {p["name"] for p in r.monitor("qom-list", {"path": DEVICE})}
    for i, (offset, _name, hw, _parents) in enumerate(RCGS):
        if hw:
            r.write(offset+4, rcg_cfg(i))
            assert r.reg(offset+4) & HW_CLK
        else:
            r.reject_write(offset+4, rcg_cfg(i) | HW_CLK)
        for field in ("active-m", "active-n", "active-d", "pending-m", "pending-n", "pending-d"):
            assert f"rcg{i}-{field}" not in names
        for delta in (8, 12, 16):
            assert offset+delta not in REGISTERS
            before = len(core_diagnostics(r.log_path.read_text(errors="replace")))
            r.read(offset+delta)
            r.write(offset+delta, 0)
            assert core_diagnostics(r.log_path.read_text(errors="replace"))[before:] == [
                (op, CC+offset+delta, 4, "(null)", "rejected") for op in ("read", "write")]


def unsupported_muxes_and_fraction_modes_never_ack(r):
    for i, (offset, _name, _hw, parents) in enumerate(RCGS):
        missing = set(range(8))-set(dict(parents))
        invalid = [rcg_cfg(i, 0, 1, mode) for mode in (1, 2, 3)]
        invalid += [rcg_cfg(i, selector) for selector in sorted(missing)]
        for cfg in invalid:
            r.write(offset, 0)
            r.write(offset+4, cfg)
            r.write(offset, ROOT | BUSY)
            r.step(2*RCG_NS)
            assert r.read(offset) & BUSY and not r.rcg(i, "pending")
            assert r.rcg(i, "active-cfg") == 0


def every_declared_mux_routes_only_its_named_carrier(r):
    run_defaults(r)
    r.fixture("core_bi_pll_test_se", 123_456_789)
    r.fixture("chip_sleep_clk", 32_003)
    for i, (_offset, _name, _hw, parents) in enumerate(RCGS):
        for selector, parent in parents:
            configure_rcg(r, i, selector, 1)
            expected = (123_456_789 if parent == "core_bi_pll_test_se" else
                        32_003 if parent == "chip_sleep_clk" else source_rate(parent))
            expect_root(r, i, expected)


def hid_floor_and_literal_v2_frequency_vectors_use_programmed_parents(r):
    run_defaults(r)
    for i, frequencies in ((1, (720_000_000, 1_014_000_000, 1_098_000_000, 1_332_000_000)),
                            (2, (840_000_000, 1_098_000_000, 1_332_000_000))):
        p = i-1
        base = PLLS[p][0]
        for hz in frequencies:
            # Match the fixed alpha helper's rounded-up Q16 multiplier.
            factor = (hz * 65536 + XO-1)//XO
            l, alpha = divmod(factor, 65536)
            r.write(base+56, 0)
            r.write(base+4, l)
            r.write(base+64, alpha)
            r.write(base+56, 1)
            r.step(LOCK_NS)
            configure_rcg(r, i, 1, 1)
            expect_root(r, i, pll_rate(p, l, alpha))
    r.fixture("core_bi_pll_test_se", 123_456_789)
    for i in range(5):
        for hid in (0, 1, 2, 3, 14, 31):
            configure_rcg(r, i, 7, hid)
            expect_root(r, i, divided(123_456_789, hid))


def unknown_sleep_and_test_inputs_never_gain_rates_from_waiting(r):
    for i in range(5):
        configure_rcg(r, i, 7)
    for offset, *_rest in BRANCHES:
        r.write(offset, 1)
    r.step(100_000)
    assert all(r.hz(n) == 0 for n in OUTPUTS)
    r.fixture("core_bi_pll_test_se", 96_000_001)
    assert all(r.hz(row[1]) == 96_000_001 for row in RCGS)
    r.fixture("core_bi_pll_test_se", 0)
    assert all(r.hz(n) == 0 for n in OUTPUTS)
    sleep = RCG_INDEX["video_cc_sleep_clk_src"]
    configure_rcg(r, sleep, 0)
    r.fixture("chip_sleep_clk", 32_003)
    expect_root(r, sleep, 32_003)
    r.expect_hz("video_cc_sleep_clk", 32_003)
    assert all(r.hz(row[1]) == 0 for i, row in enumerate(RCGS) if i != sleep)
    r.fixture("chip_sleep_clk", 0)
    assert r.read(0xf10) & OFF and r.reg(0xf10) == 1


def all_four_readonly_dividers_round_up_and_propagate_branch_demand(r):
    r.fixture("core_bi_pll_test_se", 100_000_003)
    for i in (1, 2):
        configure_rcg(r, i, 7, 1, root=False)
    for i, (offset, name, parent) in enumerate(DIVIDERS):
        branch = next(b for b in BRANCHES if b[2] == name)
        assert r.hz(parent) == 0
        for value in range(4):
            r.divider(i, value)
            assert r.read(offset) == value
            before = r.pure_snapshot()
            for written in (0, value, 3, 0xffffffff):
                r.reject_write(offset, written)
                assert r.pure_snapshot() == before
            r.write(branch[0], 1)
            expected = (100_000_003+value)//(value+1)
            r.expect_hz(parent, 100_000_003)
            r.expect_hz(name, expected)
            r.expect_hz(branch[1], expected)
            r.write(branch[0], 0)
            r.expect_hz(parent, 0)
        r.divider(i, 0)


def all_eight_branches_keep_independent_local_requests(r):
    run_defaults(r)
    r.fixture("chip_sleep_clk", 32_003)
    for index, (offset, name, parent, _voted) in enumerate(BRANCHES):
        r.write(offset, 1)
        expected = 32_003 if name == "video_cc_sleep_clk" else XO
        r.expect_hz(name, expected)
        r.expect_hz(parent, expected)
        assert not r.read(offset) & OFF
        for j, (other, other_name, *_rest) in enumerate(BRANCHES):
            assert r.reg(other) & 1 == int(j == index)
            if j != index:
                r.expect_hz(other_name, 0)
        r.write(offset, 0)
        r.expect_hz(name, 0)
        assert r.read(offset) & OFF


def four_remote_votes_or_local_requests_but_do_not_supply_parents(r):
    for i in (1, 2):
        configure_rcg(r, i, 7, root=False)
    for vote, branch_index in enumerate(VOTED):
        offset, name, _parent, _voted = BRANCHES[branch_index]
        r.vote(vote, True)
        assert r.reg(offset) == 0 and r.read(offset) & OFF
        r.step(10_000)
        r.expect_hz(name, 0)
        r.fixture("core_bi_pll_test_se", 100_000_003)
        r.expect_hz(name, 100_000_003)
        r.write(offset, 1)
        r.vote(vote, False)
        r.expect_hz(name, 100_000_003)
        r.write(offset, 0)
        r.expect_hz(name, 0)
        r.fixture("core_bi_pll_test_se", 0)


def remote_votes_never_become_local_or_sibling_requests(r):
    for vote, selected in enumerate(VOTED):
        r.vote(vote, True)
        assert [r.qom(f"remote-vote{i}") for i in range(4)] == [i == vote for i in range(4)]
        for i, (offset, name, *_rest) in enumerate(BRANCHES):
            assert r.reg(offset) & 1 == 0
            r.expect_hz(name, XO if i == selected else 0)
        r.vote(vote, False)
    assert all(r.hz(n) == 0 for n in OUTPUTS)


def shared_branch_ares_words_preserve_control_bits_independently(r):
    run_defaults(r)
    domains = r.video_snapshot()
    for offset, reset_id in ((0xc34, 2), (0xcd4, 5)):
        name = next(row[1] for row in BRANCHES if row[0] == offset)
        for value in (0, 1, 5, 4, 0, 4, 5, 1, 0):
            r.write(offset, value)
            assert r.reg(offset) == value
            assert r.read(offset) == value | (0 if value & 1 else OFF)
            r.expect_hz(name, XO if value & 1 else 0)
            r.reset_lines([i == reset_id and bool(value & ARES) for i in range(7)])
            assert r.video_snapshot() == domains
        # These are the driver's masked read-modify-write sequences.
        r.write(offset, r.read(offset) | 1)
        reset_bit(r, reset_id, True)
        assert r.reg(offset) == 5
        r.write(offset, r.read(offset) & ~1)
        assert r.reg(offset) == 4
        reset_bit(r, reset_id, False)
        assert r.reg(offset) == 0
        for bad in (2, 7, 8, 0x10000001):
            r.reject_write(offset, bad)
    r.reset_lines([False]*7)


def all_seven_reset_lines_leave_plls_clocks_and_gdsc_owners_independent(r):
    run_defaults(r)
    for offset, *_rest in BRANCHES:
        r.write(offset, 1)
    before_hz = {n: r.hz(n) for n in OUTPUTS}
    domains = r.video_snapshot()
    for i, (offset, mask) in enumerate(RESETS):
        previous = r.reg(offset)
        reset_bit(r, i, True)
        assert r.reg(offset) == previous | mask
        r.reset_lines([j == i for j in range(7)])
        assert {n: r.hz(n) for n in OUTPUTS} == before_hz
        assert r.video_snapshot() == domains
        reset_bit(r, i, False)
        assert r.reg(offset) == previous & ~mask
    r.reset_lines([False]*7, True)


def readonly_feedback_cannot_forge_or_erase_actual_status(r):
    configure_all(r)
    for base, *_rest in PLLS:
        r.write(base, r.reg(base) | LOCK | ACK | PCAL)
        assert not r.read(base) & (LOCK | ACK | PCAL)
    for offset, *_rest in RCGS + BRANCHES:
        r.write(offset, OFF)
        assert r.read(offset) == OFF and r.reg(offset) == 0
    enable_all(r)
    for i, (base, name, *_rest) in enumerate(PLLS):
        r.write(base, r.read(base) & ~(LOCK | ACK | PCAL))
        assert r.read(base) & (LOCK | PCAL) == LOCK | PCAL
        r.expect_hz(name, pll_rate(i))


def reserved_bits_narrow_accesses_and_real_holes_reject_atomically(r):
    base = PLLS[0][0]
    for offset, value in ((base+4, 0x10000), (base+8, 0x10000),
                          (base+64, 0x10000), (base+56, 2), (0xbd8, 1 << 21),
                          (0xe54, 2), (0xe58, 2), (0xc34, 2), (0xcd4, 2)):
        r.reject_write(offset, value)
    before_state = r.pure_snapshot()
    for op in ("read", "write"):
        for width in ("b", "w"):
            count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
            if op == "read":
                r.read_absolute(CC+base, width)
            else:
                r.write_absolute(CC+base, 0xffffffff, width)
            new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
            assert len(new) == 1 and new[0][0] == op and "invalid size" in new[0][-1]
        count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
        if op == "read":
            r.read_absolute(CC+base+1)
        else:
            r.write_absolute(CC+base+1, 0xffffffff)
        new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
        assert new and all(item[0] == op and "invalid size" in item[-1] for item in new)
    holes = (0, 0x428, 0x45c, 0x460, 0x470, 0x800, 0x804, 0x814,
             0xb9c, 0xbbc, 0xbdc, 0xbfc, 0xc30, 0xc38, 0xc9c,
             0xcd0, 0xcd8, 0xd1c, 0xd9c, 0xed4, 0xef8, 0xf14, 0xf4c, 0xfffc)
    assert not set(holes) & (set(REGISTERS) | set(GDSC_OFFSETS))
    count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    expected = []
    for offset in holes:
        r.read(offset)
        r.write(offset, 0xffffffff)
        expected.extend((op, CC+offset, 4, "(null)", "rejected") for op in ("read", "write"))
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[count:] == expected
    assert r.pure_snapshot() == before_state


def system_reset_cancels_work_but_retains_external_inputs_and_votes(r):
    run_defaults(r)
    r.fixture("core_bi_pll_test_se", 96_000_001)
    r.fixture("chip_sleep_clk", 32_003)
    for base, _name, l, _alpha in PLLS:
        r.write(base+4, l+1)
        r.write(base, r.read(base) | UPDATE)
    for i in range(5):
        configure_rcg(r, i, 7, wait=False)
    for i in range(7):
        reset_bit(r, i, True)
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
    for i in VOTED:
        r.expect_hz(BRANCHES[i][1], XO)
    for i in range(4):
        r.vote(i, False)
    for name in OUTPUTS:
        r.expect_hz(name, 0)


def pure_getters_and_fixtures_protect_connected_sources_and_types(r):
    before = r.pure_snapshot()
    for prop, value in [(n, r.qom(n)) for n in GLOBAL_FIELDS] + [
            ("reg.0c34", 0), ("pll0-active-l", 0), ("rcg0-active-cfg", 0),
            ("divider0-value", 0), ("remote-vote0", False)]:
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": prop, "value": value})
        assert "error" in response and "not writable" in response["error"]["desc"]
    for name in INPUTS:
        for value in (-1, 1 << 32, "19200000", True):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": value})
            assert "error" in response
    for name in INPUTS[:2]:
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": 1})
        assert "error" in response and "connected" in response["error"]["desc"]
    for name in INPUTS[2:]:
        for value in range(4_294_967_292, 4_294_967_296):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": value})
            assert "error" in response and r.period(name) == 0
        r.fixture(name, 4_294_967_291)
        assert r.period(name) == 1_000_000_001
        assert r.qom("qtest-"+name+"-hz") == 4_294_967_291
        r.fixture(name, 0)
    for i in range(4):
        for value in (-1, 4, "1", True):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": f"qtest-divider{i}-value", "value": value})
            assert "error" in response
    assert r.pure_snapshot() == before


def real_rpmh_xo_votes_revoke_both_evidenced_carriers(r):
    assert r.has_rpmh and all(r.period(n) == 0 for n in INPUTS)
    configure_all(r)
    r.write(0xe58, 1)
    r.write(0xeec, 1)
    r.step(20_000)
    assert not any(r.pll(i, "locked") for i in range(2))
    assert r.read(0xe58) & OFF and r.read(0xeec) & OFF
    r.run_one("xo.lvl", 1)
    assert all(r.period(n) == PERIOD_UNIT//XO for n in INPUTS[:2])
    enable_all(r)
    r.expect_hz("video_cc_ahb_clk", XO)
    r.expect_hz("video_cc_xo_clk", XO)
    r.run_one("xo.lvl", 0)
    assert all(r.period(n) == 0 for n in INPUTS)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.expect_hz("video_cc_ahb_clk", 0)
    r.expect_hz("video_cc_xo_clk", 0)
    r.run_one("xo.lvl", 1)
    for base, *_rest in PLLS:
        r.write(base+56, 0)
        r.write(base+56, 1)
    r.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        r.expect_hz(row[1], pll_rate(i))
    assert all(r.period(n) == 0 for n in INPUTS[2:])
    return {"real_xo_resource": "xo.lvl", "connected_inputs": list(INPUTS[:2]),
            "unknown_sleep_rate_invented": False}


ORDINARY = [(test, {}) for test in (
    cold_inventory_has_only_two_evidenced_inputs,
    literal_v2_probe_overwrites_pll1_base_recipe_but_skips_zero_writes,
    cold_critical_ahb_and_xo_require_requests_and_the_ao_provider,
    only_the_v2_pll1_recipe_can_lock_and_banks_are_independent,
    independent_lock_deadlines_require_both_output_gates,
    recipe_and_quantized_vco_bounds_never_invent_lock,
    two_lucid_updates_capture_once_then_ack_and_relock,
    update_cancel_standby_and_reset_keep_the_other_pll_independent,
    clock_jump_finishes_both_latches_and_relocks_without_mmio_repair,
    five_rcgs_capture_cfg_once_and_cancel_without_shadow_recapture,
    integer_rcg_masks_distinguish_hw_control_and_have_no_mnd_words,
    unsupported_muxes_and_fraction_modes_never_ack,
    every_declared_mux_routes_only_its_named_carrier,
    hid_floor_and_literal_v2_frequency_vectors_use_programmed_parents,
    unknown_sleep_and_test_inputs_never_gain_rates_from_waiting,
    all_four_readonly_dividers_round_up_and_propagate_branch_demand,
    all_eight_branches_keep_independent_local_requests,
    four_remote_votes_or_local_requests_but_do_not_supply_parents,
    remote_votes_never_become_local_or_sibling_requests,
    readonly_feedback_cannot_forge_or_erase_actual_status,
    reserved_bits_narrow_accesses_and_real_holes_reject_atomically,
    system_reset_cancels_work_but_retains_external_inputs_and_votes,
    pure_getters_and_fixtures_protect_connected_sources_and_types,
)] + [(shared_branch_ares_words_preserve_control_bits_independently, {"video": True}),
     (all_seven_reset_lines_leave_plls_clocks_and_gdsc_owners_independent, {"video": True}),
     (real_rpmh_xo_votes_revoke_both_evidenced_carriers, {"rpmh": True})]

def default_profile_rejects_all_words_even_with_sibling_profiles_enabled(args, out):
    for legacy in (False, True):
        rig = VideoCCRig(args, out/("default-legacy" if legacy else "default-alone"),
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
                response = rig.raw_monitor("qom-set", {"path": DEVICE,
                    "property": f"qtest-divider{i}-value", "value": 3})
                assert "error" in response
            rig.step(20_000)
            assert all(rig.reg(o) == 0 for o in REGISTERS)
            assert not any(rig.pll(i, "locked") for i in range(2))
            for name in OUTPUTS:
                rig.expect_hz(name, 0)
        finally:
            rig.close()


def paused_tcg_has_no_mutating_fixtures_and_executes_no_guest(args, out):
    rig = VideoCCRig(args, out/"paused-tcg", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        before = rig.pure_snapshot(include_clock_periods=False)
        names = {p["name"] for p in rig.monitor("qom-list", {"path": DEVICE})}
        assert not any(n.startswith("qtest-") for n in names)
        for name in ["qtest-"+n+"-hz" for n in INPUTS] + [f"qtest-divider{i}-value" for i in range(4)]:
            response = rig.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": 1})
            assert "error" in response and "not found" in response["error"]["desc"]
        assert rig.pure_snapshot(include_clock_periods=False) == before
        assert rig.cont_commands == rig.device_mmio_operations == 0
        assert rig.monitor("query-status")["running"] is False
    finally:
        rig.close()


def exact_26_leaves_preserve_all_four_enabled_video_gdsc_owners(args, out):
    rig = VideoCCRig(args, out/"sparse-and-video-gdsc-owners", video=True)
    try:
        tree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory/"mtree.txt").write_text(tree)
        matches = re.findall(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ([^\s]+)", tree)
        actual = {(int(a, 16)-CC, int(b, 16)-int(a, 16)+1)
                  for a, b, name in matches if name.startswith(TYPE+".")}
        assert actual == set(WINDOWS) and sum(n for _, n in actual) == 220
        for offset, name in zip(GDSC_OFFSETS, GDSC_NAMES):
            ranges = {(int(a, 16), int(b, 16)) for a, b, label in matches
                      if label == "qcom-video-gdsc."+name+"-control"}
            assert ranges == {(CC+offset, CC+offset+3)}
        before = rig.pure_snapshot()
        for i, offset in enumerate(GDSC_OFFSETS):
            assert rig.read(offset) == 1
            value = ((i+2)<<12) | 0x801
            rig.write(offset, value)
            assert rig.read(offset) == value
        domains = rig.video_snapshot()
        assert [d["control"] for d in domains["domains"]] == [0x2801, 0x3801, 0x4801, 0x5801]
        assert rig.pure_snapshot() == before
        run_defaults(rig)
        for i in range(7):
            reset_bit(rig, i, True)
        assert rig.video_snapshot() == domains
        assert rig.qom("reference-identity", VIDEO) == 0x56444731
    finally:
        rig.close()


def videocc_payload(snapshot):
    """Independent complete v1 wire; no output Clock or nonexistent MND state."""
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
                                  ("video_gdsc", "qcom-video-gdsc", video_payload(video))):
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
    write_json(rig.directory/"source-before-save.json", {"videocc": snapshot, "video_gdsc": video})
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
            "snapshot": snapshot, "video_gdsc": video, "sections": sections,
            "ram_witness": witness, "stream_modified": False}


def prewarm_target(rig, saved):
    # Stopped incoming timers do not dispatch; preparatory reads below are
    # explicit and occur before migrate-incoming and its no-MMIO witness.
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
        rig.fixture(name, 70_000_001+i)
    for i in range(4):
        rig.divider(i, 3)
        rig.vote(i, not saved["snapshot"]["remote-votes"][i])
    for offset, *_rest in BRANCHES:
        rig.write(offset, 1)
    for i in range(7):
        reset_bit(rig, i, True)
    for base, _name, l, _alpha in PLLS:
        rig.write(base+4, l+3)
        rig.write(base, rig.read(base) | UPDATE)
    for i in range(5):
        configure_rcg(rig, i, 7, 7, wait=False)
    for offset in GDSC_OFFSETS:
        rig.write(offset, 0x7801)
    assert rig.pure_snapshot() != saved["snapshot"]
    assert rig.video_snapshot() != saved["video_gdsc"]


def receive_source(rig, path, saved):
    rig.set_clock(saved["source_virtual_ns"])
    mmio, cont = rig.device_mmio_operations, rig.cont_commands
    rig.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(rig)
    assert received["status"] == "completed"
    assert rig.monitor("query-status")["running"] is False
    first, video = rig.pure_snapshot(), rig.video_snapshot()
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert video == saved["video_gdsc"]
    assert (rig.device_mmio_operations, rig.cont_commands) == (mmio, cont)
    assert rig.raw_read(RAM) == saved["ram_witness"]
    rig.reset_lines([bool(first["registers"][f"{offset:04x}"] & mask) for offset, mask in RESETS])
    evidence = {"snapshot": first, "video_gdsc": video,
                "device_mmio_count_before_incoming": mmio,
                "device_mmio_count_after_first_observation": rig.device_mmio_operations,
                "device_mmio_since_incoming": False, "qmp_cont_since_incoming": False,
                "guest_cpu_executed": False, "ram_witness": saved["ram_witness"],
                "query_migrate": received}
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
        rig = VideoCCRig(args, out/name, video=True, **options)
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
            "all_474_payload_bytes_verified": True, "video_gdsc_142_bytes_verified": True, **details})
        passed(name, device_mmio_before_first_observation=False, **details)

    try:
        steady = new("migration-steady-source")
        steady.step(100_000)
        run_defaults(steady)
        steady.fixture("core_bi_pll_test_se", 111_111_113)
        steady.fixture("chip_sleep_clk", 32_003)
        for i in range(5):
            configure_rcg(steady, i, 7, 3+i)
        for i, (offset, *_rest) in enumerate(BRANCHES):
            steady.write(offset, i & 1)
        for i in range(4):
            steady.divider(i, i)
            steady.vote(i, i in (0, 2))
        # MVS0 local bit0 is 1; use MVS1_DIV2 (local 0) to test remote loss.
        reset_bit(steady, 2, True)
        reset_bit(steady, 4, True)
        reset_bit(steady, 5, True)
        for i, offset in enumerate(GDSC_OFFSETS):
            steady.write(offset, ((i+2)<<12) | 0x801)
        state_path = out/"videocc-v1-steady.migration"
        saved_state = save_source(steady, state_path, 0x56494331)
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
        assert restored.video_snapshot() == saved_state["video_gdsc"]
        completed("v1_steady_clocks_ares_dividers_votes_and_gdsc_replace_stale_target",
                  state_path, steady, restored, first, stale_target_deadlines_discarded=True,
                  clock_and_vote_changes_propagate_after_restore=True)

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
        reset_bit(locking, 0, True)
        locking.step(400)
        assert all(locking.pll(i, "lock-deadline") == start+5000 for i in range(2))
        assert all(locking.rcg(i, "deadline") == start+1000 for i in range(5))
        lock_path = out/"videocc-v1-two-locks-five-rcgs.migration"
        saved_lock = save_source(locking, lock_path, 0x56494332)
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
        assert resumed.video_snapshot() == saved_lock["video_gdsc"]
        completed("v1_two_locks_and_five_rcg_deadlines_resume_without_mmio_repair",
                  lock_path, locking, resumed, first, remaining_rcg_ns=600,
                  remaining_pll_ns=4600, device_mmio_before_deadline_checks=False)

        latching = new("migration-two-latches-five-cfg-source")
        latching.step(100_000)
        run_defaults(latching)
        latching.fixture("core_bi_pll_test_se", 123_456_789)
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
            configure_rcg(latching, i, 7, 14, wait=False)
            latching.write(offset+4, rcg_cfg(i, 0, 1))
            latching.write(offset, ROOT | BUSY)
        reset_bit(latching, 2, True)
        reset_bit(latching, 5, True)
        for i, offset in enumerate(GDSC_OFFSETS):
            latching.write(offset, ((i+4)<<12) | 0x801)
        latching.step(400)
        latch_path = out/"videocc-v1-two-latches-five-cfg.migration"
        saved_latch = save_source(latching, latch_path, 0x56494333)
        report["migration_sources"].append(saved_latch)
        target = new("migration-two-latches-five-cfg-target", incoming=True)
        prewarm_target(target, saved_latch)
        first = receive_source(target, latch_path, saved_latch)
        assert target.period("chip_sleep_clk") == 0
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
        for i in range(5):
            assert target.rcg(i, "active-cfg") == rcg_cfg(i, 7, 14)
            assert target.reg(RCGS[i][0]+4) == rcg_cfg(i, 0, 1)
            target.expect_hz(RCGS[i][1], divided(123_456_789, 14))
        target.step(4999)
        assert not any(target.pll(i, "locked") for i in range(2))
        target.step(1)
        for i, row in enumerate(PLLS):
            target.expect_hz(row[1], pll_rate(i, *captured[i]))
        assert target.device_mmio_operations == mmio
        assert target.video_snapshot() == saved_latch["video_gdsc"]
        # A restored unknown sleep source must replace a warmed target carrier.
        sleep = RCG_INDEX["video_cc_sleep_clk_src"]
        configure_rcg(target, sleep, 0)
        target.write(0xf10, 1)
        target.expect_hz("video_cc_sleep_clk", 0)
        target.fixture("chip_sleep_clk", 32_003)
        target.expect_hz("video_cc_sleep_clk", 32_003)
        completed("v1_two_latches_and_five_cfg_captures_preserve_original_deadlines",
                  latch_path, latching, target, first, remaining_latch_ns=600,
                  device_mmio_before_deadline_checks=False,
                  unknown_source_input_replaces_prewarmed_carrier=True)

        disabled = new("migration-default-source", reference=None)
        disabled.fixture("chip_sleep_clk", 32_003)
        disabled.vote(3, True)
        disabled_path = out/"videocc-v1-default.migration"
        saved_disabled = save_source(disabled, disabled_path, 0x56494330)
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
        assert saved_state["snapshot"]["registers"]["0c34"] == 4
        for name, relative, width, old, xor, diagnostic in (
                ("physical_base", 52, 8, CC, 4, f"{CC:x} != {CC ^ 4:x}"),
                ("shared_branch_reserved_bit", 60+4*shared_index, 4, 4, 2,
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
        "physical_base": CC, "resource_bytes": RESOURCE_BYTES, "register_count": 55,
        "sparse_window_count": 26, "mapped_bytes": 220,
        "output_clock_count": 19, "input_clock_count": 4,
        "connected_input_clock_count": 2, "unknown_external_input_count": 2,
        "pll_count": 2, "lucid_pll_count": 2, "postdivider_count": 0,
        "rcg_count": 5, "mnd_rcg_count": 0, "hw_control_rcg_count": 3,
        "branch_count": 8, "divider_count": 4, "writable_divider_count": 0,
        "read_only_divider_count": 4, "reset_count": 7, "remote_vote_count": 4,
        "shared_branch_ares_word_count": 2, "modeled_timer_count": 1,
        "video_gdsc_vmstate_version": 1, "video_gdsc_vmstate_payload_bytes": 142,
        "video_gdsc_domain_count": 4, "reference_enabled_by_default": False,
        "pll1_v2_fixup_replayed": True, "ares_implies_clock_gating_assumed": False,
        "generic_dividers_round_up": True,
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "actual_compatible": "qcom,videocc-kona-v2",
        "source_sha256": {
            "videocc-kona.c": SOURCE_SHA256, "qcom,videocc-kona.h": BINDING_SHA256,
            "clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
            "clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
            "clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
            "clk-branch.h": "da132bd1a093a650abf64073a4e695719e0612b67bb0166726737e0f2a3332d5",
            "reset.c": "db88de0848c75cecff57623b94d63993a8f869a27686df4bd7b7be0765317f11",
            "clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
            "clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
            "clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2"},
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
        assert len(ORDINARY) == 26
        assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(INPUTS), len(PLLS),
                len(RCGS), len(BRANCHES), len(DIVIDERS), len(RESETS)) == (
                    55, 26, 19, 4, 2, 5, 8, 4, 7)
        for index, (test, options) in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = VideoCCRig(args, fixture, **options)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_profile_rejects_all_words_even_with_sibling_profiles_enabled,
                     paused_tcg_has_no_mutating_fixtures_and_executes_no_guest,
                     exact_26_leaves_preserve_all_four_enabled_video_gdsc_owners):
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
                item["video_gdsc_142_bytes_verified"] and
                item["first_target_observation"]["device_mmio_since_incoming"] is False and
                item["first_target_observation"]["qmp_cont_since_incoming"] is False
                for item in report["migrations"]) and
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
