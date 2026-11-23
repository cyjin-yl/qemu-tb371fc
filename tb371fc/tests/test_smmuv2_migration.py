#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Migrate SMMUv2 translation, faults and IRQs between real QEMU processes.

The test-only SID 0x42 DMA alias exercises real translated memory accesses.
UFS is disabled; no whole-board migration or GPU execution is attested.
"""
import argparse
from copy import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
import traceback

from test_geni_uart import Rig
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit("Acceptance checks require assertions; do not use -O.")

APPS, GPU, GICD, DMA = 0x15000000, 0x03da0000, 0x17a00000, 0x50000000
TABLE, GPU_TABLE, PA, PA2, IOVA = 0x82000000, 0x82010000, 0x81000000, 0x81002000, 0x1000
CTRL, SCTRL = 0x200436, 0x1067
PTE_RW, PTE_RO = 0x443, 0x4c3
FSR_MASK = 0x800001fe
GIC_IDS = (32 + 64, 32 + 97, 32 + 707, 32 + 685)


def cb(base, index):
    return base + (0x80000 if base == APPS else 0x8000) + index * 0x1000


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class SMMURig(Rig):
    def __init__(self, args, directory, incoming=False, intercept=False):
        self.intercept = intercept
        super().__init__(args, str(directory), incoming=incoming)

    def command(self, command):
        if not self.intercept and command.startswith("irq_intercept_out "):
            return []
        return super().command(command)

    def get(self, address, width="l"):
        return int(self.command(f"read{width} {address:#x}")[0], 0)

    def put(self, address, value, width="l"):
        self.command(f"write{width} {address:#x} {value:#x}")

    def wire(self, number, expected):
        assert self.intercept
        self.get(APPS)
        assert self.irq.get(number, False) == expected, (number, self.irq, expected)

    def pending(self, ident):
        assert not self.intercept
        return bool(self.get(GICD + 0x200 + (ident // 32) * 4) & (1 << (ident % 32)))

    def clear_pending(self, ident):
        self.put(GICD + 0x280 + (ident // 32) * 4, 1 << (ident % 32))

    def ats(self, base, index, va, write=False):
        bank = cb(base, index)
        self.put(bank + (0x808 if write else 0x800), va, "q")
        assert self.get(bank + 0x8f0) == 0
        return self.get(bank + 0x50, "q")


def configure_bank(rig, base, index, table, control=SCTRL):
    bank = cb(base, index)
    rig.put(base + 0x1000 + index * 4, 1 << 16)
    rig.put(base + 0x1800 + index * 4, 1)
    rig.put(bank + 0x10, 5 | (7 << 15))
    rig.put(bank + 0x30, 25 | (1 << 23))
    rig.put(bank + 0x20, table | (0x42 << 48), "q")
    rig.put(bank + 0x38, 0xff)
    rig.put(bank, control)


def setup(rig, intercept=False):
    for table, pa, attr in ((TABLE, PA, PTE_RW), (GPU_TABLE, PA2, PTE_RO)):
        rig.put(table, (table + 0x1000) | 3, "q")
        rig.put(table + 0x1000, (table + 0x2000) | 3, "q")
        rig.put(table + 0x2008, pa | attr, "q")
    rig.put(PA, 0x12345678)
    rig.put(PA2, 0xabcddcba)
    rig.put(0x80010000, 0x534d4d55)
    configure_bank(rig, APPS, 0, TABLE)
    configure_bank(rig, APPS, 95, GPU_TABLE)
    configure_bank(rig, GPU, 7, GPU_TABLE, SCTRL & ~0x40)
    rig.put(APPS + 0x800, 0x80000042)
    rig.put(APPS + 0xc00, 0)
    rig.put(APPS, CTRL)
    rig.put(GPU, CTRL)
    assert rig.get(DMA + IOVA) == 0x12345678
    assert rig.ats(GPU, 7, IOVA) & 0x0000fffffffff001 == PA2
    if not intercept:
        for ident in GIC_IDS:
            offset, shift = GICD + 0xc00 + (ident // 16) * 4, (ident % 16) * 2
            rig.put(offset, rig.get(offset) & ~(3 << shift))
            rig.put(GICD + 0x100 + (ident // 32) * 4, 1 << (ident % 32))
        rig.put(GICD, rig.get(GICD) | 3)
    # A real DMA write to a read-only page records a high-bank permission fault.
    rig.put(APPS + 0xc00, 95)
    rig.put(DMA + IOVA, 0xbad)
    assert rig.get(cb(APPS, 95) + 0x58) & FSR_MASK == 8
    assert rig.get(PA2) == 0xabcddcba
    rig.put(APPS + 0xc00, 0)
    # Restore the page before migration, leaving the context fault uncleared.
    rig.put(TABLE + 0x2008, 0, "q")
    rig.put(DMA + IOVA, 0xbad)
    assert rig.get(cb(APPS, 0) + 0x58) & FSR_MASK == 2
    rig.put(TABLE + 0x2008, PA | PTE_RW, "q")
    rig.put(APPS + 0x800, 0)
    rig.put(DMA + IOVA, 0xbad)
    assert rig.get(APPS + 0x48) == 2
    rig.put(APPS + 0x800, 0x80000042)
    # Architectural FSRRESTORE supplies a masked GPU-bank fault fixture;
    # GPU ATS checks are separate from apps test-alias DMA and GPU rendering.
    rig.put(cb(GPU, 7) + 0x5c, 8)
    rig.put(cb(GPU, 7) + 0x60, 0x100001234, "q")
    if intercept:
        # GFSRRESTORE also exercises the separate configuration-fault wire.
        rig.put(APPS + 0x4c, 2 | 32)
        for number in (0, 1, 2, 97):
            rig.wire(number, True)
    else:
        assert [rig.pending(i) for i in GIC_IDS] == [True, True, True, False]


def snapshot(rig):
    addresses = []
    for base in (APPS, GPU):
        addresses += [(base + off, "l") for off in
                      (0, 0x20, 0x24, 0x28, 0x48, 0x50, 0x54, 0x58, 0x800, 0xc00)]
        addresses += [(base + 0x40, "q")]
    for base, index in ((APPS, 0), (APPS, 95), (GPU, 7)):
        bank = cb(base, index)
        addresses += [(base + 0x1000 + index * 4, "l"),
                      (base + 0x1400 + index * 4, "l"),
                      (base + 0x1800 + index * 4, "l")]
        addresses += [(bank + off, "l") for off in (0, 0x10, 0x30, 0x38, 0x58, 0x68, 0x6c)]
        addresses += [(bank + off, "q") for off in (0x20, 0x28, 0x50, 0x60)]
    return {f"{address:#x}:{width}": rig.get(address, width) for address, width in addresses}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--geometry-only", action="store_true",
                        help="Run only the incompatible SMR-capacity rejection checks")
    args = parser.parse_args()
    args.qemu, args.out = str(args.qemu.resolve()), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    args.machine = "tb371fc,ufs-enabled=off"
    args.base, args.qom_path = APPS, "/machine/apps-smmu"
    args.extra_qemu_args = ["-smp", "8"]
    checks = []
    here = Path(__file__).resolve().parent
    report = {
        "schema_version": 1, "kind": "tb371fc-smmuv2-process-migration",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": False, "checks": checks, "migrations": [],
        "machine": args.machine, "ram_per_process": "128 MiB",
        "geometry_only": args.geometry_only,
        "sha256": {"qemu": sha(args.qemu), "test": sha(__file__),
                   "transport": sha(here / "test_geni_uart.py"),
                   "migration_helper": sha(here / "test_geni_migration.py")},
        "scope": "Paused process transfers; apps SID 0x42 translated DMA, GPU ATS/register state, fault IRQ and reset continuation.",
        "limits": ["UFS is disabled; no whole-board or in-flight UFS migration.",
                   "GPU ATS/register checks do not execute GPU commands or render.",
                   "No internal IOTLB exists; page replacement checks subsequent walks, not an external DMA client's notifier cache.",
                   "No native silicon, secure state, stage 2 or timing equivalence."],
    }

    def passed(name, observed=None):
        item = {"name": name, "passed": True}
        if observed is not None:
            item["observed"] = observed
        checks.append(item)

    def transfer(source, target, directory):
        source.monitor("stop")
        uri = "unix:" + str(directory / "migration.sock")
        target.monitor("migrate-incoming", {"uri": uri})
        source.monitor("migrate", {"uri": uri})
        sr, tr = wait_migration(source), wait_migration(target)
        assert source.monitor("query-status")["status"] == "postmigrate"
        assert not target.monitor("query-status")["running"]
        report["migrations"].append({"mode": directory.name, "source": sr, "destination": tr})

    started = time.monotonic()
    try:
        for mode in (() if args.geometry_only else ("real-gic", "output-wires")):
            directory = args.out / mode
            sd, td = directory / "source", directory / "target"
            sd.mkdir(parents=True)
            td.mkdir()
            source = target = None
            try:
                intercept = mode == "output-wires"
                source = SMMURig(args, sd, intercept=intercept)
                target = SMMURig(args, td, incoming=True, intercept=intercept)
                report.setdefault("qemu_version", source.monitor("query-version"))
                setup(source, intercept)
                before = snapshot(source)
                passed(mode + "-source-translation-and-faults")
                # Ensure load replaces a different receiver state and its RAM.
                target.put(cb(APPS, 0) + 0x30, 0x1234)
                target.put(PA, 0xdeadbeef)
                transfer(source, target, directory)
                passed(mode + "-actual-transfer-completed")
                after = snapshot(target)
                assert before == after, {"before": before, "after": after}
                assert target.get(0x80010000) == 0x534d4d55
                assert target.get(TABLE + 0x2008, "q") == PA | PTE_RW
                assert target.get(GPU_TABLE + 0x2008, "q") == PA2 | PTE_RO
                passed(mode + "-registers-page-tables-and-ram-restored", after)
                if intercept:
                    for number in (0, 1, 2, 97):
                        target.wire(number, True)
                    passed("all-global-and-context-output-wires-restored")
                    target.put(APPS + 0x48, 2)
                    target.wire(0, False)
                    target.wire(1, True)
                    target.put(APPS + 0x48, 32)
                    target.wire(1, False)
                    target.put(cb(APPS, 0) + 0x58, 2)
                    target.wire(2, False)
                    target.wire(97, True)
                    target.put(cb(APPS, 95) + 0x58, 8)
                    target.wire(97, False)
                    passed("restored-output-wires-clear-independently")
                    continue
                assert [target.pending(i) for i in GIC_IDS] == [True, True, True, False]
                target.clear_pending(GIC_IDS[0])
                assert target.pending(GIC_IDS[0])
                target.put(APPS + 0x48, 2)
                target.clear_pending(GIC_IDS[0])
                assert not target.pending(GIC_IDS[0])
                passed("restored-global-fault-reaches-real-gic-until-w1c")
                target.put(DMA + IOVA, 0xbad)
                assert target.get(PA) == 0x12345678
                target.clear_pending(GIC_IDS[1])
                assert target.pending(GIC_IDS[1])
                assert target.get(cb(APPS, 0) + 0x60, "q") == IOVA
                target.put(cb(APPS, 0) + 0x58, 0xffffffff)
                target.clear_pending(GIC_IDS[1])
                assert not target.pending(GIC_IDS[1])
                target.put(DMA + IOVA, 0x76543210)
                assert target.get(PA) == 0x76543210
                passed("uncleared-context-blocks-dma-and-clearing-restores-writes")
                assert target.get(cb(APPS, 95) + 0x58) & FSR_MASK == 8
                assert target.get(cb(APPS, 95) + 0x60, "q") == IOVA
                assert target.get(APPS + 0x1400 + 95 * 4) == 0x42
                assert target.pending(GIC_IDS[2])
                target.put(cb(APPS, 95) + 0x58, 8)
                target.clear_pending(GIC_IDS[2])
                assert not target.pending(GIC_IDS[2])
                passed("highest-apps-bank-retains-independent-fault-and-stream-syndrome")
                assert target.ats(GPU, 7, IOVA) & 0x0000fffffffff001 == PA2
                assert target.ats(GPU, 7, IOVA, write=True) & 9 == 9
                # FSR.FORMAT (bit 10) describes AArch64; it is not a fault.
                assert target.get(cb(GPU, 7) + 0x58) & FSR_MASK == 8
                assert not target.pending(GIC_IDS[3])
                target.put(cb(GPU, 7), SCTRL)
                assert target.pending(GIC_IDS[3])
                target.put(cb(GPU, 7) + 0x58, 8)
                target.clear_pending(GIC_IDS[3])
                assert not target.pending(GIC_IDS[3])
                passed("gpu-ats-permissions-and-masked-fault-continue-after-load")
                target.put(GPU_TABLE + 0x2008, 0, "q")
                assert target.ats(GPU, 7, IOVA) & 3 == 3
                assert target.get(cb(GPU, 7) + 0x58) & FSR_MASK == 0
                assert not target.pending(GIC_IDS[3])
                passed("destination-gpu-ats-failure-does-not-create-client-irq")
                target.put(TABLE + 0x2008, PA2 | PTE_RW, "q")
                target.put(cb(APPS, 0) + 0x618, 0)
                target.put(cb(APPS, 0) + 0x7f0, 0)
                assert target.get(cb(APPS, 0) + 0x7f4) == 0
                assert target.get(DMA + IOVA) == 0xabcddcba
                target.put(DMA + IOVA, 0xfeedcafe)
                assert target.get(PA2) == 0xfeedcafe
                assert target.get(PA) == 0x76543210
                passed("destination-table-replacement-and-tlbi-use-new-translation")
                target.monitor("system_reset")
                target.wait_for(lambda: target.get(APPS) == 0x101, "SMMU reset")
                for base, index in ((APPS, 0), (APPS, 95), (GPU, 7)):
                    assert target.get(cb(base, index)) == 0
                    assert target.get(cb(base, index) + 0x58) == 0
                assert target.get(APPS + 0x800) == 0
                assert target.get(APPS + 0xc00) == 0x20000
                assert not any(target.pending(i) for i in GIC_IDS)
                assert target.get(PA2) == 0xfeedcafe
                passed("destination-reset-clears-translation-and-fault-state-keeps-ram")
                configure_bank(target, APPS, 0, TABLE)
                target.put(APPS + 0x800, 0x80000042)
                target.put(APPS + 0xc00, 0)
                target.put(APPS, CTRL)
                assert target.get(DMA + IOVA) == 0xfeedcafe
                passed("destination-reconfiguration-after-reset-resumes-dma")
            finally:
                if source:
                    source.close()
                if target:
                    target.close()

        for source_count, target_count in ((128, 64), (64, 128)):
            directory = args.out / f"reject-smrs-{source_count}-to-{target_count}"
            sd, td = directory / "source", directory / "target"
            sd.mkdir(parents=True)
            td.mkdir()
            source = target = None
            try:
                sa, ta = copy(args), copy(args)
                sa.extra_qemu_args = [*args.extra_qemu_args, "-global", f"arm-smmuv2.num-smrs={source_count}"]
                ta.extra_qemu_args = [*args.extra_qemu_args, "-global", f"arm-smmuv2.num-smrs={target_count}"]
                source = SMMURig(sa, sd)
                target = SMMURig(ta, td, incoming=True)
                assert source.get(APPS + 0x20) & 0xff == source_count
                assert target.get(APPS + 0x20) & 0xff == target_count
                uri = "unix:" + str(directory / "migration.sock")
                target.monitor("migrate-incoming", {"uri": uri})
                source.monitor("migrate", {"uri": uri})
                deadline = time.monotonic() + 10
                while target.process.poll() is None and time.monotonic() < deadline:
                    state = source.monitor("query-migrate")
                    if state.get("status") == "completed":
                        # A successful sender may not yet see receiver failure.
                        time.sleep(0.1)
                    else:
                        time.sleep(0.02)
                attempt = {"mode": directory.name,
                           "source": source.monitor("query-migrate"),
                           "source_smrs": source_count, "destination_smrs": target_count,
                           "destination_exit": target.process.poll()}
                if target.process.poll() is None:
                    attempt["destination"] = target.monitor("query-migrate")
                report["migrations"].append(attempt)
                assert target.process.poll() is not None, "Destination did not reject incompatible SMR capacity"
                assert target.process.returncode != 0
                error = target.log_path.read_text(errors="replace")
                assert "num_smrs" in error and "Failed to load" in error, error
                attempt["destination_error"] = error
                passed(directory.name)
            finally:
                if source:
                    source.close()
                if target:
                    target.close()
        assert len(checks) == (2 if args.geometry_only else 18), len(checks)
        assert sha(args.qemu) == report["sha256"]["qemu"], "QEMU binary changed during validation"
        report["all_passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    report["completed_utc"] = datetime.now(timezone.utc).isoformat()
    report["elapsed_seconds"] = round(time.monotonic() - started, 6)
    if not report["all_passed"]:
        report["stderr_tails"] = {str(p.relative_to(args.out)): p.read_text(errors="replace")[-4096:]
                                 for p in args.out.rglob("qemu.stderr")}
    (args.out / "result.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"all_passed": report["all_passed"], "checks": len(checks),
                      "result": str(args.out / "result.json")}))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
