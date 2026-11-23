#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Test IMEM SRAM and PS_HOLD pin state, including actual QMP migration."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

IMEM, HOLD = 0x146bf000, 0x0c264000
if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


class BootRig(Rig):
    def command(self, text):
        if text.startswith("irq_intercept_out "):
            text = text.rsplit(" ", 1)[0] + " ps-hold"
        return super().command(text)

    def get(self, address, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        return int(self.command(f"read{suffix} {address:#x}")[0], 0)

    def put(self, address, value, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        self.command(f"write{suffix} {address:#x} {value:#x}")

    def level(self, expected):
        assert self.get(HOLD) == int(expected)
        assert self.irq.get(0, False) == expected, self.irq

    def board_reset(self):
        self.monitor("system_reset")
        self.wait_for(lambda: self.get(HOLD) == 1, "PS_HOLD handoff high")
        self.level(True)


def imem_initial_and_boundaries(r):
    for offset in (0, 4, 0x65c, 0x6d0, 0xff8, 0xffc):
        assert r.get(IMEM + offset) == 0
    r.put(IMEM, 0x11223344)
    r.put(IMEM + 0xffc, 0x55667788)
    r.put(IMEM - 4, 0xaabbccdd)
    r.put(IMEM + 0x1000, 0x12345678)
    assert r.get(IMEM) == 0x11223344
    assert r.get(IMEM + 0xffc) == 0x55667788


def imem_subword_little_endian(r):
    r.put(IMEM + 0x80, 0x0102030405060708, 8)
    assert [r.get(IMEM + 0x80 + i, 1) for i in range(8)] == list(range(8, 0, -1))
    r.put(IMEM + 0x81, 0xaa, 1)
    r.put(IMEM + 0x84, 0xbbcc, 2)
    assert r.get(IMEM + 0x80, 8) == 0x0102bbcc0506aa08


def imem_guest_owned_handoff_fields(r):
    # Writable SRAM locations used by native restart and KASLR handoff code.
    # These values are test inputs, not captured boot firmware contents.
    r.put(IMEM + 0x65c, 0x77665500)
    r.put(IMEM + 0x6d0, 0xdead4ead)
    r.put(IMEM + 0x6d4, 0x123456789abcdef0, 8)
    assert r.get(IMEM + 0x65c) == 0x77665500
    assert r.get(IMEM + 0x6d0) == 0xdead4ead
    assert r.get(IMEM + 0x6d4, 8) == 0x123456789abcdef0


def imem_retained_on_machine_reset(r):
    r.put(IMEM + 0x65c, 0x77665501)
    r.put(IMEM + 0x700, 0x1020304050607080, 8)
    r.put(HOLD, 0)
    r.level(False)
    r.board_reset()
    assert r.get(IMEM + 0x65c) == 0x77665501
    assert r.get(IMEM + 0x700, 8) == 0x1020304050607080


def pshold_digital_output_and_reserved_bits(r):
    r.board_reset()
    r.put(HOLD, 0)
    r.level(False)
    r.put(HOLD, 0)
    r.level(False)
    r.put(HOLD, 0xffffffff)
    r.level(True)
    r.put(HOLD, 2)
    r.level(False)
    # The pin model has no invented PMIC policy or implicit machine action.
    state = r.monitor("query-status")
    assert state["status"] == "prelaunch", state
    assert not any(e["event"] in ("SHUTDOWN", "WATCHDOG") for e in r.events)


def pshold_invalid_width_does_not_mutate(r):
    r.board_reset()
    r.put(HOLD, 0, 1)
    r.put(HOLD, 0, 2)
    r.level(True)
    r.put(HOLD + 4, 0)
    r.level(True)


def actual_migration(args, directory):
    source = target = None
    try:
        sd, td = directory / "src", directory / "dst"
        sd.mkdir()
        td.mkdir()
        source = BootRig(args, sd)
        target = BootRig(args, td, incoming=True)
        source.board_reset()
        source.put(IMEM + 0x65c, 0x77665502)
        source.put(IMEM + 0xff8, 0x1020304050607080, 8)
        source.put(HOLD, 0)
        source.level(False)
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        target.level(False)
        assert target.get(IMEM + 0x65c) == 0x77665502
        assert target.get(IMEM + 0xff8, 8) == 0x1020304050607080
        target.board_reset()
        assert target.get(IMEM + 0x65c) == 0x77665502
        return {"source_status": sm["status"], "target_status": tm["status"],
                "ram_and_low_pin_preserved": True,
                "post_migration_reset_retains_imem": True}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--qemu", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.qemu = str(a.qemu.resolve())
    a.machine = "tb371fc,ufs-enabled=off"
    a.base, a.qom_path = HOLD, "/machine/pshold"
    a.out.mkdir(parents=True, exist_ok=False)
    checks = []
    report = {"schema_version": 1, "suite": "IMEM SRAM and PS_HOLD output",
              "qemu_sha256": hashlib.sha256(Path(a.qemu).read_bytes()).hexdigest(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Captured aperture and digital pin; no initialized firmware data or PMIC power policy",
              "checks": checks, "passed": False}
    start = time.monotonic()
    tests = [imem_initial_and_boundaries, imem_subword_little_endian,
             imem_guest_owned_handoff_fields, imem_retained_on_machine_reset,
             pshold_digital_output_and_reserved_bits,
             pshold_invalid_width_does_not_mutate, actual_migration]
    for test in tests:
        directory = a.out / test.__name__
        directory.mkdir()
        rig = None
        try:
            if test == actual_migration:
                details = test(a, directory)
            else:
                rig = BootRig(a, directory)
                test(rig)
                details = {}
            checks.append({"name": test.__name__, "passed": True, **details})
        except Exception:
            checks.append({"name": test.__name__, "passed": False,
                           "error": traceback.format_exc()})
        finally:
            if rig:
                rig.close()
    report["passed"] = all(c["passed"] for c in checks)
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (a.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
