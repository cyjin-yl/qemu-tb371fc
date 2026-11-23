#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Source-bounded CPUFreq hardware reference transactions and migration.

The production driver determines the register layout and LUT decode rules.
The explicitly enabled reference table, cycle source and reset policy are
testable model inputs, not a dump of the tablet's firmware DVFS table or a
measurement of CPU execution speed, physical voltage, power or throttling.
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
SOURCE_SHA256 = "559c942f52eedf76ab22f7770b7fa49a924c1941486b1e9afd419d38885ad174"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
RAM_WITNESS = 0x80010000
GCC = 0x00100000
PERIOD_UNIT = 1_000_000_000 << 32
BILLION = 1_000_000_000

TYPE = "qcom-cpufreq-hw-epss"
PATHS = ("/machine/cpufreq0", "/machine/cpufreq1", "/machine/cpufreq2")
BASES = (0x18591000, 0x18592000, 0x18593000)
CPU_COUNTS = (4, 3, 1)
CPU_FIRST = (0, 4, 7)
# All three actual qcom,freq-domain argument pairs use max_cores=4,
# independently of the 4/3/1 physical CPU membership.
MAX_CORES = (4, 4, 4)
ENABLE = 0x000
DOMAIN_STATE = 0x020
FREQ_LUT = 0x100
VOLT_LUT = 0x200
INTR_EN, INTR_CLR, INTR_STATUS = 0x304, 0x308, 0x30c
PERF_STATE = 0x320
CYCLE_CNTR = 0x3c4
GT_IRQ = 1 << 2
XO = 19_200_000
LUT_ROW_BYTES = 4
LUT_ROWS = 40
VALID_ROWS = 4
# Explicit synthetic profile. These are neither firmware observations nor
# the physical tablet's measured frequency/voltage operating points.
REFERENCE_SOURCE = (1, 1, 1, 1)
REFERENCE_L = (16, 32, 64, 96)
REFERENCE_MV = (750, 800, 900, 1000)
REFERENCE_CORE_COUNT = 4


def reference_frequency_word(row):
    index = min(row, VALID_ROWS - 1)
    return ((REFERENCE_SOURCE[index] << 30) |
            (REFERENCE_CORE_COUNT << 16) | REFERENCE_L[index])


def reference_voltage_word(row):
    return REFERENCE_MV[min(row, VALID_ROWS - 1)]


def cpu_counter_offset(cpu_in_domain):
    return CYCLE_CNTR + (cpu_in_domain + 1) * 4


def enable_gcc_alternate(rig, *, l=31, alpha=0x4000):
    # Existing independently verified GCC PLL MMIO path. No QOM fixture can
    # override the Clock provider attached to the EPSS alternate input.
    rig.raw_write(GCC + 4, l)
    rig.raw_write(GCC + 0x40, alpha)
    rig.raw_write(GCC + 0xc, 0x107)
    rig.raw_write(GCC, (1 << 20) | 4)
    rig.raw_write(GCC + 0x52018, 1)
    rig.step(5000)
    assert rig.raw_read(GCC) & ((1 << 31) | (1 << 30)) == (1 << 31) | (1 << 30)


def disable_gcc_alternate(rig):
    rig.raw_write(GCC + 0x52018, 0)
    assert rig.raw_read(GCC) & ((1 << 31) | (1 << 30)) == 0


def base_command(args, *, reference=True, boot_index=0, accel="qtest", extra=()):
    command = [args.qemu, "-machine", args.machine, "-accel", accel,
               "-m", "128M", "-display", "none", "-monitor", "none",
               "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]
    if reference is not None:
        command += ["-global", TYPE + ".enabled-reference=" +
                    ("on" if reference else "off")]
    if boot_index:
        command += ["-global", TYPE + f".reference-boot-perf-index={boot_index}"]
    command += list(extra)
    return command


class CpuFreqRig(Rig):
    def __init__(self, args, directory, *, incoming=False, reference=True,
                 accel="qtest", extra=(), irq_domain=0, boot_index=0):
        self.args = args
        self.reference = reference is True
        self.irq_domain = irq_domain
        self.boot_index = boot_index
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qcpufreq-"))
        self.last_clock = 0
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = base_command(args, reference=reference, boot_index=boot_index, accel=accel, extra=extra) + [
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
            if irq_domain is not None:
                self.command(f"irq_intercept_out {PATHS[irq_domain]} sysbus-irq")
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

    def raw_read(self, address, width="l"):
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def raw_write(self, address, value, width="l"):
        self.command(f"write{width} {address:#x} {value:#x}")

    def read(self, domain, offset, width="l"):
        return self.raw_read(BASES[domain] + offset, width)

    def write(self, domain, offset, value, width="l"):
        self.raw_write(BASES[domain] + offset, value, width)

    def get(self, domain, name):
        return self.monitor("qom-get", {"path": PATHS[domain], "property": name})

    def put(self, domain, name, value):
        return self.monitor("qom-set", {"path": PATHS[domain],
                                        "property": name, "value": value})

    def period(self, domain, name):
        return self.monitor("qom-get", {
            "path": PATHS[domain] + "/" + name, "property": "qtest-clock-period"})

    def expect_period(self, domain, name, hz):
        expected = PERIOD_UNIT // hz if hz else 0
        actual = self.period(domain, name)
        assert actual == expected, (domain, name, hz, expected, actual)

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns, (before, ns, self.last_clock)
        return self.last_clock

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return self.last_clock

    def wire(self, level):
        self.raw_read(RAM_WITNESS)
        assert self.irq.get(0, False) is level, self.irq

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


def vendor_frequency_khz(word, xo_hz, alternate_hz):
    # qcom_cpufreq_hw_read_lut(), not an independent guessed row encoding.
    source = (word >> 30) & 3
    lval = word & 0xff
    return xo_hz * lval // 1000 if source else alternate_hz // 2 // 1000


def vendor_voltage_uv(word):
    return (word & 0xfff) * 1000


def vendor_core_count(word):
    return (word >> 16) & 7


def cycles_and_fraction(segments):
    # Wide Python integer oracle integrates explicitly chosen rates over time.
    # The raw hardware low word wraps at 2**32, independently of the pinned
    # driver's software UINT_MAX overflow expression.
    numerator = sum(hz * ns for hz, ns in segments)
    return (numerator // BILLION) & 0xffffffff, numerator % BILLION


def actual_counters(rig, domain):
    return [rig.read(domain, cpu_counter_offset(i))
            for i in range(CPU_COUNTS[domain])]


def all_perf_and_clocks(rig):
    return [{"perf": rig.read(d, PERF_STATE),
             "period": rig.period(d, "cpu-frequency"),
             "counters": actual_counters(rig, d)} for d in range(3)]


def cold_reference_exposes_the_explicit_xo_table_with_a_zero_initial_counter(rig):
    for d in range(3):
        assert rig.get(d, "enabled-reference") is True
        assert rig.read(d, ENABLE) == 1
        assert rig.read(d, PERF_STATE) == 0
        assert rig.read(d, DOMAIN_STATE) == 16
        rig.expect_period(d, "xo", XO)
        rig.expect_period(d, "alternate", 0)
        rig.expect_period(d, "cpu-frequency", 307200000)
        assert actual_counters(rig, d) == [0] * CPU_COUNTS[d]
    rig.step(125)
    for d in range(3):
        assert actual_counters(rig, d) == [38] * CPU_COUNTS[d]
        assert rig.read(d, PERF_STATE) == 0

def all_forty_raw_rows_follow_the_explicit_profile_and_actual_dt_core_count(rig):
    for d in range(3):
        for row in range(LUT_ROWS):
            frequency = rig.read(d, FREQ_LUT + row * LUT_ROW_BYTES)
            voltage = rig.read(d, VOLT_LUT + row * LUT_ROW_BYTES)
            assert frequency == reference_frequency_word(row), (d, row, frequency)
            assert voltage == reference_voltage_word(row), (d, row, voltage)
            assert vendor_core_count(frequency) == MAX_CORES[d] == 4
            assert voltage & ~0xfff == 0
        assert rig.read(d, FREQ_LUT + 3 * 4) == rig.read(d, FREQ_LUT + 4 * 4)
        assert rig.read(d, VOLT_LUT + 39 * 4) == 1000
    assert CPU_COUNTS == (4, 3, 1) and MAX_CORES == (4, 4, 4)


def production_lut_reading_stops_at_the_duplicate_row_and_preserves_units(rig):
    expected_khz = [307200, 614400, 1228800, 1843200]
    expected_uv = [750000, 800000, 900000, 1000000]
    for d in range(3):
        rig.expect_period(d, "alternate", 0)
        rows = []
        previous = None
        reads = 0
        for row in range(LUT_ROWS):
            frequency_word = rig.read(d, FREQ_LUT + row * 4)
            voltage_word = rig.read(d, VOLT_LUT + row * 4)
            frequency = vendor_frequency_khz(frequency_word, XO, 0)
            core_count = vendor_core_count(frequency_word)
            reads += 1
            # Literal production termination: frequency AND core count match.
            if previous == (frequency, core_count):
                terminal = row
                break
            assert core_count == MAX_CORES[d]
            rows.append((frequency, vendor_voltage_uv(voltage_word)))
            previous = (frequency, core_count)
        else:
            raise AssertionError("The production driver found no LUT sentinel")
        assert terminal == 4 and reads == 5
        assert rows == list(zip(expected_khz, expected_uv))
        assert rig.read(d, PERF_STATE) == 0


def legal_perf_indices_change_real_clock_outputs_and_keep_domains_independent(rig):
    rates = (307200000, 614400000, 1228800000, 1843200000)
    selected = [0, 0, 0]
    for choices in ((1, 2, 3), (3, 0, 1), (2, 3, 0), (0, 1, 2)):
        for d, index in enumerate(choices):
            rig.write(d, PERF_STATE, index)
            selected[d] = index
            for other in range(3):
                assert rig.read(other, PERF_STATE) == selected[other]
                rig.expect_period(other, "cpu-frequency", rates[selected[other]])
    # The chosen table row is feedback. It is not a frequency integer and is
    # not silently clamped to the 40-row storage capacity.
    assert [rig.read(d, PERF_STATE) for d in range(3)] == selected


def per_cpu_counters_keep_fraction_through_irregular_steps_and_repeated_reads(rig):
    indices = (1, 2, 3)
    rates = tuple(XO * REFERENCE_L[index] for index in indices)
    for d, index in enumerate(indices):
        rig.write(d, PERF_STATE, index)
        rig.expect_period(d, "cpu-frequency", rates[d])
    anchors = [{name: rig.get(d, name) for name in (
        "counter-value-at-epoch", "counter-fraction-at-epoch", "counter-epoch-ns")}
        for d in range(3)]
    elapsed = 0
    for ns in (1, 1, 13, 7, 1024, 19, 625, 3):
        rig.step(ns)
        elapsed += ns
        for d, rate in enumerate(rates):
            expected, _fraction = cycles_and_fraction([(rate, elapsed)])
            for _ in range(3):
                assert actual_counters(rig, d) == [expected] * CPU_COUNTS[d]
                assert rig.get(d, "counter") == expected
            assert {name: rig.get(d, name) for name in anchors[d]} == anchors[d]
    # All actual DT per-CPU locations were read: no fabricated fourth core in
    # domain1 or extra core in domain2 substitutes for the three-domain map.
    assert sum(CPU_COUNTS) == 8


def perf_switches_settle_the_old_rate_and_preserve_the_fractional_cycle_phase(rig):
    d = 0
    rig.write(d, PERF_STATE, 1)
    rig.step(7)
    assert actual_counters(rig, d) == [4] * 4
    rig.write(d, PERF_STATE, 2)
    rig.step(5)
    expected, fraction = cycles_and_fraction([(614400000, 7), (1228800000, 5)])
    assert expected == 10 and fraction == 444800000
    assert actual_counters(rig, d) == [expected] * 4
    rig.write(d, PERF_STATE, 0)
    rig.expect_period(d, "cpu-frequency", 307200000)
    rig.step(29)
    rig.write(d, PERF_STATE, 3)
    rig.step(11)
    expected, _fraction = cycles_and_fraction([
        (614400000, 7), (1228800000, 5), (307200000, 29), (1843200000, 11)])
    assert actual_counters(rig, d) == [expected] * 4
    for other in (1, 2):
        other_expected, _ = cycles_and_fraction([(307200000, 52)])
        assert actual_counters(rig, other) == [other_expected] * CPU_COUNTS[other]
        rig.expect_period(other, "cpu-frequency", 307200000)

def changing_the_real_gcc_alternate_does_not_replace_the_selected_xo_source(rig):
    # This finite synthetic table deliberately uses src=1 for every row.
    # The real alternate carrier is observable, but is not selected by it.
    rig.write(1, PERF_STATE, 1)
    rates = (307200000, 614400000, 307200000)
    enable_gcc_alternate(rig)
    for d in range(3):
        rig.expect_period(d, "alternate", 600000000)
        rig.expect_period(d, "cpu-frequency", rates[d])
        expected, _ = cycles_and_fraction([(rates[d], rig.last_clock)])
        assert actual_counters(rig, d) == [expected] * CPU_COUNTS[d]
    rig.step(7)
    disable_gcc_alternate(rig)
    rig.step(100003)
    for d in range(3):
        rig.expect_period(d, "alternate", 0)
        rig.expect_period(d, "cpu-frequency", rates[d])
    enable_gcc_alternate(rig, l=40, alpha=0)
    rig.step(3)
    for d in range(3):
        rig.expect_period(d, "alternate", 768000000)
        rig.expect_period(d, "cpu-frequency", rates[d])
        expected, _ = cycles_and_fraction([(rates[d], rig.last_clock)])
        assert actual_counters(rig, d) == [expected] * CPU_COUNTS[d]

def same_index_writes_do_not_reinitialize_count_or_discard_fraction(rig):
    rig.write(2, PERF_STATE, 1)
    rig.step(1)
    assert actual_counters(rig, 2) == [0]
    for _ in range(11):
        rig.write(2, PERF_STATE, 1)
        assert rig.read(2, PERF_STATE) == 1
    rig.step(1)
    assert actual_counters(rig, 2) == [1]
    rig.step(123)
    assert actual_counters(rig, 2) == [76]
    rig.write(2, PERF_STATE, 1)
    rig.step(1)
    assert actual_counters(rig, 2) == [77]


def raw_cycle_words_wrap_exactly_without_emulating_the_drivers_software_off_by_one(rig):
    for d in range(3):
        rig.write(d, PERF_STATE, 1)
    rate = 614400000
    boundary = ((1 << 32) * BILLION + rate - 1) // rate
    rig.step(boundary - 1)
    for d in range(3):
        assert actual_counters(rig, d) == [0xffffffff] * CPU_COUNTS[d]
    rig.step(1)
    expected, fraction = cycles_and_fraction([(rate, boundary)])
    assert expected == 0 and fraction < rate
    for d in range(3):
        assert actual_counters(rig, d) == [0] * CPU_COUNTS[d]
    rig.step(101)
    expected, _ = cycles_and_fraction([(rate, boundary + 101)])
    for d in range(3):
        assert actual_counters(rig, d) == [expected] * CPU_COUNTS[d]


def stopped_virtual_time_ignores_wall_clock_and_mmio_read_traffic(rig):
    for d, index in enumerate((1, 2, 3)):
        rig.write(d, PERF_STATE, index)
    rig.step(137)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    before = all_perf_and_clocks(rig)
    time.sleep(0.03)
    for _ in range(5):
        assert all_perf_and_clocks(rig) == before
    rig.monitor("cont")
    rig.step(1)
    for d, index in enumerate((1, 2, 3)):
        expected, _ = cycles_and_fraction([(XO * REFERENCE_L[index], 138)])
        assert actual_counters(rig, d) == [expected] * CPU_COUNTS[d]


def system_reset_restores_boot_selection_and_counter_epoch_with_an_external_witness(rig):
    for d, index in enumerate((1, 2, 3)):
        rig.write(d, PERF_STATE, index)
    rig.put(1, "frequency-limit-lval", 8)
    rig.step(137)
    assert any(actual_counters(rig, d)[0] for d in range(3))
    assert rig.read(1, INTR_STATUS) == GT_IRQ
    rig.wire(True)
    rig.raw_write(GCC + 4, 0x34)
    assert rig.raw_read(GCC + 4) == 0x34
    rig.events.clear()
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.raw_read(GCC + 4) == 0,
                 "independent GCC PLL L reset witness")
    rig.monitor("query-status")  # Drain any RESET event following the response.
    assert any(event.get("event") == "RESET" for event in rig.events)
    for d in range(3):
        assert rig.read(d, PERF_STATE) == 0
        assert actual_counters(rig, d) == [0] * CPU_COUNTS[d]
        expected_cap = 8 if d == 1 else 0
        assert rig.get(d, "frequency-limit-lval") == expected_cap
        assert rig.read(d, INTR_STATUS) == 0
        assert rig.get(d, "irq-level") is False
        rig.expect_period(d, "cpu-frequency", XO * (expected_cap or 16))
        rig.expect_period(d, "xo", XO)
        assert rig.read(d, FREQ_LUT + 3 * 4) == reference_frequency_word(3)
        rig.write(d, PERF_STATE, 1)
    assert rig.get(1, "limited") is True
    assert rig.read(1, INTR_STATUS) == 0
    rig.wire(False)
    rig.put(1, "frequency-limit-lval", 0)
    rig.step(2)
    for d in range(3):
        assert actual_counters(rig, d) == [1] * CPU_COUNTS[d]

RUNTIME_PROPERTIES = (
    "performance-index", "requested-lval", "effective-lval",
    "requested-frequency-hz", "frequency-hz", "selected-voltage-microvolt",
    "frequency-known", "limited", "interrupt-status", "irq-level",
    "frequency-limit-lval", "counter", "counter-fraction",
    "counter-value-at-epoch", "counter-fraction-at-epoch", "counter-epoch-ns")
GEOMETRY_PROPERTIES = (
    "enabled-reference", "domain-id", "reference-boot-perf-index",
    "memory-base", "resource-size", "related-cpu-mask", "max-cores",
    "core-counter-count", "lut-max-entries", "lut-row-size", "valid-perf-count",
    "xo-frequency-hz", "alternate-frequency-hz")
CLOCKS = ("xo", "alternate", "cpu-frequency")


def domain_snapshot(rig, domain, *, unknown=False):
    result = {"geometry": {name: rig.get(domain, name)
                           for name in GEOMETRY_PROPERTIES},
              "clock_periods": {name: rig.period(domain, name) for name in CLOCKS},
              "runtime": {}, "runtime_errors": {}, "lut": [], "core_counters": []}
    def read_property(name):
        try:
            return rig.get(domain, name)
        except AssertionError as error:
            assert unknown and TYPE + ": unknown profile" in str(error), str(error)
            result["runtime_errors"][name] = str(error)
            return None
    for name in RUNTIME_PROPERTIES:
        value = read_property(name)
        if value is not None:
            result["runtime"][name] = value
    for row in range(LUT_ROWS):
        result["lut"].append({field: read_property(f"lut{row}-{field}")
                              for field in ("frequency-word", "voltage-word", "frequency-hz")})
    for cpu in range(CPU_COUNTS[domain]):
        result["core_counters"].append(read_property(f"core{cpu}-counter"))
    return result


def snapshot(rig, *, unknown=False):
    return [domain_snapshot(rig, d, unknown=unknown) for d in range(3)]


def concise_state(rig):
    return [{"perf": rig.read(d, PERF_STATE),
             "domain_state": rig.read(d, DOMAIN_STATE),
             "status": rig.read(d, INTR_STATUS),
             "limit": rig.get(d, "frequency-limit-lval"),
             "period": rig.period(d, "cpu-frequency"),
             "counter": rig.get(d, "counter"),
             "fraction": rig.get(d, "counter-fraction")}
            for d in range(3)]


def expect_device_write_error(rig, domain, offset, value, reason):
    before = concise_state(rig)
    lines_before = device_diagnostics(rig.log_path.read_text(errors="replace"))
    rig.write(domain, offset, value)
    lines_after = device_diagnostics(rig.log_path.read_text(errors="replace"))
    expected = f"{TYPE}: write domain {domain} +0x{offset:x} size 4: {reason}"
    assert lines_after[len(lines_before):] == [expected], (
        lines_after[len(lines_before):], expected)
    assert concise_state(rig) == before


def invalid_perf_and_external_limit_values_fail_without_partial_state_changes(rig):
    for d in range(3):
        rig.write(d, PERF_STATE, 2)
        rig.put(d, "frequency-limit-lval", 45)
    rig.step(137)
    for d in range(3):
        for value in (4, 39, 40, 0x100, 0x10000, 0xffffffff):
            expect_device_write_error(rig, d, PERF_STATE, value,
                                      "invalid performance index")
        for value in (256, 0xffffffff):
            before = concise_state(rig)
            try:
                rig.put(d, "frequency-limit-lval", value)
            except AssertionError as error:
                assert TYPE + ": frequency-limit-lval must be in 0..255" in str(error)
            else:
                raise AssertionError("An invalid external limit was accepted")
            assert concise_state(rig) == before
        for value in (1, 2, 8, 0xffffffff):
            expect_device_write_error(rig, d, INTR_CLR, value, "unsupported bits")
    rig.wire(True)


def read_only_registers_and_published_feedback_cannot_forge_lut_rate_or_cycles(rig):
    for d in range(3):
        rig.write(d, PERF_STATE, 3)
    rig.step(137)
    for d in range(3):
        offsets = (ENABLE, DOMAIN_STATE, FREQ_LUT, FREQ_LUT + 39 * 4,
                   VOLT_LUT, VOLT_LUT + 39 * 4, INTR_STATUS)
        offsets += tuple(cpu_counter_offset(i) for i in range(CPU_COUNTS[d]))
        for offset in offsets:
            before_word = rig.read(d, offset)
            expect_device_write_error(rig, d, offset, 0xffffffff, "read-only register")
            assert rig.read(d, offset) == before_word
        for name, value in (("performance-index", 1), ("frequency-hz", 1),
                            ("counter", 0), ("interrupt-status", 4),
                            ("memory-base", 0), ("enabled-reference", False),
                            ("reference-boot-perf-index", 1), ("domain-id", 2)):
            before = concise_state(rig)
            try:
                rig.put(d, name, value)
            except AssertionError as error:
                message = str(error)
                if name in ("enabled-reference", "reference-boot-perf-index", "domain-id"):
                    assert f"property '{name}'" in message and "after it was realized" in message, message
                else:
                    assert f"Property '{TYPE}.{name}' is not writable" in message, message
            else:
                raise AssertionError(f"Protected property {name} accepted a write")
            assert concise_state(rig) == before
    rig.wire(False)


def strict_narrow_and_unaligned_accesses_reject_each_chunk_and_wide_bus_accesses_split(rig):
    domain = 0
    rig.write(domain, PERF_STATE, 2)
    region = TYPE + "-performance-state"
    size_reason = "invalid size (min:4 max:4)"
    cases = (
        ("read", 0, "b", 0, [(0, 1, region, size_reason)]),
        ("write", 0, "b", 1, [(0, 1, region, size_reason)]),
        ("read", 2, "w", 0, [(2, 2, region, size_reason)]),
        ("write", 2, "w", 1, [(2, 2, region, size_reason)]),
        ("read", 1, "l", 0, [(1, 1, region, size_reason), (2, 2, region, size_reason),
                            (BASES[domain] + PERF_STATE + 4, 1, "(null)", "rejected")]),
        ("write", 1, "l", 1, [(1, 1, region, size_reason), (2, 2, region, size_reason),
                             (BASES[domain] + PERF_STATE + 4, 1, "(null)", "rejected")]))
    for operation, delta, width, value, expected_chunks in cases:
        before = concise_state(rig)
        old = core_diagnostics(rig.log_path.read_text(errors="replace"))
        if operation == "read":
            rig.read(domain, PERF_STATE + delta, width)
        else:
            rig.write(domain, PERF_STATE + delta, value, width)
        actual = core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
        expected = [(operation, offset, size, name, reason)
                    for offset, size, name, reason in expected_chunks]
        assert actual == expected, (actual, expected)
        assert concise_state(rig) == before
    for d in range(3):
        # An 8-byte bus read is two legal little-endian 32-bit LUT reads.
        assert rig.read(d, FREQ_LUT, "q") == (
            reference_frequency_word(0) | reference_frequency_word(1) << 32)
        assert rig.read(d, VOLT_LUT, "q") == 750 | 800 << 32
        # An 8-byte bus write at PERF is NOT one atomic device transaction.
        # The first legal 4-byte selection succeeds; the following hole fails.
        old = core_diagnostics(rig.log_path.read_text(errors="replace"))
        rig.write(d, PERF_STATE, (0x12345678 << 32) | 3, "q")
        actual = core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
        assert actual == [("write", BASES[d] + PERF_STATE + 4, 4, "(null)", "rejected")], actual
        assert rig.read(d, PERF_STATE) == 3
        rig.expect_period(d, "cpu-frequency", 1843200000)


def external_limits_use_raw_lvals_preserve_requested_rows_and_integrate_actual_rates(rig):
    domain = rig.irq_domain
    rig.write(domain, PERF_STATE, 3)
    segments = []
    for cap, duration in ((96, 7), (48, 13), (17, 11), (255, 17), (1, 19), (0, 1)):
        rig.put(domain, "frequency-limit-lval", cap)
        effective = min(96, cap) if cap else 96
        expected_hz = XO * effective
        assert rig.read(domain, PERF_STATE) == 3
        assert rig.read(domain, DOMAIN_STATE) == effective
        assert rig.get(domain, "requested-lval") == 96
        assert rig.get(domain, "requested-frequency-hz") == 1843200000
        assert rig.get(domain, "frequency-hz") == expected_hz
        assert rig.get(domain, "limited") is (effective < 96)
        # The voltage table labels the request; there is no PMIC control or
        # inferred voltage interpolation for the host's fractional cap.
        assert rig.get(domain, "selected-voltage-microvolt") == 1000000
        rig.expect_period(domain, "cpu-frequency", expected_hz)
        rig.step(duration)
        segments.append((expected_hz, duration))
        count, fraction = cycles_and_fraction(segments)
        assert actual_counters(rig, domain) == [count] * CPU_COUNTS[domain]
        assert rig.get(domain, "counter") == count
        assert rig.get(domain, "counter-fraction") == fraction
    # L=17 and L=48 are not table rows. A rate cap is not a table index.
    assert [reference_frequency_word(i) & 0xff for i in range(4)] == [16, 32, 64, 96]


def limit_irq_latches_follow_literal_driver_poll_and_w1c_without_fake_rearming(rig):
    d = rig.irq_domain
    rig.write(d, PERF_STATE, 3)
    rig.put(d, "frequency-limit-lval", 32)
    assert rig.read(d, INTR_STATUS) == GT_IRQ
    assert rig.get(d, "limited") is True
    rig.wire(True)
    requested = vendor_frequency_khz(rig.read(d, FREQ_LUT + 3 * 4), XO, 0)
    limited = (rig.read(d, DOMAIN_STATE) & 0xff) * XO // 1000
    assert requested == 1843200 and limited == 614400 and requested != limited
    rig.write(d, INTR_CLR, 0)
    rig.wire(True)
    rig.put(d, "frequency-limit-lval", 0)
    assert rig.read(d, DOMAIN_STATE) * XO // 1000 == requested
    assert rig.get(d, "limited") is False
    assert rig.read(d, INTR_STATUS) == GT_IRQ
    rig.wire(True)  # Restoring frequency alone cannot acknowledge an old event.
    old_clear = rig.read(d, INTR_CLR)
    assert old_clear == 0
    rig.write(d, INTR_CLR, old_clear | GT_IRQ)  # Pinned driver's literal RMW.
    assert rig.read(d, INTR_STATUS) == 0 and rig.read(d, INTR_CLR) == 0
    rig.wire(False)

    rig.put(d, "frequency-limit-lval", 64)
    rig.wire(True)
    rig.write(d, INTR_CLR, GT_IRQ)
    rig.wire(False)
    for _ in range(3):
        rig.put(d, "frequency-limit-lval", 64)
        rig.write(d, PERF_STATE, 3)
        rig.step(17)
        assert rig.read(d, INTR_STATUS) == 0
        rig.wire(False)
    rig.put(d, "frequency-limit-lval", 80)  # Relaxing an existing cap is not a new fall.
    assert rig.get(d, "limited") is True
    rig.wire(False)
    rig.put(d, "frequency-limit-lval", 48)  # Further effective decline latches a new event.
    assert rig.read(d, INTR_STATUS) == GT_IRQ
    rig.wire(True)


def three_domains_keep_limit_state_requested_rows_and_irq_acknowledgments_independent(rig):
    requested = (3, 2, 1)
    limits = (48, 17, 8)
    for d in range(3):
        rig.write(d, PERF_STATE, requested[d])
        rig.put(d, "frequency-limit-lval", limits[d])
    for d in range(3):
        assert rig.read(d, PERF_STATE) == requested[d]
        assert rig.read(d, DOMAIN_STATE) == limits[d]
        assert rig.read(d, INTR_STATUS) == GT_IRQ
        rig.expect_period(d, "cpu-frequency", XO * limits[d])
    rig.wire(True)
    selected = rig.irq_domain
    for d in range(3):
        if d == selected:
            continue
        rig.write(d, INTR_CLR, GT_IRQ)
        rig.put(d, "frequency-limit-lval", 0)
        rig.wire(True)
        assert rig.read(selected, INTR_STATUS) == GT_IRQ
        assert rig.read(selected, DOMAIN_STATE) == limits[selected]
    rig.write(selected, INTR_CLR, GT_IRQ)
    rig.wire(False)
    assert rig.get(selected, "limited") is True
    assert rig.read(selected, PERF_STATE) == requested[selected]
    for d in range(3):
        assert rig.read(d, INTR_STATUS) == 0

def defined_registers(domain):
    return (ENABLE, DOMAIN_STATE,
            *(FREQ_LUT + row * 4 for row in range(LUT_ROWS)),
            *(VOLT_LUT + row * 4 for row in range(LUT_ROWS)),
            INTR_CLR, INTR_STATUS, PERF_STATE,
            *(cpu_counter_offset(cpu) for cpu in range(CPU_COUNTS[domain])))


def default_unknown_profile_rejects_every_modeled_word_and_host_limit_stimulus(args, out):
    rig = CpuFreqRig(args, out / "unknown-default", reference=None)
    try:
        assert sum(len(defined_registers(d)) for d in range(3)) == 263
        for d in range(3):
            assert rig.get(d, "enabled-reference") is False
            rig.expect_period(d, "cpu-frequency", 0)
            old = device_diagnostics(rig.log_path.read_text(errors="replace"))
            expected = []
            for offset in defined_registers(d):
                rig.read(d, offset)
                expected.append(f"{TYPE}: read domain {d} +0x{offset:x} size 4: unknown profile")
                rig.write(d, offset, 0xffffffff)
                expected.append(f"{TYPE}: write domain {d} +0x{offset:x} size 4: unknown profile")
            actual = device_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
            assert actual == expected, (actual, expected)
            try:
                rig.put(d, "frequency-limit-lval", 32)
            except AssertionError as error:
                assert TYPE + ": external limit requires a realized reference profile" in str(error)
            else:
                raise AssertionError("The unknown profile accepted a limit producer")
        rig.step(1000000)
        state = snapshot(rig, unknown=True)
        for d, item in enumerate(state):
            assert not item["runtime"]
            assert len(item["runtime_errors"]) == len(RUNTIME_PROPERTIES) + 120 + CPU_COUNTS[d]
            assert item["clock_periods"]["cpu-frequency"] == 0
        (rig.directory / "unknown-qom.json").write_text(json.dumps(state, indent=2) + "\n")
        rig.wire(False)
    finally:
        rig.close()


def explicit_boot_selection_is_real_and_bad_profile_configuration_fails_realization(args, out):
    rig = CpuFreqRig(args, out / "explicit-boot-selection", boot_index=3)
    try:
        for d in range(3):
            assert rig.get(d, "reference-boot-perf-index") == 3
            assert rig.read(d, PERF_STATE) == 3
            rig.expect_period(d, "cpu-frequency", 1843200000)
            assert actual_counters(rig, d) == [0] * CPU_COUNTS[d]
        rig.step(1250)
        for d in range(3):
            assert actual_counters(rig, d) == [2304] * CPU_COUNTS[d]
            rig.write(d, PERF_STATE, 0)
        rig.raw_write(GCC + 4, 0x34)
        rig.events.clear()
        rig.monitor("system_reset")
        rig.wait_for(lambda: rig.raw_read(GCC + 4) == 0, "GCC reset witness")
        rig.monitor("query-status")
        assert any(event.get("event") == "RESET" for event in rig.events)
        for d in range(3):
            assert rig.read(d, PERF_STATE) == 3
            assert actual_counters(rig, d) == [0] * CPU_COUNTS[d]
            rig.expect_period(d, "cpu-frequency", 1843200000)
    finally:
        rig.close()
    diagnostic = (TYPE + ": reference-boot-perf-index requires 0..3 and"
                  " must be zero in the unknown profile")
    for label, reference, boot_index in (("past-last-state", True, 4),
                                         ("reserved-high-bits", True, 0xffffffff),
                                         ("unknown-nonzero-boot", False, 1)):
        directory = out / ("invalid-configuration-" + label)
        directory.mkdir()
        command = base_command(args, reference=reference, boot_index=boot_index)
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=10)
        (directory / "qemu.stdout").write_bytes(result.stdout)
        (directory / "qemu.stderr").write_bytes(result.stderr)
        assert result.returncode == 1, (label, result.returncode, result.stderr.decode(errors="replace"))
        assert diagnostic in result.stderr.decode(errors="replace")


def paused_tcg_retains_normal_host_limit_inputs_without_executing_a_guest_cpu(args, out):
    rig = CpuFreqRig(args, out / "paused-tcg", accel="tcg", boot_index=2)
    try:
        assert rig.monitor("query-status")["running"] is False
        for d in range(3):
            assert rig.read(d, PERF_STATE) == 2
            assert rig.get(d, "counter") == 0
        rig.put(0, "frequency-limit-lval", 17)
        assert rig.get(0, "frequency-hz") == 326400000
        assert rig.get(0, "requested-frequency-hz") == 1228800000
        assert rig.read(0, PERF_STATE) == 2
        rig.wire(True)
        time.sleep(0.03)
        for d in range(3):
            assert rig.get(d, "counter") == 0
            assert rig.get(d, "counter-fraction") == 0
        assert rig.monitor("query-status")["running"] is False
        (rig.directory / "paused-tcg.json").write_text(json.dumps({
            "guest_cpu_executed": False, "qmp_cont_sent": False,
            "normal_host_limit_input_available": True,
            "requested_index_preserved": True,
            "real_output_irq_observed": True,
            "wall_time_did_not_add_cycles": True}, indent=2) + "\n")
    finally:
        rig.close()


def source_bounded_windows_preserve_unused_enable_aggregate_counter_and_legacy_holes(args, out):
    rig = CpuFreqRig(args, out / "sparse-window-boundaries")
    try:
        masks = (0x0f, 0x70, 0x80)
        before = concise_state(rig)
        for d in range(3):
            expected_geometry = {
                "memory-base": BASES[d], "resource-size": 0x1000,
                "domain-id": d, "related-cpu-mask": masks[d], "max-cores": 4,
                "core-counter-count": CPU_COUNTS[d], "lut-max-entries": 40,
                "lut-row-size": 4, "valid-perf-count": 4}
            for name, value in expected_geometry.items():
                assert rig.get(d, name) == value, (d, name, value)
            holes = (4, 0x1c, 0x24, 0xfc, 0x1a0, 0x1fc, 0x2a0,
                     0x300, INTR_EN, 0x310, 0x31c, 0x324, 0x3c0, CYCLE_CNTR,
                     cpu_counter_offset(CPU_COUNTS[d]), 0x920, 0x9c0, 0xffc)
            old = core_diagnostics(rig.log_path.read_text(errors="replace"))
            expected = []
            for offset in holes:
                assert offset not in defined_registers(d)
                rig.read(d, offset)
                rig.write(d, offset, 0xffffffff)
                expected += [(operation, BASES[d] + offset, 4, "(null)", "rejected")
                             for operation in ("read", "write")]
            actual = core_diagnostics(rig.log_path.read_text(errors="replace"))[len(old):]
            assert actual == expected, (actual, expected)
            assert concise_state(rig) == before
        assert [len(defined_registers(d)) for d in range(3)] == [89, 88, 86]
        assert sum(len(defined_registers(d)) * 4 for d in range(3)) == 1052
    finally:
        rig.close()


def all_three_domains_drive_the_actual_dt_gic_routes_without_irq_interception(args, out):
    rig = CpuFreqRig(args, out / "real-gic-routes", irq_domain=None)
    try:
        # Secure QTest distributor reads, as in the already verified AOP and
        # TSENS fixtures. DT SPIs 30/31/19 become INTIDs 62/63/51.
        pending = 0x17a00000 + 0x200 + 4
        clear = 0x17a00000 + 0x280 + 4
        bits = (1 << 30, 1 << 31, 1 << 19)
        all_bits = bits[0] | bits[1] | bits[2]
        assert rig.raw_read(pending) & all_bits == 0
        for d, cap in enumerate((16, 8, 4)):
            rig.write(d, PERF_STATE, 3)
            rig.put(d, "frequency-limit-lval", cap)
        assert rig.raw_read(pending) & all_bits == all_bits
        remaining = all_bits
        for d in (1, 2, 0):
            rig.write(d, INTR_CLR, GT_IRQ)
            rig.raw_write(clear, bits[d])
            remaining &= ~bits[d]
            assert rig.raw_read(pending) & all_bits == remaining
            assert rig.get(d, "irq-level") is False
            assert rig.get(d, "limited") is True
        (rig.directory / "gic-routing.json").write_text(json.dumps({
            "output_intercepted": False, "dt_spis": [30, 31, 19],
            "gic_intids": [62, 63, 51], "gic_pending_register": pending,
            "all_three_assertions_and_independent_deassertions_observed": True},
            indent=2) + "\n")
    finally:
        rig.close()

ORDINARY_TESTS = [
    cold_reference_exposes_the_explicit_xo_table_with_a_zero_initial_counter,
    all_forty_raw_rows_follow_the_explicit_profile_and_actual_dt_core_count,
    production_lut_reading_stops_at_the_duplicate_row_and_preserves_units,
    legal_perf_indices_change_real_clock_outputs_and_keep_domains_independent,
    per_cpu_counters_keep_fraction_through_irregular_steps_and_repeated_reads,
    perf_switches_settle_the_old_rate_and_preserve_the_fractional_cycle_phase,
    changing_the_real_gcc_alternate_does_not_replace_the_selected_xo_source,
    same_index_writes_do_not_reinitialize_count_or_discard_fraction,
    raw_cycle_words_wrap_exactly_without_emulating_the_drivers_software_off_by_one,
    stopped_virtual_time_ignores_wall_clock_and_mmio_read_traffic,
    system_reset_restores_boot_selection_and_counter_epoch_with_an_external_witness,
    invalid_perf_and_external_limit_values_fail_without_partial_state_changes,
    read_only_registers_and_published_feedback_cannot_forge_lut_rate_or_cycles,
    strict_narrow_and_unaligned_accesses_reject_each_chunk_and_wide_bus_accesses_split,
    external_limits_use_raw_lvals_preserve_requested_rows_and_integrate_actual_rates,
    limit_irq_latches_follow_literal_driver_poll_and_w1c_without_fake_rearming,
    three_domains_keep_limit_state_requested_rows_and_irq_acknowledgments_independent,
]
SPECIAL_TESTS = [
    default_unknown_profile_rejects_every_modeled_word_and_host_limit_stimulus,
    explicit_boot_selection_is_real_and_bad_profile_configuration_fails_realization,
    paused_tcg_retains_normal_host_limit_inputs_without_executing_a_guest_cpu,
    source_bounded_windows_preserve_unused_enable_aggregate_counter_and_legacy_holes,
    all_three_domains_drive_the_actual_dt_gic_routes_without_irq_interception,
]
EXPECTED_CHECKS = 30
PAYLOAD_BYTES = 84
LUT_IDENTITY = 0x584f3031
PERF_OFFSET = 48
FRACTION_OFFSET = 64

ANCHOR_PROPERTIES = ("counter-value-at-epoch", "counter-fraction-at-epoch", "counter-epoch-ns")
SERIAL_FIELDS = (
    ("profile", 0, 4), ("domain", 4, 4), ("memory_base", 8, 8),
    ("resource_size", 16, 4), ("related_cpu_mask", 20, 4), ("max_cores", 24, 4),
    ("cpu_count", 28, 4), ("boot_perf_index", 32, 4), ("lut_identity", 36, 4),
    ("lut_entries", 40, 4), ("lut_stride", 44, 4), ("performance_index", 48, 4),
    ("frequency_limit_lval", 52, 4), ("interrupt_status", 56, 4),
    ("counter", 60, 4), ("fraction", 64, 4),
    ("xo_clock_period", 68, 8), ("alternate_clock_period", 76, 8))


def anchors(rig):
    return [{name: rig.get(d, name) for name in ANCHOR_PROPERTIES} for d in range(3)]


def packed_domain(domain, state, *, reference, boot_index):
    g, runtime, periods = state["geometry"], state["runtime"], state["clock_periods"]
    assert g["enabled-reference"] is reference
    assert g["domain-id"] == domain
    assert g["memory-base"] == BASES[domain]
    assert g["related-cpu-mask"] == (0x0f, 0x70, 0x80)[domain]
    assert g["max-cores"] == 4 and g["core-counter-count"] == CPU_COUNTS[domain]
    assert g["lut-max-entries"] == 40 and g["lut-row-size"] == 4
    assert g["reference-boot-perf-index"] == boot_index
    data = struct.pack(">IIQ8I", int(reference), domain, g["memory-base"],
                       g["resource-size"], g["related-cpu-mask"], g["max-cores"],
                       g["core-counter-count"], boot_index, LUT_IDENTITY,
                       g["lut-max-entries"], g["lut-row-size"])
    assert len(data) == PERF_OFFSET
    if reference:
        data += struct.pack(">5I", runtime["performance-index"],
                            runtime["frequency-limit-lval"], runtime["interrupt-status"],
                            runtime["counter"], runtime["counter-fraction"])
    else:
        # Unknown runtime getters actually refused access. These zeros are
        # the validated disabled-profile serialization invariant, not fake
        # QOM observations or a claim that the guest read valid registers.
        assert not runtime and state["runtime_errors"]
        data += struct.pack(">5I", 0, 0, 0, 0, 0)
    assert len(data) == 68
    # hw/core/clock-vmstate.c v0 carries one period u64. The attached inputs
    # use the default 1/1 multiplier/divider and have no optional subsection.
    data += struct.pack(">QQ", periods["xo"], periods["alternate"])
    assert len(data) == PAYLOAD_BYTES
    return data


def actual_sections(path, source_state, *, reference, boot_index):
    data = path.read_bytes()
    name = TYPE.encode("ascii")
    found = []
    for domain in range(3):
        needle = bytes([len(name)]) + name + struct.pack(">II", domain, 1)
        expected = packed_domain(domain, source_state[domain],
                                 reference=reference, boot_index=boot_index)
        matches = []
        position = 0
        while True:
            index = data.find(needle, position)
            if index < 0:
                break
            header = index - 5
            payload = index + len(needle)
            if header >= 8 and data[header] in (1, 4) and data[payload:payload + PAYLOAD_BYTES] == expected:
                matches.append((header, payload))
            position = index + 1
        assert len(matches) == 1, (domain, matches)
        header, payload = matches[0]
        actual = data[payload:payload + PAYLOAD_BYTES]
        payload_path = path.with_name(path.name + f".domain{domain}.payload.bin")
        payload_path.write_bytes(actual)
        decoded = {field: int.from_bytes(actual[offset:offset + width], "big")
                   for field, offset, width in SERIAL_FIELDS}
        assert decoded["profile"] == int(reference)
        assert decoded["domain"] == domain and decoded["memory_base"] == BASES[domain]
        found.append({
            "domain": domain, "instance_id": domain, "section_version": 1,
            "section_name": TYPE, "section_header_offset": header,
            "section_id": int.from_bytes(data[header + 1:header + 5], "big"),
            "payload_offset": payload, "payload_bytes": PAYLOAD_BYTES,
            "payload_hex": actual.hex(), "payload_file": payload_path.name,
            "decoded_fields": decoded,
            "all_serialized_fields_verified": True,
            "runtime_matches_actual_source_qom": reference,
            "unknown_runtime_zero_invariant_verified": not reference,
            "input_clock_periods_match_actual_source_clock_objects": True})
    ordered = sorted(found, key=lambda section: section["payload_offset"])
    assert all(a["payload_offset"] + PAYLOAD_BYTES <= b["section_header_offset"]
               for a, b in zip(ordered, ordered[1:]))
    path.with_name(path.name + ".sections.json").write_text(json.dumps({
        "stream_sha256": digest(path), "stream_bytes": len(data),
        "field_layout": [{"name": name, "offset": offset, "bytes": width}
                         for name, offset, width in SERIAL_FIELDS],
        "sections": found}, indent=2) + "\n")
    return found


def save_real_state(source, path, witness, expected_counts=None):
    source.raw_write(RAM_WITNESS, witness)
    source.monitor("stop")
    stop_status = source.monitor("query-status")
    assert stop_status["running"] is False
    # Save full pre-save evidence without concealing the pre_save callback.
    # Read anchors first; every subsequent QOM getter must leave them intact.
    old_anchors = anchors(source) if source.reference else None
    before = snapshot(source, unknown=not source.reference)
    if source.reference:
        assert anchors(source) == old_anchors
        for d in range(3):
            assert {name: before[d]["runtime"][name] for name in ANCHOR_PROPERTIES} == old_anchors[d]
            count, fraction = expected_counts[d]
            assert before[d]["runtime"]["counter"] == count
            assert before[d]["runtime"]["counter-fraction"] == fraction
        assert any(
            before[d]["runtime"]["counter"] != old_anchors[d]["counter-value-at-epoch"] or
            before[d]["runtime"]["counter-fraction"] != old_anchors[d]["counter-fraction-at-epoch"]
            for d in range(3)), "Source state did not require a fresh pre_save settlement"
    before_file = source.directory / "source-qom-before-save.json"
    before_file.write_text(json.dumps({
        "query_status": stop_status, "source_virtual_ns": source.last_clock,
        "anchors_before_qom_observation": old_anchors,
        "qom_observation_left_anchors_unchanged": source.reference,
        "domains": before}, indent=2) + "\n")
    source.monitor("migrate", file_channels(path))
    sent = wait_migration(source)
    assert sent["status"] == "completed", sent
    final_status = source.monitor("query-status")
    assert final_status["status"] == "postmigrate" and final_status["running"] is False
    assert path.stat().st_size > 0
    after = snapshot(source, unknown=not source.reference)
    if source.reference:
        for d in range(3):
            old, new = before[d], after[d]
            assert old["geometry"] == new["geometry"]
            assert old["clock_periods"] == new["clock_periods"]
            assert old["lut"] == new["lut"] and old["core_counters"] == new["core_counters"]
            for name, value in old["runtime"].items():
                if name not in ANCHOR_PROPERTIES:
                    assert new["runtime"][name] == value, (d, name, value, new["runtime"][name])
            assert new["runtime"]["counter-value-at-epoch"] == new["runtime"]["counter"]
            assert new["runtime"]["counter-fraction-at-epoch"] == new["runtime"]["counter-fraction"]
            assert new["runtime"]["counter-epoch-ns"] == source.last_clock
    after_file = source.directory / "source-qom-after-save.json"
    after_file.write_text(json.dumps({
        "query_status": final_status, "source_virtual_ns": source.last_clock,
        "domains": after}, indent=2) + "\n")
    (source.directory / "source-query-migrate.json").write_text(json.dumps(sent, indent=2) + "\n")
    sections = actual_sections(path, after, reference=source.reference,
                               boot_index=source.boot_index)
    evidence = {
        "source_virtual_ns": source.last_clock,
        "source_counter_mmio_before_save": False,
        "source_qom_projection_before_save": True,
        "source_qom_did_not_settle_counter_before_save": source.reference,
        "pre_save_freshness_observed": source.reference,
        "source_qom_before_file": source.directory.name + "/" + before_file.name,
        "source_qom_after_file": source.directory.name + "/" + after_file.name,
        "source_query_migrate_file": source.directory.name + "/source-query-migrate.json"}
    return after, sent, sections, evidence

def prepare_target(target, destination_ns, *, clear_irqs):
    target.set_clock(destination_ns)
    for d in range(3):
        target.write(d, PERF_STATE, (3, 0, 1)[d])
        target.put(d, "frequency-limit-lval", (8, 5, 7)[d])
        if clear_irqs:
            target.write(d, INTR_CLR, GT_IRQ)
    target.raw_write(RAM_WITNESS, 0xdeadbeef)
    target.wire(not clear_irqs)
    (target.directory / "destination-before-load.json").write_text(json.dumps({
        "destination_virtual_ns": destination_ns, "query_status": target.monitor("query-status"),
        "domains": snapshot(target), "actual_irq_level": not clear_irqs},
        indent=2) + "\n")


def restore_real_state(target, path, source_state, destination_ns, witness, irq_level):
    assert target.last_clock == destination_ns
    target.monitor("migrate-incoming", file_channels(path))
    got = wait_migration(target)
    assert got["status"] == "completed", got
    status = target.monitor("query-status")
    assert status["running"] is False
    # First post-load device observations are pure QOM and actual Clock
    # objects. No EPSS MMIO, cont or post-load rate-setter may repair state.
    restored = snapshot(target)
    for d in range(3):
        expected, actual = source_state[d], restored[d]
        assert actual["geometry"] == expected["geometry"]
        assert actual["clock_periods"] == expected["clock_periods"]
        assert actual["lut"] == expected["lut"]
        assert actual["core_counters"] == expected["core_counters"]
        assert actual["runtime_errors"] == expected["runtime_errors"] == {}
        for name, value in expected["runtime"].items():
            if name != "counter-epoch-ns":
                assert actual["runtime"][name] == value, (d, name, value, actual["runtime"][name])
        assert actual["runtime"]["counter-epoch-ns"] == destination_ns
    # RAM drains only the QTest IRQ stream; it cannot run the EPSS handler.
    assert target.raw_read(RAM_WITNESS) == witness
    target.wire(irq_level)
    (target.directory / "destination-before-first-epss-mmio.json").write_text(
        json.dumps({"destination_virtual_ns": destination_ns, "query_status": status,
                    "domains": restored, "actual_irq_level": irq_level,
                    "epss_mmio_since_incoming": False,
                    "qmp_cont_since_incoming": False}, indent=2) + "\n")
    (target.directory / "destination-query-migrate.json").write_text(json.dumps(got, indent=2) + "\n")
    return got


def corrupt_single_field(original, changed_path, section, kind):
    data = original.read_bytes()
    assert data[section["payload_offset"]:section["payload_offset"] + PAYLOAD_BYTES].hex() == section["payload_hex"]
    if kind == "performance-index":
        relative = PERF_OFFSET
        old = int.from_bytes(data[section["payload_offset"] + relative:
                                  section["payload_offset"] + relative + 4], "big")
        assert old == 1 and section["domain"] == 0
        new = old | 4
    elif kind == "counter-fraction":
        relative = FRACTION_OFFSET
        old = int.from_bytes(data[section["payload_offset"] + relative:
                                  section["payload_offset"] + relative + 4], "big")
        assert old == 843200000 and section["domain"] == 2 and old < BILLION
        new = old | 0x80000000
    else:
        raise AssertionError(kind)
    field = section["payload_offset"] + relative
    changed = bytearray(data)
    changed[field:field + 4] = struct.pack(">I", new)
    assert changed[:field] == data[:field] and changed[field + 4:] == data[field + 4:]
    differences = [{"offset": i, "old_byte": before, "new_byte": after}
                   for i, (before, after) in enumerate(zip(data, changed)) if before != after]
    assert len(differences) == 1 and field <= differences[0]["offset"] < field + 4
    assert (old ^ new).bit_count() == 1
    changed_path.write_bytes(changed)
    assert original.read_bytes() == data and len(changed) == len(data)
    return {"domain": section["domain"], "field": kind,
            "section_header_offset": section["section_header_offset"],
            "payload_offset": section["payload_offset"], "field_relative_offset": relative,
            "field_offset": field, "modified_field_bytes": 4,
            "old_value": old, "new_value": new,
            "old_field_hex": data[field:field + 4].hex(),
            "new_field_hex": bytes(changed[field:field + 4]).hex(),
            "original_payload_hex": section["payload_hex"],
            "modified_payload_hex": bytes(changed[section["payload_offset"]:
                                                section["payload_offset"] + PAYLOAD_BYTES]).hex(),
            "complete_file_byte_differences": differences,
            "only_one_field_modified": True, "only_one_bit_modified": True}


def expect_incoming_failure(target, path, diagnostic):
    assert target.process.poll() is None
    request_sent = False
    command_error = None
    try:
        request_sent = True
        target.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as error:
        command_error = str(error)
    code = target.process.wait(timeout=20)
    stderr = target.log_path.read_text(errors="replace")
    assert request_sent and code == 1, (request_sent, code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {"destination_exit": code, "request_sent": request_sent,
            "command_error": command_error, "device": TYPE,
            "expected_diagnostic": diagnostic, "specific_load_failure_observed": True}

def assert_projected_phase(rig, domain, expected):
    count, fraction = expected
    assert rig.get(domain, "counter") == count, (domain, count, rig.get(domain, "counter"))
    assert rig.get(domain, "counter-fraction") == fraction
    for cpu in range(CPU_COUNTS[domain]):
        assert rig.get(domain, f"core{cpu}-counter") == count


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = CpuFreqRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, source, target, sent, got, sections, evidence, destination_ns):
        record = {
            "name": name, "passed": True,
            "source_status": sent["status"], "target_status": got["status"],
            "source": sent, "destination": got,
            "source_directory": source.directory.name, "target_directory": target.directory.name,
            "destination_virtual_ns": destination_ns,
            "different_virtual_origins": source.last_clock != destination_ns,
            "snapshot_file": path.name, "snapshot_sha256": digest(path),
            "snapshot_bytes": path.stat().st_size, "stream_modified": False,
            "domain_sections_verified": 3, "payload_bytes_per_domain": PAYLOAD_BYTES,
            "sections": sections,
            "target_epss_mmio_before_first_observation": False,
            "target_real_clock_observed_before_first_mmio": True,
            "target_real_irq_observed_before_first_mmio": True,
            "destination_state_was_different_before_load": True,
            "target_qom_before_first_mmio_file": target.directory.name +
                                               "/destination-before-first-epss-mmio.json",
            "target_query_migrate_file": target.directory.name + "/destination-query-migrate.json",
            "continued_after_restore": False,
            **evidence}
        report["migrations"].append(record)
        return record

    try:
        source = new("migration-fraction-source")
        for d, index in enumerate((1, 2, 3)):
            source.write(d, PERF_STATE, index)
        source.step(1)
        expected = [(0, 614400000), (1, 228800000), (1, 843200000)]
        assert expected == [cycles_and_fraction([(XO * lval, 1)]) for lval in (32, 64, 96)]
        first_path = out / "cpufreq-hw-v1-fractional-rates.migration"
        state, sent, sections, evidence = save_real_state(
            source, first_path, 0x43504631, expected)
        target = new("migration-fraction-target", incoming=True)
        destination_ns = 987654321
        prepare_target(target, destination_ns, clear_irqs=False)
        got = restore_real_state(target, first_path, state, destination_ns, 0x43504631, False)
        first = completed("v1_three_domain_fractional_cycles_clock_and_ram_roundtrip",
                          first_path, source, target, sent, got, sections, evidence, destination_ns)
        check("v1_fractional_state_and_real_clock_restore_before_first_epss_mmio",
              source_virtual_ns=1, destination_virtual_ns=destination_ns)

        target.monitor("cont")
        target.step(1)
        for d, lval in enumerate((32, 64, 96)):
            assert_projected_phase(target, d, cycles_and_fraction([(XO * lval, 2)]))
            assert target.get(d, "counter-epoch-ns") == destination_ns
        # Only now may a guest EPSS access change the restored configuration.
        target.write(0, PERF_STATE, 0)
        target.step(125)
        for d, segments in enumerate((
                [(614400000, 2), (307200000, 125)],
                [(1228800000, 127)], [(1843200000, 127)])):
            expected_continued = cycles_and_fraction(segments)
            assert_projected_phase(target, d, expected_continued)
            assert actual_counters(target, d) == [expected_continued[0]] * CPU_COUNTS[d]
        for d, phase in enumerate(expected):
            assert_projected_phase(source, d, phase)
        first["continued_after_restore"] = True
        check("v1_exact_remaining_fraction_and_new_perf_selection_continue_at_new_epoch",
              explicit_virtual_increment_ns=1)

        source2 = new("migration-limits-source", irq_domain=2)
        enable_gcc_alternate(source2)
        assert source2.last_clock == 5000
        for d, (index, cap) in enumerate(zip((3, 2, 1), (48, 17, 8))):
            source2.write(d, PERF_STATE, index)
            source2.put(d, "frequency-limit-lval", cap)
        source2.write(0, INTR_CLR, GT_IRQ)
        source2.step(13)
        source2.wire(True)  # RAM-only drain, not a counter/sample read.
        expected2 = [(1547, 980800000), (1540, 243200000), (1537, 996800000)]
        assert expected2 == [cycles_and_fraction([(307200000, 5000), (XO * cap, 13)])
                             for cap in (48, 17, 8)]
        second_path = out / "cpufreq-hw-v1-latched-limits.migration"
        state2, sent2, sections2, evidence2 = save_real_state(
            source2, second_path, 0x43504632, expected2)
        assert [item["runtime"]["interrupt-status"] for item in state2] == [0, 4, 4]
        target2 = new("migration-limits-target", incoming=True, irq_domain=2)
        destination_ns2 = 73
        prepare_target(target2, destination_ns2, clear_irqs=True)
        got2 = restore_real_state(target2, second_path, state2, destination_ns2, 0x43504632, True)
        second = completed("v1_external_caps_latched_irqs_provider_clock_and_ram_roundtrip",
                           second_path, source2, target2, sent2, got2, sections2, evidence2, destination_ns2)
        check("v1_latched_and_cleared_irqs_restore_with_actual_clock_before_first_epss_mmio",
              source_virtual_ns=5013, destination_virtual_ns=destination_ns2,
              source_irq_statuses=[0, 4, 4])

        target2.monitor("cont")
        target2.step(1)
        for d, cap in enumerate((48, 17, 8)):
            assert_projected_phase(target2, d,
                cycles_and_fraction([(307200000, 5000), (XO * cap, 14)]))
            assert target2.get(d, "counter-epoch-ns") == destination_ns2
        assert target2.get(0, "interrupt-status") == 0  # W1C must not be undone by load.
        assert target2.get(1, "interrupt-status") == GT_IRQ
        target2.wire(True)
        # First guest device access follows the independent phase/IRQ proof.
        target2.write(2, INTR_CLR, GT_IRQ)
        target2.wire(False)
        target2.put(2, "frequency-limit-lval", 0)
        target2.expect_period(2, "cpu-frequency", 614400000)
        target2.step(5)
        target2.put(2, "frequency-limit-lval", 4)
        target2.wire(True)
        target2.step(13)
        last_phase = cycles_and_fraction(
            [(307200000, 5000), (153600000, 14), (614400000, 5), (76800000, 13)])
        assert_projected_phase(target2, 2, last_phase)
        assert actual_counters(target2, 2) == [last_phase[0]]
        assert target2.read(2, PERF_STATE) == 1
        assert target2.read(2, DOMAIN_STATE) == 4
        assert target2.read(2, INTR_STATUS) == GT_IRQ
        for d, cap in enumerate((48, 17)):
            assert_projected_phase(target2, d,
                cycles_and_fraction([(307200000, 5000), (XO * cap, 32)]))
        for d, phase in enumerate(expected2):
            assert_projected_phase(source2, d, phase)
        second["continued_after_restore"] = True
        check("v1_capped_fraction_irq_acknowledgment_and_new_limit_events_continue",
              first_virtual_increment_ns=1)

        unknown = new("migration-unknown-source", reference=False)
        unknown.step(12345)
        unknown_path = out / "cpufreq-hw-v1-unknown-profile.migration"
        unknown_state, unknown_sent, unknown_sections, unknown_evidence = save_real_state(
            unknown, unknown_path, 0x43504630)
        report["unknown_profile_source"] = {
            "source_status": unknown_sent["status"],
            "source_directory": unknown.directory.name,
            "snapshot_file": unknown_path.name, "snapshot_bytes": unknown_path.stat().st_size,
            "snapshot_sha256": digest(unknown_path), "sections": unknown_sections,
            "guest_runtime_qom_access_refused": all(item["runtime_errors"] for item in unknown_state),
            "completed_roundtrip": False, **unknown_evidence}
        for name, path, source_reference, target_reference, original_status in (
                ("reference_to_unknown_profile_is_rejected", first_path, True, False, sent),
                ("unknown_to_reference_profile_is_rejected", unknown_path, False, True, unknown_sent)):
            rejected = new("migration-" + name, incoming=True, reference=target_reference)
            for d in range(3):
                assert rejected.get(d, "enabled-reference") is target_reference
            original_hash = digest(path)
            # load_uint32_equal uses hexadecimal; these markers are 0/1.
            diagnostic = f"{int(target_reference):x} != {int(source_reference):x}"
            failure = expect_incoming_failure(rejected, path, diagnostic)
            assert digest(path) == original_hash
            report["rejected_migrations"].append({
                "name": name, "passed": True, "actual_source_stream": True,
                "stream_modified": False, "source_status": original_status["status"],
                "source_reference": source_reference, "target_reference": target_reference,
                "target_directory": rejected.directory.name,
                "snapshot_file": path.name, "snapshot_sha256": original_hash,
                "snapshot_bytes": path.stat().st_size, "profile_mismatch": diagnostic, **failure})
            check(name, stream_modified=False)

        original_hash = digest(first_path)
        for kind, section, diagnostic in (
                ("performance-index", sections[0], TYPE + ": invalid migrated performance index"),
                ("counter-fraction", sections[2], TYPE + ": invalid migrated reference state")):
            changed = out / ("cpufreq-hw-v1-corrupted-" + kind + ".migration")
            mutation = corrupt_single_field(first_path, changed, section, kind)
            assert digest(first_path) == original_hash
            rejected = new("migration-corrupt-" + kind, incoming=True)
            failure = expect_incoming_failure(rejected, changed, diagnostic)
            assert digest(first_path) == original_hash
            report["corrupted_stream_rejections"].append({
                "name": kind, "passed": True, "actual_source_stream": True,
                "stream_modified": True, "source_status": sent["status"],
                "target_directory": rejected.directory.name,
                "legitimate_alternate_configuration": False,
                "original_stream_unchanged": True,
                "original_snapshot_file": first_path.name,
                "corrupted_snapshot_file": changed.name,
                "original_snapshot_sha256": original_hash,
                "corrupted_snapshot_sha256": digest(changed),
                "original_snapshot_bytes": first_path.stat().st_size,
                "corrupted_snapshot_bytes": changed.stat().st_size, **mutation, **failure})
            check("one_real_" + kind.replace("-", "_") + "_field_corruption_is_rejected",
                  only_one_field_modified=True, only_one_bit_modified=True)
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
        "schema": "tb371fc-cpufreq-hw-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [],
        "expected_completed_migrations": 2, "migrations": [],
        "expected_rejected_migrations": 2, "rejected_migrations": [],
        "expected_corrupted_stream_rejections": 2, "corrupted_stream_rejections": [],
        "domain_count": 3, "cpu_count": 8, "per_cpu_counter_count": 8,
        "register_count": 263, "sparse_window_count": 24, "mapped_bytes": 1052,
        "registers_per_domain": [89, 88, 86], "windows_per_domain": 8,
        "lut_rows_per_domain": 40, "lut_row_bytes": 4,
        "valid_perf_states_per_domain": 4, "irq_count": 3,
        "input_clock_count": 6, "output_clock_count": 3,
        "vmstate_version": 1, "vmstate_payload_bytes_per_domain": PAYLOAD_BYTES,
        "reference_profile_is_default": False,
        "reference_lut_is_synthetic": True,
        "factory_lut_observed": False,
        "counter_source_is_virtual_time": True,
        "counter_measures_retired_cpu_cycles": False,
        "cpu_execution_speed_controlled": False,
        "analogue_voltage_control_implemented": False,
        "hardware_thermal_throttling_observed": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "reference_lut_lvals": list(REFERENCE_L),
        "reference_lut_frequency_words": [reference_frequency_word(i) for i in range(4)],
        "reference_lut_voltage_words": list(REFERENCE_MV),
        "reference_max_cores": list(MAX_CORES), "actual_domain_cpu_counts": list(CPU_COUNTS),
        "reference_lut_identity": LUT_IDENTITY,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "source_commit": SOURCE_COMMIT, "source_sha256": SOURCE_SHA256,
        "source_url": "https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/"
                      + SOURCE_COMMIT + "/drivers/cpufreq/qcom-cpufreq-hw.c",
        "dtb_sha256": DTB_SHA256,
        "dt_evidence_blob": "541c58de5c6711215cd6ebc24e48a11ea4a69127",
        "dt_capture_sha256": "815874739b5ccfd4f248fd953849f775387dab53b2859dff508bee74ae74136d",
        "scope": "Actual compiled three-domain EPSS reference LUT transactions, "
                 "requested and capped Clock rates, exact virtual-cycle phase, "
                 "source-bounded IRQs and state/RAM migration; no physical DVFS attestation"}
    current = None
    start = time.monotonic()
    irq_one = {
        "system_reset_restores_boot_selection_and_counter_epoch_with_an_external_witness",
        "limit_irq_latches_follow_literal_driver_poll_and_w1c_without_fake_rearming"}
    irq_two = {
        "three_domains_keep_limit_state_requested_rows_and_irq_acknowledgments_independent"}
    try:
        assert len(ORDINARY_TESTS) == 17 and len(SPECIAL_TESTS) == 5
        for test in ORDINARY_TESTS:
            current = test.__name__
            rig = None
            try:
                domain = 2 if current in irq_two else 1 if current in irq_one else 0
                rig = CpuFreqRig(args, out / current, irq_domain=domain)
                test(rig)
                report["checks"].append({"name": current, "passed": True,
                                         "intercepted_irq_domain": domain})
            finally:
                if rig:
                    rig.close()
        for test in SPECIAL_TESTS:
            current = test.__name__
            test(args, out)
            report["checks"].append({"name": current, "passed": True})
        current = "actual_migration_and_rejections"
        migration_checks(args, out, report)
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 2 and
            all(item["source_status"] == item["target_status"] == "completed"
                and item["domain_sections_verified"] == 3
                and item["payload_bytes_per_domain"] == PAYLOAD_BYTES
                and item["different_virtual_origins"] is True
                and item["pre_save_freshness_observed"] is True
                and item["source_counter_mmio_before_save"] is False
                and item["source_qom_projection_before_save"] is True
                and item["source_qom_did_not_settle_counter_before_save"] is True
                and item["target_epss_mmio_before_first_observation"] is False
                and item["target_real_clock_observed_before_first_mmio"] is True
                and item["target_real_irq_observed_before_first_mmio"] is True
                and item["continued_after_restore"] is True
                and item["stream_modified"] is False
                and len(item["sections"]) == 3
                and all(section["all_serialized_fields_verified"] is True
                        and section["runtime_matches_actual_source_qom"] is True
                        and section["input_clock_periods_match_actual_source_clock_objects"] is True
                        and section["payload_bytes"] == PAYLOAD_BYTES
                        for section in item["sections"])
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and
            all(item["passed"] is True and item["destination_exit"] == 1
                and item["source_status"] == "completed" and item["stream_modified"] is False
                and item["specific_load_failure_observed"] is True
                for item in report["rejected_migrations"]) and
            len(report["corrupted_stream_rejections"]) == 2 and
            all(item["passed"] is True and item["stream_modified"] is True
                and item["only_one_field_modified"] is True
                and item["only_one_bit_modified"] is True
                and item["original_stream_unchanged"] is True
                and item["source_status"] == "completed"
                and item["specific_load_failure_observed"] is True
                and item["destination_exit"] == 1
                for item in report["corrupted_stream_rejections"]) and
            report["unknown_profile_source"]["completed_roundtrip"] is False and
            report["unknown_profile_source"]["guest_runtime_qom_access_refused"] is True and
            report["binary_unchanged"])
    except BaseException as error:
        report["failing_stage"] = current
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
    report["duration_seconds"] = round(time.monotonic() - start, 3)
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
