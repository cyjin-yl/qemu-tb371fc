#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate a supplied kernel through PID1's RAM checkpoint and real shutdown.

No vendor-kernel edits, console dependency, synthetic device, guest memory
writes or injected success. GDB's TCG breakpoint observes a completed result,
then is removed before the guest resumes and performs PSCI poweroff.
"""
from __future__ import annotations
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import select
import socket
import struct
import subprocess
import time

from boot_smoke import cpio_entry, sha256
from boot_qmp import QMP, connect, has_guest_shutdown
from boot_verdict import CHECKS, validate_counter_probe

MAGIC = 0x3153455243464254
RECORD_SIZE = 3120
PROBE_CAPACITY = 2048
FIELDS = ("magic", "version", "structure_size", "completed",
          "checks_completed", "pass_mask", "fail_mask", "failures",
          "elapsed_ns", "counter_probe_bytes", "counter_probe_wait_status",
          "probe_truncated", "start_monotonic_ns", "finish_monotonic_ns")
STRINGS = (("uname_sysname", 128), ("uname_nodename", 128),
           ("uname_release", 128), ("uname_version", 128),
           ("uname_machine", 128), ("uname_domainname", 128),
           ("online", 64), ("model", 128), ("probe_stdout", 2048))


def decode_record(blob):
    if len(blob) != RECORD_SIZE:
        raise ValueError(f"result length {len(blob)} != {RECORD_SIZE}")
    record = dict(zip(FIELDS, struct.unpack_from("<14Q", blob)))
    for name in ("elapsed_ns", "counter_probe_wait_status"):
        if record[name] >= 1 << 63:
            record[name] -= 1 << 64
    offset = 112
    for name, length in STRINGS:
        raw = blob[offset:offset + length]
        if b"\0" not in raw:
            raise ValueError(f"{name} is not NUL terminated")
        record[name] = raw.split(b"\0", 1)[0].decode("utf-8", errors="strict")
        offset += length
    return record


def validate_record(record, native):
    errors = []
    required = {
        "magic": MAGIC, "version": 1, "structure_size": RECORD_SIZE,
        "completed": 1, "checks_completed": 0xff, "pass_mask": 0xff,
        "fail_mask": 0, "failures": 0, "probe_truncated": 0,
        "counter_probe_wait_status": 0, "uname_sysname": "Linux",
        "uname_machine": "aarch64", "online": "0-7\n",
    }
    for name, value in required.items():
        if record.get(name) != value:
            errors.append(f"{name}: expected {value!r}, got {record.get(name)!r}")
    if not record.get("uname_release"):
        errors.append("empty uname release")
    if "TB371FC" not in record.get("model", ""):
        errors.append("device tree model does not identify TB371FC")
    if record.get("elapsed_ns", -1) < 20_000_000:
        errors.append("nanosleep/monotonic interval shorter than 20ms")
    if not (0 < record.get("start_monotonic_ns", 0) <
            record.get("finish_monotonic_ns", 0)):
        errors.append("invalid monotonic start/finish timestamps")
    raw = record.get("probe_stdout", "").encode()
    if not (0 < len(raw) < PROBE_CAPACITY and
            len(raw) == record.get("counter_probe_bytes")):
        errors.append("probe stdout byte count mismatch")
    probe = None
    try:
        # Exactly one complete JSON document; extra non-whitespace is rejected.
        probe = json.loads(record.get("probe_stdout", ""))
        validate_counter_probe(probe, native)
    except (ValueError, TypeError, KeyError) as error:
        errors.append(f"probe JSON: {error}")
    return errors, probe


class GDB:
    """Minimal checksummed GDB remote client; never issues a memory-write packet."""
    def __init__(self, sock):
        self.sock, self.buffer, self.transcript = sock, b"", []
        self.last_sent = b""

    def send(self, payload):
        raw = payload.encode("ascii")
        self.last_sent = b"$" + raw + b"#" + f"{sum(raw) & 255:02x}".encode()
        self.sock.sendall(self.last_sent)
        self.transcript.append({"send": payload})

    @staticmethod
    def decode(payload):
        result = bytearray()
        i = 0
        while i < len(payload):
            char = payload[i]
            i += 1
            if char == ord("}"):
                if i == len(payload):
                    raise ValueError("incomplete GDB escape")
                result.append(payload[i] ^ 0x20)
                i += 1
            elif char == ord("*"):
                if not result or i == len(payload) or payload[i] < 29:
                    raise ValueError("invalid GDB run-length encoding")
                result.extend([result[-1]] * (payload[i] - 29))
                i += 1
            else:
                result.append(char)
        return result.decode("ascii")

    def receive(self, deadline):
        while time.monotonic() < deadline:
            while self.buffer[:1] in (b"+", b"-"):
                ack, self.buffer = self.buffer[:1], self.buffer[1:]
                if ack == b"-":
                    self.sock.sendall(self.last_sent)
            if self.buffer.startswith(b"$"):
                end = self.buffer.find(b"#", 1)
                if end >= 0 and len(self.buffer) >= end + 3:
                    raw = self.buffer[1:end]
                    expected = int(self.buffer[end + 1:end + 3], 16)
                    self.buffer = self.buffer[end + 3:]
                    if (sum(raw) & 255) != expected:
                        self.sock.sendall(b"-")
                        continue
                    self.sock.sendall(b"+")
                    value = self.decode(raw)
                    self.transcript.append({"receive": value})
                    return value
            elif self.buffer:
                raise ValueError(f"unexpected GDB framing: {self.buffer[:64]!r}")
            if not select.select([self.sock], [], [],
                                 min(.1, max(0, deadline-time.monotonic())))[0]:
                continue
            data = self.sock.recv(65536)
            if not data:
                raise RuntimeError("GDB closed before expected checkpoint")
            self.buffer += data
        raise TimeoutError("GDB checkpoint/response timed out")

    def request(self, command, deadline=None):
        self.send(command)
        answer = self.receive(deadline or (time.monotonic() + 5))
        if answer.startswith("E"):
            raise RuntimeError(f"GDB {command}: {answer}")
        return answer

    def memory(self, address, length):
        data = bytearray()
        while len(data) < length:
            count = min(256, length - len(data))
            answer = self.request(f"m{address+len(data):x},{count:x}")
            chunk = bytes.fromhex(answer)
            if len(chunk) != count:
                raise ValueError("GDB returned a short memory read")
            data.extend(chunk)
        return bytes(data)


def symbols(nm, elf):
    result = subprocess.run([nm, "-S", "--defined-only", str(elf)],
                            check=True, stdout=subprocess.PIPE, text=True)
    found = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3] in (
                "tb371fc_guest_result", "tb371fc_guest_checkpoint"):
            found[parts[3]] = {"address": int(parts[0], 16),
                               "size": int(parts[1], 16)}
    if len(found) != 2 or found["tb371fc_guest_result"]["size"] != RECORD_SIZE:
        raise ValueError("guest ELF is missing the expected result/checkpoint ABI")
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--arch-probe", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cc", default="aarch64-linux-gnu-gcc")
    parser.add_argument("--nm", default="aarch64-linux-gnu-nm")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--append", default=(
        "earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 "
        "rdinit=/init loglevel=7 panic=-1 nokaslr"))
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if (out / "result.json").exists():
        parser.error("output receipt exists; use a new directory")
    if args.timeout <= 0 or args.timeout > 600:
        parser.error("timeout must be in (0, 600]")
    qmp_path, gdb_path = out / "qmp.sock", out / "gdb.sock"
    if max(len(str(qmp_path).encode()), len(str(gdb_path).encode())) >= 104:
        parser.error("output path is too long for Unix sockets")
    root = Path(__file__).resolve().parents[1]
    source = root / "tests/guest-init.c"
    header = root / "tests/guest-result.h"
    native_path = root / "evidence/android-arch-probe.json"
    native = json.loads(native_path.read_text())
    init, initrd = out / "init", out / "initramfs.cpio.gz"
    qemu, kernel, probe = (p.resolve() for p in
                           (args.qemu, args.kernel, args.arch_probe))
    report = {
        "schema_version": 1,
        "scope": "Supplied unmodified kernel, validation initramfs; not stock Android boot",
        "device_tree_source": ("explicit supplied DTB" if args.dtb else
                               "machine-generated functional-subset DT"),
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "kernel_sha256": sha256(kernel), "qemu_sha256": sha256(qemu),
        "guest_source_sha256": sha256(source),
        "guest_header_sha256": sha256(header),
        "observer_source_sha256": sha256(Path(__file__)),
        "arch_probe_sha256": sha256(probe),
        "native_probe_reference_sha256": sha256(native_path),
        "identical_probe_binary_to_native_run": sha256(probe) == native["elf_sha256"],
        "checkpoint_observed": False, "guest_shutdown_observed": False,
        "passed": False,
    }
    start = time.monotonic()
    process = qmp = gdb = None
    serial = None
    try:
        if not report["identical_probe_binary_to_native_run"]:
            raise ValueError("arch-probe ELF differs from the native-tested binary")
        compile_command = [
            args.cc, "-static", "-fno-pie", "-no-pie", "-g", "-Os",
            "-Wall", "-Wextra", "-Werror",
            "-Wl,--section-start=.tb371fc_checkpoint=0x8000000",
            str(source), "-o", str(init),
        ]
        report["compile_command"] = compile_command
        with (out / "compile.log").open("wb") as log:
            subprocess.run(compile_command, check=True, stdout=log,
                           stderr=subprocess.STDOUT)
        table = symbols(args.nm, init)
        report["guest_symbols"] = table
        report["init_elf_sha256"] = sha256(init)
        archive = cpio_entry("init", init.read_bytes(), 0o100755, 1)
        archive += cpio_entry("dev", b"", 0o040755, 2)
        archive += cpio_entry("dev/console", b"", 0o020600, 3, 5, 1)
        archive += cpio_entry("proc", b"", 0o040755, 4)
        archive += cpio_entry("sys", b"", 0o040755, 5)
        archive += cpio_entry("arch-probe", probe.read_bytes(), 0o100755, 6)
        archive += cpio_entry("TRAILER!!!", b"", 0, 7)
        initrd.write_bytes(gzip.compress(archive, mtime=0))
        report["initramfs_sha256"] = sha256(initrd)
        command = [
            str(qemu), "-M", "tb371fc", "-accel", "tcg,thread=multi",
            "-m", "8G", "-smp", "8", "-display", "none", "-monitor", "none",
            "-serial", "stdio", "-no-reboot", "-S",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-gdb", f"unix:{gdb_path},server=on,wait=off",
            "-kernel", str(kernel), "-initrd", str(initrd),
            "-append", args.append,
        ]
        if args.dtb:
            command.extend(["-dtb", str(args.dtb.resolve())])
            report["dtb_sha256"] = sha256(args.dtb)
        report["command"] = command
        serial = (out / "serial.log").open("wb")
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=serial, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + args.timeout
        qmp = QMP(connect(qmp_path, process, min(deadline, time.monotonic()+5)))
        qmp.command("qmp_capabilities")
        report["qemu_cpus"] = qmp.command("query-cpus-fast")
        if len(report["qemu_cpus"]) != 8:
            raise ValueError("machine does not expose eight CPUs")
        for cpu in report["qemu_cpus"]:
            cpu["midr"] = qmp.command(
                "qom-get", {"path": cpu["qom-path"], "property": "midr"})
        gdb = GDB(connect(gdb_path, process, min(deadline, time.monotonic()+5)))
        report["gdb_capabilities"] = gdb.request(
            "qSupported:multiprocess+;qXfer:features:read+")
        report["gdb_target_xml"] = gdb.request(
            "qXfer:features:read:target.xml:0,fff")
        checkpoint = table["tb371fc_guest_checkpoint"]["address"]
        if checkpoint != 0x8000000:
            raise ValueError("checkpoint was not linked at its reserved user VA")
        if gdb.request(f"Z0,{checkpoint:x},4") != "OK":
            raise RuntimeError("GDB breakpoint was not accepted")
        gdb.send("c")
        stopped = gdb.receive(deadline)
        report["gdb_stop_reply"] = stopped
        if not stopped.startswith(("T05", "S05")):
            raise ValueError(f"unexpected GDB stop: {stopped}")
        match = re.search(r"thread:([^;]+)", stopped)
        if match and gdb.request("Hg" + match.group(1)) != "OK":
            raise RuntimeError("cannot select checkpoint CPU")
        pc = int.from_bytes(bytes.fromhex(gdb.request("p20")), "little")
        pstate = int.from_bytes(bytes.fromhex(gdb.request("p21")), "little")
        report.update(checkpoint_pc=pc, checkpoint_pstate=pstate)
        if pc != checkpoint or (pstate & 15) != 0:
            raise ValueError("breakpoint did not stop the EL0 guest checkpoint")
        report["checkpoint_observed"] = True
        record_address = table["tb371fc_guest_result"]["address"]
        try:
            blob = gdb.memory(record_address, RECORD_SIZE)
            (out / "guest-result.bin").write_bytes(blob)
            report["guest_result_sha256"] = hashlib.sha256(blob).hexdigest()
            record = decode_record(blob)
            (out / "arch-probe.stdout").write_bytes(
                record["probe_stdout"].encode())
            errors, probe_result = validate_record(record, native)
            report.update(
                guest_result=record, guest_validation_errors=errors,
                arch_counter_probe=probe_result,
                guest_checks=[{"name": name,
                               "passed": bool(record["pass_mask"] & (1 << i)),
                               "failed": bool(record["fail_mask"] & (1 << i))}
                              for i, name in enumerate(CHECKS)])
        except Exception as error:
            errors = [f"checkpoint record could not be read/decoded: {error}"]
            report["guest_validation_errors"] = errors
        # Always resume even a guest-reported failure so shutdown is observable.
        if gdb.request(f"z0,{checkpoint:x},4") != "OK":
            raise RuntimeError("GDB breakpoint removal failed")
        gdb.send("c")
        while time.monotonic() < deadline:
            messages = qmp.receive(.05)
            if messages is None:
                break
            if process.poll() is not None:
                qmp.drain()
                break
        if process.poll() is None:
            process.wait(timeout=max(.01, deadline-time.monotonic()))
        qmp.drain()
        report["exit_code"] = process.returncode
        report["guest_shutdown_observed"] = has_guest_shutdown(qmp.messages)
        report["passed"] = (not errors and report["checkpoint_observed"] and
                            report["guest_shutdown_observed"] and
                            report["exit_code"] == 0)
        if not report["passed"]:
            report["error"] = "Guest checks or real guest-initiated shutdown failed"
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
    finally:
        if qmp:
            try:
                qmp.drain()
            except (OSError, ValueError):
                pass
            report["qmp_messages"] = qmp.messages
            report["guest_shutdown_observed"] = has_guest_shutdown(qmp.messages)
            qmp.sock.close()
        if gdb:
            (out / "gdb-transcript.json").write_text(
                json.dumps(gdb.transcript, indent=2) + "\n")
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
            report["serial_sha256"] = sha256(out / "serial.log")
        report["elapsed_seconds"] = round(time.monotonic()-start, 3)
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items()
                      if key not in ("qmp_messages", "command", "guest_result",
                                     "qemu_cpus", "gdb_target_xml",
                                     "compile_command")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
