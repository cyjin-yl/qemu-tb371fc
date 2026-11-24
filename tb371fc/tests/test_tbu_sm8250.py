#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise opt-in TBU reference HALT/ECATS using real QEMU processes.

No native MMIO, physical DMA stall, complete TBU topology or clock-gate claim.
The QTest-only e0/4e0 DMA aliases use the actual SMMUv2 page-table walker.
"""
import argparse
from copy import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration
from test_smmuv2_migration import (APPS, CTRL, FSR_MASK, PTE_RO, PTE_RW,
                                    SCTRL, cb, configure_bank)

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")

TCU = 0x15182000
BASE = (0x15185000, 0x15189000)
LOCK = (0x15182200, 0x15182208)
DMA = (0x52000000, 0x53000000)
TABLE = (0x82000000, 0x82010000)
PA = (0x81000000, 0x81002000)
SID = (0xe0, 0x4e0)
PATTERN = (0x12345678, 0xabcddcba)
IOVA, HALT, AXUSER, LATENCY = 0x1034, 1 << 16, 255 << 36, 1000
READ, WRITE, USER_READ = 0xfd, 0xff, 0xbd


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class TbuRig(Rig):
    def __init__(self, args, directory, incoming=False):
        directory.mkdir(parents=True, exist_ok=False)
        super().__init__(args, str(directory), incoming=incoming)
        self.clock_ns = 0
        if not incoming:
            # QTest executes no CPU instructions; cont enables virtual timers.
            self.monitor("cont")

    def command(self, command):
        if command.startswith("irq_intercept_out "):
            return []
        return super().command(command)

    def get(self, address, width="l"):
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def put(self, address, value, width="l"):
        self.command(f"write{width} {address:#x} {value:#x}")

    def step(self, ns):
        self.clock_ns = int(self.command(f"clock_step {ns}")[0], 0)

    def status(self, bank):
        return self.get(BASE[bank] + 0x20)

    def halt(self, bank):
        self.put(BASE[bank], HALT | (SID[bank] & 0x3ff), "q")

    def acquire(self, bank):
        assert self.status(bank) == 2
        assert self.get(LOCK[bank]) == 1
        assert self.get(LOCK[bank]) == 0

    def trigger(self, bank, request=READ, va=IOVA):
        self.put(BASE[bank] + 0x18, 0)
        self.put(BASE[bank] + 8, va, "q")
        self.put(BASE[bank] + 0x30, AXUSER, "q")
        self.put(BASE[bank] + 0x18, request)

    def query(self, bank, request=READ, va=IOVA):
        self.trigger(bank, request, va)
        assert self.status(bank) == 3
        assert self.get(BASE[bank] + 0x28, "q") == 1
        self.step(LATENCY - 1)
        assert self.status(bank) == 3
        self.step(1)
        assert self.status(bank) == 2
        return self.get(BASE[bank] + 0x28, "q")

    def release(self, bank):
        self.put(BASE[bank] + 0x18, 0)
        self.put(LOCK[bank], 0)
        self.put(BASE[bank], 0, "q")
        assert self.status(bank) == 0
        assert self.get(LOCK[bank]) == 0

    def gated(self, bank):
        before = self.get(PA[bank] + (IOVA & 0xfff))
        self.put(DMA[bank] + IOVA, 0xdeadbeef)
        assert self.get(PA[bank] + (IOVA & 0xfff)) == before
        assert self.get(DMA[bank] + IOVA) == 0
        assert self.get(cb(APPS, bank) + 0x58) & FSR_MASK == 0


def setup(r):
    for i in range(2):
        table = TABLE[i]
        r.put(table, (table + 0x1000) | 3, "q")
        r.put(table + 0x1000, (table + 0x2000) | 3, "q")
        r.put(table + 0x2008, PA[i] | PTE_RW, "q")
        r.put(PA[i] + (IOVA & 0xfff), PATTERN[i])
        configure_bank(r, APPS, i, table)
        r.put(cb(APPS, i) + 0x58, FSR_MASK)
        r.put(APPS + 0x800 + i * 4, 0x80000000 | SID[i])
        r.put(APPS + 0xc00 + i * 4, i)
    r.put(APPS + 0x48, 0xffffffff)
    r.put(APPS, CTRL)
    assert [r.get(DMA[i] + IOVA) for i in range(2)] == list(PATTERN)


def checkpoint(r, path):
    r.monitor("stop")
    r.monitor("migrate", {"uri": "file:" + str(path)})
    receipt = wait_migration(r)
    assert r.monitor("query-status")["status"] == "postmigrate"
    assert path.stat().st_size > 0
    return receipt


def section_body(raw):
    """Locate one verified v1 TBU VMState section, including its footer."""
    name = b"qcom-sm8250-tbu-debug"
    needle = name + struct.pack(">II", 0, 1)
    matches = []
    cursor = 0
    while (at := raw.find(needle, cursor)) >= 0:
        end = at + len(name)
        for start in range(max(0, at - 255), at + 1):
            length = end - start
            if (0 < length <= 255 and start >= 6 and
                    raw[start - 1] == length and raw[start - 6] == 4 and
                    b"\0" not in raw[start:end]):
                body = end + 8
                # Three equality fields (4+4+8); two banks of 5Q+I+2B+q.
                tail = body + 16 + 2 * 54
                ident = raw[start - 5:start - 1]
                assert raw[tail:tail + 5] == b"\x7e" + ident
                matches.append(body)
        cursor = at + len(needle)
    assert len(matches) == 1, ("unique TBU VMState section", matches)
    return matches[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.qemu, args.out = str(args.qemu.resolve()), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    args.machine = "tb371fc,ufs-enabled=off,tbu-profile=reference-ecats"
    args.qom_path, args.base = "/machine/tbu-debug", BASE[0]
    args.extra_qemu_args = ["-smp", "8", "-d", "guest_errors"]
    checks, transfers, rejections = [], [], []
    report = {
        "schema_version": 1, "kind": "sm8250-tbu-reference-qtest",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": False, "checks": checks, "migrations": transfers,
        "rejections": rejections, "machine": args.machine,
        "sha256": {"qemu": sha(args.qemu), "test": sha(__file__)},
        "scope": "Reference HALT admission, ECATS with real stage-1 walks, and actual process checkpoints.",
        "limits": ["HALT returns DMA transaction failure, not hardware stall or drain.",
                   "UFS disabled; two QTest DMA aliases are synthetic clients.",
                   "No native identity, timing, clock gate, or complete TBU topology.",
                   "Default profile stays architectural SMMUv2; reference DT binding is not advertised."],
    }
    live = None

    def passed(name, observed=None):
        checks.append({"name": name, "passed": True, "observed": observed})

    def start(name, *, config=None, incoming=False):
        nonlocal live
        assert live is None
        live = TbuRig(config or args, args.out / name, incoming=incoming)
        return live

    def close():
        nonlocal live
        if live:
            live.close()
            live = None

    def reject(name, path, diagnostic, config=None):
        r = start(name, config=config, incoming=True)
        try:
            r.monitor("migrate-incoming", {"uri": "file:" + str(path)})
        except (OSError, AssertionError, RuntimeError):
            pass
        assert r.process.wait(timeout=15) == 1
        error = r.log_path.read_text(errors="replace")
        assert diagnostic in error, (diagnostic, error)
        assert "qcom-sm8250-tbu-debug" in error, error
        assert "error while loading state" in error, error
        for _ in range(1024):
            raw = r.qmpfile.readline()
            if not raw:
                break
            message = json.loads(raw)
            if "event" in message:
                r.events.append(message)
        else:
            raise AssertionError("QMP did not reach EOF after rejection")
        assert not any(e.get("event") == "RESUME" for e in r.events)
        rejections.append({"name": name, "diagnostic": diagnostic,
                           "exit": r.process.returncode, "events": r.events,
                           "stream_sha256": sha(path)})
        passed(name + "-rejected-before-resume")
        close()

    started = time.monotonic()
    try:
        off = copy(args)
        off.machine = "tb371fc,ufs-enabled=off"
        r = start("default-off", config=off)
        assert r.monitor("qom-get", {"path": "/machine",
                                     "property": "tbu-profile"}) == "off"
        tree = r.monitor("human-monitor-command", {"command-line": "info mtree"})
        assert "sm8250-anoc" not in tree and "sm8250-tcu-reference" not in tree
        assert "qtest-tbu" not in tree
        off_state = args.out / "off.migration"
        checkpoint(r, off_state)
        passed("default-profile-maps-no-reference-windows")
        close()

        r = start("functional")
        assert r.get(TCU + 0x18) == 0x02000000
        r.put(TCU + 0x18, 0)
        assert r.get(TCU + 0x18) == 0x02000000
        setup(r)
        passed("reference-version-is-readonly-and-both-SIDs-translate")
        for i in range(2):
            r.halt(i)
            assert r.status(i) == 0 and r.get(LOCK[i]) == 0
            r.gated(i)
            assert r.get(DMA[1 - i] + IOVA) == PATTERN[1 - i]
            r.trigger(i)
            assert r.get(BASE[i] + 0x18) == 0  # No ACK or lock yet.
            r.step(LATENCY - 1)
            assert r.status(i) == 0
            r.step(1)
            r.acquire(i)
            assert r.query(i) == (PA[i] + (IOVA & 0xfff)) << 12
            r.release(i)
            assert r.get(DMA[i] + IOVA) == PATTERN[i]
        passed("both-SIDs-delayed-ACK-gate-isolation-real-ECATS-and-reopen")

        r.halt(0)
        r.step(LATENCY)
        r.acquire(0)
        # Read-only and privilege checks use the real SMMUv2 fault registers.
        r.put(TABLE[0] + 0x2008, PA[0] | PTE_RO, "q")
        assert r.query(0, WRITE) == 1
        assert r.get(cb(APPS, 0) + 0x58) & FSR_MASK == 8
        r.put(cb(APPS, 0) + 0x58, FSR_MASK)
        assert r.query(0) == (PA[0] + (IOVA & 0xfff)) << 12
        r.put(TABLE[0] + 0x2008, PA[0] | 0x403, "q")
        assert r.query(0, USER_READ) == 1
        assert r.get(cb(APPS, 0) + 0x58) & FSR_MASK == 8
        r.put(cb(APPS, 0) + 0x58, FSR_MASK)
        assert r.query(0) == (PA[0] + (IOVA & 0xfff)) << 12
        r.put(TABLE[0] + 0x2008, 0, "q")
        assert r.query(0) == 1
        assert r.get(cb(APPS, 0) + 0x58) & FSR_MASK == 2
        r.put(cb(APPS, 0) + 0x58, FSR_MASK)
        r.put(TABLE[0] + 0x2008, PA[0] | PTE_RW, "q")
        passed("ECATS-write-user-and-unmapped-page-faults-are-not-success")

        r.put(APPS + 0xc00, 1 << 16)  # Stream bypass.
        assert r.query(0) == 1
        r.put(APPS + 0xc00, 0)
        r.put(cb(APPS, 0), SCTRL & ~1)  # Context translation disabled.
        assert r.query(0) == 1
        r.put(cb(APPS, 0), SCTRL)
        r.put(APPS, CTRL | 1)  # CLIENTPD.
        assert r.query(0) == 1
        r.put(APPS, CTRL & ~0x400)  # USFCFG=0: unmatched stream bypass.
        r.put(APPS + 0x800, 0)
        assert r.query(0) == 1
        r.put(APPS + 0x800, 0x800000e0)
        r.put(APPS, CTRL)
        assert r.query(0) == (PA[0] + (IOVA & 0xfff)) << 12
        passed("architectural-bypass-and-disabled-context-cannot-fake-ECATS")

        r.put(BASE[0] + 0x18, 0)
        for value in (0xdd, 0x1fd, 0xc1, 0x2fd):
            r.put(BASE[0] + 0x18, value)
            assert r.get(BASE[0] + 0x18) == 0 and r.status(0) == 2
        r.put(BASE[0] + 8, 1 << 36, "q")
        assert r.get(BASE[0] + 8, "q") == IOVA
        r.put(BASE[0] + 0x30, AXUSER | 1, "q")
        assert r.get(BASE[0] + 0x30, "q") == AXUSER
        r.put(BASE[0] + 8, 0, "b")
        r.put(BASE[0] + 9, 0, "l")
        assert r.get(BASE[0] + 8, "q") == IOVA
        r.trigger(0)
        r.step(400)
        r.put(BASE[0] + 0x18, 0)
        r.step(1000)
        assert r.status(0) == 2 and r.get(BASE[0] + 0x28, "q") == 1
        r.trigger(0)
        r.step(200)
        r.put(BASE[0], 0, "q")
        r.step(1000)
        assert r.status(0) == 0 and r.get(BASE[0] + 0x28, "q") == 1
        assert r.get(DMA[0] + IOVA) == PATTERN[0]
        passed("unsupported-MMIO-and-request-encodings-do-not-acknowledge")
        passed("trigger-cancel-and-HALT-release-cancel-pending-query")

        r.halt(0)
        r.step(100)
        r.monitor("system_reset")
        r.wait_for(lambda: r.status(0) == r.status(1) == 0, "TBU reset")
        setup(r)
        r.step(2000)
        assert r.status(0) == r.status(1) == 0
        assert [r.get(DMA[i] + IOVA) for i in range(2)] == list(PATTERN)
        passed("reset-clears-gates-and-cancels-pending-ACK")
        close()

        r = start("checkpoint-source")
        setup(r)
        r.halt(0)
        r.step(LATENCY)
        r.acquire(0)
        r.trigger(0)
        r.step(250)
        r.halt(1)
        r.step(100)
        assert r.status(0) == 3 and r.status(1) == 0
        state = args.out / "pending.migration"
        sent = checkpoint(r, state)
        close()
        r = start("checkpoint-target", incoming=True)
        # Different QTest clock origin: the saved remaining durations must win.
        r.step(50000)
        assert r.clock_ns == 50000
        assert not r.monitor("query-status")["running"]
        receiver_origin = r.clock_ns
        r.monitor("migrate-incoming", {"uri": "file:" + str(state)})
        received = wait_migration(r)
        assert not r.monitor("query-status")["running"]
        assert r.status(0) == 3 and r.status(1) == 0
        r.gated(0)
        r.gated(1)
        assert r.get(LOCK[0]) == 0
        passed("process-load-restores-both-gates-and-lock-before-cont")
        r.monitor("cont")
        r.step(649)
        assert r.status(0) == 3 and r.status(1) == 0
        r.step(1)
        assert r.status(0) == 2 and r.status(1) == 0
        assert r.get(BASE[0] + 0x28, "q") == (PA[0] + (IOVA & 0xfff)) << 12
        r.step(249)
        assert r.status(1) == 0
        r.step(1)
        assert r.status(1) == 2
        r.acquire(1)
        assert r.query(1) == (PA[1] + (IOVA & 0xfff)) << 12
        r.release(0)
        assert r.get(DMA[0] + IOVA) == PATTERN[0]
        r.gated(1)
        r.release(1)
        assert r.get(DMA[1] + IOVA) == PATTERN[1]
        transfers.append({"source": sent, "destination": received,
                          "stream_sha256": sha(state), "stream_bytes": state.stat().st_size,
                          "source_remaining_ns": [650, 900],
                          "target_clock_origin_ns": receiver_origin})
        passed("actual-process-checkpoint-rebases-pending-query-and-HALT")
        close()

        reject("profile-on-to-off", state, "0 != 1", off)
        reject("profile-off-to-on", off_state, "1 != 0")
        for prop, value, original in (("reference-version", 0x02000001, 0x02000000),
                                      ("operation-latency-ns", 2000, LATENCY)):
            config = copy(args)
            config.extra_qemu_args = [*args.extra_qemu_args, "-global",
                                     f"qcom-sm8250-tbu-debug.{prop}={value}"]
            reject("mismatched-" + prop, state,
                   f"{value:x} != {original:x}", config)

        raw = state.read_bytes()
        body = section_body(raw)
        assert struct.unpack_from(">IIQ", raw, body) == (1, 0x02000000, LATENCY)
        b0, b1 = body + 16, body + 16 + 54
        assert struct.unpack_from(">QQQQQIBBq", raw, b0) == (
            HALT | 0xe0, IOVA, READ, 1, AXUSER, 2, 1, 1, 650)
        assert struct.unpack_from(">QQQQQIBBq", raw, b1) == (
            HALT | 0xe0, 0, 0, 1, 0, 1, 0, 0, 900)
        invalid = bytearray(raw)
        # Query must expose PAR=fault until the timer executes the walker.
        struct.pack_into(">Q", invalid, b0 + 24, PA[0] << 12)
        bad = args.out / "invalid-query-PAR.migration"
        bad.write_bytes(invalid)
        reject("invalid-query-PAR", bad, "invalid incoming bank 0")
        invalid = bytearray(raw)
        # Halted idle without ACK has no timer capable of opening this state.
        struct.pack_into(">I", invalid, b1 + 40, 0)
        struct.pack_into(">q", invalid, b1 + 46, -1)
        bad = args.out / "invalid-idle-HALT.migration"
        bad.write_bytes(invalid)
        reject("invalid-idle-HALT", bad, "invalid incoming bank 1")
        assert len(checks) == 16 and len(transfers) == 1 and len(rejections) == 6
        assert sha(args.qemu) == report["sha256"]["qemu"]
        report["all_passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        close()
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    if not report["all_passed"]:
        report["stderr_tails"] = {
            str(p.relative_to(args.out)): p.read_text(errors="replace")[-4096:]
            for p in args.out.rglob("qemu.stderr")}
    result = args.out / "result.json"
    result.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"all_passed": report["all_passed"], "checks": len(checks),
                      "result": str(result)}))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
