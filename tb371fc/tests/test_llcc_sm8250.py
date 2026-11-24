#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compiled SM8250 LLCC v2 control-reference acceptance.

Literal Kona values follow pinned R11 98719830 and the independently decoded
original 640-byte table. Four banks are a pinned upstream reference geometry,
not a measured STATUS value. No guest CPU, cache datapath or performance claim.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import traceback

from test_camera_gdsc import CameraRig, core_diagnostics, unique_section, write_json
from test_geni_uart import connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE, DEVICE = "qcom-sm8250-llcc", "/machine/llcc"
BASE, BCAST, RAM = 0x09200000, 0x09600000, 0x80010000
BANK_STRIDE, BANK_BYTES, BANKS_BYTES = 0x80000, 0x50000, 0x1d0000
CAD, PCB, WRSC = 0x21f00, 0x21f04, 0x21f20
HW_INFO, COMMON, TRP_ERROR, DRP_ERROR = 0x30000, 0x3000c, 0x20480, 0x41000
IDENTITY, VMSTATE_BYTES, DELAY, FIRST_QOM_COUNT = 0x4c4c4332, 783, 1000, 313
EXPECTED_CHECKS = 32
DEFAULT_MACHINE = "tb371fc,gcc-boot-configured=off,ufs-enabled=off,rpmh-profile=off,rpmh-boot-configured=off"
SOURCE_COMMIT = "98719830b0de5584f5588bbe9f4c8304d97a64c6"

# Source order is intentional: CVPFW usecase 32 maps to SID 17, last.
# Each tuple is SID, ATTR0, ATTR1, write_scid, dis_cap, retain, activate_init.
ROWS = (
    (1,  0x0fff0000, 0x30000018, 0, 0, 1, 1),
    (2,  0x0fff0000, 0x08000038, 0, 0, 1, 0),
    (6,  0x0fff0000, 0x10000010, 0, 0, 0, 0),
    (10, 0x0fff0000, 0x10000010, 0, 0, 0, 0),
    (11, 0x0fff0000, 0x10000018, 0, 0, 1, 0),
    (12, 0x0fff0000, 0x10000010, 1, 0, 1, 0),
    (13, 0x0fff0000, 0x10000018, 0, 0, 0, 1),
    (15, 0x0fff0000, 0x10000010, 0, 0, 1, 0),
    (16, 0x0fff0000, 0x30000018, 0, 0, 1, 0),
    (22, 0x0fff0000, 0x10000018, 0, 0, 1, 0),
    (23, 0x0fff0000, 0x30000018, 0, 0, 1, 0),
    (24, 0x0fff0000, 0x10000010, 0, 0, 1, 0),
    (28, 0x0fff0000, 0x04000038, 0, 0, 1, 0),
    (30, 0x00000003, 0x02000031, 0, 0, 1, 0),
    (31, 0x0fff0000, 0x04000018, 0, 0, 0, 1),
    (17, 0x0fff0000, 0x08000010, 0, 0, 1, 0),
)
SIDS = tuple(row[0] for row in ROWS)
BY_SID = {row[0]: row for row in ROWS}
INIT_SIDS = (1, 13, 31)
FINAL_FLAGS = {CAD: 0, PCB: 0x51c39806, WRSC: 0x1000}
CONTROL = tuple(sid*0x1000 for sid in SIDS)
STATUS = tuple(offset+4 for offset in CONTROL)
ATTRS = tuple(0x21000+8*sid+i for sid in SIDS for i in (0, 4))
REGISTERS = tuple(sorted(CONTROL+STATUS+ATTRS+(CAD, PCB, WRSC, HW_INFO, COMMON, TRP_ERROR, DRP_ERROR)))
BANK_REGISTERS = tuple(o for o in REGISTERS if o != HW_INFO)
BCAST_REGISTERS = tuple(o for o in REGISTERS if o not in (TRP_ERROR, DRP_ERROR))
WRITTEN_REGISTERS = set(ATTRS+(CAD, PCB, WRSC))
GLOBAL_FIELDS = (
    "enabled-profile", "reference-identity", "bank-count", "slice-count",
    "register-count", "sparse-window-count", "mapped-bytes", "region-count",
    "bank-stride", "bank-resource-bytes", "transition-delay-ns",
    "reference-common-status", "reference-hardware-info",
)
SCOPE_FIELDS = (
    "enabled-reference", "hardware-version-known", "memory-controller-count-known",
    "cache-data-modelled", "performance-counters-modelled", "ecc-error-injection-modelled",
    "bank-count-known", "mapped-word-count", "timer-count", "vmstate-bytes",
)
SLICE_FIELDS = (
    "active", "state-known", "recipe-valid", "status", "phase",
    "captured-attr0", "captured-attr1", "captured-flags", "deadline-ns",
)


def expected_leaves():
    result = []
    for view in range(5):
        base = BASE+view*BANK_STRIDE if view < 4 else BCAST
        offsets = BANK_REGISTERS if view < 4 else BCAST_REGISTERS
        runs = []
        for offset in offsets:
            if runs and runs[-1][1]+4 == offset:
                runs[-1] = (runs[-1][0], offset)
            else:
                runs.append((offset, offset))
        name = f"bank{view}" if view < 4 else "broadcast"
        result += [(base+first, base+last+3, f"{TYPE}.{name}.{first:05x}")
                   for first, last in runs]
    return tuple(result)


assert len(SIDS) == len(set(SIDS)) == 16
assert len(REGISTERS) == len(set(REGISTERS)) == 71
assert len(expected_leaves()) == 139
assert sum(last-first+1 for first, last, _ in expected_leaves()) == 1396
assert 13+4+2*71+16*9+10 == FIRST_QOM_COUNT


class LlccRig(CameraRig):
    """Existing real QTest/QMP transport, with retained local operation counts."""

    def __init__(self, args, directory, *, reference=True, incoming=False, accel="qtest"):
        self.args = copy.copy(args)
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.reference, self.accel = bool(reference), accel
        self.last_clock = self.device_mmio_operations = self.cont_commands = 0
        self.qom_get_commands = self.camera_mmio_operations = 0
        self.connections, self.events, self.irq, self.seen_irq = [], [], {}, set()
        self._request_id = 0
        self.socket_directory = Path(tempfile.mkdtemp(prefix="llcc-"))
        self.log_path = self.directory/"qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtlog = (self.directory/"qtest.jsonl").open("w")
        self.qmplog = (self.directory/"qmp.jsonl").open("w")
        command = [args.qemu, "-machine", args.machine, "-accel", accel,
                   "-m", "128M", "-smp", "8", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem",
                   "-qmp", f"unix:{self.socket_directory}/m,server=on,wait=off"]
        if reference is not None:
            command += ["-global", f"{TYPE}.enabled-reference={'on' if reference else 'off'}"]
        if accel == "qtest":
            command += ["-qtest", f"unix:{self.socket_directory}/t,server=on,wait=off",
                        "-qtest-log", "/dev/null"]
        if incoming:
            command += ["-incoming", "defer"]
        write_json(self.directory/"command.json", command)
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        try:
            self.qmp = connect_unix(self.socket_directory/"m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            self._log(self.qmplog, {"receive": greeting})
            assert "QMP" in greeting
            self.monitor("qmp_capabilities")
            if accel == "qtest":
                self.qtest = connect_unix(self.socket_directory/"t", self.process)
                self.connections.append(self.qtest)
                self.qfile = self.qtest.makefile("rwb", buffering=0)
                if not incoming:
                    self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, command):
        parts = command.split()
        if parts and parts[0] in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq"):
            address = int(parts[1], 0)
            if BASE <= address < BASE+BANKS_BYTES or BCAST <= address < BCAST+BANK_BYTES:
                self.device_mmio_operations += 1
        return super().command(command)

    def raw_monitor(self, execute, arguments=None):
        if execute == "qom-get":
            self.qom_get_commands += 1
        return super().raw_monitor(execute, arguments)

    def qom(self, name, path=DEVICE):
        return self.monitor("qom-get", {"path": path, "property": name})

    def reg(self, offset, prefix="reg"):
        return self.qom(f"{prefix}.{offset:05x}")

    def slice(self, sid, field):
        return self.qom(f"slice{sid}.{field}")

    def read(self, offset, bank=None):
        return self.raw_read((BCAST if bank is None else BASE+bank*BANK_STRIDE)+offset)

    def write(self, offset, value):
        self.raw_write(BCAST+offset, value)

    def step(self, ns):
        old = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == old+ns
        return self.last_clock

    def world(self):
        return {
            "globals": {name: self.qom(name) for name in GLOBAL_FIELDS},
            "region_bases": [self.qom(f"region{i}.physical-base") for i in range(2)],
            "region_bytes": [self.qom(f"region{i}.resource-bytes") for i in range(2)],
            "registers": {f"{o:05x}": self.reg(o) for o in REGISTERS},
            "written": {f"{o:05x}": self.reg(o, "written") for o in REGISTERS},
            "slices": {str(sid): {field: self.slice(sid, field) for field in SLICE_FIELDS}
                       for sid in SIDS},
            "scope": {name: self.qom(name) for name in SCOPE_FIELDS},
        }


def state(r, sid, *, active=False, phase=0, deadline=-1):
    assert r.slice(sid, "active") is bool(active)
    assert r.slice(sid, "state-known") is True
    assert r.slice(sid, "status") == (1 if active else 2)
    assert r.slice(sid, "phase") == phase
    assert r.slice(sid, "deadline-ns") == deadline
    if not phase:
        assert all(r.slice(sid, n) == 0 for n in
                   ("captured-attr0", "captured-attr1", "captured-flags"))


def configure_slice(r, sid, *, omit=()):
    row = BY_SID[sid]
    values = ((0x21004+8*sid, row[2]), (0x21000+8*sid, row[1]),
              (WRSC, FINAL_FLAGS[WRSC]), (CAD, 0), (PCB, FINAL_FLAGS[PCB]))
    for offset, value in values:
        if offset not in omit:
            r.write(offset, value)


def request(r, sid, activate=True):
    r.write(sid*0x1000, 3 if activate else 5)
    r.write(sid*0x1000, 2 if activate else 4)


def complete(r, sid, activate=True):
    before = r.slice(sid, "active")
    request(r, sid, activate)
    state(r, sid, active=before, phase=3 if activate else 4, deadline=r.last_clock+DELAY)
    r.step(DELAY)
    state(r, sid, active=activate)


def program_source_table(r, *, activate_init=True):
    wren = cad = pcb = 0
    early = []
    for index, (sid, a0, a1, wse, dca, rp, activate) in enumerate(ROWS):
        r.write(0x21004+8*sid, a1)
        r.write(0x21000+8*sid, a0)
        wren |= wse << sid
        cad |= dca << sid
        pcb |= rp << sid
        r.write(WRSC, wren)
        r.write(CAD, cad)
        r.write(PCB, pcb)
        assert r.slice(sid, "recipe-valid") is True
        if activate and activate_init:
            complete(r, sid)
            early.append({"sid": sid, "source_row_index": index, "wrsc": wren,
                          "cad": cad, "pcb": pcb, "virtual_ns": r.last_clock})
    assert (wren, cad, pcb) == (0x1000, 0, 0x51c39806)
    return early


def assert_mirrors(r):
    before = r.world()
    for bank in range(5):
        offsets = BANK_REGISTERS if bank < 4 else BCAST_REGISTERS
        for offset in offsets:
            if offset in STATUS:
                expected = 1 if before["slices"][str(offset//0x1000)]["active"] else 2
            elif offset == COMMON:
                expected = 0x40000000
            else:
                expected = before["registers"][f"{offset:05x}"]
            assert r.read(offset, bank if bank < 4 else None) == expected, (bank, hex(offset))
    assert r.world() == before


def rejected(r, offset, *, bank=None, write=True, value=0, width="l", reason="disabled-or-invalid-access"):
    view = "broadcast" if bank is None else f"bank{bank}"
    op, size = ("write" if write else "read"), {"b": 1, "w": 2, "l": 4, "q": 8}[width]
    line = f"{TYPE}: rejected {op} {view}+0x{offset:05x} size {size} ({reason})"
    count = r.diagnostic_count(line)
    base = BCAST if bank is None else BASE+bank*BANK_STRIDE
    r.command(f"{op}{width} {base+offset:#x}"+(f" {value:#x}" if write else ""))
    assert r.diagnostic_count(line) == count+1, line


def cold_geometry_identity_and_pure_getters(r):
    assert tuple(r.qom(n) for n in GLOBAL_FIELDS) == (
        1, IDENTITY, 4, 16, 71, 139, 1396, 2, BANK_STRIDE, BANK_BYTES, DELAY, 0x40000000, 0)
    before = r.world()
    assert before["region_bases"] == [BASE, BCAST]
    assert before["region_bytes"] == [BANKS_BYTES, BANK_BYTES]
    assert not any(before["registers"].values()) and not any(before["written"].values())
    count, mmio = r.qom_get_commands, r.device_mmio_operations
    assert r.world() == before
    assert r.qom_get_commands-count == FIRST_QOM_COUNT and r.device_mmio_operations == mmio
    for sid in SIDS:
        state(r, sid)
        assert not r.slice(sid, "recipe-valid")
    # Actual kernel first faults here; geometry is a named reference, not measured silicon.
    assert r.read(COMMON, 0) == 0x40000000 and r.read(COMMON) == 0x40000000
    assert r.world() == before
    return {"first_probe_address": BASE+COMMON, "read_bytes": 4,
            "pure_world_qom_get_count": FIRST_QOM_COUNT, "physical_bank_count_measured": False}


def original_kona_probe_order_and_all_bank_mirrors(r):
    early = program_source_table(r)
    assert [item["sid"] for item in early] == [1, 13, 31]
    assert early[0]["source_row_index"] == 0 and early[0]["pcb"] == 2
    assert early[0]["wrsc"] == 0
    for sid, a0, a1, *_ in ROWS:
        assert r.reg(0x21000+sid*8) == a0 and r.reg(0x21004+sid*8) == a1
        state(r, sid, active=sid in INIT_SIDS)
    assert {o for o in REGISTERS if r.reg(o, "written")} == WRITTEN_REGISTERS
    assert r.reg(0x21000+30*8) == 3 and r.reg(0x21004+30*8) == 0x02000031
    assert_mirrors(r)
    return {"source_order": list(SIDS), "early_init_activations": early,
            "all_349_physical_words_read": True, "cvpfw_usecase32_maps_to_sid17": True}


def all_sixteen_slices_activate_and_deactivate(r):
    program_source_table(r, activate_init=False)
    for sid in SIDS:
        complete(r, sid)
        for bank in range(4):
            assert r.read(sid*0x1000+4, bank) == 1
        complete(r, sid, False)
        assert r.read(sid*0x1000+4) == 2
    return {"complete_bidirectional_slice_count": 16}


def trigger_high_is_armed_until_matching_falling_edge(r):
    sid = 12
    configure_slice(r, sid)
    r.write(sid*0x1000, 3)
    state(r, sid, phase=1)
    assert r.slice(sid, "captured-attr0") == 0x0fff0000
    assert r.slice(sid, "captured-attr1") == 0x10000010
    assert r.slice(sid, "captured-flags") == 5
    r.step(100_000)
    state(r, sid, phase=1)
    r.write(sid*0x1000, 2)
    deadline = r.last_clock+DELAY
    r.step(DELAY-1)
    state(r, sid, phase=3, deadline=deadline)
    assert r.read(sid*0x1000+4) & 2
    r.step(1)
    state(r, sid, active=True)
    return {"high_edge_alone_does_not_start_timer": True, "transition_ns": DELAY}


def deactivation_preserves_active_until_deadline(r):
    sid = 30
    configure_slice(r, sid)
    complete(r, sid)
    r.write(sid*0x1000, 5)
    r.step(50_000)
    state(r, sid, active=True, phase=2)
    r.write(sid*0x1000, 4)
    deadline = r.last_clock+DELAY
    r.step(DELAY-1)
    state(r, sid, active=True, phase=4, deadline=deadline)
    assert r.read(sid*0x1000+4) & 1
    r.step(1)
    state(r, sid)


def explicit_zero_write_credit_is_required(r):
    sid = 6
    configure_slice(r, sid, omit=(CAD,))
    assert r.reg(CAD) == 0 and not r.reg(CAD, "written")
    assert not r.slice(sid, "recipe-valid")
    request(r, sid)
    r.step(10_000)
    state(r, sid)
    r.write(CAD, 0)
    assert r.slice(sid, "recipe-valid")
    complete(r, sid)
    return {"zero_is_data_not_programming_credit": True}


def incomplete_or_wrong_slice_recipe_cannot_activate(r):
    sid = 16
    for omit in (0x21000+sid*8, 0x21004+sid*8, WRSC, PCB):
        r.monitor("system_reset")
        configure_slice(r, sid, omit=(omit,))
        request(r, sid)
        r.step(10_000)
        state(r, sid)
    configure_slice(r, sid)
    for offset, bad in ((0x21000+sid*8, 0x0ffe0000),
                        (0x21004+sid*8, 0x30010018),
                        (WRSC, 0x1000 | (1 << sid)),
                        (CAD, 1 << sid), (PCB, 0x51c39806 ^ (1 << sid))):
        configure_slice(r, sid)
        r.write(offset, bad)
        assert not r.slice(sid, "recipe-valid")
        request(r, sid)
        r.step(10_000)
        state(r, sid)
        assert r.read(sid*0x1000+4) == 2
    return {"missing_fields": 4, "wrong_fields": 5, "opposite_status_bit_remains_asserted": True}


def related_configuration_change_cancels_armed_and_pending(r):
    sid = 2
    for pending in (False, True):
        configure_slice(r, sid)
        r.write(sid*0x1000, 3)
        if pending:
            r.write(sid*0x1000, 2)
            r.step(333)
        r.write(0x21000+sid*8, 0x0ffe0000)
        state(r, sid)
        configure_slice(r, sid)
        r.step(10_000)
        state(r, sid)
        r.write(sid*0x1000, 2)
        state(r, sid)
    complete(r, sid)
    request(r, sid, False)
    r.write(0x21004+sid*8, 0x08010038)
    state(r, sid, active=True)
    r.step(10_000)
    state(r, sid, active=True)


def unrelated_slice_and_other_global_bits_preserve_transaction(r):
    configure_slice(r, 1)
    request(r, 1)
    deadline = r.last_clock+DELAY
    r.step(400)
    # Source advances the cumulative global masks while earlier SID1 is live.
    for offset, value in ((0x21000+6*8, 0x0fff0000), (0x21004+6*8, 0x10000010),
                          (WRSC, 0x1000 | (1 << 6)), (CAD, 1 << 6),
                          (PCB, 0x51c39806 | (1 << 6))):
        r.write(offset, value)
        state(r, 1, phase=3, deadline=deadline)
    r.step(599)
    state(r, 1, phase=3, deadline=deadline)
    r.step(1)
    state(r, 1, active=True)


def own_global_bit_change_cancels_but_restore_does_not_restart(r):
    sid = 12
    for offset in (WRSC, CAD, PCB):
        configure_slice(r, sid)
        request(r, sid)
        r.step(200)
        original = r.reg(offset)
        r.write(offset, original ^ (1 << sid))
        state(r, sid)
        r.write(offset, original)
        r.step(10_000)
        state(r, sid)
    complete(r, sid)


def wrong_or_duplicate_falling_edges_cancel_without_replay(r):
    sid = 11
    configure_slice(r, sid)
    for writes in ((2,), (4,), (3, 4), (5, 2), (3, 2, 2), (3, 2, 0)):
        for value in writes:
            r.write(sid*0x1000, value)
        r.step(10_000)
        state(r, sid)
    # A repeated high edge is a newly armed request, never a completed one.
    request(r, sid)
    r.step(500)
    r.write(sid*0x1000, 3)
    r.step(10_000)
    state(r, sid, phase=1)
    r.write(sid*0x1000, 2)
    r.step(DELAY)
    state(r, sid, active=True)


def concurrent_slice_deadlines_are_independent(r):
    program_source_table(r, activate_init=False)
    request(r, 2)
    first = r.last_clock+DELAY
    r.step(250)
    request(r, 6)
    second = r.last_clock+DELAY
    r.step(250)
    request(r, 10)
    r.write(10*0x1000, 0)
    r.step(499)
    state(r, 2, phase=3, deadline=first)
    state(r, 6, phase=3, deadline=second)
    r.step(1)
    state(r, 2, active=True)
    state(r, 6, phase=3, deadline=second)
    r.step(250)
    state(r, 6, active=True)
    state(r, 10)
    return {"single_scheduler_preserves_two_deadlines": True, "cancelled_slice_isolated": True}


def all_bank_words_are_readonly_views(r):
    program_source_table(r)
    before = r.world()
    for bank in range(4):
        for offset in BANK_REGISTERS:
            rejected(r, offset, bank=bank, value=0, reason="read-only-bank-view")
    assert r.world() == before
    assert_mirrors(r)
    return {"bank_write_rejections": 280}


def dynamic_status_and_reserved_bits_are_not_writable(r):
    configure_slice(r, 1)
    request(r, 1)
    before = r.world()
    for offset in STATUS+(COMMON, HW_INFO):
        rejected(r, offset, value=0xffffffff, reason="read-only-status")
    for offset, value in ((0x1000, 1), (0x1000, 6), (0x1000, 7), (0x1000, 8),
                          (0x21008, 1 << 12), (0x2100c, 1 << 8),
                          (WRSC, 1), (CAD, 1), (PCB, 1)):
        rejected(r, offset, value=value, reason="unsupported-bits")
    assert r.world() == before
    r.step(DELAY)
    state(r, 1, active=True)


def exact_sparse_ownership_and_true_holes(r):
    tree = r.monitor("human-monitor-command", {"command-line": "info mtree -f"})
    (r.directory/"mtree.txt").write_text(tree)
    pattern = r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: ("+re.escape(TYPE)+r"\.(?:bank[0-3]|broadcast)\.[0-9a-f]{5})(?:\s|$)"
    found = {(int(a, 16), int(b, 16), name) for a, b, name in re.findall(pattern, tree)}
    assert found == set(expected_leaves()), (found, expected_leaves())
    before = r.world()
    holes = [BASE+b*BANK_STRIDE+o for b in range(4)
             for o in (0, 0x1008, HW_INFO, 0x3100c, 0x50000)]
    holes += [BCAST+o for o in (0, TRP_ERROR, DRP_ERROR, 0x30004, 0x3100c, 0x50000)]
    for address in holes:
        old = core_diagnostics(r.log_path.read_text(errors="replace"))
        r.raw_read(address)
        r.raw_write(address, 0xa55a5aa5)
        new = core_diagnostics(r.log_path.read_text(errors="replace"))[len(old):]
        assert new == [(op, address, 4, "(null)", "rejected") for op in ("read", "write")], (address, new)
    assert r.world() == before
    return {"exact_leaves": 139, "mapped_bytes": 1396, "true_holes_checked": len(holes)}


def subword_unaligned_and_wide_accesses_reject_atomically(r):
    configure_slice(r, 1)
    r.write(0x1000, 3)
    before = r.world()
    for bank in (None, 0, 3):
        for width, delta in (("b", 0), ("w", 0), ("w", 1), ("l", 1), ("q", 0)):
            rejected(r, 0x1000+delta, bank=bank, write=False, width=width)
            rejected(r, 0x1000+delta, bank=bank, value=0xffffffffffffffff if width == "q" else 1, width=width)
    assert r.world() == before
    return {"width_preserved_inside_eight_byte_control_status_leaf": True,
            "cross_leaf_partial_commit_claimed": False}


def polling_and_qom_reads_do_not_advance_time(r):
    configure_slice(r, 13)
    request(r, 13)
    before = r.world()
    now = r.last_clock
    for _ in range(128):
        assert r.read(0xd004) == 2 and r.slice(13, "status") == 2
    assert r.world() == before and r.last_clock == now
    r.step(DELAY-1)
    assert r.read(0xd004) == 2
    r.step(1)
    assert r.read(0xd004) == 1


def active_state_survives_bad_configuration_and_can_deactivate(r):
    sid = 24
    configure_slice(r, sid)
    complete(r, sid)
    r.write(0x21000+sid*8, 0)
    assert not r.slice(sid, "recipe-valid")
    state(r, sid, active=True)
    complete(r, sid, False)
    request(r, sid)
    r.step(10_000)
    state(r, sid)
    return {"stable_active_is_control_state_not_cache_data_validity": True}


def reset_clears_armed_pending_active_and_all_programming(r):
    cold = r.world()
    program_source_table(r)
    r.write(2*0x1000, 3)
    request(r, 6)
    request(r, 13, False)
    assert r.slice(1, "active") and r.slice(2, "phase") == 1
    assert r.slice(6, "phase") == 3 and r.slice(13, "phase") == 4
    r.monitor("system_reset")
    assert r.world() == cold
    r.step(10_000)
    assert r.world() == cold
    assert_mirrors(r)


def unknown_version_mc_and_observers_remain_readonly(r):
    configure_slice(r, 1)
    complete(r, 1)
    before = r.world()
    for name in ("hardware-version-known", "memory-controller-count-known",
                 "cache-data-modelled", "performance-counters-modelled", "ecc-error-injection-modelled"):
        assert r.qom(name) is False
    assert r.read(HW_INFO) == 0 and ((r.read(COMMON) >> 10) & 3) == 0
    for bank in range(4):
        assert r.read(TRP_ERROR, bank) == r.read(DRP_ERROR, bank) == 0
    for name, value in (("bank-count", 8), ("reg.21008", 0), ("written.21008", False),
                        ("slice1.active", False), ("slice1.phase", 3),
                        ("slice1.deadline-ns", 0), ("hardware-version-known", True)):
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": name, "value": value})
        assert "error" in response, response
    assert r.world() == before
    return {"zero_hw_info_and_mc_are_unknown_not_silicon_claims": True,
            "cold_ecc_status_is_not_error_injection_support": True}


ORDINARY = (
    cold_geometry_identity_and_pure_getters,
    original_kona_probe_order_and_all_bank_mirrors,
    all_sixteen_slices_activate_and_deactivate,
    trigger_high_is_armed_until_matching_falling_edge,
    deactivation_preserves_active_until_deadline,
    explicit_zero_write_credit_is_required,
    incomplete_or_wrong_slice_recipe_cannot_activate,
    related_configuration_change_cancels_armed_and_pending,
    unrelated_slice_and_other_global_bits_preserve_transaction,
    own_global_bit_change_cancels_but_restore_does_not_restart,
    wrong_or_duplicate_falling_edges_cancel_without_replay,
    concurrent_slice_deadlines_are_independent,
    all_bank_words_are_readonly_views,
    dynamic_status_and_reserved_bits_are_not_writable,
    exact_sparse_ownership_and_true_holes,
    subword_unaligned_and_wide_accesses_reject_atomically,
    polling_and_qom_reads_do_not_advance_time,
    active_state_survives_bad_configuration_and_can_deactivate,
    reset_clears_armed_pending_active_and_all_programming,
    unknown_version_mc_and_observers_remain_readonly,
)


def default_off_rejects_every_physical_word(args, out):
    r = LlccRig(args, out/"default-off", reference=None)
    try:
        before = r.world()
        assert before["globals"]["enabled-profile"] == 0
        assert before["scope"]["enabled-reference"] is False
        assert before["scope"]["bank-count-known"] is False
        for item in before["slices"].values():
            assert item["state-known"] is False and item["status"] == 3
            assert item["active"] is item["recipe-valid"] is False
        count = 0
        for view in range(5):
            offsets = BANK_REGISTERS if view < 4 else BCAST_REGISTERS
            for offset in offsets:
                bank = view if view < 4 else None
                rejected(r, offset, bank=bank, write=False)
                rejected(r, offset, bank=bank, value=0)
                count += 1
        assert count == 349
        r.step(100_000)
        assert r.world() == before
        return {"physical_words_tested": count, "unknown_status_has_both_poll_bits": True}
    finally:
        r.close()


def paused_tcg_exposes_no_mutating_fixture_or_guest_execution(args, out):
    r = LlccRig(args, out/"paused-tcg", accel="tcg")
    try:
        assert r.monitor("query-status")["running"] is False
        before = r.world()
        properties = r.monitor("qom-list", {"path": DEVICE})
        assert not [p["name"] for p in properties if p["name"].startswith("qtest-")]
        response = r.raw_monitor("qom-set", {"path": DEVICE, "property": "qtest-state", "value": 1})
        assert "error" in response and "not found" in response["error"]["desc"]
        assert r.world() == before
        assert r.device_mmio_operations == r.cont_commands == 0
        assert r.monitor("query-status")["running"] is False
        return {"fixture_override_absent": True, "guest_cpu_executed": False}
    finally:
        r.close()


def llcc_payload(world):
    """Independent literal v1 field order; each byte is compared with the stream."""
    raw = bytearray(struct.pack(">13I2Q2I", *(world["globals"][n] for n in GLOBAL_FIELDS),
                                *world["region_bases"], *world["region_bytes"]))
    raw.extend(struct.pack(">71I", *(world["registers"][f"{o:05x}"] for o in REGISTERS)))
    written = [world["written"][f"{o:05x}"] for o in REGISTERS]
    active = [world["slices"][str(sid)]["active"] for sid in SIDS]
    assert all(type(v) is bool for v in written+active)
    raw.extend(bytes(written))
    raw.extend(bytes(active))
    raw.extend(bytes(world["slices"][str(sid)]["phase"] for sid in SIDS))
    for field in ("captured-attr0", "captured-attr1", "captured-flags"):
        raw.extend(struct.pack(">16I", *(world["slices"][str(sid)][field] for sid in SIDS)))
    raw.extend(struct.pack(">16q", *(world["slices"][str(sid)]["deadline-ns"] for sid in SIDS)))
    assert len(raw) == VMSTATE_BYTES
    return bytes(raw)


def inspect_stream(path, world):
    data, expected = path.read_bytes(), llcc_payload(world)
    section = unique_section(data, TYPE, 1, VMSTATE_BYTES)
    offset = section["payload_offset"]
    actual = data[offset:offset+VMSTATE_BYTES]
    assert actual == expected, [i for i, pair in enumerate(zip(actual, expected)) if pair[0] != pair[1]][:32]
    payload_path = path.with_name(path.name+".llcc-v1.payload.bin")
    payload_path.write_bytes(actual)
    section.update(payload_file=payload_path.name, payload_sha256=digest(payload_path),
                   all_bytes_match_stopped_source_qom=True)
    result = {"llcc": section}
    write_json(path.with_name(path.name+".sections.json"), result)
    return result


def save_source(r, path, witness):
    r.raw_write(RAM, witness)
    r.monitor("stop")
    assert r.monitor("query-status")["running"] is False
    snapshot = r.world()
    write_json(r.directory/"source-before-save.json", snapshot)
    r.monitor("migrate", file_channels(path))
    sent = wait_migration(r)
    assert sent["status"] == "completed"
    assert r.monitor("query-status")["status"] == "postmigrate"
    after = r.world()
    assert after == snapshot
    write_json(r.directory/"source-after-save.json", after)
    write_json(r.directory/"source-query-migrate.json", sent)
    sections = inspect_stream(path, snapshot)
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest(path),
            "source_directory": r.directory.name, "source_virtual_ns": r.last_clock,
            "source_status": "completed", "source_query_migrate": sent,
            "snapshot": snapshot, "sections": sections, "ram_witness": witness,
            "stream_modified": False}


def prewarm_target(r, saved):
    r.set_clock(saved["source_virtual_ns"])
    program_source_table(r, activate_init=False)
    request(r, 6)
    state(r, 6, phase=3, deadline=r.last_clock+DELAY)
    r.raw_write(RAM, saved["ram_witness"] ^ 0xffffffff)
    stale = r.world()
    assert stale != saved["snapshot"]
    write_json(r.directory/"destination-before-incoming.json", {
        "snapshot": stale, "device_mmio_count": r.device_mmio_operations,
        "qmp_cont_count": r.cont_commands, "qom_get_count": r.qom_get_commands,
        "source_epoch_ns": saved["source_virtual_ns"], "stale_pending_sid": 6})


def receive_source(r, path, saved):
    r.set_clock(saved["source_virtual_ns"])
    mmio, cont = r.device_mmio_operations, r.cont_commands
    r.monitor("migrate-incoming", file_channels(path))
    received = wait_migration(r)
    assert received["status"] == "completed"
    assert r.monitor("query-status")["running"] is False
    before = r.qom_get_commands
    first = r.world()
    after = r.qom_get_commands
    assert first == saved["snapshot"], (first, saved["snapshot"])
    assert (r.device_mmio_operations, r.cont_commands) == (mmio, cont)
    assert after-before == FIRST_QOM_COUNT
    evidence = {
        "snapshot": first, "query_migrate": received,
        "device_mmio_count_before_incoming": mmio,
        "device_mmio_count_after_first_observation": r.device_mmio_operations,
        "qmp_cont_count_before_incoming": cont,
        "qmp_cont_count_after_first_observation": r.cont_commands,
        "qom_get_count_before_first_observation": before,
        "qom_get_count_after_first_observation": after,
        "first_observation_qom_get_count": after-before,
        "device_mmio_since_incoming": False, "qmp_cont_since_incoming": False,
        "guest_cpu_executed": False, "shared_log_timestamp_claimed": False,
    }
    # This independent RAM observation is deliberately after all pure getters.
    assert r.raw_read(RAM) == saved["ram_witness"]
    evidence["ram_witness"] = saved["ram_witness"]
    write_json(r.directory/"destination-before-first-device-mmio.json", evidence)
    return evidence


def load_failure(r, path, diagnostic):
    error = None
    try:
        r.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as exc:
        error = str(exc)
    code = r.process.wait(timeout=20)
    stderr = r.log_path.read_text(errors="replace")
    assert code == 1, (code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {"destination_exit": code, "expected_diagnostic": diagnostic,
            "specific_load_failure_observed": True, "command_error": error,
            "target_directory": r.directory.name}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **options):
        r = LlccRig(args, out/name, **options)
        rigs.append(r)
        return r

    def passed(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, source, target, first, **details):
        report["migrations"].append({
            "name": name, "passed": True, "source_status": "completed", "target_status": "completed",
            "snapshot_file": path.name, "snapshot_sha256": digest(path), "stream_modified": False,
            "source_directory": source.directory.name, "target_directory": target.directory.name,
            "first_target_observation": first, "continued_after_restore": True,
            "all_783_payload_bytes_verified": True, **details})
        passed(name, first_observation_mmio_delta=0, first_observation_cont_delta=0, **details)

    try:
        steady = new("migration-steady-source")
        program_source_table(steady)
        complete(steady, 30)
        # Preserve live but subsequently invalid configuration as a control
        # state, not a claim that cache data remains valid or coherent.
        steady.write(0x21000+30*8, 0)
        assert steady.slice(30, "active") and not steady.slice(30, "recipe-valid")
        steady.set_clock(100_000)
        steady_path = out/"llcc-steady.migration"
        saved_steady = save_source(steady, steady_path, 0x4c4c4301)
        report["migration_sources"].append(saved_steady)
        steady.close()
        target = new("migration-steady-target", incoming=True)
        prewarm_target(target, saved_steady)
        first = receive_source(target, steady_path, saved_steady)
        target.monitor("cont")
        assert_mirrors(target)
        complete(target, 30, False)
        request(target, 1, False)
        target.write(0x1000, 0)
        target.step(10_000)
        state(target, 1, active=True)
        complete(target, 1, False)
        completed("migration_steady_control_state", steady_path, steady, target, first,
                  active_bad_config_preserved=True, post_restore_cancel_and_new_deactivation=True)
        target.close()

        armed = new("migration-armed-source")
        program_source_table(armed)
        armed.set_clock(200_000)
        armed.write(12*0x1000, 3)
        armed.step(5000)
        state(armed, 12, phase=1)
        armed_path = out/"llcc-armed.migration"
        saved_armed = save_source(armed, armed_path, 0x4c4c4302)
        report["migration_sources"].append(saved_armed)
        armed.close()
        target = new("migration-armed-target", incoming=True)
        prewarm_target(target, saved_armed)
        first = receive_source(target, armed_path, saved_armed)
        target.monitor("cont")
        target.step(10_000)
        state(target, 12, phase=1)
        target.write(12*0x1000, 2)
        deadline = target.last_clock+DELAY
        target.step(DELAY-1)
        state(target, 12, phase=3, deadline=deadline)
        target.step(1)
        state(target, 12, active=True)
        complete(target, 12, False)
        completed("migration_armed_edge", armed_path, armed, target, first,
                  no_timer_until_new_falling_edge=True, new_transition_ns=DELAY)
        target.close()

        activate = new("migration-activate-pending-source")
        program_source_table(activate)
        activate.set_clock(300_000)
        request(activate, 12)
        activate.step(300)
        state(activate, 12, phase=3, deadline=301_000)
        act_path = out/"llcc-activate-pending.migration"
        saved_act = save_source(activate, act_path, 0x4c4c4303)
        report["migration_sources"].append(saved_act)
        activate.close()
        target = new("migration-activate-pending-target", incoming=True)
        prewarm_target(target, saved_act)
        first = receive_source(target, act_path, saved_act)
        target.monitor("cont")
        target.step(699)
        state(target, 12, phase=3, deadline=301_000)
        assert target.read(12*0x1000+4) == 2
        target.step(1)
        state(target, 12, active=True)
        for bank in range(4):
            assert target.read(12*0x1000+4, bank) == 1
        completed("migration_pending_activation", act_path, activate, target, first,
                  completed_after_ns=700, all_bank_statuses_updated=True)
        target.close()

        deactivate = new("migration-deactivate-pending-source")
        program_source_table(deactivate)
        complete(deactivate, 30)
        deactivate.set_clock(400_000)
        request(deactivate, 30, False)
        deactivate.step(300)
        state(deactivate, 30, active=True, phase=4, deadline=401_000)
        deact_path = out/"llcc-deactivate-pending.migration"
        saved_deact = save_source(deactivate, deact_path, 0x4c4c4304)
        report["migration_sources"].append(saved_deact)
        deactivate.close()
        target = new("migration-deactivate-pending-target", incoming=True)
        prewarm_target(target, saved_deact)
        first = receive_source(target, deact_path, saved_deact)
        target.monitor("cont")
        target.step(699)
        state(target, 30, active=True, phase=4, deadline=401_000)
        assert target.read(30*0x1000+4) == 1
        target.step(1)
        state(target, 30)
        for bank in range(4):
            assert target.read(30*0x1000+4, bank) == 2
        completed("migration_pending_deactivation", deact_path, deactivate, target, first,
                  completed_after_ns=700, all_bank_statuses_updated=True)
        target.close()

        disabled = new("migration-disabled-source", reference=False)
        disabled.set_clock(500_000)
        off_path = out/"llcc-disabled.migration"
        saved_off = save_source(disabled, off_path, 0x4c4c4300)
        report["migration_sources"].append(saved_off)
        disabled.close()
        for name, path, saved, reference, diagnostic in (
                ("on_to_off", steady_path, saved_steady, False, "0 != 1"),
                ("off_to_on", off_path, saved_off, True, "1 != 0")):
            target = new("reject-profile-"+name, reference=reference, incoming=True)
            target.set_clock(saved["source_virtual_ns"])
            details = load_failure(target, path, diagnostic)
            report["rejected_migrations"].append({"name": name, "passed": True,
                "snapshot_file": path.name, "snapshot_sha256": digest(path),
                "stream_modified": False, **details})
            passed("profile_"+name+"_rejected", **details)
            target.close()

        # These are independent literal v1 offsets, not a parser imported from
        # the device or generated inventory. Exactly one bit changes per file.
        mutations = (
            ("physical_base", steady_path, saved_steady, 52, 8, BASE, 4,
             f"{BASE:x} != {BASE ^ 4:x}"),
            ("captured_attr", act_path, saved_act, 463+4*SIDS.index(12), 4, 0x0fff0000, 1,
             TYPE+": invalid migrated capture"),
            ("written", steady_path, saved_steady, 360+REGISTERS.index(COMMON), 1, 0, 1,
             TYPE+": invalid migrated written"),
            ("pending_deadline", deact_path, saved_deact, 655+8*SIDS.index(30), 8, 401_000, 1 << 63,
             TYPE+": invalid migrated transition"),
        )
        for name, source_path, saved, relative, width, old, xor, diagnostic in mutations:
            original = source_path.read_bytes()
            absolute = saved["sections"]["llcc"]["payload_offset"]+relative
            assert int.from_bytes(original[absolute:absolute+width], "big") == old
            changed = bytearray(original)
            changed[absolute:absolute+width] = (old ^ xor).to_bytes(width, "big")
            differences = [i for i, pair in enumerate(zip(original, changed)) if pair[0] != pair[1]]
            expected_byte = absolute+width-1-(xor.bit_length()-1)//8
            assert differences == [expected_byte]
            assert (original[expected_byte] ^ changed[expected_byte]).bit_count() == 1
            path = out/f"llcc-corrupt-{name}.migration"
            path.write_bytes(changed)
            target = new("reject-corrupt-"+name, incoming=True)
            target.set_clock(saved["source_virtual_ns"])
            details = load_failure(target, path, diagnostic)
            report["corrupted_stream_rejections"].append({
                "name": name, "passed": True, "source_file": source_path.name,
                "source_sha256": saved["sha256"], "snapshot_file": path.name,
                "snapshot_sha256": digest(path), "stream_modified": True,
                "field_offset": relative, "field_bytes": width, "old_value": old,
                "xor": xor, "changed_byte_offsets": differences, "changed_bits": 1,
                "original_stream_unchanged": digest(source_path) == saved["sha256"], **details})
            passed("single_bit_"+name+"_rejected", **details)
            target.close()
        assert all(digest(out / item["file"]) == item["sha256"] for item in report["migration_sources"])
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--machine", default=DEFAULT_MACHINE)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started, original_hash = time.monotonic(), digest(Path(args.qemu))
    report = {
        "schema": "tb371fc-llcc-sm8250-v1", "suite": "llcc-sm8250-reference",
        "qemu": args.qemu, "machine": args.machine, "work_dir": str(out),
        "qemu_sha256_before": original_hash, "qemu_sha256_after": None,
        "test_script_sha256": digest(Path(__file__)), "expected_checks": EXPECTED_CHECKS,
        "expected_completed_migrations": 4, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 4, "expected_unmodified_streams": 5,
        "expected_stream_files": 9, "vmstate_version": 1, "vmstate_minimum_version": 1,
        "vmstate_payload_bytes": VMSTATE_BYTES, "reference_identity": IDENTITY,
        "physical_base": BASE, "broadcast_base": BCAST, "region_count": 2,
        "banks_resource_bytes": BANKS_BYTES, "broadcast_resource_bytes": BANK_BYTES,
        "bank_count": 4, "bank_stride": BANK_STRIDE, "bank_resource_bytes": BANK_BYTES,
        "slice_count": 16, "register_count": 71, "mapped_word_count": 349,
        "sparse_window_count": 139, "mapped_bytes": 1396, "timer_count": 1,
        "input_clock_count": 0, "output_clock_count": 0, "provider_count": 0,
        "transition_delay_ns": DELAY, "first_observation_qom_get_count": FIRST_QOM_COUNT,
        "reference_enabled_by_default": False, "bank_geometry_is_reference": True,
        "physical_bank_count_measured": False, "hardware_version_known": False,
        "memory_controller_count_known": False, "cache_data_modeled": False,
        "cache_performance_modeled": False, "ecc_error_injection_modeled": False,
        "runtime_provider_revocation_tested": False, "guest_cpu_executed": False,
        "native_kernel_probe_observed": False, "android_boot_passed": False,
        "source_commit": SOURCE_COMMIT,
        "geometry_source_commit": "adc218676eef25575469234709c2d87185ca223a",
        "actual_dt_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "scope": "Compiled QTest/QMP digital control transactions and full LLCC wire validation. "
                 "Four-bank geometry is a source-backed reference; HW revision and MC count "
                 "remain unknown. No cache data, coherence, performance, ECC injection, "
                 "power-provider behavior, native guest CPU or Android boot is established.",
        "binary_unchanged": False, "checks": [], "migrations": [], "rejected_migrations": [],
        "corrupted_stream_rejections": [], "migration_sources": [], "actual_stream_files": [],
        "passed": False,
    }
    try:
        assert len(ORDINARY) == len(set(ORDINARY)) == 20
        for index, test in enumerate(ORDINARY):
            rig = None
            fixture = out/f"ordinary-{index:02d}-{test.__name__}"
            try:
                rig = LlccRig(args, fixture)
                details = test(rig) or {}
                report["checks"].append({"name": test.__name__, "passed": True,
                                         "fixture": fixture.name, **details})
            finally:
                if rig is not None:
                    rig.close()
        for test in (default_off_rejects_every_physical_word,
                     paused_tcg_exposes_no_mutating_fixture_or_guest_execution):
            details = test(args, out) or {}
            report["checks"].append({"name": test.__name__, "passed": True, **details})
        migration_checks(args, out, report)
        report["actual_stream_files"] = [
            {"name": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in sorted(out.glob("*.migration"))]
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 4 and
            all(item["source_status"] == item["target_status"] == "completed" and
                item["continued_after_restore"] and item["all_783_payload_bytes_verified"] and
                item["first_target_observation"]["first_observation_qom_get_count"] == FIRST_QOM_COUNT and
                item["first_target_observation"]["device_mmio_since_incoming"] is False and
                item["first_target_observation"]["qmp_cont_since_incoming"] is False
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and len(report["corrupted_stream_rejections"]) == 4 and
            all(item["passed"] is True and item["specific_load_failure_observed"]
                for item in report["rejected_migrations"]+report["corrupted_stream_rejections"]) and
            len(report["migration_sources"]) == 5 and len(report["actual_stream_files"]) == 9 and
            len({item["name"] for item in report["actual_stream_files"]}) == 9 and
            all(item["bytes"] > 0 for item in report["actual_stream_files"]) and
            report["binary_unchanged"])
    except BaseException as error:
        report["error"], report["traceback"] = str(error), traceback.format_exc()
        report["qemu_sha256_after"] = digest(Path(args.qemu))
        report["binary_unchanged"] = report["qemu_sha256_after"] == original_hash
    report["duration_seconds"] = round(time.monotonic()-started, 6)
    write_json(out/"result.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
