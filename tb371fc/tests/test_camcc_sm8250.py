#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled Kona-v2 CAMCC protocol, clock propagation and actual migration.

Literal tables below were independently read from OnePlusOSS kernel commit
94304e12a3509eac7b3716bf574ce0b1fcaacbae, camcc-kona.c and its binding.
Lucid and Zonda, including their different fractions and handshakes, remain
separate. Exact virtual deadlines are digital reference ordering assumptions,
not measurements of analog lock or evidence of camera/Android execution.
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

from test_camera_gdsc import CameraRig, core_diagnostics, unique_section, camera_payload, write_json
from test_camera_gdsc import GLOBAL_FIELDS as GDSC_GLOBAL_FIELDS, DOMAIN_FIELDS as GDSC_DOMAIN_FIELDS
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-camcc", "/machine/camcc"
CC, RAM, CAMERA = 0x0ad00000, 0x80010000, "/machine/camera-gdsc"
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
XO, PERIOD_UNIT = 19_200_000, 1_000_000_000 << 32
OUTCTRL, BYPASS, RESET_N, UPDATE, UPDATE_BYPASS = 1, 2, 4, 1 << 22, 1 << 23
PCAL, ACK, FREQ_LOCK, LOCK, OFF = 1 << 27, 1 << 29, 1 << 29, 1 << 31, 1 << 31
ROOT, BUSY, HW_CLK, DUAL = 2, 1, 1 << 20, 2 << 12
LOCK_NS, LATCH_NS, ZONDA_SETTLE_NS, RCG_NS = 5000, 1000, 5000, 1000
IDENTITY, VMSTATE_BYTES, EXPECTED_CHECKS = 0x43414d32, 2579, 41
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "core_bi_pll_test_se", "chip_sleep_clk", "cam_cc_pll2_out_aux2")
PLLS = (
    (0x0000, "cam_cc_pll0", "lucid", 0x3e, 0x8000, 0x3100),
    (0x1000, "cam_cc_pll1", "lucid", 0x1f, 0x4000, 0x100),
    (0x2000, "cam_cc_pll2", "zonda", 0x4b, 0, 0x100),
    (0x3000, "cam_cc_pll3", "lucid", 0x24, 0x7555, 0x100),
    (0x4000, "cam_cc_pll4", "lucid", 0x24, 0x7555, 0x100),
)
LUCID = (0, 1, 3, 4)
LUCID_OFFSETS = (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 56, 64)
ZONDA_OFFSETS = tuple(range(0, 0x2c, 4))
RESETS = (0x7000, 0xc070, 0xa000, 0xb000, 0x8000, 0x9000)
GDSC_OFFSETS = (0x7004, 0x8004, 0x9004, 0xa004, 0xb004, 0xc144)
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_SHA256 = '60fa0934e7ec2ad37ade0b710430d137bf1f46008e7115c2d77569f34edd50f9'
BINDING_SHA256 = '3ea08de9ae18dd90e151407309017b3347950835883a79b0a80179fce23212a1'

OUTPUTS = ('cam_cc_bps_ahb_clk',
 'cam_cc_bps_areg_clk',
 'cam_cc_bps_axi_clk',
 'cam_cc_bps_clk',
 'cam_cc_bps_clk_src',
 'cam_cc_camnoc_axi_clk',
 'cam_cc_camnoc_axi_clk_src',
 'cam_cc_camnoc_dcd_xo_clk',
 'cam_cc_cci_0_clk',
 'cam_cc_cci_0_clk_src',
 'cam_cc_cci_1_clk',
 'cam_cc_cci_1_clk_src',
 'cam_cc_core_ahb_clk',
 'cam_cc_cpas_ahb_clk',
 'cam_cc_cphy_rx_clk_src',
 'cam_cc_csi0phytimer_clk',
 'cam_cc_csi0phytimer_clk_src',
 'cam_cc_csi1phytimer_clk',
 'cam_cc_csi1phytimer_clk_src',
 'cam_cc_csi2phytimer_clk',
 'cam_cc_csi2phytimer_clk_src',
 'cam_cc_csi3phytimer_clk',
 'cam_cc_csi3phytimer_clk_src',
 'cam_cc_csi4phytimer_clk',
 'cam_cc_csi4phytimer_clk_src',
 'cam_cc_csi5phytimer_clk',
 'cam_cc_csi5phytimer_clk_src',
 'cam_cc_csiphy0_clk',
 'cam_cc_csiphy1_clk',
 'cam_cc_csiphy2_clk',
 'cam_cc_csiphy3_clk',
 'cam_cc_csiphy4_clk',
 'cam_cc_csiphy5_clk',
 'cam_cc_fast_ahb_clk_src',
 'cam_cc_fd_core_clk',
 'cam_cc_fd_core_clk_src',
 'cam_cc_fd_core_uar_clk',
 'cam_cc_gdsc_clk',
 'cam_cc_icp_ahb_clk',
 'cam_cc_icp_clk',
 'cam_cc_icp_clk_src',
 'cam_cc_ife_0_ahb_clk',
 'cam_cc_ife_0_areg_clk',
 'cam_cc_ife_0_axi_clk',
 'cam_cc_ife_0_clk',
 'cam_cc_ife_0_clk_src',
 'cam_cc_ife_0_cphy_rx_clk',
 'cam_cc_ife_0_csid_clk',
 'cam_cc_ife_0_csid_clk_src',
 'cam_cc_ife_0_dsp_clk',
 'cam_cc_ife_1_ahb_clk',
 'cam_cc_ife_1_areg_clk',
 'cam_cc_ife_1_axi_clk',
 'cam_cc_ife_1_clk',
 'cam_cc_ife_1_clk_src',
 'cam_cc_ife_1_cphy_rx_clk',
 'cam_cc_ife_1_csid_clk',
 'cam_cc_ife_1_csid_clk_src',
 'cam_cc_ife_1_dsp_clk',
 'cam_cc_ife_lite_ahb_clk',
 'cam_cc_ife_lite_axi_clk',
 'cam_cc_ife_lite_clk',
 'cam_cc_ife_lite_clk_src',
 'cam_cc_ife_lite_cphy_rx_clk',
 'cam_cc_ife_lite_csid_clk',
 'cam_cc_ife_lite_csid_clk_src',
 'cam_cc_ipe_0_ahb_clk',
 'cam_cc_ipe_0_areg_clk',
 'cam_cc_ipe_0_axi_clk',
 'cam_cc_ipe_0_clk',
 'cam_cc_ipe_0_clk_src',
 'cam_cc_jpeg_clk',
 'cam_cc_jpeg_clk_src',
 'cam_cc_mclk0_clk',
 'cam_cc_mclk0_clk_src',
 'cam_cc_mclk1_clk',
 'cam_cc_mclk1_clk_src',
 'cam_cc_mclk2_clk',
 'cam_cc_mclk2_clk_src',
 'cam_cc_mclk3_clk',
 'cam_cc_mclk3_clk_src',
 'cam_cc_mclk4_clk',
 'cam_cc_mclk4_clk_src',
 'cam_cc_mclk5_clk',
 'cam_cc_mclk5_clk_src',
 'cam_cc_mclk6_clk',
 'cam_cc_mclk6_clk_src',
 'cam_cc_pll0',
 'cam_cc_pll0_out_even',
 'cam_cc_pll0_out_odd',
 'cam_cc_pll1',
 'cam_cc_pll1_out_even',
 'cam_cc_pll2',
 'cam_cc_pll2_out_main',
 'cam_cc_pll3',
 'cam_cc_pll3_out_even',
 'cam_cc_pll4',
 'cam_cc_pll4_out_even',
 'cam_cc_sbi_ahb_clk',
 'cam_cc_sbi_axi_clk',
 'cam_cc_sbi_clk',
 'cam_cc_sbi_cphy_rx_clk',
 'cam_cc_sbi_csid_clk',
 'cam_cc_sbi_csid_clk_src',
 'cam_cc_sbi_div_clk_src',
 'cam_cc_sbi_ife_0_clk',
 'cam_cc_sbi_ife_1_clk',
 'cam_cc_sleep_clk',
 'cam_cc_sleep_clk_src',
 'cam_cc_slow_ahb_clk_src',
 'cam_cc_xo_clk_src')

RCGS = ((28688,
  'cam_cc_bps_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49400,
  'cam_cc_camnoc_axi_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49340,
  'cam_cc_cci_0_clk_src',
  8,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49368,
  'cam_cc_cci_1_clk_src',
  8,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (41064,
  'cam_cc_cphy_rx_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (24576,
  'cam_cc_csi0phytimer_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (24608,
  'cam_cc_csi1phytimer_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (24640,
  'cam_cc_csi2phytimer_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (24672,
  'cam_cc_csi3phytimer_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (24704,
  'cam_cc_csi4phytimer_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (24736,
  'cam_cc_csi5phytimer_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (28732,
  'cam_cc_fast_ahb_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49304,
  'cam_cc_fd_core_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49268,
  'cam_cc_icp_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (40976,
  'cam_cc_ife_0_clk_src',
  0,
  ((0, 'bi_tcxo'), (6, 'cam_cc_pll3_out_even'), (7, 'core_bi_pll_test_se'))),
 (41024,
  'cam_cc_ife_0_csid_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (45072,
  'cam_cc_ife_1_clk_src',
  0,
  ((0, 'bi_tcxo'), (6, 'cam_cc_pll4_out_even'), (7, 'core_bi_pll_test_se'))),
 (45120,
  'cam_cc_ife_1_csid_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49152,
  'cam_cc_ife_lite_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49180,
  'cam_cc_ife_lite_csid_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (32784,
  'cam_cc_ipe_0_clk_src',
  0,
  ((0, 'bi_tcxo'), (4, 'cam_cc_pll1_out_even'), (7, 'core_bi_pll_test_se'))),
 (49224,
  'cam_cc_jpeg_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (20480,
  'cam_cc_mclk0_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (20508,
  'cam_cc_mclk1_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (20536,
  'cam_cc_mclk2_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (20564,
  'cam_cc_mclk3_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (20592,
  'cam_cc_mclk4_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (20620,
  'cam_cc_mclk5_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (20648,
  'cam_cc_mclk6_clk_src',
  8,
  ((0, 'bi_tcxo'), (3, 'cam_cc_pll2_out_aux2'), (5, 'cam_cc_pll2'), (7, 'core_bi_pll_test_se'))),
 (36892,
  'cam_cc_sbi_csid_clk_src',
  0,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49520, 'cam_cc_sleep_clk_src', 0, ((0, 'chip_sleep_clk'), (7, 'core_bi_pll_test_se'))),
 (28760,
  'cam_cc_slow_ahb_clk_src',
  8,
  ((0, 'bi_tcxo'),
   (1, 'cam_cc_pll0'),
   (2, 'cam_cc_pll0_out_even'),
   (3, 'cam_cc_pll0_out_odd'),
   (5, 'cam_cc_pll2_out_main'),
   (7, 'core_bi_pll_test_se'))),
 (49492, 'cam_cc_xo_clk_src', 0, ((0, 'bi_tcxo_ao'), (7, 'core_bi_pll_test_se'))))

BRANCHES = ((28784, 'cam_cc_bps_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (28756, 'cam_cc_bps_areg_clk', 'cam_cc_fast_ahb_clk_src'),
 (28728, 'cam_cc_bps_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (28712, 'cam_cc_bps_clk', 'cam_cc_bps_clk_src'),
 (49428, 'cam_cc_camnoc_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (49436, 'cam_cc_camnoc_dcd_xo_clk', 'cam_cc_xo_clk_src'),
 (49364, 'cam_cc_cci_0_clk', 'cam_cc_cci_0_clk_src'),
 (49392, 'cam_cc_cci_1_clk', 'cam_cc_cci_1_clk_src'),
 (49488, 'cam_cc_core_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (49396, 'cam_cc_cpas_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (24600, 'cam_cc_csi0phytimer_clk', 'cam_cc_csi0phytimer_clk_src'),
 (24632, 'cam_cc_csi1phytimer_clk', 'cam_cc_csi1phytimer_clk_src'),
 (24664, 'cam_cc_csi2phytimer_clk', 'cam_cc_csi2phytimer_clk_src'),
 (24696, 'cam_cc_csi3phytimer_clk', 'cam_cc_csi3phytimer_clk_src'),
 (24728, 'cam_cc_csi4phytimer_clk', 'cam_cc_csi4phytimer_clk_src'),
 (24760, 'cam_cc_csi5phytimer_clk', 'cam_cc_csi5phytimer_clk_src'),
 (24604, 'cam_cc_csiphy0_clk', 'cam_cc_cphy_rx_clk_src'),
 (24636, 'cam_cc_csiphy1_clk', 'cam_cc_cphy_rx_clk_src'),
 (24668, 'cam_cc_csiphy2_clk', 'cam_cc_cphy_rx_clk_src'),
 (24700, 'cam_cc_csiphy3_clk', 'cam_cc_cphy_rx_clk_src'),
 (24732, 'cam_cc_csiphy4_clk', 'cam_cc_cphy_rx_clk_src'),
 (24764, 'cam_cc_csiphy5_clk', 'cam_cc_cphy_rx_clk_src'),
 (49328, 'cam_cc_fd_core_clk', 'cam_cc_fd_core_clk_src'),
 (49336, 'cam_cc_fd_core_uar_clk', 'cam_cc_fd_core_clk_src'),
 (49516, 'cam_cc_gdsc_clk', 'cam_cc_xo_clk_src'),
 (49300, 'cam_cc_icp_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (49292, 'cam_cc_icp_clk', 'cam_cc_icp_clk_src'),
 (41096, 'cam_cc_ife_0_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (41008, 'cam_cc_ife_0_areg_clk', 'cam_cc_fast_ahb_clk_src'),
 (41092, 'cam_cc_ife_0_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (41000, 'cam_cc_ife_0_clk', 'cam_cc_ife_0_clk_src'),
 (41088, 'cam_cc_ife_0_cphy_rx_clk', 'cam_cc_cphy_rx_clk_src'),
 (41048, 'cam_cc_ife_0_csid_clk', 'cam_cc_ife_0_csid_clk_src'),
 (41020, 'cam_cc_ife_0_dsp_clk', 'cam_cc_ife_0_clk_src'),
 (45160, 'cam_cc_ife_1_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (45104, 'cam_cc_ife_1_areg_clk', 'cam_cc_fast_ahb_clk_src'),
 (45156, 'cam_cc_ife_1_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (45096, 'cam_cc_ife_1_clk', 'cam_cc_ife_1_clk_src'),
 (45152, 'cam_cc_ife_1_cphy_rx_clk', 'cam_cc_cphy_rx_clk_src'),
 (45144, 'cam_cc_ife_1_csid_clk', 'cam_cc_ife_1_csid_clk_src'),
 (45116, 'cam_cc_ife_1_dsp_clk', 'cam_cc_ife_1_clk_src'),
 (49216, 'cam_cc_ife_lite_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (49220, 'cam_cc_ife_lite_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (49176, 'cam_cc_ife_lite_clk', 'cam_cc_ife_lite_clk_src'),
 (49212, 'cam_cc_ife_lite_cphy_rx_clk', 'cam_cc_cphy_rx_clk_src'),
 (49204, 'cam_cc_ife_lite_csid_clk', 'cam_cc_ife_lite_csid_clk_src'),
 (32832, 'cam_cc_ipe_0_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (32828, 'cam_cc_ipe_0_areg_clk', 'cam_cc_fast_ahb_clk_src'),
 (32824, 'cam_cc_ipe_0_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (32808, 'cam_cc_ipe_0_clk', 'cam_cc_ipe_0_clk_src'),
 (49248, 'cam_cc_jpeg_clk', 'cam_cc_jpeg_clk_src'),
 (20504, 'cam_cc_mclk0_clk', 'cam_cc_mclk0_clk_src'),
 (20532, 'cam_cc_mclk1_clk', 'cam_cc_mclk1_clk_src'),
 (20560, 'cam_cc_mclk2_clk', 'cam_cc_mclk2_clk_src'),
 (20588, 'cam_cc_mclk3_clk', 'cam_cc_mclk3_clk_src'),
 (20616, 'cam_cc_mclk4_clk', 'cam_cc_mclk4_clk_src'),
 (20644, 'cam_cc_mclk5_clk', 'cam_cc_mclk5_clk_src'),
 (20672, 'cam_cc_mclk6_clk', 'cam_cc_mclk6_clk_src'),
 (36928, 'cam_cc_sbi_ahb_clk', 'cam_cc_slow_ahb_clk_src'),
 (36924, 'cam_cc_sbi_axi_clk', 'cam_cc_camnoc_axi_clk_src'),
 (36884, 'cam_cc_sbi_clk', 'cam_cc_sbi_div_clk_src'),
 (36920, 'cam_cc_sbi_cphy_rx_clk', 'cam_cc_cphy_rx_clk_src'),
 (36916, 'cam_cc_sbi_csid_clk', 'cam_cc_sbi_csid_clk_src'),
 (36932, 'cam_cc_sbi_ife_0_clk', 'cam_cc_ife_0_clk_src'),
 (36936, 'cam_cc_sbi_ife_1_clk', 'cam_cc_ife_1_clk_src'),
 (49544, 'cam_cc_sleep_clk', 'cam_cc_sleep_clk_src'))

POSTDIVS = (('cam_cc_pll0_out_even', 0, 8, 4, 1, 2),
 ('cam_cc_pll0_out_odd', 0, 12, 4, 3, 3),
 ('cam_cc_pll1_out_even', 1, 8, 4, 1, 2),
 ('cam_cc_pll2_out_main', 2, 8, 2, 1, 2),
 ('cam_cc_pll3_out_even', 3, 8, 4, 1, 2),
 ('cam_cc_pll4_out_even', 4, 8, 4, 1, 2))

REGISTERS = tuple(sorted(set(
    [base + offset for base, _, kind, *_ in PLLS
     for offset in (LUCID_OFFSETS if kind == "lucid" else ZONDA_OFFSETS)] +
    [base + offset for base, _, width, _ in RCGS
     for offset in ((0, 4, 8, 12, 16) if width else (0, 4))] +
    [offset for offset, *_ in BRANCHES] + list(RESETS) + [0x9010])))
WINDOWS = []
for offset in REGISTERS:
    if WINDOWS and offset == WINDOWS[-1][0] + WINDOWS[-1][1]:
        WINDOWS[-1] = (WINDOWS[-1][0], WINDOWS[-1][1] + 4)
    else:
        WINDOWS.append((offset, 4))
WINDOWS = tuple(WINDOWS)
GLOBAL_FIELDS = ("reference-profile", "reference-identity", "register-count", "clock-count",
                 "input-count", "pll-count", "rcg-count", "branch-count", "reset-count",
                 "window-count", "mapped-bytes", "physical-base")
PLL_FIELDS = ("active-l", "active-alpha", "pending-l", "pending-alpha", "locked", "ack",
              "calibrated", "freq-locked", "lock-pending", "latch-pending",
              "lock-deadline", "latch-deadline", "bypass-since")
RCG_FIELDS = tuple(stage + "-" + field for stage in ("active", "pending")
                   for field in ("cfg", "m", "n", "d")) + ("pending", "deadline")
MND = tuple(i for i, row in enumerate(RCGS) if row[2])
RCG_INDEX = {row[1]: i for i, row in enumerate(RCGS)}
assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(MND)) == (236, 74, 111, 10)


class CamRig(CameraRig):
    """Reuse transport/RPMh commands without changing imported module globals."""
    def __init__(self, args, directory, *, reference=True, incoming=False,
                 accel="qtest", rpmh=False, camera=False, legacy=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.camera_mmio_operations = self.camcc_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="camcc-"))
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
                   "-global", f"qcom-camera-gdsc.enabled-reference={'on' if camera else 'off'}"]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if legacy:
            command += ["-global", "qcom-sm8250-npucc.enabled-reference=on",
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
            if CC <= int(fields[1], 0) < CC + 0x10000:
                self.camcc_mmio_operations += 1
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

    # RpmhRig's relative read/write otherwise resolves through this controller.
    def dread(self, offset, drv=2):
        return self.raw_read(RPMH_BASE + drv * 0x10000 + offset)

    def dwrite(self, offset, value, drv=2):
        self.raw_write(RPMH_BASE + drv * 0x10000 + offset, value)

    def period(self, name, path=DEVICE):
        return self.qom("qtest-clock-period", path + "/" + name)

    def hz(self, name):
        return self.qom("clk." + name + ".hz")

    def expect_hz(self, name, hz):
        assert self.hz(name) == hz, (name, self.hz(name), hz)
        assert self.period(name) == (PERIOD_UNIT // hz if hz else 0), (name, hz)

    def fixture(self, name, hz):
        self.monitor("qom-set", {"path": DEVICE, "property": "qtest-" + name + "-hz", "value": hz})

    def pll(self, index, field):
        return self.qom(f"pll{index}-{field}")

    def rcg(self, index, field):
        return self.qom(f"rcg{index}-{field}")

    def pure_snapshot(self, *, include_clock_periods=True):
        snapshot = {"globals": {n: self.qom(n) for n in GLOBAL_FIELDS},
                    "registers": {f"{o:04x}": self.reg(o) for o in REGISTERS},
                    "plls": [{n: self.pll(i, n) for n in PLL_FIELDS} for i in range(5)],
                    "rcgs": [{n: self.rcg(i, n) for n in RCG_FIELDS} for i in range(33)],
                    "last-xo-hz": self.qom("last-xo-hz"), "sbi-divider": self.qom("sbi-divider"),
                    "output_hz": {n: self.hz(n) for n in OUTPUTS}}
        if include_clock_periods:
            # Clock's qtest-clock-period observer itself exists only under
            # QTest. Normal TCG still has every controller observer above.
            snapshot["outputs"] = {n: self.period(n) for n in OUTPUTS}
            snapshot["inputs"] = {n: self.period(n) for n in INPUTS}
        return snapshot

    def camera_snapshot(self):
        return {"enabled-reference": self.qom("enabled-reference", CAMERA),
                "globals": {n: self.qom(n, CAMERA) for n in GDSC_GLOBAL_FIELDS},
                "parent-known": self.qom("parent-known", CAMERA),
                "parent-on": self.qom("parent-on", CAMERA),
                "domains": [{n: self.qom(f"domain{i}.{n}", CAMERA)
                             for n in GDSC_DOMAIN_FIELDS} for i in range(6)]}

    def reject_write(self, offset, value):
        before = self.reg(offset)
        count = self.log_path.read_text(errors="replace").count(TYPE + ":")
        self.write(offset, value)
        assert self.reg(offset) == before, (hex(offset), hex(value))
        assert self.log_path.read_text(errors="replace").count(TYPE + ":") == count + 1

    def reset_lines(self, expected, require_seen=False):
        self.raw_read(RAM)
        assert [self.irq.get(i, False) for i in range(6)] == list(expected)
        if require_seen:
            assert set(range(6)) <= self.seen_irq

    def reset_device(self):
        self.events.clear()
        self.monitor("system_reset")
        self.wait_for(lambda: all(self.reg(p[0] + 4) == 0 for p in PLLS), "CAMCC reset witnesses")
        self.monitor("query-status")
        assert any(e.get("event") == "RESET" for e in self.events)


def pll_rate(index, l=None, alpha=None, xo=XO):
    row = PLLS[index]
    l = row[3] if l is None else l
    alpha = row[4] if alpha is None else alpha
    return xo * l + ((xo * alpha) >> (32 if index == 2 else 16))


def pll_reg(index, field):
    common = {"mode": 0, "l": 4, "user": 12}
    lucid = {"cal": 8, "alpha": 64, "opmode": 56, "config": 24, "config-u": 28,
             "config-u1": 32, "test": 36, "test-u": 40, "test-u1": 44}
    zonda = {"alpha": 8, "opmode": 40, "config": 16, "config-u": 20,
             "config-u1": 24, "test": 28, "test-u": 32, "test-u1": 36}
    return PLLS[index][0] + {**common, **(zonda if index == 2 else lucid)}[field]


def configure_pll(r, index):
    base, _, kind, l, alpha, user = PLLS[index]
    # Exact nonzero conditional writes of the fixed configure helpers. In
    # particular Zonda +8 is ALPHA, never CAL_L, and zero config fields do not
    # clear previous contents. This function intentionally does not normalize.
    if kind == "lucid":
        mode, opmode = r.read(base), r.read(base + 56)
        if mode & OUTCTRL and opmode & 1:
            return
        writes = ((4, l), (8, 0x44), (64, alpha), (24, 0x20485699),
                  (28, 0x2261), (32, 0x029a699c), (12, user), (16, 0x805))
    else:
        writes = ((4, l), (16, 0x08200920), (20, 0x05008011), (12, user))
    for offset, value in writes:
        r.write(base + offset, value)
    if kind == "lucid":
        r.write(base, r.read(base) | UPDATE_BYPASS)
    else:
        r.write(base, r.read(base) & ~BYPASS)
    r.write(base, r.read(base) & ~OUTCTRL)
    r.write(pll_reg(index, "opmode"), 0)
    r.write(base, r.read(base) | RESET_N)


def configure_all(r):
    for index in (0, 1, 3, 4, 2):
        configure_pll(r, index)


def output_on(r, index):
    user = pll_reg(index, "user")
    r.write(user, r.read(user) | (15 if index == 2 else 7))
    mode = pll_reg(index, "mode")
    r.write(mode, r.read(mode) | OUTCTRL)


def enable_all(r):
    r.write(0x2000, r.read(0x2000) | BYPASS)
    r.step(1000)
    for i in range(5):
        r.write(pll_reg(i, "opmode"), 1)
    r.step(LOCK_NS)
    for i, (_, name, *_rest) in enumerate(PLLS):
        assert r.pll(i, "locked") is True
        output_on(r, i)
        r.expect_hz(name, pll_rate(i))


def rcg_cfg(selector, hid=1, mode=0):
    return HW_CLK | selector << 8 | hid | mode << 12


def configure_rcg(r, index, selector=0, hid=1, *, m=0, n=0, wait=True, root=True):
    offset, _, width, _ = RCGS[index]
    if n:
        assert width == 8 and 0 < m < n <= 255
        for delta, value in ((8, m), (12, ~(n-m) & 255), (16, ~n & 255)):
            r.write(offset + delta, value)
    r.write(offset + 4, rcg_cfg(selector, hid, 2 if n else 0))
    r.write(offset, (ROOT if root else 0) | BUSY)
    if wait:
        r.step(RCG_NS)
        assert not r.read(offset) & BUSY


def divided(hz, hid, m=0, n=0):
    hz = hz * 2 // (hid + 1) if hid else hz
    return hz * m // n if n else hz


def expected_source_hz(name):
    if name in ("bi_tcxo", "bi_tcxo_ao"):
        return XO
    for i, row in enumerate(PLLS):
        if row[1] == name:
            return pll_rate(i)
    for output, pll, _, _, _, divisor in POSTDIVS:
        if output == name:
            return pll_rate(pll) // divisor
    return 0


def expect_root(r, index, hz):
    offset, name, *_ = RCGS[index]
    r.expect_hz(name, hz)
    assert bool(r.read(offset) & OFF) == (hz == 0)


def run_defaults(r):
    configure_all(r)
    enable_all(r)
    for i in range(33):
        configure_rcg(r, i)


def cold_reference_has_exact_identity_and_no_invented_clocks(r):
    values = (1, IDENTITY, 236, 111, 5, 5, 33, 66, 6, 74, 944, CC)
    assert {n: r.qom(n) for n in GLOBAL_FIELDS} == dict(zip(GLOBAL_FIELDS, values))
    status = {x[0] for x in RCGS} | {x[0] for x in BRANCHES}
    for offset in REGISTERS:
        assert r.reg(offset) == 0
        assert r.read(offset) == (OFF if offset in status else 0), hex(offset)
    for name in OUTPUTS:
        r.expect_hz(name, 0)
    for i, name in enumerate(INPUTS):
        assert r.period(name) == (PERIOD_UNIT // XO if i < 2 else 0)
    before = r.pure_snapshot()
    for name, value in list(zip(GLOBAL_FIELDS, values)) + [("reg.0000", 0), ("pll0-active-l", 0),
            ("pll2-freq-locked", False), ("rcg22-active-m", 0), ("sbi-divider", 0)]:
        result = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in result and "not writable" in result["error"]["desc"]
    r.step(100_000)
    assert r.pure_snapshot() == before


def literal_v2_configure_vectors_remain_in_standby(r):
    configure_all(r)
    for i, (base, name, kind, l, alpha, user) in enumerate(PLLS):
        assert r.read(base) == RESET_N | (UPDATE_BYPASS if kind == "lucid" else 0)
        assert r.read(base + 4) == l
        assert r.read(pll_reg(i, "alpha")) == alpha
        assert r.read(base + 12) == user
        assert r.read(pll_reg(i, "opmode")) == 0
        if kind == "lucid":
            for offset, value in ((8, 0x44), (16, 0x805), (20, 0), (24, 0x20485699),
                                  (28, 0x2261), (32, 0x029a699c), (36, 0), (40, 0), (44, 0)):
                assert r.read(base + offset) == value, (i, hex(offset))
        else:
            for offset, value in ((8, 0), (16, 0x08200920), (20, 0x05008011),
                                  (24, 0), (28, 0), (32, 0), (36, 0)):
                assert r.read(base + offset) == value, (i, hex(offset))
        assert not r.pll(i, "lock-pending") and not r.pll(i, "locked")
        r.expect_hz(name, 0)
    r.step(100_000)
    assert all(not r.pll(i, "locked") for i in range(5))


def conditional_zero_fields_are_not_silently_cleared_by_configure(r):
    for i in LUCID:
        base = PLLS[i][0]
        r.write(base + 20, 0x1234)
        r.write(base + 36, 0x5678)
        configure_pll(r, i)
        assert r.read(base + 20) == 0x1234 and r.read(base + 36) == 0x5678
    r.write(0x2008, 0x89abcdef)
    r.write(0x2018, 0x1234)
    r.write(0x201c, 0x10000)
    configure_pll(r, 2)
    assert r.read(0x2008) == 0x89abcdef
    assert r.read(0x2018) == 0x1234 and r.read(0x201c) == 0x10000
    assert r.read(0x2004) == 0x4b
    for name in OUTPUTS:
        r.expect_hz(name, 0)


def five_banks_and_the_two_pll_register_kinds_do_not_alias(r):
    for i, row in enumerate(PLLS):
        r.write(row[0] + 4, 0x30 + i)
        r.write(pll_reg(i, "alpha"), 0x12345678 if i == 2 else 0x1000 + i)
    for i, row in enumerate(PLLS):
        assert r.read(row[0] + 4) == 0x30 + i
        assert r.read(pll_reg(i, "alpha")) == (0x12345678 if i == 2 else 0x1000 + i)
    assert r.read(0x2008) == 0x12345678 and r.read(0x3008) == 0
    assert r.read(0x2028) == 0 and r.read(0x3028) == 0


def five_staggered_lock_deadlines_are_independent(r):
    configure_all(r)
    r.write(0x2000, r.read(0x2000) | BYPASS)
    r.step(1000)
    deadlines = []
    for i in range(5):
        r.write(pll_reg(i, "opmode"), 1)
        deadlines.append(r.last_clock + LOCK_NS)
        assert r.pll(i, "lock-deadline") == deadlines[-1]
        r.step(137)
    for i, deadline in enumerate(deadlines):
        r.set_clock(deadline - 1)
        assert r.pll(i, "locked") is False
        r.step(1)
        assert [r.pll(j, "locked") for j in range(5)] == [j <= i for j in range(5)]
        assert r.pll(i, "calibrated") is (i != 2)
        assert r.pll(i, "ack") is False and r.pll(i, "freq-locked") is False
        r.expect_hz(PLLS[i][1], 0)


def each_pll_requires_its_output_gates_and_kind_specific_status(r):
    configure_all(r)
    enable_all(r)
    for i, (base, name, *_rest) in enumerate(PLLS):
        user, mask = base + 12, 15 if i == 2 else 7
        saved = r.reg(user)
        r.write(user, saved & ~mask)
        r.expect_hz(name, 0)
        assert r.pll(i, "locked") is True
        r.write(user, saved)
        r.write(base, r.read(base) & ~OUTCTRL)
        r.expect_hz(name, 0)
        r.write(base, r.read(base) | OUTCTRL)
        r.expect_hz(name, pll_rate(i))
        r.reject_write(user, (saved & ~mask) | 1)
        assert r.pll(i, "ack") is False and r.pll(i, "freq-locked") is False
        assert r.pll(i, "calibrated") is (i != 2)
        assert bool(r.read(base) & PCAL) is (i != 2)


def six_postdividers_use_only_the_declared_nonzero_selectors(r):
    configure_all(r)
    enable_all(r)
    for name, index, shift, width, selector, divisor in POSTDIVS:
        user = PLLS[index][0] + 12
        original = r.reg(user)
        mask = ((1 << width) - 1) << shift
        r.expect_hz(name, pll_rate(index) // divisor)
        r.write(user, original & ~mask)
        r.expect_hz(name, 0)
        r.expect_hz(PLLS[index][1], pll_rate(index))
        unknown = 2 if selector == 1 else 1
        r.reject_write(user, (original & ~mask) | unknown << shift)
        r.write(user, original)
        r.expect_hz(name, pll_rate(index) // divisor)
    assert r.period("cam_cc_pll2_out_aux2") == 0
    return {"selector_zero_is_not_assumed_divide_one": True}


def unsupported_lucid_recipe_drops_only_its_own_lock(r):
    configure_all(r)
    enable_all(r)
    for i in LUCID:
        base = PLLS[i][0]
        for offset, bad in ((32, 0x329a699c), (16, 0x804), (8, 0x43)):
            good = r.reg(base + offset)
            r.write(base + offset, bad)
            assert r.pll(i, "locked") is False
            r.expect_hz(PLLS[i][1], 0)
            for other in range(5):
                if other != i:
                    r.expect_hz(PLLS[other][1], pll_rate(other))
            r.step(10_000)
            assert r.pll(i, "locked") is False
            r.write(base + offset, good)
            r.step(LOCK_NS)
            r.expect_hz(PLLS[i][1], pll_rate(i))


def zonda_requires_bypass_delay_and_retry_and_rejects_cfa_recipe(r):
    configure_pll(r, 2)
    r.write(0x2000, r.read(0x2000) | BYPASS)
    r.write(0x2028, 1)
    r.step(20_000)
    assert r.pll(2, "locked") is False and r.pll(2, "lock-pending") is False
    r.write(0x2028, 0)
    r.write(0x2028, 1)
    r.step(4999)
    assert r.pll(2, "locked") is False
    r.step(1)
    assert r.pll(2, "locked") is True
    output_on(r, 2)
    r.expect_hz("cam_cc_pll2", pll_rate(2))
    r.write(0x201c, 0x10000)
    r.step(20_000)
    assert not r.read(0x2000) & (LOCK | FREQ_LOCK | PCAL)
    assert all(r.pll(2, field) is False for field in ("locked", "ack", "calibrated", "freq-locked"))
    r.write(0x201c, 0)
    r.write(0x2028, 0)
    r.write(0x2028, 1)
    r.step(5000)
    r.expect_hz("cam_cc_pll2", pll_rate(2))


def four_lucid_updates_capture_shadow_once_then_ack_and_relock(r):
    configure_all(r)
    enable_all(r)
    captured = {}
    for i in LUCID:
        base = PLLS[i][0]
        captured[i] = (PLLS[i][3] + 1, 0x2000 + i)
        r.write(base + 4, captured[i][0])
        r.write(base + 64, captured[i][1])
        r.write(base, r.read(base) | UPDATE)
        assert r.pll(i, "latch-deadline") == r.last_clock + LATCH_NS
        r.write(base + 4, PLLS[i][3] + 2)
        r.write(base + 64, 0x3000 + i)
        r.write(base, r.read(base) | UPDATE)
        assert (r.pll(i, "pending-l"), r.pll(i, "pending-alpha")) == captured[i]
    r.step(999)
    for i in LUCID:
        assert r.pll(i, "ack") is False
        r.expect_hz(PLLS[i][1], pll_rate(i))
    r.step(1)
    for i in LUCID:
        assert r.pll(i, "ack") is True and r.pll(i, "locked") is False
        assert (r.pll(i, "active-l"), r.pll(i, "active-alpha")) == captured[i]
        r.expect_hz(PLLS[i][1], 0)
        r.write(PLLS[i][0], r.read(PLLS[i][0]) & ~UPDATE)
        assert r.pll(i, "ack") is False
    r.step(4999)
    assert all(r.pll(i, "locked") is False for i in LUCID)
    r.step(1)
    for i in LUCID:
        r.expect_hz(PLLS[i][1], pll_rate(i, *captured[i]))
    r.expect_hz("cam_cc_pll2", pll_rate(2))


def zonda_l_write_captures_alpha_and_settles_without_lucid_ack(r):
    configure_all(r)
    enable_all(r)
    r.write(0x2008, 0x80000000)
    r.write(0x2004, 0x4c)
    first_deadline = r.last_clock + ZONDA_SETTLE_NS
    assert r.pll(2, "latch-deadline") == first_deadline
    assert (r.pll(2, "pending-l"), r.pll(2, "pending-alpha")) == (0x4c, 0x80000000)
    r.expect_hz("cam_cc_pll2", 0)
    r.write(0x2008, 0xffffffff)
    assert r.pll(2, "latch-deadline") == first_deadline
    r.step(4999)
    assert r.pll(2, "active-l") == 0x4b
    r.step(1)
    assert (r.pll(2, "active-l"), r.pll(2, "active-alpha")) == (0x4c, 0x80000000)
    assert r.pll(2, "lock-deadline") == first_deadline + LOCK_NS
    assert r.pll(2, "ack") is False
    r.step(4999)
    assert r.pll(2, "locked") is False
    r.step(1)
    r.expect_hz("cam_cc_pll2", pll_rate(2, 0x4c, 0x80000000))
    r.write(0x2004, 0x4d)
    r.step(100)
    r.write(0x2004, 0x4e)
    assert r.pll(2, "latch-deadline") == r.last_clock + ZONDA_SETTLE_NS
    r.step(ZONDA_SETTLE_NS + LOCK_NS)
    r.expect_hz("cam_cc_pll2", pll_rate(2, 0x4e, 0xffffffff))
    r.write(0x2004, 0)
    r.step(20_000)
    assert r.pll(2, "locked") is False
    r.expect_hz("cam_cc_pll2", 0)


def pll_kind_masks_keep_zonda_alpha32_and_reject_lucid_handshakes(r):
    configure_all(r)
    r.write(0x2008, 0xffffffff)
    assert r.read(0x2008) == 0xffffffff
    for i in LUCID:
        r.reject_write(PLLS[i][0] + 64, 0x10000)
    for bad in (UPDATE, UPDATE_BYPASS, PCAL):
        r.reject_write(0x2000, r.reg(0x2000) | bad)
    r.write(0x2000, r.reg(0x2000) | FREQ_LOCK | LOCK)
    assert not r.read(0x2000) & (FREQ_LOCK | LOCK)
    assert r.pll(2, "ack") is False and r.pll(2, "freq-locked") is False


def pll_q16_q32_frequency_arithmetic_and_bounded_vco_endpoints(r):
    for i in range(5):
        bits = 32 if i == 2 else 16
        low, high = (595_200_000, 3_600_000_000) if i == 2 else (249_600_000, 2_000_000_000)
        high_l, remainder = divmod(high, XO)
        high_a = ((remainder << bits) + XO - 1) // XO
        bad_a = high_a + (0x10000 if i == 2 else 1)
        cases = ((low // XO, 0, True), (high_l, high_a, True),
                 (low // XO - 1, 0, False), (high_l, bad_a, False))
        for l, alpha, valid in cases:
            r.reset_device()
            configure_pll(r, i)
            r.write(pll_reg(i, "l"), l)
            r.write(pll_reg(i, "alpha"), alpha)
            if i == 2:
                r.write(0x2000, r.read(0x2000) | BYPASS)
                r.step(1000)
            r.write(pll_reg(i, "opmode"), 1)
            output_on(r, i)
            r.step(5000)
            assert r.pll(i, "locked") is valid, (i, l, alpha, valid)
            r.expect_hz(PLLS[i][1], pll_rate(i, l, alpha) if valid else 0)
    assert pll_rate(3) == pll_rate(4) == 699_999_902


def standby_reset_and_cancel_do_not_stop_other_plls(r):
    configure_all(r)
    enable_all(r)
    for i in range(5):
        r.write(pll_reg(i, "opmode"), 0)
        r.expect_hz(PLLS[i][1], 0)
        for j in range(5):
            if j != i:
                r.expect_hz(PLLS[j][1], pll_rate(j))
        r.write(pll_reg(i, "opmode"), 1)
        r.step(5000)
        r.expect_hz(PLLS[i][1], pll_rate(i))
    for i in LUCID:
        base = PLLS[i][0]
        r.write(base + 4, PLLS[i][3] + 1)
        r.write(base, r.read(base) | UPDATE)
        r.step(500)
        r.write(base, r.read(base) & ~UPDATE)
        r.step(10_000)
        assert not r.pll(i, "ack") and not r.pll(i, "latch-pending")
        r.expect_hz(PLLS[i][1], pll_rate(i))
    r.write(0x2000, r.read(0x2000) & ~RESET_N)
    assert r.pll(2, "locked") is False
    r.expect_hz("cam_cc_pll2", 0)
    assert all(r.pll(i, "locked") for i in LUCID)


def late_dispatch_keeps_original_latch_and_relock_deadlines(r):
    configure_all(r)
    enable_all(r)
    for i in LUCID:
        base = PLLS[i][0]
        r.write(base + 4, PLLS[i][3] + 1)
        r.write(base, r.read(base) | UPDATE)
    r.write(0x2008, 0x80000000)
    r.write(0x2004, 0x4c)
    r.set_clock(r.last_clock + 20_000)
    # No CAMCC MMIO before these checks: timer dispatch itself must finish.
    for i in LUCID:
        assert r.pll(i, "ack") is True and r.pll(i, "locked") is True
        r.expect_hz(PLLS[i][1], pll_rate(i, PLLS[i][3] + 1, PLLS[i][4]))
    assert r.pll(2, "locked") is True and r.pll(2, "ack") is False
    r.expect_hz("cam_cc_pll2", pll_rate(2, 0x4c, 0x80000000))


def all_33_rcgs_capture_shadow_once_at_their_deadline(r):
    for i, (offset, *_rest) in enumerate(RCGS):
        configure_rcg(r, i, wait=False)
        assert r.rcg(i, "pending-cfg") == rcg_cfg(0)
        assert r.rcg(i, "deadline") == r.last_clock + RCG_NS
        r.write(offset + 4, rcg_cfg(0, 3))
        r.write(offset, ROOT | BUSY)
        assert r.rcg(i, "pending-cfg") == rcg_cfg(0)
    r.step(999)
    assert all(r.rcg(i, "active-cfg") == 0 for i in range(33))
    r.step(1)
    for i, (_, name, _, parents) in enumerate(RCGS):
        assert r.rcg(i, "active-cfg") == rcg_cfg(0)
        assert r.rcg(i, "pending") is False and r.rcg(i, "deadline") == -1
        assert r.reg(RCGS[i][0] + 4) == rcg_cfg(0, 3)
        expect_root(r, i, expected_source_hz(dict(parents)[0]))


def every_declared_mux_routes_its_actual_parent(r):
    configure_all(r)
    enable_all(r)
    for i, (_offset, _name, _width, parents) in enumerate(RCGS):
        for selector, parent in parents:
            configure_rcg(r, i, selector)
            expect_root(r, i, expected_source_hz(parent))
    assert r.period("cam_cc_pll2_out_aux2") == 0


def ten_mnd_rcgs_capture_cfg_and_raw_fraction_words_together(r):
    expected_raw = (5, ~(7-5) & 255, ~7 & 255)
    for i in MND:
        offset = RCGS[i][0]
        configure_rcg(r, i, 0, 3, m=5, n=7, wait=False)
        assert tuple(r.rcg(i, "pending-" + field) for field in ("m", "n", "d")) == expected_raw
        for delta, value in ((8, 1), (12, 0xfa), (16, 0xf9)):
            r.write(offset + delta, value)
        r.write(offset + 4, rcg_cfg(0, 1, 2))
        r.write(offset, ROOT | BUSY)
    r.step(999)
    assert all(r.rcg(i, "active-m") == 0 for i in MND)
    r.step(1)
    for i in MND:
        assert tuple(r.rcg(i, "active-" + field) for field in ("m", "n", "d")) == expected_raw
        assert r.rcg(i, "active-cfg") == rcg_cfg(0, 3, 2)
        expect_root(r, i, divided(XO, 3, 5, 7))
        assert r.reg(RCGS[i][0] + 8) == 1


def all_seven_mclks_replay_v2_fraction_vectors_and_cci_slow_integer_vectors(r):
    configure_all(r)
    enable_all(r)
    for i in range(7):
        index = RCG_INDEX[f"cam_cc_mclk{i}_clk_src"]
        configure_rcg(r, index, 5, 19, m=1, n=6)
        expect_root(r, index, 24_000_000)
        assert tuple(r.reg(RCGS[index][0] + delta) for delta in (8, 12, 16)) == (1, 0xfa, 0xf9)
        configure_rcg(r, index, 5, 1, m=1, n=21)
        expect_root(r, index, 68_571_428)
    for name in ("cam_cc_cci_0_clk_src", "cam_cc_cci_1_clk_src"):
        index = RCG_INDEX[name]
        configure_rcg(r, index, 2, 31)
        expect_root(r, index, 37_500_000)
    index = RCG_INDEX["cam_cc_slow_ahb_clk_src"]
    configure_rcg(r, index, 2, 14)
    expect_root(r, index, 80_000_000)
    return {"mclk_v2_24mhz_m_n": [1, 6], "integer_68571429_table_rounding_hz": 68_571_428}


def mode_zero_bypasses_retained_fraction_registers(r):
    for i in MND:
        offset = RCGS[i][0]
        configure_rcg(r, i, 0, 3, m=1, n=6)
        previous = tuple(r.reg(offset + delta) for delta in (8, 12, 16))
        configure_rcg(r, i, 0, 1)
        assert tuple(r.reg(offset + delta) for delta in (8, 12, 16)) == previous == (1, 0xfa, 0xf9)
        assert tuple(r.rcg(i, "active-" + field) for field in ("m", "n", "d")) == previous
        assert r.rcg(i, "active-cfg") == rcg_cfg(0, 1, 0)
        expect_root(r, i, XO)


def undefined_mux_modes_and_bounded_mnd_inputs_never_ack(r):
    for i, (offset, _name, width, parents) in enumerate(RCGS):
        good = set(dict(parents))
        invalid_selector = next(sel for sel in range(8) if sel not in good)
        for cfg in (rcg_cfg(invalid_selector), rcg_cfg(0, 1, 1), rcg_cfg(0, 1, 3)):
            r.write(offset, 0)
            r.write(offset + 4, cfg)
            r.write(offset, ROOT | BUSY)
            r.step(2000)
            assert r.read(offset) & BUSY and r.rcg(i, "active-cfg") == 0
            assert r.rcg(i, "pending") is False and r.rcg(i, "deadline") == -1
        if not width:
            r.write(offset, 0)
            r.write(offset + 4, rcg_cfg(0, 1, 2))
            r.write(offset, ROOT | BUSY)
            r.step(1000)
            assert r.read(offset) & BUSY and r.rcg(i, "active-cfg") == 0
        else:
            for raw in ((0, 0xfa, 0xf9), (1, 0xff, 0xfe),
                        (2, 0, 0), (1, 0xfa, 0xf8)):
                r.write(offset, 0)
                for delta, value in zip((8, 12, 16), raw):
                    r.write(offset + delta, value)
                r.write(offset + 4, rcg_cfg(0, 1, 2))
                r.write(offset, ROOT | BUSY)
                r.step(1000)
                assert r.read(offset) & BUSY and r.rcg(i, "active-cfg") == 0
                assert r.rcg(i, "pending") is False
    return {"mnd_negative_scope": "bounded mode2: 0<M<N<=255 and source D encoding"}


def all_hid_fields_cover_bypass_integer_and_half_division(r):
    r.fixture("chip_sleep_clk", 1_000_003)
    for i, (_offset, _name, _width, parents) in enumerate(RCGS):
        parent = 1_000_003 if dict(parents)[0] == "chip_sleep_clk" else XO
        for hid in (0, 1, 2, 3, 14, 31):
            configure_rcg(r, i, 0, hid)
            expect_root(r, i, divided(parent, hid))


def unknown_aux2_sleep_and_test_inputs_only_gain_explicit_fixture_rates(r):
    configure_all(r)
    enable_all(r)
    indices = [RCG_INDEX[f"cam_cc_mclk{i}_clk_src"] for i in range(7)]
    for i in indices:
        configure_rcg(r, i, 3, 3)
        expect_root(r, i, 0)
    r.fixture("cam_cc_pll2_out_aux2", 123_456_789)
    for i in indices:
        expect_root(r, i, 61_728_394)
    r.fixture("cam_cc_pll2_out_aux2", 0)
    for i in indices:
        expect_root(r, i, 0)
    sleep = RCG_INDEX["cam_cc_sleep_clk_src"]
    configure_rcg(r, sleep, 0)
    expect_root(r, sleep, 0)
    r.fixture("chip_sleep_clk", 32768)
    expect_root(r, sleep, 32768)
    configure_rcg(r, 0, 7)
    expect_root(r, 0, 0)
    r.fixture("core_bi_pll_test_se", 111_111_111)
    expect_root(r, 0, 111_111_111)
    r.fixture("core_bi_pll_test_se", 0)
    expect_root(r, 0, 0)


def all_66_branches_have_independent_local_requests(r):
    r.fixture("chip_sleep_clk", 32768)
    for i in range(33):
        configure_rcg(r, i, root=False)
    for i, (offset, name, parent) in enumerate(BRANCHES):
        r.expect_hz(name, 0)
        r.write(offset, 1)
        expected = 32768 if parent == "cam_cc_sleep_clk_src" else XO
        r.expect_hz(name, expected)
        assert r.read(offset) == 1
        neighbor = BRANCHES[(i + 1) % len(BRANCHES)][1]
        r.expect_hz(neighbor, 0)
        r.reject_write(offset, 3)
        r.write(offset, 0)
        r.expect_hz(name, 0)
        assert r.read(offset) == OFF
    return {"local_branch_requests_checked": 66, "remote_vote_gpio_assumed": False}


def sbi_readonly_divider_uses_explicit_fixture_and_generic_ceil(r):
    index = RCG_INDEX["cam_cc_ife_0_clk_src"]
    r.fixture("core_bi_pll_test_se", 840_001)
    configure_rcg(r, index, 7, root=False)
    r.write(0x9014, 1)
    for value in range(8):
        r.monitor("qom-set", {"path": DEVICE, "property": "qtest-sbi-divider", "value": value})
        assert r.read(0x9010) == r.qom("sbi-divider") == value
        expected = (840_001 + value) // (value + 1)
        r.expect_hz("cam_cc_sbi_div_clk_src", expected)
        r.expect_hz("cam_cc_sbi_clk", expected)
        r.expect_hz("cam_cc_ife_0_clk_src", 840_001)
        r.reject_write(0x9010, 0)
        r.reject_write(0x9010, value)
    r.write(0x9014, 0)
    r.expect_hz("cam_cc_sbi_clk", 0)
    r.expect_hz("cam_cc_ife_0_clk_src", 0)
    return {"generic_divider_rounds_up": True, "fixture_divider_values": 8}


def six_reset_lines_follow_binding_order_without_touching_camera_gdsc(r):
    configure_all(r)
    enable_all(r)
    before = r.camera_snapshot()
    for i, offset in enumerate(RESETS):
        r.write(offset, 1)
        assert r.read(offset) == 1
        r.reset_lines([j == i for j in range(6)])
        assert r.camera_snapshot() == before
        for pll, row in enumerate(PLLS):
            r.expect_hz(row[1], pll_rate(pll))
        r.write(offset, 0)
        r.reset_lines([False] * 6)
    r.reset_lines([False] * 6, True)


def read_only_feedback_bits_cannot_be_forged_or_erased(r):
    configure_all(r)
    for i, (offset, *_rest) in enumerate(PLLS):
        status = LOCK | FREQ_LOCK if i == 2 else LOCK | ACK | PCAL
        r.write(offset, r.reg(offset) | status)
        assert not r.read(offset) & status
    for offset, *_rest in RCGS:
        r.write(offset, OFF)
        assert r.read(offset) == OFF and r.reg(offset) == 0
    for offset, *_rest in BRANCHES:
        r.write(offset, OFF)
        assert r.read(offset) == OFF and r.reg(offset) == 0
    enable_all(r)
    for i, (offset, name, *_rest) in enumerate(PLLS):
        status = LOCK | FREQ_LOCK if i == 2 else LOCK | ACK | PCAL
        r.write(offset, r.read(offset) & ~status)
        assert r.read(offset) & LOCK
        r.expect_hz(name, pll_rate(i))


def reserved_masks_narrow_accesses_and_unowned_words_reject_atomically(r):
    cases = ((4, 0x10000), (8, 0x10000), (64, 0x10000), (0x2028, 2),
             (0x7014, 1 << 21), (0x5008, 0x100), (0x7000, 2), (0x7028, 2))
    for offset, value in cases:
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
        old = (r.reg(0), r.reg(4))
        count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
        if op == "read":
            r.read_absolute(CC + 1)
        else:
            r.write_absolute(CC + 1, 0xffffffff)
        new = core_diagnostics(r.log_path.read_text(errors="replace"))[count:]
        assert new and all(item[0] == op and "invalid size" in item[-1] for item in new)
        assert (r.reg(0), r.reg(4)) == old
    holes = (0x30, 0x34, 0x1030, 0x202c, 0x2038, 0x3044, 0x4044,
             0x5014, 0x7018, 0xe004, 0xfffc)
    count = len(core_diagnostics(r.log_path.read_text(errors="replace")))
    expected = []
    for offset in holes:
        r.read(offset)
        r.write(offset, 0xffffffff)
        expected.extend((op, CC + offset, 4, "(null)", "rejected") for op in ("read", "write"))
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[count:] == expected


def system_reset_cancels_all_work_and_preserves_external_clock_fixtures(r):
    configure_all(r)
    enable_all(r)
    for name, hz in zip(INPUTS[2:], (123_456_789, 32768, 222_222_222)):
        r.fixture(name, hz)
    for i in LUCID:
        r.write(PLLS[i][0] + 4, PLLS[i][3] + 1)
        r.write(PLLS[i][0], r.read(PLLS[i][0]) | UPDATE)
    r.write(0x2008, 0x80000000)
    r.write(0x2004, 0x4c)
    for i in range(33):
        configure_rcg(r, i, wait=False)
    for offset in RESETS:
        r.write(offset, 1)
    r.monitor("qom-set", {"path": DEVICE, "property": "qtest-sbi-divider", "value": 7})
    inputs = {name: r.period(name) for name in INPUTS}
    r.reset_device()
    assert all(r.reg(offset) == 0 for offset in REGISTERS)
    for i in range(5):
        assert not r.pll(i, "locked") and not r.pll(i, "ack") and not r.pll(i, "calibrated")
        assert r.pll(i, "lock-deadline") == r.pll(i, "latch-deadline") == r.pll(i, "bypass-since") == -1
    for i in range(33):
        assert not r.rcg(i, "pending") and r.rcg(i, "deadline") == -1
        assert all(r.rcg(i, stage + "-" + field) == 0
                   for stage in ("active", "pending") for field in ("cfg", "m", "n", "d"))
    assert {name: r.period(name) for name in INPUTS} == inputs
    r.reset_lines([False] * 6)
    r.step(20_000)
    for name in OUTPUTS:
        r.expect_hz(name, 0)


def fixture_types_ranges_and_connected_input_protection(r):
    before = {name: r.period(name) for name in INPUTS}
    for name in INPUTS:
        for value in (-1, 1 << 32, "19200000", True):
            result = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-" + name + "-hz", "value": value})
            assert "error" in result
    for name in INPUTS[:2]:
        result = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-" + name + "-hz", "value": 1})
        assert "error" in result and "connected" in result["error"]["desc"]
    for value in (-1, 8, 1 << 32, "1", True):
        result = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-sbi-divider", "value": value})
        assert "error" in result
    for name in INPUTS[2:]:
        prop = "qtest-" + name + "-hz"
        # Clock periods are integer fixed point. These four uint32 values
        # all quantize to a period whose frequency is 2^32; an unsigned
        # clock_get_hz conversion must not turn a live input into zero.
        for value in range(4_294_967_292, 4_294_967_296):
            result = r.raw_monitor("qom-set", {"path": DEVICE, "property": prop, "value": value})
            assert "error" in result
            assert {n: r.period(n) for n in INPUTS} == before
        r.fixture(name, 4_294_967_291)
        assert r.period(name) == 1_000_000_001
        assert r.qom(prop) == 4_294_967_291
        r.fixture(name, 0)
    assert {name: r.period(name) for name in INPUTS} == before
    assert r.qom("sbi-divider") == 0
    return {"largest_representable_uint32_fixture_hz": 4_294_967_291,
            "unrepresentable_uint32_frequencies_rejected_per_input": 4}


def real_rpmh_xo_votes_propagate_the_two_connected_input_clocks(r):
    assert r.has_rpmh
    assert r.period("bi_tcxo") == r.period("bi_tcxo_ao") == 0
    configure_all(r)
    r.step(20_000)
    assert all(not r.pll(i, "locked") for i in range(5))
    r.run_one("xo.lvl", 1)
    assert r.period("bi_tcxo") == r.period("bi_tcxo_ao") == PERIOD_UNIT // XO
    configure_all(r)
    enable_all(r)
    r.run_one("xo.lvl", 0)
    assert r.period("bi_tcxo") == r.period("bi_tcxo_ao") == 0
    assert all(not r.pll(i, "locked") for i in range(5))
    for row in PLLS:
        r.expect_hz(row[1], 0)
    r.run_one("xo.lvl", 1)
    for i in range(5):
        r.write(pll_reg(i, "opmode"), 0)
        r.write(pll_reg(i, "opmode"), 1)
    r.step(LOCK_NS)
    for i, row in enumerate(PLLS):
        r.expect_hz(row[1], pll_rate(i))
    assert all(r.period(name) == 0 for name in INPUTS[2:])
    return {"real_xo_resource": "xo.lvl", "clock_fixture_for_connected_input_used": False}


ORDINARY = [
    (cold_reference_has_exact_identity_and_no_invented_clocks, {}),
    (literal_v2_configure_vectors_remain_in_standby, {}),
    (conditional_zero_fields_are_not_silently_cleared_by_configure, {}),
    (five_banks_and_the_two_pll_register_kinds_do_not_alias, {}),
    (five_staggered_lock_deadlines_are_independent, {}),
    (each_pll_requires_its_output_gates_and_kind_specific_status, {}),
    (six_postdividers_use_only_the_declared_nonzero_selectors, {}),
    (unsupported_lucid_recipe_drops_only_its_own_lock, {}),
    (zonda_requires_bypass_delay_and_retry_and_rejects_cfa_recipe, {}),
    (four_lucid_updates_capture_shadow_once_then_ack_and_relock, {}),
    (zonda_l_write_captures_alpha_and_settles_without_lucid_ack, {}),
    (pll_kind_masks_keep_zonda_alpha32_and_reject_lucid_handshakes, {}),
    (pll_q16_q32_frequency_arithmetic_and_bounded_vco_endpoints, {}),
    (standby_reset_and_cancel_do_not_stop_other_plls, {}),
    (late_dispatch_keeps_original_latch_and_relock_deadlines, {}),
    (all_33_rcgs_capture_shadow_once_at_their_deadline, {}),
    (every_declared_mux_routes_its_actual_parent, {}),
    (ten_mnd_rcgs_capture_cfg_and_raw_fraction_words_together, {}),
    (all_seven_mclks_replay_v2_fraction_vectors_and_cci_slow_integer_vectors, {}),
    (mode_zero_bypasses_retained_fraction_registers, {}),
    (undefined_mux_modes_and_bounded_mnd_inputs_never_ack, {}),
    (all_hid_fields_cover_bypass_integer_and_half_division, {}),
    (unknown_aux2_sleep_and_test_inputs_only_gain_explicit_fixture_rates, {}),
    (all_66_branches_have_independent_local_requests, {}),
    (sbi_readonly_divider_uses_explicit_fixture_and_generic_ceil, {}),
    (six_reset_lines_follow_binding_order_without_touching_camera_gdsc, {}),
    (read_only_feedback_bits_cannot_be_forged_or_erased, {}),
    (reserved_masks_narrow_accesses_and_unowned_words_reject_atomically, {}),
    (system_reset_cancels_all_work_and_preserves_external_clock_fixtures, {}),
    (fixture_types_ranges_and_connected_input_protection, {}),
    (real_rpmh_xo_votes_propagate_the_two_connected_input_clocks, {"rpmh": True}),
]


def default_profile_rejects_all_words_and_other_profiles_do_not_enable_it(args, out):
    for legacy in (False, True):
        r = CamRig(args, out / ("legacy" if legacy else "default"), reference=None, legacy=legacy)
        try:
            assert r.qom("enabled-reference") is False
            before = r.pure_snapshot()
            count = r.log_path.read_text(errors="replace").count(TYPE + ":")
            for offset in REGISTERS:
                r.read(offset)
                r.write(offset, 1)
            assert r.log_path.read_text(errors="replace").count(TYPE + ":") == count + 2 * len(REGISTERS)
            r.step(20_000)
            assert r.pure_snapshot() == before
        finally:
            r.close()


def paused_tcg_has_pure_readonly_observers_and_no_test_fixtures(args, out):
    r = CamRig(args, out / "tcg", accel="tcg")
    try:
        assert r.monitor("query-status")["running"] is False
        before = r.pure_snapshot(include_clock_periods=False)
        properties = r.monitor("qom-list", {"path": DEVICE})
        assert not any(p["name"].startswith("qtest-") for p in properties)
        for name in ["qtest-" + n + "-hz" for n in INPUTS] + ["qtest-sbi-divider"]:
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": 1})
            assert "error" in response and "not found" in response["error"]["desc"]
        assert r.pure_snapshot(include_clock_periods=False) == before
        assert r.cont_commands == r.device_mmio_operations == 0
        assert r.monitor("query-status")["running"] is False
    finally:
        r.close()


def sparse_74_windows_preserve_all_six_independent_camera_gdsc_owners(args, out):
    r = CamRig(args, out / "owners", camera=True)
    try:
        tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (r.directory / "mtree.txt").write_text(tree)
        # CAMCC's region names may add an offset suffix; their type prefix is
        # fixed. The independent camera names must never be absorbed here.
        found = {(int(a, 16) - CC, int(b, 16) - int(a, 16) + 1)
                 for a, b, name in re.findall(
                     r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ([^\s]+)", tree)
                 if name.startswith(TYPE)}
        assert found == set(WINDOWS), (sorted(found), WINDOWS)
        assert sum(size for _, size in found) == 944
        before = r.pure_snapshot()
        camera_before = r.camera_snapshot()
        for i, offset in enumerate(GDSC_OFFSETS):
            assert r.read(offset) == 1
            r.write(offset, 0x5801)
            assert r.read(offset) == 0x5801
            assert r.pure_snapshot() == before
            assert r.qom(f"domain{i}.control", CAMERA) == 0x5801
        configure_all(r)
        for i in range(6):
            assert r.qom(f"domain{i}.control", CAMERA) == 0x5801
            assert r.qom(f"domain{i}.powered", CAMERA) is False
        assert camera_before["globals"]["reference-identity"] == r.qom("reference-identity", CAMERA)
    finally:
        r.close()


def camcc_payload(snapshot):
    """Independent big-endian v1 wire oracle, with no output Clock payloads."""
    payload = bytearray(struct.pack(">11IQ", *(snapshot["globals"][n] for n in GLOBAL_FIELDS)))
    payload.extend(struct.pack(">236I", *(snapshot["registers"][f"{o:04x}"] for o in REGISTERS)))
    for field in ("active-l", "active-alpha", "pending-l", "pending-alpha"):
        payload.extend(struct.pack(">5I", *(p[field] for p in snapshot["plls"])))
    for field in (stage + "-" + word for stage in ("active", "pending")
                  for word in ("cfg", "m", "n", "d")):
        payload.extend(struct.pack(">33I", *(r[field] for r in snapshot["rcgs"])))
    for field in ("lock-deadline", "latch-deadline", "bypass-since"):
        payload.extend(struct.pack(">5q", *(p[field] for p in snapshot["plls"])))
    payload.extend(struct.pack(">33qQ", *(r["deadline"] for r in snapshot["rcgs"]),
                               snapshot["last-xo-hz"]))
    for field in ("locked", "ack", "calibrated"):
        assert all(type(p[field]) is bool for p in snapshot["plls"])
        payload.extend(bytes(p[field] for p in snapshot["plls"]))
    payload.extend(struct.pack(">5Q", *(snapshot["inputs"][name] for name in INPUTS)))
    assert len(payload) == VMSTATE_BYTES
    return bytes(payload)


def inspect_stream(path, snapshot, camera):
    data = path.read_bytes()
    sections = {}
    for label, name, expected in (("camcc", TYPE, camcc_payload(snapshot)),
                                  ("camera", "qcom-camera-gdsc", camera_payload(camera))):
        section = unique_section(data, name, 1, len(expected))
        start = section["payload_offset"]
        raw = data[start:start + len(expected)]
        assert raw == expected, (label, [i for i, (a, b) in enumerate(zip(raw, expected)) if a != b][:32])
        payload_file = path.with_name(path.name + f".{label}-v1.payload.bin")
        payload_file.write_bytes(raw)
        section.update(payload_file=payload_file.name, payload_sha256=digest(payload_file),
                       all_bytes_match_stopped_source_qom=True)
        sections[label] = section
    write_json(path.with_name(path.name + ".sections.json"), sections)
    return sections


def save_source(rig, path, witness):
    rig.raw_write(RAM, witness)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    snapshot, camera = rig.pure_snapshot(), rig.camera_snapshot()
    write_json(rig.directory / "source-before-save.json", {"camcc": snapshot, "camera": camera})
    rig.monitor("migrate", file_channels(path))
    sent = wait_migration(rig)
    assert sent["status"] == "completed"
    assert rig.monitor("query-status")["status"] == "postmigrate"
    assert rig.pure_snapshot() == snapshot and rig.camera_snapshot() == camera
    sections = inspect_stream(path, snapshot, camera)
    write_json(rig.directory / "source-query-migrate.json", sent)
    return {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
            "source_directory": rig.directory.name, "source_virtual_ns": rig.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "camera": camera, "sections": sections,
            "ram_witness": witness, "stream_modified": False}


def receive_source(rig, path, source):
    rig.set_clock(source["source_virtual_ns"])
    mmio_before, cont_before = rig.device_mmio_operations, rig.cont_commands
    rig.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(rig)
    assert received["status"] == "completed"
    assert rig.monitor("query-status")["running"] is False
    # The first inspection must be pure. No clock step, device MMIO or cont
    # can hide a post_load failure to publish clocks or discard stale state.
    first, camera = rig.pure_snapshot(), rig.camera_snapshot()
    assert first == source["snapshot"], (first, source["snapshot"])
    assert camera == source["camera"]
    assert (rig.device_mmio_operations, rig.cont_commands) == (mmio_before, cont_before)
    assert rig.raw_read(RAM) == source["ram_witness"]
    rig.reset_lines([bool(first["registers"][f"{offset:04x}"]) for offset in RESETS])
    evidence = {"snapshot": first, "camera": camera, "device_mmio_since_incoming": False,
                "qmp_cont_since_incoming": False, "guest_cpu_executed": False,
                "ram_witness": source["ram_witness"], "query_migrate": received}
    write_json(rig.directory / "destination-before-first-device-mmio.json", evidence)
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


def prewarm_target(rig, at_clock):
    """Leave different live outputs, requests, fixtures and pending deadlines."""
    # run_defaults uses 1000 ns bypass + 5000 ns lock + 33*1000 ns RCG.
    # Virtual clocks cannot run backwards: finish preparation exactly at
    # the source timestamp, with obsolete target deadlines still pending.
    assert at_clock >= 39_000
    rig.set_clock(at_clock - 39_000)
    configure_all(rig)
    rig.write(0x2000, rig.read(0x2000) | BYPASS)
    rig.step(1000)
    for i in range(5):
        rig.write(pll_reg(i, "opmode"), 1)
    rig.step(LOCK_NS)
    for i in range(5):
        # Incoming-defer keeps the virtual timer queue stopped. Preparatory
        # MMIO is intentional here; the post-load observations below never
        # use it to complete or publish an incoming request.
        rig.read(PLLS[i][0])
        assert rig.pll(i, "locked") is True
        output_on(rig, i)
    for i in range(33):
        configure_rcg(rig, i)
    assert rig.last_clock == at_clock
    rig.fixture("core_bi_pll_test_se", 77_000_003)
    rig.fixture("chip_sleep_clk", 12345)
    rig.fixture("cam_cc_pll2_out_aux2", 99_000_007)
    rig.monitor("qom-set", {"path": DEVICE, "property": "qtest-sbi-divider", "value": 7})
    for offset, *_rest in BRANCHES:
        rig.write(offset, 1)
    for offset in RESETS:
        rig.write(offset, 1)
    for i in LUCID:
        rig.write(PLLS[i][0] + 4, PLLS[i][3] + 3)
        rig.write(PLLS[i][0], rig.read(PLLS[i][0]) | UPDATE)
    rig.write(0x2008, 0x20000000)
    rig.write(0x2004, 0x60)
    for i in range(33):
        configure_rcg(rig, i, 7, 7, wait=False)
    for offset in GDSC_OFFSETS:
        rig.write(offset, 0x7801)
    return rig.pure_snapshot()


def migration_checks(args, out, report):
    rigs = []

    def new(name, **options):
        rig = CamRig(args, out / name, camera=True, **options)
        rigs.append(rig)
        return rig

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, source, target, saved, first, **details):
        record = {"name": name, "passed": True, "source_status": "completed",
                  "target_status": "completed", "snapshot_file": path.name,
                  "snapshot_sha256": digest(path), "stream_modified": False,
                  "source_directory": source.directory.name, "target_directory": target.directory.name,
                  "first_target_observation": first, "continued_after_restore": True,
                  "all_2579_payload_bytes_verified": True, "camera_194_bytes_verified": True,
                  **details}
        report["migrations"].append(record)
        passed(name, device_mmio_before_first_observation=False, **details)

    try:
        steady = new("migration-steady-source")
        run_defaults(steady)
        steady.fixture("core_bi_pll_test_se", 111_111_113)
        steady.fixture("chip_sleep_clk", 32768)
        steady.fixture("cam_cc_pll2_out_aux2", 222_222_223)
        for i, (_offset, _name, width, _parents) in enumerate(RCGS):
            configure_rcg(steady, i, 7, 3 + i % 5,
                          m=1 if width else 0, n=6 if width else 0)
        for i, (offset, *_rest) in enumerate(BRANCHES):
            steady.write(offset, i & 1)
        for i, offset in enumerate(RESETS):
            steady.write(offset, i & 1)
        steady.monitor("qom-set", {"path": DEVICE, "property": "qtest-sbi-divider", "value": 5})
        for i, offset in enumerate(GDSC_OFFSETS):
            steady.write(offset, ((i + 1) << 11) | 1)
        steady_path = out / "camcc-v1-steady.migration"
        steady_saved = save_source(steady, steady_path, 0x43414d31)
        report["migration_sources"].append(steady_saved)
        steady_target = new("migration-steady-target", incoming=True)
        old = prewarm_target(steady_target, steady_saved["source_virtual_ns"])
        assert old != steady_saved["snapshot"]
        first = receive_source(steady_target, steady_path, steady_saved)
        before_mmio = steady_target.device_mmio_operations
        steady_target.monitor("cont")
        steady_target.step(20_000)
        assert steady_target.pure_snapshot() == steady_saved["snapshot"]
        assert steady_target.device_mmio_operations == before_mmio
        # The incoming Clock object, rather than an old target fixture, owns
        # this rate. Changing it after restore must propagate without MMIO.
        steady_target.fixture("core_bi_pll_test_se", 99_000_001)
        for i, (_offset, name, width, _parents) in enumerate(RCGS):
            steady_target.expect_hz(name, divided(99_000_001, 3 + i % 5,
                                                1 if width else 0, 6 if width else 0))
        assert steady_target.camera_snapshot() == steady_saved["camera"]
        completed("v1_steady_clocks_registers_resets_and_camera_replace_stale_target",
                  steady_path, steady, steady_target, steady_saved, first,
                  stale_target_deadlines_discarded=True,
                  clock_fixture_changes_propagate_after_restore=True)

        locking = new("migration-five-locks-33-rcgs-source")
        locking.step(100_000)
        configure_all(locking)
        locking.fixture("core_bi_pll_test_se", 96_000_001)
        locking.write(0x2000, locking.read(0x2000) | BYPASS)
        locking.step(1000)
        for i in range(5):
            output_on(locking, i)
            locking.write(pll_reg(i, "opmode"), 1)
        lock_start = locking.last_clock
        for i, (_offset, _name, width, _parents) in enumerate(RCGS):
            configure_rcg(locking, i, 7, 3, m=1 if width else 0,
                          n=7 if width else 0, wait=False)
        locking.write(RESETS[2], 1)
        locking.step(400)
        assert all(locking.pll(i, "lock-deadline") == lock_start + LOCK_NS for i in range(5))
        assert all(locking.rcg(i, "deadline") == lock_start + RCG_NS for i in range(33))
        lock_path = out / "camcc-v1-five-locks-33-rcgs.migration"
        lock_saved = save_source(locking, lock_path, 0x43414d32)
        report["migration_sources"].append(lock_saved)
        lock_target = new("migration-five-locks-33-rcgs-target", incoming=True)
        # Old target requests remain pending at the same virtual epoch;
        # pre_load must cancel them before installing the source schedule.
        prewarm_target(lock_target, lock_saved["source_virtual_ns"])
        first = receive_source(lock_target, lock_path, lock_saved)
        before_mmio = lock_target.device_mmio_operations
        lock_target.monitor("cont")
        lock_target.step(599)
        assert all(lock_target.rcg(i, "pending") for i in range(33))
        assert all(not lock_target.pll(i, "locked") for i in range(5))
        lock_target.step(1)
        for i, (_offset, name, width, _parents) in enumerate(RCGS):
            assert not lock_target.rcg(i, "pending")
            lock_target.expect_hz(name, divided(96_000_001, 3,
                                              1 if width else 0, 7 if width else 0))
        lock_target.step(3999)
        assert all(not lock_target.pll(i, "locked") for i in range(5))
        lock_target.step(1)
        for i, row in enumerate(PLLS):
            assert lock_target.pll(i, "locked") is True
            lock_target.expect_hz(row[1], pll_rate(i))
        assert lock_target.device_mmio_operations == before_mmio
        assert lock_target.camera_snapshot() == lock_saved["camera"]
        completed("v1_five_locks_and_33_rcg_deadlines_fire_without_mmio_repair",
                  lock_path, locking, lock_target, lock_saved, first,
                  remaining_rcg_ns=600, remaining_pll_ns=4600,
                  device_mmio_before_deadline_checks=False)

        latching = new("migration-mixed-latches-source")
        run_defaults(latching)
        latching.fixture("core_bi_pll_test_se", 123_456_789)
        captured = {}
        for i in LUCID:
            base = PLLS[i][0]
            captured[i] = (PLLS[i][3] + 1, 0x2340 + i)
            latching.write(base + 4, captured[i][0])
            latching.write(base + 64, captured[i][1])
            latching.write(base, latching.read(base) | UPDATE)
            latching.write(base + 4, PLLS[i][3] + 4)
            latching.write(base + 64, 0x7890 + i)
            latching.write(base, latching.read(base) | UPDATE)
        captured[2] = (0x4c, 0x87654321)
        latching.write(0x2008, captured[2][1])
        latching.write(0x2004, captured[2][0])
        latching.write(0x2008, 0xfedcba98)
        latch_start = latching.last_clock
        for i in MND:
            offset = RCGS[i][0]
            configure_rcg(latching, i, 7, 14, m=5, n=7, wait=False)
            for delta, value in ((8, 1), (12, 0xfa), (16, 0xf9)):
                latching.write(offset + delta, value)
            latching.write(offset + 4, rcg_cfg(0, 1, 2))
            latching.write(offset, ROOT | BUSY)
        latching.write(RESETS[4], 1)
        latching.step(400)
        latch_path = out / "camcc-v1-lucid-zonda-mnd-latches.migration"
        latch_saved = save_source(latching, latch_path, 0x43414d33)
        report["migration_sources"].append(latch_saved)
        latch_target = new("migration-mixed-latches-target", incoming=True)
        prewarm_target(latch_target, latch_saved["source_virtual_ns"])
        first = receive_source(latch_target, latch_path, latch_saved)
        before_mmio = latch_target.device_mmio_operations
        latch_target.monitor("cont")
        latch_target.step(599)
        assert all(latch_target.pll(i, "latch-pending") for i in range(5))
        assert all(latch_target.rcg(i, "pending") for i in MND)
        for i in LUCID:
            latch_target.expect_hz(PLLS[i][1], pll_rate(i))
        latch_target.expect_hz("cam_cc_pll2", 0)
        latch_target.step(1)
        for i in LUCID:
            assert (latch_target.pll(i, "active-l"), latch_target.pll(i, "active-alpha")) == captured[i]
            assert latch_target.pll(i, "ack") is True
            assert latch_target.pll(i, "lock-deadline") == latch_start + 6000
            latch_target.expect_hz(PLLS[i][1], 0)
        assert latch_target.pll(2, "latch-pending") is True
        for i in MND:
            assert tuple(latch_target.rcg(i, "active-" + field) for field in ("m", "n", "d")) == (5, 0xfd, 0xf8)
            assert latch_target.rcg(i, "active-cfg") == rcg_cfg(7, 14, 2)
            assert latch_target.reg(RCGS[i][0] + 4) == rcg_cfg(0, 1, 2)
            latch_target.expect_hz(RCGS[i][1], divided(123_456_789, 14, 5, 7))
        latch_target.step(3999)
        assert latch_target.pll(2, "latch-pending") is True
        latch_target.step(1)
        assert (latch_target.pll(2, "active-l"), latch_target.pll(2, "active-alpha")) == captured[2]
        assert latch_target.pll(2, "lock-deadline") == latch_start + 10000
        assert latch_target.pll(2, "ack") is False
        latch_target.step(999)
        assert all(not latch_target.pll(i, "locked") for i in range(5))
        latch_target.step(1)
        for i in LUCID:
            latch_target.expect_hz(PLLS[i][1], pll_rate(i, *captured[i]))
        assert not latch_target.pll(2, "locked")
        latch_target.step(3999)
        assert not latch_target.pll(2, "locked")
        latch_target.step(1)
        latch_target.expect_hz("cam_cc_pll2", pll_rate(2, *captured[2]))
        assert latch_target.device_mmio_operations == before_mmio
        assert latch_target.camera_snapshot() == latch_saved["camera"]
        completed("v1_mixed_lucid_zonda_and_mnd_snapshots_keep_distinct_deadlines",
                  latch_path, latching, latch_target, latch_saved, first,
                  remaining_lucid_and_rcg_ns=600, remaining_zonda_settle_ns=4600,
                  remaining_zonda_lock_ns=9600, device_mmio_before_deadline_checks=False)

        disabled = new("migration-default-source", reference=None)
        disabled.fixture("chip_sleep_clk", 32768)
        disabled_path = out / "camcc-v1-default.migration"
        disabled_saved = save_source(disabled, disabled_path, 0x43414d30)
        report["migration_sources"].append(disabled_saved)
        for name, path, saved, enabled, diagnostic in (
                ("enabled_to_disabled", steady_path, steady_saved, False, "0 != 1"),
                ("disabled_to_enabled", disabled_path, disabled_saved, True, "1 != 0")):
            rejected = new("reject-" + name, incoming=True, reference=enabled)
            rejected.set_clock(saved["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["rejected_migrations"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": False, **details})
            passed("profile_" + name + "_rejected", **details)

        original = steady_path.read_bytes()
        section = steady_saved["sections"]["camcc"]
        # EQUAL base and a reserved stored divider bit exercise different
        # rejection layers. Both copies differ by one bit of one byte only.
        divider_index = REGISTERS.index(0x9010)
        for name, relative, width, old, xor, diagnostic in (
                ("physical_base", 44, 8, CC, 4, f"{CC:x} != {CC ^ 4:x}"),
                ("sbi_reserved_control", 52 + 4 * divider_index, 4, 5, 8,
                 TYPE + f": invalid migrated register {divider_index}")):
            absolute = section["payload_offset"] + relative
            assert int.from_bytes(original[absolute:absolute + width], "big") == old
            changed = bytearray(original)
            changed[absolute:absolute + width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, (a, b) in enumerate(zip(original, changed)) if a != b]
            assert differences == [absolute + width - 1]
            assert (original[differences[0]] ^ changed[differences[0]]).bit_count() == 1
            path = out / f"camcc-corrupt-{name}.migration"
            path.write_bytes(changed)
            rejected = new("reject-corrupt-" + name, incoming=True)
            rejected.set_clock(steady_saved["source_virtual_ns"])
            details = load_failure(rejected, path, diagnostic)
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": True,
                "field_offset": relative, "changed_byte_offsets": differences,
                "changed_bits": 1, "original_stream_unchanged": True, **details})
            passed("single_bit_" + name + "_rejected", **details)
        assert all(digest(out / saved["file"]) == saved["sha256"]
                   for saved in report["migration_sources"])
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
        "schema": "tb371fc-camcc-sm8250-v1", "suite": "camcc-sm8250-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "expected_unmodified_streams": 4,
        "expected_stream_files": 6, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "physical_base": CC, "resource_bytes": 65536, "register_count": 236,
        "sparse_window_count": 74, "mapped_bytes": 944,
        "output_clock_count": 111, "input_clock_count": 5,
        "connected_input_clock_count": 2, "unknown_external_input_count": 3,
        "pll_count": 5, "lucid_pll_count": 4, "zonda_pll_count": 1,
        "postdivider_count": 6, "rcg_count": 33, "mnd_rcg_count": 10,
        "mnd_width": 8, "branch_count": 66, "reset_count": 6,
        "read_only_divider_count": 1, "modeled_timer_count": 1,
        "camera_vmstate_version": 1, "camera_vmstate_payload_bytes": 194,
        "reference_enabled_by_default": False, "remote_vote_gpio_assumed": False,
        "postdivider_zero_assumed_divide_one": False, "sbi_divider_rounds_up": True,
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "source_sha256": {
            "camcc-kona.c": SOURCE_SHA256,
            "qcom,camcc-kona.h": BINDING_SHA256,
            "clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
            "clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
            "clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
            "clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
            "clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
            "clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2"},
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "camera_execution_observed": False, "analog_pll_behavior_modeled": False,
        "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP, source-derived digital protocols, "
                 "real Clock propagation and independent migration; virtual deadlines "
                 "are reference ordering assumptions, not analog measurements",
        "binary_unchanged": False, "checks": [], "migrations": [],
        "rejected_migrations": [], "corrupted_stream_rejections": [],
        "migration_sources": [], "actual_stream_files": [], "passed": False,
    }
    try:
        assert len(ORDINARY) == 31
        assert (len(REGISTERS), len(WINDOWS), len(OUTPUTS), len(INPUTS), len(PLLS),
                len(POSTDIVS), len(RCGS), len(MND), len(BRANCHES), len(RESETS)) == (
                    236, 74, 111, 5, 5, 6, 33, 10, 66, 6)
        for index, (test, options) in enumerate(ORDINARY):
            rig = None
            fixture = out / f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = CamRig(args, fixture, **options)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_profile_rejects_all_words_and_other_profiles_do_not_enable_it,
                     paused_tcg_has_pure_readonly_observers_and_no_test_fixtures,
                     sparse_74_windows_preserve_all_six_independent_camera_gdsc_owners):
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
                item["continued_after_restore"] and item["all_2579_payload_bytes_verified"] and
                item["camera_194_bytes_verified"] for item in report["migrations"]) and
            len(report["rejected_migrations"]) == len(report["corrupted_stream_rejections"]) == 2 and
            all(item["passed"] is True and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"] + report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 4 and
            len(report["actual_stream_files"]) == 6 and
            len({item["name"] for item in report["actual_stream_files"]}) == 6 and
            all(item["bytes"] > 0 for item in report["actual_stream_files"]) and
            report["binary_unchanged"])
    except BaseException as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
    report["duration_seconds"] = round(time.monotonic() - started, 6)
    write_json(out / "result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
