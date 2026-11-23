#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Migrate actual PDC state, real GIC pending IRQs, and separate output wires."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback
from datetime import datetime, timezone

from test_geni_uart import Rig
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit("Acceptance checks require assertions; do not use -O.")

PDC, SPI_CONFIG, GICD = 0x0b220000, 0x17c000f0, 0x17a00000

def cfg(pin):
    return PDC + 0x110 + 4 * pin

def bank(pin):
    return PDC + 0x10 + 4 * (pin // 32)

def gic_id(pin):
    if pin <= 93:
        return 32 + 480 + pin
    if pin <= 124:
        return 32 + 609 + pin - 94
    if pin == 125:
        return 32 + 63
    assert pin <= 137
    return 32 + 716 + pin - 126

class PDCRig(Rig):
    def __init__(self, args, directory, incoming=False, intercept=False):
        self.use_intercept = intercept
        super().__init__(args, str(directory), incoming=incoming)

    def command(self, command):
        if (not self.use_intercept and
                command == f"irq_intercept_out {self.args.qom_path} sysbus-irq"):
            # QTest interception disconnects the parent. Keep the real GIC
            # wiring intact in the first migration; observe wires separately.
            return []
        return super().command(command)

    def get(self, addr):
        return int(self.command(f"readl {addr:#x}")[0], 0)

    def put(self, addr, value):
        self.command(f"writel {addr:#x} {value:#x}")

    def input(self, pin, level):
        self.command(f"set_irq_in {self.args.qom_path} irq-in {pin} {int(level)}")

    def enable(self, pin, on):
        old, bit = self.get(bank(pin)), 1 << (pin % 32)
        self.put(bank(pin), old | bit if on else old & ~bit)

    def level(self, pin, expected):
        assert self.use_intercept
        self.get(bank(pin))
        assert self.irq.get(pin, False) == expected, (pin, self.irq, expected)

    def parent_config(self, pin, edge):
        ident = gic_id(pin)
        reg, shift = GICD + 0xc00 + 4 * (ident // 16), (ident % 16) * 2
        value = 2 << shift if edge else 0
        self.put(reg, (self.get(reg) & ~(3 << shift)) | value)
        assert self.get(reg) & (3 << shift) == value
        self.put(GICD + 0x100 + 4 * (ident // 32), 1 << (ident % 32))
        self.put(GICD, self.get(GICD) | 3)

    def pending(self, pin):
        assert not self.use_intercept
        ident = gic_id(pin)
        return bool(self.get(GICD + 0x200 + 4 * (ident // 32)) &
                    (1 << (ident % 32)))

    def clear(self, pin):
        ident = gic_id(pin)
        self.put(GICD + 0x280 + 4 * (ident // 32), 1 << (ident % 32))

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def utc():
    return datetime.now(timezone.utc).isoformat()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--machine", default="tb371fc,ufs-enabled=off")
    args = parser.parse_args()
    args.qemu = str(args.qemu.resolve())
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    if (args.out / "result.json").exists():
        parser.error("Use a fresh output directory.")
    args.base, args.qom_path = PDC, "/machine/pdc"
    args.extra_qemu_args = [
        "-smp", "8", "-global", "qcom-pdc.reference-version=0x20000"]
    here = Path(__file__).resolve().parent
    checks = []
    report = {
        "schema_version": 1, "kind": "tb371fc-pdc-process-migration",
        "started_utc": utc(), "all_passed": False, "checks": checks,
        "machine": args.machine, "ram_per_process": "128 MiB",
        "explicit_reference_version": "0x20000 (not a native version read)",
        "sha256": {"qemu": sha(args.qemu), "test": sha(__file__),
                   "transport": sha(here / "test_geni_uart.py"),
                   "migration_helper": sha(here / "test_geni_migration.py")},
        "migrations": [],
        "scope": "Two actual QMP transfers: real GIC connection, then separate level-wire interception.",
        "limits": [
            "No hardware electrical timing, power-domain wake or firmware execution equivalence.",
            "Native PDC version remains unknown; this test explicitly selects a legacy reference profile.",
            "GPIO interception disconnects GIC; the real-GIC migration never installs interception."
        ],
    }
    start = time.monotonic()

    def passed(name, observed=None):
        item = {"name": name, "passed": True}
        if observed is not None:
            item["observed"] = observed
        checks.append(item)

    def migrate(source, target, directory, mode):
        source.monitor("stop")
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sr, tr = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert not target.monitor("query-status")["running"]
        report["migrations"].append({
            "mode": mode, "source": sr, "destination": tr})
        passed(mode + "-actual-migration-completed")

    try:
        for mode in ("real-gic", "level-wires"):
            directory = args.out / mode
            sd, td = directory / "source", directory / "target"
            sd.mkdir(parents=True)
            td.mkdir()
            source = target = None
            try:
                intercept = mode == "level-wires"
                source = PDCRig(args, sd, intercept=intercept)
                target = PDCRig(args, td, incoming=True, intercept=intercept)
                report.setdefault("qemu_version", source.monitor("query-version"))
                if mode == "real-gic":
                    for pin, edge in ((1, False), (125, True), (94, True),
                                      (137, True)):
                        source.parent_config(pin, edge)
                        source.put(cfg(pin), 6 if edge else 4)
                    source.enable(1, True)
                    source.enable(125, True)
                    source.enable(137, True)
                    for pin in (1, 125, 94, 137):
                        source.input(pin, True)
                    # An enabled/high edge input with pending already cleared
                    # exposes a spurious post-load pulse that an already-pending
                    # edge alone would hide.
                    source.clear(137)
                    assert not source.pending(137)
                    source.put(SPI_CONFIG + 0x3c, 2)
                    source.put(SPI_CONFIG + 4, 0x80000000)
                    source.put(0x80010000, 0x50444331)
                    assert source.pending(1)
                    assert source.pending(125)
                    assert not source.pending(94)
                    addresses = [cfg(p) for p in (1, 94, 125, 137)]
                    addresses += [bank(p) for p in (1, 94, 125, 137)]
                    addresses += [PDC + 0x1000, SPI_CONFIG + 0x3c,
                                  SPI_CONFIG + 4]
                    before = {hex(a): source.get(a) for a in addresses}
                    passed("source-real-gic-level-edge-and-masked-input")
                    migrate(source, target, directory, mode)
                    after = {hex(a): target.get(a) for a in addresses}
                    assert before == after, {"before": before, "after": after}
                    assert target.get(0x80010000) == 0x50444331
                    assert target.pending(1)
                    assert target.pending(125)
                    assert not target.pending(94)
                    passed("registers-ram-and-real-gic-pending-restored", after)
                    assert not target.pending(137)
                    target.input(137, True)
                    assert not target.pending(137)
                    passed("cleared-edge-not-replayed-on-load")
                    target.clear(125)
                    assert not target.pending(125)
                    assert target.pending(1)
                    passed("gic-ack-does-not-replay-migrated-edge")
                    target.input(125, False)
                    assert not target.pending(125)
                    target.input(125, True)
                    assert target.pending(125)
                    target.clear(125)
                    target.enable(94, True)
                    assert not target.pending(94)
                    target.input(94, False)
                    target.input(94, True)
                    assert target.pending(94)
                    passed("destination-new-edges-and-masked-history")
                    target.enable(1, False)
                    target.clear(1)
                    assert not target.pending(1)
                    target.enable(1, True)
                    assert target.pending(1)
                    target.input(1, False)
                    target.clear(1)
                    assert not target.pending(1)
                    passed("migrated-level-mask-unmask-and-release-reaches-gic")
                else:
                    source.put(cfg(1), 4)
                    source.put(cfg(125), 0)
                    source.put(cfg(137), 4)
                    source.input(1, True)
                    for pin in (1, 125, 137):
                        source.enable(pin, True)
                    source.level(1, True)
                    source.level(125, True)
                    source.level(137, False)
                    migrate(source, target, directory, mode)
                    target.level(1, True)
                    target.level(125, True)
                    target.level(137, False)
                    passed("active-high-and-inverted-low-output-wires-restored")
                    target.input(125, True)
                    target.level(125, False)
                    target.enable(1, False)
                    target.level(1, False)
                    target.input(137, True)
                    target.level(137, True)
                    passed("destination-output-wires-continue-independently")
            finally:
                if source:
                    source.close()
                if target:
                    target.close()
        assert len(checks) == 10, len(checks)
        report["all_passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report["completed_utc"] = utc()
    report["elapsed_seconds"] = round(time.monotonic() - start, 6)
    if not report["all_passed"]:
        report["stderr_tails"] = {
            str(p.relative_to(args.out)): p.read_text(errors="replace")[-4096:]
            for p in args.out.rglob("qemu.stderr")
        }
    (args.out / "result.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"all_passed": report["all_passed"], "checks": len(checks),
                      "result": str(args.out / "result.json")}))
    return 0 if report["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
