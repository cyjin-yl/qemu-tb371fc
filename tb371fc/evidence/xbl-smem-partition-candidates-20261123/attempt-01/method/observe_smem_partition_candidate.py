#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Review draft: conditional observation of one already reported structure slot.

No native layout or platform is selected. The CLI requires an explicit mode,
fixed byte identities and a separately reviewed execution scope. The unchanged
partition core retains NATIVE_PIN=None. This adapter never generates SMEM.
"""
from dataclasses import dataclass
import copy
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types

SCHEMA = "tb371fc.smem-observed-slot-candidates.v1"
MODE = "--conditional-observed-slot"
RECORD_CAP = 16
MAX_OUTPUT = 12288
CORE_NAME = "smem_partition_candidates.py"
CORE_BYTES = 16046
CORE_SHA256 = "17ce02aae0f4c24d68824d69e3421408e1bf58ab03ad08b28c3059c4ca4caf18"
CORE_MODULE = "_tb371fc_smem_partition_core_observed_v1"
IMAGE_PATH = Path("/work/device-assets/xbl-capture-20261123-01/xbl_a.bin")
RECEIPT_PATH = Path("/work/xbl-dal-candidates-20261123-03/results/scan.stdout")
DAL_SOURCE_SHA256 = "23fcc56faaaccce7d179b606d2414db79a8c1f2e9f160885129209d2d8f67b68"
DAL_ARCHIVE_COMMIT = "1ec5285e71af7db3b8766ea8523e9b219c10cc96"
COMPACT = "compact-memory-optimised-candidate"
PLAIN = "plain-pointer-aapcs64"
SIZED = "size-plus-pointer-aapcs64"
_CORE = None


@dataclass(frozen=True)
class ObservationContract:
    """Byte/geometry identity for an observation, never a native-selection pin."""
    image_size: int
    image_sha256: str
    receipt_size: int
    receipt_sha256: str
    scope_load_indices: tuple
    dalprops_va: int
    descriptor_va: int
    descriptor_file_offset: int
    device_count: int
    device_index: int
    struct_base: int
    property_index: int
    plain_file_offset: int
    sized_file_offset: int
    slot_file_offset: int
    target_va: int
    target_file_offset: int
    load_index: int
    declared_size: int
    scalar_values: tuple


OBSERVED_CONTRACT = ObservationContract(
    image_size=3670016,
    image_sha256="eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55",
    receipt_size=7358,
    receipt_sha256="839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053",
    scope_load_indices=(4, 5, 11, 14),
    dalprops_va=0x14877e10,
    descriptor_va=0x1487a1f0,
    descriptor_file_offset=425856,
    device_count=17,
    device_index=11,
    struct_base=0x14879510,
    property_index=58,
    plain_file_offset=423024,
    sized_file_offset=423488,
    slot_file_offset=423496,
    target_va=0x14878590,
    target_file_offset=418592,
    load_index=4,
    declared_size=8,
    scalar_values=(0x000c0000, 1, 625),
)


class Rejected(Exception):
    """Only fixed codes; no private bytes or arbitrary exception strings."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(ok, code):
    if not ok:
        raise Rejected(code)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def integer(value, expected, code):
    require(type(value) is int and value == expected, code)


def dictionary(value, code):
    require(type(value) is dict, code)
    return value


def false_fields(value, fields, code):
    require(all(value.get(field) is False for field in fields), code)


def read_regular(path, size):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode)
                and stat.S_IMODE(before.st_mode) == 0o600
                and before.st_size == size, "input_file_stat")
        raw = bytearray()
        while len(raw) < size + 1:
            part = os.read(fd, min(131072, size + 1 - len(raw)))
            if not part:
                break
            raw.extend(part)
        after = os.fstat(fd)
        require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                 before.st_ctime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns,
                 after.st_ctime_ns), "input_changed_during_read")
        require(len(raw) == size, "input_read_length")
        return bytes(raw)
    finally:
        os.close(fd)


def load_core():
    global _CORE
    if _CORE is None:
        path = Path(__file__).with_name(CORE_NAME)
        raw = read_regular(path, CORE_BYTES)
        require(sha(raw) == CORE_SHA256, "core_source_identity")
        # Execute the exact hashed Python analysis source, never firmware.
        # Register before exec because the unchanged core uses dataclasses.
        module = types.ModuleType(CORE_MODULE)
        module.__file__ = str(path)
        sys.modules[CORE_MODULE] = module
        exec(compile(raw, str(path), "exec"), module.__dict__)
        require(module.NATIVE_PIN is None, "core_native_pin_must_remain_none")
        _CORE = module
    require(_CORE.NATIVE_PIN is None, "core_native_pin_must_remain_none")
    return _CORE


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "receipt_duplicate_json_key")
            result[key] = value
        return result

    def constant(_value):
        raise Rejected("receipt_nonfinite_json_number")

    try:
        value = json.loads(raw.decode("ascii"), object_pairs_hook=pairs,
                           parse_constant=constant)
    except Rejected:
        raise
    except (UnicodeError, ValueError, RecursionError):
        raise Rejected("receipt_invalid_json")
    return dictionary(value, "receipt_root_not_object")


def mapped_record(value, *, va, size, file_offset, load_index):
    value = dictionary(value, "receipt_mapping_shape")
    for key, expected in (("va", va), ("size", size), ("file_offset", file_offset),
                          ("load_index", load_index)):
        integer(value.get(key), expected, "receipt_mapping_identity")
    require(value.get("file_backed") is True, "receipt_mapping_not_file_backed")
    flags = value.get("flags")
    require(type(flags) is int and 0 <= flags <= 7 and not (flags & 1),
            "receipt_mapping_executable_or_invalid")
    # Returned metadata is reconstructed from checked fields, not copied extras.
    return {"va": va, "size": size, "file_offset": file_offset,
            "load_index": load_index, "file_backed": True, "flags": flags}


def validate_receipt(raw, image, contract):
    require(type(contract) is ObservationContract, "observation_contract_type")
    require(type(raw) is bytes and type(image) is bytes, "immutable_input_bytes_required")
    require(len(image) == contract.image_size and sha(image) == contract.image_sha256,
            "image_byte_identity")
    require(len(raw) == contract.receipt_size and len(raw) <= MAX_OUTPUT
            and sha(raw) == contract.receipt_sha256, "receipt_byte_identity")
    report = strict_json(raw)
    require(report.get("schema") == "tb371fc-xbl-dal-static-candidates-v3"
            and report.get("property_format") == COMPACT
            and report.get("analysis_completed") is True, "receipt_format")
    false_fields(report, ("platform_selected", "reachable_from_entry_proven",
                          "smem_initialized_proven", "firmware_executed",
                          "candidate_processing_truncated"), "receipt_runtime_or_truncation_claim")
    for name, expected in (("accepted_candidate_count", 1), ("candidate_details_omitted", 0),
                           ("bounded_geometric_candidates", 1), ("processed_candidates", 1)):
        integer(report.get(name), expected, "receipt_candidate_count")
    source = dictionary(report.get("input"), "receipt_image_shape")
    require(set(source) == {"basename", "size", "sha256"}
            and source.get("basename") == "xbl_a.bin"
            and source.get("sha256") == contract.image_sha256, "receipt_image_identity")
    integer(source.get("size"), contract.image_size, "receipt_image_identity")
    scope = dictionary(report.get("scope"), "receipt_scope_shape")
    indices = scope.get("load_indices")
    require(type(indices) is list and all(type(value) is int for value in indices)
            and indices == list(contract.scope_load_indices)
            and scope.get("device_name") == "/core/mproc/smem", "receipt_scope")
    integer(scope.get("known_descriptor_va"), contract.descriptor_va, "receipt_scope")
    integer(scope.get("known_descriptor_file_offset"), contract.descriptor_file_offset,
            "receipt_scope")
    candidates = report.get("candidates")
    require(type(candidates) is list and len(candidates) == 1, "receipt_candidate_list")
    item = dictionary(candidates[0], "receipt_candidate_shape")
    require(item.get("property_format") == COMPACT, "receipt_candidate_format")
    false_fields(item, ("platform_selected", "reachable_from_entry_proven",
                        "smem_initialized_proven", "firmware_executed",
                        "native_hw_version_used"), "receipt_candidate_runtime_claim")
    head = dictionary(item.get("dalprops"), "receipt_dalprops_shape")
    integer(head.get("va"), contract.dalprops_va, "receipt_dalprops_identity")
    descriptor = dictionary(item.get("target_descriptor"), "receipt_descriptor_shape")
    integer(descriptor.get("va"), contract.descriptor_va, "receipt_descriptor_identity")
    array = dictionary(item.get("device_array"), "receipt_device_array_shape")
    for name, expected in (("count", contract.device_count), ("target_index", contract.device_index),
                           ("format_lookup_selected_index", contract.device_index), ("stride", 40)):
        integer(array.get(name), expected, "receipt_device_array_identity")
    require(array.get("all_names_and_hashes_validated") is True
            and array.get("non_allowlisted_names_exported") is False, "receipt_device_array_validation")
    properties = dictionary(item.get("properties"), "receipt_properties_shape")
    scalars = {}
    for name, expected in zip(("smem_vers", "smem_toc_vers", "smem_max_items"),
                              contract.scalar_values):
        prop = dictionary(properties.get(name), "receipt_scalar_shape")
        require(prop.get("status") == "static_uint32_candidate"
                and prop.get("property_header_format") == COMPACT
                and prop.get("list_source") == "device"
                and prop.get("matches_expected_setting_type") is True
                and prop.get("visibility_denied") is False, "receipt_scalar_status")
        integer(prop.get("type"), 2, "receipt_scalar_type")
        integer(prop.get("value"), expected, "receipt_scalar_value")
        scalars[name] = expected
    integer(properties["smem_max_items"].get("loader_u16_cast_candidate"),
            contract.scalar_values[2] & 0xffff, "receipt_scalar_cast")
    prop = dictionary(properties.get("smem_partitions"), "receipt_partition_shape")
    require(prop.get("status") == "static_structure_index_with_unselected_layouts"
            and prop.get("property_header_format") == COMPACT
            and prop.get("matches_expected_setting_type") is True
            and prop.get("list_source") == "device"
            and prop.get("visibility_denied") is False, "receipt_partition_status")
    false_fields(prop, ("struct_layout_selected", "array_total_length_known",
                        "target_contents_read"), "receipt_partition_selection_claim")
    for name, expected in (("index", contract.property_index), ("struct_pointer_array_va", contract.struct_base),
                           ("type", 0x12), ("mapped_layout_count", 1)):
        integer(prop.get(name), expected, "receipt_partition_identity")
    layouts = prop.get("layout_candidates")
    require(type(layouts) is list and len(layouts) == 2, "receipt_layout_count")
    by_name = {}
    for layout in layouts:
        layout = dictionary(layout, "receipt_layout_shape")
        label = layout.get("layout")
        require(label in (PLAIN, SIZED) and label not in by_name, "receipt_layout_labels")
        false_fields(layout, ("layout_selected", "native_build_flag_proven",
                              "array_total_length_known", "target_contents_decoded"),
                     "receipt_layout_selection_claim")
        by_name[label] = layout
    plain, sized = by_name[PLAIN], by_name[SIZED]
    plain_va = contract.struct_base + 8 * contract.property_index
    sized_va = contract.struct_base + 16 * contract.property_index
    slot_va = sized_va + 8
    for row, stride, offset, address in ((plain, 8, 0, plain_va), (sized, 16, 8, sized_va)):
        for name, expected in (("entry_stride", stride), ("pointer_offset", offset), ("record_va", address)):
            integer(row.get(name), expected, "receipt_layout_geometry")
    require(plain.get("status") == "layout_mapping_not_validated"
            and plain.get("error_code") == "missing_or_ambiguous_pointer_mapping"
            and "target" not in plain and "pointer_slot" not in plain,
            "receipt_plain_unknown_must_be_preserved")
    plain_record = mapped_record(plain.get("record_mapping"), va=plain_va, size=8,
                                 file_offset=contract.plain_file_offset,
                                 load_index=contract.load_index)
    require(sized.get("status") == "slot_and_target_mapping_observed"
            and sized.get("declared_size_is_array_total_length") is False,
            "receipt_sized_observation_status")
    integer(sized.get("declared_size"), contract.declared_size, "receipt_declared_size")
    sized_record = mapped_record(sized.get("record_mapping"), va=sized_va, size=16,
                                 file_offset=contract.sized_file_offset,
                                 load_index=contract.load_index)
    slot = mapped_record(sized.get("pointer_slot"), va=slot_va, size=8,
                         file_offset=contract.slot_file_offset,
                         load_index=contract.load_index)
    target = mapped_record(sized.get("target"), va=contract.target_va, size=1,
                           file_offset=contract.target_file_offset,
                           load_index=contract.load_index)
    return {
        "scalar_candidates": scalars,
        "plain": {"layout": PLAIN, "record_mapping": plain_record,
                  "status": "layout_mapping_not_validated",
                  "error_code": "missing_or_ambiguous_pointer_mapping",
                  "target": None, "native_layout_selected": False},
        "sized": {"layout": SIZED, "record_mapping": sized_record,
                  "pointer_slot": slot, "target_mapping": target,
                  "declared_size": contract.declared_size,
                  "declared_size_is_array_total_length": False,
                  "native_layout_selected": False},
    }


def base_report():
    return {
        "schema": SCHEMA, "analysis_completed": False,
        "observation_mode": "explicit_conditional_observed_slot",
        "observation_is_native_selection": False,
        "native_pointer_pin_available": False,
        "platform_static_selection_proven": False, "entry_reachable": False,
        "smem_initialized": False, "handoff_generated": False,
        "abi_confirmed": False, "selected_layout": None,
        "array_total_length_known": False, "firmware_executed": False,
        "core_source_sha256": CORE_SHA256,
        "dal_source_sha256": DAL_SOURCE_SHA256,
        "fixed_observation_archive_commit": DAL_ARCHIVE_COMMIT,
        "record_cap": RECORD_CAP,
        "maximum_target_forward_span_bytes": RECORD_CAP * 36,
        "byte_identity_is_hardware_attestation": False,
    }


def observe_bytes(image, receipt, contract):
    """No private-input I/O; load the fixed own-source core and never select ABI."""
    result = base_report()
    try:
        core = load_core()
        observed = validate_receipt(receipt, image, contract)
        result["input_contract_kind"] = (
            "fixed_observed_backup" if contract is OBSERVED_CONTRACT
            else "self_authored_synthetic_fixture_contract")
        result["input_identities"] = {
            "image_size": len(image), "image_sha256": sha(image),
            "dal_receipt_size": len(receipt), "dal_receipt_sha256": sha(receipt)}
        result["scalar_candidates"] = observed["scalar_candidates"]
        result["other_layout_observations"] = [observed["plain"]]
        result["observed_candidate_slot_pin"] = {
            "identity_kind": "observed_slot_only",
            "property_index": contract.property_index,
            "struct_pointer_array_va": contract.struct_base,
            **observed["sized"],
        }
        # This local PointerPin only binds the observed bytes for the pure core.
        # It is never assigned to core.NATIVE_PIN and carries no native claim.
        pin = core.PointerPin(
            image_sha256=contract.image_sha256,
            dal_report_sha256=contract.receipt_sha256,
            property_index=contract.property_index,
            pointer_slot_va=contract.struct_base + 16 * contract.property_index + 8,
            pointer_slot_file_offset=contract.slot_file_offset,
            pointer_slot_load_index=contract.load_index,
            target_va=contract.target_va,
            target_file_offset=contract.target_file_offset,
            target_load_index=contract.load_index)
        decoded = core.analyze_bytes(image, pin, record_cap=RECORD_CAP,
                                     include_enum16_control=True)
        require(core.NATIVE_PIN is None, "core_native_pin_changed")
        require(decoded.get("native_pointer_pin_available") is False
                and decoded.get("native_dal_adapter_reviewed") is False
                and decoded.get("selected_layout") is None, "core_selection_contract")
        false_fields(decoded, ("platform_static_selection_proven", "entry_reachable",
                               "smem_initialized", "handoff_generated", "abi_confirmed",
                               "array_total_length_known"), "core_runtime_claim")
        if decoded.get("status") != "candidate_analysis":
            result["core_rejection"] = decoded.get("rejection")
            raise Rejected("core_observed_pointer_rejected")
        candidates = decoded.get("candidates")
        require(type(candidates) is list and len(candidates) == 2, "core_layout_count")
        for candidate, label, stride in zip(
                candidates, ("enum32_stride36", "enum16_stride32"), (36, 32)):
            require(candidate.get("layout") == label and candidate.get("stride") == stride,
                    "core_layout_identity")
            false_fields(candidate, ("selection_proven", "abi_confirmed",
                                     "array_total_length_known"), "core_layout_claim")
            entries, anomalies = candidate.get("entries"), candidate.get("anomalies")
            require(type(entries) is list and len(entries) <= RECORD_CAP
                    and type(anomalies) is list, "core_entry_bound")
            end = candidate.get("highest_read_end_va")
            require(type(end) is int and contract.target_va <= end <= contract.target_va + RECORD_CAP * stride,
                    "core_forward_span_bound")
        result["conditional_partition_candidates"] = decoded
        result["core_native_pin_remains_none"] = True
        result["analysis_completed"] = True
        result["status"] = "conditional_candidate_observation"
    except Rejected as error:
        result["status"] = "rejected"
        result["rejection"] = {"code": error.code}
    except Exception as error:
        result["status"] = "rejected"
        result["rejection"] = {"code": "unexpected_" + type(error).__name__}
    return result


def bounded_json(report):
    result = copy.deepcopy(report)
    decoded = result.get("conditional_partition_candidates", {})
    candidates = decoded.get("candidates", [])
    for row in candidates:
        row["entries_total_observed"] = len(row["entries"])
        row["entries_omitted"] = 0
        row["anomalies_total_observed"] = len(row["anomalies"])
        row["anomalies_omitted"] = 0
    result["detail_totals_include_omitted_observations"] = True
    while True:
        raw = (json.dumps(result, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
        if len(raw) <= MAX_OUTPUT:
            return raw
        rows = [row for row in candidates if row.get("entries")]
        if rows:
            row = max(rows, key=lambda value: len(value["entries"]))
            row["entries"].pop()
            row["entries_omitted"] += 1
            continue
        rows = [row for row in candidates if row.get("anomalies")]
        if rows:
            row = max(rows, key=lambda value: len(value["anomalies"]))
            row["anomalies"].pop()
            row["anomalies_omitted"] += 1
            continue
        raise Rejected("bounded_metadata_output_limit")


def main(argv=None):
    arguments = sys.argv[1:] if argv is None else argv
    if arguments != [MODE]:
        report = base_report()
        report.update(status="rejected", rejection={"code": "explicit_conditional_mode_required"})
        sys.stdout.buffer.write(bounded_json(report))
        return 64
    try:
        # Reject a changed analysis core before reading either private input.
        load_core()
        image = read_regular(IMAGE_PATH, OBSERVED_CONTRACT.image_size)
        receipt = read_regular(RECEIPT_PATH, OBSERVED_CONTRACT.receipt_size)
        report = observe_bytes(image, receipt, OBSERVED_CONTRACT)
        output = bounded_json(report)
        sys.stdout.buffer.write(output)
        return 0 if report["analysis_completed"] else 2
    except Rejected as error:
        report = base_report()
        report.update(status="rejected", rejection={"code": error.code})
    except Exception as error:
        report = base_report()
        report.update(status="rejected", rejection={"code": "unexpected_" + type(error).__name__})
    sys.stdout.buffer.write(bounded_json(report))
    return 2


if __name__ == "__main__":
    sys.exit(main())
