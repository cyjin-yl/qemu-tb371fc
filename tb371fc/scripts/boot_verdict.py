#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict, side-effect-free validation of the actual guest boot records."""
from __future__ import annotations

import json

CHECKS = ("procfs", "sysfs", "aarch64", "eight_online_cpus_psci",
          "timer_and_scheduler_progress", "fork_and_wait", "board_model",
          "arch_counter_probe")


def validate_counter_probe(probe, native):
    """Validate the entire native-probe contract, including integer types."""
    if not isinstance(probe, dict):
        raise ValueError("probe JSON is not an object")
    expected = {"schema_version": 1, "loop_iterations": 65536,
                "counter_frequency_hz": native["probe_result"]["counter_frequency_hz"]}
    for field, value in expected.items():
        if type(probe.get(field)) is not int or probe[field] != value:
            raise ValueError(f"wrong probe {field}")
    if probe.get("kind") != "tb371fc-el0-counter-probe":
        raise ValueError("wrong probe kind")
    for field in ("counter_start", "counter_end", "counter_delta"):
        if type(probe.get(field)) is not int or not 0 <= probe[field] < 1 << 64:
            raise ValueError(f"invalid unsigned counter {field}")
    if probe.get("increased") is not True or probe["counter_delta"] <= 0:
        raise ValueError("counter did not increase")
    if probe["counter_end"] - probe["counter_start"] != probe["counter_delta"]:
        raise ValueError("counter delta inconsistent with endpoints")


def validate_console(text, native):
    """Reject duplicate, partial, failed or contradictory console evidence."""
    lines = text.splitlines()
    errors = []
    checks = [line for line in lines if line.startswith("TB371FC_CHECK")]
    expected = {f"TB371FC_CHECK PASS {name}" for name in CHECKS}
    if len(checks) != len(CHECKS) or set(checks) != expected:
        errors.append("guest checks are not the eight unique successful checks")
    if lines.count("TB371FC_GUEST_BEGIN") != 1:
        errors.append("missing or repeated guest start record")
    verdicts = [line for line in lines if line.startswith("TB371FC_GUEST_RESULT")]
    if verdicts != ["TB371FC_GUEST_RESULT PASS failures=0"]:
        errors.append("missing, repeated or unsuccessful guest result")
    if any(marker in text for marker in
           ("Kernel panic", "Internal error:", "Oops:", "BUG:")):
        errors.append("kernel fatal diagnostic in console output")
    probes = []
    for line in lines:
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(record, dict) and record.get("kind") == "tb371fc-el0-counter-probe":
            probes.append(record)
    probe_errors = []
    if len(probes) != 1:
        probe_errors.append("expected exactly one counter probe record")
    else:
        try:
            validate_counter_probe(probes[0], native)
        except (ValueError, TypeError, KeyError) as exc:
            probe_errors.append(str(exc))
    errors.extend(f"counter probe: {error}" for error in probe_errors)
    return {"guest_checks": checks, "arch_counter_probe": probes,
            "arch_counter_matches_native_frequency_and_progress": not probe_errors,
            "validation_errors": errors}
