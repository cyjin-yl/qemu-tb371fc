# SM8250 GPUCC digital reference profile

The `qcom-sm8250-gpucc` device implements a source-bounded digital PLL1,
GMU clock root, 15 clock branches and five reset outputs. It does not execute
Adreno or GMU instructions and does not emulate analog lock, GPU rendering,
physical power regulation, a QDSS oscillator, or secure firmware.

The device is always present at `/machine/gpucc`. Its default
`enabled-reference=false` profile reports MMIO transaction errors; it does not
return fabricated clock status. Opt in explicitly:

```text
-global qcom-sm8250-gpucc.enabled-reference=on
```

Enabling this profile initializes no PLL frequency, branch vote or power rail.
The guest must program the documented sequence. The digital reset state is a
reference initial condition, not a capture of the tablet bootloader's state.
No changes to a supplied kernel or device tree are required by this device.

## Primary sources

The implementation uses the following exact public inputs:

| Input | Revision | SHA256 |
| --- | --- | --- |
| [Official OnePlus `gpucc-kona.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gpucc-kona.c) | `94304e12a3509eac7b3716bf574ce0b1fcaacbae` | `a9653a258c24397e366c04bb89c737d392f9cd198a2ba97c26c47a3250673c8a` |
| [Official OnePlus `clk-alpha-pll.c`](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/clk-alpha-pll.c) | same commit | `cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac` |
| [Stable Linux `gpucc-sm8250.c`](https://github.com/gregkh/linux/blob/v6.12.114/drivers/clk/qcom/gpucc-sm8250.c) | `v6.12.114` | `d51151ae5ac359a24e809fe4aea17b1d7fce55f809e813af52afe6f582a305ee` |

The same vendor commit's `clk-rcg2.c` provides the RCG update and HID divider
semantics. Both GPUCC drivers register PLL1 at offset `0x100`. The vendor parent
map mentions PLL0 but supplies no PLL0 configuration or registered PLL0 object;
the model therefore exposes it as an unconnected input rather than inventing a
second PLL. The source's software dummy rates are not physical measurements.

## Address ownership

The board adds seven small MMIO windows relative to `0x03d90000`:

| Offset | Size | Contents |
| --- | --- | --- |
| `0x100` | `0x44` | PLL1 register whitelist |
| `0x1000` | `0x8` | XO reset, CXO AON branch |
| `0x1058` | `0x14` | GX VSENSE/QDSS/GMU branches, CX reset |
| `0x1078` | `0x2c` | CX branches, sleep branch, GFX3D AON reset |
| `0x111c` | `0xc` | GMU reset, GMU CMD/CFG |
| `0x1160` | `0x4` | ACD reset |
| `0x5000` | `0x4` | HLOS GPU SMMU branch vote |

Only 36 aligned 32-bit registers are accessible. Unlisted offsets inside the
windows, including `0x130`, `0x134`, `0x13c` and `0x1060`, return transaction
errors. The source names PLL STATUS at `0x130` but does not establish the status
bit semantics implemented here; calibration status is MODE bit 27 instead.
Subword, unaligned and reserved-bit writes fail without altering the target
register. Writes containing read-only generated status bits ignore those bits.

The windows never cover CX GDSCR `0x106c` / HW control `0x1540`, or GX reset
`0x1008` / GDSCR `0x100c` / clamp `0x1508`. Those addresses retain their existing
owners and profiles. GPUCC does not create a second GDSC or GX reset shadow.

## PLL1

The modeled offsets are MODE `0x100`, L `0x104`, CAL_L `0x108`, USER
`0x10c/0x110/0x114`, CONFIG `0x118/0x11c/0x120`, TEST `0x124/0x128/0x12c`,
OPMODE `0x138` and 16-bit ALPHA `0x140`. L and CAL_L are bounded to 16 bits.
USER accepts the driver's grouped disable value 0 and enable value 7.
Individual main/aux selections 1 through 6 are outside this bounded profile:
they fail without changing USER, rather than being misreported as physically
stopped hardware. This is not an assertion that silicon requires all three
outputs for its main clock. OPMODE supports standby
0 and run 1. FSM voting, offline mode and other analog configurations are not
implemented.

The known reference configuration is CAL_L `0x44`, USER_U `0x805`, USER_U1 0,
CONFIG `0x20485699/0x2261/0x029a699c`, and zero TEST words. Other CONFIG/TEST
values are retained for readback but cannot produce a lock or latch ACK.
This bounds the reference model to the source's configuration instead of
pretending to model undocumented analog tuning.

MODE supports OUTCTRL bit 0, BYPASSNL bit 1, RESET_N bit 2, UPDATE bit 22 and
UPDATE_BYPASS bit 23. BYPASSNL is not a Lucid enable prerequisite. Generated
read-only status is PCAL_DONE bit 27, ACK bit 29 and LOCK bit 31.

On a valid RESET_N/run transition the model captures L/ALPHA and waits 5,000
virtual ns before setting LOCK and PCAL_DONE. A nonzero actual XO input and a
frequency in the source's 249.6 MHz through 2 GHz VCO range are required:

```text
frequency_hz = floor(xo_hz * (L * 65536 + ALPHA) / 65536)
```

For XO 19.2 MHz, L `0x1a` and ALPHA `0xaaa`, the exact integer result is
499,999,804 Hz. Output also requires OUTCTRL and all three USER output bits.
The driver writing a nominal 500 MHz configuration does not justify reporting
an exact 500,000,000 Hz result.

An UPDATE rising edge with RESET_N, UPDATE_BYPASS, valid configuration and live
XO captures a separate pending L/ALPHA snapshot. The old active rate remains
until the 1,000 ns latch deadline. At that deadline, the snapshot becomes
active, ACK becomes true and a further 5,000 ns lock interval begins if the
PLL is running. UPDATE remains set until software clears it; clearing it also
clears ACK. Later shadow writes cannot alter the pending snapshot. An invalid
request does not generate ACK. A stopped XO cannot produce lock or latch ACK;
changing the live XO causes a new lock interval for a valid active setting.

Standby stops the PLL output; asserting RESET_N low clears calibration and
pending PLL transitions. A system reset cancels every timer. The delays are
explicit digital ordering assumptions, not measured silicon timing.

## GMU root and clock inputs

CMD `0x1120` has UPDATE bit 0, ROOT_EN bit 1 and generated ROOT_OFF bit 31.
CFG `0x1124` permits HID `[4:0]`, source `[10:8]`, mode `[13:12]`, and HW control
bit 20. The root has no M/N divider, so nonzero mode is unsupported. HW control
is retained as a configuration bit; explicit ROOT_EN or a downstream GMU
branch request supplies demand in this reference model.

| Source | Input |
| --- | --- |
| 0 | Board `xo`, shared with GCC |
| 1 | `gpu_cc_pll0`, initially unconnected / 0 Hz |
| 3 | This device's PLL1 output |
| 5 | Actual GCC `gcc_gpu_gpll0_clk_src` output |
| 6 | Actual GCC `gcc_gpu_gpll0_div_clk_src` output |
| 7 | `core_bi_pll_test_se`, initially unconnected / 0 Hz |

Selectors 2 and 4 have no source. A valid UPDATE captures CFG and commits it
after 1,000 virtual ns, then clears UPDATE. Unsupported source encodings or
mode keep UPDATE pending without replacing the old active configuration.
The guest must clear and reassert UPDATE after correcting an invalid request.
An existing source whose input is stopped can still latch its configuration;
ROOT_OFF remains set and clock output is zero. Configuration ACK does not
assert that the parent is running.

HID 0 bypasses; HID >=1 divides by `(HID + 1) / 2`. Live parent changes propagate
through the active configuration without modifying CFG. The two GCC input
frequencies are never copied constants or fixed success values.

## Branches and reset outputs

Every branch combines software enable bit 0 with an independent
`remote-vote[index]`. The CX GMU branch additionally retains its documented
sleep/wakeup bits `[11:4]`. OFF bit 31 is derived from actual output frequency;
requesting a branch with no running parent leaves OFF set.

The remote-vote indices follow this order:

| Index | Linux clock name | Parent |
| --- | --- | --- |
| 0 | `gpu_cc_ahb_clk` | Separate unknown input |
| 1 | `gpu_cc_crc_ahb_clk` | Separate unknown input |
| 2 | `gpu_cc_cx_apb_clk` | Separate unknown input |
| 3 | `gpu_cc_cx_gmu_clk` | GMU root |
| 4 | `gpu_cc_cx_qdss_at_clk` | Shared `qdss_qmp_clk` input |
| 5 | `gpu_cc_cx_qdss_trig_clk` | Shared `qdss_qmp_clk` input |
| 6 | `gpu_cc_cx_qdss_tsctr_clk` | Shared `qdss_qmp_clk` input |
| 7 | `gpu_cc_cx_snoc_dvm_clk` | Separate unknown input |
| 8 | `gpu_cc_cxo_aon_clk` | Separate unknown input |
| 9 | `gpu_cc_cxo_clk` | Separate unknown input |
| 10 | `gpu_cc_gx_gmu_clk` | GMU root |
| 11 | `gpu_cc_gx_qdss_tsctr_clk` | Shared `qdss_qmp_clk` input |
| 12 | `gpu_cc_gx_vsense_clk` | Separate unknown input |
| 13 | `gpu_cc_sleep_clk` | Separate unknown input |
| 14 | `gpu_cc_hlos1_vote_gpu_smmu_clk` | Separate unknown input |

The nine separate inputs use `<Linux clock name>_parent`. The source supplies
no parent for those branches: names containing "cxo" or "sleep" are not enough
evidence to connect a frequency. `qdss_qmp_clk` also remains unconnected. An
AOP QDSS vote is a logical service request, not a measured or synthesized Hz.

The `reset[index]` outputs are 0 ACD (`0x1160`), 1 CX (`0x1068`), 2 GFX3D AON
(`0x10a0`), 3 GMU (`0x111c`), and 4 XO (`0x1000`). Their bit 0 controls the
logical reset line. They do not invent a coupling to clock gating. GX reset
is owned by the existing GX model and is not included in this internal list.

## Observation, testing and migration

Real Clock outputs retain their Linux names. Read-only QOM
`clk.<Linux clock name>.hz` reports the computed integer frequency; active
PLL L/ALPHA and RCG CFG are available as `pll-active-l`, `pll-active-alpha`,
and `rcg-active-cfg`. Boolean observation fields are `pll-locked`, `pll-ack`,
`pll-calibrated`, `pll-lock-pending`, `pll-latch-pending` and `rcg-pending`.

Only QTest processes expose `qtest-<input-name>-hz` uint32 fixture properties.
They change real input Clocks and cannot override a connected board provider.
Ordinary TCG exposes none of these injection properties. Synthetic fixture
frequencies are test data and must never be reported as tablet measurements.

VMState version 1 includes an immutable uint32 profile marker checked before
runtime fields, all 15 input Clocks, registers, active/pending snapshots,
absolute virtual deadlines, calibration/ACK/lock state and remote votes.
Reference/default profile mismatches are explicitly rejected in both
directions. Post-load validates state and rebuilds the earliest timer using
the saved deadlines; it does not restart a pending interval or complete it
early. Output Clocks are restored without propagating callbacks during
incoming migration. Reset GPIO levels are independently restored.

System reset enter cancels timers and clears software state without output
propagation. Reset hold publishes the resulting output state. Provider-owned
input frequencies and remote requests survive reset: a retained GMU request
can legitimately select the reset CFG's XO parent. Pending intervals do not
survive reset.

Component and migration tests establish this digital contract. A native
kernel registering GPUCC clocks is a separate integration observation and
does not establish GPU/GMU execution, rendering, or a complete Android boot.
