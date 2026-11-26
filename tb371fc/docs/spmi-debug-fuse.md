# SM8250 SPMI debug availability fuse

## Scope and unresolved identity

This device models an explicitly supplied immutable word, with one read-only
MMIO operation. It is disabled by default. Root has explicitly selected
`virtual-spmi-debug-unavailable-v1` for a future reviewed diagnostic: word
`0x01000000`, with bit 24 describing this virtual platform's unavailable debug
controller and the other 31 bits declared model-reserved zeros.
**No physical fuse value was measured. Future artifact, qualification and native
execution bindings remain UNBOUND.** The configuration decision is recorded in
`spmi-debug-fuse-reference-policy.json`; it does not automatically enable the
device. Artificial test fixtures remain separate from this policy and from
physical evidence.

The original guest's first fatal access in the ada1 diagnostic was a rejected
32-bit physical read at `0x007820a8`, from
`spmi_pmic_arb_debug_probe+0xb0/0x250`. The original native result remains a
kernel failure. The previous GCC `0x77004` write rejection was not observed;
UFS deferred with `vcc` error `-517`, so this does not prove execution or success
of the previous GDSC path.

Existing live DT metadata identifies the debug node as `status = "ok"`, with
`core = <0x06b0f000 0x60>`, `fuse = <0x007820a8 4>` and disable bit 24. Its
platform device exists but has no bound driver. The existing dmesg snapshot
contains only five lines and no relevant match. These facts do not establish
either the fuse word or the value of bit 24.

Pinned Qualcomm source:
`OnePlusOSS/android_kernel_oneplus_sm8250` commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`,
`drivers/spmi/spmi-pmic-arb-debug.c`, 9243 bytes,
SHA256 `7fefc5438d2da8bba2cd1cf3fc58fa7f8aa065613f4244f0d4a8151f7c01ae3c`,
Git blob `e23188d1de607a19f962af6bb301a9f5b7a40307`.
[Read the pinned vendor driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/spmi/spmi-pmic-arb-debug.c).
Its probe reads the fuse before allocating/registering the debug controller.
A set disable bit returns `-ENODEV`; a clear bit proceeds to controller and
clock setup. Returning zero is therefore not a neutral implementation.

The chosen source-defined field projection is a virtual availability policy.
No physical bit value was measured. Even evidence for bit 24 alone would not
establish a measured complete 32-bit physical word. This preparation selects
only the reviewed virtual configuration, with the explicit limits above.

The reviewed **virtual debug-unavailable** policy follows the existing
reference-device principle: bit 24 describes the absence of a debug controller
in this virtual platform, while the other 31 bits are declared model-reserved
zeros. The source-defined result is the driver's normal
`-ENODEV` branch, not a successful debug transaction. This is a platform
capability decision, never a reconstruction of the physical fuse. Choosing the
opposite availability requires separately addressing the missing debug core,
clock and transaction semantics. Root has selected the virtual policy and must
still review and bind the qualified artifact before native enablement. No native
execution is claimed.

## Configuration and decode

The independent QOM child is `/machine/spmi-debug-fuse`, type
`qcom-sm8250-spmi-debug-fuse`. The machine properties are:

* `spmi-debug-fuse-enabled`: boolean, default off.
* `spmi-debug-fuse-word`: mandatory when enabled, exactly `0x` followed by eight
  lowercase hexadecimal digits. No implicit zero, sign, whitespace, short form,
  or truncation is accepted. Supplying a word while disabled is also rejected.

The device properties are `enabled` and `fuse-word`; the read-only `bound-word`
getter reports `UNBOUND` while off and the complete canonical string while on.
Machine properties are fixed after machine creation; device configuration is
fixed after realization. No new native command or deployment is provided.

An enabled instance maps a 16-byte **software decode guard** at
`0x007820a0..0x007820af`. This is not a claim about the physical fuse-bank size.
The guard preserves 8-byte and unaligned transactions for strict rejection.
Only an aligned 4-byte read at offset 8 succeeds. Every write, other offset,
other width, and unaligned read returns `MEMTX_ERROR` and one device diagnostic.
Unknown offsets have no backing RAM. While off the guard is disabled, preserving
the previous unmapped address behavior. The debug controller at `0x06b0f000`
is not mapped, aliased, implemented, or completed by this device.

The ordinary SPMI five-window model, its 25 tests, its APID/ownership policy,
and its VMState are unchanged. Its protocol differs from the debug interface.
This fuse device has no IRQ, timer, clock, DMA, PMIC transaction or mutable
guest state.

## Reset and migration

Reset preserves the explicit word and enabled state. VMState version 1 consists
of two big-endian 32-bit equality guards: `profile = 0x53444600 | enabled`, then
the configured word. The off profile's internal zero is an unmapped serialization
sentinel, not a readable or selected fuse value. On/off mismatches and different
words are rejected; loading never replaces destination configuration. There are
no device mutable fields. This does not claim that a failed whole-machine
migration left guest RAM untouched or that older binaries know this new section.

## Validation status

The model and integration changes are preparation only. No full QEMU build,
compiled test, native execution, or physical MMIO read was performed here.
The separate compiled test candidate must retain real process wait/reap and
protocol cleanup checks; default/explicit off, admission failures, synthetic
word reads, strict rejection, reset, immutable QOM properties, complete migration
stream identity, same-profile loading and incompatible-profile rejection are
required. The existing ordinary SPMI checks remain required without relaxation.
