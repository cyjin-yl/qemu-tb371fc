# Explicit RPMh WAIT reserved-bit reference compatibility

`qcom-sm8250-rpmh.reference-wait-mask-reserved` is a **default-off reference
compatibility policy** for one confirmed vendor-kernel programming path. It
does not establish that SM8250 silicon ignores these bits. The existing
[RPMh resource service](rpmh-sm8250.md), command database, supported command
encodings and completion rules remain the underlying contract.

Enable the policy explicitly, together with an already configured RPMh
device:

```text
-global qcom-sm8250-rpmh.reference-wait-mask-reserved=on
```

The boolean is a device configuration property and cannot be changed after
realization. It does not enable an otherwise disabled board RPMh profile.
There is no new default firmware handoff, resource, command or completion.

## Why this option exists

The production Image used for the original-DT diagnostic has SHA256
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
The source-`3b31e83dc412962826ada872e361a1d32ea4e463` native run used QEMU
SHA256 `e0e1a669fefee1b20e8421dc6943faabee7fccb91c35f72ae2cd2f45513d23f2`.
Its QEMU device diagnostic records:

```text
qcom-sm8250-rpmh: DRV2 unsupported write @0x0d10 value=0x778bd800
```

The guest fault is in `__tcs_buffer_write+0x220/0x268`, called from
`msm_bus_dev_init_qos+0x128/0x620`. The recorded instruction is
`STR W20,[X8]`, with `W20=0x778bd800`. DRV2 offset `0xd10` is TCS0
`WAIT_FOR_CMPL`; it is not a command address or data slot. With the existing
board mapping, its derived physical address is `0x18220d10`. The physical
address is a mapping calculation, not a separate `invalid_mem` log entry:
the device callback itself rejected this write.

The bounded production-binary review covers the complete five relevant
functions, checks every disassembled machine word against the captured
bytes, and verifies the fault instruction against the actual exception.
It establishes this path:

1. `msm_bus_dev_init_qos` reserves a stack command and stores only its
   `addr` and `data` words at `sp+8` and `sp+12`. It does not initialize the
   third `wait` word at `sp+16` before the observed call.
2. `rpmh_write` copies `12 * command_count` bytes, including that third
   word, into the request. Zeroing its destination before the copy does not
   initialize the copied source word.
3. `__tcs_buffer_write` loads the old WAIT value, loads the command's third
   word, shifts it by the command index, ORs it into WAIT, and writes the
   resulting word. The failing write is the matched production instruction.

The corresponding fixed official source is
OnePlusOSS/android_kernel_oneplus_sm8250 at
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`:

| Source | Relevant contract | SHA256 |
|---|---|---|
| [include/soc/qcom/tcs.h](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/include/soc/qcom/tcs.h) | Three `u32` command words: address, data, wait | `40cd0160946b157f16641a1f17cb925cb6460107cd5510a1c0a32002e2fa6d35` |
| [drivers/soc/qcom/rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/rpmh.c) | Copies the whole command array | `51f8ba7548362b51586a749f8c920b343abeb778138a77a046dafaac5b458a68` |
| [drivers/soc/qcom/rpmh-rsc.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/rpmh-rsc.c) | WAIT offset `0x10`, read/OR/write sequence, TCS and command strides | `bc0641e355309055228dd1978c925ba7fe772d1fa80bd28d892e916f990fd018` |
| [drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c) | `tcs_cmd_gen` writes address/data; `bcm_commit_single_req` passes an otherwise uninitialized command | `14563f3f965b030d68917b48b6185c2ff2b87f4f9de2ab212f2d8cb77b510bb7` |

The detailed
[source and production-binary review](https://api.github.com/repos/cyjin-yl/qemu-tb371fc/git/blobs/9512c590d8f49929ad248f2cce070c049644fdba)
also records the bounded disassembly identities, actual kernel configuration,
earlier captured RSC properties and their provenance limits. The review
does not equate the complete production Image with this public source tree.
It does not establish the origin or meaning of the stale stack value, nor
why a previous run happened to pass this programming point.

Neither this fixed vendor source nor the reviewed upstream driver gives an
applicable hardware definition making WAIT bits above the implemented
command count write-ignored/read-as-zero. The reviewed Qualcomm QBox model
describes a different SoC and is not such a specification. The new behavior
therefore remains an explicit compatibility choice, rather than a claimed
correction to measured hardware semantics. No production Image bytes or
guest stack contents are changed.

## Exact write behavior

The model implements sixteen command slots per ordinary TCS, so the
implemented WAIT mask is `0x0000ffff`. This command count is an existing
reference-model capability, not a new sample of a physical NCPT register.

| Access | Strict/default policy | Explicit reference policy |
|---|---|---|
| Idle WAIT write, high sixteen bits zero | Store the complete low-sixteen-bit mask | Same |
| Idle WAIT write, any high bit set | Reject the write, preserve WAIT | Store only `raw & 0xffff`; record the accepted masking event |
| Running-TCS WAIT write | Reject, preserve WAIT and diagnostics | Same; the busy guard is evaluated before masking |
| CMD_ENABLE with a high bit set | Reject | Same |
| Narrow, misaligned, unsupported-address or other invalid writes | Existing access contract | Same |

WAIT reads return the stored low-sixteen-bit value. A masked write does not
start a TCS, execute a command, publish a resource vote or synthesize an IRQ.
A later normal trigger still uses the existing enabled-command set, virtual
timer and typed-resource validation. Unsupported resource commands still
remain incomplete. The option applies to the already implemented APPS and
display voters, using each voter's existing TCS geometry; it does not add a
new voter or register window.

Every accepted write that actually discards high bits increments a saturating
64-bit counter and updates the last voter, TCS and raw word. Low-only writes
do not increment it or overwrite this history. Rejected writes, including a
busy high-bit write, do neither. When `LOG_GUEST_ERROR` is enabled, each
accepted masking event records:

```text
qcom-sm8250-rpmh: DRV2 TCS0 reference WAIT mask raw=0x778bd800 effective=0xd800 ignored=0x778b0000
```

This is an example of the format and arithmetic for the observed fault word,
not a claim that a later native run has already accepted it. It is deliberately
distinct from the existing `unsupported write` error.

## Stopped-state observation

The RPMh QOM device exposes these read-only properties:

| Property | Type | Meaning |
|---|---|---|
| `wait-command-mask` | `uint32` | `0x0000ffff` |
| `wait-reserved-masked-writes` | `uint64` | Saturating count of accepted high-bit WAIT writes |
| `wait-reserved-last-voter` | `uint32` | Last affected voter: APPS 0..2 or display 3 |
| `wait-reserved-last-tcs` | `uint32` | Last affected valid TCS within that voter |
| `wait-reserved-last-raw` | `uint32` | Last accepted word before masking |
| `wait-reserved-last-effective` | `uint32` | Last raw word AND `0x0000ffff` |
| `wait-reserved-last-ignored` | `uint32` | Last raw word AND `0xffff0000` |

These getters only read recorded state. They do not read guest MMIO, advance
virtual time, complete a command or alter any state. When the count is zero,
all last-event properties are zero; the mask remains `0xffff`. The boolean
configuration property is readable separately.

A positive counter establishes that the selected model policy accepted at
least one such write since reset. It does not establish command completion,
physical hardware behavior, driver probe success, EL0 execution or Android
boot. Native acceptance must separately inspect the current first fatal,
normal TCS/resource behavior and the relevant boot evidence. The earlier
native run failed in RPMh before CPUFreq/devfreq registration and before any
SBM command; its cold SBM observation is not a native SBM success result.

## Reset and migration

System reset clears the counter and last-event fields. It preserves the
configured policy and follows the existing RPMh reset rules for TCS state,
votes, interrupts and the explicitly configured XO boot vote. This option
does not add any new reset handoff value.

The main `qcom-sm8250-rpmh` migration description stays at version 2 with
minimum version 2. Its serialized field list is unchanged. The option adds
an optional subsection named
`qcom-sm8250-rpmh/wait-reserved-reference`, version 1/minimum 1:

| Byte offset | Width | Big-endian field |
|---|---|---|
| 0 | 4 | Policy identity, exactly `1` |
| 4 | 8 | Masked-write count |
| 12 | 4 | Last voter |
| 16 | 4 | Last TCS |
| 20 | 4 | Last raw word |

The payload is 24 bytes, excluding QEMU subsection framing. The subsection
is emitted whenever the policy is enabled, including a cold state with no
masked writes. A strict source omits it. It carries no new timer, absolute
deadline or resource state: those remain in the existing main payload.

Before loading, the device disables its readiness callback, removes its
timer and clears incoming subsection markers and diagnostic state. The
subsection rejects duplicate occurrences and any policy other than `1`.
The parent validates profile identity and diagnostics before restoring any
outputs or scheduling existing TCS work:

- Strict to strict accepts the absent subsection.
- Enabled to enabled requires the subsection and restores its diagnostics.
- Strict to enabled and enabled to strict are rejected in both directions.
- A zero count requires all three stored last-event fields to be zero.
- A nonzero count requires a high bit in the last raw word, a valid voter
  and a valid TCS for that voter; display has only its existing two TCSs.

Only after these checks does the existing main-payload validation and
restoration run. A malformed subsection cannot make the policy permissive
or publish partially loaded command results. The strict payload remains
structurally compatible with the pre-option main version 2 format; this
statement does not promise arbitrary cross-version QEMU migration or replace
actual migration tests. Same-binary profile acceptance/rejection and pending
TCS restoration are the intended validation boundary.

## Validation status

The source and production-binary diagnosis is recorded independently of the
model implementation. A static source review alone is not an executed MMIO,
migration or native result. The dedicated tests must retain the old strict
RPMh gates, exercise masked WAIT separately from CMD_ENABLE and busy writes,
verify the optional subsection from actual saved streams, and require real
destination rejection for mismatched profiles and corrupted fields. Native
evidence must use the exact CI-produced binary and preserve the distinction
between accepted compatibility writes and downstream boot progress.
