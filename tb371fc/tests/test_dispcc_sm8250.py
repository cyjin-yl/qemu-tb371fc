#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled Kona DISPCC sparse MMIO, clock protocols and actual migrations.

Literal inventories were independently read from the fixed OnePlusOSS source,
not loaded from the model's generated table. Timing is an explicit digital
reference ordering contract, not analog PLL, display or Android evidence.
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
from test_mdss_gdsc import mdss_payload
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-dispcc", "/machine/dispcc"
CC, RAM, MDSS = 0x0af00000, 0x80010000, "/machine/mdss-gdsc"
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
XO, PERIOD_UNIT = 19_200_000, 1_000_000_000 << 32
OUTCTRL, RESET_N, UPDATE, UPDATE_BYPASS = 1, 4, 1 << 22, 1 << 23
PCAL, ACK, LOCK, OFF = 1 << 27, 1 << 29, 1 << 31, 1 << 31
ROOT, BUSY, HW_CLK, DUAL = 2, 1, 1 << 20, 2 << 12
LOCK_NS, LATCH_NS, RCG_NS = 5000, 1000, 1000
PLLS = ((0x0000, "disp_cc_pll0", 0x47, 0xe000),
        (0x1000, "disp_cc_pll1", 0x1f, 0x4000))
PLL_OFFSETS = (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 56, 64)
INPUTS = ("bi_tcxo", "core_bi_pll_test_se", "chip_sleep_clk", "disp_cc_pll1_out_even",
          "dp_link_clk_divsel_ten", "dp_vco_divided_clk_src_mux",
          "dptx1_phy_pll_link_clk", "dptx1_phy_pll_vco_div_clk",
          "dptx2_phy_pll_link_clk", "dptx2_phy_pll_vco_div_clk",
          "dsi0_phy_pll_out_byteclk", "dsi1_phy_pll_out_byteclk",
          "dsi0_phy_pll_out_dsiclk", "dsi1_phy_pll_out_dsiclk",
          "edp_phy_pll_link_clk", "edp_phy_pll_vco_div_clk", "disp_cc_xo_clk_parent")
RESETS, MISC = (0x2000, 0x4000), 0x8000
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_SHA256 = "63eef01db44478e3be216025f672f73c652f9f3952f840bedf1791bc62d77061"
BINDING_SHA256 = "a624fe5555fd4eddc10a46a80ff3a52da66ddb56f81567c7ea4df591f5949ac2"

OUTPUTS = ('disp_cc_mdss_ahb_clk',
 'disp_cc_mdss_ahb_clk_src',
 'disp_cc_mdss_byte0_clk',
 'disp_cc_mdss_byte0_clk_src',
 'disp_cc_mdss_byte0_div_clk_src',
 'disp_cc_mdss_byte0_intf_clk',
 'disp_cc_mdss_byte1_clk',
 'disp_cc_mdss_byte1_clk_src',
 'disp_cc_mdss_byte1_div_clk_src',
 'disp_cc_mdss_byte1_intf_clk',
 'disp_cc_mdss_dp_aux1_clk',
 'disp_cc_mdss_dp_aux1_clk_src',
 'disp_cc_mdss_dp_aux_clk',
 'disp_cc_mdss_dp_aux_clk_src',
 'disp_cc_mdss_dp_link1_clk',
 'disp_cc_mdss_dp_link1_clk_src',
 'disp_cc_mdss_dp_link1_div_clk_src',
 'disp_cc_mdss_dp_link1_intf_clk',
 'disp_cc_mdss_dp_link_clk',
 'disp_cc_mdss_dp_link_clk_src',
 'disp_cc_mdss_dp_link_div_clk_src',
 'disp_cc_mdss_dp_link_intf_clk',
 'disp_cc_mdss_dp_pixel1_clk',
 'disp_cc_mdss_dp_pixel1_clk_src',
 'disp_cc_mdss_dp_pixel2_clk',
 'disp_cc_mdss_dp_pixel2_clk_src',
 'disp_cc_mdss_dp_pixel_clk',
 'disp_cc_mdss_dp_pixel_clk_src',
 'disp_cc_mdss_edp_aux_clk',
 'disp_cc_mdss_edp_aux_clk_src',
 'disp_cc_mdss_edp_gtc_clk',
 'disp_cc_mdss_edp_gtc_clk_src',
 'disp_cc_mdss_edp_link_clk',
 'disp_cc_mdss_edp_link_clk_src',
 'disp_cc_mdss_edp_link_div_clk_src',
 'disp_cc_mdss_edp_link_intf_clk',
 'disp_cc_mdss_edp_pixel_clk',
 'disp_cc_mdss_edp_pixel_clk_src',
 'disp_cc_mdss_esc0_clk',
 'disp_cc_mdss_esc0_clk_src',
 'disp_cc_mdss_esc1_clk',
 'disp_cc_mdss_esc1_clk_src',
 'disp_cc_mdss_mdp_clk',
 'disp_cc_mdss_mdp_clk_src',
 'disp_cc_mdss_mdp_lut_clk',
 'disp_cc_mdss_non_gdsc_ahb_clk',
 'disp_cc_mdss_pclk0_clk',
 'disp_cc_mdss_pclk0_clk_src',
 'disp_cc_mdss_pclk1_clk',
 'disp_cc_mdss_pclk1_clk_src',
 'disp_cc_mdss_rot_clk',
 'disp_cc_mdss_rot_clk_src',
 'disp_cc_mdss_rscc_ahb_clk',
 'disp_cc_mdss_rscc_vsync_clk',
 'disp_cc_mdss_vsync_clk',
 'disp_cc_mdss_vsync_clk_src',
 'disp_cc_pll0',
 'disp_cc_pll1',
 'disp_cc_sleep_clk',
 'disp_cc_sleep_clk_src',
 'disp_cc_xo_clk')

RCGS = ((8892,
  'disp_cc_mdss_ahb_clk_src',
  0,
  True,
  ((0, 'bi_tcxo'), (4, 'disp_cc_pll1'), (5, 'disp_cc_pll1_out_even'), (7, 'core_bi_pll_test_se'))),
 (8464,
  'disp_cc_mdss_byte0_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dsi0_phy_pll_out_byteclk'),
   (2, 'dsi1_phy_pll_out_byteclk'),
   (7, 'core_bi_pll_test_se'))),
 (8492,
  'disp_cc_mdss_byte1_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dsi0_phy_pll_out_byteclk'),
   (2, 'dsi1_phy_pll_out_byteclk'),
   (7, 'core_bi_pll_test_se'))),
 (8768, 'disp_cc_mdss_dp_aux1_clk_src', 0, False, ((0, 'bi_tcxo'), (7, 'core_bi_pll_test_se'))),
 (8668, 'disp_cc_mdss_dp_aux_clk_src', 0, False, ((0, 'bi_tcxo'), (7, 'core_bi_pll_test_se'))),
 (8716,
  'disp_cc_mdss_dp_link1_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dp_link_clk_divsel_ten'),
   (2, 'dp_vco_divided_clk_src_mux'),
   (3, 'dptx1_phy_pll_link_clk'),
   (4, 'dptx1_phy_pll_vco_div_clk'),
   (5, 'dptx2_phy_pll_link_clk'),
   (6, 'dptx2_phy_pll_vco_div_clk'),
   (7, 'core_bi_pll_test_se'))),
 (8568,
  'disp_cc_mdss_dp_link_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dp_link_clk_divsel_ten'),
   (2, 'dp_vco_divided_clk_src_mux'),
   (3, 'dptx1_phy_pll_link_clk'),
   (4, 'dptx1_phy_pll_vco_div_clk'),
   (5, 'dptx2_phy_pll_link_clk'),
   (6, 'dptx2_phy_pll_vco_div_clk'),
   (7, 'core_bi_pll_test_se'))),
 (8644,
  'disp_cc_mdss_dp_pixel1_clk_src',
  16,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dp_link_clk_divsel_ten'),
   (2, 'dp_vco_divided_clk_src_mux'),
   (3, 'dptx1_phy_pll_link_clk'),
   (4, 'dptx1_phy_pll_vco_div_clk'),
   (5, 'dptx2_phy_pll_link_clk'),
   (6, 'dptx2_phy_pll_vco_div_clk'),
   (7, 'core_bi_pll_test_se'))),
 (8692,
  'disp_cc_mdss_dp_pixel2_clk_src',
  16,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dp_link_clk_divsel_ten'),
   (2, 'dp_vco_divided_clk_src_mux'),
   (3, 'dptx1_phy_pll_link_clk'),
   (4, 'dptx1_phy_pll_vco_div_clk'),
   (5, 'dptx2_phy_pll_link_clk'),
   (6, 'dptx2_phy_pll_vco_div_clk'),
   (7, 'core_bi_pll_test_se'))),
 (8620,
  'disp_cc_mdss_dp_pixel_clk_src',
  16,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dp_link_clk_divsel_ten'),
   (2, 'dp_vco_divided_clk_src_mux'),
   (3, 'dptx1_phy_pll_link_clk'),
   (4, 'dptx1_phy_pll_vco_div_clk'),
   (5, 'dptx2_phy_pll_link_clk'),
   (6, 'dptx2_phy_pll_vco_div_clk'),
   (7, 'core_bi_pll_test_se'))),
 (8844, 'disp_cc_mdss_edp_aux_clk_src', 0, False, ((0, 'bi_tcxo'), (7, 'core_bi_pll_test_se'))),
 (8868,
  'disp_cc_mdss_edp_gtc_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'), (4, 'disp_cc_pll1'), (5, 'disp_cc_pll1_out_even'), (7, 'core_bi_pll_test_se'))),
 (8816,
  'disp_cc_mdss_edp_link_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'), (1, 'edp_phy_pll_link_clk'), (2, 'edp_phy_pll_vco_div_clk'), (7, 'core_bi_pll_test_se'))),
 (8792,
  'disp_cc_mdss_edp_pixel_clk_src',
  16,
  False,
  ((0, 'bi_tcxo'), (1, 'edp_phy_pll_link_clk'), (2, 'edp_phy_pll_vco_div_clk'), (7, 'core_bi_pll_test_se'))),
 (8520,
  'disp_cc_mdss_esc0_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dsi0_phy_pll_out_byteclk'),
   (2, 'dsi1_phy_pll_out_byteclk'),
   (7, 'core_bi_pll_test_se'))),
 (8544,
  'disp_cc_mdss_esc1_clk_src',
  0,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dsi0_phy_pll_out_byteclk'),
   (2, 'dsi1_phy_pll_out_byteclk'),
   (7, 'core_bi_pll_test_se'))),
 (8392,
  'disp_cc_mdss_mdp_clk_src',
  0,
  True,
  ((0, 'bi_tcxo'),
   (1, 'disp_cc_pll0'),
   (4, 'disp_cc_pll1'),
   (5, 'disp_cc_pll1_out_even'),
   (7, 'core_bi_pll_test_se'))),
 (8344,
  'disp_cc_mdss_pclk0_clk_src',
  8,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dsi0_phy_pll_out_dsiclk'),
   (2, 'dsi1_phy_pll_out_dsiclk'),
   (7, 'core_bi_pll_test_se'))),
 (8368,
  'disp_cc_mdss_pclk1_clk_src',
  8,
  False,
  ((0, 'bi_tcxo'),
   (1, 'dsi0_phy_pll_out_dsiclk'),
   (2, 'dsi1_phy_pll_out_dsiclk'),
   (7, 'core_bi_pll_test_se'))),
 (8416,
  'disp_cc_mdss_rot_clk_src',
  0,
  True,
  ((0, 'bi_tcxo'),
   (1, 'disp_cc_pll0'),
   (4, 'disp_cc_pll1'),
   (5, 'disp_cc_pll1_out_even'),
   (7, 'core_bi_pll_test_se'))),
 (8440, 'disp_cc_mdss_vsync_clk_src', 0, False, ((0, 'bi_tcxo'), (7, 'core_bi_pll_test_se'))),
 (24672, 'disp_cc_sleep_clk_src', 0, False, ((0, 'chip_sleep_clk'), (7, 'core_bi_pll_test_se'))))

BRANCHES = ((8320, 'disp_cc_mdss_ahb_clk', 'disp_cc_mdss_ahb_clk_src', False),
 (8232, 'disp_cc_mdss_byte0_clk', 'disp_cc_mdss_byte0_clk_src', False),
 (8236, 'disp_cc_mdss_byte0_intf_clk', 'disp_cc_mdss_byte0_div_clk_src', False),
 (8240, 'disp_cc_mdss_byte1_clk', 'disp_cc_mdss_byte1_clk_src', False),
 (8244, 'disp_cc_mdss_byte1_intf_clk', 'disp_cc_mdss_byte1_div_clk_src', False),
 (8296, 'disp_cc_mdss_dp_aux1_clk', 'disp_cc_mdss_dp_aux1_clk_src', False),
 (8276, 'disp_cc_mdss_dp_aux_clk', 'disp_cc_mdss_dp_aux_clk_src', False),
 (8284, 'disp_cc_mdss_dp_link1_clk', 'disp_cc_mdss_dp_link1_clk_src', False),
 (8288, 'disp_cc_mdss_dp_link1_intf_clk', 'disp_cc_mdss_dp_link1_div_clk_src', False),
 (8256, 'disp_cc_mdss_dp_link_clk', 'disp_cc_mdss_dp_link_clk_src', False),
 (8260, 'disp_cc_mdss_dp_link_intf_clk', 'disp_cc_mdss_dp_link_div_clk_src', False),
 (8272, 'disp_cc_mdss_dp_pixel1_clk', 'disp_cc_mdss_dp_pixel1_clk_src', False),
 (8280, 'disp_cc_mdss_dp_pixel2_clk', 'disp_cc_mdss_dp_pixel2_clk_src', False),
 (8268, 'disp_cc_mdss_dp_pixel_clk', 'disp_cc_mdss_dp_pixel_clk_src', False),
 (8312, 'disp_cc_mdss_edp_aux_clk', 'disp_cc_mdss_edp_aux_clk_src', False),
 (8316, 'disp_cc_mdss_edp_gtc_clk', 'disp_cc_mdss_edp_gtc_clk_src', False),
 (8304, 'disp_cc_mdss_edp_link_clk', 'disp_cc_mdss_edp_link_clk_src', False),
 (8308, 'disp_cc_mdss_edp_link_intf_clk', 'disp_cc_mdss_edp_link_div_clk_src', False),
 (8300, 'disp_cc_mdss_edp_pixel_clk', 'disp_cc_mdss_edp_pixel_clk_src', False),
 (8248, 'disp_cc_mdss_esc0_clk', 'disp_cc_mdss_esc0_clk_src', False),
 (8252, 'disp_cc_mdss_esc1_clk', 'disp_cc_mdss_esc1_clk_src', False),
 (8204, 'disp_cc_mdss_mdp_clk', 'disp_cc_mdss_mdp_clk_src', False),
 (8220, 'disp_cc_mdss_mdp_lut_clk', 'disp_cc_mdss_mdp_clk_src', True),
 (16388, 'disp_cc_mdss_non_gdsc_ahb_clk', 'disp_cc_mdss_ahb_clk_src', True),
 (8196, 'disp_cc_mdss_pclk0_clk', 'disp_cc_mdss_pclk0_clk_src', False),
 (8200, 'disp_cc_mdss_pclk1_clk', 'disp_cc_mdss_pclk1_clk_src', False),
 (8212, 'disp_cc_mdss_rot_clk', 'disp_cc_mdss_rot_clk_src', False),
 (16396, 'disp_cc_mdss_rscc_ahb_clk', 'disp_cc_mdss_ahb_clk_src', False),
 (16392, 'disp_cc_mdss_rscc_vsync_clk', 'disp_cc_mdss_vsync_clk_src', False),
 (8228, 'disp_cc_mdss_vsync_clk', 'disp_cc_mdss_vsync_clk_src', False),
 (24696, 'disp_cc_sleep_clk', 'disp_cc_sleep_clk_src', False),
 (24668, 'disp_cc_xo_clk', 'disp_cc_xo_clk_parent', False))

DIVIDERS = ((8488, 'disp_cc_mdss_byte0_div_clk_src', 'disp_cc_mdss_byte0_clk_src', 2, True),
 (8516, 'disp_cc_mdss_byte1_div_clk_src', 'disp_cc_mdss_byte1_clk_src', 2, True),
 (8740, 'disp_cc_mdss_dp_link1_div_clk_src', 'disp_cc_mdss_dp_link1_clk_src', 2, False),
 (8592, 'disp_cc_mdss_dp_link_div_clk_src', 'disp_cc_mdss_dp_link_clk_src', 2, False),
 (8840, 'disp_cc_mdss_edp_link_div_clk_src', 'disp_cc_mdss_edp_link_clk_src', 2, False))

REGISTERS = tuple(sorted(set(
    [base + offset for base, *_ in PLLS for offset in PLL_OFFSETS] +
    [base + offset for base, _name, width, _hw, _parents in RCGS
     for offset in ((0, 4, 8, 12, 16) if width else (0, 4))] +
    [offset for offset, *_ in BRANCHES] + [offset for offset, *_ in DIVIDERS] +
    list(RESETS) + [MISC])))
WINDOWS = []
for offset in REGISTERS:
    if WINDOWS and offset == WINDOWS[-1][0] + WINDOWS[-1][1]:
        WINDOWS[-1] = (WINDOWS[-1][0], WINDOWS[-1][1] + 4)
    else:
        WINDOWS.append((offset, 4))
WINDOWS = tuple(WINDOWS)
RCG_INDEX = {row[1]: i for i, row in enumerate(RCGS)}
MND = tuple(i for i, row in enumerate(RCGS) if row[2])
HW_RCG = tuple(i for i, row in enumerate(RCGS) if row[3])
VOTED = tuple(i for i, row in enumerate(BRANCHES) if row[3])
READ_ONLY_DIVIDERS = tuple(i for i, row in enumerate(DIVIDERS) if not row[4])
assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(RCGS), len(BRANCHES),
        len(DIVIDERS), len(MND), len(HW_RCG), len(VOTED)) == (130, 39, 61, 22, 32, 5, 6, 3, 2)

IDENTITY, VMSTATE_BYTES, EXPECTED_CHECKS = 0x44495331, 1676, 41
GLOBAL_FIELDS = ("reference-profile", "reference-identity", "register-count", "clock-count",
                 "input-count", "pll-count", "rcg-count", "branch-count", "divider-count",
                 "reset-count", "remote-vote-count", "window-count", "mapped-bytes", "physical-base")
PLL_FIELDS = ("active-l", "active-alpha", "pending-l", "pending-alpha", "locked", "ack",
              "calibrated", "lock-pending", "latch-pending", "lock-deadline", "latch-deadline")
RCG_FIELDS = tuple(stage + "-" + field for stage in ("active", "pending")
                   for field in ("cfg", "m", "n", "d")) + ("pending", "deadline")


class DispRig(CameraRig):
    """Existing socket/RPMh transport, with entirely independent DISPCC accessors."""
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", rpmh=False, mdss=False, legacy=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.camera_mmio_operations = self.dispcc_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="dispcc-"))
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
                   "-global", f"qcom-mdss-gdsc.enabled-reference={'on' if mdss else 'off'}"]
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
            if CC <= int(fields[1], 0) < CC + 0x20000:
                self.dispcc_mmio_operations += 1
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
        assert index in (0, 1)
        self.command(f"set_irq_in {DEVICE} remote-vote {index} {int(bool(value))}")

    def pll(self, index, field):
        return self.qom(f"pll{index}-{field}")

    def rcg(self, index, field):
        return self.qom(f"rcg{index}-{field}")

    def pure_snapshot(self, *, include_clock_periods=True):
        snapshot = {"globals": {n: self.qom(n) for n in GLOBAL_FIELDS},
                    "registers": {f"{o:04x}": self.reg(o) for o in REGISTERS},
                    "plls": [{n: self.pll(i, n) for n in PLL_FIELDS} for i in range(2)],
                    "rcgs": [{n: self.rcg(i, n) for n in RCG_FIELDS} for i in range(22)],
                    "last-xo-hz": self.qom("last-xo-hz"),
                    "dividers": [self.qom(f"divider{i}-value") for i in range(5)],
                    "remote-votes": [self.qom(f"remote-vote{i}") for i in range(2)],
                    "output_hz": {n: self.hz(n) for n in OUTPUTS}}
        if include_clock_periods:
            snapshot["outputs"] = {n: self.period(n) for n in OUTPUTS}
            snapshot["inputs"] = {n: self.period(n) for n in INPUTS}
        return snapshot

    def mdss_snapshot(self):
        return {"enabled-reference": self.qom("enabled-reference", MDSS),
                "globals": {n: self.qom(n, MDSS) for n in GDSC_GLOBAL_FIELDS},
                "parent-known": self.qom("parent-known", MDSS),
                "parent-on": self.qom("parent-on", MDSS),
                "domains": [{n: self.qom("domain0." + n, MDSS) for n in GDSC_DOMAIN_FIELDS}]}

    def reject_write(self, offset, value):
        before = self.reg(offset)
        count = self.log_path.read_text(errors="replace").count(TYPE + ":")
        self.write(offset, value)
        assert self.reg(offset) == before, (hex(offset), hex(value))
        assert self.log_path.read_text(errors="replace").count(TYPE + ":") == count + 1

    def reset_lines(self, expected, require_seen=False):
        self.raw_read(RAM)
        assert [self.irq.get(i, False) for i in range(2)] == list(expected)
        if require_seen:
            assert {0, 1} <= self.seen_irq

    def reset_device(self):
        self.events.clear()
        self.monitor("system_reset")
        self.wait_for(lambda: all(self.reg(p[0] + 4) == 0 for p in PLLS), "DISPCC reset witnesses")
        self.monitor("query-status")
        assert any(e.get("event") == "RESET" for e in self.events)


def pll_rate(index, l=None, alpha=None):
    row = PLLS[index]
    return XO * (row[2] if l is None else l) + ((XO * (row[3] if alpha is None else alpha)) >> 16)


def configure_pll(r, index):
    base, _name, l, alpha = PLLS[index]
    mode, opmode = r.read(base), r.read(base + 56)
    if mode & OUTCTRL and opmode & 1:
        return
    # Literal nonzero writes. USER=0, USER_U1=0 and TEST=0 are conditional
    # omissions in the fixed helper, not requests to clear old storage.
    for offset, value in ((4, l), (8, 0x44), (64, alpha), (24, 0x20485699),
                          (28, 0x2261), (32, 0x029a699c), (16, 0x805)):
        r.write(base + offset, value)
    r.write(base, r.read(base) | UPDATE_BYPASS)
    r.write(base, r.read(base) & ~OUTCTRL)
    r.write(base + 56, 0)
    r.write(base, r.read(base) | RESET_N)


def configure_all(r):
    configure_pll(r, 0)
    configure_pll(r, 1)
    r.write(MISC, r.read(MISC) | 0x10)


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
    return (HW_CLK if RCGS[index][3] else 0) | selector << 8 | hid | mode << 12


def configure_rcg(r, index, selector=0, hid=1, *, m=0, n=0, wait=True, root=True):
    offset, _name, width, _hw, _parents = RCGS[index]
    if n:
        mask = (1 << width) - 1
        assert width and 0 < m <= n <= mask
        for delta, value in ((8, m), (12, ~(n-m) & mask), (16, ~n & mask)):
            r.write(offset + delta, value)
    r.write(offset + 4, rcg_cfg(index, selector, hid, 2 if n and m != n else 0))
    r.write(offset, (ROOT if root else 0) | BUSY)
    if wait:
        r.step(RCG_NS)
        assert not r.read(offset) & BUSY


def divided(hz, hid, m=0, n=0):
    hz = hz * 2 // (hid + 1) if hid else hz
    return hz * m // n if n else hz


def source_rate(name):
    if name == "bi_tcxo":
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
    for i in range(22):
        configure_rcg(r, i)


def cold_reference_has_exact_inventory_and_unknown_external_carriers(r):
    values = (1, IDENTITY, 130, 61, 17, 2, 22, 32, 5, 2, 2, 39, 520, CC)
    assert {n: r.qom(n) for n in GLOBAL_FIELDS} == dict(zip(GLOBAL_FIELDS, values))
    status_words = {row[0] for row in RCGS} | {row[0] for row in BRANCHES}
    for offset in REGISTERS:
        assert r.reg(offset) == 0
        assert r.read(offset) == (OFF if offset in status_words else 0), hex(offset)
    for name in OUTPUTS:
        r.expect_hz(name, 0)
    assert {n: r.period(n) for n in INPUTS} == {n: PERIOD_UNIT // XO if i == 0 else 0
                                             for i, n in enumerate(INPUTS)}
    before = r.pure_snapshot()
    r.step(100_000)
    assert r.pure_snapshot() == before


def literal_probe_configures_two_lucid_plls_and_misc_without_clearing_zeros(r):
    configure_all(r)
    for i, (base, _name, l, alpha) in enumerate(PLLS):
        expected = {0: RESET_N | UPDATE_BYPASS, 4: l, 8: 0x44, 12: 0, 16: 0x805,
                    20: 0, 24: 0x20485699, 28: 0x2261, 32: 0x029a699c,
                    36: 0, 40: 0, 44: 0, 56: 0, 64: alpha}
        assert {o: r.read(base + o) for o in PLL_OFFSETS} == expected
        assert not r.pll(i, "lock-pending") and not r.pll(i, "locked")
        r.write(base + 12, 7)
        r.write(base + 20, 0x1234)
        r.write(base + 36, 0x5678)
        configure_pll(r, i)
        assert (r.reg(base + 12), r.reg(base + 20), r.reg(base + 36)) == (7, 0x1234, 0x5678)
    assert r.reg(MISC) == 0x10
    assert all(r.hz(n) == 0 for n in OUTPUTS)


def pll_banks_and_undefined_even_output_are_independent(r):
    configure_all(r)
    enable_all(r)
    assert pll_rate(0) == 1_380_000_000 and pll_rate(1) == 600_000_000
    for i, (base, *_rest) in enumerate(PLLS):
        r.write(base + 4, 0x32 + i)
        r.write(base + 64, 0x1000 + i)
    for i, (base, name, *_rest) in enumerate(PLLS):
        assert r.reg(base + 4) == 0x32 + i and r.reg(base + 64) == 0x1000 + i
        r.expect_hz(name, pll_rate(i))  # Shadow writes alone do not retune.
        r.reject_write(base + 12, 0x107)
    index = RCG_INDEX["disp_cc_mdss_ahb_clk_src"]
    configure_rcg(r, index, 5)
    expect_root(r, index, 0)
    assert r.period("disp_cc_pll1_out_even") == 0
    r.fixture("disp_cc_pll1_out_even", 333_333_337)
    expect_root(r, index, 333_333_337)
    r.expect_hz("disp_cc_pll1", 600_000_000)
    r.fixture("disp_cc_pll1_out_even", 0)
    expect_root(r, index, 0)


def independent_lock_deadlines_require_user_and_outctrl_gates(r):
    configure_all(r)
    r.write(56, 1)
    r.step(137)
    r.write(0x1038, 1)
    r.step(4862)
    assert not r.pll(0, "locked") and not r.pll(1, "locked")
    r.step(1)
    assert r.pll(0, "locked") and not r.pll(1, "locked")
    r.step(136)
    assert not r.pll(1, "locked")
    r.step(1)
    for i, (base, name, *_rest) in enumerate(PLLS):
        assert r.pll(i, "locked") and r.pll(i, "calibrated")
        r.expect_hz(name, 0)
        r.write(base + 12, 7)
        r.expect_hz(name, 0)
        r.write(base, r.read(base) | OUTCTRL)
        r.expect_hz(name, pll_rate(i))
        r.write(base + 12, 0)
        r.expect_hz(name, 0)
        r.write(base + 12, 7)
        r.reject_write(base + 12, 1)
        r.write(base, r.read(base) & ~OUTCTRL)
        r.expect_hz(name, 0)
        assert r.pll(i, "locked")


def pll_recipe_and_quantized_vco_limits_never_invent_lock(r):
    configure_all(r)
    enable_all(r)
    for i, (base, name, *_rest) in enumerate(PLLS):
        for offset, bad in ((8, 0x43), (16, 0x804), (32, 0x329a699c), (36, 1)):
            good = r.reg(base + offset)
            r.write(base + offset, bad)
            r.step(10_000)
            assert not r.pll(i, "locked")
            r.expect_hz(name, 0)
            r.expect_hz(PLLS[1-i][1], pll_rate(1-i))
            r.write(base + offset, good)
            r.step(LOCK_NS)
            r.expect_hz(name, pll_rate(i))
    for i in range(2):
        for l, alpha, valid in ((13, 0, True), (104, 10923, True),
                                (12, 0, False), (104, 10924, False)):
            r.reset_device()
            configure_pll(r, i)
            base = PLLS[i][0]
            r.write(base + 4, l)
            r.write(base + 64, alpha)
            r.write(base + 56, 1)
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


def all_22_rcgs_capture_cfg_once_at_the_original_deadline(r):
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, wait=False)
        assert r.rcg(i, "deadline") == r.last_clock + RCG_NS
        r.write(offset + 4, rcg_cfg(i, 0, 3))
        r.write(offset, ROOT | BUSY)
        assert r.rcg(i, "pending-cfg") == rcg_cfg(i)
    r.step(999)
    assert all(r.rcg(i, "active-cfg") == 0 for i in range(22))
    r.step(1)
    for i, (_offset, _name, _width, _hw, parents) in enumerate(RCGS):
        assert r.rcg(i, "active-cfg") == rcg_cfg(i)
        assert not r.rcg(i, "pending")
        expect_root(r, i, source_rate(dict(parents)[0]))
    # Dropping UPDATE cancels a captured request, retaining the old active tuple.
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, 0, 7, wait=False)
        r.write(offset, ROOT)
    r.step(RCG_NS)
    assert all(r.rcg(i, "active-cfg") == rcg_cfg(i) for i in range(22))


def rcg_control_masks_and_eight_vs_sixteen_bit_storage_are_distinct(r):
    for i, (offset, _name, width, hw, _parents) in enumerate(RCGS):
        if hw:
            r.write(offset + 4, rcg_cfg(i))
            assert r.reg(offset + 4) & HW_CLK
        else:
            r.reject_write(offset + 4, rcg_cfg(i) | HW_CLK)
        if width:
            mask = (1 << width) - 1
            for delta in (8, 12, 16):
                r.write(offset + delta, mask)
                assert r.reg(offset + delta) == mask
                r.reject_write(offset + delta, 1 << width)
            if width == 16:
                r.write(offset + 8, 0x1234)
                assert r.reg(offset + 8) == 0x1234


def six_mnd_requests_capture_all_raw_fields_without_shadow_recapture(r):
    for i in MND:
        offset, _name, width, *_rest = RCGS[i]
        m, n = (257, 1009) if width == 16 else (5, 7)
        mask = (1 << width) - 1
        configure_rcg(r, i, 0, 3, m=m, n=n, wait=False)
        for delta, value in ((8, 1), (12, mask-5), (16, mask-6)):
            r.write(offset + delta, value)
        r.write(offset + 4, rcg_cfg(i, 0, 1, 2))
        r.write(offset, ROOT | BUSY)
        assert tuple(r.rcg(i, "pending-"+f) for f in ("m", "n", "d")) == (m, ~(n-m)&mask, ~n&mask)
    r.step(999)
    assert all(r.rcg(i, "active-m") == 0 for i in MND)
    r.step(1)
    for i in MND:
        width = RCGS[i][2]
        m, n = (257, 1009) if width == 16 else (5, 7)
        assert r.rcg(i, "active-cfg") == rcg_cfg(i, 0, 3, 2)
        expect_root(r, i, divided(XO, 3, m, n))


def integer_mode_bypasses_stale_mnd_and_literal_pixel_one_to_one_encoding(r):
    for i in MND:
        offset, _name, width, *_rest = RCGS[i]
        mask = (1 << width) - 1
        configure_rcg(r, i, 0, 3, m=3, n=8)
        previous = tuple(r.reg(offset+d) for d in (8, 12, 16))
        configure_rcg(r, i, 0, 1)
        assert tuple(r.rcg(i, "active-"+f) for f in ("m", "n", "d")) == previous
        expect_root(r, i, XO)
        configure_rcg(r, i, 0, 1, m=1, n=1)
        assert tuple(r.reg(offset+d) for d in (8, 12, 16)) == (1, mask, mask-1)
        assert not r.rcg(i, "active-cfg") & (3 << 12)
        expect_root(r, i, XO)


def invalid_mux_modes_and_bounded_fraction_tuples_never_ack(r):
    for i, (offset, _name, width, _hw, parents) in enumerate(RCGS):
        invalid = [rcg_cfg(i, 0, 1, mode) for mode in (1, 3)]
        missing = set(range(8)) - set(dict(parents))
        if missing:
            invalid.append(rcg_cfg(i, min(missing)))
        if not width:
            invalid.append(rcg_cfg(i, 0, 1, 2))
        for cfg in invalid:
            r.write(offset, 0)
            r.write(offset + 4, cfg)
            r.write(offset, ROOT | BUSY)
            r.step(2000)
            assert r.read(offset) & BUSY and not r.rcg(i, "pending")
            assert r.rcg(i, "active-cfg") == 0
        if width:
            mask = (1 << width) - 1
            for raw in ((0, mask-5, mask-6), (1, mask, mask-1),
                        (2, 0, 0), (1, mask-5, mask-7)):
                r.write(offset, 0)
                for delta, value in zip((8, 12, 16), raw):
                    r.write(offset + delta, value)
                r.write(offset + 4, rcg_cfg(i, 0, 1, 2))
                r.write(offset, ROOT | BUSY)
                r.step(RCG_NS)
                assert r.read(offset) & BUSY and not r.rcg(i, "pending")
                assert r.rcg(i, "active-m") == 0


def literal_ahb_mdp_and_rot_table_vectors_use_hid_floor(r):
    configure_all(r)
    enable_all(r)
    index = RCG_INDEX["disp_cc_mdss_ahb_clk_src"]
    for hid, hz in ((31, 37_500_000), (15, 75_000_000)):
        configure_rcg(r, index, 4, hid)
        expect_root(r, index, hz)
    for name in ("disp_cc_mdss_mdp_clk_src", "disp_cc_mdss_rot_clk_src"):
        index = RCG_INDEX[name]
        for selector, hid, hz in ((4, 5, 200_000_000), (4, 3, 300_000_000),
                                  (1, 7, 345_000_000), (1, 5, 460_000_000)):
            configure_rcg(r, index, selector, hid)
            expect_root(r, index, hz)
    configure_rcg(r, RCG_INDEX["disp_cc_mdss_mdp_clk_src"], 4, 13)
    expect_root(r, RCG_INDEX["disp_cc_mdss_mdp_clk_src"], 85_714_285)


def byte2_hid_round_up_selection_uses_only_the_explicit_phy_carrier(r):
    for n in (0, 1):
        index = RCG_INDEX[f"disp_cc_mdss_byte{n}_clk_src"]
        configure_rcg(r, index, n+1)
        expect_root(r, index, 0)
        parent = 300_000_001
        r.fixture(f"dsi{n}_phy_pll_out_byteclk", parent)
        for requested in (120_000_000, 75_000_000, 1_000_000):
            hid = min((2 * parent + requested - 1) // requested - 1, 31)
            configure_rcg(r, index, n+1, hid)
            expect_root(r, index, divided(parent, hid))
        for hid in (0, 1, 2, 3, 14, 31):
            configure_rcg(r, index, n+1, hid)
            expect_root(r, index, divided(parent, hid))


def pixel_eight_bit_source_fractions_include_bypass_one_to_one(r):
    for n in (0, 1):
        parent = 480_000_003
        r.fixture(f"dsi{n}_phy_pll_out_dsiclk", parent)
        index = RCG_INDEX[f"disp_cc_mdss_pclk{n}_clk_src"]
        for m, d in ((3, 8), (2, 9), (4, 9), (1, 1)):
            configure_rcg(r, index, n+1, 1, m=m, n=d)
            expect_root(r, index, parent * m // d)
            assert (r.rcg(index, "active-cfg") >> 12) & 3 == (0 if m == d else 2)


def four_dp_roots_preserve_fraction_bits_above_eight(r):
    for i in (j for j in MND if RCGS[j][2] == 16):
        parent = dict(RCGS[i][4])[2]
        r.fixture(parent, 123_456_789)
        for m, n in ((257, 1009), (32767, 65535)):
            configure_rcg(r, i, 2, 3, m=m, n=n)
            assert r.rcg(i, "active-m") == m
            expect_root(r, i, divided(123_456_789, 3, m, n))
    return {"dp_link_table_values_rescaled_without_evidence": False}


def every_declared_mux_routes_its_named_parent(r):
    configure_all(r)
    enable_all(r)
    for i, (_offset, _name, _width, _hw, parents) in enumerate(RCGS):
        for selector, parent in parents:
            configure_rcg(r, i, selector)
            expect_root(r, i, source_rate(parent))


def sixteen_unknown_inputs_fan_out_only_after_explicit_fixture(r):
    for ordinal, name in enumerate(INPUTS[1:], 1):
        assert r.period(name) == 0
        users = [(i, selector) for i, row in enumerate(RCGS)
                 for selector, parent in row[4] if parent == name]
        for i, selector in users:
            configure_rcg(r, i, selector)
            expect_root(r, i, 0)
        if name == "disp_cc_xo_clk_parent":
            r.write(0x605c, 1)
            r.expect_hz("disp_cc_xo_clk", 0)
        hz = 111_111_111 + ordinal * 10003
        r.fixture(name, hz)
        for i, _selector in users:
            expect_root(r, i, hz)
        if name == "disp_cc_xo_clk_parent":
            r.expect_hz("disp_cc_xo_clk", hz)
        r.fixture(name, 0)
        for i, _selector in users:
            expect_root(r, i, 0)
        if name == "disp_cc_xo_clk_parent":
            r.expect_hz("disp_cc_xo_clk", 0)


def two_writable_dividers_round_up_and_propagate_interface_demand(r):
    r.fixture("core_bi_pll_test_se", 840_001)
    for i in (0, 1):
        offset, name, parent, _width, writable = DIVIDERS[i]
        assert writable
        configure_rcg(r, RCG_INDEX[parent], 7, root=False)
        branch = next(row for row in BRANCHES if row[2] == name)
        r.write(branch[0], 1)
        for value in range(4):
            r.write(offset, value)
            assert r.qom(f"divider{i}-value") == value
            expected = (840_001 + value) // (value + 1)
            r.expect_hz(name, expected)
            r.expect_hz(branch[1], expected)
            r.expect_hz(parent, 840_001)
            r.reject_write(offset, value | 4)
        r.write(branch[0], 0)
        r.expect_hz(parent, 0)


def three_readonly_dividers_require_fixtures_and_reject_even_zero_writes(r):
    r.fixture("core_bi_pll_test_se", 840_001)
    for i in READ_ONLY_DIVIDERS:
        offset, name, parent, *_rest = DIVIDERS[i]
        configure_rcg(r, RCG_INDEX[parent], 7, root=False)
        branch = next(row for row in BRANCHES if row[2] == name)
        r.write(branch[0], 1)
        for value in range(4):
            r.divider(i, value)
            assert r.read(offset) == r.qom(f"divider{i}-value") == value
            r.expect_hz(name, (840_001 + value) // (value + 1))
            r.expect_hz(branch[1], (840_001 + value) // (value + 1))
            r.reject_write(offset, 0)
            r.reject_write(offset, value)
        r.write(branch[0], 0)
        r.expect_hz(parent, 0)


def all_32_branches_keep_independent_local_requests(r):
    r.fixture("chip_sleep_clk", 32000)
    r.fixture("disp_cc_xo_clk_parent", 7_000_003)
    for i in range(22):
        configure_rcg(r, i, root=False)
    for i, (offset, name, parent, _voted) in enumerate(BRANCHES):
        r.expect_hz(name, 0)
        r.write(offset, 1)
        hz = 32000 if parent == "disp_cc_sleep_clk_src" else (
             7_000_003 if parent == "disp_cc_xo_clk_parent" else XO)
        r.expect_hz(name, hz)
        assert r.read(offset) == 1
        r.expect_hz(BRANCHES[(i+1) % 32][1], 0)
        r.reject_write(offset, 3)
        r.write(offset, 0)
        assert r.read(offset) == OFF


def two_voted_branches_or_local_requests_but_cannot_supply_missing_parents(r):
    for vote_index, branch_index in enumerate(VOTED):
        offset, name, parent, _voted = BRANCHES[branch_index]
        index = RCG_INDEX[parent]
        configure_rcg(r, index, root=False)
        for local, remote in ((0, False), (1, False), (1, True), (0, True), (0, False)):
            r.write(offset, local)
            r.vote(vote_index, remote)
            assert r.qom(f"remote-vote{vote_index}") is remote
            r.expect_hz(name, XO if local or remote else 0)
            assert r.reg(offset) == local
        configure_rcg(r, index, 5, root=False)
        r.vote(vote_index, True)
        r.write(offset, 1)
        r.expect_hz(name, 0)
        r.fixture("disp_cc_pll1_out_even", 96_000_001)
        r.expect_hz(name, 96_000_001)
        r.fixture("disp_cc_pll1_out_even", 0)
        r.expect_hz(name, 0)
        r.vote(vote_index, False)
        r.write(offset, 0)


def remote_requests_do_not_become_local_or_other_branch_votes(r):
    for i in range(22):
        configure_rcg(r, i, root=False)
    for value in (True, False):
        for vote in range(2):
            r.vote(vote, value)
        for index, (offset, name, _parent, voted) in enumerate(BRANCHES):
            assert r.reg(offset) == 0
            r.expect_hz(name, XO if voted and value else 0)
    names = {p["name"] for p in r.monitor("qom-list", {"path": DEVICE})}
    assert {n for n in names if re.fullmatch(r"remote-vote\d+", n)} == {"remote-vote0", "remote-vote1"}


def misc_bit4_is_retained_control_without_synthetic_clock_or_power_effects(r):
    run_defaults(r)
    before, mdss = r.pure_snapshot(), r.mdss_snapshot()
    for value in (0, 0x10, 0):
        r.write(MISC, value)
        expected = copy.deepcopy(before)
        expected["registers"]["8000"] = value
        assert r.pure_snapshot() == expected
        assert r.mdss_snapshot() == mdss
    for value in (1, 0x11, 0x20, 0x80000010):
        r.reject_write(MISC, value)


def two_reset_lines_follow_bindings_without_resetting_mdss_or_plls(r):
    configure_all(r)
    enable_all(r)
    mdss = r.mdss_snapshot()
    for i, offset in enumerate(RESETS):
        r.write(offset, 1)
        r.reset_lines([j == i for j in range(2)])
        assert r.mdss_snapshot() == mdss
        for pll, row in enumerate(PLLS):
            r.expect_hz(row[1], pll_rate(pll))
        r.write(offset, 0)
    r.reset_lines([False, False], True)


def read_only_status_cannot_forge_or_erase_actual_feedback(r):
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


def reserved_bits_narrow_accesses_and_unowned_holes_reject_atomically(r):
    for offset, value in ((4, 0x10000), (8, 0x10000), (64, 0x10000), (56, 2),
                          (0x209c, 1 << 21), (0x2000, 2), (0x2004, 2), (MISC, 1)):
        r.reject_write(offset, value)
    for op in ("read", "write"):
        for width in ("b", "w"):
            count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
            if op == "read":
                r.read_absolute(CC, width)
            else:
                r.write_absolute(CC, 0xffffffff, width)
            new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
            assert len(new) == 1 and new[0][0] == op and "invalid size" in new[0][-1]
        before = (r.reg(0), r.reg(4))
        count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
        if op == "read":
            r.read_absolute(CC+1)
        else:
            r.write_absolute(CC+1, 0xffffffff)
        new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
        assert new and all(item[0] == op and "invalid size" in item[-1] for item in new)
        assert (r.reg(0), r.reg(4)) == before
    holes = (0x30, 0x34, 0x44, 0x1030, 0x2010, 0x2018, 0x2088, 0x2100,
             0x2118, 0x3004, 0x6000, 0x8004, 0x10000, 0x1fffc)
    assert not set(holes) & set(REGISTERS)
    count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    expected = []
    for offset in holes:
        r.read(offset)
        r.write(offset, 0xffffffff)
        expected.extend((op, CC+offset, 4, "(null)", "rejected") for op in ("read", "write"))
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[count:] == expected


def system_reset_cancels_work_and_retains_external_inputs_and_remote_votes(r):
    configure_all(r)
    enable_all(r)
    for i, name in enumerate(INPUTS[1:], 1):
        r.fixture(name, 20_000_000 + i)
    for base, _name, l, _alpha in PLLS:
        r.write(base+4, l+1)
        r.write(base, r.read(base) | UPDATE)
    for i in range(22):
        configure_rcg(r, i, wait=False)
    for offset in RESETS:
        r.write(offset, 1)
    for i in READ_ONLY_DIVIDERS:
        r.divider(i, 3)
    r.vote(0, True)
    r.vote(1, True)
    inputs = {n: r.period(n) for n in INPUTS}
    r.reset_device()
    assert all(r.reg(o) == 0 for o in REGISTERS)
    for i in range(2):
        assert not any(r.pll(i, f) for f in ("locked", "ack", "calibrated"))
        assert r.pll(i, "lock-deadline") == r.pll(i, "latch-deadline") == -1
    assert all(not r.rcg(i, "pending") and r.rcg(i, "active-cfg") == 0 for i in range(22))
    assert {n: r.period(n) for n in INPUTS} == inputs
    assert [r.qom(f"remote-vote{i}") for i in range(2)] == [True, True]
    r.reset_lines([False, False])
    r.step(20_000)
    for index in VOTED:
        r.expect_hz(BRANCHES[index][1], XO)
    r.vote(0, False)
    r.vote(1, False)
    for name in OUTPUTS:
        r.expect_hz(name, 0)


def pure_observers_fixture_types_and_quantized_input_limits_are_protected(r):
    before = r.pure_snapshot()
    for prop, value in [(n, r.qom(n)) for n in GLOBAL_FIELDS] + [
            ("reg.8000", 0), ("pll0-active-l", 0), ("rcg7-active-m", 0),
            ("divider0-value", 0), ("remote-vote0", False)]:
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": prop, "value": value})
        assert "error" in response and "not writable" in response["error"]["desc"]
    for name in INPUTS:
        maximum = 5_000_000_000 if name in INPUTS[10:14] else (1 << 32) - 1
        for value in (-1, maximum + 1, "19200000", True):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": value})
            assert "error" in response
    response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-bi_tcxo-hz", "value": 1})
    assert "error" in response and "connected" in response["error"]["desc"]
    for name in INPUTS[1:]:
        if name in INPUTS[10:14]:
            # This isolated profile leaves the DSI provider disabled. Only its
            # four documented parents may express the v4.1 5-GHz VCO range.
            for value in (1 << 32, 4_500_000_000, 5_000_000_000):
                r.fixture(name, value)
                expected_period = (1_000_000_000 << 32) // value
                assert r.period(name) == expected_period
                assert r.qom("qtest-"+name+"-hz") == (1_000_000_000 << 32) // expected_period
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": 5_000_000_001})
            assert "error" in response and r.period(name) == 858_993_459
            r.fixture(name, 0)
            continue
        for value in range(4_294_967_292, 4_294_967_296):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-"+name+"-hz", "value": value})
            assert "error" in response and r.period(name) == 0
        r.fixture(name, 4_294_967_291)
        assert r.period(name) == 1_000_000_001
        assert r.qom("qtest-"+name+"-hz") == 4_294_967_291
        r.fixture(name, 0)
    for i in range(5):
        for value in (-1, 4, "1", True):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": f"qtest-divider{i}-value", "value": value})
            assert "error" in response
        if i < 2:
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": f"qtest-divider{i}-value", "value": 0})
            assert "error" in response and "not found" in response["error"]["desc"]
    assert r.pure_snapshot() == before


def real_rpmh_xo_votes_change_only_the_connected_carrier(r):
    assert r.has_rpmh and r.period("bi_tcxo") == 0
    configure_all(r)
    r.step(20_000)
    assert not any(r.pll(i, "locked") for i in range(2))
    r.run_one("xo.lvl", 1)
    assert r.period("bi_tcxo") == PERIOD_UNIT // XO
    enable_all(r)
    r.run_one("xo.lvl", 0)
    assert r.period("bi_tcxo") == 0
    assert not any(r.pll(i, "locked") for i in range(2))
    r.run_one("xo.lvl", 1)
    for base, *_rest in PLLS:
        r.write(base+56, 0)
        r.write(base+56, 1)
    r.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        r.expect_hz(row[1], pll_rate(i))
    assert all(r.period(n) == 0 for n in INPUTS[1:])
    r.write(0x605c, 1)
    r.expect_hz("disp_cc_xo_clk", 0)
    return {"real_xo_resource": "xo.lvl", "critical_xo_parent_invented": False}


ORDINARY = [(f, {}) for f in (
    cold_reference_has_exact_inventory_and_unknown_external_carriers,
    literal_probe_configures_two_lucid_plls_and_misc_without_clearing_zeros,
    pll_banks_and_undefined_even_output_are_independent,
    independent_lock_deadlines_require_user_and_outctrl_gates,
    pll_recipe_and_quantized_vco_limits_never_invent_lock,
    two_lucid_updates_capture_once_then_ack_and_relock,
    update_cancel_standby_and_reset_keep_the_other_pll_independent,
    clock_jump_finishes_both_latches_and_relocks_without_mmio_repair,
    all_22_rcgs_capture_cfg_once_at_the_original_deadline,
    rcg_control_masks_and_eight_vs_sixteen_bit_storage_are_distinct,
    six_mnd_requests_capture_all_raw_fields_without_shadow_recapture,
    integer_mode_bypasses_stale_mnd_and_literal_pixel_one_to_one_encoding,
    invalid_mux_modes_and_bounded_fraction_tuples_never_ack,
    literal_ahb_mdp_and_rot_table_vectors_use_hid_floor,
    byte2_hid_round_up_selection_uses_only_the_explicit_phy_carrier,
    pixel_eight_bit_source_fractions_include_bypass_one_to_one,
    four_dp_roots_preserve_fraction_bits_above_eight,
    every_declared_mux_routes_its_named_parent,
    sixteen_unknown_inputs_fan_out_only_after_explicit_fixture,
    two_writable_dividers_round_up_and_propagate_interface_demand,
    three_readonly_dividers_require_fixtures_and_reject_even_zero_writes,
    all_32_branches_keep_independent_local_requests,
    two_voted_branches_or_local_requests_but_cannot_supply_missing_parents,
    remote_requests_do_not_become_local_or_other_branch_votes,
    misc_bit4_is_retained_control_without_synthetic_clock_or_power_effects,
    two_reset_lines_follow_bindings_without_resetting_mdss_or_plls,
    read_only_status_cannot_forge_or_erase_actual_feedback,
    reserved_bits_narrow_accesses_and_unowned_holes_reject_atomically,
    system_reset_cancels_work_and_retains_external_inputs_and_remote_votes,
    pure_observers_fixture_types_and_quantized_input_limits_are_protected,
)] + [(real_rpmh_xo_votes_change_only_the_connected_carrier, {"rpmh": True})]


def default_profile_rejects_every_word_despite_other_enabled_profiles(args, out):
    for legacy in (False, True):
        rig = DispRig(args, out / ("default-legacy" if legacy else "default-alone"),
                      reference=None, mdss=legacy, legacy=legacy)
        try:
            assert rig.qom("enabled-reference") is False
            before = rig.pure_snapshot()
            count = rig.log_path.read_text(errors="replace").count(TYPE+":")
            for offset in REGISTERS:
                rig.read(offset)
                rig.write(offset, 1)
            assert rig.log_path.read_text(errors="replace").count(TYPE+":") == count + 260
            assert rig.pure_snapshot() == before
            for name in INPUTS[1:]:
                rig.fixture(name, 96_000_001)
            rig.vote(0, True)
            rig.vote(1, True)
            rig.step(20_000)
            assert all(rig.reg(o) == 0 for o in REGISTERS)
            assert not any(rig.pll(i, "locked") for i in range(2))
            for name in OUTPUTS:
                rig.expect_hz(name, 0)
        finally:
            rig.close()


def paused_tcg_exposes_only_pure_model_observers(args, out):
    rig = DispRig(args, out / "paused-tcg", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        before = rig.pure_snapshot(include_clock_periods=False)
        names = {p["name"] for p in rig.monitor("qom-list", {"path": DEVICE})}
        assert not any(n.startswith("qtest-") for n in names)
        for name in ["qtest-"+n+"-hz" for n in INPUTS] + [f"qtest-divider{i}-value" for i in range(5)]:
            response = rig.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": 1})
            assert "error" in response and "not found" in response["error"]["desc"]
        assert rig.pure_snapshot(include_clock_periods=False) == before
        assert rig.cont_commands == rig.device_mmio_operations == 0
        assert rig.monitor("query-status")["running"] is False
    finally:
        rig.close()


def sparse_39_leaves_preserve_simultaneously_enabled_mdss_owner(args, out):
    rig = DispRig(args, out / "sparse-and-mdss-owners", mdss=True)
    try:
        tree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory / "mtree.txt").write_text(tree)
        matches = re.findall(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ([^\s]+)", tree)
        actual = {(int(a, 16)-CC, int(b, 16)-int(a, 16)+1)
                  for a, b, name in matches if name.startswith(TYPE+".")}
        assert actual == set(WINDOWS) and sum(n for _, n in actual) == 520
        mdss_ranges = {(int(a, 16), int(b, 16)) for a, b, name in matches
                       if name == "qcom-mdss-gdsc.mdss-core-control"}
        assert mdss_ranges == {(CC+0x3000, CC+0x3003)}
        before = rig.pure_snapshot()
        assert rig.read(0x3000) == 1
        rig.write(0x3000, 0x5801)
        assert rig.read(0x3000) == 0x5801
        mdss = rig.mdss_snapshot()
        assert mdss["domains"][0]["control"] == 0x5801
        assert rig.pure_snapshot() == before
        configure_all(rig)
        enable_all(rig)
        assert rig.mdss_snapshot() == mdss
        assert rig.qom("reference-identity", MDSS) == 0x4d445331
    finally:
        rig.close()


def dispcc_payload(snapshot):
    """Full independent v1 big-endian wire, with no output Clock state."""
    raw = bytearray(struct.pack(">13IQ", *(snapshot["globals"][n] for n in GLOBAL_FIELDS)))
    raw.extend(struct.pack(">130I", *(snapshot["registers"][f"{o:04x}"] for o in REGISTERS)))
    for field in ("active-l", "active-alpha", "pending-l", "pending-alpha"):
        raw.extend(struct.pack(">2I", *(p[field] for p in snapshot["plls"])))
    for field in (stage+"-"+word for stage in ("active", "pending") for word in ("cfg", "m", "n", "d")):
        raw.extend(struct.pack(">22I", *(r[field] for r in snapshot["rcgs"])))
    for field in ("lock-deadline", "latch-deadline"):
        raw.extend(struct.pack(">2q", *(p[field] for p in snapshot["plls"])))
    raw.extend(struct.pack(">22qQ", *(r["deadline"] for r in snapshot["rcgs"]), snapshot["last-xo-hz"]))
    for field in ("locked", "ack", "calibrated"):
        assert all(type(p[field]) is bool for p in snapshot["plls"])
        raw.extend(bytes(p[field] for p in snapshot["plls"]))
    assert all(type(v) is bool for v in snapshot["remote-votes"])
    raw.extend(bytes(snapshot["remote-votes"]))
    raw.extend(struct.pack(">17Q", *(snapshot["inputs"][name] for name in INPUTS)))
    assert len(raw) == VMSTATE_BYTES
    return bytes(raw)


def inspect_stream(path, snapshot, mdss):
    data, sections = path.read_bytes(), {}
    for label, name, expected in (("dispcc", TYPE, dispcc_payload(snapshot)),
                                  ("mdss", "qcom-mdss-gdsc", mdss_payload(mdss))):
        section = unique_section(data, name, 1, len(expected))
        offset = section["payload_offset"]
        raw = data[offset:offset+len(expected)]
        assert raw == expected, (label, [i for i, (a,b) in enumerate(zip(raw,expected)) if a != b][:32])
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
    snapshot, mdss = rig.pure_snapshot(), rig.mdss_snapshot()
    write_json(rig.directory/"source-before-save.json", {"dispcc": snapshot, "mdss": mdss})
    rig.monitor("migrate", file_channels(path))
    sent = wait_migration(rig)
    assert sent["status"] == "completed"
    assert rig.monitor("query-status")["status"] == "postmigrate"
    assert rig.pure_snapshot() == snapshot and rig.mdss_snapshot() == mdss
    sections = inspect_stream(path, snapshot, mdss)
    write_json(rig.directory/"source-query-migrate.json", sent)
    return {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
            "source_directory": rig.directory.name, "source_virtual_ns": rig.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "mdss": mdss, "sections": sections,
            "ram_witness": witness, "stream_modified": False}


def prewarm_target(rig, saved):
    # Use the same virtual epoch. QTest cannot rewind a clock, and an
    # incoming-defer VM does not dispatch its stopped virtual timer queue.
    at_clock = saved["source_virtual_ns"]
    assert at_clock >= 27_000
    rig.set_clock(at_clock-27_000)
    configure_all(rig)
    for base, *_rest in PLLS:
        rig.write(base+56, 1)
    rig.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        rig.read(row[0])  # Preparatory MMIO is allowed before incoming.
        assert rig.pll(i, "locked")
        output_on(rig, i)
    for i in range(22):
        configure_rcg(rig, i)
    assert rig.last_clock == at_clock
    for i, name in enumerate(INPUTS[1:], 1):
        rig.fixture(name, 70_000_000+i)
    for i, (offset, *_rest) in enumerate(DIVIDERS):
        if i in READ_ONLY_DIVIDERS:
            rig.divider(i, 3)
        else:
            rig.write(offset, 3)
    for offset, *_rest in BRANCHES:
        rig.write(offset, 1)
    for i, previous in enumerate(saved["snapshot"]["remote-votes"]):
        rig.vote(i, not previous)
    for offset in RESETS:
        rig.write(offset, 1)
    for base, _name, l, _alpha in PLLS:
        rig.write(base+4, l+3)
        rig.write(base, rig.read(base) | UPDATE)
    for i in range(22):
        configure_rcg(rig, i, 7, 7, wait=False)
    rig.write(MISC, 0)
    rig.write(0x3000, 0x7801)
    assert rig.pure_snapshot() != saved["snapshot"]


def receive_source(rig, path, saved):
    rig.set_clock(saved["source_virtual_ns"])
    mmio, cont = rig.device_mmio_operations, rig.cont_commands
    rig.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(rig)
    assert received["status"] == "completed"
    assert rig.monitor("query-status")["running"] is False
    first, mdss = rig.pure_snapshot(), rig.mdss_snapshot()
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert mdss == saved["mdss"]
    assert (rig.device_mmio_operations, rig.cont_commands) == (mmio, cont)
    assert rig.raw_read(RAM) == saved["ram_witness"]
    rig.reset_lines([bool(first["registers"][f"{o:04x}"]) for o in RESETS])
    evidence = {"snapshot": first, "mdss": mdss, "device_mmio_since_incoming": False,
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


def fraction_for(index):
    width = RCGS[index][2]
    return (257, 1009) if width == 16 else (5, 7) if width == 8 else (0, 0)


def migration_checks(args, out, report):
    rigs = []

    def new(name, **options):
        rig = DispRig(args, out/name, mdss=True, **options)
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
            "all_1676_payload_bytes_verified": True, "mdss_64_bytes_verified": True, **details})
        passed(name, device_mmio_before_first_observation=False, **details)

    try:
        steady = new("migration-steady-source")
        run_defaults(steady)
        for i, name in enumerate(INPUTS[1:], 1):
            steady.fixture(name, 111_111_113 if i == 1 else 30_000_000+i)
        for i in range(22):
            m, n = fraction_for(i)
            configure_rcg(steady, i, 7, 3+i%5, m=m, n=n)
        for i, (offset, *_rest) in enumerate(BRANCHES):
            steady.write(offset, i & 1)
        for i, (offset, *_rest) in enumerate(DIVIDERS):
            if i in READ_ONLY_DIVIDERS:
                steady.divider(i, i % 4)
            else:
                steady.write(offset, i+1)
        steady.vote(0, True)
        steady.write(RESETS[1], 1)
        steady.write(0x3000, 0x5801)
        state_path = out/"dispcc-v1-steady.migration"
        saved_state = save_source(steady, state_path, 0x44495331)
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
            m, n = fraction_for(i)
            restored.expect_hz(row[1], divided(99_000_001, 3+i%5, m, n))
        restored.vote(0, False)
        restored.expect_hz(BRANCHES[VOTED[0]][1], 0)
        assert restored.mdss_snapshot() == saved_state["mdss"]
        completed("v1_steady_clocks_dividers_votes_and_mdss_replace_stale_target",
                  state_path, steady, restored, first, stale_target_deadlines_discarded=True,
                  clock_and_vote_changes_propagate_after_restore=True)

        locking = new("migration-two-locks-22-rcgs-source")
        locking.step(100_000)
        configure_all(locking)
        locking.fixture("core_bi_pll_test_se", 96_000_001)
        for i, row in enumerate(PLLS):
            output_on(locking, i)
            locking.write(row[0]+56, 1)
        start = locking.last_clock
        for i in range(22):
            m, n = fraction_for(i)
            configure_rcg(locking, i, 7, 3, m=m, n=n, wait=False)
        locking.vote(0, True)
        locking.write(RESETS[0], 1)
        locking.step(400)
        assert all(locking.pll(i, "lock-deadline") == start+5000 for i in range(2))
        assert all(locking.rcg(i, "deadline") == start+1000 for i in range(22))
        lock_path = out/"dispcc-v1-two-locks-22-rcgs.migration"
        saved_lock = save_source(locking, lock_path, 0x44495332)
        report["migration_sources"].append(saved_lock)
        resumed = new("migration-two-locks-22-rcgs-target", incoming=True)
        prewarm_target(resumed, saved_lock)
        first = receive_source(resumed, lock_path, saved_lock)
        mmio = resumed.device_mmio_operations
        resumed.monitor("cont")
        resumed.step(599)
        assert all(resumed.rcg(i, "pending") for i in range(22))
        assert not any(resumed.pll(i, "locked") for i in range(2))
        resumed.step(1)
        for i, row in enumerate(RCGS):
            m, n = fraction_for(i)
            assert not resumed.rcg(i, "pending")
            resumed.expect_hz(row[1], divided(96_000_001, 3, m, n))
        resumed.step(3999)
        assert not any(resumed.pll(i, "locked") for i in range(2))
        resumed.step(1)
        for i, row in enumerate(PLLS):
            assert resumed.pll(i, "locked")
            resumed.expect_hz(row[1], pll_rate(i))
        assert resumed.device_mmio_operations == mmio
        assert resumed.mdss_snapshot() == saved_lock["mdss"]
        completed("v1_two_locks_and_22_rcg_deadlines_resume_without_mmio_repair",
                  lock_path, locking, resumed, first, remaining_rcg_ns=600,
                  remaining_pll_ns=4600, device_mmio_before_deadline_checks=False)

        latching = new("migration-two-latches-six-mnd-source")
        run_defaults(latching)
        latching.fixture("core_bi_pll_test_se", 123_456_789)
        ahb = RCG_INDEX["disp_cc_mdss_ahb_clk_src"]
        configure_rcg(latching, ahb, 5)
        latching.vote(1, True)
        latching.write(0x3000, 0x2801)
        captured = ((0x48, 0x2345), (0x20, 0x6789))
        for i, (base, *_rest) in enumerate(PLLS):
            latching.write(base+4, captured[i][0])
            latching.write(base+64, captured[i][1])
            latching.write(base, latching.read(base) | UPDATE)
            latching.write(base+4, 0x30+i)
            latching.write(base+64, 0xabcd)
            latching.write(base, latching.read(base) | UPDATE)
        start = latching.last_clock
        for i in MND:
            offset, _name, width, *_rest = RCGS[i]
            m, n = fraction_for(i)
            mask = (1 << width)-1
            configure_rcg(latching, i, 7, 14, m=m, n=n, wait=False)
            for delta, value in ((8, 1), (12, mask-5), (16, mask-6)):
                latching.write(offset+delta, value)
            latching.write(offset+4, rcg_cfg(i, 0, 1, 2))
            latching.write(offset, ROOT | BUSY)
        latching.step(400)
        latch_path = out/"dispcc-v1-two-latches-six-mnd.migration"
        saved_latch = save_source(latching, latch_path, 0x44495333)
        report["migration_sources"].append(saved_latch)
        target = new("migration-two-latches-six-mnd-target", incoming=True)
        prewarm_target(target, saved_latch)
        first = receive_source(target, latch_path, saved_latch)
        assert target.period("disp_cc_pll1_out_even") == 0
        target.expect_hz(BRANCHES[VOTED[1]][1], 0)
        mmio = target.device_mmio_operations
        target.monitor("cont")
        target.step(599)
        assert all(target.pll(i, "latch-pending") for i in range(2))
        assert all(target.rcg(i, "pending") for i in MND)
        for i, row in enumerate(PLLS):
            target.expect_hz(row[1], pll_rate(i))
        target.step(1)
        for i, row in enumerate(PLLS):
            assert target.pll(i, "ack") and not target.pll(i, "locked")
            assert (target.pll(i, "active-l"), target.pll(i, "active-alpha")) == captured[i]
            assert target.pll(i, "lock-deadline") == start+6000
            target.expect_hz(row[1], 0)
        for i in MND:
            m, n = fraction_for(i)
            mask = (1 << RCGS[i][2])-1
            assert tuple(target.rcg(i, "active-"+f) for f in ("m", "n", "d")) == (m, ~(n-m)&mask, ~n&mask)
            assert target.rcg(i, "active-cfg") == rcg_cfg(i, 7, 14, 2)
            assert target.reg(RCGS[i][0]+4) == rcg_cfg(i, 0, 1, 2)
            target.expect_hz(RCGS[i][1], divided(123_456_789, 14, m, n))
        target.step(4999)
        assert not any(target.pll(i, "locked") for i in range(2))
        target.step(1)
        for i, row in enumerate(PLLS):
            target.expect_hz(row[1], pll_rate(i, *captured[i]))
        assert target.device_mmio_operations == mmio
        assert target.mdss_snapshot() == saved_latch["mdss"]
        # An unknown source replaces the target's nonzero input. Only a new
        # explicit carrier can satisfy its restored remote branch request.
        target.expect_hz(RCGS[ahb][1], 0)
        target.fixture("disp_cc_pll1_out_even", 77_000_003)
        target.expect_hz(BRANCHES[VOTED[1]][1], 77_000_003)
        completed("v1_two_pll_latches_and_mixed_width_mnd_keep_captured_state",
                  latch_path, latching, target, first, remaining_latch_ns=600,
                  device_mmio_before_deadline_checks=False,
                  unknown_source_input_replaces_prewarmed_carrier=True)

        disabled = new("migration-default-source", reference=None)
        disabled.fixture("chip_sleep_clk", 32000)
        disabled.vote(1, True)
        disabled_path = out/"dispcc-v1-default.migration"
        saved_disabled = save_source(disabled, disabled_path, 0x44495330)
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
        section = saved_state["sections"]["dispcc"]
        misc_index = REGISTERS.index(MISC)
        for name, relative, width, old, xor, diagnostic in (
                ("physical_base", 52, 8, CC, 4, f"{CC:x} != {CC ^ 4:x}"),
                ("misc_reserved_control", 60+4*misc_index, 4, 0x10, 1,
                 TYPE+f": invalid migrated register {misc_index}")):
            absolute = section["payload_offset"]+relative
            assert int.from_bytes(original[absolute:absolute+width], "big") == old
            mutated = bytearray(original)
            mutated[absolute:absolute+width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, (a,b) in enumerate(zip(original,mutated)) if a != b]
            assert differences == [absolute+width-1]
            assert (original[differences[0]] ^ mutated[differences[0]]).bit_count() == 1
            path = out/f"dispcc-corrupt-{name}.migration"
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
        "schema": "tb371fc-dispcc-sm8250-v1", "suite": "dispcc-sm8250-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "expected_unmodified_streams": 4,
        "expected_stream_files": 6, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "physical_base": CC, "resource_bytes": 131072, "register_count": 130,
        "sparse_window_count": 39, "mapped_bytes": 520,
        "output_clock_count": 61, "input_clock_count": 17,
        "connected_input_clock_count": 1, "unknown_external_input_count": 16,
        "pll_count": 2, "lucid_pll_count": 2, "postdivider_count": 0,
        "rcg_count": 22, "mnd_rcg_count": 6, "mnd_width8_count": 2,
        "mnd_width16_count": 4, "hw_control_rcg_count": 3, "branch_count": 32,
        "divider_count": 5, "writable_divider_count": 2, "read_only_divider_count": 3,
        "reset_count": 2, "remote_vote_count": 2, "misc_word_count": 1,
        "modeled_timer_count": 1, "mdss_vmstate_version": 1,
        "mdss_vmstate_payload_bytes": 64, "reference_enabled_by_default": False,
        "pll1_even_alias_assumed": False, "critical_xo_parent_assumed": False,
        "generic_dividers_round_up": True,
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "source_sha256": {
            "dispcc-kona.c": SOURCE_SHA256, "qcom,dispcc-kona.h": BINDING_SHA256,
            "clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
            "clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
            "clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
            "clk-branch.h": "da132bd1a093a650abf64073a4e695719e0612b67bb0166726737e0f2a3332d5",
            "clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
            "clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
            "clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2"},
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "display_execution_observed": False, "analog_pll_behavior_modeled": False,
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
                len(RCGS), len(MND), len(BRANCHES), len(DIVIDERS), len(RESETS)) == (
                    130, 39, 61, 17, 2, 22, 6, 32, 5, 2)
        for index, (test, options) in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = DispRig(args, fixture, **options)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_profile_rejects_every_word_despite_other_enabled_profiles,
                     paused_tcg_exposes_only_pure_model_observers,
                     sparse_39_leaves_preserve_simultaneously_enabled_mdss_owner):
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
                item["continued_after_restore"] and item["all_1676_payload_bytes_verified"] and
                item["mdss_64_bytes_verified"] for item in report["migrations"]) and
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
