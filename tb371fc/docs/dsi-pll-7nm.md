# SM8250 DSI PLL 7 nm v4.1 reference

This component supplies a bounded digital reference for the two DSI PLL banks
selected by the original DT compatible `qcom,mdss_dsi_pll_7nm_v4_1`. It is disabled
by default. Its register ownership, programming requirements, clock views and
finite lock transition are explicit; it does not claim to reproduce analog PLL
or complete display PHY behavior.

## Observed native boundary

The original-Image run007 registered Display CC at guest time `2.662139` and
Video CC at `2.673751`. Its first fatal access was the 32-bit read at physical
`0x0ae94418`, from `vco_7nm_recalc_rate`, at guest time `2.974308`. The run attached
zero LUNs, preserved the four original boot inputs, and did not reach Android
userspace or a UI. Those observations establish the next missing owner; they do
not establish that this new DSI model has run successfully.

The retained evidence is
[the native receipt](../evidence/dsi-pll-20261124-6a5/native-run007-observation.json),
[the original DT nodes](../evidence/dsi-pll-20261124-6a5/actual-dt-dsi-pll-nodes.json),
and [the actual Image instruction comparison](../evidence/dsi-pll-20261124-6a5/actual-kernel-dsi-probe-instructions.json).
The Image was rehashed as
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
The actual instructions load PHY `+0x18`, extract bit 6, and branch to the
7 nm lock helper that polls PLL `+0x1b0` bit 0. This excludes the differently
named non-7 nm helper using `+0xc0`. The comparison proves this limited probe
path correspondence, not byte identity between the entire Lenovo kernel and
the public source revision.

## Source and variant

The primary implementation is
[OnePlusOSS/android_kernel_oneplus_sm8250 at 98719830b0de5584f5588bbe9f4c8304d97a64c6](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/98719830b0de5584f5588bbe9f4c8304d97a64c6).
Its main
[dsi_pll_7nm.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/techpack/display/pll/dsi_pll_7nm.c)
is 87,075 bytes, Git blob
`581fa3ba13b8f6287f2b63bbd73777a45de923ea`, SHA-256
`a286700822a0fb2fa1e9bff39cc6720295254c6137f29de65a37bcb819a8dd64`.
The earlier clock-controller source pin `94304e12...` does not supply this
display directory and is not reused as DSI provenance.

The generator verifies thirteen complete source files before extracting any
fact: the PLL implementation, platform dispatch and resource helpers, PLL
headers and Makefile, clock binding header, Kona PLL DTS, matching shared PHY
implementation and its headers/catalog. All paths, byte sizes, SHA-256 values,
Git blob identities and immutable source URLs are recorded in the generated
[register and clock manifest](dsi-pll-7nm-registers.json).

Those thirteen files are the table generator's inputs. Three additional
rate-semantics sources at the same revision are independently retained and
byte-hashed in
[the divider-semantics provenance](../evidence/dsi-pll-20261124-6a5/divider-semantics-sources.json):
`drivers/clk/qcom/clk-regmap-divider.c`, `drivers/clk/clk-divider.c`, and
`drivers/clk/clk-fixed-factor.c`. The combined table and rate-semantics
provenance therefore covers sixteen distinct primary files. The thirteen-input
generation gate does not claim to verify all rate-calculation semantics.

The source dispatch selects `MDSS_DSI_PLL_7NM_V4_1` and the common 7 nm driver.
For this variant both normal and shadow VCO views receive the 600 MHz–5 GHz
range override. All four software VCO views declare `bi_tcxo` and a 19.2 MHz
source reference. That declaration does not authorize manufacturing a running
input clock when the connected carrier is unknown. The
[binding header](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/include/dt-bindings/clock/mdss-7nm-pll-clk.h)
contains 52 DSI IDs: bank 0 uses 0–25 and bank 1 uses 26–51. The six DP IDs in
the same header form a separate namespace and are excluded. Normal and shadow
CCF views do not create four physical PLLs.

The generic OUTDIV, bit-divider and pixel-divider layers call
`divider_recalc_rate`, which rounds upward for a nonzero divisor. Each layer
rounds separately. The fixed-factor paths multiply then truncate with
`do_div`, so the byte-clock `/8` or `/7`, post-bit `/2`, post-VCO `/4`, and
CPHY `2/7` paths round downward. The reference retains these distinct CCF
projection rules before converting integer Hz to QEMU Clock periods. A
period-derived Hz observation can differ slightly from the mathematical
integer rate because Clock periods themselves are quantized. The CCF
`ALLOW_ZERO` fallback returning a parent rate is not evidence of a physical
zero-coded divisor; zero bit/pixel divisors remain without a known carrier in
this bounded model.

## Exact ownership

The matching
[Kona source DTS](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi)
and original DT agree on these named resources:

| Resource | Bank 0 base | Bank 1 base | Bytes | Reference ownership |
| --- | --- | --- | ---: | --- |
| PHY aperture | `0x0ae94400` | `0x0ae96400` | `0x800` each | Two transparent containers with exact leaf owners |
| PLL aperture | `0x0ae94900` | `0x0ae96900` | `0x260` each | Same canonical state at PHY `+0x500` |
| Dynamic refresh | `0x0ae94200` | `0x0ae96200` | `0x100` each | Unimplemented; remains transparent |
| Shared GDSC | `0x0af03000` | same | `0x8` | Existing MDSS power-domain owner |

Each bank maps nine shared PHY words and 145 directly used PLL words: 154
words in eight leaves, 616 bytes. The two-bank device therefore has 308 words,
16 leaves and 1,232 mapped bytes. The leaves, relative to each PHY base, are:

| Offset | Bytes | Contents |
| --- | ---: | --- |
| `0x010` | `0x010` | Clock configuration, mode and receive-buffer control |
| `0x024` | `0x004` | Shared PHY control 0 |
| `0x02c` | `0x008` | Shared PHY control 2 and 3 |
| `0x03c` | `0x004` | PHY PLL start/stop control |
| `0x128` | `0x004` | Digital reset control |
| `0x500` | `0x0cc` | First directly programmed PLL range |
| `0x5dc` | `0x014` | Decimal/fraction mux and active programming bank |
| `0x600` | `0x164` | Remaining directly programmed PLL words |

The declared but unused PLL factor banks at `+0xcc`, `+0xd0`, `+0xd4`, `+0xd8`,
`+0xf0`, `+0xf4`, `+0xf8` and `+0xfc` remain holes. Register declarations alone
do not establish an implemented operation. The 34 dynamic-refresh declarations
also do not become mapped operations.

`PLL_PERF_OPTIMIZE` is a source-programmed word at PLL `+0x260`. It lies exactly
outside the named PLL resource's end-exclusive `0x260` bytes, but at PHY
`+0x760` inside the original `0x800` PHY aperture. It is owned once through that
canonical physical address. The original DT size is not changed and a second
overlapping storage region is not created.

## Programming and status rules

Source programming order is initialization, mode detection, configuration and
factor calculation, commit, frequency-independent writes, then optional SSC
commit. The generated table preserves this order when selecting the final
v4.1 values. In particular:

| PLL offset | Field | Final v4.1 value |
| --- | --- | --- |
| `0x0bc` | Core input override | `0x12`, overriding initialization `0x10` |
| `0x170` | Integer gain/filter band 1 | `0x82`, after `0x54` and `0x84` |
| `0x090` | PFILT | `0x2f`, after `0x29` |
| `0x094` | IFILT | `0x3f`, after `0x2a`; v1/v2 values are excluded |
| `0x1a4` | Bias control 1 | `0x40`; the v1 value `0x66` is excluded |
| `0x234` | Gear band selection | `0x22`; the v1-only `0x21` write is excluded |
| `0x260` | Performance optimization | `0x22` |

There are 128 required configuration words per bank: 113 fixed final values,
four decimal/fraction words, three rate-dependent words, one mode word and
seven SSC words. System mux and output divider have separate explicit rules.
The table's recipe values describe programming requirements, not silicon or
reference reset state. Repeated writes and their source lines are retained in
the manifest so final values can be reviewed without trusting a generated C
initializer alone.

The fifteen observation words at PLL `0x1b0–0x1cc`, `0x200–0x20c`, `0x218`,
`0x21c` and `0x220` are initialized by source writes of zero. The reference
accepts only that zero write; it cannot set LOCK or qualify a configuration.
Only `COMMON_STATUS_ONE` at `+0x1b0` has a supported read, exposing computed
LOCK bit 0. Reads of the other unmodeled observation words are rejected.
`LOCKTIME0/1` are not promoted to status hardware merely because of their names.

Shared PHY write masks cover the fields used in the pinned PLL and matching
[PHY driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/techpack/display/msm/dsi/dsi_phy_hw_v4_0.c):
`CFG0=0xff`, `CFG1=0x37`, `GLBL_CTRL=0x40`, `RBUF=1`, `CTRL0=0x7f`,
`CTRL2=0x40`, `CTRL3=4`, `PLL_CNTRL=1`, `SPARE4=1`. These are the accepted
source-used domains of this reference, not a complete silicon reserved-bit
specification. Ordinary PLL data is byte-wide; fraction-high, SSC-control and
output-divider words have two-bit masks. A permitted stored value can still
fail the complete recipe qualification.

## Timing and implementation boundary

The source uses a 250 ns delay after changing PLL bias. The reference adopts
that source-evidenced bias interval. Its lock transition after 64 known XO
cycles is an explicit deterministic reference policy; no source or measurement
is claimed to establish a 64-cycle physical lock time. The source's 100 us
poll cadence and 5 ms timeout are software waiting limits, not lock latency.

Power and clock inputs must retain knownness. Unknown or absent carrier, lost
power, incomplete programming, reset, invalid recipe or missing start cannot
be turned into a running clock by returning a convenient LOCK value. Source
normal/shadow views share physical register state. The digital reference does
not execute dynamic refresh, invent DFPS trim data, return arbitrary analog
status, emulate lanes/panel/scanout, or establish Android UI success. Full PHY
REFGEN readiness is a different condition from PLL LOCK.

The source's only `SPARE4` pulse path follows a successful PLL lock poll in
`dsi_pll_enable`. The reference therefore qualifies both pulse assertion and
deassertion only while that bank is already locked. A pulse during pending
lock cannot become a saved digital-ready credit for later completion; migrated
asserted/ready flags must satisfy the same condition. This follows the reviewed
source sequence and does not claim that silicon cannot be reset before lock.
The unestablished external slave carrier remains unknown even if the source
writes the slave's raw pulse or global-enable bits.

## Reproduction and evidence level

The two generated outputs are
[the C data table](../../hw/misc/qcom-sm8250-dsi-pll-data.inc) and
[the JSON manifest](dsi-pll-7nm-registers.json). From the repository root:

```sh
python3 tb371fc/scripts/verify_dsi_pll_generation.py --out /tmp/dsi-pll-generation
```

The gate downloads only the thirteen immutable public source files, verifies
every SHA-256, regenerates both outputs, and compares their complete bytes.
Use `--source-root /path/to/primary-r11` to perform the same checks with already
downloaded sources. A wrong source byte fails before any generated output is
written. An output mismatch makes the gate fail. The report schema is
`tb371fc-dsi-pll-generation-v1`; successful reproduction has thirteen inputs
and exactly two output comparisons.

[Source validation](../evidence/dsi-pll-20261124-6a5/source-validation.json)
records local pin verification, regeneration and source-tamper checks. These
are source/data checks. Compiled component behavior requires the separate
QEMU model tests and CI receipts; native advancement requires a separately
bound run using the original inputs. No compiled or native pass is inferred
from source generation.
