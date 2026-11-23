#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual two-controller TSENS24xx sampling, threshold, IRQ and migration.

The raw register contract follows the pinned OnePlus production driver and
the original TB371FC DT. Environment temperatures, boot enable masks and the
one-millisecond sampling period are explicit reference inputs/policies, not
measured firmware state, calibration, analogue behavior or tablet temperature.
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

TYPE = "qcom-tsens24xx"
PATHS = ("/machine/tsens0", "/machine/tsens1")
SROT = (0x0c222000, 0x0c223000)
TM = (0x0c263000, 0x0c265000)
RAM_WITNESS = 0x80010000
GCC = 0x00100000
INT_EN, UL_STATUS, UL_CLEAR, UL_MASK = 4, 8, 12, 16
CRIT_STATUS, CRIT_CLEAR, CRIT_MASK = 20, 24, 28
UL_THRESHOLD, CRIT_THRESHOLD, SENSOR_STATUS, TRDY = 0x20, 0x60, 0xa0, 0xe4
VALID, UPPER, LOWER, CRITICAL = 1 << 21, 1 << 18, 1 << 17, 1 << 19
FIRST_ROUND = 1 << 3
PERIOD = 1_000_000
UNKNOWN = -(1 << 31)
SOURCE_COMMIT = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_SHA256 = "8e3bcb1179ecbb482cb2e7a32ad0daff28caace4ee2582b9521e016fb5ce1283"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
DT_EVIDENCE_BLOB = "b6110a64c701db6727f35234cbbb27f932c6f098"


def base_command(args, *, reference=True, boot_mask=0, temperature=UNKNOWN,
                 accel="qtest"):
    command = [args.qemu, "-machine", args.machine, "-accel", accel,
               "-m", "128M", "-display", "none", "-monitor", "none",
               "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]
    if reference:
        command += ["-global", TYPE + ".enabled-reference=on",
                    "-global", TYPE + f".reference-boot-enable-mask={boot_mask}",
                    "-global", TYPE + f".reference-temperature-millicelsius={temperature}"]
    return command


class TsensRig(Rig):
    def __init__(self, args, directory, *, incoming=False, reference=True,
                 boot_mask=0, temperature=UNKNOWN, irq_controller=0, accel="qtest"):
        self.args = args
        self.reference = reference
        self.boot_mask = boot_mask
        self.initial_temperature = temperature
        self.irq_controller = irq_controller
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qtsens-"))
        self.last_clock = 0
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = base_command(args, reference=reference, boot_mask=boot_mask,
                               temperature=temperature, accel=accel) + [
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
            if irq_controller is not None:
                # QTest supports one intercepted device per process and only
                # 256 IRQ indices. Intercept the selected device's real two
                # output pins, never GIC input indices 506..509.
                self.command(f"irq_intercept_out {PATHS[irq_controller]} sysbus-irq")
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

    def tm_read(self, controller, offset, width="l"):
        return self.read(TM[controller] + offset, width)

    def tm_write(self, controller, offset, value, width="l"):
        self.write(TM[controller] + offset, value, width)

    def control(self, controller, value):
        self.write(SROT[controller] + 4, value)

    def get(self, controller, name):
        return self.monitor("qom-get", {"path": PATHS[controller], "property": name})

    def put(self, controller, name, value):
        return self.monitor("qom-set", {"path": PATHS[controller],
                                        "property": name, "value": value})

    def input(self, controller, sensor, temperature=None, *, valid=None):
        if temperature is not None:
            self.put(controller, f"sensor{sensor}-input-millicelsius", temperature)
        if valid is not None:
            self.put(controller, f"sensor{sensor}-input-valid", valid)

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns, (before, ns, self.last_clock)
        return self.last_clock

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return self.last_clock

    def wire(self, upper_lower, critical):
        # RAM only: this must not let device reads repair missing post_load
        # IRQ publication or sample timer restoration.
        self.read(RAM_WITNESS)
        assert self.irq.get(0, False) is upper_lower, self.irq
        assert self.irq.get(1, False) is critical, self.irq

    def close(self):
        super().close()
        for name in ("qtest_transcript", "qmp_transcript"):
            stream = getattr(self, name, None)
            if stream and not stream.closed:
                stream.close()


def code(millicelsius):
    assert -204800 <= millicelsius <= 204700 and millicelsius % 100 == 0
    return (millicelsius // 100) & 0xfff


def decoded_temperature(status):
    raw = status & 0xfff
    if raw & 0x800:
        raw -= 0x1000
    return raw * 100


def c_divide_by_100(value):
    # Production C signed division truncates toward zero, unlike Python //.
    return (abs(value) // 100) * (-1 if value < 0 else 1)


def enable(rig, controller, mask):
    assert 0 < mask <= 0xffff
    rig.control(controller, 1 | (mask << 3))


def thresholds(rig, controller, sensor, low, high, critical):
    rig.tm_write(controller, UL_THRESHOLD + sensor * 4, code(low) | (code(high) << 12))
    rig.tm_write(controller, CRIT_THRESHOLD + sensor * 4, code(critical))


def status(rig, controller, sensor):
    return rig.tm_read(controller, SENSOR_STATUS + sensor * 4)


def core_diagnostics(text):
    matches = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
                         r"size ([0-9]+), region '([^']+)', reason: ([^\n]+)", text)
    return [(kind, int(address, 16), int(size), region, reason)
            for kind, address, size, region, reason in matches]


def device_diagnostics(text):
    return [line for line in text.splitlines() if TYPE + ":" in line]

GLOBAL_STATE = (
    "control", "interrupt-enable", "upper-lower-status", "upper-lower-clear",
    "upper-lower-mask", "critical-status", "critical-clear", "critical-mask",
    "valid-mask", "trdy", "captured-enable-mask", "captured-valid-mask",
    "sample-pending", "deadline-exhausted", "upper-lower-irq", "critical-irq",
    "sample-deadline-ns", "last-sample-ns", "sample-rounds", "complete-rounds")
SENSOR_STATE = (
    "threshold", "critical-threshold", "status", "last-code", "captured-code",
    "last-millicelsius", "valid", "input-millicelsius", "input-valid")


def snapshot(rig):
    return [{"global": {name: rig.get(c, name) for name in GLOBAL_STATE},
             "sensors": [{name: rig.get(c, f"sensor{i}-{name}")
                          for name in SENSOR_STATE} for i in range(16)]}
            for c in range(2)]


def quiet(rig, controller):
    rig.tm_write(controller, INT_EN, 7)
    rig.tm_write(controller, UL_MASK, 0xffffffff)
    rig.tm_write(controller, CRIT_MASK, 0xc000ffff)
    rig.tm_write(controller, UL_CLEAR, 0xffffffff)
    rig.tm_write(controller, UL_CLEAR, 0)
    rig.tm_write(controller, CRIT_CLEAR, 0xc000ffff)
    rig.tm_write(controller, CRIT_CLEAR, 0)


def configure(rig, controller, values, *, low=-40000, high=100000, critical=120000):
    quiet(rig, controller)
    for sensor, temperature in values.items():
        rig.input(controller, sensor, temperature)
        thresholds(rig, controller, sensor, low, high, critical)
    enable(rig, controller, sum(1 << i for i in values))


def clear_latches(rig, controller):
    rig.tm_write(controller, UL_CLEAR, 0xffffffff)
    rig.tm_write(controller, UL_CLEAR, 0)
    rig.tm_write(controller, CRIT_CLEAR, 0xc000ffff)
    rig.tm_write(controller, CRIT_CLEAR, 0)


def fresh_sample(rig, controller, values):
    rig.control(controller, 0)
    for sensor, temperature in values.items():
        rig.input(controller, sensor, temperature)
    clear_latches(rig, controller)
    enable(rig, controller, sum(1 << i for i in values))
    rig.step(PERIOD)


def check_temperature(rig, controller, sensor, expected):
    value = status(rig, controller, sensor)
    assert value & VALID, (controller, sensor, value)
    assert value & 0xfff == code(expected)
    assert decoded_temperature(value) == expected
    assert rig.get(controller, f"sensor{sensor}-last-millicelsius") == expected
    assert rig.get(controller, f"sensor{sensor}-valid") is True


def cold_reference_has_no_fabricated_temperature_or_firmware_enable(rig):
    for c in range(2):
        assert rig.get(c, "enabled-reference") is True
        assert rig.get(c, "reference-boot-enable-mask") == 0
        assert rig.get(c, "reference-temperature-millicelsius") == UNKNOWN
        assert rig.get(c, "srot-base") == SROT[c]
        assert rig.get(c, "tm-base") == TM[c]
        assert rig.get(c, "sensor-count") == 16
        assert rig.get(c, "sampling-period-ns") == PERIOD
        assert rig.read(SROT[c] + 4) == 0
        assert rig.tm_read(c, TRDY) == 0
        assert rig.get(c, "sample-pending") is False
        assert rig.get(c, "sample-rounds") == 0
        assert rig.get(c, "complete-rounds") == 0
        assert rig.get(c, "last-sample-ns") == -1
        for sensor in range(16):
            assert status(rig, c, sensor) & VALID == 0
            assert rig.get(c, f"sensor{sensor}-input-valid") is False
    rig.step(5 * PERIOD)
    for c in range(2):
        assert rig.get(c, "sample-rounds") == 0
        assert rig.get(c, "valid-mask") == 0
    rig.wire(False, False)


def first_sample_and_validity_follow_the_exact_virtual_deadline(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 25100})
    assert rig.get(c, "sample-pending") is True
    assert rig.get(c, "sample-deadline-ns") == PERIOD
    assert rig.get(c, "captured-enable-mask") == 1
    assert rig.get(c, "captured-valid-mask") == 1
    assert rig.get(c, "sensor0-captured-code") == 251
    rig.step(PERIOD - 1)
    assert status(rig, c, 0) & VALID == 0
    assert rig.tm_read(c, TRDY) == 0
    assert rig.get(c, "sample-rounds") == 0
    rig.step(1)
    check_temperature(rig, c, 0, 25100)
    assert rig.tm_read(c, TRDY) == FIRST_ROUND
    assert rig.get(c, "sample-rounds") == 1
    assert rig.get(c, "complete-rounds") == 1
    assert rig.get(c, "last-sample-ns") == PERIOD
    assert rig.get(c, "sample-deadline-ns") == 2 * PERIOD
    rig.wire(False, False)


def a_partial_round_cannot_claim_all_enabled_inputs_were_sampled(rig):
    c = rig.irq_controller
    quiet(rig, c)
    thresholds(rig, c, 0, -40000, 100000, 120000)
    thresholds(rig, c, 1, -40000, 100000, 120000)
    rig.input(c, 0, 18000)
    enable(rig, c, 3)
    rig.input(c, 1, 33000)  # Too late for the already captured first round.
    rig.step(PERIOD)
    check_temperature(rig, c, 0, 18000)
    assert status(rig, c, 1) & VALID == 0
    assert rig.get(c, "valid-mask") == 1
    assert rig.get(c, "trdy") == 0
    assert rig.get(c, "complete-rounds") == 0
    rig.step(PERIOD - 1)
    assert rig.get(c, "trdy") == 0
    rig.step(1)
    check_temperature(rig, c, 1, 33000)
    assert rig.get(c, "valid-mask") == 3
    assert rig.get(c, "trdy") == FIRST_ROUND
    assert rig.get(c, "sample-rounds") == 2
    assert rig.get(c, "complete-rounds") == 1


def all_thirty_two_channels_preserve_signed_codes_and_controller_isolation(rig):
    values = (-204800, -40000, -12500, -100, 0, 100, 12300, 25000,
              37000, 42500, 80000, 99900, 119900, 120000, 204600, 204700)
    for c in range(2):
        selected = values if c == 0 else tuple(reversed(values))
        configure(rig, c, dict(enumerate(selected)),
                  low=-204800, high=204700, critical=204700)
    rig.step(PERIOD)
    for c in range(2):
        selected = values if c == 0 else tuple(reversed(values))
        assert rig.get(c, "valid-mask") == 0xffff
        assert rig.get(c, "trdy") == FIRST_ROUND
        for sensor, temperature in enumerate(selected):
            check_temperature(rig, c, sensor, temperature)
    assert rig.get(0, "sensor0-last-code") == 0x800
    assert rig.get(1, "sensor0-last-code") == 0x7ff
    rig.wire(False, False)


def changes_to_environment_inputs_do_not_rewrite_the_inflight_sample(rig):
    c = rig.irq_controller
    configure(rig, c, {2: -12500, 5: 76000})
    rig.step(PERIOD // 3)
    rig.input(c, 2, 41500)
    rig.input(c, 5, valid=False)
    assert rig.get(c, "sensor2-captured-code") == code(-12500)
    assert rig.get(c, "sensor5-captured-code") == code(76000)
    assert rig.get(c, "captured-valid-mask") == (1 << 2) | (1 << 5)
    rig.step(PERIOD - PERIOD // 3)
    check_temperature(rig, c, 2, -12500)
    check_temperature(rig, c, 5, 76000)
    assert rig.get(c, "sensor2-captured-code") == code(41500)
    assert rig.get(c, "captured-valid-mask") == 1 << 2
    rig.step(PERIOD)
    check_temperature(rig, c, 2, 41500)
    assert status(rig, c, 5) & VALID == 0
    assert status(rig, c, 5) & 0xfff == code(76000)
    assert rig.get(c, "trdy") == FIRST_ROUND  # First complete round is sticky.
    assert rig.get(c, "sample-rounds") == 2
    assert rig.get(c, "complete-rounds") == 1


def withdrawn_sources_lose_validity_without_becoming_an_invented_zero(rig):
    c = rig.irq_controller
    configure(rig, c, {3: -700})
    rig.step(PERIOD)
    check_temperature(rig, c, 3, -700)
    rig.input(c, 3, valid=False)
    rig.step(PERIOD)
    check_temperature(rig, c, 3, -700)  # Second round was already captured.
    rig.step(PERIOD)
    value = status(rig, c, 3)
    assert value & VALID == 0 and value & 0xfff == code(-700)
    assert rig.get(c, "valid-mask") == 0
    assert rig.get(c, "trdy") == FIRST_ROUND
    assert rig.get(c, "complete-rounds") == 2
    assert rig.get(c, "sample-rounds") == 3


def controllers_keep_independent_sampling_phases_and_enable_masks(rig):
    configure(rig, 0, {0: 18000, 15: 55000})
    rig.step(137)
    configure(rig, 1, {8: -3000})
    assert rig.get(0, "sample-deadline-ns") == PERIOD
    assert rig.get(1, "sample-deadline-ns") == PERIOD + 137
    rig.step(PERIOD - 138)
    assert rig.get(0, "valid-mask") == rig.get(1, "valid-mask") == 0
    rig.step(1)
    assert rig.get(0, "valid-mask") == 0x8001
    assert rig.get(1, "valid-mask") == 0
    rig.step(136)
    assert rig.get(1, "valid-mask") == 0
    rig.step(1)
    assert rig.get(1, "valid-mask") == 0x100
    rig.control(0, 0)
    before = rig.get(1, "sample-deadline-ns")
    rig.step(PERIOD)
    assert rig.get(0, "sample-pending") is False
    assert rig.get(0, "valid-mask") == 0
    assert rig.get(1, "sample-deadline-ns") == before + PERIOD
    check_temperature(rig, 1, 8, -3000)


def literal_driver_threshold_rmw_preserves_the_other_field_and_c_signed_division(rig):
    c, sensor = rig.irq_controller, 7
    offset = UL_THRESHOLD + 4 * sensor
    rig.tm_write(c, offset, 0x123456)
    high_millicelsius, low_millicelsius = 65199, -12599
    old = rig.tm_read(c, offset)
    high_code = (c_divide_by_100(high_millicelsius) << 12) & 0xfff000
    rig.tm_write(c, offset, (old & ~0xfff000) | high_code)
    assert rig.tm_read(c, offset) == (651 << 12) | 0x456
    old = rig.tm_read(c, offset)
    low_code = c_divide_by_100(low_millicelsius) & 0xfff
    rig.tm_write(c, offset, (old & ~0xfff) | low_code)
    assert rig.tm_read(c, offset) == (651 << 12) | ((-125) & 0xfff)
    # Production activation RMW clears the corresponding mask bit.
    rig.tm_write(c, UL_MASK, 0xffffffff)
    rig.tm_write(c, UL_MASK, rig.tm_read(c, UL_MASK) & ~(1 << (sensor + 16)))
    rig.tm_write(c, UL_MASK, rig.tm_read(c, UL_MASK) & ~(1 << sensor))
    assert rig.tm_read(c, UL_MASK) == 0xffffffff ^ (1 << 23) ^ (1 << 7)
    assert rig.get(c, "sample-rounds") == 0
    rig.wire(False, False)


def signed_upper_and_lower_comparators_include_equality_on_real_irq(rig):
    c = rig.irq_controller
    configure(rig, c, {0: -200})
    thresholds(rig, c, 0, -100, 100, 100000)
    rig.tm_write(c, UL_MASK, 0xfffefffe)
    rig.step(PERIOD)
    assert status(rig, c, 0) & LOWER
    assert rig.tm_read(c, UL_STATUS) == 1
    rig.wire(True, False)
    for temperature, expected in ((-100, 1), (0, 0), (100, 1 << 16), (200, 1 << 16)):
        fresh_sample(rig, c, {0: temperature})
        assert rig.tm_read(c, UL_STATUS) == expected, (temperature, expected)
        assert bool(status(rig, c, 0) & LOWER) is bool(expected & 1)
        assert bool(status(rig, c, 0) & UPPER) is bool(expected & (1 << 16))
        rig.wire(bool(expected), False)
    assert rig.tm_read(c, CRIT_STATUS) == 0


def critical_comparator_and_second_irq_are_independent_of_upper_lower(rig):
    c, sensor = rig.irq_controller, 4
    configure(rig, c, {sensor: 29900})
    thresholds(rig, c, sensor, -40000, 100000, 30000)
    rig.tm_write(c, CRIT_MASK, 0xc000ffff & ~(1 << sensor))
    rig.step(PERIOD)
    rig.wire(False, False)
    for temperature in (30000, 30100):
        fresh_sample(rig, c, {sensor: temperature})
        assert rig.tm_read(c, CRIT_STATUS) == 1 << sensor
        assert status(rig, c, sensor) & CRITICAL
        assert rig.tm_read(c, UL_STATUS) == 0
        rig.wire(False, True)
    fresh_sample(rig, c, {sensor: 29900})
    rig.wire(False, False)


def global_enables_filter_real_latched_irqs_without_erasing_status(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 50000, 1: -5000})
    thresholds(rig, c, 0, -40000, 40000, 45000)
    thresholds(rig, c, 1, 0, 100000, 120000)
    rig.tm_write(c, UL_MASK, 0xfffefffd)
    rig.tm_write(c, CRIT_MASK, 0xc000fffe)
    rig.tm_write(c, INT_EN, 0)
    rig.step(PERIOD)
    assert rig.tm_read(c, UL_STATUS) == (1 << 16) | 2
    assert rig.tm_read(c, CRIT_STATUS) == 1
    for value, ul, critical in ((0, False, False), (1, True, False),
                                (2, True, False), (4, False, True),
                                (7, True, True), (0, False, False)):
        rig.tm_write(c, INT_EN, value)
        rig.wire(ul, critical)
        assert rig.tm_read(c, UL_STATUS) == (1 << 16) | 2
        assert rig.tm_read(c, CRIT_STATUS) == 1


def channel_masks_isolate_events_and_rearming_does_not_require_a_fake_sample(rig):
    c = rig.irq_controller
    configure(rig, c, {1: 80000, 13: 81000})
    for sensor in (1, 13):
        thresholds(rig, c, sensor, -40000, 60000, 70000)
    rig.step(PERIOD)
    flags = (1 << 1) | (1 << 13)
    assert rig.tm_read(c, UL_STATUS) == flags << 16
    assert rig.tm_read(c, CRIT_STATUS) == flags
    rig.wire(False, False)
    rounds = rig.get(c, "sample-rounds")
    rig.tm_write(c, UL_MASK, 0xffffffff ^ (1 << 17))
    rig.wire(True, False)
    rig.tm_write(c, UL_CLEAR, 1 << 17)
    rig.tm_write(c, UL_CLEAR, 0)
    assert rig.tm_read(c, UL_STATUS) == 1 << 29
    rig.wire(False, False)
    rig.tm_write(c, UL_MASK, 0xffffffff ^ (1 << 29))
    rig.wire(True, False)
    rig.tm_write(c, CRIT_MASK, 0xc000ffff ^ (1 << 13))
    rig.wire(True, True)
    rig.tm_write(c, CRIT_CLEAR, 1 << 13)
    rig.tm_write(c, CRIT_CLEAR, 0)
    assert rig.tm_read(c, CRIT_STATUS) == 1 << 1
    rig.wire(True, False)
    assert rig.get(c, "sample-rounds") == rounds


def clear_pulses_clear_latches_and_held_clear_suppresses_only_the_selected_event(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 90000, 2: -1000})
    thresholds(rig, c, 0, -40000, 50000, 70000)
    thresholds(rig, c, 2, 0, 100000, 120000)
    rig.tm_write(c, UL_MASK, 0xfffefffb)
    rig.tm_write(c, CRIT_MASK, 0xc000fffe)
    rig.step(PERIOD)
    rig.wire(True, True)
    rig.tm_write(c, UL_CLEAR, 1 << 16)
    rig.tm_write(c, CRIT_CLEAR, 1)
    assert rig.tm_read(c, UL_STATUS) == 4
    assert rig.tm_read(c, CRIT_STATUS) == 0
    rig.wire(True, False)
    rig.step(2 * PERIOD)
    assert rig.tm_read(c, UL_STATUS) == 4
    assert rig.tm_read(c, CRIT_STATUS) == 0
    rig.tm_write(c, UL_CLEAR, 0)
    rig.tm_write(c, CRIT_CLEAR, 0)
    assert rig.tm_read(c, UL_STATUS) == 4
    assert rig.tm_read(c, CRIT_STATUS) == 0
    rig.step(PERIOD - 1)
    assert rig.tm_read(c, UL_STATUS) == 4
    rig.step(1)
    assert rig.tm_read(c, UL_STATUS) == (1 << 16) | 4
    assert rig.tm_read(c, CRIT_STATUS) == 1
    rig.wire(True, True)

def control_changes_cancel_pending_samples_but_preserve_programmed_thresholds(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 25000, 7: 33000})
    saved_thresholds = [rig.get(c, f"sensor{i}-threshold") for i in range(16)]
    rig.step(PERIOD // 2)
    enable(rig, c, 1 << 7)
    assert rig.get(c, "sample-deadline-ns") == PERIOD + PERIOD // 2
    assert rig.get(c, "captured-enable-mask") == 1 << 7
    assert rig.get(c, "valid-mask") == rig.get(c, "trdy") == 0
    rig.step(PERIOD // 2)  # Original cancelled deadline.
    assert rig.get(c, "sample-rounds") == 0
    rig.step(PERIOD // 2)
    assert rig.get(c, "valid-mask") == 1 << 7
    assert rig.get(c, "trdy") == FIRST_ROUND
    check_temperature(rig, c, 7, 33000)
    rig.control(c, (1 << 7) << 3)  # Sensor mask alone cannot run the controller.
    assert rig.get(c, "sample-pending") is False
    assert rig.get(c, "sample-deadline-ns") == 0
    assert rig.get(c, "captured-enable-mask") == 0
    assert rig.get(c, "valid-mask") == rig.get(c, "trdy") == 0
    rounds = rig.get(c, "sample-rounds")
    rig.step(2 * PERIOD)
    assert rig.get(c, "sample-rounds") == rounds
    assert [rig.get(c, f"sensor{i}-threshold") for i in range(16)] == saved_thresholds
    rig.control(c, 1)  # EN with no sensor selected must also remain idle.
    assert rig.get(c, "sample-pending") is False


def software_reset_is_a_real_held_reset_and_preserves_external_environment(rig):
    c = rig.irq_controller
    configure(rig, c, {2: 90000})
    thresholds(rig, c, 2, -40000, 50000, 70000)
    rig.tm_write(c, UL_MASK, 0xffffffff ^ (1 << 18))
    rig.tm_write(c, CRIT_MASK, 0xc000ffff ^ (1 << 2))
    rig.step(PERIOD)
    rig.wire(True, True)
    assert rig.get(c, "sample-pending") is True
    rig.control(c, 1 | 2 | ((1 << 2) << 3))
    assert rig.get(c, "control") == 1 | 2 | ((1 << 2) << 3)
    assert rig.get(c, "sample-pending") is False
    assert rig.get(c, "sample-rounds") == rig.get(c, "complete-rounds") == 0
    assert rig.get(c, "last-sample-ns") == -1
    assert rig.get(c, "interrupt-enable") == 0
    assert rig.get(c, "upper-lower-status") == rig.get(c, "critical-status") == 0
    assert rig.get(c, "upper-lower-mask") == 0xffffffff
    assert rig.get(c, "critical-mask") == 0xc000ffff
    for sensor in range(16):
        assert rig.get(c, f"sensor{sensor}-threshold") == 0x7ff800
        assert rig.get(c, f"sensor{sensor}-critical-threshold") == 0x7ff
        assert rig.get(c, f"sensor{sensor}-last-code") == 0
    assert rig.get(c, "sensor2-input-millicelsius") == 90000
    assert rig.get(c, "sensor2-input-valid") is True
    rig.wire(False, False)
    rig.step(2 * PERIOD)
    assert rig.get(c, "sample-rounds") == 0
    enable(rig, c, 1 << 2)  # Explicitly deassert SW_RST.
    rig.step(PERIOD - 1)
    assert rig.get(c, "valid-mask") == 0
    rig.step(1)
    check_temperature(rig, c, 2, 90000)
    rig.wire(False, False)


def system_reset_cancels_timers_and_irqs_and_retains_only_external_inputs(rig):
    c = rig.irq_controller
    configure(rig, c, {5: 88000})
    thresholds(rig, c, 5, -40000, 50000, 70000)
    rig.tm_write(c, UL_MASK, 0xffffffff ^ (1 << 21))
    rig.tm_write(c, CRIT_MASK, 0xc000ffff ^ (1 << 5))
    rig.step(PERIOD)
    rig.wire(True, True)
    rig.step(PERIOD // 4)
    old_deadline = rig.get(c, "sample-deadline-ns")
    rig.write(GCC + 4, 0x77)  # Existing independent nonzero reset witness.
    assert rig.read(GCC + 4) == 0x77
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.read(GCC + 4) == 0, "GCC reset witness")
    rig.monitor("query-status")  # Drain the asynchronous RESET event.
    assert any(event.get("event") == "RESET" for event in rig.events)
    for controller in range(2):
        assert rig.get(controller, "control") == 0  # Explicit boot mask is zero.
        assert rig.get(controller, "sample-pending") is False
        assert rig.get(controller, "sample-rounds") == 0
        assert rig.get(controller, "valid-mask") == rig.get(controller, "trdy") == 0
        assert rig.get(controller, "upper-lower-status") == 0
        assert rig.get(controller, "critical-status") == 0
    rig.wire(False, False)
    assert rig.get(c, "sensor5-input-millicelsius") == 88000
    assert rig.get(c, "sensor5-input-valid") is True
    rig.step(old_deadline - rig.last_clock + PERIOD)
    assert rig.get(c, "sample-rounds") == 0
    enable(rig, c, 1 << 5)
    rig.step(PERIOD)
    check_temperature(rig, c, 5, 88000)


def expect_device_write_error(rig, controller, offset, value, reason, *, srot=False):
    before = snapshot(rig)
    lines = device_diagnostics(rig.log_path.read_text(errors="replace"))
    if srot:
        rig.control(controller, value)
    else:
        rig.tm_write(controller, offset, value)
    after = device_diagnostics(rig.log_path.read_text(errors="replace"))
    bank = "srot" if srot else "tm"
    expected = (f"{TYPE}: write controller {controller} {bank} +{offset:#x} "
                f"size 4: {reason}")
    assert after[len(lines):] == [expected], (after[len(lines):], expected)
    assert snapshot(rig) == before


def read_only_feedback_and_reserved_bits_cannot_forge_a_sample_or_irq(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 25100})
    rig.step(PERIOD)
    for offset in (UL_STATUS, CRIT_STATUS, TRDY, *(SENSOR_STATUS + i * 4 for i in range(16))):
        expect_device_write_error(rig, c, offset, 0xffffffff, "read-only register")
    for offset, value, srot in (
            (4, 4, True), (4, 1 << 19, True), (INT_EN, 8, False),
            (UL_THRESHOLD, 1 << 24, False), (CRIT_THRESHOLD, 1 << 12, False),
            (CRIT_MASK, 1 << 16, False), (CRIT_CLEAR, 1 << 16, False)):
        expect_device_write_error(rig, c, offset, value, "unsupported bits", srot=srot)
    before = snapshot(rig)
    for name, value in (
            ("enabled-reference", False), ("reference-boot-enable-mask", 1),
            ("reference-temperature-millicelsius", 30000), ("controller-id", 1 - c),
            ("sampling-period-ns", 1), ("control", 1), ("valid-mask", 0xffff),
            ("trdy", FIRST_ROUND), ("sample-rounds", 999), ("sample-pending", False),
            ("sensor0-last-code", 1000), ("sensor0-status", VALID | 1000),
            ("sensor0-valid", True), ("upper-lower-irq", True)):
        try:
            rig.put(c, name, value)
        except AssertionError as error:
            assert "error" in str(error), str(error)
        else:
            raise AssertionError((name, "read-only or realized property accepted a write"))
        assert snapshot(rig) == before


def narrow_and_unaligned_accesses_fail_while_wide_bus_accesses_split_into_words(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 12300})
    rig.step(PERIOD)
    before = snapshot(rig)
    base = TM[c] + UL_THRESHOLD
    operations = (
        ("read", SROT[c] + 4, "b", [(0, 1, TYPE + "-srot-control")]),
        ("write", SROT[c] + 4, "w", [(0, 2, TYPE + "-srot-control")]),
        ("read", base, "b", [(0x1c, 1, TYPE + "-tm-registers")]),
        ("write", base, "w", [(0x1c, 2, TYPE + "-tm-registers")]),
        ("write", base + 1, "l", [(0x1d, 1, TYPE + "-tm-registers"),
                                  (0x1e, 2, TYPE + "-tm-registers"),
                                  (0x20, 1, TYPE + "-tm-registers")]),
        ("read", TM[c] + TRDY, "w", [(0, 2, TYPE + "-trdy")]),
    )
    for kind, address, width, chunks in operations:
        count = len(core_diagnostics(rig.log_path.read_text(errors="replace")))
        if kind == "write":
            rig.write(address, 0, width)
        else:
            rig.read(address, width)
        observed = core_diagnostics(rig.log_path.read_text(errors="replace"))[count:]
        expected = [(kind, off, size, name, "rejected") for off, size, name in chunks]
        assert observed == expected, (observed, expected)
        assert snapshot(rig) == before
    # address_space_rw splits a 64-bit bus operation into two legal 32-bit
    # device transactions. It is not an atomic rejected 64-bit device write.
    rig.write(base, 0x0006789a00012345, "q")
    assert rig.tm_read(c, UL_THRESHOLD) == 0x12345
    assert rig.tm_read(c, UL_THRESHOLD + 4) == 0x6789a
    assert rig.read(base, "q") == 0x0006789a00012345
    count = len(device_diagnostics(rig.log_path.read_text(errors="replace")))
    rig.write(base, (1 << 24) << 32 | 0x54321, "q")
    assert rig.tm_read(c, UL_THRESHOLD) == 0x54321
    assert rig.tm_read(c, UL_THRESHOLD + 4) == 0x6789a
    expected = f"{TYPE}: write controller {c} tm +0x24 size 4: unsupported bits"
    assert device_diagnostics(rig.log_path.read_text(errors="replace"))[count:] == [expected]


def host_environment_validation_is_atomic_and_never_directly_updates_guest_feedback(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 25000})
    rig.step(PERIOD)
    before = snapshot(rig)
    for value in (-204900, 204800, -12550, 1):
        try:
            rig.input(c, 0, value)
        except AssertionError as error:
            assert "temperature must be INT32_MIN (unknown)" in str(error), str(error)
        else:
            raise AssertionError((value, "invalid environment accepted"))
        assert snapshot(rig) == before
    try:
        rig.input(c, 9, valid=True)
    except AssertionError as error:
        assert "a known temperature is required before input-valid" in str(error), str(error)
    else:
        raise AssertionError("unknown source was marked valid")
    assert snapshot(rig) == before
    rig.input(c, 0, 204700)
    assert rig.get(c, "sensor0-input-millicelsius") == 204700
    assert status(rig, c, 0) & 0xfff == code(25000)
    rig.input(c, 0, UNKNOWN)
    assert rig.get(c, "sensor0-input-valid") is False
    assert status(rig, c, 0) & VALID
    assert status(rig, c, 0) & 0xfff == code(25000)


def paused_time_and_late_dispatch_commit_only_one_captured_round(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 25000})
    before = snapshot(rig)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    time.sleep(0.02)
    assert snapshot(rig) == before
    late = 5 * PERIOD + 17
    rig.set_clock(late)
    assert snapshot(rig) == before
    rig.input(c, 0, 36000)
    assert rig.get(c, "sensor0-captured-code") == code(25000)
    assert rig.get(c, "sample-rounds") == 0
    # Device reads are observational even when the virtual deadline is late.
    assert status(rig, c, 0) & VALID == 0
    assert rig.get(c, "sample-rounds") == 0
    rig.monitor("cont")
    rig.step(1)
    assert rig.get(c, "sample-rounds") == 1
    assert rig.get(c, "last-sample-ns") == late
    assert rig.get(c, "sample-deadline-ns") == late + PERIOD
    assert rig.get(c, "sensor0-last-code") == code(25000)
    assert rig.get(c, "sensor0-captured-code") == code(36000)
    rig.step(PERIOD - 2)
    assert rig.get(c, "sample-rounds") == 1
    rig.step(1)
    check_temperature(rig, c, 0, 36000)
    assert rig.get(c, "sample-rounds") == 2


def active_channel_gating_changes_wires_without_destroying_latched_history(rig):
    c = rig.irq_controller
    configure(rig, c, {0: 90000, 1: 25000})
    thresholds(rig, c, 0, -40000, 50000, 70000)
    rig.tm_write(c, UL_MASK, 0xfffeffff)
    rig.tm_write(c, CRIT_MASK, 0xc000fffe)
    rig.step(PERIOD)
    rig.wire(True, True)
    original = (rig.tm_read(c, UL_STATUS), rig.tm_read(c, CRIT_STATUS))
    enable(rig, c, 2)  # Keep controller active but disable the tripped sensor.
    assert (rig.tm_read(c, UL_STATUS), rig.tm_read(c, CRIT_STATUS)) == original
    rig.wire(False, False)
    enable(rig, c, 3)
    assert rig.get(c, "valid-mask") == 0  # No new conversion yet.
    assert (rig.tm_read(c, UL_STATUS), rig.tm_read(c, CRIT_STATUS)) == original
    rig.wire(True, True)
    rig.control(c, 0)
    rig.wire(False, False)
    assert (rig.tm_read(c, UL_STATUS), rig.tm_read(c, CRIT_STATUS)) == original

ORDINARY_TESTS = (
    cold_reference_has_no_fabricated_temperature_or_firmware_enable,
    first_sample_and_validity_follow_the_exact_virtual_deadline,
    a_partial_round_cannot_claim_all_enabled_inputs_were_sampled,
    all_thirty_two_channels_preserve_signed_codes_and_controller_isolation,
    changes_to_environment_inputs_do_not_rewrite_the_inflight_sample,
    withdrawn_sources_lose_validity_without_becoming_an_invented_zero,
    controllers_keep_independent_sampling_phases_and_enable_masks,
    literal_driver_threshold_rmw_preserves_the_other_field_and_c_signed_division,
    signed_upper_and_lower_comparators_include_equality_on_real_irq,
    critical_comparator_and_second_irq_are_independent_of_upper_lower,
    global_enables_filter_real_latched_irqs_without_erasing_status,
    channel_masks_isolate_events_and_rearming_does_not_require_a_fake_sample,
    clear_pulses_clear_latches_and_held_clear_suppresses_only_the_selected_event,
    control_changes_cancel_pending_samples_but_preserve_programmed_thresholds,
    software_reset_is_a_real_held_reset_and_preserves_external_environment,
    system_reset_cancels_timers_and_irqs_and_retains_only_external_inputs,
    read_only_feedback_and_reserved_bits_cannot_forge_a_sample_or_irq,
    narrow_and_unaligned_accesses_fail_while_wide_bus_accesses_split_into_words,
    host_environment_validation_is_atomic_and_never_directly_updates_guest_feedback,
    paused_time_and_late_dispatch_commit_only_one_captured_round,
    active_channel_gating_changes_wires_without_destroying_latched_history,
)

# QTest can intercept one output device per process. Exercise both pairs
# across independent actual QEMU instances; a separate fixture preserves all
# physical connections and reads the real GIC distributor pending bits.
IRQ_CONTROLLER_ONE = {
    first_sample_and_validity_follow_the_exact_virtual_deadline.__name__,
    critical_comparator_and_second_irq_are_independent_of_upper_lower.__name__,
    clear_pulses_clear_latches_and_held_clear_suppresses_only_the_selected_event.__name__,
    software_reset_is_a_real_held_reset_and_preserves_external_environment.__name__,
    narrow_and_unaligned_accesses_fail_while_wide_bus_accesses_split_into_words.__name__,
}


def defined_registers():
    for c in range(2):
        yield c, "srot", 4, SROT[c] + 4
        for offset in (*range(4, 0xe0, 4), TRDY):
            yield c, "tm", offset, TM[c] + offset


def default_unknown_profile_rejects_raw_state_and_cannot_become_a_thermal_success(args, out):
    rig = TsensRig(args, out / "unknown-profile", reference=False)
    try:
        for c in range(2):
            assert rig.get(c, "enabled-reference") is False
            assert rig.get(c, "srot-base") == SROT[c]
            assert rig.get(c, "tm-base") == TM[c]
            for name in ("control", "trdy", "valid-mask", "sample-rounds", "sensor0-status"):
                try:
                    rig.get(c, name)
                except AssertionError as error:
                    assert TYPE + ": unknown profile" in str(error), str(error)
                else:
                    raise AssertionError((c, name, "unknown profile exposed raw state"))
            assert rig.get(c, "sensor0-input-millicelsius") == UNKNOWN
            assert rig.get(c, "sensor0-input-valid") is False
            try:
                rig.input(c, 0, 25000)
            except AssertionError as error:
                assert TYPE + ": unknown profile" in str(error), str(error)
            else:
                raise AssertionError("unknown profile accepted an environment source")
        for c, bank, offset, address in defined_registers():
            count = len(device_diagnostics(rig.log_path.read_text(errors="replace")))
            rig.read(address)
            rig.write(address, 0)
            expected = [
                f"{TYPE}: {direction} controller {c} {bank} +{offset:#x} size 4: unknown profile"
                for direction in ("read", "write")]
            assert device_diagnostics(rig.log_path.read_text(errors="replace"))[count:] == expected
        rig.step(5 * PERIOD)
        rig.wire(False, False)
    finally:
        rig.close()


def explicit_handoff_requires_known_inputs_and_valid_configuration(args, out):
    known = unknown = None
    try:
        unknown = TsensRig(args, out / "handoff-unknown-environment", boot_mask=0x8001)
        assert unknown.read(SROT[0] + 4) == 1 | (0x8001 << 3)
        unknown.step(3 * PERIOD)
        for c in range(2):
            assert unknown.get(c, "sample-rounds") == 3
            assert unknown.get(c, "complete-rounds") == 0
            assert unknown.get(c, "valid-mask") == unknown.get(c, "trdy") == 0
            assert status(unknown, c, 0) & VALID == 0
            assert status(unknown, c, 15) & VALID == 0

        known = TsensRig(args, out / "handoff-known-environment",
                         boot_mask=0x8001, temperature=25000)
        known.input(1, 15, -7500)
        known.step(PERIOD - 1)
        for c in range(2):
            assert known.get(c, "valid-mask") == 0
            assert known.get(c, "trdy") == 0
        known.step(1)
        for c in range(2):
            assert known.get(c, "valid-mask") == 0x8001
            assert known.get(c, "trdy") == FIRST_ROUND
            check_temperature(known, c, 0, 25000)
            check_temperature(known, c, 15, 25000)
        known.step(PERIOD)
        check_temperature(known, 1, 15, -7500)
        known.input(0, 0, 42500)
        known.control(0, 0)
        known.control(1, 0)
        known.monitor("system_reset")
        expected_control = 1 | (0x8001 << 3)
        known.wait_for(lambda: known.get(0, "control") == expected_control,
                       "reference boot controller restoration")
        known.monitor("query-status")
        assert any(item.get("event") == "RESET" for item in known.events)
        for c in range(2):
            assert known.get(c, "control") == expected_control
            assert known.get(c, "sample-rounds") == 0
            assert known.get(c, "valid-mask") == 0
            assert known.get(c, "sample-pending") is True
        known.step(PERIOD)
        check_temperature(known, 0, 0, 42500)
        check_temperature(known, 1, 15, -7500)
    finally:
        if known:
            known.close()
        if unknown:
            unknown.close()

    cases = (
        (True, 0x10000, UNKNOWN, "reference-boot-enable-mask must fit 16 sensors"),
        (True, 0, -204900, "reference-temperature-millicelsius must be INT32_MIN"),
        (True, 0, 204800, "reference-temperature-millicelsius must be INT32_MIN"),
        (True, 0, 12345, "reference-temperature-millicelsius must be INT32_MIN"),
        (False, 1, UNKNOWN, "reference boot state requires enabled-reference"),
        (False, 0, 25000, "reference boot state requires enabled-reference"),
    )
    for index, (reference, boot_mask, temperature, diagnostic) in enumerate(cases):
        directory = out / f"invalid-handoff-{index}"
        directory.mkdir()
        command = base_command(args, reference=False) + [
            "-global", TYPE + f".enabled-reference={'on' if reference else 'off'}",
            "-global", TYPE + f".reference-boot-enable-mask={boot_mask}",
            "-global", TYPE + f".reference-temperature-millicelsius={temperature}"]
        result = subprocess.run(command, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        (directory / "qemu.stderr").write_bytes(result.stderr)
        assert result.returncode == 1, (command, result.returncode, result.stderr)
        assert TYPE.encode() in result.stderr and diagnostic.encode() in result.stderr


def paused_tcg_environment_properties_are_inputs_and_do_not_execute_a_conversion(args, out):
    rig = TsensRig(args, out / "paused-tcg-environment", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        properties = rig.monitor("qom-list", {"path": PATHS[0]})
        names = {item["name"] for item in properties}
        assert "sensor0-input-millicelsius" in names
        assert "sensor0-input-valid" in names
        assert not any(name.startswith("qtest-") for name in names)
        configure(rig, 0, {0: 20000})
        assert rig.get(0, "sample-pending") is True
        assert rig.get(0, "sensor0-captured-code") == code(20000)
        rig.input(0, 0, 31000)
        time.sleep(0.02)
        assert rig.get(0, "sensor0-input-millicelsius") == 31000
        assert rig.get(0, "sensor0-captured-code") == code(20000)
        assert rig.get(0, "sample-rounds") == 0
        assert rig.get(0, "valid-mask") == rig.get(0, "trdy") == 0
        assert status(rig, 0, 0) & VALID == 0
        assert rig.monitor("query-status")["running"] is False
    finally:
        rig.close()


def source_bounded_windows_preserve_version_calibration_watchdog_and_0c_holes(args, out):
    rig = TsensRig(args, out / "sparse-layout")
    try:
        mtree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory / "mtree.txt").write_text(mtree)
        pattern = (r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" +
                   "|".join(re.escape(TYPE + "-" + name)
                            for name in ("srot-control", "tm-registers", "trdy")) +
                   r")(?:\s|$)")
        found = set()
        for line in mtree.splitlines():
            match = re.search(pattern, line)
            if match:
                first, last, name = match.groups()
                found.add((int(first, 16), int(last, 16), name))
        expected = set()
        for c in range(2):
            expected.add((SROT[c] + 4, SROT[c] + 7, TYPE + "-srot-control"))
            expected.add((TM[c] + 4, TM[c] + 0xdf, TYPE + "-tm-registers"))
            expected.add((TM[c] + 0xe4, TM[c] + 0xe7, TYPE + "-trdy"))
        assert found == expected, (found ^ expected, mtree)
        assert len(list(defined_registers())) == 114
        assert sum(last - first + 1 for first, last, _ in expected) == 456
        configure(rig, 0, {0: 19000})
        configure(rig, 1, {8: -14000})
        before = snapshot(rig)
        holes = set()
        for c in range(2):
            holes.update(SROT[c] + offset for offset in (0, 8, 0x1c))
            holes.update(TM[c] + offset
                         for offset in (0, 0xe0, 0xe8, 0xec, 0x13c, 0x170, 0x1fc, 0x200))
        for address in sorted(holes):
            old_log = rig.log_path.read_text(errors="replace")
            rig.read(address)
            rig.write(address, 0x12345678)
            fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(fresh) == [
                ("read", address, 4, "(null)", "rejected"),
                ("write", address, 4, "(null)", "rejected")], (hex(address), fresh)
            assert not device_diagnostics(fresh), fresh
        assert snapshot(rig) == before
        (rig.directory / "expected-layout.json").write_text(json.dumps({
            "windows": sorted(expected), "holes_tested": sorted(holes),
            "dtb_sha256": DTB_SHA256, "dt_evidence_blob": DT_EVIDENCE_BLOB,
            "original_dt_srot_declared_bytes_per_controller": 4,
            "source_observed_control_offset_beyond_declared_srot_size": 4,
            "model_control_mapped_bytes_per_controller": 4,
            "original_dt_tm_declared_bytes_per_controller": 0x1ff,
            "reference_sensor_slots_per_controller": 16,
            "unique_dt_referenced_sensor_counts": [16, 9],
            "dt_references_are_measured_firmware_enable_masks": False},
            indent=2) + "\n")
    finally:
        rig.close()


def both_controllers_drive_all_four_real_gic_routes_without_interception(args, out):
    rig = TsensRig(args, out / "real-gic-routing", irq_controller=None)
    try:
        # Same secure QTest distributor path as the existing passing AOP
        # notification fixture. INTID = DT SPI number + 32.
        gic_pending = 0x17a00000 + 0x200 + (538 // 32) * 4
        gic_clear = 0x17a00000 + 0x280 + (538 // 32) * 4
        ul0, ul1, crit0, crit1 = (1 << n for n in (26, 27, 28, 29))
        all_lines = ul0 | ul1 | crit0 | crit1
        assert rig.read(gic_pending) & all_lines == 0
        for c in range(2):
            configure(rig, c, {0: 90000})
            thresholds(rig, c, 0, -40000, 50000, 70000)
            rig.tm_write(c, UL_MASK, 0xfffeffff)
            rig.tm_write(c, CRIT_MASK, 0xc000fffe)
        rig.step(PERIOD)
        assert rig.read(gic_pending) & all_lines == all_lines
        rig.tm_write(0, UL_MASK, 0xffffffff)
        rig.write(gic_clear, ul0)
        assert rig.read(gic_pending) & all_lines == ul1 | crit0 | crit1
        rig.tm_write(1, CRIT_MASK, 0xc000ffff)
        rig.write(gic_clear, crit1)
        assert rig.read(gic_pending) & all_lines == ul1 | crit0
        rig.tm_write(0, CRIT_MASK, 0xc000ffff)
        rig.tm_write(1, UL_MASK, 0xffffffff)
        rig.write(gic_clear, all_lines)
        assert rig.read(gic_pending) & all_lines == 0
        for c in range(2):
            assert rig.tm_read(c, UL_STATUS) == 1 << 16
            assert rig.tm_read(c, CRIT_STATUS) == 1
        (rig.directory / "gic-routing.json").write_text(json.dumps({
            "output_intercepted": False, "gic_intids": [538, 539, 540, 541],
            "dt_spis": [506, 507, 508, 509], "gic_pending_register": gic_pending,
            "all_four_assertions_and_independent_deassertions_observed": True},
            indent=2) + "\n")
    finally:
        rig.close()

EXPECTED_CHECKS = 34
PAYLOAD_BYTES = 375
VALID_MASK_OFFSET = 296
CAPTURED_CODE_OFFSET = 319


def packed_controller_state(controller, before, *, reference=True, boot_mask=0,
                            initial_temperature=UNKNOWN):
    g, sensors = before["global"], before["sensors"]
    data = struct.pack(">IIQQIII", int(reference), controller, SROT[controller],
                       TM[controller], PERIOD, boot_mask, initial_temperature & 0xffffffff)
    assert len(data) == 36
    input_valid = sum(1 << i for i, item in enumerate(sensors) if item["input-valid"])
    data += struct.pack(">I16i", input_valid,
                        *(item["input-millicelsius"] for item in sensors))
    data += struct.pack(">8I", *(g[name] for name in (
        "control", "interrupt-enable", "upper-lower-status", "upper-lower-clear",
        "upper-lower-mask", "critical-status", "critical-clear", "critical-mask")))
    data += struct.pack(">16I", *(item["threshold"] for item in sensors))
    data += struct.pack(">16I", *(item["critical-threshold"] for item in sensors))
    data += struct.pack(">16H", *(item["last-code"] for item in sensors))
    assert len(data) == VALID_MASK_OFFSET
    data += struct.pack(">I???qII", g["valid-mask"], bool(g["trdy"] & FIRST_ROUND),
                        g["sample-pending"], g["deadline-exhausted"],
                        g["sample-deadline-ns"], g["captured-enable-mask"],
                        g["captured-valid-mask"])
    assert len(data) == CAPTURED_CODE_OFFSET
    data += struct.pack(">16HQQq", *(item["captured-code"] for item in sensors),
                        g["sample-rounds"], g["complete-rounds"], g["last-sample-ns"])
    assert len(data) == PAYLOAD_BYTES
    return data


def unknown_internal_state():
    # Only an internal serialization oracle: the unknown profile does not
    # expose raw runtime registers or sample status to QOM/the guest.
    g = {name: 0 for name in GLOBAL_STATE}
    g.update({"upper-lower-mask": 0xffffffff, "critical-mask": 0xc000ffff,
              "last-sample-ns": -1, "sample-pending": False,
              "deadline-exhausted": False, "upper-lower-irq": False,
              "critical-irq": False})
    sensor = {name: 0 for name in SENSOR_STATE}
    sensor.update({"threshold": 0x7ff800, "critical-threshold": 0x7ff,
                   "input-millicelsius": UNKNOWN, "input-valid": False, "valid": False})
    return [{"global": dict(g), "sensors": [dict(sensor) for _ in range(16)]}
            for _ in range(2)]


def actual_sections(path, before, *, reference=True, boot_mask=0, initial_temperature=UNKNOWN):
    data = path.read_bytes()
    name = TYPE.encode("ascii")
    sections = []
    for controller in range(2):
        # Both SysBus instances use the same bare VMState name. Their actual
        # instance IDs and immutable geometry distinguish the two sections.
        needle = bytes([len(name)]) + name + struct.pack(">II", controller, 1)
        expected = packed_controller_state(controller, before[controller],
                                           reference=reference, boot_mask=boot_mask,
                                           initial_temperature=initial_temperature)
        candidates, start = [], 0
        while True:
            index = data.find(needle, start)
            if index < 0:
                break
            header, payload = index - 5, index + len(needle)
            if (header >= 8 and data[header] in (1, 4) and
                    data[payload:payload + PAYLOAD_BYTES] == expected):
                candidates.append((header, payload))
            start = index + 1
        assert len(candidates) == 1, (controller, candidates)
        header, payload = candidates[0]
        # Independently decode the immutable prefix and actual critical
        # timing/temperature fields before recording any mutation offset.
        prefix = struct.unpack_from(">IIQQIII", data, payload)
        assert prefix == (int(reference), controller, SROT[controller], TM[controller],
                          PERIOD, boot_mask, initial_temperature & 0xffffffff)
        assert struct.unpack_from(">I", data, payload + VALID_MASK_OFFSET)[0] == before[controller]["global"]["valid-mask"]
        assert struct.unpack_from(">16H", data, payload + CAPTURED_CODE_OFFSET) == tuple(
            sensor["captured-code"] for sensor in before[controller]["sensors"])
        sections.append({
            "controller": controller, "instance_id": controller, "version": 1,
            "section_header_offset": header, "payload_offset": payload,
            "payload_bytes": PAYLOAD_BYTES, "srot_base": SROT[controller],
            "tm_base": TM[controller],
            "all_serialized_fields_match_source_qom": reference})
    assert sections[0]["payload_offset"] != sections[1]["payload_offset"]
    return sections


def take_snapshot(source, path, witness, before=None):
    source.write(RAM_WITNESS, witness)
    source.monitor("stop")
    assert source.monitor("query-status")["running"] is False
    if before is None:
        before = snapshot(source)
    source.monitor("migrate", file_channels(path))
    sent = wait_migration(source)
    assert sent["status"] == "completed"
    assert source.monitor("query-status")["status"] == "postmigrate"
    assert path.stat().st_size > 0
    sections = actual_sections(path, before, reference=source.reference,
                               boot_mask=source.boot_mask,
                               initial_temperature=source.initial_temperature)
    return before, sent, sections


def prepare_destination_with_different_inputs(target):
    configure(target, 0, {0: -20000, 5: 5000})
    configure(target, 1, {3: -17000, 8: 43000})
    target.tm_write(0, UL_THRESHOLD, 0x123456)
    target.tm_write(1, CRIT_THRESHOLD + 8 * 4, 0x321)
    assert target.get(0, "sensor0-input-millicelsius") == -20000
    assert target.get(1, "sensor8-input-millicelsius") == 43000
    assert target.get(0, "sample-pending") is True
    assert target.get(1, "sample-pending") is True


def restore_snapshot(target, path, source_clock, before, witness, irq_levels):
    target.set_clock(source_clock)
    target.monitor("migrate-incoming", file_channels(path))
    got = wait_migration(target)
    assert got["status"] == "completed"
    assert target.monitor("query-status")["running"] is False
    # QOM getters never sample or advance a timer. RAM drains the actual
    # IRQ notifications without touching either controller's MMIO.
    assert snapshot(target) == before
    assert target.read(RAM_WITNESS) == witness
    target.wire(*irq_levels)
    return got


def corrupt_one_field(original, changed_path, section, kind):
    data = original.read_bytes()
    payload = section["payload_offset"]
    if kind == "valid-mask-outside-sensors":
        offset, width = payload + VALID_MASK_OFFSET, 4
        old = struct.unpack_from(">I", data, offset)[0]
        assert old == 0
        new = 1 << 16
        replacement = struct.pack(">I", new)
    elif kind == "captured-code-reserved-bit":
        offset, width = payload + CAPTURED_CODE_OFFSET + 5 * 2, 2
        old = struct.unpack_from(">H", data, offset)[0]
        assert old == code(-12000)
        new = old | 0x1000
        replacement = struct.pack(">H", new)
    else:
        raise AssertionError(kind)
    changed = bytearray(data)
    changed[offset:offset + width] = replacement
    assert changed != data
    assert changed[:offset] == data[:offset] and changed[offset + width:] == data[offset + width:]
    changed_path.write_bytes(changed)
    assert original.read_bytes() == data
    return {**section, "modified_field": kind, "field_offset": offset,
            "modified_field_bytes": width, "old_value": old, "new_value": new,
            "only_one_field_modified": True,
            "changed_byte_count": sum(a != b for a, b in zip(data, changed))}


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


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = TsensRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, sent, got, sections, clock, irq_controller):
        report["migrations"].append({
            "name": name, "passed": True, "source_status": sent["status"],
            "target_status": got["status"], "source": sent, "destination": got,
            "source_virtual_ns": clock, "destination_virtual_ns": clock,
            "target_tsens_mmio_before_first_observation": False,
            "target_real_irq_observed_before_first_mmio": True,
            "observed_irq_controller": irq_controller,
            "destination_inputs_were_different_before_load": True,
            "controller_sections_verified": 2, "payload_bytes_per_controller": PAYLOAD_BYTES,
            "sections": sections, "stream_modified": False,
            "snapshot_sha256": digest(path), "snapshot_bytes": path.stat().st_size})

    try:
        source = new("migration-captured-source")
        configure(source, 0, {0: 10000, 5: -12000})
        source.step(200000)
        configure(source, 1, {8: 30000})
        source.step(200000)
        source.input(0, 0, 25000)
        source.input(1, 8, valid=False)
        assert source.last_clock == 400000
        captured_path = out / "tsens24xx-v1-two-captured-rounds.migration"
        before, sent, sections = take_snapshot(source, captured_path, 0x54533031)
        target = new("migration-captured-target", incoming=True)
        prepare_destination_with_different_inputs(target)
        got = restore_snapshot(target, captured_path, source.last_clock, before,
                               0x54533031, (False, False))
        completed("v1_two_controller_captured_inputs_configuration_and_ram_roundtrip",
                  captured_path, sent, got, sections, source.last_clock, 0)
        check("v1_both_captured_rounds_restore_before_first_tsens_mmio")

        target.monitor("cont")
        target.step(599999)
        assert target.get(0, "sample-rounds") == target.get(1, "sample-rounds") == 0
        assert target.get(0, "valid-mask") == target.get(1, "valid-mask") == 0
        target.step(1)
        assert target.get(0, "sample-rounds") == 1
        assert target.get(0, "valid-mask") == 0x21
        assert target.get(0, "sensor0-last-code") == code(10000)
        assert target.get(0, "sensor5-last-code") == code(-12000)
        assert target.get(0, "sensor0-captured-code") == code(25000)
        assert target.get(1, "sample-rounds") == 0
        target.step(199999)
        assert target.get(1, "valid-mask") == 0
        target.step(1)
        assert target.get(1, "sample-rounds") == 1
        assert target.get(1, "valid-mask") == 0x100
        assert target.get(1, "sensor8-last-code") == code(30000)
        assert target.get(1, "captured-valid-mask") == 0
        target.wire(False, False)
        # First device MMIO follows pure-QOM proof that both restored timers
        # fired at their saved deadlines and consumed the saved captures.
        check_temperature(target, 0, 0, 10000)
        check_temperature(target, 1, 8, 30000)
        target.step(PERIOD)
        check_temperature(target, 0, 0, 25000)
        assert status(target, 1, 8) & VALID == 0
        assert status(target, 1, 8) & 0xfff == code(30000)
        assert target.get(1, "trdy") == FIRST_ROUND
        assert source.get(0, "sample-rounds") == source.get(1, "sample-rounds") == 0
        check("v1_both_exact_deadlines_and_later_changed_or_withdrawn_sources_continue",
              remaining_deadlines_ns=[600000, 800000])

        source2 = new("migration-irqs-source", irq_controller=1)
        configure(source2, 0, {0: 24000})
        configure(source2, 1, {3: 90000, 8: -1000})
        thresholds(source2, 1, 3, -40000, 50000, 70000)
        thresholds(source2, 1, 8, 0, 100000, 120000)
        source2.tm_write(1, UL_MASK, 0xffffffff ^ (1 << 19) ^ (1 << 8))
        source2.tm_write(1, CRIT_MASK, 0xc000ffff ^ (1 << 3))
        source2.step(PERIOD)
        source2.wire(True, True)
        source2.tm_write(1, UL_CLEAR, 1 << 19)  # Keep this clear input held high.
        assert source2.get(1, "upper-lower-status") == 1 << 8
        source2.wire(True, True)
        source2.input(1, 3, 20000)
        source2.input(1, 8, 5000)
        source2.step(400000)
        irq_path = out / "tsens24xx-v1-latched-irqs-and-clear-hold.migration"
        before2, sent2, sections2 = take_snapshot(source2, irq_path, 0x54533032)
        target2 = new("migration-irqs-target", incoming=True, irq_controller=1)
        prepare_destination_with_different_inputs(target2)
        got2 = restore_snapshot(target2, irq_path, source2.last_clock, before2,
                                0x54533032, (True, True))
        completed("v1_actual_latched_irqs_held_clear_current_inputs_and_ram_roundtrip",
                  irq_path, sent2, got2, sections2, source2.last_clock, 1)
        check("v1_both_asserted_irq_wires_and_clear_hold_restore_before_first_tsens_mmio")

        target2.monitor("cont")
        target2.step(599999)
        assert target2.get(1, "sample-rounds") == 1
        target2.wire(True, True)
        target2.step(1)
        assert target2.get(0, "sample-rounds") == target2.get(1, "sample-rounds") == 2
        assert target2.get(1, "sensor3-last-code") == code(90000)
        assert target2.get(1, "sensor8-last-code") == code(-1000)
        assert target2.get(1, "sensor3-captured-code") == code(20000)
        assert target2.get(1, "sensor8-captured-code") == code(5000)
        assert target2.get(1, "upper-lower-status") == 1 << 8
        assert target2.get(1, "critical-status") == 1 << 3
        assert target2.get(1, "upper-lower-clear") == 1 << 19
        target2.wire(True, True)
        # Now clear the real latches and observe deassertion; the newly
        # captured in-range environment cannot resurrect the old events.
        clear_latches(target2, 1)
        target2.wire(False, False)
        target2.step(PERIOD)
        check_temperature(target2, 1, 3, 20000)
        check_temperature(target2, 1, 8, 5000)
        target2.wire(False, False)
        target2.input(1, 3, 100000)
        target2.step(PERIOD)
        target2.wire(False, False)
        target2.step(PERIOD)
        target2.wire(True, True)
        assert status(target2, 1, 3) & (UPPER | CRITICAL) == UPPER | CRITICAL
        assert source2.get(1, "sample-rounds") == 1
        assert source2.get(1, "upper-lower-clear") == 1 << 19
        check("v1_pending_sample_consumes_saved_capture_then_new_real_irq_events_continue",
              remaining_deadline_ns=600000)

        unknown = new("migration-unknown-source", reference=False)
        unknown_path = out / "tsens24xx-v1-unknown-profile.migration"
        _, unknown_sent, _ = take_snapshot(unknown, unknown_path, 0x54533030,
                                          unknown_internal_state())
        for name, path, source_reference, target_reference, source_state in (
                ("reference_to_unknown_profile_is_rejected", captured_path, True, False, sent),
                ("unknown_to_reference_profile_is_rejected", unknown_path, False, True,
                 unknown_sent)):
            rejected = new("migration-" + name, incoming=True, reference=target_reference)
            for c in range(2):
                assert rejected.get(c, "enabled-reference") is target_reference
            old_hash = digest(path)
            diagnostic = f"{int(target_reference):x} != {int(source_reference):x}"
            failure = expect_incoming_failure(rejected, path, diagnostic)
            assert digest(path) == old_hash
            report["rejected_migrations"].append({
                "name": name, "passed": True, "actual_source_stream": True,
                "stream_modified": False, "source_status": source_state["status"],
                "source_reference": source_reference, "target_reference": target_reference,
                "snapshot_sha256": old_hash, "snapshot_bytes": path.stat().st_size,
                "profile_mismatch": diagnostic, **failure})
            check(name, stream_modified=False)

        original_hash = digest(captured_path)
        for kind, diagnostic in (
                ("valid-mask-outside-sensors", TYPE + ": invalid migrated TSENS state"),
                ("captured-code-reserved-bit", TYPE + ": invalid migrated sensor 5 state")):
            changed = out / ("tsens24xx-v1-corrupted-" + kind + ".migration")
            mutation = corrupt_one_field(captured_path, changed, sections[0], kind)
            assert digest(captured_path) == original_hash
            rejected = new("migration-corrupt-" + kind, incoming=True)
            failure = expect_incoming_failure(rejected, changed, diagnostic)
            assert digest(captured_path) == original_hash
            report["corrupted_stream_rejections"].append({
                "name": kind, "passed": True, "actual_source_stream": True,
                "stream_modified": True, "source_status": sent["status"],
                "legitimate_alternate_configuration": False,
                "original_stream_unchanged": True,
                "original_snapshot_sha256": original_hash,
                "corrupted_snapshot_sha256": digest(changed),
                "original_snapshot_bytes": captured_path.stat().st_size,
                "corrupted_snapshot_bytes": changed.stat().st_size,
                **mutation, **failure})
            check("one_real_" + kind.replace("-", "_") + "_field_corruption_is_rejected",
                  stream_modified=True, only_one_field_modified=True)
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
        "schema": "tb371fc-tsens24xx-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [],
        "expected_completed_migrations": 2, "migrations": [],
        "expected_rejected_migrations": 2, "rejected_migrations": [],
        "expected_corrupted_stream_rejections": 2, "corrupted_stream_rejections": [],
        "controller_count": 2, "sensor_count": 32, "sensors_per_controller": 16,
        "register_count": 114, "sparse_window_count": 6, "mapped_bytes": 456,
        "registers_per_controller": 57, "mapped_bytes_per_controller": 228,
        "irq_count": 4, "sampling_period_ns": PERIOD,
        "vmstate_version": 1, "vmstate_payload_bytes_per_controller": PAYLOAD_BYTES,
        "reference_profile_is_default": False,
        "reference_boot_state_is_measured": False,
        "environment_input_is_host_controlled": True,
        "analogue_calibration_implemented": False,
        "watchdog_producer_implemented": False,
        "cycle_monitor_producer_implemented": False,
        "original_dt_srot_declared_bytes_per_controller": 4,
        "source_observed_control_offset_beyond_declared_srot_size": 4,
        "original_dt_tm_declared_bytes_per_controller": 0x1ff,
        "unique_dt_referenced_sensor_counts": [16, 9],
        "dt_references_are_measured_firmware_enable_masks": False,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "scope": "Actual compiled two-controller TSENS transactions, captured "
                 "external reference inputs, virtual sample deadlines, signed "
                 "thresholds, real IRQ outputs/GIC routes and state/RAM migration",
        "source_commit": SOURCE_COMMIT,
        "source_url": "https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/"
                      + SOURCE_COMMIT + "/drivers/thermal/tsens2xxx.c",
        "source_sha256": {"tsens2xxx.c": SOURCE_SHA256,
                         "tsens.h": "dc878c52e6d0cd41b102759018c15b7982004957562ade83db3cd3fa5db0970e",
                         "msm-tsens.c": "07de996bd2b5e4f4bcf06508535a75f70fa59b9b174f9dad397284753658e282"},
        "dtb_sha256": DTB_SHA256, "dt_evidence_blob": DT_EVIDENCE_BLOB}
    start = time.monotonic()
    current = None
    try:
        assert len(ORDINARY_TESTS) == 21
        for test in ORDINARY_TESTS:
            current = test.__name__
            rig = None
            try:
                controller = int(current in IRQ_CONTROLLER_ONE)
                rig = TsensRig(args, out / current, irq_controller=controller)
                test(rig)
                report["checks"].append({"name": current, "passed": True,
                                         "irq_controller": controller})
            finally:
                if rig:
                    rig.close()
        for test in (
                default_unknown_profile_rejects_raw_state_and_cannot_become_a_thermal_success,
                explicit_handoff_requires_known_inputs_and_valid_configuration,
                paused_tcg_environment_properties_are_inputs_and_do_not_execute_a_conversion,
                source_bounded_windows_preserve_version_calibration_watchdog_and_0c_holes,
                both_controllers_drive_all_four_real_gic_routes_without_interception):
            current = test.__name__
            test(args, out)
            report["checks"].append({"name": current, "passed": True})
        current = "actual_state_migration_and_rejections"
        migration_checks(args, out, report)
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 2 and
            all(item["source_status"] == item["target_status"] == "completed"
                and item["controller_sections_verified"] == 2
                and item["payload_bytes_per_controller"] == PAYLOAD_BYTES
                and item["target_tsens_mmio_before_first_observation"] is False
                and item["target_real_irq_observed_before_first_mmio"] is True
                and item["destination_inputs_were_different_before_load"] is True
                and item["stream_modified"] is False
                and len(item["sections"]) == 2
                and all(section["all_serialized_fields_match_source_qom"] is True
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
                and item["original_stream_unchanged"] is True
                and item["source_status"] == "completed"
                and item["specific_load_failure_observed"] is True
                and item["destination_exit"] == 1
                for item in report["corrupted_stream_rejections"]) and
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











