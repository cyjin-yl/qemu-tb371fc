#!/usr/bin/env python3
"""Build an initramfs and boot a supplied Linux Image on -M tb371fc.

The kernel is an explicit input, never downloaded or silently substituted.
Only a successful guest verdict AND clean PSCI poweroff pass the test.
"""
# SPDX-License-Identifier: GPL-2.0-or-later
from __future__ import annotations

import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import time


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def cpio_entry(name: str, data: bytes, mode: int, inode: int,
               rdev_major: int = 0, rdev_minor: int = 0) -> bytes:
    filename = name.encode() + b"\0"
    fields = [inode, mode, 0, 0, 1, 0, len(data), 0, 0,
              rdev_major, rdev_minor, len(filename), 0]
    header = b"070701" + b"".join(f"{value:08x}".encode() for value in fields)
    part = header + filename
    part += b"\0" * (-len(part) % 4)
    part += data
    return part + b"\0" * (-len(data) % 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cc", default="aarch64-linux-gnu-gcc")
    parser.add_argument("--arch-probe", type=Path,
                        help="Use an existing probe ELF, e.g. the exact binary tested on Android")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    qemu, kernel, out = args.qemu.resolve(), args.kernel.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = Path(__file__).resolve().parents[1] / "tests" / "guest-init.c"
    init = out / "init"
    compile_command = [args.cc, "-static", "-Os", "-Wall", "-Wextra", "-Werror",
                       str(source), "-o", str(init)]
    subprocess.run(compile_command, check=True)
    probe = args.arch_probe.resolve() if args.arch_probe else out / "arch-probe"
    if not args.arch_probe:
        subprocess.run(["sh", str(Path(__file__).with_name("build_arch_probe.sh")),
                        str(probe)], check=True)
    native_reference = (Path(__file__).resolve().parents[1] / "evidence" /
                        "android-arch-probe.json")
    native = json.loads(native_reference.read_text())
    archive = cpio_entry("init", init.read_bytes(), 0o100755, 1)
    archive += cpio_entry("dev", b"", 0o040755, 2)
    archive += cpio_entry("dev/console", b"", 0o020600, 3, 5, 1)
    archive += cpio_entry("proc", b"", 0o040755, 4)
    archive += cpio_entry("sys", b"", 0o040755, 5)
    archive += cpio_entry("arch-probe", probe.read_bytes(), 0o100755, 6)
    archive += cpio_entry("TRAILER!!!", b"", 0, 7)
    initramfs = out / "initramfs.cpio.gz"
    initramfs.write_bytes(gzip.compress(archive, mtime=0))
    command = [str(qemu), "-M", "tb371fc", "-accel", "tcg,thread=multi",
               "-m", "8G", "-smp", "8", "-display", "none", "-monitor", "none",
               "-serial", "stdio", "-no-reboot", "-kernel", str(kernel),
               "-initrd", str(initramfs), "-append",
               "earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 "
               "rdinit=/init loglevel=7 panic=-1 nokaslr"]
    start = time.monotonic()
    result = {
        "schema_version": 1,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Linux boot on experimental tb371fc machine; not stock Android or hardware equivalence",
        "command": command,
        "kernel_sha256": sha256(kernel),
        "qemu_sha256": sha256(qemu),
        "guest_source_sha256": sha256(source),
        "initramfs_sha256": sha256(initramfs),
        "arch_probe_sha256": sha256(probe),
        "native_probe_reference_sha256": sha256(native_reference),
        "identical_probe_binary_to_native_run": sha256(probe) == native["elf_sha256"],
        "timeout_seconds": args.timeout,
        "compile_command": compile_command,
    }
    timed_out = False
    try:
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=args.timeout, check=False)
        output = proc.stdout
        result["exit_code"] = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        output = exc.stdout or b""
        result["exit_code"] = None
    log = out / "serial.log"
    log.write_bytes(output)
    text = output.decode("utf-8", errors="replace")
    probe_records = []
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("kind") == "tb371fc-el0-counter-probe":
            probe_records.append(record)
    probe_ok = (len(probe_records) == 1 and
                probe_records[0]["counter_frequency_hz"] ==
                native["probe_result"]["counter_frequency_hz"] and
                probe_records[0]["counter_delta"] > 0 and
                probe_records[0]["increased"] is True)
    checks = [line.strip() for line in text.splitlines() if line.startswith("TB371FC_CHECK ")]
    expected = {"procfs", "sysfs", "aarch64", "eight_online_cpus_psci",
                "timer_and_scheduler_progress", "fork_and_wait", "board_model",
                "arch_counter_probe"}
    passed = {line.removeprefix("TB371FC_CHECK PASS ") for line in checks
              if line.startswith("TB371FC_CHECK PASS ")}
    verdict = (not timed_out and result["exit_code"] == 0 and passed == expected and probe_ok and
               "TB371FC_GUEST_RESULT PASS failures=0" in text and
               "TB371FC_CHECK FAIL" not in text and "Kernel panic" not in text)
    result.update(elapsed_seconds=round(time.monotonic() - start, 3),
                  timed_out=timed_out, guest_checks=checks,
                  arch_counter_probe=probe_records,
                  arch_counter_matches_native_frequency_and_progress=probe_ok,
                  serial_sha256=sha256(log), passed=verdict)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not verdict:
        print(text[-12000:])
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
