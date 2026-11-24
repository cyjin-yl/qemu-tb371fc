#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate the bounded Kona LLCC reference tables from eight complete inputs.

Four-bank geometry is an explicit SM8250 source reference, not a reading of
the tablet's COMMON_STATUS0. HW_INFO and memory-controller identity remain
unknown. Writable masks describe this digital reference's accepted domain;
they are not an undocumented silicon reserved-bit or cache-data model.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
REPOSITORY = "OnePlusOSS/android_kernel_oneplus_sm8250"
UPSTREAM_REVISION = "adc218676eef25575469234709c2d87185ca223a"
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-llcc-data.inc",
           "tb371fc/docs/llcc-sm8250-registers.json")
SOURCE_PINS = (
    ("primary-r11", REPOSITORY, REVISION, "drivers/soc/qcom/llcc-slice.c", 10965,
     "a933f377db535f12f47942eff31fffe214af0912", "5a49480bebfa84ca94240202abc3fc3bc8eaf6379df9f4b71a92640e140eb1c5"),
    ("primary-r11", REPOSITORY, REVISION, "drivers/soc/qcom/llcc-kona.c", 3764,
     "a155d6365246ceaf2e8bca5a289ed72772cac74e", "140c5774139c0827003754bb083d0b6945fbd9982bc5538105084614eac1b2a9"),
    ("primary-r11", REPOSITORY, REVISION, "include/linux/soc/qcom/llcc-qcom.h", 5789,
     "f0e21085aec911e1f9d854fefd77418666e77d4d", "1d348978460ba7a32087af7567c80ddeaa98399855c719e7c0c2b1fcc4d7f093"),
    ("primary-r11", REPOSITORY, REVISION, "drivers/soc/qcom/llcc_perfmon.c", 37150,
     "99278c99cc311838d0b612bd5a96e908a5ec03ed", "38f1df996a946b0a6ce8f4cd71559367d1f4498d475605bc9089311d63defeeb"),
    ("primary-r11", REPOSITORY, REVISION, "drivers/soc/qcom/llcc_perfmon.h", 9820,
     "4a3accda43e9a2f681c118f43b502e13a52d2eba", "536de10d400093fa406b88f7a3ebfaf30966b43e15f88533be98c85ac5baebfd"),
    ("primary-r11", REPOSITORY, REVISION, "drivers/edac/qcom_edac.c", 11972,
     "560c96c1e5b88dc8b9b622ce4d3d590370470d7a", "ae3c0da558345d3c471fd35a5ae7f878a84c61d21093056d4ac66359f2486a24"),
    ("primary-r11", REPOSITORY, REVISION, "arch/arm64/boot/dts/vendor/qcom/kona.dtsi", 132499,
     "e9651f09bd579616787894dc8514429063623b2d", "12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55"),
    ("primary-upstream", "torvalds/linux", UPSTREAM_REVISION, "arch/arm64/boot/dts/qcom/sm8250.dtsi", 176883,
     "630f4eff20bf810e347685066dce74ab92a23b56", "8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499"),
)
FIELDS = ("usecase_id", "slice_id", "max_cap", "priority", "fixed_size",
          "bonus_ways", "res_ways", "cache_mode", "probe_target_ways",
          "dis_cap_alloc", "write_scid_en", "retain_on_pc", "activate_on_init")


def require(value, message):
    if not value:
        raise ValueError(message)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def load_sources(root):
    sources, pins = {}, []
    for prefix, repository, revision, path, size, blob, digest in SOURCE_PINS:
        local = prefix+"/"+path
        raw = (Path(root)/local).read_bytes()
        actual_blob = hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()
        require(len(raw) == size and sha256(raw) == digest and actual_blob == blob,
                "Complete source identity mismatch: "+local)
        sources[local] = raw.decode("utf-8")
        pins.append({"path": local, "source_path": path, "repository": repository,
                     "revision": revision, "bytes": size, "git_blob_sha": blob, "sha256": digest})
    return sources, pins


def body(source, name):
    match = re.search(r"\b"+re.escape(name)+r"\s*\([^;{}]*\)\s*\{", source)
    require(match is not None, "Missing source function "+name)
    start, level, end = match.end(), 1, match.end()
    while level and end < len(source):
        level += (source[end] == "{") - (source[end] == "}")
        end += 1
    require(level == 0, "Unbalanced source function "+name)
    return source[start:end-1]


def node(source, name):
    match = re.search(r"(?m)^\s*"+re.escape(name)+r"\s*\{([^{}]*)\};", source)
    require(match is not None, "Missing flat DTS node "+name)
    return match.group(1)


def literal(source, name):
    match = re.search(r"(?m)^#define\s+"+re.escape(name)+r"\s+\(?\s*(0x[0-9a-fA-F]+|[0-9]+)\s*\)?\s*$", source)
    require(match is not None, "Missing literal macro "+name)
    return int(match.group(1), 0)


def compact(text):
    return re.sub(r"\s+", "", text)


def geometry(sources):
    downstream = node(sources["primary-r11/arch/arm64/boot/dts/vendor/qcom/kona.dtsi"], "cache-controller@9200000")
    require('compatible = "qcom,llcc-v2";' in downstream, "Wrong legacy compatible")
    require("cap-based-alloc-and-pwr-collapse;" in downstream, "Missing legacy capacity/power flag")
    downstream_reg = re.search(r"\breg\s*=\s*(.*?);", downstream, re.S)
    require(downstream_reg is not None, "Missing legacy resources")
    values = [int(x, 16) for x in re.findall(r"0x[0-9a-fA-F]+", downstream_reg.group(1))]
    require(values == [0x9200000, 0x1d0000, 0x9600000, 0x50000], "Legacy combined resource changed")
    require(re.search(r'reg-names\s*=\s*"llcc_base",\s*"llcc_broadcast_base"', downstream), "Legacy reg names changed")
    require(not re.search(r"\b(interrupts|clocks)\s*=", downstream), "Unexpected LLCC clock or IRQ resource")
    upstream = node(sources["primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi"], "system-cache-controller@9200000")
    require('compatible = "qcom,sm8250-llcc";' in upstream, "Wrong upstream geometry target")
    reg = re.search(r"\breg\s*=\s*(.*?);", upstream, re.S)
    names = re.search(r"\breg-names\s*=\s*(.*?);", upstream, re.S)
    require(reg is not None and names is not None, "Missing upstream geometry")
    cells = [[int(x, 0) for x in re.findall(r"0x[0-9a-fA-F]+|\b[0-9]+\b", group)]
             for group in re.findall(r"<([^>]+)>", reg.group(1))]
    expected = [[0, base, 0, 0x50000] for base in (0x9200000, 0x9280000, 0x9300000, 0x9380000, 0x9600000)]
    require(cells == expected, "Upstream four-bank physical geometry changed")
    require(re.findall(r'"([^"]+)"', names.group(1)) ==
            ["llcc0_base", "llcc1_base", "llcc2_base", "llcc3_base", "llcc_broadcast_base"],
            "Upstream four-bank names changed")
    return {"bank_count": 4, "bank_stride": 0x80000, "bank_resource_bytes": 0x50000,
            "bank_bases": [r[1] for r in cells[:4]], "broadcast_base": cells[4][1],
            "regions": [{"name": "llcc_base", "base": values[0], "bytes": values[1]},
                        {"name": "llcc_broadcast_base", "base": values[2], "bytes": values[3]}],
            "original_physical_common_status_measured": False,
            "four_banks_are_source_bound_reference_geometry": True}


def source_semantics(sources):
    main = sources["primary-r11/drivers/soc/qcom/llcc-slice.c"]
    perf = sources["primary-r11/drivers/soc/qcom/llcc_perfmon.c"]
    ph = sources["primary-r11/drivers/soc/qcom/llcc_perfmon.h"]
    edac = sources["primary-r11/drivers/edac/qcom_edac.c"]
    require(literal(main, "LLCC_COMMON_STATUS0") == 0x3000c and
            literal(main, "LLCC_LB_CNT_SHIFT") == 28 and
            literal(main, "BANK_OFFSET_STRIDE") == 0x80000 and
            literal(main, "CACHE_LINE_SIZE_SHIFT") == 6, "Core geometry/line fields changed")
    require("GENMASK(31,28)" in compact(main), "Core bank field width changed")
    for name, expression in (
            ("LLCC_TRP_ACT_CTRLn(n)", "(n*SZ_4K)"),
            ("LLCC_TRP_STATUSn(n)", "(4+n*SZ_4K)"),
            ("LLCC_TRP_ATTR0_CFGn(n)", "(0x21000+SZ_8*n)"),
            ("LLCC_TRP_ATTR1_CFGn(n)", "(0x21004+SZ_8*n)")):
        require("#define"+name+expression in compact(main), "Core register formula changed: "+name)
    configure = compact(body(main, "qcom_llcc_cfg_program"))
    for text in ("max_cap_cacheline=max_cap_cacheline/drv_data->num_banks;",
                 "max_cap_cacheline>>=CACHE_LINE_SIZE_SHIFT;",
                 "attr1_val|=max_cap_cacheline<<ATTR1_MAX_CAP_SHIFT;",
                 "wren|=llcc_table[i].write_scid_en<<llcc_table[i].slice_id;",
                 "cad|=llcc_table[i].dis_cap_alloc<<llcc_table[i].slice_id;",
                 "pcb|=llcc_table[i].retain_on_pc<<llcc_table[i].slice_id;"):
        require(text in configure, "Source attribute/global programming changed")
    activate = compact(body(main, "llcc_update_act_ctrl"))
    require("act_ctrl_reg_val|=ACT_CTRL_ACT_TRIG;" in activate and
            "act_ctrl_reg_val&=~ACT_CTRL_ACT_TRIG;" in activate and
            "!(slice_status&status)" in activate, "Source activation/poll edge changed")
    require(literal(main, "LLCC_STATUS_READ_DELAY") == 100, "Source poll timeout changed")
    probe = body(main, "qcom_llcc_probe")
    require('"qcom_llcc_edac"' in probe and '"qcom_llcc_perfmon"' in probe and
            "platform_get_irq(pdev, 0)" in probe, "Source child instantiation changed")
    perf_probe = body(perf, "llcc_perfmon_probe")
    reads = re.findall(r"llcc_bcast_read\s*\(\s*llcc_priv\s*,\s*(\w+)", perf_probe)
    require(reads == ["LLCC_COMMON_STATUS0", "LLCC_COMMON_HW_INFO"], "Perfmon probe reads changed")
    require("llcc_priv->expires = 0;" in perf_probe and "hrtimer_start(" not in perf_probe,
            "Perfmon probe unexpectedly starts a counter timer")
    require(literal(ph, "LLCC_COMMON_HW_INFO") == 0x30000 and
            literal(ph, "LLCC_COMMON_STATUS0") == 0x3000c and
            literal(ph, "NUM_MC_SHIFT") == 10, "Perfmon identity field offsets changed")
    ep = body(edac, "qcom_llcc_edac_probe")
    require("if (ecc_irq < 0)" in ep and "if (ecc_irq >= 0)" in ep and
            "edev_ctl->edac_check = qcom_llcc_poll_cache_errors;" in ep,
            "No-IRQ EDAC polling path changed")
    handler = body(edac, "llcc_ecc_irq_handler")
    require("drv->offsets[i] + DRP_INTERRUPT_STATUS" in handler and
            "drv->offsets[i] + TRP_INTERRUPT_0_STATUS" in handler, "EDAC per-bank poll words changed")
    require(literal(edac, "DRP_INTERRUPT_STATUS") == 0x41000 and
            literal(edac, "TRP_INTERRUPT_0_STATUS") == 0x20480, "EDAC poll offsets changed")
    return {"source_activation_assert_then_deassert": True, "source_poll_timeout_us": 100,
            "source_cache_line_bytes": 64, "perfmon_probe_read_offsets": [0x3000c, 0x30000],
            "perfmon_probe_starts_counter": False, "edac_no_irq_poll_interval_ms": 5000,
            "edac_no_error_poll_offsets_per_bank": [0x41000, 0x20480],
            "perfmon_runtime_counters_implemented": False, "cache_data_ecc_datapath_implemented": False,
            "hardware_version_known": False, "memory_controller_count_known": False}


def parse_slices(sources, bank_count):
    header = sources["primary-r11/include/linux/soc/qcom/llcc-qcom.h"]
    source = sources["primary-r11/drivers/soc/qcom/llcc-kona.c"]
    ids = {name: int(value) for name, value in re.findall(r"(?m)^#define\s+(LLCC_\w+)\s+(\d+)\s*$", header)}
    match = re.search(r"kona_data\[\]\s*=\s*\{(.*?)\};", source, re.S)
    require(match is not None, "Missing Kona SCT array")
    entries = re.findall(r"SCT_ENTRY\(([^)]+)\)", match.group(1))
    require(len(entries) == 16, "Wrong Kona SCT row count")
    slices, raw_rows = [], []
    for index, text in enumerate(entries):
        tokens = [x.strip() for x in text.split(",")]
        require(len(tokens) == 13 and tokens[0] in ids, "Invalid Kona SCT row")
        values = [ids[tokens[0]]] + [int(x, 0) for x in tokens[1:]]
        row = dict(zip(FIELDS, values))
        require(0 <= row["slice_id"] < 32 and row["max_cap"] > 0 and
                row["priority"] in (1, 3) and row["cache_mode"] in (0, 1),
                "Kona source exceeds reviewed reference domain")
        for key in ("fixed_size", "probe_target_ways", "dis_cap_alloc", "write_scid_en", "retain_on_pc", "activate_on_init"):
            require(row[key] in (0, 1), "Invalid source boolean "+key)
        cap_bytes = row["max_cap"] * 1024
        require(cap_bytes % (bank_count*64) == 0, "Unrepresentable per-bank capacity")
        attr0 = (row["res_ways"] & 0xfff) | (row["bonus_ways"] << 16)
        attr1 = (row["cache_mode"] | row["probe_target_ways"] << 2 |
                 row["fixed_size"] << 3 | row["priority"] << 4 |
                 (cap_bytes // bank_count // 64) << 16)
        require(attr0 & ~0x0fff0fff == 0 and attr1 & ~0xffff003d == 0, "Generated attribute exceeds reference mask")
        slices.append(dict(row, index=index, name=tokens[0], attr0=attr0, attr1=attr1,
                           per_bank_max_capacity_cachelines=cap_bytes//bank_count//64))
        raw_rows.append(struct.pack("<IIIIB3xIIII4B", *values))
    require(len({s["slice_id"] for s in slices}) == 16, "Duplicate source SID")
    require([s["slice_id"] for s in slices if s["activate_on_init"]] == [1, 13, 31], "Source init slice set changed")
    require(slices[-1]["name"] == "LLCC_CVPFW" and slices[-1]["usecase_id"] == 32 and slices[-1]["slice_id"] == 17,
            "Source usecase/SID distinction changed")
    return slices, b"".join(raw_rows)


def register_table(sources, slices):
    main = sources["primary-r11/drivers/soc/qcom/llcc-slice.c"]
    mask = sum(1 << s["slice_id"] for s in slices)
    regs = []
    def add(name, offset, write_mask, kind, slice_index=255):
        regs.append({"name": name, "offset": offset, "write_mask": write_mask, "kind": kind, "slice": slice_index})
    for s in slices:
        sid, index = s["slice_id"], s["index"]
        add("LLCC_TRP_ACT_CTRL"+str(sid), sid*0x1000, 7, "CTRL", index)
        add("LLCC_TRP_STATUS"+str(sid), sid*0x1000+4, 0, "STATUS", index)
        add("LLCC_TRP_ATTR0_CFG"+str(sid), 0x21000+8*sid, 0x0fff0fff, "ATTR0", index)
        add("LLCC_TRP_ATTR1_CFG"+str(sid), 0x21004+8*sid, 0xffff003d, "ATTR1", index)
    for name, value, kind in (("LLCC_TRP_SCID_DIS_CAP_ALLOC", 0x21f00, "CAD"),
                               ("LLCC_TRP_PCB_ACT", 0x21f04, "PCB"),
                               ("LLCC_TRP_WRSC_EN", 0x21f20, "WRSC")):
        require(literal(main, name) == value, "Global offset changed: "+name)
        add(name, value, mask, kind)
    add("LLCC_COMMON_HW_INFO", 0x30000, 0, "HW_INFO")
    add("LLCC_COMMON_STATUS0", 0x3000c, 0, "COMMON_STATUS")
    add("TRP_INTERRUPT_0_STATUS", 0x20480, 0, "TRP_ERROR")
    add("DRP_INTERRUPT_STATUS", 0x41000, 0, "DRP_ERROR")
    regs.sort(key=lambda r: r["offset"])
    require(len(regs) == len({r["offset"] for r in regs}) == 71, "Canonical descriptor cardinality")
    return regs, mask


def make_windows(regs, geo):
    words, windows = [], []
    for region, bank in [(0, x) for x in range(4)] + [(1, 255)]:
        selected = [r for r in regs if (r["kind"] != "HW_INFO" if region == 0 else r["kind"] not in ("TRP_ERROR", "DRP_ERROR"))]
        for r in selected:
            offset = r["offset"] + (bank*geo["bank_stride"] if region == 0 else 0)
            require(offset+4 <= geo["regions"][region]["bytes"], "Word outside original resource")
            words.append({"region": region, "bank": bank, "offset": offset, "logical_offset": r["offset"], "name": r["name"]})
            if windows and (windows[-1]["region"], windows[-1]["bank"], windows[-1]["offset"]+windows[-1]["size"]) == (region, bank, offset):
                windows[-1]["size"] += 4
            else:
                windows.append({"region": region, "bank": bank, "offset": offset, "size": 4})
    require(len(words) == 349 and len(windows) == 139 and sum(w["size"] for w in windows) == 1396, "Exact exposed word/leaf/byte contract")
    return words, windows


def emit_data(manifest):
    lines = ["/* Generated by tb371fc/scripts/generate_llcc_sm8250.py; do not edit. */",
             "/* Eight exact source pins. Reference geometry is not a physical status measurement. */",
             "static const LlccSliceDef llcc_slices[LLCC_SLICES] = {"]
    for s in manifest["slices"]:
        lines.append('    { "%s", %d, 0x%08x, 0x%08x, %d, %d, %d, %d },' %
                     (s["name"], s["slice_id"], s["attr0"], s["attr1"], s["write_scid_en"],
                      s["dis_cap_alloc"], s["retain_on_pc"], s["activate_on_init"]))
    lines += ["};", "", "static const LlccRegDef llcc_regs[LLCC_REGISTERS] = {"]
    for r in manifest["registers"]:
        lines.append('    { "%s", 0x%05x, 0x%08x, LLCC_REG_%s, %d },' %
                     (r["name"], r["offset"], r["write_mask"], r["kind"], r["slice"]))
    lines += ["};", "", "const LlccWindowDef llcc_windows[LLCC_WINDOWS] = {"]
    for w in manifest["windows"]:
        lines.append("    { %d, %d, 0x%06x, 0x%x }," % (w["region"], w["bank"], w["offset"], w["size"]))
    return "\n".join(lines+["};", ""])


def generate(source_root):
    sources, pins = load_sources(source_root)
    geo = geometry(sources)
    semantics = source_semantics(sources)
    slices, raw_table = parse_slices(sources, geo["bank_count"])
    regs, mask = register_table(sources, slices)
    words, windows = make_windows(regs, geo)
    manifest = {"schema": "tb371fc-llcc-sm8250-registers-v1", "source_input_count": len(pins),
        "sources": pins, "source_revision": REVISION, "geometry_source_revision": UPSTREAM_REVISION,
        "bank_count": 4, "slice_count": 16, "register_count": 71, "region_count": 2,
        "mapped_register_count": 349, "window_count": 139, "mapped_bytes": 1396,
        "geometry": geo, "source_semantics": semantics, "supported_sid_mask": mask,
        "source_table_aarch64_layout": "<IIIIB3xIIII4B", "source_table_row_bytes": 40,
        "source_table_bytes": len(raw_table), "source_table_sha256": sha256(raw_table),
        "source_init_slice_ids": [s["slice_id"] for s in slices if s["activate_on_init"]],
        "source_final_masks": {name: sum(s[field] << s["slice_id"] for s in slices)
                              for name, field in (("CAD", "dis_cap_alloc"), ("PCB", "retain_on_pc"), ("WRSC", "write_scid_en"))},
        "reference_mask_scope": "Supported reference writes, not a complete silicon reserved-bit specification",
        "slices": slices, "registers": regs, "exposed_words": words, "windows": windows}
    require(len(pins) == 8 and len(raw_table) == 640, "Input/source-table cardinality")
    return emit_data(manifest).encode(), (json.dumps(manifest, indent=2)+"\n").encode()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path,
                        help="Contains primary-r11/ and primary-upstream/ with exact source bytes")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    for relative, raw in zip(OUTPUTS, generate(args.source_root)):
        target = args.output_root/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        print(json.dumps({"path": relative, "bytes": len(raw), "sha256": sha256(raw)}))
