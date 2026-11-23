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
a tested cross-version v2 transfer without an actual old source executable.
The public CI invocation explicitly records `legacy_v2_transfer_tested: false`.
Supplying `--legacy-qemu /path/to/actual-v2-qemu` adds two separate checks:
v2-to-mask-1 ATS/DMA continuation and v2-to-mask-7 rejection with the dedicated
version-2 diagnostic. Both executables are hashed before and after the run.
A passing extended run has 28 groups and five migration attempts; a current
v3 executable cannot pass as the legacy input because its rejection diagnostic
differs. This optional path does not add an expiring binary-artifact dependency
to the public CI gate.

## Executable acceptance contract

```sh
python3 tb371fc/tests/test_smmuv2_granules.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/smmu-granules
```

The script contains 26 required semantic groups and is a required public
component gate. Its presence alone is an acceptance plan, not a passing receipt.
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

## Measured CI result

The [run for b5f4415](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45219017052)
completed both jobs successfully. The retained
[granule report](../evidence/smmuv2-granules-20261123/ci-granules.json)
passes all 26 groups and three migration attempts. The
[component report](../evidence/smmuv2-granules-20261123/ci-components.json)
passes 55/55 gates, including the 23 C QTests without skips and all 18 default
SMMUv2 migration checks. The separate
[Linux report](../evidence/smmuv2-granules-20261123/ci-linux.json)
passes all four integration gates. The A77/shared-UFS job also passed its
852 guest checks, two rejected CPU configurations and 15 PCI cases.

Public artifact `14842111035` contains source commit
`b5f441554b01900a33ada617054f6e7f2729e5b8` and QEMU SHA-256
`3d3ece4d21e3806210920a2cb83531c02f825d1cdb428980590e4fc49bc82336`.
The executable bytes were hashed independently against the artifact's receipt.
These are exact-build observations; the CI invocation did not supply a legacy
v2 executable and therefore does not attest the cross-version path.

### Actual v2-to-v3 transfer in CT102

The [extended CT102 report](../evidence/smmuv2-granules-20261123/ct102-with-glib.json)
then passed **28/28 groups and five migration attempts** in 2.358 seconds,
using that unchanged CI executable and the extended test script with SHA-256
`9a83189384fb9f1f07bfc7d2a744b579a08bc459c231c2639ac7cefa0759111a`.
The old source was the actual CI-built v2 executable from commit
`339b9cd277aeae84fba52b60ae34ede90b7ff101`, SHA-256
`7964b2a21fccede724639f6503f5e19ae91d71b0df4051b10a6f51fd8337b0f0`.
Both binary hashes were checked before and after the run. The v2-to-mask-1
receiver continued ATS and real DMA reads/writes; the v2-to-mask-7 receiver
exited nonzero with the dedicated version-2 compatibility error. The extended
script also checks profile 9, which combines the required 4 KiB bit with an
unknown capability bit, and requires realization to reject it.

The [first CT102 attempt](../evidence/smmuv2-granules-20261123/ct102-missing-glib.json)
failed before any check because this Docker image lacked `libglib-2.0.so.0`.
Its [original loader stderr](../evidence/smmuv2-granules-20261123/ct102-missing-glib.stderr)
is retained. This was an environment failure, not a SMMU counterexample.
The same Debian 13 runtime package was downloaded, SHA-256 checked, and
extracted under the isolated test directory without a global installation.
The [dependency receipt](../evidence/smmuv2-granules-20261123/ct102-deps.json)
binds the package and library bytes; the
[run receipt](../evidence/smmuv2-granules-20261123/ct102-run.json)
records the process-local library path. No QEMU executable was rebuilt or
patched to obtain the passing result. At most two paused 128 MiB QEMU
processes existed at once; none remained after the test. The
[publication record](../evidence/smmuv2-granules-20261123/publication.json)
binds the CI and CT102 inputs and receipts without promoting either to native
hardware or whole-board evidence.

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
