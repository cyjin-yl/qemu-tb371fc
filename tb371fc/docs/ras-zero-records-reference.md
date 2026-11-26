# TB371FC virtual zero-record RAS compatibility

## Purpose and limits

This is an explicit virtual compatibility policy for the fixed TB371FC A55/A77
CPU topology. It retains `ERRIDR_EL1.NUM == 0` and the existing RAS feature
advertisement. It supplies no physical error records, CPU/DSU RAS hardware,
error injection, fault addresses, error payload, error interrupt, or assurance
that Android starts successfully.

The generic QEMU zero-record implementation deliberately leaves the error
record selectors and registers undefined. That is a permitted architectural
choice. This option chooses the permitted RES0/RAZ/WI behavior for the seven
AArch64 registers below. It does not identify a generic QEMU architecture bug.
Physical Cortex-A77 behavior must not be inferred from this virtual policy:
the A77 TRM describes core/DSU error records, including NUM=2 when L3 is present.

## Explicit and immutable configuration

The machine option is `-M tb371fc,ras-zero-records-compat=on`. Its default is
`off`. The board sets the CPU property `tb371fc-ras-zero-records-compat`
consistently on all eight CPUs before realization. CPU0–3 remain Cortex-A55;
CPU4–7 remain Cortex-A77. No CPU identity, cache geometry, PMU, timer, clock,
GIC, native boot runner, original kernel, DT, or boot arguments are changed.

The CPU option is admitted only for the actual `tb371fc-machine` type, an exact
A55/A77 CPU type, AArch64 RASv1, EL2 enabled and EL3 disabled, and TCG software
execution or qtest device observation. Enabling a CPU property without the
machine option is rejected. Other boards and accelerators cannot opt in via a
CPU global. The machine option cannot change after the first CPU is created;
the qdev boolean cannot change after CPU realization.

Pure QOM observation on each `/machine/cpu0` through `/machine/cpu7` exposes:

| Property | QOM type | Off | On |
|---|---|---:|---:|
| `tb371fc-ras-zero-records-compat` | `bool` | false | true |
| `tb371fc-ras-zero-records-profile` | `uint32` | 0 | 1 |

The profile field is read-only and is set from the admitted option at realize.
Its getter performs no guest register access. CPU reset does not change this
configuration, and none of the added registers has mutable selector or record
state.

## Exact AArch64 register contract

All entries have `op0=3`, `op1=0`, `CRn=5`. The seven added definitions are exact
encodings, have constant value zero, and use no wildcard or register override.

| Register | Full encoding | EL1 and higher permissions | Virtual behavior |
|---|---|---|---|
| ERRSELR_EL1 | S3_0_C5_C3_1 | read/write | RES0; writes ignored, including 0, 1 and out-of-range values |
| ERXFR_EL1 | S3_0_C5_C4_0 | read only | RAZ; writes remain undefined |
| ERXCTLR_EL1 | S3_0_C5_C4_1 | read/write | RAZ/WI |
| ERXSTATUS_EL1 | S3_0_C5_C4_2 | read/write | RAZ/WI; no error-valid or overflow state |
| ERXADDR_EL1 | S3_0_C5_C4_3 | read/write | RAZ/WI; no captured address |
| ERXMISC0_EL1 | S3_0_C5_C5_0 | read/write | RAZ/WI; no payload |
| ERXMISC1_EL1 | S3_0_C5_C5_1 | read/write | RAZ/WI; no payload |

The existing read-only ERRIDR_EL1, S3_0_C5_C3_0, remains zero and cannot be
written. Existing DISR/VDISR/VSESR definitions are unchanged. This option does
not add AArch32 aliases, ERXMISC2/3, RASv1.1 fault-generation registers, or any
other encoding. Unregistered encodings retain their original undefined path.

The normal translation path checks EL permissions first, then the existing
`access_terr` and fine-grained trap machinery, before the constant result or
ignored write. EL0 is not granted access. HCR_EL2.TERR and SCR_EL3.TERR keep
their existing exception ordering. Each new entry has its proper FGT mapping;
MISC0 and MISC1 share ERXMISCN. The qualified A55/A77 profile advertises no FGT
and disables EL3, so this does not claim new FGT or EL3 runtime coverage.

## Migration and reset

The existing CPU VMState base version 22, field order and generic CPreg
migration helpers are unchanged. A CPU with this option adds one optional
subsection, `cpu/tb371fc-ras-zero-records-compat`, version 1/minimum version 1.
Its entire payload is one big-endian uint32 equal to immutable profile 1.
Disabled CPUs do not emit it, preserving the previous disabled wire contract.

Before loading, a separate nonserialized `received` flag is reset to false.
The subsection validates the profile with `VMSTATE_UINT32_EQUAL`; its load
callback requires the option to be enabled before setting `received=true`.
At CPU post-load, after the existing counter-mode guard and before merging or
applying CPreg values, configured mode must equal subsection presence.
Consequently on→off, off→on, a missing on-profile subsection, and a changed
profile value are rejected. On→on and off→off are the intended compatible
pairs. Older consumers that do not know the new subsection cannot silently
accept the new mode.

This is a CPU-local mode check. Ordinary CPU fields and unrelated devices or
RAM may already have been read by QEMU's migration core. It does not promise
a whole-machine transaction or claim that guest memory was not loaded.

All seven constants remain in the normal CPreg migration list. No `NO_RAW`,
alias, tolerance or custom raw-write bypass is introduced. The original
write/readback validator rejects nonzero incoming values for those constants.
Existing generic handling of missing or unrelated CPreg indexes is not
expanded or advertised as a new strict stream parser. Complete emitted arrays
and all seven zero values must be checked independently in compiled tests.

## Consumer evidence and validation boundary

The original kernel's complete 904-byte `kryo_cpu_erp_probe` was read from the
already pinned kernel asset, not from physical system registers. Its first
failing instruction at function offset 0x30 writes zero to ERRSELR_EL1. The
same function later writes selector 1, ERXCTLR_EL1=0x10f and
ERXMISC0_EL1=0x7f7f00000000, and calls CPU callbacks. This establishes why
handling only the first selector write is insufficient.

The bounded original consumer review now also includes all six callback bodies
(296 bytes) and both directly called error checkers (584 bytes). The combined
nine-function coverage is **1,784 complete bytes, 446 decoded four-byte
instruction slots and 45 direct BL sites**. Each selected function has one
same-name text-symbol candidate in the pinned complete symbols. Exact starts,
next-symbol extents, full slice hashes and semantic evidence are recorded in
[the original consumer evidence](../evidence/ras-original-consumer-20261126-5297812/README.md).
Instruction-slot counts include padding and are not executed-instruction counts.

`initialize_registers` writes control and miscellaneous values for the current
selector without selecting a record. The CPU-PM callback, on numeric action 2,
initializes selectors 0 and 1 before calling the L3 and L1/L2 checkers. The
probe's possible-CPU loop and later `on_each_cpu` call are present in the code;
mask values, actual callback execution and online-CPU results were not observed.
No CPU-family restriction is inferred from these initialization paths.

The L3 checker selects 1 and reads both status and miscellaneous data before
testing status bit 30. Zero status reaches unlock and return. The L1/L2 checker
selects 0 and reads status and TPIDR; zero status returns before reading
miscellaneous data or MIDR. Neither zero-status path reaches diagnostics,
`dump_err_reg`, or a status write. Zero miscellaneous data alone is insufficient
for the L1/L2 valid-error branch, so the clean-path conclusion depends on
`ERXSTATUS_EL1 == 0`, independently of the zero miscellaneous value.

On conditional nonzero error paths, the reviewed instructions write the
captured original status back: L3 at `+0xd4` through x21 and L1/L2 at `+0x160`
through x20. This is local dataflow under the calling convention, not a physical
clear-semantics measurement. The bodies of `dump_err_reg`, locks, diagnostic
output and panic helpers, and the nonzero-miscellaneous jump table were not
reviewed; they are outside the zero-status branch closure. The original native
run stopped at the earlier selector write. This static review does not claim
that those later guest paths or the new virtual policy have executed successfully.

The independent compiled gate is required to exercise all eight actual CPUs,
default/explicit-off behavior, opt-in zero reads and ignored writes, read-only
and EL restrictions, the original selector/control/misc sequence, reset and
both migration directions, complete constant values, malformed profile and
constant-state rejection, and actual subprocess cleanup. Existing gates and
native runners are retained. Source review alone is not compiled execution,
and no future artifact or native success is implied by this document.

## Primary references

- [Arm RAS Extension DDI0587B, section 4.4 and pp. 41–49](https://documentation-service.arm.com/static/5f8db521f86e16515cdba662?token=): zero-record selector/error-register choices and access restrictions.
- [Cortex-A77 TRM 101111_0101_05_en, ERRIDR/ERRSELR, pp. 172–175](https://documentation-service.arm.com/static/64ac62b4df6cd61d528c60a0): physical core/DSU record descriptions, distinct from this virtual policy.
- [Exact predecessor QEMU helper.c](https://github.com/cyjin-yl/qemu-tb371fc/blob/5297812e6af5f5a6d88372debb12c5841142757c/target/arm/helper.c): minimal_ras_reginfo, access_terr, constant raw validation and registration.
- [Exact predecessor CPU migration](https://github.com/cyjin-yl/qemu-tb371fc/blob/5297812e6af5f5a6d88372debb12c5841142757c/target/arm/machine.c): CPU v22, CPreg arrays and the existing optional counter-mode subsection pattern.
