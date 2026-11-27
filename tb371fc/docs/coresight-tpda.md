# CoreSight TPDA authentication-disabled management profile

The preserved TB371FC full DT contains five AMBA TPDA instances. Native007
reached their probe after passing the previous STM failure, then took an
external abort on a 32-bit read of the first TPDA's AUTHSTATUS. This change
implements a deliberately limited management profile for those five apertures.

The profile reports both non-invasive debug classes as implemented and
disabled. The original driver's authentication predicate rejects this profile,
so the probe returns -EPERM before TPDA configuration or CoreSight registration.
This is an expected
unavailable-device result. It is not successful TPDA probing, trace aggregation,
trace delivery, Android userspace, or Android boot.

## Original evidence

Native007 used source commit
615a986be6de6501ddc4f7401e1aaf504913a942, merged without tree changes by PR55
as cbc49f43adcded9fe15892783b85aceac66322ee. Its fresh artifact 15221029676
was consumed once after the original core 116, TMC 16, replicator 10, STM 12,
and original profile 11 qualifications passed.

The original kernel, ramdisk, full DTB and boot arguments retained their input
digests. The runner used the unchanged 120-second budget and stopped on the
next kernel failure after 10.58 seconds:

- First external abort at guest 6.544892 seconds.
- PC: tpda_probe+0x270/0x420; LR: tpda_probe+0x20c/0x420.
- Physical address: 0x06b08fb8, a 32-bit read at TPDA base + 0xfb8.
- Original function VA: 0xffffff8008d2a6e8; Image offset: 0xcaa6e8.
- Fatal instruction VA: 0xffffff8008d2a958; instruction: 0xb9400108.
- The retained 1,056 function bytes have SHA-256
  d0246d4ab25f1fa86c8fdaec8151ebf94cfbf205dbdb4ee953cc22b6b42847f9.
- Original DT SHA-256:
  5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207.
- Native result SHA-256:
  12a948aeae9aede1345bce772c547cbe9505abf9001e8b7d5d2aeebeaa203a42.
- Printk SHA-256:
  e3a269e0fc3711cbf7dce6204bb1d1f16b0a7d806169d6d0f60e727a166516de.

The native process returned the kernel-failure result, QEMU exited zero after
QMP quit, all owned processes were actually waited and reaped, and the
one-consumption claim remains retained. Native007 is not replayed.

Each node has compatible arm,primecell, periphid 0x000bb969, a 4-KiB tpda-base
resource, and apb_pclk from the original clock phandle:

| Original node | Address | CoreSight name |
| --- | --- | --- |
| /soc/tpda@6004000 | 0x06004000 | coresight-tpda |
| /soc/tpda@69c1000 | 0x069c1000 | coresight-tpda-dl-south |
| /soc/tpda@6ac1000 | 0x06ac1000 | coresight-tpda-dl-north |
| /soc/tpda@6b08000 | 0x06b08000 | coresight-tpda-swao |
| /soc/tpda@7863000 | 0x07863000 | coresight-tpda-apss |

The board maps these apertures directly. The original guest inputs are not
modified.

## Primary contracts and implementation choices

The fixed
[vendor driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/coresight/coresight-tpda.c)
has Git blob e1f77f684b367f40e9b8672ff8c27eac1c34e8e3 and SHA-256
263e07a1f673f8035687a6130a3d3a337551ccbdc4c801491628adfffe644162.
Its AMBA match ID/mask are 0x0003b969/0x0003ffff. Its
coresight_authstatus_enabled helper reads offset 0xfb8 and rejects if any of
the four two-bit authentication fields equals 2. The probe returns -EPERM on
that path before any component configuration or coresight_register call.
Returning zero from AUTHSTATUS would not disable this driver.

The common register semantics follow
[Arm IHI 0029D, CoreSight Architecture v2.0](https://documentation-service.arm.com/static/63a039b41d698c4dc521ca6f),
particularly sections B2.5.5, B2.5.6, B2.5.9–B2.5.11 and D2.3. Authentication
disables the applicable functionality rather than preventing access to the
implemented management registers.

No public TPDA-specific TRM was used to infer unmeasured component capabilities.
The following are explicitly selected model properties, not physical-silicon
reset measurements:

| Register or state | Implemented behavior |
| --- | --- |
| AUTHSTATUS, 0xfb8 | Read-only 0x88. NSID=0, NSNID=2, SID=0, SNID=2. |
| Invasive authentication | Not implemented; the two corresponding fields are 0. |
| Non-invasive authentication | Implemented and permanently disabled in both security domains. |
| DEVARCH, 0xfbc | Zero, PRESENT=0; no standard TPDA/router architecture is claimed. |
| LAR/LSR, 0xfb0/0xfb4 | 32-bit key 0xc5acce55 unlocks. Every other key locks. LSR is 3 after reset and 1 unlocked. |
| CLAIMSET/CLAIMCLR | Four implementation-selected tags. SET reads 0xf and writes W1S. CLR reads current tags and writes W1C. Reset is zero. |
| ITCTRL, 0xf00 | RAZ/WI; integration is not implemented. |
| PIDR/CIDR | AMBA identity selected to match original DT part/designer/revision bytes; class-9 component, one 4-KiB aperture. |
| DEVID/DEVTYPE | Explicitly unsupported; no unverified port count, implementation capability, or device classification is returned. |
| TPDA component-specific registers | No aggregation/configuration/flush implementation or writable shadow storage. Unsupported accesses are diagnosed. |

Authentication is a fixed constant. There is no guest register, QOM property,
or migration field that can enable it. A model-profile equality field prevents
a different migration profile from being accepted.

The software lock ignores writes to recognized control registers, including
claims and the vendor's known component-control offsets; LAR remains usable.
Once unlocked, writes to unsupported component registers fail explicitly.
Read-only identification/authentication/status cannot become writable shadow
state. Unknown holes fail independently of authentication. Accesses must be
aligned 32-bit APB transactions; malformed accesses inside the aperture cannot
be split into state-changing narrow writes.

There is no trace source, aggregation datapath, ATB output, port enable/routing
operation, flush-completion engine, DMA, interrupt generation, or integration
datapath. Those limitations are not presented as successful operations.

## Validation

tb371fc/tests/test_coresight_tpda.py uses the existing process-owning QMP/QTest
transport and real AddressSpace MMIO entry points. It requires nine scenarios:

1. Five original-DT AMBA identities and actual flat memory-map apertures.
2. Reset locks, four-bit claim masks, and fixed 0x88 authentication.
3. The exact vendor authentication predicate driven by a real AUTHSTATUS read
   from each instance, without issuing component-configuration writes.
4. Independent claim set/clear state and absent integration.
5. Software-lock protection, including a rejected wide LAR write that must not
   partially unlock the component.
6. Forty-six strict-access rejection cases plus a real 64-bit write crossing
   the last APB word into the unassigned address beyond the aperture.
   Together with the locked wide-access case, 48 rejected accesses are required;
   no mutable TPDA state or component identity may change.
7. System reset of all five instances.
8. Ordinary migration of five distinct claim/lock combinations followed by
   identical set/clear/relock continuation in an independent target process.
9. Every created process is actually waited and reaped, normal QMP quit and
   channel drains complete, and no QOM state setter is used.

The test preserves the raw protocol and stdio receipts and hashes the actual
memory-map output. It reports that it did not execute a guest CPU or the
original Linux probe. Its migration coverage is an unmodified checkpoint and
continued operation; it does not claim malformed-stream rejection testing.

CI adds this gate without changing the original core, TMC, replicator, STM,
Linux, A77 or shared-PCI gates. Raw migration streams are excluded from the
uploaded artifact; their produced hashes and the real protocol/cleanup receipts
remain. A later native diagnostic must use a new exact-source qualified
artifact and the original once-claim and bounded runner. Its actual next
boundary determines progress beyond native007.
