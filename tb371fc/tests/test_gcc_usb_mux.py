#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the two compiled USB PIPE muxes and their real Clock inputs.

Fixture frequencies exist only under QTest. No USB controller, PHY training,
guest CPU instructions or physical clock frequencies are attested here.
The v2 negative test relabels an actual v3 GCC snapshot header; it does not
execute an older QEMU binary.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback

from test_geni_uart import Rig, connect_unix
from test_geni_migration import wait_migration
from test_gcc_sm8250 import GccRig, OFF, pll
from test_gcc_cpuss import file_channels, relabel_gcc_version, VMSTATE_VERSION

if not __debug__:
    raise SystemExit("Acceptance requires assertions; do not use -O.")

MUXES = (0xf060, 0x10060)
BRANCHES = (0xf05c, 0x1005c)
IDS = (188, 194)
NAMES = ("gcc_usb3_prim_phy_pipe_clk", "gcc_usb3_sec_phy_pipe_clk")
PHY_INPUTS = tuple(name + "_parent" for name in NAMES)
TEST_INPUT = "core_bi_pll_test_se"
FIXTURES = ("qtest-usb3-primary-phy-hz", "qtest-usb3-secondary-phy-hz",
            "qtest-core-bi-pll-test-hz")
XO = 19_200_000
EXPECTED_CHECKS = 15


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inputs(rig, primary=125_000_000, secondary=250_000_000, test=62_500_000):
    for prop, hz in zip(FIXTURES, (primary, secondary, test)):
        rig.monitor("qom-set", {"path": "/machine/gcc",
                              "property": prop, "value": hz})


def source_hz(rig, index, hz):
    rig.monitor("qom-set", {"path": "/machine/gcc",
                          "property": FIXTURES[index], "value": hz})


def select(rig, primary, secondary):
    rig.write(MUXES[0], primary)
    rig.write(MUXES[1], secondary)
    assert (rig.read(MUXES[0]), rig.read(MUXES[1])) == (primary, secondary)


def local_on(rig):
    for offset in BRANCHES:
        rig.write(offset, 1)


def rates(rig, primary, secondary):
    for index, hz in enumerate((primary, secondary)):
        rig.expect_hz(NAMES[index], hz)
        assert bool(rig.read(BRANCHES[index]) & OFF) == (hz == 0)


def mux_rates(rig, primary, secondary):
    for index, hz in enumerate((primary, secondary)):
        rig.expect_hz(NAMES[index] + "_src", hz)


def cold_unknown_inputs_are_stopped(rig):
    assert tuple(rig.read(offset) for offset in MUXES) == (0, 0)
    assert tuple(rig.read(offset) for offset in BRANCHES) == (OFF, OFF)
    rates(rig, 0, 0)
    mux_rates(rig, 0, 0)
    for name in (*PHY_INPUTS, TEST_INPUT):
        rig.expect_hz(name, 0)
    children = rig.monitor("qom-list", {"path": "/machine/gcc"})
    names = {item["name"] for item in children}
    assert all(prop in names for prop in FIXTURES)
    assert sum(name.endswith("_parent") for name in names) == 83
    assert all(name in names for name in PHY_INPUTS)


def independent_phy_sources_feed_their_muxes(rig):
    inputs(rig)
    # The mux is a selector; only its downstream branch has a gate.
    mux_rates(rig, 125_000_000, 250_000_000)
    rates(rig, 0, 0)
    local_on(rig)
    rates(rig, 125_000_000, 250_000_000)
    for name, hz in zip(PHY_INPUTS, (125_000_000, 250_000_000)):
        rig.expect_hz(name, hz)
    assert tuple(rig.read(offset) for offset in MUXES) == (0, 0)


def shared_test_source_fans_out(rig):
    inputs(rig)
    local_on(rig)
    select(rig, 1, 1)
    mux_rates(rig, 62_500_000, 62_500_000)
    rates(rig, 62_500_000, 62_500_000)
    source_hz(rig, 2, 80_000_000)
    rates(rig, 80_000_000, 80_000_000)
    rig.write(MUXES[0], 0)
    rates(rig, 125_000_000, 80_000_000)


def xo_selection_uses_the_board_clock(rig):
    inputs(rig)
    local_on(rig)
    select(rig, 2, 2)
    mux_rates(rig, XO, XO)
    rates(rig, XO, XO)
    inputs(rig, 0, 0, 0)
    rates(rig, XO, XO)
    rig.expect_hz("xo", XO)


def undefined_selector_has_no_parent(rig):
    inputs(rig)
    local_on(rig)
    select(rig, 3, 3)
    rates(rig, 0, 0)
    mux_rates(rig, 0, 0)
    assert all(rig.read(offset) & 1 for offset in BRANCHES)
    source_hz(rig, 2, 90_000_000)
    rates(rig, 0, 0)
    select(rig, 2, 0)
    rates(rig, XO, 250_000_000)


def live_phy_source_changes_propagate(rig):
    inputs(rig)
    local_on(rig)
    rates(rig, 125_000_000, 250_000_000)
    source_hz(rig, 0, 100_000_000)
    rates(rig, 100_000_000, 250_000_000)
    source_hz(rig, 0, 0)
    rates(rig, 0, 250_000_000)
    source_hz(rig, 1, 0)
    rates(rig, 0, 0)
    source_hz(rig, 0, 150_000_000)
    rates(rig, 150_000_000, 0)
    assert tuple(rig.read(offset) for offset in MUXES) == (0, 0)


def live_test_source_changes_follow_selection(rig):
    inputs(rig)
    local_on(rig)
    select(rig, 1, 1)
    source_hz(rig, 2, 0)
    rates(rig, 0, 0)
    source_hz(rig, 2, 90_000_000)
    rates(rig, 90_000_000, 90_000_000)
    rig.write(MUXES[0], 2)
    source_hz(rig, 2, 75_000_000)
    rates(rig, XO, 75_000_000)
    assert tuple(rig.read(offset) for offset in MUXES) == (2, 1)


def local_and_remote_requests_are_independent(rig):
    select(rig, 2, 2)
    rates(rig, 0, 0)
    rig.gpio("remote-vote", IDS[0], True)
    rates(rig, XO, 0)
    assert rig.read(BRANCHES[0]) & 1 == 0
    rig.write(BRANCHES[1], 1)
    rates(rig, XO, XO)
    rig.gpio("remote-vote", IDS[1], True)
    rig.write(BRANCHES[1], 0)
    rates(rig, XO, XO)
    assert rig.read(BRANCHES[1]) & 1 == 0
    rig.gpio("remote-vote", IDS[1], False)
    rates(rig, XO, 0)
    rig.write(BRANCHES[0], 1)
    rig.gpio("remote-vote", IDS[0], False)
    rates(rig, XO, 0)
    rig.write(BRANCHES[0], 0)
    rates(rig, 0, 0)
    mux_rates(rig, XO, XO)


def invalid_masks_preserve_selectors_and_rates(rig):
    inputs(rig)
    local_on(rig)
    select(rig, 2, 1)
    for value in (4, 0x101, 0xffffffff):
        rig.write(MUXES[0], value)
        rig.write(MUXES[1], value)
        assert tuple(rig.read(offset) for offset in MUXES) == (2, 1)
        rates(rig, XO, 62_500_000)
    log = rig.log_path.read_text(errors="replace")
    assert "unsupported write 0x00000004 at 0xf060" in log
    assert "unsupported write 0x00000004 at 0x10060" in log


def reset_preserves_external_inputs_and_remote_request(rig):
    # GccRig.reset uses cleared PLL registers as an asynchronous reset witness.
    pll(rig)
    assert rig.read(4) != 0 and rig.read(0x52018) != 0
    inputs(rig)
    select(rig, 1, 2)
    rig.write(BRANCHES[0], 1)
    rig.gpio("remote-vote", IDS[1], True)
    rates(rig, 62_500_000, XO)
    rig.reset()
    assert tuple(rig.read(offset) for offset in MUXES) == (0, 0)
    assert all(rig.read(offset) & 1 == 0 for offset in BRANCHES)
    # Existing GCC reset contract leaves provider-owned input levels intact.
    rates(rig, 0, 250_000_000)
    mux_rates(rig, 125_000_000, 250_000_000)
    for name, hz in zip((*PHY_INPUTS, TEST_INPUT),
                        (125_000_000, 250_000_000, 62_500_000)):
        rig.expect_hz(name, hz)
    rig.gpio("remote-vote", IDS[1], False)
    rates(rig, 0, 0)
    rig.write(BRANCHES[0], 1)
    rates(rig, 125_000_000, 0)


def fixture_hz_types_reject_invalid_values(rig):
    inputs(rig)
    local_on(rig)
    for prop in FIXTURES:
        for value in (-1, 1 << 32, True):
            try:
                rig.monitor("qom-set", {"path": "/machine/gcc",
                                      "property": prop, "value": value})
            except AssertionError as error:
                assert "error" in str(error), str(error)
            else:
                raise AssertionError((prop, value, "invalid uint32 accepted"))
    rates(rig, 125_000_000, 250_000_000)
    rig.expect_hz(TEST_INPUT, 62_500_000)


ORDINARY_TESTS = [
    cold_unknown_inputs_are_stopped,
    independent_phy_sources_feed_their_muxes,
    shared_test_source_fans_out,
    xo_selection_uses_the_board_clock,
    undefined_selector_has_no_parent,
    live_phy_source_changes_propagate,
    live_test_source_changes_follow_selection,
    local_and_remote_requests_are_independent,
    invalid_masks_preserve_selectors_and_rates,
    reset_preserves_external_inputs_and_remote_request,
    fixture_hz_types_reject_invalid_values,
]


class PausedTcg(Rig):
    """A real paused TCG process; no QTest server or guest instructions."""
    def __init__(self, args, directory):
        self.args = args
        self.directory = directory
        directory.mkdir()
        self.log_path = directory / "qemu.stderr"
        self.log = self.log_path.open("wb")
        command = [args.qemu, "-machine", args.machine, "-accel", "tcg",
                   "-m", "128M", "-display", "none", "-monitor", "none",
                   "-serial", "null", "-S", "-qmp",
                   f"unix:{directory}/qmp,server=on,wait=off"]
        self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=self.log)
        self.connections = []
        try:
            self.qmp = connect_unix(directory / "qmp", self.process)
            self.connections.append(self.qmp)
            self.qmpfile = self.qmp.makefile("rwb", buffering=0)
            self.events = []
            greeting = json.loads(self.qmpfile.readline())
            assert "QMP" in greeting, greeting
            self.monitor("qmp_capabilities")
        except BaseException:
            self.close()
            raise


def normal_tcg_exposes_no_fixture_inputs(args, out):
    rig = PausedTcg(args, out / "paused-tcg")
    try:
        assert rig.monitor("query-status")["running"] is False
        children = rig.monitor("qom-list", {"path": "/machine/gcc"})
        names = {item["name"] for item in children}
        assert all(prop not in names for prop in FIXTURES)
        for prop in FIXTURES:
            try:
                rig.monitor("qom-set", {"path": "/machine/gcc",
                                      "property": prop, "value": 125_000_000})
            except AssertionError as error:
                assert "not found" in str(error), str(error)
            else:
                raise AssertionError(("TCG exposed fixture input", prop))
        assert rig.monitor("query-status")["running"] is False
        return {"accel": "tcg", "paused_before_and_after": True,
                "fixture_properties_absent": True}
    finally:
        rig.close()


def migration_checks(args, out, report):
    rigs = []

    def new_rig(name, incoming=False):
        rig = GccRig(args, out / name, incoming=incoming)
        rigs.append(rig)
        return rig

    def check(name, **details):
        report["checks"].append({"name": name, "passed": True, **details})

    try:
        source = new_rig("migration-source")
        inputs(source)
        select(source, 0, 1)
        source.gpio("remote-vote", IDS[0], True)
        source.write(BRANCHES[1], 1)
        rates(source, 125_000_000, 62_500_000)
        source.command("writel 0x80010000 0x4d555833")
        source.monitor("stop")
        assert source.monitor("query-status")["running"] is False
        before = {hex(offset): source.read(offset)
                  for offset in (*MUXES, *BRANCHES)}
        snapshot = out / "gcc-mux-v3.migration"
        source.monitor("migrate", file_channels(snapshot))
        source_state = wait_migration(source)
        assert source.monitor("query-status")["status"] == "postmigrate"

        target = new_rig("migration-target", incoming=True)
        for name in (*PHY_INPUTS, TEST_INPUT):
            target.expect_hz(name, 0)
        if source.last_clock:
            target.command(f"clock_set {source.last_clock}")
        target.monitor("migrate-incoming", file_channels(snapshot))
        target_state = wait_migration(target)
        assert source_state["status"] == target_state["status"] == "completed"
        assert target.monitor("query-status")["running"] is False
        assert before == {hex(offset): target.read(offset)
                          for offset in (*MUXES, *BRANCHES)}
        assert int(target.command("readl 0x80010000")[0], 0) == 0x4d555833
        for name, hz in zip((*PHY_INPUTS, TEST_INPUT),
                            (125_000_000, 250_000_000, 62_500_000)):
            target.expect_hz(name, hz)
        rates(target, 125_000_000, 62_500_000)
        report["migrations"].append({
            "name": "v3_mux_input_and_vote_roundtrip",
            "source_status": source_state["status"],
            "target_status": target_state["status"],
            "passed": True, "source": source_state, "destination": target_state})
        check("v3_restore_preserves_muxes_inputs_remote_vote_and_ram")

        target.monitor("cont")
        source_hz(target, 0, 100_000_000)
        rates(target, 100_000_000, 62_500_000)
        source_hz(target, 2, 75_000_000)
        rates(target, 100_000_000, 75_000_000)
        select(target, 1, 0)
        rates(target, 75_000_000, 250_000_000)
        target.gpio("remote-vote", IDS[0], False)
        rates(target, 0, 250_000_000)
        target.write(BRANCHES[1], 0)
        rates(target, 0, 0)
        target.gpio("remote-vote", IDS[1], True)
        rates(target, 0, 250_000_000)
        check("v3_destination_propagates_new_sources_and_independent_requests")

        legacy = out / "gcc-mux-advertised-v2.migration"
        version_change = relabel_gcc_version(snapshot, legacy, advertised_version=2)
        assert version_change["source_version"] == VMSTATE_VERSION == 3
        assert version_change["advertised_version"] == 2
        report["legacy_version_fixture"] = version_change
        rejected = new_rig("migration-v2-rejected", incoming=True)
        assert rejected.process.poll() is None
        command_error = None
        try:
            rejected.monitor("migrate-incoming", file_channels(legacy))
        except (AssertionError, OSError, RuntimeError) as error:
            command_error = str(error)
        returncode = rejected.process.wait(timeout=20)
        stderr = rejected.log_path.read_text(errors="replace")
        needle = ("qcom-sm8250-gcc: incoming version_id 2 is too old "
                  "for local minimum version_id 3")
        assert returncode != 0 and needle in stderr, (returncode, stderr)
        report["rejected_migrations"].append({
            "name": "advertised_v2_rejected_before_field_decode",
            "passed": True, "destination_exit": returncode,
            "command_error": command_error, "error": needle,
            "original_v2_binary_executed": False})
        check("v2_header_is_rejected_before_new_clock_state_is_decoded")
    finally:
        for rig in reversed(rigs):
            rig.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.qemu = os.path.abspath(args.qemu)
    args.machine = "tb371fc,gcc-boot-configured=off,ufs-enabled=off"
    args.base, args.qom_path = 0x100000, "/machine/gcc"
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    initial_hash = sha(args.qemu)
    report = {"schema": "tb371fc-gcc-usb-mux-v1", "passed": False,
              "expected_checks": EXPECTED_CHECKS, "checks": [],
              "migrations": [], "rejected_migrations": [],
              "qemu_sha256": initial_hash, "script_sha256": sha(__file__),
              "vmstate_version": 3, "external_parent_inputs": 83,
              "expected_completed_migrations": 1,
              "expected_rejected_migrations": 1,
              "guest_cpu_executed": False, "usb_phy_emulated": False,
              "native_kernel_probe_observed": False,
              "scope": "Actual mux/Clock/gate transactions and migration; "
                       "fixture Hz are explicit QTest inputs, not physical measurements"}
    started = time.monotonic()
    try:
        assert VMSTATE_VERSION == 3
        for test in ORDINARY_TESTS:
            rig = None
            try:
                rig = GccRig(args, out / test.__name__)
                test(rig)
                report["checks"].append({"name": test.__name__, "passed": True})
            finally:
                if rig:
                    rig.close()
        details = normal_tcg_exposes_no_fixture_inputs(args, out)
        report["checks"].append({
            "name": "normal_tcg_exposes_no_fixture_inputs", "passed": True, **details})
        migration_checks(args, out, report)
        report["binary_unchanged"] = sha(args.qemu) == initial_hash
        report["passed"] = (
            len(report["checks"]) == EXPECTED_CHECKS and
            len({item["name"] for item in report["checks"]}) == EXPECTED_CHECKS and
            all(item["passed"] is True for item in report["checks"]) and
            len(report["migrations"]) == 1 and
            all(item["source_status"] == item["target_status"] == "completed"
                for item in report["migrations"]) and
            len(report["rejected_migrations"]) == 1 and
            all(item["passed"] is True for item in report["rejected_migrations"]) and
            report["binary_unchanged"])
    except Exception:
        report["error"] = traceback.format_exc()
    report["completed_migrations"] = len(report["migrations"])
    report["rejected_migration_attempts"] = len(report["rejected_migrations"])
    report["elapsed_seconds"] = time.monotonic() - started
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"passed": report["passed"], "checks": len(report["checks"]),
                      "completed_migrations": report["completed_migrations"],
                      "rejected_migrations": report["rejected_migration_attempts"],
                      "out": str(out)}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
