#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded static A64 observations; never execute firmware or select a native ABI."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import struct
import subprocess
import sys

INPUT = Path("/work/device-assets/xbl-capture-20261123-01/xbl_a.bin")
INPUT_SIZE = 3670016
INPUT_SHA = "eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55"
ROOT = Path("/work/xbl-dal-accessor-observe-20261123-01")
SEEDS = ((0x1481b8e8, 168, 0x98e8), (0x1481ba40, 96, 0x9a40))
REFS = {0x14874f29: "/core/mproc/smem", 0x14874f72: "smem_vers",
        0x14875022: "smem_max_items"}
DATA_TARGETS = {0x14879510: "structure_table_candidate",
                0x148796e0: "plain_slot_candidate",
                0x148798b8: "sized_slot_candidate",
                0x14878590: "sized_target_candidate",
                0x14879fd4: "structure_property_header"}
# Filled only from root-reviewed CT executable metadata, never a Termux guess.
# Required keys: family ("gnu"), objdump/as: {path, realpath, bytes, sha256, version_line}.
TOOLCHAIN_PIN = {
    "family": "gnu",
    "objdump": {
        "path": "/usr/bin/aarch64-linux-gnu-objdump",
        "realpath": "/usr/bin/aarch64-linux-gnu-objdump",
        "bytes": 465800,
        "sha256": "b9ae08c9c02c91ced02b80a140c937ba119fe073872fff701f67477bf597a5eb",
        "version_line": "GNU objdump (GNU Binutils for Debian) 2.44"
    },
    "as": {
        "path": "/usr/bin/aarch64-linux-gnu-as",
        "realpath": "/usr/bin/aarch64-linux-gnu-as",
        "bytes": 1146008,
        "sha256": "4e623869baaf4e0e165573b095f0aedaf47fe26f000ea8f3288e1c1a582cc644",
        "version_line": "GNU assembler (GNU Binutils for Debian) 2.44"
    }
}
MAX_OUTPUT = 12288
MAX_TOOL_TEXT = 262144

class Reject(Exception):
    pass

def need(ok, code):
    if not ok:
        raise Reject(code)

def digest(raw):
    return hashlib.sha256(raw).hexdigest()

def read_regular(path, limit):
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW)
    try:
        a = os.fstat(fd)
        need(stat.S_ISREG(a.st_mode) and a.st_size <= limit, "regular_file_bound")
        chunks, total = [], 0
        while total <= limit:
            chunk = os.read(fd, min(131072, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        b = os.fstat(fd)
        need((a.st_dev, a.st_ino, a.st_size, a.st_mtime_ns, a.st_ctime_ns) ==
             (b.st_dev, b.st_ino, b.st_size, b.st_mtime_ns, b.st_ctime_ns),
             "file_changed")
        raw = b"".join(chunks)
        need(len(raw) == a.st_size, "file_read_length")
        return raw
    finally:
        os.close(fd)

def new_file(path, raw):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        view = memoryview(raw)
        while view:
            n = os.write(fd, view)
            need(n > 0, "short_write")
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)

def extent(start, size, end):
    need(type(start) is int and type(size) is int and
         0 <= start <= end and 0 <= size <= end - start, "extent")

class ELF:
    def __init__(self, raw):
        need(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01", "elf_identity")
        h = struct.unpack_from("<HHIQQQIHHHHHH", raw, 16)
        need(h[1] == 183 and h[2] == 1 and h[7] == 64 and h[8] == 56
             and 0 < h[9] <= 64, "elf_header")
        extent(h[4], h[9] * 56, len(raw))
        self.raw, self.loads = raw, []
        for i in range(h[9]):
            p = struct.unpack_from("<IIQQQQQQ", raw, h[4] + i * 56)
            typ, flags, off, va, pa, filesz, memsz, align = p
            if typ != 1:
                continue
            extent(off, filesz, len(raw))
            extent(va, memsz, 1 << 64)
            need(filesz <= memsz, "load_size")
            self.loads.append(dict(index=i, flags=flags, off=off, va=va,
                                   filesz=filesz, memsz=memsz))

    def mapping(self, va, size, executable=False):
        extent(va, size, 1 << 64)
        found = [p for p in self.loads if p["va"] <= va
                 and size <= p["va"] + p["filesz"] - va]
        need(size > 0 and len(found) == 1, "unique_file_mapping")
        p = found[0]
        if executable:
            need(p["flags"] & 1 and p["index"] != 8, "non_fv_executable_mapping")
        return dict(va=va, size=size, load_index=p["index"],
                    file_offset=p["off"] + va - p["va"], flags=p["flags"])

    def code(self, va, size):
        need(va % 4 == 0 and size % 4 == 0, "a64_alignment")
        m = self.mapping(va, size, True)
        return self.raw[m["file_offset"]:m["file_offset"] + size], m

def analysis_elf(code, va):
    need(code and len(code) % 4 == 0 and va % 4 == 0, "analysis_geometry")
    names = b"\0.text\0.shstrtab\0"
    nameoff, shoff = 64 + len(code), (64 + len(code) + len(names) + 7) & ~7
    ident = b"\x7fELF" + bytes((2, 1, 1, 0, 0)) + bytes(7)
    header = struct.pack("<16sHHIQQQIHHHHHH", ident, 2, 183, 1, 0, 0, shoff,
                         0, 64, 0, 0, 64, 3, 2)
    text = struct.pack("<IIQQQQIIQQ", 1, 1, 6, va, 64, len(code), 0, 0, 4, 0)
    strings = struct.pack("<IIQQQQIIQQ", 7, 3, 0, 0, nameoff, len(names), 0, 0, 1, 0)
    return header + code + names + bytes(shoff - nameoff - len(names)) + bytes(64) + text + strings

def object_text(raw):
    need(len(raw) >= 64 and raw[:7] == b"\x7fELF\x02\x01\x01", "control_elf")
    h = struct.unpack_from("<HHIQQQIHHHHHH", raw, 16)
    need(h[0] == 1 and h[1] == 183 and h[10] == 64 and 0 < h[11] <= 64
         and h[12] < h[11], "control_sections")
    extent(h[5], h[11] * 64, len(raw))
    sections = [struct.unpack_from("<IIQQQQIIQQ", raw, h[5] + i * 64)
                for i in range(h[11])]
    ns = sections[h[12]]
    extent(ns[4], ns[5], len(raw))
    names = raw[ns[4]:ns[4] + ns[5]]
    found = []
    for sec in sections:
        need(sec[0] < len(names), "control_section_name")
        end = names.find(b"\0", sec[0])
        need(end >= 0, "control_section_nul")
        if names[sec[0]:end] == b".text":
            extent(sec[4], sec[5], len(raw))
            found.append(raw[sec[4]:sec[4] + sec[5]])
        # The self-authored control must not depend on unresolved relocations.
        need(not (sec[1] in (4, 9) and sec[5]), "control_relocations")
    need(len(found) == 1 and found[0] and len(found[0]) <= 512, "control_text")
    return found[0]

LINE = re.compile(r"^\s*([0-9a-fA-F]+):\s+([A-Za-z.][A-Za-z0-9_.]*|<unknown>)(?:\s+(.*))?$")
REG = re.compile(r"^(?:[xw](?:[0-9]|[12][0-9]|30)|sp|wsp|xzr|wzr)$")

def rows_from_text(text, va, size):
    rows = []
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        pc = int(m[1], 16)
        need(va <= pc < va + size and (pc - va) % 4 == 0, "decoded_pc_range")
        args = (m[3] or "").split("//", 1)[0].strip()
        rows.append((pc, m[2].lower(), args))
    need([r[0] for r in rows] == list(range(va, va + size, 4)), "decoded_slot_coverage")
    return rows

def split_args(text):
    out, depth, start = [], 0, 0
    for i, char in enumerate(text):
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif char == "," and depth == 0:
            out.append(text[start:i].strip())
            start = i + 1
    out.append(text[start:].strip())
    need(depth == 0, "operand_brackets")
    return out

def number(text, address=False):
    text = text.split("<", 1)[0].strip().lstrip("#")
    need(re.fullmatch(r"-?(?:0x[0-9a-fA-F]+|[0-9a-fA-F]+)", text) is not None,
         "numeric_operand")
    return int(text, 16 if address or "0x" in text.lower() else 10)

def regkey(reg):
    need(REG.fullmatch(reg) is not None, "integer_register_required")
    if reg in ("sp", "wsp"):
        return "sp"
    if reg in ("xzr", "wzr"):
        return "zero"
    return "r" + reg[1:]

@dataclass(frozen=True)
class Expr:
    terms: tuple
    offset: int = 0
    pcs: tuple = ()

def combine(a, b, pc, factor=1):
    terms = dict(a.terms)
    for atom, n in b.terms:
        terms[atom] = terms.get(atom, 0) + factor * n
    terms = {k: v for k, v in terms.items() if v}
    pcs = tuple(sorted(set(a.pcs + b.pcs + (pc,))))
    need(len(terms) <= 2 and len(pcs) <= 16, "affine_bound")
    return Expr(tuple(sorted(terms.items())), a.offset + factor * b.offset, pcs)

def expression_public(expr, symbols, known_addresses):
    out = {"terms": [{"source": symbols[k], "scale": n} for k, n in expr.terms],
           "witness_pcs": list(expr.pcs), "arithmetic": "local_modulo_64_candidate"}
    if -65536 <= expr.offset <= 65536:
        out["displacement"] = expr.offset
    elif not expr.terms and expr.offset in known_addresses:
        out["known_address"] = expr.offset
        out["known_address_label"] = known_addresses[expr.offset]
    else:
        out["constant_omitted"] = True
    return out

def observe_rows(rows, va, size, refs, map_target):
    """Small, local whitelist. Unknown semantics terminate provenance, not imply success."""
    env, symbols, widths, generations, inputs = {}, {}, {}, {}, {}
    block = va
    facts, edges, frontiers, calls = [], [], [], []
    known_addresses = {**DATA_TARGETS, **refs}
    known_entries = {}
    for pc, op, operands in rows:
        if op in ("b", "bl", "cbz", "cbnz", "tbz", "tbnz") or op.startswith("b."):
            try:
                target = number(split_args(operands)[-1], address=True)
                if va <= target < va + size and (target - va) % 4 == 0:
                    known_entries.setdefault(target, []).append(pc)
            except (Reject, ValueError, IndexError):
                pass  # The main pass records the unmodeled edge.

    def reset(pc):
        nonlocal block
        env.clear()
        widths.clear()
        block = pc

    def atom(name, metadata, source=None):
        symbols[name] = {"source_id": name, **metadata}
        if source is not None:
            inputs[name] = source
        pcs = tuple(sorted(set((source.pcs if source is not None else ()) +
                               ((metadata["pc"],) if "pc" in metadata else ()))))
        need(len(pcs) <= 16, "atom_witness_bound")
        return Expr(((name, 1),), 0, pcs)

    def next_generation(key):
        generations[key] = generations.get(key, 0) + 1
        return generations[key]

    def get(reg):
        key = regkey(reg)
        if key == "zero":
            return Expr(())
        if key not in env:
            generation = next_generation(key)
            env[key] = atom("input_%x_%s_%d" % (block, key, generation),
                            {"kind": "stack_input" if key == "sp" else "unresolved_register_input",
                             "block_pc": block, "register": key,
                             "definition_generation": generation})
            widths[key] = 64
        value = env[key]
        if reg.startswith("w") and widths.get(key) != 32:
            # Non-affine truncation is unresolved; bind it to this register definition.
            return atom("low32_%x_%s_%d" % (block, key, generations[key]),
                        {"kind": "unresolved_low32_input", "block_pc": block,
                         "register": key, "logical_bits": 32,
                         "definition_generation": generations[key],
                         "input_source_ids": [a for a, _ in value.terms],
                         "input_witness_pcs": list(value.pcs)}, value)
        return value

    def put(reg, value, bits=64):
        key = regkey(reg)
        if key != "zero":
            next_generation(key)
            env[key], widths[key] = value, bits

    def public(value):
        return expression_public(value, symbols, known_addresses)

    def provenance_complete(value, seen=()):
        # This concerns the expression's observed origin, never a live pointer value.
        if not value.terms:
            return value.offset in known_addresses
        for name, _ in value.terms:
            if name in seen or len(seen) >= 16:
                return False
            meta = symbols[name]
            if meta["kind"] == "stack_input":
                continue
            if (meta["kind"] != "memory_read" or name not in inputs or
                    not provenance_complete(inputs[name], seen + (name,))):
                return False
        return True

    def ready_pointer(value):
        return bool(value.pcs) and provenance_complete(value) and all(
            symbols[name]["kind"] == "stack_input" or
            (symbols[name]["kind"] == "memory_read" and symbols[name]["bytes"] == 8 and
             not symbols[name]["signed"]) for name, _ in value.terms)

    def ref_arg(reg):
        value = get(reg)
        return refs.get(value.offset) if not value.terms else None

    def call_binding(pc):
        label, role, other = ref_arg("x1"), "property_call_candidate", ("x0", "x3")
        if label is None:
            label, role, other = ref_arg("x0"), "handle_call_candidate", ("x1",)
        valid_role = ((role == "handle_call_candidate" and label == "/core/mproc/smem")
                      or (role == "property_call_candidate" and label in
                          ("smem_vers", "smem_max_items", "smem_toc_vers", "smem_partitions")))
        index_value = get("w2")
        index_zero = role == "handle_call_candidate" or (not index_value.terms and index_value.offset == 0)
        prepared = bool(valid_role and all(ready_pointer(get(r)) for r in other))
        qualified = bool(prepared and index_zero)
        # No raw operand text, arbitrary constants or private register values.
        return {"name_reference": label, "role_hypothesis": role if valid_role else "unresolved",
                "argument_witness_complete": qualified,
                "argument_witness_pcs": sorted(set(p for r in other + (("x0",) if
                    role == "handle_call_candidate" else ("x1", "w2")) for p in get(r).pcs)),
                "other_argument_sources_known": prepared,
                "argument_pointer_validity_proven": False,
                "native_function_identity_proven": False}

    def address(text, pc):
        need(text.startswith("[") and text.endswith("]"), "memory_address_form_unmodeled")
        pieces = split_args(text[1:-1])
        value = get(pieces[0])
        if len(pieces) == 1:
            return value
        if pieces[1].startswith("#"):
            need(len(pieces) == 2, "memory_immediate_suffix")
            return combine(value, Expr((), number(pieces[1])), pc)
        need(len(pieces) in (2, 3), "memory_index_form")
        index = get(pieces[1])
        shift = 0
        if len(pieces) == 3:
            m = re.fullmatch(r"(lsl|uxtw)\s*#(\d+|0x[0-9a-f]+)", pieces[2])
            need(m is not None, "memory_extension_unmodeled")
            shift = number(m[2])
            need(0 <= shift <= 4 and (m[1] != "uxtw" or pieces[1].startswith("w")),
                 "memory_extension_width")
        return combine(value, index, pc, 1 << shift)

    for pc, op, operands in rows:
        if pc in known_entries:
            reset(pc)
            frontiers.append({"pc": pc, "kind": "known_control_target_entry",
                              "incoming_branch_pcs": sorted(known_entries[pc]),
                              "all_predecessors_known": False})
        try:
            args = split_args(operands)
            direct = op in ("bl", "b")
            conditional = op.startswith("b.") or op in ("cbz", "cbnz", "tbz", "tbnz")
            terminal = op in ("ret", "retaa", "retab", "br", "blr", "braa", "brab",
                              "blraa", "blrab", "eret", "drps", "svc", "hvc", "smc", "brk", "hlt")
            if direct or conditional or terminal:
                kind = "direct_call" if op == "bl" else "direct_branch" if op == "b" else (
                    "conditional_branch" if conditional else "indirect_or_terminal")
                edge = {"pc": pc, "kind": kind, "runtime_taken_proven": False}
                if direct or conditional:
                    target = number(args[-1], address=True)
                    edge.update(target_va=target, target_inside_window=va <= target < va + size)
                    try:
                        edge["target_mapping"] = map_target(target, 4)
                    except Reject:
                        edge["target_mapping_validated"] = False
                    if op == "bl":
                        binding = call_binding(pc)
                        calls.append({**edge, **binding,
                                      "eligible_for_follow": binding["argument_witness_complete"]
                                      and "target_mapping" in edge and target % 4 == 0})
                    if not edge["target_inside_window"]:
                        frontiers.append({"pc": pc, "kind": "control_target_outside_window",
                                          "target_va": target})
                    if conditional:
                        edge["fallthrough_va"] = pc + 4
                else:
                    frontiers.append({"pc": pc, "kind": "indirect_target_or_terminal"})
                edges.append(edge)
                reset(pc + 4)
                continue
            if op in ("adr", "adrp"):
                need(len(args) == 2, "adr_form")
                target = number(args[1], address=True)
                if op == "adrp":
                    need(target % 4096 == 0, "adrp_page")
                put(args[0], Expr((), target, (pc,)))
            elif op == "mov":
                need(len(args) == 2, "mov_form")
                value = (get(args[1]) if REG.fullmatch(args[1]) else Expr((), number(args[1])))
                if args[0].startswith("w") and not value.terms:
                    value = Expr((), value.offset & 0xffffffff, value.pcs)
                put(args[0], combine(value, Expr(()), pc), 32 if args[0].startswith("w") else 64)
            elif op in ("add", "sub"):
                need(len(args) in (3, 4) and not args[0].startswith("w"), "64bit_add_form")
                left = get(args[1])
                if args[2].startswith("#"):
                    factor = -1 if op == "sub" else 1
                    shift = 0
                    if len(args) == 4:
                        need(args[3] in ("lsl #12", "lsl #0xc"), "add_immediate_shift")
                        shift = 12
                    right = Expr((), number(args[2]) << shift)
                else:
                    need(op == "add", "subtract_register_unmodeled")
                    right, factor = get(args[2]), 1
                    if len(args) == 4:
                        m = re.fullmatch(r"(lsl|uxtw)\s*#(\d+|0x[0-9a-f]+)", args[3])
                        need(m is not None, "add_extension_unmodeled")
                        shift = number(m[2])
                        need(0 <= shift <= 4 and (m[1] != "uxtw" or args[2].startswith("w")),
                             "add_extension_width")
                        factor = 1 << shift
                value = combine(left, right, pc, factor)
                if regkey(args[0]) == regkey(args[1]) and args[2].startswith("#"):
                    facts.append({"pc": pc, "kind": "local_cursor_step",
                                  "step": factor * right.offset, "before": public(left),
                                  "source_or_destination_role": "unresolved"})
                put(args[0], value, 32 if args[0].startswith("w") else 64)
            elif op == "lsl":
                need(len(args) == 3 and args[0].startswith("x"), "64bit_shift_form")
                shift = number(args[2])
                need(0 <= shift <= 4, "shift_bound")
                put(args[0], combine(Expr(()), get(args[1]), pc, 1 << shift))
            elif op in ("ldr", "ldur", "str", "stur", "ldrb", "ldurb", "strb", "sturb",
                        "ldrh", "ldurh", "strh", "sturh", "ldrsw", "ldursw"):
                need(len(args) == 2 and REG.fullmatch(args[0]), "integer_memory_form")
                reg = args[0]
                need(reg not in ("sp", "wsp") and
                     (not op.endswith("sw") or reg.startswith("x")) and
                     (not op.endswith(("b", "h")) or reg.startswith("w")),
                     "memory_register_width")
                width = (1 if op.endswith("b") else 2 if op.endswith("h")
                         else 4 if op.endswith("sw") or reg.startswith("w") else 8)
                addr = address(args[1], pc)
                load = op.startswith("ld")
                row = {"pc": pc, "kind": "memory_read" if load else "memory_write",
                       "access_bytes": width, "address": public(addr),
                       "native_object_role": "unresolved"}
                if load:
                    name = "load_%x" % pc
                    row["read_id"] = name
                    # Preserve the address dependency graph separately from the loaded value.
                    # Public read_id/PC links resolve to the earlier memory fact's address.
                    put(reg, atom(name, {"kind": "memory_read", "pc": pc, "bytes": width,
                                        "signed": op.endswith("sw"),
                                        "address_source_ids": [a for a, _ in addr.terms],
                                        "address_witness_pcs": list(addr.pcs),
                                        "address_provenance_complete": provenance_complete(addr),
                                        "loaded_pointer_validity_proven": False}, addr),
                        32 if reg.startswith("w") else 64)
                facts.append(row)
            elif op in ("uxth", "uxtb", "ubfx", "lsr"):
                need(len(args) >= 2, "extract_form")
                source = get(args[1])
                start, bits = 0, 16 if op == "uxth" else 8
                if op == "ubfx":
                    need(len(args) == 4, "extract_form")
                    start, bits = number(args[2]), number(args[3])
                elif op == "lsr":
                    need(len(args) == 3, "extract_form")
                    start = number(args[2])
                    bits = (32 if args[1].startswith("w") else 64) - start
                source_bits = 32 if args[1].startswith("w") else 64
                need(0 <= start and 0 < bits and start + bits <= source_bits, "extract_width")
                facts.append({"pc": pc, "kind": "logical_bit_extract", "first_bit": start,
                              "logical_bits": bits, "source": public(source)})
                put(args[0], atom("extract_%x" % pc,
                                 {"kind": "logical_extract", "pc": pc,
                                  "first_bit": start, "bits": bits}),
                    32 if args[0].startswith("w") else 64)
            elif op in ("cmp", "cmn", "tst", "nop", "hint"):
                pass  # No general-purpose register result; no flags are evaluated.
            else:
                raise Reject("instruction_semantics_unmodeled")
        except (Reject, ValueError, IndexError):
            frontiers.append({"pc": pc, "kind": "instruction_semantics_unmodeled"})
            reset(pc + 4)
    return {"va": va, "bytes": size, "instruction_slots": len(rows),
            "all_slot_addresses_present": True, "facts": facts, "edges": edges,
            "frontiers": frontiers, "call_candidates": calls,
            "function_extent_proven": False, "entry_reachability_proven": False,
            "native_layout_selected": False, "native_configuration_abi_selected": False}

def follow_candidates(observations, visited):
    candidates = {}
    for obs in observations:
        for call in obs["call_candidates"]:
            if call["eligible_for_follow"] and call["target_va"] not in visited:
                candidates.setdefault(call["target_va"], call)
    return sorted(candidates.values(),
                  key=lambda c: (c["role_hypothesis"] != "property_call_candidate", c["pc"]))[:2]

def bounded_json(report):
    report["public_details_truncated"] = False
    report["details_omitted"] = 0
    while True:
        raw = (json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
               + "\n").encode("utf-8")
        if len(raw) <= MAX_OUTPUT:
            return raw
        candidates = [(obs, key) for obs in report.get("observations", [])
                      for key in ("facts", "edges", "frontiers", "call_candidates") if obs[key]]
        need(candidates, "metadata_output_limit")
        obs, key = max(candidates, key=lambda item: len(item[0][item[1]]))
        obs[key].pop()
        report["details_omitted"] += 1
        report["public_details_truncated"] = True
        report["complete_witnesses_exported"] = False

CONTROL_ASM = """.text
.p2align 2
.global _start
_start:
    ldr w2, [x3]
    ldr x1, [x0]
    ldr x4, [x1, x2, lsl #3]
    add x5, x1, x2, lsl #4
    ldr x6, [x5, #8]
    ldr w7, [x5]
    ldr w8, [x9, #8]
    uxth w10, w8
    lsr w11, w8, #16
    add x12, x12, #36
    add x13, x13, #32
    add x14, x14, #48
    mov x5, xzr
    ldr x6, [x5, #8]
    ret
"""

def child_limits(restore_mask):
    signal.pthread_sigmask(signal.SIG_SETMASK, restore_mask)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_TOOL_TEXT, MAX_TOOL_TEXT))

def termination_requested(signum, frame):
    raise Reject("observer_termination_requested")

def run_tool(argv, label, receipts):
    stdout, stderr = ROOT / "private" / (label + ".stdout"), ROOT / "private" / (label + ".stderr")
    out = os.open(str(stdout), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    err = os.open(str(stderr), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    proc = None
    timed_out = False
    record = {"label": label, "process_started": False, "cleanup_reaped": False}
    receipts.append(record)
    try:
        # Assignment and registration must complete before the parent handles TERM.
        old_mask = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM})
        try:
            proc = subprocess.Popen(argv, stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                                    start_new_session=True, close_fds=True,
                                    preexec_fn=lambda: child_limits(old_mask),
                                    env={"PATH": "/usr/bin:/bin", "LC_ALL": "C", "LANG": "C"})
            record.update(process_started=True, process_group=proc.pid)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, old_mask)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)
        os.fsync(out)
        os.fsync(err)
    finally:
        try:
            if proc is not None and proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.wait(timeout=2)
        finally:
            record["timeout"] = timed_out
            if proc is not None:
                record.update(exit_code=proc.returncode, cleanup_reaped=proc.poll() is not None)
                try:
                    os.killpg(proc.pid, 0)
                    record["group_present_after_cleanup"] = True
                except ProcessLookupError:
                    record["group_present_after_cleanup"] = False
            os.close(out)
            os.close(err)
    a, b = read_regular(stdout, MAX_TOOL_TEXT), read_regular(stderr, MAX_TOOL_TEXT)
    record.update(stdout_bytes=len(a), stderr_bytes=len(b),
                  stdout_sha256=digest(a), stderr_sha256=digest(b))
    need(not timed_out and proc.returncode == 0 and not b
         and record["cleanup_reaped"] and not record["group_present_after_cleanup"],
         "tool_failed")
    return a


def checked_tools(receipts):
    pin = TOOLCHAIN_PIN
    need(type(pin) is dict and pin.get("family") == "gnu", "toolchain_pin_unavailable")
    paths = {}
    for name in ("objdump", "as"):
        item = pin[name]
        need(type(item) is dict and set(item) == {"path", "realpath", "bytes", "sha256", "version_line"},
             "tool_pin_schema")
        need(Path(item["path"]).is_absolute() and
             os.path.realpath(item["path"]) == item["realpath"], "tool_realpath")
        raw = read_regular(Path(item["realpath"]), 64 * 1024 * 1024)
        need(len(raw) == item["bytes"] and digest(raw) == item["sha256"], "tool_binary_identity")
        line = run_tool([item["path"], "--version"], name + "-version", receipts).decode("utf-8").splitlines()[0]
        need(line == item["version_line"], "tool_version")
        paths[name] = item["path"]
    return paths

def disassemble(code, va, label, tools, receipts):
    path = ROOT / "private" / (label + ".elf")
    new_file(path, analysis_elf(code, va))
    output = run_tool([tools["objdump"], "-d", "--no-show-raw-insn",
                       "--disassemble-zeroes", str(path)], label, receipts)
    return rows_from_text(output.decode("utf-8", errors="strict"), va, len(code))

def controls(tools, receipts):
    source, obj = ROOT / "private" / "self-control.s", ROOT / "private" / "self-control.o"
    new_file(source, CONTROL_ASM.encode("ascii"))
    need(not os.path.lexists(obj), "control_object_exists")
    run_tool([tools["as"], "-march=armv8-a", "-o", str(obj), str(source)],
             "assemble-self-control", receipts)
    code = object_text(read_regular(obj, 16384))
    need(len(code) == 60, "self_control_size")
    rows = disassemble(code, 0, "self-load-control", tools, receipts)
    no_map = lambda target, size: {"va": target, "size": size, "synthetic": True}
    observed = observe_rows(rows, 0, len(code), {}, no_map)
    facts = {f["pc"]: f for f in observed["facts"]}
    plain, sized, clobbered = (facts[pc] for pc in (8, 16, 52))
    checks = {
        "plain_pointer_scale8": plain["access_bytes"] == 8 and
            sorted(t["scale"] for t in plain["address"]["terms"]) == [1, 8],
        "sized_pointer_scale16_plus8": sized["access_bytes"] == 8 and
            sorted(t["scale"] for t in sized["address"]["terms"]) == [1, 16] and
            sized["address"]["displacement"] == 8,
        "size_field_word_read": facts[20]["access_bytes"] == 4,
        "word_can_extract_two_halfwords": facts[24]["access_bytes"] == 4 and
            facts[28]["logical_bits"] == 16 and facts[32]["logical_bits"] == 16 and
            facts[32]["first_bit"] == 16,
        "distinct_cursor_steps": [facts[p]["step"] for p in (36, 40, 44)] == [36, 32, 48],
        "clobbered_address_not_reused": not clobbered["address"]["terms"],
        "no_unmodeled_self_instruction": not any(f["kind"] == "instruction_semantics_unmodeled"
                                                for f in observed["frontiers"]),
    }
    branch_code = struct.pack("<III", 0x94000002, 0xd65f03c0, 0xd65f03c0)
    branch_rows = disassemble(branch_code, 0x12345000, "self-branch-control", tools, receipts)
    branch = observe_rows(branch_rows, 0x12345000, 12, {}, no_map)
    checks["direct_call_target"] = (branch["edges"][0]["pc"] == 0x12345000 and
                                    branch["edges"][0]["target_va"] == 0x12345008)
    need(all(checks.values()), "self_controls_failed")
    return {"passed": True, "checks": checks, "self_authored": True,
            "firmware_executed": False, "all_instruction_forms_covered": False}

def observe_native(data, tools, receipts):
    view = ELF(data)
    original = [p for p in view.loads if p["index"] == 2]
    need(len(original) == 1 and original[0] ==
         {"index": 2, "flags": 5, "off": 12288, "va": 0x14815000,
          "filesz": 388492, "memsz": original[0]["memsz"]}, "original_code_load")
    for addr, name in REFS.items():
        expected = name.encode("ascii") + b"\0"
        m = view.mapping(addr, len(expected))
        need(not m["flags"] & 1 and
             data[m["file_offset"]:m["file_offset"] + len(expected)] == expected,
             "allowlisted_reference_identity")
    observations = []
    def target_mapping(va, size):
        need(va % 4 == 0, "target_alignment")
        return view.mapping(va, size, True)
    def one(va, size, label, provenance):
        code, mapping = view.code(va, size)
        rows = disassemble(code, va, label, tools, receipts)
        out = observe_rows(rows, va, size, REFS, target_mapping)
        out.update(source_mapping=mapping, slice_sha256=digest(code),
                   provenance=provenance, fact_count=len(out["facts"]),
                   edge_count=len(out["edges"]), frontier_count=len(out["frontiers"]),
                   call_candidate_count=len(out["call_candidates"]))
        observations.append(out)
        return out
    for i, (va, size, expected_off) in enumerate(SEEDS):
        need(view.mapping(va, size, True)["file_offset"] == expected_off, "seed_mapping")
        one(va, size, "seed-%d" % i, {"kind": "fixed_reference_neighborhood"})
    chosen = follow_candidates(observations, {va for va, _, _ in SEEDS})
    follow_receipts = []
    for i, call in enumerate(chosen):
        try:
            one(call["target_va"], 512, "callee-%d" % i,
                {"kind": "observed_qualified_direct_call", "source_pc": call["pc"],
                 "name_reference": call["name_reference"]})
            follow_receipts.append({"source_pc": call["pc"], "target_va": call["target_va"],
                                    "bytes_observed": 512, "observed": True})
        except Reject as error:
            follow_receipts.append({"source_pc": call["pc"], "target_va": call["target_va"],
                                    "observed": False, "reason": str(error)})
    return observations, follow_receipts

def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    report = {"schema": "tb371fc-xbl-accessor-observation-v1", "analysis_completed": False,
              "firmware_input_read": False, "firmware_input_read_attempted": False,
              "firmware_executed": False,
              "native_layout_selected": False, "native_configuration_abi_selected": False,
              "platform_selected": False, "entry_reachability_proven": False,
              "smem_initialized_proven": False, "observations": [],
              "complete_witnesses_exported": False, "tool_receipts": [],
              "data_target_source": {"git_blob": "19ff1fba410a363801b00c97b96df1981c9da21d",
                  "sha256": "839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053",
                  "native_layout_selected": False}}
    created = False
    try:
        need(argv == ["--observe-fixed"], "explicit_observation_flag_required")
        need(TOOLCHAIN_PIN is not None, "toolchain_pin_unavailable")
        need(sys.flags.optimize == 0 and sys.flags.isolated and sys.flags.no_site
             and sys.dont_write_bytecode, "isolated_nonoptimised_runtime_required")
        signal.signal(signal.SIGTERM, termination_requested)
        os.umask(0o077)
        new_file(ROOT / "observer.started.json", b'{"started":true,"completed":false}\n')
        created = True
        os.mkdir(ROOT / "private", 0o700)
        source = read_regular(Path(__file__), 131072)
        report["method_source"] = {"bytes": len(source), "sha256": digest(source)}
        tools = checked_tools(report["tool_receipts"])
        report["controls"] = controls(tools, report["tool_receipts"])
        need(stat.S_IMODE(os.stat(INPUT, follow_symlinks=False).st_mode) == 0o600,
             "input_private_mode")
        report["firmware_input_read_attempted"] = True
        data = read_regular(INPUT, INPUT_SIZE)
        report["firmware_input_read"] = True
        need(len(data) == INPUT_SIZE and digest(data) == INPUT_SHA, "input_identity")
        report["input"] = {"basename": INPUT.name, "bytes": len(data), "sha256": digest(data)}
        report["observations"], report["callee_follow_receipts"] = observe_native(
            data, tools, report["tool_receipts"])
        after = read_regular(INPUT, INPUT_SIZE)
        need(digest(after) == INPUT_SHA, "input_changed_after_observation")
        report.update(analysis_completed=True, complete_witnesses_exported=True,
                      input_unchanged=True, total_bytes_observed=sum(o["bytes"] for o in report["observations"]),
                      scope="Linear static candidates only. No complete function or runtime path is established.")
        code = 0
    except Exception as error:
        report["error_code"] = str(error) if isinstance(error, Reject) else "observer_io_or_decode_error"
        report["error_type"] = type(error).__name__
        code = 2
    raw = bounded_json(report)
    if created:
        new_file(ROOT / "observations.json", raw)
        new_file(ROOT / "observer.exit", (str(code) + "\n").encode("ascii"))
    sys.stdout.buffer.write(raw)
    return code

if __name__ == "__main__":
    sys.exit(main())
