# Kona-v2 VIDEOCC bounded digital reference

`qcom-sm8250-videocc` supplies the source-defined clock and reset register
protocol for the original TB371FC `qcom,videocc-kona-v2` node. It is a
separate, opt-in device:

```sh
-global qcom-sm8250-videocc.enabled-reference=on
```

The reference covers two Lucid PLLs, five integer RCGs, four read-only
dividers, eight branches and seven reset outputs. It does not implement
the video codec, firmware execution, video DMA, analog PLL behavior or
physical transition timing. Its four sibling VIDEO GDSC owners retain
their existing registers, power protocol and migration section.

Source generation, compiled device tests and original Android execution
are separate evidence. The generation checks described below establish
reproducible tables. A compiled gate must execute the C model and compare
independent expected values. Neither result by itself establishes original
Android userspace, a rendered interface or boot completion.

## Original DT and native frontier

The [original VIDEOCC node extraction](../evidence/videocc-20261124-6a5/actual-dt-videocc-node.json)
is bound to original DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The controller's physical resource is `0x0abf0000`, size `0x10000`, and its
compatible is `qcom,videocc-kona-v2`. `cfg_ahb_clk` names GCC phandle 22,
vendor binding 205. `vdd_mx-supply` is phandle 105 and `vdd_mm-supply` is
phandle 104, the existing `mmcx.lvl` provider.

The [native frontier receipt](../evidence/videocc-20261124-6a5/native-frontier-after-dispcc.json)
records the preceding original-input execution separately. It is the
evidence for the failure that motivates this device; it is not a successful
original-kernel execution of this VIDEOCC implementation.

Only 55 unique aligned 32-bit words are assigned. Adjacent supported words
form 26 sparse windows totaling 220 bytes. The enclosing DT resource and
the vendor regmap maximum (`0xf4c`) do not authorize a broad successful
register bank. Unknown neighbors, Lucid's opaque `STATUS` words and unused
RCG fraction words remain unassigned. The
[generated register manifest](videocc-sm8250-registers.json) lists every
word, mask, source owner and window.

The VIDEO GDSC words at offsets `0xbf8`, `0xc98`, `0xd18` and `0xd98` are
explicitly excluded from every VIDEOCC window. Their physical addresses
remain `0x0abf0bf8`, `0x0abf0c98`, `0x0abf0d18` and `0x0abf0d98`.

## Fixed primary sources and regeneration

The generator checks ten complete input files at OnePlusOSS/
android_kernel_oneplus_sm8250 commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`. The central declarations are
[videocc-kona.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/videocc-kona.c)
and its
[clock/reset binding](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/dt-bindings/clock/qcom,videocc-kona.h).
Their SHA-256 values are respectively
`33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5`
and `0f51753e9eb497399d3c2d9a362e6d3ee7baf4f789b6e6628a7911b00f89d774`.

The remaining pins cover `clk-alpha-pll.c`, `clk-rcg2.c`, `clk-branch.c`,
`clk-branch.h`, `clk-regmap-divider.c`, generic `clk-divider.c`, `reset.c`
and `clk-rpmh.c`. The manifest contains all ten complete hashes, exact
clock and reset binding IDs, source declaration lines, parent selectors,
PLL configuration tables and the Kona-v2 probe order.

From the repository root:

```sh
python3 tb371fc/scripts/generate_videocc_sm8250.py /path/to/pinned/kernel
python3 tb371fc/scripts/verify_videocc_generation.py \
  --source-root /path/to/pinned/kernel --out /path/to/new/generation-result
```

Omit `--source-root` to fetch the same pinned public bytes. The verifier
requires a new output directory, checks each input hash, runs generation
in isolation and byte-compares both generated outputs with the committed
`qcom-sm8250-videocc-data.inc` and `videocc-sm8250-registers.json`.
Its result schema is `tb371fc-videocc-generation-v1`; the source manifest
schema is `tb371fc-videocc-source-facts-v1`. A hash mismatch, generation
failure or output mismatch makes the verifier fail.

## Lucid PLLs and the v2 fixup

PLL0 starts at offset `0x42c`, PLL1 at `0x7d0`. Both use the Lucid register
layout and the source VCO interval of 249.6 MHz through 2 GHz. Each has
14 modeled words: MODE, L, CAL_L, USER_CTL and its two upper words,
CONFIG_CTL and its two upper words, TEST_CTL and its two upper words,
OPMODE and ALPHA. The shared alpha-PLL source defines the offsets;
opaque `STATUS` is excluded.

The actual probe first calls `clk_lucid_pll_configure` for PLL0's base
configuration, then PLL1's base configuration. Only afterward does the
Kona-v2 fixup configure PLL1 again and replace MVS1's frequency table:

| Configuration | L | CAL_L | ALPHA | CONFIG_CTL_U1 |
| --- | --- | --- | --- | --- |
| PLL0 base | `0x25` | `0x44` | `0x8000` | `0x029a699c` |
| PLL1 base | `0x29` | `0x44` | `0xfaaa` | `0x029a699c` |
| PLL1 Kona-v2 fixup | `0x2b` | `0x44` | `0xc000` | `0x329a699c` |

All three declare CONFIG_CTL `0x20485699`, CONFIG_CTL_U `0x2261`,
USER_CTL_U `0x805`, USER_CTL zero and USER_CTL_U1 zero. The helper skips
zero-valued configuration fields; these declarations are not fabricated
zero writes. It also skips reconfiguration if OPMODE and OUTCTRL already
indicate a running PLL. Its configuration tail sets UPDATE_BYPASS, clears
OUTCTRL, selects STANDBY and asserts RESET_N. The manifest preserves this
ordering instead of preloading the final recipe as cold hardware state.

Cold model registers are zero. The reference accepts the base configuration
writes and the later fixup. Successful PLL1 feedback requires the final
v2 CONFIG_CTL_U1 value, while PLL0 requires its own base value. The model
stores requested L/ALPHA separately from the active and pending values.
An enabled output also requires a supported recipe, a real input carrier,
the allowed VCO rate, completed lock, OUTCTRL and USER_CTL value 7.

Lock, calibration and update acknowledgment are derived status in MODE,
not permanently asserted bits. A reference lock takes 5000 virtual
nanoseconds; the UPDATE latch takes 1000 nanoseconds and requires a new
5000-nanosecond lock interval. Changing or losing the XO carrier invalidates
lock. Clearing UPDATE or UPDATE_BYPASS withdraws acknowledgment; clearing
RESET_N cancels pending state and calibration. These delays define a
testable digital protocol and are not measured silicon timings.

## Roots, parent provenance and dividers

All five RCGs have `mnd_width=0`, `hid_width=5` and `clk_rcg2_ops`. Each
owns only CMD and CFG at CMD + 4. Fractional modes and M/N/D register
accesses are unsupported. A valid UPDATE latches CFG after 1000 virtual
nanoseconds. Until that deadline, output uses the prior active CFG.

| RCG / CMD offset | Selector 0 | Selector 1 | Selector 7 | HW_CLK_CTRL / safe configuration |
| --- | --- | --- | --- | --- |
| AHB / `0xbd4` | `bi_tcxo_ao` | unsupported | test input | both declared |
| MVS0 / `0xb94` | `bi_tcxo` | `video_pll0` | test input | both declared |
| MVS1 / `0xbb4` | `bi_tcxo` | `video_pll1` | test input | both declared |
| SLEEP / `0xef0` | `chip_sleep_clk` | unsupported | test input | neither declared |
| XO / `0xecc` | `bi_tcxo_ao` | unsupported | test input | neither declared |

Other selectors are unsupported. The source's AHB and XO rate request is
19.2 MHz. MVS0 requests 720, 1014, 1098 or 1332 MHz; Kona-v2 MVS1 requests
840, 1098 or 1332 MHz. Its replaced base table is also retained in the
manifest for review. These are software requests, not evidence of a
carrier being present.

The input order is `bi_tcxo`, `bi_tcxo_ao`, `core_bi_pll_test_se`,
`chip_sleep_clk`. The fixed RPMh source declares the first two from
`xo_board` through divisor 2 and the `xo.lvl` resource. Board integration
connects both to the existing established carrier. The test and sleep
inputs have no established board provider and remain absent. In particular,
the sleep frequency-table entry of 32000 does not create a 32 kHz input.
QTest can supply an unconnected input for component testing; it cannot
override a connected clock, and those fixture properties are absent from
ordinary TCG execution.

| Divider | Offset | Parent |
| --- | --- | --- |
| MVS0_DIV | `0xd54` | MVS0 root |
| MVS0C_DIV2_DIV | `0xc54` | MVS0 root |
| MVS1_DIV | `0xdd4` | MVS1 root |
| MVS1C_DIV2_DIV | `0xcf4` | MVS1 root |

All four declare the read-only divider operations, shift 0 and width 2,
with no custom divider table or divider flags. The source's generic
calculation uses divisor `field + 1` and rounds the divided rate upward.
Thus the supported raw values 0, 1, 2 and 3 mean divide by 1, 2, 3 and 4.
The substring `DIV2` supplies no additional factor. Cold raw value zero is
the reference's explicit fixture state, not a claim about a physical fuse.
Every guest MMIO write to these words is rejected; QTest-only fixture
properties permit independent coverage of all four encodings.

## Branches and shared reset words

Each branch's enable bit is bit 0 of its own halt register. Bit 31 reports
CLOCK_OFF from the derived parent rate and the actual local or remote
request. The four `BRANCH_HALT_VOTED` declarations receive four independent
remote-vote inputs in binding order. A vote can request an available
parent; it cannot manufacture an absent input frequency. CCF's critical
flag remains source metadata and does not silently pre-enable a branch.

| Branch | Offset | Parent | Remote vote index |
| --- | --- | --- | --- |
| AHB | `0xe58` | AHB root | none |
| MVS0 | `0xd34` | MVS0_DIV | 0 |
| MVS0C | `0xc34` | MVS0C_DIV2_DIV | none |
| MVS1 | `0xdb4` | MVS1_DIV | 1 |
| MVS1_DIV2 | `0xdf4` | MVS1C_DIV2_DIV | 2 |
| MVS1C | `0xcd4` | MVS1C_DIV2_DIV | 3 |
| SLEEP | `0xf10` | SLEEP root | none |
| XO | `0xeec` | XO root | none |

The reset binding has seven entries. The reset driver computes
`BIT(map->bit)` and performs a masked update, so the two explicit bit-2
ARES entries must share their branch words:

| Reset ID / binding | Offset | Mask | Word owner |
| --- | --- | --- | --- |
| 0 / CVP_VIDEO_CC_INTERFACE_BCR | `0xe54` | `0x1` | reset |
| 1 / CVP_VIDEO_CC_MVS0_BCR | `0xd14` | `0x1` | reset |
| 2 / VIDEO_CC_MVS0C_CLK_ARES | `0xc34` | `0x4` | MVS0C branch |
| 3 / CVP_VIDEO_CC_MVS0C_BCR | `0xbf4` | `0x1` | reset |
| 4 / CVP_VIDEO_CC_MVS1_BCR | `0xd94` | `0x1` | reset |
| 5 / VIDEO_CC_MVS1C_CLK_ARES | `0xcd4` | `0x4` | MVS1C branch |
| 6 / CVP_VIDEO_CC_MVS1C_BCR | `0xc94` | `0x1` | reset |

Each shared word appears once in the sorted register table with writable
and stored mask `0x5`. Branch and reset descriptors reference that same
index. Updating ARES preserves the enable request; updating the enable
request preserves ARES when the driver performs its RMW. Reset outputs
are published from their exact masks. They do not substitute for the
separate GDSC power protocol or claim execution of a reset video engine.

Only aligned 32-bit device accesses are accepted. Writes with unsupported
bits fail before state mutation; documented derived status bits may be
carried through an ordinary RMW without becoming stored feedback. Other
PLL output selections fail explicitly. Reset clears model-owned register,
active, pending and timer state while preserving provider-owned input
carriers and remote votes. QOM getters observe state without advancing
timers or completing an update.

## Independent migration contract

The section `qcom-sm8250-videocc` is version 1/minimum 1 and uses identity
`0x56494332` (`VIC2`). Profile, identity, all geometry counts and physical
base are equality guarded. The normal, unit-ratio input-clock payload is
474 bytes, excluding the surrounding QEMU section framing:

| Offset | Bytes | Contents |
| --- | --- | --- |
| 0 | 52 | Thirteen guarded 32-bit profile/identity/geometry fields |
| 52 | 8 | Guarded physical base |
| 60 | 220 | Sorted register state |
| 280 | 32 | Active and pending L/ALPHA for both PLLs |
| 312 | 40 | Active and pending CFG for all five RCGs |
| 352 | 72 | PLL lock/latch and RCG update deadlines |
| 424 | 8 | Last observed XO frequency |
| 432 | 10 | Two lock, two ACK, two calibration and four remote-vote booleans |
| 442 | 32 | Four input Clock periods |

QEMU's default Clock state stores one 64-bit period per input. A non-unit
ratio creates an additional subsection; this device rejects a restored
non-unit ratio rather than accepting a changed clock profile. These
figures do not alter the independent 142-byte VIDEO GDSC payload or the
camera, MDSS and NPU migration sections.

Pre-load withdraws clock/reset outputs and removes local timers.
Post-load validates writable masks, clock ratio/range, the last XO
observation, active/pending states, feedback consistency and deadline
bounds before publishing any derived output. Pending updates remain
pending on a stopped target. Publication before first target MMIO uses
the incoming state without completing deadlines. Clock source restoration
does not overwrite an upstream provider's own state.

## Required compiled and native acceptance

The component fixture must use literal expectations independently derived
from the pinned vendor source. Required checks include sparse ownership,
the real base-then-v2 PLL configuration sequence, PLL lock/update/cancel
behavior, zero-MND root updates, unavailable input paths, every read-only
divider encoding, all four remote votes and independent ARES/branch RMW.
Profile-off behavior, ordinary TCG fixture isolation and reset behavior
are separate cases. Pending/running migrations must compare complete
source/target state before target MMIO or `cont`, and reject altered
identity/profile, illegal state and corrupted stream fields. The four
existing GDSC siblings must keep their independent state and ownership.

Those are required compiled checks until a completed receipt identifies
the exact source and binary and records their results. A subsequent native
run must preserve the original kernel, ramdisk, full DT and boot arguments,
record every attached LUN's identity and report kernel failures separately
from QMP process health. A VIDEOCC probe message alone is not Android boot
acceptance. Actual userspace and interface evidence must come from that
guest execution; this source or a QTest fixture does not provide it.
