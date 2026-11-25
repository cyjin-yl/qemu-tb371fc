# Exact 66d9e20 RPM artifact: original-input native diagnostic

The qualified RPM statistics offset binary progressed beyond the previous
invalid access at `0x0c3f0004` and stopped at the unmapped DDR statistics
offset, a four-byte read at physical `0x0c3f001c` in
`ddr_stats_probe+0x88/0x180`. **The Android boot goal remains unachieved.**
This was one guarded, zero-LUN diagnostic with the original direct-kernel
inputs, not an execution of the OEM PBL/XBL/ABL chain.

## Exact attempt and result

| Item | Recorded identity or result |
|---|---|
| QEMU source | `66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b` |
| Source tree | `7c13a3b85075e0eaefcc14d4f55c160bed699a08` |
| Qualified push workflow | [45533031476](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45533031476) |
| Artifact | `14986446678` |
| Actual ZIP | 56,393,004 bytes; SHA256 `809199bd6f39181dfe9340a60bf58fde4544b536d752736f0f475fbc89e46ec4` |
| Actual QEMU ELF | 63,700,664 bytes; SHA256 `019c56322a1dfd64a5db9d82719b93d49f7e6c7a5891e708f6f9f4c535aa703e` |
| Attempt marker UTC | `2026-11-25T03:04:14.952610+00:00` |
| Native invocation UTC | `2026-11-25T03:04:17.734319+00:00` to `2026-11-25T03:04:26.195739+00:00` |
| Runner result start / elapsed | `2026-11-25T03:04:18.330488+00:00` / 6.592 seconds |
| Host job completion UTC | `2026-11-25T03:04:27.158882+00:00` |
| Outcome | `kernel-failure`; runner exit 2; host exit 2; QEMU exit 0 |
| Original LUNs | 0 attached; `storage_complete=false`, `snapshot_consistent=false` |
| Android acceptance | `stock_android_boot_passed=false`; userspace, interactive display and boot completion not observed |

The [CI qualification](../qualification/rpmstats-66d9e20-qualification.json)
was completed before this attempt. Its SHA256 is
`4272efca70e43f31de1ed0ef9db20e3478d8157b64fbb44f84fa853dabf576d6`.
The original 59-file CI snapshot remains unchanged at
[`d103750ca2a191559daeb9eff4341b8c80013dc6`](https://github.com/cyjin-yl/qemu-tb371fc/tree/d103750ca2a191559daeb9eff4341b8c80013dc6/tb371fc/evidence/rpm-stats-offset-66d9e20-ci-20261125-6a5).
Its native-false fields describe that earlier qualification stage; this
addendum records the subsequent execution separately.

## Input preservation and review scope

[observation.json](observation.json) contains the selected public result.
[independent-review.json](independent-review.json) is the exact completed
review: **20,263 bytes**, SHA256
`18c4cfb5554c3286531f6aa3a5694e92ff36896471fac63e7002c02b3de0a366`.
The offline reviewer verified all 20 retained files, totaling 851,906 bytes,
against the collected payload. Publication preparation independently
rehashed those same 20 local files against the review and collection manifest.
Review success establishes evidence consistency, not Android boot success.

The actual QEMU `-append` value retains the original **1,392 bytes** and
SHA256 `63c5738bb0c67419dd7efa0c585156499d163479d01b421203c0b51756fc6732`.
The original kernel, initramfs, full DT and bootargs retain their planned
identities and the recorded runner/coordinator post-run digests. The prepared
partial AOP reference input and the printk symbols also remain unchanged.
There was no test-init injection, reduced DT or bootargs modification.
The original input files were not copied to the offline reviewer: these
preservation conclusions use the exact plan, emitted command and separately
recorded post-run digest evidence. Runtime ELF and runner pins are retained
collector observations, not fresh remote queries by this review.

All 30 global-property keys are unique and agree across the actual records.
The prior 29 remain in the same order; the sole addition is
`qcom-sm8250-rpm-stats-offset.enabled-reference=on`. The machine remains the
same as in the preceding SPSS diagnostic. The lane records one native
attempt and disables automatic retry. No guest rerun or CI rerun was used
to prepare this addendum.

## Exact DDR fault and the two diagnostic lines

The first recorded fatal kernel failure occurs at guest **3.218093 seconds**;
panic follows at **3.220608 seconds**. The recorded instruction is
`b9400018` (`LDR W24, [X0]`), with PC `ddr_stats_probe+0x88/0x180`,
LR `ddr_stats_probe+0x84/0x180`, and virtual PC/ELR
`0xffffff80086b2430`. CPU 6 records ESR `0x96000010` and
X0/FAR `0xffffff800b18d01c`.

The QEMU log contains two adjacent four-byte rejection diagnostics:

| Diagnostic address | Source-based interpretation |
|---|---|
| `0x0c3f001c` | Inner validation at the complete physical address |
| `0x1c` | Outer validation at the subpage-relative offset |

The [nested-dispatch review](dispatch-subpage-review.json), **5,072 bytes**,
SHA256 `02d9906328f360be695b6558ce7b560d5b7d7a14a84c0b71f3b5b522bb5f0f76`,
binds this explanation to `system/memory.c` and `system/physmem.c` at the
exact qualified commit. Its source-based explanation is that mapping the
four-byte RPM offset register introduces a subpage wrapper. The inner
validation adds page base `0x0c3f0000` to offset `0x1c`, rejects the
unassigned address, then propagates the rejection to the outer validation,
which emits the relative-offset diagnostic. The pair immediately precedes
one matching DDR Data Abort. It does not establish two guest instructions
or a separate physical read at address `0x1c`.

**The complete QEMU log contains two Data Abort records overall.** The
earlier, different CPU 4 record is at log line 3280, with ESR `0x96000005`,
FAR `0x0`, and ELR `0xffffff800818e10c`; an exception return follows it.
The DDR-associated CPU 6 record is at line 18984. The count of one applies
only to that matching DDR frontier record, not to the entire run.

The earlier `0x0c3f0004` invalid access is absent. There is no explicit
RPM probe-success or statistics-payload-read result in this evidence.
Successful DDR offset reads, DDR payload availability and firmware producer
state are not established.

## QMP cleanup: reset, errno 104

The 22 retained QMP messages show:
`RESUME → WATCHDOG(reset) → guest SHUTDOWN(guest-reset) → STOP →
host SHUTDOWN(host-qmp-quit)`.
The final status is `shutdown, running=false`. Quit request 16 has its
unique empty-object acknowledgement, and the saved quit tail matches the
message stream.

Drain ended with **connection reset, errno 104**, and **zero residual bytes**.
It did not end with EOF. The unchanged runner accepts this reset only with
the recorded stopped guest-reset provenance, fresh host-quit event, complete
tail, unique acknowledgement, no prior command error and QEMU exit 0.
No forced host termination occurred. Accordingly `qmp_shutdown_normal=true`
coexists with `kernel-failure` and `stock_android_boot_passed=false`.

## Retained review corrections and publication boundary

The independent review retains two preliminary reviewer failures: an initial
use of the prior collection's `data_b64` key instead of the actual
`data_base64` key, and an overly broad assumption that the whole QEMU log
contained only one Data Abort. The corrected review verifies all payload
bytes and preserves both abort records. Neither correction changed the
native execution, original inputs or retained evidence, and neither caused
another native attempt.

Only this README, the bounded observation, the exact independent review and
the exact dispatch review form this additive native publication. Full private
printk/QEMU logs, complete commands/bootargs, original firmware and host
control/configuration remain private. Their hashes and logical record names
in the review identify retained evidence; they are not public download links.
The earlier CI snapshot and its manifest remain an immutable historical
snapshot. Original storage completeness and the Android boot goal remain
unachieved.
