def verify_rpmh_wait_streams(z, info, data):
    """Independently verify actual RPMh WAIT CI streams and retained wire logs.

    This function only reads ZIP members and parses bytes, JSON and logs with
    the standard library. It never imports the test, extracts or executes a
    member, starts a process, contacts a service, or drives a guest. The outer
    worker separately binds the archive binary to the formal CI source.
    """
    import hashlib
    import json
    import re
    import struct

    def need(condition, message):
        if not condition:
            raise AssertionError("RPMh WAIT artifact verification: " + message)

    def sha(raw):
        return hashlib.sha256(raw).hexdigest()

    def same(actual, expected, label):
        if type(expected) is dict:
            need(type(actual) is dict and set(actual) == set(expected), label + " keys")
            for key in expected:
                same(actual[key], expected[key], label + "." + str(key))
        elif type(expected) is list:
            need(type(actual) is list and len(actual) == len(expected), label + " shape")
            for index, value in enumerate(expected):
                same(actual[index], value, label + "[" + str(index) + "]")
        elif type(expected) is bytes:
            need(type(actual) is bytes and actual == expected,
                 label + " byte mismatch (actual length/SHA=" +
                 (str(len(actual)) + "/" + sha(actual) if type(actual) is bytes else "not bytes") +
                 "; expected=" + str(len(expected)) + "/" + sha(expected) + ")")
        else:
            need(type(actual) is type(expected) and actual == expected,
                 label + ": expected " + repr(expected) + ", got " + repr(actual))

    need(type(info.filename) is str and info.filename.endswith("/result.json"),
         "expected component result.json ZipInfo")
    base = info.filename.rsplit("/", 1)[0]
    members, cache, qmp_cache, qtest_cache = {}, {}, {}, {}
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
                 "indexed ZIP size mismatch " + name)
            cache[name] = raw
        return cache[name]

    def document(relative):
        return json.loads(read(relative))

    def provenance(relative):
        raw = read(relative)
        return {"member": base + "/" + relative, "bytes": len(raw),
                "sha256": sha(raw), "read_from_zip": True}

    def records(key, count):
        rows = data.get(key)
        need(type(rows) is list and len(rows) == count, key + " count")
        need(all(type(row) is dict and type(row.get("name")) is str for row in rows),
             key + " record shape")
        need(len({row["name"] for row in rows}) == count, key + " duplicate names")
        for row in rows:
            same(row.get("passed"), True, key + "." + row["name"])
        return {row["name"]: row for row in rows}

    def qmp(directory, allow_unanswered_incoming=False):
        relative = directory + "/qmp.jsonl"
        if relative in qmp_cache:
            result = qmp_cache[relative]
        else:
            entries, events, pending, greeting = [], [], None, None
            next_id = 1
            for number, line in enumerate(read(relative).decode("utf-8").splitlines(), 1):
                row = json.loads(line)
                need(type(row) is dict and len(row) == 1 and
                     next(iter(row)) in ("send", "receive"), relative + " invalid frame")
                if "send" in row:
                    need(greeting is not None and pending is None,
                         relative + " overlapping request or missing greeting")
                    request = row["send"]
                    need(type(request) is dict and set(request) in
                         ({"execute", "id"}, {"execute", "id", "arguments"}),
                         relative + " request shape")
                    same(request["id"], next_id, relative + " monotonically matched ID")
                    need(type(request["execute"]) is str and
                         ("arguments" not in request or type(request["arguments"]) is dict),
                         relative + " request type")
                    next_id += 1
                    pending = {"command": request["execute"],
                               "arguments": request.get("arguments"),
                               "id": request["id"], "request_line": number,
                               "answered": False, "response": None,
                               "error": None, "response_line": None}
                    entries.append(pending)
                    continue
                response = row["receive"]
                need(type(response) is dict, relative + " response type")
                if "QMP" in response:
                    need(greeting is None and not entries and number == 1,
                         relative + " misplaced/duplicate QMP greeting")
                    greeting = response
                elif "event" in response:
                    need(greeting is not None and type(response["event"]) is str and
                         "id" not in response, relative + " malformed async event")
                    events.append({"line": number, "event": response})
                else:
                    need(pending is not None and
                         (("return" in response) != ("error" in response)),
                         relative + " unmatched/malformed QMP response")
                    same(response.get("id"), pending["id"], relative + " response ID")
                    pending.update({"answered": True, "response": response,
                                    "error": "error" in response,
                                    "response_line": number})
                    pending = None
            need(greeting is not None, relative + " no actual QMP greeting")
            result = {"entries": entries, "events": events, "greeting": greeting,
                      "unanswered": pending, "provenance": provenance(relative)}
            qmp_cache[relative] = result
        pending = result["unanswered"]
        need(pending is None or (allow_unanswered_incoming and
             pending["command"] == "migrate-incoming" and
             result["entries"][-1] is pending), relative + " unanswered request")
        return result

    def returned(entry, label):
        need(entry["answered"] and entry["error"] is False,
             label + " did not return successfully")
        return entry["response"]["return"]

    def qtest(directory):
        relative = directory + "/qtest.jsonl"
        if relative in qtest_cache:
            return qtest_cache[relative]
        pairs, pending, now, irqs = [], None, 0, {index: False for index in range(4)}
        events, clocks = [], []
        for number, line in enumerate(read(relative).decode("utf-8").splitlines(), 1):
            row = json.loads(line)
            need(type(row) is dict, relative + " frame is not an object")
            if "send" in row:
                same(set(row), {"send", "virtual_ns_before"}, relative + " request keys")
                need(pending is None and type(row["send"]) is str,
                     relative + " overlapping/invalid request")
                same(row["virtual_ns_before"], now, relative + " recorded clock before request")
                words = row["send"].split()
                need(words and words[0] in
                     ("readl", "writel", "clock_set", "clock_step", "irq_intercept_out"),
                     relative + " unexpected operation")
                pending = {"words": words, "request_line": number, "before_ns": now}
                continue
            same(set(row), {"receive"}, relative + " response keys")
            response = row["receive"]
            need(pending is not None and type(response) is str,
                 relative + " response without a request")
            fields = response.split()
            if fields and fields[0] == "IRQ":
                need(len(fields) == 3 and fields[1] in ("raise", "lower"),
                     relative + " invalid IRQ event")
                index = int(fields[2], 10)
                need(index in irqs, relative + " unexpected output IRQ number")
                irqs[index] = fields[1] == "raise"
                events.append({"line": number, "irq": index, "level": irqs[index],
                               "during_request_line": pending["request_line"]})
                continue
            need(fields and fields[0] == "OK", relative + " non-OK QTest response")
            words, reply = pending["words"], fields[1:]
            operation = words[0]
            if operation in ("clock_step", "clock_set"):
                need(len(words) == 2 and len(reply) == 1, relative + " clock reply shape")
                value, actual = int(words[1], 0), int(reply[0], 0)
                expected = now + value if operation == "clock_step" else value
                need(value >= 0 and actual == expected and actual >= now,
                     relative + " virtual clock result")
                now = actual
                clocks.append({"operation": operation, "argument_ns": value,
                               "returned_virtual_ns": now, "request_line": pending["request_line"]})
            elif operation == "readl":
                need(len(words) == 2 and len(reply) == 1 and
                     0 <= int(reply[0], 0) <= 0xffffffff, relative + " read32 reply")
            elif operation == "writel":
                need(len(words) == 3 and not reply and
                     0 <= int(words[2], 0) <= 0xffffffff, relative + " write32 reply")
            else:
                same(words, ["irq_intercept_out", device_path, "sysbus-irq"],
                     relative + " intercepted output group")
                same(reply, [], relative + " interception reply")
            pending.update({"reply": reply, "response_line": number,
                            "after_ns": now, "irq_levels": dict(irqs)})
            pairs.append(pending)
            pending = None
        need(pending is None, relative + " unanswered QTest request")
        result = {"pairs": pairs, "clock_events": clocks, "irq_events": events,
                  "last_virtual_ns": now, "provenance": provenance(relative)}
        qtest_cache[relative] = result
        return result

    def operations(directory):
        rows = []
        for entry in qtest(directory)["pairs"]:
            words = entry["words"]
            if words[0] == "irq_intercept_out":
                rows.append(["irq_intercept_out"])
            elif words[0] in ("clock_set", "clock_step"):
                rows.append([words[0], int(words[1], 0), entry["after_ns"]])
            elif words[0] == "writel":
                rows.append(["writel", int(words[1], 0), int(words[2], 0), entry["before_ns"]])
            else:
                rows.append(["readl", int(words[1], 0), int(entry["reply"][0], 0), entry["before_ns"]])
        return rows

    def filename_argument(entry, expected, label):
        args = entry["arguments"]
        need(type(args) is dict and set(args) == {"channels"}, label + " file channels")
        channels = args["channels"]
        need(type(channels) is list and len(channels) == 1, label + " channel count")
        expected_name = data["work_dir"].rstrip("/") + "/" + expected
        same(channels[0], {"channel-type": "main", "addr": {
            "transport": "file", "filename": expected_name, "offset": 0}},
            label + " actual file transport")

    def stopped(entry, label, expected_status=None):
        value = returned(entry, label)
        need(entry["command"] == "query-status" and type(value) is dict and
             value.get("running") is False and type(value.get("status")) is str,
             label + " not actually stopped")
        if expected_status is not None:
            same(value["status"], expected_status, label + " runstate")
        return value

    def command_proof(directory, policy, incoming=False, accel="qtest"):
        argv = document(directory + "/command.json")
        need(type(argv) is list and all(type(item) is str for item in argv),
             directory + " invalid argv")
        prefix = [data["qemu"], "-machine", machine, "-accel", accel,
                  "-m", "128M", "-display", "none", "-monitor", "none",
                  "-serial", "null", "-S", "-d", "unimp,guest_errors"]
        same(argv[:16], prefix, directory + " fixed command prefix")
        need(len(argv) >= 22, directory + " incomplete transport arguments")
        same(argv[16:22:2], ["-qtest", "-qtest-log", "-qmp"], directory + " transport options")
        same(argv[19], "/dev/null", directory + " built-in qtest log")
        qt = re.fullmatch(r"unix:(/[^,]+)/t,server=on,wait=off", argv[17])
        qm = re.fullmatch(r"unix:(/[^,]+)/m,server=on,wait=off", argv[21])
        need(qt is not None and qm is not None and qt.group(1) == qm.group(1),
             directory + " paired short Unix sockets")
        tail = [] if policy is None else ["-global", device_type + "." + policy_name +
                                          ("=on" if policy else "=off")]
        if incoming:
            tail += ["-incoming", "defer"]
        same(argv[22:], tail, directory + " exact policy/deferred-incoming options")
        return provenance(directory + "/command.json")

    same(document("result.json"), data, "supplied report matches actual ZIP JSON")
    device_type, device_path = "qcom-sm8250-rpmh", "/machine/rpmh"
    policy_name = "reference-wait-mask-reserved"
    subsection_name = device_type + "/wait-reserved-reference"
    main_bytes, sub_bytes, ram = 19420, 24, 0x80010000
    period_unit = 1_000_000_000 << 32
    xo_input_period, xo_output_period = period_unit // 38_400_000, period_unit // 19_200_000
    cmd_db_sha = "416efcf69817cc7b0465a33724bace31e576040b3ec775e0a184a2d051f8c6f8"
    test_sha = "b72b503c8c968310ea435dc058c6db1a072cad2a20281761696e2ffb3bd16032"
    machine = ("tb371fc,gcc-boot-configured=off,ufs-enabled=off,"
               "rpmh-profile=reference,rpmh-boot-configured=off")
    diagnostic_names = ["wait-command-mask", "wait-reserved-masked-writes",
                        "wait-reserved-last-voter", "wait-reserved-last-tcs",
                        "wait-reserved-last-raw", "wait-reserved-last-effective",
                        "wait-reserved-last-ignored"]
    expected_report = {
        "schema": "tb371fc-rpmh-wait-mask-v1", "suite": "RPMh WAIT explicit reserved-mask reference",
        "base_source_commit": "3b31e83dc412962826ada872e361a1d32ea4e463",
        "test_sha256": test_sha, "machine": machine, "success": True, "binary_unchanged": True,
        "expected_checks": 17, "expected_completed_migrations": 1,
        "expected_rejected_migrations": 2, "expected_corrupted_stream_rejections": 2,
        "vmstate_version": 2, "vmstate_minimum_version": 2,
        "subsection_version": 1, "subsection_payload_bytes": 24,
        "main_payload_bytes_with_default_clock": 19420,
        "commands_per_tcs": 16, "voter_count": 4, "apps_tcs_per_drv": 8,
        "display_tcs_count": 2, "wait_command_mask": 65535,
        "default_strict_main_vmstate_unchanged": True, "old_rpmh_tests_changed": False,
        "old_rpmh_test_blobs_match": True, "silicon_reserved_bit_semantics_proved": False,
        "guest_cpu_executed": False, "native_kernel_probe_observed": False,
        "guest_image_patched": False, "android_boot_passed": False,
        "diagnostic_counters_are_model_observations": True,
        "reference_policy_auto_enabled": False, "reference_command_db_sha256": cmd_db_sha,
    }
    for key, expected in expected_report.items():
        same(data.get(key), expected, "report." + key)
    binary_hash = data.get("qemu_sha256_before")
    need(type(binary_hash) is str and re.fullmatch(r"[0-9a-f]{64}", binary_hash), "binary hash")
    same(data.get("qemu_sha256_after"), binary_hash, "unchanged binary SHA256")
    need(type(data.get("qemu")) is str and data["qemu"].startswith("/"), "absolute QEMU path")
    need(type(data.get("work_dir")) is str and bool(data["work_dir"]), "work directory")
    same(data.get("old_rpmh_test_identities"), {
        "test_rpmh_sm8250.py": {"bytes": 27170,
            "sha256": "aa8250a4799da7e6db8cd871d3115d6c3b747baa99cd6aee0c99e3bca1c24baf",
            "git_blob": "2eb42c9aa4187d585a903dcda659cc004a9aee7d"},
        "test_rpmh_display.py": {"bytes": 19616,
            "sha256": "eedc4d025763a91e89e0f4fea038054aa16763c4ceb8d303037d3e4d636b5557",
            "git_blob": "de7a6303cf009cf07115e87eb42db8016ccbc6f0"},
    }, "unchanged original RPMh test identities")
    checks = records("checks", 17)
    same(list(checks), [
        "default_strict_geometry_and_readonly_diagnostics", "strict_reserved_wait_writes_are_atomic",
        "reference_masks_only_high_bits_and_audits_each_write",
        "reference_isolated_across_all_voters_and_boundary_tcs",
        "low_wait_bits_control_completion_without_response",
        "masked_high_bits_do_not_request_low_slot_completion",
        "busy_wait_rejection_does_not_restart_original_deadline",
        "pending_irq_is_independent_of_idle_wait_programming",
        "cmd_enable_and_invalid_resource_validation_remain_strict",
        "system_reset_clears_wait_diagnostics_and_pending_command",
        "paused_tcg_exposes_the_same_pure_readonly_diagnostics",
        "real_v2_main_and_v1_subsection_restore_before_first_rpmh_mmio",
        "restored_nonresponse_wait_completes_at_599_plus_1_ns_and_accepts_new_work",
        "strict_source_rejected_by_prewarmed_masked_target",
        "cold_masked_source_emits_policy_and_is_rejected_by_strict_target",
        "single_bit_policy_corruption", "single_bit_last_voter_corruption",
    ], "exact gate check names/order")
    migrations = records("migrations", 1)
    rejected = records("rejected_migrations", 2)
    corrupted = records("corrupted_stream_rejections", 2)

    def diagnostic_state(count=0, voter=0, tcs=0, raw=0):
        return dict(zip(diagnostic_names, [65535, count, voter, tcs, raw,
                                          raw & 65535, raw & 0xffff0000]))

    def snapshot(policy, count=0, voter=0, tcs=0, raw=0, xo_value=0):
        return {"policy": policy, "diagnostics": diagnostic_state(count, voter, tcs, raw),
                "cmd_db_sha256": cmd_db_sha, "xo_value": xo_value, "ldoa12_value": 0,
                "xo_input_period": xo_input_period,
                "xo_output_period": xo_output_period if xo_value else 0}

    def diagnostic_pairs(state):
        return [(device_path, name, state["diagnostics"][name]) for name in diagnostic_names]

    def snapshot_pairs(state):
        return ([(device_path, policy_name, state["policy"])] + diagnostic_pairs(state) + [
            (device_path, "cmd-db-sha256", state["cmd_db_sha256"]),
            (device_path, "res.xo.lvl.value", state["xo_value"]),
            (device_path, "res.ldoa12.value", state["ldoa12_value"]),
            (device_path + "/xo-board", "qtest-clock-period", state["xo_input_period"]),
            (device_path + "/bi_tcxo", "qtest-clock-period", state["xo_output_period"]),
        ])

    def match_getters(entries, expected, label):
        getters = [entry for entry in entries if entry["command"] == "qom-get"]
        same(len(getters), len(expected), label + " getter count")
        for entry, (path, name, value) in zip(getters, expected):
            same(entry["arguments"], {"path": path, "property": name}, label + " getter identity")
            same(returned(entry, label), value, label + "." + name)
        return {"count": len(getters), "request_ids": [row["id"] for row in getters],
                "request_lines": [row["request_line"] for row in getters],
                "all_values_and_unsigned_types_checked": True}

    def snapshot_witness(entries, expected, label, expected_status=None):
        same([entry["command"] for entry in entries],
             ["query-status"] + ["qom-get"] * 26 + ["query-status"],
             label + " stopped/double-snapshot ordering")
        first = stopped(entries[0], label + " before", expected_status)
        last = stopped(entries[-1], label + " after", expected_status)
        same(last, first, label + " stable runstate")
        proof = match_getters(entries, snapshot_pairs(expected) * 2, label)
        return first, {**proof, "double_read_snapshot": expected,
                       "before_status_request_id": entries[0]["id"],
                       "after_status_request_id": entries[-1]["id"]}

    def completed_request(entries, command, filename, expected, label):
        positions = [i for i, entry in enumerate(entries) if entry["command"] == command]
        same(len(positions), 1, label + " unique migration request")
        position = positions[0]
        filename_argument(entries[position], filename, label)
        returned(entries[position], label + " request")
        need(type(expected) is dict and expected.get("status") == "completed",
             label + " completed report")
        done = None
        for i in range(position + 1, len(entries)):
            entry = entries[i]
            if entry["command"] != "query-migrate":
                break
            state = returned(entry, label + " query-migrate")
            need(type(state) is dict and state.get("status") not in ("failed", "cancelled"),
                 label + " actual migration failed")
            if state.get("status") == "completed":
                same(state, expected, label + " actual completed reply")
                done = i
                break
        need(done is not None, label + " no actual completed query-migrate reply")
        return position, done

    def section(raw, scenario, sub_expected, label):
        same(raw[:8], b"QEVM\x00\x00\x00\x03", label + " stream header")
        encoded = device_type.encode("ascii")
        marker = bytes([len(encoded)]) + encoded
        candidates, cursor = [], 0
        while True:
            at = raw.find(marker, cursor)
            if at < 0:
                break
            cursor = at + 1
            header, metadata = at - 5, at + len(marker)
            if header >= 8 and raw[header] in (1, 4) and metadata + 8 + main_bytes <= len(raw):
                instance, version = struct.unpack_from(">II", raw, metadata)
                candidates.append((header, metadata + 8, instance, version))
        same(len(candidates), 1, label + " unique FULL/START RPMh section")
        header, offset, instance, version = candidates[0]
        same(instance, 0, label + " device instance")
        same(version, 2, label + " main VMState version")
        section_id = int.from_bytes(raw[header + 1:header + 5], "big")
        payload = raw[offset:offset + main_bytes]
        need(len(payload) == main_bytes, label + " complete main payload")
        position = 0

        def take(fmt):
            nonlocal position
            width = struct.calcsize(">" + fmt)
            need(position + width <= len(payload), label + " field beyond main payload")
            values = struct.unpack_from(">" + fmt, payload, position)
            position += width
            return list(values)

        digest = payload[:32].hex()
        position = 32
        same(digest, cmd_db_sha, label + " fixed reference command database digest")
        latency = take("Q")[0]
        same(latency, 1000, label + " command latency")
        drvs, waits = [], []
        inflight = scenario == "inflight"
        need(scenario in ("inflight", "cold"), label + " source scenario")
        selected_waits = {(0, 7): 1, (2, 0): 0x8000, (3, 1): 2} if inflight else {}
        for voter in range(4):
            enable, status, wake_hi, wake_lo, sleeping = take("4IB")
            same([enable, status, wake_hi, wake_lo, sleeping],
                 [1 if inflight and voter == 2 else 0, 0, 0, 0, 0],
                 label + " voter " + str(voter) + " header")
            tcss = []
            for tcs in range(8):
                wait_offset = position
                wait, control, command_enable = take("3I")
                active = inflight and (voter, tcs) == (2, 0)
                same([wait, control, command_enable],
                     [selected_waits.get((voter, tcs), 0),
                      0x1010000 if active else 0, 0x8000 if active else 0],
                     label + " voter/TCS " + str((voter, tcs)))
                waits.append({"voter": voter, "tcs": tcs, "offset": wait_offset, "value": wait})
                commands = []
                for slot in range(16):
                    fields = take("5I")
                    same(fields, [0x10008, 0x3e000, 3, 0x100, 0]
                         if active and slot == 15 else [0, 0, 0, 0, 0],
                         label + " command " + str((voter, tcs, slot)))
                    commands.append(fields)
                current, deadline, running, stalled = take("iqBB")
                same([current, deadline, running, stalled],
                     [15, 1000, 1, 0] if active else [-1, -1, 0, 0],
                     label + " TCS execution state " + str((voter, tcs)))
                tcss.append({"wait": wait, "control": control, "enable": command_enable,
                             "commands": commands, "cursor": current, "deadline_ns": deadline,
                             "running": bool(running), "stalled": bool(stalled)})
            drvs.append({"irq_enable": enable, "irq_status": status, "wake_hi": wake_hi,
                         "wake_lo": wake_lo, "sleeping": bool(sleeping), "tcs": tcss})
        resources = []
        for index in range(98):
            votes = [take("4I") for _ in range(4)]
            staged, dirty = take("4I"), take("4B")
            same(votes, [[0, 0, 0, 0] for _ in range(4)], label + " resource votes " + str(index))
            same(staged, [0, 0, 0, 0], label + " staged resource " + str(index))
            same(dirty, [0, 0, 0, 0], label + " resource dirty flags " + str(index))
            resources.append({"votes": votes, "staged": staged, "dirty": [False] * 4})
        input_period = take("Q")[0]
        same(input_period, xo_input_period, label + " serialized actual XO input Clock")
        same(position, main_bytes, label + " all main bytes independently decoded")
        decoded_main = {"cmd_db_digest": digest, "command_latency_ns": latency,
                        "drv": drvs, "resources": resources, "xo_board_period": input_period}
        boundary = offset + main_bytes
        encoded_sub = subsection_name.encode("ascii")
        sub_marker = bytes([5, len(encoded_sub)]) + encoded_sub
        occurrences, cursor = [], 0
        while True:
            found = raw.find(sub_marker, cursor)
            if found < 0:
                break
            occurrences.append(found)
            cursor = found + 1
        sub = None
        if sub_expected is None:
            same(occurrences, [], label + " strict source has no policy subsection of any version")
            footer = boundary
        else:
            same(occurrences, [boundary], label + " exactly one immediately adjacent subsection")
            sub_offset = boundary + len(sub_marker) + 4
            same(int.from_bytes(raw[sub_offset - 4:sub_offset], "big"), 1,
                 label + " subsection version")
            raw_sub = raw[sub_offset:sub_offset + sub_bytes]
            same(len(raw_sub), 24, label + " subsection payload size")
            values = list(struct.unpack(">IQIII", raw_sub))
            same(values, sub_expected, label + " every subsection field")
            sub = {"name": subsection_name, "version": 1, "header_offset": boundary,
                   "payload_offset": sub_offset, "payload_bytes": 24,
                   "payload_sha256": sha(raw_sub), "payload_hex": raw_sub.hex(),
                   "decoded": dict(zip(["policy", "masked_write_count", "last_voter",
                                         "last_tcs", "last_raw"], values))}
            footer = sub_offset + sub_bytes
        # save-section-footer defaults true in the pinned options.c. The
        # command/log allowlists forbid changing this migration configuration.
        same(raw[footer:footer + 5], b"\x7e" + struct.pack(">I", section_id),
             label + " matching QEMU section footer")
        return {
            "section_type": raw[header], "name": device_type, "instance_id": 0, "version": 2,
            "header_offset": header, "payload_offset": offset, "payload_bytes": main_bytes,
            "section_id": section_id, "payload_sha256": sha(payload), "scenario": scenario,
            "serialized_waits": waits, "decoded_main": decoded_main, "subsection": sub,
            "footer_offset": footer, "footer_matches_main_section_id": True,
        }

    def receipt(raw, decoded, filename):
        main = {key: decoded[key] for key in (
            "name", "instance_id", "version", "header_offset", "payload_offset",
            "payload_bytes", "section_id", "payload_sha256", "scenario", "serialized_waits")}
        main.update({"payload_file": filename + ".main-v2.payload.bin",
                     "complete_payload_verified_against_fixed_source_scenario": True})
        at = main["payload_offset"]
        same(read(main["payload_file"]), raw[at:at + main_bytes], filename + " saved actual main bytes")
        sub = decoded["subsection"]
        if sub is not None:
            sub = dict(sub)
            sub["payload_file"] = filename + ".sub-v1.payload.bin"
            sub["all_24_bytes_match_actual_stopped_source_qom"] = True
            at = sub["payload_offset"]
            same(read(sub["payload_file"]), raw[at:at + sub_bytes], filename + " saved actual subsection bytes")
        expected = {"main": main, "subsection": sub, "stream_sha256": sha(raw), "stream_bytes": len(raw)}
        same(document(filename + ".sections.json"), expected, filename + " section receipt")
        return expected

    inflight_name = "rpmh-wait-v2-subv1-masked-inflight.migration"
    strict_name = "rpmh-wait-v2-strict-source.migration"
    cold_name = "rpmh-wait-v2-subv1-cold-masked.migration"
    inflight_state = snapshot(True, 3, 3, 1, 0x20002)
    cold_state, strict_state = snapshot(True), snapshot(False)
    prewarm_state = snapshot(True, 1, 1, 6, 0x40000004)
    source_specs = [
        {"file": inflight_name, "directory": "mig-source", "time": 400,
         "witness": 0x57544d31, "state": inflight_state, "scenario": "inflight",
         "policy_arg": True, "sub": [1, 3, 3, 1, 0x20002]},
        {"file": strict_name, "directory": "strict-source", "time": 23,
         "witness": 0x53545230, "state": strict_state, "scenario": "cold",
         "policy_arg": None, "sub": None},
        {"file": cold_name, "directory": "cold-source", "time": 37,
         "witness": 0x4d41534b, "state": cold_state, "scenario": "cold",
         "policy_arg": True, "sub": [1, 0, 0, 0, 0]},
    ]
    rows = data.get("source_streams")
    need(type(rows) is list and len(rows) == 3 and all(type(row) is dict for row in rows),
         "three actual source saves")
    same([row.get("file") for row in rows], [case["file"] for case in source_specs], "source file identities")
    source_records = {row["file"]: row for row in rows}
    streams, decoded_sections, log_evidence, originals, decoded, receipts = {}, [], {}, {}, {}, {}

    def source_proof(case):
        filename, directory = case["file"], case["directory"]
        record = source_records[filename]
        raw = read(filename)
        originals[filename] = raw
        parsed = section(raw, case["scenario"], case["sub"], filename)
        decoded[filename] = parsed
        expected_receipt = receipt(raw, parsed, filename)
        receipts[filename] = expected_receipt
        command_info = command_proof(directory, case["policy_arg"])
        transcript = qmp(directory)
        entries = transcript["entries"]
        need(all(entry["command"] in ("qmp_capabilities", "cont", "stop", "qom-get",
                                      "query-status", "migrate", "query-migrate") for entry in entries),
             directory + " unexpected QMP mutation")
        same([entry["command"] for entry in entries if entry["command"] in
              ("qmp_capabilities", "cont", "stop", "migrate")],
             ["qmp_capabilities", "cont", "stop", "migrate"], directory + " lifecycle order")
        for entry in entries:
            returned(entry, directory)
        start, done = completed_request(entries, "migrate", filename,
                                        record.get("source_query_migrate"), directory)
        stop_index = next(i for i, entry in enumerate(entries) if entry["command"] == "stop")
        before_status, before_proof = snapshot_witness(
            entries[stop_index + 1:start], case["state"], directory + " before save", "paused")
        after_status, after_proof = snapshot_witness(
            entries[done + 1:], case["state"], directory + " after save", "postmigrate")
        before = {"query_status": before_status, "source_virtual_ns": case["time"],
                  "qom": case["state"], "repeated_pure_qom_unchanged": True}
        after = {"query_status": after_status, "source_virtual_ns": case["time"], "qom": case["state"]}
        same(document(directory + "/source-qom-before-save.json"), before, directory + " pre-save JSON")
        same(document(directory + "/source-qom-after-save.json"), after, directory + " post-save JSON")
        same(document(directory + "/source-query-migrate.json"), record["source_query_migrate"],
             directory + " completed query JSON")
        expected_record = {
            "file": filename, "sha256": sha(raw), "bytes": len(raw), "source_directory": directory,
            "source_status": "completed", "source_virtual_ns": case["time"],
            "source_query_migrate": record["source_query_migrate"],
            "source_qom_before_file": directory + "/source-qom-before-save.json",
            "source_qom_after_file": directory + "/source-qom-after-save.json",
            "source_query_migrate_file": directory + "/source-query-migrate.json",
            "source_qom": case["state"], "sections": expected_receipt,
            "ram_witness": case["witness"], "source_stream_modified": False,
        }
        same(record, expected_record, directory + " complete source receipt")
        ops = [["irq_intercept_out"]]
        if case["scenario"] == "inflight":
            ops += [
                ["writel", 0x18201f70, 0x10001, 0],
                ["writel", 0x18220d10, 0x80008000, 0],
                ["writel", 0x0af21eb0, 0x20002, 0],
                ["writel", 0x18220e5c, 0x10008, 0],
                ["writel", 0x18220e60, 0x3e000, 0],
                ["writel", 0x18220e64, 3, 0],
                ["writel", 0x18220d00, 1, 0],
                ["writel", 0x18220d1c, 0x8000, 0],
                ["writel", 0x18220d14, 0x10000, 0],
                ["writel", 0x18220d14, 0x1010000, 0],
                ["clock_step", 400, 400], ["readl", 0x18220e68, 0x100, 400],
            ]
        else:
            ops += [["clock_set", case["time"], case["time"]]]
        ops += [["writel", ram, case["witness"], case["time"]]]
        same(operations(directory), ops, directory + " exact actual QTest programming/time sequence")
        qt = qtest(directory)
        need(not any(event["level"] for event in qt["irq_events"]),
             directory + " source IRQ asserted before any command completed")
        stderr = read(directory + "/qemu.stderr").decode("utf-8")
        masked_lines = [line for line in stderr.splitlines() if "reference WAIT mask" in line]
        expected_lines = [
            device_type + ": DRV0 TCS7 reference WAIT mask raw=0x00010001 effective=0x0001 ignored=0x00010000",
            device_type + ": DRV2 TCS0 reference WAIT mask raw=0x80008000 effective=0x8000 ignored=0x80000000",
            device_type + ": DRV3 TCS1 reference WAIT mask raw=0x00020002 effective=0x0002 ignored=0x00020000",
        ] if case["scenario"] == "inflight" else []
        same(masked_lines, expected_lines, directory + " actual accepted-mask diagnostics")
        need(not any("unsupported write" in line or "no completion" in line
                     for line in stderr.splitlines() if device_type in line),
             directory + " unexpected RPMh source error")
        decoded_sections.append({**parsed, "file": filename, "purpose": "actual-source-save",
                                 "all_main_fields_match_fixed_source_scenario": True,
                                 "subsection_matches_raw_source_qom": case["sub"] is not None})
        streams[filename] = {**provenance(filename), "source_directory": directory,
                             "actual_source_completed": True, "source_virtual_ns": case["time"],
                             "completed_roundtrip": False, "main_bytes": main_bytes,
                             "subsection_present": case["sub"] is not None,
                             "subsection_bytes": 24 if case["sub"] is not None else 0}
        proof = {"command": command_info, "qmp": transcript["provenance"],
                 "qtest": qt["provenance"], "clock_events": qt["clock_events"],
                 "stderr": provenance(directory + "/qemu.stderr"),
                 "before_save": before_proof, "after_save": after_proof,
                 "actual_source_completed": True,
                 "all_main_fields_checked_against_fixed_programming_scenario": True,
                 "hidden_individual_resource_votes_claimed_as_qom_observations": False}
        log_evidence[directory] = proof
        return proof

    for case in source_specs:
        source_proof(case)
    same(read(strict_name + ".main-v2.payload.bin"), read(cold_name + ".main-v2.payload.bin"),
         "strict and zero-use masked main v2 payloads are byte-identical")
    same(data.get("strict_and_cold_masked_main_payload_bytes_identical"), True,
         "cold main-payload comparison report")
    same(data.get("strict_and_cold_masked_main_payload_sha256"), decoded[strict_name]["payload_sha256"],
         "cold main-payload comparison digest")

    same(list(migrations), ["v2_with_wait_reference_v1_inflight"], "completed migration identity")
    migration = migrations["v2_with_wait_reference_v1_inflight"]
    target = "mig-target"
    target_command = command_proof(target, True, True)
    target_transcript = qmp(target)
    entries = target_transcript["entries"]
    need(all(entry["command"] in ("qmp_capabilities", "qom-get", "query-status",
                                  "migrate-incoming", "query-migrate", "cont") for entry in entries),
         target + " unexpected QMP mutation")
    for entry in entries:
        returned(entry, target)
    load_at, done_at = completed_request(entries, "migrate-incoming", inflight_name,
                                         migration.get("destination"), target)
    continues = [i for i, entry in enumerate(entries) if entry["command"] == "cont"]
    same(len(continues), 1, target + " unique actual continuation")
    cont_at = continues[0]
    need(cont_at > done_at, target + " continued before completed incoming")
    same([entry["command"] for entry in entries[:10]],
         ["qmp_capabilities", "qom-get", "query-status"] + ["qom-get"] * 7,
         target + " prewarm prefix")
    match_getters(entries[:10], [(device_path, policy_name, True)] +
                  diagnostic_pairs(prewarm_state), target + " actual prewarm diagnostics")
    old_status = stopped(entries[2], target + " initial stop")
    preload_status, preload_proof = snapshot_witness(
        entries[10:load_at], prewarm_state, target + " pre-load")
    same(preload_status, old_status, target + " prewarm stayed stopped")
    same(document(target + "/destination-before-load.json"), {
        "query_status": preload_status, "destination_virtual_ns": 400, "qom": prewarm_state,
    }, target + " pre-load receipt")
    restored_window = entries[done_at + 1:cont_at]
    need(restored_window, target + " missing first restored observations")
    restored_status, restored_proof = snapshot_witness(
        restored_window[:-1], inflight_state, target + " first restored")
    same(stopped(restored_window[-1], target + " after RAM drain"), restored_status,
         target + " state after first restored RAM witness")
    same(document(target + "/destination-before-first-rpmh-mmio.json"), {
        "query_status": restored_status, "destination_virtual_ns": 400, "qom": inflight_state,
        "ram_witness": 0x57544d31, "rpmh_mmio_since_incoming": False,
        "qmp_cont_since_incoming": False,
    }, target + " first restored receipt")
    same(document(target + "/destination-query-migrate.json"), migration["destination"],
         target + " actual completed incoming JSON")
    after_state = snapshot(True, 3, 3, 1, 0x20002, xo_value=3)
    next_state = snapshot(True, 4, 2, 0, 0x80000001, xo_value=1)
    after_cont = entries[cont_at + 1:]
    same([entry["command"] for entry in after_cont], ["qom-get"] * 52,
         target + " pure timer/new-work QMP sequence")
    before_pairs = ([(device_path, "res.xo.lvl.value", 0),
                     (device_path + "/bi_tcxo", "qtest-clock-period", 0)] +
                    diagnostic_pairs(inflight_state) + snapshot_pairs(inflight_state))
    after_pairs = ([(device_path + "/bi_tcxo", "qtest-clock-period", xo_output_period),
                    (device_path, "res.xo.lvl.value", 3)] +
                   diagnostic_pairs(after_state) + snapshot_pairs(after_state))
    next_pairs = [(device_path, "res.xo.lvl.value", 1)] + diagnostic_pairs(next_state)
    timer_qom_proof = match_getters(after_cont, before_pairs + after_pairs + next_pairs,
                                    target + " actual 999/1000/2000 QOM values")
    same(document(target + "/restored-timer-before-first-rpmh-mmio.json"), {
        "before_at_999_ns": inflight_state, "after_at_1000_ns": after_state,
        "device_mmio_since_incoming": False, "irq2": True,
        "actual_clock_period_before": 0, "actual_clock_period_after": xo_output_period,
    }, target + " timer receipt")
    expected_target_operations = [
        ["irq_intercept_out"], ["writel", 0x18211cd0, 0x40000004, 0],
        ["writel", ram, 0xdeadbeef, 0], ["clock_set", 400, 400],
        ["readl", ram, 0x57544d31, 400], ["clock_step", 599, 999],
        ["clock_step", 1, 1000], ["readl", ram, 0x57544d31, 1000],
        ["readl", 0x18201f70, 1, 1000], ["readl", 0x18220d10, 0x8000, 1000],
        ["readl", 0x0af21eb0, 2, 1000], ["readl", 0x18220e68, 0x10100, 1000],
        ["readl", 0x18220d04, 1, 1000],
        ["writel", 0x18220d14, 0, 1000], ["writel", 0x18220d1c, 0, 1000],
        ["writel", 0x18220d08, 1, 1000], ["writel", 0x18220d10, 0x80000001, 1000],
        ["writel", 0x18220d30, 0x10008, 1000], ["writel", 0x18220d34, 0x3e000, 1000],
        ["writel", 0x18220d38, 1, 1000], ["writel", 0x18220d1c, 1, 1000],
        ["writel", 0x18220d14, 0x10000, 1000], ["writel", 0x18220d14, 0x1010000, 1000],
        ["clock_step", 1000, 2000], ["readl", 0x18220d3c, 0x10100, 2000],
    ]
    same(operations(target), expected_target_operations, target + " exact actual QTest sequence")
    qt_target = qtest(target)
    same(qt_target["pairs"][5]["irq_levels"][2], False, target + " no early completion IRQ at 999 ns")
    same(qt_target["pairs"][7]["irq_levels"][2], True,
         target + " actual IRQ2 restored/dispatched before first device MMIO")
    need(not any(event["level"] and event["irq"] != 2 for event in qt_target["irq_events"]),
         target + " completion asserted an unrelated voter IRQ")
    target_stderr = read(target + "/qemu.stderr").decode("utf-8")
    same([line for line in target_stderr.splitlines() if "reference WAIT mask" in line], [
        device_type + ": DRV1 TCS6 reference WAIT mask raw=0x40000004 effective=0x0004 ignored=0x40000000",
        device_type + ": DRV2 TCS0 reference WAIT mask raw=0x80000001 effective=0x0001 ignored=0x80000000",
    ], target + " prewarm/new-work mask diagnostics")
    need(not any("unsupported write" in line or "no completion" in line
                 for line in target_stderr.splitlines() if device_type in line),
         target + " unexpected device error")
    expected_migration = {
        "name": "v2_with_wait_reference_v1_inflight", "passed": True,
        "source_status": "completed", "target_status": "completed",
        "source": source_records[inflight_name]["source_query_migrate"],
        "destination": migration["destination"], "snapshot_file": inflight_name,
        "snapshot_sha256": sha(originals[inflight_name]), "snapshot_bytes": len(originals[inflight_name]),
        "stream_modified": False, "source_directory": "mig-source", "target_directory": target,
        "source_virtual_ns": 400, "destination_virtual_ns": 400,
        "absolute_command_deadline_ns": 1000, "remaining_ns_at_transfer": 600,
        "existing_absolute_deadline_clock_alignment_preserved": True,
        "sections": receipts[inflight_name], "snapshot_before": inflight_state,
        "snapshot_after": inflight_state, "target_first_observation_before_rpmh_mmio": True,
        "source_qom_before_file": "mig-source/source-qom-before-save.json",
        "source_qom_after_file": "mig-source/source-qom-after-save.json",
        "target_qom_before_first_mmio_file": target + "/destination-before-first-rpmh-mmio.json",
        "target_query_migrate_file": target + "/destination-query-migrate.json",
        "continued_after_restore": True,
        "restored_timer_observations_file": target + "/restored-timer-before-first-rpmh-mmio.json",
    }
    same(migration, expected_migration, "completed migration complete receipt")
    streams[inflight_name].update({"completed_roundtrip": True, "target_directory": target,
                                   "actual_destination_completed": True,
                                   "target_clock_aligned_to_source_ns": 400,
                                   "restored_absolute_deadline_ns": 1000})
    log_evidence[target] = {
        "command": target_command, "qmp": target_transcript["provenance"],
        "qtest": qt_target["provenance"], "clock_events": qt_target["clock_events"],
        "irq_events": qt_target["irq_events"], "stderr": provenance(target + "/qemu.stderr"),
        "prewarm": preload_proof, "first_restored": restored_proof,
        "timer_qom": timer_qom_proof, "actual_completed_incoming": True,
        "first_restored_qom_precedes_cont_in_actual_qmp_log": True,
        "irq2_high_before_first_post_incoming_rpmh_mmio_in_actual_qtest_log": True,
        "actual_clock_zero_at_999_ns_and_19_2mhz_at_1000_ns": True,
        "source_and_destination_virtual_clock_alignment_is_required": True,
        "absolute_deadlines_rebased_or_policy_timer_added": False,
        "cross_channel_order_scope":
            "QMP and QTest are independently ordered logs without shared timestamps. "
            "Their joint ordering is bound to pinned test 4fb1a83a and its retained "
            "receipts, not inferred from a common event timestamp.",
    }

    def failed_incoming(directory, filename, record, diagnostic, policy, time_ns, prewarm=False):
        command_info = command_proof(directory, policy, True)
        transcript = qmp(directory, allow_unanswered_incoming=True)
        rows = transcript["entries"]
        need(rows and rows[0]["command"] == "qmp_capabilities", directory + " QMP initialized")
        positions = [i for i, row in enumerate(rows) if row["command"] == "migrate-incoming"]
        same(len(positions), 1, directory + " unique actual incoming request")
        load = positions[0]
        same(load, len(rows) - 1, directory + " no commands after failed incoming")
        request = rows[load]
        filename_argument(request, filename, directory)
        need(not any(row["command"] in ("cont", "qom-set", "system_reset", "stop") for row in rows),
             directory + " execution/state repair during rejected incoming")
        for row in rows[:load]:
            returned(row, directory + " setup")
        if prewarm:
            same([row["command"] for row in rows[:10]],
                 ["qmp_capabilities", "qom-get", "query-status"] + ["qom-get"] * 7,
                 directory + " actual prewarm prefix")
            match_getters(rows[:10], [(device_path, policy_name, True)] +
                          diagnostic_pairs(prewarm_state), directory + " prewarm history")
            initial = stopped(rows[2], directory + " initial stop")
            before, before_proof = snapshot_witness(rows[10:load], prewarm_state, directory + " before rejected load")
            same(before, initial, directory + " prewarm stayed stopped")
            same(document(directory + "/destination-before-load.json"), {
                "query_status": before, "destination_virtual_ns": time_ns, "qom": prewarm_state,
            }, directory + " actual prewarm JSON")
            expected_ops = [["irq_intercept_out"], ["writel", 0x18211cd0, 0x40000004, 0],
                            ["writel", ram, 0xdeadbeef, 0], ["clock_set", time_ns, time_ns]]
        else:
            same([row["command"] for row in rows[:load]], ["qmp_capabilities"],
                 directory + " no setup state mutation")
            before_proof = None
            expected_ops = [["irq_intercept_out"], ["clock_set", time_ns, time_ns]]
        same(operations(directory), expected_ops, directory + " no guest-memory repair or extra time advancement")
        if not request["answered"]:
            same(record.get("command_error"), "QMP connection closed", directory + " EOF after actual incoming send")
        elif request["error"]:
            error = request["response"]["error"]
            need(type(error) is dict and type(error.get("desc")) is str and
                 type(record.get("command_error")) is str and
                 error["desc"] in record["command_error"], directory + " actual QMP error receipt")
        else:
            same(record.get("command_error"), None, directory + " accepted incoming command result")
        for key, expected in {
            "destination_exit": 1, "request_sent": True, "expected_diagnostic": diagnostic,
            "specific_load_failure_observed": True, "target_directory": directory,
            "stderr_file": directory + "/qemu.stderr",
        }.items():
            same(record.get(key), expected, directory + "." + key)
        stderr = read(directory + "/qemu.stderr").decode("utf-8")
        need(diagnostic in stderr and "load of migration failed" in stderr and
             re.search(r"error while loading state[^\n]*" + re.escape(device_type), stderr) is not None,
             directory + " no specific device-load failure in actual receiver stderr")
        need(not any(event["level"] for event in qtest(directory)["irq_events"]),
             directory + " published completion IRQ during rejected incoming")
        return {"command": command_info, "qmp": transcript["provenance"],
                "qtest": qtest(directory)["provenance"],
                "clock_events": qtest(directory)["clock_events"],
                "stderr": provenance(directory + "/qemu.stderr"),
                "prewarm": before_proof, "incoming_request_observed": True,
                "incoming_qmp_answered": request["answered"],
                "specific_device_diagnostic": diagnostic,
                "actual_specific_device_load_failure": True,
                "reported_receiver_exit": 1, "process_exit_independently_reexecuted": False}

    same(list(rejected), ["strict_to_prewarmed_masked_requires_present_policy",
                          "cold_masked_to_strict_rejects_even_without_prior_masking"],
         "profile rejection identities")
    profile_cases = [
        ("strict_to_prewarmed_masked_requires_present_policy", strict_name, "strict-source",
         "reject-s-m", False, True, 23, True,
         device_type + ": wait-reserved migration profile mismatch (configured=1 incoming=0 present=0)"),
        ("cold_masked_to_strict_rejects_even_without_prior_masking", cold_name, "cold-source",
         "reject-m-s", True, False, 37, False,
         device_type + ": wait-reserved migration profile mismatch (configured=0 incoming=1 present=1)"),
    ]
    for name, filename, source_dir, directory, source_policy, target_policy, time_ns, prewarm, diagnostic in profile_cases:
        record = rejected[name]
        for key, value in {
            "source_policy": source_policy, "target_policy": target_policy,
            "source_status": "completed", "source_directory": source_dir,
            "snapshot_file": filename, "snapshot_sha256": sha(originals[filename]),
            "snapshot_bytes": len(originals[filename]), "stream_modified": False,
            "source_sections": receipts[filename],
        }.items():
            same(record.get(key), value, name + "." + key)
        if source_policy:
            same(record.get("source_masked_write_count"), 0, name + " zero-use enabled policy")
        proof = failed_incoming(directory, filename, record, diagnostic, target_policy, time_ns, prewarm)
        streams[filename]["profile_rejection"] = proof
        log_evidence[directory] = proof

    same(list(corrupted), ["single_bit_policy_corruption", "single_bit_last_voter_corruption"],
         "corruption rejection identities")
    original = originals[inflight_name]
    original_section = decoded[inflight_name]
    original_sub = original_section["subsection"]
    for kind, suffix, field, relative, old, new, directory, diagnostic in [
        ("policy", "policy-bit", "policy", 0, 1, 0, "reject-policy",
         device_type + ": invalid wait-reserved subsection policy 0"),
        ("last_voter", "last-voter-bit", "last_voter", 12, 3, 7, "reject-diag",
         device_type + ": invalid wait-reserved migration diagnostics"),
    ]:
        record = corrupted["single_bit_" + kind + "_corruption"]
        filename = "rpmh-wait-v2-subv1-" + suffix + ".migration"
        changed = read(filename)
        at = original_sub["payload_offset"] + relative
        same(len(changed), len(original), filename + " unchanged complete stream length")
        same(changed[:at], original[:at], filename + " unchanged complete prefix")
        same(changed[at + 4:], original[at + 4:], filename + " unchanged complete suffix")
        same(int.from_bytes(original[at:at + 4], "big"), old, filename + " original field")
        same(int.from_bytes(changed[at:at + 4], "big"), new, filename + " changed field")
        differences = [i for i, (left, right) in enumerate(zip(original, changed)) if left != right]
        same(differences, [at + 3], filename + " full-file byte differences")
        same((old ^ new).bit_count(), 1, filename + " exactly one changed bit")
        expected_sub = [1, 3, 3, 1, 0x20002]
        expected_sub[0 if kind == "policy" else 2] = new
        parsed = section(changed, "inflight", expected_sub, filename)
        same(parsed["decoded_main"], original_section["decoded_main"], filename + " complete main payload unchanged")
        for key in ("section_type", "header_offset", "payload_offset", "payload_bytes", "section_id", "footer_offset"):
            same(parsed[key], original_section[key], filename + " unchanged section framing " + key)
        for key, value in {
            "source_status": "completed", "source_directory": "mig-source",
            "original_sections": receipts[inflight_name],
            "field": field, "field_offset_in_subsection": relative, "field_width": 4,
            "absolute_field_offset": at, "original_value": old, "modified_value": new,
            "xor_mask": old ^ new, "changed_byte_offsets": differences, "changed_bits": 1,
            "original_file": inflight_name, "original_sha256": sha(original),
            "snapshot_file": filename, "snapshot_sha256": sha(changed),
            "snapshot_bytes": len(changed), "stream_modified": True,
            "original_stream_unchanged": True,
        }.items():
            same(record.get(key), value, filename + "." + key)
        proof = failed_incoming(directory, filename, record, diagnostic, True, 400)
        decoded_sections.append({**parsed, "file": filename, "purpose": "single-bit-corrupted-copy",
                                 "intentional_invalid_field": field,
                                 "all_other_serialized_fields_match_actual_source": True})
        streams[filename] = {**provenance(filename), "purpose": "specific-corrupted-copy-rejection",
                             "completed_roundtrip": False, "original_file": inflight_name,
                             "original_sha256": sha(original), "modified_field": field,
                             "absolute_field_offset": at, "field_bytes": 4,
                             "old_value": old, "new_value": new, "xor_mask": old ^ new,
                             "full_file_changed_byte_offsets": differences,
                             "exactly_one_bit_in_one_field_changed": True, "rejection": proof}
        log_evidence[directory] = proof

    # This existing gate case establishes that the audit getters also exist in
    # normal TCG. It stays paused, emits no QTest command, and never runs a CPU.
    tcg_name = "paused_tcg_exposes_the_same_pure_readonly_diagnostics"
    tcg_record = checks[tcg_name]
    tcg_command = command_proof("tcg", True, accel="tcg")
    tcg_log = qmp("tcg")
    tcg_entries = tcg_log["entries"]
    same([entry["command"] for entry in tcg_entries],
         ["qmp_capabilities", "query-status"] + ["qom-get"] * 15 + ["query-status"],
         "paused normal TCG exact QMP sequence")
    returned(tcg_entries[0], "TCG capabilities")
    first_tcg = stopped(tcg_entries[1], "TCG before")
    same(stopped(tcg_entries[-1], "TCG after"), first_tcg, "TCG stayed stopped")
    tcg_getters = match_getters(tcg_entries,
        diagnostic_pairs(cold_state) + [(device_path, policy_name, True)] + diagnostic_pairs(cold_state),
        "normal TCG actual read-only properties")
    same(operations("tcg"), [], "paused TCG no MMIO, time control, or IRQ interception")
    for key, expected in {"normal_tcg": True, "guest_cpu_executed": False,
                          "device_mmio_used": False,
                          "qom_observations": diagnostic_state()}.items():
        same(tcg_record.get(key), expected, "TCG report." + key)
    log_evidence["paused_normal_tcg"] = {"command": tcg_command,
        "qmp": tcg_log["provenance"], "qtest": qtest("tcg")["provenance"],
        "read_only_getters": tcg_getters, "cont_requests": 0, "qtest_requests": 0,
        "raw_statuses_reported_running_false": True}

    for filename, raw in originals.items():
        same(z.read(base + "/" + filename), raw, filename + " original ZIP stream remains unchanged")
    same(len(streams), 5, "five actual migration files")
    same(len(decoded_sections), 5, "five complete main-section decodes")
    same(sum(item["subsection"] is not None for item in decoded_sections), 4,
         "four actual policy subsections including the two modified copies")
    log_evidence["scope"] = {
        "test_commit": "4fb1a83a52d89d50066703c2e57b7aaa7d519c67",
        "test_sha256": test_sha, "actual_completed_roundtrips": 1,
        "actual_profile_rejections": 2, "actual_single_bit_corruption_rejections": 2,
        "unmodified_source_saves": 3, "actual_files": 5,
        "main_version": 2, "main_bytes_per_section": 19420,
        "subsection_version": 1, "subsection_payload_bytes": 24,
        "main_command_tuple_fields": ["msgid", "address", "data", "status", "response"],
        "main_fields_independently_decoded": ["digest32", "latency64", "four_DRV_headers",
            "all_32_TCS_states_and_512_command_slots", "all_98_resource_vote_staging_dirty_states",
            "XO_input_Clock_period64"],
        "main_payload_matches_literal_programming_scenario_not_hidden_qom_guesses": True,
        "subsection_fields_match_actual_stopped_source_qom": True,
        "cold_enabled_subsection_required_even_with_zero_count": True,
        "strict_and_cold_enabled_main_payloads_identical": True,
        "source_and_target_absolute_clock_aligned_at_ns": 400,
        "deadline_proven_at_999_then_1000_ns": True,
        "QMP_IDs_events_and_actual_replies_parsed": True,
        "QTest_clock_replies_and_async_IRQ_events_parsed": True,
        "binary_and_formal_source_identity_bound_by_outer_worker": True,
        "test_imported_or_executed": False, "guest_or_process_executed_by_verifier": False,
        "hardware_reserved_bit_semantics_claimed": False,
        "native_kernel_progress_or_Android_boot_claimed": False,
    }
    return {"rpmh_wait_streams_from_zip": streams,
            "rpmh_wait_decoded_sections": decoded_sections,
            "rpmh_wait_log_evidence": log_evidence}
