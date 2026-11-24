#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate the bounded v4.1 DSI PLL/shared-PHY inventory from pinned sources."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PINS = ROOT / "tb371fc/evidence/dsi-pll7nm-20261124-e1a9/source-pins.json"
OUTPUTS = ("hw/misc/qcom-sm8250-dsi-pll7nm-data.inc",
           "tb371fc/docs/dsi-pll7nm-sm8250-registers.json")
SHARED = {"PHY_CMN_CLK_CFG0": 0xff, "PHY_CMN_CLK_CFG1": 0x3f,
          "PHY_CMN_GLBL_CTRL": 0x40, "PHY_CMN_RBUF_CTRL": 1,
          "PHY_CMN_CTRL_0": 0x7f, "PHY_CMN_CTRL_2": 0x40,
          "PHY_CMN_CTRL_3": 4, "PHY_CMN_PLL_CNTRL": 1,
          "PHY_CMN_GLBL_DIGTOP_SPARE4": 1}
SUFFIXES = ("vco", "pll_out", "bitclk", "post_bit_div", "post_vco_div",
            "cphy_post_vco_div3_5", "phy_pll_out_byteclk", "phy_pll_out_dsiclk")
VARIABLE = {"PLL_SYSTEM_MUXES", "PLL_DECIMAL_DIV_START_1",
            "PLL_FRAC_DIV_START_LOW_1", "PLL_FRAC_DIV_START_MID_1",
            "PLL_FRAC_DIV_START_HIGH_1", "PLL_PLL_OUTDIV_RATE",
            "PLL_CLOCK_INVERTERS_1", "PLL_VCO_CONFIG_1",
            "PLL_CMODE_1", "PLL_ANALOG_CONTROLS_FIVE_1", "PLL_SSC_CONTROL"}
VARIABLE |= {"PLL_SSC_" + field + "_1" for field in
             ("STEPSIZE_LOW", "STEPSIZE_HIGH", "DIV_PER_LOW", "DIV_PER_HIGH",
              "ADJPER_LOW", "ADJPER_HIGH")}


def function(source, name):
    match = re.search(r"\b" + name + r"\([^;{}]*\)\s*\{", source)
    if not match:
        raise ValueError("Missing source function: " + name)
    start, depth, end = match.end(), 1, match.end()
    while depth:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    return source[start:end-1]


def literal_writes(body):
    return [(name, int(value, 0)) for name, value in re.findall(
        r"MDSS_PLL_REG_W\(pll_base,\s*(PLL_\w+),\s*(0x[0-9a-fA-F]+|\d+)\)", body)]


def inventory(module_root, core_root):
    pins = json.loads(PINS.read_text())
    sources = {}
    for group, directory in (("module", module_root), ("core", core_root)):
        for path, pin in pins[group]["files"].items():
            raw = (directory / path).read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            if len(raw) != pin["bytes"] or hashlib.sha256(raw).hexdigest() != pin["sha256"] or blob != pin["git_blob"]:
                raise ValueError("Source pin mismatch: " + path)
            sources[path] = raw.decode()
    source = sources["kernel/msm-4.19/techpack/display/pll/dsi_pll_7nm.c"]
    binding = sources["include/dt-bindings/clock/mdss-7nm-pll-clk.h"]
    ids = {name: int(value) for name, value in re.findall(r"^#define\s+(\w+)\s+(\d+)\s*$", binding.split("/* DP PLL clocks */")[0], re.M)}
    assert len(ids) == 52 and set(ids.values()) == set(range(52))
    offsets = {name: int(value, 16) for name, value in re.findall(
        r"^#define\s+(PLL_\w+|PHY_CMN_\w+)\s+(0x[0-9A-Fa-f]+)\s*$", source, re.M)}
    written = set(re.findall(r"MDSS_PLL_REG_W\(\s*(?:pll_base|rsc->pll_base|rsc->slave->pll_base),\s*(PLL_\w+)", source))
    assert len(written) == 145 and offsets["PLL_PERF_OPTIMIZE"] == 0x260
    init = literal_writes(function(source, "dsi_pll_init_val"))
    # The preceding 0x66 write is conditional on old v1 hardware, not v4.1.
    init = [row for row in init if row != ("PLL_BIAS_CONTROL_1", 0x66)]
    assert len(init) == 130 and len(dict(init)) == 130
    fixed = dict(init)
    for name, value in literal_writes(function(source, "dsi_pll_config_hzindep_reg")):
        if name != "PLL_GEAR_BAND_SELECT_CONTROLS":  # conditional old v1 0x21
            fixed[name] = value
    fixed.update(dict(literal_writes(function(source, "dsi_pll_commit"))))
    for name in VARIABLE:
        fixed.pop(name, None)
    unknown_status = {name for name in written if
        "STATUS" in name or name in ("PLL_BAND_SEL_CAL", "PLL_FD_OUT_LOW", "PLL_FD_OUT_HIGH",
        "PLL_LOCKTIME0", "PLL_LOCKTIME1", "PLL_SW_RESET") or re.fullmatch(r"PLL_DEBUG_BUS[0-3]", name)}
    unknown_status.discard("PLL_COMMON_STATUS_ONE")
    registers = []
    for name, mask in SHARED.items():
        registers.append({"name": name, "offset": offsets[name], "write_mask": mask,
                          "kind": "REG_PHY", "recipe": None})
    for name in written:
        kind = ("REG_LOCK" if name == "PLL_COMMON_STATUS_ONE" else
                "REG_UNKNOWN_ZERO" if name in unknown_status else "REG_PLL")
        mask = 0 if kind != "REG_PLL" else 0xff
        if name in ("PLL_FRAC_DIV_START_HIGH_1", "PLL_PLL_OUTDIV_RATE", "PLL_SSC_CONTROL"):
            mask = 3
        registers.append({"name": name, "offset": 0x500 + offsets[name],
                          "write_mask": mask, "kind": kind,
                          "recipe": fixed.get(name) if kind == "REG_PLL" else None})
    registers.sort(key=lambda row: row["offset"])
    assert len(registers) == 154 and len({r["offset"] for r in registers}) == 154
    windows = []
    for row in registers:
        if windows and windows[-1][0] + windows[-1][1] == row["offset"]:
            windows[-1][1] += 4
        else:
            windows.append([row["offset"], 4])
    recalc = function(source, "vco_7nm_recalc_rate")
    detect = function(source, "dsi_pll_detect_phy_mode")
    assert recalc.index("dsi_pll_detect_phy_mode") < recalc.index("dsi_pll_7nm_lock_status")
    assert "PHY_CMN_GLBL_CTRL" in detect and "BIT(6)" in detect
    assert "600000000" in source and "5000000000" in source
    assert "config->frac_bits = 18" in source and "divider = fref * 2" in source
    assert "val = 3;" in function(source, "cphy_pclk_mux_write_sel")
    factors = {}
    for name, body in re.findall(r"static struct clk_fixed_factor (dsi[01]pll_\w+) = \{(.*?)^\};", source, re.M | re.S):
        factors[name] = {field: int(re.search(r"\." + field + r"\s*=\s*(\d+)", body).group(1))
                         for field in ("div", "mult")}
    for bank in range(2):
        for suffix, div, mult in (("byteclk_src", 8, 1), ("cphy_byteclk_src", 7, 1),
                                  ("post_bit_div", 2, 1), ("post_vco_div", 4, 1),
                                  ("post_vco_div3_5", 7, 2)):
            assert factors[f"dsi{bank}pll_{suffix}"] == {"div": div, "mult": mult}
    divider = sources["drivers/clk/clk-divider.c"]
    assert "return parent_rate" in function(divider, "divider_recalc_rate")
    facts = {
        "schema": "tb371fc-dsi-pll7nm-source-facts-v1", "source_pins": pins,
        "actual_compatible": "qcom,mdss_dsi_pll_7nm_v4_1", "phy_compatible": "qcom,dsi-phy-v4.1",
        "phy_bases": [0x0ae94400, 0x0ae96400], "dynamic_bases": [0x0ae94200, 0x0ae96200],
        "phy_size": 0x800, "actual_phy_driver_size": 0x7c0, "pll_subresource_size": 0x260,
        "pll_offset": 0x500, "registers_per_bank": 154, "registers": registers,
        "windows": windows, "dynamic_words": [{"offset": 0, "write_mask": 0,
        "rule": "read zero, accept only zero write; all nonzero controls rejected atomically"}],
        "binding_ids": ids, "physical_clock_names": [f"dsi{p}_{suffix}" for p in range(2) for suffix in SUFFIXES],
        "source_fixed_factors": factors,
        "physical_clock_formulas_per_bank": ["floor(38400000*q/262144)",
            "ceil(vco/2^OUTDIV)", "ceil(pll_out/max(CFG0[3:0],1))",
            "floor(bitclk/2)", "floor(pll_out/4)", "floor(2*pll_out/7)",
            "DPHY floor(bitclk/8), CPHY floor(bitclk/7); gated",
            "ceil((DPHY selector0:bitclk,selector1:post_bit; CPHY selector3:cphy_post)/max(CFG0[7:4],1)); gated"],
        "physical_gate": "CFG1 bit5, CTRL3=4, SPARE4=0, internal route; CLK_EN_SEL bit4 is resync control, not a permanent clock gate",
        "pixel_mux_does_not_gate_byte": True,
        "software_aliases_are_hardware_state": False, "shadow_sequence_supported": False,
        "external_route_tap_known": False, "unknown_phy_offsets": [0, 0x140, 0x148, 0x14c],
        "initialization_writes": [{"name": n, "offset": 0x500+offsets[n], "value": v} for n, v in init],
        "vco_min_hz": 600000000, "vco_max_hz": 5000000000, "reference_hz": 19200000,
        "factor_fraction_bits": 18, "prescaler": 2,
        "recipe_request_buckets": [
            [600000000, 1000000000, 0xa0, 8, 1],
            [1000000001, 1519999999, 0x20, 8, 1],
            [1520000000, 2500000000, 0x20, 1, 1],
            [2500000001, 2989999999, 0, 1, 1],
            [2990000000, 3020000000, 0, 0, 1],
            [3020000001, 3099999999, 0x40, 0, 1],
            [3100000000, 5000000000, 0x40, 0, 3]],
        "recipe_quantization_rule": "intersect requested integer Hz bucket with [ceil(q*38400000/262144), ceil((q+1)*38400000/262144)-1]",
        "ssc_default": {"frequency": 31500, "ppm": 4800, "adjustment_period": 2,
                        "period": 302, "center_values": [0, 1], "nominal_carrier_only": True},
        "reference_timing_ns": {"lock": 5000},
        "lock_requires": ["enabled explicit reference", "19.2 MHz XO", "known on MDSS GDSC",
            "explicit known ready REFGEN", "complete source v4.1 recipe", "PHY start and bias/system mux"],
        "reference_reset": "all modeled digital words zero; DPHY mode; unlocked; not captured firmware handoff",
        "unknown_states": ["REFGEN without provider", "PHY revision", "ULPS", "lane stopstate",
            "analog calibration status", "dynamic sequencer", "DSI controller/panel", "external-route tap"],
    }
    return facts


def render(facts):
    lines = ["/* SPDX-License-Identifier: GPL-2.0-only */",
             "/* Generated from hash-pinned Qualcomm display v4.1 declarations.",
             " * Source copyright (c) 2017-2020, The Linux Foundation.",
             " * Regenerate with tb371fc/scripts/generate_dsi_pll7nm_sm8250.py. */",
             "#define DSI_DPHY_BYTE_DIV 8", "#define DSI_CPHY_BYTE_DIV 7",
             "enum DsiPll7nmRegisterIndex {"]
    for index, row in enumerate(facts["registers"]):
        lines.append(f'    R_{row["name"]} = {index},')
    lines += ["};", "static const DsiPll7nmRegister dsi_registers[DSI_PLL7NM_REGISTERS_PER_BANK] = {"]
    for row in facts["registers"]:
        recipe = "-1" if row["recipe"] is None else f'0x{row["recipe"]:02x}'
        lines.append(f'    {{ 0x{row["offset"]:03x}, 0x{row["write_mask"]:02x}, {row["kind"]}, {recipe} }},')
    lines += ["};", "static const DsiPll7nmWindowDef dsi_windows[DSI_PLL7NM_WINDOWS_PER_BANK] = {"]
    lines += [f"    {{ 0x{offset:03x}, 0x{size:x} }}," for offset, size in facts["windows"]]
    lines += ["};", "static const char *const dsi_clock_names[DSI_PLL7NM_CLOCKS] = {"]
    lines += [f'    "{name}",' for name in facts["physical_clock_names"]]
    lines += ["};", "static const DsiPll7nmRecipeBucket dsi_recipe_buckets[] = {"]
    lines += [f"    {{ {lo}ULL, {hi}ULL, 0x{inv:02x}, 0x{vco:02x}, 0x{analog:02x} }},"
              for lo, hi, inv, vco, analog in facts["recipe_request_buckets"]]
    lines += ["};", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--module-root", type=Path, required=True)
    parser.add_argument("--core-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    facts = inventory(args.module_root, args.core_root)
    for relative, content in zip(OUTPUTS, (render(facts), json.dumps(facts, indent=2) + "\n")):
        path = args.output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print(json.dumps({"registers_per_bank": 154, "windows_per_bank": len(facts["windows"]),
                      "clocks": 16, "binding_ids": 52, "source_pins": 13}))


if __name__ == "__main__":
    main()
