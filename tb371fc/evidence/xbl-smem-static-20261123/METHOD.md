# XBL static evidence: collection and reproduction

This checkpoint contains bounded static metadata from the tablet's existing
active-a firmware backup. It establishes file integrity, selected format fields,
string/address candidates, and a small amount of numeric control-flow metadata.
The SMEM initializer's selected values, actual execution path, and resulting
shared-memory contents remain unproved. No SMEM handoff was generated.

## Inputs and read scope

The source was the already-created private archive:

`/data/data/com.termux/files/home/qemu-tb371fc-probe/private-xbl-20261123-01/active-a.tar.gz`

Its exact size is 2,849,209 bytes; SHA-256 is
`30a30d8670bd69579d2a250ddcc542b99f3140d062a8b95cb61c09bfd8f1671e`.
It contains exactly `xbl_a.bin`, `xbl_config_a.bin`, and `capture.json`.
The archive validator checks the whole hash, three regular USTAR members,
member sizes and hashes, manifest fields, padding, and bounds. Links, paths
outside the exact member allowlist, extended headers, and duplicate JSON keys
are rejected.

Each tablet wrapper opened only this existing archive with
`O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NOATIME` and checked file stability.
It did not read block devices, live RAM, identities, or new device settings.
Archive contents stayed in process memory. No tablet file was written and no
firmware or emulator was executed. This is a current active-partition backup;
its provenance does not establish pristine factory contents.

## Wrappers and actual checks

The archive labels below describe **wrapper bodies requiring source injection**.
They are not standalone command-line programs.

| Body | Actual scope | Control and output limits |
|---|---|---|
| metadata-wrapper.py | ELF fields, bounded FV header candidates, allowed SMEM markers and literal-map candidates in both firmware members | Reuses the original parser's pure functions. Output: 9,693 bytes, including LF. |
| pointer-wrapper.py (A) | Sixteen known string locations; LE32/LE64 absolute-pointer scan of non-executable LOAD4/5/11/14, totaling 171,946 bytes | Same-slot 32/64 matches merge. Two distinct pointer slots and eight mapped neighbouring address candidates were reported without truncation. |
| control-wrapper.py (B) | File offset 24,336, size 4,096; VA 0x14817f10 | Self-authored BL +8 / RET / RET fixture passed before private decoding. Of 144 detected control-flow candidates, 31 were reported; 113 were omitted. All five address-candidate details were omitted by the output budget, with counts retained. |
| followed-callees-wrapper-v2.py (C) | File offset 38,052, size 2,048; VA 0x1481b4a4. File offset 59,112, size 2,048; VA 0x148206e8 | The original three-instruction control and a new ten-instruction control passed. Eight new assertions cover exact ADR, adjacent ADRP+ADD, B.EQ/CBNZ/TBZ/BL/RET targets, and rejection of a mismatched producer register. All 49 address candidates were reported. Control-flow details were 5/121 and 6/125; omitted counts remain explicit. |

All safe JSON outputs are capped at 12 KiB including LF. B/C use the installed
LLVM 20.1.8 `llvm-objdump`, stdin and pipes, a five-second limit and a combined
256 KiB stdout/stderr budget per subprocess. The wrapper's overall analysis
budget is 17 seconds; the tablet tool request is capped at 20 seconds.

The original XBL ELF has no section table. B/C create a derived analysis ELF
entirely in memory, with one synthetic executable section at the original
slice's VA. Original slice bytes are unchanged and hashed. This synthetic
header is explicitly distinguished from the original ELF header. The object is
sent only to the disassembler. LLVM's stdin and section-disassembly behaviour
is documented in its [command guide](https://llvm.org/docs/CommandGuide/llvm-objdump.html).

RX windows can contain literal pools. Linear decoding and numeric branch
candidates do not prove code execution, complete function boundaries, or
reachability from the real firmware entry. Both conditional successors remain
possible. The offline CFG receipt stops at the exported control-flow prefix;
it does not invent fall-through edges for omitted output.

The wrappers had not received separate testing before their first collection
runs. The 12 earlier CT unit tests apply to the original metadata parser only.
B's three-instruction control covers its stated BL/RET format check. C's new
controls use the same numeric extractor as the private windows, but still do
not cover every A64 form. These checks are not QEMU hardware-equivalence gates.

## Source injection and actual invocation

Each body requires three globals:

- `PARSER_SOURCE`: exact text of the frozen `xbl_metadata.py`.
- `ARCHIVE_VALIDATOR_SOURCE`: exact text of `archive_validation.py`.
- `WRAPPER_SOURCE_SHA256`: SHA-256 of the exact selected wrapper body.

The bodies verify the parser and archive-validator hashes before compiling
their definitions into isolated namespaces. They invoke pure parsing/validation
functions; they do not call the original parser's `main()` or file-writing
`analyze()` path. The original parser also has a separate CT CLI that writes
analysis results; that CLI was not used by these tablet wrappers.

The actual tablet process form was:

`su -c '<Python executable> -I -B -S -c <plain, shell-quoted assembled source>'`

The Python executable was
`/data/data/com.termux/files/usr/bin/python`.
Source was supplied as directly reviewable text, without a base64/compressed
executable payload. A collector failure or rejected/confirmation-required tool
request ends that attempt.

For example, the following **preparation-only** code assembles the C invocation
from the archived text files. Verify all files against the archive manifest
first. This snippet prints a command; it does not execute it or read firmware.

```python
import hashlib
import json
from pathlib import Path
import shlex

parser = Path("xbl_metadata.py").read_text(encoding="utf-8")
validator = Path("archive_validation.py").read_text(encoding="utf-8")
wrapper = Path("followed-callees-wrapper-v2.py").read_text(encoding="utf-8")
wrapper_sha = hashlib.sha256(wrapper.encode("utf-8")).hexdigest()
assert wrapper_sha == "621e6b1615d098ceebd115f9000f4b4dfac94cdbbc8b809f32ec121a0fe9e6fd"
source = (
    "PARSER_SOURCE = " + json.dumps(parser) + "\n"
    "ARCHIVE_VALIDATOR_SOURCE = " + json.dumps(validator) + "\n"
    "WRAPPER_SOURCE_SHA256 = " + json.dumps(wrapper_sha) + "\n"
    + wrapper
)
argv = [
    "/data/data/com.termux/files/usr/bin/python",
    "-I", "-B", "-S", "-c", source,
]
print("su -c " + shlex.quote(shlex.join(argv)))
```

## Integrity anchors

All listed objects were stored as Git data blobs in
`cyjin-yl/qemu-tb371fc` and fetched back byte-for-byte before reporting.
Blob creation alone changed no tree, commit or branch reference.

| Evidence | Git blob |
|---|---|
| Initial metadata JSON | `34609b527f52a24d6df85d4f8f87b638d076a9f9` |
| A pointer JSON | `46d799a79a485ad477083eedc093390a60fa7a02` |
| B control JSON | `a101d5a6fd51d6eee3b633c54c616634d7a028b4` |
| C followed-callees JSON | `7afdec014f33400f90746add5a0b9f6cf81510e1` |
| Offline CFG/property inference JSON | `7d45529079b9593dca73ad0a23a36b8cb8925f53` |
| Actual C wrapper body | `922b13ec4199bf147c8a1bd132d972abacb9fe5f` |

The separately prepared transfer completion v2 is historical and must not be
executed: it would unlink a newly created stage file, contrary to this task's
file-retention rule. Completion v3 retains stages and records their paths,
while preserving the archive and refusing conflicting existing outputs.
Neither the script's existence nor these static receipts prove that the
separate CT transfer/extraction has completed; that requires its own receipt.
