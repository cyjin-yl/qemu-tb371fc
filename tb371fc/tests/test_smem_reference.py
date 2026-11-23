#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validate explicit SMEM handoff RAM, reset and actual process migration.

QTest writes allocation fixtures in the documented Linux format and uses the
real TCSR MMIO owner word. It does not execute a guest CPU or claim a successful
Linux SMEM driver probe. The guest-driver gate is separate.
"""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
import traceback
from types import SimpleNamespace

from test_geni_uart import Rig

BASE, SIZE = 0x80900000, 0x200000
PTABLE = SIZE - 0x1000
GLOBAL = 0x3000
LOCK = 0x01F43000
IPCC = 0x00408000
EXPECTED_CHECKS = 22

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


class SmemRig(Rig):
    def __init__(self, qemu, artifacts, profile="off", unlocked=False,
                 incoming=False):
        self.artifacts = Path(artifacts)
        self.artifacts.mkdir(parents=True, exist_ok=False)
        self.socket_directory = Path(tempfile.mkdtemp(prefix="smem-"))
        self.transcript = (self.artifacts / "transactions.log").open(
            "w", buffering=1)
        self.closed = False
        extra = []
        if unlocked:
            extra += ["-global", "qcom-tcsr-mutex.boot-unlocked-reference=on"]
        args = SimpleNamespace(
            qemu=str(qemu), base=BASE, qom_path="/machine/smem-handoff",
            machine="tb371fc,ufs-enabled=off,smem-profile=" + profile,
            extra_qemu_args=extra)
        (self.artifacts / "configuration.json").write_text(json.dumps({
            "qemu": str(qemu), "machine": args.machine,
            "extra_qemu_args": extra, "incoming": incoming,
            "socket_directory": str(self.socket_directory),
        }, indent=2) + "\n")
        super().__init__(args, str(self.socket_directory), incoming)

    def trace(self, prefix, text):
        if len(text) > 300:
            text = (text[:80] + "... length=" + str(len(text)) +
                    " sha256=" + hashlib.sha256(text.encode()).hexdigest())
        self.transcript.write(prefix + text + "\n")

    def command(self, command):
        if command.startswith("irq_intercept_out "):
            return []
        self.trace("qtest> ", command)
        reply = super().command(command)
        self.trace("qtest< ", repr(reply))
        return reply

    def monitor(self, execute, arguments=None):
        self.transcript.write("qmp> " + json.dumps(
            {"execute": execute, "arguments": arguments}) + "\n")
        result = super().monitor(execute, arguments)
        self.transcript.write("qmp< " + json.dumps(result) + "\n")
        return result

    def get(self, address):
        return int(self.command(f"readl {address:#x}")[0], 0)

    def put(self, address, value):
        self.command(f"writel {address:#x} {value:#x}")

    def read_bytes(self, address, size):
        # Bound transcript and protocol lines while covering the entire region.
        data = bytearray()
        for offset in range(0, size, 4096):
            n = min(4096, size - offset)
            reply = self.command(f"read {address + offset:#x} {n:#x}")
            assert len(reply) == 1 and reply[0].startswith("0x"), reply
            block = bytes.fromhex(reply[0][2:])
            assert len(block) == n
            data.extend(block)
        return bytes(data)

    def store_bytes(self, address, data):
        for offset in range(0, len(data), 4096):
            part = data[offset:offset + 4096]
            self.command(f"write {address + offset:#x} {len(part):#x} "
                         f"0x{part.hex()}")

    def image(self):
        return self.read_bytes(BASE, SIZE)

    def reset_board(self):
        self.put(IPCC + 0x0c, 0)
        before = self.monitor("qom-get", {
            "path": "/machine/ipcc", "property": "transmit-count"})
        assert before > 0
        self.monitor("system_reset")
        self.wait_for(lambda: self.monitor("qom-get", {
            "path": "/machine/ipcc", "property": "transmit-count"}) == 0,
            "actual system reset completion")

    def close(self):
        if self.closed:
            return
        self.closed = True
        super().close()
        if self.log_path.exists():
            shutil.copy2(self.log_path, self.artifacts / "qemu.stderr")
        self.transcript.close()


def parse_image(image):
    """Independent bounded decoder for the selected empty/global v12 ABI."""
    assert len(image) == SIZE
    word = lambda off: struct.unpack_from("<I", image, off)[0]
    assert word(0x5c) == 12 << 16
    assert word(0xc0) == 1 and word(0xcc) == 0
    assert image[PTABLE:PTABLE + 4] == b"$TOC"
    assert word(PTABLE + 4) == 1 and word(PTABLE + 8) == 1
    entry = PTABLE + 32
    offset, size, flags, host0, host1, cacheline = struct.unpack_from(
        "<IIIHHI", image, entry)
    assert offset >= 0x20d0 and offset % 4096 == 0
    assert size >= 32 and offset + size == PTABLE
    assert host0 == host1 == 0xfffe and flags == 0
    assert cacheline > 0 and cacheline & (cacheline - 1) == 0
    assert size % cacheline == 0
    assert image[offset:offset + 4] == b"$PRT"
    h0, h1, part_size, uncached, cached = struct.unpack_from(
        "<HHIII", image, offset + 4)
    assert (h0, h1, part_size) == (host0, host1, size)
    assert 32 <= uncached <= cached <= size
    # This fixture allocator only uses the uncached list.
    assert cached == size
    info = entry + 48
    assert info + 20 <= SIZE
    assert image[info:info + 4] == b"SIII"
    assert word(info + 4) == SIZE and word(info + 8) == BASE
    count = struct.unpack_from("<H", image, info + 16)[0]
    assert count == 512
    items = {}
    cursor = 32
    while cursor < uncached:
        assert cursor + 16 <= uncached
        canary, item, data_size, padding, header_padding, reserved = (
            struct.unpack_from("<HHIHHI", image, offset + cursor))
        assert canary == 0xa5a5 and reserved == 0
        assert 0 < data_size < size and padding < data_size
        assert item < count and item not in items
        start = cursor + 16 + header_padding
        end = start + data_size
        assert cursor < start <= end <= uncached
        items[item] = image[offset + start:offset + end - padding]
        cursor = end
    assert cursor == uncached
    return {"offset": offset, "size": size, "uncached": uncached,
            "cached": cached, "cacheline": cacheline, "items": items}


def allocate_fixture(rig, item, data):
    """Real RAM/MMIO transactions with a synthetic Linux-format allocation."""
    assert 8 <= item < 512 and len(data) > 0
    rig.put(LOCK, 1)
    assert rig.get(LOCK) == 1
    try:
        part = BASE + GLOBAL
        free = rig.get(part + 12)
        cached = rig.get(part + 16)
        aligned = (len(data) + 7) & ~7
        assert free >= 32 and free + 16 + aligned <= cached
        entry = struct.pack("<HHIHHI", 0xa5a5, item, aligned,
                            aligned - len(data), 0, 0)
        rig.store_bytes(part + free, entry + data + bytes(aligned - len(data)))
        rig.put(part + 12, free + len(entry) + aligned)
    finally:
        rig.put(LOCK, 0)


def migrate(source, target, rejection=False):
    uri = "unix:" + str(target.socket_directory / "migration")
    target.monitor("migrate-incoming", {"uri": uri})
    source.monitor("migrate", {"uri": uri})
    deadline = time.monotonic() + 25
    outgoing, incoming = {}, {}
    while time.monotonic() < deadline:
        outgoing = source.monitor("query-migrate")
        if target.process.poll() is not None:
            break
        try:
            incoming = target.monitor("query-migrate")
        except (RuntimeError, OSError, AssertionError):
            if not rejection:
                raise
        if not rejection:
            if outgoing.get("status") == incoming.get("status") == "completed":
                return {"source": outgoing, "destination": incoming,
                        "expected_rejection": False}
            assert outgoing.get("status") not in ("failed", "cancelled"), outgoing
        time.sleep(0.01)
    else:
        raise TimeoutError("migration did not reach the required outcome")
    assert rejection, {"source": outgoing, "destination": incoming}
    exit_code = target.process.returncode
    target.close()
    stderr = (target.artifacts / "qemu.stderr").read_text(errors="replace")
    assert exit_code not in (None, 0), exit_code
    assert ("qcom-smem-handoff: reference-empty differs between source and "
            "destination") in stderr, stderr
    return {"source": outgoing, "destination": incoming,
            "expected_rejection": True, "destination_exit": exit_code,
            "destination_stderr": stderr}


def rejected_configuration(qemu, profile, needle, directory):
    command = [str(qemu), "-machine",
               "tb371fc,ufs-enabled=off,smem-profile=" + profile,
               "-accel", "qtest", "-m", "128M", "-display", "none",
               "-serial", "null", "-monitor", "none", "-S"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    Path(directory, "rejected-" + profile + ".json").write_text(json.dumps({
        "command": command, "exit": result.returncode,
        "stdout": result.stdout, "stderr": result.stderr,
    }, indent=2) + "\n")
    assert result.returncode != 0 and needle in result.stderr, result


def run(args):
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=False)
    qemu = Path(args.qemu).resolve(strict=True)
    started = time.monotonic()
    report = {"schema": "tb371fc-smem-reference-v1", "checks": [],
              "expected_checks": EXPECTED_CHECKS, "migrations": [],
              "passed": False, "all_passed": False,
              "qemu_sha256": digest(qemu), "script_sha256": digest(__file__),
              "started_utc": datetime.datetime.now(
                  datetime.timezone.utc).isoformat(),
              "guest_cpu_executed": False, "native_sbl_captured": False,
              "remote_firmware_emulated": False,
              "scope": "Actual QTest RAM/MMIO and process migration; allocation "
                       "fixtures are synthetic, not a guest driver verdict"}
    rigs = []
    def rig(name, profile="off", unlocked=False, incoming=False):
        obj = SmemRig(qemu, out / name, profile, unlocked, incoming)
        rigs.append(obj)
        return obj
    def check(name, condition=True):
        assert condition, name
        report["checks"].append({"name": name, "passed": True})
    try:
        d = rig("default")
        check("always_present_marker_defaults_off", d.monitor("qom-get", {
            "path": "/machine/smem-handoff", "property": "reference-empty"})
            is False)
        check("default_ram_has_no_initialized_smem",
              d.read_bytes(BASE + 0xc0, 16) == bytes(16) and
              d.read_bytes(BASE + PTABLE, 32) == bytes(32))
        d.store_bytes(BASE + GLOBAL, b"off-guest-owned")
        d.put(BASE + 0xc0, 0x12345678)
        d.reset_board()
        check("off_reset_does_not_invent_a_handoff",
              d.get(BASE + 0xc0) == 0x12345678 and
              d.read_bytes(BASE + GLOBAL, 15) == b"off-guest-owned")
        d.close()
        rejected_configuration(qemu, "invalid", "smem-profile must be", out)
        check("invalid_profile_rejected")
        rejected_configuration(qemu, "reference-empty",
                               "requires -global qcom-tcsr-mutex", out)
        check("reference_requires_explicit_unlocked_mutex")
        r = rig("reference", "reference-empty", True)
        check("reference_and_mutex_profiles_are_explicit",
              r.monitor("qom-get", {"path": "/machine",
                  "property": "smem-profile"}) == "reference-empty" and
              r.monitor("qom-get", {"path": "/machine/tcsr-mutex",
                  "property": "boot-unlocked-reference"}) is True)
        initial = r.image()
        decoded = parse_image(initial)
        check("v12_header_directory_and_partition_are_valid")
        check("global_partition_is_bounded_and_disjoint",
              decoded["offset"] == GLOBAL and
              decoded["size"] == PTABLE - GLOBAL and
              decoded["cacheline"] == 64)
        check("global_and_legacy_items_are_empty",
              decoded["items"] == {} and initial[0xd0:0x20d0] == bytes(8192))
        check("no_fabricated_payload_in_allocator",
              initial[GLOBAL + 32:PTABLE] == bytes(PTABLE - GLOBAL - 32))
        r.store_bytes(BASE + GLOBAL + 101, b"writeable")
        check("handoff_is_writable_ordinary_ram",
              r.read_bytes(BASE + GLOBAL + 101, 9) == b"writeable")
        r.store_bytes(BASE + GLOBAL + 101, bytes(9))
        allocate_fixture(r, 42, b"first-fixture")
        check("locked_allocation_fixture_has_valid_bytes",
              parse_image(r.image())["items"] == {42: b"first-fixture"} and
              r.get(LOCK) == 0)
        allocate_fixture(r, 73, bytes(range(33)))
        expected_items = {42: b"first-fixture", 73: bytes(range(33))}
        check("second_fixture_preserves_prior_allocation",
              parse_image(r.image())["items"] == expected_items)
        r.put(BASE - 4, 0xA1B2C3D4)
        r.put(BASE + SIZE, 0x10203040)
        r.put(LOCK, 1)
        before = r.image()
        target = rig("reference-incoming", "reference-empty", True, True)
        report["migrations"].append(migrate(r, target))
        check("same_profile_migration_completed")
        check("all_ram_and_lock_state_survive_migration",
              target.image() == before and target.get(LOCK) == 1)
        target.put(LOCK, 0)
        allocate_fixture(target, 74, b"after-migration")
        expected_items[74] = b"after-migration"
        check("allocator_frontier_continues_after_migration",
              parse_image(target.image())["items"] == expected_items)
        target.reset_board()
        check("normal_reset_recreates_exact_empty_image",
              target.image() == initial and target.get(LOCK) == 0)
        check("reset_does_not_touch_neighbor_ram",
              target.get(BASE - 4) == 0xA1B2C3D4 and
              target.get(BASE + SIZE) == 0x10203040)
        try:
            target.monitor("qom-set", {"path": "/machine",
                "property": "smem-profile", "value": "off"})
        except AssertionError as exc:
            check("profile_is_immutable_after_creation",
                  "fixed after machine creation" in str(exc))
        else:
            raise AssertionError("smem-profile changed after initialization")
        r.close()
        target.close()
        a = rig("off-source", unlocked=True)
        b = rig("off-incoming", unlocked=True, incoming=True)
        a.store_bytes(BASE + 0xc0, b"guest-owned-off-profile")
        report["migrations"].append(migrate(a, b))
        check("off_to_off_migrates_existing_ram",
              b.read_bytes(BASE + 0xc0, 23) == b"guest-owned-off-profile")
        a.close()
        b.close()
        for src_profile, dst_profile, name in (
                ("reference-empty", "off", "reference_to_off"),
                ("off", "reference-empty", "off_to_reference")):
            a = rig(name + "-source", src_profile, True)
            b = rig(name + "-incoming", dst_profile, True, True)
            # Same mutex profiles isolate the handoff marker's rejection.
            report["migrations"].append(migrate(a, b, rejection=True))
            check(name + "_migration_rejected_by_handoff_marker")
            a.close()
        report["binary_unchanged"] = digest(qemu) == report["qemu_sha256"]
        report["all_passed"] = (len(report["checks"]) == EXPECTED_CHECKS and
                                report["binary_unchanged"] and
                                len(report["migrations"]) == 4)
        report["passed"] = report["all_passed"]
    except BaseException as error:
        report["error"] = type(error).__name__ + ": " + str(error)
        report["traceback"] = traceback.format_exc()
    finally:
        for item in reversed(rigs):
            item.close()
        report["elapsed_seconds"] = time.monotonic() - started
        (out / "result.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": report["passed"],
                      "checks": len(report["checks"]),
                      "expected": EXPECTED_CHECKS, "out": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", required=True)
    raise SystemExit(run(parser.parse_args()))
