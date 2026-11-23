# VIDEOCC source note for subsequent native fault diagnosis

This is read-only preparation, not a device implementation or a prediction
of the next fault. The current native observation is a nonfatal
`Unable to register for bw voting` defer. In the pinned driver,
bus-client registration precedes both PLL configuration calls. Successful
NoC configuration would not by itself prove that this dependency or
VIDEOCC is usable. Actual original-DT VIDEO resource geometry, compatible
revision and any later invalid-memory address must still be captured.

## Fixed evidence

The controlling vendor source is [videocc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/videocc-kona.c)
at `94304e12a3509eac7b3716bf574ce0b1fcaacbae`, Git blob
`4616556fba14c22296be25a0853c94539781883c`
(SHA256 `33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5`).
Its [clock/reset binding](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,videocc-kona.h)
is blob `08b33ce32dc6075bc6073ba87e314f7b686e70ac`.
Register offsets below are relative to the resource, not assumed physical addresses.

## Vendor layout and routing

The declaration contains 19 clocks: two Lucid PLLs, five RCGs, four
driver-read-only dividers and eight branches, plus seven reset lines.

| Block | Relative registers | Declared parents / behavior |
| --- | --- | --- |
| PLL0 / PLL1 | Bases `0x42c` / `0x7d0` | Both use bi_tcxo; existing Lucid helper register layout |
| AHB RCG | CMD/CFG `0xbd4/0xbd8` | selector 0 bi_tcxo_ao, 7 test |
| MVS0 / MVS1 RCG | `0xb94/0xb98` / `0xbb4/0xbb8` | 0 bi_tcxo, 1 corresponding PLL, 7 test |
| Sleep RCG | `0xef0/0xef4` | 0 chip_sleep_clk, 7 test; table requests 32000 Hz |
| XO RCG | `0xecc/0xed0` | 0 bi_tcxo_ao, 7 test |
| Four dividers | `0xd54,0xc54,0xdd4,0xcf4` | MVS0, MVS0, MVS1, MVS1 respectively; bits [1:0] |

All RCGs have HID width 5 and no M/N divider. AHB/MVS0/MVS1 declare
HW clock control and safe configuration; sleep/XO do not. Other selector
values have no declared parent.

Branch enable is bit 0 throughout. Routing is
`e58←AHB`, `d34←d54`, `c34←c54`, `db4←dd4`,
`df4←cf4`, `cd4←cf4`, `f10←sleep`, `eec←XO`.
The d34/db4/df4/cd4 branches use HALT_VOTED; the others use HALT.
Binding reset IDs 0–6 map respectively to
`e54.0, d14.0, c34.2, bf4.0, d94.0, cd4.2, c94.0`.
Two ARES bits share CBCR words; a future model must preserve both fields.

[clk-regmap-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-regmap-divider.c)
(blob `a9f47534b495c2f68821d379a253d0039f986aa1`) calls
[clk-divider.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/clk-divider.c)
(blob `44baad024ec47c888a94eeee9b0958edaff79b86`).
The unflagged encoding 0/1/2/3 means division by 1/2/3/4, with
`DIV_ROUND_UP_ULL(parent_rate, divisor)` on rate readback.
The vendor driver does not write these divider values. A name containing
div2 does not establish its reset or firmware-programmed encoding.

## Revision-sensitive PLL values

At an actual 19.2 MHz parent, floor-Q16 arithmetic gives:

| PLL recipe | L / ALPHA | CONFIG_U1 | Integer output, before gating |
| --- | --- | --- | ---: |
| PLL0, both revisions | `25 / 8000` | `029a699c` | 720000000 Hz |
| PLL1 initial / v1 | `29 / faaa` | `029a699c` | 805999804 Hz |
| PLL1 v2 fixup | `2b / c000` | `329a699c` | 840000000 Hz |

All values are hexadecimal except the rate column. Common configuration
includes CAL_L 44, CONFIG 20485699/2261, USER_U 805. The probe first
configures both initial recipes, then the
`qcom,videocc-kona-v2` match reconfigures PLL1 and replaces the MVS1
rate/VDD table. PLL0 does not inherit the NPU or PLL1-v2 CONFIG_U1 value.

## Inputs and exclusions to retain

The existing RPMh evidence can support a common active carrier for
bi_tcxo/bi_tcxo_ao, with the same independent-sleep-vote limitation already
documented for NPUCC. chip_sleep_clk's 32000-Hz table must not be replaced
by the separate MPM counter's 32768 Hz. The test source remains unknown.
The probe obtains and releases cfg_ahb_clk; obtaining a handle alone
does not prove a known parent frequency or enabled clock.

[Upstream v6.12.114](
https://github.com/gregkh/linux/blob/v6.12.114/drivers/clk/qcom/videocc-sm8250.c)
(blob `df479a69cddd0098ab92aede5ee30a7bf3c3def6`) corroborates offsets
and identifies separate GDSCs at `bf8,c98,d18,d98`. It has a smaller clock
subset, uses CONFIG_U1 329a699c for PLL0, and a different compatible/probe
path, so it cannot replace the vendor contract. The vendor descriptor
does not register these GDSCs. Preserve their ownership as separate
dependencies. Neither max_register=f4c nor an eventual DT resource size
authorizes a RAM window or invented power/status success.

A future implementation requires an observed fault and verified original-DT
binding, an explicit policy for unknown divider values, and independent
state/timing/migration tests. This note establishes no video codec,
DSP firmware, frame processing, throughput, suspend or Android functionality.
