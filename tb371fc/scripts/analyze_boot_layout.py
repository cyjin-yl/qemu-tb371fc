#!/usr/bin/env python3
"""Analyze only numeric boot layout from an already private live DT/Image."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import re
import struct
from pathlib import Path
from inventory_dtb import decode_reg, integer, parse_dtb, u32s

def sha(data):
    return hashlib.sha256(data).hexdigest()

def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dtb", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--native-memory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    blob = args.dtb.read_bytes()
    image = args.image.read_bytes()
    native_raw = args.native_memory.read_bytes()
    native = json.loads(native_raw)
    nodes = parse_dtb(blob)
    assert image[56:60] == b"ARM\x64", "Not an AArch64 Image header"
    text_offset, image_size, flags = struct.unpack_from("<QQQ", image, 8)
    chosen = {}
    for key in ("linux,initrd-start", "linux,initrd-end"):
        raw = nodes.get("/chosen", {}).get(key)
        if raw is None:
            chosen[key] = {"present": False}
        else:
            assert len(raw) in (4, 8), "Unexpected initrd address width"
            chosen[key] = {"present": True, "value": integer(u32s(raw)),
                           "be32": u32s(raw), "property_bytes": len(raw),
                           "sha256": sha(raw)}
    native_ranges = []
    for line in native["iomem"]["selected"]:
        m = re.fullmatch(r"([0-9a-fA-F]+)-([0-9a-fA-F]+) : (.+)", line)
        assert m
        start, end = int(m[1], 16), int(m[2], 16) + 1
        native_ranges.append({"start": start, "end_exclusive": end, "kind": m[3]})
    code = [r for r in native_ranges if r["kind"] == "Kernel code"]
    data = [r for r in native_ranges if r["kind"] == "Kernel data"]
    derived_base = (code[0]["start"] - text_offset
                    if len(code) == 1 and code[0]["start"] else None)
    queries = [{"name": "command-db", "start": 0x80860000, "size": 0x20000},
               {"name": "loader-neighborhood", "start": 0x9e000000,
                "size": 0x08000000}]
    queries += [{"name": r["kind"], "start": r["start"],
                 "size": r["end_exclusive"]-r["start"]}
                for r in code + data]
    if derived_base is not None:
        queries += [{"name": "Image-memory-footprint-at-native-base",
                     "start": derived_base + text_offset, "size": image_size},
                    {"name": "candidate-kernel-base", "start": derived_base,
                     "size": text_offset}]
    start = chosen["linux,initrd-start"].get("value")
    end = chosen["linux,initrd-end"].get("value")
    if start is not None and end is not None and end >= start:
        queries.append({"name": "native-initrd", "start": start, "size": end-start})
    elif start is not None or end is not None:
        raise ValueError("Incomplete/inverted initrd interval")
    regions, unresolved = [], []
    for path, props in nodes.items():
        if not path.startswith("/reserved-memory/") or "reg" not in props:
            continue
        for reg in decode_reg(nodes, path):
            if "system_address" not in reg or "size" not in reg:
                unresolved.append({"node": path, **reg})
                continue
            begin, length = reg["system_address"], reg["size"]
            hits = [v["name"] for v in queries if length and v["size"]
                    and overlaps((begin, begin+length),
                                 (v["start"], v["start"]+v["size"]))]
            if hits:
                regions.append({"node": path, "start": begin, "size": length,
                                "end_exclusive": begin+length,
                                "no_map": "no-map" in props,
                                "reusable": "reusable" in props,
                                "status": props.get("status", b"okay\x00").rstrip(b"\x00").decode("ascii"),
                                "overlaps_queries": hits,
                                "reg_property_sha256": sha(props["reg"])})
    memreserve = []
    off = struct.unpack_from(">I", blob, 16)[0]
    while True:
        begin, length = struct.unpack_from(">QQ", blob, off)
        off += 16
        if begin == 0 and length == 0:
            break
        hits = [v["name"] for v in queries if length and v["size"]
                and overlaps((begin, begin+length),
                             (v["start"], v["start"]+v["size"]))]
        if hits:
            memreserve.append({"start": begin, "size": length,
                               "end_exclusive": begin+length,
                               "overlaps_queries": hits})
    for query in queries:
        query["end_exclusive"] = query["start"] + query["size"]
        query["overlapping_reserved_nodes"] = [
            r["node"] for r in regions if query["name"] in r["overlaps_queries"]]
        query["overlapping_no_map_nodes"] = [
            r["node"] for r in regions if r["no_map"]
            and query["name"] in r["overlaps_queries"]]
        query["overlaps_memreserve"] = any(
            query["name"] in r["overlaps_queries"] for r in memreserve)
    result = {
        "schema_version": 1,
        "kind": "tb371fc-current-boot-numeric-layout",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": {
            "private_live_dtb": {"filename": args.dtb.name, "sha256": sha(blob)},
            "current_active_partition_kernel": {"filename": args.image.name,
                 "sha256": sha(image), "bytes": len(image)},
            "native_hardware_receipt": {"filename": args.native_memory.name,
                 "sha256": sha(native_raw), "captured_utc": native["captured_utc"]},
            "analyzer_sha256": sha(Path(__file__).read_bytes()),
        },
        "native_iomem": native_ranges,
        "chosen_addresses": chosen,
        "image_header": {"text_offset": text_offset, "image_size": image_size,
                         "flags": flags},
        "derived_loader_base": derived_base,
        "derivation": "Observed Kernel code start minus AArch64 Image text_offset. This is an inference about this boot, not a read of bootloader state.",
        "interval_convention": "All derived intervals are [start, end_exclusive).",
        "queries": queries, "reserved_regions_in_scope": regions,
        "memreserve_entries_in_scope": memreserve,
        "unresolved_reserved_translations": unresolved,
        "limitations": [
            "No bootargs, serials, seeds or firmware payload values are emitted.",
            "Current partition kernel is not certified pristine factory firmware.",
            "Observed Android initrd addresses do not prescribe QEMU's initrd size or address.",
            "No-overlap findings apply to queried intervals; QEMU DTB/initrd must be checked at their final generated locations.",
            "Only statically addressed reserved-memory entries and FDT memreserve entries can be checked; dynamic reservations without reg need separate allocation."
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    assert not args.output.exists(), "Preserve existing evidence; choose a new output"
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+"\n")
    print(json.dumps({"output": str(args.output), "sha256": sha(args.output.read_bytes()),
                      "derived_loader_base": derived_base, "chosen": chosen,
                      "queries": queries, "unresolved": unresolved}, indent=2))

if __name__ == "__main__":
    main()
