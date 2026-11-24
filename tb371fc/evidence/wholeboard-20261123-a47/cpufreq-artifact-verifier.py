def verify_cpufreq_streams(z, info, data):
    """Audit CPUFreq migration bytes and actual QMP records in one CI ZIP.

    No test-module import, network, extraction, guest execution, or subprocess.
    The caller verifies the shared board binary/source receipt separately.
    """
    import hashlib
    import json
    import re
    import struct

    def need(condition, message):
        if not condition:
            raise AssertionError("CPUFreq artifact verification: " + message)

    def same(actual, expected, label):
        if type(expected) is dict:
            need(type(actual) is dict and set(actual) == set(expected),
                 label + " object keys differ")
            for key in expected:
                same(actual[key], expected[key], label + "." + str(key))
        elif type(expected) is list:
            need(type(actual) is list and len(actual) == len(expected),
                 label + " list shape differs")
            for index, value in enumerate(expected):
                same(actual[index], value, label + "[" + str(index) + "]")
        elif type(expected) is bytes:
            if type(actual) is not bytes or actual != expected:
                description = ("bytes=" + str(len(actual)) + ", sha256=" + sha(actual)
                               if type(actual) is bytes else type(actual).__name__)
                need(False, label + ": binary mismatch; expected bytes=" +
                     str(len(expected)) + ", sha256=" + sha(expected) +
                     "; actual " + description)
        else:
            need(type(actual) is type(expected) and actual == expected,
                 label + ": expected " + repr(expected) + ", got " + repr(actual))

    def sha(payload):
        return hashlib.sha256(payload).hexdigest()

    need(type(info.filename) is str and info.filename.endswith("/result.json"),
         "expected a component result.json ZipInfo")
    base = info.filename.rsplit("/", 1)[0]
    names, cache = {}, {}
    for member in z.infolist():
        names.setdefault(member.filename, []).append(member)

    def read(relative):
        need(type(relative) is str and not relative.startswith("/") and
             all(part not in ("", ".", "..") for part in relative.split("/")),
             "invalid relative ZIP name " + repr(relative))
        name = base + "/" + relative
        need(len(names.get(name, [])) == 1, "missing/duplicate ZIP member " + name)
        if name not in cache:
            raw = z.read(name)
            need(type(raw) is bytes and len(raw) == names[name][0].file_size,
                 "ZIP indexed size mismatch: " + name)
            cache[name] = raw
        return cache[name]

    def provenance(relative):
        raw = read(relative)
        return {"member": base + "/" + relative, "bytes": len(raw),
                "sha256": sha(raw), "read_from_zip": True}

    same(json.loads(read("result.json")), data, "supplied component report")
    device_type = "qcom-cpufreq-hw-epss"
    bases = (0x18591000, 0x18592000, 0x18593000)
    masks = (0x0f, 0x70, 0x80)
    counts = (4, 3, 1)
    multipliers = (16, 32, 64, 96)
    millivolts = (750, 800, 900, 1000)
    billion = 1_000_000_000
    period_unit = billion << 32
    payload_size = 84
    domain_paths = tuple("/machine/cpufreq" + str(i) for i in range(3))
    geometry_keys = (
        "enabled-reference", "domain-id", "reference-boot-perf-index",
        "memory-base", "resource-size", "related-cpu-mask", "max-cores",
        "core-counter-count", "lut-max-entries", "lut-row-size",
        "valid-perf-count", "xo-frequency-hz", "alternate-frequency-hz")
    runtime_keys = (
        "performance-index", "requested-lval", "effective-lval",
        "requested-frequency-hz", "frequency-hz", "selected-voltage-microvolt",
        "frequency-known", "limited", "interrupt-status", "irq-level",
        "frequency-limit-lval", "counter", "counter-fraction",
        "counter-value-at-epoch", "counter-fraction-at-epoch", "counter-epoch-ns")
    clock_keys = ("xo", "alternate", "cpu-frequency")
    anchor_keys = ("counter-value-at-epoch", "counter-fraction-at-epoch",
                   "counter-epoch-ns")
    lut_keys = tuple("lut" + str(i) + "-" + field
                    for i in range(40)
                    for field in ("frequency-word", "voltage-word", "frequency-hz"))
    logs = {}

    def qmp(directory):
        relative = directory + "/qmp.log"
        if relative in logs:
            return logs[relative]["entries"]
        raw = read(relative)
        entries, pending = [], None
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line:
                continue
            if line.startswith("> "):
                need(pending is None, relative + " overlapping requests")
                request = json.loads(line[2:])
                need(type(request) is list and len(request) == 2 and
                     type(request[0]) is str and
                     (request[1] is None or type(request[1]) is dict),
                     relative + " malformed request")
                pending = (request[0], request[1], number)
            elif line.startswith("< "):
                need(pending is not None, relative + " unmatched response")
                error = line.startswith("< ERROR ")
                response = line[8:] if error else json.loads(line[2:])
                entries.append({"command": pending[0], "arguments": pending[1],
                                "request_line": pending[2], "response_line": number,
                                "error": error, "response": response})
                pending = None
            else:
                need(False, relative + " unframed line " + str(number))
        need(pending is None, relative + " unanswered request")
        logs[relative] = {**provenance(relative), "entries": entries}
        return entries

    def qtest(directory):
        relative = directory + "/qtest.log"
        raw = read(relative)
        pairs, clock_events, pending, now = [], [], None, 0
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line:
                continue
            if line.startswith("> "):
                need(pending is None, relative + " overlapping requests")
                pending = (line[2:], number)
            elif line.startswith("< OK"):
                need(pending is not None, relative + " unmatched response")
                command, command_line = pending
                result = line[4:].split()
                words = command.split()
                if words[0] in ("clock_step", "clock_set"):
                    need(len(words) == 2 and len(result) == 1,
                         relative + " malformed clock exchange")
                    argument, actual = int(words[1], 0), int(result[0], 0)
                    expected = now + argument if words[0] == "clock_step" else argument
                    need(actual == expected and actual >= now,
                         relative + " invalid/backwards virtual clock")
                    clock_events.append({"command": words[0], "argument_ns": argument,
                                         "returned_virtual_ns": actual,
                                         "request_line": command_line})
                    now = actual
                pairs.append({"command": command, "words": words, "reply": result,
                              "request_line": command_line, "virtual_ns": now})
                pending = None
            else:
                need(False, relative + " unframed QTest line " + str(number))
        need(pending is None, relative + " unanswered QTest request")
        return pairs, {**provenance(relative), "clock_events": clock_events,
                       "last_virtual_ns": now}

    def filename_argument(arguments, expected, label):
        need(type(arguments) is dict, label + " arguments not an object")
        channels = arguments.get("channels")
        if channels is not None:
            need(type(channels) is list and len(channels) == 1, label + " channel count")
            channel = channels[0]
            need(type(channel) is dict and channel.get("channel-type") == "main",
                 label + " not main channel")
            address = channel.get("addr")
            need(type(address) is dict and address.get("transport") == "file",
                 label + " not file transport")
            filename = address.get("filename")
        else:
            uri = arguments.get("uri")
            need(type(uri) is str and uri.startswith("file:"), label + " not a file URI")
            filename = uri[5:]
        need(type(filename) is str and filename.rsplit("/", 1)[-1] == expected,
             label + " migration filename differs")

    def completed_log(directory, filename, incoming, expected_result=None):
        entries = qmp(directory)
        command = "migrate-incoming" if incoming else "migrate"
        positions = [i for i, item in enumerate(entries) if item["command"] == command]
        need(len(positions) == 1, directory + " migration request count")
        position = positions[0]
        request = entries[position]
        need(not request["error"], directory + " migration request failed")
        filename_argument(request["arguments"], filename, directory)
        done = [(i, item["response"]) for i, item in enumerate(entries)
                if i > position and item["command"] == "query-migrate" and
                not item["error"] and type(item["response"]) is dict and
                item["response"].get("status") == "completed"]
        need(done, directory + " has no actual completed QMP migration")
        if expected_result is not None:
            need(any(result == expected_result for _, result in done),
                 directory + " reported completion differs from raw QMP")
        if incoming:
            need(not any(item["command"] == "cont" for item in entries[:position]),
                 directory + " ran before incoming")
        else:
            stops = [i for i in range(position)
                     if entries[i]["command"] == "stop" and not entries[i]["error"]]
            need(stops, directory + " source was not stopped")
            stop = stops[-1]
            need(any(item["command"] == "query-status" and not item["error"] and
                     type(item["response"]) is dict and
                     item["response"].get("running") is False
                     for item in entries[stop + 1:position]),
                 directory + " missing stopped source observation")
            need(any(item["command"] == "query-status" and not item["error"] and
                     type(item["response"]) is dict and
                     item["response"].get("status") == "postmigrate"
                     for item in entries[position + 1:]),
                 directory + " missing source postmigrate state")
        return entries, position, done[0][0]

    def required_keys(domain):
        return (set(geometry_keys) | set(runtime_keys) | set(lut_keys) |
                {"core" + str(i) + "-counter" for i in range(counts[domain])} |
                {"clock:" + name for name in clock_keys})

    def observations(entries, start, end, label, unknown=False):
        values, errors, occurrences = [{} for _ in range(3)], [{} for _ in range(3)], [{} for _ in range(3)]
        for item in entries[start:end]:
            if item["command"] in ("qom-set", "cont", "system_reset"):
                need(False, label + " state-changing request in observation window")
            if item["command"] != "qom-get":
                continue
            args = item["arguments"]
            need(type(args) is dict and type(args.get("path")) is str and
                 type(args.get("property")) is str, label + " malformed qom-get")
            path, prop = args["path"], args["property"]
            matches = [d for d, root in enumerate(domain_paths)
                       if path == root or path.startswith(root + "/")]
            need(len(matches) == 1, label + " unexpected observed object " + path)
            domain = matches[0]
            if path != domain_paths[domain]:
                child = path[len(domain_paths[domain]) + 1:]
                need(child in clock_keys and prop == "qtest-clock-period",
                     label + " unexpected Clock property")
                prop = "clock:" + child
            need(prop in required_keys(domain), label + " unexpected property " + prop)
            target = errors[domain] if item["error"] else values[domain]
            need(prop not in (values[domain] if item["error"] else errors[domain]),
                 label + " property alternated success and failure")
            if prop in target:
                same(item["response"], target[prop], label + " repeated read " + prop)
            target[prop] = item["response"]
            occurrences[domain][prop] = occurrences[domain].get(prop, 0) + 1
            if item["error"]:
                need(unknown and device_type + ": unknown profile" in item["response"],
                     label + " unexpected QOM failure")
        for d in range(3):
            if unknown:
                known_keys = set(geometry_keys) | {"clock:" + key for key in clock_keys}
                same(sorted(values[d]), sorted(known_keys), label + " known-property set")
                same(sorted(errors[d]), sorted(required_keys(d) - known_keys),
                     label + " complete unknown runtime rejection set")
            else:
                same(sorted(values[d]), sorted(required_keys(d)), label + " complete property set")
                same(errors[d], {}, label + " unexpected errors")
        return values, errors, occurrences

    field_names = (
        "profile_marker", "domain_id", "base", "resource_size", "cpu_mask",
        "max_cores", "cpu_count", "reference_boot_perf_index", "lut_identity",
        "lut_entries", "lut_stride", "perf_index", "frequency_limit_lval",
        "irq_status", "counter_value", "counter_fraction", "xo_period",
        "alternate_period")
    wire_format = struct.Struct(">IIQ13I2Q")
    need(wire_format.size == payload_size, "independent 84-byte layout definition")

    def immutable(domain, profile):
        return {
            "profile_marker": profile, "domain_id": domain, "base": bases[domain],
            "resource_size": 0x1000, "cpu_mask": masks[domain], "max_cores": 4,
            "cpu_count": counts[domain], "reference_boot_perf_index": 0,
            "lut_identity": 0x584f3031, "lut_entries": 40, "lut_stride": 4}

    def decode(raw):
        need(len(raw) == 84, "truncated EPSS payload")
        return dict(zip(field_names, wire_format.unpack(raw)))

    def find_sections(stream, profile):
        same(stream[:8], b"QEVM\x00\x00\x00\x03", "migration stream header")
        output = []
        for domain in range(3):
            name = device_type.encode("ascii")
            marker = bytes([len(name)]) + name + struct.pack(">II", domain, 1)
            candidates, cursor = [], 0
            while True:
                found = stream.find(marker, cursor)
                if found < 0:
                    break
                cursor = found + 1
                header, offset = found - 5, found + len(marker)
                if header < 8 or stream[header] not in (1, 4) or offset + 84 > len(stream):
                    continue
                state = decode(stream[offset:offset + 84])
                if all(state[key] == value for key, value in immutable(domain, profile).items()):
                    candidates.append((header, offset, state))
            need(len(candidates) == 1, "domain " + str(domain) +
                 " lacks a unique FULL/START section with the fixed identity")
            header, offset, state = candidates[0]
            output.append({
                "domain": domain, "instance_id": domain, "version": 1,
                "section_type": stream[header],
                "section_id": struct.unpack_from(">I", stream, header + 1)[0],
                "section_header_offset": header, "payload_offset": offset,
                "payload_bytes": 84, "payload_sha256": sha(stream[offset:offset + 84]),
                "decoded_state": state})
        need(len({item["payload_offset"] for item in output}) == 3,
             "controller sections overlap")
        for left in range(3):
            for right in range(left + 1, 3):
                need(abs(output[left]["payload_offset"] - output[right]["payload_offset"]) >= 84,
                     "controller payload ranges overlap")
        return output

    def hz(period):
        return period_unit // period if period else 0

    def period(frequency):
        return period_unit // frequency if frequency else 0

    def expected_view(domain, state, epoch, anchor_value, anchor_fraction, now, unknown=False):
        xo, alternate = hz(state["xo_period"]), hz(state["alternate_period"])
        view = {
            "enabled-reference": bool(state["profile_marker"]),
            "domain-id": domain, "reference-boot-perf-index": 0,
            "memory-base": bases[domain], "resource-size": 0x1000,
            "related-cpu-mask": masks[domain], "max-cores": 4,
            "core-counter-count": counts[domain], "lut-max-entries": 40,
            "lut-row-size": 4, "valid-perf-count": 4,
            "xo-frequency-hz": xo, "alternate-frequency-hz": alternate,
            "clock:xo": state["xo_period"], "clock:alternate": state["alternate_period"]}
        if unknown:
            view["clock:cpu-frequency"] = 0
            return view
        index, cap = state["perf_index"], state["frequency_limit_lval"]
        need(0 <= index < 4 and 0 <= cap <= 255, "invalid source runtime selector")
        requested_l = multipliers[index]
        effective_l = min(requested_l, cap) if cap else requested_l
        rate = xo * effective_l
        need(now >= epoch, "counter observation predates its anchor")
        numerator = anchor_fraction + (now - epoch) * rate
        current = (anchor_value + numerator // billion) & 0xffffffff
        fraction = numerator % billion
        view.update({
            "performance-index": index, "requested-lval": requested_l,
            "effective-lval": effective_l, "requested-frequency-hz": xo * requested_l,
            "frequency-hz": rate, "selected-voltage-microvolt": millivolts[index] * 1000,
            "frequency-known": rate != 0, "limited": bool(rate and effective_l < requested_l),
            "interrupt-status": state["irq_status"], "irq-level": bool(state["irq_status"] & 4),
            "frequency-limit-lval": cap, "counter": current, "counter-fraction": fraction,
            "counter-value-at-epoch": anchor_value,
            "counter-fraction-at-epoch": anchor_fraction, "counter-epoch-ns": epoch,
            "clock:cpu-frequency": period(rate)})
        for row in range(40):
            i = min(row, 3)
            view["lut" + str(row) + "-frequency-word"] = 0x40040000 | multipliers[i]
            view["lut" + str(row) + "-voltage-word"] = millivolts[i]
            view["lut" + str(row) + "-frequency-hz"] = xo * multipliers[i]
        for core in range(counts[domain]):
            view["core" + str(core) + "-counter"] = current
        return view

    def reported_snapshot(view, errors, domain):
        return {
            "geometry": {key: view[key] for key in geometry_keys},
            "clock_periods": {key: view["clock:" + key] for key in clock_keys},
            "runtime": {key: view[key] for key in runtime_keys if key in view},
            "runtime_errors": errors,
            "lut": [{field: view.get("lut" + str(row) + "-" + field)
                     for field in ("frequency-word", "voltage-word", "frequency-hz")}
                    for row in range(40)],
            "core_counters": [view.get("core" + str(i) + "-counter")
                              for i in range(counts[domain])]}

    def stream_entry(filename, purpose):
        raw = read(filename)
        return {"member": base + "/" + filename, "bytes": len(raw),
                "sha256": sha(raw), "purpose": purpose, "read_from_zip": True}

    def records(key, number):
        rows = data.get(key)
        need(type(rows) is list and len(rows) == number, "wrong " + key + " count")
        need(all(type(row) is dict and type(row.get("name")) is str for row in rows),
             "malformed " + key + " record")
        need(len({row["name"] for row in rows}) == number, "duplicate " + key + " names")
        return {row["name"]: row for row in rows}

    def failed_incoming(directory, filename, record, diagnostic):
        same(record.get("passed"), True, directory + " failure verdict")
        same(record.get("source_status"), "completed", directory + " source status")
        same(record.get("destination_exit"), 1, directory + " reported destination exit")
        same(record.get("request_sent"), True, directory + " request flag")
        same(record.get("specific_load_failure_observed"), True, directory + " specific failure")
        entries = qmp(directory)
        incoming = [item for item in entries if item["command"] == "migrate-incoming"]
        need(len(incoming) == 1, directory + " incoming request count")
        filename_argument(incoming[0]["arguments"], filename, directory)
        need(not any(item["command"] == "query-migrate" and not item["error"] and
                     type(item["response"]) is dict and
                     item["response"].get("status") == "completed" for item in entries),
             directory + " unexpectedly completed")
        raw = read(directory + "/qemu.stderr")
        text = raw.decode("utf-8")
        need(diagnostic in text, directory + " missing exact rejection diagnostic")
        need(re.search(r"error while loading state[^\n]*" + re.escape(device_type), text) is not None
             and "load of migration failed" in text,
             directory + " missing actual device-specific migration failure")
        return {"reported_destination_exit": 1, "incoming_request_observed": True,
                "specific_device_failure_in_actual_stderr": True,
                "expected_diagnostic": diagnostic,
                "qmp": provenance(directory + "/qmp.log"),
                "stderr": provenance(directory + "/qemu.stderr")}

    expected_report = {
        "schema": "tb371fc-cpufreq-hw-v1", "passed": True, "binary_unchanged": True,
        "script_sha256": "0a11d0f3c8f70c09716083830b8f672a2c62b82d3ff42589373b480b3998dd03",
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "source_sha256": "559c942f52eedf76ab22f7770b7fa49a924c1941486b1e9afd419d38885ad174",
        "dtb_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "dt_evidence_blob": "541c58de5c6711215cd6ebc24e48a11ea4a69127",
        "dt_capture_sha256": "815874739b5ccfd4f248fd953849f775387dab53b2859dff508bee74ae74136d",
        "expected_checks": 30, "expected_completed_migrations": 2,
        "expected_rejected_migrations": 2, "expected_corrupted_stream_rejections": 2,
        "domain_count": 3, "cpu_count": 8, "per_cpu_counter_count": 8,
        "register_count": 263, "sparse_window_count": 24, "mapped_bytes": 1052,
        "registers_per_domain": [89, 88, 86], "windows_per_domain": 8,
        "lut_rows_per_domain": 40, "lut_row_bytes": 4, "valid_perf_states_per_domain": 4,
        "irq_count": 3, "input_clock_count": 6, "output_clock_count": 3,
        "vmstate_version": 1, "vmstate_payload_bytes_per_domain": 84,
        "reference_profile_is_default": False, "reference_lut_is_synthetic": True,
        "factory_lut_observed": False, "counter_source_is_virtual_time": True,
        "counter_measures_retired_cpu_cycles": False, "cpu_execution_speed_controlled": False,
        "analogue_voltage_control_implemented": False, "hardware_thermal_throttling_observed": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False, "reference_lut_lvals": [16, 32, 64, 96],
        "reference_lut_frequency_words": [0x40040010, 0x40040020, 0x40040040, 0x40040060],
        "reference_lut_voltage_words": [750, 800, 900, 1000],
        "reference_max_cores": [4, 4, 4], "actual_domain_cpu_counts": [4, 3, 1],
        "reference_lut_identity": 0x584f3031,
    }
    for key, value in expected_report.items():
        same(data.get(key), value, "report." + key)
    checks = data.get("checks")
    need(type(checks) is list and len(checks) == 30 and
         all(type(item) is dict and type(item.get("name")) is str and
             item.get("passed") is True for item in checks), "thirty successful checks required")
    need(len({item["name"] for item in checks}) == 30, "duplicate check names")
    completed, rejected, corrupted = records("migrations", 2), records("rejected_migrations", 2), records("corrupted_stream_rejections", 2)
    scenarios = (
        {
            "name": "v1_three_domain_fractional_cycles_clock_and_ram_roundtrip",
            "file": "cpufreq-hw-v1-fractional-rates.migration",
            "source": "migration-fraction-source", "target": "migration-fraction-target",
            "time": 1, "target_time": 987654321, "anchor_time": 0, "anchor_count": 0,
            "indices": (1, 2, 3), "caps": (0, 0, 0), "irq": (0, 0, 0),
            "counts": (0, 1, 1), "fractions": (614400000, 228800000, 843200000),
            "alternate": 0, "source_steps": [1], "target_steps": [1, 125],
            "target_clear": False, "observed_target_irq": False,
        },
        {
            "name": "v1_external_caps_latched_irqs_provider_clock_and_ram_roundtrip",
            "file": "cpufreq-hw-v1-latched-limits.migration",
            "source": "migration-limits-source", "target": "migration-limits-target",
            "time": 5013, "target_time": 73, "anchor_time": 5000, "anchor_count": 1536,
            "indices": (3, 2, 1), "caps": (48, 17, 8), "irq": (0, 4, 4),
            "counts": (1547, 1540, 1537), "fractions": (980800000, 243200000, 996800000),
            "alternate": 600000000, "source_steps": [5000, 13], "target_steps": [1, 5, 13],
            "target_clear": True, "observed_target_irq": True,
        },
    )
    same(sorted(completed), sorted(case["name"] for case in scenarios), "completed scenario names")
    unknown_file = "cpufreq-hw-v1-unknown-profile.migration"
    mutation_specs = {
        "performance-index": (0, 48, 1, 5, "perf_index",
                              device_type + ": invalid migrated performance index"),
        "counter-fraction": (2, 64, 843200000, 843200000 | 0x80000000, "counter_fraction",
                             device_type + ": invalid migrated reference state"),
    }
    expected_files = {case["file"] for case in scenarios} | {unknown_file} | {
        "cpufreq-hw-v1-corrupted-" + kind + ".migration" for kind in mutation_specs}
    actual_files = {name[len(base) + 1:] for name in names
                    if name.startswith(base + "/") and
                    "/" not in name[len(base) + 1:] and name.endswith(".migration")}
    same(sorted(actual_files), sorted(expected_files), "five actual root migration files")
    streams, decoded_sections, section_map, log_evidence, originals = {}, [], {}, {}, {}

    def source_state(case, domain):
        return {
            **immutable(domain, 1),
            "perf_index": case["indices"][domain],
            "frequency_limit_lval": case["caps"][domain], "irq_status": case["irq"][domain],
            "counter_value": case["counts"][domain], "counter_fraction": case["fractions"][domain],
            "xo_period": period(19200000), "alternate_period": period(case["alternate"])}

    def raw_status_witness(entries, reported, label):
        need(type(reported) is dict and reported.get("running") is False,
             label + " is not stopped")
        need(any(item["command"] == "query-status" and not item["error"] and
                 item["response"] == reported for item in entries),
             label + " is absent from actual QMP")

    def command_proof(directory, profile, incoming):
        argv = json.loads(read(directory + "/command.json"))
        need(type(argv) is list and all(type(item) is str for item in argv), "invalid command file")
        need(argv.count("-accel") == 1 and argv[argv.index("-accel") + 1] == "qtest" and "-S" in argv,
             directory + " did not use stopped QTest acceleration")
        setting = device_type + ".enabled-reference=" + ("on" if profile else "off")
        same(argv.count(setting), 1, directory + " explicit reference profile")
        need(not any(option in argv for option in ("-kernel", "-bios", "-firmware")),
             directory + " unexpectedly provided guest executable input")
        if incoming:
            need("-incoming" in argv and argv[argv.index("-incoming") + 1] == "defer",
                 directory + " is not a deferred incoming process")
        return provenance(directory + "/command.json")

    def section_receipt(reported, decoded, stream, label, reference):
        need(type(reported) is list and len(reported) == 3, label + " section count")
        for domain, (receipt, own) in enumerate(zip(reported, decoded)):
            state, offset = own["decoded_state"], own["payload_offset"]
            for key, value in {
                "domain": domain, "instance_id": domain, "section_version": 1,
                "section_name": device_type, "section_header_offset": own["section_header_offset"],
                "section_id": own["section_id"], "payload_offset": offset,
                "payload_bytes": 84, "payload_hex": stream[offset:offset + 84].hex(),
                "all_serialized_fields_verified": True,
                "runtime_matches_actual_source_qom": reference,
                "unknown_runtime_zero_invariant_verified": not reference,
                "input_clock_periods_match_actual_source_clock_objects": True,
            }.items():
                same(receipt.get(key), value, label + "." + str(domain) + "." + key)
            reported_names = (
                "profile", "domain", "memory_base", "resource_size", "related_cpu_mask",
                "max_cores", "cpu_count", "boot_perf_index", "lut_identity", "lut_entries",
                "lut_stride", "performance_index", "frequency_limit_lval", "interrupt_status",
                "counter", "fraction", "xo_clock_period", "alternate_clock_period")
            same(receipt.get("decoded_fields"),
                 dict(zip(reported_names, (state[key] for key in field_names))),
                 label + " reported decoded fields")
            payload_file = receipt.get("payload_file")
            need(type(payload_file) is str and "/" not in payload_file, label + " payload filename")
            same(read(payload_file), stream[offset:offset + 84], label + " actual saved payload")

    for case in scenarios:
        name, filename = case["name"], case["file"]
        record, raw = completed[name], read(filename)
        originals[filename] = raw
        for key, expected in {
            "passed": True, "source_status": "completed", "target_status": "completed",
            "source_directory": case["source"], "target_directory": case["target"],
            "source_virtual_ns": case["time"], "destination_virtual_ns": case["target_time"],
            "different_virtual_origins": True, "snapshot_file": filename,
            "snapshot_bytes": len(raw), "snapshot_sha256": sha(raw), "stream_modified": False,
            "domain_sections_verified": 3, "payload_bytes_per_domain": 84,
            "target_epss_mmio_before_first_observation": False,
            "target_real_clock_observed_before_first_mmio": True,
            "target_real_irq_observed_before_first_mmio": True,
            "destination_state_was_different_before_load": True,
            "source_counter_mmio_before_save": False,
            "source_qom_projection_before_save": True,
            "source_qom_did_not_settle_counter_before_save": True,
            "pre_save_freshness_observed": True, "continued_after_restore": True,
        }.items():
            same(record.get(key), expected, name + "." + key)
        source, target = case["source"], case["target"]
        command_proof(source, True, False)
        command_proof(target, True, True)
        src, save_position, _ = completed_log(source, filename, False, record["source"])
        dst, load_position, _ = completed_log(target, filename, True, record["destination"])
        for directory, key, report_key in (
                (source, "source-query-migrate.json", "source"),
                (target, "destination-query-migrate.json", "destination")):
            same(json.loads(read(directory + "/" + key)), record[report_key],
                 directory + " saved actual query-migrate")
        stop = max(i for i in range(save_position)
                   if src[i]["command"] == "stop" and not src[i]["error"])
        before, before_errors, repetitions = observations(src, stop + 1, save_position, source + " before-save")
        after, after_errors, _ = observations(src, save_position + 1, len(src), source + " after-save")
        next_cont = next((i for i in range(load_position + 1, len(dst))
                          if dst[i]["command"] == "cont"), None)
        need(next_cont is not None and not dst[next_cont]["error"], target + " never continued")
        restored, restored_errors, _ = observations(dst, load_position + 1, next_cont,
                                                      target + " first restored observation")
        last_target_set = max(i for i in range(load_position) if dst[i]["command"] == "qom-set")
        previous, previous_errors, _ = observations(dst, last_target_set + 1, load_position,
                                                    target + " pre-load observation")
        sq, source_clock = qtest(source)
        tq, target_clock = qtest(target)
        same(source_clock["last_virtual_ns"], case["time"], source + " source time")
        same([(e["command"], e["argument_ns"]) for e in source_clock["clock_events"]],
             [("clock_step", step) for step in case["source_steps"]], source + " clock exchanges")
        same([(e["command"], e["argument_ns"]) for e in target_clock["clock_events"]],
             [("clock_set", case["target_time"])] +
             [("clock_step", step) for step in case["target_steps"]], target + " clock exchanges")
        for pair in sq:
            words = pair["words"]
            if words[0].startswith("read") and len(words) >= 2:
                address = int(words[1], 0)
                need(not any(base_addr + 0x3c8 <= address < base_addr + 0x3c8 + 4 * counts[d]
                             for d, base_addr in enumerate(bases)),
                     source + " read a counter through MMIO before save")
        own_sections = find_sections(raw, 1)
        section_map[filename] = own_sections
        section_receipt(record["sections"], own_sections, raw, filename, True)
        before_json = json.loads(read(source + "/source-qom-before-save.json"))
        after_json = json.loads(read(source + "/source-qom-after-save.json"))
        target_json = json.loads(read(target + "/destination-before-first-epss-mmio.json"))
        previous_json = json.loads(read(target + "/destination-before-load.json"))
        same(before_json.get("source_virtual_ns"), case["time"], source + " before JSON time")
        same(after_json.get("source_virtual_ns"), case["time"], source + " after JSON time")
        same(target_json.get("destination_virtual_ns"), case["target_time"], target + " JSON time")
        same(target_json.get("epss_mmio_since_incoming"), False, target + " MMIO scope")
        same(target_json.get("qmp_cont_since_incoming"), False, target + " cont scope")
        same(target_json.get("actual_irq_level"), case["observed_target_irq"], target + " reported GPIO")
        same(before_json.get("qom_observation_left_anchors_unchanged"), True, source + " anchor scope")
        for entries, evidence, label in (
                (src, before_json, source + " before status"),
                (src, after_json, source + " after status"),
                (dst, target_json, target + " restored status"),
                (dst, previous_json, target + " pre-load status")):
            raw_status_witness(entries, evidence.get("query_status"), label)
        for d in range(3):
            state = own_sections[d]["decoded_state"]
            same(state, source_state(case, d), filename + " independently specified scenario/domain " + str(d))
            expected_before = expected_view(d, state, case["anchor_time"], case["anchor_count"], 0, case["time"])
            expected_after = expected_view(d, state, case["time"], state["counter_value"],
                                           state["counter_fraction"], case["time"])
            expected_target = expected_view(d, state, case["target_time"], state["counter_value"],
                                            state["counter_fraction"], case["target_time"])
            same(before[d], expected_before, source + " raw pre-save QMP/domain " + str(d))
            same(after[d], expected_after, source + " raw post-save QMP/domain " + str(d))
            same(restored[d], expected_target, target + " raw restored QMP/domain " + str(d))
            for key in anchor_keys:
                same(repetitions[d][key], 3, source + " repeated nonmutating anchor observations")
            same(before_json["anchors_before_qom_observation"][d],
                 {key: expected_before[key] for key in anchor_keys}, source + " saved raw anchors")
            need((state["counter_value"], state["counter_fraction"]) !=
                 (expected_before["counter-value-at-epoch"], expected_before["counter-fraction-at-epoch"]),
                 filename + " did not actually require pre-save freshness")
            for evidence, view, errors, label in (
                    (before_json, before[d], before_errors[d], "source before"),
                    (after_json, after[d], after_errors[d], "source after"),
                    (target_json, restored[d], restored_errors[d], "target restored"),
                    (previous_json, previous[d], previous_errors[d], "target previous")):
                same(evidence["domains"][d], reported_snapshot(view, errors, d),
                     filename + " saved JSON matches raw QMP/" + label + "/" + str(d))
            need(previous[d] != restored[d], target + " destination state was already identical")
            same(previous[d]["counter-epoch-ns"], case["target_time"], target + " pre-load epoch")
            expected_first_ns = expected_view(d, state, case["target_time"],
                                             state["counter_value"], state["counter_fraction"],
                                             case["target_time"] + 1)
            for prop in ("counter", "counter-fraction", "counter-epoch-ns") + tuple(
                    "core" + str(core) + "-counter" for core in range(counts[d])):
                hits = [entry for entry in dst[next_cont + 1:]
                        if entry["command"] == "qom-get" and
                        entry["arguments"] == {"path": domain_paths[d], "property": prop}]
                need(hits and not hits[0]["error"], target + " missing first-nanosecond phase proof")
                same(hits[0]["response"], expected_first_ns[prop], target + " exact first-nanosecond " + prop)
            decoded_sections.append({
                **own_sections[d], "file": filename, "purpose": "completed-roundtrip-source",
                "all_fields_independently_decoded": True,
                "immutable_prefix_matches_fixed_board_and_boot_contract": True,
                "runtime_and_clock_fields_match_actual_source_qom": True,
                "full_state_matches_independently_specified_scenario": True,
                "pre_save_freshness_required_and_observed": True,
                "source_virtual_ns": case["time"], "source_anchor_ns_before_save": case["anchor_time"],
                "destination_reanchored_virtual_ns": case["target_time"],
                "source_qom_property_count": len(before[d]),
                "first_destination_nanosecond_preserves_fraction": True})
        for key, value in {
            "source_qom_before_file": source + "/source-qom-before-save.json",
            "source_qom_after_file": source + "/source-qom-after-save.json",
            "source_query_migrate_file": source + "/source-query-migrate.json",
            "target_qom_before_first_mmio_file": target + "/destination-before-first-epss-mmio.json",
            "target_query_migrate_file": target + "/destination-query-migrate.json",
        }.items():
            same(record.get(key), value, name + " evidence path")
        streams[filename] = stream_entry(filename, "completed-roundtrip-source")
        streams[filename].update({"source": source, "target": target,
                                 "actual_source_and_destination_completed": True,
                                 "independently_decoded_domains": 3,
                                 "source_virtual_ns": case["time"],
                                 "destination_virtual_ns": case["target_time"]})
        log_evidence[name] = {
            "source_qmp": provenance(source + "/qmp.log"),
            "target_qmp": provenance(target + "/qmp.log"),
            "source_qtest": source_clock, "target_qtest": target_clock,
            "source_counter_mmio_absent": True,
            "pre_save_counter_projection_matches_raw_bytes": True,
            "pre_save_anchor_reads_were_nonmutating": True,
            "destination_was_stopped_and_different_before_load": True,
            "first_restored_qom_is_before_cont_or_qom_set": True,
            "restored_clock_and_irq_getters_match_loaded_state": True,
            "first_one_ns_continuation_independently_verified": True,
            "physical_irq_interception_verdict_scope":
                "GPIO level is reported by the acceptance test; raw QMP corroborates the loaded IRQ latch",
        }

    unknown_record = data.get("unknown_profile_source")
    need(type(unknown_record) is dict, "missing unknown-profile source record")
    unknown_raw = read(unknown_file)
    originals[unknown_file] = unknown_raw
    same(unknown_record.get("completed_roundtrip"), False, "unknown source is not a third roundtrip")
    same(unknown_record.get("guest_runtime_qom_access_refused"), True, "unknown runtime refusal")
    for key, expected in {
        "source_directory": "migration-unknown-source", "snapshot_file": unknown_file,
        "source_status": "completed", "snapshot_bytes": len(unknown_raw),
        "snapshot_sha256": sha(unknown_raw), "source_virtual_ns": 12345,
        "source_counter_mmio_before_save": False, "source_qom_projection_before_save": True,
        "source_qom_did_not_settle_counter_before_save": False, "pre_save_freshness_observed": False,
    }.items():
        same(unknown_record.get(key), expected, "unknown." + key)
    unknown_dir = "migration-unknown-source"
    command_proof(unknown_dir, False, False)
    unknown_done = json.loads(read(unknown_dir + "/source-query-migrate.json"))
    ue, up, _ = completed_log(unknown_dir, unknown_file, False, unknown_done)
    us = max(i for i in range(up) if ue[i]["command"] == "stop" and not ue[i]["error"])
    ub, ube, _ = observations(ue, us + 1, up, "unknown source before", True)
    ua, uae, _ = observations(ue, up + 1, len(ue), "unknown source after", True)
    _, unknown_clock = qtest(unknown_dir)
    same([(e["command"], e["argument_ns"]) for e in unknown_clock["clock_events"]],
         [("clock_step", 12345)], "unknown source time exchange")
    unknown_sections = find_sections(unknown_raw, 0)
    section_receipt(unknown_record["sections"], unknown_sections, unknown_raw, unknown_file, False)
    for d, section in enumerate(unknown_sections):
        state = {
            **immutable(d, 0), "perf_index": 0, "frequency_limit_lval": 0,
            "irq_status": 0, "counter_value": 0, "counter_fraction": 0,
            "xo_period": period(19200000), "alternate_period": 0}
        same(section["decoded_state"], state, "unknown independently rebuilt state")
        known = expected_view(d, state, 12345, 0, 0, 12345, unknown=True)
        same(ub[d], known, "unknown pre-save actual known QOM")
        same(ua[d], known, "unknown post-save actual known QOM")
        for stage, view, errors in (("before", ub[d], ube[d]), ("after", ua[d], uae[d])):
            saved = json.loads(read(unknown_dir + "/source-qom-" + stage + "-save.json"))
            same(saved["domains"][d], reported_snapshot(view, errors, d),
                 "unknown saved observation agrees with actual QMP")
        decoded_sections.append({
            **section, "file": unknown_file, "purpose": "reverse-profile-rejection-source-only",
            "all_fields_independently_decoded": True,
            "immutable_prefix_matches_fixed_board_and_boot_contract": True,
            "runtime_was_not_readable_through_qom": True,
            "runtime_zero_state_matches_unknown_profile_invariant": True,
            "input_clock_periods_match_actual_source_qom": True})
    streams[unknown_file] = stream_entry(unknown_file, "reverse profile rejection source; not a third completed roundtrip")
    log_evidence["unknown_source"] = {
        "qmp": provenance(unknown_dir + "/qmp.log"), "qtest": unknown_clock,
        "actual_source_save_completed": True, "unknown_runtime_getters_actually_refused_access": True,
        "completed_roundtrip": False}

    profile_cases = (
        ("reference_to_unknown_profile_is_rejected", scenarios[0]["file"], True, False),
        ("unknown_to_reference_profile_is_rejected", unknown_file, False, True))
    same(sorted(rejected), sorted(row[0] for row in profile_cases), "profile rejection cases")
    for name, filename, source_ref, target_ref in profile_cases:
        row, directory = rejected[name], "migration-" + name
        diagnostic = str(int(target_ref)) + " != " + str(int(source_ref))
        for key, expected in {
            "actual_source_stream": True, "stream_modified": False,
            "source_reference": source_ref, "target_reference": target_ref,
            "target_directory": directory, "snapshot_file": filename,
            "snapshot_bytes": len(originals[filename]), "snapshot_sha256": sha(originals[filename]),
            "profile_mismatch": diagnostic, "device": device_type,
            "expected_diagnostic": diagnostic,
        }.items():
            same(row.get(key), expected, name + "." + key)
        command_proof(directory, target_ref, True)
        proof = failed_incoming(directory, filename, row, diagnostic)
        streams[filename].setdefault("profile_rejection_proofs", []).append(proof)

    same(sorted(corrupted), sorted(mutation_specs), "specific corrupted-field rejection cases")
    original_file = scenarios[0]["file"]
    original = originals[original_file]
    for kind, (domain, relative, old, new, state_key, diagnostic) in mutation_specs.items():
        row = corrupted[kind]
        filename = "cpufreq-hw-v1-corrupted-" + kind + ".migration"
        directory = "migration-corrupt-" + kind
        changed = read(filename)
        original_section = section_map[original_file][domain]
        offset = original_section["payload_offset"] + relative
        same(len(changed), len(original), filename + " total bytes")
        same(changed[:offset], original[:offset], filename + " unchanged prefix bytes")
        same(changed[offset + 4:], original[offset + 4:], filename + " unchanged suffix bytes")
        same(struct.unpack_from(">I", original, offset)[0], old, filename + " old raw field")
        same(struct.unpack_from(">I", changed, offset)[0], new, filename + " new raw field")
        differences = [{"offset": i, "old_byte": a, "new_byte": b}
                       for i, (a, b) in enumerate(zip(original, changed)) if a != b]
        need(len(differences) == 1 and (old ^ new).bit_count() == 1,
             filename + " is not exactly one bit in one field")
        for key, expected in {
            "actual_source_stream": True, "stream_modified": True, "source_status": "completed",
            "target_directory": directory, "legitimate_alternate_configuration": False,
            "original_stream_unchanged": True, "original_snapshot_file": original_file,
            "corrupted_snapshot_file": filename, "original_snapshot_bytes": len(original),
            "corrupted_snapshot_bytes": len(changed), "original_snapshot_sha256": sha(original),
            "corrupted_snapshot_sha256": sha(changed), "domain": domain, "field": kind,
            "section_header_offset": original_section["section_header_offset"],
            "payload_offset": original_section["payload_offset"],
            "field_relative_offset": relative, "field_offset": offset, "modified_field_bytes": 4,
            "old_value": old, "new_value": new, "old_field_hex": original[offset:offset + 4].hex(),
            "new_field_hex": changed[offset:offset + 4].hex(),
            "original_payload_hex": original[original_section["payload_offset"]:original_section["payload_offset"] + 84].hex(),
            "modified_payload_hex": changed[original_section["payload_offset"]:original_section["payload_offset"] + 84].hex(),
            "complete_file_byte_differences": differences,
            "only_one_field_modified": True, "only_one_bit_modified": True,
            "device": device_type, "expected_diagnostic": diagnostic,
        }.items():
            same(row.get(key), expected, filename + "." + key)
        command_proof(directory, True, True)
        proof = failed_incoming(directory, filename, row, diagnostic)
        changed_sections = find_sections(changed, 1)
        for d, section in enumerate(changed_sections):
            expected_state = dict(section_map[original_file][d]["decoded_state"])
            if d == domain:
                expected_state[state_key] = new
            same(section["decoded_state"], expected_state, filename + " all fields/domain " + str(d))
            decoded_sections.append({
                **section, "file": filename, "purpose": "specific-corrupted-copy-rejection",
                "all_fields_independently_decoded": True,
                "immutable_prefix_matches_fixed_board_and_boot_contract": True,
                "invalid_runtime_field_expected": state_key if d == domain else None,
                "all_other_fields_identical_to_actual_source": True})
        streams[filename] = {
            **stream_entry(filename, "specific-corrupted-copy-rejection"),
            "original_file": original_file, "original_sha256": sha(original),
            "only_specified_four_byte_field_modified": True, "only_one_bit_modified": True,
            "domain": domain, "field": kind, "field_offset": offset,
            "old_raw_value": old, "new_raw_value": new,
            "actual_file_byte_differences": differences, "failure_proof": proof}

    for filename, original_bytes in originals.items():
        same(z.read(base + "/" + filename), original_bytes, filename + " remains unchanged")
    need(len(streams) == 5 and len(decoded_sections) == 15,
         "five files and all fifteen domain sections must be verified")
    return {
        "cpufreq_hw_streams_from_zip": streams,
        "cpufreq_hw_decoded_sections": decoded_sections,
        "cpufreq_hw_log_evidence": log_evidence,
    }
