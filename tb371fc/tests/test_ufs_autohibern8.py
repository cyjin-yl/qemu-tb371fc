#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise AHIT, runtime clock/PHY sleep, and real process checkpoints.

Each receiver starts after its source exits and reopens the same synthetic
disk with normal file locking. No physical timing or complete-board claim.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import time
import traceback

from test_geni_migration import wait_migration
from test_ufs_migration import (
    UFSRig, setup, io, flush, host_block, PATTERN_A, lun_ready, CB,
)
from test_ufs_sm8250 import GCC, HCI, PHY, UTRL, CMD, sha

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")

AHIT = HCI + 0x18
AUTOH8 = 1 << 23
UIC_COMPLETION = 1 << 10
HIBERN8_INTERRUPTS = (1 << 5) | (1 << 6)


def state(r):
    return r.monitor("qom-get", {"path": "/machine/ufs",
                                  "property": "link-state"})


def phy_off(r):
    r.w(PHY + 0xc08, 1)
    r.w(PHY + 0xc00, 0)
    r.w(PHY + 0xc04, 0)
    assert r.r(PHY + 0xd80) == 0


def phy_on(r):
    r.w(PHY + 0xc04, 1)
    r.w(PHY + 0xc08, 0)
    r.w(PHY + 0xc00, 1)
    r.step(10000)
    assert r.r(PHY + 0xd80) == 1


def subsection(raw):
    name = b"qcom-sm8250-ufs/auto-hibern8"
    header = bytes([5, len(name)]) + name + struct.pack(">I", 1)
    assert raw.count(header) == 1
    return raw.index(header) + len(header)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.qemu, args.out = args.qemu.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    disk = args.out / "disk.raw"
    with disk.open("xb") as stream:
        stream.truncate(64 * 1024 * 1024)
    checks, migrations, rejections, processes = [], [], [], []
    report = {
        "schema_version": 1, "kind": "sm8250-ufs-auto-hibern8",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": False, "checks": checks, "migrations": migrations,
        "rejections": rejections, "processes": processes,
        "qemu_sha256": sha(args.qemu), "test_sha256": sha(__file__),
        "helpers_sha256": {name: sha(Path(__file__).with_name(name)) for name in
                           ("test_ufs_migration.py", "test_ufs_sm8250.py",
                            "test_geni_migration.py")},
        "scope": "AHIT with real SID e0 nonidentity DMA, PHY/clock sleep and quiescent file checkpoints.",
        "limits": ["Digital idle timing; automatic exit has no analog latency model.",
                   "Storage queues must be quiescent for migration.",
                   "The external medium is reused, not copied by the stream.",
                   "QTest external clocks are aligned after load.",
                   "No Android, native-DT or physical device attestation."],
    }
    live = None
    started = time.monotonic()

    def passed(name, detail=None):
        row = {"name": name, "passed": True}
        if detail is not None:
            row["detail"] = detail
        checks.append(row)

    def start(name, *, incoming=False, extra_args=()):
        nonlocal live
        assert live is None
        directory = args.out / name
        directory.mkdir()
        live = UFSRig(args.qemu, directory, disk=disk, incoming=incoming,
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
        assert not r.monitor("query-status")["running"]
        clock_ns = r.clock_ns
        path = args.out / (name + ".migration")
        r.monitor("migrate", {"uri": "file:" + str(path)})
        outgoing = wait_migration(r)
        assert r.monitor("query-status")["status"] == "postmigrate"
        close()
        target = start(name + "-receiver", incoming=True)
        target.monitor("migrate-incoming", {"uri": "file:" + str(path)})
        incoming = wait_migration(target)
        assert not target.monitor("query-status")["running"]
        target.monitor("cont")
        target.clock_ns = int(target.command(f"clock_set {clock_ns}")[0], 0)
        target.monitor("stop")
        assert target.clock_ns == clock_ns
        migrations.append({"name": name, "source": outgoing, "target": incoming,
                           "qtest_clock_ns": clock_ns, "stream_sha256": sha(path),
                           "stream_bytes": path.stat().st_size})
        passed(name + "-new-process-restored-paused")
        return target, path

    def rejected(name, path, diagnostic, *, extra_args=()):
        before = sha(disk)
        r = start(name, incoming=True, extra_args=extra_args)
        error = None
        try:
            r.monitor("migrate-incoming", {"uri": "file:" + str(path)})
        except (OSError, ValueError, AssertionError) as exc:
            error = str(exc)
        assert r.p.wait(timeout=15) == 1
        text = (r.d / "qemu.stderr").read_text(errors="replace")
        assert diagnostic in text, (name, diagnostic, text)
        for _ in range(1024):
            line = r.m.readline(1024 * 1024)
            if not line:
                break
            message = json.loads(line)
            if "event" in message:
                r.events.append(message)
        else:
            raise AssertionError("QMP did not reach EOF")
        assert not any(e.get("event") == "RESUME" for e in r.events)
        assert sha(disk) == before
        rejections.append({"name": name, "expected_diagnostic": diagnostic,
                           "qmp_error": error, "qmp_events": r.events,
                           "qmp_drained_to_eof": True,
                           "stream_sha256": sha(path), "media_sha256": before})
        passed(name + "-no-resume-or-media-change")
        close()

    try:
        r = start("source")
        setup(r)
        cap = r.r(HCI)
        assert cap & AUTOH8 and r.r(AHIT) == 0
        io(r, 4, PATTERN_A)
        flush(r)
        assert host_block(disk, 4) == PATTERN_A
        # A masked completion must still start idle timing.
        r.w(HCI + 0x24, 0)
        r.w(HCI + 0x20, 0xffffffff)
        r.w(AHIT, 10 | (1 << 10))  # 10 * 10 microseconds.
        assert r.r(AHIT) == 10 | (1 << 10)
        r.step(99999)
        assert state(r) == "active"
        r.step(1)
        assert state(r) == "hibern8"
        assert not r.r(HCI + 0x20) & (HIBERN8_INTERRUPTS | UIC_COMPLETION)
        passed("capability-and-exact-scaled-idle-deadline-without-uic-interrupt")
        for _ in range(3):
            assert io(r, 4) == PATTERN_A
            r.step(200000)
            assert state(r) == "hibern8"
            assert not r.r(HCI + 0x20) & HIBERN8_INTERRUPTS
        passed("three-automatic-wake-dma-read-and-idle-reentry-cycles-with-ie-zero")

        # Disabling idle entry keeps an already sleeping link wakeable.
        r.w(AHIT, 0)
        assert io(r, 4) == PATTERN_A
        r.step(1000000)
        assert state(r) == "active"
        passed("disabling-ahit-stops-reentry-and-keeps-automatic-wake")

        # QUERY_TASK completes synchronously, so it must replace a nearly
        # expired idle deadline even if its completion IRQ is masked.
        task = bytearray(80)
        struct.pack_into("<I", task, 0, 1 << 24)
        struct.pack_into("<I", task, 8, 15)
        task[16], task[21] = 4, 0x80
        r.write(CMD, task)
        r.w(HCI + 0x70, r.dma_address(CMD))
        r.w(HCI + 0x74, 0)
        r.w(HCI + 0x80, 1)
        r.w(AHIT, 100)
        r.step(90000)
        r.w(HCI + 0x78, 1)
        response = r.read(CMD, 80)
        assert r.r(HCI + 0x78) == 0
        assert struct.unpack_from("<I", response, 8)[0] & 0xff == 0
        assert response[48] == 0x24 and response[54] == 0
        assert r.r(HCI + 0x20) & (1 << 9)
        r.step(99999)
        assert state(r) == "active"
        r.step(1)
        assert state(r) == "hibern8"
        passed("synchronous-task-management-restarts-full-idle-interval")
        r.w(AHIT, 0)
        assert io(r, 4) == PATTERN_A

        r.w(AHIT, 100)
        r.step(40000)
        r.w(AHIT, 0)
        r.step(200000)
        assert state(r) == "active"
        r.w(AHIT, 0x2000)
        assert r.r(AHIT) == 0
        r.w(AHIT, 1 | (6 << 10))
        assert r.r(AHIT) == 0
        passed("ahit-disable-cancels-pending-timer-and-invalid-encodings-reject")

        # UIC occupies the link until its own completion, then a full AHIT
        # interval begins. QOM only observes state; it does not force it.
        r.w(AHIT, 1)
        r.w(HCI + 0x20, 0xffffffff)
        r.w(HCI + 0x94, 0x9007 << 16)
        r.w(HCI + 0x98, 0)
        r.w(HCI + 0x9c, 0)
        r.w(HCI + 0x90, 1)
        r.step(999)
        assert state(r) == "active"
        assert not r.r(HCI + 0x20) & UIC_COMPLETION
        r.step(1)
        assert state(r) == "active" and r.r(HCI + 0x20) & UIC_COMPLETION
        r.step(999)
        assert state(r) == "active"
        r.step(1)
        assert state(r) == "hibern8"
        passed("pending-uic-blocks-idle-and-completion-restarts-full-delay")

        r.w(AHIT, 100)
        assert r.uic(0x17)[0] == 0  # Convert automatic sleep to explicit sleep.
        assert state(r) == "hibern8"
        r.w(HCI + 0x58, 1)
        assert r.r(HCI + 0x58) == 0 and state(r) == "hibern8"
        assert r.uic(0x18)[0] == 0
        assert io(r, 4) == PATTERN_A
        passed("explicit-hibern8-requires-explicit-exit-even-with-ahit-enabled")

        # Stop the host clock before the idle deadline; restoring it starts
        # a new full interval without losing media or link state.
        r.w(AHIT, 100)
        r.step(30000)
        r.w(GCC + 0x77010, 0)  # AXI/core clock, not the MMIO interface clock.
        r.step(200000)
        assert state(r) == "active"
        r.w(GCC + 0x77010, 1)
        r.step(99999)
        assert state(r) == "active"
        r.step(1)
        assert state(r) == "hibern8"
        assert io(r, 4) == PATTERN_A
        passed("core-clock-gating-cancels-idle-until-clock-restored")

        r.w(AHIT, 100)
        r.w(HCI + 0x50, 0x30000)  # Deliberately unmapped UTRD IOVA.
        r.w(HCI + 0x58, 1)
        r.step(300000)
        assert r.r(HCI + 0x58) == 1 and r.r(CB + 0x58) & 2
        assert state(r) == "active"
        assert r.uic(0x17)[0] != 0
        r.w(HCI + 0x34, 0)
        r.w(CB + 0x58, 0xffffffff)
        r.w(AHIT, 0)
        r.w(HCI + 0x34, 1)
        assert r.uic(0x16)[0] == 0
        r.w(HCI + 0x50, r.dma_address(UTRL))
        r.w(HCI + 0x60, 1)
        lun_ready(r)
        assert io(r, 4) == PATTERN_A
        passed("unfinished-dma-blocks-automatic-and-explicit-sleep-until-reset-recovery")

        r.w(AHIT, 100)
        r.step(30000)
        r, pending = checkpoint("ahit-pending", r)
        assert r.r(AHIT) == 100 and state(r) == "active"
        r.step(69999)
        assert state(r) == "active"
        r.step(1)
        assert state(r) == "hibern8"
        passed("restored-ahit-keeps-original-remaining-deadline")

        phy_off(r)
        r.w(GCC + 0x77010, 0)
        assert state(r) == "hibern8"
        r, sleeping = checkpoint("auto-hibern8-phy-off", r)
        assert state(r) == "hibern8" and r.r(PHY + 0xd80) == 0
        r.w(HCI + 0x58, 1)
        assert r.r(HCI + 0x58) == 0 and state(r) == "hibern8"
        r.w(GCC + 0x77010, 1)
        phy_on(r)
        # No explicit HIBER_EXIT: this real request wakes the restored link.
        assert io(r, 4) == PATTERN_A
        assert host_block(disk, 4) == PATTERN_A
        passed("sleeping-checkpoint-retains-media-and-auto-wakes-after-phy-clock-restore")

        # A device reset must cancel a UIC timer, not let it resurrect the link.
        r.w(AHIT, 0)
        r.w(HCI + 0x20, 0xffffffff)
        r.w(HCI + 0x90, 0x16)
        r.command("set_irq_in /machine/ufs device-reset 0 1")
        r.command("set_irq_in /machine/ufs device-reset 0 0")
        r.step(20000)
        assert state(r) == "down" and r.r(HCI + 0x34) == 0
        assert not r.r(HCI + 0x20) & UIC_COMPLETION
        r.w(HCI + 0x34, 1)
        assert r.uic(0x16)[0] == 0
        r.w(HCI + 0x50, r.dma_address(UTRL))
        r.w(HCI + 0x60, 1)
        lun_ready(r)
        assert io(r, 4) == PATTERN_A
        passed("device-reset-cancels-uic-and-recovery-keeps-media")
        close()

        raw = pending.read_bytes()
        offset = subsection(raw)
        assert raw[offset] == 0
        assert struct.unpack_from(">q", raw, offset + 1)[0] >= 0
        invalid = bytearray(raw)
        invalid[offset] = 1  # Cannot be automatic-Hibern8 while link is active.
        bad = args.out / "bad-auto-state.migration"
        bad.write_bytes(invalid)
        rejected("reject-auto-state", bad, "inconsistent Auto-Hibern8 state")
        invalid = bytearray(raw)
        struct.pack_into(">q", invalid, offset + 1, -1)
        bad = args.out / "bad-auto-timer.migration"
        bad.write_bytes(invalid)
        rejected("reject-auto-timer", bad, "inconsistent Auto-Hibern8 state")

        r = start("legacy-profile", extra_args=(
            "-global", "qcom-sm8250-ufs.auto-hibern8=off"))
        setup(r)
        legacy_cap = r.r(HCI)
        assert legacy_cap == cap & ~AUTOH8
        r.w(AHIT, 100)
        assert r.r(AHIT) == 0
        assert io(r, 4) == PATTERN_A
        r.step(1000000)
        assert state(r) == "active"
        passed("legacy-capability-profile-remains-explicit-hibern8-only")
        close()
        rejected("reject-auto-capability-profile", pending,
                 f"{legacy_cap:x} != {cap:x}", extra_args=(
                     "-global", "qcom-sm8250-ufs.auto-hibern8=off"))
        assert len(migrations) == 2 and len(rejections) == 3
        assert len(checks) == 18
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
