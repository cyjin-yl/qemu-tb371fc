# TB371FC TSENS24xx digital reference

## What this model establishes

Two bounded TSENS24xx devices implement host-supplied temperature sampling,
signed threshold comparisons, latched upper/lower and critical interrupts,
masking and held-clear state. They use virtual time and participate in reset
and migration. They do not obtain physical temperatures, perform analog
conversion or reconstruct factory calibration.

The model is disabled by default. An unavailable profile returns a failed
transaction for every mapped MMIO register; it does not supply an always-ready
controller or a valid zero-temperature sample.

This increment follows an actual failure in the original-kernel boot with the
71-gate QEMU build, source commit
`3ef98a8afb49a873d677288693d6491c50fd75e1`:
`__tsens2xxx_hw_init+0x10` attempted a 32-bit read at physical
`0x0c222004`. The binary SHA-256 was
`703af9a899455c1c32636a9e3535294031c93cd9624fdc193177c61cb8ff7600`.
That observation establishes the missing access, not a measured register value.

## Sources and actual device tree

The primary driver is the official
[OnePlusOSS SM8250 tree at 94304e12](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/94304e12a3509eac7b3716bf574ce0b1fcaacbae).
The following exact files establish the downstream register consumers.

| File | SHA-256 | Relevant evidence |
| --- | --- | --- |
| [tsens2xxx.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/thermal/tsens2xxx.c) | `8e3bcb1179ecbb482cb2e7a32ad0daff28caace4ee2582b9521e016fb5ce1283` | Control enable bits, signed temperature codes, VALID, first-round flag, thresholds, mask/clear sequences and IRQ handlers |
| [tsens.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/thermal/tsens.h) | `dc878c52e6d0cd41b102759018c15b7982004957562ade83db3cd3fa5db0970e` | Sixteen channels and sensor-status offset |
| [msm-tsens.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/thermal/msm-tsens.c) | `07de996bd2b5e4f4bcf06508535a75f70fa59b9b174f9dad397284753658e282` | Compatible selection, named resources, optional calibration and thermal-sensor registration |
| [tsens-dbg.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/thermal/tsens-dbg.c) | `3b0421f3bcb60e3507a9896a7dcc92c1da5719a05acc0fe21a181aead863ec52` | Separate debug/test-bus paths, left unsupported here |

Upstream Linux
[tsens-v2.c at v6.12.114](https://github.com/gregkh/linux/blob/v6.12.114/drivers/thermal/qcom/tsens-v2.c),
SHA-256
`9c6603dc6fff25f4c191e148df28edc08f2b35e0f556bc20dc9a1986759442f9`,
provides additional field evidence for SROT software reset bit 1 and the
critical-threshold register. Its common
[tsens.c](https://github.com/gregkh/linux/blob/v6.12.114/drivers/thermal/qcom/tsens.c),
SHA-256
`c69db793d439d22959a3247c5956155ebfe9137a30d6feb4c490cffb0d77a161`,
also programs critical thresholds. These sources do not replace the vendor
driver's use of TRDY bit 3 with upstream's separate bit-0 interpretation.

The bounded [actual DT capture](../evidence/wholeboard-20261123-a47/tsens-dt-evidence-3ef98.json)
and [independent source/DT review](../evidence/wholeboard-20261123-a47/tsens-dt-review-3ef98.json)
come from the original DT with SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The capture preserves the complete two controller nodes and their direct
thermal references.

| QOM object | SROT DT resource | Implemented control word | TM DT resource | Upper/lower SPI | Critical SPI |
| --- | --- | --- | --- | --- | --- |
| `/machine/tsens0` | `0x0c222000 + 0x4` | `0x0c222004` | `0x0c263000 + 0x1ff` | 506 | 508 |
| `/machine/tsens1` | `0x0c223000 + 0x4` | `0x0c223004` | `0x0c265000 + 0x1ff` | 507 | 509 |

Both nodes identify `qcom,tsens24xx`, have
`tsens-reinit-wa`, and specify level-high IRQs. Neither has
a 0C sensor, a calibration/eeprom resource, nvmem cells, clocks or supplies.
Forty-five thermal references cover channels 0–15 on the first controller and
0–8 on the second. Those references do not reveal the firmware enable bitmap.

The DT's four-byte SROT length is preserved as evidence. The pinned driver and
actual failure establish its access at offset 4. The pinned ARM64 ioremap code
page-rounds the mapping, which explains how the driver reaches that word.
Only the established word is mapped; there is no fabricated SROT RAM page.

## Explicit configuration and temperature inputs

Device type: `qcom-tsens24xx`.

A deliberately chosen reference boot environment can be requested with:

```text
-global qcom-tsens24xx.enabled-reference=on
-global qcom-tsens24xx.reference-boot-enable-mask=0xffff
-global qcom-tsens24xx.reference-temperature-millicelsius=25000
```

This example chooses all sixteen channels and a 25 °C external environment
for both controllers. It is not a reading of the board's enable bitmap or
temperature. The defaults are profile off, boot mask zero, and temperature
`INT32_MIN` (unknown). Explicit boot state with profile off
is rejected. A reference profile with mask zero remains disabled until its
control word is programmed.

The boot mask accepts only sixteen bits. A nonzero boot mask produces
`CTRL.EN | (mask << 3)`; zero produces a zero control word.
Initial temperature accepts the unknown sentinel or a value from -204800 to
204700 millidegrees Celsius, in exact steps of 100.

Each controller exposes normal host environment inputs:

- `sensorN-input-millicelsius` is a signed 32-bit read/write
  property. A legal numeric temperature supplies a known input; the unknown
  sentinel removes it.
- `sensorN-input-valid` is a Boolean read/write property.
  Clearing it withdraws the source without destroying its stored temperature.
  Setting it requires a previously supplied numeric temperature.

These inputs are available in normal TCG as well as QTest. They are product
environment stimuli, not test-only backdoors, measured thermometers or guest
MMIO registers. A host change does not edit the current captured conversion,
last sample, threshold, VALID flag or interrupt latch.

## Sparse register contract

All device transactions are aligned, little-endian 32-bit accesses. Unknown
addresses remain unassigned. Unsupported bits reject the individual word
transaction without mutation. A wider system-memory request can be split by
QEMU; failure of a later fragment does not roll back an earlier valid word.

Each controller exposes 57 words, or 228 bytes:

| Bank/offset | Behavior | Supported bits |
| --- | --- | --- |
| SROT `+0x04` | Read/write enable, software reset and channel bitmap | EN 0, SW_RST 1, channels 18:3 |
| TM `+0x04` | Global interrupt enables | lower 0, upper 1, critical 2 |
| TM `+0x08` | Read-only upper/lower latches | lower 15:0, upper 31:16 |
| TM `+0x0c` | Held upper/lower clear | all 32 bits |
| TM `+0x10` | Upper/lower mask; one suppresses IRQ | all 32 bits |
| TM `+0x14` | Read-only critical latches | sensor bits 15:0 |
| TM `+0x18` | Held critical clear | sensor bits and configuration bits 30/31 |
| TM `+0x1c` | Critical mask | sensor bits and configuration bits 30/31 |
| TM `+0x20 + 4*N` | Lower/upper thresholds | signed lower 11:0, upper 23:12 |
| TM `+0x60 + 4*N` | Critical threshold | signed 11:0 |
| TM `+0xa0 + 4*N` | Read-only sample and latches | code 11:0; lower 17, upper 18, critical 19, VALID 21 |
| TM `+0xe4` | Read-only first-complete-round flag | bit 3 |

The TM container has the original size `0x1ff`; its actual
subregions cover `[0x04,0xe0)` and
`[0xe4,0xe8)`. SROT offset zero and all other SROT words,
TM controller ID, 0C/min-temperature paths, watchdog log, debug/test bus,
and other holes have no implementation.

The 24xx driver masks cycle monitoring and unmasks watchdog reporting during
initialization. Their mask/clear configuration bits are retained, but this
model has no watchdog or cycle-monitor event producer. No-event status is
not evidence that those physical monitors are healthy. It does not fabricate
their logs or turn missing analog input into a guessed watchdog event.

The model implements only the downstream first-round flag at TRDY bit 3.
It does not claim upstream bit 0 ready/in-progress semantics, sensor min/max
flags, a measured hardware version or any analog register interpretation.

## Sampling and threshold behavior

The reference period is exactly 1,000,000 virtual nanoseconds. This is a model
policy, not a measured TSENS clock or conversion duration. It does not use the
board's 32 kHz sleep clock or the separate 32768 Hz MPM counter.

Enabling a nonempty channel bitmap starts one round. The round captures the
enabled mask and every available input code. Before the first deadline there
is no valid result or first-round-complete flag. A host input change during a
round affects a later capture, never the already pending round.

At the deadline, each captured known channel updates its signed twelve-bit
code and sets VALID. An unknown channel clears VALID and preserves its previous
code as historical data. A channel that has never sampled therefore has code
zero with VALID clear; this is not a valid zero-temperature measurement.
TRDY bit 3 becomes set only when a round completes with known inputs for
every enabled channel. It remains a historical first-round flag until a
control change or reset; it does not guarantee all later inputs remain valid.

Code conversion is exact: `millicelsius = signed12(code) * 100`.
For example, 25000 becomes code 250 and -10000 becomes code `0xf9c`.
The driver can accept a repeated raw code after three invalid reads, so a
successful guest temperature print alone does not prove that sampling or
VALID was correct.

At each completed round, known samples compare against the currently programmed
signed thresholds:

- lower violation when sample is less than or equal to the lower threshold;
- upper violation when sample is greater than or equal to the upper threshold;
- critical violation when sample is greater than or equal to the critical
  threshold.

Violation bits latch independently of interrupt masks. A clear-register one
immediately clears its latch and suppresses relatching while that bit remains
one. Writing zero releases the hold; a later sample may latch again. This
finite held-clear policy supports the driver's mask, clear-one, clear-zero
sequence without inventing unmeasured hardware edge timing.

Threshold and input writes do not immediately reevaluate a sample. The next
sampling deadline evaluates current thresholds against the captured input.
IRQs reflect the resulting latches, per-channel masks, global enables and the
currently enabled channel set. Output 0 combines enabled lower/upper events;
output 1 carries enabled critical events. Both connect to the actual GIC SPIs
listed above.

A late callback commits its one captured round exactly once, records actual
callback time, and schedules the next capture for that time plus one period.
It does not recreate past input changes, invent intermediate samples or run
an unbounded catch-up loop. At the signed virtual-clock limit, an unrepresentable
next deadline leaves the sampler explicitly exhausted; a new control change
requiring such a deadline is rejected atomically.

## Reset and migration

Reference reset values are explicit policy: interrupts disabled, all supported
mask bits set, clear and status latches zero, upper/lower thresholds
`0x7ff800`, and critical thresholds `0x7ff`.
These extrema avoid assuming firmware thermal policy. They are not measured
silicon reset values.

A control enable/bitmap change cancels the pending round, clears VALID/TRDY
and starts a new capture from the current virtual time if enabled. It keeps
historical codes, counters, thresholds and latches; disabled channels cannot
assert an IRQ. Software reset clears volatile state and cancels the timer
while its bit is held. Clearing reset can start a new round.

System reset cancels pending work, clears the same volatile state and restores
the explicitly configured boot mask. External temperature inputs survive
both forms of reset because they describe the environment. The initial
temperature option is used at realization; reset does not overwrite later
host environment changes. Reset enter does not drive outputs; reset hold
restores IRQs and schedules the new reference round.

VMState name `qcom-tsens24xx`, version 1, minimum 1, has one
375-byte payload per controller. Its leading 36 bytes compare immutable
profile, controller identity, SROT/TM bases, sampling period, boot bitmap and
the signed initial-temperature bit pattern. Profile and bootstrap mismatch
are rejected in both directions. The fixed identity also binds the IRQ
routing of this board implementation.

The stream separately preserves:

- live external input values and their known mask;
- control, IRQ enables, masks, held clears and latches;
- all thresholds, last codes, VALID and first-round state;
- pending enable/known masks and the captured codes;
- the original pending deadline, counters and last completion time.

Incoming state validates masks, ranges, signed temperature granularity,
held-clear/latch consistency, pending/capture relationships, counter ordering,
unknown-profile state and canonical inactive captures. Loading stops the
old timer, validates without sampling, drives both restored IRQ outputs, and
rearms the original absolute deadline. No first MMIO read is required to
repair timer or IRQ state.

QTest's private virtual clock is not part of normal timer migration, so the
migration fixture aligns its destination clock to the source before incoming
load. Normal VM virtual-clock migration remains QEMU's responsibility.
Historical binaries lacking this device are outside this compatibility claim.

## Observations and acceptance boundary

Read-only QOM properties expose controller registers, both IRQ levels, sample
and completion counters, the pending deadline/captures, last-sample time and
per-channel sampled state. Observations do not advance time, complete a sample
or clear a latch. Historical temperatures must be interpreted with their VALID
flags. Unknown-profile runtime observations return an explicit error.

The associated compiled-device tests are intended to exercise known and unknown
inputs, all signed channels, deadline boundaries, negative temperatures,
threshold equality, actual IRQ outputs, masking/clear holds, independent
controllers, reset cancellation, sparse/invalid accesses, paused TCG host
inputs, actual migration restoration before device MMIO, profile rejection
and precisely corrupted streams. Test presence and static review are not
execution evidence; the unified CI result and binary receipt provide that
evidence.

Advancing the original TSENS probe would establish this digital reference path
under explicit inputs. It would not establish calibrated die temperature,
thermal physics, fan/cooling performance, PMIC thermal behavior or secure
firmware initialization. The existing SCM TSENS reinitialization service is
not acknowledged or implemented by this device.
