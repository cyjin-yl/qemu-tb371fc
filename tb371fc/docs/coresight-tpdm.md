# TB371FC TPDM management interface with no collection datasets

This model provides the CoreSight management registers at the 28 TPDM MMIO
ranges in the preserved TB371FC device tree. It deliberately advertises
**zero collection datasets**. It does not reproduce measured SM8250 TPDM
capabilities or provide trace, profiling counters, samples, DMA, interrupts,
software increments, or an integration datapath.

## Observed original-kernel boundary

Native diagnostic `dsu-591f34a-coresight-tpda-prng-3bfc-008` used source
`591f34a4db7805dcabfb73db0ddf6181b8f71b0f`, merge
`f51f40f169286f92d3c46169ea479d0e16eae77f`, and the once-consumed public artifact
15227483664 (SHA-256
`4e79a901b6ff7667e0aa58f35645e32d646fd42d7f7e9d4d964439d1a727cfb2`).
All original 116 core gates, the 16 TMC, 10 replicator, 12 STM and nine TPDA
additional checks, and the 11 ordinary supply-profile checks passed before
this run. These are the measured prerequisites, not a new TPDM test result.

The original kernel reached five TPDA probes, each returning `-EPERM`, then
aborted on a 32-bit read at `0x06b09fe8`. The PC was
`tpdm_probe+0x33c/0x5c8`, address `0xffffff8008d2bc04`.
The preserved Image function starts at `0xffffff8008d2b8c8`
(Image offset `0xcab8c8`); its 1,480 bytes have SHA-256
`2600bf0c295ef44df57550a649abe4eb49251d26867eb9fc0e12dc9be3f0574c`.
Its `add x8, x8, #0xfe8; ldr w8, [x8]` matches the missing PIDR2 read.
The following original reads are PIDR0 at probe offset `0x36c` and DEVID
at `0x550`.

The diagnostic stopped after the kernel failure, in 9.911 seconds, under
the unchanged 120-second maximum. QEMU acknowledged QMP quit and returned
zero; the runner returned two for the guest failure. All wrapper processes
were actually waited. Kernel, ramdisk, full DTB and bootargs remained
unchanged. No Android userspace was observed; this was a zero-LUN direct-kernel
diagnostic, not an OEM PBL/XBL/ABL boot or a complete Android boot.

## Why the DT match ID is not the dataset bitmap

The preserved full DTB has SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
Its hardware TPDM nodes have `arm,primecell-periphid` overrides of
`0x000bb968` or `0x0003b968`.

In the fixed vendor source, `of_amba_device_create()` assigns this property
to `dev->periphid`, and `amba_device_try_add()` skips hardware PID/CID
discovery when that value is nonzero. These properties supply the bus match
identity. They do not establish a hardware PIDR0 register value.

The vendor TPDM probe separately reads PIDR0 and interprets all its set bits
as dataset presence. Returning `0x68` solely because it appears in the DT
override would advertise TC, GPR and MCMB interfaces that this model does not
implement. Linux v6.12 also explicitly describes varying low eight bits of
TPDM peripheral IDs and masks those bits when matching its newer TPDM family.
The newer upstream designer ID is not used for this older vendor interface.

This model therefore returns PIDR0=0, retains the old designer-family bytes
PIDR1=`0xb9` and PIDR2=`0x0b`, and selects revision zero. Its reconstructed
virtual peripheral ID is `0x000bb900`. The original DT override remains
unchanged and still supplies the vendor bus match. Neither ID is described
as a physical capability measurement.

The vendor probe has no nonzero-dataset requirement and does not consult
AUTHSTATUS. Empty dataset allocation and initialization perform no collection
operations. The preserved Image reads `boot_enable` at
`0xffffff800a512ffc`, which the original symbol table places in BSS;
its initial value is zero. The optional empty-dataset software-enable path
has only unlock/relock writes because none of the 28 hardware nodes requests
`qcom,clk-enable`. The QTest checks that limited sequence separately and
does not claim to execute the Linux probe.

## Mapped hardware ranges

Every range is exactly 4 KiB. Board children `/machine/tpdm0` through
`/machine/tpdm27` follow this address order. Three original nodes are disabled;
their status properties remain unchanged. The dummy node at
`/soc/tpdm@6b26000` has no `reg` property and gets no TPDM MMIO range.

| Instance | Base | Original coresight name suffix | Original DT status | AMBA override |
|---|---|---|---|---|
| 0 | `0x6830000` | venus | enabled (status absent) | `0xbb968` |
| 1 | `0x6840000` | vsense | disabled | `0xbb968` |
| 2 | `0x6844000` | lpass | enabled (status absent) | `0xbb968` |
| 3 | `0x684c000` | prng | enabled (status absent) | `0xbb968` |
| 4 | `0x6850000` | pimem | enabled (status absent) | `0xbb968` |
| 5 | `0x6870000` | dcc | enabled (status absent) | `0x3b968` |
| 6 | `0x6900000` | gpu | enabled (status absent) | `0x3b968` |
| 7 | `0x6980000` | turing | enabled (status absent) | `0xbb968` |
| 8 | `0x6981000` | turing-llm | disabled | `0xbb968` |
| 9 | `0x69c0000` | dl-south | enabled (status absent) | `0xbb968` |
| 10 | `0x69d0000` | qm | enabled (status absent) | `0xbb968` |
| 11 | `0x6ac0000` | dl-north | enabled (status absent) | `0xbb968` |
| 12 | `0x6b09000` | swao-0 | enabled (status absent) | `0xbb968` |
| 13 | `0x6b0a000` | swao-1 | enabled (status absent) | `0xbb968` |
| 14 | `0x6c08000` | mm | enabled (status absent) | `0xbb968` |
| 15 | `0x6c28000` | dlct | enabled (status absent) | `0xbb968` |
| 16 | `0x6c29000` | ipcc | enabled (status absent) | `0xbb968` |
| 17 | `0x6c40000` | npu-llm | enabled (status absent) | `0xbb968` |
| 18 | `0x6c41000` | npu-dpm | enabled (status absent) | `0xbb968` |
| 19 | `0x6c47000` | npu | enabled (status absent) | `0xbb968` |
| 20 | `0x6c60000` | mdss | enabled (status absent) | `0xbb968` |
| 21 | `0x6e00000` | ddr | disabled | `0xbb968` |
| 22 | `0x6e10000` | ddr-ch02 | enabled (status absent) | `0xbb968` |
| 23 | `0x6e20000` | ddr-ch13 | enabled (status absent) | `0xbb968` |
| 24 | `0x7860000` | actpm | enabled (status absent) | `0xbb968` |
| 25 | `0x7861000` | apss | enabled (status absent) | `0xbb968` |
| 26 | `0x78a0000` | llm-silver | enabled (status absent) | `0xbb968` |
| 27 | `0x78b0000` | llm-gold | enabled (status absent) | `0xbb968` |

The original node spelling `/soc/tpdm@69810000` differs from its `reg`
address `0x06981000`. Mapping uses the actual `reg` cells, not that spelling.
The DCC node retains its `qcom,hw-enable-check` property; this change does not
modify the guest's secure-monitor call or its result.

## Implemented register contract

The allowlist contains **20 aligned register offsets**. Control accesses must
be exactly 32 bits and aligned. Narrow, wide and unaligned transactions are
rejected before state changes. The device callback receives the original
transaction width rather than silently splitting a wide write.

| Offset | Read behavior | Unlocked write behavior |
|---|---|---|
| `0xf00` ITCTRL | Zero | Ignored; integration is absent |
| `0xfa0` CLAIMSET | `0xf`, the four implemented claim bits | OR low four bits |
| `0xfa4` CLAIMCLR | Current claim bits | Clear written low four bits |
| `0xfb0` LAR | Unsupported | `0xc5acce55` unlocks; any other 32-bit key locks |
| `0xfb4` LSR | `3` when locked, `1` when unlocked | Rejected |
| `0xfb8` AUTHSTATUS | Fixed `0x88` | Rejected |
| `0xfbc` DEVARCH | Zero; architecture-present clear | Rejected |
| `0xfc8` DEVID | Fixed `0x15000000` | Rejected |
| `0xfd0` PIDR4 | `4` | Rejected |
| `0xfd4, 0xfd8, 0xfdc` PIDR5–7 | Zero | Rejected |
| `0xfe0` PIDR0 | Zero, no dataset presence bits | Rejected |
| `0xfe4` PIDR1 | `0xb9` | Rejected |
| `0xfe8` PIDR2 | `0x0b`, selected revision zero | Rejected |
| `0xfec` PIDR3 | Zero | Rejected |
| `0xff0, 0xff4, 0xff8, 0xffc` CIDR0–3 | `0x0d, 0x90, 0x05, 0xb1` | Rejected |

The software lock ignores writes to implemented management registers other
than LAR. Absent collection and control registers are outside this allowlist
and reject accesses even while locked. This distinction is tested.

DEVID has the vendor enum value `NO=2` in TC trigger bits 28:27, BC trigger
bits 26:25 and BC ganging bits 24:23. Its BC and TC counter-number encodings
are zero. The vendor's arithmetic decodes those fields to one, but they only
describe counters when the relevant dataset exists. PIDR0 advertises neither
BC nor TC, so no counter dataset is present. The test retains both the raw
field decoding and the absent-dataset result instead of calling those
encodings physical zero-counter counts.

AUTHSTATUS `0x88` is an explicit model configuration: secure and nonsecure
non-invasive authentication classes are implemented but disabled; invasive
debug classes are not implemented here. It does not cause this vendor probe
to return an error and is not a claim about the tablet's authentication pins.
There is no property or migration field that can enable collection.

DEVTYPE `0xfcc`, unspecified management holes, all dataset registers,
clock control `0x220` and integration datapath control `0xef0` fail with
`MEMTX_ACCESS_ERROR` and a specific guest-error diagnostic. No whole-page
RAZ/WI bank supplies fabricated successful collection or configuration.

## Reset and migration

Each instance resets to claim zero and locked. Profile identity
`0x54504d01` is compared during ordinary migration; only claims and the
software-lock state are restored. The post-load check rejects claims outside
the four implemented bits. Fixed capability and authentication values are
constants, not writable or migrated state.

The dedicated test uses real QTest/AddressSpace accesses, verifies all 28
memory-map ranges and both unassigned neighbors of the final bank, and
contains ten named checks with 56 rejected transactions. It includes a
64-bit access wholly within the final ID words, plus an independent
cross-aperture request whose actual dispatch behavior must match its
recorded diagnostic. A different observed dispatch must fail the test.

It also exercises system reset, varied claims and lock states across all
28 instances, an unmodified ordinary migration stream, continuation on both
source and destination, and actual waiting and reaping of both child
processes. It does not test a deliberately corrupted migration stream or
execute a guest CPU. The stream itself is excluded from the public artifact;
its receipts, protocol, process records, stderr and memory-map evidence are
retained. Existing core, TMC, replicator, STM and TPDA gates remain separate.

The implementation and test require actual build and CI execution before
their results can qualify a new original-kernel diagnostic. The failed
native008 artifact is already consumed and is not replayed.

## Primary sources

- [Fixed vendor TPDM driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/coresight/coresight-tpdm.c),
  Git blob `8744b2b4a7a8c1725c37504a7cd949d9dc12e6b1`,
  SHA-256 `9887f26f3daed739420000bfb83034c2809edcec407991d82d1ce6249397176b`.
- [Vendor OF platform enumeration](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/of/platform.c),
  Git blob `8b339f03a0ad483677d48acdf4f8116b7631ced0`.
- [Vendor AMBA enumeration](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/amba/bus.c),
  Git blob `41b706403ef72ec94848b7dae98867ecd30ec971`.
- [Linux v6.12 TPDM driver](https://github.com/torvalds/linux/blob/v6.12/drivers/hwtracing/coresight/coresight-tpdm.c)
  and [header](https://github.com/torvalds/linux/blob/v6.12/drivers/hwtracing/coresight/coresight-tpdm.h):
  varying peripheral-ID low bits and dataset-presence semantics, not a source
  for copying the newer designer identity into the older device.
- [Arm CoreSight Architecture Specification, IHI0029D](https://documentation-service.arm.com/static/63a039b41d698c4dc521ca6f),
  sections B2.5.5 and B2.5.9–B2.5.11: authentication, claim tags and software
  locking. The chosen four claim bits and fixed authentication inputs are
  model configuration choices.
