# NPU GDSC bounded digital reference

`qcom-npu-gdsc` owns only the four-byte control word at `0x09981004`.
It is a separate device from NPUCC, camera and MDSS, and its default-disabled
profile returns transaction errors. Select the reference explicitly with:

```sh
-global qcom-npu-gdsc.enabled-reference=on
```

The reference starts with SW_COLLAPSE asserted. Its parent remains unknown:
the actual DT does not declare a supply, and the board does not infer one
from mmcx, NPUCC, a branch vote or a clock rate. This increment provides a
bounded regulator control interface, not NPU execution or original Android
boot completion.

## Observed native trigger

The [sanitized original-input run002 summary](https://github.com/cyjin-yl/qemu-tb371fc/blob/fd905b821f318c1499ea0e0f8a4e7017226b383d/tb371fc/evidence/mdss-20261124-e1a9/native-frontier-after-mdss.json)
binds the run to source `9d5e942d9fd5f481e831b89045af93150d2fc55b`,
formal CI run 45402110062 and QEMU SHA-256
`33d27c37b0a9abd53630616fc803808702326ec3aab8a8d13dfeadccea47e94e`.
The original kernel, ramdisk, complete DT and boot arguments were unchanged.
No original LUN was attached, and Android userspace was not observed.

The kernel registered `mdss_core_gdsc` at guest 3.546862 s. At 3.555330 s,
CPU 6's PID 83 `kworker/6:1` reported a synchronous external abort in
`deferred_probe_work_func` -> `gdsc_probe+0x770` ->
`regmap_mmio_read32le+0x8`. QEMU independently logged an invalid four-byte
read at physical `0x09981004`. The following diagnostic's `0x4` is a
subregion-relative offset, not a second physical device. This was a worker
Oops; it is not evidence of a PID 1 panic or completed userspace startup.

The first captured printk SHA-256 is
`0c35e6a0326ae6d4db2c4bfe5f54b7fe22c07b517f0e7d838c002f6d451a0f75`;
the QEMU log SHA-256 is
`ccaa6deceab4646099b853261fb1cc4d25f79577f554e2c5a634aac3028f34bc`.
These are the preceding run's observations, before this NPU model existed.

## Fixed source and actual DT

The [remaining GDSC contract](../evidence/wholeboard-20261123-a47/remaining-gdsc-frontier-contract.md)
and its [bounded DT extraction](../evidence/wholeboard-20261123-a47/gdsc-dt-evidence-09c638.json)
identify `/soc/qcom,gdsc@9981004` in the original DT whose SHA-256 is
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.

| DT field | Recorded value and consequence |
| --- | --- |
| `reg` | `0x09981004`, 4 bytes; one independent control word |
| `regulator-name` | `npu_core_gdsc` |
| `qcom,retain-regs` | Present; the request bit can be stored |
| `clocks` / `clock-names` | GCC phandle 22, vendor binding 43 / `ahb_clk` |
| Parent supply | Neither `parent-supply` nor `vdd_parent-supply` is declared; parent remains unknown |
| Hardware trigger | Not declared; HW_CONTROL writes are rejected |
| Auxiliary registers | No domain, software-reset or hardware-status address |
| Root clock / timeout | No enable-root, force-root or timeout override |
| Retention and proxy | No retain-mem, retain-periph, disallow-clear, skip-logic-collapse or proxy property |

All vendor references use OnePlusOSS/android_kernel_oneplus_sm8250 commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`:

- [gdsc-regulator.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gdsc-regulator.c),
  SHA-256 `41acd7aa326c656db5b9dbe57c5fe05f1783abf8a299aaf9f8d4c58b9ecea3d2`.
- [gcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c),
  SHA-256 `ed208678508c983d0989f1f135fac898c5a9a91c14af1fa42cd7ea2f24cd14cd`.
- [clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c),
  SHA-256 `375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa`.

The fixed probe reads GDSCR, clears HW_CONTROL and SW_OVERRIDE, and writes
the resulting control back. The cold collapsed state supports that sequence
without asserting any parent supply. The driver's default 100-us polling
bound is exposed as source metadata; no transition timer is invented.
Its per-clock retention requests depend on the initial status and optional
DT fields. Source analysis alone does not show that a particular nonzero
retention write occurred in run002.

## Control word and three-state feedback

Only aligned 32-bit accesses at the exact address are supported.

| Field | Bits | Behavior |
| --- | --- | --- |
| SW_COLLAPSE | 0 | 1 removes the software request; cold/reset value is 1 |
| HW_CONTROL | 1 | Rejected because the actual node does not declare hardware mode |
| RETAIN_FF | 11 | Stores a request; no retained payload is modeled |
| CLK_DIS_WAIT | 12..15 | Stores the field; no latency is inferred |
| POWER_ON | 31 | Derived read-only feedback; may be carried through ordinary RMW |

The common control mask is `0xf803`, with an additional DT-based rejection
of HW_CONTROL. SW_OVERRIDE and all other non-status bits are unsupported.
Rejected writes leave control unchanged. A wide transaction can split into
the supported low word and an unassigned following word; no atomicity is
promised across those separate transactions.

Power feedback is the three-state AND of the parent and selected request.
Software mode always has a known request from SW_COLLAPSE. A known-off
parent or a deasserted software request establishes known-off. Known-on
requires an explicitly known-on parent and an asserted request. With an
unknown parent, clearing SW_COLLAPSE records the request but leaves feedback
unknown; reading GDSCR returns `MEMTX_ERROR`, not a successful ON status.

The board leaves `parent-known` and `parent-on` GPIOs unconnected. They exist
for a future evidenced provider and explicit test fixtures; no command-line
property supplies a hidden always-on rail. The retained hardware-request
inputs cannot bypass the hardware-mode rejection. Provider-owned input
values survive device reset, while control returns to SW_COLLAPSE=1.

`state-known` qualifies the `power` output. Unknown feedback publishes both
low. Pure QOM getters observe the request, input knowledge and derived
feedback without executing or repairing a transition. An asserted stale
input value with its knowledge flag low cannot establish power availability.

## Ownership and NPU_CFG_AHB requests

The NPUCC controller keeps its original 26 sparse windows and 95 registers.
The GDSCR word lies between NPUCC's `cc + 0x1000` word and the next window
at `cc + 0x1010`; `cc + 0x1008/+0x100c` remain unassigned. No broad NPUCC
or DSP memory container is added. Camera and MDSS retain their independent
types, profiles and VMState; their shared mmcx wiring does not reach NPU.

The generator checks the fixed `gcc_npu_cfg_ahb_clk` declaration and branch
`set_flags` implementation. It adds only PERIPH/MEM retention bits 13/14 at
GCC offset `0x4d004`: writable mask `0x00000003` becomes `0x00006003`.
The existing HWCG bit 1 and read-only status mask `0xf0000000` stay intact.
The [generated manifest](gcc-sm8250-registers.json) records source hashes
and declaration line 2112.

QEMU descriptor 42, external-parent index 21, the 200-clock geometry and
GCC v3 wire layout remain unchanged. The vendor node has no declared clock
parent, so its unknown external input still yields zero clock period.
Setting enable, HWCG or either retention request cannot synthesize a rate.
Camera and display AHB masks remain `0x6001`. The later
[VIDEO increment](video-gdsc-reference.md) independently adds the same two
retention requests to VIDEO_AHB; it does not connect NPU to mmcx.

## Independent migration identity

The `qcom-npu-gdsc` section is version 1/minimum 1, with exactly 64 payload
bytes. Fixed-width integers use the normal big-endian migration encoding.

| Offset | Bytes | Meaning |
| --- | --- | --- |
| 0 | 4 | Enabled profile, equal on source and destination |
| 4 | 4 | Identity `0x4e504731` (`NPG1`) |
| 8 | 4 | Domain count 1 |
| 12 | 4 | Register size 4 |
| 16 | 4 | Total mapped bytes 4 |
| 20 | 4 | Parent resource index `0xffffffff`: no declared resource |
| 24 | 4 | Cold control 1 |
| 28 | 4 | Common write mask `0xf803` |
| 32 | 4 | Power status mask `0x80000000` |
| 36..37 | 2 | Parent-known and parent-on inputs |
| 38 | 4 | Domain ID 0 |
| 42 | 8 | Physical address `0x09981004` |
| 50 | 4 | DT flags 1: retain-regs only |
| 54 | 4 | Driver polling bound 100 us, metadata only |
| 58 | 4 | Writable control |
| 62..63 | 2 | Hardware-request-known and hardware-request inputs |

Equality guards bind every fixed identity and geometry field. Incoming
state first withdraws output validity and power; the whole payload and
runtime controls must validate before successful feedback can be published.
Profile mismatch, altered geometry, reserved controls and hardware mode
are rejected. Unknown source state must overwrite a known-on target
without acquiring the target's old parent state. A known-parent test fixture
is also restored over a target with unknown inputs; this is explicit fixture
state, not a claim about the tablet's physical supply.

## Acceptance gate and remaining execution

`test_npu_gdsc.py` requires 21 unique compiled checks, three completed
process migrations, two profile rejections and two single-bit corrupted
stream rejections. Four original streams and two damaged copies are counted
separately from successful transfers. The tests require real QEMU MMIO,
GPIO observation, QMP state and source/destination processes.

The ordinary cases cover disabled ownership, the cold driver-style probe,
all parent/request combinations, unsupported mode, atomic rejected writes,
access shape, reset, independent camera/MDSS profiles, the unchanged NPUCC
window geometry, and retention with an unknown GCC input. A real mmcx vote
must change camera/MDSS while leaving NPU's parent unknown. Migration checks
observe target QOM and output state before target MMIO or `cont`, including
an in-flight RPMh completion that still cannot invent an NPU supply.

The new CI plan is 80 component/parser gates plus five Linux integration
gates. Separate active and runtime-suspended Linux UFS transfers, A77 and
PCI UFS regressions remain required. Source validation and test registration
are not a compiled pass. A matching compiled receipt and a new unchanged-input
native run are needed before claiming that the observed NPU abort is past.
