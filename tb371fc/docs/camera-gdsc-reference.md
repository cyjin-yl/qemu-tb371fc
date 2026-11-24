# Camera GDSC digital reference

`qcom-camera-gdsc` adds the six four-byte camera power-control registers declared
by the original TB371FC device tree. The device is always present at
`/machine/camera-gdsc`, but its MMIO protocol is disabled by default. Enable the
bounded reference explicitly with:

```text
-global qcom-camera-gdsc.enabled-reference=on
```

This option does not affect `qcom-gdsc` or `qcom-gpu-cx-gdsc`. The new type derives
directly from `SysBusDevice`; it is not a subclass of the existing GPU device.
Existing GPU and GCC register ownership and migration formats are preserved by
this camera addition.

The [source contract](camera-gdsc-source-contract.md) binds the register semantics
to the fixed vendor driver and the geometry and regulator relationships to the
actual DT captures. The reference does not model camera image processing,
firmware, PMIC voltage, memory retention, a hardware transition delay, or an
access clock. A returned digital power bit is not evidence of those functions.

## Exact resources

Each row is a separate four-byte little-endian register window. The intervening
address ranges remain unassigned by this device.

| Domain index and name | GDSCR address | DT flags | Driver poll timeout |
| --- | --- | --- | --- |
| 0, `bps` | `0x0ad07004` | retain-regs; HW mode declared | 100 us, driver default |
| 1, `ipe` | `0x0ad08004` | retain-regs; HW mode declared | 100 us, driver default |
| 2, `sbi` | `0x0ad09004` | retain-regs | 100 us, driver default |
| 3, `ife0` | `0x0ad0a004` | retain-regs | 100 us, driver default |
| 4, `ife1` | `0x0ad0b004` | retain-regs | 100 us, driver default |
| 5, `titan-top` | `0x0ad0c144` | retain-regs | 500 us, DT property |

All six are siblings supplied by the same `mmcx.lvl` regulator. The name
`titan-top` does not make that GDSC a supply parent for the other five domains.
The DT contains no `domain-addr`, `sw-reset`, or `hw-ctrl-addr` for these domains.
No such auxiliary windows are added. NPU, VIDEO, and MDSS GDSCs are outside this
increment, as are the ten DT GDSCs already owned by the existing GCC model.
The later [MDSS increment](mdss-gdsc-reference.md) adds its own separate
four-byte owner and shares mmcx through a stateless splitter; the six-camera
device and its migration payload remain unchanged.

The six GDSCs refer to vendor binding `GCC_CAMERA_AHB_CLK = 11`. The current QEMU
GCC descriptor with that name has internal array index 10 and an unknown external
parent. This device neither invents a clock rate nor equates the DT clock binding
number with the QEMU descriptor index. Access-clock gating is not modeled here.
GCC CBCR retention bits are separate from the GDSCR retain bit.

## Register protocol

Only aligned 32-bit transactions are supported. Reads return the stored control
word with the derived `POWER_ON` bit if that feedback is known. Disabled-reference
accesses and unknown power-feedback reads return `MEMTX_ERROR` and a diagnostic.

| Bits | Protocol |
| --- | --- |
| 0, `SW_COLLAPSE` | Stored software collapse request; zero requests power in software mode |
| 1, `HW_CONTROL` | Select the external hardware-request input in the declared reference HW modes |
| 11, `RETAIN_FF_ENABLE` | Stored configuration only; no RAM or flip-flop retention execution |
| 15:12, `CLK_DIS_WAIT` | Stored configuration only; no timing model is derived from this field |
| 31, `POWER_ON` | Read-only digital availability derived from valid request and parent inputs |

The stored write mask is `0x0000f803`. A normal driver read/modify/write may carry
bit 31 back into a write; it is ignored rather than stored. `SW_OVERRIDE` bit 2
and all other unsupported bits reject the complete four-byte write. Rejected
writes leave every domain and external input unchanged.

The DT permits regulator FAST mode only for BPS and IPE. This reference accepts
`HW_CONTROL=1` for those two domains and rejects it for the other four. That is
a bound on this reference's declared protocol, not a claim that other silicon
domains physically lack that bit. Clearing it is valid for every domain.

Atomic rejection refers to a single four-byte device transaction. QEMU may split
a wider memory request into multiple transactions; a later access to a hole does
not roll back an earlier valid transaction.

## Request and parent state

The shared GPIO inputs `parent-known` and `parent-on` carry existing RPMh digital
availability. Board integration connects the single RPMh `power[3]` output once
to the aggregate camera device and initializes it from `res.mmcx.lvl.enabled`.
`res.mmcx.lvl.present` establishes whether that upstream resource is modeled. An
absent RPMh model or absent resource leaves parent availability unknown.

The fixed DT supplement independently identifies `qcom,resource-name = mmcx.lvl`.
Index 3, command-db address `0x3e030`, and the synthetic ARC level table belong to
the existing explicit RPMh reference; they are not measured tablet command-db
data. The regulator leaf's minimum level 64 is not an added hardware enable
threshold. The existing RPMh resource's effective nonzero vote determines its
digital availability. Its software MX/CX supply relationships are preserved in
the evidence without fabricating analog rail dependencies or PMIC behavior.

Each domain has `hw-request-known` and `hw-request` GPIO inputs. They initially
remain unknown because no camera hardware requester is implemented. Software
mode derives a known request from `!SW_COLLAPSE`; HW mode derives it only from a
known hardware input. The reference assumes no additional hidden TZ/HYP votes.

The digital result follows these rules:

- A known-off parent establishes known-off power even if the selected request
  is unknown.
- A known-off request establishes known-off power even if the parent is unknown.
- A known-on parent and a known-on request establish known-on power.
- Other combinations retain unknown feedback and reject GDSCR status reads.

The output arrays `power` and `state-known` each have six entries. A false `power`
output with false `state-known` is an unknown placeholder, not established off.
Similarly, the `requested` QOM value is false when `request-known` is false.

This protocol is synchronous and digital. It does not introduce a timer or
pretend the driver's 1 us polling delay or 100/500 us timeout measures a physical
transition. Parent/input changes update feedback; reading a register or a QOM
property never advances or repairs a state machine.

## Reset and observation

Enabling the reference selects a documented cold assumption: all six control
words start at `SW_COLLAPSE=1`. This is not a firmware handoff dump. A system
reset restores those six words and preserves the externally provided parent and
hardware-request inputs. Reset's enter phase does not propagate outputs; its
hold phase republishes the derived feedback.

The only configurable device property is `enabled-reference` before realization.
All following properties are read-only, including in normal TCG operation:

| Global properties | QOM type |
| --- | --- |
| `enabled-profile`, `reference-identity`, `domain-count` | `uint64` |
| `register-bytes`, `mapped-bytes`, `parent-resource-index` | `uint64` |
| `cold-control`, `control-write-mask`, `power-status-mask` | `uint64` |
| `parent-known`, `parent-on` | `bool` |

For `N = 0..5`, the domain properties are:

| `domainN.` suffix | QOM type |
| --- | --- |
| `name` | `str` |
| `address`, `dt-flags`, `timeout-us`, `control` | `uint64` |
| `request-known`, `requested`, `state-known`, `powered` | `bool` |
| `hw-request-known`, `hw-request` | `bool` |

The profile boolean plus these 77 read-only properties form a 78-get snapshot.
Getters do not mutate state, drive outputs, commit a request, or synthesize an
acknowledgment. They can distinguish declared configuration, external inputs,
and derived digital feedback, but cannot prove a Linux probe or camera workload
completed.

## Migration

The independent `qcom-camera-gdsc` VMState has version 1 and minimum version 1.
Its payload is exactly 194 bytes, with multibyte values in VMState wire order.

| Offset | Fields | Bytes |
| --- | --- | --- |
| 0 | Nine immutable u32 values listed below | 36 |
| 36 | `parent_known`, `parent_on`, one byte each | 2 |
| `38 + 26*N` | Domain tuple and runtime for domain N | 26 each |

The nine immutable values, in order, are `enabled_profile`,
`reference_identity=0x43474431` (`CGD1`), `domain_count=6`, `register_bytes=4`,
`mapped_bytes=24`, `parent_resource_index=3`, `cold_control=1`,
`control_write_mask=0xf803`, and `power_status_mask=0x80000000`.

Each domain's 26-byte block contains immutable `domain_id` (u32), `base` (u64),
`dt_flags` (u32), and `timeout_us` (u32), followed by mutable `control` (u32),
`hw_request_known` (one byte), and `hw_request` (one byte). DT flag bit 0 means
retain-regs is declared; bit 1 means HW mode is declared. The flags are
`3,3,1,1,1,1`; the timeouts are `100,100,100,100,100,500`.

Every immutable value is compared with the destination before it can replace
that identity. Both profile mismatch directions reject, as do domain swaps,
unsupported control bits, a HW mode outside the declared reference, or a
non-cold control word in a disabled-profile stream. Incoming preparation disables
publication and clears the feedback outputs. All six domain controls are
validated before post-load enables publication and restores outputs; no first
MMIO read is needed to restore them. A failed stream does not publish success.

No timer, clock, or absolute epoch exists in this model. Its addition does not
change the existing GPU or RPMh migration payloads. Compatibility with historic
binaries lacking the new device is not claimed. Component validation must use
actual same-version streams and explicit mismatch/corruption cases rather than
infer compatibility from a source-level structure size.

## Validation status

This document describes the reference protocol and its source/DT scope. The
model, tests, and native image must still be tied to a specific formal CI binary
before reporting a successful runtime result. Creation of a source commit,
static review, or a known control word does not establish whole-board, camera,
Android, or user-space execution.
