#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual transactions and relative-time migration for the bounded SBM peer.

Cold connected state, 1000 ns latency, host-controlled stalls and diagnostic
write counters are explicit model policies. They do not prove physical NoC
traffic blocking/drain, firmware execution, a Clock change or silicon timing.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import traceback

from test_geni_uart import Rig, connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels
from test_noc_qos import (PORTS as QOS_PORTS, OFFSETS as QOS_OFFSETS,
                         REGISTER_NAMES as QOS_NAMES, matrix_values as qos_values,
                         port_path as qos_path)

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE = "qcom-noc-sbm"
DEVICE = "/machine/noc-sbm-config"
CLEAR, SET, SENSE = 0x01506080, 0x01506088, 0x01506100
ADDRESSES = (CLEAR, SET, SENSE)
REGIONS = dict(zip(ADDRESSES, ("clear", "set", "sense")))
PORT_MASK, PORT_COUNT, LATENCY = 0xff, 8, 1000
REFERENCE_ID = 0x53424d31
EXPECTED_CHECKS, PAYLOAD_BYTES = 28, 152
GCC, RAM_WITNESS = 0x00100000, 0x80010000
INT64_MAX = (1 << 63) - 1
SOURCE_COMMIT = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_SHA256 = "9e408e18745857c575be792a92cb3eb7bcab1fb51b181cc389fd2b81213560a6"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
LAYOUT_BLOB = "614c1a2225a701be9fbce9f6a893db4080614882"
CONTRACT_BLOB = "6346b2fab8d6ec380eaaab42ef9a21c5e27b5be5"
OLD_QOS_TEST_SHA256 = "b2d3ff9127b4a7967e8fe89a4fce9cbeee4ceb5d4e0d655eea042c24407ead00"
GEOMETRY = {
    "instance-id": 0, "clear-address": CLEAR, "set-address": SET,
    "sense-address": SENSE, "supported-port-mask": 255, "port-count": 8,
    "reference-latency-ns": 1000, "reference-identity": REFERENCE_ID,
    "reference-boot-disconnected-mask": 0, "mapped-window-count": 3,
    "mapped-register-count": 3, "mapped-bytes": 12,
}
RUNTIME = ("requested-mask", "sense-mask", "pending-mask",
           "accepted-set-writes", "accepted-clear-writes",
           "last-set-mask", "last-clear-mask")
PORT_PROPERTIES = tuple(f"port{i}-{suffix}" for i in range(8)
                        for suffix in ("remaining-ns", "deadline-ns"))
HOLES = (CLEAR - 4, CLEAR + 4, SET + 4, 0x01506090, 0x01506094,
         SENSE - 4, SENSE + 4, 0x01506000, 0x01506108)


def base_command(args, *, reference=True, accel="qtest", qos_reference=False):
    command = [args.qemu, "-machine", args.machine, "-accel", accel,
               "-m", "128M", "-display", "none", "-monitor", "none",
               "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]
    if reference is not None:
        command += ["-global", TYPE + ".enabled-reference=" +
                    ("on" if reference else "off")]
    if qos_reference:
        command += ["-global", "qcom-noc-qos.enabled-reference=on"]
    return command


class SbmRig(Rig):
    def __init__(self, args, directory, *, incoming=False, reference=True,
                 accel="qtest", qos_reference=False):
        self.args, self.reference = args, reference is True
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qsbm-"))
        self.last_clock = 0
        self.sbm_mmio_operations = 0
        self.cont_commands = 0
        self.connections, self.events = [], []
        self.process = None
        self.irq = {}
        command = base_command(args, reference=reference, accel=accel,
                               qos_reference=qos_reference) + [
            "-qmp", f"unix:{self.socket_dir}/m,server=on,wait=off",
            "-qtest", f"unix:{self.socket_dir}/t,server=on,wait=off",
            "-qtest-log", "/dev/null"]
        if incoming:
            command += ["-incoming", "defer"]
        (self.directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=self.log)
            self.qmp = connect_unix(self.socket_dir / "m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            assert "QMP" in json.loads(self.qmpfile.readline())
            self.monitor("qmp_capabilities")
            self.qtest = connect_unix(self.socket_dir / "t", self.process)
            self.connections.append(self.qtest)
            self.qfile = self.qtest.makefile("rwb", buffering=0)
            if not incoming and accel == "qtest":
                self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, value):
        self.qtest_transcript.write("> " + value + "\n")
        result = super().command(value)
        self.qtest_transcript.write("< OK " + " ".join(result) + "\n")
        return result

    def monitor(self, execute, arguments=None):
        self.qmp_transcript.write("> " + json.dumps([execute, arguments]) + "\n")
        if execute == "cont":
            self.cont_commands += 1
        try:
            result = super().monitor(execute, arguments)
        except BaseException as error:
            self.qmp_transcript.write("< ERROR " + str(error) + "\n")
            raise
        self.qmp_transcript.write("< " + json.dumps(result) + "\n")
        return result

    def read(self, address, width="l"):
        if any(base <= address < base + 4 for base in ADDRESSES):
            self.sbm_mmio_operations += 1
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def write(self, address, value, width="l"):
        if any(base <= address < base + 4 for base in ADDRESSES):
            self.sbm_mmio_operations += 1
        self.command(f"write{width} {address:#x} {value:#x}")

    def get(self, name):
        return self.monitor("qom-get", {"path": DEVICE, "property": name})

    def put(self, name, value):
        return self.monitor("qom-set", {"path": DEVICE, "property": name, "value": value})

    def stall(self, mask):
        self.put("reference-peer-stall-mask", mask)

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns
        return self.last_clock

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return self.last_clock

    def close(self):
        super().close()
        for name in ("qtest_transcript", "qmp_transcript"):
            stream = getattr(self, name, None)
            if stream and not stream.closed:
                stream.close()


def core_diagnostics(text):
    return [(kind, int(address, 16), int(size), region, reason)
            for kind, address, size, region, reason in re.findall(
                r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), size ([0-9]+), "
                r"region '([^']+)', reason: ([^\n]+)", text)]


def device_diagnostics(text):
    return [line for line in text.splitlines() if TYPE + ":" in line]


def diagnostic(address, reason, *, write=True, value=0, width=4):
    operation = "write" if write else "read"
    return (f"{TYPE}: {reason} {operation} {REGIONS[address]}+0x0 "
            f"width={width} value=0x{value:x}")


def snapshot(rig, *, unknown=False):
    geometry = {name: rig.get(name) for name in GEOMETRY}
    assert geometry == GEOMETRY
    geometry["enabled-reference"] = rig.get("enabled-reference")
    geometry["mmio-enabled"] = rig.get("mmio-enabled")
    assert geometry["enabled-reference"] is (not unknown)
    assert geometry["mmio-enabled"] is (not unknown)
    stall = rig.get("reference-peer-stall-mask")
    runtime, ports, errors = {}, [], {}
    if unknown:
        assert stall == 0
        for name in RUNTIME + PORT_PROPERTIES:
            try:
                rig.get(name)
            except (AssertionError, RuntimeError) as error:
                errors[name] = str(error)
                assert TYPE + ": unknown profile" in str(error), str(error)
            else:
                raise AssertionError(("unknown runtime was readable", name))
    else:
        runtime = {name: rig.get(name) for name in RUNTIME}
        for i in range(8):
            ports.append({"port": i, "remaining_ns": rig.get(f"port{i}-remaining-ns"),
                          "deadline_ns": rig.get(f"port{i}-deadline-ns")})
    return {"geometry": geometry, "peer_stall_mask": stall, "runtime": runtime,
            "ports": ports, "runtime_errors": errors}


def expect_state(rig, requested, sense, pending, *, stall=None, mmio=False):
    assert pending == requested ^ sense
    assert rig.get("requested-mask") == requested
    assert rig.get("sense-mask") == sense
    assert rig.get("pending-mask") == pending
    if stall is not None:
        assert rig.get("reference-peer-stall-mask") == stall
    if mmio:
        assert rig.read(SENSE) == sense


def expect_port(rig, port, remaining, deadline):
    assert rig.get(f"port{port}-remaining-ns") == remaining, (port, remaining)
    assert rig.get(f"port{port}-deadline-ns") == deadline, (port, deadline)


def expect_idle_ports(rig):
    for i in range(8):
        expect_port(rig, i, 0, -1)


def expect_host_failure(rig, value, message):
    before = snapshot(rig, unknown=not rig.reference)
    try:
        rig.stall(value)
    except (AssertionError, RuntimeError) as error:
        assert TYPE + ": " + message in str(error), str(error)
    else:
        raise AssertionError(("invalid peer input accepted", value))
    assert snapshot(rig, unknown=not rig.reference) == before



def explicit_reference_cold_state_has_only_connected_ports_and_zero_diagnostics(rig):
    state = snapshot(rig)
    expect_state(rig, 0, 0, 0, stall=0, mmio=True)
    expect_idle_ports(rig)
    assert all(state["runtime"][name] == 0 for name in RUNTIME)
    assert rig.last_clock == 0
    # These are host diagnostics; no additional physical register bank exists.
    assert state["geometry"]["mapped-bytes"] == 12


def literal_driver_disconnect_and_reconnect_polls_observe_completed_transitions(rig):
    # msm_bus_noc_rpmh.c: enable -> W1CLR + wait while SENSE&bit;
    # disable -> W1SET + wait while !(SENSE&bit), with 10 us udelay.
    rig.write(CLEAR, 1)
    assert rig.read(SENSE) & 1 == 0  # cold connected can be an idempotent enable
    assert rig.get("pending-mask") == 0
    for port in (0, 3, 7):
        bit = 1 << port
        rig.write(SET, bit)
        assert rig.read(SENSE) & bit == 0
        assert rig.get("pending-mask") == bit
        rig.step(10000)
        assert rig.read(SENSE) & bit == bit
        rig.write(CLEAR, bit)
        assert rig.read(SENSE) & bit == bit
        assert rig.get("pending-mask") == bit
        rig.step(10000)
        assert rig.read(SENSE) & bit == 0
    expect_state(rig, 0, 0, 0, mmio=True)
    assert rig.get("accepted-set-writes") == 3
    assert rig.get("accepted-clear-writes") == 4


def all_eight_ports_have_independent_exact_deadlines(rig):
    for port in range(8):
        if port:
            rig.step(100)
        rig.write(SET, 1 << port)
        expect_port(rig, port, 1000, port * 100 + 1000)
    assert rig.last_clock == 700
    expect_state(rig, 255, 0, 255)
    rig.step(299)
    expect_state(rig, 255, 0, 255)
    for port in range(8):
        if port:
            rig.step(99)
            expect_state(rig, 255, (1 << port) - 1, 255 ^ ((1 << port) - 1))
        rig.step(1)
        completed = (1 << (port + 1)) - 1
        expect_state(rig, 255, completed, 255 ^ completed, mmio=True)
        expect_port(rig, port, 0, -1)
    expect_idle_ports(rig)


def word_commands_preserve_unselected_bits_and_count_accepted_zero_masks(rig):
    rig.write(SET, 0x81)
    rig.write(SET, 0x24)
    expect_state(rig, 0xa5, 0, 0xa5)
    rig.write(CLEAR, 0x20)
    expect_state(rig, 0x85, 0, 0x85)
    rig.write(SET, 0)
    rig.write(CLEAR, 0)
    assert rig.get("accepted-set-writes") == 3
    assert rig.get("accepted-clear-writes") == 2
    assert rig.get("last-set-mask") == rig.get("last-clear-mask") == 0
    rig.step(1000)
    expect_state(rig, 0x85, 0x85, 0, mmio=True)
    # Real byte order, not an invented extra word alias.
    assert rig.command(f"read {SENSE:#x} 4")[0].lower() == "0x85000000"
    rig.write(CLEAR, 0x80)
    rig.step(1000)
    expect_state(rig, 5, 5, 0, mmio=True)


def duplicate_requests_do_not_rearm_pending_or_completed_ports(rig):
    rig.write(SET, 3)
    rig.step(400)
    for _ in range(3):
        rig.write(SET, 1)
        expect_port(rig, 0, 600, 1000)
        expect_port(rig, 1, 600, 1000)
    rig.step(599)
    expect_state(rig, 3, 0, 3)
    rig.step(1)
    expect_state(rig, 3, 3, 0)
    rig.write(SET, 3)
    expect_state(rig, 3, 3, 0)
    expect_idle_ports(rig)
    assert rig.get("accepted-set-writes") == 5
    rig.write(CLEAR, 1)
    rig.step(700)
    rig.write(CLEAR, 1)
    expect_port(rig, 0, 300, 2000)
    rig.step(299)
    expect_state(rig, 2, 3, 1)
    rig.step(1)
    expect_state(rig, 2, 2, 0, mmio=True)


def reversing_to_current_sense_cancels_without_a_late_response(rig):
    rig.write(SET, 0x41)
    rig.step(321)
    rig.write(CLEAR, 1)
    expect_state(rig, 0x40, 0, 0x40)
    expect_port(rig, 0, 0, -1)
    expect_port(rig, 6, 679, 1000)
    rig.step(679)
    expect_state(rig, 0x40, 0x40, 0)
    rig.write(CLEAR, 0x40)
    rig.step(123)
    rig.write(SET, 0x40)
    expect_state(rig, 0x40, 0x40, 0)
    expect_idle_ports(rig)
    rig.step(2000)
    expect_state(rig, 0x40, 0x40, 0, mmio=True)


def stalled_before_request_keeps_the_full_phase_until_host_release(rig):
    rig.stall(0x81)
    rig.write(SET, 0x81)
    rig.step(100000)
    expect_state(rig, 0x81, 0, 0x81, stall=0x81, mmio=True)
    expect_port(rig, 0, 1000, -1)
    expect_port(rig, 7, 1000, -1)
    rig.stall(0)
    expect_state(rig, 0x81, 0, 0x81, stall=0)
    expect_port(rig, 0, 1000, 101000)
    expect_port(rig, 7, 1000, 101000)
    rig.step(999)
    expect_state(rig, 0x81, 0, 0x81)
    rig.step(1)
    expect_state(rig, 0x81, 0x81, 0, mmio=True)


def midflight_stall_preserves_remaining_phase_in_both_directions(rig):
    rig.write(SET, 4)
    rig.step(317)
    rig.stall(4)
    expect_port(rig, 2, 683, -1)
    rig.step(7000)
    rig.stall(4)
    expect_port(rig, 2, 683, -1)
    rig.stall(0)
    expect_port(rig, 2, 683, 8000)
    rig.step(682)
    expect_state(rig, 4, 0, 4)
    rig.step(1)
    expect_state(rig, 4, 4, 0)
    rig.write(CLEAR, 4)
    rig.step(222)
    rig.stall(4)
    rig.step(3000)
    expect_port(rig, 2, 778, -1)
    rig.stall(0)
    rig.step(777)
    expect_state(rig, 0, 4, 4)
    rig.step(1)
    expect_state(rig, 0, 0, 0, mmio=True)


def reversal_while_stalled_discards_the_saved_request_phase(rig):
    rig.stall(2)
    rig.write(SET, 2)
    rig.step(5000)
    rig.write(CLEAR, 2)
    expect_state(rig, 0, 0, 0, stall=2)
    expect_port(rig, 1, 0, -1)
    rig.stall(0)
    rig.step(1000)
    expect_state(rig, 0, 0, 0)
    rig.write(SET, 2)
    rig.step(1000)
    rig.stall(2)
    rig.write(CLEAR, 2)
    rig.step(3000)
    rig.write(SET, 2)
    expect_state(rig, 2, 2, 0, stall=2)
    expect_idle_ports(rig)
    rig.stall(0)
    rig.step(1000)
    expect_state(rig, 2, 2, 0, mmio=True)


def unrelated_callbacks_never_finish_a_stalled_zero_remaining_port(rig):
    rig.write(SET, 1)
    rig.step(100)
    rig.write(SET, 2)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    # qtest clock_set advances the test clock while paused timer lists cannot
    # dispatch: util/qemu-timer.c timerlist_run_timers checks clock->enabled.
    rig.set_clock(1000)
    expect_state(rig, 3, 0, 3)
    expect_port(rig, 0, 0, 1000)
    expect_port(rig, 1, 100, 1100)
    rig.stall(1)
    expect_port(rig, 0, 0, -1)
    expect_port(rig, 1, 100, 1100)  # changing another bit must not rearm it
    rig.monitor("cont")
    rig.step(100)
    expect_state(rig, 3, 2, 1, stall=1, mmio=True)
    expect_port(rig, 0, 0, -1)
    rig.step(10000)
    expect_state(rig, 3, 2, 1)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    rig.stall(0)
    expect_port(rig, 0, 0, rig.last_clock)
    expect_state(rig, 3, 2, 1)
    # The input setter schedules a zero remainder but may not acknowledge it.
    rig.monitor("cont")
    rig.step(1)
    expect_state(rig, 3, 3, 0, mmio=True)


def reserved_port_bits_fail_whole_commands_and_host_input_atomically(rig):
    rig.write(SET, 0x81)
    rig.step(123)
    before = snapshot(rig)
    for address in (SET, CLEAR):
        for value in (0x100, 0x101, 0x80000080, 0xffffffff):
            old_log = rig.log_path.read_text(errors="replace")
            rig.write(address, value)
            new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert device_diagnostics(new_log) == [
                diagnostic(address, "unsupported port bits", value=value)], new_log
            assert not core_diagnostics(new_log), new_log
            assert snapshot(rig) == before
    expect_host_failure(rig, 0x100, "reference-peer-stall-mask supports only ports 0..7")
    expect_host_failure(rig, 0xffffffff,
                        "reference-peer-stall-mask supports only ports 0..7")
    rig.step(876)
    expect_state(rig, 0x81, 0, 0x81)
    rig.step(1)
    expect_state(rig, 0x81, 0x81, 0)


def register_permissions_and_readonly_qom_cannot_forge_handshake_state(rig):
    rig.write(SET, 0xa5)
    rig.step(137)
    before = snapshot(rig)
    for address in (CLEAR, SET):
        old_log = rig.log_path.read_text(errors="replace")
        rig.read(address)
        new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
        assert device_diagnostics(new_log) == [
            diagnostic(address, "write-only register", write=False)], new_log
        assert snapshot(rig) == before
    for value in (0, 0xff, 0xffffffff):
        old_log = rig.log_path.read_text(errors="replace")
        rig.write(SENSE, value)
        new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
        assert device_diagnostics(new_log) == [
            diagnostic(SENSE, "read-only register", value=value)], new_log
        assert snapshot(rig) == before
    for name, value in (("sense-mask", 255), ("requested-mask", 0),
                        ("port0-remaining-ns", 0), ("port7-deadline-ns", 0),
                        ("accepted-set-writes", 900), ("clear-address", 0),
                        ("mmio-enabled", False), ("enabled-reference", False)):
        try:
            rig.put(name, value)
        except (AssertionError, RuntimeError) as error:
            if name == "enabled-reference":
                # hw/core/qdev-properties.c qdev_prop_set_after_realize.
                assert "Attempt to set property 'enabled-reference'" in str(error)
                assert TYPE in str(error) and "after it was realized" in str(error)
            else:
                assert f"Property '{TYPE}.{name}' is not writable" in str(error), str(error)
        else:
            raise AssertionError(("readonly property accepted", name))
        assert snapshot(rig) == before


def invalid_widths_and_unaligned_requests_do_not_undo_valid_wide_word_transactions(rig):
    rig.write(SET, 0xa5)
    rig.step(1000)
    before = snapshot(rig)
    size_reason = "invalid size (min:4 max:4)"
    for base in ADDRESSES:
        region = TYPE + "-" + REGIONS[base]
        for width, delta, pieces in (
                ("b", 0, [(0, 1, region, size_reason)]),
                ("w", 0, [(0, 2, region, size_reason)]),
                ("w", 1, [(1, 1, region, size_reason), (2, 1, region, size_reason)]),
                ("l", 1, [(1, 1, region, size_reason), (2, 2, region, size_reason),
                          (base + 4, 1, "(null)", "rejected")]),
                ("l", 2, [(2, 2, region, size_reason),
                          (base + 4, 2, "(null)", "rejected")])):
            for write in (False, True):
                old_log = rig.log_path.read_text(errors="replace")
                if write:
                    rig.write(base + delta, 0, width)
                else:
                    rig.read(base + delta, width)
                new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
                operation = "write" if write else "read"
                assert core_diagnostics(new_log) == [
                    (operation, address, size, name, reason)
                    for address, size, name, reason in pieces], new_log
                assert not device_diagnostics(new_log), new_log
                assert snapshot(rig) == before
    # address_space_write splits an eight-byte request at the four-byte
    # region boundary: the low word can commit before the high hole fails.
    old_log = rig.log_path.read_text(errors="replace")
    rig.write(SET, (0x12345678 << 32) | 0x02, "q")
    new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
    assert core_diagnostics(new_log) == [("write", SET + 4, 4, "(null)", "rejected")]
    assert not device_diagnostics(new_log), new_log
    expect_state(rig, 0xa7, 0xa5, 2)
    assert rig.get("accepted-set-writes") == before["runtime"]["accepted-set-writes"] + 1
    old_log = rig.log_path.read_text(errors="replace")
    rig.read(SENSE, "q")
    new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
    assert core_diagnostics(new_log) == [("read", SENSE + 4, 4, "(null)", "rejected")]
    rig.step(1000)
    expect_state(rig, 0xa7, 0xa7, 0, mmio=True)


def paused_virtual_time_and_reads_do_not_acknowledge_due_requests(rig):
    rig.write(SET, 0x80)
    rig.step(400)
    rig.monitor("stop")
    status = rig.monitor("query-status")
    assert status["running"] is False
    before = snapshot(rig)
    for _ in range(8):
        assert rig.read(SENSE) == 0
        assert snapshot(rig) == before
    rig.set_clock(2000)
    for _ in range(3):
        expect_state(rig, 0x80, 0, 0x80, mmio=True)
        expect_port(rig, 7, 0, 1000)
    assert rig.monitor("query-status")["running"] is False
    rig.monitor("cont")
    rig.step(1)
    expect_state(rig, 0x80, 0x80, 0, mmio=True)
    expect_idle_ports(rig)


def system_reset_cancels_requests_and_counters_but_preserves_the_host_stall_input(rig):
    rig.write(SET, 0xff)
    rig.step(1000)
    rig.stall(0x15)
    rig.write(CLEAR, 0x55)
    rig.step(400)
    expect_state(rig, 0xaa, 0xff, 0x55, stall=0x15)
    rig.write(GCC + 4, 0x35)
    assert rig.read(GCC + 4) == 0x35
    rig.events.clear()
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.read(GCC + 4) == 0, "independent GCC reset witness")
    rig.monitor("query-status")  # drain an asynchronous RESET after its response
    assert any(event.get("event") == "RESET" for event in rig.events)
    expect_state(rig, 0, 0, 0, stall=0x15, mmio=True)
    expect_idle_ports(rig)
    state = snapshot(rig)
    assert all(state["runtime"][name] == 0 for name in RUNTIME)
    rig.step(10000)
    assert snapshot(rig)["runtime"] == state["runtime"]
    rig.write(SET, 3)
    rig.step(1000)
    expect_state(rig, 3, 2, 1, stall=0x15)
    rig.stall(0)
    rig.step(1000)
    expect_state(rig, 3, 3, 0, mmio=True)


def deadline_overflow_rejects_multibit_commands_and_unstall_without_partial_changes(rig):
    rig.write(SET, 1)
    rig.step(1000)
    rig.stall(1)
    rig.write(CLEAR, 1)
    expect_state(rig, 0, 1, 1, stall=1)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    rig.set_clock(INT64_MAX - 999)
    before = snapshot(rig)
    old_log = rig.log_path.read_text(errors="replace")
    # Bit0 would cancel a stalled reconnect, but bit1 needs an overflowing
    # new deadline: rejection must preserve the entire original transaction.
    rig.write(SET, 3)
    new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
    assert device_diagnostics(new_log) == [
        diagnostic(SET, "virtual deadline overflow", value=3)], new_log
    assert snapshot(rig) == before
    expect_host_failure(rig, 0, "peer stall change would overflow the virtual deadline")
    # A cancellation requires no new deadline and remains a legal command.
    rig.write(SET, 1)
    expect_state(rig, 1, 1, 0, stall=1)
    expect_idle_ports(rig)
    rig.write(CLEAR, 0)
    rig.write(SET, 0)
    expect_state(rig, 1, 1, 0)



def sbm_windows(rig):
    text = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (rig.directory / "mtree.txt").write_text(text)
    names = "|".join(re.escape(TYPE + "-" + name) for name in REGIONS.values())
    pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + names + r")(?:\s|$)"
    windows = set()
    for line in text.splitlines():
        match = re.search(pattern, line)
        if match:
            first, last, name = match.groups()
            windows.add((int(first, 16), int(last, 16), name))
    return windows


def default_unknown_is_instantiated_but_keeps_all_sbm_words_unmapped(args, out):
    rig = SbmRig(args, out / "default-unknown", reference=None)
    try:
        state = snapshot(rig, unknown=True)
        assert len(state["runtime_errors"]) == 23
        assert sbm_windows(rig) == set()
        for address in sorted(set(ADDRESSES + HOLES)):
            old_log = rig.log_path.read_text(errors="replace")
            rig.write(address, 0x12345678)
            rig.read(address)
            new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(new_log) == [
                ("write", address, 4, "(null)", "rejected"),
                ("read", address, 4, "(null)", "rejected")], new_log
            assert not device_diagnostics(new_log), new_log
        for value in (0, 1):
            expect_host_failure(rig, value,
                                "peer stall input requires a realized reference profile")
        assert snapshot(rig, unknown=True) == state
        (rig.directory / "unknown-contract.json").write_text(json.dumps({
            "device_exists": True, "memory_regions_enabled": False,
            "geometry": state["geometry"], "runtime_errors": state["runtime_errors"],
            "addresses_that_remain_holes": sorted(set(ADDRESSES + HOLES)),
            "old_qos_sbm_hole_contract_preserved": True}, indent=2) + "\n")
    finally:
        rig.close()


def enabled_profile_maps_only_three_low_words_and_keeps_status_and_high_banks_holes(args, out):
    rig = SbmRig(args, out / "sparse-low-bank-layout")
    try:
        expected = {(address, address + 3, TYPE + "-" + REGIONS[address])
                    for address in ADDRESSES}
        assert sbm_windows(rig) == expected
        assert len(expected) == 3 and sum(last - first + 1 for first, last, _ in expected) == 12
        rig.write(SET, 0xa5)
        rig.step(137)
        before = snapshot(rig)
        for address in HOLES:
            old_log = rig.log_path.read_text(errors="replace")
            rig.write(address, 0x12345678)
            rig.read(address)
            new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(new_log) == [
                ("write", address, 4, "(null)", "rejected"),
                ("read", address, 4, "(null)", "rejected")], new_log
            assert not device_diagnostics(new_log), new_log
            assert snapshot(rig) == before
        (rig.directory / "expected-layout.json").write_text(json.dumps({
            "windows": sorted(expected), "holes": list(HOLES),
            "physical_port_mask": 255, "mapped_bytes": 12,
            "status_registers_implemented": False,
            "high_bank_implemented": False}, indent=2) + "\n")
    finally:
        rig.close()


def paused_normal_tcg_exposes_the_host_peer_input_without_running_a_guest(args, out):
    rig = SbmRig(args, out / "paused-tcg", accel="tcg")
    try:
        before_status = rig.monitor("query-status")
        assert before_status["running"] is False and rig.cont_commands == 0
        rig.stall(0x80)
        rig.write(SET, 0x80)
        expect_state(rig, 0x80, 0, 0x80, stall=0x80, mmio=True)
        expect_port(rig, 7, 1000, -1)
        rig.stall(0)
        before = snapshot(rig)
        for _ in range(4):
            assert rig.read(SENSE) == 0
            assert snapshot(rig) == before
        after_status = rig.monitor("query-status")
        assert after_status["running"] is False and rig.cont_commands == 0
        (rig.directory / "paused-tcg.json").write_text(json.dumps({
            "status_before": before_status, "status_after": after_status,
            "device": before, "normal_environment_input_available": True,
            "qtest_only_peer_input": False, "qmp_cont_sent": False,
            "guest_cpu_executed": False}, indent=2) + "\n")
    finally:
        rig.close()


def sbm_requests_are_isolated_from_all_forty_seven_existing_qos_devices(args, out):
    # Import the existing independent physical-port oracle without editing it.
    # Its own gate continues to exercise its original 20/2/2/2 contract.
    qos_test_path = Path(__file__).with_name("test_noc_qos.py")
    assert digest(qos_test_path) == OLD_QOS_TEST_SHA256
    assert len(QOS_PORTS) == 47
    rig = SbmRig(args, out / "sbm-and-qos-coexistence", qos_reference=True)
    expected = qos_values()

    def verify_qos():
        for item in QOS_PORTS:
            for offset, name, value in zip(QOS_OFFSETS, QOS_NAMES, expected[item[2]]):
                assert rig.read(item[2] + offset) == value, (item, name)
                assert rig.monitor("qom-get", {
                    "path": qos_path(item), "property": name}) == value, (item, name)

    try:
        for item in QOS_PORTS:
            for offset, value in zip(QOS_OFFSETS, expected[item[2]]):
                rig.write(item[2] + offset, value)
        verify_qos()
        rig.write(SET, 0xff)
        rig.step(1000)
        rig.stall(5)
        rig.write(CLEAR, 0x55)
        rig.step(1000)
        expect_state(rig, 0xaa, 0xaf, 5, stall=5, mmio=True)
        verify_qos()
        rig.stall(0)
        rig.step(1000)
        expect_state(rig, 0xaa, 0xaa, 0, mmio=True)
        verify_qos()
        (rig.directory / "qos-isolation.json").write_text(json.dumps({
            "old_qos_test_sha256": digest(qos_test_path),
            "port_count": 47, "register_count": 188,
            "all_existing_words_preserved": True,
            "old_qos_vmstate_version_changed": False,
            "old_qos_gate_checks_removed": False}, indent=2) + "\n")
    finally:
        rig.close()


ORDINARY_TESTS = [
    explicit_reference_cold_state_has_only_connected_ports_and_zero_diagnostics,
    literal_driver_disconnect_and_reconnect_polls_observe_completed_transitions,
    all_eight_ports_have_independent_exact_deadlines,
    word_commands_preserve_unselected_bits_and_count_accepted_zero_masks,
    duplicate_requests_do_not_rearm_pending_or_completed_ports,
    reversing_to_current_sense_cancels_without_a_late_response,
    stalled_before_request_keeps_the_full_phase_until_host_release,
    midflight_stall_preserves_remaining_phase_in_both_directions,
    reversal_while_stalled_discards_the_saved_request_phase,
    unrelated_callbacks_never_finish_a_stalled_zero_remaining_port,
    reserved_port_bits_fail_whole_commands_and_host_input_atomically,
    register_permissions_and_readonly_qom_cannot_forge_handshake_state,
    invalid_widths_and_unaligned_requests_do_not_undo_valid_wide_word_transactions,
    paused_virtual_time_and_reads_do_not_acknowledge_due_requests,
    system_reset_cancels_requests_and_counters_but_preserves_the_host_stall_input,
    deadline_overflow_rejects_multibit_commands_and_unstall_without_partial_changes,
]
SPECIAL_TESTS = [
    default_unknown_is_instantiated_but_keeps_all_sbm_words_unmapped,
    enabled_profile_maps_only_three_low_words_and_keeps_status_and_high_banks_holes,
    paused_normal_tcg_exposes_the_host_peer_input_without_running_a_guest,
    sbm_requests_are_isolated_from_all_forty_seven_existing_qos_devices,
]


SERIAL_FIELDS = (
    ("profile", 0, 4), ("instance_id", 4, 4),
    ("clear_address", 8, 8), ("set_address", 16, 8), ("sense_address", 24, 8),
    ("supported_port_mask", 32, 4), ("reference_latency_ns", 36, 4),
    ("reference_identity", 40, 4), ("boot_disconnected_mask", 44, 4),
    ("peer_stall_mask", 48, 4), ("requested_mask", 52, 4),
    ("sense_mask", 56, 4), ("pending_mask", 60, 4),
) + tuple((f"port{i}_remaining_ns", 64 + i * 8, 8) for i in range(8)) + (
    ("accepted_set_writes", 128, 8), ("accepted_clear_writes", 136, 8),
    ("last_set_mask", 144, 4), ("last_clear_mask", 148, 4))


def packed_device(state, *, reference):
    geometry, runtime = state["geometry"], state["runtime"]
    assert {name: geometry[name] for name in GEOMETRY} == GEOMETRY
    assert geometry["enabled-reference"] is reference
    assert geometry["mmio-enabled"] is reference
    prefix = struct.pack(">II3Q4I", int(reference), 0, CLEAR, SET, SENSE,
                         255, 1000, REFERENCE_ID, 0)
    assert len(prefix) == 48
    if reference:
        assert len(state["ports"]) == 8
        remaining = [port["remaining_ns"] for port in state["ports"]]
        assert all(0 <= value <= 1000 for value in remaining)
        requested, sense, pending = (runtime[name] for name in
                                    ("requested-mask", "sense-mask", "pending-mask"))
        assert pending == requested ^ sense
        tail = struct.pack(
            ">4I8Q2Q2I", state["peer_stall_mask"], requested, sense, pending,
            *remaining, runtime["accepted-set-writes"], runtime["accepted-clear-writes"],
            runtime["last-set-mask"], runtime["last-clear-mask"])
    else:
        assert state["peer_stall_mask"] == 0
        assert not runtime and not state["ports"] and len(state["runtime_errors"]) == 23
        # Off runtime is explicitly unreadable. This is the validated disabled
        # serialization invariant, not a fabricated successful QOM read.
        tail = bytes(104)
    result = prefix + tail
    assert len(result) == PAYLOAD_BYTES == 152
    return result


def actual_section(path, source_state, *, reference):
    data = path.read_bytes()
    name = TYPE.encode("ascii")
    needle = bytes([len(name)]) + name + struct.pack(">II", 0, 1)
    expected = packed_device(source_state, reference=reference)
    matches, position = [], 0
    while True:
        index = data.find(needle, position)
        if index < 0:
            break
        header, payload = index - 5, index + len(needle)
        if header >= 8 and data[header] in (1, 4):
            matches.append((header, payload))
        position = index + 1
    assert len(matches) == 1, matches
    header, payload = matches[0]
    actual = data[payload:payload + PAYLOAD_BYTES]
    # Validate every serialized byte, including pre_save's relative remainder,
    # against the final stopped source observations before modifying any stream.
    assert len(actual) == PAYLOAD_BYTES and actual == expected, (actual.hex(), expected.hex())
    decoded = {name: int.from_bytes(actual[offset:offset + width], "big")
               for name, offset, width in SERIAL_FIELDS}
    assert decoded["profile"] == int(reference)
    payload_path = path.with_name(path.name + ".instance0.payload.bin")
    payload_path.write_bytes(actual)
    section = {
        "instance_id": 0, "section_version": 1, "section_name": TYPE,
        "section_header_offset": header,
        "section_id": int.from_bytes(data[header + 1:header + 5], "big"),
        "payload_offset": payload, "payload_bytes": PAYLOAD_BYTES,
        "payload_hex": actual.hex(), "payload_file": payload_path.name,
        "decoded_fields": decoded, "all_serialized_fields_verified": True,
        "runtime_matches_actual_source_qom": reference,
        "unknown_runtime_zero_invariant_verified": not reference}
    path.with_name(path.name + ".sections.json").write_text(json.dumps({
        "stream_sha256": digest(path), "stream_bytes": len(data),
        "field_layout": [{"name": name, "offset": offset, "bytes": width}
                         for name, offset, width in SERIAL_FIELDS],
        "sections": [section]}, indent=2) + "\n")
    return section


def save_real_state(source, path, witness):
    source.write(RAM_WITNESS, witness)
    source.monitor("stop")
    status = source.monitor("query-status")
    assert status["running"] is False
    before = snapshot(source, unknown=not source.reference)
    assert snapshot(source, unknown=not source.reference) == before
    before_file = source.directory / "source-qom-before-save.json"
    before_file.write_text(json.dumps({
        "query_status": status, "source_virtual_ns": source.last_clock,
        "device": before, "repeated_qom_observation_unchanged": True}, indent=2) + "\n")
    source.monitor("migrate", file_channels(path))
    sent = wait_migration(source)
    assert sent["status"] == "completed", sent
    final_status = source.monitor("query-status")
    assert final_status["status"] == "postmigrate" and final_status["running"] is False
    assert path.stat().st_size > 0
    after = snapshot(source, unknown=not source.reference)
    # pre_save projects remaining time but must not complete a due handshake.
    assert after == before
    after_file = source.directory / "source-qom-after-save.json"
    after_file.write_text(json.dumps({
        "query_status": final_status, "source_virtual_ns": source.last_clock,
        "device": after}, indent=2) + "\n")
    (source.directory / "source-query-migrate.json").write_text(json.dumps(sent, indent=2) + "\n")
    section = actual_section(path, after, reference=source.reference)
    evidence = {
        "source_virtual_ns": source.last_clock,
        "source_qom_and_pre_save_did_not_acknowledge_requests": True,
        "source_qom_before_file": source.directory.name + "/" + before_file.name,
        "source_qom_after_file": source.directory.name + "/" + after_file.name,
        "source_query_migrate_file": source.directory.name + "/source-query-migrate.json"}
    return after, sent, section, evidence


def prepare_target(target, destination_ns):
    assert target.monitor("query-status")["running"] is False
    target.set_clock(destination_ns)
    target.stall(0x80)
    target.write(SET, 0x80)
    expect_state(target, 0x80, 0, 0x80, stall=0x80)
    expect_port(target, 7, 1000, -1)
    target.write(RAM_WITNESS, 0xdeadbeef)
    state = snapshot(target)
    (target.directory / "destination-before-load.json").write_text(json.dumps({
        "destination_virtual_ns": destination_ns,
        "query_status": target.monitor("query-status"), "device": state}, indent=2) + "\n")
    return state


def expected_rebased_state(source_state, destination_ns):
    state = json.loads(json.dumps(source_state))
    pending, stalled = state["runtime"]["pending-mask"], state["peer_stall_mask"]
    for port in state["ports"]:
        bit = 1 << port["port"]
        port["deadline_ns"] = (destination_ns + port["remaining_ns"]
                               if pending & bit and not stalled & bit else -1)
    return state


def restore_real_state(target, path, source_state, destination_ns, witness):
    assert target.last_clock == destination_ns
    old_mmio, old_cont = target.sbm_mmio_operations, target.cont_commands
    target.monitor("migrate-incoming", file_channels(path))
    got = wait_migration(target)
    assert got["status"] == "completed", got
    status = target.monitor("query-status")
    assert status["running"] is False
    # These getters only observe. In particular, a pending zero remainder may
    # not acknowledge until the virtual timer is actually enabled/dispatched.
    restored = snapshot(target)
    assert restored == expected_rebased_state(source_state, destination_ns)
    assert target.sbm_mmio_operations == old_mmio and target.cont_commands == old_cont
    assert target.read(RAM_WITNESS) == witness
    assert target.monitor("query-status")["running"] is False
    (target.directory / "destination-before-first-sbm-mmio.json").write_text(json.dumps({
        "destination_virtual_ns": destination_ns, "query_status": status,
        "device": restored, "ram_witness": witness,
        "sbm_mmio_since_incoming": False, "qmp_cont_since_incoming": False,
        "relative_deadlines_rebased": True}, indent=2) + "\n")
    (target.directory / "destination-query-migrate.json").write_text(json.dumps(got, indent=2) + "\n")
    return got


def expect_incoming_failure(target, path, diagnostic):
    assert target.process.poll() is None
    requested, command_error = False, None
    try:
        requested = True
        target.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as error:
        command_error = str(error)
    code = target.process.wait(timeout=20)
    stderr = target.log_path.read_text(errors="replace")
    assert requested and code == 1, (requested, code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {"destination_exit": code, "request_sent": requested,
            "command_error": command_error, "device": TYPE,
            "expected_diagnostic": diagnostic, "specific_load_failure_observed": True}


def corrupt_single_field(original, changed_path, section, kind):
    data = original.read_bytes()
    start = section["payload_offset"]
    assert data[start:start + PAYLOAD_BYTES].hex() == section["payload_hex"]
    if kind == "reference-identity":
        relative, width = 40, 4
        old = int.from_bytes(data[start + relative:start + relative + width], "big")
        assert old == REFERENCE_ID
        new = old ^ 1
    elif kind == "port1-remaining":
        relative, width = 72, 8
        old = int.from_bytes(data[start + relative:start + relative + width], "big")
        assert old == 100
        new = old | 1024
    else:
        raise AssertionError(kind)
    field = start + relative
    changed = bytearray(data)
    changed[field:field + width] = new.to_bytes(width, "big")
    assert changed[:field] == data[:field] and changed[field + width:] == data[field + width:]
    differences = [{"offset": i, "old_byte": before, "new_byte": after}
                   for i, (before, after) in enumerate(zip(data, changed)) if before != after]
    assert len(differences) == 1 and field <= differences[0]["offset"] < field + width
    assert (old ^ new).bit_count() == 1
    changed_path.write_bytes(changed)
    assert original.read_bytes() == data and len(changed) == len(data)
    return {"instance_id": 0, "field": kind,
            "section_header_offset": section["section_header_offset"],
            "payload_offset": start, "field_relative_offset": relative,
            "field_offset": field, "modified_field_bytes": width,
            "old_value": old, "new_value": new,
            "old_field_hex": data[field:field + width].hex(),
            "new_field_hex": bytes(changed[field:field + width]).hex(),
            "original_payload_hex": section["payload_hex"],
            "modified_payload_hex": bytes(changed[start:start + PAYLOAD_BYTES]).hex(),
            "complete_file_byte_differences": differences,
            "only_one_field_modified": True, "only_one_bit_modified": True}



def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = SbmRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, source, target, sent, got, section, evidence, destination_ns):
        record = {
            "name": name, "passed": True,
            "source_status": sent["status"], "target_status": got["status"],
            "source": sent, "destination": got,
            "source_directory": source.directory.name, "target_directory": target.directory.name,
            "destination_virtual_ns": destination_ns,
            "different_virtual_origins": source.last_clock != destination_ns,
            "snapshot_file": path.name, "snapshot_sha256": digest(path),
            "snapshot_bytes": path.stat().st_size, "stream_modified": False,
            "device_sections_verified": 1, "payload_bytes_per_device": PAYLOAD_BYTES,
            "sections": [section],
            "target_sbm_mmio_before_first_observation": False,
            "target_qmp_cont_before_first_observation": False,
            "destination_state_was_different_before_load": True,
            "target_qom_before_first_mmio_file": target.directory.name +
                                                "/destination-before-first-sbm-mmio.json",
            "target_query_migrate_file": target.directory.name + "/destination-query-migrate.json",
            "continued_after_restore": False, **evidence}
        report["migrations"].append(record)
        return record

    try:
        source = new("migration-deadlines-source")
        for port in range(8):
            if port:
                source.step(100)
            source.write(SET, 1 << port)
        source.step(50)
        assert source.last_clock == 750
        source.monitor("stop")
        assert source.monitor("query-status")["running"] is False
        source.set_clock(1000)
        # Paused QTest clock_set advances the counter without timer dispatch.
        # This is a due, still-pending port, not an already acknowledged one.
        expect_state(source, 255, 0, 255, stall=0)
        for port in range(8):
            expect_port(source, port, port * 100, 1000 + port * 100)
        first_path = out / "noc-sbm-v1-eight-relative-deadlines.migration"
        state, sent, section, evidence = save_real_state(source, first_path, 0x53424d31)
        assert state["runtime"] == {
            "requested-mask": 255, "sense-mask": 0, "pending-mask": 255,
            "accepted-set-writes": 8, "accepted-clear-writes": 0,
            "last-set-mask": 128, "last-clear-mask": 0}
        assert state["peer_stall_mask"] == 0
        assert [p["remaining_ns"] for p in state["ports"]] == list(range(0, 800, 100))

        target = new("migration-deadlines-target", incoming=True)
        destination_ns = 987654321
        old = prepare_target(target, destination_ns)
        assert old["runtime"] != state["runtime"]
        assert old["peer_stall_mask"] != state["peer_stall_mask"]
        got = restore_real_state(target, first_path, state, destination_ns, 0x53424d31)
        first = completed("v1_eight_independent_relative_deadlines_and_ram",
                          first_path, source, target, sent, got, section, evidence, destination_ns)
        first["source_had_pending_zero_remaining_port"] = True
        check("v1_relative_deadlines_and_zero_remainder_restore_before_first_sbm_mmio",
              source_virtual_ns=1000, destination_virtual_ns=destination_ns)

        mmio_before, observations = target.sbm_mmio_operations, []
        target.monitor("cont")
        target.step(1)
        expect_state(target, 255, 1, 254)
        expect_port(target, 0, 0, -1)
        for port in range(1, 8):
            expect_port(target, port, port * 100 - 1, destination_ns + port * 100)
        observations.append({"elapsed_ns": 1, "device": snapshot(target)})
        for port in range(1, 8):
            # The first loop advances from elapsed1 to99; later loops start at
            # the previous exact100ns boundary and advance another99ns.
            target.step(98 if port == 1 else 99)
            expected = (1 << port) - 1
            expect_state(target, 255, expected, 255 ^ expected)
            expect_port(target, port, 1, destination_ns + port * 100)
            observations.append({"elapsed_ns": port * 100 - 1, "device": snapshot(target)})
            target.step(1)
            expected = (1 << (port + 1)) - 1
            expect_state(target, 255, expected, 255 ^ expected)
            expect_port(target, port, 0, -1)
            observations.append({"elapsed_ns": port * 100, "device": snapshot(target)})
        expect_idle_ports(target)
        assert target.sbm_mmio_operations == mmio_before
        phase_file = target.directory / "destination-restored-timers-before-first-sbm-mmio.json"
        phase_file.write_text(json.dumps({
            "no_sbm_mmio_since_incoming": True, "observations": observations,
            "eight_independent_deadlines": True, "qemu_timer_objects": 1}, indent=2) + "\n")
        # Only after all restored timers have independently fired is device
        # MMIO allowed, so an MMIO-triggered reschedule cannot repair the test.
        assert target.read(SENSE) == 255
        target.write(CLEAR, 0x81)
        target.step(999)
        expect_state(target, 0x7e, 0xff, 0x81)
        target.step(1)
        expect_state(target, 0x7e, 0x7e, 0, mmio=True)
        first["continued_after_restore"] = True
        first["restored_timer_observations_file"] = target.directory.name + "/" + phase_file.name
        first["restored_deadlines_verified_before_any_sbm_mmio"] = True
        check("restored_eight_deadlines_dispatch_independently_and_accept_new_commands")
        first_digest = digest(first_path)
        source.close()
        target.close()

        source2 = new("migration-stalled-source")
        source2.write(SET, 255)
        source2.step(1000)
        source2.write(CLEAR, 0x55)
        source2.step(400)
        source2.stall(5)
        source2.write(SET, 4)  # cancel the stalled port2 reconnect
        source2.step(100)
        assert source2.last_clock == 1500
        expect_state(source2, 0xae, 0xff, 0x51, stall=5)
        for port, remaining in enumerate((600, 0, 0, 0, 500, 0, 500, 0)):
            expect_port(source2, port, remaining, 2000 if port in (4, 6) else -1)
        second_path = out / "noc-sbm-v1-stalled-reconnect.migration"
        state2, sent2, section2, evidence2 = save_real_state(source2, second_path, 0x53424d32)
        assert state2["runtime"] == {
            "requested-mask": 174, "sense-mask": 255, "pending-mask": 81,
            "accepted-set-writes": 2, "accepted-clear-writes": 1,
            "last-set-mask": 4, "last-clear-mask": 85}
        assert state2["peer_stall_mask"] == 5

        target2 = new("migration-stalled-target", incoming=True)
        destination_ns2 = 73
        old2 = prepare_target(target2, destination_ns2)
        assert old2["runtime"] != state2["runtime"]
        assert old2["peer_stall_mask"] != state2["peer_stall_mask"]
        got2 = restore_real_state(target2, second_path, state2, destination_ns2, 0x53424d32)
        second = completed("v1_stalled_reconnect_and_environment_input",
                           second_path, source2, target2, sent2, got2, section2, evidence2,
                           destination_ns2)
        check("v1_stalled_and_active_reconnects_restore_before_first_sbm_mmio",
              source_virtual_ns=1500, destination_virtual_ns=destination_ns2)

        mmio_before2, observations2 = target2.sbm_mmio_operations, []
        target2.monitor("cont")
        target2.step(499)
        expect_state(target2, 0xae, 0xff, 0x51, stall=5)
        expect_port(target2, 0, 600, -1)
        expect_port(target2, 4, 1, 573)
        expect_port(target2, 6, 1, 573)
        observations2.append({"elapsed_ns": 499, "device": snapshot(target2)})
        target2.step(1)
        expect_state(target2, 0xae, 0xaf, 1, stall=5)
        expect_port(target2, 0, 600, -1)
        observations2.append({"elapsed_ns": 500, "device": snapshot(target2)})
        target2.stall(4)  # resume only port0; retain the independent idle-port input
        expect_state(target2, 0xae, 0xaf, 1, stall=4)
        expect_port(target2, 0, 600, 1173)
        target2.step(599)
        expect_state(target2, 0xae, 0xaf, 1, stall=4)
        expect_port(target2, 0, 1, 1173)
        observations2.append({"elapsed_ns": 1099, "device": snapshot(target2)})
        target2.step(1)
        expect_state(target2, 0xae, 0xae, 0, stall=4)
        expect_idle_ports(target2)
        observations2.append({"elapsed_ns": 1100, "device": snapshot(target2)})
        assert target2.sbm_mmio_operations == mmio_before2
        phase_file2 = target2.directory / "destination-restored-timers-before-first-sbm-mmio.json"
        phase_file2.write_text(json.dumps({
            "no_sbm_mmio_since_incoming": True, "observations": observations2,
            "active_callbacks_did_not_finish_stalled_port": True,
            "stalled_remainder_resumed_after_host_release": True}, indent=2) + "\n")
        assert target2.read(SENSE) == 0xae
        target2.write(CLEAR, 4)
        expect_state(target2, 0xaa, 0xae, 4, stall=4)
        expect_port(target2, 2, 1000, -1)
        target2.write(GCC + 4, 0x36)
        assert target2.read(GCC + 4) == 0x36
        target2.events.clear()
        target2.monitor("system_reset")
        target2.wait_for(lambda: target2.read(GCC + 4) == 0, "restored GCC reset witness")
        target2.monitor("query-status")
        assert any(event.get("event") == "RESET" for event in target2.events)
        expect_state(target2, 0, 0, 0, stall=4, mmio=True)
        expect_idle_ports(target2)
        assert all(snapshot(target2)["runtime"][name] == 0 for name in RUNTIME)
        target2.step(10000)
        expect_state(target2, 0, 0, 0, stall=4, mmio=True)
        second["continued_after_restore"] = True
        second["restored_timer_observations_file"] = target2.directory.name + "/" + phase_file2.name
        second["restored_deadlines_verified_before_any_sbm_mmio"] = True
        second["system_reset_preserved_environment_input"] = True
        check("restored_stall_phase_resumes_exactly_and_system_reset_cancels_the_next_request")
        source2.close()
        target2.close()

        unknown = new("migration-unknown-source", reference=False)
        unknown.step(4567)
        unknown_path = out / "noc-sbm-v1-unknown-profile.migration"
        unknown_state, unknown_sent, unknown_section, unknown_evidence = save_real_state(
            unknown, unknown_path, 0x53424d30)
        assert unknown_section["unknown_runtime_zero_invariant_verified"]
        assert len(unknown_state["runtime_errors"]) == 23
        unknown.close()
        # The third source file is a negative-fixture source, not a third
        # completed source-to-destination roundtrip.
        for label, original, original_source, target_reference, diagnostic_text, sections, source_evidence in (
                ("reference-to-unknown", first_path, sent, False, "0 != 1", [section], evidence),
                ("unknown-to-reference", unknown_path, unknown_sent, True, "1 != 0",
                 [unknown_section], unknown_evidence)):
            incoming = new("rejected-" + label, incoming=True, reference=target_reference)
            original_hash = digest(original)
            failed = expect_incoming_failure(incoming, original, diagnostic_text)
            assert digest(original) == original_hash
            report["rejected_migrations"].append({
                "name": label, "passed": True, "profile_mismatch": True,
                "source_status": original_source["status"], "source": original_source,
                "snapshot_file": original.name, "snapshot_sha256": original_hash,
                "snapshot_bytes": original.stat().st_size, "stream_modified": False,
                "sections": sections, "target_reference": target_reference,
                "target_directory": incoming.directory.name,
                "original_stream_hash_unchanged": True,
                **source_evidence, **failed})
            check("real_migration_rejects_" + label.replace("-", "_") + "_profile")
            incoming.close()

        for kind, diagnostic_text in (
                # UINT32_EQUAL uses PRIx32, target first, incoming second.
                ("reference-identity", "53424d31 != 53424d30"),
                ("port1-remaining", TYPE + ": invalid migrated reference state")):
            changed_path = out / ("noc-sbm-v1-corrupted-" + kind + ".migration")
            mutation = corrupt_single_field(first_path, changed_path, section, kind)
            assert digest(first_path) == first_digest
            incoming = new("corrupted-" + kind, incoming=True)
            failed = expect_incoming_failure(incoming, changed_path, diagnostic_text)
            assert digest(first_path) == first_digest
            report["corrupted_stream_rejections"].append({
                "name": kind, "passed": True, "stream_modified": True,
                "source_status": sent["status"], "source": sent,
                "original_stream_file": first_path.name, "original_stream_sha256": first_digest,
                "original_stream_bytes": first_path.stat().st_size,
                "corrupted_stream_file": changed_path.name,
                "corrupted_stream_sha256": digest(changed_path),
                "corrupted_stream_bytes": changed_path.stat().st_size,
                "original_stream_hash_unchanged": True,
                "target_directory": incoming.directory.name,
                "original_sections": [section], "mutation": mutation, **failed})
            check("real_migration_rejects_single_field_corruption_" + kind.replace("-", "_"))
            incoming.close()
        assert digest(first_path) == first_digest
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    # UFS is excluded from component migration and GCC's reset witness must
    # reset to zero, independently of the board's optional firmware handoff.
    parser.add_argument("--machine", default=(
        "tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
        "rpmh-profile=off,rpmh-boot-configured=off"))
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = str(Path(args.qemu).resolve())
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    before_binary = digest(Path(args.qemu))
    started = time.monotonic()
    report = {
        "schema": "tb371fc-noc-sbm-v1", "gate": "noc-sbm-reference",
        "qemu": args.qemu, "machine": args.machine,
        "qemu_sha256_before": before_binary,
        "test_sha256": digest(Path(__file__).resolve()),
        "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_SHA256,
        "source_path": "drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c",
        "actual_dtb_sha256": DTB_SHA256, "actual_dt_layout_blob": LAYOUT_BLOB,
        "source_contract_blob": CONTRACT_BLOB,
        "old_qos_test_sha256": OLD_QOS_TEST_SHA256,
        "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 2,
        "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2,
        "vmstate_version": 1, "vmstate_payload_bytes_per_device": PAYLOAD_BYTES,
        "device_count": 1, "port_count": 8, "register_count": 3,
        "sparse_window_count": 3, "mapped_bytes": 12,
        "reference_latency_ns": 1000, "supported_port_mask": 255,
        "reference_boot_disconnected_mask": 0,
        "reference_profile_is_default": False,
        "default_profile_leaves_mmio_unmapped": True,
        "independent_logical_port_deadlines": 8,
        "qemu_timer_object_count": 1,
        "diagnostic_counters_are_hardware": False,
        "hardware_latency_measured": False,
        "traffic_blocking_implemented": False, "traffic_drain_observed": False,
        "clock_control_implemented": False, "irq_implemented": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": []}
    current = None
    try:
        assert len(ORDINARY_TESTS) == 16 and len(SPECIAL_TESTS) == 4
        for test in ORDINARY_TESTS:
            current = test.__name__
            rig = SbmRig(args, out / current)
            try:
                test(rig)
            finally:
                rig.close()
            report["checks"].append({"name": current, "passed": True})
        for test in SPECIAL_TESTS:
            current = test.__name__
            test(args, out)
            report["checks"].append({"name": current, "passed": True})
        current = "migration_checks"
        migration_checks(args, out, report)
        assert len(report["checks"]) == EXPECTED_CHECKS
        assert len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS
        assert len(report["migrations"]) == 2
        assert all(item["source_status"] == item["target_status"] == "completed" and
                   item["continued_after_restore"] and
                   item["restored_deadlines_verified_before_any_sbm_mmio"] and
                   item["different_virtual_origins"] and not item["stream_modified"]
                   for item in report["migrations"])
        assert len(report["rejected_migrations"]) == 2
        assert all(item["destination_exit"] == 1 and item["source_status"] == "completed" and
                   item["specific_load_failure_observed"] and not item["stream_modified"]
                   for item in report["rejected_migrations"])
        assert len(report["corrupted_stream_rejections"]) == 2
        assert all(item["destination_exit"] == 1 and item["source_status"] == "completed" and
                   item["specific_load_failure_observed"] and item["stream_modified"] and
                   item["mutation"]["only_one_field_modified"] and
                   item["mutation"]["only_one_bit_modified"]
                   for item in report["corrupted_stream_rejections"])
        report["passed"] = True
    except BaseException as error:
        report["checks"].append({"name": current, "passed": False,
                                 "error": str(error), "traceback": traceback.format_exc()})
        report["passed"] = False
    finally:
        report["duration_seconds"] = time.monotonic() - started
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == before_binary
        report["passed"] = report.get("passed", False) and report["binary_unchanged"]
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "migrations": len(report["migrations"]),
                      "rejected_migrations": len(report["rejected_migrations"]),
                      "corrupted_stream_rejections": len(report["corrupted_stream_rejections"]),
                      "result": str(out / "result.json")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
