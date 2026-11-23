# MPM2 sleep counter digital reference

The always-present `qcom-mpm2-sleep-counter` device at
`/machine/mpm2-sleep-counter` implements one read-only 32-bit
counter. It advances from QEMU virtual time at the original DT's declared
32,768 Hz and wraps modulo 2^32. It is independent of the existing GCC
`sleep` input, which is 32,000 Hz on this board.

This is a bounded digital counter model. Its initial epoch, persistence
across QEMU system reset and behavior during paused emulation are explicit
reference policies, not measured tablet retention or oscillator behavior.
The model supplies no MPM interrupt controller, wakeup routing, suspend
firmware, counter controls or bootloader execution.

## Evidence and physical mapping

The pinned official source is
[OnePlusOSS boot_stats.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/boot_stats.c),
commit `94304e12a3509eac7b3716bf574ce0b1fcaacbae`.
Its byte SHA256 is
`4fd7113b1755f4dcaac55c688abba97d00f15fa000b1d88b5b187946f6925ff6`
and Git blob is `1d59ae9812cc29681fe59f7837c82f3f30094aa6`.

That driver finds `qcom,mpm2-sleep-counter`, reads the DT
`clock-frequency` property for logging, maps resource zero and
performs only `readl_relaxed(mpm_counter_base)`. It prints the result
as an unsigned 32-bit kernel timestamp. It specifies no writes, reset
register, enable sequence, extra words or interrupt.

The supplied original TB371FC DT was independently read as:

| Item | Value |
| --- | --- |
| Node | `/soc/qcom,mpm2-sleep-counter@c221000` |
| Compatible | `qcom,mpm2-sleep-counter` |
| Resource | `0x0c221000`, size `0x1000` |
| Clock-frequency | `0x8000` = 32,768 Hz |

The native failure motivating this model occurred in boot_stats_init,
after all four IMEM bootloader KPI words had been printed. The faulting
instruction was a 32-bit load through the mapped counter pointer.
At that stage the fault's physical address was inferred from the fixed
driver and original DT; it was not independently recovered by translating
the faulting virtual address. A subsequent native run must establish
whether this model resolves that access.

The board maps only `0x0c221000..0x0c221003`, using the device's
4-byte memory region. The remainder of the DT's 4 KiB resource is left
unmapped. The model does not turn that range into RAM or provide values for
undocumented words. The four IMEM bootloader KPI words are separate data;
this change does not manufacture their timestamps.

## MMIO and observations

| Interface | Behavior |
| --- | --- |
| Aligned 32-bit read at offset 0 | Current low 32 bits of the counter |
| Write at offset 0 | Transaction error; no counter or epoch change |
| Subword or unaligned access | Memory-core transaction error |
| Offset 4 and beyond | Unmapped; no additional register semantics |
| QOM `counter` | Read-only uint32 observation of the same count |
| QOM `clock-frequency` | Fixed configuration, 32768 |

The `clock-frequency` uint32 property defaults to 32768. Realization
rejects every other frequency; configuration cannot be changed after
realization. This preserves the source-bound scope and makes an erroneous
32,000 Hz override fail explicitly. There is no guest-writable count,
counter-setting QOM property, frequency injection fixture, IRQ or timer
callback.

Reading MMIO or QOM samples time without changing the count's epoch or
fractional phase. A rejected write cannot restart the counter.

## Exact arithmetic and clock-domain policy

A cold device realization initializes count zero at its current
`QEMU_CLOCK_VIRTUAL` instant. For uninterrupted execution:

```text
count = floor(elapsed_virtual_ns * 32768 / 1,000,000,000) modulo 2^32
```

The implementation reuses the existing
`arm_gt_ns_to_ticks(..., exact=true)` wide-integer conversion helper.
Sharing that arithmetic does not share the Arm architectural counter,
its epoch, CNTFRQ, timer interrupts or frequency controls.

The first complete tick is at 30,518 ns, not 30,517 ns. At one second the
count is exactly 32,768. The 32-bit count wraps after exactly 131,072
seconds. Large virtual advances use the helper's overflow-safe
`muldiv64` path instead of multiplying nanoseconds in 64 bits.

When preserving a checkpoint, state consists of a uint32 count and a
fractional numerator with denominator 1,000,000,000. For a subsequent
elapsed interval, the helper computes complete ticks and the remainder is:

```text
r = ((elapsed_ns % 1,000,000,000) * 32768) % 1,000,000,000
r += saved_fraction
count = (saved_count + complete_ticks + r / 1,000,000,000) modulo 2^32
fraction = r % 1,000,000,000
```

The remainder product is less than 1e9 * 32768 and fits uint64. Its sum
with a saved fraction is less than 2e9. Thus the calculation retains
subtick phase across irregular sampling, uint32 wrap and migration.

QEMU system reset leaves this reference counting domain continuous,
including its fractional phase. No reset register is supported by the
source. This persistence is an explicit modeling choice; cold realization
still starts at zero. The device remains a SysBus child in the machine
tree, with no reset callback that restarts its state.

Only virtual time advances the count. Host delays while the VM is paused,
or while migration is copying state, add no ticks. QTest's explicit virtual
time controls can advance the domain directly even while CPUs are stopped;
that is a test operation, not a wall-clock source.

## Migration contract

VMState name is `qcom-mpm2-sleep-counter`, version 1, minimum 1.
The field order is:

1. Fixed uint32 frequency, checked for equality with the destination.
2. Current uint32 counter value.
3. Fractional tick numerator, required to be less than 1,000,000,000.

pre_save samples the source at one virtual instant and updates the saved
count, fraction and local epoch together. If the source later resumes,
this sampling introduces no discontinuity.

The absolute epoch is not serialized. post_load validates the fraction
and anchors the saved phase at the destination's current virtual instant.
Normal CPU clock migration is registered before machine devices; QTest's
test clock origin is not itself migrated. Rebasing therefore preserves
count and subtick phase even when a QTest destination deliberately starts
at a different absolute virtual time.

The destination must observe the saved count immediately and reach the
next tick after the exact remaining interval. It does not restart a
complete tick, add host migration downtime, require source/destination
QTest origins to match, or import the source's absolute epoch.

Frequency equality protects this fixed configuration and rejects damaged
or incompatible streams. There is no supported alternate-frequency
profile; a test that changes a serialized frequency must be described as
a modified-stream rejection, not a migration between two valid profiles.

## Verification requirements

The compiled-device test records the QEMU and script hashes, actual MMIO
transactions and migration status. Its checks cover exact fractional
boundaries, irregular intervals, large virtual advances, wrap, rejected
writes and widths, sparse mapping, reset continuity, fixed-frequency
validation and pause behavior.

Migration checks use actual source streams, intentionally different
destination virtual origins, RAM witnesses and pure QOM observation before
the first device MMIO. They verify both preserved count and the remaining
fractional interval. A successful unit test or kernel timestamp print
does not establish MPM wakeup, suspend firmware or Android boot.
