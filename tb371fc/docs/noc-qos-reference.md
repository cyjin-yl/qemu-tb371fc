# TB371FC NoC QoS configuration reference

`qcom-noc-qos` models the four source-defined configuration words
of one physical NoC QoS port. The TB371FC board instantiates 47 ports and
188 individual 4-byte windows from its original DT. This is configuration
state, not a NoC traffic simulator: it does not promise throughput, latency,
arbitration results, bandwidth enforcement, clock rates, power readiness or
SBM completion.

Every device is present in the default unknown profile. In that profile,
both MMIO reads and writes return transaction errors, and raw QOM register
queries return an error. Unknown reset state is not exposed as a successful
zero-valued hardware read. Enable the finite reference profile explicitly:

```text
-global qcom-noc-qos.enabled-reference=on
```

The enabled profile starts the four supported words at zero and restores
that state on system reset. This is an explicit reference initial condition,
not a measured silicon or bootloader reset value. The board does not seed
DT priorities or simulate completion of the driver's initialization.
Only accepted guest writes change a word.

## Fixed sources and actual fault

The primary register and operation source is official OnePlus
[msm_bus_noc_rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/msm_bus/msm_bus_noc_rpmh.c), pinned at
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`.

| Input | Fixed identity |
| --- | --- |
| msm_bus_noc_rpmh.c | SHA256 `9e408e18745857c575be792a92cb3eb7bcab1fb51b181cc389fd2b81213560a6` |
| [msm_bus_fabric_rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/msm_bus/msm_bus_fabric_rpmh.c) | Git blob `3a48c03a8be79fd419ffb5acae2aa65b700666de` |
| [msm_bus_of_rpmh.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/msm_bus/msm_bus_of_rpmh.c) | Git blob `ecd7898a5209718a314324962fafaa834eb3f82c` |
| [Makefile](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/soc/qcom/msm_bus/Makefile) | Git blob `0c8811247b19b612ab17045555f42993a4c10045` |

The Makefile selects the RPMh implementation when
`CONFIG_QCOM_BUS_CONFIG_RPMH` is set. This is established for the
actual original kernel, rather than inferred merely from a repeated
function name: its embedded configuration has
`CONFIG_QCOM_BUS_SCALING=y` and `CONFIG_QCOM_BUS_CONFIG_RPMH=y`.
The extracted IKCONFIG SHA256 is
`23d3db565be3cd6cfc07bb90541d971dc70e6a6ce3516360155b8fd15590e726`.

The original kernel successfully passed MPM timestamp reporting, then
faulted in msm_bus_noc_qos_init. With invalid-memory logging enabled,
the actual failure is a 32-bit unassigned read at physical
`0x016e9008`, followed by a CPU5 data abort, ESR `0x96000010`
and FAR `0xffffff800b2e9008`. This directly measured physical
address resolves to aggre1 QoS port 7 MAINCTL, the DT's mas-qhm-qspi
client. It is not a guessed base derived only from the platform device's
name.

The bounded original-DT evidence is identified by:

| Evidence | Identity |
| --- | --- |
| Original DTB SHA256 | `5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207` |
| Original kernel SHA256 | `ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f` |
| [Raw 219-node NoC subtree, configuration and fault evidence](../evidence/wholeboard-20261123-a47/noc-dt-evidence-0508.json) | Git blob `c300271fab1acabb6d1b45ddcf626f5e2f091d25` |
| [Decoded resource/fabric/client layout](../evidence/wholeboard-20261123-a47/noc-layout-0508.json) | Git blob `77b1851faf6063d4c1a88d11c524de1b239005e6` |

These evidence objects are in `cyjin-yl/qemu-tb371fc`.
Numeric DT fields are decoded as big-endian 32-bit cells; printable-string
heuristics are not used to interpret offsets, ports or phandles.

## Physical ownership

The vendor callback is passed
`qos_base, base_offset, qos_off, qos_freq`. Its helper argument
names differ from those DT properties. The actual formula is:

```text
physical_word = resource_base
              + DT qcom,base-offset
              + port * DT qcom,qos-off
              + word_offset
```

All six modeled groups have the source's NoC bus type. Their observed
physical ports are:

| QOM fabric name | Resource base | Base offset | Port stride | Ports |
| --- | --- | --- | --- | --- |
| aggre1 | `0x016e0000` | `0x2000` | `0x1000` | 0, 1, 2, 3, 4, 5, 6, 7, 8 |
| aggre2 | `0x01700000` | `0x3000` | `0x1000` | 0, 1, 2, 3, 4, 7, 8, 9, 11, 12 |
| compute | `0x01700000` | `0x33000` | `0x800` | 6, 7 |
| gem | `0x09100000` | `0x21000` | `0x1000` | 0, 1, 2, 3, 64, 65, 66, 67, 127, 128, 129, 130, 131 |
| mmss | `0x01740000` | `0xa000` | `0x800` | 0, 1, 2, 4, 5, 6, 8, 10, 12, 13, 14 |
| system | `0x01620000` | `0x12000` | `0x1000` | 0, 1 |

This is 9 + 10 + 2 + 13 + 11 + 2 = 47 ports. All 188 words are distinct
and within the respective DT resources. Large gem port numbers, including
127 through 131, are actual QoSgen ports; an unused legacy MAXn=32 macro
does not constrain this layout.

Compute and aggre2 use the same resource base but different offsets.
The gem/mc and system/IPA resource aliases do not create extra independent
configuration state. Seven display client references identify already
listed physical ports; their fabrics bypass QoS programming and their
clients are not AP-owned. The board creates no duplicate display devices.

The AP-owned IPA client at aggre2 port 2 has defer-init-qos. Its hardware
words remain available in the reference profile, but the model does not
preload its policy or mark it initialized. A native observation must not
require every one of the 47 ports to have been programmed by the first
probe.

QOM paths are `/machine/noc-qos-<fabric>-<port>`, for example
`/machine/noc-qos-aggre1-7`. The read-only
`physical-port-address` property is the block address before
adding the word offset; for that example it is `0x016e9000`.

There is no whole-fabric memory region. Each port has four 4-byte regions,
mapped only at the addresses below. The gaps between words, between
ports, and all unlisted ports remain unmapped.

## Four words and access behavior

| Word / QOM property | Port offset | Supported write mask |
| --- | --- | --- |
| mainctl | `0x08` | `0x00000079` |
| limitbw | `0x18` | `0x03ff07ff` |
| regul0ctl | `0x40` | `0x00007703` |
| regul0bw | `0x48` | `0x03ff07ff` |

Only aligned 32-bit little-endian accesses are supported. Each valid write
replaces the complete word. A write containing any unsupported bit fails
atomically and leaves the previous word intact. This atomicity applies to
one 4-byte device transaction. A wider QTest or address-space request may
be split into several transactions; a later transaction into a hole does
not roll back a previously accepted word. Narrow and unaligned accesses
are rejected by the memory core. Readback returns actual stored
configuration; time advancement does not rewrite it, and reads have no
side effects.

MAINCTL includes default priority bits [6:4], urgent forwarding bit 3 and
limiter enable bit 0. The regulator control mask includes read/write
enable bits [1:0] and the two source-declared priority bit groups.
The two bandwidth words retain their documented raw fields. These are
configuration values, not measurements or predictions of traffic.

The memory region names are `qcom-noc-qos-mainctl`,
`qcom-noc-qos-limitbw`, `qcom-noc-qos-regul0ctl`
and `qcom-noc-qos-regul0bw`; addresses in a memory-core diagnostic
for one of these regions are relative to that individual 4-byte word.

## Preserve the actual vendor programming sequence

For each port the RPMh driver programs default priority, limiter,
regulator, then urgent forwarding. Its barriers order software writes;
the modeled register set supplies no invented ready or acknowledgement bit.

Some pinned source expressions are inconsistent with the apparent field
names:

- Both saturation masks are `0x03ff0000`, but the source shifts
  saturation values left by `0x11` (17).
- Regulator HI has mask `0x7000` but a left shift of 8; LOW has
  mask `0x0700` but a left shift of 12.
- Several regulator reads are first ANDed with one field mask, then a
  complete word is written. A later write can therefore discard another
  field that was written earlier. In particular, the regulator SAT write
  discards the previous BW field in that source sequence.

The model stores the exact word the guest actually writes. It does not
correct these source expressions, merge an imagined field update,
restore bits discarded by software, or reinterpret the values as a
physical bandwidth result. Tests must use the literal driver sequence
and independent nonzero complete-word patterns; the current DT's missing
limiter/regulator parameters and resulting zero words are insufficient
to prove a functional model.

Although the source retains legacy definitions at offsets 0x0c, 0x10 and
0x14, they are not used by this registered QoS initialization path. They
remain unmapped. The qos_freq callback parameter is unused by the
four-word initialization, so this device has no Clock input or invented
frequency.

## SBM and other fabric behavior remain separate

The original DT names config-NoC disable ports for camera, display and
venus. Their SBM base is `0x01500000 + 0x6000`, with registers
at offsets 0x80, 0x84, 0x88, 0x8c, 0x90, 0x94, 0x100 and 0x104.
None of those words overlaps a modeled QoS word and none is mapped here.

The source's SBM callback polls sense bits after separate reconnect or
disconnect requests. This model supplies no fake sense response.
Any later access to those interfaces is a separate hardware dependency,
not a reason to make all NoC reads or writes succeed. No existing GCC
parent or power domain is forced on for this configuration model.

## Observation, reset and migration

The four raw QOM properties are read-only uint32 observations. They do not
advance time, count writes or infer initialization. With reference mode
disabled, they return `reference profile is disabled`. The
physical address and immutable enabled-reference setting remain
inspectable. There are no QTest injection properties, timers, IRQs or
hardware-status synthesis.

Each instance is a SysBus child registered with the system reset tree.
Reset entry clears only the four configuration words; it leaves the fixed
physical identity and profile intact. In the unknown profile the internal
zero storage still cannot be read as valid hardware state.

VMState `qcom-noc-qos` has version 1 and minimum 1. Field order is
a uint32 equality check for the immutable profile, a uint64 equality check
for the physical port block address, and the four uint32 words. Thus
reference/unknown mismatches reject in both directions, and a stream
cannot silently attach one physical port's state to another port.
Loading rejects reserved bits and nonzero configuration in a disabled
profile. No output callback or post-load time advancement is needed.

This contract applies to this implementation and its fixed topology.
Compatibility with historical binaries that have no such devices is not
asserted. Same-profile tests use real migration streams and verify all
ports before further device MMIO; malformed-stream tests are identified
as corruption tests, not successful migrations.

Compiled-device acceptance and native-kernel observations must be
recorded separately from this source contract. Successful configuration
readback or probe continuation does not establish NoC scheduling quality,
camera/video/display execution, suspend behavior or Android boot.

## Executed 99ef checkpoint

Commit `99ef6052e3e10e84c67f3482471f4bde12ab295a` passed [66 component gates, four Linux boots and the independent A77/UFS job](../evidence/wholeboard-20261123-a47/public-ci-99ef.json). The actual board binary SHA256 is `226f826f02a97978f9f0afbcc1241dbcd6c25faca4840f8e1f72cee22f3d0000`. [The original NoC report](../evidence/wholeboard-20261123-a47/noc-qos-99ef.json) contains 20 successful checks, two completed process migrations, two profile mismatch rejections and two single-field corrupted-stream rejections. All four stream files and all 47 real device sections were checked from the artifact. These are executed configuration/migration tests; physical timing and bandwidth enforcement remain outside scope.

[The same binary's production-Image/full-DT run](../evidence/wholeboard-20261123-a47/native-99ef.json) crossed the earlier QSPI QoS abort. Its stopped read-only QOM snapshot found 47 correct physical identities, 46 nonzero nondeferred MAINCTL words and all 46 complete four-word DT configurations matching. Deferred IPA stayed at zero and was not required to match its eventual policy. The observer made no NoC MMIO transactions or guest writes and required the guest to remain paused before and after observation. This demonstrates stored guest programming, not an inferred completion acknowledgement. The next actual fatal was the separate QSMMUv500 TCU read at `0x15182018`; full native-DT userspace and Android remain unpassed.
