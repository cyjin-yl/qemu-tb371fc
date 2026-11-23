#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise KPSS watchdog timing, interrupt, reset and real migration behavior."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")

BASE = 0x17c10000
RST, EN, STS, BARK, BITE = 4, 8, 12, 16, 20
NSEC, MASK = 1000000000, 0xfffff


class WatchdogRig(Rig):
    def __init__(self, args, directory, incoming=False, group="sysbus-irq"):
        self.group = group
        self.now = 0
        self.epoch = 0
        self.frequency = args.frequency
        super().__init__(args, directory, incoming=incoming)
        if not incoming:
            self.monitor("cont")

    def command(self, text):
        if text.startswith("irq_intercept_out "):
            text = text.rsplit(" ", 1)[0] + " " + self.group
        return super().command(text)

    def advance(self, ns):
        assert ns > 0, ns
        self.now = int(self.command(f"clock_step {ns}")[0], 0)
        return self.now

    def deadline(self, ticks):
        return (ticks * NSEC + self.frequency - 1) // self.frequency

    def start(self, bark, bite, enable=1):
        self.write(EN, 0)
        self.write(RST, 1)
        self.epoch = self.now
        self.write(BARK, bark)
        self.write(BITE, bite)
        self.write(EN, enable)

    def count(self):
        return self.read(STS) >> 1

    def level(self, n, value):
        self.read(STS)
        assert self.irq.get(n, False) is value, (n, self.irq, value)

    def watchdog_events(self):
        self.monitor("query-status")
        return [event for event in self.events if event["event"] == "WATCHDOG"]


def reset_fields_and_stopped_counter(r):
    for reg in (RST, EN, STS, BARK, BITE):
        assert r.read(reg) == 0, (reg, r.read(reg))
    r.advance(NSEC)
    assert r.count() == 0
    r.level(0, False)
    r.level(1, False)


def register_masks_and_invalid_access(r):
    r.write(BARK, 0xffffffff)
    r.write(BITE, 0xffffffff)
    assert r.read(BARK) == r.read(BITE) == MASK
    r.write(EN, 0xfffffffe)
    assert r.read(EN) == 2
    r.advance(NSEC)
    assert r.count() == 0
    r.write(STS, 0xffffffff)
    assert r.count() == 0
    for suffix in ("b", "w"):
        r.command(f"write{suffix} {BASE + BARK:#x} 0")
        assert r.read(BARK) == MASK
    r.write(RST, 0xfffffffe)
    assert r.read(RST) == 0


def exact_reference_frequency(r):
    assert r.monitor("qom-get", {"path": "/machine/watchdog",
                                "property": "frequency"}) == r.frequency
    r.start(MASK, MASK)
    r.advance(NSEC)
    assert r.count() == r.frequency
    r.level(0, False)
    r.level(1, False)


def first_nanosecond_bark_boundary(r):
    r.start(3, 7)
    r.advance(r.deadline(3) - 1)
    assert r.count() == 2
    r.level(0, False)
    r.level(1, False)
    r.advance(1)
    assert r.count() == 3
    r.level(0, True)
    r.level(1, False)


def pet_clears_and_rearms(r):
    r.start(3, 7)
    r.advance(r.deadline(3))
    r.level(0, True)
    r.write(RST, 0)
    r.level(0, True)
    r.write(RST, 1)
    assert r.count() == 0
    r.level(0, False)
    r.advance(r.deadline(3) - 1)
    r.level(0, False)
    r.advance(1)
    r.level(0, True)


def disable_preserves_subtick_phase(r):
    r.start(100, 200)
    first = r.deadline(1) // 2
    r.advance(first)
    assert r.count() == 0
    r.write(EN, 0)
    r.advance(2 * NSEC)
    assert r.count() == 0
    r.write(EN, 1)
    r.advance(r.deadline(1) - first - 1)
    assert r.count() == 0
    r.advance(1)
    assert r.count() == 1
    r.level(0, False)


def disable_gates_asserted_irq(r):
    r.start(1, 100)
    r.advance(r.deadline(1))
    r.level(0, True)
    r.write(EN, 0)
    frozen = r.count()
    r.level(0, False)
    r.advance(NSEC)
    assert r.count() == frozen
    r.write(EN, 1)
    r.level(0, True)
    r.write(RST, 1)
    r.level(0, False)


def bite_event_once_per_pet(r):
    r.start(3, 7)
    r.advance(r.deadline(7))
    r.level(0, True)
    r.level(1, True)
    events = r.watchdog_events()
    assert len(events) == 1 and events[0]["data"]["action"] == "none", events
    r.advance(NSEC)
    assert len(r.watchdog_events()) == 1
    r.write(RST, 1)
    r.level(0, False)
    r.level(1, False)
    r.advance(r.deadline(7))
    assert len(r.watchdog_events()) == 2


def bite_before_bark(r):
    r.start(10, 3)
    r.advance(r.deadline(3))
    r.level(0, False)
    r.level(1, True)
    assert len(r.watchdog_events()) == 1
    r.advance(r.deadline(10) - r.deadline(3))
    r.level(0, True)
    assert len(r.watchdog_events()) == 1


def rewrite_elapsed_compare(r):
    r.start(100, 200)
    r.advance(r.deadline(50))
    assert r.count() == 50
    r.write(BARK, 25)
    r.advance(1)
    r.level(0, True)
    assert r.count() == 50
    r.write(BITE, 1)
    r.advance(1)
    r.level(1, True)
    assert len(r.watchdog_events()) == 1


def counter_field_wrap(r):
    r.start(MASK, MASK)
    r.advance(r.deadline((1 << 20) + 5))
    assert r.count() == 5
    assert r.read(STS) & 1 == 0
    assert len(r.watchdog_events()) == 1


def machine_reset_cancels_deadline(r):
    r.start(3, 7)
    r.advance(r.deadline(2))
    r.monitor("system_reset")
    r.wait_for(lambda: r.read(EN) == 0 and r.read(BARK) == 0,
               "watchdog reset completion")
    assert r.count() == 0
    r.advance(NSEC)
    r.level(0, False)
    r.level(1, False)
    assert not r.watchdog_events()


def wakeup_output_reference_profile(r):
    r.start(1, 100, enable=1)
    r.advance(r.deadline(1))
    r.level(0, False)
    r.write(EN, 3)
    r.level(0, True)
    r.write(EN, 1)
    r.level(0, False)
    r.write(EN, 3)
    r.level(0, True)
    r.write(RST, 1)
    r.level(0, False)


def bite_requests_real_machine_reset(r):
    r.start(3, 7)
    r.advance(r.deadline(7))
    r.wait_for(lambda: r.read(EN) == 0 and r.read(BARK) == 0,
               "watchdog action machine reset")
    r.monitor("query-status")
    watchdog = [e for e in r.events if e["event"] == "WATCHDOG"]
    resets = [e for e in r.events if e["event"] == "RESET"]
    assert len(watchdog) == 1, watchdog
    assert watchdog[0]["data"]["action"] == "reset", watchdog
    # QEMU watchdog.c requests SHUTDOWN_CAUSE_GUEST_RESET. The distinct
    # WATCHDOG event above carries the watchdog origin; RESET uses guest-reset.
    assert len(resets) == 1, resets
    assert resets[0]["data"] == {"guest": True, "reason": "guest-reset"}, resets
    assert r.events.index(watchdog[0]) < r.events.index(resets[0]), r.events
    assert r.count() == 0


def migration(args, directory):
    source = target = None
    details = {}
    try:
        sd, td = directory / "source", directory / "target"
        sd.mkdir()
        td.mkdir()
        source = WatchdogRig(args, sd)
        target = WatchdogRig(args, td, incoming=True)
        source.advance(17000000)
        source.start(10, 20, enable=3)
        source.advance(source.deadline(15))
        source.level(0, True)
        source.level(1, False)
        before = {reg: source.read(reg) for reg in (EN, STS, BARK, BITE)}
        ns, epoch = source.now, source.epoch
        source.monitor("stop")
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        details["source"] = wait_migration(source)
        details["target"] = wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert not target.monitor("query-status")["running"]
        target.monitor("cont")
        # QTest owns an external clock; align that fixture after incoming load.
        target.now = int(target.command(f"clock_set {ns}")[0], 0)
        assert target.now == ns
        after = {reg: target.read(reg) for reg in (EN, STS, BARK, BITE)}
        assert after == before, (before, after)
        target.level(0, True)
        target.level(1, False)
        remaining = epoch + target.deadline(20) - ns
        target.advance(remaining - 1)
        target.level(1, False)
        target.advance(1)
        target.level(1, True)
        assert len(target.watchdog_events()) == 1
        target.write(RST, 1)
        target.level(0, False)
        target.level(1, False)
        details["preserved"] = ["counter_phase", "registers", "asserted_bark",
                                "pending_bite_deadline", "post_migration_pet"]
        return details
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--frequency", type=int, default=32765)
    parser.add_argument("--migration-only", action="store_true")
    args = parser.parse_args()
    args.qemu = str(Path(args.qemu).resolve())
    args.machine = "tb371fc,ufs-enabled=off"
    args.base, args.qom_path = BASE, "/machine/watchdog"
    args.extra_qemu_args = ["-watchdog-action", "none", "-global",
                           f"qcom-kpss-watchdog.frequency={args.frequency}"]
    args.out.mkdir(parents=True, exist_ok=False)
    checks = []
    report = {
        "schema_version": 1,
        "suite": "KPSS watchdog " + ("migration" if args.migration_only else "QTest"),
        "qemu_sha256": hashlib.sha256(Path(args.qemu).read_bytes()).hexdigest(),
        "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "frequency_hz": args.frequency,
        "frequency_provenance": "explicit reference profile; not measured hardware",
        "scope": "Digital MMIO counter, IRQ and QEMU watchdog action; no secure firmware",
        "checks": checks, "passed": False,
    }
    started = time.monotonic()
    if args.migration_only:
        directory = args.out / "pending"
        directory.mkdir()
        try:
            details = migration(args, directory)
            checks.append({"name": "actual_pending_bite_migration",
                           "passed": True, "details": details})
        except Exception:
            checks.append({"name": "actual_pending_bite_migration",
                           "passed": False, "error": traceback.format_exc()})
    else:
        tests = [reset_fields_and_stopped_counter,
                 register_masks_and_invalid_access,
                 exact_reference_frequency, first_nanosecond_bark_boundary,
                 pet_clears_and_rearms, disable_preserves_subtick_phase,
                 disable_gates_asserted_irq, bite_event_once_per_pet,
                 bite_before_bark, rewrite_elapsed_compare,
                 counter_field_wrap, machine_reset_cancels_deadline,
                 wakeup_output_reference_profile,
                 bite_requests_real_machine_reset]
        for test in tests:
            directory = args.out / test.__name__
            directory.mkdir()
            config = copy.copy(args)
            group = "wakeup" if test == wakeup_output_reference_profile else "sysbus-irq"
            if test == bite_requests_real_machine_reset:
                config.extra_qemu_args = [x if x != "none" else "reset"
                                         for x in args.extra_qemu_args]
            rig = None
            try:
                rig = WatchdogRig(config, directory, group=group)
                test(rig)
                checks.append({"name": test.__name__, "passed": True})
            except Exception:
                checks.append({"name": test.__name__, "passed": False,
                               "error": traceback.format_exc()})
            finally:
                if rig:
                    rig.close()
        for rate in (0, 1000000001):
            command = [args.qemu, "-M", args.machine, "-m", "128M",
                       "-display", "none", "-monitor", "none", "-serial", "none",
                       "-global", f"qcom-kpss-watchdog.frequency={rate}", "-S"]
            try:
                result = subprocess.run(command, capture_output=True, timeout=5)
                text = result.stderr.decode(errors="replace")
                assert result.returncode != 0 and "frequency must be" in text, text
                checks.append({"name": f"invalid_frequency_{rate}_rejected",
                               "passed": True, "stderr": text})
            except Exception:
                checks.append({"name": f"invalid_frequency_{rate}_rejected",
                               "passed": False, "error": traceback.format_exc()})
    report["passed"] = bool(checks) and all(c["passed"] for c in checks)
    report["passed_count"] = sum(c["passed"] for c in checks)
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
