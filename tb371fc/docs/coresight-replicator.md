# TB371FC CoreSight dynamic replicator APB control reference

The original Android diagnostic using source `66ffa7b51af299f7e5bdc09bae2cd76f5a062668`
crossed the former TMC DEVID abort and reached the AMBA
`replicator_probe+0x144/0x1c0`. It then aborted on an unmapped 32-bit write to
`0x06046fb0`, the QDSS replicator's software lock access register.

The original full DTB (SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`)
contains these two independent `arm,primecell` nodes:

| Node | MMIO base | Size | DT name | DT AMBA override |
| --- | --- | --- | --- | --- |
| QDSS | 0x06046000 | 0x1000 | coresight-replicator-qdss | 0x000bb909 |
| SWAO | 0x06b06000 | 0x1000 | coresight-replicator-swao | 0x000bb909 |

This change maps both real DT locations. It preserves the kernel, ramdisk,
full DTB, boot arguments and native runner. The native005 failure result and
kernel log have SHA-256
`99e0c46812346115762902a3169aca97339edfe71c2d311d5648f2de7b8cd7c7` and
`156f0c305d40e13d26465abc7b08ffcce2167337cee741583e72646a9f65b04c`,
respectively. This diagnostic did not reach Android userspace.

## Primary references and identity

The register reference is
[Arm CoreSight SoC TRM, DDI 0480A](https://documentation-service.arm.com/static/5f873565405d955c5176e176),
especially the dynamic replicator register summary and descriptions in
pages 3-37 and 3-43 through 3-49.
The software consumer is the fixed vendor
[coresight-dynamic-replicator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/coresight/coresight-dynamic-replicator.c)
(blob `1d9f40f49e0ca47e85f19e9cb886744f287ca7ee`), together with the CoreSight
claim helpers and lock macros at the same commit.

The SoC manual's release name and each component's revision are different
fields. Its dynamic replicator table gives PIDR2=0x1b for component r1p0.
This reference therefore reports full peripheral ID 0x001bb909. Its part and
designer bits match the original DT's 0x000bb909 override under the vendor
AMBA mask 0x000fffff. The firmware override is not evidence of the physical
tablet's component revision, and this model does not claim that measurement.
CID is 0xb105900d, DEVID is 2 for two master ports, and DEVTYPE is 0x22.
AUTHSTATUS is zero; this component implements no local authentication gate.

The original kernel bytes independently agree with the probe sequence:
its AMBA `replicator_probe` begins at `0xffffff8008d25be8`; at +0x144 it
writes the unlock key to LAR, claims bit 1, writes 0xff to both ID filters,
disclaims and relocks. The other same-named kernel symbol is the static
platform replicator driver and is not the failing AMBA consumer.

## Implemented contract

Each output has an independent eight-bit IDFILTER register. Both reset to
zero. Writes retain only those eight bits; setting a filter bit represents
blocking the corresponding ATID group. The registers represent the control
configuration only. No ATB producer or routing datapath is connected, no
trace packet is synthesized, and these controls do not prove trace delivery.

The software lock resets locked (LSR=3). Only 0xc5acce55 written to LAR
unlocks it (LSR=1); other values relock it. A locked component ignores writes
to its implemented controls other than LAR. CLAIMSET reads the supported
mask 0xf and applies write-one-to-set; CLAIMCLR reads the current claim tags
and applies write-one-to-clear. Claim state resets to zero. The two instances
never share filter, claim or lock state.

The actual vendor protocol is tested: probe/reset first takes the self-hosted
claim, blocks both ports with 0xff, releases the claim and relocks. A busy
claim prevents those filter writes. Starting the first output takes claim
bit 1; starting the second retains that claim. Disabling one port leaves it
owned while the other remains enabled, and disabling the last port releases
the claim. An external owner is not silently cleared.

No integration test datapath is implemented. As permitted by the register
description for such implementations, ITCTRL reads zero and accepts zero.
When unlocked, nonzero ITCTRL requests are explicitly rejected. The unsupported
EF8/EFC integration registers always produce an access error. Only the documented control subset and identity
registers are mapped. Wrong-size, misaligned, reserved-hole, write-only-read
and unlocked read-only-write transactions produce an access error and diagnostic,
without turning those addresses into mutable shadow registers.

Ordinary migration preserves the profile identity, both filter words, claim
tags and software lock for each instance. Invalid filter or claim widths are
rejected when loading. There is no pending trace transfer, timer, IRQ or DMA
state to manufacture or discard.

## Verification

`tb371fc/tests/test_coresight_replicator.py` has ten required scenarios:
the two DT-compatible identities, reset and software locking, the real
vendor probe/reset protocol, filter and claim masks, busy-owner rejection,
two-port claim lifetime, access rejection without state mutation, two-device
system reset, and an ordinary process checkpoint with matched source and
destination continuation. Its final scenario verifies every child was
actually quit, waited, reaped and drained. Forced cleanup fails acceptance.

The test records and rechecks binary and test-source hashes. It never changes
QOM state, guest inputs or migration payloads. Raw checkpoint streams are
excluded from the uploaded artifact; the receipt records their hashes and
the test checks each stream again after loading. This is a control-register
and process-migration result, not a stock Android or trace-routing verdict.

The existing 116 public core gates, the separate sixteen TMC scenarios,
Linux migration continuations and CPU/PCI gates remain required. The ready
workflow also watches the new device, header and test paths so each final
candidate must obtain fresh push and PR readiness results.
