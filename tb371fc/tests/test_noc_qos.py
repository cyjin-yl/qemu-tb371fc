#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the compiled TB371FC RPMh NoC QoS reference configuration.

Official msm_bus_noc_rpmh.c at OnePlusOSS commit 94304e12a3509eac7b3716bf574ce0b1fcaacbae
defines four raw register words. The captured TB371FC DT identifies 47 physical
ports; seven display references alias existing ports. Tests preserve the actual
vendor shifts and whole-word writes, including fields its RMW sequence clears.
These checks do not execute a guest, enforce bandwidth, or emulate SBM.
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

TYPE = "qcom-noc-qos"
EXPECTED_CHECKS = 20
SOURCE_COMMIT = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_URL = ("https://raw.githubusercontent.com/OnePlusOSS/"
              "android_kernel_oneplus_sm8250/" + SOURCE_COMMIT +
              "/drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c")
SOURCE_SHA256 = "9e408e18745857c575be792a92cb3eb7bcab1fb51b181cc389fd2b81213560a6"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
LAYOUT_EVIDENCE_BLOB = "77b1851faf6063d4c1a88d11c524de1b239005e6"
RAW_DT_EVIDENCE_BLOB = "c300271fab1acabb6d1b45ddcf626f5e2f091d25"
INDEPENDENT_DT_DECODE_BLOB = "86be9c194406e6853aca493eefed1049358fde92"
GCC = 0x00100000
RAM_WITNESS = 0x80010000

# Independently decoded be32 reg/base-offset/qos-off/qport cells from the
# captured DT. qos-off is the port stride, not the fabric's base offset.
FABRICS = (
    ("aggre1", 0x016e0000, 0x02000, 0x1000, (0, 1, 2, 3, 4, 5, 6, 7, 8)),
    ("aggre2", 0x01700000, 0x03000, 0x1000, (0, 1, 2, 3, 4, 7, 8, 9, 11, 12)),
    ("compute", 0x01700000, 0x33000, 0x0800, (6, 7)),
    ("gem", 0x09100000, 0x21000, 0x1000,
     (0, 1, 2, 3, 64, 65, 66, 67, 127, 128, 129, 130, 131)),
    ("mmss", 0x01740000, 0x0a000, 0x0800, (0, 1, 2, 4, 5, 6, 8, 10, 12, 13, 14)),
    ("system", 0x01620000, 0x12000, 0x1000, (0, 1)),
)
PORTS = tuple((name, index, base + offset + stride * index)
              for name, base, offset, stride, indices in FABRICS for index in indices)
REGISTER_NAMES = ("mainctl", "limitbw", "regul0ctl", "regul0bw")
OFFSETS = (0x08, 0x18, 0x40, 0x48)
MASKS = (0x00000079, 0x03ff07ff, 0x00007703, 0x03ff07ff)
DISPLAY_ALIAS_PORTS = (("gem", 2), ("gem", 3), ("gem", 66), ("gem", 67),
                       ("mmss", 6), ("mmss", 8), ("mmss", 10))
SBM_ADDRESSES = tuple(0x01506000 + offset
                      for offset in (0x80, 0x84, 0x88, 0x8c, 0x90, 0x94, 0x100, 0x104))


def port(fabric, number):
    matches = [item for item in PORTS if item[:2] == (fabric, number)]
    assert len(matches) == 1, (fabric, number, matches)
    return matches[0]


def port_path(item):
    return f"/machine/noc-qos-{item[0]}-{item[1]}"


def matrix_values():
    result = {}
    for index, item in enumerate(PORTS, 1):
        words = (((index & 7) << 4) | (((index >> 3) & 1) << 3) | 1,
                 ((0x101 + index * 13) & 0x7ff) |
                 (((0x41 + index * 7) & 0x3ff) << 16),
                 (((index + 1) & 7) << 12) | (((index + 3) & 7) << 8) | (index & 3),
                 ((0x207 + index * 19) & 0x7ff) |
                 (((0x123 + index * 11) & 0x3ff) << 16))
        assert all(value != 0 and value & ~mask == 0
                   for value, mask in zip(words, MASKS)), (item, words)
        result[item[2]] = words
    assert len(set(result.values())) == 47
    return result


def base_command(args, reference=True):
    command = [args.qemu, "-machine", args.machine, "-accel", "qtest",
               "-m", "128M", "-display", "none", "-monitor", "none",
               "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]
    # False deliberately omits the switch: verify the model's actual default.
    if reference:
        command += ["-global", TYPE + ".enabled-reference=on"]
    return command


class NocRig(Rig):
    def __init__(self, args, directory, *, incoming=False, reference=True):
        self.args = args
        self.reference = reference
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qnoc-"))
        self.last_clock = 0
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = base_command(args, reference) + [
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

    def read(self, item, register):
        return self.raw_read(item[2] + OFFSETS[register])

    def write(self, item, register, value):
        self.raw_write(item[2] + OFFSETS[register], value)

    def get(self, item, name):
        return self.monitor("qom-get", {"path": port_path(item), "property": name})

    def expect_word(self, item, register, value):
        assert self.read(item, register) == value, (item, register, value)
        assert self.get(item, REGISTER_NAMES[register]) == value, (item, register, value)

    def write_checked(self, item, register, value):
        self.write(item, register, value)
        self.expect_word(item, register, value)

    def qom_snapshot(self):
        return {item[2]: tuple(self.get(item, name) for name in REGISTER_NAMES)
                for item in PORTS}

    def mmio_snapshot(self):
        return {item[2]: tuple(self.read(item, register) for register in range(4))
                for item in PORTS}

    def write_matrix(self, values):
        for item in PORTS:
            for register, value in enumerate(values[item[2]]):
                self.write_checked(item, register, value)

    def expect_matrix(self, values, *, qom_first=False):
        if qom_first:
            assert self.qom_snapshot() == values
            assert self.mmio_snapshot() == values
        else:
            assert self.mmio_snapshot() == values
            assert self.qom_snapshot() == values

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns, (before, ns, self.last_clock)
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


def disabled_qom_error(rig, item, name):
    try:
        rig.get(item, name)
    except AssertionError as error:
        assert "reference profile is disabled" in str(error), str(error)
    else:
        raise AssertionError((item, name, "disabled register exposed as known state"))


def zero_values():
    return {item[2]: (0, 0, 0, 0) for item in PORTS}


def cold_reference_has_47_physical_identities_and_188_zero_words(rig):
    assert len(PORTS) == len({item[2] for item in PORTS}) == 47
    assert len(PORTS) * len(OFFSETS) == 188
    assert [len(item[4]) for item in FABRICS] == [9, 10, 2, 13, 11, 2]
    children = rig.monitor("qom-list", {"path": "/machine"})
    actual = {item["name"] for item in children if item["name"].startswith("noc-qos-")}
    expected = {port_path(item).rsplit("/", 1)[-1] for item in PORTS}
    assert actual == expected, (actual, expected)
    for item in PORTS:
        assert rig.get(item, "enabled-reference") is True
        assert rig.get(item, "physical-port-address") == item[2]
    rig.expect_matrix(zero_values())
    assert port("aggre1", 7)[2] + OFFSETS[0] == 0x016e9008


def every_documented_bit_is_immediate_and_independent(rig):
    # Each fabric contributes a physical port for every walking bit; every
    # port additionally holds all known bits. Reads do not cause a commit.
    for name, _, _, _, indices in FABRICS:
        item = port(name, indices[0])
        for register, mask in enumerate(MASKS):
            for bit in range(32):
                if mask & (1 << bit):
                    rig.write_checked(item, register, 1 << bit)
            rig.write_checked(item, register, 0)
    for item in PORTS:
        for register, mask in enumerate(MASKS):
            rig.write_checked(item, register, mask)
    expected = {item[2]: MASKS for item in PORTS}
    rig.expect_matrix(expected)
    assert not device_diagnostics(rig.log_path.read_text(errors="replace"))


def full_word_replacement_clears_fields_without_a_merge_or_ack(rig):
    for name, _, _, _, indices in FABRICS:
        item = port(name, indices[-1])
        for register, mask in enumerate(MASKS):
            low = mask & -mask
            high = 1 << (mask.bit_length() - 1)
            for value in (mask, low, high, 0, mask ^ low, mask ^ high, 0):
                rig.write_checked(item, register, value)
    rig.expect_matrix(zero_values())


def all_188_words_high_port_ids_and_display_aliases_are_independent(rig):
    expected = matrix_values()
    rig.write_matrix(expected)
    rig.expect_matrix(expected)
    for number in (64, 65, 66, 67, 127, 128, 129, 130, 131):
        item = port("gem", number)
        assert rig.get(item, "physical-port-address") == 0x09121000 + 0x1000 * number
        assert tuple(rig.read(item, index) for index in range(4)) == expected[item[2]]
    # Display clients address these same physical ports; the board must not
    # manufacture a second state object or let an overlay shadow their MMIO.
    for fabric, number in DISPLAY_ALIAS_PORTS:
        item = port(fabric, number)
        old = expected[item[2]]
        changed = (old[0] ^ 1, old[1] ^ 1, old[2] ^ 1, old[3] ^ 1)
        for register, value in enumerate(changed):
            rig.write_checked(item, register, value)
        expected[item[2]] = changed
        rig.expect_matrix(expected)


def literal_vendor_13_write_sequence_keeps_its_actual_rmw_side_effects(rig):
    item = port("aggre1", 7)
    for register, value in enumerate(MASKS):
        rig.write_checked(item, register, value)
    writes = []

    def issue(register, value, expected):
        assert value == expected, (register, value, expected)
        rig.write_checked(item, register, value)
        writes.append({"register": REGISTER_NAMES[register],
                       "address": item[2] + OFFSETS[register], "value": value})

    # msm_bus_noc_rpmh.c:165-307, executed in qos_init's actual call order.
    # Values below are literal independent expectations, not a mask-only
    # model of what a cleaner priority/bandwidth API might intend.
    old = rig.read(item, 0)
    issue(0, (old & ~0x70) | ((5 << 4) & 0x70), 0x59)
    old = rig.read(item, 0)
    issue(0, old & ~1, 0x58)
    old = rig.read(item, 1)
    issue(1, (old & ~0x7ff) | 0x345, 0x03ff0345)
    old = rig.read(item, 1)
    issue(1, (old & ~0x03ff0000) | ((0x155 << 17) & 0x03ff0000), 0x02aa0345)
    old = rig.read(item, 0)
    issue(0, (old & ~1) | 1, 0x59)

    # The driver's pre-masks discard unrelated fields before a whole-word
    # write. Its priority shifts are crossed; SAT is shifted by 17, not 16.
    old = rig.read(item, 2) & 3
    issue(2, old & ~3, 0)
    old = rig.read(item, 2) & 0x7000
    issue(2, (old & ~0x7000) | ((3 << 8) & 0x7000), 0)
    old = rig.read(item, 2) & 0x700
    issue(2, (old & ~0x700) | ((6 << 12) & 0x700), 0)
    old = rig.read(item, 3) & 0x7ff
    issue(3, (old & ~0x7ff) | 0x123, 0x123)
    old = rig.read(item, 3) & 0x03ff0000
    issue(3, (old & ~0x03ff0000) | ((0x2aa << 17) & 0x03ff0000), 0x01540000)
    old = rig.read(item, 2)
    issue(2, (old & ~2) | (1 << 1), 2)
    old = rig.read(item, 2)
    issue(2, (old & ~1) | 0, 2)
    old = rig.read(item, 0)
    issue(0, (old & ~8) | (1 << 3), 0x59)
    assert len(writes) == 13
    assert tuple(rig.read(item, index) for index in range(4)) == (
        0x59, 0x02aa0345, 2, 0x01540000)
    assert rig.read(item, 3) & 0x7ff == 0
    (rig.directory / "literal-vendor-writes.json").write_text(
        json.dumps(writes, indent=2) + "\n")
    for other in PORTS:
        if other != item:
            assert tuple(rig.read(other, index) for index in range(4)) == (0, 0, 0, 0)


def aligned_byte_buffers_have_little_endian_word_order(rig):
    # QTest's bulk read/write uses actual address_space transactions. Four
    # aligned bytes form one 32-bit device access, not four byte MMIO reads.
    for name, _, _, _, indices in FABRICS:
        item = port(name, indices[0])
        values = (0x59, 0x01230567, 0x6502, 0x02ab0456)
        for register, value in enumerate(values):
            address = item[2] + OFFSETS[register]
            rig.write_checked(item, register, value)
            response = rig.command(f"read {address:#x} 4")[0]
            assert response.startswith("0x")
            assert bytes.fromhex(response[2:]) == struct.pack("<I", value), response
            changed = value ^ (MASKS[register] & -MASKS[register])
            payload = struct.pack("<I", changed).hex()
            rig.command(f"write {address:#x} 4 0x{payload}")
            rig.expect_word(item, register, changed)


def reserved_bits_reject_the_whole_write_without_changing_any_word(rig):
    expected = matrix_values()
    rig.write_matrix(expected)
    for item in PORTS:
        for register, mask in enumerate(MASKS):
            old_log = rig.log_path.read_text(errors="replace")
            old = expected[item[2]][register]
            known_bit = mask & -mask
            reserved = [bit for bit in range(32) if not mask & (1 << bit)]
            # Test every unknown bit on the faulting physical port, and both
            # ends of each unknown-bit set at every other physical register.
            bits = reserved if item == port("aggre1", 7) else (reserved[0], reserved[-1])
            for bit in bits:
                bad = (old ^ known_bit) | (1 << bit)
                assert bad & ~mask
                rig.write(item, register, bad)
                assert tuple(rig.read(item, index) for index in range(4)) == expected[item[2]]
                assert rig.get(item, REGISTER_NAMES[register]) == old
            fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
            diagnostic = (f"{TYPE}: unsupported bits at port {item[2]:#x}"
                          f" +0x{OFFSETS[register]:x}")
            assert device_diagnostics(fresh) == [diagnostic] * len(bits), (item, register, fresh)
            assert not core_diagnostics(fresh), fresh
    rig.expect_matrix(expected)


def narrow_and_unaligned_accesses_have_exact_core_failures_and_no_partial_write(rig):
    expected = matrix_values()
    rig.write_matrix(expected)
    item = port("aggre1", 7)
    width = "invalid size (min:4 max:4)"
    for register, offset in enumerate(OFFSETS):
        address = item[2] + offset
        region = TYPE + "-" + REGISTER_NAMES[register]
        operations = (
            (f"readb {address:#x}", [("read", 0, 1, region, width)]),
            (f"readw {address:#x}", [("read", 0, 2, region, width)]),
            (f"readw {address + 1:#x}", [("read", 1, 1, region, width),
                                       ("read", 2, 1, region, width)]),
            (f"readl {address + 1:#x}", [("read", 1, 1, region, width),
                                       ("read", 2, 2, region, width),
                                       ("read", address + 4, 1, "(null)", "rejected")]),
            (f"writeb {address:#x} 0", [("write", 0, 1, region, width)]),
            (f"writew {address:#x} 0", [("write", 0, 2, region, width)]),
            (f"writel {address + 1:#x} 0", [("write", 1, 1, region, width),
                                          ("write", 2, 2, region, width),
                                          ("write", address + 4, 1, "(null)", "rejected")]),
        )
        for operation, errors in operations:
            old_log = rig.log_path.read_text(errors="replace")
            rig.command(operation)
            assert tuple(rig.read(item, index) for index in range(4)) == expected[item[2]]
            fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(fresh) == errors, (operation, errors, fresh)
            assert not device_diagnostics(fresh), fresh
    rig.expect_matrix(expected)


def raw_qom_state_identity_and_realized_profile_are_read_only(rig):
    expected = matrix_values()
    rig.write_matrix(expected)
    for name, _, _, _, indices in FABRICS:
        item = port(name, indices[0])
        attempts = [("physical-port-address", item[2] + 0x1000),
                    ("enabled-reference", False), ("enabled-reference", True)]
        attempts += [(register, MASKS[index])
                     for index, register in enumerate(REGISTER_NAMES)]
        for name, value in attempts:
            before = rig.get(item, name)
            try:
                rig.monitor("qom-set", {"path": port_path(item),
                                       "property": name, "value": value})
            except AssertionError as error:
                assert "error" in str(error), str(error)
            else:
                raise AssertionError((item, name, "realized/read-only property was writable"))
            assert rig.get(item, name) == before
    rig.expect_matrix(expected)


def virtual_time_and_repeated_reads_do_not_invent_commits_or_ack_bits(rig):
    expected = matrix_values()
    rig.write_matrix(expected)
    for delta in (1, 999, 1_000_000, 1_000_000_000):
        rig.step(delta)
        rig.expect_matrix(expected, qom_first=True)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    time.sleep(0.02)
    for _ in range(3):
        assert rig.qom_snapshot() == expected
    rig.monitor("cont")
    rig.step(1)
    rig.expect_matrix(expected)
    assert not device_diagnostics(rig.log_path.read_text(errors="replace"))


def system_reset_clears_all_reference_words_with_an_independent_reset_witness(rig):
    expected = matrix_values()
    rig.write_matrix(expected)
    rig.raw_write(GCC + 4, 31)
    assert rig.raw_read(GCC + 4) == 31
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.raw_read(GCC + 4) == 0,
                 "actual reset clears the independent nonzero GCC witness")
    # QTest does not drain an asynchronous RESET event from the QMP socket.
    rig.monitor("query-status")
    assert any(event.get("event") == "RESET" for event in rig.events), rig.events
    # Observe stored state before any NoC MMIO, so a read cannot repair reset.
    assert rig.qom_snapshot() == zero_values()
    rig.expect_matrix(zero_values())
    for item in PORTS:
        assert rig.get(item, "enabled-reference") is True
        assert rig.get(item, "physical-port-address") == item[2]
    # A cleared port must remain writable without another enable handshake.
    item = port("gem", 131)
    for register, value in enumerate(expected[item[2]]):
        rig.write_checked(item, register, value)


ORDINARY_TESTS = (
    cold_reference_has_47_physical_identities_and_188_zero_words,
    every_documented_bit_is_immediate_and_independent,
    full_word_replacement_clears_fields_without_a_merge_or_ack,
    all_188_words_high_port_ids_and_display_aliases_are_independent,
    literal_vendor_13_write_sequence_keeps_its_actual_rmw_side_effects,
    aligned_byte_buffers_have_little_endian_word_order,
    reserved_bits_reject_the_whole_write_without_changing_any_word,
    narrow_and_unaligned_accesses_have_exact_core_failures_and_no_partial_write,
    raw_qom_state_identity_and_realized_profile_are_read_only,
    virtual_time_and_repeated_reads_do_not_invent_commits_or_ack_bits,
    system_reset_clears_all_reference_words_with_an_independent_reset_witness,
)


def assert_default_profile_boundaries(rig, *, mmio=True):
    for item in PORTS:
        assert rig.get(item, "enabled-reference") is False
        assert rig.get(item, "physical-port-address") == item[2]
        for name in REGISTER_NAMES:
            disabled_qom_error(rig, item, name)
    if mmio:
        for item in PORTS:
            for register, offset in enumerate(OFFSETS):
                old_log = rig.log_path.read_text(errors="replace")
                rig.write(item, register, MASKS[register])
                rig.read(item, register)
                fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
                assert device_diagnostics(fresh) == [
                    f"{TYPE}: unmodeled write at port {item[2]:#x} +0x{offset:x} size 4",
                    f"{TYPE}: unmodeled read at port {item[2]:#x} +0x{offset:x} size 4"
                ], (item, register, fresh)
                assert not core_diagnostics(fresh), fresh


def default_profile_never_exposes_reference_zero_or_accepts_mmio(args, out):
    rig = NocRig(args, out / "default-profile", reference=False)
    try:
        assert_default_profile_boundaries(rig)
        rig.raw_write(GCC + 4, 19)
        assert rig.raw_read(GCC + 4) == 19
        rig.monitor("system_reset")
        rig.wait_for(lambda: rig.raw_read(GCC + 4) == 0, "default-profile reset witness")
        rig.monitor("query-status")
        assert any(event.get("event") == "RESET" for event in rig.events), rig.events
        assert_default_profile_boundaries(rig, mmio=False)
        # Realize-time policy is immutable in both directions, including off.
        first = PORTS[0]
        try:
            rig.monitor("qom-set", {"path": port_path(first),
                                   "property": "enabled-reference", "value": True})
        except AssertionError as error:
            assert "error" in str(error)
        else:
            raise AssertionError("default profile could be enabled after realization")
        assert rig.get(first, "enabled-reference") is False
    finally:
        rig.close()


def exactly_188_four_byte_windows_leave_legacy_sbm_and_unlisted_ports_unmapped(args, out):
    rig = NocRig(args, out / "sparse-physical-layout")
    try:
        mtree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory / "mtree.txt").write_text(mtree)
        found = set()
        names = "|".join(re.escape(TYPE + "-" + name) for name in REGISTER_NAMES)
        pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + names + r")(?:\s|$)"
        for line in mtree.splitlines():
            match = re.search(pattern, line)
            if match:
                first, last, name = match.groups()
                found.add((int(first, 16), int(last, 16), name))
        expected_windows = {
            (item[2] + offset, item[2] + offset + 3, TYPE + "-" + name)
            for item in PORTS for offset, name in zip(OFFSETS, REGISTER_NAMES)}
        assert found == expected_windows, (found ^ expected_windows, mtree)
        assert len(found) == 188 and sum(last - first + 1 for first, last, _ in found) == 752
        expected = matrix_values()
        rig.write_matrix(expected)
        # Old MODE/BW/SAT offsets are not the four RPMh words. Neighbouring
        # words and unlisted ports must not be turned into a permissive RAM.
        holes = {item[2] + offset for item in PORTS
                 for offset in (0, 4, 0x0c, 0x10, 0x14, 0x1c, 0x3c, 0x44, 0x4c)}
        holes.update(SBM_ADDRESSES)
        holes.update((0x01708008, 0x01733008, 0x0174b808, 0x01634008))
        assert all(not (first <= address <= last)
                   for address in holes for first, last, _ in expected_windows)
        for address in sorted(holes):
            old_log = rig.log_path.read_text(errors="replace")
            rig.raw_write(address, 0x12345678)
            rig.raw_read(address)
            fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(fresh) == [
                ("write", address, 4, "(null)", "rejected"),
                ("read", address, 4, "(null)", "rejected")], (hex(address), fresh)
            assert not device_diagnostics(fresh), fresh
        rig.expect_matrix(expected)
        (rig.directory / "expected-layout.json").write_text(json.dumps({
            "ports": [{"fabric": f, "port": p, "base": base} for f, p, base in PORTS],
            "windows": sorted(expected_windows), "holes_tested": sorted(holes),
            "sbm_words_tested": list(SBM_ADDRESSES)}, indent=2) + "\n")
    finally:
        rig.close()


def find_real_port_sections(snapshot, reference, expected):
    data = snapshot.read_bytes()
    suffix = TYPE.encode("ascii")
    matches = {}
    scan = 0
    while True:
        index = data.find(suffix, scan)
        if index < 0:
            break
        end = index + len(suffix)
        # FULL/START headers have type/u32-section/u8-name-length/name,
        # u32-instance/u32-version, then the 28-byte device payload. Sysbus
        # currently uses the bare TYPE; allow a genuine VMState path prefix.
        for start in range(max(14, end - 255), index + 1):
            header = start - 6
            length = end - start
            if data[start - 1] != length or data[header] not in (1, 4):
                continue
            name = data[start:end]
            if name != suffix and not name.endswith(b"/" + suffix):
                continue
            try:
                name_string = name.decode("ascii")
            except UnicodeDecodeError:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_./,:@-]+", name_string):
                continue
            if end + 36 > len(data):
                continue
            instance, version, saved_profile = struct.unpack_from(">III", data, end)
            address = struct.unpack_from(">Q", data, end + 12)[0]
            if version != 1 or saved_profile != int(reference) or address not in expected:
                continue
            words = struct.unpack_from(">IIII", data, end + 20)
            if words != expected[address]:
                continue
            assert address not in matches, (address, matches.get(address), header)
            matches[address] = {
                "section_header_offset": header, "section_name": name_string,
                "section_id": struct.unpack_from(">I", data, header + 1)[0],
                "instance_id": instance, "section_version": version,
                "profile_field_offset": end + 8, "address_field_offset": end + 12,
                "register_fields_offset": end + 20,
                "saved_profile": saved_profile, "physical_port_address": address,
                "saved_registers": words, "device_payload_bytes": 28}
        scan = index + 1
    assert set(matches) == set(expected), (sorted(matches), sorted(expected))
    assert len(matches) == 47
    assert len({entry["section_header_offset"] for entry in matches.values()}) == 47
    return matches


def replace_one_field(source, target, section, kind):
    data = source.read_bytes()
    changed = bytearray(data)
    if kind == "physical-port-address":
        offset = section["address_field_offset"]
        width = 8
        old = section["physical_port_address"]
        new = old + 0x1000
        assert old == port("aggre1", 7)[2] and new == port("aggre1", 8)[2]
        assert data[offset:offset + width] == struct.pack(">Q", old)
        changed[offset:offset + width] = struct.pack(">Q", new)
    elif kind == "reserved-mainctl-bit":
        offset = section["register_fields_offset"]
        width = 4
        old = section["saved_registers"][0]
        new = old | 2
        assert old & ~MASKS[0] == 0 and new & ~MASKS[0] == 2
        assert data[offset:offset + width] == struct.pack(">I", old)
        changed[offset:offset + width] = struct.pack(">I", new)
    else:
        raise AssertionError(kind)
    assert changed != data
    assert changed[:offset] == data[:offset] and changed[offset + width:] == data[offset + width:]
    target.write_bytes(changed)
    assert source.read_bytes() == data
    return {**section, "modified_field": kind, "field_offset": offset,
            "modified_field_bytes": width, "old_field_value": old, "new_field_value": new,
            "only_one_field_modified": True,
            "changed_byte_count": sum(a != b for a, b in zip(data, changed))}


def take_snapshot(source, path, expected, witness):
    source.raw_write(RAM_WITNESS, witness)
    source.monitor("stop")
    assert source.monitor("query-status")["running"] is False
    source.monitor("migrate", file_channels(path))
    state = wait_migration(source)
    assert state["status"] == "completed"
    assert source.monitor("query-status")["status"] == "postmigrate"
    assert path.stat().st_size > 0
    sections = find_real_port_sections(path, source.reference, expected)
    return state, sections


def restore_snapshot(target, path, expected, witness, destination_ns):
    target.set_clock(destination_ns)
    target.monitor("migrate-incoming", file_channels(path))
    state = wait_migration(target)
    assert state["status"] == "completed"
    assert target.monitor("query-status")["running"] is False
    # Raw QOM getters have no state update. Observe every restored word and
    # immutable identity before any NoC MMIO or virtual-clock advance.
    for item in PORTS:
        assert target.get(item, "physical-port-address") == item[2]
        assert target.get(item, "enabled-reference") is target.reference
    if target.reference:
        assert target.qom_snapshot() == expected
    else:
        assert_default_profile_boundaries(target, mmio=False)
    assert target.raw_read(RAM_WITNESS) == witness
    return state


def failed_incoming(target, path, expected_diagnostic):
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
    assert TYPE in stderr and expected_diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {"destination_exit": code, "request_sent": request_sent,
            "command_error": command_error, "device": TYPE,
            "expected_diagnostic": expected_diagnostic,
            "specific_load_failure_observed": True}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = NocRig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, sent, got, sections, source_ns, target_ns, reference):
        report["migrations"].append({
            "name": name, "passed": True, "source_status": sent["status"],
            "target_status": got["status"], "source": sent, "destination": got,
            "source_reference": reference, "target_reference": reference,
            "source_virtual_ns": source_ns, "destination_virtual_ns": target_ns,
            "target_device_mmio_before_first_observation": False,
            "actual_device_sections_verified": len(sections),
            "actual_register_words_verified": len(sections) * 4,
            "stream_modified": False, "snapshot_sha256": digest(path),
            "snapshot_bytes": path.stat().st_size})

    try:
        expected = matrix_values()
        source = new("migration-reference-source")
        source.write_matrix(expected)
        source.set_clock(456_789)
        source.expect_matrix(expected)
        reference_path = out / "noc-qos-v1-reference-all-47-ports.migration"
        sent, sections = take_snapshot(source, reference_path, expected, 0x4e4f4331)
        (out / "reference-stream-sections.json").write_text(
            json.dumps(sections, indent=2) + "\n")
        target = new("migration-reference-target", incoming=True)
        target_ns = 987_654_321
        got = restore_snapshot(target, reference_path, expected, 0x4e4f4331, target_ns)
        completed("v1_reference_all_47_physical_ports_and_ram_roundtrip",
                  reference_path, sent, got, sections, source.last_clock, target_ns, True)
        check("v1_all_188_nonzero_words_and_identities_restore_before_device_mmio",
              target_device_mmio_before_first_observation=False)
        target.expect_matrix(expected, qom_first=True)

        target.monitor("cont")
        target.step(12345)
        changed = dict(expected)
        chosen = port("aggre1", 7)
        changed[chosen[2]] = tuple(value ^ (mask & -mask)
                                    for value, mask in zip(expected[chosen[2]], MASKS))
        for register, value in enumerate(changed[chosen[2]]):
            target.write_checked(chosen, register, value)
        target.expect_matrix(changed)
        # Source and destination are separate compiled processes. A target
        # write must not mutate the stopped source or alias another port.
        assert source.qom_snapshot() == expected
        target.raw_write(GCC + 4, 29)
        assert target.raw_read(GCC + 4) == 29
        target.monitor("system_reset")
        target.wait_for(lambda: target.raw_read(GCC + 4) == 0,
                        "incoming machine remains connected to system reset")
        target.monitor("query-status")
        assert any(event.get("event") == "RESET" for event in target.events)
        assert target.qom_snapshot() == zero_values()
        target.expect_matrix(zero_values())
        assert source.qom_snapshot() == expected
        check("v1_destination_writes_remain_isolated_and_restored_ports_reset")

        disabled = new("migration-default-source", reference=False)
        assert_default_profile_boundaries(disabled)
        disabled.set_clock(345_679)
        default_path = out / "noc-qos-v1-default-profile.migration"
        disabled_sent, disabled_sections = take_snapshot(
            disabled, default_path, zero_values(), 0x4e4f4330)
        disabled_target = new("migration-default-target", reference=False, incoming=True)
        disabled_ns = 17
        disabled_got = restore_snapshot(disabled_target, default_path, zero_values(),
                                        0x4e4f4330, disabled_ns)
        completed("v1_default_profile_unknown_boundary_and_ram_roundtrip",
                  default_path, disabled_sent, disabled_got, disabled_sections,
                  disabled.last_clock, disabled_ns, False)
        disabled_target.monitor("cont")
        disabled_target.step(123)
        assert_default_profile_boundaries(disabled_target)
        check("v1_default_profile_remains_unknown_after_actual_transfer")

        for name, path, source_reference, target_reference, source_state in (
                ("reference_to_default_profile_is_rejected", reference_path, True, False, sent),
                ("default_to_reference_profile_is_rejected", default_path, False, True,
                 disabled_sent)):
            rejected = new("migration-" + name, reference=target_reference, incoming=True)
            for item in PORTS:
                assert rejected.get(item, "enabled-reference") is target_reference
            before_hash = digest(path)
            # vmstate-types.c load_uint32_equal prints hexadecimal values;
            # these two actual profiles happen to have the same 0/1 spelling.
            mismatch = f"{int(target_reference):x} != {int(source_reference):x}"
            failure = failed_incoming(rejected, path, mismatch)
            assert digest(path) == before_hash
            report["rejected_migrations"].append({
                "name": name, "passed": True, "source_status": source_state["status"],
                "source_reference": source_reference, "target_reference": target_reference,
                "actual_source_stream": True, "stream_modified": False,
                "profile_mismatch": mismatch, "snapshot_sha256": before_hash,
                "snapshot_bytes": path.stat().st_size, **failure})
            check(name, stream_modified=False)

        selected = sections[port("aggre1", 7)[2]]
        original_hash = digest(reference_path)
        for kind, name in (
                ("physical-port-address", "one_physical_port_identity_corruption_is_rejected"),
                ("reserved-mainctl-bit", "one_reserved_register_bit_corruption_is_rejected")):
            modified = out / ("noc-qos-v1-corrupted-" + kind + ".migration")
            mutation = replace_one_field(reference_path, modified, selected, kind)
            assert digest(reference_path) == original_hash
            rejected = new("migration-" + name, incoming=True)
            if kind == "physical-port-address":
                # load_uint64_equal uses PRIx64, without a 0x prefix.
                diagnostic = (f"{mutation['old_field_value']:x} != "
                              f"{mutation['new_field_value']:x}")
            else:
                diagnostic = (f"{TYPE}: invalid migrated bits at port "
                              f"{selected['physical_port_address']:#x} +0x8")
            failure = failed_incoming(rejected, modified, diagnostic)
            assert digest(reference_path) == original_hash
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "stream_modified": True,
                "actual_source_stream": True, "legitimate_alternate_configuration": False,
                "source_status": sent["status"], "original_stream_unchanged": True,
                "original_snapshot_sha256": original_hash,
                "corrupted_snapshot_sha256": digest(modified),
                "original_snapshot_bytes": reference_path.stat().st_size,
                "corrupted_snapshot_bytes": modified.stat().st_size,
                **mutation, **failure})
            check(name, stream_modified=True, only_one_field_modified=True)
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
        "schema": "tb371fc-noc-qos-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [],
        "expected_completed_migrations": 2, "migrations": [],
        "expected_rejected_migrations": 2, "rejected_migrations": [],
        "expected_corrupted_stream_rejections": 2, "corrupted_stream_rejections": [],
        "vmstate_version": 1, "port_count": 47, "fabric_count": 6,
        "register_count": 188, "sparse_window_count": 188, "mapped_bytes": 752,
        "register_offsets": list(OFFSETS), "register_masks": list(MASKS),
        "logical_port_references": 54, "display_alias_references": 7,
        "reference_profile_is_default": False,
        "reference_system_reset_policy": "zero-all-four-stored-configuration-words",
        "reference_policy_is_measured_hardware_reset_behavior": False,
        "bandwidth_enforcement_observed": False, "sbm_implemented": False,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "scope": "Actual compiled-device QTest/QMP transactions and real migration "
                 "of source-bounded NoC QoS raw configuration words",
        "source_commit": SOURCE_COMMIT, "source_url": SOURCE_URL,
        "source_sha256": {"msm_bus_noc_rpmh.c": SOURCE_SHA256},
        "dtb_sha256": DTB_SHA256, "layout_evidence_blob": LAYOUT_EVIDENCE_BLOB,
        "raw_dt_evidence_blob": RAW_DT_EVIDENCE_BLOB,
        "independent_dt_decode_blob": INDEPENDENT_DT_DECODE_BLOB}
    start = time.monotonic()
    current = None
    try:
        assert len(ORDINARY_TESTS) == 11
        for test in ORDINARY_TESTS:
            current = test.__name__
            rig = None
            try:
                rig = NocRig(args, out / current)
                test(rig)
                report["checks"].append({"name": current, "passed": True})
            finally:
                if rig:
                    rig.close()
        for test in (default_profile_never_exposes_reference_zero_or_accepts_mmio,
                     exactly_188_four_byte_windows_leave_legacy_sbm_and_unlisted_ports_unmapped):
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
                and item["actual_device_sections_verified"] == 47
                and item["actual_register_words_verified"] == 188
                and item["target_device_mmio_before_first_observation"] is False
                and item["stream_modified"] is False
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and
            all(item["passed"] is True and item["destination_exit"] == 1
                and item["source_status"] == "completed" and item["stream_modified"] is False
                for item in report["rejected_migrations"]) and
            len(report["corrupted_stream_rejections"]) == 2 and
            all(item["passed"] is True and item["stream_modified"] is True
                and item["only_one_field_modified"] is True
                and item["original_stream_unchanged"] is True
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
