#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Generate the immutable RPM statistics reference from its frozen contract.

Six complete public source files independently establish the consumer ABI.
The record tags and initialized zero values are bound to the private research
receipt in the contract; public source verification does not reverify firmware.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = "tb371fc/docs/rpm-stats-sm8250-reference-contract.json"
CONTRACT_SHA256 = "9aef5bfdf8c6c7ccbd01ef666f8c0f4cd9c4083365a06d27ca1d4bf979aa8337"
CONTRACT_BYTES = 8530
OUTPUTS = ("include/hw/misc/qcom-sm8250-rpm-stats-reference.h",
           "tb371fc/docs/rpm-stats-sm8250-reference-evidence.json")
PREFIX = "QCOM_SM8250_RPM_STATS_"
SOURCE_PREFIXES = {
    "OnePlusOSS/android_kernel_oneplus_sm8250": "vendor",
    "torvalds/linux": "mainline",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def git_blob(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def canonical_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")) + "\n").encode("utf-8")


def bounded_read(path, expected_bytes):
    with Path(path).open("rb") as stream:
        return stream.read(expected_bytes + 1)


def load_contract(path):
    raw = bounded_read(path, CONTRACT_BYTES)
    require(len(raw) == CONTRACT_BYTES and digest(raw) == CONTRACT_SHA256,
            "Frozen RPM statistics contract identity mismatch")
    contract = json.loads(raw)
    require(canonical_bytes(contract) == raw, "Contract is not canonical JSON with one final LF")
    require(contract["schema"] == "tb371fc-rpm-stats-firmware-initialized-reference-v1",
            "Unsupported reference contract schema")
    require(len(contract["public_sources"]["pins"]) == 6,
            "The reference requires six complete public inputs")
    return contract


def source_path(pin):
    return SOURCE_PREFIXES[pin["repository"]] + "/" + pin["path"]


def raw_url(pin):
    return ("https://raw.githubusercontent.com/" + pin["repository"] + "/" +
            pin["commit"] + "/" + pin["path"])


def verify_source(raw, pin):
    actual = {"bytes": len(raw), "sha256": digest(raw), "git_blob_sha1": git_blob(raw)}
    require(all(actual[key] == pin[key] for key in actual),
            "Complete public source identity mismatch: " + source_path(pin))
    return actual


def load_sources(source_root, contract):
    texts, receipts = {}, []
    for pin in contract["public_sources"]["pins"]:
        local = source_path(pin)
        raw = bounded_read(source_root / local, pin["bytes"])
        actual = verify_source(raw, pin)
        texts[local] = raw.decode("utf-8")
        receipts.append({**pin, **actual, "local_path": local})
    return texts, receipts


def without_comments(text):
    # Preserve strings while discarding comments; no upstream code is executed.
    return re.sub(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*',
                  lambda match: match[0] if match[0].startswith('"') else " ",
                  text, flags=re.S)


def one(pattern, text, description):
    matches = list(re.finditer(pattern, text, re.S))
    require(len(matches) == 1, "Expected one " + description)
    return matches[0]


def require_text(pattern, text, description):
    require(re.search(pattern, text, re.S) is not None, "Missing " + description)


def record_layout(text, name, smd_conditional=False):
    body = one(r"\bstruct\s+" + name + r"\s*\{([^{}]*)\}\s*;",
               without_comments(text), name + " definition")[1]
    if smd_conditional:
        optional = one(r"#if\s+defined\(CONFIG_MSM_RPM_SMD\)(.*?)#endif", body,
                       "conditional SMD extension")
        require(re.fullmatch(r"\s*u32\s+client_votes\s*;\s*u32\s+reserved\[3\]\s*;\s*",
                             optional[1]) is not None, "Unexpected SMD extension layout")
        body = body[:optional.start()] + body[optional.end():]
    declarations = list(re.finditer(r"\b(u32|u64)\s+(\w+)\s*;", body))
    require(re.sub(r"\b(u32|u64)\s+(\w+)\s*;", "", body).strip() == "",
            "Unsupported record declaration")
    result, offset, alignment = [], 0, 1
    for declaration in declarations:
        size = {"u32": 4, "u64": 8}[declaration[1]]
        offset = (offset + size - 1) // size * size
        result.append({"name": declaration[2], "offset_bytes": offset, "size_bytes": size})
        offset += size
        alignment = max(alignment, size)
    return result, (offset + alignment - 1) // alignment * alignment


def public_facts(texts, contract):
    rpm = without_comments(texts["vendor/drivers/soc/qcom/rpm_stats.c"])
    ddr = without_comments(texts["vendor/drivers/soc/qcom/ddr_stats.c"])
    io_c = without_comments(texts["vendor/arch/arm64/kernel/io.c"])
    io_h = without_comments(texts["vendor/arch/arm64/include/asm/io.h"])
    main = without_comments(texts["mainline/drivers/soc/qcom/qcom_stats.c"])
    dts = without_comments(texts["mainline/arch/arm64/boot/dts/qcom/sm8250.dtsi"])
    vendor_fields, vendor_stride = record_layout(rpm, "msm_rpm_stats_data", True)
    main_fields, main_stride = record_layout(main, "sleep_stats")
    expected = contract["record_layout"]
    require(vendor_fields == main_fields == expected["fields"],
            "Public record fields differ from the frozen consumer ABI")
    require(vendor_stride == main_stride == expected["stride_bytes"] == 32,
            "Public record stride differs from the frozen consumer ABI")
    require("# CONFIG_MSM_RPM_SMD is not set" in
            contract["input_identity"]["kernel_config"]["selected_lines"],
            "Reference layout requires the recorded non-SMD configuration")

    require_text(r"u32\s+offset_addr\s*=\s*0\s*;", rpm, "unsigned vendor offset")
    require_text(r"offset_addr\s*=\s*readl_relaxed\(phys_ptr\)\s*;", rpm,
                 "32-bit vendor offset read")
    require_text(r"pdata->phys_addr_base\s*=\s*res->start\s*\+\s*offset_addr\s*;", rpm,
                 "full vendor resource-plus-offset rule")
    require_text(r"return\s+readl_relaxed\(regbase\s*\+\s*offset\s*\+\s*"
                 r"index\s*\*\s*sizeof\(struct\s+msm_rpm_stats_data\)\)\s*;", rpm,
                 "vendor 32-bit field read")
    require_text(r"memcpy_fromio\(&dst,\s*regbase\s*\+\s*offset\s*\+\s*"
                 r"index\s*\*\s*sizeof\(struct\s+msm_rpm_stats_data\),\s*8\)\s*;", rpm,
                 "vendor eight-byte timestamp copy")
    require_text(r"void\s+__memcpy_fromio\([^{};]*\)\s*\{\s*"
                 r"while\s*\(count\s*&&\s*!IS_ALIGNED\(\(unsigned long\)from,\s*8\)\)",
                 io_c, "ARM64 memcpy_fromio alignment rule")
    require_text(r"while\s*\(count\s*>=\s*8\)\s*\{\s*"
                 r"\*\(u64\s*\*\)to\s*=\s*__raw_readq_no_log\(from\)\s*;", io_c,
                 "ARM64 aligned 64-bit IO copy")
    require_text(r"static\s+inline\s+u64\s+__raw_readq_no_log\([^{};]*\)\s*\{\s*u64\s+val;"
                 r".*?ALTERNATIVE\(\"ldr %0, \[%1\]\",\s*\"ldar %0, \[%1\]\"",
                 io_h, "ARM64 64-bit load primitive")
    require_text(r"#define\s+memcpy_fromio\(.*?__memcpy_fromio", io_h,
                 "ARM64 memcpy_fromio binding")

    config = one(r"static\s+const\s+struct\s+stats_config\s+rpmh_data\s*=\s*\{([^{}]*)\};",
                 main, "three-record RPMh configuration")[1]
    values = dict(re.findall(r"\.(\w+)\s*=\s*(0x[0-9a-f]+|[0-9]+|true|false)\s*,", config))
    require(values["num_records"] == str(expected["count"]) and
            values["appended_stats_avail"] == values["dynamic_offset"] == "false",
            "Unexpected mainline RPMh record selection")
    require_text(r'\{\s*\.compatible\s*=\s*"qcom,rpmh-stats",\s*\.data\s*=\s*&rpmh_data\s*\}',
                 main, "mainline RPMh compatible binding")
    node = one(r"sram@c3f0000\s*\{([^{}]*)\};", dts, "SM8250 global statistics node")[1]
    require_text(r'compatible\s*=\s*"qcom,rpmh-stats"\s*;', node,
                 "SM8250 statistics compatible")
    cells = one(r"\breg\s*=\s*<([^<>]+)>\s*;", node, "SM8250 statistics resource")[1].split()
    require(len(cells) == 4 and all(re.fullmatch(r"0x[0-9a-f]+|[0-9]+", cell) for cell in cells),
            "Unexpected statistics resource syntax")
    cells = [int(cell, 0) for cell in cells]
    base, size = (cells[0] << 32) | cells[1], (cells[2] << 32) | cells[3]
    global_region = contract["addressing"]["mainline_global_region"]
    stats_offset = int(values["stats_offset"], 0)
    require((base, size, stats_offset) == (int(global_region["physical_base"], 0),
            global_region["size_bytes"], global_region["stats_offset_bytes"]),
            "Mainline statistics geometry differs from contract")
    require(base + stats_offset == int(expected["records"][0]["physical_base"], 0),
            "Independent mainline statistics address differs")
    require_text(r"struct\s+stats_entry\s*\{\s*uint32_t\s+name;\s*uint32_t\s+count;"
                 r"\s*uint64_t\s+duration;\s*\}", ddr, "separate DDR residency record")
    require_text(r"if\s*\(!offset_addr\)\s*return\s+-ENODEV\s*;", ddr,
                 "separate DDR offset consumer")
    return {
        "record_fields": vendor_fields,
        "record_stride_bytes": vendor_stride,
        "mainline_record_count": int(values["num_records"]),
        "mainline_global_base": base,
        "mainline_global_resource_bytes": size,
        "mainline_stats_offset_bytes": stats_offset,
        "vendor_offset_is_full_unsigned_u32": True,
        "vendor_timestamp_copy_bytes": 8,
        "arm64_aligned_io_read_bytes": 8,
        "separate_ddr_residency_consumer_verified": True,
        "kernel_build_provenance_verified": False,
        "record_tags_or_initialized_values_verified_from_public_sources": False,
    }


def header_bytes(contract):
    addressing, layout = contract["addressing"], contract["record_layout"]
    offset, records = addressing["leaf_regions"]
    lines = ["/* SPDX-License-Identifier: GPL-2.0-or-later */",
             "/* Generated by tb371fc/scripts/generate_rpm_stats_reference.py. */",
             "/* Frozen initialized reference; public sources verify its consumer ABI. */",
             "#ifndef HW_MISC_QCOM_SM8250_RPM_STATS_REFERENCE_H",
             "#define HW_MISC_QCOM_SM8250_RPM_STATS_REFERENCE_H", ""]
    definitions = [
        ("OFFSET_PHYS_BASE", f"0x{int(offset['physical_base'], 0):08x}ULL"),
        ("OFFSET_BYTES", f"{offset['size_bytes']}U"),
        ("OFFSET_WORD_VALUE", f"0x{int(addressing['offset_value_u32'], 0):08x}U"),
        ("VENDOR_RESOURCE_BASE", f"0x{int(addressing['vendor_resource_base'], 0):08x}ULL"),
        ("RECORD_PHYS_BASE", f"0x{int(records['physical_base'], 0):08x}ULL"),
        ("RECORD_COUNT", f"{layout['count']}U"),
        ("RECORD_STRIDE", f"{layout['stride_bytes']}U"),
        ("RECORD_WINDOW_BYTES", f"{layout['total_bytes']}U"),
    ]
    definitions += [(f"TAG{record['index']}", f"0x{int(record['stat_type_u32'], 0):08x}U")
                    for record in layout["records"]]
    for field in layout["fields"]:
        definitions += [(f"FIELD_{field['name'].upper()}_OFFSET", f"{field['offset_bytes']}U"),
                        (f"FIELD_{field['name'].upper()}_BYTES", f"{field['size_bytes']}U")]
    definitions += [("CONTRACT_DIGEST_BYTES", "32U"),
                    ("CONTRACT_SHA256_HEX", '"' + CONTRACT_SHA256 + '"')]
    lines += ["#define " + PREFIX + name + " " + value for name, value in definitions]
    lines += ["#define " + PREFIX + "CONTRACT_SHA256_BYTES { \\"]
    raw_digest = bytes.fromhex(CONTRACT_SHA256)
    for offset in range(0, len(raw_digest), 8):
        lines.append("    " + ", ".join(f"0x{byte:02x}" for byte in raw_digest[offset:offset + 8]) +
                     ", \\")
    lines += ["}", "", "#endif /* HW_MISC_QCOM_SM8250_RPM_STATS_REFERENCE_H */", ""]
    return "\n".join(lines).encode("ascii")


def generate(source_root, contract_path):
    contract = load_contract(contract_path)
    texts, sources = load_sources(source_root, contract)
    facts = public_facts(texts, contract)
    evidence = {
        "schema": "tb371fc-rpm-stats-reference-evidence-v1",
        "contract_sha256": CONTRACT_SHA256,
        "contract_bytes": CONTRACT_BYTES,
        "source_input_count": len(sources),
        "source_pins_verified": True,
        "sources": sources,
        "public_facts": facts,
        "private_firmware_evidence_verified": False,
        "private_research_receipt_sha256": contract["evidence_binding"]["private_research_receipt_sha256"],
        "firmware_is_pristine_stock": contract["input_identity"]["aop_image"]["firmware_is_pristine_stock"],
        "evidence_scope": "Complete pinned public sources verify the consumer ABI. The frozen contract binds private initialization evidence by hash only.",
    }
    return {OUTPUTS[0]: header_bytes(contract),
            OUTPUTS[1]: (json.dumps(evidence, sort_keys=True, indent=2) + "\n").encode("utf-8")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="Complete sources under vendor/ and mainline/")
    parser.add_argument("--contract", type=Path, default=ROOT / CONTRACT_PATH)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    outputs = generate(args.source_root, args.contract)
    args.output_root.mkdir(parents=True, exist_ok=False)
    for relative, raw in outputs.items():
        path = args.output_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    print(json.dumps({"contract_sha256": CONTRACT_SHA256, "source_input_count": 6,
                      "outputs": list(outputs), "private_firmware_evidence_verified": False},
                     sort_keys=True))


if __name__ == "__main__":
    main()
