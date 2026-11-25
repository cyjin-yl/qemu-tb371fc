#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive one DDR statistics offset word from four complete pinned sources.

The vendor consumer adds a firmware-provided word to its DT resource base.
The upstream SM8250 DT and its matching fixed RPMh DDR configuration identify
only a reference layout. No DDR payload, counters, initialization or timing
is implemented by the generated four-byte offset-word device.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

VENDOR_REPOSITORY = "OnePlusOSS/android_kernel_oneplus_sm8250"
VENDOR_REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
UPSTREAM_REPOSITORY = "torvalds/linux"
UPSTREAM_REVISION = "75f2c0b3690702c90863c2e138cb5520670845ea"
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-ddr-stats-offset-data.inc",
           "tb371fc/docs/ddr-stats-offset-sm8250-registers.json")
SOURCE_PINS = (
    ("primary-r11", VENDOR_REPOSITORY, VENDOR_REVISION,
     "drivers/soc/qcom/ddr_stats.c", 6442,
     "49b020908d7b3b22d0590eea466860c4d42a3a18",
     "26d08430d1ae76562118e313f91f285a3bce8b4a71175082aee6441b4dd3ebfb"),
    ("primary-r11", VENDOR_REPOSITORY, VENDOR_REVISION,
     "arch/arm64/boot/dts/vendor/qcom/kona-pm.dtsi", 2817,
     "a5eba34468ee25209d8f07baf5efe9c53462ec2a",
     "c1f3a89f461cf1381335ffb2e3066026c28a27642389f9f92fccf540646e5231"),
    ("primary-upstream", UPSTREAM_REPOSITORY, UPSTREAM_REVISION,
     "drivers/soc/qcom/qcom_stats.c", 11697,
     "2e380faf9080354fae120e74f0b9bd1f3786d3e5",
     "cf7447d3c9501203c699a403ea5f394ab9a809177f8b8b43e95699ff5dee5ed4"),
    ("primary-upstream", UPSTREAM_REPOSITORY, UPSTREAM_REVISION,
     "arch/arm64/boot/dts/qcom/sm8250.dtsi", 177763,
     "7076720413ab2c34aaab618a4dcf7a27beb6076e",
     "ec9a5a3b9d9425475bf2fdaad772395ef2b331a9a4f92ac139bf83d0cad79ad0"),
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def load_sources(source_root):
    texts, pins = [], []
    for prefix, repository, revision, relative, size, blob, expected in SOURCE_PINS:
        local = prefix + "/" + relative
        raw = (source_root / local).read_bytes()
        require((len(raw), git_blob(raw), digest(raw)) == (size, blob, expected),
                "Complete source identity mismatch: " + local)
        texts.append(raw.decode("utf-8"))
        pins.append({"path": local, "source_path": relative, "repository": repository,
                     "revision": revision, "bytes": size, "git_blob_sha": blob,
                     "sha256": expected,
                     "url": f"https://raw.githubusercontent.com/{repository}/{revision}/{relative}"})
    return texts, pins


def without_comments(text):
    # Quoted strings are retained, including escaped characters.
    pattern = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*'
    return re.sub(pattern, lambda m: m[0] if m[0].startswith('"') else " ",
                  text, flags=re.S)


def one(pattern, text, description, flags=0):
    matches = list(re.finditer(pattern, text, flags))
    require(len(matches) == 1, "Expected one " + description)
    return matches[0]


def block_after(source, pattern, description):
    """Return one balanced brace block; braces in string literals are ignored."""
    start = one(pattern, source, description).end()
    require(start > 0 and source[start - 1] == "{", "Block pattern must end in {")
    depth, quoted, escaped = 1, False, False
    for end in range(start, len(source)):
        char = source[end]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[start:end]
    raise ValueError("Unbalanced block: " + description)


def function_body(source, name):
    return block_after(source, r"\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{",
                       "definition of " + name)


def flat_node(source, name):
    return one(r"(?m)^\s*" + re.escape(name) + r"\s*\{([^{}]*)\};",
               source, "flat DTS node " + name).group(1)


def property_value(node, name):
    return one(r"(?m)^\s*" + re.escape(name) + r"\s*=\s*([^;]+);",
               node, "property " + name).group(1)


def direct_text(body):
    """Keep parent-level text while excluding nested DTS blocks."""
    output, depth, quoted, escaped = [], 0, False, False
    for char in body:
        if quoted:
            if depth == 0:
                output.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            if depth == 0:
                output.append(char)
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            require(depth >= 0, "Unbalanced parent DTS text")
        elif depth == 0 or char == "\n":
            output.append(char)
    require(depth == 0 and not quoted, "Unbalanced parent DTS body")
    return "".join(output)


def integer(token):
    require(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", token) is not None,
            "Only unsigned integer literals are supported")
    value = int(token, 16 if token.lower().startswith("0x") else 10)
    require(0 <= value <= 0xffffffff, "Integer literal exceeds uint32")
    return value


def cells(value):
    require(re.fullmatch(r"\s*<[^<>]+>(?:\s*,\s*<[^<>]+>)*\s*", value) is not None,
            "Unsupported DTS cell syntax")
    return [[integer(token) for token in group.split()]
            for group in re.findall(r"<([^>]+)>", value)]


def strings(value):
    require(re.fullmatch(r'\s*"[^"\\]*"(?:\s*,\s*"[^"\\]*")*\s*', value) is not None,
            "Unsupported DTS string syntax")
    return re.findall(r'"([^"\\]*)"', value)


def configuration_fields(body):
    fields, end = {}, 0
    pattern = r"\s*\.(\w+)\s*=\s*(true|false|0[xX][0-9a-fA-F]+|[0-9]+)\s*,"
    for match in re.finditer(pattern, body):
        require(not body[end:match.start()].strip(), "Unsupported configuration syntax")
        name, token = match.groups()
        require(name not in fields, "Duplicate configuration field: " + name)
        fields[name] = token == "true" if token in ("true", "false") else integer(token)
        end = match.end()
    require(not body[end:].strip(), "Unsupported trailing configuration syntax")
    return fields


def validate_vendor_consumer(driver):
    probe = function_body(driver, "ddr_stats_probe")
    compact = re.sub(r"\s+", "", probe)
    ordered = (
        'res=platform_get_resource_byname(pdev,IORESOURCE_MEM,"phys_addr_base");',
        'offset=platform_get_resource_byname(pdev,IORESOURCE_MEM,"offset_addr");',
        'offset_addr=readl_relaxed(phys_ptr);',
        'iounmap(phys_ptr);',
        'if(!offset_addr)return-ENODEV;',
        'pdata->phys_addr_base=res->start+offset_addr;',
        'pdata->phys_size=resource_size(res);',
        'returnddr_stats_create_sysfs(pdev,pdata);',
    )
    for fragment in ('u32offset_addr=0;', *ordered):
        require(compact.count(fragment) == 1,
                "Vendor probe address contract changed: " + fragment)
    require([compact.index(fragment) for fragment in ordered] ==
            sorted(compact.index(fragment) for fragment in ordered),
            "Vendor probe address operation order changed")
    offset_body = block_after(probe, r"\bif\s*\(\s*offset\s*\)\s*\{",
                              "optional offset resource branch")
    expected = ('phys_ptr=ioremap_nocache(offset->start,SZ_4);'
                'if(!phys_ptr){pr_err("Failed to ioremap offset address\\n");return-ENODEV;}'
                'offset_addr=readl_relaxed(phys_ptr);iounmap(phys_ptr);')
    require(re.sub(r"\s+", "", offset_body) == re.sub(r"\s+", "", expected),
            "Vendor offset branch is not the bounded map/read/unmap sequence")
    mmio = re.findall(r"\b((?:read|write)[bwlq](?:_relaxed)?|"
                      r"__raw_(?:read|write)[bwlq]|ioread\w*|iowrite\w*|"
                      r"memcpy_(?:from|to)io)\s*\(", probe)
    require(mmio == ["readl_relaxed"], "Vendor probe MMIO access count changed")
    return {"function": "ddr_stats_probe", "read_function": "readl_relaxed",
            "mapping_function": "ioremap_nocache", "mapping_size_token": "SZ_4",
            "direct_mmio_reads": 1, "direct_mmio_writes": 0,
            "zero_offset_returns": "-ENODEV", "payload_read_during_probe": False,
            "address_formula": "phys_addr_base resource start + uint32 offset word"}


def literal_define(source, name):
    token = one(r"(?m)^\s*#define\s+" + re.escape(name) +
                r"[ \t]+([^\n]+)$", source, "literal define " + name).group(1).strip()
    return integer(token)


def record_layout(source, name, expected):
    body = block_after(source, r"\bstruct\s+" + re.escape(name) + r"\s*\{",
                       "structure " + name)
    fields = re.findall(r"\b(u32|u64|uint32_t|uint64_t)\s+(\w+)\s*;", body)
    require(not re.sub(r"\b(?:u32|u64|uint32_t|uint64_t)\s+\w+\s*;", "", body).strip()
            and fields == expected, "Unsupported structure layout: " + name)
    size, alignment = 0, 1
    for kind, _ in fields:
        width = {"u32": 4, "u64": 8, "uint32_t": 4, "uint64_t": 8}[kind]
        size = (size + width - 1) // width * width + width
        alignment = max(alignment, width)
    return (size + alignment - 1) // alignment * alignment


def derive(vendor_driver_text, vendor_dts_text, upstream_driver_text, upstream_dts_text, pins):
    vendor, vendor_dts, upstream, upstream_dts = map(
        without_comments, (vendor_driver_text, vendor_dts_text,
                           upstream_driver_text, upstream_dts_text))
    consumer = validate_vendor_consumer(vendor)
    node = flat_node(vendor_dts, "qcom,ddr-stats@c3f0000")
    require(strings(property_value(node, "compatible")) == ["qcom,ddr-stats"],
            "Unexpected vendor DDR statistics compatible")
    names = strings(property_value(node, "reg-names"))
    reg = cells(property_value(node, "reg"))
    require(names == ["phys_addr_base", "offset_addr"] and len(reg) == 2 and
            all(len(pair) == 2 for pair in reg), "Unexpected vendor resource geometry")
    resource_base, resource_bytes = reg[0]
    word_base, word_bytes = reg[1]
    require(resource_base % 4 == 0 and resource_bytes > 0 and word_bytes == 4 and
            word_base % 4 == 0 and word_base == int("c3f001c", 16),
            "Unsupported four-byte DDR offset aperture")

    soc = block_after(upstream_dts, r"\bsoc\s*:\s*soc@0\s*\{", "upstream soc@0")
    parent = direct_text(soc)
    require(cells(property_value(parent, "#address-cells")) == [[2]] and
            cells(property_value(parent, "#size-cells")) == [[2]],
            "Upstream statistics parent cell geometry changed")
    stats_node = flat_node(soc, "sram@c3f0000")
    require(stats_node == flat_node(upstream_dts, "sram@c3f0000"),
            "Upstream statistics node is not unique in soc@0")
    compatible = strings(property_value(stats_node, "compatible"))
    require(compatible == ["qcom,rpmh-stats"], "Unsupported upstream statistics compatible")
    require(not re.search(r"\bqcom,qmp\b", stats_node),
            "This SM8250 reference does not establish QMP synchronization")
    stats_reg = cells(property_value(stats_node, "reg"))
    require(len(stats_reg) == 1 and len(stats_reg[0]) == 4,
            "Upstream resource must have two address and two size cells")
    hi, lo, size_hi, size_lo = stats_reg[0]
    stats_base, stats_bytes = (hi << 32) | lo, (size_hi << 32) | size_lo
    require(stats_base == int("c3f0000", 16) and stats_bytes > 0,
            "Upstream statistics unit address does not match reg")
    require(stats_base <= word_base and word_base + word_bytes <= stats_base + stats_bytes,
            "Offset word lies outside the source statistics region")

    match_table = block_after(
        upstream, r"\bstatic\s+const\s+struct\s+of_device_id\s+qcom_stats_table\s*\[\]\s*=\s*\{",
        "upstream qcom_stats_table")
    config_name = one(
        r'\{\s*\.compatible\s*=\s*"qcom,rpmh-stats"\s*,\s*\.data\s*=\s*&(\w+)\s*\}',
        match_table, "RPMh compatible configuration")[1]
    require(config_name == "rpmh_data", "RPMh compatible points at another profile")
    config_body = block_after(
        upstream, r"\bstatic\s+const\s+struct\s+stats_config\s+" +
        re.escape(config_name) + r"\s*=\s*\{", "fixed RPMh configuration")
    config = configuration_fields(config_body)
    require(set(config) == {"stats_offset", "ddr_stats_offset", "num_records",
                            "appended_stats_avail", "dynamic_offset", "subsystem_stats_in_smem"},
            "Unexpected RPMh configuration fields")
    require(type(config["stats_offset"]) is int and config["stats_offset"] == 0x48 and
            type(config["ddr_stats_offset"]) is int and
            type(config["num_records"]) is int and config["num_records"] == 3 and
            config["appended_stats_avail"] is False and config["dynamic_offset"] is False and
            config["subsystem_stats_in_smem"] is True,
            "RPMh fixed layout/profile contract changed")

    vendor_record_bytes = record_layout(vendor, "stats_entry", [
        ("uint32_t", "name"), ("uint32_t", "count"), ("uint64_t", "duration")])
    record_bytes = record_layout(upstream, "ddr_stats_entry", [
        ("u32", "name"), ("u32", "count"), ("u64", "duration")])
    require(record_bytes == vendor_record_bytes == 16, "DDR entry widths disagree")
    vendor_data = block_after(vendor, r"\bstruct\s+ddr_stats_data\s*\{",
                             "vendor DDR header and entry array")
    require(re.sub(r"\s+", "", vendor_data) ==
            "uint32_tkey;uint32_tentry_count;structstats_entryentry[MAX_NUM_MODES];",
            "Vendor DDR header layout changed")
    magic = literal_define(upstream, "DDR_STATS_MAGIC_KEY")
    maximum_records = literal_define(upstream, "DDR_STATS_MAX_NUM_MODES")
    key_offset = literal_define(upstream, "DDR_STATS_MAGIC_KEY_ADDR")
    count_offset = literal_define(upstream, "DDR_STATS_NUM_MODES_ADDR")
    header_bytes = literal_define(upstream, "DDR_STATS_ENTRY_START_ADDR")
    require(magic == literal_define(vendor, "MAGIC_KEY1") == 0xa1157a75 and
            maximum_records == literal_define(vendor, "MAX_NUM_MODES") == 20 and
            (key_offset, count_offset, header_bytes) == (0, 4, 8),
            "Vendor/upstream DDR header contracts disagree")
    create = re.sub(r"\s+", "", function_body(upstream, "qcom_create_ddr_stat_files"))
    expected_create = (
        'u32key;if(!config->ddr_stats_offset)return;'
        'key=readl_relaxed(reg+config->ddr_stats_offset+DDR_STATS_MAGIC_KEY_ADDR);'
        'if(key==DDR_STATS_MAGIC_KEY)debugfs_create_file("ddr_stats",0400,root,'
        '(__forcevoid*)reg+config->ddr_stats_offset,&qcom_ddr_stats_fops);')
    require(create == expected_create, "Upstream DDR offset/magic use changed")
    show = re.sub(r"\s+", "", function_body(upstream, "qcom_ddr_stats_show"))
    for fragment in ('entry_count=readl_relaxed(reg+DDR_STATS_NUM_MODES_ADDR);',
                     'if(entry_count>DDR_STATS_MAX_NUM_MODES)return-EINVAL;',
                     'reg+=DDR_STATS_ENTRY_START_ADDR;',
                     'memcpy_fromio(data,reg,sizeof(structddr_stats_entry)*entry_count);'):
        require(show.count(fragment) == 1, "Upstream DDR record use changed: " + fragment)
    probe = re.sub(r"\s+", "", function_body(upstream, "qcom_stats_probe"))
    for fragment in ('config=device_get_match_data(&pdev->dev);',
                     'reg=devm_platform_get_and_ioremap_resource(pdev,0,NULL);',
                     'qcom_create_ddr_stat_files(root,reg,config);'):
        require(probe.count(fragment) == 1, "Upstream probe binding changed: " + fragment)

    ddr_offset = config["ddr_stats_offset"]
    payload_bytes = header_bytes + maximum_records * record_bytes
    require(ddr_offset > 0 and ddr_offset % 8 == 0 and
            ddr_offset >= word_base + word_bytes - stats_base and
            ddr_offset + payload_bytes <= stats_bytes,
            "DDR reference span exceeds or overlaps the source pointer layout")
    payload_base = stats_base + ddr_offset
    value = payload_base - resource_base
    require(0 < value <= 0xffffffff and resource_base + value == payload_base,
            "Relative offset is not a nonzero vendor uint32 value")
    require(not (resource_base <= word_base < resource_base + resource_bytes) and
            not (resource_base <= payload_base < resource_base + resource_bytes),
            "Reference DDR statistics unexpectedly overlaps the mailbox resource")
    return {
        "schema": "tb371fc-ddr-stats-offset-registers-v1",
        "source_pins": pins, "source_input_count": len(pins),
        "vendor_repository": VENDOR_REPOSITORY, "vendor_revision": VENDOR_REVISION,
        "upstream_repository": UPSTREAM_REPOSITORY, "upstream_revision": UPSTREAM_REVISION,
        "register_count": 1, "window_count": 1,
        "physical_base": word_base, "mapped_bytes": word_bytes, "offset_value": value,
        "vendor_consumer": consumer,
        "vendor_resources": [
            {"name": names[0], "base": resource_base, "bytes": resource_bytes},
            {"name": names[1], "base": word_base, "bytes": word_bytes}],
        "upstream_layout": {
            "node": "sram@c3f0000", "compatible": compatible[0],
            "config_name": config_name, "config": config,
            "region_base": stats_base, "region_bytes": stats_bytes,
            "ddr_stats_offset": ddr_offset, "header_bytes": header_bytes,
            "maximum_records": maximum_records, "record_bytes": record_bytes,
            "payload_base": payload_base, "payload_bytes": payload_bytes,
            "payload_bytes_is_maximum_abi_span": True,
            "payload_mapped_by_this_device": False,
            "magic_key_is_source_ABI_only": magic,
            "cold_probe_differs_from_vendor_sysfs_consumer": True},
        "derivation": {
            "expression": "upstream region base + matching fixed DDR offset - vendor base",
            "hex_expression": f"0x{stats_base:08x} + 0x{ddr_offset:x} - "
                              f"0x{resource_base:08x} = 0x{value:08x}",
            "little_endian_bytes": value.to_bytes(4, "little").hex(),
            "classification": "explicit public SM8250 reference ABI layout",
            "captured_runtime_word_claimed": False},
        "reference_scope": {
            "default_enabled": False, "enabled_aligned_read32_supported": True,
            "reference_window_mapped_only_when_enabled": True,
            "default_off_mapped_bytes": 0,
            "writes_supported": False, "disabled_reads_supported": False,
            "statistics_payload_implemented": False, "statistics_counters_implemented": False,
            "statistics_names_implemented": False, "statistics_magic_implemented": False,
            "statistics_entry_count_implemented": False, "firmware_ready_implemented": False,
            "aop_execution_implemented": False, "irq_output_implemented": False,
            "clock_implemented": False, "timer_implemented": False,
            "rpm_statistics_pointer_implemented": False,
            "mailbox_aperture_modified": False, "native_reset_value_known": False,
            "whole_production_kernel_source_identity_claimed": False},
    }


def render_inc(manifest):
    values = (("BASE", f'UINT64_C(0x{manifest["physical_base"]:08x})'),
              ("WINDOW_BYTES", str(manifest["mapped_bytes"]) + "u"),
              ("VALUE", f'UINT32_C(0x{manifest["offset_value"]:08x})'))
    return ("/* SPDX-License-Identifier: GPL-2.0-or-later */\n"
            "/* Generated by tb371fc/scripts/gen_ddr_stats_offset.py; do not edit. */\n"
            "/* Four pinned public inputs; fixed reference pointer, no statistics payload. */\n" +
            "".join(f"#define DDR_STATS_OFFSET_{name} {value}\n" for name, value in values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True,
                        help="Directory containing primary-r11/ and primary-upstream/")
    parser.add_argument("--output-root", type=Path, default=ROOT)
    args = parser.parse_args()
    sources, pins = load_sources(args.source_root)
    manifest = derive(*sources, pins)
    generated = (render_inc(manifest), json.dumps(manifest, indent=2) + "\n")
    for relative, content in zip(OUTPUTS, generated):
        path = args.output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    print(json.dumps({"source_inputs": len(pins), "outputs": list(OUTPUTS),
                      "physical_base": manifest["physical_base"],
                      "mapped_bytes": manifest["mapped_bytes"],
                      "offset_value": manifest["offset_value"]}))


if __name__ == "__main__":
    main()
