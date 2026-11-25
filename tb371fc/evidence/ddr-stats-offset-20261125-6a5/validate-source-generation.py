#!/usr/bin/env python3
"""Offline acceptance checks; no device, network, firmware or QEMU execution."""
import argparse
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


def require(condition, description):
    if not condition:
        raise RuntimeError(description)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    scripts = args.candidate / "tb371fc/scripts"
    g = load("gen_ddr_stats_offset", scripts / "gen_ddr_stats_offset.py")
    load("source_download", scripts / "source_download.py")
    v = load("verify_ddr_stats_offset_generation", scripts / "verify_ddr_stats_offset_generation.py")
    source, pins = g.load_sources(args.source_root)
    manifest = g.derive(*source, pins)
    expected = {"source_input_count": 4, "register_count": 1, "window_count": 1,
                "mapped_bytes": 4, "physical_base": 0x0c3f001c, "offset_value": 0xf00b8}
    require({k: manifest[k] for k in expected} == expected, "Baseline contract differs")
    report = {"schema": "tb371fc-ddr-stats-offset-source-validation-v1",
              "passed": False, "source_inputs": 4, "outputs": 2,
              "source_tamper_checks": [], "parser_negative_checks": [],
              "committed_output_tamper_checks": [], "network_used": False,
              "firmware_executed": False, "qemu_executed": False,
              "device_RPC_used": False, "SSH_used": False}

    # Each whole pinned input is changed by one bit, with all other bytes intact.
    for index, pin in enumerate(g.SOURCE_PINS):
        case = args.out / ("source-tamper-" + str(index))
        inputs = case / "inputs"
        mutation = None
        for other_index, (prefix, repo, rev, relative, size, blob, sha) in enumerate(g.SOURCE_PINS):
            name = prefix + "/" + relative
            raw = (args.source_root / name).read_bytes()
            if index == other_index:
                position = len(raw) // 2
                edited = bytearray(raw)
                edited[position] ^= 1
                raw = bytes(edited)
                mutation = {"path": name, "offset": position, "xor": 1,
                            "bytes": len(raw), "sha256": g.digest(raw)}
            path = inputs / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        try:
            g.load_sources(inputs)
        except ValueError as error:
            require(str(error).startswith("Complete source identity mismatch: "),
                    "Source tamper did not fail at the identity boundary")
            generator_error = str(error)
        else:
            raise RuntimeError("Generator accepted one-bit source mutation")
        output = case / "verifier"
        code = v.run(argparse.Namespace(out=output, source_root=inputs))
        receipt = json.loads((output / "result.json").read_bytes())
        require(code == 1 and receipt["passed"] is False and
                receipt["source_pins_verified"] is False and "generator_exit" not in receipt and
                not (output / "generated").exists(), "Verifier accepted tampered input")
        report["source_tamper_checks"].append({
            "mutation": mutation, "generator_error": generator_error,
            "verifier_exit": code, "generator_not_run": True,
            "verifier_result_sha256": g.digest((output / "result.json").read_bytes()),
            "passed": True})

    def change(index, old, new):
        require(source[index].count(old) == 1, "Nonunique parser test edit: " + old)
        changed = list(source)
        changed[index] = changed[index].replace(old, new, 1)
        return changed

    config_header = "static const struct stats_config rpmh_data = {"
    config_body = g.block_after(g.without_comments(source[2]),
                               r"\bstatic\s+const\s+struct\s+stats_config\s+rpmh_data\s*=\s*\{",
                               "RPMh test configuration")
    config_full = config_header + config_body + "}"
    require(source[2].count(config_full) == 1, "Test configuration extraction differs")

    def config_change(old, new):
        require(config_full.count(old) == 1, "Nonunique config test edit")
        return change(2, config_full, config_full.replace(old, new, 1))

    vectors = [
        ("vendor_write_instead_of_offset_read",
         change(0, "offset_addr = readl_relaxed(phys_ptr);", "writel_relaxed(0, phys_ptr);")),
        ("vendor_mapping_is_eight_bytes",
         change(0, "ioremap_nocache(offset->start, SZ_4)", "ioremap_nocache(offset->start, SZ_8)")),
        ("vendor_address_formula_is_or",
         change(0, "res->start + offset_addr;", "res->start | offset_addr;")),
        ("vendor_duplicate_offset_read",
         change(0, "offset_addr = readl_relaxed(phys_ptr);",
                "offset_addr = readl_relaxed(phys_ptr); offset_addr = readl_relaxed(phys_ptr);")),
        ("vendor_zero_guard_returns_success",
         change(0, "if (!offset_addr)\n\t\treturn -ENODEV;", "if (!offset_addr)\n\t\treturn 0;")),
        ("vendor_aperture_is_eight_bytes",
         change(1, "<0xc3f001c 0x4>", "<0xc3f001c 0x8>")),
        ("vendor_resource_names_reordered",
         change(1, '<0xc3f001c 0x4>;\n\t\treg-names = "phys_addr_base", "offset_addr";',
                '<0xc3f001c 0x4>;\n\t\treg-names = "offset_addr", "phys_addr_base";')),
        ("vendor_compatible_differs",
         change(1, 'compatible = "qcom,ddr-stats";', 'compatible = "qcom,other-stats";')),
        ("vendor_record_width_changed",
         change(0, "uint64_t duration;", "uint32_t duration;")),
        ("vendor_header_field_changed",
         change(0, "uint32_t entry_count;", "uint64_t entry_count;")),
        ("upstream_compatible_differs",
         change(3, 'compatible = "qcom,rpmh-stats";', 'compatible = "qcom,sdm845-rpmh-stats";')),
        ("upstream_parent_address_cells_changed",
         change(3, "soc: soc@0 {\n\t\t#address-cells = <2>;",
                "soc: soc@0 {\n\t\t#address-cells = <1>;")),
        ("upstream_reg_cell_geometry_changed",
         change(3, "<0 0x0c3f0000 0 0x400>", "<0x0c3f0000 0x400>")),
        ("upstream_match_points_to_other_config",
         change(2, '{ .compatible = "qcom,rpmh-stats", .data = &rpmh_data }',
                '{ .compatible = "qcom,rpmh-stats", .data = &rpmh_data_sdm845 }')),
        ("upstream_dynamic_offset_enabled",
         config_change(".dynamic_offset = false,", ".dynamic_offset = true,")),
        ("upstream_appended_records_enabled",
         config_change(".appended_stats_avail = false,", ".appended_stats_avail = true,")),
        ("upstream_duplicate_DDR_layout_field",
         config_change(".ddr_stats_offset = 0xb8,", ".ddr_stats_offset = 0xb8,\n\t.ddr_stats_offset = 0xb8,")),
        ("upstream_record_width_changed",
         change(2, "u64 duration;", "u32 duration;")),
        ("upstream_magic_disagrees_with_vendor",
         change(2, "0xA1157A75", "0xA1157A74")),
        ("upstream_maximum_count_disagrees_with_vendor",
         change(2, "DDR_STATS_MAX_NUM_MODES\t\t20", "DDR_STATS_MAX_NUM_MODES\t\t21")),
        ("upstream_entry_header_offset_changed",
         change(2, "DDR_STATS_ENTRY_START_ADDR\t0x8", "DDR_STATS_ENTRY_START_ADDR\t0x10")),
        ("upstream_magic_read_width_changed",
         change(2, "key = readl_relaxed(reg + config->ddr_stats_offset + DDR_STATS_MAGIC_KEY_ADDR);",
                "key = readq_relaxed(reg + config->ddr_stats_offset + DDR_STATS_MAGIC_KEY_ADDR);")),
        ("upstream_record_stride_expression_changed",
         change(2, "sizeof(struct ddr_stats_entry) * entry_count", "sizeof(struct sleep_stats) * entry_count")),
        ("upstream_resource_macro_not_literal",
         change(3, "<0 0x0c3f0000 0 0x400>", "<0 0x0c3f0000 0 SIZE>")),
        ("upstream_cell_exceeds_uint32",
         change(3, "<0 0x0c3f0000 0 0x400>", "<0 0x0c3f0000 0 0x100000000>")),
        ("upstream_unknown_config_field",
         config_change(".ddr_stats_offset = 0xb8,", ".ddr_stats_offset = 0xb8,\n\t.unknown = 0,")),
        ("upstream_maximum_payload_exceeds_region",
         config_change(".ddr_stats_offset = 0xb8,", ".ddr_stats_offset = 0x3f8,")),
        ("upstream_DDR_payload_is_unaligned",
         config_change(".ddr_stats_offset = 0xb8,", ".ddr_stats_offset = 0xb9,")),
        ("upstream_DDR_offset_is_absent",
         config_change(".ddr_stats_offset = 0xb8,", ".ddr_stats_offset = 0,")),
        ("upstream_DDR_payload_overlaps_pointer",
         config_change(".ddr_stats_offset = 0xb8,", ".ddr_stats_offset = 0x18,")),
        ("upstream_QMP_requirement_not_in_reference",
         change(3, "reg = <0 0x0c3f0000 0 0x400>;",
                "reg = <0 0x0c3f0000 0 0x400>;\n\t\t\tqcom,qmp = <&aoss_qmp>;")),
        ("upstream_probe_uses_other_consumer",
         change(2, "qcom_create_ddr_stat_files(root, reg, config);",
                "other_create_ddr_stat_files(root, reg, config);")),
    ]
    for name, changed in vectors:
        try:
            g.derive(*changed, pins)
        except ValueError as error:
            report["parser_negative_checks"].append(
                {"name": name, "error": str(error), "passed": True})
        else:
            raise RuntimeError("Unsupported parser vector was accepted: " + name)

    # A complete bad HTTP response must reach identity failure once. This uses
    # an in-memory response, not a server or a replacement transport policy.
    first = g.SOURCE_PINS[0]
    bad = bytearray((args.source_root / first[0] / first[3]).read_bytes())
    bad[len(bad) // 2] ^= 1
    calls = []
    original_read = v.read_source

    def complete_bad_response(url, *, max_bytes):
        calls.append({"url": url, "max_bytes": max_bytes})
        return bytes(bad)

    v.read_source = complete_bad_response
    try:
        out = args.out / "complete-bad-response"
        code = v.run(argparse.Namespace(out=out, source_root=None))
    finally:
        v.read_source = original_read
    receipt = json.loads((out / "result.json").read_bytes())
    require(code == 1 and len(calls) == 1 and len(receipt["inputs"]) == 1 and
            receipt["source_pins_verified"] is False and "generator_exit" not in receipt,
            "Complete identity failure retried or fell back")
    report["complete_bad_response"] = {"passed": True, "transport_calls": len(calls),
                                      "identity_mismatch_final": True,
                                      "generator_not_run": True,
                                      "result_sha256": g.digest((out / "result.json").read_bytes())}

    for index, relative in enumerate(g.OUTPUTS):
        out = args.out / ("output-tamper-" + str(index))
        committed = out / "committed"
        for other_index, name in enumerate(g.OUTPUTS):
            raw = bytearray((args.candidate / name).read_bytes())
            if index == other_index:
                raw[len(raw) // 2] ^= 1
            path = committed / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        original_root = v.ROOT
        v.ROOT = committed
        try:
            code = v.run(argparse.Namespace(out=out / "verifier", source_root=args.source_root))
        finally:
            v.ROOT = original_root
        receipt = json.loads((out / "verifier/result.json").read_bytes())
        require(code == 1 and receipt["source_pins_verified"] is True and
                receipt["generator_exit"] == 0 and len(receipt["checks"]) == 2 and
                sum(x["passed"] is False for x in receipt["checks"]) == 1 and
                receipt["checks"][index]["passed"] is False, "Committed-output drift accepted")
        report["committed_output_tamper_checks"].append({
            "path": relative, "xor": 1, "generator_exit": 0, "verifier_exit": 1,
            "only_mutated_output_failed": True, "passed": True,
            "result_sha256": g.digest((out / "verifier/result.json").read_bytes())})

    report["passed"] = True
    report["counts"] = {"whole_source_one_bit_rejections": len(report["source_tamper_checks"]),
                        "parser_negative_rejections": len(report["parser_negative_checks"]),
                        "complete_bad_response_rejections_without_retry": 1,
                        "committed_output_one_bit_rejections": len(report["committed_output_tamper_checks"])}
    report["input_manifest"] = pins
    report["files"] = []
    for relative in ("tb371fc/scripts/gen_ddr_stats_offset.py",
                     "tb371fc/scripts/verify_ddr_stats_offset_generation.py",
                     "tb371fc/scripts/source_download.py", *g.OUTPUTS):
        raw = (args.candidate / relative).read_bytes()
        report["files"].append({"path": relative, "bytes": len(raw), "sha256": g.digest(raw)})
    path = args.out / "result.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": True, "counts": report["counts"],
                      "result": str(path), "sha256": g.digest(path.read_bytes())}))


if __name__ == "__main__":
    main()
