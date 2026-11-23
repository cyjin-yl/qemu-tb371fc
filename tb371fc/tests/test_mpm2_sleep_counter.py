#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual MPM2 counter transactions, exact phase, reset and migration.

OnePlusOSS/android_kernel_oneplus_sm8250 at
94304e12a3509eac7b3716bf574ce0b1fcaacbae, drivers/soc/qcom/boot_stats.c,
uses a read32 at offset zero. The captured TB371FC DT specifies 32768 Hz.
Cold zero and continuity across system reset are explicit reference policies,
not measured claims about a physical reset or firmware epoch.
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

TYPE = "qcom-mpm2-sleep-counter"
PATH = "/machine/mpm2-sleep-counter"
BASE = 0x0c221000
GCC = 0x00100000
FREQUENCY = 32768
NS_PER_SECOND = 1_000_000_000
PERIOD_UNIT = NS_PER_SECOND << 32
MASK = 0xffffffff
WRAP_NS = (1 << 32) * NS_PER_SECOND // FREQUENCY
EXPECTED_CHECKS = 17
DIAGNOSTIC = TYPE + ":"
SOURCE_COMMIT = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_URL = ("https://raw.githubusercontent.com/OnePlusOSS/"
              "android_kernel_oneplus_sm8250/" + SOURCE_COMMIT +
              "/drivers/soc/qcom/boot_stats.c")
SOURCE_SHA256 = "4fd7113b1755f4dcaac55c688abba97d00f15fa000b1d88b5b187946f6925ff6"


def expected_count(elapsed_ns):
    # Independent integer oracle. Do not approximate one tick as 30517 ns.
    return (elapsed_ns * FREQUENCY // NS_PER_SECOND) & MASK


def base_command(args):
    return [args.qemu, "-machine", args.machine, "-accel", "qtest",
            "-m", "128M", "-display", "none", "-monitor", "none",
            "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]


class CounterRig(Rig):
    def __init__(self, args, directory, *, incoming=False):
        self.args = args
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qmpm2-"))
        self.last_clock = 0
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = base_command(args) + [
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
            greeting = json.loads(self.qmpfile.readline())
            assert "QMP" in greeting, greeting
            self.monitor("qmp_capabilities")
            self.qtest = connect_unix(self.socket_dir / "t", self.process)
            self.connections.append(self.qtest)
            self.qfile = self.qtest.makefile("rwb", buffering=0)
            if not incoming:
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

    def raw_read(self, address):
        return int(self.command(f"readl {address:#x}")[0], 0)

    def raw_write(self, address, value):
        self.command(f"writel {address:#x} {value:#x}")

    def read(self):
        return self.raw_read(BASE)

    def get(self, name="counter", path=PATH):
        return self.monitor("qom-get", {"path": path, "property": name})

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns, (before, ns, self.last_clock)
        return self.last_clock

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return self.last_clock

    def assert_count(self, expected):
        # Actual peripheral MMIO is primary; QOM independently reports the
        # same low 32 bits. Migration deliberately reverses this ordering.
        assert self.read() == expected, (self.last_clock, self.read(), expected)
        assert self.get() == expected, (self.last_clock, self.get(), expected)

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


def cold_zero_and_dt_frequency_are_distinct_from_the_gcc_sleep_carrier(rig):
    assert rig.get("clock-frequency") == FREQUENCY
    rig.assert_count(0)
    # Existing GCC sleep input is 32000 Hz. MPM2 must not silently reuse it.
    period = rig.get("qtest-clock-period", "/machine/gcc/sleep")
    assert period == PERIOD_UNIT // 32000
    rig.step(NS_PER_SECOND)
    rig.assert_count(32768)
    assert rig.read() != 32000
    assert rig.get("qtest-clock-period", "/machine/gcc/sleep") == period


def exact_fractional_tick_and_second_boundaries(rig):
    for ns, count in ((30517, 0), (30518, 1), (61035, 1), (61036, 2),
                      (999_999_999, 32767), (1_000_000_000, 32768),
                      (1_000_030_517, 32768), (1_000_030_518, 32769)):
        rig.set_clock(ns)
        rig.assert_count(count)
        assert count == expected_count(ns)


def irregular_steps_and_repeated_reads_do_not_discard_fractional_phase(rig):
    deltas = (1, 1001, 29, 30517, 7, 12345, 511, 99999, 17, 30518)
    total = 0
    for index in range(70):
        delta = deltas[index % len(deltas)]
        total += delta
        rig.step(delta)
        for _ in range(3):
            rig.assert_count(expected_count(total))
    assert rig.last_clock == total
    # A model that floors each elapsed delta independently loses ticks.
    independently_truncated = sum(
        deltas[i % len(deltas)] * FREQUENCY // NS_PER_SECOND
        for i in range(70))
    assert independently_truncated < expected_count(total)


def counter_wraps_at_exactly_2_to_32_ticks(rig):
    assert WRAP_NS == 131_072_000_000_000
    rig.set_clock(WRAP_NS - 1)
    rig.assert_count(MASK)
    rig.step(1)
    rig.assert_count(0)
    rig.step(30517)
    rig.assert_count(0)
    rig.step(1)
    rig.assert_count(1)
    rig.set_clock(2 * WRAP_NS + NS_PER_SECOND)
    rig.assert_count(32768)


def wide_elapsed_time_uses_exact_math_before_low_word_wrap(rig):
    # The intermediate ns * 32768 exceeds uint64_t; the result still fits
    # the documented 32-bit modulo counter within QEMU's signed time range.
    ns = (1 << 60) + 987_654_321
    assert ns * FREQUENCY > (1 << 64) - 1
    assert ns < (1 << 63) - 1
    rig.set_clock(ns)
    rig.assert_count(expected_count(ns))
    for delta in (1, 30517, 1, NS_PER_SECOND):
        ns += delta
        rig.step(delta)
        rig.assert_count(expected_count(ns))


def paused_virtual_time_ignores_host_wait_and_read_traffic(rig):
    ns = 98_765_432
    rig.set_clock(ns)
    expected = expected_count(ns)
    rig.assert_count(expected)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    time.sleep(0.05)
    for _ in range(20):
        rig.assert_count(expected)
    assert rig.monitor("query-status")["running"] is False
    assert rig.last_clock == ns
    rig.monitor("cont")
    rig.step(51_234)
    rig.assert_count(expected_count(ns + 51_234))


def system_reset_preserves_reference_count_and_fraction_with_a_real_reset_witness(rig):
    rig.step(30517)
    rig.assert_count(0)
    rig.raw_write(GCC + 4, 31)
    assert rig.raw_read(GCC + 4) == 31
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.raw_read(GCC + 4) == 0,
                 "GCC nonzero register witness clears during actual system reset")
    assert any(event.get("event") == "RESET" for event in rig.events), rig.events
    rig.assert_count(0)
    rig.step(1)
    rig.assert_count(1)
    rig.set_clock(1_234_567_891)
    expected = expected_count(rig.last_clock)
    rig.raw_write(GCC + 4, 19)
    assert rig.raw_read(GCC + 4) == 19
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.raw_read(GCC + 4) == 0,
                 "second actual system reset clears the independent witness")
    rig.assert_count(expected)
    rig.step(12345)
    rig.assert_count(expected_count(rig.last_clock))


def aligned_writes_are_rejected_without_changing_count_or_phase(rig):
    rig.step(30517)
    old_log = rig.log_path.read_text(errors="replace")
    values = (0, 1, MASK, 0x12345678)
    for value in values:
        rig.raw_write(BASE, value)
        rig.assert_count(0)
    new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
    lines = [line for line in new_log.splitlines() if DIAGNOSTIC in line]
    assert lines == [TYPE + ": write to read-only counter at +0x0 size 4"] * len(values), new_log
    rig.step(1)
    rig.assert_count(1)


def narrow_and_unaligned_accesses_have_exact_core_rejections(rig):
    rig.step(30517)
    width = "invalid size (min:4 max:4)"
    operations = (
        (f"readb {BASE:#x}", [("read", 0, 1, TYPE, width)]),
        (f"readw {BASE:#x}", [("read", 0, 2, TYPE, width)]),
        (f"readw {BASE + 1:#x}", [("read", 1, 1, TYPE, width),
                                 ("read", 2, 1, TYPE, width)]),
        (f"readl {BASE + 1:#x}", [("read", 1, 1, TYPE, width),
                                 ("read", 2, 2, TYPE, width),
                                 ("read", BASE + 4, 1, "(null)", "rejected")]),
        (f"writeb {BASE:#x} 0", [("write", 0, 1, TYPE, width)]),
        (f"writew {BASE:#x} 0", [("write", 0, 2, TYPE, width)]),
        (f"writel {BASE + 1:#x} 0", [("write", 1, 1, TYPE, width),
                                    ("write", 2, 2, TYPE, width),
                                    ("write", BASE + 4, 1, "(null)", "rejected")]),
    )
    for operation, expected in operations:
        old_log = rig.log_path.read_text(errors="replace")
        rig.command(operation)
        rig.assert_count(0)
        new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
        assert core_diagnostics(new_log) == expected, (operation, expected, new_log)
    rig.step(1)
    rig.assert_count(1)


def configuration_is_immutable_and_qom_count_is_read_only(rig):
    rig.set_clock(999_999_999)
    expected = expected_count(rig.last_clock)
    for name, value in (("clock-frequency", 32000), ("clock-frequency", 32768),
                        ("counter", 0), ("counter", MASK)):
        before = rig.get(name)
        try:
            rig.monitor("qom-set", {"path": PATH, "property": name, "value": value})
        except AssertionError as error:
            assert "error" in str(error), str(error)
        else:
            raise AssertionError((name, value, "read-only realized property was writable"))
        assert rig.get(name) == before
        rig.assert_count(expected)
    rig.step(1)
    rig.assert_count(32768)


ORDINARY_TESTS = (
    cold_zero_and_dt_frequency_are_distinct_from_the_gcc_sleep_carrier,
    exact_fractional_tick_and_second_boundaries,
    irregular_steps_and_repeated_reads_do_not_discard_fractional_phase,
    counter_wraps_at_exactly_2_to_32_ticks,
    wide_elapsed_time_uses_exact_math_before_low_word_wrap,
    paused_virtual_time_ignores_host_wait_and_read_traffic,
    system_reset_preserves_reference_count_and_fraction_with_a_real_reset_witness,
    aligned_writes_are_rejected_without_changing_count_or_phase,
    narrow_and_unaligned_accesses_have_exact_core_rejections,
    configuration_is_immutable_and_qom_count_is_read_only,
)


def only_the_four_byte_counter_is_mapped_within_the_dt_aperture(args, out):
    rig = CounterRig(args, out / "sparse-four-byte-window")
    try:
        mtree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory / "mtree.txt").write_text(mtree)
        found = set()
        for line in mtree.splitlines():
            match = re.search(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" +
                              re.escape(TYPE) + r")(?:\s|$)", line)
            if match:
                first, last, name = match.groups()
                found.add((int(first, 16), int(last, 16), name))
        assert found == {(BASE, BASE + 3, TYPE)}, (found, mtree)
        rig.step(30517)
        for offset in (4, 8, 0x100, 0xffc):
            address = BASE + offset
            old_log = rig.log_path.read_text(errors="replace")
            rig.raw_write(address, 0x12345678)
            rig.raw_read(address)
            rig.assert_count(0)
            new_log = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(new_log) == [
                ("write", address, 4, "(null)", "rejected"),
                ("read", address, 4, "(null)", "rejected")], (address, new_log)
        rig.step(1)
        rig.assert_count(1)
    finally:
        rig.close()


def non_dt_frequencies_are_rejected_during_realization(args, out):
    receipts = []
    expected = TYPE + ": only the DT-declared clock-frequency 32768 is supported"
    for frequency in (0, 32000, 32769):
        directory = out / f"invalid-frequency-{frequency}"
        directory.mkdir()
        command = base_command(args) + ["-global", f"{TYPE}.clock-frequency={frequency}"]
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (directory / "qemu.stderr").open("wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
            try:
                try:
                    code = process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    raise AssertionError((frequency, "invalid frequency realized successfully"))
                assert code != 0, (frequency, code)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        stderr = (directory / "qemu.stderr").read_text(errors="replace")
        assert expected in stderr, (frequency, stderr)
        receipts.append({"clock_frequency": frequency, "destination_exit": code,
                         "expected_diagnostic_observed": True})
    return {"rejected_configurations": receipts}


def save_without_prior_counter_sample(source, path, witness):
    # The caller advances time but never reads MPM2 before this save. Only
    # pre_save can refresh count/fraction; a getter cannot conceal stale state.
    source.raw_write(0x80010000, witness)
    source.monitor("stop")
    assert source.monitor("query-status")["running"] is False
    source.monitor("migrate", file_channels(path))
    state = wait_migration(source)
    assert state["status"] == "completed"
    assert source.monitor("query-status")["status"] == "postmigrate"
    assert path.stat().st_size > 0
    return state


def restore_at_different_virtual_time(target, path, destination_ns, expected, witness):
    target.set_clock(destination_ns)
    target.monitor("migrate-incoming", file_channels(path))
    state = wait_migration(target)
    assert state["status"] == "completed"
    assert target.monitor("query-status")["running"] is False
    # First observation is pure QOM, before any device MMIO and before cont.
    # It checks saved count rather than counting the different target epoch.
    assert target.get() == expected, (destination_ns, target.get(), expected)
    assert target.get("clock-frequency") == FREQUENCY
    assert target.raw_read(0x80010000) == witness
    return state


def replace_only_the_frequency_in_a_real_device_section(source_path, changed_path):
    data = source_path.read_bytes()
    name = TYPE.encode("ascii")
    # FULL/START section: type, be32 section-id, u8 name length, name,
    # be32 instance-id, be32 VMState version, then its first u32 field.
    needle = bytes([len(name)]) + name + struct.pack(">II", 0, 1)
    positions = []
    pos = 0
    while True:
        index = data.find(needle, pos)
        if index < 0:
            break
        header = index - 5
        field = index + len(needle)
        if header >= 8 and data[header] in (1, 4):
            if data[field:field + 4] == struct.pack(">I", FREQUENCY):
                positions.append((header, field))
        pos = index + 1
    assert len(positions) == 1, positions
    header, field = positions[0]
    changed = bytearray(data)
    changed[field:field + 4] = struct.pack(">I", 32000)
    assert changed[:field] == data[:field] and changed[field + 4:] == data[field + 4:]
    assert changed != data
    changed_path.write_bytes(changed)
    assert source_path.read_bytes() == data
    return {"section_header_offset": header, "frequency_field_offset": field,
            "section_version": 1, "instance_id": 0,
            "original_frequency": FREQUENCY, "corrupted_frequency": 32000,
            "modified_field_bytes": 4, "only_frequency_field_modified": True}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = CounterRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, sent, got, source_ns, destination_ns):
        report["migrations"].append({
            "name": name, "passed": True, "source_status": sent["status"],
            "target_status": got["status"], "source": sent, "destination": got,
            "source_virtual_ns": source_ns, "destination_virtual_ns": destination_ns,
            "different_virtual_origins": source_ns != destination_ns,
            "source_counter_read_before_save": False,
            "target_device_mmio_before_first_observation": False,
            "snapshot_sha256": digest(path), "snapshot_bytes": path.stat().st_size})

    try:
        first_source = new("migration-first-tick-source")
        first_source.step(30517)
        first_path = out / "mpm2-v1-first-tick-fraction.migration"
        sent = save_without_prior_counter_sample(first_source, first_path, 0x4d504d31)
        first_target = new("migration-first-tick-target", incoming=True)
        destination = 987_654_321
        assert destination != first_source.last_clock
        got = restore_at_different_virtual_time(first_target, first_path, destination, 0, 0x4d504d31)
        completed("v1_first_tick_fraction_actual_roundtrip", first_path, sent, got,
                  first_source.last_clock, destination)
        check("v1_presave_freshness_and_fraction_restore_at_a_different_virtual_origin",
              source_fraction_numerator=999981056,
              device_mmio_before_observation=False)

        first_target.monitor("cont")
        first_target.step(1)
        assert first_target.get() == 1  # Preserve the near-tick source phase.
        assert first_target.read() == 1
        first_target.step(30517)
        assert first_target.get() == 1
        first_target.step(1)
        first_target.assert_count(2)
        first_target.step(NS_PER_SECOND)
        first_target.assert_count(32770)
        check("v1_fraction_crosses_first_and_second_ticks_without_reset_or_downtime_drift",
              first_delta_ns=1, second_boundary_delta_ns=30518)

        wrap_source = new("migration-wrap-source")
        wrap_source.set_clock(WRAP_NS - 1)
        wrap_path = out / "mpm2-v1-wrap-fraction.migration"
        wrap_sent = save_without_prior_counter_sample(wrap_source, wrap_path, 0x4d504d32)
        wrap_target = new("migration-wrap-target", incoming=True)
        wrap_destination = 73
        assert wrap_destination != wrap_source.last_clock
        wrap_got = restore_at_different_virtual_time(
            wrap_target, wrap_path, wrap_destination, MASK, 0x4d504d32)
        completed("v1_wrap_boundary_count_and_fraction_actual_roundtrip", wrap_path,
                  wrap_sent, wrap_got, wrap_source.last_clock, wrap_destination)
        check("v1_wrap_count_is_sampled_at_save_and_restored_before_device_mmio",
              source_fraction_numerator=999967232,
              device_mmio_before_observation=False)

        wrap_target.monitor("cont")
        wrap_target.step(1)
        assert wrap_target.get() == 0
        assert wrap_target.read() == 0
        wrap_target.step(30517)
        wrap_target.assert_count(0)
        wrap_target.step(1)
        wrap_target.assert_count(1)
        wrap_target.step(NS_PER_SECOND)
        wrap_target.assert_count(32769)
        check("v1_wrap_and_fraction_continue_from_saved_phase_at_destination",
              first_delta_ns=1, low_word_wrap_observed=True)

        corrupted = out / "mpm2-v1-corrupted-frequency-32000.migration"
        original_hash = digest(first_path)
        mutation = replace_only_the_frequency_in_a_real_device_section(first_path, corrupted)
        assert digest(first_path) == original_hash
        rejected = new("migration-corrupted-frequency-target", incoming=True)
        rejected.set_clock(1_234_567)
        assert rejected.get("clock-frequency") == FREQUENCY
        assert rejected.process.poll() is None
        request_sent = False
        command_error = None
        try:
            request_sent = True
            rejected.monitor("migrate-incoming", file_channels(corrupted))
        except (AssertionError, OSError, RuntimeError) as error:
            command_error = str(error)
        exit_code = rejected.process.wait(timeout=20)
        stderr = rejected.log_path.read_text(errors="replace")
        assert request_sent and exit_code == 1, (request_sent, exit_code, stderr)
        assert TYPE in stderr and "32768 != 32000" in stderr, stderr
        assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
        report["corrupted_stream_rejections"].append({
            "name": "frequency_field_corruption_is_rejected",
            "passed": True, "stream_modified": True, "actual_source_stream": True,
            "legitimate_alternate_frequency_profile": False,
            "source_status": sent["status"], "destination_exit": exit_code,
            "request_sent": request_sent, "command_error": command_error,
            "device": TYPE, "frequency_mismatch": "32768 != 32000",
            "original_snapshot_sha256": original_hash,
            "corrupted_snapshot_sha256": digest(corrupted),
            "original_stream_unchanged": digest(first_path) == original_hash,
            **mutation})
        check("one_real_stream_with_only_frequency_corrupted_is_rejected",
              stream_modified=True, legitimate_alternate_frequency_profile=False)
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.machine = ("tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
                    "rpmh-profile=off,rpmh-boot-configured=off")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    initial_hash = digest(args.qemu)
    report = {
        "schema": "tb371fc-mpm2-sleep-counter-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [],
        "expected_completed_migrations": 2, "migrations": [],
        "expected_corrupted_stream_rejections": 1, "corrupted_stream_rejections": [],
        "vmstate_version": 1, "clock_frequency_hz": FREQUENCY,
        "legacy_gcc_sleep_input_hz": 32000, "counter_bits": 32,
        "mmio_register_count": 1, "mmio_window_bytes": 4, "dt_aperture_bytes": 4096,
        "read_only": True, "clock_domain": "QEMU_CLOCK_VIRTUAL",
        "reference_cold_origin": "zero-at-realization",
        "reference_system_reset_policy": "preserve-count-and-fraction",
        "reference_policy_is_measured_hardware_reset_behavior": False,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP transactions and real VMState "
                 "migration of the explicit digital reference counter",
        "source_commit": SOURCE_COMMIT, "source_url": SOURCE_URL,
        "source_sha256": {"boot_stats.c": SOURCE_SHA256}}
    start = time.monotonic()
    try:
        assert len(ORDINARY_TESTS) == 10
        for test in ORDINARY_TESTS:
            rig = None
            try:
                rig = CounterRig(args, out / test.__name__)
                test(rig)
                report["checks"].append({"name": test.__name__, "passed": True})
            finally:
                if rig:
                    rig.close()
        only_the_four_byte_counter_is_mapped_within_the_dt_aperture(args, out)
        report["checks"].append({
            "name": "only_the_four_byte_counter_is_mapped_within_the_dt_aperture",
            "passed": True})
        configs = non_dt_frequencies_are_rejected_during_realization(args, out)
        report["checks"].append({
            "name": "non_dt_frequencies_are_rejected_during_realization",
            "passed": True, **configs})
        migration_checks(args, out, report)
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 2 and
            all(item["source_status"] == item["target_status"] == "completed"
                and item["different_virtual_origins"] is True
                for item in report["migrations"]) and
            len(report["corrupted_stream_rejections"]) == 1 and
            all(item["passed"] is True and item["stream_modified"] is True
                and item["original_stream_unchanged"] is True
                for item in report["corrupted_stream_rejections"]) and
            report["binary_unchanged"])
    except BaseException as error:
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
    report["duration_seconds"] = round(time.monotonic() - start, 3)
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
