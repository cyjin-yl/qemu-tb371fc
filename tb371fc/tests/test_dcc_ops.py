#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Real QTest DCC WRITE/LOOP/RMW checks. No test is a hardware-identity claim."""
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import sys
import time
import traceback

BASE, SRAM, RAM = 0x1023000, 0x103a000, 0x80001000
END, RMW = 0xc0000000, 0x80000000
FD, SENTINEL = 0x2000, 0xa55aa55a
EXPECTED_CHECKS = 16


def address(value, write=False, apb=False):
    assert value % 16 == 0
    return (value >> 4) | (int(write) << 28) | (int(apb) << 29)


def link(offset=0, length=1):
    return END | (1 << 15) | offset | (length << 8)


def loop(back, iterations):
    return 0x40000000 | ((iterations - 1) << 16) | back


def read_words(addr=RAM):
    return [address(addr & ~15), link((addr & 15) // 4)]


def write_words(addr, value, apb=False):
    return [address(addr & ~15, write=True, apb=apb),
            link((addr & 15) // 4), value]


def run(r, words, initial=0, expected=None):
    r.put(RAM, initial)
    r.words(SRAM + FD, [SENTINEL] * 8)
    r.program(words)
    r.trigger()
    r.step()
    r.expect_busy(False)
    if expected is not None:
        assert r.collect(FD, len(expected)) == expected


def blocked(r, words, ll=0x100):
    r.put(RAM, 0x12345678)
    r.words(SRAM + FD, [SENTINEL] * 8)
    r.program(words, ll=ll)
    r.trigger()
    r.step(1000000)
    r.expect_busy(True)
    assert r.get(RAM) == 0x12345678
    assert r.collect(FD, 8) == [SENTINEL] * 8


def write_only_waits_for_deadline(r):
    r.put(RAM, 0)
    r.words(SRAM + FD, [SENTINEL])
    r.program(write_words(RAM, 0x11223344) + [END])
    r.trigger()
    r.step(999)
    r.expect_busy(True)
    assert r.get(RAM) == 0
    r.step(1)
    r.expect_busy(False)
    assert r.get(RAM) == 0x11223344
    assert r.collect(FD, 1) == [SENTINEL]


def write_literal_is_not_an_opcode(r):
    for value in (0, 0x40000000, RMW, END, 0xffffffff):
        r.reset_board()
        run(r, write_words(RAM, value) + read_words() + [END],
            expected=[value])


def ordered_writes_and_captures(r):
    words = (write_words(RAM, 11) + read_words() +
             write_words(RAM, 22) + read_words() + [END])
    run(r, words, expected=[11, 22])
    assert r.get(RAM) == 22
    r.reset_board()
    # A bus failure during execution preserves a preceding real write.
    r.put(RAM, 0)
    r.words(SRAM + FD, [SENTINEL])
    r.program(write_words(RAM, 33) +
              write_words(0x40000000, 44) + [END])
    r.trigger()
    r.step(1000000)
    r.expect_busy(True)
    assert r.get(RAM) == 33
    assert r.collect(FD, 1) == [SENTINEL]


def write_offset_and_bus_selector(r):
    for apb in (False, True):
        r.reset_board()
        words = write_words(RAM + 12, 0x87654321, apb)
        words += read_words(RAM + 12) + [END]
        run(r, words, expected=[0x87654321])
        assert r.get(RAM + 12) == 0x87654321


def rmw_captures_old_then_new(r):
    words = read_words() + [RMW, 0xf, 0xa] + read_words() + [END]
    run(r, words, initial=0x12345675,
        expected=[0x12345675, 0x1234567a])
    assert r.get(RAM) == 0x1234567a


def rmw_masks_zero_all_and_outside_value_bits(r):
    for mask, value, want in (
            (0, 0xffffffff, 0xa5a55a5a),
            (0xffffffff, 0x01234567, 0x01234567),
            (0x00ff00ff, 0xff12ff34, 0xa5125a34)):
        r.reset_board()
        run(r, read_words() + [RMW, mask, value] + read_words() + [END],
            initial=0xa5a55a5a, expected=[0xa5a55a5a, want])
        assert r.get(RAM) == want


def loop_one_and_max_driver_iterations(r):
    for count in (1, 255):
        r.reset_board()
        run(r, read_words() + [loop(2, count), END],
            initial=0x13579bdf, expected=[0x13579bdf] * count)


def loop_reexecutes_bus_operations(r):
    # First read sees 0; the WRITE in that iteration changes subsequent reads.
    words = read_words() + write_words(RAM, 1) + [loop(5, 3), END]
    run(r, words, expected=[0, 1, 1])
    assert r.get(RAM) == 1


def sequential_loops_use_their_own_body(r):
    words = read_words() + [loop(2, 2)]
    words += read_words(RAM + 16) + [loop(2, 3), END]
    r.put(RAM + 16, 22)
    run(r, words, initial=11, expected=[11, 11, 22, 22, 22])


def rmw_inside_loop_uses_each_new_read(r):
    words = read_words() + [RMW, 0xf, 1, loop(5, 3), END]
    run(r, words, initial=0x10, expected=[0x10, 0x11, 0x11])
    assert r.get(RAM) == 0x11


def truncated_payloads_have_no_earlier_effects(r):
    blocked(r, [address(RAM, write=True), link()], ll=0x5ff8)
    r.reset_board()
    blocked(r, read_words() + [RMW, 0xf], ll=0x5ff0)


def invalid_loop_targets_and_nesting_are_rejected(r):
    cases = [
        read_words() + [0x40000000, END],  # zero distance
        read_words() + [loop(3, 2), END],  # outside this list
        read_words() + [loop(1, 2), END],  # LINK, not ADDRESS
        write_words(RAM, 5) + [loop(1, 2), END],  # literal payload
        read_words() + [loop(2, 2), loop(3, 2), END],  # nested
        read_words() + [loop(2, 256), END],
    ]
    for words in cases:
        r.reset_board()
        blocked(r, words)


def rmw_requires_exactly_one_adjacent_read(r):
    cases = [
        [RMW, 0xf, 1, END],
        [address(RAM), link(0, 2), RMW, 0xf, 1, END],
        write_words(RAM, 1) + [RMW, 0xf, 1, END],
    ]
    for words in cases:
        r.reset_board()
        blocked(r, words)


def expanded_capture_overflow_is_preflighted(r):
    blocked(r, [address(RAM), link(0, 127), loop(2, 255), END])


def self_modifying_write_is_not_silently_snapshotted(r):
    blocked(r, write_words(SRAM + 0x100, 0) + [END])
    assert r.get(SRAM + 0x100) == address(SRAM + 0x100, write=True)
    r.reset_board()
    # A later unsupported target must prevent an earlier valid bus WRITE.
    words = write_words(RAM, 0xdeadbeef)
    words += write_words(SRAM + 0x100, 0) + [END]
    blocked(r, words)
    assert r.get(SRAM + 0x100) == address(RAM, write=True)


def migration_executes_pending_program_once(args, directory):
    source_dir, target_dir = directory / "s", directory / "t"
    source_dir.mkdir()
    target_dir.mkdir()
    source = target = None
    try:
        source = DccRig(args, source_dir)
        target = DccRig(args, target_dir, incoming=True)
        source.put(RAM, 0)
        source.words(SRAM + FD, [SENTINEL] * 3)
        source.program(read_words() + write_words(RAM, 1) +
                       [loop(5, 3), END])
        source.trigger()
        now = int(source.command("clock_step 400")[0], 0)
        source.expect_busy(True)
        assert source.get(RAM) == 0
        source.monitor("stop")
        target.command(f"clock_step {now}")
        uri = "unix:" + str(directory / "transfer")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert sm["status"] == tm["status"] == "completed"
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert target.get(RAM) == 0
        assert target.collect(FD, 3) == [SENTINEL] * 3
        target.monitor("cont")
        target.step(599)
        target.expect_busy(True)
        assert target.get(RAM) == 0
        target.step(1)
        target.expect_busy(False)
        assert target.collect(FD, 3) == [0, 1, 1]
        assert target.get(RAM) == 1
        target.step(10000)
        assert target.collect(FD, 3) == [0, 1, 1]
        return {"source": sm, "destination": tm, "remaining_ns": 600}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--helpers", type=Path,
                        default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    if not __debug__:
        parser.error("Assertions are required")
    sys.path.insert(0, str(args.helpers.resolve()))
    global DccRig, wait_migration
    helper = importlib.import_module("test_dcc")
    DccRig, wait_migration = helper.DccRig, helper.wait_migration
    args.qemu = str(args.qemu.resolve())
    args.base, args.qom_path = BASE, "/machine/dcc"
    args.machine = "tb371fc,ufs-enabled=off"
    args.extra_qemu_args = ["-d", "unimp,guest_errors"]
    args.out.mkdir(parents=True, exist_ok=False)
    cases = [
        write_only_waits_for_deadline, write_literal_is_not_an_opcode,
        ordered_writes_and_captures, write_offset_and_bus_selector,
        rmw_captures_old_then_new, rmw_masks_zero_all_and_outside_value_bits,
        loop_one_and_max_driver_iterations, loop_reexecutes_bus_operations,
        sequential_loops_use_their_own_body, rmw_inside_loop_uses_each_new_read,
        truncated_payloads_have_no_earlier_effects,
        invalid_loop_targets_and_nesting_are_rejected,
        rmw_requires_exactly_one_adjacent_read,
        expanded_capture_overflow_is_preflighted,
        self_modifying_write_is_not_silently_snapshotted,
        migration_executes_pending_program_once,
    ]
    assert len(cases) == EXPECTED_CHECKS
    report = {"schema_version": 1, "expected_checks": EXPECTED_CHECKS,
              "qemu_sha256": hashlib.sha256(Path(args.qemu).read_bytes()).hexdigest(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "helper_sha256": hashlib.sha256(Path(helper.__file__).read_bytes()).hexdigest(),
              "checks": [], "passed": False,
              "scope": "Reference canonical DCC programs; actual memory/IO and migration. No native identity or physical bus timing claim."}
    start = time.monotonic()
    for index, case in enumerate(cases):
        folder = args.out / f"{index:02d}"
        folder.mkdir()
        rig = None
        try:
            if case is migration_executes_pending_program_once:
                details = case(args, folder)
            else:
                rig = DccRig(args, folder)
                details = case(rig) or {}
            report["checks"].append({"name": case.__name__, "passed": True,
                                     **details})
        except Exception:
            report["checks"].append({"name": case.__name__, "passed": False,
                                     "error": traceback.format_exc()})
        finally:
            if rig:
                rig.close()
    report["passed"] = (len(report["checks"]) == EXPECTED_CHECKS and
                        all(x["passed"] for x in report["checks"]))
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
