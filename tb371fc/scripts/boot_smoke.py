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
import tempfile
import time

from boot_qmp import QMP, connect, has_guest_shutdown
from boot_verdict import validate_console


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


def observe_boot(command, log, qmp_path, timeout):
    """Observe QEMU until exit and preserve the reason for guest shutdown."""
    report = {"timed_out": False, "guest_shutdown_observed": False,
              "qmp_messages": [], "exit_code": None}
    process = qmp = None
    deadline = time.monotonic() + timeout
    with log.open("wb") as serial:
        try:
            process = subprocess.Popen(command, stdout=serial, stderr=subprocess.STDOUT)
            qmp = QMP(connect(qmp_path, process, min(deadline, time.monotonic() + 5)))
            qmp.command("qmp_capabilities")
            qmp.command("cont")
            while process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                if qmp.receive(min(.1, remaining)) is None:
                    process.wait(timeout=min(3, remaining))
            qmp.drain()
        except subprocess.TimeoutExpired:
            report["timed_out"] = True
        except Exception as exc:
            report["observer_error"] = str(exc)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3)
                report["exit_code"] = process.returncode
            if qmp is not None:
                try:
                    qmp.drain()
                except Exception as exc:
                    report.setdefault("observer_error", str(exc))
                report["qmp_messages"] = qmp.messages
                report["guest_shutdown_observed"] = has_guest_shutdown(qmp.messages)
                qmp.sock.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--cc", default="aarch64-linux-gnu-gcc")
    parser.add_argument("--arch-probe", type=Path,
                        help="Use an existing probe ELF, e.g. the exact binary tested on Android")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--dtb", type=Path, help="Optional explicit device tree input")
    parser.add_argument("--append", help="Explicit kernel command line for compatibility diagnosis")
    parser.add_argument("--without-ufs-lu", action="store_true",
                        help="Diagnostic empty-UFS-bus configuration; no synthetic LUN")
    parser.add_argument("--debug-mmio", action="store_true",
                        help="Record QEMU unimplemented/guest-error diagnostics")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    qemu, kernel, out = args.qemu.resolve(), args.kernel.resolve(), args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
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
    if args.append is not None:
        command[-1] = args.append
    if args.dtb:
        command.extend(["-dtb", str(args.dtb.resolve())])
    disk = None
    if not args.without_ufs_lu:
        # A tablet boot fixture needs a populated device, even when PID 1 lives
        # wholly in initramfs. Keep the empty-bus case explicit for diagnosis.
        disk = out / "smoke-disk.raw"
        with disk.open("xb") as stream:
            stream.truncate(64 * 1024 * 1024)
        command.extend([
            "-blockdev", json.dumps({"driver": "file", "node-name": "smoke-file",
                                    "filename": str(disk)}),
            "-blockdev", "driver=raw,node-name=smoke-disk,file=smoke-file",
            "-device", "ufs-lu,bus=ufs0,drive=smoke-disk,lun=0",
        ])
    debug_log = out / "qemu-debug.log"
    if args.debug_mmio:
        command.extend(["-d", "unimp,guest_errors", "-D", str(debug_log)])
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
    result["ufs_fixture"] = {"populated": disk is not None,
                             "kind": "new synthetic empty 64 MiB disk" if disk else "none",
                             "bytes": disk.stat().st_size if disk else 0}
    if args.dtb:
        result["dtb_sha256"] = sha256(args.dtb.resolve())
    log = out / "serial.log"
    # Start paused so even a fast guest cannot shut down before QMP subscribes.
    # A short socket directory also works in deeply nested CI checkouts.
    with tempfile.TemporaryDirectory(prefix="tbboot-", dir="/tmp") as runtime:
        qmp_path = Path(runtime) / "qmp.sock"
        command.extend(["-S", "-qmp", f"unix:{qmp_path},server=on,wait=off"])
        result.update(observe_boot(command, log, qmp_path, args.timeout))
    output = log.read_bytes()
    if debug_log.exists():
        result["qemu_debug_sha256"] = sha256(debug_log)
    text = output.decode("utf-8", errors="replace")
    result.update(validate_console(text, native))
    verdict = (not result["timed_out"] and result["exit_code"] == 0 and
               "observer_error" not in result and result["guest_shutdown_observed"] and
               not result["validation_errors"])
    result.update(elapsed_seconds=round(time.monotonic() - start, 3),
                  serial_sha256=sha256(log), passed=verdict)
    (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if not verdict:
        print(text[-12000:])
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
