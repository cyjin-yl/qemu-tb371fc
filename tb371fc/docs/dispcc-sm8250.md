# Kona DISPCC digital reference

`qcom-sm8250-dispcc` implements the bounded, source-defined clock protocol at
the next observed original-kernel failure. It defaults to disabled and requires
an explicit diagnostic option:

```sh
-global qcom-sm8250-dispcc.enabled-reference=on
```

The QOM object is `/machine/dispcc`. The 130 supported words occupy 520 bytes
in 39 exact contiguous windows within the actual 128-KiB resource. A transparent
container exports these leaves without handlers, storage or default responses
in intervening holes. MDSS GDSCR `0x0af03000` remains owned by the existing
independent power-domain device, including its unchanged 64-byte v1 payload.

This increment covers digital requests, captured configuration, derived clock
rates, reset outputs and migration. It does not implement display engines,
DSI/DP/eDP PHYs, a panel, pixels, physical analog calibration, voltage safety,
or display DMA. Component success alone is not an Android boot verdict.

## Actual trigger and device-tree identity

The preceding exact source `8798465b1b70f9c86f5cd5e1e38ce595bd6088ce` passed
[formal CI 45426713919](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45426713919):
83 component gates, five Linux integrations, both Linux UFS checkpoint modes,
and the independent A77/PCI UFS job. The inspected artifact binary SHA-256 is
`026f93a8c26950dd66cdb5c44eaf5e1575db638e59b69cbf025591d810be5594`.
The CAMCC gate actually passed 41 checks, three completed migrations, two profile
rejections and two single-bit corrupted-stream rejections. Its
[independent ZIP audit](../evidence/camcc-20261124-e1a9/ci-artifact-audit-8798465.json)
verifies the six actual streams, complete CAMCC and camera payloads, and all
111 output rates and Clock periods. That qualification belongs to 8798465,
before this DISPCC implementation.

In the same-binary original-input run005, the kernel reported
`Registered CAM CC clocks` at guest 3.693603 seconds. The next deferred probe
linked `disp_cc-kona af00000.qcom,dispcc` to its regulator and registered
`clk_dispcc_debugfs`. The first fatal was a synchronous external abort at
3.697772 seconds on CPU4, PID84 `kworker/4:1`, through
`deferred_probe_work_func`, `disp_cc_kona_probe+0x11c/0x1e0`,
`clk_lucid_pll_configure+0x40/0x320`, and `regmap_mmio_read32le+0x8`.
QEMU independently recorded the invalid read32 at physical `0x0af00000`.
ESR was `0x96000010`, FAR `0xffffff8015cc0000`, and ELR
`0xffffff800884e968`. This is a worker Oops, not evidence of a PID1 panic.

Run005 ended at 2026-11-24T15:50:23.377176Z after 17.415 seconds, with normal
QMP shutdown. Original Image, ramdisk, full DT, boot arguments, AOP input,
symbols and QEMU hashes were unchanged. No original LUN was attached; no
Android userspace or boot-complete event was observed. The first 55,347-byte
printk hash is
`9a2639cfa65a48c5c7e9ec6cba4c151ecfc04f5edb3f039b1f580df95e4a18ea`;
the QEMU log hash is
`2c725d2caf414c16c0a862100d1bb54bd95067aea55f48f4a3329c90d5aa4b3f`.
The [bounded receipt](../evidence/camcc-20261124-e1a9/native-frontier-after-camcc.json)
preserves the preceding checkpoint without publishing private original inputs.

The [actual inventory](../evidence/expansion-20261123/android-device-inventory.json)
binds `/soc/qcom,dispcc@af00000` to original DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The complete node has no omitted or rejected properties. It declares
`qcom,kona-dispcc`, followed by `syscon`, `cc_base = 0x0af00000/0x20000`,
phandle107 and one clock/reset argument cell. `/soc` translation preserves the
physical address. Its sole named clock is `cfg_ahb_clk = <22 24>` from GCC;
`vdd_mm-supply = <104>` identifies RPMh mmcx. The supply is not a clock
carrier, and this increment does not alter MDSS's existing parent wiring.

## Pinned primary sources and geometry

All nine source inputs are pinned to OnePlusOSS kernel commit
[`94304e12a3509eac7b3716bf574ce0b1fcaacbae`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/94304e12a3509eac7b3716bf574ce0b1fcaacbae).
Their SHA-256 values are recorded in the
[generator](../scripts/generate_dispcc_sm8250.py) and
[register manifest](dispcc-sm8250-registers.json).

| Primary source | Contract used |
| --- | --- |
| [dispcc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/dispcc-kona.c) | Exact compatible, two Lucid PLLs, 22 RCGs, 32 branches, five dividers, two resets, MISC bit4 and all 61 exported clocks |
| [qcom,dispcc-kona.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,dispcc-kona.h) | Clock IDs 0–60 and reset order |
| [clk-alpha-pll.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-alpha-pll.c) | Lucid configure/enable/disable/UPDATE sequences and Q16 factor |
| [clk-rcg2.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rcg2.c) | Common raw CMD/CFG/MND protocol, byte2 HID selection, pixel and DP fractional encodings |
| [clk-branch.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.c), [clk-branch.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-branch.h) | Local enable/halt protocol and BRANCH_VOTED's shared-request meaning |
| [clk-regmap-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-regmap-divider.c), [clk-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/clk-divider.c) | Two writable and three read-only dividers; generic rate rounding upward |
| [clk-rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-rpmh.c) | `bi_tcxo` uses `xo.lvl` from `xo_board` |

The source-generation gate verifies all nine digests and regenerates the C
table and JSON manifest byte for byte. The Linux `max_register = 0x10000`
is a regmap bound, not a declaration that every word exists. The largest modeled
offset is `0x8000`. Opaque Lucid STATUS at offsets `0x30/0x1030`, MDSS GDSCR
at `0x3000`, and all other undeclared words are excluded from the DISPCC table.

| Register family | Words | Protocol boundary |
| --- | ---: | --- |
| Two Lucid PLLs | 28 | Fourteen source-accessed words per PLL |
| 22 RCG CMD/CFG pairs | 44 | Explicit muxes and per-RCG HW control permission |
| Six M/N/D triples | 18 | Four 16-bit DP/eDP pixel roots, two 8-bit DSI pixel roots |
| Branch controls | 32 | Bit0 request and rate-derived CLK_OFF31 |
| Dividers | 5 | Two-bit field; only BYTE0/1 writable through MMIO |
| Reset controls | 2 | Core and RSCC BCR bit0 |
| MISC command | 1 | Offset0x8000, bit4 only |
| **Total** | **130** | **520 bytes in 39 exact leaves** |

## Inputs and honest unknowns

Seventeen input Clock objects have a fixed migration order:

1. `bi_tcxo`
2. `core_bi_pll_test_se`
3. `chip_sleep_clk`
4. `disp_cc_pll1_out_even`
5. `dp_link_clk_divsel_ten`
6. `dp_vco_divided_clk_src_mux`
7. `dptx1_phy_pll_link_clk`
8. `dptx1_phy_pll_vco_div_clk`
9. `dptx2_phy_pll_link_clk`
10. `dptx2_phy_pll_vco_div_clk`
11. `dsi0_phy_pll_out_byteclk`
12. `dsi1_phy_pll_out_byteclk`
13. `dsi0_phy_pll_out_dsiclk`
14. `dsi1_phy_pll_out_dsiclk`
15. `edp_phy_pll_link_clk`
16. `edp_phy_pll_vco_div_clk`
17. `disp_cc_xo_clk_parent`

Only `bi_tcxo` connects to the existing, sourced board XO. All other carriers
remain zero until an explicit provider supplies a rate. `disp_cc_pll1_out_even`
occurs in the parent-name table but has no provider/export in this file, so it
is not aliased to PLL1 or divided speculatively. `disp_cc_xo_clk` is marked
software-critical but declares no parent. Its separate unknown input preserves
that missing evidence: a local request does not clear CLK_OFF without a known
carrier. No physical DSI/DP/eDP link rate or 32-kHz sleep clock is guessed.
Consequently the native critical-clock enable may time out at this branch.
That outcome requires a new observation and source/physical-clock evidence;
it is not a reason to insert a constant successful halt response.

The DP link table literally uses values 162000, 270000, 540000 and 810000,
whereas eDP uses MHz-scale values. The model derives rates from actual input
carriers and register fields; it does not multiply one table by an assumed
unit conversion or execute the driver's software rate-selection policy.

## Lucid capture, lock and UPDATE

PLL0 and PLL1 have bases0 and0x1000. The source recipes program L/ALPHA as
`0x47/0xe000` and `0x1f/0x4000`, giving 1.38 GHz and 600 MHz at19.2 MHz XO.
Both use CAL_L0x44, CONFIG0x20485699/U0x2261/U10x029a699c,
USER_U0x805, and zero USER_U1/TEST/TEST_U/TEST_U1. The configure helper skips
zero-valued USER configuration writes. Cold USER is zero; successful output
requires all three grouped output bits and MODE.OUTCTRL.

MODE accepts writable mask0x00c00007 and generates LOCK31, ACK29 and PCAL27.
L, ALPHA and CAL_L are16-bit, OPMODE is0/1, and USER supports only group0/7.
There is no postdivider register contract or Zonda behavior in this device.
Other configuration/test words retain32-bit storage, but only the fixed recipe
qualifies for a successful reference lock.

Valid RUN captures L/ALPHA and requires RESET_N, live XO and an admissible
249.6-MHz–2-GHz VCO rate. Lock takes5000 virtual ns. UPDATE rising with
UPDATE_BYPASS captures L/ALPHA once, commits after1000ns, asserts ACK,
withdraws LOCK and starts a5000ns relock interval. Later shadow writes and
repeated-high UPDATE do not replace the capture. The previous output may
continue until the latch commits. Clearing UPDATE or UPDATE_BYPASS cancels
uncommitted work and ACK. RESET_N low clears active/pending factors,
calibration and deadlines; standby withdraws active/LOCK while retaining
calibration. Invalid recipe, source or factor never gains LOCK/ACK by waiting.

These delays are explicit digital ordering assumptions, not tablet analog
measurements. Rate calculation uses actual XO and the Q16 factor. Only the
single rounded-up upper-endpoint factor allowed by the fixed driver's500-Hz
margin is admitted above2GHz; there is no arbitrary success band.

## RCGs, dividers, votes and reset

RCG order follows the source declaration order. AHB, MDP and ROT alone declare
HW_CLK_CTRL_MODE; their CFG mask is0x10371f. The remaining19 CFG words use
0x371f and reject writes setting bit20. CMD accepts UPDATE0/ROOT_EN1 and
reports ROOT_OFF31 from the actual derived output.

DP pixel1, pixel2, pixel and eDP pixel have16-bit M/N/D. DSI pixel0/1 have8-bit
M/N/D; the other16 RCGs have none. Mode0 bypasses the fraction and permits
old M/N/D storage, including the source pixel1/1 encoding. Bounded mode2
requires `0 < M < N <= (1 << width) - 1`, with
`N = (~Nreg & mask) + M` and `Dreg = (~N & mask)`. Other modes, mux holes,
reserved control bits and invalid counter ratios cannot synthesize UPDATE
completion. A valid rising UPDATE captures CFG/M/N/D once and commits after
1000ns. A known selector with a stopped/unknown carrier may acknowledge the
configuration; its rate remains zero. Cancel and repeated-high behavior retain
the captured-state distinction. HID division floors first, then the fraction.

All five dividers have shift0/width2 and calculate
`ceil(parent_rate / (field + 1))`. BYTE0 at0x2128 and BYTE1 at0x2144 accept
MMIO writes0–3. DP link1 at0x2224, DP link at0x2190 and eDP link at0x2288
reject every MMIO write, including zero. QTest-only fixtures can supply these
three read-only configuration fields explicitly. No read-only field is guessed
from a requested display mode.

The32 branches use local bit0. MDP_LUT at0x201c and NON_GDSC_AHB at0x4004
are BRANCH_VOTED. Two explicit GPIO inputs named `remote-vote`, indices0/1,
OR with those local requests. No request is seeded from a hypothetical remote
firmware. Other branches have no remote-vote input. Branch demand propagates
through each divider to its source RCG. Halt status follows the resulting
carrier, including zero for an unknown parent. Input carriers and remote
requests remain provider-owned across reset and migration.

MISC0x8000 bit4 stores the source's MDP clock-gating configuration; it does not
invent a physical gate request, supply, parent rate or PLL acknowledgement.
Core BCR0x2000 and RSCC BCR0x4000 bit0 drive the two `reset` GPIO outputs in
binding order. They do not simulate execution or retained contents of display
engines. Unsupported widths, addresses and bits reject without changing stored
configuration; bus accesses spanning multiple regions retain normal QEMU
per-transaction behavior.

## Migration and verification boundary

The independent VMState section is `qcom-sm8250-dispcc`, version1/minimum1.
With the fixed1:1 input Clock ratios, its complete payload is1676 bytes:

| State, in wire order | Bytes |
| --- | ---: |
| Thirteen equal-checked32-bit profile/identity/geometry fields and64-bit physical base | 60 |
| 130 stored register words in increasing offset order | 520 |
| Active/pending L and ALPHA arrays for two PLLs | 32 |
| Active/pending CFG/M/N/D arrays for22 RCGs | 704 |
| Two PLL lock and two latch deadlines | 32 |
| 22 RCG deadlines | 176 |
| Last observed XO | 8 |
| Two PLL LOCK/ACK/calibration boolean arrays | 6 |
| Two remote-request booleans | 2 |
| Seventeen input Clock periods | 136 |
| **Total** | **1676** |

The target first cancels its previous timer and withdraws stale outputs. After
loading, it checks profile, all geometry, supported masks, carrier ratios and
rate range, captured counter shapes, PLL invariants and deadline bounds. It
derives output clocks and reset lines before the first guest access, without
advancing virtual time, regenerating ACK or replacing saved deadlines. Pending
locks/latches/RCG updates then continue on their original schedule. Output Clock
state is derived, not separately serialized. MDSS retains its own section and
parent-state semantics.

Pure QOM getters expose stored words, captured factors/configurations, deadlines,
61 computed rates, divider fields and remote requests. QTest-only input-rate
fixtures refuse to overwrite a connected provider and reject periods that
would quantize a uint32 rate to2^32Hz. The three read-only-divider fixtures
are absent in normal TCG. Observation must not repair or advance pending work.

The new formal gate requires41 compiled groups, three completed transfers, two
profile mismatch rejections and two deliberate single-bit stream rejections.
Four original streams plus two mutants must be present and hashed. Its literal
oracle is independently derived from the fixed primary sources; it verifies the
full1676-byte payload plus MDSS64 bytes, actual memory-region ownership with
both devices enabled, and the first destination QOM state before device MMIO
or `cont`. A separate gate requires two exact regeneration comparisons from
nine pinned source files. This creates an85-component +5-Linux plan; this new
combination still requires its own formal build and original-input run.
