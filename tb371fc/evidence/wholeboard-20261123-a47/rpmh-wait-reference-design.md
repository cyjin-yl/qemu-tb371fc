# RPMh WAIT reserved-bit reference policy

Design against source `3b31e83dc412962826ada872e361a1d32ea4e463`.
This document is a contract for an explicit reference extension. It is not an
implementation or a passing test receipt.

## Evidence and boundary

The native 76 run wrote `0x778bd800` to DRV2 `0xd10`, TCS0's
WAIT_FOR_CMPL register. The mapped aligned 32-bit access reaches the RPMh callback.
The current model rejects a running TCS first, and otherwise rejects high bits.
The fixed vendor source leaves a single BCM command's u32 wait member
uninitialized; the exact production Image disassembly now corroborates that
construction and the raw shift/OR into the faulting WAIT write. The provenance of
the stack contents is still unknown. No applicable silicon evidence establishes
whether bits above the implemented command slots are ignored, retained, or cause
an error.

The five RPMh model/header/data/test files are byte-identical in 3b31 and b95.
The existing default strict behavior is a model policy, not a measured silicon
claim. New reference behavior must never be enabled implicitly.

## One new opt-in property

Add immutable bool `reference-wait-mask-reserved`, default false, to
`qcom-sm8250-rpmh`. There is no existing `enabled-reference` property on this
device: the board's `rpmh-profile` selects off/reference/file, and the device
requires exactly one of a synthetic command database or an external database.
The new property is independent of that database choice.

Both modeled APPS and display geometries expose 16 command slots through
`(DRV_PRNT_CHLD_CONFIG >> 27) & 0x1f`. The effective mask is fixed `0xffff`.
Do not change the capability value to advertise 32 commands.

Only an idle, valid WAIT_FOR_CMPL write is affected. Busy/stalled rejection occurs
first. With the property on, store `raw & 0xffff` and record a compatibility
observation if any high bits were removed. With the property off, retain the
existing rejection. CMD_ENABLE, IRQ registers, command data validation, all
unknown MMIO and typed resource checks remain strict. A WAIT write itself cannot
trigger a TCS, produce completion, change an IRQ, apply a vote or move a deadline.
Low wait bits are preserved exactly, not converted to one Boolean.

## Auditable state

Expose seven pure read-only QOM properties: `wait-command-mask`,
`wait-reserved-masked-writes`, and
`wait-reserved-last-{voter,tcs,raw,effective,ignored}`.
The counter saturates at UINT64_MAX. Count only accepted idle high-bit WAIT
writes. Derived effective/ignored values come from the last raw value.
A rejected busy write changes neither state nor these diagnostics.
These are model observations, not hardware registers or firmware execution proof.

Reset preserves the immutable property and clears the new diagnostics.
The existing TCS/resource reset behavior remains unchanged. With count zero,
all last fields are zero. With a positive count, the last raw value must have
high bits and its voter/TCS must exist.

## Preserve v2 strict migration

Keep the existing `qcom-sm8250-rpmh` main version 2/minimum 2 and every field
unchanged. Retain the existing incoming low-16-bit WAIT validation.
Add optional subsection `qcom-sm8250-rpmh/wait-reserved-reference`, version 1:

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 4 | Policy identity, exactly 1 |
| 4 | 8 | Saturating masked-write count |
| 12 | 4 | Last voter |
| 16 | 4 | Last TCS |
| 20 | 4 | Last raw WAIT value |

Payload is 24 bytes, big-endian `>IQIII`. Emit it whenever the configured
property is on, including count zero. A count-based `needed` predicate is wrong:
it would omit the identity of an enabled but unused policy.

Parent pre_load clears an incoming-only policy marker, a nonserialized
subsection-seen flag, and the new diagnostic fields. The subsection rejects a
duplicate occurrence and requires policy 1 plus valid diagnostics. Parent
post_load requires the incoming policy and subsection presence to match the
configured immutable property before its existing outputs/timer restoration.
It never overwrites the configured property.

This rejects strict-to-masked even on a prewarmed target, and rejects
masked-to-strict. Masked-to-masked restores effective WAIT values in the old main
payload and audit history in the subsection. The old absolute command-deadline
contract remains; the QTest roundtrip aligns target virtual time as existing RPMh
tests do.

The pinned loader calls parent pre_load before fields, then loads subsections,
then parent post_load. It does not consult a subsection's `needed` on load.
An old binary fails an unrecognized matching-prefix subsection. That is a
source-code conclusion, not a claim of an executed old-binary transfer.

A direct v3/min3 format could also distinguish policy, but would reject every
old strict stream and unnecessarily force unchanged strict tests to change
their version interpretation. The optional subsection is the recommended
narrow extension.

## Targeted formal gate and native declaration

The independent `rpmh-wait-mask-reference` gate is planned for 17 checks:
10 isolated MMIO/state cases, one paused TCG QOM case, two checks within one
completed masked roundtrip, two real profile rejections and two single-bit
corruption rejections. The old APPS 22+5 and display 11+6 files stay byte-identical.
Cold masked state with a zero counter supplies the masked-to-strict source, so
the gate verifies that an unused enabled policy still emits its subsection.
Strict-to-masked uses a prewarmed masked target to catch stale incoming identity.

Formal CI is the only QEMU execution path. No local or remote development build
or QTest run is authorized. Static AST/compile checks are allowed.

An approved native invocation would retain the original 17 reference globals
and append exactly one eighteenth pair:

`-global qcom-sm8250-rpmh.reference-wait-mask-reserved=on`

The actual-DT consumer must require that choice explicitly in its configuration.
A paused pure-QOM observer can record policy, mask, count and last values,
surrounded by running=false status checks. It must label them reference-policy
usage, preserve original Image/DTB hashes, and independently report whether the
guest progressed. It cannot turn the earlier native 76 panic into a success.

The accompanying JSON contains source/blob identities, raw receipt hashes,
precise field names, reset/migration invariants and the proposed report counts.
