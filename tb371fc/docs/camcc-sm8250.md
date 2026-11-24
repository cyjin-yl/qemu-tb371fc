# Kona-v2 CAMCC digital reference

`qcom-sm8250-camcc` implements the source-defined, software-visible clock
protocol at the next observed original-kernel failure. It defaults to disabled;
the native diagnostic must opt in explicitly:

```sh
-global qcom-sm8250-camcc.enabled-reference=on
```

The QOM object is `/machine/camcc`. Its 236 protocol words occupy 944 bytes
in 74 exact contiguous windows. A transparent 64-KiB container carries those
windows because SysBus has 32 export slots per device. The container has no
read/write callbacks, backing RAM or default response. Holes remain unassigned,
and the six camera GDSCR words keep their independent owner and VMState.

This reference does not implement camera capture, IFE/IPE/BPS execution,
sensor links, imaging DMA, physical analog calibration, voltage/frequency
safety, or retained engine contents. Passing its component tests is not an
Android boot verdict.

## Actual trigger and selected variant

The preceding source `4cc9dee07f542697c129b60d3ff5162aa0f71a53` passed
[formal CI run 45414535386](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45414535386):
81 component gates and five Linux integrations, with separate A77 and PCI UFS
checks. The inspected artifact QEMU SHA-256 was
`33da659249dab043671e0de0d04326ba9a52c6a53dedffcf7b1e6f42130d6808`.
Its VIDEO gate passed 22 checks, three completed migrations, two profile
rejections and two single-bit corrupted-stream rejections. That result applies
to 4cc, before this CAMCC implementation.

The same-binary original-input run004 recovery1 registered all four VIDEO
GDSCs with `pm8150a_s4_level`: mvs0 at 2.078649 s, mvs0c at 2.081513 s,
mvs1 at 2.084333 s, and mvs1c at 2.087353 s. The first observed synchronous
external abort occurred at guest 2.090202 s on CPU5, PID85 `kworker/5:1`,
through `deferred_probe_work_func`, `cam_cc_kona_probe+0x168/0x378`,
`clk_lucid_pll_configure+0x40/0x320`, and `regmap_mmio_read32le+0x8`.
QEMU independently recorded the invalid read32 at physical `0x0ad00000`.
ESR was `0x96000010`, FAR `0xffffff8015ca0000`, and ELR
`0xffffff800884e968`. This is a worker Oops, not evidence of a PID1 panic.

The run stopped and cleaned up after 18.821 seconds at
2026-11-24T14:50:10.398975Z under the explicitly requested early-stop policy.
Original Image, ramdisk, full DT, boot arguments, AOP input and QEMU hashes
were unchanged. No original LUN was attached, and no Android userspace or
boot-complete event was observed. The printk SHA-256 was
`45d568a6a523c2784b2eaf0f33bf6aaa92d1be8d20447c3246061aab1e10fe24`;
the QEMU log SHA-256 was
`66be2a53c66fbe9f1f85c46535248359323d1af34d7524c4df7a67fd4be3d855`.
The [bounded native receipt](../evidence/video-20261124-e1a9/native-frontier-after-video.json)
and [artifact audit](../evidence/video-20261124-e1a9/ci-artifact-audit-4cc9dee.json)
preserve that preceding checkpoint separately.

The [actual inventory](../evidence/expansion-20261123/android-device-inventory.json)
binds `/soc/qcom,camcc@ad00000` to original DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The complete node has no omitted or rejected properties. Its compatible is
`qcom,camcc-kona-v2`, followed by `syscon`, with `cc_base` at
`0x0ad00000/0x10000`, phandle108, and one clock/reset argument cell.
The address translates identically through `/soc`.

| Actual dependency | Meaning retained by this increment |
| --- | --- |
| `clocks = <22 11>`, `cfg_ahb_clk` | Existing GCC camera AHB dependency; no new frequency is invented |
| `vdd_mx-supply = <105>` | RPMh `mx.lvl` / `pm8150a_s3_level`; a supply is not a clock carrier |
| `vdd_mm-supply = <104>` | RPMh `mmcx.lvl` / `pm8150a_s4_level`; existing GDSC parent wiring remains independent |

## Pinned sources and reproducible geometry

All eight primary inputs are fixed to OnePlusOSS kernel commit
[`94304e12a3509eac7b3716bf574ce0b1fcaacbae`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/94304e12a3509eac7b3716bf574ce0b1fcaacbae).
Their full SHA-256 values are committed in the
[generator](../scripts/generate_camcc_sm8250.py) and the
[register manifest](camcc-sm8250-registers.json).

| Source | Contract used |
| --- | --- |
| [camcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/camcc-kona.c) | Variant fixup, five PLLs, six postdividers, 33 RCGs, 66 branches, one read-only divider, six resets and 111 clock binding entries |
| [qcom,camcc-kona.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,camcc-kona.h) | Clock IDs 0–110 and reset binding order |
| [clk-alpha-pll.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-alpha-pll.c) | Distinct Lucid and Zonda offsets, configure/enable/set-rate sequences, output gates and postdivider tables |
| [clk-rcg2.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rcg2.c) | CMD/CFG/HID/mux and encoded M/N/D calculations |
| [clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c) | Local enable bit0 and halt status; no CAMCC branch is HALT_VOTED |
| [clk-regmap-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-regmap-divider.c) | Read-only SBI divider operation |
| [clk-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/clk-divider.c) | Generic divider rate rounds upward, unlike the PLL postdivider's floor |
| [clk-rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rpmh.c) | Both `bi_tcxo` names use `xo.lvl` from `xo_board` |

The generation gate verifies all eight source digests and regenerates both
`hw/misc/qcom-sm8250-camcc-data.inc` and the JSON manifest byte for byte.
The driver's `max_register = 0xe004` is a regmap bound, not a register whitelist.
The actual modeled register maximum is 0xc188. Opaque Lucid STATUS at +0x30
and Zonda STATUS at +0x38 are excluded, as are every other undeclared word.
Camera GDSCR offsets 0x7004, 0x8004, 0x9004, 0xa004, 0xb004 and 0xc144 are
excluded from the CAMCC table.

There are five input Clock objects, in migration order: `bi_tcxo`,
`bi_tcxo_ao`, `core_bi_pll_test_se`, `chip_sleep_clk`, and
`cam_cc_pll2_out_aux2`. Only the first two connect to the existing board XO
active carrier. The test, sleep, and unregistered aux2 parents remain zero
until an explicit provider supplies them. In particular, aux2 is not silently
aliased to PLL2, and no 32-kHz rate is guessed. Logical RPMh sleep votes and
physical rail behavior are outside this increment.

## PLL protocols

The v2 source keeps PLL0, PLL1, PLL3 and PLL4 as Lucid and PLL2 as Zonda.
Replacing all five with Lucid would apply the wrong offsets, fraction width
and meaning of status bit29. The v2 Zonda configuration's `cal_l` member is
not written by `clk_zonda_pll_configure`; the model does not alias it to ALPHA.

| Property | Lucid PLL0/1/3/4 | Zonda PLL2 |
| --- | --- | --- |
| Fraction | 16 bits at +0x40 | 32 bits at +0x08 |
| CAL_L | +0x08, known recipe 0x44 | No CAL_L register |
| OPMODE | +0x38 | +0x28 |
| Known CONFIG / U / U1 | 0x20485699 / 0x2261 / 0x029a699c | 0x08200920 / 0x05008011 / 0 |
| Known USER_U / U1 | 0x805 / 0 | No such words |
| TEST / U / U1 | All zero | All zero; CFA is not supported |
| MODE writable mask | 0x00c00007 | 0x00000007 |
| Generated MODE feedback | LOCK31, ACK29, PCAL27 | LOCK31; FREQ_LOCK29 remains false in the known non-CFA recipe |
| Supported VCO range | 249.6 MHz–2 GHz | 595.2 MHz–3.6 GHz |

A valid RUN request captures the programmed factor and requires live XO,
RESET_N, a known recipe and an admissible VCO frequency. Lock takes 5000
virtual ns. Lucid UPDATE rising with UPDATE_BYPASS captures L/ALPHA once;
after 1000 ns it commits that tuple, asserts ACK, withdraws LOCK and starts
a 5000-ns relock interval. Repeated-high UPDATE and later shadow writes do
not overwrite the captured tuple. The old valid output may continue before
the latch commits. Clearing UPDATE cancels an uncommitted request; clearing
RESET_N clears calibration, captured work and pending deadlines.

Zonda requires BYPASSNL held for at least 1000 ns before RUN. An early RUN
does not become successful merely by waiting: software must retry standby
and RUN. During RUN a write to L captures the current ALPHA and L, withdraws
LOCK/output, settles for 5000 ns, then starts a 5000-ns lock interval. Another
L write captures again and restarts settling; ALPHA-only writes remain shadow
storage. Invalid retunes leave no successful output. UPDATE and UPDATE_BYPASS
are rejected on Zonda, and it never gains Lucid ACK or PCAL.

These delays are explicit digital ordering assumptions, not measured analog
timing. The fixed vendor Zonda disable helper adds the base twice on its first
MODE read; no alias is added to reproduce that source defect. Shutdown tests
exercise the source-defined output/bypass/reset/OPMODE protocol at PLL2's
correct addresses.

Rates use actual XO and the Q16/Q32 factor. The upper VCO boundary admits
only the single rounded-up factor permitted by the driver's 500-Hz margin,
not an arbitrary above-range band. The normal 19.2-MHz vectors give PLL0
1.2 GHz, PLL1 600 MHz, PLL2 1.44 GHz, and PLL3/4 699,999,902 Hz.

Output control and grouped USER gates must both be enabled. The six sourced
postdividers accept only selector1/divide2, except PLL0 odd selector3/divide3.
Selector0 may be stored as a collapsed reference value but produces no
postdivider frequency: the trailing empty table entry is not a divide1
contract. Other nonzero selectors and partial grouped outputs reject atomically.

## RCGs, branches, divider and resets

All 33 RCGs have CMD and CFG; exactly ten have 8-bit M/N/D: CCI0/1,
MCLK0–6 and slow AHB. CMD writes mask3 and exposes ROOT_OFF31. CFG uses
mask0x10371f. Every source mux selector is explicit in the generated table.
An undefined selector or unsupported mode never acknowledges UPDATE.

Mode0 bypasses MND and may preserve old M/N/D storage, matching the driver's
integer-rate entries. Bounded dual-edge mode2 requires `0 < M < N <= 255`,
where `N = (~Nreg & 255) + M` and `Dreg = (~N & 255)`. Other modes have no
accepted reference protocol. Valid UPDATE rising captures CFG/M/N/D together
and commits after 1000 ns. Repeated-high UPDATE and shadow changes leave
that snapshot untouched. A known mux selector may commit while its parent
is stopped or unknown; that grants configuration completion, not frequency.

HID division floors first, followed by the M/N fraction. For example, v2
MCLK 24 MHz selects raw PLL2 (selector5), HID19, mode2, M1, Nreg0xfa and
Dreg0xf9. The 68,571,429-Hz table entry computes 68,571,428 Hz with HID1,
M1, Nreg0xeb and Dreg0xea; the model reports the computed integer value.

All 66 branches use local enable bit0. The source has no CAMCC HWCG fields
or HALT_VOTED branches, so no remote-vote GPIO, retention bit or unknown
remote vote is invented. `cam_cc_core_ahb_clk` is declared HALT_DELAY;
the other 65 use HALT. Feedback follows actual derived frequency. Branch
demand propagates through the read-only SBI divider to its IFE0 root.

SBI word0x9010 has width3, shift0 and reference cold value0/divide1.
Its rate uses the generic divider's ceiling rule, `ceil(parent/(field+1))`.
Every MMIO write to that word rejects, including zero. Under QTest only,
`qtest-sbi-divider` supplies an explicit 0–7 fixture. The six BCR words store
only bit0 and drive six `reset` GPIOs in binding order; resetting an engine's
functional datapath is outside this model.

## Inspection, reset and migration

Every `reg.<four-digit-hex-offset>` property exposes stored control without
generated status. `clk.<Linux-clock-name>.hz` exposes the derived rate, and
all 111 outputs are actual QEMU Clock objects. PLL properties expose active
and pending L/ALPHA, lock/ACK/calibration flags, pending flags and signed
lock/latch/bypass timestamps. RCG properties expose active and pending
CFG/M/N/D plus their pending flag and deadline. All getters are pure: they
do not advance virtual time, consume a deadline or repair state.

Only QTest exposes `qtest-<input-name>-hz` fixture setters; connected input
Clocks reject replacement. Period quantization must remain representable by
QEMU's unsigned Hz interface, so an input that rounds beyond UINT32_MAX is
rejected before storage; incoming periods are checked with 64-bit arithmetic.
All fixtures are absent under TCG. Device reset
clears stored registers and pending work, preserves provider input Clocks,
and republishes outputs during reset hold.

The independent `qcom-sm8250-camcc` VMState is version1/minimum1 and is
2579 bytes with default Clock ratios. Its exact order is:

1. Eleven equality-guarded u32 fields: profile, identity0x43414d32,
   registers236, clocks111, inputs5, PLLs5, RCGs33, branches66, resets6,
   windows74 and mapped bytes944; then equality-guarded u64 base0x0ad00000.
2. 236 stored u32 words in ascending offset order, including the SBI fixture.
3. Four arrays of five u32: active L, active ALPHA, pending L, pending ALPHA.
4. Eight arrays of 33 u32: active CFG/M/N/D, then pending CFG/M/N/D.
5. Three arrays of five signed64 timestamps: lock, latch and bypass; then
   33 RCG signed64 deadlines and u64 last observed XO Hz.
6. Three arrays of five booleans: locked, ACK and calibrated.
7. Five input Clock periods in the input order above.

Output Clocks are derived rather than serialized. Pre-load withdraws stale
outputs and cancels the shared scheduler. Post-load validates stored masks,
identity, profile, PLL-kind rules, captured configurations, deadlines,
input periods and default Clock ratios before deriving output periods and
reset lines. It schedules the original deadlines without consuming them or
creating new ones. Invalid runtime state rejects with
`qcom-sm8250-camcc: invalid migrated ...`; equality guards retain QEMU's
normal mismatch diagnostic.

## Acceptance boundary

The new compiled gate requires 41 distinct checks, including default-off
rejection, paused-TCG observers, all sparse ownership boundaries, all sourced
muxes, separate PLL protocols, MND, actual RPMh XO propagation, three
completed migrations, two profile rejections and two single-bit corrupted
streams. Four original streams and both mutations must be retained and
decoded against the full 2579-byte oracle. Camera VMState remains independent.

The separate generation gate adds two exact comparisons. Together they extend
the plan from 81 to 83 component gates plus the existing five Linux gates.
Source regeneration has been checked locally; compiled QTest, migration and
native acceptance require new receipts for this source. No 4cc result is
reused as a pass for CAMCC.
