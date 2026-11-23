#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify real IPCC message latches, masks, self routing, IRQ and migration."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

BASE = 0x408000
CONFIG, SEND, RECV, ENABLE, DISABLE, CLEAR, CLIENT_CLEAR = (
    8, 12, 16, 20, 24, 28, 56)
NONE = 0xffffffff

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


def packed(client, signal):
    return (client << 16) | signal


class IpccRig(Rig):
    def __init__(self, args, directory, incoming=False, intercept=True):
        self.use_intercept = intercept
        super().__init__(args, str(directory), incoming=incoming)

    def command(self, command):
        if (not self.use_intercept and
                command == f"irq_intercept_out {self.args.qom_path} sysbus-irq"):
            # Output interception replaces the parent connection. The real-GIC
            # acceptance case must preserve the board's SPI229 connection.
            return []
        return super().command(command)

    def inject(self, client, signal):
        self.monitor("qom-set", {"path": self.args.qom_path,
                                 "property": "qtest-rx-message",
                                 "value": packed(client, signal)})

    def stat(self, name):
        return self.monitor("qom-get", {"path": self.args.qom_path,
                                        "property": name})

    def irq_is(self, expected):
        assert self.use_intercept
        self.read(CONFIG)  # Drain IRQ observations without consuming a message.
        assert self.irq.get(0, False) is expected, self.irq

    def reset_board(self):
        self.monitor("system_reset")
        self.wait_for(lambda: self.read(RECV) == NONE and
                      self.stat("transmit-count") == 0, "IPCC cold reset")
        self.irq_is(False)


def initial_state_and_client_clear(r):
    assert r.read(CONFIG) == 0
    assert r.read(RECV) == NONE
    r.irq_is(False)
    assert r.stat("transmit-count") == 0
    assert r.stat("last-transmit") == NONE
    r.write(CLIENT_CLEAR, 1)
    assert r.read(RECV) == NONE


def apps_self_send_is_real_receive(r):
    ident = packed(8, 2)  # Captured APPS self-ping DT channel.
    r.write(ENABLE, ident)
    r.write(SEND, ident)
    r.irq_is(True)
    assert r.read(RECV) == ident
    assert r.read(RECV) == ident  # Default read does not acknowledge.
    assert r.stat("last-transmit") == ident
    assert r.stat("transmit-count") == 1
    r.write(CLEAR, ident)
    r.irq_is(False)
    assert r.read(RECV) == NONE


def masked_arrival_remains_pending(r):
    ident = packed(8, 2)
    r.write(SEND, ident)
    r.irq_is(False)
    assert r.read(RECV) == NONE
    r.write(ENABLE, ident)
    r.irq_is(True)
    assert r.read(RECV) == ident
    r.write(CLEAR, ident)
    r.irq_is(False)


def explicit_peer_receive_uses_sender_id(r):
    ident = packed(3, 3)
    r.write(ENABLE, ident)
    r.inject(3, 3)
    r.irq_is(True)
    assert r.read(RECV) == ident
    assert r.stat("transmit-count") == 0
    r.write(CLEAR, ident)
    r.irq_is(False)


def remote_send_never_fabricates_reply(r):
    for peer in (0, 1, 2, 3, 4, 6, 7):
        ident = packed(peer, 3)
        r.write(ENABLE, ident)
        r.write(SEND, ident)
        assert r.read(RECV) == NONE
        r.irq_is(False)
        assert r.stat("last-transmit") == ident
    assert r.stat("transmit-count") == 7
    # A separately injected real peer event is required for receive.
    r.inject(3, 3)
    assert r.read(RECV) == packed(3, 3)
    r.irq_is(True)


def masking_and_unmasking_preserve_pending(r):
    ident = packed(6, 3)
    r.write(ENABLE, ident)
    r.inject(6, 3)
    r.irq_is(True)
    r.write(DISABLE, ident)
    r.irq_is(False)
    assert r.read(RECV) == NONE
    r.write(ENABLE, ident)
    r.irq_is(True)
    assert r.read(RECV) == ident


def clear_only_selected_pair(r):
    one, two = packed(3, 1), packed(3, 2)
    for ident in (one, two):
        r.write(ENABLE, ident)
    r.inject(3, 1)
    r.inject(3, 2)
    r.write(CLEAR, packed(7, 1))
    assert r.read(RECV) == one
    r.irq_is(True)
    r.write(CLEAR, one)
    assert r.read(RECV) == two
    r.irq_is(True)
    r.write(CLEAR, two)
    assert r.read(RECV) == NONE
    r.irq_is(False)


def duplicate_messages_coalesce_as_latches(r):
    ident = packed(8, 2)
    r.write(ENABLE, ident)
    for _ in range(3):
        r.write(SEND, ident)
    assert r.stat("transmit-count") == 3
    assert r.read(RECV) == ident
    r.write(CLEAR, ident)
    r.irq_is(False)
    assert r.read(RECV) == NONE


def deterministic_reference_arbitration(r):
    identities = [packed(8, 3), packed(3, 2), packed(3, 1), packed(0, 0)]
    for ident in identities:
        r.write(ENABLE, ident)
        r.inject(ident >> 16, ident & 0xffff)
    for ident in sorted(identities):
        assert r.read(RECV) == ident
        r.write(CLEAR, ident)
    r.irq_is(False)


def clear_on_read_selects_next_message(r):
    one, two = packed(3, 1), packed(8, 2)
    for ident in (one, two):
        r.write(ENABLE, ident)
        r.inject(ident >> 16, ident & 0xffff)
    r.write(CONFIG, 1)
    assert r.read(RECV) == one
    r.irq_is(True)
    assert r.read(RECV) == two
    r.irq_is(False)
    assert r.read(RECV) == NONE
    r.write(CONFIG, 0)
    r.inject(8, 2)
    assert r.read(RECV) == two
    assert r.read(RECV) == two


def protocol_clear_resets_masks_pending_and_config(r):
    ident = packed(8, 2)
    r.write(ENABLE, ident)
    r.write(SEND, ident)
    r.write(CONFIG, 1)
    r.write(CLIENT_CLEAR, 1)
    assert r.read(CONFIG) == 0
    assert r.read(RECV) == NONE
    assert r.stat("transmit-count") == 0
    r.irq_is(False)
    r.write(SEND, ident)
    r.irq_is(False)  # Reset also cleared its enable mask.
    r.write(ENABLE, ident)
    r.irq_is(True)
    assert r.read(RECV) == ident


def full_packed_id_and_zero_id_are_distinct(r):
    for ident in (0, packed(0xffff, 0xfffe), packed(1, 0xffff)):
        r.write(ENABLE, ident)
        r.inject(ident >> 16, ident & 0xffff)
        assert r.read(RECV) == ident
        r.write(CLEAR, ident)
    r.irq_is(False)
    try:
        r.inject(0xffff, 0xffff)
    except AssertionError as error:
        assert "IPCC receive ID" in str(error), str(error)
    else:
        raise AssertionError("No-pending sentinel accepted as an incoming ID")
    assert r.read(RECV) == NONE


def invalid_width_and_configuration_do_not_mutate(r):
    ident = packed(8, 2)
    r.command(f"writeb {BASE + ENABLE:#x} {ident & 0xff}")
    r.command(f"writew {BASE + ENABLE:#x} {ident & 0xffff}")
    r.write(SEND, ident)
    r.irq_is(False)
    r.write(CONFIG, 2)
    assert r.read(CONFIG) == 0
    r.write(ENABLE, ident)
    r.irq_is(True)
    r.write(CLIENT_CLEAR, 2)
    assert r.read(RECV) == ident
    r.write(ENABLE, NONE)
    r.write(SEND, NONE)
    assert r.stat("transmit-count") == 1
    assert r.read(RECV) == ident
    r.write(0x20, 0x12345678)  # Inside this frame, but not an implemented register.
    r.read(0x20)
    assert r.read(RECV) == ident
    diagnostic = r.log_path.read_text()
    assert "unsupported write at 0x20" in diagnostic, diagnostic
    assert "unsupported read at 0x20" in diagnostic, diagnostic
    r.write(0x1000, 0x12345678)  # Separate out-of-frame access.
    assert r.read(RECV) == ident


def bounded_sparse_capacity_preserves_live_entries(r):
    # This is an emulator memory bound, not a claim about physical dimensions.
    for signal in range(4096):
        r.write(ENABLE, packed(0, signal))
    try:
        r.inject(3, 3)
    except AssertionError as error:
        assert "IPCC receive ID" in str(error), str(error)
    else:
        raise AssertionError("A full sparse table silently accepted a new pair")
    r.inject(0, 4095)
    r.irq_is(True)
    assert r.read(RECV) == packed(0, 4095)
    r.write(CLEAR, packed(0, 4095))
    r.write(DISABLE, 0)
    r.write(ENABLE, packed(3, 3))
    r.inject(3, 3)
    assert r.read(RECV) == packed(3, 3)
    r.write(CLEAR, packed(3, 3))
    r.irq_is(False)


def real_gic_spi229_pending_mask_clear_and_deassert(r):
    assert not r.use_intercept
    gicd, intid = 0x17a00000, 32 + 229
    bit = 1 << (intid % 32)
    bank = 4 * (intid // 32)

    def get(address):
        return int(r.command(f"readl {address:#x}")[0], 0)

    def put(address, value):
        r.command(f"writel {address:#x} {value:#x}")

    def pending():
        return bool(get(gicd + 0x200 + bank) & bit)

    def clear_parent():
        put(gicd + 0x280 + bank, bit)

    config, shift = gicd + 0xc00 + 4 * (intid // 16), (intid % 16) * 2
    put(config, get(config) & ~(3 << shift))  # Native DT: level-high.
    assert get(config) & (3 << shift) == 0
    put(gicd + 0x100 + bank, bit)
    put(gicd, get(gicd) | 3)
    clear_parent()
    assert not pending()
    ident = packed(8, 2)
    r.write(ENABLE, ident)
    r.write(SEND, ident)
    assert pending()
    assert r.read(RECV) == ident
    # A masked device retains its message but releases the real parent input.
    r.write(DISABLE, ident)
    clear_parent()
    assert not pending()
    r.write(ENABLE, ident)
    assert pending()
    # Device acknowledgement releases the level, allowing the parent latch clear.
    r.write(CLEAR, ident)
    clear_parent()
    assert not pending()
    r.write(DISABLE, ident)
    r.write(SEND, ident)
    assert not pending()
    r.write(ENABLE, ident)
    assert pending()
    r.write(CONFIG, 1)
    assert r.read(RECV) == ident
    clear_parent()
    assert not pending()
    assert r.read(RECV) == NONE
    return {"gic_spi": 229, "architectural_intid": intid,
            "parent_connection_intercepted": False,
            "self_send_mask_unmask_and_both_ack_paths_reach_gic": True}


def actual_pending_mask_and_irq_migration(args, directory):
    source = target = None
    sd, td = directory / "s", directory / "d"
    sd.mkdir()
    td.mkdir()
    try:
        source, target = IpccRig(args, sd), IpccRig(args, td, incoming=True)
        source.write(ENABLE, packed(8, 2))
        source.write(SEND, packed(8, 2))
        source.inject(0, 0)  # Masked pending pair must survive.
        source.write(SEND, packed(3, 3))  # Unmodeled peer: no receive.
        source.write(CONFIG, 1)  # Nondefault read/ack behavior must migrate.
        source.irq_is(True)
        uri = "unix:" + str(directory / "migration")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        target.irq_is(True)
        assert target.read(CONFIG) == 1
        assert target.read(RECV) == packed(8, 2)
        target.irq_is(False)  # Migrated clear-on-read took effect.
        assert target.stat("transmit-count") == 2
        assert target.stat("last-transmit") == packed(3, 3)
        target.write(CLEAR, packed(8, 2))
        target.irq_is(False)
        target.write(ENABLE, 0)
        target.irq_is(True)
        assert target.read(RECV) == 0
        target.irq_is(False)
        assert target.read(RECV) == NONE
        target.reset_board()
        target.write(SEND, packed(8, 2))
        target.irq_is(False)
        return {"source_status": sm["status"], "target_status": tm["status"],
                "pending_masks_summary_irq_and_tx_state_preserved": True,
                "clear_on_read_configuration_preserved": True}
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
    a.base, a.qom_path = BASE, "/machine/ipcc"
    a.machine = "tb371fc,ufs-enabled=off"
    a.extra_qemu_args = ["-d", "unimp,guest_errors"]
    a.out.mkdir(parents=True, exist_ok=False)
    checks = []
    report = {"schema_version": 1, "suite": "Qualcomm IPCC APPS message frame",
              "qemu_sha256": hashlib.sha256(Path(a.qemu).read_bytes()).hexdigest(),
              "test_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Actual message latches/masks; APPS self-loop; real GIC SPI229 pending/deassert; separate wire-level migration; explicit test peer events; no DSP/firmware reply claim",
              "checks": checks, "passed": False}
    start = time.monotonic()
    tests = [initial_state_and_client_clear, apps_self_send_is_real_receive,
             masked_arrival_remains_pending, explicit_peer_receive_uses_sender_id,
             remote_send_never_fabricates_reply, masking_and_unmasking_preserve_pending,
             clear_only_selected_pair, duplicate_messages_coalesce_as_latches,
             deterministic_reference_arbitration, clear_on_read_selects_next_message,
             protocol_clear_resets_masks_pending_and_config,
             full_packed_id_and_zero_id_are_distinct,
             invalid_width_and_configuration_do_not_mutate,
             bounded_sparse_capacity_preserves_live_entries,
             real_gic_spi229_pending_mask_clear_and_deassert,
             actual_pending_mask_and_irq_migration]
    for index, test in enumerate(tests):
        directory = a.out / str(index)
        directory.mkdir()
        rig = None
        try:
            if test == actual_pending_mask_and_irq_migration:
                details = test(a, directory)
            else:
                rig = IpccRig(
                    a, directory,
                    intercept=test != real_gic_spi229_pending_mask_clear_and_deassert)
                details = test(rig) or {}
            checks.append({"name": test.__name__, "passed": True, **details})
        except Exception:
            checks.append({"name": test.__name__, "passed": False,
                           "error": traceback.format_exc()})
        finally:
            if rig:
                rig.close()
    report["expected_checks"] = len(tests)
    report["passed"] = len(checks) == len(tests) and all(c["passed"] for c in checks)
    report["elapsed_seconds"] = round(time.monotonic() - start, 3)
    (a.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
