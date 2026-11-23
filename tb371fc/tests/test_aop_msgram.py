#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise AOP message SRAM, absent-peer behavior, reset, and real migration."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

BASE, SIZE = 0x0c300000, 0x1000
IPCC, SEND, RECV, ENABLE = 0x00408000, 0x0c, 0x10, 0x14
NONE, UP, DOWN = 0xffffffff, 0x0000ffff, 0xffff0000
TX, RX = 0x100, 0x200
EXPECTED_CHECKS = 10

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class MsgramRig(Rig):
    def command(self, text):
        if text.startswith("irq_intercept_out "):
            # SRAM has no IRQ; retain the real IPCC -> GIC parent connection.
            return []
        return super().command(text)

    def get(self, address, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        return int(self.command(f"read{suffix} {address:#x}")[0], 0)

    def put(self, address, value, width=4):
        suffix = {1: "b", 2: "w", 4: "l", 8: "q"}[width]
        self.command(f"write{suffix} {address:#x} {value:#x}")

    def bytes_at(self, offset, size):
        response = self.command(f"read {BASE + offset:#x} {size:#x}")
        assert len(response) == 1 and response[0].startswith("0x"), response
        data = bytes.fromhex(response[0][2:])
        assert len(data) == size, (len(data), size)
        return data

    def store(self, offset, data):
        self.command(f"write {BASE + offset:#x} {len(data):#x} 0x{data.hex()}")

    def ipcc_stat(self, name):
        return self.monitor("qom-get", {
            "path": "/machine/ipcc", "property": name,
        })

    def reset_board(self):
        # A nonzero unrelated device counter witnesses that reset completed.
        self.put(IPCC + SEND, 0)
        assert self.ipcc_stat("transmit-count") > 0
        self.monitor("system_reset")
        self.wait_for(lambda: self.ipcc_stat("transmit-count") == 0,
                      "IPCC reset completion")
        assert self.get(IPCC + RECV) == NONE

    def advance(self, ns=1_000_000_000):
        self.monitor("cont")
        self.command(f"clock_step {ns}")


def pattern():
    return bytes(((i * 73 + 19) ^ (i >> 4)) & 0xff for i in range(SIZE))


def reference_descriptor():
    # Deliberately constructed test data, never a default/captured AOP profile.
    return struct.pack("<16I", 0x4d41494c, 1, 0,
                       UP, DOWN, UP, DOWN, 0x80, RX,
                       UP, DOWN, UP, DOWN, 0x80, TX, 0)


def pending_image():
    data = bytearray(pattern())
    data[:64] = reference_descriptor()
    payload = b"{class: clock, res: qdss, val: 1}\0"
    payload += bytes((-len(payload)) % 4)
    struct.pack_into("<I", data, TX, len(payload))
    data[TX + 4:TX + 4 + len(payload)] = payload
    struct.pack_into("<I", data, RX, 12)
    data[RX + 4:RX + 16] = b"pending-rx!!"
    return bytes(data)


def cold_zero_is_protocol_unready(r):
    assert r.bytes_at(0, SIZE) == bytes(SIZE)
    assert [r.get(BASE + i, 8) for i in range(0, 64, 8)] == [0] * 8
    assert r.get(IPCC + RECV) == NONE
    return {"cold_zero_is_model_convention": True,
            "firmware_descriptor_present": False}


def full_aperture_stores_all_bytes(r):
    data = pattern()
    r.store(0, data)
    assert r.bytes_at(0, SIZE) == data
    # A second pattern catches storage that only recognizes one fixture.
    inverse = bytes(x ^ 0xff for x in data)
    r.store(0, inverse)
    assert r.bytes_at(0, SIZE) == inverse
    return {"bytes_compared_per_pattern": SIZE, "patterns": 2}


def subword_and_64bit_little_endian(r):
    r.put(BASE + 0x80, 0x0102030405060708, 8)
    assert [r.get(BASE + 0x80 + i, 1) for i in range(8)] == list(range(8, 0, -1))
    r.put(BASE + 0x81, 0xaa, 1)
    r.put(BASE + 0x84, 0xbbcc, 2)
    r.put(BASE + 0x88, 0x10203040, 4)
    assert r.get(BASE + 0x80, 8) == 0x0102bbcc0506aa08
    assert r.bytes_at(0x88, 4) == bytes.fromhex("40302010")
    return {"access_widths": [1, 2, 4, 8],
            "scope": "QEMU RAM contract; no silicon bus-width or atomicity claim"}


def unaligned_ram_accesses_preserve_neighbors(r):
    data = bytearray(pattern())
    r.store(0, data)
    for offset, width, value in ((3, 8, 0x1020304050607080),
                                 (31, 4, 0xaabbccdd), (63, 2, 0x1234)):
        r.put(BASE + offset, value, width)
        data[offset:offset + width] = value.to_bytes(width, "little")
        assert r.get(BASE + offset, width) == value
        assert r.bytes_at(0, SIZE) == bytes(data)
    return {"scope": "QTest RAM transactions; no ARM alignment-policy claim"}


def boundary_writes_do_not_alias(r):
    data = pattern()
    r.store(0, data)
    r.put(BASE - 4, 0xaabbccdd)
    r.put(BASE + SIZE, 0x11223344)
    assert r.get(BASE - 4) != 0xaabbccdd
    assert r.get(BASE + SIZE) != 0x11223344
    assert r.bytes_at(0, SIZE) == data
    r.put(BASE + SIZE - 8, 0x8877665544332211, 8)
    assert r.get(BASE, 8) == int.from_bytes(data[:8], "little")
    assert r.bytes_at(SIZE - 8, 8) == bytes.fromhex("1122334455667788")
    return {"first_byte": BASE, "last_byte": BASE + SIZE - 1}


def descriptor_is_storage_not_auto_handshake(r):
    data = reference_descriptor()
    r.store(0, data)
    words = [r.get(BASE + i, 8) for i in range(0, 64, 8)]
    assert b"".join(x.to_bytes(8, "little") for x in words) == data
    assert r.get(BASE + 0x28) == DOWN
    assert r.get(BASE + 0x30) == DOWN
    assert r.get(IPCC + RECV) == NONE
    return {"descriptor_bytes_read": 64, "auto_acknowledged": False}


def apss_doorbell_does_not_ack_or_consume(r):
    data = pending_image()
    r.store(0, data)
    r.put(IPCC + ENABLE, 0)  # Enable actual AOP client 0, signal 0.
    before = r.ipcc_stat("transmit-count")
    for _ in range(2):
        r.put(IPCC + SEND, 0)
    assert r.bytes_at(0, SIZE) == data
    # Also prefill a connected reference descriptor explicitly. A plausible
    # packet still requires actual remote execution, not automatic completion.
    connected = bytearray(data)
    for offset in (0x10, 0x18, 0x28, 0x30):
        struct.pack_into("<I", connected, offset, UP)
    data = bytes(connected)
    r.store(0, data)
    for _ in range(2):
        r.put(IPCC + SEND, 0)
    assert r.ipcc_stat("transmit-count") == before + 4
    assert r.ipcc_stat("last-transmit") == 0
    assert r.get(IPCC + RECV) == NONE
    assert r.bytes_at(0, SIZE) == data
    # No intercepted IRQ output: inspect the real GIC's SPI229 pending bit.
    pending = r.get(0x17a00000 + 0x200 + (261 // 32) * 4)
    assert pending & (1 << (261 % 32)) == 0, hex(pending)
    return {"actual_ipcc_sends": 4, "receive_pending": False,
            "gic_intid": 261, "gic_pending": False,
            "output_interception_installed": False}


def elapsed_time_does_not_execute_firmware(r):
    data = pending_image()
    r.store(0, data)
    r.advance()
    assert r.bytes_at(0, SIZE) == data
    assert r.get(IPCC + RECV) == NONE
    return {"elapsed_virtual_ns": 1_000_000_000, "message_consumed": False}


def warm_reset_retains_entire_sram(r):
    data = pending_image()
    r.store(0, data)
    r.reset_board()
    assert r.bytes_at(0, SIZE) == data
    return {"retained_bytes": SIZE,
            "retention_is_model_convention": True}


def actual_migration(args, directory):
    source = target = None
    try:
        sd, td = directory / "src", directory / "dst"
        sd.mkdir()
        td.mkdir()
        source = MsgramRig(args, sd)
        target = MsgramRig(args, td, incoming=True)
        data = pending_image()
        source.store(0, data)
        source.put(IPCC + ENABLE, 0)
        source.put(IPCC + SEND, 0)
        source.advance(17_000_000)
        assert target.bytes_at(0, SIZE) == bytes(SIZE)
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert sm["status"] == tm["status"] == "completed", (sm, tm)
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert target.bytes_at(0, SIZE) == data
        assert target.ipcc_stat("transmit-count") == 1
        assert target.get(IPCC + RECV) == NONE
        target.advance()
        assert target.bytes_at(0, SIZE) == data
        target.reset_board()
        assert target.bytes_at(0, SIZE) == data
        target.put(BASE + SIZE - 1, 0x5a, 1)
        assert target.bytes_at(0, SIZE) == data[:-1] + b"\x5a"
        return {"source_status": sm["status"], "target_status": tm["status"],
                "transferred_bytes": SIZE, "source_contents_sha256": digest(data),
                "pending_message_preserved": True, "no_replay_after_time": True,
                "post_migration_reset_retained": True,
                "post_migration_ram_writable": True}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = str(args.qemu.resolve())
    args.machine = "tb371fc,ufs-enabled=off"
    args.base, args.qom_path = BASE, "/machine/aop-msgram"
    args.out.mkdir(parents=True, exist_ok=False)
    tests = [cold_zero_is_protocol_unready, full_aperture_stores_all_bytes,
             subword_and_64bit_little_endian,
             unaligned_ram_accesses_preserve_neighbors,
             boundary_writes_do_not_alias,
             descriptor_is_storage_not_auto_handshake,
             apss_doorbell_does_not_ack_or_consume,
             elapsed_time_does_not_execute_firmware,
             warm_reset_retains_entire_sram, actual_migration]
    assert len(tests) == EXPECTED_CHECKS
    report = {"schema_version": 1, "suite": "AOP message SRAM without peer",
              "machine": args.machine, "qemu_sha256": digest(Path(args.qemu).read_bytes()),
              "test_sha256": digest(Path(__file__).read_bytes()),
              "expected_checks": EXPECTED_CHECKS,
              "scope": "Captured SRAM aperture; no AOP descriptor initialization or firmware execution",
              "output_interception_installed": False,
              "checks": [], "migrations": [], "passed": False, "all_passed": False}
    start = time.monotonic()
    for index, test in enumerate(tests):
        directory = args.out / f"{index:02d}"
        directory.mkdir()
        rig = None
        try:
            if test is actual_migration:
                details = test(args, directory)
                report["migrations"].append(details)
            else:
                rig = MsgramRig(args, directory)
                details = test(rig)
            report["checks"].append({"name": test.__name__, "passed": True, **details})
        except Exception:
            report["checks"].append({"name": test.__name__, "passed": False,
                                     "error": traceback.format_exc()})
        finally:
            if rig:
                rig.close()
        report["elapsed_seconds"] = round(time.monotonic() - start, 3)
        (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    passed = (len(report["checks"]) == EXPECTED_CHECKS and
              all(c["passed"] is True for c in report["checks"]) and
              len(report["migrations"]) == 1)
    report["passed"] = report["all_passed"] = passed
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
