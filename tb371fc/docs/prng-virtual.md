# Explicit virtual PRNG for TB371FC

The `prng-virtual=on` machine option provides a host-entropy data path for the
preserved `/soc/qrng@793000` node. It is **off by default**. This is a virtual
device contract, not a Qualcomm silicon RNG, a firmware boot, or a measurement
of physical entropy quality.

## Original input and observed gap

The retained full DTB has SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
Its PRNG node has compatible `qcom,msm-rng`, region `<0x793000 0x1000>`,
`iface_clk` at the Kona GCC binding 80, and both
`qcom,msm-rng-iface-clk` and `qcom,no-qrng-config`.
It does not ask this driver to configure the secure-side QRNG registers.

The pinned vendor reference is
[OnePlusOSS commit 94304e12](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/94304e12a3509eac7b3716bf574ce0b1fcaacbae).
Its [GCC descriptor](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c)
uses vote offset 0x52000 bit 13 and halt/CBCR offset 0x34004.
The [branch driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c)
waits for CLK_OFF bit 31 to clear when enabling.
The existing QEMU offsets and masks already agree. Its PRNG external parent
was unconnected, so a guest vote could not produce a running branch.

The saved original-kernel log first reports this clock off at 4.153257 seconds,
then repeats hwrng failures. Initialization continues to the separate DSU
undefined instruction at 5.406881 seconds. These warnings alone did not
establish that PRNG was the fatal boot frontier. The reference repository is
not asserted to be the exact full source build of stock 4.19.157-perf+.

## Activation, clock and firmware assumptions

Use:

```text
-machine tb371fc,...,prng-virtual=on
```

The option is fixed after machine creation. It explicitly connects GCC's
`gcc_prng_ahb_clk_parent` to the board's existing
`bootstrap-fabric-hz` carrier, normally 100 MHz. This is a synthetic virtual
AHB carrier. The real silicon parent and rate have not been identified.

The connection does not force the branch on. The preserved guest must still
vote bit 13 at physical address 0x00152000. GCC derives CLK_OFF at 0x00134004
from that vote and actual parent availability. A zero carrier or a cleared
guest vote leaves the PRNG unavailable.

The profile represents firmware having configured the data generator, as
required by the preserved `qcom,no-qrng-config` input. It does not modify the
kernel, DT, boot arguments or secure-side configuration bank.

When off, the board leaves the parent unconnected, does not map the PRNG MMIO
aperture, and does not create its default entropy backend. An inert device
object remains at `/machine/prng` solely for explicit migration compatibility.

## Entropy backend

An enabled device without an explicit `rng` link creates QEMU's existing
`rng-random` backend with filename `/dev/urandom`. The backend must open
successfully before the device is realized. This selects the host operating
system's entropy interface; it does not use QEMU's deterministic guest
`-seed` generator.

The generic QEMU `rng` link is retained for separately declared backends and
component fixtures, for example a finite file or `/dev/null`. Those cases use
a different migration profile ID. They must not be reported as the default
host-entropy profile or used as a native entropy qualification. The production
profile requires the default backend type and filename to be observed.

As with QEMU's existing backend, open failure rejects startup. A normal short
read or EOF never creates missing entropy. An unexpected fatal I/O error is
handled by the existing `rng-random` implementation, which terminates rather
than handing the device fabricated bytes. Backend/OS operation is not a
verification of a tablet's physical RNG.

## Register and FIFO contract

The original 4 KiB region is a decode guard. Only these two operations succeed:

| Offset | Access | Meaning |
| --- | --- | --- |
| 0x000 | aligned 32-bit read | Consume one complete FIFO word, little endian |
| 0x004 | aligned 32-bit read | Bit 0 is 1 only when clocked and at least four bytes are buffered; other bits zero |

Writes, unsupported widths, unaligned accesses, and other offsets return
`MEMTX_ACCESS_ERROR`. In particular +0x100 and +0x104 are not invented as
working configuration registers. A 1–8-byte transaction reaches the callback
whole before its width/alignment is checked, preventing a 64-bit DATA access
from being split into a successful DATA read and a STATUS read.

A 64-byte circular FIFO retains complete and partial backend responses. Only
complete four-byte words are exposed. DATA reads remove exactly four bytes
and clear the consumed storage. A DATA read while clocked off or without a
complete word fails and consumes nothing. Error-returned bus bytes are never
classified as entropy by the tests.

There is at most one pending backend request. The clock-on transition and
valid guest accesses may request free FIFO space. Completion copies only
bytes actually returned by the backend. A short response is retained; EOF
leaves STATUS clear if fewer than four bytes remain. Retrying requires a new
guest access, so EOF does not create a background request loop. No fabricated
LFSR stream, fixed-data fallback, unconditional-ready status, or IRQ is used.

The [vendor data reader](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/char/hw_random/msm_rng.c)
first enables the clock, then polls STATUS bit 0 and reads DATA. If unavailable,
it performs five 440-microsecond delays before returning without data. The
driver advertises hwrng quality 1024 and also a crypto RNG; that is why returning
non-entropy just to pass probing is unacceptable.

The model does not claim silicon sample timing, physical throughput or an
entropy rate. Backend completion is asynchronous host I/O, not a cycle model.

## Reset, clock removal and migration

Clock removal cancels this device's outstanding backend request and retains
already buffered bytes. STATUS becomes zero and DATA cannot consume those
bytes until the clock returns. Cancellation is scoped by the backend callback
and opaque pointer, so it does not cancel another frontend's requests.

System reset cancels pending requests, zeros FIFO storage and indices, and
clears diagnostic counters. GCC independently resets the guest vote. No
pending host request or host file descriptor is serialized.

Migration uses version 1 and requires equal profile IDs before loading
mutable state:

- 0: off;
- 0x50524e01: explicit virtual profile with the default host entropy backend;
- 0x50524e02: explicit virtual profile with an externally supplied backend.

The FIFO, its read position, byte count and diagnostic counters are migrated.
Pre-save and pre-load cancel this frontend's pending request. Post-load
validates bounds, canonical unused bytes and the inert off state. It preserves
unconsumed entropy without pre-filling the FIFO; the next ordinary guest
access may request new entropy from the destination backend. The GCC remains
the owner of the clock. The destination must provide its own declared backend.

Ordinary migration retains the virtual machine's unconsumed entropy, just as
it retains other guest state. It is not a facility for promising distinct
random streams after deliberately cloning or reverting a whole VM.

Read-only QOM diagnostics are `profile-id`, `backend-type`, `clock-hz`,
`fifo-bytes`, `words-read`, `backend-requests`, `empty-responses`, and
`request-pending`. Reading them does not request or consume entropy. Raw FIFO
contents are not exposed as QOM properties.

## Regression scope

`tb371fc/tests/test_prng_virtual.py` uses the existing QMP/QTest transport and
actual child cleanup. It adds a mandatory `prng-virtual` CI gate after the
110 retained component gates, preserving their order and verdict requirements.
The five Linux gates retain their existing order. The complete plan has 111
component gates and five Linux gates.

Eleven checks cover default-off behavior; actual default backend/type/path and
the original GCC vote/poll; read-only/width/alignment/hole rejection; gating;
an ordinary migration preserving actual unconsumed host-backed FIFO words;
finite-byte consumption including a three-byte tail and EOF; system reset;
pending-request cancellation for clock removal, reset and pre-save followed by
source resumption; empty-backend failure; unavailable-backend startup failure;
and legitimate
on/off migration incompatibility.

The gate uses nine independently launched QTest fixtures, three ordinary saved
streams and two profile-mismatch rejections. It does not forge or alter a
migration payload, execute original Android, access physical MMIO, claim real
GIC delivery, or certify entropy quality. Finite backend bytes are explicit
test input. The host-entropy sample is never used as a fixed expected sequence;
the migration check compares it only with the same saved VM state.

A green component result establishes this virtual interface contract.
Original Android userspace and six-LUN boot remain separate acceptance
milestones requiring their own observed evidence.
