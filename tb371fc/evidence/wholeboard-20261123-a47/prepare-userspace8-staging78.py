#!/usr/bin/env python3
"""Draft host-only preparation; never copy remotely, build, or execute a guest.

Read already accepted artifact/stage receipts plus a local copy of the exact
primary diagnostic and its COMPLETE kernel log. Write only a candidate consumer
configuration and a copy/verify inventory in a fresh output directory. Root must
review these and decide whether to invoke the separately reviewed consumer.
"""

import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat


CONSUMER_SHA = "80961a7cf28321f662cbf90af048a09a5a587ce0ad2624c1f909542f678b1b69"
CONSUMER_CONSTANTS = {
    "GLOBALS", "HELPERS", "STAGED_NAMES", "PREFIX", "KNOWN_RPMH_REFERENCE_GLOBAL",
    "KNOWN_CAMERA_REFERENCE_GLOBAL",
    "KERNEL_SHA", "DT_SHA", "NATIVE_SHA", "DT_RECEIPT_SHA",
}
LINUX_GATES = ["linux-console-userspace", "linux-console-empty-ufs",
               "linux-ufs-smmu", "linux-ufs-rpmh"]


def require(value, message):
    if not value:
        raise ValueError(message)


def equal(actual, expected, message):
    require(type(actual) is type(expected) and actual == expected, message)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_file(path, expected=None, size=None):
    path = Path(path).absolute()
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode) and not path.is_symlink(),
            "regular non-symlink input required: " + str(path))
    require(0 < before.st_size <= 64 * 1024 * 1024, "input size outside preparation bound")
    raw = path.read_bytes()
    after = path.lstat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "input changed while reading")
    if expected is not None:
        equal(digest(raw), expected, "input SHA mismatch: " + str(path))
    if size is not None:
        equal(len(raw), size, "input byte count mismatch: " + str(path))
    return raw, {"host_path": str(path), "bytes": len(raw), "sha256": digest(raw)}


def read_json(path, expected=None):
    raw, identity = read_file(path, expected)
    value = json.loads(raw)
    require(type(value) is dict, "JSON object required: " + str(path))
    return value, identity


def absolute_ct_path(value):
    require(type(value) is str and "\\" not in value and "\0" not in value,
            "invalid CT path")
    path = PurePosixPath(value)
    require(path.is_absolute() and ".." not in path.parts and str(path) == value,
            "canonical absolute CT path required")
    return path


def primary_options(command):
    require(type(command) is list and command and all(type(x) is str for x in command),
            "primary command must be the actual argument vector")
    keys = {"-M", "-accel", "-m", "-smp", "-display", "-monitor", "-serial", "-qmp",
            "-kernel", "-initrd", "-append", "-d", "-D", "-dtb", "-global"}
    permitted_flags = {"-S", "-no-reboot", "-no-shutdown"}
    values, flags, globals_ = {}, set(), []
    i = 1
    while i < len(command):
        key = command[i]
        if key in permitted_flags:
            require(key not in flags, "duplicate primary flag")
            flags.add(key)
            i += 1
            continue
        require(key in keys and i + 1 < len(command), "unknown primary option or added device")
        if key == "-global":
            globals_.append(command[i + 1])
        else:
            require(key not in values, "duplicate primary value option")
            values[key] = command[i + 1]
        i += 2
    equal(set(values), keys - {"-global"}, "primary option set differs")
    equal(flags, permitted_flags, "primary flag set differs")
    for key, expected in {"-accel": "tcg,thread=multi", "-m": "8G", "-smp": "8",
                          "-display": "none", "-monitor": "none", "-serial": "stdio",
                          "-append": "rdinit=/init nokaslr panic=0",
                          "-d": "unimp,guest_errors,invalid_mem,int,cpu_reset"}.items():
        equal(values[key], expected, "primary fixed option differs: " + key)
    return values, globals_


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


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--hostroot", type=Path, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--expected", type=Path, required=True,
                   help="Root-supplied exact source/run/runner/binary/artifact/machine expectations")
    p.add_argument("--plan", type=Path, required=True, help="Independent source78 plan78-preflight.json")
    p.add_argument("--primary-dir", type=Path, required=True,
                   help="Local copies of the original CT diagnostic.json and COMPLETE kernel-printk.log")
    p.add_argument("--ct-primary-dir", required=True, help="Unchanged original primary directory on CT")
    p.add_argument("--ctroot", required=True, help="CT parent for NEW copied support and fixture directories")
    p.add_argument("--support", type=Path, required=True,
                   help="Local helpers/ plus android-arch-probe.json and actual-dt-model-3b31e8.json")
    p.add_argument("--consumer", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True, help="Fresh host directory for preparation outputs only")
    args = p.parse_args()
    require(re.fullmatch(r"[A-Za-z0-9_-]{1,48}", args.label) is not None, "invalid label")
    expected, expected_id = read_json(args.expected)
    plan, plan_id = read_json(args.plan)
    for key, width in {"source_commit": 40, "runner_sha256": 64, "qemu_sha256": 64,
                       "artifact_sha256": 64}.items():
        require(type(expected.get(key)) is str and re.fullmatch("[0-9a-f]{%d}" % width, expected[key]),
                "exact independently supplied expectation required: " + key)
    for key in ("workflow_run", "artifact_id"):
        require(type(expected.get(key)) is int and expected[key] > 0, "exact integer required: " + key)
    require(type(expected.get("expected_primary_machine")) is str and expected["expected_primary_machine"],
            "explicit reviewed primary machine required")
    require(type(expected.get("timeout_seconds", 120)) is int and
            0 < expected.get("timeout_seconds", 120) <= 600, "invalid timeout")
    ctroot = absolute_ct_path(args.ctroot)
    primary_ct = absolute_ct_path(args.ct_primary_dir)
    ct_qemu = str(absolute_ct_path(expected["ct_qemu"]))
    consumer_raw, consumer_id = read_file(args.consumer, CONSUMER_SHA)
    constants = {}
    for node in ast.parse(consumer_raw).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CONSUMER_CONSTANTS:
                    require(target.id not in constants, "duplicate consumer constant")
                    constants[target.id] = ast.literal_eval(node.value)
    equal(set(constants), CONSUMER_CONSTANTS, "pinned consumer contract constants missing")
    addition = expected["additional_reference_globals"]
    require(type(addition) is list and addition in ([], [constants["KNOWN_RPMH_REFERENCE_GLOBAL"]],
            [constants["KNOWN_RPMH_REFERENCE_GLOBAL"], constants["KNOWN_CAMERA_REFERENCE_GLOBAL"]]),
            "only the reviewed ordered RPMh eighteenth and camera nineteenth globals are permitted")
    equal(plan["runner_sha256"], expected["runner_sha256"], "independent plan runner differs")
    order = {name: plan["suites"][name]["names"] for name in ("components", "linux")}
    equal(order["linux"], LINUX_GATES, "the four Linux gates differ")
    for name, count in (("components", 74), ("linux", 4)):
        require(type(order[name]) is list and len(order[name]) == count and
                all(type(x) is str and x for x in order[name]) and len(set(order[name])) == count,
                "source78 plan cardinality/uniqueness differs")
    host = args.hostroot.absolute()
    artifact, artifact_id = read_json(host / (args.label + "-artifact.json"))
    summary, summary_id = read_json(host / (args.label + "-summary.json"))
    extra, extra_id = read_json(host / (args.label + "-extra.json"))
    stage_receipt, stage_id = read_json(host / (args.label + "-full-dtb-fixture-stage.json"))
    stage_dir = host / (args.label + "-full-dtb-fixture-stage")
    for artifact_key, expected_key in (("source", "source_commit"), ("workflow_run", "workflow_run"),
                                      ("artifact_id", "artifact_id"), ("artifact_sha256", "artifact_sha256"),
                                      ("qemu_sha256", "qemu_sha256")):
        equal(artifact[artifact_key], expected[expected_key], "accepted artifact identity mismatch")
    suites = artifact["suites"]
    require(type(suites) is list and len(suites) == 2 and {x["suite"] for x in suites} == set(order),
            "exact two accepted suites required")
    for suite in suites:
        for key, value in {"passed": True, "qemu_unchanged": True, "source_dirty": False,
                           "source_commit": expected["source_commit"], "runner_sha256": expected["runner_sha256"],
                           "qemu_sha256": expected["qemu_sha256"], "gate_order": order[suite["suite"]],
                           "expected_gates": len(order[suite["suite"]]),
                           "passed_gates": len(order[suite["suite"]])}.items():
            equal(suite[key], value, "accepted suite mismatch: " + key)
    for key in ("source_commit", "workflow_run", "artifact_id", "artifact_sha256", "qemu_sha256", "runner_sha256"):
        equal(stage_receipt[key], expected[key], "stage/expected identity mismatch: " + key)
    for key, value in {"schema": "tb371fc-accepted-full-dtb-fixture-stage-v1",
                       "staged_from_verified_zip": True, "binary_source_audit_passed": True,
                       "manifest_member": constants["PREFIX"] + "manifest.json"}.items():
        equal(stage_receipt[key], value, "stage contract mismatch: " + key)
    diagnostic, diagnostic_id = read_json(args.primary_dir / "diagnostic.json", summary["diagnostic_sha256"])
    for key, value in {"source_commit": expected["source_commit"], "qemu_sha256": expected["qemu_sha256"],
                       "kernel_sha256": constants["KERNEL_SHA"], "dtb_sha256": constants["DT_SHA"],
                       "qemu_unchanged": True, "observer_error": None, "legacy_printk_error": None}.items():
        equal(summary[key], value, "primary summary mismatch: " + key)
    for key in ("qemu_sha256", "kernel_sha256", "dtb_sha256"):
        equal(diagnostic[key], summary[key], "primary diagnostic identity differs")
    equal(extra["schema"], "tb371fc-native-diagnostic-extra-v1", "wrong extra schema")
    for key in ("source_commit", "qemu_sha256"):
        equal(extra[key], expected[key], "primary extra identity differs")
    evidence = extra["raw_evidence"]
    require(type(evidence) is list and all(type(x) is dict and type(x.get("name")) is str for x in evidence),
            "invalid primary raw evidence inventory")
    by_name = {x["name"]: x for x in evidence}
    equal(len(by_name), len(evidence), "duplicate primary raw evidence name")
    for key in ("sha256", "bytes"):
        equal(by_name["diagnostic.json"][key], diagnostic_id[key], "raw diagnostic identity differs")
    kernel_raw, kernel_id = read_file(args.primary_dir / "kernel-printk.log",
                                    by_name["kernel-printk.log"]["sha256"],
                                    by_name["kernel-printk.log"]["bytes"])
    require(len(kernel_raw) <= 4 * 1024 * 1024, "primary full kernel log exceeds accepted bound")
    kernel = kernel_raw.decode("utf-8", errors="replace")
    milestones = extra["kernel_milestones"]
    require(type(milestones) is list and milestones and all(type(x) is str for x in milestones),
            "full primary milestone list required")
    lines = set(kernel.splitlines())
    require(all(x in lines for x in milestones), "milestone absent from complete kernel log")
    fatal = r"Kernel panic|Internal error|Unable to handle"
    require(re.search(fatal, kernel, re.I) is None and
            all(re.search(fatal, x, re.I) is None for x in milestones),
            "primary has a fatal error: do not prepare this fixture for execution")
    for container in (diagnostic, summary, extra, diagnostic.get("aop_peer") or {}):
        require(type(container) is dict, "invalid primary observer container")
        for key, value in container.items():
            if key.endswith("observer_error") or key == "legacy_printk_error":
                equal(value, None, "primary observer failed: " + key)
    verify_camera_primary(diagnostic, extra, addition, constants["KNOWN_CAMERA_REFERENCE_GLOBAL"])
    values, globals_ = primary_options(diagnostic["command"])
    equal(diagnostic["command"][0], ct_qemu, "primary did not use explicitly expected CT binary path")
    equal(values["-M"], expected["expected_primary_machine"], "reviewed primary machine differs")
    equal(globals_, list(constants["GLOBALS"]) + addition, "ordered primary globals differ; do not auto-enable")

    stem = args.label + "-u8"
    fixture_ct = ctroot / (stem + "-stage")
    support_ct = ctroot / (stem + "-support")
    cfg = {key: expected[key] for key in ("source_commit", "workflow_run", "artifact_id", "artifact_sha256",
                                         "qemu_sha256", "runner_sha256", "expected_primary_machine",
                                         "additional_reference_globals")}
    cfg.update({"qemu": ct_qemu, "expected_gate_order": order,
                "artifact_receipt": str(ctroot / (stem + "-artifact.json")),
                "artifact_receipt_sha256": artifact_id["sha256"],
                "fixture_stage": str(fixture_ct),
                "fixture_stage_receipt": str(ctroot / (stem + "-stage.json")),
                "fixture_stage_receipt_sha256": stage_id["sha256"],
                "manifest_sha256": stage_receipt["manifest_sha256"],
                "baseline_diagnostic": str(primary_ct / "diagnostic.json"),
                "baseline_diagnostic_sha256": diagnostic_id["sha256"],
                "primary_summary": str(ctroot / (stem + "-summary.json")),
                "primary_summary_sha256": summary_id["sha256"],
                "primary_extra": str(ctroot / (stem + "-extra.json")),
                "primary_extra_sha256": extra_id["sha256"],
                "helpers": str(support_ct / "helpers"),
                "native_probe_reference": str(support_ct / "android-arch-probe.json"),
                "actual_dt_model_receipt": str(support_ct / "actual-dt-model-3b31e8.json"),
                "out": str(ctroot / (stem + "-out")),
                "timeout_seconds": expected.get("timeout_seconds", 120)})
    require(len((cfg["out"] + "/gdb.sock").encode()) < 100, "choose a shorter CT output parent/label")
    copies = []

    def add(identity, destination, mode="0444"):
        copies.append({**identity, "ct_path": str(destination), "mode_after_copy": mode,
                       "require_destination_absent": True})

    for identity, destination in ((artifact_id, cfg["artifact_receipt"]), (stage_id, cfg["fixture_stage_receipt"]),
                                  (summary_id, cfg["primary_summary"]), (extra_id, cfg["primary_extra"])):
        add(identity, destination)
    items = stage_receipt["files"]
    require(type(items) is list and len(items) == len(constants["STAGED_NAMES"]), "wrong staged member count")
    found = {}
    for item in items:
        member = item["member"]
        require(type(member) is str and member.startswith(constants["PREFIX"]), "unexpected staged member")
        relative = PurePosixPath(member)
        require(not relative.is_absolute() and ".." not in relative.parts and "\\" not in member and
                "\0" not in member and str(relative) == member, "unsafe staged member path")
        equal(item["relative_path"], member, "stage layout does not preserve ZIP member")
        name = member[len(constants["PREFIX"]):]
        require(name in constants["STAGED_NAMES"] and name not in found, "unexpected/duplicate staged member")
        raw, identity = read_file(stage_dir.joinpath(*relative.parts), item["sha256"], item["bytes"])
        found[name] = raw
        add(identity, fixture_ct / relative)
    equal(set(found), set(constants["STAGED_NAMES"]), "incomplete fixed stage")
    equal(digest(found["manifest.json"]), cfg["manifest_sha256"], "actual staged manifest differs")
    equal(found["source-commit.txt"].decode().strip(), cfg["source_commit"], "fixture source differs")
    manifest = json.loads(found["manifest.json"])
    equal(manifest["schema"], "tb371fc-full-dtb-user-fixture-v1", "wrong fixture manifest")
    equal(manifest["source_commit"], cfg["source_commit"], "manifest source differs")
    equal(manifest["build_completed"], True, "fixture build not completed")
    equal(manifest["execution"], {"performed": False, "userspace_passed": None,
                                  "guest_shutdown_observed": None, "android_boot_passed": None},
          "build manifest must not claim execution")
    helper_root = args.support / "helpers"
    require(not (helper_root / "__pycache__").exists() and not list(helper_root.glob("*.pyc")),
            "fresh source-only helper support required")
    for name, sha256 in constants["HELPERS"].items():
        _, identity = read_file(helper_root / name, sha256)
        add(identity, support_ct / "helpers" / name)
    for name, key, size in (("android-arch-probe.json", "NATIVE_SHA", 2391),
                            ("actual-dt-model-3b31e8.json", "DT_RECEIPT_SHA", 3064)):
        _, identity = read_file(args.support / name, constants[key], size)
        add(identity, support_ct / name)
    consumer_ct = ctroot / (stem + "-consumer.py")
    add(consumer_id, consumer_ct)
    require(len({x["ct_path"] for x in copies}) == len(copies), "duplicate copy destination")
    verified_existing = [{**diagnostic_id, "ct_path": cfg["baseline_diagnostic"], "action": "verify_only_do_not_overwrite"},
                         {**kernel_id, "ct_path": str(primary_ct / "kernel-printk.log"), "action": "verify_only_do_not_overwrite"}]
    result = {"schema": "tb371fc-userspace8-staging-preparation-v1", "status": "candidate_config_only",
              "source_commit": cfg["source_commit"], "workflow_run": cfg["workflow_run"],
              "root_review_and_execution_decision_still_required": True,
              "consumer_revalidates_inputs_before_launch": True,
              "primary_fatal_gate_checked_from_full_log": True,
              "device_error_count_is_not_a_fatal_gate": True,
              "consumer_imported_or_called": False, "remote_operation_performed": False,
              "guest_executed": False, "userspace_passed": None, "guest_shutdown_observed": None,
              "expected_input": expected_id, "independent_plan": plan_id,
              "copy_files_to_fresh_destinations": copies,
              "existing_primary_files_to_verify_on_ct": verified_existing,
              "existing_ct_inputs_consumer_will_hash_again": {
                  "qemu": ct_qemu, "kernel": values["-kernel"], "dtb": values["-dtb"],
                  "original_primary_initrd": values["-initrd"], "machine": values["-M"]},
              "fresh_ct_directories_required": [str(fixture_ct), str(support_ct), cfg["out"]],
              "consumer_invocation_argv_for_root_review_only":
                  ["python3", str(consumer_ct), str(ctroot / (stem + "-config.json"))]}
    out = args.out.absolute()
    require(not out.exists(), "preparation output directory must be fresh")
    out.mkdir(parents=True, exist_ok=False)
    config_path = out / "consumer-config.json"
    raw_cfg = (json.dumps(cfg, indent=2) + "\n").encode()
    config_path.write_bytes(raw_cfg)
    add({"host_path": str(config_path), "bytes": len(raw_cfg), "sha256": digest(raw_cfg)},
        ctroot / (stem + "-config.json"))
    (out / "copy-inventory.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "candidate_config_only", "config": str(config_path),
                      "copy_inventory": str(out / "copy-inventory.json"), "guest_executed": False}))


if __name__ == "__main__":
    main()
