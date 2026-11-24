#!/usr/bin/env python3
"""Validate Linux UFS runtime suspend, ordinary block-I/O wake and media integrity."""
# SPDX-License-Identifier: GPL-2.0-only
import argparse
import datetime
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import socket
import subprocess
import time

if not __debug__:
    raise SystemExit("This acceptance fixture requires assertions; do not use -O or PYTHONOPTIMIZE")

LINUX_VERSION = "6.12.114"
LINUX_SOURCE_SHA256 = "7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3"
REQUIRED_KERNEL_OPTIONS = (
    "CONFIG_PM", "CONFIG_BLK_PM", "CONFIG_SCSI", "CONFIG_BLK_DEV_SD",
    "CONFIG_SCSI_UFSHCD", "CONFIG_SCSI_UFSHCD_PLATFORM", "CONFIG_SCSI_UFS_QCOM",
    "CONFIG_PHY_QCOM_QMP_UFS", "CONFIG_SYSFS", "CONFIG_PROC_FS", "CONFIG_DEVTMPFS",
)
EXPECTED_CHECKS = (
    "procfs sysfs devtmpfs ufs_disk_open qcom_ufs_bound block_identity_before "
    "capacity_64mib_before sector_4096_before host_control_on lun_control_on "
    "host_active_before lun_active_before runtime_target_hibern8 aligned_buffers "
    "write_before_suspend flush_before_suspend read_before_suspend control_tty "
    "baseline_ack scsi_autosuspend_enabled host_control_auto "
    "controller_runtime_suspended lun_runtime_suspended device_wlun_runtime_suspended "
    "controller_stays_suspended wake_ack suspended_immediately_before_io "
    "direct_read_wakes_and_preserves_payload controller_runtime_active_after_io "
    "lun_runtime_active_after_io write_after_resume flush_after_resume read_after_resume "
    "close_after_resume reopen_after_resume same_block_identity capacity_64mib_after "
    "reopened_original_payload reopened_new_payload final_close"
).split()


def sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for data in iter(lambda: f.read(1 << 20), b""):
            h.update(data)
    return h.hexdigest()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def cpio_entry(name, data, mode, inode, rdev_major=0, rdev_minor=0):
    filename = name.encode() + b"\0"
    fields = [inode, mode, 0, 0, 1, 0, len(data), 0, 0,
              rdev_major, rdev_minor, len(filename), 0]
    part = b"070701" + b"".join(f"{v:08x}".encode() for v in fields) + filename
    part += b"\0" * (-len(part) % 4)
    part += data
    return part + b"\0" * (-len(data) % 4)


class QMP:
    def __init__(self, path, process, receipts):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.settimeout(10)
        self.sequence = 0
        self.receipts = receipts
        end = time.monotonic() + 15
        while True:
            try:
                self.s.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if process.poll() is not None or time.monotonic() > end:
                    raise RuntimeError("QEMU did not open QMP")
                time.sleep(.05)
        self.stream = self.s.makefile("rb")
        greeting = self.receive()
        if "QMP" not in greeting:
            raise RuntimeError("No QMP greeting")
        self.command("qmp_capabilities")

    def receive(self):
        line = self.stream.readline(1048577)
        if not line or len(line) > 1048576:
            raise RuntimeError("QMP EOF or oversized message")
        obj = json.loads(line)
        self.receipts.append({"at": now(), "direction": "receive", "message": obj})
        return obj

    def command(self, name, args=None):
        # Deliberate allowlist: only read state, negotiate QMP and end the run.
        if name not in {"qmp_capabilities", "qom-get", "query-status", "quit", "human-monitor-command"}:
            raise RuntimeError("Mutating device command forbidden: " + name)
        if name == "human-monitor-command" and args != {"command-line": "info qtree"}:
            raise RuntimeError("Only read-only info qtree is allowed")
        self.sequence += 1
        msg = {"execute": name, "id": self.sequence}
        if args is not None:
            msg["arguments"] = args
        self.receipts.append({"at": now(), "direction": "send", "message": msg})
        self.s.sendall((json.dumps(msg) + "\n").encode())
        while True:
            obj = self.receive()
            if "event" in obj:
                continue
            if obj.get("id") != self.sequence or "error" in obj:
                raise RuntimeError("Bad QMP reply " + str(obj))
            return obj["return"]

    def close(self):
        self.stream.close()
        self.s.close()


def snapshot(qmp, phase):
    result = {"phase": phase, "at": now(), "method": "qom-get, query-status and read-only info qtree"}
    result["vm"] = qmp.command("query-status")
    result["link_state"] = qmp.command("qom-get", {"path": "/machine/ufs", "property": "link-state"})
    # The qtest-clock-period QOM property exists only with qtest_enabled().
    # Normal TCG must retain its real Linux timing path. info qtree exposes
    # clock_display_freq -> clock_get_hz without changing any device state.
    tree = qmp.command("human-monitor-command", {"command-line": "info qtree"})
    lines = tree.splitlines()
    hosts = [(i, len(m.group(1))) for i, line in enumerate(lines)
             if (m := re.match(r'^(\s*)dev: qcom-sm8250-ufs, id "[^"]*"$', line))]
    if len(hosts) != 1:
        raise RuntimeError("Expected exactly one UFS device in info qtree")
    start, indent = hosts[0]
    block = []
    clocks = {}
    for line in lines[start + 1:]:
        depth = len(line) - len(line.lstrip())
        if line.strip() and depth <= indent:
            break
        block.append(line)
        m = re.match(r'^\s*clock-in "([^"]+)" freq_hz=(.+)$', line)
        if m and depth == indent + 2:
            if m.group(1) in clocks:
                raise RuntimeError("Duplicate clock in UFS qtree")
            clocks[m.group(1)] = m.group(2)
    for name in ("core-clk", "iface-clk", "unipro-clk", "phy-aux-clk", "ref-clk"):
        if name not in clocks:
            raise RuntimeError("Missing UFS clock in qtree: " + name)
    result["clock_frequency_display"] = clocks
    result["ufs_qtree_block"] = [lines[start]] + block
    result["qtree_sha256"] = hashlib.sha256(tree.encode()).hexdigest()
    result["qtree_raw_in_qmp_receipts"] = True
    return result


def compare_disk(path):
    actual = path.read_bytes()
    expected = bytearray(64 * 1024 * 1024)
    payloads = []
    for offset, mult, salt in ((4 * 1024 * 1024, 37, 0x61), (8 * 1024 * 1024, 53, 0xb7)):
        data = bytes((i * mult + salt) & 255 for i in range(32768))
        expected[offset:offset + len(data)] = data
        payloads.append({"offset": offset, "length": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    passed = actual == expected
    mismatch = next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b), None) if not passed else None
    return {"passed": passed, "size_bytes": len(actual), "bytes_compared": min(len(actual), len(expected)),
            "first_mismatch_offset": mismatch, "sha256": hashlib.sha256(actual).hexdigest(),
            "expected_sha256": hashlib.sha256(expected).hexdigest(), "payloads": payloads,
            "all_other_bytes_expected": 0}


def validate_kernel(kernel, config, receipt_path):
    """Bind the public build receipt, Image and effective runtime-PM config."""
    receipt = json.loads(receipt_path.read_text())
    if not (receipt.get("source_version") == LINUX_VERSION and
            receipt.get("source_tar_sha256") == LINUX_SOURCE_SHA256 and
            receipt.get("patches_applied_by_script") == [] and
            receipt.get("build_completed") is True and
            receipt.get("image_sha256") == sha(kernel) and
            receipt.get("image_bytes") == kernel.stat().st_size and
            receipt.get("effective_config_sha256") == sha(config)):
        raise ValueError("Kernel requires the matching pinned, unpatched public build.json and kernel.config")
    values = {}
    for line in config.read_text().splitlines():
        if line.startswith("CONFIG_") and "=" in line:
            name, value = line.split("=", 1)
            if name in values:
                raise ValueError("Duplicate kernel configuration setting: " + name)
            values[name] = value
    missing = [name for name in REQUIRED_KERNEL_OPTIONS if values.get(name) != "y"]
    if missing:
        raise ValueError("Runtime-PM kernel requires built-in settings: " + ", ".join(missing))
    return receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qemu", type=Path, required=True)
    p.add_argument("--kernel", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--timeout", type=float, default=90)
    args = p.parse_args()
    if not 0 < args.timeout <= 300:
        p.error("--timeout must be in (0, 300]")
    qemu, kernel = args.qemu.resolve(), args.kernel.resolve()
    kernel_config = kernel.parent / "kernel.config"
    kernel_receipt = kernel.parent / "build.json"
    guest_source = Path(__file__).resolve().parents[1] / "tests/ufs-runtime-pm-guest.c"
    for name, path in (("QEMU", qemu), ("Image", kernel),
                       ("kernel.config", kernel_config), ("build.json", kernel_receipt),
                       ("guest source", guest_source)):
        if not path.is_file():
            p.error(f"{name} is not a file: {path}")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(guest_source, out / "guest.c")
    shutil.copyfile(__file__, out / "run.py")
    inputs = {"qemu": qemu, "kernel": kernel, "kernel_config": kernel_config,
              "kernel_receipt": kernel_receipt, "guest_source": out / "guest.c", "harness": out / "run.py"}
    result = {"schema_version": 1, "kind": "sm8250-real-linux-ufs-runtime-pm", "started_utc": now(),
              "scope": "One real Linux device runtime suspend and ordinary block-I/O wake on a fresh synthetic 64 MiB LUN",
              "system_suspend_tested": False, "inflight_checkpoint_tested": False,
              "frozen_inputs": {k: str(v) for k, v in inputs.items()},
              "input_sha256_before": {k: sha(v) for k, v in inputs.items()},
              "expected_guest_checks": EXPECTED_CHECKS, "qmp_receipts": [], "host_observations": [],
              "guest_control_inputs": [], "passed": False,
              "limitations": ["Generated QEMU DT and upstream public Linux; no native Android or physical tablet was used.",
                  "No QEMU or kernel rebuild, device-state injection, MMIO write or QMP state forcing is used.",
                  "SCSI device START STOP is the existing accepted control operation; this is a driver/clock/PHY/link and data-integrity contract, not physical-media energy behavior.",
                  "This test does not cover whole-system suspend or migration during Linux runtime suspend."]}
    proc = None
    qmp = None
    started = time.monotonic()
    try:
        result["kernel_build_receipt"] = validate_kernel(kernel, kernel_config, kernel_receipt)
        result["kernel_required_options"] = {name: "y" for name in REQUIRED_KERNEL_OPTIONS}
        source_check_names = re.findall(r'check\("([^\"]+)"', (out / "guest.c").read_text())
        assert source_check_names == EXPECTED_CHECKS
        cc = ["aarch64-linux-gnu-gcc", "-static", "-Os", "-Wall", "-Wextra", "-Werror", str(out / "guest.c"), "-o", str(out / "init")]
        result["compile_command"] = cc
        build = subprocess.run(cc, capture_output=True, text=True, timeout=45)
        (out / "compile.log").write_text(build.stdout + build.stderr)
        result["compile_exit_code"] = build.returncode
        assert build.returncode == 0, "Guest compile failed"
        archive = cpio_entry("init", (out / "init").read_bytes(), 0o100755, 1)
        for i, name in enumerate(("dev", "proc", "sys"), 2):
            archive += cpio_entry(name, b"", 0o040755, i)
        archive += cpio_entry("dev/console", b"", 0o020600, 5, 5, 1)
        archive += cpio_entry("TRAILER!!!", b"", 0, 6)
        (out / "initramfs.cpio.gz").write_bytes(gzip.compress(archive, mtime=0))
        with (out / "disk.raw").open("xb") as f:
            f.truncate(64 * 1024 * 1024)
        result["empty_disk_sha256"] = sha(out / "disk.raw")
        result["init_sha256"] = sha(out / "init")
        result["initramfs_sha256"] = sha(out / "initramfs.cpio.gz")
        cmd = [str(qemu), "-M", "tb371fc", "-accel", "tcg,thread=multi", "-m", "8G", "-smp", "8",
               "-display", "none", "-monitor", "none", "-serial", "stdio", "-no-reboot",
               "-qmp", f"unix:{out}/qmp.sock,server=on,wait=off", "-kernel", str(kernel),
               "-initrd", str(out / "initramfs.cpio.gz"), "-append",
               "earlycon=qcom_geni,0xa90000 console=ttyMSM0,115200n8 rdinit=/init loglevel=7 panic=-1 nokaslr",
               "-blockdev", json.dumps({"driver": "file", "node-name": "f0", "filename": str(out / "disk.raw")}),
               "-blockdev", "driver=raw,node-name=d0,file=f0", "-device", "ufs-lu,bus=ufs0,drive=d0,lun=0",
               "-d", "unimp,guest_errors", "-D", str(out / "qemu-debug.log")]
        result["command"] = cmd
        with (out / "serial.log").open("wb") as log:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT)
            qmp = QMP(out / "qmp.sock", proc, result["qmp_receipts"])
            deadline = time.monotonic() + args.timeout

            def wait_marker(marker):
                while proc.poll() is None and time.monotonic() < deadline:
                    text = (out / "serial.log").read_text(errors="replace")
                    if marker in text:
                        return
                    if "TB371FC_RPM_RESULT " in text:
                        raise RuntimeError("Guest ended before " + marker)
                    time.sleep(.05)
                raise TimeoutError("Did not observe " + marker)

            def send(value):
                proc.stdin.write((value + "\n").encode())
                proc.stdin.flush()
                result["guest_control_inputs"].append({"at": now(), "value": value})

            wait_marker("TB371FC_RPM_STAGE BASELINE")
            result["host_observations"].append(snapshot(qmp, "baseline"))
            send("SUSPEND")
            wait_marker("TB371FC_RPM_STAGE SUSPENDED")
            suspended = snapshot(qmp, "suspended")
            result["host_observations"].append(suspended)
            result["host_sleep_checks"] = {
                "vm_remains_running": suspended["vm"]["running"] is True,
                "ufs_link_hibern8": suspended["link_state"] == "hibern8",
                "core_clock_gated": suspended["clock_frequency_display"]["core-clk"] == "0 Hz",
                "iface_clock_gated": suspended["clock_frequency_display"]["iface-clk"] == "0 Hz",
                "unipro_clock_gated": suspended["clock_frequency_display"]["unipro-clk"] == "0 Hz",
            }
            send("READ_WAKE")
            wait_marker("TB371FC_RPM_RESULT ")
            result["host_observations"].append(snapshot(qmp, "after_io"))
            qmp.command("quit")
            proc.wait(timeout=10)
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {e}"
    finally:
        if qmp:
            if proc and proc.poll() is None:
                try:
                    qmp.command("quit")
                    proc.wait(timeout=5)
                except Exception as e:
                    result["cleanup_error"] = str(e)
            qmp.close()
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if proc:
            result["qemu_exit_code"] = proc.returncode
        text = (out / "serial.log").read_text(errors="replace") if (out / "serial.log").exists() else ""
        result["guest_checks"] = re.findall(r"TB371FC_RPM_CHECK (PASS|FAIL) ([a-z0-9_]+) errno=(\d+)", text)
        result["guest_verdicts"] = re.findall(r"TB371FC_RPM_RESULT (PASS|FAIL) checks=(\d+) failures=(\d+)", text)
        result["guest_observation_lines"] = [s for s in text.splitlines() if s.startswith(("TB371FC_RPM_OBSERVE ", "TB371FC_RPM_SUSPENDED_TIME ", "TB371FC_RPM_SCSI ", "TB371FC_RPM_DEVICE_WLUN ", "TB371FC_RPM_SYSFS_WRITE ", "TB371FC_RPM_IDLE_WAIT_MS "))]
        result["kernel_failure_lines"] = [s for s in text.splitlines() if re.search(r"ufshcd.*failed|hibern8.*failed|I/O error|timed out|Kernel panic", s, re.I)]
        if (out / "disk.raw").exists():
            result["host_disk"] = compare_disk(out / "disk.raw")
        result["input_sha256_after"] = {k: sha(v) for k, v in inputs.items()}
        result["inputs_unchanged"] = result["input_sha256_before"] == result["input_sha256_after"]
        result["passed"] = ("error" not in result and result.get("qemu_exit_code") == 0 and
            [v[1] for v in result["guest_checks"]] == EXPECTED_CHECKS and
            all(v[0] == "PASS" for v in result["guest_checks"]) and
            result["guest_verdicts"] == [("PASS", str(len(EXPECTED_CHECKS)), "0")] and
            all(result.get("host_sleep_checks", {}).values()) and len(result.get("host_sleep_checks", {})) == 5 and
            result.get("host_disk", {}).get("passed") is True and result["inputs_unchanged"] and
            not result["kernel_failure_lines"])
        result["logs_sha256"] = {name: sha(out / name) for name in ("serial.log", "compile.log", "qemu-debug.log") if (out / name).exists()}
        result["completed_utc"] = now()
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({"passed": result["passed"], "checks": len(result["guest_checks"]), "error": result.get("error"), "out": str(out)}), flush=True)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
