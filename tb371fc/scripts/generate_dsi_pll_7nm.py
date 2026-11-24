#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate the bounded Kona DSI PLL v4.1 reference tables from pinned sources.

This is deliberately a parser for one reviewed source revision, not a general C
interpreter.  Every input is hashed before extracting any fact.  Conditional
programming is selected explicitly for v4.1; generated recipe values are final
programming values, never an assertion about hardware reset state.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
REPOSITORY = "OnePlusOSS/android_kernel_oneplus_sm8250"
SOURCE_HASHES = {
    "techpack/display/pll/dsi_pll_7nm.c": "a286700822a0fb2fa1e9bff39cc6720295254c6137f29de65a37bcb819a8dd64",
    "techpack/display/pll/pll_drv.c": "14652116c7ea1ff9c5380fe5441087c910d57b890d52aec3a2a031b76b250c56",
    "techpack/display/pll/pll_drv.h": "ba1df695799b9c4b078fcde5c6cfa3bddf35ed026f818f4fe372cf89d3221feb",
    "techpack/display/pll/pll_util.c": "652450b062d1a957a2db5b83c46244e11168974e259506f0c9a3e5b959f2e49a",
    "techpack/display/pll/dsi_pll.h": "2f1afd78313228853871d028c8ae035a5080d56d4a64b4c8c2e566bff1a188bf",
    "techpack/display/pll/dsi_pll_util.c": "01f80def383b13ca240f409dc11ff4982f68e669ddf24d970f3d523d2da0363f",
    "techpack/display/pll/Makefile": "83598d0660b38fb30979ca52a17e2ac2ece01573ca49ed9a86ea01cd3624bcf0",
    "include/dt-bindings/clock/mdss-7nm-pll-clk.h": "51632c1a73cfce8c4915e5e5c56f46b0fddae983d3e0ac44cd2731c488105d82",
    "arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi": "d275792039d00a135a3f809e49954196c62f1e68202702597fe9d93fb5484118",
    "techpack/display/msm/dsi/dsi_phy_hw_v4_0.c": "89d6e0cc0783f197a73bb2d6ece734dcdebb9291ffaaadd37cb2f48911bc084d",
    "techpack/display/msm/dsi/dsi_hw.h": "f09364f20032944a1a886e90f627c3a23af70e0d2927cf9a35358f6a1087cccc",
    "techpack/display/msm/dsi/dsi_phy_hw.h": "201412264a93eacff63d6d9022e75c27e8b9cad2c4ed7da74ac6474216ab9138",
    "techpack/display/msm/dsi/dsi_catalog.c": "5e53e4d2f82034d8b86b8b92727f05760e2e54ff2de227e8ad581f997e2e58a1",
}
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-dsi-pll-data.inc",
           "tb371fc/docs/dsi-pll-7nm-registers.json")
PLL_SOURCE = "techpack/display/pll/dsi_pll_7nm.c"
PHY_SOURCE = "techpack/display/msm/dsi/dsi_phy_hw_v4_0.c"
PLL_OFFSET = 0x500
UNMAPPED_PLL = (0xcc, 0xd0, 0xd4, 0xd8, 0xf0, 0xf4, 0xf8, 0xfc)
# These masks delimit source-used fields accepted by this reference.  They are
# not claims about undocumented silicon reserved bits or analog PHY behavior.
PHY_MASKS = {
    "PHY_CMN_CLK_CFG0": 0xff,
    "PHY_CMN_CLK_CFG1": 0x37,
    "PHY_CMN_GLBL_CTRL": 0x40,
    "PHY_CMN_RBUF_CTRL": 0x01,
    "PHY_CMN_CTRL_0": 0x7f,
    "PHY_CMN_CTRL_2": 0x40,
    "PHY_CMN_CTRL_3": 0x04,
    "PHY_CMN_PLL_CNTRL": 0x01,
    "PHY_CMN_GLBL_DIGTOP_SPARE4": 0x01,
}
# Source init writes zero to these observation words.  Only COMMON_STATUS_ONE
# has a supported read (computed LOCK bit 0); the others do not return fake data.
STATUS_OFFSETS = (*range(0x1b0, 0x1d0, 4), *range(0x200, 0x210, 4),
                  0x218, 0x21c, 0x220)
RULE_OFFSETS = {
    "FACTOR": (0xe0, 0xe4, 0xe8, 0xec),
    "RATE": (0x240, 0x248, 0x258),
    "MODE": (0x250,),
    "SSC": (*range(0x120, 0x138, 4), 0x150),
    "NONE": (0x28, 0x154),
}


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def function(source, name):
    match = re.search(r"^(?:static\s+)?[^\n;{}]*\b" + re.escape(name) +
                      r"\s*\([^;{}]*\)\s*\{", source, re.M)
    require(match is not None, "Missing reviewed function: " + name)
    start = match.end() - 1
    depth = 1
    end = start + 1
    while end < len(source) and depth:
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    require(depth == 0, "Unterminated reviewed function: " + name)
    return source[start + 1:end - 1], start + 1


def write_calls(source, body, absolute_start):
    """Extract each macro call, retaining expression, order and source line."""
    result = []
    for match in re.finditer(r"MDSS_PLL_REG_W\s*\(", body):
        depth = 1
        cursor = match.end()
        begin = cursor
        parts = []
        while cursor < len(body) and depth:
            char = body[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            if char == "," and depth == 1:
                parts.append(body[begin:cursor].strip())
                begin = cursor + 1
            elif depth == 0:
                parts.append(body[begin:cursor].strip())
            cursor += 1
        require(depth == 0 and len(parts) == 3, "Unparsed source write")
        base, register, expression = parts
        result.append({"base": base, "register": register,
                       "expression": re.sub(r"\s+", " ", expression),
                       "line": source[:absolute_start + match.start()].count("\n") + 1,
                       "relative_position": match.start()})
    return result


def definitions(source, prefix):
    return [{"name": match.group(1), "offset": int(match.group(2), 16),
             "line": source[:match.start()].count("\n") + 1}
            for match in re.finditer(r"^#define\s+(" + prefix +
                                     r"\w+)\s+\(?(0x[0-9a-fA-F]+)\)?\s*$",
                                     source, re.M)]


def clocks(source, binding):
    dsi = binding.split("/* DP PLL clocks */")[0]
    ids = {name: int(value) for name, value in re.findall(
        r"^#define\s+(\w+)\s+(\d+)\s*$", dsi, re.M)}
    require(len(ids) == 52 and set(ids.values()) == set(range(52)),
            "DSI binding IDs differ (DP IDs must not enter this namespace)")
    array = re.search(r"static struct clk_hw \*mdss_dsi_pllcc_7nm\[\] = \{(.*?)^};",
                      source, re.M | re.S)
    require(array is not None, "Missing DSI onecell clock array")
    entries = re.findall(r"\[(\w+)\]\s*=\s*&(\w+)\.(?:clkr\.)?hw", array.group(1))
    require(len(entries) == 52 and {key for key, _ in entries} == set(ids),
            "Onecell array and DSI binding differ")
    result = []
    for symbol, obj in entries:
        block = re.search(r"^static struct (\w+) " + re.escape(obj) +
                          r" = \{(.*?)^};", source, re.M | re.S)
        require(block is not None, "Missing clock declaration: " + obj)
        body = block.group(2)
        name = re.search(r'\.name\s*=\s*"([^"]+)"', body)
        parents = re.search(r"\.parent_names\s*=\s*\(const char \*\[\]\)\s*\{(.*?)\}",
                            body, re.S)
        require(name is not None and parents is not None, "Unparsed clock: " + obj)
        row = {"id": ids[symbol], "binding": symbol, "name": name.group(1),
               "object": obj, "type": block.group(1),
               "bank": ids[symbol] // 26, "shadow": "shadow" in obj,
               "parents": re.findall(r'"([^"]+)"', parents.group(1)),
               "source_line": source[:block.start()].count("\n") + 1}
        for field in ("shift", "width", "mult", "div"):
            literal = re.search(r"\." + field + r"\s*=\s*(\d+)\s*,", body)
            if literal:
                row[field] = int(literal.group(1))
        result.append(row)
    result.sort(key=lambda row: row["id"])
    require(len({row["name"] for row in result}) == 52, "Duplicate clock name")
    require([row["id"] for row in result if row["type"] == "dsi_pll_vco_clk"] ==
            [0, 10, 26, 36], "Unexpected physical/shadow VCO views")
    return result


def final_recipe(source):
    """Select the v4.1 path and retain repeated writes in source order."""
    init, init_start = function(source, "dsi_pll_init_val")
    commit, commit_start = function(source, "dsi_pll_commit")
    hz, hz_start = function(source, "dsi_pll_config_hzindep_reg")
    set_rate, _ = function(source, "vco_7nm_set_rate")
    sequence = ["dsi_pll_init_val", "dsi_pll_detect_phy_mode",
                "dsi_pll_setup_config", "dsi_pll_calc_dec_frac",
                "dsi_pll_calc_ssc", "dsi_pll_commit",
                "dsi_pll_config_hzindep_reg", "dsi_pll_ssc_commit"]
    positions = [set_rate.find(name + "(") for name in sequence]
    require(all(pos >= 0 for pos in positions) and positions == sorted(positions),
            "Reviewed programming order changed")
    require(re.search(r"if \(dsi_pll_7nm_is_hw_revision_v1\(rsc\)\)\s*"
                      r"MDSS_PLL_REG_W\(pll_base, PLL_BIAS_CONTROL_1, 0x00000066\);\s*"
                      r"else\s*MDSS_PLL_REG_W\(pll_base, PLL_BIAS_CONTROL_1, 0x00000040\);",
                      init) is not None, "Unknown BIAS variant selection")
    require(re.search(r"if \(dsi_pll_7nm_is_hw_revision_v1\(rsc\)\)\s*"
                      r"MDSS_PLL_REG_W\(pll_base, PLL_GEAR_BAND_SELECT_CONTROLS, 0x21\);",
                      hz) is not None, "Unknown GEAR variant selection")
    require(re.search(r"case MDSS_DSI_PLL_7NM_V4_1:\s*default:\s*"
                      r"MDSS_PLL_REG_W\(pll_base, PLL_IFILT, 0x3F\);", hz) is not None,
            "Unknown IFILT variant selection")
    values, history, rejected = {}, {}, []
    for stage, body, start in (("init", init, init_start),
                               ("commit", commit, commit_start),
                               ("hz-independent", hz, hz_start)):
        for call in write_calls(source, body, start):
            if call["base"] != "pll_base":
                continue  # The peer write is recorded separately in the model contract.
            name, expression = call["register"], call["expression"]
            if not re.fullmatch(r"0x[0-9a-fA-F]+|\d+", expression):
                continue
            value = int(expression, 0)
            row = {"stage": stage, "source_line": call["line"], "value": value}
            skip = ((stage == "init" and name == "PLL_BIAS_CONTROL_1" and value == 0x66)
                    or (stage == "hz-independent" and name == "PLL_GEAR_BAND_SELECT_CONTROLS")
                    or (stage == "hz-independent" and name == "PLL_IFILT" and
                        value in (0x30, 0x22))
                    or (stage == "hz-independent" and name in
                        ("PLL_ANALOG_CONTROLS_FIVE_1", "PLL_VCO_CONFIG_1")))
            if skip:
                rejected.append({"register": name, **row})
                continue
            values[name] = value
            history.setdefault(name, []).append(row)
    required = {"PLL_CORE_INPUT_OVERRIDE": 0x12,
                "PLL_PLL_INT_GAIN_IFILT_BAND_1": 0x82, "PLL_PFILT": 0x2f,
                "PLL_IFILT": 0x3f, "PLL_BIAS_CONTROL_1": 0x40,
                "PLL_GEAR_BAND_SELECT_CONTROLS": 0x22, "PLL_PERF_OPTIMIZE": 0x22}
    require(all(values.get(name) == value for name, value in required.items()),
            "v4.1 final repeated-write recipe differs")
    return values, history, {"source_call_order": sequence,
                             "excluded_other_variant_or_rate_dependent_literals": rejected,
                             "required_final_values": required}


def variant_facts(sources):
    source = sources[PLL_SOURCE]
    driver = sources["techpack/display/pll/pll_drv.c"]
    require(re.search(r'!strcmp\(compatible_stream, "qcom,mdss_dsi_pll_7nm_v4_1"\)\)\s*'
                      r"pll_res->pll_interface_type = MDSS_DSI_PLL_7NM_V4_1;", driver),
            "v4.1 compatible dispatch changed")
    require(re.search(r"case MDSS_DSI_PLL_7NM_V4_1:\s*"
                      r"rc = dsi_pll_clock_register_7nm\(pdev, pll_res\);", driver),
            "v4.1 registration dispatch changed")
    require(source.count(".ref_clk_rate = 19200000UL,") == 4,
            "Normal/shadow reference rate declarations changed")
    registration, _ = function(source, "dsi_pll_clock_register_7nm")
    for bank in range(2):
        for shadow in ("", "shadow_"):
            obj = f"dsi{bank}pll_{shadow}vco_clk"
            require(f"{obj}.min_rate = 600000000;" in registration and
                    f"{obj}.max_rate = 5000000000;" in registration,
                    "v4.1 VCO range override changed")
    for name in ("dsi_pll_disable_pll_bias", "dsi_pll_enable_pll_bias"):
        body, _ = function(source, name)
        require("ndelay(250);" in body, "Bias timing witness changed")
    fraction, _ = function(source, "dsi_pll_calc_dec_frac")
    require("(frac & 0x30000) >> 16" in fraction,
            "Fraction high register is not the reviewed 2-bit field")
    thresholds = re.findall(r"if \(pll_freq <= (\d+)ULL\)\s*"
                            r"regs->pll_clock_inverters = (0x[0-9a-fA-F]+);", fraction)
    require(thresholds == [("1000000000", "0xA0"), ("2500000000", "0x20"),
                           ("3020000000", "0x00")], "v4.1 inverter thresholds changed")
    require("DSI_PHY_VERSION_4_1" in sources["techpack/display/msm/dsi/dsi_catalog.c"],
            "Matching shared PHY v4.1 dispatch is missing")
    dts = sources["arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi"]
    banks = []
    for bank in range(2):
        match = re.search(r"mdss_dsi" + str(bank) + r"_pll:[^{]+\{(.*?)\n\s*};", dts, re.S)
        require(match is not None, "Missing source DSI bank")
        body = match.group(1)
        require('compatible = "qcom,mdss_dsi_pll_7nm_v4_1";' in body,
                "Source DT variant differs")
        reg = re.search(r"\breg\s*=\s*(.*?);", body, re.S)
        names = re.search(r"reg-names\s*=\s*(.*?);", body, re.S)
        require(reg is not None and names is not None, "Missing source DT resources")
        pairs = [(int(a, 16), int(b, 16)) for a, b in
                 re.findall(r"<(0x[0-9a-f]+)\s+(0x[0-9a-f]+)>", reg.group(1))]
        labels = re.findall(r'"([^"]+)"', names.group(1))
        require(labels == ["pll_base", "phy_base", "gdsc_base", "dynamic_pll_base"]
                and len(pairs) == len(labels), "Source DT resource order differs")
        resources = {name: {"base": pair[0], "bytes": pair[1]}
                     for name, pair in zip(labels, pairs)}
        require(pairs == [(0xae94900 + bank * 0x2000, 0x260),
                          (0xae94400 + bank * 0x2000, 0x800), (0xaf03000, 8),
                          (0xae94200 + bank * 0x2000, 0x100)],
                "Source DT address geometry differs")
        require('clock-names = "iface_clk";' in body and "clock-rate = <0>;" in body
                and "clocks = <&clock_dispcc DISP_CC_MDSS_AHB_CLK>;" in body,
                "Source DT interface-clock declaration differs")
        require('qcom,dsi-pll-ssc-mode = "down-spread";' in body
                and "qcom,dsi-pll-ssc-en;" in body, "Source DT SSC declaration differs")
        banks.append({"bank": bank, "resources": resources,
                      "binding_id_min": 26 * bank, "binding_id_max": 26 * bank + 25,
                      "has_dfps_memory_region": "memory-region" in body})
    require([bank["has_dfps_memory_region"] for bank in banks] == [True, False],
            "Source DT DFPS memory ownership differs")
    return {"compatible": "qcom,mdss_dsi_pll_7nm_v4_1", "banks": banks,
            "vco_min_hz": 600000000, "vco_max_hz": 5000000000,
            "reference_hz": 19200000,
            "inverter_thresholds_hz_inclusive": [
                {"max_hz": int(rate), "value": int(value, 16)}
                for rate, value in thresholds],
            "inverter_above_last_threshold": 0x40,
            "fraction_bits": 18, "bias_wait_ns": 250}


def inventory(directory):
    sources, pins = {}, []
    for path, expected in SOURCE_HASHES.items():
        raw = (directory / path).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        require(actual == expected, "Source pin mismatch: " + path + ": " + actual)
        sources[path] = raw.decode("utf-8")
        pins.append({"path": path, "repository": REPOSITORY, "commit": REVISION,
                     "bytes": len(raw), "sha256": actual,
                     "git_blob_sha": hashlib.sha1(b"blob " + str(len(raw)).encode() +
                                                   b"\0" + raw).hexdigest(),
                     "url": "https://github.com/" + REPOSITORY + "/blob/" + REVISION + "/" + path})
    source = sources[PLL_SOURCE]
    variant = variant_facts(sources)
    pll = definitions(source, "PLL_")
    phy = definitions(source, "PHY_CMN_")
    dynamic = definitions(source, "DSI_DYNAMIC_REFRESH_")
    require(len(pll) == 153 and [row["offset"] for row in pll] == list(range(0, 0x264, 4)),
            "PLL declaration geometry changed")
    require(len(phy) == 9 and {row["name"] for row in phy} == set(PHY_MASKS),
            "Shared PHY declaration geometry changed")
    require(len(dynamic) == 34, "Dynamic refresh declarations changed")
    all_writes = write_calls(source, source, 0)
    literal_touched = {call["register"] for call in all_writes}
    literal_touched.update(re.findall(r"MDSS_PLL_REG_R\([^,]+,\s*(\w+)\s*\)", source))
    mapped_pll = [row for row in pll if row["name"] in literal_touched]
    require(len(mapped_pll) == 145 and
            [row["offset"] for row in pll if row not in mapped_pll] == list(UNMAPPED_PLL),
            "Direct PLL access allowlist changed")
    require({row["name"] for row in phy if row["name"] in literal_touched} ==
            set(PHY_MASKS) - {"PHY_CMN_CTRL_2"}, "Direct PHY access set changed")
    require("DSI_W32(phy, DSIPHY_CMN_CTRL_2, 0x40);" in sources[PHY_SOURCE],
            "PHY CTRL_2 full-rate write is not source backed")
    require("PLL_COMMON_STATUS_ONE" in function(source, "dsi_pll_7nm_lock_status")[0]
            and "BIT(0)" in function(source, "dsi_pll_7nm_lock_status")[0],
            "Supported LOCK read differs")
    values, history, programming = final_recipe(source)
    rows = []
    for row in phy:
        rows.append({**row, "block": "PHY", "kind": "PHY", "rule": "NONE",
                     "write_mask": PHY_MASKS[row["name"]], "recipe_value": 0,
                     "recipe_value_applicable": False, "source": PLL_SOURCE})
    for row in mapped_pll:
        offset, name = row["offset"], row["name"]
        kind = "STATUS" if offset in STATUS_OFFSETS else "PLL"
        rules = [rule for rule, offsets in RULE_OFFSETS.items() if offset in offsets]
        require(len(rules) <= 1, "Overlapping recipe rule")
        rule = "NONE" if kind == "STATUS" else (rules[0] if rules else "FIXED")
        if kind == "STATUS" or rule == "FIXED":
            require(name in values, "No source final literal for " + name)
        if kind == "STATUS":
            require(values[name] == 0 and all(entry["value"] == 0 for entry in history[name]),
                    "Status initialization is not zero-only: " + name)
        mask = 0 if kind == "STATUS" else (3 if offset in (0xec, 0x150, 0x154) else 0xff)
        rows.append({**row, "relative_pll_offset": offset,
                     "offset": PLL_OFFSET + offset, "block": "PLL", "kind": kind,
                     "rule": rule, "write_mask": mask,
                     "recipe_value": values[name] if rule == "FIXED" else 0,
                     "recipe_value_applicable": rule == "FIXED",
                     "source": PLL_SOURCE,
                     "literal_programming_history": history.get(name, []),
                     **({"read_policy": "computed-lock-bit0" if offset == 0x1b0 else "reject-unknown",
                         "write_policy": "accept-zero-only"} if kind == "STATUS" else {})})
    rows.sort(key=lambda row: row["offset"])
    require(len(rows) == 154 and len({row["offset"] for row in rows}) == 154,
            "Mapped per-bank register count differs")
    rule_counts = {rule: sum(row["rule"] == rule for row in rows)
                   for rule in ("NONE", "FIXED", "FACTOR", "RATE", "MODE", "SSC")}
    require(rule_counts == {"NONE": 26, "FIXED": 113, "FACTOR": 4,
                            "RATE": 3, "MODE": 1, "SSC": 7}, "Recipe rule counts differ")
    windows = []
    for row in rows:
        if windows and windows[-1]["offset"] + windows[-1]["size"] == row["offset"]:
            windows[-1]["size"] += 4
        else:
            windows.append({"offset": row["offset"], "size": 4})
    require(windows == [dict(offset=offset, size=size) for offset, size in
                       ((0x10, 0x10), (0x24, 4), (0x2c, 8), (0x3c, 4),
                        (0x128, 4), (0x500, 0xcc), (0x5dc, 0x14), (0x600, 0x164))],
            "Sparse per-bank windows differ")
    clock_rows = clocks(source, sources["include/dt-bindings/clock/mdss-7nm-pll-clk.h"])
    return {"schema": "tb371fc-dsi-pll-7nm-registers-v1",
            "source_revision": REVISION, "source_repository": REPOSITORY,
            "source_files": pins, "compatible": "qcom,mdss_dsi_pll_7nm_v4_1",
            "variant": "MDSS_DSI_PLL_7NM_V4_1", "variant_facts": variant,
            "default_enabled": False,
            "source_identity_scope": "Pinned public implementation; limited original-Image probe-path agreement only, not complete Lenovo kernel source identity.",
            "bank_count": 2, "bank_bases": [0xae94400, 0xae96400],
            "bank_container_bytes": 0x800, "pll_offset_from_phy": PLL_OFFSET,
            "original_dt_named_pll_bytes": 0x260,
            "per_bank_register_count": len(rows), "register_count": len(rows) * 2,
            "per_bank_window_count": len(windows), "window_count": len(windows) * 2,
            "mapped_bytes": len(rows) * 8, "clock_count": len(clock_rows),
            "per_bank_recipe_rule_counts": rule_counts,
            "per_bank_required_recipe_words": 128,
            "declared_register_counts": {"PLL": len(pll), "PHY": len(phy), "dynamic": len(dynamic)},
            "declared_register_counts_are_not_mapped_counts": True,
            "unmapped_pll_offsets": list(UNMAPPED_PLL),
            "dynamic_refresh": {"implemented": False, "declared_registers": dynamic,
                                "reason": "Trigger/trim state is not implemented; shadow CCF views do not create extra physical PLLs."},
            "perf_optimize_alias": {"relative_pll_offset": 0x260,
                                    "canonical_phy_offset": 0x760,
                                    "inside_original_phy_resource": True,
                                    "inside_named_pll_resource": False},
            "masks_are_source_used_reference_domains_not_silicon_reserved_bit_specification": True,
            "recipe_values_are_programming_requirements_not_reset_values": True,
            "programming": programming,
            "reference_timing": {"lock_xo_cycles": 64,
                                 "lock_delay_is_reference_policy": True,
                                 "bias_wait_ns": 250, "bias_wait_is_source_evidenced": True},
            "source_reference_hz": 19200000, "unknown_xo_must_remain_unknown": True,
            "shared_mdss_gdsc_owned_elsewhere": {"base": 0xaf03000, "bytes": 8},
            "registers": rows, "windows": windows, "clocks": clock_rows}


def render(data):
    lines = ["/* SPDX-License-Identifier: GPL-2.0-or-later */",
             "/* Generated by tb371fc/scripts/generate_dsi_pll_7nm.py. */",
             "/* Pinned source: " + REVISION + "; recipe values are not reset values. */", "",
             "static const DsiPllRegister dsi_pll_registers[154] = {"]
    for row in data["registers"]:
        lines.append("    { 0x%03x, 0x%02x, DSI_REG_%s, DSI_RULE_%s, 0x%02x }, /* %s */" %
                     (row["offset"], row["write_mask"], row["kind"], row["rule"],
                      row["recipe_value"], row["name"]))
    lines.extend(["};", "", "const QcomDsiPllWindowDef dsi_pll_windows[8] = {"])
    for row in data["windows"]:
        lines.append("    { 0x%03x, 0x%03x }," % (row["offset"], row["size"]))
    lines.extend(["};", "", "static const char * const dsi_pll_clock_names[52] = {"])
    for row in data["clocks"]:
        lines.append("    [%d] = %s, /* %s */" %
                     (row["id"], json.dumps(row["name"]), row["binding"]))
    lines.extend(["};", ""])
    return {OUTPUTS[0]: "\n".join(lines),
            OUTPUTS[1]: json.dumps(data, indent=2) + "\n"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    data = inventory(args.source_root)
    outputs = render(data)
    for relative, content in outputs.items():
        destination = args.output_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    print(json.dumps({"source_pins": len(data["source_files"]),
                      "per_bank_registers": data["per_bank_register_count"],
                      "windows": data["window_count"], "mapped_bytes": data["mapped_bytes"],
                      "clocks": data["clock_count"], "outputs": list(outputs)}))


if __name__ == "__main__":
    main()
