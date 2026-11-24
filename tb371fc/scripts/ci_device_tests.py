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
    guest_errno: bool = False


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
            expected_lengths=None, guest_errno=False):
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
                          expected_lengths=expected_lengths or {},
                          guest_errno=guest_errno))
        return data

    def device(name, script, *flags, **kwargs):
        return add(name, tests / script, ("--qemu", qemu, *flags), **kwargs)

    if args.suite in ("all", "components"):
        for name, script in (
            ("ci-verdict-unit", scripts / "test_ci_device_tests.py"),
            ("smem-observer-unit", scripts / "test_observe_smem.py"),
            ("aop-observer-unit", scripts / "test_observe_aop.py"),
            ("board-profile-unit", scripts / "test_board_profile.py"),
            ("machine-validator-unit", scripts / "test_validate_machine.py"),
            ("dt-inventory-unit", scripts / "test_inventory_dtb.py"),
            ("hardware-sysfs-unit", scripts / "test_collect_hardware_sysfs.py"),
            ("guest-result-unit", tests / "test_guest_result.py"),
            ("console-boot-verdict-unit", tests / "test_boot_verdict.py"),
            ("tlmm-map-unit", tests / "test_tlmm_map.py"),
        ):
            add(name, script, result="exit")
        add("gcc-source-generation", scripts / "verify_gcc_generation.py",
            output="--out", result="file", count=2,
            expected={"source_pins_verified": True})
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
        device("gcc-cpuss-branch", "test_gcc_cpuss.py",
               output="--out", result="file", count=11,
               expected={"expected_checks": 11, "vmstate_version": 3,
                         "external_parent_inputs": 83,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False},
               expected_lengths={"migrations": 2})
        device("gcc-usb-pipe-mux", "test_gcc_usb_mux.py",
               output="--out", result="file", count=15,
               expected={"expected_checks": 15, "vmstate_version": 3,
                         "external_parent_inputs": 83,
                         "expected_completed_migrations": 1,
                         "expected_rejected_migrations": 1,
                         "completed_migrations": 1,
                         "rejected_migration_attempts": 1,
                         "binary_unchanged": True,
                         "guest_cpu_executed": False,
                         "usb_phy_emulated": False,
                         "native_kernel_probe_observed": False},
               expected_lengths={"migrations": 1, "rejected_migrations": 1})
        device("geni-clock-reset", "test_geni_clock_reset.py",
               output="--work-dir", verdict="success", count=6)
        device("geni-clock-migration", "test_geni_clock_reset.py",
               "--migration-only", output="--work-dir", verdict="success", count=4)
        device("mpm2-sleep-counter", "test_mpm2_sleep_counter.py",
               output="--out", result="file", count=17,
               expected={"expected_checks": 17,
                         "expected_completed_migrations": 2,
                         "expected_corrupted_stream_rejections": 1,
                         "vmstate_version": 1,
                         "clock_frequency_hz": 32768,
                         "mmio_register_count": 1, "mmio_window_bytes": 4,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2,
                                 "corrupted_stream_rejections": 1})
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
        device("tbu-reference-ecats", "test_tbu_sm8250.py",
               output="--out", result="file", verdict="all_passed", count=17,
               expected_lengths={"migrations": 1, "rejections": 6})
        device("rpmh-reference", "test_rpmh_sm8250.py",
               output="--work-dir", verdict="success", count=22)
        device("rpmh-reference-migration", "test_rpmh_sm8250.py",
               "--migration-only", output="--work-dir", verdict="success", count=5)
        device("rpmh-display", "test_rpmh_display.py",
               output="--work-dir", verdict="success", count=11)
        device("rpmh-display-migration", "test_rpmh_display.py",
               "--migration-only", output="--work-dir", verdict="success", count=6)
        device("rpmh-wait-mask-reference", "test_rpmh_wait_mask.py",
               output="--out", result="file", verdict="success", count=17,
               expected={'schema': 'tb371fc-rpmh-wait-mask-v1',
                         'success': True,
                         'expected_checks': 17,
                         'expected_completed_migrations': 1,
                         'expected_rejected_migrations': 2,
                         'expected_corrupted_stream_rejections': 2,
                         'vmstate_version': 3,
                         'vmstate_minimum_version': 2,
                         'subsection_version': 1,
                         'subsection_payload_bytes': 24,
                         'main_payload_bytes_with_default_clock': 19424,
                         'commands_per_tcs': 16,
                         'voter_count': 4,
                         'apps_tcs_per_drv': 8,
                         'display_tcs_count': 2,
                         'wait_command_mask': 65535,
                         'strict_and_wait_reference_main_v3_payload_identical': True,
                         'old_rpmh_tests_changed': False,
                         'old_rpmh_test_blobs_match': True,
                         'silicon_reserved_bit_semantics_proved': False,
                         'guest_cpu_executed': False,
                         'native_kernel_probe_observed': False,
                         'guest_image_patched': False,
                         'android_boot_passed': False,
                         'binary_unchanged': True,
                         'diagnostic_counters_are_model_observations': True,
                         'reference_policy_auto_enabled': False},
               expected_lengths={"migrations": 1, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2, "source_streams": 3})
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
        device("gpucc-sm8250", "test_gpucc_sm8250.py",
               output="--out", result="file", count=30,
               expected={"expected_checks": 30,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "vmstate_version": 1, "register_count": 36,
                         "sparse_window_count": 7, "branch_count": 15,
                         "reset_count": 5, "input_clock_count": 15,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "gpu_execution_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2})
        device("npucc-sm8250", "test_npucc_sm8250.py",
               output="--out", result="file", count=37,
               expected={"expected_checks": 37,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "vmstate_version": 1, "register_count": 95,
                         "sparse_window_count": 26, "branch_count": 33,
                         "reset_count": 7, "input_clock_count": 12,
                         "pll_count": 3, "rcg_count": 6,
                         "output_clock_count": 45,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "npu_execution_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2})
        device("noc-qos", "test_noc_qos.py",
               output="--out", result="file", count=20,
               expected={"expected_checks": 20,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "vmstate_version": 1, "port_count": 47,
                         "register_count": 188, "sparse_window_count": 188,
                         "mapped_bytes": 752,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
        device("noc-sbm-reference", "test_noc_sbm.py",
               output="--out", result="file", count=28,
               expected={"schema": "tb371fc-noc-sbm-v1",
                         "expected_checks": 28,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes_per_device": 152,
                         "device_count": 1, "port_count": 8,
                         "register_count": 3, "sparse_window_count": 3,
                         "mapped_bytes": 12,
                         "reference_latency_ns": 1000,
                         "supported_port_mask": 255,
                         "reference_boot_disconnected_mask": 0,
                         "reference_profile_is_default": False,
                         "default_profile_leaves_mmio_unmapped": True,
                         "diagnostic_counters_are_hardware": False,
                         "hardware_latency_measured": False,
                         "traffic_blocking_implemented": False,
                         "traffic_drain_observed": False,
                         "clock_control_implemented": False,
                         "irq_implemented": False,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
        device("smmuv500-reference", "test_smmuv500.py",
               output="--out", result="file", count=36,
               expected={"expected_checks": 36,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "vmstate_version": 1,
                         "architectural_smmuv2_vmstate_version": 3,
                         "tcu_count": 1, "tbu_count": 10,
                         "register_count": 71, "sparse_window_count": 71,
                         "mapped_bytes": 484,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
        device("tsens24xx-reference", "test_tsens24xx.py",
               output="--out", result="file", count=34,
               expected={"expected_checks": 34,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes_per_controller": 375,
                         "controller_count": 2, "sensor_count": 32,
                         "sensors_per_controller": 16,
                         "register_count": 114, "sparse_window_count": 6,
                         "mapped_bytes": 456, "irq_count": 4,
                         "sampling_period_ns": 1000000,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
        device("cpufreq-hw-reference", "test_cpufreq_hw.py",
               output="--out", result="file", count=30,
               expected={"schema": "tb371fc-cpufreq-hw-v1",
                         "expected_checks": 30,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "domain_count": 3, "cpu_count": 8,
                         "per_cpu_counter_count": 8,
                         "register_count": 263, "sparse_window_count": 24,
                         "mapped_bytes": 1052,
                         "registers_per_domain": [89, 88, 86],
                         "windows_per_domain": 8, "lut_rows_per_domain": 40,
                         "lut_row_bytes": 4, "valid_perf_states_per_domain": 4,
                         "irq_count": 3, "input_clock_count": 6,
                         "output_clock_count": 3,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes_per_domain": 84,
                         "reference_profile_is_default": False,
                         "reference_lut_is_synthetic": True,
                         "factory_lut_observed": False,
                         "counter_source_is_virtual_time": True,
                         "counter_measures_retired_cpu_cycles": False,
                         "cpu_execution_speed_controlled": False,
                         "analogue_voltage_control_implemented": False,
                         "hardware_thermal_throttling_observed": False,
                         "reference_lut_lvals": [16, 32, 64, 96],
                         "reference_lut_voltage_words": [750, 800, 900, 1000],
                         "reference_max_cores": [4, 4, 4],
                         "actual_domain_cpu_counts": [4, 3, 1],
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
        device("devfreq-qcom-fw-reference", "test_devfreq_qcom_fw.py",
               output="--out", result="file", count=20,
               expected={"schema": "tb371fc-devfreq-qcom-fw-v1",
                         "expected_checks": 20,
                         "expected_completed_migrations": 2,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes_per_device": 72,
                         "device_count": 1, "register_count": 42,
                         "sparse_window_count": 3, "mapped_bytes": 168,
                         "lut_rows": 40, "lut_row_bytes": 4,
                         "valid_perf_states": 4, "software_voter_count": 4,
                         "reference_nominal_xo_hz": 19200000,
                         "reference_profile_is_default": False,
                         "reference_lut_is_synthetic": True,
                         "factory_lut_observed": False,
                         "hardware_voter_slots_implemented": False,
                         "clock_control_implemented": False,
                         "actual_cache_frequency_measured": False,
                         "irq_implemented": False, "timer_implemented": False,
                         "counter_implemented": False,
                         "voltage_control_implemented": False,
                         "firmware_execution_observed": False,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "android_boot_passed": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 2, "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2})
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
        device("camera-gdsc-reference", "test_camera_gdsc.py",
               output="--out", result="file", count=29,
               expected={'schema': 'tb371fc-camera-gdsc-v1',
                         'expected_checks': 29,
                         'expected_completed_migrations': 2,
                         'expected_rejected_migrations': 2,
                         'expected_corrupted_stream_rejections': 2,
                         'expected_unmodified_streams': 3,
                         'expected_stream_files': 5,
                         'vmstate_version': 1,
                         'vmstate_payload_bytes': 194,
                         'domain_count': 6,
                         'register_count': 6,
                         'sparse_window_count': 6,
                         'mapped_bytes': 24,
                         'gpio_output_count': 12,
                         'parent_resource_index': 3,
                         'reference_identity': 1128743985,
                         'hardware_mode_domain_mask': 3,
                         'modeled_timer_count': 0,
                         'modeled_clock_count': 0,
                         'gcc_vmstate_version': 3,
                         'gcc_camera_ahb_clock_id': 10,
                         'gcc_camera_ahb_write_mask': 24577,
                         'guest_cpu_executed': False,
                         'native_kernel_probe_observed': False,
                         'camera_execution_observed': False,
                         'analog_power_modeled': False,
                         'retention_effect_modeled': False,
                         'android_boot_passed': False,
                         'binary_unchanged': True},
               expected_lengths={'migrations': 2,
                                 'rejected_migrations': 2,
                                 'corrupted_stream_rejections': 2,
                                 'migration_sources': 3})
        device("mdss-gdsc-reference", "test_mdss_gdsc.py",
               output="--out", result="file", count=20,
               expected={"schema": "tb371fc-mdss-gdsc-v1",
                         "expected_checks": 20,
                         "expected_completed_migrations": 3,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "expected_unmodified_streams": 4,
                         "expected_stream_files": 6,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes": 64,
                         "domain_count": 1,
                         "register_count": 1,
                         "sparse_window_count": 1,
                         "mapped_bytes": 4,
                         "gpio_output_count": 2,
                         "parent_resource_index": 3,
                         "reference_identity": 0x4d445331,
                         "camera_vmstate_version": 1,
                         "camera_vmstate_payload_bytes": 194,
                         "modeled_timer_count": 0,
                         "modeled_clock_count": 0,
                         "gcc_vmstate_version": 3,
                         "gcc_display_ahb_clock_id": 23,
                         "gcc_display_ahb_write_mask": 0x6001,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "display_execution_observed": False,
                         "analog_power_modeled": False,
                         "retention_effect_modeled": False,
                         "android_boot_passed": False,
                         "parent_output_intercepted": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 3,
                                 "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2,
                                 "migration_sources": 4,
                                 "actual_stream_files": 6})
        device("npu-gdsc-reference", "test_npu_gdsc.py",
               output="--out", result="file", count=21,
               expected={"schema": "tb371fc-npu-gdsc-v1",
                         "expected_checks": 21,
                         "expected_completed_migrations": 3,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "expected_unmodified_streams": 4,
                         "expected_stream_files": 6,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes": 64,
                         "domain_count": 1,
                         "register_count": 1,
                         "sparse_window_count": 1,
                         "mapped_bytes": 4,
                         "gpio_output_count": 2,
                         "parent_resource_index": 0xffffffff,
                         "parent_supply_declared": False,
                         "parent_power_connected": False,
                         "hardware_mode_domain_mask": 0,
                         "domain_dt_flags": 1,
                         "reference_identity": 0x4e504731,
                         "camera_vmstate_version": 1,
                         "camera_vmstate_payload_bytes": 194,
                         "mdss_vmstate_version": 1,
                         "mdss_vmstate_payload_bytes": 64,
                         "modeled_timer_count": 0,
                         "modeled_clock_count": 0,
                         "gcc_vmstate_version": 3,
                         "gcc_npu_cfg_ahb_clock_id": 42,
                         "gcc_npu_cfg_ahb_write_mask": 0x6003,
                         "gcc_npu_cfg_ahb_external_parent_index": 21,
                         "gcc_npu_cfg_ahb_status_mask": 0xf0000000,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "npu_execution_observed": False,
                         "analog_power_modeled": False,
                         "retention_effect_modeled": False,
                         "android_boot_passed": False,
                         "parent_output_intercepted": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 3,
                                 "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2,
                                 "migration_sources": 4,
                                 "actual_stream_files": 6})
        device("video-gdsc-reference", "test_video_gdsc.py",
               output="--out", result="file", count=22,
               expected={"schema": "tb371fc-video-gdsc-v1",
                         "expected_checks": 22,
                         "expected_completed_migrations": 3,
                         "expected_rejected_migrations": 2,
                         "expected_corrupted_stream_rejections": 2,
                         "expected_unmodified_streams": 4,
                         "expected_stream_files": 6,
                         "vmstate_version": 1,
                         "vmstate_payload_bytes": 142,
                         "domain_count": 4,
                         "register_count": 4,
                         "sparse_window_count": 4,
                         "mapped_bytes": 16,
                         "gpio_output_count": 8,
                         "parent_resource_index": 3,
                         "parent_supply_declared": True,
                         "parent_power_connected": True,
                         "fanout_consumer_count": 3,
                         "npu_parent_power_connected": False,
                         "hardware_mode_domain_mask": 12,
                         "domain_dt_flags": [1, 1, 3, 3],
                         "reference_identity": 0x56444731,
                         "camera_vmstate_version": 1,
                         "camera_vmstate_payload_bytes": 194,
                         "mdss_vmstate_version": 1,
                         "mdss_vmstate_payload_bytes": 64,
                         "npu_vmstate_version": 1,
                         "npu_vmstate_payload_bytes": 64,
                         "modeled_timer_count": 0,
                         "modeled_clock_count": 0,
                         "gcc_vmstate_version": 3,
                         "gcc_video_ahb_clock_id": 196,
                         "gcc_video_ahb_write_mask": 0x6001,
                         "gcc_video_ahb_external_parent_index": 79,
                         "gcc_video_ahb_status_mask": 0,
                         "guest_cpu_executed": False,
                         "native_kernel_probe_observed": False,
                         "video_execution_observed": False,
                         "analog_power_modeled": False,
                         "retention_effect_modeled": False,
                         "android_boot_passed": False,
                         "parent_output_intercepted": False,
                         "binary_unchanged": True},
               expected_lengths={"migrations": 3,
                                 "rejected_migrations": 2,
                                 "corrupted_stream_rejections": 2,
                                 "migration_sources": 4,
                                 "actual_stream_files": 6})
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
        device("ufs-crypto-rejection", "test_ufs_crypto.py",
               output="--out", result="file", count=14,
               expected={"qemu_unchanged": True, "private_inputs_used": False})
        device("ufs-auto-hibern8", "test_ufs_autohibern8.py",
               output="--out", result="file", verdict="all_passed", count=18,
               expected_lengths={"migrations": 2, "rejections": 3})
        device("power-checkpoint", "test_power_checkpoint.py",
               output="--out", result="file", verdict="all_passed", count=7,
               expected={"expected_checks": 7, "qemu_unchanged": True})
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
        device("smmuv2-migration", "test_smmuv2_migration.py",
               output="--out", result="file", verdict="all_passed", count=18,
               expected={"schema_version": 1,
                         "kind": "tb371fc-smmuv2-process-migration",
                         "geometry_only": False},
               expected_lengths={"migrations": 4})
        device("smmuv2-granules", "test_smmuv2_granules.py",
               output="--out", result="file", verdict="all_passed", count=26,
               expected={"expected_checks": 26, "legacy_v2_transfer_tested": False},
               expected_lengths={"migrations": 3})

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
        add("linux-ufs-runtime-pm", scripts / "boot_ufs_runtime_pm.py",
            ("--qemu", qemu, "--kernel", kernel, "--timeout", "120"),
            output="--out", result="file", count=40,
            count_field="guest_checks", guest_errno=True,
            expected={"inputs_unchanged": True, "qemu_exit_code": 0,
                      "kernel_failure_lines": [],
                      "guest_verdicts": [["PASS", "40", "0"]],
                      "host_sleep_checks": {
                          "vm_remains_running": True,
                          "ufs_link_hibern8": True,
                          "core_clock_gated": True,
                          "iface_clock_gated": True,
                          "unipro_clock_gated": True},
                      "system_suspend_tested": False,
                      "inflight_checkpoint_tested": False},
            expected_lengths={"host_observations": 3})
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


def check_identity(check, location, guest=False, guest_errno=False):
    """Validate the three receipt formats produced by component/guest tests."""
    if guest and isinstance(check, str):
        match = re.fullmatch(r"TB371FC_CHECK PASS ([A-Za-z0-9_]+)", check)
        if match:
            return match[1]
        raise ValueError(f"Unsuccessful or malformed {location}")
    if guest and isinstance(check, list):
        # Only the runtime-PM fixture emits an explicit errno observation.
        # errno is diagnostic (a successful syscall need not clear it).
        valid_shape = (len(check) == 3 and isinstance(check[2], str) and
                       re.fullmatch(r"[0-9]+", check[2])) if guest_errno else len(check) == 2
        if (valid_shape and check[0] == "PASS" and
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
                                     guest=case.count_field == "guest_checks",
                                     guest_errno=case.guest_errno)
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
