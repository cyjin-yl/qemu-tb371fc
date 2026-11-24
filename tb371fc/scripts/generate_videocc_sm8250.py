#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate bounded Kona-v2 VIDEOCC facts from hash-pinned primary sources."""
import argparse
import hashlib
import json
from pathlib import Path
import re

REVISION = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_HASHES = {
    "drivers/clk/clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2",
    "drivers/clk/qcom/videocc-kona.c": "33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5",
    "include/dt-bindings/clock/qcom,videocc-kona.h": "0f51753e9eb497399d3c2d9a362e6d3ee7baf4f789b6e6628a7911b00f89d774",
    "drivers/clk/qcom/clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
    "drivers/clk/qcom/clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
    "drivers/clk/qcom/clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
    "drivers/clk/qcom/clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
    "drivers/clk/qcom/clk-branch.h": "da132bd1a093a650abf64073a4e695719e0612b67bb0166726737e0f2a3332d5",
    "drivers/clk/qcom/reset.c": "db88de0848c75cecff57623b94d63993a8f869a27686df4bd7b7be0765317f11",
    "drivers/clk/qcom/clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
}
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "core_bi_pll_test_se", "chip_sleep_clk")
PF = ("MODE", "L", "CAL_L", "USER", "USER_U", "USER_U1", "CONFIG",
      "CONFIG_U", "CONFIG_U1", "TEST", "TEST_U", "TEST_U1", "OPMODE", "ALPHA")
OFFSET_FIELDS = dict(zip(PF, ("MODE", "L_VAL", "CAL_L_VAL", "USER_CTL",
    "USER_CTL_U", "USER_CTL_U1", "CONFIG_CTL", "CONFIG_CTL_U", "CONFIG_CTL_U1",
    "TEST_CTL", "TEST_CTL_U", "TEST_CTL_U1", "OPMODE", "ALPHA_VAL")))
GDSC = (0xbf8, 0xc98, 0xd18, 0xd98)
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-videocc-data.inc",
           "tb371fc/docs/videocc-sm8250-registers.json")
CONFIG_FIELDS = {
    "l": "L", "cal_l": "CAL_L", "alpha": "ALPHA",
    "config_ctl_val": "CONFIG", "config_ctl_hi_val": "CONFIG_U",
    "config_ctl_hi1_val": "CONFIG_U1", "user_ctl_val": "USER",
    "user_ctl_hi_val": "USER_U", "user_ctl_hi1_val": "USER_U1",
}


def field(body, name, default=None):
    match = re.search(r"\." + re.escape(name) + r"\s*=\s*([^,\n]+)", body)
    return match.group(1).strip() if match else default


def number(body, name, default=None):
    value = field(body, name)
    return int(value, 0) if value is not None else default


def blocks(source, typename):
    return [(match.group(1), match.group(2), source[:match.start()].count("\n") + 1)
            for match in re.finditer(r"^static (?:const )?struct " + typename +
                                     r" (\w+)(?:\[\])? = \{(.*?)^\};",
                                     source, re.M | re.S)]


def variant_facts(source, alpha):
    """Keep requested probe writes separate from modeled cold register state."""
    configurations = {}
    for name, body, line in blocks(source, "alpha_pll_config"):
        declared = dict(re.findall(r"\.(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", body))
        assert set(declared) == set(CONFIG_FIELDS)
        values = {CONFIG_FIELDS[key]: int(declared[key], 0) for key in CONFIG_FIELDS}
        configurations[name] = {
            "source_line": line, "declared_register_values": values,
            # clk_lucid_pll_configure skips zero-valued configuration fields.
            "conditional_nonzero_writes": [
                {"field": key, "value": value} for key, value in values.items() if value],
        }
    assert set(configurations) == {
        "video_pll0_config", "video_pll1_config", "video_pll1_config_sm8250_v2"}
    assert configurations["video_pll1_config"]["declared_register_values"]["CONFIG_U1"] == 0x029a699c
    assert configurations["video_pll1_config_sm8250_v2"]["declared_register_values"]["CONFIG_U1"] == 0x329a699c
    helper = re.search(r"^void clk_lucid_pll_configure\(.*?^\}", alpha, re.M | re.S).group()
    assert "if (lucid_pll_is_enabled(pll, regmap))" in helper
    for name in CONFIG_FIELDS:
        assert "if (config->" + name + ")" in helper
    calls = r"clk_lucid_pll_configure\(&([a-z0-9_]+),\s*regmap,\s*&([a-z0-9_]+)\)"
    probe = re.search(r"^static int video_cc_kona_probe\(.*?^\}", source, re.M | re.S).group()
    fixup = re.search(r"^static void video_cc_kona_fixup_konav2\(.*?^\}", source, re.M | re.S).group()
    base_calls = re.findall(calls, probe)
    fixup_calls = re.findall(calls, fixup)
    assert base_calls == [("video_pll0", "video_pll0_config"),
                          ("video_pll1", "video_pll1_config")]
    assert fixup_calls == [("video_pll1", "video_pll1_config_sm8250_v2")]
    assert (probe.index("&video_pll0_config") < probe.index("&video_pll1_config") <
            probe.index("video_cc_kona_fixup_konav2(regmap)") <
            probe.index("qcom_cc_really_probe"))
    assert re.search(r'of_device_is_compatible\([^;]+"qcom,videocc-kona-v2"\)\)\s*'
                     r"video_cc_kona_fixup_konav2\(regmap\)", probe)
    table = re.search(r"video_cc_mvs1_clk_src\.freq_tbl\s*=\s*(\w+)", fixup).group(1)
    assert table == "ftbl_video_cc_mvs1_clk_src_kona_v2"
    rate_max = {level: int(rate) for level, rate in re.findall(
        r"rate_max\[(\w+)\]\s*=\s*(\d+)", fixup)}
    assert rate_max == {"VDD_LOWER": 840000000, "VDD_LOW": 1098000000}
    return {"configurations": configurations,
            "probe_sequence": [
                {"phase": phase, "pll": pll, "configuration": config}
                for phase, items in (("base", base_calls), ("kona-v2-fixup", fixup_calls))
                for pll, config in items],
            "configure_skips_running_pll": True,
            "configure_tail": ["MODE.UPDATE_BYPASS=1", "MODE.OUTCTRL=0",
                               "OPMODE=STANDBY", "MODE.RESET_N=1"],
            "v2_frequency_table": table, "v2_rate_max": rate_max}


def inventory(directory):
    sources = {}
    for path, expected in SOURCE_HASHES.items():
        raw = (directory / path).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(f"Source pin mismatch: {path}: {actual}")
        sources[path] = raw.decode("utf-8")
    source = sources["drivers/clk/qcom/videocc-kona.c"]
    binding = sources["include/dt-bindings/clock/qcom,videocc-kona.h"]
    alpha = sources["drivers/clk/qcom/clk-alpha-pll.c"]
    variant = variant_facts(source, alpha)
    frequency_tables = {}
    for name, body, line in blocks(source, "freq_tbl"):
        entries = [{"frequency_hz": int(rate), "parent_symbol": parent,
                    "pre_divisor": int(divisor), "m": int(m), "n": int(n)}
                   for rate, parent, divisor, m, n in re.findall(
                       r"F\(\s*(\d+),\s*(\w+),\s*(\d+),\s*(\d+),\s*(\d+)\)", body)]
        assert entries and all(row["pre_divisor"] == 1 and row["m"] == row["n"] == 0
                               for row in entries)
        frequency_tables[name] = {"source_line": line, "entries": entries}
    assert len(frequency_tables) == 5
    vco = re.findall(r"\{\s*(\d+),\s*(\d+),\s*(\d+)\s*\}", blocks(source, "pll_vco")[0][1])
    assert vco == [("249600000", "2000000000", "0")]
    assert re.search(r"#define\s+BRANCH_HALT_VOTED\s+\(BRANCH_HALT \| BRANCH_VOTED\)",
                     sources["drivers/clk/qcom/clk-branch.h"])
    assert "mask = BIT(map->bit);" in sources["drivers/clk/qcom/reset.c"]
    assert 'DEFINE_CLK_RPMH_ARC(kona, bi_tcxo, bi_tcxo_ao, "xo.lvl", 0x3, 2);' in sources["drivers/clk/qcom/clk-rpmh.c"]
    ids = {name: int(value) for name, value in re.findall(
        r"^#define\s+(\w+)\s+(\d+)\s*$", binding, re.M)}
    array = re.search(r"static struct clk_regmap \*video_cc_kona_clocks\[\] = \{(.*?)^\};",
                      source, re.M | re.S).group(1)
    clocks = {name: {"id": ids[symbol], "binding": symbol, "name": name}
              for symbol, name in re.findall(r"\[(\w+)\]\s*=\s*&(\w+)\.clkr", array)}
    assert len(clocks) == 19 and {v["id"] for v in clocks.values()} == set(range(19))
    parents = {name: re.findall(r'"([^"]+)"', body) for name, body in re.findall(
        r"static const char \* const (\w+)\[\] = \{(.*?)^\};", source, re.M | re.S)}
    maps = {name: [(symbol, int(selector, 0)) for symbol, selector in re.findall(
        r"\{\s*(\w+),\s*(0x[0-9a-fA-F]+|\d+)\s*\}", body)]
        for name, body in re.findall(r"static const struct parent_map (\w+)\[\] = \{(.*?)^\};",
                                     source, re.M | re.S)}

    def parent_names(body):
        inline = re.search(r"\.parent_names\s*=\s*\(const char \*\[\]\)\{(.*?)\}", body, re.S)
        return re.findall(r'"([^"]+)"', inline.group(1)) if inline else parents.get(field(body, "parent_names"), [])

    def parent_id(name):
        if name in clocks:
            return clocks[name]["id"]
        if name not in INPUTS:
            raise ValueError("Unreviewed external clock: " + name)
        return -INPUTS.index(name) - 1

    registers = {}

    def add(offset, write, status, kind, index, member, store=None):
        assert 0 <= offset < 0x10000 and offset % 4 == 0
        assert offset not in GDSC and offset not in registers
        registers[offset] = {"offset": offset, "write_mask": write,
                             "status_mask": status, "stored_mask": write if store is None else store,
                             "kind": kind, "index": index, "field": member}

    plls, rcgs, branches, dividers = [], [], [], []
    pll_blocks = sorted(blocks(source, "clk_alpha_pll"), key=lambda x: number(x[1], "offset"))
    for index, (name, body, line) in enumerate(pll_blocks):
        base = number(body, "offset")
        kind = "LUCID"
        assert field(body, "ops") == "&clk_alpha_pll_" + kind.lower() + "_ops"
        assert parent_names(body) == ["bi_tcxo"]
        table = re.search(r"\[CLK_ALPHA_PLL_TYPE_" + kind + r"\]\s*=\s*\{(.*?)\n\s*\},",
                          alpha, re.S).group(1)
        relative = {key: int(value, 0) for key, value in re.findall(
            r"\[PLL_OFF_(\w+)\]\s*=\s*(0x[0-9a-fA-F]+)", table)}
        relative["MODE"] = 0
        own = {}
        for pf, member in enumerate(PF):
            if OFFSET_FIELDS[member] not in relative:
                continue
            offset = base + relative[OFFSET_FIELDS[member]]
            own[member] = offset
            write, status = 0xffffffff, 0
            if member == "MODE":
                write, status = 0xc00007, 0xa8000000
            elif member in ("L", "CAL_L", "ALPHA"):
                write = 0xffff
            elif member == "USER":
                write = 7
            elif member == "OPMODE":
                write = 1
            add(offset, write, status, "PLL", index, pf)
        assert len(own) == 14
        plls.append({"name": name, "clock_id": clocks[name]["id"], "kind": kind,
                     "base": base, "source_line": line, "registers": own})
        clocks[name].update(kind=kind, source_line=line, parents=["bi_tcxo"])
    assert [p["base"] for p in plls] == [0x42c, 0x7d0]

    assert not blocks(source, "clk_alpha_pll_postdiv")

    for index, (name, body, line) in enumerate(blocks(source, "clk_rcg2")):
        base, width = number(body, "cmd_rcgr"), number(body, "mnd_width")
        assert width == 0 and number(body, "hid_width") == 5
        assert field(body, "ops") == "&clk_rcg2_ops"
        names, mapping = parent_names(body), maps[field(body, "parent_map")]
        assert len(names) == len(mapping) and len({sel for _, sel in mapping}) == len(mapping)
        selectors = [-127] * 8
        for parent, (_, selector) in zip(names, mapping):
            selectors[selector] = parent_id(parent)
        offsets = [base, base + 4]
        add(base, 3, 0x80000000, "RCG", index, 0)
        hw_control = field(body, "flags") == "HW_CLK_CTRL_MODE"
        cfg_mask = 0x10371f if hw_control else 0x371f
        add(base + 4, cfg_mask, 0, "RCG", index, 1)
        rcgs.append({"name": name, "clock_id": clocks[name]["id"], "base": base,
                     "mnd_width": width, "hid_width": 5,
                     "cfg_mask": cfg_mask, "hw_control": hw_control,
                     "source_ops": field(body, "ops"), "source_line": line, "registers": offsets,
                     "parents": names, "selector_clock_ids": selectors,
                     "frequency_table": field(body, "freq_tbl"),
                     "active_frequency_table": (variant["v2_frequency_table"]
                         if name == "video_cc_mvs1_clk_src" else field(body, "freq_tbl")),
                     "critical": "CLK_IS_CRITICAL" in body,
                     "enable_safe_config": field(body, "enable_safe_config") == "true"})
        clocks[name].update(kind="RCG", source_line=line, parents=names)

    for index, (name, body, line) in enumerate(sorted(blocks(source, "clk_branch"), key=lambda row: clocks[row[0]]["id"])):
        offset = number(body, "enable_reg")
        assert offset == number(body, "halt_reg") and field(body, "enable_mask") == "BIT(0)"
        assert field(body, "hwcg_reg") is None and field(body, "hwcg_bit") is None
        halt = field(body, "halt_check")
        assert halt in ("BRANCH_HALT", "BRANCH_HALT_VOTED")
        declared_parents = parent_names(body)
        parent, = declared_parents
        vote_index = (sum(item["remote_vote_index"] >= 0 for item in branches)
                      if halt == "BRANCH_HALT_VOTED" else -1)
        add(offset, 1, 0x80000000, "BRANCH", index, 0)
        branches.append({"name": name, "clock_id": clocks[name]["id"], "offset": offset,
                         "parent": parent, "parent_clock_id": parent_id(parent),
                         "halt_check": halt, "critical": "CLK_IS_CRITICAL" in body,
                         "parent_declared": bool(declared_parents), "remote_vote_index": vote_index,
                         "source_line": line})
        clocks[name].update(kind="BRANCH", source_line=line, parents=declared_parents)

    for index, (name, body, line) in enumerate(blocks(source, "clk_regmap_div")):
        ops = field(body, "ops")
        assert ops == "&clk_regmap_div_ro_ops"
        read_only = True
        declaration = body.split(".clkr", 1)[0]
        assert field(declaration, "flags") is None and field(declaration, "table") is None
        offset, shift, width = (number(body, key) for key in ("reg", "shift", "width"))
        assert shift == 0 and width == 2
        parent, = parent_names(body)
        add(offset, 0, 0, "DIVIDER", index, 0, 3)
        dividers.append({"name": name, "clock_id": clocks[name]["id"], "offset": offset,
                         "parent": parent, "parent_clock_id": parent_id(parent),
                         "shift": shift, "width": width, "read_only": read_only,
                         "divisor_encoding": "register_field + 1",
                         "source_line": line})
        clocks[name].update(kind="DIVIDER", source_line=line, parents=[parent])

    reset_body = blocks(source, "qcom_reset_map")[0][1]
    resets = []
    for name, offset, bit in re.findall(
            r"\[(\w+)\]\s*=\s*\{\s*(0x[0-9a-fA-F]+)(?:\s*,\s*(\d+))?\s*\}",
            reset_body):
        bit = int(bit or "0")
        resets.append({"name": name, "id": ids[name], "offset": int(offset, 0),
                       "bit": bit, "mask": 1 << bit})
    resets.sort(key=lambda item: item["id"])
    for index, reset in enumerate(resets):
        assert reset["id"] == index
        offset, mask = reset["offset"], reset["mask"]
        if offset in registers:
            # ARES shares the branch word, not its enable bit or ownership.
            register = registers[offset]
            assert register["kind"] == "BRANCH" and mask == 4
            assert offset in (0xc34, 0xcd4)
            register["write_mask"] |= mask
            register["stored_mask"] |= mask
            register["shared_reset_id"] = index
            reset["shared_with_branch"] = branches[register["index"]]["name"]
        else:
            assert mask == 1
            add(offset, mask, 0, "RESET", index, 0)
            reset["shared_with_branch"] = None
    assert (len(plls), len(rcgs), len(branches), len(dividers), len(resets)) == (2, 5, 8, 4, 7)
    assert [b["offset"] for b in branches if b["remote_vote_index"] >= 0] == [
        0xd34, 0xdb4, 0xdf4, 0xcd4]
    assert [r["name"] for r in rcgs if r["hw_control"]] == [
        "video_cc_ahb_clk_src", "video_cc_mvs0_clk_src", "video_cc_mvs1_clk_src"]
    assert [(r["offset"], r["bit"]) for r in resets if r["shared_with_branch"]] == [
        (0xc34, 2), (0xcd4, 2)]
    ordered = [registers[offset] for offset in sorted(registers)]
    lookup = {item["offset"]: i for i, item in enumerate(ordered)}
    for pll in plls:
        pll["register_indices"] = [lookup[pll["registers"][name]] if name in pll["registers"] else -1 for name in PF]
    for rcg in rcgs:
        rcg["register_indices"] = [lookup[offset] for offset in rcg["registers"]]
    for items in (branches, dividers, resets):
        for item in items:
            item["register_index"] = lookup[item["offset"]]
    windows = []
    for item in ordered:
        offset = item["offset"]
        if windows and windows[-1]["offset"] + windows[-1]["size"] == offset:
            windows[-1]["size"] += 4
        else:
            windows.append({"offset": offset, "size": 4})
    assert len(ordered) == 55 and len(windows) == 26
    assert sum(w["size"] for w in windows) == 220
    assert not any(w["offset"] <= gdsc < w["offset"] + w["size"] for w in windows for gdsc in GDSC)
    assert all("kind" in item for item in clocks.values())
    return {"schema": "tb371fc-videocc-source-facts-v1", "source_revision": REVISION,
            "source_hashes": SOURCE_HASHES, "actual_dtb_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
            "actual_compatible": "qcom,videocc-kona-v2", "physical_base": 0x0abf0000,
            "resource_bytes": 0x10000, "registers": ordered, "windows": windows,
            "mapped_bytes": 220, "excluded_gdsc_offsets": list(GDSC),
            "dt_cfg_ahb_clock": {"provider_phandle": 22, "vendor_binding_id": 205},
            "dt_regulator_phandles": {"vdd_mx": 105, "vdd_mm": 104},
            "inputs": list(INPUTS), "clocks": sorted(clocks.values(), key=lambda item: item["id"]),
            "xo_input_provenance": {"names": list(INPUTS[:2]), "parent": "xo_board",
                                    "rpmh_resource": "xo.lvl", "parent_divisor": 2},
            "unresolved_board_inputs": list(INPUTS[2:]),
            "pll_vco_range_hz": [int(vco[0][0]), int(vco[0][1])],
            "variant": variant, "frequency_tables": frequency_tables,
            "plls": plls, "rcgs": rcgs,
            "branches": branches, "dividers": dividers, "resets": resets,
            "limits": ["No opaque PLL STATUS semantics or analog behavior",
                       "No fixed DIV2 factor inferred from a clock name",
                       "No invented sleep or test clock rate",
                       "No physical video codec, firmware or DMA execution"]}


def c_data(data):
    lines = ["/* SPDX-License-Identifier: GPL-2.0-only */",
             "/* Generated from pinned OnePlus Kona clock/register declarations.",
             " * Source copyright (c) 2018-2019, The Linux Foundation.",
             " * Regenerate with tb371fc/scripts/generate_videocc_sm8250.py.",
             " */", "static const char *const videocc_input_names[VIDEOCC_INPUTS] = {"]
    lines += [f'    "{name}",' for name in data["inputs"]]
    lines += ["};", "static const char *const videocc_clock_names[VIDEOCC_CLOCKS] = {"]
    lines += [f'    [{item["id"]}] = "{item["name"]}",' for item in data["clocks"]]
    lines += ["};", "static const VideoccRegister videocc_registers[VIDEOCC_REGISTERS] = {"]
    for item in data["registers"]:
        lines.append("    { 0x%04x, 0x%08x, 0x%08x, 0x%08x," %
                     tuple(item[key] for key in ("offset", "write_mask", "status_mask", "stored_mask")))
        lines.append("      REG_%s, %u, %u }," % (item["kind"], item["index"], item["field"]))
    lines += ["};", "static const VideoccPll videocc_plls[VIDEOCC_PLLS] = {"]
    for pll in data["plls"]:
        values = pll["register_indices"]
        lines += ["    { %u," % pll["clock_id"],
                  "      { " + ", ".join(str(n) for n in values[:7]) + ",",
                  "        " + ", ".join(str(n) for n in values[7:]) + " } },"]
    lines += ["};", "static const VideoccRcg videocc_rcgs[VIDEOCC_RCGS] = {"]
    for item in data["rcgs"]:
        lines += ["    { %u, 0x%x, { %s }," % (item["clock_id"], item["cfg_mask"],
                  ", ".join(str(n) for n in item["register_indices"])),
                  "      { " + ", ".join(str(n) for n in item["selector_clock_ids"]) + " } },"]
    lines += ["};", "static const VideoccBranch videocc_branches[VIDEOCC_BRANCHES] = {"]
    for item in data["branches"]:
        lines.append("    { %u, %u, %d, %d }," % (item["clock_id"], item["register_index"], item["parent_clock_id"], item["remote_vote_index"]))
    lines += ["};", "static const VideoccDivider videocc_dividers[VIDEOCC_DIVIDERS] = {"]
    for item in data["dividers"]:
        lines.append("    { %u, %u, %d, %u, %u }," % (
            item["clock_id"], item["register_index"], item["parent_clock_id"],
            item["shift"], item["width"]))
    lines += ["};", "static const VideoccReset videocc_resets[VIDEOCC_RESETS] = {"]
    for item in data["resets"]:
        lines.append("    { %u, 0x%x }," % (item["register_index"], item["mask"]))
    lines += ["};", "const QcomVideoccWindowDef qcom_sm8250_videocc_windows[VIDEOCC_WINDOWS] = {"]
    for item in data["windows"]:
        lines.append("    { 0x%04x, 0x%x }," % (item["offset"], item["size"]))
    lines += ["};", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    data = inventory(args.source_root)
    contents = (c_data(data), json.dumps(data, indent=2) + "\n")
    for name, content in zip(OUTPUTS, contents):
        path = args.output_root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    print(json.dumps({"registers": len(data["registers"]), "windows": len(data["windows"]),
                      "clocks": len(data["clocks"]), "inputs": len(data["inputs"]),
                      "mapped_bytes": data["mapped_bytes"], "source_pins_verified": True}))


if __name__ == "__main__":
    main()
