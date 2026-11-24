# TB371FC apps SMMUv500 reference companion

`qcom-smmuv500` adds the bounded Qualcomm TCU/TBU interface for the
original DT's apps SMMU. It is a companion to the existing functional
`arm-smmuv2` stage-1 model, not a second page-table walker. The
architectural aperture, context banks, faults, IRQs, ATS operations and
VMState version 3 remain owned by that core. The current core adds an explicit
granule profile while the board still defaults to 4 KiB. The frozen 3ef98 and
52492a receipts used core version 2; new combined-source results are required.

## Evidence and current scope

The direct native fault was a 32-bit read at physical `0x15182018`
in `qsmmuv500_arch_init+0x88`, after NoC configuration and discovery
of 128 SMMU stream groups. This is the original apps TCU VERSION word.

The primary implementation source is official OnePlus
[arm-smmu.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/iommu/arm-smmu.c),
pinned at `94304e12a3509eac7b3716bf574ce0b1fcaacbae`;
SHA256 `ccc4b09010c420ea42c9838d397b0b81fe6a0a4e345e95cb935c2e70308fbf49`.
The [source research contract](smmuv500-source-contract.md) records the
supporting headers and upstream distinctions. The
[bounded original-DT capture](../evidence/wholeboard-20261123-a47/smmuv500-dt-evidence-99ef.json)
preserves raw cells from DTB SHA256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.

The research includes both apps and KGSL SMMUs, twelve TBUs in total.
This implementation covers only **apps TCU plus its ten TBUs**. Existing
KGSL architectural state remains unchanged; its vendor TCU/TBU resources
are not mapped here. Its unresolved GCC parent-clock warning is not fixed
by enabling this companion. The four MNOC TBU GDSC control words are already owned by the existing
GCC model (power domains 7–10); their native provider and consumer
readiness require separate run evidence. The PCIe TBU CBCR is also
modeled, while its external parent remains unknown and unconnected.
The apps companion does not add power-state coupling to those dependencies.
The [complete GDSC DT and ownership review](../evidence/wholeboard-20261123-a47/smmuv500-tbu-gdsc-review.json)
records this distinction.

## Explicit reference identity

Default mode is unknown. Its mapped interface reads and writes return
transaction errors; runtime QOM queries also return an unknown-profile
error. Physical geometry and immutable configuration remain inspectable.
Enable the digital reference contract with both arguments:

```text
-global qcom-smmuv500.enabled-reference=on
-global qcom-smmuv500.reference-hw-version=0x02000000
```

`0x02000000` identifies the modeled non-v1 protocol. It is **not a
measured TB371FC silicon version**. Enabling the model without that explicit
identity is rejected; unknown mode requires version zero. Version
`0x01000000` is not supported: the pinned driver bypasses the status
semaphore for that value, so it cannot be selected to avoid implementing
arbitration.

The reference profile starts all requests clear, PAR faulted (value 1),
and the ECATS owner empty. It uses a 1000 ns virtual delay for halt ACK
and for debug translation. Initial conditions, arbitration details and
delays are explicit model policies; the driver establishes the interface
and its 100 us timeout, not these silicon timings.

## Physical ownership

The architectural apps aperture remains `0x15000000 + 0x100000`.
The companion QOM path is `/machine/apps-smmu-v500`.
Its sparse container is based at `0x15182000`, covering only the
mapped words below. Unmapped container gaps do not shadow sibling
regions and do not become RAM.

| TBU index | Original DT name | Debug base | Semaphore address | Complete SID range |
| --- | --- | --- | --- | --- |
| 0 | anoc_1 | 15185000 | 15182200 | 0000–03ff |
| 1 | anoc_2 | 15189000 | 15182208 | 0400–07ff |
| 2 | mnoc_hf_0 | 1518d000 | 15182210 | 0800–0bff |
| 3 | mnoc_hf_1 | 15191000 | 15182218 | 0c00–0fff |
| 4 | compute_dsp_1 | 15195000 | 15182220 | 1000–13ff |
| 5 | compute_dsp_0 | 15199000 | 15182228 | 1400–17ff |
| 6 | adsp | 1519d000 | 15182230 | 1800–1bff |
| 7 | anoc_1_pcie | 151a1000 | 15182238 | 1c00–1fff |
| 8 | mnoc_sf_0 | 151a5000 | 15182240 | 2000–23ff |
| 9 | mnoc_sf_1 | 151a9000 | 15182248 | 2400–27ff |

Addresses and SIDs in this table are hexadecimal. VERSION is a 32-bit
read-only word at TCU +0x18. Only the low 32-bit semaphore word is mapped;
the upper four bytes of each DT status resource remain a hole.

| TBU word | Offset | Access | Supported state |
| --- | --- | --- | --- |
| HALT/SID | 00 | 32/64-bit R/W | HALT bit 16, local SID bits [9:0] |
| VA | 08 | 32/64-bit R/W | 36-bit IOVA |
| TRIGGER | 18 | 32/64-bit R/W | raw mask 1ff; starting requests only fd/ff |
| STATUS | 20 | 32-bit R/O | HALT_ACK bit 1, ECATS_RUNNING bit 0 |
| PAR | 28 | 32/64-bit R/O | fault bit 0, complete byte PA in bits [47:12] |
| AXUSER | 30 | 32/64-bit R/W | CDMID bits [43:36] |

This gives 71 individual register windows and 484 mapped bytes. Register
names are prefixed `qcom-smmuv500-` and use version, semaphore,
halt-sid, va, trigger, status, par and axuser. Paired registers support
aligned low/high 32-bit accesses as well as aligned 64-bit accesses.
Unsupported bits reject the individual device transaction unchanged.
Wider or unaligned address-space requests can split at window boundaries;
a later hole error does not roll back an earlier accepted transaction.

TCU testbus +08, TBU +10/+24, testbus +50/+58, capture/debug ranges and
semaphore +4 remain holes. This companion does not add Qualcomm testbus
semantics inside the existing architectural aperture either; that
aperture retains the core's existing behavior rather than gaining a
working vendor debug bus.

## Halt, ownership and actual translation

A rising HALT request immediately prevents new ordinary DMA translations
for the complete TBU SID range and invalidates external IOMMU cache
entries through the core's real UNMAP notifier path. After 1000 ns,
HALT_ACK becomes set. Clearing a request cancels a pending ACK and
immediately permits new translations. The emulated DMA API is synchronous:
blocked accesses return access errors and are not queued for later replay.
This is a reference limitation, not a model of the physical stall queue.

ECATS has one owner across all ten TBUs. Reading a free semaphore
acquires it and returns 1. While owned, reads at every semaphore,
including the owner's, return 0. Only an owner write of zero with no
pending ECATS operation releases it. Nonowner, nonzero and pending
releases fail unchanged. This read-to-acquire arbitration is an explicit
reference interpretation of the vendor's poll-for-1/release-with-0
sequence; the source does not specify every contention detail.

A trigger requires an acknowledged halt, ownership, CDMID 255 and
nonsecure privileged data attributes. Values fd and ff select read and
write respectively. Only a 0-to-1 trigger transition starts work;
retriggering before clearing it fails. A zero high half of a split
64-bit trigger write is accepted without starting a second request.

The request captures IOVA, local SID and access type. Subsequent
HALT/SID, VA or AXUSER staging writes do not change the captured request.
The complete SID is the TBU range start plus the local ten bits. After
1000 ns the companion calls the same stream-match, bypass-configuration,
stage-1 walk, permission and fault/IRQ path as ordinary DMA. It bypasses
only the external halt filter, allowing a halted TBU to issue its debug
request. Architectural context-bank ATS remains separate and unchanged.

Successful ECATS PAR is the actual complete byte PA shifted left by 12,
matching the pinned driver's decode. It is not architectural CB PAR
copied verbatim and is not an identity mapping shortcut. Translation
failures retain fault result 1 and the core's actual fault effects.
A valid core PA outside the ECATS 36-bit output range also returns fault 1,
without truncating it into success or inventing a core FSR bit.

Clearing TRIGGER cancels pending ECATS, clears captured state and returns
PAR to 1, while preserving semaphore ownership and halt state. Dropping
HALT during a pending translation fails unchanged; software must first
complete or cancel the operation. A completed trigger must still be
cleared before reuse.

## Observation, reset and migration

All runtime QOM properties are read-only and have no timer, acquire or
translation side effects. Global observations are
`tcu-base`, `tbu-count`, `ecats-owner` (int32, -1 means empty) and
`dma-blocked-mask`. Each `tbuN-` prefix exposes base, status-address,
sid-start, halt-sid, va, trigger, axuser, par, status, halt-pending, halted,
ecats-pending, halt-deadline-ns, ecats-deadline-ns, captured-sid,
captured-va and captured-write. Inactive deadlines are zero.

Reset entry cancels the timer, discards all requests/ownership and
restores the reference initial state. Reset hold publishes the cleared
DMA gate and invalidates actual cache clients. No input clock, regulator
or bootloader context is silently programmed.

VMState `qcom-smmuv500` is version 1/minimum 1. Before runtime data it
checks immutable profile, reference version, physical TCU base and TBU
count. One parent section contains owner and all ten TBU states, avoiding
cross-section arbitration dependencies. Pre-load stops the timer and
temporarily blocks reference DMA. Post-load validates masks, reachable
trigger/capture relationships, ownership and pending deadlines; it then
restores the blocker, sends real UNMAP notifications and rearms the
original earliest virtual deadline. It does not execute a translation or
advance virtual time during load. No first MMIO read is required to make
the restored state effective.

Same-binary migrations require matching reference assumptions and core
geometry. Both profile mismatch directions reject. Historical binaries
without the companion are outside this compatibility claim. QTest's
virtual time origin is not migrated; tests must align the target clock
before restoring absolute pending deadlines.

## QTest cache witness and limits of native progress

Normal TCG has no cache-fixture window or properties. Under qtest only,
`0x51000000 + 16 MiB` is a RAM-target DMA cache client for SID 0x42.
It calls the real IOMMU translator on a miss and deliberately reuses the
cached entry on a hit. Its actual UNMAP callback invalidates that entry.
Read-only `qtest-cache-valid`, `qtest-cache-hits`,
`qtest-cache-fills` and `qtest-cache-unmaps` distinguish a real
cached access from an uncached repeated read. These diagnostic counters
and cache contents are not migration payload; architectural state is.
The original `0x50000000` uncached DMA alias is unchanged.

The original DT uses `qcom,skip-init`. In the pinned reset function
that skips stream/context initialization, but global FSR clearing, TLB
invalidation and sCR0 programming still execute. This model does not
reconstruct firmware's unknown stream/context handoff. A later native
probe passing VERSION therefore does not establish native DMA readiness.

Power-domain readiness, unresolved clocks, hardware stall queues, timing
fidelity, test/capture buses, secure-world state, stage-2/nested walks and
GPU execution remain outside this companion. Existing architectural
stage-1 restrictions continue to fail through the original walker.
Acceptance must distinguish compiled-device QTests and real migrations
from observed original-kernel behavior; neither proves Android boot.

## Actual 3ef98 validation

Commit `3ef98a8afb49a873d677288693d6491c50fd75e1` passed the complete 71-gate plan and the separate A77/UFS job. Its [original component report](../evidence/wholeboard-20261123-a47/smmuv500-3ef98.json) passed 36 checks, two complete migrations, two profile rejections and two deliberately corrupted-stream rejections. The [artifact audit](../evidence/wholeboard-20261123-a47/public-ci-3ef98.json) independently decoded all ten states in both actual 744-byte device sections and hashed all five source/corruption streams. The unknown-profile source supports a rejection test and is not an additional roundtrip.

The [matching native run](../evidence/wholeboard-20261123-a47/native-3ef98.json) passed the TCU VERSION fault. It logged the four MNOC regulator consumer links and ten subsequent bus resource clients, then continued through SCSI/USB initialization before a new TSENS external abort. Stopped QOM observed all ten TBU geometries correctly with no active halt, owner or ECATS request; PAR remained the initial fault value. This is consistent with initialization without debug translation. It does not prove original-firmware context handoff, native DMA or full Android boot. The next actual read fault is `0x0c222004` in `__tsens2xxx_hw_init+0x10`.

## Concurrent core v3 integration

The [reviewed three-way merge](../evidence/wholeboard-20261123-a47/smmuv2-granule-merge-review.json)
retains the core/companion API, HALT filter, shared stage-1 walker and real cache
notifications while adding explicit architectural granules. Companion VMState
remains v1/744 bytes. Its existing migration checks additionally require the
two actual core sections to be v3, with their board geometry and default
mask-1 profile. The new granule gate and the unchanged 36-check companion
semantics require a fresh combined CI result; previous core-v2 receipts are
not relabeled as core-v3 execution evidence. This does not add native DMA,
optional-granule native capability or full-board migration claims.
