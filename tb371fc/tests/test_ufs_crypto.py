#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reject unsupported ICE/crypto requests without modifying synthetic media.

Uses real UFS DMA through a nonidentity SMMU mapping. No native disk, crypto
key, SCM service, encryption algorithm or Android boot is exercised.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import traceback

from test_ufs_migration import UFSRig, setup, io, flush, host_block, APPS
from test_ufs_sm8250 import HCI, UTRL, CMD, sha

if not __debug__:
    raise SystemExit("Acceptance checks require assertions; do not use -O.")

ICE = 0x1d90000
CRYPTO = 0x00800000
LBA = 17
ORIGINAL = bytes((i * 37 + 11) & 255 for i in range(4096))
CHANGED = bytes((i * 19 + 83) & 255 for i in range(4096))


class CryptoRig(UFSRig):
    crypto = False

    def write(self, address, data):
        if self.crypto and address == UTRL and len(data) == 32:
            value = struct.unpack_from("<I", data)[0] | CRYPTO
            data = struct.pack("<I", value) + data[4:]
        super().write(address, data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    args.qemu, args.out = args.qemu.resolve(), args.out.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": 1,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "scope": "Unsupported ICE probe and rejected encrypted UFS DMA",
              "private_inputs_used": False,
              "qemu_sha256": sha(args.qemu), "test_sha256": sha(__file__),
              "checks": [], "passed": False}
    r = None

    def check(name, condition, observed=None):
        row = {"name": name, "passed": bool(condition)}
        if observed is not None:
            row["observed"] = observed
        report["checks"].append(row)
        assert condition, row

    try:
        run = args.out / "run"
        run.mkdir()
        r = CryptoRig(args.qemu, run)
        report["command"] = r.cmd
        check("unsupported-version-sentinel", r.r(ICE + 8) == 0xff000000)
        check("unavailable-fuse-profile", r.r(ICE + 0x10) == 1)
        check("bist-does-not-claim-success", r.r(ICE + 0x70) == 0xf0000000)
        for offset in (8, 0x10, 0x70, 0x1000):
            r.w(ICE + offset, 0x3000000)
        r.command(f"readb {ICE + 8:#x}")
        r.r(ICE + 0x1000)
        check("probe-values-are-immutable",
              [r.r(ICE + x) for x in (8, 0x10, 0x70)] ==
              [0xff000000, 1, 0xf0000000])
        setup(r)
        check("crypto-capability-remains-clear", not r.r(HCI) & (1 << 28))
        io(r, LBA, ORIGINAL)
        flush(r)
        check("ordinary-translated-io", io(r, LBA) == ORIGINAL and
              host_block(r.disk, LBA) == ORIGINAL)
        before = sha(r.disk)
        report["media_before_sha256"] = before
        cdb = [0x2a, 0] + list(struct.pack(">I", LBA)) + [0, 0, 1, 0]
        r.crypto = True
        status, _, _ = r.scsi(cdb, write=CHANGED)
        r.crypto = False
        check("encrypted-write-has-defined-error", status == 9, status)
        flush(r)
        check("encrypted-write-preserves-entire-media", sha(r.disk) == before)
        cdb[0] = 0x28
        r.crypto = True
        status, _, payload = r.scsi(cdb, buffer_size=4096)
        r.crypto = False
        check("encrypted-read-does-not-transfer-plaintext",
              status == 9 and payload == b"\xcc" * 4096,
              {"ocs": status, "buffer_unchanged": payload == b"\xcc" * 4096})

        # No UPIU or PRDT fetch is needed to reject a legacy descriptor. Its
        # deliberately unmapped command base must not prevent OCS writeback.
        r.w(HCI + 0x20, 0xffffffff)
        descriptor = struct.pack("<6I4H", 0x13800000, 0, 15, 0,
                                 0, 0xdeadbeef, 128, 256, 1, 512)
        r.write(UTRL, descriptor)
        r.w(HCI + 0x58, 1)
        r.wait(lambda: not r.r(HCI + 0x58) & 1, "invalid crypto completion")
        status = struct.unpack("<I", r.read(UTRL + 8, 4))[0] & 15
        check("invalid-upiu-and-prdt-still-complete", status == 9 and
              r.r(APPS + 0x48) == 0, {"ocs": status})

        # A transport error has no device response UPIU. Preserve its canary.
        canary = b"\x77" * 512
        r.write(CMD + 1024, canary)
        descriptor = struct.pack("<6I4H", 0x13800000, 0, 15, 0,
                                 r.dma_address(CMD), 0, 128, 256, 1, 0xffff)
        r.write(UTRL, descriptor)
        r.w(HCI + 0x58, 1)
        r.wait(lambda: not r.r(HCI + 0x58) & 1, "crypto response canary")
        status = struct.unpack("<I", r.read(UTRL + 8, 4))[0] & 15
        check("rejection-skips-prdt-and-response-dma", status == 9 and
              r.read(CMD + 1024, 512) == canary and r.r(APPS + 0x48) == 0)
        io(r, LBA, CHANGED)
        flush(r)
        check("queue-recovers-for-ordinary-io", io(r, LBA) == CHANGED and
              host_block(r.disk, LBA) == CHANGED)
        r.monitor("system_reset")
        check("reset-keeps-unsupported-ice", r.r(ICE + 8) == 0xff000000 and
              r.r(ICE + 0x70) == 0xf0000000)
        r.close()
        report["exit_code"] = r.p.returncode
        r = None
        log = (run / "qemu.stderr").read_text(errors="replace")
        check("unsupported-accesses-reported",
              "tb371fc-ice: unsupported read" in log and
              "tb371fc-ice: read-only unsupported crypto profile" in log)
        report["media_after_sha256"] = sha(run / "disk.raw")
        report["qemu_unchanged"] = sha(args.qemu) == report["qemu_sha256"]
        report["passed"] = (len(report["checks"]) == 14 and
                            report["exit_code"] == 0 and
                            report["qemu_unchanged"])
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if r is not None:
            r.close()
        report["completed_utc"] = datetime.now(timezone.utc).isoformat()
        (args.out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"],
                      "checks": len(report["checks"]),
                      "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
