# GPU GX GDSC reference controller

This change supplies the three captured GPU GX control windows while retaining
`qcom-gpu-cx-gdsc` as the separate CX controller. The new `qcom-gdsc` instance
at `/machine/gpu-gx-gdsc` models digital requests, isolation/reset requests,
and explicitly qualified power feedback. It does not execute GPU, GMU, or
secure firmware, implement graphics, or measure rails or retention.

## Evidence and ownership

The previously recorded TB371FC DT capture has SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
Its GX resources are independently corroborated by the [official Kona DT][dt]
and [upstream SM8250 GPUCC driver][gpucc]:

| Resource | Address | Implemented requests |
| --- | --- | --- |
| GX GDSCR | `0x03d9100c` | Collapse, explicit hardware-request selection, retention/wait configuration; qualified read-only power bit |
| GX domain control | `0x03d91508` | Clamp bit 0 and GMEM reset bit 4 |
| GX block reset | `0x03d91008` | Block reset bit 0 |

Only these four-byte windows are mapped. There is no GX hardware-status
syscon mapping, no GPUCC catch-all, and no second CX register model. Broader
GPUCC/GMU views must share these same regions rather than duplicate state.
The C model's optional hardware-status region is unused on this board.

The [Qualcomm downstream driver][driver] defines the register fields and the
normal enable sequence, including block reset, AON reset, clamp release, and
collapse release. The model preserves the requested clamp/reset values; it
does not claim that arbitrary sequences satisfy physical pulse-width or rail
requirements. Upstream uses `gdsc_gx_do_nothing_enable` for GX because its
GMU path owns activation. This model alone cannot complete that upstream path.

## Default unknown state and reference selection

With no option, `enabled-reference=false`: all GX window accesses fail
with diagnostics, power validity is false, and hardware/external input changes
do not invent a known state. The register apertures exist so diagnostics can
identify the unimplemented handoff; the default is not a zero-filled native
reset image.

Explicit reference control initialization is selected with:

```sh
-global qcom-gdsc.enabled-reference=on
```

The default reference request is collapsed, clamp asserted, block reset
clear, and zero retention/wait fields. The optional additional
`-global qcom-gdsc.reference-on=on` selects an uncollapsed/unclamped control
request. Neither option asserts or makes known a parent supply.
These are synthetic reference requests, not captured bootloader state.

## Requests, parent validity, and feedback

The hardware-request and aggregate external-vote pins are explicit digital
reference inputs. They start low. `HW_CONTROL` chooses between the hardware
input and inverse software collapse; the external vote can independently
retain a request. This reference rule is not a complete specification of
native GPU/GMU firmware voting.

The board's parent is **GFX**, as declared by the Kona GX
`VDD_GFX_LEVEL` parent-supply. RPMh resource index 4 is `gfx.lvl`;
its `power[4]` output drives GX `parent-on`. Resource presence establishes
`parent-known`. The two `res.gfx.lvl.*` fields use RPMh's existing uint64
QOM interface. CX retains its separate resource index 1 connection.
Absent RPMh or an absent GFX resource leaves GX parent validity unknown.
The modeled RPMh level is a digital command result, not physical power-good.

The reference result uses three-valued AND:

- No request resolves to known off even if the parent is unknown.
- A known-off parent resolves all requests to off.
- A request with known-on parent resolves to on.
- A request with unknown parent leaves feedback unknown.

Unknown feedback makes a GDSCR or optional status read fail explicitly.
Domain-control and block-reset request words remain readable in reference
mode because their contents do not claim power feedback. Invalidating an
output lowers `state-known` before `power-on`; establishing a known result
drives the result before asserting validity. Consumers must honor validity.

Digital feedback changes synchronously. This zero-delay reference contract
is not measured silicon timing. Retention/wait fields are preserved requests,
not implemented memory retention or inferred transition delays. Parent
changes preserve control/isolation/reset requests. They do not implement
physical brownout retention.

Supported aligned 32-bit writes accept GDSCR bits 0, 1, 11, and 15:12; the
read-only power bit can be carried through RMW but cannot force feedback.
Unsupported fields, including software override, reject the entire access.
Domain/reset words reject unsupported bits. Subword accesses reaching a
window fail without mutation. QEMU can split wider accesses at region edges;
whole-transaction atomicity across those edges is not claimed.

## Reset and migration

Reset restores only the declared reference requests. Hardware/external and
parent inputs remain owned by their producers and survive protocol reset.
VMState version 2 includes both parent level and validity as well as requests,
input votes, and immutable reference configuration. Version 1 omitted the
parent and is explicitly rejected. Destinations with either different
reference-enable or reset-request policy also reject migration.
Feedback and output requests are derived from restored state. A successful
sender status alone is insufficient: tests inspect the receiving process,
restored registers, GPIOs, inputs, and subsequent changes/reset behavior.

## Executable acceptance

```sh
python3 tb371fc/tests/test_gpu_gx_gdsc.py \\
  --qemu /absolute/path/qemu-system-aarch64 --out /fresh/gpu-gx-result
```

The suite plans 37 checks, including 12 actual migration requests:
eight completed receiving processes and four specific configuration rejections.
The GPIO fixtures explicitly inject a known parent only for isolated model
cases; the real RPMh integration case does not override the board connection.
That case verifies that CX votes cannot power GX, while APPS and display GFX
votes independently retain and release the GX parent.

Other cases cover default rejection, normal requests, negative MMIO accesses,
clamp/reset sources and observed GPIOs, parent loss/unknown feedback, unchanged
CX state, and migration of hidden votes, unknown/off parent state, requests,
isolation and reset. Every migration attempt is recorded at the outgoing
QMP call. Receipts include the exact QEMU and test hashes and fail unless all
planned checks and receiver outcomes are present.

A test plan or committed source is not a passing run. Read the matching CI
receipt for execution results. No complete native-DT boot, Android boot,
GPU acceleration, or whole-device equivalence follows from this gate.

[dt]: https://android.googlesource.com/kernel/msm-extra/devicetree/+/refs/heads/android-msm-bramble-4.19-android11-qpr1/qcom/kona.dtsi
[driver]: https://android.googlesource.com/kernel/msm/+/b6410c3f75259c0ca2df45a01838d2e46272723e/drivers/clk/qcom/gdsc-regulator.c
[gpucc]: https://linux.googlesource.com/linux/kernel/git/torvalds/linux/+/aaed66fadba2d2de8fe0daa0aa3eac827d2076b9/drivers/clk/qcom/gpucc-sm8250.c
