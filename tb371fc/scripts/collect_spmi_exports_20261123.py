#!/usr/bin/env python3
"""Allowlisted hardware metadata and kernel tracing-capability observation."""
from pathlib import Path
import datetime, gzip, hashlib, json, re, shutil

CONFIG_KEYS = ("BPF", "BPF_SYSCALL", "BPF_JIT", "BPF_EVENTS", "KPROBES",
               "KPROBE_EVENTS", "PERF_EVENTS", "FTRACE", "TRACING",
               "DEBUG_INFO_BTF", "KALLSYMS", "KALLSYMS_ALL", "IKCONFIG",
               "DEBUG_FS", "DEVMEM", "MODULES", "MODULE_SIG_FORCE")
ATTRS = ("version", "hw_version", "hw_ver", "arbiter_version", "revision",
         "apid_count", "max_periphs", "ee", "channel", "ppid_to_apid",
         "apid_map", "ownership", "owner", "mapping_table")
ROOTS = ("/sys/bus/platform/devices/c440000.qcom,spmi",
         "/sys/bus/platform/devices/c440000.qcom,spmi/spmi-0",
         "/sys/bus/spmi/devices/spmi-0")
CAP_PATHS = ("/sys/kernel/btf/vmlinux",
             "/sys/kernel/tracing/kprobe_events",
             "/sys/kernel/debug/spmi",
             "/sys/kernel/debug/tracing/kprobe_events")

def main():
    out = {"schema_version": 1, "kind": "tb371fc-spmi-export-discovery",
           "captured_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "collector_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
           "scope": "Read-only named hardware sysfs exports and allowlisted kernel config. No MMIO, arbitrary kernel memory, trace attachment, module loading, log dump, or settings changes.",
           "command_argv": ["su", "-c", "/data/data/com.termux/files/usr/bin/python /data/data/com.termux/files/home/qemu-tb371fc-probe/collect_spmi_exports_20261123.py"],
           "config": {}, "export_roots": [], "capability_paths": {},
           "platform": {}, "tools": {}, "not_read": [
               "bootargs", "serial numbers", "unique device identifiers",
               "userdata", "input events", "credentials", "raw register windows",
               "trace payloads", "kernel pointers", "MMIO", "arbitrary kernel memory"]}
    try:
        cfg = gzip.decompress(Path("/proc/config.gz").read_bytes()).decode()
        for key in CONFIG_KEYS:
            found = re.search(r"^CONFIG_" + key + r"=(.*)$", cfg, re.M)
            out["config"][key] = found[1] if found else (
                "n" if "# CONFIG_" + key + " is not set" in cfg else "not_present")
    except OSError as error:
        out["config_error"] = type(error).__name__
    for name in ROOTS:
        p = Path(name)
        row = {"path": name, "exists": p.exists(), "attributes": {}}
        if p.is_dir():
            row["directory_entries"] = sorted(child.name for child in p.iterdir())[:128]
            if (p / "driver").is_symlink():
                row["bound_driver"] = (p / "driver").resolve().name
            for attr in ATTRS:
                f = p / attr
                if not f.is_file():
                    continue
                try:
                    with f.open("rb") as handle:
                        data = handle.read(65537)
                    if len(data) > 65536:
                        row["attributes"][attr] = {"excluded": "oversized"}
                        continue
                    value = data.decode("ascii").strip()
                    if not re.fullmatch(r"[0-9A-Fa-fxXvV,:\s=+\[\]_-]{1,65536}", value):
                        row["attributes"][attr] = {"excluded": "non-numeric export"}
                        continue
                    row["attributes"][attr] = {"value": value,
                        "read_sha256": hashlib.sha256(data).hexdigest(),
                        "size_bytes": len(data)}
                except (OSError, UnicodeError) as error:
                    row["attributes"][attr] = {"error": type(error).__name__}
        out["export_roots"].append(row)
    for name in CAP_PATHS:
        p = Path(name)
        out["capability_paths"][name] = {"exists": p.exists(), "directory": p.is_dir()}
    for key in ("hw_platform", "platform_subtype", "platform_subtype_id", "platform_version"):
        try:
            value = (Path("/sys/devices/soc0") / key).read_text().strip()
            out["platform"][key] = value if re.fullmatch(r"[A-Za-z0-9_. /-]{0,64}", value) else "excluded_nonconforming"
        except OSError as error:
            out["platform"][key] = {"error": type(error).__name__}
    for name in ("bpftool", "bpftrace", "perf", "clang"):
        out["tools"][name] = shutil.which(name)
    out["interpretation"] = [
        "Absent exports are an access/evidence limitation, not evidence of absent hardware.",
        "Kernel config and file presence only evaluate tracing feasibility; no trace program was installed or attached.",
        "Platform fields are firmware-reported sysfs metadata; their relationship to AOP internal structures requires separate source proof."]
    dest = Path(__file__).with_name("android-spmi-export-discovery-20261123.json")
    data = json.dumps(out, indent=2, sort_keys=True) + "\n"
    with dest.open("x") as handle:
        handle.write(data)
    print(data, end="")

if __name__ == "__main__":
    main()
