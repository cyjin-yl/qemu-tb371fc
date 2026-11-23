#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual NPUCC reference transactions, Clock propagation and migration.

The independent vectors are from OnePlusOSS/android_kernel_oneplus_sm8250
94304e12a3509eac7b3716bf574ce0b1fcaacbae, npucc-kona.c and clk-alpha-pll.c.
An explicit digital reference profile and QTest-only input frequencies are
used. These checks do not establish NPU/DSP execution, firmware or Android.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import traceback

from test_geni_uart import Rig, connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

PATH = "/machine/npucc"
TYPE = "qcom-sm8250-npucc"
CC, DSP, DSP_PLL = 0x09980000, 0x09800000, 0x09810000
GCC = 0x00100000
XO = 19_200_000
PERIOD_UNIT = 1_000_000_000 << 32
OUTCTRL, RESET_N, UPDATE, UPDATE_BYPASS = 1, 4, 1 << 22, 1 << 23
PCAL, ACK, LOCK, OFF = 1 << 27, 1 << 29, 1 << 31, 1 << 31
ROOT, BUSY = 2, 1
PLL_OFFSETS = (0, 4, 8, 0xc, 0x10, 0x14, 0x18, 0x1c, 0x20,
               0x24, 0x28, 0x2c, 0x38, 0x40)
PLLS = (
    (CC, "npu_cc_pll0", 0x1f, 0x4000, 600_000_000),
    (CC + 0x400, "npu_cc_pll1", 0x4e, 0x2000, 1_500_000_000),
    (DSP_PLL, "npu_q6ss_pll", 0xf, 0xa000, 300_000_000),
)
RCGS = (
    (CC + 0x1010, "npu_cc_core_clk_src", (0, 1, 2, 4, 5, 7)),
    (CC + 0x1060, "npu_cc_lmh_clk_src", (0, 1, 2, 4, 5, 7)),
    (CC + 0x1100, "npu_cc_cal_hm0_clk_src", (0, 1, 2, 4, 5, 7)),
    (CC + 0x1140, "npu_cc_cal_hm1_clk_src", (0, 1, 2, 4, 5, 7)),
    (CC + 0x1400, "npu_cc_xo_clk_src", (0, 7)),
    (DSP + 0x28, "npu_dsp_core_clk_src", (0, 2, 7)),
)
# Branch declaration order, including the pinned HM1 -> HM0 parent relation.
BRANCHES = (
    (0x10d0, "npu_cc_atb_clk", "qdss_qmp_clk"),
    (0x10dc, "npu_cc_bto_core_clk", "npu_cc_xo_clk_src"),
    (0x10d8, "npu_cc_bwmon_clk", "npu_cc_xo_clk_src"),
    (0x1098, "npu_cc_cal_hm0_cdc_clk", "npu_cc_cal_hm0_clk_src"),
    (0x1110, "npu_cc_cal_hm0_clk", "npu_cc_cal_hm0_clk_src"),
    (0x109c, "npu_cc_cal_hm0_dpm_ip_clk", "npu_cc_cal_hm0_clk_src"),
    (0x10a0, "npu_cc_cal_hm0_perf_cnt_clk", "npu_cc_cal_hm0_clk_src"),
    (0x10a4, "npu_cc_cal_hm1_cdc_clk", "npu_cc_cal_hm0_clk_src"),
    (0x1150, "npu_cc_cal_hm1_clk", "npu_cc_cal_hm0_clk_src"),
    (0x10a8, "npu_cc_cal_hm1_dpm_ip_clk", "npu_cc_cal_hm0_clk_src"),
    (0x10ac, "npu_cc_cal_hm1_perf_cnt_clk", "npu_cc_cal_hm0_clk_src"),
    (0x1030, "npu_cc_core_clk", "npu_cc_core_clk_src"),
    (0x1238, "npu_cc_dl_dpm_clk", "npu_cc_lmh_clk_src"),
    (0x1234, "npu_cc_dl_llm_clk", "npu_cc_lmh_clk_src"),
    (0x107c, "npu_cc_dpm_clk", "npu_cc_lmh_clk_src"),
    (0x10c4, "npu_cc_dpm_temp_clk", "npu_llm_temp_clk"),
    (0x1094, "npu_cc_dpm_xo_clk", "npu_cc_xo_clk_src"),
    (0x1214, "npu_cc_dsp_ahbm_clk", "npu_cc_core_clk_src"),
    (0x1210, "npu_cc_dsp_ahbs_clk", "npu_cc_core_clk_src"),
    (0x121c, "npu_cc_dsp_axi_clk", "gcc_npu_noc_axi_clk"),
    (0x1218, "npu_cc_dsp_bwmon_ahb_clk", "npu_cc_core_clk_src"),
    (0x1224, "npu_cc_dsp_bwmon_clk", "npu_cc_xo_clk_src"),
    (0x1078, "npu_cc_isense_clk", "npu_cc_lmh_clk_src"),
    (0x1074, "npu_cc_llm_clk", "npu_cc_lmh_clk_src"),
    (0x10d4, "npu_cc_llm_curr_clk", "npu_llm_curr_clk"),
    (0x10c8, "npu_cc_llm_temp_clk", "npu_llm_temp_clk"),
    (0x1090, "npu_cc_llm_xo_clk", "npu_cc_xo_clk_src"),
    (0x10c0, "npu_cc_noc_ahb_clk", "npu_cc_noc_ahb_clk_parent"),
    (0x10b8, "npu_cc_noc_axi_clk", "gcc_npu_noc_axi_clk"),
    (0x10b0, "npu_cc_noc_dma_clk", "gcc_npu_noc_dma_clk"),
    (0x10e0, "npu_cc_rsc_xo_clk", "npu_cc_xo_clk_src"),
    (0x10cc, "npu_cc_s2p_clk", "npu_s2p_clk"),
    (0x1410, "npu_cc_xo_clk", "npu_cc_xo_clk_src"),
)
RESETS = ((0x10f0, 1), (0x1130, 1), (0x1000, 1), (0x10c4, 4),
          (0x1200, 1), (0x10d4, 4), (0x10c8, 4))
CRC = ((0x11a4, 0x15011), (0x11a0, 0x800000),
       (0x11b4, 0x15011), (0x11b0, 0x800000))
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "gcc_npu_gpll0_clk_src",
          "gcc_npu_gpll0_div_clk_src", "core_bi_pll_test_se", "qdss_qmp_clk",
          "npu_llm_temp_clk", "npu_llm_curr_clk", "npu_s2p_clk",
          "gcc_npu_noc_axi_clk", "gcc_npu_noc_dma_clk",
          "npu_cc_noc_ahb_clk_parent")
OUTPUTS = tuple(p[1] for p in PLLS) + (
    "npu_cc_pll0_out_even", "npu_cc_pll1_out_even", "npu_cc_crc_div"
) + tuple(r[1] for r in RCGS) + tuple(b[1] for b in BRANCHES)
REGISTERS = tuple(sorted(set(
    [p[0] + offset for p in PLLS for offset in PLL_OFFSETS] +
    [r[0] + offset for r in RCGS for offset in (0, 4)] +
    [CC + b[0] for b in BRANCHES] + [CC + offset for offset, _ in RESETS] +
    [CC + offset for offset, _ in CRC])))
STATE_PROPERTIES = tuple(
    f"pll{i}-{name}" for i in range(3) for name in (
        "locked", "ack", "calibrated", "lock-pending", "latch-pending",
        "active-l", "active-alpha")) + tuple(
    f"rcg{i}-{name}" for i in range(6) for name in ("pending", "active-cfg"))
DIAGNOSTIC = TYPE + ":"


class NpuRig(Rig):
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest"):
        self.args = args
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qnpucc-"))
        self.last_clock = 0
        self.reference = reference
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = [args.qemu, "-machine", args.machine, "-accel", accel,
                   "-m", "128M", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
                   "-qmp", f"unix:{self.socket_dir}/m,server=on,wait=off"]
        if accel == "qtest":
            command += ["-qtest", f"unix:{self.socket_dir}/t,server=on,wait=off",
                        "-qtest-log", "/dev/null"]
        if reference is not None:
            command += ["-global", "qcom-sm8250-npucc.enabled-reference=" +
                        ("on" if reference else "off")]
        if incoming:
            command += ["-incoming", "defer"]
        (self.directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=self.log)
            self.qmp = connect_unix(self.socket_dir / "m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            assert "QMP" in greeting, greeting
            self.monitor("qmp_capabilities")
            if accel == "qtest":
                self.qtest = connect_unix(self.socket_dir / "t", self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile("rwb", buffering=0)
                self.command(f"irq_intercept_out {PATH} reset")
                if not incoming:
                    self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, value):
        self.qtest_transcript.write("> " + value + "\n")
        result = super().command(value)
        self.qtest_transcript.write("< OK " + " ".join(result) + "\n")
        return result

    def monitor(self, execute, arguments=None):
        self.qmp_transcript.write("> " + json.dumps([execute, arguments]) + "\n")
        try:
            result = super().monitor(execute, arguments)
        except BaseException as error:
            self.qmp_transcript.write("< ERROR " + str(error) + "\n")
            raise
        self.qmp_transcript.write("< " + json.dumps(result) + "\n")
        return result

    def read(self, offset):
        return self.raw_read(offset)

    def write(self, offset, value):
        self.raw_write(offset, value)

    def raw_read(self, address):
        return int(self.command(f"readl {address:#x}")[0], 0)

    def raw_write(self, address, value):
        self.command(f"writel {address:#x} {value:#x}")

    def step(self, ns):
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        return self.last_clock

    def get(self, name, path=PATH):
        return self.monitor("qom-get", {"path": path, "property": name})

    def period(self, name, path=PATH):
        return self.get("qtest-clock-period", path + "/" + name)

    def expect_hz(self, name, hz, path=PATH):
        actual = self.period(name, path)
        expected = PERIOD_UNIT // hz if hz else 0
        assert actual == expected, (name, hz, expected, actual)

    def fixture(self, name, hz):
        self.monitor("qom-set", {"path": PATH, "property": "qtest-" + name + "-hz",
                                 "value": hz})

    def snapshot(self):
        return {"registers": {hex(address): self.read(address) for address in REGISTERS},
                "state": {name: self.get(name) for name in STATE_PROPERTIES},
                "outputs": {name: self.period(name) for name in OUTPUTS},
                "inputs": {name: self.period(name) for name in INPUTS}}

    def gpio(self, name, number, value, path=PATH):
        self.command(f"set_irq_in {path} {name} {number} {int(value)}")

    def diagnostic_count(self, needle):
        return self.log_path.read_text(errors="replace").count(needle)

    def reset(self):
        self.monitor("system_reset")
        self.wait_for(lambda: all(self.read(pll[0]) == self.read(pll[0] + 4) == 0
                                  for pll in PLLS),
                      "all NPU PLL register witnesses cleared by system reset")

    def close(self):
        super().close()
        for name in ("qtest_transcript", "qmp_transcript"):
            stream = getattr(self, name, None)
            if stream and not stream.closed:
                stream.close()


def pll_state(rig, index, field):
    return rig.get(f"pll{index}-{field}")


def rcg_state(rig, index, field):
    return rig.get(f"rcg{index}-{field}")


def configure_pll(rig, index, *, crc=True):
    base, _, l, alpha, _ = PLLS[index]
    assert not (rig.read(base) & OUTCTRL and rig.read(base + 0x38) & 1)
    for offset, value in ((4, l), (8, 0x44), (0x40, alpha),
                          (0x18, 0x20485699), (0x1c, 0x2261),
                          (0x20, 0x329a699c), (0x10, 0x805)):
        rig.write(base + offset, value)
    if index == 0 and crc:
        for offset, value in CRC:
            rig.write(CC + offset, value)
    rig.write(base, rig.read(base) | UPDATE_BYPASS)
    rig.write(base, rig.read(base) & ~OUTCTRL)
    rig.write(base + 0x38, 0)
    rig.write(base, rig.read(base) | RESET_N)


def configure_all(rig, *, crc=True):
    for index in range(3):
        configure_pll(rig, index, crc=crc)


def output_on(rig, index):
    base = PLLS[index][0]
    rig.write(base + 0xc, 7)
    rig.write(base, rig.read(base) | OUTCTRL)


def enable_all(rig):
    for base, *_ in PLLS:
        rig.write(base + 0x38, 1)
    rig.step(5000)
    for index, (base, name, _, _, hz) in enumerate(PLLS):
        assert rig.read(base) & (LOCK | PCAL) == LOCK | PCAL
        output_on(rig, index)
        rig.expect_hz(name, hz)


def configure_rcg(rig, index, selector, hid=1, *, wait=True, root=True):
    base = RCGS[index][0]
    rig.write(base + 4, rcg_cfg(index, selector, hid))
    rig.write(base, (ROOT if root else 0) | BUSY)
    if wait:
        rig.step(1000)
        assert not rig.read(base) & BUSY


def branch_hz(rig, index, hz):
    offset, name, _ = BRANCHES[index]
    rig.expect_hz(name, hz)
    assert bool(rig.read(CC + offset) & OFF) == (hz == 0), (name, rig.read(CC + offset), hz)


def configured_outputs(rig):
    configure_all(rig)
    enable_all(rig)
    for index in range(6):
        configure_rcg(rig, index, 0)


def rcg_cfg(index, selector, hid=1):
    # The vendor XO root alone omits HW_CLK_CTRL_MODE.
    return (selector << 8) | hid | (0 if index == 4 else 1 << 20)


def divided(hz, hid):
    return hz * 2 // (hid + 1) if hid else hz


def expect_root(rig, index, hz):
    base, name, _ = RCGS[index]
    rig.expect_hz(name, hz)
    assert bool(rig.read(base) & OFF) == (hz == 0), (index, hz, rig.read(base))


def cold_reference_has_no_running_or_invented_clock(rig):
    assert rig.get("enabled-reference") is True
    assert len(REGISTERS) == 95 and len(OUTPUTS) == 45
    status_addresses = {r[0] for r in RCGS} | {CC + b[0] for b in BRANCHES}
    for address in REGISTERS:
        assert rig.read(address) == (OFF if address in status_addresses else 0), hex(address)
    assert all(rig.get(name) in (False, 0) for name in STATE_PROPERTIES)
    assert rig.get("crc-reference-configured") is False
    for name in OUTPUTS:
        rig.expect_hz(name, 0)
    for index, name in enumerate(INPUTS):
        rig.expect_hz(name, XO if index < 2 else 0)
    rig.step(100_000)
    assert all(rig.get(name) in (False, 0) for name in STATE_PROPERTIES)


def actual_vendor_three_bank_configure_vector_remains_in_standby(rig):
    configure_all(rig)
    for index, (base, name, l, alpha, _) in enumerate(PLLS):
        assert rig.read(base) == RESET_N | UPDATE_BYPASS
        for offset, expected in ((4, l), (8, 0x44), (0x40, alpha),
                                 (0x18, 0x20485699), (0x1c, 0x2261),
                                 (0x20, 0x329a699c), (0x10, 0x805)):
            assert rig.read(base + offset) == expected, (index, offset)
        assert rig.read(base + 0x38) == 0
        assert pll_state(rig, index, "lock-pending") is False
        rig.expect_hz(name, 0)
    for offset, value in CRC:
        assert rig.read(CC + offset) == value
    assert rig.get("crc-reference-configured") is True
    rig.step(100_000)
    for base, *_ in PLLS:
        assert not rig.read(base) & (LOCK | PCAL | ACK)


def physically_separate_pll_banks_do_not_alias(rig):
    for index, (base, *_) in enumerate(PLLS):
        rig.write(base + 4, 0x30 + index)
        rig.write(base + 0x40, 0x1000 + index)
    for index, (base, *_) in enumerate(PLLS):
        assert rig.read(base + 4) == 0x30 + index
        assert rig.read(base + 0x40) == 0x1000 + index
    configure_all(rig)
    rig.write(DSP_PLL + 0x38, 1)
    rig.step(5000)
    output_on(rig, 2)
    for index, (base, name, _, _, hz) in enumerate(PLLS):
        assert pll_state(rig, index, "locked") is (index == 2)
        rig.expect_hz(name, hz if index == 2 else 0)
    assert not rig.read(CC) & PCAL and not rig.read(CC + 0x400) & PCAL


def three_staggered_pll_lock_deadlines_are_independent(rig):
    configure_all(rig)
    for index, (base, *_) in enumerate(PLLS):
        output_on(rig, index)
        rig.write(base + 0x38, 1)
        if index != 2:
            rig.step(1000)
    rig.step(2999)
    for index, (_, name, *_) in enumerate(PLLS):
        assert pll_state(rig, index, "lock-pending") is True
        rig.expect_hz(name, 0)
    rig.step(1)
    for index, (_, name, _, _, hz) in enumerate(PLLS):
        assert pll_state(rig, index, "locked") is (index == 0)
        rig.expect_hz(name, hz if index == 0 else 0)
    rig.step(999)
    assert pll_state(rig, 1, "locked") is False
    rig.step(1)
    assert pll_state(rig, 1, "locked") is True
    assert pll_state(rig, 2, "locked") is False
    rig.step(999)
    assert pll_state(rig, 2, "locked") is False
    rig.step(1)
    for index, (base, name, _, _, hz) in enumerate(PLLS):
        assert rig.read(base) & (LOCK | PCAL) == LOCK | PCAL
        assert pll_state(rig, index, "lock-pending") is False
        rig.expect_hz(name, hz)


def every_pll_requires_both_output_gates_and_rejects_unknown_selection(rig):
    configure_all(rig)
    for base, *_ in PLLS:
        rig.write(base + 0x38, 1)
    rig.step(5000)
    for index, (base, name, _, _, hz) in enumerate(PLLS):
        assert pll_state(rig, index, "locked") is True
        rig.expect_hz(name, 0)
        rig.write(base + 0xc, 7)
        rig.expect_hz(name, 0)
        rig.write(base, rig.read(base) | OUTCTRL)
        rig.expect_hz(name, hz)
        count = rig.diagnostic_count(DIAGNOSTIC)
        for selection in range(1, 7):
            rig.write(base + 0xc, selection)
            assert rig.read(base + 0xc) == 7
            rig.expect_hz(name, hz)
        assert rig.diagnostic_count(DIAGNOSTIC) - count == 6
        rig.write(base + 0xc, 0)
        rig.expect_hz(name, 0)
        assert rig.read(base) & LOCK
        rig.write(base + 0xc, 7)
        rig.expect_hz(name, hz)
        rig.write(base, rig.read(base) & ~OUTCTRL)
        rig.expect_hz(name, 0)
        assert pll_state(rig, index, "locked") is True


def pll0_lock_and_even_output_do_not_depend_on_crc_programming(rig):
    configure_all(rig, crc=False)
    enable_all(rig)
    assert rig.get("crc-reference-configured") is False
    rig.expect_hz("npu_cc_pll0_out_even", 600_000_000)
    rig.expect_hz("npu_cc_pll1_out_even", 1_500_000_000)
    rig.expect_hz("npu_cc_crc_div", 0)
    configure_rcg(rig, 0, 2)
    configure_rcg(rig, 2, 2)
    expect_root(rig, 0, 600_000_000)
    expect_root(rig, 2, 0)


def crc_reference_vector_controls_only_the_divide_two_path(rig):
    configure_all(rig, crc=False)
    enable_all(rig)
    configure_rcg(rig, 2, 2)
    for index, (offset, value) in enumerate(CRC):
        rig.write(CC + offset, value)
        rig.expect_hz("npu_cc_crc_div", 300_000_000 if index == 3 else 0)
        rig.expect_hz("npu_cc_pll0", 600_000_000)
    expect_root(rig, 2, 300_000_000)
    for offset, value in CRC:
        rig.write(CC + offset, 0)
        assert rig.get("crc-reference-configured") is False
        rig.expect_hz("npu_cc_crc_div", 0)
        expect_root(rig, 2, 0)
        assert pll_state(rig, 0, "locked") is True
        rig.expect_hz("npu_cc_pll0_out_even", 600_000_000)
        rig.write(CC + offset, value)
        rig.expect_hz("npu_cc_crc_div", 300_000_000)
    assert rig.get("crc-reference-configured") is True


def postdivider_zero_means_divide_one_and_unknown_encodings_are_atomic(rig):
    configure_all(rig)
    enable_all(rig)
    for index in range(2):
        base, name, _, _, hz = PLLS[index]
        even = name + "_out_even"
        rig.expect_hz(even, hz)
        before = rig.snapshot()
        count = rig.diagnostic_count(DIAGNOSTIC)
        for encoding in range(1, 16):
            rig.write(base + 0xc, 7 | (encoding << 8))
            assert rig.read(base + 0xc) == 7
            rig.expect_hz(even, hz)
        assert rig.snapshot() == before
        assert rig.diagnostic_count(DIAGNOSTIC) - count == 15


def v2_nominal_two_ghz_accepts_only_the_ceil_q16_endpoint(rig):
    configure_pll(rig, 0)
    rig.write(CC + 4, 0x68)
    rig.write(CC + 0x40, 0x2aab)
    rig.write(CC + 0x38, 1)
    rig.step(5000)
    output_on(rig, 0)
    assert pll_state(rig, 0, "locked") is True
    hz = XO * ((0x68 << 16) | 0x2aab) // 65536
    assert hz == 2_000_000_097
    # Assert real Clock period first; scalar metadata cannot replace it.
    rig.expect_hz("npu_cc_pll0", hz)
    rig.expect_hz("npu_cc_pll0_out_even", hz)
    rig.expect_hz("npu_cc_crc_div", 1_000_000_048)
    configure_rcg(rig, 2, 2)
    expect_root(rig, 2, 1_000_000_048)
    assert rig.get("clk.npu_cc_pll0.hz") == hz
    rig.write(CC + 0x40, 0x2aac)
    rig.write(CC, rig.read(CC) | UPDATE)
    assert pll_state(rig, 0, "latch-pending") is False
    rig.step(20_000)
    assert not rig.read(CC) & ACK
    assert pll_state(rig, 0, "active-alpha") == 0x2aab
    rig.expect_hz("npu_cc_pll0", hz)
    rig.write(CC, rig.read(CC) & ~(RESET_N | UPDATE))
    rig.write(CC, rig.read(CC) | RESET_N)
    rig.step(20_000)
    assert not rig.read(CC) & (LOCK | PCAL | ACK)
    rig.expect_hz("npu_cc_pll0", 0)
    assert pll_state(rig, 0, "lock-pending") is False


def unsupported_analog_recipe_stops_only_the_affected_pll(rig):
    configure_all(rig)
    enable_all(rig)
    for index, (base, name, _, _, hz) in enumerate(PLLS):
        rig.write(base + 0x20, 0x029a699c)  # GPU's recipe is not the NPU recipe.
        rig.step(20_000)
        assert pll_state(rig, index, "locked") is False
        rig.expect_hz(name, 0)
        for other, (_, other_name, _, _, other_hz) in enumerate(PLLS):
            if other != index:
                rig.expect_hz(other_name, other_hz)
        rig.write(base + 0x20, 0x329a699c)
        rig.step(4999)
        rig.expect_hz(name, 0)
        rig.step(1)
        rig.expect_hz(name, hz)


LATCH_VALUES = ((32, 0, 614_400_000), (40, 0x8000, 777_600_000),
                (20, 0x4000, 388_800_000))


def capture_three_latches(rig):
    for index, (base, *_) in enumerate(PLLS):
        l, alpha, _ = LATCH_VALUES[index]
        rig.write(base + 4, l)
        rig.write(base + 0x40, alpha)
        rig.write(base, rig.read(base) | UPDATE)
        rig.write(base + 4, 44 + index)
        rig.write(base + 0x40, 0)


def three_captured_pll_latches_keep_old_outputs_until_ack_and_relock(rig):
    configure_all(rig)
    enable_all(rig)
    capture_three_latches(rig)
    rig.step(999)
    for index, (base, name, l, alpha, hz) in enumerate(PLLS):
        assert pll_state(rig, index, "latch-pending") is True
        assert pll_state(rig, index, "active-l") == l
        assert pll_state(rig, index, "active-alpha") == alpha
        assert not rig.read(base) & ACK
        rig.expect_hz(name, hz)
    rig.step(1)
    for index, (base, name, *_) in enumerate(PLLS):
        l, alpha, _ = LATCH_VALUES[index]
        assert rig.read(base) & (ACK | UPDATE) == ACK | UPDATE
        assert pll_state(rig, index, "active-l") == l
        assert pll_state(rig, index, "active-alpha") == alpha
        assert rig.read(base + 4) == 44 + index and rig.read(base + 0x40) == 0
        rig.expect_hz(name, 0)
        rig.write(base, rig.read(base) & ~UPDATE)
        assert not rig.read(base) & ACK
    rig.step(4999)
    assert all(not pll_state(rig, i, "locked") for i in range(3))
    rig.step(1)
    for index, (_, name, *_) in enumerate(PLLS):
        rig.expect_hz(name, LATCH_VALUES[index][2])
        assert pll_state(rig, index, "locked") is True


def standby_reset_and_cancel_are_local_to_each_pll(rig):
    configure_all(rig)
    for base, *_ in PLLS:
        rig.write(base + 0x38, 1)
    rig.step(1000)
    rig.write(PLLS[1][0] + 0x38, 0)
    assert pll_state(rig, 1, "lock-pending") is False
    rig.step(4000)
    assert [pll_state(rig, i, "locked") for i in range(3)] == [True, False, True]
    rig.write(PLLS[1][0] + 0x38, 1)
    rig.step(5000)
    for index in range(3):
        output_on(rig, index)
    capture_three_latches(rig)
    rig.step(500)
    rig.write(CC, rig.read(CC) & ~UPDATE)
    rig.write(DSP_PLL, rig.read(DSP_PLL) & ~UPDATE_BYPASS)
    assert pll_state(rig, 0, "latch-pending") is False
    assert pll_state(rig, 2, "latch-pending") is False
    rig.step(500)
    assert [pll_state(rig, i, "ack") for i in range(3)] == [False, True, False]
    assert pll_state(rig, 0, "active-l") == PLLS[0][2]
    assert pll_state(rig, 2, "active-l") == PLLS[2][2]
    rig.write(CC + 0x38, 0)
    assert pll_state(rig, 0, "calibrated") is True
    rig.write(CC, rig.read(CC) & ~RESET_N)
    assert pll_state(rig, 0, "calibrated") is False
    assert pll_state(rig, 2, "calibrated") is True


def late_dispatch_uses_original_latch_deadlines_for_all_three_plls(rig):
    configure_all(rig)
    enable_all(rig)
    capture_three_latches(rig)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    rig.last_clock = int(rig.command(f"clock_set {rig.last_clock + 6000}")[0], 0)
    # Running clock_step would dispatch at each deadline and miss this bug.
    for index, (_, name, _, _, hz) in enumerate(PLLS):
        assert pll_state(rig, index, "latch-pending") is True
        assert pll_state(rig, index, "ack") is False
        rig.expect_hz(name, hz)
    rig.monitor("cont")
    for index, (base, name, *_) in enumerate(PLLS):
        assert rig.read(base) & (ACK | LOCK) == ACK | LOCK
        rig.expect_hz(name, LATCH_VALUES[index][2])


CAPTURED_HIDS = (0, 1, 2, 3, 7, 15)


def capture_six_rcgs(rig):
    for index, (base, _, _) in enumerate(RCGS):
        configure_rcg(rig, index, 7, CAPTURED_HIDS[index], wait=False)
        rig.write(base + 4, rcg_cfg(index, 0, 31))


def six_independent_rcgs_capture_shadow_and_latch_at_one_microsecond(rig):
    for index in range(6):
        configure_rcg(rig, index, 0)
    rig.fixture("core_bi_pll_test_se", 96_000_000)
    capture_six_rcgs(rig)
    rig.step(999)
    for index in range(6):
        assert rcg_state(rig, index, "pending") is True
        assert rcg_state(rig, index, "active-cfg") == rcg_cfg(index, 0)
        expect_root(rig, index, XO)
    rig.step(1)
    for index, (base, _, _) in enumerate(RCGS):
        assert rcg_state(rig, index, "pending") is False
        assert rcg_state(rig, index, "active-cfg") == rcg_cfg(index, 7, CAPTURED_HIDS[index])
        assert rig.read(base + 4) == rcg_cfg(index, 0, 31)
        assert not rig.read(base) & BUSY
        expect_root(rig, index, divided(96_000_000, CAPTURED_HIDS[index]))
    # A canceled request must not appear later, independently for all roots.
    active = [rcg_state(rig, i, "active-cfg") for i in range(6)]
    for index in range(6):
        configure_rcg(rig, index, 0, wait=False)
    rig.step(400)
    for base, *_ in RCGS:
        rig.write(base, ROOT)
    rig.step(1000)
    assert [rcg_state(rig, i, "active-cfg") for i in range(6)] == active
    assert all(not rcg_state(rig, i, "pending") for i in range(6))


def source_maps_route_pll_postdivider_crc_and_dsp_outputs(rig):
    configure_all(rig)
    enable_all(rig)
    vectors = ((0, 1, 8, 333_333_333), (0, 2, 1, 600_000_000),
               (1, 1, 9, 300_000_000), (1, 2, 1, 600_000_000),
               (2, 2, 1, 300_000_000), (3, 2, 1, 300_000_000),
               (4, 0, 1, XO), (5, 2, 1, 300_000_000))
    for index, selector, hid, hz in vectors:
        configure_rcg(rig, index, selector, hid)
        expect_root(rig, index, hz)
    rig.write(DSP_PLL + 0xc, 0)
    expect_root(rig, 5, 0)
    expect_root(rig, 2, 300_000_000)
    rig.write(DSP_PLL + 0xc, 7)
    expect_root(rig, 5, 300_000_000)


def hm_rcgs_are_separate_and_hm1_branches_follow_the_pinned_hm0_parent(rig):
    configure_all(rig)
    enable_all(rig)
    configure_rcg(rig, 2, 2)
    configure_rcg(rig, 3, 1, 3)
    expect_root(rig, 2, 300_000_000)
    expect_root(rig, 3, 750_000_000)
    for index in range(3, 11):
        rig.write(CC + BRANCHES[index][0], 1)
        branch_hz(rig, index, 300_000_000)
    configure_rcg(rig, 3, 1, 5)
    expect_root(rig, 3, 500_000_000)
    expect_root(rig, 2, 300_000_000)
    for index in range(3, 11):
        branch_hz(rig, index, 300_000_000)
    configure_rcg(rig, 2, 1, 5)
    for index in range(3, 11):
        branch_hz(rig, index, 500_000_000)
    assert rcg_state(rig, 3, "active-cfg") == rcg_cfg(3, 1, 5)


def six_hid_fields_cover_bypass_integer_and_fractional_division(rig):
    rig.fixture("core_bi_pll_test_se", 96_000_000)
    for index in range(6):
        for hid, hz in ((0, 96_000_000), (1, 96_000_000),
                        (2, 64_000_000), (31, 6_000_000)):
            configure_rcg(rig, index, 7, hid)
            expect_root(rig, index, hz)


def undefined_source_codes_and_nonzero_mnd_modes_do_not_commit(rig):
    for index, (base, _, valid) in enumerate(RCGS):
        configure_rcg(rig, index, 0)
        active = rcg_state(rig, index, "active-cfg")
        invalid = [rcg_cfg(index, selector) for selector in range(8) if selector not in valid]
        invalid += [rcg_cfg(index, 0) | (mode << 12) for mode in (1, 2, 3)]
        for cfg in invalid:
            rig.write(base, ROOT)
            rig.write(base + 4, cfg)
            rig.write(base, ROOT | BUSY)
            assert rcg_state(rig, index, "pending") is False
            rig.step(10_000)
            assert rig.read(base) & BUSY
            assert rcg_state(rig, index, "active-cfg") == active
            expect_root(rig, index, XO)
        rig.write(base, ROOT)


def legal_unknown_input_can_latch_without_creating_a_frequency(rig):
    for index in range(6):
        configure_rcg(rig, index, 7)
        assert rcg_state(rig, index, "active-cfg") == rcg_cfg(index, 7)
        assert rcg_state(rig, index, "pending") is False
        expect_root(rig, index, 0)
    for hz in (80_000_000, 0, 50_000_000):
        rig.fixture("core_bi_pll_test_se", hz)
        for index in range(6):
            expect_root(rig, index, hz)


def real_gcc_parent_clocks_propagate_through_board_links(rig):
    cfg_before = rig.read(GCC + 0x4d004)
    assert cfg_before & OFF
    for offset, value in ((4, 31), (0x40, 0x4000), (0xc, 0x107),
                          (0, (1 << 20) | RESET_N), (0x52018, 1),
                          (0x52000, (1 << 18) | (1 << 19))):
        rig.write(GCC + offset, value)
    rig.step(5000)
    rig.expect_hz("gcc_npu_gpll0_clk_src", 600_000_000)
    rig.expect_hz("gcc_npu_gpll0_div_clk_src", 300_000_000)
    for index in range(4):
        configure_rcg(rig, index, 4 if index % 2 == 0 else 5, 5)
        expect_root(rig, index, 200_000_000 if index % 2 == 0 else 100_000_000)
    rig.write(GCC + 0x52000, 1 << 19)
    for index in range(4):
        expect_root(rig, index, 0 if index % 2 == 0 else 100_000_000)
    rig.write(GCC + 0x52000, 1 << 18)
    for index in range(4):
        expect_root(rig, index, 200_000_000 if index % 2 == 0 else 0)
    rig.write(GCC + 0x52018, 0)
    for index in range(4):
        expect_root(rig, index, 0)
    assert rig.read(GCC + 0x4d004) == cfg_before


def all_33_branches_have_independent_local_and_remote_requests(rig):
    for index in range(6):
        configure_rcg(rig, index, 0)
    external = {name: (20 + index) * 1_000_000 for index, name in enumerate(INPUTS[4:])}
    for name, hz in external.items():
        rig.fixture(name, hz)
    for index, (offset, name, parent) in enumerate(BRANCHES):
        hz = external.get(parent, XO)
        branch_hz(rig, index, 0)
        rig.write(CC + offset, 1)
        branch_hz(rig, index, hz)
        for other, (_, other_name, _) in enumerate(BRANCHES):
            if other != index:
                rig.expect_hz(other_name, 0)
        rig.gpio("remote-vote", index, True)
        rig.write(CC + offset, 0)
        branch_hz(rig, index, hz)
        assert not rig.read(CC + offset) & 1
        rig.gpio("remote-vote", index, False)
        branch_hz(rig, index, 0)
    configure_rcg(rig, 0, 0, root=False)
    expect_root(rig, 0, 0)
    rig.gpio("remote-vote", 11, True)
    expect_root(rig, 0, XO)
    branch_hz(rig, 11, XO)
    rig.gpio("remote-vote", 11, False)
    expect_root(rig, 0, 0)


def unknown_external_branch_parents_remain_zero_and_shared_inputs_fan_out(rig):
    external_indices = [i for i, (_, _, parent) in enumerate(BRANCHES) if parent in INPUTS]
    for index in external_indices:
        rig.write(CC + BRANCHES[index][0], 1)
        branch_hz(rig, index, 0)
    rig.step(100_000)
    for index in external_indices:
        branch_hz(rig, index, 0)
    for parent, hz, indices in (("npu_llm_temp_clk", 48_000_000, (15, 25)),
                                ("gcc_npu_noc_axi_clk", 72_000_000, (19, 28)),
                                ("gcc_npu_noc_dma_clk", 27_000_000, (29,))):
        rig.fixture(parent, hz)
        for index in indices:
            branch_hz(rig, index, hz)
    rig.write(CC + BRANCHES[15][0], 0)
    rig.fixture("npu_llm_temp_clk", 50_000_000)
    branch_hz(rig, 15, 0)
    branch_hz(rig, 25, 50_000_000)
    for index in (0, 24, 27, 31):
        branch_hz(rig, index, 0)
    rig.fixture("gcc_npu_noc_axi_clk", 0)
    for index in (19, 28):
        branch_hz(rig, index, 0)
    branch_hz(rig, 29, 27_000_000)


def shared_ares_and_cbcr_bits_preserve_each_other_without_invented_gating(rig):
    rig.fixture("npu_llm_temp_clk", 48_000_000)
    rig.fixture("npu_llm_curr_clk", 36_000_000)
    for branch, reset, hz in ((15, 3, 48_000_000), (24, 5, 36_000_000),
                              (25, 6, 48_000_000)):
        address = CC + BRANCHES[branch][0]
        rig.write(address, 1)
        branch_hz(rig, branch, hz)
        rig.write(address, rig.read(address) | 4)
        rig.raw_read(0x80000000)
        assert rig.irq.get(reset, False) is True
        assert rig.read(address) & 5 == 5
        branch_hz(rig, branch, hz)
        rig.write(address, rig.read(address) & ~1)
        rig.raw_read(0x80000000)
        assert rig.irq.get(reset, False) is True
        assert rig.read(address) == OFF | 4
        branch_hz(rig, branch, 0)
        rig.write(address, rig.read(address) | 1)
        branch_hz(rig, branch, hz)
        rig.write(address, rig.read(address) & ~4)
        rig.raw_read(0x80000000)
        assert rig.irq.get(reset, False) is False
        branch_hz(rig, branch, hz)
        rig.write(address, 0)


def seven_reset_outputs_follow_binding_order_and_shared_word_masks(rig):
    for index, (offset, mask) in enumerate(RESETS):
        address = CC + offset
        rig.write(address, rig.read(address) | mask)
        assert rig.read(address) & mask
        rig.raw_read(0x80000000)
        for other in range(7):
            assert rig.irq.get(other, False) is (other == index), (index, other, rig.irq)
        rig.write(address, rig.read(address) & ~mask)
        rig.raw_read(0x80000000)
        assert rig.irq.get(index, False) is False
        assert not rig.read(address) & mask


def reserved_registers_masks_and_narrow_accesses_are_atomic(rig):
    configured_outputs(rig)
    before = rig.snapshot()
    invalid = []
    for base, *_ in PLLS:
        invalid.extend(((base + 4, 1 << 16), (base + 0x40, 1 << 16),
                        (base, 1 << 19), (base + 0x38, 2), (base + 0xc, 8)))
    for base, *_ in RCGS:
        invalid.extend(((base, 1 << 4), (base + 4, 1 << 31)))
    invalid.extend((CC + offset, 2) for offset, _, _ in BRANCHES)
    invalid.extend((CC + offset, 2) for offset, mask in RESETS if mask == 1)
    invalid.extend((CC + offset, 0xfeed) for offset, _ in CRC)
    holes = [base + offset for base, *_ in PLLS for offset in (0x30, 0x34, 0x3c)]
    count = rig.diagnostic_count(DIAGNOSTIC)
    for address, value in invalid:
        rig.write(address, value)
        assert rig.snapshot() == before, (hex(address), value)
    for address in holes:
        rig.write(address, 0)
        rig.read(address)
        assert rig.snapshot() == before, hex(address)
    assert rig.diagnostic_count(DIAGNOSTIC) - count == len(invalid) + 2 * len(holes)
    # Core width rejection happens before the controller callback. QTest's
    # scalar unaligned accesses split into the exact natural-width chunks.
    for address, local, region in ((CC + 4, 4, "npucc-cc-0"),
                                    (CC + 0x404, 4, "npucc-cc-400"),
                                    (DSP_PLL + 4, 4, "npucc-qdsp6ss_pll-0"),
                                    (DSP + 0x28, 0, "npucc-qdsp6ss-28")):
        operations = (
            (f"writeb {address:#x} 0", [("write", local, 1)]),
            (f"writew {address:#x} 0", [("write", local, 2)]),
            (f"writel {address + 1:#x} 0", [("write", local + 1, 1),
                                           ("write", local + 2, 2), ("write", local + 4, 1)]),
            (f"readb {address:#x}", [("read", local, 1)]),
            (f"readw {address + 1:#x}", [("read", local + 1, 1), ("read", local + 2, 1)]))
        for operation, chunks in operations:
            old_log = rig.log_path.read_text(errors="replace")
            rig.command(operation)
            assert rig.snapshot() == before, operation
            new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
            matches = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
                                 r"size ([0-9]+), region '([^']+)', reason: ([^\n]+)", new_log)
            actual = [(kind, int(address, 16), int(size), name, reason)
                      for kind, address, size, name, reason in matches]
            expected = [(kind, offset, size, region, "invalid size (min:4 max:4)")
                        for kind, offset, size in chunks]
            assert actual == expected, (operation, actual, expected, new_log)


def read_only_feedback_cannot_be_forged_or_erased(rig):
    configure_all(rig)
    for base, *_ in PLLS:
        rig.write(base, rig.read(base) | LOCK | ACK | PCAL)
        assert not rig.read(base) & (LOCK | ACK | PCAL)
    for base, *_ in RCGS:
        rig.write(base, OFF)
        assert rig.read(base) == OFF
    for offset, _, _ in BRANCHES:
        rig.write(CC + offset, OFF)
        assert rig.read(CC + offset) == OFF
    enable_all(rig)
    for index, (base, name, _, _, hz) in enumerate(PLLS):
        rig.write(base, rig.read(base) & ~(LOCK | ACK | PCAL))
        assert rig.read(base) & (LOCK | PCAL) == LOCK | PCAL
        rig.expect_hz(name, hz)
    for index, (base, _, _) in enumerate(RCGS):
        configure_rcg(rig, index, 0)
        rig.write(base, rig.read(base) | OFF)
        expect_root(rig, index, XO)


def system_reset_cancels_all_pending_work_and_retains_external_requests(rig):
    configured_outputs(rig)
    rig.fixture("npu_cc_noc_ahb_clk_parent", 42_000_000)
    rig.fixture("core_bi_pll_test_se", 96_000_000)
    rig.gpio("remote-vote", 27, True)
    capture_three_latches(rig)
    capture_six_rcgs(rig)
    for offset, mask in RESETS:
        rig.write(CC + offset, rig.read(CC + offset) | mask)
    assert all(pll_state(rig, i, "latch-pending") for i in range(3))
    assert all(rcg_state(rig, i, "pending") for i in range(6))
    rig.reset()
    assert all(rig.get(name) in (False, 0) for name in STATE_PROPERTIES)
    assert rig.get("crc-reference-configured") is False
    rig.expect_hz("npu_cc_noc_ahb_clk_parent", 42_000_000)
    rig.expect_hz("core_bi_pll_test_se", 96_000_000)
    branch_hz(rig, 27, 42_000_000)
    assert rig.read(CC + BRANCHES[27][0]) == 0
    for index, (offset, mask) in enumerate(RESETS):
        assert not rig.read(CC + offset) & mask
    rig.raw_read(0x80000000)
    assert all(not rig.irq.get(i, False) for i in range(7))
    for index in range(6):
        assert rig.read(RCGS[index][0]) == OFF
        expect_root(rig, index, 0)
    rig.step(100_000)
    assert all(rig.get(name) in (False, 0) for name in STATE_PROPERTIES)
    for name in OUTPUTS:
        rig.expect_hz(name, 42_000_000 if name == "npu_cc_noc_ahb_clk" else 0)
    rig.gpio("remote-vote", 27, False)
    branch_hz(rig, 27, 0)


def qtest_fixture_types_and_connected_sources_are_protected(rig):
    rig.fixture("core_bi_pll_test_se", 80_000_000)
    before = {name: rig.period(name) for name in INPUTS}
    for value in (-1, 1 << 32, True, 1.5, "80000000"):
        try:
            rig.fixture("core_bi_pll_test_se", value)
        except AssertionError as error:
            assert "error" in str(error), str(error)
        else:
            raise AssertionError((value, "invalid uint32 fixture write accepted"))
    for name in INPUTS[:4]:
        try:
            rig.fixture(name, 123)
        except AssertionError as error:
            assert "cannot override a connected clock source" in str(error), str(error)
        else:
            raise AssertionError((name, "connected provider overridden"))
    assert {name: rig.period(name) for name in INPUTS} == before


ORDINARY_TESTS = (
    cold_reference_has_no_running_or_invented_clock,
    actual_vendor_three_bank_configure_vector_remains_in_standby,
    physically_separate_pll_banks_do_not_alias,
    three_staggered_pll_lock_deadlines_are_independent,
    every_pll_requires_both_output_gates_and_rejects_unknown_selection,
    pll0_lock_and_even_output_do_not_depend_on_crc_programming,
    crc_reference_vector_controls_only_the_divide_two_path,
    postdivider_zero_means_divide_one_and_unknown_encodings_are_atomic,
    v2_nominal_two_ghz_accepts_only_the_ceil_q16_endpoint,
    unsupported_analog_recipe_stops_only_the_affected_pll,
    three_captured_pll_latches_keep_old_outputs_until_ack_and_relock,
    standby_reset_and_cancel_are_local_to_each_pll,
    late_dispatch_uses_original_latch_deadlines_for_all_three_plls,
    six_independent_rcgs_capture_shadow_and_latch_at_one_microsecond,
    source_maps_route_pll_postdivider_crc_and_dsp_outputs,
    hm_rcgs_are_separate_and_hm1_branches_follow_the_pinned_hm0_parent,
    six_hid_fields_cover_bypass_integer_and_fractional_division,
    undefined_source_codes_and_nonzero_mnd_modes_do_not_commit,
    legal_unknown_input_can_latch_without_creating_a_frequency,
    real_gcc_parent_clocks_propagate_through_board_links,
    all_33_branches_have_independent_local_and_remote_requests,
    unknown_external_branch_parents_remain_zero_and_shared_inputs_fan_out,
    shared_ares_and_cbcr_bits_preserve_each_other_without_invented_gating,
    seven_reset_outputs_follow_binding_order_and_shared_word_masks,
    reserved_registers_masks_and_narrow_accesses_are_atomic,
    read_only_feedback_cannot_be_forged_or_erased,
    system_reset_cancels_all_pending_work_and_retains_external_requests,
    qtest_fixture_types_and_connected_sources_are_protected,
)


def default_profile_rejects_all_defined_mmio_and_stays_uninitialized(args, out):
    rig = NpuRig(args, out / "default-profile", reference=None)
    try:
        assert rig.get("enabled-reference") is False
        before = {name: rig.get(name) for name in STATE_PROPERTIES}
        count = rig.diagnostic_count(DIAGNOSTIC)
        for address in REGISTERS:
            rig.read(address)
            rig.write(address, 1)
        assert rig.diagnostic_count(DIAGNOSTIC) - count == 2 * len(REGISTERS)
        rig.fixture("npu_cc_noc_ahb_clk_parent", 80_000_000)
        rig.fixture("core_bi_pll_test_se", 60_000_000)
        for index in range(33):
            rig.gpio("remote-vote", index, True)
        rig.step(100_000)
        assert {name: rig.get(name) for name in STATE_PROPERTIES} == before
        assert rig.get("crc-reference-configured") is False
        for name in OUTPUTS:
            rig.expect_hz(name, 0)
        rig.raw_read(0x80000000)
        assert all(not rig.irq.get(index, False) for index in range(7))
    finally:
        rig.close()


def normal_tcg_exposes_no_fixture_setters(args, out):
    rig = NpuRig(args, out / "paused-tcg", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        names = {item["name"] for item in rig.monitor("qom-list", {"path": PATH})}
        for name in INPUTS:
            assert "qtest-" + name + "-hz" not in names
            try:
                rig.fixture(name, 80_000_000)
            except AssertionError as error:
                assert "not found" in str(error), str(error)
            else:
                raise AssertionError((name, "ordinary TCG exposed fixture setter"))
        assert rig.monitor("query-status")["running"] is False
        return {"accel": "tcg", "guest_cpu_executed": False,
                "paused_before_and_after": True, "fixture_properties_absent": True}
    finally:
        rig.close()


WINDOWS = (
    (CC, 0, 0x44, "cc"), (CC, 0x400, 0x44, "cc"),
    (CC, 0x1000, 4, "cc"), (CC, 0x1010, 8, "cc"),
    (CC, 0x1030, 4, "cc"), (CC, 0x1060, 8, "cc"),
    (CC, 0x1074, 0xc, "cc"), (CC, 0x1090, 0x24, "cc"),
    (CC, 0x10b8, 4, "cc"), (CC, 0x10c0, 0x24, "cc"),
    (CC, 0x10f0, 4, "cc"), (CC, 0x1100, 8, "cc"),
    (CC, 0x1110, 4, "cc"), (CC, 0x1130, 4, "cc"),
    (CC, 0x1140, 8, "cc"), (CC, 0x1150, 4, "cc"),
    (CC, 0x11a0, 8, "cc"), (CC, 0x11b0, 8, "cc"),
    (CC, 0x1200, 4, "cc"), (CC, 0x1210, 0x10, "cc"),
    (CC, 0x1224, 4, "cc"), (CC, 0x1234, 8, "cc"),
    (CC, 0x1400, 8, "cc"), (CC, 0x1410, 4, "cc"),
    (DSP, 0x28, 8, "qdsp6ss"), (DSP_PLL, 0, 0x44, "qdsp6ss_pll"),
)


def three_physical_banks_have_only_26_sparse_windows_and_leave_gdsc_unmapped(args, out):
    rig = NpuRig(args, out / "physical-windows-and-gaps")
    try:
        mtree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory / "mtree.txt").write_text(mtree)
        found = set()
        for line in mtree.splitlines():
            match = re.search(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (npucc-[^\s]+)", line)
            if match:
                first, last, name = match.groups()
                found.add((int(first, 16), int(last, 16), name))
        expected = {(base + offset, base + offset + size - 1, f"npucc-{bank}-{offset:x}")
                    for base, offset, size, bank in WINDOWS}
        assert found == expected and len(found) == 26, (found, expected, mtree)
        configured_outputs(rig)
        before = rig.snapshot()
        # The separate DT GDSC at 0x09981004 has no implementation here.
        # Actual memory-core decode errors must accompany the unchanged state.
        gaps = (CC + 0x1004, CC + 0x1040, CC + 0x11a8, CC + 0x1500,
                DSP, DSP + 0x24, DSP + 0x30, DSP_PLL + 0x44)
        for address in gaps:
            old_log = rig.log_path.read_text(errors="replace")
            rig.write(address, 0x12345678)
            rig.read(address)
            assert rig.snapshot() == before, hex(address)
            new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
            matches = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
                                 r"size ([0-9]+), region '([^']+)', reason: ([^\n]+)", new_log)
            actual = [(kind, int(addr, 16), int(size), region, reason)
                      for kind, addr, size, region, reason in matches]
            # io_mem_init gives the unassigned region a NULL name and no
            # owner; the Linux CI host renders that diagnostic as '(null)'.
            assert actual == [("write", address, 4, "(null)", "rejected"),
                              ("read", address, 4, "(null)", "rejected")], (address, actual, new_log)
    finally:
        rig.close()


def take_snapshot(source, path, witness):
    source.raw_write(0x80010000, witness)
    source.monitor("stop")
    assert source.monitor("query-status")["running"] is False
    before = source.snapshot()
    source.monitor("migrate", file_channels(path))
    state = wait_migration(source)
    assert state["status"] == "completed"
    assert source.monitor("query-status")["status"] == "postmigrate"
    assert path.stat().st_size > 0
    return before, state


def restore_without_device_mmio(target, path, clock, before, witness, expected_irqs=()):
    target.last_clock = int(target.command(f"clock_set {clock}")[0], 0)
    assert target.last_clock == clock
    target.monitor("migrate-incoming", file_channels(path))
    state = wait_migration(target)
    assert state["status"] == "completed"
    assert target.monitor("query-status")["running"] is False
    # A controller MMIO read would update state, republish outputs and repair
    # missing post_load timer scheduling. Do not use snapshot/read here.
    assert {name: target.get(name) for name in STATE_PROPERTIES} == before["state"]
    for group in ("outputs", "inputs"):
        for name, period in before[group].items():
            assert target.period(name) == period, (group, name, target.period(name), period)
    # Ordinary RAM drains the GPIO transcript without touching NPUCC.
    assert target.raw_read(0x80010000) == witness
    for index, level in expected_irqs:
        assert target.irq.get(index, False) is level, (index, level, target.irq)
    return state


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = NpuRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, source, target):
        report["migrations"].append({"name": name, "passed": True,
            "source_status": source["status"], "target_status": target["status"],
            "source": source, "destination": target,
            "snapshot_sha256": digest(path), "snapshot_bytes": path.stat().st_size})

    def reject(name, path, clock, source_reference, target_reference, source_state):
        target = new(name, reference=target_reference, incoming=True)
        target.command(f"clock_set {clock}")
        assert target.process.poll() is None
        assert target.get("enabled-reference") is target_reference
        error = None
        request_sent = False
        try:
            request_sent = True
            target.monitor("migrate-incoming", file_channels(path))
        except (AssertionError, OSError, RuntimeError) as exception:
            error = str(exception)
        code = target.process.wait(timeout=20)
        stderr = target.log_path.read_text(errors="replace")
        mismatch = f"{int(target_reference)} != {int(source_reference)}"
        assert request_sent and code != 0
        assert TYPE in stderr and mismatch in stderr, stderr
        assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
        report["rejected_migrations"].append({"name": name, "passed": True,
            "source_status": source_state["status"], "destination_exit": code,
            "source_reference": source_reference, "target_reference": target_reference,
            "request_sent": request_sent, "command_error": error,
            "device": TYPE, "profile_mismatch": mismatch,
            "actual_source_stream": True, "stream_relabelled": False})
        check(name)

    try:
        source = new("migration-three-lock-source")
        configure_all(source)
        source.fixture("npu_cc_noc_ahb_clk_parent", 42_000_000)
        source.fixture("core_bi_pll_test_se", 50_000_000)
        source.gpio("remote-vote", 27, True)
        source.write(CC + RESETS[3][0], 4)  # Shared bit2 ARES, not bit0 enable.
        for index, (base, *_) in enumerate(PLLS):
            output_on(source, index)
            source.write(base + 0x38, 1)
            source.step(1000)
        assert source.last_clock == 3000
        assert all(pll_state(source, i, "lock-pending") for i in range(3))
        lock_path = out / "npucc-v1-three-locks.migration"
        before, sent = take_snapshot(source, lock_path, 0x4e505531)
        target = new("migration-three-lock-target", incoming=True)
        target.expect_hz("npu_cc_noc_ahb_clk_parent", 0)
        got = restore_without_device_mmio(target, lock_path, source.last_clock, before,
                                         0x4e505531, expected_irqs=((3, True),))
        target.expect_hz("npu_cc_noc_ahb_clk_parent", 42_000_000)
        target.expect_hz("npu_cc_noc_ahb_clk", 42_000_000)
        completed("v1_three_lock_deadlines_inputs_vote_gpio_and_ram_roundtrip", lock_path, sent, got)
        check("v1_three_pending_plls_inputs_clock_gpio_and_ram_restore_before_mmio",
              device_mmio_before_observation=False)

        target.monitor("cont")
        target.step(1999)
        assert all(not pll_state(target, i, "locked") for i in range(3))
        for index, (_, name, _, _, hz) in enumerate(PLLS):
            target.step(1)
            for other, (_, other_name, _, _, other_hz) in enumerate(PLLS):
                assert pll_state(target, other, "locked") is (other <= index)
                assert pll_state(target, other, "lock-pending") is (other > index)
                target.expect_hz(other_name, other_hz if other <= index else 0)
            if index < 2:
                target.step(999)
                assert pll_state(target, index + 1, "locked") is False
        # Only now may the first device MMIO verify all migrated registers.
        expected_regs = dict(before["registers"])
        for base, *_ in PLLS:
            expected_regs[hex(base)] |= LOCK | PCAL
        assert {hex(address): target.read(address) for address in REGISTERS} == expected_regs
        target.gpio("remote-vote", 27, False)
        branch_hz(target, 27, 0)
        target.write(CC + RESETS[3][0], 0)
        target.raw_read(0x80000000)
        assert target.irq.get(3, False) is False
        check("v1_three_distinct_lock_deadlines_fire_without_mmio_repair",
              remaining_ns=[2000, 3000, 4000], device_mmio_before_deadline_checks=False)

        latch_source = new("migration-three-latches-six-rcgs-source")
        configured_outputs(latch_source)
        latch_source.fixture("core_bi_pll_test_se", 96_000_000)
        latch_source.fixture("npu_llm_temp_clk", 48_000_000)
        latch_source.gpio("remote-vote", 15, True)
        latch_source.write(CC + RESETS[3][0], 4)
        latch_source.gpio("remote-vote", 11, True)
        capture_three_latches(latch_source)
        capture_six_rcgs(latch_source)
        latch_source.step(400)
        assert all(pll_state(latch_source, i, "latch-pending") for i in range(3))
        assert all(rcg_state(latch_source, i, "pending") for i in range(6))
        latch_path = out / "npucc-v1-three-latches-six-rcgs.migration"
        before, sent = take_snapshot(latch_source, latch_path, 0x4e505532)
        latch_target = new("migration-three-latches-six-rcgs-target", incoming=True)
        got = restore_without_device_mmio(latch_target, latch_path, latch_source.last_clock,
                                         before, 0x4e505532, expected_irqs=((3, True),))
        completed("v1_three_pll_latches_six_rcg_requests_and_shadows_roundtrip", latch_path, sent, got)
        check("v1_nine_independent_captured_requests_restore_before_mmio",
              device_mmio_before_observation=False)

        latch_target.monitor("cont")
        latch_target.step(599)
        for index, (_, name, _, _, hz) in enumerate(PLLS):
            assert pll_state(latch_target, index, "ack") is False
            assert pll_state(latch_target, index, "latch-pending") is True
            latch_target.expect_hz(name, hz)
        for index, (_, name, _) in enumerate(RCGS):
            assert rcg_state(latch_target, index, "pending") is True
            latch_target.expect_hz(name, XO)
        latch_target.step(1)
        for index, (_, name, *_) in enumerate(PLLS):
            l, alpha, _ = LATCH_VALUES[index]
            assert pll_state(latch_target, index, "ack") is True
            assert pll_state(latch_target, index, "latch-pending") is False
            assert pll_state(latch_target, index, "lock-pending") is True
            assert pll_state(latch_target, index, "locked") is False
            assert pll_state(latch_target, index, "active-l") == l
            assert pll_state(latch_target, index, "active-alpha") == alpha
            latch_target.expect_hz(name, 0)
        for index, (_, name, _) in enumerate(RCGS):
            assert rcg_state(latch_target, index, "pending") is False
            assert rcg_state(latch_target, index, "active-cfg") == rcg_cfg(index, 7, CAPTURED_HIDS[index])
            latch_target.expect_hz(name, divided(96_000_000, CAPTURED_HIDS[index]))
        # At this point pure getters and actual Clock periods proved timer
        # restoration. Reading the register image can no longer repair it.
        for index, (base, *_) in enumerate(PLLS):
            assert latch_target.read(base) & (ACK | UPDATE) == ACK | UPDATE
            assert latch_target.read(base + 4) == 44 + index
            assert latch_target.read(base + 0x40) == 0
            latch_target.write(base, latch_target.read(base) & ~UPDATE)
            assert not latch_target.read(base) & ACK
        for index, (base, _, _) in enumerate(RCGS):
            assert not latch_target.read(base) & BUSY
            assert latch_target.read(base + 4) == rcg_cfg(index, 0, 31)
        latch_target.step(4999)
        assert all(not pll_state(latch_target, i, "locked") for i in range(3))
        latch_target.step(1)
        for index, (_, name, *_) in enumerate(PLLS):
            latch_target.expect_hz(name, LATCH_VALUES[index][2])
        latch_target.fixture("core_bi_pll_test_se", 48_000_000)
        for index, (_, name, _) in enumerate(RCGS):
            latch_target.expect_hz(name, divided(48_000_000, CAPTURED_HIDS[index]))
        branch_hz(latch_target, 11, 48_000_000)
        latch_target.gpio("remote-vote", 11, False)
        branch_hz(latch_target, 11, 0)
        branch_hz(latch_target, 15, 48_000_000)
        latch_target.fixture("npu_llm_temp_clk", 36_000_000)
        branch_hz(latch_target, 15, 36_000_000)
        assert latch_target.read(CC + RESETS[3][0]) & 4
        latch_target.gpio("remote-vote", 15, False)
        branch_hz(latch_target, 15, 0)
        check("v1_nine_captured_deadlines_and_new_input_changes_resume_without_mmio_repair",
              remaining_ns=600, device_mmio_before_deadline_checks=False)

        reject("reference_to_default_profile_is_rejected", lock_path, source.last_clock,
               True, False, report["migrations"][0]["source"])
        disabled = new("migration-default-source", reference=False)
        disabled.fixture("npu_cc_noc_ahb_clk_parent", 30_000_000)
        disabled.gpio("remote-vote", 27, True)
        disabled.raw_write(0x80010000, 0x4e505530)
        disabled.monitor("stop")
        assert disabled.monitor("query-status")["running"] is False
        disabled_path = out / "npucc-v1-default.migration"
        disabled.monitor("migrate", file_channels(disabled_path))
        disabled_state = wait_migration(disabled)
        assert disabled_state["status"] == "completed"
        assert disabled.monitor("query-status")["status"] == "postmigrate"
        reject("default_to_reference_profile_is_rejected", disabled_path, disabled.last_clock,
               False, True, disabled_state)
    finally:
        for rig in reversed(rigs):
            rig.close()


EXPECTED_CHECKS = 37


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.machine = ("tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
                    "rpmh-profile=off,rpmh-boot-configured=off")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    initial_hash = digest(args.qemu)
    report = {"schema": "tb371fc-npucc-sm8250-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [], "migrations": [],
        "rejected_migrations": [], "expected_completed_migrations": 2,
        "expected_rejected_migrations": 2, "vmstate_version": 1,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "register_count": 95, "sparse_window_count": 26, "branch_count": 33,
        "reset_count": 7, "input_clock_count": 12, "pll_count": 3,
        "rcg_count": 6, "output_clock_count": 45,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "npu_execution_observed": False, "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP transactions, real Clock periods, "
                 "and migration of an explicit digital reference profile",
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "source_sha256": {
            "npucc-kona.c": "6c5e241d61d685e0bb14bcfcebd689d36d23d0c803a4bf621105bae03d1ade60",
            "qcom,npucc-kona.h": "6b5b44c191c3dc131be3abfe746f5a321bde60e006a7ca6fc8527e897b8d987a",
            "clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac"},
        "quantized_v2_endpoint_hz": {"pll0": 2_000_000_097, "crc_div": 1_000_000_048}}
    start = time.monotonic()
    try:
        assert len(ORDINARY_TESTS) == 28
        assert (len(REGISTERS), len(WINDOWS), len(BRANCHES), len(RESETS), len(INPUTS),
                len(PLLS), len(RCGS), len(OUTPUTS)) == (95, 26, 33, 7, 12, 3, 6, 45)
        for test in ORDINARY_TESTS:
            rig = None
            try:
                rig = NpuRig(args, out / test.__name__)
                test(rig)
                report["checks"].append({"name": test.__name__, "passed": True})
            finally:
                if rig:
                    rig.close()
        default_profile_rejects_all_defined_mmio_and_stays_uninitialized(args, out)
        report["checks"].append({"name": "default_profile_rejects_all_defined_mmio_and_stays_uninitialized",
                                 "passed": True})
        paused = normal_tcg_exposes_no_fixture_setters(args, out)
        report["checks"].append({"name": "normal_tcg_exposes_no_fixture_setters", "passed": True, **paused})
        three_physical_banks_have_only_26_sparse_windows_and_leave_gdsc_unmapped(args, out)
        report["checks"].append({"name": "three_physical_banks_have_only_26_sparse_windows_and_leave_gdsc_unmapped",
                                 "passed": True})
        migration_checks(args, out, report)
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
        report["passed"] = (len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 2 and
            all(item["source_status"] == item["target_status"] == "completed"
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and
            all(item["passed"] is True for item in report["rejected_migrations"]) and
            report["binary_unchanged"])
    except BaseException as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
    report["duration_seconds"] = round(time.monotonic() - start, 3)
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
