#!/usr/bin/env python3
"""Allowlisted SPMI/PDC/TLMM fields from an already private live DT."""
from pathlib import Path
import argparse, datetime, hashlib, json
from inventory_dtb import parse_dtb, encode_property, decode_reg

ROOTS = ("/soc/qcom,spmi@c440000", "/soc/pinctrl@f000000",
         "/soc/interrupt-controller@b220000")
STRINGS = {"compatible", "reg-names", "status", "interrupt-names", "clock-names",
           "pinctrl-names", "gpio-ranges-group-names"}
CELLS = {"reg", "interrupts", "interrupts-extended", "interrupt-parent", "phandle",
         "#address-cells", "#size-cells", "#interrupt-cells", "#gpio-cells",
         "gpio-ranges", "ngpios", "qcom,ee", "qcom,channel", "qcom,num-gpios",
         "qcom,gpios-disallowed", "qcom,pdc-ranges", "qcom,pdc-gpios",
         "irqdomain-map", "irqdomain-map-mask", "irqdomain-map-pass-thru",
         "wakeup-parent", "clocks"}
BOOLEANS = {"interrupt-controller", "gpio-controller", "no-map", "reusable"}

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dtb", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    blob = a.dtb.read_bytes()
    nodes = parse_dtb(blob)
    output = {"schema_version": 1, "kind": "tb371fc-spmi-pdc-tlmm-dt-evidence",
              "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "source": {"filename": a.dtb.name,
                         "sha256": hashlib.sha256(blob).hexdigest()},
              "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "scope": "Allowlisted hardware properties only. No chosen, bootargs, serials, seeds or payloads.",
              "nodes": {}}
    for name, props in nodes.items():
        if not any(name == root or name.startswith(root+"/") for root in ROOTS):
            continue
        selected, omitted = {}, []
        for prop, raw in props.items():
            if prop in STRINGS:
                kind = "strings"
            elif prop in CELLS:
                kind = "cells"
            elif prop in BOOLEANS:
                kind = "boolean"
            else:
                omitted.append(prop)
                continue
            selected[prop] = encode_property(raw, kind)
        record = {"properties": selected, "omitted_property_names": sorted(omitted)}
        if name in ROOTS and "reg" in props:
            record["cpu_physical_register_regions"] = decode_reg(nodes, name)
        elif name.startswith(ROOTS[0]+"/"):
            record["reg_interpretation"] = "SPMI SID/address-type or PMIC-local peripheral address fields; these are not CPU physical MMIO regions."
        output["nodes"][name] = record
    a.output.parent.mkdir(parents=True, exist_ok=True)
    assert not a.output.exists(), "Preserve existing evidence; choose a new output"
    a.output.write_text(json.dumps(output,indent=2,sort_keys=True)+"\n")
    print(json.dumps({"nodes":len(output["nodes"]),"output":str(a.output),
                      "sha256":hashlib.sha256(a.output.read_bytes()).hexdigest()}))

if __name__ == "__main__":
    main()
