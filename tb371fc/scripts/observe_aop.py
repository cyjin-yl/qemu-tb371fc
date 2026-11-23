#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read stopped-QEMU reference AOP state without writing guest memory.

This observes the explicit reference transport/service. It neither executes
AOP firmware nor establishes Android boot or a particular guest driver's
return path.
"""

PEER_PATH = "/machine/aop-peer"
PROFILE = "reference-qdss-vote"
STATE_VALUES = {"link-down", "link-up", "channel-opening", "connected"}
ERROR_VALUES = {"none", "descriptor", "link", "channel", "length", "command"}
COUNT_FIELDS = ("accepted-messages", "rejected-requests", "notifications")
BOOL_FIELDS = ("qdss-vote", "qdss-ao-vote", "work-pending",
               "notification-pending")
FIELDS = ("state", "accepted-messages", "rejected-requests", "last-error",
          "qdss-vote", "qdss-ao-vote", "notifications", "work-pending",
          "notification-pending")
STOPPED_STATES = {"paused", "prelaunch", "shutdown", "suspended",
                  "guest-panicked", "internal-error", "io-error",
                  "watchdog", "debug"}


def reference_profile_selected(machine):
    """Require exactly one explicit option, not a substring or shadowed value."""
    if not isinstance(machine, str):
        return False
    fields = machine.split(",")
    if fields[0] != "tb371fc":
        return False
    profiles = [value for field in fields[1:]
                for key, separator, value in [field.partition("=")]
                if separator and key == "aop-profile"]
    return profiles == [PROFILE]


def initial_observation(machine):
    return {
        "schema_version": 1,
        "scope": "Stopped reference AOP peer state and supported-command consumption",
        "machine": machine,
        "qom_path": PEER_PATH,
        "reference_exchange_observed": False,
        "aop_firmware_executed": False,
        "android_boot_passed": False,
        "guest_memory_written": False,
        "verdict": "observation-inconclusive",
        "reason": "Guest did not reach a stopped observation point",
        "observations": {},
    }


def _require_stopped(status):
    if (not isinstance(status, dict) or status.get("running") is not False or
            status.get("status") not in STOPPED_STATES):
        raise ValueError("Guest is not in a stable stopped state")


def _validate_field(name, value):
    if name in COUNT_FIELDS:
        if type(value) is not int or not 0 <= value < 1 << 64:
            raise ValueError("Invalid unsigned counter: " + name)
    elif name in BOOL_FIELDS:
        if type(value) is not bool:
            raise ValueError("Invalid boolean field: " + name)
    elif name == "state":
        if type(value) is not str or value not in STATE_VALUES:
            raise ValueError("Invalid reference peer state")
    elif name == "last-error":
        if type(value) is not str or value not in ERROR_VALUES:
            raise ValueError("Invalid reference peer error state")
    else:
        raise ValueError("Unrecognized observer field: " + name)


def capture_aop_peer(command, machine):
    """Return observed/not-observed/inconclusive; never issue a mutating QMP."""
    report = initial_observation(machine)
    try:
        if not reference_profile_selected(machine):
            raise ValueError("Observer requires explicit aop-profile=reference-qdss-vote")
        before = command("query-status")
        report["stopped_status"] = before
        _require_stopped(before)
        values = report["observations"]
        for name in FIELDS:
            value = command("qom-get", {"path": PEER_PATH, "property": name})
            values[name] = value
            _validate_field(name, value)
        after = command("query-status")
        report["stopped_status_after"] = after
        _require_stopped(after)
        if before["status"] != after["status"]:
            raise ValueError("Guest stop state changed during observation")

        missing = []
        if values["state"] != "connected":
            missing.append("reference channel is not connected")
        if values["accepted-messages"] == 0:
            missing.append("no supported command was consumed")
        if values["notifications"] == 0:
            missing.append("no IPCC notification was inserted")
        if values["notification-pending"]:
            # Notifications also count the handshake. A positive total does
            # not prove the pending service completion reached IPCC.
            missing.append("a completion notification is still pending")
        report["unmet_conditions"] = missing
        report["reference_exchange_observed"] = not missing
        if missing:
            report["verdict"] = "reference-exchange-not-observed"
            report["reason"] = "; ".join(missing)
        else:
            report["verdict"] = "reference-exchange-observed"
            report["reason"] = (
                "Connected reference peer consumed a supported command; "
                "IPCC notifications exist and no completion notification is pending")
    except Exception as exc:
        report["reference_exchange_observed"] = False
        report["verdict"] = "observation-inconclusive"
        report["reason"] = "Stopped reference-peer evidence could not be verified"
        report["error"] = type(exc).__name__ + ": " + str(exc)
    return report
