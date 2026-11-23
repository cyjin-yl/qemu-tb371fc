#!/usr/bin/env python3
"""Read only nonunique PMIC identity exports and hardware driver bindings."""
from pathlib import Path
import datetime
import hashlib
import json

def read_small(path):
    try:
        value = path.read_bytes()
        if len(value) > 256:
            return {"error": "attribute exceeds identity size limit"}
        return {"value": value.decode("ascii").strip()}
    except (OSError, UnicodeError) as exc:
        return {"error": str(exc)}

def main():
    out = {
        "kind": "tb371fc-pmic-identity-exports",
        "schema_version": 1,
        "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "command_argv": ["su", "-c", "/data/data/com.termux/files/usr/bin/python /data/data/com.termux/files/home/qemu-tb371fc-probe/collect_pmic_identity.py"],
        "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "scope": "Read-only nonunique PMIC model/revision exports and device-driver binding metadata. No raw registers, events, power-state writes, serials or kernel logs.",
        "socinfo": {},
        "drivers": {}
    }
    for base in ("/sys/devices/soc0", "/sys/devices/system/soc/soc0"):
        for attr in ("pmic_model", "pmic_die_revision", "pmic_model1",
                     "pmic_die_revision1", "pmic_model2", "pmic_die_revision2",
                     "pmic_model3", "pmic_die_revision3"):
            path = Path(base)/attr
            out["socinfo"][str(path)] = read_small(path)
    attrs = {"rev1", "rev2", "rev3", "rev4", "type", "subtype", "revision",
             "revid", "pmic_revid", "pmic_model", "pmic_die_revision", "pmic_name"}
    drivers = [("platform","qpnp-revid"), ("platform","qcom,qpnp-revid"),
               ("platform","spmi_pmic_arb"), ("platform","spmi-pmic-arb"),
               ("spmi","qcom-spmi-pmic"), ("spmi","qcom,spmi-pmic")]
    for bus, driver in drivers:
        path = Path("/sys/bus")/bus/"drivers"/driver
        item = {"exists": path.is_dir(), "devices": []}
        if path.is_dir():
            for dev in sorted(path.iterdir()):
                if dev.name in {"bind","unbind","uevent","module","new_id","remove_id"} or not dev.is_dir():
                    continue
                names = sorted(x.name for x in dev.iterdir())
                item["devices"].append({
                    "name": dev.name,
                    "of_node_basename": (dev/"of_node").resolve().name if (dev/"of_node").exists() else None,
                    "attribute_names": names,
                    "identity_attributes": {name:read_small(dev/name) for name in names if name in attrs},
                })
        out["drivers"][str(path)] = item
    target = Path(__file__).with_name("android-pmic-identity.json")
    if target.exists():
        raise RuntimeError("Preserve previous receipt; use a new task filename")
    target.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    print(json.dumps(out,sort_keys=True))

if __name__ == "__main__":
    main()
