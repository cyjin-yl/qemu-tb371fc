def verify_tsens_streams(z, info, data):
    """Independently audit TSENS streams and source QMP observations in a ZIP.

    This function reads only the supplied ZipFile. It does not import the
    acceptance test, access the network, extract files, or execute QEMU.
    A successful return is evidence about this supplied artifact only.
    """
    import hashlib
    import json
    import re
    import struct

    def need(condition, message):
        if not condition:
            raise AssertionError("TSENS artifact verification: " + message)

    def exact(actual, expected, label):
        if isinstance(expected, dict):
            need(type(actual) is dict, label + " is not an object")
            need(set(actual) == set(expected), label + " has different keys")
            for key in expected:
                exact(actual[key], expected[key], label + "." + str(key))
        elif isinstance(expected, list):
            need(type(actual) is list and len(actual) == len(expected),
                 label + " has different list shape")
            for index, value in enumerate(expected):
                exact(actual[index], value, label + "[" + str(index) + "]")
        else:
            need(type(actual) is type(expected) and actual == expected,
                 label + ": expected " + repr(expected) + ", got " + repr(actual))

    def digest(payload):
        return hashlib.sha256(payload).hexdigest()

    def integer(value, label):
        need(type(value) is int, label + " must be an integer")
        return value

    filename = info.filename
    need(type(filename) is str and filename.endswith("/result.json"),
         "expected a component result.json ZipInfo")
    base = filename.rsplit("/", 1)[0]
    names = {}
    for item in z.infolist():
        names.setdefault(item.filename, []).append(item)
    cache = {}

    def read(relative):
        need(type(relative) is str and not relative.startswith("/") and
             all(part not in ("", ".", "..") for part in relative.split("/")),
             "invalid relative archive member " + repr(relative))
        path = base + "/" + relative
        need(len(names.get(path, [])) == 1, "missing or duplicate ZIP member " + path)
        if path not in cache:
            payload = z.read(path)
            need(type(payload) is bytes, path + " did not return bytes")
            need(len(payload) == names[path][0].file_size, path + " size differs from ZIP index")
            cache[path] = payload
        return cache[path]

    exact(json.loads(read("result.json")), data, "supplied receipt")
    exact(data.get("schema"), "tb371fc-tsens24xx-v1", "schema")
    for key, expected in {
        "passed": True, "binary_unchanged": True, "expected_checks": 34,
        "expected_completed_migrations": 2, "expected_rejected_migrations": 2,
        "expected_corrupted_stream_rejections": 2, "controller_count": 2,
        "sensors_per_controller": 16, "sensor_count": 32, "register_count": 114,
        "sparse_window_count": 6, "mapped_bytes": 456, "irq_count": 4,
        "sampling_period_ns": 1000000, "vmstate_version": 1,
        "vmstate_payload_bytes_per_controller": 375,
        "reference_profile_is_default": False, "reference_boot_state_is_measured": False,
        "environment_input_is_host_controlled": True,
        "analogue_calibration_implemented": False,
        "watchdog_producer_implemented": False, "cycle_monitor_producer_implemented": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "android_boot_passed": False,
    }.items():
        exact(data.get(key), expected, key)

    checks = data.get("checks")
    need(type(checks) is list and len(checks) == 34, "expected 34 checks")
    need(all(type(entry) is dict and type(entry.get("name")) is str and
             entry.get("passed") is True for entry in checks), "a check is not an actual pass")
    need(len({entry["name"] for entry in checks}) == 34, "duplicate check names")

    def records(key, count):
        result = data.get(key)
        need(type(result) is list and len(result) == count, "wrong " + key + " count")
        need(all(type(entry) is dict and type(entry.get("name")) is str for entry in result),
             key + " contains a malformed record")
        need(len({entry["name"] for entry in result}) == count, key + " has duplicate names")
        return {entry["name"]: entry for entry in result}

    completed = records("migrations", 2)
    rejected = records("rejected_migrations", 2)
    corrupted = records("corrupted_stream_rejections", 2)
    type_name = "qcom-tsens24xx"
    srot = (0x0c222000, 0x0c223000)
    tm = (0x0c263000, 0x0c265000)
    period = 1000000
    unknown_temperature = -2147483648
    payload_bytes = 375
    sources = (
        ("v1_two_controller_captured_inputs_configuration_and_ram_roundtrip",
         "tsens24xx-v1-two-captured-rounds.migration",
         "migration-captured-source", "migration-captured-target", 400000, 0),
        ("v1_actual_latched_irqs_held_clear_current_inputs_and_ram_roundtrip",
         "tsens24xx-v1-latched-irqs-and-clear-hold.migration",
         "migration-irqs-source", "migration-irqs-target", 1400000, 1),
    )
    need(set(completed) == {row[0] for row in sources}, "unexpected completed scenarios")
    profile_cases = (
        ("reference_to_unknown_profile_is_rejected",
         "tsens24xx-v1-two-captured-rounds.migration", True, False),
        ("unknown_to_reference_profile_is_rejected",
         "tsens24xx-v1-unknown-profile.migration", False, True),
    )
    need(set(rejected) == {row[0] for row in profile_cases}, "unexpected profile rejections")
    corrupt_kinds = ("valid-mask-outside-sensors", "captured-code-reserved-bit")
    need(set(corrupted) == set(corrupt_kinds), "unexpected corrupted-stream rejections")
    unknown_name = "tsens24xx-v1-unknown-profile.migration"
    expected_streams = {row[1] for row in sources} | {unknown_name} | {
        "tsens24xx-v1-corrupted-" + kind + ".migration" for kind in corrupt_kinds}
    actual_streams = {
        path[len(base) + 1:] for path in names
        if path.startswith(base + "/") and "/" not in path[len(base) + 1:]
        and path.endswith(".migration")}
    exact(sorted(actual_streams), sorted(expected_streams), "five migration stream members")

    global_names = (
        "control", "interrupt-enable", "upper-lower-status", "upper-lower-clear",
        "upper-lower-mask", "critical-status", "critical-clear", "critical-mask",
        "valid-mask", "trdy", "captured-enable-mask", "captured-valid-mask",
        "sample-pending", "deadline-exhausted", "upper-lower-irq", "critical-irq",
        "sample-deadline-ns", "last-sample-ns", "sample-rounds", "complete-rounds")
    sensor_names = (
        "threshold", "critical-threshold", "status", "last-code", "captured-code",
        "last-millicelsius", "valid", "input-millicelsius", "input-valid")
    register_names = (
        "control", "interrupt_enable", "ul_status", "ul_clear", "ul_mask",
        "critical_status", "critical_clear", "critical_mask")

    def qmp_records(relative):
        raw = read(relative)
        entries, pending = [], None
        for number, line in enumerate(raw.decode("utf-8").splitlines(), 1):
            if not line:
                continue
            if line.startswith("> "):
                need(pending is None, relative + " has overlapping QMP requests")
                request = json.loads(line[2:])
                need(type(request) is list and len(request) == 2 and
                     type(request[0]) is str and
                     (request[1] is None or type(request[1]) is dict),
                     relative + " has an invalid request at line " + str(number))
                pending = (request[0], request[1], number)
            elif line.startswith("< "):
                need(pending is not None, relative + " has an unmatched QMP result")
                failed = line.startswith("< ERROR ")
                response = line[8:] if failed else json.loads(line[2:])
                entries.append({
                    "execute": pending[0], "arguments": pending[1],
                    "request_line": pending[2], "response": response, "error": failed})
                pending = None
            else:
                need(False, relative + " contains an unframed QMP line " + str(number))
        need(pending is None, relative + " ends with an unanswered QMP request")
        return entries, {"path": base + "/" + relative, "bytes": len(raw),
                         "sha256": digest(raw), "read_from_zip": True}

    def file_request(arguments, basename, label):
        need(type(arguments) is dict, label + " arguments are not an object")
        if "channels" in arguments:
            channels = arguments["channels"]
            need(type(channels) is list and len(channels) == 1, label + " channel count")
            channel = channels[0]
            need(type(channel) is dict and channel.get("channel-type") == "main",
                 label + " is not the main migration channel")
            address = channel.get("addr")
            need(type(address) is dict and address.get("transport") == "file",
                 label + " is not a file migration")
            path = address.get("filename")
        else:
            uri = arguments.get("uri")
            need(type(uri) is str and uri.startswith("file:"), label + " has no file URI")
            path = uri[5:]
        need(type(path) is str and path.rsplit("/", 1)[-1] == basename,
             label + " used a different migration file")

    def migration_log(directory, basename, incoming=False, expected_response=None):
        entries, provenance = qmp_records(directory + "/qmp.log")
        command = "migrate-incoming" if incoming else "migrate"
        positions = [index for index, entry in enumerate(entries)
                     if entry["execute"] == command]
        need(len(positions) == 1, directory + " has wrong migration request count")
        position = positions[0]
        request = entries[position]
        need(not request["error"], directory + " migration request itself failed")
        file_request(request["arguments"], basename, directory)
        done = [entry["response"] for entry in entries[position + 1:]
                if entry["execute"] == "query-migrate" and not entry["error"] and
                type(entry["response"]) is dict and entry["response"].get("status") == "completed"]
        need(done, directory + " has no actual completed query-migrate result")
        if expected_response is not None:
            need(any(response == expected_response for response in done),
                 directory + " receipt differs from actual completed QMP result")
        if incoming:
            need(not any(entry["execute"] == "cont" for entry in entries[:position]),
                 directory + " ran the destination before incoming load")
            need(any(entry["execute"] == "query-status" and not entry["error"] and
                     type(entry["response"]) is dict and entry["response"].get("running") is False
                     for entry in entries[position + 1:]),
                 directory + " did not observe a paused restored destination")
        else:
            stops = [index for index in range(position)
                     if entries[index]["execute"] == "stop" and not entries[index]["error"]]
            need(stops, directory + " has no successful pre-save stop")
            stop = stops[-1]
            need(any(entry["execute"] == "query-status" and not entry["error"] and
                     type(entry["response"]) is dict and entry["response"].get("running") is False
                     for entry in entries[stop + 1:position]),
                 directory + " did not observe the stopped source")
            need(any(entry["execute"] == "query-status" and not entry["error"] and
                     type(entry["response"]) is dict and
                     entry["response"].get("status") == "postmigrate"
                     for entry in entries[position + 1:]),
                 directory + " has no actual postmigrate source status")
        return entries, position, provenance

    def source_qom(entries, migrate_position, label):
        stop = max(index for index in range(migrate_position)
                   if entries[index]["execute"] == "stop" and not entries[index]["error"])
        observed = [{}, {}]
        for entry in entries[stop + 1:migrate_position]:
            if entry["execute"] != "qom-get":
                continue
            need(not entry["error"], label + " contains a failed pre-save observation")
            arguments = entry["arguments"]
            need(type(arguments) is dict, label + " qom-get arguments")
            path = arguments.get("path")
            need(path in ("/machine/tsens0", "/machine/tsens1"),
                 label + " unexpected pre-save QOM object")
            controller = int(path[-1])
            name = arguments.get("property")
            need(type(name) is str and name not in observed[controller],
                 label + " duplicate or invalid pre-save property")
            observed[controller][name] = entry["response"]
        required = set(global_names) | {
            "sensor" + str(i) + "-" + suffix
            for i in range(16) for suffix in sensor_names}
        for controller in range(2):
            exact(sorted(observed[controller]), sorted(required),
                  label + " controller " + str(controller) + " complete QOM observation")
        return observed

    def source_clock(directory, expected):
        raw = read(directory + "/qtest.log")
        pending, current, events = None, 0, []
        for line in raw.decode("utf-8").splitlines():
            if not line:
                continue
            if line.startswith("> "):
                need(pending is None, directory + " overlapping QTest requests")
                pending = line[2:]
            elif line.startswith("< OK"):
                need(pending is not None, directory + " unmatched QTest result")
                words = pending.split()
                if words and words[0] in ("clock_step", "clock_set"):
                    need(len(words) == 2, directory + " ambiguous clock command")
                    value = int(words[1], 0)
                    result = line[4:].split()
                    need(len(result) == 1, directory + " invalid clock reply")
                    returned = int(result[0], 0)
                    want = current + value if words[0] == "clock_step" else value
                    need(returned == want and returned >= current,
                         directory + " inconsistent or backwards source clock")
                    events.append({"command": words[0], "argument_ns": value,
                                   "returned_virtual_ns": returned})
                    current = returned
                pending = None
            else:
                need(False, directory + " unframed QTest line")
        need(pending is None, directory + " unanswered QTest request")
        exact(current, expected, directory + " actual source virtual time")
        return {"path": base + "/" + directory + "/qtest.log", "bytes": len(raw),
                "sha256": digest(raw), "clock_events": events,
                "actual_virtual_ns": current, "read_from_zip": True}

    def decode(raw):
        need(len(raw) == payload_bytes, "incorrect controller payload size")
        position = 0

        def take(fmt):
            nonlocal position
            formatter = struct.Struct(">" + fmt)
            need(position + formatter.size <= len(raw), "truncated controller payload")
            values = formatter.unpack_from(raw, position)
            position += formatter.size
            return values

        profile, controller, sbase, tbase, interval, boot_mask, boot_temp = take("IIQQIII")
        state = {
            "reference_profile": profile, "controller_id": controller,
            "srot_base": sbase, "tm_base": tbase, "sampling_period_ns": interval,
            "reference_boot_enable_mask": boot_mask, "reference_boot_temperature": boot_temp,
            "input_valid": take("I")[0], "input_mc": list(take("16i"))}
        state.update(zip(register_names, take("8I")))
        state["threshold"] = list(take("16I"))
        state["critical_threshold"] = list(take("16I"))
        state["last_code"] = list(take("16H"))
        need(position == 296, "independent decoder valid-mask offset changed")
        state["valid_mask"] = take("I")[0]
        booleans = take("3B")
        need(all(value in (0, 1) for value in booleans), "noncanonical serialized Boolean")
        for name, value in zip(("first_round_complete", "sample_pending", "deadline_exhausted"),
                               booleans):
            state[name] = bool(value)
        state["sample_deadline_ns"] = take("q")[0]
        state["captured_enable_mask"], state["captured_valid_mask"] = take("II")
        need(position == 319, "independent decoder captured-code offset changed")
        state["captured_code"] = list(take("16H"))
        state["sample_rounds"], state["complete_rounds"], state["last_sample_ns"] = take("QQq")
        need(position == len(raw) == 375, "independent decoder did not consume all fields")
        return state

    def sections(stream, profile):
        need(stream[:8] == b"QEVM\x00\x00\x00\x03", "unexpected QEMU migration header")
        output = []
        for controller in range(2):
            name = type_name.encode("ascii")
            needle = bytes([len(name)]) + name + struct.pack(">II", controller, 1)
            candidates, start = [], 0
            while True:
                found = stream.find(needle, start)
                if found < 0:
                    break
                start = found + 1
                header, offset = found - 5, found + len(needle)
                if (header >= 8 and stream[header] in (1, 4) and
                    offset + payload_bytes <= len(stream)):
                    prefix = struct.unpack_from(">IIQQIII", stream, offset)
                    if prefix == (profile, controller, srot[controller], tm[controller],
                                  period, 0, 0x80000000):
                        candidates.append((header, offset))
            need(len(candidates) == 1,
                 "controller " + str(controller) + " does not have one unique FULL/START section")
            header, offset = candidates[0]
            raw = stream[offset:offset + payload_bytes]
            output.append({
                "controller": controller, "instance_id": controller, "version": 1,
                "section_type": stream[header],
                "section_id": struct.unpack_from(">I", stream, header + 1)[0],
                "section_header_offset": header, "payload_offset": offset,
                "payload_bytes": payload_bytes, "payload_sha256": digest(raw),
                "decoded_state": decode(raw)})
        need(output[0]["payload_offset"] != output[1]["payload_offset"],
             "the two controllers resolved to one section")
        return output

    def signed_temperature(code):
        need(type(code) is int and 0 <= code <= 0xfff, "invalid twelve-bit sample code")
        return (code - 4096 if code & 0x800 else code) * 100

    def qom_view(state):
        active = ((state["control"] >> 3) & 0xffff
                  if state["control"] & 1 and not state["control"] & 2 else 0)
        unmasked = state["ul_status"] & ~state["ul_mask"]
        ul_irq = bool(((state["interrupt_enable"] & 1) and (unmasked & active)) or
                      ((state["interrupt_enable"] & 2) and ((unmasked >> 16) & active)))
        critical_irq = bool((state["interrupt_enable"] & 4) and
                            (state["critical_status"] & ~state["critical_mask"] & active))
        result = {
            "control": state["control"], "interrupt-enable": state["interrupt_enable"],
            "upper-lower-status": state["ul_status"], "upper-lower-clear": state["ul_clear"],
            "upper-lower-mask": state["ul_mask"], "critical-status": state["critical_status"],
            "critical-clear": state["critical_clear"], "critical-mask": state["critical_mask"],
            "valid-mask": state["valid_mask"],
            "trdy": 8 if state["first_round_complete"] else 0,
            "captured-enable-mask": state["captured_enable_mask"],
            "captured-valid-mask": state["captured_valid_mask"],
            "sample-pending": state["sample_pending"],
            "deadline-exhausted": state["deadline_exhausted"],
            "upper-lower-irq": ul_irq, "critical-irq": critical_irq,
            "sample-deadline-ns": state["sample_deadline_ns"],
            "last-sample-ns": state["last_sample_ns"],
            "sample-rounds": state["sample_rounds"], "complete-rounds": state["complete_rounds"]}
        for sensor in range(16):
            sampled_valid = bool(state["valid_mask"] & (1 << sensor))
            status = state["last_code"][sensor]
            status |= (1 << 21) if sampled_valid else 0
            status |= (1 << 17) if state["ul_status"] & (1 << sensor) else 0
            status |= (1 << 18) if state["ul_status"] & (1 << (sensor + 16)) else 0
            status |= (1 << 19) if state["critical_status"] & (1 << sensor) else 0
            values = {
                "threshold": state["threshold"][sensor],
                "critical-threshold": state["critical_threshold"][sensor],
                "status": status, "last-code": state["last_code"][sensor],
                "captured-code": state["captured_code"][sensor],
                "last-millicelsius": signed_temperature(state["last_code"][sensor]),
                "valid": sampled_valid, "input-millicelsius": state["input_mc"][sensor],
                "input-valid": bool(state["input_valid"] & (1 << sensor))}
            for suffix, value in values.items():
                result["sensor" + str(sensor) + "-" + suffix] = value
        return result

    def cold(controller, profile):
        state = {
            "reference_profile": profile, "controller_id": controller,
            "srot_base": srot[controller], "tm_base": tm[controller],
            "sampling_period_ns": period, "reference_boot_enable_mask": 0,
            "reference_boot_temperature": 0x80000000,
            "input_valid": 0, "input_mc": [unknown_temperature] * 16,
            "control": 0, "interrupt_enable": 0,
            "ul_status": 0, "ul_clear": 0, "ul_mask": 0xffffffff,
            "critical_status": 0, "critical_clear": 0, "critical_mask": 0xc000ffff,
            "threshold": [0x7ff800] * 16, "critical_threshold": [0x7ff] * 16,
            "last_code": [0] * 16, "valid_mask": 0, "first_round_complete": False,
            "sample_pending": False, "deadline_exhausted": False, "sample_deadline_ns": 0,
            "captured_enable_mask": 0, "captured_valid_mask": 0, "captured_code": [0] * 16,
            "sample_rounds": 0, "complete_rounds": 0, "last_sample_ns": -1}
        return state

    def code(mc):
        need(mc % 100 == 0, "scenario temperature is not exactly representable")
        return (mc // 100) & 0xfff

    def configured(controller, inputs):
        state = cold(controller, 1)
        mask = sum(1 << sensor for sensor in inputs)
        state.update({"input_valid": mask, "control": 1 | (mask << 3),
                      "interrupt_enable": 7, "sample_pending": True,
                      "captured_enable_mask": mask, "captured_valid_mask": mask})
        for sensor, temperature in inputs.items():
            state["input_mc"][sensor] = temperature
            state["captured_code"][sensor] = code(temperature)
            state["threshold"][sensor] = code(-40000) | (code(100000) << 12)
            state["critical_threshold"][sensor] = code(120000)
        return state

    def scenario(number, controller):
        if number == 0:
            if controller == 0:
                state = configured(0, {0: 10000, 5: -12000})
                state["input_mc"][0] = 25000
                state["sample_deadline_ns"] = 1000000
            else:
                state = configured(1, {8: 30000})
                state["input_valid"] = 0
                state["sample_deadline_ns"] = 1200000
        else:
            state = configured(controller, {0: 24000} if controller == 0 else
                               {3: 90000, 8: -1000})
            state.update({
                "sample_deadline_ns": 2000000,
                "valid_mask": state["captured_valid_mask"], "first_round_complete": True,
                "sample_rounds": 1, "complete_rounds": 1, "last_sample_ns": 1000000})
            state["last_code"] = list(state["captured_code"])
            if controller == 1:
                state["input_mc"][3], state["input_mc"][8] = 20000, 5000
                state["threshold"][3] = code(-40000) | (code(50000) << 12)
                state["critical_threshold"][3] = code(70000)
                state["threshold"][8] = code(0) | (code(100000) << 12)
                state["ul_status"] = 1 << 8
                state["ul_clear"] = 1 << 19
                state["ul_mask"] = 0xffffffff ^ (1 << 19) ^ (1 << 8)
                state["critical_status"] = 1 << 3
                state["critical_mask"] = 0xc000ffff ^ (1 << 3)
        return state

    streams, decoded_sections, originals, decoded_by_name = {}, [], {}, {}
    for number, (record_name, basename, source_dir, target_dir, clock, irq_controller) in enumerate(sources):
        entry = completed[record_name]
        for key, expected in {
            "passed": True, "source_status": "completed", "target_status": "completed",
            "source_virtual_ns": clock, "destination_virtual_ns": clock,
            "stream_modified": False, "controller_sections_verified": 2,
            "payload_bytes_per_controller": 375,
            "target_tsens_mmio_before_first_observation": False,
            "target_real_irq_observed_before_first_mmio": True,
            "destination_inputs_were_different_before_load": True,
            "observed_irq_controller": irq_controller,
        }.items():
            exact(entry.get(key), expected, record_name + "." + key)
        stream = read(basename)
        exact(entry.get("snapshot_bytes"), len(stream), basename + " receipt bytes")
        exact(entry.get("snapshot_sha256"), digest(stream), basename + " receipt hash")
        originals[basename] = stream
        source_entries, source_position, source_log = migration_log(
            source_dir, basename, expected_response=entry.get("source"))
        need(type(entry.get("source")) is dict and entry["source"].get("status") == "completed",
             record_name + " missing source completed QMP object")
        need(type(entry.get("destination")) is dict and entry["destination"].get("status") == "completed",
             record_name + " missing destination completed QMP object")
        _, _, target_log = migration_log(
            target_dir, basename, incoming=True, expected_response=entry["destination"])
        clock_log = source_clock(source_dir, clock)
        observations = source_qom(source_entries, source_position, source_dir)
        independently_decoded = sections(stream, 1)
        saved_sections = entry.get("sections")
        need(type(saved_sections) is list and len(saved_sections) == 2,
             record_name + " missing two reported section locations")
        saved_by_controller = {}
        for reported in saved_sections:
            need(type(reported) is dict, record_name + " malformed section record")
            controller = integer(reported.get("controller"), "reported controller")
            need(controller in (0, 1) and controller not in saved_by_controller,
                 record_name + " duplicate/invalid reported controller")
            saved_by_controller[controller] = reported
        for section in independently_decoded:
            controller = section["controller"]
            state = section["decoded_state"]
            exact(state, scenario(number, controller),
                  basename + " independently specified scenario/controller " + str(controller))
            expected_qom = qom_view(state)
            exact(observations[controller], expected_qom,
                  basename + " stopped-source QOM/controller " + str(controller))
            reported = saved_by_controller[controller]
            for key in ("controller", "instance_id", "version", "section_header_offset",
                        "payload_offset", "payload_bytes"):
                exact(reported.get(key), section[key], basename + " section." + key)
            exact(reported.get("srot_base"), srot[controller], basename + " reported SROT")
            exact(reported.get("tm_base"), tm[controller], basename + " reported TM")
            exact(reported.get("all_serialized_fields_match_source_qom"), True,
                  basename + " reported source-QOM conclusion")
            decoded_sections.append({
                **section, "basename": basename, "purpose": "completed-roundtrip-source",
                "source_virtual_ns": clock,
                "remaining_deadline_ns": state["sample_deadline_ns"] - clock,
                "all_fields_independently_decoded": True,
                "all_runtime_fields_match_raw_source_qom": True,
                "immutable_fields_match_fixed_board_and_boot_contract": True,
                "source_qom_properties_compared": len(expected_qom),
                "all_fields_match_independently_specified_scenario": True,
                "source_qmp_sha256": source_log["sha256"]})
        decoded_by_name[basename] = independently_decoded
        streams[basename] = {
            "sha256": digest(stream), "bytes": len(stream), "read_from_zip": True,
            "purpose": "completed-roundtrip-source", "source_qmp": source_log,
            "destination_qmp": target_log, "source_qtest_clock": clock_log,
            "completed_statuses_observed_in_source_and_destination_logs": True,
            "controller_sections_independently_decoded": 2}

    reverse = rejected["unknown_to_reference_profile_is_rejected"]
    unknown_stream = read(unknown_name)
    exact(reverse.get("snapshot_sha256"), digest(unknown_stream), "unknown source hash")
    exact(reverse.get("snapshot_bytes"), len(unknown_stream), "unknown source bytes")
    unknown_entries, unknown_position, unknown_log = migration_log(
        "migration-unknown-source", unknown_name)
    unknown_clock = source_clock("migration-unknown-source", 0)
    unknown_sections = sections(unknown_stream, 0)
    for section in unknown_sections:
        exact(section["decoded_state"], cold(section["controller"], 0),
              "unknown source independent cold state/controller " + str(section["controller"]))
        decoded_sections.append({
            **section, "basename": unknown_name,
            "purpose": "reverse-profile-rejection-source-only",
            "source_virtual_ns": 0, "all_fields_independently_decoded": True,
            "all_runtime_fields_match_raw_source_qom": None,
            "immutable_fields_match_fixed_board_and_boot_contract": True,
            "all_fields_match_independently_rebuilt_unknown_cold_state": True})
    originals[unknown_name] = unknown_stream
    streams[unknown_name] = {
        "sha256": digest(unknown_stream), "bytes": len(unknown_stream), "read_from_zip": True,
        "purpose": "reverse-profile-rejection source; not a third completed roundtrip",
        "source_qmp": unknown_log, "source_qtest_clock": unknown_clock,
        "controller_sections_independently_decoded": 2,
        "cold_state_independently_reconstructed": True}

    def failure_proof(directory, basename, entry, diagnostic):
        for key, expected in {
            "passed": True, "source_status": "completed", "actual_source_stream": True,
            "destination_exit": 1, "request_sent": True,
            "specific_load_failure_observed": True, "device": type_name,
            "expected_diagnostic": diagnostic,
        }.items():
            exact(entry.get(key), expected, directory + "." + key)
        entries, qmp_log = qmp_records(directory + "/qmp.log")
        requests = [item for item in entries if item["execute"] == "migrate-incoming"]
        need(len(requests) == 1, directory + " has no unique actual incoming request")
        file_request(requests[0]["arguments"], basename, directory)
        stderr = read(directory + "/qemu.stderr")
        text = stderr.decode("utf-8", errors="strict")
        need(diagnostic in text, directory + " lacks the exact device diagnostic")
        need(type_name in text and "error while loading state" in text and
             "load of migration failed" in text, directory + " lacks the real migration load failure")
        need(re.search(r"error while loading state[^\n]*" + re.escape(type_name), text) is not None,
             directory + " load-failure line does not identify the TSENS device")
        need(not any(item["execute"] == "query-migrate" and not item["error"] and
                     type(item["response"]) is dict and
                     item["response"].get("status") == "completed" for item in entries),
             directory + " unexpectedly completed migration")
        return {
            "reported_destination_exit": 1,
            "actual_incoming_request_read_from_zip": True,
            "specific_device_load_failure_confirmed_in_stderr": True,
            "expected_diagnostic": diagnostic, "qmp_log": qmp_log,
            "stderr": {"path": base + "/" + directory + "/qemu.stderr",
                       "bytes": len(stderr), "sha256": digest(stderr), "read_from_zip": True}}

    for name, basename, source_reference, target_reference in profile_cases:
        entry = rejected[name]
        for key, expected in {
            "source_reference": source_reference, "target_reference": target_reference,
            "stream_modified": False,
            "profile_mismatch": format(int(target_reference), "x") + " != " +
                                format(int(source_reference), "x"),
        }.items():
            exact(entry.get(key), expected, name + "." + key)
        exact(entry.get("snapshot_sha256"), streams[basename]["sha256"], name + " source hash")
        exact(entry.get("snapshot_bytes"), streams[basename]["bytes"], name + " source bytes")
        proof = failure_proof("migration-" + name, basename, entry, entry["profile_mismatch"])
        streams[basename].setdefault("profile_rejection_proofs", []).append(proof)

    original_name = sources[0][1]
    original = originals[original_name]
    original_section = decoded_by_name[original_name][0]
    need(original_section["controller"] == 0, "corruption source is not controller zero")
    mutation_specs = {
        "valid-mask-outside-sensors": (
            296, 4, ">I", 0, 1 << 16, type_name + ": invalid migrated TSENS state"),
        "captured-code-reserved-bit": (
            319 + 5 * 2, 2, ">H", code(-12000), code(-12000) | 0x1000,
            type_name + ": invalid migrated sensor 5 state"),
    }
    for kind in corrupt_kinds:
        entry = corrupted[kind]
        relative, width, fmt, old_value, new_value, diagnostic = mutation_specs[kind]
        basename = "tsens24xx-v1-corrupted-" + kind + ".migration"
        changed = read(basename)
        offset = original_section["payload_offset"] + relative
        need(len(changed) == len(original), basename + " changed stream length")
        need(original[:offset] == changed[:offset] and
             original[offset + width:] == changed[offset + width:],
             basename + " changed bytes outside the permitted single field")
        exact(struct.unpack_from(fmt, original, offset)[0], old_value, basename + " old raw value")
        exact(struct.unpack_from(fmt, changed, offset)[0], new_value, basename + " new raw value")
        changed_count = sum(left != right for left, right in zip(original, changed))
        exact(changed_count, 1, basename + " actual changed-byte count")
        for key, expected in {
            "modified_field": kind, "modified_field_bytes": width,
            "field_offset": offset, "old_value": old_value, "new_value": new_value,
            "changed_byte_count": changed_count, "only_one_field_modified": True,
            "stream_modified": True, "original_stream_unchanged": True,
            "legitimate_alternate_configuration": False,
            "original_snapshot_bytes": len(original), "corrupted_snapshot_bytes": len(changed),
            "original_snapshot_sha256": digest(original),
            "corrupted_snapshot_sha256": digest(changed),
            "controller": 0, "instance_id": 0, "version": 1,
            "section_header_offset": original_section["section_header_offset"],
            "payload_offset": original_section["payload_offset"], "payload_bytes": 375,
        }.items():
            exact(entry.get(key), expected, basename + "." + key)
        proof = failure_proof("migration-corrupt-" + kind, basename, entry, diagnostic)
        streams[basename] = {
            "sha256": digest(changed), "bytes": len(changed), "read_from_zip": True,
            "purpose": "precise-corrupted-copy-rejection",
            "original_basename": original_name, "original_sha256": digest(original),
            "only_specified_controller_zero_field_modified": True,
            "field_offset": offset, "field_width_bytes": width,
            "field": kind, "old_raw_value": old_value, "new_raw_value": new_value,
            "changed_byte_count": changed_count, "failure_proof": proof}
    need(z.read(base + "/" + original_name) == original,
         "original captured-round stream changed during verification")
    need(len(streams) == 5 and len(decoded_sections) == 6,
         "expected five streams and six controller decodes (four completed-source, two unknown)")
    return {
        "tsens24xx_streams_from_zip": streams,
        "tsens24xx_decoded_sections": decoded_sections,
    }
