# Config NoC SBM reference handshake

This addition supplies an opt-in digital peer for three config-NoC SBM words
used by the production driver. It stores connection requests, delays each
port's feedback independently, and permits an external reference peer to
remain stalled. It does not drain camera, display or Venus traffic, execute
those endpoints, or block unrelated UFS/SMMU DMA.

The immediate input was the original-kernel run of source
`b95f973f9b20d53e3a0b8843c919156e3acb58d9`: after all four firmware-devfreq
voters and their parent registered, the next recorded unsupported access was
a 32-bit write at `0x01506080` in `msm_bus_noc_sbm_config`. The exception was
in deferred-probe work. It is not evidence that PID1 panicked, that userspace
was never reached, or that Android boot completed. The root evidence receipt
tracks the exact binary and native run independently of this model document.

## Sources and geometry

The register direction, W1 masks, feedback polarity and software polling
sequence come from the fixed vendor implementation:

- Repository: `OnePlusOSS/android_kernel_oneplus_sm8250`.
- Commit: `94304e12a3509eac7b3716bf574ce0b1fcaacbae`.
- File: [drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c).
- Bytes: 13806; SHA256:
  `9e408e18745857c575be792a92cb3eb7bcab1fb51b181cc389fd2b81213560a6`.
- Function `msm_bus_noc_sbm_config`, lines 374–445. The low/high constants
  precede it; STATUS constants are defined but not used by this function.

The actual production DT has SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
It supplies one config-NoC owner at `0x01500000` and an SBM offset `0x6000`.
The relevant camera, display and Venus consumer groups select low ports
0–2, 3–4 and 5–7 respectively. No selected port requires the high bank.

The independent [source and actual-DT review](https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/6346b2fab8d6ec380eaaab42ef9a21c5e27b5be5)
contains the full binding, call-path and provenance review. That JSON is
19529 bytes, SHA256
`231b262385d880b294d9622b7924017c4b2d8568de4036e42250d35ed67c2f9e`.
It records the distinction between source facts and the reference policies
below. It is a source review, not a successful execution receipt.

| Resource | Physical address | Supported access | Meaning |
|---|---:|---|---|
| FLAGOUTCLR0_LOW | `0x01506080` | write32 | Each written 1 requests connected feedback 0 |
| FLAGOUTSET0_LOW | `0x01506088` | write32 | Each written 1 requests disconnected feedback 1 |
| SENSEIN0_LOW | `0x01506100` | read32 | Current committed reference feedback |

Each word has its own four-byte region. The total active footprint is 12
bytes, with mask `0xff`. CLR/SET reads and SENSE writes fail. Any write with
bits outside ports 0–7 fails before changing requests, timers or diagnostics.
The source does not establish CLR/SET readback, so the model does not invent it.

The high-bank words at `0x01506084`, `0x0150608c`, `0x01506104`, the STATUS
words at `0x01506090` and `0x01506094`, and other surrounding words remain
unassigned. No broad SRAM or fabric container absorbs them. Invalid mapped
width/alignment is rejected by the QEMU memory core. Atomic rejection refers
to a single four-byte device transaction: a wider host memory request may be
split by QEMU, and a later hole does not undo an earlier completed transaction.

The existing 47-port/188-word QoS device, its header, its tests and its VMState
remain unchanged. SBM is a separate type and does not reuse a QoS instance ID.

## Explicit policy and default behavior

The type is `qcom-noc-sbm`, at `/machine/noc-sbm-config`. It is always created
as a SysBus device, so it participates in reset and migration even with the
default profile disabled. Enable the reference with:

```text
-global qcom-noc-sbm.enabled-reference=on
```

The default is `off`. After qdev properties have been applied, realization
disables all three MemoryRegions for that profile. They are consequently
absent from the active FlatView and retain the existing unassigned-memory
behavior. This preserves the old QoS tests' strict negative accesses. The
region geometry QOM values describe the declared layout; `mmio-enabled`
reports whether this reference profile enables that layout.

The reference cold state is all eight ports connected: requested, sense and
pending masks are zero. There is no writable boot-state override. This is
an explicit reference policy, not a dump of firmware's initial SBM state.

The per-port completion delay is exactly **1000 virtual nanoseconds**. This
number is a model policy, not measured hardware timing. In particular, the
driver's `udelay(10)` polling interval and `msecs_to_jiffies(1)` software
timeout do not determine a hardware handshake latency.

## Requests, completion and stalls

For each selected port the model keeps requested and committed sense bits
separate. When they first differ, that port receives a new 1000 ns interval.
Only the virtual-clock timer can commit requested into sense. Neither an
MMIO read nor a QOM getter services a timer or completes a request.

- Repeating an outstanding request preserves its deadline or paused remainder.
- A reverse request that equals current sense cancels the outstanding request.
- A request already equal to current sense creates no pending transition.
- A legal zero-mask write is a W1 no-op. It still counts as an accepted model
  MMIO transaction, without creating or extending a transition.
- Ports retain separate deadlines. One timer schedules the earliest active
  deadline and can complete all ports whose own intervals have expired.

The normal host QOM property `reference-peer-stall-mask` is an external
reference stimulus, available in normal TCG as well as QTest. It is not a
guest register and is not a measured endpoint-busy input. Its value is limited
to `0xff`; its setter requires a realized, enabled reference profile.

Setting a port's stall bit pauses that pending port at
`max(deadline - current_virtual_time, 0)`. Other ports continue. Removing a
stall resumes the exact preserved remainder from the current virtual time.
Repeating the same stall mask does not restart an interval. A new request
while already stalled holds a full 1000 ns remainder until released.

A pending remainder of zero is legitimate: a VM may stop after a deadline
without dispatching the callback. A stalled zero-remainder request remains
pending even while another port's callback runs. Removing its stall only
schedules a due timer; the setter does not acknowledge it. MMIO commands,
host stimuli and timer callbacks take effect in their actual QEMU dispatch
order. A late callback commits each currently pending, unstalled port once;
it does not synthesize past peer activity or additional requests.

Before a multi-port command or unstall is changed, every newly required
deadline must fit signed 64-bit virtual time. Otherwise the whole operation
fails without partial updates or diagnostic increments. An incoming stream
which cannot rebase its active remainders onto the target's virtual time is
also rejected. No deadline wraps or is silently shortened.

This peer can remain incomplete indefinitely when stalled. That is a useful
model timeout condition, but it is not a simulation of a hardware transaction
queue or a proof that real endpoints have drained. There is no IRQ, Clock,
voltage, bandwidth, traffic injector or firmware service in this component.

## Reset and observations

System reset cancels the timer, clears all requested/sense/pending bits and
all remainders, and restores the connected cold policy. It clears accepted
command counters and last masks. It **retains** the external peer-stall mask.
Thus a stalled environment can hold a new request after reset. Initial
realization starts with external stall zero.

Readonly QOM observations use the following names:

| Properties | Type | Scope |
|---|---|---|
| `instance-id`, `supported-port-mask`, `port-count` | uint32 | Fixed identity: 0, 255, 8 |
| `clear-address`, `set-address`, `sense-address` | uint64 | Actual three physical words |
| `reference-latency-ns`, `reference-identity`, `reference-boot-disconnected-mask` | uint32 | 1000, `0x53424d31` (SBM1), 0 |
| `mapped-window-count`, `mapped-register-count`, `mapped-bytes` | uint32 | Declared layout: 3, 3, 12 |
| `mmio-enabled` | bool | Reference-profile activation |
| `requested-mask`, `sense-mask`, `pending-mask` | uint32 | Stored handshake state |
| `accepted-set-writes`, `accepted-clear-writes` | uint64 | Model accepted-MMIO diagnostics, saturating at UINT64_MAX |
| `last-set-mask`, `last-clear-mask` | uint32 | Last accepted mask for each command kind |
| `portN-remaining-ns`, N=0…7 | uint64 | Pure remaining-time projection, or fixed paused remainder |
| `portN-deadline-ns`, N=0…7 | int64 | Target-local deadline, or -1 for idle/stalled |

`enabled-reference` is the immutable qdev configuration property.
`reference-peer-stall-mask` is uint32 and is the only mutable host stimulus.
Its getter describes the external reference input even while the profile is
disabled; writes, including zero, are refused while disabled. The other
runtime getters fail with `qcom-noc-sbm: unknown profile` when disabled.

Accepted-command counts and last masks are **model diagnostics**, not extra
SBM registers. They are migrated and reset so a native stopped observation
can identify model MMIO activity without modifying guest memory. An accepted
CLR on an already connected port can be entirely idempotent; its count does
not prove a real drain, a new transition, driver success, or userspace boot.
The fixed source also has caller paths whose software connection bookkeeping
changes before the SBM call and whose commit path does not propagate its
return value. Native claims therefore require the actual access/feedback and
kernel evidence, not just a consumer registration message.

## Migration format

VMState is `qcom-noc-sbm`, version 1, minimum version 1. The complete device
payload is **152 bytes**. There is no serialized QEMU timer, absolute deadline,
virtual epoch, Clock, IRQ, DMA queue or native traffic state.

| Offset | Bytes | Fields |
|---:|---:|---|
| 0 | 4 | Profile marker, UINT32_EQUAL |
| 4 | 4 | Instance ID 0, UINT32_EQUAL |
| 8 / 16 / 24 | 8 each | CLR / SET / SENSE addresses, UINT64_EQUAL |
| 32 | 4 | Supported mask `0xff`, UINT32_EQUAL |
| 36 | 4 | Latency 1000 ns, UINT32_EQUAL |
| 40 | 4 | Reference identity `0x53424d31`, UINT32_EQUAL |
| 44 | 4 | Fixed boot-disconnected mask 0, UINT32_EQUAL |
| 48 / 52 / 56 / 60 | 4 each | Stall / requested / sense / pending masks |
| 64…127 | 8 each | Eight canonical remaining nanoseconds |
| 128 / 136 | 8 each | Accepted SET / CLR transaction counters |
| 144 / 148 | 4 each | Last SET / CLR masks |

The first 48 bytes bind immutable profile, geometry and reference policy.
Source and destination must match, including `off` versus `on` in either
direction. Historical binaries without this device are outside the migration
compatibility claim; this addition does not change other devices' formats.

Pre-save samples virtual time once and records each canonical remainder.
It does not complete a due transition or change the source's absolute
deadlines. Pre-load disables timer dispatch. Post-load rejects unsupported
bits, inconsistent `pending != requested XOR sense`, nonzero idle remainders,
remainders above 1000, and impossible command-history states. In particular,
SET count zero cannot coexist with requested or sense bits equal to one,
CLR count zero cannot coexist with a reconnect pending, and count zero
requires its corresponding last mask to be zero. Disabled runtime is entirely
zero, including its external input and diagnostics.

Post-load samples the target's own virtual time once, validates the restored
state, then reconstructs active deadlines as `target_now + remaining` and schedules the
timer before the first device MMIO. Paused ports remain paused with deadline
-1. The restored external mask replaces the target's old reference stimulus.
A zero remainder stays pending until timer dispatch. Target and source clock
origins need not match. No QOM read or initial device MMIO repairs the loaded
state.

## Verification scope

This model increment is subject to the separate formal component and unified
board CI run. The implementation submission itself is not a build or runtime
receipt. Acceptance covers nonzero disconnect/reconnect MMIO requests, independent
port intervals, stalls, repeated/reversed commands, strict access errors,
reset, actual file migration and rejected malformed states. Root records the
actual same-binary native outcome separately. Neither a synthetic peer ACK nor
a passed component gate establishes complete board or Android usability.
