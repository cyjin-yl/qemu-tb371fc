#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify the compiled reference AOP peer through real QTest/IPCC and migration."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time
import traceback

from test_geni_uart import Rig
from test_aop_msgram import MsgramRig, BASE, SIZE, IPCC, UP, DOWN, NONE
from test_geni_migration import wait_migration

SEND, RECV, ENABLE, DISABLE, CLEAR = 0x0c, 0x10, 0x14, 0x18, 0x1c
RX, TX, MBOX = 0x100, 0x200, 0x100
WORK, RETRY = 1000, 100000
PEER = "/machine/aop-peer"
GIC_PENDING = 0x17a00000 + 0x200 + (261 // 32) * 4
GIC_CLEAR = 0x17a00000 + 0x280 + (261 // 32) * 4
GIC_BIT = 1 << (261 % 32)
PROFILE = "tb371fc,ufs-enabled=off,aop-profile=reference-qdss-vote"
NONE_PROFILE = "tb371fc,ufs-enabled=off,aop-profile=none"

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")


def digest(data):
    return hashlib.sha256(data).hexdigest()


class AopRig(MsgramRig):
    def __init__(self, args, directory, incoming=False):
        self.virtual_ns = 0
        super().__init__(args, directory, incoming=incoming)

    def command(self, command):
        if command == f"irq_intercept_out {self.args.qom_path} sysbus-irq":
            # Never replace IPCC -> real GIC SPI229 with a test sink.
            return []
        return Rig.command(self, command)

    def stat(self, name):
        return self.monitor("qom-get", {"path": PEER, "property": name})

    def advance(self, ns=WORK):
        self.monitor("cont")
        result = self.command(f"clock_step {ns}")
        self.virtual_ns = int(result[0], 0)

    def kick(self, advance=True, recipient=0, signal=0):
        self.put(IPCC + SEND, (recipient << 16) | signal)
        if advance:
            self.advance()

    def clear_notification(self):
        self.put(IPCC + CLEAR, 0)
        self.put(GIC_CLEAR, GIC_BIT)
        assert self.get(IPCC + RECV) == NONE

    def connect_link(self):
        self.put(IPCC + ENABLE, 0)
        self.put(BASE + 0x10, UP)
        self.put(BASE + 0x24, UP)
        self.kick()
        assert self.get(BASE + 0x28) == UP
        assert self.stat("state") == "link-up"
        assert self.get(IPCC + RECV) == 0
        self.clear_notification()

    def connect_channel(self):
        self.put(BASE + 0x2c, UP)
        self.kick()
        assert self.get(BASE + 0x14) == UP
        assert self.get(BASE + 0x30) == DOWN
        assert self.stat("state") == "channel-opening"
        self.clear_notification()
        self.put(BASE + 0x18, UP)
        self.kick()
        assert self.get(BASE + 0x30) == UP
        assert self.stat("state") == "connected"
        self.clear_notification()

    def connect_peer(self):
        self.connect_link()
        self.connect_channel()

    def publish(self, command, size=96, padding=0xa5):
        payload = command.encode("ascii") + b"\0"
        assert len(payload) <= size <= MBOX - 4
        data = payload + bytes([padding]) * (size - len(payload))
        self.store(TX + 4, data)
        self.put(BASE + TX, size)
        return data

    def vote(self, resource, value, size=96):
        data = self.publish(f"{{class: clock, res: {resource}, val: {value}}}",
                            size=size)
        before = self.stat("accepted-messages")
        self.kick()
        assert self.get(BASE + TX) == 0
        assert self.stat("accepted-messages") == before + 1
        assert self.stat("last-error") == "none"
        assert self.get(IPCC + RECV) == 0
        self.clear_notification()
        return data

    def expect_refused(self, command, reason="command", size=96):
        data = self.publish(command, size=size)
        before = self.stat("accepted-messages")
        rejected = self.stat("rejected-requests")
        notifications = self.stat("notifications")
        votes = [self.stat("qdss-vote"), self.stat("qdss-ao-vote")]
        self.kick()
        assert self.get(BASE + TX) == size
        assert self.bytes_at(TX + 4, size) == data
        assert self.stat("accepted-messages") == before
        assert self.stat("rejected-requests") == rejected + 1
        assert self.stat("last-error") == reason
        assert self.stat("notifications") == notifications
        assert [self.stat("qdss-vote"), self.stat("qdss-ao-vote")] == votes
        assert self.get(IPCC + RECV) == NONE

    def intercept_vote(self, name):
        self.command(f"irq_intercept_out {PEER} {name}")

    def output_is(self, level):
        self.get(BASE)  # Drain asynchronous GPIO notifications.
        assert self.irq.get(0, False) is level, (self.irq, level)


def initialized_reference_descriptor(r):
    expected = (0x4d41494c, 1, 0, UP, DOWN, DOWN, DOWN, MBOX, RX,
                DOWN, DOWN, DOWN, DOWN, MBOX, TX, 0)
    assert struct.unpack("<16I", r.bytes_at(0, 64)) == expected
    assert r.bytes_at(RX, MBOX) == r.bytes_at(TX, MBOX) == bytes(MBOX)
    assert r.stat("state") == "link-down"
    assert r.stat("qdss-vote") is r.stat("qdss-ao-vote") is False
    assert r.stat("accepted-messages") == r.stat("notifications") == 0
    assert r.get(IPCC + RECV) == NONE
    assert not r.get(GIC_PENDING) & GIC_BIT
    # Production memcpy_fromio also uses wide descriptor accesses.
    assert b"".join(r.get(BASE + o, 8).to_bytes(8, "little")
                    for o in range(0, 64, 8)) == struct.pack("<16I", *expected)
    return {"reference_geometry": {"rx": RX, "tx": TX, "mailbox_bytes": MBOX}}


def link_requires_actual_ack_and_doorbell(r):
    r.put(IPCC + ENABLE, 0)
    r.put(BASE + 0x24, UP)
    r.kick()
    assert r.get(BASE + 0x28) == DOWN
    assert r.stat("last-error") == "link"
    assert r.get(IPCC + RECV) == NONE
    r.put(BASE + 0x10, UP)
    r.advance(100 * WORK)
    assert r.get(BASE + 0x28) == DOWN
    r.kick(advance=False)
    assert r.stat("work-pending") is True
    r.advance(WORK - 1)
    assert r.get(BASE + 0x28) == DOWN
    r.advance(1)
    assert r.get(BASE + 0x28) == UP
    assert r.stat("state") == "link-up"
    assert r.get(IPCC + RECV) == 0
    assert r.get(GIC_PENDING) & GIC_BIT


def channel_requires_both_sides_ack(r):
    r.connect_link()
    r.put(BASE + 0x2c, UP)
    r.kick()
    assert r.stat("state") == "channel-opening"
    assert r.get(BASE + 0x14) == UP and r.get(BASE + 0x30) == DOWN
    r.clear_notification()
    r.kick()
    assert r.get(BASE + 0x30) == DOWN
    assert r.get(IPCC + RECV) == NONE
    r.put(BASE + 0x18, UP)
    r.advance(100 * WORK)
    assert r.get(BASE + 0x30) == DOWN
    r.kick()
    assert r.stat("state") == "connected"
    assert r.get(BASE + 0x30) == UP
    assert r.get(IPCC + RECV) == 0


def premature_packet_is_not_handshake_completion(r):
    r.publish("{class: clock, res: qdss, val: 1}")
    r.kick()
    assert r.stat("last-error") == "link"
    assert r.get(BASE + TX) == 96
    r.connect_peer()
    assert r.get(BASE + TX) == 96
    assert r.stat("accepted-messages") == 0
    assert r.stat("qdss-vote") is False
    r.kick()  # Only a new connected-channel request can consume the packet.
    assert r.get(BASE + TX) == 0
    assert r.stat("accepted-messages") == 1
    assert r.stat("qdss-vote") is True


def wrong_ipcc_route_cannot_activate_peer(r):
    r.put(BASE + 0x10, UP)
    r.put(BASE + 0x24, UP)
    before = r.bytes_at(0, SIZE)
    for recipient, signal in ((0, 1), (1, 0), (3, 3), (8, 2)):
        r.kick(recipient=recipient, signal=signal)
    assert r.bytes_at(0, SIZE) == before
    assert r.stat("work-pending") is False
    assert r.stat("state") == "link-down"
    assert r.stat("notifications") == 0


def payload_and_length_require_a_new_doorbell(r):
    r.connect_peer()
    data = r.publish("{class: clock, res: qdss, val: 1}")
    r.advance(1000 * WORK)
    assert r.get(BASE + TX) == 96 and r.stat("accepted-messages") == 0
    r.put(BASE + TX, 0)  # Payload without published length is not a request.
    r.kick()
    assert r.bytes_at(TX + 4, len(data)) == data
    assert r.stat("qdss-vote") is False
    assert r.stat("accepted-messages") == 0
    assert r.get(IPCC + RECV) == NONE


def vendor_96_byte_qdss_vote_drives_output(r):
    r.intercept_vote("qdss-vote")
    r.connect_peer()
    data = r.vote("qdss", 1)
    assert b"\0\xa5" in data
    assert r.stat("qdss-vote") is True
    assert r.stat("qdss-ao-vote") is False
    r.output_is(True)
    r.vote("qdss", 0)
    r.output_is(False)
    assert r.stat("qdss-vote") is False
    assert r.stat("accepted-messages") == 2
    try:
        r.monitor("qom-set", {"path": PEER, "property": "qdss-vote", "value": True})
    except AssertionError as error:
        assert "not writable" in str(error) or "not found" in str(error), str(error)
    else:
        raise AssertionError("guest-observable vote could be spoofed through QOM")
    return {"actual_gpio_output_observed": "qdss-vote", "padding_byte": 0xa5}


def upstream_64_byte_qdss_ao_is_independent(r):
    r.intercept_vote("qdss-ao-vote")
    r.connect_peer()
    r.vote("qdss_ao", 1, size=64)
    r.output_is(True)
    assert r.stat("qdss-vote") is False
    r.vote("qdss", 1, size=64)
    r.output_is(True)
    r.vote("qdss", 0, size=64)
    assert r.stat("qdss-ao-vote") is True
    r.vote("qdss_ao", 0, size=64)
    r.output_is(False)
    return {"actual_gpio_output_observed": "qdss-ao-vote",
            "both_votes_independently_changed": True}


def duplicate_doorbells_do_not_replay(r):
    r.connect_peer()
    r.publish("{class: clock, res: qdss, val: 1}")
    for _ in range(5):
        r.kick(advance=False)
    r.advance(WORK - 1)
    assert r.stat("accepted-messages") == 0
    r.advance(1)
    assert r.stat("accepted-messages") == 1
    assert r.get(BASE + TX) == 0
    r.clear_notification()
    before = r.stat("notifications")
    for _ in range(5):
        r.kick()
    assert r.stat("accepted-messages") == 1
    assert r.stat("notifications") == before
    assert r.get(IPCC + RECV) == NONE
    return {"doorbells_before_consumption": 5, "doorbells_after_consumption": 5}


def unsupported_services_are_not_consumed(r):
    r.connect_peer()
    commands = ("{class: clock, res: cx, val: 1}",
                "{class: ddr, res: qdss, val: 1}",
                "{class: volt_flr, res: cx, val: 1}",
                "{class: clock, res: qdss_unknown, val: 1}")
    for command in commands:
        r.expect_refused(command)
    return {"rejected_commands": len(commands)}


def unsupported_levels_are_not_consumed(r):
    r.connect_peer()
    r.vote("qdss", 1)
    values = [2, 3, 4, 5, 6, 7, -1, 0x100000000]
    for value in values:
        r.expect_refused(f"{{class: clock, res: qdss, val: {value}}}")
    assert r.stat("qdss-vote") is True
    return {"rejected_values": values}


def malformed_and_extra_commands_are_not_consumed(r):
    r.connect_peer()
    commands = ("", "{class: clock, res: qdss, val: 1",
                "{class: clock, res: qdss, val: 1}garbage",
                "{class: clock, res: qdss, val: 1, val: 0}",
                "{class: clock, res: qdss, val: true}",
                "{class: clock, res: qdss, val: 01}")
    for command in commands:
        r.expect_refused(command)
    return {"rejected_commands": len(commands)}


def invalid_length_and_missing_nul_are_not_consumed(r):
    r.connect_peer()
    r.store(TX + 4, bytes([0x41]) * (MBOX - 4))
    for length in (1, 3, 253, 256, 0xffffffff, 96):
        r.put(BASE + TX, length)
        before = r.stat("accepted-messages")
        notifications = r.stat("notifications")
        r.kick()
        assert r.get(BASE + TX) == length
        assert r.stat("accepted-messages") == before
        assert r.stat("last-error") == "length"
        assert r.stat("notifications") == notifications
        assert r.get(IPCC + RECV) == NONE
    return {"invalid_lengths_or_no_nul": 6}


def modified_descriptor_and_bounds_are_not_trusted(r):
    r.connect_peer()
    r.publish("{class: clock, res: qdss, val: 1}")
    original = r.bytes_at(0, 64)
    alterations = ((0, 0), (4, 2), (8, 1), (0x1c, 0xffffffff),
                   (0x20, SIZE), (0x34, 0xffffffff), (0x38, SIZE),
                   (0x38, RX), (0x14, DOWN), (0x28, DOWN), (0x30, DOWN))
    before = r.bytes_at(TX, MBOX)
    for offset, value in alterations:
        r.store(0, original)
        r.put(BASE + offset, value)
        notifications = r.stat("notifications")
        r.kick()
        assert r.bytes_at(TX, MBOX) == before
        assert r.stat("accepted-messages") == 0
        assert r.stat("last-error") == "descriptor"
        assert r.stat("notifications") == notifications
        assert r.get(IPCC + RECV) == NONE
    r.store(0, original)
    return {"rejected_descriptor_alterations": len(alterations)}


def fabricated_ack_bytes_cannot_create_connected_phase(r):
    for offset in (0x10, 0x14, 0x18, 0x24, 0x28, 0x2c, 0x30):
        r.put(BASE + offset, UP)
    r.publish("{class: clock, res: qdss, val: 1}")
    r.kick()
    assert r.stat("state") == "link-down"
    assert r.stat("qdss-vote") is False
    assert r.stat("last-error") == "descriptor"
    assert r.get(BASE + TX) == 96
    assert r.get(IPCC + RECV) == NONE


def close_preserves_votes_and_refuses_staged_message(r):
    r.connect_peer()
    r.vote("qdss", 1)
    r.publish("{class: clock, res: qdss, val: 0}")
    r.put(BASE + 0x2c, DOWN)
    r.kick()
    assert r.stat("state") == "link-up"
    assert r.get(BASE + 0x30) == DOWN
    assert r.get(BASE + TX) == 96
    assert r.stat("qdss-vote") is True
    r.clear_notification()
    r.kick()
    assert r.get(IPCC + RECV) == NONE
    assert r.get(BASE + TX) == 96
    r.put(BASE + 0x24, DOWN)
    r.kick()
    assert r.stat("state") == "link-down"
    assert r.get(BASE + 0x28) == DOWN
    assert r.stat("qdss-vote") is True
    assert r.get(BASE + TX) == 96
    r.clear_notification()
    r.connect_peer()
    assert r.get(BASE + TX) == 96
    r.kick()
    assert r.get(BASE + TX) == 0
    assert r.stat("qdss-vote") is False


def masked_completion_uses_real_ipcc_and_gic(r):
    r.connect_peer()
    r.put(IPCC + DISABLE, 0)
    r.publish("{class: clock, res: qdss, val: 1}", size=64, padding=0)
    r.kick()
    assert r.get(BASE + TX) == 0
    assert r.stat("qdss-vote") is True
    assert r.get(IPCC + RECV) == NONE
    assert not r.get(GIC_PENDING) & GIC_BIT
    r.put(IPCC + ENABLE, 0)
    assert r.get(IPCC + RECV) == 0
    assert r.get(GIC_PENDING) & GIC_BIT
    r.clear_notification()
    assert not r.get(GIC_PENDING) & GIC_BIT
    return {"gic_intid": 261, "ipcc_output_intercepted": False}


def stale_irq_cannot_ack_an_unknown_packet(r):
    r.connect_peer()
    r.publish("{class: clock, res: qdss, val: 1}")
    r.kick()
    assert r.get(IPCC + RECV) == 0  # Deliberately keep the previous completion.
    r.publish("{class: unsupported, res: qdss, val: 1}")
    before = r.stat("notifications")
    r.kick()
    assert r.get(IPCC + RECV) == 0
    assert r.get(BASE + TX) == 96
    assert r.stat("accepted-messages") == 1
    assert r.stat("notifications") == before
    assert r.stat("last-error") == "command"
    r.clear_notification()
    assert r.get(BASE + TX) == 96


def reset_cancels_work_and_reinitializes_only_reference_ranges(r):
    r.intercept_vote("qdss-vote")
    r.connect_peer()
    r.vote("qdss", 1)
    r.vote("qdss_ao", 1)
    r.store(0x800, b"outside-reference-ranges")
    r.publish("{class: clock, res: qdss, val: 0}")
    r.kick(advance=False)
    assert r.stat("work-pending") is True
    r.reset_board()
    assert r.stat("state") == "link-down"
    assert r.stat("work-pending") is r.stat("notification-pending") is False
    assert r.stat("qdss-vote") is r.stat("qdss-ao-vote") is False
    r.output_is(False)
    assert r.stat("accepted-messages") == r.stat("notifications") == 0
    assert r.bytes_at(RX, MBOX) == r.bytes_at(TX, MBOX) == bytes(MBOX)
    assert r.bytes_at(0x800, len(b"outside-reference-ranges")) == b"outside-reference-ranges"
    r.advance(1000 * WORK)
    assert r.stat("notifications") == 0
    assert r.get(IPCC + RECV) == NONE
    r.connect_peer()
    r.vote("qdss", 1)
    r.output_is(True)


def fill_ipcc_and_leave_completion_pending(r):
    r.connect_peer()
    r.put(IPCC + DISABLE, 0)
    r.put(IPCC + CLEAR, 0)  # Reclaim the now-disabled AOP entry.
    for signal in range(1, 4097):
        r.put(IPCC + SEND, (8 << 16) | signal)
    r.publish("{class: clock, res: qdss, val: 1}")
    before = r.stat("notifications")
    r.kick()
    assert r.get(BASE + TX) == 0
    assert r.stat("accepted-messages") == 1
    assert r.stat("qdss-vote") is True
    assert r.stat("notification-pending") is True
    assert r.stat("work-pending") is False
    assert r.stat("notifications") == before
    return before


def ipcc_full_retry_never_replays_service(r):
    before = fill_ipcc_and_leave_completion_pending(r)
    r.advance(RETRY)
    assert r.stat("notification-pending") is True
    assert r.stat("accepted-messages") == 1
    r.put(IPCC + CLEAR, (8 << 16) | 1)
    r.advance(RETRY)
    assert r.stat("notification-pending") is False
    assert r.stat("accepted-messages") == 1
    assert r.stat("notifications") == before + 1
    r.put(IPCC + ENABLE, 0)
    assert r.get(IPCC + RECV) == 0
    assert r.get(GIC_PENDING) & GIC_BIT
    return {"filled_ipcc_entries": 4096, "actual_service_executions": 1}


def with_migration(args, directory, prepare, verify, target_prepare=None):
    source = target = None
    try:
        sd, td = directory / "src", directory / "dst"
        sd.mkdir()
        td.mkdir()
        source = AopRig(args, sd)
        target = AopRig(args, td, incoming=True)
        if target_prepare:
            target_prepare(target)
        context = prepare(source)
        source.monitor("stop")
        # QTest's clock counter is test-harness state, not migration state.
        # Align it before loading absolute VMState timer deadlines.
        if source.virtual_ns:
            result = target.command(f"clock_set {source.virtual_ns}")
            target.virtual_ns = int(result[0], 0)
            assert target.virtual_ns == source.virtual_ns
        target_before_migration_ns = target.virtual_ns
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sm, tm = wait_migration(source), wait_migration(target)
        assert sm["status"] == tm["status"] == "completed", (sm, tm)
        assert source.monitor("query-status")["status"] == "postmigrate"
        details = verify(target, context) or {}
        return {"source_status": sm["status"], "target_status": tm["status"],
                "actual_two_process_migration": True,
                "source_virtual_ns_at_migration": source.virtual_ns,
                "target_virtual_ns_before_migration": target_before_migration_ns,
                **details}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def migrate_idle_votes_and_outputs(args, directory):
    def prepare(r):
        r.connect_peer()
        r.vote("qdss", 1)
        r.vote("qdss_ao", 1)
        r.store(0x800, b"retained")
        return r.stat("notifications")

    def verify(r, notifications):
        assert r.stat("state") == "connected"
        assert r.stat("qdss-vote") is r.stat("qdss-ao-vote") is True
        r.output_is(True)
        assert r.stat("accepted-messages") == 2
        assert r.bytes_at(0x800, 8) == b"retained"
        assert r.stat("work-pending") is r.stat("notification-pending") is False
        r.advance(1000 * WORK)
        assert r.stat("notifications") == notifications
        assert r.get(IPCC + RECV) == NONE
        r.vote("qdss", 0)
        r.output_is(False)
        assert r.stat("qdss-ao-vote") is True
        return {"votes_and_actual_gpio_restored": True,
                "post_migration_off_request_consumed": True}

    return with_migration(args, directory, prepare, verify,
                          lambda r: r.intercept_vote("qdss-vote"))


def migrate_pending_message_exactly_once(args, directory):
    def prepare(r):
        r.connect_peer()
        data = r.publish("{class: clock, res: qdss, val: 1}")
        r.kick(advance=False)
        r.advance(WORK - 1)
        assert r.stat("accepted-messages") == 0
        assert r.stat("work-pending") is True
        return data

    def verify(r, data):
        assert r.stat("work-pending") is True
        assert r.get(BASE + TX) == 96
        assert r.bytes_at(TX + 4, 96) == data
        assert r.stat("accepted-messages") == 0
        r.monitor("cont")
        assert r.stat("accepted-messages") == 0
        r.advance(1)
        assert r.get(BASE + TX) == 0
        assert r.stat("accepted-messages") == 1
        assert r.stat("qdss-vote") is True
        assert r.get(IPCC + RECV) == 0
        r.clear_notification()
        r.advance(1000 * WORK)
        assert r.stat("accepted-messages") == 1
        assert r.get(IPCC + RECV) == NONE
        return {"remaining_virtual_ns": 1, "service_execution_count": 1}

    return with_migration(args, directory, prepare, verify)


def migrate_pending_link_handshake(args, directory):
    def prepare(r):
        r.put(IPCC + ENABLE, 0)
        r.put(BASE + 0x10, UP)
        r.put(BASE + 0x24, UP)
        r.kick(advance=False)
        r.advance(WORK // 2)
        assert r.get(BASE + 0x28) == DOWN

    def verify(r, _):
        assert r.stat("state") == "link-down"
        assert r.stat("work-pending") is True
        assert r.get(BASE + 0x28) == DOWN
        r.advance(WORK // 2 - 1)
        assert r.get(BASE + 0x28) == DOWN
        r.advance(1)
        assert r.get(BASE + 0x28) == UP
        assert r.stat("state") == "link-up"
        assert r.get(IPCC + RECV) == 0
        r.clear_notification()
        r.connect_channel()
        r.vote("qdss_ao", 1)
        return {"pending_link_completed_then_channel_opened": True}

    return with_migration(args, directory, prepare, verify)


def migrate_unknown_packet_remains_unconsumed(args, directory):
    def prepare(r):
        r.connect_peer()
        r.expect_refused("{class: clock, res: unknown, val: 1}")
        return r.bytes_at(TX, MBOX)

    def verify(r, image):
        assert r.bytes_at(TX, MBOX) == image
        assert r.stat("accepted-messages") == 0
        assert r.stat("last-error") == "command"
        assert r.stat("work-pending") is False
        r.advance(1000 * WORK)
        assert r.bytes_at(TX, MBOX) == image
        assert r.get(IPCC + RECV) == NONE
        r.put(BASE + TX, 0)  # Simulate guest timeout cleanup, not peer ACK.
        assert r.stat("accepted-messages") == 0
        r.vote("qdss", 1)
        assert r.stat("accepted-messages") == 1
        return {"unknown_packet_retained": True,
                "guest_cleanup_was_not_counted_as_peer_completion": True}

    return with_migration(args, directory, prepare, verify)


def migrate_masked_reply_preserves_real_ipcc_pending(args, directory):
    def prepare(r):
        r.connect_peer()
        r.put(IPCC + DISABLE, 0)
        r.publish("{class: clock, res: qdss, val: 1}", size=64)
        r.kick()
        assert r.get(BASE + TX) == 0
        assert r.get(IPCC + RECV) == NONE
        return r.stat("notifications")

    def verify(r, notifications):
        assert r.get(BASE + TX) == 0
        assert r.stat("accepted-messages") == 1
        assert r.get(IPCC + RECV) == NONE
        assert not r.get(GIC_PENDING) & GIC_BIT
        r.put(IPCC + ENABLE, 0)
        assert r.get(IPCC + RECV) == 0
        assert r.get(GIC_PENDING) & GIC_BIT
        r.clear_notification()
        r.advance(1000 * WORK)
        assert r.get(IPCC + RECV) == NONE
        assert r.stat("notifications") == notifications
        return {"masked_ipcc_latch_restored": True, "gic_intid": 261,
                "ipcc_output_intercepted": False}

    return with_migration(args, directory, prepare, verify)


def migrate_pending_full_ipcc_notification(args, directory):
    def prepare(r):
        before = fill_ipcc_and_leave_completion_pending(r)
        r.advance(RETRY // 2)
        assert r.stat("notification-pending") is True
        return before

    def verify(r, before):
        assert r.stat("notification-pending") is True
        assert r.stat("work-pending") is False
        assert r.stat("accepted-messages") == 1
        assert r.get(BASE + TX) == 0
        r.put(IPCC + CLEAR, (8 << 16) | 1)
        r.advance(RETRY // 2 - 1)
        assert r.stat("notification-pending") is True
        assert r.stat("notifications") == before
        r.advance(1)
        assert r.stat("notification-pending") is False
        assert r.stat("notifications") == before + 1
        assert r.stat("accepted-messages") == 1
        r.put(IPCC + ENABLE, 0)
        assert r.get(IPCC + RECV) == 0
        assert r.get(GIC_PENDING) & GIC_BIT
        return {"notification_retry_restored": True,
                "remaining_virtual_ns": RETRY // 2,
                "service_execution_count": 1}

    return with_migration(args, directory, prepare, verify)


def reject_migration_profile(args, directory, source_profile, target_profile):
    source = target = None
    try:
        sd, td = directory / "src", directory / "dst"
        sd.mkdir()
        td.mkdir()
        sa, ta = copy.copy(args), copy.copy(args)
        sa.machine, ta.machine = source_profile, target_profile
        source = AopRig(sa, sd)
        target = AopRig(ta, td, incoming=True)
        # Connect both monitors successfully before asking migration to fail.
        assert source.process.poll() is target.process.poll() is None
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        deadline = time.monotonic() + 25
        outgoing = {}
        while time.monotonic() < deadline:
            outgoing = source.monitor("query-migrate")
            if target.process.poll() is not None:
                break
            time.sleep(.01)
        else:
            raise TimeoutError("mismatched profile did not terminate incoming migration")
        exit_code = target.process.returncode
        stderr = target.log_path.read_text(errors="replace")
        assert exit_code not in (None, 0), (exit_code, stderr)
        assert "reference_peer" in stderr and "qcom-aop-msgram" in stderr, stderr
        return {"source_profile": source_profile, "target_profile": target_profile,
                "expected_rejection": True, "destination_exit": exit_code,
                "source_migration": outgoing, "destination_stderr": stderr,
                "profile_equality_marker_rejected": True}
    finally:
        if source:
            source.close()
        if target:
            target.close()


def reject_none_to_reference_migration(args, directory):
    return reject_migration_profile(args, directory, NONE_PROFILE, PROFILE)


def reject_reference_to_none_migration(args, directory):
    return reject_migration_profile(args, directory, PROFILE, NONE_PROFILE)


def default_and_explicit_none_keep_raw_sram(args, directory):
    for index, machine in enumerate(("tb371fc,ufs-enabled=off", NONE_PROFILE)):
        d = directory / str(index)
        d.mkdir()
        a = copy.copy(args)
        a.machine = machine
        rig = None
        try:
            rig = AopRig(a, d)
            children = rig.monitor("qom-list", {"path": "/machine"})
            assert not any(item["name"] == "aop-peer" for item in children)
            assert rig.bytes_at(0, SIZE) == bytes(SIZE)
            image = bytes((i * 29 + 7) & 0xff for i in range(SIZE))
            rig.store(0, image)
            rig.put(IPCC + ENABLE, 0)
            rig.kick()
            assert rig.bytes_at(0, SIZE) == image
            assert rig.get(IPCC + RECV) == NONE
            rig.reset_board()
            assert rig.bytes_at(0, SIZE) == image
        finally:
            if rig:
                rig.close()
    return {"default_and_explicit_none_tested": True,
            "raw_sram_reset_retention": True}


def invalid_profile_is_rejected(args, directory):
    command = [args.qemu, "-machine", "tb371fc,aop-profile=invalid",
               "-accel", "qtest", "-m", "128M", "-display", "none",
               "-monitor", "none", "-serial", "null", "-S"]
    result = subprocess.run(command, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True, timeout=10)
    (directory / "qemu.stderr").write_text(result.stderr)
    assert result.returncode != 0
    assert "aop-profile must be none or reference-qdss-vote" in result.stderr
    return {"returncode": result.returncode}


ORDINARY_TESTS = [
    initialized_reference_descriptor,
    link_requires_actual_ack_and_doorbell,
    channel_requires_both_sides_ack,
    premature_packet_is_not_handshake_completion,
    wrong_ipcc_route_cannot_activate_peer,
    payload_and_length_require_a_new_doorbell,
    vendor_96_byte_qdss_vote_drives_output,
    upstream_64_byte_qdss_ao_is_independent,
    duplicate_doorbells_do_not_replay,
    unsupported_services_are_not_consumed,
    unsupported_levels_are_not_consumed,
    malformed_and_extra_commands_are_not_consumed,
    invalid_length_and_missing_nul_are_not_consumed,
    modified_descriptor_and_bounds_are_not_trusted,
    fabricated_ack_bytes_cannot_create_connected_phase,
    close_preserves_votes_and_refuses_staged_message,
    masked_completion_uses_real_ipcc_and_gic,
    stale_irq_cannot_ack_an_unknown_packet,
    reset_cancels_work_and_reinitializes_only_reference_ranges,
    ipcc_full_retry_never_replays_service,
]
MIGRATION_TESTS = [
    migrate_idle_votes_and_outputs,
    migrate_pending_message_exactly_once,
    migrate_pending_link_handshake,
    migrate_unknown_packet_remains_unconsumed,
    migrate_masked_reply_preserves_real_ipcc_pending,
    migrate_pending_full_ipcc_notification,
]
REJECTED_MIGRATION_TESTS = [
    reject_none_to_reference_migration,
    reject_reference_to_none_migration,
]
PROFILE_TESTS = [default_and_explicit_none_keep_raw_sram, invalid_profile_is_rejected]
EXPECTED_CHECKS = 30
EXPECTED_MIGRATIONS = 6
EXPECTED_REJECTIONS = 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = str(args.qemu.resolve())
    args.machine, args.base, args.qom_path = PROFILE, BASE, PEER
    args.extra_qemu_args = ["-d", "unimp,guest_errors"]
    args.out.mkdir(parents=True, exist_ok=False)
    tests = (ORDINARY_TESTS + MIGRATION_TESTS + REJECTED_MIGRATION_TESTS +
             PROFILE_TESTS)
    assert len(tests) == EXPECTED_CHECKS
    assert len({test.__name__ for test in tests}) == EXPECTED_CHECKS
    report = {"schema_version": 1, "suite": "AOP reference QDSS vote peer",
              "machine": args.machine, "expected_checks": EXPECTED_CHECKS,
              "qemu_sha256": digest(Path(args.qemu).read_bytes()),
              "test_sha256": digest(Path(__file__).read_bytes()),
              "expected_completed_migrations": EXPECTED_MIGRATIONS,
              "expected_rejected_migrations": EXPECTED_REJECTIONS,
              "scope": "Real QTest/IPCC/GIC, logical GPIO votes and process migration; no physical frequency or AOP firmware claim",
              "guest_cpu_executed": False,
              "ipcc_output_intercepted": False,
              "checks": [], "migrations": [], "rejected_migrations": [],
              "passed": False, "all_passed": False}
    start = time.monotonic()
    for index, test in enumerate(tests):
        directory = args.out / f"{index:02d}"
        directory.mkdir()
        rig = None
        try:
            if test in ORDINARY_TESTS:
                rig = AopRig(args, directory)
                details = test(rig) or {}
            else:
                details = test(args, directory)
                if test in MIGRATION_TESTS:
                    report["migrations"].append({"name": test.__name__, **details})
                elif test in REJECTED_MIGRATION_TESTS:
                    report["rejected_migrations"].append({"name": test.__name__, **details})
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
              all(check["passed"] is True for check in report["checks"]) and
              len(report["migrations"]) == EXPECTED_MIGRATIONS and
              all(item["source_status"] == item["target_status"] == "completed"
                  for item in report["migrations"]) and
              len(report["rejected_migrations"]) == EXPECTED_REJECTIONS and
              all(item["profile_equality_marker_rejected"] is True
                  for item in report["rejected_migrations"]))
    report["passed"] = report["all_passed"] = passed
    report["completed_migrations"] = len(report["migrations"])
    report["rejected_migration_attempts"] = len(report["rejected_migrations"])
    (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
