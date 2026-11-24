#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DRAFT: consume an accepted CI-built full-production-DT userspace fixture.

No build, artifact download, automatic follow-up boot, or helper main() call.
This adapter has only been prepared and statically reviewed. Invocation is a
separate root-controlled action after the same-binary primary diagnostic.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import subprocess
import sys
import time
import zlib

sys.dont_write_bytecode = True

KERNEL_SHA = "ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f"
DT_SHA = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
DT_BYTES = 973052
MODEL = "Qualcomm Technologies, Inc. kona QRD"
MODEL_SHA = "9bf6d330148e3423a006773f7ece6eac4c9719787a7da87c75574a00186d2878"
DT_RECEIPT_SHA = "d7c70cfcdc7ac0d19df25feb22c3431d047129a85d03b4f306e97f5bd1f08e28"
CMD_DB_SHA = "a971809c06fa1a3ff44c9d28463bc6ff53203d9375ffd0f76dc73d4c8a3ea6f9"
PRIMARY_INITRD_SHA = "8c2f582b72f19ba6043af7e85f91a3936c3c8fcb3c2a306ae4f76efe6d72ae7e"
NATIVE_SHA = "69c592e097069a779cc54eb38b823192b7de3ef18465eb49a3da35f4d496edf8"
PROBE_SHA = "0ed51cb6c6dd24685beb3f77056d17d47c5c2f697df96d87eef4a678d4c5cb1b"
PROBE_BYTES = 2264
BUILDER_SHA = "644f398957859b707fc5f6135875d3b000074e346ff93a8a1a386214944a5fa1"
HELPERS = {
    "boot_smoke_gdb.py": "93e046b915b71cd74198e8dd518564c4c0e7ea85b75a29966f08c42a8897b737",
    "boot_smoke.py": "4889f2cdd2e3fd3331cc57f3bf1a6b6ed8894f87503283c7f404766919540f7d",
    "boot_qmp.py": "b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c",
    "boot_verdict.py": "e0505903c5f52986a8650ffa38933db183501bca416e9876fac4560b08537cdf",
    "boot_verdict_actual_dt.py": "2e1a7d8d7e60abbede54385cba11ed1e50067b5f209ab119d15739e537b989ef",
}
PINNED_MANIFEST_INPUTS = {
    "tests/guest-init.c": "4f1aac3a68e6c660abebdc04f239191a46ee1330ffb47a494ee37d6081d38a79",
    "tests/guest-init-actual-dt.c": "4c884da703dcde0fc33912ac8e0484422984b82f592fbd89e5f8f845ee7d9840",
    "tests/guest-result.h": "0087f4feaaddb78762685fc2810e010a0edee9cb23386bff62584e5f1af133c3",
    "tests/arch-probe.S": "b976d2cc83c8782d0832c27f42df3491459d01d4dd164c97e10b50d90147125f",
    "scripts/build_arch_probe.sh": "70b27ae44f78e0f7d4479ae51d72de53fe7c191bfee0c6936ebda1b0612bbf2c",
    "scripts/build_actual_dt_fixture.py": BUILDER_SHA,
    "evidence/android-arch-probe.json": NATIVE_SHA,
    **{"scripts/" + name: digest for name, digest in HELPERS.items()
       if name != "boot_qmp.py"},
}
GLOBALS = (
    "qcom-refgen.boot-disabled-reference=on",
    "qcom-tcsr-mutex.boot-unlocked-reference=on",
    "qcom-gpu-cx-gdsc.boot-collapsed-reference=on",
    "qcom-gdsc.enabled-reference=on",
    "qcom-sm8250-gpucc.enabled-reference=on",
    "qcom-sm8250-npucc.enabled-reference=on",
    "qcom-noc-qos.enabled-reference=on",
    "qcom-smmuv500.enabled-reference=on",
    "qcom-smmuv500.reference-hw-version=0x02000000",
    "qcom-tsens24xx.enabled-reference=on",
    "qcom-tsens24xx.reference-boot-enable-mask=0xffff",
    "qcom-tsens24xx.reference-temperature-millicelsius=25000",
    "qcom-cpufreq-hw-epss.enabled-reference=on",
    "qcom-cpufreq-hw-epss.reference-boot-perf-index=0",
    "qcom-devfreq-fw.enabled-reference=on",
    "qcom-devfreq-fw.reference-boot-perf-index=0",
    "qcom-noc-sbm.enabled-reference=on",
)
KNOWN_RPMH_REFERENCE_GLOBAL = "qcom-sm8250-rpmh.reference-wait-mask-reserved=on"
KNOWN_CAMERA_REFERENCE_GLOBAL = "qcom-camera-gdsc.enabled-reference=on"
CHECK_NAMES = (
    "procfs", "sysfs", "aarch64", "eight_online_cpus_psci",
    "timer_and_scheduler_progress", "fork_and_wait", "board_model",
    "arch_counter_probe",
)
LINUX_GATES = (
    "linux-console-userspace", "linux-console-empty-ufs",
    "linux-ufs-smmu", "linux-ufs-rpmh",
)
PREFIX = "tb371fc-results/full-dtb-user-fixture/"
STAGED_NAMES = (
    "manifest.json", "init", "initramfs.cpio.gz", "arch-probe",
    "init.nm.txt", "init.readelf.txt", "init.checkpoint.objdump.txt",
    "source-commit.txt",
)
RECORD_BYTES, CHECKPOINT_ADDRESS = 3120, 0x08000000


def require(value, reason):
    if not value:
        raise ValueError(reason)


def equal(actual, expected, reason):
    require(type(actual) is type(expected) and actual == expected, reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def regular(path):
    path = Path(path).absolute()
    # No staged symlink or parent symlink may redirect an accepted member.
    for item in (path, *path.parents):
        require(not item.is_symlink(), "symlink input: " + str(item))
    require(stat.S_ISREG(path.stat().st_mode), "not a regular input: " + str(path))
    return path


def track(path, expected, identities, size=None):
    require(type(expected) is str and re.fullmatch(r"[0-9a-f]{64}", expected),
            "missing exact input SHA256")
    path = regular(path)
    if size is not None:
        require(type(size) is int and size >= 0 and path.stat().st_size == size,
                "input size mismatch: " + str(path))
    require(sha(path) == expected, "input digest mismatch: " + str(path))
    identities[str(path)] = expected
    return path


def tracked_json(path, expected, identities):
    return json.loads(track(path, expected, identities).read_text())


def verify_camera_primary(baseline, extra, additional, camera_global):
    """Require the new stopped observer when that exact camera profile is used."""
    if camera_global not in additional:
        return None
    peer = baseline.get("aop_peer")
    require(type(peer) is dict, "camera primary peer evidence is missing")
    camera = peer.get("camera_gdsc_observer")
    require(type(camera) is dict, "camera primary observation is missing")
    equal(extra.get("camera_gdsc_observer"), camera,
          "camera primary extra and original diagnostic differ")
    for key, value in {
            "schema": "tb371fc-camera-gdsc-stopped-observer-v1",
            "observation_status": "complete",
            "explicit_reference_policy_enabled": True,
            "raw_state_stable_during_capture": True,
            "reference_state_invariants_match": True,
            "camera_qom_properties_per_snapshot": 78,
            "parent_qom_properties_per_snapshot": 2,
            "model_api_sha256": "05c104c8f37cc8349af650cdbab18f0b7dd051b993553bd3c218fac297b803ff",
            "actual_dt_capture_sha256": "6dbe11407254a358648375ec13bb07baf5c7cee06f6709e835d9e2908bdbf0c1",
            "actual_parent_dt_capture_sha256": "8a8bdded1c180c56a9029a3ef954c1817554b70e8cda52696bebb9075fb56ac2",
    }.items():
        equal(camera.get(key), value, "camera primary observer differs: " + key)
    equal(camera["raw_state"], camera["raw_state_after"],
          "camera primary raw snapshots differ")
    for key in ("stopped_status", "stopped_status_after"):
        require(type(camera.get(key)) is dict and camera[key].get("running") is False,
                "camera primary observer was not stopped")
    equal(camera["stopped_status_after"]["status"], camera["stopped_status"]["status"],
          "camera primary stop status changed")
    require(type(camera.get("domains")) is list and len(camera["domains"]) == 6,
            "camera primary domain coverage differs")
    return {"complete_stopped_camera_observer_required": True,
            "logical_parent_cross_check_required": True,
            "camera_snapshot_is_userspace_success": False}


def verify_acceptance(cfg, identities):
    """Bind root's accepted artifact receipt and the one same-binary primary."""
    require(re.fullmatch(r"[0-9a-f]{40}", cfg["source_commit"]), "exact source commit required")
    for key in ("qemu_sha256", "runner_sha256", "artifact_sha256", "manifest_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", cfg[key]), "exact " + key + " required")
    for key in ("workflow_run", "artifact_id"):
        require(type(cfg[key]) is int and cfg[key] > 0, "invalid " + key)
    order = cfg["expected_gate_order"]
    require(type(order) is dict and set(order) == {"components", "linux"}, "two expected gate plans required")
    equal(order["linux"], list(LINUX_GATES), "the four original Linux gates changed")
    for suite, names in order.items():
        require(type(names) is list and names and all(type(n) is str and n for n in names)
                and len(names) == len(set(names)), "invalid expected gate order: " + suite)
    artifact = tracked_json(cfg["artifact_receipt"], cfg["artifact_receipt_sha256"], identities)
    for actual, expected in (
            (artifact["source"], cfg["source_commit"]),
            (artifact["workflow_run"], cfg["workflow_run"]),
            (artifact["artifact_id"], cfg["artifact_id"]),
            (artifact["artifact_sha256"], cfg["artifact_sha256"]),
            (artifact["qemu_sha256"], cfg["qemu_sha256"])):
        equal(actual, expected, "accepted artifact identity mismatch")
    suites = artifact["suites"]
    require(type(suites) is list and len(suites) == 2 and
            {s["suite"] for s in suites} == set(order), "two accepted suites required")
    for suite in suites:
        for key, expected in {
                "passed": True, "qemu_unchanged": True, "source_dirty": False,
                "source_commit": cfg["source_commit"], "qemu_sha256": cfg["qemu_sha256"],
                "runner_sha256": cfg["runner_sha256"], "gate_order": order[suite["suite"]],
                "passed_gates": len(order[suite["suite"]]),
                "expected_gates": len(order[suite["suite"]]),
        }.items():
            equal(suite[key], expected, "accepted suite mismatch: " + key)
        require(re.fullmatch(r"[0-9a-f]{64}", suite["receipt_sha256"]), "suite receipt digest missing")
    baseline = tracked_json(cfg["baseline_diagnostic"], cfg["baseline_diagnostic_sha256"], identities)
    primary = tracked_json(cfg["primary_summary"], cfg["primary_summary_sha256"], identities)
    for key, expected in {
            "source_commit": cfg["source_commit"], "qemu_sha256": cfg["qemu_sha256"],
            "diagnostic_sha256": cfg["baseline_diagnostic_sha256"], "qemu_unchanged": True,
            "kernel_sha256": KERNEL_SHA, "dtb_sha256": DT_SHA,
            "observer_error": None, "legacy_printk_error": None,
    }.items():
        equal(primary[key], expected, "same-binary primary summary mismatch: " + key)
    for key, expected in {"qemu_sha256": cfg["qemu_sha256"], "kernel_sha256": KERNEL_SHA,
                          "dtb_sha256": DT_SHA}.items():
        equal(baseline[key], expected, "primary diagnostic mismatch: " + key)
    extra = tracked_json(cfg["primary_extra"], cfg["primary_extra_sha256"], identities)
    for key, expected in {"schema": "tb371fc-native-diagnostic-extra-v1",
                          "source_commit": cfg["source_commit"], "qemu_sha256": cfg["qemu_sha256"]}.items():
        equal(extra[key], expected, "same-binary primary extra mismatch: " + key)
    evidence = extra["raw_evidence"]
    require(type(evidence) is list and all(type(row) is dict and type(row.get("name")) is str for row in evidence)
            and len({row["name"] for row in evidence}) == len(evidence), "invalid primary raw-evidence inventory")
    by_name = {row["name"]: row for row in evidence}
    require("diagnostic.json" in by_name and "kernel-printk.log" in by_name, "primary complete diagnostic/kernel evidence missing")
    equal(by_name["diagnostic.json"]["sha256"], cfg["baseline_diagnostic_sha256"], "extra/diagnostic raw hash binding")
    equal(by_name["diagnostic.json"]["bytes"], Path(cfg["baseline_diagnostic"]).stat().st_size,
          "extra/diagnostic raw size binding")
    kernel_item = by_name["kernel-printk.log"]
    kernel_log = track(Path(cfg["baseline_diagnostic"]).absolute().parent / "kernel-printk.log",
                       kernel_item["sha256"], identities, kernel_item["bytes"])
    require(0 < kernel_item["bytes"] <= 4 * 1024 * 1024, "primary kernel evidence is empty or exceeds the capture bound")
    full_kernel = kernel_log.read_text(encoding="utf-8", errors="replace")
    milestones = extra["kernel_milestones"]
    require(type(milestones) is list and milestones and all(type(line) is str for line in milestones),
            "complete primary milestone list required")
    lines = set(full_kernel.splitlines())
    require(all(line in lines for line in milestones), "primary milestone is absent from the hash-bound complete kernel log")
    fatal_pattern = r"Kernel panic|Internal error|Unable to handle"
    require(not any(re.search(fatal_pattern, line, re.I) for line in milestones),
            "primary milestone records a fatal error; this fixture must not be launched")
    require(re.search(fatal_pattern, full_kernel, re.I) is None,
            "complete primary kernel log records a fatal error; this fixture must not be launched")
    observer_containers = [baseline, primary, extra, baseline.get("aop_peer") or {}]
    for container in observer_containers:
        require(type(container) is dict, "malformed primary observer container")
        for name, value in container.items():
            if name.endswith("observer_error") or name == "legacy_printk_error":
                equal(value, None, "primary observer failed: " + name)
    primary_proof = {"primary_extra_sha256": cfg["primary_extra_sha256"],
                     "kernel_printk_sha256": kernel_item["sha256"], "kernel_printk_bytes": kernel_item["bytes"],
                     "complete_milestone_count": len(milestones), "full_kernel_log_checked_not_tail": True,
                     "fatal_patterns": ["Kernel panic", "Internal error", "Unable to handle"],
                     "fatal_patterns_absent": True, "observer_errors_absent": True,
                     "device_error_count_not_a_fatal_gate": True}
    primary_proof["camera_reference"] = verify_camera_primary(
        baseline, extra, cfg["additional_reference_globals"], KNOWN_CAMERA_REFERENCE_GLOBAL)
    # A fatal-free diagnostic is a prerequisite here, not a userspace verdict.
    # Whether to attempt this fixture remains a separate root decision.
    return artifact, baseline, primary, primary_proof


def verify_stage(cfg, identities):
    stage = Path(cfg["fixture_stage"]).absolute()
    require(stage.is_dir() and not stage.is_symlink(), "accepted fixture stage directory required")
    receipt = tracked_json(cfg["fixture_stage_receipt"], cfg["fixture_stage_receipt_sha256"], identities)
    for key, expected in {
            "schema": "tb371fc-accepted-full-dtb-fixture-stage-v1",
            "source_commit": cfg["source_commit"], "workflow_run": cfg["workflow_run"],
            "artifact_id": cfg["artifact_id"], "artifact_sha256": cfg["artifact_sha256"],
            "qemu_sha256": cfg["qemu_sha256"], "runner_sha256": cfg["runner_sha256"],
            "manifest_member": PREFIX + "manifest.json", "manifest_sha256": cfg["manifest_sha256"],
            "staged_from_verified_zip": True, "binary_source_audit_passed": True,
    }.items():
        equal(receipt[key], expected, "accepted fixture stage mismatch: " + key)
    members = receipt["files"]
    require(type(members) is list and len(members) == len(STAGED_NAMES), "exact staged member inventory required")
    files = {}
    for item in members:
        member = item["member"]
        require(type(member) is str and member.startswith(PREFIX), "unexpected ZIP member")
        relative = PurePosixPath(member)
        require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in member
                and "\0" not in member and str(relative) == member, "unsafe stage member path")
        name = member[len(PREFIX):]
        require(name in STAGED_NAMES and name not in files, "missing/duplicate/extra staged fixture member")
        equal(item["relative_path"], member, "stage layout must retain the exact ZIP member name")
        files[name] = track(stage.joinpath(*relative.parts), item["sha256"], identities, item["bytes"])
    equal(set(files), set(STAGED_NAMES), "incomplete fixture stage")
    equal(sha(files["manifest.json"]), cfg["manifest_sha256"], "staged manifest digest")
    equal(files["source-commit.txt"].read_text().strip(), cfg["source_commit"], "fixture build source identity")
    return files, json.loads(files["manifest.json"].read_text()), receipt


def inspect_init(raw, manifest, nm_text, objdump_text):
    """Independently link raw ELF symbols, sections, PT_LOADs and the RET."""
    require(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01", "init must be ELF64 little-endian")
    etype, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = struct.unpack_from("<HHIQQQIHHHHHH", raw, 16)
    require((etype, machine, version, ehsize, phsize, shsize) == (2, 183, 1, 64, 56, 64), "ELF64 ET_EXEC/AArch64 geometry mismatch")
    require(0 < phnum < 0xffff and 0 < shnum and 0 <= shstr < shnum,
            "unsupported extended or empty ELF table")
    require(phoff + phsize * phnum <= len(raw) and shoff + shsize * shnum <= len(raw), "ELF tables exceed actual bytes")

    def span(offset, length):
        require(0 <= offset <= len(raw) and 0 <= length <= len(raw) - offset, "ELF range exceeds actual bytes")
        return raw[offset:offset + length]

    def string(table, offset):
        require(0 <= offset < len(table), "ELF string outside table")
        end = table.find(b"\0", offset)
        require(end >= 0, "unterminated ELF string")
        return table[offset:end].decode("ascii")

    programs = []
    for i in range(phnum):
        values = struct.unpack_from("<II6Q", raw, phoff + i * phsize)
        p = dict(zip(("type", "flags", "file_offset", "virtual_address", "physical_address",
                      "file_bytes", "memory_bytes", "alignment"), values))
        require(p["type"] != 3, "PT_INTERP is not allowed")
        span(p["file_offset"], p["file_bytes"])
        require(p["type"] != 1 or p["file_bytes"] <= p["memory_bytes"], "invalid PT_LOAD sizes")
        programs.append(p)
    sections = [struct.unpack_from("<II4QII2Q", raw, shoff + i * shsize) for i in range(shnum)]
    require(sections[shstr][1] == 3, "section-name table is not STRTAB")
    names = span(sections[shstr][4], sections[shstr][5])
    public_sections = []
    for section in sections:
        if section[1] != 8:
            span(section[4], section[5])
        public_sections.append({"name": string(names, section[0]), "type": section[1],
                                "flags": section[2], "virtual_address": section[3],
                                "file_offset": section[4], "bytes": section[5], "alignment": section[8]})
    needed = {"tb371fc_guest_checkpoint": (2, 4), "tb371fc_guest_result": (1, RECORD_BYTES)}
    symbols = {}
    for section in sections:
        if section[1] != 2:
            continue
        require(section[9] == 24 and section[5] % 24 == 0 and section[6] < shnum, "invalid ELF symbol table")
        strings = sections[section[6]]
        require(strings[1] == 3, "symbol sh_link does not name STRTAB")
        table = span(strings[4], strings[5])
        for offset in range(section[4], section[4] + section[5], 24):
            name_offset, info, other, index, address, size = struct.unpack_from("<IBBHQQ", raw, offset)
            name = string(table, name_offset)
            if name not in needed:
                continue
            require(name not in symbols and info >> 4 == 1 and (info & 15, size) == needed[name]
                    and 0 < index < shnum, "required symbol is duplicate, undefined or wrong type/size")
            target = sections[index]
            require(target[3] <= address and address + size <= target[3] + target[5], "symbol exceeds its section")
            symbols[name] = {"address": address, "size": size, "section_index": index,
                             "st_type": info & 15, "st_bind": info >> 4}
    equal(set(symbols), set(needed), "missing actual ELF checkpoint/result symbol")
    cp, result = symbols["tb371fc_guest_checkpoint"], symbols["tb371fc_guest_result"]
    require(cp["address"] == CHECKPOINT_ADDRESS and result["address"] > 0, "wrong checkpoint/result virtual address")
    cs, rs = sections[cp["section_index"]], sections[result["section_index"]]
    require(public_sections[cp["section_index"]]["name"] == ".tb371fc_checkpoint" and
            cs[1] == 1 and cs[2] & 6 == 6 and cs[3] == cp["address"] and cs[5] == 4,
            "checkpoint section must be the four executable RET bytes")
    require(rs[1] in (1, 8) and rs[2] & 3 == 3, "result section must be allocated writable PROGBITS/NOBITS")
    executable = [p for p in programs if p["type"] == 1 and p["flags"] & 1 and
                  p["virtual_address"] <= cp["address"] and cp["address"] + 4 <= p["virtual_address"] + p["file_bytes"]]
    writable = [p for p in programs if p["type"] == 1 and p["flags"] & 2 and
                p["virtual_address"] <= result["address"] and
                result["address"] + RECORD_BYTES <= p["virtual_address"] + p["memory_bytes"]]
    require(len(executable) == 1 and len(writable) == 1, "required symbols lack unique corresponding PT_LOADs")
    checkpoint_offset = executable[0]["file_offset"] + cp["address"] - executable[0]["virtual_address"]
    require(checkpoint_offset == cs[4] and span(checkpoint_offset, 4) == bytes.fromhex("c0035fd6"),
            "checkpoint PT_LOAD/section file offsets or RET bytes disagree")
    nm = {}
    for line in nm_text.splitlines():
        words = line.split()
        if len(words) == 4 and words[3] in needed:
            require(words[3] not in nm, "duplicate required nm symbol")
            nm[words[3]] = {"address": int(words[0], 16), "size": int(words[1], 16), "type": words[2]}
    expected_nm = {
        "tb371fc_guest_checkpoint": {"address": cp["address"], "size": 4, "type": "T"},
        "tb371fc_guest_result": {"address": result["address"], "size": RECORD_BYTES, "type": "B" if rs[1] == 8 else "D"},
    }
    equal(nm, expected_nm, "actual ELF and nm symbol evidence disagree")
    instructions = re.findall(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+([^\n]+)$", objdump_text, re.MULTILINE)
    require(len(instructions) == 1 and int(instructions[0][0], 16) == cp["address"] and
            instructions[0][1] == "d65f03c0" and instructions[0][2].strip() == "ret", "objdump RET evidence differs")
    for key, expected in {
            "class": "ELF64", "endianness": "little", "elf_type": "ET_EXEC", "machine": "AArch64",
            "e_machine": 183, "entry_address": entry, "program_headers": programs,
            "sections": public_sections, "interpreter_present": False, "elf_flags": flags,
            "symbols": expected_nm, "checkpoint_instruction_bytes": "c0035fd6",
            "result_address_from_this_build": result["address"], "result_address_reused_from_old_fixture": False,
    }.items():
        equal(manifest[key], expected, "manifest and raw ELF disagree: " + key)
    return {"checkpoint_address": cp["address"], "checkpoint_file_offset": checkpoint_offset,
            "checkpoint_instruction_bytes": "c0035fd6", "result_address": result["address"],
            "result_bytes": RECORD_BYTES, "raw_elf_symbols": symbols,
            "checkpoint_program_header": executable[0], "result_program_header": writable[0],
            "derived_from_this_staged_elf": True, "nm_or_other_subprocess_invoked": False}


def verify_fixture(cfg, files, manifest, helpers):
    """Validate the staged bytes; CI absolute paths are never used as inputs."""
    equal(manifest["schema"], "tb371fc-full-dtb-user-fixture-v1", "wrong fixture manifest schema")
    equal(manifest["build_completed"], True, "fixture did not complete its formal CI build")
    equal(manifest["source_commit"], cfg["source_commit"], "fixture and tested QEMU source differ")
    require("build_error" not in manifest, "fixture has a build error")
    equal(manifest["execution"], {"performed": False, "userspace_passed": None,
                                  "guest_shutdown_observed": None, "android_boot_passed": None},
          "build-only manifest must not claim guest execution")
    dt = manifest["target_dt"]
    for key, expected in {"sha256": DT_SHA, "exact_model": MODEL,
                          "dtb_read_by_builder": False, "model_measured_by_builder": False}.items():
        equal(dt[key], expected, "fixture DT binding mismatch: " + key)
    equal(dt["identity_receipt"]["sha256"], DT_RECEIPT_SHA, "unapproved direct model identity receipt")
    equal(dt["identity_receipt"]["bytes"], 3064, "wrong direct model receipt size")
    equal(set(manifest["inputs"]), set(PINNED_MANIFEST_INPUTS), "fixture source inventory changed")
    for path, expected in PINNED_MANIFEST_INPUTS.items():
        equal(manifest["inputs"][path]["sha256"], expected, "fixture source changed: " + path)
    contract = {
        "check_count": 8, "check_names": list(CHECK_NAMES), "model_check_bit": 6,
        "exact_model": MODEL, "result_bytes": RECORD_BYTES, "magic": 0x3153455243464254,
        "version": 1, "header_bytes": 112, "probe_stdout_offset": 1072, "probe_capacity": 2048,
        "expected_checks_completed": 255, "expected_pass_mask": 255, "expected_fail_mask": 0,
        "minimum_elapsed_ns": 20000000, "expected_online_cpus": "0-7\n",
        "counter_frequency_hz": 19200000, "counter_loop_iterations": 65536,
        "guest_only_difference_is_exact_model_predicate": True,
        "validator_only_difference_is_exact_model_predicate": True,
        "guest_shutdown_requires_separate_actual_qmp_evidence": True,
        "old_four_linux_gates_modified": False,
    }
    equal(manifest["guest_contract"], contract, "eight-check ABI or verdict contract changed")

    def matches_file(name, record):
        equal(record["sha256"], sha(files[name]), "manifest/file hash mismatch: " + name)
        equal(record["bytes"], files[name].stat().st_size, "manifest/file size mismatch: " + name)

    matches_file("init", manifest["init"])
    matches_file("arch-probe", manifest["arch_probe"])
    matches_file("initramfs.cpio.gz", manifest["initramfs"])
    for name, key in (("init.nm.txt", "nm_log"), ("init.readelf.txt", "readelf_log"),
                      ("init.checkpoint.objdump.txt", "objdump_log")):
        matches_file(name, manifest["init"][key])
    for key, expected in {"sha256": PROBE_SHA, "bytes": PROBE_BYTES,
                          "identical_to_native_tested_elf": True, "elf_type": "ET_EXEC",
                          "e_machine": 183, "interpreter_present": False}.items():
        equal(manifest["arch_probe"][key], expected, "native-tested probe differs: " + key)
    readelf = files["init.readelf.txt"].read_text()
    require(re.search(r"Type:\s+EXEC\b", readelf) and re.search(r"Machine:\s+AArch64\b", readelf)
            and re.search(r"^\s*INTERP\s", readelf, re.MULTILINE) is None,
            "readelf does not independently report static ET_EXEC/AArch64")
    raw_init, raw_probe = files["init"].read_bytes(), files["arch-probe"].read_bytes()
    elf = inspect_init(raw_init, manifest["init"], files["init.nm.txt"].read_text(),
                       files["init.checkpoint.objdump.txt"].read_text())
    compressed = files["initramfs.cpio.gz"].read_bytes()
    require(len(compressed) >= 18 and compressed[:3] == b"\x1f\x8b\x08" and
            compressed[4:8] == bytes(4), "fixture gzip header or mtime differs")
    decoder = zlib.decompressobj(31)
    limit = 32 * 1024 * 1024
    archive = decoder.decompress(compressed, limit + 1)
    require(len(archive) <= limit and decoder.eof and not decoder.unconsumed_tail and
            not decoder.unused_data, "fixture gzip exceeds bound, is truncated or has trailing streams")
    entries = [("init", raw_init, 0o100755, 1, 0, 0), ("dev", b"", 0o040755, 2, 0, 0),
               ("dev/console", b"", 0o020600, 3, 5, 1), ("proc", b"", 0o040755, 4, 0, 0),
               ("sys", b"", 0o040755, 5, 0, 0), ("arch-probe", raw_probe, 0o100755, 6, 0, 0),
               ("TRAILER!!!", b"", 0, 7, 0, 0)]
    # Reuse only the pinned pure encoder. No archive is extracted or rewritten.
    expected_archive = b"".join(helpers.cpio_entry(*entry) for entry in entries)
    equal(archive, expected_archive, "staged initrd is not exactly the seven required entries")
    for key, expected in {
            "gzip_mtime": 0, "uncompressed_bytes": len(archive), "uncompressed_sha256": digest(archive),
            "entries_including_trailer": 7,
            "entries": [{"path": name, "bytes": len(payload), "mode": mode, "inode": inode,
                         "rdev_major": major, "rdev_minor": minor, "payload_sha256": digest(payload)}
                        for name, payload, mode, inode, major, minor in entries],
    }.items():
        equal(manifest["initramfs"][key], expected, "initrd manifest disagrees with actual archive: " + key)
    return {"elf": elf, "init_sha256": digest(raw_init), "initrd_sha256": digest(compressed),
            "arch_probe_sha256": digest(raw_probe), "archive_sha256": digest(archive),
            "archive_entries": 7, "archive_extracted_or_rebuilt_on_disk": False,
            "manifest_sha256": cfg["manifest_sha256"], "manifest_absolute_paths_used": False,
            "build_completed_is_not_guest_success": True}


def parse_baseline(baseline, cfg):
    original = baseline["command"]
    require(type(original) is list and original and all(type(x) is str for x in original),
            "malformed primary command")
    value_keys = {"-M", "-accel", "-m", "-smp", "-display", "-monitor", "-serial", "-qmp",
                  "-kernel", "-initrd", "-append", "-d", "-D", "-dtb", "-global"}
    flag_keys = {"-S", "-no-reboot", "-no-shutdown"}
    pairs, values, flags = [], {}, []
    index = 1
    while index < len(original):
        key = original[index]
        if key in flag_keys:
            require(key not in flags, "duplicate primary flag")
            flags.append(key)
            pairs.append((key, None))
            index += 1
        else:
            require(key in value_keys and index + 1 < len(original), "unexpected primary option/device/disk")
            value = original[index + 1]
            if key != "-global":
                require(key not in values, "duplicate primary option")
                values[key] = value
            pairs.append((key, value))
            index += 2
    require(set(values) == value_keys - {"-global"} and set(flags) == flag_keys, "primary option/flag shape changed")
    extra = cfg["additional_reference_globals"]
    require(type(extra) is list and extra in ([], [KNOWN_RPMH_REFERENCE_GLOBAL],
            [KNOWN_RPMH_REFERENCE_GLOBAL, KNOWN_CAMERA_REFERENCE_GLOBAL]),
            "only the reviewed ordered RPMh eighteenth and camera nineteenth globals are allowed")
    equal([value for key, value in pairs if key == "-global"], list(GLOBALS) + extra,
          "ordered primary globals differ from fixed 17 plus explicitly configured known additions")
    for key, expected in {"-accel": "tcg,thread=multi", "-smp": "8", "-m": "8G",
                          "-display": "none", "-monitor": "none", "-serial": "stdio",
                          "-append": "rdinit=/init nokaslr panic=0",
                          "-d": "unimp,guest_errors,invalid_mem,int,cpu_reset"}.items():
        equal(values[key], expected, "primary execution option changed: " + key)
    equal(values["-M"], cfg["expected_primary_machine"], "primary machine differs from the reviewed configuration")
    machine = values["-M"].split(",")
    require(machine[0] == "tb371fc", "wrong machine")
    options = {}
    for item in machine[1:]:
        key, separator, value = item.partition("=")
        require(separator and key and value and key not in options, "malformed/duplicate primary machine property")
        require(not key.startswith(("ufs-lu", "ufs-disk", "drive", "blockdev")), "unexpected disk/LU machine property")
        options[key] = value
    require(options.get("aop-profile") == "reference-qdss-vote" and "gdsc-profile" not in options,
            "wrong reference machine profile")
    return original, pairs, values, options


def prepare(cfg, out):
    identities = {}
    artifact, baseline, primary, primary_proof = verify_acceptance(cfg, identities)
    files, manifest, stage_receipt = verify_stage(cfg, identities)
    original, pairs, values, options = parse_baseline(baseline, cfg)
    qemu = track(cfg["qemu"], cfg["qemu_sha256"], identities)
    require(Path(original[0]).absolute() == qemu, "primary command did not use this installed binary path")
    track(values["-kernel"], KERNEL_SHA, identities)
    track(values["-dtb"], DT_SHA, identities, DT_BYTES)
    track(values["-initrd"], PRIMARY_INITRD_SHA, identities)
    track(options["rpmh-cmd-db"], CMD_DB_SHA, identities)
    native_path = track(cfg["native_probe_reference"], NATIVE_SHA, identities, 2391)
    track(cfg["actual_dt_model_receipt"], DT_RECEIPT_SHA, identities, 3064)
    require(digest(MODEL.encode() + b"\0") == MODEL_SHA, "root-model byte identity constant mismatch")
    helpers = Path(cfg["helpers"]).absolute()
    require(not (helpers / "__pycache__").exists() and not list(helpers.glob("*.pyc")),
            "use a fresh helper stage without precompiled Python bytecode")
    for name, expected in HELPERS.items():
        track(helpers / name, expected, identities)
        module = name[:-3]
        require(module not in sys.modules, "run in a fresh Python process; pinned helper already imported: " + module)
    # Imports supply only pure encoders/validators and transport classes. In
    # particular, boot_smoke_gdb.main() would rebuild: it is never called.
    sys.path.insert(0, str(helpers))
    h = importlib.import_module("boot_smoke_gdb")
    validator = importlib.import_module("boot_verdict_actual_dt")
    for name in HELPERS:
        equal(str(Path(sys.modules[name[:-3]].__file__).absolute()), str(helpers / name), "wrong helper import origin")
    equal(tuple(validator.CHECKS), CHECK_NAMES, "validator check ordering changed")
    equal(validator.EXPECTED_MODEL, MODEL, "wrong model validator")
    equal(validator.RECORD_SIZE, RECORD_BYTES, "wrong result decoder ABI")
    native = json.loads(native_path.read_text())
    equal(native["elf_sha256"], PROBE_SHA, "wrong native-tested arch-probe ELF")
    equal(native["elf_size"], PROBE_BYTES, "wrong native-tested arch-probe length")
    equal(native["probe_result"]["counter_frequency_hz"], 19200000, "native counter frequency differs")
    equal(native["probe_result"]["loop_iterations"], 65536, "native counter loop differs")
    fixture = verify_fixture(cfg, files, manifest, h)
    command = [str(qemu)]
    replacements = {"-initrd": str(files["initramfs.cpio.gz"]),
                    "-qmp": f"unix:{out / 'qmp.sock'},server=on,wait=off", "-D": str(out / "qemu.log")}
    for key, value in pairs:
        if key == "-no-shutdown":
            continue  # Require the guest's own poweroff to terminate QEMU.
        command.append(key)
        if value is not None:
            command.append(replacements.get(key, value))
    command += ["-gdb", f"unix:{out / 'gdb.sock'},server=on,wait=off"]
    proof = {
        "source_commit": cfg["source_commit"], "qemu_sha256": cfg["qemu_sha256"],
        "runner_sha256": cfg["runner_sha256"], "workflow_run": cfg["workflow_run"],
        "artifact_id": cfg["artifact_id"], "artifact_sha256": cfg["artifact_sha256"],
        "artifact_receipt_sha256": cfg["artifact_receipt_sha256"],
        "fixture_stage_receipt_sha256": cfg["fixture_stage_receipt_sha256"],
        "baseline_diagnostic_sha256": cfg["baseline_diagnostic_sha256"],
        "primary_summary_sha256": cfg["primary_summary_sha256"],
        "primary_readiness_evidence": primary_proof,
        "same_binary_primary_identity_verified": True, "accepted_suite_count": len(artifact["suites"]),
        "fixture_member_count": len(stage_receipt["files"]), "base_global_count": len(GLOBALS),
        "additional_reference_globals": cfg["additional_reference_globals"],
        "ordered_global_count": len(GLOBALS) + len(cfg["additional_reference_globals"]),
        "kernel_sha256": KERNEL_SHA, "dtb_sha256": DT_SHA, "dtb_bytes": DT_BYTES,
        "exact_dt_model": MODEL, "model_property_sha256": MODEL_SHA,
        "direct_model_identity_receipt_sha256": DT_RECEIPT_SHA, "fixture": fixture,
        "primary_diagnostic_is_not_a_userspace_success_verdict": True,
        "command_changes": ["replace initrd with the accepted actual-DT CI fixture", "replace QMP/log output paths",
                            "remove no-shutdown for actual guest poweroff", "append the GDB Unix socket"],
        "command": command, "baseline_command": original, "input_hashes": identities,
    }
    return h, validator, native, command, identities, proof


def audit_gdb_packets(transcript, checkpoint, result_address):
    """Record the bounded observation commands actually sent by the helper."""
    sent = [entry["send"] for entry in transcript if "send" in entry]
    reads = []
    fixed = {"qSupported:multiprocess+;qXfer:features:read+", "qXfer:features:read:target.xml:0,fff",
             "p20", "p21", "c", f"Z0,{checkpoint:x},4", f"z0,{checkpoint:x},4"}
    for packet in sent:
        if packet in fixed or re.fullmatch(r"Hg(?:p[0-9a-f]+\.)?[0-9a-f]+", packet):
            continue
        memory = re.fullmatch(r"m([0-9a-f]+),([0-9a-f]+)", packet)
        require(memory is not None, "unexpected GDB command; no guest memory/register/PC write is permitted")
        address, length = (int(value, 16) for value in memory.groups())
        require(result_address <= address and 0 < length <= RECORD_BYTES and
                address + length <= result_address + RECORD_BYTES, "GDB memory read outside the result ABI")
        reads.append({"address": address, "bytes": length})
    return {"sent_packets": sent, "bounded_result_reads": reads,
            "memory_register_or_pc_write_packets": 0,
            "continue_packets_contain_no_address": True,
            "tcg_debug_breakpoints_and_stop_resume": True}


def run(cfg, config_path):
    out = Path(cfg["out"]).absolute()
    require(not out.exists(), "use a fresh output directory")
    require(len(str(out / "qmp.sock").encode()) < 104 and len(str(out / "gdb.sock").encode()) < 104,
            "Unix socket path too long")
    timeout = cfg.get("timeout_seconds", 120)
    require(type(timeout) in (int, float) and 0 < timeout <= 600, "timeout range")
    out.mkdir(parents=True)
    report = {
        "schema": "tb371fc-native-actual-dt-userspace8-draft-v1",
        "scope": "CI-built validation PID1 under the production Image/full DT; not Android init",
        "source_commit": cfg.get("source_commit"), "qemu_sha256": cfg.get("qemu_sha256"),
        "runner_sha256": cfg.get("runner_sha256"), "manifest_sha256": cfg.get("manifest_sha256"),
        "checkpoint_observed": False, "userspace_record_passed": False,
        "guest_shutdown_observed": False, "lifecycle_passed": False, "passed": False,
        "guest_ram_written_by_collector": False, "guest_registers_or_pc_written_by_collector": False,
        "debug_stop_resume_and_tcg_breakpoints": True, "compiler_invoked": False,
        "fixture_modified": False, "synthetic_disk_created": False, "ufs_lu_attached": False,
        "android_boot_passed": None, "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    process = qmp = gdb = serial = h = None
    initial_hashes, commands = {}, []
    checkpoint, result_address = None, None
    checkpoint_saved = False
    started = time.monotonic()

    def monitor(name):
        require(name in ("qmp_capabilities", "query-cpus-fast", "query-status"), "unapproved QMP command")
        row = {"execute": name}
        commands.append(row)
        answer = qmp.command(name)
        row["return"] = answer
        return answer

    try:
        h, validator, native, command, initial_hashes, proof = prepare(cfg, out)
        initial_hashes[str(regular(config_path))] = sha(config_path)
        initial_hashes[str(regular(__file__))] = sha(__file__)
        report.update(prepared_inputs=proof, command=command, input_hashes=initial_hashes,
                      initramfs_sha256=proof["fixture"]["initrd_sha256"],
                      init_elf_sha256=proof["fixture"]["init_sha256"], kernel_sha256=KERNEL_SHA, dtb_sha256=DT_SHA)
        checkpoint = proof["fixture"]["elf"]["checkpoint_address"]
        result_address = proof["fixture"]["elf"]["result_address"]
        report.update(result_address=result_address, result_bytes=RECORD_BYTES,
                      checkpoint_address=checkpoint,
                      checkpoint_file_offset=proof["fixture"]["elf"]["checkpoint_file_offset"])
        write_json(out / "prepared-inputs.json", report)
        serial = (out / "serial.log").open("xb")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=serial, stderr=subprocess.STDOUT)
        report["qemu_started"] = True
        deadline = time.monotonic() + timeout
        qmp = h.QMP(h.connect(out / "qmp.sock", process, min(deadline, time.monotonic() + 5)))
        monitor("qmp_capabilities")
        cpus = monitor("query-cpus-fast")
        require(type(cpus) is list and len(cpus) == 8 and
                sorted(cpu["cpu-index"] for cpu in cpus) == list(range(8)), "not the eight expected QEMU CPUs")
        report["qemu_cpus"] = cpus
        gdb = h.GDB(h.connect(out / "gdb.sock", process, min(deadline, time.monotonic() + 5)))
        report["gdb_capabilities"] = gdb.request("qSupported:multiprocess+;qXfer:features:read+")
        report["gdb_target_xml"] = gdb.request("qXfer:features:read:target.xml:0,fff")
        require(gdb.request(f"Z0,{checkpoint:x},4") == "OK", "TCG breakpoint rejected")
        report["breakpoint_inserted"] = True
        gdb.send("c")  # Never pass a PC/address to continue.
        stopped = gdb.receive(deadline)
        report["gdb_stop_reply"] = stopped
        require(stopped.startswith(("T05", "S05")), "not a breakpoint stop")
        thread = re.search(r"thread:([^;]+)", stopped)
        if thread:
            require(re.fullmatch(r"(?:p[0-9a-f]+\.)?[0-9a-f]+", thread.group(1)), "unexpected stopped CPU selector")
            require(gdb.request("Hg" + thread.group(1)) == "OK", "cannot select stopped CPU")
        raw_pc, raw_pstate = gdb.request("p20"), gdb.request("p21")
        require(re.fullmatch(r"[0-9a-fA-F]{16}", raw_pc) and
                re.fullmatch(r"[0-9a-fA-F]{8}", raw_pstate), "wrong AArch64 PC/PSTATE register widths")
        pc = int.from_bytes(bytes.fromhex(raw_pc), "little")
        pstate = int.from_bytes(bytes.fromhex(raw_pstate), "little")
        report.update(checkpoint_pc=pc, checkpoint_pstate=pstate)
        require(pc == checkpoint and (pstate & 15) == 0, "not this ELF's EL0 checkpoint")
        report["checkpoint_observed"] = True
        before = monitor("query-status")
        require(type(before) is dict and before.get("running") is False, "guest not stopped before RAM observation")
        report["status_at_checkpoint"] = before
        try:
            raw = gdb.memory(result_address, RECORD_BYTES)
            equal(len(raw), RECORD_BYTES, "incomplete guest RAM result")
            (out / "guest-result.bin").write_bytes(raw)
            report["guest_result_sha256"] = digest(raw)
            # This is the only result decoder/verdict used. It does not filter
            # errors from the old name check or change any guest-returned byte.
            record = validator.decode_record(raw)
            errors, probe = validator.validate_record(record, native)
            (out / "arch-probe.stdout").write_bytes(record["probe_stdout"].encode())
            report.update(guest_result=record, arch_counter_probe=probe, guest_validation_errors=errors,
                          userspace_record_passed=not errors,
                          guest_checks=[{"name": name, "passed": bool(record["pass_mask"] & (1 << i)),
                                         "failed": bool(record["fail_mask"] & (1 << i))}
                                        for i, name in enumerate(validator.CHECKS)])
        except Exception as error:
            report["guest_validation_errors"] = [type(error).__name__ + ": " + str(error)]
        after = monitor("query-status")
        report["status_after_record"] = after
        require(type(after) is dict and after.get("running") is False and after.get("status") == before.get("status"),
                "guest state changed during read-only record capture")
        report["gdb_packet_audit_at_checkpoint"] = audit_gdb_packets(gdb.transcript, checkpoint, result_address)
        # Persist the EL0/result evidence before resuming. A subsequent shutdown
        # failure must not erase a valid eight-check observation.
        write_json(out / "userspace-checkpoint.json", report)
        checkpoint_saved = True
        require(gdb.request(f"z0,{checkpoint:x},4") == "OK", "breakpoint removal failed")
        report["breakpoint_removed"] = True
        report["poweroff_event_start_index"] = len(qmp.messages)
        gdb.send("c")
        report["resumed_for_guest_poweroff"] = True
        while time.monotonic() < deadline and process.poll() is None:
            # [] is timeout or a partial JSON frame; only None means EOF.
            if qmp.receive(.05) is None:
                break
        if process.poll() is None:
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        qmp.drain()
    except Exception as error:
        report["collector_error"] = type(error).__name__ + ": " + str(error)
    finally:
        if qmp:
            try:
                qmp.drain()
            except (OSError, ValueError) as error:
                report["qmp_drain_error"] = type(error).__name__ + ": " + str(error)
            report["qmp_messages"] = qmp.messages
            report["qmp_trailing_unparsed_bytes"] = len(qmp.buffer)
            start = report.get("poweroff_event_start_index", len(qmp.messages))
            report["guest_shutdown_observed"] = h.has_guest_shutdown(qmp.messages[start:])
            write_json(out / "qmp-messages.json", qmp.messages)
            qmp.sock.close()
        write_json(out / "qmp-commands.json", commands)
        if gdb:
            write_json(out / "gdb-transcript.json", gdb.transcript)
            try:
                report["gdb_packet_audit"] = audit_gdb_packets(gdb.transcript, checkpoint, result_address)
            except Exception as error:
                report["collector_error"] = type(error).__name__ + ": " + str(error)
            gdb.sock.close()
        if process and process.poll() is None:
            report["observer_terminated_qemu"] = True
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if process:
            report["exit_code"] = process.returncode
        if serial:
            serial.close()
            report["serial_sha256"] = sha(out / "serial.log")
        changed = []
        for path, expected in initial_hashes.items():
            try:
                if sha(path) != expected:
                    changed.append({"path": path, "reason": "digest changed"})
            except OSError as error:
                changed.append({"path": path, "reason": type(error).__name__ + ": " + str(error)})
        report["inputs_unchanged"] = bool(initial_hashes) and not changed
        report["changed_inputs"] = changed
        report["lifecycle_passed"] = bool(
            report["guest_shutdown_observed"] and report.get("exit_code") == 0 and
            not report.get("observer_terminated_qemu", False) and
            report.get("breakpoint_removed") is True and report.get("resumed_for_guest_poweroff") is True)
        report["passed"] = bool(report["userspace_record_passed"] and report["checkpoint_observed"] and
                                report["lifecycle_passed"] and report["inputs_unchanged"] and
                                not report.get("collector_error"))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        if not checkpoint_saved:
            write_json(out / "userspace-checkpoint.json", {
                "schema": "tb371fc-actual-dt-userspace8-checkpoint-not-completed-v1",
                "source_commit": cfg.get("source_commit"), "qemu_sha256": cfg.get("qemu_sha256"),
                "checkpoint_observed": report["checkpoint_observed"],
                "userspace_record_passed": report["userspace_record_passed"],
                "collector_error": report.get("collector_error"), "full_context": "result.json"})
        write_json(out / "lifecycle.json", {
            "schema": "tb371fc-actual-dt-userspace8-lifecycle-v1", "source_commit": cfg.get("source_commit"),
            "qemu_sha256": cfg.get("qemu_sha256"), "manifest_sha256": cfg.get("manifest_sha256"),
            "checkpoint_evidence": "userspace-checkpoint.json",
            "checkpoint_observed": report["checkpoint_observed"],
            "userspace_record_passed": report["userspace_record_passed"],
            "guest_shutdown_observed": report["guest_shutdown_observed"],
            "breakpoint_removed": report.get("breakpoint_removed", False),
            "resumed_for_guest_poweroff": report.get("resumed_for_guest_poweroff", False),
            "qemu_exit_code": report.get("exit_code"),
            "observer_terminated_qemu": report.get("observer_terminated_qemu", False),
            "lifecycle_passed": report["lifecycle_passed"], "inputs_unchanged": report["inputs_unchanged"],
            "qmp_messages": report.get("qmp_messages", []), "collector_error": report.get("collector_error"),
            "poweroff_event_start_index": report.get("poweroff_event_start_index"),
            "android_boot_passed": None})
        write_json(out / "result.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: native-userspace8-actual-dt-draft.py CONFIG.json")
    config_path = regular(sys.argv[1])
    raise SystemExit(run(json.loads(config_path.read_text()), config_path))
