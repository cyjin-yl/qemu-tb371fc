#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Exercise held RPMh sleep/wake requests and real PDC/GIC checkpoint state.

Sleep inputs are explicitly driven by QTest. This does not claim automatic
CPU WFI/PDC-to-RPMh coordination, AOP execution, or physical power collapse.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shlex
import struct
import time
import traceback

from test_geni_migration import wait_migration
from test_pdc_migration import GICD, PDC, bank, cfg, gic_id
from test_rpmh_sm8250 import RpmhRig, TCS_BASE, ISSUED, COMPLETE, check

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


class PowerRig(RpmhRig):
    """Keep both RPMh and PDC physically connected to the emulated GIC."""

    def command(self, command):
        if command.startswith("irq_intercept_out "):
            return []
        return super().command(command)

    def get(self, address):
        return int(self.command(f"readl {address:#x}")[0], 0)

    def put(self, address, value):
        self.command(f"writel {address:#x} {value:#x}")

    def pdc_input(self, pin, level):
        self.command(f"set_irq_in /machine/pdc irq-in {pin} {int(level)}")

    def parent_config(self, ident, edge):
        reg = GICD + 0xc00 + 4 * (ident // 16)
        shift = (ident % 16) * 2
        value = (2 if edge else 0) << shift
        self.put(reg, (self.get(reg) & ~(3 << shift)) | value)
        self.put(GICD + 0x100 + 4 * (ident // 32), 1 << (ident % 32))
        self.put(GICD, self.get(GICD) | 3)

    def pending(self, ident):
        return bool(self.get(GICD + 0x200 + 4 * (ident // 32)) &
                    (1 << (ident % 32)))

    def clear_parent(self, ident):
        self.put(GICD + 0x280 + 4 * (ident // 32), 1 << (ident % 32))

    def pdc_config(self, pin, edge):
        self.parent_config(gic_id(pin), edge)
        self.put(cfg(pin), 6 if edge else 4)
        self.put(bank(pin), self.get(bank(pin)) | (1 << (pin % 32)))


def program_low_power(r):
    r.run_one("xo.lvl", 3)
    r.clear()
    r.program("xo.lvl", 0, tcs=2)
    r.dwrite(r.tcs(2, 0x1c), 1)
    r.program("xo.lvl", 3, tcs=5)
    r.dwrite(r.tcs(5, 0x1c), 1)


def busy_sleep_level_is_retained(r):
    program_low_power(r)
    r.program("xo.lvl", 1)
    r.trigger()
    r.gpio("sleep", 2, True)
    r.step(1000)
    check(r.status(tcs=2) == ISSUED, "held sleep level was lost during active TCS")
    r.step(1000)
    check(r.resource("xo.lvl") == 0, "deferred sleep bank did not complete")
    r.gpio("sleep", 2, False)
    r.step(1000)
    r.expect_hz("bi_tcxo", 19_200_000)


def busy_wake_level_is_retained(r):
    program_low_power(r)
    r.gpio("sleep", 2, True)
    r.step(400)
    r.gpio("sleep", 2, False)
    r.step(600)
    check(r.resource("xo.lvl") == 0, "sleep bank failed to finish first")
    check(r.status(tcs=5) == ISSUED, "held wake level was lost during sleep TCS")
    r.step(999)
    check(r.resource("xo.lvl") == 0, "wake command completed before its deadline")
    r.step(1)
    r.expect_hz("bi_tcxo", 19_200_000)


def latest_level_replaces_unstarted_transition(r):
    program_low_power(r)
    r.program("xo.lvl", 1)
    r.trigger()
    r.gpio("sleep", 2, True)
    r.gpio("sleep", 2, False)
    r.step(5000)
    check(r.resource("xo.lvl") == 1, "obsolete sleep/wake request was replayed")
    check(r.status(tcs=2) == r.status(tcs=5) == 0,
          "low-power bank ran despite latest level matching the awake state")


def cancellation_releases_held_request_without_acknowledging_stall(r):
    program_low_power(r)
    r.program("xo.lvl", 1, address=0x3dead)
    r.trigger()
    r.step(1000)
    r.gpio("sleep", 2, True)
    r.step(5000)
    check(r.status() == ISSUED and r.status(tcs=2) == 0,
          "unsupported command was acknowledged or bypassed by sleep")
    r.dwrite(r.tcs(0, 0x14), 0)
    check(r.status(tcs=2) == ISSUED, "cancel did not release held sleep request")
    r.step(1000)
    check(r.resource("xo.lvl") == 0, "sleep after explicit cancellation failed")


def reset_discards_deferred_request(r):
    program_low_power(r)
    r.program("xo.lvl", 1)
    r.trigger()
    r.gpio("sleep", 2, True)
    r.monitor("system_reset")
    r.wait_for(lambda: r.status() == 0 and r.status(tcs=2) == 0,
               "RPMh reset")
    program_low_power(r)
    r.program("xo.lvl", 1)
    r.trigger()
    r.step(5000)
    check(r.resource("xo.lvl") == 1 and r.status(tcs=2) == 0,
          "reset leaked a previously held sleep request")


def migrate(source, target, directory):
    now = source.last_clock
    source.monitor("stop")
    target.step(now)
    uri = "unix:" + str(directory / "migration.sock")
    target.monitor("migrate-incoming", {"uri": uri})
    source.monitor("migrate", {"uri": uri})
    sent = wait_migration(source)
    received = wait_migration(target)
    check(source.monitor("query-status")["status"] == "postmigrate",
          "source did not stop after migration")
    check(not target.monitor("query-status")["running"],
          "destination resumed during inspection")
    return {"source": sent, "destination": received,
            "qtest_virtual_ns_at_transfer": now}


def combined_pdc_pending_and_deferred_wake(args, directory):
    source = target = None
    try:
        source = PowerRig(args, directory / "s")
        target = PowerRig(args, directory / "t", incoming=True)
        program_low_power(source)
        for pin, edge in ((1, False), (125, True), (94, True)):
            source.pdc_config(pin, edge)
            source.pdc_input(pin, True)
            check(source.pending(gic_id(pin)), f"PDC pin {pin} did not reach GIC")
        source.clear_parent(gic_id(94))
        check(not source.pending(gic_id(94)), "control edge did not clear")
        source.parent_config(37, False)  # APPS DRV2: SPI 5, INTID 37.
        source.dwrite(TCS_BASE, (1 << 2) | (1 << 5))
        source.gpio("sleep", 2, True)
        source.step(400)
        source.gpio("sleep", 2, False)
        receipt = migrate(source, target, directory)

        check(target.resource("xo.lvl") == 3, "load applied an unfinished sleep command")
        check(target.status(tcs=2) == ISSUED and target.status(tcs=5) == 0,
              "load changed TCS execution order")
        for pin, expected in ((1, True), (125, True), (94, False)):
            check(target.pending(gic_id(pin)) == expected,
                  f"PDC/GIC pin {pin} state was lost or replayed")
        target.clear_parent(gic_id(125))
        target.pdc_input(125, True)
        check(not target.pending(gic_id(125)), "acknowledged held edge replayed")
        target.pdc_input(1, False)
        check(not target.pending(gic_id(1)), "restored level did not deassert")

        target.monitor("cont")
        target.step(599)
        check(target.resource("xo.lvl") == 3, "sleep deadline fired early after load")
        target.step(1)
        check(target.resource("xo.lvl") == 0 and target.status(tcs=5) == ISSUED,
              "pending wake did not start once migrated sleep completed")
        check(target.pending(37), "sleep completion did not reach real GIC")
        target.dwrite(TCS_BASE + 8, 1 << 2)
        check(not target.pending(37), "sleep W1C failed to lower real GIC input")
        target.step(999)
        check(target.resource("xo.lvl") == 0, "wake deadline fired early after load")
        target.step(1)
        target.expect_hz("bi_tcxo", 19_200_000)
        check(target.status(tcs=5) == (ISSUED | COMPLETE) and target.pending(37),
              "wake completion or GIC interrupt was lost")
        target.dwrite(TCS_BASE + 8, 1 << 5)
        target.gpio("sleep", 2, False)
        target.step(5000)
        check(target.dread(TCS_BASE + 4) == 0 and not target.pending(37),
              "held wake level produced another completion")
        receipt["observations"] = [
            "PDC level, unacknowledged edge and acknowledged edge migrate together",
            "unfinished sleep and held wake resume in order at remaining deadlines",
            "RPMh sleep/wake completion reaches real GIC and W1C drains it",
            "unchanged levels do not replay edge or wake completions",
        ]
        return receipt
    finally:
        if source:
            source.close()
        if target:
            target.close()


def legacy_v2_sleep_to_current(args, directory):
    source = target = None
    try:
        old = copy.copy(args)
        old.qemu = str(args.legacy_qemu)
        source = PowerRig(old, directory / "s")
        target = PowerRig(args, directory / "t", incoming=True)
        program_low_power(source)
        source.gpio("sleep", 2, True)
        source.step(1000)
        receipt = migrate(source, target, directory)
        check(target.resource("xo.lvl") == 0, "legacy sleep vote was not restored")
        target.monitor("cont")
        target.gpio("sleep", 2, False)
        target.step(1000)
        target.expect_hz("bi_tcxo", 19_200_000)
        receipt["legacy_qemu_sha256"] = sha(args.legacy_qemu)
        return receipt
    finally:
        if source:
            source.close()
        if target:
            target.close()


def reject_idle_deferred_request(args, directory):
    source = target = None
    try:
        source = PowerRig(args, directory / "s")
        program_low_power(source)
        source.gpio("sleep", 2, True)
        source.step(1000)
        source.monitor("stop")
        checkpoint = directory / "valid.state"
        source.monitor("migrate", {"uri": "exec:cat > " + shlex.quote(str(checkpoint))})
        receipt = wait_migration(source)
        raw = checkpoint.read_bytes()
        name = b"qcom-sm8250-rpmh"
        needle = name + struct.pack(">II", 0, 3)
        matches = []
        cursor = 0
        while (at := raw.find(needle, cursor)) >= 0:
            end = at + len(name)
            for start in range(max(0, at - 255), at + 1):
                length = end - start
                if (0 < length <= 255 and start >= 6 and
                        raw[start - 1] == length and raw[start - 6] == 4 and
                        b"\0" not in raw[start:end]):
                    matches.append((end + 8, raw[start - 5:start - 1]))
            cursor = at + len(needle)
        check(len(matches) == 1, ("unique version-3 RPMh section", matches))
        body, section_id = matches[0]
        # v2 wire prefix: digest/latency, four voters, 98 resources, XO clock.
        # Match its exact v3 extension and section footer before changing a byte.
        command_bytes = 5 * 4
        tcs_bytes = 3 * 4 + 16 * command_bytes + 4 + 8 + 2
        voter_bytes = 4 * 4 + 1 + 8 * tcs_bytes
        resource_bytes = 4 * 4 * 4 + 4 * 4 + 4
        pending_at = body + 32 + 8 + 4 * voter_bytes + 98 * resource_bytes + 8
        check(raw[pending_at:pending_at + 4] == bytes((0, 0, 1, 0)),
              "unexpected sleep request wire values")
        check(raw[pending_at + 4:pending_at + 9] == b"\x7e" + section_id,
              "unexpected RPMh section tail")
        damaged = bytearray(raw)
        damaged[pending_at + 2] = 0
        invalid = directory / "invalid.state"
        invalid.write_bytes(damaged)
        target = PowerRig(args, directory / "t", incoming=True)
        try:
            target.monitor("migrate-incoming", {
                "uri": "exec:cat " + shlex.quote(str(invalid))})
        except (AssertionError, RuntimeError):
            pass
        code = target.process.wait(timeout=10)
        diagnostic = "RPMh migration: idle voter 2 has an unconsumed sleep request"
        check(code != 0, "destination accepted an impossible deferred request")
        check(diagnostic in target.log_path.read_text(),
              "destination failed for an unrelated reason")
        return {"source_migration": receipt, "destination_exit": code,
                "wire_offset": pending_at + 2, "diagnostic": diagnostic,
                "original_sha256": sha(checkpoint), "mutated_sha256": sha(invalid)}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


RUNTIME_TESTS = (
    busy_sleep_level_is_retained,
    busy_wake_level_is_retained,
    latest_level_replaces_unstarted_transition,
    cancellation_releases_held_request_without_acknowledging_stall,
    reset_discards_deferred_request,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--legacy-qemu", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--case", choices=[f.__name__ for f in RUNTIME_TESTS] +
                        ["combined_pdc_pending_and_deferred_wake", "legacy_v2_sleep_to_current",
                         "reject_idle_deferred_request"])
    args = parser.parse_args()
    if args.case == "legacy_v2_sleep_to_current" and not args.legacy_qemu:
        parser.error("legacy_v2_sleep_to_current requires --legacy-qemu")
    args.qemu = str(args.qemu.resolve())
    if args.legacy_qemu:
        args.legacy_qemu = args.legacy_qemu.resolve()
    args.out = args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    args.machine = "tb371fc,rpmh-profile=reference,rpmh-boot-configured=off,ufs-enabled=off"
    args.base, args.cmd_db_base, args.qom_path = 0x18200000, 0x80860000, "/machine/rpmh"
    report = {
        "schema_version": 1,
        "kind": "tb371fc-held-wake-and-pdc-gic-process-checkpoint",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": False,
        "machine": args.machine,
        "qemu_sha256": sha(args.qemu),
        "test_sha256": sha(__file__),
        "helpers_sha256": {name: sha(Path(__file__).with_name(name)) for name in
                           ("test_rpmh_sm8250.py", "test_pdc_migration.py",
                            "test_gcc_sm8250.py", "test_geni_uart.py",
                            "test_geni_migration.py")},
        "checks": [],
        "limits": [
            "Explicit sleep/wake GPIO only; no automatic CPU WFI/PDC power solver.",
            "PDC and RPMh share one migrated board; they are not implicitly coupled.",
            "Synthetic command database and virtual command latency; no silicon/AOP equivalence.",
            "UFS disabled for this component suite; no claim of in-flight storage migration.",
        ],
    }
    tests = [(f.__name__, f, False) for f in RUNTIME_TESTS]
    tests.append(("combined_pdc_pending_and_deferred_wake",
                  combined_pdc_pending_and_deferred_wake, True))
    tests.append(("reject_idle_deferred_request", reject_idle_deferred_request, True))
    if args.legacy_qemu:
        tests.append(("legacy_v2_sleep_to_current", legacy_v2_sleep_to_current, True))
    if args.case:
        tests = [t for t in tests if t[0] == args.case]
    report["expected_checks"] = len(tests)
    for index, (name, function, process_test) in enumerate(tests):
        directory = args.out / f"{index:02d}"
        directory.mkdir()
        rig = None
        started = time.monotonic()
        row = {"name": name, "passed": False, "directory": str(directory)}
        try:
            if process_test:
                row["receipt"] = function(args, directory)
            else:
                rig = PowerRig(args, directory / "q")
                function(rig)
            row["passed"] = True
        except Exception:
            row["error"] = traceback.format_exc()
        finally:
            if rig:
                rig.close()
        row["seconds"] = round(time.monotonic() - started, 4)
        report["checks"].append(row)
    report["qemu_unchanged"] = sha(args.qemu) == report["qemu_sha256"]
    report["all_passed"] = report["qemu_unchanged"] and len(report["checks"]) == len(tests) and all(
        row["passed"] for row in report["checks"])
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
