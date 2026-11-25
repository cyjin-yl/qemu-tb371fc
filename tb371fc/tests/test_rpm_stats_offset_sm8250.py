#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled acceptance for the bounded SM8250 RPM statistics offset reference.

Only the configured offset word is modeled. The suite checks actual QTest
reads, rejected writes/access shapes, exact mapping and 24 migration bytes.
It does not create statistics records, DDR metadata, firmware or Android
progress. Migration prewarming changes RAM only, never immutable device data.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import traceback

from test_camera_gdsc import CameraRig, core_diagnostics, unique_section, write_json
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-rpm-stats-offset", "/machine/rpm-stats-offset"
BASE, RAM, IDENTITY = 0x0c3f0004, 0x80010000, 0x52534f31
OFFSET_VALUE, VMSTATE_BYTES, FIRST_QOM_COUNT, EXPECTED_CHECKS = 0x000f0048, 24, 17, 20
REGION = TYPE + ".offset"
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
UINT_FIELDS = (
    "enabled-profile", "reference-identity", "physical-base", "window-bytes",
    "offset-value", "vmstate-bytes", "register-count", "irq-output-count",
    "clock-input-count", "clock-output-count", "timer-count",
)
BOOL_FIELDS = (
    "offset-known", "stats-payload-modeled", "ddr-offset-modeled",
    "firmware-modeled", "ready-modeled", "enabled-reference",
)
QOM_FIELDS = UINT_FIELDS + BOOL_FIELDS
SCENARIOS = ("enabled", "disabled")
CORRUPT = ("profile", "identity", "physical_base", "window_bytes",
           "enabled_offset", "disabled_offset")
REPORT_CONSTANTS = {
    "schema": "tb371fc-rpm-stats-offset-sm8250-v1",
    "suite": "rpm-stats-offset-sm8250-reference",
    "expected_checks": EXPECTED_CHECKS,
    "expected_completed_migrations": 2,
    "expected_rejected_migrations": 2,
    "expected_corrupted_stream_rejections": 6,
    "expected_unmodified_streams": 2,
    "expected_stream_files": 8,
    "vmstate_version": 1,
    "vmstate_minimum_version": 1,
    "vmstate_payload_bytes": VMSTATE_BYTES,
    "reference_identity": IDENTITY,
    "physical_base": BASE,
    "register_count": 1,
    "sparse_window_count": 1,
    "mapped_bytes": 4,
    "source_offset_value": OFFSET_VALUE,
    "first_observation_qom_get_count": FIRST_QOM_COUNT,
    "irq_output_count": 0,
    "input_clock_count": 0,
    "output_clock_count": 0,
    "timer_count": 0,
    "reference_enabled_by_default": False,
    "readonly_offset_register": True,
    "mutable_device_state": False,
    "migration_destination_device_state_dirtied": False,
    "migration_destination_ram_witness_dirtied": True,
    "stats_payload_modeled": False,
    "ddr_offset_modeled": False,
    "firmware_modeled": False,
    "ready_modeled": False,
    "guest_cpu_executed": False,
    "native_kernel_probe_observed": False,
    "android_boot_passed": False,
    "binary_unchanged": True,
}


class RpmStatsOffsetRig(CameraRig):
    """Existing real QTest/QMP transport with retained request counters."""

    def __init__(self, args, directory, *, reference=True, incoming=False, accel="qtest"):
        self.args = copy.copy(args)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.accel = bool(reference), accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.qom_get_commands = self.camera_mmio_operations = 0
        self.memory_operations = self.ram_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="rpmoff-"))
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory / "qtest.jsonl").open("w")
        self.qmplog = (self.directory / "qmp.jsonl").open("w")
        command = [args.qemu, "-machine", args.machine, "-accel", accel,
                   "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
                   "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off"]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if accel == "qtest":
            command += ["-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
                        "-qtest-log", "/dev/null"]
        if incoming:
            command += ["-incoming", "defer"]
        write_json(self.directory / "command.json", command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qmp = connect_unix(self.socket_directory / "m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self._log(self.qmplog, {"receive": greeting})
            assert "QMP" in greeting
            self.monitor("qmp_capabilities")
            if accel == "qtest":
                self.qtest = connect_unix(self.socket_directory / "t", self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile("rwb", buffering=0)
                if not incoming:
                    self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, command):
        parts = command.split()
        if parts and parts[0] in ("readb", "readw", "readl", "readq",
                                  "writeb", "writew", "writel", "writeq",
                                  "read", "write"):
            address = int(parts[1], 0)
            self.memory_operations += 1
            if BASE <= address < BASE + 4:
                self.device_mmio_operations += 1
            if RAM <= address < RAM + 4:
                self.ram_operations += 1
        return super().command(command)

    def raw_monitor(self, execute, arguments=None):
        if execute == "qom-get":
            self.qom_get_commands += 1
        return super().raw_monitor(execute, arguments)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def step(self, ns):
        old = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == old + ns
        return self.last_clock

    def world(self):
        result = {name: self.qom(name) for name in QOM_FIELDS}
        validate_world(result)
        return result


def expected_world(reference=True):
    assert type(reference) is bool
    return {
        "enabled-profile": int(reference), "reference-identity": IDENTITY,
        "physical-base": BASE, "window-bytes": 4,
        "offset-value": OFFSET_VALUE if reference else 0,
        "vmstate-bytes": VMSTATE_BYTES, "register-count": 1,
        "irq-output-count": 0, "clock-input-count": 0,
        "clock-output-count": 0, "timer-count": 0,
        "offset-known": reference, "stats-payload-modeled": False,
        "ddr-offset-modeled": False, "firmware-modeled": False,
        "ready-modeled": False, "enabled-reference": reference,
    }


def validate_world(world):
    assert set(world) == set(QOM_FIELDS)
    assert all(type(world[name]) is int for name in UINT_FIELDS)
    assert all(type(world[name]) is bool for name in BOOL_FIELDS)
    assert world["enabled-profile"] in (0, 1)
    assert world == expected_world(bool(world["enabled-profile"]))


def assert_state(r):
    actual = r.world()
    assert actual == expected_world(r.reference), actual
    return actual


def accepted_read(r, *, raw=False):
    """Exact returned data plus no device/core rejection, never transport OK alone."""
    assert r.reference is True
    before = r.world()
    old_log = r.log_path.read_text(errors="replace")
    old_core = core_diagnostics(old_log)
    command = f"read {BASE:#x} 4" if raw else f"readl {BASE:#x}"
    reply = r.command(command)
    assert len(reply) == 1, reply
    if raw:
        assert reply[0].startswith("0x"), reply
        value = bytes.fromhex(reply[0][2:])
        assert value == b"\x48\x00\x0f\x00", value.hex()
        observation = {"returned_bytes_hex": value.hex()}
    else:
        value = int(reply[0], 0)
        assert value == OFFSET_VALUE, value
        observation = {"returned_u32": value}
    after_log = r.log_path.read_text(errors="replace")
    assert core_diagnostics(after_log)[len(old_core):] == []
    assert after_log.count(TYPE + ": rejected") == old_log.count(TYPE + ": rejected")
    assert r.world() == before
    return {"command": command, "actual_read_data_checked": True,
            "specific_rejection_absent": True, "state_unchanged": True, **observation}


def rejected(r, *, write=False, width="l", delta=0, value=0):
    """One delivered callback with exact width and its specific device rejection."""
    before = r.world()
    old_log = r.log_path.read_text(errors="replace")
    old_core = core_diagnostics(old_log)
    op, size = ("write" if write else "read"), {"b": 1, "w": 2, "l": 4, "q": 8}[width]
    command = f"{op}{width} {BASE + delta:#x}" + (f" {value:#x}" if write else "")
    r.command(command)
    after_log = r.log_path.read_text(errors="replace")
    reason = "read-only-register" if write else "disabled-or-invalid-access"
    needle = f"{TYPE}: rejected {op} offset={delta:#x} size {size} ({reason})"
    assert after_log.count(needle) == old_log.count(needle) + 1, (
        command, needle, after_log[len(old_log):])
    assert after_log.count(TYPE + ": rejected") == old_log.count(TYPE + ": rejected") + 1
    added_core = core_diagnostics(after_log)[len(old_core):]
    # A callback's MEMTX_ERROR is distinct from access_valid/hole rejection:
    # memory_region_dispatch_* returns it without a generic invalid-mem line.
    assert added_core == [], added_core
    assert r.world() == before
    return {"command": command, "callback_offset": delta, "callback_size": size,
            "specific_diagnostic": needle, "device_callback_rejection_count": 1,
            "actual_core_rejections": added_core,
            "state_unchanged": True, "error_numeric_value_used_as_oracle": False}


def cold_identity_and_pure_getters(r):
    mmio, memory, now, count = (r.device_mmio_operations, r.memory_operations,
                                r.last_clock, r.qom_get_commands)
    before = assert_state(r)
    assert r.qom_get_commands - count == FIRST_QOM_COUNT
    assert r.device_mmio_operations == mmio == 0
    assert r.memory_operations == memory == 0 and r.last_clock == now == 0
    properties = r.monitor("qom-list", {"path": DEVICE})
    types = {item["name"]: item["type"] for item in properties}
    assert all(types[name] == "uint64" for name in UINT_FIELDS)
    assert all(types[name] == "bool" for name in BOOL_FIELDS)
    assert not [name for name in types if name.startswith("qtest-")]
    assert r.world() == before
    return {"actual_pure_getters": FIRST_QOM_COUNT, "offset_known_from_enabled_layout": True,
            "exact_qom_types": {name: types[name] for name in QOM_FIELDS}}


def enabled_reads_return_only_layout_offset(r):
    reads = [accepted_read(r) for _ in range(3)]
    reads.append(accepted_read(r, raw=True))
    before = assert_state(r)
    assert before["offset-value"] == OFFSET_VALUE
    assert before["offset-known"] is True
    assert all(before[name] is False for name in
               ("stats-payload-modeled", "ddr-offset-modeled", "firmware-modeled", "ready-modeled"))
    # A five-byte raw read returns the valid four-byte prefix, then reaches
    # an unmapped byte. The failed suffix is not register data or an oracle.
    old_log = r.log_path.read_text(errors="replace")
    old_core = core_diagnostics(old_log)
    command = f"read {BASE:#x} 5"
    reply = r.command(command)
    assert len(reply) == 1 and reply[0].startswith("0x"), reply
    returned = bytes.fromhex(reply[0][2:])
    assert len(returned) == 5 and returned[:4] == b"\x48\x00\x0f\x00"
    after_log = r.log_path.read_text(errors="replace")
    assert after_log.count(TYPE + ": rejected") == old_log.count(TYPE + ": rejected")
    added_core = core_diagnostics(after_log)[len(old_core):]
    assert added_core == [("read", BASE + 4, 1, "(null)", "rejected")], added_core
    assert r.world() == before
    return {"actual_reads": reads, "byte_order": "little",
            "buffered_read_command": command,
            "buffered_read_valid_prefix_hex": returned[:4].hex(),
            "buffered_read_actual_core_rejections": added_core,
            "buffered_read_failed_suffix_interpreted": False,
            "buffered_read_whole_buffer_succeeded": False,
            "whole_buffer_atomicity_claimed": False,
            "offset_does_not_supply_statistics_records": True}


def all_aligned_writes_are_readonly(r):
    patterns = (0, OFFSET_VALUE, 0xffffffff, 0x80000000, 1,
                0x55555555, 0xaaaaaaaa, 0x01020304)
    probes = [rejected(r, write=True, value=value) for value in patterns]
    accepted_read(r)
    return {"rejected_u32_patterns": list(patterns), "actual_rejections": probes,
            "mutable_device_state": False}


def narrow_unaligned_and_u64_reads_are_rejected(r):
    probes = []
    for width, deltas in (("b", range(4)), ("w", range(3)),
                          ("l", (1, 2, 3)), ("q", (0,))):
        for delta in deltas:
            probes.append(rejected(r, width=width, delta=delta))
    accepted_read(r)
    return {"actual_rejections": probes, "u64_split_into_valid_u32_reads": False,
            "rejected_read_numeric_values_interpreted": False}


def invalid_write_shapes_and_buffered_write_never_change_layout(r):
    probes = []
    for width, deltas in (("b", range(4)), ("w", range(3)),
                          ("l", (1, 2, 3)), ("q", (0,))):
        for delta in deltas:
            size = {"b": 1, "w": 2, "l": 4, "q": 8}[width]
            probes.append(rejected(r, write=True, width=width, delta=delta,
                                   value=(1 << (size * 8)) - 1))
    # A five-byte buffer is delivered as four bytes at the leaf plus one byte
    # at its unmapped neighbor. Both fail; there is no mutable device prefix.
    before = r.world()
    old_log = r.log_path.read_text(errors="replace")
    old_core = core_diagnostics(old_log)
    command = f"write {BASE:#x} 5 0x04030201ff"
    r.command(command)
    after_log = r.log_path.read_text(errors="replace")
    needle = f"{TYPE}: rejected write offset=0x0 size 4 (read-only-register)"
    assert after_log.count(needle) == old_log.count(needle) + 1
    assert after_log.count(TYPE + ": rejected") == old_log.count(TYPE + ": rejected") + 1
    added_core = core_diagnostics(after_log)[len(old_core):]
    assert added_core == [("write", BASE + 4, 1, "(null)", "rejected")], added_core
    assert r.world() == before
    accepted_read(r)
    return {"actual_scalar_rejections": probes,
            "typed_eight_byte_callback_rejected_without_split": True,
            "buffered_write_command": command,
            "buffered_write_actual_core_rejections": added_core,
            "buffered_write_changed_device_state": False,
            "whole_buffer_atomicity_claimed": False}


def reset_retains_enabled_layout_without_firmware_data(r):
    before = assert_state(r)
    accepted_read(r)
    rejected(r, write=True, value=0)
    r.monitor("system_reset")
    assert r.world() == before
    r.step(10000)
    assert r.world() == before
    accepted_read(r)
    r.monitor("system_reset")
    assert r.world() == before
    return {"reset_preserves_configured_profile_and_offset": True,
            "statistics_or_firmware_data_invented": False}


def exact_single_leaf_and_unmodeled_neighbors(r):
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory / "mtree.txt").write_text(tree)
    pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + re.escape(TYPE) + r"\.[^\s]+)(?:\s|$)"
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(pattern, tree)}
    assert found == {(BASE, BASE + 3, REGION)}, found
    before = r.world()
    holes = (0x0c3f0000, 0x0c3f0008, 0x0c3f001c,
             0x0c3f0048, 0x0c3f004c, 0x0c3f00a4)
    observations = []
    for address in holes:
        old = core_diagnostics(r.log_path.read_text(errors="replace"))
        r.raw_read(address)
        r.raw_write(address, 0xa55a5aa5)
        added = core_diagnostics(r.log_path.read_text(errors="replace"))[len(old):]
        assert added == [(op, address, 4, "(null)", "rejected")
                         for op in ("read", "write")], added
        assert r.world() == before
        observations.append({"address": address, "actual_core_rejections": added})
    return {"exact_leaves": 1, "mapped_bytes": 4, "true_holes": list(holes),
            "hole_observations": observations, "ddr_offset_added": False,
            "statistics_payload_added": False}


def runtime_observers_are_readonly_and_time_has_no_effect(r):
    before = r.world()
    mmio, memory, now = r.device_mmio_operations, r.memory_operations, r.last_clock
    for name in QOM_FIELDS:
        value = not before[name] if type(before[name]) is bool else before[name] ^ 1
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in response, (name, response)
    assert r.world() == before
    assert (r.device_mmio_operations, r.memory_operations, r.last_clock) == (mmio, memory, now)
    r.step(1_000_000)
    assert r.world() == before
    assert (r.device_mmio_operations, r.memory_operations) == (mmio, memory)
    return {"readonly_properties_checked": list(QOM_FIELDS),
            "timer_or_event_progress_invented": False}


ORDINARY = (
    cold_identity_and_pure_getters,
    enabled_reads_return_only_layout_offset,
    all_aligned_writes_are_readonly,
    narrow_unaligned_and_u64_reads_are_rejected,
    invalid_write_shapes_and_buffered_write_never_change_layout,
    reset_retains_enabled_layout_without_firmware_data,
    exact_single_leaf_and_unmodeled_neighbors,
    runtime_observers_are_readonly_and_time_has_no_effect,
)


def default_off_keeps_unknown_offset_and_rejects_all_access(args, out):
    r = RpmStatsOffsetRig(args, out / "default-off", reference=None)
    try:
        before = assert_state(r)
        command = json.loads((r.directory / "command.json").read_text())
        assert "-global" not in command
        assert before["enabled-profile"] == 0 and before["enabled-reference"] is False
        assert before["offset-value"] == 0 and before["offset-known"] is False
        probes = []
        for width in ("b", "w", "l", "q"):
            probes.append(rejected(r, width=width))
            probes.append(rejected(r, write=True, width=width, value=0))
        for value in (OFFSET_VALUE, 0xffffffff):
            probes.append(rejected(r, write=True, value=value))
        r.step(10000)
        r.monitor("system_reset")
        assert r.world() == before
        return {"default_profile_used": True, "actual_rejections": probes,
                "unknown_zero_is_not_a_valid_guest_offset": True}
    finally:
        r.close()


def paused_tcg_exposes_no_mutating_fixture_or_guest_execution(args, out):
    r = RpmStatsOffsetRig(args, out / "paused-tcg", accel="tcg")
    try:
        assert r.monitor("query-status")["running"] is False
        before = assert_state(r)
        properties = r.monitor("qom-list", {"path": DEVICE})
        assert not [p["name"] for p in properties if p["name"].startswith("qtest-")]
        for name, value in (("qtest-offset-value", 1), ("offset-value", 1), ("offset-known", False)):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
            assert "error" in response, response
        assert r.world() == before
        assert r.memory_operations == r.device_mmio_operations == r.cont_commands == 0
        assert r.monitor("query-status")["running"] is False
        return {"fixture_override_absent": True, "guest_cpu_executed": False}
    finally:
        r.close()


def offset_payload(world):
    validate_world(world)
    result = struct.pack(">IIQII", world["enabled-profile"], world["reference-identity"],
                         world["physical-base"], world["window-bytes"], world["offset-value"])
    assert len(result) == VMSTATE_BYTES
    return result


def inspect_stream(path, world):
    data, expected = path.read_bytes(), offset_payload(world)
    section = unique_section(data, TYPE, 1, VMSTATE_BYTES)
    offset = section["payload_offset"]
    actual = data[offset:offset + VMSTATE_BYTES]
    assert actual == expected, (actual.hex(), expected.hex())
    payload_path = path.with_name(path.name + ".rpm-stats-offset-v1.payload.bin")
    payload_path.write_bytes(actual)
    section.update(payload_file=payload_path.name, payload_sha256=digest(payload_path),
                   all_bytes_match_stopped_source_qom=True)
    result = {"rpm_stats_offset": section}
    write_json(path.with_name(path.name + ".sections.json"), result)
    return result


def save_source(r, path, witness):
    r.raw_write(RAM, witness)
    assert r.raw_read(RAM) == witness
    r.monitor("stop")
    assert r.monitor("query-status")["running"] is False
    snapshot = r.world()
    write_json(r.directory / "source-before-save.json", snapshot)
    r.monitor("migrate", file_channels(path))
    sent = wait_migration(r)
    assert sent["status"] == "completed"
    assert r.monitor("query-status")["status"] == "postmigrate"
    after = r.world()
    assert after == snapshot
    write_json(r.directory / "source-after-save.json", after)
    write_json(r.directory / "source-query-migrate.json", sent)
    sections = inspect_stream(path, snapshot)
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path),
            "source_directory": r.directory.name, "source_virtual_ns": r.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "sections": sections, "ram_witness": witness,
            "stream_modified": False, "device_state_mutable": False}


def prewarm_target(r, saved):
    """Only RAM differs: the device exposes immutable configured metadata."""
    r.set_clock(saved["source_virtual_ns"])
    dirty_witness = saved["ram_witness"] ^ 0xffffffff
    r.raw_write(RAM, dirty_witness)
    assert r.raw_read(RAM) == dirty_witness
    before = r.world()
    assert before == saved["snapshot"]
    assert r.device_mmio_operations == r.cont_commands == 0
    assert r.ram_operations == r.memory_operations == 2
    write_json(r.directory / "destination-before-incoming.json", {
        "snapshot": before, "device_mmio_count": r.device_mmio_operations,
        "memory_command_count": r.memory_operations,
        "ram_command_count": r.ram_operations,
        "qmp_cont_count": r.cont_commands, "qom_get_count": r.qom_get_commands,
        "source_epoch_ns": saved["source_virtual_ns"], "ram_witness_before": dirty_witness,
        "ram_witness_expected_after": saved["ram_witness"],
        "ram_witness_dirtied": True, "device_state_mutable": False,
        "device_state_dirtied": False,
    })


def receive_source(r, path, saved):
    mmio, memory, ram, cont = (r.device_mmio_operations, r.memory_operations,
                               r.ram_operations, r.cont_commands)
    r.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(r)
    assert received["status"] == "completed"
    assert r.monitor("query-status")["running"] is False
    before = r.qom_get_commands
    first = r.world()
    after = r.qom_get_commands
    assert first == saved["snapshot"]
    assert after - before == FIRST_QOM_COUNT
    assert (r.device_mmio_operations, r.memory_operations, r.ram_operations,
            r.cont_commands) == (mmio, memory, ram, cont)
    repeated = r.world()
    repeated_after = r.qom_get_commands
    assert repeated == first
    assert repeated_after - after == FIRST_QOM_COUNT
    assert (r.device_mmio_operations, r.memory_operations, r.ram_operations,
            r.cont_commands) == (mmio, memory, ram, cont)
    evidence = {
        "snapshot": first, "query_migrate": received,
        "device_mmio_count_before_incoming": mmio,
        "device_mmio_count_after_first_observation": r.device_mmio_operations,
        "memory_command_count_before_incoming": memory,
        "memory_command_count_after_first_observation": r.memory_operations,
        "qmp_cont_count_before_incoming": cont,
        "qmp_cont_count_after_first_observation": r.cont_commands,
        "qom_get_count_before_first_observation": before,
        "qom_get_count_after_first_observation": after,
        "first_observation_qom_get_count": after - before,
        "repeated_snapshot": repeated,
        "qom_get_count_after_repeated_observation": repeated_after,
        "repeated_observation_qom_get_count": repeated_after - after,
        "device_mmio_since_incoming": False,
        "any_memory_command_since_incoming_before_observations": False,
        "qmp_cont_since_incoming": False,
        "guest_cpu_executed": False, "shared_log_timestamp_claimed": False,
        "device_state_mutable": False, "device_state_dirtied": False,
    }
    # First and repeated complete QOM snapshots precede this real RAM read.
    actual_witness = r.raw_read(RAM)
    assert actual_witness == saved["ram_witness"]
    assert r.ram_operations == ram + 1 and r.memory_operations == memory + 1
    assert r.device_mmio_operations == mmio == 0 and r.cont_commands == cont == 0
    evidence.update(ram_witness=actual_witness, ram_witness_before=saved["ram_witness"] ^ 0xffffffff,
                    ram_witness_loaded=True, ram_witness_checked_before_cont=True)
    write_json(r.directory / "destination-before-first-device-mmio.json", evidence)
    return evidence


def load_failure(r, path, diagnostic):
    error = None
    try:
        r.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as exc:
        error = str(exc)
    code = r.process.wait(timeout=20)
    stderr = r.log_path.read_text(errors="replace")
    assert code == 1, (code, stderr)
    assert TYPE + ": " + diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    assert r.memory_operations == r.device_mmio_operations == r.cont_commands == 0
    return {"destination_exit": code, "expected_diagnostic": TYPE + ": " + diagnostic,
            "specific_load_failure_observed": True, "command_error": error,
            "target_directory": r.directory.name, "device_mmio_commands": 0,
            "memory_commands": 0, "cont_commands": 0}


def migration_checks(args, out, report):
    rigs, saved_by_name, path_by_name = [], {}, {}

    def new(name, **options):
        r = RpmStatsOffsetRig(args, out / name, **options)
        rigs.append(r)
        return r

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    try:
        for index, scenario in enumerate(SCENARIOS):
            reference = scenario == "enabled"
            source = new("migration-" + scenario + "-source", reference=reference)
            expected = expected_world(reference)
            assert source.world() == expected
            source.set_clock((index + 1) * 100_000)
            path = out / ("rpm-stats-offset-" + scenario + ".migration")
            saved = save_source(source, path, 0x52534f00 + index)
            saved_by_name[scenario], path_by_name[scenario] = saved, path
            report["migration_sources"].append(saved)
            source.close()
            target = new("migration-" + scenario + "-target", reference=reference, incoming=True)
            prewarm_target(target, saved)
            first = receive_source(target, path, saved)
            target.monitor("cont")
            target.step(10000)
            assert target.world() == expected
            if reference:
                accepted_read(target)
            else:
                rejected(target)
            rejected(target, write=True, value=OFFSET_VALUE ^ 0xffffffff)
            target.monitor("system_reset")
            target.step(10000)
            assert target.world() == expected
            if reference:
                accepted_read(target)
            else:
                rejected(target)
            name = "migration_" + scenario
            report["migrations"].append({
                "name": name, "passed": True, "source_status": "completed", "target_status": "completed",
                "snapshot_file": path.name, "snapshot_sha256": digest(path), "stream_modified": False,
                "source_directory": source.directory.name, "target_directory": target.directory.name,
                "first_target_observation": first, "continued_after_restore": True,
                "all_24_payload_bytes_verified": True,
                "post_restore_layout_and_read_semantics_checked": True,
                "post_restore_reset_retains_profile": True,
                "destination_ram_witness_dirtied": True,
                "destination_device_state_dirtied": False})
            passed(name, first_observation_mmio_delta=0, first_observation_cont_delta=0,
                   ram_witness_loaded_before_cont=True,
                   device_state_dirtied=False, post_restore_reset_retains_profile=True)
            target.close()

        for name, scenario, reference in (("on_to_off", "enabled", False),
                                          ("off_to_on", "disabled", True)):
            saved, path = saved_by_name[scenario], path_by_name[scenario]
            target = new("reject-profile-" + name, reference=reference, incoming=True)
            target.set_clock(saved["source_virtual_ns"])
            details = load_failure(target, path, "migrated profile mismatch")
            report["rejected_migrations"].append({"name": name, "passed": True,
                "snapshot_file": path.name, "snapshot_sha256": digest(path),
                "stream_modified": False, **details})
            passed("profile_" + name + "_rejected", **details)
            target.close()

        # Literal v1 offsets are independent of the C implementation and generator.
        mutations = (
            ("profile", "enabled", 0, 4, 1, 2, True, "migrated profile mismatch"),
            ("identity", "enabled", 4, 4, IDENTITY, 1, True, "migrated identity mismatch"),
            ("physical_base", "enabled", 8, 8, BASE, 4, True, "migrated geometry mismatch"),
            ("window_bytes", "enabled", 16, 4, 4, 1, True, "migrated geometry mismatch"),
            ("enabled_offset", "enabled", 20, 4, OFFSET_VALUE, 1, True, "migrated offset mismatch"),
            ("disabled_offset", "disabled", 20, 4, 0, 1, False, "migrated offset mismatch"),
        )
        for name, scenario, relative, width, old, xor, reference, diagnostic in mutations:
            source_path, saved = path_by_name[scenario], saved_by_name[scenario]
            original = source_path.read_bytes()
            absolute = saved["sections"]["rpm_stats_offset"]["payload_offset"] + relative
            assert int.from_bytes(original[absolute:absolute + width], "big") == old
            changed = bytearray(original)
            changed[absolute:absolute + width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, pair in enumerate(zip(original, changed)) if pair[0] != pair[1]]
            expected_byte = absolute + width - 1 - (xor.bit_length() - 1) // 8
            assert differences == [expected_byte]
            assert (original[expected_byte] ^ changed[expected_byte]).bit_count() == 1
            path = out / f"rpm-stats-offset-corrupt-{name}.migration"
            path.write_bytes(changed)
            target = new("reject-corrupt-" + name, reference=reference, incoming=True)
            target.set_clock(saved["source_virtual_ns"])
            details = load_failure(target, path, diagnostic)
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "source_file": source_path.name,
                "source_sha256": saved["sha256"], "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": True,
                "field_offset": relative, "field_bytes": width, "old_value": old,
                "xor": xor, "changed_byte_offsets": differences, "changed_bits": 1,
                "destination_reference_enabled": reference,
                "original_stream_unchanged": digest(source_path) == saved["sha256"], **details})
            passed("single_bit_" + name + "_rejected", **details)
            target.close()
        assert all(digest(out / item["file"]) == item["sha256"]
                   for item in report["migration_sources"])
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--machine", default=DEFAULT_MACHINE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started, original_hash = time.monotonic(), digest(Path(args.qemu))
    report = {
        **REPORT_CONSTANTS, "qemu": args.qemu, "machine": args.machine,
        "work_dir": str(out), "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)),
        "source_revision": "98719830b0de5584f5588bbe9f4c8304d97a64c6",
        "geometry_source_revision": "adc218676eef25575469234709c2d87185ca223a",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "scope": "One configured read-only RPM statistics offset word. No statistics payload, "
                 "DDR offset, firmware, READY, clock or timer behavior. No mutable device state "
                 "is claimed: incoming prewarming changes RAM only. No guest CPU, native probe "
                 "or Android boot claim.",
        "binary_unchanged": False, "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "migration_sources": [], "actual_stream_files": [],
        "passed": False,
    }
    try:
        assert len(ORDINARY) == len(set(ORDINARY)) == 8
        assert len(QOM_FIELDS) == FIRST_QOM_COUNT == 17
        for index, test in enumerate(ORDINARY):
            rig = None
            fixture = out / f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = RpmStatsOffsetRig(args, fixture)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_off_keeps_unknown_offset_and_rejects_all_access,
                     paused_tcg_exposes_no_mutating_fixture_or_guest_execution):
            details = test(args, out) or {}
            report["checks"].append({"name": test.__name__, "passed": True, **details})
        migration_checks(args, out, report)
        report["actual_stream_files"] = [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in sorted(out.glob("*.migration"))]
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
        expected_files = {"rpm-stats-offset-" + s + ".migration" for s in SCENARIOS}
        expected_files |= {"rpm-stats-offset-corrupt-" + s + ".migration" for s in CORRUPT}
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 2 and
            all(item["source_status"] == item["target_status"] == "completed" and
                item["continued_after_restore"] and item["all_24_payload_bytes_verified"] and
                item["post_restore_layout_and_read_semantics_checked"] and
                item["post_restore_reset_retains_profile"] and
                item["destination_ram_witness_dirtied"] is True and
                item["destination_device_state_dirtied"] is False and
                item["first_target_observation"]["first_observation_qom_get_count"] == FIRST_QOM_COUNT and
                item["first_target_observation"]["repeated_observation_qom_get_count"] == FIRST_QOM_COUNT and
                item["first_target_observation"]["device_mmio_since_incoming"] is False and
                item["first_target_observation"]["any_memory_command_since_incoming_before_observations"] is False and
                item["first_target_observation"]["qmp_cont_since_incoming"] is False and
                item["first_target_observation"]["ram_witness_loaded"] is True and
                item["first_target_observation"]["ram_witness_checked_before_cont"] is True
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and len(report["corrupted_stream_rejections"]) == 6 and
            all(item["passed"] and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"] + report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 2 and
            len(report["actual_stream_files"]) == len(expected_files) == 8 and
            {item["name"] for item in report["actual_stream_files"]} == expected_files and
            all(item["original_stream_unchanged"] and item["changed_bits"] == 1
                for item in report["corrupted_stream_rejections"]) and
            report["binary_unchanged"])
    except BaseException as exc:
        report["error"], report["traceback"] = str(exc), traceback.format_exc()
    finally:
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
        report["passed"] = report["passed"] and report["binary_unchanged"]
        report["duration_seconds"] = time.monotonic() - started
        write_json(out / "result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
