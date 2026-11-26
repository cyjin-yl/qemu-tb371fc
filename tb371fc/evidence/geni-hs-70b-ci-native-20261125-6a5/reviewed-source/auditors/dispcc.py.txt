#!/usr/bin/env python3
"""Read a CI ZIP and independently audit wide DISPCC migration evidence.

No extraction, network, guest execution, repository import or producer wire
serializer is used. The explicit decoder follows the reviewed C VMState field
order. Clock topology below is a fixed capture of the reviewed C descriptors.
Separate QMP/QTest logs have no common timestamp: the receipt distinguishes
direct channel ordering from the pinned harness/counter/epoch association.
"""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import traceback
import zipfile


CONTRACT = {'inputs': ['bi_tcxo', 'core_bi_pll_test_se', 'chip_sleep_clk', 'disp_cc_pll1_out_even', 'dp_link_clk_divsel_ten', 'dp_vco_divided_clk_src_mux', 'dptx1_phy_pll_link_clk', 'dptx1_phy_pll_vco_div_clk', 'dptx2_phy_pll_link_clk', 'dptx2_phy_pll_vco_div_clk', 'dsi0_phy_pll_out_byteclk', 'dsi1_phy_pll_out_byteclk', 'dsi0_phy_pll_out_dsiclk', 'dsi1_phy_pll_out_dsiclk', 'edp_phy_pll_link_clk', 'edp_phy_pll_vco_div_clk', 'disp_cc_xo_clk_parent'], 'outputs': ['disp_cc_mdss_ahb_clk', 'disp_cc_mdss_ahb_clk_src', 'disp_cc_mdss_byte0_clk', 'disp_cc_mdss_byte0_clk_src', 'disp_cc_mdss_byte0_div_clk_src', 'disp_cc_mdss_byte0_intf_clk', 'disp_cc_mdss_byte1_clk', 'disp_cc_mdss_byte1_clk_src', 'disp_cc_mdss_byte1_div_clk_src', 'disp_cc_mdss_byte1_intf_clk', 'disp_cc_mdss_dp_aux1_clk', 'disp_cc_mdss_dp_aux1_clk_src', 'disp_cc_mdss_dp_aux_clk', 'disp_cc_mdss_dp_aux_clk_src', 'disp_cc_mdss_dp_link1_clk', 'disp_cc_mdss_dp_link1_clk_src', 'disp_cc_mdss_dp_link1_div_clk_src', 'disp_cc_mdss_dp_link1_intf_clk', 'disp_cc_mdss_dp_link_clk', 'disp_cc_mdss_dp_link_clk_src', 'disp_cc_mdss_dp_link_div_clk_src', 'disp_cc_mdss_dp_link_intf_clk', 'disp_cc_mdss_dp_pixel1_clk', 'disp_cc_mdss_dp_pixel1_clk_src', 'disp_cc_mdss_dp_pixel2_clk', 'disp_cc_mdss_dp_pixel2_clk_src', 'disp_cc_mdss_dp_pixel_clk', 'disp_cc_mdss_dp_pixel_clk_src', 'disp_cc_mdss_edp_aux_clk', 'disp_cc_mdss_edp_aux_clk_src', 'disp_cc_mdss_edp_gtc_clk', 'disp_cc_mdss_edp_gtc_clk_src', 'disp_cc_mdss_edp_link_clk', 'disp_cc_mdss_edp_link_clk_src', 'disp_cc_mdss_edp_link_div_clk_src', 'disp_cc_mdss_edp_link_intf_clk', 'disp_cc_mdss_edp_pixel_clk', 'disp_cc_mdss_edp_pixel_clk_src', 'disp_cc_mdss_esc0_clk', 'disp_cc_mdss_esc0_clk_src', 'disp_cc_mdss_esc1_clk', 'disp_cc_mdss_esc1_clk_src', 'disp_cc_mdss_mdp_clk', 'disp_cc_mdss_mdp_clk_src', 'disp_cc_mdss_mdp_lut_clk', 'disp_cc_mdss_non_gdsc_ahb_clk', 'disp_cc_mdss_pclk0_clk', 'disp_cc_mdss_pclk0_clk_src', 'disp_cc_mdss_pclk1_clk', 'disp_cc_mdss_pclk1_clk_src', 'disp_cc_mdss_rot_clk', 'disp_cc_mdss_rot_clk_src', 'disp_cc_mdss_rscc_ahb_clk', 'disp_cc_mdss_rscc_vsync_clk', 'disp_cc_mdss_vsync_clk', 'disp_cc_mdss_vsync_clk_src', 'disp_cc_pll0', 'disp_cc_pll1', 'disp_cc_sleep_clk', 'disp_cc_sleep_clk_src', 'disp_cc_xo_clk'], 'registers': [[0, 12582919, 2818572288, 12582919, 'REG_PLL', 0, 0], [4, 65535, 0, 65535, 'REG_PLL', 0, 1], [8, 65535, 0, 65535, 'REG_PLL', 0, 2], [12, 7, 0, 7, 'REG_PLL', 0, 3], [16, 4294967295, 0, 4294967295, 'REG_PLL', 0, 4], [20, 4294967295, 0, 4294967295, 'REG_PLL', 0, 5], [24, 4294967295, 0, 4294967295, 'REG_PLL', 0, 6], [28, 4294967295, 0, 4294967295, 'REG_PLL', 0, 7], [32, 4294967295, 0, 4294967295, 'REG_PLL', 0, 8], [36, 4294967295, 0, 4294967295, 'REG_PLL', 0, 9], [40, 4294967295, 0, 4294967295, 'REG_PLL', 0, 10], [44, 4294967295, 0, 4294967295, 'REG_PLL', 0, 11], [56, 1, 0, 1, 'REG_PLL', 0, 12], [64, 65535, 0, 65535, 'REG_PLL', 0, 13], [4096, 12582919, 2818572288, 12582919, 'REG_PLL', 1, 0], [4100, 65535, 0, 65535, 'REG_PLL', 1, 1], [4104, 65535, 0, 65535, 'REG_PLL', 1, 2], [4108, 7, 0, 7, 'REG_PLL', 1, 3], [4112, 4294967295, 0, 4294967295, 'REG_PLL', 1, 4], [4116, 4294967295, 0, 4294967295, 'REG_PLL', 1, 5], [4120, 4294967295, 0, 4294967295, 'REG_PLL', 1, 6], [4124, 4294967295, 0, 4294967295, 'REG_PLL', 1, 7], [4128, 4294967295, 0, 4294967295, 'REG_PLL', 1, 8], [4132, 4294967295, 0, 4294967295, 'REG_PLL', 1, 9], [4136, 4294967295, 0, 4294967295, 'REG_PLL', 1, 10], [4140, 4294967295, 0, 4294967295, 'REG_PLL', 1, 11], [4152, 1, 0, 1, 'REG_PLL', 1, 12], [4160, 65535, 0, 65535, 'REG_PLL', 1, 13], [8192, 1, 0, 1, 'REG_RESET', 0, 0], [8196, 1, 2147483648, 1, 'REG_BRANCH', 24, 0], [8200, 1, 2147483648, 1, 'REG_BRANCH', 25, 0], [8204, 1, 2147483648, 1, 'REG_BRANCH', 21, 0], [8212, 1, 2147483648, 1, 'REG_BRANCH', 26, 0], [8220, 1, 2147483648, 1, 'REG_BRANCH', 22, 0], [8228, 1, 2147483648, 1, 'REG_BRANCH', 29, 0], [8232, 1, 2147483648, 1, 'REG_BRANCH', 1, 0], [8236, 1, 2147483648, 1, 'REG_BRANCH', 2, 0], [8240, 1, 2147483648, 1, 'REG_BRANCH', 3, 0], [8244, 1, 2147483648, 1, 'REG_BRANCH', 4, 0], [8248, 1, 2147483648, 1, 'REG_BRANCH', 19, 0], [8252, 1, 2147483648, 1, 'REG_BRANCH', 20, 0], [8256, 1, 2147483648, 1, 'REG_BRANCH', 9, 0], [8260, 1, 2147483648, 1, 'REG_BRANCH', 10, 0], [8268, 1, 2147483648, 1, 'REG_BRANCH', 13, 0], [8272, 1, 2147483648, 1, 'REG_BRANCH', 11, 0], [8276, 1, 2147483648, 1, 'REG_BRANCH', 6, 0], [8280, 1, 2147483648, 1, 'REG_BRANCH', 12, 0], [8284, 1, 2147483648, 1, 'REG_BRANCH', 7, 0], [8288, 1, 2147483648, 1, 'REG_BRANCH', 8, 0], [8296, 1, 2147483648, 1, 'REG_BRANCH', 5, 0], [8300, 1, 2147483648, 1, 'REG_BRANCH', 18, 0], [8304, 1, 2147483648, 1, 'REG_BRANCH', 16, 0], [8308, 1, 2147483648, 1, 'REG_BRANCH', 17, 0], [8312, 1, 2147483648, 1, 'REG_BRANCH', 14, 0], [8316, 1, 2147483648, 1, 'REG_BRANCH', 15, 0], [8320, 1, 2147483648, 1, 'REG_BRANCH', 0, 0], [8344, 3, 2147483648, 3, 'REG_RCG', 17, 0], [8348, 14111, 0, 14111, 'REG_RCG', 17, 1], [8352, 255, 0, 255, 'REG_RCG', 17, 2], [8356, 255, 0, 255, 'REG_RCG', 17, 3], [8360, 255, 0, 255, 'REG_RCG', 17, 4], [8368, 3, 2147483648, 3, 'REG_RCG', 18, 0], [8372, 14111, 0, 14111, 'REG_RCG', 18, 1], [8376, 255, 0, 255, 'REG_RCG', 18, 2], [8380, 255, 0, 255, 'REG_RCG', 18, 3], [8384, 255, 0, 255, 'REG_RCG', 18, 4], [8392, 3, 2147483648, 3, 'REG_RCG', 16, 0], [8396, 1062687, 0, 1062687, 'REG_RCG', 16, 1], [8416, 3, 2147483648, 3, 'REG_RCG', 19, 0], [8420, 1062687, 0, 1062687, 'REG_RCG', 19, 1], [8440, 3, 2147483648, 3, 'REG_RCG', 20, 0], [8444, 14111, 0, 14111, 'REG_RCG', 20, 1], [8464, 3, 2147483648, 3, 'REG_RCG', 1, 0], [8468, 14111, 0, 14111, 'REG_RCG', 1, 1], [8488, 3, 0, 3, 'REG_DIVIDER', 0, 0], [8492, 3, 2147483648, 3, 'REG_RCG', 2, 0], [8496, 14111, 0, 14111, 'REG_RCG', 2, 1], [8516, 3, 0, 3, 'REG_DIVIDER', 1, 0], [8520, 3, 2147483648, 3, 'REG_RCG', 14, 0], [8524, 14111, 0, 14111, 'REG_RCG', 14, 1], [8544, 3, 2147483648, 3, 'REG_RCG', 15, 0], [8548, 14111, 0, 14111, 'REG_RCG', 15, 1], [8568, 3, 2147483648, 3, 'REG_RCG', 6, 0], [8572, 14111, 0, 14111, 'REG_RCG', 6, 1], [8592, 0, 0, 3, 'REG_DIVIDER', 3, 0], [8620, 3, 2147483648, 3, 'REG_RCG', 9, 0], [8624, 14111, 0, 14111, 'REG_RCG', 9, 1], [8628, 65535, 0, 65535, 'REG_RCG', 9, 2], [8632, 65535, 0, 65535, 'REG_RCG', 9, 3], [8636, 65535, 0, 65535, 'REG_RCG', 9, 4], [8644, 3, 2147483648, 3, 'REG_RCG', 7, 0], [8648, 14111, 0, 14111, 'REG_RCG', 7, 1], [8652, 65535, 0, 65535, 'REG_RCG', 7, 2], [8656, 65535, 0, 65535, 'REG_RCG', 7, 3], [8660, 65535, 0, 65535, 'REG_RCG', 7, 4], [8668, 3, 2147483648, 3, 'REG_RCG', 4, 0], [8672, 14111, 0, 14111, 'REG_RCG', 4, 1], [8692, 3, 2147483648, 3, 'REG_RCG', 8, 0], [8696, 14111, 0, 14111, 'REG_RCG', 8, 1], [8700, 65535, 0, 65535, 'REG_RCG', 8, 2], [8704, 65535, 0, 65535, 'REG_RCG', 8, 3], [8708, 65535, 0, 65535, 'REG_RCG', 8, 4], [8716, 3, 2147483648, 3, 'REG_RCG', 5, 0], [8720, 14111, 0, 14111, 'REG_RCG', 5, 1], [8740, 0, 0, 3, 'REG_DIVIDER', 2, 0], [8768, 3, 2147483648, 3, 'REG_RCG', 3, 0], [8772, 14111, 0, 14111, 'REG_RCG', 3, 1], [8792, 3, 2147483648, 3, 'REG_RCG', 13, 0], [8796, 14111, 0, 14111, 'REG_RCG', 13, 1], [8800, 65535, 0, 65535, 'REG_RCG', 13, 2], [8804, 65535, 0, 65535, 'REG_RCG', 13, 3], [8808, 65535, 0, 65535, 'REG_RCG', 13, 4], [8816, 3, 2147483648, 3, 'REG_RCG', 12, 0], [8820, 14111, 0, 14111, 'REG_RCG', 12, 1], [8840, 0, 0, 3, 'REG_DIVIDER', 4, 0], [8844, 3, 2147483648, 3, 'REG_RCG', 10, 0], [8848, 14111, 0, 14111, 'REG_RCG', 10, 1], [8868, 3, 2147483648, 3, 'REG_RCG', 11, 0], [8872, 14111, 0, 14111, 'REG_RCG', 11, 1], [8892, 3, 2147483648, 3, 'REG_RCG', 0, 0], [8896, 1062687, 0, 1062687, 'REG_RCG', 0, 1], [16384, 1, 0, 1, 'REG_RESET', 1, 0], [16388, 1, 2147483648, 1, 'REG_BRANCH', 23, 0], [16392, 1, 2147483648, 1, 'REG_BRANCH', 28, 0], [16396, 1, 2147483648, 1, 'REG_BRANCH', 27, 0], [24668, 1, 2147483648, 1, 'REG_BRANCH', 31, 0], [24672, 3, 2147483648, 3, 'REG_RCG', 21, 0], [24676, 14111, 0, 14111, 'REG_RCG', 21, 1], [24696, 1, 2147483648, 1, 'REG_BRANCH', 30, 0], [32768, 16, 0, 16, 'REG_MISC', 0, 0]], 'plls': [[56, [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]], [57, [14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27]]], 'rcgs': [[1, 0, 1062687, [119, 120, -1, -1, -1], [-1, -127, -127, -127, 57, -4, -127, -2]], [3, 0, 14111, [72, 73, -1, -1, -1], [-1, -11, -12, -127, -127, -127, -127, -2]], [7, 0, 14111, [75, 76, -1, -1, -1], [-1, -11, -12, -127, -127, -127, -127, -2]], [11, 0, 14111, [105, 106, -1, -1, -1], [-1, -127, -127, -127, -127, -127, -127, -2]], [13, 0, 14111, [95, 96, -1, -1, -1], [-1, -127, -127, -127, -127, -127, -127, -2]], [15, 0, 14111, [102, 103, -1, -1, -1], [-1, -5, -6, -7, -8, -9, -10, -2]], [19, 0, 14111, [82, 83, -1, -1, -1], [-1, -5, -6, -7, -8, -9, -10, -2]], [23, 16, 14111, [90, 91, 92, 93, 94], [-1, -5, -6, -7, -8, -9, -10, -2]], [25, 16, 14111, [97, 98, 99, 100, 101], [-1, -5, -6, -7, -8, -9, -10, -2]], [27, 16, 14111, [85, 86, 87, 88, 89], [-1, -5, -6, -7, -8, -9, -10, -2]], [29, 0, 14111, [115, 116, -1, -1, -1], [-1, -127, -127, -127, -127, -127, -127, -2]], [31, 0, 14111, [117, 118, -1, -1, -1], [-1, -127, -127, -127, 57, -4, -127, -2]], [33, 0, 14111, [112, 113, -1, -1, -1], [-1, -15, -16, -127, -127, -127, -127, -2]], [37, 16, 14111, [107, 108, 109, 110, 111], [-1, -15, -16, -127, -127, -127, -127, -2]], [39, 0, 14111, [78, 79, -1, -1, -1], [-1, -11, -12, -127, -127, -127, -127, -2]], [41, 0, 14111, [80, 81, -1, -1, -1], [-1, -11, -12, -127, -127, -127, -127, -2]], [43, 0, 1062687, [66, 67, -1, -1, -1], [-1, 56, -127, -127, 57, -4, -127, -2]], [47, 8, 14111, [56, 57, 58, 59, 60], [-1, -13, -14, -127, -127, -127, -127, -2]], [49, 8, 14111, [61, 62, 63, 64, 65], [-1, -13, -14, -127, -127, -127, -127, -2]], [51, 0, 1062687, [68, 69, -1, -1, -1], [-1, 56, -127, -127, 57, -4, -127, -2]], [55, 0, 14111, [70, 71, -1, -1, -1], [-1, -127, -127, -127, -127, -127, -127, -2]], [59, 0, 14111, [126, 127, -1, -1, -1], [-3, -127, -127, -127, -127, -127, -127, -2]]], 'branches': [[0, 55, 1, -1], [2, 35, 3, -1], [5, 36, 4, -1], [6, 37, 7, -1], [9, 38, 8, -1], [10, 49, 11, -1], [12, 45, 13, -1], [14, 47, 15, -1], [17, 48, 16, -1], [18, 41, 19, -1], [21, 42, 20, -1], [22, 44, 23, -1], [24, 46, 25, -1], [26, 43, 27, -1], [28, 53, 29, -1], [30, 54, 31, -1], [32, 51, 33, -1], [35, 52, 34, -1], [36, 50, 37, -1], [38, 39, 39, -1], [40, 40, 41, -1], [42, 31, 43, -1], [44, 33, 43, 0], [45, 122, 1, 1], [46, 29, 47, -1], [48, 30, 49, -1], [50, 32, 51, -1], [52, 124, 1, -1], [53, 123, 55, -1], [54, 34, 55, -1], [58, 128, 59, -1], [60, 125, -17, -1]], 'dividers': [[4, 74, 3, 0, 2, False], [8, 77, 7, 0, 2, False], [16, 104, 15, 0, 2, True], [20, 84, 19, 0, 2, True], [34, 114, 33, 0, 2, True]], 'test_sha256': 'c626ec6135958a43b81bca435bf8ddb1532dc4323f07dee1c9afb6e682d9217e', 'reviewed_source_sha256': {'hw/misc/qcom-sm8250-dispcc.c': 'cf10f13e3cf47b750896704c045bc3ed17cd0887ee2ddf708f9e4f3ae7e70c79', 'tb371fc/tests/test_dispcc_sm8250.py': 'c626ec6135958a43b81bca435bf8ddb1532dc4323f07dee1c9afb6e682d9217e', 'hw/misc/qcom-sm8250-dispcc-data.inc': 'a910182ea5c2957e918b02764f910ca61ac58aa8c24b3b259606192e9bac8732'}}
TYPE = "qcom-sm8250-dispcc"
DEVICE = "/machine/dispcc"
MDSS = "/machine/mdss-gdsc"
CC = 0x0AF00000
RAM = 0x80010000
UNIT = 1_000_000_000 << 32
GLOBALS = (
    "reference-profile", "reference-identity", "register-count", "clock-count",
    "input-count", "pll-count", "rcg-count", "branch-count", "divider-count",
    "reset-count", "remote-vote-count", "window-count", "mapped-bytes",
    "physical-base",
)
PLL_FIELDS = (
    "active-l", "active-alpha", "pending-l", "pending-alpha", "locked", "ack",
    "calibrated", "lock-pending", "latch-pending", "lock-deadline", "latch-deadline",
)
RCG_FIELDS = tuple(stage + "-" + word for stage in ("active", "pending")
                   for word in ("cfg", "m", "n", "d")) + ("pending", "deadline")
GDSC_GLOBALS = (
    "enabled-profile", "reference-identity", "domain-count", "register-bytes",
    "mapped-bytes", "parent-resource-index", "cold-control", "control-write-mask",
    "power-status-mask",
)
GDSC_FIELDS = (
    "name", "address", "dt-flags", "timeout-us", "control", "request-known",
    "requested", "state-known", "powered", "hw-request-known", "hw-request",
)
ORIGINALS = (
    "dispcc-v1-steady.migration", "dispcc-v1-two-locks-22-rcgs.migration",
    "dispcc-v1-two-latches-six-mnd.migration", "dispcc-v1-default.migration",
)
MIGRATION_NAMES = (
    "v1_steady_clocks_dividers_votes_and_mdss_replace_stale_target",
    "v1_two_locks_and_22_rcg_deadlines_resume_without_mmio_repair",
    "v1_two_pll_latches_and_mixed_width_mnd_keep_captured_state",
)


class AuditError(RuntimeError):
    pass


def need(condition, message):
    if not condition:
        raise AuditError(message)


def eq(actual, expected, message):
    need(type(actual) is type(expected), f"{message}: distinct value types")
    if isinstance(expected, dict):
        need(actual.keys() == expected.keys(), f"{message}: distinct dictionary fields")
        for key, value in expected.items():
            eq(actual[key], value, message + "." + str(key))
    elif isinstance(expected, (list, tuple)):
        need(len(actual) == len(expected), f"{message}: distinct sequence lengths")
        for index, (left, right) in enumerate(zip(actual, expected)):
            eq(left, right, message + f"[{index}]")
    else:
        need(actual == expected,
             f"{message}: actual={str(actual)[:350]} expected={str(expected)[:350]}")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        need(key not in result, f"Duplicate JSON key {key}")
        result[key] = value
    return result


def json_value(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda s: (_ for _ in ()).throw(AuditError(s)))


def safe_name(name):
    need(type(name) is str and name and "\\" not in name, "Invalid member path")
    path = PurePosixPath(name)
    need(not path.is_absolute() and all(p not in ("", ".", "..") for p in path.parts),
         f"Unsafe relative path {name}")
    need(str(path) == name.rstrip("/"), f"Noncanonical path {name}")
    return name.rstrip("/")


class Archive:
    def __init__(self, path):
        self.path = path
        self.before = path.stat()
        self.zip = zipfile.ZipFile(path)
        self.members, self.reads = {}, {}
        self.manifest, self.prefix = None, None
        for entry in self.zip.infolist():
            name = safe_name(entry.filename)
            need(name not in self.members, f"Duplicate ZIP member {name}")
            need(not stat.S_ISLNK(entry.external_attr >> 16), f"Symlink member {name}")
            self.members[name] = entry
        need(len(self.members) < 200000, "Unreasonable member count")

    def find(self, suffix):
        matches = [n for n, e in self.members.items() if not e.is_dir() and
                   (n == suffix or n.endswith("/" + suffix))]
        need(len(matches) == 1, f"Expected unique {suffix}: {matches}")
        return matches[0]

    def note(self, name, size, digest):
        if self.prefix and name.startswith(self.prefix + "/") and not name.endswith("/sha256.json"):
            key = name[len(self.prefix) + 1:]
            eq(self.manifest.get(key), digest, f"SHA inventory {name}")
        self.reads[name] = {"bytes": size, "sha256": digest}

    def read(self, name, maximum=64 * 1024 * 1024):
        need(name in self.members and not self.members[name].is_dir(), f"Missing {name}")
        need(self.members[name].file_size <= maximum, f"Member too large {name}")
        raw = self.zip.read(self.members[name])
        eq(len(raw), self.members[name].file_size, f"Member length {name}")
        self.note(name, len(raw), sha(raw))
        return raw

    def hash(self, name):
        h, size = hashlib.sha256(), 0
        with self.zip.open(self.members[name]) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(chunk)
                size += len(chunk)
        eq(size, self.members[name].file_size, f"Member length {name}")
        self.note(name, size, h.hexdigest())
        return h.hexdigest()

    def json(self, name):
        return json_value(self.read(name).decode("utf-8"))

    def text(self, name):
        return self.read(name).decode("utf-8")

    def unchanged(self):
        after = self.path.stat()
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
        eq(tuple(getattr(after, f) for f in fields),
           tuple(getattr(self.before, f) for f in fields), "Input ZIP changed during audit")


class Wire:
    """Cursor decoder: every byte is consumed once, with no reserialization."""
    def __init__(self, data):
        self.data, self.offset = data, 0

    def take(self, code, count=1):
        size = struct.calcsize(">" + code) * count
        need(self.offset + size <= len(self.data), "Short migration payload")
        values = struct.unpack_from(">" + str(count) + code, self.data, self.offset)
        self.offset += size
        return list(values)

    def one(self, code):
        return self.take(code)[0]

    def boolean(self):
        value = self.one("B")
        need(value in (0, 1), "Noncanonical VMState boolean")
        return bool(value)

    def done(self, size):
        eq(len(self.data), size, "Payload length")
        eq(self.offset, size, "Complete cursor coverage")


def locate_section(stream, name, length):
    need(stream[:8] == b"QEVM\x00\x00\x00\x03", "Migration magic/version")
    label = name.encode("ascii")
    needle = bytes((len(label),)) + label + struct.pack(">II", 0, 1)
    found, offset = [], 0
    while True:
        offset = stream.find(needle, offset)
        if offset < 0:
            break
        header = offset - 5
        if header >= 8 and stream[header] in (1, 4):
            found.append((header, offset + len(needle)))
        offset += 1
    need(len(found) == 1, f"Missing or duplicate {name} section")
    header, start = found[0]
    identity = struct.unpack_from(">I", stream, header + 1)[0]
    footer = b"\x7e" + struct.pack(">I", identity)
    eq(stream[start + length:start + length + 5], footer, f"Exact {name} footer")
    return {"name": name, "version": 1, "instance_id": 0,
            "header_offset": header, "section_id": identity,
            "payload_offset": start, "payload_bytes": length,
            "footer_offset": start + length, "footer_hex": footer.hex()}, stream[start:start + length]


def hz(period):
    return UNIT // period if period else 0


def derive_rates(snapshot):
    """Independent integer topology walk using decoded fields and fixed data."""
    result = [0] * 61
    if snapshot["globals"]["reference-profile"] == 0:
        return result
    regs = [snapshot["registers"][f"{r[0]:04x}"] for r in CONTRACT["registers"]]
    input_rates = [hz(snapshot["inputs"][name]) for name in CONTRACT["inputs"]]
    xo = input_rates[0]
    for i, (clock, indices) in enumerate(CONTRACT["plls"]):
        values = [regs[j] for j in indices]
        state = snapshot["plls"][i]
        l, alpha = state["active-l"], state["active-alpha"]
        numerator = (l << 16) + alpha
        valid = (0 < xo <= 2_000_000_500 and 0 < l <= 65535 and alpha <= 65535 and
                 numerator <= ((2_000_000_000 << 16) + xo - 1) // xo)
        rate = xo * numerator // 65536 if valid else 0
        recipe = (values[2] == 0x44 and values[4] == 0x805 and values[5] == 0 and
                  values[6:9] == [0x20485699, 0x2261, 0x029A699C] and
                  values[9:12] == [0, 0, 0])
        if (state["locked"] and values[0] & 4 and values[0] & 1 and values[3] == 7 and
                values[12] == 1 and recipe and 249_600_000 <= rate <= 2_000_000_500):
            result[clock] = rate

    def parent_rate(parent):
        return 0 if parent == -127 else input_rates[-parent - 1] if parent < 0 else result[parent]

    requests = [bool(regs[reg] & 1 or vote >= 0 and snapshot["remote-votes"][vote])
                for _clock, reg, _parent, vote in CONTRACT["branches"]]
    demand = {clock for clock, _width, _mask, indices, _parents in CONTRACT["rcgs"]
              if regs[indices[0]] & 2}
    demand.update(row[2] for row, requested in zip(CONTRACT["branches"], requests)
                  if requested and row[2] >= 0)
    for clock, _reg, parent, _shift, _width, _readonly in CONTRACT["dividers"]:
        if clock in demand:
            demand.add(parent)
    for row, state in zip(CONTRACT["rcgs"], snapshot["rcgs"]):
        clock, width, cfg_mask, _indices, parents = row
        cfg, m, n, d = (state["active-" + word] for word in ("cfg", "m", "n", "d"))
        mask, mode = (1 << width) - 1, (cfg >> 12) & 3
        parent = parents[(cfg >> 8) & 7]
        denominator = ((~n) & mask) + m
        valid = not (cfg & ~cfg_mask or (m | n | d) & ~mask or parent == -127)
        if mode:
            valid = valid and mode == 2 and width > 0 and 0 < m < denominator <= mask and d == (~denominator & mask)
        if clock not in demand or not valid:
            continue
        rate = parent_rate(parent)
        hid = cfg & 31
        rate = rate * 2 // (hid + 1) if hid else rate
        result[clock] = rate * m // denominator if mode == 2 else rate
    for clock, reg, parent, shift, width, _readonly in CONTRACT["dividers"]:
        divisor = 1 + ((regs[reg] >> shift) & ((1 << width) - 1))
        result[clock] = (result[parent] + divisor - 1) // divisor
    for row, requested in zip(CONTRACT["branches"], requests):
        if requested:
            result[row[0]] = parent_rate(row[2])
    return result


def decode_dispcc(payload):
    w = Wire(payload)
    globals_ = dict(zip(GLOBALS[:-1], w.take("I", 13)))
    globals_["physical-base"] = w.one("Q")
    expected = [globals_["reference-profile"], 0x44495331, 130, 61, 17, 2, 22, 32, 5, 2, 2, 39, 520, CC]
    need(expected[0] in (0, 1), "Invalid reference profile")
    eq(list(globals_.values()), expected, "DISPCC wire identity/counts")
    regs = w.take("I", 130)
    for value, descriptor in zip(regs, CONTRACT["registers"]):
        need(value & ~descriptor[3] == 0, "Reserved register bits in unmodified source")
    plls = [{}, {}]
    for field in ("active-l", "active-alpha", "pending-l", "pending-alpha"):
        for state, value in zip(plls, w.take("I", 2)):
            state[field] = value
    rcgs = [{} for _ in range(22)]
    for stage in ("active", "pending"):
        for word in ("cfg", "m", "n", "d"):
            for state, value in zip(rcgs, w.take("I", 22)):
                state[stage + "-" + word] = value
    for field in ("lock-deadline", "latch-deadline"):
        for state, value in zip(plls, w.take("q", 2)):
            state[field] = value
    for state, value in zip(rcgs, w.take("q", 22)):
        state["deadline"], state["pending"] = value, value >= 0
    last_xo = w.one("Q")
    for field in ("locked", "ack", "calibrated"):
        for state in plls:
            state[field] = w.boolean()
    votes = [w.boolean(), w.boolean()]
    eq(w.offset, 1540, "Independent input-array wire offset")
    inputs = dict(zip(CONTRACT["inputs"], w.take("Q", 17)))
    w.done(1676)
    eq(last_xo, hz(inputs["bi_tcxo"]), "Saved input XO observation")
    for state in plls:
        state["lock-pending"] = state["lock-deadline"] >= 0
        state["latch-pending"] = state["latch-deadline"] >= 0
    snapshot = {
        "globals": globals_,
        "registers": {f"{row[0]:04x}": value for row, value in zip(CONTRACT["registers"], regs)},
        "plls": plls, "rcgs": rcgs, "last-xo-hz": last_xo,
        "dividers": [(regs[row[1]] >> row[3]) & ((1 << row[4]) - 1) for row in CONTRACT["dividers"]],
        "remote-votes": votes, "inputs": inputs,
    }
    rates = derive_rates(snapshot)
    snapshot["output_hz"] = dict(zip(CONTRACT["outputs"], rates))
    snapshot["outputs"] = {name: UNIT // rate if rate else 0 for name, rate in zip(CONTRACT["outputs"], rates)}
    return snapshot


def decode_mdss(payload):
    w = Wire(payload)
    globals_ = dict(zip(GDSC_GLOBALS, w.take("I", 9)))
    expected = [globals_["enabled-profile"], 0x4D445331, 1, 4, 4, 3, 1, 0xF803, 0x80000000]
    need(expected[0] in (0, 1), "MDSS reference profile")
    eq(list(globals_.values()), expected, "MDSS wire identity/counts")
    parent_known, parent_on = w.boolean(), w.boolean()
    eq(w.one("I"), 0, "MDSS domain ID")
    address = w.one("Q")
    flags, timeout, control = w.take("I", 3)
    eq((address, flags, timeout), (0x0AF03000, 3, 100), "MDSS static domain")
    need(not control & ~0xF803, "Invalid MDSS control bits")
    hw_known, hw_request = w.boolean(), w.boolean()
    w.done(64)
    request_known = not control & 2 or hw_known
    requested = hw_known and hw_request if control & 2 else not control & 1
    enabled = bool(globals_["enabled-profile"])
    known = enabled and ((parent_known and not parent_on) or
                         (request_known and not requested) or (parent_known and request_known))
    powered = enabled and parent_known and parent_on and request_known and requested
    domain = dict(zip(GDSC_FIELDS, ("mdss-core", address, flags, timeout, control,
                                   bool(request_known), bool(requested), bool(known), bool(powered),
                                   hw_known, hw_request)))
    return {"enabled-reference": enabled, "globals": globals_, "parent-known": parent_known,
            "parent-on": parent_on, "domains": [domain]}


def qmp_calls(a, directory):
    calls, pending, previous_id = [], None, 0
    for number, line in enumerate(a.text(directory + "/qmp.jsonl").splitlines(), 1):
        record = json_value(line)
        need(type(record) is dict and len(record) == 1, "Malformed raw QMP record")
        if "send" in record:
            need(pending is None, "Overlapping QMP requests")
            request = record["send"]
            eq(request.get("id"), previous_id + 1, "Monotonic QMP request ID")
            previous_id += 1
            pending = {"request": request, "response": None, "line": number}
            calls.append(pending)
        elif "receive" in record:
            response = record["receive"]
            need(type(response) is dict, "Malformed QMP response")
            if "QMP" in response or "event" in response:
                continue
            need(pending is not None, "QMP response without request")
            eq(response.get("id"), pending["request"]["id"], "QMP response ID")
            pending["response"], pending = response, None
        else:
            raise AuditError("Unknown QMP record")
    return calls


def execute(call):
    return call["request"]["execute"]


def response(call):
    value = call["response"]
    need(type(value) is dict and "return" in value and "error" not in value, "No successful QMP response")
    return value["return"]


def qtest_calls(a, directory):
    calls, pending = [], None
    for line in a.text(directory + "/qtest.jsonl").splitlines():
        record = json_value(line)
        if "send" in record:
            need(pending is None and type(record["send"]) is str, "Malformed QTest send")
            need(type(record.get("virtual_ns_before")) is int and record["virtual_ns_before"] >= 0,
                 "Missing QTest virtual epoch")
            pending = {"command": record["send"], "before": record["virtual_ns_before"], "reply": None}
            calls.append(pending)
        elif "receive" in record:
            value = record["receive"]
            need(type(value) is str, "Malformed QTest reply")
            if value.startswith("IRQ "):
                continue
            need(pending is not None and (value == "OK" or value.startswith("OK ")), "Failed QTest operation")
            pending["reply"], pending = value, None
        else:
            raise AuditError("Unknown QTest record")
    need(pending is None, "Truncated QTest log")
    return calls


def mmio(command):
    pieces = command.split()
    if pieces[0] not in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq"):
        return False
    address = int(pieces[1], 0)
    camera = (0xAD07004, 0xAD08004, 0xAD09004, 0xAD0A004, 0xAD0B004, 0xAD0C144)
    return (CC <= address < CC + 0x20000 or 0x100000 <= address < 0x2F0000 or
            0x18200000 <= address < 0x18230000 or any(b <= address < b + 4 for b in camera))


def snapshot_queries(snapshot, mdss):
    queries = []
    def add(path, key, value):
        queries.append(({"path": path, "property": key}, value))
    for key in GLOBALS:
        add(DEVICE, key, snapshot["globals"][key])
    for reg in CONTRACT["registers"]:
        name = f"{reg[0]:04x}"
        add(DEVICE, "reg." + name, snapshot["registers"][name])
    for i in range(2):
        for key in PLL_FIELDS:
            add(DEVICE, f"pll{i}-" + key, snapshot["plls"][i][key])
    for i in range(22):
        for key in RCG_FIELDS:
            add(DEVICE, f"rcg{i}-" + key, snapshot["rcgs"][i][key])
    add(DEVICE, "last-xo-hz", snapshot["last-xo-hz"])
    for i, value in enumerate(snapshot["dividers"]):
        add(DEVICE, f"divider{i}-value", value)
    for i, value in enumerate(snapshot["remote-votes"]):
        add(DEVICE, f"remote-vote{i}", value)
    for name in CONTRACT["outputs"]:
        add(DEVICE, "clk." + name + ".hz", snapshot["output_hz"][name])
    for group in ("outputs", "inputs"):
        for name in CONTRACT[group]:
            add(DEVICE + "/" + name, "qtest-clock-period", snapshot[group][name])
    add(MDSS, "enabled-reference", mdss["enabled-reference"])
    for key in GDSC_GLOBALS:
        add(MDSS, key, mdss["globals"][key])
    for key in ("parent-known", "parent-on"):
        add(MDSS, key, mdss[key])
    for key in GDSC_FIELDS:
        add(MDSS, "domain0." + key, mdss["domains"][0][key])
    eq(len(queries), 556, "Independent complete pure-getter count")
    return queries


def verify_queries(calls, expected):
    eq(len(calls), len(expected), "Complete pure observation length")
    for i, (call, (arguments, wanted)) in enumerate(zip(calls, expected)):
        eq(execute(call), "qom-get", f"Pure observation command {i}")
        eq(call["request"].get("arguments"), arguments, f"QOM property {i}")
        eq(response(call), wanted, f"Actual QOM value {i}")


def migration_argument(call, filename):
    channels = call["request"].get("arguments", {}).get("channels")
    need(type(channels) is list and len(channels) == 1, "Missing migration file channel")
    eq(channels[0].get("channel-type"), "main", "Migration channel")
    address = channels[0].get("addr", {})
    eq(address.get("transport"), "file", "Migration transport")
    eq(PurePosixPath(address.get("filename", "")).name, filename, "Actual migration filename")


def audit_source_trace(a, root, saved, snapshot, mdss):
    directory = root + "/" + safe_name(saved["source_directory"])
    eq(a.json(directory + "/source-before-save.json"), {"dispcc": snapshot, "mdss": mdss},
       "Retained stopped source snapshot")
    calls = qmp_calls(a, directory)
    migrations = [i for i, call in enumerate(calls) if execute(call) == "migrate"]
    eq(len(migrations), 1, "Source migrate count")
    end = migrations[0]
    migration_argument(calls[end], saved["file"])
    stops = [i for i, call in enumerate(calls[:end]) if execute(call) == "stop"]
    need(stops, "Missing source stop")
    first = stops[-1] + 1
    eq(execute(calls[first]), "query-status", "Stopped status probe")
    eq(response(calls[first]).get("running"), False, "Source paused")
    expected = snapshot_queries(snapshot, mdss)
    verify_queries(calls[first + 1:end], expected)
    sent = a.json(directory + "/source-query-migrate.json")
    eq(sent, saved["source_query_migrate"], "Retained source completion")
    eq(sent.get("status"), "completed", "Source status")
    need(any(execute(c) == "query-migrate" and response(c) == sent for c in calls[end + 1:]),
         "Actual source completion response absent")
    need(any(execute(c) == "query-status" and response(c).get("status") == "postmigrate"
             for c in calls[end + 1:]), "Actual source postmigrate response absent")
    commands = qtest_calls(a, directory)
    writes = [c for c in commands if c["command"].split()[:2] == ["writel", hex(RAM)]]
    need(writes, "Missing source RAM witness write")
    eq(int(writes[-1]["command"].split()[2], 0), saved["ram_witness"], "Source RAM witness")
    return {"actual_stopped_qom_getters": len(expected), "actual_source_completed": True}


def audit_target_trace(a, root, saved, completed, snapshot, mdss):
    directory = root + "/" + safe_name(completed["target_directory"])
    first = a.json(directory + "/destination-before-first-device-mmio.json")
    eq(first, completed["first_target_observation"], "First target receipt versus report")
    eq(first["snapshot"], snapshot, "First target snapshot versus full wire decoder")
    eq(first["mdss"], mdss, "First target MDSS versus full wire decoder")
    for field in ("device_mmio_since_incoming", "qmp_cont_since_incoming", "guest_cpu_executed"):
        eq(first.get(field), False, "First target " + field)
    before = first["device_mmio_count_before_incoming"]
    need(type(before) is int and before > 0, "Missing stale-target MMIO counter")
    eq(first["device_mmio_count_after_first_observation"], before, "Persisted MMIO counter pair")
    eq(first["qmp_cont_count_before_incoming"], 0, "Target pre-incoming cont counter")
    eq(first["qmp_cont_count_after_first_observation"], 0, "Target first-observation cont counter")
    calls = qmp_calls(a, directory)
    incoming = [i for i, c in enumerate(calls) if execute(c) == "migrate-incoming"]
    eq(len(incoming), 1, "Actual target incoming count")
    start = incoming[0]
    migration_argument(calls[start], saved["file"])
    getters = [i for i in range(start + 1, len(calls)) if execute(calls[i]) == "qom-get"]
    need(getters, "No first target pure observation")
    index = getters[0]
    wait = calls[start + 1:index]
    need(wait and all(execute(c) in ("query-migrate", "query-status") for c in wait),
         "Target mutating QMP before first observation")
    eq(first["query_migrate"].get("status"), "completed", "Target completion receipt")
    need(any(execute(c) == "query-migrate" and response(c) == first["query_migrate"] for c in wait),
         "Actual target completion response absent")
    eq(execute(wait[-1]), "query-status", "Actual target pause confirmation")
    eq(response(wait[-1]).get("running"), False, "Actual target paused")
    expected = snapshot_queries(snapshot, mdss)
    verify_queries(calls[index:index + len(expected)], expected)
    eq(sum(execute(c) == "cont" for c in calls[:index + len(expected)]), 0,
       "Recounted QMP cont before complete observation")
    need(any(execute(c) == "cont" for c in calls[index + len(expected):]), "No post-observation continuation")
    commands = qtest_calls(a, directory)
    marker = [i for i, c in enumerate(commands) if c["command"] == f"clock_set {saved['source_virtual_ns']}"]
    eq(len(marker), 1, "Unique incoming QTest epoch marker")
    mark = marker[0]
    eq(sum(mmio(c["command"]) for c in commands[:mark]), before, "Independently recounted stale-target MMIO")
    need(len(commands) > mark + 3, "Missing post-incoming witness/drain/step sequence")
    for call in commands[mark + 1:mark + 3]:
        eq(call["command"], "readl " + hex(RAM), "Only RAM access at first observation boundary")
        eq(int(call["reply"].split()[1], 0), saved["ram_witness"], "Actual restored RAM witness")
        eq(call["before"], saved["source_virtual_ns"], "Restored virtual epoch")
    eq(commands[mark + 3]["command"],
       "clock_step 20000" if saved["file"] == ORIGINALS[0] else "clock_step 599",
       "First virtual advance follows complete pure observation")
    return {"actual_first_qom_getters": len(expected), "prewarm_device_mmio_recounted": before,
            "mmio_between_epoch_and_witness_drain": 0, "cont_before_full_first_observation": 0,
            "persisted_counter_pair_verified": True, "raw_ram_witness_verified": True,
            "shared_cross_channel_timestamp_available": False,
            "cross_channel_association": "reviewed test hash, persisted equal counters, exact QMP ordering and QTest epoch/witness sequence"}


def audit_report(a, root, report, binary_hash):
    expected = {
        "schema": "tb371fc-dispcc-sm8250-v1", "suite": "dispcc-sm8250-reference",
        "expected_checks": 42, "expected_completed_migrations": 3,
        "expected_rejected_migrations": 2, "expected_corrupted_stream_rejections": 3,
        "expected_unmodified_streams": 4, "expected_stream_files": 7,
        "vmstate_version": 1, "vmstate_minimum_version": 1, "vmstate_payload_bytes": 1676,
        "reference_identity": 0x44495331, "physical_base": CC, "resource_bytes": 131072,
        "register_count": 130, "sparse_window_count": 39, "mapped_bytes": 520,
        "output_clock_count": 61, "input_clock_count": 17, "mdss_vmstate_payload_bytes": 64,
        "reference_enabled_by_default": False, "generic_dividers_round_up": True,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "display_execution_observed": False, "analog_pll_behavior_modeled": False,
        "android_boot_passed": False, "binary_unchanged": True, "passed": True,
        "test_script_sha256": CONTRACT["test_sha256"],
        "qemu_sha256_before": binary_hash, "qemu_sha256_after": binary_hash,
    }
    for key, value in expected.items():
        eq(report.get(key), value, "Report " + key)
    for key, count in (("checks", 42), ("migrations", 3), ("rejected_migrations", 2),
                       ("corrupted_stream_rejections", 3), ("migration_sources", 4), ("actual_stream_files", 7)):
        need(type(report.get(key)) is list, "Missing report list " + key)
        eq(len(report[key]), count, "Report length " + key)
    names = [row.get("name") for row in report["checks"]]
    eq(len(set(names)), 42, "Unique checks")
    for row in report["checks"]:
        eq(row.get("passed"), True, "Failed check " + str(row.get("name")))
    eq([row["file"] for row in report["migration_sources"]], list(ORIGINALS), "Original stream inventory")
    eq([row["name"] for row in report["migrations"]], list(MIGRATION_NAMES), "Actual migration identities")
    stream_members = {PurePosixPath(name).name: name for name in a.members
                      if str(PurePosixPath(name).parent) == root and name.endswith(".migration")}
    eq(len(stream_members), 7, "Actual ZIP stream count")
    actual = {name: a.read(member) for name, member in stream_members.items()}
    eq({row["name"] for row in report["actual_stream_files"]}, set(actual), "Reported stream filenames")
    for row in report["actual_stream_files"]:
        eq(row["sha256"], sha(actual[row["name"]]), "Actual stream SHA")
        eq(row["bytes"], len(actual[row["name"]]), "Actual stream length")
    source_receipts, snapshots, sections = [], {}, {}
    for source in report["migration_sources"]:
        filename = source["file"]
        raw = actual[filename]
        eq(source["sha256"], sha(raw), "Source stream actual SHA")
        eq(source["bytes"], len(raw), "Source stream actual bytes")
        eq(source["stream_modified"], False, "Source stream modification flag")
        cc_section, cc_bytes = locate_section(raw, TYPE, 1676)
        mdss_section, mdss_bytes = locate_section(raw, "qcom-mdss-gdsc", 64)
        decoded, mdss = decode_dispcc(cc_bytes), decode_mdss(mdss_bytes)
        eq(source["snapshot"], decoded, "All DISPCC serialized and derived fields versus stopped source")
        eq(source["mdss"], mdss, "All MDSS serialized and derived fields versus stopped source")
        stored_sections = a.json(root + "/" + filename + ".sections.json")
        eq(stored_sections, source["sections"], "Sections receipt versus source report")
        for label, found, payload in (("dispcc", cc_section, cc_bytes), ("mdss", mdss_section, mdss_bytes)):
            declared = stored_sections[label]
            for key, value in found.items():
                if key in declared:
                    eq(declared[key], value, "Actual section " + key)
            eq(declared["payload_offset"], found["payload_offset"], "Actual section start")
            eq(declared["payload_sha256"], sha(payload), "Payload SHA")
            eq(a.read(root + "/" + safe_name(declared["payload_file"])), payload, "Retained payload bytes")
            eq(declared["all_bytes_match_stopped_source_qom"], True, "Complete byte comparison claim")
        trace = audit_source_trace(a, root, source, decoded, mdss)
        snapshots[filename], sections[filename] = (decoded, mdss), (cc_section, mdss_section)
        detail = {"file": filename, "sha256": sha(raw), "wire_bytes_decoded": 1676,
                  "mdss_bytes_decoded": 64, "output_clocks_recomputed": 61, "source_trace": trace}
        matching = [m for m in report["migrations"] if m["snapshot_file"] == filename]
        if filename != ORIGINALS[3]:
            eq(len(matching), 1, "Unique completed target for source")
            target = matching[0]
            for key in ("passed", "continued_after_restore", "all_1676_payload_bytes_verified", "mdss_64_bytes_verified"):
                eq(target.get(key), True, "Completed migration " + key)
            eq(target["source_status"], "completed", "Completed source")
            eq(target["target_status"], "completed", "Completed target")
            eq(target["snapshot_sha256"], sha(raw), "Completed target input hash")
            eq(target["stream_modified"], False, "Completed target stream intact")
            detail["target_trace"] = audit_target_trace(a, root, source, target, decoded, mdss)
        source_receipts.append(detail)

    steady, steady_mdss = snapshots[ORIGINALS[0]]
    wide = []
    for i, period in enumerate((954_437_176, 858_993_459)):
        input_name, clock = f"dsi{i}_phy_pll_out_dsiclk", f"disp_cc_mdss_pclk{i}_clk_src"
        eq(steady["inputs"][input_name], period, "Wide saved input period")
        eq(steady["output_hz"][clock], hz(period), "Wide independently derived pixel rate")
        need(steady["output_hz"][clock] > 0xFFFFFFFF, "Pixel clock truncated to 32 bits")
        eq(steady["outputs"][clock], UNIT // hz(period), "Wide actual output Clock period")
        wide.append({"input": input_name, "input_period": period, "clock": clock,
                     "clock_hz": steady["output_hz"][clock], "clock_period": steady["outputs"][clock]})

    originals = actual[ORIGINALS[0]]
    base_offset = sections[ORIGINALS[0]][0]["payload_offset"]
    misc_index = [row[0] for row in CONTRACT["registers"]].index(0x8000)
    negatives = []
    negative_contract = [
        (report["rejected_migrations"][0], "enabled_to_disabled", ORIGINALS[0], "0 != 1", None),
        (report["rejected_migrations"][1], "disabled_to_enabled", ORIGINALS[3], "1 != 0", None),
        (report["corrupted_stream_rejections"][0], "physical_base", "dispcc-corrupt-physical_base.migration",
         f"{CC:x} != {CC ^ 4:x}", (52, 8, CC, 4)),
        (report["corrupted_stream_rejections"][1], "misc_reserved_control", "dispcc-corrupt-misc_reserved_control.migration",
         TYPE + f": invalid migrated register {misc_index}", (60 + 4 * misc_index, 4, 0x10, 1)),
        (report["corrupted_stream_rejections"][2], "dsi1_pixel_period_below_5ghz_boundary",
         "dispcc-corrupt-dsi1_pixel_period_below_5ghz_boundary.migration",
         TYPE + ": invalid migrated input clock 13", (1644, 8, 0x33333333, 1)),
    ]
    for row, name, filename, diagnostic, mutation in negative_contract:
        eq(row["name"], name, "Negative identity")
        eq(row["snapshot_file"], filename, "Negative filename")
        eq(row["snapshot_sha256"], sha(actual[filename]), "Negative actual SHA")
        eq(row["stream_modified"], mutation is not None, "Negative modified flag")
        eq(row["passed"], True, "Negative verdict")
        eq(row["destination_exit"], 1, "Receiver exit recorded by pinned harness")
        eq(row["specific_load_failure_observed"], True, "Specific load failure claim")
        eq(row["expected_diagnostic"], diagnostic, "Negative diagnostic")
        directory = root + "/" + safe_name(row["target_directory"])
        error = a.text(directory + "/qemu.stderr")
        need(all(value in error for value in (TYPE, diagnostic, "load of migration failed", "error while loading state")),
             "Missing specific actual receiver error")
        incoming = [c for c in qmp_calls(a, directory) if execute(c) == "migrate-incoming"]
        eq(len(incoming), 1, "Negative actual incoming request")
        migration_argument(incoming[0], filename)
        evidence = {"name": name, "actual_specific_load_error": True, "recorded_receiver_exit": 1}
        if mutation:
            changed = actual[filename]
            eq(len(changed), len(originals), "Corrupt stream same length")
            positions = [i for i, (old, new) in enumerate(zip(originals, changed)) if old != new]
            eq(len(positions), 1, "Exactly one changed stream byte")
            eq((originals[positions[0]] ^ changed[positions[0]]).bit_count(), 1, "Exactly one changed stream bit")
            offset, width, old, flip = mutation
            absolute = base_offset + offset
            eq(positions, [absolute + width - 1], "Actual corrupt byte location")
            eq(int.from_bytes(originals[absolute:absolute + width], "big"), old, "Original field")
            eq(int.from_bytes(changed[absolute:absolute + width], "big"), old ^ flip, "Corrupt field")
            eq(row["field_offset"], offset, "Reported corrupt offset")
            eq(row["changed_byte_offsets"], positions, "Reported changed bytes")
            eq(row["changed_bits"], 1, "Reported changed bits")
            eq(row["original_stream_unchanged"], True, "Original retained unchanged")
            locate_section(changed, TYPE, 1676)
            _, mdss_bytes = locate_section(changed, "qcom-mdss-gdsc", 64)
            eq(decode_mdss(mdss_bytes), steady_mdss, "MDSS unaffected by DISPCC bit flip")
            evidence.update(field_offset=offset, changed_bits=1, original_value=old,
                            changed_value=old ^ flip, actual_byte_offset=positions[0])
        negatives.append(evidence)
    return {"checks": 42, "completed_migrations": 3, "profile_rejections": 2,
            "single_bit_rejections": 3, "original_streams": 4, "total_streams": 7,
            "source_receipts": source_receipts, "wide_pixel_evidence": wide, "rejections": negatives,
            "all_1676_dispcc_and_64_mdss_bytes_independently_decoded": True,
            "all_61_source_and_first_target_clocks_recomputed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--expected-zip-sha256", required=True)
    parser.add_argument("--expected-qemu-sha256", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    need(not args.out.exists(), "Audit output must be a new file")
    need(args.out.resolve() != args.zip.resolve(), "Audit output cannot overwrite ZIP")
    result = {"schema": "tb371fc-dispcc-wide-independent-zip-audit-v1", "passed": False,
              "expected_head": args.expected_head, "auditor_sha256": file_hash(Path(__file__)),
              "reviewed_source_sha256": CONTRACT["reviewed_source_sha256"],
              "read_only_zip": True, "extracted_members": 0, "qemu_executed": False,
              "guest_executed": False, "native_android_boot_claimed": False}
    archive = None
    try:
        need(re.fullmatch(r"[0-9a-f]{40}", args.expected_head), "Expected complete Git commit SHA")
        for value in (args.expected_zip_sha256, args.expected_qemu_sha256):
            need(re.fullmatch(r"[0-9a-f]{64}", value), "Expected complete SHA256 digest")
        eq(file_hash(args.zip), args.expected_zip_sha256, "Independent expected ZIP digest")
        archive = Archive(args.zip)
        result["zip"] = {"path": str(args.zip.resolve()), "bytes": args.zip.stat().st_size,
                         "sha256": args.expected_zip_sha256, "members": len(archive.members)}
        eq(archive.text("tb371fc-results/source-commit.txt").strip(), args.expected_head, "Retained source commit")
        binary = archive.find("qemu-system-aarch64")
        eq(archive.hash(binary), args.expected_qemu_sha256, "Actual ZIP QEMU binary digest")
        with archive.zip.open(archive.members[binary]) as stream:
            eq(stream.read(4), b"\x7fELF", "Actual ELF magic")
        result["qemu"] = {"member": binary, **archive.reads[binary]}
        aggregate_path = archive.find("components/result.json")
        prefix = str(PurePosixPath(aggregate_path).parent)
        manifest = archive.json(prefix + "/sha256.json")
        need(type(manifest) is dict and manifest, "Missing component SHA inventory")
        archive.prefix, archive.manifest = prefix, manifest
        aggregate = archive.json(aggregate_path)
        eq(aggregate.get("source_commit"), args.expected_head, "Aggregate source binding")
        eq(aggregate.get("source_dirty"), False, "Aggregate clean source")
        eq(aggregate.get("qemu_sha256"), args.expected_qemu_sha256, "Aggregate QEMU binding")
        eq(aggregate.get("qemu_unchanged"), True, "Aggregate QEMU immutability")
        gates = aggregate.get("gates")
        need(type(gates) is list, "Missing aggregate gates")
        selected = [(i, g) for i, g in enumerate(gates) if g.get("name") == "dispcc-sm8250-reference"]
        eq(len(selected), 1, "Unique DISPCC gate")
        index, gate = selected[0]
        gate_root = f"{prefix}/{index:02d}-dispcc-sm8250-reference"
        eq(archive.json(gate_root + "/case.json"), gate, "Gate versus aggregate case")
        eq(gate.get("exit_code"), 0, "Actual DISPCC process exit")
        eq(gate.get("passed"), True, "DISPCC process verdict")
        need(not any(key in gate for key in ("error", "timed_out", "artifact_error")), "Incomplete DISPCC gate")
        plan = archive.json(prefix + "/plan.json")
        eq(plan[index].get("name"), "dispcc-sm8250-reference", "Registered gate")
        eq(plan[index].get("expected_count"), 42, "Registered check count")
        eq(gate.get("command"), plan[index].get("command"), "Registered versus executed command")
        for name in ("stdout.log", "stderr.log"):
            eq(sha(archive.read(gate_root + "/" + name)), gate["log_sha256"][name], "Actual process log digest")
        fixture = gate["fixture"].rstrip("/") + "/"
        result_path = plan[index]["result"]
        need(result_path.startswith(fixture), "Unrelated fixture result path")
        report_path = gate_root + "/fixtures/" + safe_name(result_path[len(fixture):])
        report = archive.json(report_path)
        result["dispcc"] = audit_report(archive, str(PurePosixPath(report_path).parent), report,
                                        args.expected_qemu_sha256)
        archive.unchanged()
        eq(file_hash(args.zip), args.expected_zip_sha256, "ZIP digest after audit")
        result["verified_members"] = archive.reads
        result["passed"] = True
    except Exception as error:
        result["error"], result["traceback"] = str(error), traceback.format_exc()
        if archive:
            result["verified_members_before_failure"] = archive.reads
    finally:
        if archive:
            archive.zip.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"passed": result["passed"], "receipt": str(args.out), "error": result.get("error")}, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
