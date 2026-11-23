def capture_camera_gdsc(command):
    """Take two stopped, read-only QOM snapshots of the six camera domains."""
    path = "/machine/camera-gdsc"
    identities = [
        ("bps", 0x0ad07004, 3, 100),
        ("ipe", 0x0ad08004, 3, 100),
        ("sbi", 0x0ad09004, 1, 100),
        ("ife0", 0x0ad0a004, 1, 100),
        ("ife1", 0x0ad0b004, 1, 100),
        ("titan-top", 0x0ad0c144, 1, 500),
    ]
    immutable = {
        "reference-identity": 0x43474431,
        "domain-count": 6,
        "register-bytes": 4,
        "mapped-bytes": 24,
        "parent-resource-index": 3,
        "cold-control": 1,
        "control-write-mask": 0xf803,
        "power-status-mask": 0x80000000,
    }
    fields = {"enabled-reference": "bool", "enabled-profile": "uint32"}
    fields.update({key: "uint32" for key in immutable})
    fields.update({"parent-known": "bool", "parent-on": "bool"})
    domain_fields = {
        "name": "str", "address": "uint64", "dt-flags": "uint32",
        "timeout-us": "uint32", "control": "uint32",
        "request-known": "bool", "requested": "bool",
        "state-known": "bool", "powered": "bool",
        "hw-request-known": "bool", "hw-request": "bool",
    }
    for index in range(6):
        fields.update({"domain%d.%s" % (index, key): kind
                       for key, kind in domain_fields.items()})
    report = {
        "schema": "tb371fc-camera-gdsc-stopped-observer-v1",
        "scope": "Six camera GDSC digital reference states and their shared RPMh parent output",
        "path": path,
        "actual_dt_capture_sha256": "6dbe11407254a358648375ec13bb07baf5c7cee06f6709e835d9e2908bdbf0c1",
        "actual_parent_dt_capture_sha256": "8a8bdded1c180c56a9029a3ef954c1817554b70e8cda52696bebb9075fb56ac2",
        "model_api_sha256": "05c104c8f37cc8349af650cdbab18f0b7dd051b993553bd3c218fac297b803ff",
        "guest_memory_written": False,
        "device_mmio_read": False,
        "device_property_written": False,
        "guest_cpu_executed_by_observer": False,
        "virtual_clock_advanced_by_observer": False,
        "power_transition_completed_by_observer": False,
        "native_driver_success_assessed": False,
        "native_mmio_usage_proved_by_snapshot": False,
        "camera_execution_assessed": False,
        "physical_power_or_retention_assessed": False,
        "userspace_success_assessed": False,
        "android_boot_passed": False,
        "observation_status": "inconclusive",
    }

    def stopped(status):
        return (type(status) is dict and status.get("running") is False
                and type(status.get("status")) is str and bool(status["status"]))

    def read_all():
        result = {}
        for key, kind in fields.items():
            value = command("qom-get", {"path": path, "property": key})
            if kind == "bool":
                valid = type(value) is bool
            elif kind == "str":
                valid = type(value) is str and 0 < len(value) <= 32
            else:
                width = 32 if kind == "uint32" else 64
                valid = type(value) is int and 0 <= value < (1 << width)
            if not valid:
                raise RuntimeError("Camera GDSC unexpected QOM type/range: " + key)
            result[key] = value
        parent = {}
        for key in ("present", "enabled"):
            value = command("qom-get", {"path": "/machine/rpmh",
                            "property": "res.mmcx.lvl." + key})
            if type(value) is not int or value not in (0, 1):
                raise RuntimeError("Camera GDSC invalid RPMh parent output: " + key)
            parent[key] = value
        return {"camera": result, "parent_resource": parent}

    before = command("query-status")
    report["stopped_status"] = before
    if not stopped(before):
        raise RuntimeError("Camera GDSC observation requires a stopped guest")
    try:
        first, second = read_all(), read_all()
        state, parent = first["camera"], first["parent_resource"]
        report["raw_state"] = first
        report["raw_state_after"] = second
        report["raw_state_stable_during_capture"] = first == second
        enabled = state["enabled-reference"]
        pknown, pon = state["parent-known"], state["parent-on"]
        checks = {
            "profile_matches_property": state["enabled-profile"] == int(enabled),
            "immutable_geometry_matches": all(state[key] == value
                                              for key, value in immutable.items()),
            "parent_present_matches_known_input": pknown == bool(parent["present"]),
            "parent_enabled_matches_on_input": pon == bool(parent["enabled"]),
        }
        domains = []
        for index, (name, address, flags, timeout) in enumerate(identities):
            domain = {key: state["domain%d.%s" % (index, key)]
                      for key in domain_fields}
            control = domain["control"]
            hwmode = bool(control & 2)
            reqknown = not hwmode or domain["hw-request-known"]
            requested = (domain["hw-request-known"] and domain["hw-request"]
                         if hwmode else not bool(control & 1))
            known = enabled and ((pknown and not pon) or
                                  (reqknown and not requested) or
                                  (pknown and reqknown))
            powered = enabled and pknown and pon and reqknown and requested
            domain_checks = {
                "identity_matches": (domain["name"], domain["address"],
                                      domain["dt-flags"], domain["timeout-us"]) ==
                                     (name, address, flags, timeout),
                "control_supported": (control & ~0xf803) == 0 and
                                     (not hwmode or bool(flags & 2)),
                "disabled_profile_remains_collapsed": enabled or control == 1,
                "request_knowledge_matches": domain["request-known"] == reqknown,
                "request_matches": domain["requested"] == requested,
                "power_knowledge_matches": domain["state-known"] == known,
                "power_matches": domain["powered"] == powered,
            }
            domains.append({"index": index, "observed": domain,
                            "reference_checks": domain_checks,
                            "reference_invariants_match": all(domain_checks.values())})
        checks["all_domain_invariants_match"] = all(
            domain["reference_invariants_match"] for domain in domains)
        report["domains"] = domains
        report["reference_state_checks"] = checks
        report["reference_state_invariants_match"] = all(checks.values())
        report["explicit_reference_policy_enabled"] = enabled
        report["powered_domain_indices"] = [x["index"] for x in domains
                                             if x["observed"]["powered"]]
        report["unknown_power_domain_indices"] = [x["index"] for x in domains
                                                   if not x["observed"]["state-known"]]
        report["camera_qom_properties_per_snapshot"] = len(fields)
        report["parent_qom_properties_per_snapshot"] = 2
    finally:
        after = command("query-status")
        report["stopped_status_after"] = after
        if not stopped(after) or after["status"] != before["status"]:
            raise RuntimeError("Camera GDSC guest status changed during observation")
    if (report["explicit_reference_policy_enabled"] and
            report["raw_state_stable_during_capture"] and
            report["reference_state_invariants_match"]):
        report["observation_status"] = "complete"
    report["interpretation"] = (
        "Two stopped QOM snapshots agree with the declared digital reference. "
        "Unknown request/power values use false placeholders with separate knowledge flags; "
        "they do not prove physical power-off. Timeout metadata describes the sourced driver "
        "poll limit, not a modeled or measured transition delay. A stable state snapshot "
        "does not establish that the native driver accessed any register, that camera "
        "execution or retention worked, or that userspace or Android booted. "
        "The RPMh cross-check is of the logical mmcx resource output, not PMIC voltage.")
    return report
