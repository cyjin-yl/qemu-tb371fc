# QCE/BAM compiled acceptance contract

This candidate contains executable tests for a separately compiled QEMU. It is
local preparation, not a compiled model result. No QEMU process, device, SSH,
network operation, native runner, or production boot input was used while
preparing this directory. The original five native runners are unchanged.

## Bound input and provenance

The working profile is the complete Root-reviewed JSON at
`tb371fc/docs/qce-bam-sm8250-reference-profile.json`: 16,599 bytes, file SHA256
`6bb5bff5a445a73fab54261376ef73a820c6b2ac050048e5fca45715755f76be`.
Its canonical ASCII JSON SHA256 is
`f9809a8de0e6b91c57e7baf3d675dfd16c4ce1bc6e2797a0d866ec42bebf1c3b`.
Canonicalization sorts keys, uses `(',', ':')` separators, has no self-digest
field and no trailing newline. It is a deliberately limited virtual reference,
not a physical identity capture, secure firmware execution or crypto result.

The seven identity words in this reviewed policy are `05030001`, `00000000`,
`07fff004`, `00000203`, `00004008`, `800000f0`, `00000000`, in the order QCE
version, engines, BAM config, revision, pipes, EE attribute, initial EE mask.
All source pins and rationale remain in the complete profile. The test does
not extract its expected values from the model's C source or generated include.

The source baseline is runtime commit
`70b387fb064cdf2099e85dd89213a5ea7e2c398f`, unchanged by docs-only HEAD
`20cc7d51def15a9e980bad202166d29f3628d266`. The compatible model observation and
wire interface is `/dev/shm/qce-model-candidate-6a5-002/test-interface-contract-002.json`.
The model's static API review binds the actual QEMU memory dispatch, VMState,
QOM, reset and migration APIs; this is source evidence, not runtime evidence.

## Executable entry points and CI accounting

```sh
python3 tb371fc/tests/test_qce_bam_sm8250.py --qemu /absolute/built/qemu-system-aarch64 --mode safety --out /new/absolute/safety-result
python3 tb371fc/tests/test_qce_bam_sm8250.py --qemu /absolute/built/qemu-system-aarch64 --mode reference --out /new/absolute/reference-result
```

Both commands require a new output directory. Neither retries failed cases.
The exact QEMU binary SHA256 is recorded before execution and checked afterward.
Every test-created process starts with `-S`, no kernel/boot image and no `cont`.

| Gate | Checks | New processes | Migration files | Scope |
| --- | ---: | ---: | ---: | --- |
| `qce-bam-source-generation` | 2 aggregate source checks | 0 QEMU | 0 | Complete public sources, policy and exact generation; final implementation is supplied by the separate source-generation candidate. |
| `qce-bam-safety` | 4 | 4 | 0 | Default off, explicit off, pure paused TCG observations, bound admission. |
| `qce-bam-reference` | 74 | 76 | 65 | Cold control, strict rejection and complete wire/migration checks. |

The unchanged baseline is **102 component + 5 Linux = 107 gates**. Three QCE
gates append to the plan, giving **105 component + 5 Linux = 110 gates**. No
preexisting command, expected field, timeout, list length, count or sequence is
changed. The entire normalized prior 107-case plan has SHA256
`53489eaf9a4141bc48c69b910ac86fae384065c637829c2ce7f990da0b5365f8`, identical to
the retained part of the new plan. A separate receipt compares every field of
every case in order; older 100/102-case assertions remain as additional checks.

The production source generator/verifier is owned by the separate
`/dev/shm/qce-source-generation-6a5-001/source` candidate. This tests candidate does not
ship competing implementations at those paths. Root must integrate both the
model and source-generation candidates before running the complete CI plan.

## Independent state and access checks

The device exposes 59 unsigned integer, 8 Boolean and 2 string read-only own
properties, plus the `enabled-reference` configuration property. Two complete
70-getter observations happen before any test memory command or `cont` at each
new source and migration target. Counts and protocol transcripts prove this
boundary. Getter immutability is exercised for all 69 read-only properties.

The active memory map must contain exactly one `.union` leaf spanning
`[0x01dc4000, 0x01e00000)` when enabled and no such leaf when disabled. This is
a strict dispatch window, not backing RAM. Off-state accesses are checked as
address-space decode rejections without interpreting returned error bytes.

The source-derived cold path exercises pipe pairs 7/6 and 5/4 and, separately,
the inverse client-order fixture. Descriptor addresses and client order are
explicit test inputs, not a claim to have observed the original kernel's exact
runtime trace. QCE version and engines are read per client; BAM config is read
on first registration. The shared remote BAM's EE attribute/revision/pipe-count
check occurs only once, before the first producer connection. Later pipes and
the second cached client do not repeat that check.

For each pipe the test retains the complete reset1/reset0, EE mask RMW, IRQ
disable, direction RMW, system mode RMW, threshold, descriptor low, FIFO-size
RMW, stream-clear RMW, conditional descriptor high, group RMW, final enable,
runtime IRQ enable/mask RMW and automatic enable-state read. Each accepted
write is checked against the complete independent QOM state. Extra IRQ-source
reads and the protocol IRQ fence are recorded outside the source-order trace.
The final EE mask is `(M0 & ~0xf0) | 0xa0`, with all other bits preserved.

Aligned 1/2/8-byte accesses and unaligned 4-byte accesses at +1/+2/+3 must
produce exactly one diagnostic with their original offset and width. Actual
QEMU dispatch source confirms that `impl.unaligned=true` preserves this call
shape within one region. QTest ignores `MemTxResult` and replies `OK` after
dispatch, so `OK` is never used as MMIO success or a rejected read value as
register identity. The test requires the exact diagnostic and unchanged full
state, RAM witnesses and IRQ events.

Read-only, unknown register, unsupported pipe/EE, three GO selector, key, IV,
data, status/config and every tested doorbell access are rejected. Doorbell
values include zero. Unsupported operations compare every byte of four
allocated 16-byte descriptor witnesses and a 256-byte output witness. Those
are deliberately bounded artificial RAM inputs; no claim is made about
unobserved RAM. No invalid operation is repaired before comparison.

Descriptor state covers zero and the 36-bit maximum, high-word overflow, low
alignment, FIFO alignment/limits, the enabled minimum of 16 bytes, threshold
limits, reserved/unsupported control bits, held-reset writes, isolation of
each pipe, all 16 owned EE mask combinations, unowned-bit rejection, idle W1C,
and system reset to the reviewed initial mask. These tests never submit DMA,
generate a completion, implement algorithms, or establish GIC/guest IRQ use.

## Complete migration contract

The ordinary VMState section must be type 4 (`FULL`), version 1, instance 0,
with exactly 248 bytes followed by its matching section footer. All payload
bytes are compared to an independently encoded source QOM snapshot before
mutation. The guard is 116 bytes, followed by the 4-byte EE mask and four
32-byte pipe records. VMState integers are big-endian; host C padding is not
used. The guard's local owner pointer is never serialized.

Disabled and enabled mixed-state sources each migrate forward, are re-saved,
and migrate back into a fresh process: four completed load directions. The
enabled snapshot includes enabled pipes, maximum-width descriptor state,
partially configured disabled state and a held-reset pipe. Both target
observations occur before any test RAM/MMIO access or `cont`. Equality covers
all 70 own observations and all 248 wire bytes.

Two unmodified profile-cross streams must fail. Fifty-three single-bit field
mutations cover every guard field (including both ends of its digest), EE mask
and all eight fields of each pipe. Five further single-bit negatives cover
cross-field constraints and disabled-state mutation. Three structural streams
exercise section identity, minimum version and missing guard. Each negative
retains its original and mutated full file, exact mutation offset/hash and
expected diagnostic. Originals are rehashed after all negatives.

A rejected load is expected to terminate QEMU with exit 1. The test records
two pure preload observations, zero memory/`cont` commands, the specific
diagnostic, actual wait, final protocol drains and the fatal exit. It does not
invent QOM observations from an already dead process. The nested guard checks
precede this device's mutable fields; the test does not claim that full-machine
RAM or other devices have not been restored by QEMU core.

## Process ownership and cleanup

Every process is newly created by its fixture. Normal exits require a QMP
`quit` request, acknowledgement, EOF on every opened protocol connection,
complete drain receipts and an actual successful `Popen.wait()`. Expected
fatal exits explicitly mark quit inapplicable because the child has already
exited, require actual `wait()` exit 1, and retain EOF or connection-reset
terminal reads. Unopened startup/TCG channels are explicitly distinguished
from observed EOF. Standard output/error remain in full files with byte counts
and SHA256 after wait. No poll-only cleanup proof is accepted.

A timeout can signal only the `Popen` child created by the same fixture; any
forced terminate/kill is recorded and makes the gate fail. No existing process
is enumerated or signalled. Output interception observes only this device's
IRQ line and replaces its GIC connection; it is not guest interrupt evidence.

## UNBOUND and local verification boundaries

The earlier UNBOUND profile, candidate, receipts and fail-closed test branch
are retained. Against a separately compiled UNBOUND artifact, `--mode safety`
with `--profile tb371fc/docs/qce-bam-sm8250-unbound-test-profile.json` requires
enabled realization to fail; against an UNBOUND policy `--mode reference`
fails before creating QEMU. The final bound binary cannot establish compiled
UNBOUND realization behavior. Root elected not to add an expensive second
build; its unexecuted branch is not counted as runtime success.

Local AST/import and policy/wire-parser checks do not execute QEMU. The parser
checks use explicitly synthetic section envelopes, not fabricated QEMU
migration evidence. The first local CI-unit attempt is retained: two plan
exclusion-list failures were corrected without changing historical hashes;
one original socket-copy unit failed with `AF_UNIX socket(): EPERM` in this
environment. Its test and gate remain unchanged. A targeted 20-test plan and
QCE-verdict run passed after correction; this is not a full CI-unit pass.

The separately owned production verifier's local `verify-002/result.json`
actually passed two aggregate checks, 127 rejection cases and two parser
positive controls over 12 inputs totaling 770,745 bytes and two exact outputs.
This CI judge accepted that complete receipt and rejected ten altered,
contradictory receipts. These checks exercise source and verdict handling only.

The production source-generation gate, compiled safety/reference gates and
native execution remain Root's separate next actions, with their real results
retained on first run.
