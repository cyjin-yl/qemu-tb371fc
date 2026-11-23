# Arm SMMUv2 stage-1 model for TB371FC

This component implements non-secure AArch64 stage-1 DMA translation for the
two SMMU apertures observed in the tablet's live device tree. It performs real
RAM-backed translation-table walks and permission checks. It is an architectural
functional subset, not an implementation of the Qualcomm QSMMU v500 extensions
or a claim that the complete tablet is emulated.

## Evidence and board integration

The hardware source is
[`android-device-inventory.json`](../evidence/expansion-20261123/android-device-inventory.json),
SHA-256 `3797824168bc12cd65d8079ffe428c45a4126f54b2170460981ee8903d504263`.
The collector retained allowed device-tree property bytes, hashes and decoded
big-endian cells; it did not read SMMU registers. Native IDR capabilities,
translation behavior and implementation-specific state have therefore **not**
been measured.

| Instance | Native DT node | Architectural aperture | Context banks configured | QOM object |
|---|---|---|---|---|
| Applications | `/soc/apps-smmu@15000000` | `0x15000000`, 1 MiB | 96 | `/machine/apps-smmu` |
| GPU | `/soc/kgsl-smmu@3da0000` | `0x03da0000`, 64 KiB | 8 | `/machine/kgsl-smmu` |

The configured context-bank counts follow the captured interrupt topology after
its two global interrupts. They are not direct reads of native IDR1.NUMCB.
The native compatibles are `qcom,qsmmu-v500`, with `#iommu-cells = <2>`,
TCU apertures and TBU children. The generated guest DT deliberately describes
the implemented generic `arm,smmu-v2` interface with one stream-ID argument,
two global interrupts and `dma-coherent`. It does not advertise Qualcomm
extensions or MMU-500 implementation identity.

Exact interrupt evidence is in these JSON paths:

- `nodes["/soc/apps-smmu@15000000"].properties.interrupts.be32`
- `nodes["/soc/kgsl-smmu@3da0000"].properties.interrupts.be32`

Each interrupt has GIC cells `<0 SPI 4>`. Applications global SPIs are 64 and 65;
its remaining 96 entries are the ordered context-bank interrupt mapping. GPU
global SPIs are 672 and 673; its context SPIs are 678 through 685. Preserve the
applications array's order and gaps instead of synthesizing a contiguous range.
The raw property hashes are respectively
`ddb2ce290744c6b97b8b8806c04e703daf3f274730c4a6123928df2edbec10fe` and
`b5b8659d58cffc7a2048e5c075f55afa816271d1c14a88f835520e35e5ca51a2`.

The current UFS DMA port uses applications stream ID `0xe0`. The guest DT also
lists `0x4e0`; both are assigned to the UFS domain by the generic Linux driver.
Only one physical DMA port is currently emitted by the UFS model. GPU clients
have not been implemented merely by instantiating their SMMU.

## Device interface

The implementation is in [`hw/arm/arm-smmuv2.c`](../../hw/arm/arm-smmuv2.c)
and its public API is in
[`include/hw/arm/arm-smmuv2.h`](../../include/hw/arm/arm-smmuv2.h).

| Property | Meaning | Default |
|---|---|---|
| `mmio-size` | Total architectural aperture, power of two | `0x10000` |
| `num-context-banks` | Implemented context banks | 8 |
| `num-smrs` | Stream-match entries | 128 |
| `memory` | Target physical MemoryRegion | System memory |

After realization, `arm_smmuv2_get_dma_as(dev, sid)` returns an AddressSpace for
a specific stream. `arm_smmuv2_get_dma_mr(dev, sid)` exposes the equivalent
IOMMUMemoryRegion for device DMA links. Incoming privilege/security attributes
remain distinct translation indexes. Secure transactions are unsupported and
produce an unsupported-transaction fault.

SysBus MMIO 0 contains GR0 at offset 0, GR1 at offset `0x1000`, and context bank
`n` at `mmio-size / 2 + n * 0x1000`. SysBus IRQ 0 is the global client-fault
output, IRQ 1 the global configuration-fault output, and IRQ `2+n` the context
output. The IRQs belong to the named `sysbus-irq` GPIO group.

## Implemented behavior

The IDRs describe **this model**: stage 1, stream matching, 15-bit stream IDs,
4 KiB AArch64 tables, 48-bit input/output translation limits, coherent table
reads, and the configured context-bank count. Extended stream IDs, stage 2,
nesting and AArch32 contexts are not advertised.

SMRs are matched using their valid/ID/mask fields. Missing, conflicting and
out-of-range mappings record global faults when the configured architectural
fault controls require them. Explicit architectural bypass modes remain
available: CLIENTPD, stream bypass, disabled context translation, and the
architecturally selected no-match/conflict policies. A failed translation never
falls back to bypass.

The stage-1 walker supports separate TTBR0 and TTBR1 regions, canonical-address
checks, TnSZ 16 through 39, 4 KiB pages, level-1 and level-2 blocks, hierarchical
APTable restrictions, leaf AP permissions, access flags and output-address-size
checks. TTBR1 is selected independently; a noncanonical upper address cannot
silently alias TTBR0. ATS supports TBI0/TBI1 and returns PAR without generating a
client fault interrupt for a stage-1 translation failure.

Translation tables must reside in RAM/ROM storage. A walk into unmapped memory
or MMIO records an external table-walk fault. This restriction prevents a
recursive MMIO/IOMMU page-table access while the model lock is held. Table byte
order follows SCTLR.E. There is no modeled table-walk cache or timing.

Context faults record FSR, FAR, FSYNR0 and CBFRSYNRA; global faults record GFSR,
GFAR and GFSYNR0/1. Fault status is W1C. Multiple-fault state preserves the first
address/syndrome and sets MULTI. CFIE/GFIE control interrupts. Pending faults
block subsequent client transactions when HUPCF is clear.

TLB maintenance completes synchronously. Every new access walks the current
tables; invalidation also notifies registered external UNMAP consumers.
MAP/DEVIOTLB notifier consumers such as VFIO are unsupported.

## Normative details that need care

The primary specification is
[Arm IHI0062D.c](https://documentation-service.arm.com/static/5f900d34f86e16515cdc08fb).
The downloaded PDF SHA-256 is
`5f12c28c3a449381913112ac15cc914cfe2ea1a98ce3685f3ef079b247ca7e9d`.

| Detail | Specification | Implemented interpretation |
|---|---|---|
| SMMUv2 PAR failure layout | §16.5.14, pp. 16-308–16-309 | F is bit 0; TF/AFF/PF/EF/ASF use bits 1/2/3/4/7; AArch64 Format is bits 9:8 = 2; PLVL is bits 33:32. This is not the CPU PAR_EL1 FST encoding. |
| PASize reserved encodings | §16.5.39, p. 16-338 | Encodings 6 and 7 are treated as 48-bit, as explicitly specified. Do not turn them into faults solely because the field labels them reserved. |
| Global fault address | §9.6.14, pp. 9-208–9-209 | GFAR records the original input address of the first outstanding fault. |
| Global syndrome | §9.6.17, pp. 9-211–9-213 | WNR is bit 1; PNU is bit 2, where 1 means privileged. |
| Context syndrome | §16.5.11, pp. 16-301–16-303 | WNR is bit 4; PNU is bit 5, where 1 means privileged after PRIVCFG transformation. |
| Extended-ID enable | §9.6.3, pp. 9-193–9-194 | With IDR0.EXIDS = 0, CR0.EXIDENABLE is RAZ/WI. |
| First-fault preservation | §3.8.1, p. 3-115 | Later global faults set MULTI without replacing the original details. |

Block output addresses are extracted according to walk level: low descriptor
bits outside the block base are masked, and the input's block offset is added.
This follows the existing QEMU SMMU walker and the correction discussed in
[QEMU issue 790](https://gitlab.com/qemu-project/qemu/-/issues/790), which points
to Arm ARM D5.3.1 and `AArch64.BlockBase`. A RES0 label alone is not evidence that
every nonzero low bit must raise a translation fault. This project has not yet
performed an independent exhaustive audit of all Arm ARM descriptor RES0 cases.

The Linux programming sequence was checked against the upstream
[`arm-smmu.c`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/iommu/arm/arm-smmu/arm-smmu.c?h=v6.12.114)
and
[`arm-smmu.h`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/iommu/arm/arm-smmu/arm-smmu.h?h=v6.12.114)
from Linux 6.12.114. Their local source hashes are recorded in
[`smmuv2-sources.json`](smmuv2-sources.json).

## Validation

The QTest fixture exists only when `qtest_enabled()` is true. It aliases
`0x50000000..0x50ffffff` to applications SID `0x42`, IOVA `0..0xffffff`.
The alias is absent from normal TCG execution and the guest DT. Test reads and
writes therefore traverse the same IOMMUMemoryRegion callback used by an actual
DMA client, and tests inspect physical RAM to prove both translation and
write-blocking.

[`tb371fc-smmuv2-test.c`](../../tests/qtest/tb371fc-smmuv2-test.c) contains 23
cases: capabilities; page read/write; SMR masking; missing/conflicting streams;
invalid/out-of-range contexts; read-only, access-flag, invalid-leaf and hierarchical
permission faults; unprivileged permissions; output-address size; external table
walk; block translation; invalidation; ATS-only failure; independent TTBR1; TBI1;
unsupported granules/stages/AArch32; pending-fault blocking; first-fault preservation.

The initial strengthened tests were run individually against frozen binary
`ddb2b5e4ef7719c7812d0fec01cd5c4b0bf296bd1a0198270b55d9599aaaadd0`:
14 passed and 9 failed. The failures independently demonstrated missing GFAR,
incorrect PNU polarity and unsupported EXIDENABLE readback. The model was then
corrected. The complete suite then passed **23/23** against frozen binary
`a70862bfa8aa957ea26e362b5defd9ae60b4cc4bb53f2ac2c70c0b4fe3b67e2a`,
with process exit 0. See the [actual result](../evidence/smmuv2-20261123/qtest-result.json)
and [QTest log](../evidence/smmuv2-20261123/qtest.log). The
[pre-fix result](../evidence/smmuv2-20261123/pre-fix-result.json) preserves the
independent counterexamples. The final model source hash is
`5c620f3e93ebb9e26a2cfae41f26401fb3dce65bed1ea43f8384ecab800daf2c`.
These results establish this test scope on that exact binary.

To execute the current suite after building the registered QTest target:

```sh
ninja -C build tests/qtest/tb371fc-smmuv2-test
QTEST_QEMU_BINARY="$PWD/build/qemu-system-aarch64" \
  build/tests/qtest/tb371fc-smmuv2-test --verbose
```

## Remaining limits

Stage 2/nesting, AArch32 translation tables, 16/64 KiB granules, hardware AF/dirty
updates, transaction stalls/resume, secure-world allocation, Qualcomm TCU/TBU,
ACTLR implementation effects, performance counters and hardware timing are not
implemented. Unsupported configured translation modes fault rather than being
treated as TTBR0 or bypass. Generic configuration-access fault generation is not
complete even though the second global IRQ output exists.

GFRE/CFRE-masked upstream RAZ/WI responses are not modeled as a separate bus
sink: failed DMA receives IOMMU_NONE. Cacheability/shareability metadata in PAR
is represented, but data-cache behavior is not simulated.

Migration fields and reset/post-load IRQ restoration are implemented; SMMU
migration has not yet been separately validated. The QTests establish selected
architectural behavior against the implementation, not native SMMU silicon
equivalence. A Linux driver binding to the generated DT and translated UFS I/O
are additional integration gates. Neither gate establishes that the production
Android DT, GPU, display or all tablet peripherals work.
