#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Public synthetic gate for the explicit, bounded AOP SCS reference profile.

This file never opens firmware. The original entry runner and its fourteen
checks are unchanged. Core-register state tests use QEMU's existing NVIC.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time
import traceback

BASE_SHA256 = "18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b"
BASE_PATH = Path(__file__).with_name("test_aop_entry_reference.py")
PROFILE = "local-alias-dtcm32k-scs-reference"
EXPECTED_CHECKS = 18
SCS = 0xe000e000
WINDOWS = [
    ("ictr",  0x004,  4, True, False, False),
    ("iser0", 0x100,  4, True, True, False),
    ("icer0", 0x180,  4, True, True, False),
    ("ispr0", 0x200,  4, True, True, False),
    ("icpr0", 0x280,  4, True, True, False),
    ("iabr0", 0x300,  4, True, False, False),
    ("ipr",   0x400, 32, True, True, True),
    ("cpuid", 0xd00,  4, True, False, False),
    ("icsr",  0xd04,  4, True, True, False),
    ("vtor",  0xd08,  4, True, True, False),
    ("aircr", 0xd0c,  4, True, True, False),
    ("ccr",   0xd14,  4, True, True, False),
    ("shpr",  0xd18, 12, True, True, True),
    ("shcsr", 0xd24,  4, True, True, False),
]
LOAD = {1: bytes.fromhex("0878"), 2: bytes.fromhex("0888"),
        4: bytes.fromhex("0868")}
STORE = {1: bytes.fromhex("0870"), 2: bytes.fromhex("0880"),
         4: bytes.fromhex("0860")}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


if not __debug__:
    raise RuntimeError("Assertions must remain enabled")
if digest(BASE_PATH) != BASE_SHA256:
    raise RuntimeError("Original entry runner does not match the reviewed source")
spec = importlib.util.spec_from_file_location("aop_entry_reference_base", BASE_PATH)
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)


def expected_windows():
    return [{"name": name, "base": SCS + offset, "size": size,
             "read": read, "write": write, "subword": subword}
            for name, offset, size, read, write, subword in WINDOWS]


def ram_layout():
    return base.layout(base.PROFILES[2])


def guarded_layout():
    return ram_layout() + [[SCS + offset, size, 4 | (2 if write else 0)]
                           for _, offset, size, _, write, _ in WINDOWS]


class ScsReference(base.Reference):
    """Use the unchanged RSP, observer, state and cleanup implementations."""

    def __init__(self, qemu, output):
        output.mkdir()
        self.output = output
        self.profile = PROFILE
        self.qmp = self.rsp = self.process = self.log = None
        self.socket_dir = Path(tempfile.mkdtemp(prefix="aopscs-", dir="/tmp"))
        self.initial = None
        try:
            self.log = (output / "qemu.log").open("wb")
            command = [
                str(qemu), "-M", "aop-entry-reference,profile=" + PROFILE,
                "-accel", "tcg,thread=single", "-S", "-nodefaults",
                "-display", "none", "-monitor", "none", "-serial", "none",
                "-no-reboot", "-no-shutdown",
                "-qmp", "unix:" + str(self.socket_dir / "qmp") +
                ",server=on,wait=off",
                "-gdb", "unix:" + str(self.socket_dir / "gdb") +
                ",server=on,wait=off",
            ]
            base.save(output / "launch.json", {
                "phase": "before-process-start", "command": command,
                "socket_directory": str(self.socket_dir), "retained": True,
                "firmware_read": False,
            })
            self.process = subprocess.Popen(
                command, stdout=self.log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 3
            while not all((self.socket_dir / x).exists() for x in ("qmp", "gdb")):
                assert self.process.poll() is None, "QEMU exited during startup"
                assert time.monotonic() < deadline, "QEMU socket startup timeout"
                time.sleep(.005)
            self.qmp = base.QMP(self.socket_dir / "qmp")
            self.rsp = base.RSP(self.socket_dir / "gdb")
            cpus = self.qmp.call("query-cpus-fast")
            assert len(cpus) == 1, cpus
            children = self.qmp.call("qom-list", {"path": "/machine/armv7m"})
            assert any(x["name"] == "cpu" and
                       x["type"] == "child<cortex-m3-arm-cpu>" for x in children)
            expected = ";".join("0x%x:0x%x:%d" % tuple(r) for r in ram_layout())
            assert self.qmp.get("reference-layout") == expected
            assert json.loads(self.qmp.get("scs-reference-windows")) == expected_windows()
            assert self.qmp.get("padding-pristine") is True
            self.step_bits_raw = self.rsp.packet("qqemu.sstepbits").decode("ascii")
            self.step_flags_raw = self.rsp.packet("qqemu.sstep").decode("ascii")
            self.step_bits = {
                k: int(v, 16)
                for k, v in (x.split("=") for x in self.step_bits_raw.split(","))
            }
            assert set(self.step_bits) == {"ENABLE", "NOIRQ", "NOTIMER"}
            assert self.step_bits == {"ENABLE": 1, "NOIRQ": 2, "NOTIMER": 4}
            assert int(self.step_flags_raw, 16) == 7
            self.initial = self.state()
            self.start_bus_counts = self.counts()
            state = self.scs()
            assert state["enabled"] is True
            assert state["external_irqs"] == 32
            assert state["priority_bits"] == 8
            assert state["reference_clock_hz"] == 1_000_000
            assert self.scs_counts() == [0, 0, 0, 0]
        except BaseException:
            self.close()
            raise

    def scs(self):
        result = json.loads(self.qmp.get("scs-reference-state"))
        assert set(result) == {
            "enabled", "external_irqs", "priority_bits", "reference_clock_hz",
            "successful_reads", "successful_writes", "failed_reads",
            "failed_writes",
        }
        assert type(result["enabled"]) is bool
        assert all(type(v) is int for k, v in result.items() if k != "enabled")
        return result

    def scs_counts(self):
        state = self.scs()
        return [state[k] for k in ("successful_reads", "successful_writes",
                                  "failed_reads", "failed_writes")]

    def prepare(self, instruction, address=base.CODE, registers=None):
        self.rsp.write(address, instruction)
        self.rsp.set_reg("xpsr", 0x01000000)
        self.rsp.set_reg("sp", 0xe8000)
        self.rsp.set_reg("pc", address)
        assert self.rsp.reg("msp") == 0xe8000
        for name, value in (registers or {}).items():
            self.rsp.set_reg(name, value)
        self.watchpoints = self.rsp.guards(guarded_layout())
        assert self.watchpoint_snapshot() == [], "Stale hit before first access"

    def snapshot(self):
        return {address: self.rsp.read(address, size)
                for address, size, _ in ram_layout()[:5]}

    def initialize_scalar_probes(self):
        # LDR[B/H]/STR[B/H] r0,[r1], with no synthetic peripheral results.
        self.probe_addresses = {}
        cursor = base.CODE
        data = bytearray()
        for write, encodings in ((False, LOAD), (True, STORE)):
            for size, instruction in encodings.items():
                self.probe_addresses[(write, size)] = cursor
                data.extend(instruction)
                cursor += len(instruction)
        self.prepare(bytes(data))

    def access(self, address, size=4, value=None):
        write = value is not None
        before_scs = self.scs_counts()
        before_bus = self.counts()
        pc = self.probe_addresses[(write, size)]
        self.rsp.set_reg("pc", pc)
        self.rsp.set_reg("r1", address)
        self.rsp.set_reg("r0", value if write else 0xdecafbad)
        stop = self.rsp.step()
        assert base.watch_address(stop) is None, stop
        assert self.rsp.reg("pc") == pc + 2
        assert self.rsp.reg("xpsr") & 0x1ff == 0
        assert self.counts() == before_bus
        expected = list(before_scs)
        expected[1 if write else 0] += 1
        assert self.scs_counts() == expected, (before_scs, self.scs_counts())
        assert self.qmp.get("padding-pristine") is True
        return self.rsp.reg("r0")


def new_reference(args, out):
    r = ScsReference(args.qemu, out)
    try:
        r.initialize_scalar_probes()
        return r
    except BaseException:
        r.close()
        raise


def negative(args, out, instruction, target, access, allowed_prefix=0):
    with ScsReference(args.qemu, out) as r:
        registers = {"r0": 0x55667788, "r1": target, "r2": target}
        if instruction == bytes.fromhex("c2e90001"):
            registers["r1"] = 0xa1b2c3d4
        r.prepare(instruction, registers=registers)
        ram_before = r.snapshot()
        state_before = r.state()
        bus_before = r.counts()
        scs_before = r.scs_counts()
        stop = r.rsp.step()
        hit = r.watchpoint_hit(stop, access)
        assert hit["hitaddr"] == target + allowed_prefix, hit
        assert r.rsp.reg("pc") == base.CODE
        assert r.counts() == bus_before
        assert r.qmp.get("padding-pristine") is True
        assert r.snapshot() == ram_before
        expected_scs = list(scs_before)
        expected_scs[1] += 1 if allowed_prefix else 0
        assert r.scs_counts() == expected_scs
        if access == "read":
            assert r.rsp.reg("r0") == state_before["r0"]
        partial = None
        if allowed_prefix:
            assert allowed_prefix == 4 and target == SCS + 0x41c
            partial = r.rsp.read(target, 4).hex()
            assert partial == struct.pack("<I", registers["r0"]).hex()
        return {
            "watchpoint_evidence": hit, "fault_address": hit["hitaddr"],
            "pc_before": base.CODE, "pc_after": r.rsp.reg("pc"),
            "unsupported_callbacks_unchanged": True, "ram_unchanged": True,
            "invalid_backing_padding_unchanged": True,
            "successful_scs_writes_before_stop": int(bool(allowed_prefix)),
            "earlier_legal_scs_write": partial, "resumed_after_fault": False,
        }


def run(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    initial_hashes = {
        "qemu": digest(args.qemu), "runner": digest(__file__),
        "base_runner": digest(BASE_PATH),
    }
    started = time.monotonic()
    result = {
        "schema": "aop-scs-reference-synthetic-v1",
        "passed": False, "all_passed": False, "expected_checks": EXPECTED_CHECKS,
        "checks": [], "firmware_read": False, "firmware_executed": False,
        "actual_aop_cpu": "unknown", "actual_aop_irq_count": "unknown",
        "actual_aop_reset_map": "unknown", "full_firmware_start": False,
        "full_tablet_equivalence": False,
        "internal_exception_before_access_proven": False,
        "reference_profile": PROFILE, "reference_external_irqs": 32,
        "reference_priority_bits": 8, "reference_clock_hz": 1_000_000,
        "qemu_sha256": initial_hashes["qemu"],
        "runner_sha256": initial_hashes["runner"],
        "original_runner_sha256": initial_hashes["base_runner"],
        "single_step_keeps_noirq_and_notimer": True,
        "irq_handler_delivery_tested": False,
    }

    def case(name, function):
        try:
            detail = function(out / ("%02d" % len(result["checks"])))
            result["checks"].append({"name": name, "passed": True, "detail": detail})
        except BaseException:
            result["checks"].append({
                "name": name, "passed": False, "exception": traceback.format_exc()})
            base.save(out / "result.json", result)
            raise
        base.save(out / "result.json", result)

    try:
        def model(path):
            with ScsReference(args.qemu, path) as r:
                return {"state": r.scs(), "windows": expected_windows(),
                        "sstepbits_raw": r.step_bits_raw,
                        "sstep_flags_raw": r.step_flags_raw,
                        "reference_initial_core_state": r.initial,
                        "soc_peripheral_responses": 0}
        case("explicit-reference-geometry-and-single-step-contract", model)

        def old(path):
            path.mkdir()
            evidence = []
            for index, profile in enumerate(base.PROFILES):
                evidence.append(base.negative(
                    args.qemu, path / str(index), LOAD[4], SCS + 4,
                    profile=profile))
            return {"unchanged_profile_count": 3, "before_bus_stops": evidence,
                    "original_runner_sha256": digest(BASE_PATH)}
        case("all-three-original-profiles-still-guard-ictr", old)

        def ids(path):
            with new_reference(args, path) as r:
                count = r.scs()["external_irqs"]
                ictr = r.access(SCS + 4)
                cpuid = r.access(SCS + 0xd00)
                assert ictr == count // 32 - 1 == 0
                assert cpuid & 0xff00fff0 == 0x4100c230
                return {"external_irqs": count, "ictr": ictr, "cpuid": cpuid,
                        "identity_is_reference_only": True}
        case("real-nvic-ictr-and-cortex-m3-cpuid", ids)

        def enable(path):
            with new_reference(args, path) as r:
                assert r.access(SCS + 0x100) == 0
                r.access(SCS + 0x100, value=3)
                r.access(SCS + 0x100, value=5)
                r.access(SCS + 0x100, value=0)
                assert r.access(SCS + 0x100) == 7
                assert r.access(SCS + 0x180) == 7
                return {"write_one_to_set": True, "enable_readback": 7}
        case("nvic-enable-write-one-to-set", enable)

        def disable(path):
            with new_reference(args, path) as r:
                r.access(SCS + 0x100, value=7)
                r.access(SCS + 0x180, value=2)
                r.access(SCS + 0x180, value=0)
                assert r.access(SCS + 0x100) == 5
                assert r.access(SCS + 0x180) == 5
                r.access(SCS + 0x180, value=0xffffffff)
                assert r.access(SCS + 0x100) == 0
                return {"write_one_to_clear": True, "final_enable": 0}
        case("nvic-disable-write-one-to-clear", disable)

        def pending(path):
            with new_reference(args, path) as r:
                assert r.access(SCS + 0x100) == 0
                r.access(SCS + 0x200, value=0x80000001)
                r.access(SCS + 0x200, value=0)
                assert r.access(SCS + 0x200) == 0x80000001
                assert r.access(SCS + 0x280) == 0x80000001
                assert r.access(SCS + 0x300) == 0
                r.access(SCS + 0x280, value=1)
                assert r.access(SCS + 0x200) == 0x80000000
                r.access(SCS + 0x280, value=0x80000000)
                assert r.access(SCS + 0x200) == 0
                return {"disabled_pending_latches": True,
                        "pending_clears_by_written_bits": True,
                        "active_remains_zero": True}
        case("nvic-pending-latches-while-disabled-and-clears", pending)

        def priority(path):
            with new_reference(args, path) as r:
                r.access(SCS + 0x400, value=0x12345678)
                r.access(SCS + 0x401, size=1, value=0xab)
                assert r.access(SCS + 0x400) == 0x1234ab78
                r.access(SCS + 0x402, size=2, value=0xcdef)
                assert r.access(SCS + 0x400) == 0xcdefab78
                assert r.access(SCS + 0x401, size=1) == 0xab
                assert r.access(SCS + 0x402, size=2) == 0xcdef
                r.access(SCS + 0x41f, size=1, value=0x95)
                assert r.access(SCS + 0x41f, size=1) == 0x95
                return {"byte_halfword_word_actual_cpu_accesses": True,
                        "priority_bits_reference": 8,
                        "final_first_word": 0xcdefab78, "last_priority": 0x95}
        case("nvic-priority-byte-halfword-word-lanes", priority)

        def vtor(path):
            with new_reference(args, path) as r:
                r.access(SCS + 0xd08, value=0xe017f)
                assert r.access(SCS + 0xd08) == 0xe0100
                r.access(SCS + 0xd08, value=0)
                assert r.access(SCS + 0xd08) == 0
                return {"low_seven_bits_masked": True, "final_vtor": 0,
                        "vector_fetch_not_exercised_by_this_case": True}
        case("vtor-state-and-alignment-mask", vtor)

        def aircr(path):
            with new_reference(args, path) as r:
                initial = r.access(SCS + 0xd0c)
                assert initial == 0xfa050000
                r.access(SCS + 0xd0c, value=0xdead0500)
                assert r.access(SCS + 0xd0c) == initial
                r.access(SCS + 0xd0c, value=0x05fa0300)
                assert r.access(SCS + 0xd0c) == 0xfa050300
                r.access(SCS + 0xd0c, value=0xbeef0500)
                assert r.access(SCS + 0xd0c) == 0xfa050300
                return {"wrong_key_ignored": True, "keyed_prigroup": 3,
                        "reset_not_requested": True}
        case("aircr-real-key-and-priority-group-behavior", aircr)

        def ccr(path):
            with new_reference(args, path) as r:
                r.access(SCS + 0xd14, value=0x210)
                assert r.access(SCS + 0xd14) == 0x210
                r.access(SCS + 0xd14, value=0x200)
                assert r.access(SCS + 0xd14) == 0x200
                return {"trap_and_stack_alignment_state_roundtrip": True}
        case("ccr-configuration-state-roundtrip", ccr)

        def system_priority(path):
            with new_reference(args, path) as r:
                r.access(SCS + 0xd18, value=0x11223344)
                assert r.access(SCS + 0xd18) == 0x00223344
                r.access(SCS + 0xd18, size=1, value=0x55)
                assert r.access(SCS + 0xd18) == 0x00223355
                r.access(SCS + 0xd1c, value=0xc0000000)
                r.access(SCS + 0xd20, value=0xffff0080)
                assert r.access(SCS + 0xd1c) == 0xc0000000
                assert r.access(SCS + 0xd20) == 0xffff0080
                r.access(SCS + 0xd24, value=0x70000)
                assert r.access(SCS + 0xd24) & 0x70000 == 0x70000
                return {"fault_handler_enables": 0x70000,
                        "system_priorities_actual_state": True,
                        "reserved_shpr_lane_reads_zero": True}
        case("system-handler-priority-and-fault-enables", system_priority)

        def icsr(path):
            with new_reference(args, path) as r:
                r.access(SCS + 0xd04, value=1 << 28)
                assert r.access(SCS + 0xd04) & (1 << 28)
                r.access(SCS + 0xd04, value=1 << 27)
                assert r.access(SCS + 0xd04) & (1 << 28) == 0
                assert r.rsp.reg("xpsr") & 0x1ff == 0
                return {"pendsv_set_and_clear": True,
                        "single_step_noirq_kept": True,
                        "handler_delivery_claimed": False}
        case("icsr-pendsv-pending-set-clear", icsr)

        def forbidden(path):
            path.mkdir()
            addresses = [SCS + 0x008, SCS + 0x010, SCS + 0x104,
                         SCS + 0x420, SCS + 0xd3c, SCS + 0xd90,
                         0xe0001000, 0x40000000]
            evidence = []
            for index, address in enumerate(addresses):
                evidence.append(negative(args, path / str(index), LOAD[4],
                                         address, "read"))
            return {"unsupported_addresses": addresses,
                    "before_bus_and_no_scs_dispatch": evidence}
        case("unlisted-ppb-and-soc-accesses-still-stop-before-bus", forbidden)

        def readonly(path):
            path.mkdir()
            return {"writes": [
                negative(args, path / str(i), STORE[4], address, "write")
                for i, address in enumerate(
                    (SCS + 4, SCS + 0x300, SCS + 0xd00))
            ]}
        case("readonly-scs-writes-stop-before-dispatch", readonly)

        case("strd-window-boundary-retains-only-first-legal-beat", lambda path:
             negative(args, path, bytes.fromhex("c2e90001"),
                      SCS + 0x41c, "write", allowed_prefix=4))

        def malformed(path):
            with ScsReference(args.qemu, path) as r:
                replies = []
                for address, size in ((SCS + 4, 1), (SCS + 4, 2),
                                      (SCS + 5, 4), (SCS + 4, 8)):
                    before = r.scs_counts()
                    bus_before = r.counts()
                    reply = r.rsp.packet("m%x,%x" % (address, size))
                    assert reply == b"E14", (address, size, reply)
                    after = r.scs_counts()
                    bus_after = r.counts()
                    assert bus_after[0] > bus_before[0]
                    assert bus_after[1] == bus_before[1]
                    assert after[1] == before[1] and after[3] == before[3]
                    if size == 8:
                        # The legal ICTR word precedes an unlisted ACTLR word.
                        assert after[0] == before[0] + 1
                        assert after[2] == before[2]
                    else:
                        assert after[0] == before[0]
                        assert after[2] > before[2]
                    replies.append({"address": address, "size": size,
                                    "reply": reply.decode(),
                                    "scs_counts_before": before,
                                    "scs_counts_after": after,
                                    "unsupported_counts_before": bus_before,
                                    "unsupported_counts_after": bus_after})
                return {"halted_debug_bus_tests_only": True,
                        "ordinary_cpu_before_access_claimed": False,
                        "rejections": replies}
        case("strict-size-alignment-and-cross-window-debug-bus-errors", malformed)

        def debug_bits(path):
            with ScsReference(args.qemu, path) as r:
                before = r.scs_counts()
                for value in (0x05fa0001, 0x05fa0002, 0x05fa0003):
                    data = struct.pack("<I", value)
                    reply = r.rsp.packet("M%x,4:%s" % (SCS + 0xd0c, data.hex()))
                    assert reply == b"E14", reply
                after = r.scs_counts()
                assert after[1] == before[1]
                assert after[3] == before[3] + 3
                return {"unpredictable_debug_bit_writes_not_forwarded": True,
                        "halted_debug_bus_tests_only": True,
                        "ordinary_cpu_before_access_claimed": False}
        case("aircr-debug-only-unpredictable-controls-rejected", debug_bits)

        def privilege(path):
            with ScsReference(args.qemu, path) as r:
                handler = base.CODE + 0x200
                r.rsp.write(base.PHYSICAL[0][0] + 3 * 4,
                            struct.pack("<I", handler | 1))
                r.rsp.write(handler, bytes.fromhex("00bffee7"))
                # MSR CONTROL,r0; ISB; LDR r0,[r1].
                r.prepare(bytes.fromhex("80f31488bff36f8f0868"),
                          registers={"r0": 1, "r1": SCS + 4})
                assert base.watch_address(r.rsp.step()) is None
                assert r.rsp.reg("control") & 1 == 1
                assert base.watch_address(r.rsp.step()) is None
                r.rsp.set_reg("r0", 0xdecafbad)
                ram_before = r.snapshot()
                counts_before = r.scs_counts()
                bus_before = r.counts()
                stop = r.rsp.step()
                assert base.watch_address(stop) is None, stop
                assert r.rsp.reg("xpsr") & 0x1ff == 3
                assert r.rsp.reg("r0") == 0xdecafbad
                assert r.rsp.reg("pc") in (handler, handler + 2)
                assert r.counts() == bus_before
                expected = list(counts_before)
                expected[2] += 1
                assert r.scs_counts() == expected
                assert r.rsp.reg("msp") == 0xe7fe0
                changes = []
                ram_after = r.snapshot()
                stack_base = base.PHYSICAL[1][0] + 0x7fe0
                for address in ram_before:
                    for offset, (old, new) in enumerate(
                            zip(ram_before[address], ram_after[address])):
                        if old != new:
                            actual = address + offset
                            assert stack_base <= actual < stack_base + 32
                            changes.append(actual)
                assert changes, "Expected real architectural exception stacking"
                assert r.qmp.get("padding-pristine") is True
                return {"nvic_received_unprivileged_attributes": True,
                        "exception_number": 3, "stopped_pc": r.rsp.reg("pc"),
                        "architectural_stack_write_addresses": changes,
                        "invalid_load_returned_no_value": True,
                        "exception_internal_before_access_proven": False,
                        "resumed_after_fault": False}
        case("unprivileged-attrs-produce-real-nvic-fault", privilege)

        assert len(result["checks"]) == EXPECTED_CHECKS
        assert all(entry["passed"] is True for entry in result["checks"])
        result["passed"] = result["all_passed"] = True
    except BaseException:
        result["failure"] = traceback.format_exc()
    finally:
        final_hashes = {
            "qemu": digest(args.qemu), "runner": digest(__file__),
            "base_runner": digest(BASE_PATH),
        }
        result["hashes_before"] = initial_hashes
        result["hashes_after"] = final_hashes
        result["sources_and_binary_unchanged"] = initial_hashes == final_hashes
        result["elapsed_seconds"] = time.monotonic() - started
        result["passed"] = result["all_passed"] = (
            result["passed"] is True and result["sources_and_binary_unchanged"]
            and "failure" not in result
            and len(result["checks"]) == EXPECTED_CHECKS
            and all(entry["passed"] is True for entry in result["checks"]))
        base.save(out / "result.json", result)
    return 0 if result["passed"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if not __debug__:
        raise RuntimeError("Assertions must remain enabled")
    args.qemu = args.qemu.resolve(strict=True)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
