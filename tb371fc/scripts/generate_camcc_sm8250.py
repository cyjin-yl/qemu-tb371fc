#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate bounded Kona-v2 CAMCC facts from hash-pinned primary sources."""
import argparse
import hashlib
import json
from pathlib import Path
import re

REVISION = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_HASHES = {
    "drivers/clk/clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2",
    "drivers/clk/qcom/camcc-kona.c": "60fa0934e7ec2ad37ade0b710430d137bf1f46008e7115c2d77569f34edd50f9",
    "include/dt-bindings/clock/qcom,camcc-kona.h": "3ea08de9ae18dd90e151407309017b3347950835883a79b0a80179fce23212a1",
    "drivers/clk/qcom/clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
    "drivers/clk/qcom/clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
    "drivers/clk/qcom/clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
    "drivers/clk/qcom/clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
    "drivers/clk/qcom/clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
}
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "core_bi_pll_test_se", "chip_sleep_clk",
          "cam_cc_pll2_out_aux2")
PF = ("MODE", "L", "CAL_L", "USER", "USER_U", "USER_U1", "CONFIG",
      "CONFIG_U", "CONFIG_U1", "TEST", "TEST_U", "TEST_U1", "OPMODE", "ALPHA")
OFFSET_FIELDS = dict(zip(PF, ("MODE", "L_VAL", "CAL_L_VAL", "USER_CTL",
    "USER_CTL_U", "USER_CTL_U1", "CONFIG_CTL", "CONFIG_CTL_U", "CONFIG_CTL_U1",
    "TEST_CTL", "TEST_CTL_U", "TEST_CTL_U1", "OPMODE", "ALPHA_VAL")))
GDSC = (0x7004, 0x8004, 0x9004, 0xa004, 0xb004, 0xc144)
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-camcc-data.inc",
           "tb371fc/docs/camcc-sm8250-registers.json")


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


def inventory(directory):
    sources = {}
    for path, expected in SOURCE_HASHES.items():
        raw = (directory / path).read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(f"Source pin mismatch: {path}: {actual}")
        sources[path] = raw.decode("utf-8")
    source = sources["drivers/clk/qcom/camcc-kona.c"]
    binding = sources["include/dt-bindings/clock/qcom,camcc-kona.h"]
    alpha = sources["drivers/clk/qcom/clk-alpha-pll.c"]
    ids = {name: int(value) for name, value in re.findall(
        r"^#define\s+(\w+)\s+(\d+)\s*$", binding, re.M)}
    array = re.search(r"static struct clk_regmap \*cam_cc_kona_clocks\[\] = \{(.*?)^\};",
                      source, re.M | re.S).group(1)
    clocks = {name: {"id": ids[symbol], "binding": symbol, "name": name}
              for symbol, name in re.findall(r"\[(\w+)\]\s*=\s*&(\w+)\.clkr", array)}
    assert len(clocks) == 111 and {v["id"] for v in clocks.values()} == set(range(111))
    parents = {name: re.findall(r'"([^"]+)"', body) for name, body in re.findall(
        r"static const char \* const (\w+)\[\] = \{(.*?)^\};", source, re.M | re.S)}
    maps = {name: [(symbol, int(selector, 0)) for symbol, selector in re.findall(
        r"\{\s*(\w+),\s*(0x[0-9a-fA-F]+|\d+)\s*\}", body)]
        for name, body in re.findall(r"static const struct parent_map (\w+)\[\] = \{(.*?)^\};",
                                     source, re.M | re.S)}

    def parent_names(body):
        inline = re.search(r"\.parent_names\s*=\s*\(const char \*\[\]\)\{(.*?)\}", body, re.S)
        return re.findall(r'"([^"]+)"', inline.group(1)) if inline else parents[field(body, "parent_names")]

    def parent_id(name):
        if name in clocks:
            return clocks[name]["id"]
        if name not in INPUTS:
            raise ValueError("Unreviewed external clock: " + name)
        return -INPUTS.index(name) - 1

    registers = {}

    def add(offset, write, status, kind, index, member, store=None):
        assert offset % 4 == 0 and offset not in GDSC and offset not in registers
        registers[offset] = {"offset": offset, "write_mask": write,
                             "status_mask": status, "stored_mask": write if store is None else store,
                             "kind": kind, "index": index, "field": member}

    plls, postdivs, rcgs, branches, dividers = [], [], [], [], []
    pll_blocks = sorted(blocks(source, "clk_alpha_pll"), key=lambda x: number(x[1], "offset"))
    for index, (name, body, line) in enumerate(pll_blocks):
        base = number(body, "offset")
        kind = "ZONDA" if index == 2 else "LUCID"
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
                write, status = (7, 0xa0000000) if kind == "ZONDA" else (0xc00007, 0xa8000000)
            elif member in ("L", "CAL_L") or member == "ALPHA" and kind == "LUCID":
                write = 0xffff
            elif member == "USER":
                write = 0x30f if kind == "ZONDA" else 0xff07 if index == 0 else 0xf07
            elif member == "OPMODE":
                write = 1
            add(offset, write, status, "PLL", index, pf)
        assert len(own) == (11 if kind == "ZONDA" else 14)
        plls.append({"name": name, "clock_id": clocks[name]["id"], "kind": kind,
                     "base": base, "source_line": line, "registers": own})
        clocks[name].update(kind=kind, source_line=line, parents=["bi_tcxo"])
    assert [p["base"] for p in plls] == [0, 0x1000, 0x2000, 0x3000, 0x4000]

    divider_tables = {name: [(int(value, 0), int(divisor, 0)) for value, divisor in
                             re.findall(r"\{\s*(0x[0-9a-fA-F]+|\d+)\s*,\s*(\d+)\s*\}", body)]
                      for name, body, _ in blocks(source, "clk_div_table")}
    for name, body, line in sorted(blocks(source, "clk_alpha_pll_postdiv"), key=lambda row: clocks[row[0]]["id"]):
        parent, = parent_names(body)
        index = next(i for i, pll in enumerate(plls) if pll["name"] == parent)
        table = divider_tables[field(body, "post_div_table")]
        assert len(table) == 1 and table[0][0] and table[0][1]
        postdivs.append({"name": name, "clock_id": clocks[name]["id"], "pll_index": index,
                        "shift": number(body, "post_div_shift"), "width": number(body, "width"),
                        "selector": table[0][0], "divisor": table[0][1], "source_line": line})
        clocks[name].update(kind="POSTDIV", source_line=line, parents=[parent])

    for index, (name, body, line) in enumerate(blocks(source, "clk_rcg2")):
        base, width = number(body, "cmd_rcgr"), number(body, "mnd_width")
        assert width in (0, 8) and number(body, "hid_width") == 5
        assert field(body, "ops") == "&clk_rcg2_ops"
        names, mapping = parent_names(body), maps[field(body, "parent_map")]
        assert len(names) == len(mapping) and len({sel for _, sel in mapping}) == len(mapping)
        selectors = [-127] * 8
        for parent, (_, selector) in zip(names, mapping):
            selectors[selector] = parent_id(parent)
        offsets = [base, base + 4]
        add(base, 3, 0x80000000, "RCG", index, 0)
        add(base + 4, 0x10371f, 0, "RCG", index, 1)
        for member in range(2, 5):
            if width:
                offset = base + 4 * member
                add(offset, 0xff, 0, "RCG", index, member)
                offsets.append(offset)
        rcgs.append({"name": name, "clock_id": clocks[name]["id"], "base": base,
                     "mnd_width": width, "source_line": line, "registers": offsets,
                     "parents": names, "selector_clock_ids": selectors,
                     "enable_safe_config": field(body, "enable_safe_config") == "true"})
        clocks[name].update(kind="RCG", source_line=line, parents=names)

    for index, (name, body, line) in enumerate(sorted(blocks(source, "clk_branch"), key=lambda row: clocks[row[0]]["id"])):
        offset = number(body, "enable_reg")
        assert offset == number(body, "halt_reg") and field(body, "enable_mask") == "BIT(0)"
        assert field(body, "hwcg_reg") is None and field(body, "hwcg_bit") is None
        halt = field(body, "halt_check")
        assert halt in ("BRANCH_HALT", "BRANCH_HALT_DELAY")
        parent, = parent_names(body)
        add(offset, 1, 0x80000000, "BRANCH", index, 0)
        branches.append({"name": name, "clock_id": clocks[name]["id"], "offset": offset,
                         "parent": parent, "parent_clock_id": parent_id(parent),
                         "halt_check": halt, "critical": "CLK_IS_CRITICAL" in body,
                         "source_line": line})
        clocks[name].update(kind="BRANCH", source_line=line, parents=[parent])

    for index, (name, body, line) in enumerate(blocks(source, "clk_regmap_div")):
        assert name == "cam_cc_sbi_div_clk_src" and field(body, "ops") == "&clk_regmap_div_ro_ops"
        offset, shift, width = (number(body, key) for key in ("reg", "shift", "width"))
        assert (offset, shift, width) == (0x9010, 0, 3)
        parent, = parent_names(body)
        add(offset, 0, 0, "DIVIDER", index, 0, 7)
        dividers.append({"name": name, "clock_id": clocks[name]["id"], "offset": offset,
                         "parent": parent, "parent_clock_id": parent_id(parent),
                         "shift": shift, "width": width, "source_line": line})
        clocks[name].update(kind="DIVIDER", source_line=line, parents=[parent])

    reset_body = blocks(source, "qcom_reset_map")[0][1]
    resets = [{"name": name, "id": ids[name], "offset": int(offset, 0), "mask": 1}
              for name, offset in re.findall(r"\[(\w+)\]\s*=\s*\{\s*(0x[0-9a-fA-F]+)\s*\}", reset_body)]
    resets.sort(key=lambda item: item["id"])
    for index, reset in enumerate(resets):
        assert reset["id"] == index
        add(reset["offset"], 1, 0, "RESET", index, 0)
    assert (len(plls), len(postdivs), len(rcgs), len(branches), len(dividers), len(resets)) == (5, 6, 33, 66, 1, 6)
    ordered = [registers[offset] for offset in sorted(registers)]
    lookup = {item["offset"]: i for i, item in enumerate(ordered)}
    for pll in plls:
        pll["register_indices"] = [lookup[pll["registers"][name]] if name in pll["registers"] else -1 for name in PF]
    for rcg in rcgs:
        rcg["register_indices"] = [lookup[offset] for offset in rcg["registers"]] + [-1] * (5 - len(rcg["registers"]))
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
    assert len(ordered) == 236 and len(windows) == 74
    assert sum(w["size"] for w in windows) == 944
    assert all("kind" in item for item in clocks.values())
    return {"schema": "tb371fc-camcc-source-facts-v1", "source_revision": REVISION,
            "source_hashes": SOURCE_HASHES, "actual_dtb_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
            "actual_compatible": "qcom,camcc-kona-v2", "physical_base": 0x0ad00000,
            "resource_bytes": 0x10000, "registers": ordered, "windows": windows,
            "mapped_bytes": 944, "excluded_gdsc_offsets": list(GDSC),
            "inputs": list(INPUTS), "clocks": sorted(clocks.values(), key=lambda item: item["id"]),
            "plls": plls, "postdividers": postdivs, "rcgs": rcgs,
            "branches": branches, "dividers": dividers, "resets": resets,
            "limits": ["No opaque PLL STATUS semantics", "No Zonda CFA or FSM voting",
                       "Only explicitly listed nonzero postdivider selectors produce outputs",
                       "No invented sleep/test/PLL2-aux2 clock", "No physical analog or camera execution"]}


def c_data(data):
    lines = ["/* SPDX-License-Identifier: GPL-2.0-only */",
             "/* Generated from pinned OnePlus Kona clock/register declarations.",
             " * Source copyright (c) 2018-2019, The Linux Foundation.",
             " * Regenerate with tb371fc/scripts/generate_camcc_sm8250.py.",
             " */", "static const char *const camcc_input_names[CAMCC_INPUTS] = {"]
    lines += [f'    "{name}",' for name in data["inputs"]]
    lines += ["};", "static const char *const camcc_clock_names[CAMCC_CLOCKS] = {"]
    lines += [f'    [{item["id"]}] = "{item["name"]}",' for item in data["clocks"]]
    lines += ["};", "static const CamccRegister camcc_registers[CAMCC_REGISTERS] = {"]
    for item in data["registers"]:
        lines.append("    { 0x%04x, 0x%08x, 0x%08x, 0x%08x," %
                     tuple(item[key] for key in ("offset", "write_mask", "status_mask", "stored_mask")))
        lines.append("      REG_%s, %u, %u }," % (item["kind"], item["index"], item["field"]))
    lines += ["};", "static const CamccPll camcc_plls[CAMCC_PLLS] = {"]
    for pll in data["plls"]:
        values = pll["register_indices"]
        lines += ["    { %u, PLL_%s," % (pll["clock_id"], pll["kind"]),
                  "      { " + ", ".join(str(n) for n in values[:7]) + ",",
                  "        " + ", ".join(str(n) for n in values[7:]) + " } },"]
    lines += ["};", "static const CamccPostdiv camcc_postdivs[CAMCC_POSTDIVS] = {"]
    for item in data["postdividers"]:
        lines.append("    { %u, %u, %u, %u, %u, %u }," % tuple(
            item[key] for key in ("clock_id", "pll_index", "shift", "width", "selector", "divisor")))
    lines += ["};", "static const CamccRcg camcc_rcgs[CAMCC_RCGS] = {"]
    for item in data["rcgs"]:
        lines += ["    { %u, %u, { %s }," % (item["clock_id"], item["mnd_width"],
                  ", ".join(str(n) for n in item["register_indices"])),
                  "      { " + ", ".join(str(n) for n in item["selector_clock_ids"]) + " } },"]
    lines += ["};", "static const CamccBranch camcc_branches[CAMCC_BRANCHES] = {"]
    for item in data["branches"]:
        lines.append("    { %u, %u, %d }," % (item["clock_id"], item["register_index"], item["parent_clock_id"]))
    lines += ["};", "static const CamccDivider camcc_sbi_divider = {"]
    item, = data["dividers"]
    lines += ["    %u, %u, %d, %u, %u," % tuple(item[key] for key in
              ("clock_id", "register_index", "parent_clock_id", "shift", "width")),
              "};", "static const CamccReset camcc_resets[CAMCC_RESETS] = {"]
    for item in data["resets"]:
        lines.append("    { %u, 0x%x }," % (item["register_index"], item["mask"]))
    lines += ["};", "const QcomCamccWindowDef qcom_sm8250_camcc_windows[CAMCC_WINDOWS] = {"]
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
