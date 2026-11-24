#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled VIDEO GDSC transactions, three-way mmcx fanout and real migrations.

The four four-byte words and VIDEO_AHB retention request fields are bounded
by the pinned vendor source and original DT. This gate does not execute guest
instructions, video decoding, DMA or firmware. Unknown power/clock inputs are
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
from test_npu_gdsc import npu_payload, oracle as npu_oracle
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels
from test_rpmh_sm8250 import PROFILE, ISSUED, COMPLETE

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-video-gdsc", "/machine/video-gdsc"
CAMERA, MDSS, NPU = "/machine/camera-gdsc", "/machine/mdss-gdsc", "/machine/npu-gdsc"
MDSS_ADDRESS, NPU_ADDRESS = 0x0af03000, 0x09981004
DOMAIN_NAMES = ("mvs0c", "mvs1c", "mvs0", "mvs1")
ADDRESSES = (0x0abf0bf8, 0x0abf0c98, 0x0abf0d18, 0x0abf0d98)
DT_FLAGS = (1, 1, 3, 3)
CAMERA_ADDRESSES = (0x0ad07004, 0x0ad08004, 0x0ad09004,
                    0x0ad0a004, 0x0ad0b004, 0x0ad0c144)
REGIONS = tuple(f"{TYPE}.{name}-control" for name in DOMAIN_NAMES)
GCC, RAM = 0x100000, 0x80010000
VIDEO_AHB, CAMERA_AHB, DISP_AHB = GCC + 0xb004, GCC + 0xb008, GCC + 0xb00c
NPU_CFG_AHB = GCC + 0x4d004
RPMH, RPMH_BASE = "/machine/rpmh", 0x18200000
COLLAPSE, HW_MODE, RETAIN, WAIT, POWER = 1, 2, 1 << 11, 15 << 12, 1 << 31
WRITE_MASK, IDENTITY, VMSTATE_BYTES = 0xf803, 0x56444731, 142
RETENTION_MASK, EXPECTED_CHECKS = 0x6001, 22
DEFAULT_MACHINE = (
    "tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
    "rpmh-profile=off,rpmh-boot-configured=off"
)


def oracle(controls=None, parent_known=False, parent_on=False,
           hw_known=None, hw_requests=None, reference=True):
    """Enumerate uncertain Boolean inputs independently of device getters."""
    controls = [COLLAPSE] * 4 if controls is None else list(controls)
    hw_known = [False] * 4 if hw_known is None else list(hw_known)
    hw_requests = [False] * 4 if hw_requests is None else list(hw_requests)
    assert len(controls) == len(hw_known) == len(hw_requests) == 4
    result = {
        "enabled-reference": bool(reference),
        "globals": dict(zip(GLOBAL_FIELDS, (
            int(bool(reference)), IDENTITY, 4, 4, 16, 3, 1, WRITE_MASK, POWER,
        ))),
        "parent-known": bool(parent_known), "parent-on": bool(parent_on),
        "domains": [],
    }
    parent_values = {bool(parent_on)} if parent_known else {False, True}
    for i, control in enumerate(controls):
        assert not control & ~WRITE_MASK
        assert not control & HW_MODE or DT_FLAGS[i] & 2
        if control & HW_MODE:
            request_values = {bool(hw_requests[i])} if hw_known[i] else {False, True}
        else:
            request_values = {not bool(control & COLLAPSE)}
        possible = {parent and request for parent in parent_values for request in request_values}
        result["domains"].append(dict(zip(DOMAIN_FIELDS, (
            DOMAIN_NAMES[i], ADDRESSES[i], DT_FLAGS[i], 100, control,
            len(request_values) == 1, request_values == {True},
            bool(reference) and len(possible) == 1,
            bool(reference) and possible == {True}, bool(hw_known[i]), bool(hw_requests[i]),
        ))))
    return result


class VideoRig(CameraRig):
    """Reuse only the existing transport and real RPMh command helpers.

    This constructor and the VIDEO accessors deliberately do not mutate the
    camera module's globals, constructor, oracle or acceptance contract.
    """

    def __init__(self, args, directory, *, reference=True, incoming=False,
                 rpmh=False, group="power", accel="qtest", camera=True,
                 mdss=True, npu=False, legacy_gpu=False):
        self.args = copy.copy(args)
        self.args.qom_path, self.args.base = RPMH, RPMH_BASE
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.has_rpmh = bool(reference), bool(rpmh)
        self.group, self.accel = group, accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.video_mmio_operations = self.camera_mmio_operations = 0
        self.mdss_mmio_operations = self.npu_mmio_operations = 0
        self.has_npu = bool(npu)
        self.connections = []
        self.events, self.irq, self.seen_irq = [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="videogdsc-"))
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
                   "-global", f"qcom-npu-gdsc.enabled-reference={'on' if npu else 'off'}"]
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
            if any(base <= address < base + 4 for base in ADDRESSES):
                self.video_mmio_operations += 1
                self.device_mmio_operations += 1
            elif MDSS_ADDRESS <= address < MDSS_ADDRESS + 4:
                self.mdss_mmio_operations += 1
                self.device_mmio_operations += 1
            elif NPU_ADDRESS <= address < NPU_ADDRESS + 4:
                self.npu_mmio_operations += 1
                self.device_mmio_operations += 1
        # The inherited transport accounts for GCC, camera and RPMh.
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
        return self.snapshot_at(DEVICE, 4)

    def camera_snapshot(self):
        return self.snapshot_at(CAMERA, 6)

    def mdss_snapshot(self):
        return self.snapshot_at(MDSS, 1)

    def npu_snapshot(self):
        return self.snapshot_at(NPU, 1)

    def parent(self, known, on):
        assert not self.has_rpmh, "Do not override the connected real mmcx provider"
        self.input("parent-on", on)
        self.input("parent-known", known)

    def input(self, name, level, domain=0):
        assert 0 <= domain < 4
        assert name in ("parent-known", "parent-on", "hw-request-known", "hw-request")
        assert not name.startswith("parent-") or domain == 0
        self.command(f"set_irq_in {DEVICE} {name} {domain} {int(level)}")

    def hardware(self, domain, known, requested):
        self.input("hw-request", requested, domain)
        self.input("hw-request-known", known, domain)

    def write_domain(self, domain, value):
        self.raw_write(ADDRESSES[domain], value)

    def read_domain(self, domain):
        return self.raw_read(ADDRESSES[domain])

    def video_period(self, parent=False):
        name = "gcc_video_ahb_clk" + ("_parent" if parent else "")
        return self.qom("qtest-clock-period", "/machine/gcc/" + name)

    def expect_lines(self, expected, require_seen=False):
        assert self.group is not None and len(expected) == 4
        self.raw_read(RAM)
        if require_seen:
            assert set(range(4)) <= self.seen_irq, (self.group, self.seen_irq)
        actual = [self.irq.get(i, False) for i in range(4)]
        assert actual == list(expected), (self.group, actual, expected)

    def reject(self, domain, reason, *, write=False, value=0):
        text = (f"{TYPE}: rejected {'write' if write else 'read'} domain={domain} "
                f"offset=0x0 size=4 value=0x{value:x} reason={reason}")
        before = self.diagnostic_count(text)
        if write:
            self.write_domain(domain, value)
        else:
            self.read_domain(domain)
        assert self.diagnostic_count(text) == before + 1, text


def check_state(rig, expected, feedback=True):
    actual = rig.pure_snapshot()
    assert actual == expected, (actual, expected)
    if feedback:
        for i, domain in enumerate(expected["domains"]):
            if not expected["enabled-reference"]:
                rig.reject(i, "reference-disabled")
            elif not domain["state-known"]:
                rig.reject(i, "power-feedback-unknown")
            else:
                assert rig.read_domain(i) == domain["control"] | (POWER if domain["powered"] else 0)
    return actual


def write_controls(rig, controls):
    assert len(controls) == 4
    for i, control in enumerate(controls):
        rig.write_domain(i, control)


def hardware_inputs(rig, known, requests):
    assert len(known) == len(requests) == 4
    for i in range(4):
        rig.hardware(i, known[i], requests[i])


def cold_identity_four_windows_and_readonly_properties(r):
    expected = oracle()
    check_state(r, expected)
    assert r.pure_snapshot() == expected and r.last_clock == 0
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory / "mtree.txt").write_text(tree)
    for address, region in zip(ADDRESSES, REGIONS):
        found = {(int(first, 16), int(last, 16)) for first, last in re.findall(
            r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: " + re.escape(region) + r"(?:\s|$)", tree)}
        assert found == {(address, address + 3)}, (region, found)
    properties = {**expected["globals"], "parent-known": False, "parent-on": False}
    for i, domain in enumerate(expected["domains"]):
        properties.update({f"domain{i}." + key: value for key, value in domain.items()})
    for name, value in properties.items():
        reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in reply and "not writable" in reply["error"]["desc"], reply
    reply = r.raw_monitor("qom-set", {"path": DEVICE, "property": "enabled-reference", "value": False})
    assert "error" in reply and "realized" in reply["error"]["desc"], reply
    check_state(r, expected)
    return {"readonly_properties_checked": len(properties), "mapped_bytes": 16}


def literal_four_domain_probe_is_readable_when_collapsed(r):
    controls = [((i + 1) << 12) | RETAIN | COLLAPSE for i in range(4)]
    for i, control in enumerate(controls):
        assert r.read_domain(i) == COLLAPSE
        r.write_domain(i, r.read_domain(i) & ~(HW_MODE | 4))
        r.write_domain(i, control)
        r.write_domain(i, r.read_domain(i) & ~(HW_MODE | 4))
    check_state(r, oracle(controls))
    requested = [control & ~COLLAPSE for control in controls]
    write_controls(r, requested)
    check_state(r, oracle(requested))
    assert all(not d["state-known"] for d in r.pure_snapshot()["domains"])
    write_controls(r, controls)
    check_state(r, oracle(controls))
    assert r.last_clock == 0
    return {"native_probe_sequence_replayed_by_qtest": True,
            "native_kernel_executed": False, "parent_supply_assumed": False}


def software_three_valued_parent_truth_and_readonly_power_rmw(r):
    for known, on, collapse, carried in itertools.product((False, True), repeat=4):
        r.parent(known, on)
        controls = [((i + 1) << 12) | RETAIN | int(collapse) for i in range(4)]
        write_controls(r, [control | (POWER if carried else 0) for control in controls])
        check_state(r, oracle(controls, known, on))
    assert r.last_clock == 0
    return {"software_truth_rows_per_domain": 16}


def declared_hardware_modes_use_request_knowledge_and_ignore_collapse(r):
    for pk, po, hk, request, collapse in itertools.product((False, True), repeat=5):
        r.parent(pk, po)
        known, requests = [False, False, hk, hk], [False, False, request, request]
        hardware_inputs(r, known, requests)
        controls = [COLLAPSE, COLLAPSE, HW_MODE | RETAIN | int(collapse),
                    HW_MODE | 0x7000 | int(collapse)]
        write_controls(r, controls)
        check_state(r, oracle(controls, pk, po, known, requests))
    return {"hardware_truth_rows_per_declared_domain": 32, "hardware_domains": [2, 3]}


def undeclared_mvs_c_hardware_modes_reject_atomically(r):
    for pk, po, hk, request, collapse in itertools.product((False, True), repeat=5):
        r.parent(pk, po)
        known, requests = [hk, hk, False, False], [request, request, False, False]
        hardware_inputs(r, known, requests)
        controls = [RETAIN | int(collapse), 0x7000 | int(collapse), COLLAPSE, COLLAPSE]
        write_controls(r, controls)
        expected = oracle(controls, pk, po, known, requests)
        check_state(r, expected)
        for domain in (0, 1):
            for status in (0, POWER):
                r.reject(domain, "hw-mode-not-in-reference", write=True,
                         value=controls[domain] | HW_MODE | status)
                assert r.pure_snapshot() == expected
    return {"hardware_mode_rejected_domains": [0, 1], "input_combinations": 32}


def reserved_bits_reject_atomically_without_changing_siblings(r):
    r.parent(True, True)
    controls = [0x1800, 0x2801, 0x3802, 0x4803]
    known, requests = [True, False, True, False], [True] * 4
    hardware_inputs(r, known, requests)
    write_controls(r, controls)
    before = r.pure_snapshot()
    siblings = (r.camera_snapshot(), r.mdss_snapshot(), r.npu_snapshot())
    for domain, control in enumerate(controls):
        for bit in (2, 3, 10, 16, 30):
            r.reject(domain, "unsupported-bits", write=True, value=control | (1 << bit))
            assert r.pure_snapshot() == before
            assert (r.camera_snapshot(), r.mdss_snapshot(), r.npu_snapshot()) == siblings
        r.write_domain(domain, control | POWER)
    check_state(r, oracle(controls, True, True, known, requests))


def unknown_feedback_stays_unknown_through_reads_and_time(r):
    r.parent(False, True)
    controls = [0x1800, 0x2801, 0x3802, 0x4803]
    known, requests = [True, False, False, True], [True] * 4
    hardware_inputs(r, known, requests)
    write_controls(r, controls)
    expected = oracle(controls, False, True, known, requests)
    for _ in range(3):
        check_state(r, expected)
    r.step(1_000_000)
    check_state(r, expected)
    known[2], requests[2] = True, False
    r.hardware(2, known[2], requests[2])
    check_state(r, oracle(controls, False, True, known, requests))
    assert r.qom("domain2.state-known") is True and r.qom("domain2.powered") is False
    r.parent(True, True)
    check_state(r, oracle(controls, True, True, known, requests))


def access_shapes_and_unowned_videocc_holes_are_exact(r):
    controls = [((i + 1) << 12) | RETAIN | COLLAPSE for i in range(4)]
    write_controls(r, controls)
    before = r.pure_snapshot()
    for address, region in zip(ADDRESSES, REGIONS):
        for operation in ("read", "write"):
            for width, size in (("b", 1), ("w", 2)):
                previous = core_diagnostics(r.log_path.read_text(errors="replace"))
                if operation == "read":
                    r.read_absolute(address, width)
                else:
                    r.write_absolute(address, 0, width)
                assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == [
                    (operation, 0, size, region, "invalid size (min:4 max:4)")]
            for displacement in (1, 2):
                previous = core_diagnostics(r.log_path.read_text(errors="replace"))
                if operation == "read":
                    r.read_absolute(address + displacement)
                else:
                    r.write_absolute(address + displacement, 0x03020100)
                chunks = ((1, 1), (2, 2), (4, 1)) if displacement == 1 else ((2, 2), (4, 2))
                expected = [(operation, address + offset, size, "(null)", "rejected")
                            if offset == 4 else
                            (operation, offset, size, region, "invalid size (min:4 max:4)")
                            for offset, size in chunks]
                assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == expected
    assert r.pure_snapshot() == before
    holes = sorted({0x0abf0000, 0x0abf1000, 0x0abffffc,
                    *(address + 4 for address in ADDRESSES)})
    previous = core_diagnostics(r.log_path.read_text(errors="replace"))
    expected = []
    for address in holes:
        r.raw_read(address)
        r.raw_write(address, 0xffffffff)
        expected.extend((op, address, 4, "(null)", "rejected") for op in ("read", "write"))
    assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == expected
    assert r.pure_snapshot() == before
    # The four preceding BCR words now belong to the disabled VIDEOCC model.
    # They must still reject accesses without changing either device's state.
    videocc_path = "/machine/videocc"
    assert r.qom("enabled-reference", videocc_path) is False
    for address in (address - 4 for address in ADDRESSES):
        offset = address - 0x0abf0000
        register = f"reg.{offset:04x}"
        original = r.qom(register, videocc_path)
        for operation in ("read", "write"):
            diagnostic = (f"qcom-sm8250-videocc: unmodeled {operation} "
                          f"+0x{offset:x} size 4")
            count = r.diagnostic_count(diagnostic)
            if operation == "read":
                r.raw_read(address)
            else:
                r.raw_write(address, 0xffffffff)
            assert r.diagnostic_count(diagnostic) == count + 1
            assert r.qom(register, videocc_path) == original
            assert r.pure_snapshot() == before
    # Each spanning write commits its valid low word, then rejects the next
    # unowned word. The gate does not claim an atomic 64-bit device access.
    for i, address in enumerate(ADDRESSES):
        previous = core_diagnostics(r.log_path.read_text(errors="replace"))
        controls[i] = ((i + 2) << 12) | COLLAPSE
        r.write_absolute(address, (0x12345678 << 32) | controls[i], "q")
        assert r.read_domain(i) == controls[i]
        assert core_diagnostics(r.log_path.read_text(errors="replace"))[len(previous):] == [
            ("write", address + 4, 4, "(null)", "rejected")]
    check_state(r, oracle(controls))
    return {"unowned_controller_words_checked": len(holes),
            "disabled_videocc_reset_words_checked": len(ADDRESSES),
            "wide_write_atomicity_claimed": False}


def four_domain_controls_and_hardware_inputs_are_isolated(r):
    r.parent(True, True)
    controls = [0x1800, 0x2801, 0x3803, 0x4802]
    known, requests = [True, False, True, False], [False, True, True, True]
    hardware_inputs(r, known, requests)
    write_controls(r, controls)
    check_state(r, oracle(controls, True, True, known, requests))
    for i in range(4):
        before = r.pure_snapshot()
        requests[i] = not requests[i]
        r.hardware(i, known[i], requests[i])
        after = check_state(r, oracle(controls, True, True, known, requests))
        assert all(before["domains"][j] == after["domains"][j] for j in range(4) if j != i)
        before = after
        controls[i] ^= COLLAPSE | RETAIN
        r.write_domain(i, controls[i])
        after = check_state(r, oracle(controls, True, True, known, requests))
        assert all(before["domains"][j] == after["domains"][j] for j in range(4) if j != i)


def reset_preserves_provider_inputs_and_collapses_all_four_domains(r):
    r.parent(True, True)
    write_controls(r, [0] * 4)
    r.expect_lines([True] * 4, True)
    known, requests = [True, False, True, False], [False, True, True, True]
    hardware_inputs(r, known, requests)
    controls = [0, 0, HW_MODE, HW_MODE]
    write_controls(r, controls)
    r.expect_lines([True, True, True, False])
    r.raw_write(VIDEO_AHB, RETENTION_MASK)
    r.raw_write(GCC + 4, 0x35)
    assert r.raw_read(GCC + 4) == 0x35
    r.events.clear()
    r.monitor("system_reset")
    r.wait_for(lambda: r.raw_read(GCC + 4) == 0, "GCC reset witness")
    r.monitor("query-status")
    assert any(event.get("event") == "RESET" for event in r.events)
    check_state(r, oracle(parent_known=True, parent_on=True,
                          hw_known=known, hw_requests=requests))
    r.expect_lines([False] * 4)
    assert r.raw_read(VIDEO_AHB) == 0 and r.video_period() == r.video_period(True) == 0
    write_controls(r, controls)
    check_state(r, oracle(controls, True, True, known, requests))
    r.expect_lines([True, True, True, False])


def power_and_knowledge_are_distinct_four_line_gpio_groups(r):
    r.parent(True, True)
    write_controls(r, [0] * 4)
    r.expect_lines([True] * 4, True)
    r.parent(False, True)
    controls = [COLLAPSE, 0, HW_MODE | COLLAPSE, HW_MODE | COLLAPSE]
    known, requests = [False, False, True, False], [False, False, False, True]
    hardware_inputs(r, known, requests)
    write_controls(r, controls)
    check_state(r, oracle(controls, False, True, known, requests))
    r.expect_lines([False] * 4)
    other = VideoRig(r.args, r.directory / "validity", group="state-known")
    try:
        other.parent(True, True)
        write_controls(other, [0] * 4)
        other.expect_lines([True] * 4, True)
        other.parent(False, True)
        hardware_inputs(other, known, requests)
        write_controls(other, controls)
        check_state(other, oracle(controls, False, True, known, requests))
        other.expect_lines([True, False, True, False], True)
    finally:
        other.close()


def request_npu_without_supplier(r):
    assert r.has_npu
    r.raw_write(NPU_ADDRESS, 0)
    assert r.npu_snapshot() == npu_oracle(0)


def assert_shared_mmcx(r, powered, video_expected=None):
    assert r.has_rpmh
    for path, count in ((DEVICE, 4), (CAMERA, 6), (MDSS, 1)):
        assert r.qom("parent-known", path) is True
        assert r.qom("parent-on", path) is bool(powered)
        if path != DEVICE or video_expected is None:
            assert all(r.qom(f"domain{i}.powered", path) is bool(powered) for i in range(count))
    if video_expected is not None:
        assert r.pure_snapshot() == video_expected
    assert r.npu_snapshot() == npu_oracle(0)


def request_camera_mdss_and_npu(r):
    for address in CAMERA_ADDRESSES:
        r.raw_write(address, 0)
    r.raw_write(MDSS_ADDRESS, 0)
    request_npu_without_supplier(r)


def real_mmcx_three_way_fanout_preserves_unconnected_npu(r):
    assert r.has_rpmh and r.resource("mmcx.lvl", "present") == 1
    assert r.qom("num-lines", "/machine/mmcx-gdsc-fanout") == 3
    write_controls(r, [0] * 4)
    request_camera_mdss_and_npu(r)
    assert_shared_mmcx(r, False)
    for resource in ("cx.lvl", "gfx.lvl", "mx.lvl"):
        r.run_one(resource, 1)
        assert r.resource(resource) == 1 and r.resource("mmcx.lvl") == 0
        assert_shared_mmcx(r, False)
    r.clear()
    r.program("mmcx.lvl", 1)
    r.trigger()
    r.step(999)
    assert_shared_mmcx(r, False)
    r.step(1)
    assert_shared_mmcx(r, True)
    r.expect_lines([True] * 4, True)
    assert r.status() == ISSUED | COMPLETE
    r.run_one("mmcx.lvl", 2, drv=0)
    r.run_one("mmcx.lvl", 0, drv=2)
    assert r.resource("mmcx.lvl") == 2
    assert_shared_mmcx(r, True)
    r.run_one("mmcx.lvl", 0, drv=0)
    assert_shared_mmcx(r, False)
    r.expect_lines([False] * 4)
    return {"parent_output_intercepted": False, "fanout_consumers": 3,
            "powered_domains_on_known_mmcx": 11, "npu_parent_fixture_used": False}


def video_ahb_retention_keeps_unknown_clock_and_neighbor_masks(r):
    before = r.pure_snapshot()
    for low in (0, 1):
        r.raw_write(VIDEO_AHB, low)
        for bit in (1 << 13, 1 << 14):
            value = r.raw_read(VIDEO_AHB) | bit
            r.raw_write(VIDEO_AHB, value)
            assert r.raw_read(VIDEO_AHB) == value
        for bit in (1 << 13, 1 << 14):
            value = r.raw_read(VIDEO_AHB) & ~bit
            r.raw_write(VIDEO_AHB, value)
            assert r.raw_read(VIDEO_AHB) == value
        assert r.raw_read(VIDEO_AHB) == low
    for value in (0, 1, 0x2000, 0x2001, 0x4000, 0x4001, 0x6000, 0x6001):
        r.raw_write(VIDEO_AHB, value)
        r.step(5000)
        assert r.raw_read(VIDEO_AHB) == value
        assert r.video_period() == r.video_period(True) == 0
        assert r.pure_snapshot() == before
    for address, bad in ((VIDEO_AHB, 0x6003), (VIDEO_AHB, 0x7001),
                         (VIDEO_AHB, 0x8001), (CAMERA_AHB, 0x6003),
                         (DISP_AHB, 0x6003), (NPU_CFG_AHB, 0x7003)):
        text = f"qcom-sm8250-gcc: unsupported write 0x{bad:08x} at 0x{address-GCC:x}"
        previous, old = r.diagnostic_count(text), r.raw_read(address)
        r.raw_write(address, bad)
        assert r.diagnostic_count(text) == previous + 1
        assert r.raw_read(address) == old
    r.raw_write(CAMERA_AHB, 0x6001)
    r.raw_write(DISP_AHB, 0x6001)
    r.raw_write(NPU_CFG_AHB, 0x6003)
    assert r.raw_read(CAMERA_AHB) == r.raw_read(DISP_AHB) == 0x6001
    assert r.raw_read(NPU_CFG_AHB) == POWER | 0x6003
    assert r.raw_read(VIDEO_AHB) == RETENTION_MASK
    return {"vendor_binding_id": 205, "qemu_descriptor_id": 196,
            "external_parent_index": 79, "video_cbcr_status_fabricated": False,
            "invented_frequency": False}


ORDINARY = [
    (cold_identity_four_windows_and_readonly_properties, {}),
    (literal_four_domain_probe_is_readable_when_collapsed, {}),
    (software_three_valued_parent_truth_and_readonly_power_rmw, {}),
    (declared_hardware_modes_use_request_knowledge_and_ignore_collapse, {}),
    (undeclared_mvs_c_hardware_modes_reject_atomically, {}),
    (reserved_bits_reject_atomically_without_changing_siblings, {}),
    (unknown_feedback_stays_unknown_through_reads_and_time, {}),
    (access_shapes_and_unowned_videocc_holes_are_exact, {}),
    (four_domain_controls_and_hardware_inputs_are_isolated, {}),
    (reset_preserves_provider_inputs_and_collapses_all_four_domains, {}),
    (power_and_knowledge_are_distinct_four_line_gpio_groups, {}),
    (real_mmcx_three_way_fanout_preserves_unconnected_npu, {"rpmh": True, "npu": True}),
    (video_ahb_retention_keeps_unknown_clock_and_neighbor_masks, {}),
]


def explicit_video_profile_is_independent_of_camera_mdss_npu_and_gpu(args, out):
    for legacy in (False, True):
        rig = VideoRig(args, out / ("legacy" if legacy else "default"),
                       reference=None, legacy_gpu=legacy, npu=True)
        try:
            check_state(rig, oracle(reference=False))
            for i in range(4):
                rig.reject(i, "reference-disabled", write=True)
            rig.parent(True, True)
            hardware_inputs(rig, [True] * 4, [True] * 4)
            check_state(rig, oracle(parent_known=True, parent_on=True,
                                   hw_known=[True] * 4, hw_requests=[True] * 4,
                                   reference=False))
            for path in (CAMERA, MDSS, NPU):
                assert rig.qom("enabled-reference", path) is True
            assert rig.qom("enabled-reference", "/machine/gpu-gx-gdsc") is legacy
            assert rig.qom("boot-collapsed-reference", "/machine/gpu-cx-gdsc") is legacy
        finally:
            rig.close()
    rig = VideoRig(args, out / "video-only", camera=False, mdss=False)
    try:
        check_state(rig, oracle())
        for path in (CAMERA, MDSS, NPU):
            assert rig.qom("enabled-reference", path) is False
    finally:
        rig.close()


def paused_tcg_pure_getters_do_not_execute_guest_or_repair_state(args, out):
    rig = VideoRig(args, out / "tcg", accel="tcg", group=None)
    try:
        assert rig.monitor("query-status")["running"] is False
        assert rig.pure_snapshot() == rig.pure_snapshot() == oracle()
        properties = rig.monitor("qom-list", {"path": DEVICE})
        assert not any(prop["name"].startswith("qtest-") for prop in properties)
        assert rig.monitor("query-status")["running"] is False
        assert rig.cont_commands == rig.device_mmio_operations == 0
    finally:
        rig.close()


def video_payload(snapshot):
    result = struct.pack(">9I", *(snapshot["globals"][name] for name in GLOBAL_FIELDS))
    result += struct.pack(">BB", snapshot["parent-known"], snapshot["parent-on"])
    assert len(snapshot["domains"]) == 4
    for i, domain in enumerate(snapshot["domains"]):
        result += struct.pack(">IQIIIBB", i, domain["address"], domain["dt-flags"],
                              domain["timeout-us"], domain["control"],
                              domain["hw-request-known"], domain["hw-request"])
    assert len(result) == VMSTATE_BYTES
    return result


def combined_snapshot(rig):
    return {"video": rig.pure_snapshot(), "camera": rig.camera_snapshot(),
            "mdss": rig.mdss_snapshot(), "npu": rig.npu_snapshot(),
            "provider": provider_snapshot(rig) if rig.has_rpmh else None,
            "video_parent_period": rig.video_period(True),
            "video_output_period": rig.video_period()}


def inspect_stream(path, snapshot, scenario, gcc_value):
    raw = path.read_bytes()
    sections = {}
    for key, name, size, payload in (
            ("video", TYPE, VMSTATE_BYTES, video_payload(snapshot["video"])),
            ("camera", "qcom-camera-gdsc", 194, camera_payload(snapshot["camera"])),
            ("mdss", "qcom-mdss-gdsc", 64, mdss_payload(snapshot["mdss"])),
            ("npu", "qcom-npu-gdsc", 64, npu_payload(snapshot["npu"]))):
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
    assert regs == {"0xb004": gcc_value, "0xb008": 0, "0xb00c": 0, "0x4d004": 0}, regs
    section.update(selected_registers=regs, complete_gcc_runtime_oracle_claimed=False)
    sections["gcc"] = section
    write_json(path.with_name(path.name + ".sections.json"), sections)
    return sections


def save_source(rig, path, expected, scenario, gcc_value):
    assert rig.raw_read(VIDEO_AHB) == gcc_value
    assert rig.video_period() == rig.video_period(True) == 0
    rig.raw_write(RAM, IDENTITY)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    before = combined_snapshot(rig)
    assert before["video"] == expected
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
    assert rig.raw_read(RAM) == IDENTITY
    field = "powered" if rig.group == "power" else "state-known"
    rig.expect_lines([domain[field] for domain in first["video"]["domains"]])
    evidence = {"snapshot": first, "device_mmio_since_incoming": False,
                "qmp_cont_since_incoming": False, "guest_cpu_executed": False,
                "ram_witness": IDENTITY, "query_migrate": received}
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
        # The four migration fixtures explicitly enable the independent NPU
        # model. No NPU parent GPIO is assigned anywhere in this suite.
        rig = VideoRig(args, out / name, npu=True, **kwargs)
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
        state = new("state-source", rpmh=True)
        state.run_one("mmcx.lvl", 1)
        controls = [0x1800, 0x2801, 0x3803, 0x4802]
        known, requests = [True, False, True, False], [False, True, True, True]
        hardware_inputs(state, known, requests)
        write_controls(state, controls)
        request_camera_mdss_and_npu(state)
        state.raw_write(VIDEO_AHB, RETENTION_MASK)
        state.step(137)
        state_path = out / "video-four-domain-state.bin"
        saved_state = save_source(state, state_path,
                                  oracle(controls, True, True, known, requests),
                                  "state", RETENTION_MASK)
        report["migration_sources"].append(saved_state)
        target = new("state-target", rpmh=True, incoming=True)
        target.program("mmcx.lvl", 3)
        target.trigger()
        hardware_inputs(target, [True] * 4, [False] * 4)
        write_controls(target, [0] * 4)
        target.set_clock(987654321)
        assert target.resource("mmcx.lvl") == 0 and target.status() == ISSUED
        write_json(target.directory / "destination-before-load.json", combined_snapshot(target))
        first = receive_source(target, state_path, saved_state)
        restored = record("independent_four_domain_state_and_all_gdsc_siblings", state,
                          target, state_path, saved_state, first)
        assert target.raw_read(VIDEO_AHB) == RETENTION_MASK
        target.monitor("cont")
        target.step(1)
        assert target.resource("mmcx.lvl") == 1
        check_state(target, oracle(controls, True, True, known, requests))
        assert_shared_mmcx(target, True, oracle(controls, True, True, known, requests))
        target.run_one("mmcx.lvl", 0)
        check_state(target, oracle(controls, True, False, known, requests))
        assert_shared_mmcx(target, False, oracle(controls, True, False, known, requests))
        target.run_one("mmcx.lvl", 2)
        assert_shared_mmcx(target, True, oracle(controls, True, True, known, requests))
        known[3] = True
        target.hardware(3, True, True)
        check_state(target, oracle(controls, True, True, known, requests))
        assert target.video_period() == target.video_period(True) == 0
        assert target.raw_read(VIDEO_AHB) == RETENTION_MASK
        restored.update(continued_after_restore=True,
                        stale_target_parent_command_discarded=True,
                        npu_unknown_parent_preserved=True)
        report["migrations"].append(restored)
        passed("four_domain_state_and_independent_siblings_restore_before_mmio_then_continue")

        pending = new("pending-source", rpmh=True, group="state-known")
        pending.step(3000)
        pending_controls = [0, 0, HW_MODE | RETAIN, HW_MODE | RETAIN | COLLAPSE]
        pending_known, pending_requests = [False, False, False, True], [False, False, True, True]
        hardware_inputs(pending, pending_known, pending_requests)
        write_controls(pending, pending_controls)
        request_camera_mdss_and_npu(pending)
        pending.raw_write(VIDEO_AHB, RETENTION_MASK)
        pending.dwrite(0xd00, 1)
        pending.program("mmcx.lvl", 2)
        pending.trigger()
        pending.step(400)
        assert pending.last_clock == 3400 and pending.status() == ISSUED
        pending_path = out / "video-mmcx-pending.bin"
        saved_pending = save_source(
            pending, pending_path,
            oracle(pending_controls, True, False, pending_known, pending_requests),
            "parent-pending", RETENTION_MASK)
        report["migration_sources"].append(saved_pending)
        resumed = new("pending-target", rpmh=True, incoming=True, group="state-known")
        resumed.program("mmcx.lvl", 1)
        resumed.trigger()
        hardware_inputs(resumed, [True] * 4, [True] * 4)
        write_controls(resumed, [0] * 4)
        resumed.set_clock(3400)
        first = receive_source(resumed, pending_path, saved_pending)
        restored = record("real_mmcx_pending_and_unknown_video_hardware_request", pending,
                          resumed, pending_path, saved_pending, first)
        mmio_before = resumed.device_mmio_operations
        resumed.monitor("cont")
        resumed.step(599)
        assert resumed.resource("mmcx.lvl") == 0
        expected = oracle(pending_controls, True, False, pending_known, pending_requests)
        assert_shared_mmcx(resumed, False, expected)
        resumed.expect_lines([True] * 4, True)
        resumed.step(1)
        assert resumed.last_clock == 4000 and resumed.resource("mmcx.lvl") == 2
        expected = oracle(pending_controls, True, True, pending_known, pending_requests)
        assert_shared_mmcx(resumed, True, expected)
        resumed.expect_lines([True, True, False, True], True)
        assert resumed.device_mmio_operations == mmio_before
        write_json(resumed.directory / "deadline-before-first-device-mmio.json", {
            "virtual_ns": 4000, "snapshot": combined_snapshot(resumed),
            "observed_remaining_599_then_1_ns": True,
            "device_mmio_after_restore_before_deadline_observation": False})
        resumed.reject(2, "power-feedback-unknown")
        assert resumed.status() == ISSUED | COMPLETE and resumed.dread(0xd04) == 1
        assert resumed.raw_read(VIDEO_AHB) == RETENTION_MASK
        pending_known[2] = True
        resumed.hardware(2, True, True)
        check_state(resumed, oracle(pending_controls, True, True, pending_known, pending_requests))
        resumed.expect_lines([True] * 4, True)
        assert resumed.npu_snapshot() == npu_oracle(0)
        restored.update(continued_after_restore=True, absolute_rpmh_deadline_ns=4000,
                        remaining_ns_at_transfer=600,
                        timer_and_validity_verified_before_first_device_mmio=True,
                        real_rpmh_completion_did_not_supply_npu=True)
        report["migrations"].append(restored)
        passed("pending_mmcx_599_then_1_ns_updates_three_consumers_without_npu_or_mmio_repair")

        unknown = new("unknown-source", group="state-known")
        unknown.parent(False, True)
        unknown_controls = [0x1800, 0x2801, 0x3802, 0x4803]
        unknown_known, unknown_requests = [False, True, False, True], [True, False, True, True]
        hardware_inputs(unknown, unknown_known, unknown_requests)
        write_controls(unknown, unknown_controls)
        request_npu_without_supplier(unknown)
        unknown_path = out / "video-unknown-parent-and-request.bin"
        saved_unknown = save_source(
            unknown, unknown_path,
            oracle(unknown_controls, False, True, unknown_known, unknown_requests), "unknown", 0)
        report["migration_sources"].append(saved_unknown)
        other = new("unknown-target", incoming=True, group="state-known")
        other.parent(True, True)
        hardware_inputs(other, [True] * 4, [True] * 4)
        write_controls(other, [0] * 4)
        other.expect_lines([True] * 4, True)
        first = receive_source(other, unknown_path, saved_unknown)
        restored = record("unknown_source_over_known_on_target", unknown, other,
                          unknown_path, saved_unknown, first)
        other.monitor("cont")
        other.step(1000)
        check_state(other, oracle(unknown_controls, False, True, unknown_known, unknown_requests))
        unknown_known[2], unknown_requests[2] = True, False
        other.hardware(2, True, False)
        check_state(other, oracle(unknown_controls, False, True, unknown_known, unknown_requests))
        other.parent(True, True)
        check_state(other, oracle(unknown_controls, True, True, unknown_known, unknown_requests))
        assert other.npu_snapshot() == npu_oracle(0)
        restored.update(continued_after_restore=True, stale_target_availability_discarded=True,
                        explicit_video_fixture_did_not_supply_npu=True)
        report["migrations"].append(restored)
        passed("unknown_parent_and_requests_replace_prewarmed_target_without_becoming_available")

        disabled = new("disabled-source", reference=None, rpmh=True)
        disabled_path = out / "video-disabled.bin"
        saved_disabled = save_source(disabled, disabled_path,
                                      oracle(parent_known=True, reference=False), "unknown", 0)
        report["migration_sources"].append(saved_disabled)
        for name, path, saved, enabled, diagnostic in (
                ("enabled_to_disabled", state_path, saved_state, False, "0 != 1"),
                ("disabled_to_enabled", disabled_path, saved_disabled, True, "1 != 0")):
            reject = new("reject-" + name, incoming=True, rpmh=True, reference=enabled)
            reject.set_clock(saved["source_virtual_ns"])
            details = load_failure(reject, path, diagnostic)
            report["rejected_migrations"].append({
                "name": name, "passed": True, "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": False, **details})
            passed("profile_" + name + "_rejected", **details)

        original = state_path.read_bytes()
        section = saved_state["sections"]["video"]
        for name, relative, width, old, xor, diagnostic in (
                ("mvs0_address", 94, 8, ADDRESSES[2], 4,
                 f"{ADDRESSES[2]:x} != {ADDRESSES[2] ^ 4:x}"),
                ("mvs0c_undeclared_hw_control", 58, 4, 0x1800, HW_MODE,
                 TYPE + ": invalid migrated video domain 0")):
            absolute = section["payload_offset"] + relative
            assert int.from_bytes(original[absolute:absolute + width], "big") == old
            mutated = bytearray(original)
            mutated[absolute:absolute + width] = (old ^ xor).to_bytes(width, "big")
            differences = [index for index, (a, b) in enumerate(zip(original, mutated)) if a != b]
            assert differences == [absolute + width - 1]
            assert (original[differences[0]] ^ mutated[differences[0]]).bit_count() == 1
            path = out / f"video-corrupt-{name}.bin"
            path.write_bytes(mutated)
            reject = new("reject-corrupt-" + name, incoming=True, rpmh=True)
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
    out = args.out or Path(tempfile.mkdtemp(prefix="tb371fc-video-gdsc-"))
    out.mkdir(parents=True, exist_ok=True)
    assert not (out / "result.json").exists(), "refusing to overwrite a receipt"
    started, original_hash = time.monotonic(), digest(Path(args.qemu))
    report = {
        "schema": "tb371fc-video-gdsc-v1", "suite": "video-gdsc-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 3, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "expected_unmodified_streams": 4,
        "expected_stream_files": 6, "vmstate_version": 1, "vmstate_payload_bytes": VMSTATE_BYTES,
        "domain_count": 4, "register_count": 4, "sparse_window_count": 4,
        "mapped_bytes": 16, "gpio_output_count": 8, "parent_resource_index": 3,
        "parent_supply_declared": True, "parent_power_connected": True,
        "fanout_consumer_count": 3, "npu_parent_power_connected": False,
        "hardware_mode_domain_mask": 0xc, "domain_dt_flags": list(DT_FLAGS),
        "reference_identity": IDENTITY, "camera_vmstate_version": 1,
        "camera_vmstate_payload_bytes": 194, "mdss_vmstate_version": 1,
        "mdss_vmstate_payload_bytes": 64, "npu_vmstate_version": 1,
        "npu_vmstate_payload_bytes": 64, "modeled_timer_count": 0,
        "modeled_clock_count": 0, "gcc_vmstate_version": 3,
        "gcc_video_ahb_clock_id": 196, "gcc_video_ahb_write_mask": RETENTION_MASK,
        "gcc_video_ahb_external_parent_index": 79, "gcc_video_ahb_status_mask": 0,
        "actual_dt_sha256": DT_SHA256, "fixed_gdsc_driver_sha256": DRIVER_SHA256,
        "reference_command_db_sha256": PROFILE["command_db_sha256"],
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "video_execution_observed": False, "analog_power_modeled": False,
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
                rig = VideoRig(args, fixture, **options)
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
        for test in (explicit_video_profile_is_independent_of_camera_mdss_npu_and_gpu,
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
