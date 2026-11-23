#!/usr/bin/env python3
"""Bounded metadata analysis of the two captured TB371FC active-a firmware files.
No firmware payload, symbol table, certificate, or arbitrary string is exported.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import sys
import traceback

EXPECTED = {
    "xbl_a": (3670016, "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55"),
    "xbl_config_a": (131072, "bb16d00d0f7581caa641d2f1c736adf5cc6505403c67250c47e8a893cbda93ec"),
}
MARKERS = (
    b"smem_vers", b"smem_toc_vers", b"smem_partitions", b"smem_max_items",
    b"/core/mproc/smem", b"smem_boot_init", b"boot_smem_init",
    b"smem_xbl_loader", b"$TOC", b"$PRT", b"SIII",
)
SOURCES = {
    "elf_abi": "https://refspecs.linuxfoundation.org/elf/gabi4+/ch4.eheader.html",
    "uefi_pi": "https://uefi.org/specs/PI/1.8/V3_Code_Definitions.html",
    "smem_protocol": "https://android.googlesource.com/kernel/msm/+/android-7.1.0_r0.2/Documentation/arm/msm/msm_smem.txt",
}
def utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(data):
    return hashlib.sha256(data).hexdigest()
def within(offset, size, total):
    return 0 <= offset <= total and 0 <= size <= total - offset
def offsets(data, needle, cap=64):
    hits = []
    pos = 0
    while len(hits) < cap:
        pos = data.find(needle, pos)
        if pos < 0:
            break
        hits.append(pos)
        pos += 1
    total = data.count(needle)
    return {"count": total, "offsets": hits, "offsets_truncated": total > len(hits)}
def elf_metadata(data):
    result = {"magic_at_zero": data[:4] == b"\x7fELF", "errors": []}
    if not result["magic_at_zero"]:
        return result
    if len(data) < 16:
        result["errors"].append("truncated ELF identification")
        return result
    cls, byte_order, ident_version = data[4:7]
    result.update(elf_class=cls, byte_order=byte_order, ident_version=ident_version)
    if cls not in (1, 2) or byte_order not in (1, 2) or ident_version != 1:
        result["errors"].append("unsupported ELF identification")
        return result
    endian = "<" if byte_order == 1 else ">"
    expected_eh = 52 if cls == 1 else 64
    expected_ph = 32 if cls == 1 else 56
    if len(data) < expected_eh:
        result["errors"].append("truncated ELF header")
        return result
    values = struct.unpack_from(endian + ("HHIIIIIHHHHHH" if cls == 1 else "HHIQQQIHHHHHH"), data, 16)
    (etype, machine, version, entry, phoff, shoff, flags, ehsize,
     phsize, phnum, shsize, shnum, shstr) = values
    result.update(header_decoded=True, type=etype, machine=machine, version=version,
                  entry=hex(entry), flags=hex(flags), header_size=ehsize,
                  program_header_offset=phoff, program_header_size=phsize,
                  program_header_count=phnum, section_header_offset=shoff,
                  section_header_size=shsize, section_header_count=shnum,
                  section_name_table_index=shstr)
    if ehsize != expected_eh or version != 1:
        result["errors"].append("unexpected ELF header size/version")
    if phnum == 0xffff:
        result["errors"].append("extended program-header count is deliberately unsupported")
        return result
    if phnum > 256 or (phnum and (phsize < expected_ph or not within(phoff, phsize * phnum, len(data)))):
        result["errors"].append("invalid or over-limit program-header table")
        return result
    segments = []
    for i in range(phnum):
        raw = struct.unpack_from(endian + ("IIIIIIII" if cls == 1 else "IIQQQQQQ"), data, phoff + i * phsize)
        if cls == 1:
            typ, off, va, pa, fs, ms, fl, al = raw
        else:
            typ, fl, off, va, pa, fs, ms, al = raw
        item = {"index": i, "type": typ, "flags": fl, "file_offset": off,
                "vaddr": hex(va), "paddr": hex(pa), "filesz": fs, "memsz": ms,
                "alignment": al, "file_range_valid": within(off, fs, len(data))}
        if typ == 1:
            item["load_file_fits_memory"] = fs <= ms
            item["alignment_is_power_of_two_or_zero"] = al == 0 or al & (al - 1) == 0
        segments.append(item)
    result["segments"] = segments
    result["program_table_valid"] = True
    normalized_entry = entry & ~1 if machine == 40 else entry
    result["entry_low_bit_interpreted_as_thumb"] = machine == 40 and bool(entry & 1)
    result["entry_executable_loads_by_vaddr"] = [
        s["index"] for s in segments if s["type"] == 1 and s["flags"] & 1
        and int(s["vaddr"], 16) <= normalized_entry < int(s["vaddr"], 16) + s["memsz"]
    ]
    result["entry_executable_loads_by_paddr"] = [
        s["index"] for s in segments if s["type"] == 1 and s["flags"] & 1
        and int(s["paddr"], 16) <= normalized_entry < int(s["paddr"], 16) + s["memsz"]
    ]
    result["entry_note"] = "Static segment membership only; not evidence of execution, reset mapping, or a specific CPU."
    return result

def fv_metadata(data):
    sig = offsets(data, b"_FVH", 128)
    candidates = []
    for signature in sig["offsets"]:
        start = signature - 40
        r = {"signature_offset": signature, "candidate_header_offset": start, "errors": []}
        candidates.append(r)
        if not within(start, 56, len(data)):
            r["errors"].append("fixed header outside input")
            continue
        length = struct.unpack_from("<Q", data, start + 32)[0]
        attr = struct.unpack_from("<I", data, start + 44)[0]
        header_len, checksum, ext = struct.unpack_from("<HHH", data, start + 48)
        reserved, revision = data[start + 54:start + 56]
        r.update(length=length, attributes=hex(attr), header_length=header_len,
                 checksum_field=checksum, extended_header_offset=ext, revision=revision)
        if header_len < 64 or header_len % 2 or not within(start, header_len, len(data)):
            r["errors"].append("invalid bounded FV header length")
            continue
        if length < header_len or not within(start, length, len(data)):
            r["errors"].append("FV extent outside input")
        hdr = data[start:start + header_len]
        checksum_ok = sum(struct.unpack("<" + "H" * (header_len // 2), hdr)) & 0xffff == 0
        r["header_checksum_valid"] = checksum_ok
        if not checksum_ok:
            r["errors"].append("FV header checksum mismatch")
        if reserved != 0 or revision != 2:
            r["errors"].append("unsupported FV reserved/revision fields")
        blocks = []
        terminator = False
        pos = 56
        for i in range(4096):
            if pos + 8 > header_len:
                break
            count, blocksize = struct.unpack_from("<II", hdr, pos)
            pos += 8
            if count == 0 and blocksize == 0:
                terminator = True
                break
            if not count or not blocksize:
                r["errors"].append("partial zero in FV block map")
                break
            blocks.append({"count": count, "length": blocksize})
        r["block_map"] = blocks
        r["block_map_terminated"] = terminator
        r["block_map_total_size"] = sum(x["count"] * x["length"] for x in blocks)
        if not terminator or r["block_map_total_size"] != length:
            r["errors"].append("unterminated or inconsistent FV block map")
        if ext:
            if ext < 56 or ext + 20 > length or not within(start + ext, 20, len(data)):
                r["errors"].append("invalid FV extended-header position")
            else:
                ext_size = struct.unpack_from("<I", data, start + ext + 16)[0]
                r["extended_header_size"] = ext_size
                if ext_size < 20 or ext + ext_size > length or not within(start + ext, ext_size, len(data)):
                    r["errors"].append("invalid FV extended-header size")
        r["structurally_valid_candidate"] = not r["errors"]
        r["file_system_and_volume_guids_exported"] = False
        r["files_or_compressed_sections_decoded"] = False
    return {"signature_count": sig["count"], "candidates_truncated": sig["offsets_truncated"],
            "candidates": candidates,
            "valid_candidate_count": sum(c.get("structurally_valid_candidate", False) for c in candidates),
            "interpretation": "Signature matches may be literals in code; only bounded checksum/block-map candidates are accepted. No FFS, executable section, or compressed payload is extracted."}

def memory_map_literals(data):
    pattern = rb'(?m)^[ \t]*(0x[0-9a-fA-F]{1,16})[ \t]*,[ \t]*(0x[0-9a-fA-F]{1,16})[ \t]*,[ \t]*"(SMEM|Kernel)"[ \t]*,'
    found = []
    for m in re.finditer(pattern, data):
        if len(found) == 32:
            break
        found.append({"offset": m.start(), "base": hex(int(m.group(1), 16)),
                      "size": int(m.group(2), 16), "label": m.group(3).decode("ascii"),
                      "active_configuration_proven": False})
    return found

def analyze(name, path):
    size, expected_sha = EXPECTED[name]
    import stat
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size != size:
            raise ValueError(name + ": expected regular input with exact captured size")
        chunks = []
        remaining = size + 1
        while remaining:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if os.fstat(fd).st_size != size:
            raise ValueError(name + ": input size changed during read")
    finally:
        os.close(fd)
    got = digest(data)
    if len(data) != size or got != expected_sha:
        raise ValueError(name + ": capture hash or size mismatch")
    return {"name": name, "private_path": str(path), "size": size, "sha256": got,
            "expected_capture_match": True, "elf": elf_metadata(data),
            "firmware_volumes": fv_metadata(data),
            "allowlisted_markers": {m.decode("ascii"): offsets(data, m) for m in MARKERS},
            "memory_map_literal_candidates": memory_map_literals(data),
            "limitations": ["Marker presence/offset does not establish a table, selected configuration, initializer call, or runtime value.",
                            "No complete strings, certificates, unique identities, symbols, or firmware code are exported.",
                            "No firmware execution or initialized SMEM handoff was produced."]}

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--xbl", type=Path, required=True)
    p.add_argument("--xbl-config", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(mode=0o700, exist_ok=False)
    rc = 1
    try:
        result = {"schema": "tb371fc-xbl-metadata-v1", "started_utc": utc(),
                  "script_sha256": digest(Path(__file__).read_bytes()), "sources": SOURCES,
                  "input_capture_kind": "current-active-a-partition-backup",
                  "firmware_payload_exported": False, "runtime_or_profile_validation": False,
                  "inputs": [analyze("xbl_a", a.xbl), analyze("xbl_config_a", a.xbl_config)]}
        result["finished_utc"] = utc()
        raw = (json.dumps(result, sort_keys=True, indent=2) + "\n").encode()
        (a.out / "metadata.json").write_bytes(raw)
        summary = {"metadata_sha256": digest(raw), "metadata_path": str(a.out / "metadata.json"),
                   "inputs": [{"name": r["name"], "sha256": r["sha256"],
                               "elf_class": r["elf"].get("elf_class"),
                               "elf_machine": r["elf"].get("machine"),
                               "entry": r["elf"].get("entry"),
                               "valid_fv_candidates": r["firmware_volumes"]["valid_candidate_count"],
                               "marker_counts": {k: v["count"] for k, v in r["allowlisted_markers"].items()},
                               "memory_map_literal_candidates": r["memory_map_literal_candidates"]}
                              for r in result["inputs"]]}
        (a.out / "summary.json").write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n")
        print(json.dumps(summary, sort_keys=True))
        rc = 0
    except Exception:
        (a.out / "run.log").write_text(traceback.format_exc())
        print(json.dumps({"metadata_complete": False, "error_log": str(a.out / "run.log")}))
    finally:
        (a.out / "run.exit").write_text(str(rc) + "\n")
    return rc
if __name__ == "__main__":
    sys.exit(main())
