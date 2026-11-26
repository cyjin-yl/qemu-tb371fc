#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Observe caller-supplied original Android boot with per-LUN writable overlays.

The prepared manifest supplies the caller-supplied original kernel and ramdisk,
captured complete DT and original boot arguments. Machine/reference properties
must be explicit. No test init, rdinit, console or nokaslr option is injected.
Commands, boot arguments and raw diagnostics are private local run artifacts.
"""
from __future__ import annotations

import argparse
import datetime
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shutil
import stat
import subprocess
import tempfile
import time

from boot_qmp import QMP, connect
from legacy_printk import capture_kernel_log

MAX_QMP_MESSAGES = 32768
MAX_QMP_BUFFER = 1024 * 1024
MAX_STAGE_RECORDS = 64
KERNEL_FAILURE = re.compile(
    r"(?:Kernel panic - not syncing|Unable to handle kernel |"
    r"Internal error:.*(?:Oops|BUG|synchronous external abort|undefined instruction)|"
    r"\bOops:|\bBUG: (?:unable to handle|scheduling while atomic|soft lockup)|"
    r"Attempted to kill init!)", re.I)
HARDWARE_ERROR = re.compile(
    r"(?:Invalid (?:read|write) at addr|unassigned (?:read|write)|"
    r"unsupported (?:read|write)|synchronous external abort)", re.I)
STAGE_PATTERNS = (
    ("kernel_entry", r"(?:Linux version \d+\.|Booting Linux on physical CPU)"),
    ("ramdisk_unpack", r"(?:Trying to unpack rootfs image as initramfs|Unpacking initramfs|Freeing initrd memory)"),
    ("init_handoff", r"(?:Run /init as init process|Starting init: /init)"),
    ("android_first_stage_init", r"\binit(?: first stage|: first stage) started!?"),
    ("android_second_stage_init", r"\binit(?: second stage|: second stage) started!?"),
    ("android_filesystem_mount", r"(?:fs_mgr.*(?:successfully mounted|Mounted)|init:.*mount.*succeeded)"),
    ("zygote_service_start", r"\binit\b.*starting service ['\"]zygote(?:32|64|_secondary)?['\"]"),
    ("system_server", r"\bSystemServer\b.*(?:Entered the Android system server|System server process is running|Start services)"),
    ("boot_completed_property", r"\binit\b.*processing action\s*\(sys\.boot_completed=1(?:[\s&)])"),
    ("framework_boot_complete", r"\bActivityManager\b.*(?:Finished booting|Boot completed)"),
)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path = Path(path)
    with path.open("x", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def regular_path(value):
    path = Path(value).resolve(strict=True)
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError(f"not a regular file: {path}")
    return path


def executable(value):
    found = shutil.which(str(value))
    if not found:
        raise ValueError(f"executable not found: {value}")
    return str(regular_path(found))


def fingerprint(path):
    st = regular_path(path).stat()
    return {"device": st.st_dev, "inode": st.st_ino, "size": st.st_size,
            "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


def validate_inputs(manifest):
    """Validate again at the runner boundary, even for callers bypassing CLI."""
    if manifest.get("schema") != "tb371fc-android-boot-inputs-v1":
        raise ValueError("unsupported Android input manifest")
    for name in ("kernel", "initrd", "dtb", "bootargs"):
        item = manifest[name]
        path = regular_path(item["path"])
        if str(path) != item["path"]:
            raise ValueError(f"{name} path must be resolved and absolute")
        if type(item["size"]) is not int or path.stat().st_size != item["size"]:
            raise ValueError(f"{name} size changed")
        if digest(path) != item["sha256"]:
            raise ValueError(f"{name} digest changed")
    raw = Path(manifest["bootargs"]["path"]).read_bytes()
    if len(raw) > 65536 or any(x in raw for x in (b"\0", b"\n", b"\r")):
        raise ValueError("bootargs must be one exact NUL-free UTF-8 command line")
    bootargs = raw.decode("utf-8", errors="strict")
    luns = manifest.get("luns", [])
    if not isinstance(luns, list):
        raise ValueError("luns must be a list")
    seen, paths = set(), set()
    for lun in luns:
        number = lun["lun"]
        if type(number) is not int or not 0 <= number < 32 or number in seen:
            raise ValueError("duplicate or unsupported UFS LUN")
        if lun.get("format") != "raw" or lun.get("readonly_original") is not True:
            raise ValueError("factory LUN backings must be explicitly read-only raw files")
        path = regular_path(lun["path"])
        if str(path) != lun["path"] or path in paths:
            raise ValueError("UFS LUN backings must be distinct absolute files")
        count = lun["logical_block_count"]
        if (type(lun.get("logical_block_size")) is not int or
                lun["logical_block_size"] != 4096 or type(count) is not int or count <= 0):
            raise ValueError("this QEMU UFS model requires recorded 4096-byte logical blocks")
        if path.stat().st_size != 4096 * count or lun.get("size") != 4096 * count:
            raise ValueError("UFS backing size differs from the recorded LU geometry")
        seen.add(number)
        paths.add(path)
    return bootargs


def build_plan(manifest, *, qemu, qemu_img, out, qmp_socket, machine,
               global_properties=(), memory="8G", cpus=8, trace_interrupts=False):
    bootargs = validate_inputs(manifest)
    if not isinstance(machine, str) or machine.split(",", 1)[0] != "tb371fc":
        raise ValueError("an explicit tb371fc machine configuration is required")
    if any(x in machine for x in ("\0", "\n", "\r")):
        raise ValueError("invalid machine configuration")
    if manifest.get("luns") and "ufs-enabled=off" in machine.split(","):
        raise ValueError("recorded factory LUNs require the UFS controller to be enabled")
    if type(cpus) is not int or cpus != 8 or memory != "8G":
        raise ValueError("captured TB371FC full-DT runs require eight CPUs and 8G memory")
    globals_copy = list(global_properties)
    if any(not isinstance(x, str) or "=" not in x or
           any(c in x for c in ("\0", "\n", "\r")) for x in globals_copy):
        raise ValueError("invalid explicit global property")
    out = Path(out).resolve()
    command = [str(qemu), "-M", machine, "-accel", "tcg,thread=multi",
               "-m", memory, "-smp", str(cpus), "-display", "none", "-monitor", "none",
               "-serial", "stdio", "-no-reboot", "-no-shutdown", "-S",
               "-qmp", f"unix:{qmp_socket},server=on,wait=off"]
    for value in globals_copy:
        command += ["-global", value]
    command += ["-kernel", manifest["kernel"]["path"], "-initrd", manifest["initrd"]["path"],
                "-dtb", manifest["dtb"]["path"], "-append", bootargs,
                "-d", "unimp,guest_errors,invalid_mem" + (",int,cpu_reset" if trace_interrupts else ""),
                "-D", str(out / "qemu.log")]
    overlays = []
    for lun in sorted(manifest.get("luns", []), key=lambda x: x["lun"]):
        n = lun["lun"]
        base = Path(lun["path"])
        overlay = out / f"ufs-lun-{n}.qcow2"
        nodes = [
            {"driver": "file", "node-name": f"ufs-base-file-{n}", "filename": str(base), "read-only": True},
            {"driver": "raw", "node-name": f"ufs-base-{n}", "file": f"ufs-base-file-{n}", "read-only": True},
            {"driver": "file", "node-name": f"ufs-overlay-file-{n}", "filename": str(overlay)},
            {"driver": "qcow2", "node-name": f"ufs-disk-{n}", "file": f"ufs-overlay-file-{n}", "backing": f"ufs-base-{n}"},
        ]
        for node in nodes:
            command += ["-blockdev", json.dumps(node, separators=(",", ":"))]
        command += ["-device", f"ufs-lu,bus=ufs0,drive=ufs-disk-{n},lun={n}"]
        overlays.append({"lun": n, "base": str(base), "path": str(overlay),
                         "virtual_size": lun["size"], "base_before": fingerprint(base),
                         "base_sha256_supplied": lun.get("sha256"),
                         "create_command": [str(qemu_img), "create", "-q", "-f", "qcow2",
                                            "-F", "raw", "-b", str(base), str(overlay)]})
    return {"schema": "tb371fc-android-boot-plan-v1", "command": command,
            "qmp_socket": str(qmp_socket), "out": str(out), "machine": machine,
            "global_properties": globals_copy, "overlays": overlays,
            "inputs": manifest, "bootargs_modified": False, "test_init_injected": False,
            "qemu_img": str(qemu_img), "trace_interrupts": trace_interrupts}


def prepare_overlays(plan, run=subprocess.run):
    receipts = []
    for item in plan["overlays"]:
        path = Path(item["path"])
        if path.exists():
            raise FileExistsError(path)
        made = run(item["create_command"], check=True, capture_output=True, text=True, timeout=60)
        os.chmod(path, 0o600)
        inspected = run([plan["qemu_img"], "info", "--output=json", str(path)],
                        check=True, capture_output=True, text=True, timeout=30)
        info = json.loads(inspected.stdout)
        backing = info.get("full-backing-filename", info.get("backing-filename"))
        if (info.get("format") != "qcow2" or
                info.get("virtual-size") != item["virtual_size"] or not backing or
                Path(backing).resolve() != Path(item["base"]) or
                info.get("backing-filename-format") != "raw"):
            raise ValueError("qemu-img produced an overlay with unexpected backing or geometry")
        receipts.append({"lun": item["lun"], "create_returncode": made.returncode,
                         "info": info, "create_stderr": made.stderr})
    return receipts


class BoundedQMP(QMP):
    def __init__(self, sock):
        super().__init__(sock)
        self.first_command_error = None

    def command(self, name, arguments=None):
        try:
            return super().command(name, arguments)
        except Exception as exc:
            # A printk capture may catch its callback's exception and continue.
            # A later clean quit cannot erase that QMP command failure. Final
            # drain receives directly, so its expected reset is not latched here.
            if self.first_command_error is None:
                self.first_command_error = {"command": name, "type": type(exc).__name__,
                                            "message": str(exc), "errno": getattr(exc, "errno", None)}
            raise

    def receive(self, timeout):
        if self.closed:
            return None
        if not select.select([self.sock], [], [], timeout)[0]:
            return []
        data = self.sock.recv(65536)
        if not data:
            self.closed = True
            return None
        self.buffer += data
        messages = []
        while True:
            end = self.buffer.find(b"\n")
            # Check the complete frame before splitting/decoding it: a final
            # fragment containing the newline must not erase an oversized frame.
            if (end < 0 and len(self.buffer) > MAX_QMP_BUFFER) or end + 1 > MAX_QMP_BUFFER:
                raise RuntimeError("QMP observation bound exceeded")
            if end < 0:
                break
            line, self.buffer = self.buffer[:end], self.buffer[end + 1:]
            if not line.strip():
                continue
            if len(self.messages) >= MAX_QMP_MESSAGES:
                raise RuntimeError("QMP observation bound exceeded")
            message = json.loads(line)
            self.messages.append(message)
            validate_qmp_message(message)
            messages.append(message)
        return messages

    def drain(self, timeout=2):
        """Record a bounded transport terminus; only the caller can qualify it."""
        result = {"terminal": None, "errno": None, "error": None,
                  "prior_command_error": self.first_command_error}
        deadline = time.monotonic() + timeout
        try:
            while not self.closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result["terminal"] = "timeout"
                    raise TimeoutError("QMP drain timed out before socket closure")
                try:
                    self.receive(min(.1, remaining))
                except ConnectionResetError as exc:
                    if exc.errno != errno.ECONNRESET:
                        raise
                    result.update(terminal="reset", errno=exc.errno,
                                  transport_error=str(exc))
                    self.closed = True
            if result["terminal"] is None:
                result["terminal"] = "eof"
            if self.buffer:
                raise ValueError("partial QMP frame at socket closure")
        except Exception as exc:
            result["terminal"] = result["terminal"] or "error"
            result["error"] = f"{type(exc).__name__}: {exc}"
            if isinstance(exc, OSError):
                result["errno"] = exc.errno
        result.update(closed=self.closed, residual_bytes=len(self.buffer),
                      message_count=len(self.messages))
        return result


def validate_qmp_message(message):
    if type(message) is not dict:
        raise ValueError("QMP message is not an object")
    kinds = [key for key in ("QMP", "event", "return", "error") if key in message]
    if len(kinds) != 1:
        raise ValueError("ambiguous or missing QMP message kind")
    kind = kinds[0]
    allowed = {"QMP": {"QMP"}, "event": {"event", "data", "timestamp"},
               "return": {"return", "id"}, "error": {"error", "id"}}
    if set(message) - allowed[kind]:
        raise ValueError("unexpected QMP message fields")
    if kind == "event":
        if (type(message["event"]) is not str or not message["event"] or
                ("data" in message and type(message["data"]) is not dict)):
            raise ValueError("invalid QMP event")
    elif kind == "QMP":
        if type(message["QMP"]) is not dict:
            raise ValueError("invalid QMP greeting")
    elif type(message.get("id")) is not int or message["id"] <= 0:
        raise ValueError("invalid QMP response id")
    if kind == "error":
        raise ValueError(f"QMP error response: {message}")


def quit_response(messages, request_id):
    """Require one actual matching ACK; QMP itself does not guarantee quit ACKs."""
    if type(request_id) is not int or request_id <= 0:
        raise ValueError("invalid quit request id")
    replies = []
    for message in messages:
        validate_qmp_message(message)
        if "event" in message:
            continue
        if (set(message) != {"id", "return"} or message["id"] != request_id or
                type(message["return"]) is not dict or message["return"] != {}):
            raise ValueError("unexpected response after quit request")
        replies.append(message)
    if len(replies) != 1:
        raise ValueError("quit requires exactly one matching empty-object ACK")
    return replies[0]


def stopped_before_quit(status, messages):
    """Accept a paused guest or an observed, still-stopped guest shutdown."""
    if type(status) is not dict or status.get("running") is not False:
        return False
    if status.get("status") == "paused":
        return True
    if status.get("status") != "shutdown":
        return False
    # With -no-shutdown, stop ACKs without changing shutdown to paused:
    # qmp_stop -> vm_stop -> do_vm_stop only changes a live runstate.
    # Require the guest's SHUTDOWN, with no subsequent contradictory transition.
    transition = None
    for message in messages:
        if type(message) is not dict:
            return False
        if message.get("event") in ("SHUTDOWN", "RESUME", "RESET", "GUEST_PANICKED", "WATCHDOG"):
            transition = message
    data = transition.get("data", {}) if transition else {}
    return bool(transition and transition.get("event") == "SHUTDOWN" and
                type(data) is dict and data.get("guest") is True and
                data.get("reason") in ("guest-shutdown", "guest-reset", "guest-panic"))


def normal_qmp_shutdown(report):
    """Qualify a fully observed host-requested close; guest success is separate."""
    final, quit_info, drain = (report.get(key, {}) for key in
                              ("final_status", "qmp_quit", "qmp_drain"))
    if any(type(value) is not dict for value in (final, quit_info, drain)):
        return False
    if (final.get("status") not in ("paused", "shutdown") or final.get("running") is not False or
            type(report.get("qemu_returncode")) is not int or report["qemu_returncode"] != 0 or
            report.get("error") or report.get("host_terminated_qemu") or
            report.get("qmp_drain_error") or quit_info.get("acknowledged") is not True or
            drain.get("terminal") not in ("eof", "reset") or
            drain.get("closed") is not True or type(drain.get("residual_bytes")) is not int or
            drain["residual_bytes"] != 0 or type(drain.get("message_count")) is not int or
            not 0 <= drain["message_count"] <= MAX_QMP_MESSAGES or
            "error" not in drain or drain["error"] is not None or
            "prior_command_error" not in drain or drain["prior_command_error"] is not None):
        return False
    if ((drain["terminal"] == "eof" and drain.get("errno") is not None) or
            (drain["terminal"] == "reset" and
             (type(drain.get("errno")) is not int or drain["errno"] != errno.ECONNRESET or
              type(drain.get("transport_error")) is not str or not drain["transport_error"]))):
        return False
    tail = quit_info.get("messages", [])
    if (type(tail) is not list or type(quit_info.get("message_start")) is not int or
            quit_info["message_start"] < 0 or
            quit_info["message_start"] + len(tail) != drain["message_count"]):
        return False
    try:
        ack = quit_response(tail, quit_info.get("request_id"))
        if (quit_response([quit_info.get("response")], quit_info["request_id"]) != ack):
            return False
    except (TypeError, ValueError):
        return False
    host_quit = False
    for message in tail:
        event = message.get("event")
        if event in ("RESUME", "RESET", "GUEST_PANICKED", "WATCHDOG"):
            return False
        if event == "SHUTDOWN":
            data = message.get("data", {})
            if data.get("guest") is not False or data.get("reason") != "host-qmp-quit":
                return False
            host_quit = True
    if not host_quit or final["status"] == "paused":
        return host_quit
    events = report.get("qmp_events")
    tail_events = [message for message in tail if "event" in message]
    # The guest shutdown must precede this quit, not be manufactured from its
    # host SHUTDOWN event or a contradictory later event in the drained tail.
    if (type(events) is not list or not tail_events or
            events[-len(tail_events):] != tail_events):
        return False
    return stopped_before_quit(final, events[:-len(tail_events)])


def open_qmp(path, process, deadline):
    qmp = BoundedQMP(connect(path, process, deadline))
    while time.monotonic() < deadline:
        messages = qmp.receive(.1)
        if messages is None:
            raise RuntimeError("QMP closed before greeting")
        if any(isinstance(x, dict) and "QMP" in x for x in messages):
            qmp.command("qmp_capabilities")
            return qmp
    qmp.sock.close()
    raise TimeoutError("QMP greeting timed out")


def check_backing_nodes(qmp, overlays):
    nodes = qmp.command("query-named-block-nodes")
    by_name = {x.get("node-name"): x for x in nodes}
    for item in overlays:
        n = item["lun"]
        for name in (f"ufs-base-file-{n}", f"ufs-base-{n}"):
            if by_name.get(name, {}).get("ro") is not True:
                raise RuntimeError(f"factory backing was not opened read-only: {name}")
        if by_name.get(f"ufs-disk-{n}", {}).get("ro") is not False:
            raise RuntimeError("guest overlay is not writable")
    return nodes


def capture_stopped_printk(qmp, symbols, out, number, capture=capture_kernel_log):
    directory = Path(out) / f"printk-{number:04d}"
    directory.mkdir(mode=0o700)
    qmp.command("stop")
    before = qmp.command("query-status")
    if before.get("running") is not False:
        raise RuntimeError("guest must be stopped before reading printk")
    result = {"directory": directory.name, "before": before, "guest_memory_written": False}
    try:
        result["capture"] = capture(qmp.command, symbols, directory)
    except Exception as exc:
        result["error"] = str(exc)
    after = qmp.command("query-status")
    if after.get("running") is not False:
        raise RuntimeError("guest ran during printk observation")
    result["after"] = after
    return result


def observe(plan, *, seconds, max_log_bytes, symbols=None, snapshot_interval=15,
            stop_on_kernel_failure=False,
            popen=subprocess.Popen, qmp_open=open_qmp, clock=time.monotonic,
            capture=capture_kernel_log):
    out, process, qmp = Path(plan["out"]), None, None
    result = {"executed": False, "printk": [], "stop_reason": None,
              "stop_on_kernel_failure": stop_on_kernel_failure,
              "qmp_events": [], "base_files_opened_read_only": False}
    start = clock()
    try:
        with (out / "serial.log").open("xb") as serial, (out / "stderr.log").open("xb") as stderr:
            os.chmod(out / "serial.log", 0o600)
            os.chmod(out / "stderr.log", 0o600)
            process = popen(plan["command"], stdin=subprocess.DEVNULL, stdout=serial,
                            stderr=stderr, start_new_session=True)
            result["executed"] = True
            result["pid"] = process.pid
            qmp = qmp_open(plan["qmp_socket"], process, min(start + seconds, clock() + 10))
            before = qmp.command("query-status")
            if before.get("running") is not False:
                raise RuntimeError("QEMU did not start paused")
            result["initial_status"] = before
            result["block_nodes"] = check_backing_nodes(qmp, plan["overlays"])
            result["base_files_opened_read_only"] = True
            qmp.command("cont")
            deadline, next_snapshot = start + seconds, clock() + snapshot_interval
            while process.poll() is None and clock() < deadline:
                messages = qmp.receive(min(.2, max(0, deadline - clock())))
                if messages is None:
                    result["stop_reason"] = "qmp-closed"
                    break
                if any(x.get("event") in ("SHUTDOWN", "GUEST_PANICKED") for x in messages):
                    result["stop_reason"] = "guest-shutdown-or-panic-event"
                    break
                if any(p.exists() and p.stat().st_size >= max_log_bytes
                       for p in (out / "serial.log", out / "stderr.log", out / "qemu.log")):
                    result["stop_reason"] = "log-size-bound"
                    break
                if symbols and clock() >= next_snapshot:
                    snapshot = capture_stopped_printk(
                        qmp, symbols, out, len(result["printk"]), capture)
                    result["printk"].append(snapshot)
                    if stop_on_kernel_failure and "capture" in snapshot and "error" not in snapshot:
                        path = out / snapshot["directory"] / "kernel-printk.log"
                        with path.open("rb") as stream:
                            text = stream.read(max_log_bytes).decode("utf-8", errors="replace")
                        failure = analyze_logs([(str(path.relative_to(out)), text)])["first_kernel_failure"]
                        if failure:
                            result["early_kernel_failure"] = failure
                            result["stop_reason"] = "kernel-failure"
                            # Stay paused and take the normal final snapshot,
                            # QMP drain and post-run preservation checks.
                            break
                    qmp.command("cont")
                    next_snapshot = clock() + snapshot_interval
            if result["stop_reason"] is None:
                result["stop_reason"] = "process-exit" if process.poll() is not None else "time-bound"
            if process.poll() is None and not qmp.closed:
                qmp.command("stop")
                if symbols:
                    result["printk"].append(capture_stopped_printk(
                        qmp, symbols, out, len(result["printk"]), capture))
                pause_start = len(qmp.messages)
                result["final_status"] = qmp.command("query-status")
                if not stopped_before_quit(result["final_status"], qmp.messages):
                    raise RuntimeError("QEMU did not reach a confirmed stopped state before quit")
                if any(message.get("event") in
                       ("RESUME", "RESET", "GUEST_PANICKED", "WATCHDOG", "SHUTDOWN")
                       for message in qmp.messages[pause_start:]):
                    raise RuntimeError("guest state changed during final pause confirmation")
                quit_info = {"request_id": qmp.identifier + 1,
                             "message_start": len(qmp.messages), "acknowledged": False}
                result["qmp_quit"] = quit_info
                returned = qmp.command("quit")
                if type(returned) is not dict or returned != {}:
                    raise RuntimeError("quit did not return an empty-object ACK")
                quit_info["response"] = quit_response(
                    qmp.messages[quit_info["message_start"]:], quit_info["request_id"])
                quit_info["acknowledged"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["stop_reason"] = "runner-error"
    finally:
        if process is not None:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                result["host_terminated_qemu"] = True
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            result["qemu_returncode"] = process.returncode
        if qmp is not None:
            try:
                result["qmp_drain"] = qmp.drain()
            except Exception as exc:
                result["qmp_drain_error"] = str(exc)
            result["qmp_events"] = [x for x in qmp.messages if isinstance(x, dict) and "event" in x]
            if "qmp_quit" in result:
                quit_info = result["qmp_quit"]
                quit_info["messages"] = qmp.messages[quit_info["message_start"]:]
            result["qmp_shutdown_normal"] = normal_qmp_shutdown(result)
            if not result["qmp_shutdown_normal"]:
                drain = result.get("qmp_drain", {})
                if type(drain) is not dict:
                    drain = {"error": "QMP drain did not produce a terminal receipt"}
                result.setdefault("qmp_drain_error", drain.get("error") or
                                  drain.get("transport_error") or "QMP shutdown was not fully confirmed")
            write_json(out / "qmp-messages.json", qmp.messages)
            qmp.sock.close()
        result["elapsed_seconds"] = round(clock() - start, 3)
    return result


def analyze_logs(logs):
    """Classify observed messages; exit 0 or a bare property string is no proof."""
    stages, failures, hardware = {}, [], []
    seen_failures, seen_hardware = set(), set()
    patterns = [(name, re.compile(pattern, re.I)) for name, pattern in STAGE_PATTERNS]
    for source, text in logs:
        for number, line in enumerate(text.splitlines(), 1):
            # A printed command line is configuration, never an observed milestone.
            if re.search(r"(?:Kernel command line:|Command line:|append[\"']?\s*[:=])", line, re.I):
                continue
            record = {"source": source, "line": number, "text": line[:4096]}
            timestamp = re.match(r"\s*\[\s*(\d+(?:\.\d+)?)\s*\]", line)
            if timestamp:
                record["guest_time_seconds"] = float(timestamp.group(1))
            for name, pattern in patterns:
                if pattern.search(line):
                    previous = stages.get(name)
                    if (previous is None or record.get("guest_time_seconds", float("inf")) <
                            previous.get("guest_time_seconds", float("inf"))):
                        stages[name] = record
            identity = (record.get("guest_time_seconds"), record["text"].strip())
            if KERNEL_FAILURE.search(line) and identity not in seen_failures:
                failures.append(record)
                seen_failures.add(identity)
                if len(failures) > MAX_STAGE_RECORDS:
                    failures.sort(key=lambda x: x.get("guest_time_seconds", float("inf")))
                    removed = failures.pop()
                    seen_failures.remove((removed.get("guest_time_seconds"), removed["text"].strip()))
            if (HARDWARE_ERROR.search(line) and identity not in seen_hardware and
                    len(hardware) < MAX_STAGE_RECORDS):
                hardware.append(record)
                seen_hardware.add(identity)
    # A later serial observation must not hide an earlier fault retained in the
    # printk ring. Repeated snapshots of the same ring record are one event.
    failures.sort(key=lambda x: x.get("guest_time_seconds", float("inf")))
    failures, hardware = failures[:MAX_STAGE_RECORDS], hardware[:MAX_STAGE_RECORDS]
    complete = ("android_second_stage_init" in stages and
                ("boot_completed_property" in stages or "framework_boot_complete" in stages))
    return {"stages": stages, "android_userspace_observed": any(
                x in stages for x in ("android_first_stage_init", "android_second_stage_init", "system_server")),
            "android_boot_completed_observed": complete,
            "first_kernel_failure": failures[0] if failures else None,
            "kernel_failures": failures, "hardware_diagnostics": hardware,
            "interactive_display_verified": False}


def collect_logs(out, bound):
    out = Path(out)
    paths = [out / "serial.log"] + sorted(out.glob("printk-*/kernel-printk.log"))
    paths += [out / "stderr.log", out / "qemu.log"]
    logs, identities = [], []
    remaining = min(4 * bound, 256 * 1024 * 1024)
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as stream:
            data = stream.read(min(bound, remaining))
        remaining -= len(data)
        logs.append((str(path.relative_to(out)), data.decode("utf-8", errors="replace")))
        identities.append({"path": str(path.relative_to(out)), "size": path.stat().st_size,
                           "read_bytes": len(data), "analysis_truncated": path.stat().st_size > len(data),
                           "analyzed_bytes_sha256": hashlib.sha256(data).hexdigest()})
    return logs, identities


def verify_preserved(plan):
    core = {name: digest(item["path"]) == item["sha256"]
            for name, item in plan["inputs"].items() if name in ("kernel", "initrd", "dtb", "bootargs")}
    luns = [{"lun": x["lun"], "stat_unchanged": fingerprint(x["base"]) == x["base_before"],
             "base_sha256_supplied": x["base_sha256_supplied"],
             "full_base_digest_rechecked_after_run": False} for x in plan["overlays"]]
    return {"core_input_digests_unchanged": core, "original_luns": luns,
            "preserved": all(core.values()) and all(x["stat_unchanged"] for x in luns)}


def printk_capture_failed(report):
    # A healthy QMP transport does not make symbol parsing, virtual-memory
    # reads or ring decoding successful. Retain failures even if a later
    # snapshot succeeds; missing diagnostics cannot establish a clean run.
    return any("error" in snapshot for snapshot in report.get("printk", []))


def stock_boot_passed(report, plan):
    """Require Android evidence and reject contradictory guest/runtime outcomes."""
    adverse_event = any(
        item.get("event") == "GUEST_PANICKED" or
        (item.get("event") == "SHUTDOWN" and item.get("data", {}).get("guest") is not False)
        for item in report.get("qmp_events", []))
    obs = report["observation"]
    return bool(
        plan["overlays"] and obs["android_boot_completed_observed"] and
        not obs["kernel_failures"] and not report.get("early_kernel_failure") and not adverse_event and
        not printk_capture_failed(report) and
        report["preservation"]["preserved"] and not report.get("error") and
        report["base_files_opened_read_only"] and report.get("qemu_returncode") == 0 and
        report.get("qemu_unchanged") is True and
        report["stop_reason"] == "time-bound" and
        report.get("final_status", {}).get("running") is False and
        report.get("final_status", {}).get("status") == "paused" and
        not report.get("qmp_drain_error") and not report.get("host_terminated_qemu") and
        normal_qmp_shutdown(report) and
        not any(x["analysis_truncated"] for x in report["logs"]))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--qemu-img", default="qemu-img")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--machine", required=True, help="Explicit previously reviewed machine and profile settings")
    parser.add_argument("--global-property", "--global", action="append", default=[])
    parser.add_argument("--configuration-evidence", type=Path)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--max-log-mib", type=int, default=128)
    parser.add_argument("--legacy-printk-symbols", type=Path,
                        help="Exact runtime symbol addresses for this boot, including any KASLR relocation; capture errors are diagnostic failures")
    parser.add_argument("--symbols-kernel-sha256", help="SHA-256 of the kernel to which supplied runtime addresses belong")
    parser.add_argument("--snapshot-interval", type=float, default=15)
    parser.add_argument("--stop-on-kernel-failure", action="store_true",
                        help="End observation after a successful stopped printk snapshot identifies a kernel failure; requires --legacy-printk-symbols")
    parser.add_argument("--trace-interrupts", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not 0 < args.seconds <= 3600 or not 1 <= args.max_log_mib <= 1024:
        parser.error("seconds must be in (0,3600] and max-log-mib in [1,1024]")
    if not 1 <= args.snapshot_interval <= 300 or args.seconds / args.snapshot_interval > 240:
        parser.error("invalid snapshot interval or more than 240 requested snapshots")
    if args.stop_on_kernel_failure and not args.legacy_printk_symbols:
        parser.error("--stop-on-kernel-failure requires --legacy-printk-symbols")
    from android_boot_inputs import load_prepared_manifest
    manifest = load_prepared_manifest(args.manifest, verify_hashes=True)
    qemu, qemu_img = executable(args.qemu), executable(args.qemu_img)
    if args.legacy_printk_symbols:
        args.legacy_printk_symbols = regular_path(args.legacy_printk_symbols)
        if args.symbols_kernel_sha256 != manifest["kernel"]["sha256"]:
            parser.error("printk symbols require their exact --symbols-kernel-sha256")
    if args.configuration_evidence:
        args.configuration_evidence = regular_path(args.configuration_evidence)
    out = args.out.resolve()
    out.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {"schema": "tb371fc-android-boot-result-v1",
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "manifest_sha256": digest(args.manifest), "qemu_sha256": digest(qemu),
              "qemu_img_sha256": digest(qemu_img), "runner_sha256": digest(__file__),
              "dry_run": args.dry_run, "executed": False,
              "stop_on_kernel_failure": args.stop_on_kernel_failure,
              "stock_android_boot_passed": False,
              "input_provenance": {"factory_provenance_verified": False, "avb_verified": False,
                  "luns": [{"lun": lun["lun"], "expected_sha256_supplied": bool(lun.get("sha256")),
                            "sha256_verified": lun.get("sha256_verified") is True}
                           for lun in manifest["luns"]]},
              "scope": "Original Android direct-kernel boot; OEM PBL/XBL/ABL chain is not executed"}
    if args.legacy_printk_symbols:
        report["printk_symbols_sha256"] = digest(args.legacy_printk_symbols)
    if args.configuration_evidence:
        report["configuration_evidence"] = {"path": str(args.configuration_evidence),
                                            "sha256": digest(args.configuration_evidence)}
    exit_code = 1
    with tempfile.TemporaryDirectory(prefix="tb-android-") as socket_dir:
        plan = None
        try:
            plan = build_plan(manifest, qemu=qemu, qemu_img=qemu_img, out=out,
                              qmp_socket=Path(socket_dir) / "q", machine=args.machine,
                              global_properties=args.global_property,
                              trace_interrupts=args.trace_interrupts)
            write_json(out / "plan.json", plan)
            if args.dry_run:
                report["outcome"] = "dry-run"
                exit_code = 0
            else:
                report["overlay_creation"] = prepare_overlays(plan)
                report.update(observe(plan, seconds=args.seconds,
                                      max_log_bytes=args.max_log_mib * 1024 * 1024,
                                      symbols=args.legacy_printk_symbols,
                                      snapshot_interval=args.snapshot_interval,
                                      stop_on_kernel_failure=args.stop_on_kernel_failure))
                logs, report["logs"] = collect_logs(out, args.max_log_mib * 1024 * 1024)
                report["observation"] = analyze_logs(logs)
                report["preservation"] = verify_preserved(plan)
                report["qemu_sha256_after"] = digest(qemu)
                report["qemu_unchanged"] = report["qemu_sha256_after"] == report["qemu_sha256"]
                obs = report["observation"]
                passed = stock_boot_passed(report, plan)
                report["stock_android_boot_passed"] = bool(passed)
                report["outcome"] = ("android-boot-complete-observed" if passed else
                                     "input-preservation-failure" if not report["preservation"]["preserved"] else
                                     "kernel-failure" if obs["kernel_failures"] or report.get("early_kernel_failure") else
                                     "runner-error" if report.get("error") else
                                     "diagnostic-failure" if printk_capture_failed(report) else
                                     "android-userspace-incomplete" if obs["android_userspace_observed"] else
                                     "no-android-userspace-evidence")
                exit_code = (0 if passed else 1 if report.get("error") or printk_capture_failed(report) or
                             not report["preservation"]["preserved"] else 2)
        except Exception as exc:
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["outcome"] = "runner-error"
            if plan is not None:
                try:
                    report["preservation"] = verify_preserved(plan)
                except Exception as check_exc:
                    report["preservation_error"] = str(check_exc)
        write_json(out / "result.json", report)
    print(json.dumps({"outcome": report["outcome"], "executed": report["executed"],
                      "stock_android_boot_passed": report["stock_android_boot_passed"],
                      "result": str(out / "result.json")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
