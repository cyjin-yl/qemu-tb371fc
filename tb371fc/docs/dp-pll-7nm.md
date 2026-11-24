# SM8250 DP PLL 7 nm digital reference

This component models the bounded digital programming contract selected by
`qcom,mdss_dp_pll_7nm` in the original DT. The selected interface is non-V2.
It is disabled by default. Exact register storage, qualification and derived
clock carriers are separate from analog VCO behavior, the AUX engine, USB
operation, DisplayPort training and an Android display result.

## Observed native boundary

The qualified DSI revision `8e8e19a97efc23c215b35ed4dd40120ffab0e40d`
ran the unchanged original kernel, ramdisk, full DT and bootargs. The log records
DSI bank 0 registration at guest time `6.170571`, bank 1 registration at
`6.240493`, and the MDSS DP PLL probe at `6.242075`. The first fatal access is
the 32-bit read at physical `0x088ea158`, from
`dp_vco_recalc_rate_7nm+0x64`, at guest time `6.260019`.

The [retained native receipt](../evidence/dp-pll-20261124-6a5/native-frontier-before-dp.json)
binds that observation to the DSI binary and preserves its failure verdict.
It attached zero LUNs, preserved the four original inputs and AOP, and shut
down through QMP normally. Android userspace, boot completion and an interactive
display were not observed. The receipt establishes the boundary before this DP
implementation; it is not a DP model acceptance result.

The [actual Image instructions and PLL DT node](../evidence/dp-pll-20261124-6a5/actual-kernel-dp-probe-instructions.json)
and [helper instructions](../evidence/dp-pll-20261124-6a5/actual-kernel-dp-helper-instructions.json)
are bound to Image SHA-256
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
At relative offsets `+0x60/+0x64/+0x68`, the original instructions add `0x158`,
perform a 32-bit load and mask the low four bits. At `+0x94/+0x98/+0x9c`, they
add PHY offset `0x28`, load a word and extract bits 3:2. The retained HSCLK
jump table is `[1, 2, 5, 3]` for selectors 0 through 3. These observations
match the limited public-source probe path. They do not prove that the entire
Lenovo Image was compiled from the public source revision.

## Source identity and generation

The primary source is
[OnePlusOSS/android_kernel_oneplus_sm8250 at 98719830b0de5584f5588bbe9f4c8304d97a64c6](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/98719830b0de5584f5588bbe9f4c8304d97a64c6).
The main
[dp_pll_7nm.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/techpack/display/pll/dp_pll_7nm.c)
defines clock topology and registration. Its Git blob is
`8db4747d4ff25e7cf7cfe59b3e3b06003a749e5c`, SHA-256
`b8c40f85bb9ee966b905434e91225c70e27ecd945a5ea2495cc7604219d483a1`,
and length 9,015 bytes. The programming algorithm is in
[dp_pll_7nm_util.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/techpack/display/pll/dp_pll_7nm_util.c):
Git blob `4ce47a7f5497d81f4567e4056c46ce982d0b4004`, SHA-256
`1135de217c3ec633a4539565e699baa86fdbe452e48e444cd3660b5cab981532`,
19,655 bytes.

The [generator](../scripts/generate_dp_pll_7nm.py) checks seventeen complete
primary files before parsing any facts: the two implementation files and two
DP headers, platform dispatch/resource code and its header, Makefile, DP clock
binding, both Kona display DTS files, three provider bindings, v420 catalog,
shared register header and USB orientation enum. The generated
[register/clock manifest](dp-pll-7nm-registers.json) records every source path,
immutable URL, byte count, SHA-256 and Git blob identity. The separate
[rate-unit provenance](../evidence/dp-pll-20261124-6a5/rate-unit-sources.json)
checks six more primary files. The seventeen-input generation gate covers its
table inputs; the six supporting files establish the rate-unit call chain.
The fixed public Kona DTS and the
[original DT PHY-version property](../evidence/dp-pll-20261124-6a5/actual-dp-phy-version.json)
both select `0x420`, the v420 companion catalog. The retained production
instructions establish the listed PLL paths; they do not establish the
identity of every compiled DP controller routine.

The [verifier](../scripts/verify_dp_pll_generation.py) produces
`tb371fc-dp-pll-generation-v1` with exactly seventeen inputs and two output
comparisons. Its outputs are `hw/misc/qcom-sm8250-dp-pll-data.inc` and the JSON
manifest. Local regeneration reproduced both files byte for byte. Changing one
bit in each of the seventeen inputs was rejected before any table was returned;
the original input files remained unchanged. These are source and generation
checks. They do not constitute compiled QEMU or native DP validation. The
[source-validation receipt](../evidence/dp-pll-20261124-6a5/source-validation.json)
records that boundary explicitly.

## Exact ownership and aliases

The original PLL DT and
[actual DP controller node](../evidence/dp-pll-20261124-6a5/actual-dp-controller-dt.json)
agree with the fixed source DTS on these addresses. The node's historical unit
address `@c011000` does not override its `reg` property.

| Canonical region | Physical base | Aperture | Owned words | Owned bytes |
| --- | --- | ---: | ---: | ---: |
| PLL | `0x088ea000` | `0x200` | 30 | 120 |
| PHY | `0x088eaa00` | `0x200` | 11 | 44 |
| TX0 | `0x088ea200` | `0x200` | 16 | 64 |
| TX1 | `0x088ea600` | `0x200` | 16 | 64 |

These four transparent containers contain 42 exact leaves, 73 independent
32-bit words and 292 mapped bytes. The utility driver directly accesses or
polls 63 of those words. The additional ten words are five source-written TX
configuration words per bank at offsets `0x0c`, `0x14`, `0x54`, `0x58` and
`0x5c`; they retain digital values without implementing lane voltage or training.
Other words remain unimplemented. The manifest lists every exact leaf and word.

| Client view | Canonical word or range | Consequence |
| --- | --- | --- |
| PLL node `ln_tx0_tran_base` / `ln_tx1_tran_base` | TX0/TX1 `+0xb8`, length `0x8` | Main and alias views share `+0xb8/+0xbc` storage |
| PLL node `ln_tx0_vmode_base` / `ln_tx1_vmode_base` | TX0/TX1 `+0xe8`, length `0x4` | One word per TX bank |
| DP controller `dp_pll` | PLL `+0x40`, length `0x10` | Catalog offset `+0x4` and utility offset `+0x44` both address `0x088ea044` |
| DP controller PHY and TX resources | Same PHY/TX bases as the PLL node | One physical owner per word |
| Shared `gdsc_base` | `0x0af03000`, length `0x8` | Existing MDSS power-domain owner is retained |

The original DT also declares the broad USB combo resource
`0x088e8000/0x3000`, while the DP controller names a `usb3_dp_com`
subresource `0x088e8000/0x20`. Those declarations are recorded as context.
They do not authorize mapping the entire combo aperture into this DP model.
Unimplemented USB/AUX resources and holes remain visible to later owners.

The source mux object uses regmap key `0x64`. Its custom callbacks ignore that
key and perform real accesses to PHY `+0x70`; `0x64` is not added as a physical
register. The source low-two-bit codes are 1 for parent 0 (`/2`), 2 for parent 1
(`/4`), and 0 for parent 2 (`/6`). Code 3 has no supported selection.

## Source programming and clock units

The non-V2 interface publishes this six-clock namespace. The two V2 replacement
names in the public file are excluded because the original compatible is non-V2.

| ID | Binding | Non-V2 name | Source relationship |
| ---: | --- | --- | --- |
| 0 | `DP_VCO_CLK` | `dp_vco_clk` | HSCLK request number |
| 1 | `DP_LINK_CLK_DIVSEL_TEN` | `dp_link_clk_divsel_ten` | HSCLK `/10` |
| 2 | `DP_VCO_DIVIDED_TWO_CLK_SRC` | `dp_vco_divsel_two_clk_src` | HSCLK `/2` |
| 3 | `DP_VCO_DIVIDED_FOUR_CLK_SRC` | `dp_vco_divsel_four_clk_src` | HSCLK `/4` |
| 4 | `DP_VCO_DIVIDED_SIX_CLK_SRC` | `dp_vco_divsel_six_clk_src` | HSCLK `/6` |
| 5 | `DP_VCO_DIVIDED_CLK_SRC_MUX` | `dp_vco_divided_clk_src_mux` | PHY `+0x70` mux |

The driver accepts HSCLK request numbers `1620000`, `2700000`, `5400000` and
`8100000`, named `MHZDIV1000` in its header. The DP control path obtains link
numbers `162000`, `270000`, `540000` and `810000` from the DRM helper and passes
them through `dp_power.c` and `sde_io_util.c` to `clk_set_rate` without a factor
of 1,000. Its pixel rate also uses `pixel_clk_khz`, optionally divided by two
for wide-bus mode. The matching DISPCC driver retains these numeric units and
the original names `dp_link_clk_divsel_ten` and
`dp_vco_divided_clk_src_mux` for its DP inputs.

The digital reference must therefore distinguish those CCF numeric values
from QEMU Clock periods. Its physical HSCLK interpretation is the qualified
source request in kHz multiplied by 1,000, with derived divider outputs in Hz.
That conversion is explicit reference behavior. The `dp_vco_clk` software name
and diagnostic strings about 9.72 or 10.8 GHz do not prove an analog VCO model.

There are 58 required source-programming words. Seven PLL fields vary across
the four recipes; 47 fields have fixed source values. PHY orientation and
power control have separate rules, and `SPARE0` supplies the catalog's lane
count/orientation input. PHY `VCO_DIV` must be written but remains a live mux
selection. The source begins the enable sequence with PHY CFG writes
`1, 5, 1, 9`, writes PLL RESET state-machine control `0x20`, polls C_READY,
FREQ_DONE and PLL_LOCKED, then writes PHY CFG `0x19` and polls TSYNC_DONE and
PHY_READY. The source polling interval is 500 microseconds with a 10 millisecond
timeout. This polling budget does not itself specify a silicon lock delay.

| Observed state | Physical word | Bit |
| --- | --- | ---: |
| C_READY | PLL `+0x178` | 0 |
| FREQ_DONE | PLL `+0x140` | 0 |
| PLL_LOCKED | PLL `+0x140` | 1 |
| TSYNC_DONE | PHY `+0xdc` | 0 |
| PHY_READY | PHY `+0xdc` | 1 |

These status words are derived and read-only, including rejection of zero
writes. Cold zero-initialized configuration is a digital reference storage
policy, not a claim about silicon power-on values. Source-used write masks
bound accepted values without purporting to describe every silicon reserved
bit. In particular, a cold `HSCLK_SEL=0` and `AUX_CFG2=0` cause the software
recalc function to report `8100000`; that function does not poll lock and does
not establish a physical carrier. Carrier availability requires the reference
model's separately qualified programming and state transitions.

The model's `raw-ccf-rate-khz` observation is a bounded decoder for supported
selector encodings. An unsupported HSCLK nibble or AUX divider code 3 returns
zero with `raw-rate-known=false`. The vendor recalc function instead falls back
to divisor 5 for an unrecognized HSCLK selection and continues its software
calculation after an unsupported AUX selection. That fallback is recorded as
source behavior, without granting an unsupported encoding a model rate or
carrier. The legal cold-zero combination still decodes to the nominal
`8100000` while all physical outputs remain unavailable.

## Digital reference state

Qualification requires a known, on MDSS power supplier and the actual
`ref_clk_src` input at exactly 19.2 MHz. The four clock-provider declarations
in the DT remain distinct: DISPCC AHB ID 0, RPMh XO ID 0, GCC display AHB ID
24 and GCC USB3 primary PHY pipe ID 197. The PLL's reference input does not
manufacture an interface or USB pipe carrier.

The required configuration must be written while the power supplier is known
and on. Accepted lane counts are 1, 2 and 4, with orientation codes CC1=1 or
CC2=2. After the source CFG sequence and PLL reset command, the reference
captures all 73 words, the qualifying XO period and the selected source rate.
The PLL transition takes 64 XO cycles rounded upward to 3,334 nanoseconds.
A later CFG `0x19` starts a second 3,334-nanosecond transition to PHY-ready.
Both 64-cycle delays are finite reference policies, not measured silicon
latencies or values claimed by the public source.

After PLL lock, the internal HSCLK and `/2`, `/4`, `/6` views can be available.
The two final link and pixel-mux outputs require PHY-ready. The catalog's late
CFG `0x18` reset keeps the qualified PLL but removes those two final outputs.
Its 2-millisecond wait comes directly from the source's `udelay(2000)`.
After that wait, CFG `0x19` initiates the reference PHY transition again.
An early command does not bypass the wait.

Power loss or loss of known power cancels the capture and all written-word
qualification while retaining raw configuration storage. XO changes cancel
the captured transition and output carriers without claiming that the
registers lost power. Re-establishing a carrier requires another valid command
sequence and matching captured state. The live PHY mux can change without
retuning the PLL; its physical output follows the selected source divider.
The vendor's CCF mux recalculation separately chooses a nominal divisor from
the HSCLK rate, which does not prove a physically running output.

Callbacks accept aligned 32-bit accesses. Unsupported bits and writes to
status words fail without mutating that callback's state. An eight-byte
transaction delivered to one callback is rejected. An address-space access
crossing a sparse leaf boundary can be split before a callback sees it, so
there is no blanket promise of atomic rejection or rollback across separate
owners and holes.

The version-1 DP migration payload has 791 bytes with a default 1:1 input
Clock ratio. It records 13 fixed identity/count/policy words, four physical
bases, all raw words, every written marker, the full capture, XO periods,
deadline, phase, sequence, captured rate and power inputs. Output clocks are
recomputed. This byte count is a serialization contract; a successful
migration still requires complete stream and first-target validation.

## Validation boundary

Source generation currently establishes the exact input bytes, selected
variant, sparse addresses, aliases, clock names, recipe tables and the limited
production probe correspondence. Compiled device tests must separately check
power/XO loss, finite transitions, bad or missing programming, rejected writes,
all derived clock periods and shared-owner preservation. A migration verdict
must compare the complete serialized device state and the first destination
QOM/Clock observations before device MMIO or guest continuation, while retaining
raw protocol logs and rejection diagnostics.

Successful component tests or migration are not a substitute for another
original-input native run. Full Android acceptance additionally requires all
six original LUNs through the verified ready-consumer path, unchanged input
identity, actual userspace evidence, boot completion and an observed interface.
