#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled SM8250 non-v2 DP 7nm digital-reference acceptance.

Independent literal vectors follow public R11 98719830. Vendor CCF numerical
rates are kHz; an inferred cold CCF value does not make a physical Clock known.
This suite does not execute a guest CPU or establish analog/link/Android success.
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
from test_camera_gdsc import GLOBAL_FIELDS as GDSC_GLOBAL_FIELDS
from test_camera_gdsc import DOMAIN_FIELDS as GDSC_DOMAIN_FIELDS
from test_mdss_gdsc import mdss_payload
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

# Physical resources are from the exact original full DT. The non-v2 source
# graph and wire contract are pinned independently of the generated model table.
TYPE, DEVICE = "qcom-sm8250-dp-pll", "/machine/dp-pll"
MDSS, DISPCC = "/machine/mdss-gdsc", "/machine/dispcc"
BASE, PHY, TX0, TX1 = 0x088ea000, 0xa00, 0x200, 0x600
MDSS_BASE, DISPCC_BASE, RAM = 0x0af03000, 0x0af00000, 0x80010000
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
XO, PERIOD_UNIT = 19_200_000, 1_000_000_000 << 32
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_COMMIT = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
OUTPUTS = ("dp_vco_clk", "dp_link_clk_divsel_ten", "dp_vco_divsel_two_clk_src",
           "dp_vco_divsel_four_clk_src", "dp_vco_divsel_six_clk_src",
           "dp_vco_divided_clk_src_mux")
CARRIERS = ("dp_link_clk_divsel_ten", "dp_vco_divided_clk_src_mux")

# Explicit per-rate rows from dp_vco_pll_init_db_7nm(). These are software kHz
# choices for HSCLK, not measured analog VCO rates. Keep zero-valued writes.
RATE_FIELDS = (0x158, 0x0bc, 0x0cc, 0x0d0, 0x0d4, 0x0ac, 0x0b0, 0x0a4, 0xa70)
RATE_ROWS = {
    1_620_000: (5, 0x69, 0, 0x80, 7, 0x6f, 0x08, 4, 1),
    2_700_000: (3, 0x69, 0, 0x80, 7, 0x0f, 0x0e, 8, 1),
    5_400_000: (1, 0x8c, 0, 0x00, 10, 0x1f, 0x1c, 8, 2),
    8_100_000: (0, 0x69, 0, 0x80, 7, 0x2f, 0x2a, 8, 0),
}
COMMON_PLL_BEFORE = (
    (0x184, 5), (0x094, 0x3b), (0x04c, 2), (0x048, 0x0c),
    (0x050, 6), (0x154, 0x30), (0x058, 0x0f), (0x084, 0x36),
    (0x07c, 0x16), (0x074, 6),
)
COMMON_PLL_AFTER = (
    (0x17c, 2), (0x0ec, 0x3f), (0x0f0, 0), (0x10c, 0),
    (0x00c, 0x0a), (0x168, 0x0a), (0x108, 0), (0x044, 0x17), (0x174, 0x1f),
)
TX_WRITES = ((0x0e8, 0x40), (0x020, 0x30), (0x02c, 0x3b), (0x008, 0x0f),
             (0x01c, 3), (0x0b8, 0x0f), (0x060, 0), (0x0bc, 0),
             (0x03c, 0x11), (0x040, 0x11), (0x024, 4))
START_PHY_WRITES = (1, 5, 1, 9)
STATUS_OFFSETS = (0x140, 0x178, 0xadc)


def period(hz):
    return PERIOD_UNIT//hz if hz else 0


def observed(hz):
    return PERIOD_UNIT//period(hz) if hz else 0


def cold_ccf_hsclk_khz(hsclk_sel, aux_cfg2):
    """Pure source recalc interpretation; never an availability/LOCK oracle."""
    divide = {5: 5, 3: 3, 1: 2, 0: 1}.get(hsclk_sel & 15, 5)
    if ((aux_cfg2 >> 2) & 3) == 2:
        return 2_700_000
    return {5: 1_620_000, 3: 2_700_000, 2: 5_400_000, 1: 8_100_000}[divide]


def mux_divisor(raw):
    # Source regmap callbacks encode parent indices 0/1/2 as raw 1/2/0.
    # Raw 3 has no source-defined parent; it must not silently select one.
    return {1: 2, 2: 4, 0: 6}.get(raw & 3)


def programming(hsclk_khz, lanes=4, orientation=1):
    """Literal set-rate sequence; SPARE0 is supplied by the real PHY caller."""
    assert hsclk_khz in RATE_ROWS and lanes in (1, 2, 4) and orientation in (1, 2)
    pd = 0x7d if lanes == 4 else (0x6d if orientation == 2 else 0x75)
    writes = [(PHY+0xc8, (orientation << 4) | lanes), (PHY+0x14, 15), (PHY+0x18, pd)]
    writes += list(COMMON_PLL_BEFORE) + list(zip(RATE_FIELDS, RATE_ROWS[hsclk_khz]))
    writes += list(COMMON_PLL_AFTER)
    writes += [(PHY+0x1c, 0x4c if orientation == 2 else 0x5c),
               (PHY+0x24, 0x13), (PHY+0x28, 0xa4)]
    for tx, lane_control in ((TX0, 0x78), (TX1, 0x9c)):
        writes.append((PHY+lane_control, 5))
        writes += [(tx+offset, value) for offset, value in TX_WRITES]
    return writes


IDENTITY, VMSTATE_BYTES, EXPECTED_CHECKS = 0x44503731, 791, 36
PLL_NS, PHY_NS, LATE_PHY_NS = 3334, 3334, 2_000_000
FIRST_QOM_COUNT = 304
REGION_LAYOUT = (("pll", 0x000, BASE), ("phy", 0xa00, BASE+0xa00),
                 ("tx0", 0x200, BASE+0x200), ("tx1", 0x600, BASE+0x600))
PLL_REGS = (0x00c, 0x044, 0x048, 0x04c, 0x050, 0x058, 0x074, 0x07c, 0x084,
            0x094, 0x09c, 0x0a4, 0x0ac, 0x0b0, 0x0bc, 0x0cc, 0x0d0, 0x0d4,
            0x0ec, 0x0f0, 0x108, 0x10c, 0x140, 0x154, 0x158, 0x168, 0x174,
            0x178, 0x17c, 0x184)
PHY_REGS = (0x010, 0x014, 0x018, 0x01c, 0x024, 0x028, 0x070, 0x078,
            0x09c, 0x0c8, 0x0dc)
TX_REGS = (0x008, 0x00c, 0x014, 0x01c, 0x020, 0x024, 0x02c, 0x03c,
           0x040, 0x054, 0x058, 0x05c, 0x060, 0x0b8, 0x0bc, 0x0e8)
REGISTERS = PLL_REGS + tuple(PHY+o for o in PHY_REGS) + tuple(TX0+o for o in TX_REGS) + tuple(TX1+o for o in TX_REGS)
REQUIRED = tuple(o for o in REGISTERS if o in dict(programming(1_620_000)))
TX_NONQUALIFYING = tuple(base+o for base in (TX0, TX1) for o in (0x00c, 0x014, 0x054, 0x058, 0x05c))
GLOBAL_FIELDS = ("enabled-profile", "reference-identity", "region-count", "register-count",
                 "sparse-window-count", "mapped-bytes", "output-clock-count", "input-clock-count",
                 "timer-count", "ccf-rate-scale", "pll-delay-xo-cycles", "phy-delay-xo-cycles",
                 "resource-bytes-per-region")
RUNTIME_FIELDS = ("power-known", "power-on", "phase", "sequence-index", "deadline-ns",
                  "captured-xo-period", "last-xo-period", "captured-rate-khz", "pll-locked",
                  "phy-ready", "pll-pending", "phy-pending", "late-phy-pending", "recipe-valid",
                  "raw-rate-known", "raw-ccf-rate-khz", "lane-count", "orientation")


def register_key(offset):
    for name, start, _ in REGION_LAYOUT:
        if start <= offset < start+0x200:
            return f"{name}.{offset-start:03x}"
    raise AssertionError(("not a DP physical resource", offset))


def expected_leaves():
    leaves = []
    for _, start, physical in REGION_LAYOUT:
        offsets = sorted(o-start for o in REGISTERS if start <= o < start+0x200)
        for offset in offsets:
            if leaves and leaves[-1][1]+1 == physical+offset:
                leaves[-1] = (leaves[-1][0], physical+offset+3)
            else:
                leaves.append((physical+offset, physical+offset+3))
    return tuple(leaves)


assert len(REGISTERS) == len(set(REGISTERS)) == 73
assert len(REQUIRED) == 58 and len(expected_leaves()) == 42


class DpRig(CameraRig):
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", mdss=True, dispcc=True, rpmh=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh, self.accel = bool(reference), bool(rpmh), accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.qom_get_commands = self.camera_mmio_operations = self.dp_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="dppll-"))
        self.log_path = self.directory/"qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory/"qtest.jsonl").open("w")
        self.qmplog = (self.directory/"qmp.jsonl").open("w")
        fields = [f for f in args.machine.split(",") if not f.startswith(
            ("rpmh-profile=", "rpmh-boot-configured=", "rpmh-cmd-db="))]
        fields += [f"rpmh-profile={'reference' if rpmh else 'off'}", "rpmh-boot-configured=off"]
        command = [args.qemu, "-machine", ",".join(fields), "-accel", accel, "-m", "128M",
                   "-smp", "8", "-display", "none", "-monitor", "none", "-serial", "null",
                   "-S", "-d", "unimp,guest_errors,invalid_mem", "-qmp",
                   f"unix:{self.socket_directory}/m,server=on,wait=off",
                   "-global", f"qcom-mdss-gdsc.enabled-reference={'on' if mdss else 'off'}",
                   "-global", f"qcom-sm8250-dispcc.enabled-reference={'on' if dispcc else 'off'}"]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if accel == "qtest":
            command += ["-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
                        "-qtest-log", "/dev/null"]
        if incoming:
            command += ["-incoming", "defer"]
        write_json(self.directory/"command.json", command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qmp = connect_unix(self.socket_directory/"m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self._log(self.qmplog, {"receive": greeting})
            assert "QMP" in greeting
            self.monitor("qmp_capabilities")
            if accel == "qtest":
                self.qtest = connect_unix(self.socket_directory/"t", self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile("rwb", buffering=0)
                if not incoming:
                    self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, command):
        parts = command.split()
        if parts and parts[0] in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq"):
            address = int(parts[1], 0)
            if BASE <= address < BASE+0xc00:
                self.dp_mmio_operations += 1
                self.device_mmio_operations += 1
            elif DISPCC_BASE <= address < DISPCC_BASE+0x20000:
                self.device_mmio_operations += 1
        return super().command(command)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def raw_monitor(self, execute, arguments=None):
        if execute == "qom-get":
            self.qom_get_commands += 1
        return super().raw_monitor(execute, arguments)

    def reg(self, offset, prefix="reg"):
        return self.qom(f"{prefix}.{register_key(offset)}")

    def read(self, offset):
        return self.raw_read(BASE+offset)

    def write(self, offset, value):
        self.raw_write(BASE+offset, value)

    def dread(self, offset, drv=2):
        return self.raw_read(RPMH_BASE+drv*0x10000+offset)

    def dwrite(self, offset, value, drv=2):
        self.raw_write(RPMH_BASE+drv*0x10000+offset, value)

    def step(self, ns):
        old = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == old+ns
        return self.last_clock

    def clock_period(self, name, path=DEVICE):
        return self.qom("qtest-clock-period", path+"/"+name)

    def expect_rate(self, name, hz):
        expected = period(hz)
        assert self.qom("clk."+name+".period") == expected, (name, hz)
        assert self.clock_period(name) == expected, (name, hz)
        assert self.qom("clk."+name+".hz") == observed(hz), (name, hz)

    def supply(self, known=True, on=True, collapse=False):
        assert not self.has_rpmh, "Do not override a connected real RPMh provider"
        self.command(f"set_irq_in {MDSS} parent-on 0 {int(on)}")
        self.command(f"set_irq_in {MDSS} parent-known 0 {int(known)}")
        self.raw_write(MDSS_BASE, int(collapse))
        assert self.qom("power-known") == self.qom("domain0.state-known", MDSS)
        assert self.qom("power-on") == self.qom("domain0.powered", MDSS)


    def pure_snapshot(self, *, child_periods=True):
        result = {
            "globals": {n: self.qom(n) for n in GLOBAL_FIELDS},
            "region_bases": [self.qom(f"region{i}.physical-base") for i in range(4)],
            "registers": {register_key(o): self.reg(o) for o in REGISTERS},
            "written": {register_key(o): self.reg(o, "written") for o in REGISTERS},
            "captured": {register_key(o): self.reg(o, "captured") for o in REGISTERS},
            "runtime": {n: self.qom(n) for n in RUNTIME_FIELDS},
            "input_period": self.qom("input.ref_clk_src.period"),
            "input_hz": self.qom("input.ref_clk_src.hz"),
            "outputs": {n: self.qom("clk."+n+".period") for n in OUTPUTS},
            "output_hz": {n: self.qom("clk."+n+".hz") for n in OUTPUTS},
        }
        if child_periods:
            result["physical_outputs"] = {n: self.clock_period(n) for n in OUTPUTS}
            result["physical_input_period"] = self.clock_period("ref_clk_src")
            assert result["physical_outputs"] == result["outputs"]
            assert result["physical_input_period"] == result["input_period"]
        return result

    def world(self):
        return {"dp": self.pure_snapshot(), "mdss": self.mdss_snapshot(),
                "dispcc_carrier_periods": {n: self.clock_period(n, DISPCC) for n in CARRIERS},
                "dispcc_roots": {n: {"period": self.clock_period(n, DISPCC),
                    "hz": self.qom("clk."+n+".hz", DISPCC)} for n in
                    ("disp_cc_mdss_dp_link_clk_src", "disp_cc_mdss_dp_pixel_clk_src")}}

    def mdss_snapshot(self):
        return {"enabled-reference": self.qom("enabled-reference", MDSS),
                "globals": {n: self.qom(n, MDSS) for n in GDSC_GLOBAL_FIELDS},
                "parent-known": self.qom("parent-known", MDSS),
                "parent-on": self.qom("parent-on", MDSS),
                "domains": [{n: self.qom("domain0."+n, MDSS) for n in GDSC_DOMAIN_FIELDS}]}


def configure(r, hsclk_khz, *, lanes=4, orientation=1, omit=()):
    for offset, value in programming(hsclk_khz, lanes, orientation):
        if offset not in omit:
            r.write(offset, value)


def start_pll(r):
    for value in START_PHY_WRITES:
        r.write(PHY+0x10, value)
    r.write(0x09c, 0x20)


def expect_outputs(r, hsclk_khz=0, *, locked=False, ready=False, mux=None):
    hs = hsclk_khz*1000
    rates = [0]*6
    if locked:
        rates[0], rates[2], rates[3], rates[4] = hs, hs//2, hs//4, hs//6
    if ready:
        assert locked
        rates[1] = hs//10
        divisor = mux_divisor(r.reg(PHY+0x70) if mux is None else mux)
        rates[5] = hs//divisor if divisor else 0
    for name, hz in zip(OUTPUTS, rates):
        r.expect_rate(name, hz)
    for name in CARRIERS:
        assert r.clock_period(name, DISPCC) == period(rates[OUTPUTS.index(name)])


def expect_no_capture(r, *, sequence=None):
    assert r.qom("phase") == 0 and r.qom("deadline-ns") == -1
    assert r.qom("captured-rate-khz") == r.qom("captured-xo-period") == 0
    assert not any(r.reg(o, "captured") for o in REGISTERS)
    assert all(not r.qom(n) for n in ("pll-locked", "phy-ready", "pll-pending",
                                     "phy-pending", "late-phy-pending"))
    if sequence is not None:
        assert r.qom("sequence-index") == sequence
    expect_outputs(r)


def expect_status(r, *, locked, ready):
    assert r.read(0x178) == int(locked)
    assert r.read(0x140) == (3 if locked else 0)
    assert r.read(PHY+0xdc) == (3 if ready else 0)
    assert all(r.reg(o) == 0 and not r.reg(o, "written") for o in STATUS_OFFSETS)


def run_reference(r, rate=5_400_000, *, lanes=4, orientation=1):
    assert r.qom("power-known") and r.qom("power-on")
    configure(r, rate, lanes=lanes, orientation=orientation)
    assert r.qom("recipe-valid")
    start_pll(r)
    assert r.qom("phase") == 1 and r.qom("deadline-ns") == r.last_clock+PLL_NS
    r.step(PLL_NS)
    assert r.qom("phase") == 2 and r.qom("deadline-ns") == -1
    expect_outputs(r, rate, locked=True)
    r.write(PHY+0x10, 0x19)
    assert r.qom("phase") == 3 and r.qom("deadline-ns") == r.last_clock+PHY_NS
    r.step(PHY_NS)
    assert r.qom("phase") == 4 and r.qom("deadline-ns") == -1
    expect_outputs(r, rate, locked=True, ready=True)
    expect_status(r, locked=True, ready=True)


def rejected_access(r, offset, *, value=0, write=True, width="l", reason="disabled-or-invalid-access"):
    key = register_key(offset)
    region, relative = key.split(".")
    op, size = ("write" if write else "read"), {"b": 1, "w": 2, "l": 4, "q": 8}[width]
    line = f"{TYPE}: rejected {op} {region}+0x{relative} size {size} ({reason})"
    old = r.diagnostic_count(line)
    command = f"{op}{width} {BASE+offset:#x}"+(f" {value:#x}" if write else "")
    r.command(command)
    assert r.diagnostic_count(line) == old+1, line


def cold_identity_probe_reads_and_pure_observers(r):
    expected = (1, IDENTITY, 4, 73, 42, 292, 6, 1, 1, 1000, 64, 64, 512)
    assert tuple(r.qom(n) for n in GLOBAL_FIELDS) == expected
    assert [r.qom(f"region{i}.physical-base") for i in range(4)] == [x[2] for x in REGION_LAYOUT]
    assert r.clock_period("ref_clk_src") == period(XO)
    before = r.world()
    count = r.qom_get_commands
    assert r.world() == before and r.qom_get_commands-count == FIRST_QOM_COUNT
    assert not any(before["dp"]["registers"].values())
    assert not any(before["dp"]["written"].values())
    # Actual fault instruction is LDR W8,[X8] at PLL+0x158. Source next reads
    # PHY+0x28 without first checking LOCK. Cold nominal rate is not a carrier.
    assert r.read(0x158) == r.read(PHY+0x28) == 0
    assert r.qom("raw-rate-known") and r.qom("raw-ccf-rate-khz") == 8_100_000
    assert cold_ccf_hsclk_khz(0, 0) == 8_100_000
    expect_no_capture(r, sequence=0)
    expect_status(r, locked=False, ready=False)
    assert r.world() == before
    return {"actual_fault_read_width": 4, "cold_recalc_is_not_a_carrier": True,
            "pure_world_qom_get_count": FIRST_QOM_COUNT}


def four_rates_all_lane_orientation_literal_sequences(r):
    vectors = []
    for rate in RATE_ROWS:
        for lanes in (1, 2, 4):
            for orientation in (1, 2):
                r.supply(on=False)
                r.supply()
                run_reference(r, rate, lanes=lanes, orientation=orientation)
                assert r.qom("lane-count") == lanes and r.qom("orientation") == orientation
                assert r.qom("captured-rate-khz") == r.qom("raw-ccf-rate-khz") == rate
                assert all(r.reg(o) == v for o, v in programming(rate, lanes, orientation))
                assert all(r.reg(o, "written") for o in REQUIRED)
                vectors.append({"hsclk_khz": rate, "lanes": lanes, "orientation": orientation})
    return {"literal_configurations": vectors, "configuration_count": 24,
            "high_hsclk_uses_64_bit_clock_period": True}


def explicit_required_zero_writes_are_not_reset_defaults(r):
    for omitted in (0x0cc, 0x0f0, 0x10c, TX0+0xbc):
        r.supply(on=False)
        r.supply()
        configure(r, 1_620_000, omit=(omitted,))
        assert r.reg(omitted) == 0 and not r.reg(omitted, "written")
        assert not r.qom("recipe-valid")
        start_pll(r)
        r.step(10_000)
        expect_no_capture(r, sequence=0)
        r.write(omitted, 0)
        assert r.qom("recipe-valid")
        start_pll(r)
        r.step(PLL_NS)
        assert r.qom("pll-locked")


def unprogrammed_start_does_not_fabricate_lock(r):
    r.supply()
    start_pll(r)
    r.write(PHY+0x10, 0x19)
    r.step(100_000)
    expect_no_capture(r, sequence=0)
    expect_status(r, locked=False, ready=False)


def pll_deadline_separates_internal_views_from_final_carriers(r):
    r.supply()
    configure(r, 8_100_000)
    start_pll(r)
    deadline = r.qom("deadline-ns")
    assert deadline == r.last_clock+PLL_NS
    expect_outputs(r)
    r.step(PLL_NS-1)
    assert r.qom("phase") == 1 and not r.qom("pll-locked")
    expect_status(r, locked=False, ready=False)
    r.step(1)
    assert r.qom("phase") == 2 and r.qom("deadline-ns") == -1
    expect_status(r, locked=True, ready=False)
    expect_outputs(r, 8_100_000, locked=True)


def phy_deadline_is_required_after_pll_lock(r):
    r.supply()
    configure(r, 2_700_000)
    start_pll(r)
    r.step(PLL_NS)
    r.write(PHY+0x10, 0x19)
    assert r.qom("deadline-ns") == r.last_clock+PHY_NS and r.qom("phase") == 3
    expect_outputs(r, 2_700_000, locked=True)
    r.step(PHY_NS-1)
    expect_status(r, locked=True, ready=False)
    r.step(1)
    expect_status(r, locked=True, ready=True)
    expect_outputs(r, 2_700_000, locked=True, ready=True)


def skipped_or_early_enable_commands_cancel_qualification(r):
    r.supply()
    configure(r, 5_400_000)
    for sequence in ((9,), (1, 9), (1, 5, 9), (5, 1, 9)):
        r.write(0x09c, 0)
        for value in sequence:
            r.write(PHY+0x10, value)
        r.write(0x09c, 0x20)
        r.step(PLL_NS+PHY_NS)
        expect_no_capture(r, sequence=0)
    start_pll(r)
    assert r.qom("phase") == 1
    r.write(PHY+0x10, 0x19)
    r.step(PLL_NS+PHY_NS)
    expect_no_capture(r, sequence=0)


def pending_recipe_change_cancels_without_late_resurrection(r):
    r.supply()
    configure(r, 5_400_000)
    start_pll(r)
    r.step(1000)
    r.write(0x0bc, 0x8d)
    expect_no_capture(r, sequence=0)
    r.write(0x0bc, 0x8c)
    r.step(10_000)
    expect_no_capture(r, sequence=0)
    start_pll(r)
    r.step(PLL_NS)
    assert r.qom("phase") == 2


def live_required_change_revokes_but_same_value_preserves_capture(r):
    r.supply()
    run_reference(r)
    before = r.pure_snapshot()
    for offset in REQUIRED:
        r.write(offset, r.reg(offset))
    assert r.pure_snapshot() == before
    r.write(TX0+0x40, 0x10)
    expect_no_capture(r, sequence=0)
    r.write(TX0+0x40, 0x11)
    r.step(10_000)
    expect_no_capture(r, sequence=0)


def mdss_power_loss_clears_written_but_retains_raw(r):
    r.supply()
    run_reference(r)
    raw = {o: r.reg(o) for o in REGISTERS}
    r.supply(on=False)
    assert not r.qom("power-on") and r.qom("power-known")
    assert {o: r.reg(o) for o in REGISTERS} == raw
    assert not any(r.reg(o, "written") for o in REGISTERS)
    expect_no_capture(r, sequence=0)
    r.supply()
    start_pll(r)
    r.step(10_000)
    expect_no_capture(r, sequence=0)
    run_reference(r)
    r.supply(known=False, on=True)
    assert not r.qom("power-known") and not r.qom("power-on")
    assert not any(r.reg(o, "written") for o in REGISTERS)
    expect_no_capture(r, sequence=0)


def unknown_or_off_power_programming_cannot_qualify(r):
    for known, on in ((False, True), (True, False)):
        r.supply(known=known, on=on)
        configure(r, 2_700_000)
        assert not any(r.reg(o, "written") for o in REQUIRED)
        assert not r.qom("recipe-valid")
        start_pll(r)
        r.step(10_000)
        expect_no_capture(r, sequence=0)
        r.supply()
        start_pll(r)
        r.step(10_000)
        expect_no_capture(r, sequence=0)


def real_rpmh_xo_votes_revoke_pending_and_ready_captures(r):
    assert r.has_rpmh and r.clock_period("ref_clk_src") == 0
    assert r.resource("mmcx.lvl", "present") == r.resource("xo.lvl", "present") == 1
    r.run_one("mmcx.lvl", 1)
    r.raw_write(MDSS_BASE, 0)
    assert r.qom("power-known") and r.qom("power-on")
    r.run_one("xo.lvl", 1)
    assert r.clock_period("ref_clk_src") == period(XO)
    configure(r, 5_400_000)
    start_pll(r)
    deadline = r.qom("deadline-ns")
    assert r.qom("phase") == 1 and deadline == r.last_clock+PLL_NS
    r.run_one("xo.lvl", 0)  # The fixed helper advances exactly 1000 ns.
    assert r.last_clock < deadline
    expect_no_capture(r, sequence=0)
    assert r.clock_period("ref_clk_src") == r.qom("last-xo-period") == 0
    assert r.qom("recipe-valid") and r.qom("power-on") and r.resource("mmcx.lvl") == 1
    r.run_one("xo.lvl", 1)
    r.step(10_000)
    expect_no_capture(r, sequence=0)
    run_reference(r)
    r.run_one("xo.lvl", 0)
    expect_no_capture(r, sequence=0)
    assert r.qom("power-known") and r.qom("power-on")
    r.run_one("xo.lvl", 1)
    run_reference(r)
    r.write(PHY+0x10, 0x18)
    deadline = r.qom("deadline-ns")
    assert r.qom("phase") == 5
    r.run_one("xo.lvl", 0)
    assert r.last_clock < deadline
    expect_no_capture(r, sequence=0)
    r.run_one("xo.lvl", 1)
    r.step(LATE_PHY_NS)
    expect_no_capture(r, sequence=0)
    run_reference(r)
    return {"runtime_xo_loss_exercised": True, "cancelled_phases": [1, 4, 5],
            "actual_xo_resource": "xo.lvl", "actual_supply_resource": "mmcx.lvl",
            "mdss_remained_on": True, "new_enable_sequence_required": True}


def live_pixel_mux_all_encodings_and_high_carrier_boundary(r):
    r.supply()
    run_reference(r, 8_100_000)
    captured = {o: r.reg(o, "captured") for o in REGISTERS}
    for mux in (1, 2, 0, 3, 1):
        r.write(PHY+0x70, mux)
        assert r.qom("phase") == 4 and r.qom("recipe-valid")
        assert {o: r.reg(o, "captured") for o in REGISTERS} == captured
        expect_outputs(r, 8_100_000, locked=True, ready=True, mux=mux)
    assert r.clock_period(OUTPUTS[5]) == period(4_050_000_000)
    assert r.qom("clk."+OUTPUTS[0]+".hz") == observed(8_100_000_000) > 0xffffffff
    return {"maximum_tested_pixel_carrier_hz": 4_050_000_000,
            "maximum_tested_hsclk_hz": 8_100_000_000, "unknown_mux_has_zero_carrier": True}


def raw_ccf_units_and_unsupported_selectors_do_not_grant_clocks(r):
    r.supply()
    for hs, rate in ((5, 1_620_000), (3, 2_700_000), (1, 5_400_000), (0, 8_100_000)):
        r.write(0x158, hs)
        r.write(PHY+0x28, 0xa4)
        assert r.qom("raw-rate-known") and r.qom("raw-ccf-rate-khz") == rate
        assert cold_ccf_hsclk_khz(hs, 0xa4) == rate
        expect_outputs(r)
        r.write(PHY+0x28, 0xa8)
        assert r.qom("raw-rate-known") and r.qom("raw-ccf-rate-khz") == 2_700_000
    for hs, aux in ((2, 0xa4), (15, 0xa4), (0, 0xac)):
        r.write(0x158, hs)
        r.write(PHY+0x28, aux)
        assert not r.qom("raw-rate-known") and r.qom("raw-ccf-rate-khz") == 0
        expect_outputs(r)
    return {"ccf_numerical_scale_to_physical_hz": 1000,
            "software_fallback_never_establishes_physical_availability": True}


def tx_aliases_are_single_words_and_banks_are_independent(r):
    r.supply()
    properties = [p["name"] for p in r.monitor("qom-list", {"path": DEVICE})]
    for tx, other in ((TX0, TX1), (TX1, TX0)):
        for offset, value in ((0xb8, 0x0a), (0xbc, 0x05), (0xe8, 0x40)):
            previous = r.reg(other+offset)
            r.raw_write(BASE+tx+offset, value)
            assert r.read(tx+offset) == r.reg(tx+offset) == value
            assert r.reg(other+offset) == previous
            assert properties.count("reg."+register_key(tx+offset)) == 1
    return {"physical_alias_offsets_per_tx": [0xb8, 0xbc, 0xe8], "separate_alias_storage": False}


def catalog_tx_storage_does_not_retune_or_revoke_capture(r):
    r.supply()
    run_reference(r)
    captured = {o: r.reg(o, "captured") for o in REGISTERS}
    for i, offset in enumerate(TX_NONQUALIFYING):
        r.write(offset, 0x10+i)
        assert r.reg(offset) == 0x10+i and r.reg(offset, "written")
    assert {o: r.reg(o, "captured") for o in REGISTERS} == captured
    assert r.qom("phase") == 4 and r.qom("recipe-valid")
    expect_outputs(r, 5_400_000, locked=True, ready=True)


def readonly_status_and_reserved_bits_reject_atomically(r):
    r.supply()
    run_reference(r)
    before = r.pure_snapshot()
    for offset in STATUS_OFFSETS:
        for value in (0, 1, 0xffffffff):
            rejected_access(r, offset, value=value, reason="read-only-status")
    for offset in REGISTERS:
        if offset not in STATUS_OFFSETS:
            rejected_access(r, offset, value=r.reg(offset) | 0x100,
                            reason="unsupported-bits")
    assert r.pure_snapshot() == before
    expect_status(r, locked=True, ready=True)


def exact_sparse_leaves_holes_widths_and_shared_owner(r):
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory/"mtree.txt").write_text(tree)
    matches = re.findall(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ([^\s]+)", tree)
    actual = {(int(a, 16), int(b, 16)) for a, b, name in matches
              if re.fullmatch(re.escape(TYPE)+r"\.(pll|phy|tx0|tx1)\.[0-9a-f]{3}", name)}
    assert actual == set(expected_leaves()) and sum(b-a+1 for a, b in actual) == 292
    mdss = {(int(a, 16), int(b, 16)) for a, b, name in matches
            if name == "qcom-mdss-gdsc.mdss-core-control"}
    assert mdss == {(MDSS_BASE, MDSS_BASE+3)}
    holes = (0, 0x10, 0x40, 0x188, 0x1fc, TX0, TX0+0x10, TX0+0xc0,
             TX0+0x1fc, TX1, TX1+0xec, PHY, PHY+0x20, PHY+0x2c, PHY+0xe0, PHY+0x1fc)
    before = r.world()
    old = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    addresses = [BASE+offset for offset in holes]+[0x088e8000, MDSS_BASE+4]
    expected = []
    for address in addresses:
        r.raw_read(address)
        r.raw_write(address, 0xffffffff)
        expected += [(op, address, 4, "(null)", "rejected") for op in ("read", "write")]
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[old:] == expected
    # Entire byte/halfword/unaligned/64-bit requests fit the 0x044..0x053 leaf.
    # Callbacks must reject the actual width rather than split valid words.
    for write in (False, True):
        for width in ("b", "w", "q"):
            rejected_access(r, 0x044, write=write, width=width, value=0)
        rejected_access(r, 0x045, write=write, value=0)
    assert r.world() == before
    # A 64-bit operation at an isolated 4-byte leaf legitimately commits its
    # low word, then fails at the hole. No transaction-wide rollback claim.
    old = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    r.command(f"writeq {BASE+0x00c:#x} 0xdeadbeef0000000a")
    assert r.reg(0x00c) == 0x0a
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[old:] == [
        ("write", BASE+0x010, 4, "(null)", "rejected")]
    return {"exact_leaf_count": 42, "exact_mapped_bytes": 292,
            "mdss_owner_preserved": True, "cross_leaf_partial_write_verified": True}


def connect_dispcc_roots(r):
    # R11 selector 1=non-v2 link, selector 2=non-v2 mux; HID=1 is divide-by-1.
    for command, selector, name, carrier in (
            (0x2178, 1, "disp_cc_mdss_dp_link_clk_src", CARRIERS[0]),
            (0x21ac, 2, "disp_cc_mdss_dp_pixel_clk_src", CARRIERS[1])):
        r.raw_write(DISPCC_BASE+command+4, (selector << 8) | 1)
        r.raw_write(DISPCC_BASE+command, 3)
        r.step(1000)
        assert not r.raw_read(DISPCC_BASE+command) & 1
        carrier_hz = r.qom("clk."+carrier+".hz")
        assert r.qom("clk."+name+".hz", DISPCC) == observed(carrier_hz)
        assert r.clock_period(name, DISPCC) == period(carrier_hz)


def two_real_dispcc_carriers_use_source_names_and_selectors(r):
    r.supply()
    run_reference(r, 8_100_000)
    r.write(PHY+0x70, 1)
    expect_outputs(r, 8_100_000, locked=True, ready=True)
    for name in CARRIERS:
        assert r.clock_period(name, DISPCC) == r.clock_period(name)
        response = r.raw_monitor("qom-set", {"path": DISPCC, "property": "qtest-"+name+"-hz", "value": 1})
        assert "error" in response and "connected" in response["error"]["desc"]
    for name in ("dptx1_phy_pll_link_clk", "dptx1_phy_pll_vco_div_clk",
                 "dptx2_phy_pll_link_clk", "dptx2_phy_pll_vco_div_clk"):
        assert r.clock_period(name, DISPCC) == 0
    connect_dispcc_roots(r)
    r.write(PHY+0x18, 2)
    expect_no_capture(r, sequence=0)
    assert all(r.clock_period(n, DISPCC) == 0 for n in
               ("disp_cc_mdss_dp_link_clk_src", "disp_cc_mdss_dp_pixel_clk_src"))


def connected_reference_and_state_observers_are_readonly(r):
    before = r.pure_snapshot()
    properties = [(n, r.qom(n)) for n in GLOBAL_FIELDS+RUNTIME_FIELDS]+[
        ("reg.pll.158", 0), ("written.pll.158", False), ("captured.pll.158", 0),
        ("region0.physical-base", BASE), ("input.ref_clk_src.period", period(XO)),
        ("input.ref_clk_src.hz", XO), ("clk.dp_vco_clk.period", 0), ("clk.dp_vco_clk.hz", 0)]
    for name, value in properties:
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in response and "not writable" in response["error"]["desc"]
    names = {p["name"] for p in r.monitor("qom-list", {"path": DEVICE})}
    assert "qtest-ref_clk_src-hz" not in names
    response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-ref_clk_src-hz", "value": 0})
    assert "error" in response and "not found" in response["error"]["desc"]
    assert r.pure_snapshot() == before


def system_reset_cancels_transaction_and_replays_mdss_collapse(r):
    r.supply()
    configure(r, 5_400_000)
    start_pll(r)
    assert r.qom("phase") == 1
    xo = r.clock_period("ref_clk_src")
    r.events.clear()
    r.monitor("system_reset")
    r.wait_for(lambda: r.reg(0x158) == 0 and r.qom("phase") == 0, "DP reset witnesses")
    assert any(e.get("event") == "RESET" for e in r.events)
    assert all(r.reg(o) == 0 and not r.reg(o, "written") for o in REGISTERS)
    assert r.clock_period("ref_clk_src") == xo
    assert r.qom("domain0.control", MDSS) == 1
    assert r.qom("power-known") == r.qom("domain0.state-known", MDSS)
    assert r.qom("power-on") == r.qom("domain0.powered", MDSS) is False
    r.step(10_000)
    expect_no_capture(r, sequence=0)


def late_phy_wait_enforces_two_ms_and_rejects_early_restart(r):
    r.supply()
    run_reference(r)
    captured = {o: r.reg(o, "captured") for o in REGISTERS}
    r.write(PHY+0x10, 0x18)
    assert r.qom("phase") == 5 and r.qom("deadline-ns") == r.last_clock+LATE_PHY_NS
    expect_outputs(r, 5_400_000, locked=True)
    assert {o: r.reg(o, "captured") for o in REGISTERS} == captured
    r.step(LATE_PHY_NS-1)
    assert r.qom("phase") == 5
    r.write(PHY+0x10, 0x19)
    expect_no_capture(r, sequence=0)
    r.step(1+PHY_NS)
    expect_no_capture(r, sequence=0)
    run_reference(r)
    r.write(PHY+0x10, 0x18)
    r.step(LATE_PHY_NS-1)
    assert r.qom("phase") == 5
    r.step(1)
    assert r.qom("phase") == 2 and r.qom("deadline-ns") == -1
    expect_outputs(r, 5_400_000, locked=True)
    r.write(PHY+0x10, 0x19)
    r.step(PHY_NS-1)
    assert not r.qom("phy-ready")
    r.step(1)
    expect_outputs(r, 5_400_000, locked=True, ready=True)


def invalid_lane_orientation_and_mixed_rate_recipe_never_lock(r):
    r.supply()
    for offset, value in ((PHY+0xc8, 0x13), (PHY+0xc8, 0x04),
                          (PHY+0xc8, 0x34), (PHY+0x18, 0x75),
                          (PHY+0x1c, 0x4c), (0x158, 3), (0x0bc, 0x69)):
        configure(r, 5_400_000)
        r.write(offset, value)
        assert not r.qom("recipe-valid"), (offset, value)
        start_pll(r)
        r.step(PLL_NS+PHY_NS)
        expect_no_capture(r, sequence=0)


def polling_cannot_advance_or_replace_the_finite_timer(r):
    r.supply()
    configure(r, 1_620_000)
    start_pll(r)
    before, now = r.pure_snapshot(), r.last_clock
    for _ in range(8):
        expect_status(r, locked=False, ready=False)
        assert r.pure_snapshot() == before
    assert r.last_clock == now
    r.step(PLL_NS-1)
    assert not r.qom("pll-locked")
    r.step(1)
    assert r.qom("pll-locked")


ORDINARY = (
    cold_identity_probe_reads_and_pure_observers,
    four_rates_all_lane_orientation_literal_sequences,
    explicit_required_zero_writes_are_not_reset_defaults,
    unprogrammed_start_does_not_fabricate_lock,
    pll_deadline_separates_internal_views_from_final_carriers,
    phy_deadline_is_required_after_pll_lock,
    skipped_or_early_enable_commands_cancel_qualification,
    pending_recipe_change_cancels_without_late_resurrection,
    live_required_change_revokes_but_same_value_preserves_capture,
    mdss_power_loss_clears_written_but_retains_raw,
    unknown_or_off_power_programming_cannot_qualify,
    real_rpmh_xo_votes_revoke_pending_and_ready_captures,
    live_pixel_mux_all_encodings_and_high_carrier_boundary,
    raw_ccf_units_and_unsupported_selectors_do_not_grant_clocks,
    tx_aliases_are_single_words_and_banks_are_independent,
    catalog_tx_storage_does_not_retune_or_revoke_capture,
    readonly_status_and_reserved_bits_reject_atomically,
    exact_sparse_leaves_holes_widths_and_shared_owner,
    two_real_dispcc_carriers_use_source_names_and_selectors,
    connected_reference_and_state_observers_are_readonly,
    system_reset_cancels_transaction_and_replays_mdss_collapse,
    late_phy_wait_enforces_two_ms_and_rejects_early_restart,
    invalid_lane_orientation_and_mixed_rate_recipe_never_lock,
    polling_cannot_advance_or_replace_the_finite_timer,
)


def default_off_rejects_all_words_with_sibling_profiles_enabled(args, out):
    r = DpRig(args, out/"default-off", reference=None)
    try:
        assert not r.qom("enabled-reference") and r.qom("enabled-profile") == 0
        r.supply()
        before = r.world()
        for offset in REGISTERS:
            rejected_access(r, offset, write=False)
            rejected_access(r, offset, value=0)
        r.step(10_000)
        assert r.world() == before
        assert set(before["dispcc_carrier_periods"].values()) == {0}
        assert r.qom("enabled-reference", MDSS) and r.qom("enabled-reference", DISPCC)
    finally:
        r.close()


def paused_tcg_has_no_mutating_fixture_or_guest_execution(args, out):
    r = DpRig(args, out/"paused-tcg", accel="tcg")
    try:
        assert not r.monitor("query-status")["running"]
        before = r.pure_snapshot(child_periods=False)
        names = {p["name"] for p in r.monitor("qom-list", {"path": DEVICE})}
        assert "qtest-ref_clk_src-hz" not in names
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-ref_clk_src-hz", "value": XO})
        assert "error" in response and "not found" in response["error"]["desc"]
        assert r.pure_snapshot(child_periods=False) == before
        assert r.cont_commands == r.device_mmio_operations == 0
        assert not r.monitor("query-status")["running"]
    finally:
        r.close()


def dp_payload(snapshot):
    """Independent exact 791-byte VMState v1; output Clocks are derived."""
    raw = bytearray(struct.pack(">13I4Q", *(snapshot["globals"][n] for n in GLOBAL_FIELDS),
                                *snapshot["region_bases"]))
    for group, code in (("registers", "I"), ("written", "B"), ("captured", "I")):
        values = [snapshot[group][register_key(o)] for o in REGISTERS]
        if group == "written":
            assert all(type(value) is bool for value in values)
        raw.extend(struct.pack(">73"+code, *values))
    state = snapshot["runtime"]
    raw.extend(struct.pack(">QQqIIQ", state["captured-xo-period"], state["last-xo-period"],
                           state["deadline-ns"], state["phase"], state["sequence-index"],
                           state["captured-rate-khz"]))
    assert type(state["power-known"]) is type(state["power-on"]) is bool
    raw.extend(bytes((state["power-known"], state["power-on"])))
    raw.extend(struct.pack(">Q", snapshot["input_period"]))
    assert len(raw) == VMSTATE_BYTES
    return bytes(raw)


def inspect_stream(path, world):
    data, sections = path.read_bytes(), {}
    for label, name, expected in (("dp", TYPE, dp_payload(world["dp"])),
                                  ("mdss", "qcom-mdss-gdsc", mdss_payload(world["mdss"]))):
        section = unique_section(data, name, 1, len(expected))
        offset = section["payload_offset"]
        actual = data[offset:offset+len(expected)]
        assert actual == expected, (label, [i for i, (a, b) in enumerate(zip(actual, expected)) if a != b][:32])
        payload_path = path.with_name(path.name+f".{label}-v1.payload.bin")
        payload_path.write_bytes(actual)
        section.update(payload_file=payload_path.name, payload_sha256=digest(payload_path),
                       all_bytes_match_stopped_source_qom=True)
        sections[label] = section
    write_json(path.with_name(path.name+".sections.json"), sections)
    return sections


def save_source(r, path, witness):
    r.raw_write(RAM, witness)
    r.monitor("stop")
    assert r.monitor("query-status")["running"] is False
    snapshot = r.world()
    assert snapshot["dp"]["runtime"]["power-known"] == snapshot["mdss"]["domains"][0]["state-known"]
    assert snapshot["dp"]["runtime"]["power-on"] == snapshot["mdss"]["domains"][0]["powered"]
    write_json(r.directory/"source-before-save.json", snapshot)
    r.monitor("migrate", file_channels(path))
    sent = wait_migration(r)
    assert sent["status"] == "completed"
    assert r.monitor("query-status")["status"] == "postmigrate"
    after = r.world()
    assert after == snapshot
    write_json(r.directory/"source-after-save.json", after)
    write_json(r.directory/"source-query-migrate.json", sent)
    sections = inspect_stream(path, snapshot)
    return {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
            "source_directory": r.directory.name, "source_virtual_ns": r.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "sections": sections, "ram_witness": witness,
            "stream_modified": False}


def receive_source(r, path, saved):
    r.set_clock(saved["source_virtual_ns"])
    mmio, cont = r.device_mmio_operations, r.cont_commands
    r.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(r)
    assert received["status"] == "completed"
    assert r.monitor("query-status")["running"] is False
    qom_before = r.qom_get_commands
    first = r.world()
    qom_after = r.qom_get_commands
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert (r.device_mmio_operations, r.cont_commands) == (mmio, cont)
    assert qom_after-qom_before == FIRST_QOM_COUNT
    evidence = {"snapshot": first, "query_migrate": received,
                "device_mmio_count_before_incoming": mmio,
                "device_mmio_count_after_first_observation": r.device_mmio_operations,
                "qmp_cont_count_before_incoming": cont,
                "qmp_cont_count_after_first_observation": r.cont_commands,
                "qom_get_count_before_first_observation": qom_before,
                "qom_get_count_after_first_observation": qom_after,
                "first_observation_qom_get_count": qom_after-qom_before,
                "device_mmio_since_incoming": False, "qmp_cont_since_incoming": False,
                "guest_cpu_executed": False, "shared_log_timestamp_claimed": False}
    # The RAM witness read occurs only after the complete pure first snapshot.
    assert r.raw_read(RAM) == saved["ram_witness"]
    evidence["ram_witness"] = saved["ram_witness"]
    write_json(r.directory/"destination-before-first-device-mmio.json", evidence)
    return evidence


def prewarm_target(r, saved):
    # Use the real MDSS provider and its already-connected outputs. A dirty
    # target must not get its power state from a disconnected DP GPIO fixture.
    r.set_clock(saved["source_virtual_ns"])
    r.supply()
    r.raw_write(MDSS_BASE, 0x1800)
    configure(r, 1_620_000, lanes=1, orientation=2)
    start_pll(r)
    assert r.qom("phase") == 1
    r.raw_write(RAM, saved["ram_witness"] ^ 0xffffffff)
    stale = r.world()
    assert stale != saved["snapshot"] and stale["mdss"] != saved["snapshot"]["mdss"]
    write_json(r.directory/"destination-before-incoming.json", {
        "snapshot": stale, "device_mmio_count": r.device_mmio_operations,
        "qmp_cont_count": r.cont_commands, "qom_get_count": r.qom_get_commands,
        "source_epoch_ns": saved["source_virtual_ns"], "stale_pending_phase": 1})


def load_failure(r, path, diagnostic):
    error = None
    try:
        r.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as exc:
        error = str(exc)
    code = r.process.wait(timeout=20)
    stderr = r.log_path.read_text(errors="replace")
    assert code == 1, (code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {"destination_exit": code, "expected_diagnostic": diagnostic,
            "specific_load_failure_observed": True, "command_error": error,
            "target_directory": r.directory.name}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **options):
        rig = DpRig(args, out/name, **options)
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
            "all_791_payload_bytes_verified": True, "mdss_64_bytes_verified": True,
            "actual_two_dispcc_input_periods_restored": True, **details})
        passed(name, first_observation_mmio_delta=0, first_observation_cont_delta=0, **details)

    try:
        steady = new("migration-steady-source")
        steady.set_clock(100_000)
        steady.supply()
        run_reference(steady, 8_100_000)
        steady.write(PHY+0x70, 1)
        connect_dispcc_roots(steady)
        steady_path = out/"dp-steady.migration"
        saved_steady = save_source(steady, steady_path, 0x44503034)
        assert saved_steady["snapshot"]["dp"]["runtime"]["phase"] == 4
        report["migration_sources"].append(saved_steady)
        steady.close()
        target = new("migration-steady-target", incoming=True)
        prewarm_target(target, saved_steady)
        first = receive_source(target, steady_path, saved_steady)
        target.monitor("cont")
        expect_outputs(target, 8_100_000, locked=True, ready=True, mux=1)
        expect_status(target, locked=True, ready=True)
        target.write(PHY+0x18, 2)
        expect_no_capture(target, sequence=0)
        assert all(target.clock_period(n, DISPCC) == 0 for n in
                   ("disp_cc_mdss_dp_link_clk_src", "disp_cc_mdss_dp_pixel_clk_src"))
        completed("migration_steady_high_carriers", steady_path, steady, target, first,
                  high_hsclk_and_two_actual_dispcc_roots_restored=True,
                  post_restore_powerdown_revoked_carriers=True)
        target.close()

        pll = new("migration-pll-pending-source")
        pll.supply()
        configure(pll, 5_400_000, lanes=2, orientation=2)
        pll.set_clock(200_000)
        start_pll(pll)
        pll.step(1000)
        pll_path = out/"dp-pll-pending.migration"
        saved_pll = save_source(pll, pll_path, 0x44503031)
        assert saved_pll["snapshot"]["dp"]["runtime"]["phase"] == 1
        assert saved_pll["snapshot"]["dp"]["runtime"]["deadline-ns"] == 203_334
        report["migration_sources"].append(saved_pll)
        pll.close()
        target = new("migration-pll-pending-target", incoming=True)
        prewarm_target(target, saved_pll)
        first = receive_source(target, pll_path, saved_pll)
        target.monitor("cont")
        target.step(2333)
        assert target.qom("phase") == 1
        expect_outputs(target)
        target.step(1)
        assert target.qom("phase") == 2 and target.qom("deadline-ns") == -1
        expect_outputs(target, 5_400_000, locked=True)
        target.write(PHY+0x10, 0x19)
        target.step(PHY_NS)
        expect_outputs(target, 5_400_000, locked=True, ready=True)
        completed("migration_pending_pll", pll_path, pll, target, first,
                  pll_completed_after_ns=2334, phy_required_new_command_after_restore=True)
        target.close()

        phy = new("migration-phy-pending-source")
        phy.supply()
        configure(phy, 2_700_000, lanes=1, orientation=1)
        phy.set_clock(300_000)
        start_pll(phy)
        phy.step(PLL_NS)
        phy.write(PHY+0x10, 0x19)
        phy.step(1000)
        phy_path = out/"dp-phy-pending.migration"
        saved_phy = save_source(phy, phy_path, 0x44503033)
        assert saved_phy["snapshot"]["dp"]["runtime"]["phase"] == 3
        assert saved_phy["snapshot"]["dp"]["runtime"]["deadline-ns"] == 306_668
        report["migration_sources"].append(saved_phy)
        phy.close()
        target = new("migration-phy-pending-target", incoming=True)
        prewarm_target(target, saved_phy)
        first = receive_source(target, phy_path, saved_phy)
        target.monitor("cont")
        target.step(2333)
        assert target.qom("phase") == 3
        expect_outputs(target, 2_700_000, locked=True)
        target.step(1)
        assert target.qom("phase") == 4
        expect_outputs(target, 2_700_000, locked=True, ready=True)
        target.supply(on=False)
        expect_no_capture(target, sequence=0)
        assert not any(target.reg(o, "written") for o in REGISTERS)
        completed("migration_pending_phy", phy_path, phy, target, first,
                  phy_completed_after_ns=2334, actual_mdss_power_loss_revoked_capture=True)
        target.close()

        late = new("migration-late-phy-wait-source")
        late.supply()
        run_reference(late, 5_400_000)
        late.set_clock(400_000)
        late.write(PHY+0x10, 0x18)
        late.step(500_000)
        late_path = out/"dp-late-phy-wait.migration"
        saved_late = save_source(late, late_path, 0x44503035)
        assert saved_late["snapshot"]["dp"]["runtime"]["phase"] == 5
        assert saved_late["snapshot"]["dp"]["runtime"]["deadline-ns"] == 2_400_000
        report["migration_sources"].append(saved_late)
        late.close()
        target = new("migration-late-phy-wait-target", incoming=True)
        prewarm_target(target, saved_late)
        first = receive_source(target, late_path, saved_late)
        target.monitor("cont")
        target.step(1_499_999)
        assert target.qom("phase") == 5
        expect_outputs(target, 5_400_000, locked=True)
        target.step(1)
        assert target.qom("phase") == 2 and target.qom("deadline-ns") == -1
        expect_outputs(target, 5_400_000, locked=True)
        target.write(PHY+0x10, 0x19)
        target.step(PHY_NS-1)
        assert target.qom("phase") == 3
        target.step(1)
        expect_outputs(target, 5_400_000, locked=True, ready=True)
        completed("migration_late_phy_wait", late_path, late, target, first,
                  late_wait_completed_after_ns=1_500_000,
                  new_phy_command_and_delay_required=True)
        target.close()

        disabled = new("migration-disabled-source", reference=False)
        disabled.supply()
        disabled.set_clock(1_000_000)
        off_path = out/"dp-disabled.migration"
        saved_off = save_source(disabled, off_path, 0x44503030)
        report["migration_sources"].append(saved_off)
        disabled.close()
        for name, path, saved, reference, diagnostic in (
                ("on_to_off", steady_path, saved_steady, False, "0 != 1"),
                ("off_to_on", off_path, saved_off, True, "1 != 0")):
            rejected = new("reject-profile-"+name, reference=reference, incoming=True)
            rejected.set_clock(saved["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["rejected_migrations"].append({"name": name, "passed": True,
                "snapshot_file": path.name, "snapshot_sha256": digest(path),
                "stream_modified": False, **details})
            passed("profile_"+name+"_rejected", **details)
            rejected.close()

        captured_offset = 449+4*REGISTERS.index(0x0bc)
        cases = (
            ("physical_base", steady_path, saved_steady, 52, 8, BASE, 4,
             f"{BASE:x} != {BASE ^ 4:x}"),
            ("captured_recipe", pll_path, saved_pll, captured_offset, 4,
             saved_pll["snapshot"]["dp"]["captured"]["pll.0bc"], 1,
             TYPE+": invalid migrated capture"),
            ("last_xo_period", phy_path, saved_phy, 749, 8, period(XO), 1,
             TYPE+": invalid migrated reference clock"),
            ("late_deadline", late_path, saved_late, 757, 8, 2_400_000, 1 << 63,
             TYPE+": invalid migrated transition"),
        )
        for name, source_path, saved, relative, width, old, xor, diagnostic in cases:
            original = source_path.read_bytes()
            absolute = saved["sections"]["dp"]["payload_offset"]+relative
            assert int.from_bytes(original[absolute:absolute+width], "big") == old
            changed = bytearray(original)
            changed[absolute:absolute+width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
            expected_byte = absolute+width-1-(xor.bit_length()-1)//8
            assert differences == [expected_byte]
            assert (original[expected_byte] ^ changed[expected_byte]).bit_count() == 1
            path = out/f"dp-corrupt-{name}.migration"
            path.write_bytes(changed)
            rejected = new("reject-corrupt-"+name, incoming=True)
            rejected.set_clock(saved["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "source_file": source_path.name,
                "source_sha256": saved["sha256"], "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": True,
                "field_offset": relative, "field_bytes": width, "old_value": old,
                "xor": xor, "changed_byte_offsets": differences, "changed_bits": 1,
                "original_stream_unchanged": digest(source_path) == saved["sha256"], **details})
            passed("single_bit_"+name+"_rejected", **details)
            rejected.close()
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
        "schema": "tb371fc-dp-pll-7nm-v1", "suite": "dp-pll-7nm-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 4, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 4, "expected_unmodified_streams": 5,
        "expected_stream_files": 9, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "source_variant": "qcom,mdss_dp_pll_7nm", "physical_base": BASE,
        "region_count": 4, "resource_bytes_per_region": 512,
        "register_count": 73, "sparse_window_count": 42, "mapped_bytes": 292,
        "output_clock_count": 6, "input_clock_count": 1, "connected_input_clock_count": 1,
        "dispcc_carrier_connection_count": 2, "timer_count": 1, "ccf_rate_scale": 1000,
        "required_programmed_words": 58, "readonly_status_words": 3,
        "pll_reference_cycles": 64, "phy_reference_cycles": 64,
        "pll_reference_ns_ceiling": PLL_NS, "phy_reference_ns_ceiling": PHY_NS,
        "late_phy_wait_ns": LATE_PHY_NS, "mdss_vmstate_payload_bytes": 64,
        "mdss_vmstate_version": 1, "reference_enabled_by_default": False,
        "frequency_checks_use_exact_clock_period": True,
        "first_observation_qom_get_count": FIRST_QOM_COUNT,
        "runtime_xo_change_tested": False,
        "runtime_xo_provider": "Actual board RPMh xo.lvl votes; reference input has no fixture override",
        "source_commit": SOURCE_COMMIT,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "stock_compiled_dp_source_identity_claimed": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "analog_pll_behavior_modeled": False, "aux_protocol_modeled": False,
        "link_training_modeled": False, "android_boot_passed": False,
        "scope": "Compiled-device QTest/QMP and source-bound digital transactions; real MDSS "
                 "supply, RPMh XO and two DISPCC carriers; full DP and MDSS migration payloads. "
                 "Cold software rates do not imply physical availability. No AUX, analog PLL, "
                 "USB arbitration, link training, sink or native Android success is established.",
        "binary_unchanged": False, "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "migration_sources": [], "actual_stream_files": [],
        "passed": False,
    }
    try:
        assert len(ORDINARY) == len(set(ORDINARY)) == 24
        for index, test in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = DpRig(args, fixture,
                            rpmh=test is real_rpmh_xo_votes_revoke_pending_and_ready_captures)
                details = test(rig) or {}
                if test is real_rpmh_xo_votes_revoke_pending_and_ready_captures:
                    report["runtime_xo_change_tested"] = details["runtime_xo_loss_exercised"] is True
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_off_rejects_all_words_with_sibling_profiles_enabled,
                     paused_tcg_has_no_mutating_fixture_or_guest_execution):
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
            len(report["migrations"]) == 4 and
            all(item["source_status"] == item["target_status"] == "completed" and
                item["continued_after_restore"] and item["all_791_payload_bytes_verified"] and
                item["mdss_64_bytes_verified"] and
                item["first_target_observation"]["first_observation_qom_get_count"] == FIRST_QOM_COUNT and
                item["first_target_observation"]["device_mmio_since_incoming"] is False and
                item["first_target_observation"]["qmp_cont_since_incoming"] is False
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and len(report["corrupted_stream_rejections"]) == 4 and
            all(item["passed"] is True and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"]+report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 5 and len(report["actual_stream_files"]) == 9 and
            len({item["name"] for item in report["actual_stream_files"]}) == 9 and
            all(item["bytes"] > 0 for item in report["actual_stream_files"]) and
            report["runtime_xo_change_tested"] is True and report["binary_unchanged"])
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
