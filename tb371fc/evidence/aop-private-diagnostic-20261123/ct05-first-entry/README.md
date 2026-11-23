# Actual bounded AOP entry diagnostics after CT attempt 05

This record describes **three completed diagnostic profiles, not AOP startup**.
They actually ran in CT102, Docker `qemu-tb371fc-dev`, from
2026-11-23 15:12:32.984736 UTC to 15:12:36.851854 UTC (3.867062 seconds).
The private supervisor and each profile exited 0, meaning the bounded
diagnostic completed and produced a stop. All owned process groups were
absent after cleanup, and the fixed inputs were unchanged.

## Prerequisites and identities

[CT attempt 05](../../aop-reference-ci-20261123/ct-attempt-05/README.md)
independently built exact source commit
`fe89e5a553fe038babe07abb279a0975d4306715` and passed all eleven XML
regressions and fourteen synthetic CPU preflight checks. The supervisor
checks those original verdicts and their pinned hashes before private input
is read. The private operation then uses the same binary and runner.

| Input | SHA-256 |
| --- | --- |
| CT QEMU | `c6bed2c465e0b771d6e40d60225b9f89636f6d213bf78b3c174510c24f3f4493` |
| Runner | `18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b` |
| CPU preflight result | `8d7c4231f15a0d7a68a26ae969a06fe536f3026c313a0023b1565948a84bb5a1` |
| Captured private AOP input, 524288 bytes | `3dbb0746d21100d124d76ee19b096d122d58c4aa534dec5828005835535b6671` |

## Actual stops

| Explicit profile | Single-step attempts / completed | First stop |
| --- | --- | --- |
| `elf-physical-entry` | 2 / 2 | Host executable-range guard at PC `0x00001cf8`, before another CPU step. The corresponding ELF physical location is `0x0b001cf8`; this profile does not enable that low alias. This is not labeled a hardware exception. |
| `local-alias-vector` | 3 / 2 | Actual write hit `0x000e7fe0`, PC `0x00001cf8`, before the bus callback. The captured writable segment ends at `0x000e604c`. |
| `local-alias-dtcm32k-reference` | 17 / 16 | Actual read hit `0xe000e004`, PC `0x00001f30`, before the bus callback. The stack fits only after this profile's explicit DTCM extension. |

The safe result preserves the **actual hit address separately from the RSP
watchpoint interval base**. For the write, the interval base is
`0x000e604c`, raw hit flags 151. For the read, the interval base is
`0x8081c7b0`, raw hit flags 87. The actual hit is independently matched to
one marked QOM watchpoint entry, with its read/write/stop-before-access
flags. Each stopped PC equals the last attempted instruction PC for the
two memory-access stops.

`0xe000e004` is the architectural ICTR address; see the
[Arm Cortex-M3 TRM](https://documentation-service.arm.com/static/5f2286f2f3ce30357bc28b2a)
and the fixed QEMU [NVIC implementation](https://github.com/cyjin-yl/qemu-tb371fc/blob/fe89e5a553fe038babe07abb279a0975d4306715/hw/intc/armv7m_nvic.c).
This identifies the next modeled access to investigate. It does not prove
the tablet's AOP CPU type, NVIC capacity, clock or reset mapping. The
[public loader-source analysis](../../aop-loading-public-20261123/README.md)
also remains a cross-platform reference rather than native reset attestation.

## What remained enforced

The profiles retain Cortex-M3 and 1 MHz as explicit reference choices.
Each has a 4096-step maximum and a ten-second deadline; the supervisor has
a 45-second global deadline. No host hook replaces a firmware function.
Successful peripheral responses remained zero, unsupported bus callback
counts were unchanged, and invalid backing padding was unchanged.

The original three profiles are not relaxed by this observation. Any later
SCS/NVIC continuation requires a separate explicit configuration and its
own actual architecture controls. Full AOP startup, native hardware reset,
Android boot and full tablet equivalence remain false or unproved.
Ordinary TCG watchpoint results also do not establish before-access
coverage of internal exception stacking/vector fetches.

## Public and private records

[safe-stop-projection.json](safe-stop-projection.json) is the exact reviewed
output of a later read-only projection, observed at 15:18:15.829677 UTC.
It is **derived metadata**, not the original private diagnostic JSON.
[projection-metadata.json](projection-metadata.json) and
[manifest.json](manifest.json) bind the projector, original private-result
hashes, fixed source and execution context.

The original private results, firmware, opcode bytes, disassembly, general
register state and RAM snapshots stay in the CT. This directory contains
only reviewed selected numeric metadata, hashes, and the public supervisor
and projection source. All prior failures and private run outputs were
retained.
