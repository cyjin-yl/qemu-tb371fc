#!/usr/bin/env python3
"""Private one-shot 6a5 LLCC coordination; initially unbound; host --host is explicit.

No physical-device access. An explicitly hashed binding must name the new CI;
there is no previous-run fallback. Input selection is frozen once under the
native lock: verified generation2 six-LUN ready, otherwise the old no-LUN
diagnostic. An existing invalid ready receipt fails. Every output is exclusive;
inspect native-attempt/result receipts before any subsequent run. Owner inputs
and checkpoint remain read-only; only the existing shared native lock is held.
"""
import argparse
import base64
import copy
from contextlib import contextmanager
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile

REPO = "cyjin-yl/qemu-tb371fc"
CONTROL = Path("/tank/shared/projects/qemu-tb371fc/.git/tb371fc-support-6a5-control")
SHARED_CONTROL = Path("/home/ezra/qemu-stockboot-e1a9-control")
OWNER_CHECKPOINT_NAME = "checkpoint.json"
OWNER_ID = "root/e1a9"
VOLUME = "/tank/docker/doescontainer/docker/volumes/qemu-tb371fc-build/_data"
CONTAINER = "qemu-tb371fc-dev"
PCT = ["sudo", "-n", "/usr/sbin/pct"]
DOCKER = PCT + ["exec", "102", "--", "docker"]
EXEC = DOCKER + ["exec", CONTAINER]
GUEST_PARENT = Path("/work/stockboot-6a5")
CONSUMER = "boot_android_from_ready.py"
# This relative lock is resolved inside the exclusive own-lane run directory.
CONSUMER_LOCK = Path("full-lun-native-run.lock")
OLD = Path("/work/stockboot-e1a9/runner-5223")
HOST_BOOT_ID = "f093b2c8-08ac-413b-92ea-f2c1efd77c5a"
OLD_POLICY_SHA = "1fd5bd07fe55670ab0cde3fd8bafde143f8a2f667f7176dd331e2046b958b3da"
ASSEMBLY = Path("/work/stockboot-e1a9/full-original-20261124-e1a9/generation2-assembly-20261124T152912Z-d32c9387")
ASSEMBLY_SHA = "19b6f1c42ee5e7e4515a3ff399c21185b88ce28b6d06c84c9c31f32af44f37b8"
GENERATION_PLAN_SHA = "a90c1161c6cbce7d0fada8e1901354f0a58b5707e02231d206a0c1f663a1a77a"
SOURCE_BINDING_SHA = "78fa6806272f40e906dabbf9c75bb6553642c2fbe9e1deb7d0f20a784cc8b131"
LUN_BYTES = [125720068096, 8388608, 8388608, 33554432, 2147483648, 33554432]
CORE = {
    "kernel": {"size": 37445648, "sha256": "ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f"},
    "initrd": {"size": 1298323, "sha256": "563f4b334f8470155c6a451395342f7044b70e79b480a886c7641960f534f596"},
    "dtb": {"size": 973052, "sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"},
    "bootargs": {"size": 1392, "sha256": "63c5738bb0c67419dd7efa0c585156499d163479d01b421203c0b51756fc6732"},
}
AOP = {"path": "/work/device-assets/aop-command-db-analysis/aop-static-plus-clock-partial.bin",
       "sha256": "a971809c06fa1a3ff44c9d28463bc6ff53203d9375ffd0f76dc73d4c8a3ea6f9"}
SYMBOLS = {"path": "/work/kernel-symbols/production-4.19.157.kallsyms",
           "sha256": "e32ef8e07019bd35105f7e2ddd6b7f47b9c23d828575afa6858b0ade37c49ab0"}
FALSE_FLAGS = ("snapshot_consistent", "source_immutable_claim_verified", "pristine_factory_images",
               "restore_tested", "android_boot_tested", "all_backups_complete")
# This binds the committed harness and the matching subset of its exact plan.
# The separately pinned independent audit must still inspect actual fixtures,
# full wire bytes and all 93 + 5 cases; this coordinator does not replace it.
REQUIRED_COMPONENT_CONTRACTS = {'dispcc-sm8250-reference': {'check_count': 42,
                             'expected_values': {'schema': 'tb371fc-dispcc-sm8250-v1',
                                                 'expected_checks': 42,
                                                 'reference_identity': 1145656113,
                                                 'vmstate_payload_bytes': 1676,
                                                 'resource_bytes': 131072,
                                                 'expected_stream_files': 7},
                             'expected_lengths': {'migrations': 3,
                                                  'rejected_migrations': 2,
                                                  'corrupted_stream_rejections': 3,
                                                  'migration_sources': 4,
                                                  'actual_stream_files': 7}},
 'videocc-sm8250-reference': {'check_count': 41,
                              'expected_values': {'schema': 'tb371fc-videocc-sm8250-v1',
                                                  'expected_checks': 41,
                                                  'reference_identity': 1447641906,
                                                  'vmstate_payload_bytes': 474,
                                                  'expected_stream_files': 6},
                              'expected_lengths': {'migrations': 3,
                                                   'rejected_migrations': 2,
                                                   'corrupted_stream_rejections': 2,
                                                   'migration_sources': 4,
                                                   'actual_stream_files': 6}},
 'dsi-pll-7nm-reference': {'check_count': 36,
                           'expected_values': {'schema': 'tb371fc-dsi-pll-7nm-v1',
                                               'expected_checks': 36,
                                               'reference_identity': 1146308916,
                                               'vmstate_payload_bytes': 2926,
                                               'output_clock_count': 52,
                                               'mapped_bytes': 1232,
                                               'sparse_window_count': 16,
                                               'runtime_xo_change_tested': True,
                                               'first_observation_qom_get_count': 1163,
                                               'expected_stream_files': 8},
                           'expected_lengths': {'migrations': 3,
                                                'rejected_migrations': 2,
                                                'corrupted_stream_rejections': 4,
                                                'migration_sources': 4,
                                                'actual_stream_files': 8}},
 'dsi-pll-source-generation': {'check_count': 2,
                               'expected_values': {'schema': 'tb371fc-dsi-pll-generation-v1',
                                                   'source_pins_verified': True},
                               'expected_lengths': {'inputs': 13}}}
# DP policy is frozen to the published acceptance harness; exact run/artifact binding remains mandatory.
REQUIRED_COMPONENT_CONTRACTS["dp-pll-7nm-reference"] = {'check_count': 36,
 'expected_values': {'schema': 'tb371fc-dp-pll-7nm-v1',
                     'suite': 'dp-pll-7nm-reference',
                     'expected_checks': 36,
                     'expected_completed_migrations': 4,
                     'expected_rejected_migrations': 2,
                     'expected_corrupted_stream_rejections': 4,
                     'expected_unmodified_streams': 5,
                     'expected_stream_files': 9,
                     'vmstate_version': 1,
                     'vmstate_minimum_version': 1,
                     'vmstate_payload_bytes': 791,
                     'reference_identity': 1146107697,
                     'source_variant': 'qcom,mdss_dp_pll_7nm',
                     'physical_base': 143564800,
                     'region_count': 4,
                     'resource_bytes_per_region': 512,
                     'register_count': 73,
                     'sparse_window_count': 42,
                     'mapped_bytes': 292,
                     'output_clock_count': 6,
                     'input_clock_count': 1,
                     'connected_input_clock_count': 1,
                     'dispcc_carrier_connection_count': 2,
                     'timer_count': 1,
                     'ccf_rate_scale': 1000,
                     'required_programmed_words': 58,
                     'readonly_status_words': 3,
                     'pll_reference_cycles': 64,
                     'phy_reference_cycles': 64,
                     'pll_reference_ns_ceiling': 3334,
                     'phy_reference_ns_ceiling': 3334,
                     'late_phy_wait_ns': 2000000,
                     'mdss_vmstate_payload_bytes': 64,
                     'mdss_vmstate_version': 1,
                     'first_observation_qom_get_count': 304,
                     'reference_enabled_by_default': False,
                     'frequency_checks_use_exact_clock_period': True,
                     'runtime_xo_change_tested': True,
                     'stock_compiled_dp_source_identity_claimed': False,
                     'guest_cpu_executed': False,
                     'native_kernel_probe_observed': False,
                     'analog_pll_behavior_modeled': False,
                     'aux_protocol_modeled': False,
                     'link_training_modeled': False,
                     'android_boot_passed': False,
                     'binary_unchanged': True},
 'expected_lengths': {'checks': 36,
                      'migrations': 4,
                      'rejected_migrations': 2,
                      'corrupted_stream_rejections': 4,
                      'migration_sources': 5,
                      'actual_stream_files': 9}}
REQUIRED_COMPONENT_CONTRACTS["dp-pll-source-generation"] = {'check_count': 2,
 'expected_values': {'schema': 'tb371fc-dp-pll-generation-v1', 'source_pins_verified': True},
 'expected_lengths': {'inputs': 17}}
# LLCC policy binds the current local harness candidate. Its final published
# bytes must be rechecked before binding an exact run and independently audited
# artifact. This file has no commit/run/artifact/qualification binding.
REQUIRED_COMPONENT_CONTRACTS["llcc-sm8250-reference"] = {'check_count': 32,
 'expected_values': {'schema': 'tb371fc-llcc-sm8250-v1',
                     'suite': 'llcc-sm8250-reference',
                     'expected_checks': 32,
                     'expected_completed_migrations': 4,
                     'expected_rejected_migrations': 2,
                     'expected_corrupted_stream_rejections': 4,
                     'expected_unmodified_streams': 5,
                     'expected_stream_files': 9,
                     'vmstate_version': 1,
                     'vmstate_minimum_version': 1,
                     'vmstate_payload_bytes': 783,
                     'reference_identity': 1280066354,
                     'physical_base': 153092096,
                     'broadcast_base': 157286400,
                     'region_count': 2,
                     'banks_resource_bytes': 1900544,
                     'broadcast_resource_bytes': 327680,
                     'bank_count': 4,
                     'bank_stride': 524288,
                     'bank_resource_bytes': 327680,
                     'slice_count': 16,
                     'register_count': 71,
                     'mapped_word_count': 349,
                     'sparse_window_count': 139,
                     'mapped_bytes': 1396,
                     'timer_count': 1,
                     'input_clock_count': 0,
                     'output_clock_count': 0,
                     'provider_count': 0,
                     'transition_delay_ns': 1000,
                     'first_observation_qom_get_count': 313,
                     'reference_enabled_by_default': False,
                     'bank_geometry_is_reference': True,
                     'physical_bank_count_measured': False,
                     'hardware_version_known': False,
                     'memory_controller_count_known': False,
                     'cache_data_modeled': False,
                     'cache_performance_modeled': False,
                     'ecc_error_injection_modeled': False,
                     'runtime_provider_revocation_tested': False,
                     'guest_cpu_executed': False,
                     'native_kernel_probe_observed': False,
                     'android_boot_passed': False,
                     'binary_unchanged': True},
 'expected_lengths': {'checks': 32,
                      'migrations': 4,
                      'rejected_migrations': 2,
                      'corrupted_stream_rejections': 4,
                      'migration_sources': 5,
                      'actual_stream_files': 9}}
REQUIRED_COMPONENT_CONTRACTS["llcc-source-generation"] = {'check_count': 2,
 'expected_values': {'schema': 'tb371fc-llcc-generation-v1', 'source_pins_verified': True},
 'expected_lengths': {'inputs': 8}}
BINDING_TEMPLATE = {
    "commit": None,  # Await the separately reviewed LLCC publication.
    "run_id": None,  # Await that exact publication's actual CI run.
    "reviewed_artifact": None,  # Explicit pins from that publication's independent audit.
    "expected_components": 93,
    "expected_linux": 5,
    "video_property": "qcom-video-gdsc.enabled-reference=on",
    "camcc_property": "qcom-sm8250-camcc.enabled-reference=on",
    "dispcc_property": "qcom-sm8250-dispcc.enabled-reference=on",
    "videocc_property": "qcom-sm8250-videocc.enabled-reference=on",
    "dsi_pll_property": "qcom-sm8250-dsi-pll.enabled-reference=on",
    "dp_pll_property": "qcom-sm8250-dp-pll.enabled-reference=on",
    "llcc_property": "qcom-sm8250-llcc.enabled-reference=on",
    "ci_harness_sha256": "ad6661e68f31f72c71b6ac1a3224f0b656c3d679fdb6d04de389400c361a9330",
    "required_component_contracts": copy.deepcopy(REQUIRED_COMPONENT_CONTRACTS),
    "runner_sha256": {
        "boot_android_from_ready.py": "b2ef676a99c7064b9d31035d1b3e1e471d613500cba6373a0992b4c4f4e4154d",
        "boot_android.py": "ebba3b55ae4f931e5768717d635647dd855ab56a1efb19350acbaf1a4498b3eb",
        "android_boot_inputs.py": "08affe8ef5c40c7350be9e4c0f0482c4c5e594a3dcff7e3a88cf7887ea1eb5d8",
        "boot_qmp.py": "b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c",
        "legacy_printk.py": "6b7d18188ef8987c32135fef179f271dcf7284766a233626d09269696cada27d",
    },
}
BINDING = copy.deepcopy(BINDING_TEMPLATE)
ADDED_GLOBALS = ["qcom-mdss-gdsc.enabled-reference=on",
                 "qcom-npu-gdsc.enabled-reference=on", BINDING_TEMPLATE["video_property"],
                 BINDING_TEMPLATE["camcc_property"], BINDING_TEMPLATE["dispcc_property"],
                 BINDING_TEMPLATE["videocc_property"], BINDING_TEMPLATE["dsi_pll_property"],
                 BINDING_TEMPLATE["dp_pll_property"], BINDING_TEMPLATE["llcc_property"]]
BINARY = "qemu-build/qemu-system-aarch64"
MEMBERS = {BINARY: 512 * 1024**2,
           "tb371fc-results/components/result.json": 8 * 1024**2,
           "tb371fc-results/components/plan.json": 4 * 1024**2,
           "tb371fc-results/linux/result.json": 8 * 1024**2,
           "tb371fc-results/source-commit.txt": 4096,
           "tb371fc-results/qemu.sha256": 4096}

# The container creates these files as uid/gid 0, matching the already verified
# CT102 host mapping 100000:100000. No host-side volume writes or chown are used.
SETUP_CODE = """import json,os,pathlib,stat,sys
assert os.geteuid()==0 and os.getegid()==0
old=pathlib.Path('/work/stockboot-e1a9/runner-5223')
assert old.is_dir() and old.stat().st_uid==0 and old.stat().st_gid==0
parent=pathlib.Path('/work/stockboot-6a5')
assert parent.parent.resolve(strict=True)==parent.parent
parent.mkdir(mode=0o700,exist_ok=True)
s=parent.lstat()
assert stat.S_ISDIR(s.st_mode) and parent.resolve(strict=True)==parent
assert s.st_uid==0 and s.st_gid==0 and not s.st_mode & 0o022
p=pathlib.Path(sys.argv[1]); assert p.parent==parent and p.name not in ('.','..')
p.mkdir(mode=0o700,exist_ok=False)
(p/'runner').mkdir(mode=0o700,exist_ok=False)
print(json.dumps({'uid':os.geteuid(),'gid':os.getegid(),'directory':str(p)}))
"""
UPLOAD_CODE = """import hashlib,json,os,pathlib,sys
p=pathlib.Path(sys.argv[1]); size=int(sys.argv[2]); expected=sys.argv[3]
h=hashlib.sha256(); count=0
with p.open('xb') as f:
 os.fchmod(f.fileno(),0o600)
 while count<size:
  data=sys.stdin.buffer.read(min(1024*1024,size-count))
  if not data: raise ValueError('truncated upload')
  f.write(data); h.update(data); count+=len(data)
 if sys.stdin.buffer.read(1): raise ValueError('oversized upload')
 f.flush(); os.fsync(f.fileno())
if h.hexdigest()!=expected: raise ValueError('upload digest mismatch')
print(json.dumps({'bytes':count,'sha256':h.hexdigest()}))
"""


def require(ok, message):
    if not ok:
        raise ValueError(message)


def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save_bytes(path, raw):
    with Path(path).open("xb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def save_json(path, value):
    save_bytes(path, (json.dumps(value, indent=2, sort_keys=True) + "\n").encode())


def checked(command, *, timeout=60, **kwargs):
    return subprocess.run(command, check=True, timeout=timeout, **kwargs)


def api(path):
    result = checked(["gh", "api", "repos/" + REPO + "/" + path],
                     capture_output=True, text=True)
    return json.loads(result.stdout)


def validate_binding(binding):
    require(isinstance(BINDING_TEMPLATE["ci_harness_sha256"], str) and
            re.fullmatch(r"[0-9a-f]{64}", BINDING_TEMPLATE["ci_harness_sha256"]) and
            all(isinstance(REQUIRED_COMPONENT_CONTRACTS[name], dict) and
                REQUIRED_COMPONENT_CONTRACTS[name].get("check_count", 0) > 0
                for name in ("dp-pll-7nm-reference", "dp-pll-source-generation",
                             "llcc-sm8250-reference", "llcc-source-generation")),
            "LLCC acceptance contract is unset; deployment is prohibited")
    require(isinstance(binding, dict) and set(binding) == set(BINDING_TEMPLATE), "binding fields changed")
    require(isinstance(binding["commit"], str) and re.fullmatch(r"[0-9a-f]{40}", binding["commit"]) and
            type(binding["run_id"]) is int and binding["run_id"] > 0,
            "LLCC commit/run binding is unset; deployment is prohibited")
    require(binding["commit"] not in ("0" * 40, "8798465b1b70f9c86f5cd5e1e38ce595bd6088ce",
                                      "f209f658316ada5ddd5c245f0be6c2c0eca271bd",
                                      "c232d14e46241934442a71b8cde40cb2eea1145b",
                                      "be708c3cc6c2b5ed2fde28dba0dca39ab6c4864c",
                                      "8e8e19a97efc23c215b35ed4dd40120ffab0e40d",
                                      "7639c8706faa7c7a0dbc2740214e12564a4a78b9",
                                      "ed43749ccea68fb082db3470f75c061b04ca09c0",
                                      "470834014ab97c58d8bc836eb8bd9b6ded238b40") and
            binding["run_id"] not in (45426713919, 45434451557, 45440459756, 45445501200,
                                      45467596293, 45484446351, 45487380226, 45487393635),
            "earlier CAMCC/DISPCC/VIDEOCC/DSI/DP source/artifact cannot stand in for LLCC")
    reviewed = binding["reviewed_artifact"]
    require(isinstance(reviewed, dict) and set(reviewed) == {
        "id", "archive_bytes", "archive_sha256", "qemu_sha256", "audit_receipt_sha256"},
        "reviewed artifact fields are missing or changed")
    require(type(reviewed["id"]) is int and reviewed["id"] > 0 and
            type(reviewed["archive_bytes"]) is int and 0 < reviewed["archive_bytes"] <= 1024**3 and
            all(isinstance(reviewed[key], str) and re.fullmatch(r"[0-9a-f]{64}", reviewed[key])
                for key in ("archive_sha256", "qemu_sha256", "audit_receipt_sha256")),
            "reviewed artifact identity/size/digests are invalid")
    for key in set(BINDING_TEMPLATE) - {"commit", "run_id", "reviewed_artifact"}:
        require(binding[key] == BINDING_TEMPLATE[key], "reviewed LLCC policy changed: " + key)


def generate_binding(commit, run_id, reviewed_artifact):
    """Pure generator: call only with the subsequently reviewed exact new CI."""
    value = copy.deepcopy(BINDING_TEMPLATE)
    value.update(commit=commit, run_id=run_id, reviewed_artifact=copy.deepcopy(reviewed_artifact))
    validate_binding(value)
    return value


def install_binding(value):
    global BINDING
    validate_binding(value)
    BINDING = copy.deepcopy(value)


def input_policy():
    return {"ready_candidate": str(ASSEMBLY / "ready.json"),
            "fallback_manifest": str(OLD / "prepared-001/manifest.json"),
            "selection": "once-under-native-lock", "existing_invalid_ready": "fail",
            "core": CORE, "aop": AOP, "symbols": SYMBOLS,
            "six_lun_entrypoint": CONSUMER, "six_lun_consumer_lock": str(CONSUMER_LOCK)}


def verify_reviewed_artifact(artifact):
    reviewed = BINDING["reviewed_artifact"]
    require(type(artifact.get("id")) is int and artifact["id"] == reviewed["id"] and
            type(artifact.get("size_in_bytes")) is int and
            artifact["size_in_bytes"] == reviewed["archive_bytes"] and
            artifact.get("digest") == "sha256:" + reviewed["archive_sha256"],
            "API artifact differs from the independently reviewed artifact")


def verify_config_artifact(config):
    reviewed = BINDING["reviewed_artifact"]
    require(type(config.get("artifact_id")) is int and config["artifact_id"] == reviewed["id"] and
            type(config.get("archive_bytes")) is int and config["archive_bytes"] == reviewed["archive_bytes"] and
            all(config.get(key) == reviewed[key] for key in
                ("archive_sha256", "qemu_sha256", "audit_receipt_sha256")),
            "deployment artifact differs from the independently reviewed binding")


def generate_config(tag, artifact, archive_bytes, qhash, coordinator_hash):
    """Create the deployment config only after exact-CI artifact verification."""
    validate_parameters(BINDING["commit"], BINDING["run_id"], tag)
    require(type(artifact.get("id")) is int and artifact["id"] > 0 and
            artifact.get("expired") is False and artifact.get("name") == "tb371fc-public-gates-" + BINDING["commit"] and
            artifact.get("workflow_run", {}).get("id") == BINDING["run_id"] and
            artifact["workflow_run"]["head_sha"] == BINDING["commit"] and
            re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")), "unbound deployment artifact")
    verify_reviewed_artifact(artifact)
    require(type(archive_bytes) is int and 0 < archive_bytes <= 1024**3 and
            archive_bytes == artifact["size_in_bytes"] and
            all(isinstance(x, str) and re.fullmatch(r"[0-9a-f]{64}", x) for x in (qhash, coordinator_hash)),
            "deployment archive/binary/coordinator metadata invalid")
    require(qhash == BINDING["reviewed_artifact"]["qemu_sha256"],
            "artifact QEMU differs from the independently reviewed binary")
    return {"schema": 2, "binding": copy.deepcopy(BINDING), "tag": tag, "artifact_id": artifact["id"],
            "archive_sha256": artifact["digest"][7:], "archive_bytes": archive_bytes,
            "qemu_sha256": qhash, "coordinator_sha256": coordinator_hash,
            "audit_receipt_sha256": BINDING["reviewed_artifact"]["audit_receipt_sha256"],
            "expected_host_boot_id": HOST_BOOT_ID, "input_policy": input_policy()}


def validate_parameters(commit, run_id, tag):
    validate_binding(BINDING)
    require(re.fullmatch(r"[0-9a-f]{40}", commit) is not None, "commit must be exact 40 lowercase hex")
    require(type(run_id) is int and run_id > 0, "run_id must be a positive integer")
    require(commit == BINDING["commit"] and run_id == BINDING["run_id"],
            "source/run differ from the explicitly reviewed LLCC binding")
    require(re.fullmatch(r"llcc-" + BINDING["commit"][:7] + r"-diagnostic(?:-[a-z0-9][a-z0-9_-]{0,31})?", tag) is not None,
            "use a unique llcc-<commit-short>-diagnostic[-suffix] tag with no path separators")


def verify_receipts(read, qhash):
    require(read("tb371fc-results/source-commit.txt").decode().strip() == BINDING["commit"],
            "artifact source-commit mismatch")
    require(read("tb371fc-results/qemu.sha256").decode().split()[0] == qhash,
            "artifact qemu.sha256 mismatch")
    for suite, count in (("components", BINDING["expected_components"]), ("linux", BINDING["expected_linux"])):
        result = json.loads(read("tb371fc-results/" + suite + "/result.json"))
        require(result.get("suite") == suite and result.get("source_commit") == BINDING["commit"] and
                result.get("source_dirty") is False and result.get("qemu_sha256") == qhash and
                result.get("runner_sha256") == BINDING["ci_harness_sha256"] and
                result.get("qemu_unchanged") is True and result.get("passed") is True,
                suite + " source/binary/verdict mismatch")
        require(type(result.get("passed_gates")) is int and result["passed_gates"] == count and
                type(result.get("expected_gates")) is int and result["expected_gates"] == count,
                suite + " gate count mismatch")
        gates = result.get("gates")
        require(isinstance(gates, list) and len(gates) == count and
                len({g.get("name") for g in gates}) == count and
                all(isinstance(g.get("name"), str) and g["name"] and g.get("passed") is True and
                    type(g.get("exit_code")) is int and g["exit_code"] == 0 and
                    not g.get("timed_out") and not g.get("error") and not g.get("artifact_error")
                    for g in gates), suite + " contains missing, duplicate or unsuccessful gates")
        if suite == "components":
            plan = json.loads(read("tb371fc-results/components/plan.json"))
            require(isinstance(plan, list) and len(plan) == count and
                    all(isinstance(row, dict) and isinstance(row.get("name"), str) for row in plan) and
                    len({row["name"] for row in plan}) == count and
                    {row["name"] for row in plan} == {gate["name"] for gate in gates},
                    "component plan and successful gate inventory differ")
            plans = {row["name"]: row for row in plan}
            completed = {gate["name"]: gate for gate in gates}
            for name, contract in BINDING["required_component_contracts"].items():
                require(name in plans, "required source/component gate is absent: " + name)
                row = plans[name]
                verified = completed[name].get("verified", {})
                require(type(row.get("expected_count")) is int and
                        row["expected_count"] == contract["check_count"] and
                        type(verified.get("check_count")) is int and
                        verified["check_count"] == contract["check_count"] and
                        verified.get("verdict") is True and row.get("verdict") == "passed",
                        "required component check count/verdict differs: " + name)
                for key, expected in contract["expected_values"].items():
                    actual = row.get("expected_values", {}).get(key)
                    require(type(actual) is type(expected) and actual == expected,
                            "component identity/policy differs: " + name + "/" + key)
                for key, expected in contract["expected_lengths"].items():
                    for observed in (row.get("expected_lengths", {}).get(key),
                                     verified.get("verified_lengths", {}).get(key)):
                        require(type(observed) is int and observed == expected,
                                "component evidence inventory differs: " + name + "/" + key)



def inspect_archive(path, destination=None):
    with zipfile.ZipFile(path) as archive:
        for name, limit in MEMBERS.items():
            infos = [entry for entry in archive.infolist() if entry.filename == name]
            require(len(infos) == 1, "missing or duplicate artifact member: " + name)
            info = infos[0]
            require(not info.is_dir() and not info.flag_bits & 1 and 0 < info.file_size <= limit and
                    stat.S_IFMT(info.external_attr >> 16) in (0, stat.S_IFREG),
                    "unsafe/oversized artifact member: " + name)
        with archive.open(BINARY) as stream:
            qhash = hashlib.file_digest(stream, "sha256").hexdigest()
        verify_receipts(archive.read, qhash)
        if destination is not None:
            for name in MEMBERS:
                target = destination / name
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.open(name) as source, target.open("xb") as output:
                    os.fchmod(output.fileno(), 0o700 if name == BINARY else 0o600)
                    shutil.copyfileobj(source, output, 1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                require(target.stat().st_size == archive.getinfo(name).file_size, "extraction length mismatch")
            require(sha(destination / BINARY) == qhash, "extracted QEMU digest mismatch")
        return qhash


def verify_container():
    data = json.loads(checked(DOCKER + ["inspect", CONTAINER], capture_output=True, text=True).stdout)
    require(len(data) == 1 and data[0].get("Name") == "/" + CONTAINER and
            data[0].get("State", {}).get("Running") is True, "expected development container is not running")
    mounts = [m for m in data[0].get("Mounts", []) if m.get("Destination") == "/work"]
    require(len(mounts) == 1 and mounts[0].get("Source") == VOLUME and
            mounts[0].get("Type") == "volume" and mounts[0].get("RW") is True,
            "development-container /work mount changed")
    return {"container_id": data[0]["Id"], "work_mount": mounts[0],
            "expected_previously_verified_host_root_mapping": [100000, 100000]}


def upload(source, destination):
    size, expected = source.stat().st_size, sha(source)
    with source.open("rb") as stream:
        result = checked(DOCKER + ["exec", "-i", CONTAINER, "python3", "-I", "-S", "-c", UPLOAD_CODE,
                                  str(destination), str(size), expected], stdin=stream,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=900)
    receipt = json.loads(result.stdout)
    require(receipt == {"bytes": size, "sha256": expected}, "upload acknowledgement mismatch")


def read_json_record(path, *, publishing=False):
    """Bounded regular-file read; only the directly published ready needs retry."""
    path = Path(path)
    stop, previous = time.monotonic() + 10, None
    while True:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_size <= 2 * 1024**2,
                    "invalid JSON record type/size: " + str(path))
            raw = stream.read(2 * 1024**2 + 1)
            after = os.fstat(stream.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        changed = identity(before) != identity(after) or identity(after) != identity(path.lstat()) or len(raw) != after.st_size
        error = None
        if changed:
            error = ValueError("record changed while reading: " + str(path))
        else:
            try:
                value = json.loads(raw)
            except json.JSONDecodeError as exc:
                error = exc
            else:
                require(isinstance(value, dict), "JSON record must be an object")
                record = (value, hashlib.sha256(raw).hexdigest())
                stable = (record[1], identity(after))
                if not publishing or previous == stable:
                    return record
                previous = stable
        if error is not None:
            if not publishing or time.monotonic() >= stop:
                raise error
            previous = None
        require(time.monotonic() < stop, "ready publication did not stabilize")
        time.sleep(min(0.25, max(0, stop - time.monotonic())))


def false_qualifications(value, flags=FALSE_FLAGS):
    require(all(value.get(key) is False for key in flags), "capture qualification changed")


def validate_core(manifest):
    require(manifest.get("schema") == "tb371fc-android-boot-inputs-v1", "wrong prepared schema")
    for name, expected in CORE.items():
        item = manifest[name]
        require({key: item.get(key) for key in expected} == expected and
                Path(item["path"]).stat().st_size == expected["size"] and sha(item["path"]) == expected["sha256"],
                "fixed original core input changed: " + name)


def select_inputs():
    """Exactly one readiness decision; never catch invalid ready as a fallback."""
    ready_path = ASSEMBLY / "ready.json"
    ready_present = os.path.lexists(ready_path)
    selection = {"selected_utc": utc(), "ready_candidate": str(ready_path),
                 "ready_present_at_selection": ready_present,
                 "capture_qualification": {key: False for key in FALSE_FLAGS}}
    if not ready_present:
        path = OLD / "prepared-001/manifest.json"
        manifest, manifest_hash = read_json_record(path)
        require(manifest.get("luns") == [], "fallback must be the reviewed no-LUN manifest")
        validate_core(manifest)
        selection.update(classification="no-lun-diagnostic", storage_complete=False,
                         original_luns_attached=0, reason="generation2-ready-not-present-at-selection")
    else:
        ready, ready_hash = read_json_record(ready_path, publishing=True)
        false_qualifications(ready)
        require(ready.get("all_six_original_luns_assembled") is True and
                ready.get("generation2_jobs_verified") == 107 and ready.get("assembly_manifests") == 108 and
                ready.get("old_partials_preserved") is True and
                ready.get("generation_plan_sha256") == GENERATION_PLAN_SHA and
                ready.get("source_binding_sha256") == SOURCE_BINDING_SHA and
                sha(ASSEMBLY / "assemble_generation2.py") == ASSEMBLY_SHA and
                sha(ASSEMBLY / "jobs-plan.json") == GENERATION_PLAN_SHA and
                sha(ASSEMBLY / "source-binding.json") == SOURCE_BINDING_SHA and
                sha(ASSEMBLY / "generation2-complete.json") == ready["generation_completion_sha256"],
                "generation2 ready/tool/source binding mismatch")
        baseline_path = ASSEMBLY / "baseline-result.json"
        require(ready["baseline_verification"] == str(baseline_path), "baseline path changed")
        baseline, baseline_hash = read_json_record(baseline_path)
        require(baseline.get("schema") == "tb371fc-reboot-resume-verification-v1" and
                baseline.get("verified") is True and
                baseline["plan_sha256"] == sha(ASSEMBLY / "baseline-plan.json") and
                set(baseline["captures"]) == {"super", "lun1", "lun2", "lun3", "lun4", "lun5"} and
                [x["lun"] for x in baseline["raws"]] == list(range(1, 6)), "baseline verification changed")
        false_qualifications(baseline, ("snapshot_consistent", "pristine_factory_images", "restore_tested", "android_boot_tested"))
        raw_receipt, receipt_hash = read_json_record(ASSEMBLY / "raw/lun0/lun0.raw.manifest.json")
        false_qualifications(raw_receipt, FALSE_FLAGS[:-1] + ("pristine_factory",))
        require(receipt_hash == ready["new_lun0_assembly_receipt_sha256"] and
                raw_receipt.get("schema") == "tb371fc-whole-lun-assembly-v1" and
                raw_receipt.get("status") == "verified-whole-lun-assembly" and
                all(raw_receipt.get(k) is True for k in ("whole_source_bytes_captured", "coverage_nonoverlapping", "raw_readback_verified")) and
                raw_receipt["source"] == baseline["captures"]["super"]["source"] and
                raw_receipt["chunks_verified"] == 1842 and len(raw_receipt["source_manifests"]) == 108 and
                raw_receipt["raw"]["path"] == str(ASSEMBLY / "raw/lun0/lun0.raw") and
                raw_receipt["raw"]["bytes"] == LUN_BYTES[0], "LU0 assembly evidence changed")
        path = ASSEMBLY / "prepared-001/manifest.json"
        require(ready["prepared_manifest"] == str(path), "ready prepared path changed")
        manifest, manifest_hash = read_json_record(path)
        validate_core(manifest)
        false_qualifications(manifest["metadata"], ("factory_provenance_verified", "avb_verified", "android_boot_verified", "partition_layout_verified"))
        require([x["lun"] for x in manifest["luns"]] == list(range(6)), "exact six-LUN prepared set required")
        hashes = [raw_receipt["raw"]["sha256"]] + [x["sha256"] for x in baseline["raws"]]
        paths = [str(ASSEMBLY / "raw/lun0/lun0.raw")] + [str(ASSEMBLY.parent / ("raw/lun%d/lun%d.raw" % (n, n))) for n in range(1, 6)]
        for n, lun in enumerate(manifest["luns"]):
            require(lun["path"] == paths[n] and lun["sha256"] == hashes[n] and re.fullmatch(r"[0-9a-f]{64}", hashes[n]) and
                    lun["size"] == LUN_BYTES[n] and lun["format"] == "raw" and
                    lun["logical_block_size"] == 4096 and lun["logical_block_count"] == LUN_BYTES[n] // 4096 and
                    lun["descriptor"] == {"logical_block_size_exponent": 12, "logical_block_count": LUN_BYTES[n] // 4096} and
                    lun["readonly_original"] is True and lun["sha256_verified"] is True and
                    lun["partition_layout_verified"] is False and Path(paths[n]).stat().st_size == LUN_BYTES[n],
                    "prepared LUN geometry/hash/evidence mismatch: " + str(n))
        selection.update(classification="six-lun-original-input", storage_complete=True,
                         original_luns_attached=6, ready_sha256=ready_hash,
                         baseline_sha256=baseline_hash, lun0_receipt_sha256=receipt_hash,
                         source_binding_sha256=SOURCE_BINDING_SHA, assembly_coordinator_sha256=ASSEMBLY_SHA,
                         runner_requires_raw_hash_recheck_before_qemu=True)
    selection.update(prepared_manifest=str(path), prepared_manifest_sha256=manifest_hash)
    return path, manifest, selection


def validate_preservation(result, manifest):
    expected = [{"lun": x["lun"], "stat_unchanged": True, "base_sha256_supplied": x["sha256"],
                 "full_base_digest_rechecked_after_run": False} for x in manifest["luns"]]
    require(result["preservation"]["preserved"] is True and result["preservation"]["original_luns"] == expected,
            "runner LUN preservation set differs from selected input")
    require(result.get("base_files_opened_read_only") is True, "runner did not establish readonly backing nodes")
    reported = result.get("stock_android_boot_passed")
    require(type(reported) is bool and (manifest["luns"] or reported is False), "no-LUN diagnostics cannot qualify Android boot")
    if reported:
        require(result["outcome"] == "android-boot-complete-observed", "runner Android verdict contradicts outcome")


def full_lun_binding(manifest_path, manifest, selection, plan, qemu, qemu_img):
    """Bind completed selected files; never make another readiness decision."""
    require(selection["classification"] == "six-lun-original-input" and
            selection["storage_complete"] is True and selection["original_luns_attached"] == 6,
            "full consumer requires the already selected complete six-LUN input")
    qemu_hash = sha(qemu)
    require(qemu_hash == BINDING["reviewed_artifact"]["qemu_sha256"],
            "full-LUN QEMU differs from the independently reviewed binary")

    def selected(path, expected):
        value, digest = read_json_record(path)
        require(digest == expected, "selected receipt changed before consumer binding: " + str(path))
        return value, {"path": str(path), "sha256": digest}

    ready, ready_ref = selected(ASSEMBLY / "ready.json", selection["ready_sha256"])
    baseline, baseline_ref = selected(ASSEMBLY / "baseline-result.json", selection["baseline_sha256"])
    raw0, raw0_ref = selected(ASSEMBLY / "raw/lun0/lun0.raw.manifest.json", selection["lun0_receipt_sha256"])
    prepared, prepared_ref = selected(manifest_path, selection["prepared_manifest_sha256"])
    require(prepared == manifest, "selected prepared manifest object changed")
    _, completion_ref = selected(ASSEMBLY / "generation2-complete.json", ready["generation_completion_sha256"])
    source, _ = selected(ASSEMBLY / "source-binding.json", SOURCE_BINDING_SHA)
    require(source["source"] == baseline["captures"]["super"]["source"] == raw0["source"],
            "actual source binding and selected LU0 source disagree")
    require(sha(ASSEMBLY / "jobs-plan.json") == GENERATION_PLAN_SHA and
            sha(ASSEMBLY / "assemble_generation2.py") == ASSEMBLY_SHA and
            sha(ASSEMBLY / "baseline-plan.json") == baseline["plan_sha256"],
            "selected generation/coordinator/baseline plan changed")
    boot = plan["known_inputs"]["boot_image"]
    original_boot = {"path": boot["path"], "size": boot["bytes"], "sha256": boot["sha256"]}
    require({key: prepared["provenance"]["boot_image"]["source"].get(key) for key in original_boot} == original_boot,
            "prepared boot-image provenance differs from the original native plan")
    luns = [{**{key: row[key] for key in ("lun", "path", "sha256", "logical_block_size", "logical_block_count")},
             "bytes": row["size"],
             "source": copy.deepcopy(baseline["captures"]["super" if row["lun"] == 0 else "lun" + str(row["lun"])]["source"])}
            for row in prepared["luns"]]
    return {"schema": "tb371fc-full-lun-boot-binding-v1", "ready": ready_ref,
            "prepared_manifest": prepared_ref, "lun0_assembly": raw0_ref,
            "baseline_verification": baseline_ref, "generation_completion": completion_ref,
            "generation_plan_sha256": GENERATION_PLAN_SHA, "source_binding_sha256": SOURCE_BINDING_SHA,
            "capture_counts": {"jobs": ready["generation2_jobs_verified"],
                               "assembly_manifests": ready["assembly_manifests"], "chunks": raw0["chunks_verified"]},
            "inputs": {**{name: {key: prepared[name][key] for key in ("path", "size", "sha256")}
                          for name in CORE}, "boot_image": original_boot},
            "luns": luns,
            "executables": {"qemu": {"path": str(qemu), "sha256": qemu_hash},
                            "qemu_img": {"path": str(qemu_img), "sha256": sha(qemu_img)}},
            "runner_dependencies": {name: digest for name, digest in BINDING["runner_sha256"].items() if name != CONSUMER},
            "machine": plan["machine"], "global_properties": list(plan["global_properties"]),
            "resources": [{**AOP, "size": Path(AOP["path"]).stat().st_size}],
            "observation": {"seconds": 120, "max_log_mib": 128, "snapshot_interval": 15,
                            "stop_on_kernel_failure": True, "trace_interrupts": True},
            "printk": {**SYMBOLS, "size": Path(SYMBOLS["path"]).stat().st_size,
                       "kernel_sha256": prepared["kernel"]["sha256"]}}


def verify_consumer_result(consumer, output, binding_path, binding_hash, invocation_rc, runner_result):
    """Require a definite public handoff, including definite failed boots."""
    require(consumer.get("schema") == "tb371fc-full-lun-boot-result-v1" and
            consumer.get("dry_run") is False and consumer.get("runner_invocation_started") is True and
            consumer.get("executed") is True and consumer.get("execution_state_unknown") is False and
            consumer.get("automatic_retry_attempted") is False and consumer.get("handoff_verified") is True and
            consumer.get("full_lun_digests_verified") is True and consumer.get("bound_inputs_unchanged") is True and
            "error" not in consumer and "publication_error" not in consumer,
            "public full-LUN consumer did not verify a definite executed handoff")
    false_qualifications(consumer["capture_qualifications"])
    require(consumer["binding_sha256"] == binding_hash == sha(binding_path) and
            consumer["runner_result_sha256"] == sha(output / "runner/result.json") and
            consumer["runner_plan_sha256"] == sha(output / "runner/plan.json"),
            "consumer binding/result/plan digest mismatch")
    require(type(invocation_rc) is int and type(consumer.get("runner_returncode")) is int and
            invocation_rc == consumer["runner_returncode"] and invocation_rc in (0, 1, 2) and
            type(consumer.get("stock_android_boot_passed")) is bool and
            consumer["stock_android_boot_passed"] is runner_result["stock_android_boot_passed"] and
            (invocation_rc == 0) is consumer["stock_android_boot_passed"] and
            consumer["runner_outcome"] == consumer["outcome"] == runner_result["outcome"],
            "consumer/runner outcome or exit code disagrees")


def host_context(tag):
    observed = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    require(observed == HOST_BOOT_ID, "host rebooted; inspect before running this bound coordinator")
    require(os.environ.get("TMUX") and os.environ.get("TMUX_PANE"), "long coordinator must run in its own tmux context")
    fields = checked(["tmux", "display-message", "-p", "-t", os.environ["TMUX_PANE"],
                      "#{session_name}\t#{window_name}\t#{pane_id}"], capture_output=True, text=True).stdout.strip().split("\t")
    require(len(fields) == 3 and tag in fields[:2] and fields[2] == os.environ["TMUX_PANE"],
            "tmux session or window must be named with this unique run007 tag")
    return {"boot_id": observed, "tmux_session": fields[0], "tmux_window": fields[1], "tmux_pane": fields[2]}


def guest_config(path, expected):
    require(os.geteuid() == 0 and os.getegid() == 0, "container execution must use uid/gid 0")
    require(re.fullmatch(r"[0-9a-f]{64}", expected) and sha(path) == expected, "configuration digest mismatch")
    config = json.loads(path.read_bytes())
    require(config.get("schema") == 2 and config.get("expected_host_boot_id") == HOST_BOOT_ID and
            config.get("input_policy") == input_policy(), "deployment input/host policy changed")
    install_binding(config.get("binding"))
    verify_config_artifact(config)
    validate_parameters(BINDING["commit"], BINDING["run_id"], config["tag"])
    root = GUEST_PARENT / config["tag"]
    require(path == root / "config.json" and Path(__file__).resolve() == root / "coordinator.py",
            "unexpected deployed coordinator/configuration location")
    require(sha(__file__) == config["coordinator_sha256"], "coordinator bytes changed")
    for name, expected_hash in BINDING["runner_sha256"].items():
        require(sha(root / "runner" / name) == expected_hash, "deployed runner changed: " + name)
    return root, config


def prepare_guest(path, expected):
    root, config = guest_config(path, expected)
    archive = root / "artifact.zip"
    require(archive.stat().st_size == config["archive_bytes"] and sha(archive) == config["archive_sha256"],
            "deployed archive mismatch")
    require(inspect_archive(archive, root) == config["qemu_sha256"], "deployed artifact QEMU differs")
    save_json(root / "deployment-receipt.json", {"config_sha256": expected,
              "qemu_sha256": config["qemu_sha256"], "source_commit": BINDING["commit"],
              "verified_components": BINDING["expected_components"],
              "verified_linux": BINDING["expected_linux"], "native_started": False})
    return 0


def native(path, expected):
    root, config = guest_config(path, expected)
    deployed = json.loads((root / "deployment-receipt.json").read_bytes())
    require(deployed["config_sha256"] == expected, "deployment receipt belongs to another configuration")
    qemu = root / BINARY
    qhash = sha(qemu)
    require(qhash == config["qemu_sha256"] == deployed["qemu_sha256"], "native QEMU digest mismatch")
    verify_receipts(lambda name: (root / name).read_bytes(), qhash)
    old_plan = OLD / "native-command-plan.json"
    plan = copy.deepcopy(json.loads(old_plan.read_bytes()))
    manifest_path, manifest, selection = select_inputs()
    save_json(root / "input-selection.json", selection)
    core = {name: manifest[name] for name in ("kernel", "initrd", "dtb", "bootargs")}
    for name, item in plan["known_inputs"].items():
        if "path" in item:
            require(sha(item["path"]) == item["sha256"], "known original input changed: " + name)
    for name, item in core.items():
        require(sha(item["path"]) == item["sha256"], "prepared core input changed: " + name)
    aop = plan["known_inputs"]["aop_partial_command_db"]
    symbols = plan["known_inputs"]["prior_symbols"]
    require({key: aop.get(key) for key in AOP} == AOP and
            {key: symbols.get(key) for key in SYMBOLS} == SYMBOLS,
            "fixed AOP or runtime-symbol record changed")
    require(plan["known_inputs"]["kernel_existing_extraction"]["sha256"] == core["kernel"]["sha256"],
            "runtime symbols and prepared kernel binding differ")
    properties = plan["global_properties"]
    require(isinstance(properties, list) and len(properties) == 19 and
            all(isinstance(g, str) and "=" in g for g in properties), "old plan must retain its 19 globals")
    require(hashlib.sha256(json.dumps({key: plan[key] for key in ("machine", "global_properties")},
                                     sort_keys=True, separators=(",", ":")).encode()).hexdigest() == OLD_POLICY_SHA,
            "original machine/19-global policy changed")
    keys = [g.split("=", 1)[0] for g in properties + ADDED_GLOBALS]
    require(len(set(keys)) == 28, "duplicate or pre-existing MDSS/NPU/VIDEO/CAMCC/DISPCC/VIDEOCC/DSI/DP/LLCC property")
    plan.update(qemu_source_commit=BINDING["commit"], ci_run=BINDING["run_id"],
                global_properties=properties + ADDED_GLOBALS,
                prior_plan_sha256=sha(old_plan), prepared_manifest_sha256=selection["prepared_manifest_sha256"],
                input_selection=selection,
                verified_luns_currently_supplied=selection["storage_complete"],
                runtime_purpose="LLCC original-input frontier; " + selection["classification"])
    new_plan = root / "native-command-plan.json"
    save_json(new_plan, plan)
    qemu_img = shutil.which("qemu-img")
    require(qemu_img is not None, "qemu-img is unavailable")
    qemu_img = str(Path(qemu_img).resolve(strict=True))
    output = root / "original-android-llcc"
    binding_path, binding_hash, runner_output = None, None, output
    if selection["storage_complete"]:
        binding_path = root / "full-lun-binding.json"
        save_json(binding_path, full_lun_binding(manifest_path, manifest, selection, plan, qemu, qemu_img))
        binding_hash = sha(binding_path)
        # This is an additional container lock. The caller retains the real
        # SHARED_CONTROL/native-run.lock for the entire container invocation below.
        args = ["python3", "-S", str(root / "runner" / CONSUMER), "--ready", str(ASSEMBLY / "ready.json"),
                "--binding", str(binding_path), "--lock-file", str(root / CONSUMER_LOCK), "--out", str(output)]
        runner_output = output / "runner"
    else:
        args = ["python3", "-S", str(root / "runner/boot_android.py"), "--manifest", str(manifest_path),
                "--qemu", str(qemu), "--qemu-img", qemu_img, "--out", str(output), "--machine", plan["machine"],
                "--configuration-evidence", str(new_plan), "--seconds", "120", "--snapshot-interval", "15",
                "--stop-on-kernel-failure", "--trace-interrupts", "--legacy-printk-symbols",
                plan["known_inputs"]["prior_symbols"]["path"], "--symbols-kernel-sha256", core["kernel"]["sha256"]]
        for value in plan["global_properties"]:
            args += ["--global-property", value]
    save_json(root / "actual-invocation-llcc.json", {"argv": args, "source": BINDING["commit"],
              "ci_run": BINDING["run_id"], "qemu_sha256": qhash, "input_selection": selection,
              "storage_complete": selection["storage_complete"],
              "original_luns_attached": selection["original_luns_attached"], "runner_sha256": BINDING["runner_sha256"],
              "full_lun_binding_sha256": binding_hash, "runner_output": str(runner_output)})
    receipt = {"started_utc": utc(), "storage_complete": selection["storage_complete"],
               "original_luns_attached": selection["original_luns_attached"],
               "input_classification": selection["classification"], "snapshot_consistent": False,
               "stock_android_boot_passed": False, "runner_exit_code": None, "invocation_exit_code": None,
               "full_lun_binding_sha256": binding_hash, "runner_result": str(runner_output / "result.json")}
    try:
        # One invocation only. The runner owns its 120-second observation bound.
        require(sha(qemu) == qhash == BINDING["reviewed_artifact"]["qemu_sha256"],
                "reviewed QEMU changed before the runner invocation")
        receipt["invocation_exit_code"] = subprocess.run(args, check=False).returncode
        if not selection["storage_complete"]:
            receipt["runner_exit_code"] = receipt["invocation_exit_code"]
    except Exception as exc:
        receipt["invocation_error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        try:
            receipt["core_unchanged"] = {name: sha(item["path"]) == item["sha256"] for name, item in core.items()}
            receipt["aop_sha256_after"] = sha(aop["path"])
            receipt["aop_unchanged"] = receipt["aop_sha256_after"] == aop["sha256"]
            receipt["symbols_sha256_after"] = sha(symbols["path"])
            receipt["symbols_unchanged"] = receipt["symbols_sha256_after"] == symbols["sha256"]
            consumer = None
            if selection["storage_complete"]:
                consumer, digest = read_json_record(output / "result.json")
                receipt.update(consumer_result=str(output / "result.json"), consumer_result_sha256=digest,
                               consumer_outcome=consumer.get("outcome"))
            result = json.loads((runner_output / "result.json").read_bytes())
            if consumer is not None:
                verify_consumer_result(consumer, output, binding_path, binding_hash,
                                       receipt["invocation_exit_code"], result)
                receipt["runner_exit_code"] = consumer["runner_returncode"]
            receipt["runner_outcome"] = result["outcome"]
            receipt["qmp_shutdown_normal"] = result.get("qmp_shutdown_normal")
            validate_preservation(result, manifest)
            require(result["executed"] is True and
                    result["stop_on_kernel_failure"] is True and result["qemu_sha256"] == qhash and
                    result.get("qmp_shutdown_normal") is True and not result.get("qmp_drain_error") and
                    result["qemu_unchanged"] is True and sha(qemu) == qhash and
                    result["manifest_sha256"] == plan["prepared_manifest_sha256"] == sha(manifest_path) and
                    result["printk_symbols_sha256"] == symbols["sha256"] and receipt["symbols_unchanged"] and
                    result["runner_sha256"] == BINDING["runner_sha256"]["boot_android.py"] and
                    all(sha(root / "runner" / name) == digest for name, digest in BINDING["runner_sha256"].items()) and
                    all(receipt["core_unchanged"].values()) and receipt["aop_unchanged"],
                    "native result or original-input preservation failed")
            require(result["stock_android_boot_passed"] is False or receipt["runner_exit_code"] == 0,
                    "successful Android verdict requires runner exit 0")
            receipt["stock_android_boot_passed"] = result["stock_android_boot_passed"]
        except Exception as exc:
            receipt["verification_error"] = type(exc).__name__ + ": " + str(exc)
        receipt["finished_utc"] = utc()
        save_json(root / "llcc-run-receipt.json", receipt)
        print(json.dumps(receipt), flush=True)
    return receipt["invocation_exit_code"] if not any(k.endswith("error") for k in receipt) else 1


# Only matching process kinds and PIDs leave this scanner. In particular,
# unrelated process arguments, which may contain credentials, are never logged.
NATIVE_PROCESS_SCAN_CODE = r"""import json,os,pathlib,sys
root=pathlib.Path(sys.argv[1]) if len(sys.argv)==2 else pathlib.Path('/proc')
assert root.is_absolute()
matches=[]
for entry in root.iterdir():
 if not entry.name.isdecimal(): continue
 try:
  raw=(entry/'cmdline').read_bytes()
 except (FileNotFoundError,ProcessLookupError):
  continue
 argv=[os.fsdecode(arg) for arg in raw.split(b'\0') if arg]
 if not argv: continue
 names=[os.path.basename(arg) for arg in argv]
 kind=None
 if names[0]=='qemu-system-aarch64':
  kind='aarch64-qemu'
 elif 'boot_android.py' in names:
  kind='boot-android-runner'
 elif 'boot_android_from_ready.py' in names:
  kind='full-lun-consumer'
 elif 'native' in argv and any(
  arg.startswith(('/work/stockboot-e1a9/','/work/stockboot-6a5/')) and
  os.path.basename(arg)=='coordinator.py' for arg in argv):
  kind='native-coordinator'
 if kind: matches.append({'pid':int(entry.name),'kind':kind})
print(json.dumps({'schema':'tb371fc-native-process-scan-v1',
                  'matches':sorted(matches,key=lambda row:row['pid'])}))
"""


def ensure_own_control():
    require(CONTROL.is_absolute() and CONTROL != SHARED_CONTROL and
            CONTROL.parent.resolve(strict=True) == CONTROL.parent,
            "own control parent is not the expected absolute directory")
    CONTROL.mkdir(mode=0o700, exist_ok=True)
    info = CONTROL.lstat()
    require(stat.S_ISDIR(info.st_mode) and CONTROL.resolve(strict=True) == CONTROL and
            info.st_uid == os.geteuid() and not info.st_mode & 0o022,
            "own control must be a real directory owned by the current user without group/other writes")


def owner_idle_guard():
    """Observe the owner and both process namespaces; never acquire ownership."""
    checkpoint_path = SHARED_CONTROL / OWNER_CHECKPOINT_NAME
    require(SHARED_CONTROL.resolve(strict=True) == SHARED_CONTROL,
            "shared owner control path must not be redirected")
    checkpoint, digest = read_json_record(checkpoint_path)
    prep = checkpoint.get("original_run008_preparation")
    require(checkpoint.get("owner") == OWNER_ID and
            type(checkpoint.get("active_foreground_orchestration")) is bool and
            isinstance(checkpoint.get("updated_utc"), str) and
            datetime.datetime.fromisoformat(checkpoint["updated_utc"]).tzinfo is not None and
            isinstance(prep, dict), "owner run008 checkpoint is missing or unrecognized")
    require(prep.get("deployed") is False and prep.get("started") is False and
            prep.get("state") == "local_only_unbound_not_deployed",
            "owner run008 is deployed, started or no longer in the reviewed idle state")
    observations = {}
    for name, prefix in (("host", []), ("container", EXEC)):
        response = checked(prefix + ["python3", "-I", "-S", "-c", NATIVE_PROCESS_SCAN_CODE],
                           capture_output=True, text=True)
        observed = json.loads(response.stdout)
        require(isinstance(observed, dict) and set(observed) == {"schema", "matches"} and
                observed["schema"] == "tb371fc-native-process-scan-v1" and
                isinstance(observed["matches"], list) and
                all(isinstance(row, dict) and set(row) == {"pid", "kind"} and
                    type(row["pid"]) is int and row["pid"] > 0 and
                    row["kind"] in ("aarch64-qemu", "boot-android-runner", "full-lun-consumer", "native-coordinator")
                    for row in observed["matches"]), "invalid native-process observation: " + name)
        require(not observed["matches"], "existing native process in " + name + ": " +
                json.dumps(observed["matches"], sort_keys=True))
        observations[name] = observed
    return {"observed_utc": utc(), "checkpoint": str(checkpoint_path), "checkpoint_sha256": digest,
            "owner": checkpoint["owner"], "owner_updated_utc": checkpoint["updated_utc"],
            "active_foreground_orchestration": checkpoint["active_foreground_orchestration"],
            "run008": {key: prep[key] for key in ("deployed", "started", "state")},
            "native_processes": observations}


@contextmanager
def shared_native_lock():
    """Open the existing owner's inode read-only; never create or alter it."""
    path = SHARED_CONTROL / "native-run.lock"
    before = path.lstat()
    require(stat.S_ISREG(before.st_mode), "existing shared native lock must be a regular file")
    with path.open("rb") as lock:
        opened = os.fstat(lock.fileno())
        require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                "shared native lock changed while opening")
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("existing shared native lock is busy; no waiting or native retry") from exc
        current = path.lstat()
        require(stat.S_ISREG(current.st_mode) and
                (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino),
                "shared native lock inode changed")
        yield {"path": str(path), "open_mode": "rb", "exclusive": True, "nonblocking": True,
               "device": opened.st_dev, "inode": opened.st_ino}
        current = path.lstat()
        require(stat.S_ISREG(current.st_mode) and
                (current.st_dev, current.st_ino) == (opened.st_dev, opened.st_ino),
                "shared native lock inode changed during the native call")


def claim_native_attempt(tag, binding_hash, config_hash):
    """A second tag cannot automatically repeat this exact reviewed artifact."""
    reviewed = BINDING["reviewed_artifact"]
    claim = CONTROL / ("native-once-" + str(reviewed["id"]) + "-" + reviewed["archive_sha256"])
    claim.mkdir(mode=0o700, exist_ok=False)
    save_json(claim / "attempt.json", {"utc": utc(), "tag": tag, "binding_sha256": binding_hash,
              "config_sha256": config_hash, "reviewed_artifact": copy.deepcopy(reviewed),
              "automatic_retry_permitted": False})
    return str(claim)


def host(args):
    binding, binding_hash = read_json_record(args.binding)
    require(re.fullmatch(r"[0-9a-f]{64}", args.binding_sha256) and binding_hash == args.binding_sha256,
            "reviewed binding file digest mismatch")
    install_binding(binding)
    validate_parameters(args.commit, args.run_id, args.tag)
    require(0 < args.ci_wait_seconds <= 7200, "CI wait must be in (0,7200] seconds")
    if args.check_only:
        print(json.dumps({"parameters_valid": True, "execution_started": False,
                          "binding": BINDING, "binding_sha256": binding_hash,
                          "input_policy": input_policy(), "tag": args.tag}, indent=2))
        return 0
    require(getattr(args, "host", False) is True, "native execution requires explicit host --host")
    context = host_context(args.tag)
    owner_preflight = owner_idle_guard()
    ensure_own_control()
    out = CONTROL / args.tag
    out.mkdir(mode=0o700, exist_ok=False)
    job = {"source_commit": args.commit, "ci_run": args.run_id, "native_invoked": False,
           "input_policy": input_policy(), "binding_sha256": binding_hash,
           "host_context": context, "owner_preflight": owner_preflight, "started_utc": utc()}
    rc = 1
    try:
        deadline = time.monotonic() + args.ci_wait_seconds
        while True:
            run = api("actions/runs/" + str(args.run_id))
            require(run["id"] == args.run_id and run["head_sha"] == args.commit and
                    run.get("path") == ".github/workflows/tb371fc.yml", "CI identity or workflow changed")
            print(json.dumps({"ci_run": args.run_id, "status": run["status"],
                              "conclusion": run.get("conclusion")}), flush=True)
            if run["status"] == "completed":
                require(run.get("conclusion") == "success", "exact LLCC CI did not succeed")
                break
            require(time.monotonic() < deadline, "CI wait bound reached; native was not started")
            time.sleep(min(20, max(0, deadline - time.monotonic())))
        items = api("actions/runs/" + str(args.run_id) + "/artifacts?per_page=100")
        require(items["total_count"] <= 100, "unexpected artifact pagination; inspect before proceeding")
        matches = [a for a in items["artifacts"] if a["name"] == "tb371fc-public-gates-" + args.commit]
        require(len(matches) == 1, "exact public artifact missing or ambiguous")
        artifact = matches[0]
        require(artifact.get("expired") is False and
                re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", "")) and
                artifact.get("workflow_run", {}).get("id") == args.run_id and
                artifact["workflow_run"]["head_sha"] == args.commit,
                "artifact provenance/digest invalid")
        verify_reviewed_artifact(artifact)
        require(type(artifact["size_in_bytes"]) is int and 0 < artifact["size_in_bytes"] <= 1024**3 and
                shutil.disk_usage(out).free >= artifact["size_in_bytes"] + 64 * 1024**2,
                "artifact size or host free space outside the download bound")
        archive = out / "artifact.zip"
        with archive.open("xb") as stream:
            checked(["gh", "api", "repos/" + REPO + "/actions/artifacts/" + str(artifact["id"]) + "/zip"],
                    stdout=stream, timeout=900)
            stream.flush()
            os.fsync(stream.fileno())
        archive_hash = sha(archive)
        require("sha256:" + archive_hash == artifact["digest"] and
                archive_hash == BINDING["reviewed_artifact"]["archive_sha256"] and
                archive.stat().st_size == BINDING["reviewed_artifact"]["archive_bytes"],
                "downloaded artifact differs from the independently reviewed archive")
        qhash = inspect_archive(archive)
        require(qhash == BINDING["reviewed_artifact"]["qemu_sha256"],
                "downloaded QEMU differs from the independently reviewed binary")
        save_json(out / "artifact-receipt.json", {"ci": run, "artifact": artifact,
                  "zip_sha256": sha(archive), "qemu_sha256": qhash,
                  "reviewed_artifact": copy.deepcopy(BINDING["reviewed_artifact"]),
                  "verified_components": BINDING["expected_components"], "verified_linux": BINDING["expected_linux"]})
        runner = out / "runner"
        runner.mkdir(mode=0o700)
        for name, expected in BINDING["runner_sha256"].items():
            remote = api("contents/tb371fc/scripts/" + name + "?ref=" + args.commit)
            require(remote.get("type") == "file" and remote.get("path") == "tb371fc/scripts/" + name and
                    remote.get("encoding") == "base64" and 0 < remote.get("size", 0) <= 2 * 1024**2,
                    "unexpected commit-ref runner response")
            raw = base64.b64decode("".join(remote["content"].split()), validate=True)
            require(len(raw) == remote["size"] and hashlib.sha256(raw).hexdigest() == expected,
                    "commit-ref runner hash mismatch: " + name)
            save_bytes(runner / name, raw)
        config = generate_config(args.tag, artifact, archive.stat().st_size, qhash, sha(__file__))
        config_path = out / "config.json"
        save_json(config_path, config)
        config_hash = sha(config_path)
        preflight = verify_container()
        save_json(out / "container-preflight.json", preflight)
        guest = GUEST_PARENT / args.tag
        checked(EXEC + ["python3", "-I", "-S", "-c", SETUP_CODE, str(guest)])
        for source, destination in [(archive, guest / "artifact.zip"),
                                    (Path(__file__).resolve(), guest / "coordinator.py"),
                                    (config_path, guest / "config.json")]:
            upload(source, destination)
        for name in BINDING["runner_sha256"]:
            upload(runner / name, guest / "runner" / name)
        command = EXEC + ["python3", "-I", "-S", str(guest / "coordinator.py")]
        common = ["--config", str(guest / "config.json"), "--config-sha256", config_hash]
        checked(command + ["prepare"] + common, timeout=900)
        with shared_native_lock() as lock_record:
            require(host_context(args.tag) == context and
                    verify_container()["container_id"] == preflight["container_id"],
                    "host/tmux/container context changed before native")
            job["owner_pre_native"] = owner_idle_guard()
            job["shared_native_lock"] = lock_record
            job["native_once_claim"] = claim_native_attempt(args.tag, binding_hash, config_hash)
            save_json(out / "native-attempt.json", {"utc": utc(), "config_sha256": config_hash,
                      "argv": command + ["native"] + common, "retry_permitted": False})
            job["native_invoked"] = True
            # No RPC/native retry and no transport timeout that might conceal a
            # still-running guest. The invoked runner has its own time bound.
            rc = subprocess.run(command + ["native"] + common, check=False).returncode
            job["native_exit_code"] = rc
            selected = json.loads(checked(EXEC + ["cat", str(guest / "input-selection.json")],
                                         capture_output=True, text=True).stdout)
            job.update(input_classification=selected["classification"], storage_complete=selected["storage_complete"],
                       original_luns_attached=selected["original_luns_attached"])
    except Exception as exc:
        rc = 1
        job["error"] = type(exc).__name__ + ": " + str(exc)
        job["native_status_requires_inspection"] = job["native_invoked"]
    finally:
        job.update(exit_code=rc, finished_utc=utc())
        save_json(out / "job-result.json", job)
        print(json.dumps(job), flush=True)
    return rc


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    host_parser = modes.add_parser("host", help="Run on the existing authorized doesvm host")
    host_parser.add_argument("--host", action="store_true",
                             help="Explicitly permit one reviewed own-lane native attempt; omitted means no execution")
    host_parser.add_argument("--commit", required=True)
    host_parser.add_argument("--run-id", required=True, type=int)
    host_parser.add_argument("--tag", required=True)
    host_parser.add_argument("--binding", required=True, type=Path, help="Explicitly reviewed LLCC binding JSON")
    host_parser.add_argument("--binding-sha256", required=True)
    host_parser.add_argument("--ci-wait-seconds", type=float, default=3600)
    host_parser.add_argument("--check-only", action="store_true", help="Validate binding only; no RPC, downloads or outputs")
    binding_parser = modes.add_parser("make-binding", help="Create a local binding from reviewed new commit/CI; no external actions")
    binding_parser.add_argument("--commit", required=True)
    binding_parser.add_argument("--run-id", required=True, type=int)
    binding_parser.add_argument("--reviewed-artifact", required=True, type=Path,
                                help="Explicit artifact pins from the matching independent audit JSON")
    binding_parser.add_argument("--out", required=True, type=Path)
    for mode in ("prepare", "native"):
        sub = modes.add_parser(mode, help="Internal deployed-container mode")
        sub.add_argument("--config", required=True, type=Path)
        sub.add_argument("--config-sha256", required=True)
    args = parser.parse_args(argv)
    if args.mode == "make-binding":
        reviewed_artifact, _ = read_json_record(args.reviewed_artifact)
        save_json(args.out, generate_binding(args.commit, args.run_id, reviewed_artifact))
        print(json.dumps({"binding": str(args.out), "sha256": sha(args.out), "execution_started": False}))
        return 0
    return host(args) if args.mode == "host" else (
        prepare_guest(args.config, args.config_sha256) if args.mode == "prepare" else
        native(args.config, args.config_sha256))


if __name__ == "__main__":
    raise SystemExit(main())
