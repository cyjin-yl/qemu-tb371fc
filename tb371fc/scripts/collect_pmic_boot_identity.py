#!/usr/bin/env python3
"""Stream kernel output and retain only whitelisted PMIC identity fields."""
from pathlib import Path
import datetime
import hashlib
import json
import re
import subprocess

def main():
    out = {"schema_version": 1, "kind": "tb371fc-pmic-boot-identity",
           "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "scope": "Read-only dmesg stream. Only PMIC name/revision/SID and arbiter version fields survive the allowlist; no raw log is saved or printed.",
           "command_argv": ["su", "-c", "/data/data/com.termux/files/usr/bin/python /data/data/com.termux/files/home/qemu-tb371fc-probe/collect_pmic_boot_identity.py"],
           "pmics": [], "arbiter": []}
    pmic = re.compile(r"\b(PM[A-Z0-9]{3,16}|SMB[0-9]{3,6})\s+v([0-9]+(?:\.[0-9]+){1,3})\b", re.I)
    sid = re.compile(r"PMIC\s*@\s*SID\s+(0x[0-9a-f]+|[0-9]+)", re.I)
    arb = re.compile(r"PMIC arbiter version\s+(v[0-9]+)\s*\((0x[0-9a-f]+)\)", re.I)
    process = subprocess.Popen(["/system/bin/dmesg"], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True, errors="replace")
    for line in process.stdout:
        match = arb.search(line)
        if match:
            value = {"version_class": match[1].lower(), "hardware_version": int(match[2],16)}
            if value not in out["arbiter"]:
                out["arbiter"].append(value)
        if not re.search(r"QPNP|PMIC|qpnp.revid", line, re.I):
            continue
        match = pmic.search(line)
        if not match:
            continue
        sid_match = sid.search(line)
        value = {"model": match[1].upper(), "revision": match[2]}
        if sid_match:
            value["sid"] = int(sid_match[1],0) if sid_match[1].lower().startswith("0x") else int(sid_match[1],10)
        if value not in out["pmics"]:
            out["pmics"].append(value)
    stderr = process.stderr.read(4096)
    out["dmesg_exit_code"] = process.wait()
    if out["dmesg_exit_code"]:
        out["access_error"] = "dmesg failed; no security setting was changed"
        out["stderr_sha256"] = hashlib.sha256(stderr.encode()).hexdigest()
    out["interpretation"] = "These are previously logged kernel driver observations from the current boot, not fresh raw MMIO/SPMI register reads. Absence of a matching line is not proof a chip is absent."
    dest = Path(__file__).with_name("android-pmic-boot-identity.json")
    assert not dest.exists(), "Preserve prior evidence"
    dest.write_text(json.dumps(out,indent=2,sort_keys=True)+"\n")
    print("IDENTITY_JSON:"+json.dumps(out,sort_keys=True))

if __name__ == "__main__":
    main()
