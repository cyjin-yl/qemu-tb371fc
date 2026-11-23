#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Actual apps SMMUv500 reference transactions with the existing S1 walker.

Sources: OnePlusOSS/android_kernel_oneplus_sm8250 at
94304e12a3509eac7b3716bf574ce0b1fcaacbae, drivers/iommu/arm-smmu.c, and the
captured TB371FC DT. The non-v1 revision and one-microsecond operations are
explicit digital reference choices, not measurements of the tablet.
"""
import argparse
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import traceback

from test_geni_uart import Rig, connect_unix
from test_geni_migration import wait_migration
from test_gpucc_sm8250 import digest, file_channels

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use Python -O.")

TYPE = "qcom-smmuv500"
PATH = "/machine/apps-smmu-v500"
APPS = 0x15000000
TCU = 0x15182000
VERSION = TCU + 0x18
TBUS = tuple(0x15185000 + index * 0x4000 for index in range(10))
SEMAPHORES = tuple(TCU + 0x200 + index * 8 for index in range(10))
GPU_TCU = 0x03dc2000
GPU_TBUS = (0x03dc5000, 0x03dc9000)
DMA = 0x50000000
TABLE = 0x82000000
PA = 0x81000000
IOVA = 0x1000
RAM_WITNESS = 0x80010000
CTRL, SCTRL = 0x200436, 0x1067
PTE_RW, PTE_RO = 0x443, 0x4c3
FSR_MASK = 0x800001fe
HALT = 1 << 16
HALT_ACK = 2
RUNNING = 1
AXUSER = 255 << 36
READ_TRIGGER = 0xfd
WRITE_TRIGGER = 0xff
REFERENCE_VERSION = 0x02000000
DELAY_NS = 1000
CACHED_DMA = 0x51000000
SOURCE_COMMIT = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_SHA256 = "ccc4b09010c420ea42c9838d397b0b81fe6a0a4e345e95cb935c2e70308fbf49"
DTB_SHA256 = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
DT_EVIDENCE_BLOB = "8306d244d2f785ce0efb8c2071b8badc77b252e7"


def cb(index):
    return APPS + 0x80000 + index * 0x1000


def sid(index, low=0x42):
    assert 0 <= index < 10 and 0 <= low < 0x400
    return index * 0x400 + low


def table(index):
    return TABLE + index * 0x4000


def pa(index):
    return PA + index * 0x2000


def base_command(args, reference=True, accel="qtest"):
    command = [args.qemu, "-machine", args.machine, "-accel", accel,
               "-m", "128M", "-display", "none", "-monitor", "none",
               "-serial", "null", "-S", "-d", "unimp,guest_errors,invalid_mem"]
    if reference:
        command += ["-global", TYPE + ".enabled-reference=on",
                    "-global", TYPE + f".reference-hw-version={REFERENCE_VERSION:#x}"]
    return command


class V500Rig(Rig):
    def __init__(self, args, directory, *, incoming=False, reference=True, accel="qtest"):
        self.args = args
        self.reference = reference
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=False)
        self.log_path = self.directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        self.qtest_transcript = (self.directory / "qtest.log").open("w", buffering=1)
        self.qmp_transcript = (self.directory / "qmp.log").open("w", buffering=1)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="qv500-"))
        self.last_clock = 0
        self.connections = []
        self.process = None
        self.events = []
        self.irq = {}
        command = base_command(args, reference, accel) + [
            "-qmp", f"unix:{self.socket_dir}/m,server=on,wait=off",
            "-qtest", f"unix:{self.socket_dir}/t,server=on,wait=off",
            "-qtest-log", "/dev/null"]
        if incoming:
            command += ["-incoming", "defer"]
        (self.directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        try:
            self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.DEVNULL, stderr=self.log)
            self.qmp = connect_unix(self.socket_dir / "m", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            greeting = json.loads(self.qmpfile.readline())
            assert "QMP" in greeting, greeting
            self.monitor("qmp_capabilities")
            self.qtest = connect_unix(self.socket_dir / "t", self.process)
            self.connections.append(self.qtest)
            self.qfile = self.qtest.makefile("rwb", buffering=0)
            self.command("irq_intercept_out /machine/apps-smmu sysbus-irq")
            if not incoming and accel == "qtest":
                self.monitor("cont")
        except BaseException:
            self.close()
            raise

    def command(self, value):
        self.qtest_transcript.write("> " + value + "\n")
        result = super().command(value)
        self.qtest_transcript.write("< OK " + " ".join(result) + "\n")
        return result

    def monitor(self, execute, arguments=None):
        self.qmp_transcript.write("> " + json.dumps([execute, arguments]) + "\n")
        try:
            result = super().monitor(execute, arguments)
        except BaseException as error:
            self.qmp_transcript.write("< ERROR " + str(error) + "\n")
            raise
        self.qmp_transcript.write("< " + json.dumps(result) + "\n")
        return result

    def read(self, address, width="l"):
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def write(self, address, value, width="l"):
        self.command(f"write{width} {address:#x} {value:#x}")

    def get(self, name, path=PATH):
        return self.monitor("qom-get", {"path": path, "property": name})

    def tget(self, index, name):
        return self.get(f"tbu{index}-{name}")

    def tread(self, index, offset, width="l"):
        return self.read(TBUS[index] + offset, width)

    def twrite(self, index, offset, value, width="l"):
        self.write(TBUS[index] + offset, value, width)

    def step(self, ns):
        before = self.last_clock
        self.last_clock = int(self.command(f"clock_step {ns}")[0], 0)
        assert self.last_clock == before + ns, (before, ns, self.last_clock)
        return self.last_clock

    def set_clock(self, ns):
        self.last_clock = int(self.command(f"clock_set {ns}")[0], 0)
        assert self.last_clock == ns
        return self.last_clock

    def wire(self, number, expected):
        self.read(RAM_WITNESS)  # Drain QTest IRQs without companion MMIO.
        assert self.irq.get(number, False) is expected, (number, self.irq, expected)

    def close(self):
        super().close()
        for name in ("qtest_transcript", "qmp_transcript"):
            stream = getattr(self, name, None)
            if stream and not stream.closed:
                stream.close()


def configure_s1(rig, index=0, *, leaf=PTE_RW, output=None, low_sid=0x42):
    """Build real three-level RAM tables; do not supply an expected PAR."""
    base = table(index)
    output = pa(index) if output is None else output
    rig.write(base, (base + 0x1000) | 3, "q")
    rig.write(base + 0x1000, (base + 0x2000) | 3, "q")
    rig.write(base + 0x2008, output | leaf, "q")
    # CBAR.S1_TRANS_S2_BYPASS and CBA2R.VA64, matching the existing
    # architectural acceptance fixture rather than a vendor success hook.
    rig.write(APPS + 0x1000 + index * 4, 1 << 16)
    rig.write(APPS + 0x1800 + index * 4, 1)
    rig.write(cb(index) + 0x10, 5 | (7 << 15))
    rig.write(cb(index) + 0x30, 25 | (1 << 23))
    rig.write(cb(index) + 0x20, base | (0x42 << 48), "q")
    rig.write(cb(index) + 0x38, 0xff)
    rig.write(cb(index), SCTRL)
    rig.write(APPS + 0x800 + index * 4, 0x80000000 | sid(index, low_sid))
    rig.write(APPS + 0xc00 + index * 4, index)
    rig.write(APPS, CTRL)


def clear_faults(rig, index=0):
    rig.write(APPS + 0x48, 0xffffffff)
    rig.write(cb(index) + 0x58, 0xffffffff)


def fsr(rig, index=0):
    return rig.read(cb(index) + 0x58) & FSR_MASK


def core_diagnostics(text):
    matches = re.findall(r"Invalid (read|write) at addr 0x([0-9a-fA-F]+), "
                         r"size ([0-9]+), region '([^']+)', reason: ([^\n]+)", text)
    return [(kind, int(address, 16), int(size), region, reason)
            for kind, address, size, region, reason in matches]


def device_diagnostics(text):
    return [line for line in text.splitlines() if TYPE + ":" in line]


STATE_FIELDS = ("halt-sid", "va", "trigger", "axuser", "par", "halt-pending",
                "halted", "ecats-pending", "halt-deadline-ns",
                "ecats-deadline-ns", "status", "captured-sid", "captured-va",
                "captured-write")


def snapshot(rig):
    return {"owner": rig.get("ecats-owner"),
            "dma_blocked_mask": rig.get("dma-blocked-mask"),
            "tbus": [{field: rig.tget(index, field) for field in STATE_FIELDS}
                     for index in range(10)]}


def halt_tbu(rig, index=0, *, wait=True, low_sid=0x42):
    rig.twrite(index, 0, HALT | low_sid)
    assert rig.tget(index, "halt-pending") is True
    assert rig.tget(index, "halted") is False
    assert rig.tget(index, "halt-deadline-ns") == rig.last_clock + DELAY_NS
    assert rig.get("dma-blocked-mask") & (1 << index)
    if wait:
        rig.step(DELAY_NS)
        assert rig.tget(index, "halt-pending") is False
        assert rig.tget(index, "halted") is True
        assert rig.tget(index, "status") & HALT_ACK


def acquire(rig, index=0):
    assert rig.read(SEMAPHORES[index]) == 1
    assert rig.get("ecats-owner") == index


def request(rig, index=0, *, va=IOVA, low_sid=0x42, write=False, wait=True):
    old = rig.tget(index, "halt-sid")
    rig.twrite(index, 0, (old & ~0x3ff) | low_sid, "q")
    rig.twrite(index, 8, va, "q")
    rig.twrite(index, 0x30, AXUSER, "q")
    rig.twrite(index, 0x18, WRITE_TRIGGER if write else READ_TRIGGER, "q")
    assert rig.tget(index, "ecats-pending") is True
    assert rig.tget(index, "ecats-deadline-ns") == rig.last_clock + DELAY_NS
    assert rig.tget(index, "status") & RUNNING
    if wait:
        rig.step(DELAY_NS)
        assert rig.tget(index, "ecats-pending") is False
        assert not rig.tget(index, "status") & RUNNING


def successful_par(rig, index, expected):
    # Qualcomm ECATS PAR is a different wire format from architectural CB
    # PAR: the vendor driver shifts the entire result down by twelve.
    value = rig.tget(index, "par")
    assert value & 1 == 0, (index, value)
    assert value == expected << 12, (index, value, expected << 12)
    assert (value >> 12) & ((1 << 36) - 1) == expected
    assert rig.tread(index, 0x28, "q") == value


def release_and_resume(rig, index=0):
    rig.twrite(index, 0x18, 0, "q")
    rig.twrite(index, 8, 0, "q")
    rig.twrite(index, 0, HALT)
    rig.write(SEMAPHORES[index], 0)
    assert rig.get("ecats-owner") == -1
    rig.twrite(index, 0, 0)
    assert not rig.get("dma-blocked-mask") & (1 << index)
    assert rig.tget(index, "halted") is False


def cold_state_exposes_explicit_reference_identity_without_success(rig):
    assert rig.get("enabled-reference") is True
    assert rig.get("reference-hw-version") == REFERENCE_VERSION
    assert rig.read(VERSION) == REFERENCE_VERSION
    assert REFERENCE_VERSION != 0x01000000  # Do not bypass the ECATS semaphore.
    assert rig.get("tcu-base") == TCU
    assert rig.get("tbu-count") == 10
    assert rig.get("ecats-owner") == -1
    assert rig.get("dma-blocked-mask") == 0
    for index in range(10):
        for field in ("halt-sid", "va", "trigger", "axuser", "status"):
            assert rig.tget(index, field) == 0, (index, field)
        assert rig.tget(index, "par") & 1
        for field in ("halt-pending", "halted", "ecats-pending"):
            assert rig.tget(index, field) is False, (index, field)


def halt_blocks_real_dma_at_request_and_ack_has_an_exact_deadline(rig):
    configure_s1(rig)
    rig.write(PA, 0x12345678)
    assert rig.read(DMA + IOVA) == 0x12345678
    rig.write(DMA + IOVA, 0x87654321)
    assert rig.read(PA) == 0x87654321
    halt_tbu(rig, wait=False)
    # A halted client must not even run the walk and create a fault.
    rig.write(table(0) + 0x2008, 0, "q")
    rig.write(DMA + IOVA, 0xbad)
    assert rig.read(PA) == 0x87654321
    assert fsr(rig) == 0 and rig.read(APPS + 0x48) == 0
    rig.write(table(0) + 0x2008, PA | PTE_RW, "q")
    rig.wire(0, False)
    rig.wire(2, False)
    rig.step(999)
    assert rig.tget(0, "halt-pending") is True
    assert rig.tget(0, "halted") is False
    assert not rig.tget(0, "status") & HALT_ACK
    rig.step(1)
    assert rig.tget(0, "halted") is True
    assert rig.tget(0, "halt-pending") is False
    assert rig.tget(0, "status") & HALT_ACK
    rig.write(DMA + IOVA, 0xdeadbeef)
    assert rig.read(PA) == 0x87654321
    rig.twrite(0, 0, 0)
    assert rig.get("dma-blocked-mask") == 0
    rig.write(DMA + IOVA, 0x76543210)
    assert rig.read(PA) == 0x76543210
    assert fsr(rig) == 0


def all_tbu_halt_ranges_are_independent_and_do_not_block_another_sid(rig):
    configure_s1(rig)
    for index in range(1, 10):
        halt_tbu(rig, index)
        assert rig.get("dma-blocked-mask") == sum(1 << item for item in range(1, index + 1))
        rig.write(DMA + IOVA, 0x60000000 + index)
        assert rig.read(PA) == 0x60000000 + index
    halt_tbu(rig, 0)
    assert rig.get("dma-blocked-mask") == 0x3ff
    rig.write(DMA + IOVA, 0xbad)
    assert rig.read(PA) == 0x60000009
    for index in range(9, 0, -1):
        rig.twrite(index, 0, 0)
        assert rig.tget(index, "halted") is False
    assert rig.get("dma-blocked-mask") == 1
    rig.twrite(0, 0, 0)
    rig.write(DMA + IOVA, 0xabcddcba)
    assert rig.read(PA) == 0xabcddcba


def cancelling_halt_prevents_late_ack_and_releases_dma_immediately(rig):
    configure_s1(rig)
    rig.write(PA, 0x11223344)
    halt_tbu(rig, wait=False)
    rig.step(400)
    rig.twrite(0, 0, 0x42)
    assert rig.tget(0, "halt-pending") is False
    assert rig.tget(0, "halted") is False
    assert rig.get("dma-blocked-mask") == 0
    rig.write(DMA + IOVA, 0xaabbccdd)
    assert rig.read(PA) == 0xaabbccdd
    rig.step(5000)
    assert rig.tget(0, "halted") is False
    assert not rig.tget(0, "status") & HALT_ACK
    halt_tbu(rig)
    assert rig.tget(0, "halted") is True


def read_to_acquire_is_a_shared_mutex_and_qom_observation_does_not_acquire(rig):
    for _ in range(3):
        before = snapshot(rig)
        assert before["owner"] == -1
        for index in range(10):
            assert rig.tget(index, "status") == 0
        assert snapshot(rig) == before
    for owner in (0, 9, 4):
        acquire(rig, owner)
        for index in range(10):
            # Even the current owner reads busy after its first acquisition.
            assert rig.read(SEMAPHORES[index]) == 0
            assert rig.get("ecats-owner") == owner
        for _ in range(3):
            snapshot(rig)
        assert rig.get("ecats-owner") == owner
        rig.write(SEMAPHORES[owner], 0)
        assert rig.get("ecats-owner") == -1


def real_external_cache_hits_are_invalidated_by_the_halt_notifier(rig):
    configure_s1(rig)
    rig.write(PA, 0x1234abcd)
    assert rig.get("qtest-cache-valid") is False
    fills = rig.get("qtest-cache-fills")
    assert rig.read(CACHED_DMA + IOVA) == 0x1234abcd
    assert rig.get("qtest-cache-valid") is True
    assert rig.get("qtest-cache-fills") > fills
    hits = rig.get("qtest-cache-hits")
    assert rig.read(CACHED_DMA + IOVA) == 0x1234abcd
    assert rig.get("qtest-cache-hits") > hits
    unmaps = rig.get("qtest-cache-unmaps")
    halt_tbu(rig, wait=False)
    assert rig.get("qtest-cache-valid") is False
    assert rig.get("qtest-cache-unmaps") > unmaps
    rig.write(CACHED_DMA + IOVA, 0xbad)
    assert rig.read(PA) == 0x1234abcd
    assert rig.get("qtest-cache-valid") is False
    rig.step(DELAY_NS)
    rig.write(CACHED_DMA + IOVA, 0xdeadbeef)
    assert rig.read(PA) == 0x1234abcd
    rig.twrite(0, 0, 0)
    assert rig.read(CACHED_DMA + IOVA) == 0x1234abcd
    assert rig.get("qtest-cache-valid") is True
    rig.write(CACHED_DMA + IOVA, 0xaabbccdd)
    assert rig.read(PA) == 0xaabbccdd


def literal_ecats_sequence_translates_real_tables_while_ordinary_dma_is_halted(rig):
    configure_s1(rig)
    rig.write(PA, 0x13572468)
    halt_tbu(rig)
    acquire(rig)
    request(rig, va=IOVA + 3, wait=False)
    rig.write(DMA + IOVA, 0xbad)
    assert rig.read(PA) == 0x13572468
    rig.step(999)
    assert rig.tget(0, "ecats-pending") is True
    assert rig.tget(0, "status") & RUNNING
    rig.step(1)
    assert rig.tget(0, "ecats-pending") is False
    assert not rig.tget(0, "status") & RUNNING
    successful_par(rig, 0, PA + 3)
    assert (PA + 3) != IOVA + 3
    assert fsr(rig) == 0
    rig.wire(2, False)
    release_and_resume(rig)
    rig.write(DMA + IOVA, 0x24681357)
    assert rig.read(PA) == 0x24681357


def every_tbu_restores_the_full_sid_and_selects_its_own_context(rig):
    for index in range(10):
        configure_s1(rig, index)
        rig.write(pa(index), 0x70000000 + index)
    for index in range(10):
        halt_tbu(rig, index)
        acquire(rig, index)
        request(rig, index, va=IOVA + index)
        successful_par(rig, index, pa(index) + index)
        assert fsr(rig, index) == 0
        release_and_resume(rig, index)
    assert rig.get("dma-blocked-mask") == 0
    assert rig.get("ecats-owner") == -1


def ecats_write_permissions_fault_in_the_real_context_and_assert_its_irq(rig):
    configure_s1(rig, leaf=PTE_RO)
    rig.write(PA, 0x12345678)
    halt_tbu(rig)
    acquire(rig)
    request(rig)
    successful_par(rig, 0, PA)
    rig.twrite(0, 0x18, 0, "q")
    request(rig, write=True)
    assert rig.tget(0, "par") & 1
    assert fsr(rig) == 8
    assert rig.read(cb(0) + 0x60, "q") == IOVA
    assert rig.read(PA) == 0x12345678
    rig.wire(2, True)
    clear_faults(rig)
    rig.wire(2, False)
    release_and_resume(rig)


def missing_leaf_faults_and_a_repaired_table_is_walked_again(rig):
    configure_s1(rig, leaf=0, output=0)
    halt_tbu(rig)
    acquire(rig)
    request(rig)
    assert rig.tget(0, "par") & 1
    assert fsr(rig) == 2
    rig.wire(2, True)
    rig.write(table(0) + 0x2008, pa(0) | PTE_RW, "q")
    clear_faults(rig)
    rig.twrite(0, 0x18, 0, "q")
    request(rig)
    successful_par(rig, 0, pa(0))
    assert fsr(rig) == 0
    rig.wire(2, False)
    release_and_resume(rig)


def stream_selection_errors_do_not_become_identity_translations(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    # No stream matches SID 0x43, while the configured 0x42 mapping is valid.
    request(rig, low_sid=0x43)
    assert rig.tget(0, "par") & 1
    assert rig.read(APPS + 0x48) & 2
    rig.wire(0, True)
    clear_faults(rig)
    rig.twrite(0, 0x18, 0, "q")
    # Two actual SMRs now match 0x42; the existing pipeline must report SMCF.
    rig.write(APPS + 0x804, 0x80000042)
    rig.write(APPS + 0xc04, 0)
    request(rig)
    assert rig.tget(0, "par") & 1
    assert rig.read(APPS + 0x48) & 4
    rig.wire(0, True)
    clear_faults(rig)
    rig.write(APPS + 0x804, 0)
    rig.twrite(0, 0x18, 0, "q")
    request(rig)
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def unsupported_stages_granules_and_aarch32_remain_real_configuration_faults(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    cases = [(APPS + 0x1000, kind << 16) for kind in (0, 2, 3)]
    cases += [(APPS + 0x1800, 0)]
    cases += [(cb(0) + 0x30, 25 | (1 << 23) | (tg << 14)) for tg in (1, 2, 3)]
    for address, value in cases:
        rig.write(APPS + 0x1000, 1 << 16)
        rig.write(APPS + 0x1800, 1)
        rig.write(cb(0) + 0x30, 25 | (1 << 23))
        rig.write(address, value)
        request(rig)
        assert rig.tget(0, "par") & 1
        assert rig.read(APPS + 0x48) & 1
        rig.wire(0, True)
        clear_faults(rig)
        rig.twrite(0, 0x18, 0, "q")
    rig.write(APPS + 0x1000, 1 << 16)
    rig.write(APPS + 0x1800, 1)
    rig.write(cb(0) + 0x30, 25 | (1 << 23))
    request(rig)
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def pending_request_captures_sid_va_and_attributes_without_rewriting_staging(rig):
    configure_s1(rig, 0)
    configure_s1(rig, 1)
    # A second low SID in TBU0 reaches CB1, so using the changed staging SID
    # would select a different real mapping instead of the captured context.
    rig.write(APPS + 0x804, 0x80000043)
    halt_tbu(rig)
    acquire(rig)
    request(rig, va=IOVA + 3, wait=False)
    deadline = rig.tget(0, "ecats-deadline-ns")
    rig.step(400)
    rig.twrite(0, 0, HALT | 0x43, "q")
    rig.twrite(0, 8, 0x9000, "q")
    rig.twrite(0, 0x30, 0, "q")
    assert rig.tget(0, "ecats-deadline-ns") == deadline
    rig.step(599)
    assert rig.tget(0, "ecats-pending") is True
    rig.step(1)
    assert rig.tget(0, "ecats-pending") is False
    successful_par(rig, 0, pa(0) + 3)
    assert rig.tget(0, "halt-sid") == HALT | 0x43
    assert rig.tget(0, "va") == 0x9000
    assert rig.tget(0, "axuser") == 0
    rig.twrite(0, 0x18, 0, "q")
    request(rig, low_sid=0x43, va=IOVA + 4)
    successful_par(rig, 0, pa(1) + 4)
    release_and_resume(rig)


def expect_rejected_write(rig, address, value, *, kind, index=0,
                          offset=0, width="l", reason):
    before = snapshot(rig)
    old_log = rig.log_path.read_text(errors="replace")
    rig.write(address, value, width)
    assert snapshot(rig) == before, (kind, index, reason)
    fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
    size = 8 if width == "q" else 4
    expected = f"{TYPE}: write {kind} tbu {index} +0x{offset:x} size {size}: {reason}"
    assert device_diagnostics(fresh) == [expected], (expected, fresh)
    assert not core_diagnostics(fresh), fresh


def semaphore_release_requires_the_owner_and_no_pending_request(rig):
    for index in (0, 9):
        expect_rejected_write(rig, SEMAPHORES[index], 0, kind="semaphore",
                              index=index, reason="invalid semaphore release")
    acquire(rig, 0)
    expect_rejected_write(rig, SEMAPHORES[1], 0, kind="semaphore", index=1,
                          reason="invalid semaphore release")
    expect_rejected_write(rig, SEMAPHORES[0], 1, kind="semaphore",
                          reason="invalid semaphore release")
    configure_s1(rig)
    halt_tbu(rig)
    request(rig, wait=False)
    rig.step(400)
    expect_rejected_write(rig, SEMAPHORES[0], 0, kind="semaphore",
                          reason="invalid semaphore release")
    assert rig.get("ecats-owner") == 0
    rig.step(600)
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def invalid_request_state_and_attributes_do_not_start_or_claim_success(rig):
    configure_s1(rig)
    rig.twrite(0, 8, IOVA, "q")
    rig.twrite(0, 0x30, AXUSER, "q")
    expect_rejected_write(rig, TBUS[0] + 0x18, READ_TRIGGER, kind="trigger", width="q",
                          reason="unsupported ECATS request or state")
    acquire(rig)
    expect_rejected_write(rig, TBUS[0] + 0x18, READ_TRIGGER, kind="trigger", width="q",
                          reason="unsupported ECATS request or state")
    halt_tbu(rig, wait=False)
    rig.step(999)
    expect_rejected_write(rig, TBUS[0] + 0x18, READ_TRIGGER, kind="trigger", width="q",
                          reason="unsupported ECATS request or state")
    rig.step(1)
    rig.twrite(0, 0x30, 0, "q")
    expect_rejected_write(rig, TBUS[0] + 0x18, READ_TRIGGER, kind="trigger", width="q",
                          reason="unsupported ECATS request or state")
    rig.twrite(0, 0x30, AXUSER, "q")
    # Only the sourced privileged/nonsecure data read/write forms are
    # described by this reference. These are not silently normalized.
    for trigger in (0x7d, 0xbd, 0x1fd, 1):
        expect_rejected_write(rig, TBUS[0] + 0x18, trigger, kind="trigger", width="q",
                              reason="unsupported ECATS request or state")
    assert rig.tget(0, "par") == 1
    assert rig.tget(0, "ecats-pending") is False
    request(rig)
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def retrigger_and_resume_are_atomic_errors_while_a_request_is_pending(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    request(rig, wait=False)
    deadline = rig.tget(0, "ecats-deadline-ns")
    rig.step(400)
    for value in (READ_TRIGGER, WRITE_TRIGGER):
        expect_rejected_write(rig, TBUS[0] + 0x18, value, kind="trigger", width="q",
                              reason="trigger must be cleared before reuse")
    expect_rejected_write(rig, TBUS[0], 0x42, kind="halt-sid",
                          reason="ECATS still pending")
    assert rig.tget(0, "ecats-deadline-ns") == deadline
    rig.step(599)
    assert rig.tget(0, "ecats-pending") is True
    rig.step(1)
    successful_par(rig, 0, PA)
    expect_rejected_write(rig, TBUS[0] + 0x18, READ_TRIGGER, kind="trigger", width="q",
                          reason="trigger must be cleared before reuse")
    release_and_resume(rig)


def clearing_trigger_cancels_work_and_a_later_deadline_cannot_publish_a_result(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    request(rig, wait=False)
    rig.step(400)
    rig.twrite(0, 0x18, 0, "q")
    assert rig.tget(0, "ecats-pending") is False
    assert rig.tget(0, "ecats-deadline-ns") == 0
    assert rig.tget(0, "par") == 1
    assert rig.tget(0, "captured-sid") == 0
    assert rig.tget(0, "captured-va") == 0
    assert rig.tget(0, "captured-write") is False
    assert rig.get("ecats-owner") == 0
    rig.step(5000)
    assert rig.tget(0, "par") == 1
    assert fsr(rig) == 0
    request(rig)
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def read_only_feedback_and_qom_state_cannot_be_forged(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    request(rig)
    successful_par(rig, 0, PA)
    for address, kind, width, value in (
            (VERSION, "version", "l", 0x01000000),
            (TBUS[0] + 0x20, "status", "l", 0),
            (TBUS[0] + 0x28, "par", "q", IOVA << 12),
            (TBUS[0] + 0x2c, "par", "l", 0xffffffff)):
        expect_rejected_write(rig, address, value, kind=kind, width=width,
                              offset=4 if address == TBUS[0] + 0x2c else 0,
                              reason="read-only register")
    before = snapshot(rig)
    for name, value in (("enabled-reference", False),
                        ("reference-hw-version", 0x01000000),
                        ("reference-hw-version", REFERENCE_VERSION),
                        ("tcu-base", 0x03dc2000), ("tbu-count", 2),
                        ("ecats-owner", -1), ("dma-blocked-mask", 0),
                        ("tbu0-par", IOVA << 12), ("tbu0-halted", False),
                        ("qtest-cache-valid", True)):
        try:
            rig.monitor("qom-set", {"path": PATH, "property": name, "value": value})
        except AssertionError as error:
            assert "error" in str(error), str(error)
        else:
            raise AssertionError((name, "read-only/realized state was writable"))
        assert snapshot(rig) == before
    release_and_resume(rig)


def aligned_32_bit_halves_and_64_bit_transactions_preserve_little_endian_fields(rig):
    rig.twrite(0, 8, 0x0000000abcdef123, "q")
    assert rig.tread(0, 8) == 0xbcdef123
    assert rig.tread(0, 12) == 0xa
    rig.twrite(0, 8, 0x12345678)
    assert rig.tget(0, "va") == 0xa12345678
    rig.twrite(0, 12, 2)
    assert rig.tread(0, 8, "q") == 0x212345678
    rig.twrite(0, 0x30, 0, "q")
    rig.twrite(0, 0x34, 0xff0)
    assert rig.tget(0, "axuser") == AXUSER
    assert rig.tread(0, 0x30) == 0 and rig.tread(0, 0x34) == 0xff0
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    rig.twrite(0, 8, IOVA)
    rig.twrite(0, 12, 0)
    # Two legal 32-bit halves make the native 64-bit trigger value. The zero
    # upper-half write must not restart the captured lower-half request.
    rig.twrite(0, 0x18, READ_TRIGGER)
    deadline = rig.tget(0, "ecats-deadline-ns")
    rig.step(400)
    old_log = rig.log_path.read_text(errors="replace")
    rig.twrite(0, 0x1c, 0)
    assert not device_diagnostics(rig.log_path.read_text(errors="replace")[len(old_log):])
    assert rig.tget(0, "ecats-deadline-ns") == deadline
    rig.step(600)
    successful_par(rig, 0, PA)
    value = rig.tget(0, "par")
    assert rig.tread(0, 0x28) == value & 0xffffffff
    assert rig.tread(0, 0x2c) == value >> 32
    payload = rig.command(f"read {TBUS[0] + 0x28:#x} 8")[0]
    assert bytes.fromhex(payload[2:]) == struct.pack("<Q", value)
    release_and_resume(rig)


def reserved_bits_narrow_and_unaligned_accesses_do_not_partially_change_state(rig):
    # These are individual legal-width accesses rejected by the device.
    for offset, kind, value, width, local in (
            (0, "halt-sid", 1 << 10, "q", 0),
            (4, "halt-sid", 1, "l", 4),
            (8, "va", 1 << 36, "q", 0),
            (12, "va", 0x10, "l", 4),
            (0x18, "trigger", 1 << 9, "q", 0),
            (0x1c, "trigger", 1, "l", 4),
            (0x30, "axuser", 1, "q", 0),
            (0x34, "axuser", 0x1000, "l", 4)):
        expect_rejected_write(rig, TBUS[0] + offset, value, kind=kind,
                              width=width, offset=local, reason="unsupported bits")
    rig.twrite(0, 8, 0x123456789, "q")
    operations = (
        (f"readb {VERSION:#x}",
         [("read", 0, 1, TYPE + "-version", "invalid size (min:4 max:4)")]),
        (f"writew {TBUS[0]:#x} 0",
         [("write", 0, 2, TYPE + "-halt-sid", "invalid size (min:4 max:8)")]),
        (f"writel {TBUS[0] + 1:#x} 0",
         [("write", 1, 1, TYPE + "-halt-sid", "invalid size (min:4 max:8)"),
          ("write", 2, 2, TYPE + "-halt-sid", "invalid size (min:4 max:8)"),
          ("write", 4, 1, TYPE + "-halt-sid", "invalid size (min:4 max:8)")]),
        (f"writel {TBUS[0] + 13:#x} 0",
         [("write", 5, 1, TYPE + "-va", "invalid size (min:4 max:8)"),
          ("write", 6, 2, TYPE + "-va", "invalid size (min:4 max:8)"),
          ("write", TBUS[0] + 16, 1, "(null)", "rejected")]),
        (f"readw {SEMAPHORES[0]:#x}",
         [("read", 0, 2, TYPE + "-semaphore", "invalid size (min:4 max:4)")]),
    )
    for operation, expected in operations:
        before = snapshot(rig)
        old_log = rig.log_path.read_text(errors="replace")
        rig.command(operation)
        assert snapshot(rig) == before
        fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
        assert core_diagnostics(fresh) == expected, (operation, expected, fresh)
        assert not device_diagnostics(fresh), fresh


def unrepresentable_physical_results_fault_without_truncating_into_a_mapping(rig):
    high = 1 << 36
    configure_s1(rig, output=high)
    halt_tbu(rig)
    acquire(rig)
    request(rig)
    assert rig.tget(0, "par") == 1
    # The architectural walker supports this PA width; only ECATS's sourced
    # 36-bit packed output cannot represent it. Do not invent a core FSR.
    assert fsr(rig) == 0
    rig.write(cb(0) + 0x800, IOVA, "q")
    architectural = rig.read(cb(0) + 0x50, "q")
    assert architectural & 0x0000fffffffff001 == high
    rig.twrite(0, 0x18, 0, "q")
    rig.write(table(0) + 0x2008, (high - 0x1000) | PTE_RW, "q")
    request(rig, va=IOVA + 0xfff)
    successful_par(rig, 0, high - 1)
    release_and_resume(rig)


def system_reset_cancels_requests_releases_mutex_and_restores_dma_permissions(rig):
    configure_s1(rig, 0)
    configure_s1(rig, 1)
    rig.write(PA, 0x99887766)
    assert rig.read(CACHED_DMA + IOVA) == 0x99887766
    halt_tbu(rig)
    acquire(rig)
    request(rig, wait=False)
    rig.step(400)
    halt_tbu(rig, 1, wait=False)
    assert rig.tget(0, "ecats-pending") is True
    assert rig.tget(1, "halt-pending") is True
    rig.write(0x00100004, 31)
    assert rig.read(0x00100004) == 31
    rig.monitor("system_reset")
    rig.wait_for(lambda: rig.read(0x00100004) == 0, "independent GCC reset witness")
    rig.monitor("query-status")
    assert any(event.get("event") == "RESET" for event in rig.events), rig.events
    # Pure QOM before any companion register read.
    assert rig.get("ecats-owner") == -1
    assert rig.get("dma-blocked-mask") == 0
    assert rig.get("qtest-cache-valid") is False
    for index in range(10):
        for name in ("halt-sid", "va", "trigger", "axuser",
                     "halt-deadline-ns", "ecats-deadline-ns", "status",
                     "captured-sid", "captured-va"):
            assert rig.tget(index, name) == 0
        for name in ("halt-pending", "halted", "ecats-pending", "captured-write"):
            assert rig.tget(index, name) is False
        assert rig.tget(index, "par") == 1
    rig.step(10000)
    assert rig.tget(0, "par") == 1 and rig.tget(1, "status") == 0
    assert rig.read(PA) == 0x99887766
    configure_s1(rig)
    rig.write(CACHED_DMA + IOVA, 0xa1b2c3d4)
    assert rig.read(PA) == 0xa1b2c3d4
    halt_tbu(rig)
    acquire(rig)
    request(rig)
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def paused_virtual_time_and_pure_observation_do_not_complete_a_late_request(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    request(rig, wait=False)
    before = snapshot(rig)
    rig.monitor("stop")
    assert rig.monitor("query-status")["running"] is False
    time.sleep(0.03)
    for _ in range(10):
        assert snapshot(rig) == before
    # Running clock_step dispatches each timer on time. Warp only while
    # paused, then prove the request was still pending before cont.
    rig.set_clock(rig.last_clock + 6000)
    assert snapshot(rig) == before
    rig.monitor("cont")
    rig.step(1)
    assert rig.tget(0, "ecats-pending") is False
    successful_par(rig, 0, PA)
    release_and_resume(rig)


def architectural_ats_retains_standard_par_and_its_own_fault_contract(rig):
    configure_s1(rig)
    halt_tbu(rig)
    acquire(rig)
    request(rig)
    successful_par(rig, 0, PA)
    vendor_par = rig.tget(0, "par")
    rig.write(cb(0) + 0x800, IOVA, "q")
    assert rig.read(cb(0) + 0x8f0) == 0
    architectural = rig.read(cb(0) + 0x50, "q")
    assert architectural & 0x0000fffffffff001 == PA
    assert architectural != vendor_par
    rig.write(table(0) + 0x2008, 0, "q")
    rig.write(cb(0) + 0x800, IOVA, "q")
    assert rig.read(cb(0) + 0x50, "q") & 3 == 3
    assert fsr(rig) == 0  # Architectural ATS is PAR-only on failure.
    rig.wire(2, False)
    rig.twrite(0, 0x18, 0, "q")
    request(rig)
    assert rig.tget(0, "par") == 1 and fsr(rig) == 2
    rig.wire(2, True)  # Vendor ECATS is a real client-side fault path.
    clear_faults(rig)
    release_and_resume(rig)


ORDINARY_TESTS = (
    cold_state_exposes_explicit_reference_identity_without_success,
    halt_blocks_real_dma_at_request_and_ack_has_an_exact_deadline,
    all_tbu_halt_ranges_are_independent_and_do_not_block_another_sid,
    cancelling_halt_prevents_late_ack_and_releases_dma_immediately,
    read_to_acquire_is_a_shared_mutex_and_qom_observation_does_not_acquire,
    real_external_cache_hits_are_invalidated_by_the_halt_notifier,
    literal_ecats_sequence_translates_real_tables_while_ordinary_dma_is_halted,
    every_tbu_restores_the_full_sid_and_selects_its_own_context,
    ecats_write_permissions_fault_in_the_real_context_and_assert_its_irq,
    missing_leaf_faults_and_a_repaired_table_is_walked_again,
    stream_selection_errors_do_not_become_identity_translations,
    unsupported_stages_granules_and_aarch32_remain_real_configuration_faults,
    pending_request_captures_sid_va_and_attributes_without_rewriting_staging,
    semaphore_release_requires_the_owner_and_no_pending_request,
    invalid_request_state_and_attributes_do_not_start_or_claim_success,
    retrigger_and_resume_are_atomic_errors_while_a_request_is_pending,
    clearing_trigger_cancels_work_and_a_later_deadline_cannot_publish_a_result,
    read_only_feedback_and_qom_state_cannot_be_forged,
    aligned_32_bit_halves_and_64_bit_transactions_preserve_little_endian_fields,
    reserved_bits_narrow_and_unaligned_accesses_do_not_partially_change_state,
    unrepresentable_physical_results_fault_without_truncating_into_a_mapping,
    system_reset_cancels_requests_releases_mutex_and_restores_dma_permissions,
    paused_virtual_time_and_pure_observation_do_not_complete_a_late_request,
    architectural_ats_retains_standard_par_and_its_own_fault_contract,
)


def defined_windows():
    result = [(VERSION, 4, "version", 0)]
    for index in range(10):
        result.append((SEMAPHORES[index], 4, "semaphore", index))
        for offset, size, kind in ((0, 8, "halt-sid"), (8, 8, "va"),
                                   (0x18, 8, "trigger"), (0x20, 4, "status"),
                                   (0x28, 8, "par"), (0x30, 8, "axuser")):
            result.append((TBUS[index] + offset, size, kind, index))
    assert len(result) == 71 and sum(item[1] for item in result) == 484
    return result


def default_profile_rejects_vendor_state_without_breaking_architectural_dma(args, out):
    rig = V500Rig(args, out / "unknown-profile", reference=False)
    try:
        assert rig.get("enabled-reference") is False
        assert rig.get("reference-hw-version") == 0
        assert rig.get("tcu-base") == TCU and rig.get("tbu-count") == 10
        configure_s1(rig)
        rig.write(PA, 0x19283746)
        assert rig.read(DMA + IOVA) == 0x19283746
        for address, size, kind, index in defined_windows():
            old_log = rig.log_path.read_text(errors="replace")
            width = "q" if size == 8 else "l"
            rig.write(address, 0xffffffff, width)
            rig.read(address, width)
            fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert device_diagnostics(fresh) == [
                f"{TYPE}: write {kind} tbu {index} +0x0 size {size}: unknown profile",
                f"{TYPE}: read {kind} tbu {index} +0x0 size {size}: unknown profile"
            ], (address, fresh)
            assert not core_diagnostics(fresh), fresh
        for name in ("ecats-owner", "dma-blocked-mask") + tuple(
                f"tbu{index}-{field}" for index in range(10) for field in STATE_FIELDS):
            try:
                rig.get(name)
            except AssertionError as error:
                assert TYPE + ": unknown profile" in str(error), str(error)
            else:
                raise AssertionError((name, "unknown runtime state was exposed"))
        rig.step(10000)
        rig.write(DMA + IOVA, 0xabcdef01)
        assert rig.read(PA) == 0xabcdef01
        assert rig.read(CACHED_DMA + IOVA) == 0xabcdef01
        assert fsr(rig) == 0
    finally:
        rig.close()


def an_explicit_supported_non_v1_identity_is_required_at_realization(args, out):
    diagnostic = (TYPE + ": reference mode requires explicit reference-hw-version"
                  " 0x02000000; unknown mode requires 0")
    for index, options in enumerate((
            ["-global", TYPE + ".enabled-reference=on"],
            ["-global", TYPE + ".enabled-reference=on",
             "-global", TYPE + ".reference-hw-version=0x01000000"],
            ["-global", TYPE + ".enabled-reference=on",
             "-global", TYPE + ".reference-hw-version=0x02000001"],
            ["-global", TYPE + ".reference-hw-version=0x02000000"])):
        directory = out / f"invalid-reference-{index}"
        directory.mkdir()
        command = base_command(args, reference=False) + options
        (directory / "command.json").write_text(json.dumps(command, indent=2) + "\n")
        with (directory / "qemu.stderr").open("wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
            try:
                code = process.wait(timeout=10)
                assert code == 1, (options, code)
            finally:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
        stderr = (directory / "qemu.stderr").read_text(errors="replace")
        assert diagnostic in stderr, (options, stderr)


def normal_paused_tcg_has_no_external_cache_fixture_or_dma_test_alias(args, out):
    rig = V500Rig(args, out / "paused-tcg", accel="tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        names = {item["name"] for item in rig.monitor("qom-list", {"path": PATH})}
        for suffix in ("valid", "hits", "fills", "unmaps"):
            name = "qtest-cache-" + suffix
            assert name not in names
            try:
                rig.get(name)
            except AssertionError as error:
                assert "not found" in str(error), str(error)
            else:
                raise AssertionError((name, "TCG exposed QTest cache property"))
        old_log = rig.log_path.read_text(errors="replace")
        rig.read(CACHED_DMA + IOVA)
        rig.read(DMA + IOVA)
        fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
        assert core_diagnostics(fresh) == [
            ("read", CACHED_DMA + IOVA, 4, "(null)", "rejected"),
            ("read", DMA + IOVA, 4, "(null)", "rejected")], fresh
        assert rig.monitor("query-status")["running"] is False
    finally:
        rig.close()


def exact_sparse_windows_preserve_kgsl_testbus_capture_and_reserved_holes(args, out):
    rig = V500Rig(args, out / "sparse-layout")
    try:
        mtree = rig.monitor("human-monitor-command", {"command-line": "info mtree -f"})
        (rig.directory / "mtree.txt").write_text(mtree)
        names = ("version", "semaphore", "halt-sid", "va", "trigger", "status", "par", "axuser")
        pattern = (r"([0-9a-fA-F]+)-([0-9a-fA-F]+) .*: (" +
                   "|".join(re.escape(TYPE + "-" + name) for name in names) + r")(?:\s|$)")
        found = set()
        for line in mtree.splitlines():
            match = re.search(pattern, line)
            if match:
                first, last, name = match.groups()
                found.add((int(first, 16), int(last, 16), name))
        expected = {(address, address + size - 1, TYPE + "-" + kind)
                    for address, size, kind, _ in defined_windows()}
        assert found == expected, (found ^ expected, mtree)
        for index in range(10):
            assert rig.tget(index, "base") == TBUS[index]
            assert rig.tget(index, "status-address") == SEMAPHORES[index]
            assert rig.tget(index, "sid-start") == index * 0x400
        assert rig.get("tcu-base") == TCU
        halt_tbu(rig, 3)
        acquire(rig, 3)
        rig.twrite(3, 8, 0x123456789, "q")
        before = snapshot(rig)
        holes = {TCU + offset for offset in (0, 4, 8, 12, 16, 20, 28)}
        holes.update(address + 4 for address in SEMAPHORES)
        for base in TBUS:
            holes.update(base + off for off in
                         (0x10, 0x14, 0x24, 0x38, 0x3c, 0x40, 0x80, 0x100, 0xffc))
        holes.add(TBUS[-1] + 0x4000)
        # KGSL has its own DT resources. This apps-only companion must not
        # synthesize them or connect them to the apps translation pipeline.
        holes.update((GPU_TCU + 0x18, GPU_TCU + 0x200, GPU_TCU + 0x208))
        holes.update(base + off for base in GPU_TBUS
                     for off in (0, 8, 0x18, 0x20, 0x28, 0x30))
        assert all(not (first <= address <= last)
                   for address in holes for first, last, _ in expected)
        for address in sorted(holes):
            old_log = rig.log_path.read_text(errors="replace")
            rig.write(address, 0x12345678)
            rig.read(address)
            fresh = rig.log_path.read_text(errors="replace")[len(old_log):]
            assert core_diagnostics(fresh) == [
                ("write", address, 4, "(null)", "rejected"),
                ("read", address, 4, "(null)", "rejected")], (hex(address), fresh)
            assert not device_diagnostics(fresh), fresh
        assert snapshot(rig) == before
        (rig.directory / "expected-layout.json").write_text(json.dumps({
            "windows": sorted(expected), "holes_tested": sorted(holes),
            "dtb_sha256": DTB_SHA256, "dt_evidence_blob": DT_EVIDENCE_BLOB,
            "kgsl_vendor_extension_implemented": False}, indent=2) + "\n")
    finally:
        rig.close()


TBU_STATE = struct.Struct(">QQQQQ???qqQI?")
EXPECTED_CHECKS = 36


def packed_device_state(before, reference=True):
    assert TBU_STATE.size == 72
    data = struct.pack(">IIQIi", int(reference),
                       REFERENCE_VERSION if reference else 0, TCU, 10, before["owner"])
    for item in before["tbus"]:
        data += TBU_STATE.pack(
            item["halt-sid"], item["va"], item["trigger"], item["axuser"], item["par"],
            item["halt-pending"], item["halted"], item["ecats-pending"],
            item["halt-deadline-ns"], item["ecats-deadline-ns"],
            item["captured-va"], item["captured-sid"], item["captured-write"])
    assert len(data) == 24 + 10 * 72 == 744
    return data


def cold_internal_migration_state():
    # Internal serialization of the unknown profile must remain inaccessible
    # through raw QOM/MMIO. This is a migration oracle, not a guest readout.
    item = {name: 0 for name in STATE_FIELDS}
    item.update({"par": 1, "halt-pending": False, "halted": False,
                 "ecats-pending": False, "captured-write": False})
    return {"owner": -1, "dma_blocked_mask": 0, "tbus": [dict(item) for _ in range(10)]}


def actual_section(snapshot_path, before, reference=True):
    data = snapshot_path.read_bytes()
    name = TYPE.encode("ascii")
    needle = bytes([len(name)]) + name + struct.pack(">II", 0, 1)
    expected = packed_device_state(before, reference)
    candidates = []
    start = 0
    while True:
        index = data.find(needle, start)
        if index < 0:
            break
        header = index - 5
        payload = index + len(needle)
        if (header >= 8 and data[header] in (1, 4) and
                data[payload:payload + len(expected)] == expected):
            candidates.append((header, payload))
        start = index + 1
    assert len(candidates) == 1, candidates
    header, payload = candidates[0]
    return {"section_header_offset": header, "payload_offset": payload,
            "section_version": 1, "instance_id": 0, "payload_bytes": 744,
            "tbu_payload_bytes": 72, "tbu_count": 10,
            "all_serialized_fields_match_source_qom": reference}


def take_snapshot(source, path, witness, before=None):
    source.write(RAM_WITNESS, witness)
    source.monitor("stop")
    assert source.monitor("query-status")["running"] is False
    if before is None:
        before = snapshot(source)
    source.monitor("migrate", file_channels(path))
    sent = wait_migration(source)
    assert sent["status"] == "completed"
    assert source.monitor("query-status")["status"] == "postmigrate"
    assert path.stat().st_size > 0
    section = actual_section(path, before, source.reference)
    return before, sent, section


def prime_different_destination_cache(target):
    # A nonempty pre-load cache makes omitted notifier restoration visible.
    configure_s1(target, output=PA + 0x1000)
    target.write(PA + 0x1000, 0xdeadbeef)
    assert target.read(CACHED_DMA + IOVA) == 0xdeadbeef
    hits = target.get("qtest-cache-hits")
    assert target.read(CACHED_DMA + IOVA) == 0xdeadbeef
    assert target.get("qtest-cache-hits") > hits
    assert target.get("qtest-cache-valid") is True


def restore_snapshot(target, path, source_clock, before, witness):
    target.set_clock(source_clock)
    target.monitor("migrate-incoming", file_channels(path))
    got = wait_migration(target)
    assert got["status"] == "completed"
    assert target.monitor("query-status")["running"] is False
    # No companion MMIO, cont or timer advance before all restored state,
    # the live DMA blocker and the real external cache are observed.
    assert snapshot(target) == before
    assert target.get("qtest-cache-valid") is False
    assert target.read(RAM_WITNESS) == witness
    return got


def corrupt_one_field(original, changed_path, section, kind):
    data = original.read_bytes()
    base = section["payload_offset"]
    if kind == "owner-out-of-range":
        offset, width = base + 20, 4
        old = struct.unpack_from(">i", data, offset)[0]
        assert old == 7
        new = 10
        replacement = struct.pack(">i", new)
    elif kind == "captured-sid-wrong-tbu":
        # TBU7 is the actual in-flight owner. Its captured SID must remain
        # in range7 even if the staging SID has since changed.
        offset, width = base + 24 + 7 * 72 + 67, 4
        old = struct.unpack_from(">I", data, offset)[0]
        assert old == sid(7)
        new = sid(0)
        replacement = struct.pack(">I", new)
    else:
        raise AssertionError(kind)
    changed = bytearray(data)
    changed[offset:offset + width] = replacement
    assert changed != data
    assert changed[:offset] == data[:offset] and changed[offset + width:] == data[offset + width:]
    changed_path.write_bytes(changed)
    assert original.read_bytes() == data
    return {**section, "modified_field": kind, "field_offset": offset,
            "modified_field_bytes": width, "old_value": old, "new_value": new,
            "only_one_field_modified": True,
            "changed_byte_count": sum(a != b for a, b in zip(data, changed))}


def expect_incoming_failure(target, path, diagnostic):
    assert target.process.poll() is None
    request_sent = False
    command_error = None
    try:
        request_sent = True
        target.monitor("migrate-incoming", file_channels(path))
    except (AssertionError, OSError, RuntimeError) as error:
        command_error = str(error)
    code = target.process.wait(timeout=20)
    stderr = target.log_path.read_text(errors="replace")
    assert request_sent and code == 1, (request_sent, code, stderr)
    assert TYPE in stderr and diagnostic in stderr, stderr
    assert "load of migration failed" in stderr and "error while loading state" in stderr, stderr
    return {"destination_exit": code, "request_sent": request_sent,
            "command_error": command_error, "device": TYPE,
            "expected_diagnostic": diagnostic, "specific_load_failure_observed": True}


def migration_checks(args, out, report):
    rigs = []

    def new(name, **kwargs):
        rig = V500Rig(args, out / name, **kwargs)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    def completed(name, path, sent, got, section, clock):
        report["migrations"].append({
            "name": name, "passed": True, "source_status": sent["status"],
            "target_status": got["status"], "source": sent, "destination": got,
            "source_virtual_ns": clock, "destination_virtual_ns": clock,
            "target_companion_mmio_before_first_observation": False,
            "target_external_cache_was_primed": True,
            "target_external_cache_invalid_before_first_observation": True,
            "stream_modified": False, "snapshot_sha256": digest(path),
            "snapshot_bytes": path.stat().st_size, **section})

    try:
        source = new("migration-ten-halts-source")
        for index in range(10):
            configure_s1(source, index)
            source.write(pa(index), 0x71000000 + index)
        source.write(PA + 0x1000, 0xa0b0c0d0)
        assert source.read(CACHED_DMA + IOVA) == 0x71000000
        assert source.get("qtest-cache-valid") is True
        for index in range(10):
            halt_tbu(source, index, wait=False)
            if index != 9:
                source.step(100)
        source.step(50)
        assert source.last_clock == 950
        halt_path = out / "qsmmuv500-v1-ten-halt-pending.migration"
        before, sent, section = take_snapshot(source, halt_path, 0x56353031)
        target = new("migration-ten-halts-target", incoming=True)
        prime_different_destination_cache(target)
        got = restore_snapshot(target, halt_path, source.last_clock, before, 0x56353031)
        # Both the ordinary and genuinely cached aliases must honor the
        # already-published incoming gate before companion MMIO.
        target.write(DMA + IOVA, 0xbad)
        target.write(CACHED_DMA + IOVA, 0xbad)
        assert target.read(PA) == 0x71000000
        assert target.read(PA + 0x1000) == 0xa0b0c0d0
        assert fsr(target) == 0
        completed("v1_ten_halt_requests_live_dma_gate_cache_and_ram_roundtrip",
                  halt_path, sent, got, section, source.last_clock)
        check("v1_all_tbu_requests_and_actual_cache_revocation_restore_before_companion_mmio")

        target.monitor("cont")
        for index in range(10):
            target.step(49 if index == 0 else 99)
            assert target.tget(index, "halt-pending") is True
            assert target.tget(index, "halted") is False
            target.step(1)
            for number in range(10):
                assert target.tget(number, "halted") is (number <= index)
                assert target.tget(number, "halt-pending") is (number > index)
        # Only now, after every preserved timer boundary, touch companion
        # registers. A read could not have restored a missing timer.
        acquire(target, 9)
        request(target, 9)
        successful_par(target, 9, pa(9))
        release_and_resume(target, 9)
        assert target.get("dma-blocked-mask") == 0x1ff
        check("v1_all_ten_remaining_halt_deadlines_fire_before_mmio_and_ecats_continues",
              remaining_deadlines_ns=[50 + index * 100 for index in range(10)])

        source2 = new("migration-ecats-source")
        configure_s1(source2, 0)
        configure_s1(source2, 7)
        source2.write(PA, 0x72000000)
        source2.write(pa(7), 0x72000007)
        source2.write(PA + 0x1000, 0x1234abcd)
        halt_tbu(source2, 7)
        acquire(source2, 7)
        request(source2, 7, va=IOVA + 5, wait=False)
        source2.step(400)
        halt_tbu(source2, 0, wait=False)
        source2.twrite(7, 0, HALT | 0x43, "q")
        source2.twrite(7, 8, 0x9000, "q")
        source2.twrite(7, 0x30, 0, "q")
        assert source2.last_clock == 1400
        ecats_path = out / "qsmmuv500-v1-captured-ecats-pending.migration"
        before2, sent2, section2 = take_snapshot(source2, ecats_path, 0x56353032)
        target2 = new("migration-ecats-target", incoming=True)
        prime_different_destination_cache(target2)
        configure_s1(target2, 7, output=PA + 0x1000)
        got2 = restore_snapshot(target2, ecats_path, source2.last_clock, before2, 0x56353032)
        completed("v1_captured_ecats_owner_shadow_context_and_ram_roundtrip",
                  ecats_path, sent2, got2, section2, source2.last_clock)
        check("v1_owner_captured_request_staging_and_pending_halt_restore_before_companion_mmio")

        target2.monitor("cont")
        target2.step(599)
        assert target2.tget(7, "ecats-pending") is True
        assert target2.tget(7, "par") == 1
        target2.step(1)
        assert target2.tget(7, "ecats-pending") is False
        assert target2.tget(7, "par") == (pa(7) + 5) << 12
        assert target2.tget(7, "captured-sid") == sid(7)
        assert target2.tget(7, "captured-va") == IOVA + 5
        assert target2.tget(7, "va") == 0x9000
        assert target2.tget(7, "axuser") == 0
        assert target2.get("ecats-owner") == 7
        assert target2.tget(0, "halt-pending") is True
        target2.wire(9, False)
        target2.step(399)
        assert target2.tget(0, "halted") is False
        target2.step(1)
        assert target2.tget(0, "halted") is True
        # First companion MMIO follows the pure-QOM deadline proof.
        successful_par(target2, 7, pa(7) + 5)
        target2.twrite(7, 0x18, 0, "q")
        target2.write(table(7) + 0x2008, pa(7) | PTE_RO, "q")
        request(target2, 7, write=True)
        assert target2.tget(7, "par") == 1
        assert fsr(target2, 7) == 8
        assert target2.read(APPS + 0x1400 + 7 * 4) == sid(7)
        target2.wire(9, True)
        clear_faults(target2, 7)
        target2.wire(9, False)
        release_and_resume(target2, 7)
        target2.twrite(0, 0, 0)
        assert target2.read(CACHED_DMA + IOVA) == 0x72000000
        target2.write(CACHED_DMA + IOVA, 0x87654321)
        assert target2.read(PA) == 0x87654321
        assert source2.read(PA) == 0x72000000
        check("v1_deadlines_use_captured_real_walk_then_new_fault_irq_and_cached_dma_continue",
              ecats_remaining_ns=600, other_halt_remaining_ns=1000)

        unknown = new("migration-unknown-source", reference=False)
        unknown_path = out / "qsmmuv500-v1-unknown-profile.migration"
        _, unknown_sent, _ = take_snapshot(
            unknown, unknown_path, 0x56353030, cold_internal_migration_state())
        for name, path, source_reference, target_reference, source_state in (
                ("reference_to_unknown_profile_is_rejected", halt_path, True, False, sent),
                ("unknown_to_reference_profile_is_rejected", unknown_path, False, True,
                 unknown_sent)):
            rejected = new("migration-" + name, incoming=True, reference=target_reference)
            assert rejected.get("enabled-reference") is target_reference
            before_hash = digest(path)
            # load_uint32_equal reports target/source in hexadecimal.
            diagnostic = f"{int(target_reference):x} != {int(source_reference):x}"
            failure = expect_incoming_failure(rejected, path, diagnostic)
            assert digest(path) == before_hash
            report["rejected_migrations"].append({
                "name": name, "passed": True, "actual_source_stream": True,
                "stream_modified": False, "source_status": source_state["status"],
                "source_reference": source_reference, "target_reference": target_reference,
                "snapshot_sha256": before_hash, "snapshot_bytes": path.stat().st_size,
                "profile_mismatch": diagnostic, **failure})
            check(name, stream_modified=False)

        original_hash = digest(ecats_path)
        for kind, diagnostic in (
                ("owner-out-of-range", TYPE + ": invalid migrated ECATS owner"),
                ("captured-sid-wrong-tbu", TYPE + ": invalid migrated TBU 7 state")):
            changed = out / ("qsmmuv500-v1-corrupted-" + kind + ".migration")
            mutation = corrupt_one_field(ecats_path, changed, section2, kind)
            assert digest(ecats_path) == original_hash
            rejected = new("migration-corrupt-" + kind, incoming=True)
            failure = expect_incoming_failure(rejected, changed, diagnostic)
            assert digest(ecats_path) == original_hash
            report["corrupted_stream_rejections"].append({
                "name": kind, "passed": True, "actual_source_stream": True,
                "stream_modified": True, "source_status": sent2["status"],
                "legitimate_alternate_configuration": False,
                "original_stream_unchanged": True,
                "original_snapshot_sha256": original_hash,
                "corrupted_snapshot_sha256": digest(changed),
                "original_snapshot_bytes": ecats_path.stat().st_size,
                "corrupted_snapshot_bytes": changed.stat().st_size,
                **mutation, **failure})
            check("one_real_" + kind.replace("-", "_") + "_field_corruption_is_rejected",
                  stream_modified=True, only_one_field_modified=True)
    finally:
        for rig in reversed(rigs):
            rig.close()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.machine = ("tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
                    "rpmh-profile=off,rpmh-boot-configured=off")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    initial_hash = digest(args.qemu)
    report = {
        "schema": "tb371fc-smmuv500-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [],
        "expected_completed_migrations": 2, "migrations": [],
        "expected_rejected_migrations": 2, "rejected_migrations": [],
        "expected_corrupted_stream_rejections": 2, "corrupted_stream_rejections": [],
        "vmstate_version": 1, "architectural_smmuv2_vmstate_version": 2,
        "tcu_count": 1, "tbu_count": 10, "register_count": 71,
        "sparse_window_count": 71, "mapped_bytes": 484,
        "register_slots_32_bit": 21, "register_slots_64_bit": 50,
        "reference_hw_version": REFERENCE_VERSION, "reference_delay_ns": DELAY_NS,
        "reference_hw_version_is_measured": False,
        "reference_profile_is_default": False,
        "hardware_stall_queue_implemented": False,
        "kgsl_vendor_extension_implemented": False,
        "qtest_cache_fixture_used": True,
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
        "scope": "Actual compiled apps companion QTest/QMP transactions, shared "
                 "SMMUv2 stream matching and S1 walking, real IOMMU notifier "
                 "cache revocation, and real state/RAM migration",
        "source_commit": SOURCE_COMMIT,
        "source_url": "https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/"
                      + SOURCE_COMMIT + "/drivers/iommu/arm-smmu.c",
        "source_sha256": {"arm-smmu.c": SOURCE_SHA256},
        "dtb_sha256": DTB_SHA256, "dt_evidence_blob": DT_EVIDENCE_BLOB}
    start = time.monotonic()
    current = None
    try:
        assert len(ORDINARY_TESTS) == 24
        for test in ORDINARY_TESTS:
            current = test.__name__
            rig = None
            try:
                rig = V500Rig(args, out / current)
                test(rig)
                report["checks"].append({"name": current, "passed": True})
            finally:
                if rig:
                    rig.close()
        for test in (
                default_profile_rejects_vendor_state_without_breaking_architectural_dma,
                an_explicit_supported_non_v1_identity_is_required_at_realization,
                normal_paused_tcg_has_no_external_cache_fixture_or_dma_test_alias,
                exact_sparse_windows_preserve_kgsl_testbus_capture_and_reserved_holes):
            current = test.__name__
            test(args, out)
            report["checks"].append({"name": current, "passed": True})
        current = "actual_state_migration_and_rejections"
        migration_checks(args, out, report)
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 2 and
            all(item["source_status"] == item["target_status"] == "completed"
                and item["payload_bytes"] == 744 and item["tbu_count"] == 10
                and item["all_serialized_fields_match_source_qom"] is True
                and item["target_companion_mmio_before_first_observation"] is False
                and item["target_external_cache_was_primed"] is True
                and item["target_external_cache_invalid_before_first_observation"] is True
                and item["stream_modified"] is False
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 2 and
            all(item["passed"] is True and item["destination_exit"] == 1
                and item["source_status"] == "completed" and item["stream_modified"] is False
                and item["specific_load_failure_observed"] is True
                for item in report["rejected_migrations"]) and
            len(report["corrupted_stream_rejections"]) == 2 and
            all(item["passed"] is True and item["stream_modified"] is True
                and item["only_one_field_modified"] is True
                and item["original_stream_unchanged"] is True
                and item["source_status"] == "completed"
                and item["specific_load_failure_observed"] is True
                and item["destination_exit"] == 1
                for item in report["corrupted_stream_rejections"]) and
            report["binary_unchanged"])
    except BaseException as error:
        report["failing_stage"] = current
        report["error"] = str(error)
        report["traceback"] = traceback.format_exc()
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
    report["duration_seconds"] = round(time.monotonic() - start, 3)
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())






