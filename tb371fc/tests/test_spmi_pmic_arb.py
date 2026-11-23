#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the explicit v5 SPMI reference through actual QEMU MMIO and migration."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

CORE, RW, OBS, INTR, CNFG = 0x0c440000, 0x0c600000, 0x0e600000, 0x0e700000, 0x0c40a000
CMD, STATUS, WDATA, RDATA = 0, 8, 0x10, 0x18
ACC, IRQ_STATUS, IRQ_CLEAR = 0x100, 0x104, 0x108
DONE, FAILURE, DENIED, DROPPED = 1, 3, 5, 9
TEST_APID = 4
ROM = b"TB371FC\x01"

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


class SpmiRig(Rig):
    def __init__(self, args, directory, incoming=False):
        super().__init__(args, directory, incoming=incoming)
        self.ee = getattr(args, "ee", 0)
        if not incoming:
            self.monitor("cont")

    def get(self, address, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        return int(self.command(f"read{suffix} {address:#x}")[0], 0)

    def put(self, address, value, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        self.command(f"write{suffix} {address:#x} {value:#x}")

    def step(self, ns=1000):
        return int(self.command(f"clock_step {ns}")[0], 0)

    def bank(self, apid, read=True, ee=None):
        if read:
            return OBS + 0x10000 * (self.ee if ee is None else ee) + 0x80 * apid
        return RW + 0x10000 * apid

    def start(self, apid, offset, length, data=None, opcode=None, ee=None):
        bank = self.bank(apid, data is None, ee)
        if data is not None:
            payload = bytes(data).ljust(8, b"\0")
            assert len(data) == length and length <= 8
            self.put(bank + WDATA, int.from_bytes(payload[:4], "little"))
            self.put(bank + WDATA + 4, int.from_bytes(payload[4:], "little"))
        op = (1 if data is None else 0) if opcode is None else opcode
        self.put(bank + CMD, (op << 27) | (offset << 4) | (length - 1))
        return bank

    def transfer(self, apid, offset, length, data=None, expected=DONE, opcode=None, ee=None):
        bank = self.start(apid, offset, length, data, opcode, ee)
        assert self.get(bank + STATUS) == 0, "completion must wait for virtual time"
        self.step()
        status = self.get(bank + STATUS)
        assert status == expected, (hex(bank), hex(offset), length, status, expected)
        received = (self.get(bank + RDATA).to_bytes(4, "little") +
                    self.get(bank + RDATA + 4).to_bytes(4, "little"))
        if data is None and expected != DONE:
            assert received == bytes(8), "failed transaction published stale/partial data"
        return received[:length]

    def read_bytes(self, offset, length=1, apid=TEST_APID):
        return self.transfer(apid, offset, length)

    def write_bytes(self, offset, data, apid=TEST_APID, expected=DONE):
        return self.transfer(apid, offset, len(data), bytes(data), expected)

    def irq_level(self, level):
        self.get(CORE)
        assert self.irq[0] == level, (self.irq, level)

    def input(self, pin, level):
        self.command(f"set_irq_in {self.args.qom_path} test-irq {pin} {int(level)}")

    def configure_irq(self, irq_type=1, high=1, low=0, mask=1, apid=TEST_APID):
        self.write_bytes(0x11, bytes([irq_type, high, low]), apid)
        self.put(self.bank(apid, False) + ACC, 1)
        # Exact Linux unmask sequence: peripheral LATCH_CLEAR + ENABLE_SET.
        self.write_bytes(0x14, bytes([mask, mask]), apid)

    def ack(self, mask=1, apid=TEST_APID):
        # Exact Linux ACK order: arbiter first, peripheral second.
        self.put(self.bank(apid, False) + IRQ_CLEAR, mask)
        self.write_bytes(0x14, bytes([mask]), apid)

    def reset_board(self):
        self.monitor("system_reset")
        self.wait_for(lambda: self.get(self.bank(TEST_APID, False) + STATUS) == 0 and
                      self.get(self.bank(TEST_APID, False) + ACC) == 0,
                      "SPMI reset completion")
        self.irq_level(False)


def reference_geometry_and_sparse_table(r):
    assert r.get(CORE) == 0x50000000
    assert r.get(CORE + 4) == 512
    for apid, ppid in enumerate((0x001, 0x201, 0x401, 0xa01, 0xffe, 0xffd)):
        assert r.get(CORE + 0x900 + 4 * apid) == (ppid << 8) | ((apid >= 4) << 24)
        assert r.get(CNFG + 0x700 + 4 * apid) == int(apid == 5)
    for apid in (6, 127, 255, 511):
        assert r.get(CORE + 0x900 + 4 * apid) == 0
        assert r.get(CNFG + 0x700 + 4 * apid) == 0
    assert r.get(INTR) == 0


def identity_bytes_have_bounded_provenance(r):
    for apid, subtype in enumerate((0x1e, 0x20, 0x1f, 0x24)):
        assert r.transfer(apid, 4, 2) == bytes([0x51, subtype])
    assert r.transfer(0, 2, 4) == bytes([0, 3, 0x51, 0x1e])


def unknown_identity_is_failure_without_partial_data(r):
    r.transfer(0, 2, 4)
    for apid, offset, length in ((0, 0, 1), (0, 1, 4), (0, 4, 3),
                                 (1, 2, 4), (2, 3, 1), (3, 0xf2, 1)):
        r.transfer(apid, offset, length, expected=FAILURE)


def read_only_identity_write_fails(r):
    for apid in range(4):
        r.transfer(apid, 4, 2, b"\xaa\xbb", expected=FAILURE)
    identity_bytes_have_bounded_provenance(r)


def one_to_eight_byte_read_packing(r):
    for length in range(1, 9):
        assert r.transfer(TEST_APID, 0, length) == ROM[:length]
        bank = r.bank(TEST_APID)
        actual = (r.get(bank + RDATA).to_bytes(4, "little") +
                  r.get(bank + RDATA + 4).to_bytes(4, "little"))
        assert actual == ROM[:length].ljust(8, b"\0")
    assert r.transfer(TEST_APID, 3, 5) == ROM[3:]


def writes_change_real_irq_register_state(r):
    r.write_bytes(0x11, bytes([0x93, 0x55, 0x66]))
    assert r.read_bytes(0x11, 3) == bytes([0x93, 0x55, 0x66])
    # Six defined writable bytes exercise both WDATA words atomically.
    r.write_bytes(0x11, bytes([0xff, 0xa5, 0x5a, 0xff, 0xf3, 0x13]))
    assert r.read_bytes(0x11, 3) == bytes([0xff, 0xa5, 0x5a])
    assert r.read_bytes(0x15, 2) == bytes([0xe0, 0xe0])


def completion_waits_and_uses_command_time_data(r):
    bank = r.start(TEST_APID, 0x11, 3, b"\x81\x82\x83")
    assert r.get(bank + STATUS) == 0
    r.put(bank + WDATA, 0xdeadbeef)
    r.step(999)
    assert r.get(bank + STATUS) == 0
    r.step(1)
    assert r.get(bank + STATUS) == DONE
    assert r.read_bytes(0x11, 3) == b"\x81\x82\x83"


def concurrent_apid_observers_are_independent(r):
    a = r.start(0, 4, 2)
    b = r.start(1, 4, 2)
    assert r.get(a + STATUS) == r.get(b + STATUS) == 0
    r.step()
    assert r.get(a + STATUS) == r.get(b + STATUS) == DONE
    assert r.get(a + RDATA) == 0x1e51
    assert r.get(b + RDATA) == 0x2051


def unsupported_opcodes_do_not_succeed(r):
    for op in (0, 2, 3, 13, 15, 16, 31):
        r.transfer(TEST_APID, 0, 1, opcode=op, expected=FAILURE)
    r.transfer(TEST_APID, 0x11, 1, b"\xff", opcode=1, expected=FAILURE)
    assert r.read_bytes(0x11) == b"\0"


def malformed_command_and_page_boundary_fail(r):
    bank = r.bank(TEST_APID)
    for bad_bit in (3, 12, 20, 26):
        r.put(bank + CMD, (1 << 27) | (1 << bad_bit))
        r.step()
        assert r.get(bank + STATUS) == FAILURE
    r.transfer(TEST_APID, 0xfc, 8, expected=FAILURE)
    r.transfer(TEST_APID, 0xff, 2, expected=FAILURE)


def missing_apids_never_acknowledge(r):
    for apid in (6, 255, 511):
        r.transfer(apid, 0, 8, expected=FAILURE)
        r.transfer(apid, 0x11, 1, b"\xff", expected=FAILURE)


def ownership_and_observer_ee_are_enforced(r):
    r.transfer(5, 0x11, 1, b"\xff", expected=DENIED)
    assert r.transfer(5, 0, 8) == ROM  # Read does not require write ownership.
    r.transfer(TEST_APID, 0, 8, expected=DENIED, ee=1)
    assert r.transfer(TEST_APID, 0, 8) == ROM


def invalid_write_span_is_atomic(r):
    r.write_bytes(0x11, b"\x12\x34\x56")
    for offset, payload in ((0x10, b"\xff\xff"),
                            (0x11, bytes([0xff]) * 7),
                            (0x11, bytes([0xff]) * 8),
                            (0x16, b"\xff\xff")):
        r.write_bytes(offset, payload, expected=FAILURE)
        assert r.read_bytes(0x11, 3) == b"\x12\x34\x56"
        assert r.read_bytes(0x15) == b"\0"


def command_collision_reports_drop_without_mutation(r):
    bank = r.start(TEST_APID, 0x11, 1, b"\xaa")
    r.start(TEST_APID, 0x11, 1, b"\xbb")
    assert r.get(bank + STATUS) == DROPPED
    r.step(10000)
    assert r.get(bank + STATUS) == DROPPED
    assert r.read_bytes(0x11) == b"\0"


def invalid_mmio_widths_and_read_only_registers(r):
    bank = r.bank(TEST_APID, False)
    r.put(bank + WDATA, 0x12345678)
    for width in (1, 2, 8):
        r.put(bank + WDATA, 0xff, width)
        r.put(bank + CMD, 0, width)
    r.put(bank + WDATA + 1, 0x99999999)
    r.put(bank + 4, 0xffffffff)
    r.put(bank + STATUS, DONE)
    r.put(CORE, 0x70000000)
    r.put(CORE + 0x900, 0xffffff00)
    r.put(CNFG + 0x700, 7)
    r.get(CORE + 8)
    r.get(OBS + 0x80000)
    r.get(INTR + 0x40)
    assert r.get(bank + WDATA) == 0x12345678
    assert r.get(bank + STATUS) == 0
    assert r.get(CORE) == 0x50000000
    assert r.get(CORE + 0x900) == 0x100
    assert r.get(CNFG + 0x700) == 0
    assert "unsupported write" in r.log_path.read_text()
    assert "unsupported read" in r.log_path.read_text()


def linux_unmask_discards_disabled_stale_edge(r):
    r.write_bytes(0x11, b"\x01\x01\0")
    r.input(0, 1)
    assert r.read_bytes(0x18) == b"\x01"
    r.irq_level(False)
    r.put(r.bank(TEST_APID, False) + ACC, 1)
    r.write_bytes(0x14, b"\x01\x01")
    assert r.read_bytes(0x18) == b"\0"
    r.irq_level(False)
    r.input(0, 0)
    r.input(0, 1)
    r.irq_level(True)
    assert r.get(INTR) == 1 << TEST_APID


def linux_edge_acknowledges_both_latches(r):
    r.configure_irq()
    r.input(0, 1)
    r.irq_level(True)
    bank = r.bank(TEST_APID, False)
    r.put(bank + IRQ_CLEAR, 1)
    r.irq_level(False)
    assert r.read_bytes(0x18) == b"\x01"
    r.write_bytes(0x14, b"\x01")
    assert r.read_bytes(0x18) == b"\0"
    r.irq_level(False)
    r.input(0, 0)
    r.input(0, 1)
    r.write_bytes(0x14, b"\x01")
    r.irq_level(True)  # Peripheral ACK alone does not clear arbiter latch.
    r.put(bank + IRQ_CLEAR, 1)
    r.irq_level(False)


def level_interrupt_reasserts_until_source_deasserts(r):
    r.configure_irq(irq_type=0)
    r.input(0, 1)
    r.irq_level(True)
    bank = r.bank(TEST_APID, False)
    r.put(bank + IRQ_CLEAR, 1)
    r.irq_level(False)
    r.write_bytes(0x14, b"\x01")
    r.irq_level(True)
    assert r.read_bytes(0x10) == b"\x01"
    r.input(0, 0)
    r.ack()
    r.irq_level(False)
    assert r.read_bytes(0x10) == r.read_bytes(0x18) == b"\0"


def falling_and_both_edge_polarities(r):
    r.configure_irq(irq_type=1, high=0, low=1)
    r.input(0, 1)
    r.irq_level(False)
    r.input(0, 0)
    r.irq_level(True)
    r.ack()
    r.write_bytes(0x11, b"\x01\x01\x01")
    for level in (1, 0, 1, 0):
        r.input(0, level)
        r.irq_level(True)
        r.ack()
        r.irq_level(False)


def peripheral_and_arbiter_enable_gates(r):
    r.configure_irq()
    r.input(0, 1)
    r.irq_level(True)
    bank = r.bank(TEST_APID, False)
    r.write_bytes(0x16, b"\x01")
    r.irq_level(False)
    assert r.read_bytes(0x18) == b"\x01"
    assert r.get(bank + IRQ_STATUS) == 1
    assert r.get(INTR) == 0
    r.write_bytes(0x15, b"\x01")
    r.irq_level(True)
    r.put(bank + ACC, 0)
    r.irq_level(False)
    assert r.get(bank + IRQ_STATUS) == 1 and r.get(INTR) == 0
    r.put(bank + ACC, 1)
    r.irq_level(True)


def irq_bits_and_w1c_are_independent(r):
    r.configure_irq(irq_type=0xff, high=0xff, mask=0xff)
    r.input(0, 1)
    r.input(7, 1)
    bank = r.bank(TEST_APID, False)
    assert r.get(bank + IRQ_STATUS) == 0x81
    r.ack(mask=1)
    assert r.get(bank + IRQ_STATUS) == 0x80
    assert r.read_bytes(0x18) == b"\x80"
    r.irq_level(True)
    r.ack(mask=0x80)
    assert r.get(bank + IRQ_STATUS) == 0
    r.irq_level(False)


def reset_cancels_pending_and_preserves_physical_input(r):
    r.configure_irq()
    r.input(0, 1)
    r.start(TEST_APID, 0x11, 1, b"\xff")
    r.step(400)
    r.reset_board()
    r.step(10000)
    assert r.get(r.bank(TEST_APID, False) + STATUS) == 0
    assert r.get(r.bank(TEST_APID, False) + IRQ_STATUS) == 0
    assert r.read_bytes(0x10, 4) == b"\x01\0\0\0"
    assert r.read_bytes(0x15) == r.read_bytes(0x18) == b"\0"
    r.irq_level(False)


def synthetic_endpoints_are_opt_in(args, directory):
    a = copy.copy(args)
    a.extra_qemu_args = ["-d", "unimp,guest_errors"]
    r = None
    try:
        r = SpmiRig(a, directory)
        assert r.get(CORE + 0x900 + 4 * TEST_APID) == 0
        r.transfer(TEST_APID, 0, 8, expected=FAILURE)
        identity_bytes_have_bounded_provenance(r)
        return {"synthetic_peripherals_enabled": False}
    finally:
        if r:
            r.close()


def execution_environment_one_has_separate_ownership(args, directory):
    a = copy.copy(args)
    a.ee = 1
    a.extra_qemu_args = [*args.extra_qemu_args, "-global",
                         "qcom-spmi-pmic-arb.execution-environment=1"]
    r = None
    try:
        r = SpmiRig(a, directory)
        r.transfer(TEST_APID, 0x11, 1, b"\xff", expected=DENIED)
        assert r.transfer(0, 4, 2) == b"\x51\x1e"
        r.configure_irq(apid=5)
        r.input(8, 1)
        r.irq_level(True)
        assert r.get(INTR) == 0
        assert r.get(INTR + 0x10000) == 1 << 5
        r.ack(apid=5)
        r.irq_level(False)
        return {"execution_environment": 1, "irq_owner_apid": 5}
    finally:
        if r:
            r.close()


def migrate_pending_transaction_and_irq(args, directory):
    source = target = None
    sd, td = directory / "s", directory / "d"
    sd.mkdir()
    td.mkdir()
    try:
        source, target = SpmiRig(args, sd), SpmiRig(args, td, incoming=True)
        source.configure_irq()
        source.input(0, 1)
        source.irq_level(True)
        bank = source.start(TEST_APID, 0x16, 1, b"\x01")
        now = source.step(400)
        assert source.get(bank + STATUS) == 0
        source.monitor("stop")
        target.step(now)
        uri = "unix:" + str(directory / "migrate")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert not target.monitor("query-status")["running"]
        assert target.get(bank + STATUS) == 0
        assert target.get(bank + IRQ_STATUS) == 1
        target.irq_level(True)
        target.monitor("cont")
        target.step(599)
        assert target.get(bank + STATUS) == 0
        target.irq_level(True)
        target.step(1)
        assert target.get(bank + STATUS) == DONE
        target.irq_level(False)
        assert target.read_bytes(0x10) == b"\x01"
        assert target.read_bytes(0x15) == b"\0"
        assert target.read_bytes(0x18) == b"\x01"
        return {"source_status": sm["status"], "target_status": tm["status"],
                "pending_data_and_irq_preserved": True,
                "qtest_virtual_ns_at_transfer": now}
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
    a.base, a.qom_path, a.ee = CORE, "/machine/spmi", 0
    a.machine = "tb371fc,ufs-enabled=off,spmi-profile=reference"
    a.extra_qemu_args = ["-global", "qcom-spmi-pmic-arb.test-peripheral=on",
                         "-d", "unimp,guest_errors"]
    a.out.mkdir(parents=True, exist_ok=False)
    tests = [reference_geometry_and_sparse_table,
             identity_bytes_have_bounded_provenance,
             unknown_identity_is_failure_without_partial_data,
             read_only_identity_write_fails, one_to_eight_byte_read_packing,
             writes_change_real_irq_register_state,
             completion_waits_and_uses_command_time_data,
             concurrent_apid_observers_are_independent,
             unsupported_opcodes_do_not_succeed,
             malformed_command_and_page_boundary_fail,
             missing_apids_never_acknowledge,
             ownership_and_observer_ee_are_enforced,
             invalid_write_span_is_atomic,
             command_collision_reports_drop_without_mutation,
             invalid_mmio_widths_and_read_only_registers,
             linux_unmask_discards_disabled_stale_edge,
             linux_edge_acknowledges_both_latches,
             level_interrupt_reasserts_until_source_deasserts,
             falling_and_both_edge_polarities,
             peripheral_and_arbiter_enable_gates,
             irq_bits_and_w1c_are_independent,
             reset_cancels_pending_and_preserves_physical_input,
             synthetic_endpoints_are_opt_in,
             execution_environment_one_has_separate_ownership,
             migrate_pending_transaction_and_irq]
    special = {synthetic_endpoints_are_opt_in,
               execution_environment_one_has_separate_ownership,
               migrate_pending_transaction_and_irq}
    checks = []
    report = {"schema_version": 1, "suite": "SPMI PMIC arbiter v5 reference subset",
              "qemu_sha256": hashlib.sha256(Path(a.qemu).read_bytes()).hexdigest(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Explicit reference VERSION/APID map; real byte transactions, strict failures, synthetic peripheral IRQ and actual migration; no native PMIC analog claim",
              "checks": checks, "passed": False}
    start = time.monotonic()
    for index, test in enumerate(tests):
        directory = a.out / str(index)
        directory.mkdir()
        rig = None
        try:
            if test in special:
                details = test(a, directory)
            else:
                rig = SpmiRig(a, directory)
                test(rig)
                details = {}
            checks.append({"name": test.__name__, "passed": True, **details})
        except Exception:
            checks.append({"name": test.__name__, "passed": False,
                           "error": traceback.format_exc()})
        finally:
            if rig:
                rig.close()
    report["passed"] = len(checks) == len(tests) and all(c["passed"] for c in checks)
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (a.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
