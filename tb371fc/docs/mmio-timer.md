# Architected memory-mapped timer

The model implements the Arm Generic Timer's CNTCTLBase control page and
independent physical/virtual timer frames. It was added after the unmodified
production kernel, with the complete live device tree, faulted on its first
CNTTIDR read at physical address `0x17c20008`.

This is an architectural behavioral model. It does not recreate Qualcomm
counter power domains, oscillator drift, secure firmware policy, undocumented
implementation IDs, or timer-driven wakeup from an unmodeled low-power state.

## Evidence behind the mapping

The [limited diagnostic receipt](../evidence/mmio-timer-origin.json) records the
original Image hash and the source-pinned symbol extractor. The unchanged Image
yielded 121,415 symbols; its recovered text base was checked against the
original `linux_banner` bytes. The first failing PC was
`arch_timer_mem_of_init+0x198`. The preceding instruction adds eight to the
mapped control base, and the faulting instruction is `ldr w12,[x11]`.
The supplied kernel and complete recovered symbol map remain private.

[The live DT inventory](../evidence/expansion-20261123/android-device-inventory.json)
declares the following regions. Frame-number values were also read from the
same saved live DTB; that property was omitted by the original JSON inventory's
property allowlist.

| Frame | CNTBase | CNTEL0Base | Physical SPI | Virtual SPI | DT status |
| --- | --- | --- | --- | --- | --- |
| Control | `0x17c20000` | - | - | - | available |
| 0 | `0x17c21000` | `0x17c22000` | 8 | 6 | available |
| 1 | `0x17c23000` | not declared | 9 | not declared | disabled |
| 2 | `0x17c25000` | not declared | 10 | not declared | disabled |
| 3 | `0x17c27000` | not declared | 11 | not declared | disabled |
| 4 | `0x17c29000` | not declared | 12 | not declared | disabled |
| 5 | `0x17c2b000` | not declared | 13 | not declared | disabled |
| 6 | `0x17c2d000` | not declared | 14 | not declared | disabled |

Each region is 4 KiB. The parent compatible is
`arm,armv7-timer-mem` and the DT frequency is 19,200,000 Hz. SPI numbers
in this table exclude the GIC's 32 private interrupt IDs.

The modeled capability masks advertise virtual and EL0 views only for frame 0.
This is supported by its two interrupts and two register regions in the DT;
it is not a claim that CNTTIDR was read from the real tablet. Capabilities of
the disabled frames have not been measured, so additional virtual or EL0
views are not advertised.

## Device interface and direct-kernel boot contract

The QOM type is `arm-arch-timer-mmio`, with a stable board child at
`/machine/mmio-timer`.

| Property | Generic default | TB371FC setting |
| --- | --- | --- |
| `num-frames` | 1 | 7 |
| `frequency` | 19,200,000 | 19,200,000 |
| `virtual-frame-mask` | 1 | 1 |
| `el0-frame-mask` | 1 | 1 |
| `reset-ns-access` | 0 | 1 |
| `exact-frequency` | false | selected with machine `counter-exact` |

MMIO index 0 is the control page. For frame `n`, indices `1+2*n`
and `2+2*n` are its normal and EL0 pages. Sysbus IRQ indices `2*n`
and `2*n+1` are physical and virtual outputs.

The board's `reset-ns-access=1` is an explicit replacement for the
firmware configuration needed for a direct non-secure kernel boot. It gives
that kernel access to frame 0 and its control registers. It does not claim to
reproduce a measured secure reset value. CNTACR and CNTEL0ACR reset to zero;
Linux then enables the capabilities it needs through CNTACR.

CNTFRQ in the control page and CNTNSAR are secure-only. Non-secure accesses
to a disallowed frame, its CNTACR or its CNTVOFF return zero and ignore writes,
as specified by the architecture. Secure accesses do not bypass CNTACR or
the second-view access controls.

## Implemented behavior

The control page implements CNTFRQ, CNTNSAR, CNTTIDR, each CNTACR and each
implemented virtual frame's CNTVOFF. A frame implements the physical counter,
frequency annotation, physical compare/value/control registers and optional
virtual counterparts. Its CNTVOFF view is read-only; its EL0 view never
exposes the offset or the EL0-access-control register.

CNTACR independently controls physical/virtual counter reads, frequency and
offset reads, and physical/virtual timer access. The optional EL0 page adds
the CNTEL0ACR permissions and cannot grant access denied by CNTACR.
Architected 64-bit registers support atomic 64-bit transfers and split
32-bit transfers. Byte/halfword transfers are rejected. Reserved registers
are RAZ/WI; no measured Qualcomm identification value is invented.

Each timer has its own 64-bit compare value, signed 32-bit TVAL behavior,
enable and interrupt-mask bits. ISTATUS is derived from the unsigned
counter-versus-compare condition and remains set while the interrupt is
masked. Disabling the timer lowers its IRQ; the underlying counter and TVAL
continue to advance. Permission changes affect MMIO access, not a running
timer's enable state.

A frame's virtual count is physical count minus that frame's CNTVOFF, with
64-bit unsigned wraparound. Changing CNTVOFF reschedules the virtual timer.
A future offset may first produce a large wrapped virtual count, then roll
back to zero when the physical count reaches the offset; the model schedules
both the resulting deassertion and the later compare event.

These are independent timer channels. Frame 0's compare, enable and virtual
offset do not alias a CPU's CNTP/CNTV registers or CNTVOFF_EL2. Only the
physical counter timebase is shared.

## Exact frequency and retained legacy mode

`frequency` on the MMIO device and `cntfrq` on each CPU describe
the configured clock input. Guest writes to a CNTFRQ register change the
software annotation only. They do not retime that input.

The upstream QEMU 11.1.3 counter converts this frequency to an integer
nanosecond period. At 19.2 MHz that produces 52 ns and an effective virtual
rate of 19,230,769.230769 ticks/s. The old-mode receipt below measures the
1602.564 ppm discrepancy; reading CNTFRQ as 19.2 MHz does not validate rate.

The explicit CPU property `cntfrq-exact` and MMIO property
`exact-frequency` instead use:

- physical count = floor(virtual nanoseconds * configured frequency / 1e9);
- event deadline = ceil(target physical tick * 1e9 / configured frequency).

Both use [the shared conversion helper](../../include/hw/timer/arm-gt-counter.h).
The conversion uses wide intermediate arithmetic and saturates deadlines
outside QEMUTimer's signed range. Rounding a future deadline up prevents
expiry before the count reaches its compare value or repeated scheduling
in the past.

The machine's `counter-exact` setting controls both sides together.
The generic properties default to false, preserving other machines' existing
timing. For a deliberate comparison, use
`-M tb371fc,counter-exact=off`. Exact mode also converts CPU WFE event
streams and WFxT timeouts into nanoseconds; the compatibility branches retain
their previous behavior.

This is exact relative to QEMU virtual time at the configured nominal
frequency. It is not a CPU cycle-accurate performance model or a measurement
of the real tablet's oscillator drift.

## Reset and migration

Reset clears compare/control/permission/offset state and deasserts all
timer IRQs. It restores the chosen frequency annotation and the explicitly
configured non-secure access mask. It does not rewind the shared counter.

Migration saves every timer's absolute deadline, compare/control state,
permissions, virtual offset and frequency annotation. Post-load recomputes
output levels and reschedules with the restored state. Optional exact-mode
subsections preserve the legacy wire layout when that feature is disabled.
A mismatched mode or configured frequency is rejected, instead of silently
changing time during migration.

The QTest migration fixture uses two actual QEMU processes. QTest's synthetic
clock is externally driven and is not itself migrated, so the destination
test clock is explicitly aligned after incoming state load, before inspecting
timers or stepping to a pending deadline. This fixture does not claim to
measure uninterrupted real-time delivery during migration.

## Validation and reproduction

[The retained legacy receipts](../evidence/mmio-timer-legacy/) cover nine
QTest groups, 1,024 real MRS/MMIO/offset comparisons and four process-to-process
migration groups on the frozen `c5c60a3...` binary. The one-second QTest
sample observed 19,230,769 ticks. The independent legacy CPU icount run
observed 22,685 ticks over its 1,179,650 ns instruction interval and passed
the CPU physical/virtual CVAL, signed TVAL and wrap checks. Tests were run
with the unrelated UFS controller disabled, at 128 MiB, as component checks.

The [exact-mode receipts](../evidence/mmio-timer-exact/) on the frozen
`a70862bfa8aa957ea26e362b5defd9ae60b4cc4bb53f2ac2c70c0b4fe3b67e2a`
binary record ten passing MMIO groups, exactly 19,200,000 ticks in one
virtual second, and 22,650 CPU ticks for the instruction-counted interval
whose rational reference is 22,649 plus endpoint phase. The CPU's physical
and virtual compare, signed-TVAL, masking and wrap checks also pass.
Four MMIO migration groups and both directions of cross-mode migration
rejection pass. The same new binary with exact mode disabled retains the
old measured rate. A separate C arithmetic probe matches an independent
Python Fraction reference for 1,848 boundary cases.

An additional real-time CPU migration probe exposed stale cached ISTATUS:
after the counter passed its compare, the main-loop callback had not yet
updated the value returned by a CPU control-register read. The same ELF
without migration reproduced failure code 8. Exact-mode CTL access now
resamples the live condition and updates the IRQ; exact-mode mask writes
also resample the condition. Legacy behavior remains unchanged.

[The retained before/after evidence](../evidence/mmio-timer-live-status-20261123/)
uses the identical regression ELF
`37e1b51ffc5ab43a34f794aa264fbe5cf5cea015501ff61722538a95e682d35f`
on both binaries. Frozen QEMU
`99dfb4fc3ff472624f0a079c67ba26b99e60935a4afc859d1e66afc8942e8c4c`
passes the strict real-CPU migration and no-migration control, observes
both guest timer expiries, and reaches the payload's real semihost exit 0.
The migration retains compare values, virtual offset and counter continuity;
its checks were not relaxed after the failure.

This frozen binary also passes all ten MMIO groups, the independent CPU
icount rate measurement, four MMIO migration groups and both cross-mode
rejections. Exact and legacy rates remain 19,200,000 and 19,230,769 ticks
per virtual second respectively. A separate payload directly reads the
CPU0 GIC redistributor's pending-input state for INTID 30 and 27. Its 16
line observations pass in real-time exact mode, instruction-counted exact
mode and instruction-counted legacy mode. It checks masked expiry,
unmask, mask, disable, re-enable at an expired compare, and moving a
compare into the future. Each line is checked before a CTL read can
refresh status. This gate observes the timer-to-GIC wires; it does not
claim to execute an interrupt exception handler.

The current acceptance commands add a one-nanosecond deadline boundary,
an exact one-second rate measurement, independent CPU instruction-count
timing, real CPU physical/virtual TVAL/CVAL/control behavior, and conversion
overflow tests. Use a new output directory for each run:

```sh
python3 /src/tb371fc/tests/test_counter_conversion.py \
  --build-config /work/build-next --out /work/timer-check/math
python3 /src/tb371fc/tests/test_mmio_timer.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/timer-check/mmio
python3 /src/tb371fc/tests/test_mmio_timer_cpu.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/timer-check/cpu \
  --icount-rate
python3 /src/tb371fc/tests/test_mmio_timer_migration.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/timer-check/migration
python3 /src/tb371fc/tests/test_cpu_timer_migration.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/timer-check/cpu-migration
python3 /src/tb371fc/tests/test_cpu_timer_migration.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/timer-check/cpu-control \
  --without-migration --elf /work/timer-check/cpu-migration/probe.elf
python3 /src/tb371fc/tests/test_cpu_timer_irq.py \
  --qemu /path/to/qemu-system-aarch64 --out /work/timer-check/cpu-irq
```

The CPU rate payload uses a fixed assembly loop under
`-icount shift=0,align=off,sleep=off`. Its independent time reference is
one virtual nanosecond per executed instruction, not a second read of
CNTFRQ or the MMIO counter. It deliberately changes the CPU's CNTFRQ
annotation during that measurement to ensure the clock input remains fixed.
This controlled test does not compare TCG instruction speed with real silicon.

The production-kernel PID1 result is a separate
[console-independent acceptance gate](guest-acceptance.md). Passing a timer
component gate does not by itself prove full-device-tree Linux or stock
Android boot.

## Primary references

- [Arm ARM DDI 0406C.d, Appendix D5](https://documentation-service.arm.com/static/5f8daeb7f86e16515cdb8c4e):
  the memory-mapped Generic Timer register layout, permissions and behavior.
  Retrieved PDF SHA-256:
  `294668ae6480133b32d85e9567cc77c5eb0e1232decdf42cac7ab480e884f6e0`.
- [Arm CNTFRQ external-register definition](https://support.arm.com/documentation/ddi0595/2020-12/External-Registers/CNTFRQ--Counter-timer-Frequency):
  the software frequency annotation and per-view access controls.
- [Linux v6.12.114 arm_arch_timer.c](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/clocksource/arm_arch_timer.c?h=v6.12.114):
  frame discovery and CNTACR negotiation used by the supplied clean test kernel.
- QEMU's existing `target/arm/helper.c` generic-timer implementation:
  unsigned compare/wrap semantics, offset handling and legacy timing baseline.
