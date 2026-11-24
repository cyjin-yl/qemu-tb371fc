# SPDX-License-Identifier: GPL-2.0-or-later
"""Unexecuted draft: embed only stage_actual_dt_fixture in the accepted-ZIP worker."""


def stage_actual_dt_fixture(z, accepted_artifact, expected, stage_dir, receipt_path):
    """Validate and stage one CI-built fixture; never run a guest or toolchain.

    z is the already SHA-verified open ZipFile in root's worker. expected is
    root's exact source/run/artifact/binary/runner and ordered two-suite plan.
    This function does not repeat the outer whole-ZIP or component-stream audit.
    It verifies the fixture's actual bytes before creating a fresh host stage.
    """
    import hashlib
    import json
    from pathlib import Path, PurePosixPath
    import re
    import stat
    import struct
    from types import SimpleNamespace
    import zlib

    DT_SHA = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"

    MODEL = "Qualcomm Technologies, Inc. kona QRD"

    DT_RECEIPT_SHA = "d7c70cfcdc7ac0d19df25feb22c3431d047129a85d03b4f306e97f5bd1f08e28"

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

    def sha(path):
        return digest(path.read_bytes())

    def reject(condition, message):
        if not condition:
            raise ValueError("actual-DT fixture stage: " + message)

    def regular_output_parent(path):
        for item in (path.parent, *path.parent.parents):
            reject(item.is_dir() and not item.is_symlink(), "output parent must be an existing real directory")
        reject(not path.exists() and not path.is_symlink(), "use fresh output paths")

    # Require root's current exact acceptance context, not an artifact label.
    reject(type(expected) is dict and type(accepted_artifact) is dict, "missing acceptance context")
    reject(re.fullmatch(r"[0-9a-f]{40}", expected["source_commit"]) is not None, "exact source required")
    for key in ("qemu_sha256", "runner_sha256", "artifact_sha256"):
        reject(re.fullmatch(r"[0-9a-f]{64}", expected[key]) is not None, "exact " + key + " required")
    for key in ("workflow_run", "artifact_id"):
        reject(type(expected[key]) is int and expected[key] > 0, "invalid " + key)
    for artifact_key, expected_key in (("source", "source_commit"), ("workflow_run", "workflow_run"),
                                      ("artifact_id", "artifact_id"), ("artifact_sha256", "artifact_sha256"),
                                      ("qemu_sha256", "qemu_sha256")):
        equal(accepted_artifact[artifact_key], expected[expected_key], "outer artifact identity mismatch")
    order = expected["expected_gate_order"]
    reject(type(order) is dict and set(order) == {"components", "linux"}, "exact two-suite plan required")
    equal(order["linux"], list(LINUX_GATES), "four original Linux gates changed")
    for suite, names in order.items():
        reject(type(names) is list and names and all(type(name) is str and name for name in names)
               and len(names) == len(set(names)), "invalid ordered plan: " + suite)
    suites = accepted_artifact["suites"]
    reject(type(suites) is list and len(suites) == 2 and {s["suite"] for s in suites} == set(order),
           "two completed acceptance suites required")
    for suite in suites:
        for key, value in {
                "passed": True, "qemu_unchanged": True, "source_dirty": False,
                "source_commit": expected["source_commit"], "qemu_sha256": expected["qemu_sha256"],
                "runner_sha256": expected["runner_sha256"], "gate_order": order[suite["suite"]],
                "passed_gates": len(order[suite["suite"]]), "expected_gates": len(order[suite["suite"]]),
        }.items():
            equal(suite[key], value, "outer suite acceptance mismatch: " + key)

    stage, receipt_file = Path(stage_dir).absolute(), Path(receipt_path).absolute()
    regular_output_parent(stage)
    regular_output_parent(receipt_file)
    reject(stage != receipt_file and stage not in receipt_file.parents, "receipt must be outside the new stage")
    inventory = z.infolist()
    selected = {}
    wanted = {PREFIX + name for name in STAGED_NAMES}
    for info in inventory:
        if info.filename not in wanted:
            continue
        reject(info.filename not in selected, "duplicate selected ZIP member: " + info.filename)
        parts = PurePosixPath(info.filename)
        reject(not parts.is_absolute() and ".." not in parts.parts and "\\" not in info.filename
               and "\0" not in info.filename and str(parts) == info.filename, "unsafe selected ZIP member path")
        mode = info.external_attr >> 16
        reject(not info.is_dir() and stat.S_IFMT(mode) in (0, stat.S_IFREG),
               "selected member is a symlink or special file")
        reject(not (info.flag_bits & 1), "encrypted selected member unsupported")
        selected[info.filename] = info
    equal(set(selected), wanted, "complete fixed fixture ZIP inventory required")
    sizes = {"init": 16 * 1024 * 1024, "initramfs.cpio.gz": 16 * 1024 * 1024,
             "arch-probe": PROBE_BYTES, "source-commit.txt": 128,
             "manifest.json": 4 * 1024 * 1024, "init.nm.txt": 4 * 1024 * 1024,
             "init.readelf.txt": 4 * 1024 * 1024, "init.checkpoint.objdump.txt": 1024 * 1024}
    reject(sum(info.file_size for info in selected.values()) <= 40 * 1024 * 1024, "selected ZIP members exceed total bound")
    payloads = {}
    for name in STAGED_NAMES:
        info = selected[PREFIX + name]
        reject(type(info.file_size) is int and 0 < info.file_size <= sizes[name], "ZIP member exceeds its size bound: " + name)
        raw = z.read(info)
        equal(len(raw), info.file_size, "ZIP length disagrees with actual data: " + name)
        payloads[name] = raw
    manifest = json.loads(payloads["manifest.json"])
    manifest_sha = digest(payloads["manifest.json"])
    if "manifest_sha256" in expected:
        equal(manifest_sha, expected["manifest_sha256"], "preselected manifest hash differs")
    equal(payloads["source-commit.txt"].decode("ascii").strip(), expected["source_commit"], "fixture build source differs")

    class ReadOnlyBytes:
        """A bounded in-memory member, never an absolute CI manifest path."""
        def __init__(self, data):
            self.data = data

        def read_bytes(self):
            return self.data

        def read_text(self):
            return self.data.decode("utf-8")

        def stat(self):
            return SimpleNamespace(st_size=len(self.data))

    def cpio_entry(name, data, mode, inode, rdev_major=0, rdev_minor=0):
        # Exact source-bound newc geometry, no extraction or executable code.
        filename = name.encode("utf-8") + b"\0"
        values = [inode, mode, 0, 0, 1, 0, len(data), 0, 0, rdev_major, rdev_minor, len(filename), 0]
        header = b"070701" + b"".join(f"{value:08x}".encode("ascii") for value in values)
        prefix = header + filename
        prefix += b"\0" * (-len(prefix) % 4)
        return prefix + data + b"\0" * (-len(data) % 4)

    fixture_proof = verify_fixture({"source_commit": expected["source_commit"], "manifest_sha256": manifest_sha},
                                  {name: ReadOnlyBytes(raw) for name, raw in payloads.items()},
                                  manifest, SimpleNamespace(cpio_entry=cpio_entry))
    # Nothing above creates an output or invokes any supplied code. Only now
    # write the validated fixed member names into a fresh host directory.
    stage.mkdir(mode=0o755)
    member_dir = stage / "tb371fc-results" / "full-dtb-user-fixture"
    member_dir.mkdir(parents=True)
    records = []
    for name in STAGED_NAMES:
        member, raw = PREFIX + name, payloads[name]
        destination = member_dir / name
        with destination.open("xb") as stream:
            stream.write(raw)
        destination.chmod(0o444)
        equal(destination.read_bytes(), raw, "written stage bytes differ: " + name)
        records.append({"member": member, "relative_path": member, "bytes": len(raw), "sha256": digest(raw)})
    receipt = {
        "schema": "tb371fc-accepted-full-dtb-fixture-stage-v1", "source_commit": expected["source_commit"],
        "workflow_run": expected["workflow_run"], "artifact_id": expected["artifact_id"],
        "artifact_sha256": expected["artifact_sha256"], "qemu_sha256": expected["qemu_sha256"],
        "runner_sha256": expected["runner_sha256"], "manifest_member": PREFIX + "manifest.json",
        "manifest_sha256": manifest_sha, "staged_from_verified_zip": True,
        "binary_source_audit_passed": True, "files": records,
    }
    receipt_raw = (json.dumps(receipt, indent=2) + "\n").encode("utf-8")
    with receipt_file.open("xb") as stream:
        stream.write(receipt_raw)
    receipt_file.chmod(0o444)
    equal(receipt_file.read_bytes(), receipt_raw, "written stage receipt differs")
    return {"fixture_stage": str(stage), "fixture_stage_receipt": str(receipt_file),
            "fixture_stage_receipt_sha256": digest(receipt_raw), "manifest_sha256": manifest_sha,
            "receipt": receipt, "fixture_proof": fixture_proof,
            "validated_selected_zip_members": len(records), "fixture_build_completed": True,
            "whole_zip_and_binary_audit_reused_from_outer_worker": True,
            "whole_zip_sha256_recomputed_by_helper": False,
            "supplied_python_module_imported": False, "toolchain_or_subprocess_invoked": False,
            "qemu_or_guest_executed": False, "userspace_passed": None, "android_boot_passed": None}
