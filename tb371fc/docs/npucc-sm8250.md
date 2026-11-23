# SM8250 / Kona v2 NPUCC digital reference profile

The `qcom-sm8250-npucc` device models three Lucid PLLs, two
source-defined divide-by-one outputs, one bounded CRC clock divider path,
six independent root clock generators, 33 clock branches and seven reset
outputs. It does not execute NPU or QDSP6 instructions, calculate CRC data,
emulate analog tuning, power the NPU core, or implement secure firmware.

The device is always present at `/machine/npucc`. The default
`enabled-reference=false` profile reports MMIO transaction errors and
produces no reference output clock. Enable the digital profile explicitly:

```text
-global qcom-sm8250-npucc.enabled-reference=on
```

This does not initialize a PLL rate, CRC configuration, branch request or
power rail. Its zeroed digital reset state and transition delays are explicit
reference assumptions, not a capture of the tablet's bootloader or measured
silicon timing. The original kernel and device tree need no modification to
access the mapped registers.

## Fixed primary sources

All vendor inputs below are from the official OnePlus SM8250 kernel at
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`:

| Input | Byte SHA256 |
| --- | --- |
| [npucc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/npucc-kona.c) | `6c5e241d61d685e0bb14bcfcebd689d36d23d0c803a4bf621105bae03d1ade60` |
| [qcom,npucc-kona.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,npucc-kona.h) | `6b5b44c191c3dc131be3abfe746f5a321bde60e006a7ca6fc8527e897b8d987a` |
| [clk-alpha-pll.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-alpha-pll.c) | `cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac` |
| [clk-rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rpmh.c) | `75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76` |

The same pinned tree's [clk-rcg2.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rcg2.c)
defines the shadow update and HID divider calculations;
[reset.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/reset.c) defines reset-bit read/modify/write
operations. No upstream NPUCC controller source is asserted as corroboration:
the checked Linux 6.12.114 Qualcomm clock directory has no
`npucc-sm8250.c`.

The original DT uses `qcom,npucc-kona-v2` and three resources:
cc `0x09980000/0x10000`, qdsp6ss `0x09800000/0x10000`, and
qdsp6ss_pll `0x09810000/0x10000`. The vendor v2 fixup substitutes
HM0/HM1 frequency and voltage tables; it changes no register addresses or
PLL configuration recipe. Its listed HM rates are nominal software requests,
not evidence that a parent is actually running.

## Sparse address ownership

Only 95 aligned 32-bit registers are implemented. The following 26 windows
are offsets and sizes within the three resources, not whole-resource RAM:

| Resource | Offset | Size |
| --- | --- | --- |
| cc | `0x0` | `0x44` |
| cc | `0x400` | `0x44` |
| cc | `0x1000` | `0x4` |
| cc | `0x1010` | `0x8` |
| cc | `0x1030` | `0x4` |
| cc | `0x1060` | `0x8` |
| cc | `0x1074` | `0xc` |
| cc | `0x1090` | `0x24` |
| cc | `0x10b8` | `0x4` |
| cc | `0x10c0` | `0x24` |
| cc | `0x10f0` | `0x4` |
| cc | `0x1100` | `0x8` |
| cc | `0x1110` | `0x4` |
| cc | `0x1130` | `0x4` |
| cc | `0x1140` | `0x8` |
| cc | `0x1150` | `0x4` |
| cc | `0x11a0` | `0x8` |
| cc | `0x11b0` | `0x8` |
| cc | `0x1200` | `0x4` |
| cc | `0x1210` | `0x10` |
| cc | `0x1224` | `0x4` |
| cc | `0x1234` | `0x8` |
| cc | `0x1400` | `0x8` |
| cc | `0x1410` | `0x4` |
| qdsp6ss | `0x28` | `0x8` |
| qdsp6ss_pll | `0x0` | `0x44` |

Unlisted words inside a window return transaction errors. In particular,
each PLL's offsets `0x30/0x34/0x3c` remain unsupported; the listed
PLL STATUS word has no fabricated status bits. Lucid PCAL_DONE is MODE
bit 27. Subword, unaligned and unsupported-bit writes fail without changing
the target register. Generated read-only bits are ignored on otherwise
valid read/modify/write operations.

The NPU core GDSC at cc `+0x1004` has no model in this change and is
outside every window. The model does not silently acknowledge that power
domain. Existing GPUCC, GPU CX/GX and GCC mappings remain separate.

## PLL and divider behavior

PLL0 occupies cc `+0`, PLL1 cc `+0x400`, and the Q6SS PLL
qdsp6ss_pll `+0`. Each exposes MODE +0, L +4, CAL_L +8,
USER +c/+10/+14, CONFIG +18/+1c/+20, TEST +24/+28/+2c, OPMODE +38,
and 16-bit ALPHA +40. L and CAL_L are also limited to 16 bits.

All three known recipes use CAL_L `0x44`, USER_U `0x805`,
USER_U1 zero, CONFIG `0x20485699/0x2261/0x329a699c`, and zero
TEST words. Other CONFIG/TEST words remain readable after writes but cannot
produce LOCK or a latch ACK. The NPU CONFIG_U1 is intentionally distinct
from the GPUCC recipe.

| PLL | Vendor L | Vendor ALPHA | Actual rate with 19.2 MHz XO |
| --- | --- | --- | --- |
| npu_cc_pll0 | `0x1f` | `0x4000` | 600,000,000 Hz |
| npu_cc_pll1 | `0x4e` | `0x2000` | 1,500,000,000 Hz |
| npu_q6ss_pll | `0xf` | `0xa000` | 300,000,000 Hz |

USER accepts the source's grouped disable 0 and grouped enable 7.
Individual output selections 1 through 6 are outside this finite profile
and are rejected atomically, rather than reported as physically stopped.
This is not a claim that silicon main output requires its auxiliary outputs.
PLL0/1 retain the postdivider field geometry at USER [11:8], but their
source tables contain only selector 0, divide 1. Other selections fail
without changing USER. The Q6SS PLL has no corresponding postdivider here.

MODE supports OUTCTRL bit 0, BYPASSNL bit 1, RESET_N bit 2, UPDATE bit 22
and UPDATE_BYPASS bit 23. OPMODE accepts standby 0 and run 1. BYPASSNL is
not a Lucid lock prerequisite. The reference state machine generates
PCAL_DONE bit 27, ACK bit 29 and LOCK bit 31.

A valid reset/run transition captures L/ALPHA. LOCK and PCAL_DONE become
true after 5,000 virtual ns only if the actual XO is nonzero, the known
recipe holds, RESET_N is high and OPMODE is run. Output also requires
OUTCTRL and grouped USER enable. Frequency is the exact integer result:

```text
frequency_hz = floor(xo_hz * (L * 65536 + ALPHA) / 65536)
```

The VCO lower bound is 249.6 MHz. At the upper bound, the driver clamps
the nominal request to 2 GHz and subsequently rounds the Q16 factor up
again in set_rate, allowing its 500 Hz quantization margin. The model
accepts at most `ceil(2e9 * 65536 / xo_hz)` and also enforces the
500 Hz limit. At 19.2 MHz, L `0x68`, ALPHA `0x2aab`
therefore yields the supported 2,000,000,097 Hz. The next Q16 value
`0x2aac` is outside the quantized nominal endpoint and is rejected
for lock/latch even though its frequency is less than 2 GHz + 500 Hz.
No result is rounded or clipped to a nominal rate.

UPDATE's rising edge captures a separate L/ALPHA snapshot when RESET_N,
UPDATE_BYPASS, the recipe and frequency are valid. The previous active
output remains until the 1,000 ns latch deadline. The snapshot then becomes
active, ACK is set, and a running PLL starts a further 5,000 ns lock
interval. The interval is anchored to the original latch deadline even
if its callback is dispatched late. A newly observed XO change still
requires its own full lock interval. Later shadow writes cannot alter
the pending snapshot. UPDATE remains set until software clears it;
clearing UPDATE also clears ACK. Withdrawing RESET_N or UPDATE_BYPASS
cancels a pending latch. Invalid requests never generate ACK.

The two `npu_cc_pll*_out_even` outputs are divide 1, despite their
names. CRC is a separate divide-by-two path. The known custom configure
writes are cc +11a4=15011, +11a0=800000, +11b4=15011, +11b0=800000
(all hexadecimal). Each word accepts zero or its specified value;
unsupported values fail atomically. Only the complete vector qualifies
the reference `npu_cc_crc_div` output. Absence or removal of CRC
configuration does not affect PLL0 LOCK, main output or out_even.
This is a bounded clock-path assumption, not a model of the undocumented
CRC engine or a fabricated completion bit.

## Roots and input clocks

All six RCGs have independent shadow CFG, captured pending CFG, active CFG,
and 1,000 ns UPDATE deadlines:

| Index | Root | Resource CMD/CFG |
| --- | --- | --- |
| 0 | npu_cc_core_clk_src | cc +1010/+1014 |
| 1 | npu_cc_lmh_clk_src | cc +1060/+1064 |
| 2 | npu_cc_cal_hm0_clk_src | cc +1100/+1104 |
| 3 | npu_cc_cal_hm1_clk_src | cc +1140/+1144 |
| 4 | npu_cc_xo_clk_src | cc +1400/+1404 |
| 5 | npu_dsp_core_clk_src | qdsp6ss +28/+2c |

CFG mask is `0x0010371f`: HID [4:0], source [10:8], mode [13:12]
and HW control bit 20. All six source roots have mnd_width zero; nonzero
mode is outside the supported profile. HID 0 bypasses; HID >=1 applies
the exact integer factor 2/(HID+1). HW control is retained, while explicit
CMD ROOT_EN or a downstream branch request supplies reference demand.

| Root group | 0 | 1 | 2 | 4 | 5 | 7 |
| --- | --- | --- | --- | --- | --- | --- |
| Core, LMH | XO | PLL1 even | PLL0 even | GCC main | GCC div | Test input |
| HM0, HM1 | XO | PLL1 even | CRC /2 | GCC main | GCC div | Test input |
| XO | XO AO | unknown | unknown | unknown | unknown | Test input |
| DSP | XO | unknown | Q6SS PLL | unknown | unknown | Test input |

Selectors 3 and 6 are unknown for all roots. A valid rising UPDATE commits
its captured configuration after 1,000 ns and clears UPDATE. Unsupported
source encodings or mode leave UPDATE busy and the old active configuration
intact; software must clear and reassert UPDATE after correction. A valid
parent whose actual frequency is zero can latch configuration, but ROOT_OFF
remains set and output remains zero. A configuration latch is not a claim
that the parent is running.

The vendor HM0 dependent operation calls software to change HM1 as a
second operation. No MMIO write automatically copies one root into the other.
The source explicitly parents all eight HM0/HM1 branches to HM0, including
the branches named HM1; the model preserves that declaration.

Twelve actual input Clock objects are exposed. The board connects
`bi_tcxo` and `bi_tcxo_ao` to the same existing XO active carrier:
the pinned RPMh definitions use the same xo.lvl resource, xo_board parent
and divide-by-two factor. This preserves actual RPMh carrier changes but
does not model independent active-only versus SLEEP vote allocation.
The two GCC inputs `gcc_npu_gpll0_clk_src` and
`gcc_npu_gpll0_div_clk_src` are connected to GCC's actual outputs,
not copied constant rates.

The remaining inputs default to zero and are deliberately unconnected:
`core_bi_pll_test_se`, `qdss_qmp_clk`,
`npu_llm_temp_clk`, `npu_llm_curr_clk`,
`npu_s2p_clk`, `gcc_npu_noc_axi_clk`,
`gcc_npu_noc_dma_clk`, and `npu_cc_noc_ahb_clk_parent`.
Similar names do not establish a GCC alias or a physical frequency.
AOP QDSS votes remain logical service requests, not Hz.
GCC NPU CFG's separate unknown parent is not forced on by this device.

## Branch votes and reset outputs

Every branch combines local enable bit 0 with its named GPIO
`remote-vote[index]`. OFF bit 31 reflects actual output frequency:
a request with an unknown or stopped parent does not produce a live clock.
The indices and source-declared routes are:

| Remote index | Linux name | cc offset | Parent |
| --- | --- | --- | --- |
| 0 | `npu_cc_atb_clk` | `0x10d0` | `qdss_qmp_clk` |
| 1 | `npu_cc_bto_core_clk` | `0x10dc` | `npu_cc_xo_clk_src` |
| 2 | `npu_cc_bwmon_clk` | `0x10d8` | `npu_cc_xo_clk_src` |
| 3 | `npu_cc_cal_hm0_cdc_clk` | `0x1098` | `npu_cc_cal_hm0_clk_src` |
| 4 | `npu_cc_cal_hm0_clk` | `0x1110` | `npu_cc_cal_hm0_clk_src` |
| 5 | `npu_cc_cal_hm0_dpm_ip_clk` | `0x109c` | `npu_cc_cal_hm0_clk_src` |
| 6 | `npu_cc_cal_hm0_perf_cnt_clk` | `0x10a0` | `npu_cc_cal_hm0_clk_src` |
| 7 | `npu_cc_cal_hm1_cdc_clk` | `0x10a4` | `npu_cc_cal_hm0_clk_src` |
| 8 | `npu_cc_cal_hm1_clk` | `0x1150` | `npu_cc_cal_hm0_clk_src` |
| 9 | `npu_cc_cal_hm1_dpm_ip_clk` | `0x10a8` | `npu_cc_cal_hm0_clk_src` |
| 10 | `npu_cc_cal_hm1_perf_cnt_clk` | `0x10ac` | `npu_cc_cal_hm0_clk_src` |
| 11 | `npu_cc_core_clk` | `0x1030` | `npu_cc_core_clk_src` |
| 12 | `npu_cc_dl_dpm_clk` | `0x1238` | `npu_cc_lmh_clk_src` |
| 13 | `npu_cc_dl_llm_clk` | `0x1234` | `npu_cc_lmh_clk_src` |
| 14 | `npu_cc_dpm_clk` | `0x107c` | `npu_cc_lmh_clk_src` |
| 15 | `npu_cc_dpm_temp_clk` | `0x10c4` | `npu_llm_temp_clk` |
| 16 | `npu_cc_dpm_xo_clk` | `0x1094` | `npu_cc_xo_clk_src` |
| 17 | `npu_cc_dsp_ahbm_clk` | `0x1214` | `npu_cc_core_clk_src` |
| 18 | `npu_cc_dsp_ahbs_clk` | `0x1210` | `npu_cc_core_clk_src` |
| 19 | `npu_cc_dsp_axi_clk` | `0x121c` | `gcc_npu_noc_axi_clk` |
| 20 | `npu_cc_dsp_bwmon_ahb_clk` | `0x1218` | `npu_cc_core_clk_src` |
| 21 | `npu_cc_dsp_bwmon_clk` | `0x1224` | `npu_cc_xo_clk_src` |
| 22 | `npu_cc_isense_clk` | `0x1078` | `npu_cc_lmh_clk_src` |
| 23 | `npu_cc_llm_clk` | `0x1074` | `npu_cc_lmh_clk_src` |
| 24 | `npu_cc_llm_curr_clk` | `0x10d4` | `npu_llm_curr_clk` |
| 25 | `npu_cc_llm_temp_clk` | `0x10c8` | `npu_llm_temp_clk` |
| 26 | `npu_cc_llm_xo_clk` | `0x1090` | `npu_cc_xo_clk_src` |
| 27 | `npu_cc_noc_ahb_clk` | `0x10c0` | Separate unknown input |
| 28 | `npu_cc_noc_axi_clk` | `0x10b8` | `gcc_npu_noc_axi_clk` |
| 29 | `npu_cc_noc_dma_clk` | `0x10b0` | `gcc_npu_noc_dma_clk` |
| 30 | `npu_cc_rsc_xo_clk` | `0x10e0` | `npu_cc_xo_clk_src` |
| 31 | `npu_cc_s2p_clk` | `0x10cc` | `npu_s2p_clk` |
| 32 | `npu_cc_xo_clk` | `0x1410` | `npu_cc_xo_clk_src` |

Seven `reset[index]` GPIO outputs follow binding order:

| Index | Reset | cc offset | Bit |
| --- | --- | --- | --- |
| 0 | CAL_HM0 | 10f0 | 0 |
| 1 | CAL_HM1 | 1130 | 0 |
| 2 | CORE | 1000 | 0 |
| 3 | DPM_TEMP | 10c4 | 2 |
| 4 | DSP | 1200 | 0 |
| 5 | LLM_CURR | 10d4 | 2 |
| 6 | LLM_TEMP | 10c8 | 2 |

The three bit-2 ARES lines share their register with branch enable bit 0.
Read/modify/write preserves each independently. Asserting reset does not
invent a clock-gating effect absent from the source; both signals remain
observable. These lines currently have no NPU execution-device consumers.

## Observation, reset and migration

All 45 outputs are actual Clock objects named after their Linux declaration,
including the derived CRC output. Read-only
`clk.<Linux name>.hz` properties report the computed integer rate.
`pll0-*`, `pll1-*`, and `pll2-*` expose locked, ack,
calibrated, lock-pending, latch-pending, active-l and active-alpha;
`rcg0-*` through `rcg5-*` expose pending and active-cfg.
`crc-reference-configured` reports the bounded vector predicate.
Reading QOM observations does not advance virtual time or repair outputs.

Only QTest acceleration creates `qtest-<input-name>-hz` uint32 input
fixtures. These set actual unconnected input Clock objects, refuse replacing
a connected provider, and are absent in normal TCG. They do not alter the
guest's MMIO protocol or claim native frequency discovery.

System reset cancels every PLL/RCG deadline, clears digital registers,
active/pending snapshots, generated PLL state and reset lines, and retains
external input Clocks and remote requests. Reset entry does not propagate
Clock changes; the hold phase drives outputs after the reset tree has
entered its reset state.

VMState `qcom-sm8250-npucc` version 1 / minimum 1 saves all
runtime registers, each independent snapshot/deadline, all external Clock
periods and remote votes. A fixed uint32 profile marker is validated before
runtime fields, rejecting enabled-reference on/off mismatches in either
direction. This contract covers this VMState implementation, not historical
binaries without an NPUCC device.

Loading validates register masks and supported states, restores derived
Clock periods without Clock callbacks, independently re-drives the seven
reset GPIOs and rearms the original earliest deadline. It does not perform
a fresh update, restart intervals, finish pending work early or propagate
Clocks into devices whose state may not yet be loaded.

The companion compiled-device test must record actual binary and script
hashes, physical MMIO traces, real pending-deadline round trips, restored
Clock periods and GPIO levels before any NPUCC MMIO read, and both
profile-rejection directions. Static source review or a successful probe
alone is not evidence of NPU, DSP, firmware or Android execution.
