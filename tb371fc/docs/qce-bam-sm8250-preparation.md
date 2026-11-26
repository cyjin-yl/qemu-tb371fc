# Shared QCE/BAM virtual cold-control preparation

This candidate binds **`virtual-qce-bam-cold-control-v1`, default off**. Its
generated include is copied unchanged from the formal source generator;
the seven words below match the separately reviewed policy. Opt-in remains
`qcom-sm8250-qce-bam.enabled-reference=on`. This candidate has not been
compiled as QEMU, run in QEMU, used for migration, or used for an original
Android attempt.

This profile is permanently a virtual software reference. No review or test
can turn it into a physical register/reset capture or silicon-version
evidence. Any physical profile would require a separate design and identity;
this virtual profile must never be relabeled as one.

The full policy is in `qce-bam-sm8250-reference-profile.json`, and its
canonical JSON SHA256 is
`f9809a8de0e6b91c57e7baf3d675dfd16c4ce1bc6e2797a0d866ec42bebf1c3b`.
The companion `qce-bam-sm8250-source-manifest.json` records the generator's
12 complete pinned inputs and the exact generated include. Both documents
and the complete source/licence bundle come from the separate source
generation candidate and must accompany integration.

| Word | Bound value | Virtual reference meaning |
| --- | --- | --- |
| QCE version | `0x05030001` | Named vendor 5.3.1 software ABI; no silicon version claim. |
| QCE engines | `0x00000000` | No modeled crypto datapath or engine-selection capability. |
| BAM configuration | `0x07fff004` | Public initializer subset with command descriptors disabled. |
| BAM revision | `0x00000203` | New virtual non-MTI control marker plus two-EE topology. |
| BAM pipe count/width | `0x00004008` | Eight-channel topology and selected 36-bit address encoding. |
| EE0 attributes | `0x800000f0` | Virtual cold-controller admission and ownership of pipes 4–7. |
| Initial EE IRQ mask | `0x00000000` | Explicit idle initialization policy. |

The vendor driver also hardcodes some software algorithm declarations;
the zero engine word does not remove those declarations. No declared
algorithm is implemented by this cold-control model. Later direct QCE
configuration or BAM submissions still encounter the unsupported boundary.

## Device and address contract

The original DT's `qcedev` and `qcrypto` clients share the QCE resource at
`0x01de0000/0x20000` and BAM resource at `0x01dc4000/0x24000`. The resources
overlap by `0x8000` bytes. One device maps their union
`0x01dc4000/0x3c000` and dispatches an explicit register allowlist. The union
has no RAM backing. Unknown offsets and unsupported widths or alignments
return a transaction error; there is no generic zero register bank.

The fixed vendor source and original SPS node select `qcom,msm-sps-4k`,
`qcom,pipe-attr-ee`, EE0 and remote management. The model therefore uses the
4K register table and the EE attribute read. It does not replace that read
with an ordinary BAM_CTRL enable bit. The actual fatal read was QCE base
`+0x1a000`; the additional direct cold reads are QCE `+0x1a108` and BAM
`+0x7c`. No returned hardware value was observed by those original-function
analyses.

The machine creates one child, `/machine/qce-bam`, with one sysbus IRQ wired
to SPI 272. No clock or timer is implemented. This matches the selected
consumer's `qcom,no-clock-support` path; it makes no physical power or clock
claim. The inactive reference has its MMIO region disabled.

## State and transitions

Pipes 4–7 have independent control, reset, IRQ enable/status, descriptor
address low/high, descriptor FIFO size and event-threshold state. The public
SPS interface limits the high address field to four bits; this candidate
retains at most a 36-bit address and never dereferences it. Address zero is
not rejected merely for being zero. Eight-byte descriptor alignment and an
enabled system-mode ring with at least two descriptor slots are explicit
supported-subset checks, not a claim about silicon's error responses.

The actual disabled-state RMW sequence is accepted: reset assertion and
release, per-pipe EE mask update, IRQ disable, direction, system mode,
threshold, descriptor address and size, stream-off, optional high address,
lock group, then pipe enable. Producer and consumer configuration remain
separate. The pure preparation check uses the source-derived sequence for
qcedev's producer/consumer 7/6 and qcrypto's producer/consumer 5/4.

Only EE mask bits 4–7 can change. Every other bit must equal the initial
reference mask M0. After both client pairs complete the cold sequence, the
expected mask is `(M0 & ~0xf0) | 0xa0`; it is `0xa0` only when the initial
outside bits are zero. This virtual policy binds M0 to zero, so its cold
sequence ends at `0xa0`; the model still preserves unowned bits by its rule.

Pipe reset is an explicit reference policy: assertion clears only that
pipe's configuration and pending state and holds it; release performs no
work. The global EE mask is retained. System reset restores the initial
reference mask and idle pipe state. Reset enter changes only internal state;
the hold phase updates the IRQ output. These are model rules, not captured
physical reset values.

IRQ status is a latch represented in state, with a read-only status and a
write-only W1C clear register. No accepted operation produces an event in
this cold-control subset, so the only reachable pending state is zero.
IRQ routing is derived from pending status, per-pipe IRQ enables and the EE mask.
A migrated nonzero pending status is rejected; migration cannot inject a
completion. The hardware-completed descriptor offset is read-only zero
because no submission is accepted and no descriptor can advance.

## Deliberately unsupported operations

Every BAM P_EVNT_REG write is rejected, including a zero doorbell. Reads of
that omitted submit register also reject. QCE GO, secure/OEM GO, data FIFO,
key, IV and algorithm registers are not in the allowlist. BAM-to-BAM data
and peer addresses, halt/timer/extra control features, other pipes, other
EEs and global control writes reject. Rejected operations do not update
state. Guest key/payload values are not included in rejection logs.

No descriptor fetch, output write, crypto engine, secure key, firmware,
clock operation, timer or asynchronous completion exists. Configuring and
enabling an idle ring does not manufacture a submission or a done IRQ.

The synchronous `qce_open` cold path and qcedev setup prepare software data
without a source-observed transfer. The pinned public crypto manager was
also examined: none of the 20 prefixed algorithm names or their 20 driver
names appears in its 188-entry test table. That source's registration path
therefore returns from its no-test branch without a cryptographic test
callback. This is a property of the reviewed public source; it is not an
observed original-kernel result or a completed cryptographic self-test.
The candidate does not change DT flags, algorithm names or callbacks to
select that branch. Any later submission reaches the explicit unsupported
boundary. No complete native qcrypto-probe or Android success is claimed.

## Migration and observation

VMState v1 has a 116-byte identity guard followed by a 4-byte EE mask and
four 32-byte pipe states: 248 payload bytes, excluding QEMU section framing.
The guard contains the enabled profile, full policy digest, exact resource
geometry, pipe/EE selection and all seven bound words. Its nested post-load
check runs before mutable control fields are read. Profile, geometry or
identity mismatch rejects before any device activity. The final post-load
check validates every mutable field and rejects impossible pending, reset,
width, feature and enabled-ring states before touching the IRQ output.

The incoming stream never contains the guard's local owner pointer. No
clock, timer, backend, memory access or deferred action runs during load.
Pure QOM getters expose the guard identity and every mutable word. QOM
getters do not read MMIO, update state or continue the guest. IRQ-route
delivery through the guest GIC is not established by these interfaces.

The guard ordering is local to this device's payload. Other machine state
can precede this device in a whole-machine stream. A rejected incoming load
may terminate QEMU; the acceptance test must retain the two pre-load pure
QOM snapshots, exact rejection diagnostic, exit status, complete output
drain and process reap. It must not invent a post-exit QOM observation or
claim that no other machine state was loaded.

The device emits one rejection diagnostic through a single log call whose
mask includes both `unimp` and `guest_errors`. The compiled test must check
the diagnostic's exact count, offset, width and direction, together with
unchanged complete QOM state and RAM witnesses. The qtest protocol's `OK`
response does not establish that an MMIO transaction succeeded.

## What actually ran

In candidate 003, the exact production `qcom-sm8250-qce-bam-state.h` helper
was included by a small standalone C preparation check and compiled with C11, optimization,
all/extra warnings, warnings-as-errors and pedantic checking. Its first
execution passed 392 checks, including four complete cold sequences, 25
atomic rejection cases, per-pipe isolation/reset, 36-bit state, zero-address
handling, and a synthetic nonzero M0 fixture. The fixture is not a proposed
hardware profile. Both compilation and execution returned zero with empty
stderr.

This checks the actual pure transition helper. It does not execute the
generated identity profile, QEMU object ABI, MMIO dispatch, migration runtime,
IRQ routing or native boot. The generated include was separately checked
byte for byte, with all ten macros, seven words and its 32-byte digest matched
to the reviewed policy. Required runtime checks remain in the accompanying
acceptance contract. The retained QEMU API review records the earlier 002
UNBOUND preparation; this binding preserves that reviewed implementation and
wire interface.

## Remaining runtime acceptance

The seven words are bound through generated source, with no numeric command
line override. The driver's revision thresholds and accepted version range
remain software conditions, not physical measurements. The formal QEMU
build, complete compiled gates and independent artifact review must precede
any original-input native attempt using this virtual profile.

Candidates 001 and 002 retain their original UNBOUND includes and evidence.
No separate UNBOUND QEMU build or enabled-realization runtime check was
performed. The final bound binary can establish only its actual default-off,
explicit-off and bound-admission behavior; the retained UNBOUND branch must
not be reported as executed coverage.
