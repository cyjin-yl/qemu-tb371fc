# TB371FC shared DSU PMU reference profile

The `tb371fc` machine has an optional shared DSU PMU selected by
`dsu-pmu=on`. It is disabled by default. The model gives all eight A55/A77
CPUs access to one counter bank and connects its level interrupt to GIC SPI
50, architectural INTID 82. Its clock is an explicit virtual reference:
`dsu-pmu-frequency-hz` accepts 1 through 1,000,000,000 Hz and defaults to
100,000,000 Hz. This frequency does not report a measured device clock.

The source adds no MMIO window, DMA access, cache or bus event generator,
physical power observation, or native launch. The independent RAS
`ras-zero-records-compat` option remains separate. Disabling the DSU option
retains the previous CPU register definitions and migration format.

## Evidence and scope

The original enabled DT node `/soc/dsu_pmu@0` associates the eight enabled
CPUs with MPIDRs `0x0` through `0x700` in steps of `0x100`. Its inherited
GICv3 interrupt tuple is `[0, 50, 4]`, which selects SPI 50, level high.
The consumed diagnostic's first unsupported DSU access was an EL1 read of
`S3_0_C15_C5_0` at `dsu_pmu_cpu_online+0x6c`. This symbol offset and the
complete 280-byte function were checked against the original image.

All 21 matching original DSU functions, totalling 4,132 bytes and 1,033
instruction slots, were decoded. They establish the consumer's lifecycle:
global enable, counter allocation, event programming, period preload,
overflow acknowledgement, interrupt handling, and transfer of the service
CPU. Counter index 31 is dedicated to event `0x11`, independently of the
number of ordinary counters. The online function still clears overflow
and assigns IRQ affinity when the reported ordinary-counter count is zero.
Returning zero from PMCR alone would not model that lifecycle.

The retained source evidence has these complete byte digests. These are
evidence identities, not new runtime success claims.

| Evidence | Bytes | SHA-256 |
| --- | ---: | --- |
| Original topology and lifecycle tool return | 60,400 | `e2d855811c3cd38b7e2ea8df3eab8c72a1eebfdb9dd50b4015193be5808bce9a` |
| Decoded lifecycle consumer contract | 29,442 | `84f0b25356de1e58f5cfd52691540e9f8197db80758831a31a13c492d7d532c4` |

The register encoding, field masks, aliases, and privilege rules follow
[Arm DSU TRM 100453_0401_04](https://documentation-service.arm.com/static/64a533dbdf6cd61d528c4c76),
chapters 5, 13 and 15. The particular reference frequency, deterministic
reset values, selected event set, and virtual event attribution below are
model policies. They do not assert that the physical TB371FC PMU exposes
the same values or timing.

## Exact register interface

The profile implements only the following thirteen AArch64 encodings.
All have `op0=3`, `op1=0`, `CRn=15`. There is no wildcard register bank.
The existing AArch32 definitions remain unchanged.

| Register | CRm | op2 | Model operation |
| --- | ---: | ---: | --- |
| CLUSTERPMCR_EL1 | 5 | 0 | Fixed identity and six counters; E, P and C controls |
| CLUSTERPMCNTENSET_EL1 | 5 | 1 | Counter-enable read; write-one-to-set |
| CLUSTERPMCNTENCLR_EL1 | 5 | 2 | Same enable read; write-one-to-clear |
| CLUSTERPMOVSSET_EL1 | 5 | 3 | Overflow-status read; write-one-to-set |
| CLUSTERPMOVSCLR_EL1 | 5 | 4 | Same status read; write-one-to-clear |
| CLUSTERPMSELR_EL1 | 5 | 5 | Store and return the low five selector bits |
| CLUSTERPMINTENSET_EL1 | 5 | 6 | Interrupt-enable read; write-one-to-set |
| CLUSTERPMINTENCLR_EL1 | 5 | 7 | Same interrupt-enable read; write-one-to-clear |
| CLUSTERPMCCNTR_EL1 | 6 | 0 | Full 64-bit cycle-counter read and write |
| CLUSTERPMXEVTYPER_EL1 | 6 | 1 | Selected event type and filter read and write |
| CLUSTERPMXEVCNTR_EL1 | 6 | 2 | Selected 32-bit counter read and write |
| CLUSTERPMCEID0_EL1 | 6 | 4 | Read `0x00020000`; writes ignored |
| CLUSTERPMCEID1_EL1 | 6 | 5 | Read zero; writes ignored |

PMCR reads `0x41413040 | E`: the reference identity uses IMP `0x41`, IDCODE
`0x41`, N=6 and RES1 bit 6. E is bit 0. Writing P, bit 1, clears the six
ordinary counters; writing C, bit 2, clears the dedicated cycle counter.
P and C read zero and do not clear overflow flags, event types, enable
bits, or fractional phase. Bit 3 is RES0: this DSU profile has no D bit or
divide-by-64 mode. X, bit 4, reads zero and ignores writes; other reserved
bits also read zero and ignore writes. Identity fields ignore writes.

The counter, overflow, and interrupt masks are `0x8000003f`. Reads through
the two aliases of each set/clear pair observe the same state. Selector
values 6 through 31 make the selected type and selected counter read zero
and ignore writes. The selector itself returns its stored low five bits;
this is the model's deterministic choice for an otherwise unimplemented
selection. Counter and control writes do not synthesize an overflow.

Only event `0x11` is advertised and implemented. Ordinary event types store
`value & 0xa000ffff`. Other event codes can be selected and read back but
do not increment. This preserves the original driver's raw-event path
without fabricating cache, bus, memory, or power events.

## Virtual time, filters and IRQ

The reference clock uses `QEMU_CLOCK_VIRTUAL`. With E and the corresponding
counter-enable bit set, an eligible counter advances by elapsed virtual
ticks. Six ordinary counters wrap modulo 2^32; the dedicated cycle counter
wraps modulo 2^64. A real arithmetic wrap sets its sticky overflow flag.
An explicit OVSSET write can also set a flag, as requested by software;
that operation is distinguishable from a measured event.

The reference gives event `0x11` ticks in ordinary slots a Non-secure
attribution. Such a slot counts when its stored NS bit 29 equals S bit 31.
This applies the register's NS/S selection to the profile's chosen virtual
event attribution. It does not establish how physical cluster-cycle
events are security-attributed. The dedicated CCNTR is independent of
SELR and the selected event filter.

The fractional phase is a numerator with denominator 1,000,000,000.
Synchronization advances that phase even while E or individual enables
are clear, but disabled counters receive no increments. P and C retain
phase. Enabling a counter therefore joins the existing virtual reference
clock, rather than creating a new oscillator. A VM pause freezes virtual
time; host wall time is never added. Physical quiescent-clock behavior is
outside this profile.

A virtual timer is scheduled for the next eligible arithmetic overflow
whose flag is still clear. IRQ is exactly
`(overflow_status & interrupt_enable) != 0`; E and counter-enable do not
mask an already pending interrupt. W1C acknowledgement or interrupt
disable withdraws the line when no enabled pending bit remains. No
unconditional completion interrupt is generated.

## Privilege, reset and ownership

The option is valid only for the board-created shared backend on the exact
A55/A77 CPU types, with AArch64, TCG or qtest, EL2 enabled, and EL3 disabled.
Each of the eight CPUs receives the same backend before realization.
Enabling a CPU property on a generic machine, or without the board option,
fails realization. The DSU device cannot be added independently with
`-device`.

EL0 has no access to these registers. Existing `HCR_EL2.TIDCP` processing
runs before register lookup for EL1, including unknown encodings in the
same space. It is preserved. When TIDCP does not trap the access, EL1
writes to the eleven mutable DSU registers require ACTLR_EL2 bit 12;
clearing it causes a categorized trap to EL2. EL1 reads and CEID
write-ignored accesses do not use this extra write gate.

On-profile AArch64 ACTLR_EL2 stores only bit 12, is accessible from EL2,
and resets with that bit set. This permissive boot setting is explicit
virtual policy, not a physical register capture. The previous AArch32
HACTLR entry and all off-profile ACTLR behavior are preserved.

Only the device's whole-system reset clears the shared controls, counts,
event types, phase, timer and IRQ. An individual CPU reset resets that
CPU's ACTLR permission only. CPU0 is the migration owner, not the reset
owner of the cluster. PSCI offline/online and migration of the driver's
service CPU leave the shared bank intact.

## Read-only observation

The machine switch is named `dsu-pmu`; the device child has the distinct
QOM path `/machine/dsu-pmu-device`. Off-profile machines have no such
child. Each `/machine/cpu0` through `/machine/cpu7` exposes read-only
`tb371fc-dsu-pmu-profile` and `tb371fc-dsu-pmu-frequency-hz` properties.
The bool qdev property `tb371fc-dsu-pmu` is fixed at realization.

The device exposes the stored counts, event types, selector, masks,
overflow, phase, last synchronization time, timer deadline and pending
state, IRQ level, migration masks, and immutable reference identity.
`event-counters-hex` and `event-types-hex` encode six ordered 32-bit values
as 48 lowercase hexadecimal characters. These properties do not
synchronize time, change counters, rearm a timer, or publish an IRQ.
Reading while running can consequently observe an older stored value.
The pause handler synchronizes virtual time before stable paused
observations. `physical-state-captured`, `cache-events-modeled`, and
`bus-events-modeled` remain false.

## Migration format and validation

The parent CPU VMState version remains 22. On-profile CPUs add subsection
`cpu/tb371fc-dsu-pmu`, version 1 and minimum version 1. Off-profile CPUs do
not emit it. The thirteen shared registers and the on-profile AArch64
ACTLR_EL2 use `ARM_CP_NO_RAW`: set/clear aliases are not replayed through
the generic CPU CPreg list.

Each CPU's subsection has a 24-byte common body in QEMU VMState integer
byte order: profile u32, CPU index u32, reference frequency u64, then
ACTLR_EL2 u64. Profile, index and frequency must equal the destination's
configured values. Only ACTLR bit 12 may be set.

CPU0 alone appends the following 108-byte bank. This is explicit
field-by-field serialization; host C structure padding is not used.

| Bank offset | Field | Bytes |
| ---: | --- | ---: |
| 0 | Magic `0x44535531` | 4 |
| 4 | CPU mask `0xff` | 4 |
| 8 | IRQ INTID 82 | 4 |
| 12 | Six event counters | 4 |
| 16 | Reference frequency | 8 |
| 24 | PMCR E | 4 |
| 28 | Counter enable | 4 |
| 32 | Overflow status | 4 |
| 36 | Interrupt enable | 4 |
| 40 | Selector | 4 |
| 44 | Six event counters | 24 |
| 68 | Six event types | 24 |
| 92 | Cycle counter | 8 |
| 100 | Fractional phase | 8 |

CPU0's body is 132 bytes; each other CPU's body is 24 bytes. The aggregate
DSU body across eight CPU subsections is 300 bytes, excluding subsection
framing and the existing CPU VMState bodies.

CPU0 pre-load withdraws the old warm IRQ and stops the timer before
incoming state is accepted. A complete valid bank is staged, with
identity, masks, selector, event-type and phase checks. CPUs must load in
the board's canonical 0 through 7 order. Their DSU mode checks precede
generic CPreg merge, and each CPU marks completion only after its existing
CPU post-load validation. Duplicate DSU subsections and repeated CPU0
while that eight-CPU load is incomplete are rejected. CPU7 publishes the
staged bank only when all eight completion bits are present.

The destination rebases synchronization to its restored virtual clock
and retains the saved fractional phase. It recomputes the IRQ and next
overflow deadline instead of importing a host timer object or counting
wall time. On/off mismatches in either direction, missing DSU subsections,
profile/index/frequency differences, invalid ACTLR bits, malformed bank
fields, and truncated input fail the affected load. A failed incomplete
load keeps the DSU output withdrawn.

These checks concern the CPU sections that QEMU actually loads. They are
not a new generic migration-container validator and do not claim to
reject every possible omission of an entire unrelated device or CPU
section. Independent stream tests must retain full framing and verify all
eight CPU sections, the one bank, and unchanged surrounding bytes.

## Validation status

This model source preparation does not establish a compiled gate result,
successful migration, a new native result, or Android/UI success. The
independent `dsu-pmu-reference` gate is expected to exercise all eight
CPUs, exact access and trap syndromes, shared state and reset boundaries,
virtual-time brackets, real overflow IRQ delivery, and complete migration
streams. Those results must be obtained from the corresponding compiled
artifact and retained raw evidence before making runtime claims.
