#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled SM8250 DSI 7nm v4.1 static reference and actual migration tests.

The literal programming vectors come from the fixed public R11 driver, not
the model's generated table. Finite digital qualification is not evidence of
analog lock, a panel, execution of the stock kernel, or completed Android boot.
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

TYPE, DEVICE = "qcom-sm8250-dsi-pll", "/machine/dsi-pll"
MDSS, DISPCC = "/machine/mdss-gdsc", "/machine/dispcc"
BASE, STRIDE, BANK_SIZE = 0x0ae94400, 0x2000, 0x800
MDSS_BASE, DISPCC_BASE, RAM = 0x0af03000, 0x0af00000, 0x80010000
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
XO, FREF, FRAC_SCALE = 19_200_000, 38_400_000, 1 << 18
PERIOD_UNIT, BIAS_NS, LOCK_NS = 1_000_000_000 << 32, 250, 3334
IDENTITY, VARIANT, VMSTATE_BYTES, EXPECTED_CHECKS = 0x44534934, 0x401, 2926, 36
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_COMMIT = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
# Offsets are relative to each PHY base; the PLL starts at PHY + 0x500.
WINDOWS = ((0x010, 0x10), (0x024, 4), (0x02c, 8), (0x03c, 4),
           (0x128, 4), (0x500, 0xcc), (0x5dc, 0x14), (0x600, 0x164))
REGISTERS = tuple(offset for base, size in WINDOWS for offset in range(base, base+size, 4))
ALL_REGISTERS = tuple(bank*STRIDE+offset for bank in range(2) for offset in REGISTERS)
STATUS = tuple(range(0x6b0, 0x6d0, 4)) + tuple(range(0x700, 0x710, 4)) + (0x718, 0x71c, 0x720)
PHY = (0x10, 0x14, 0x18, 0x1c, 0x24, 0x2c, 0x30, 0x3c, 0x128)
FACTOR = (0x5e0, 0x5e4, 0x5e8, 0x5ec)
SSC = (0x620, 0x624, 0x628, 0x62c, 0x630, 0x634, 0x650)
REQUIRED = tuple(o for o in REGISTERS if o not in PHY+STATUS+(0x528, 0x654))
assert (len(REGISTERS), len(ALL_REGISTERS), len(STATUS), len(REQUIRED)) == (154, 308, 15, 128)

# dsi_pll_init_val(), in write order. The v4.1 branch writes BIAS_CONTROL_1=40.
# Keep the earlier values that commit/hzindep deliberately overwrite.
INIT_WRITES = (
    (0x500, 0x00), (0x508, 0x3f), (0x50c, 0x00), (0x514, 0x00), (0x51c, 0x80),
    (0x528, 0x00), (0x52c, 0x00), (0x530, 0x10), (0x534, 0x20), (0x538, 0x10),
    (0x53c, 0x02), (0x540, 0x1c), (0x548, 0x00), (0x54c, 0x02), (0x550, 0x20),
    (0x554, 0x00), (0x558, 0xff), (0x55c, 0x00), (0x560, 0x0a), (0x564, 0x25),
    (0x568, 0xba), (0x56c, 0x4f), (0x570, 0x0a), (0x574, 0x00), (0x578, 0x0c),
    (0x57c, 0x20), (0x580, 0x00), (0x584, 0xff), (0x588, 0x10), (0x58c, 0x46),
    (0x598, 0x54), (0x59c, 0x00), (0x5a0, 0x00), (0x5a4, 0x40), (0x5ac, 0x04),
    (0x5b0, 0x00), (0x5b4, 0x00), (0x5b8, 0x00), (0x5bc, 0x10), (0x5c0, 0x00),
    (0x5c4, 0x08), (0x5c8, 0x08), (0x5dc, 0x00), (0x600, 0x03), (0x604, 0x00),
    (0x608, 0x00), (0x60c, 0x00), (0x610, 0x00), (0x614, 0x00), (0x618, 0x00),
    (0x61c, 0x00), (0x620, 0x00), (0x624, 0x00), (0x628, 0x00), (0x62c, 0x00),
    (0x630, 0x00), (0x634, 0x00), (0x638, 0x00), (0x63c, 0x00), (0x640, 0x00),
    (0x644, 0x00), (0x648, 0x00), (0x64c, 0x00), (0x650, 0x00), (0x654, 0x00),
    (0x658, 0x40), (0x65c, 0x40), (0x660, 0x0c), (0x664, 0x0a), (0x668, 0xc0),
    (0x66c, 0x00), (0x670, 0x54), (0x674, 0x54), (0x678, 0x4c), (0x67c, 0x4c),
    (0x680, 0x03), (0x684, 0x00), (0x688, 0x00), (0x68c, 0x00), (0x690, 0x80),
    (0x694, 0x06), (0x698, 0x19), (0x69c, 0x00), (0x6a0, 0x00), (0x6a4, 0x40),
    (0x6a8, 0x20), (0x6ac, 0x00), (0x6b0, 0x00), (0x6b4, 0x00), (0x6b8, 0x00),
    (0x6bc, 0x00), (0x6c0, 0x00), (0x6c4, 0x00), (0x6c8, 0x00), (0x6cc, 0x00),
    (0x6d0, 0x00), (0x6d4, 0x02), (0x6d8, 0x11), (0x6dc, 0x00), (0x6e0, 0x00),
    (0x6e4, 0x80), (0x6e8, 0x00), (0x6ec, 0x00), (0x6f0, 0x00), (0x6f4, 0x00),
    (0x6f8, 0x00), (0x6fc, 0x00), (0x700, 0x00), (0x704, 0x00), (0x708, 0x00),
    (0x70c, 0x00), (0x710, 0x00), (0x714, 0x00), (0x718, 0x00), (0x71c, 0x00),
    (0x720, 0x00), (0x724, 0x00), (0x728, 0x1d), (0x72c, 0x1c), (0x730, 0xff),
    (0x734, 0x22), (0x738, 0x09), (0x73c, 0x00), (0x740, 0x00), (0x744, 0x00),
    (0x748, 0x40), (0x74c, 0x00), (0x750, 0x10), (0x754, 0x10), (0x75c, 0x03),
)
HZ_INDEPENDENT = (
    (0x518, 1), (0x504, 3), (0x510, 0), (0x520, 0), (0x524, 0x4e),
    (0x544, 0x40), (0x568, 0xba), (0x578, 0x0c), (0x5a8, 0), (0x5b8, 0),
    (0x5c8, 8), (0x660, 0x0a), (0x668, 0xc0), (0x670, 0x84), (0x670, 0x82),
    (0x678, 0x4c), (0x690, 0x80), (0x590, 0x29), (0x590, 0x2f),
    (0x594, 0x2a), (0x594, 0x3f), (0x760, 0x22),
)
OUTPUT0 = (
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
# The source has this irregular spelling in the bank1 shadow CPHY view.
OUTPUT1 = tuple(n.replace("dsi0", "dsi1").replace(
    "dsi1pll_shadow_cphy_byteclk_src", "dsi1pll_cphy_shadow_byteclk_src") for n in OUTPUT0)
OUTPUTS = OUTPUT0+OUTPUT1
CARRIERS = tuple(f"dsi{bank}_phy_pll_out_{suffix}" for suffix in ("byteclk", "dsiclk") for bank in range(2))
GLOBAL_FIELDS = ("reference-profile", "reference-identity", "source-variant", "bank-count",
                 "words-per-bank", "register-count", "clock-count", "input-count",
                 "window-count", "mapped-bytes", "lock-reference-cycles", "bias-settle-ns",
                 "physical-base", "bank-stride")
BANK_FLAGS = ("pll-locked", "lock-pending", "bias-settled", "reset-asserted", "digital-ready",
              "config-complete", "recipe-valid", "cphy", "external-selected", "external-known",
              "environment-qualified")
BANK_FIELDS = BANK_FLAGS+("bias-deadline", "lock-deadline", "lock-qualify-after",
                          "captured-xo-period", "configured-vco-hz", "active-vco-hz")


def period(hz):
    return PERIOD_UNIT//hz if hz else 0


def observed(hz):
    return PERIOD_UNIT//period(hz) if hz else 0


def nominal(request):
    return (request*FRAC_SCALE//FREF)*FREF//FRAC_SCALE


def ceildiv(value, divisor):
    return (value+divisor-1)//divisor if divisor else 0


def programming(request, cphy=False, ssc=0):
    """Literal init/commit/v4.1 hzindep/default SSC write sequence."""
    factor = request*FRAC_SCALE//FREF
    frac = factor & (FRAC_SCALE-1)
    inverter = 0xa0 if request <= 1_000_000_000 else (0x20 if request <= 2_500_000_000 else
               (0 if request <= 3_020_000_000 else 0x40))
    vco = 8 if request < 1_520_000_000 else (1 if request < 2_990_000_000 else 0)
    writes = list(INIT_WRITES)+[
        (0x5bc, 0x12), (0x5e0, factor >> 18), (0x5e4, frac & 255),
        (0x5e8, (frac >> 8) & 255), (0x5ec, frac >> 16), (0x658, 0x40),
        (0x694, 6), (0x750, 0 if cphy else 0x10), (0x748, inverter),
        (0x758, 1 if request < 3_100_000_000 else 3), (0x740, vco),
    ]+list(HZ_INDEPENDENT)
    if ssc:
        assert ssc in (2, 3)
        per = ((XO+31500//2)//31500)//2-1
        per -= (per+1)%3
        step = (factor*4800*3//(per+1)+500_000)//1_000_000
        writes += list(zip(SSC, (step & 255, step >> 8, per & 255, per >> 8, 2, 0, ssc)))
    return writes


class DsiRig(CameraRig):
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", mdss=True, dispcc=True, rpmh=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh, self.accel = bool(reference), bool(rpmh), accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.qom_get_commands = 0
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
            if BASE <= address < BASE+STRIDE+BANK_SIZE:
                self.dsi_mmio_operations += 1
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

    def reg(self, bank, offset, prefix="reg"):
        return self.qom(f"{prefix}.{bank*STRIDE+offset:04x}")

    def bank(self, bank, field):
        return self.qom(f"bank{bank}-{field}")

    def read(self, bank, offset):
        return self.raw_read(BASE+bank*STRIDE+offset)

    def write(self, bank, offset, value):
        self.raw_write(BASE+bank*STRIDE+offset, value)

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

    def expect_rate(self, name, rate):
        expected = period(rate)
        assert self.qom("clk."+name+".period") == expected, (name, rate)
        assert self.clock_period(name) == expected, (name, rate)
        assert self.qom("clk."+name+".hz") == observed(rate), (name, rate)

    def supply(self, known=True, on=True, collapse=False):
        # The board owns DSI power inputs. Exercise their MDSS supplier instead.
        assert not self.has_rpmh, "Do not override a connected real RPMh power provider"
        self.command(f"set_irq_in {MDSS} parent-on 0 {int(on)}")
        self.command(f"set_irq_in {MDSS} parent-known 0 {int(known)}")
        self.raw_write(MDSS_BASE, int(collapse))
        assert self.qom("power-known") == self.qom("domain0.state-known", MDSS)
        assert self.qom("power-on") == self.qom("domain0.powered", MDSS)

    def pure_snapshot(self, *, child_periods=True):
        result = {
            "globals": {n: self.qom(n) for n in GLOBAL_FIELDS},
            "registers": {f"{o:04x}": self.qom(f"reg.{o:04x}") for o in ALL_REGISTERS},
            "written": {f"{o:04x}": self.qom(f"written.{o:04x}") for o in ALL_REGISTERS},
            "captured": {f"{o:04x}": self.qom(f"captured.{o:04x}") for o in ALL_REGISTERS},
            "banks": [{n: self.bank(b, n) for n in BANK_FIELDS} for b in range(2)],
            "last-xo-period": self.qom("last-xo-period"),
            "power-known": self.qom("power-known"), "power-on": self.qom("power-on"),
            "outputs": {n: self.qom("clk."+n+".period") for n in OUTPUTS},
            "output_hz": {n: self.qom("clk."+n+".hz") for n in OUTPUTS},
        }
        if child_periods:
            result["physical_outputs"] = {n: self.clock_period(n) for n in OUTPUTS}
            assert result["physical_outputs"] == result["outputs"]
            result["input_period"] = self.clock_period("bi_tcxo")
        return result

    def mdss_snapshot(self):
        return {"enabled-reference": self.qom("enabled-reference", MDSS),
                "globals": {n: self.qom(n, MDSS) for n in GDSC_GLOBAL_FIELDS},
                "parent-known": self.qom("parent-known", MDSS),
                "parent-on": self.qom("parent-on", MDSS),
                "domains": [{n: self.qom("domain0."+n, MDSS) for n in GDSC_DOMAIN_FIELDS}]}

    def world(self):
        return {"dsi": self.pure_snapshot(), "mdss": self.mdss_snapshot(),
                "dispcc_carrier_periods": {n: self.clock_period(n, DISPCC) for n in CARRIERS},
                "dispcc_pixel_roots": {f"disp_cc_mdss_pclk{b}_clk_src": {
                    "period": self.clock_period(f"disp_cc_mdss_pclk{b}_clk_src", DISPCC),
                    "hz": self.qom(f"clk.disp_cc_mdss_pclk{b}_clk_src.hz", DISPCC)} for b in range(2)}}

    def reject_write(self, bank, offset, value, *, unsupported=True):
        before = (self.reg(bank, offset), self.reg(bank, offset, "written"), self.reg(bank, offset, "captured"))
        line = (f"{TYPE}: unsupported bits bank{bank} +0x{offset:x}" if unsupported else
                f"{TYPE}: unmodeled write bank{bank} +0x{offset:x} size 4")
        count = self.diagnostic_count(line)
        self.write(bank, offset, value)
        assert self.diagnostic_count(line) == count+1, line
        assert (self.reg(bank, offset), self.reg(bank, offset, "written"), self.reg(bank, offset, "captured")) == before


def configure(r, bank, request=4_500_000_000, *, cphy=False, ssc=0, omit=()):
    r.write(bank, 0x3c, 0)
    r.write(bank, 0x18, 0x40 if cphy else 0)
    for offset, value in programming(request, cphy, ssc):
        if offset not in omit:
            r.write(bank, offset, value)
    r.write(bank, 0x10, 0x11)
    r.write(bank, 0x14, 3 if cphy else 0)
    r.write(bank, 0x2c, 0 if cphy else 0x40)
    r.write(bank, 0x30, 0)
    r.write(bank, 0x128, 0)


def start(r, bank, *, wait_bias=True):
    r.write(bank, 0x24, 0x60)
    r.write(bank, 0x528, 0xc0)
    if wait_bias and not r.bank(bank, "bias-settled"):
        r.step(BIAS_NS)
    r.write(bank, 0x3c, 1)


def finish(r, bank, *, gates=True):
    deadline = r.bank(bank, "lock-deadline")
    assert deadline > r.last_clock
    r.step(deadline-r.last_clock)
    assert r.bank(bank, "pll-locked") is True
    assert r.read(bank, 0x6b0) == 1
    if gates:
        open_gates(r, bank)


def open_gates(r, bank):
    r.write(bank, 0x128, 1)
    r.write(bank, 0x128, 0)
    r.write(bank, 0x30, 4)
    r.write(bank, 0x14, r.reg(bank, 0x14) | 0x30)


def run_bank(r, bank, request=4_500_000_000, **kwargs):
    configure(r, bank, request, **kwargs)
    start(r, bank)
    finish(r, bank)


def expect_bank(r, bank, request, *, cphy=False, outdiv=0, bitdiv=1,
                pixeldiv=1, selector=0, external=False, full_rate=True, gated=True):
    """CCF integer divider ceil, fixed factors floor; then Clock quantization."""
    vco = nominal(request)
    out = ceildiv(vco, 1 << outdiv)
    bit = ceildiv(out, bitdiv) if not external and (cphy or full_rate) and bitdiv else 0
    post_bit, post_vco = bit//2, out//4
    dbyte, cbyte, csource, cpixel, dpixel, dsource = 0, 0, 0, 0, 0, 0
    if cphy:
        cbyte, csource = bit//7, out*2//7
        cmux = csource if selector == 3 and not external else 0
        cpixel = ceildiv(cmux, pixeldiv)
        dmux = 0
    else:
        dbyte = bit//8
        dmux = (bit if selector == 0 else post_bit) if selector <= 1 else 0
        dpixel = ceildiv(dmux, pixeldiv)
        cmux = 0
    rates = [vco, out, bit, dbyte, post_bit, post_vco,
             (cbyte if cphy else dbyte) if gated else 0, dmux, dpixel,
             (cpixel if cphy else dpixel) if gated else 0,
             vco, out, bit, dbyte, post_bit, post_vco, dmux, dpixel,
             cbyte, csource, cmux, cpixel, cbyte, csource, cmux, cpixel]
    assert len(rates) == 26
    for name, hz in zip((OUTPUT0, OUTPUT1)[bank], rates):
        r.expect_rate(name, hz)
    assert r.bank(bank, "configured-vco-hz") == r.bank(bank, "active-vco-hz") == vco
    return rates


def expect_no_capture(r, bank):
    assert not r.bank(bank, "pll-locked") and not r.bank(bank, "lock-pending")
    assert r.bank(bank, "lock-deadline") == r.bank(bank, "lock-qualify-after") == -1
    assert r.bank(bank, "captured-xo-period") == 0
    assert not r.bank(bank, "digital-ready") and not r.bank(bank, "reset-asserted")
    assert all(r.reg(bank, o, "captured") == 0 for o in REGISTERS)
    for name in (OUTPUT0, OUTPUT1)[bank]:
        r.expect_rate(name, 0)


def cold_identity_and_pure_observers(r):
    expected = (1, IDENTITY, VARIANT, 2, 154, 308, 52, 1, 16, 1232, 64, 250, BASE, STRIDE)
    assert tuple(r.qom(n) for n in GLOBAL_FIELDS) == expected
    assert r.qom("dynamic-sequencer-supported") is False
    assert r.qom("phy-refgen-ready-supported") is False
    before = r.pure_snapshot()
    assert set(before["registers"].values()) == {0}
    assert set(before["written"].values()) == {False}
    assert set(before["captured"].values()) == {0}
    assert before["input_period"] == before["last-xo-period"] == period(XO)
    assert set(before["outputs"].values()) == {0}
    for bank in before["banks"]:
        assert not bank["pll-locked"] and not bank["config-complete"] and not bank["recipe-valid"]
        assert bank["bias-deadline"] == bank["lock-deadline"] == bank["lock-qualify-after"] == -1
    count, cont, now = r.device_mmio_operations, r.cont_commands, r.last_clock
    assert r.pure_snapshot() == before
    assert (r.device_mmio_operations, r.cont_commands, r.last_clock) == (count, cont, now)
    return {"pure_snapshot_without_mmio_or_time": True}


def literal_v4_1_programming_and_overwrites_both_banks(r):
    r.supply()
    for bank, rate, cphy in ((0, 4_500_000_000, False), (1, 2_990_000_000, True)):
        configure(r, bank, rate, cphy=cphy)
        expected = dict(programming(rate, cphy))
        for offset, value in expected.items():
            assert r.reg(bank, offset) == value
            assert r.reg(bank, offset, "written") is (offset not in STATUS)
            if offset not in STATUS or offset == 0x6b0:
                assert r.read(bank, offset) == value
        assert r.bank(bank, "config-complete") and r.bank(bank, "recipe-valid")
        assert r.bank(bank, "configured-vco-hz") == nominal(rate)
        expect_no_capture(r, bank)
        for offset, value in ((0x5bc, 0x12), (0x670, 0x82), (0x590, 0x2f),
                              (0x594, 0x3f), (0x6a4, 0x40), (0x734, 0x22), (0x760, 0x22)):
            assert r.reg(bank, offset) == value
    return {"init_write_count": len(INIT_WRITES), "required_words_per_bank": 128,
            "overwritten_final_values_verified": True}


def bias_then_lock_has_exact_finite_boundaries(r):
    r.supply()
    configure(r, 0)
    r.set_clock(100_000)
    r.write(0, 0x24, 0x60)
    r.write(0, 0x528, 0xc0)
    assert r.bank(0, "bias-deadline") == 100_250
    r.step(249)
    assert not r.bank(0, "bias-settled")
    r.step(1)
    assert r.bank(0, "bias-settled") and r.bank(0, "bias-deadline") == -1
    r.write(0, 0x3c, 1)
    assert r.bank(0, "lock-qualify-after") == 100_250
    assert r.bank(0, "lock-deadline") == 103_584
    capture = tuple(r.reg(0, o, "captured") for o in REGISTERS)
    for _ in range(3):
        assert r.read(0, 0x6b0) == 0 and r.bank(0, "lock-pending")
    assert r.last_clock == 100_250 and tuple(r.reg(0, o, "captured") for o in REGISTERS) == capture
    r.step(3333)
    assert r.read(0, 0x6b0) == 0
    r.step(1)
    assert r.read(0, 0x6b0) == 1 and not r.bank(0, "lock-pending")
    expect_bank(r, 0, 4_500_000_000, gated=False)
    open_gates(r, 0)
    expect_bank(r, 0, 4_500_000_000)
    return {"bias_ns": 250, "lock_cycles": 64, "lock_ns_ceiling": 3334}


def start_before_bias_waits_for_both_independent_deadlines(r):
    r.supply()
    for bank in range(2):
        configure(r, bank)
    r.set_clock(200_000)
    for bank in range(2):
        start(r, bank, wait_bias=False)
        assert r.bank(bank, "bias-deadline") == 200_250
        assert r.bank(bank, "lock-deadline") == 203_584
    r.step(249)
    assert all(not r.bank(b, "bias-settled") and not r.bank(b, "pll-locked") for b in range(2))
    r.step(1)
    assert all(r.bank(b, "bias-settled") and not r.bank(b, "pll-locked") for b in range(2))
    r.step(3333)
    assert all(not r.bank(b, "pll-locked") for b in range(2))
    r.step(1)
    for bank in range(2):
        assert r.bank(bank, "pll-locked")
        open_gates(r, bank)
        expect_bank(r, bank, 4_500_000_000)


def pending_stop_cancels_capture_without_late_ack(r):
    r.supply()
    configure(r, 0)
    start(r, 0)
    r.step(LOCK_NS-1)
    r.write(0, 0x3c, 0)
    expect_no_capture(r, 0)
    r.step(10_000)
    expect_no_capture(r, 0)
    r.write(0, 0x3c, 1)
    assert r.bank(0, "lock-deadline") == r.last_clock+LOCK_NS
    finish(r, 0)
    expect_bank(r, 0, 4_500_000_000)


def pending_recipe_change_requires_a_new_start_edge(r):
    r.supply()
    configure(r, 0)
    start(r, 0)
    deadline = r.bank(0, "lock-deadline")
    r.write(0, 0x508, 0x3f)
    assert r.bank(0, "lock-deadline") == deadline
    r.write(0, 0x508, 0x3e)
    expect_no_capture(r, 0)
    r.write(0, 0x508, 0x3f)
    r.write(0, 0x3c, 1)  # Stored high is not a new request edge.
    r.step(10_000)
    expect_no_capture(r, 0)
    r.write(0, 0x3c, 0)
    r.write(0, 0x3c, 1)
    finish(r, 0)
    expect_bank(r, 0, 4_500_000_000)


def required_zero_words_and_fixed_recipe_are_not_reset_defaults(r):
    r.supply()
    configure(r, 0, omit=(0x500,))
    assert r.reg(0, 0x500) == 0 and r.reg(0, 0x500, "written") is False
    assert not r.bank(0, "config-complete")
    start(r, 0)
    r.step(10_000)
    expect_no_capture(r, 0)
    r.write(0, 0x500, 0)
    r.write(0, 0x508, 0x3e)
    assert r.bank(0, "config-complete") and not r.bank(0, "recipe-valid")
    r.write(0, 0x3c, 0)
    r.write(0, 0x3c, 1)
    r.step(10_000)
    expect_no_capture(r, 0)
    r.write(0, 0x508, 0x3f)
    r.write(0, 0x3c, 0)
    r.write(0, 0x3c, 1)
    finish(r, 0)


def raw_request_threshold_triplets_and_five_ghz_use_exact_periods(r):
    r.supply()
    requests = [600_000_000, 4_500_000_000, 5_000_000_000]
    thresholds = (1_000_000_000, 1_520_000_000, 2_500_000_000,
                  2_990_000_000, 3_020_000_000, 3_100_000_000)
    requests += [t+d for t in thresholds for d in (-1, 0, 1)]
    records = []
    for bank in range(2):
        for request in requests:
            run_bank(r, bank, request, cphy=bool(bank))
            rate = nominal(request)
            r.expect_rate((OUTPUT0, OUTPUT1)[bank][0], rate)
            assert r.bank(bank, "active-vco-hz") == rate
            if not bank:
                r.expect_rate("dsi0_phy_pll_out_dsiclk", rate)
            records.append({"bank": bank, "request_hz": request, "nominal_hz": rate,
                            "period": period(rate), "observed_hz": observed(rate)})
    assert nominal(4_500_000_000) == 4_500_000_000 and observed(4_500_000_000) == 4_500_000_004
    assert nominal(5_000_000_000) == 4_999_999_951
    assert period(nominal(5_000_000_000)) == 858_993_467
    assert observed(nominal(5_000_000_000)) == 4_999_999_954
    return {"raw_request_vectors": records, "comparison_uses_period_not_tolerance": True}


def wrong_threshold_and_out_of_range_factor_do_not_lock(r):
    r.supply()
    records = []
    for request, offset, wrong in ((1_500_000_000, 0x740, 1),
                                  (2_600_000_000, 0x748, 0x20),
                                  (3_200_000_000, 0x758, 1),
                                  (599_000_000, None, None),
                                  (5_000_001_000, None, None)):
        configure(r, 0, request)
        if offset is not None:
            r.write(0, offset, wrong)
        assert not r.bank(0, "recipe-valid")
        start(r, 0)
        r.step(10_000)
        expect_no_capture(r, 0)
        records.append({"request_hz": request, "wrong_offset": offset, "wrong_value": wrong})
    return {"rejected_vectors": records,
            "out_of_range_request_that_quantizes_identically_is_not_claimed_distinguishable": True}


def source_default_ssc_downspread_center_and_off(r):
    r.supply()
    for bank in range(2):
        for ssc in (0, 2, 3):
            run_bank(r, bank, 2_999_999_999, cphy=bool(bank), ssc=ssc)
            values = dict(programming(2_999_999_999, bool(bank), ssc))
            assert [r.reg(bank, o) for o in SSC] == [values[o] for o in SSC]
            expect_bank(r, bank, 2_999_999_999, cphy=bool(bank), selector=3 if bank else 0)
    return {"ssc_programming_profiles": [0, 2, 3], "analog_modulation_claimed": False}


def inconsistent_ssc_fields_do_not_gain_lock(r):
    r.supply()
    for control, offset, wrong in ((0, 0x620, 1), (2, 0x628, 0x2f),
                                   (3, 0x630, 1), (2, 0x650, 1)):
        configure(r, 0, ssc=control)
        r.write(0, offset, wrong)
        assert not r.bank(0, "recipe-valid")
        start(r, 0)
        r.step(10_000)
        expect_no_capture(r, 0)


def prelock_reset_pulse_cannot_prepare_final_carriers(r):
    r.supply()
    configure(r, 0)
    start(r, 0)
    open_gates(r, 0)  # Deliberately issue the pulse before LOCK.
    assert not r.bank(0, "reset-asserted") and not r.bank(0, "digital-ready")
    finish(r, 0, gates=False)
    expect_bank(r, 0, 4_500_000_000, gated=False)
    r.write(0, 0x128, 0)
    assert not r.bank(0, "digital-ready")
    r.write(0, 0x128, 1)
    assert r.bank(0, "reset-asserted") and not r.bank(0, "digital-ready")
    r.expect_rate("dsi0_phy_pll_out_dsiclk", 0)
    r.write(0, 0x128, 0)
    assert not r.bank(0, "reset-asserted") and r.bank(0, "digital-ready")
    expect_bank(r, 0, 4_500_000_000)


def dphy_dividers_muxes_and_gates_keep_separate_roles(r):
    r.supply()
    request = 4_500_000_123
    run_bank(r, 0, request)
    for bitdiv, pixeldiv, mux in ((3, 7, 0), (7, 3, 1), (15, 15, 0),
                                  (0, 1, 0), (1, 0, 0), (1, 1, 2), (1, 1, 3)):
        r.write(0, 0x10, bitdiv | pixeldiv << 4)
        r.write(0, 0x14, 0x30 | mux)
        assert r.bank(0, "pll-locked")
        expect_bank(r, 0, request, bitdiv=bitdiv, pixeldiv=pixeldiv, selector=mux)
    r.write(0, 0x10, 0x11)
    for cfg1, ctrl3, full, gated in ((0, 4, True, False), (0x10, 4, True, False),
                                    (0x20, 4, True, False), (0x30, 0, True, False),
                                    (0x30, 4, False, True), (0x30, 4, True, True)):
        r.write(0, 0x14, cfg1)
        r.write(0, 0x30, ctrl3)
        r.write(0, 0x2c, 0x40 if full else 0)
        assert r.bank(0, "pll-locked")
        expect_bank(r, 0, request, full_rate=full, gated=gated)


def cphy_clock_views_and_unknown_external_selection(r):
    r.supply()
    for bank in range(2):
        run_bank(r, bank, 4_500_000_123, cphy=True)
        for bitdiv, pixeldiv, mux in ((3, 7, 3), (7, 3, 3), (1, 1, 0), (0, 1, 3), (1, 0, 3)):
            r.write(bank, 0x10, bitdiv | pixeldiv << 4)
            r.write(bank, 0x14, 0x30 | mux)
            expect_bank(r, bank, 4_500_000_123, cphy=True, bitdiv=bitdiv,
                        pixeldiv=pixeldiv, selector=mux)
        r.write(bank, 0x10, 0x11)
        r.write(bank, 0x14, 0x37)
        assert r.bank(bank, "external-selected") and not r.bank(bank, "external-known")
        expect_bank(r, bank, 4_500_000_123, cphy=True, selector=3, external=True)
    return {"normal_and_shadow_views_verified": 52, "external_carrier_kept_unknown": True}


def shared_phy_fields_and_bank_storage_are_independent(r):
    r.supply()
    for bank in range(2):
        run_bank(r, bank, 4_500_000_000-bank*300_000_000)
    before1 = {o: (r.reg(1, o), r.reg(1, o, "captured")) for o in REGISTERS}
    r.write(0, 0x10, (r.read(0, 0x10) & 0xf0) | 3)
    r.write(0, 0x10, (r.read(0, 0x10) & 0x0f) | 0x70)
    assert r.reg(0, 0x10) == 0x73
    r.write(0, 0x14, (r.read(0, 0x14) & ~3) | 1)
    assert r.reg(0, 0x14) == 0x31
    expect_bank(r, 0, 4_500_000_000, bitdiv=3, pixeldiv=7, selector=1)
    r.write(0, 0x14, r.read(0, 0x14) | 4)
    expect_bank(r, 0, 4_500_000_000, bitdiv=3, pixeldiv=7, selector=1, external=True)
    r.reject_write(0, 0x14, 0x3d)  # Bit3 has no established source meaning.
    r.write(0, 0x18, 0x40)
    expect_no_capture(r, 0)
    assert {o: (r.reg(1, o), r.reg(1, o, "captured")) for o in REGISTERS} == before1
    expect_bank(r, 1, 4_200_000_000)


def power_loss_clears_qualification_but_retains_raw_words(r):
    r.supply()
    for bank in range(2):
        run_bank(r, bank)
    raw = {o: r.qom(f"reg.{o:04x}") for o in ALL_REGISTERS}
    r.supply(known=False, on=True)
    assert not r.qom("power-known")
    assert {o: r.qom(f"reg.{o:04x}") for o in ALL_REGISTERS} == raw
    assert not any(r.qom(f"written.{o:04x}") for o in ALL_REGISTERS)
    for bank in range(2):
        expect_no_capture(r, bank)
    r.supply()
    r.step(10_000)
    for bank in range(2):
        r.write(bank, 0x3c, 0)
        r.write(bank, 0x3c, 1)
        expect_no_capture(r, bank)
    run_bank(r, 0)
    expect_bank(r, 0, 4_500_000_000)
    expect_no_capture(r, 1)
    r.supply(known=True, on=False)
    assert r.qom("power-known") and not r.qom("power-on")
    expect_no_capture(r, 0)


def programming_with_unknown_or_off_supply_cannot_qualify(r):
    for known, on in ((False, True), (True, False)):
        r.supply(known, on)
        configure(r, 0)
        start(r, 0, wait_bias=False)
        r.step(10_000)
        assert not r.bank(0, "config-complete")
        assert not any(r.reg(0, o, "written") for o in REGISTERS)
        expect_no_capture(r, 0)
        r.supply()
        r.write(0, 0x500, 0)  # One retained zero is insufficient.
        r.write(0, 0x3c, 0)
        r.write(0, 0x3c, 1)
        r.step(10_000)
        expect_no_capture(r, 0)
    run_bank(r, 0)
    expect_bank(r, 0, 4_500_000_000)


def live_outdiv_all_four_values_preserves_lock_and_capture(r):
    r.supply()
    run_bank(r, 0, 4_500_000_123)
    before = {o: r.reg(0, o, "captured") for o in REGISTERS}
    for divider in range(4):
        r.write(0, 0x654, divider)
        assert r.bank(0, "pll-locked") and not r.bank(0, "lock-pending")
        expect_bank(r, 0, 4_500_000_123, outdiv=divider)
    assert {o: r.reg(0, o, "captured") for o in REGISTERS} == before
    r.reject_write(0, 0x654, 4)


def qualification_changes_revoke_lock_and_final_carriers(r):
    r.supply()
    changes = ((0x508, 0x3e), (0x5e4, 1), (0x528, 0),
               (0x18, 0x40), (0x24, 0x40), (0x3c, 0))
    for offset, value in changes:
        run_bank(r, 0)
        r.write(0, offset, value)
        expect_no_capture(r, 0)
        r.step(10_000)
        expect_no_capture(r, 0)


def status_zero_writes_and_reserved_bits_are_atomic(r):
    r.supply()
    run_bank(r, 0)
    before = r.pure_snapshot()
    for bank in range(2):
        for offset in STATUS:
            r.write(bank, offset, 0)
            r.reject_write(bank, offset, 1)
            assert r.reg(bank, offset, "written") is False
            if offset != 0x6b0:
                line = f"{TYPE}: unmodeled read bank{bank} +0x{offset:x} size 4"
                count = r.diagnostic_count(line)
                r.read(bank, offset)
                assert r.diagnostic_count(line) == count+1
        for offset, mask in ((0x10, 0xff), (0x14, 0x37), (0x18, 0x40), (0x1c, 1),
                              (0x24, 0x7f), (0x2c, 0x40), (0x30, 4), (0x3c, 1),
                              (0x128, 1), (0x500, 0xff), (0x5ec, 3), (0x654, 3)):
            bit = next(1 << i for i in range(32) if not (mask & (1 << i)))
            r.reject_write(bank, offset, r.reg(bank, offset) | bit)
    assert r.read(0, 0x6b0) == 1
    assert r.pure_snapshot() == before


def exact_sparse_leaves_holes_access_shapes_and_alias(r):
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory/"mtree.txt").write_text(tree)
    matches = re.findall(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ([^\s]+)", tree)
    actual = {(int(a, 16)-BASE, int(b, 16)-int(a, 16)+1)
              for a, b, name in matches if name.startswith(TYPE+".bank")}
    expected = {(b*STRIDE+o, size) for b in range(2) for o, size in WINDOWS}
    assert actual == expected and sum(size for _, size in actual) == 1232
    mdss_ranges = {(int(a, 16), int(b, 16)) for a, b, name in matches
                   if name == "qcom-mdss-gdsc.mdss-core-control"}
    assert mdss_ranges == {(MDSS_BASE, MDSS_BASE+3)}, mdss_ranges
    holes = (0, 0x20, 0x28, 0x34, 0x40, 0x124, 0x12c, 0x140, 0x260,
             0x4fc, 0x5cc, 0x5f0, 0x764, 0x7fc, 0x800)
    assert not set(holes) & set(REGISTERS)
    start_count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    rejected = []
    for bank in range(2):
        for offset in holes:
            r.read(bank, offset)
            r.write(bank, offset, 0xffffffff)
            rejected += [(op, BASE+bank*STRIDE+offset, 4, "(null)", "rejected") for op in ("read", "write")]
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[start_count:] == rejected
    for bank in range(2):
        for operation in ("read", "write"):
            for width, size in (("b", 1), ("w", 2)):
                previous = len(core_diagnostics(r.log_path.read_text(errors="replace")))
                command = f"{operation}{width} {BASE+bank*STRIDE+0x10:#x}" + (" 0xff" if operation == "write" else "")
                r.command(command)
                new = core_diagnostics(r.log_path.read_text(errors="replace"))[previous:]
                assert len(new) == 1 and new[0][0] == operation and new[0][2] == size and "invalid size" in new[0][-1]
            previous = len(core_diagnostics(r.log_path.read_text(errors="replace")))
            r.command(f"{operation}l {BASE+bank*STRIDE+0x11:#x}" + (" 0xffffffff" if operation == "write" else ""))
            new = core_diagnostics(r.log_path.read_text(errors="replace"))[previous:]
            assert new and all(row[0] == operation and "invalid size" in row[-1] for row in new)
        assert r.reg(bank, 0x10) == r.reg(bank, 0x14) == 0
        # A 64-bit request at a 4-byte leaf commits its low word, then fails
        # at the actual adjacent hole; no transaction-wide rollback is invented.
        previous = len(core_diagnostics(r.log_path.read_text(errors="replace")))
        r.command(f"writeq {BASE+bank*STRIDE+0x128:#x} 0x5a5a5a5a00000001")
        assert r.reg(bank, 0x128) == 1
        assert core_diagnostics(r.log_path.read_text(errors="replace"))[previous:] == [
            ("write", BASE+bank*STRIDE+0x12c, 4, "(null)", "rejected")]
        r.write(bank, 0x760, 0x22)
        assert r.read(bank, 0x760) == 0x22
        properties = [x["name"] for x in r.monitor("qom-list", {"path": DEVICE})]
        assert properties.count(f"reg.{bank*STRIDE+0x760:04x}") == 1
    return {"exact_leaf_count": 16, "exact_mapped_bytes": 1232,
            "phy_status_0x140_is_a_hole": True, "pll_0x260_has_one_physical_word": True}


def four_real_dispcc_carriers_and_high_pixel_rate(r):
    r.supply()
    requests = (4_500_000_000, 5_000_000_000)
    for bank, request in enumerate(requests):
        run_bank(r, bank, request)
    for bank, request in enumerate(requests):
        for suffix, hz in (("byteclk", nominal(request)//8), ("dsiclk", nominal(request))):
            name = f"dsi{bank}_phy_pll_out_{suffix}"
            assert r.clock_period(name, DISPCC) == r.clock_period(name) == period(hz)
            response = r.raw_monitor("qom-set", {"path": DISPCC, "property": "qtest-"+name+"-hz", "value": 1})
            assert "error" in response and "connected" in response["error"]["desc"]
    # Source selectors 1/2 route DSI0/1 directly to their two pixel RCGs.
    for bank, request in enumerate(requests):
        command = (0x2098, 0x20b0)[bank]
        r.raw_write(DISPCC_BASE+command+4, ((bank+1) << 8) | 1)
        r.raw_write(DISPCC_BASE+command, 3)
        r.step(1000)
        assert r.raw_read(DISPCC_BASE+command) & 1 == 0
        name = f"disp_cc_mdss_pclk{bank}_clk_src"
        carrier_hz = observed(nominal(request))
        assert r.qom("clk."+name+".hz", DISPCC) == observed(carrier_hz)
        assert r.clock_period(name, DISPCC) == period(carrier_hz)
    r.write(0, 0x14, 0)
    assert r.clock_period("dsi0_phy_pll_out_dsiclk", DISPCC) == 0
    assert r.clock_period("disp_cc_mdss_pclk0_clk_src", DISPCC) == 0
    assert r.clock_period("dsi1_phy_pll_out_dsiclk", DISPCC) == period(nominal(requests[1]))


def connected_xo_and_all_state_observers_are_readonly(r):
    before = r.pure_snapshot()
    properties = [(n, r.qom(n)) for n in GLOBAL_FIELDS]+[
        ("reg.0500", 0), ("written.0500", False), ("captured.0500", 0),
        ("bank0-pll-locked", False), ("bank0-lock-deadline", -1),
        ("bank0-configured-vco-hz", 0), ("power-known", True), ("power-on", False),
        ("clk.dsi0pll_vco_clk.period", 0), ("clk.dsi0pll_vco_clk.hz", 0)]
    for name, value in properties:
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in response and "not writable" in response["error"]["desc"]
    for value in (-1, 1 << 64, "19200000", True, 0, 1, XO):
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-bi_tcxo-hz", "value": value})
        assert "error" in response
        if type(value) is int and 0 <= value < 1 << 64:
            assert "connected" in response["error"]["desc"]
    assert r.pure_snapshot() == before
    return {"connected_xo_override_rejected": True}


def system_reset_cancels_work_and_replays_supplier_collapse(r):
    r.supply()
    run_bank(r, 0)
    configure(r, 1)
    start(r, 1, wait_bias=False)
    assert r.bank(1, "lock-pending")
    input_period = r.clock_period("bi_tcxo")
    r.events.clear()
    r.monitor("system_reset")
    r.wait_for(lambda: r.reg(0, 0x508) == r.reg(1, 0x508) == 0, "DSI reset witnesses")
    assert any(e.get("event") == "RESET" for e in r.events)
    assert all(r.qom(f"reg.{o:04x}") == 0 for o in ALL_REGISTERS)
    assert not any(r.qom(f"written.{o:04x}") for o in ALL_REGISTERS)
    assert r.clock_period("bi_tcxo") == input_period
    assert r.qom("domain0.control", MDSS) == 1
    assert r.qom("power-known") == r.qom("domain0.state-known", MDSS)
    assert r.qom("power-on") == r.qom("domain0.powered", MDSS) is False
    for bank in range(2):
        expect_no_capture(r, bank)
        assert r.bank(bank, "bias-deadline") == -1
    r.step(10_000)
    for bank in range(2):
        expect_no_capture(r, bank)


def real_rpmh_xo_vote_loss_revokes_active_and_pending_banks(r):
    assert r.has_rpmh and r.clock_period("bi_tcxo") == 0
    assert r.resource("mmcx.lvl", "present") == r.resource("xo.lvl", "present") == 1
    r.run_one("mmcx.lvl", 1)
    r.raw_write(MDSS_BASE, 0)
    assert r.resource("mmcx.lvl") == 1 and r.qom("power-known") and r.qom("power-on")
    r.run_one("xo.lvl", 1)
    assert r.clock_period("bi_tcxo") == period(XO)
    run_bank(r, 0)
    configure(r, 1)
    start(r, 1, wait_bias=False)
    assert r.bank(0, "pll-locked") and r.bank(1, "lock-pending")
    r.run_one("xo.lvl", 0)
    assert r.clock_period("bi_tcxo") == r.qom("last-xo-period") == 0
    assert r.resource("mmcx.lvl") == 1 and r.qom("power-known") and r.qom("power-on")
    for bank in range(2):
        expect_no_capture(r, bank)
        assert r.bank(bank, "config-complete")
    assert all(r.clock_period(n, DISPCC) == 0 for n in CARRIERS)
    r.run_one("xo.lvl", 1)
    assert r.clock_period("bi_tcxo") == period(XO)
    for bank in range(2):
        r.write(bank, 0x3c, 1)
    r.step(10_000)
    for bank in range(2):
        expect_no_capture(r, bank)
        r.write(bank, 0x3c, 0)
        r.write(bank, 0x3c, 1)
        assert r.bank(bank, "lock-deadline") == r.last_clock+LOCK_NS
    r.step(LOCK_NS-1)
    assert all(not r.bank(b, "pll-locked") for b in range(2))
    r.step(1)
    for bank in range(2):
        assert r.bank(bank, "pll-locked")
        open_gates(r, bank)
        expect_bank(r, bank, 4_500_000_000)
    return {"runtime_xo_loss_exercised": True, "actual_xo_resource": "xo.lvl",
            "actual_supply_resource": "mmcx.lvl", "mdss_remained_on": True,
            "active_and_pending_capture_cancelled": True, "new_request_edge_required": True}


ORDINARY = (
    cold_identity_and_pure_observers,
    literal_v4_1_programming_and_overwrites_both_banks,
    bias_then_lock_has_exact_finite_boundaries,
    start_before_bias_waits_for_both_independent_deadlines,
    pending_stop_cancels_capture_without_late_ack,
    pending_recipe_change_requires_a_new_start_edge,
    required_zero_words_and_fixed_recipe_are_not_reset_defaults,
    raw_request_threshold_triplets_and_five_ghz_use_exact_periods,
    wrong_threshold_and_out_of_range_factor_do_not_lock,
    source_default_ssc_downspread_center_and_off,
    inconsistent_ssc_fields_do_not_gain_lock,
    prelock_reset_pulse_cannot_prepare_final_carriers,
    dphy_dividers_muxes_and_gates_keep_separate_roles,
    cphy_clock_views_and_unknown_external_selection,
    shared_phy_fields_and_bank_storage_are_independent,
    power_loss_clears_qualification_but_retains_raw_words,
    programming_with_unknown_or_off_supply_cannot_qualify,
    live_outdiv_all_four_values_preserves_lock_and_capture,
    qualification_changes_revoke_lock_and_final_carriers,
    status_zero_writes_and_reserved_bits_are_atomic,
    exact_sparse_leaves_holes_access_shapes_and_alias,
    four_real_dispcc_carriers_and_high_pixel_rate,
    connected_xo_and_all_state_observers_are_readonly,
    system_reset_cancels_work_and_replays_supplier_collapse,
    real_rpmh_xo_vote_loss_revokes_active_and_pending_banks,
)


def default_off_rejects_all_words_with_sibling_profiles_enabled(args, out):
    r = DsiRig(args, out/"default-off", reference=None)
    try:
        assert not r.qom("enabled-reference") and r.qom("reference-profile") == 0
        r.supply()
        before = r.world()
        count = r.log_path.read_text(errors="replace").count(TYPE+":")
        for bank in range(2):
            for offset in REGISTERS:
                r.read(bank, offset)
                r.write(bank, offset, 0)
        assert r.log_path.read_text(errors="replace").count(TYPE+":") == count+616
        r.step(10_000)
        assert r.world() == before
        assert set(before["dispcc_carrier_periods"].values()) == {0}
        assert r.qom("enabled-reference", MDSS) and r.qom("enabled-reference", DISPCC)
    finally:
        r.close()


def paused_tcg_has_no_mutating_fixture_or_guest_execution(args, out):
    r = DsiRig(args, out/"paused-tcg", accel="tcg")
    try:
        assert not r.monitor("query-status")["running"]
        before = r.pure_snapshot(child_periods=False)
        names = {p["name"] for p in r.monitor("qom-list", {"path": DEVICE})}
        assert "qtest-bi_tcxo-hz" not in names
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-bi_tcxo-hz", "value": XO})
        assert "error" in response and "not found" in response["error"]["desc"]
        assert r.pure_snapshot(child_periods=False) == before
        assert r.cont_commands == r.device_mmio_operations == 0
        assert not r.monitor("query-status")["running"]
    finally:
        r.close()


def dsi_payload(snapshot):
    """Independent complete v1 wire; physical output Clock objects are derived."""
    raw = bytearray(struct.pack(">12I2Q", *(snapshot["globals"][n] for n in GLOBAL_FIELDS)))
    for group, code in (("registers", "I"), ("written", "B"), ("captured", "I")):
        values = [snapshot[group][f"{o:04x}"] for o in ALL_REGISTERS]
        if group == "written":
            assert all(type(v) is bool for v in values)
        raw.extend(struct.pack(">308"+code, *values))
    raw.extend(struct.pack(">2QQ", *(b["captured-xo-period"] for b in snapshot["banks"]),
                           snapshot["last-xo-period"]))
    for field in ("bias-deadline", "lock-deadline", "lock-qualify-after"):
        raw.extend(struct.pack(">2q", *(b[field] for b in snapshot["banks"])))
    for field in ("pll-locked", "bias-settled", "reset-asserted", "digital-ready"):
        values = [b[field] for b in snapshot["banks"]]
        assert all(type(v) is bool for v in values)
        raw.extend(bytes(values))
    raw.extend(bytes((snapshot["power-known"], snapshot["power-on"])))
    raw.extend(struct.pack(">Q", snapshot["input_period"]))
    assert len(raw) == VMSTATE_BYTES
    return bytes(raw)


def inspect_stream(path, world):
    data, sections = path.read_bytes(), {}
    for label, name, expected in (("dsi", TYPE, dsi_payload(world["dsi"])),
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
    assert snapshot["dsi"]["power-known"] == snapshot["mdss"]["domains"][0]["state-known"]
    assert snapshot["dsi"]["power-on"] == snapshot["mdss"]["domains"][0]["powered"]
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


def prewarm_target(r, saved):
    # A stopped incoming target may hold future work. No lazy MMIO completion
    # is assumed: these are deliberately pending captures and dirty raw state.
    r.set_clock(saved["source_virtual_ns"])
    r.supply()
    r.raw_write(MDSS_BASE, 0x1800)
    for bank, request in ((0, 1_500_000_000), (1, 2_600_000_000)):
        configure(r, bank, request, cphy=bool(bank), ssc=2)
        start(r, bank, wait_bias=False)
        assert r.bank(bank, "lock-pending") and not r.bank(bank, "pll-locked")
    r.raw_write(RAM, saved["ram_witness"] ^ 0xffffffff)
    stale = r.world()
    assert stale != saved["snapshot"]
    assert stale["mdss"] != saved["snapshot"]["mdss"]
    write_json(r.directory/"destination-before-incoming.json", {
        "snapshot": stale, "device_mmio_count": r.device_mmio_operations,
        "qmp_cont_count": r.cont_commands, "qom_get_count": r.qom_get_commands,
        "source_epoch_ns": saved["source_virtual_ns"], "stale_pending_banks": [0, 1]})


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
    assert qom_after-qom_before == 1163
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
    # This RAM witness is after the complete pure first-observation interval.
    assert r.raw_read(RAM) == saved["ram_witness"]
    evidence["ram_witness"] = saved["ram_witness"]
    write_json(r.directory/"destination-before-first-device-mmio.json", evidence)
    return evidence


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
            "all_2926_payload_bytes_verified": True, "mdss_64_bytes_verified": True,
            "actual_four_dispcc_input_periods_restored": True, **details})
        passed(name, first_observation_mmio_delta=0, first_observation_cont_delta=0, **details)

    try:
        # Steady: both high-frequency DPHY banks and both real DISPCC pixels.
        steady = new("migration-steady-source")
        steady.set_clock(100_000)
        four_real_dispcc_carriers_and_high_pixel_rate(steady)
        open_gates(steady, 0)
        steady_path = out/"dsi-steady.migration"
        saved_steady = save_source(steady, steady_path, 0x44534931)
        assert all(b["pll-locked"] and b["digital-ready"] for b in saved_steady["snapshot"]["dsi"]["banks"])
        report["migration_sources"].append(saved_steady)
        target = new("migration-steady-target", incoming=True)
        prewarm_target(target, saved_steady)
        first = receive_source(target, steady_path, saved_steady)
        target.monitor("cont")
        expect_bank(target, 0, 4_500_000_000)
        expect_bank(target, 1, 5_000_000_000)
        target.write(0, 0x14, 0)
        assert target.clock_period("dsi0_phy_pll_out_dsiclk", DISPCC) == 0
        assert target.clock_period("disp_cc_mdss_pclk0_clk_src", DISPCC) == 0
        assert target.clock_period("dsi1_phy_pll_out_dsiclk", DISPCC) == period(nominal(5_000_000_000))
        completed("migration_steady_high_carriers", steady_path, steady, target, first,
                  restored_high_pixel_rates=True, post_restore_gate_withdrawal=True)

        bias = new("migration-pending-bias-source")
        bias.supply()
        configure(bias, 0, 4_500_000_000)
        configure(bias, 1, 3_100_000_000, cphy=True)
        bias.set_clock(200_000)
        for bank in range(2):
            start(bias, bank, wait_bias=False)
        bias_path = out/"dsi-pending-bias.migration"
        saved_bias = save_source(bias, bias_path, 0x44534932)
        assert all(b["bias-deadline"] == 200_250 and b["lock-deadline"] == 203_584
                   for b in saved_bias["snapshot"]["dsi"]["banks"])
        report["migration_sources"].append(saved_bias)
        target = new("migration-pending-bias-target", incoming=True)
        prewarm_target(target, saved_bias)
        first = receive_source(target, bias_path, saved_bias)
        target.monitor("cont")
        target.step(249)
        assert all(not target.bank(b, "bias-settled") and not target.bank(b, "pll-locked") for b in range(2))
        target.step(1)
        assert all(target.bank(b, "bias-settled") and not target.bank(b, "pll-locked") for b in range(2))
        target.step(3333)
        assert all(not target.bank(b, "pll-locked") for b in range(2))
        target.step(1)
        for bank in range(2):
            assert target.bank(bank, "pll-locked")
            open_gates(target, bank)
        expect_bank(target, 0, 4_500_000_000)
        expect_bank(target, 1, 3_100_000_000, cphy=True, selector=3)
        completed("migration_pending_bias_and_lock", bias_path, bias, target, first,
                  bias_completed_after_ns=250, lock_completed_after_ns=3584)

        lock = new("migration-pending-lock-source")
        lock.supply()
        for bank in range(2):
            configure(lock, bank, 4_500_000_000-bank*100_000_000)
        lock.set_clock(300_000)
        for bank in range(2):
            lock.write(bank, 0x24, 0x60)
            lock.write(bank, 0x528, 0xc0)
        lock.step(250)
        for bank in range(2):
            lock.write(bank, 0x3c, 1)
        lock.step(1000)
        lock_path = out/"dsi-pending-lock.migration"
        saved_lock = save_source(lock, lock_path, 0x44534933)
        assert all(b["bias-settled"] and b["bias-deadline"] == -1 and
                   b["lock-deadline"] == 303_584 for b in saved_lock["snapshot"]["dsi"]["banks"])
        report["migration_sources"].append(saved_lock)
        target = new("migration-pending-lock-target", incoming=True)
        prewarm_target(target, saved_lock)
        first = receive_source(target, lock_path, saved_lock)
        target.monitor("cont")
        target.step(2333)
        assert all(not target.bank(b, "pll-locked") for b in range(2))
        target.step(1)
        for bank in range(2):
            assert target.bank(bank, "pll-locked")
            open_gates(target, bank)
            expect_bank(target, bank, 4_500_000_000-bank*100_000_000)
        target.supply(known=True, on=False)
        for bank in range(2):
            expect_no_capture(target, bank)
        completed("migration_pending_lock", lock_path, lock, target, first,
                  lock_completed_after_ns=2334, actual_mdss_power_loss_revoked_both_banks=True)

        disabled = new("migration-disabled-source", reference=False)
        disabled.supply()
        disabled.set_clock(400_000)
        off_path = out/"dsi-disabled.migration"
        saved_off = save_source(disabled, off_path, 0x44534930)
        report["migration_sources"].append(saved_off)
        for name, path, saved, reference, diagnostic in (
                ("on_to_off", steady_path, saved_steady, False, "1 != 0"),
                ("off_to_on", off_path, saved_off, True, "0 != 1")):
            rejected = new("reject-profile-"+name, reference=reference, incoming=True)
            rejected.set_clock(saved["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["rejected_migrations"].append({"name": name, "passed": True,
                "snapshot_file": path.name, "snapshot_sha256": digest(path),
                "stream_modified": False, **details})
            passed("profile_"+name+"_rejected", **details)

        captured_dec = 1604+4*REGISTERS.index(0x5e0)
        cases = (
            ("physical_base", steady_path, saved_steady, 48, 8, BASE, 4,
             f"{BASE:x} != {BASE ^ 4:x}"),
            ("captured_factor", lock_path, saved_lock, captured_dec, 4,
             saved_lock["snapshot"]["dsi"]["captured"]["05e0"], 1,
             TYPE+": invalid migrated lock transaction 0"),
            ("captured_xo_period", bias_path, saved_bias, 2836, 8, period(XO), 1,
             TYPE+": invalid migrated lock transaction 0"),
            ("pending_digital_ready", lock_path, saved_lock, 2915, 1, 0, 1,
             TYPE+": invalid migrated PHY qualification 1"),
        )
        for name, source_path, saved, relative, width, old, xor, diagnostic in cases:
            original = source_path.read_bytes()
            section = saved["sections"]["dsi"]
            absolute = section["payload_offset"]+relative
            assert int.from_bytes(original[absolute:absolute+width], "big") == old
            changed = bytearray(original)
            changed[absolute:absolute+width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
            assert differences == [absolute+width-1]
            assert (original[differences[0]] ^ changed[differences[0]]).bit_count() == 1
            path = out/f"dsi-corrupt-{name}.migration"
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
        "schema": "tb371fc-dsi-pll-7nm-v1", "suite": "dsi-pll-7nm-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 4, "expected_unmodified_streams": 4,
        "expected_stream_files": 8, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "source_variant": VARIANT, "physical_base": BASE, "bank_stride": STRIDE,
        "bank_count": 2, "bank_container_bytes": BANK_SIZE, "words_per_bank": 154,
        "register_count": 308, "sparse_window_count": 16, "mapped_bytes": 1232,
        "output_clock_count": 52, "physical_pll_count": 2, "input_clock_count": 1,
        "connected_input_clock_count": 1, "dispcc_carrier_connection_count": 4,
        "required_programmed_words_per_bank": 128, "status_zero_only_words_per_bank": 15,
        "bias_settle_ns": BIAS_NS, "lock_reference_cycles": 64, "lock_reference_ns_ceiling": LOCK_NS,
        "mdss_vmstate_payload_bytes": 64, "mdss_vmstate_version": 1,
        "reference_enabled_by_default": False, "generic_dividers_round_up": True,
        "fixed_factors_round_down": True, "frequency_checks_use_exact_clock_period": True,
        "first_observation_qom_get_count": 1163,
        "runtime_xo_change_tested": False,
        "runtime_xo_provider": "Actual board RPMh xo.lvl votes; connected overrides are rejected",
        "source_commit": SOURCE_COMMIT,
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "stock_compiled_dsi_source_identity_claimed": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "analog_pll_behavior_modeled": False, "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP and static source-bound digital programming, "
                 "real MDSS supply and DISPCC Clock propagation, complete migration payloads; "
                 "reference delays do not establish silicon lock or display operation.",
        "binary_unchanged": False, "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "migration_sources": [], "actual_stream_files": [],
        "passed": False,
    }
    try:
        assert len(ORDINARY) == 25 and len(set(ORDINARY)) == 25
        for index, test in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = DsiRig(args, fixture,
                             rpmh=test is real_rpmh_xo_vote_loss_revokes_active_and_pending_banks)
                details = test(rig) or {}
                if test is real_rpmh_xo_vote_loss_revokes_active_and_pending_banks:
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
            len(report["migrations"]) == 3 and
            all(item["source_status"] == item["target_status"] == "completed" and
                item["continued_after_restore"] and item["all_2926_payload_bytes_verified"] and
                item["mdss_64_bytes_verified"] and
                item["first_target_observation"]["first_observation_qom_get_count"] == 1163 and
                item["first_target_observation"]["device_mmio_since_incoming"] is False and
                item["first_target_observation"]["qmp_cont_since_incoming"] is False
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and len(report["corrupted_stream_rejections"]) == 4 and
            all(item["passed"] is True and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"]+report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 4 and len(report["actual_stream_files"]) == 8 and
            len({item["name"] for item in report["actual_stream_files"]}) == 8 and
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
