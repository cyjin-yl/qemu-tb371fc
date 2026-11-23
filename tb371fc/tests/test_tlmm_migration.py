#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Transfer actual TLMM pending IRQs and output wires between QEMU processes."""
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

PIN0, PIN4 = 0x0f500000, 0x0f904000
PIN133, PIN179 = 0x0f185000, 0x0f1b3000
UFS, SDC2 = 0x0f5b8000, 0x0f9b7000
RISING, HIGH = 0x77, 0x73

class TLMMRig(Rig):
    """Reuse the QMP/QTest transport, selecting a named TLMM output group."""
    def __init__(self, args, directory, incoming=False, group="sysbus-irq"):
        self.output_group = group
        super().__init__(args, str(directory), incoming=incoming)

    def command(self, command):
        original = f"irq_intercept_out {self.args.qom_path} sysbus-irq"
        if command == original:
            command = f"irq_intercept_out {self.args.qom_path} {self.output_group}"
        return super().command(command)

    def read_at(self, addr):
        return int(self.command(f"readl {addr:#x}")[0], 0)

    def write_at(self, addr, value):
        self.command(f"writel {addr:#x} {value:#x}")

    def input(self, pin, level):
        self.command(f"set_irq_in {self.args.qom_path} gpio-in {pin} {int(level)}")

    def level(self, pin, expected):
        self.read_at(PIN0 + 12)  # Drain pending asynchronous IRQ events.
        actual = self.irq.get(pin, False)
        assert actual == expected, (self.output_group, pin, actual, expected)

def hash_file(path):
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
        parser.error("Use a fresh output directory to preserve evidence.")
    args.base, args.qom_path = PIN0, "/machine/tlmm"
    args.extra_qemu_args = ["-smp", "8"]
    here = Path(__file__).resolve().parent
    checks = []
    report = {
        "schema_version": 1, "kind": "tb371fc-tlmm-process-migration",
        "started_utc": utc(), "all_passed": False, "checks": checks,
        "machine": args.machine, "ram_per_process": "128 MiB",
        "sha256": {
            "qemu": hash_file(args.qemu), "test": hash_file(__file__),
            "transport": hash_file(here / "test_geni_uart.py"),
            "migration_helper": hash_file(here / "test_geni_migration.py"),
        },
        "scope": "Real QMP process-to-process migration and QTest GPIO/IRQ continuation.",
        "limits": [
            "Virtual functional migration, not native electrical or timing equivalence.",
            "Separate migrations observe summary IRQ, output latch, output enable, and dedicated UFS reset.",
            "No tablet access, firmware, native register write, or guest execution."
        ],
        "migrations": [],
    }
    start = time.monotonic()

    def passed(name, observed=None):
        item = {"name": name, "passed": True}
        if observed is not None:
            item["observed"] = observed
        checks.append(item)

    def migrate(source, target, directory, name):
        source.monitor("stop")
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sr, tr = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert not target.monitor("query-status")["running"]
        report["migrations"].append({"output_group": name, "source": sr,
                                     "destination": tr})
        passed(name + "-actual-migration-completed")

    try:
        for group in ("sysbus-irq", "gpio-out", "gpio-oe", "ufs-reset"):
            directory = args.out / group
            sd, td = directory / "source", directory / "target"
            sd.mkdir(parents=True)
            td.mkdir()
            source = target = None
            try:
                source = TLMMRig(args, sd, group=group)
                target = TLMMRig(args, td, incoming=True, group=group)
                report.setdefault("qemu_version", source.monitor("query-version"))
                if group == "sysbus-irq":
                    source.write_at(PIN0, 0x243)
                    source.write_at(PIN0 + 4, 2)
                    source.write_at(PIN4 + 8, RISING)
                    source.write_at(PIN133 + 8, RISING & ~1)
                    source.write_at(PIN179 + 8, HIGH & ~1)
                    for pin in (4, 133, 179):
                        source.input(pin, True)
                    source.write_at(UFS, 0x12)
                    source.write_at(UFS + 4, 1)
                    source.write_at(SDC2, 0x188)
                    source.write_at(0x80010000, 0x544c4d4d)
                    addresses = [p + o for p in (PIN0, PIN4, PIN133, PIN179)
                                 for o in (0, 4, 8, 12)] + [UFS, UFS + 4, SDC2]
                    before = {hex(a): source.read_at(a) for a in addresses}
                    assert before[hex(PIN4 + 12)] == 1
                    assert before[hex(PIN133 + 12)] == 1
                    assert before[hex(PIN179 + 12)] == 1
                    source.level(0, True)
                    passed("source-output-masked-edge-active-level-and-pending-irq")
                    migrate(source, target, directory, group)
                    after = {hex(a): target.read_at(a) for a in addresses}
                    assert before == after, {"before": before, "after": after}
                    assert target.read_at(0x80010000) == 0x544c4d4d
                    target.level(0, True)
                    passed("all-registers-input-levels-ram-and-summary-restored",
                           {"register_count": len(addresses), "registers": after})
                    target.write_at(PIN4 + 8, RISING & ~1)
                    target.level(0, False)
                    assert target.read_at(PIN4 + 12) == 1
                    target.write_at(PIN133 + 8, RISING)
                    target.level(0, True)
                    target.write_at(PIN133 + 12, 1)
                    target.level(0, True)
                    target.write_at(PIN133 + 12, 0)
                    target.level(0, False)
                    passed("masked-pending-edge-unmasks-and-write-zero-ack")
                    target.input(133, False)
                    target.level(0, False)
                    target.input(133, True)
                    target.level(0, True)
                    target.write_at(PIN133 + 12, 0)
                    target.level(0, False)
                    passed("destination-detects-next-edge-without-replaying-old-edge")
                    target.write_at(PIN179 + 8, HIGH)
                    target.level(0, True)
                    target.write_at(PIN179 + 12, 0)
                    target.level(0, True)
                    target.write_at(PIN179 + 8, HIGH & ~0x11)
                    target.write_at(PIN179 + 12, 0)
                    target.level(0, False)
                    target.input(179, False)
                    target.write_at(PIN179 + 8, HIGH)
                    target.level(0, False)
                    passed("migrated-active-level-relatches-until-raw-masked")
                elif group == "gpio-out":
                    source.write_at(PIN0 + 4, 2)
                    source.write_at(PIN4 + 4, 2)
                    source.level(0, True)
                    source.level(4, True)
                    migrate(source, target, directory, group)
                    target.level(0, True)
                    target.level(4, True)
                    target.level(179, False)
                    passed("output-latch-wires-restored")
                    target.write_at(PIN0 + 4, 0)
                    target.level(0, False)
                    target.level(4, True)
                    passed("destination-output-wires-change-independently")
                elif group == "gpio-oe":
                    source.write_at(PIN0, 0x200)
                    source.write_at(PIN4, 0x204)
                    source.write_at(PIN179, 0x200)
                    source.level(0, True)
                    source.level(4, False)
                    source.level(179, True)
                    migrate(source, target, directory, group)
                    target.level(0, True)
                    target.level(4, False)
                    target.level(179, True)
                    passed("gpio-mode-and-enable-wires-restored")
                    target.write_at(PIN0, 0x204)
                    target.level(0, False)
                    target.level(179, True)
                    passed("destination-alternate-mux-disables-gpio-drive")
                else:
                    source.write_at(UFS + 4, 1)
                    source.level(0, True)
                    migrate(source, target, directory, group)
                    target.level(0, True)
                    assert target.read_at(UFS + 4) == 1
                    passed("dedicated-reset-output-wire-restored")
                    target.write_at(UFS + 4, 0)
                    target.level(0, False)
                    passed("destination-dedicated-reset-output-changes")
            finally:
                if source:
                    source.close()
                if target:
                    target.close()
        report["all_passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report["elapsed_seconds"] = round(time.monotonic() - start, 6)
    report["completed_utc"] = utc()
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
