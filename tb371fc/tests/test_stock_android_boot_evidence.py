#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""SYNTHETIC UNIT FIXTURES ONLY: no QEMU, firmware, guest or tablet is used."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from verify_stock_android_boot_evidence import (Bundle, INVENTORY_KIND, MANIFEST_KIND,
                                               verify)


class StockEvidenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-stock-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "manifest.json"
        self.inventory = {
            "kind": INVENTORY_KIND, "synthetic": True,
            "backup_id": "SYNTHETIC UNIT TEST; NOT A FIRMWARE BACKUP", "inputs": {},
        }
        self.manifest = {
            "schema_version": 1, "kind": MANIFEST_KIND, "synthetic": True,
            "run_id": "synthetic-unit-test", "boot_id": "11111111-2222-4333-8444-555555555555",
            "scope": "android-framework", "evidence_kind": "guest-runtime",
            "entry_path": "direct-kernel", "slot": "a",
            "kernel_cmdline": "SYNTHETIC_FIXTURE_ONLY androidboot.slot_suffix=_a",
            "inputs": [], "userdata": {"mode": "omitted"},
            "qemu": {
                "source_commit": "0" * 40, "source_dirty": False,
                "binary": self.artifact("qemu-placeholder", b"SYNTHETIC, NOT AN EXECUTABLE"),
                "argv": ["SYNTHETIC-NOT-A-QEMU-COMMAND", "--unit-fixture"],
            },
        }
        for role in ("kernel", "ramdisk", "system", "vendor"):
            base = self.artifact(role + ".placeholder", ("SYNTHETIC " + role).encode())
            partition = "boot_a" if role in ("kernel", "ramdisk") else role + "_a"
            self.inventory["inputs"][role] = {
                "role": role, "partition": partition, "slot": "a", "sha256": base["sha256"],
            }
            self.manifest["inputs"].append({
                "id": role, "role": role, "partition": partition, "slot": "a",
                "base": base, "base_read_only": True, "overlay": None,
            })
        common = {key: self.manifest[key] for key in ("run_id", "boot_id", "synthetic")}
        self.records = [
            dict(common, kind="launch"),
            dict(common, kind="processes", probe="/proc", exit_code=0, uptime_ns=100,
                 processes=[
                     {"pid": 1, "ppid": 0, "exe": "/init", "cmdline": ["/init", "second_stage"]},
                     {"pid": 10, "ppid": 1, "exe": "/system/bin/app_process64", "cmdline": ["zygote64"]},
                     {"pid": 20, "ppid": 10, "exe": "/system/bin/app_process64", "cmdline": ["system_server"]},
                 ]),
            dict(common, kind="properties", command=["getprop"], exit_code=0, uptime_ns=200,
                 kernel_cmdline=self.manifest["kernel_cmdline"],
                 stdout="[sys.boot_completed]: [1]\n[ro.boot.slot_suffix]: [_a]\n"),
        ]

    def artifact(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

    def save(self, sync_launch=True):
        self.manifest["inventory"] = self.artifact("inventory.json", json.dumps(self.inventory).encode())
        if sync_launch:
            for key in ("qemu", "slot", "entry_path", "inputs", "userdata", "inventory"):
                self.records[0][key] = copy.deepcopy(self.manifest[key])
        self.manifest["capture"] = self.artifact(
            "capture.jsonl", ("\n".join(json.dumps(row) for row in self.records) + "\n").encode())
        self.path.write_text(json.dumps(self.manifest))

    def check(self, expected="synthetic-consistent", sync_launch=True):
        self.save(sync_launch)
        result = verify(self.path, allow_synthetic=True)
        self.assertEqual(result["status"], expected, result)
        self.assertIs(result["stock_android_boot_pass"], False)
        self.assertIs(result["native_pass"], False)
        self.assertEqual(result["oem_boot_chain"], "not-evaluated")
        self.assertEqual(result["display"], "not-evaluated")
        self.assertEqual(result["input"], "not-evaluated")
        return result

    def test_synthetic_requires_explicit_opt_in_and_never_claims_boot(self):
        self.check()
        self.assertEqual(verify(self.path)["status"], "rejected")

    def test_dual_abi_and_32_bit_zygote_are_supported(self):
        rows = self.records[1]["processes"]
        rows.append({"pid": 11, "ppid": 1, "exe": "/system/bin/app_process32", "cmdline": ["zygote"]})
        rows[2].update(ppid=11, exe="/system/bin/app_process32")
        self.check()
        rows[:] = [row for row in rows if row["pid"] != 10]
        self.check()

    def test_hash_tampering_and_resealed_original_input_are_rejected(self):
        self.save()
        (self.root / "system.placeholder").write_bytes(b"CHANGED SYNTHETIC INPUT")
        self.assertEqual(verify(self.path, True)["status"], "rejected")
        self.manifest["inputs"][2]["base"] = self.artifact("system.placeholder", b"CHANGED SYNTHETIC INPUT")
        self.assertIn("inventory hash mismatch", self.check("rejected")["errors"][0])

    def test_capture_hash_is_checked(self):
        self.save()
        with (self.root / "capture.jsonl").open("a") as stream:
            stream.write("sys.boot_completed=1\n")
        self.assertIn("SHA-256 mismatch", verify(self.path, True)["errors"][0])

    def test_json_hash_and_parse_use_the_same_bytes_even_if_file_is_replaced(self):
        # A is a failing capture with the expected digest. B would pass, but
        # has a different digest. A two-open hash-then-parse bug accepts B.
        self.records[2]["stdout"] = "[sys.boot_completed]: [0]\n[ro.boot.slot_suffix]: [_a]\n"
        self.save()
        passing = copy.deepcopy(self.records)
        passing[2]["stdout"] = "[sys.boot_completed]: [1]\n[ro.boot.slot_suffix]: [_a]\n"
        replacement = ("\n".join(json.dumps(row) for row in passing) + "\n").encode()
        original_read = Bundle._read
        replaced = []

        def replace_after_read(bundle, name, limit=None):
            result = original_read(bundle, name, limit)
            if name == "capture.jsonl" and not replaced:
                (self.root / name).write_bytes(replacement)
                replaced.append(True)
            return result

        with mock.patch.object(Bundle, "_read", replace_after_read):
            result = verify(self.path, True)
        self.assertTrue(replaced)
        self.assertEqual(result["status"], "rejected", result)
        self.assertIn("sys.boot_completed was not observed as 1", result["errors"][0])

    def test_super_can_cover_system_vendor_with_explicit_inventory_bound_slot(self):
        self.manifest["inputs"] = self.manifest["inputs"][:2]
        logical = [{"role": role, "name": role + "_a", "slot": "a"}
                   for role in ("system", "vendor")]
        base = self.artifact("super.placeholder", b"SYNTHETIC SUPER, NOT AN LP IMAGE")
        item = {"id": "super", "role": "super", "partition": "super", "slot": "shared",
                "base": base, "base_read_only": True, "overlay": None,
                "logical_partitions": logical}
        self.manifest["inputs"].append(item)
        self.inventory["inputs"]["super"] = {
            "role": "super", "partition": "super", "slot": "shared",
            "sha256": base["sha256"], "logical_partitions": copy.deepcopy(logical),
        }
        self.check()
        logical[1]["slot"] = "b"
        self.check("rejected")
        self.inventory["inputs"]["super"]["logical_partitions"] = copy.deepcopy(logical)
        self.check("rejected")

    def whole_lun_inputs(self):
        self.direct_vendor = copy.deepcopy(self.manifest["inputs"][-1])
        self.manifest["inputs"] = self.manifest["inputs"][:2]
        declarations = [
            {"role": role, "gpt_partition": "super", "logical_partition": role + "_a", "slot": "a"}
            for role in ("system", "vendor")
        ] + [{"role": "userdata", "gpt_partition": "userdata", "slot": "shared"}]
        for number in (0, 1):
            identifier = f"lun{number}"
            base = self.artifact(identifier + ".placeholder", f"SYNTHETIC WHOLE LUN {number}".encode())
            overlay = dict(self.artifact(identifier + "-overlay.placeholder", b"SYNTHETIC OVERLAY"),
                           run_id=self.manifest["run_id"], base_sha256=base["sha256"])
            item = {"id": identifier, "role": identifier, "lun": number, "slot": "shared",
                    "base": base, "base_read_only": True, "overlay": overlay,
                    "contained_partitions": declarations if number == 0 else []}
            self.manifest["inputs"].append(item)
            self.inventory["inputs"][identifier] = {
                "role": identifier, "lun": number, "slot": "shared", "sha256": base["sha256"],
                "contained_partitions": copy.deepcopy(item["contained_partitions"]),
            }
        self.manifest["userdata"] = {"mode": "private-overlay", "input_id": "lun0"}

    def test_whole_luns_cover_selected_slot_and_userdata_without_fake_extractions(self):
        self.whole_lun_inputs()
        self.check()
        self.manifest["inputs"][2]["overlay"] = None
        self.manifest["userdata"]["mode"] = "original-read-only"
        self.check()

    def test_whole_lun_slot_userdata_alias_and_identity_conflicts_are_rejected(self):
        self.whole_lun_inputs()
        baseline = copy.deepcopy((self.manifest, self.inventory))
        for case in ("system-slot", "userdata-slot", "whole-lun-slot", "userdata-target",
                     "userdata-omitted", "base-alias", "duplicate-lun", "invalid-lun-role",
                     "duplicate-coverage", "separate-role-conflict", "fake-partition"):
            with self.subTest(case=case):
                self.manifest, self.inventory = copy.deepcopy(baseline)
                item = self.manifest["inputs"][2]
                if case in ("system-slot", "userdata-slot"):
                    item["contained_partitions"][0 if case == "system-slot" else 2]["slot"] = "b"
                elif case == "whole-lun-slot":
                    item["slot"] = "a"
                elif case == "userdata-target":
                    self.manifest["userdata"]["input_id"] = "lun1"
                elif case == "userdata-omitted":
                    self.manifest["userdata"] = {"mode": "omitted"}
                elif case == "base-alias":
                    item["overlay"].update(self.manifest["inputs"][3]["base"])
                elif case == "duplicate-lun":
                    self.manifest["inputs"].append(dict(copy.deepcopy(item), id="duplicate-lun0"))
                elif case == "invalid-lun-role":
                    item["role"] = "lun00"
                elif case == "duplicate-coverage":
                    item["contained_partitions"].append(copy.deepcopy(item["contained_partitions"][1]))
                elif case == "separate-role-conflict":
                    self.manifest["inputs"].append(self.direct_vendor)
                elif case == "fake-partition":
                    item["partition"] = "super"
                # Keep inventory declarations in sync so invalid configurations
                # fail their own checks, not merely an inventory mismatch.
                source = self.inventory["inputs"]["lun0"]
                for key in ("role", "slot", "contained_partitions"):
                    source[key] = copy.deepcopy(item[key])
                self.check("rejected")

    def test_mixed_run_boot_and_synthetic_classifications(self):
        for key, value in (("run_id", "another-run"),
                           ("boot_id", "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
                           ("synthetic", False)):
            with self.subTest(key=key):
                old = self.records[2][key]
                self.records[2][key] = value
                self.check("rejected")
                self.records[2][key] = old

    def test_missing_required_inputs_and_fields(self):
        for key in ("boot_id", "scope", "kernel_cmdline", "synthetic"):
            with self.subTest(key=key):
                value = self.manifest.pop(key)
                self.check("rejected")
                self.manifest[key] = value
        self.manifest["inputs"].pop()
        self.check("rejected")

    def test_launch_source_arguments_and_storage_cannot_disagree(self):
        self.save()
        for field, value in (("source_commit", "1" * 40),
                             ("argv", ["DIFFERENT-SYNTHETIC-COMMAND", "--test"])):
            old = self.records[0]["qemu"][field]
            self.records[0]["qemu"][field] = value
            self.check("rejected", sync_launch=False)
            self.records[0]["qemu"][field] = old
        self.records[0]["inputs"][0]["base_read_only"] = 1
        self.check("rejected", sync_launch=False)

    def test_writable_bases_and_wrong_slot_are_rejected(self):
        item = self.manifest["inputs"][0]
        item["base_read_only"] = False
        self.check("rejected")
        item["base_read_only"] = True
        item["slot"] = "b"
        self.inventory["inputs"]["kernel"]["slot"] = "b"
        self.check("rejected")

    def test_overlay_run_binding_and_base_alias(self):
        item = self.manifest["inputs"][2]
        overlay = dict(self.artifact("overlay.placeholder", b"SYNTHETIC OVERLAY"),
                       run_id=self.manifest["run_id"], base_sha256=item["base"]["sha256"])
        item["overlay"] = overlay
        self.check()
        overlay["run_id"] = "old-run"
        self.check("rejected")
        overlay.update(item["base"], run_id=self.manifest["run_id"])
        self.check("rejected")
        overlay.update(self.manifest["inputs"][3]["base"])
        self.check("rejected")

    def test_userdata_source_must_be_explicit_and_private(self):
        self.manifest["userdata"] = {"mode": "fresh-image", "run_id": self.manifest["run_id"],
                                     "image": self.artifact("data.placeholder", b"SYNTHETIC EMPTY DATA")}
        self.check()
        self.manifest["userdata"]["image"] = self.manifest["inputs"][0]["base"]
        self.check("rejected")
        self.manifest["userdata"] = {"mode": "private-overlay"}
        self.check("rejected")

    def test_legacy_native8_ci_and_kernel_only_are_not_stock_boot(self):
        # Existing RAM observer ABI from tests/test_guest_result.py; not Android.
        legacy = {"magic": 0x3153455243464254, "version": 1, "structure_size": 3120,
                  "completed": 1, "checks_completed": 255, "pass_mask": 255, "failures": 0}
        self.path.write_text(json.dumps(legacy))
        self.assertEqual(verify(self.path, True)["status"], "rejected")
        self.manifest["evidence_kind"] = "ci-built-only"
        self.check("rejected")
        self.manifest["evidence_kind"] = "guest-runtime"
        self.records[1]["processes"] = [self.records[1]["processes"][0]]
        self.check("rejected")

    def test_boot_completed_text_is_not_a_successful_getprop_capture(self):
        properties = self.records[2]
        for key, value in (("stdout", "sys.boot_completed=1\n"),
                           ("stdout", "[sys.boot_completed]: [0]\n"),
                           ("command", ["echo", "[sys.boot_completed]: [1]"]),
                           ("exit_code", 1), ("exit_code", False),
                           ("uptime_ns", 1), ("kernel_cmdline", "another boot")):
            with self.subTest(key=key, value=value):
                old = properties[key]
                properties[key] = value
                self.check("rejected")
                properties[key] = old
        properties["stdout"] += "[sys.boot_completed]: [1]\n"
        self.check("rejected")

    def test_android_process_ancestry_and_guest_slot_are_required(self):
        self.records[1]["processes"][2]["ppid"] = 1
        self.check("rejected")
        self.records[1]["processes"][2]["ppid"] = 10
        self.records[2]["stdout"] = "[sys.boot_completed]: [1]\n[ro.boot.slot_suffix]: [_b]\n"
        self.check("rejected")

    def test_dirty_source_needs_the_actual_patch_artifact(self):
        self.manifest["qemu"]["source_dirty"] = True
        self.check("rejected")
        self.manifest["qemu"]["source_diff"] = self.artifact("source.patch", b"SYNTHETIC PATCH ONLY")
        self.check()

    def test_path_escape_symlink_and_nonregular_files_are_rejected(self):
        base = self.manifest["inputs"][0]["base"]
        old = base["path"]
        (self.root / "link").symlink_to(self.root / old)
        (self.root / "directory").mkdir()
        for name in ("../outside", str(self.root / old), "link", "directory"):
            with self.subTest(path=name):
                base["path"] = name
                self.check("rejected")
        base["path"] = old
        self.save()
        alias = self.root / "manifest-link.json"
        alias.symlink_to(self.path)
        self.assertEqual(verify(alias, True)["status"], "rejected")

    def test_duplicate_json_keys_are_rejected(self):
        self.save()
        self.path.write_text(self.path.read_text()[:-1] + ', "synthetic": false}')
        self.assertIn("duplicate key", verify(self.path, True)["errors"][0])

    def test_cli_exit_status_and_classification(self):
        self.save()
        command = [sys.executable, str(SCRIPTS / "verify_stock_android_boot_evidence.py"), str(self.path)]
        for extra, code, status in (([], 1, "rejected"),
                                    (["--allow-synthetic"], 0, "synthetic-consistent")):
            result = subprocess.run(command + extra, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, code, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["status"], status)
            self.assertIs(receipt["stock_android_boot_pass"], False)
            self.assertIs(receipt["native_pass"], False)


if __name__ == "__main__":
    unittest.main()
