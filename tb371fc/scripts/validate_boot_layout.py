#!/usr/bin/env python3
"""Verify paused QEMU's actual kernel/initrd/DTB layout against live DT ranges."""
from __future__ import annotations
import argparse
import datetime
import hashlib
import json
import re
import struct
import subprocess
from pathlib import Path
from inventory_dtb import decode_reg, integer, parse_dtb, u32s
from validate_machine import QMP, ValidationError

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def overlap(a, b):
    return a["start"] < b["start"] + b["size"] and b["start"] < a["start"] + a["size"]

def rom_entries(text):
    result = []
    for line in text.splitlines():
        m = re.fullmatch(r'addr=(?:0x)?([0-9a-fA-F]+) size=0x([0-9a-fA-F]+) mem=(ram|rom) name="(.*)"', line.strip())
        if m:
            result.append({"start": int(m[1], 16), "size": int(m[2], 16),
                           "memory": m[3], "name": m[4]})
    if not result:
        raise ValidationError("No physical ROM entries parsed from actual QEMU")
    return result

def only_rom(entries, name):
    matches = [e for e in entries if e["name"] == name]
    if len(matches) != 1:
        raise ValidationError("Expected one ROM entry for " + name)
    return matches[0]

def reservations(blob):
    nodes = parse_dtb(blob)
    result = []
    for name, props in nodes.items():
        if not name.startswith("/reserved-memory/") or "reg" not in props:
            continue
        for region in decode_reg(nodes, name):
            if "system_address" not in region:
                raise ValidationError("Untranslated reserved-memory node " + name)
            result.append({"node": name, "start": region["system_address"],
                           "size": region["size"], "no_map": "no-map" in props})
    off = struct.unpack_from(">I", blob, 16)[0]
    while True:
        start, size = struct.unpack_from(">QQ", blob, off)
        off += 16
        if not start and not size:
            break
        result.append({"node": "FDT memreserve", "start": start, "size": size,
                       "no_map": None})
    return nodes, result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--qemu", required=True, type=Path)
    p.add_argument("--kernel", required=True, type=Path)
    p.add_argument("--initrd", required=True, type=Path)
    p.add_argument("--reference-dtb", required=True, type=Path)
    p.add_argument("--work-dir", required=True, type=Path)
    p.add_argument("--loader-base", type=lambda x:int(x,0), default=0xa0000000)
    p.add_argument("--timeout", type=float, default=15)
    a = p.parse_args()
    for key in ("qemu", "kernel", "initrd", "reference_dtb", "work_dir"):
        setattr(a, key, getattr(a, key).resolve())
    a.work_dir.mkdir(parents=True, exist_ok=True)
    result_path = a.work_dir / "result.json"
    assert not result_path.exists(), "Preserve existing evidence; choose a fresh work directory"
    socket = a.work_dir / "monitor.sock"
    command = [str(a.qemu), "-M", "tb371fc", "-m", "8G", "-smp", "8",
               "-accel", "tcg", "-S", "-display", "none", "-serial", "null",
               "-monitor", "none", "-qmp", f"unix:{socket},server=on,wait=off",
               "-kernel", str(a.kernel), "-initrd", str(a.initrd)]
    result = {"schema_version": 1, "kind": "tb371fc-runtime-boot-layout",
              "started_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "command": command, "checks": [], "all_passed": False,
              "scope": "Paused normal TCG. Actual ROM map and memory-resident DTB; no guest boot verdict.",
              "sha256": {"qemu": sha(a.qemu), "kernel": sha(a.kernel),
                         "initrd": sha(a.initrd), "private_reference_dtb": sha(a.reference_dtb),
                         "validator": sha(Path(__file__))}}
    qmp = None
    process = None
    def check(name, passed, observed):
        result["checks"].append({"name": name, "passed": bool(passed), "observed": observed})
    try:
        with (a.work_dir/"qemu.log").open("wb") as log:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=log)
            qmp = QMP(socket, process, a.timeout)
            result["qemu_version"] = qmp.command("query-version")
            status = qmp.command("query-status")
            cpus = qmp.command("query-cpus-fast")
            check("paused-normal-tcg", not status["running"], status)
            check("eight-cpus", len(cpus) == 8, len(cpus))
            rawroms = qmp.command("human-monitor-command", {"command-line": "info roms"})
            (a.work_dir/"roms.txt").write_text(rawroms)
            entries = rom_entries(rawroms)
            kernel = only_rom(entries, str(a.kernel))
            initrd = only_rom(entries, str(a.initrd))
            dtb = only_rom(entries, "dtb")
            result["observed_roms"] = [
                {**x, "name": Path(x["name"]).name} for x in entries]
            if dtb["size"] <= 0 or dtb["size"] > 16*1024*1024:
                raise ValidationError("Unexpected DTB ROM size")
            guestdt = a.work_dir/"memory-resident.dtb"
            qmp.command("pmemsave", {"val": dtb["start"], "size": dtb["size"],
                                    "filename": str(guestdt)})
            raw = guestdt.read_bytes()
            guestnodes, guestres = reservations(raw)
            _, liveres = reservations(a.reference_dtb.read_bytes())
            result["sha256"]["memory_resident_dtb"] = sha(guestdt)
            head = a.kernel.read_bytes()[:64]
            assert head[56:60] == b"ARM\x64", "AArch64 Image required"
            text, imagesize = struct.unpack_from("<QQ", head, 8)
            if imagesize:
                actual_offset = text + (0x200000 if text < 4096 else 0)
            else:
                actual_offset, imagesize = 0x80000, a.kernel.stat().st_size
            expected_entry = a.loader_base + actual_offset
            check("kernel-actual-entry", kernel["start"] == expected_entry,
                  {"actual": kernel["start"], "expected": expected_entry})
            qmp.command("pmemsave", {"val": kernel["start"], "size": 64,
                                    "filename": str(a.work_dir/"kernel-header.bin")})
            check("kernel-header-in-memory", (a.work_dir/"kernel-header.bin").read_bytes() == head,
                  {"source_sha256": hashlib.sha256(head).hexdigest(),
                   "memory_sha256": sha(a.work_dir/"kernel-header.bin")})
            expected_initrd = max(a.loader_base+0x8000000, kernel["start"]+imagesize)
            expected_initrd = (expected_initrd+4095)&~4095
            check("initrd-actual-placement", initrd["start"] == expected_initrd,
                  {"actual": initrd["start"], "expected": expected_initrd, "size": initrd["size"]})
            if initrd["size"] != a.initrd.stat().st_size:
                raise ValidationError("Fixture expects raw initrd load, not a uImage ramdisk")
            qmp.command("pmemsave", {"val": initrd["start"], "size": initrd["size"],
                                    "filename": str(a.work_dir/"initrd-memory.bin")})
            check("initrd-bytes-in-memory", sha(a.work_dir/"initrd-memory.bin") == sha(a.initrd),
                  {"memory_sha256": sha(a.work_dir/"initrd-memory.bin"),
                   "source_sha256": sha(a.initrd)})
            chosen = {}
            for prop in ("linux,initrd-start", "linux,initrd-end"):
                rawprop = guestnodes.get("/chosen", {}).get(prop)
                if rawprop is None or len(rawprop) not in (4,8):
                    raise ValidationError("Missing/invalid guest initrd address " + prop)
                chosen[prop] = integer(u32s(rawprop))
            check("chosen-matches-loaded-initrd",
                  chosen["linux,initrd-start"] == initrd["start"] and
                  chosen["linux,initrd-end"] == initrd["start"]+initrd["size"], chosen)
            expected_dtb = (initrd["start"]+initrd["size"]+0x1fffff)&~0x1fffff
            check("dtb-actual-placement", dtb["start"] == expected_dtb,
                  {"actual": dtb["start"], "expected": expected_dtb, "size": dtb["size"]})
            memory = []
            for name, props in guestnodes.items():
                if props.get("device_type") == b"memory\x00" and "reg" in props:
                    memory += [{"start": r["system_address"], "size": r["size"]}
                               for r in decode_reg(guestnodes, name)]
            expected_memory = [{"start": 0x80000000, "size": 0x3bb00000},
                               {"start": 0xc0000000, "size": 0x1c0000000}]
            check("physical-8g-ram-map-with-native-gap",
                  sorted(memory, key=lambda x:x["start"]) == expected_memory, memory)
            loaded = [{"name": "kernel-memory-footprint", "start": kernel["start"],
                       "size": max(kernel["size"], imagesize)},
                      {"name": "initrd", "start": initrd["start"], "size": initrd["size"]},
                      {"name": "dtb", "start": dtb["start"], "size": dtb["size"]}]
            pairs = [(x["name"],y["name"]) for i,x in enumerate(loaded)
                     for y in loaded[i+1:] if overlap(x,y)]
            check("loaded-inputs-do-not-overlap", not pairs, pairs)
            for label, reserved in (("native",liveres),("generated",guestres)):
                collisions = [{"loaded": x["name"], "reserved": r}
                              for x in loaded for r in reserved
                              if x["size"] and r["size"] and overlap(x,r)]
                check(label+"-static-reservation-clearance", not collisions, collisions)
            missing = [x for x in loaded if not any(
                x["start"]>=r["start"] and x["start"]+x["size"]<=r["start"]+r["size"]
                for r in memory)]
            check("all-loaded-inputs-in-usable-ram", not missing, missing)
            result["all_passed"] = all(x["passed"] for x in result["checks"])
    except Exception as exc:
        result["error"] = str(exc)
    finally:
        if qmp:
            try:qmp.command("quit")
            except (OSError,ValueError,ValidationError):pass
            qmp.close()
        if process:
            try:process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            result["qemu_exit_code"] = process.returncode
        result["completed_utc"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        result["limits"] = [
            "No Android or Linux execution result: CPUs remain paused.",
            "Static reservations only; dynamic reserved-memory allocations are not simulated.",
            "DTB payload and loaded-memory samples remain in the private work directory.",
            "The native DT is a reference for reserved ranges, not proof of peripheral behavior."
        ]
        result_path.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        print(json.dumps({"all_passed":result["all_passed"],
                          "passed":sum(c["passed"] for c in result["checks"]),
                          "total":len(result["checks"]),"error":result.get("error"),
                          "result":str(result_path)}))
    return 0 if result["all_passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
