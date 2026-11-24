#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled source-qualified DSI 7nm v4.1 configuration and migration checks.

The register inventory and literal initialization sequence are independent
oracles read from the pinned display module, not from the generated model data.
Explicit QTest REFGEN inputs and digital deadlines are reference test inputs;
they do not establish analog PHY readiness or native Android boot completion.
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
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels
from test_mdss_gdsc import mdss_payload
from test_dispcc_sm8250 import (
    dispcc_payload, GLOBAL_FIELDS as DISP_GLOBAL_FIELDS, PLL_FIELDS as DISP_PLL_FIELDS,
    RCG_FIELDS as DISP_RCG_FIELDS, REGISTERS as DISP_REGISTERS,
    OUTPUTS as DISP_OUTPUTS, INPUTS as DISP_INPUTS)

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-dsi-pll7nm", "/machine/dsi-pll7nm"
BASES, DYNAMIC_BASES = (0x0ae94400, 0x0ae96400), (0x0ae94200, 0x0ae96200)
MDSS, MDSS_BASE = "/machine/mdss-gdsc", 0x0af03000
DISP, DISP_BASE = "/machine/dispcc", 0x0af00000
RPMH, RPMH_BASE, RAM = "/machine/rpmh", 0x18200000, 0x80010000
XO, PERIOD_UNIT, FRAC_SCALE = 19_200_000, 1_000_000_000 << 32, 1 << 18
LOCK_NS = 5000
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
MODULE_COMMIT = "1d242bf29cb92defd2d569c42286e199de382bee"
CORE_COMMIT = "601d399161053be6df99c089abd7780cf197aa45"
SOURCE_SHA256 = "ec9b2045e3a44ef6cde304baf95b4b7fdea0cf6be9a52172fda4ca709e3851cf"
BINDING_SHA256 = "51632c1a73cfce8c4915e5e5c56f46b0fddae983d3e0ac44cd2731c488105d82"

# PLL-relative offsets written by the source. The old decimal bank and bank 2
# fractions are defined but never written by this v4.1 path. PERF_OPTIMIZE at
# +0x260 lies in the enclosing PHY resource despite being just beyond the PLL
# node's nominal +0x260 resource length; it has a single owner at PHY+0x760.
PLL_REGISTERS = tuple(o for o in range(0, 0x264, 4)
                      if o not in (0xcc, 0xd0, 0xd4, 0xd8, 0xf0, 0xf4, 0xf8, 0xfc))
PHY_REGISTERS = (0x10, 0x14, 0x18, 0x1c, 0x24, 0x2c, 0x30, 0x3c, 0x128)
REGISTERS = PHY_REGISTERS + tuple(0x500+o for o in PLL_REGISTERS)
assert len(PLL_REGISTERS) == 145 and len(REGISTERS) == 154
WINDOWS = ((0x10, 0x10), (0x24, 4), (0x2c, 8), (0x3c, 4), (0x128, 4),
           (0x500, 0xcc), (0x5dc, 0x14), (0x600, 0x164))
UNKNOWN_ZERO_PLL = (0x1b4, 0x1b8, 0x1bc, 0x1c0, 0x1c4, 0x1c8, 0x1cc,
                    0x1ec, 0x1f4, 0x1f8, 0x200, 0x204, 0x208, 0x20c,
                    0x218, 0x21c, 0x220)
PHY_MASKS = dict(zip(PHY_REGISTERS, (0xff, 0x3f, 0x40, 1, 0x7f, 0x40, 4, 1, 1)))
OUTPUT_SUFFIXES = ("vco", "pll_out", "bitclk", "post_bit_div", "post_vco_div",
                   "cphy_post_vco_div3_5", "phy_pll_out_byteclk", "phy_pll_out_dsiclk")
OUTPUTS = tuple(f"dsi{i}_"+n for i in range(2) for n in OUTPUT_SUFFIXES)
CONNECTED = tuple(f"dsi{i}_phy_pll_out_{n}" for n in ("byteclk", "dsiclk") for i in range(2))
IDENTITY, VMSTATE_BYTES, EXPECTED_CHECKS = 0x44534941, 1356, 41
GLOBAL_FIELDS = ("reference-profile", "reference-identity", "bank-count", "registers-per-bank",
                 "register-count", "clock-count", "input-count", "window-count", "mapped-bytes")
PLL_FIELDS = ("active-factor", "pending-factor", "lock-deadline", "locked", "lock-pending",
              "recipe-valid", "cphy", "refgen-known", "refgen-ready", "output-route-known")

# Literal dsi_pll_init_val() order with the v4.1, not v1, BIAS_CONTROL_1 branch.
# These 130 writes deliberately preserve initial values later overwritten by
# dsi_pll_commit()/dsi_pll_config_hzindep_reg().
INITIAL_WRITES = (
    (0x000, 0x00), (0x008, 0x3f), (0x00c, 0x00), (0x014, 0x00),
    (0x01c, 0x80), (0x028, 0x00), (0x02c, 0x00), (0x030, 0x10),
    (0x034, 0x20), (0x038, 0x10), (0x03c, 0x02), (0x040, 0x1c),
    (0x048, 0x00), (0x04c, 0x02), (0x050, 0x20), (0x054, 0x00),
    (0x058, 0xff), (0x05c, 0x00), (0x060, 0x0a), (0x064, 0x25),
    (0x068, 0xba), (0x06c, 0x4f), (0x070, 0x0a), (0x074, 0x00),
    (0x078, 0x0c), (0x07c, 0x20), (0x080, 0x00), (0x084, 0xff),
    (0x088, 0x10), (0x08c, 0x46), (0x098, 0x54), (0x09c, 0x00),
    (0x0a0, 0x00), (0x0a4, 0x40), (0x0ac, 0x04), (0x0b0, 0x00),
    (0x0b4, 0x00), (0x0b8, 0x00), (0x0bc, 0x10), (0x0c0, 0x00),
    (0x0c4, 0x08), (0x0c8, 0x08), (0x0dc, 0x00), (0x100, 0x03),
    (0x104, 0x00), (0x108, 0x00), (0x10c, 0x00), (0x110, 0x00),
    (0x114, 0x00), (0x118, 0x00), (0x11c, 0x00), (0x120, 0x00),
    (0x124, 0x00), (0x128, 0x00), (0x12c, 0x00), (0x130, 0x00),
    (0x134, 0x00), (0x138, 0x00), (0x13c, 0x00), (0x140, 0x00),
    (0x144, 0x00), (0x148, 0x00), (0x14c, 0x00), (0x150, 0x00),
    (0x154, 0x00), (0x158, 0x40), (0x15c, 0x40), (0x160, 0x0c),
    (0x164, 0x0a), (0x168, 0xc0), (0x16c, 0x00), (0x170, 0x54),
    (0x174, 0x54), (0x178, 0x4c), (0x17c, 0x4c), (0x180, 0x03),
    (0x184, 0x00), (0x188, 0x00), (0x18c, 0x00), (0x190, 0x80),
    (0x194, 0x06), (0x198, 0x19), (0x19c, 0x00), (0x1a0, 0x00),
    (0x1a4, 0x40), (0x1a8, 0x20), (0x1ac, 0x00), (0x1b0, 0x00),
    (0x1b4, 0x00), (0x1b8, 0x00), (0x1bc, 0x00), (0x1c0, 0x00),
    (0x1c4, 0x00), (0x1c8, 0x00), (0x1cc, 0x00), (0x1d0, 0x00),
    (0x1d4, 0x02), (0x1d8, 0x11), (0x1dc, 0x00), (0x1e0, 0x00),
    (0x1e4, 0x80), (0x1e8, 0x00), (0x1ec, 0x00), (0x1f0, 0x00),
    (0x1f4, 0x00), (0x1f8, 0x00), (0x1fc, 0x00), (0x200, 0x00),
    (0x204, 0x00), (0x208, 0x00), (0x20c, 0x00), (0x210, 0x00),
    (0x214, 0x00), (0x218, 0x00), (0x21c, 0x00), (0x220, 0x00),
    (0x224, 0x00), (0x228, 0x1d), (0x22c, 0x1c), (0x230, 0xff),
    (0x234, 0x22), (0x238, 0x09), (0x23c, 0x00), (0x240, 0x00),
    (0x244, 0x00), (0x248, 0x40), (0x24c, 0x00), (0x250, 0x10),
    (0x254, 0x10), (0x25c, 0x03),
)
assert len(INITIAL_WRITES) == 130


def ceildiv(value, divisor):
    return (value+divisor-1)//divisor


def quantized_factors(requested_hz):
    # dsi_pll_calc_dec_frac(), 18 fractional bits and the enabled prescaler.
    q = requested_hz*FRAC_SCALE//(2*XO)
    return q >> 18, q & (FRAC_SCALE-1)


def represented_rate(requested_hz):
    dec, frac = quantized_factors(requested_hz)
    return (2*XO)*((dec << 18) | frac)//FRAC_SCALE


def ssc_writes(requested_hz, center=False):
    # Literal source defaults: 31.5 kHz modulation, 4800 ppm, adj_period=2.
    # The driver rounds twice, with integer division between the two rounds.
    dec, frac = quantized_factors(requested_hz)
    period = ((XO+31500//2)//31500)//2-1
    period -= (period+1) % 3
    step = ((((dec << 18) | frac)*4800*3//(period+1))+500000)//1000000
    return ((0x120, step & 255), (0x124, (step >> 8) & 255),
            (0x128, period & 255), (0x12c, period >> 8),
            (0x130, 2), (0x134, 0), (0x150, 2 | int(center)))


def recipe_writes(requested_hz, *, cphy=False, ssc=False, center=False):
    dec, frac = quantized_factors(requested_hz)
    invert = (0xa0 if requested_hz <= 1_000_000_000 else
              0x20 if requested_hz <= 2_500_000_000 else
              0 if requested_hz <= 3_020_000_000 else 0x40)
    vco = 8 if requested_hz < 1_520_000_000 else 1 if requested_hz < 2_990_000_000 else 0
    commit = ((0x0bc, 0x12), (0x0e0, dec), (0x0e4, frac & 255),
              (0x0e8, (frac >> 8) & 255), (0x0ec, frac >> 16),
              (0x158, 0x40), (0x194, 6), (0x250, 0 if cphy else 0x10),
              (0x248, invert))
    independent = (
        (0x258, 1 if requested_hz < 3_100_000_000 else 3), (0x240, vco),
        (0x018, 1), (0x004, 3), (0x010, 0), (0x020, 0), (0x024, 0x4e),
        (0x044, 0x40), (0x068, 0xba), (0x078, 0x0c), (0x0a8, 0),
        (0x0b8, 0), (0x0c8, 8), (0x160, 10), (0x168, 0xc0),
        (0x170, 0x84), (0x170, 0x82), (0x178, 0x4c), (0x190, 0x80),
        (0x090, 0x29), (0x090, 0x2f), (0x094, 0x2a), (0x094, 0x3f),
        (0x260, 0x22))
    return INITIAL_WRITES+commit+independent+(ssc_writes(requested_hz, center) if ssc else ())


class DsiRig(CameraRig):
    """Existing socket/RPMh transport; independent DSI device inventory/oracle."""
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", rpmh=True, mdss=True, disp=True, legacy=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.has_mdss, self.has_disp = bool(mdss), bool(disp)
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.camera_mmio_operations = self.dsi_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="dsipll-"))
        self.log_path = self.directory/"qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory/"qtest.jsonl").open("w")
        self.qmplog = (self.directory/"qmp.jsonl").open("w")
        fields = [f for f in args.machine.split(",") if not f.startswith(
            ("rpmh-profile=", "rpmh-boot-configured=", "rpmh-cmd-db="))]
        fields += [f"rpmh-profile={'reference' if rpmh else 'off'}", "rpmh-boot-configured=off"]
        command = [args.qemu, "-machine", ",".join(fields), "-accel", accel,
                   "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
                   "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off",
                   "-global", f"qcom-mdss-gdsc.enabled-reference={'on' if mdss else 'off'}",
                   "-global", f"qcom-sm8250-dispcc.enabled-reference={'on' if disp else 'off'}"]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if legacy:
            command += ["-global", "qcom-sm8250-camcc.enabled-reference=on",
                        "-global", "qcom-sm8250-videocc.enabled-reference=on",
                        "-global", "qcom-sm8250-npucc.enabled-reference=on",
                        "-global", "qcom-sm8250-gpucc.enabled-reference=on"]
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
        fields = command.split()
        if fields and fields[0] in ("readb", "readw", "readl", "readq",
                                    "writeb", "writew", "writel", "writeq"):
            address = int(fields[1], 0)
            if (any(base <= address < base+0x800 for base in BASES) or
                    any(base <= address < base+0x100 for base in DYNAMIC_BASES)):
                self.dsi_mmio_operations += 1
                self.device_mmio_operations += 1
            elif DISP_BASE <= address < DISP_BASE+0x20000:
                self.device_mmio_operations += 1
        return super().command(command)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def reg(self, bank, offset):
        return self.qom(f"bank{bank}.reg.{offset:03x}")

    def read(self, bank, offset):
        return self.raw_read(BASES[bank]+offset)

    def write(self, bank, offset, value):
        self.raw_write(BASES[bank]+offset, value)

    def pwrite(self, bank, offset, value):
        self.write(bank, 0x500+offset, value)

    def pll(self, bank, field):
        return self.qom(f"pll{bank}-{field}")

    def hz(self, bank, suffix):
        return self.qom(f"clk.dsi{bank}_{suffix}.hz")

    def period(self, bank, suffix):
        return self.qom(f"clk.dsi{bank}_{suffix}.period")

    def expect_hz(self, bank, suffix, hz):
        assert self.hz(bank, suffix) == hz, (bank, suffix, self.hz(bank, suffix), hz)
        assert self.period(bank, suffix) == (PERIOD_UNIT//hz if hz else 0)

    def refgen(self, bank, known, ready):
        assert type(known) is type(ready) is bool
        assert known or not ready
        # Both are explicit external test inputs; neither is a success status
        # inferred from a guest request or an otherwise-unmodeled regulator.
        self.monitor("qom-set", {"path": DEVICE, "property": f"qtest-refgen{bank}-known", "value": known})
        self.monitor("qom-set", {"path": DEVICE, "property": f"qtest-refgen{bank}-ready", "value": ready})

    def mdss_parent(self, known, on):
        assert not self.has_rpmh, "Do not override the real RPMh provider"
        self.command(f"set_irq_in {MDSS} parent-on 0 {int(on)}")
        self.command(f"set_irq_in {MDSS} parent-known 0 {int(known)}")

    def dread(self, offset, drv=2):
        return self.raw_read(RPMH_BASE+drv*0x10000+offset)

    def dwrite(self, offset, value, drv=2):
        self.raw_write(RPMH_BASE+drv*0x10000+offset, value)

    def pure_snapshot(self):
        return {
            "enabled-reference": self.qom("enabled-reference"),
            "globals": {n: self.qom(n) for n in GLOBAL_FIELDS},
            "physical-bases": [self.qom(f"phy{i}-base") for i in range(2)],
            "dynamic-bases": [self.qom(f"dynamic{i}-base") for i in range(2)],
            "registers": [{f"{o:03x}": self.reg(i, o) for o in REGISTERS} for i in range(2)],
            "plls": [{n: self.pll(i, n) for n in PLL_FIELDS} for i in range(2)],
            "last-xo-hz": self.qom("last-xo-hz"),
            "power-known": self.qom("power-known"), "power-on": self.qom("power-on"),
            "input-hz": self.qom("input.bi_tcxo.hz"),
            "input-period": self.qom("input.bi_tcxo.period"),
            "output-hz": {n: self.qom(f"clk.{n}.hz") for n in OUTPUTS},
            "output-periods": {n: self.qom(f"clk.{n}.period") for n in OUTPUTS}}

    def mdss_snapshot(self):
        return {"enabled-reference": self.qom("enabled-reference", MDSS),
                "globals": {n: self.qom(n, MDSS) for n in GDSC_GLOBAL_FIELDS},
                "parent-known": self.qom("parent-known", MDSS),
                "parent-on": self.qom("parent-on", MDSS),
                "domains": [{n: self.qom("domain0."+n, MDSS) for n in GDSC_DOMAIN_FIELDS}]}

    def disp_snapshot(self):
        return {
            "globals": {n: self.qom(n, DISP) for n in DISP_GLOBAL_FIELDS},
            "registers": {f"{o:04x}": self.qom(f"reg.{o:04x}", DISP) for o in DISP_REGISTERS},
            "plls": [{n: self.qom(f"pll{i}-"+n, DISP) for n in DISP_PLL_FIELDS} for i in range(2)],
            "rcgs": [{n: self.qom(f"rcg{i}-"+n, DISP) for n in DISP_RCG_FIELDS} for i in range(22)],
            "last-xo-hz": self.qom("last-xo-hz", DISP),
            "dividers": [self.qom(f"divider{i}-value", DISP) for i in range(5)],
            "remote-votes": [self.qom(f"remote-vote{i}", DISP) for i in range(2)],
            "output_hz": {n: self.qom("clk."+n+".hz", DISP) for n in DISP_OUTPUTS},
            "outputs": {n: self.qom("qtest-clock-period", DISP+"/"+n) for n in DISP_OUTPUTS},
            "inputs": {n: self.qom("qtest-clock-period", DISP+"/"+n) for n in DISP_INPUTS}}

    def reject_write(self, bank, offset, value):
        before = self.pure_snapshot()
        count = self.log_path.read_text(errors="replace").count(TYPE+":")
        self.write(bank, offset, value)
        assert self.pure_snapshot() == before, (bank, hex(offset), hex(value))
        assert self.log_path.read_text(errors="replace").count(TYPE+":") == count+1

    def reject_read(self, bank, offset):
        before = self.pure_snapshot()
        log = self.log_path.read_text(errors="replace")
        self.read(bank, offset)
        assert self.pure_snapshot() == before, (bank, hex(offset))
        after = self.log_path.read_text(errors="replace")[len(log):]
        assert TYPE+":" in after or "Invalid read" in after, after

    def reset_device(self):
        self.events.clear()
        self.monitor("system_reset")
        self.wait_for(lambda: all(self.reg(i, 0x3c) == 0 for i in range(2)), "DSI reset witness")
        self.monitor("query-status")
        assert any(e.get("event") == "RESET" for e in self.events)


def supply_mdss(r):
    assert r.has_mdss
    if r.has_rpmh:
        r.run_one("mmcx.lvl", 1)
        r.run_one("xo.lvl", 1)
    else:
        r.mdss_parent(True, True)
    r.raw_write(MDSS_BASE, 0)
    assert r.qom("power-known") is True and r.qom("power-on") is True


def configure_bank(r, bank, requested_hz=1_200_000_000, *, cphy=False, ssc=False, center=False):
    r.write(bank, 0x3c, 0)
    r.write(bank, 0x18, 0x40 if cphy else 0)
    r.write(bank, 0x14, 3 if cphy else 0)
    r.write(bank, 0x10, 0x43)
    r.write(bank, 0x1c, 0)
    r.write(bank, 0x24, 0x60)
    for offset, value in recipe_writes(requested_hz, cphy=cphy, ssc=ssc, center=center):
        r.pwrite(bank, offset, value)
    assert r.pll(bank, "recipe-valid") is True, (bank, requested_hz, cphy, ssc)


def start_bank(r, bank):
    r.pwrite(bank, 0x28, 0xc0)
    r.write(bank, 0x24, r.reg(bank, 0x24) | 0x20)
    r.write(bank, 0x3c, 1)


def open_outputs(r, bank):
    # Source post-lock digital reset pulse and global clock enable; RBUF is
    # independently enabled by the PHY resynchronization path.
    r.write(bank, 0x128, 1)
    r.write(bank, 0x128, 0)
    r.write(bank, 0x30, 4)
    r.write(bank, 0x14, r.reg(bank, 0x14) | 0x30)
    r.write(bank, 0x1c, 1)


def enable_bank(r, bank, requested_hz=1_200_000_000, *, cphy=False, ssc=False, center=False):
    configure_bank(r, bank, requested_hz, cphy=cphy, ssc=ssc, center=center)
    r.refgen(bank, True, True)
    start_bank(r, bank)
    r.step(LOCK_NS)
    assert r.pll(bank, "locked") is True
    r.expect_hz(bank, "vco", represented_rate(requested_hz))
    open_outputs(r, bank)


def expected_rates(requested_hz, *, outdiv=0, bitdiv=3, pixeldiv=4, selector=0, cphy=False):
    vco = represented_rate(requested_hz)
    pll_out = ceildiv(vco, 1 << outdiv)
    bit = ceildiv(pll_out, bitdiv) if bitdiv else pll_out
    post_bit, post_vco, cpost = bit//2, pll_out//4, pll_out*2//7
    parent = cpost if cphy and selector == 3 else bit if not cphy and selector == 0 else post_bit if not cphy and selector == 1 else 0
    pixel = ceildiv(parent, pixeldiv) if pixeldiv else parent
    return dict(zip(OUTPUT_SUFFIXES, (vco, pll_out, bit, post_bit, post_vco, cpost,
                                      bit//(7 if cphy else 8), pixel)))


def assert_rates(r, bank, requested_hz, **options):
    for name, hz in expected_rates(requested_hz, **options).items():
        r.expect_hz(bank, name, hz)


def cold_inventory_and_faulting_mode_word_are_readable_without_claiming_handoff(r):
    s = r.pure_snapshot()
    assert tuple(s["globals"][n] for n in GLOBAL_FIELDS) == (1, IDENTITY, 2, 154, 308, 16, 1, 18, 1240)
    assert s["physical-bases"] == list(BASES) and s["dynamic-bases"] == list(DYNAMIC_BASES)
    assert all(v == 0 for bank in s["registers"] for v in bank.values())
    assert all(v == 0 for v in s["output-hz"].values())
    assert s["input-period"] == 0 and s["power-known"] is True and s["power-on"] is False
    for i in range(2):
        assert r.read(i, 0x18) == 0 and not r.pll(i, "cphy")
        assert not r.pll(i, "locked") and not r.pll(i, "recipe-valid")
        assert not r.pll(i, "refgen-known") and not r.pll(i, "refgen-ready")
        assert r.read(i, 0x6b0) == 0
    assert r.pure_snapshot() == s
    return {"first_fault_addresses": [base+0x18 for base in BASES], "firmware_handoff_invented": False}


def source_initial_writes_and_overwrites_preserve_both_register_views(r):
    supply_mdss(r)
    for bank in range(2):
        other = r.pure_snapshot()["registers"][1-bank]
        for offset, value in INITIAL_WRITES:
            r.pwrite(bank, offset, value)
            assert r.reg(bank, 0x500+offset) == value
        assert not r.pll(bank, "recipe-valid") and not r.pll(bank, "locked")
        assert r.pure_snapshot()["registers"][1-bank] == other
        configure_bank(r, bank)
        final = dict(recipe_writes(1_200_000_000))
        assert all(r.reg(bank, 0x500+offset) == value for offset, value in final.items())
        assert r.read(bank, 0x760) == 0x22
    return {"literal_initialization_writes": 130, "perf_optimize_inside_shared_phy": True}


def two_complete_dphy_cphy_recipes_lock_independently(r):
    supply_mdss(r)
    enable_bank(r, 0, 1_200_000_000)
    before = [r.hz(0, name) for name in OUTPUT_SUFFIXES]
    enable_bank(r, 1, 2_400_000_000, cphy=True)
    assert [r.hz(0, name) for name in OUTPUT_SUFFIXES] == before
    assert_rates(r, 0, 1_200_000_000)
    assert_rates(r, 1, 2_400_000_000, selector=3, cphy=True)
    assert r.read(0, 0x18) == 0 and r.read(1, 0x18) == 0x40


def every_vco_recipe_bucket_and_supported_endpoint_is_source_qualified(r):
    supply_mdss(r)
    values = (600_000_000, 800_000_000, 1_200_000_000, 1_800_000_000,
              2_700_000_000, 3_000_000_000, 3_060_000_000, 3_600_000_000, 5_000_000_000)
    for index, value in enumerate(values):
        bank = index % 2
        enable_bank(r, bank, value, cphy=bool(index % 3 == 0))
        assert r.pll(bank, "active-factor") == value*FRAC_SCALE//(2*XO)
        assert r.read(bank, 0x6b0) == 1
    return {"vco_vectors_hz": list(values), "upper_vector_exceeds_uint32": True}


def quantized_boundary_cells_accept_only_intersecting_driver_buckets(r):
    supply_mdss(r)
    thresholds = (1_000_000_000, 1_520_000_000, 2_500_000_000,
                  2_990_000_000, 3_020_000_000, 3_100_000_000)
    for threshold in thresholds:
        for requested in (threshold-1, threshold, threshold+1):
            enable_bank(r, 0, requested)
            assert r.pll(0, "recipe-valid")
        # Use a complete, otherwise legal distant bucket, not merely an
        # unsupported byte. Adjacent buckets may share the same quantized q.
        wrong = (0x40, 0, 3) if threshold < 3_100_000_000 else (0xa0, 8, 1)
        for offset, value in zip((0x248, 0x240, 0x258), wrong):
            r.pwrite(0, offset, value)
        assert not r.pll(0, "recipe-valid") and not r.pll(0, "locked")
    return {"thresholds_hz": list(thresholds), "requested_rate_interval_checked": True}


def factors_below_minimum_above_maximum_and_high_fraction_bits_cannot_lock(r):
    supply_mdss(r)
    for value, delta in ((600_000_000, -1), (5_000_000_000, 1)):
        enable_bank(r, 0, value)
        q = value*FRAC_SCALE//(2*XO)+delta
        for offset, byte in ((0xe0, q >> 18), (0xe4, q & 255),
                             (0xe8, (q >> 8) & 255), (0xec, (q >> 16) & 3)):
            r.pwrite(0, offset, byte)
        assert not r.pll(0, "recipe-valid") and not r.pll(0, "locked")
        r.step(LOCK_NS*2)
        r.expect_hz(0, "vco", 0)
    r.reject_write(0, 0x5ec, 4)


def changed_full_recipe_fields_withdraw_only_the_affected_pll(r):
    supply_mdss(r)
    for bank in range(2):
        enable_bank(r, bank, 1_200_000_000+bank*1_200_000_000)
    offsets = (0x008, 0x01c, 0x024, 0x044, 0x090, 0x094,
               0x158, 0x170, 0x190, 0x1a4, 0x230, 0x260)
    for bank in range(2):
        other = r.hz(1-bank, "vco")
        for offset in offsets:
            saved = r.reg(bank, 0x500+offset)
            r.pwrite(bank, offset, saved ^ 1)
            assert not r.pll(bank, "recipe-valid") and not r.pll(bank, "locked")
            r.expect_hz(bank, "vco", 0)
            r.expect_hz(1-bank, "vco", other)
            r.pwrite(bank, offset, saved)
            assert r.pll(bank, "lock-pending")
            r.step(LOCK_NS-1)
            assert not r.pll(bank, "locked")
            r.step(1)
            r.expect_hz(bank, "vco", represented_rate(1_200_000_000+bank*1_200_000_000))


def mode_bit_and_cmode_recipe_must_agree_without_guessing_other_phy_writes(r):
    supply_mdss(r)
    enable_bank(r, 0)
    r.write(0, 0x18, 0x40)
    assert r.pll(0, "cphy") and not r.pll(0, "recipe-valid")
    assert not r.pll(0, "locked")
    r.pwrite(0, 0x250, 0)
    r.write(0, 0x14, 0x33)
    r.step(LOCK_NS)
    assert r.pll(0, "locked")
    assert_rates(r, 0, 1_200_000_000, cphy=True, selector=3)
    r.write(0, 0x2c, 0x40)
    assert r.read(0, 0x18) == 0x40, "A DPHY configuration word must not erase the CPHY mode bit"


def real_mdss_collapse_and_rpmh_supply_bound_both_plls(r):
    supply_mdss(r)
    for bank in range(2):
        enable_bank(r, bank)
    r.raw_write(MDSS_BASE, 1)
    assert r.qom("power-known") and not r.qom("power-on")
    assert all(not r.pll(i, "locked") and r.hz(i, "vco") == 0 for i in range(2))
    r.raw_write(MDSS_BASE, 0)
    r.step(LOCK_NS)
    assert all(r.pll(i, "locked") for i in range(2))
    r.run_one("mmcx.lvl", 0)
    assert r.qom("power-known") and not r.qom("power-on")
    assert all(r.hz(i, "vco") == 0 for i in range(2))
    # The wrong real resource cannot power a domain supplied by MMCX.
    r.run_one("mx.lvl", 3)
    r.run_one("cx.lvl", 3)
    assert not r.qom("power-on")
    r.run_one("mmcx.lvl", 1)
    r.step(LOCK_NS)
    assert all(r.pll(i, "locked") for i in range(2))


def explicit_refgen_known_and_ready_inputs_are_independent_per_bank(r):
    supply_mdss(r)
    for bank in range(2):
        configure_bank(r, bank)
        start_bank(r, bank)
    before = r.pure_snapshot()
    invalid = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-refgen0-ready", "value": True})
    assert "error" in invalid and r.pure_snapshot() == before
    for known, ready in ((False, False), (True, False)):
        r.refgen(0, known, ready)
        r.step(LOCK_NS*2)
        assert not r.pll(0, "locked") and not r.pll(0, "lock-pending")
        assert r.hz(0, "vco") == 0
    r.refgen(0, True, True)
    r.step(LOCK_NS)
    assert r.pll(0, "locked") and not r.pll(1, "locked")
    r.refgen(1, True, True)
    r.step(LOCK_NS)
    assert r.pll(1, "locked")
    r.refgen(0, False, False)
    assert not r.pll(0, "locked") and r.pll(1, "locked")


def lock_completion_occurs_at_original_virtual_deadline_without_device_reads(r):
    supply_mdss(r)
    configure_bank(r, 0)
    r.refgen(0, True, True)
    start_bank(r, 0)
    deadline = r.pll(0, "lock-deadline")
    assert deadline == r.last_clock+LOCK_NS
    before = r.device_mmio_operations
    r.step(LOCK_NS-1)
    assert r.pll(0, "lock-deadline") == deadline and not r.pll(0, "locked")
    for _ in range(3):
        assert r.pll(0, "lock-pending") and not r.pll(0, "locked")
    r.step(1)
    assert r.pll(0, "locked") and not r.pll(0, "lock-pending")
    assert r.device_mmio_operations == before
    r.expect_hz(0, "vco", 1_200_000_000)


def start_bias_and_system_mux_are_separate_lock_conditions(r):
    supply_mdss(r)
    for bank in range(2):
        enable_bank(r, bank)
    for offset, invalid in ((0x3c, 0), (0x24, 0x40), (0x528, 0)):
        old = r.reg(0, offset)
        r.write(0, offset, invalid)
        assert not r.pll(0, "locked") and r.hz(0, "vco") == 0
        assert r.pll(1, "locked")
        r.write(0, offset, old)
        r.step(LOCK_NS)
        assert r.pll(0, "locked")


def four_output_divider_encodings_use_power_of_two_and_generic_rounding(r):
    supply_mdss(r)
    rate = 1_234_567_891
    enable_bank(r, 0, rate)
    for outdiv in range(4):
        r.pwrite(0, 0x154, outdiv)
        assert_rates(r, 0, rate, outdiv=outdiv)


def all_bit_dividers_include_zero_passthrough_and_round_up(r):
    supply_mdss(r)
    rate = 1_234_567_891
    enable_bank(r, 0, rate)
    for div in range(16):
        r.write(0, 0x10, 0x40 | div)
        assert_rates(r, 0, rate, bitdiv=div)


def dphy_pixel_mux_and_every_pixel_divider_preserve_fixed_factor_floor(r):
    supply_mdss(r)
    rate = 1_234_567_891
    enable_bank(r, 0, rate)
    for selector in (0, 1):
        r.write(0, 0x14, 0x30 | selector)
        for div in range(16):
            r.write(0, 0x10, div << 4 | 3)
            assert_rates(r, 0, rate, pixeldiv=div, selector=selector)


def cphy_pixel_tap_uses_two_sevenths_of_pll_out_not_bit_divider(r):
    supply_mdss(r)
    rate = 2_345_678_901
    enable_bank(r, 1, rate, cphy=True)
    for outdiv, bitdiv, pixeldiv in ((0, 0, 0), (1, 3, 5), (2, 15, 7), (3, 2, 15)):
        r.pwrite(1, 0x154, outdiv)
        r.write(1, 0x10, pixeldiv << 4 | bitdiv)
        assert_rates(r, 1, rate, outdiv=outdiv, bitdiv=bitdiv, pixeldiv=pixeldiv, selector=3, cphy=True)


def unsupported_pixel_selectors_do_not_create_a_carrier(r):
    supply_mdss(r)
    enable_bank(r, 0)
    enable_bank(r, 1, 2_400_000_000, cphy=True)
    for bank, selectors in ((0, (2, 3)), (1, (0, 1, 2))):
        for selector in selectors:
            r.write(bank, 0x14, 0x30 | selector)
            assert r.pll(bank, "locked")
            r.expect_hz(bank, "phy_pll_out_dsiclk", 0)
            r.expect_hz(bank, "phy_pll_out_byteclk", 50_000_000 if bank == 0 else 800_000_000//7)


def external_route_values_remain_unknown_without_cross_bank_aliases(r):
    supply_mdss(r)
    enable_bank(r, 0)
    enable_bank(r, 1, 2_400_000_000)
    for route in (4, 8, 12):
        r.write(1, 0x14, 0x30 | route)
        assert not r.pll(1, "output-route-known")
        r.expect_hz(1, "phy_pll_out_byteclk", 0)
        r.expect_hz(1, "phy_pll_out_dsiclk", 0)
        r.expect_hz(0, "phy_pll_out_byteclk", 50_000_000)
    r.write(1, 0x14, 0x30)
    assert r.pll(1, "output-route-known")
    assert_rates(r, 1, 2_400_000_000)


def global_clock_gate_controls_physical_outputs_without_forging_pll_state(r):
    supply_mdss(r)
    enable_bank(r, 0)
    r.write(0, 0x14, r.reg(0, 0x14) & ~0x20)
    r.expect_hz(0, "phy_pll_out_byteclk", 0)
    r.expect_hz(0, "phy_pll_out_dsiclk", 0)
    r.expect_hz(0, "vco", 1_200_000_000)
    r.write(0, 0x14, r.reg(0, 0x14) | 0x20)
    assert_rates(r, 0, 1_200_000_000)
    # Driver later clears CLK_EN_SEL bit4 after FIFO sync; bit5 remains gate.
    r.write(0, 0x14, r.reg(0, 0x14) & ~0x10)
    assert_rates(r, 0, 1_200_000_000)


def fifo_and_digital_reset_requests_do_not_reset_the_other_bank_or_analog_lock(r):
    supply_mdss(r)
    enable_bank(r, 0)
    enable_bank(r, 1, 2_400_000_000)
    other = r.pure_snapshot()["registers"][1]
    r.write(0, 0x1c, 0)
    assert r.read(0, 0x1c) == 0
    r.expect_hz(0, "vco", 1_200_000_000)
    r.write(0, 0x1c, 1)
    r.write(0, 0x128, 1)
    assert r.pll(0, "locked") and r.pll(1, "locked")
    r.write(0, 0x128, 0)
    assert_rates(r, 0, 1_200_000_000)
    assert r.pure_snapshot()["registers"][1] == other


def source_ssc_fields_store_both_modes_but_invalid_parameters_cannot_lock(r):
    supply_mdss(r)
    for bank, center in ((0, False), (1, True)):
        enable_bank(r, bank, 2_400_000_000, ssc=True, center=center)
        for offset, value in ssc_writes(2_400_000_000, center):
            assert r.reg(bank, 0x500+offset) == value
        assert_rates(r, bank, 2_400_000_000)
        saved = r.reg(bank, 0x628)
        r.write(bank, 0x628, saved ^ 1)
        assert not r.pll(bank, "recipe-valid") and not r.pll(bank, "locked")
        r.write(bank, 0x628, saved)
        r.step(LOCK_NS)
        assert r.pll(bank, "locked")
        r.reject_write(bank, 0x650, 4)
    return {"ssc_modulation_simulated": False, "nominal_carrier_only": True}


def status_zero_initialization_is_noop_and_never_forges_or_erases_lock(r):
    supply_mdss(r)
    enable_bank(r, 0)
    before = r.pure_snapshot()
    for offset in (0x1b0,)+UNKNOWN_ZERO_PLL:
        r.pwrite(0, offset, 0)
        assert r.pure_snapshot() == before
        r.reject_write(0, 0x500+offset, 1)
    assert r.read(0, 0x6b0) == 1 and r.pll(0, "locked")


def unknown_refgen_revision_ulps_lane_and_calibration_reads_reject_without_success(r):
    for bank in range(2):
        for offset in (0, 0x140, 0x148, 0x14c):
            r.reject_read(bank, offset)
        for offset in UNKNOWN_ZERO_PLL:
            r.reject_read(bank, 0x500+offset)
    return {"unknown_status_read_as_success": False, "ulps_zero_success_invented": False}


def dynamic_control_all_nonzero_requests_reject_and_no_sequencer_memory_is_faked(r):
    for bank, base in enumerate(DYNAMIC_BASES):
        assert r.raw_read(base) == 0
        before = r.pure_snapshot()
        r.raw_write(base, 0)
        assert r.pure_snapshot() == before
        for value in (1, 1 << 8, 1 << 13, 1 << 16, 0x12101, 0xffffffff):
            oldlog = r.log_path.read_text(errors="replace")
            r.raw_write(base, value)
            assert r.pure_snapshot() == before
            assert TYPE+":" in r.log_path.read_text(errors="replace")[len(oldlog):]
        for offset in (4, 8, 12, 0x10, 0x14, 0x90, 0x94, 0x98, 0xfc):
            oldlog = r.log_path.read_text(errors="replace")
            r.raw_write(base+offset, 0xdeadbeef)
            r.raw_read(base+offset)
            assert r.pure_snapshot() == before
            assert core_diagnostics(r.log_path.read_text(errors="replace")[len(oldlog):])
    return {"dynamic_execution_observed": False, "controller_done_synthesized": False}


def reserved_bits_narrow_transactions_and_sparse_holes_are_atomic(r):
    for bank in range(2):
        for offset, mask in PHY_MASKS.items():
            r.reject_write(bank, offset, (~mask) & (mask+1))
        r.reject_write(bank, 0x2c, 0x20)
        for offset, value in ((0x500, 0x100), (0x654, 4), (0x5ec, 4), (0x650, 4)):
            r.reject_write(bank, offset, value)
        for op, offset, value in (("writeb", 0x18, 0x40), ("writew", 0x18, 0x40),
                                   ("writeq", 0x10, 0), ("writel", 0x19, 0)):
            before = r.pure_snapshot()
            log = r.log_path.read_text(errors="replace")
            r.command(f"{op} {BASES[bank]+offset:#x} {value:#x}")
            assert r.pure_snapshot() == before
            assert r.log_path.read_text(errors="replace")[len(log):]
        for offset in (4, 0x20, 0x28, 0x34, 0x40, 0x124, 0x12c, 0x200,
                       0x418, 0x4fc, 0x5cc, 0x5d8, 0x5f0, 0x5fc, 0x764, 0x7fc):
            assert offset not in REGISTERS
            before = r.pure_snapshot()
            log = r.log_path.read_text(errors="replace")
            r.write(bank, offset, 1)
            r.read(bank, offset)
            assert r.pure_snapshot() == before
            assert core_diagnostics(r.log_path.read_text(errors="replace")[len(log):])


def readonly_observers_and_boolean_fixture_types_cannot_mutate_runtime(r):
    before = r.pure_snapshot()
    for name, value in (("reference-identity", 0), ("bank-count", 1), ("power-known", True),
                        ("power-on", True), ("bank0.reg.018", 0x40), ("pll0-locked", True),
                        ("clk.dsi0_vco.hz", XO), ("input.bi_tcxo.hz", XO)):
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in response, response
        assert r.pure_snapshot() == before
    for name in ("qtest-refgen0-known", "qtest-refgen0-ready",
                 "qtest-refgen1-known", "qtest-refgen1-ready"):
        for invalid in (-1, 0, 1, "true", None, [], {}):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": invalid})
            assert "error" in response, (name, invalid, response)
            assert r.pure_snapshot() == before


def system_reset_cancels_pending_locks_and_preserves_explicit_refgen_inputs(r):
    supply_mdss(r)
    enable_bank(r, 0)
    configure_bank(r, 1, 2_400_000_000, cphy=True)
    r.refgen(1, True, True)
    start_bank(r, 1)
    r.step(401)
    assert r.pll(0, "locked") and r.pll(1, "lock-pending")
    r.reset_device()
    s = r.pure_snapshot()
    assert all(v == 0 for bank in s["registers"] for v in bank.values())
    assert all(not p["locked"] and not p["lock-pending"] for p in s["plls"])
    assert all(p["refgen-known"] and p["refgen-ready"] for p in s["plls"])
    r.step(LOCK_NS*2)
    assert all(r.hz(i, "vco") == 0 for i in range(2))


def real_rpmh_xo_removal_withdraws_both_banks_and_restoration_relocks(r):
    supply_mdss(r)
    for bank in range(2):
        enable_bank(r, bank)
    r.run_one("xo.lvl", 0)
    assert r.qom("input.bi_tcxo.hz") == 0
    assert all(not r.pll(i, "locked") and r.hz(i, "vco") == 0 for i in range(2))
    r.run_one("xo.lvl", 1)
    assert r.qom("input.bi_tcxo.hz") == XO
    r.step(LOCK_NS-1)
    assert all(not r.pll(i, "locked") for i in range(2))
    r.step(1)
    assert all(r.pll(i, "locked") for i in range(2))


def four_real_dispcc_inputs_follow_physical_clock_periods_including_above_uint32(r):
    supply_mdss(r)
    enable_bank(r, 0, 5_000_000_000)
    enable_bank(r, 1, 2_400_000_000, cphy=True)
    r.write(0, 0x10, 0)  # both one-based dividers explicitly permit zero.
    assert r.hz(0, "phy_pll_out_dsiclk") > 0xffffffff
    for bank in range(2):
        for suffix in ("byteclk", "dsiclk"):
            name = f"dsi{bank}_phy_pll_out_{suffix}"
            assert r.qom("qtest-clock-period", DISP+"/"+name) == r.qom(f"clk.{name}.period")
    # Fixed DISPCC source: pixel0 RCG 0x2098 selects DSI0 pixel at selector1.
    # HID=1 is unity; no MND mode. No demand fabrication from critical flags.
    r.raw_write(DISP_BASE+0x209c, 0x101)
    r.raw_write(DISP_BASE+0x2098, 3)
    r.step(1000)
    incoming_period = r.period(0, "phy_pll_out_dsiclk")
    expected = PERIOD_UNIT//incoming_period
    assert r.qom("clk.disp_cc_mdss_pclk0_clk_src.hz", DISP) == expected
    assert expected > 0xffffffff
    r.write(0, 0x14, r.reg(0, 0x14) & ~0x20)
    assert r.qom("qtest-clock-period", DISP+"/dsi0_phy_pll_out_dsiclk") == 0
    assert r.qom("clk.disp_cc_mdss_pclk0_clk_src.hz", DISP) == 0


def connected_dispcc_fixtures_reject_overrides_and_software_aliases_have_no_hardware_state(r):
    before = r.pure_snapshot()
    for name in CONNECTED:
        response = r.raw_monitor("qom-set", {"path": DISP, "property": "qtest-"+name+"-hz", "value": 123456789})
        assert "error" in response, (name, response)
        assert r.pure_snapshot() == before
    for name in ("dsi0pll_shadow_vco_clk", "dsi0pll_shadow_pll_out_div", "dsi1pll_cphy_shadow_byteclk_src"):
        response = r.raw_monitor("qom-get", {"path": DEVICE, "property": "clk."+name+".hz"})
        assert "error" in response, response
    # Independent unconnected test carrier retains its original DISPCC fixture.
    r.monitor("qom-set", {"path": DISP, "property": "qtest-core_bi_pll_test_se-hz", "value": 70_000_003})
    assert r.qom("qtest-clock-period", DISP+"/core_bi_pll_test_se") == PERIOD_UNIT//70_000_003


def dual_pending_requests_keep_distinct_captured_factors_and_deadlines(r):
    supply_mdss(r)
    configure_bank(r, 0)
    configure_bank(r, 1, 2_400_000_000)
    r.refgen(0, True, True)
    r.refgen(1, True, True)
    start_bank(r, 0)
    first = r.pll(0, "lock-deadline")
    r.step(900)
    start_bank(r, 1)
    second = r.pll(1, "lock-deadline")
    assert second-first == 900
    assert r.pll(0, "pending-factor") != r.pll(1, "pending-factor")
    r.set_clock(first)
    assert r.pll(0, "locked") and not r.pll(1, "locked")
    r.set_clock(second)
    assert r.pll(1, "locked")


ORDINARY = (
    cold_inventory_and_faulting_mode_word_are_readable_without_claiming_handoff,
    source_initial_writes_and_overwrites_preserve_both_register_views,
    two_complete_dphy_cphy_recipes_lock_independently,
    every_vco_recipe_bucket_and_supported_endpoint_is_source_qualified,
    quantized_boundary_cells_accept_only_intersecting_driver_buckets,
    factors_below_minimum_above_maximum_and_high_fraction_bits_cannot_lock,
    changed_full_recipe_fields_withdraw_only_the_affected_pll,
    mode_bit_and_cmode_recipe_must_agree_without_guessing_other_phy_writes,
    real_mdss_collapse_and_rpmh_supply_bound_both_plls,
    explicit_refgen_known_and_ready_inputs_are_independent_per_bank,
    lock_completion_occurs_at_original_virtual_deadline_without_device_reads,
    start_bias_and_system_mux_are_separate_lock_conditions,
    four_output_divider_encodings_use_power_of_two_and_generic_rounding,
    all_bit_dividers_include_zero_passthrough_and_round_up,
    dphy_pixel_mux_and_every_pixel_divider_preserve_fixed_factor_floor,
    cphy_pixel_tap_uses_two_sevenths_of_pll_out_not_bit_divider,
    unsupported_pixel_selectors_do_not_create_a_carrier,
    external_route_values_remain_unknown_without_cross_bank_aliases,
    global_clock_gate_controls_physical_outputs_without_forging_pll_state,
    fifo_and_digital_reset_requests_do_not_reset_the_other_bank_or_analog_lock,
    source_ssc_fields_store_both_modes_but_invalid_parameters_cannot_lock,
    status_zero_initialization_is_noop_and_never_forges_or_erases_lock,
    unknown_refgen_revision_ulps_lane_and_calibration_reads_reject_without_success,
    dynamic_control_all_nonzero_requests_reject_and_no_sequencer_memory_is_faked,
    reserved_bits_narrow_transactions_and_sparse_holes_are_atomic,
    readonly_observers_and_boolean_fixture_types_cannot_mutate_runtime,
    system_reset_cancels_pending_locks_and_preserves_explicit_refgen_inputs,
    real_rpmh_xo_removal_withdraws_both_banks_and_restoration_relocks,
    four_real_dispcc_inputs_follow_physical_clock_periods_including_above_uint32,
    connected_dispcc_fixtures_reject_overrides_and_software_aliases_have_no_hardware_state,
    dual_pending_requests_keep_distinct_captured_factors_and_deadlines,
)
assert len(ORDINARY) == 31


def default_profile_rejects_all_words_despite_other_enabled_references(args, out):
    r = DsiRig(args, out/"standalone-default-profile", reference=None, legacy=True, rpmh=False)
    try:
        assert not r.qom("enabled-reference") and r.qom("reference-profile") == 0
        before = r.pure_snapshot()
        for bank in range(2):
            for offset in REGISTERS:
                count = r.log_path.read_text(errors="replace").count(TYPE+":")
                r.read(bank, offset)
                r.write(bank, offset, 0)
                assert r.log_path.read_text(errors="replace").count(TYPE+":") == count+2
            count = r.log_path.read_text(errors="replace").count(TYPE+":")
            r.raw_read(DYNAMIC_BASES[bank])
            r.raw_write(DYNAMIC_BASES[bank], 0)
            assert r.log_path.read_text(errors="replace").count(TYPE+":") == count+2
            for suffix in ("known", "ready"):
                response = r.raw_monitor("qom-set", {"path": DEVICE,
                    "property": f"qtest-refgen{bank}-{suffix}", "value": True})
                assert "error" in response
        assert r.pure_snapshot() == before
        # Disabled DSI keeps the pre-existing DISP fixture contract usable.
        for i, name in enumerate(CONNECTED):
            value = 70_000_003+i
            r.monitor("qom-set", {"path": DISP, "property": "qtest-"+name+"-hz", "value": value})
            assert r.qom("qtest-clock-period", DISP+"/"+name) == PERIOD_UNIT//value
    finally:
        r.close()


def paused_tcg_exposes_only_pure_observers_and_never_executes_guest(args, out):
    r = DsiRig(args, out/"standalone-paused-tcg", accel="tcg", rpmh=False)
    try:
        assert r.monitor("query-status")["running"] is False
        before = r.pure_snapshot()
        for bank in range(2):
            for suffix in ("known", "ready"):
                response = r.raw_monitor("qom-set", {"path": DEVICE,
                    "property": f"qtest-refgen{bank}-{suffix}", "value": True})
                assert "error" in response
        assert r.pure_snapshot() == before and r.cont_commands == r.device_mmio_operations == 0
        assert r.monitor("query-status")["running"] is False
    finally:
        r.close()


def eighteen_sparse_leaves_preserve_enabled_mdss_and_dispcc_owners(args, out):
    r = DsiRig(args, out/"standalone-sparse-ownership")
    try:
        text = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (r.directory/"mtree.txt").write_text(text)
        for bank, base in enumerate(BASES):
            for offset, size in WINDOWS:
                pattern = rf"{base+offset:016x}-{base+offset+size-1:016x}.*dsi{bank}-phy-pll-sparse\.{offset:03x}"
                assert re.search(pattern, text), pattern
            dynamic = DYNAMIC_BASES[bank]
            assert re.search(rf"{dynamic:016x}-{dynamic+3:016x}.*dsi{bank}-dynamic-control", text)
        r.raw_write(DISP_BASE+0x8000, 0x10)
        assert r.raw_read(DISP_BASE+0x8000) == 0x10
        assert r.raw_read(MDSS_BASE) == 1
        mdss_before, disp_before = r.mdss_snapshot(), r.disp_snapshot()
        r.write(0, 0x18, 0x40)
        r.write(1, 0x18, 0)
        for bank in range(2):
            r.reject_read(bank, 0x140)
        assert r.mdss_snapshot() == mdss_before and r.disp_snapshot() == disp_before
        assert r.read(0, 0x18) == 0x40 and r.read(1, 0x18) == 0
        assert sum(size for _offset, size in WINDOWS)*2+8 == 1240
    finally:
        r.close()


def dsi_payload(snapshot):
    """Independent complete v1 wire, including the 8-byte 1:1 Clock field."""
    raw = bytearray(struct.pack(">9I", *(snapshot["globals"][n] for n in GLOBAL_FIELDS)))
    raw.extend(struct.pack(">4Q", *snapshot["physical-bases"], *snapshot["dynamic-bases"]))
    raw.extend(struct.pack(">308I", *(bank[f"{o:03x}"] for bank in snapshot["registers"] for o in REGISTERS)))
    for name in ("active-factor", "pending-factor"):
        raw.extend(struct.pack(">2I", *(p[name] for p in snapshot["plls"])))
    raw.extend(struct.pack(">2qQ", *(p["lock-deadline"] for p in snapshot["plls"]), snapshot["last-xo-hz"]))
    values = ([p["locked"] for p in snapshot["plls"]] +
              [snapshot["power-known"], snapshot["power-on"]] +
              [p["refgen-known"] for p in snapshot["plls"]] +
              [p["refgen-ready"] for p in snapshot["plls"]])
    assert len(values) == 8 and all(type(v) is bool for v in values)
    raw.extend(bytes(values))
    raw.extend(struct.pack(">Q", snapshot["input-period"]))
    assert len(raw) == VMSTATE_BYTES
    return bytes(raw)


def inspect_stream(path, snapshot, dispcc, mdss):
    data, sections = path.read_bytes(), {}
    for label, name, expected in (("dsi", TYPE, dsi_payload(snapshot)),
                                  ("dispcc", "qcom-sm8250-dispcc", dispcc_payload(dispcc)),
                                  ("mdss", "qcom-mdss-gdsc", mdss_payload(mdss))):
        section = unique_section(data, name, 1, len(expected))
        offset = section["payload_offset"]
        actual = data[offset:offset+len(expected)]
        assert actual == expected, (label, [i for i, (a, b) in enumerate(zip(actual, expected)) if a != b][:32])
        payload = path.with_name(path.name+f".{label}-v1.payload.bin")
        payload.write_bytes(actual)
        section.update(payload_file=payload.name, payload_sha256=digest(payload),
                       all_bytes_match_stopped_source_qom=True)
        sections[label] = section
    write_json(path.with_name(path.name+".sections.json"), sections)
    return sections


def provider_snapshot(rig):
    return {"xo.lvl": rig.resource("xo.lvl"), "mmcx.lvl": rig.resource("mmcx.lvl"),
            "dsi_xo_period": rig.qom("input.bi_tcxo.period"),
            "mdss_known": rig.qom("domain0.state-known", MDSS),
            "mdss_on": rig.qom("domain0.powered", MDSS)}


def save_source(rig, path, witness):
    rig.raw_write(RAM, witness)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    snapshot, dispcc, mdss = rig.pure_snapshot(), rig.disp_snapshot(), rig.mdss_snapshot()
    assert all(snapshot["output-periods"][n] == dispcc["inputs"][n] for n in CONNECTED)
    provider = provider_snapshot(rig)
    write_json(rig.directory/"source-before-save.json", {"dsi": snapshot, "dispcc": dispcc,
                                                         "mdss": mdss, "provider": provider})
    rig.monitor("migrate", file_channels(path))
    sent = wait_migration(rig)
    assert sent["status"] == "completed"
    assert rig.monitor("query-status")["status"] == "postmigrate"
    assert rig.pure_snapshot() == snapshot and rig.disp_snapshot() == dispcc
    assert rig.mdss_snapshot() == mdss and provider_snapshot(rig) == provider
    sections = inspect_stream(path, snapshot, dispcc, mdss)
    write_json(rig.directory/"source-query-migrate.json", sent)
    return {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
            "source_directory": rig.directory.name, "source_virtual_ns": rig.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "dispcc": dispcc, "mdss": mdss, "provider": provider,
            "sections": sections, "ram_witness": witness, "stream_modified": False}


def route_display(rig, *, both=True):
    # Fixed DISPCC byte0/byte1/pixel0/pixel1 RCG offsets, selector 1/2.
    for offset, selector in ((0x2110, 1), (0x212c, 2), (0x2098, 1), (0x20b0, 2)):
        if not both and selector == 2:
            continue
        rig.raw_write(DISP_BASE+offset+4, (selector << 8) | 1)
        rig.raw_write(DISP_BASE+offset, 3)
    rig.step(1000)


def prewarm_target(rig, saved):
    at = saved["source_virtual_ns"]
    assert at >= 100_000
    rig.set_clock(at-50_000)
    supply_mdss(rig)
    for bank, rate in ((0, 1_800_000_000), (1, 3_600_000_000)):
        configure_bank(rig, bank, rate, cphy=(bank == 1))
        rig.refgen(bank, True, True)
        start_bank(rig, bank)
        rig.step(LOCK_NS)
        # The explicit incoming-state guard suppresses both timer transitions
        # and provider propagation before DSI's pre_load. This target is dirty
        # and externally powered/ready, but must not be described as locked.
        assert rig.read(bank, 0x6b0) == 0 and not rig.pll(bank, "locked")
        open_outputs(rig, bank)
    route_display(rig)
    for offset in (0x2110, 0x212c, 0x2098, 0x20b0):
        rig.raw_read(DISP_BASE+offset)
    rig.raw_write(DISP_BASE+0x8000, 0x10)
    rig.set_clock(at)
    assert rig.pure_snapshot() != saved["snapshot"]
    assert rig.disp_snapshot() != saved["dispcc"]
    write_json(rig.directory/"target-before-incoming.json", {
        "dsi": rig.pure_snapshot(), "dispcc": rig.disp_snapshot(), "mdss": rig.mdss_snapshot(),
        "incoming_guard_suppresses_lock": True,
        "target_prewarmed_registers_and_refgen_inputs": True,
        "target_prewarmed_locked_carrier": False})


def receive_source(rig, path, saved):
    rig.set_clock(saved["source_virtual_ns"])
    mmio, cont = rig.device_mmio_operations, rig.cont_commands
    rig.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(rig)
    assert received["status"] == "completed"
    assert rig.monitor("query-status")["running"] is False
    first, dispcc, mdss = rig.pure_snapshot(), rig.disp_snapshot(), rig.mdss_snapshot()
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert dispcc == saved["dispcc"] and mdss == saved["mdss"]
    assert all(first["output-periods"][n] == dispcc["inputs"][n] for n in CONNECTED)
    assert provider_snapshot(rig) == saved["provider"]
    assert (rig.device_mmio_operations, rig.cont_commands) == (mmio, cont)
    assert rig.raw_read(RAM) == saved["ram_witness"]
    evidence = {"snapshot": first, "dispcc": dispcc, "mdss": mdss,
                "provider": provider_snapshot(rig), "device_mmio_since_incoming": False,
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
        rig = DsiRig(args, out/name, **options)
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
            "all_1356_payload_bytes_verified": True, "dispcc_1676_bytes_verified": True,
            "mdss_64_bytes_verified": True, **details})
        passed(name, device_mmio_before_first_observation=False, **details)

    try:
        steady = new("migration-steady-source")
        supply_mdss(steady)
        steady.set_clock(200_000)
        enable_bank(steady, 0, 5_000_000_000)
        enable_bank(steady, 1, 2_400_000_000, cphy=True, ssc=True, center=True)
        steady.write(0, 0x10, 0)
        steady.write(0, 0x14, 0x20)  # normal DPHY post-resync clears bit4
        route_display(steady)
        assert_rates(steady, 0, 5_000_000_000, bitdiv=0, pixeldiv=0)
        assert_rates(steady, 1, 2_400_000_000, cphy=True, selector=3)
        high_hz = steady.qom("clk.disp_cc_mdss_pclk0_clk_src.hz", DISP)
        assert high_hz == PERIOD_UNIT//steady.period(0, "phy_pll_out_dsiclk")
        assert high_hz > 0xffffffff
        steady_path = out/"dsi-v1-steady.migration"
        saved_steady = save_source(steady, steady_path, 0xd5100001)
        report["migration_sources"].append(saved_steady)
        target = new("migration-steady-target", incoming=True)
        prewarm_target(target, saved_steady)
        first = receive_source(target, steady_path, saved_steady)
        target.monitor("cont")
        assert_rates(target, 0, 5_000_000_000, bitdiv=0, pixeldiv=0)
        assert_rates(target, 1, 2_400_000_000, cphy=True, selector=3)
        assert target.qom("clk.disp_cc_mdss_pclk0_clk_src.hz", DISP) == high_hz
        target.write(0, 0x3c, 0)
        assert target.hz(0, "vco") == 0 and target.pll(1, "locked")
        assert target.qom("qtest-clock-period", DISP+"/dsi0_phy_pll_out_dsiclk") == 0
        completed("migration_steady_above_uint32_and_cphy_ssc", steady_path, steady, target, first,
                  high_frequency_consumer_hz=high_hz,
                  four_physical_provider_consumer_periods_verified=True,
                  target_prewarmed_locked_carrier=False)

        pending = new("migration-dual-pending-source")
        supply_mdss(pending)
        pending.set_clock(200_000)
        configure_bank(pending, 0, 1_200_000_000)
        configure_bank(pending, 1, 2_400_000_000, cphy=True)
        for bank in range(2):
            pending.refgen(bank, True, True)
        start_bank(pending, 0)
        pending.step(700)
        start_bank(pending, 1)
        pending.step(400)
        assert [pending.pll(i, "lock-deadline")-pending.last_clock for i in range(2)] == [3900, 4600]
        pending_path = out/"dsi-v1-dual-pending.migration"
        saved_pending = save_source(pending, pending_path, 0xd5100002)
        report["migration_sources"].append(saved_pending)
        resumed = new("migration-dual-pending-target", incoming=True)
        prewarm_target(resumed, saved_pending)
        first = receive_source(resumed, pending_path, saved_pending)
        resumed.monitor("cont")
        mmio = resumed.device_mmio_operations
        resumed.step(3899)
        assert all(not resumed.pll(i, "locked") for i in range(2))
        resumed.step(1)
        assert resumed.pll(0, "locked") and not resumed.pll(1, "locked")
        resumed.step(699)
        assert not resumed.pll(1, "locked")
        resumed.step(1)
        assert resumed.pll(1, "locked")
        assert resumed.device_mmio_operations == mmio
        for bank in range(2):
            open_outputs(resumed, bank)
        assert_rates(resumed, 0, 1_200_000_000)
        assert_rates(resumed, 1, 2_400_000_000, cphy=True, selector=3)
        completed("migration_two_distinct_pending_deadlines", pending_path, pending, resumed, first,
                  remaining_pll_ns=[3900, 4600], device_mmio_before_deadline_checks=False,
                  target_prewarmed_locked_carrier=False)

        unknown = new("migration-unknown-refgen-source")
        supply_mdss(unknown)
        unknown.set_clock(200_000)
        configure_bank(unknown, 0, 1_500_000_000, ssc=True)
        configure_bank(unknown, 1, 2_400_000_000, cphy=True)
        unknown.refgen(0, False, False)
        unknown.refgen(1, True, False)
        for bank in range(2):
            start_bank(unknown, bank)
            open_outputs(unknown, bank)
        assert all(not unknown.pll(i, "locked") and not unknown.pll(i, "lock-pending") for i in range(2))
        unknown_path = out/"dsi-v1-unknown-refgen.migration"
        saved_unknown = save_source(unknown, unknown_path, 0xd5100003)
        report["migration_sources"].append(saved_unknown)
        overwritten = new("migration-unknown-refgen-target", incoming=True)
        prewarm_target(overwritten, saved_unknown)
        assert all(overwritten.pll(i, "refgen-known") and overwritten.pll(i, "refgen-ready") for i in range(2))
        first = receive_source(overwritten, unknown_path, saved_unknown)
        assert not first["snapshot"]["plls"][0]["refgen-known"]
        assert first["snapshot"]["plls"][1]["refgen-known"]
        assert all(not p["refgen-ready"] for p in first["snapshot"]["plls"])
        overwritten.monitor("cont")
        overwritten.step(LOCK_NS*2)
        assert all(overwritten.hz(i, "vco") == 0 for i in range(2))
        overwritten.refgen(0, True, True)
        overwritten.step(LOCK_NS)
        assert overwritten.pll(0, "locked") and not overwritten.pll(1, "locked")
        overwritten.refgen(1, True, True)
        overwritten.step(LOCK_NS)
        assert_rates(overwritten, 0, 1_500_000_000)
        assert_rates(overwritten, 1, 2_400_000_000, cphy=True, selector=3)
        completed("migration_unknown_refgen_overwrites_ready_target_inputs", unknown_path,
                  unknown, overwritten, first, known_ready_target_inputs_overwritten=True,
                  target_prewarmed_locked_carrier=False,
                  incoming_guard_suppresses_prewarm_lock=True)

        disabled = new("migration-default-source", reference=None)
        disabled.set_clock(200_000)
        disabled_path = out/"dsi-v1-default.migration"
        saved_disabled = save_source(disabled, disabled_path, 0xd5100004)
        report["migration_sources"].append(saved_disabled)
        for name, path, source, reference in (
                ("enabled-to-default", steady_path, saved_steady, None),
                ("default-to-enabled", disabled_path, saved_disabled, True)):
            rejected = new("migration-reject-"+name, incoming=True, reference=reference)
            rejected.set_clock(source["source_virtual_ns"])
            outcome = load_failure(rejected, path, "reference_profile")
            record = {"name": name, "passed": True, "source_file": path.name,
                      "source_sha256": digest(path), "stream_modified": False, **outcome}
            report["rejected_migrations"].append(record)
            passed("migration_profile_reject_"+name, **outcome)

        original = steady_path.read_bytes()
        section = saved_steady["sections"]["dsi"]
        for name, relative, mask, diagnostic in (
                ("physical-base", 36+7, 1, "physical_base[0]"),
                ("phy-control", 68+REGISTERS.index(0x18)*4+3, 1, "invalid migrated register")):
            absolute = section["payload_offset"]+relative
            changed = bytearray(original)
            changed[absolute] ^= mask
            assert len(changed) == len(original)
            assert sum(a != b for a, b in zip(changed, original)) == 1
            assert sum((a ^ b).bit_count() for a, b in zip(changed, original)) == 1
            path = out/f"dsi-corrupt-{name}.migration"
            path.write_bytes(changed)
            rejected = new("migration-corrupt-"+name, incoming=True)
            rejected.set_clock(saved_steady["source_virtual_ns"])
            outcome = load_failure(rejected, path, diagnostic)
            mutation = {"name": name, "passed": True, "source_file": steady_path.name,
                        "source_sha256": digest(steady_path), "file": path.name,
                        "sha256": digest(path), "stream_modified": True,
                        "relative_payload_offset": relative, "absolute_stream_offset": absolute,
                        "xor_mask": mask, "changed_bytes": 1, "changed_bits": 1}
            report["corrupted_stream_rejections"].append({**mutation, **outcome})
            passed("migration_corrupt_"+name, **outcome,
                   relative_payload_offset=relative, changed_bits=1)
        assert all(digest(out/saved["file"]) == saved["sha256"] for saved in report["migration_sources"])
    finally:
        for rig in rigs:
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
        "schema": "tb371fc-dsi-pll7nm-sm8250-v1", "suite": "dsi-pll7nm-sm8250-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "expected_unmodified_streams": 4,
        "expected_stream_files": 6, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "physical_bases": list(BASES), "dynamic_bases": list(DYNAMIC_BASES),
        "bank_count": 2, "resource_bytes_per_bank": 0x800,
        "registers_per_bank": 154, "pll_registers_per_bank": 145, "shared_phy_words_per_bank": 9,
        "register_count": 308, "sparse_window_count": 18, "mapped_bytes": 1240,
        "output_clock_count": 16, "input_clock_count": 1, "connected_input_clock_count": 1,
        "connected_dispcc_input_count": 4, "unknown_refgen_provider_count": 2,
        "software_binding_clock_count": 52, "pll_count": 2, "modeled_timer_count": 1,
        "mdss_vmstate_version": 1, "mdss_vmstate_payload_bytes": 64,
        "dispcc_vmstate_version": 1, "dispcc_vmstate_payload_bytes": 1676,
        "actual_compatible": "qcom,mdss_dsi_pll_7nm_v4_1", "phy_compatible": "qcom,dsi-phy-v4.1",
        "reference_enabled_by_default": False, "full_source_recipe_required": True,
        "generic_dividers_round_up": True, "fixed_factors_round_down": True,
        "zero_divider_is_passthrough": True, "ssc_modulation_simulated": False,
        "software_aliases_are_hardware_state": False, "shadow_sequence_supported": False,
        "external_route_tap_known": False, "unknown_status_read_as_success": False,
        "dphy_byte_divisor": 8, "cphy_byte_divisor": 7,
        "vco_min_hz": 600_000_000, "vco_max_hz": 5_000_000_000,
        "source_commit": MODULE_COMMIT, "core_source_commit": CORE_COMMIT,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "source_sha256": {"dsi_pll_7nm.c": SOURCE_SHA256, "mdss-7nm-pll-clk.h": BINDING_SHA256,
            "dsi_phy_hw_v4_0.c": "5495ce37c745ddce6dba71910aa1ceac59d6532b4770c7b3a61a01115cd62641",
            "clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2",
            "clk-fixed-factor.c": "2c30bcbf35d84141020db553f2de6d6298a2c355990d464ab5be659317cf3f22"},
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "dsi_execution_observed": False, "analog_pll_behavior_modeled": False,
        "dynamic_execution_observed": False, "firmware_handoff_reproduced": False,
        "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP; source-qualified digital PLL/clock contract, "
                 "explicit unknown REFGEN boundary and independent DSI/DISPCC/MDSS migration. "
                 "The digital lock deadline is a reference assumption, not analog evidence.",
        "binary_unchanged": False, "checks": [], "migrations": [],
        "rejected_migrations": [], "corrupted_stream_rejections": [],
        "migration_sources": [], "actual_stream_files": [], "passed": False,
    }
    try:
        assert len(ORDINARY) == 31
        for index, test in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = DsiRig(args, fixture)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_profile_rejects_all_words_despite_other_enabled_references,
                     paused_tcg_exposes_only_pure_observers_and_never_executes_guest,
                     eighteen_sparse_leaves_preserve_enabled_mdss_and_dispcc_owners):
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
            len({x["name"] for x in report["checks"]}) == EXPECTED_CHECKS and
            all(x["passed"] is True for x in report["checks"]) and
            len(report["migrations"]) == 3 and
            all(x["source_status"] == x["target_status"] == "completed" and
                x["continued_after_restore"] and x["all_1356_payload_bytes_verified"] and
                x["dispcc_1676_bytes_verified"] and x["mdss_64_bytes_verified"] for x in report["migrations"]) and
            len(report["rejected_migrations"]) == len(report["corrupted_stream_rejections"]) == 2 and
            all(x["passed"] is True and x["specific_load_failure_observed"] for x in
                report["rejected_migrations"]+report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 4 and len(report["actual_stream_files"]) == 6 and
            len({x["name"] for x in report["actual_stream_files"]}) == 6 and
            all(x["bytes"] > 0 for x in report["actual_stream_files"]) and report["binary_unchanged"])
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
