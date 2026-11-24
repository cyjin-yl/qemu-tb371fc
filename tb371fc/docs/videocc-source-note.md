# VIDEOCC source preparation and subsequent implementation

This note preserves the earlier read-only preparation. The subsequent
[VIDEOCC implementation contract](videocc-sm8250.md) follows the actually
observed run006 read32 failure at physical0x0abf042c after Display CC registered.
At the preparation checkpoint, the native observation was a nonfatal
`Unable to register for bw voting` defer. In the pinned driver,
bus-client registration precedes both PLL configuration calls. Successful
NoC configuration would not by itself prove that this dependency or
VIDEOCC is usable. The original DT now establishes the resource and v2
compatible below; that earlier evidence contained no later VIDEOCC invalid
memory address. The new receipt and planned compiled acceptance retain their
own exact-source identities, separate from this historical preparation.

## Fixed evidence

The controlling vendor source is [videocc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/videocc-kona.c)
at `94304e12a3509eac7b3716bf574ce0b1fcaacbae`, Git blob
`4616556fba14c22296be25a0853c94539781883c`
(SHA256 `33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5`).
Its [clock/reset binding](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,videocc-kona.h)
is blob `08b33ce32dc6075bc6073ba87e314f7b686e70ac`.
Register offsets below are relative to the resource, not assumed physical addresses.

## Actual original-DT binding

A single read-only extraction from CT102's qemu-tb371fc-dev container
verified the complete DTB SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The [bounded JSON](../evidence/wholeboard-20261123-a47/videocc-dt-evidence.json)
preserves raw hex, big-endian cells and the direct provider identity;
no full DTB or firmware was exported.

| Property | Actual value |
| --- | --- |
| Node | `/soc/qcom,videocc@abf0000` |
| compatible | `qcom,videocc-kona-v2`, `syscon` |
| reg / reg-names | `0x0abf0000, 0x10000` / `cc_base`; parent cells 1 address + 1 size |
| clocks / clock-names | `0x16, 0xcd` / `cfg_ahb_clk` |
| Direct clock provider | `/soc/qcom,gcc@100000`, `qcom,gcc-kona`; phandle 0x16, #clock-cells 1 |
| VIDEOCC provider | phandle 0x6a, #clock-cells 1 |
| assigned-clocks / assigned-clock-parents / assigned-clock-rates | All absent |

The pinned [GCC binding](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,gcc-kona.h)
(blob `2e74c2800db35b88b585e2e74256bf4b1e08078e`) identifies
205 (0xcd) as GCC_VIDEO_AHB_CLK. This is the only explicit VIDEOCC DT clock
specifier; the other named inputs below come from driver declarations,
not additional DT clock specifiers.

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

| PLL recipe | L / ALPHA | CONFIG_U1 | Calculated integer factor rate |
| --- | --- | --- | ---: |
| PLL0, both revisions | `25 / 8000` | `029a699c` | 720000000 Hz |
| PLL1 initial / v1 | `29 / faaa` | `029a699c` | 805999804 Hz |
| PLL1 v2 fixup | `2b / c000` | `329a699c` | 840000000 Hz |

All values are hexadecimal except the rate column. Common configuration
includes CAL_L 44, CONFIG 20485699/2261, USER_U 805. The probe first
configures both initial recipes, then the
`qcom,videocc-kona-v2` match reconfigures PLL1 and replaces the MVS1
rate/VDD table. PLL0 does not inherit the NPU or PLL1-v2 CONFIG_U1 value.

The actual v2 probe's initial PLL1 writes establish configuration storage,
not a working legacy PLL1 carrier. The pinned `clk_lucid_pll_configure`
ends with OUTCTRL cleared, OPMODE_STANDBY and RESET_N set (lines1853–1868);
it does not start RUN or wait for LOCK. The v2 fixup executes before
`qcom_cc_really_probe` (videocc-kona.c lines645–652). Accordingly the v2
reference permits new PLL1 LOCK/ACK only with CONFIG_U1 `0x329a699c`.
Its initial `0x029a699c` remains accepted for standby storage; the
805999804-Hz calculation above does not qualify a modeled v2 output.

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

The subsequent contract records the observed fault, an explicit reference
reset policy for unknown hardware divider values, and independent
state/timing/migration tests. This note establishes no video codec,
DSP firmware, frame processing, throughput, suspend or Android functionality.
