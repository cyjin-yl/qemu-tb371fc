#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the public TB371FC acceptance gates sequentially and preserve receipts.

No production boot image or AOP input is used. Short, retained /tmp fixtures
avoid Linux AF_UNIX path limits even inside a long GitHub checkout directory.
"""
import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Case:
    name: str
    command: list[str]
    fixture: Path
    result: str = "stdout"
    verdict: str = "passed"
    expected_count: int | None = None
    count_field: str = "checks"
    expected_values: dict = field(default_factory=dict)
    tap_count: int | None = None
    timeout: int = 180
    expected_lengths: dict = field(default_factory=dict)


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def plan(args, runtime):
    cases = []
    qemu = str(args.qemu.resolve())
    build = args.build_dir.resolve()
    tests, scripts = ROOT / "tb371fc/tests", ROOT / "tb371fc/scripts"
    legacy = "tb371fc,ufs-enabled=off,counter-exact=off"
    exact = "tb371fc,ufs-enabled=off"

    def add(name, script, flags=(), output=None, verdict="passed", count=None,
            result="stdout", expected=None, count_field="checks", timeout=180,
            expected_lengths=None):
        fixture = runtime / f"{len(cases):02d}"
        data = fixture / "data"
        argv = [sys.executable, str(script)]
        argv += [str(x).replace("@DATA@", str(data)) for x in flags]
        if output:
            argv += [output, str(data)]
        if result == "file":
            result = str(data / "result.json")
        cases.append(Case(name, argv, fixture, result, verdict, count,
                          count_field, expected or {}, timeout=timeout,
                          expected_lengths=expected_lengths or {}))
        return data

    def device(name, script, *flags, **kwargs):
        return add(name, tests / script, ("--qemu", qemu, *flags), **kwargs)

    if args.suite in ("all", "components"):
        for name, script in (
            ("ci-verdict-unit", scripts / "test_ci_device_tests.py"),
            ("smem-observer-unit", scripts / "test_observe_smem.py"),
            ("board-profile-unit", scripts / "test_board_profile.py"),
            ("machine-validator-unit", scripts / "test_validate_machine.py"),
            ("dt-inventory-unit", scripts / "test_inventory_dtb.py"),
            ("hardware-sysfs-unit", scripts / "test_collect_hardware_sysfs.py"),
            ("guest-result-unit", tests / "test_guest_result.py"),
            ("console-boot-verdict-unit", tests / "test_boot_verdict.py"),
            ("tlmm-map-unit", tests / "test_tlmm_map.py"),
        ):
            add(name, script, result="exit")
        add("live-machine-profile", scripts / "validate_machine.py",
            ("--qemu", qemu, "--output", "@DATA@/result.json",
             "--dtb-output", "@DATA@/generated.dtb"), result="file")
        device("geni-registers", "test_geni_uart.py",
               verdict="success", count=14)
        device("geni-migration", "test_geni_migration.py",
               verdict="success", count=6)
        device("gcc-registers", "test_gcc_sm8250.py",
               output="--work-dir", verdict="success", count=19)
        device("gcc-migration", "test_gcc_sm8250.py", "--migration-only",
               output="--work-dir", verdict="success", count=5)
        device("geni-clock-reset", "test_geni_clock_reset.py",
               output="--work-dir", verdict="success", count=6)
        device("geni-clock-migration", "test_geni_clock_reset.py",
               "--migration-only", output="--work-dir", verdict="success", count=4)
        add("counter-arithmetic", tests / "test_counter_conversion.py",
            ("--build-config", str(build)), output="--out", result="file",
            expected={"cases": 1848, "errors": []})
        device("mmio-timer-exact", "test_mmio_timer.py",
               output="--out", result="file", count=10)
        device("mmio-timer-legacy", "test_mmio_timer.py",
               "--machine", legacy, "--counter-mode", "legacy",
               output="--out", result="file", count=10)
        device("cpu-counter-exact", "test_mmio_timer_cpu.py", "--icount-rate",
               output="--out", result="file",
               expected={"cpu_timer_probe_passed": True})
        device("cpu-counter-legacy", "test_mmio_timer_cpu.py", "--icount-rate",
               "--machine", legacy, "--counter-mode", "legacy",
               output="--out", result="file",
               expected={"cpu_timer_probe_passed": True})
        device("mmio-timer-migration", "test_mmio_timer_migration.py",
               output="--out", result="file", count=4)
        device("mmio-timer-legacy-migration", "test_mmio_timer_migration.py",
               "--machine", legacy, "--counter-mode", "legacy",
               output="--out", result="file", count=4)
        device("reject-exact-to-legacy", "test_mmio_timer_migration.py",
               "--target-machine", legacy, "--expect-mode-rejection",
               output="--out", result="file", count=2)
        device("reject-legacy-to-exact", "test_mmio_timer_migration.py",
               "--machine", legacy, "--counter-mode", "legacy",
               "--target-machine", exact, "--expect-mode-rejection",
               output="--out", result="file", count=2)
        migration = device("cpu-timer-migration", "test_cpu_timer_migration.py",
                           output="--out", result="file")
        device("cpu-timer-control", "test_cpu_timer_migration.py",
               "--without-migration", "--elf", str(migration / "probe.elf"),
               output="--out", result="file")
        device("cpu-irq-realtime", "test_cpu_timer_irq.py",
               output="--out", result="file")
        device("cpu-irq-exact-icount", "test_cpu_timer_irq.py", "--icount",
               output="--out", result="file")
        device("cpu-irq-legacy-icount", "test_cpu_timer_irq.py", "--icount",
               "--machine", legacy, output="--out", result="file")
        for name, binary, count in (
            ("smmuv2-c-qtest", "tb371fc-smmuv2-test", 23),
            ("tlmm-c-qtest", "tb371fc-tlmm-test", 25),
            ("pdc-c-qtest", "tb371fc-pdc-test", 18),
        ):
            fixture = runtime / f"{len(cases):02d}"
            cases.append(Case(name, [str(build / "tests/qtest" / binary), "--tap"],
                              fixture, result="tap", tap_count=count))
        device("rpmh-reference", "test_rpmh_sm8250.py",
               output="--work-dir", verdict="success", count=22)
        device("rpmh-reference-migration", "test_rpmh_sm8250.py",
               "--migration-only", output="--work-dir", verdict="success", count=5)
        device("rpmh-display", "test_rpmh_display.py",
               output="--work-dir", verdict="success", count=11)
        device("rpmh-display-migration", "test_rpmh_display.py",
               "--migration-only", output="--work-dir", verdict="success", count=6)
        device("qsee-notification", "test_qsee_irq.py",
               output="--work-dir", verdict="success", count=12)
        device("qsee-notification-migration", "test_qsee_irq.py",
               "--migration-only", output="--work-dir", verdict="success", count=6)
        device("tcsr-mutex", "test_tcsr_mutex.py",
               output="--out", result="file", count=24,
               expected={"all_passed": True, "expected_checks": 24,
                         "native_reset_observed": False,
                         "external_processor_emulated": False},
               expected_lengths={"migrations": 3})
        device("refgen-request", "test_refgen.py",
               output="--out", result="file", count=16,
               expected={"all_passed": True, "success": True,
                         "expected_checks": 16, "migration_expected_checks": 6,
                         "socket_directories_retained": True},
               expected_lengths={"migrations": 6})
        device("gpu-gx-gdsc", "test_gpu_gx_gdsc.py",
               output="--out", result="file", count=37,
               expected={"all_passed": True, "success": True,
                         "expected_checks": 37, "migration_expected_checks": 12,
                         "expected_migration_attempts": 12,
                         "completed_migration_attempts": 8,
                         "rejected_migration_attempts": 4,
                         "passed_checks": 37},
               expected_lengths={"migrations": 12, "migration_attempts": 12})
        device("gpu-cx-gdsc", "test_gpu_cx_gdsc.py",
               output="--out", result="file", count=26,
               expected={"all_passed": True, "success": True,
                         "expected_checks": 26, "migration_expected_checks": 8,
                         "expected_migration_attempts": 9,
                         "completed_migration_attempts": 6,
                         "rejected_migration_attempts": 3,
                         "socket_directories_retained": True},
               expected_lengths={"migrations": 8, "migration_attempts": 9})
        device("aop-reference-peer", "test_aop_reference.py",
               output="--out", result="file", count=30,
               expected={"all_passed": True, "expected_checks": 30,
                         "expected_completed_migrations": 6,
                         "expected_rejected_migrations": 2,
                         "completed_migrations": 6,
                         "rejected_migration_attempts": 2,
                         "guest_cpu_executed": False,
                         "ipcc_output_intercepted": False},
               expected_lengths={"migrations": 6, "rejected_migrations": 2})
        device("aop-msgram", "test_aop_msgram.py",
               output="--out", result="file", count=10,
               expected={"all_passed": True, "expected_checks": 10,
                         "output_interception_installed": False},
               expected_lengths={"migrations": 1})
        device("smem-reference-handoff", "test_smem_reference.py",
               output="--out", result="file", count=22,
               expected={"all_passed": True, "expected_checks": 22,
                         "guest_cpu_executed": False,
                         "native_sbl_captured": False,
                         "remote_firmware_emulated": False},
               expected_lengths={"migrations": 4})
        device("dcc-operations", "test_dcc_ops.py",
               output="--out", result="file", count=16)
        device("ufs-register-dma", "test_ufs_sm8250.py",
               output="--out", result="file", count=56)
        device("watchdog-registers", "test_watchdog_kpss.py",
               output="--out", result="file", count=16)
        device("watchdog-migration", "test_watchdog_kpss.py",
               "--migration-only", output="--out", result="file", count=1)
        device("boot-resources", "test_boot_resources.py",
               output="--out", result="file", count=7)
        device("ipcc-messages", "test_ipcc.py",
               output="--out", result="file", count=16)
        device("dcc-capture", "test_dcc.py",
               output="--out", result="file", count=17)
        device("spmi-pmic-arb", "test_spmi_pmic_arb.py",
               output="--out", result="file", count=25)
        device("spmi-pdc-gic-route", "test_spmi_pdc_route.py",
               output="--out", result="file", count=8,
               expected={"all_passed": True, "expected_checks": 8,
                         "output_interception_installed": False},
               expected_lengths={"migrations": 1})
        device("tlmm-migration", "test_tlmm_migration.py",
               output="--out", result="file", verdict="all_passed", count=15)
        device("pdc-migration", "test_pdc_migration.py",
               output="--out", result="file", verdict="all_passed", count=10)

    if args.suite in ("all", "linux"):
        kernel = str(args.kernel.resolve()) if args.kernel else "@KERNEL@"
        add("linux-console-userspace", scripts / "boot_smoke.py",
            ("--qemu", qemu, "--kernel", kernel, "--timeout", "120"),
            output="--out", result="file", count=8,
            count_field="guest_checks",
            expected={"arch_counter_matches_native_frequency_and_progress": True,
                      "guest_shutdown_observed": True, "validation_errors": []})
        add("linux-console-empty-ufs", scripts / "boot_smoke.py",
            ("--qemu", qemu, "--kernel", kernel, "--timeout", "120",
             "--without-ufs-lu", "--debug-mmio"),
            output="--out", result="file", count=8,
            count_field="guest_checks",
            expected={"arch_counter_matches_native_frequency_and_progress": True,
                      "guest_shutdown_observed": True, "validation_errors": [],
                      "ufs_fixture": {"populated": False, "kind": "none", "bytes": 0}})
        add("linux-ufs-smmu", scripts / "boot_ufs.py",
            ("--qemu", qemu, "--kernel", kernel, "--timeout", "120"),
            output="--out", result="file", count=19,
            count_field="guest_checks",
            expected={"host_verified_disk_payload": True})
        add("linux-ufs-rpmh", scripts / "boot_ufs.py",
            ("--qemu", qemu, "--kernel", kernel, "--timeout", "120",
             "--machine", "tb371fc,rpmh-profile=reference", "--require-rpmh"),
            output="--out", result="file", count=23,
            count_field="guest_checks",
            expected={"host_verified_disk_payload": True, "require_rpmh": True,
                      "expected_guest_checks": 23})
    return cases


def preserve_fixture(source, destination):
    """Copy regular artifacts, never sockets, symlinks or outside targets."""
    destination.mkdir()
    for current, directories, files in os.walk(source, followlinks=False):
        current = Path(current)
        directories[:] = sorted(
            name for name in directories if not (current / name).is_symlink())
        relative = current.relative_to(source)
        target = destination / relative
        target.mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            item = current / name
            if item.is_file() and not item.is_symlink():
                shutil.copy2(item, target / name)


def expected_json_value(actual, expected):
    """Keep booleans distinct from integers, including inside fixture metadata."""
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        return (actual.keys() == expected.keys() and
                all(expected_json_value(actual[k], v)
                    for k, v in expected.items()))
    if isinstance(expected, list):
        return (len(actual) == len(expected) and
                all(expected_json_value(a, b)
                    for a, b in zip(actual, expected)))
    return actual == expected


def checked_verdict_fields(record, location):
    """A true aggregate cannot conceal a contradictory local verdict."""
    present = False
    for field in ("passed", "success", "all_passed"):
        if field in record:
            present = True
            if record[field] is not True:
                raise ValueError(f"Contradictory {location}.{field}")
    return present


def check_identity(check, location, guest=False):
    """Validate the three receipt formats produced by component/guest tests."""
    if guest and isinstance(check, str):
        match = re.fullmatch(r"TB371FC_CHECK PASS ([A-Za-z0-9_]+)", check)
        if match:
            return match[1]
        raise ValueError(f"Unsuccessful or malformed {location}")
    if guest and isinstance(check, list):
        if (len(check) == 2 and check[0] == "PASS" and
                isinstance(check[1], str) and
                re.fullmatch(r"[A-Za-z0-9_]+", check[1])):
            return check[1]
        raise ValueError(f"Unsuccessful or malformed {location}")
    if not isinstance(check, dict):
        raise ValueError(f"{location} is not an attested check object")
    verdict = checked_verdict_fields(check, location)
    if "status" in check:
        if (not isinstance(check["status"], str) or
                check["status"].lower() not in ("pass", "passed", "ok")):
            raise ValueError(f"Unsuccessful {location}.status")
        verdict = True
    if not verdict:
        raise ValueError(f"{location} has no positive check verdict")
    names = [check[field] for field in ("name", "test", "check") if field in check]
    if not names or any(not isinstance(name, str) or not name.strip()
                        for name in names):
        raise ValueError(f"{location} has no valid check identity")
    if len(set(names)) != 1:
        raise ValueError(f"{location} has conflicting check identities")
    return names[0]


def judge(case, stdout):
    if case.result == "exit":
        return {"verdict": "successful test process"}
    if case.result == "tap":
        text = stdout.read_text(errors="replace")
        passed = re.findall(r"^ok\s+[0-9]+\b.*$", text, re.MULTILINE)
        bad = re.search(r"^(not ok\b|Bail out!)", text, re.MULTILINE)
        skipped = any(re.search(r"#\s*(?:SKIP|TODO)\b", line, re.IGNORECASE)
                      for line in passed)
        identifiers = [int(re.match(r"ok\s+([0-9]+)", line)[1])
                       for line in passed]
        plans = re.findall(r"^1\.\.([0-9]+)(?:\s*#.*)?$", text, re.MULTILINE)
        valid_plan = (plans == [str(case.tap_count)] and
                      identifiers == list(range(1, case.tap_count + 1)))
        if bad or skipped or not valid_plan or len(passed) != case.tap_count:
            raise ValueError(
                f"TAP required plan 1..{case.tap_count} and unique ordered "
                f"passes with no skips/TODO; got {len(passed)}, "
                f"skipped={skipped}, failed={bool(bad)}, valid_plan={valid_plan}")
        return {"tap_passed": len(passed), "tap_skipped": False}
    path = stdout if case.result == "stdout" else Path(case.result)
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or data.get(case.verdict) is not True:
        raise ValueError(f"Missing true JSON verdict {case.verdict!r}: {path}")
    checked_verdict_fields(data, "summary")
    details = {"verdict_field": case.verdict, "verdict": True}
    if case.expected_count is not None:
        checks = data.get(case.count_field)
        if not isinstance(checks, list) or len(checks) != case.expected_count:
            raise ValueError(
                f"{case.count_field} must contain {case.expected_count} entries")
        identities = [check_identity(check, f"{case.count_field}[{index}]",
                                     guest=case.count_field == "guest_checks")
                      for index, check in enumerate(checks)]
        if len(set(identities)) != len(identities):
            raise ValueError(f"Duplicate check identities in {case.count_field}")
        details["check_count"] = len(checks)
    for key, count in case.expected_lengths.items():
        value = data.get(key)
        if not isinstance(value, list) or len(value) != count:
            raise ValueError(f"{key} must contain {count} entries")
        details.setdefault("verified_lengths", {})[key] = len(value)
    for key, value in case.expected_values.items():
        if not expected_json_value(data.get(key), value):
            raise ValueError(f"Expected {key}={value!r}; got {data.get(key)!r}")
    return details


def run_case(case, destination, qemu):
    destination.mkdir()
    case.fixture.mkdir(parents=True)
    temporary = case.fixture / "tmp"
    temporary.mkdir()
    env = os.environ.copy()
    env.update(QTEST_QEMU_BINARY=str(qemu.resolve()), TMPDIR=str(temporary),
               PYTHONDONTWRITEBYTECODE="1")
    env.pop("PYTHONOPTIMIZE", None)
    stdout, stderr = destination / "stdout.log", destination / "stderr.log"
    report = {
        "name": case.name, "command": case.command, "started_utc": utc(),
        "timeout_seconds": case.timeout, "fixture": str(case.fixture),
        "environment": {key: env[key] for key in
                        ("QTEST_QEMU_BINARY", "TMPDIR", "PYTHONDONTWRITEBYTECODE")},
        "passed": False,
    }
    started = time.monotonic()
    proc = None
    try:
        with stdout.open("wb") as out, stderr.open("wb") as err:
            proc = subprocess.Popen(case.command, cwd=ROOT, env=env,
                                    stdout=out, stderr=err, start_new_session=True)
            try:
                proc.wait(timeout=case.timeout)
            except subprocess.TimeoutExpired:
                report["timed_out"] = True
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=3)
            report["exit_code"] = proc.returncode
        if proc.returncode != 0 or report.get("timed_out"):
            raise RuntimeError(f"Gate exited {proc.returncode}")
        report["verified"] = judge(case, stdout)
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if proc:
            # Terminate any descendants left behind by an interrupted runner.
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            preserve_fixture(case.fixture, destination / "fixtures")
        except Exception:
            report["passed"] = False
            report["artifact_error"] = traceback.format_exc()
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["completed_utc"] = utc()
    report["log_sha256"] = {p.name: sha(p) for p in (stdout, stderr) if p.exists()}
    (destination / "case.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--kernel", type=Path,
                        help="Public unpatched Image from build_smoke_kernel.sh")
    parser.add_argument("--suite", choices=("all", "components", "linux"),
                        default="all")
    parser.add_argument("--list", action="store_true",
                        help="Print the plan without executing gates")
    args = parser.parse_args()
    if not __debug__ or sys.version_info < (3, 11):
        parser.error("Use Python 3.11+ without -O")
    if args.list:
        for index, case in enumerate(plan(args, Path("/tmp/TB371FC-CI"))):
            print(f"{index:02d} {case.name}: " + " ".join(case.command))
        return 0
    if not args.qemu.is_file() or not args.build_dir.is_dir():
        parser.error("--qemu and --build-dir must identify an existing build")
    if args.suite in ("all", "linux") and (
            not args.kernel or not args.kernel.is_file()):
        parser.error("--kernel is required for the all/linux suite")
    kernel_receipt = None
    if args.suite in ("all", "linux"):
        receipt_path = args.kernel.parent / "build.json"
        try:
            kernel_receipt = json.loads(receipt_path.read_text())
            valid = (
                kernel_receipt["source_version"] == "6.12.114" and
                kernel_receipt["source_tar_sha256"] ==
                "7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3" and
                kernel_receipt["patches_applied_by_script"] == [] and
                kernel_receipt["build_completed"] is True and
                kernel_receipt["image_sha256"] == sha(args.kernel))
        except (OSError, ValueError, KeyError):
            valid = False
        if not valid:
            parser.error("--kernel requires the matching public build_smoke_kernel.sh build.json")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    runtime = Path(tempfile.mkdtemp(prefix="tbci-", dir="/tmp"))
    cases = plan(args, runtime)
    source = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    report = {
        "schema_version": 1, "suite": args.suite, "started_utc": utc(),
        "scope": "Public digital component and clean-Linux gates; no stock Android attestation",
        "private_inputs_used": False, "source_commit": source,
        "source_dirty": bool(subprocess.check_output(
            ["git", "status", "--porcelain"], cwd=ROOT, text=True)),
        "runner_sha256": sha(__file__), "qemu_sha256": sha(args.qemu),
        "qemu_version": subprocess.check_output(
            [str(args.qemu.resolve()), "--version"], text=True).strip(),
        "python": sys.version, "platform": platform.platform(),
        "runtime_fixtures_retained": str(runtime), "expected_gates": len(cases),
        "gates": [], "passed": False,
    }
    if args.kernel:
        report["kernel_sha256"] = sha(args.kernel)
        report["kernel_build_receipt"] = kernel_receipt
    report["c_test_binary_sha256"] = {
        Path(c.command[0]).name: sha(c.command[0])
        for c in cases if c.result == "tap" and Path(c.command[0]).is_file()
    }
    (out / "plan.json").write_text(json.dumps([
        {"name": c.name, "command": c.command, "result": c.result,
         "expected_count": c.expected_count, "tap_count": c.tap_count,
         "verdict": c.verdict, "count_field": c.count_field,
         "expected_values": c.expected_values,
         "expected_lengths": c.expected_lengths}
        for c in cases], indent=2) + "\n")
    started = time.monotonic()
    for index, case in enumerate(cases):
        print(f"[{index + 1}/{len(cases)}] {case.name}", flush=True)
        item = run_case(case, out / f"{index:02d}-{case.name}", args.qemu)
        report["gates"].append(item)
        print("PASS" if item["passed"] else "FAIL", case.name, flush=True)
        (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    report["passed"] = (len(report["gates"]) == len(cases) and
                        all(g["passed"] for g in report["gates"]))
    report["passed_gates"] = sum(g["passed"] for g in report["gates"])
    report["completed_utc"] = utc()
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    report["qemu_unchanged"] = sha(args.qemu) == report["qemu_sha256"]
    report["passed"] = report["passed"] and report["qemu_unchanged"]
    (out / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    (out / "sha256.json").write_text(json.dumps({
        str(p.relative_to(out)): sha(p) for p in sorted(out.rglob("*"))
        if p.is_file() and p != out / "sha256.json"
    }, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in
                      ("passed", "passed_gates", "expected_gates",
                       "elapsed_seconds", "qemu_unchanged")}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
