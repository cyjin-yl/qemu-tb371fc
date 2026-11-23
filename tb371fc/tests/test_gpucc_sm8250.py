#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled GPUCC reference transactions, real Clocks, and migration.

The register vectors come from OnePlusOSS/android_kernel_oneplus_sm8250 at
94304e12a3509eac7b3716bf574ce0b1fcaacbae. Tests explicitly opt into the bounded
digital reference model. They do not establish native GPU probe, analogue PLL
behavior, GPU execution, or Android boot. No guest CPU instructions execute.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import traceback

from test_geni_uart import Rig, connect_unix
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

BASE = 0x03d90000
PATH = "/machine/gpucc"
GCC = 0x00100000
PERIOD_UNIT = 1_000_000_000 << 32
XO = 19_200_000
MODE, L, CAL_L, USER, USER_U, USER_U1 = (0x100, 0x104, 0x108, 0x10c, 0x110, 0x114)
CONFIG, CONFIG_U, CONFIG_U1 = 0x118, 0x11c, 0x120
STATUS, OPMODE, ALPHA = 0x130, 0x138, 0x140
OUTCTRL, RESET_N, UPDATE, UPDATE_BYPASS = 1, 4, 1 << 22, 1 << 23
PCAL, ACK, LOCK, OFF = 1 << 27, 1 << 29, 1 << 31, 1 << 31
CMD, CFG, ROOT, BUSY = 0x1120, 0x1124, 2, 1
CX_GMU, GX_GMU = 0x1098, 0x1064
PLL_HZ = (XO * ((0x1a << 16) | 0xaaa)) >> 16
CONFIG_VECTOR = ((L, 0x1a), (CAL_L, 0x44), (ALPHA, 0xaaa),
                 (CONFIG, 0x20485699), (CONFIG_U, 0x2261),
                 (CONFIG_U1, 0x029a699c), (USER_U, 0x805))
BRANCHES = (
    (0x1078, "gpu_cc_ahb_clk"), (0x107c, "gpu_cc_crc_ahb_clk"),
    (0x1088, "gpu_cc_cx_apb_clk"), (0x1098, "gpu_cc_cx_gmu_clk"),
    (0x1080, "gpu_cc_cx_qdss_at_clk"), (0x1094, "gpu_cc_cx_qdss_trig_clk"),
    (0x1084, "gpu_cc_cx_qdss_tsctr_clk"), (0x108c, "gpu_cc_cx_snoc_dvm_clk"),
    (0x1004, "gpu_cc_cxo_aon_clk"), (0x109c, "gpu_cc_cxo_clk"),
    (0x1064, "gpu_cc_gx_gmu_clk"), (0x105c, "gpu_cc_gx_qdss_tsctr_clk"),
    (0x1058, "gpu_cc_gx_vsense_clk"), (0x1090, "gpu_cc_sleep_clk"),
    (0x5000, "gpu_cc_hlos1_vote_gpu_smmu_clk"),
)
RESETS = (0x1160, 0x1068, 0x10a0, 0x111c, 0x1000)
GMU_INDICES, QDSS_INDICES = (3, 10), (4, 5, 6, 11)
EXTERNAL_INDICES = tuple(i for i in range(15) if i not in GMU_INDICES + QDSS_INDICES)
INPUTS = ("xo", "gpu_cc_pll0", "gcc_gpu_gpll0_clk_src",
          "gcc_gpu_gpll0_div_clk_src", "core_bi_pll_test_se", "qdss_qmp_clk") + tuple(
              BRANCHES[i][1] + "_parent" for i in EXTERNAL_INDICES)
UNKNOWN_INPUTS = tuple(name for name in INPUTS if name != "xo")
PLL_REGS = (MODE, L, CAL_L, USER, USER_U, USER_U1, CONFIG, CONFIG_U,
            CONFIG_U1, 0x124, 0x128, 0x12c, OPMODE, ALPHA)
REGISTERS = tuple(sorted(PLL_REGS + tuple(x[0] for x in BRANCHES) + RESETS + (CMD, CFG)))
STATE_PROPERTIES = ("pll-locked", "pll-ack", "pll-calibrated", "pll-lock-pending",
                    "pll-latch-pending", "rcg-pending", "pll-active-l",
                    "pll-active-alpha", "rcg-active-cfg")
DIAGNOSTIC = "qcom-sm8250-gpucc:"


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def file_channels(path):
    return {"channels": [{"channel-type": "main", "addr": {
        "transport": "file", "filename": str(path), "offset": 0}}]}


class GpuRig(Rig):
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", gdsc_reference=False):
        self.args = args
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qgpucc-"))
        self.last_clock = 0
        self.reference = reference
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = [args.qemu, "-machine", args.machine, "-accel", accel,
                   "-m", "128M", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors",
                   "-qmp", f"unix:{self.socket_dir}/m,server=on,wait=off"]
        if accel == "qtest":
            command += ["-qtest", f"unix:{self.socket_dir}/t,server=on,wait=off",
                        "-qtest-log", "/dev/null"]
        if reference is not None:
            command += ["-global", "qcom-sm8250-gpucc.enabled-reference=" +
                        ("on" if reference else "off")]
        if gdsc_reference:
            command += ["-global", "qcom-gdsc.enabled-reference=on",
                        "-global", "qcom-gdsc.reference-on=off",
                        "-global", "qcom-gpu-cx-gdsc.boot-collapsed-reference=on"]
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
        return self.raw_read(BASE + offset)

    def write(self, offset, value):
        self.raw_write(BASE + offset, value)

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
        return {"registers": {hex(offset): self.read(offset) for offset in REGISTERS},
                "state": {name: self.get(name) for name in STATE_PROPERTIES},
                "outputs": {name: self.period(name) for name in
                            ("gpu_cc_pll1", "gpu_cc_gmu_clk_src") +
                            tuple(item[1] for item in BRANCHES)}}

    def gpio(self, name, number, value, path=PATH):
        self.command(f"set_irq_in {path} {name} {number} {int(value)}")

    def diagnostic_count(self, needle):
        return self.log_path.read_text(errors="replace").count(needle)

    def reset(self):
        self.monitor("system_reset")
        self.wait_for(lambda: self.read(L) == 0 and self.read(MODE) == 0,
                      "GPUCC reset has cleared a programmed PLL witness")

    def close(self):
        super().close()
        for name in ("qtest_transcript", "qmp_transcript"):
            stream = getattr(self, name, None)
            if stream and not stream.closed:
                stream.close()


def vendor_configure(rig):
    """The pinned driver's conditional writes and read-modify-write order."""
    assert not (rig.read(MODE) & OUTCTRL and rig.read(OPMODE) & 1)
    for offset, value in CONFIG_VECTOR:
        rig.write(offset, value)
    rig.write(MODE, rig.read(MODE) | UPDATE_BYPASS)
    rig.write(MODE, rig.read(MODE) & ~OUTCTRL)
    rig.write(OPMODE, 0)
    rig.write(MODE, rig.read(MODE) | RESET_N)


def vendor_enable(rig):
    rig.write(OPMODE, 1)
    rig.step(5000)
    assert rig.read(MODE) & LOCK
    rig.write(USER, rig.read(USER) | 7)
    rig.write(MODE, rig.read(MODE) | OUTCTRL)


def gmu_configure(rig, selector, hid=1, *, wait=True, root=True):
    rig.write(CFG, (selector << 8) | hid | (1 << 20))
    rig.write(CMD, (ROOT if root else 0) | BUSY)
    if wait:
        rig.step(1000)
        assert not rig.read(CMD) & BUSY


def gmu_rates(rig, cx, gx):
    for offset, name, hz in ((CX_GMU, "gpu_cc_cx_gmu_clk", cx),
                             (GX_GMU, "gpu_cc_gx_gmu_clk", gx)):
        rig.expect_hz(name, hz)
        assert bool(rig.read(offset) & OFF) == (hz == 0), (name, rig.read(offset), hz)


def cold_reference_has_no_running_or_invented_clock(rig):
    assert len(REGISTERS) == len(set(REGISTERS)) == 36
    for offset in PLL_REGS + RESETS + (CFG,):
        assert rig.read(offset) == 0, (hex(offset), rig.read(offset))
    assert rig.read(CMD) == OFF
    for offset, name in BRANCHES:
        assert rig.read(offset) == OFF, (name, rig.read(offset))
        rig.expect_hz(name, 0)
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.expect_hz("gpu_cc_gmu_clk_src", 0)
    rig.expect_hz("xo", XO)
    for name in UNKNOWN_INPUTS:
        rig.expect_hz(name, 0)
    for name in STATE_PROPERTIES:
        assert rig.get(name) in (False, 0), (name, rig.get(name))


def actual_vendor_configure_vector_remains_in_standby(rig):
    vendor_configure(rig)
    for offset, value in CONFIG_VECTOR:
        assert rig.read(offset) == value, (offset, rig.read(offset), value)
    assert rig.read(MODE) == UPDATE_BYPASS | RESET_N
    assert rig.read(USER) == 0 and rig.read(OPMODE) == 0
    rig.step(100_000)
    assert not rig.read(MODE) & (LOCK | PCAL | ACK)
    assert rig.get("pll-locked") is False
    rig.expect_hz("gpu_cc_pll1", 0)
    # Match the second explicit probe operation: CX GMU WAKE/SLEEP fields.
    rig.write(CX_GMU, (rig.read(CX_GMU) & ~0xff0) | 0xff0)
    assert rig.read(CX_GMU) == OFF | 0xff0
    rig.expect_hz("gpu_cc_cx_gmu_clk", 0)


def lucid_lock_is_delayed_and_outputs_have_two_gates(rig):
    vendor_configure(rig)
    rig.write(OPMODE, 1)
    assert rig.get("pll-lock-pending") is True
    rig.step(4999)
    assert not rig.read(MODE) & (LOCK | PCAL)
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.step(1)
    assert rig.read(MODE) & (LOCK | PCAL) == LOCK | PCAL
    assert rig.get("pll-locked") is True and rig.get("pll-calibrated") is True
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.write(USER, 7)
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.write(MODE, rig.read(MODE) | OUTCTRL)
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    count = rig.diagnostic_count(DIAGNOSTIC)
    for unsupported in range(1, 7):
        rig.write(USER, unsupported)
        assert rig.read(USER) == 7
        rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    assert rig.diagnostic_count(DIAGNOSTIC) == count + 6
    rig.write(USER, 0)
    rig.expect_hz("gpu_cc_pll1", 0)
    assert rig.read(MODE) & LOCK
    rig.write(USER, 7)
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    rig.write(MODE, rig.read(MODE) & ~OUTCTRL)
    rig.expect_hz("gpu_cc_pll1", 0)


def invalid_l_and_unqualified_analog_programming_cannot_lock(rig):
    for multiplier in (0, 1, 0xffff):
        vendor_configure(rig)
        rig.write(L, multiplier)
        rig.write(OPMODE, 1)
        rig.step(100_000)
        assert not rig.read(MODE) & (LOCK | PCAL | ACK), multiplier
        rig.expect_hz("gpu_cc_pll1", 0)
        rig.write(OPMODE, 0)
    vendor_configure(rig)
    rig.write(CONFIG, rig.read(CONFIG) ^ 1)
    rig.write(OPMODE, 1)
    rig.step(100_000)
    assert not rig.read(MODE) & (LOCK | PCAL)
    rig.expect_hz("gpu_cc_pll1", 0)


def calibration_survives_standby_and_real_reset_clears_it(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    rig.write(MODE, rig.read(MODE) & ~OUTCTRL)
    rig.write(USER, rig.read(USER) & ~7)
    rig.write(OPMODE, 0)
    rig.write(MODE, rig.read(MODE) | RESET_N)
    assert rig.read(MODE) & PCAL and not rig.read(MODE) & LOCK
    assert rig.get("pll-calibrated") is True
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.reset()
    assert rig.read(MODE) == 0 and rig.get("pll-calibrated") is False
    rig.step(100_000)
    assert rig.read(MODE) == 0


def pll_latch_captures_shadow_and_requires_software_ack_clear(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.write(L, 31)
    rig.write(ALPHA, 0x4000)
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    rig.write(MODE, rig.read(MODE) | UPDATE)
    assert rig.get("pll-latch-pending") is True
    # A later shadow write must not alter the already captured latch request.
    rig.write(L, 40)
    rig.write(ALPHA, 0)
    rig.step(999)
    assert not rig.read(MODE) & ACK
    assert rig.get("pll-active-l") == 0x1a
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    rig.step(1)
    assert rig.read(MODE) & (UPDATE | ACK) == UPDATE | ACK
    assert rig.get("pll-active-l") == 31 and rig.get("pll-active-alpha") == 0x4000
    assert rig.read(L) == 40 and rig.read(ALPHA) == 0
    assert not rig.read(MODE) & LOCK
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.write(MODE, rig.read(MODE) & ~UPDATE)
    assert not rig.read(MODE) & ACK
    rig.step(4999)
    rig.expect_hz("gpu_cc_pll1", 0)
    rig.step(1)
    rig.expect_hz("gpu_cc_pll1", 600_000_000)
    assert rig.read(MODE) & LOCK
    # Only a second UPDATE may adopt the later 40.0x shadow configuration.
    rig.write(MODE, rig.read(MODE) | UPDATE)
    rig.step(1000)
    assert rig.get("pll-active-l") == 40 and rig.get("pll-active-alpha") == 0
    rig.write(MODE, rig.read(MODE) & ~UPDATE)
    rig.step(5000)
    rig.expect_hz("gpu_cc_pll1", 768_000_000)


def rcg_shadow_is_atomic_and_commits_at_the_reference_deadline(rig):
    rig.write(CX_GMU, 1)
    gmu_configure(rig, 0)
    gmu_rates(rig, XO, 0)
    rig.fixture("core_bi_pll_test_se", 80_000_000)
    gmu_configure(rig, 7, wait=False)
    pending = (7 << 8) | 1 | (1 << 20)
    rig.write(CFG, (1 << 20) | 2)  # XO/1.5, a later shadow value.
    assert rig.get("rcg-active-cfg") != pending
    rig.step(999)
    assert rig.read(CMD) & BUSY
    gmu_rates(rig, XO, 0)
    rig.step(1)
    assert not rig.read(CMD) & BUSY
    assert rig.get("rcg-active-cfg") == pending
    assert rig.read(CFG) == (1 << 20) | 2
    gmu_rates(rig, 80_000_000, 0)
    rig.write(CMD, ROOT | BUSY)
    rig.step(1000)
    gmu_rates(rig, 12_800_000, 0)


def rcg_hid_zero_one_and_fractional_divisor_follow_the_driver(rig):
    rig.write(CX_GMU, 1)
    rig.write(GX_GMU, 1)
    for hid, hz in ((0, XO), (1, XO), (2, 12_800_000), (3, 9_600_000), (31, 1_200_000)):
        gmu_configure(rig, 0, hid)
        gmu_rates(rig, hz, hz)


def legal_unknown_rcg_source_latches_without_inventing_a_clock(rig):
    rig.write(CX_GMU, 1)
    for selector, name in ((1, "gpu_cc_pll0"), (7, "core_bi_pll_test_se")):
        gmu_configure(rig, selector)
        assert rig.read(CMD) & OFF and not rig.read(CMD) & BUSY
        gmu_rates(rig, 0, 0)
        rig.fixture(name, 80_000_000)
        assert not rig.read(CMD) & OFF
        gmu_rates(rig, 80_000_000, 0)
        rig.fixture(name, 0)
        gmu_rates(rig, 0, 0)
        assert rig.read(CMD) & OFF


def undefined_source_and_mnd_mode_do_not_commit(rig):
    rig.write(CX_GMU, 1)
    gmu_configure(rig, 0)
    active = rig.get("rcg-active-cfg")
    for value in ((2 << 8) | 1, (4 << 8) | 1, (1 << 12) | 1):
        rig.write(CMD, ROOT)
        rig.write(CFG, value)
        rig.write(CMD, ROOT | BUSY)
        rig.step(100_000)
        assert rig.read(CMD) & BUSY
        assert rig.get("rcg-active-cfg") == active
        gmu_rates(rig, XO, 0)
    rig.write(CMD, ROOT)
    gmu_configure(rig, 0, 3)
    gmu_rates(rig, XO // 2, 0)


def live_pll_feeds_both_gmu_branches(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.write(CX_GMU, 1)
    rig.write(GX_GMU, 1)
    gmu_configure(rig, 3)
    gmu_rates(rig, PLL_HZ, PLL_HZ)
    rig.write(MODE, rig.read(MODE) & ~OUTCTRL)
    gmu_rates(rig, 0, 0)
    rig.write(MODE, rig.read(MODE) | OUTCTRL)
    gmu_rates(rig, PLL_HZ, PLL_HZ)
    rig.write(CX_GMU, 0)
    gmu_rates(rig, 0, PLL_HZ)
    rig.gpio("remote-vote", 3, True)
    gmu_rates(rig, PLL_HZ, PLL_HZ)
    rig.write(GX_GMU, 0)
    gmu_rates(rig, PLL_HZ, 0)
    rig.gpio("remote-vote", 3, False)
    gmu_rates(rig, 0, 0)


def actual_gcc_provider_changes_propagate_through_board_clock_links(rig):
    def write(offset, value):
        rig.raw_write(GCC + offset, value)
    write(4, 31)
    write(0x40, 0x4000)
    write(0xc, 0x107)
    write(0, (1 << 20) | RESET_N)
    write(0x52018, 1)
    write(0x52000, (1 << 15) | (1 << 16))
    rig.step(5000)
    rig.expect_hz("gcc_gpu_gpll0_clk_src", 600_000_000)
    rig.expect_hz("gcc_gpu_gpll0_div_clk_src", 300_000_000)
    rig.write(CX_GMU, 1)
    gmu_configure(rig, 5)
    gmu_rates(rig, 600_000_000, 0)
    gmu_configure(rig, 6, 2)
    gmu_rates(rig, 200_000_000, 0)
    write(0x52000, 1 << 15)
    gmu_rates(rig, 0, 0)
    assert rig.read(CMD) & OFF
    write(0x52000, (1 << 15) | (1 << 16))
    gmu_rates(rig, 200_000_000, 0)
    write(0x52018, 0)
    gmu_rates(rig, 0, 0)


def every_branch_has_independent_local_and_remote_requests(rig):
    gmu_configure(rig, 0)
    rig.fixture("qdss_qmp_clk", 60_000_000)
    for index in EXTERNAL_INDICES:
        rig.fixture(BRANCHES[index][1] + "_parent", 12_000_000 + index * 1_000_000)
    for index, (offset, name) in enumerate(BRANCHES):
        hz = XO if index in GMU_INDICES else (60_000_000 if index in QDSS_INDICES
                                               else 12_000_000 + index * 1_000_000)
        assert rig.read(offset) & OFF
        rig.expect_hz(name, 0)
        rig.write(offset, 1)
        rig.expect_hz(name, hz)
        assert not rig.read(offset) & OFF
        rig.gpio("remote-vote", index, True)
        rig.write(offset, 0)
        rig.expect_hz(name, hz)
        assert not rig.read(offset) & (OFF | 1)
        rig.gpio("remote-vote", index, False)
        rig.expect_hz(name, 0)
        assert rig.read(offset) & OFF


def unknown_branch_parent_stays_stopped_and_live_qdss_input_fans_out(rig):
    for offset, name in BRANCHES:
        rig.write(offset, 1)
        if offset not in (CX_GMU, GX_GMU):
            rig.expect_hz(name, 0)
            assert rig.read(offset) & OFF
    rig.step(1_000_000)
    for index in EXTERNAL_INDICES + QDSS_INDICES:
        rig.expect_hz(BRANCHES[index][1], 0)
    rig.fixture("qdss_qmp_clk", 75_000_000)
    for index in QDSS_INDICES:
        rig.expect_hz(BRANCHES[index][1], 75_000_000)
    rig.write(BRANCHES[4][0], 0)
    rig.fixture("qdss_qmp_clk", 50_000_000)
    rig.expect_hz(BRANCHES[4][1], 0)
    for index in (5, 6, 11):
        rig.expect_hz(BRANCHES[index][1], 50_000_000)
    rig.fixture("qdss_qmp_clk", 0)
    for index in QDSS_INDICES:
        rig.expect_hz(BRANCHES[index][1], 0)


def reset_registers_drive_five_distinct_gpio_outputs(rig):
    for index, offset in enumerate(RESETS):
        rig.write(offset, 1)
        assert rig.read(offset) == 1
        rig.raw_read(0x80000000)
        assert rig.irq.get(index, False) is True, (index, rig.irq)
        for other in range(5):
            if other != index:
                assert rig.irq.get(other, False) is False, (index, other, rig.irq)
        rig.write(offset, 0)
        rig.raw_read(0x80000000)
        assert rig.irq.get(index, False) is False


def reserved_status_and_invalid_accesses_are_atomic(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.write(CX_GMU, 0xff1)
    gmu_configure(rig, 0)
    before = rig.snapshot()
    count = rig.diagnostic_count(DIAGNOSTIC)
    invalid = [(L, 1 << 16), (ALPHA, 1 << 16), (MODE, 1 << 19),
               (OPMODE, 2), (CFG, 1 << 31), (CMD, 1 << 4),
               (CX_GMU, 1 << 12), (RESETS[0], 2),
               (0x130, 0), (0x134, 0), (0x1060, 0)]
    for offset, value in invalid:
        rig.write(offset, value)
        assert rig.snapshot() == before, (hex(offset), value)
    for offset in (0x130, 0x134, 0x1060):
        rig.read(offset)
        assert rig.snapshot() == before
    assert rig.diagnostic_count(DIAGNOSTIC) - count == len(invalid) + 3
    core_count = rig.diagnostic_count("Invalid ")
    operations = (f"writeb {BASE + L:#x} 0", f"writew {BASE + L:#x} 0",
                  f"writel {BASE + L + 1:#x} 0", f"readb {BASE + L:#x}",
                  f"readw {BASE + L + 1:#x}")
    for operation in operations:
        rig.command(operation)
        assert rig.snapshot() == before, operation
    # MemoryRegionOps rejects these sizes/alignment before the device callback.
    assert rig.diagnostic_count("Invalid ") - core_count >= len(operations)


def read_only_feedback_cannot_be_forged_by_guest_writes(rig):
    vendor_configure(rig)
    rig.write(MODE, rig.read(MODE) | LOCK | ACK | PCAL)
    assert not rig.read(MODE) & (LOCK | ACK | PCAL)
    rig.write(CX_GMU, OFF)
    assert rig.read(CX_GMU) == OFF
    vendor_enable(rig)
    # A register read-modify-write must preserve hardware-generated status.
    rig.write(MODE, rig.read(MODE) & ~(LOCK | ACK | PCAL))
    assert rig.read(MODE) & (LOCK | PCAL) == LOCK | PCAL
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)


def reset_cancels_pending_work_but_preserves_external_inputs_and_votes(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.fixture("gpu_cc_ahb_clk_parent", 80_000_000)
    rig.fixture("core_bi_pll_test_se", 50_000_000)
    rig.gpio("remote-vote", 0, True)
    rig.write(L, 31)
    rig.write(ALPHA, 0x4000)
    rig.write(MODE, rig.read(MODE) | UPDATE)
    gmu_configure(rig, 7, wait=False)
    rig.write(RESETS[0], 1)
    assert rig.get("pll-latch-pending") is True and rig.get("rcg-pending") is True
    rig.reset()
    assert all(rig.get(name) in (False, 0) for name in STATE_PROPERTIES)
    assert rig.read(CFG) == 0 and rig.read(CMD) == OFF
    rig.expect_hz("gpu_cc_ahb_clk_parent", 80_000_000)
    rig.expect_hz("core_bi_pll_test_se", 50_000_000)
    rig.expect_hz("gpu_cc_ahb_clk", 80_000_000)
    assert rig.read(BRANCHES[0][0]) == 0
    for offset in RESETS:
        assert rig.read(offset) == 0
    rig.step(100_000)
    assert not rig.read(MODE) & (LOCK | ACK | PCAL)
    assert not rig.read(CMD) & BUSY
    rig.gpio("remote-vote", 0, False)
    rig.expect_hz("gpu_cc_ahb_clk", 0)


def qtest_fixture_types_and_connected_providers_are_protected(rig):
    rig.fixture("core_bi_pll_test_se", 80_000_000)
    for name, value in (("core_bi_pll_test_se", -1), ("core_bi_pll_test_se", 1 << 32),
                        ("core_bi_pll_test_se", True), ("xo", 0),
                        ("gcc_gpu_gpll0_clk_src", 1), ("gcc_gpu_gpll0_div_clk_src", 1)):
        try:
            rig.fixture(name, value)
        except AssertionError as error:
            assert "error" in str(error), str(error)
        else:
            raise AssertionError((name, value, "invalid fixture write accepted"))
    rig.expect_hz("core_bi_pll_test_se", 80_000_000)
    rig.expect_hz("xo", XO)


def late_timer_dispatch_preserves_the_original_latch_and_relock_deadlines(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.write(L, 31)
    rig.write(ALPHA, 0x4000)
    rig.write(MODE, rig.read(MODE) | UPDATE)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    # Running clock_step dispatches each deadline in order. Warp only while
    # paused, so the latch callback actually runs 5000ns after its deadline.
    rig.last_clock = int(rig.command(f"clock_set {rig.last_clock + 6000}")[0], 0)
    assert rig.get("pll-latch-pending") is True  # No MMIO update while paused.
    assert rig.get("pll-ack") is False
    rig.monitor("cont")
    assert rig.read(MODE) & (ACK | LOCK | UPDATE) == ACK | LOCK | UPDATE
    rig.expect_hz("gpu_cc_pll1", 600_000_000)


def clearing_update_cancels_captured_pll_and_rcg_requests(rig):
    vendor_configure(rig)
    vendor_enable(rig)
    rig.write(CX_GMU, 1)
    gmu_configure(rig, 0)
    active = rig.get("rcg-active-cfg")
    rig.fixture("core_bi_pll_test_se", 80_000_000)
    rig.write(L, 31)
    rig.write(ALPHA, 0x4000)
    rig.write(MODE, rig.read(MODE) | UPDATE)
    gmu_configure(rig, 7, wait=False)
    rig.step(500)
    rig.write(MODE, rig.read(MODE) & ~UPDATE)
    rig.write(CMD, ROOT)
    assert rig.get("pll-latch-pending") is False
    assert rig.get("rcg-pending") is False
    rig.step(100_000)
    assert not rig.read(MODE) & ACK
    assert rig.get("pll-active-l") == 0x1a
    assert rig.get("rcg-active-cfg") == active
    rig.expect_hz("gpu_cc_pll1", PLL_HZ)
    gmu_rates(rig, XO, 0)


ORDINARY_TESTS = (
    cold_reference_has_no_running_or_invented_clock,
    actual_vendor_configure_vector_remains_in_standby,
    lucid_lock_is_delayed_and_outputs_have_two_gates,
    invalid_l_and_unqualified_analog_programming_cannot_lock,
    calibration_survives_standby_and_real_reset_clears_it,
    pll_latch_captures_shadow_and_requires_software_ack_clear,
    rcg_shadow_is_atomic_and_commits_at_the_reference_deadline,
    rcg_hid_zero_one_and_fractional_divisor_follow_the_driver,
    legal_unknown_rcg_source_latches_without_inventing_a_clock,
    undefined_source_and_mnd_mode_do_not_commit,
    live_pll_feeds_both_gmu_branches,
    actual_gcc_provider_changes_propagate_through_board_clock_links,
    every_branch_has_independent_local_and_remote_requests,
    unknown_branch_parent_stays_stopped_and_live_qdss_input_fans_out,
    reset_registers_drive_five_distinct_gpio_outputs,
    reserved_status_and_invalid_accesses_are_atomic,
    read_only_feedback_cannot_be_forged_by_guest_writes,
    reset_cancels_pending_work_but_preserves_external_inputs_and_votes,
    qtest_fixture_types_and_connected_providers_are_protected,
    late_timer_dispatch_preserves_the_original_latch_and_relock_deadlines,
    clearing_update_cancels_captured_pll_and_rcg_requests,
)


def default_profile_rejects_all_defined_mmio_and_stays_uninitialized(args, out):
    rig = GpuRig(args, out / "default-profile", reference=None)
    try:
        assert rig.get("enabled-reference") is False
        before = {name: rig.get(name) for name in STATE_PROPERTIES}
        count = rig.diagnostic_count(DIAGNOSTIC)
        for offset in REGISTERS:
            rig.read(offset)
            rig.write(offset, 1)
        assert rig.diagnostic_count(DIAGNOSTIC) - count == 2 * len(REGISTERS)
        rig.fixture("gpu_cc_ahb_clk_parent", 80_000_000)
        rig.gpio("remote-vote", 0, True)
        rig.step(1_000_000)
        assert before == {name: rig.get(name) for name in STATE_PROPERTIES}
        for _, name in BRANCHES:
            rig.expect_hz(name, 0)
        rig.expect_hz("gpu_cc_pll1", 0)
    finally:
        rig.close()


def normal_tcg_exposes_no_fixture_setters(args, out):
    rig = GpuRig(args, out / "paused-tcg", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        names = {entry["name"] for entry in rig.monitor("qom-list", {"path": PATH})}
        for name in INPUTS:
            assert "qtest-" + name + "-hz" not in names
            try:
                rig.fixture(name, 80_000_000)
            except AssertionError as error:
                assert "not found" in str(error), str(error)
            else:
                raise AssertionError((name, "TCG exposed fixture setter"))
        assert rig.monitor("query-status")["running"] is False
        return {"accel": "tcg", "guest_cpu_executed": False,
                "paused_before_and_after": True, "fixture_properties_absent": True}
    finally:
        rig.close()


def sparse_gpucc_windows_preserve_existing_cx_and_gx_owners(args, out):
    rig = GpuRig(args, out / "sparse-window-owners", gdsc_reference=True)
    try:
        cx_path, gx_path = "/machine/gpu-cx-gdsc", "/machine/gpu-gx-gdsc"
        rig.step(1000)  # Let the independent collapsed CX feedback settle.
        cx_fields = ("control", "control-known", "state-known", "power-on",
                     "transition-pending", "transition-deadline-ns")
        def cx_state():
            return {name: rig.get(name, cx_path) for name in cx_fields}
        cx_before = cx_state()
        assert rig.raw_read(BASE + 0x106c) == 1
        assert rig.raw_read(BASE + 0x1540) == 0
        assert rig.raw_read(BASE + 0x1008) == 0
        assert rig.raw_read(BASE + 0x1508) == 1
        gx_before = (rig.get("clamp-io", gx_path), rig.get("reset-asserted", gx_path))
        vendor_configure(rig)
        vendor_enable(rig)
        rig.write(CX_GMU, 0xff1)
        rig.write(GX_GMU, 1)
        gmu_configure(rig, 3)
        for offset in RESETS:
            rig.write(offset, 1)
        assert cx_state() == cx_before
        assert (rig.get("clamp-io", gx_path), rig.get("reset-asserted", gx_path)) == gx_before
        assert rig.raw_read(BASE + 0x1008) == 0
        before = rig.snapshot()
        # These writes must reach the old GX model, not a new GPUCC array.
        rig.raw_write(BASE + 0x1008, 1)
        assert rig.raw_read(BASE + 0x1008) == 1
        assert rig.get("reset-asserted", gx_path) is True
        rig.raw_write(BASE + 0x1508, 0x11)
        assert rig.raw_read(BASE + 0x1508) == 0x11
        assert rig.get("clamp-io", gx_path) is True
        assert rig.snapshot() == before
        assert cx_state() == cx_before
        rig.raw_write(BASE + 0x1008, 0)
        rig.raw_write(BASE + 0x1508, 0)
        assert rig.get("clamp-io", gx_path) is False
        assert rig.get("reset-asserted", gx_path) is False
        assert rig.snapshot() == before
        rig.raw_write(BASE + 0x106c, (1 << 11) | 1)
        assert rig.raw_read(BASE + 0x106c) == (1 << 11) | 1
        assert rig.get("control", cx_path) == (1 << 11) | 1
        assert rig.raw_read(BASE + 0x1540) == 0
        assert rig.snapshot() == before
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


def restore_snapshot(target, path, clock, before, witness):
    target.command(f"clock_set {clock}")
    target.last_clock = clock
    target.monitor("migrate-incoming", file_channels(path))
    state = wait_migration(target)
    assert state["status"] == "completed"
    assert target.monitor("query-status")["running"] is False
    assert target.snapshot() == before
    assert target.raw_read(0x80010000) == witness
    return state


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = GpuRig(args, out / name, **kwargs)
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
        assert "qcom-sm8250-gpucc" in stderr and mismatch in stderr, stderr
        assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
        report["rejected_migrations"].append({"name": name, "passed": True,
            "source_status": source_state["status"], "destination_exit": code,
            "source_reference": source_reference, "target_reference": target_reference,
            "request_sent": request_sent, "command_error": error,
            "device": "qcom-sm8250-gpucc", "profile_mismatch": mismatch,
            "actual_source_stream": True, "stream_relabelled": False})
        check(name)

    try:
        source = new("migration-lock-source")
        vendor_configure(source)
        source.fixture("gpu_cc_ahb_clk_parent", 80_000_000)
        source.fixture("core_bi_pll_test_se", 50_000_000)
        source.gpio("remote-vote", 0, True)
        source.write(RESETS[0], 1)
        source.write(OPMODE, 1)
        source.step(2000)
        assert source.get("pll-lock-pending") is True
        assert not source.read(MODE) & LOCK
        lock_path = out / "gpucc-v1-lock-pending.migration"
        before, sent = take_snapshot(source, lock_path, 0x47505531)

        target = new("migration-lock-target", incoming=True)
        target.expect_hz("gpu_cc_ahb_clk_parent", 0)
        got = restore_snapshot(target, lock_path, source.last_clock, before, 0x47505531)
        target.expect_hz("gpu_cc_ahb_clk_parent", 80_000_000)
        target.expect_hz("core_bi_pll_test_se", 50_000_000)
        target.expect_hz("gpu_cc_ahb_clk", 80_000_000)
        target.raw_read(0x80000000)
        assert target.irq.get(0, False) is True, target.irq
        completed("v1_lock_pending_clock_input_vote_gpio_and_ram_roundtrip", lock_path, sent, got)
        check("v1_lock_pending_state_inputs_gpio_and_ram_restore")

        target.monitor("cont")
        target.step(2999)
        assert not target.read(MODE) & LOCK
        assert target.get("pll-lock-pending") is True
        target.step(1)
        assert target.read(MODE) & (LOCK | PCAL) == LOCK | PCAL
        target.write(USER, 7)
        target.write(MODE, target.read(MODE) | OUTCTRL)
        target.expect_hz("gpu_cc_pll1", PLL_HZ)
        target.gpio("remote-vote", 0, False)
        target.expect_hz("gpu_cc_ahb_clk", 0)
        target.write(RESETS[0], 0)
        target.raw_read(0x80000000)
        assert target.irq.get(0, False) is False
        check("v1_lock_remaining_3000ns_and_destination_requests_resume")

        latch_source = new("migration-latch-rcg-source")
        vendor_configure(latch_source)
        vendor_enable(latch_source)
        latch_source.write(CX_GMU, 1)
        gmu_configure(latch_source, 0)
        latch_source.fixture("core_bi_pll_test_se", 80_000_000)
        latch_source.write(L, 31)
        latch_source.write(ALPHA, 0x4000)
        latch_source.write(MODE, latch_source.read(MODE) | UPDATE)
        gmu_configure(latch_source, 7, wait=False)
        latch_source.write(L, 40)  # Keep pending and later shadow distinguishable.
        latch_source.write(ALPHA, 0)
        latch_source.write(CFG, (1 << 20) | 3)
        latch_source.gpio("remote-vote", 3, True)
        latch_source.write(CX_GMU, 0)
        latch_source.step(400)
        assert latch_source.get("pll-latch-pending") is True
        assert latch_source.get("rcg-pending") is True
        latch_path = out / "gpucc-v1-latch-rcg-pending.migration"
        before, sent = take_snapshot(latch_source, latch_path, 0x47505532)
        latch_target = new("migration-latch-rcg-target", incoming=True)
        got = restore_snapshot(latch_target, latch_path, latch_source.last_clock, before, 0x47505532)
        completed("v1_captured_pll_latch_and_rcg_shadow_roundtrip", latch_path, sent, got)
        check("v1_independent_captured_requests_survive_migration")

        latch_target.monitor("cont")
        latch_target.step(599)
        assert not latch_target.read(MODE) & ACK
        assert latch_target.read(CMD) & BUSY
        latch_target.expect_hz("gpu_cc_pll1", PLL_HZ)
        gmu_rates(latch_target, XO, 0)
        latch_target.step(1)
        assert latch_target.read(MODE) & (UPDATE | ACK) == UPDATE | ACK
        assert latch_target.get("pll-active-l") == 31
        assert latch_target.get("pll-active-alpha") == 0x4000
        assert latch_target.get("rcg-active-cfg") == (1 << 20) | (7 << 8) | 1
        assert latch_target.read(L) == 40 and latch_target.read(ALPHA) == 0
        assert latch_target.read(CFG) == (1 << 20) | 3
        latch_target.expect_hz("gpu_cc_pll1", 0)
        gmu_rates(latch_target, 80_000_000, 0)
        latch_target.write(MODE, latch_target.read(MODE) & ~UPDATE)
        assert not latch_target.read(MODE) & ACK
        latch_target.step(4999)
        assert not latch_target.read(MODE) & LOCK
        latch_target.step(1)
        latch_target.expect_hz("gpu_cc_pll1", 600_000_000)
        gmu_configure(latch_target, 3)
        gmu_rates(latch_target, 600_000_000, 0)
        latch_target.fixture("core_bi_pll_test_se", 50_000_000)
        gmu_rates(latch_target, 600_000_000, 0)
        gmu_configure(latch_target, 7)
        gmu_rates(latch_target, 50_000_000, 0)
        latch_target.gpio("remote-vote", 3, False)
        gmu_rates(latch_target, 0, 0)
        check("v1_latch_rcg_deadlines_and_new_clock_values_resume_after_restore")

        reject("reference_to_default_profile_is_rejected", lock_path,
               source.last_clock, True, False, report["migrations"][0]["source"])
        disabled = new("migration-default-source", reference=False)
        disabled.fixture("gpu_cc_ahb_clk_parent", 30_000_000)
        disabled.gpio("remote-vote", 0, True)
        disabled.raw_write(0x80010000, 0x47505530)
        disabled.monitor("stop")
        assert disabled.monitor("query-status")["running"] is False
        disabled_path = out / "gpucc-v1-default.migration"
        disabled.monitor("migrate", file_channels(disabled_path))
        disabled_state = wait_migration(disabled)
        assert disabled_state["status"] == "completed"
        assert disabled.monitor("query-status")["status"] == "postmigrate"
        reject("default_to_reference_profile_is_rejected", disabled_path,
               disabled.last_clock, False, True, disabled_state)
    finally:
        for rig in reversed(rigs):
            rig.close()


EXPECTED_CHECKS = 30


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
    report = {"schema": "tb371fc-gpucc-sm8250-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [], "migrations": [],
        "rejected_migrations": [], "expected_completed_migrations": 2,
        "expected_rejected_migrations": 2, "vmstate_version": 1,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "register_count": 36, "sparse_window_count": 7, "branch_count": 15,
        "reset_count": 5, "input_clock_count": 15,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "gpu_execution_observed": False, "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP transactions, Clock providers, "
                 "and migration of explicit digital reference state",
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "source_sha256": {
            "gpucc-kona.c": "a9653a258c24397e366c04bb89c737d392f9cd198a2ba97c26c47a3250673c8a",
            "clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac"}}
    start = time.monotonic()
    try:
        assert len(ORDINARY_TESTS) == 21
        for test in ORDINARY_TESTS:
            rig = None
            try:
                rig = GpuRig(args, out / test.__name__)
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
        sparse_gpucc_windows_preserve_existing_cx_and_gx_owners(args, out)
        report["checks"].append({"name": "sparse_gpucc_windows_preserve_existing_cx_and_gx_owners", "passed": True})
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
