#!/usr/bin/env python3
"""Bounded, read-only GDSC relationships from the one pinned original DTB.

Publish selected qcom,gdsc properties and their direct provider/consumer
relationships only. Do not publish the complete tree, read hardware, execute a
guest, resolve partitions, or modify the input. Root supplies a fresh JSON path.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import stat
import struct


INPUT = "/work/device-assets/live-android-20261123.dtb"
EXPECTED_SHA = "5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207"
EXPECTED_BYTES = 973052
FAULT_ADDRESS = 0x0AD07004
SOURCE_COMMIT = "09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e"
MAX_NODES = 32768
MAX_PROPERTIES = 262144
MAX_REPORT_BYTES = 2 * 1024 * 1024
CONTROL_PROPERTIES = (
    "domain-addr", "sw-reset", "hw-ctrl-addr", "parent-supply", "vdd_parent-supply",
    "qcom,gds-timeout", "qcom,clk-dis-wait-val", "qcom,enable-root-clk",
    "qcom,force-enable-root-clk", "qcom,reset-aon-logic", "qcom,retain-mem",
    "qcom,retain-periph", "qcom,disallow-clear", "qcom,no-status-check-on-disable",
    "qcom,retain-regs", "qcom,skip-logic-collapse", "qcom,support-hw-trigger",
)
IDENTITY_PROPERTIES = {
    "compatible", "status", "phandle", "linux,phandle", "reg", "reg-names",
    "#address-cells", "#size-cells", "ranges", "#clock-cells", "#reset-cells",
    "#power-domain-cells", "regulator-name", "clock-frequency", "clock-output-names",
}
RELATION_PROPERTIES = {
    "clocks", "clock-names", "resets", "reset-names", "power-domains", "power-domain-names",
    "assigned-clocks", "assigned-clock-parents", "assigned-clock-rates",
}
REGULATOR_PROPERTIES = {
    "regulator-boot-on", "regulator-always-on", "regulator-initial-mode",
    "regulator-min-microvolt", "regulator-max-microvolt", "regulator-enable-ramp-delay",
    "regulator-system-load", "regulator-ramp-delay",
}
STRING_PROPERTIES = {
    "compatible", "status", "reg-names", "regulator-name", "clock-names", "reset-names",
    "power-domain-names", "clock-output-names", "qcom,msm-bus,name",
}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def metadata(st):
    return {name: getattr(st, name) for name in
            ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")}


def read_pinned_input():
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    fd = None
    try:
        for part in ("work", "device-assets"):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        basename = "live-android-20261123.dtb"
        before_path = os.stat(basename, dir_fd=directory, follow_symlinks=False)
        require(stat.S_ISREG(before_path.st_mode) and before_path.st_size == EXPECTED_BYTES,
                "fixed DTB must be the expected regular file and exact size")
        fd = os.open(basename, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
        before = os.fstat(fd)
        require(metadata(before) == metadata(before_path), "DTB changed before opening")
        chunks, remaining = [], EXPECTED_BYTES
        digest = hashlib.sha256()
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            require(bool(chunk), "short DTB read")
            chunks.append(chunk)
            digest.update(chunk)
            remaining -= len(chunk)
        after = os.fstat(fd)
        after_path = os.stat(basename, dir_fd=directory, follow_symlinks=False)
        require(metadata(before) == metadata(after) == metadata(after_path), "DTB changed during read")
    finally:
        if fd is not None:
            os.close(fd)
        os.close(directory)
    require(digest.hexdigest() == EXPECTED_SHA, "fixed DTB SHA256 mismatch")
    return b"".join(chunks), {"path": INPUT, "bytes": EXPECTED_BYTES, "sha256": digest.hexdigest(),
                            "read_only": True, "nofollow_all_path_components": True,
                            "stat_before": metadata(before), "stat_after": metadata(after),
                            "path_stat_after": metadata(after_path), "stat_unchanged": True}


def selected_property(name):
    return (name in IDENTITY_PROPERTIES or name in RELATION_PROPERTIES or
            name in REGULATOR_PROPERTIES or name in CONTROL_PROPERTIES or
            name.endswith("-supply") or name.startswith("qcom,msm-bus,"))


def parse_fdt(raw):
    names = ("magic", "total_size", "structure_offset", "strings_offset", "reserve_map_offset",
             "version", "last_compatible_version", "boot_cpu_id", "strings_size", "structure_size")
    header = dict(zip(names, struct.unpack_from(">10I", raw)))
    require(header["magic"] == 0xd00dfeed and header["version"] == 17 and
            header["last_compatible_version"] <= 17, "unsupported FDT header")
    total = header["total_size"]
    so, ss = header["structure_offset"], header["structure_size"]
    to, ts = header["strings_offset"], header["strings_size"]
    ro = header["reserve_map_offset"]
    require(40 <= total <= len(raw) and so % 4 == 0 and ss % 4 == 0 and ro % 8 == 0,
            "invalid FDT sizes or alignments")
    require(40 <= so and ss >= 12 and so + ss <= total and 40 <= to and ts > 0 and to + ts <= total,
            "FDT block bounds invalid")
    require(so + ss <= to or to + ts <= so, "structure and strings overlap")
    require(40 <= ro and ro + 16 <= total and not so <= ro < so + ss and not to <= ro < to + ts,
            "reserve-map start invalid")
    nodes, stack, position, end = {}, [], so, so + ss
    root_closed, end_seen, property_count = False, False, 0
    while position + 4 <= end:
        token = struct.unpack_from(">I", raw, position)[0]
        position += 4
        if token == 1:
            nul = raw.find(b"\0", position, end)
            require(nul >= 0 and nul - position <= 1024, "invalid node name")
            name = raw[position:nul].decode("utf-8", errors="strict")
            require("/" not in name, "slash in a node component")
            parent = stack[-1] if stack else None
            if parent is None:
                require(not nodes and not root_closed and name == "", "root is repeated or named")
                path = "/"
            else:
                path = parent.rstrip("/") + "/" + name
            require(path not in nodes and len(nodes) < MAX_NODES and len(stack) < 128,
                    "duplicate node or tree bound exceeded")
            nodes[path] = {"parent": parent, "properties": {}, "property_names": set()}
            stack.append(path)
            position = (nul + 4) & ~3
            require(position <= end, "node alignment exceeds structure")
        elif token == 2:
            require(bool(stack), "unbalanced node end")
            stack.pop()
            if not stack:
                root_closed = True
        elif token == 3:
            require(stack and position + 8 <= end, "property outside a node")
            length, name_offset = struct.unpack_from(">II", raw, position)
            position += 8
            require(position + length <= end and name_offset < ts, "property or name outside FDT bounds")
            nul = raw.find(b"\0", to + name_offset, to + ts)
            require(nul >= 0 and nul - to - name_offset <= 1024, "invalid property name")
            name = raw[to + name_offset:nul].decode("ascii", errors="strict")
            node = nodes[stack[-1]]
            require(name not in node["property_names"], "duplicate property")
            node["property_names"].add(name)
            property_count += 1
            require(property_count <= MAX_PROPERTIES, "property count exceeds bounded scope")
            if selected_property(name):
                require(length <= 65536, "selected property exceeds bounded scope")
                node["properties"][name] = raw[position:position + length]
            position = (position + length + 3) & ~3
            require(position <= end, "property alignment exceeds structure")
        elif token == 4:
            pass
        elif token == 9:
            require(root_closed and not stack and "/" in nodes, "FDT end before root closure")
            end_seen = True
            break
        else:
            raise ValueError("unknown FDT token")
    require(end_seen, "FDT end missing")
    return nodes, {k: v for k, v in header.items() if k != "boot_cpu_id"}


def cells(raw):
    require(len(raw) % 4 == 0, "cell-valued property has a partial u32")
    return list(struct.unpack(">" + str(len(raw) // 4) + "I", raw))


def strings(raw):
    require(raw and raw.endswith(b"\0"), "string-list property lacks a terminator")
    return raw[:-1].decode("utf-8", errors="strict").split("\0")


def property_record(name, raw):
    record = {"bytes": len(raw), "hex": raw.hex()}
    if len(raw) % 4 == 0:
        record["be32"] = cells(raw)
    if not raw:
        record["present_boolean"] = True
    elif name in STRING_PROPERTIES:
        record["strings"] = strings(raw)
    return record


def node_properties(node, names=None):
    return {k: property_record(k, v) for k, v in sorted(node["properties"].items())
            if names is None or k in names}


def reg_geometry(path, nodes):
    node = nodes[path]
    if "reg" not in node["properties"]:
        return None
    parent_path = node["parent"]
    require(parent_path in nodes, "reg has no parent")
    parent = nodes[parent_path]["properties"]
    ac = cells(parent.get("#address-cells", struct.pack(">I", 2)))
    sc = cells(parent.get("#size-cells", struct.pack(">I", 1)))
    require(len(ac) == len(sc) == 1 and 0 <= ac[0] <= 2 and 0 <= sc[0] <= 2 and ac[0] + sc[0] > 0,
            "unsupported parent address/size cells")
    ac, sc = ac[0], sc[0]
    words = cells(node["properties"]["reg"])
    require(len(words) % (ac + sc) == 0, "reg does not match parent cell widths")
    resources = []
    for start in range(0, len(words), ac + sc):
        address, size = 0, 0
        for word in words[start:start + ac]:
            address = (address << 32) | word
        for word in words[start + ac:start + ac + sc]:
            size = (size << 32) | word
        resources.append({"index": start // (ac + sc), "bus_address": address, "size": size,
                          "contains_observed_fault_address_number": address <= FAULT_ADDRESS < address + size})
    context = []
    cursor = parent_path
    while cursor is not None:
        ancestor = nodes[cursor]
        context.append({"path": cursor, "properties": node_properties(
            ancestor, {"#address-cells", "#size-cells", "ranges"})})
        cursor = ancestor["parent"]
    return {"parent": parent_path, "parent_address_cells": ac, "parent_size_cells": sc,
            "parent_address_cells_used_default": "#address-cells" not in parent,
            "parent_size_cells_used_default": "#size-cells" not in parent,
            "resources": resources, "ancestor_address_context": context,
            "physical_address_translation_performed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="Fresh JSON file in an existing work directory")
    args = parser.parse_args()
    output = args.out.absolute()
    require(output.parent.is_dir() and not output.exists() and not output.is_symlink(), "fresh output file required")
    raw, input_identity = read_pinned_input()
    nodes, header = parse_fdt(raw)
    domains = sorted(path for path, n in nodes.items() if
                     "compatible" in n["properties"] and "qcom,gdsc" in strings(n["properties"]["compatible"]))
    require(0 < len(domains) <= 96, "qcom,gdsc node count outside bound")
    phandles = {}
    for path, node in nodes.items():
        values = []
        for key in ("phandle", "linux,phandle"):
            if key in node["properties"]:
                words = cells(node["properties"][key])
                require(len(words) == 1 and words[0] not in (0, 0xffffffff), "invalid node phandle")
                values.append(words[0])
        require(not values or len(set(values)) == 1, "node phandle aliases differ")
        if values:
            require(values[0] not in phandles, "duplicate phandle across nodes")
            phandles[values[0]] = path
    domain_handles = {handle: path for handle, path in phandles.items() if path in domains}
    provider_paths, relationships, unresolved = set(), [], []

    def add_single(source, prop, relation):
        words = cells(nodes[source]["properties"][prop])
        if len(words) != 1 or words[0] not in phandles:
            unresolved.append({"node": source, "property": prop, "reason": "single phandle unresolved"})
            return
        target = phandles[words[0]]
        provider_paths.add(target)
        relationships.append({"from": source, "property": prop, "kind": relation,
                              "phandle": words[0], "arguments": [], "to": target})

    def parse_specs(source, prop, count_property):
        words = cells(nodes[source]["properties"][prop])
        cursor, result = 0, []
        while cursor < len(words):
            handle = words[cursor]
            cursor += 1
            if handle == 0 and prop == "assigned-clock-parents":
                result.append({"phandle": 0, "arguments": [], "to": None, "unspecified_parent": True})
                continue
            target = phandles.get(handle)
            if target is None:
                return None, "provider phandle unresolved"
            props = nodes[target]["properties"]
            if count_property not in props:
                return None, "provider has no " + count_property
            count = cells(props[count_property])
            if len(count) != 1 or count[0] > 16 or cursor + count[0] > len(words):
                return None, "provider argument count is invalid or truncated"
            result.append({"phandle": handle, "arguments": words[cursor:cursor + count[0]], "to": target})
            cursor += count[0]
        return result, None

    def add_specs(source, prop, count_property, kind):
        specs, error = parse_specs(source, prop, count_property)
        if error:
            unresolved.append({"node": source, "property": prop, "reason": error})
            return
        for index, entry in enumerate(specs):
            relationships.append({"from": source, "property": prop, "kind": kind,
                                  "specifier_index": index, **entry})
            if entry["to"] is not None:
                provider_paths.add(entry["to"])

    mappings = (("clocks", "#clock-cells"), ("assigned-clocks", "#clock-cells"),
                ("assigned-clock-parents", "#clock-cells"), ("resets", "#reset-cells"),
                ("power-domains", "#power-domain-cells"))
    for path in domains:
        props = nodes[path]["properties"]
        for name in props:
            if name.endswith("-supply") or name in ("domain-addr", "hw-ctrl-addr", "sw-reset"):
                add_single(path, name, "gdsc-direct-provider")
        for name, count in mappings:
            if name in props:
                add_specs(path, name, count, "gdsc-direct-provider")
    consumers = {}
    unresolved_consumer_power_domain_properties = 0
    for path, node in nodes.items():
        if path in domains:
            continue
        linked = set()
        for name, value in node["properties"].items():
            if name.endswith("-supply") and len(value) == 4 and cells(value)[0] in domain_handles:
                linked.add(name)
                add_single(path, name, "direct-gdsc-consumer")
        if "power-domains" in node["properties"]:
            specs, error = parse_specs(path, "power-domains", "#power-domain-cells")
            if error:
                unresolved_consumer_power_domain_properties += 1
            elif any(entry["phandle"] in domain_handles for entry in specs):
                linked.add("power-domains")
                for index, entry in enumerate(specs):
                    if entry["phandle"] in domain_handles:
                        relationships.append({"from": path, "property": "power-domains",
                                              "kind": "direct-gdsc-consumer", "specifier_index": index, **entry})
        if linked:
            consumers[path] = linked
            for name, count in mappings:
                if name in node["properties"] and name != "power-domains":
                    add_specs(path, name, count, "direct-consumer-clock-reset-provider")
    require(len(consumers) <= 256 and len(provider_paths) <= 128 and len(relationships) <= 4096,
            "relationship capture exceeds explicit bounds")
    domain_records, fault_matches = [], []
    for path in domains:
        geometry = reg_geometry(path, nodes)
        if geometry and any(x["contains_observed_fault_address_number"] for x in geometry["resources"]):
            fault_matches.append(path)
        domain_records.append({"path": path, "properties": node_properties(nodes[path]),
                               "source_control_property_present": {key: key in nodes[path]["properties"]
                                                                   for key in CONTROL_PROPERTIES},
                               "omitted_property_names": sorted(nodes[path]["property_names"] -
                                                                 set(nodes[path]["properties"])),
                               "decoded_reg": geometry})
    require(fault_matches, "observed fault number not covered by any selected GDSC reg")
    consumer_records = []
    for path, linked in sorted(consumers.items()):
        allowed = IDENTITY_PROPERTIES | RELATION_PROPERTIES | linked
        consumer_records.append({"path": path, "matching_gdsc_reference_properties": sorted(linked),
                                 "properties": node_properties(nodes[path], allowed),
                                 "decoded_reg": reg_geometry(path, nodes)})
    provider_records = [{"path": path, "properties": node_properties(nodes[path], IDENTITY_PROPERTIES),
                         "decoded_reg": reg_geometry(path, nodes)} for path in sorted(provider_paths)]
    report = {"schema": "tb371fc-gdsc-bounded-dtb-reference-capture-v1",
              "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "collector_source_sha256": sha(Path(__file__).read_bytes()),
              "triggering_qemu_source": SOURCE_COMMIT, "observed_fault_address": FAULT_ADDRESS,
              "scope": "All qcom,gdsc nodes: selected register/control properties and direct relationships only",
              "input_dtb": input_identity, "fdt_header": header,
              "gdsc_nodes": domain_records, "fault_number_reg_matches": fault_matches,
              "direct_consumers": consumer_records, "direct_providers": provider_records,
              "relationships": relationships, "unresolved_selected_relationships": unresolved,
              "unresolved_other_power_domain_properties_count": unresolved_consumer_power_domain_properties,
              "counts": {"gdsc_nodes": len(domain_records), "direct_consumers": len(consumer_records),
                         "direct_providers": len(provider_records), "relationships": len(relationships)},
              "interpretation": "Raw DT declarations and decoded phandle arguments, not measured handoff, power, clock rates, successful probe or DMA. Numeric reg addresses retain ancestor ranges; no implicit bus-to-physical translation is claimed.",
              "actions": {"input_written": False, "complete_dtb_published": False,
                          "unselected_property_values_published": False, "hardware_mmio": False,
                          "partitions_read": False, "qemu_started": False, "guest_executed": False,
                          "model_or_firmware_modified": False}, "output_path": str(output)}
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    require(len(encoded) <= MAX_REPORT_BYTES, "bounded report exceeds 2 MiB; no output written")
    with output.open("xb") as stream:
        stream.write(encoded)
    require(output.read_bytes() == encoded, "JSON output readback differs")
    print(json.dumps({"output": str(output), "bytes": len(encoded), "sha256": sha(encoded),
                      "counts": report["counts"], "fault_number_reg_matches": fault_matches,
                      "complete_dtb_published": False, "guest_executed": False}))


if __name__ == "__main__":
    main()
