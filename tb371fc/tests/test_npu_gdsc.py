#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled NPU GDSC transactions, unconnected parent inputs and real migrations.

The one four-byte word and the NPU_CFG_AHB retention request fields are bounded
by the pinned vendor source and original DT. This gate does not execute guest
instructions, NPU inference, DMA or firmware. Unknown power/clock inputs are
never replaced by invented availability or a frequency.
"""
import argparse
import copy
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import traceback

from test_camera_gdsc import (
    CameraRig, GLOBAL_FIELDS, DOMAIN_FIELDS, camera_payload, core_diagnostics,
    literal_rpmh_payload, unique_section, provider_snapshot, write_json,
    GCC_VMSTATE_BYTES, DT_SHA256, DRIVER_SHA256,
)
from test_mdss_gdsc import mdss_payload
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels
from test_rpmh_sm8250 import PROFILE, ISSUED, COMPLETE

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-npu-gdsc", "/machine/npu-gdsc"
CAMERA = "/machine/camera-gdsc"
ADDRESS, GCC, RAM = 0x09981004, 0x100000, 0x80010000
NPU_CFG_AHB, CAMERA_AHB, DISP_AHB = GCC + 0x4d004, GCC + 0xb008, GCC + 0xb00c
MDSS, MDSS_ADDRESS = "/machine/mdss-gdsc", 0x0af03000
NPUCC, NPUCC_BASE = "/machine/npucc", 0x09980000
PARENT_RESOURCE = 0xffffffff
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
COLLAPSE, HW_MODE, RETAIN, WAIT, POWER = 1, 2, 1 << 11, 15 << 12, 1 << 31
WRITE_MASK, IDENTITY, VMSTATE_BYTES = 0xf803, 0x4e504731, 64
REGION = TYPE + ".npu-core-control"
RETENTION_MASK = 0x6003
EXPECTED_CHECKS = 21
DEFAULT_MACHINE = (
    "tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
    "rpmh-profile=off,rpmh-boot-configured=off"
)


def oracle(control=COLLAPSE, parent_known=False, parent_on=False,
           hw_known=False, hw_request=False, reference=True):
    """Enumerate uncertain supply values; no hardware mode is DT-declared."""
    assert control & ~WRITE_MASK == 0 and not control & HW_MODE
    parent_values = {bool(parent_on)} if parent_known else {False, True}
    request = not bool(control & COLLAPSE)
    possible = {parent and request for parent in parent_values}
    return {
        "enabled-reference": bool(reference),
        "globals": dict(zip(GLOBAL_FIELDS, (
            int(bool(reference)), IDENTITY, 1, 4, 4, PARENT_RESOURCE, 1, WRITE_MASK, POWER,
        ))),
        "parent-known": bool(parent_known), "parent-on": bool(parent_on),
        "domains": [dict(zip(DOMAIN_FIELDS, (
            "npu-core", ADDRESS, 1, 100, control, True, request,
            bool(reference) and len(possible) == 1,
            bool(reference) and possible == {True}, bool(hw_known), bool(hw_request),
        )))],
    }


class NpuGdscRig(CameraRig):
    """Reuse only the existing transport and real RPMh command helpers.

    This constructor and the NPU accessors deliberately do not mutate the
    camera module's globals, constructor, oracle or acceptance contract.
    """

    def __init__(self, args, directory, *, reference=True, incoming=False,
                 rpmh=False, group="power", accel="qtest", camera=True,
                 mdss=True, npucc=False, legacy_gpu=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.group, self.accel = group, accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.npu_mmio_operations = self.camera_mmio_operations = 0
        self.mdss_mmio_operations = self.npucc_mmio_operations = 0
        self.connections = []
        self.events, self.irq, self.seen_irq = [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="npugdsc-"))
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory / "qtest.jsonl").open("w")
        self.qmplog = (self.directory / "qmp.jsonl").open("w")
        fields = [field for field in args.machine.split(",")
                  if not field.startswith(("rpmh-profile=", "rpmh-boot-configured=",
                                           "rpmh-cmd-db="))]
        fields += [f"rpmh-profile={'reference' if rpmh else 'off'}",
                   "rpmh-boot-configured=off"]
        command = [args.qemu, "-machine", ",".join(fields), "-accel", accel,
                   "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
                   "-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
                   "-qtest-log", "/dev/null",
                   "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off",
                   "-global", f"qcom-camera-gdsc.enabled-reference={'on' if camera else 'off'}",
                   "-global", f"qcom-mdss-gdsc.enabled-reference={'on' if mdss else 'off'}",
                   "-global", f"qcom-sm8250-npucc.enabled-reference={'on' if npucc else 'off'}"]
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
                self.command(f"irq_intercept_out {DEVICE} {group}")
            if not incoming and accel == "qtest":
                self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, command):
        fields = command.split()
        if fields and fields[0] in ("readb", "readw", "readl", "readq",
                                    "writeb", "writew", "writel", "writeq"):
            address = int(fields[1], 0)
            if ADDRESS <= address < ADDRESS + 4:
                self.npu_mmio_operations += 1
                self.device_mmio_operations += 1
            elif MDSS_ADDRESS <= address < MDSS_ADDRESS + 4:
                self.mdss_mmio_operations += 1
                self.device_mmio_operations += 1
            elif NPUCC_BASE <= address < NPUCC_BASE + 0x2000:
                self.npucc_mmio_operations += 1
                self.device_mmio_operations += 1
        # The inherited transport also accounts for GCC, camera and RPMh.
        return super().command(command)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def snapshot_at(self, path, count):
        return {
            "enabled-reference": self.qom("enabled-reference", path),
            "globals": {name: self.qom(name, path) for name in GLOBAL_FIELDS},
            "parent-known": self.qom("parent-known", path),
            "parent-on": self.qom("parent-on", path),
            "domains": [{name: self.qom(f"domain{i}.{name}", path)
                         for name in DOMAIN_FIELDS} for i in range(count)],
        }

    def pure_snapshot(self):
        return self.snapshot_at(DEVICE, 1)

    def camera_snapshot(self):
        return self.snapshot_at(CAMERA, 6)

    def mdss_snapshot(self):
        return self.snapshot_at(MDSS, 1)

    def parent(self, known, on):
        # This explicit test fixture is permitted even when RPMh exists:
        # the board has no supplier connection to these NPU inputs.
        self.input("parent-on", on)
        self.input("parent-known", known)

    def input(self, name, level, domain=0):
        assert domain == 0
        assert name in ("parent-known", "parent-on", "hw-request-known", "hw-request")
        self.command(f"set_irq_in {DEVICE} {name} 0 {int(level)}")

    def hardware(self, known, requested):
        self.input("hw-request", requested)
        self.input("hw-request-known", known)

    def write_control(self, value):
        self.raw_write(ADDRESS, value)

    def read_control(self):
        return self.raw_read(ADDRESS)

    def cfg_period(self, parent=False):
        name = "gcc_npu_cfg_ahb_clk" + ("_parent" if parent else "")
        return self.qom("qtest-clock-period", "/machine/gcc/" + name)

    def expect_line(self, expected, require_seen=False):
        assert self.group is not None
        self.raw_read(RAM)
        if require_seen:
            assert 0 in self.seen_irq, (self.group, self.seen_irq)
        assert self.irq.get(0, False) is bool(expected), (self.group, self.irq, expected)

    def reject(self, reason, *, write=False, value=0):
        text = (f"{TYPE}: rejected {'write' if write else 'read'} domain=0 "
                f"offset=0x0 size=4 value=0x{value:x} reason={reason}")
        before = self.diagnostic_count(text)
        if write:
            self.write_control(value)
        else:
            self.read_control()
        assert self.diagnostic_count(text) == before + 1, text


def check_state(rig, expected, feedback=True):
    actual = rig.pure_snapshot()
    assert actual == expected, (actual, expected)
    if feedback:
        domain = expected["domains"][0]
        if not expected["enabled-reference"]:
            rig.reject("reference-disabled")
        elif not domain["state-known"]:
            rig.reject("power-feedback-unknown")
        else:
            assert rig.read_control() == domain["control"] | (POWER if domain["powered"] else 0)
    return actual


def cold_identity_one_word_and_readonly_properties(r):
    expected = oracle()
    check_state(r, expected)
    assert r.pure_snapshot() == expected and r.last_clock == 0
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory / "mtree.txt").write_text(tree)
    found = {(int(first, 16), int(last, 16)) for first, last in re.findall(
        r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: " + re.escape(REGION) + r"(?:\s|$)", tree)}
    assert found == {(ADDRESS, ADDRESS + 3)}, (found, tree)
    properties = {**expected["globals"], "parent-known": False, "parent-on": False}
    properties.update({"domain0." + key: value for key, value in expected["domains"][0].items()})
    for name, value in properties.items():
        reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in reply and "not writable" in reply["error"]["desc"], reply
    reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": "enabled-reference", "value": False})
    assert "error" in reply and "realized" in reply["error"]["desc"], reply
    check_state(r, expected)
    return {"readonly_properties_checked": len(properties), "mapped_bytes": 4}


def literal_native_probe_is_readable_collapsed_without_supplier(r):
    # gdsc_probe reads GDSCR then clears HW_CONTROL and SW_OVERRIDE. No parent
    # provider is invented to make this initial read succeed.
    assert r.read_control() == COLLAPSE
    r.write_control(r.read_control() & ~(HW_MODE | 4))
    check_state(r, oracle())
    control = 0x5000 | RETAIN | COLLAPSE
    r.write_control(control)
    r.write_control(r.read_control() & ~(HW_MODE | 4))
    check_state(r, oracle(control))
    r.write_control(control & ~COLLAPSE)
    check_state(r, oracle(control & ~COLLAPSE))
    assert r.qom("domain0.requested") is True
    assert r.qom("domain0.state-known") is False
    r.write_control(control)
    check_state(r, oracle(control))
    assert r.last_clock == 0
    return {"native_probe_sequence_replayed_by_qtest": True,
            "native_kernel_executed": False, "parent_supply_assumed": False}


def software_three_valued_parent_truth_and_readonly_power_rmw(r):
    for known, on, collapse, carried in itertools.product((False, True), repeat=4):
        r.parent(known, on)
        value = 0x3000 | RETAIN | int(collapse)
        r.write_control(value | (POWER if carried else 0))
        check_state(r, oracle(value, known, on))
    assert r.last_clock == 0


def undeclared_hardware_mode_rejects_atomically_for_every_input(r):
    for pk, po, hk, request, collapse in itertools.product((False, True), repeat=5):
        r.parent(pk, po)
        r.hardware(hk, request)
        control = RETAIN | int(collapse)
        r.write_control(control)
        expected = oracle(control, pk, po, hk, request)
        check_state(r, expected)
        r.reject("hw-mode-not-in-reference", write=True, value=control | HW_MODE)
        assert r.pure_snapshot() == expected
        r.reject("hw-mode-not-in-reference", write=True, value=control | HW_MODE | POWER)
        assert r.pure_snapshot() == expected
    return {"hardware_mode_declared": False, "input_combinations": 32}


def reserved_bits_reject_atomically_without_changing_sibling_domains(r):
    r.parent(True, True)
    r.hardware(True, True)
    value = RETAIN | 0xa000
    r.write_control(value)
    before, camera, mdss = r.pure_snapshot(), r.camera_snapshot(), r.mdss_snapshot()
    for bit in (2, 3, 10, 16, 30):
        r.reject("unsupported-bits", write=True, value=value | (1 << bit))
        assert r.pure_snapshot() == before
        assert r.camera_snapshot() == camera and r.mdss_snapshot() == mdss
    r.write_control(value | POWER)
    check_state(r, oracle(value, True, True, True, True))


def unknown_feedback_never_becomes_known_through_reads_time_or_hw_gpio(r):
    r.parent(False, True)
    r.hardware(False, True)
    r.write_control(RETAIN | WAIT)
    expected = oracle(RETAIN | WAIT, False, True, False, True)
    for _ in range(3):
        check_state(r, expected)
    r.step(1_000_000)
    check_state(r, expected)
    r.hardware(True, True)
    check_state(r, oracle(RETAIN | WAIT, False, True, True, True))
    r.hardware(True, False)
    check_state(r, oracle(RETAIN | WAIT, False, True, True, False))
    r.parent(True, True)
    check_state(r, oracle(RETAIN | WAIT, True, True, True, False))


def access_shapes_and_unowned_controller_holes_are_exact(r):
    value = RETAIN | 0x5000 | COLLAPSE
    r.write_control(value)
    before = r.pure_snapshot()
    for operation in ("read", "write"):
        for width, size in (("b", 1), ("w", 2)):
            previous = core_diagnostics(r.log_path.read_text(errors="replace"))
            if operation == "read":
                r.read_absolute(ADDRESS, width)
            else:
                r.write_absolute(ADDRESS, 0, width)
            assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == [
                (operation, 0, size, REGION, "invalid size (min:4 max:4)")]
        for displacement in (1, 2):
            previous = core_diagnostics(r.log_path.read_text(errors="replace"))
            if operation == "read":
                r.read_absolute(ADDRESS + displacement)
            else:
                r.write_absolute(ADDRESS + displacement, 0x03020100)
            chunks = ((1, 1), (2, 2), (4, 1)) if displacement == 1 else ((2, 2), (4, 2))
            expected = [(operation, ADDRESS + offset, size, "(null)", "rejected")
                        if offset == 4 else
                        (operation, offset, size, REGION, "invalid size (min:4 max:4)")
                        for offset, size in chunks]
            assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == expected
    assert r.pure_snapshot() == before
    holes = (ADDRESS + 4, ADDRESS + 8, NPUCC_BASE + 0x1040,
             NPUCC_BASE + 0x11a8, NPUCC_BASE + 0x1500,
             MDSS_ADDRESS + 4,
             0x0abf0bf4, 0x0abf0bfc, 0x0abf0c94, 0x0abf0c9c,
             0x0abf0d14, 0x0abf0d1c, 0x0abf0d94, 0x0abf0d9c)
    previous = core_diagnostics(r.log_path.read_text(errors="replace"))
    expected = []
    for address in holes:
        r.raw_read(address)
        r.raw_write(address, 0xffffffff)
        expected.extend((op, address, 4, "(null)", "rejected") for op in ("read", "write"))
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == expected
    # The new VIDEO owner is independent and remains disabled in this fixture.
    for domain, address in enumerate((0x0abf0bf8, 0x0abf0c98,
                                      0x0abf0d18, 0x0abf0d98)):
        for operation in ("read", "write"):
            diagnostic = ("qcom-video-gdsc: rejected " + operation +
                          f" domain={domain} offset=0x0 size=4 value=0x" +
                          ("0" if operation == "read" else "ffffffff") +
                          " reason=reference-disabled")
            count = r.diagnostic_count(diagnostic)
            if operation == "read":
                r.raw_read(address)
            else:
                r.raw_write(address, 0xffffffff)
            assert r.diagnostic_count(diagnostic) == count + 1
    assert r.pure_snapshot() == before
    # A spanning bus write commits its supported low word, then rejects the
    # adjacent hole; this is not falsely described as an atomic 64-bit write.
    previous = core_diagnostics(r.log_path.read_text(errors="replace"))
    r.write_absolute(ADDRESS, (0x12345678 << 32) | 0x2001, "q")
    assert r.read_control() == 0x2001
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == [
        ("write", ADDRESS + 4, 4, "(null)", "rejected")]


def adjacent_npucc_words_keep_their_own_programming_and_geometry(r):
    assert r.qom("enabled-reference", NPUCC) is True
    r.write_control(0x5801)
    before = r.pure_snapshot()
    for value in (1, 0):
        r.raw_write(NPUCC_BASE + 0x1000, value)
        assert r.raw_read(NPUCC_BASE + 0x1000) == value
        assert r.pure_snapshot() == before
    # Exercise the independent RCG config without an UPDATE or a clock-source
    # assumption. Its real board XO input is separate from NPU power knowledge.
    for value in (0x101, 0):
        r.raw_write(NPUCC_BASE + 0x1014, value)
        assert r.raw_read(NPUCC_BASE + 0x1014) == value
        r.raw_write(NPUCC_BASE + 0x1010, 0)
        assert r.raw_read(NPUCC_BASE + 0x1010) == POWER
        assert r.pure_snapshot() == before
    r.raw_write(NPUCC_BASE + 0x1000, 1)
    r.write_control(0x2801)
    assert r.raw_read(NPUCC_BASE + 0x1000) == 1
    assert r.raw_read(NPUCC_BASE + 0x1010) == POWER
    assert r.raw_read(NPUCC_BASE + 0x1014) == 0
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory / "mtree.txt").write_text(tree)
    windows = re.findall(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (npucc-[^\s]*)", tree)
    ranges = [(int(a, 16), int(b, 16)) for a, b, _ in windows]
    assert any(a <= NPUCC_BASE + 0x1000 <= b for a, b in ranges), ranges
    assert any(a <= NPUCC_BASE + 0x1010 <= b for a, b in ranges), ranges
    assert all(not a <= ADDRESS <= b for a, b in ranges), ranges
    return {"npucc_bcr_and_rcg_observed": True, "npucc_gdsc_overlap": False}


def reset_preserves_external_inputs_and_collapses_only_control(r):
    r.parent(True, True)
    r.hardware(True, True)
    r.write_control(RETAIN)
    r.expect_line(True, True)
    r.raw_write(NPU_CFG_AHB, RETENTION_MASK)
    r.raw_write(GCC + 4, 0x35)
    assert r.raw_read(GCC + 4) == 0x35
    r.events.clear()
    r.monitor("system_reset")
    r.wait_for(lambda: r.raw_read(GCC + 4) == 0, "GCC reset witness")
    r.monitor("query-status")
    assert any(event.get("event") == "RESET" for event in r.events)
    check_state(r, oracle(COLLAPSE, True, True, True, True))
    r.expect_line(False)
    assert r.raw_read(NPU_CFG_AHB) == POWER
    assert r.cfg_period() == r.cfg_period(True) == 0
    r.write_control(0)
    check_state(r, oracle(0, True, True, True, True))
    r.expect_line(True, True)


def power_and_knowledge_outputs_are_distinct_real_gpio_lines(r):
    r.parent(True, True)
    r.write_control(0)
    r.expect_line(True, True)
    r.parent(False, True)
    r.expect_line(False)
    r.write_control(COLLAPSE)
    r.expect_line(False)
    other = NpuGdscRig(r.args, r.directory / "validity", group="state-known")
    try:
        other.parent(True, True)
        other.write_control(0)
        other.expect_line(True, True)
        other.parent(False, True)
        other.expect_line(False)
        other.write_control(COLLAPSE)
        other.expect_line(True, True)
    finally:
        other.close()


def assert_no_npu_supplier(r, expected, mmcx_powered):
    assert r.pure_snapshot() == expected
    assert r.qom("parent-known", CAMERA) is True
    assert r.qom("parent-on", CAMERA) is mmcx_powered
    assert r.qom("parent-known", MDSS) is True
    assert r.qom("parent-on", MDSS) is mmcx_powered
    assert r.qom("domain0.powered", CAMERA) is mmcx_powered
    assert r.qom("domain0.powered", MDSS) is mmcx_powered


def real_rpmh_votes_never_supply_the_unconnected_npu_parent(r):
    assert r.has_rpmh and r.resource("mmcx.lvl", "present") == 1
    # No fixture writes to NPU parent inputs occur in this entire test.
    r.write_control(0)
    r.raw_write(0x0ad07004, 0)
    r.raw_write(MDSS_ADDRESS, 0)
    expected = oracle(0)
    assert_no_npu_supplier(r, expected, False)
    for resource in ("cx.lvl", "gfx.lvl", "mx.lvl"):
        r.run_one(resource, 1)
        assert r.resource(resource) == 1 and r.resource("mmcx.lvl") == 0
        assert_no_npu_supplier(r, expected, False)
    r.clear()
    r.program("mmcx.lvl", 1)
    r.trigger()
    r.step(999)
    assert_no_npu_supplier(r, expected, False)
    r.step(1)
    assert_no_npu_supplier(r, expected, True)
    r.expect_line(False)
    assert r.status() == ISSUED | COMPLETE
    r.run_one("mmcx.lvl", 2, drv=0)
    r.run_one("mmcx.lvl", 0, drv=2)
    assert r.resource("mmcx.lvl") == 2
    assert_no_npu_supplier(r, expected, True)
    r.run_one("mmcx.lvl", 0, drv=0)
    assert_no_npu_supplier(r, expected, False)
    r.reject("power-feedback-unknown")
    r.write_control(COLLAPSE)
    check_state(r, oracle())
    return {"parent_output_intercepted": False, "npu_parent_fixture_used": False,
            "shared_camera_and_mdss_provider_observed": True}


def npu_cfg_ahb_retention_preserves_hwcg_status_and_unknown_frequency(r):
    before = r.pure_snapshot()
    for low in (0, 1, 2, 3):
        r.raw_write(NPU_CFG_AHB, low)
        for bit in (1 << 13, 1 << 14):
            value = r.raw_read(NPU_CFG_AHB) | bit
            r.raw_write(NPU_CFG_AHB, value)
            assert r.raw_read(NPU_CFG_AHB) == value
        for bit in (1 << 13, 1 << 14):
            value = r.raw_read(NPU_CFG_AHB) & ~bit
            r.raw_write(NPU_CFG_AHB, value)
            assert r.raw_read(NPU_CFG_AHB) == value
        assert r.raw_read(NPU_CFG_AHB) == POWER | low
    for retained, low in itertools.product((0, 0x2000, 0x4000, 0x6000), range(4)):
        value = retained | low
        r.raw_write(NPU_CFG_AHB, value)
        r.step(5000)
        assert r.raw_read(NPU_CFG_AHB) == POWER | value
        assert r.cfg_period() == r.cfg_period(True) == 0
        assert r.pure_snapshot() == before
    for address, bad in ((NPU_CFG_AHB, 0x7003), (NPU_CFG_AHB, 0x8003),
                         (GCC + 0xb004, 0x7001), (DISP_AHB, 0x6003),
                         (CAMERA_AHB, 0x6003)):
        text = f"qcom-sm8250-gcc: unsupported write 0x{bad:08x} at 0x{address-GCC:x}"
        previous, old = r.diagnostic_count(text), r.raw_read(address)
        r.raw_write(address, bad)
        assert r.diagnostic_count(text) == previous + 1
        assert r.raw_read(address) == old
    r.raw_write(CAMERA_AHB, 0x6001)
    r.raw_write(DISP_AHB, 0x6001)
    assert r.raw_read(CAMERA_AHB) == r.raw_read(DISP_AHB) == 0x6001
    assert r.raw_read(NPU_CFG_AHB) == POWER | RETENTION_MASK
    return {"qemu_descriptor_id": 42, "external_parent_index": 21,
            "readonly_cbcr_off": POWER, "invented_frequency": False}


ORDINARY = [
    (cold_identity_one_word_and_readonly_properties, {}),
    (literal_native_probe_is_readable_collapsed_without_supplier, {}),
    (software_three_valued_parent_truth_and_readonly_power_rmw, {}),
    (undeclared_hardware_mode_rejects_atomically_for_every_input, {}),
    (reserved_bits_reject_atomically_without_changing_sibling_domains, {}),
    (unknown_feedback_never_becomes_known_through_reads_time_or_hw_gpio, {}),
    (access_shapes_and_unowned_controller_holes_are_exact, {}),
    (adjacent_npucc_words_keep_their_own_programming_and_geometry, {"npucc": True}),
    (reset_preserves_external_inputs_and_collapses_only_control, {}),
    (power_and_knowledge_outputs_are_distinct_real_gpio_lines, {}),
    (real_rpmh_votes_never_supply_the_unconnected_npu_parent, {"rpmh": True}),
    (npu_cfg_ahb_retention_preserves_hwcg_status_and_unknown_frequency, {}),
]


def explicit_npu_profile_is_independent_of_camera_mdss_npucc_and_gpu(args, out):
    for legacy in (False, True):
        rig = NpuGdscRig(args, out / ("legacy" if legacy else "default"),
                         reference=None, legacy_gpu=legacy, npucc=True)
        try:
            check_state(rig, oracle(reference=False))
            rig.reject("reference-disabled", write=True)
            rig.parent(True, True)
            rig.hardware(True, True)
            check_state(rig, oracle(parent_known=True, parent_on=True,
                                   hw_known=True, hw_request=True, reference=False))
            assert rig.qom("enabled-reference", CAMERA) is True
            assert rig.qom("enabled-reference", MDSS) is True
            assert rig.qom("enabled-reference", NPUCC) is True
            assert rig.qom("enabled-reference", "/machine/gpu-gx-gdsc") is legacy
        finally:
            rig.close()
    rig = NpuGdscRig(args, out / "npu-only", camera=False, mdss=False)
    try:
        check_state(rig, oracle())
        for path in (CAMERA, MDSS, NPUCC):
            assert rig.qom("enabled-reference", path) is False
    finally:
        rig.close()


def paused_tcg_pure_getters_do_not_execute_guest_or_repair_state(args, out):
    rig = NpuGdscRig(args, out / "tcg", accel="tcg", group=None)
    try:
        assert rig.monitor("query-status")["running"] is False
        assert rig.pure_snapshot() == rig.pure_snapshot() == oracle()
        properties = rig.monitor("qom-list", {"path": DEVICE})
        assert not any(prop["name"].startswith("qtest-") for prop in properties)
        assert rig.monitor("query-status")["running"] is False
        assert rig.cont_commands == rig.device_mmio_operations == 0
    finally:
        rig.close()


def npu_payload(snapshot):
    result = struct.pack(">9I", *(snapshot["globals"][name] for name in GLOBAL_FIELDS))
    result += struct.pack(">BB", snapshot["parent-known"], snapshot["parent-on"])
    domain = snapshot["domains"][0]
    result += struct.pack(">IQIIIBB", 0, domain["address"], domain["dt-flags"],
                          domain["timeout-us"], domain["control"],
                          domain["hw-request-known"], domain["hw-request"])
    assert len(result) == VMSTATE_BYTES
    return result


def combined_snapshot(rig):
    return {"npu": rig.pure_snapshot(), "camera": rig.camera_snapshot(),
            "mdss": rig.mdss_snapshot(),
            "provider": provider_snapshot(rig) if rig.has_rpmh else None,
            "cfg_parent_period": rig.cfg_period(True),
            "cfg_output_period": rig.cfg_period()}


def inspect_stream(path, snapshot, scenario, gcc_value):
    raw = path.read_bytes()
    sections = {}
    for key, name, size, payload in (
            ("npu", TYPE, VMSTATE_BYTES, npu_payload(snapshot["npu"])),
            ("camera", "qcom-camera-gdsc", 194, camera_payload(snapshot["camera"])),
            ("mdss", "qcom-mdss-gdsc", 64, mdss_payload(snapshot["mdss"]))):
        section = unique_section(raw, name, 1, size)
        actual = raw[section["payload_offset"]:section["payload_offset"] + size]
        assert actual == payload, (key, actual.hex(), payload.hex())
        payload_file = path.with_name(path.name + f".{key}-v1.payload.bin")
        payload_file.write_bytes(actual)
        section.update(payload_file=payload_file.name, payload_sha256=digest(payload_file),
                       payload_hex=actual.hex(), all_bytes_match_stopped_source_qom=True)
        sections[key] = section
    if snapshot["provider"] is not None:
        section = unique_section(raw, "qcom-sm8250-rpmh", 3, 19424)
        actual = raw[section["payload_offset"]:section["payload_offset"] + 19424]
        expected = literal_rpmh_payload(scenario, snapshot["provider"]["xo_input_period"])
        assert actual == expected, ("RPMh bounded scenario", scenario)
        section.update(all_bytes_match_literal_source_scenario=True,
                       payload_sha256=hashlib.sha256(actual).hexdigest())
        sections["rpmh"] = section
    section = unique_section(raw, "qcom-sm8250-gcc", 3, GCC_VMSTATE_BYTES)
    regs = {hex(offset): struct.unpack_from(">I", raw, section["payload_offset"] + offset)[0]
            for offset in (0xb004, 0xb008, 0xb00c, 0x4d004)}
    assert regs == {"0xb004": 0, "0xb008": 0, "0xb00c": 0, "0x4d004": gcc_value}, regs
    section.update(selected_registers=regs, complete_gcc_runtime_oracle_claimed=False)
    sections["gcc"] = section
    write_json(path.with_name(path.name + ".sections.json"), sections)
    return sections


def save_source(rig, path, expected, scenario, gcc_value):
    assert rig.raw_read(NPU_CFG_AHB) == POWER | gcc_value
    assert rig.cfg_period() == rig.cfg_period(True) == 0
    rig.raw_write(RAM, 0x4e504731)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    before = combined_snapshot(rig)
    assert before["npu"] == expected
    assert combined_snapshot(rig) == before
    write_json(rig.directory / "source-before-save.json", before)
    rig.monitor("migrate", file_channels(path))
    sent = wait_migration(rig)
    assert sent["status"] == "completed"
    assert rig.monitor("query-status")["status"] == "postmigrate"
    assert combined_snapshot(rig) == before
    write_json(rig.directory / "source-after-save.json", before)
    write_json(rig.directory / "source-query-migrate.json", sent)
    sections = inspect_stream(path, before, scenario, gcc_value)
    return {"file": path.name, "sha256": digest(path), "bytes": path.stat().st_size,
            "source_directory": rig.directory.name, "source_virtual_ns": rig.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": before, "sections": sections, "stream_modified": False}


def receive_source(rig, path, source):
    mmio_before, cont_before = rig.device_mmio_operations, rig.cont_commands
    rig.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(rig)
    assert received["status"] == "completed"
    assert rig.monitor("query-status")["running"] is False
    first = combined_snapshot(rig)
    assert first == source["snapshot"], (first, source["snapshot"])
    assert rig.device_mmio_operations == mmio_before and rig.cont_commands == cont_before
    assert rig.raw_read(RAM) == 0x4e504731
    domain = first["npu"]["domains"][0]
    rig.expect_line(domain["powered" if rig.group == "power" else "state-known"])
    evidence = {"snapshot": first, "device_mmio_since_incoming": False,
                "qmp_cont_since_incoming": False, "guest_cpu_executed": False,
                "ram_witness": 0x4e504731, "query_migrate": received}
    write_json(rig.directory / "destination-before-first-device-mmio.json", evidence)
    return evidence


def load_failure(rig, path, diagnostic):
    error = None
    try:
        rig.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as exc:
        error = str(exc)
    code = rig.process.wait(timeout=20)
    stderr = rig.log_path.read_text(errors="replace")
    assert code == 1, (code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr
    return {"destination_exit": code, "expected_diagnostic": diagnostic,
            "specific_load_failure_observed": True, "command_error": error,
            "target_directory": rig.directory.name}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = NpuGdscRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def record(name, source, target, path, saved, first):
        return {"name": name, "passed": True, "source_status": "completed",
                "target_status": "completed", "source_directory": source.directory.name,
                "target_directory": target.directory.name, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": False,
                "source_snapshot": saved["snapshot"], "first_target_observation": first,
                "continued_after_restore": False}

    try:
        unknown = new("unknown-source", rpmh=True, group="state-known")
        unknown.run_one("mmcx.lvl", 1)
        unknown.parent(False, True)
        unknown.hardware(False, True)
        unknown.write_control(0x5800)
        unknown.raw_write(0x0ad07004, 0)
        unknown.raw_write(MDSS_ADDRESS, 0)
        unknown.raw_write(NPU_CFG_AHB, RETENTION_MASK)
        unknown.step(137)
        unknown_path = out / "npu-unknown-parent.bin"
        expected = oracle(0x5800, False, True, False, True)
        saved_unknown = save_source(unknown, unknown_path, expected, "state", RETENTION_MASK)
        report["migration_sources"].append(saved_unknown)
        target = new("unknown-target", rpmh=True, incoming=True, group="state-known")
        target.program("mmcx.lvl", 3)
        target.trigger()
        target.parent(True, True)
        target.hardware(True, True)
        target.write_control(0)
        target.expect_line(True, True)
        target.set_clock(987654321)
        write_json(target.directory / "destination-before-load.json", combined_snapshot(target))
        first = receive_source(target, unknown_path, saved_unknown)
        restored = record("unknown_source_over_known_on_target", unknown, target,
                          unknown_path, saved_unknown, first)
        assert target.raw_read(NPU_CFG_AHB) == POWER | RETENTION_MASK
        target.monitor("cont")
        target.step(1)
        assert target.resource("mmcx.lvl") == 1
        check_state(target, expected)
        for path in (CAMERA, MDSS):
            assert target.qom("domain0.powered", path) is True
        target.run_one("mmcx.lvl", 0)
        assert_no_npu_supplier(target, expected, False)
        target.run_one("mmcx.lvl", 2)
        assert_no_npu_supplier(target, expected, True)
        target.parent(True, True)
        check_state(target, oracle(0x5800, True, True, False, True))
        target.parent(True, False)
        check_state(target, oracle(0x5800, True, False, False, True))
        assert target.cfg_period() == target.cfg_period(True) == 0
        restored.update(continued_after_restore=True,
                        stale_target_parent_command_discarded=True,
                        unknown_npu_supply_not_replaced_by_real_mmcx=True)
        report["migrations"].append(restored)
        passed("unknown_parent_restores_over_known_on_before_mmio_and_ignores_real_mmcx")

        state = new("known-on-source")
        state.parent(True, True)
        state.hardware(True, False)
        state.write_control(0x4800)
        state.raw_write(NPU_CFG_AHB, RETENTION_MASK)
        state_path = out / "npu-known-on-fixture.bin"
        saved_state = save_source(state, state_path,
                                 oracle(0x4800, True, True, True, False),
                                 "unknown", RETENTION_MASK)
        report["migration_sources"].append(saved_state)
        known_target = new("known-on-target", incoming=True)
        known_target.parent(False, True)
        known_target.hardware(False, True)
        known_target.write_control(0)
        first = receive_source(known_target, state_path, saved_state)
        restored = record("explicit_known_on_source_over_unknown_target", state,
                          known_target, state_path, saved_state, first)
        known_target.monitor("cont")
        known_target.step(1000)
        check_state(known_target, oracle(0x4800, True, True, True, False))
        known_target.expect_line(True, True)
        known_target.parent(True, False)
        check_state(known_target, oracle(0x4800, True, False, True, False))
        known_target.parent(False, False)
        check_state(known_target, oracle(0x4800, False, False, True, False))
        known_target.write_control(0x4801)
        check_state(known_target, oracle(0x4801, False, False, True, False))
        assert known_target.raw_read(NPU_CFG_AHB) == POWER | RETENTION_MASK
        restored.update(continued_after_restore=True, explicit_test_provider_only=True)
        report["migrations"].append(restored)
        passed("explicit_known_on_fixture_restores_over_unknown_without_power_fabrication")

        pending = new("pending-source", rpmh=True, group="state-known")
        pending.step(3000)
        pending.write_control(RETAIN)
        pending.raw_write(0x0ad07004, 0)
        pending.raw_write(MDSS_ADDRESS, 0)
        pending.raw_write(NPU_CFG_AHB, RETENTION_MASK)
        pending.dwrite(0xd00, 1)
        pending.program("mmcx.lvl", 2)
        pending.trigger()
        pending.step(400)
        assert pending.last_clock == 3400 and pending.status() == ISSUED
        pending_path = out / "npu-unrelated-mmcx-pending.bin"
        pending_expected = oracle(RETAIN)
        saved_pending = save_source(pending, pending_path, pending_expected,
                                     "parent-pending", RETENTION_MASK)
        report["migration_sources"].append(saved_pending)
        resumed = new("pending-target", rpmh=True, incoming=True, group="state-known")
        resumed.program("mmcx.lvl", 1)
        resumed.trigger()
        resumed.parent(True, True)
        resumed.write_control(0)
        resumed.expect_line(True, True)
        resumed.set_clock(3400)
        first = receive_source(resumed, pending_path, saved_pending)
        restored = record("pending_unrelated_real_mmcx", pending, resumed,
                          pending_path, saved_pending, first)
        mmio_before = resumed.device_mmio_operations
        resumed.monitor("cont")
        resumed.step(599)
        assert resumed.resource("mmcx.lvl") == 0
        assert_no_npu_supplier(resumed, pending_expected, False)
        resumed.expect_line(False)
        resumed.step(1)
        assert resumed.last_clock == 4000 and resumed.resource("mmcx.lvl") == 2
        assert_no_npu_supplier(resumed, pending_expected, True)
        resumed.expect_line(False)
        assert resumed.device_mmio_operations == mmio_before
        write_json(resumed.directory / "deadline-before-first-device-mmio.json", {
            "virtual_ns": 4000, "snapshot": combined_snapshot(resumed),
            "observed_remaining_599_then_1_ns": True,
            "device_mmio_after_restore_before_deadline_observation": False})
        resumed.reject("power-feedback-unknown")
        assert resumed.status() == ISSUED | COMPLETE and resumed.dread(0xd04) == 1
        assert resumed.raw_read(NPU_CFG_AHB) == POWER | RETENTION_MASK
        resumed.parent(True, False)
        check_state(resumed, oracle(RETAIN, True, False))
        resumed.expect_line(True, True)
        resumed.parent(True, True)
        check_state(resumed, oracle(RETAIN, True, True))
        restored.update(continued_after_restore=True, absolute_rpmh_deadline_ns=4000,
                        remaining_ns_at_transfer=600,
                        timer_and_validity_verified_before_first_device_mmio=True,
                        real_rpmh_completion_did_not_supply_npu=True)
        report["migrations"].append(restored)
        passed("pending_mmcx_completion_restores_camera_mdss_but_npu_supply_stays_unknown")

        disabled = new("disabled-source", reference=None)
        disabled_path = out / "npu-disabled.bin"
        saved_disabled = save_source(disabled, disabled_path,
                                      oracle(reference=False), "unknown", 0)
        report["migration_sources"].append(saved_disabled)
        for name, path, saved, enabled, diagnostic in (
                ("enabled_to_disabled", state_path, saved_state, False, "0 != 1"),
                ("disabled_to_enabled", disabled_path, saved_disabled, True, "1 != 0")):
            reject = new("reject-" + name, incoming=True, reference=enabled)
            reject.set_clock(saved["source_virtual_ns"])
            details = load_failure(reject, path, diagnostic)
            report["rejected_migrations"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": False, **details})
            passed("profile_" + name + "_rejected", **details)

        original = state_path.read_bytes()
        section = saved_state["sections"]["npu"]
        for name, relative, width, old, xor, diagnostic in (
                ("domain_address", 42, 8, ADDRESS, 4, f"{ADDRESS:x} != {ADDRESS ^ 4:x}"),
                ("undeclared_hw_control", 58, 4, 0x4800, HW_MODE,
                 TYPE + ": invalid migrated npu domain 0")):
            absolute = section["payload_offset"] + relative
            assert int.from_bytes(original[absolute:absolute + width], "big") == old
            mutated = bytearray(original)
            mutated[absolute:absolute + width] = (old ^ xor).to_bytes(width, "big")
            differences = [index for index, (a, b) in enumerate(zip(original, mutated)) if a != b]
            assert differences == [absolute + width - 1]
            assert (original[differences[0]] ^ mutated[differences[0]]).bit_count() == 1
            path = out / f"npu-corrupt-{name}.bin"
            path.write_bytes(mutated)
            reject = new("reject-corrupt-" + name, incoming=True)
            reject.set_clock(saved_state["source_virtual_ns"])
            details = load_failure(reject, path, diagnostic)
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": True,
                "field_offset": relative, "changed_byte_offsets": differences,
                "changed_bits": 1, "original_stream_unchanged": True, **details})
            passed("single_bit_" + name + "_rejected", **details)
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
    out = args.out or Path(tempfile.mkdtemp(prefix="tb371fc-npu-gdsc-"))
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / "result.json").exists(), "refusing to overwrite a receipt"
    started, original_hash = time.monotonic(), digest(Path(args.qemu))
    report = {
        "schema": "tb371fc-npu-gdsc-v1", "suite": "npu-gdsc-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "expected_unmodified_streams": 4,
        "expected_stream_files": 6, "vmstate_version": 1, "vmstate_payload_bytes": VMSTATE_BYTES,
        "domain_count": 1, "register_count": 1, "sparse_window_count": 1,
        "mapped_bytes": 4, "gpio_output_count": 2, "parent_resource_index": PARENT_RESOURCE,
        "parent_supply_declared": False, "parent_power_connected": False,
        "hardware_mode_domain_mask": 0, "domain_dt_flags": 1,
        "reference_identity": IDENTITY, "camera_vmstate_version": 1,
        "camera_vmstate_payload_bytes": 194, "mdss_vmstate_version": 1,
        "mdss_vmstate_payload_bytes": 64, "modeled_timer_count": 0,
        "modeled_clock_count": 0, "gcc_vmstate_version": 3,
        "gcc_npu_cfg_ahb_clock_id": 42, "gcc_npu_cfg_ahb_write_mask": RETENTION_MASK,
        "gcc_npu_cfg_ahb_external_parent_index": 21,
        "gcc_npu_cfg_ahb_status_mask": 0xf0000000,
        "actual_dt_sha256": DT_SHA256, "fixed_gdsc_driver_sha256": DRIVER_SHA256,
        "reference_command_db_sha256": PROFILE["command_db_sha256"],
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "npu_execution_observed": False, "analog_power_modeled": False,
        "retention_effect_modeled": False, "android_boot_passed": False,
        "parent_output_intercepted": False, "binary_unchanged": False,
        "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "migration_sources": [], "passed": False,
    }
    try:
        for index, (test, options) in enumerate(ORDINARY):
            rig = None
            fixture = out / f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = NpuGdscRig(args, fixture, **options)
                metadata = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **metadata})
            except Exception:
                report["checks"].append({"name": test.__name__, "passed": False,
                                         "fixture": fixture.name, "error": traceback.format_exc()})
                raise
            finally:
                if rig:
                    rig.close()
        for test in (explicit_npu_profile_is_independent_of_camera_mdss_npucc_and_gpu,
                     paused_tcg_pure_getters_do_not_execute_guest_or_repair_state):
            fixture = out / test.__name__
            fixture.mkdir()
            metadata = test(args, fixture) or {}
            report["checks"].append({"name": test.__name__, "passed": True,
                                     "fixture": fixture.name, **metadata})
        migration_checks(args, out, report)
        assert len(report["checks"]) == len({check["name"] for check in report["checks"]}) == EXPECTED_CHECKS
        assert all(check["passed"] for check in report["checks"])
        assert len(report["migrations"]) == 3
        assert all(item["source_status"] == item["target_status"] == "completed" and
                   item["continued_after_restore"] for item in report["migrations"])
        assert len(report["rejected_migrations"]) == len(report["corrupted_stream_rejections"]) == 2
        assert len(report["migration_sources"]) == 4
        files = [out / item["file"] for item in report["migration_sources"]]
        files += [out / item["snapshot_file"] for item in report["corrupted_stream_rejections"]]
        assert len(files) == len(set(files)) == 6
        report["actual_stream_files"] = [
            {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path)} for path in files]
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = original_hash == report["qemu_sha256_after"]
        report["passed"] = report["passed"] and report["binary_unchanged"]
        report["seconds"] = round(time.monotonic() - started, 6)
        write_json(out / "result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
