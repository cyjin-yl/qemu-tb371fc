# CPUFreq HW EPSS reference

## Scope and evidence

The TB371FC board always creates three **qcom-cpufreq-hw-epss** devices at
/machine/cpufreq0, /machine/cpufreq1 and /machine/cpufreq2. Their default
profile is unknown: supported register locations return transaction errors,
output clocks remain stopped and IRQs remain low. Merely creating the board
does not supply a firmware frequency table.

The explicit reference profile implements a synthetic four-state LUT,
requested performance state, an external frequency-limit stimulus, a
latched limit IRQ and exact virtual-time cycle accounting. It does not
implement analog DVFS, rail settling, hardware characterization, TCG CPU
execution speed, PMU/retired cycles, CPU idle gating or proprietary firmware.
Its voltages and frequencies are declared reference data, not measurements
of the tablet.

The source interface is pinned to
[OnePlusOSS/android_kernel_oneplus_sm8250 at
94304e12a3509eac7b3716bf574ce0b1fcaacbae](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/94304e12a3509eac7b3716bf574ce0b1fcaacbae):

| Source | SHA-256 | Relevant contract |
|---|---|---|
| [drivers/cpufreq/qcom-cpufreq-hw.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/cpufreq/qcom-cpufreq-hw.c) | 559c942f52eedf76ab22f7770b7fa49a924c1941486b1e9afd419d38885ad174 | EPSS offsets; LUT parsing; performance index; per-CPU counter addresses; limit IRQ and clear sequence |
| [include/dt-bindings/clock/qcom,gcc-kona.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,gcc-kona.h) | ec7b2a775a7f96a543c064c7fc733fb83ffa7e3dae3c4bb517200627ae5f7fed | GCC clock ID 209 is GPLL0 |
| [include/dt-bindings/clock/qcom,rpmh.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,rpmh.h) | 04394dd2f1bfd89c06730c75e5bc3c01833b1f072968f17d4b5630e7247872cf | RPMh clock ID 0 is RPMH_CXO_CLK |
| [drivers/clk/qcom/clk-rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rpmh.c) | 75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76 | Kona bi_tcxo uses xo_board divided by two |

The [bounded actual DT capture](../evidence/wholeboard-20261123-a47/cpufreq-dt-evidence-52492a.json)
and [independent source/DT review](../evidence/wholeboard-20261123-a47/cpufreq-dt-review-52492a.json)
preserve the raw properties and distinctions from reference policy. The
input DTB is 973052 bytes with SHA-256
5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207.
The 40324-byte bounded capture has Git blob
541c58de5c6711215cd6ebc24e48a11ea4a69127 and SHA-256
815874739b5ccfd4f248fd953849f775387dab53b2859dff508bee74ae74136d.
The review is Git blob 230c524e407eec3fd70922f66551a1f2a2aba770.

The preceding native run used source
52492a797d2acd9c918a8639564e8ff3e7beab3f and actual QEMU SHA-256
debe05589d1673680231dada5377c68b0f7f7ec1b2432f8fb7eff249e8a582e3.
Its first invalid MMIO access was a four-byte read at **0x18591100**, in
qcom_cpufreq_hw_driver_probe+0x408/0x980. This matches domain 0's EPSS LUT
row 0. CPU4 executing that probe does not make it domain 1's access.
The production Image was not rebuilt from the reference source, so exact
inlined C-statement attribution remains source-correlated inference.
This motivates the model; it does not certify the new model's CI or native
result.

## Actual topology

The captured compatible is qcom,cpufreq-hw-epss. It is not the standard
qcom,cpufreq-hw layout with offsets 0x110/0x114/0x920/0x9c0.
The DT supplies a four-byte LUT row size. It has no maximum-entry override,
so the pinned driver uses its default of 40 entries.

| Domain | Resource | CPU consumers | DT max_cores argument | Counter offsets | SPI / GIC INTID |
|---|---|---|---:|---|---|
| 0 | 0x18591000 + 0x1000 | CPU0..3 | 4 | 0x3c8, 0x3cc, 0x3d0, 0x3d4 | 30 / 62 |
| 1 | 0x18592000 + 0x1000 | CPU4..6 | 4 | 0x3c8, 0x3cc, 0x3d0 | 31 / 63 |
| 2 | 0x18593000 + 0x1000 | CPU7 | 4 | 0x3c8 | 19 / 51 |

All IRQs are level-high. All frequency-domain second arguments are **4**;
the model does not replace them with the different CPU-consumer counts
4/3/1. CPU masks are 0x0f, 0x70 and 0x80.

qcom,skip-enable-check is present, so this native driver skips ENABLE.
The reference profile nevertheless supplies the one source-defined ENABLE
word for a bounded opt-in firmware handoff. qcom,no-accumulative-counter
is absent. Consequently the driver adds
(cpu - first_related_cpu + 1) * 4 to 0x3c4, and the base word 0x3c4 itself
is not used. That word remains a hole.

The actual clock specifiers are RPMh phandle 21 / ID 0 for xo and GCC
phandle 22 / ID 209 for alternate. The board connects the input Clock xo
to its existing XO carrier and alternate to the existing GCC gpll0 Clock
output. xo_board is explicitly 38400000 Hz in the DT; the pinned RPMh
driver divides it by two. The nominal 19200000 Hz is therefore a source/DT
value, not a measured crystal frequency. A disconnected or stopped XO
does not become a fabricated running frequency.

The original DT is not rewritten. Child services such as cpu7-notify,
CPU isolation and qcom,msm-hw-limits remain outside this device. The
child's isens reference-voltage properties are not CPU LUT voltages.

## Explicit reference table and boot state

Enable the synthetic XO01 profile with:

    -global qcom-cpufreq-hw-epss.enabled-reference=on

This is an explicit choice of the following table and reference firmware
handoff. The physical LUT, boot performance index and voltages were not
captured.

| Row | Raw frequency word | L | Reference frequency at 19.2 MHz XO | Raw millivolts | OPP microvolts |
|---:|---:|---:|---:|---:|---:|
| 0 | 0x40040010 | 16 | 307200000 Hz | 750 | 750000 |
| 1 | 0x40040020 | 32 | 614400000 Hz | 800 | 800000 |
| 2 | 0x40040040 | 64 | 1228800000 Hz | 900 | 900000 |
| 3 | 0x40040060 | 96 | 1843200000 Hz | 1000 | 1000000 |
| 4..39 | 0x40040060 | 96 | Same as row 3 | 1000 | 1000000 |

Every word uses source encoding 1 and core_count 4. The source extracts
bits 31:30, 18:16 and 7:0. Every nonzero source value in that driver selects
XO times L; this particular reference ROM exposes only encoding 1.
The voltage word uses the low 12 bits in millivolts, with the other fields
zero. These values merely supply reference OPP descriptions. There is no
voltage-regulator output or claim that the tablet supports these OPPs.

The driver encounters the equal adjacent frequency/core_count words at
rows 3 and 4, and makes row 4 the table terminator. Only rows **0..3** can
be selected by PERF. Rows 4..39 are deterministic repeated ROM entries,
not extra operating points, boost states or writable LUT storage.

The pinned driver also knows a source-0 path derived from alternate/2.
It is intentionally absent from this limited reference ROM. In particular,
the model does not claim that a measured 300 MHz entry exists. The real
alternate Clock remains connected and observable; changing GCC GPLL0
does not change a selected XO row. Using all-XO rows keeps the limit
status's integer L representation exact without inventing a quantization
rule.

reference-boot-perf-index is a pre-realize uint32 property, default 0,
allowing only 0..3. A nonzero value is rejected when the profile is
unknown. It explicitly selects the reference cold/reset state and is
part of migration identity. ENABLE reads 1 under the reference profile.
These boot choices are not evidence of the tablet's firmware state.

## Sparse register semantics

Every device transaction must be aligned and exactly four bytes. There
are eight sparse windows per domain, totaling 89/88/86 words, or 263 words
and 1052 bytes across the board. The rest of the three 4-KiB resources
remains unassigned.

| Offset | Access | Reference semantics |
|---|---|---|
| 0x000 | RO | ENABLE bit 0 is 1 in the explicit reference profile |
| 0x020 | RO | Effective L in bits 7:0 |
| 0x100..0x19c, stride 4 | RO | Forty frequency ROM words |
| 0x200..0x29c, stride 4 | RO | Forty voltage ROM words |
| 0x308 | RW command | Reads zero; writing bit 2 clears the limit-status latch |
| 0x30c | RO | Limit-status latch, bit 2 only |
| 0x320 | RW | Requested performance index, full value must be 0..3 |
| 0x3c8..last related core, stride 4 | RO | Exact low 32 bits of that core's reference cycle count |

0x304 is a hole. Although named REG_INTR_EN in the source's offset array,
the pinned driver never accesses it and provides no bit/reset contract.
The reference IRQ path is armed when the profile is enabled; the guest
driver masks and reenables the actual GIC interrupt.

The physical counter addresses follow the actual per-core layout. No
extra counters are exposed for nonexistent domain consumers, and no
domain-aggregate counter is silently invented at 0x3c4. Standard-layout
PERF/counter offsets and surrounding control/debug words remain holes.

Writes to ROM, status, ENABLE, DOMAIN_STATE and counter words fail.
PERF indices outside 0..3, including the repeated terminator and values
with high bits set, fail without changing state. CLR permits only bit 2;
unsupported bits reject the complete four-byte transaction. Zero is a
valid no-op CLR command.

Atomic rejection describes one four-byte device transaction. A larger
QTest/address-space request may be split into separate word transactions;
a later word hitting a hole does not roll back an earlier accepted word.
Core width/alignment diagnostics and device policy errors are separate.
The core emits the exact invalid-size diagnostic for naturally aligned
one-/two-byte chunks; unassigned holes use its accepts-callback rejection.

## Performance, limits and IRQ state

A valid PERF write changes the requested row synchronously. Before the
derived rate changes, the model settles all elapsed virtual time using
the old cached rate, preserving fractional cycles. No undocumented PLL
transition delay, analog completion bit or firmware acknowledgement is
invented. PERF continues to report the requested index during a limit.

frequency-limit-lval is a live host-controlled uint32 input, available
during normal TCG as well as QTest. It is not a guest register or a
QTest-only shortcut. It requires a realized reference profile. Values
outside 0..255 fail atomically.

- 0 supplies an uncapped reference environment.
- 1..255 supplies an external cap expressed as an XO multiplier.
- Effective L is the minimum of requested L and a nonzero cap.
- The output cpu-frequency Clock and cycle integrator use effective L
  times the actual input XO frequency.
- A zero/unknown XO stops the output and cycle accumulation. Raw ROM and
  requested/effective multiplier state do not claim that a physical clock
  is locked or running.

The external cap does not come from a modeled thermal sensor, current
sensor, rail controller or secure firmware. Setting it is an explicit
stimulus. It does not overwrite PERF or pretend that the guest changed its
request. selected-voltage-microvolt remains the requested ROM descriptor,
not a measured or enforced physical rail voltage.

The armed reference path latches STATUS bit 2 on entering a known
restriction, or when the effective frequency decreases further while
restricted. An overlarge nonbinding cap produces no event. Removing a
cap changes the effective rate but preserves a previously latched event.

CLR is an explicit reference W1C policy: read zero, OR 4, write 4 is the
driver's clearing sequence. A sustained unchanged restriction does not
immediately relatch after W1C. A subsequent newly effective restriction
or further rate decrease can latch it again. Raising an existing cap
while still restricted does not itself relatch a cleared event.
No host timer fabricates repeated limit events.

The driver can compare DOMAIN_STATE's effective L times XO with the
requested PERF frequency. When they agree, its poll routine clears the
event and reenables its GIC interrupt. That comparison is exact for the
reference all-XO table. No separate register returns an unconditional
mitigation-success acknowledgement.

## Cycle accounting

This profile treats every related core's reference clock as active while
QEMU virtual time advances, at the common effective domain frequency and
with a common cold origin. Therefore the separate related-core counter
addresses return equal values within a domain. The corresponding cores
do not have independent activity stimuli in this limited implementation.

For each elapsed interval:

    cycles = floor((elapsed_ns * frequency_hz + saved_fraction) / 1000000000)
    new_fraction = (elapsed_ns * frequency_hz + saved_fraction) % 1000000000
    counter = (previous_counter + cycles) mod 2^32

mulu64 and divu128 perform the intermediate arithmetic without overflowing
even for a large virtual-clock jump. The low 32 bits of the full quotient
are intentionally retained. The driver has a separate UINT_MAX-based
software rollover quirk; the model does not reproduce that off-by-one in
the hardware word.

Read-only MMIO and QOM counter observations calculate the current value
without modifying the saved value, fraction or epoch. A real PERF change,
external limit update, provider Clock update or migration pre-save
settles the old interval once. No cycles are credited while QEMU virtual
time is stopped, for host wall-clock delay or for migration transport time.

These are synthetic clock-cycle counters. They are not retired
instructions, execution time, scheduler utilization, WFI-gated cycles or
measured power consumption. Selecting a row does not alter the
Cortex-A55/A77 TCG execution speed, Arm architected timer or PMU.

## Reset and migration

Cold realization starts the reference boot row with count/fraction zero,
no external cap and a clear status latch. System reset restores that
explicit boot row, clears count/fraction and the status latch, and resets
the reference epoch to current virtual time. Provider Clocks and the live
external cap survive system reset. A retained cap immediately affects the
restored rate, but reset itself does not generate a fresh limit IRQ.
Later provider/input transitions still follow the normal event policy.

Reset enter only changes local state; reset hold publishes Clock and IRQ
outputs. There is no guest reset register inferred from ENABLE.

VMState name qcom-cpufreq-hw-epss has version 1/minimum 1. Every domain
exists in both profiles, so immutable configuration is checked in both
reference-to-unknown and unknown-to-reference directions. The immutable
prefix includes profile, domain, physical resource, CPU mask/count,
max_cores, reference boot index and the XO01 table identity/geometry.

The normal 1:1 input-Clock state has this payload:

| Byte offset | Field |
|---:|---|
| 0 | Profile marker, uint32 equality |
| 4 | Domain ID, uint32 equality |
| 8 | Physical base, uint64 equality |
| 16 | Resource size, uint32 equality |
| 20 | CPU mask, uint32 equality |
| 24 | max_cores, uint32 equality |
| 28 | CPU counter count, uint32 equality |
| 32 | Reference boot index, uint32 equality |
| 36 | LUT identity 0x584f3031, uint32 equality |
| 40 | LUT entries 40, uint32 equality |
| 44 | LUT stride 4, uint32 equality |
| 48 | Requested performance index, uint32 |
| 52 | External frequency-limit L, uint32 |
| 56 | IRQ-status latch, uint32 |
| 60 | Settled counter value, uint32 |
| 64 | Settled fraction, uint32 |
| 68 | XO Clock period, uint64 |
| 76 | Alternate Clock period, uint64 |

This is 84 bytes per domain before any optional Clock subsections.
The table words are immutable ROM, not separate mutable state. The
source's absolute counter epoch and derived rate are not serialized.

pre_save samples virtual time once and commits count plus fractional
progress. post_load validates performance index, limit range, supported
status bits, fraction below 1e9 and the canonical unknown-profile state.
It rebases that saved count/phase at the destination's current virtual
time, so source and destination QTest origins need not match. Normal CPU
timer state loads before board devices.

The first destination MMIO is not required to repair the state:
post_load derives the rate from the restored inputs and request/cap,
sets the output Clock without propagation callbacks, and immediately
redrives the IRQ. It does not execute a guest request, generate a new
event or credit transport time. Existing devices' VMState versions are
unchanged. Compatibility with historical binaries lacking this device
is not claimed.

## Read-only observation

The normal host input frequency-limit-lval is the only live writable
state property. The pre-realize profile, domain and reference boot
properties are fixed after realization. There are no cycle-count,
raw-LUT, status or output-rate injection properties.

Geometry getters include memory-base, resource-size, related-cpu-mask,
max-cores, core-counter-count, lut-max-entries, lut-row-size and
valid-perf-count. xo-frequency-hz and alternate-frequency-hz read actual
input Clocks. The table exposes lutN-frequency-word, lutN-voltage-word
and lutN-frequency-hz for N=0..39.

Runtime getters include performance-index, requested-lval,
effective-lval, requested-frequency-hz, frequency-hz,
selected-voltage-microvolt, frequency-known, limited, interrupt-status
and irq-level. The cpu-frequency output is a normal QEMU Clock object.

counter and counter-fraction compute the current reference value without
writing state. counter-value-at-epoch, counter-fraction-at-epoch and
counter-epoch-ns expose the stored anchor. coreN-counter exists only for
the actual local related cores, with the same reference-origin policy.
Unknown-profile runtime/table/limit getters return an explicit error;
profile, geometry and provider observations remain available.

A stopped pure-QOM snapshot is an observation of these declared digital
states. It cannot establish firmware equivalence, successful native
CPUFreq registration, physical CPU voltage, actual CPU performance,
userspace boot or Android.
