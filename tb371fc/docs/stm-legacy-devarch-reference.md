# Explicit legacy STM DEVARCH compatibility

`-machine tb371fc,stm-legacy-devarch-reference=on` selects one virtual
compatibility behavior: an aligned 32-bit read of the STM control aperture at
offset `0xFBC` returns `0x00000000` without an access error. The default is off.
The original control aperture remains `0x06002000` through `0x06002fff`.

This value is a deterministic choice for an older reserved location. It is not
a measured TB371FC register, an architectural read-as-zero requirement, a newer
STM architecture identifier, or a new trace capability.

## Why this is a separate choice

Linux 6.12.114's [`amba_read_periphid()`][linux-bus] reads PIDR and CIDR first.
After finding CoreSight CID `0xb105900d`, it reads `DEVARCH` at `0xFBC`, then
`DEVTYPE` at `0xFCC`; the offsets are defined in [the AMBA header][linux-header].

The Linux research board18 run with Image SHA-256
`798a037ecf721862c35aa4dac679a787330e7d92a010ef1827f59d199fddc0a1`
stopped at `amba_read_periphid+0x264`. The retained instruction is
`0xb94001ce`, `ldr w14, [x14]`, with `x14=0xffff8000828fdfbc` and the
mapping base `x0=0xffff8000828fd000`. Its actual DT identifies the STM control
window at `0x06002000` with size `0x1000`. These facts infer a read at
`0x06002fbc`; no physical/MMIO bus trace was captured. This is a generic AMBA
match invoked during another driver's registration, not proof that the STM
driver itself completed probe.

The existing reference represents the older part `0x962`. [ARM IHI0054A][ihi],
section 2.1 and table 2-1, lists `0xFBC–0xFC4` as reserved configuration locations
with UNK/SBZP semantics. [ARM DDI0444A][ddi], section 3.1, says software must
avoid reserved or unused locations and that accessing them can have
Unpredictable behavior. Neither document guarantees a zero read there. The
option deliberately names a virtual compatibility policy instead of changing
those architectural or physical claims.

## Exact access boundary

| Access | Option off | Option on |
| --- | --- | --- |
| Aligned `read32` at `0xFBC` | Existing access error | Explicit reference value zero |
| Any write at `0xFBC`, including zero | Access error | Access error |
| `read8`, `read16`, `read64`, or unaligned access at `0xFBC` | Access error | Access error |
| Accesses at `0xFC0` and `0xFC4` | Access error | Access error |
| Existing implemented STM offsets | Existing behavior | Same behavior |

The FBC write rejection precedes software-lock write suppression. Locking the
device does not turn these writes into successful ignored operations. Wider APB
accesses remain whole transactions; the model does not split an invalid 64-bit
access into successful identity reads.

PID `0x000bb962`, CID `0xb105900d`, DEVTYPE `0x63`, AUTHSTATUS `0xaa`, feature
values, control masks, stimulus aperture, reset state and authorization remain
unchanged. The option adds no ATB output, trace generation, DMA, interrupt, or
authentication input.

## Configuration and migration

The machine boolean is frozen after machine creation. The STM device's
`legacy-devarch-reference` boolean is frozen after realization. Both are
available through typed QMP `qom-get` on `/machine` and `/machine/stm`.
They are configuration, not mutable guest state. System reset clears the
existing control state while keeping the selected configuration.

The default retains the existing migration profile `0x53544d01`; the explicit
option selects `0x53544d02`. The existing `VMSTATE_UINT32_EQUAL(profile, ...)`
field rejects either mixed-profile direction. No field order, stream version,
or existing post-load check changes. The separate test uses an ordinary stream
for same-profile continuation and two unmodified streams for the mixed-profile
failures. This is not a claim of untested cross-version migration support.

## Independent verification

`tb371fc/tests/test_stm_legacy_devarch.py` is a separate public gate:

- Ten named checks, 26 actual model rejection diagnostics and four rejected
  attempts to mutate realized QOM properties.
- The actual Linux PIDR/CIDR/FBC/FCC read order in locked and unlocked states,
  with an unchanged control-state snapshot and no access-error diagnostic for
  the allowed FBC read. A numeric zero alone is insufficient evidence.
- Default rejection, exact read width/alignment, writes in both lock states,
  neighboring reserved locations, reset and continued claim operations.
- Five owned QEMU children, one ordinary successful migration and two expected
  profile-mismatch failures. Every created child must be actually waited and
  reaped; forced cleanup fails acceptance.
- Original streams and source dependencies are rehashed. No migration payload
  is edited, and no guest CPU is run by this component fixture.

The existing STM gate's 12 checks and 41 rejection cases, the original 116 core
checks, and all other additive gates remain independent and unchanged. A
component pass does not establish Linux userspace, a later STM probe result,
physical tablet identity or native device readiness. The existing Linux LLCC
`-110` and rejected unsupported SPMI transactions remain separate issues.

[linux-bus]: https://github.com/gregkh/linux/blob/v6.12.114/drivers/amba/bus.c
[linux-header]: https://github.com/gregkh/linux/blob/v6.12.114/include/linux/amba/bus.h
[ihi]: https://documentation-service.arm.com/static/5f8ffb02f86e16515cdbfec0?token=
[ddi]: https://documentation-service.arm.com/static/5f10692b0daa596235e7f8cb
