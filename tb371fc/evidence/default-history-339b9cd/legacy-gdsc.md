# GPU GDSC digital control model

The `qcom-gdsc` device implements the software-visible control and power-status
protocol needed by the captured TB371FC GPU CX and GX power-domain nodes. It
does not execute GPU, GMU, SPSS or TrustZone firmware, provide GPU acceleration,
implement GPUCC clocks, or model a physical supply, isolation cell or memory
retention circuit. Its external input signals and reset profiles are explicitly
defined digital reference interfaces.

## Evidence and scope

The unmodified production kernel's next observed abort after the REFGEN work
was an access to `0x03d9106c` in `gdsc_probe+0x770`. The captured node is
`/soc/qcom,gdsc@3d9106c`, compatible `qcom,gdsc`, named `gpu_cx_gdsc`.
Its `hw-ctrl-addr` resolves to the four-byte syscon at `0x03d91540`.

The same captured DT contains `gpu_gx_gdsc` at `0x03d9100c`, whose
`domain-addr` and `sw-reset` references resolve to `0x03d91508` and
`0x03d91008`. Each of these register resources is four bytes long.
The capture's SHA256 is
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
The complete DT and raw production-kernel diagnostics remain private.

Register meanings come from the Qualcomm downstream `gdsc-regulator.c`:

- [Official Android source at b6410c3f](https://android.googlesource.com/kernel/msm/+/b6410c3f75259c0ca2df45a01838d2e46272723e/drivers/clk/qcom/gdsc-regulator.c),
  SHA256 `1018185ce92e333ac7b94d1058f5411a73635a4270446ba6a38dea2524e6696b`.
- [Fixed Lenovo source mirror at 9d8abebf](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/clk/qcom/gdsc-regulator.c),
  SHA256 `e2f0e4269e1a81b2680deb15cc2c3b11ed55611831640b0432d3772293d777e7`.
  This mirror is not asserted to be an exact verified source release for the
  running production kernel. Both sources agree on the register fields used
  by this model.

The driver reads the control register, clears hardware-control and
software-override bits at probe, and sets the requested clock-disable wait
field. The captured CX node requests wait value 8. Enabling clears software
collapse and polls power-on; disabling sets collapse. When `hw-ctrl-addr`
exists, the polling reads its power-on bit instead. The driver's comments
explicitly account for a separate TZ/HYP vote keeping a domain powered while
the normal-world software collapse bit remains set.

The source does not provide a complete hardware state-machine specification,
power-on reset values, supply behavior, or the precise relation between an
internal hardware trigger and external votes. Those are not measured by this
implementation. The level-input combination below is a reference contract
that permits exercising the software-visible paths without inventing firmware
responses.

## Board mapping and opt-in profiles

The board uses `gdsc-profile=none|reference-off|reference-on`. The default
`none` leaves these GPU register resources unmapped. Both QOM devices remain
present with `enabled-reference=false` so migration can check the absence of
a reference profile in both directions. Choosing either reference profile
sets `enabled-reference=true` and the corresponding `reference-on` boolean.
These settings are fixed before realization. An inactive device rejects
register accesses even if an external board were to map its windows.

| QOM child | MMIO index | Physical address | Purpose |
| --- | --- | --- | --- |
| `/machine/gpu-cx-gdsc` | 0 | `0x03d9106c` | CX GDSCR |
| `/machine/gpu-cx-gdsc` | 1 | `0x03d91540` | CX hardware power status |
| `/machine/gpu-gx-gdsc` | 0 | `0x03d9100c` | GX GDSCR |
| `/machine/gpu-gx-gdsc` | 2 | `0x03d91508` | GX domain clamp/reset |
| `/machine/gpu-gx-gdsc` | 3 | `0x03d91008` | GX software block reset |

The four MMIO indices are separate four-byte regions. The board maps only
the resources captured for each domain. There is no whole-GPUCC or syscon
catch-all mapping, and no mapping for GDSCR CFG/CFG2 debug words whose
semantics have not been implemented.

Existing GCC GDSCs remain owned by `qcom-sm8250-gcc`: their registers, clock
dependencies and timed state transitions are already part of that model.
The GPU windows above are outside the GCC aperture and do not replace or
overlap those implementations. Camera, display, NPU and video GDSCs also appear
in the captured DT but are not connected by this change.

## Register protocol

### GDSCR, MMIO 0

| Field | Bits | Access and behavior |
| --- | --- | --- |
| `SW_COLLAPSE` | 0 | Read/write; 1 withdraws the local software power request |
| `HW_CONTROL` | 1 | Read/write; selects the explicit hardware-request input |
| `SW_OVERRIDE` | 2 | Unsupported manual sequencing; writes setting it fail |
| `RETAIN_FF_ENABLE` | 11 | Read/write digital retention request, with no physical retention model |
| `CLK_DIS_WAIT` | 15:12 | Read/write requested wait encoding; no physical timing claim |
| `PWR_ON` | 31 | Read-only derived digital power status |

The writable mask is `0x0000f803`. A normal read/modify/write may include bit
31; writes accept that bit and ignore its value. Neither setting nor clearing
the written status bit can override the derived state. Any write containing
other unsupported bits, including bit 2, fails without modifying supported
fields. Reads return only the supported control bits and derived status.

### Hardware status, MMIO 1

Reads expose derived `PWR_ON` at bit 31, identical to the status in GDSCR.
All writes fail. The driver uses this word for CX polling. No hardware control
fields beyond the observed read-only power-status interface are claimed.

### Domain control and software reset, MMIO 2 and 3

Domain control supports `GMEM_CLAMP_IO` at bit 0 and `GMEM_RESET` at bit 4.
Software reset supports `BCR_BLK_ARES` at bit 0. Other written bits fail the
complete transaction. The model retains the requested values and exposes
digital clamp/reset outputs. These requests do not independently create or
remove a power vote: isolation, reset and power state are distinct signals.

The implementation accepts aligned 32-bit register accesses. Subword or
nonzero-offset operations reaching the device are diagnosed and rejected.
QEMU can split an access at a four-byte region boundary, so a wider bus
transaction crossing a mapped/unmapped boundary is not promised to be atomic.

## Digital requests and outputs

Each domain has two one-bit named input groups:

- `hw-enable`: an explicit external hardware power request.
- `external-vote`: an independent external request that may keep the domain
  powered even when local software sets collapse.

Both inputs start low. Guest register writes never raise them automatically.
The state function is:

```text
selected_request = HW_CONTROL ? hw_enable : !SW_COLLAPSE
power_on = external_vote || selected_request
```

Changing a supported control bit or input updates the digital status
synchronously. This is not an estimate of a physical transition duration.
The `CLK_DIS_WAIT` field is retained for driver readback without claiming
that its value establishes measured timing.

Named outputs are `power-on`, `clamp-io`, and `reset-asserted`:

- `power-on` carries the state function above.
- `clamp-io` carries the domain-control clamp request.
- `reset-asserted` is the OR of the domain reset and software block reset.

These names are also read-only boolean QOM properties. The properties
`hw-enable-state` and `external-vote-state` expose the current input levels.
The read-only `state-known` property reflects `enabled-reference`: it is false
for the default inactive profile. Inactive devices drive all outputs low; that
absence of a signal must not be interpreted as a measured power-off state.
The outputs describe the local digital model; they are not evidence that a
GPU can run, that an upstream regulator is live, or that firmware accepted a
request. Supply sequencing and integration with actual parent regulators,
GPUCC, GMU and retained GPU memories remain outside this model.

## Reset and migration

`reference-on=false` resets GDSCR to `SW_COLLAPSE=1`, domain control to
`GMEM_CLAMP_IO=1`, and software reset to zero. `reference-on=true` resets all
three control words to zero. Input levels belong to the external source and
are preserved across protocol reset. Thus an asserted external vote can
continue to hold the digital power status high after a reference-off reset.
These are declared reference reset states, not values captured from silicon.

VMState version 1 serializes all three control words and both input levels.
Power, clamp and reset outputs are derived again after load. The source's
enabled-reference and reference-on configurations are serialized separately
and checked against the destination configuration. Migration rejects either
mismatch rather than silently changing what a subsequent reset would mean or
introducing reference registers absent from the source. Reserved control bits
in an incoming migration stream are rejected.

This protocol can be reused for another GDSC only after its register resources
and required fields are established. It should not be used to cover a broader
clock-controller aperture or to assume unobserved hardware state.

## Validation boundaries

The dedicated tests exercise reference profiles, software collapse, ignored
read-only writes, preserved retention/wait fields, explicit hardware and
external requests, domain independence, unsupported accesses, reset and
process-to-process migration. GPIO assertions check the model's output signal
as well as register/QOM readback. Configuration-mismatch migration is expected
to fail with a diagnostic.

Test source alone is not a runtime pass. A compiled-binary hash and successful
receipts must accompany any validation claim. A production-kernel run that
passes this probe still does not establish complete native-DT boot, Android
userspace, working graphics, or whole-device hardware equivalence.
