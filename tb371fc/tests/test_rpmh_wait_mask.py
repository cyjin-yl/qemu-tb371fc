#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Real RPMh WAIT tests for an explicit, unmeasured reserved-bit reference policy.

The original strict RPMh gates remain unchanged. Only formal CI executes QEMU.
This does not establish silicon WI/RAZ behavior, AOP firmware, or Android boot.
"""
import argparse
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
from test_rpmh_display import DisplayRig, DISPLAY, DISPLAY_BASE
from test_rpmh_sm8250 import PROFILE, RESOURCES, ISSUED, COMPLETE
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE = "qcom-sm8250-rpmh"
DEVICE = "/machine/rpmh"
POLICY = "reference-wait-mask-reserved"
SUBSECTION = TYPE + "/wait-reserved-reference"
BASE, GCC, RAM = 0x18200000, 0x100000, 0x80010000
WAIT, CONTROL, IDLE, ENABLE = 0x10, 0x14, 0x18, 0x1c
TCS_BASE, TCS_STRIDE = 0xd00, 0x2a0
MASK, LATENCY = 0xffff, 1000
NO_RESPONSE, WITH_RESPONSE = 0x10008, 0x10108
PERIOD_UNIT = 1_000_000_000 << 32
MAIN_BYTES, SUB_BYTES, EXPECTED_CHECKS = 19420, 24, 17
DIAGNOSTICS = (
    "wait-command-mask", "wait-reserved-masked-writes",
    "wait-reserved-last-voter", "wait-reserved-last-tcs",
    "wait-reserved-last-raw", "wait-reserved-last-effective",
    "wait-reserved-last-ignored",
)
OLD_TEST_BLOBS = {
    "test_rpmh_sm8250.py": "2eb42c9aa4187d585a903dcda659cc004a9aee7d",
    "test_rpmh_display.py": "de7a6303cf009cf07115e87eb42db8016ccbc6f0",
}
DEFAULT_MACHINE = (
    "tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
    "rpmh-profile=reference,rpmh-boot-configured=off"
)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def old_test_identities():
    found = {}
    for name, expected in OLD_TEST_BLOBS.items():
        raw = Path(__file__).with_name(name).read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        assert actual == expected, (name, actual, expected)
        found[name] = {"git_blob": actual, "bytes": len(raw),
                       "sha256": hashlib.sha256(raw).hexdigest()}
    return found


def qom_expected(count=0, voter=0, tcs=0, raw=0):
    return dict(zip(DIAGNOSTICS, (
        MASK, count, voter, tcs, raw, raw & MASK, raw & ~MASK,
    )))


class WaitRig(DisplayRig):
    """Existing typed RPMh helpers, with explicit policy and retained wire logs."""

    def __init__(self, args, directory, *, policy=True, incoming=False,
                 accel="qtest"):
        self.args, self.directory = args, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.policy, self.accel = bool(policy), accel
        self.last_clock = 0
        self.rpmh_mmio_operations = 0
        self.cont_commands = 0
        self.intercept_outputs = accel == "qtest"
        self.connections = []
        self.events, self.irq = [], {i: False for i in range(45)}
        self._request_id = 0
        # Evidence paths can exceed sockaddr_un's limit in Actions. Only the
        # ephemeral transport endpoints use a short /tmp directory.
        self.socket_directory = Path(tempfile.mkdtemp(prefix="rpmhwm-"))
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory / "qtest.jsonl").open("w")
        self.qmplog = (self.directory / "qmp.jsonl").open("w")
        command = [args.qemu, "-machine", args.machine, "-accel", accel,
                   "-m", "128M", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors",
                   "-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
                   "-qtest-log", "/dev/null",
                   "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off"]
        # None omits the property, exercising its actual default.
        if policy is not None:
            command += ["-global", f"{TYPE}.{POLICY}={'on' if policy else 'off'}"]
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
            if self.intercept_outputs:
                self.command(f"irq_intercept_out {args.qom_path} sysbus-irq")
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
            if BASE <= address < BASE + 0x30000 or DISPLAY_BASE <= address < DISPLAY_BASE + 0x10000:
                self.rpmh_mmio_operations += 1
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
                self.irq[int(number)] = state == "raise"
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

    def qom(self, name):
        return self.monitor("qom-get", {"path": self.args.qom_path, "property": name})

    def diagnostics(self):
        return {name: self.qom(name) for name in DIAGNOSTICS}

    def pure_snapshot(self):
        return {
            "policy": self.qom(POLICY),
            "diagnostics": self.diagnostics(),
            "cmd_db_sha256": self.qom("cmd-db-sha256"),
            "xo_value": self.resource("xo.lvl"),
            "ldoa12_value": self.resource("ldoa12"),
            "xo_input_period": self.period("xo-board"),
            "xo_output_period": self.period("bi_tcxo"),
        }

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return ns

    def start_preserving_wait(self, mask=1, drv=2, tcs=0):
        self.dwrite(self.tcs(tcs, ENABLE), mask, drv)
        self.dwrite(self.tcs(tcs, CONTROL), 0x10000, drv)
        self.dwrite(self.tcs(tcs, CONTROL), 0x1010000, drv)

    def wait_word(self, value, drv=2, tcs=0):
        self.dwrite(self.tcs(tcs, WAIT), value, drv)

    def wait_read(self, drv=2, tcs=0):
        return self.dread(self.tcs(tcs, WAIT), drv)

    def expect_diagnostic(self, text, before):
        lines = self.log_path.read_text(errors="replace").splitlines()
        assert sum(line == text for line in lines) == before + 1, (text, lines[-12:])

    def diagnostic_count(self, text):
        return self.log_path.read_text(errors="replace").splitlines().count(text)

    def rejected_wait(self, raw, drv=2, tcs=0):
        base = 0x1c00 if drv == DISPLAY else TCS_BASE
        offset = base + tcs * TCS_STRIDE + WAIT
        line = f"{TYPE}: DRV{drv} unsupported write @0x{offset:04x} value=0x{raw:08x}"
        before = self.diagnostic_count(line)
        self.wait_word(raw, drv, tcs)
        self.expect_diagnostic(line, before)

    def masked_wait(self, raw, drv=2, tcs=0):
        line = (f"{TYPE}: DRV{drv} TCS{tcs} reference WAIT mask "
                f"raw=0x{raw:08x} effective=0x{raw & MASK:04x} "
                f"ignored=0x{raw & ~MASK:08x}")
        before = self.diagnostic_count(line)
        self.wait_word(raw, drv, tcs)
        self.expect_diagnostic(line, before)

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


def default_strict_geometry_and_readonly_diagnostics(r):
    assert r.qom(POLICY) is False
    assert r.diagnostics() == qom_expected()
    for drv, config in enumerate((0x80008208, 0x80008208, 0x80008208, 0x80000002)):
        actual = r.dread(0xc, drv)
        assert actual == config and (actual >> 27) & 0x1f == 16
    for name in DIAGNOSTICS:
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": 1})
        assert "error" in response and "not writable" in response["error"]["desc"], response
    response = r.raw_monitor("qom-set", {"path": DEVICE, "property": POLICY, "value": True})
    assert "error" in response and "realized" in response["error"]["desc"], response
    assert r.qom(POLICY) is False and r.diagnostics() == qom_expected()


def strict_reserved_wait_writes_are_atomic(r):
    assert r.qom(POLICY) is False
    for drv in range(4):
        r.wait_word(0x8001, drv)
        r.program("xo.lvl", 3, drv=drv)
        before = (r.wait_read(drv), r.dread(r.tcs(0, 0x38), drv),
                  r.dread(r.tcs(0, ENABLE), drv), r.dread(TCS_BASE + 4, drv))
        for raw in (0x778bd800, 0x10000, 0xffffffff):
            r.rejected_wait(raw, drv)
            assert (r.wait_read(drv), r.dread(r.tcs(0, 0x38), drv),
                    r.dread(r.tcs(0, ENABLE), drv), r.dread(TCS_BASE + 4, drv)) == before
    assert r.diagnostics() == qom_expected()
    r.step(10000)
    assert r.resource("xo.lvl") == 0
    r.expect_hz("bi_tcxo", 0)


def reference_masks_only_high_bits_and_audits_each_write(r):
    assert r.qom(POLICY) is True
    for count, raw in enumerate((0x778bd800, 0x10001, 0xffff0000, 0xffff0000), 1):
        r.masked_wait(raw)
        assert r.wait_read() == raw & MASK
        assert r.diagnostics() == qom_expected(count, 2, 0, raw)
        assert r.diagnostics() == r.diagnostics()
    old = r.diagnostics()
    r.wait_word(0x8001)
    assert r.wait_read() == 0x8001 and r.diagnostics() == old
    assert r.dread(r.tcs(0, IDLE)) == 1 and r.dread(TCS_BASE + 4) == 0
    r.step(10000)
    assert r.resource("xo.lvl") == 0
    r.expect_hz("bi_tcxo", 0)


def reference_isolated_across_all_voters_and_boundary_tcs(r):
    expected = {}
    for drv in range(4):
        tcs = 1 if drv == DISPLAY else 7
        raw = (1 << (16 + drv)) | (1 << tcs)
        r.masked_wait(raw, drv, tcs)
        expected[(drv, tcs)] = raw & MASK
        assert r.diagnostics() == qom_expected(drv + 1, drv, tcs, raw)
    for drv in range(4):
        for tcs in range(2 if drv == DISPLAY else 8):
            assert r.wait_read(drv, tcs) == expected.get((drv, tcs), 0)
    old = r.diagnostics()
    # An unmodeled third display TCS remains an error; policy cannot extend geometry.
    r.rejected_wait(0x10001, DISPLAY, 2)
    assert r.diagnostics() == old and r.resource("xo.lvl") == 0


def low_wait_bits_control_completion_without_response(r):
    r.program("xo.lvl", 1, slot=0, msg=NO_RESPONSE)
    r.program("ldoa12", 1800, slot=15, msg=NO_RESPONSE)
    r.wait_word(1 << 15)
    r.dwrite(TCS_BASE, 1)
    r.start_preserving_wait((1 << 15) | 1)
    r.step(999)
    assert r.status() == ISSUED and r.resource("xo.lvl") == 0
    r.step(1)
    r.expect_hz("bi_tcxo", 19_200_000)
    assert r.resource("xo.lvl") == 1 and r.status() == ISSUED
    assert r.status(slot=15) == ISSUED and r.dread(TCS_BASE + 4) == 0
    r.step(999)
    assert r.resource("ldoa12") == 0
    r.step(1)
    assert r.resource("ldoa12") == 1800
    assert r.status(slot=15) == ISSUED | COMPLETE
    assert all(r.status(slot=i) == 0 for i in range(1, 15))
    r.irq_level(2, True)
    r.clear()
    r.program("xo.lvl", 3, msg=NO_RESPONSE)
    r.wait_word(1)
    r.start_preserving_wait()
    r.step(1000)
    assert r.status() == ISSUED | COMPLETE and r.resource("xo.lvl") == 3
    assert r.diagnostics() == qom_expected()


def masked_high_bits_do_not_request_low_slot_completion(r):
    r.program("xo.lvl", 3, msg=NO_RESPONSE)
    r.masked_wait(0x778bd800)
    r.start_preserving_wait()
    r.step(999)
    assert r.resource("xo.lvl") == 0
    r.step(1)
    assert r.resource("xo.lvl") == 3
    assert r.status() == ISSUED  # Effective d800 does not mark command 0.
    assert r.dread(TCS_BASE + 4) == 1
    assert r.diagnostics() == qom_expected(1, 2, 0, 0x778bd800)
    r.clear()
    r.program("xo.lvl", 1, msg=WITH_RESPONSE)
    r.start_preserving_wait()
    r.step(1000)
    assert r.status() == ISSUED | COMPLETE  # MSG response remains independently effective.


def busy_wait_rejection_does_not_restart_original_deadline(r):
    r.masked_wait(0x10001)
    r.program("xo.lvl", 3, msg=NO_RESPONSE)
    r.start_preserving_wait()
    r.step(400)
    old = r.diagnostics()
    for raw in (0, 0x778bd800):
        r.rejected_wait(raw)
        assert r.wait_read() == 1 and r.diagnostics() == old
        assert r.dread(r.tcs(0, IDLE)) == 0 and r.status() == ISSUED
    r.step(599)
    r.expect_hz("bi_tcxo", 0)
    assert r.resource("xo.lvl") == 0
    r.step(1)
    r.expect_hz("bi_tcxo", 19_200_000)
    assert r.resource("xo.lvl") == 3 and r.status() == ISSUED | COMPLETE
    assert r.last_clock == 1000 and r.diagnostics() == old
    r.clear()
    r.masked_wait(0x20002)
    assert r.wait_read() == 2 and r.diagnostics() == qom_expected(2, 2, 0, 0x20002)


def pending_irq_is_independent_of_idle_wait_programming(r):
    r.program("xo.lvl", 3)
    r.start_preserving_wait()
    r.step(1000)
    assert r.dread(TCS_BASE + 4) == 1 and r.dread(r.tcs(0, IDLE)) == 1
    r.dwrite(TCS_BASE, 1)
    r.irq_level(2, True)
    r.masked_wait(0x40004)
    assert r.wait_read() == 4 and r.dread(TCS_BASE + 4) == 1
    r.irq_level(2, True)
    r.dwrite(TCS_BASE + 8, 1)
    r.irq_level(2, False)
    assert r.diagnostics() == qom_expected(1, 2, 0, 0x40004)


def cmd_enable_and_invalid_resource_validation_remain_strict(r):
    r.masked_wait(0x10001)
    old = r.diagnostics()
    r.dwrite(r.tcs(0, ENABLE), 1)
    line = f"{TYPE}: DRV2 unsupported write @0x0d1c value=0x00010001"
    before = r.diagnostic_count(line)
    r.dwrite(r.tcs(0, ENABLE), 0x10001)
    r.expect_diagnostic(line, before)
    assert r.dread(r.tcs(0, ENABLE)) == 1 and r.diagnostics() == old
    r.program("xo.lvl", 3, address=0x3dead, msg=NO_RESPONSE)
    r.start_preserving_wait()
    r.step(1000)
    assert r.status() == ISSUED and r.dread(r.tcs(0, IDLE)) == 0
    assert r.resource("xo.lvl") == 0 and r.dread(TCS_BASE + 4) == 0
    assert "no completion" in r.log_path.read_text()
    r.rejected_wait(0x20002)  # Stalled is still busy, not a masking opportunity.
    assert r.diagnostics() == old
    r.clear()
    r.masked_wait(0x20002)
    assert r.diagnostics() == qom_expected(2, 2, 0, 0x20002)


def system_reset_clears_wait_diagnostics_and_pending_command(r):
    r.masked_wait(0x10001, 0, 7)
    r.masked_wait(0x20002, DISPLAY, 1)
    r.masked_wait(0x80008000)
    r.program("xo.lvl", 3, slot=15, msg=NO_RESPONSE)
    r.start_preserving_wait(1 << 15)
    r.step(400)
    r.raw_write(GCC + 4, 0x37)
    assert r.raw_read(GCC + 4) == 0x37
    r.events.clear()
    r.monitor("system_reset")
    r.wait_for(lambda: r.raw_read(GCC + 4) == 0, "independent GCC reset witness")
    r.monitor("query-status")  # Drain asynchronous RESET after the real witness.
    assert any(x.get("event") == "RESET" for x in r.events)
    assert r.qom(POLICY) is True and r.diagnostics() == qom_expected()
    for drv in range(4):
        for tcs in range(2 if drv == DISPLAY else 8):
            assert r.wait_read(drv, tcs) == 0
            assert r.dread(r.tcs(tcs, ENABLE), drv) == 0
            assert r.dread(r.tcs(tcs, IDLE), drv) == 1
        assert r.dread(TCS_BASE + 4, drv) == 0
        r.irq_level(drv, False)
    r.step(10000)
    r.expect_hz("bi_tcxo", 0)
    assert r.resource("xo.lvl") == 0 and r.status(slot=15) == 0


ORDINARY = [
    (default_strict_geometry_and_readonly_diagnostics, None),
    (strict_reserved_wait_writes_are_atomic, False),
    (reference_masks_only_high_bits_and_audits_each_write, True),
    (reference_isolated_across_all_voters_and_boundary_tcs, True),
    (low_wait_bits_control_completion_without_response, True),
    (masked_high_bits_do_not_request_low_slot_completion, True),
    (busy_wait_rejection_does_not_restart_original_deadline, True),
    (pending_irq_is_independent_of_idle_wait_programming, True),
    (cmd_enable_and_invalid_resource_validation_remain_strict, True),
    (system_reset_clears_wait_diagnostics_and_pending_command, True),
]


def paused_tcg_exposes_the_same_pure_readonly_diagnostics(args, out):
    r = WaitRig(args, out / "tcg", policy=True, accel="tcg")
    try:
        assert r.monitor("query-status")["running"] is False
        before = r.diagnostics()
        assert before == qom_expected() and r.qom(POLICY) is True
        assert r.diagnostics() == before
        assert r.monitor("query-status")["running"] is False
        assert r.rpmh_mmio_operations == 0 and r.cont_commands == 0
        return {"normal_tcg": True, "guest_cpu_executed": False,
                "device_mmio_used": False, "qom_observations": before}
    finally:
        r.close()


def literal_main_payload(scenario, xo_input_period):
    """Independent fixed v2 wire oracle for the three deliberately bounded sources.

    All typed resource votes remain zero: the only in-flight command has not
    reached its deadline. No per-resource public aggregate is used to guess a
    hidden individual vote. The complete serialized bytes must match.
    """
    assert scenario in ("cold", "inflight")
    assert xo_input_period == PERIOD_UNIT // 38_400_000
    payload = bytearray(bytes.fromhex(PROFILE["command_db_sha256"]))
    payload.extend(struct.pack(">Q", LATENCY))
    waits = {(0, 7): 1, (2, 0): 0x8000, (3, 1): 2} if scenario == "inflight" else {}
    for drv in range(4):
        payload.extend(struct.pack(">4IB", int(scenario == "inflight" and drv == 2), 0, 0, 0, 0))
        for tcs in range(8):  # Unused display slots 2..7 remain reset on the old wire.
            active = scenario == "inflight" and (drv, tcs) == (2, 0)
            payload.extend(struct.pack(">3I", waits.get((drv, tcs), 0),
                                       0x1010000 if active else 0, 0x8000 if active else 0))
            for slot in range(16):
                values = ((NO_RESPONSE, RESOURCES["xo.lvl"]["address"], 3, ISSUED, 0)
                          if active and slot == 15 else (0, 0, 0, 0, 0))
                payload.extend(struct.pack(">5I", *values))
            payload.extend(struct.pack(">iqBB", 15 if active else -1,
                                       1000 if active else -1, int(active), 0))
    payload.extend(bytes(98 * (4 * 4 * 4 + 4 * 4 + 4)))
    payload.extend(struct.pack(">Q", xo_input_period))
    assert len(payload) == MAIN_BYTES
    return bytes(payload)


def literal_subsection(snapshot):
    d = snapshot["diagnostics"]
    return struct.pack(">IQIII", 1, d["wait-reserved-masked-writes"],
                       d["wait-reserved-last-voter"], d["wait-reserved-last-tcs"],
                       d["wait-reserved-last-raw"])


def unique_main_section(data):
    name = TYPE.encode()
    needle = bytes([len(name)]) + name + struct.pack(">II", 0, 2)
    found, pos = [], 0
    while True:
        offset = data.find(needle, pos)
        if offset < 0:
            break
        header, payload = offset - 5, offset + len(needle)
        if header >= 8 and data[header] in (1, 4):
            found.append((header, payload))
        pos = offset + 1
    assert len(found) == 1, found
    return found[0]


def inspect_actual_stream(path, source_state, scenario):
    data = path.read_bytes()
    header, payload = unique_main_section(data)
    expected = literal_main_payload(scenario, source_state["xo_input_period"])
    actual = data[payload:payload + MAIN_BYTES]
    assert actual == expected, (
        "v2 main payload differs", len(actual),
        [i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]][:32])
    main_file = path.with_name(path.name + ".main-v2.payload.bin")
    main_file.write_bytes(actual)
    main = {
        "name": TYPE, "instance_id": 0, "version": 2,
        "header_offset": header, "payload_offset": payload, "payload_bytes": MAIN_BYTES,
        "section_id": int.from_bytes(data[header + 1:header + 5], "big"),
        "payload_file": main_file.name, "payload_sha256": digest(main_file),
        "complete_payload_verified_against_fixed_source_scenario": True,
        "scenario": scenario,
        "serialized_waits": [
            {"voter": drv, "tcs": tcs,
             "offset": 40 + drv * 2785 + 17 + tcs * 346,
             "value": int.from_bytes(actual[40 + drv * 2785 + 17 + tcs * 346:
                                          44 + drv * 2785 + 17 + tcs * 346], "big")}
            for drv in range(4) for tcs in range(8)
        ],
    }
    name = SUBSECTION.encode()
    sub_header = bytes([5, len(name)]) + name + struct.pack(">I", 1)
    # The old main's final VMSTATE_CLOCK has its default multiplier/divider,
    # hence no nested clock subsection. Confirm the actual boundary, not just
    # an unstructured byte-string occurrence elsewhere in RAM or JSON metadata.
    boundary = payload + MAIN_BYTES
    matches, pos = [], 0
    while True:
        offset = data.find(sub_header, pos)
        if offset < 0:
            break
        matches.append(offset)
        pos = offset + 1
    subsection = None
    if source_state["policy"]:
        assert matches == [boundary], (matches, boundary)
        sub_payload = boundary + len(sub_header)
        raw = data[sub_payload:sub_payload + SUB_BYTES]
        assert raw == literal_subsection(source_state) and len(raw) == SUB_BYTES
        policy, count, voter, tcs, value = struct.unpack(">IQIII", raw)
        sub_file = path.with_name(path.name + ".sub-v1.payload.bin")
        sub_file.write_bytes(raw)
        subsection = {
            "name": SUBSECTION, "version": 1, "header_offset": boundary,
            "payload_offset": sub_payload, "payload_bytes": SUB_BYTES,
            "payload_file": sub_file.name, "payload_sha256": digest(sub_file),
            "payload_hex": raw.hex(),
            "decoded": {"policy": policy, "masked_write_count": count,
                        "last_voter": voter, "last_tcs": tcs, "last_raw": value},
            "all_24_bytes_match_actual_stopped_source_qom": True,
        }
    else:
        assert matches == [], matches
        # A strict source must not emit any WAIT-policy subsection, regardless
        # of version byte. The explicit name must not exist on its device wire.
        assert data[boundary:boundary + 2 + len(name)] != bytes([5, len(name)]) + name
    sections = {"main": main, "subsection": subsection,
                "stream_sha256": digest(path), "stream_bytes": len(data)}
    write_json(path.with_name(path.name + ".sections.json"), sections)
    return sections


def stopped_qom_snapshot(r):
    status = r.monitor("query-status")
    assert status["running"] is False
    first = r.pure_snapshot()
    assert r.pure_snapshot() == first
    assert r.monitor("query-status")["running"] is False
    return status, first


def save_source(r, path, scenario, witness):
    r.raw_write(RAM, witness)
    r.monitor("stop")
    status, before = stopped_qom_snapshot(r)
    write_json(r.directory / "source-qom-before-save.json", {
        "query_status": status, "source_virtual_ns": r.last_clock,
        "qom": before, "repeated_pure_qom_unchanged": True})
    r.monitor("migrate", file_channels(path))
    sent = wait_migration(r)
    assert sent["status"] == "completed", sent
    final_status, after = stopped_qom_snapshot(r)
    assert final_status["status"] == "postmigrate" and after == before
    write_json(r.directory / "source-qom-after-save.json", {
        "query_status": final_status, "source_virtual_ns": r.last_clock, "qom": after})
    write_json(r.directory / "source-query-migrate.json", sent)
    sections = inspect_actual_stream(path, after, scenario)
    record = {
        "file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
        "source_directory": r.directory.name, "source_status": sent["status"],
        "source_virtual_ns": r.last_clock, "source_query_migrate": sent,
        "source_qom_before_file": r.directory.name + "/source-qom-before-save.json",
        "source_qom_after_file": r.directory.name + "/source-qom-after-save.json",
        "source_query_migrate_file": r.directory.name + "/source-query-migrate.json",
        "source_qom": after, "sections": sections, "ram_witness": witness,
        "source_stream_modified": False,
    }
    return record


def prewarm_masked_target(r, now):
    assert r.qom(POLICY) is True and r.monitor("query-status")["running"] is False
    r.masked_wait(0x40000004, 1, 6)
    assert r.diagnostics() == qom_expected(1, 1, 6, 0x40000004)
    r.raw_write(RAM, 0xdeadbeef)
    r.set_clock(now)
    status, old = stopped_qom_snapshot(r)
    write_json(r.directory / "destination-before-load.json", {
        "query_status": status, "destination_virtual_ns": now, "qom": old})
    return old


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
    return {"destination_exit": code, "request_sent": requested,
            "expected_diagnostic": expected, "specific_load_failure_observed": True,
            "command_error": command_error, "target_directory": target.directory.name,
            "stderr_file": target.directory.name + "/qemu.stderr"}


def single_bit_corruption(original, changed, sections, kind):
    before_digest = digest(original)
    data = original.read_bytes()
    subsection = sections["subsection"]
    assert subsection is not None
    relative, old, xor = (0, 1, 1) if kind == "policy" else (12, 3, 4)
    absolute = subsection["payload_offset"] + relative
    assert int.from_bytes(data[absolute:absolute + 4], "big") == old
    result = bytearray(data)
    result[absolute:absolute + 4] = (old ^ xor).to_bytes(4, "big")
    differences = [i for i, (a, b) in enumerate(zip(data, result)) if a != b]
    assert differences == [absolute + 3]
    assert bin(data[differences[0]] ^ result[differences[0]]).count("1") == 1
    assert result[:absolute] == data[:absolute] and result[absolute + 4:] == data[absolute + 4:]
    changed.write_bytes(result)
    assert digest(original) == before_digest and len(result) == len(data)
    return {
        "field": "policy" if kind == "policy" else "last_voter",
        "field_offset_in_subsection": relative, "field_width": 4,
        "absolute_field_offset": absolute, "original_value": old,
        "modified_value": old ^ xor, "xor_mask": xor,
        "changed_byte_offsets": differences, "changed_bits": 1,
        "original_file": original.name, "original_sha256": before_digest,
        "snapshot_file": changed.name, "snapshot_sha256": digest(changed),
        "snapshot_bytes": len(result), "stream_modified": True,
        "original_stream_unchanged": True,
    }


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        r = WaitRig(args, out / name, **kwargs)
        rigs.append(r)
        return r

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    try:
        source = new("mig-source")
        source.masked_wait(0x10001, 0, 7)
        source.masked_wait(0x80008000, 2, 0)
        source.masked_wait(0x20002, DISPLAY, 1)
        assert source.diagnostics() == qom_expected(3, 3, 1, 0x20002)
        source.program("xo.lvl", 3, tcs=0, slot=15, msg=NO_RESPONSE)
        source.dwrite(TCS_BASE, 1)
        source.start_preserving_wait(0x8000)
        source.step(400)
        assert source.last_clock == 400 and source.status(slot=15) == ISSUED
        source.expect_hz("bi_tcxo", 0)
        first_path = out / "rpmh-wait-v2-subv1-masked-inflight.migration"
        first_source = save_source(source, first_path, "inflight", 0x57544d31)
        report["source_streams"].append(first_source)
        assert first_source["source_qom"]["diagnostics"] == qom_expected(3, 3, 1, 0x20002)

        target = new("mig-target", incoming=True)
        old = prewarm_masked_target(target, 400)
        assert old != first_source["source_qom"]
        old_mmio, old_cont = target.rpmh_mmio_operations, target.cont_commands
        target.monitor("migrate-incoming", file_channels(first_path))
        got = wait_migration(target)
        assert got["status"] == "completed", got
        status, restored = stopped_qom_snapshot(target)
        assert restored == first_source["source_qom"]
        assert target.rpmh_mmio_operations == old_mmio and target.cont_commands == old_cont
        assert target.raw_read(RAM) == first_source["ram_witness"]
        assert target.monitor("query-status")["running"] is False
        write_json(target.directory / "destination-before-first-rpmh-mmio.json", {
            "query_status": status, "destination_virtual_ns": 400, "qom": restored,
            "ram_witness": first_source["ram_witness"],
            "rpmh_mmio_since_incoming": False, "qmp_cont_since_incoming": False})
        write_json(target.directory / "destination-query-migrate.json", got)
        migration = {
            "name": "v2_with_wait_reference_v1_inflight",
            "passed": True, "source_status": "completed", "target_status": got["status"],
            "source": first_source["source_query_migrate"], "destination": got,
            "snapshot_file": first_path.name, "snapshot_sha256": digest(first_path),
            "snapshot_bytes": first_path.stat().st_size, "stream_modified": False,
            "source_directory": source.directory.name, "target_directory": target.directory.name,
            "source_virtual_ns": 400, "destination_virtual_ns": 400,
            "absolute_command_deadline_ns": 1000, "remaining_ns_at_transfer": 600,
            "existing_absolute_deadline_clock_alignment_preserved": True,
            "sections": first_source["sections"],
            "snapshot_before": first_source["source_qom"], "snapshot_after": restored,
            "target_first_observation_before_rpmh_mmio": True,
            "source_qom_before_file": first_source["source_qom_before_file"],
            "source_qom_after_file": first_source["source_qom_after_file"],
            "target_qom_before_first_mmio_file": target.directory.name + "/destination-before-first-rpmh-mmio.json",
            "target_query_migrate_file": target.directory.name + "/destination-query-migrate.json",
            "continued_after_restore": False,
        }
        report["migrations"].append(migration)
        passed("real_v2_main_and_v1_subsection_restore_before_first_rpmh_mmio")

        target.monitor("cont")
        target.step(599)
        assert target.resource("xo.lvl") == 0
        target.expect_hz("bi_tcxo", 0)
        assert target.diagnostics() == qom_expected(3, 3, 1, 0x20002)
        before_deadline = target.pure_snapshot()
        target.step(1)
        # This actual Clock and resource observation happens before any target
        # RPMh MMIO after incoming, proving restoration of the virtual timer.
        target.expect_hz("bi_tcxo", 19_200_000)
        assert target.resource("xo.lvl") == 3
        assert target.diagnostics() == qom_expected(3, 3, 1, 0x20002)
        assert target.rpmh_mmio_operations == old_mmio
        target.raw_read(RAM)  # Drain real IRQ notifications without device MMIO.
        assert target.irq[2] is True
        after_deadline = target.pure_snapshot()
        write_json(target.directory / "restored-timer-before-first-rpmh-mmio.json", {
            "before_at_999_ns": before_deadline, "after_at_1000_ns": after_deadline,
            "device_mmio_since_incoming": False, "irq2": target.irq[2],
            "actual_clock_period_before": 0,
            "actual_clock_period_after": PERIOD_UNIT // 19_200_000})
        assert target.wait_read(0, 7) == 1 and target.wait_read(2, 0) == 0x8000
        assert target.wait_read(DISPLAY, 1) == 2
        assert target.status(slot=15) == ISSUED | COMPLETE
        assert target.dread(TCS_BASE + 4) == 1
        target.clear()
        target.masked_wait(0x80000001)
        target.program("xo.lvl", 1, msg=NO_RESPONSE)
        target.start_preserving_wait()
        target.step(1000)
        assert target.status() == ISSUED | COMPLETE and target.resource("xo.lvl") == 1
        assert target.diagnostics() == qom_expected(4, 2, 0, 0x80000001)
        migration["continued_after_restore"] = True
        migration["restored_timer_observations_file"] = target.directory.name + "/restored-timer-before-first-rpmh-mmio.json"
        passed("restored_nonresponse_wait_completes_at_599_plus_1_ns_and_accepts_new_work")

        strict_source = new("strict-source", policy=None)
        strict_source.set_clock(23)
        strict_path = out / "rpmh-wait-v2-strict-source.migration"
        strict = save_source(strict_source, strict_path, "cold", 0x53545230)
        report["source_streams"].append(strict)
        assert strict["source_qom"]["policy"] is False
        assert strict["sections"]["subsection"] is None
        strict_target = new("reject-s-m", incoming=True)
        prewarm_masked_target(strict_target, 23)
        failure = expect_load_failure(strict_target, strict_path,
            f"{TYPE}: wait-reserved migration profile mismatch (configured=1 incoming=0 present=0)")
        report["rejected_migrations"].append({
            "name": "strict_to_prewarmed_masked_requires_present_policy",
            "passed": True, "source_policy": False, "target_policy": True,
            "source_status": strict["source_status"], "source_directory": strict_source.directory.name,
            "snapshot_file": strict_path.name, "snapshot_sha256": digest(strict_path),
            "snapshot_bytes": strict_path.stat().st_size, "stream_modified": False,
            "source_sections": strict["sections"], **failure})
        passed("strict_source_rejected_by_prewarmed_masked_target")

        cold_source = new("cold-source")
        cold_source.set_clock(37)
        cold_path = out / "rpmh-wait-v2-subv1-cold-masked.migration"
        cold = save_source(cold_source, cold_path, "cold", 0x4d41534b)
        report["source_streams"].append(cold)
        assert cold["source_qom"]["diagnostics"] == qom_expected()
        assert cold["sections"]["subsection"]["decoded"] == {
            "policy": 1, "masked_write_count": 0, "last_voter": 0, "last_tcs": 0, "last_raw": 0}
        strict_main = out / strict["sections"]["main"]["payload_file"]
        cold_main = out / cold["sections"]["main"]["payload_file"]
        assert strict_main.read_bytes() == cold_main.read_bytes()
        report["strict_and_cold_masked_main_payload_bytes_identical"] = True
        report["strict_and_cold_masked_main_payload_sha256"] = digest(strict_main)
        cold_target = new("reject-m-s", incoming=True, policy=False)
        cold_target.set_clock(37)
        failure = expect_load_failure(cold_target, cold_path,
            f"{TYPE}: wait-reserved migration profile mismatch (configured=0 incoming=1 present=1)")
        report["rejected_migrations"].append({
            "name": "cold_masked_to_strict_rejects_even_without_prior_masking",
            "passed": True, "source_policy": True, "target_policy": False,
            "source_status": cold["source_status"], "source_directory": cold_source.directory.name,
            "snapshot_file": cold_path.name, "snapshot_sha256": digest(cold_path),
            "snapshot_bytes": cold_path.stat().st_size, "stream_modified": False,
            "source_sections": cold["sections"], "source_masked_write_count": 0, **failure})
        passed("cold_masked_source_emits_policy_and_is_rejected_by_strict_target")

        for kind, suffix, expected in (
                ("policy", "policy-bit", f"{TYPE}: invalid wait-reserved subsection policy 0"),
                ("diag", "last-voter-bit", f"{TYPE}: invalid wait-reserved migration diagnostics")):
            changed = out / f"rpmh-wait-v2-subv1-{suffix}.migration"
            mutation = single_bit_corruption(first_path, changed, first_source["sections"], kind)
            bad_target = new("reject-" + kind, incoming=True)
            bad_target.set_clock(400)
            failure = expect_load_failure(bad_target, changed, expected)
            record = {
                "name": "single_bit_" + ("policy" if kind == "policy" else "last_voter") + "_corruption",
                "passed": True, "source_status": first_source["source_status"],
                "source_directory": source.directory.name, "original_sections": first_source["sections"],
                **mutation, **failure,
            }
            report["corrupted_stream_rejections"].append(record)
            passed(record["name"], original_stream_unchanged=True)
        assert digest(first_path) == first_source["sha256"]
    finally:
        for r in reversed(rigs):
            r.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--machine", default=DEFAULT_MACHINE)
    parser.add_argument("--base", type=lambda x: int(x, 0), default=BASE)
    parser.add_argument("--cmd-db-base", type=lambda x: int(x, 0), default=0x80860000)
    parser.add_argument("--qom-path", default=DEVICE)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    assert args.base == BASE and args.qom_path == DEVICE, "this gate pins the actual board geometry"
    out = args.out or Path(tempfile.mkdtemp(prefix="rpmh-wait-"))
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / "result.json").exists(), "refusing to overwrite a receipt"
    report = {
        "schema": "tb371fc-rpmh-wait-mask-v1", "suite": "RPMh WAIT explicit reserved-mask reference",
        "base_source_commit": "3b31e83dc412962826ada872e361a1d32ea4e463",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": digest(Path(args.qemu)),
        "test_sha256": digest(Path(__file__)),
        "expected_checks": EXPECTED_CHECKS, "expected_completed_migrations": 1,
        "expected_rejected_migrations": 2, "expected_corrupted_stream_rejections": 2,
        "vmstate_version": 2, "vmstate_minimum_version": 2,
        "subsection_version": 1, "subsection_payload_bytes": SUB_BYTES,
        "main_payload_bytes_with_default_clock": MAIN_BYTES,
        "commands_per_tcs": 16, "voter_count": 4, "apps_tcs_per_drv": 8,
        "display_tcs_count": 2, "wait_command_mask": MASK,
        "default_strict_main_vmstate_unchanged": True,
        "old_rpmh_tests_changed": False, "old_rpmh_test_blobs_match": False,
        "silicon_reserved_bit_semantics_proved": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "guest_image_patched": False, "android_boot_passed": False,
        "diagnostic_counters_are_model_observations": True,
        "reference_policy_auto_enabled": False,
        "reference_command_db_sha256": PROFILE["command_db_sha256"],
        "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "source_streams": [],
        "success": False,
    }
    started = time.monotonic()
    try:
        report["old_rpmh_test_identities"] = old_test_identities()
        report["old_rpmh_test_blobs_match"] = True
        for index, (test, policy) in enumerate(ORDINARY):
            rig = None
            try:
                rig = WaitRig(args, out / f"{index:02d}", policy=policy)
                test(rig)
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": f"{index:02d}", "policy": bool(policy)})
            finally:
                if rig:
                    rig.close()
        details = paused_tcg_exposes_the_same_pure_readonly_diagnostics(args, out)
        report["checks"].append({
            "name": "paused_tcg_exposes_the_same_pure_readonly_diagnostics",
            "passed": True, **details})
        migration_checks(args, out, report)
        assert len(report["checks"]) == EXPECTED_CHECKS
        assert len({x["name"] for x in report["checks"]}) == EXPECTED_CHECKS
        assert all(x["passed"] is True for x in report["checks"])
        assert len(report["migrations"]) == 1
        assert all(x["source_status"] == x["target_status"] == "completed" and
                   x["continued_after_restore"] for x in report["migrations"])
        assert len(report["rejected_migrations"]) == 2
        assert len(report["corrupted_stream_rejections"]) == 2
        assert len(report["source_streams"]) == 3
        report["success"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_before"] == report["qemu_sha256_after"]
        report["success"] = report["success"] and report["binary_unchanged"]
        report["duration_seconds"] = round(time.monotonic() - started, 6)
        write_json(out / "result.json", report)
        print(json.dumps(report, indent=2))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
