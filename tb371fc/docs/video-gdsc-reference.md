# VIDEO GDSC bounded digital reference

`qcom-video-gdsc` owns four independent four-byte GDSCR words from the actual
TB371FC DT. Its profile defaults to disabled; select the reference explicitly:

```sh
-global qcom-video-gdsc.enabled-reference=on
```

The reference models software-visible digital power requests and qualified
feedback. It adds no VIDEOCC clock controller, codec, DSP firmware, video DMA,
retained memory contents or physical transition timing. Existing camera,
MDSS and NPU types and VMState are unchanged.

## Observed native trigger

The preceding original-input run003 used source
`ce945f8dd74a704f0a0b0973a41c3a95e407f8d3` and QEMU SHA-256
`26d20d0835c42b880061bfb598d5973671feaa60307e6a4d3be26683c7d34a9c`,
from [formal CI run 45408402483](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45408402483).
That source passed 80 component gates and five Linux integrations, including
the NPU 21-check gate and its three completed, two profile-rejected and two
corrupted-stream-rejected migrations. Those are component results, not an
original Android boot verdict.

The captured printk progressed beyond the earlier NPU GDSCR read abort.
At guest 6.770760 s it linked `gdsc@abf0d18` to its parent regulator; at
6.772420 s it created the `mvs0_gdsc_ahb` software bus client. The first
observed synchronous external abort followed at 6.780231 s in CPU 4's
PID 79 `kworker/4:1`, through `deferred_probe_work_func` ->
`gdsc_probe+0x770` -> `regmap_mmio_read32le+0x8`. QEMU independently recorded
an invalid four-byte read at physical `0x0abf0d18`.

The [completed run003 receipt](https://github.com/cyjin-yl/qemu-tb371fc/blob/4e030d048b152038a75c0e2a49df0dcd24e30352/tb371fc/evidence/npu-20261124-e1a9/native-frontier-after-npu.json)
records a 120-second request, 122.55 seconds elapsed and final QMP paused
state at 2026-11-24 14:14:11 UTC. Original kernel, ramdisk, full DT, boot
arguments, AOP input and QEMU hashes were unchanged. No original LUN was
attached and no Android userspace or boot completion was observed.

The first printk SHA-256 is
`f5c1001ccc62a274005e9da55e13aa09e44b1d362b72cfde3a2505ead5729e8d`;
the QEMU log SHA-256 is
`43e208369292d4d32d8a10347e60001759fd28be686a902a1d224f5ce946b9fa`.
This is the actual next regulator-word failure, observed before this VIDEO
model existed. A worker Oops does not establish a PID 1 panic or successful
Android userspace.

## Fixed source and original-DT geometry

The [remaining GDSC contract](../evidence/wholeboard-20261123-a47/remaining-gdsc-frontier-contract.md)
and its [bounded DT extraction](../evidence/wholeboard-20261123-a47/gdsc-dt-evidence-09c638.json)
bind these resources to original DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The recorded `/soc` ranges map these bus addresses to identical physical
addresses. The parent-chain supplement resolves phandle 104 to `mmcx.lvl`.

| Index / domain | Physical word | DT flags | Driver polling bound |
| --- | --- | --- | --- |
| 0 / mvs0c | `0x0abf0bf8` | 1: retain-regs | 100 us |
| 1 / mvs1c | `0x0abf0c98` | 1: retain-regs | 100 us |
| 2 / mvs0 | `0x0abf0d18` | 3: retain-regs and hardware-trigger declaration | 100 us |
| 3 / mvs1 | `0x0abf0d98` | 3: retain-regs and hardware-trigger declaration | 100 us |

All four declare `parent-supply` and `vdd_parent-supply` as phandle 104 and
one `ahb_clk`: GCC phandle 22, vendor binding ID 205. They are siblings;
neither a control domain nor a camera domain is a parent of another. Each
software bus path uses master 1 to slave 596, with idle 0/0 and active
0/1 KBps vectors. These values describe driver requests, not measured
throughput or modeled video traffic.

None declares an auxiliary domain, software-reset or hardware-status
register; none declares root-clock enable, forced root-clock enable,
retain-mem, retain-periph, disallow-clear, skip-logic-collapse, proxy supply
or a timeout override. The fixed driver's 100-us polling bound is metadata,
not an invented transition delay.

The primary vendor sources are pinned to OnePlusOSS/android_kernel_oneplus_sm8250
commit `94304e12a3509eac7b3716bf574ce0b1fcaacbae`:

- [gdsc-regulator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gdsc-regulator.c),
  SHA-256 `41acd7aa326c656db5b9dbe57c5fe05f1783abf8a299aaf9f8d4c58b9ecea3d2`.
- [gcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c),
  SHA-256 `ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd`.
- [clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c),
  SHA-256 `375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa`.

## Exact access and three-state power protocol

Only aligned 32-bit accesses to each listed word are supported. Unassigned
neighbors and every other VIDEOCC address remain unassigned. In particular,
the board does not map the enclosing `0x0abf0000..0x0abfffff` range as RAM
or as a successful default register bank.

| Field | Bits | Reference behavior |
| --- | --- | --- |
| SW_COLLAPSE | 0 | Removes the software request when set; cold/reset value is 1 |
| HW_CONTROL | 1 | Selects a separately supplied hardware request; allowed only for mvs0/mvs1 |
| RETAIN_FF | 11 | Stores the software request without claiming retained contents |
| CLK_DIS_WAIT | 12..15 | Stores the field without asserting physical latency |
| POWER_ON | 31 | Derived read-only feedback; ordinary RMW may carry the bit through |

The common writable mask is `0xf803`. DT flags separately reject HW_CONTROL
on mvs0c and mvs1c. Unsupported bits, including SW_OVERRIDE, return a
transaction error without changing the old word. The fixed probe's initial
read, clear-HW_CONTROL/clear-SW_OVERRIDE and write-back sequence preserves
the collapsed cold state. A wide bus access may split into one supported
word and an unassigned neighbor; the accepted low word is not rolled back
if the second transaction fails.

The shared parent and each selected request have separate value and
knowledge bits. Software mode has a known request from SW_COLLAPSE.
Hardware mode uses its own `hw-request-known` and `hw-request` inputs;
the DT declaration does not create a requester. Power feedback is the
logical AND over possible parent and request values: a known zero makes
the result known-off, and only two known asserted inputs establish ON.
Other combinations remain unknown and reject GDSCR reads.

`state-known` qualifies each `power` GPIO. Unknown state publishes both
low; stale asserted input values with knowledge deasserted cannot establish
availability. Four domains retain independent controls and request inputs.
Pure QOM getters observe state without executing or repairing transitions.
Reset restores SW_COLLAPSE while preserving provider-owned inputs.

## Three-consumer mmcx wiring

The stateless `/machine/mmcx-gdsc-fanout` has three outputs. Its existing
output 0 stays connected to camera and output 1 to MDSS; output 2 connects
the new VIDEO aggregate. RPMh `power[3]` has one connection to this splitter,
so adding VIDEO cannot silently replace either existing consumer. Each
knowledge input is initialized from `res.mmcx.lvl.present` in the selected
RPMh profile. When no provider is present, all relevant knowledge remains
unknown.

The splitter has no migrated state. RPMh publishes its restored output to
all three consumers. Each GDSC independently validates and republishes its
own incoming state. Camera stays at 194 bytes, MDSS at 64 bytes and NPU at
64 bytes. NPU's actual DT declares no supply; its parent GPIOs remain
unconnected and are not added to this fanout. MDSS's self proxy remains a
software consumer, not a hardware parent loop.

## VIDEO_AHB retention requests

The generator verifies `gcc_video_ahb_clk` at line 3987 of the pinned vendor
source and the branch driver's `set_flags` dispatch. Only retention request
bits 13/14 are added to GCC offset `0xb004`; its mask changes from `0x1`
to `0x6001`. Their meanings are PERIPH and MEM retention requests. The
[generated manifest](gcc-sm8250-registers.json) records the source hashes,
owner line and exact field bits.

The existing mainline-derived descriptor remains ID 196, external input
79, with `reg=0xffffffff` and a probe-only CBCR. No read-only status bits or
HWCG mode are added to that descriptor; bit 1 remains unsupported. Its
unknown parent clock stays unknown, and branch or retention writes cannot
create a frequency. GCC's 200-clock geometry, 83 external slots and v3
wire format stay unchanged. Camera and DISP_AHB retain mask `0x6001`;
NPU_CFG_AHB retains `0x6003` and its existing HWCG/status behavior.

## Independent migration identity

The section `qcom-video-gdsc` is version 1/minimum 1 and contains exactly
142 payload bytes. Integers use the standard big-endian migration encoding.

| Offset | Bytes | Fixed or runtime field |
| --- | --- | --- |
| 0 | 4 | Enabled profile, equal on source and destination |
| 4 | 4 | Identity `0x56444731` (`VDG1`) |
| 8 | 4 | Domain count 4 |
| 12 | 4 | Register size 4 |
| 16 | 4 | Total mapped bytes 16 |
| 20 | 4 | Parent resource index 3, mmcx |
| 24 | 4 | Cold control 1 |
| 28 | 4 | Common writable mask `0xf803` |
| 32 | 4 | Power status mask `0x80000000` |
| 36..37 | 2 | Parent-known and parent-on |
| `38 + 26*i` | 4 | Domain ID `i` |
| `42 + 26*i` | 8 | Exact domain address |
| `50 + 26*i` | 4 | Domain DT flags |
| `54 + 26*i` | 4 | Polling bound 100 us |
| `58 + 26*i` | 4 | Writable control |
| `62 + 26*i` | 2 | Hardware-request-known and hardware-request |

All identity and geometry fields are equality guarded. Pre-load withdraws
validity and power for every domain. Post-load validates every control
before publishing any successful feedback. A mismatched profile, altered
address or geometry, unsupported control bit or HW_CONTROL on a domain
without that DT declaration rejects the stream. Restoring unknown inputs
over a previously powered target must not retain stale target availability.
The existing RPMh v3 sleep/wake fields and v2 receiver rules are unchanged.

## Required compiled acceptance

`test_video_gdsc.py` registers 22 unique compiled checks: 13 ordinary cases,
two independent profile/paused-TCG cases, three completed migrations, two
profile rejections and two single-bit corrupted-stream rejections. Four
original streams and two damaged copies are retained separately from the
successful-transfer count. Actual QTest MMIO, GPIO observations, QMP state
and separate source/destination processes are required.

The gate covers per-domain hardware declarations and unknown truth cases,
four-word ownership, cold driver RMW, reserved-bit atomic rejection, reset,
independent opt-in, three real mmcx consumers and the still-unconnected
NPU parent. Retention writes and migration cannot turn an unknown clock
into a frequency. The full 142-byte VIDEO state and independent camera,
MDSS and NPU states are compared against stopped source observations.
Restored target QOM and validity are checked before the first target device
MMIO or `cont`. After `cont`, the remaining 599+1 ns of a real pending mmcx
command are observed before any device MMIO can repair the state.

Source `4cc9dee07f542697c129b60d3ff5162aa0f71a53` actually passed the resulting
81 component/parser gates, five Linux integrations, existing UFS process
checkpoints and A77/PCI UFS checks in
[run 45414535386](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45414535386).
The actual ZIP and all six VIDEO streams were independently decoded. Its
same-binary original-input run004 registered all four VIDEO GDSCs, then first
aborted at CAMCC PLL0 MODE physical `0x0ad00000`; the
[CAMCC increment](camcc-sm8250.md) records that next boundary separately.
These component checks and POWER_ON feedback do not establish video
processing or completed Android boot.
