# SM8250 DSI 7nm v4.1 PLL and shared PHY reference

This increment addresses the first abort in original-input run007: physical
`0x0ae94418`, a 32-bit read from PHY0 `GLBL_CTRL`, in
`vco_7nm_recalc_rate`. The observed guest was CPU5, PID1; VIDEOCC had already
registered. This is a PLL/shared-PHY protocol candidate. Its compiled acceptance
and next original-firmware run require their own exact source/binary receipts.
It does not attest Android userspace, display output, panel signaling, or a
complete original-firmware boot.

Enable explicitly:

```text
-global qcom-sm8250-dsi-pll7nm.enabled-reference=on
```

The model is disabled by default. The QOM object is `/machine/dsi-pll7nm`.
The two banks are one owner because each PLL address range is nested inside
the same physical PHY view. No second independent PLL region obscures PHY
registers, and no display-controller bank is backed by opaque RAM.

## Actual DT and fixed primary sources

The [actual-DT excerpt](../evidence/dsi-pll7nm-20261124-e1a9/actual-dt-dsi-nodes.json)
comes from the already collected, allowlisted DT inventory with source DT SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
All four selected nodes have resolved dependencies and no rejected properties.
The larger inventory still has unrelated unresolved entries; this excerpt does
not assert complete system coverage.

| View | Bank 0 | Bank 1 | Actual resource size |
| --- | --- | --- | --- |
| PHY driver | `0x0ae94400` | `0x0ae96400` | `0x7c0` |
| PHY view named by PLL driver | same | same | `0x800` |
| PLL subresource | `0x0ae94900` | `0x0ae96900` | `0x260` |
| Dynamic refresh | `0x0ae94200` | `0x0ae96200` | `0x100` |
| MDSS GDSC view | `0x0af03000` | same | `8` |

Actual compatibles are `qcom,dsi-phy-v4.1` and
`qcom,mdss_dsi_pll_7nm_v4_1`. PHY phandles are 1386/1387; PLL phandles are
1388/1389. Both PLLs name `iface_clk` from DISPCC clock ID0. Both PHYs name
PM8150 L5 as their `vdda-0p9` supply. Only PLL0 references the DFPS reserved
memory node. The inventory records the presence of SSC enable/mode and platform
lane configuration properties, but does not provide their values. There is no
captured PHY revision, REFGEN ready, lane-stop, ULPS, or PLL handoff register
snapshot here.

The matching 52-ID interface is pinned to two official public source commits:

- [Display module `1d242bf29cb92defd2d569c42286e199de382bee`](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8250/tree/1d242bf29cb92defd2d569c42286e199de382bee/kernel/msm-4.19/techpack/display):
  DSI PLL, PHY v4.0 operations selected for v4.1, catalog, and resource helpers.
- [Core `601d399161053be6df99c089abd7780cf197aa45`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/tree/601d399161053be6df99c089abd7780cf197aa45):
  the 52-ID `mdss-7nm-pll-clk.h`, generic divider, fixed-factor, and regmap
  divider behavior.

All 13 complete file hashes, byte lengths and Git blob identities are in
[source-pins.json](../evidence/dsi-pll7nm-20261124-e1a9/source-pins.json).
The earlier clock-controller core `94304e12...` contains no matching display
implementation. The older monolithic display source at `cad09e06...` has a
36-ID interface and lacks the observed PHY-mode read in recalc. Neither is used
as the DSI implementation contract. The matched public interface is a
source-qualified reference, not a claim that its complete code is byte-identical
to Lenovo's production kernel.

## Register ownership and unknown inputs

The [generated inventory](dsi-pll7nm-sm8250-registers.json) contains **154 stored
words per bank**: 145 PLL words directly written by the fixed source, plus nine
shared PHY controls. Their eight contiguous sparse leaves occupy 616 bytes per
bank. Each dynamic bank adds only its 4-byte control word. Total: **308 stored
words, 18 exact windows, 1240 mapped bytes**. Transparent PHY containers span
`0x800`; holes have no handler or storage.

The source explicitly writes `PLL_PERF_OPTIMIZE` at PLL `+0x260`, which is PHY
`+0x760`. Although this is one word past the declared PLL subresource length,
it is inside both actual PHY views. The single physical owner covers this
proven word without expanding the PLL subresource into a generic bank.

| Shared PHY word | Offset | Accepted stored bits |
| --- | --- | --- |
| `CLK_CFG0` | `0x010` | `0xff` |
| `CLK_CFG1` | `0x014` | `0x3f` |
| `GLBL_CTRL` | `0x018` | CPHY bit6 |
| `RBUF_CTRL` | `0x01c` | FIFO request bit0 |
| `CTRL_0` | `0x024` | `0x7f`, including PLL bias bit5 |
| `CTRL_2` | `0x02c` | source full-rate selection bit6 only |
| `CTRL_3` | `0x030` | source global-clock control bit2 |
| `PLL_CNTRL` | `0x03c` | start bit0 |
| `GLBL_DIGTOP_SPARE4` | `0x128` | digital reset request bit0 |

`COMMON_STATUS_ONE` at PLL `+0x1b0` exposes the derived lock bit0. The source
initialization sequence writes zero to this and several opaque status words.
Those writes are accepted as no-ops; zero never sets or clears a derived lock.
Nonzero writes to status are rejected. Opaque analog/debug/calibration status
reads are rejected even though their source initialization zero-write is
recognized. Other PLL configuration words have bounded 8-bit storage; fraction
high, output divider, and SSC control have the narrower source masks.

Unknown PHY revision (`+0x000`), REFGEN status (`+0x140`), ULPS (`+0x148`),
lane stopstate (`+0x14c`), timing and lane words remain outside this increment.
In particular, returning zero for unknown ULPS would incorrectly acknowledge
all requested lanes; it is not used as a safe-looking default. The independent
REFGEN-known/ready input pair qualifies PLL operation, but does not implement
the rest of the PHY's status-register protocol.

Dynamic control reads zero and accepts only zero writes. Every nonzero control
write is rejected atomically. The other dynamic words remain unassigned. There
is no shadow sequencer, target-write execution, DSI-controller completion IRQ,
or fabricated refresh-complete bit. Stored control requests do not prove analog
events or FIFO/lane execution.

The existing MDSS GDSC and DISPCC MISC word at `0x0af08000` retain their original
owners. Camera, NPU and VIDEO GDSC windows and the existing MMCX three-way
fanout remain unchanged.

## Qualified PLL state and timing

The explicit reference cold state has zero digital configuration, DPHY mode,
no pending lock and no carrier. It is not a firmware handoff snapshot. A PLL
can become qualified only when all of these are present:

1. The DSI reference is enabled, XO is the connected 19.2-MHz source, and the
   actual MDSS provider reports known-on power.
2. REFGEN is explicitly known-ready. No native provider is connected in this
   increment. QTest-only fixtures can supply these input observations; an
   unknown REFGEN cannot be made ready by writing `CTRL_0`.
3. PHY start and bias are asserted, the PLL system mux is the source's `0xc0`,
   and the complete v4.1 initialization/final configuration recipe is valid.
4. DEC/FRAC, mode-dependent `CMODE_1`, rate-dependent analog selections and
   optional SSC parameters are within the source-qualified domain.

Qualification starts a **5000-ns virtual reference deadline**. Lock and carrier
appear only after the deadline. This is deterministic digital reference timing,
not a measurement or analog PLL simulation. Invalidating power, REFGEN, XO,
recipe, mode or start removes lock and pending/active factor state. A changed
PLL configuration starts a new qualification interval; output-divider and
shared divider/mux changes update derived carriers without inventing a latch
ACK. Rewriting an unchanged configuration or writing a source status zero does
not create a new transition.

The source range for v4.1 is **600 MHz through 5 GHz**. Its integer factor is

```text
q = floor(requested_hz * 262144 / 38400000)
carrier_hz = floor(q * 38400000 / 262144)
```

The source selects analog recipe buckets using the requested frequency before
floor-quantizing the factor. The model therefore intersects each recipe's
integer-Hz range with

```text
[ceil(q * 38400000 / 262144), ceil((q + 1) * 38400000 / 262144) - 1]
```

and requires one complete matching bucket. It does not reclassify a request
such as exactly 3.1 GHz using its slightly lower quantized carrier. Nor does it
accept arbitrary combinations across buckets. The seven exact ranges and
their inverter/VCO/analog values are in the generated inventory.

SSC-disabled state requires its six programmable parameters to remain zero.
Enabled SSC is qualified for the fixed source defaults: 31500-Hz modulation,
4800 ppm, adjustment period 2, derived period 302, and either down or center
selection. The step calculation uses the source's intermediate floor followed
by nearest rounding. Only the nominal carrier is exposed; no spread waveform
or other unobserved DT override is synthesized.

## Clock formulas and DISPCC connections

There are **16 output Clock objects**, eight per bank. The 52 binding IDs
include software aliases and shadow paths; they do not imply 52 physical PLLs.
The final software-only byte/pixel mux callbacks write no hardware and return
selector zero. No fictitious mux register is added for those aliases.

| Output suffix | Formula before QEMU Clock quantization |
| --- | --- |
| `vco` | qualified factor carrier above |
| `pll_out` | `ceil(vco / 2^OUTDIV)` |
| `bitclk` | `ceil(pll_out / bit_div)`; zero field means divisor 1 |
| `post_bit_div` | `floor(bitclk / 2)` |
| `post_vco_div` | `floor(pll_out / 4)` |
| `cphy_post_vco_div3_5` | `floor(2 * pll_out / 7)` |
| `phy_pll_out_byteclk` | DPHY `floor(bitclk / 8)`; CPHY `floor(bitclk / 7)` |
| `phy_pll_out_dsiclk` | pixel source divided with ceiling; zero field bypass |

The byte divisors are parsed from the actual fixed-factor declarations during
generation. For DPHY the pixel source selector is bit clock (0) or post-bit
divide (1). CPHY uses selector 3 and post-VCO divide by 3.5. Other combinations
remain unknown. Shared `CFG0` fields are `ONE_BASED | ALLOW_ZERO`, not `field+1`.
Fixed factors multiply before flooring; ordinary dividers round up. An unknown
pixel selector suppresses only the pixel carrier; it cannot gate the independent
byte fixed-factor path.

Physical byte/pixel outputs also require global clock enable bit5, `CTRL_3=4`,
deasserted digital reset, and a known internal route. `CLK_EN_SEL` bit4 is a
resynchronization selection control, so clearing it does not permanently gate
either mode. RBUF storage alone does not attest FIFO execution. External-route
selection is stored but produces no claimed external carrier: the fixed source
observes the slave selector without proving the exact physical tap to model.

The board connects only these four carriers to their same-named DISPCC inputs
when the DSI reference is enabled:

- `dsi0_phy_pll_out_byteclk`
- `dsi1_phy_pll_out_byteclk`
- `dsi0_phy_pll_out_dsiclk`
- `dsi1_phy_pll_out_dsiclk`

An enabled provider remains connected even when its carrier is zero. QTest
cannot override a connected parent. With DSI disabled, those inputs retain the
old isolated unknown/fixture behavior. DP/eDP, the undeclared PLL1-even alias
and parentless critical XO remain unknown.

All DSI frequency handling uses 64-bit period conversions. DISPCC consumes the
actual upstream period and publishes periods without the unsigned-32-bit Hz
helpers. Only the four DSI input slots admit the source's 5-GHz range; the other
13 retain their previous 32-bit/roundtrip limit. A requested 5-GHz fixture has
period 858993459 and decodes as 5000000001 Hz; this legal quantization must not
be rejected or wrapped. The model's factor-quantized 5-GHz VCO is 4999999951 Hz
with period 858993467, observed downstream as 4999999954 Hz.

## Migration and observation

The [wire layout](../evidence/dsi-pll7nm-20261124-e1a9/migration-layout.json)
is independent VMState version 1, minimum 1, identity `0x44534941`, with a
**1356-byte base payload**. It stores profile/geometry identity, both physical
bases and dynamic bases, 308 register words, active/pending factors, two lock
deadlines, last XO observation, lock/power/REFGEN observations and one input
Clock period. Clock multipliers/dividers must remain 1:1; other optional Clock
subsections are rejected. Output clocks are derived, not serialized as trusted
status values.

The board creates providers in MDSS → DSI → DISPCC order. DSI validates its
migrated power observations against the already restored MDSS pure state and
its XO period against the connected source. DISPCC validates each connected
DSI input period against the restored upstream output. Neither step repairs a
disagreement by propagating the source over the incoming state.

Provider callbacks may precede a consumer's own `pre_load`. DSI therefore
records input levels but does not advance state or propagate clocks during
`INMIGRATE` or `RESTORE_VM`. Its `post_load` validates registers, factors,
qualification and deadline consistency, rebuilds outputs without callbacks,
and schedules deadlines without consuming them. Pure QOM observers never
advance virtual time or repair a transition. Incoming-defer targets can have
stale stored configuration, but this phase guard prevents forming a new stale
locked carrier there; the tests must describe that boundary accurately.

The existing 1676-byte DISPCC v1 layout and identity remain unchanged.

## Validation gates

`dsi-pll7nm-source-generation` checks all 13 source byte/hash/blob pins and
reproduces the generated C table and JSON inventory. `dsi-pll7nm-sm8250-reference`
uses a separately encoded literal oracle against the compiled binary: 41
groups, three completed process migrations, two profile mismatch rejections
and two corrupted-stream rejections, preserving four original streams and two
mutated copies. It covers unknown MMIO, source recipe boundaries, both PHY
modes, real MDSS/XO inputs, DISPCC connections, high frequency, loss of lock and
first restored state. The old DISPCC 41-group gate retains its scope while its
four isolated DSI fixtures exercise the new 64-bit bound.

Together these add two gates to the existing 87+5 plan, yielding **89 component
gates plus five Linux gates**. Host generation and syntax checks are separate
from compiled execution. The exact new CI and native run receipts will establish
which of those gates have actually run for a published candidate.
