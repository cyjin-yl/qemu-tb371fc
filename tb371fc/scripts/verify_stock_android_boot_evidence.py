#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Check an offline stock Android evidence bundle; never declare a boot pass."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid


MANIFEST_KIND = "tb371fc-stock-android-boot-evidence"
INVENTORY_KIND = "tb371fc-stock-input-inventory"
MAX_JSON_BYTES = 16 * 1024 * 1024
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


class EvidenceError(ValueError):
    """Missing, malformed, or inconsistent evidence."""


def require(condition, message):
    if not condition:
        raise EvidenceError(message)


def object_value(value, label):
    require(isinstance(value, dict), f"{label}: expected an object")
    return value


def text_value(value, label):
    require(isinstance(value, str) and bool(value.strip()),
            f"{label}: expected a nonempty string")
    return value


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"JSON: duplicate key {key!r}")
        result[key] = value
    return result


def parse_json(raw):
    def bad_constant(value):
        raise EvidenceError(f"JSON: invalid constant {value}")
    return json.loads(raw, object_pairs_hook=no_duplicate_keys,
                      parse_constant=bad_constant)


def same_json(left, right):
    return (json.dumps(left, sort_keys=True, allow_nan=False) ==
            json.dumps(right, sort_keys=True, allow_nan=False))


class Bundle:
    def __init__(self, root):
        self.root = root

    def file(self, name):
        name = text_value(name, "artifact path")
        path = Path(name)
        require(not path.is_absolute() and ".." not in path.parts,
                "artifact path must stay within the bundle")
        current = self.root
        for part in path.parts:
            current = current / part
            require(not current.is_symlink(), "artifact symlinks are not allowed")
        require(stat.S_ISREG(current.stat().st_mode),
                "artifact must be a regular file")
        return current

    def _read(self, name, limit=None):
        path = self.file(name)
        before = path.stat()

        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size,
                    info.st_mtime_ns, info.st_ctime_ns)

        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            require(stat.S_ISREG(opened.st_mode) and identity(before) == identity(opened),
                    "artifact identity changed before reading")
            digest = hashlib.sha256()
            raw = None
            if limit is not None:
                raw = stream.read(limit + 1)
                require(len(raw) <= limit, "JSON evidence exceeds 16 MiB")
                digest.update(raw)
            else:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            require(identity(opened) == identity(os.fstat(stream.fileno())) and
                    identity(opened) == identity(self.file(name).stat()),
                    "artifact changed while reading")
        return path, digest.hexdigest(), raw

    def artifact(self, value, label, *, text=False):
        value = object_value(value, label)
        expected = value.get("sha256")
        require(isinstance(expected, str) and SHA256.fullmatch(expected),
                f"{label}: expected a lowercase SHA-256")
        path, digest, raw = self._read(value.get("path"), MAX_JSON_BYTES if text else None)
        require(digest == expected, f"{label}: SHA-256 mismatch")
        return raw.decode("utf-8") if text else path

    def text(self, name):
        return self._read(name, MAX_JSON_BYTES)[2].decode("utf-8")


def check_bundle(manifest, bundle, allow_synthetic):
    manifest = object_value(manifest, "manifest")
    require(manifest.get("kind") == MANIFEST_KIND,
            "not a stock Android runtime evidence manifest (native8/CI is insufficient)")
    require(type(manifest.get("schema_version")) is int and
            manifest["schema_version"] == 1, "unsupported schema_version")
    synthetic = manifest.get("synthetic")
    require(type(synthetic) is bool, "synthetic must be explicitly true or false")
    require(not synthetic or allow_synthetic,
            "synthetic evidence is rejected; unit tests may use --allow-synthetic")
    run_id = text_value(manifest.get("run_id"), "run_id")
    require(not synthetic or run_id.startswith("synthetic-"),
            "synthetic run_id must start with synthetic-")
    require(synthetic or not run_id.startswith("synthetic-"),
            "a synthetic run_id cannot be declared real")
    boot_id = text_value(manifest.get("boot_id"), "boot_id")
    require(str(uuid.UUID(boot_id)) == boot_id, "boot_id must be a canonical UUID")
    require(manifest.get("scope") == "android-framework" and
            manifest.get("evidence_kind") == "guest-runtime",
            "only Android framework runtime evidence is supported")
    require(manifest.get("entry_path") in ("direct-kernel", "bootloader"),
            "entry_path must explicitly identify direct-kernel or bootloader")
    slot = manifest.get("slot")
    require(slot in ("a", "b", "none"), "slot must be a, b, or none")
    text_value(manifest.get("kernel_cmdline"), "kernel_cmdline")

    inventory = object_value(parse_json(bundle.artifact(
        manifest.get("inventory"), "inventory", text=True)), "inventory")
    require(inventory.get("kind") == INVENTORY_KIND and
            inventory.get("synthetic") is synthetic,
            "inventory kind/synthetic classification mismatch")
    text_value(inventory.get("backup_id"), "inventory.backup_id")
    original = object_value(inventory.get("inputs"), "inventory.inputs")
    inputs = manifest.get("inputs")
    require(isinstance(inputs, list) and bool(inputs), "inputs must be a nonempty list")
    by_id, by_role, covered_roles, lun_numbers = {}, {}, {}, set()
    for item in inputs:
        item = object_value(item, "input")
        identifier = text_value(item.get("id"), "input.id")
        role = text_value(item.get("role"), "input.role")
        require(identifier not in by_id and role not in by_role,
                "input IDs and roles must be unique")
        source = object_value(original.get(identifier), f"inventory input {identifier}")
        is_lun = (role.startswith("lun") or "lun" in item or "lun" in source or
                  "contained_partitions" in item or "contained_partitions" in source)
        for field in (("role", "slot") if is_lun else ("role", "partition", "slot")):
            text_value(item.get(field), f"input.{field}")
            require(item[field] == source.get(field), f"input {identifier}: inventory {field} mismatch")
        if is_lun:
            lun = item.get("lun")
            require(type(lun) is int and lun >= 0 and role == f"lun{lun}" and
                    type(source.get("lun")) is int and source["lun"] == lun and
                    lun not in lun_numbers, "invalid or duplicate whole-LUN number/role")
            require(item["slot"] == "shared" and "partition" not in item and
                    "partition" not in source, "whole LUNs are shared, not partition inputs")
            lun_numbers.add(lun)
        if is_lun or role == "super":
            field = "contained_partitions" if is_lun else "logical_partitions"
            declarations = item.get(field)
            require(isinstance(declarations, list) and
                    same_json(declarations, source.get(field)),
                    f"{field} must match the original inventory")
            for partition in declarations:
                partition = object_value(partition, "contained partition")
                covered_role = text_value(partition.get("role"), "contained partition role")
                names = [text_value(partition.get("gpt_partition" if is_lun else "name"),
                                    "contained partition name")]
                if is_lun and partition.get("logical_partition") is not None:
                    names.append(text_value(partition["logical_partition"], "logical partition name"))
                allowed_slots = (slot, "shared") if is_lun and covered_role == "userdata" else (slot,)
                require(covered_role not in covered_roles and partition.get("slot") in allowed_slots,
                        "duplicate contained role or mixed contained slots")
                for name in names:
                    if name.endswith(("_a", "_b")):
                        require(name[-1] == partition["slot"], "contained partition name contradicts slot")
                covered_roles[covered_role] = identifier
        require(item["slot"] in (slot, "shared"), f"input {identifier}: mixed slots")
        if not is_lun and item["partition"].endswith(("_a", "_b")):
            require(item["partition"][-1] == item["slot"],
                    f"input {identifier}: partition suffix contradicts slot")
        base = object_value(item.get("base"), "input.base")
        require(base.get("sha256") == source.get("sha256"),
                f"input {identifier}: original inventory hash mismatch")
        base_path = bundle.artifact(base, f"input {identifier}")
        require(item.get("base_read_only") is True,
                f"input {identifier}: base_read_only must be true")
        require("overlay" in item, f"input {identifier}: overlay must be explicit (null or object)")
        overlay = item["overlay"]
        if overlay is not None:
            overlay = object_value(overlay, "overlay")
            require(role not in ("kernel", "ramdisk", "dtb"),
                    "kernel/ramdisk/DTB inputs cannot be overlays")
            require(overlay.get("run_id") == run_id and
                    overlay.get("base_sha256") == base["sha256"],
                    f"input {identifier}: overlay run/base binding mismatch")
            overlay_path = bundle.artifact(overlay, f"overlay {identifier}")
            require(not overlay_path.samefile(base_path), "overlay must not alias its base")
        by_id[identifier], by_role[role] = item, item
    require(not covered_roles.keys() & by_role.keys(), "contained and separate input roles overlap")
    require({"kernel", "ramdisk"} <= by_role.keys() and
            {"system", "vendor"} <= (by_role.keys() | covered_roles.keys()),
            "original kernel/ramdisk and system/vendor (separate, super or whole LUN) are required")
    bases = [bundle.file(item["base"]["path"]) for item in inputs]
    for item in inputs:
        if item["overlay"] is not None:
            overlay_path = bundle.file(item["overlay"]["path"])
            require(all(not overlay_path.samefile(base) for base in bases),
                    "overlay must not alias any original base")

    userdata = object_value(manifest.get("userdata"), "userdata")
    mode = userdata.get("mode")
    require(mode in ("omitted", "fresh-image", "original-read-only", "private-overlay"),
            "userdata.mode must describe the actual data source")
    if mode in ("omitted", "fresh-image"):
        require("userdata" not in by_role and "userdata" not in covered_roles,
                "userdata mode contradicts original inputs")
        if mode == "fresh-image":
            require(userdata.get("run_id") == run_id, "fresh userdata belongs to another run")
            fresh = bundle.artifact(userdata.get("image"), "fresh userdata")
            require(all(not fresh.samefile(bundle.file(i["base"]["path"])) for i in inputs),
                    "fresh userdata must not alias an original input")
    else:
        data_input = by_role.get("userdata")
        if data_input is None:
            data_id = userdata.get("input_id")
            require(isinstance(data_id, str) and data_id in by_id and
                    covered_roles.get("userdata") == data_id and "lun" in by_id[data_id],
                    "userdata.input_id must select its containing LUN")
            data_input = by_id[data_id]
        require(userdata.get("input_id", data_input["id"]) == data_input["id"],
                "userdata.input_id contradicts the original userdata input")
        require((data_input["overlay"] is not None) == (mode == "private-overlay"),
                "userdata mode contradicts its overlay declaration")

    qemu = object_value(manifest.get("qemu"), "qemu")
    revision = qemu.get("source_commit")
    require(isinstance(revision, str) and COMMIT.fullmatch(revision),
            "qemu.source_commit must be a full Git commit")
    require(type(qemu.get("source_dirty")) is bool, "qemu.source_dirty must be explicit")
    if qemu["source_dirty"]:
        bundle.artifact(qemu.get("source_diff"), "qemu source diff")
    else:
        require(qemu.get("source_diff") is None, "clean QEMU source cannot have a diff")
    bundle.artifact(qemu.get("binary"), "qemu binary")
    argv = qemu.get("argv")
    require(isinstance(argv, list) and len(argv) > 1 and
            all(isinstance(arg, str) and arg and "\0" not in arg for arg in argv),
            "qemu.argv must contain the actual argument vector, not a shell command")

    capture_text = bundle.artifact(manifest.get("capture"), "capture", text=True)
    records = [object_value(parse_json(line), "capture record")
               for line in capture_text.splitlines() if line.strip()]
    require([r.get("kind") for r in records] == ["launch", "processes", "properties"],
            "capture needs launch, process snapshot, and successful getprop records in order")
    for record in records:
        require(record.get("run_id") == run_id and record.get("boot_id") == boot_id and
                record.get("synthetic") is synthetic,
                "capture record belongs to another run/boot/classification")
    launch, processes, properties = records
    for field in ("qemu", "slot", "entry_path", "inputs", "userdata", "inventory"):
        require(same_json(launch.get(field), manifest[field]),
                f"launch {field} differs from manifest")
    for record in (processes, properties):
        require(type(record.get("exit_code")) is int and record["exit_code"] == 0,
                "guest observation command did not succeed")
        require(type(record.get("uptime_ns")) is int and record["uptime_ns"] > 0,
                "guest observation needs a positive captured uptime_ns")
    require(processes["uptime_ns"] <= properties["uptime_ns"],
            "guest observation uptime went backwards")
    require(processes.get("probe") == "/proc", "process evidence must be a /proc snapshot")
    rows = processes.get("processes")
    require(isinstance(rows, list), "processes must be a list")
    pids = {}
    for row in rows:
        row = object_value(row, "process")
        pid, ppid = row.get("pid"), row.get("ppid")
        require(type(pid) is int and pid > 0 and pid not in pids and
                type(ppid) is int and ppid >= 0, "invalid or duplicate process PID")
        text_value(row.get("exe"), "process.exe")
        args = row.get("cmdline")
        require(isinstance(args, list) and args and
                all(isinstance(arg, str) for arg in args), "process cmdline must be an argv list")
        pids[pid] = row
    init = pids.get(1, {})
    require(init.get("ppid") == 0 and init.get("exe") in ("/init", "/system/bin/init") and
            init.get("cmdline", [None])[0] in ("/init", "/system/bin/init"),
            "Android PID 1 init evidence is missing")
    app_process = ("/system/bin/app_process", "/system/bin/app_process32", "/system/bin/app_process64")
    zygotes = {pid for pid, row in pids.items() if row["ppid"] == 1 and
               row["exe"] in app_process and row["cmdline"][0] in ("zygote", "zygote32", "zygote64")}
    require(bool(zygotes), "Android zygote process evidence is missing")
    require(any(row["ppid"] in zygotes and row["exe"] in app_process and
                row["cmdline"][0] == "system_server" for row in pids.values()),
            "system_server must be observed as a child of a captured zygote")
    require(properties.get("command") in (["getprop"], ["/system/bin/getprop"]),
            "properties must record a successful getprop command")
    require(properties.get("kernel_cmdline") == manifest["kernel_cmdline"],
            "captured kernel command line differs from manifest")
    # Parse the command's stdout, never a substring in a console/CI log.
    stdout = text_value(properties.get("stdout"), "getprop stdout")
    values = {}
    for line in stdout.splitlines():
        match = re.fullmatch(r"\[([^\]\r\n]+)\]: \[([^\r\n]*)\]", line)
        require(match is not None, "invalid getprop stdout record")
        name, value = match.groups()
        require(name not in values, "duplicate property in getprop stdout")
        values[name] = value
    require(values.get("sys.boot_completed") == "1", "sys.boot_completed was not observed as 1")
    require(values.get("ro.boot.slot_suffix", "") == ("" if slot == "none" else "_" + slot),
            "guest slot suffix differs from input slot")
    return synthetic


def verify(manifest_path, allow_synthetic=False):
    result = {
        "scope": "offline-artifact-consistency", "status": "rejected",
        "stock_android_boot_pass": False, "native_pass": False,
        "oem_boot_chain": "not-evaluated", "display": "not-evaluated",
        "input": "not-evaluated", "errors": [],
    }
    try:
        path = Path(manifest_path).absolute()
        # Check the manifest and every ancestor before reading any bundle data.
        require(not any(p.is_symlink() for p in (path, *path.parents)),
                "manifest path must not contain symlinks")
        bundle = Bundle(path.parent)
        manifest = parse_json(bundle.text(path.name))
        synthetic = check_bundle(manifest, bundle, allow_synthetic)
        result.update(status="synthetic-consistent" if synthetic else "consistent",
                      run_id=manifest["run_id"], boot_id=manifest["boot_id"],
                      entry_path=manifest["entry_path"], synthetic=synthetic,
                      android_framework_evidence="present-in-offline-artifacts")
    except (OSError, ValueError, TypeError) as error:
        result["errors"].append(str(error))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--allow-synthetic", action="store_true",
                        help="unit-test fixtures only; cannot produce a boot pass")
    args = parser.parse_args()
    result = verify(args.manifest, args.allow_synthetic)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 1 if result["status"] == "rejected" else 0


if __name__ == "__main__":
    sys.exit(main())
