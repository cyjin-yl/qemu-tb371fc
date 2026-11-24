# MDSS GDSC bounded digital reference

`qcom-mdss-gdsc` owns exactly the four-byte control word at `0x0af03000`.
It is independent of the six-camera device and defaults to disabled. Select
the reference explicitly with:

```sh
-global qcom-mdss-gdsc.enabled-reference=on
```

This enables the documented digital power-request interface. It does not
enable DISPCC, a display engine, scanout, a panel, a clock source, or an NPU.
The native MDSS register-read frontier motivates the increment; component
receipts must not be presented as stock Android boot or display execution.

## Observed native trigger

The [sanitized original-input trace summary](../evidence/mdss-20261124-e1a9/native-frontier-before-mdss.json)
binds the preceding run to source `42144e86685aeb47f6a1eef8472d222795722863`
and QEMU SHA-256
`3be30157af1ca7ec511897effef06c5d7dfb8bfc5395b2e4de744ee1751f3f74`.
The original kernel, ramdisk, complete DT and boot arguments had identical
digests before and after that run. No original LUN was attached.

After camera-domain registration progressed, the kernel created the
`mdss_core_gdsc_ahb` software client. Its first observed synchronous external
abort occurred at guest time 3.696830 s in CPU 4's PID 78 `kworker/4:1`,
inside `deferred_probe_work_func` -> `gdsc_probe+0x770` ->
`regmap_mmio_read32le`. QEMU independently recorded the rejected four-byte
read at physical `0x0af03000`. This was a deferred-probe worker Oops;
no PID 1 panic or userspace success was observed. The two full-log hashes
and a short address/stack excerpt are retained without original firmware,
full boot arguments or endpoint identifiers.

That run preceded this MDSS model. It establishes the next native failure,
not successful execution of the new model or an Android boot result.

## Fixed source and original-DT geometry

The [remaining GDSC source contract](../evidence/wholeboard-20261123-a47/remaining-gdsc-frontier-contract.md)
records the original DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`,
the bounded extraction and its parent-chain supplement. It identifies one
`mdss_core_gdsc` resource, `qcom,retain-regs`, hardware-trigger support,
the `mmcx.lvl` ARC parent at phandle 104, and `GCC_DISP_AHB_CLK` at vendor
binding ID 24. The software bus request is master 1 to slave 590; it is not
a throughput measurement or a modeled display interconnect transaction.

All vendor references use OnePlusOSS/android_kernel_oneplus_sm8250 commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`:

- [gdsc-regulator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gdsc-regulator.c),
  SHA-256 `41acd7aa326c656db5b9dbe57c5fe05f1783abf8a299aaf9f8d4c58b9ecea3d2`.
- [gcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c),
  SHA-256 `ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd`.
- [clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c),
  SHA-256 `375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa`.

The node has no auxiliary domain, software-reset or hardware-status register,
no enable-root-clock or force-root-clock declaration, and no timeout override.
The fixed driver's 100-us poll bound is retained as source metadata; the
reference does not turn that bound into an invented transition delay.

## Exact register protocol

Only aligned 32-bit accesses to `0x0af03000` are supported. The rest of
DISPCC remains unassigned, including the words immediately before and after
the GDSCR. The display RPMh RSC at `0x0af20000` keeps its existing owner.

| Field | Bits | Reference behavior |
| --- | --- | --- |
| SW_COLLAPSE | 0 | In software mode, 1 removes the domain request; cold/reset value is 1. |
| HW_CONTROL | 1 | Selects the separately supplied hardware request and its knowledge flag. |
| RETAIN_FF | 11 | Stores the software request without claiming retained hardware contents. |
| CLK_DIS_WAIT | 12..15 | Stores the field without inventing latency. |
| POWER_ON | 31 | Derived, read-only feedback; ordinary read/modify/write may carry it through. |

The writable mask is `0x0000f803`. A write containing any other non-status
bit returns a transaction error and leaves the old control unchanged.
The fixed driver's probe reads the word, clears HW_CONTROL and SW_OVERRIDE,
then writes it back. SW_OVERRIDE is not implemented as an independent mode;
setting it is rejected. A read whose power result is unknown also returns
a transaction error. Default-disabled accesses always fail explicitly.

An eight-byte bus transaction can be decomposed by QEMU into this supported
low word and the unassigned next word. A successful low-word write is not
rolled back by the second transaction's error. Tests check that behavior
instead of claiming atomicity for an unsupported wide access.

## Three-state supply and request reasoning

The parent and selected request each carry a Boolean value and a separate
knowledge flag. Software mode always has a known request from SW_COLLAPSE.
Hardware mode begins with an unknown request; enabling the reference does
not create a display hardware requester.

The power result is the logical AND of the two inputs, evaluated over all
their possible values. A known-off parent or a known deasserted request
establishes a known-off result. Known-on requires both inputs to be known
and asserted. Other combinations remain unknown. A stale asserted value
with a deasserted knowledge flag is not evidence of availability.

The `power` GPIO is meaningful only when `state-known` is asserted. Unknown
feedback publishes both lines low and rejects the register read. Pure QOM
getters neither repair state nor execute a transition. Device reset preserves
provider inputs, restores SW_COLLAPSE, and republishes the resulting state.

## Shared mmcx connection and proxy consumer

RPMh resource `power[3]` drives the stateless two-output
`/machine/mmcx-gdsc-fanout` splitter. Output 0 preserves the existing camera
parent input; output 1 drives MDSS. Both knowledge inputs are initialized
from the selected RPMh profile's `res.mmcx.lvl.present` value. No second
connection overwrites the camera wire, and neither domain becomes a parent
of the other. Camera retains its original type, six entries and 194-byte
VMState.

The original MDSS node's self-referencing `proxy-supply` represents the
fixed regulator framework's software consumer. It is not a hardware parent
loop, an always-on rail or another MMIO block. If that software path runs,
its enable and retention requests use the same GDSCR/GCC protocol. This
model does not assert that the original kernel's proxy option executed.

## DISP_AHB retention requests

The generator pins the actual `gcc_disp_ahb_clk` declaration and the
`clk_branch2_ops.set_flags` implementation. The only new GCC register
permission is bits 13 and 14 at offset `0xb00c`, changing its writable mask
from `0x00000001` to `0x00006001`. These are PERIPH and MEM retention requests.
The exact source hashes and declaration line are in the regenerated
[register manifest](gcc-sm8250-registers.json).

The existing GCC descriptor ID 23, external-parent index 10, clock name,
clock array and GCC v3 wire layout stay unchanged. There is no declared
clock parent in the fixed vendor node, so the current unknown external
input remains unknown. Setting the branch vote or either retention bit
does not produce a frequency. NPU_CFG_AHB `0x4d004` and VIDEO_AHB `0xb004`
retain their old masks. The camera branch keeps its existing `0x6001` mask.

## Independent migration identity

The section name is `qcom-mdss-gdsc`, version 1/minimum 1. Its payload is
exactly 64 bytes; fixed-width integers are big endian on the migration wire.

| Payload offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 4 | Enabled profile, equal on source and destination |
| 4 | 4 | Reference identity `0x4d445331` (`MDS1`) |
| 8 | 4 | Domain count 1 |
| 12 | 4 | Register size 4 |
| 16 | 4 | Total mapped bytes 4 |
| 20 | 4 | Parent resource index 3 |
| 24 | 4 | Cold control 1 |
| 28 | 4 | Write mask `0xf803` |
| 32 | 4 | Power feedback mask `0x80000000` |
| 36..37 | 2 | Parent-known and parent-on inputs |
| 38 | 4 | Domain ID 0 |
| 42 | 8 | Physical address `0x0af03000` |
| 50 | 4 | DT flags 3: retain-regs and declared hardware mode |
| 54 | 4 | Driver poll bound 100 us, metadata only |
| 58 | 4 | Writable control |
| 62..63 | 2 | Hardware-request-known and hardware-request inputs |

All fixed identity and geometry fields use equality guards. Incoming state
first withdraws output validity and power. It publishes successful feedback
only after the whole payload has loaded and control bits have been checked.
Profile mismatch, altered geometry and reserved control bits are rejected.
The existing RPMh v3 sleep/wake fields and v2 receiving rules are unchanged.

## Acceptance gate

`test_mdss_gdsc.py` runs actual compiled QEMU transactions and separate source
and destination processes. The CI contract requires 20 unique checks,
three completed migrations, two bidirectional profile rejections and two
single-bit corrupted-stream rejections. Four original streams and two
damaged copies are counted separately from successful transfers.

The ordinary checks cover exact ownership, access widths, independent opt-in,
literal driver-style updates, exhaustive unknown inputs, reset, two observed
output groups, real RPMh fan-out and retention storage with zero clock period.
The tests do not intercept the parent power output. They require the camera
and MDSS consumers to respond to the same real RPMh command completion.

Migration checks compare the complete 64-byte MDSS and unchanged 194-byte
camera sections, the bounded 19424-byte RPMh source scenario, and the selected
GCC words. Destination QOM, validity and RAM are observed before any
GDSC/GCC/RPMh MMIO or `cont` can conceal an incorrect restore. A pending mmcx
vote resumes at its original 599+1 ns remainder; unknown hardware feedback
stays unknown until a real input is supplied. Unknown source state is also
loaded over an already-powered target.

These are required tests, not a claim that this source revision has already
passed compiled execution. New CI/CT102 receipts and a new unchanged-input
native run are required before advancing the observed Android frontier.
