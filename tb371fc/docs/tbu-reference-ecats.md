<!--
SPDX-License-Identifier: GPL-2.0-or-later
-->
# Opt-in ANOC TBU debug contract

`-machine tb371fc,tbu-profile=reference-ecats` adds a deliberately limited
ANOC1/ANOC2 HALT and ECATS adapter around the existing architectural SMMUv2
model. The default is `tbu-profile=off`; the generated device tree continues to
describe `arm,smmu-v2`, and does not advertise a native Qualcomm QSMMU identity.

## Evidence and reference choices

The safe live device-tree capture supplies the addresses and SID intervals in
the table. It did not read TCU/TBU MMIO or observe a UFS DMA transaction. The
register protocol follows the official OnePlus SM8250 kernel's
[`drivers/iommu/arm-smmu.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/iommu/arm-smmu.c),
specifically `qsmmuv500_tbu_halt`, `qsmmuv500_iova_to_phys` and the adjacent
register definitions. UFS SIDs `0xe0`/`0x4e0` come from the
[`v6.12.114` upstream SM8250 DTS](https://github.com/gregkh/linux/blob/v6.12.114/arch/arm64/boot/dts/qcom/sm8250.dtsi).
Their membership in the two captured intervals is an arithmetic inference,
not a native transaction trace.

| Resource | Address / size | Supported reference behavior |
| --- | --- | --- |
| TCU | `0x15182000 / 0x20` | Read-only version at `+0x18` |
| ANOC1 debug | `0x15185000 / 0x1000` | SID interval `0x000..0x3ff` |
| ANOC1 ECATS arbitration | `0x15182200 / 8` | Read-to-acquire, write-zero-to-release |
| ANOC2 debug | `0x15189000 / 0x1000` | SID interval `0x400..0x7ff` |
| ANOC2 ECATS arbitration | `0x15182208 / 8` | Read-to-acquire, write-zero-to-release |

The default TCU version `0x02000000` and operation delay of `1000 ns` are
explicit **reference values**, not hardware measurements. They can be set with
`-global qcom-sm8250-tbu-debug.reference-version=...` (major version 2 only) and
`-global qcom-sm8250-tbu-debug.operation-latency-ns=...` (1 through 1,000,000,000).
Only the vendor driver's nonsecure, data, cacheable translation request
encoding is supported. No clock-gate register is invented: the captured TBU
nodes do not themselves supply a clock gate register contract.

## HALT, ACK and translation

Writing HALT bit 16 in a bank's `+0x00` register immediately closes ordinary
emulated DMA admission for that bank's complete 0x400-SID interval. HALT ACK
appears in bit 1 of `+0x20` after the configured virtual-clock delay. ACK means
the **reference admission gate is closed**. A gated DMA access returns a
transaction failure. There is no queue, silicon transaction stall, in-flight
drain, or timed power transition.

After ACK, a 32-bit read of the bank's arbitration resource returns one and
acquires its reference ECATS lock; later reads return zero until released.
Set the lower ten SID bits in `+0x00`, the 36-bit VA at `+0x08`, and CDMID 255
at bits 43:36 of `+0x30`. The trigger at `+0x18` accepts `0xfd` (privileged
read), `0xff` (privileged write), `0xbd` (user read) or `0xbf` (user write).
It requires a held ACK and arbitration lock. Unsupported access encodings,
reserved bits, unaligned accesses and unsupported widths fail without starting
a query. Debug fields accept aligned 32-bit halves or 64-bit accesses; the
TCU version and arbitration resources accept aligned 32-bit accesses only.

While a query is pending, bit 0 of `+0x20` is set and PAR is faulted. Completion
uses the existing SMMUv2 stream matching, context configuration and stage-1
page-table walker, including access permissions and the existing fault
register behavior. Disabled contexts, unmatched/ambiguous streams, bypass,
unsupported configurations and faults do not fabricate a translated address.
A successful 36-bit physical address is returned as `PA << 12` at `+0x28`,
matching the vendor driver's `(PAR >> 12) & ((1ULL << 36) - 1)` extraction;
failure sets PAR bit zero. This is ECATS PAR encoding, not architectural
SMMUv2 CB_PAR encoding.

Clear the trigger, release the arbitration lock with a 32-bit zero write, and
clear HALT to reopen DMA. Clearing the trigger cancels an unfinished query;
clearing HALT cancels all pending work and clears ACK, arbitration and PAR.
Reset does the same. A query bypasses only its own debug admission gate; it
does not bypass SMMU mappings, privilege controls or page-table permissions.

## Checkpoints and scope

Both machine profiles instantiate a migration section, while `off` maps no
new guest windows. New-format streams require identical profile, reference
version and operation latency. Migration validates register encodings and
reachable HALT/query/ACK combinations before restoring admission gates. It
saves each pending operation's remaining duration and rebases the timer on the
receiver. The SMMU architectural registers remain owned by the existing SMMU
VMState. No old-snapshot-to-reference-profile compatibility is promised.

This adapter does not model all native TBUs, undocumented TCU registers,
performance counters, secure firmware, physical clocks, power collapse,
hardware ECATS retries, native interconnect arbitration or the complete DMA
topology. Its result is a functional debug contract for the stated reference
profile. It is not evidence that the original Android QSMMU driver can bind or
that vendor hardware and QEMU are behaviorally equivalent.

## Targeted verification

Run `tb371fc/tests/test_tbu_sm8250.py` against the compiled QEMU executable.
For example:

```sh
python3 tb371fc/tests/test_tbu_sm8250.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --out /absolute/path/new-tbu-results
```

The script exercises both SID intervals with nonidentity page mappings and
two QTest-only DMA aliases (`0x52000000` for SID `0xe0`, `0x53000000` for SID
`0x4e0`, each 16 MiB). These aliases exist only in the opt-in profile under
QTest, and are absent from normal TCG execution.

The tests cover delayed ACK, actual DMA failure/reopening, page-table and
permission faults, query cancellation/reset, pending HALT and query transfer
between real processes, and incompatible or impossible incoming state.
UFS is disabled in these component fixtures: the aliases exercise SMMU DMA,
not a physical UFS controller or whole-board power management.
