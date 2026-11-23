# Explicit SMMUv2 AArch64 granule profiles

The stage-1 model can walk 16 KiB and 64 KiB tables when explicitly configured.
Both TB371FC SMMU instances still default to 4 KiB only. This is an architectural
model capability, not a measurement of Qualcomm silicon. The captured native
DT establishes neither IDR2.PTFS support nor native page-walk behavior.

## Configuration and bounded behavior

`arm-smmuv2.aarch64-granules` is a bit mask: bit 0 selects 4 KiB, bit 1 selects
16 KiB, and bit 2 selects 64 KiB. Accepted values are `1`, `3`, `5`, and `7`.
4 KiB is required, so the IOMMU interface retains its 4096-byte minimum page
size. Zero, profiles without bit 0, and unknown bits fail realization. IDR2
PTFS[14:12] uses the same mask; reset does not change this immutable property.

For an explicit model experiment, add:

```sh
-global arm-smmuv2.aarch64-granules=7
```

That global option configures both apps and GPU SMMU objects. It does not
enable a GPU command processor, change the native tablet, or imply that a
guest using the production Qualcomm DT will boot.

| Format | TG0 | TG1 | Table index width | Valid block levels in this model | Page size |
| --- | --- | --- | --- | --- | --- |
| 4 KiB | `00` | `10` | 9 bits | L1: 1 GiB; L2: 2 MiB | 4 KiB at L3 |
| 16 KiB | `10` | `01` | 11 bits | L2: 32 MiB | 16 KiB at L3 |
| 64 KiB | `01` | `11` | 13 bits | L2: 512 MiB | 64 KiB at L3 |

The existing 48-bit VA/PA limits and TnSZ range 16 through 39 remain. There is
no LPA/LPA2 or 52-bit output format. The two TTBR regions may choose different
granules. The initial table uses its architected size-dependent alignment;
subsequent tables use the full granule alignment. A 16 KiB L0 initial table
can occupy only 16 bytes. With 64 KiB and TnSZ=39, the initial L3 table needs
only 4 KiB alignment. Treating every TTBR base as a full-page-aligned address
would silently walk different memory.

Successful PAR still contains PA[47:12], even for a large page or block.
Page offsets are returned by the IOMMU translation and survive real DMA;
descriptor AP/APTable restrictions, AF, MAIR, byte order, faults and IRQs use
the same implementation as the default 4 KiB walker. No hardware AF/DBM update
or internal IOTLB is introduced.

### Reserved and unsupported encodings

TG0=`11`, TG1=`00`, or a valid format disabled by the profile keep the existing
model's conservative rejection: ATS returns a global-fault PAR; client DMA
records GFSR.ICF and does not fall back to bypass. **This is a model deviation.**
IHI0062D.c specifies implementation-defined selection of an implemented
granule for reserved or unimplemented TG values; it does not mandate a fault.
Invalid block descriptor levels instead produce a context translation fault.

## Migration compatibility

VMState version 3 stores and checks the granule profile after the three geometry
fields, before loading register arrays. Different masks are incompatible even
if the running guest currently uses only 4 KiB. Silently adding or removing
IDR2 capability bits on the destination would change the hardware interface.

The minimum version remains 2. A v2 source has only the historical 4 KiB
capability, so the receiver accepts v2 only when its configured mask is `1`.
The non-default rejection occurs before post-load IRQ updates and invalidation.
Unqualified v1 input remains rejected. This compatibility path is one-way:
an old v2 binary cannot accept a v3 stream.

The new gate transfers a live mask-7 machine with distinct 16/64 KiB contexts,
then continues actual ATS and DMA on the receiver. It separately requires a
nonzero receiver exit and the SMMUv2 equality diagnostic for `1 -> 7` and
`7 -> 1`. The existing 18-check gate retains its default-profile fault/IRQ
restoration and both SMR-capacity mismatch cases. The new gate does not claim
a tested cross-version v2 transfer without an actual old source executable;
its result explicitly records `legacy_v2_transfer_tested: false`.

## Executable acceptance contract

```sh
python3 tb371fc/tests/test_smmuv2_granules.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/smmu-granules
```

The script contains 26 required semantic groups and is a required public
component gate. Its presence is an acceptance plan, not a passing receipt.
Every run records its own verdict, QEMU/test/helper hashes, migration results,
retained stderr and failure traceback. An incomplete run or changed binary
cannot pass. Existing 23 C QTests and 18 default migration checks remain
separate required regressions.

Hand-written constant table fixtures cover partial initial tables, 48-bit
input, a 16 KiB leaf index of 1024, a 64 KiB leaf index of 4096, a 64 KiB
initial L3 table, and mixed TTBR0/TTBR1 formats. They do not use the model's
walk-geometry formulas to construct expected results. Real DMA crosses 4 KiB
offsets within a page and crosses a granule boundary into a non-contiguous
physical page. Read-only, AF, hierarchical permissions, output-size and
external-walk checks distinguish ATS-only PAR failures from client FSR/IRQ
and inspect physical RAM for blocked writes. Valid L2 blocks and reserved
L1 blocks are checked independently. Reset keeps the configured capabilities.

UFS is disabled in these 128 MiB paused-process tests. There is no native-DT
boot, Android, rendering, hardware timing, external DMA-client cache or
whole-board migration claim.

## Fixed primary sources

- [Arm IHI0062D.c](https://documentation-service.arm.com/static/5f900d34f86e16515cdc08fb),
  SHA-256 `5f12c28c3a449381913112ac15cc914cfe2ea1a98ce3685f3ef079b247ca7e9d`:
  AArch64 stage-1 initial/table addresses in §2.6; IDR2 capability fields;
  §16.5 TCR TG encodings/fallback and PAR layout.
- [Linux io-pgtable-arm.c at adc218676eef25575469234709c2d87185ca223a](https://github.com/torvalds/linux/blob/adc218676eef25575469234709c2d87185ca223a/drivers/iommu/io-pgtable-arm.c):
  `arm_lpae_restrict_pgsizes()` distinguishes 4 KiB, 16 KiB and 64 KiB page/block
  sets; its additional 4 TiB block requires output width above 48 bits. Git blob
  `0e67f1721a3d980de842052cb37006492769e6d3`.
- [QEMU CPU walker at the fixed base 339b9cd277aeae84fba52b60ae34ede90b7ff101](https://github.com/cyjin-yl/qemu-tb371fc/blob/339b9cd277aeae84fba52b60ae34ede90b7ff101/target/arm/ptw.c):
  upstream `lpae_block_desc_valid()` restricts 16/64 KiB L1 blocks when LPA/LPA2
  is absent. The [generic SMMU walker at the same base](https://github.com/cyjin-yl/qemu-tb371fc/blob/339b9cd277aeae84fba52b60ae34ede90b7ff101/hw/arm/smmu-common.c)
  independently uses granule-dependent table indexes and masks.

The existing [source record](smmuv2-sources.json) preserves the hardware
evidence boundary. None of the architectural sources measures native TB371FC
granule capabilities.
