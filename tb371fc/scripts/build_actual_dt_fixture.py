#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build, but never execute, the dedicated full-DTB eight-check fixture.

This is a public CI build artifact. It does not load a production Image/DTB,
start QEMU, execute the guest ELF, alter a guest record, or attest Android boot.
The model identity receipt is a separate read-only observation of the exact
DTB. The original guest, validators and four Linux boot gates remain unchanged.
"""
from __future__ import annotations

import argparse
import ast
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys

sys.dont_write_bytecode = True

EXPECTED_MODEL = "Qualcomm Technologies, Inc. kona QRD"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
# Independently observed root-model identity for the exact production DTB.
# The builder verifies this public receipt; it never reads a private DTB.
DT_IDENTITY_RECEIPT_SHA256 = "d7c70cfcdc7ac0d19df25feb22c3431d047129a85d03b4f306e97f5bd1f08e28"
ARCH_PROBE_SHA256 = "0ed51cb6c6dd24685beb3f77056d17d47c5c2f697df96d87eef4a678d4c5cb1b"
ARCH_PROBE_BYTES = 2264
CHECKPOINT = 0x08000000
RESULT_BYTES = 3120
COMPILE_FLAGS = ["-static", "-fno-pie", "-no-pie", "-g", "-Os", "-Wall", "-Wextra", "-Werror",
                 "-Wl,--section-start=.tb371fc_checkpoint=0x8000000"]
PINNED_INPUTS = {
    "tests/guest-init.c": "4f1aac3a68e6c660abebdc04f239191a46ee1330ffb47a494ee37d6081d38a79",
    "tests/guest-init-actual-dt.c": "4c884da703dcde0fc33912ac8e0484422984b82f592fbd89e5f8f845ee7d9840",
    "tests/guest-result.h": "0087f4feaaddb78762685fc2810e010a0edee9cb23386bff62584e5f1af133c3",
    "tests/arch-probe.S": "b976d2cc83c8782d0832c27f42df3491459d01d4dd164c97e10b50d90147125f",
    "scripts/boot_smoke_gdb.py": "93e046b915b71cd74198e8dd518564c4c0e7ea85b75a29966f08c42a8897b737",
    "scripts/boot_smoke.py": "4889f2cdd2e3fd3331cc57f3bf1a6b6ed8894f87503283c7f404766919540f7d",
    "scripts/boot_verdict.py": "e0505903c5f52986a8650ffa38933db183501bca416e9876fac4560b08537cdf",
    "scripts/boot_verdict_actual_dt.py": "2e1a7d8d7e60abbede54385cba11ed1e50067b5f209ab119d15739e537b989ef",
    "scripts/build_arch_probe.sh": "70b27ae44f78e0f7d4479ae51d72de53fe7c191bfee0c6936ebda1b0612bbf2c",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def identity(path):
    raw = path.read_bytes()
    return {"path": str(path), "bytes": len(raw), "sha256": digest(raw)}


def function_text(source, name):
    nodes = [node for node in ast.parse(source).body
             if isinstance(node, ast.FunctionDef) and node.name == name]
    require(len(nodes) == 1, "missing/duplicate source function " + name)
    return "".join(source.splitlines(keepends=True)[nodes[0].lineno - 1:nodes[0].end_lineno])


def inspect_elf(raw):
    """Decode the actual little-endian ELF64 file, without external libraries."""
    require(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01", "not ELF64 little-endian version1")
    header = struct.unpack_from("<HHIQQQIHHHHHH", raw, 16)
    etype, machine, version, entry, phoff, shoff, flags, ehsize, phsize, phnum, shsize, shnum, shstr = header
    require(etype == 2 and machine == 183 and version == 1, "ELF is not ET_EXEC/AArch64")
    require(ehsize == 64 and phsize == 56 and shsize == 64, "unexpected ELF header widths")
    require(0 < phnum < 0xffff and 0 < shnum and 0 <= shstr < shnum, "unsupported extended/empty ELF tables")
    require(phoff + phsize * phnum <= len(raw) and shoff + shsize * shnum <= len(raw), "ELF tables exceed file")
    programs = []
    for i in range(phnum):
        values = struct.unpack_from("<II6Q", raw, phoff + i * phsize)
        item = dict(zip(("type", "flags", "file_offset", "virtual_address", "physical_address",
                         "file_bytes", "memory_bytes", "alignment"), values))
        require(item["type"] != 3, "PT_INTERP present")
        require(item["file_offset"] + item["file_bytes"] <= len(raw), "program data exceeds ELF")
        if item["type"] == 1:
            require(item["file_bytes"] <= item["memory_bytes"], "PT_LOAD file size exceeds memory size")
        programs.append(item)
    sections = [struct.unpack_from("<II4QII2Q", raw, shoff + i * shsize) for i in range(shnum)]
    names = sections[shstr]
    require(names[1] == 3 and names[4] + names[5] <= len(raw), "invalid section-name string table")
    name_bytes = raw[names[4]:names[4] + names[5]]
    named = []
    for item in sections:
        require(item[0] < len(name_bytes), "section name outside string table")
        end = name_bytes.find(b"\0", item[0])
        require(end >= 0, "unterminated ELF section name")
        named.append({"name": name_bytes[item[0]:end].decode("ascii"), "type": item[1],
                      "flags": item[2], "virtual_address": item[3], "file_offset": item[4],
                      "bytes": item[5], "alignment": item[8]})
    return {"class": "ELF64", "endianness": "little", "elf_type": "ET_EXEC",
            "machine": "AArch64", "e_machine": machine, "entry_address": entry,
            "program_headers": programs, "sections": named, "interpreter_present": False,
            "elf_flags": flags}


def parse_symbols(text):
    found = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 4 or fields[3] not in ("tb371fc_guest_result", "tb371fc_guest_checkpoint"):
            continue
        name = fields[3]
        require(name not in found, "duplicate required ELF symbol")
        found[name] = {"address": int(fields[0], 16), "size": int(fields[1], 16), "type": fields[2]}
    require(set(found) == {"tb371fc_guest_result", "tb371fc_guest_checkpoint"}, "missing result/checkpoint symbols")
    result, checkpoint = found["tb371fc_guest_result"], found["tb371fc_guest_checkpoint"]
    require(result["size"] == RESULT_BYTES and result["address"] > 0 and result["type"] in ("D", "B"),
            "wrong guest RAM result symbol")
    require(checkpoint == {"address": CHECKPOINT, "size": 4, "type": "T"}, "wrong fixed checkpoint symbol")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--dt-identity-receipt", type=Path, required=True)
    parser.add_argument("--cc", default="aarch64-linux-gnu-gcc")
    parser.add_argument("--nm", default="aarch64-linux-gnu-nm")
    parser.add_argument("--readelf", default="aarch64-linux-gnu-readelf")
    parser.add_argument("--objdump", default="aarch64-linux-gnu-objdump")
    parser.add_argument("--cross-prefix", default="aarch64-linux-gnu-")
    args = parser.parse_args()
    root, out = Path(__file__).resolve().parents[1], args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {
        "schema": "tb371fc-full-dtb-user-fixture-v1", "build_completed": False,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Build-only eight-check initramfs for a separately identified production DT; no guest execution",
        "target_dt": {"sha256": DTB_SHA256, "exact_model": EXPECTED_MODEL,
                      "dtb_read_by_builder": False, "model_measured_by_builder": False},
        "execution": {"performed": False, "userspace_passed": None,
                      "guest_shutdown_observed": None, "android_boot_passed": None},
        "commands": [], "inputs": {}, "toolchain": {},
    }
    import os
    environment = dict(os.environ, LC_ALL="C", TB371FC_CROSS_PREFIX=args.cross_prefix)

    def run(command, log_name):
        command = [str(value) for value in command]
        report["commands"].append({"argv": command, "log": log_name})
        with (out / log_name).open("xb") as log:
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT,
                           env=environment, cwd=root.parent)
        return (out / log_name).read_text(encoding="utf-8", errors="strict")

    def tool(label, executable):
        resolved = shutil.which(executable)
        require(resolved is not None, "missing tool " + executable)
        path = Path(resolved).resolve()
        version = run([path, "--version"], "tool-" + label + ".log")
        report["toolchain"][label] = {**identity(path), "requested": executable,
                                      "version_text": version}
        return str(path)

    try:
        require(re.fullmatch(r"[0-9a-f]{64}", DT_IDENTITY_RECEIPT_SHA256) is not None,
                "actual-DTB root-model identity receipt has not been approved")
        receipt = args.dt_identity_receipt.resolve()
        require(identity(receipt)["sha256"] == DT_IDENTITY_RECEIPT_SHA256,
                "actual-DTB identity receipt differs from the reviewed input")
        report["target_dt"]["identity_receipt"] = identity(receipt)
        for relative, expected in PINNED_INPUTS.items():
            actual = identity(root / relative)
            require(actual["sha256"] == expected, "pinned fixture input changed: " + relative)
            report["inputs"][relative] = actual
        original = (root / "tests/guest-init.c").read_bytes()
        variant = (root / "tests/guest-init-actual-dt.c").read_bytes()
        old = b'          strstr(buf, "TB371FC"), "board_model");'
        new = b'          !strcmp(buf, "Qualcomm Technologies, Inc. kona QRD"), "board_model");'
        require(original.count(old) == 1 and variant == original.replace(old, new),
                "guest variant differs outside the single exact model predicate")
        validator = (root / "scripts/boot_verdict_actual_dt.py").read_text()
        old_validator = (root / "scripts/boot_smoke_gdb.py").read_text()
        require(function_text(validator, "decode_record") == function_text(old_validator, "decode_record"),
                "RAM-record decoder changed")
        old_rule = ('    if "TB371FC" not in record.get("model", ""):\n'
                    '        errors.append("device tree model does not identify TB371FC")')
        new_rule = ('    if record.get("model") != EXPECTED_MODEL:\n'
                    '        errors.append(f"device tree model does not equal {EXPECTED_MODEL!r}")')
        actual_rule = function_text(validator, "validate_record")
        require(actual_rule.count(new_rule) == 1 and actual_rule.replace(new_rule, old_rule) ==
                function_text(old_validator, "validate_record"), "non-model validation logic changed")
        native_path = root / "evidence/android-arch-probe.json"
        native = json.loads(native_path.read_text())
        require(native["elf_sha256"] == ARCH_PROBE_SHA256 and native["elf_size"] == ARCH_PROBE_BYTES and
                native["source_sha256"] == PINNED_INPUTS["tests/arch-probe.S"] and
                native["probe_result"]["counter_frequency_hz"] == 19200000 and
                native["probe_result"]["loop_iterations"] == 65536, "native probe identity changed")
        report["inputs"]["evidence/android-arch-probe.json"] = identity(native_path)
        report["inputs"]["scripts/build_actual_dt_fixture.py"] = identity(Path(__file__).resolve())
        report["source_commit"] = run(["git", "rev-parse", "HEAD"], "source-commit.txt").strip()
        require(re.fullmatch(r"[0-9a-f]{40}", report["source_commit"]) is not None, "missing exact source commit")
        cc, nm = tool("cc", args.cc), tool("nm", args.nm)
        readelf, objdump = tool("readelf", args.readelf), tool("objdump", args.objdump)
        tool("probe-as", args.cross_prefix + "as")
        tool("probe-ld", args.cross_prefix + "ld")
        cc_ld = run([cc, "-print-prog-name=ld"], "cc-linker-path.txt").strip()
        tool("cc-linker", cc_ld)
        init, probe, initrd = out / "init", out / "arch-probe", out / "initramfs.cpio.gz"
        compile_command = [cc, *COMPILE_FLAGS, root / "tests/guest-init-actual-dt.c", "-o", init]
        run(compile_command, "compile.log")
        run(["sh", root / "scripts/build_arch_probe.sh", probe], "arch-probe-build.log")
        require(probe.stat().st_size == ARCH_PROBE_BYTES and identity(probe)["sha256"] == ARCH_PROBE_SHA256,
                "built arch-probe is not the native-tested ELF")
        probe_elf = inspect_elf(probe.read_bytes())
        nm_text = run([nm, "-S", "--defined-only", init], "init.nm.txt")
        readelf_text = run([readelf, "-h", "-l", "-S", "-W", init], "init.readelf.txt")
        disassembly = run([objdump, "-d", "-j", ".tb371fc_checkpoint", init], "init.checkpoint.objdump.txt")
        require(re.search(r"Type:\s+EXEC\b", readelf_text) is not None and
                re.search(r"Machine:\s+AArch64\b", readelf_text) is not None and
                re.search(r"^\s*INTERP\s", readelf_text, re.MULTILINE) is None,
                "readelf does not show ET_EXEC/AArch64 without INTERP")
        symbols = parse_symbols(nm_text)
        raw_init = init.read_bytes()
        elf = inspect_elf(raw_init)
        sections = [section for section in elf["sections"] if section["name"] == ".tb371fc_checkpoint"]
        require(len(sections) == 1, "missing/duplicate checkpoint section")
        checkpoint_section = sections[0]
        require(checkpoint_section["type"] == 1 and checkpoint_section["virtual_address"] == CHECKPOINT and
                checkpoint_section["bytes"] == 4, "checkpoint section geometry differs")
        start = checkpoint_section["file_offset"]
        checkpoint_bytes = raw_init[start:start + 4]
        require(checkpoint_bytes == bytes.fromhex("c0035fd6"), "checkpoint is not exactly the AArch64 RET instruction")
        instructions = re.findall(r"^\s*([0-9a-f]+):\s+([0-9a-f]{8})\s+([^\n]+)$", disassembly, re.MULTILINE)
        require(len(instructions) == 1 and int(instructions[0][0], 16) == CHECKPOINT and
                instructions[0][1] == "d65f03c0" and instructions[0][2].strip() == "ret",
                "objdump checkpoint is not the one expected RET")
        result_address = symbols["tb371fc_guest_result"]["address"]
        loads = [program for program in elf["program_headers"] if program["type"] == 1]
        require(any(p["flags"] & 1 and p["virtual_address"] <= CHECKPOINT and
                    CHECKPOINT + 4 <= p["virtual_address"] + p["file_bytes"] for p in loads),
                "checkpoint does not reside in an executable file-backed PT_LOAD")
        require(any(p["flags"] & 2 and p["virtual_address"] <= result_address and
                    result_address + RESULT_BYTES <= p["virtual_address"] + p["memory_bytes"] for p in loads),
                "result object does not reside completely in a writable PT_LOAD")
        # The original, pinned helper supplies the newc encoding, inode/order,
        # padding and device-node representation. Importing it does not run main.
        from boot_smoke import cpio_entry
        entries = [("init", raw_init, 0o100755, 1, 0, 0),
                   ("dev", b"", 0o040755, 2, 0, 0),
                   ("dev/console", b"", 0o020600, 3, 5, 1),
                   ("proc", b"", 0o040755, 4, 0, 0),
                   ("sys", b"", 0o040755, 5, 0, 0),
                   ("arch-probe", probe.read_bytes(), 0o100755, 6, 0, 0),
                   ("TRAILER!!!", b"", 0, 7, 0, 0)]
        archive = b"".join(cpio_entry(*entry) for entry in entries)
        compressed = gzip.compress(archive, mtime=0)
        with initrd.open("xb") as output:
            output.write(compressed)
        require(compressed[:2] == b"\x1f\x8b" and compressed[4:8] == bytes(4), "wrong gzip mtime/header")
        report["init"] = {**identity(init), **elf, "symbols": symbols,
                          "checkpoint_instruction_bytes": checkpoint_bytes.hex(),
                          "result_address_from_this_build": result_address,
                          "result_address_reused_from_old_fixture": False,
                          "nm_log": identity(out / "init.nm.txt"),
                          "readelf_log": identity(out / "init.readelf.txt"),
                          "objdump_log": identity(out / "init.checkpoint.objdump.txt")}
        report["arch_probe"] = {**identity(probe), "identical_to_native_tested_elf": True,
                                "elf_type": probe_elf["elf_type"], "e_machine": probe_elf["e_machine"],
                                "interpreter_present": probe_elf["interpreter_present"]}
        report["initramfs"] = {**identity(initrd), "gzip_mtime": 0,
                               "uncompressed_bytes": len(archive), "uncompressed_sha256": digest(archive),
                               "entries_including_trailer": len(entries),
                               "entries": [{"path": name, "bytes": len(payload), "mode": mode,
                                            "inode": inode, "rdev_major": major, "rdev_minor": minor,
                                            "payload_sha256": digest(payload)}
                                           for name, payload, mode, inode, major, minor in entries]}
        report["guest_contract"] = {
            "check_count": 8, "check_names": ["procfs", "sysfs", "aarch64", "eight_online_cpus_psci",
                "timer_and_scheduler_progress", "fork_and_wait", "board_model", "arch_counter_probe"],
            "model_check_bit": 6, "exact_model": EXPECTED_MODEL,
            "result_bytes": RESULT_BYTES, "magic": 0x3153455243464254, "version": 1,
            "header_bytes": 112, "probe_stdout_offset": 1072, "probe_capacity": 2048,
            "expected_checks_completed": 255, "expected_pass_mask": 255, "expected_fail_mask": 0,
            "minimum_elapsed_ns": 20000000, "expected_online_cpus": "0-7\n",
            "counter_frequency_hz": 19200000, "counter_loop_iterations": 65536,
            "guest_only_difference_is_exact_model_predicate": True,
            "validator_only_difference_is_exact_model_predicate": True,
            "guest_shutdown_requires_separate_actual_qmp_evidence": True,
            "old_four_linux_gates_modified": False,
        }
        report["build_completed"] = True
    except Exception as error:
        report["build_error"] = {"type": type(error).__name__, "message": str(error)}
    finally:
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"schema": report["schema"], "build_completed": report["build_completed"],
                      "manifest": str(out / "manifest.json"), "guest_executed": False}))
    return 0 if report["build_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
