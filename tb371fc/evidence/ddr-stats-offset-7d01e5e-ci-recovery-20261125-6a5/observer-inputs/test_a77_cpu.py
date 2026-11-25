#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compile and execute a bare-metal A77 probe; requires GENERIC_LOADER.

Uses the actual TCG CPU and guest exception vectors. No tablet access, source
constant introspection or simulated pass results. A dedicated -M none instance
isolates CPU behavior from the board; the 8-CPU/8-GiB Linux gate remains separate.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute(command, timeout=30, log=None):
    if log is not None:
        with log.open("w") as output:
            try:
                result = subprocess.run(command, stdout=output,
                                        stderr=subprocess.STDOUT,
                                        text=True, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                raise RuntimeError(
                    f"Timed out; live guest output retained at {log}") from error
        return result.returncode, log.read_text()
    result = subprocess.run(command, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=timeout)
    return result.returncode, result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cross-compile", default="aarch64-linux-gnu-")
    args = parser.parse_args()
    args.qemu = args.qemu.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    # Never overwrite prior evidence. Each invocation needs a new output dir.
    if (args.output / "result.json").exists():
        parser.error("output already contains result.json; use a fresh directory")
    sources = Path(__file__).resolve().with_name("a77")
    cc = shutil.which(args.cross_compile + "gcc")
    if not cc:
        parser.error("AArch64 cross compiler not found")
    variants = [
        ("default", "", 0, 512 * 1024, 0, True),
        ("observed-revidr", ",revidr=3", 3, 512 * 1024, 0, True),
        ("l2-128k", ",l2-cache-size=128K", 0, 128 * 1024, 0, True),
        ("l2-256k", ",l2-cache-size=256K", 0, 256 * 1024, 0, True),
        ("l3-512k", ",l3-cache-size=512K", 0, 512 * 1024, 512 * 1024, True),
        ("l3-1m", ",l3-cache-size=1M", 0, 512 * 1024, 1024 * 1024, True),
        ("l3-2m", ",l3-cache-size=2M", 0, 512 * 1024, 2 * 1024 * 1024, True),
        ("l3-4m", ",l3-cache-size=4M", 0, 512 * 1024, 4 * 1024 * 1024, True),
        ("crypto-off", ",crypto=off", 0, 512 * 1024, 0, False),
    ]
    receipt = {
        "scope": "A77 architectural CPU, isolated -M none bare-metal probe",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "qemu": str(args.qemu), "qemu_sha256": sha256(args.qemu),
        "compiler": cc, "compiler_sha256": sha256(cc),
        "compiler_version": execute([cc, "--version"])[1].splitlines()[0],
        "source_sha256": {p.name: sha256(p) for p in
                          sorted(sources.iterdir()) if p.is_file()},
        "test_runner_sha256": sha256(Path(__file__)),
        "cpu_source_sha256": {
            relative: sha256(sources.parents[2] / relative)
            for relative in (
                "target/arm/tcg/cpu64.c", "target/arm/cpregs-pmu.c",
                "configs/devices/aarch64-softmmu/a77-test.mak",
            )
        },
        "variants": [], "invalid_configurations": [], "passed": False,
    }
    start = time.monotonic()
    try:
        for name, options, revidr, l2, l3, crypto in variants:
            elf = args.output / (name + ".elf")
            command = [
                cc, "-O2", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
                "-fno-builtin", "-fno-stack-protector", "-fno-pie",
                "-mgeneral-regs-only", "-march=armv8.2-a", "-nostdlib",
                "-static", "-Wl,--build-id=none",
                "-T", str(sources / "link.ld"),
                f"-DEXPECT_REVIDR={revidr}", f"-DEXPECT_L2={l2}",
                f"-DEXPECT_L3={l3}", f"-DEXPECT_CRYPTO={int(crypto)}",
                str(sources / "start.S"), str(sources / "probe.c"),
                "-o", str(elf),
            ]
            code, compile_log = execute(command)
            (args.output / (name + ".build.log")).write_text(compile_log)
            if code:
                raise RuntimeError(f"{name}: guest compilation failed: {compile_log}")
            qemu_command = [
                str(args.qemu), "-M", "none", "-cpu", "cortex-a77" + options,
                "-accel", "tcg,thread=single", "-m", "4M", "-display", "none",
                "-monitor", "none", "-serial", "none", "-no-reboot",
                "-semihosting-config", "enable=on,target=native",
                "-device", f"loader,file={elf},cpu-num=0",
            ]
            log = args.output / (name + ".log")
            code, output = execute(qemu_command, log=log)
            checks = [line[5:] for line in output.splitlines()
                      if line.startswith("PASS ")]
            entry = {
                "name": name, "cpu_options": options, "build_command": command,
                "command": qemu_command, "elf_sha256": sha256(elf),
                "log": str(log), "log_sha256": sha256(log),
                "exit_code": code, "passed_checks": len(checks),
                "checks": checks,
                "passed": code == 0 and "A77_PROBE_PASS checks=" in output and
                          "FAIL " not in output and len(checks) >= 75,
            }
            receipt["variants"].append(entry)
            if not entry["passed"]:
                raise RuntimeError(f"{name}: probe failed\n{output}")
            print(f"PASS {name}: {len(checks)} guest checks", flush=True)
        for options, expected in [
            (",l2-cache-size=1M", "L2 size must be"),
            (",l3-cache-size=128K", "L3 size must be"),
        ]:
            command = [str(args.qemu), "-M", "none", "-cpu",
                       "cortex-a77" + options, "-display", "none",
                       "-monitor", "none", "-serial", "none"]
            code, output = execute(command, timeout=10)
            passed = code != 0 and expected in output
            receipt["invalid_configurations"].append({
                "cpu_options": options, "command": command,
                "exit_code": code, "output": output, "passed": passed,
            })
            if not passed:
                raise RuntimeError(f"invalid configuration was not rejected: {output}")
            print(f"PASS rejected {options}", flush=True)
        receipt["passed"] = True
    except Exception as error:
        receipt["error"] = str(error)
        print(str(error), file=sys.stderr, flush=True)
    finally:
        receipt["elapsed_seconds"] = round(time.monotonic() - start, 3)
        receipt["finished_utc"] = datetime.datetime.now(
            datetime.timezone.utc).isoformat()
        (args.output / "result.json").write_text(json.dumps(
            receipt, indent=2, sort_keys=True) + "\n")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
