#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive one RPM statistics offset word from four complete pinned sources.

The vendor consumer adds a firmware-provided word to its DT resource base.
The upstream SM8250 DT and its matching fixed RPMh configuration independently
identify the reference payload address. Only their relative pointer is exposed;
no statistics payload, firmware execution, initialization or timing is modeled.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

VENDOR_REPOSITORY = "OnePlusOSS/android_kernel_oneplus_sm8250"
VENDOR_REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
UPSTREAM_REPOSITORY = "torvalds/linux"
UPSTREAM_REVISION = "adc218676eef25575469234709c2d87185ca223a"
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-rpm-stats-offset-data.inc",
           "tb371fc/docs/rpm-stats-offset-sm8250-registers.json")
SOURCE_PINS = (
    ("primary-r11", VENDOR_REPOSITORY, VENDOR_REVISION,
     "drivers/soc/qcom/rpm_stats.c", 11328,
     "3c493aed9975865203e5e3e2b2cbd95b8357bb47",
     "cb733f4433d6432187dd7faa31d8eff81e1b115ee20691eb53a3b1ca0eba0750"),
    ("primary-r11", VENDOR_REPOSITORY, VENDOR_REVISION,
     "arch/arm64/boot/dts/vendor/qcom/kona-pm.dtsi", 2817,
     "a5eba34468ee25209d8f07baf5efe9c53462ec2a",
     "c1f3a89f461cf1381335ffb2e3066026c28a27642389f9f92fccf540646e5231"),
    ("primary-upstream", UPSTREAM_REPOSITORY, UPSTREAM_REVISION,
     "drivers/soc/qcom/qcom_stats.c", 7641,
     "c429d5154aaec2c3115ccefd1c23d712bf6817f7",
     "b62608854c3dbdef9b43d69a0e5b51b577624c583a80da6eeb5cd176752dd6c5"),
    ("primary-upstream", UPSTREAM_REPOSITORY, UPSTREAM_REVISION,
     "arch/arm64/boot/dts/qcom/sm8250.dtsi", 176883,
     "630f4eff20bf810e347685066dce74ab92a23b56",
     "8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499"),
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
    probe = function_body(driver, "msm_rpmstats_probe")
    compact = re.sub(r"\s+", "", probe)
    required = (
        'u32offset_addr=0;',
        'key="phys_addr_base";res=platform_get_resource_byname(pdev,IORESOURCE_MEM,key);',
        'key="offset_addr";offset=platform_get_resource_byname(pdev,IORESOURCE_MEM,key);',
        'pdata->phys_addr_base=res->start+offset_addr;',
        'pdata->phys_size=resource_size(res);',
        'key="qcom,num-records";',
    )
    for fragment in required:
        require(compact.count(fragment) == 1, "Vendor probe address contract changed: " + fragment)
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
    require(mmio == ["readl_relaxed"], "Vendor probe contains unsupported MMIO")
    require(compact.index('key="phys_addr_base";') < compact.index('key="offset_addr";') <
            compact.index('offset_addr=readl_relaxed(phys_ptr);') <
            compact.index('pdata->phys_addr_base=res->start+offset_addr;'),
            "Vendor probe address operation order changed")
    return {"function": "msm_rpmstats_probe", "read_function": "readl_relaxed",
            "mapping_function": "ioremap_nocache", "mapping_size_token": "SZ_4",
            "direct_mmio_reads": 1, "direct_mmio_writes": 0,
            "payload_read_during_probe": False,
            "address_formula": "phys_addr_base resource start + uint32 offset word"}


def derive(vendor_driver_text, vendor_dts_text, upstream_driver_text, upstream_dts_text, pins):
    vendor, vendor_dts, upstream, upstream_dts = map(
        without_comments, (vendor_driver_text, vendor_dts_text,
                           upstream_driver_text, upstream_dts_text))
    consumer = validate_vendor_consumer(vendor)
    node = flat_node(vendor_dts, "qcom,rpm-stats@c3f0004")
    require(strings(property_value(node, "compatible")) == ["qcom,rpm-stats"],
            "Unexpected vendor RPM statistics compatible")
    names = strings(property_value(node, "reg-names"))
    reg = cells(property_value(node, "reg"))
    require(names == ["phys_addr_base", "offset_addr"] and len(reg) == 2 and
            all(len(pair) == 2 for pair in reg), "Unexpected vendor resource geometry")
    resource_base, resource_bytes = reg[0]
    word_base, word_bytes = reg[1]
    require(resource_base % 4 == 0 and resource_bytes > 0 and word_bytes == 4 and
            word_base % 4 == 0 and word_base == int("c3f0004", 16),
            "Unsupported four-byte offset aperture")
    count_cells = cells(property_value(node, "qcom,num-records"))
    require(len(count_cells) == 1 and len(count_cells[0]) == 1,
            "Vendor record count must be one literal cell")
    vendor_records = count_cells[0][0]
    require(vendor_records == 3, "This profile requires three SM8250 records")

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
    stats_reg = cells(property_value(stats_node, "reg"))
    require(len(stats_reg) == 1 and len(stats_reg[0]) == 4,
            "Upstream statistics resource must have two address and two size cells")
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
    require(set(config) == {"stats_offset", "num_records", "appended_stats_avail",
                            "dynamic_offset", "subsystem_stats_in_smem"},
            "Unexpected RPMh configuration fields")
    require(type(config["stats_offset"]) is int and
            type(config["num_records"]) is int and
            config["num_records"] == vendor_records and
            config["appended_stats_avail"] is False and
            config["dynamic_offset"] is False and
            config["subsystem_stats_in_smem"] is True,
            "RPMh fixed layout/profile contract changed")

    stats_struct = block_after(upstream, r"\bstruct\s+sleep_stats\s*\{",
                               "upstream sleep_stats structure")
    fields = re.findall(r"\b(u32|u64)\s+(\w+)\s*;", stats_struct)
    require(not re.sub(r"\b(?:u32|u64)\s+\w+\s*;", "", stats_struct).strip() and
            fields == [("u32", "stat_type"), ("u32", "count"),
                       ("u64", "last_entered_at"), ("u64", "last_exited_at"),
                       ("u64", "accumulated")], "Unsupported sleep statistics record layout")
    record_bytes = 0
    for kind, _ in fields:
        width = {"u32": 4, "u64": 8}[kind]
        record_bytes = (record_bytes + width - 1) // width * width + width
    record_bytes = (record_bytes + 7) // 8 * 8
    require(record_bytes == 32, "Unexpected upstream sleep statistics record size")
    create = re.sub(r"\s+", "", function_body(upstream, "qcom_create_soc_sleep_stat_files"))
    for fragment in ("size_tstats_offset=config->stats_offset;",
                     "d[i].base=reg+offset+stats_offset;",
                     "offset+=sizeof(structsleep_stats);"):
        require(create.count(fragment) == 1, "Upstream fixed layout use changed: " + fragment)
    probe = re.sub(r"\s+", "", function_body(upstream, "qcom_stats_probe"))
    for fragment in ("config=device_get_match_data(&pdev->dev);",
                     "reg=devm_platform_get_and_ioremap_resource(pdev,0,NULL);",
                     "qcom_create_soc_sleep_stat_files(root,reg,d,config);"):
        require(probe.count(fragment) == 1, "Upstream probe binding changed: " + fragment)

    stats_offset = config["stats_offset"]
    payload_bytes = config["num_records"] * record_bytes
    require(stats_offset % 8 == 0 and stats_offset >= word_base + word_bytes - stats_base and
            stats_offset + payload_bytes <= stats_bytes,
            "Reference records exceed or overlap the source pointer layout")
    payload_base = stats_base + stats_offset
    value = payload_base - resource_base
    require(0 <= value <= 0xffffffff and resource_base + value == payload_base,
            "Relative offset is not representable by the vendor uint32 read")
    require(not (resource_base <= word_base < resource_base + resource_bytes) and
            not (resource_base <= payload_base < resource_base + resource_bytes),
            "Reference statistics unexpectedly overlaps the mailbox resource")
    return {
        "schema": "tb371fc-rpm-stats-offset-registers-v1",
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
            "stats_offset": stats_offset, "num_records": config["num_records"],
            "record_bytes": record_bytes, "payload_base": payload_base,
            "payload_bytes": payload_bytes, "payload_mapped_by_this_device": False},
        "derivation": {
            "expression": "upstream region base + matching fixed stats offset - vendor base",
            "hex_expression": f"0x{stats_base:08x} + 0x{stats_offset:x} - "
                              f"0x{resource_base:08x} = 0x{value:08x}",
            "little_endian_bytes": value.to_bytes(4, "little").hex(),
            "classification": "explicit public SM8250 reference ABI layout",
            "captured_runtime_word_claimed": False},
        "reference_scope": {
            "default_enabled": False, "enabled_aligned_read32_supported": True,
            "writes_supported": False, "disabled_reads_supported": False,
            "statistics_payload_implemented": False, "statistics_counters_implemented": False,
            "statistics_names_implemented": False, "firmware_ready_implemented": False,
            "aop_execution_implemented": False, "irq_output_implemented": False,
            "clock_implemented": False, "timer_implemented": False,
            "ddr_statistics_pointer_implemented": False,
            "mailbox_aperture_modified": False, "native_reset_value_known": False,
            "whole_production_kernel_source_identity_claimed": False},
    }


def render_inc(manifest):
    values = (("BASE", f'UINT64_C(0x{manifest["physical_base"]:08x})'),
              ("WINDOW_BYTES", str(manifest["mapped_bytes"]) + "u"),
              ("VALUE", f'UINT32_C(0x{manifest["offset_value"]:08x})'))
    return ("/* SPDX-License-Identifier: GPL-2.0-or-later */\n"
            "/* Generated by tb371fc/scripts/gen_rpm_stats_offset.py; do not edit. */\n"
            "/* Four pinned public inputs; fixed reference pointer, no statistics payload. */\n" +
            "".join(f"#define RPM_STATS_OFFSET_{name} {value}\n" for name, value in values))


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
