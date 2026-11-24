#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded Qualcomm firmware Devfreq request registers and real migration.

The fixed production source defines the table decoder and the software voters.
The opt-in four-row table is a synthetic handoff, not an observed firmware LUT.
Requested/nominal frequency is not an actual cache Clock, execution speed,
voltage, thermal state or hardware acknowledgement.
"""
import argparse
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

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

SOURCE_COMMIT = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_SHA256 = "745457c2a20c90521772ef29c5f572570f481541d01ef427707d3da35bc5eb16"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
DT_CAPTURE_SHA256 = "3b4d663de9e5ea33f5a86fc7d9f49f26223a6217657594423691d59f0ded124b"
TYPE = "qcom-devfreq-fw"
DEVICE = "/machine/devfreq-l3"
ENABLE, TABLE, PERF = 0x18590000, 0x18590100, 0x18590320
XO_NOMINAL = 19_200_000
ROW_BYTES, ROW_COUNT, VALID_ROWS = 4, 40, 4
REFERENCE_L = (16, 32, 64, 96)
REFERENCE_HZ = (307200000, 614400000, 1228800000, 1843200000)
LUT_IDENTITY = 0x44463031
RAM_WITNESS = 0x80010000
GCC = 0x00100000
PAYLOAD_BYTES = 72
PERF_OFFSET, LUT_ID_OFFSET = 68, 52
EXPECTED_CHECKS = 20
REGIONS = {
    ENABLE: "qcom-devfreq-fw-enable",
    TABLE: "qcom-devfreq-fw-frequency-table",
    PERF: "qcom-devfreq-fw-performance",
}
REGISTERS = (ENABLE,) + tuple(TABLE + row * 4 for row in range(40)) + (PERF,)
GEOMETRY = {
    "instance-id": 0,
    "enable-address": ENABLE, "frequency-table-address": TABLE,
    "performance-address": PERF,
    "enable-resource-size": 4, "frequency-table-resource-size": 160,
    "performance-resource-size": 4, "lut-row-size": 4,
    "lut-max-entries": 40, "valid-perf-count": 4,
    "reference-lut-identity": LUT_IDENTITY,
    "reference-nominal-xo-hz": XO_NOMINAL,
    "mapped-window-count": 3, "mapped-register-count": 42,
    "mapped-bytes": 168,
}
RUNTIME = ("firmware-enabled", "performance-index", "requested-lval",
           "requested-frequency-hz")


def reference_word(row):
    return 0x40000000 | REFERENCE_L[min(row, VALID_ROWS - 1)]


def vendor_frequency_hz(word):
    # devfreq_qcom_populate_opp() uses constants, not clk_get_rate().
    source = (word >> 30) & 3
    return XO_NOMINAL * (word & 0xff) if source else 300_000_000


def vendor_table(words):
    frequencies = []
    previous = 0
    for row, word in enumerate(words[:ROW_COUNT]):
        frequency = vendor_frequency_hz(word)
        # The comment mentions core counts; the actual condition only compares
        # frequencies. No core-count field is decoded in this driver.
        if row > 0 and previous == frequency:
            return frequencies, row
        frequencies.append(frequency)
        previous = frequency
    raise AssertionError("Production frequency parser found no duplicate sentinel")


def base_command(args, *, reference=True, boot_index=0, accel="qtest", extra=()):
    command = [args.qemu, "-machine", args.machine, "-accel", accel,
               "-m", "128M", "-display", "none", "-monitor", "none",
               "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]
    if reference is not None:
        command += ["-global", TYPE + ".enabled-reference=" +
                    ("on" if reference else "off")]
    if boot_index:
        command += ["-global", TYPE + f".reference-boot-perf-index={boot_index}"]
    return command + list(extra)


class DevfreqRig(Rig):
    def __init__(self, args, directory, *, incoming=False, reference=True,
                 boot_index=0, accel="qtest", extra=()):
        self.args, self.reference, self.boot_index = args, reference is True, boot_index
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qdevfreq-"))
        self.last_clock = 0
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = base_command(args, reference=reference, boot_index=boot_index,
                               accel=accel, extra=extra) + [
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
        try:
            result = super().monitor(execute, arguments)
        except BaseException as error:
            self.qmp_transcript.write("< ERROR " + str(error) + "\n")
            raise
        self.qmp_transcript.write("< " + json.dumps(result) + "\n")
        return result

    def read(self, address, width="l"):
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def write(self, address, value, width="l"):
        self.command(f"write{width} {address:#x} {value:#x}")

    def get(self, name):
        return self.monitor("qom-get", {"path": DEVICE, "property": name})

    def put(self, name, value):
        return self.monitor("qom-set", {"path": DEVICE, "property": name, "value": value})

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
    matches = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
                         r"size ([0-9]+), region '([^']+)', reason: ([^\n]+)", text)
    return [(kind, int(address, 16), int(size), region, reason)
            for kind, address, size, region, reason in matches]


def device_diagnostics(text):
    return [line for line in text.splitlines() if TYPE + ":" in line]


def raw_words(rig):
    return [rig.read(address) for address in REGISTERS]


def expect_request(rig, index, *, mmio=True):
    assert rig.get("firmware-enabled") is True
    assert rig.get("performance-index") == index
    assert rig.get("requested-lval") == REFERENCE_L[index]
    assert rig.get("requested-frequency-hz") == REFERENCE_HZ[index]
    if mmio:
        assert rig.read(PERF) == index
        assert rig.read(ENABLE) == 1


def snapshot(rig, *, unknown=False):
    geometry = {name: rig.get(name) for name in GEOMETRY}
    geometry["enabled-reference"] = rig.get("enabled-reference")
    geometry["reference-boot-perf-index"] = rig.get("reference-boot-perf-index")
    assert {name: geometry[name] for name in GEOMETRY} == GEOMETRY
    assert geometry["enabled-reference"] is (not unknown)
    assert geometry["reference-boot-perf-index"] == rig.boot_index
    runtime, errors, lut = {}, {}, []
    if unknown:
        for name in RUNTIME + tuple(
                f"lut{row}-{field}" for row in range(ROW_COUNT)
                for field in ("frequency-word", "nominal-frequency-hz")):
            try:
                rig.get(name)
            except (AssertionError, RuntimeError) as error:
                errors[name] = str(error)
                assert TYPE + ": unknown profile" in str(error), str(error)
            else:
                raise AssertionError(("unknown profile exposed runtime", name))
    else:
        runtime = {name: rig.get(name) for name in RUNTIME}
        for row in range(ROW_COUNT):
            word = rig.get(f"lut{row}-frequency-word")
            frequency = rig.get(f"lut{row}-nominal-frequency-hz")
            assert word == reference_word(row)
            assert frequency == vendor_frequency_hz(word)
            lut.append({"row": row, "word": word, "nominal_frequency_hz": frequency})
    return {"geometry": geometry, "runtime": runtime, "runtime_errors": errors, "lut": lut}


def explicit_reference_exposes_all_forty_rows_and_three_actual_dt_resources(rig):
    state = snapshot(rig)
    assert state["geometry"]["mapped-register-count"] == len(REGISTERS) == 42
    assert len(set(REGISTERS)) == 42
    expected = [1] + [reference_word(row) for row in range(40)] + [0]
    assert raw_words(rig) == expected
    assert rig.read(TABLE + 39 * 4) == 0x40000060
    expect_request(rig, 0)


def literal_production_parser_uses_frequency_only_and_stops_at_the_duplicate(rig):
    words = [rig.read(TABLE + row * ROW_BYTES) for row in range(ROW_COUNT)]
    frequencies, terminal = vendor_table(words)
    assert frequencies == list(REFERENCE_HZ) and terminal == 4
    # The actual driver ignores bits 29:8 and accepts every nonzero SRC
    # encoding as the same nominal XO source. These local decode vectors
    # exercise that source oracle, not additional writable hardware rows.
    assert vendor_frequency_hz(0x000000ff) == 300_000_000
    for source in (1, 2, 3):
        assert vendor_frequency_hz((source << 30) | 0x00ff0020) == 614_400_000
    different_core_bits = list(words)
    different_core_bits[4] ^= 7 << 16
    assert different_core_bits[3] != different_core_bits[4]
    assert vendor_table(different_core_bits) == (list(REFERENCE_HZ), 4)
    # get_cur_freq() clamps in Linux software. Unsupported hardware writes
    # are tested separately and must not silently perform that clamp.
    for index in range(VALID_ROWS):
        rig.write(PERF, index)
        sampled = rig.read(PERF)
        assert frequencies[min(sampled, len(frequencies) - 1)] == REFERENCE_HZ[index]
    expect_request(rig, 3)


def every_legal_index_changes_requested_feedback_with_little_endian_words(rig):
    table = [rig.read(TABLE + row * 4) for row in range(40)]
    for index in (3, 1, 0, 2, 3, 2, 0, 1):
        rig.write(PERF, index)
        expect_request(rig, index)
        response = rig.command(f"read {PERF:#x} 4")
        assert len(response) == 1
        assert response[0].lower().removeprefix("0x") == struct.pack("<I", index).hex()
        rig.write(PERF, index)
        expect_request(rig, index)
        assert [rig.read(TABLE + row * 4) for row in range(40)] == table
    response = rig.command(f"read {TABLE:#x} 16")
    assert response[0].lower().removeprefix("0x") == b"".join(
        struct.pack("<I", reference_word(i)) for i in range(4)).hex()


def four_software_voters_write_the_literal_maximum_without_hidden_hardware_votes(rig):
    # The actual DT has four software voter children, not four hardware
    # request registers. Reproduce devfreq_qcom_fw_target()'s final writes.
    requests = [0, 0, 0, 0]
    trace = []
    updates = ((0, 2), (1, 1), (2, 3), (3, 1),
               (2, 0), (0, 0), (1, 0), (3, 0), (3, 3), (3, 0))
    for voter, index in updates:
        requests[voter] = index
        effective = max(requests)
        rig.write(PERF, effective)
        expect_request(rig, effective)
        trace.append({"software_voter": voter, "requests": list(requests),
                      "driver_written_index": effective})
    # No unmodelled vote persists in the hardware: a later direct low index
    # replaces the high request synchronously.
    rig.write(PERF, 3)
    rig.write(PERF, 0)
    expect_request(rig, 0)
    (rig.directory / "software-voter-write-sequence.json").write_text(
        json.dumps({"actual_dt_voter_phandles": [0x56, 0x59, 0x5a, 0x8e],
                    "software_sequence_only": True,
                    "hardware_vote_slots_implemented": False,
                    "trace": trace}, indent=2) + "\n")


def requested_state_does_not_invent_autonomous_clock_or_time_feedback(rig):
    rig.write(PERF, 2)
    before = snapshot(rig)
    for delta in (1, 999, 1_000_000, 1_000_000_000):
        rig.step(delta)
        assert snapshot(rig) == before
        expect_request(rig, 2)
    rig.monitor("stop")
    status = rig.monitor("query-status")
    assert status["running"] is False
    for _ in range(3):
        assert snapshot(rig) == before
        expect_request(rig, 2)
    rig.monitor("cont")
    rig.step(5000)
    assert snapshot(rig) == before
    # No real Clock object is exported by this request-only contract.
    children = rig.monitor("qom-list", {"path": DEVICE})
    assert not [entry for entry in children if entry["type"] == "child<clock>"], children


def system_reset_restores_boot_request_with_an_independent_gcc_witness(rig):
    rig.write(PERF, 3)
    rig.write(GCC + 4, 0x35)
    assert rig.read(GCC + 4) == 0x35
    expect_request(rig, 3)
    before_table = [rig.read(TABLE + row * 4) for row in range(40)]
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.read(GCC + 4) == 0, "GCC reset witness")
    rig.monitor("query-status")  # Drain the asynchronous RESET event.
    assert any(event.get("event") == "RESET" for event in rig.events), rig.events
    expect_request(rig, 0)
    assert [rig.read(TABLE + row * 4) for row in range(40)] == before_table


def invalid_indices_fail_atomically_instead_of_selecting_terminator_rows(rig):
    rig.write(PERF, 2)
    before = snapshot(rig)
    original = raw_words(rig)
    old = device_diagnostics(rig.log_path.read_text(errors="replace"))
    for value in (4, 5, 39, 40, 0x101, 0x10000000, 0xffffffff):
        rig.write(PERF, value)
        assert raw_words(rig) == original
        assert snapshot(rig) == before
    errors = device_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
    assert len(errors) == 7, errors
    for line, value in zip(errors, (4, 5, 39, 40, 0x101, 0x10000000, 0xffffffff)):
        assert line == invalid_performance_diagnostic(value), line


def read_only_handoff_and_table_and_qom_properties_cannot_forge_runtime(rig):
    rig.write(PERF, 1)
    before = snapshot(rig)
    words = raw_words(rig)
    old = device_diagnostics(rig.log_path.read_text(errors="replace"))
    addresses = (ENABLE,) + tuple(TABLE + row * 4 for row in range(40))
    for address in addresses:
        rig.write(address, 0xffffffff)
        assert snapshot(rig) == before
    errors = device_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
    assert errors == [read_only_diagnostic(address) for address in addresses], errors
    assert raw_words(rig) == words
    for name, value in {
            "performance-index": 3, "requested-lval": 96,
            "requested-frequency-hz": 1, "firmware-enabled": False,
            "lut0-frequency-word": 0, "lut39-nominal-frequency-hz": 1,
            "reference-lut-identity": 0, "enable-address": ENABLE + 4,
            "mapped-register-count": 1}.items():
        try:
            rig.put(name, value)
        except (AssertionError, RuntimeError) as error:
            assert f"Property '{TYPE}.{name}' is not writable" in str(error), str(error)
        else:
            raise AssertionError(("accepted readonly property write", name))
        assert snapshot(rig) == before
    for name, value in (("enabled-reference", False), ("reference-boot-perf-index", 3)):
        try:
            rig.put(name, value)
        except (AssertionError, RuntimeError) as error:
            assert name in str(error) and "after it was realized" in str(error), str(error)
        else:
            raise AssertionError(("changed realized profile", name))
        assert snapshot(rig) == before


def access_diagnostic(address, reason, *, write=True, value=0xffffffff, width=4):
    if address == ENABLE:
        bank, local = "enable", 0
    elif TABLE <= address < TABLE + 160:
        bank, local = "frequency-table", address - TABLE
    elif address == PERF:
        bank, local = "performance", 0
    else:
        raise AssertionError(("diagnostic requested for hole", address))
    operation = "write" if write else "read"
    return (f"{TYPE}: {reason} {operation} {bank}+0x{local:x} "
            f"width={width} value=0x{value:x}")


def invalid_performance_diagnostic(value):
    return access_diagnostic(PERF, "invalid performance index", value=value)


def read_only_diagnostic(address):
    return access_diagnostic(address, "read-only register")


def strict_access_widths_and_sparse_holes_preserve_real_word_transactions(rig):
    rig.write(PERF, 2)
    baseline = snapshot(rig)
    words = raw_words(rig)
    for base in (ENABLE, TABLE, PERF):
        region = REGIONS[base]
        for operation in ("read", "write"):
            for width, size in (("b", 1), ("w", 2)):
                old = core_diagnostics(rig.log_path.read_text(errors="replace"))
                if operation == "read":
                    rig.read(base, width)
                else:
                    rig.write(base, 0xffff & ((1 << (8 * size)) - 1), width)
                errors = core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
                assert errors == [(operation, 0, size, region,
                                   "invalid size (min:4 max:4)")], errors
            for displacement in (1, 2):
                old = core_diagnostics(rig.log_path.read_text(errors="replace"))
                if operation == "read":
                    rig.read(base + displacement)
                else:
                    rig.write(base + displacement, 0x03020100)
                pieces = [(1, 1), (2, 2), (4, 1)] if displacement == 1 else [(2, 2), (4, 2)]
                expected = []
                for local, size in pieces:
                    if base != TABLE and local == 4:
                        expected.append((operation, base + local, size, "(null)", "rejected"))
                    else:
                        expected.append((operation, local, size, region,
                                         "invalid size (min:4 max:4)"))
                errors = core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
                assert errors == expected, (base, operation, displacement, errors, expected)
        assert snapshot(rig) == baseline and raw_words(rig) == words
    holes = (ENABLE - 4, ENABLE + 4, ENABLE + 0x20, TABLE - 4,
             TABLE + 160, TABLE + 0xfc, ENABLE + 0x200, ENABLE + 0x304,
             PERF - 4, PERF + 4, ENABLE + 0x3c4, ENABLE + 0x3c8,
             ENABLE + 0xffc)
    assert not set(holes) & set(REGISTERS)
    old = core_diagnostics(rig.log_path.read_text(errors="replace"))
    expected = []
    for address in holes:
        rig.read(address)
        rig.write(address, 0xffffffff)
        expected += [(operation, address, 4, "(null)", "rejected")
                     for operation in ("read", "write")]
    assert core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):] == expected
    assert snapshot(rig) == baseline and raw_words(rig) == words

    # QTest's 8-byte bus request is split into 4-byte device transactions.
    # A table read therefore returns two real adjacent words; no 8-byte
    # device callback exists in this contract.
    assert rig.read(TABLE, "q") == reference_word(0) | (reference_word(1) << 32)
    old = device_diagnostics(rig.log_path.read_text(errors="replace"))
    rig.write(TABLE, 0xa5a5a5a55a5a5a5a, "q")
    assert device_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):] == [
        access_diagnostic(TABLE, "read-only register", value=0x5a5a5a5a),
        access_diagnostic(TABLE + 4, "read-only register", value=0xa5a5a5a5)]
    assert snapshot(rig) == baseline and raw_words(rig) == words
    old = core_diagnostics(rig.log_path.read_text(errors="replace"))
    rig.write(PERF, (0xa5a5a5a5 << 32) | 1, "q")
    assert core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):] == [
        ("write", PERF + 4, 4, "(null)", "rejected")]
    expect_request(rig, 1)
    assert [rig.read(TABLE + row * 4) for row in range(40)] == words[1:-1]


def default_unknown_profile_rejects_all_forty_two_words_and_runtime_getters(args, out):
    rig = DevfreqRig(args, out / "default-unknown", reference=None)
    try:
        assert rig.get("enabled-reference") is False
        before = snapshot(rig, unknown=True)
        assert not before["runtime"] and len(before["runtime_errors"]) == 84
        old = device_diagnostics(rig.log_path.read_text(errors="replace"))
        expected = []
        for address in REGISTERS:
            rig.read(address)
            rig.write(address, 0)
            expected += [
                access_diagnostic(address, "unknown profile", write=False, value=0),
                access_diagnostic(address, "unknown profile", value=0)]
        errors = device_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
        assert errors == expected, (errors, expected)
        rig.step(1_000_000)
        assert snapshot(rig, unknown=True) == before
        (rig.directory / "unknown-qom.json").write_text(json.dumps(before, indent=2) + "\n")
    finally:
        rig.close()


def explicit_boot_request_and_realization_constraints_are_observable(args, out):
    rig = DevfreqRig(args, out / "explicit-boot-selection", boot_index=3)
    try:
        assert rig.get("reference-boot-perf-index") == 3
        expect_request(rig, 3)
        rig.write(PERF, 0)
        rig.write(GCC + 4, 0x34)
        assert rig.read(GCC + 4) == 0x34
        rig.events.clear()
        rig.monitor("system_reset")
        rig.wait_for(lambda: rig.read(GCC + 4) == 0, "GCC reset witness")
        rig.monitor("query-status")
        assert any(event.get("event") == "RESET" for event in rig.events)
        expect_request(rig, 3)
    finally:
        rig.close()
    diagnostic = (TYPE + ": reference-boot-perf-index requires 0..3 and"
                  " must be zero in the unknown profile")
    for label, reference, boot in (
            ("terminator-index", True, 4),
            ("reserved-high-bits", True, 0xffffffff),
            ("unknown-nonzero-boot", False, 1)):
        directory = out / ("invalid-configuration-" + label)
        directory.mkdir()
        command = base_command(args, reference=reference, boot_index=boot)
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10)
        (directory / "qemu.stdout").write_bytes(result.stdout)
        (directory / "qemu.stderr").write_bytes(result.stderr)
        assert result.returncode == 1, (label, result.returncode, result.stderr.decode(errors="replace"))
        assert diagnostic in result.stderr.decode(errors="replace")


def paused_tcg_uses_the_same_request_registers_without_guest_cpu_execution(args, out):
    rig = DevfreqRig(args, out / "paused-tcg", accel="tcg", boot_index=2)
    try:
        before = rig.monitor("query-status")
        assert before["running"] is False
        expect_request(rig, 2)
        assert raw_words(rig) == [1] + [reference_word(i) for i in range(40)] + [2]
        for index in (1, 3, 0):
            rig.write(PERF, index)
            expect_request(rig, index)
        after = rig.monitor("query-status")
        assert after["running"] is False
        (rig.directory / "paused-tcg.json").write_text(json.dumps({
            "status_before": before, "status_after": after,
            "guest_cpu_executed": False, "qmp_cont_sent": False,
            "normal_tcg_mmio_contract_observed": True,
            "actual_cache_frequency_measured": False,
            "actual_clock_objects_exported": False}, indent=2) + "\n")
    finally:
        rig.close()


ORDINARY_TESTS = [
    explicit_reference_exposes_all_forty_rows_and_three_actual_dt_resources,
    literal_production_parser_uses_frequency_only_and_stops_at_the_duplicate,
    every_legal_index_changes_requested_feedback_with_little_endian_words,
    four_software_voters_write_the_literal_maximum_without_hidden_hardware_votes,
    requested_state_does_not_invent_autonomous_clock_or_time_feedback,
    system_reset_restores_boot_request_with_an_independent_gcc_witness,
    invalid_indices_fail_atomically_instead_of_selecting_terminator_rows,
    read_only_handoff_and_table_and_qom_properties_cannot_forge_runtime,
    strict_access_widths_and_sparse_holes_preserve_real_word_transactions,
]
SPECIAL_TESTS = [
    default_unknown_profile_rejects_all_forty_two_words_and_runtime_getters,
    explicit_boot_request_and_realization_constraints_are_observable,
    paused_tcg_uses_the_same_request_registers_without_guest_cpu_execution,
]


SERIAL_FIELDS = (
    ("profile", 0, 4), ("instance_id", 4, 4),
    ("enable_address", 8, 8), ("frequency_table_address", 16, 8),
    ("performance_address", 24, 8), ("enable_resource_size", 32, 4),
    ("frequency_table_resource_size", 36, 4), ("performance_resource_size", 40, 4),
    ("lut_row_size", 44, 4), ("boot_perf_index", 48, 4),
    ("lut_identity", 52, 4), ("lut_entries", 56, 4),
    ("valid_perf_count", 60, 4), ("nominal_xo_hz", 64, 4),
    ("performance_index", 68, 4))


def packed_device(state, *, reference, boot_index):
    geometry, runtime = state["geometry"], state["runtime"]
    assert {name: geometry[name] for name in GEOMETRY} == GEOMETRY
    assert geometry["enabled-reference"] is reference
    assert geometry["reference-boot-perf-index"] == boot_index
    if reference:
        index = runtime["performance-index"]
        assert 0 <= index < 4
        assert runtime["firmware-enabled"] is True
        assert runtime["requested-lval"] == REFERENCE_L[index]
        assert runtime["requested-frequency-hz"] == REFERENCE_HZ[index]
    else:
        assert not runtime and len(state["runtime_errors"]) == 84
        # The disabled serialization invariant is zero. It is not a guessed
        # successful runtime QOM observation: all those getters really failed.
        index = 0
    data = struct.pack(">II3Q10I", int(reference), 0, ENABLE, TABLE, PERF,
                       4, 160, 4, 4, boot_index, LUT_IDENTITY, 40, 4,
                       XO_NOMINAL, index)
    assert len(data) == PAYLOAD_BYTES == 72
    return data


def actual_section(path, source_state, *, reference, boot_index):
    data = path.read_bytes()
    name = TYPE.encode("ascii")
    needle = bytes([len(name)]) + name + struct.pack(">II", 0, 1)
    expected = packed_device(source_state, reference=reference, boot_index=boot_index)
    matches = []
    position = 0
    while True:
        index = data.find(needle, position)
        if index < 0:
            break
        header, payload = index - 5, index + len(needle)
        if header >= 8 and data[header] in (1, 4) and data[payload:payload + PAYLOAD_BYTES] == expected:
            matches.append((header, payload))
        position = index + 1
    assert len(matches) == 1, matches
    header, payload = matches[0]
    actual = data[payload:payload + PAYLOAD_BYTES]
    decoded = {name: int.from_bytes(actual[offset:offset + width], "big")
               for name, offset, width in SERIAL_FIELDS}
    assert decoded["profile"] == int(reference)
    assert decoded["performance_index"] == (source_state["runtime"]["performance-index"]
                                           if reference else 0)
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
    # Every getter is pure: taking a second complete snapshot may not repair
    # or change the request that is about to enter the actual stream.
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
    assert after == before
    after_file = source.directory / "source-qom-after-save.json"
    after_file.write_text(json.dumps({
        "query_status": final_status, "source_virtual_ns": source.last_clock,
        "device": after}, indent=2) + "\n")
    (source.directory / "source-query-migrate.json").write_text(json.dumps(sent, indent=2) + "\n")
    section = actual_section(path, after, reference=source.reference, boot_index=source.boot_index)
    evidence = {
        "source_virtual_ns": source.last_clock,
        "source_qom_observation_did_not_change_request": True,
        "source_qom_before_file": source.directory.name + "/" + before_file.name,
        "source_qom_after_file": source.directory.name + "/" + after_file.name,
        "source_query_migrate_file": source.directory.name + "/source-query-migrate.json"}
    return after, sent, section, evidence


def prepare_target(target, destination_ns, different_index):
    target.set_clock(destination_ns)
    target.write(PERF, different_index)
    expect_request(target, different_index)
    target.write(RAM_WITNESS, 0xdeadbeef)
    state = snapshot(target)
    (target.directory / "destination-before-load.json").write_text(json.dumps({
        "destination_virtual_ns": destination_ns,
        "query_status": target.monitor("query-status"), "device": state}, indent=2) + "\n")
    return state


def restore_real_state(target, path, source_state, destination_ns, witness):
    assert target.last_clock == destination_ns
    target.monitor("migrate-incoming", file_channels(path))
    got = wait_migration(target)
    assert got["status"] == "completed", got
    status = target.monitor("query-status")
    assert status["running"] is False
    # First device observations after incoming use only pure QOM. Neither an
    # MMIO handler nor a guest write, reset or cont may recreate the state.
    restored = snapshot(target)
    assert restored == source_state
    assert target.read(RAM_WITNESS) == witness
    (target.directory / "destination-before-first-devfreq-mmio.json").write_text(
        json.dumps({"destination_virtual_ns": destination_ns, "query_status": status,
                    "device": restored, "ram_witness": witness,
                    "devfreq_mmio_since_incoming": False,
                    "qmp_cont_since_incoming": False}, indent=2) + "\n")
    (target.directory / "destination-query-migrate.json").write_text(json.dumps(got, indent=2) + "\n")
    return got


def corrupt_single_field(original, changed_path, section, kind):
    data = original.read_bytes()
    start = section["payload_offset"]
    assert data[start:start + PAYLOAD_BYTES].hex() == section["payload_hex"]
    if kind == "performance-index":
        relative = PERF_OFFSET
        old = int.from_bytes(data[start + relative:start + relative + 4], "big")
        assert old == 1
        new = old | 4
    elif kind == "lut-identity":
        relative = LUT_ID_OFFSET
        old = int.from_bytes(data[start + relative:start + relative + 4], "big")
        assert old == LUT_IDENTITY
        new = old ^ 1
    else:
        raise AssertionError(kind)
    field = start + relative
    changed = bytearray(data)
    changed[field:field + 4] = struct.pack(">I", new)
    assert changed[:field] == data[:field] and changed[field + 4:] == data[field + 4:]
    differences = [{"offset": i, "old_byte": before, "new_byte": after}
                   for i, (before, after) in enumerate(zip(data, changed)) if before != after]
    assert len(differences) == 1 and field <= differences[0]["offset"] < field + 4
    assert (old ^ new).bit_count() == 1
    changed_path.write_bytes(changed)
    assert original.read_bytes() == data and len(changed) == len(data)
    return {"instance_id": 0, "field": kind,
            "section_header_offset": section["section_header_offset"],
            "payload_offset": start, "field_relative_offset": relative,
            "field_offset": field, "modified_field_bytes": 4,
            "old_value": old, "new_value": new,
            "old_field_hex": data[field:field + 4].hex(),
            "new_field_hex": bytes(changed[field:field + 4]).hex(),
            "original_payload_hex": section["payload_hex"],
            "modified_payload_hex": bytes(changed[start:start + PAYLOAD_BYTES]).hex(),
            "complete_file_byte_differences": differences,
            "only_one_field_modified": True, "only_one_bit_modified": True}


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


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = DevfreqRig(args, out / name, **kwargs)
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
            "sections": [section], "source_boot_perf_index": source.boot_index,
            "target_devfreq_mmio_before_first_observation": False,
            "target_qmp_cont_before_first_observation": False,
            "destination_state_was_different_before_load": True,
            "target_qom_before_first_mmio_file": target.directory.name +
                                               "/destination-before-first-devfreq-mmio.json",
            "target_query_migrate_file": target.directory.name + "/destination-query-migrate.json",
            "continued_after_restore": False, **evidence}
        report["migrations"].append(record)
        return record

    try:
        source = new("migration-request-source")
        source.write(PERF, 1)
        source.step(137)
        first_path = out / "devfreq-fw-v1-request-index.migration"
        state, sent, section, evidence = save_real_state(source, first_path, 0x44465731)
        assert state["runtime"]["performance-index"] == 1
        target = new("migration-request-target", incoming=True)
        destination_ns = 987654321
        old = prepare_target(target, destination_ns, 3)
        assert old["runtime"] != state["runtime"]
        got = restore_real_state(target, first_path, state, destination_ns, 0x44465731)
        first = completed("v1_requested_index_and_ram_roundtrip_before_first_mmio",
                          first_path, source, target, sent, got, section, evidence, destination_ns)
        check("v1_request_state_restores_before_first_devfreq_mmio",
              source_virtual_ns=137, destination_virtual_ns=destination_ns)
        target.monitor("cont")
        target.step(999)
        expect_request(target, 1, mmio=False)
        # Only now may an MMIO read or write exercise the restored device.
        assert target.read(PERF) == 1
        target.write(PERF, 2)
        expect_request(target, 2)
        target.write(PERF, 0)
        expect_request(target, 0)
        first["continued_after_restore"] = True
        check("restored_request_accepts_independent_new_indices_without_persisting_old_votes")
        first_digest = digest(first_path)
        source.close()
        target.close()

        source2 = new("migration-boot-policy-source", boot_index=2)
        expect_request(source2, 2)
        source2.write(PERF, 3)
        source2.step(12345)
        second_path = out / "devfreq-fw-v1-explicit-boot-policy.migration"
        state2, sent2, section2, evidence2 = save_real_state(source2, second_path, 0x44465732)
        assert state2["runtime"]["performance-index"] == 3
        assert state2["geometry"]["reference-boot-perf-index"] == 2
        target2 = new("migration-boot-policy-target", incoming=True, boot_index=2)
        destination_ns2 = 73
        old2 = prepare_target(target2, destination_ns2, 0)
        assert old2["runtime"] != state2["runtime"]
        got2 = restore_real_state(target2, second_path, state2, destination_ns2, 0x44465732)
        second = completed("v1_nonzero_boot_policy_and_live_request_roundtrip",
                           second_path, source2, target2, sent2, got2, section2, evidence2,
                           destination_ns2)
        check("v1_nonzero_boot_policy_and_distinct_live_request_restore_before_mmio",
              source_virtual_ns=12345, destination_virtual_ns=destination_ns2)
        target2.monitor("cont")
        target2.step(1)
        expect_request(target2, 3, mmio=False)
        target2.write(PERF, 0)
        expect_request(target2, 0)
        target2.write(GCC + 4, 0x33)
        assert target2.read(GCC + 4) == 0x33
        target2.events.clear()
        target2.monitor("system_reset")
        target2.wait_for(lambda: target2.read(GCC + 4) == 0, "restored GCC reset witness")
        target2.monitor("query-status")
        assert any(event.get("event") == "RESET" for event in target2.events)
        expect_request(target2, 2)
        second["continued_after_restore"] = True
        second["restored_explicit_boot_policy_verified_by_actual_system_reset"] = True
        check("restored_live_request_changes_and_system_reset_reapplies_explicit_boot_policy")
        source2.close()
        target2.close()

        unknown = new("migration-unknown-source", reference=False)
        unknown.step(4567)
        unknown_path = out / "devfreq-fw-v1-unknown-profile.migration"
        unknown_state, unknown_sent, unknown_section, unknown_evidence = save_real_state(
            unknown, unknown_path, 0x44465730)
        assert unknown_section["unknown_runtime_zero_invariant_verified"]
        unknown.close()
        for label, original, original_source, target_reference, diagnostic, sections, source_evidence in (
                ("reference-to-unknown", first_path, sent, False, "0 != 1", [section], evidence),
                ("unknown-to-reference", unknown_path, unknown_sent, True, "1 != 0",
                 [unknown_section], unknown_evidence)):
            incoming = new("rejected-" + label, incoming=True, reference=target_reference)
            original_hash = digest(original)
            failed = expect_incoming_failure(incoming, original, diagnostic)
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

        for kind, diagnostic in (
                ("performance-index", TYPE + ": invalid migrated performance index"),
                # migration/vmstate-types.c load_uint32_equal prints PRIx32:
                # target immutable identity first, incoming mutated identity second.
                ("lut-identity", "44463031 != 44463030")):
            changed_path = out / ("devfreq-fw-v1-corrupted-" + kind + ".migration")
            mutation = corrupt_single_field(first_path, changed_path, section, kind)
            assert digest(first_path) == first_digest
            incoming = new("corrupted-" + kind, incoming=True)
            failed = expect_incoming_failure(incoming, changed_path, diagnostic)
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
    # Match the already verified CPUFreq component fixture: migration must
    # not include UFS, and the independent GCC reset witness needs reset L=0.
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
        "schema": "tb371fc-devfreq-qcom-fw-v1",
        "qemu": args.qemu, "machine": args.machine,
        "qemu_sha256_before": before_binary,
        "test_sha256": digest(Path(__file__).resolve()),
        "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_SHA256,
        "source_path": "drivers/devfreq/devfreq_qcom_fw.c",
        "actual_dtb_sha256": DTB_SHA256, "actual_dt_capture_sha256": DT_CAPTURE_SHA256,
        "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 2,
        "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2,
        "vmstate_version": 1, "vmstate_payload_bytes_per_device": PAYLOAD_BYTES,
        "device_count": 1, "register_count": 42, "sparse_window_count": 3,
        "mapped_bytes": 168, "lut_rows": 40, "lut_row_bytes": 4,
        "valid_perf_states": 4, "software_voter_count": 4,
        "reference_nominal_xo_hz": XO_NOMINAL,
        "reference_profile_is_default": False,
        "reference_lut_is_synthetic": True,
        "factory_lut_observed": False,
        "hardware_voter_slots_implemented": False,
        "clock_control_implemented": False,
        "actual_cache_frequency_measured": False,
        "irq_implemented": False, "timer_implemented": False,
        "counter_implemented": False, "voltage_control_implemented": False,
        "firmware_execution_observed": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": []}
    current = None
    try:
        for test in ORDINARY_TESTS:
            current = test.__name__
            rig = DevfreqRig(args, out / current)
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
                   item["continued_after_restore"] and not item["stream_modified"]
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
