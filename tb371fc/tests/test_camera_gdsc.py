#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled camera GDSC transactions and real file migrations.

The six words and DT flags come from the fixed TB371FC DT; the digital
reference is explicitly enabled. No camera instructions, physical rails,
retention effect, clock carrier, analog timing or Android boot is inferred.
The existing GPU GX/CX acceptance scripts remain byte-for-byte unchanged.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import time
import traceback

from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_rpmh_display import DisplayRig
from test_rpmh_sm8250 import PROFILE, RESOURCES, ISSUED, COMPLETE
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-camera-gdsc", "/machine/camera-gdsc"
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
GCC, GCC_PATH, RAM = 0x100000, "/machine/gcc", 0x80010000
CAMERA_AHB = GCC + 0xb008
COLLAPSE, HW_MODE, RETAIN, WAIT, POWER = 1, 2, 1 << 11, 15 << 12, 1 << 31
WRITE_MASK, IDENTITY = 0xf803, 0x43474431
VMSTATE_BYTES, EXPECTED_CHECKS = 194, 29
GCC_VMSTATE_BYTES = 645271
GCC_RETENTION_MASK = 0x6001
DOMAIN_NAMES = ("bps", "ipe", "sbi", "ife0", "ife1", "titan-top")
ADDRESSES = (0x0ad07004, 0x0ad08004, 0x0ad09004,
             0x0ad0a004, 0x0ad0b004, 0x0ad0c144)
DT_FLAGS = (3, 3, 1, 1, 1, 1)
TIMEOUT_US = (100, 100, 100, 100, 100, 500)
REGION_NAMES = tuple(f"{TYPE}.{name}-control" for name in DOMAIN_NAMES)
GLOBAL_FIELDS = (
    "enabled-profile", "reference-identity", "domain-count", "register-bytes",
    "mapped-bytes", "parent-resource-index", "cold-control",
    "control-write-mask", "power-status-mask",
)
DOMAIN_FIELDS = (
    "name", "address", "dt-flags", "timeout-us", "control", "request-known",
    "requested", "state-known", "powered", "hw-request-known", "hw-request",
)
DEFAULT_MACHINE = (
    "tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
    "rpmh-profile=off,rpmh-boot-configured=off"
)
OLD_GPU_BLOBS = {
    "test_gpu_gx_gdsc.py": "af4919deb5a99654bef146a017bbeccbdf05cd1c",
    "test_gpu_cx_gdsc.py": "25c517cb652c6f02e744776b128881639fa99e7f",
}
DT_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
DRIVER_SHA256 = "41acd7aa326c656db5b9dbe57c5fe05f1783abf8a299aaf9f8d4c58b9ecea3d2"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2) + "\n")


def old_gpu_identities():
    result = {}
    for name, wanted in OLD_GPU_BLOBS.items():
        raw = Path(__file__).with_name(name).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        assert actual == wanted, (name, actual, wanted)
        result[name] = {"git_blob": actual, "bytes": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest()}
    return result


def oracle_snapshot(controls, parent_known=False, parent_on=False,
                    hw_known=None, hw_requests=None, reference=True):
    """Enumerate possible AND results instead of relying on a device getter."""
    hw_known = [False] * 6 if hw_known is None else list(hw_known)
    hw_requests = [False] * 6 if hw_requests is None else list(hw_requests)
    assert len(controls) == len(hw_known) == len(hw_requests) == 6
    result = {
        "enabled-reference": bool(reference),
        "globals": dict(zip(GLOBAL_FIELDS, (
            int(bool(reference)), IDENTITY, 6, 4, 24, 3, 1, WRITE_MASK, POWER,
        ))),
        "parent-known": bool(parent_known), "parent-on": bool(parent_on),
        "domains": [],
    }
    parent_values = {bool(parent_on)} if parent_known else {False, True}
    for i, control in enumerate(controls):
        if control & HW_MODE:
            request_values = {bool(hw_requests[i])} if hw_known[i] else {False, True}
        else:
            request_values = {not bool(control & COLLAPSE)}
        possible = {a and b for a in parent_values for b in request_values}
        request_known = len(request_values) == 1
        known = bool(reference) and len(possible) == 1
        result["domains"].append(dict(zip(DOMAIN_FIELDS, (
            DOMAIN_NAMES[i], ADDRESSES[i], DT_FLAGS[i], TIMEOUT_US[i], control,
            request_known, request_values == {True}, known,
            bool(reference) and possible == {True},
            bool(hw_known[i]), bool(hw_requests[i]),
        ))))
    return result


def core_diagnostics(text):
    matches = re.findall(
        r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
        r"size ([0-9]+), region '([^']+)', reason: (.+)", text)
    return [(op, int(address, 16), int(size), region, reason)
            for op, address, size, region, reason in matches]


def diagnostic(domain, reason, *, write=True, value=0, offset=0, size=4):
    return (f"{TYPE}: rejected {'write' if write else 'read'} domain={domain} "
            f"offset=0x{offset:x} size={size} value=0x{value:x} reason={reason}")


class CameraRig(DisplayRig):
    """Retained JSONL evidence and existing real RPMh command helpers."""

    def __init__(self, args, directory, *, reference=True, incoming=False,
                 rpmh=False, group="power", accel="qtest", legacy_gpu=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.group, self.accel = group, accel
        self.last_clock = 0
        self.camera_mmio_operations = 0
        self.device_mmio_operations = 0
        self.cont_commands = 0
        self.connections = []
        self.events, self.irq, self.seen_irq = [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="camgdsc-"))
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory / "qtest.jsonl").open("w")
        self.qmplog = (self.directory / "qmp.jsonl").open("w")
        fields = [x for x in args.machine.split(",")
                  if not x.startswith(("rpmh-profile=", "rpmh-boot-configured=",
                                       "rpmh-cmd-db="))]
        fields += [f"rpmh-profile={'reference' if rpmh else 'off'}",
                   "rpmh-boot-configured=off"]
        command = [
            args.qemu, "-machine", ",".join(fields), "-accel", accel,
            "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
            "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
            "-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
            "-qtest-log", "/dev/null",
            "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off",
        ]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if legacy_gpu:
            command += ["-global", "qcom-gdsc.enabled-reference=on",
                        "-global", "qcom-gdsc.reference-on=on",
                        "-global", "qcom-gpu-cx-gdsc.boot-collapsed-reference=on"]
        if incoming:
            command += ["-incoming", "defer"]
        write_json(self.directory / "command.json", command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qtest = connect_unix(self.socket_directory / "t", self.process)
            self.connections.append(self.qtest)
            self.qfile = self.qtest.makefile("rwb", buffering=0)
            self.qmp = connect_unix(self.socket_directory / "m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self._log(self.qmplog, {"receive": greeting})
            assert "QMP" in greeting, greeting
            self.monitor("qmp_capabilities")
            if group is not None:
                assert group in ("power", "state-known")
                # QTest supports only one named output group per process.
                self.command(f"irq_intercept_out {DEVICE} {group}")
            if not incoming and accel == "qtest":
                self.monitor("cont")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _log(stream, item):
        stream.write(json.dumps(item) + "\n")
        stream.flush()

    def command(self, command):
        fields = command.split()
        if fields and fields[0] in ("readb", "readw", "readl", "readq",
                                   "writeb", "writew", "writel", "writeq"):
            address = int(fields[1], 0)
            if any(base <= address < base + 4 for base in ADDRESSES):
                self.camera_mmio_operations += 1
            if (any(base <= address < base + 4 for base in ADDRESSES) or
                    GCC <= address < GCC + 0x1f0000 or
                    RPMH_BASE <= address < RPMH_BASE + 0x30000):
                self.device_mmio_operations += 1
        self._log(self.qtlog, {"send": command, "virtual_ns_before": self.last_clock})
        self.qfile.write((command + "\n").encode())
        while True:
            raw = self.qfile.readline()
            if not raw:
                raise RuntimeError("QTest connection closed")
            line = raw.decode().strip()
            self._log(self.qtlog, {"receive": line})
            if line.startswith("IRQ "):
                _, state, number = line.split()
                number = int(number)
                self.irq[number] = state == "raise"
                self.seen_irq.add(number)
                continue
            assert line.startswith("OK"), (command, line)
            return line.split()[1:]

    def raw_monitor(self, execute, arguments=None):
        self._request_id += 1
        request = {"execute": execute, "id": self._request_id}
        if arguments is not None:
            request["arguments"] = arguments
        if execute == "cont":
            self.cont_commands += 1
        self._log(self.qmplog, {"send": request})
        self.qmpfile.write((json.dumps(request) + "\n").encode())
        while True:
            raw = self.qmpfile.readline()
            if not raw:
                raise RuntimeError("QMP connection closed")
            response = json.loads(raw)
            self._log(self.qmplog, {"receive": response})
            if "event" in response:
                self.events.append(response)
                continue
            assert response.get("id") == self._request_id, response
            return response

    def monitor(self, execute, arguments=None):
        response = self.raw_monitor(execute, arguments)
        assert "error" not in response, response
        return response.get("return")

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def pure_snapshot(self):
        # Exactly 78 pure getters, always in this order.
        result = {
            "enabled-reference": self.qom("enabled-reference"),
            "globals": {name: self.qom(name) for name in GLOBAL_FIELDS},
            "parent-known": self.qom("parent-known"),
            "parent-on": self.qom("parent-on"),
            "domains": [],
        }
        for i in range(6):
            result["domains"].append({
                name: self.qom(f"domain{i}.{name}") for name in DOMAIN_FIELDS
            })
        return result

    def controls(self):
        return [self.qom(f"domain{i}.control") for i in range(6)]

    def input(self, name, level, domain=0):
        assert name in ("parent-known", "parent-on", "hw-request-known", "hw-request")
        self.command(f"set_irq_in {DEVICE} {name} {domain} {int(level)}")

    def parent(self, known, on):
        assert not self.has_rpmh, "Do not override a connected real parent provider"
        self.input("parent-on", on)
        self.input("parent-known", known)

    def hardware(self, domain, known, requested):
        self.input("hw-request", requested, domain)
        self.input("hw-request-known", known, domain)

    def read_domain(self, domain):
        return self.raw_read(ADDRESSES[domain])

    def write_domain(self, domain, value):
        self.raw_write(ADDRESSES[domain], value)

    def read_absolute(self, address, width="l"):
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def write_absolute(self, address, value, width="l"):
        self.command(f"write{width} {address:#x} {value:#x}")

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return ns

    def camera_period(self, parent=False):
        name = "gcc_camera_ahb_clk" + ("_parent" if parent else "")
        return self.qom("qtest-clock-period", GCC_PATH + "/" + name)

    def expect_lines(self, expected, require_seen=True):
        assert self.group is not None
        self.raw_read(RAM)  # Drain only RAM; never repair via camera/GCC MMIO.
        if require_seen:
            assert set(range(6)) <= self.seen_irq, (self.group, self.seen_irq)
        actual = [self.irq.get(i, False) for i in range(6)]
        assert actual == list(expected), (self.group, actual, expected)

    def diagnostic_count(self, text):
        return self.log_path.read_text(errors="replace").splitlines().count(text)

    def reject_write(self, domain, value, reason):
        line = diagnostic(domain, reason, value=value)
        before = self.diagnostic_count(line)
        self.write_domain(domain, value)
        assert self.diagnostic_count(line) == before + 1, line

    def reject_read(self, domain, reason):
        line = diagnostic(domain, reason, write=False)
        before = self.diagnostic_count(line)
        self.read_domain(domain)
        assert self.diagnostic_count(line) == before + 1, line

    def close(self):
        for name in ("qfile", "qmpfile"):
            item = getattr(self, name, None)
            if item:
                try:
                    item.close()
                except OSError:
                    pass
        for item in getattr(self, "connections", []):
            item.close()
        process = getattr(self, "process", None)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        for name in ("log", "qtlog", "qmplog"):
            item = getattr(self, name, None)
            if item:
                item.close()
        if hasattr(self, "socket_directory"):
            shutil.rmtree(self.socket_directory, ignore_errors=True)


def assert_snapshot(rig, expected):
    actual = rig.pure_snapshot()
    assert actual == expected, (actual, expected)
    return actual


def assert_feedback_reads(rig, expected):
    for i, domain in enumerate(expected["domains"]):
        if not expected["enabled-reference"]:
            rig.reject_read(i, "reference-disabled")
        elif domain["state-known"]:
            assert rig.read_domain(i) == domain["control"] | (POWER if domain["powered"] else 0)
        else:
            rig.reject_read(i, "power-feedback-unknown")


def cold_reference_six_words_and_exact_identity(r):
    expected = oracle_snapshot([COLLAPSE] * 6)
    assert_snapshot(r, expected)
    assert r.pure_snapshot() == expected
    assert [r.read_domain(i) for i in range(6)] == [1] * 6
    assert len(set(ADDRESSES)) == 6 and sum(4 for _ in ADDRESSES) == 24
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory / "mtree.txt").write_text(tree)
    pattern = (r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" +
               "|".join(re.escape(name) for name in REGION_NAMES) + r")(?:\s|$)")
    found = {(int(first, 16), int(last, 16), name)
             for first, last, name in re.findall(pattern, tree)}
    assert found == {(address, address + 3, name)
                     for address, name in zip(ADDRESSES, REGION_NAMES)}, (found, tree)
    assert r.last_clock == 0  # The reference has no sequencer delay.
    write_json(r.directory / "cold-qom.json", expected)


def literal_vendor_probe_rmw_does_not_enable_collapsed_domain(r):
    r.parent(True, True)
    controls = [(i + 1) << 12 | RETAIN | COLLAPSE for i in range(6)]
    for i, value in enumerate(controls):
        r.write_domain(i, value)
    before = oracle_snapshot(controls, True, True)
    assert_snapshot(r, before)
    for i in range(6):
        # gdsc_probe 847-859: clear HW_CONTROL and SW_OVERRIDE, whole-word RMW.
        value = r.read_domain(i)
        r.write_domain(i, value & ~(HW_MODE | 4))
        after = r.read_domain(i)
        assert after == controls[i] and not after & POWER
        # Probe removes the bus vote for collapsed/unpowered state (874-881).
        assert not (after & POWER) or after & COLLAPSE
    assert_snapshot(r, before)


def literal_vendor_enable_disable_preserve_retention_requests(r):
    r.parent(True, True)
    controls = [COLLAPSE | ((i + 1) << 12) for i in range(6)]
    for i, value in enumerate(controls):
        r.write_domain(i, value)
    for i in range(6):
        value = r.read_domain(i) & ~COLLAPSE
        r.write_domain(i, value)  # Source enable 291-293.
        r.step(1000)  # Source software wait, not a modeled camera deadline.
        assert r.read_domain(i) == value | POWER
        value |= RETAIN  # Source retain-FF request after successful polling.
        r.write_domain(i, value)
        assert r.read_domain(i) == value | POWER
        # Source disable carries a previously read RO POWER bit through RMW.
        r.write_domain(i, r.read_domain(i) | COLLAPSE)
        r.step(1000)
        controls[i] |= RETAIN
        assert r.read_domain(i) == controls[i]
    assert_snapshot(r, oracle_snapshot(controls, True, True))


def software_requests_obey_parent_three_valued_truth(r):
    for parent_known, parent_on in ((False, False), (False, True),
                                    (True, False), (True, True)):
        r.parent(parent_known, parent_on)
        for collapsed in (True, False):
            controls = [int(collapsed) | ((i + 1) << 12) for i in range(6)]
            for i, value in enumerate(controls):
                r.write_domain(i, value)
            expected = oracle_snapshot(controls, parent_known, parent_on)
            assert_snapshot(r, expected)
            assert_feedback_reads(r, expected)


def hardware_requests_obey_explicit_knowledge_and_ignore_collapse(r):
    for parent_known, parent_on in ((False, False), (False, True),
                                    (True, False), (True, True)):
        r.parent(parent_known, parent_on)
        for hw_known, hw_request in ((False, False), (False, True),
                                     (True, False), (True, True)):
            known, requests = [False] * 6, [False] * 6
            for domain in (0, 1):
                r.hardware(domain, hw_known, hw_request)
                known[domain], requests[domain] = hw_known, hw_request
            for collapse in (0, COLLAPSE):
                controls = [HW_MODE | collapse | RETAIN,
                            HW_MODE | collapse | (7 << 12)] + [COLLAPSE] * 4
                for i in (0, 1):
                    r.write_domain(i, controls[i])
                expected = oracle_snapshot(controls, parent_known, parent_on,
                                           known, requests)
                assert_snapshot(r, expected)
                assert_feedback_reads(r, expected)


def undeclared_hw_modes_and_reserved_bits_are_atomic(r):
    r.parent(True, True)
    controls = [RETAIN | COLLAPSE | ((i + 1) << 12) for i in range(6)]
    for i, value in enumerate(controls):
        r.write_domain(i, value)
        r.hardware(i, True, True)
    baseline = r.pure_snapshot()
    for i in range(2, 6):
        r.reject_write(i, controls[i] | HW_MODE, "hw-mode-not-in-reference")
        assert r.controls() == controls
    for i in range(6):
        for forbidden in (4, 1 << 10, 1 << 16, 1 << 30):
            r.reject_write(i, controls[i] | forbidden, "unsupported-bits")
            assert r.controls() == controls
    assert_snapshot(r, baseline)


def distinct_domains_and_hardware_inputs_are_isolated(r):
    r.parent(True, True)
    controls = [HW_MODE | COLLAPSE | 0x1000, HW_MODE | RETAIN | 0x2000,
                0x3000, COLLAPSE | 0x4000, RETAIN | 0x5000,
                COLLAPSE | RETAIN | 0x6000]
    known = [True, False, True, False, True, False]
    requests = [True, True, False, True, True, False]
    for i in range(6):
        r.hardware(i, known[i], requests[i])
        r.write_domain(i, controls[i])
    assert_snapshot(r, oracle_snapshot(controls, True, True, known, requests))
    for i in range(6):
        before = r.pure_snapshot()
        requests[i] = not requests[i]
        r.hardware(i, known[i], requests[i])
        after = assert_snapshot(r, oracle_snapshot(controls, True, True, known, requests))
        assert all(before["domains"][j] == after["domains"][j]
                   for j in range(6) if j != i)
    assert r.qom("enabled-reference", "/machine/gpu-gx-gdsc") is False
    assert r.qom("boot-collapsed-reference", "/machine/gpu-cx-gdsc") is False


def power_status_is_derived_and_read_only_in_rmw(r):
    for parent_on in (False, True):
        r.parent(True, parent_on)
        for collapse in (0, COLLAPSE):
            for carried_status in (0, POWER):
                controls = [collapse | RETAIN | ((i + 1) << 12) for i in range(6)]
                for i, control in enumerate(controls):
                    r.write_domain(i, control | carried_status)
                expected = oracle_snapshot(controls, True, parent_on)
                assert_snapshot(r, expected)
                assert_feedback_reads(r, expected)


def retention_wait_fields_do_not_supply_power_or_add_delay(r):
    r.parent(True, False)
    for i in range(6):
        r.write_domain(i, RETAIN | WAIT)  # Requested, but no parent supply.
    expected = oracle_snapshot([RETAIN | WAIT] * 6, True, False)
    assert_snapshot(r, expected)
    assert [r.read_domain(i) for i in range(6)] == [RETAIN | WAIT] * 6
    assert r.last_clock == 0
    r.parent(True, True)
    assert_snapshot(r, oracle_snapshot([RETAIN | WAIT] * 6, True, True))
    assert r.last_clock == 0
    for i in range(6):
        r.write_domain(i, 0)
    assert_snapshot(r, oracle_snapshot([0] * 6, True, True))
    r.parent(True, False)
    assert_snapshot(r, oracle_snapshot([0] * 6, True, False))
    assert r.last_clock == 0


def unknown_feedback_is_an_error_without_register_mutation(r):
    controls = [RETAIN | ((i + 1) << 12) for i in range(6)]
    for i, value in enumerate(controls):
        r.write_domain(i, value)
    r.parent(False, True)  # A stale true value is not valid supply knowledge.
    baseline = oracle_snapshot(controls, False, True)
    assert_snapshot(r, baseline)
    for _ in range(3):
        for i in range(6):
            r.reject_read(i, "power-feedback-unknown")
        assert_snapshot(r, baseline)
    r.step(1_000_000)
    assert_snapshot(r, baseline)
    r.parent(True, True)
    assert_snapshot(r, oracle_snapshot(controls, True, True))
    assert [r.read_domain(i) for i in range(6)] == [x | POWER for x in controls]


def narrow_unaligned_and_wide_bus_transactions_are_exact(r):
    controls = [RETAIN | COLLAPSE | ((i + 1) << 12) for i in range(6)]
    for i, value in enumerate(controls):
        r.write_domain(i, value)
    baseline = r.pure_snapshot()
    for i, base in enumerate(ADDRESSES):
        for operation in ("read", "write"):
            for width, size in (("b", 1), ("w", 2)):
                previous = core_diagnostics(r.log_path.read_text(errors="replace"))
                if operation == "read":
                    r.read_absolute(base, width)
                else:
                    r.write_absolute(base, (1 << (8 * size)) - 1, width)
                assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == [
                    (operation, 0, size, REGION_NAMES[i], "invalid size (min:4 max:4)")
                ]
            for displacement in (1, 2):
                previous = core_diagnostics(r.log_path.read_text(errors="replace"))
                if operation == "read":
                    r.read_absolute(base + displacement)
                else:
                    r.write_absolute(base + displacement, 0x03020100)
                chunks = ((1, 1), (2, 2), (4, 1)) if displacement == 1 else ((2, 2), (4, 2))
                expected = [
                    (operation, base + local, size, "(null)", "rejected")
                    if local == 4 else
                    (operation, local, size, REGION_NAMES[i], "invalid size (min:4 max:4)")
                    for local, size in chunks
                ]
                actual = core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):]
                assert actual == expected, (i, operation, displacement, actual, expected)
        assert_snapshot(r, baseline)
    # The QTest bus request spans two devices. Its valid low 32-bit write
    # commits; failure at the adjacent hole cannot roll that write back.
    for i, base in enumerate(ADDRESSES):
        value = COLLAPSE | ((15 - i) << 12)
        previous = core_diagnostics(r.log_path.read_text(errors="replace"))
        r.write_absolute(base, (0x5a5a5a5a << 32) | value, "q")
        assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == [
            ("write", base + 4, 4, "(null)", "rejected")
        ]
        controls[i] = value
        assert r.controls() == controls
        assert r.read_domain(i) == value


VIDEOCC_NEIGHBOR_BCRS = (0x0abf0bf4, 0x0abf0c94, 0x0abf0d14, 0x0abf0d94)


def assert_disabled_videocc_neighbors(r):
    # VIDEOCC now owns the source-declared BCR immediately before each GDSC.
    # Its independent default-off profile must still reject every access.
    path = "/machine/videocc"
    video = "/machine/video-gdsc"

    def neighbor_snapshot():
        return {
            "enabled-reference": r.qom("enabled-reference", path),
            "registers": [r.qom(f"reg.{address - 0x0abf0000:04x}", path)
                          for address in VIDEOCC_NEIGHBOR_BCRS],
            "video": {
                "enabled-reference": r.qom("enabled-reference", video),
                "globals": {name: r.qom(name, video) for name in GLOBAL_FIELDS},
                "parent-known": r.qom("parent-known", video),
                "parent-on": r.qom("parent-on", video),
                "domains": [{name: r.qom(f"domain{i}.{name}", video)
                             for name in DOMAIN_FIELDS} for i in range(4)],
            },
        }

    before = r.pure_snapshot()
    neighbors = neighbor_snapshot()
    assert neighbors["enabled-reference"] is False
    assert neighbors["registers"] == [0] * 4
    for address in VIDEOCC_NEIGHBOR_BCRS:
        for operation in ("read", "write"):
            line = (f"qcom-sm8250-videocc: unmodeled {operation} "
                    f"+0x{address - 0x0abf0000:x} size 4")
            count = r.diagnostic_count(line)
            if operation == "read":
                r.read_absolute(address)
            else:
                r.write_absolute(address, 0xffffffff)
            assert r.diagnostic_count(line) == count + 1, line
            assert neighbor_snapshot() == neighbors, (address, operation)
            assert r.pure_snapshot() == before, (address, operation)


def camera_holes_and_unrelated_gdsc_addresses_stay_unmapped(r):
    before = r.pure_snapshot()
    # Kona-v2 CAMCC now owns these source-qualified neighboring words. Its
    # reference is independently disabled in this camera-only fixture.
    camcc_owned = {0x0000, 0x7000, 0x7014, 0x8000, 0x8014, 0x9000,
                   0x9014, 0xa000, 0xa014, 0xb000, 0xb014, 0xc154}
    holes = sorted({x + displacement for x in ADDRESSES for displacement in (-4, 4, 0x10)}
                   | {0x0ad00000, 0x0ad0fffc, 0x09981008, 0x0998100c,
                      0x0abf0bf4, 0x0abf0bfc, 0x0abf0c94, 0x0abf0c9c,
                      0x0abf0d14, 0x0abf0d1c, 0x0abf0d94, 0x0abf0d9c,
                      0x0af02ffc, 0x0af03004})
    holes = [address for address in holes
             if address - 0x0ad00000 not in camcc_owned
             and address not in VIDEOCC_NEIGHBOR_BCRS]
    assert not set(holes) & set(ADDRESSES)
    previous = core_diagnostics(r.log_path.read_text(errors="replace"))
    expected = []
    for address in holes:
        r.read_absolute(address)
        r.write_absolute(address, 0xffffffff)
        expected.extend((op, address, 4, "(null)", "rejected")
                        for op in ("read", "write"))
    actual = core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):]
    assert actual == expected, (actual, expected)
    for offset in sorted(camcc_owned):
        for operation in ("read", "write"):
            line = f"qcom-sm8250-camcc: unmodeled {operation} +0x{offset:x} size 4"
            count = r.diagnostic_count(line)
            if operation == "read":
                r.read_absolute(0x0ad00000 + offset)
            else:
                r.write_absolute(0x0ad00000 + offset, 0xffffffff)
            assert r.diagnostic_count(line) == count + 1, line
    assert_disabled_videocc_neighbors(r)
    # Independent MDSS/NPU/VIDEO owners stay disabled in this camera fixture.
    for owner, domain, address in (("qcom-mdss-gdsc", 0, 0x0af03000),
                                   ("qcom-npu-gdsc", 0, 0x09981004),
                                   ("qcom-video-gdsc", 0, 0x0abf0bf8),
                                   ("qcom-video-gdsc", 1, 0x0abf0c98),
                                   ("qcom-video-gdsc", 2, 0x0abf0d18),
                                   ("qcom-video-gdsc", 3, 0x0abf0d98)):
        for operation in ("read", "write"):
            line = (owner + ": rejected " + operation +
                    f" domain={domain} offset=0x0 size=4 value=0x" +
                    ("0" if operation == "read" else "ffffffff") +
                    " reason=reference-disabled")
            count = r.diagnostic_count(line)
            if operation == "read":
                r.read_absolute(address)
            else:
                r.write_absolute(address, 0xffffffff)
            assert r.diagnostic_count(line) == count + 1, line
    assert_snapshot(r, before)


def system_reset_preserves_inputs_and_republishes_collapsed_outputs(r):
    r.parent(True, True)
    known = [True, False, True, False, True, False]
    requests = [True, True, False, True, False, True]
    for i in range(6):
        r.hardware(i, known[i], requests[i])
        r.write_domain(i, RETAIN | ((i + 1) << 12))
    r.expect_lines([True] * 6)
    r.raw_write(CAMERA_AHB, GCC_RETENTION_MASK)
    r.raw_write(GCC + 4, 0x35)
    assert r.raw_read(GCC + 4) == 0x35
    r.events.clear()
    r.monitor("system_reset")
    r.wait_for(lambda: r.raw_read(GCC + 4) == 0, "independent GCC reset witness")
    r.monitor("query-status")  # Drain the asynchronous RESET event.
    assert any(event.get("event") == "RESET" for event in r.events)
    expected = oracle_snapshot([COLLAPSE] * 6, True, True, known, requests)
    assert_snapshot(r, expected)
    r.expect_lines([False] * 6)
    assert r.raw_read(CAMERA_AHB) == 0
    assert r.camera_period() == 0 and r.camera_period(parent=True) == 0
    assert [r.read_domain(i) for i in range(6)] == [1] * 6
    r.write_domain(0, HW_MODE)
    assert r.qom("domain0.powered") is True  # Preserved input is still meaningful.


def power_and_validity_outputs_drive_all_six_lines(r):
    r.parent(True, True)
    for i in range(6):
        r.write_domain(i, 0)
    r.expect_lines([True] * 6)
    for i in range(6):
        r.write_domain(i, COLLAPSE)
        r.expect_lines([j > i for j in range(6)])
    other = CameraRig(r.args, r.directory / "state-known-group", group="state-known")
    try:
        other.parent(True, True)
        other.expect_lines([True] * 6)
        for i in range(6):
            other.write_domain(i, 0)
        other.parent(False, True)
        other.expect_lines([False] * 6)
        for i in range(6):
            other.write_domain(i, COLLAPSE)
            other.expect_lines([j <= i for j in range(6)])
        return {"observed_named_groups": ["power", "state-known"],
                "separate_processes": True, "distinct_observed_lines": 12}
    finally:
        other.close()


def real_rpmh_mmcx_parent_and_wrong_resource_votes(r):
    assert r.has_rpmh
    assert r.resource("mmcx.lvl", "present") == 1
    assert r.qom("parent-known") is True and r.qom("parent-on") is False
    for i in range(6):
        r.write_domain(i, 0)
    for name in ("cx.lvl", "gfx.lvl", "mx.lvl"):
        r.run_one(name, 1)
        assert r.resource(name) == 1 and r.resource("mmcx.lvl") == 0
        assert_snapshot(r, oracle_snapshot([0] * 6, True, False))
    r.clear()
    r.program("mmcx.lvl", 1)
    r.trigger()
    r.step(999)
    assert r.resource("mmcx.lvl") == 0
    assert_snapshot(r, oracle_snapshot([0] * 6, True, False))
    r.step(1)
    assert r.resource("mmcx.lvl") == 1
    assert_snapshot(r, oracle_snapshot([0] * 6, True, True))
    r.expect_lines([True] * 6)
    assert r.status() == ISSUED | COMPLETE
    # A second real voter holds the shared resource when DRV2 releases it.
    r.run_one("mmcx.lvl", 2, drv=0)
    r.run_one("mmcx.lvl", 0, drv=2)
    assert r.resource("mmcx.lvl") == 2
    r.expect_lines([True] * 6)
    r.run_one("mmcx.lvl", 0, drv=0)
    assert r.resource("mmcx.lvl") == 0
    r.expect_lines([False] * 6)
    assert_snapshot(r, oracle_snapshot([0] * 6, True, False))


def gcc_camera_ahb_retention_bits_have_independent_literal_rmw(r):
    # Fixed clk-branch.c set_flags: MEM=bit14 and PERIPH=bit13.
    for enabled in (0, 1):
        r.raw_write(CAMERA_AHB, enabled)
        for bit in (1 << 13, 1 << 14):
            value = r.raw_read(CAMERA_AHB) | bit
            r.raw_write(CAMERA_AHB, value)
            assert r.raw_read(CAMERA_AHB) == value
            assert r.raw_read(CAMERA_AHB) & 1 == enabled
        assert r.raw_read(CAMERA_AHB) == 0x6000 | enabled
        for bit in (1 << 13, 1 << 14):
            value = r.raw_read(CAMERA_AHB) & ~bit
            r.raw_write(CAMERA_AHB, value)
            assert r.raw_read(CAMERA_AHB) == value
            assert r.raw_read(CAMERA_AHB) & 1 == enabled
        assert r.raw_read(CAMERA_AHB) == enabled
    r.raw_write(CAMERA_AHB, 0x6001)
    # Clearing only the branch vote leaves both retention request fields.
    r.raw_write(CAMERA_AHB, r.raw_read(CAMERA_AHB) & ~1)
    assert r.raw_read(CAMERA_AHB) == 0x6000


def gcc_camera_ahb_unknown_parent_and_real_clock_remain_zero(r):
    before = r.pure_snapshot()
    for value in (0, 1, 0x2000, 0x2001, 0x4000, 0x4001, 0x6000, 0x6001):
        r.raw_write(CAMERA_AHB, value)
        r.step(5000)
        assert r.raw_read(CAMERA_AHB) == value
        assert r.camera_period(parent=True) == 0
        assert r.camera_period() == 0
        assert_snapshot(r, before)
    return {"actual_clock": "gcc_camera_ahb_clk", "qemu_descriptor_id": 10,
            "vendor_dt_clock_id": 11, "observed_parent_period": 0,
            "observed_output_period": 0, "invented_frequency": False}


def gcc_retention_reserved_bits_and_neighbor_permissions_remain_strict(r):
    r.raw_write(CAMERA_AHB, 0x6001)
    for address in (GCC + 0xb004, GCC + 0xb00c):
        r.raw_write(address, 1)
        assert r.raw_read(address) & 1 == 1
    for address, bad in ((CAMERA_AHB, 0x7001), (CAMERA_AHB, 0x8001),
                         (GCC + 0xb004, 0x7001), (GCC + 0xb00c, 0x7001)):
        line = (f"qcom-sm8250-gcc: unsupported write 0x{bad:08x} "
                f"at 0x{address - GCC:x}")
        count = r.diagnostic_count(line)
        before = [r.raw_read(x) for x in (GCC + 0xb004, CAMERA_AHB, GCC + 0xb00c)]
        r.raw_write(address, bad)
        assert r.diagnostic_count(line) == count + 1, line
        assert [r.raw_read(x) for x in (GCC + 0xb004, CAMERA_AHB, GCC + 0xb00c)] == before
    assert r.raw_read(CAMERA_AHB) == 0x6001


ORDINARY = [
    (cold_reference_six_words_and_exact_identity, False),
    (literal_vendor_probe_rmw_does_not_enable_collapsed_domain, False),
    (literal_vendor_enable_disable_preserve_retention_requests, False),
    (software_requests_obey_parent_three_valued_truth, False),
    (hardware_requests_obey_explicit_knowledge_and_ignore_collapse, False),
    (undeclared_hw_modes_and_reserved_bits_are_atomic, False),
    (distinct_domains_and_hardware_inputs_are_isolated, False),
    (power_status_is_derived_and_read_only_in_rmw, False),
    (retention_wait_fields_do_not_supply_power_or_add_delay, False),
    (unknown_feedback_is_an_error_without_register_mutation, False),
    (narrow_unaligned_and_wide_bus_transactions_are_exact, False),
    (camera_holes_and_unrelated_gdsc_addresses_stay_unmapped, False),
    (system_reset_preserves_inputs_and_republishes_collapsed_outputs, False),
    (power_and_validity_outputs_drive_all_six_lines, False),
    (real_rpmh_mmcx_parent_and_wrong_resource_votes, True),
    (gcc_camera_ahb_retention_bits_have_independent_literal_rmw, False),
    (gcc_camera_ahb_unknown_parent_and_real_clock_remain_zero, False),
    (gcc_retention_reserved_bits_and_neighbor_permissions_remain_strict, False),
]


def default_profile_and_legacy_gpu_globals_keep_camera_unknown(args, out):
    for legacy in (False, True):
        r = CameraRig(args, out / ("legacy-gpu-on" if legacy else "default"),
                      reference=None, legacy_gpu=legacy)
        try:
            expected = oracle_snapshot([1] * 6, reference=False)
            assert_snapshot(r, expected)
            for i in range(6):
                r.reject_read(i, "reference-disabled")
                r.reject_write(i, 0, "reference-disabled")
            r.parent(True, True)
            for i in range(6):
                r.hardware(i, True, True)
            expected = oracle_snapshot([1] * 6, True, True,
                                       [True] * 6, [True] * 6, reference=False)
            assert_snapshot(r, expected)
            assert all(not x["state-known"] and not x["powered"] for x in expected["domains"])
            assert r.qom("enabled-reference", "/machine/gpu-gx-gdsc") is legacy
            assert r.qom("boot-collapsed-reference", "/machine/gpu-cx-gdsc") is legacy
            r.step(10000)
            assert_snapshot(r, expected)
        finally:
            r.close()
    return {"default_unknown": True, "legacy_gpu_globals_do_not_enable_camera": True}


def new_reference_scope_and_readonly_identity_are_enforced(args, out):
    r = CameraRig(args, out / "readonly")
    try:
        expected = oracle_snapshot([1] * 6)
        assert_snapshot(r, expected)
        assert r.qom("enabled-reference", "/machine/gpu-gx-gdsc") is False
        assert r.qom("boot-collapsed-reference", "/machine/gpu-cx-gdsc") is False
        properties = {**expected["globals"],
                      "parent-known": False, "parent-on": False}
        for i, domain in enumerate(expected["domains"]):
            properties.update({f"domain{i}.{name}": value for name, value in domain.items()})
        for name, value in properties.items():
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
            assert "error" in response and "not writable" in response["error"]["desc"], response
        response = r.raw_monitor("qom-set", {
            "path": DEVICE, "property": "enabled-reference", "value": False})
        assert "error" in response and "realized" in response["error"]["desc"], response
        assert_snapshot(r, expected)
        return {"readonly_properties_checked": len(properties),
                "realized_profile_mutation_rejected": True}
    finally:
        r.close()


def paused_normal_tcg_observes_runtime_without_guest_execution(args, out):
    r = CameraRig(args, out / "normal-tcg", accel="tcg", group=None)
    try:
        before = r.monitor("query-status")
        assert before["running"] is False
        expected = oracle_snapshot([1] * 6)
        first = assert_snapshot(r, expected)
        assert r.pure_snapshot() == first
        properties = r.monitor("qom-list", {"path": DEVICE})
        assert not any(x["name"].startswith("qtest-") for x in properties)
        after = r.monitor("query-status")
        assert after["running"] is False
        assert r.cont_commands == 0 and r.camera_mmio_operations == 0
        return {"accel": "tcg", "paused_before": before, "paused_after": after,
                "guest_cpu_executed": False, "camera_mmio_used": False,
                "pure_getters_per_snapshot": 78}
    finally:
        r.close()


STATE_CONTROLS = [0x1003, 0x2802, 0x3800, 0x4001, 0x5800, 0x6801]
STATE_HW_KNOWN = [True, False, True, False, True, False]
STATE_HW_REQUESTS = [True, True, False, True, True, False]
PENDING_CONTROLS = [0x7003, 0x8802, 0x9800, 0xa001, 0xb800, 0xc000]
PENDING_HW_KNOWN = [False, True, True, False, True, False]
PENDING_HW_REQUESTS = [True, False, False, True, True, False]


def camera_payload(snapshot):
    globals_ = snapshot["globals"]
    payload = bytearray(struct.pack(">9I", *(globals_[name] for name in GLOBAL_FIELDS)))
    payload.extend(struct.pack(">BB", snapshot["parent-known"], snapshot["parent-on"]))
    for i, domain in enumerate(snapshot["domains"]):
        payload.extend(struct.pack(">IQIIIBB", i, domain["address"], domain["dt-flags"],
                                   domain["timeout-us"], domain["control"],
                                   domain["hw-request-known"], domain["hw-request"]))
    assert len(payload) == VMSTATE_BYTES
    return bytes(payload)


def decode_camera_payload(raw):
    assert len(raw) == VMSTATE_BYTES
    globals_ = dict(zip(GLOBAL_FIELDS, struct.unpack_from(">9I", raw)))
    parent_known, parent_on = struct.unpack_from(">2B", raw, 36)
    assert parent_known in (0, 1) and parent_on in (0, 1)
    domains = []
    for i in range(6):
        offset = 38 + 26 * i
        values = struct.unpack_from(">IQIIIBB", raw, offset)
        assert values[-2] in (0, 1) and values[-1] in (0, 1)
        domains.append(dict(zip(("domain_id", "address", "dt_flags", "timeout_us",
                                 "control", "hw_request_known", "hw_request"), values)))
    return {"globals": globals_, "parent_known": bool(parent_known),
            "parent_on": bool(parent_on), "domains": domains}


def literal_rpmh_payload(scenario, input_period):
    """Exact v3 wire for the three deliberately bounded source command sets."""
    assert scenario in ("state", "parent-pending", "unknown")
    assert input_period == (1_000_000_000 << 32) // 38_400_000
    assert len(RESOURCES) == 98 and RESOURCES["mmcx.lvl"]["address"] == 0x3e030
    payload = bytearray(bytes.fromhex(PROFILE["command_db_sha256"]))
    payload.extend(struct.pack(">Q", 1000))
    for voter in range(4):
        payload.extend(struct.pack(
            ">4IB", int(scenario == "parent-pending" and voter == 2),
            int(scenario == "state" and voter == 2), 0, 0, 0))
        for tcs in range(8):
            selected = scenario != "unknown" and (voter, tcs) == (2, 0)
            pending = selected and scenario == "parent-pending"
            payload.extend(struct.pack(">3I", 0, 0x1010000 if selected else 0,
                                       1 if selected else 0))
            for slot in range(16):
                values = (0, 0, 0, 0, 0)
                if selected and slot == 0:
                    values = (0x10108, 0x3e030, 2 if pending else 1,
                              ISSUED if pending else ISSUED | COMPLETE, 0)
                payload.extend(struct.pack(">5I", *values))
            # Cursor is signed; an idle reset TCS is -1, a finished TCS is16.
            payload.extend(struct.pack(">iqBB", 0 if pending else 16 if selected else -1,
                                       4000 if pending else -1, int(pending), 0))
    for resource in range(98):
        # 16 vote words, four staged words, and four zero dirty bytes
        # (the latter are packed here as one zero u32).
        values = [0] * 21
        if scenario == "state" and resource == 3:
            values[2 * 4] = 1
        payload.extend(struct.pack(">21I", *values))
    payload.extend(struct.pack(">Q", input_period))
    # No source scenario drives a sleep input; all four retained levels are low.
    payload.extend(bytes(4))
    assert len(payload) == 19424
    return bytes(payload)


def unique_section(data, name, version, payload_bytes):
    assert data[:4] == b"QEVM"
    encoded = name.encode()
    needle = bytes([len(encoded)]) + encoded + struct.pack(">II", 0, version)
    found, start = [], 0
    while True:
        offset = data.find(needle, start)
        if offset < 0:
            break
        header = offset - 5
        payload = offset + len(needle)
        if header >= 8 and data[header] in (1, 4):
            found.append((header, payload))
        start = offset + 1
    assert len(found) == 1, (name, found)
    header, payload = found[0]
    section_id = int.from_bytes(data[header + 1:header + 5], "big")
    footer = b"\x7e" + section_id.to_bytes(4, "big")
    assert data[payload + payload_bytes:payload + payload_bytes + 5] == footer, (
        name, payload, payload_bytes, data[payload + payload_bytes:payload + payload_bytes + 12].hex())
    return {"name": name, "instance_id": 0, "version": version,
            "header_offset": header, "section_id": section_id,
            "payload_offset": payload, "payload_bytes": payload_bytes,
            "footer_offset": payload + payload_bytes, "footer_hex": footer.hex()}


def inspect_actual_stream(path, snapshot, provider, scenario, gcc_value):
    data = path.read_bytes()
    camera = unique_section(data, TYPE, 1, VMSTATE_BYTES)
    offset = camera["payload_offset"]
    raw = data[offset:offset + VMSTATE_BYTES]
    expected = camera_payload(snapshot)
    assert raw == expected, ("camera payload mismatch",
                             [i for i, pair in enumerate(zip(raw, expected)) if pair[0] != pair[1]])
    camera_file = path.with_name(path.name + ".camera-v1.payload.bin")
    camera_file.write_bytes(raw)
    camera.update(payload_file=camera_file.name, payload_sha256=digest(camera_file),
                  payload_hex=raw.hex(), decoded=decode_camera_payload(raw),
                  all_bytes_match_stopped_source_qom=True)
    rpmh = unique_section(data, "qcom-sm8250-rpmh", 3, 19424)
    offset = rpmh["payload_offset"]
    actual_rpmh = data[offset:offset + 19424]
    expected_rpmh = literal_rpmh_payload(scenario, provider["xo_input_period"])
    assert actual_rpmh == expected_rpmh, (
        "RPMh source scenario mismatch",
        [i for i, pair in enumerate(zip(actual_rpmh, expected_rpmh)) if pair[0] != pair[1]][:32])
    rpmh_file = path.with_name(path.name + ".rpmh-v3.payload.bin")
    rpmh_file.write_bytes(actual_rpmh)
    rpmh.update(payload_file=rpmh_file.name, payload_sha256=digest(rpmh_file),
                all_bytes_match_literal_source_scenario=True, scenario=scenario,
                serialized_sleep_requests=list(actual_rpmh[-4:]),
                wait_reference_subsection_present=False)
    gcc = unique_section(data, "qcom-sm8250-gcc", 3, GCC_VMSTATE_BYTES)
    offset = gcc["payload_offset"]
    # GCC's fixed v3 first field is regs[], serialized as BE32. This checks
    # real retained bits independently of the target's MMIO implementation.
    slots = {f"0x{reg:x}": struct.unpack_from(">I", data, offset + reg)[0]
             for reg in (0xb004, 0xb008, 0xb00c)}
    assert slots == {"0xb004": 0, "0xb008": gcc_value, "0xb00c": 0}, slots
    gcc.update(camera_ahb_offset_in_payload=0xb008, selected_registers=slots,
               payload_sha256=hashlib.sha256(data[offset:offset + GCC_VMSTATE_BYTES]).hexdigest(),
               complete_gcc_runtime_oracle_claimed=False)
    sections = {"camera": camera, "rpmh": rpmh, "gcc": gcc,
                "stream_sha256": digest(path), "stream_bytes": len(data)}
    write_json(path.with_name(path.name + ".sections.json"), sections)
    return sections


def provider_snapshot(r):
    assert r.has_rpmh
    return {
        "mmcx_value": r.resource("mmcx.lvl"),
        "mmcx_present": r.resource("mmcx.lvl", "present"),
        "mmcx_enabled": r.resource("mmcx.lvl", "enabled"),
        "cx_value": r.resource("cx.lvl"),
        "mx_value": r.resource("mx.lvl"),
        "gfx_value": r.resource("gfx.lvl"),
        "command_db_sha256": r.qom("cmd-db-sha256", RPMH),
        "xo_input_period": r.period("xo-board"),
    }


def stopped_snapshot(r):
    before = r.monitor("query-status")
    assert before["running"] is False
    camera = r.pure_snapshot()
    assert r.pure_snapshot() == camera
    provider = provider_snapshot(r)
    after = r.monitor("query-status")
    assert after["running"] is False
    return after, camera, provider


def save_source(r, path, scenario, witness, expected_camera, gcc_value):
    assert r.raw_read(CAMERA_AHB) == gcc_value
    assert r.camera_period() == 0 and r.camera_period(parent=True) == 0
    r.raw_write(RAM, witness)
    r.monitor("stop")
    status, camera, provider = stopped_snapshot(r)
    assert camera == expected_camera, (camera, expected_camera)
    value = 1 if scenario == "state" else 0
    assert provider["mmcx_value"] == value and provider["mmcx_enabled"] == int(bool(value))
    assert provider["mmcx_present"] == 1
    assert all(provider[name] == 0 for name in ("cx_value", "mx_value", "gfx_value"))
    write_json(r.directory / "source-qom-before-save.json", {
        "query_status": status, "source_virtual_ns": r.last_clock,
        "qom": camera, "provider": provider, "repeated_pure_qom_unchanged": True})
    r.monitor("migrate", file_channels(path))
    sent = wait_migration(r)
    assert sent["status"] == "completed"
    final_status, after, after_provider = stopped_snapshot(r)
    assert final_status["status"] == "postmigrate"
    assert after == camera and after_provider == provider
    write_json(r.directory / "source-qom-after-save.json", {
        "query_status": final_status, "source_virtual_ns": r.last_clock,
        "qom": after, "provider": after_provider})
    write_json(r.directory / "source-query-migrate.json", sent)
    sections = inspect_actual_stream(path, after, after_provider, scenario, gcc_value)
    return {
        "scenario": scenario, "file": path.name, "sha256": digest(path),
        "bytes": path.stat().st_size, "source_directory": r.directory.name,
        "source_status": "completed", "source_virtual_ns": r.last_clock,
        "source_query_migrate": sent, "source_qom": camera,
        "source_provider_qom": provider, "ram_witness": witness,
        "gcc_camera_ahb_value": gcc_value, "sections": sections,
        "source_qom_before_file": r.directory.name + "/source-qom-before-save.json",
        "source_qom_after_file": r.directory.name + "/source-qom-after-save.json",
        "source_query_migrate_file": r.directory.name + "/source-query-migrate.json",
        "source_stream_modified": False,
    }


def prewarm_target(r, scenario, target_ns):
    assert r.has_rpmh and r.monitor("query-status")["running"] is False
    assert r.cont_commands == 0
    # qmp_cont in INMIGRATE merely changes autostart. Keep this real old
    # command pending; never claim its timer ran while the target was paused.
    r.program("mmcx.lvl", 3 if scenario == "state" else 1)
    r.trigger()
    assert r.resource("mmcx.lvl") == 0 and r.status() == ISSUED
    if scenario == "state":
        controls = [1, 0, 1, 0, 1, 0]
        known, requests = [True] * 6, [False] * 6
    else:
        controls = [HW_MODE, HW_MODE, 0, 0, 0, 0]
        known, requests = [False] * 6, [True] * 6
    for i in range(6):
        r.hardware(i, known[i], requests[i])
        r.write_domain(i, controls[i])
    r.raw_write(CAMERA_AHB, 0)
    assert r.raw_read(CAMERA_AHB) == 0
    r.raw_write(RAM, 0xdeadbeef)
    r.monitor("stop")
    r.set_clock(target_ns)
    status, old, provider = stopped_snapshot(r)
    assert old == oracle_snapshot(controls, True, False, known, requests)
    assert provider["mmcx_value"] == 0
    write_json(r.directory / "destination-before-load.json", {
        "query_status": status, "destination_virtual_ns": target_ns,
        "qom": old, "provider": provider,
        "old_parent_command_pending": True, "old_parent_command_completed": False,
        "old_parent_command_data": 3 if scenario == "state" else 1,
        "old_parent_command_deadline_ns": 1000, "gcc_camera_ahb_value": 0})
    return old


def receive_source(target, path, source_record):
    old_mmio, old_cont = target.device_mmio_operations, target.cont_commands
    target.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(target)
    assert received["status"] == "completed"
    status, camera, provider = stopped_snapshot(target)
    assert camera == source_record["source_qom"], (camera, source_record["source_qom"])
    assert provider == source_record["source_provider_qom"]
    assert target.device_mmio_operations == old_mmio and target.cont_commands == old_cont
    assert target.raw_read(RAM) == source_record["ram_witness"]
    levels = [domain["powered" if target.group == "power" else "state-known"]
              for domain in camera["domains"]]
    target.expect_lines(levels, require_seen=target.group == "state-known")
    if target.group == "power":
        assert {i for i, value in enumerate(levels) if value} <= target.seen_irq
    assert target.monitor("query-status")["running"] is False
    first = {
        "query_status": status, "destination_virtual_ns": target.last_clock,
        "qom": camera, "provider": provider, "ram_witness": source_record["ram_witness"],
        "intercepted_group": target.group, "gpio_levels": levels,
        "gpio_lines_seen": sorted(target.seen_irq),
        "camera_gcc_rpmh_mmio_since_incoming": False,
        "qmp_cont_since_incoming": False,
    }
    write_json(target.directory / "destination-before-first-device-mmio.json", first)
    write_json(target.directory / "destination-query-migrate.json", received)
    return received, first


def expect_load_failure(target, path, expected):
    requested, command_error = False, None
    assert target.process.poll() is None
    try:
        requested = True
        target.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as exc:
        command_error = str(exc)
    code = target.process.wait(timeout=20)
    stderr = target.log_path.read_text(errors="replace")
    assert requested and code == 1, (requested, code, stderr)
    assert TYPE in stderr and expected in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {
        "destination_exit": code, "request_sent": requested,
        "expected_diagnostic": expected, "specific_load_failure_observed": True,
        "command_error": command_error, "target_directory": target.directory.name,
        "stderr_file": target.directory.name + "/qemu.stderr",
    }


def corrupt_one_field(original, changed, section, kind):
    original_sha = digest(original)
    data = original.read_bytes()
    if kind == "domain-address":
        domain, relative, width = 4, 38 + 4 * 26 + 4, 8
        old, xor = ADDRESSES[4], 4
        expected_diagnostic = f"{old:x} != {old ^ xor:x}"
    else:
        assert kind == "control"
        domain, relative, width = 5, 38 + 5 * 26 + 20, 4
        old, xor = STATE_CONTROLS[5], 4
        expected_diagnostic = TYPE + ": invalid migrated camera domain 5"
    absolute = section["payload_offset"] + relative
    assert int.from_bytes(data[absolute:absolute + width], "big") == old
    mutated = bytearray(data)
    mutated[absolute:absolute + width] = (old ^ xor).to_bytes(width, "big")
    differences = [i for i, (a, b) in enumerate(zip(data, mutated)) if a != b]
    assert differences == [absolute + width - 1]
    assert (data[differences[0]] ^ mutated[differences[0]]).bit_count() == 1
    assert mutated[:absolute] == data[:absolute]
    assert mutated[absolute + width:] == data[absolute + width:]
    changed.write_bytes(mutated)
    assert digest(original) == original_sha and len(mutated) == len(data)
    return {
        "field": kind, "domain_id": domain, "field_offset_in_camera_payload": relative,
        "field_width": width, "absolute_field_offset": absolute,
        "original_value": old, "modified_value": old ^ xor, "xor_mask": xor,
        "changed_byte_offsets": differences, "changed_bits": 1,
        "original_file": original.name, "original_sha256": original_sha,
        "snapshot_file": changed.name, "snapshot_sha256": digest(changed),
        "snapshot_bytes": len(mutated), "stream_modified": True,
        "original_stream_unchanged": True, "expected_diagnostic": expected_diagnostic,
    }


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = CameraRig(args, out / name, rpmh=True, **kwargs)
        rigs.append(rig)
        return rig

    def passed(name, **metadata):
        report["checks"].append({"name": name, "passed": True, **metadata})

    def migration_record(name, source, target, path, source_record, received, first):
        return {
            "name": name, "passed": True, "source_status": "completed",
            "target_status": received["status"], "destination_status": received["status"],
            "source": source_record["source_query_migrate"], "destination": received,
            "snapshot_file": path.name, "snapshot_sha256": digest(path),
            "snapshot_bytes": path.stat().st_size, "stream_modified": False,
            "source_directory": source.directory.name, "target_directory": target.directory.name,
            "source_virtual_ns": source.last_clock,
            "destination_virtual_ns": target.last_clock,
            "snapshot_before": source_record["source_qom"], "snapshot_after": first["qom"],
            "source_provider": source_record["source_provider_qom"],
            "target_provider": first["provider"], "sections": source_record["sections"],
            "intercepted_group": target.group, "first_target_gpio": first["gpio_levels"],
            "target_first_observation_before_device_mmio": True,
            "source_qom_before_file": source_record["source_qom_before_file"],
            "source_qom_after_file": source_record["source_qom_after_file"],
            "target_qom_before_first_mmio_file":
                target.directory.name + "/destination-before-first-device-mmio.json",
            "target_query_migrate_file": target.directory.name + "/destination-query-migrate.json",
            "continued_after_restore": False,
        }

    try:
        source = new("state-source")
        source.run_one("mmcx.lvl", 1)
        assert source.last_clock == 1000 and source.status() == ISSUED | COMPLETE
        for i in range(6):
            source.hardware(i, STATE_HW_KNOWN[i], STATE_HW_REQUESTS[i])
            source.write_domain(i, STATE_CONTROLS[i])
        source.raw_write(CAMERA_AHB, GCC_RETENTION_MASK)
        source.step(137)
        assert source.last_clock == 1137
        state_path = out / "camera-state.bin"
        expected = oracle_snapshot(STATE_CONTROLS, True, True,
                                   STATE_HW_KNOWN, STATE_HW_REQUESTS)
        state_record = save_source(source, state_path, "state", 0x43414d31,
                                   expected, GCC_RETENTION_MASK)
        report["migration_sources"].append(state_record)

        target = new("state-target", incoming=True)
        old = prewarm_target(target, "state", 987654321)
        assert old != state_record["source_qom"]
        received, first = receive_source(target, state_path, state_record)
        first_migration = migration_record(
            "six_domain_state_and_gcc_retention", source, target, state_path,
            state_record, received, first)
        report["migrations"].append(first_migration)
        assert target.raw_read(CAMERA_AHB) == GCC_RETENTION_MASK
        assert target.camera_period(parent=True) == target.camera_period() == 0
        assert_feedback_reads(target, expected)
        passed("actual_aggregate_state_and_gcc_restore_before_device_mmio",
               migration="six_domain_state_and_gcc_retention",
               distinct_domain_identities=6, different_target_epoch=True,
               actual_gcc_value=GCC_RETENTION_MASK)

        # The discarded target command was already due at its much later
        # epoch. Resuming must not apply its old data3 over the restored data1.
        target.monitor("cont")
        target.step(1)
        assert target.resource("mmcx.lvl") == 1
        assert_snapshot(target, expected)
        known, requests = STATE_HW_KNOWN.copy(), STATE_HW_REQUESTS.copy()
        known[1] = True
        target.hardware(1, True, True)
        expected = oracle_snapshot(STATE_CONTROLS, True, True, known, requests)
        assert_snapshot(target, expected)
        target.expect_lines([x["powered"] for x in expected["domains"]], require_seen=False)
        target.run_one("mmcx.lvl", 0)
        assert_snapshot(target, oracle_snapshot(STATE_CONTROLS, True, False, known, requests))
        target.expect_lines([False] * 6, require_seen=False)
        target.run_one("mmcx.lvl", 2)
        assert_snapshot(target, oracle_snapshot(STATE_CONTROLS, True, True, known, requests))
        assert target.raw_read(CAMERA_AHB) == GCC_RETENTION_MASK
        assert target.camera_period(parent=True) == target.camera_period() == 0
        target.raw_write(CAMERA_AHB, 0x2001)
        assert target.raw_read(CAMERA_AHB) == 0x2001
        assert digest(state_path) == state_record["sha256"]
        first_migration["continued_after_restore"] = True
        first_migration["old_target_parent_request_did_not_apply"] = True
        first_migration["restored_gcc_and_unknown_clock_observed"] = True
        passed("restored_domains_continue_with_actual_parent_votes_and_retention_rmw",
               discarded_target_command_data=3, restored_parent_value=1)

        pending = new("parent-pending-source", group="state-known")
        pending.step(3000)
        for i in range(6):
            pending.hardware(i, PENDING_HW_KNOWN[i], PENDING_HW_REQUESTS[i])
            pending.write_domain(i, PENDING_CONTROLS[i])
        pending.raw_write(CAMERA_AHB, GCC_RETENTION_MASK)
        pending.dwrite(0xd00, 1)  # Real DRV2 completion IRQ is enabled.
        pending.program("mmcx.lvl", 2)
        pending.trigger()
        pending.step(400)
        assert pending.last_clock == 3400 and pending.status() == ISSUED
        assert pending.resource("mmcx.lvl") == 0
        pending_expected = oracle_snapshot(PENDING_CONTROLS, True, False,
                                           PENDING_HW_KNOWN, PENDING_HW_REQUESTS)
        pending_path = out / "camera-parent-pending.bin"
        pending_record = save_source(pending, pending_path, "parent-pending", 0x43414d32,
                                     pending_expected, GCC_RETENTION_MASK)
        report["migration_sources"].append(pending_record)

        resumed = new("parent-pending-target", incoming=True, group="state-known")
        old = prewarm_target(resumed, "parent-pending", 3400)
        assert old != pending_record["source_qom"]
        before_mmio = resumed.device_mmio_operations
        received, first = receive_source(resumed, pending_path, pending_record)
        second_migration = migration_record(
            "actual_mmcx_pending_command_and_six_domains", pending, resumed,
            pending_path, pending_record, received, first)
        second_migration.update(absolute_parent_deadline_ns=4000,
                                remaining_ns_at_transfer=600,
                                provider_absolute_clock_alignment_preserved=True)
        report["migrations"].append(second_migration)
        assert resumed.device_mmio_operations == before_mmio
        passed("actual_parent_pending_and_all_domains_restore_before_device_mmio",
               migration="actual_mmcx_pending_command_and_six_domains",
               absolute_parent_deadline_ns=4000, remaining_ns_at_transfer=600)

        resumed.monitor("cont")
        resumed.step(599)
        assert resumed.last_clock == 3999 and resumed.resource("mmcx.lvl") == 0
        assert_snapshot(resumed, pending_expected)
        resumed.expect_lines([True] * 6)
        assert resumed.device_mmio_operations == before_mmio
        resumed.step(1)
        assert resumed.last_clock == 4000 and resumed.resource("mmcx.lvl") == 2
        powered_expected = oracle_snapshot(PENDING_CONTROLS, True, True,
                                           PENDING_HW_KNOWN, PENDING_HW_REQUESTS)
        assert_snapshot(resumed, powered_expected)
        resumed.expect_lines([False, True, True, True, True, True])
        assert resumed.device_mmio_operations == before_mmio
        write_json(resumed.directory / "deadline-before-first-device-mmio.json", {
            "virtual_ns": 4000, "qom": powered_expected,
            "mmcx_value": 2, "actual_remaining_599_then_1_ns": True,
            "camera_gcc_rpmh_mmio_since_incoming": False,
            "intercepted_group": "state-known",
            "gpio_levels": [False, True, True, True, True, True],
        })
        # Only now inspect either device's MMIO, after timer/real-line proof.
        assert resumed.raw_read(CAMERA_AHB) == GCC_RETENTION_MASK
        assert resumed.camera_period(parent=True) == resumed.camera_period() == 0
        assert resumed.status() == ISSUED | COMPLETE and resumed.dread(0xd04) == 1
        assert_feedback_reads(resumed, powered_expected)
        resumed.hardware(0, True, True)
        known = PENDING_HW_KNOWN.copy()
        known[0] = True
        assert_snapshot(resumed, oracle_snapshot(PENDING_CONTROLS, True, True,
                                                 known, PENDING_HW_REQUESTS))
        resumed.expect_lines([True] * 6)
        resumed.run_one("mmcx.lvl", 0)
        assert_snapshot(resumed, oracle_snapshot(PENDING_CONTROLS, True, False,
                                                 known, PENDING_HW_REQUESTS))
        resumed.expect_lines([True] * 6)
        assert digest(pending_path) == pending_record["sha256"]
        second_migration["continued_after_restore"] = True
        second_migration["timer_and_validity_verified_before_first_device_mmio"] = True
        passed("restored_real_parent_deadline_and_unknown_hw_request_remain_distinct",
               observed_deadline_ns=4000, camera_timer_invented=False)

        unknown = new("unknown-source", reference=None)
        unknown.step(73)
        unknown_path = out / "camera-unknown.bin"
        unknown_record = save_source(
            unknown, unknown_path, "unknown", 0x43414d30,
            oracle_snapshot([1] * 6, True, False, reference=False), 0)
        report["migration_sources"].append(unknown_record)
        # Both directions use actual complete source streams, not edited profiles.
        for name, path, record, target_reference, expected_error in (
                ("reference_to_unknown", state_path, state_record, False, "0 != 1"),
                ("unknown_to_reference", unknown_path, unknown_record, True, "1 != 0")):
            reject = new("reject-" + name, reference=target_reference, incoming=True)
            reject.set_clock(record["source_virtual_ns"])
            metadata = expect_load_failure(reject, path, expected_error)
            result = {
                "name": name, "passed": True, "source_status": "completed",
                "source_reference": not target_reference,
                "target_reference": target_reference,
                "snapshot_file": path.name, "snapshot_sha256": digest(path),
                "snapshot_bytes": path.stat().st_size, "stream_modified": False,
                "source_directory": record["source_directory"], **metadata,
            }
            report["rejected_migrations"].append(result)
            passed("camera_profile_" + name + "_rejected", **metadata)

        for kind, basename in (
                ("domain-address", "camera-bad-domain-address.bin"),
                ("control", "camera-bad-control.bin")):
            path = out / basename
            mutation = corrupt_one_field(state_path, path, state_record["sections"]["camera"], kind)
            reject = new("reject-corrupt-" + kind, incoming=True)
            reject.set_clock(state_record["source_virtual_ns"])
            metadata = expect_load_failure(reject, path, mutation["expected_diagnostic"])
            assert mutation["expected_diagnostic"] == metadata["expected_diagnostic"]
            evidence = {**mutation, **metadata}
            result = {"name": kind, "passed": True, "source_status": "completed",
                      "source_directory": state_record["source_directory"],
                      **evidence}
            report["corrupted_stream_rejections"].append(result)
            passed("single_bit_" + kind.replace("-", "_") + "_corruption_rejected",
                   **evidence)
        assert all(digest(out / item["file"]) == item["sha256"]
                   for item in report["migration_sources"])
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--machine", default=DEFAULT_MACHINE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    out = args.out or Path(tempfile.mkdtemp(prefix="tb371fc-camera-gdsc-"))
    out.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    original_hash = digest(Path(args.qemu))
    report = {
        "schema": "tb371fc-camera-gdsc-v1", "suite": "camera-gdsc-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)),
        "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 2, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2,
        "expected_unmodified_streams": 3, "expected_stream_files": 5,
        "vmstate_version": 1, "vmstate_payload_bytes": VMSTATE_BYTES,
        "domain_count": 6, "register_count": 6, "sparse_window_count": 6,
        "mapped_bytes": 24, "gpio_output_count": 12, "parent_resource_index": 3,
        "reference_identity": IDENTITY, "hardware_mode_domain_mask": 3,
        "modeled_timer_count": 0, "modeled_clock_count": 0,
        "gcc_vmstate_version": 3, "gcc_camera_ahb_clock_id": 10,
        "gcc_camera_ahb_write_mask": GCC_RETENTION_MASK,
        "actual_dt_sha256": DT_SHA256, "fixed_gdsc_driver_sha256": DRIVER_SHA256,
        "reference_command_db_sha256": PROFILE["command_db_sha256"],
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "camera_execution_observed": False, "analog_power_modeled": False,
        "retention_effect_modeled": False, "android_boot_passed": False,
        "binary_unchanged": False, "checks": [], "migrations": [],
        "rejected_migrations": [], "corrupted_stream_rejections": [],
        "migration_sources": [], "passed": False,
    }
    try:
        report["old_gpu_test_identities"] = old_gpu_identities()
        assert len(ORDINARY) == 18
        for index, (test, rpmh) in enumerate(ORDINARY):
            rig = None
            fixture = out / f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = CameraRig(args, fixture, rpmh=rpmh)
                metadata = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **metadata})
            except Exception:
                report["checks"].append({"name": test.__name__, "passed": False,
                                         "fixture": fixture.name,
                                         "error": traceback.format_exc()})
                raise
            finally:
                if rig is not None:
                    rig.close()
        for test in (
                default_profile_and_legacy_gpu_globals_keep_camera_unknown,
                new_reference_scope_and_readonly_identity_are_enforced,
                paused_normal_tcg_observes_runtime_without_guest_execution):
            fixture = out / test.__name__
            fixture.mkdir()
            try:
                metadata = test(args, fixture) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **metadata})
            except Exception:
                report["checks"].append({"name": test.__name__, "passed": False,
                                         "fixture": fixture.name,
                                         "error": traceback.format_exc()})
                raise
        migration_checks(args, out, report)
        assert len(report["checks"]) == EXPECTED_CHECKS
        assert len({x["name"] for x in report["checks"]}) == EXPECTED_CHECKS
        assert all(x["passed"] for x in report["checks"])
        assert len(report["migrations"]) == 2
        assert all(x["source_status"] == x["target_status"] == "completed" and
                   x["continued_after_restore"] for x in report["migrations"])
        assert len(report["rejected_migrations"]) == 2
        assert len(report["corrupted_stream_rejections"]) == 2
        assert len(report["migration_sources"]) == 3
        files = [out / x["file"] for x in report["migration_sources"]]
        files += [out / x["snapshot_file"] for x in report["corrupted_stream_rejections"]]
        assert len(files) == len(set(files)) == 5
        report["actual_stream_files"] = [
            {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path)}
            for path in files
        ]
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_before"] == report["qemu_sha256_after"]
        report["passed"] = report["passed"] and report["binary_unchanged"]
        report["seconds"] = round(time.monotonic() - started, 6)
        write_json(out / "result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
