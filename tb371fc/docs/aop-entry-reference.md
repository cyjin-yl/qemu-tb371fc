# Bounded QEMU AOP entry reference

This is an independent diagnostic reference. Its public workflow reads no
private firmware. Fixed source commit `fe89e5a553fe038babe07abb279a0975d4306715`
passed all eleven XML regressions and fourteen actual synthetic preflight checks
in [CI run 45183148964](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45183148964).
The tested binary SHA256 is
`3bd0d1d124cc9c3f3cf010bddf2b634a6e7e8f9d5a464c29c895005e04e64bc0`.
[Independent CT attempt 04](../evidence/aop-reference-ci-20261123/ct-attempt-04/README.md) failed during Git source fetch before compilation or tests; that failure is preserved. [CT attempt 05](../evidence/aop-reference-ci-20261123/ct-attempt-05/README.md) reconstructed the exact same commit through verified public Git objects and actually passed all 11 XML and 14 CPU checks. Its separately built QEMU SHA256 is `c6bed2c465e0b771d6e40d60225b9f89636f6d213bf78b3c174510c24f3f4493`.

| Preserved attempt | Actual result |
| --- | --- |
| [attempt-01](../evidence/aop-reference-ci-20261123/attempt-01/manifest.json) | Build completed; missing ARM target interface prevented machine selection. No synthetic guest instruction ran. |
| [attempt-02](../evidence/aop-reference-ci-20261123/attempt-02/manifest.json) | CI run 45175027472 built and registered the machine. Unbound `xi:` in QEMU's `target.xml` then failed ElementTree parsing before guest execution. The separate CT1230 build reproduced that parser failure. |
| [CT attempt-03](../evidence/aop-reference-ci-20261123/ct-attempt-03/manifest.json) | Same frozen CT binary; all 11 XML regressions passed. The first two CPU checks passed, the third failed, and the remaining eleven were not reached. The old runner confused the RSP range base with the actual overlap address. |

The original successful CI receipts are archived in
[CI attempt-04](../evidence/aop-reference-ci-20261123/ci-attempt-04/manifest.json).
They record the raw RSP base `0x0b0e604c` separately from the actual unmapped
read overlap `0x40000000`. The STM boundary case records four earlier legal
bytes while retaining the forbidden-tail and unrelated-memory requirements.
No private firmware was read or executed.

The XML adapter only normalizes the exact source-derived QEMU target envelope.
Malformed XML, unknown prefixes, other annexes, duplicate attributes and recursive
includes remain rejected. The new read-only hit snapshot corrects address
observation without relaxing the precise address, PC, RAM, padding or unsupported
bus-callback requirements. Neither the earlier 11 XML passes nor two CPU passes
constitutes fourteen-check acceptance.

The selected CPU is upstream QEMU Cortex-M3. The captured AOP image establishes
ELF32 little endian, EM_ARM=40 and Thumb entry candidates; it does not identify
the actual AOP core, its cold reset mapping, its clocks, or its peripheral state.
The diagnostic must retain `hardware_reset_verified=false` and
`aop_startup_validated=false`, even after a bounded run produces a useful first
unsupported-access address.

## Why the previous engine was stopped

The preserved Unicorn 2.1.4 preflight failed its mapped-tail write check. Calling
`emu_stop()` in a valid-memory write hook still changed the destination from
`44332211` to `88776655`. The prior unmapped read, SCS read and mapped-tail
read checks passed; the final shared-alias check was not reached. No firmware
was read or executed by that preflight. The failure concerns the instrumentation
method, not AOP hardware.

The original harness, corrected Cortex-M3 constructor version, preflight
failure and CPU-model API probe remain under
`/work/aop-reset-reference-20261123`. They must not be overwritten or relabeled
as successful.

## Exact input-derived memory geometry

The private source image is 524288 bytes with SHA256
`3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671`.
Only the five PT_LOAD entries are used. Each has alignment four and equal
file/memory sizes. Non-LOAD headers and their separate memory-size fields do not
allocate guest RAM.

| Segment | File offset | Physical/virtual address | File and memory bytes | ELF flags |
| --- | ---: | ---: | ---: | --- |
| 2 | 0x3000 | 0x0b000000 | 0x14f08 | R-X |
| 3 | 0x17f10 | 0x0b0e0000 | 0x604c | RW- |
| 4 | 0x1df60 | 0x80800000 | 0xdfc8 | RW- |
| 5 | 0x2bf28 | 0x80810000 | 0x1384 | RW- |
| 6 | 0x2d2ac | 0x80818000 | 0x47b0 | RWX |

These are loader-derived reference ranges, not measured physical bank sizes.
The candidate stack value 0xe8000 is outside every writable ELF LOAD range.

The ELF entry is 0x0b000009. The first two words of the executable LOAD yield
candidate MSP=0xe8000 and candidate reset PC=9. The words are not proof of a
measured reset ABI.

## Three independent profiles

- `elf-physical-entry`: map only the five physical ranges and explicitly enter
  the ELF entry with the candidate stack value.
- `local-alias-vector`: additionally map code at zero and data at 0xe0000 using
  aliases of the exact same QEMU RAM backings; explicitly enter candidate PC9.
- `local-alias-dtcm32k-reference`: the second profile plus an explicit data
  region extension from 0x604c to 0x8000, adding 8116 unmeasured bytes. Only this
  profile makes the word immediately below candidate MSP writable.

The machine's initial reset reads occur before the RSP controller loads image
bytes. Initial vector placement and zeroed RAM are reference construction
choices. The runner records the actual QEMU initial register state separately
from the later entry/SP overrides. It does not present the overrides as a
native reset observation.

One Cortex-M3, a fixed 1 MHz required CPU clock, and 32 unconnected NVIC inputs
are reference configuration. They are not AOP clock, IRQ-count, or CPU identity
measurements. Bit-banding is disabled. No remote processor, device response,
heap hook, function replacement, interrupt injection, or peer acknowledgement
is present.

## Strict accesses

Every RAM block has page-rounded host backing, but only an exact-size alias is
mapped into guest address space. Hidden padding is filled with 0xa5 and exposed
only to a read-only host QOM integrity check. It is not additional guest RAM.

The entire unused 32-bit space returns MEMTX_ERROR. A high-priority guard also
covers 0xe0000000..0xe00fffff in the ARMv7M container. This matters because normal
upstream ARMv7M supplies SCS/SysTick devices and privileged PPB RAZ/WI defaults.
The guard must not allow these unknown AOP accesses to succeed implicitly.

The RSP controller installs hardware watchpoints over every hole and write
watchpoints over ELF read-only regions. This QEMU revision sets ARM's
`gdb_stop_before_watchpoint=true`; TCG converts the requests to
`BP_STOP_BEFORE_ACCESS`. Its check restores vCPU state, sets EXCP_DEBUG, and
exits the CPU loop before the offending access is dispatched. The runner never
resumes a watchpoint-faulted instruction.

This is not instruction-transaction rollback. STRD/STM or split unaligned
stores may have earlier legal bus beats. Tests require forbidden target
padding and all unrelated RAM to remain unchanged, require no unsupported
bus callback to run, and preserve any earlier valid writes in the receipt.
They also require the precise expected first forbidden overlap byte, so a different
exception-stack access cannot accidentally satisfy the test.

GDB's RSP stop packet reports the configured watch range base (`vaddr`),
not the access hit. The read-only `watchpoint-hits` QOM property is available
only with BQL held and the vCPU stopped. It preserves raw flags and
macro-decoded state. The runner requires an empty initial snapshot, uniquely
matches this packet's range/type and the installed length, checks
before-access/read/write state, and compares `hitaddr` with
`target + allowed_prefix`. The value is the first overlap byte,
`MAX(access_address, watch_range_base)`, not an architectural FAR or an
access-width report. Evidence is taken before continuing execution; the
faulted instruction is never resumed. No CPU watchpoint flags, registers,
memory or device state are changed by this observer.

Before each single-step request the controller checks the PC against executable
file ranges and checks the whole two- or four-byte Thumb instruction extent.
The T16/T32 length split is the same top-five-bit rule used by this revision's
Thumb translator. Known blocking/trap encodings stop the reference diagnostic;
all other instruction semantics are executed by QEMU. No Capstone dependency
or replacement instruction interpreter is used.

## Internal exception accesses have a separate boundary

In the fixed base revision, `arm_v7m_load_vector()` calls
`address_space_ldl()` directly, and `v7m_stack_write()/v7m_stack_read()`
perform direct physical accesses after address translation.
These paths do not call `cpu_check_watchpoint()`.
The fourteen ordinary TCG access checks therefore do not prove that internal
exception vector or stack accesses stop before bus dispatch. Earlier legal
stack writes can remain visible. A failed vector read is escalated rather than
using the guard's zero value as a successful response.

The existing private runner stops after observing a nonzero IPSR and rejects
changed unsupported-callback counters or hidden padding. Its
`architectural-exception-after-step` outcome is a bounded exception
observation, with no internal-exception before-access guarantee. This public
CI does not call the private command. Before a later private diagnostic, its
machine-readable verdict must explicitly retain that limitation; a passing
synthetic run does not remove it.

Source: [fixed m_helper.c][m-helper], SHA-256
`8238f84c72ddd082387603d6b11e03133d734f984702d0bfbee072a7e7b51925`.

[m-helper]: https://github.com/cyjin-yl/qemu-tb371fc/blob/3b938b23f4ce3e9cde88f31ae39d6280524624e0/target/arm/tcg/m_helper.c

## Process and directory retention

Before each QEMU process is started, its result directory receives
`launch.json` with the exact command, the actual socket-directory path and
`retained=true`. The harness does not delete its temporary socket directories.
QEMU logs, launch records, partial results and failed attempts remain available
for review.

Normal instance teardown requests QMP quit before closing RSP, waits up to one
second, then kills and reaps that QEMU process if it has not exited. This avoids
resuming a stopped guest merely by disconnecting the debugger. Public CI must
also bound the entire synthetic preflight to 180 seconds in its own process
group. The separate XML regression step has a 30-second CI bound; the private
command's separate ten-second profile bound does not apply to preflight.

## Public preflight first

The preflight has 14 top-level checks:

1. Actual Cortex-M3 QOM type and an authored MRS MSP instruction.
2. Permitted guest STR/LDR.
3. Unmapped read stopped before a value is supplied.
4. Unmapped write stopped before bus dispatch.
5. PPB read and write, including SCS and SysTick locations.
6. A read just beyond an exact range inside its host backing page.
7. A write to that same logical tail.
8. An unaligned STR spanning an exact range boundary.
9. STRD across a boundary, with an independent permitted two-word control.
10. STM across a boundary, with an independent permitted two-word/writeback
    control and recorded earlier legal beats.
11. ELF read-only code write.
12. Guest writes through both real aliases and execution from the code alias.
13. A T32 instruction crossing the executable file end and a non-executable PC.
14. Extra DTCM exists only in the explicit reference profile.

The opcodes used by preflight are self-authored; they are not copied from the
private firmware. Exact count, boolean verdict, runner hash and QEMU binary hash
must all match before the private command accepts an image path.

## Bounded private execution

Use a fresh output directory for every attempt:

```sh
python3 tb371fc/tests/test_aop_entry_reference.py preflight \
  --qemu /work/build-aop-reference-20261123/qemu-system-aarch64 \
  --out /work/aop-qemu-preflight-01

# Run only after reviewing the real 14-check preflight result.
python3 tb371fc/tests/test_aop_entry_reference.py diagnose-private \
  --qemu /work/build-aop-reference-20261123/qemu-system-aarch64 \
  --preflight /work/aop-qemu-preflight-01/result.json \
  --firmware /work/device-assets/current-aop_a-20261123.bin \
  --out /work/aop-qemu-diagnostic-01
```

Each profile runs in a fresh process, with at most 4096 single-step attempts and
a ten-second parent-enforced wall deadline. On timeout the parent kills the
entire private Python/QEMU process group; it does not leave a detached QEMU
running after the debugger disconnects.

A completed diagnostic means the instrumentation produced a bounded outcome.
A first unsupported address, architectural exception, blocking instruction, or
budget stop is not a firmware boot success. Completed single-step count does
not claim cycle accuracy or architectural retirement for a partially executed
faulting multi-access instruction.

Private raw image bytes, memory images, logs containing private state, and
register snapshots remain private. A public summary may retain input/tool
hashes, selected profile assumptions, counter values, and first fault address
and location after review. It must not publish firmware bytes, disassembly, or
per-opcode low-entropy hashes.

## Source verification

The source read was taken from the isolated tree at commit
`3b938b23f4ce3e9cde88f31ae39d6280524624e0`. Exact copies and their hashes
are saved in the draft's `baseline/` and `source-read-receipt.json`.

Relevant primary implementation paths:

- [`gdbstub/system.c`](https://github.com/cyjin-yl/qemu-tb371fc/blob/567e4f9834547f1445ecf24de96603132df8cfb8/gdbstub/system.c): RSP emits `vaddr`, then clears the hit pointer.
- [`accel/tcg/cpu-exec.c`](https://github.com/cyjin-yl/qemu-tb371fc/blob/567e4f9834547f1445ecf24de96603132df8cfb8/accel/tcg/cpu-exec.c) and [`target/arm/tcg/debug.c`](https://github.com/cyjin-yl/qemu-tb371fc/blob/567e4f9834547f1445ecf24de96603132df8cfb8/target/arm/tcg/debug.c): debug-stop lifecycle and retained entry hit fields.
- [`accel/tcg/watchpoint.c`](https://github.com/cyjin-yl/qemu-tb371fc/blob/567e4f9834547f1445ecf24de96603132df8cfb8/accel/tcg/watchpoint.c): before-access CPU-loop exit and overlap semantics.
- `system/watchpoint.c`: arbitrary nonempty interval insertion and overflow
  checks.
- `accel/tcg/tcg-accel-ops.c`: GDB watchpoint conversion.
- `target/arm/cpu.c`: ARM before-access setting and M-profile reset vectors.
- `target/arm/tcg/translate.c`: Thumb instruction length split.
- `target/arm/gdbstub.c`: writable core registers, read-only M-system registers.
- `hw/arm/armv7m.c`: NVIC/PPB layering, clock and reset requirements.

Upstream context:
https://www.qemu.org/docs/master/system/gdb.html
https://github.com/qemu/qemu/blob/master/hw/arm/armv7m.c

The public documentation is background; the proposed build uses the captured
local revision and must record its own actual resulting binary hash.

## Public loader evidence

[Fixed public source analysis](../evidence/aop-loading-public-20261123/README.md) distinguishes the SDM670 AP/XBL address convention from AOP-local instruction and vector addresses. The ELF builder rewrites selected program headers and entry metadata, without rewriting the payload. These cross-platform source facts do not identify the native Kona core, RAM size, reset vector mapping or reset-release sequence.
