#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise DCC SRAM capture through real QEMU QTest and actual migration."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

BASE, SRAM, INTERNAL = 0x1023000, 0x103a000, 0x1a000
IMEM, HOLD = 0x146bf000, 0x0c264000
INFO, STATUS, LISTS = 4, 0x1c, 0x10
LOCK, CFG, LL, FD = 0, 4, 8, 12
TIMEOUT, INT_EN, INT_STATUS, TRIGGER, BUS_STATUS = 16, 24, 28, 44, 48
END = 0xc0000000

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


class DccRig(Rig):
    def __init__(self, args, directory, incoming=False):
        super().__init__(args, directory, incoming=incoming)
        # QTest executes no guest instructions, but virtual timer dispatch
        # still requires a running VM event loop.
        if not incoming:
            self.monitor("cont")

    def command(self, text):
        if text.startswith("irq_intercept_out "):
            # DCC's native DT has no IRQ. This suite uses its polling contract.
            return []
        return super().command(text)

    def get(self, address, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        return int(self.command(f"read{suffix} {address:#x}")[0], 0)

    def put(self, address, value, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        self.command(f"write{suffix} {address:#x} {value:#x}")

    def reg(self, n, off):
        return BASE + 0x2c + n * 0x80 + off

    def read_list(self, n, off):
        return self.get(self.reg(n, off))

    def write_list(self, n, off, value):
        self.put(self.reg(n, off), value)

    def words(self, address, values):
        for n, value in enumerate(values):
            self.put(address + n * 4, value)

    def collect(self, offset, n):
        return [self.get(SRAM + offset + i * 4) for i in range(n)]

    def program(self, descriptors, n=0, ll=0x100, fd=0x2000, cfg=0x200):
        self.words(SRAM + ll, descriptors)
        self.write_list(n, LOCK, 1)
        self.write_list(n, CFG, cfg)
        self.write_list(n, LL, (INTERNAL + ll) // 4)
        self.write_list(n, FD, (INTERNAL + fd) // 4)
        self.write_list(n, TIMEOUT, 0xfff)

    def trigger(self, n=0):
        cfg = self.read_list(n, CFG)
        self.write_list(n, CFG, cfg & ~0x200)
        self.write_list(n, TRIGGER, 1)
        self.write_list(n, CFG, cfg)

    def step(self, ns=1000):
        self.command(f"clock_step {ns}")

    def expect_busy(self, busy):
        assert bool(self.get(BASE + STATUS) & 3) == busy

    def reset_board(self):
        self.monitor("system_reset")
        self.wait_for(lambda: self.get(BASE + STATUS) == 0,
                      "DCC register reset")


def address(value, apb=False, write=False):
    assert value % 16 == 0
    return (value >> 4) | (int(apb) << 29) | (int(write) << 28)


def link(offset, length, offset2=None, length2=None):
    value = END | offset | (length << 8)
    if offset2 is None:
        return value | (1 << 15)
    return value | ((offset2 | (length2 << 8)) << 15)


def reference_probe_and_widths(r):
    assert r.get(BASE + INFO) == 0x7f
    assert r.get(BASE) == 0
    assert r.get(BASE + LISTS) == 8
    r.expect_busy(False)
    r.put(BASE + INFO, 0xffffffff)
    assert r.get(BASE + INFO) == 0x7f
    r.put(r.reg(0, LOCK), 1, 1)
    r.put(r.reg(0, LOCK), 1, 2)
    assert r.read_list(0, LOCK) == 0
    r.put(BASE + 0x1000, 0x12345678)
    assert r.read_list(0, LOCK) == 0


def all_eight_banks_are_independent(r):
    for n in range(8):
        r.write_list(n, LOCK, 1)
        r.write_list(n, LL, 0x6800 + n * 4)
        r.write_list(n, FD, 0x7000 + n * 4)
        r.write_list(n, CFG, 0x200 | (n % 2) * 0x100)
        r.write_list(n, TIMEOUT, 0xff0 + n)
    for n in range(8):
        assert r.read_list(n, LOCK) == 1
        assert r.read_list(n, LL) == 0x6800 + n * 4
        assert r.read_list(n, FD) == 0x7000 + n * 4
        assert r.read_list(n, CFG) == 0x200 | (n % 2) * 0x100
        assert r.read_list(n, TIMEOUT) == 0xff0 + n


def sram_aperture_and_subword_access(r):
    assert r.get(SRAM) == 0
    assert r.get(SRAM + 0x5ffc) == 0
    r.put(SRAM, 0x0102030405060708, 8)
    assert [r.get(SRAM + i, 1) for i in range(8)] == list(range(8, 0, -1))
    r.put(SRAM + 1, 0xab, 1)
    r.put(SRAM + 6, 0xcdef, 2)
    assert r.get(SRAM, 8) == 0xcdef03040506ab08
    r.put(SRAM + 0x5ffc, 0xdeadbeef)
    r.put(SRAM + 0x6000, 0x12345678)
    assert r.get(SRAM + 0x5ffc) == 0xdeadbeef


def actual_dma_from_ram_after_timer(r):
    r.words(IMEM, [0x11111111, 0x22334455, 0xaabbccdd])
    r.program([address(IMEM), link(0, 3), END])
    r.trigger()
    r.expect_busy(True)
    assert r.collect(0x2000, 3) == [0, 0, 0]
    r.step(999)
    assert r.collect(0x2000, 3) == [0, 0, 0]
    # Capture reads current source memory at the timer, not canned probe data.
    r.put(IMEM, 0x87654321)
    r.step(1)
    r.expect_busy(False)
    assert r.collect(0x2000, 3) == [0x87654321, 0x22334455, 0xaabbccdd]


def two_link_pairs_and_cumulative_offsets(r):
    r.words(IMEM, list(range(0x40, 0x60)))
    r.program([address(IMEM), link(2, 3, 5, 2), link(3, 2), END])
    r.trigger()
    r.step()
    r.expect_busy(False)
    assert r.collect(0x2000, 7) == [0x42, 0x43, 0x44, 0x49, 0x4a, 0x4d, 0x4e]


def multiple_addresses_and_device_mmio(r):
    r.put(IMEM, 0xdeadbeef)
    r.put(HOLD, 1)
    r.program([address(IMEM), link(0, 1),
               address(HOLD, apb=True), link(0, 1), END])
    r.trigger()
    r.step()
    r.expect_busy(False)
    assert r.collect(0x2000, 2) == [0xdeadbeef, 1]
    r.put(HOLD, 0)
    r.trigger()
    r.step()
    assert r.collect(0x2000, 2) == [0xdeadbeef, 0]


def separate_lists_capture_independently(r):
    r.words(IMEM, [11, 22, 33, 44])
    r.program([address(IMEM), link(0, 2), END], n=0, ll=0x100, fd=0x2000)
    r.program([address(IMEM), link(2, 2), END], n=7, ll=0x200, fd=0x2100)
    r.trigger(0)
    r.step(400)
    r.trigger(7)
    r.step(600)
    assert r.collect(0x2000, 2) == [11, 22]
    assert r.collect(0x2100, 2) == [0, 0]
    r.expect_busy(True)
    r.step(400)
    r.expect_busy(False)
    assert r.collect(0x2100, 2) == [33, 44]


def writes_to_pending_configuration_rejected(r):
    r.words(IMEM, [11, 22])
    r.program([address(IMEM), link(0, 2), END])
    ll, fd = r.read_list(0, LL), r.read_list(0, FD)
    r.trigger()
    r.write_list(0, LL, 0xffffffff)
    r.write_list(0, FD, 0xffffffff)
    r.write_list(0, CFG, 1)  # CRC changes cannot rewrite an in-flight capture.
    r.write_list(0, TRIGGER, 1)
    assert r.read_list(0, LL) == ll
    assert r.read_list(0, FD) == fd
    assert r.read_list(0, CFG) == 0x200
    r.step()
    assert r.collect(0x2000, 2) == [11, 22]


def assert_stalled(r):
    r.step(1000000)
    r.expect_busy(True)
    assert r.collect(0x2000, 2) == [0, 0]
    assert r.read_list(0, INT_STATUS) == 0


def unsupported_modes_never_complete(r):
    for cfg in (1, 0x10, 0x80000000):
        r.reset_board()
        r.program([address(IMEM), link(0, 1), END], cfg=cfg)
        r.trigger()
        assert_stalled(r)


def unsupported_descriptors_never_complete(r):
    for descriptor in (address(IMEM, write=True), 0x40010002, 0x80000000):
        r.reset_board()
        r.program([descriptor, END])
        r.trigger()
        assert_stalled(r)


def invalid_pointer_and_output_overlap(r):
    for field, value in ((LL, 0), (LL, 0x8000), (FD, 0), (FD, 0x8000),
                         (FD, (INTERNAL + 0x100) // 4)):
        r.reset_board()
        r.program([address(IMEM), link(0, 2), END])
        r.write_list(0, field, value)
        r.trigger()
        assert_stalled(r)
    r.reset_board()
    r.program([address(IMEM), link(0, 2), END], fd=0x5ffc)
    r.trigger()
    assert_stalled(r)


def bad_lists_and_source_fault_do_not_succeed(r):
    for descriptors in ([link(0, 1), END],
                        [address(IMEM), END | 1, END],
                        [address(0x40000000), link(0, 1), END]):
        r.reset_board()
        r.program(descriptors)
        r.trigger()
        assert_stalled(r)
    r.reset_board()
    r.program([address(IMEM)], ll=0x5ffc)
    r.trigger()
    assert_stalled(r)


def unimplemented_source_register_fails_without_zero_payload(r):
    sentinel = [0xa5a5a5a5, 0x5a5a5a5a]
    r.words(SRAM + 0x2000, sentinel)
    # HW_INFO and version are defined reference IDs; SRAM_SIZE_INFO is not.
    # Capturing an unimplemented DCC register must propagate a bus failure.
    r.program([address(BASE), link(2, 1), END])
    r.trigger()
    r.step(1000000)
    r.expect_busy(True)
    assert r.collect(0x2000, 2) == sentinel
    assert r.read_list(0, INT_STATUS) == 0


def empty_list_and_unowned_trigger(r):
    r.program([END])
    r.trigger()
    r.step()
    r.expect_busy(False)
    assert r.collect(0x2000, 2) == [0, 0]
    r.write_list(0, LOCK, 0)
    r.trigger()
    assert_stalled(r)


def reset_cancels_and_retains_sram(r):
    r.put(IMEM, 0x12121212)
    r.program([address(IMEM), link(0, 1), END])
    r.put(SRAM + 0x4000, 0x12345678)
    r.trigger()
    r.step(400)
    r.reset_board()
    r.step(1000000)
    assert r.collect(0x2000, 1) == [0]
    assert r.get(SRAM + 0x4000) == 0x12345678
    assert r.read_list(0, LOCK) == 0
    assert r.read_list(0, LL) == 0
    assert r.read_list(0, FD) == 0


def release_cancels_pending_model_work(r):
    r.program([address(IMEM), link(0, 1), END])
    r.trigger()
    r.write_list(0, LOCK, 0)
    r.step(1000000)
    r.expect_busy(False)
    assert r.collect(0x2000, 1) == [0]


def migrate_pending_capture(args, directory):
    source = target = None
    sd, td = directory / "s", directory / "d"
    sd.mkdir()
    td.mkdir()
    try:
        source, target = DccRig(args, sd), DccRig(args, td, incoming=True)
        source.words(IMEM, [0xaabbccdd, 0x12345678])
        source.program([address(IMEM), link(0, 2), END])
        source.trigger()
        source.step(400)
        source.expect_busy(True)
        source.monitor("stop")
        # Each QTest process owns its synthetic clock; align the destination
        # before loading the real VMState absolute timer deadline.
        target.step(400)
        uri = "unix:" + str(directory / "migrate")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        target.expect_busy(True)
        assert not target.monitor("query-status")["running"]
        assert target.collect(0x2000, 2) == [0, 0]
        target.monitor("cont")
        target.step(599)
        assert target.collect(0x2000, 2) == [0, 0]
        target.step(1)
        target.expect_busy(False)
        assert target.collect(0x2000, 2) == [0xaabbccdd, 0x12345678]
        return {"source_status": sm["status"], "target_status": tm["status"],
                "pending_capture_and_sram_preserved": True,
                "qtest_virtual_ns_at_transfer": 400}
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
    a.base, a.qom_path = BASE, "/machine/dcc"
    a.machine = "tb371fc,ufs-enabled=off"
    a.extra_qemu_args = ["-d", "unimp,guest_errors"]
    a.out.mkdir(parents=True, exist_ok=False)
    checks = []
    report = {"schema_version": 1, "suite": "Qualcomm DCC v2 SRAM capture subset",
              "qemu_sha256": hashlib.sha256(Path(a.qemu).read_bytes()).hexdigest(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Reference map2 identity; real read-descriptor DMA, bounded failures and component migration; no CRC/CTI/IRQ claim",
              "checks": checks, "passed": False}
    start = time.monotonic()
    tests = [reference_probe_and_widths, all_eight_banks_are_independent,
             sram_aperture_and_subword_access, actual_dma_from_ram_after_timer,
             two_link_pairs_and_cumulative_offsets, multiple_addresses_and_device_mmio,
             separate_lists_capture_independently, writes_to_pending_configuration_rejected,
             unsupported_modes_never_complete, unsupported_descriptors_never_complete,
             invalid_pointer_and_output_overlap, bad_lists_and_source_fault_do_not_succeed,
             unimplemented_source_register_fails_without_zero_payload,
             empty_list_and_unowned_trigger, reset_cancels_and_retains_sram,
             release_cancels_pending_model_work, migrate_pending_capture]
    for index, test in enumerate(tests):
        directory = a.out / str(index)
        directory.mkdir()
        rig = None
        try:
            if test == migrate_pending_capture:
                details = test(a, directory)
            else:
                rig = DccRig(a, directory)
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
