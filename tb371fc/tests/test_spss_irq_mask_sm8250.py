#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled acceptance for the one-word SM8250 SPSS interrupt-mask reference.

The original PIL probe writes 0xfcffffff to PA 0x01881028.  This suite verifies
write-control provenance, rejection boundaries and full migration bytes.  It
cannot establish IRQ delivery, secure firmware, PAS service or Android boot.
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

TYPE, DEVICE = "qcom-sm8250-spss-irq-mask", "/machine/spss-irq-mask"
BASE, RAM, IDENTITY = 0x01881028, 0x80010000, 0x53504d31
DRIVER_MASK, VMSTATE_BYTES, FIRST_QOM_COUNT, EXPECTED_CHECKS = 0xfcffffff, 25, 18, 21
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
UINT_FIELDS = (
    "enabled-profile", "reference-identity", "physical-base", "window-bytes",
    "mask-value", "vmstate-bytes", "register-count", "irq-output-count",
    "clock-input-count", "clock-output-count", "timer-count",
)
BOOL_FIELDS = (
    "programmed", "mask-known", "readback-supported", "ready-modeled",
    "firmware-modeled", "pas-modeled", "enabled-reference",
)
QOM_FIELDS = UINT_FIELDS + BOOL_FIELDS
SCENARIOS = ("unknown", "driver", "known-zero", "disabled")
CORRUPT = ("identity", "physical_base", "window_bytes", "programmed_flag",
           "unknown_mask", "disabled_programmed")

REPORT_CONSTANTS = {'schema': 'tb371fc-spss-irq-mask-sm8250-v1',
 'suite': 'spss-irq-mask-sm8250-reference',
 'expected_checks': 21,
 'expected_completed_migrations': 3,
 'expected_rejected_migrations': 2,
 'expected_corrupted_stream_rejections': 6,
 'expected_unmodified_streams': 4,
 'expected_stream_files': 10,
 'vmstate_version': 1,
 'vmstate_minimum_version': 1,
 'vmstate_payload_bytes': 25,
 'reference_identity': 1397771569,
 'physical_base': 25694248,
 'register_count': 1,
 'sparse_window_count': 1,
 'mapped_bytes': 4,
 'first_observation_qom_get_count': 18,
 'irq_output_count': 0,
 'input_clock_count': 0,
 'output_clock_count': 0,
 'timer_count': 0,
 'reference_enabled_by_default': False,
 'readback_supported': False,
 'ready_modeled': False,
 'firmware_modeled': False,
 'pas_modeled': False,
 'hardware_reset_value_known': False,
 'programmed_zero_distinct_from_unknown': True,
 'source_probe_mask': 4244635647,
 'guest_cpu_executed': False,
 'native_kernel_probe_observed': False,
 'android_boot_passed': False,
 'binary_unchanged': True}


class SpssMaskRig(CameraRig):
    """Existing real QTest/QMP transport, with retained local operation counts."""

    def __init__(self, args, directory, *, reference=True, incoming=False, accel="qtest"):
        self.args = copy.copy(args)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.accel = bool(reference), accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.qom_get_commands = self.camera_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="spmask-"))
        self.log_path = self.directory/"qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory/"qtest.jsonl").open("w")
        self.qmplog = (self.directory/"qmp.jsonl").open("w")
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
        write_json(self.directory/"command.json", command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qmp = connect_unix(self.socket_directory/"m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self._log(self.qmplog, {"receive": greeting})
            assert "QMP" in greeting
            self.monitor("qmp_capabilities")
            if accel == "qtest":
                self.qtest = connect_unix(self.socket_directory/"t", self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile("rwb", buffering=0)
                if not incoming:
                    self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, command):
        parts = command.split()
        if parts and parts[0] in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq", "read", "write"):
            address = int(parts[1], 0)
            if BASE <= address < BASE+4:
                self.device_mmio_operations += 1
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
        assert self.last_clock == old+ns
        return self.last_clock

    def write(self, value):
        self.raw_write(BASE, value)

    def world(self):
        result = {name: self.qom(name) for name in QOM_FIELDS}
        validate_world(result)
        return result


def expected_world(reference=True, mask=0, programmed=False):
    assert type(reference) is bool and type(programmed) is bool
    assert type(mask) is int and 0 <= mask <= 0xffffffff
    assert reference or (mask == 0 and not programmed)
    assert programmed or mask == 0
    return {
        "enabled-profile": int(reference), "reference-identity": IDENTITY,
        "physical-base": BASE, "window-bytes": 4, "mask-value": mask,
        "vmstate-bytes": VMSTATE_BYTES, "register-count": 1,
        "irq-output-count": 0, "clock-input-count": 0,
        "clock-output-count": 0, "timer-count": 0,
        "programmed": programmed, "mask-known": programmed,
        "readback-supported": False, "ready-modeled": False,
        "firmware-modeled": False, "pas-modeled": False,
        "enabled-reference": reference,
    }


def validate_world(world):
    assert set(world) == set(QOM_FIELDS)
    assert all(type(world[name]) is int for name in UINT_FIELDS)
    assert all(type(world[name]) is bool for name in BOOL_FIELDS)
    assert world["enabled-profile"] in (0, 1)
    assert world == expected_world(bool(world["enabled-profile"]),
                                   world["mask-value"], world["programmed"])


def assert_state(r, value=0, programmed=False):
    actual = r.world()
    assert actual == expected_world(r.reference, value, programmed), actual
    return actual


def rejected(r, *, write=False, width="l", delta=0, value=0):
    """One transaction expected to reach the word callback unchanged in width."""
    before = r.world()
    old = r.log_path.read_text(errors="replace")
    op, size = ("write" if write else "read"), {"b": 1, "w": 2, "l": 4, "q": 8}[width]
    command = f"{op}{width} {BASE+delta:#x}" + (f" {value:#x}" if write else "")
    r.command(command)
    after = r.log_path.read_text(errors="replace")
    reason = "disabled-or-invalid-access" if write else "readback-unimplemented"
    needle = f"{TYPE}: rejected {op} offset={delta:#x} size {size} ({reason})"
    assert after.count(needle) == old.count(needle)+1, (command, needle, after[len(old):])
    assert r.world() == before
    return {"command": command, "callback_offset": delta, "callback_size": size,
            "specific_diagnostic": needle, "state_unchanged": True}


def cold_identity_unknown_and_pure_getters(r):
    mmio, now, count = r.device_mmio_operations, r.last_clock, r.qom_get_commands
    before = assert_state(r)
    assert r.qom_get_commands-count == FIRST_QOM_COUNT
    assert r.device_mmio_operations == mmio == 0 and r.last_clock == now == 0
    properties = r.monitor("qom-list", {"path": DEVICE})
    types = {item["name"]: item["type"] for item in properties}
    assert all(types[name] == "uint64" for name in UINT_FIELDS)
    assert all(types[name] == "bool" for name in BOOL_FIELDS)
    assert not [name for name in types if name.startswith("qtest-")]
    assert r.world() == before
    return {"actual_pure_getters": FIRST_QOM_COUNT, "mask_zero_is_unknown": True,
            "exact_qom_types": {name: types[name] for name in QOM_FIELDS}}


def literal_original_probe_mask(r):
    expected = 0xffffffff & ~(1 << 24) & ~(1 << 25)
    assert expected == DRIVER_MASK
    r.write(expected)
    world = assert_state(r, expected, True)
    assert all(world[name] is False for name in
               ("readback-supported", "ready-modeled", "firmware-modeled", "pas-modeled"))
    assert all(world[name] == 0 for name in
               ("irq-output-count", "clock-input-count", "clock-output-count", "timer-count"))
    return {"physical_address": BASE, "actual_guest_word": expected,
            "original_bit_positions": [24, 25], "modeled_remote_events": 0}


def full_u32_writes_replace_entire_mask(r):
    patterns = (0xffffffff, 0, 0x80000000, 1, 0x03000000, DRIVER_MASK,
                0x55555555, 0xaaaaaaaa, 0x01020304, 0x04030201)
    for value in patterns:
        r.write(value)
        assert_state(r, value, True)
    return {"full_u32_patterns": list(patterns), "replacement_not_or_accumulation": True}


def zero_programming_provenance_and_reset(r):
    unknown = assert_state(r)
    r.write(0)
    known_zero = assert_state(r, 0, True)
    assert known_zero != unknown and known_zero["mask-value"] == unknown["mask-value"]
    r.write(0)
    assert r.world() == known_zero
    r.write(DRIVER_MASK)
    assert_state(r, DRIVER_MASK, True)
    r.monitor("system_reset")
    assert r.world() == unknown
    r.step(10000)
    assert r.world() == unknown
    r.write(0)
    assert r.world() == known_zero
    r.monitor("system_reset")
    assert r.world() == unknown
    return {"programmed_zero_distinct": True, "reset_clears_value_and_provenance": True,
            "hardware_reset_value_claimed": False}


def every_guest_read_is_rejected(r):
    r.write(DRIVER_MASK)
    probes = []
    for width, deltas in (("b", range(4)), ("w", range(3)), ("l", (0,))):
        for delta in deltas:
            probes.append(rejected(r, width=width, delta=delta))
    assert_state(r, DRIVER_MASK, True)
    return {"rejected_read_probes": probes, "read_error_numeric_value_used_as_oracle": False}


def invalid_width_and_alignment_never_program(r):
    r.write(DRIVER_MASK)
    probes = []
    for width, deltas in (("b", (0, 3)), ("w", (0, 1, 2)), ("l", (1,))):
        for delta in deltas:
            probes.append(rejected(r, write=True, width=width, delta=delta,
                                   value=(1 << ({"b": 1, "w": 2, "l": 4}[width]*8))-1))

    # With this device's max_access_size=8 and unaligned=true, QTest's
    # eight-byte buffer reaches one size-8 callback, even at this 4-byte leaf.
    # Its rejection is not a claim that arbitrary buffers are atomic.
    old_core = core_diagnostics(r.log_path.read_text(errors="replace"))
    probes.append(rejected(r, write=True, width="q", value=0xffffffffffffffff))
    probes.append(rejected(r, width="q"))
    added_core = core_diagnostics(r.log_path.read_text(errors="replace"))[len(old_core):]
    assert not [item for item in added_core if item[3] == "(null)"], added_core
    assert_state(r, DRIVER_MASK, True)

    # A five-byte raw buffer is different: pow2floor dispatches a legal
    # four-byte write first, then the fifth byte hits the adjacent hole.
    # address_space_write retains the error and does not roll back the prefix;
    # QTest's OK response itself does not attest a successful MMIO transaction.
    before = r.world()
    old_log = r.log_path.read_text(errors="replace")
    old_core = core_diagnostics(old_log)
    command = f"write {BASE:#x} 5 0x04030201ff"
    r.command(command)
    after_log = r.log_path.read_text(errors="replace")
    added_core = core_diagnostics(after_log)[len(old_core):]
    assert added_core == [("write", BASE+4, 1, "(null)", "rejected")], added_core
    assert after_log.count(TYPE+": rejected") == old_log.count(TYPE+": rejected")
    after = r.world()
    assert after == {**before, "mask-value": 0x01020304}
    assert after["programmed"] is True and after["mask-known"] is True
    return {"rejected_scalar_probes": probes,
            "typed_eight_byte_callback_rejected_without_hole": True,
            "buffered_write_command": command,
            "buffered_write_prefix_value": 0x01020304,
            "buffered_write_hole_address": BASE+4,
            "buffered_write_hole_size": 1,
            "buffered_write_valid_prefix_committed": True,
            "whole_buffer_atomicity_claimed": False}


def exact_single_leaf_and_surrounding_holes(r):
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory/"mtree.txt").write_text(tree)
    pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ("+re.escape(TYPE)+r"\.[^\s]+)(?:\s|$)"
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(pattern, tree)}
    assert found == {(BASE, BASE+3, TYPE+".mask")}, found
    r.write(DRIVER_MASK)
    before = r.world()
    # Neighboring source resources stay unmodeled; no readiness is inferred.
    holes = (0x0188101c, 0x01881024, 0x0188102c, 0x0188103c,
             0x0188200c, 0x01882010, 0x01882014)
    for address in holes:
        old = core_diagnostics(r.log_path.read_text(errors="replace"))
        r.raw_read(address)
        r.raw_write(address, 0xa55a5aa5)
        added = core_diagnostics(r.log_path.read_text(errors="replace"))[len(old):]
        assert added == [(op, address, 4, "(null)", "rejected") for op in ("read", "write")], added
        assert r.world() == before
    return {"exact_leaves": 1, "mapped_bytes": 4, "true_holes": list(holes),
            "neighboring_source_resources_not_added": True}


def runtime_observers_are_readonly_and_time_has_no_effect(r):
    r.write(0x89abcdef)
    before = r.world()
    mmio, now = r.device_mmio_operations, r.last_clock
    for name in QOM_FIELDS:
        value = not before[name] if type(before[name]) is bool else before[name] ^ 1
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in response, (name, response)
    assert r.world() == before and r.device_mmio_operations == mmio and r.last_clock == now
    r.step(1_000_000)
    assert r.world() == before
    assert r.device_mmio_operations == mmio
    return {"readonly_properties_checked": list(QOM_FIELDS), "timer_or_event_progress_invented": False}


ORDINARY = (
    cold_identity_unknown_and_pure_getters,
    literal_original_probe_mask,
    full_u32_writes_replace_entire_mask,
    zero_programming_provenance_and_reset,
    every_guest_read_is_rejected,
    invalid_width_and_alignment_never_program,
    exact_single_leaf_and_surrounding_holes,
    runtime_observers_are_readonly_and_time_has_no_effect,
)


def default_off_rejects_control_without_programming(args, out):
    r = SpssMaskRig(args, out/"default-off", reference=None)
    try:
        before = assert_state(r)
        command = json.loads((r.directory/"command.json").read_text())
        assert "-global" not in command
        assert before["enabled-profile"] == 0 and before["enabled-reference"] is False
        rejected(r)
        for value in (0, DRIVER_MASK, 0xffffffff):
            rejected(r, write=True, value=value)
        r.step(10000)
        r.monitor("system_reset")
        assert r.world() == before
        return {"default_profile_used": True, "zero_and_nonzero_writes_rejected": True,
                "programmed": False, "mask_known": False}
    finally:
        r.close()


def paused_tcg_exposes_no_mutating_fixture_or_guest_execution(args, out):
    r = SpssMaskRig(args, out/"paused-tcg", accel="tcg")
    try:
        assert r.monitor("query-status")["running"] is False
        before = assert_state(r)
        properties = r.monitor("qom-list", {"path": DEVICE})
        assert not [p["name"] for p in properties if p["name"].startswith("qtest-")]
        for name, value in (("qtest-mask-value", 1), ("mask-value", 1), ("programmed", True)):
            response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
            assert "error" in response, response
        assert r.world() == before
        assert r.device_mmio_operations == r.cont_commands == 0
        assert r.monitor("query-status")["running"] is False
        return {"fixture_override_absent": True, "guest_cpu_executed": False}
    finally:
        r.close()


def mask_payload(world):
    validate_world(world)
    result = struct.pack(">IIQIIB", world["enabled-profile"], world["reference-identity"],
                         world["physical-base"], world["window-bytes"],
                         world["mask-value"], int(world["programmed"]))
    assert len(result) == VMSTATE_BYTES
    return result


def inspect_stream(path, world):
    data, expected = path.read_bytes(), mask_payload(world)
    section = unique_section(data, TYPE, 1, VMSTATE_BYTES)
    offset = section["payload_offset"]
    actual = data[offset:offset+VMSTATE_BYTES]
    assert actual == expected, (actual.hex(), expected.hex())
    payload_path = path.with_name(path.name+".spss-mask-v1.payload.bin")
    payload_path.write_bytes(actual)
    section.update(payload_file=payload_path.name, payload_sha256=digest(payload_path),
                   all_bytes_match_stopped_source_qom=True)
    result = {"spss_irq_mask": section}
    write_json(path.with_name(path.name+".sections.json"), result)
    return result


def save_source(r, path, witness):
    r.raw_write(RAM, witness)
    r.monitor("stop")
    assert r.monitor("query-status")["running"] is False
    snapshot = r.world()
    write_json(r.directory/"source-before-save.json", snapshot)
    r.monitor("migrate", file_channels(path))
    sent = wait_migration(r)
    assert sent["status"] == "completed"
    assert r.monitor("query-status")["status"] == "postmigrate"
    after = r.world()
    assert after == snapshot
    write_json(r.directory/"source-after-save.json", after)
    write_json(r.directory/"source-query-migrate.json", sent)
    sections = inspect_stream(path, snapshot)
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path),
            "source_directory": r.directory.name, "source_virtual_ns": r.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "sections": sections, "ram_witness": witness,
            "stream_modified": False}


def prewarm_target(r, saved):
    r.set_clock(saved["source_virtual_ns"])
    r.write(saved["snapshot"]["mask-value"] ^ 0xffffffff)
    r.raw_write(RAM, saved["ram_witness"] ^ 0xffffffff)
    stale = r.world()
    assert stale != saved["snapshot"]
    assert stale["programmed"] is True
    assert r.device_mmio_operations == 1 and r.cont_commands == 0
    write_json(r.directory/"destination-before-incoming.json", {
        "snapshot": stale, "device_mmio_count": r.device_mmio_operations,
        "qmp_cont_count": r.cont_commands, "qom_get_count": r.qom_get_commands,
        "source_epoch_ns": saved["source_virtual_ns"], "actual_dirty_mask": stale["mask-value"]})


def receive_source(r, path, saved):
    r.set_clock(saved["source_virtual_ns"])
    mmio, cont = r.device_mmio_operations, r.cont_commands
    r.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(r)
    assert received["status"] == "completed"
    assert r.monitor("query-status")["running"] is False
    before = r.qom_get_commands
    first = r.world()
    after = r.qom_get_commands
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert (r.device_mmio_operations, r.cont_commands) == (mmio, cont)
    assert after-before == FIRST_QOM_COUNT
    evidence = {
        "snapshot": first, "query_migrate": received,
        "device_mmio_count_before_incoming": mmio,
        "device_mmio_count_after_first_observation": r.device_mmio_operations,
        "qmp_cont_count_before_incoming": cont,
        "qmp_cont_count_after_first_observation": r.cont_commands,
        "qom_get_count_before_first_observation": before,
        "qom_get_count_after_first_observation": after,
        "first_observation_qom_get_count": after-before,
        "device_mmio_since_incoming": r.device_mmio_operations != mmio,
        "qmp_cont_since_incoming": r.cont_commands != cont,
        "guest_cpu_executed": False, "shared_log_timestamp_claimed": False,
    }
    # RAM verification is deliberately after the first complete pure-QOM world.
    assert r.raw_read(RAM) == saved["ram_witness"]
    evidence["ram_witness"] = saved["ram_witness"]
    write_json(r.directory/"destination-before-first-device-mmio.json", evidence)
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
    assert TYPE+": "+diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    assert r.device_mmio_operations == r.cont_commands == 0
    return {"destination_exit": code, "expected_diagnostic": TYPE+": "+diagnostic,
            "specific_load_failure_observed": True, "command_error": error,
            "target_directory": r.directory.name, "device_mmio_commands": 0, "cont_commands": 0}


def migration_checks(args, out, report):
    rigs, saved_by_name, path_by_name = [], {}, {}

    def new(name, **options):
        r = SpssMaskRig(args, out/name, **options)
        rigs.append(r)
        return r

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    try:
        for index, scenario in enumerate(SCENARIOS):
            reference = scenario != "disabled"
            source = new("migration-"+scenario+"-source", reference=reference)
            if scenario == "driver":
                source.write(DRIVER_MASK)
            elif scenario == "known-zero":
                source.write(0)
            expected = expected_world(reference, DRIVER_MASK if scenario == "driver" else 0,
                                      scenario in ("driver", "known-zero"))
            assert source.world() == expected
            source.set_clock((index+1)*100_000)
            path = out/("spss-mask-"+scenario+".migration")
            saved = save_source(source, path, 0x53504d00+index)
            saved_by_name[scenario], path_by_name[scenario] = saved, path
            report["migration_sources"].append(saved)
            source.close()
            if not reference:
                continue
            target = new("migration-"+scenario+"-target", incoming=True)
            prewarm_target(target, saved)
            first = receive_source(target, path, saved)
            target.monitor("cont")
            target.step(10000)
            assert target.world() == expected
            rejected(target)
            replacement = expected["mask-value"] ^ 0xffffffff
            target.write(replacement)
            assert_state(target, replacement, True)
            target.monitor("system_reset")
            assert_state(target)
            target.write(0)
            assert_state(target, 0, True)
            name = "migration_"+scenario.replace("-", "_")
            report["migrations"].append({
                "name": name, "passed": True, "source_status": "completed", "target_status": "completed",
                "snapshot_file": path.name, "snapshot_sha256": digest(path), "stream_modified": False,
                "source_directory": source.directory.name, "target_directory": target.directory.name,
                "first_target_observation": first, "continued_after_restore": True,
                "all_25_payload_bytes_verified": True, "post_restore_replacement": replacement,
                "post_restore_reset_and_known_zero": True})
            passed(name, first_observation_mmio_delta=0, first_observation_cont_delta=0,
                   post_restore_reset_and_known_zero=True)
            target.close()

        for name, scenario, reference in (("on_to_off", "driver", False),
                                           ("off_to_on", "disabled", True)):
            saved, path = saved_by_name[scenario], path_by_name[scenario]
            target = new("reject-profile-"+name, reference=reference, incoming=True)
            target.set_clock(saved["source_virtual_ns"])
            details = load_failure(target, path, "migrated profile mismatch")
            report["rejected_migrations"].append({"name": name, "passed": True,
                "snapshot_file": path.name, "snapshot_sha256": digest(path),
                "stream_modified": False, **details})
            passed("profile_"+name+"_rejected", **details)
            target.close()

        # Literal v1 wire positions are independent of the C and generated data.
        mutations = (
            ("identity", "driver", 4, 4, IDENTITY, 1, True, "migrated identity mismatch"),
            ("physical_base", "driver", 8, 8, BASE, 4, True, "migrated geometry mismatch"),
            ("window_bytes", "driver", 16, 4, 4, 1, True, "migrated geometry mismatch"),
            ("programmed_flag", "unknown", 24, 1, 0, 2, True, "invalid migrated programmed flag"),
            ("unknown_mask", "unknown", 20, 4, 0, 1, True, "invalid migrated unknown mask"),
            ("disabled_programmed", "disabled", 24, 1, 0, 1, False, "invalid migrated disabled state"),
        )
        for name, scenario, relative, width, old, xor, reference, diagnostic in mutations:
            source_path, saved = path_by_name[scenario], saved_by_name[scenario]
            original = source_path.read_bytes()
            absolute = saved["sections"]["spss_irq_mask"]["payload_offset"]+relative
            assert int.from_bytes(original[absolute:absolute+width], "big") == old
            changed = bytearray(original)
            changed[absolute:absolute+width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, pair in enumerate(zip(original, changed)) if pair[0] != pair[1]]
            expected_byte = absolute+width-1-(xor.bit_length()-1)//8
            assert differences == [expected_byte]
            assert (original[expected_byte] ^ changed[expected_byte]).bit_count() == 1
            path = out/f"spss-mask-corrupt-{name}.migration"
            path.write_bytes(changed)
            target = new("reject-corrupt-"+name, reference=reference, incoming=True)
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
            passed("single_bit_"+name+"_rejected", **details)
            target.close()
        assert all(digest(out / item["file"]) == item["sha256"] for item in report["migration_sources"])
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
        "source_commit": "98719830b0de5584f5588bbe9f4c8304d97a64c6",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "scope": "One write-only mask-control word. Unknown reset placeholder is distinct from "
                 "programmed zero. No IRQ, READY, firmware, PAS, clock or timer behavior; "
                 "no guest CPU, silicon reset measurement or Android success claim.",
        "binary_unchanged": False, "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "migration_sources": [], "actual_stream_files": [],
        "passed": False,
    }
    try:
        assert len(ORDINARY) == len(set(ORDINARY)) == 8
        for index, test in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = SpssMaskRig(args, fixture)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_off_rejects_control_without_programming,
                     paused_tcg_exposes_no_mutating_fixture_or_guest_execution):
            details = test(args, out) or {}
            report["checks"].append({"name": test.__name__, "passed": True, **details})
        migration_checks(args, out, report)
        report["actual_stream_files"] = [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in sorted(out.glob("*.migration"))]
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
        expected_files = {"spss-mask-"+s+".migration" for s in SCENARIOS}
        expected_files |= {"spss-mask-corrupt-"+s+".migration" for s in CORRUPT}
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 3 and
            all(item["source_status"] == item["target_status"] == "completed" and
                item["continued_after_restore"] and item["all_25_payload_bytes_verified"] and
                item["post_restore_reset_and_known_zero"] and
                item["first_target_observation"]["first_observation_qom_get_count"] == FIRST_QOM_COUNT and
                item["first_target_observation"]["device_mmio_since_incoming"] is False and
                item["first_target_observation"]["qmp_cont_since_incoming"] is False
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and len(report["corrupted_stream_rejections"]) == 6 and
            all(item["passed"] and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"]+report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 4 and
            len(report["actual_stream_files"]) == len(expected_files) == 10 and
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
        report["duration_seconds"] = time.monotonic()-started
        write_json(out/"result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
