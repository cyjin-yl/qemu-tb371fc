#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Ordinary MMIO/reset acceptance for the immutable RPM initialization leaf.

Only synthetic QTest accesses and paused-TCG identity are exercised. There is
no kernel/AOP execution, migration command, stream creation or stream mutation.
The declared VMState identity guards are inspected separately in source; this
suite does not claim a runtime migration or profile-mismatch result.
"""
import argparse
import copy
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

from test_camera_gdsc import CameraRig, core_diagnostics, write_json
from test_geni_uart import connect_unix

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-rpm-stats", "/machine/rpm-stats"
OFFSET_BASE, RECORD_BASE = 0x0c3f0004, 0x0c3f0048
OFFSET_VALUE, STRIDE, RECORD_BYTES = 0xf0048, 32, 96
TAGS = (0x64736f61, 0x64737863, 0x20726464)
CONTRACT_SHA = "9aef5bfdf8c6c7ccbd01ef666f8c0f4cd9c4083365a06d27ca1d4bf979aa8337"
VMSTATE_BYTES, EXPECTED_CHECKS = 40, 15
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
WIDTHS = {"b": 1, "w": 2, "l": 4, "q": 8}
UINT_FIELDS = (
    "enabled-profile", "reference-abi", "offset-physical-base",
    "offset-window-bytes", "offset-word-value", "record-physical-base",
    "record-count", "record-stride", "record-window-bytes", "mapped-bytes",
    "vmstate-bytes", "irq-output-count", "clock-input-count",
    "clock-output-count", "timer-count",
)
BOOL_FIELDS = (
    "immutable-reference", "aop-executed", "live-statistics",
    "runtime-updates-modeled", "ready-modeled", "enabled-reference",
)
QOM_FIELDS = UINT_FIELDS + BOOL_FIELDS + ("contract-sha256",)
EXPECTED_BYTES = b"".join(struct.pack("<IIQQQ", tag, 0, 0, 0, 0) for tag in TAGS)

REPORT_CONSTANTS = {
    "schema": "tb371fc-rpm-stats-sm8250-v1",
    "suite": "rpm-stats-sm8250-reference",
    "expected_checks": EXPECTED_CHECKS,
    "reference_abi": 1,
    "contract_sha256": CONTRACT_SHA,
    "offset_physical_base": OFFSET_BASE,
    "offset_window_bytes": 4,
    "offset_word_value": OFFSET_VALUE,
    "record_physical_base": RECORD_BASE,
    "record_count": 3,
    "record_stride": STRIDE,
    "record_window_bytes": RECORD_BYTES,
    "record_tags": list(TAGS),
    "sparse_window_count": 2,
    "mapped_bytes": 100,
    "qom_property_count": len(QOM_FIELDS),
    "vmstate_version": 1,
    "vmstate_minimum_version": 1,
    "vmstate_payload_bytes": VMSTATE_BYTES,
    "reference_enabled_by_default": False,
    "immutable_reference": True,
    "aop_executed": False,
    "live_statistics": False,
    "runtime_updates_modeled": False,
    "ready_modeled": False,
    "firmware_is_pristine_stock": False,
    "guest_cpu_executed": False,
    "native_kernel_probe_observed": False,
    "android_boot_passed": False,
    "migration_tested": False,
    "migration_profile_mismatch_tested": False,
    "migration_wire_payload_verified": False,
    "migration_streams_created": 0,
    "migration_streams_modified": 0,
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def expected_world(reference):
    return {
        **dict(zip(UINT_FIELDS, (int(reference), 1, OFFSET_BASE, 4, OFFSET_VALUE,
                                RECORD_BASE, 3, STRIDE, RECORD_BYTES, 100,
                                VMSTATE_BYTES, 0, 0, 0, 0))),
        **dict(zip(BOOL_FIELDS, (True, False, False, False, False, bool(reference)))),
        "contract-sha256": CONTRACT_SHA,
    }


class RpmStatsRig(CameraRig):
    """Reuse the existing QTest/QMP transport, retaining all real commands."""

    def __init__(self, args, directory, *, reference=True, accel="qtest"):
        self.args = copy.copy(args)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.accel = bool(reference), accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.qom_get_commands = self.camera_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id, self.connected = 0, False
        self.socket_directory = Path(tempfile.mkdtemp(prefix="rpmstats-"))
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
                self.monitor("cont")
            self.connected = True
        except BaseException:
            self.close()
            raise

    def command(self, command):
        fields = command.split()
        if fields and fields[0] in tuple(op + width for op in ("read", "write") for width in WIDTHS):
            address = int(fields[1], 0)
            if OFFSET_BASE <= address < OFFSET_BASE + 4 or RECORD_BASE <= address < RECORD_BASE + RECORD_BYTES:
                self.device_mmio_operations += 1
        return super().command(command)

    def raw_monitor(self, execute, arguments=None):
        if execute == "qom-get":
            self.qom_get_commands += 1
        return super().raw_monitor(execute, arguments)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def world(self):
        value = {name: self.qom(name) for name in QOM_FIELDS}
        assert all(type(value[name]) is int for name in UINT_FIELDS)
        assert all(type(value[name]) is bool for name in BOOL_FIELDS)
        assert type(value["contract-sha256"]) is str
        assert value == expected_world(self.reference), value
        return value

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns

    def close(self):
        cleanup = {"connected": self.connected, "qmp_quit_acknowledged": False,
                   "forced_teardown_needed": False}
        error = None
        if self.connected and self.process.poll() is not None:
            error = RuntimeError("QEMU exited before the fixture quit request")
            cleanup["error"] = str(error)
        if self.connected and self.process.poll() is None:
            try:
                assert self.monitor("quit") == {}
                cleanup["qmp_quit_acknowledged"] = True
                self.process.wait(timeout=5)
                assert self.process.returncode == 0
            except Exception as exc:
                error = exc
                cleanup["error"] = str(exc)
        cleanup["forced_teardown_needed"] = self.process.poll() is None
        super().close()
        cleanup["qemu_returncode"] = self.process.returncode
        write_json(self.directory / "cleanup.json", cleanup)
        if error is not None:
            raise error


def record_bytes(rig, width="l"):
    size = WIDTHS[width]
    assert size in (4, 8)
    values = [rig.read_absolute(RECORD_BASE + offset, width)
              for offset in range(0, RECORD_BYTES, size)]
    raw = b"".join(value.to_bytes(size, "little") for value in values)
    assert raw == EXPECTED_BYTES
    return raw


def rejected(rig, region, offset, width, *, write=False):
    base = OFFSET_BASE if region == "offset" else RECORD_BASE
    size = WIDTHS[width]
    before_world = rig.world()
    before = rig.log_path.read_text(errors="replace")
    operation = "write" if write else "read"
    command = f"{operation}{width} {base + offset:#x}"
    if write:
        command += f" {(1 << (size * 8)) - 1:#x}"
    rig.command(command)
    after = rig.log_path.read_text(errors="replace")
    reason = "read-only-reference" if write else "disabled-or-invalid-access"
    diagnostic = f"{TYPE}: rejected {operation} region={region} offset={offset:#x} size {size} ({reason})"
    assert after.count(diagnostic) == before.count(diagnostic) + 1, (command, diagnostic, after[len(before):])
    assert rig.world() == before_world
    return {"command": command, "specific_diagnostic": diagnostic,
            "numeric_read_error_value_used_as_oracle": False}


def default_profile_and_rejection(rig):
    assert rig.reference is False and rig.world() == expected_world(False)
    probes = [rejected(rig, "offset", 0, "l"),
              rejected(rig, "records", 0, "l"), rejected(rig, "records", 0, "q")]
    return {"default_enabled": False, "probes": probes}


def enabled_identity_and_geometry(rig):
    before = rig.device_mmio_operations, rig.last_clock
    start = rig.qom_get_commands
    assert rig.world() == expected_world(True)
    assert rig.qom_get_commands - start == len(QOM_FIELDS)
    properties = {item["name"]: item["type"] for item in rig.monitor("qom-list", {"path": DEVICE})}
    assert all(properties[name] == "uint64" for name in UINT_FIELDS)
    assert all(properties[name] == "bool" for name in BOOL_FIELDS)
    assert properties["contract-sha256"] == "str"
    assert not any(name.startswith("qtest-") for name in properties)
    assert (rig.device_mmio_operations, rig.last_clock) == before
    return {"pure_qom_getters": len(QOM_FIELDS), "contract_sha256": CONTRACT_SHA}


def directory_word_read32(rig):
    assert rig.read_absolute(OFFSET_BASE) == OFFSET_VALUE
    assert 0x0c300000 + OFFSET_VALUE == RECORD_BASE
    return {"offset_word": OFFSET_VALUE, "full_u32_offset_preserved": True}


def all_record_words_read32(rig):
    raw = record_bytes(rig)
    write_json(rig.directory / "readback32.json", list(struct.unpack("<24I", raw)))
    return {"words": 24, "bytes": len(raw), "data_sha256": hashlib.sha256(raw).hexdigest()}


def all_record_words_read64(rig):
    raw = record_bytes(rig, "q")
    write_json(rig.directory / "readback64.json", list(struct.unpack("<12Q", raw)))
    return {"words": 12, "bytes": len(raw), "data_sha256": hashlib.sha256(raw).hexdigest()}


def record_field_layout_and_space_tag(rig):
    decoded = [struct.unpack("<IIQQQ", record_bytes(rig)[i * STRIDE:(i + 1) * STRIDE]) for i in range(3)]
    assert decoded == [(tag, 0, 0, 0, 0) for tag in TAGS]
    assert EXPECTED_BYTES[64:68] == b"ddr " and len(EXPECTED_BYTES) == 96
    return {"records": decoded, "ddr_tag_has_space_not_nul": True, "appended_smd_statistics": False}


def legal_accesses_at_region_ends(rig):
    assert rig.read_absolute(OFFSET_BASE) == OFFSET_VALUE
    assert rig.read_absolute(RECORD_BASE) == TAGS[0]
    assert rig.read_absolute(RECORD_BASE + 92) == 0
    assert rig.read_absolute(RECORD_BASE + 88, "q") == 0
    assert record_bytes(rig) == record_bytes(rig, "q")
    return {"last_read32_address": RECORD_BASE + 92, "last_read64_address": RECORD_BASE + 88}


def all_aligned_data_writes_are_rejected(rig):
    before = record_bytes(rig)
    probes = [rejected(rig, "offset", 0, "l", write=True)]
    for width in ("l", "q"):
        for offset in range(0, RECORD_BYTES, WIDTHS[width]):
            probes.append(rejected(rig, "records", offset, width, write=True))
    assert record_bytes(rig) == before and rig.read_absolute(OFFSET_BASE) == OFFSET_VALUE
    return {"probe_count": len(probes), "probes": probes, "data_unchanged": True}


def unsupported_read_widths_and_alignment(rig):
    probes = []
    matrices = {
        "offset": {"b": (0, 3), "w": (0, 1, 2), "l": (1, 2, 3), "q": (0,)},
        "records": {"b": (0, 1, 95), "w": (0, 1, 94), "l": (1, 2, 3, 93), "q": (1, 4, 7, 89, 92)},
    }
    for region, widths in matrices.items():
        for width, offsets in widths.items():
            probes += [rejected(rig, region, offset, width) for offset in offsets]
    assert record_bytes(rig) == EXPECTED_BYTES
    return {"probe_count": len(probes), "probes": probes}


def all_write_widths_and_alignment_are_rejected(rig):
    probes = []
    for region, matrices in (("offset", {"b": (0, 3), "w": (0, 1, 2), "l": (1, 3), "q": (0,)}),
                             ("records", {"b": (0, 1, 95), "w": (0, 1, 94), "l": (1, 2, 93), "q": (1, 4, 92)})):
        for width, offsets in matrices.items():
            probes += [rejected(rig, region, offset, width, write=True) for offset in offsets]
    assert record_bytes(rig) == EXPECTED_BYTES and rig.read_absolute(OFFSET_BASE) == OFFSET_VALUE
    return {"probe_count": len(probes), "probes": probes, "data_unchanged": True}


def exact_sparse_regions_and_holes(rig):
    tree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (rig.directory / "mtree.txt").write_text(tree)
    pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" + re.escape(TYPE) + r"\.[^\s]+)(?:\s|$)"
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(pattern, tree)}
    assert found == {(OFFSET_BASE, OFFSET_BASE + 3, TYPE + ".offset"),
                     (RECORD_BASE, RECORD_BASE + RECORD_BYTES - 1, TYPE + ".records")}, found
    holes = (0x0c3f0000, 0x0c3f0008, 0x0c3f001c, 0x0c3f0044,
             0x0c3f00a8, 0x0c3f00b8, 0x0c3f03fc)
    for address in holes:
        before = core_diagnostics(rig.log_path.read_text(errors="replace"))
        rig.read_absolute(address)
        rig.write_absolute(address, 0xffffffff)
        added = core_diagnostics(rig.log_path.read_text(errors="replace"))[len(before):]
        assert added == [(operation, address, 4, "(null)", "rejected") for operation in ("read", "write")], added
    assert record_bytes(rig) == EXPECTED_BYTES
    return {"sparse_window_count": 2, "mapped_bytes": 100, "holes": list(holes),
            "ddr_directory_and_next_record_not_owned": True}


def observers_and_time_do_not_update_statistics(rig):
    before = rig.world(), record_bytes(rig)
    for ns in (1, 99, 1000000):
        rig.step(ns)
        assert (rig.world(), record_bytes(rig)) == before
    assert not rig.seen_irq
    return {"clock_steps_ns": [1, 99, 1000000], "statistics_unchanged": True,
            "live_residency_measurement_claimed": False}


def reset_preserves_reference_and_disabled_rejection(rig):
    before = rig.world()
    if rig.reference:
        raw = record_bytes(rig)
    else:
        rejected(rig, "offset", 0, "l")
        rejected(rig, "records", 0, "q")
    rig.monitor("system_reset")
    assert rig.world() == before
    if rig.reference:
        assert record_bytes(rig) == raw and rig.read_absolute(OFFSET_BASE) == OFFSET_VALUE
    else:
        rejected(rig, "offset", 0, "l")
        rejected(rig, "records", 88, "q")
        rejected(rig, "records", 0, "l", write=True)
    return {"reference_enabled": rig.reference, "same_initialized_snapshot": True}


def paused_tcg_identity_only(rig):
    assert rig.accel == "tcg" and rig.cont_commands == 0
    status = rig.monitor("query-status")
    assert status["running"] is False and status["status"] in ("prelaunch", "paused")
    assert len(rig.monitor("query-cpus-fast")) == 8
    assert rig.world() == expected_world(True)
    assert rig.device_mmio_operations == 0
    return {"running": False, "cont_commands": 0, "device_mmio_operations": 0}


CASES = (
    (default_profile_and_rejection, None, "qtest"),
    (enabled_identity_and_geometry, True, "qtest"),
    (directory_word_read32, True, "qtest"),
    (all_record_words_read32, True, "qtest"),
    (all_record_words_read64, True, "qtest"),
    (record_field_layout_and_space_tag, True, "qtest"),
    (legal_accesses_at_region_ends, True, "qtest"),
    (all_aligned_data_writes_are_rejected, True, "qtest"),
    (unsupported_read_widths_and_alignment, True, "qtest"),
    (all_write_widths_and_alignment_are_rejected, True, "qtest"),
    (exact_sparse_regions_and_holes, True, "qtest"),
    (observers_and_time_do_not_update_statistics, True, "qtest"),
    (reset_preserves_reference_and_disabled_rejection, True, "qtest"),
    (reset_preserves_reference_and_disabled_rejection, False, "qtest"),
    (paused_tcg_identity_only, True, "tcg"),
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--machine", default=DEFAULT_MACHINE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = Path(__file__).resolve().parents[2]
    contract = root / "tb371fc/docs/rpm-stats-sm8250-reference-contract.json"
    started, original_hash = time.monotonic(), digest(args.qemu)
    report = {**REPORT_CONSTANTS, "qemu": args.qemu, "machine": args.machine,
              "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
              "test_script_sha256": digest(__file__), "checks": [],
              "binary_unchanged": False, "passed": False,
              "scope": "Immutable initialized reference only; ordinary MMIO boundaries and reset. No migration or firmware execution."}
    try:
        assert digest(contract) == CONTRACT_SHA
        fixed = json.loads(contract.read_bytes())
        assert fixed["record_layout"]["count"] == 3 and fixed["record_layout"]["stride_bytes"] == 32
        assert fixed["record_layout"]["total_bytes"] == 96 and fixed["record_layout"]["appended_smd_statistics"] is False
        assert [int(item["stat_type_u32"], 16) for item in fixed["record_layout"]["records"]] == list(TAGS)
        assert fixed["input_identity"]["aop_image"]["firmware_is_pristine_stock"] is False
        report["reviewed_source_sha256"] = {name: digest(root / name) for name in (
            "hw/misc/qcom-sm8250-rpm-stats.c", "include/hw/misc/qcom-sm8250-rpm-stats.h",
            "include/hw/misc/qcom-sm8250-rpm-stats-reference.h",
            "tb371fc/docs/rpm-stats-sm8250-reference-contract.json")}
        assert len(CASES) == EXPECTED_CHECKS
        for index, (test, reference, accel) in enumerate(CASES):
            fixture = out / f"case-{index:02d}-{test.__name__}"
            rig = None
            try:
                rig = RpmStatsRig(args, fixture, reference=reference, accel=accel)
                details = test(rig) or {}
            finally:
                if rig is not None:
                    rig.close()
            report["checks"].append({"name": f"{index:02d}-{test.__name__}", "passed": True,
                                     "fixture": fixture.name, **details})
        report["passed"] = (len(report["checks"]) == EXPECTED_CHECKS and
                            all(item["passed"] is True for item in report["checks"]))
    except BaseException as exc:
        report["error"], report["traceback"] = str(exc), traceback.format_exc()
    finally:
        report["qemu_sha256_after"] = digest(args.qemu)
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
        report["passed"] = report["passed"] and report["binary_unchanged"]
        report["duration_seconds"] = time.monotonic() - started
        write_json(out / "result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
