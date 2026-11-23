#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict, bounded QEMU Cortex-M3 reference; never an AOP boot verdict."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

PROFILES = ("elf-physical-entry", "local-alias-vector",
            "local-alias-dtcm32k-reference")
PHYSICAL = ((0x0b000000, 0x14f08, 5), (0x0b0e0000, 0x604c, 6),
            (0x80800000, 0xdfc8, 6), (0x80810000, 0x1384, 6),
            (0x80818000, 0x47b0, 7))
EXPECTED_IMAGE = "3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671"
CODE = PHYSICAL[0][0] + 0x100
STEP_LIMIT = 4096
HARD_SECONDS = 10
PREFLIGHT_CHECKS = 14


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def layout(profile):
    result = [list(x) for x in PHYSICAL]
    if profile == PROFILES[2]:
        result[1][1] = 0x8000
    if profile != PROFILES[0]:
        result += [[0, result[0][1], 5], [0xe0000, result[1][1], 6]]
    return result


def contains(ranges, address, size, flag=4):
    return size > 0 and any(flags & flag and base <= address and
                           address + size <= base + length
                           for base, length, flags in ranges)


class QMP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rb")
        greeting = json.loads(self.file.readline())
        assert "QMP" in greeting, repr(greeting)
        self.next_id = 0
        self.call("qmp_capabilities")

    def call(self, name, arguments=None):
        self.next_id += 1
        request = {"execute": name, "id": self.next_id}
        if arguments is not None:
            request["arguments"] = arguments
        self.sock.sendall(json.dumps(request).encode() + b"\n")
        while True:
            raw = self.file.readline()
            assert raw, "QMP disconnected"
            reply = json.loads(raw)
            if reply.get("id") != self.next_id:
                continue
            assert "error" not in reply, reply
            return reply.get("return")

    def get(self, name):
        return self.call("qom-get", {"path": "/machine", "property": name})

    def close(self):
        self.file.close()
        self.sock.close()


class RSP:
    def __init__(self, path):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(2)
        self.sock.connect(str(path))
        self.buffer = bytearray()
        self.history = []
        self.packet("qSupported:multiprocess+;qXfer:features:read+")
        initial = self.packet("?")
        assert initial[:1] in (b"S", b"T"), initial
        self.registers = {}
        self._next_register = 0
        self._read_feature("target.xml", set())
        for name in ("r0", "r1", "r2", "sp", "lr", "pc", "xpsr", "msp",
                     "psp", "control", "primask", "basepri", "faultmask"):
            assert name in self.registers, (name, self.registers)

    def byte(self):
        if not self.buffer:
            data = self.sock.recv(65536)
            assert data, "RSP disconnected"
            self.buffer.extend(data)
        value = self.buffer[0]
        del self.buffer[0]
        return value

    def receive(self):
        while True:
            value = self.byte()
            if value == ord("-"):
                raise RuntimeError("RSP peer rejected packet checksum")
            if value == ord("$"):
                break
        encoded = bytearray()
        while True:
            value = self.byte()
            if value == ord("#"):
                break
            encoded.append(value)
        received = int(bytes((self.byte(), self.byte())), 16)
        assert sum(encoded) & 0xff == received, "RSP checksum mismatch"
        self.sock.sendall(b"+")
        decoded = bytearray()
        i = 0
        while i < len(encoded):
            value = encoded[i]
            i += 1
            if value == ord("}"):
                assert i < len(encoded)
                decoded.append(encoded[i] ^ 0x20)
                i += 1
            elif value == ord("*"):
                assert decoded and i < len(encoded)
                count = encoded[i] - 29
                assert count >= 0
                decoded.extend(bytes((decoded[-1],)) * count)
                i += 1
            else:
                decoded.append(value)
        return bytes(decoded)

    def packet(self, payload):
        raw = payload.encode()
        assert all(x not in raw for x in (b"$", b"#", b"}", b"*"))
        self.sock.sendall(b"$" + raw + b"#" +
                          ("%02x" % (sum(raw) & 0xff)).encode())
        result = self.receive()
        # Record packet kinds only: no private image bytes or register payloads.
        self.history.append(payload.split(":", 1)[0].split(",", 1)[0][:32])
        return result

    def xml(self, name):
        offset = 0
        chunks = []
        while True:
            reply = self.packet("qXfer:features:read:%s:%x,800" % (name, offset))
            assert reply[:1] in (b"m", b"l"), reply
            chunks.append(reply[1:])
            offset += len(reply) - 1
            if reply[:1] == b"l":
                return ET.fromstring(b"".join(chunks))

    def _read_feature(self, name, visited):
        assert name not in visited, "Recursive GDB target XML"
        visited.add(name)
        tree = self.xml(name)
        for node in tree.iter():
            local = node.tag.rsplit("}", 1)[-1]
            if local == "include":
                self._read_feature(node.attrib["href"], visited)
            elif local == "reg":
                number = int(node.attrib.get("regnum", self._next_register))
                bits = int(node.attrib["bitsize"])
                self.registers[node.attrib["name"]] = (number, bits)
                self._next_register = number + 1

    def reg(self, name):
        number, bits = self.registers[name]
        value = self.packet("p%x" % number)
        assert len(value) == bits // 4 and b"x" not in value, (name, value)
        return int.from_bytes(bytes.fromhex(value.decode()), "little")

    def set_reg(self, name, value):
        number, bits = self.registers[name]
        reply = self.packet("P%x=%s" %
                            (number, value.to_bytes(bits // 8, "little").hex()))
        assert reply == b"OK", (name, reply)
        assert self.reg(name) == value, (name, value, self.reg(name))

    def read(self, address, size):
        result = bytearray()
        while len(result) < size:
            length = min(1024, size - len(result))
            raw = self.packet("m%x,%x" % (address + len(result), length))
            assert len(raw) == length * 2 and not raw.startswith(b"E"), raw[:80]
            result.extend(bytes.fromhex(raw.decode()))
        return bytes(result)

    def write(self, address, data):
        for offset in range(0, len(data), 512):
            chunk = data[offset:offset + 512]
            reply = self.packet("M%x,%x:%s" %
                                (address + offset, len(chunk), chunk.hex()))
            assert reply == b"OK", (hex(address + offset), reply)

    def guards(self, ranges):
        ordered = sorted(ranges)
        cursor = 0
        installed = []
        for base, length, flags in ordered:
            assert cursor <= base, "Overlapping visible ranges"
            if base > cursor:
                installed.append((4, cursor, base - cursor))
            if not flags & 2:
                installed.append((2, base, length))
            cursor = base + length
        if cursor < 2**32:
            installed.append((4, cursor, 2**32 - cursor))
        for typ, base, length in installed:
            assert self.packet("Z%x,%x,%x" % (typ, base, length)) == b"OK"
        return [{"type": t, "base": b, "length": n} for t, b, n in installed]

    def step(self):
        stop = self.packet("s")
        assert stop.startswith(b"T05") or stop == b"S05", stop
        return stop.decode()

    def close(self):
        self.sock.close()


class Reference:
    def __init__(self, qemu, output, profile=PROFILES[0]):
        output.mkdir()
        self.output = output
        self.profile = profile
        self.qmp = self.rsp = self.process = self.log = None
        self.socket_dir = Path(tempfile.mkdtemp(prefix="aopq-", dir="/tmp"))
        self.initial = None
        try:
            self.log = (output / "qemu.log").open("wb")
            command = [str(qemu), "-M", "aop-entry-reference,profile=" + profile,
                       "-accel", "tcg,thread=single", "-S", "-nodefaults",
                       "-display", "none", "-monitor", "none", "-serial", "none",
                       "-no-reboot", "-no-shutdown",
                       "-qmp", "unix:" + str(self.socket_dir / "qmp") +
                       ",server=on,wait=off",
                       "-gdb", "unix:" + str(self.socket_dir / "gdb") +
                       ",server=on,wait=off"]
            save(output / "launch.json", {
                "phase": "before-process-start", "command": command,
                "socket_directory": str(self.socket_dir), "retained": True})
            self.process = subprocess.Popen(command, stdout=self.log,
                                            stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 3
            while not all((self.socket_dir / x).exists() for x in ("qmp", "gdb")):
                assert self.process.poll() is None, "QEMU exited during startup"
                assert time.monotonic() < deadline, "QEMU socket startup timeout"
                time.sleep(.005)
            self.qmp = QMP(self.socket_dir / "qmp")
            self.rsp = RSP(self.socket_dir / "gdb")
            cpus = self.qmp.call("query-cpus-fast")
            assert len(cpus) == 1, cpus
            children = self.qmp.call("qom-list", {"path": "/machine/armv7m"})
            assert any(x["name"] == "cpu" and
                       x["type"] == "child<cortex-m3-arm-cpu>" for x in children)
            expected = ";".join("0x%x:0x%x:%d" % tuple(r) for r in layout(profile))
            assert self.qmp.get("reference-layout") == expected
            assert self.qmp.get("padding-pristine") is True
            self.initial = self.state()
            self.start_bus_counts = self.counts()
        except BaseException:
            self.close()
            raise

    def state(self):
        names = ["r%d" % i for i in range(13)] + ["sp", "lr", "pc", "xpsr",
                 "msp", "psp", "control", "primask", "basepri", "faultmask"]
        return {name: self.rsp.reg(name) for name in names}

    def counts(self):
        return [self.qmp.get("unsupported-read-count"),
                self.qmp.get("unsupported-write-count")]

    def prepare(self, instruction, address=CODE, registers=None):
        self.rsp.write(address, instruction)
        self.rsp.set_reg("xpsr", 0x01000000)
        self.rsp.set_reg("sp", 0xe8000)
        self.rsp.set_reg("pc", address)
        assert self.rsp.reg("msp") == 0xe8000
        for name, value in (registers or {}).items():
            self.rsp.set_reg(name, value)
        self.watchpoints = self.rsp.guards(layout(self.profile))

    def snapshot(self):
        return {base: self.rsp.read(base, size)
                for base, size, flags in layout(self.profile)[:5]}

    def close(self):
        if self.process is not None and self.process.poll() is None:
            if self.qmp is not None:
                with contextlib.suppress(Exception):
                    self.qmp.call("quit")
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        if self.rsp is not None:
            self.rsp.close()
        if self.qmp is not None:
            self.qmp.close()
        if self.log is not None:
            self.log.close()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()


def watch_address(stop):
    for key in ("watch", "rwatch", "awatch"):
        marker = key + ":"
        if marker in stop:
            return int(stop.split(marker, 1)[1].split(";", 1)[0], 16)
    return None


def negative(qemu, output, instruction, target, allowed_prefix=0,
             profile=PROFILES[0]):
    with Reference(qemu, output, profile) as r:
        values = {"r0": 0x55667788, "r1": target, "r2": target,
                  "r3": 0xa1b2c3d4}
        # STRD/STM use r0 and r1 as payload and r2 as the base.
        if instruction in (bytes.fromhex("c2e90001"), bytes.fromhex("03c2")):
            values["r1"] = 0xa1b2c3d4
        r.prepare(instruction, registers=values)
        before = r.snapshot()
        initial_registers = r.state()
        bus = r.counts()
        stop = r.rsp.step()
        final_registers = r.state()
        after = r.snapshot()
        assert watch_address(stop) == target + allowed_prefix, stop
        assert final_registers["pc"] == CODE, final_registers
        assert r.counts() == bus, "Unsupported bus callback ran before watchpoint"
        assert r.qmp.get("padding-pristine") is True
        changes = []
        for base in before:
            for i, (old, new) in enumerate(zip(before[base], after[base])):
                if old != new:
                    address = base + i
                    assert target <= address < target + allowed_prefix, hex(address)
                    changes.append({"address": address, "old": old, "new": new})
        if not allowed_prefix:
            assert before == after
        else:
            expected = struct.pack("<II", values["r0"], values["r1"])
            for change in changes:
                assert change["new"] == expected[change["address"] - target]
        # A load must not receive a synthesized value from a forbidden target.
        if instruction == bytes.fromhex("0868"):
            assert final_registers["r0"] == values["r0"]
        return {"stop": stop, "fault_address": watch_address(stop),
                "pc_before": CODE, "pc_after": final_registers["pc"],
                "invalid_backing_padding_unchanged": True,
                "unsupported_callbacks_unchanged": True,
                "earlier_legal_byte_writes": changes,
                "register_changes": {k: [initial_registers[k], v]
                                     for k, v in final_registers.items()
                                     if v != initial_registers[k]},
                "resumed_after_fault": False}


def instruction_size_and_stop(rsp, ranges, pc):
    if not contains(ranges, pc, 2, 1):
        return None, "unsupported-instruction-fetch"
    first = int.from_bytes(rsp.read(pc, 2), "little")
    size = 2 if first >> 11 < 0x1d else 4
    if not contains(ranges, pc, size, 1):
        return None, "instruction-crosses-executable-file-boundary"
    second = int.from_bytes(rsp.read(pc + 2, 2), "little") if size == 4 else None
    stop = (size == 2 and (first in (0xbf20, 0xbf30) or
            first >> 8 in (0xbe, 0xde, 0xdf))) or (
            size == 4 and ((first == 0xf3af and second in (0x8002, 0x8003)) or
            (first & 0xfff0 == 0xf7f0 and second & 0xf000 == 0xa000)))
    return size, "blocking-or-exception-instruction" if stop else None


def run_preflight(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    result = {"schema": "aop-qemu-reference-preflight-v1", "passed": False,
              "all_passed": False, "expected_checks": PREFLIGHT_CHECKS,
              "firmware_read": False, "firmware_executed": False,
              "hardware_reset_verified": False, "aop_startup_validated": False,
              "qemu_sha256": digest(args.qemu), "runner_sha256": digest(__file__),
              "checks": []}
    def case(name, function):
        try:
            detail = function()
            result["checks"].append({"name": name, "passed": True,
                                     "detail": detail})
        except BaseException:
            result["checks"].append({"name": name, "passed": False,
                                     "exception": traceback.format_exc()})
            save(out / "result.json", result)
            raise
        save(out / "result.json", result)
    def path(n):
        return out / ("%02d" % n)
    try:
        def core():
            with Reference(args.qemu, path(0)) as r:
                initial = r.initial
                r.prepare(bytes.fromhex("eff30880"))
                stop = r.rsp.step()
                assert watch_address(stop) is None
                assert r.rsp.reg("r0") == r.rsp.reg("msp") == 0xe8000
                assert r.rsp.reg("pc") == CODE + 4
                assert r.counts() == r.start_bus_counts
                return {"cpu_type": "cortex-m3-arm-cpu",
                        "reference_initial_state": initial,
                        "executed_public_probe": "MRS r0, MSP",
                        "hardware_cpu_identity_established": False}
        case("cortex-m3-and-architectural-msp", core)
        def legal():
            with Reference(args.qemu, path(1)) as r:
                target = PHYSICAL[2][0] + 0x100
                r.prepare(bytes.fromhex("08600868"), registers={
                    "r0": 0x12345678, "r1": target})
                r.rsp.step()
                assert r.rsp.read(target, 4) == struct.pack("<I", 0x12345678)
                r.rsp.set_reg("r0", 0)
                r.rsp.step()
                assert r.rsp.reg("r0") == 0x12345678
                assert r.rsp.reg("pc") == CODE + 4
                return {"store_and_load_executed": True}
        case("permitted-store-and-load", legal)
        case("unmapped-read-stops-before-value", lambda:
             negative(args.qemu, path(2), bytes.fromhex("0868"), 0x40000000))
        case("unmapped-write-stops-before-dispatch", lambda:
             negative(args.qemu, path(3), bytes.fromhex("0860"), 0x40000000))
        def ppb():
            return {"read": negative(args.qemu, path(4), bytes.fromhex("0868"),
                                     0xe000ed00),
                    "write": negative(args.qemu, path(5), bytes.fromhex("0860"),
                                      0xe000e010)}
        case("ppb-read-and-write-no-implicit-success", ppb)
        end = PHYSICAL[1][0] + PHYSICAL[1][1]
        case("same-page-tail-read", lambda:
             negative(args.qemu, path(6), bytes.fromhex("0868"), end))
        case("same-page-tail-write", lambda:
             negative(args.qemu, path(7), bytes.fromhex("0860"), end))
        case("unaligned-str-crosses-exact-boundary", lambda:
             negative(args.qemu, path(8), bytes.fromhex("0860"), end - 2, 2))
        def multi(instruction, valid_path, invalid_path, writeback):
            target = PHYSICAL[2][0] + 0x100
            with Reference(args.qemu, path(valid_path)) as r:
                r.prepare(instruction, registers={"r0": 0x55667788,
                          "r1": 0xa1b2c3d4, "r2": target})
                stop = r.rsp.step()
                assert watch_address(stop) is None
                assert r.rsp.read(target, 8) == struct.pack("<II", 0x55667788,
                                                          0xa1b2c3d4)
                assert r.rsp.reg("pc") == CODE + len(instruction)
                assert r.rsp.reg("r2") == target + (8 if writeback else 0)
                valid = {"actual_two_word_store": True,
                         "base_writeback": writeback}
            invalid = negative(args.qemu, path(invalid_path), instruction,
                               end - 4, 4)
            return {"permitted_control": valid, "boundary": invalid}
        case("strd-crosses-exact-boundary", lambda:
             multi(bytes.fromhex("c2e90001"), 15, 9, False))
        case("stm-records-earlier-permitted-beats", lambda:
             multi(bytes.fromhex("03c2"), 16, 10, True))
        case("read-only-code-write", lambda:
             negative(args.qemu, path(11), bytes.fromhex("0860"),
                      PHYSICAL[0][0] + 0x200))
        def aliases():
            with Reference(args.qemu, path(12), PROFILES[1]) as r:
                alias = 0xe0100
                physical = PHYSICAL[1][0] + 0x100
                r.prepare(bytes.fromhex("08600860"), address=0x100, registers={
                    "r0": 0x11223344, "r1": alias})
                r.rsp.step()
                assert r.rsp.read(physical, 4) == struct.pack("<I", 0x11223344)
                r.rsp.set_reg("r0", 0x55667788)
                r.rsp.set_reg("r1", physical)
                r.rsp.step()
                assert r.rsp.read(alias, 4) == struct.pack("<I", 0x55667788)
                assert r.rsp.read(CODE, 4) == bytes.fromhex("08600860")
                assert r.qmp.get("padding-pristine") is True
                return {"bidirectional_guest_writes_share_backing": True,
                        "code_executed_from_alias": True}
        case("real-shared-code-and-data-aliases", aliases)
        def fetch():
            with Reference(args.qemu, path(13)) as r:
                end_code = PHYSICAL[0][0] + PHYSICAL[0][1]
                r.rsp.write(end_code - 2, bytes.fromhex("00f0"))
                width, reason = instruction_size_and_stop(
                    r.rsp, layout(PROFILES[0]), end_code - 2)
                assert width is None
                assert reason == "instruction-crosses-executable-file-boundary"
                width, reason = instruction_size_and_stop(
                    r.rsp, layout(PROFILES[0]), PHYSICAL[1][0])
                assert width is None and reason == "unsupported-instruction-fetch"
                return {"host_fetch_guard_stopped_before_s_packet": True,
                        "guest_instruction_executed": False}
        case("t32-tail-and-nonexecutable-fetch-guards", fetch)
        def extension():
            with Reference(args.qemu, path(14), PROFILES[2]) as r:
                target = 0xe7ffc
                r.prepare(bytes.fromhex("0860"), registers={
                    "r0": 0xabcdef01, "r1": target})
                stop = r.rsp.step()
                assert watch_address(stop) is None
                assert r.rsp.read(PHYSICAL[1][0] + 0x7ffc, 4) == struct.pack(
                    "<I", 0xabcdef01)
                assert r.qmp.get("padding-pristine") is True
                return {"explicit_additional_dtcm_bytes": 0x8000 - 0x604c,
                        "extra_ram_measured_on_hardware": False}
        case("extra-dtcm-only-in-explicit-reference-profile", extension)
        assert len(result["checks"]) == PREFLIGHT_CHECKS
        result["passed"] = result["all_passed"] = True
    except BaseException:
        result["failure"] = traceback.format_exc()
    save(out / "result.json", result)
    return 0 if result["passed"] else 1


def parse_firmware(data):
    assert hashlib.sha256(data).hexdigest() == EXPECTED_IMAGE
    head = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    assert head[0][:7] == b"\x7fELF\x01\x01\x01"
    assert head[2] == 40 and head[4] == 0x0b000009 and head[9] == 32
    segments = []
    for index in range(head[10]):
        at = head[5] + index * head[9]
        assert at + 32 <= len(data)
        typ, offset, va, pa, filesz, memsz, flags, alignment = struct.unpack_from(
            "<8I", data, at)
        if typ != 1:
            continue
        assert offset + filesz <= len(data) and filesz == memsz
        assert va == pa and alignment == 4
        segments.append({"index": index, "offset": offset, "paddr": pa,
                         "filesz": filesz, "memsz": memsz, "flags": flags})
    assert [(s["paddr"], s["memsz"], s["flags"]) for s in segments] == list(PHYSICAL)
    stack, vector = struct.unpack_from("<II", data, segments[0]["offset"])
    assert stack == 0xe8000 and vector == 9
    return segments, stack, vector, head[4]


def diagnostic_worker(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    proof = json.loads(Path(args.preflight).read_text())
    assert proof["passed"] is True and proof["all_passed"] is True
    assert proof["expected_checks"] == PREFLIGHT_CHECKS
    assert len(proof["checks"]) == PREFLIGHT_CHECKS
    assert all(x["passed"] is True for x in proof["checks"])
    assert proof["qemu_sha256"] == digest(args.qemu)
    assert proof["runner_sha256"] == digest(__file__)
    assert os.getsid(0) == os.getpid(), "_worker must be started by diagnose-private"
    data = Path(args.firmware).read_bytes()
    segments, stack, vector, entry = parse_firmware(data)
    record = {"schema": "aop-qemu-entry-reference-diagnostic-v1",
              "qemu_sha256": digest(args.qemu), "runner_sha256": digest(__file__),
              "input_sha256": hashlib.sha256(data).hexdigest(),
              "preflight_sha256": digest(args.preflight),
              "profile": args.profile, "reference_cpu": "cortex-m3",
              "actual_aop_cpu": "unknown", "full_firmware_start": False,
              "hardware_reset_verified": False, "aop_startup_validated": False,
              "firmware_read": True, "completed_single_steps": 0,
              "steps_attempted": 0, "firmware_execution_requested": False, "maximum_steps": STEP_LIMIT,
              "raw_instruction_bytes_exported": False,
              "host_hooks_replacing_firmware_functions": False,
              "successful_peripheral_responses": 0}
    try:
        with Reference(args.qemu, out / "machine", args.profile) as r:
            record["qemu_reference_initial_state_before_loading"] = r.initial
            for segment in segments:
                start = segment["offset"]
                r.rsp.write(segment["paddr"], data[start:start + segment["filesz"]])
            # Only core GDB registers are writable in this QEMU revision.
            # Read-only M-system registers retain the recorded reference reset.
            for i in range(13):
                r.rsp.set_reg("r%d" % i, 0)
            r.rsp.set_reg("lr", 0)
            r.rsp.set_reg("xpsr", 0x01000000)
            r.rsp.set_reg("sp", stack)
            r.rsp.set_reg("pc", (entry if args.profile == PROFILES[0] else vector)
                           & ~1)
            assert r.rsp.reg("msp") == stack
            record["unmeasured_zero_seeds"] = ["r%d" % i for i in range(13)] + ["lr"]
            record["seeded_reference_state"] = r.state()
            record["candidate_stack_is_in_writable_range"] = contains(
                layout(args.profile), stack - 4, 4, 2)
            record["watchpoints"] = r.rsp.guards(layout(args.profile))
            before_bus = r.counts()
            save(out / "progress.json", record)
            recent = []
            for index in range(STEP_LIMIT):
                pc = r.rsp.reg("pc")
                width, stop_reason = instruction_size_and_stop(
                    r.rsp, layout(args.profile), pc)
                if stop_reason:
                    record["stop_reason"] = stop_reason
                    record["stopped_pc"] = pc
                    break
                recent.append({"pc": pc, "instruction_size": width})
                recent = recent[-16:]
                record["steps_attempted"] += 1
                record["firmware_execution_requested"] = True
                stop = r.rsp.step()
                address = watch_address(stop)
                if address is not None:
                    record["stop_reason"] = "unsupported-access-before-bus"
                    record["fault_address"] = address
                    record["stopped_pc"] = r.rsp.reg("pc")
                    record["debug_stop"] = stop
                    break
                record["completed_single_steps"] += 1
                if (index + 1) % 32 == 0:
                    record["recent_instruction_locations"] = recent
                    save(out / "progress.json", record)
                if r.rsp.reg("xpsr") & 0x1ff:
                    record["stop_reason"] = "architectural-exception-after-step"
                    record["exception_number"] = r.rsp.reg("xpsr") & 0x1ff
                    record["stopped_pc"] = r.rsp.reg("pc")
                    break
            else:
                record["stop_reason"] = "instruction-budget"
            record["recent_instruction_locations"] = recent
            record["final_state"] = r.state()
            record["unsupported_bus_counts_before"] = before_bus
            record["unsupported_bus_counts_after"] = r.counts()
            record["invalid_backing_padding_unchanged"] = r.qmp.get(
                "padding-pristine")
            assert record["unsupported_bus_counts_after"] == before_bus
            assert record["invalid_backing_padding_unchanged"] is True
            record["diagnostic_completed"] = True
            record["terminated_by"] = "host-qmp-quit"
    except BaseException:
        record["diagnostic_completed"] = False
        record["exception"] = traceback.format_exc()
    save(out / "result.json", record)
    return 0 if record.get("diagnostic_completed") else 1


def run_private(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    summary = {"aop_startup_validated": False, "hardware_reset_verified": False,
               "full_firmware_start": False, "profiles": []}
    for index, profile in enumerate(PROFILES):
        target = out / ("%02d" % index)
        command = [sys.executable, str(Path(__file__).resolve()), "_worker",
                   "--qemu", str(Path(args.qemu).resolve()), "--out", str(target),
                   "--firmware", str(Path(args.firmware).resolve()),
                   "--preflight", str(Path(args.preflight).resolve()),
                   "--profile", profile]
        with (out / ("%02d.log" % index)).open("wb") as log:
            started = time.monotonic()
            worker = subprocess.Popen(command, stdout=log,
                                      stderr=subprocess.STDOUT,
                                      start_new_session=True)
            try:
                rc = worker.wait(timeout=HARD_SECONDS)
                status = {"profile": profile, "exit_code": rc}
            except subprocess.TimeoutExpired:
                # Kill the whole private worker/QEMU process group.  Killing
                # only the Python parent could leave a disconnected QEMU alive.
                os.killpg(worker.pid, signal.SIGKILL)
                worker.wait(timeout=2)
                status = {"profile": profile, "hard_timeout_seconds": HARD_SECONDS,
                          "diagnostic_completed": False,
                          "entire_worker_process_group_killed": True}
            status["wall_seconds"] = time.monotonic() - started
        summary["profiles"].append(status)
        save(out / "summary.json", summary)
    return 0 if all(x.get("exit_code") == 0 for x in summary["profiles"]) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)
    for name in ("preflight", "diagnose-private", "_worker"):
        sub = commands.add_parser(name)
        sub.add_argument("--qemu", required=True, type=Path)
        sub.add_argument("--out", required=True, type=Path)
        if name != "preflight":
            sub.add_argument("--firmware", required=True, type=Path)
            sub.add_argument("--preflight", required=True, type=Path)
        if name == "_worker":
            sub.add_argument("--profile", choices=PROFILES, required=True)
    args = parser.parse_args()
    if args.operation == "preflight":
        return run_preflight(args)
    if args.operation == "_worker":
        return diagnostic_worker(args)
    return run_private(args)


if __name__ == "__main__":
    sys.exit(main())
