#!/usr/bin/env python3
"""Continue real Linux UFS I/O across an active or runtime-suspended checkpoint."""
# SPDX-License-Identifier: GPL-2.0-only
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import secrets
import subprocess
import time
import traceback

from boot_smoke import cpio_entry, sha256
from validate_machine import QMP, ValidationError


DISK_BYTES = 64 * 1024 * 1024
PAYLOAD_BYTES = 32768
SOURCE_OFFSET = 4 * 1024 * 1024
TARGET_OFFSET = 8 * 1024 * 1024
SOURCE_CHECKS = [
    "procfs", "sysfs", "devtmpfs", "eight_online_cpus", "qcom_host_driver",
    "qmp_phy_driver", "gcc_driver", "apps_smmu_driver",
    "ufs_translated_dma_domain_before", "ufs_block_discovered",
    "logical_sector_4096_before", "capacity_64_mib_before", "aligned_buffers",
    "source_direct_write", "source_flush", "source_direct_readback",
    "source_close", "source_boot_id", "source_monotonic", "control_tty_raw",
    "stored_host_nonce",
]
TARGET_CHECKS = [
    "resume_control_received", "preserved_pid1", "preserved_boot_id",
    "preserved_monotonic", "eight_online_cpus_after",
    "ufs_translated_dma_domain_after", "target_open", "logical_sector_4096_after",
    "capacity_64_mib_after", "target_reads_source_payload", "target_direct_write",
    "target_flush", "target_direct_readback", "target_close", "target_reopen",
    "target_reopen_source_payload", "target_reopen_target_payload",
    "target_final_close",
]
PM_SOURCE_CHECKS = (
    "pm_source_reopen pm_source_block_identity pm_host_control_on "
    "pm_lun_control_on pm_host_active pm_lun_active pm_target_hibern8 "
    "pm_scsi_autosuspend pm_host_control_auto pm_source_host_suspended "
    "pm_source_lun_suspended pm_source_wlun_suspended pm_source_stays_suspended"
).split()
PM_TARGET_CHECKS = (
    "pm_target_host_suspended pm_target_lun_suspended pm_target_wlun_suspended "
    "pm_suspended_time_preserved pm_open_fd_preserved pm_direct_read_wakes "
    "pm_target_host_active pm_target_lun_active pm_retained_fd_close"
).split()
CHECK_RE = re.compile(r"TB371FC_UFS_MIGRATION_CHECK (PASS|FAIL) (\w+)")
CHECKPOINT_RE = re.compile(
    r"TB371FC_UFS_MIGRATION_CHECKPOINT nonce=([0-9a-f]{32}) "
    r"boot_id=([0-9a-f-]{36}) monotonic_ns=(\d+) checks=(\d+) failures=(\d+)")
RESULT_RE = re.compile(
    r"TB371FC_UFS_MIGRATION_RESULT (PASS|FAIL) nonce=(\S+) "
    r"boot_id=(\S+) checks=(\d+) failures=(\d+)")


def require(condition, message):
    if not condition:
        raise ValidationError(message)


class RecordedQMP(QMP):
    """Retain messages including asynchronous events the base class consumes."""

    def __init__(self, path, process, timeout, transcript):
        self.transcript = transcript
        super().__init__(path, process, timeout)

    def receive(self):
        reply = super().receive()
        self.transcript.append({"direction": "receive", "message": reply})
        return reply

    def command(self, name, arguments=None):
        self.transcript.append({"direction": "send", "command": name,
                                "arguments": arguments})
        return super().command(name, arguments)

    def events(self):
        return [item["message"]["event"] for item in self.transcript
                if item["direction"] == "receive" and "event" in item["message"]]


class Guest:
    def __init__(self, name, args, initrd, disk):
        self.name = name
        self.directory = args.out / name
        self.directory.mkdir()
        self.serial = self.directory / "serial.log"
        self.debug = self.directory / "qemu-debug.log"
        self.socket = self.directory / "qmp.sock"
        self.process = self.qmp = self.log = None
        self.transcript = []
        self.command = [
            str(args.qemu), "-M", "tb371fc", "-accel", "tcg,thread=multi",
            "-m", "8G", "-smp", "8", "-display", "none", "-monitor", "none",
            "-serial", "stdio", "-no-reboot", "-qmp",
            f"unix:{self.socket},server=on,wait=off", "-kernel", str(args.kernel),
            "-initrd", str(initrd), "-append",
            "earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 "
            "rdinit=/init loglevel=7 panic=-1 nokaslr",
            "-blockdev", json.dumps({"driver": "file", "node-name": "f0",
                                    "filename": str(disk)}),
            "-blockdev", "driver=raw,node-name=d0,file=f0",
            "-device", "ufs-lu,bus=ufs0,drive=d0,lun=0",
            "-d", "unimp,guest_errors", "-D", str(self.debug),
        ]
        if name == "target":
            self.command += ["-incoming", "defer", "-S"]
        self.receipt = {"command": self.command, "qmp": self.transcript}

    def start(self):
        self.log = self.serial.open("wb")
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                        stdout=self.log, stderr=subprocess.STDOUT)
        self.receipt["pid"] = self.process.pid
        self.receipt["started_monotonic"] = time.monotonic()
        self.qmp = RecordedQMP(self.socket, self.process, 10, self.transcript)

    def text(self):
        return self.serial.read_text(errors="replace") if self.serial.exists() else ""

    def wait_serial(self, marker, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            text = self.text()
            if any(marker in line and line.endswith("\n")
                   for line in text.splitlines(keepends=True)):
                return text
            require(self.process.poll() is None,
                    f"{self.name} exited before {marker!r}: {self.process.returncode}")
            require("TB371FC_UFS_MIGRATION_RESULT " not in text,
                    f"{self.name} ended before {marker!r}")
            time.sleep(0.05)
        raise TimeoutError(f"{self.name} did not emit {marker!r}")

    def send(self, command):
        self.receipt.setdefault("serial_input", []).append(command)
        self.process.stdin.write((command + "\n").encode("ascii"))
        self.process.stdin.flush()

    def stop(self):
        before = self.qmp.events().count("STOP")
        self.qmp.command("stop")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.qmp.command("query-status")
            if self.qmp.events().count("STOP") > before:
                require(status["status"] == "paused" and not status["running"],
                        f"{self.name} did not stop: {status}")
                self.receipt["stopped_status"] = status
                return
            time.sleep(0.02)
        raise TimeoutError(f"{self.name} STOP event not observed")

    def finish(self):
        self.qmp.command("quit")
        code = self.process.wait(timeout=10)
        self.receipt["exited_monotonic"] = time.monotonic()
        self.receipt["exit_code"] = code
        require(code == 0, f"{self.name} exited with {code}")

    def cleanup(self):
        if self.process and self.process.poll() is None:
            self.receipt["aborted_after_failure"] = True
            if self.qmp:
                try:
                    self.receipt["failure_status"] = self.qmp.command("query-status")
                    self.receipt["failure_registers"] = self.qmp.command(
                        "human-monitor-command", {"command-line": "info registers"})
                    self.qmp.command("quit")
                    self.process.wait(timeout=5)
                except Exception as error:
                    self.receipt["cleanup_error"] = f"{type(error).__name__}: {error}"
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
        if self.qmp:
            self.qmp.close()
        if self.process:
            self.receipt["exit_code"] = self.process.returncode
            self.process.stdin.close()
        if self.log:
            self.log.close()
        self.receipt["guest_checks"] = CHECK_RE.findall(self.text())
        self.receipt["guest_verdicts"] = RESULT_RE.findall(self.text())
        for path in (self.serial, self.debug):
            if path.exists():
                self.receipt[path.name + "_sha256"] = sha256(path)
        (self.directory / "receipt.json").write_text(
            json.dumps(self.receipt, indent=2) + "\n")


def wait_migration(guest, timeout):
    deadline = time.monotonic() + timeout
    state = {}
    while time.monotonic() < deadline:
        state = guest.qmp.command("query-migrate")
        guest.receipt["migration"] = state
        if state.get("status") == "completed":
            return state
        require(state.get("status") not in ("failed", "cancelled"),
                f"{guest.name} migration failed: {state}")
        time.sleep(0.05)
    raise TimeoutError(f"{guest.name} migration did not complete: {state}")


def verify_disk(path, include_target):
    payloads = [(SOURCE_OFFSET, bytes((i * 37 + 0x61) & 255
                                     for i in range(PAYLOAD_BYTES)))]
    if include_target:
        payloads.append((TARGET_OFFSET, bytes((i * 53 + 0xb7) & 255
                                             for i in range(PAYLOAD_BYTES))))
    expected_hash, observed_hash = hashlib.sha256(), hashlib.sha256()
    first_mismatch = None
    compared = 0
    with path.open("rb") as stream:
        for offset in range(0, DISK_BYTES, 1 << 20):
            expected = bytearray(1 << 20)
            for start, data in payloads:
                if offset <= start < offset + len(expected):
                    expected[start - offset:start - offset + len(data)] = data
            observed = stream.read(len(expected))
            compared += len(observed)
            expected_hash.update(expected)
            observed_hash.update(observed)
            if first_mismatch is None and observed != expected:
                first_mismatch = offset + next((i for i, pair in
                    enumerate(zip(observed, expected)) if pair[0] != pair[1]),
                    len(observed))
    size = path.stat().st_size
    return {"passed": size == DISK_BYTES and first_mismatch is None,
            "size_bytes": size, "bytes_compared": compared,
            "first_mismatch_offset": first_mismatch,
            "sha256": observed_hash.hexdigest(),
            "expected_sha256": expected_hash.hexdigest(),
            "payloads": [{"offset": offset, "length": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()}
                         for offset, data in payloads],
            "all_other_bytes_expected": 0}


def require_suspended(observation, running):
    require(observation["vm"]["running"] is running,
            "Unexpected VM run state in " + observation["phase"])
    require(observation["link_state"] == "hibern8",
            "UFS lost Hibern8 in " + observation["phase"])
    for name in ("core-clk", "iface-clk", "unipro-clk"):
        require(observation["clock_frequency_display"][name] == "0 Hz",
                f"UFS {name} not gated in {observation['phase']}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--runtime-suspended", action="store_true",
                        help="Checkpoint after real Linux UFS runtime suspend, then wake by I/O")
    args = parser.parse_args()
    if not 0 < args.timeout <= 300:
        parser.error("--timeout must be in (0, 300]")
    args.qemu, args.kernel, args.out = (p.resolve() for p in
                                      (args.qemu, args.kernel, args.out))
    args.out.mkdir(parents=True, exist_ok=False)
    source = Path(__file__).resolve().parents[1] / "tests/ufs-migration-guest.c"
    init, initrd, disk, stream = (args.out / name for name in
        ("init", "initramfs.cpio.gz", "disk.raw", "checkpoint.migration"))
    input_paths = {"qemu": args.qemu, "kernel": args.kernel,
                   "kernel_build_receipt": args.kernel.parent / "build.json",
                   "guest_source": source, "harness": Path(__file__).resolve(),
                   "init": init, "initramfs": initrd}
    source_checks = SOURCE_CHECKS + (PM_SOURCE_CHECKS if args.runtime_suspended else [])
    target_checks = (TARGET_CHECKS[:6] + PM_TARGET_CHECKS + TARGET_CHECKS[6:]
                     if args.runtime_suspended else TARGET_CHECKS)
    input_paths.update({"boot_smoke_module": Path(__file__).with_name("boot_smoke.py"),
                        "qmp_module": Path(__file__).with_name("validate_machine.py")})
    if args.runtime_suspended:
        input_paths.update({
            "runtime_pm_module": Path(__file__).with_name("boot_ufs_runtime_pm.py"),
            "kernel_config": args.kernel.parent / "kernel.config",
        })
    started = time.monotonic()
    guests = []
    report = {
        "schema_version": 1, "passed": False,
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "Real Linux qcom UFS and translated SMMU DMA after a quiescent process checkpoint",
        "guest_shutdown_tested": False, "inflight_ufs_migration_tested": False,
        "external_disk_bytes_migrated": False,
        "expected_source_checks": source_checks, "expected_target_checks": target_checks,
        "runtime_suspended_migration_requested": args.runtime_suspended,
        "runtime_suspended_migration_tested": False,
        "system_suspend_tested": False, "host_observations": [],
        "limitations": [
            "One stopped checkpoint, one synthetic 64 MiB LUN and a generated device tree.",
            "The source exits before the receiver opens the same disk with normal locking.",
            "No firmware, stock Android, native device tree or hardware equivalence is tested.",
        ],
    }
    try:
        if args.runtime_suspended:
            # Reuse the independent driver's read-only QMP observations and
            # pinned kernel/config validation, without its process lifecycle.
            from boot_ufs_runtime_pm import snapshot, validate_kernel
            validate_kernel(args.kernel, input_paths["kernel_config"],
                            input_paths["kernel_build_receipt"])
            require("CONFIG_PROC_SYSCTL=y" in
                    input_paths["kernel_config"].read_text().splitlines(),
                    "Runtime-suspended continuation requires the boot-ID interface")
            report["scope"] = ("Real Linux UFS runtime-suspended process checkpoint, "
                               "preserved open fd and ordinary direct-I/O wake")
            report["limitations"].append(
                "UFS queues remain quiescent; system suspend, ICE and physical-media power behavior are not tested.")
        receipt_path = args.kernel.parent / "build.json"
        receipt = json.loads(receipt_path.read_text())
        require(receipt["source_version"] == "6.12.114" and
                receipt["source_tar_sha256"] ==
                "7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3" and
                receipt["patches_applied_by_script"] == [] and
                receipt["build_completed"] is True and
                receipt["image_sha256"] == sha256(args.kernel),
                "Kernel requires the matching pinned, unpatched public build.json")
        report["kernel_build_receipt"] = receipt
        report["kernel_build_receipt_sha256"] = sha256(receipt_path)
        report["compile_command"] = ["aarch64-linux-gnu-gcc", "-static", "-Os",
                                     "-Wall", "-Wextra", "-Werror", str(source),
                                     "-o", str(init)]
        if args.runtime_suspended:
            report["compile_command"].insert(1, "-DTB371FC_RUNTIME_SUSPENDED=1")
        with (args.out / "compile.log").open("wb") as log:
            subprocess.run(report["compile_command"], check=True, stdout=log,
                           stderr=subprocess.STDOUT, timeout=60)
        archive = cpio_entry("init", init.read_bytes(), 0o100755, 1)
        for i, name in enumerate(("dev", "proc", "sys"), 2):
            archive += cpio_entry(name, b"", 0o040755, i)
        archive += cpio_entry("dev/console", b"", 0o020600, 5, 5, 1)
        archive += cpio_entry("TRAILER!!!", b"", 0, 6)
        initrd.write_bytes(gzip.compress(archive, mtime=0))
        report["input_sha256_before"] = {
            name: sha256(path) for name, path in input_paths.items()}
        with disk.open("xb") as output:
            output.truncate(DISK_BYTES)
        nonce = secrets.token_hex(16)
        report["nonce"] = nonce
        sender = Guest("source", args, initrd, disk)
        guests.append(sender)
        sender.start()
        sender.wait_serial("TB371FC_UFS_MIGRATION_READY", args.timeout)
        if args.runtime_suspended:
            report["host_observations"].append(snapshot(sender.qmp, "source_baseline"))
        sender.send("STORE:" + nonce)
        text = sender.wait_serial("TB371FC_UFS_MIGRATION_CHECKPOINT ", args.timeout)
        checkpoint = CHECKPOINT_RE.findall(text)
        report["checkpoint_markers"] = checkpoint
        require(len(checkpoint) == 1 and checkpoint[0][0] == nonce and
                int(checkpoint[0][2]) > 0 and
                checkpoint[0][3:] == (str(len(source_checks)), "0"),
                f"Invalid source checkpoint marker: {checkpoint}")
        require(CHECK_RE.findall(text) == [("PASS", name) for name in source_checks],
                "Source check names, order or verdicts differ")
        require(text.count("TB371FC_UFS_MIGRATION_BOOT pid=1") == 1 and
                "TB371FC_UFS_MIGRATION_RESULT " not in text,
                "Source did not reach its unique PID 1 checkpoint")
        if args.runtime_suspended:
            sleeping = snapshot(sender.qmp, "source_suspended_running")
            report["host_observations"].append(sleeping)
            require_suspended(sleeping, running=True)
        sender.stop()
        if args.runtime_suspended:
            saved_pm = snapshot(sender.qmp, "source_suspended_paused")
            report["host_observations"].append(saved_pm)
            require_suspended(saved_pm, running=False)
        report["source_disk"] = verify_disk(disk, include_target=False)
        require(report["source_disk"]["passed"], "Source disk differs from exact expected image")
        sender.qmp.command("migrate", {"uri": "file:" + str(stream)})
        wait_migration(sender, args.timeout)
        sender.receipt["postmigration_status"] = sender.qmp.command("query-status")
        require(sender.receipt["postmigration_status"]["status"] == "postmigrate" and
                not sender.receipt["postmigration_status"]["running"],
                "Source did not enter postmigrate")
        report["migration_sha256_before_load"] = sha256(stream)
        sender.finish()
        require(sender.process.poll() == 0, "Source has not released the disk")

        receiver = Guest("target", args, initrd, disk)
        guests.append(receiver)
        receiver.start()
        report["source_exited_before_target_started"] = (
            sender.receipt["exited_monotonic"] < receiver.receipt["started_monotonic"])
        require(report["source_exited_before_target_started"], "Source and target overlapped")
        receiver.qmp.command("migrate-incoming", {"uri": "file:" + str(stream)})
        wait_migration(receiver, args.timeout)
        receiver.receipt["loaded_status"] = receiver.qmp.command("query-status")
        require(receiver.receipt["loaded_status"]["status"] == "paused" and
                not receiver.receipt["loaded_status"]["running"] and
                "RESUME" not in receiver.qmp.events(),
                "Receiver ran before the explicit cont command")
        require("TB371FC_UFS_MIGRATION_" not in receiver.text(),
                "Receiver emitted guest markers before explicit resume")
        if args.runtime_suspended:
            loaded_pm = snapshot(receiver.qmp, "target_loaded_paused")
            report["host_observations"].append(loaded_pm)
            require_suspended(loaded_pm, running=False)
            report["runtime_pm_state_preserved"] = all(
                saved_pm[key] == loaded_pm[key]
                for key in ("link_state", "clock_frequency_display"))
            require(report["runtime_pm_state_preserved"],
                    "Receiver changed the saved UFS link or clock state")
        receiver.qmp.command("cont")
        if args.runtime_suspended:
            before_wake = snapshot(receiver.qmp, "target_running_before_io")
            report["host_observations"].append(before_wake)
            require_suspended(before_wake, running=True)
        # Deliberately do not resend the nonce, STORE command, or guest state.
        receiver.send("CONTINUE")
        text = receiver.wait_serial("TB371FC_UFS_MIGRATION_RESULT ", args.timeout)
        verdicts = RESULT_RE.findall(text)
        require(CHECK_RE.findall(text) == [("PASS", name) for name in target_checks],
                "Target continuation checks, order or verdicts differ")
        require(verdicts == [("PASS", nonce, checkpoint[0][1],
                             str(len(source_checks) + len(target_checks)), "0")],
                f"Target did not preserve the original nonce, boot ID and counters: {verdicts}")
        require(not any(marker in text for marker in (
            "TB371FC_UFS_MIGRATION_BOOT", "TB371FC_UFS_MIGRATION_READY",
            "TB371FC_UFS_MIGRATION_CHECKPOINT")), "Target rebooted or replayed source markers")
        if args.runtime_suspended:
            report["host_observations"].append(snapshot(receiver.qmp, "target_after_io"))
        receiver.stop()
        report["target_disk"] = verify_disk(disk, include_target=True)
        require(report["target_disk"]["passed"], "Target disk differs from exact expected image")
        receiver.finish()
        report["migration_sha256_after_load"] = sha256(stream)
        require(report["migration_sha256_before_load"] == report["migration_sha256_after_load"],
                "Migration input changed between save and load")
        report["runtime_suspended_migration_tested"] = args.runtime_suspended
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        for guest in guests:
            try:
                guest.cleanup()
            except Exception:
                report.setdefault("cleanup_errors", []).append(traceback.format_exc())
                report["passed"] = False
        report["guests"] = {guest.name: guest.receipt for guest in guests}
        if args.runtime_suspended:
            report["kernel_failure_lines"] = [
                {"guest": guest.name, "line": line}
                for guest in guests for line in guest.text().splitlines()
                if re.search(r"ufshcd.*failed|hibern8.*failed|I/O error|timed out|Kernel panic",
                             line, re.I)]
            if report["kernel_failure_lines"]:
                report["passed"] = False
        for name, path in {**input_paths, "disk": disk, "migration": stream}.items():
            if path.is_file():
                report[name + "_sha256"] = sha256(path)
                report[name + "_size_bytes"] = path.stat().st_size
        if "input_sha256_before" in report:
            report["inputs_unchanged"] = all(
                report.get(name + "_sha256") == digest
                for name, digest in report["input_sha256_before"].items())
            if not report["inputs_unchanged"]:
                report["passed"] = False
                report["input_change_error"] = "Immutable runtime input changed during the test"
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["finished_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

