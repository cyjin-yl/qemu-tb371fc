#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""DRAFT: fixed-fixture full-production-DT EL0 checkpoint collector.

Reuse pinned boot_smoke_gdb helpers, never its rebuilding main. This file has
only been syntax/static reviewed. Run only after root accepts the exact CI
binary and separately decides to attempt this userspace fixture.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

KERNEL_SHA = "ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f"
DT_SHA = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
INIT = Path("/work/gdb-smoke/exact-production-1/init")
INITRD = INIT.with_name("initramfs.cpio.gz")
INIT_SHA = "5c62d22ab5c7115506733e5a2bed8e8c0aa49b3b22e9ef9b9f5b8d7ec3389293"
INITRD_SHA = "88c075fc15487f3d6dc8e7cb1b61a35aa74a8f25ec6001a7d883c9623f86d7f6"
CMD_DB_SHA = "a971809c06fa1a3ff44c9d28463bc6ff53203d9375ffd0f76dc73d4c8a3ea6f9"
NATIVE_SHA = "69c592e097069a779cc54eb38b823192b7de3ef18465eb49a3da35f4d496edf8"
HELPERS = {
    "boot_smoke_gdb.py": "93e046b915b71cd74198e8dd518564c4c0e7ea85b75a29966f08c42a8897b737",
    "boot_smoke.py": "4889f2cdd2e3fd3331cc57f3bf1a6b6ed8894f87503283c7f404766919540f7d",
    "boot_qmp.py": "b0d95776470110fd682371d393ffda4e2996fb295b1d3c81b41c46980be7a54c",
    "boot_verdict.py": "e0505903c5f52986a8650ffa38933db183501bca416e9876fac4560b08537cdf",
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
PC, RECORD, RECORD_BYTES = 0x08000000, 0x004a0040, 3120


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def require(value, reason):
    if not value:
        raise ValueError(reason)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def prepare(cfg, out):
    """Validate fixed inputs; retain every baseline machine/global argument."""
    require(re.fullmatch(r"[0-9a-f]{40}", cfg["source_commit"]),
            "source_commit must name the accepted source receipt")
    for key in ("qemu_sha256", "baseline_diagnostic_sha256"):
        require(re.fullmatch(r"[0-9a-f]{64}", cfg[key]), key + " must be exact")
    diagnostic = Path(cfg["baseline_diagnostic"]).resolve()
    require(sha(diagnostic) == cfg["baseline_diagnostic_sha256"],
            "baseline diagnostic digest mismatch")
    baseline = json.loads(diagnostic.read_text())
    original = baseline["command"]
    require(type(original) is list and all(type(x) is str for x in original),
            "malformed baseline command")
    require(baseline["qemu_sha256"] == cfg["qemu_sha256"],
            "baseline did not run the requested CI binary")
    require(baseline["kernel_sha256"] == KERNEL_SHA and
            baseline["dtb_sha256"] == DT_SHA, "baseline kernel/DT identity")

    value_keys = {"-M", "-accel", "-m", "-smp", "-display", "-monitor",
                  "-serial", "-qmp", "-kernel", "-initrd", "-append",
                  "-d", "-D", "-dtb", "-global"}
    flag_keys = {"-S", "-no-reboot", "-no-shutdown"}
    pairs, values, flags = [], {}, []
    i = 1
    while i < len(original):
        key = original[i]
        if key in flag_keys:
            require(key not in flags, "duplicate baseline flag")
            flags.append(key)
            pairs.append((key, None))
            i += 1
        else:
            require(key in value_keys and i + 1 < len(original),
                    "unexpected baseline argument; no devices/disks may be added")
            value = original[i + 1]
            if key != "-global":
                require(key not in values, "duplicate baseline option")
                values[key] = value
            pairs.append((key, value))
            i += 2
    require(set(values) == value_keys - {"-global"} and
            set(flags) == flag_keys, "unexpected baseline command shape")
    observed_globals = [v for k, v in pairs if k == "-global"]
    require(observed_globals == list(GLOBALS), "reference globals changed")
    require(values["-accel"] == "tcg,thread=multi" and values["-smp"] == "8"
            and values["-m"] == "8G" and values["-append"] ==
            "rdinit=/init nokaslr panic=0", "baseline execution configuration")
    machine = values["-M"].split(",")
    require(machine[0] == "tb371fc", "wrong machine")
    machine_options = dict(x.split("=", 1) for x in machine[1:])
    require(machine_options.get("aop-profile") == "reference-qdss-vote" and
            "gdsc-profile" not in machine_options, "wrong reference machine")
    identities = {
        str(Path(cfg["qemu"]).resolve()): cfg["qemu_sha256"],
        values["-kernel"]: KERNEL_SHA, values["-dtb"]: DT_SHA,
        str(INIT): INIT_SHA, str(INITRD): INITRD_SHA,
        machine_options["rpmh-cmd-db"]: CMD_DB_SHA,
        str(Path(cfg["native_probe_reference"]).resolve()): NATIVE_SHA,
    }
    helpers = Path(cfg["helpers"]).resolve()
    identities.update({str(helpers / n): digest for n, digest in HELPERS.items()})
    for path, expected in identities.items():
        require(sha(path) == expected, "input digest mismatch: " + path)
    # Identity pins the already-inspected ELF and result ABI; no relinking or nm.
    require(INIT.read_bytes()[655360:655364] == bytes.fromhex("c0035fd6"),
            "fixed checkpoint is not the inspected RET")
    for name in ("boot_smoke_gdb", "boot_smoke", "boot_qmp", "boot_verdict"):
        if name in sys.modules:
            require(Path(sys.modules[name].__file__).resolve() ==
                    helpers / (name + ".py"), "different helper already imported")
    sys.path.insert(0, str(helpers))
    h = importlib.import_module("boot_smoke_gdb")  # never invoke h.main()
    native = json.loads(Path(cfg["native_probe_reference"]).read_text())
    require(native["elf_sha256"] ==
            "0ed51cb6c6dd24685beb3f77056d17d47c5c2f697df96d87eef4a678d4c5cb1b",
            "wrong fixed arch-probe identity")

    command = [str(Path(cfg["qemu"]).resolve())]
    replacements = {"-initrd": str(INITRD),
                    "-qmp": f"unix:{out / 'qmp.sock'},server=on,wait=off",
                    "-D": str(out / "qemu.log")}
    for key, value in pairs:
        if key == "-no-shutdown":
            continue  # permit the real guest poweroff to exit QEMU
        command.append(key)
        if value is not None:
            command.append(replacements.get(key, value))
    command += ["-gdb", f"unix:{out / 'gdb.sock'},server=on,wait=off"]
    return h, native, command, identities, original


def run(cfg):
    out = Path(cfg["out"]).resolve()
    require(not out.exists(), "use a fresh output directory")
    require(len(str(out / "qmp.sock").encode()) < 104 and
            len(str(out / "gdb.sock").encode()) < 104, "Unix socket path too long")
    timeout = cfg.get("timeout_seconds", 120)
    require(type(timeout) in (int, float) and 0 < timeout <= 600, "timeout range")
    out.mkdir(parents=True)
    report = {
        "schema": "tb371fc-native-full-dt-userspace8-draft-v1",
        "scope": "Fixed validation PID1 under production Image/full DT; not Android",
        "source_commit_provenance": "Caller supplies the accepted CI receipt identity",
        "source_commit": cfg.get("source_commit"),
        "qemu_sha256": cfg.get("qemu_sha256"),
        "checkpoint_observed": False, "userspace_record_passed": False,
        "guest_shutdown_observed": False, "lifecycle_passed": False,
        "passed": False, "guest_ram_written_by_collector": False,
        "guest_registers_or_pc_written_by_collector": False,
        "debug_stop_resume_and_tcg_breakpoints": True,
        "compiler_invoked": False, "fixture_modified": False,
        "synthetic_disk_created": False, "ufs_lu_attached": False,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
    process = qmp = gdb = serial = h = None
    initial_hashes = {}
    started = time.monotonic()
    try:
        h, native, command, initial_hashes, original = prepare(cfg, out)
        report.update(command=command, baseline_command=original,
                      baseline_diagnostic_sha256=cfg["baseline_diagnostic_sha256"],
                      input_hashes=initial_hashes,
                      initramfs_sha256=INITRD_SHA, init_elf_sha256=INIT_SHA,
                      kernel_sha256=KERNEL_SHA, dtb_sha256=DT_SHA)
        write_json(out / "prepared-inputs.json", report)
        serial = (out / "serial.log").open("wb")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=serial, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        qmp = h.QMP(h.connect(out / "qmp.sock", process,
                              min(deadline, time.monotonic() + 5)))
        qmp.command("qmp_capabilities")
        report["qemu_cpus"] = qmp.command("query-cpus-fast")
        require(len(report["qemu_cpus"]) == 8, "not eight QEMU CPUs")
        gdb = h.GDB(h.connect(out / "gdb.sock", process,
                              min(deadline, time.monotonic() + 5)))
        report["gdb_capabilities"] = gdb.request(
            "qSupported:multiprocess+;qXfer:features:read+")
        report["gdb_target_xml"] = gdb.request("qXfer:features:read:target.xml:0,fff")
        require(gdb.request(f"Z0,{PC:x},4") == "OK", "TCG breakpoint rejected")
        gdb.send("c")  # no address: never replaces the guest PC
        stopped = gdb.receive(deadline)
        report["gdb_stop_reply"] = stopped
        require(stopped.startswith(("T05", "S05")), "not a breakpoint stop")
        thread = re.search(r"thread:([^;]+)", stopped)
        if thread:
            require(gdb.request("Hg" + thread.group(1)) == "OK",
                    "cannot select stopped CPU")
        pc = int.from_bytes(bytes.fromhex(gdb.request("p20")), "little")
        pstate = int.from_bytes(bytes.fromhex(gdb.request("p21")), "little")
        report.update(checkpoint_pc=pc, checkpoint_pstate=pstate)
        require(pc == PC and (pstate & 15) == 0, "not the fixed EL0 checkpoint")
        report["checkpoint_observed"] = True
        report["status_at_checkpoint"] = qmp.command("query-status")
        require(report["status_at_checkpoint"].get("running") is False,
                "guest is not stopped during record observation")
        try:
            raw = gdb.memory(RECORD, RECORD_BYTES)
            (out / "guest-result.bin").write_bytes(raw)
            report["guest_result_sha256"] = hashlib.sha256(raw).hexdigest()
            record = h.decode_record(raw)
            errors, probe = h.validate_record(record, native)
            (out / "arch-probe.stdout").write_bytes(record["probe_stdout"].encode())
            report.update(guest_result=record, arch_counter_probe=probe,
                          guest_validation_errors=errors,
                          userspace_record_passed=not errors,
                          guest_checks=[{"name": name,
                              "passed": bool(record["pass_mask"] & (1 << i)),
                              "failed": bool(record["fail_mask"] & (1 << i))}
                              for i, name in enumerate(h.CHECKS)])
        except Exception as exc:
            report["guest_validation_errors"] = [type(exc).__name__ + ": " + str(exc)]
        # Preserve EL0/8-check evidence before lifecycle observation can fail.
        write_json(out / "userspace-checkpoint.json", report)
        require(gdb.request(f"z0,{PC:x},4") == "OK", "breakpoint removal failed")
        report["breakpoint_removed"] = True
        gdb.send("c")
        report["resumed_for_guest_poweroff"] = True
        while time.monotonic() < deadline and process.poll() is None:
            if qmp.receive(.05) is None:
                break
        if process.poll() is None:
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        qmp.drain()
    except Exception as exc:
        report["collector_error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        if qmp:
            try:
                qmp.drain()
            except (OSError, ValueError):
                pass
            report["qmp_messages"] = qmp.messages
            report["guest_shutdown_observed"] = h.has_guest_shutdown(qmp.messages)
            qmp.sock.close()
        if gdb:
            write_json(out / "gdb-transcript.json", gdb.transcript)
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
        report["inputs_unchanged"] = bool(initial_hashes) and all(
            sha(path) == digest for path, digest in initial_hashes.items())
        report["lifecycle_passed"] = bool(
            report["guest_shutdown_observed"] and report.get("exit_code") == 0
            and not report.get("observer_terminated_qemu", False)
            and report.get("breakpoint_removed") is True
            and report.get("resumed_for_guest_poweroff") is True)
        report["passed"] = bool(report["userspace_record_passed"]
            and report["checkpoint_observed"] and report["lifecycle_passed"]
            and report["inputs_unchanged"] and not report.get("collector_error"))
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        write_json(out / "result.json", report)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: native-userspace8-draft.py CONFIG.json")
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
