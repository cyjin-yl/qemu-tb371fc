#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the real CPUSS branch, parent clocks and migration version gate.

No guest CPU or production kernel executes in this component gate. The v1
negative case relabels only the GCC section header of an actual v3 snapshot;
it proves rejection before fields are decoded, not a run of an old binary.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import time
import traceback

from test_gcc_sm8250 import GccRig, pll, OFF, ROOT, BUSY
from test_geni_migration import wait_migration

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")

CBCR, VOTE, CMD, CFG, DIV = 0x48198, 0x52000, 0x48010, 0x48014, 0x48028
CLOCK = "gcc_sys_noc_cpuss_ahb_clk"
PARENT = "gcc_cpuss_ahb_postdiv_clk_src"
RCG = "gcc_cpuss_ahb_clk_src"
OTHER_VOTE = 1 << 21
EXPECTED_CHECKS = 11
VMSTATE_VERSION = 3


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def configure(rig, configuration, wait=True):
    rig.write(CFG, configuration)
    rig.write(CMD, ROOT | BUSY)
    if wait:
        rig.step(1000)
        assert not rig.read(CMD) & BUSY


def cold_branch_and_real_parent(rig):
    assert rig.read(CBCR) == OFF
    assert rig.read(DIV) == 0
    rig.expect_hz(CLOCK, 0)
    children = rig.monitor("qom-list", {"path": "/machine/gcc"})
    external = [item["name"] for item in children
                if item["name"].endswith("_parent")]
    assert len(external) == 83, external
    assert CLOCK + "_parent" not in external
    assert "gcc_ufs_phy_ahb_clk_parent" in external


def local_vote_propagates_through_existing_parent(rig):
    rig.write(VOTE, 1)
    assert not rig.read(CBCR) & OFF
    for name in (CLOCK, PARENT, RCG):
        rig.expect_hz(name, 19_200_000)
    rig.write(VOTE, 0)
    assert rig.read(CBCR) & OFF
    for name in (CLOCK, PARENT, RCG):
        rig.expect_hz(name, 0)


def stopped_parent_and_relock(rig):
    configure(rig, (1 << 8) | 11)  # GPLL0 / 6, then read-only divide1
    rig.write(VOTE, 1)
    assert rig.read(CBCR) & OFF
    rig.expect_hz(CLOCK, 0)
    pll(rig)
    assert not rig.read(CBCR) & OFF
    rig.expect_hz(CLOCK, 100_000_000)
    rig.write(0x52018, 0)
    assert rig.read(CBCR) & OFF
    assert rig.read(VOTE) == 1
    rig.expect_hz(CLOCK, 0)
    pll(rig, wait=False)
    rig.step(4999)
    assert rig.read(CBCR) & OFF
    rig.step(1)
    rig.expect_hz(CLOCK, 100_000_000)
    assert not rig.read(CBCR) & OFF


def remote_vote_and_shared_register_are_independent(rig):
    rig.write(VOTE, OTHER_VOTE)
    rig.expect_hz("gcc_cpuss_ahb_clk", 19_200_000)
    assert rig.read(CBCR) & OFF
    rig.gpio("remote-vote", 143, True)
    rig.expect_hz(CLOCK, 19_200_000)
    assert rig.read(VOTE) == OTHER_VOTE
    rig.write(VOTE, OTHER_VOTE | 1)
    rig.gpio("remote-vote", 143, False)
    rig.expect_hz(CLOCK, 19_200_000)
    rig.write(VOTE, OTHER_VOTE)
    assert rig.read(CBCR) & OFF
    rig.expect_hz(CLOCK, 0)
    rig.expect_hz("gcc_cpuss_ahb_clk", 19_200_000)
    assert rig.read(VOTE) == OTHER_VOTE


def active_parent_changes_only_after_rcg_update(rig):
    pll(rig)
    configure(rig, (1 << 8) | 11)
    rig.write(VOTE, 1)
    rig.expect_hz(CLOCK, 100_000_000)
    rig.write(CFG, (6 << 8) | 1)  # GPLL0_OUT_EVEN, divide1
    rig.expect_hz(CLOCK, 100_000_000)
    assert rig.read(CMD) & (1 << 4)
    rig.write(CMD, ROOT | BUSY)
    rig.step(999)
    rig.expect_hz(CLOCK, 100_000_000)
    rig.step(1)
    for name in (CLOCK, PARENT, RCG):
        rig.expect_hz(name, 300_000_000)
    assert not rig.read(CBCR) & OFF


def status_cannot_override_vote_or_parent(rig):
    rig.write(CBCR, 0)
    assert rig.read(CBCR) == OFF
    rig.write(VOTE, 1)
    rig.write(CBCR, OFF)
    assert rig.read(CBCR) == 0
    rig.expect_hz(CLOCK, 19_200_000)
    rig.write(CBCR, 1)
    assert rig.read(CBCR) == 0
    assert rig.read(VOTE) == 1
    assert "unsupported write 0x00000001 at 0x48198" in (
        rig.log_path.read_text(errors="replace"))


def reset_cancels_programmed_branch(rig):
    pll(rig)
    configure(rig, (1 << 8) | 11)
    rig.write(VOTE, 1)
    rig.expect_hz(CLOCK, 100_000_000)
    rig.reset()
    rig.step(20_000)
    assert rig.read(CBCR) == OFF and rig.read(VOTE) == 0
    rig.expect_hz(CLOCK, 0)


TESTS = [
    cold_branch_and_real_parent,
    local_vote_propagates_through_existing_parent,
    stopped_parent_and_relock,
    remote_vote_and_shared_register_are_independent,
    active_parent_changes_only_after_rcg_update,
    status_cannot_override_vote_or_parent,
    reset_cancels_programmed_branch,
]


def file_channels(path):
    return {"channels": [{"channel-type": "main",
                          "addr": {"transport": "file",
                                   "filename": str(path), "offset": 0}}]}


def relabel_gcc_version(snapshot, output, advertised_version=1):
    """Find exactly one full device header using QEMU's savevm framing."""
    raw = bytearray(snapshot.read_bytes())
    assert raw[:8] == b"QEVM" + struct.pack(">I", 3), raw[:8]
    name = b"qcom-sm8250-gcc"
    matches = []
    position = 0
    while True:
        position = raw.find(name, position)
        if position < 0:
            break
        end = position + len(name)
        for length in range(len(name), 256):
            start = end - length
            if start < 6 or end + 8 > len(raw):
                continue
            full_name = bytes(raw[start:end])
            if (raw[start - 1] == length and raw[start - 6] in (1, 4) and
                    (full_name == name or full_name.endswith(b"/" + name)) and
                    all(32 <= value < 127 for value in full_name) and
                    struct.unpack_from(">II", raw, end) == (0, VMSTATE_VERSION)):
                matches.append((end + 4, full_name.decode()))
        position = end
    assert len(matches) == 1, matches
    offset, section = matches[0]
    before = bytes(raw)
    struct.pack_into(">I", raw, offset, advertised_version)
    assert raw[:offset] == before[:offset]
    assert raw[offset + 4:] == before[offset + 4:]
    output.write_bytes(raw)
    return {"section": section, "version_offset": offset,
            "source_version": VMSTATE_VERSION, "advertised_version": advertised_version,
            "original_sha256": hashlib.sha256(before).hexdigest(),
            "relabeled_sha256": hashlib.sha256(raw).hexdigest()}


def migration_checks(args, out, report):
    rigs = []
    def rig(name, incoming=False):
        obj = GccRig(args, out / name, incoming=incoming)
        rigs.append(obj)
        return obj
    def check(name):
        report["checks"].append({"name": name, "passed": True})
    try:
        source = rig("migration-source")
        pll(source)
        configure(source, (1 << 8) | 11)
        source.gpio("remote-vote", 143, True)
        source.write(VOTE, OTHER_VOTE)
        source.expect_hz(CLOCK, 100_000_000)
        configure(source, (6 << 8) | 1, wait=False)
        source.monitor("stop")
        assert source.monitor("query-status")["running"] is False
        source.command("writel 0x80010000 0x43505553")
        regs = (CBCR, VOTE, CMD, CFG, DIV, 0, 4, 0x40, 0x52018)
        before = {hex(reg): source.read(reg) for reg in regs}
        now = source.last_clock
        snapshot = out / "gcc-v3.migration"
        source.monitor("migrate", file_channels(snapshot))
        source_state = wait_migration(source)
        assert source.monitor("query-status")["status"] == "postmigrate"
        legacy = out / "gcc-advertised-v1.migration"
        version_change = relabel_gcc_version(snapshot, legacy)
        report["legacy_version_fixture"] = version_change
        check("actual_v3_stream_contains_the_new_gcc_version")

        target = rig("migration-target", incoming=True)
        target.command(f"clock_set {now}")
        target.monitor("migrate-incoming", file_channels(snapshot))
        target_state = wait_migration(target)
        assert target.monitor("query-status")["running"] is False
        assert before == {hex(reg): target.read(reg) for reg in regs}
        assert int(target.command("readl 0x80010000")[0], 0) == 0x43505553
        target.expect_hz(CLOCK, 100_000_000)
        assert target.read(VOTE) == OTHER_VOTE  # remote vote preserved
        report["migrations"].append({
            "name": "v3_file_roundtrip", "passed": True,
            "source": source_state, "destination": target_state})
        check("v3_restore_preserves_parent_shadow_remote_vote_and_ram")

        target.monitor("cont")
        target.step(999)
        target.expect_hz(CLOCK, 100_000_000)
        assert target.read(CMD) & BUSY
        target.step(1)
        target.expect_hz(CLOCK, 300_000_000)
        assert not target.read(CMD) & BUSY
        target.gpio("remote-vote", 143, False)
        target.expect_hz(CLOCK, 0)
        assert target.read(CBCR) & OFF
        target.expect_hz("gcc_cpuss_ahb_clk", 300_000_000)
        check("v3_destination_resumes_updates_and_independent_votes")

        rejected = rig("migration-v1-rejected", incoming=True)
        rejected.command(f"clock_set {now}")
        command_error = None
        try:
            rejected.monitor("migrate-incoming", file_channels(legacy))
        except (AssertionError, OSError, RuntimeError) as error:
            command_error = str(error)
        returncode = rejected.process.wait(timeout=20)
        stderr = rejected.log_path.read_text(errors="replace")
        needle = ("qcom-sm8250-gcc: incoming version_id 1 is too old "
                  f"for local minimum version_id {VMSTATE_VERSION}")
        assert returncode != 0 and needle in stderr, (returncode, stderr)
        report["migrations"].append({
            "name": "advertised_v1_rejected_before_field_decode",
            "passed": True, "destination_exit": returncode,
            "command_error": command_error, "error": needle,
            "original_v1_binary_executed": False})
        check("v1_header_is_explicitly_rejected_before_array_decode")
    finally:
        for item in reversed(rigs):
            item.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.machine = "tb371fc,gcc-boot-configured=off,ufs-enabled=off"
    args.base = 0x100000
    args.qom_path = "/machine/gcc"
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    initial_hash = digest(args.qemu)
    report = {
        "schema": "tb371fc-gcc-cpuss-v1", "passed": False,
        "expected_checks": EXPECTED_CHECKS, "checks": [], "migrations": [],
        "qemu_sha256": initial_hash, "script_sha256": digest(__file__),
        "vmstate_version": VMSTATE_VERSION, "external_parent_inputs": 83,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "scope": "Actual QTest register/clock transactions and migration; "
                 "native GCC probe and full-board boot require separate runs"}
    started = time.monotonic()
    try:
        for test in TESTS:
            rig = None
            try:
                rig = GccRig(args, out / test.__name__)
                test(rig)
                report["checks"].append({"name": test.__name__, "passed": True})
            finally:
                if rig:
                    rig.close()
        migration_checks(args, out, report)
        report["binary_unchanged"] = digest(args.qemu) == initial_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len(report["migrations"]) == 2 and
            all(item["passed"] for item in report["checks"]) and
            all(item["passed"] for item in report["migrations"]) and
            report["binary_unchanged"])
    except Exception:
        report["error"] = traceback.format_exc()
    report["elapsed_seconds"] = time.monotonic() - started
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"],
                      "checks": len(report["checks"]), "out": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
