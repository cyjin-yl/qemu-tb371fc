# Kona v2 VIDEOCC digital reference

`qcom-sm8250-videocc` implements the bounded clock-controller protocol at the
next observed original-kernel failure. The QOM object is `/machine/videocc`.
The device defaults to disabled and requires an explicit diagnostic option:

```sh
-global qcom-sm8250-videocc.enabled-reference=on
```

The 55 supported words occupy 220 bytes in 26 exact contiguous leaves within
the actual 64-KiB resource. A transparent container has no handlers or storage
for holes. The existing independent VIDEO GDSCR words at offsets
`0xbf8/0xc98/0xd18/0xd98`, their mmcx parent fanout and their 142-byte v1 state
remain unchanged. This increment models clock requests, captured settings,
derived rates, reset outputs and migration. Video codecs, CVP, DSP firmware,
frame processing, DMA and physical analog PLL behavior remain outside it.

## Actual preceding result and trigger

Exact source `f209f658316ada5ddd5c245f0be6c2c0eca271bd` passed
[formal CI 45434451557](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45434451557):
85 components, five Linux integrations, both Linux UFS checkpoint modes and
the independent A77/PCI UFS job. Its board binary SHA-256 is
`0738a1910ef86c8ccc403ba100ae2094de573d4f0c11fdff9877e1b9fa798d6c`.
The [independent DISPCC audit](../evidence/dispcc-20261124-e1a9/ci-artifact-audit-f209f65.json)
checked the actual ZIP, all six DISPCC migration files, complete 1676-byte
DISPCC plus 64-byte MDSS states and all 61 clock rates. That qualification
belongs to f209, before this VIDEOCC device exists.

The same binary's [original-input run006](../evidence/dispcc-20261124-e1a9/native-frontier-after-dispcc.json)
reported `Registered Display CC clocks` at guest 3.503753 seconds. The
parentless critical DISPCC XO branch produced a warning, but registration
continued; no missing carrier was invented to suppress that warning.
The first fatal then occurred at guest 3.508680 seconds on CPU4, PID78
`kworker/4:1`, while reading physical `0x0abf042c` as a 32-bit word:

```text
video_cc_kona_probe+0x274/0x378
  clk_lucid_pll_configure+0x40/0x320
    regmap_mmio_read32le+0x8/0x20
```

QEMU recorded the physical rejection and a following relative-offset
`0x42c` rejection for the same access. Data Abort ESR was `0x96000010`, FAR
`0xffffff8015cf042c`, ELR `0xffffff800884e968`. This was a deferred-probe
worker Oops, not a PID1 panic. Run006 stopped after 16.449 wall seconds with
normal QMP shutdown, runner exit2 and QEMU exit0. Original Image, ramdisk,
complete DT, boot arguments, AOP, symbols and QEMU hashes were unchanged.
No original LUN was attached and no Android userspace or boot completion was
observed. The two identical 61,033-byte printk captures have SHA-256
`748f4a5c57d1c5c047f7ac79d0f9a4c5631cc33dac69f3e3a765ac8da692b318`;
the 627,204-byte QEMU log has SHA-256
`e6ec8e61564b22efab72088e983d98c000fffd561ce2e590dd755513aaf8d070`.

## Actual DT and fixed primary sources

The complete [public controller node](../evidence/videocc-20261124-e1a9/actual-dt-videocc-node.json)
comes from the existing original-DT inventory, bound to DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
There are no omitted or rejected properties.

| Property | Actual value |
| --- | --- |
| Node | `/soc/qcom,videocc@abf0000` |
| Compatible | `qcom,videocc-kona-v2`, `syscon` |
| Resource | `cc_base`, physical `0x0abf0000`, size `0x10000` |
| Provider | Phandle106, one clock cell, one reset cell |
| Named input | `cfg_ahb_clk = <22 205>` from GCC |
| Supplies | MX phandle105 and MMCX phandle104 |

The supply references are not clock carriers or implicit VIDEO GDSC parents.
All ten [generator inputs](../scripts/generate_videocc_sm8250.py) are pinned
to OnePlusOSS kernel commit
[`94304e12a3509eac7b3716bf574ce0b1fcaacbae`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/94304e12a3509eac7b3716bf574ce0b1fcaacbae).
Their SHA-256 values and source line provenance are preserved in the
[register manifest](videocc-sm8250-registers.json).

| Primary input | Protocol used |
| --- | --- |
| [videocc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/videocc-kona.c) | Actual v2 match, initial and v2 PLL recipes, 19 clocks and seven reset mappings |
| [qcom,videocc-kona.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,videocc-kona.h) | Clock IDs0–18 and reset IDs0–6 |
| [clk-alpha-pll.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-alpha-pll.c) | Lucid configure, enable, disable, UPDATE and Q16 factors |
| [clk-rcg2.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rcg2.c) | Integer CMD/CFG, HID arithmetic and force/configure/unforce callbacks |
| [clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c), [clk-branch.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.h) | Branch request/halt and the four HALT_VOTED declarations |
| [clk-regmap-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-regmap-divider.c), [clk-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/clk-divider.c) | Four read-only two-bit fields and rounded-up output rate |
| [clk-rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rpmh.c) | The two `bi_tcxo` carriers share `xo.lvl` from `xo_board` |
| [reset.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/reset.c) | Reset assertion/deassertion changes only its mapped bit |

`max_register = 0xf4c` is a regmap bound, not an address whitelist. The
highest modeled word is `0xf10`. Opaque PLL STATUS at `0x45c/0x800`, every
M/N/D hole, the four GDSCRs and all undeclared words are excluded from this
controller's handlers.

| Register family | Distinct words | Ownership |
| --- | ---: | --- |
| Two Lucid PLLs |28| Fourteen supported words each |
| Five RCG CMD/CFG pairs |10| No M/N/D words |
| Eight branch controls |8| Two also contain a separately mapped ARES bit |
| Four read-only dividers |4| Explicit two-bit configuration |
| Five distinct BCR controls |5| Separate from four neighboring GDSCRs |
| **Total** |**55**| **220 bytes in 26 leaves** |

## Carrier and reset-state policy

Input Clock order is `bi_tcxo`, `bi_tcxo_ao`, `core_bi_pll_test_se`,
`chip_sleep_clk`. The first two connect to the existing board XO. They share
the active carrier; separate physical sleep-vote behavior is not modeled by
this connection. The last two remain zero until an explicit provider supplies
a rate. A 32000-Hz sleep rate in a software frequency table is not evidence
for substituting the separate MPM counter's 32768-Hz carrier.

The enabled reference uses a zero register reset image. In particular, each
RO divider starts at field0 (divide by one). This is an explicit digital
reference reset choice, not an observation of the tablet's strap or firmware
configuration. QTest-only fixtures may supply each other declared field value.
No clock request is seeded merely because software labels a clock critical;
the driver must issue its actual requests. Carrier inputs and explicit remote
votes are provider-owned and survive a device reset.

## Lucid PLLs and captured updates

PLL0 begins at `0x42c`, PLL1 at `0x7d0`. Both are Lucid; no exported
postdivider or Zonda protocol is introduced. The actual v2 probe first applies
both generic configurations, then rewrites PLL1 with its v2 recipe:

| Recipe | L / ALPHA | CONFIG_U1 | Rate at 19.2 MHz |
| --- | --- | --- | ---: |
| PLL0 | `0x25 / 0x8000` | `0x029a699c` |720000000 Hz|
| PLL1 initial | `0x29 / 0xfaaa` | `0x029a699c` |805999804 Hz|
| PLL1 v2 | `0x2b / 0xc000` | `0x329a699c` |840000000 Hz|

Common fields are CAL_L0x44, CONFIG0x20485699, CONFIG_U0x2261, USER_U0x805,
with USER_U1 and TEST words zero. Configuration storage admits the source's
write sequence; lock/ACK qualification requires a known recipe. PLL0 does
not accept PLL1's v2 CONFIG_U1 for a successful lock. MODE write mask is
0x00c00007 with generated LOCK31, ACK29 and PCAL27. L/CAL_L/ALPHA are16-bit,
OPMODE is0/1, and USER supports grouped output selection0/7. Output requires
the whole group7, OUTCTRL, RESET_N and a qualified locked RUN state.

Valid RUN captures L/ALPHA. The reference lock interval is5000 virtual ns.
A rising UPDATE with RESET_N and UPDATE_BYPASS captures the factors once;
1000ns later it commits them, raises ACK and begins a5000ns relock. Later
shadow writes or repeated-high UPDATE do not overwrite the pending capture.
Clearing UPDATE or UPDATE_BYPASS cancels the reference pending latch and ACK;
this cancellation policy is not a claimed measured hardware transaction.
RESET_N low clears captures, deadlines, LOCK/ACK and PCAL. Standby withdraws
active output while retaining completed calibration.

Rate uses `XO * L + floor(XO * ALPHA / 65536)`. Only249.6MHz–2GHz factors
qualify, with the single quantized upper endpoint permitted by the fixed
driver's500-Hz rounding margin. Unknown carrier, unsupported recipe or an
inadmissible factor cannot gain LOCK/ACK by waiting. Timings are declared
digital ordering assumptions, not analog measurements.

## Integer roots, dividers and shared controls

| RCG, in source/state order | CMD | Declared selectors | HW control / safe config |
| --- | --- | --- | --- |
| AHB |0xbd4|0 XO AO, 7 test|Yes / yes|
| MVS0 |0xb94|0 XO, 1 PLL0, 7 test|Yes / yes|
| MVS1 |0xbb4|0 XO, 1 PLL1, 7 test|Yes / yes|
| Sleep |0xef0|0 sleep, 7 test|No / no|
| XO |0xecc|0 XO AO, 7 test|No / no|

CMD accepts UPDATE0/ROOT_EN1 and reports ROOT_OFF31 from the derived rate.
The first three CFG masks are0x10371f; the remaining two use0x371f and reject
bit20. All five declare MND width0. Only integer mode0 qualifies for UPDATE
completion; unsupported modes, reserved controls and mux holes cannot ACK.
A known selector whose carrier is unknown may latch its configuration, while
its output remains zero. Valid rising UPDATE captures CFG once and commits
after1000ns; repeated-high writes and later shadow writes preserve the capture.

ROOT_EN or downstream branch demand makes a root active. For HID0 the rate
is the parent rate; otherwise it is `floor(parent * 2 / (HID + 1))`.
Safe-config and critical flags describe driver policy, not autonomous device
requests. The actual clock tree is recomputed from committed configuration
and live carriers, without artificial HALT completion.

All four divider fields have width2, shift0 and RO ops. Their rate is
`ceil(parent / (field + 1))`. Every MMIO write rejects, including a write of
zero. The MVS1C divider feeds both MVS1C and MVS1_DIV2 branches; demand from
either must reach MVS1. A name containing `div2` does not force field1.

| Divider order | Offset | Parent |
| --- | --- | --- |
| MVS0 |0xd54|MVS0 root|
| MVS0C |0xc54|MVS0 root|
| MVS1 |0xdd4|MVS1 root|
| MVS1C |0xcf4|MVS1 root|

Four explicit `remote-vote` GPIOs OR with local branch bit0 in binding order:
MVS0 at0xd34, MVS1 at0xdb4, MVS1_DIV2 at0xdf4, MVS1C at0xcd4. Their default
is low; no remote-firmware vote is invented. The other four branches have no
remote input. CLK_OFF31 follows the resulting clock rate.

Reset outputs follow binding IDs0–6: e54.bit0, d14.bit0, c34.bit2,
bf4.bit0, d94.bit0, cd4.bit2 and c94.bit0. The c34/cd4 words therefore have
write mask5 and a single handler, preserving branch bit0 during ARES bit2
changes and vice versa. ARES drives its separate reset signal; this model
does not infer an undocumented reset-to-carrier coupling. No codec engine is
attached to these reset outputs.

## Migration and acceptance boundary

The independent `qcom-sm8250-videocc` VMState is v1/minimum1, exactly474 bytes:

| Wire group, in order | Bytes |
| --- | ---: |
| Thirteen equal-checked32-bit geometry/identity fields and64-bit base |60|
| 55 sorted stored register words |220|
| Active/pending L and ALPHA arrays for two PLLs |32|
| Active/pending CFG arrays for five integer RCGs |40|
| Two PLL lock and two latch deadlines |32|
| Five RCG deadlines |40|
| Last observed XO frequency |8|
| LOCK/ACK/PCAL Boolean arrays |6|
| Four remote-vote Booleans |4|
| Four input Clock periods |32|
| **Total** |**474**|

No unused M/N/D arrays or derived output Clock values are serialized. Preload
withdraws prior outputs and deletes the target timer. Postload checks profile,
geometry, stored masks, input ratio/range, captured integer configuration,
PLL consistency and deadline bounds. It derives outputs and schedules the
original remaining deadlines without consuming time. Pure QOM getters expose
stored words, captured values, status/deadlines, divider fields, votes and
19 calculated rates. They cannot repair or complete pending work. QTest-only
input/divider setters are absent in ordinary TCG; connected XO overrides and
rates whose period would quantize beyond u32 are rejected.

The new gate requires41 groups:31 ordinary cases, three independent runs,
three completed migrations, two profile mismatch rejections and two deliberate
single-bit stream rejections. Four original streams and two mutants must be
present. The literal oracle checks the complete474-byte section, independent
VIDEO GDSC142 bytes, all19 rates, and first destination QOM before device MMIO
or `cont`. The geometry case enables both controllers and verifies all four
existing GDSCR owners through actual accesses. Migrations cover a prewarmed
destination, two pending PLL locks, two pending latches, all five captured RCGs,
unknown-input replacement, four remote votes and shared ARES fields.

A separate two-check generation gate verifies ten primary hashes and
reproduces both data files byte for byte. Existing camera/MDSS/NPU/VIDEO tests
still require rejection at the four adjacent BCRs; those accesses now must
reach the independently disabled VIDEOCC owner rather than an unassigned hole.
Their original suite counts and remaining negative checks are preserved.
This creates an87-component +5-Linux plan. Host generation, syntax and plan
checks are recorded separately; actual compiled QTest, migration and the
next original-input run require a new exact-source binary. Neither f209's
prior success nor this source contract establishes VIDEOCC/native Android
completion.
