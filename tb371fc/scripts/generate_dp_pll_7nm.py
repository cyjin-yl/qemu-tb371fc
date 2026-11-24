#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate exact Kona DP 7 nm tables from seventeen complete pinned inputs.

This parser selects one reviewed non-V2 driver and its explicitly supported
shared TX words. Register masks and cold zero storage are reference policies,
not a silicon reset-value or undocumented reserved-bit specification.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
REPOSITORY = "OnePlusOSS/android_kernel_oneplus_sm8250"
SOURCE_HASHES = {
    "arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi": "d275792039d00a135a3f809e49954196c62f1e68202702597fe9d93fb5484118",
    "arch/arm64/boot/dts/vendor/qcom/kona-sde.dtsi": "1247f63e2538357a0ca825064ad3b44ce1e0bfa17d45b13f90ca9e5f7b88f352",
    "include/dt-bindings/clock/mdss-7nm-pll-clk.h": "51632c1a73cfce8c4915e5e5c56f46b0fddae983d3e0ac44cd2731c488105d82",
    "include/dt-bindings/clock/qcom,dispcc-kona.h": "a624fe5555fd4eddc10a46a80ff3a52da66ddb56f81567c7ea4df591f5949ac2",
    "include/dt-bindings/clock/qcom,gcc-kona.h": "ec7b2a775a7f96a543c064c7fc733fb83ffa7e3dae3c4bb517200627ae5f7fed",
    "include/dt-bindings/clock/qcom,rpmh.h": "9b8ad7915183052c15a1acd4b227099904120639d885e46ea80a5c4be4d13136",
    "include/linux/usb/usbpd.h": "6128e1eeaaac1e4ffe13d2ec1a2a6a50c2dda9184346edb8e21e4b1fc39ec957",
    "techpack/display/msm/dp/dp_catalog_v420.c": "6c68e49ac69d52b1394e4c64344734f3312ff5cccda04f35eefba04dbc3dddc3",
    "techpack/display/msm/dp/dp_reg.h": "9f359d5c920644fc573fe9478be62e3ae0ecc197daf0c81efbb7a886836f8cab",
    "techpack/display/pll/Makefile": "83598d0660b38fb30979ca52a17e2ac2ece01573ca49ed9a86ea01cd3624bcf0",
    "techpack/display/pll/dp_pll.h": "f9d9cae58e7720bd4415d1c33d3645850ce3d3c5dba2adf835d6d9acd07f4a1c",
    "techpack/display/pll/dp_pll_7nm.c": "b8c40f85bb9ee966b905434e91225c70e27ecd945a5ea2495cc7604219d483a1",
    "techpack/display/pll/dp_pll_7nm.h": "7c95097365330c9aad6999ec62e9b15ee20957ca76f94f599e6fc5be615705fd",
    "techpack/display/pll/dp_pll_7nm_util.c": "1135de217c3ec633a4539565e699baa86fdbe452e48e444cd3660b5cab981532",
    "techpack/display/pll/pll_drv.c": "14652116c7ea1ff9c5380fe5441087c910d57b890d52aec3a2a031b76b250c56",
    "techpack/display/pll/pll_drv.h": "ba1df695799b9c4b078fcde5c6cfa3bddf35ed026f818f4fe372cf89d3221feb",
    "techpack/display/pll/pll_util.c": "652450b062d1a957a2db5b83c46244e11168974e259506f0c9a3e5b959f2e49a",
}
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-dp-pll-data.inc",
           "tb371fc/docs/dp-pll-7nm-registers.json")
PLL_SOURCE = "techpack/display/pll/dp_pll_7nm.c"
UTIL_SOURCE = "techpack/display/pll/dp_pll_7nm_util.c"
CATALOG_SOURCE = "techpack/display/msm/dp/dp_catalog_v420.c"
REG_SOURCE = "techpack/display/msm/dp/dp_reg.h"
REGIONS = ("PLL", "PHY", "TX0", "TX1")
REGION_BASES = (0x088ea000, 0x088eaa00, 0x088ea200, 0x088ea600)
RATE_NUMBERS = (1620000, 2700000, 5400000, 8100000)
RATE_SYMBOLS = ("DP_VCO_HSCLK_RATE_1620MHZDIV1000",
                "DP_VCO_HSCLK_RATE_2700MHZDIV1000",
                "DP_VCO_HSCLK_RATE_5400MHZDIV1000",
                "DP_VCO_HSCLK_RATE_8100MHZDIV1000")
# Explicit source-supported storage, not a blanket interpretation of dp_reg.h.
EXTRA_TX = ("TXn_TX_EMP_POST1_LVL", "TXn_TX_DRV_LVL_V420",
            "TXn_TRANSCEIVER_BIAS_EN_V420", "TXn_HIGHZ_DRVR_EN_V420",
            "TXn_TX_POL_INV_V420")
STATUS = {"QSERDES_COM_CMN_STATUS": (0, 0x140, "PLL_STATUS", 0x3),
          "QSERDES_COM_C_READY_STATUS": (0, 0x178, "PLL_STATUS", 0x1),
          "DP_PHY_STATUS": (1, 0xdc, "PHY_STATUS", 0x3)}


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def function(source, name):
    match = re.search(r"^(?:static\s+)?[^\n;{}]*\b" + re.escape(name) +
                      r"\s*\([^;{}]*\)\s*\{", source, re.M)
    require(match is not None, "Missing reviewed function: " + name)
    start = match.end() - 1
    depth, end = 1, start + 1
    while end < len(source) and depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    require(depth == 0, "Unterminated function: " + name)
    return source[start + 1:end - 1], start + 1


def definitions(source):
    result = {}
    for match in re.finditer(r"^#define\s+(\w+)\s+\(?(0x[0-9a-fA-F]+|\d+)"
                            r"(?:UL)?\)?\s*$", source, re.M):
        name, value = match.groups()
        require(name not in result, "Duplicate literal definition: " + name)
        result[name] = {"value": int(value, 0),
                        "line": source[:match.start()].count("\n") + 1}
    return result


def calls(source, function_name=None):
    body, start = function(source, function_name) if function_name else (source, 0)
    rows = []
    for match in re.finditer(r"MDSS_PLL_REG_([RW])\s*\(\s*dp_res->(\w+)"
                            r"\s*,\s*(\w+)(?:\s*,\s*([^;]*?))?\s*\)", body):
        op, base, name, expression = match.groups()
        require((op == "R") == (expression is None),
                "Unexpected PLL read/write macro form")
        rows.append({"operation": op, "base": base, "register": name,
                     "expression": (re.sub(r"\s+", " ", expression.strip())
                                    if expression is not None else None),
                     "line": source[:start + match.start()].count("\n") + 1})
    return rows


def node(source, label):
    match = re.search(r"\b" + re.escape(label) + r":\s*[^{]+\{", source)
    require(match is not None, "Missing DTS node: " + label)
    depth, end = 1, match.end()
    while end < len(source) and depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    require(depth == 0, "Unterminated DTS node")
    return source[match.end():end - 1]


def resources(body):
    reg = re.search(r"\breg\s*=\s*(.*?);", body, re.S)
    names = re.search(r"\breg-names\s*=\s*(.*?);", body, re.S)
    require(reg and names, "Missing named DTS resources")
    nums = [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", reg.group(1))]
    labels = re.findall(r'"([^"]+)"', names.group(1))
    require(len(nums) == 2 * len(labels), "DTS resources are not address/size pairs")
    require(len(set(labels)) == len(labels), "Duplicate DTS resource")
    return {name: {"address": nums[2 * i], "bytes": nums[2 * i + 1]}
            for i, name in enumerate(labels)}


def parse_context(sources):
    pll_node = node(sources["arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi"],
                    "mdss_dp_pll")
    display_node = node(sources["arch/arm64/boot/dts/vendor/qcom/kona-sde.dtsi"],
                        "sde_dp")
    require('compatible = "qcom,mdss_dp_pll_7nm";' in pll_node,
            "The reviewed DP PLL is the non-V2 interface")
    require(re.search(r"qcom,phy-version\s*=\s*<0x420>", display_node),
            "Shared TX support requires the reviewed v420 catalog")
    pll, display = resources(pll_node), resources(display_node)
    expected = {
        "pll_base": (0x088ea000, 0x200), "phy_base": (0x088eaa00, 0x200),
        "ln_tx0_base": (0x088ea200, 0x200), "ln_tx0_tran_base": (0x088ea2b8, 8),
        "ln_tx0_vmode_base": (0x088ea2e8, 4),
        "ln_tx1_base": (0x088ea600, 0x200), "ln_tx1_tran_base": (0x088ea6b8, 8),
        "ln_tx1_vmode_base": (0x088ea6e8, 4), "gdsc_base": (0x0af03000, 8),
    }
    require({k: (v["address"], v["bytes"]) for k, v in pll.items()} == expected,
            "PLL named resources changed")
    aliases = []
    for name, canonical in (("dp_phy", "phy_base"), ("dp_ln_tx0", "ln_tx0_base"),
                            ("dp_ln_tx1", "ln_tx1_base")):
        require(display[name] == pll[canonical], "Display/PLL shared resource differs")
        aliases.append({"display_name": name, "pll_name": canonical, **display[name]})
    require(display["dp_pll"] == {"address": 0x088ea040, "bytes": 0x10},
            "The display PLL subresource is +0x40, length 0x10")
    require(display["usb3_dp_com"] == {"address": 0x088e8000, "bytes": 0x20},
            "USB combo context differs")
    dp_defs = definitions(sources[REG_SOURCE])
    pll_defs = definitions(sources[UTIL_SOURCE])
    require(display["dp_pll"]["address"] +
            dp_defs["QSERDES_COM_BIAS_EN_CLKBUFLR_EN"]["value"] ==
            pll["pll_base"]["address"] +
            pll_defs["QSERDES_COM_BIAS_EN_CLKBUFLR_EN"]["value"] == 0x088ea044,
            "The two BIAS offsets must address one canonical word")
    aliases.append({"display_name": "dp_pll", **display["dp_pll"],
                    "display_register_offset": 4, "canonical_pll_offset": 0x44,
                    "physical_word": 0x088ea044})
    provider_specs = (
        ("iface_clk", "clock_dispcc", "DISP_CC_MDSS_AHB_CLK",
         "include/dt-bindings/clock/qcom,dispcc-kona.h", 0),
        ("ref_clk_src", "clock_rpmh", "RPMH_CXO_CLK",
         "include/dt-bindings/clock/qcom,rpmh.h", 0),
        ("gcc_iface", "clock_gcc", "GCC_DISP_AHB_CLK",
         "include/dt-bindings/clock/qcom,gcc-kona.h", 24),
        ("pipe_clk", "clock_gcc", "GCC_USB3_PRIM_PHY_PIPE_CLK",
         "include/dt-bindings/clock/qcom,gcc-kona.h", 197),
    )
    clock_section = re.search(r"\bclocks\s*=(.*?);", pll_node, re.S).group(1)
    clock_pairs = re.findall(r"<&(\w+)\s+(\w+)>", clock_section)
    names = re.search(r"\bclock-names\s*=(.*?);", pll_node, re.S).group(1)
    require(re.findall(r'"([^"]+)"', names) == [x[0] for x in provider_specs],
            "PLL clock-name ordering differs")
    require(clock_pairs == [(x[1], x[2]) for x in provider_specs],
            "PLL provider ordering differs")
    providers = []
    for name, provider, binding, header, ident in provider_specs:
        require(definitions(sources[header])[binding]["value"] == ident,
                "Provider binding ID differs: " + binding)
        providers.append({"name": name, "provider": provider, "binding": binding,
                          "id": ident, "source": header})
    drv = sources["techpack/display/pll/pll_drv.c"]
    require(re.search(r'"qcom,mdss_dp_pll_7nm"\)\)\s*{?\s*'
                      r'pll_res->pll_interface_type\s*=\s*MDSS_DP_PLL_7NM', drv),
            "Non-V2 dispatch identity differs")
    require("case MDSS_DP_PLL_7NM:" in drv and
            "dp_pll_clock_register_7nm(pdev, pll_res)" in drv,
            "Non-V2 dispatch target differs")
    require("readl_relaxed(pll_res->gdsc_base) & BIT(31)" in
            sources["techpack/display/pll/pll_drv.h"],
            "PLL resource check no longer reads the existing GDSC power bit")
    makefile = sources["techpack/display/pll/Makefile"]
    require("dp_pll_7nm.o" in makefile and "dp_pll_7nm_util.o" in makefile,
            "DP PLL objects are not built together")
    return {"compatible": "qcom,mdss_dp_pll_7nm", "interface": "MDSS_DP_PLL_7NM",
            "pll_resources": pll, "display_aliases": aliases, "providers": providers,
            "unmapped_context": {"usb3_dp_com": display["usb3_dp_com"],
                                 "existing_mdss_gdsc": pll["gdsc_base"]}}


def parse_clocks(source, binding):
    dp = binding.split("/* DP PLL clocks */")
    require(len(dp) == 2, "Missing separate DP binding namespace")
    ids = {k: v["value"] for k, v in definitions(dp[1]).items()}
    require(len(ids) == 6 and set(ids.values()) == set(range(6)),
            "DP onecell IDs must be exactly 0 through 5")
    match = re.search(r"static struct clk_hw \*mdss_dp_pllcc_7nm\[\] = \{(.*?)^};",
                      source, re.M | re.S)
    require(match is not None, "Missing DP onecell table")
    entries = re.findall(r"\[(\w+)\]\s*=\s*&(\w+)\.(?:clkr\.)?hw", match.group(1))
    require(len(entries) == 6 and {k for k, _ in entries} == set(ids),
            "DP onecell entries differ from bindings")
    result = []
    for symbol, obj in sorted(entries, key=lambda x: ids[x[0]]):
        match = re.search(r"static struct \w+\s+" + obj + r"\s*=\s*\{(.*?)^};",
                          source, re.M | re.S)
        require(match is not None, "Missing DP clock object: " + obj)
        body = match.group(1)
        names = re.findall(r'\.name\s*=\s*"([^"]+)"', body)
        require(len(names) == 1, "Clock object must have one name")
        row = {"id": ids[symbol], "binding": symbol, "object": obj, "name": names[0]}
        div, mult = re.search(r"\.div\s*=\s*(\d+)", body), re.search(r"\.mult\s*=\s*(\d+)", body)
        if div or mult:
            require(div and mult, "Incomplete fixed factor")
            row["fixed_factor"] = {"multiply": int(mult.group(1)), "divide": int(div.group(1))}
        if symbol == "DP_VCO_CLK":
            require('"xo_board"' in body, "VCO parent declaration changed")
            row["declared_parent"] = "xo_board"
        if symbol == "DP_VCO_DIVIDED_CLK_SRC_MUX":
            require(re.search(r"\.reg\s*=\s*0x64", body), "Regmap key changed")
            row.update({"regmap_key": 0x64, "physical_region": "PHY",
                        "physical_offset": 0x70,
                        "code_to_parent_index": {"1": 0, "2": 1, "0": 2},
                        "unqualified_code": 3})
        result.append(row)
    require([r["name"] for r in result] ==
            ["dp_vco_clk", "dp_link_clk_divsel_ten", "dp_vco_divsel_two_clk_src",
             "dp_vco_divsel_four_clk_src", "dp_vco_divsel_six_clk_src",
             "dp_vco_divided_clk_src_mux"], "Non-V2 clock names differ")
    require("MDSS_DP_PLL_7NM_V2" in source and '"dp_phy_pll_link_clk"' in source and
            '"dp_phy_pll_vco_div_clk"' in source,
            "Expected V2 substitution branch is missing")
    return result


def parse_recipes(util, header):
    defs = definitions(header)
    require(tuple(defs[name]["value"] for name in RATE_SYMBOLS) == RATE_NUMBERS,
            "Four source HSCLK request numbers differ")
    body, _ = function(util, "dp_vco_pll_init_db_7nm")
    defaults = {name: int(value, 0) for name, value in re.findall(
        r"pdb->(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;", body.split("switch (rate)")[0])}
    recipes = []
    for symbol, rate in zip(RATE_SYMBOLS, RATE_NUMBERS):
        case = re.search(r"case\s+" + symbol + r"\s*:(.*?)\bbreak;", body, re.S)
        require(case is not None, "Missing supported source rate: " + symbol)
        values = dict(defaults)
        values.update({name: int(value, 0) for name, value in re.findall(
            r"pdb->(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)\s*;", case.group(1))})
        recipes.append({"rate_symbol": symbol, "ccf_hsclk_khz": rate,
                        "physical_hsclk_hz_reference": rate * 1000, "fields": values})
    require(len(re.findall(r"\bcase\s+DP_VCO_HSCLK_", body)) == 4,
            "Unexpected additional DP recipe")
    return recipes


def parse_registers(util, catalog, reg_header, context, recipes):
    defs, dp_defs = definitions(util), definitions(reg_header)
    all_calls = calls(util)
    config_calls = calls(util, "dp_config_vco_rate_7nm")
    region_for = {"pll_base": 0, "phy_base": 1,
                  "ln_tx0_base": 2, "ln_tx0_tran_base": 2, "ln_tx0_vmode_base": 2,
                  "ln_tx1_base": 3, "ln_tx1_tran_base": 3, "ln_tx1_vmode_base": 3}
    resources_by_name = context["pll_resources"]
    registers = {}
    for call in all_calls:
        require(call["base"] in region_for, "Unexpected register base")
        region = region_for[call["base"]]
        local = defs[call["register"]]["value"]
        resource = resources_by_name[call["base"]]
        require(local % 4 == 0 and local + 4 <= resource["bytes"],
                "Access exceeds named resource")
        offset = resource["address"] - REGION_BASES[region] + local
        key = region, offset
        if key not in registers:
            registers[key] = {"name": (REGIONS[region] + "_" if region >= 2 else "") +
                             call["register"], "region": region, "offset": offset,
                             "source_register": call["register"], "kind": "CONFIG",
                             "rule": None, "required": True, "write_mask": 0xff,
                             "values": None, "source_accesses": []}
        registers[key]["source_accesses"].append({"source": UTIL_SOURCE, **call})
    require(len(registers) == 60, "Expected 60 directly accessed util words")
    status_body, _ = function(util, "dp_7nm_pll_get_status")
    for name, (region, offset, kind, read_mask) in STATUS.items():
        require(defs[name]["value"] == offset and name in status_body,
                "Computed status word differs")
        require((region, offset) not in registers, "Status also directly writable")
        registers[region, offset] = {
            "name": name, "source_register": name, "region": region, "offset": offset,
            "kind": kind, "rule": "NONE", "required": False, "write_mask": 0,
            "values": [0] * 4, "computed_read_mask": read_mask,
            "source_accesses": [{"source": UTIL_SOURCE, "operation": "poll",
                                 "register": name, "line": defs[name]["line"]}]}
    require(len(registers) == 63, "Direct util plus poll closure must contain 63 words")
    for region in (2, 3):
        for name in EXTRA_TX:
            offset = dp_defs[name]["value"]
            require(re.search(r"\bdp_write\(\s*" + name + r"\s*,", catalog),
                    "Extra TX word lacks a catalog write: " + name)
            require((region, offset) not in registers, "Duplicate extra TX word")
            registers[region, offset] = {
                "name": REGIONS[region] + "_" + name, "source_register": name,
                "region": region, "offset": offset, "kind": "CONFIG", "rule": "NONE",
                "required": False, "write_mask": 0xff, "values": [0] * 4,
                "source_accesses": [{"source": CATALOG_SOURCE, "operation": "W",
                                     "register": name,
                                     "definition_source": REG_SOURCE,
                                     "definition_line": dp_defs[name]["line"]}]}
    for row in registers.values():
        if row["rule"] is not None:
            continue
        key, name = (row["region"], row["offset"]), row["source_register"]
        if key in ((0, 0x9c), (1, 0x10)):
            row.update(rule="NONE", required=False, values=[0] * 4)
        elif key == (1, 0xc8):
            require("DP_PHY_SPARE0_V420" in catalog and
                    dp_defs["DP_PHY_SPARE0_V420"]["value"] == row["offset"] and
                    "info |= (ln_cnt & 0x0F);" in catalog and
                    "info |= ((orientation & 0x0F) << 4);" in catalog,
                    "Shared lane/orientation write differs")
            row.update(rule="SPARE", values=[0] * 4)
        elif key == (1, 0x18):
            values = {int(c["expression"], 0) for c in config_calls
                      if c["base"] == "phy_base" and c["register"] == name}
            require(values == {0x75, 0x6d, 0x7d}, "PHY power recipes differ")
            row.update(rule="POWER", values=[0x75, 0x6d, 0x7d, 0])
        elif key == (1, 0x1c):
            values = {int(c["expression"], 0) for c in config_calls
                      if c["base"] == "phy_base" and c["register"] == name}
            require(values == {0x5c, 0x4c}, "PHY orientation modes differ")
            row.update(rule="MODE", values=[0x5c, 0x4c, 0, 0])
        else:
            matching = [c for c in config_calls
                        if c["register"] == name and region_for[c["base"]] == row["region"]]
            require(len(matching) == 1, "Expected one configuration write: " + row["name"])
            expression = matching[0]["expression"]
            if re.fullmatch(r"0x[0-9a-fA-F]+|\d+", expression):
                values = [int(expression, 0)] * 4
            else:
                match = re.fullmatch(r"pdb->(\w+)", expression)
                require(match is not None, "Unreviewed configuration expression: " + expression)
                values = [r["fields"][match.group(1)] for r in recipes]
            row.update(rule=("FIXED" if len(set(values)) == 1 else "RATE"), values=values)
            if key == (1, 0x70):
                row["rule"] = "NONE"  # Written once, then live mux selection is permitted.
        row["write_mask"] = {(1, 0x10): 0x1f, (1, 0x14): 0x0f,
                             (1, 0x18): 0x7f, (1, 0x1c): 0x7f,
                             (1, 0x70): 0x03}.get(key, 0xff)
    result = [registers[k] for k in sorted(registers)]
    require(len(result) == 73 and sum(r["required"] for r in result) == 58,
            "Register/required counts differ from the reviewed contract")
    require([sum(r["region"] == n for r in result) for n in range(4)] == [30, 11, 16, 16],
            "Per-region word counts differ")
    rule_counts = {rule: sum(r["rule"] == rule for r in result)
                   for rule in ("NONE", "FIXED", "RATE", "MODE", "POWER", "SPARE")}
    require(rule_counts == {"NONE": 16, "FIXED": 47, "RATE": 7,
                            "MODE": 1, "POWER": 1, "SPARE": 1},
            "Rule cardinalities differ")
    require(all(r["offset"] % 4 == 0 and r["offset"] + 4 <= 0x200 for r in result),
            "Generated owner exceeds its physical container")
    require(all(all((v & ~r["write_mask"]) == 0 for v in r["values"])
                for r in result if r["kind"] == "CONFIG"),
            "A source recipe exceeds the reference write mask")
    return result, config_calls


def windows(registers):
    result = []
    for row in registers:
        region, offset = row["region"], row["offset"]
        if result and result[-1]["region"] == region and \
                result[-1]["offset"] + result[-1]["bytes"] == offset:
            result[-1]["bytes"] += 4
        else:
            result.append({"region": region, "offset": offset, "bytes": 4})
    require(len(result) == 42 and sum(w["bytes"] for w in result) == 292,
            "Sparse leaf contract differs")
    return result


def source_contract(sources):
    util = sources[UTIL_SOURCE]
    recalc, _ = function(util, "dp_vco_recalc_rate_7nm")
    require("QSERDES_COM_HSCLK_SEL" in recalc and "DP_PHY_AUX_CFG2" in recalc and
            recalc.index("QSERDES_COM_HSCLK_SEL") < recalc.index("DP_PHY_AUX_CFG2") and
            "hsclk_sel &= 0x0f;" in recalc and "link_clk_divsel >>= 2;" in recalc and
            "link_clk_divsel &= 0x3;" in recalc and "dp_7nm_pll_get_status" not in recalc,
            "Cold registration must read selector words without requiring LOCK")
    for name in ("dp_mux_set_parent_7nm", "dp_mux_get_parent_7nm"):
        body, _ = function(util, name)
        require("DP_PHY_VCO_DIV" in body and "0x03" in body,
                "Mux callback physical word differs")
    usb = sources["include/linux/usb/usbpd.h"]
    require(re.search(r"enum plug_orientation\s*\{\s*ORIENTATION_NONE,\s*"
                      r"ORIENTATION_CC1,\s*ORIENTATION_CC2,\s*}", usb),
            "Orientation CC1/CC2 numeric definitions changed")
    defs = definitions(util)
    require(defs["DP_PHY_PLL_POLL_SLEEP_US"]["value"] == 500 and
            defs["DP_PHY_PLL_POLL_TIMEOUT_US"]["value"] == 10000,
            "Source status polling limits changed")
    enable = calls(util, "dp_pll_enable_7nm")
    require([(c["base"], c["register"], int(c["expression"], 0)) for c in enable] ==
            [("phy_base", "DP_PHY_CFG", 1), ("phy_base", "DP_PHY_CFG", 5),
             ("phy_base", "DP_PHY_CFG", 1), ("phy_base", "DP_PHY_CFG", 9),
             ("pll_base", "QSERDES_COM_RESETSM_CNTRL", 0x20),
             ("phy_base", "DP_PHY_CFG", 0x19)],
            "Source digital enable sequence changed")
    return {"cold_probe_registers": [
                {"region": "PLL", "offset": 0x158, "mask": 0xf},
                {"region": "PHY", "offset": 0x28, "shift": 2, "mask": 3}],
            "cold_recalc_requires_lock": False,
            "hsclk_selector_to_divisor": {"0": 1, "1": 2, "3": 3, "5": 5},
            "unrecognized_hsclk_selector_software_fallback_divisor": 5,
            "aux_divisor_codes": {"0": 5, "1": 10, "2": 20},
            "aux_code_2_software_recalc_hsclk_khz": 2700000,
            "cold_zero_software_recalc_hsclk_khz": 8100000,
            "software_fallback_does_not_qualify_carrier": True,
            "enable_writes": enable, "source_poll_sleep_us": 500,
            "source_poll_timeout_us": 10000,
            "status_fields": [
                {"name": "C_READY", "region": "PLL", "offset": 0x178, "mask": 1},
                {"name": "FREQ_DONE", "region": "PLL", "offset": 0x140, "mask": 1},
                {"name": "PLL_LOCKED", "region": "PLL", "offset": 0x140, "mask": 2},
                {"name": "TSYNC_DONE", "region": "PHY", "offset": 0xdc, "mask": 1},
                {"name": "PHY_READY", "region": "PHY", "offset": 0xdc, "mask": 2}],
            "status_writes_supported": False,
            "orientation_codes": {"CC1": 1, "CC2": 2}}


def emit_data(manifest):
    result = ["/* Generated by tb371fc/scripts/generate_dp_pll_7nm.py; do not edit. */",
              "/* Exact non-V2 digital reference; masks/zero storage are reference policy. */",
              "static const DpPllRegDef dp_pll_regs[DP_PLL_REGISTERS] = {"]
    for r in manifest["registers"]:
        values = ", ".join(f"0x{v:x}" for v in r["values"])
        result.append("    { " + json.dumps(r["name"]) +
                      f", 0x{r['offset']:03x}, DP_REGION_{REGIONS[r['region']]}, " +
                      f"DP_REG_{r['kind']}, DP_RULE_{r['rule']}, " +
                      ("true" if r["required"] else "false") +
                      f", 0x{r['write_mask']:x}, {{ {values} }} }},")
    result += ["};", "", "const DpPllWindowDef dp_pll_windows[DP_PLL_WINDOWS] = {"]
    for w in manifest["windows"]:
        result.append(f"    {{ DP_REGION_{REGIONS[w['region']]}, " +
                      f"0x{w['offset']:03x}, 0x{w['bytes']:03x} }},")
    result += ["};", "", "static const char *const dp_pll_clock_names[DP_PLL_CLOCKS] = {"]
    result += ["    " + json.dumps(c["name"]) + "," for c in manifest["clocks"]]
    result += ["};", ""]
    return "\n".join(result)


def generate(source_root):
    sources, pins = {}, []
    require(len(SOURCE_HASHES) == 17, "Source input count differs")
    for relative, expected in SOURCE_HASHES.items():
        raw = (source_root / relative).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected,
                "Pinned source hash differs: " + relative)
        sources[relative] = raw.decode("utf-8")
        pins.append({"path": relative, "repository": REPOSITORY, "commit": REVISION,
                     "bytes": len(raw), "sha256": expected,
                     "git_blob_sha": hashlib.sha1(
                         b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest(),
                     "url": f"https://github.com/{REPOSITORY}/blob/{REVISION}/{relative}"})
    context = parse_context(sources)
    clocks = parse_clocks(sources[PLL_SOURCE],
                          sources["include/dt-bindings/clock/mdss-7nm-pll-clk.h"])
    recipes = parse_recipes(sources[UTIL_SOURCE],
                            sources["techpack/display/pll/dp_pll_7nm.h"])
    regs, config_calls = parse_registers(sources[UTIL_SOURCE], sources[CATALOG_SOURCE],
                                        sources[REG_SOURCE], context, recipes)
    leaves = windows(regs)
    manifest = {
        "schema": "tb371fc-dp-pll-7nm-registers-v1",
        "source_revision": REVISION, "source_files": pins, "source_input_count": 17,
        "source_scope": "Table inputs only; rate-unit support provenance is separate.",
        "source_contract": source_contract(sources), "context": context,
        "regions": [{"id": n, "name": name, "physical_base": REGION_BASES[n],
                     "container_bytes": 0x200} for n, name in enumerate(REGIONS)],
        "register_count": 73, "required_register_count": 58,
        "window_count": 42, "mapped_bytes": 292, "clock_count": 6,
        "direct_util_word_count": 63, "shared_catalog_tx_word_count": 10,
        "registers": regs, "windows": leaves, "clocks": clocks, "recipes": recipes,
        "source_configuration_writes": config_calls,
        "reference_policy": {
            "cold_storage": "Zero-initialized digital storage, not a claimed silicon POR value.",
            "masks": "Bounded source-used fields, not a silicon reserved-bit specification.",
            "status": "Computed read-only status; writes including zero are rejected.",
            "carrier": "Cold recalc returns a software nominal rate without proving a carrier.",
            "unsupported": ["Other PLL/PHY/TX words", "AUX engine/interrupt behavior",
                            "USB combo engine", "USB/display link training", "Analog VCO behavior"],
            "existing_mdss_owner_preserved": True,
            "regmap_0x64_is_not_a_physical_word": True,
        },
    }
    return emit_data(manifest), json.dumps(manifest, indent=2) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    outputs = generate(args.source_root)
    for relative, content in zip(OUTPUTS, outputs):
        destination = args.output_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        print(json.dumps({"path": relative, "bytes": len(content.encode("utf-8")),
                          "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()}))
