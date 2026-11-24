def verify_devfreq_streams(z, info, data):
    """Read and audit the Devfreq component evidence in one formal CI ZIP.

    Only stdlib parsing is used. This function does not import the acceptance
    test, extract files, run a process, access a network, or execute a guest.
    The caller separately binds the shared binary, source tree and CI receipt.
    """
    import hashlib
    import json
    import re
    import struct

    def need(condition, message):
        if not condition:
            raise AssertionError("Devfreq artifact verification: " + message)

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

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
            need(type(actual) is bytes, label + " is not bytes")
            need(actual == expected,
                 label + " binary mismatch: actual bytes=" + str(len(actual)) +
                 " sha256=" + sha(actual) + "; expected bytes=" +
                 str(len(expected)) + " sha256=" + sha(expected))
        else:
            need(type(actual) is type(expected) and actual == expected,
                 label + ": expected " + repr(expected) + ", got " + repr(actual))

    need(type(info.filename) is str and info.filename.endswith("/result.json"),
         "expected component result.json ZipInfo")
    base = info.filename.rsplit("/", 1)[0]
    members, cache = {}, {}
    for member in z.infolist():
        members.setdefault(member.filename, []).append(member)

    def read(relative):
        need(type(relative) is str and not relative.startswith("/") and
             all(part not in ("", ".", "..") for part in relative.split("/")),
             "invalid relative member " + repr(relative))
        name = base + "/" + relative
        need(len(members.get(name, [])) == 1, "missing/duplicate ZIP member " + name)
        if name not in cache:
            raw = z.read(name)
            need(type(raw) is bytes and len(raw) == members[name][0].file_size,
                 "indexed size mismatch " + name)
            cache[name] = raw
        return cache[name]

    def provenance(relative):
        raw = read(relative)
        return {"member": base + "/" + relative, "bytes": len(raw),
                "sha256": sha(raw), "read_from_zip": True}

    def document(relative):
        return json.loads(read(relative))

    same(document("result.json"), data, "supplied component report")
    device_type = "qcom-devfreq-fw"
    device_path = "/machine/devfreq-l3"
    enable, table, perf, ram = 0x18590000, 0x18590100, 0x18590320, 0x80010000
    lut_identity, nominal_xo = 0x44463031, 19200000
    multipliers = (16, 32, 64, 96)
    nominal_rates = (307200000, 614400000, 1228800000, 1843200000)
    payload_bytes = 72
    machine = ("tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
               "rpmh-profile=off,rpmh-boot-configured=off")
    test_sha = "8c4e49817f58cb2dca002d446486e75fee45c65ee2142963ed9112fbe24669cf"
    geometry = {
        "instance-id": 0, "enable-address": enable,
        "frequency-table-address": table, "performance-address": perf,
        "enable-resource-size": 4, "frequency-table-resource-size": 160,
        "performance-resource-size": 4, "lut-row-size": 4,
        "lut-max-entries": 40, "valid-perf-count": 4,
        "reference-lut-identity": lut_identity,
        "reference-nominal-xo-hz": nominal_xo,
        "mapped-window-count": 3, "mapped-register-count": 42,
        "mapped-bytes": 168,
    }
    geometry_names = list(geometry) + ["enabled-reference", "reference-boot-perf-index"]
    runtime_names = ["firmware-enabled", "performance-index", "requested-lval",
                     "requested-frequency-hz"]
    lut_names = ["lut" + str(row) + "-" + suffix for row in range(40)
                 for suffix in ("frequency-word", "nominal-frequency-hz")]
    observation_names = geometry_names + runtime_names + lut_names
    fields = (
        ("profile", 0, 4), ("instance_id", 4, 4),
        ("enable_address", 8, 8), ("frequency_table_address", 16, 8),
        ("performance_address", 24, 8), ("enable_resource_size", 32, 4),
        ("frequency_table_resource_size", 36, 4), ("performance_resource_size", 40, 4),
        ("lut_row_size", 44, 4), ("boot_perf_index", 48, 4),
        ("lut_identity", 52, 4), ("lut_entries", 56, 4),
        ("valid_perf_count", 60, 4), ("nominal_xo_hz", 64, 4),
        ("performance_index", 68, 4),
    )

    expected_report = {
        "schema": "tb371fc-devfreq-qcom-fw-v1", "passed": True,
        "binary_unchanged": True, "machine": machine, "test_sha256": test_sha,
        "source_commit": "94304e12a3509eac7b3716bf574ce0b1fcaacbae",
        "source_path": "drivers/devfreq/devfreq_qcom_fw.c",
        "source_sha256": "745457c2a20c90521772ef29c5f572570f481541d01ef427707d3da35bc5eb16",
        "actual_dtb_sha256": "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207",
        "actual_dt_capture_sha256": "3b4d663de9e5ea33f5a86fc7d9f49f26223a6217657594423691d59f0ded124b",
        "expected_checks": 20, "expected_completed_migrations": 2,
        "expected_rejected_migrations": 2, "expected_corrupted_stream_rejections": 2,
        "vmstate_version": 1, "vmstate_payload_bytes_per_device": 72,
        "device_count": 1, "register_count": 42, "sparse_window_count": 3,
        "mapped_bytes": 168, "lut_rows": 40, "lut_row_bytes": 4,
        "valid_perf_states": 4, "software_voter_count": 4,
        "reference_nominal_xo_hz": nominal_xo,
        "reference_profile_is_default": False, "reference_lut_is_synthetic": True,
        "factory_lut_observed": False, "hardware_voter_slots_implemented": False,
        "clock_control_implemented": False, "actual_cache_frequency_measured": False,
        "irq_implemented": False, "timer_implemented": False,
        "counter_implemented": False, "voltage_control_implemented": False,
        "firmware_execution_observed": False, "guest_cpu_executed": False,
        "native_kernel_probe_observed": False, "android_boot_passed": False,
    }
    for key, expected in expected_report.items():
        same(data.get(key), expected, "report." + key)
    binary_hash = data.get("qemu_sha256_before")
    need(type(binary_hash) is str and re.fullmatch(r"[0-9a-f]{64}", binary_hash),
         "malformed binary SHA256")
    same(data.get("qemu_sha256_after"), binary_hash, "unchanged binary SHA256")
    need(type(data.get("qemu")) is str and data["qemu"].startswith("/"),
         "missing absolute QEMU path in report")

    def records(key, count):
        rows = data.get(key)
        need(type(rows) is list and len(rows) == count, key + " count")
        need(all(type(row) is dict and type(row.get("name")) is str for row in rows),
             key + " malformed record")
        need(len({row["name"] for row in rows}) == count, key + " duplicate names")
        for row in rows:
            same(row.get("passed"), True, key + "." + row["name"])
        return {row["name"]: row for row in rows}

    checks = records("checks", 20)
    expected_checks = [
        "explicit_reference_exposes_all_forty_rows_and_three_actual_dt_resources",
        "literal_production_parser_uses_frequency_only_and_stops_at_the_duplicate",
        "every_legal_index_changes_requested_feedback_with_little_endian_words",
        "four_software_voters_write_the_literal_maximum_without_hidden_hardware_votes",
        "requested_state_does_not_invent_autonomous_clock_or_time_feedback",
        "system_reset_restores_boot_request_with_an_independent_gcc_witness",
        "invalid_indices_fail_atomically_instead_of_selecting_terminator_rows",
        "read_only_handoff_and_table_and_qom_properties_cannot_forge_runtime",
        "strict_access_widths_and_sparse_holes_preserve_real_word_transactions",
        "default_unknown_profile_rejects_all_forty_two_words_and_runtime_getters",
        "explicit_boot_request_and_realization_constraints_are_observable",
        "paused_tcg_uses_the_same_request_registers_without_guest_cpu_execution",
        "v1_request_state_restores_before_first_devfreq_mmio",
        "restored_request_accepts_independent_new_indices_without_persisting_old_votes",
        "v1_nonzero_boot_policy_and_distinct_live_request_restore_before_mmio",
        "restored_live_request_changes_and_system_reset_reapplies_explicit_boot_policy",
        "real_migration_rejects_reference_to_unknown_profile",
        "real_migration_rejects_unknown_to_reference_profile",
        "real_migration_rejects_single_field_corruption_performance_index",
        "real_migration_rejects_single_field_corruption_lut_identity",
    ]
    same(list(checks), expected_checks, "reported exact check order")
    completed = records("migrations", 2)
    rejected = records("rejected_migrations", 2)
    corrupted = records("corrupted_stream_rejections", 2)
    qmp_cache, qtest_cache = {}, {}

    def qmp(directory):
        relative = directory + "/qmp.log"
        if relative in qmp_cache:
            return qmp_cache[relative]
        entries, pending = [], None
        for number, line in enumerate(read(relative).decode("utf-8").splitlines(), 1):
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
                need(pending is not None, relative + " unmatched reply")
                error = line.startswith("< ERROR ")
                response = line[8:] if error else json.loads(line[2:])
                entries.append({"command": pending[0], "arguments": pending[1],
                                "request_line": pending[2], "response_line": number,
                                "error": error, "response": response})
                pending = None
            else:
                need(False, relative + " unframed line " + str(number))
        need(pending is None, relative + " unanswered request")
        qmp_cache[relative] = entries
        return entries

    def qtest(directory):
        relative = directory + "/qtest.log"
        if relative in qtest_cache:
            return qtest_cache[relative]
        pairs, clocks, pending, now = [], [], None, 0
        for number, line in enumerate(read(relative).decode("utf-8").splitlines(), 1):
            if not line:
                continue
            if line.startswith("> "):
                need(pending is None, relative + " overlapping requests")
                pending = (line[2:], number)
            elif line.startswith("< OK"):
                need(pending is not None, relative + " unmatched reply")
                command, request_line = pending
                words, reply = command.split(), line[4:].split()
                need(words, relative + " empty request")
                if words[0] in ("clock_step", "clock_set"):
                    need(len(words) == 2 and len(reply) == 1,
                         relative + " malformed virtual clock exchange")
                    argument, returned = int(words[1], 0), int(reply[0], 0)
                    expected = now + argument if words[0] == "clock_step" else argument
                    need(returned == expected and returned >= now,
                         relative + " invalid virtual clock result")
                    clocks.append({"command": words[0], "argument_ns": argument,
                                   "returned_virtual_ns": returned, "request_line": request_line})
                    now = returned
                pairs.append({"words": words, "reply": reply, "request_line": request_line,
                              "response_line": number, "virtual_ns": now})
                pending = None
            else:
                need(False, relative + " unframed line " + str(number))
        need(pending is None, relative + " unanswered request")
        result = (pairs, {**provenance(relative), "clock_events": clocks,
                          "last_virtual_ns": now,
                          "clock_values_are_fixture_witnesses_not_device_state": True})
        qtest_cache[relative] = result
        return result

    def command_proof(directory, reference, boot_index, incoming):
        argv = document(directory + "/command.json")
        need(type(argv) is list and all(type(arg) is str for arg in argv),
             directory + " invalid command file")
        prefix = [data["qemu"], "-machine", machine, "-accel", "qtest", "-m", "128M",
                  "-display", "none", "-monitor", "none", "-serial", "null", "-S",
                  "-d", "unimp,guest_errors,invalid_mem", "-global",
                  device_type + ".enabled-reference=" + ("on" if reference else "off")]
        if boot_index:
            prefix += ["-global", device_type + ".reference-boot-perf-index=" + str(boot_index)]
        same(argv[:len(prefix)], prefix, directory + " explicit command contract")
        tail = argv[len(prefix):]
        need(len(tail) == (8 if incoming else 6), directory + " unexpected command options")
        same(tail[::2], ["-qmp", "-qtest", "-qtest-log"] + (["-incoming"] if incoming else []),
             directory + " transport flags")
        need(re.fullmatch(r"unix:/[^,]+/m,server=on,wait=off", tail[1]) is not None and
             re.fullmatch(r"unix:/[^,]+/t,server=on,wait=off", tail[3]) is not None,
             directory + " transport socket shape")
        same(tail[5], "/dev/null", directory + " qtest log option")
        if incoming:
            same(tail[7], "defer", directory + " deferred incoming")
        return provenance(directory + "/command.json")

    def filename_argument(arguments, expected, label):
        need(type(arguments) is dict, label + " migration arguments")
        if "channels" in arguments:
            channels = arguments["channels"]
            need(type(channels) is list and len(channels) == 1,
                 label + " migration channel count")
            channel = channels[0]
            need(type(channel) is dict and channel.get("channel-type") == "main",
                 label + " migration channel type")
            address = channel.get("addr")
            need(type(address) is dict and address.get("transport") == "file",
                 label + " migration transport")
            filename = address.get("filename")
        else:
            uri = arguments.get("uri")
            need(type(uri) is str and uri.startswith("file:"), label + " file URI")
            filename = uri[5:]
        need(type(filename) is str and filename.rsplit("/", 1)[-1] == expected,
             label + " migration filename")
        return filename

    def completed_log(directory, filename, incoming, expected):
        entries = qmp(directory)
        command = "migrate-incoming" if incoming else "migrate"
        positions = [i for i, entry in enumerate(entries) if entry["command"] == command]
        need(len(positions) == 1, directory + " migration request count")
        position = positions[0]
        need(not entries[position]["error"], directory + " migration command failed")
        filename_argument(entries[position]["arguments"], filename, directory)
        same(expected.get("status") if type(expected) is dict else None,
             "completed", directory + " expected completed result")
        done = [i for i in range(position + 1, len(entries))
                if entries[i]["command"] == "query-migrate" and not entries[i]["error"] and
                entries[i]["response"] == expected]
        need(done, directory + " actual completed query-migrate differs from report")
        if incoming:
            need(not any(item["command"] == "cont" for item in entries[:position]),
                 directory + " continued before loading")
        else:
            need(any(item["command"] == "stop" and not item["error"]
                     for item in entries[:position]), directory + " no source stop")
        return entries, position, done[0]

    def status_witness(entries, reported, label):
        need(type(reported) is dict and reported.get("running") is False,
             label + " not stopped")
        need(any(item["command"] == "query-status" and not item["error"] and
                 item["response"] == reported for item in entries),
             label + " missing raw query-status")

    def expected_flat(reference, boot_index, index):
        result = {**geometry, "enabled-reference": reference,
                  "reference-boot-perf-index": boot_index}
        if reference:
            result.update({"firmware-enabled": True, "performance-index": index,
                           "requested-lval": multipliers[index],
                           "requested-frequency-hz": nominal_rates[index]})
            for row in range(40):
                selected = min(row, 3)
                result["lut" + str(row) + "-frequency-word"] = 0x40000000 | multipliers[selected]
                result["lut" + str(row) + "-nominal-frequency-hz"] = nominal_rates[selected]
        return result

    def observations(entries, start, end, reference, boot_index, index, copies, label):
        window = entries[start:end]
        need(not any(item["command"] in ("qom-set", "system_reset", "cont", "migrate", "migrate-incoming")
                     for item in window), label + " mutating command inside observation window")
        getters = [item for item in window if item["command"] == "qom-get"]
        same(len(getters), len(observation_names) * copies, label + " getter count")
        expected, snapshots = expected_flat(reference, boot_index, index), []
        for copy in range(copies):
            observed, errors = {}, {}
            for name, item in zip(observation_names, getters[copy * 101:(copy + 1) * 101]):
                same(item["arguments"], {"path": device_path, "property": name},
                     label + " exact getter order")
                if name in expected:
                    same(item["error"], False, label + " unexpected QOM failure " + name)
                    same(item["response"], expected[name], label + " raw QOM " + name)
                    observed[name] = item["response"]
                else:
                    same(item["error"], True, label + " unknown runtime was readable " + name)
                    need(type(item["response"]) is str and
                         device_type + ": unknown profile" in item["response"],
                         label + " wrong unknown-runtime diagnostic " + name)
                    errors[name] = item["response"]
            result = {
                "geometry": {name: observed[name] for name in geometry_names},
                "runtime": {name: observed[name] for name in runtime_names} if reference else {},
                "runtime_errors": errors,
                "lut": [{"row": row,
                         "word": observed["lut" + str(row) + "-frequency-word"],
                         "nominal_frequency_hz": observed["lut" + str(row) + "-nominal-frequency-hz"]}
                        for row in range(40)] if reference else [],
            }
            snapshots.append(result)
        for snapshot in snapshots[1:]:
            same(snapshot, snapshots[0], label + " repeated complete QOM is unchanged")
        return snapshots[0], {"getter_count": len(getters), "complete_snapshots": copies,
                              "first_qmp_request_line": getters[0]["request_line"],
                              "last_qmp_response_line": getters[-1]["response_line"]}

    def expected_state(reference, boot_index, index):
        return {
            "profile": int(reference), "instance_id": 0,
            "enable_address": enable, "frequency_table_address": table,
            "performance_address": perf,
            "enable_resource_size": 4, "frequency_table_resource_size": 160,
            "performance_resource_size": 4, "lut_row_size": 4,
            "boot_perf_index": boot_index, "lut_identity": lut_identity,
            "lut_entries": 40, "valid_perf_count": 4, "nominal_xo_hz": nominal_xo,
            "performance_index": index,
        }

    def find_section(raw, label):
        same(raw[:8], b"QEVM\x00\x00\x00\x03", label + " QEMU stream header")
        name = device_type.encode("ascii")
        marker = bytes([len(name)]) + name
        candidates, cursor = [], 0
        while True:
            found = raw.find(marker, cursor)
            if found < 0:
                break
            cursor = found + 1
            header, metadata = found - 5, found + len(marker)
            offset = metadata + 8
            if header < 8 or raw[header] not in (1, 4) or offset + payload_bytes > len(raw):
                continue
            instance, version = struct.unpack_from(">II", raw, metadata)
            candidates.append((header, offset, instance, version))
        need(len(candidates) == 1, label + " lacks exactly one FULL/START controller section")
        header, offset, instance, version = candidates[0]
        same(instance, 0, label + " section instance")
        same(version, 1, label + " section version")
        payload = raw[offset:offset + payload_bytes]
        # Independent offset/width decoder. No acceptance-test module or its
        # packed_device/actual_section implementation is imported or called.
        state = {name: int.from_bytes(payload[at:at + width], "big")
                 for name, at, width in fields}
        return {
            "instance_id": instance, "section_version": version, "section_name": device_type,
            "section_type": raw[header], "section_id": struct.unpack_from(">I", raw, header + 1)[0],
            "section_header_offset": header, "payload_offset": offset,
            "payload_bytes": payload_bytes, "payload_sha256": sha(payload),
            "decoded_fields": state,
        }

    def section_receipt(rows, section, raw, filename, reference):
        need(type(rows) is list and len(rows) == 1, filename + " reported section count")
        receipt = rows[0]
        offset = section["payload_offset"]
        expected = {key: section[key] for key in (
            "instance_id", "section_version", "section_name", "section_header_offset",
            "section_id", "payload_offset", "payload_bytes", "decoded_fields")}
        payload_file = filename + ".instance0.payload.bin"
        expected.update({
            "payload_hex": raw[offset:offset + payload_bytes].hex(), "payload_file": payload_file,
            "all_serialized_fields_verified": True,
            "runtime_matches_actual_source_qom": reference,
            "unknown_runtime_zero_invariant_verified": not reference,
        })
        same(receipt, expected, filename + " independently decoded receipt")
        same(read(payload_file), raw[offset:offset + payload_bytes], filename + " saved actual payload bytes")
        same(document(filename + ".sections.json"), {
            "stream_sha256": sha(raw), "stream_bytes": len(raw),
            "field_layout": [{"name": name, "offset": at, "bytes": width} for name, at, width in fields],
            "sections": [expected],
        }, filename + " saved section metadata")

    def memory_accesses(pairs, operation, address):
        output = []
        for item in pairs:
            words = item["words"]
            if words[0] == operation and len(words) >= 2 and int(words[1], 0) == address:
                if operation == "writel":
                    need(len(words) == 3 and not item["reply"], "malformed write32 exchange")
                    value = int(words[2], 0)
                else:
                    need(len(words) == 2 and len(item["reply"]) == 1, "malformed read32 exchange")
                    value = int(item["reply"][0], 0)
                output.append((item["request_line"], value))
        return output

    def evidence_paths(record, directory, label):
        for key, expected in {
            "source_qom_before_file": directory + "/source-qom-before-save.json",
            "source_qom_after_file": directory + "/source-qom-after-save.json",
            "source_query_migrate_file": directory + "/source-query-migrate.json",
            "source_qom_observation_did_not_change_request": True,
        }.items():
            same(record.get(key), expected, label + "." + key)

    cases = [
        {"name": "v1_requested_index_and_ram_roundtrip_before_first_mmio",
         "file": "devfreq-fw-v1-request-index.migration",
         "source": "migration-request-source", "target": "migration-request-target",
         "boot": 0, "index": 1, "source_time": 137, "target_time": 987654321,
         "target_step": 999, "target_before": 3, "witness": 0x44465731},
        {"name": "v1_nonzero_boot_policy_and_live_request_roundtrip",
         "file": "devfreq-fw-v1-explicit-boot-policy.migration",
         "source": "migration-boot-policy-source", "target": "migration-boot-policy-target",
         "boot": 2, "index": 3, "source_time": 12345, "target_time": 73,
         "target_step": 1, "target_before": 0, "witness": 0x44465732},
    ]
    unknown_file = "devfreq-fw-v1-unknown-profile.migration"
    bad_files = ["devfreq-fw-v1-corrupted-performance-index.migration",
                 "devfreq-fw-v1-corrupted-lut-identity.migration"]
    expected_files = [case["file"] for case in cases] + [unknown_file] + bad_files
    actual_files = [name[len(base) + 1:] for name in members if name.startswith(base + "/") and
                    "/" not in name[len(base) + 1:] and name.endswith(".migration")]
    same(sorted(actual_files), sorted(expected_files), "exactly five root migration files")
    same(list(completed), [case["name"] for case in cases], "completed roundtrip names")
    same(list(rejected), ["reference-to-unknown", "unknown-to-reference"], "profile cases")
    same(list(corrupted), ["performance-index", "lut-identity"], "corrupted-field cases")
    streams, decoded_sections, log_evidence = {}, [], {}
    originals, sections, source_results, source_proofs = {}, {}, {}, {}

    def source_proof(case, reference, record):
        filename, directory = case["file"], case["source"]
        raw = read(filename)
        originals[filename] = raw
        command_info = command_proof(directory, reference, case["boot"], False)
        sent = document(directory + "/source-query-migrate.json")
        same(record.get("source"), sent, directory + " reported source completion")
        entries, position, done = completed_log(directory, filename, False, sent)
        stop = max(i for i in range(position) if entries[i]["command"] == "stop" and not entries[i]["error"])
        before, before_proof = observations(entries, stop + 1, position, reference,
                                            case["boot"], case["index"], 2, directory + " before save")
        after, after_proof = observations(entries, done + 1, len(entries), reference,
                                          case["boot"], case["index"], 1, directory + " after save")
        same(after, before, directory + " before/after source state")
        before_json = document(directory + "/source-qom-before-save.json")
        after_json = document(directory + "/source-qom-after-save.json")
        same(set(before_json), {"query_status", "source_virtual_ns", "device",
                                "repeated_qom_observation_unchanged"}, directory + " before metadata keys")
        same(set(after_json), {"query_status", "source_virtual_ns", "device"}, directory + " after metadata keys")
        for stage, reported, observed, window in (
                ("before", before_json, before, entries[stop + 1:position]),
                ("after", after_json, after, entries[done + 1:])):
            same(reported.get("device"), observed, directory + " " + stage + " JSON/raw QMP")
            same(reported.get("source_virtual_ns"), case["source_time"], directory + " " + stage + " fixture time")
            status_witness(window, reported.get("query_status"), directory + " " + stage + " stopped status")
        same(before_json.get("repeated_qom_observation_unchanged"), True, directory + " pure repeated observation")
        same(after_json["query_status"].get("status"), "postmigrate", directory + " source postmigrate")
        pairs, clock_info = qtest(directory)
        same([[event["command"], event["argument_ns"]] for event in clock_info["clock_events"]],
             [["clock_step", case["source_time"]]], directory + " source fixture clock")
        same(clock_info["last_virtual_ns"], case["source_time"], directory + " final fixture time")
        same([value for _, value in memory_accesses(pairs, "writel", ram)],
             [case["witness"]], directory + " actual RAM witness write")
        same([value for _, value in memory_accesses(pairs, "writel", perf)],
             [case["index"]] if reference else [], directory + " actual source PERF write")
        section = find_section(raw, filename)
        same(section["decoded_fields"], expected_state(reference, case["boot"], case["index"]),
             filename + " every serialized field")
        if reference:
            same(section["decoded_fields"]["performance_index"], before["runtime"]["performance-index"],
                 filename + " mutable field matches actual source QOM")
        else:
            same(section["decoded_fields"]["performance_index"], 0, filename + " canonical unknown runtime")
            same(len(before["runtime_errors"]), 84, filename + " actually inaccessible runtime properties")
        section_receipt(record["sections"], section, raw, filename, reference)
        evidence_paths(record, directory, filename)
        same(record.get("source_virtual_ns"), case["source_time"], filename + " reported source time")
        for key, expected in {
            "snapshot_file": filename, "snapshot_sha256": sha(raw), "snapshot_bytes": len(raw),
            "source_status": "completed", "stream_modified": False,
        }.items():
            same(record.get(key), expected, filename + "." + key)
        sections[filename], source_results[filename] = section, sent
        decoded_sections.append({
            **section, "file": filename,
            "purpose": "completed-roundtrip-source" if reference else "reverse-profile-rejection-source-only",
            "all_fields_independently_decoded": True,
            "immutable_prefix_matches_fixed_board_boot_and_rom_contract": True,
            "immutable_prefix_claimed_as_mutable_qom_state": False,
            "mutable_performance_index_matches_actual_source_qom": reference,
            "unknown_runtime_getters_actually_refused_access": not reference,
            "unknown_runtime_zero_is_serialization_invariant": not reference,
        })
        proof = {
            "command": command_info, "qmp": provenance(directory + "/qmp.log"), "qtest": clock_info,
            "before_save": before_proof, "after_save": after_proof,
            "source_query_migrate": provenance(directory + "/source-query-migrate.json"),
            "source_qom_before": provenance(directory + "/source-qom-before-save.json"),
            "source_qom_after": provenance(directory + "/source-qom-after-save.json"),
            "actual_source_save_completed": True, "source_stopped_for_observations": True,
            "repeat_observation_and_save_do_not_change_request": True,
            "actual_ram_witness_write": case["witness"],
            "unknown_runtime_error_count": 0 if reference else 84,
        }
        source_proofs[filename] = proof
        streams[filename] = {
            **provenance(filename), "source_directory": directory,
            "purpose": "completed-roundtrip-source" if reference else "reverse-profile-rejection-source-only",
            "actual_source_save_completed": True, "completed_roundtrip": reference,
            "source_virtual_ns_fixture_only": case["source_time"],
        }
        return before, proof

    for case in cases:
        record = completed[case["name"]]
        filename, target = case["file"], case["target"]
        for key, expected in {
            "source_directory": case["source"], "target_directory": target,
            "target_status": "completed", "destination_virtual_ns": case["target_time"],
            "different_virtual_origins": True, "device_sections_verified": 1,
            "payload_bytes_per_device": 72, "source_boot_perf_index": case["boot"],
            "target_devfreq_mmio_before_first_observation": False,
            "target_qmp_cont_before_first_observation": False,
            "destination_state_was_different_before_load": True,
            "continued_after_restore": True,
            "target_qom_before_first_mmio_file": target + "/destination-before-first-devfreq-mmio.json",
            "target_query_migrate_file": target + "/destination-query-migrate.json",
        }.items():
            same(record.get(key), expected, case["name"] + "." + key)
        before, source_log_proof = source_proof(case, True, record)
        command_info = command_proof(target, True, case["boot"], True)
        got = document(target + "/destination-query-migrate.json")
        same(record.get("destination"), got, target + " reported destination completion")
        entries, load, done = completed_log(target, filename, True, got)
        starts = [i for i in range(load) if entries[i]["command"] == "qom-get" and
                  entries[i]["arguments"] == {"path": device_path, "property": "instance-id"}]
        need(len(starts) == 1, target + " unique complete pre-load QOM snapshot")
        previous, previous_proof = observations(entries, starts[0], load, True, case["boot"],
                                                case["target_before"], 1, target + " before incoming")
        continues = [i for i in range(done + 1, len(entries)) if entries[i]["command"] == "cont"]
        need(len(continues) == 1 and not entries[continues[0]]["error"], target + " actual continuation count")
        cont = continues[0]
        restored, restored_proof = observations(entries, done + 1, cont, True, case["boot"],
                                                case["index"], 1, target + " first post-load QOM")
        same(restored, before, target + " restored full device snapshot")
        need(previous["runtime"] != restored["runtime"], target + " pre-load request already matched")
        previous_json = document(target + "/destination-before-load.json")
        target_json = document(target + "/destination-before-first-devfreq-mmio.json")
        for label, saved, observed, window in (
                ("pre-load", previous_json, previous, entries[starts[0]:load]),
                ("first restored", target_json, restored, entries[done + 1:cont])):
            same(saved.get("device"), observed, target + " " + label + " JSON/raw QMP")
            same(saved.get("destination_virtual_ns"), case["target_time"], target + " " + label + " fixture time")
            status_witness(window, saved.get("query_status"), target + " " + label + " stopped status")
        for key, expected in {
            "ram_witness": case["witness"], "devfreq_mmio_since_incoming": False,
            "qmp_cont_since_incoming": False,
        }.items():
            same(target_json.get(key), expected, target + " first-observation metadata " + key)
        pairs, clock_info = qtest(target)
        same([[event["command"], event["argument_ns"]] for event in clock_info["clock_events"]],
             [["clock_set", case["target_time"]], ["clock_step", case["target_step"]]],
             target + " different-origin fixture clock exchanges")
        same([value for _, value in memory_accesses(pairs, "writel", ram)], [0xdeadbeef],
             target + " distinguishable pre-load RAM")
        ram_reads = memory_accesses(pairs, "readl", ram)
        same([value for _, value in ram_reads], [case["witness"]], target + " actual restored RAM")
        later_getters = [entry for entry in entries[cont + 1:] if entry["command"] == "qom-get"]
        need(len(later_getters) >= 4, target + " missing first continued request observation")
        continued_expected = expected_flat(True, case["boot"], case["index"])
        for name, item in zip(runtime_names, later_getters[:4]):
            same(item["arguments"], {"path": device_path, "property": name},
                 target + " first continued pure getter")
            same(item["error"], False, target + " first continued getter error")
            same(item["response"], continued_expected[name], target + " first continued request value")
        perf_writes = memory_accesses(pairs, "writel", perf)
        expected_writes = [3, 2, 0] if case["boot"] == 0 else [0, 0]
        same([value for _, value in perf_writes], expected_writes, target + " live request writes")
        same([value for _, value in memory_accesses(pairs, "readl", perf)],
             [3, 1, 2, 0] if case["boot"] == 0 else [0, 0, 2], target + " actual PERF readbacks")
        need(perf_writes[0][0] < ram_reads[0][0] < perf_writes[1][0],
             target + " RAM witness not between pre-load and subsequent live writes")
        need(not any(item["command"] == "qom-set" for item in entries),
             target + " attempted host state repair")
        if case["boot"] == 2:
            same(record.get("restored_explicit_boot_policy_verified_by_actual_system_reset"), True,
                 target + " reset report")
            resets = [i for i, item in enumerate(entries) if item["command"] == "system_reset"]
            need(len(resets) == 1 and resets[0] > cont and not entries[resets[0]]["error"],
                 target + " missing successful actual reset request")
            reset = resets[0]
            reset_getters = [item for item in entries[reset + 1:] if item["command"] == "qom-get"]
            same(len(reset_getters), 4, target + " reset runtime getter count")
            reset_view = expected_flat(True, 2, 2)
            for name, item in zip(runtime_names, reset_getters):
                same(item["arguments"], {"path": device_path, "property": name}, target + " reset getter")
                same(item["error"], False, target + " reset getter error")
                same(item["response"], reset_view[name], target + " reset boot policy")
            same([value for _, value in memory_accesses(pairs, "writel", 0x00100004)], [0x33],
                 target + " independent GCC reset witness write")
            witness_reads = memory_accesses(pairs, "readl", 0x00100004)
            need(len(witness_reads) >= 2 and witness_reads[0][1] == 0x33 and witness_reads[-1][1] == 0 and
                 all(value in (0, 0x33) for _, value in witness_reads), target + " GCC reset witness readbacks")
        else:
            need(not any(item["command"] == "system_reset" for item in entries), target + " unexpected reset")
        streams[filename].update({"target_directory": target, "actual_destination_completed": True,
                                  "destination_virtual_ns_fixture_only": case["target_time"],
                                  "serialized_device_time_fields": 0})
        log_evidence[case["name"]] = {
            "source": source_log_proof, "target_command": command_info,
            "target_qmp": provenance(target + "/qmp.log"), "target_qtest": clock_info,
            "target_query_migrate": provenance(target + "/destination-query-migrate.json"),
            "target_pre_load": previous_proof, "target_first_restored": restored_proof,
            "target_qom_before_load": provenance(target + "/destination-before-load.json"),
            "target_qom_before_first_mmio": provenance(target + "/destination-before-first-devfreq-mmio.json"),
            "actual_source_and_destination_completed": True,
            "destination_was_stopped_and_had_different_request": True,
            "first_restored_complete_qom_precedes_cont_in_qmp_log": True,
            "actual_ram_witness_read": case["witness"],
            "first_continued_request_and_new_mmio_request_readbacks_verified": True,
            "reset_boot_policy_and_gcc_witness_verified": case["boot"] == 2,
            "cross_channel_order_scope":
                "QMP and QTest lack shared timestamps; before-first-MMIO ordering is bound to the pinned harness and its receipt. Each channel's requests and results are independently checked.",
            "device_has_clock_timer_counter_or_epoch_state": False,
        }

    unknown_case = {"file": unknown_file, "source": "migration-unknown-source", "boot": 0,
                    "index": 0, "source_time": 4567, "witness": 0x44465730}
    _, unknown_proof = source_proof(unknown_case, False, rejected["unknown-to-reference"])
    log_evidence["unknown_source"] = {**unknown_proof, "completed_roundtrip": False,
                                      "used_only_for_reverse_profile_rejection": True}

    def failed_incoming(directory, filename, record, diagnostic, reference):
        command_info = command_proof(directory, reference, 0, True)
        for key, expected in {
            "passed": True, "source_status": "completed", "destination_exit": 1,
            "request_sent": True, "specific_load_failure_observed": True,
            "device": device_type, "expected_diagnostic": diagnostic,
        }.items():
            same(record.get(key), expected, directory + "." + key)
        entries = qmp(directory)
        requests = [item for item in entries if item["command"] == "migrate-incoming"]
        need(len(requests) == 1, directory + " incoming request count")
        request = requests[0]
        filename_argument(request["arguments"], filename, directory)
        same(record.get("command_error"), request["response"] if request["error"] else None,
             directory + " reported command result/error")
        need(not any(item["command"] in ("cont", "qom-set", "system_reset") for item in entries),
             directory + " execution/state mutation during rejected load")
        need(not any(item["command"] == "query-migrate" and not item["error"] and
                     type(item["response"]) is dict and item["response"].get("status") == "completed"
                     for item in entries), directory + " unexpected completed load")
        text = read(directory + "/qemu.stderr").decode("utf-8")
        need(diagnostic in text and "load of migration failed" in text and
             re.search(r"error while loading state[^\n]*" + re.escape(device_type), text) is not None,
             directory + " missing specific actual device-load failure")
        return {
            "command": command_info, "qmp": provenance(directory + "/qmp.log"),
            "stderr": provenance(directory + "/qemu.stderr"),
            "incoming_request_observed": True, "specific_device_failure_in_actual_stderr": True,
            "expected_diagnostic": diagnostic, "reported_destination_exit": 1,
            "process_exit_independently_reexecuted": False,
        }

    for name, filename, reference, diagnostic in (
            ("reference-to-unknown", cases[0]["file"], False, "0 != 1"),
            ("unknown-to-reference", unknown_file, True, "1 != 0")):
        record = rejected[name]
        directory = "rejected-" + name
        for key, expected in {
            "profile_mismatch": True, "target_reference": reference,
            "target_directory": directory, "original_stream_hash_unchanged": True,
            "snapshot_file": filename, "snapshot_sha256": sha(originals[filename]),
            "snapshot_bytes": len(originals[filename]), "stream_modified": False,
            "source_status": "completed",
        }.items():
            same(record.get(key), expected, name + "." + key)
        same(record.get("source"), source_results[filename], name + " actual completed source")
        source_case = cases[0] if name == "reference-to-unknown" else unknown_case
        evidence_paths(record, source_case["source"], name)
        same(record.get("source_virtual_ns"), source_case["source_time"], name + " source fixture time")
        section_receipt(record.get("sections"), sections[filename], originals[filename], filename,
                        name == "reference-to-unknown")
        proof = failed_incoming(directory, filename, record, diagnostic, reference)
        streams[filename].setdefault("profile_rejections", []).append(proof)
        log_evidence[name] = proof

    original_file = cases[0]["file"]
    original, original_section = originals[original_file], sections[original_file]
    for kind, relative, old, new, state_name, diagnostic in (
            ("performance-index", 68, 1, 5, "performance_index",
             device_type + ": invalid migrated performance index"),
            ("lut-identity", 52, lut_identity, lut_identity ^ 1, "lut_identity",
             "44463031 != 44463030")):
        record = corrupted[kind]
        filename, directory = "devfreq-fw-v1-corrupted-" + kind + ".migration", "corrupted-" + kind
        changed = read(filename)
        field = original_section["payload_offset"] + relative
        same(len(changed), len(original), filename + " same total bytes")
        same(changed[:field], original[:field], filename + " identical full prefix")
        same(changed[field + 4:], original[field + 4:], filename + " identical full suffix")
        same(int.from_bytes(original[field:field + 4], "big"), old, filename + " original raw field")
        same(int.from_bytes(changed[field:field + 4], "big"), new, filename + " changed raw field")
        differences = [{"offset": i, "old_byte": left, "new_byte": right}
                       for i, (left, right) in enumerate(zip(original, changed)) if left != right]
        need(len(differences) == 1 and (old ^ new).bit_count() == 1 and
             field <= differences[0]["offset"] < field + 4, filename + " not precisely one field/bit")
        section = find_section(changed, filename)
        expected = dict(original_section["decoded_fields"])
        expected[state_name] = new
        same(section["decoded_fields"], expected, filename + " all changed/unchanged fields")
        for key in ("section_type", "section_id", "section_header_offset", "payload_offset",
                    "payload_bytes", "instance_id", "section_version", "section_name"):
            same(section[key], original_section[key], filename + " unchanged section " + key)
        start = original_section["payload_offset"]
        mutation = {
            "instance_id": 0, "field": kind,
            "section_header_offset": original_section["section_header_offset"],
            "payload_offset": start, "field_relative_offset": relative, "field_offset": field,
            "modified_field_bytes": 4, "old_value": old, "new_value": new,
            "old_field_hex": original[field:field + 4].hex(), "new_field_hex": changed[field:field + 4].hex(),
            "original_payload_hex": original[start:start + payload_bytes].hex(),
            "modified_payload_hex": changed[start:start + payload_bytes].hex(),
            "complete_file_byte_differences": differences,
            "only_one_field_modified": True, "only_one_bit_modified": True,
        }
        same(record.get("mutation"), mutation, filename + " mutation metadata")
        for key, value in {
            "stream_modified": True, "source_status": "completed",
            "original_stream_file": original_file, "original_stream_sha256": sha(original),
            "original_stream_bytes": len(original), "corrupted_stream_file": filename,
            "corrupted_stream_sha256": sha(changed), "corrupted_stream_bytes": len(changed),
            "original_stream_hash_unchanged": True, "target_directory": directory,
        }.items():
            same(record.get(key), value, filename + "." + key)
        same(record.get("source"), source_results[original_file], filename + " actual completed source")
        section_receipt(record.get("original_sections"), original_section, original, original_file, True)
        proof = failed_incoming(directory, filename, record, diagnostic, True)
        decoded_sections.append({
            **section, "file": filename, "purpose": "specific-corrupted-copy-rejection",
            "all_fields_independently_decoded": True,
            "immutable_prefix_matches_fixed_board_boot_and_rom_contract": kind != "lut-identity",
            "intentional_invalid_field": state_name,
            "all_other_fields_identical_to_actual_source": True,
            "only_one_bit_in_one_four_byte_field_modified": True,
        })
        streams[filename] = {
            **provenance(filename), "purpose": "specific-corrupted-copy-rejection",
            "completed_roundtrip": False, "original_file": original_file,
            "original_sha256": sha(original), "field": state_name,
            "field_offset": field, "old_raw_value": old, "new_raw_value": new,
            "actual_file_byte_differences": differences,
            "only_one_bit_in_one_four_byte_field_modified": True, "failure_proof": proof,
        }
        log_evidence["corrupted-" + kind] = proof

    for filename, raw in originals.items():
        same(z.read(base + "/" + filename), raw, filename + " original ZIP stream remains unchanged")
    need(len(streams) == 5 and len(decoded_sections) == 5 and len(originals) == 3,
         "five streams, five complete device sections and three unmodified sources required")
    log_evidence["scope"] = {
        "test_sha256_pinned_to_report": test_sha,
        "actual_completed_roundtrips": 2, "actual_profile_rejections": 2,
        "actual_single_field_corruption_rejections": 2,
        "unknown_source_is_a_third_save_not_a_third_completed_roundtrip": True,
        "immutable_bytes_per_section": 68, "mutable_bytes_per_section": 4,
        "runtime_mutable_field": "performance_index",
        "clock_timer_counter_epoch_fields": 0,
        "source_qom_getter_count_per_complete_snapshot": 101,
        "unknown_runtime_errors_per_complete_snapshot": 84,
        "binary_source_clean_receipt_bound_by_outer_worker": True,
        "test_module_imported_or_decoder_called": False,
        "guest_or_process_executed_by_verifier": False,
        "requested_nominal_frequency_is_actual_cache_frequency": False,
        "native_kernel_probe_or_android_claimed_by_component": False,
    }
    return {
        "devfreq_fw_streams_from_zip": streams,
        "devfreq_fw_decoded_sections": decoded_sections,
        "devfreq_fw_log_evidence": log_evidence,
    }
