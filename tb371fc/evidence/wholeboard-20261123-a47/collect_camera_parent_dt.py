#!/usr/bin/env python3
"""Read only the pinned camera GDSC parent regulator and its bounded supply chain.

This does not publish a DT dump, access hardware, start a guest, or change the
input. Root alone may run it with a new JSON output path after source review.
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
SOURCE_COMMIT = "09c638d6a6d4fa4a4e4e332a7b39aa7bf7f5418e"
LEAF = "/soc/rsc@18200000/rpmh-regulator-mmcxlvl/regulator-pm8150a-s4-level"
AGGREGATE = "/soc/rsc@18200000/rpmh-regulator-mmcxlvl"
RSC = "/soc/rsc@18200000"
HANDLE = 104
MAX_NODES = 32768
MAX_PROPERTIES = 262144
MAX_REPORT_BYTES = 131072
IDENTITY_PROPERTIES = {
    "compatible", "status", "phandle", "linux,phandle", "reg", "reg-names",
    "#address-cells", "#size-cells", "ranges", "regulator-name",
}
RESOURCE_PROPERTIES = {
    "qcom,resource-name", "qcom,regulator-type", "qcom,always-wait-for-ack",
    "qcom,set", "qcom,init-voltage", "qcom,init-enable", "qcom,init-mode",
    "qcom,init-headroom", "qcom,default-voltage", "qcom,default-mode",
    "regulator-min-microvolt", "regulator-max-microvolt", "regulator-initial-mode",
    "regulator-boot-on", "regulator-always-on", "regulator-enable-ramp-delay",
    "regulator-system-load", "regulator-ramp-delay", "regulator-allow-bypass",
}
STRING_PROPERTIES = {"compatible", "status", "reg-names", "regulator-name", "qcom,resource-name"}


def selected_property(name):
    return name in IDENTITY_PROPERTIES or name in RESOURCE_PROPERTIES or name.endswith("-supply")


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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True,
                        help="New JSON file in an existing work directory")
    output = parser.parse_args().out.absolute()
    require(output.parent.is_dir() and not output.exists() and not output.is_symlink(),
            "fresh output file required")
    raw, identity = read_pinned_input()
    nodes, header = parse_fdt(raw)
    phandles = {}
    for path, node in nodes.items():
        aliases = []
        for key in ("phandle", "linux,phandle"):
            if key in node["properties"]:
                value = cells(node["properties"][key])
                require(len(value) == 1 and value[0] not in (0, 0xffffffff), "invalid phandle")
                aliases.append(value[0])
        require(not aliases or len(set(aliases)) == 1, "phandle aliases disagree")
        if aliases:
            require(aliases[0] not in phandles, "duplicate phandle")
            phandles[aliases[0]] = path
    require(phandles.get(HANDLE) == LEAF and LEAF in nodes and AGGREGATE in nodes and RSC in nodes,
            "pinned camera regulator path or phandle changed")
    require(nodes[LEAF]["parent"] == AGGREGATE and nodes[AGGREGATE]["parent"] == RSC,
            "camera regulator ancestry changed")
    selected = {LEAF: "camera-parent-regulator", AGGREGATE: "rpmh-resource-ancestor",
                RSC: "rsc-ancestor-identity"}
    queue = [(LEAF, 0), (AGGREGATE, 0)]
    visited, edges, unresolved = set(), [], []
    while queue:
        path, depth = queue.pop(0)
        if path in visited:
            continue
        visited.add(path)
        require(depth <= 8 and len(selected) <= 16, "supply-chain scope exceeded")
        for key, value in sorted(nodes[path]["properties"].items()):
            if not key.endswith("-supply"):
                continue
            word = cells(value)
            if len(word) != 1 or word[0] not in phandles:
                unresolved.append({"from": path, "property": key,
                                   "reason": "not a resolved single phandle"})
                continue
            target = phandles[word[0]]
            edges.append({"from": path, "property": key, "phandle": word[0], "to": target})
            selected.setdefault(target, "supply-chain-referent")
            queue.append((target, depth + 1))
            parent = nodes[target]["parent"]
            if parent is not None:
                props = nodes[parent]["properties"]
                comps = strings(props["compatible"]) if "compatible" in props else []
                if "qcom,resource-name" in props or any(x in comps for x in
                        ("qcom,rpmh-arc-regulator", "qcom,rpmh-vrm-regulator", "qcom,rpmh-xob-regulator")):
                    selected.setdefault(parent, "supply-referent-rpmh-ancestor")
                    queue.append((parent, depth + 1))
        require(len(edges) <= 64 and len(selected) <= 16, "supply-chain scope exceeded")
    records = []
    for path, role in sorted(selected.items()):
        props = nodes[path]["properties"]
        # RSC is an identity anchor only, never a request to publish its children.
        allowed = IDENTITY_PROPERTIES if path == RSC else IDENTITY_PROPERTIES | RESOURCE_PROPERTIES | {
            key for key in props if key.endswith("-supply")}
        record = {"path": path, "role": role, "parent_path": nodes[path]["parent"],
                  "properties": node_properties(nodes[path], allowed)}
        if path != RSC:
            record["source_resource_property_present"] = {key: key in props for key in sorted(RESOURCE_PROPERTIES)}
        records.append(record)
    report = {
        "schema": "tb371fc-camera-gdsc-parent-dtb-capture-v1",
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "collector_source_sha256": sha(Path(__file__).read_bytes()),
        "triggering_qemu_source": SOURCE_COMMIT,
        "input_dtb": identity, "fdt_header": header,
        "selected_leaf_phandle": HANDLE, "selected_leaf_path": LEAF,
        "scope": "One exact camera parent regulator, its resource/RSC ancestors and resolved supply-chain referents",
        "nodes": records, "supply_relationships": edges,
        "unresolved_supply_relationships": unresolved,
        "counts": {"published_nodes": len(records), "supply_relationships": len(edges)},
        "interpretation": "DT declarations only. No measured regulator level, actual power, voltage, firmware handoff, command-db address, successful probe or guest execution is inferred.",
        "actions": {"input_written": False, "complete_dtb_published": False,
                    "unselected_property_values_published": False, "hardware_mmio": False,
                    "partitions_read": False, "qemu_started": False, "guest_executed": False},
        "output_path": str(output),
    }
    encoded = (json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    require(len(encoded) <= MAX_REPORT_BYTES, "bounded report exceeds 128 KiB")
    with output.open("xb") as stream:
        stream.write(encoded)
    require(output.read_bytes() == encoded, "JSON output readback differs")
    print(json.dumps({"output": str(output), "bytes": len(encoded), "sha256": sha(encoded),
                      "counts": report["counts"], "complete_dtb_published": False,
                      "guest_executed": False}))


if __name__ == "__main__":
    main()
