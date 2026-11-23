#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify actual SPMI -> PDC input 1 -> GIC SPI 481 routing and migration."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_spmi_pmic_arb import (
    SpmiRig, CORE, STATUS, IRQ_STATUS, TEST_APID, DONE,
)
from test_geni_migration import wait_migration

PDC = 0x0b220000
GICD = 0x17a00000
SPI = 481
INTID = SPI + 32
PDC_ENABLE = PDC + 0x10
PDC_CONFIG_PIN1 = PDC + 0x110 + 4
MARKER_ADDRESS = 0x80001000
MARKER = 0x53504d4950444331
EXPECTED_CHECKS = 8

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for data in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


class RouteRig(SpmiRig):
    """Keep the board's real SysBus connection instead of intercepting it."""
    def __init__(self, args, directory, incoming=False):
        self.suppressed_interceptions = 0
        super().__init__(args, directory, incoming=incoming)

    def command(self, line):
        if line.startswith("irq_intercept_out "):
            self.suppressed_interceptions += 1
            return []
        return super().command(line)

    def pending(self, spi=SPI):
        intid = spi + 32
        return bool(self.get(GICD + 0x200 + 4 * (intid // 32)) &
                    (1 << (intid % 32)))

    def prepare_parent(self):
        # ICFGR[INTID] bit 1 = 0 selects a level-sensitive parent SPI.
        cfg = GICD + 0xc00 + 4 * (INTID // 16)
        edge_bit = 1 << (2 * (INTID % 16) + 1)
        self.put(cfg, self.get(cfg) & ~edge_bit)
        assert not (self.get(cfg) & edge_bit)
        self.put(GICD + 0x280 + 4 * (INTID // 32), 1 << (INTID % 32))
        self.put(GICD + 0x100 + 4 * (INTID // 32), 1 << (INTID % 32))
        self.put(GICD, self.get(GICD) | 3)
        # Native PDC pin 1 is level-high and routes to SPI 481.
        self.put(PDC_CONFIG_PIN1, 4)
        self.put(PDC_ENABLE, self.get(PDC_ENABLE) | 2)
        assert self.get(PDC_CONFIG_PIN1) == 4
        assert self.get(PDC_ENABLE) & 2
        assert not self.pending()

    def assert_route(self, active):
        assert self.pending() is active, {
            "expected": active,
            "gic_pending_spi_481": self.pending(),
            "spmi_irq_status": self.get(self.bank(TEST_APID, False) + IRQ_STATUS),
            "pdc_enable": self.get(PDC_ENABLE),
        }
        assert not self.pending(SPI - 1)
        assert not self.pending(SPI + 1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = str(args.qemu.resolve())
    args.base, args.qom_path, args.ee = CORE, "/machine/spmi", 0
    args.machine = "tb371fc,ufs-enabled=off,spmi-profile=reference"
    args.extra_qemu_args = [
        "-global", "qcom-spmi-pmic-arb.test-peripheral=on",
        "-d", "unimp,guest_errors",
    ]
    args.out.mkdir(parents=True, exist_ok=False)
    source_dir, target_dir = args.out / "source", args.out / "destination"
    source_dir.mkdir()
    target_dir.mkdir()
    here = Path(__file__).resolve().parent
    report = {
        "schema_version": 1,
        "kind": "tb371fc-spmi-pdc-real-gic-route",
        "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "expected_checks": EXPECTED_CHECKS,
        "qemu_sha256": sha256(args.qemu),
        "test_sha256": sha256(__file__),
        "helper_sha256": {
            n: sha256(here / n) for n in (
                "test_spmi_pmic_arb.py", "test_geni_uart.py",
                "test_geni_migration.py")
        },
        "machine": args.machine,
        "extra_qemu_args": args.extra_qemu_args,
        "route": {"spmi_test_apid": TEST_APID, "pdc_input": 1,
                  "gic_spi": SPI, "gic_intid": INTID},
        "scope": "Actual byte transactions, un-intercepted board IRQ wiring, real GIC pending registers and one QMP process migration.",
        "limits": [
            "Explicit reference VERSION/APID/ownership and opt-in synthetic PMIC IRQ endpoint.",
            "No native PMIC identity, physical timing, analog behavior, TrustZone or power-state wake equivalence.",
            "No guest CPU interrupt handler is executed; this verifies the hardware-model route through the GIC distributor.",
            "PDC VERSION is neither read nor replaced with a reference value in this test.",
        ],
        "checks": [], "migrations": [],
        "passed": False, "all_passed": False,
    }
    source = target = None
    start = time.monotonic()
    stage = "startup"

    def check(name, **observed):
        report["checks"].append({"name": name, "passed": True,
                                 "observed": observed})
        print(json.dumps({"name": name, "passed": True}), flush=True)

    try:
        source = RouteRig(args, source_dir)
        target = RouteRig(args, target_dir, incoming=True)
        assert source.suppressed_interceptions == target.suppressed_interceptions == 1
        report["qemu_version"] = source.monitor("query-version")
        report["output_interception_installed"] = False
        source.prepare_parent()
        source.configure_irq()

        stage = "unintercepted-spmi-event-reaches-native-gic-spi-481"
        source.input(0, 1)
        source.assert_route(True)
        assert source.get(source.bank(TEST_APID, False) + IRQ_STATUS) == 1
        check(stage, gic_pending=True, adjacent_spis_pending=False)

        stage = "pdc-mask-and-unmask-preserve-spmi-latch"
        source.put(PDC_ENABLE, source.get(PDC_ENABLE) & ~2)
        source.assert_route(False)
        assert source.get(source.bank(TEST_APID, False) + IRQ_STATUS) == 1
        source.put(PDC_ENABLE, source.get(PDC_ENABLE) | 2)
        source.assert_route(True)
        check(stage, controller_latch_retained=True, mask_and_unmask_observed=True)

        stage = "linux-ack-clears-route-without-new-edge"
        source.ack()
        source.assert_route(False)
        assert source.read_bytes(0x18) == b"\0"
        source.input(0, 1)  # No transition; must not fabricate a new edge.
        source.input(7, 1)  # An unrelated disabled input must not raise this IRQ.
        source.assert_route(False)
        check(stage, irq_cleared=True, unchanged_input_did_not_replay=True)

        stage = "new-edge-reaches-only-the-captured-parent-spi"
        source.input(0, 0)
        source.input(0, 1)
        source.assert_route(True)
        source.put(MARKER_ADDRESS, MARKER, 8)
        assert source.get(MARKER_ADDRESS, 8) == MARKER
        check(stage, gic_spi=SPI, gic_intid=INTID)

        stage = "actual-qmp-transfer-completed"
        bank = source.start(TEST_APID, 0x16, 1, b"\x01")
        now = source.step(400)
        assert source.get(bank + STATUS) == 0
        source.assert_route(True)
        source.monitor("stop")
        target.step(now)
        uri = "unix:" + str(args.out / "migration.socket")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert sm["status"] == tm["status"] == "completed"
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert not target.monitor("query-status")["running"]
        report["migrations"].append({"source": sm, "destination": tm,
                                     "source_qtest_virtual_ns": now})
        check(stage, source_status=sm["status"], destination_status=tm["status"])

        stage = "destination-restores-irq-registers-and-ram"
        assert target.get(bank + STATUS) == 0
        assert target.get(bank + IRQ_STATUS) == 1
        assert target.get(PDC_CONFIG_PIN1) == 4
        assert target.get(PDC_ENABLE) & 2
        assert target.get(MARKER_ADDRESS, 8) == MARKER
        target.assert_route(True)
        check(stage, pending_irq=True, pending_transaction=True,
              pdc_configuration_preserved=True, ram_marker_preserved=True)

        stage = "pending-write-completes-at-remaining-virtual-deadline"
        target.monitor("cont")
        target.step(599)
        assert target.get(bank + STATUS) == 0
        target.assert_route(True)
        target.step(1)
        assert target.get(bank + STATUS) == DONE
        target.assert_route(False)
        assert target.read_bytes(0x15) == b"\0"
        assert target.read_bytes(0x10)[0] & 1
        check(stage, remaining_nanoseconds=600,
              source_input_still_asserted=True, output_deasserted_by_real_write=True)

        stage = "destination-continues-with-new-events-and-acknowledgement"
        target.ack()
        target.configure_irq()
        target.input(0, 0)
        target.input(0, 1)
        target.assert_route(True)
        target.ack()
        target.assert_route(False)
        check(stage, new_irq_delivered=True, acknowledgement_cleared_real_gic=True)
    except Exception:
        report["checks"].append({"name": stage, "passed": False,
                                 "error": traceback.format_exc()})
    finally:
        if source:
            source.close()
        if target:
            target.close()

    report["elapsed_seconds"] = round(time.monotonic() - start, 6)
    report["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    passed = (len(report["checks"]) == EXPECTED_CHECKS and
              all(c["passed"] for c in report["checks"]) and
              len(report["migrations"]) == 1)
    report["passed"] = report["all_passed"] = passed
    (args.out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"passed": passed, "checks": len(report["checks"]),
                      "expected_checks": EXPECTED_CHECKS,
                      "result": str(args.out / "result.json")}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
