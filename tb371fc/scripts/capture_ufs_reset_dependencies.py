#!/usr/bin/env python3
"""Normalize only native UFS reset and dedicated-pin DT declarations."""
import argparse
import datetime
import hashlib
import json
from pathlib import Path
from inventory_dtb import parse_dtb, encode_property

PATHS = (
    "/soc/ufshc@1d84000",
    "/soc/pinctrl@f000000",
    "/soc/pinctrl@f000000/ufs_dev_reset_assert",
    "/soc/pinctrl@f000000/ufs_dev_reset_assert/config",
    "/soc/pinctrl@f000000/ufs_dev_reset_deassert",
    "/soc/pinctrl@f000000/ufs_dev_reset_deassert/config",
)
STRINGS = {"compatible", "status", "pinctrl-names", "pins", "function", "reset-names"}
CELLS = {"reg", "phandle", "pinctrl-0", "pinctrl-1", "drive-strength",
         "resets", "reset-gpios"}
BOOLEANS = {"bias-disable", "bias-pull-down", "bias-pull-up",
            "output-low", "output-high"}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Preserve evidence: use a new output file.")
    raw = args.dtb.read_bytes()
    nodes = parse_dtb(raw)
    selected = {}
    for path in PATHS:
        props = nodes.get(path)
        if props is None:
            selected[path] = {"missing": True}
            continue
        out = {}
        for key, value in props.items():
            if key in STRINGS:
                kind = "strings"
            elif key in CELLS:
                kind = "cells"
            elif key in BOOLEANS:
                kind = "boolean"
            else:
                continue
            out[key] = encode_property(value, kind)
        selected[path] = {
            "properties": out,
            "omitted_property_names": sorted(set(props) - set(out)),
        }
    result = {
        "schema_version": 1, "kind": "native-ufs-reset-dependencies",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "source": {"filename": args.dtb.name,
                   "sha256": hashlib.sha256(raw).hexdigest()},
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Allowed static hardware DT fields; no native register contents.",
        "nodes": selected,
        "interpretation": [
            "Native pinctrl names identify assert/deassert states.",
            "Assert has output-low; deassert has output-high for dedicated ufs_reset.",
            "The same states specify bias-pull-down and drive-strength 8.",
            "GCC core_reset is distinct from the dedicated UFS device-reset pad.",
            "This declaration establishes polarity, not measured pulse timing."
        ],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"output": str(args.out), "sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()}))

if __name__ == "__main__":
    main()
