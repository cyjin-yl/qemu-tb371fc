#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real paused UFS checkpoints, translated DMA, and rejected unsafe state.

Each receiver starts after its source exits. The external disk is reused with
normal file locking; block contents are not transferred by the migration stream.
No native-DT boot, Android, active storage-I/O migration or silicon is attested.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import time
import traceback

from test_geni_migration import wait_migration
from test_ufs_sm8250 import Rig, ROOT, GCC, HCI, PHY, UTRL, CMD, DATA, sha

if not __debug__:
    raise SystemExit("Acceptance checks require assertions; do not use -O.")

APPS, CB, TABLE = 0x15000000, 0x15080000, 0x82000000
PTE_RW = 0x443
PATTERN_A = bytes((i * 37 + 11) & 255 for i in range(4096))
PATTERN_B = bytes((i * 19 + 83) & 255 for i in range(4096))


class UFSRig(Rig):
    def step(self, ns=20000):
        # QTest owns an external clock and cannot warp while the VM is paused.
        running = self.monitor("query-status")["running"]
        if not running:
            self.monitor("cont")
        try:
            return super().step(ns)
        finally:
            if not running:
                self.monitor("stop")

    def dma_address(self, physical):
        return physical - 0x80000000

    def qwrite(self, address, value):
        self.command(f"writeq {address:#x} {value:#x}")

    def qread(self, address):
        return int(self.command(f"readq {address:#x}")[0], 0)

    def wire(self, expected):
        self.r(HCI + 0x20)
        assert self.irq.get(0, False) == expected, self.irq


def setup(r):
    # Nonidentity translations for UTRD, command/response UPIU and PRDT data.
    r.qwrite(TABLE, (TABLE + 0x1000) | 3)
    r.qwrite(TABLE + 0x1000, (TABLE + 0x2000) | 3)
    for pa in (UTRL, CMD, DATA):
        r.qwrite(TABLE + 0x2000 + (r.dma_address(pa) >> 12) * 8, pa | PTE_RW)
    r.w(APPS + 0x1000, 1 << 16)
    r.w(APPS + 0x1800, 1)
    r.w(CB + 0x10, 5 | (7 << 15))
    r.w(CB + 0x30, 25 | (1 << 23))
    r.qwrite(CB + 0x20, TABLE | (0x42 << 48))
    r.w(CB + 0x38, 0xff)
    r.w(CB, 0x1067)
    r.w(APPS + 0x800, 0x800000e0)
    r.w(APPS + 0xc00, 0)
    r.w(APPS, 0x200436)
    r.w(GCC + 0x77004, 0)
    r.step(30000)
    assert r.r(GCC + 0x77004) & 0x80000000
    for offset in (0x77018, 0x77010, 0x770cc, 0x7705c, 0x7709c, 0x8c000):
        r.w(GCC + offset, 1)
    r.step()
    r.w(HCI + 0x34, 1)
    r.w(HCI + 0x24, 0x71fff)
    rows = json.loads((ROOT / "tb371fc/docs/ufs-qmp-registers.json").read_text())
    for entry in rows["registers"]:
        if entry["documented_driver_values"]:
            r.w(PHY + entry["offset"], entry["documented_driver_values"][0])
    r.w(PHY + 0xc04, 1)
    r.w(PHY + 0xc08, 0)
    r.w(PHY + 0xc00, 1)
    r.step(50000)
    assert r.r(PHY + 0xd80) == 1
    for offset in (0x7701c, 0x77020, 0x770b8):
        r.w(GCC + offset, 1)
    r.step()
    assert r.uic(0x16)[0] == 0
    r.w(HCI + 0x50, r.dma_address(UTRL))
    r.w(HCI + 0x54, 0)
    r.w(HCI + 0x60, 1)
    lun_ready(r)


def lun_ready(r):
    for _ in range(3):
        ocs, response, _ = r.scsi([0] * 6)
        if ocs == 0 and response[7] == 0:
            break
        r.scsi([3, 0, 0, 0, 18, 0], buffer_size=18)
    assert ocs == 0 and response[7] == 0


def io(r, lba, pattern=None):
    cdb = [0x2a if pattern is not None else 0x28, 0]
    cdb += list(struct.pack(">I", lba)) + [0, 0, 1, 0]
    ocs, response, data = r.scsi(cdb, write=pattern,
                                buffer_size=0 if pattern is not None else 4096)
    assert ocs == 0 and response[7] == 0, (ocs, response[:52].hex())
    return data


def flush(r):
    ocs, response, _ = r.scsi([0x35] + [0] * 9)
    assert ocs == 0 and response[7] == 0


def host_block(path, lba):
    with path.open("rb") as stream:
        stream.seek(lba * 4096)
        return stream.read(4096)


def registers(r):
    return {hex(off): r.r(HCI + off) for off in
            (0, 8, 0x20, 0x24, 0x30, 0x34, 0x50, 0x54, 0x58, 0x60, 0x64,
             0x70, 0x74, 0x78, 0x80, 0x94, 0x98, 0x9c, 0xdc, 0xe0, 0xe4)}


def section_body(data, suffix):
    # QEMU_VM_SECTION_FULL: tag, section-id, name-length, name, instance, version.
    # Require a unique v1 header, rather than a casual match in RAM or JSON.
    matches = []
    needle = suffix.encode() + struct.pack(">II", 0, 1)
    cursor = 0
    while (at := data.find(needle, cursor)) >= 0:
        end = at + len(suffix)
        for start in range(max(0, at - 255), at + 1):
            length = end - start
            if (0 < length <= 255 and start >= 6 and
                    data[start - 1] == length and data[start - 6] == 4):
                name = data[start:end]
                if name.endswith(suffix.encode()) and b"\0" not in name:
                    matches.append(end + 8)
        cursor = at + len(needle)
    assert len(matches) == 1, (suffix, matches)
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.qemu, args.out = args.qemu.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    checks, migrations, rejections, processes = [], [], [], []
    report = {
        "schema_version": 1, "kind": "sm8250-ufs-quiescent-migration",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": False, "checks": checks, "migrations": migrations,
        "rejections": rejections, "processes": processes,
        "qemu_sha256": sha(args.qemu), "test_sha256": sha(__file__),
        "helpers_sha256": {name: sha(Path(__file__).with_name(name)) for name in
                           ("test_ufs_sm8250.py", "test_geni_migration.py")},
        "scope": "Paused file checkpoints with UFS enabled and SID e0 stage-1 nonidentity DMA.",
        "limits": ["Only quiescent legacy storage queues; no in-flight SCSI or MCQ migration.",
                   "One QEMU process at a time, each with 128 MiB RAM.",
                   "QTest's external clock is explicitly aligned after load; it is not migrated device state.",
                   "The same external disk file is reused with locking enabled; media bytes are not migrated.",
                   "No native-DT boot, Android, silicon or complete-board compatibility claim."],
    }
    live = None
    disk = args.out / "disk.raw"
    with disk.open("xb") as stream:
        stream.truncate(64 * 1024 * 1024)
    started = time.monotonic()

    def passed(name, detail=None):
        row = {"name": name, "passed": True}
        if detail is not None:
            row["detail"] = detail
        checks.append(row)

    def start(name, *, incoming=False, image=disk, extra_args=()):
        nonlocal live
        assert live is None
        directory = args.out / name
        directory.mkdir()
        live = UFSRig(args.qemu, directory, disk=image, incoming=incoming,
                      extra_args=extra_args, autostart=False)
        processes.append({"name": name, "command": live.cmd})
        return live

    def close():
        nonlocal live
        if live is not None:
            live.close()
            processes[-1].update(exit_code=live.p.returncode,
                                 stderr_sha256=sha(live.d / "qemu.stderr"))
            live = None

    def checkpoint(name, r):
        path = args.out / (name + ".migration")
        assert not r.monitor("query-status")["running"]
        clock_ns = r.step(0)
        r.monitor("migrate", {"uri": "file:" + str(path)})
        outgoing = wait_migration(r)
        assert r.monitor("query-status")["status"] == "postmigrate"
        close()
        target = start(name + "-receiver", incoming=True)
        target.monitor("migrate-incoming", {"uri": "file:" + str(path)})
        incoming = wait_migration(target)
        assert not target.monitor("query-status")["running"]
        target.monitor("cont")
        restored_ns = int(target.command(f"clock_set {clock_ns}")[0], 0)
        target.monitor("stop")
        assert restored_ns == clock_ns
        migrations.append({"name": name, "source": outgoing, "target": incoming,
                           "external_qtest_clock_ns": clock_ns,
                           "stream_sha256": sha(path), "stream_bytes": path.stat().st_size})
        passed(name + "-qmp-completed-and-receiver-paused")
        return target, path

    def rejected(name, path, expected, *, image=disk, extra_args=()):
        before = sha(image)
        r = start(name, incoming=True, image=image, extra_args=extra_args)
        error = None
        try:
            r.monitor("migrate-incoming", {"uri": "file:" + str(path)})
        except (ValueError, OSError, AssertionError) as exc:
            error = str(exc)
        code = r.p.wait(timeout=15)
        text = (r.d / "qemu.stderr").read_text(errors="replace")
        assert code == 1, (name, code, text)
        assert expected in text, (name, expected, text)
        assert not any(e.get("event") == "RESUME" for e in r.events), r.events
        assert sha(image) == before
        rejections.append({"name": name, "exit_code": code,
                           "expected_diagnostic": expected, "qmp_error": error,
                           "stream_sha256": sha(path), "media_sha256": before})
        passed(name + "-rejected-without-resume-or-media-change")
        close()

    try:
        r = start("source")
        setup(r)
        assert r.query(6, 0x0e) == (0, 0, 1)
        assert r.query(6, 0x10) == (0, 0, 1)
        assert r.query(4, 0x0f, 0xdecafbad) == (0, 0, 0xdecafbad)
        assert r.uic(2, 0x9007, 12)[0] == 0
        io(r, 4, PATTERN_A)
        assert io(r, 4) == PATTERN_A
        flush(r)
        assert host_block(disk, 4) == PATTERN_A
        assert r.query(3, 0x1d) == (0, 0, 9)
        assert r.r(CB + 0x58) & 0x800001fe == 0
        passed("source-translated-write-read-flush-and-host-media-match")
        ocs, response, _ = r.scsi([0xff] + [0] * 15)
        assert ocs == 0 and response[7] == 2
        # Keep the real transfer-completion IRQ asserted while a DME timer runs.
        r.w(HCI + 0x20, 1 << 10)
        r.w(HCI + 0x94, 0x9007 << 16)
        r.w(HCI + 0x98, 0)
        r.w(HCI + 0x9c, 44)
        r.w(HCI + 0x90, 2)
        r.step(333)
        assert r.r(HCI + 0x20) & 1 and not r.r(HCI + 0x20) & (1 << 10)
        r.wire(True)
        before = registers(r)
        r, original = checkpoint("active-uic", r)
        assert registers(r) == before
        r.wire(True)
        passed("hci-ram-and-pending-transfer-irq-preserved")
        r.w(HCI + 0x20, 1)
        r.wire(False)
        r.step(666)
        assert not r.r(HCI + 0x20) & (1 << 10)
        r.step(1)
        assert r.r(HCI + 0x20) & (1 << 10) and r.r(HCI + 0x98) & 0xff == 0
        r.wire(True)
        r.w(HCI + 0x20, 0xffffffff)
        r.wire(False)
        assert r.uic(1, 0x9007) == (0, 44)
        passed("pending-uic-finishes-on-original-deadline-and-irq-clears")
        assert r.query(3, 0x0f) == (0, 0, 0xdecafbad)
        assert r.query(5, 0x0e) == (0, 0, 1)
        assert r.query(5, 0x10) == (0, 0, 1)
        assert r.query(3, 0x1d) == (0, 0, 9)
        passed("query-wire-endian-attribute-and-wb-flags-accounting-preserved")
        ocs, response, sense = r.scsi([3, 0, 0, 0, 18, 0], buffer_size=18)
        assert ocs == 0 and response[7] == 0 and sense[2] == 5 and sense[12] == 0x20
        passed("scsi-child-sense-state-preserved")
        assert io(r, 4) == PATTERN_A
        io(r, 7, PATTERN_B)
        flush(r)
        assert io(r, 7) == PATTERN_B and host_block(disk, 7) == PATTERN_B
        assert host_block(disk, 4) == PATTERN_A
        r.qwrite(CB + 0x800, r.dma_address(DATA))
        assert r.qread(CB + 0x50) & 0x0000fffffffff001 == DATA
        assert r.r(CB + 0x58) & 0x800001fe == 0
        passed("receiver-smmu-translation-dma-write-read-and-host-media-match")
        # Hold the WB workload while wall time runs during fixture alignment.
        assert r.query(7, 0x10) == (0, 0, 0)
        assert r.uic(0x17)[0] == 0
        r.w(PHY + 0xc08, 1)
        r.w(PHY + 0xc00, 0)
        r.w(PHY + 0xc04, 0)
        assert r.r(PHY + 0xd80) == 0 and r.r(HCI + 0x30) & 1
        before = registers(r)
        r, _ = checkpoint("hibern8-phy-off", r)
        assert registers(r) == before and r.r(PHY + 0xd80) == 0
        r.w(HCI + 0x58, 1)
        assert r.r(HCI + 0x58) == 0
        passed("hibern8-phy-off-retains-link-and-rejects-data-doorbell")
        r.w(PHY + 0xc04, 1)
        r.w(PHY + 0xc08, 0)
        r.w(PHY + 0xc00, 1)
        r.step(3000)
        assert r.r(PHY + 0xd80) == 0
        r, _ = checkpoint("phy-timer", r)
        r.step(6999)
        assert r.r(PHY + 0xd80) == 0
        r.step(1)
        assert r.r(PHY + 0xd80) == 1
        assert r.monitor("qom-get", {"path": "/machine/ufs/tx-lane0",
                                     "property": "qtest-clock-period"}) > 0
        passed("phy-ready-deadline-and-symbol-clock-propagation-preserved")
        assert r.uic(0x18)[0] == 0
        assert r.query(3, 0x1d) == (0, 0, 9)
        assert r.query(5, 0x10) == (0, 0, 0)
        assert io(r, 4) == PATTERN_A and io(r, 7) == PATTERN_B
        passed("second-and-third-receivers-continue-real-media-io")
        assert r.query(6, 0x10) == (0, 0, 1)
        assert r.uic(0x17)[0] == 0
        r.monitor("cont")
        time.sleep(0.35)
        r.monitor("stop")
        assert r.uic(0x18)[0] == 0
        assert r.query(3, 0x1d) == (0, 0, 10)
        passed("restored-virtual-rt-idle-timer-flushes-wb-in-hibern8")
        close()

        for prop, value in (("hw-version", "0x30000000"), ("nutrs", "16")):
            rejected("reject-" + prop, original, "qcom-sm8250-ufs",
                     extra_args=("-global", f"qcom-sm8250-ufs.{prop}={value}"))
        small = args.out / "different-capacity.raw"
        with small.open("xb") as stream:
            stream.truncate(32 * 1024 * 1024)
        rejected("reject-lun-capacity", original, "migration_config", image=small)

        raw = original.read_bytes()
        body = section_body(raw, "qcom-sm8250-ufs")
        # v1 has a 44-byte profile prefix, then the 908-byte UfsReg stream.
        bad = bytearray(raw)
        doorbell = body + 44 + 0x58
        assert bad[doorbell:doorbell + 4] == bytes(4)
        struct.pack_into(">I", bad, doorbell, 1)
        path = args.out / "bad-doorbell.migration"
        path.write_bytes(bad)
        rejected("reject-nonempty-wire-doorbell", path, "quiescent legacy queues")
        # Packed wire Attributes = 309 bytes, Flags = 255; WB used_bytes is +24.
        used = body + 44 + 908 + 309 + 255 + 24
        assert struct.unpack_from(">Q", raw, used)[0] == 4096
        bad = bytearray(raw)
        struct.pack_into(">Q", bad, used, (1 << 64) - 1)
        path = args.out / "bad-wb.migration"
        path.write_bytes(bad)
        rejected("reject-wb-overflow", path, "invalid WB or HID state")
        scsi = section_body(raw, "scsi-disk")
        # vmstate_scsi_device: UA (3), sense_is_ua (1), sense (96), length (4).
        requests = scsi + 104
        assert raw[requests] == 0
        fake = bytes([2]) + bytes(16) + struct.pack(">II", 0, 0)
        path = args.out / "bad-scsi-queue.migration"
        path.write_bytes(raw[:requests] + fake + raw[requests:])
        rejected("reject-incoming-scsi-request", path,
                 "UFS migration rejects an incoming SCSI request")

        r = start("reject-source-busy")
        setup(r)
        r.w(HCI + 0x50, 0x30000)  # Deliberately unmapped UTRD IOVA.
        r.w(HCI + 0x58, 1)
        r.step()
        assert r.r(HCI + 0x58) == 1 and r.r(CB + 0x58) & 2
        assert r.qread(CB + 0x60) == 0x30000
        r.monitor("migrate", {"uri": "file:" + str(args.out / "busy.migration")})
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            state = r.monitor("query-migrate")
            if state.get("status") == "failed":
                break
            time.sleep(0.02)
        assert state.get("status") == "failed", state
        assert "quiescent legacy queues" in state.get("error-desc", ""), state
        assert not r.monitor("query-status")["running"]
        assert r.r(HCI + 0x58) == 1
        rejections.append({"name": "reject-source-busy", "source": state})
        passed("actual-uncompleted-source-dma-request-rejects-checkpoint")
        r.w(HCI + 0x34, 0)
        r.w(CB + 0x58, 0xffffffff)
        r.w(HCI + 0x34, 1)
        assert r.uic(0x16)[0] == 0
        r.w(HCI + 0x50, r.dma_address(UTRL))
        r.w(HCI + 0x60, 1)
        ocs, response, _ = r.transfer(bytes(32))
        assert ocs == 0 and response[0] == 0x20
        lun_ready(r)
        assert io(r, 4) == PATTERN_A
        passed("failed-source-checkpoint-keeps-controller-recoverable-by-hce-reset")
        close()
        assert len(migrations) == 3 and len(rejections) == 7
        assert sha(args.qemu) == report["qemu_sha256"]
        report["all_passed"] = True
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        close()
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["passed_checks"] = len(checks)
        (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
