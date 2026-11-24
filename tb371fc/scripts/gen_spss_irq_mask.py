#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Derive the single SPSS probe mask word from two complete pinned sources.

This parser accepts only the small C expression grammar used by the pinned
mask_scsr_irqs helper. It does not evaluate arbitrary source as Python code.
The other DT resources, remote events, firmware and PAS services are outside
the generated device contract.
"""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import re

REPOSITORY = "OnePlusOSS/android_kernel_oneplus_sm8250"
REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
ROOT = Path(__file__).resolve().parents[2]
OUTPUTS = ("hw/misc/qcom-sm8250-spss-irq-mask-data.inc",
           "tb371fc/docs/spss-irq-mask-sm8250-registers.json")
SOURCE_PINS = (
    ("primary-r11", REPOSITORY, REVISION, "drivers/soc/qcom/subsys-pil-tz.c", 32203,
     "ed5e07c193b3841c60c94d92a4db259335d665c9",
     "c574017a8f4e5ad5a4215434029d73bdc2c9ee9736b006429aa30a30ac219608"),
    ("primary-r11", REPOSITORY, REVISION, "arch/arm64/boot/dts/vendor/qcom/kona.dtsi", 132499,
     "e9651f09bd579616787894dc8514429063623b2d",
     "12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55"),
)
UINT32_MASK = (1 << 32) - 1


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
    # Keep quoted strings intact when removing C/DTS comments.
    pattern = r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*'
    return re.sub(pattern, lambda m: m[0] if m[0].startswith('"') else " ",
                  text, flags=re.S)


def one(pattern, text, description, flags=0):
    matches = list(re.finditer(pattern, text, flags))
    require(len(matches) == 1, "Expected one " + description)
    return matches[0]


def function_body(source, name):
    start = one(r"\b" + re.escape(name) + r"\s*\([^;{}]*\)\s*\{", source,
                "definition of " + name).end()
    depth, end = 1, start
    while depth and end < len(source):
        depth += (source[end] == "{") - (source[end] == "}")
        end += 1
    require(depth == 0, "Unbalanced function body")
    return source[start:end - 1]


def flat_node(source, name):
    return one(r"(?m)^\s*" + re.escape(name) + r"\s*\{([^{}]*)\};",
               source, "flat DTS node " + name).group(1)


def property_value(node, name):
    return one(r"(?m)^\s*" + re.escape(name) + r"\s*=\s*([^;]+);",
               node, "property " + name).group(1)


def cells(value):
    require(re.fullmatch(r"\s*<[^<>]+>(?:\s*,\s*<[^<>]+>)*\s*", value),
            "Unsupported DTS cell syntax")
    groups = []
    for group in re.findall(r"<([^>]+)>", value):
        tokens = group.split()
        require(all(re.fullmatch(r"0[xX][0-9a-fA-F]+|[0-9]+", t) for t in tokens),
                "Only literal DTS cells are supported")
        groups.append([int(t, 0) for t in tokens])
    return groups


def strings(value):
    require(re.fullmatch(r'\s*"[^"\\]*"(?:\s*,\s*"[^"\\]*")*\s*', value),
            "Unsupported DTS string syntax")
    return re.findall(r'"([^"\\]*)"', value)


def mask_expression(text, variables, bit_values):
    """Parse only names, integer constants, unary ~, &, and BIT(bit-name)."""
    translated = re.sub(r"d\s*->\s*bits_arr\s*\[\s*(\w+)\s*\]",
                        lambda m: "source_bit_" + m[1], text.strip())
    expression = ast.parse("(" + translated + ")", mode="eval").body
    used_bits = []

    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) is int:
            require(0 <= node.value <= UINT32_MASK, "C integer outside uint32 domain")
            return node.value, {"integer": node.value}
        if isinstance(node, ast.Name) and node.id in variables:
            return variables[node.id], {"variable": node.id}
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Invert):
            value, tree = visit(node.operand)
            return (~value) & UINT32_MASK, {"not_u32": tree}
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitAnd):
            left, left_tree = visit(node.left)
            right, right_tree = visit(node.right)
            return left & right, {"and": [left_tree, right_tree]}
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and
                node.func.id == "BIT" and len(node.args) == 1 and not node.keywords and
                isinstance(node.args[0], ast.Name)):
            macro = node.args[0].id.removeprefix("source_bit_")
            require(node.args[0].id == "source_bit_" + macro and macro in bit_values,
                    "Unknown source bit expression")
            bit = bit_values[macro]
            require(0 <= bit < 32, "Source bit outside uint32 domain")
            used_bits.append(macro)
            return 1 << bit, {"bit": {"index_macro": macro, "bit_position": bit}}
        raise ValueError("Unsupported source mask expression: " + ast.dump(node))

    value, tree = visit(expression)
    return value, tree, used_bits


def derive(driver_text, dts_text, pins):
    driver, dts = without_comments(driver_text), without_comments(dts_text)
    node = flat_node(dts, "qcom,spss@1880000")
    require(strings(property_value(node, "compatible")) == ["qcom,pil-tz-generic"],
            "Unexpected SPSS compatible")
    require(re.search(r"(?m)^\s*qcom,pil-generic-irq-handler\s*;", node) and
            not re.search(r"(?m)^\s*qcom,pil-no-auth\s*;", node),
            "Unexpected SPSS probe/authentication selection")
    reg = cells(property_value(node, "reg"))
    names = strings(property_value(node, "reg-names"))
    require(len(reg) == len(names) == 5 and len(set(names)) == 5 and
            all(len(pair) == 2 for pair in reg), "Unexpected SPSS resource geometry")
    resources = [{"name": name, "base": pair[0], "bytes": pair[1]}
                 for name, pair in zip(names, reg)]
    bit_cells = cells(property_value(node, "qcom,spss-scsr-bits"))
    require(len(bit_cells) == 1 and len(bit_cells[0]) == 2,
            "Unexpected SCSR bit array")
    bits = bit_cells[0]
    require(len(set(bits)) == 2 and all(0 <= bit < 32 for bit in bits), "Invalid SCSR bits")
    bit_indices = {name: int(one(r"(?m)^#define\s+" + name + r"\s+([0-9]+)\s*$",
                                driver, "index macro " + name)[1])
                   for name in ("ERR_READY", "PBL_DONE")}
    require(set(bit_indices.values()) == set(range(len(bits))), "Unexpected bit index mapping")
    bit_values = {name: bits[index] for name, index in bit_indices.items()}

    helper = function_body(driver, "mask_scsr_irqs")
    statements = one(r"\s*uint32_t\s+(\w+)\s*;\s*\1\s*=\s*([^;]+);\s*"
                     r"__raw_writel\s*\((.*),\s*d\s*->\s*(\w+)\s*\)\s*;\s*\Z",
                     helper, "complete mask helper", re.S)
    variable, initializer, expression, field = statements.groups()
    initial, initial_ast, init_bits = mask_expression(initializer, {}, {})
    require(not init_bits, "Unexpected bit-dependent initializer")
    value, expression_ast, used_bits = mask_expression(expression, {variable: initial}, bit_values)
    require(used_bits == ["ERR_READY", "PBL_DONE"], "Source helper bit operands changed")

    probe = function_body(driver, "pil_tz_driver_probe")
    mapping = one(r'res\s*=\s*platform_get_resource_byname\s*\(\s*pdev\s*,\s*'
                  r'IORESOURCE_MEM\s*,\s*"([^"]+)"\s*\)\s*;\s*d\s*->\s*' +
                  re.escape(field) + r'\s*=\s*devm_ioremap_resource\s*\(',
                  probe, "mask field resource mapping")
    selected = [r for r in resources if r["name"] == mapping[1]]
    require(len(selected) == 1, "Mask resource is not unique")
    window = selected[0]
    require(window["name"] == "sp2soc_irq_mask" and window["bytes"] == 4 and
            window["base"] % 4 == 0, "Unsupported mask aperture")
    require(len(re.findall(r"\bmask_scsr_irqs\s*\(\s*d\s*\)\s*;", probe)) == 1 and
            not re.search(r"\b(?:__raw_)?(?:readl|writel)\s*\(", probe),
            "Direct SPSS probe MMIO contract changed")

    return {"schema": "tb371fc-spss-irq-mask-registers-v1",
            "repository": REPOSITORY, "revision": REVISION, "source_pins": pins,
            "source_input_count": len(pins), "register_count": 1, "window_count": 1,
            "physical_base": window["base"], "mapped_bytes": window["bytes"],
            "err_ready_bit": bit_values["ERR_READY"], "pbl_done_bit": bit_values["PBL_DONE"],
            "driver_mask": value, "register_name": window["name"],
            "source_parser": {"helper": "mask_scsr_irqs", "target_field": field,
                "index_macros": bit_indices, "dt_bit_array": bits,
                "initializer_ast": initial_ast, "initializer_value_u32": initial,
                "mask_expression_ast": expression_ast,
                "probe_calls_helper_once": True, "probe_other_resources_are_mappings": True},
            "source_resources": resources,
            "out_of_scope_resources": [r for r in resources if r != window],
            "source_interrupt_cells": cells(property_value(node, "interrupts"))[0],
            "source_pas_id": cells(property_value(node, "qcom,pas-id"))[0][0],
            "source_firmware_name": strings(property_value(node, "qcom,firmware-name"))[0],
            "reference_scope": {"aligned_guest_write32_storage": True,
                "full_uint32_write_values_accepted": True,
                "native_reset_value_known": False, "guest_readback_implemented": False,
                "status_clear_error_words_implemented": False, "irq_output_implemented": False,
                "remote_events_generated": False, "pas_or_firmware_execution_implemented": False,
                "source_memory_layout_imported": False,
                "whole_production_kernel_source_identity_claimed": False}}


def render_inc(manifest):
    values = (("BASE", f'UINT64_C(0x{manifest["physical_base"]:08x})'),
              ("WINDOW_BYTES", str(manifest["mapped_bytes"]) + "u"),
              ("ERR_READY_BIT", str(manifest["err_ready_bit"]) + "u"),
              ("PBL_DONE_BIT", str(manifest["pbl_done_bit"]) + "u"),
              ("DRIVER_MASK", f'UINT32_C(0x{manifest["driver_mask"]:08x})'))
    return ("/* SPDX-License-Identifier: GPL-2.0-or-later */\n"
            "/* Generated by tb371fc/scripts/gen_spss_irq_mask.py; do not edit. */\n"
            "/* Two complete pinned inputs; probe control, not remote SPSS state. */\n" +
            "".join(f"#define SPSS_IRQ_MASK_{name} {value}\n" for name, value in values))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_root", type=Path, help="Directory containing primary-r11/")
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
                      "physical_base": manifest["physical_base"], "mapped_bytes": manifest["mapped_bytes"],
                      "driver_mask": manifest["driver_mask"]}))


if __name__ == "__main__":
    main()
