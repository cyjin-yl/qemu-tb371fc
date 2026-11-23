# Fixed XBL accessor observer — implementation draft

This is an unexecuted method draft. Neither its 18 fixture methods nor its
tool-based controls have been run. TOOLCHAIN_PIN now contains the actual CT
GNU objdump/as metadata obtained by the qemu agent without firmware reads.
Root must review this fixed source before scheduling native observation.

## Files and execution scope

The fixed new CT directory is /work/xbl-dal-accessor-observe-20261123-01.
The observer filename is observe_xbl_accessor.py; the fixtures load that
fixed sibling using importlib, including sys.modules registration, for
python -I -B -S. The fixture file has exactly 18 unittest methods and
uses only self-authored byte/text inputs. No fixture reads firmware.

The only native input is the existing CT xbl_a.bin at
/work/device-assets/xbl-capture-20261123-01/xbl_a.bin:
3670016 bytes, SHA-256
eaf27cf49114d144282e86880c017fbadb3442df97bd30449e386f77e804da55.
The whole pinned XBL file is read for identity hashing and retained only in
private process memory. No archive, tablet or block device is accessed, and
no embedded firmware volume is decoded or followed. Instruction observation
is limited to the fixed seed slices and qualified bounded callee slices.

The observer first requires --observe-fixed, an explicit toolchain pin,
and isolated/unoptimized/no-site/no-bytecode Python. It creates only new
observer.started.json, private/, observations.json and observer.exit under
its installed directory; private outputs are mode 0600. It retains errors
and partial files. Existing output causes refusal rather than overwrite.

## Actual code-reading budget

Phase A consists only of:

- VA 0x1481b8e8, 168 bytes, file offset 0x98e8.
- VA 0x1481ba40, 96 bytes, file offset 0x9a40.

These are 264 bytes / 66 A64 slots in the original executable LOAD 2.
The ELF class, machine, ranges, original LOAD geometry and exact-NUL
allowlisted strings are checked against the fixed input.

The small whitelist records direct and conditional control edges, integer
memory access widths, simple local affine address relationships, logical
bit extractions and cursor increments. An unsupported instruction or
addressing form invalidates local provenance and adds a frontier. A branch
or call also ends local provenance. Before the local pass, every decoded
in-window direct/conditional control target is marked as an entry barrier.
The environment is cleared at that PC, including backward targets, because
the textually previous instruction is not established as its only predecessor. This deliberately avoids a whole
function/CFG reconstruction and does not infer across missing code.

Only actual Phase A BL targets with a complete local name/argument witness
are eligible for automatic follow. A property-call candidate needs the
allowlisted name in x1, zero ID in w2, and observed pointer preparation for
x0 and x3. A handle-call candidate needs /core/mproc/smem in x0 and observed
output-pointer preparation in x1. A memory-read value is eligible only when
its address expression recursively has an observed origin: the local stack
input, an allowlisted fixed address, or a previously observed read whose own
address meets that rule. Unknown register inputs, unresolved truncations,
unsupported operations and a bounded dependency chain that cannot be resolved
remain incomplete. Pointer-shaped arguments must use full unsigned 64-bit reads.
Expression provenance never proves that a loaded value is a valid pointer;
argument_pointer_validity_proven remains false. These are conditional API-role
hypotheses, not proof of native function names or runtime pointer validity.

Eligible direct targets are deduplicated, property-call candidates take
priority, and at most two 512-byte uniquely mapped executable windows are
observed. This implementation follows one direct-call level only. Direct
tail branches, indirect calls, missing arguments and further callee calls
remain explicit frontiers; no second-level expansion is automatic. This
is narrower than the earlier plan's maximum depth-two allowance.

## Controls before private code

The actual installed GNU assembler and objdump must each match their
reviewed path, realpath, size, SHA-256 and version first line. There is no
Termux fallback and no library/tool installation.

Before reading XBL, the assembler compiles a small self-authored A64
memory/address fixture. Its object must have a bounded .text and no
unresolved relocation. The same objdump path and parser used for native
windows then verify:

- A 64-bit pointer read with index scale 8.
- A 64-bit pointer read with index scale 16 and displacement 8.
- A distinct 32-bit size-field read.
- One 32-bit load followed by two logical 16-bit extractions.
- Distinct unresolved cursor increments 36, 32 and 48.
- A clobbered address register does not retain its old affine relation.
- No unsupported instruction in this self-authored control.
- A separate self-authored three-word BL/RET/RET control has the exact
  expected direct-call target.

All eight checks must pass before any firmware bytes are read. They do
not claim coverage of every A64 form. The standalone 18 fixtures add
negative cases for wrong registers/argument roles, clobbering, branch
boundaries, ELF/BSS/overlap errors, follow deduplication/bounds, output
redaction/truncation and absent-tool-pin refusal.

Each external tool gets a new process group, a 5-second deadline, TERM
with a 1-second grace, then KILL/reap. Core dumps are disabled and each
private tool output is limited to 262144 bytes. All tool outputs remain
private CT files; only their identities/counts are public.

## Evidence boundary

The data target labels are bound to the existing DAL03 receipt:
Git blob 19ff1fba410a363801b00c97b96df1981c9da21d,
SHA-256 839510ad39da6af7f14aacd682dedca7051f87edc86bc14756eb2c66da354053.
That receipt supplies index58 and the two unselected structure layouts.
The observer does not read or decode the pointed-to partition table.

No successful mapping, dwSize=8, scale occurrence or plausible contents
selects an ABI. The exported affine relationships are local observations
with object roles unresolved. The source of the index/base and later
configuration cursor still require interpretation against complete
instruction witnesses. In particular a 32-bit load may contain two
16-bit fields, and a 48-byte TOC destination is not a configuration source.

The public JSON is capped at 12288 bytes including newline. It contains
hashes, fixed ranges, call/branch PCs and target mappings, access widths,
logical bit widths, local affine relationships, witness PCs, frontiers,
totals and omission counts. No private opcode, mnemonic, operand text,
nonallowlisted string, disassembly or table content is exported.
Truncation clears complete_witnesses_exported and records omissions.
All native-layout/configuration-selection, runtime reachability, platform
selection and SMEM-initialization flags remain false.

The primary source contract and earlier numeric metadata are recorded in
plan blob57a975ea739153fdf214d1d866bf6067c709fc89. This method does not turn
the third-party SDM670 mirror into verified native SM8250 source.

## Current handoff state

Source and fixtures are for root review and a later coordinated CT
compile/fixture/control/native observation run. A source blob is not
evidence that Python compiled or any check passed. The GNU toolchain pin
is now fixed to the observed GNU Binutils for Debian 2.44 binaries. No native
observation has been executed, and root review/scheduling remains pending.

## Actual toolchain pin

The qemu agent recorded these installed executables with stable pre/post
size and mtime in the CT metadata batch aop-followup-tar-tools-metadata-ct05-01.
No tool was installed for this task.

- /usr/bin/aarch64-linux-gnu-objdump; realpath identical; 465800 B;
  SHA-256 b9ae08c9c02c91ced02b80a140c937ba119fe073872fff701f67477bf597a5eb;
  version first line: GNU objdump (GNU Binutils for Debian) 2.44.
- /usr/bin/aarch64-linux-gnu-as; realpath identical; 1146008 B;
  SHA-256 4e623869baaf4e0e165573b095f0aedaf47fe26f000ea8f3288e1c1a582cc644;
  version first line: GNU assembler (GNU Binutils for Debian) 2.44.

The actual toolchain pin and read-attempt receipt fields were fixed after the
initial submission. A subsequent independent text review identified and fixed
the three provenance issues documented below. The original 14 fixture method
bodies remain byte-for-byte unchanged; four meaningful methods were added.
None of the 18 fixtures or eight tool-based controls has been executed. firmware_input_read_attempted is set before the input read.
firmware_input_read is set once that bounded read completes, before the hash
check, so a completed read with a mismatched hash cannot be reported as no
input read. A failed or partial read retains the explicit attempted flag.


## Provenance review correction (unexecuted)

A 64-bit register truncated through wN is not approximated as a 64-bit affine
value. Its unresolved low-32 atom now carries the current register definition
generation, source IDs and witness PCs. Repeated reads of an unchanged
definition remain the same atom; a later definition cannot collide with it,
including in the exported metadata.

Each memory-read atom retains its immutable address-expression dependencies
inside the observer. The public read_id/source_id and PC links refer to the
corresponding memory fact's full address expression. Those links, the address
source IDs/PCs and address_provenance_complete expose the observed chain
without exporting private loaded values or instruction text. Recursive
completeness is used for follow eligibility; an unknown address cannot become
known merely because a load occurred. Source-expression completeness and
loaded-pointer validity are distinct; all pointer-validity flags remain false.

The known-control-target entry barrier handles both forward and backward
targets found in the bounded window. It does not claim to discover incoming
edges outside that window, resolve indirect branches, prove reachability or
enumerate all predecessors. A call can qualify only if its local argument
witness is re-established after a barrier.

Four added fixture methods cover: stable versus redefined low-32 sources;
two-level memory-address provenance with known stack and unknown-register
origins; forward/backward joins that invalidate textual predecessor values;
and a complete argument witness rebuilt after a join. These are authored
controls pending actual CT execution. No native range, callee count, following
depth, table read or runtime-selection policy was expanded.
