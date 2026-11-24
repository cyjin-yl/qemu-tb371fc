#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise opt-in SMMUv2 16/64 KiB walks in actual QEMU processes.

Fixed table vectors, real SID 0x42 DMA, ATS, faults and migration; no firmware,
native IDR measurements, GPU execution or whole-board migration claim.
"""
import argparse
from copy import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time
import traceback

from test_smmuv2_migration import (APPS, GPU, DMA, CTRL, SCTRL, FSR_MASK,
                                    SMMURig, cb, sha)
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit("Acceptance checks require assertions; do not use -O.")

TABLE, PA, PA2 = 0x82000000, 0x81000000, 0x81100000
PAR_PA = 0x0000fffffffff001
RW, RO = 0x443, 0x4c3
# The initial bases deliberately satisfy only the initial table alignment.
# These constants are independently laid out from IHI0062D.c tables 2-9..2-11;
# do not replace them with the implementation's level/stride calculation.
VECTORS = (
    {"name": "16k", "profile": 3, "size": 0x4000, "tsz": 25,
     "tg0": 2, "tg1": 1, "root": TABLE + 0x80, "upper_root": TABLE + 0xc0,
     "path": ((TABLE + 0x80, TABLE + 0x4000),
              (TABLE + 0x4000, TABLE + 0x8000)),
     "upper_path": ((TABLE + 0xc0, TABLE + 0xc000),
                    (TABLE + 0xc000, TABLE + 0x10000)),
     "leaf": TABLE + 0x8008, "upper_leaf": TABLE + 0x10008,
     "va": 0x5234, "upper_va": 0xffffff8000005234,
     "block_shift": 25, "block_tsz": 28},
    {"name": "64k", "profile": 5, "size": 0x10000, "tsz": 30,
     "tg0": 1, "tg1": 3, "root": TABLE + 0x100, "upper_root": TABLE + 0x200,
     "path": ((TABLE + 0x100, TABLE + 0x10000),),
     "upper_path": ((TABLE + 0x200, TABLE + 0x20000),),
     "leaf": TABLE + 0x10008, "upper_leaf": TABLE + 0x20008,
     "va": 0x1d234, "upper_va": 0xfffffffc0001d234,
     "block_shift": 29, "block_tsz": 30},
)


def configure(rig, v, index=0, base=APPS):
    bank = cb(base, index)
    rig.put(base + 0x1000 + index * 4, 1 << 16)
    rig.put(base + 0x1800 + index * 4, 1)
    rig.put(bank + 0x10, 5 | (7 << 15))
    rig.put(bank + 0x30, v["tsz"] | (v["tsz"] << 16) |
            (v["tg0"] << 14) | (v["tg1"] << 30))
    rig.put(bank + 0x20, v["root"] | (0x42 << 48), "q")
    rig.put(bank + 0x28, v["upper_root"] | (0x43 << 48), "q")
    rig.put(bank + 0x38, 0xff)
    rig.put(bank, SCTRL)
    rig.put(base, CTRL)
    if base == APPS:
        rig.put(APPS + 0x800, 0x80000042)
        rig.put(APPS + 0xc00, index)


def tables(rig, v):
    for entry, target in v["path"] + v["upper_path"]:
        rig.put(entry, target | 3, "q")
    rig.put(v["leaf"], PA | RW, "q")
    rig.put(v["leaf"] + 8, PA2 | RW, "q")
    rig.put(v["upper_leaf"], PA2 | RW, "q")


def ats(rig, va, kind="pr", index=0, base=APPS):
    bank = cb(base, index)
    rig.put(bank + {"pr": 0x800, "pw": 0x808,
                    "ur": 0x810, "uw": 0x818}[kind], va, "q")
    assert rig.get(bank + 0x8f0) == 0
    return rig.get(bank + 0x50, "q")


def success(rig, va, pa, **kwargs):
    par = ats(rig, va, **kwargs)
    assert par & PAR_PA == pa & ~0xfff, (hex(va), hex(par), hex(pa))
    assert (par >> 56) == 0xff and par & 0x800, hex(par)


def clear_fault(rig, index=0):
    rig.put(cb(APPS, index) + 0x58, 0xffffffff)
    rig.put(APPS + 0x48, 0xffffffff)
    rig.wire(0, False)
    rig.wire(2 + index, False)


def fault(rig, va, fsr, level, index=0, ptw=False, kind="pw"):
    bank = cb(APPS, index)
    clear_fault(rig, index)
    par = ats(rig, va, kind=kind, index=index)
    assert par == 0x201 | fsr | (level << 32), hex(par)
    assert rig.get(bank + 0x58) & FSR_MASK == 0
    rig.wire(2 + index, False)
    # The same access through the DMA address space must fault independently.
    rig.put(DMA + va, 0xbad0bad0)
    assert rig.get(bank + 0x58) & FSR_MASK == fsr
    assert rig.get(bank + 0x60, "q") == va
    assert rig.get(bank + 0x68) == level | 0x30 | (0x400 if ptw else 0)
    assert rig.get(APPS + 0x1400 + index * 4) == 0x42
    rig.wire(2 + index, True)
    clear_fault(rig, index)


def profile_args(args, profile):
    result = copy(args)
    result.extra_qemu_args = ["-smp", "8"]
    if profile is not None:
        result.extra_qemu_args += ["-global", f"arm-smmuv2.aarch64-granules={profile}"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--legacy-qemu", type=Path,
                        help="Actual pre-granule VMState-v2 binary for two additional transfer checks")
    args = parser.parse_args()
    args.qemu, args.out = str(args.qemu.resolve()), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    args.machine, args.base = "tb371fc,ufs-enabled=off", APPS
    args.qom_path = "/machine/apps-smmu"
    checks = []
    report = {
        "schema_version": 1, "kind": "tb371fc-smmuv2-granules",
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "all_passed": False, "checks": checks, "migrations": [],
        "sha256": {"qemu": sha(args.qemu), "test": sha(__file__),
                   "smmu_helper": sha(Path(__file__).with_name("test_smmuv2_migration.py")),
                   "transport": sha(Path(__file__).with_name("test_geni_uart.py")),
                   "migration_helper": sha(Path(__file__).with_name("test_geni_migration.py"))},
        "scope": "Explicit architectural granule profiles, hand-written tables, ATS, real apps SID 0x42 DMA and paused process migration.",
        "limits": ["Default IDR2 still advertises only 4 KiB; no measured native large-granule support.",
                   "Reserved/unsupported TG rejection is a deliberate model deviation from architectural fallback.",
                   "48-bit PA/VA, stage 1 only; no LPA/LPA2, hardware AF/DBM, GPU execution or timing evidence.",
                   "UFS disabled; no whole-board migration or external DMA-client IOTLB cache tested."],
    }
    started = time.monotonic()

    def passed(name, observed=None):
        assert name not in {item["name"] for item in checks}, name
        item = {"name": name, "passed": True}
        if observed is not None:
            item["observed"] = observed
        checks.append(item)

    def spawn(name, profile, incoming=False, legacy=False):
        directory = args.out / name
        directory.mkdir(parents=True)
        config = profile_args(args, profile)
        if legacy:
            config.qemu = str(args.legacy_qemu.resolve())
        return SMMURig(config, directory, incoming=incoming, intercept=True)

    try:
        rig = spawn("default", None)
        try:
            report["qemu_version"] = rig.monitor("query-version")
            for base in (APPS, GPU):
                assert (rig.get(base + 0x28) >> 12) & 7 == 1
            # Original 4 KiB table geometry and real DMA remain valid.
            v = {"tsz": 25, "tg0": 0, "tg1": 2,
                 "root": TABLE, "upper_root": TABLE}
            rig.put(TABLE, (TABLE + 0x1000) | 3, "q")
            rig.put(TABLE + 0x1000, (TABLE + 0x2000) | 3, "q")
            rig.put(TABLE + 0x2008, PA | RW, "q")
            configure(rig, v)
            success(rig, 0x1234, PA + 0x234)
            rig.put(DMA + 0x1234, 0x12345678)
            assert rig.get(PA + 0x234) == 0x12345678
            passed("default-advertises-only-4k-and-keeps-real-4k-dma")
            for v in VECTORS:
                tables(rig, v)
                configure(rig, v)
                target = PA + v["va"] % v["size"]
                rig.put(target, 0xabcdef01)
                assert ats(rig, v["va"]) == 0x20000201
                assert rig.get(APPS + 0x48) == 0
                rig.put(DMA + v["va"], 0xbad)
                assert rig.get(APPS + 0x48) == 1
                assert rig.get(target) == 0xabcdef01
                rig.wire(0, True)
                clear_fault(rig)
            passed("default-rejects-unadvertised-16k-and-64k-without-translated-writes")
        finally:
            rig.close()

        for v in VECTORS:
            name = v["name"]
            rig = spawn(name, v["profile"])
            try:
                for base in (APPS, GPU):
                    assert (rig.get(base + 0x28) >> 12) & 7 == v["profile"]
                tables(rig, v)
                configure(rig, v)
                offsets = (0x44, 0x1234, v["size"] - 4)
                for i, offset in enumerate(offsets):
                    va = v["size"] + offset
                    success(rig, va, PA + offset)
                    rig.put(DMA + va, 0x11220000 + i)
                    assert rig.get(PA + offset) == 0x11220000 + i
                    assert rig.get(DMA + va) == 0x11220000 + i
                # One DMA transaction crosses the granule boundary into a
                # physically non-contiguous page, not just a 4K subpage.
                rig.put(DMA + 2 * v["size"] - 4, 0x8877665544332211, "q")
                assert rig.get(PA + v["size"] - 4) == 0x44332211
                assert rig.get(PA2) == 0x88776655
                passed(name + "-partial-root-alignment-page-offsets-and-cross-page-dma")

                success(rig, v["upper_va"], PA2 + v["va"] % v["size"])
                success(rig, v["va"], PA + v["va"] % v["size"])
                configure(rig, v, index=7, base=GPU)
                success(rig, v["va"], PA + v["va"] % v["size"], index=7, base=GPU)
                passed(name + "-ttbr1-encoding-independent-table-and-gpu-ats")

                offset = v["va"] % v["size"]
                rig.put(PA + offset, 0xabcdef01)
                rig.put(v["leaf"], PA | RO, "q")
                success(rig, v["va"], PA + offset)
                fault(rig, v["va"], 8, 3)
                assert rig.get(PA + offset) == 0xabcdef01
                passed(name + "-leaf-read-only-ats-and-client-fault-preserve-ram")

                rig.put(v["leaf"], PA | (RW & ~0x400), "q")
                fault(rig, v["va"], 4, 3)
                assert rig.get(PA + offset) == 0xabcdef01
                assert rig.get(v["leaf"], "q") & 0x400 == 0
                rig.put(v["leaf"], PA | RW, "q")
                entry, target = v["path"][-1]
                rig.put(entry, target | 3 | (1 << 62), "q")
                fault(rig, v["va"], 8, 3)
                assert rig.get(PA + offset) == 0xabcdef01
                rig.put(entry, target | 3 | (1 << 61), "q")
                assert ats(rig, v["va"], "ur") == 0x300000209
                success(rig, v["va"], PA + offset)
                rig.put(entry, target | 3, "q")
                success(rig, v["va"], PA + offset, kind="uw")
                passed(name + "-access-flag-and-hierarchical-privilege-restrictions")

                rig.put(v["leaf"], (1 << 36) | RW, "q")
                rig.put(cb(APPS, 0) + 0x10, 0 | (7 << 15))
                fault(rig, v["va"], 0x80, 3)
                rig.put(cb(APPS, 0) + 0x10, 5 | (7 << 15))
                rig.put(v["leaf"], PA | RW, "q")
                rig.put(entry, 0x30000000 | 3, "q")
                fault(rig, v["va"], 0x10, 3, ptw=True)
                rig.put(entry, target | 3, "q")
                passed(name + "-output-size-and-external-table-walk-faults")

                # L2 blocks are 32 MiB or 512 MiB; low output bits are masked.
                block = dict(v, tsz=v["block_tsz"], root=TABLE + 0x40000)
                configure(rig, block)
                rig.put(block["root"], 0x80000000 | 0x10000 | 0x441, "q")
                success(rig, 0x4568, 0x80004568)
                rig.put(DMA + 0x4568, 0xa1b2c3d4)
                assert rig.get(0x80004568) == 0xa1b2c3d4
                # Without LPA/LPA2, a valid-looking L1 block is reserved.
                configure(rig, dict(block, tsz=17))
                fault(rig, 0x4568, 2, 1)
                assert rig.get(0x80004568) == 0xa1b2c3d4
                passed(name + "-l2-block-dma-and-reserved-l1-block-rejection")

                configure(rig, v)
                original = rig.get(cb(APPS, 0) + 0x30)
                for tg in (3, 1 if name == "16k" else 2):
                    rig.put(PA + offset, 0xabcdef01)
                    rig.put(cb(APPS, 0) + 0x30, (original & ~(3 << 14)) | (tg << 14))
                    assert ats(rig, v["va"]) == 0x20000201
                    assert rig.get(APPS + 0x48) == 0
                    rig.put(DMA + v["va"], 0xbad)
                    assert rig.get(APPS + 0x48) == 1
                    assert rig.get(PA + offset) == 0xabcdef01
                    rig.wire(0, True)
                    clear_fault(rig)
                rig.put(cb(APPS, 0) + 0x30, original & ~(3 << 30))
                assert ats(rig, v["upper_va"]) == 0x20000201
                passed(name + "-reserved-and-disabled-tg-fail-closed-policy")

                rig.monitor("system_reset")
                rig.wait_for(lambda: rig.get(APPS) == 0x101, "SMMU reset")
                assert rig.get(cb(APPS, 0)) == 0
                for base in (APPS, GPU):
                    assert (rig.get(base + 0x28) >> 12) & 7 == v["profile"]
                configure(rig, v)
                success(rig, v["va"], PA + offset)
                rig.put(DMA + v["va"], 0xfaceb00c)
                assert rig.get(PA + offset) == 0xfaceb00c
                passed(name + "-reset-retains-profile-and-reconfigured-dma")
            finally:
                rig.close()

        rig = spawn("geometry", 7)
        try:
            v16, v64 = VECTORS
            # Maximum-width 16K L0 table, 16-byte aligned, VA[47] = 1.
            v = dict(v16, tsz=16, root=TABLE + 0x10)
            configure(rig, v)
            for entry, target in ((TABLE + 0x18, TABLE + 0x4000),
                                  (TABLE + 0x4000, TABLE + 0x8000),
                                  (TABLE + 0x8000, TABLE + 0xc000)):
                rig.put(entry, target | 3, "q")
            rig.put(TABLE + 0xe000, PA | RW, "q")  # L3 index 1024.
            success(rig, 0x800001001234, PA + 0x1234)
            passed("16k-48bit-l0-partial-table-and-11bit-leaf-index")

            # 64K starts at L1 for 48-bit input, with 512-byte initial table.
            v = dict(v64, tsz=16, root=TABLE + 0x200)
            configure(rig, v)
            rig.put(TABLE + 0x208, (TABLE + 0x10000) | 3, "q")
            rig.put(TABLE + 0x10000, (TABLE + 0x20000) | 3, "q")
            rig.put(TABLE + 0x28000, PA2 | RW, "q")  # L3 index 4096.
            success(rig, 0x4001000d234, PA2 + 0xd234)
            passed("64k-48bit-l1-partial-table-and-13bit-leaf-index")

            # Minimum-width input starts at L3, root only 4K aligned.
            v = dict(v64, tsz=39, root=TABLE + 0x3000)
            configure(rig, v)
            rig.put(TABLE + 0x3008, PA | RW, "q")
            success(rig, 0x1d234, PA + 0xd234)
            rig.put(DMA + 0x1d234, 0x13579bdf)
            assert rig.get(PA + 0xd234) == 0x13579bdf
            passed("64k-25bit-input-starts-at-l3-with-4k-root-alignment")

            # Different granules can coexist in the two halves of one context.
            tables(rig, v16)
            tables(rig, v64)
            # Recreate lower 16K path after overlapping scratch table writes.
            for entry, target in v16["path"]:
                rig.put(entry, target | 3, "q")
            rig.put(v16["leaf"], PA | RW, "q")
            configure(rig, v16)
            rig.put(cb(APPS, 0) + 0x28, v64["upper_root"], "q")
            rig.put(cb(APPS, 0) + 0x30, 25 | (2 << 14) | (30 << 16) | (3 << 30))
            success(rig, v16["va"], PA + 0x1234)
            success(rig, v64["upper_va"], PA2 + 0xd234)
            passed("mixed-16k-ttbr0-and-64k-ttbr1-in-one-context")
        finally:
            rig.close()

        # Reject malformed capability profiles before exposing a device.
        for profile in (0, 2, 4, 8, 9):
            command = [args.qemu, "-machine", args.machine, "-accel", "qtest",
                       "-m", "128M", "-display", "none", "-monitor", "none", "-S",
                       "-global", f"arm-smmuv2.aarch64-granules={profile}"]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=10)
            assert completed.returncode != 0 and "aarch64-granules must include 4K" in completed.stderr
        passed("invalid-profiles-rejected-at-realization")

        # Matching profile migration carries a live 16K and 64K context.
        source = target = None
        try:
            source = spawn("migration/source", 7)
            target = spawn("migration/target", 7, incoming=True)
            tables(source, VECTORS[0])
            tables(source, VECTORS[1])
            for entry, address in VECTORS[0]["path"]:
                source.put(entry, address | 3, "q")
            source.put(VECTORS[0]["leaf"], PA | RW, "q")
            configure(source, VECTORS[0], index=0)
            configure(source, VECTORS[1], index=1)
            source.put(PA + 0x1234, 0x12345678)
            source.put(PA + 0xd234, 0x87654321)
            uri = "unix:" + str(args.out / "migration/migration.sock")
            target.monitor("migrate-incoming", {"uri": uri})
            source.monitor("migrate", {"uri": uri})
            report["migrations"].append({"mode": "matching-7",
                                          "source": wait_migration(source),
                                          "destination": wait_migration(target)})
            assert source.monitor("query-status")["status"] == "postmigrate"
            assert not target.monitor("query-status")["running"]
            for index, v, value in ((0, VECTORS[0], 0x12345678),
                                     (1, VECTORS[1], 0x87654321)):
                assert (target.get(APPS + 0x28) >> 12) & 7 == 7
                target.put(APPS + 0xc00, index)
                success(target, v["va"], PA + v["va"] % v["size"], index=index)
                assert target.get(DMA + v["va"]) == value
                target.put(DMA + v["va"], value ^ 0xffffffff)
                assert target.get(PA + v["va"] % v["size"]) == value ^ 0xffffffff
            target.monitor("system_reset")
            target.wait_for(lambda: target.get(APPS) == 0x101, "migrated SMMU reset")
            for base in (APPS, GPU):
                assert (target.get(base + 0x28) >> 12) & 7 == 7
            passed("matching-profile-migration-restores-16k-and-64k-dma")
        finally:
            if source:
                source.close()
            if target:
                target.close()

        for src_profile, dst_profile in ((1, 7), (7, 1)):
            source = target = None
            mode = f"reject-profile-{src_profile}-to-{dst_profile}"
            try:
                source = spawn(mode + "/source", src_profile)
                target = spawn(mode + "/target", dst_profile, incoming=True)
                uri = "unix:" + str(args.out / mode / "migration.sock")
                target.monitor("migrate-incoming", {"uri": uri})
                source.monitor("migrate", {"uri": uri})
                target.process.wait(timeout=15)
                error = target.log_path.read_text(errors="replace")
                assert target.process.returncode != 0
                assert "error while loading state" in error and "'arm-smmuv2'" in error
                assert f"{dst_profile:x} != {src_profile:x}" in error, error
                report["migrations"].append({"mode": mode,
                    "source": source.monitor("query-migrate"),
                    "destination_exit": target.process.returncode,
                    "destination_error": error})
                passed(mode)
            finally:
                if source:
                    source.close()
                if target:
                    target.close()

        report["legacy_v2_transfer_tested"] = False
        if args.legacy_qemu:
            report["sha256"]["legacy_qemu"] = sha(args.legacy_qemu)
            for dst_profile in (1, 7):
                source = target = None
                mode = f"legacy-v2-to-profile-{dst_profile}"
                try:
                    source = spawn(mode + "/source", None, legacy=True)
                    target = spawn(mode + "/target", dst_profile, incoming=True)
                    assert (source.get(APPS + 0x28) >> 12) & 7 == 1
                    v = {"tsz": 25, "tg0": 0, "tg1": 2,
                         "root": TABLE, "upper_root": TABLE}
                    source.put(TABLE, (TABLE + 0x1000) | 3, "q")
                    source.put(TABLE + 0x1000, (TABLE + 0x2000) | 3, "q")
                    source.put(TABLE + 0x2008, PA | RW, "q")
                    configure(source, v)
                    source.put(DMA + 0x1234, 0x76543210)
                    assert source.get(PA + 0x234) == 0x76543210
                    uri = "unix:" + str(args.out / mode / "migration.sock")
                    target.monitor("migrate-incoming", {"uri": uri})
                    source.monitor("migrate", {"uri": uri})
                    if dst_profile == 1:
                        report["migrations"].append({"mode": mode,
                            "source": wait_migration(source),
                            "destination": wait_migration(target)})
                        assert source.monitor("query-status")["status"] == "postmigrate"
                        assert not target.monitor("query-status")["running"]
                        assert (target.get(APPS + 0x28) >> 12) & 7 == 1
                        success(target, 0x1234, PA + 0x234)
                        assert target.get(DMA + 0x1234) == 0x76543210
                        target.put(DMA + 0x1234, 0xabcdef01)
                        assert target.get(PA + 0x234) == 0xabcdef01
                    else:
                        target.process.wait(timeout=15)
                        error = target.log_path.read_text(errors="replace")
                        assert target.process.returncode != 0
                        assert "version 2 requires aarch64-granules=1" in error, error
                        assert "error while loading state" in error and "'arm-smmuv2'" in error
                        report["migrations"].append({"mode": mode,
                            "source": source.monitor("query-migrate"),
                            "destination_exit": target.process.returncode,
                            "destination_error": error})
                    passed(mode)
                finally:
                    if source:
                        source.close()
                    if target:
                        target.close()
            assert sha(args.legacy_qemu) == report["sha256"]["legacy_qemu"]
            report["legacy_v2_transfer_tested"] = True
        report["expected_checks"] = 28 if args.legacy_qemu else 26
        assert len(checks) == report["expected_checks"], len(checks)
        assert len(report["migrations"]) == (5 if args.legacy_qemu else 3)
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
