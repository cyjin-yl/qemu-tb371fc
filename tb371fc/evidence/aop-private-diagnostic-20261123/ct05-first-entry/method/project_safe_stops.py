import datetime, hashlib, json, stat, sys
from pathlib import Path
root = Path("/work/aop-reference-private-after-ct05")
profiles = ("elf-physical-entry", "local-alias-vector", "local-alias-dtcm32k-reference")
reasons = {"unsupported-access-before-bus", "unsupported-instruction-fetch",
           "instruction-crosses-executable-file-boundary",
           "blocking-or-exception-instruction", "architectural-exception-after-step",
           "instruction-budget"}
loads = ((2, 0x0b000000, 0x14f08, 5), (3, 0x0b0e0000, 0x604c, 6),
         (4, 0x80800000, 0xdfc8, 6), (5, 0x80810000, 0x1384, 6),
         (6, 0x80818000, 0x47b0, 7))
def digest(raw):
    return hashlib.sha256(raw).hexdigest()
def uint(value):
    if type(value) is not int or not 0 <= value < 2 ** 32:
        raise RuntimeError("Unexpected numeric address metadata")
    return value
def hx(value):
    return "0x%08x" % uint(value)
def location(pc, profile):
    matches = []
    for index, base, size, flags in loads:
        if base <= pc < base + size:
            matches.append({"elf_program_header_index": index, "view": "ELF physical",
                            "mapping_enabled": True, "base": hx(base), "memsz": size,
                            "offset": pc - base, "flags": flags})
    for index, base, size, flags, physical in ((2, 0, 0x14f08, 5, 0xb000000),
                                              (3, 0xe0000, 0x604c, 6, 0xb0e0000)):
        if base <= pc < base + size:
            matches.append({"elf_program_header_index": index, "view": "reference low alias",
                            "mapping_enabled": profile != profiles[0], "base": hx(base),
                            "memsz": size, "offset": pc - base, "flags": flags,
                            "corresponding_elf_physical_address": hx(physical + pc - base)})
    return matches
out = {"schema": "aop-private-ct05-safe-stop-projection-v1", "read_only": True,
       "observed_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
       "raw_results_private": True, "raw_results_transferred": False,
       "raw_instruction_bytes_exported": False, "register_dump_exported": False,
       "firmware_payload_read_by_projection": False,
       "full_firmware_boot": False, "full_tablet_equivalence": False,
       "internal_exception_before_access_proven": False,
       "root": str(root), "root_exists": root.is_dir(), "profiles": []}
if root.is_dir():
    out["root_mode"] = oct(stat.S_IMODE(root.stat().st_mode))
path = root / "job.exit"
out["job_finalized"] = path.is_file()
if path.is_file():
    out["job_exit"] = path.read_text().strip()
summary_path = root / "result.json"
if summary_path.is_file():
    raw = summary_path.read_bytes()
    summary = json.loads(raw)
    out["private_supervisor_result_sha256"] = digest(raw)
    out["private_supervisor_result_bytes"] = len(raw)
    fields = ("diagnostic_completed", "supervision_completed",
              "ordinary_tcg_access_preflight_passed", "inputs_unchanged",
              "firmware_read", "firmware_execution_requested",
              "actual_aop_core", "actual_reset_mapping", "hardware_reset_verified",
              "reference_cpu", "reference_clock_hz", "full_firmware_start",
              "aop_startup_validated", "maximum_steps_per_profile",
              "maximum_seconds_per_profile", "global_execution_deadline_seconds",
              "started_utc", "finished_utc", "wall_seconds", "not_run_profiles",
              "final_cleanup")
    out["supervision"] = {key: summary[key] for key in fields if key in summary}
    out["supervision"]["exception_present"] = "exception" in summary
    if "exception" in summary:
        out["supervision"]["exception_sha256"] = digest(summary["exception"].encode())
    out["profile_processes"] = []
    for item in summary.get("profiles", []):
        fields = ("profile", "exit_code", "wall_seconds", "diagnostic_completed",
                  "stop_reason", "steps_attempted", "completed_single_steps",
                  "cleanup", "timed_out", "result_sha256", "result_bytes")
        out["profile_processes"].append({key: item[key] for key in fields if key in item})
for index, name in enumerate(profiles):
    path = root / "profiles" / ("%02d" % index) / "result.json"
    item = {"profile": name, "result_exists": path.is_file()}
    if not path.is_file():
        out["profiles"].append(item)
        continue
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("profile") != name:
        raise RuntimeError("Profile identity differs")
    for key, expected in {
        "qemu_sha256": "c6bed2c465e0b771d6e40d60225b9f89636f6d213bf78b3c174510c24f3f4493",
        "runner_sha256": "18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b",
        "input_sha256": "3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671",
        "preflight_sha256": "8d7c4231f15a0d7a68a26ae969a06fe536f3026c313a0023b1565948a84bb5a1"}.items():
        if data.get(key) != expected:
            raise RuntimeError("Diagnostic input identity differs")
    item.update({"raw_result_sha256": digest(raw), "raw_result_bytes": len(raw),
                 "exception_present": "exception" in data})
    fields = ("diagnostic_completed", "reference_cpu", "actual_aop_cpu",
              "steps_attempted", "completed_single_steps", "maximum_steps",
              "firmware_execution_requested", "candidate_stack_is_in_writable_range",
              "host_hooks_replacing_firmware_functions", "successful_peripheral_responses",
              "invalid_backing_padding_unchanged", "exception_number")
    item.update({key: data[key] for key in fields if key in data})
    reason = data.get("stop_reason")
    if reason is not None and reason not in reasons:
        raise RuntimeError("Unexpected diagnostic stop reason")
    item["stop_reason"] = reason
    pc = data.get("stopped_pc", data.get("final_state", {}).get("pc"))
    if pc is not None:
        pc = uint(pc)
        item["stopped_pc"] = hx(pc)
        item["pc_known_load_correspondences"] = location(pc, name)
    if "unsupported_bus_counts_before" in data and "unsupported_bus_counts_after" in data:
        item["unsupported_bus_callbacks_unchanged"] = (
            data["unsupported_bus_counts_before"] == data["unsupported_bus_counts_after"])
    if "fault_address" in data:
        item["fault_address"] = hx(data["fault_address"])
    evidence = data.get("watchpoint_evidence")
    if evidence is not None:
        base, address = uint(evidence["range_base"]), uint(evidence["hitaddr"])
        matches = [entry for entry in evidence["all_marked_entries"]
                   if entry["vaddr"] == base and entry["rsp_type"] == evidence["rsp_kind"]
                   and entry["hitaddr"] == address]
        if len(matches) != 1:
            raise RuntimeError("Watchpoint stop has no unique marked entry")
        hit = matches[0]
        if hit["flags"] != evidence["flags"] or any(type(hit[k]) is not bool
                for k in ("read_hit", "write_hit", "stop_before_access", "gdb", "cpu")):
            raise RuntimeError("Watchpoint access evidence differs")
        item["watchpoint"] = {
            "actual_hit_address": hx(address), "range_base": hx(base),
            "range_length": evidence["range_length"], "rsp_kind": evidence["rsp_kind"],
            "raw_flags": evidence["flags"], "read_hit": hit["read_hit"],
            "write_hit": hit["write_hit"], "stop_before_access": hit["stop_before_access"],
            "gdb": hit["gdb"], "cpu": hit["cpu"],
            "access": "read+write" if hit["read_hit"] and hit["write_hit"] else
                      "read" if hit["read_hit"] else "write" if hit["write_hit"] else "unknown"}
        recent = data.get("recent_instruction_locations", [])
        if recent and pc is not None:
            item["stopped_pc_equals_last_attempted_pc"] = recent[-1]["pc"] == pc
    elif reason in {"unsupported-instruction-fetch", "instruction-crosses-executable-file-boundary"}:
        item["address_evidence"] = "host fetch guard before issuing CPU single-step"
    out["profiles"].append(item)
raw = (json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
if len(raw) > 32768:
    raise RuntimeError("Safe stop projection exceeds cap")
sys.stdout.buffer.write(raw)
