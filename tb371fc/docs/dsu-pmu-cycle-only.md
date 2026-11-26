# TB371FC DSU cycle-only compatibility profile

The machine option `-M tb371fc,dsu-pmu-cycle-only=on` enables a synthetic,
AArch64 DSU PMU interface for the stock kernel's DSU probe. It is off by
default. This option supplies a shared virtual cycle counter; it does not
claim to implement the physical Snapdragon 870 DSU or its cache/bus events.

## Evidence and topology

The original captured DTB, SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`,
contains `/soc/dsu_pmu@0`, compatible `arm,dsu-pmu`, and interrupts
`<0 50 4>`. Its CPU phandles 13 through 20 resolve to all eight
`/cpus/cpu@{0,100,200,300,400,500,600,700}` nodes. Consequently this model
has one shared DSU for all eight CPUs and one level-high SPI 50 (INTID 82).
CPU capacity differences are not evidence of separate DSUs.

The stock run stopped at the MRS of `S3_0_C15_C5_0` in
`dsu_pmu_cpu_online+0x6c` (ELR `ffffff8008d17b1c`), after the RAS probe.
The option addresses that missing interface. Passing its component tests
does not establish Android userspace, six-LUN completeness, or boot
completion. Those require a fresh, separately qualified original-input run.

## Exposed subset

`CLUSTERPMCR_EL1` reads `0x51000040 | E`: synthetic implementer 0x51,
ID zero, zero programmable event counters, and bit 6 RES1. No physical
DSU identification is copied. `CLUSTERPMCEID0_EL1` and
`CLUSTERPMCEID1_EL1` are zero and read-only.

The pinned Linux DSU driver permits N=0. Its common-event probe returns
without reading CEID when N=0, but the CPU-online path still clears overflow
status. Its cycle event allocation treats raw event 0x11 specially and
allocates counter index 31 independently of N. Thus cycles remain
functional through the raw event even though that driver's named common
event attributes are not advertised for this profile. Other raw events
have no counter to allocate.

| Register | Encoding | Behavior |
| --- | --- | --- |
| CLUSTERPMCR_EL1 | S3_0_C15_C5_0 | E stored; C resets cycle count; P has no counters to reset; P/C self-clear |
| CLUSTERPMCNTENSET/CLR_EL1 | S3_0_C15_C5_1/2 | Read shared enable; W1S/W1C of bit 31 only |
| CLUSTERPMOVSSET/CLR_EL1 | S3_0_C15_C5_3/4 | Read shared overflow; W1S/W1C of bit 31 only |
| CLUSTERPMSELR_EL1 | S3_0_C15_C5_5 | Shared selector, bits 4:0 stored |
| CLUSTERPMINTENSET/CLR_EL1 | S3_0_C15_C5_6/7 | Read shared interrupt enable; W1S/W1C of bit 31 only |
| CLUSTERPMCCNTR_EL1 | S3_0_C15_C6_0 | Shared read/write 64-bit cycle counter |
| CLUSTERPMXEVTYPER_EL1 | S3_0_C15_C6_1 | RAZ/WI for every selector, including 31 |
| CLUSTERPMXEVCNTR_EL1 | S3_0_C15_C6_2 | RAZ/WI for every selector, including 31 |
| CLUSTERPMCEID0/1_EL1 | S3_0_C15_C6_4/5 | Read zero; writes are undefined |

All other enable, overflow, and interrupt bits are RES0. P and C do not
clear overflow. Selected event registers never alias the cycle counter.

## Time and interrupts

When E and CNTEN[31] are both set, the counter advances at one tick per
QEMU virtual nanosecond (a fixed virtual 1 GHz), wrapping modulo 2^64.
It counts virtual elapsed time, including guest WFI when the virtual clock
advances, and stops while the VM is paused. It is not a host performance
measurement or an estimate of the silicon frequency. With icount it follows
QEMU's icount-derived virtual clock.

Wrap sets OV[31]. SPI 50 is asserted exactly when the profile is enabled
and OV[31] and INTEN[31] are both set. A virtual timer detects wrap without
requiring a guest register read. Its deadline is based on the same
materialized clock epoch as the counter. Intervals beyond QEMU's signed
64-bit timer horizon are left unscheduled; they cannot wrap before that
horizon. In particular a zero counter is not mistaken for a zero-delay wrap.

Sysreg callbacks use ARM_CP_IO so QEMU serializes their side effects with
the BQL. The cluster state belongs to one SysBusDevice, not to whichever
CPU happened to access it. The device's own VMState owns that state; the
new cluster sysreg definitions use ARM_CP_NO_RAW to avoid duplicate migration.
The pre-existing CPU ACTLR migration slots retain canonical zero raw values;
its architectural access gate is migrated solely in the DSU device state.

## Privilege and direct-kernel handoff

The DSU aliases exist only when the option is enabled. Their static access
rights require EL1 or above, and EL0 access remains undefined. The model
does not add AArch32 DSU register aliases or an EL3-only PMMDCR register.
The board has no EL3.

ACTLR_EL2 bit 12 controls EL1 writes to mutable DSU registers when EL2 is
enabled. Clearing it causes a normal system-register trap to EL2; reads
remain available. The existing ACTLR_EL2/HACTLR encoding is overridden
with a per-CPU bit 12, while every unrelated bit retains its existing
RAZ/WI behavior. ACTLR_EL1 and HACTLR2 are unchanged.

For this direct-kernel machine, reset/handoff grants that bit on every
CPU. This is an explicit firmware-handoff policy, not a claim about the
silicon reset value. A secondary CPU reset restores only its own access
gate and never resets the shared PMU. A system/device reset clears the
shared counter, control, selector, enable, overflow, and interrupt state
and lowers the IRQ.

## Migration and observation

The device exists in both configurations. Profile ID is zero when disabled
and 0x44535501 when enabled. Migration checks the profile ID before mutable
state; an enabled/disabled mismatch fails rather than silently losing or
inventing a PMU. The machine option is fixed once CPUs are created.

Pre-save materializes the cycle counter and updates the IRQ. Post-load
validates masks, restores the per-CPU ACTLR gates and shared state, rebases
the virtual clock epoch, then recomputes the timer and IRQ. Migration
downtime does not add cycles. Off-profile state must remain all zero.
Timer and clock-baseline fields are derived rather than copied as host
deadlines. This new device stream does not promise compatibility with
older QEMU binaries that have no DSU migration section.

The child `/machine/dsu-pmu` exposes read-only QOM properties
`profile-id`, `frequency-hz`, `cycle-count`, `control`,
`counter-enable`, `interrupt-enable`, and `overflow`.
These are observations of the last materialized state; a QOM query never
advances the counter or changes timers/interrupts. In particular, use a
guest register read when a live count sample is needed.

The original DTB is not edited. The board's generated minimal DT does not
gain a DSU node as part of this stock-DTB compatibility option.

## References

- [Arm DSU Technical Reference Manual, r4p1](https://documentation-service.arm.com/static/5e7e1bd8b2608e4d7f0a35b4):
  cluster PMU register access, masks, selector and overflow/reset behavior.
  The real r4p1 implementation has programmable counters; this profile is
  an explicit reduced virtual interface.
- [Pinned Linux arm_dsu_pmu.c](https://code.googlesource.com/linux/torvalds/linux/+/dfc53baae3c6a165a35735b789e3e083786271d6/drivers/perf/arm_dsu_pmu.c):
  zero-counter probe and special cycle-counter allocation.
- [QEMU multi-threaded TCG](https://www.qemu.org/docs/master/devel/multi-thread-tcg.html):
  serialized IO side effects.
- [QEMU icount](https://www.qemu.org/docs/master/devel/tcg-icount.html):
  the virtual clock under instruction counting.
