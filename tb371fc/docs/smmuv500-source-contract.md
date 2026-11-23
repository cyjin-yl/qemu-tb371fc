# QSMMUv500 source and actual DT contract

This is source and DT research, not a completed device model or a native boot pass. No QEMU source, CI registry, branch or existing device state was changed for this note.

## Evidence identity

The reported native run used the passing 99ef6052e3e10e84c67f3482471f4bde12ab295a CI binary. Root observed the first fatal read at physical 0x15182018, size 4, in qsmmuv500_arch_init+0x88/0x238 at guest 2.939821 seconds. The direct physical access was supplied by root, independently of the source correlation below.

The DT was read once, without guest memory access or hardware MMIO, from the existing private input. Its SHA256 is 5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207 and size is 973052 bytes. Only two matching SMMU nodes, their twelve TBU children, parent addressing context and narrowly selected properties of referenced providers were exported. The public JSON slice is Git blob **8306d244d2f785ce0efb8c2071b8badc77b252e7**, 46375 bytes, SHA256 **23e8ca465e6b9bb6244eea78e824f7afb0632ff30c5a6574b1090a22989ad34b**. Property bytes/BE32 cells are authoritative; string decoding was limited to named string properties.

All kernel sources below were actually read at official OnePlusOSS/android_kernel_oneplus_sm8250 commit **94304e12a3509eac7b3716bf574ce0b1fcaacbae**:

| Path under drivers/iommu | Bytes | SHA256 |
| --- | ---: | --- |
| arm-smmu.c | 175613 | ccc4b09010c420ea42c9838d397b0b81fe6a0a4e345e95cb935c2e70308fbf49 |
| arm-smmu-regs.h | 8154 | 0cf3ca3b4b0a57f1b68e08b989e99bd0037f349247741cd8166d0829ac83356e |
| arm-smmu-debug.c | 8149 | a6bfba9da8c7ee08aeeeda0281090757c6dd7fc01fa61509f8cde938d3ec8a80 |
| arm-smmu-debug.h | 4461 | a26b40d1afbff4f334175ea3cd91663c78b4bd3d10a2711e6b7c9c499bce3c83 |

Canonical source:
https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/iommu/arm-smmu.c

The fixed repository's Documentation/devicetree/bindings is a symlink to ../../arch/arm64/boot/dts/vendor/bindings; that target was not available through the requested fixed repository paths. This note does not claim to have read a binding document from that pin. Nor has the private production Image been rebuilt from this source: function-level correspondence remains source-correlated evidence.

## Actual geometry

Both parent nodes have compatible qcom,qsmmu-v500, reg-names base/tcu-base, #iommu-cells=2, #address-cells=1, #size-cells=1 and empty ranges. Neither has enable-static-cb, disable-atos, testbus-version or a hardware-version property. Both have qcom,skip-init and qcom,use-3-lvl-tables. KGSL additionally has qcom,no-dynamic-asid.

| Instance | Main resource | TCU resource | IRQ resources |
| --- | --- | --- | --- |
| apps /soc/apps-smmu@15000000 | 0x15000000 + 0x100000 | 0x15182000 + 0x20 | 2 global + 96 context entries |
| KGSL /soc/kgsl-smmu@3da0000 | 0x03da0000 + 0x10000 | 0x03dc2000 + 0x20 | 2 global + 8 context entries |

Each TBU is qcom,qsmmuv500-tbu, names its resources base/status-reg and gives qcom,stream-id-range as start/count. All twelve lack capture-bus interrupt properties and opt-out-tbu-halting.

| Apps index | TBU | Base (size 0x1000) | Status (size 8) | SID start/count |
| ---: | --- | --- | --- | --- |
| 0 | anoc_1 | 0x15185000 | 0x15182200 | 0x0000 / 0x400 |
| 1 | anoc_2 | 0x15189000 | 0x15182208 | 0x0400 / 0x400 |
| 2 | mnoc_hf_0 | 0x1518d000 | 0x15182210 | 0x0800 / 0x400 |
| 3 | mnoc_hf_1 | 0x15191000 | 0x15182218 | 0x0c00 / 0x400 |
| 4 | compute_dsp_1 | 0x15195000 | 0x15182220 | 0x1000 / 0x400 |
| 5 | compute_dsp_0 | 0x15199000 | 0x15182228 | 0x1400 / 0x400 |
| 6 | adsp | 0x1519d000 | 0x15182230 | 0x1800 / 0x400 |
| 7 | anoc_1_pcie | 0x151a1000 | 0x15182238 | 0x1c00 / 0x400 |
| 8 | mnoc_sf_0 | 0x151a5000 | 0x15182240 | 0x2000 / 0x400 |
| 9 | mnoc_sf_1 | 0x151a9000 | 0x15182248 | 0x2400 / 0x400 |

KGSL gfx_0/gfx_1 bases are 0x03dc5000/0x03dc9000; status resources 0x03dc2200/0x03dc2208; SID starts 0/0x400 and counts 0x400.

DT resource size does not establish that every byte is implemented. In particular status-reg is eight bytes in DT, but this driver only reads/writes its low 32-bit word.

The apps main node has no clock or supply property. Apps mnoc_hf_0/hf_1/sf_0/sf_1 TBUs reference GDSC providers at 0x0017d050/58/54/6c respectively. The PCIe TBU references GCC clock ID 3. KGSL references GCC IDs 38/39, GPUCC ID 0 and GPU CX GDSC 0x03d9106c. These references are dependencies, not proof that every provider currently works; do not force unknown clock or power inputs on.

## Actual probe path and later consumers

arm-smmu.c lines 6446-6509 perform:

1. Map the named tcu-base and read its 32-bit +0x18 VERSION.
2. Read optional qcom,testbus-version (zero-initialized when absent) and create TCU debugfs files.
3. Return early only for static context-bank mode, which is absent in this DT.
4. Read qcom,actlr triples and clear main GR0 sACR +0x10 CACHE_LOCK bit 26 with an RMW/readback.
5. Populate TBU children and require their probes to have bound; otherwise defer the parent.

The TBU probe at lines 6524-6594 maps base/status-reg, parses SID range, optionally requests capture IRQs, acquires power resources and creates debugfs files. The debugfs constructors themselves do not access debug hardware. Thus normal probe does not exercise HALT, ECATS, testbus or capturebus merely by creating these objects.

Generic caller lines 5087-5255 subsequently registers the IOMMU and performs reset/TLB/SMR work. qcom,skip-init skips the initial stream/context-bank clearing inside reset; it does not skip VERSION, sACR, FSR clearing, global TLB invalidation or sCR0 configuration. The qsmmuv500 arch_ops supplies init, hard address translation, context-bank ACTLR and grouping callbacks; it has no separate vendor device_reset callback.

ACTLR is written at CB +0x4 and followed by a real TLB flush (lines 5792-5813). Actual DT ACTLR values are 0x103 for selected apps SID patterns and 0x32b for selected KGSL patterns. Their complete SID/mask triples remain in the slice; no firmware-programmed context or successful mapping should be synthesized from them.

## TBU maintenance and translation interface

Definitions and consumers are arm-smmu.c lines 5357-5386 and 5414-5745.

| Base-relative word | Driver access | Proven fields/role |
| --- | --- | --- |
| TCU +0x18 | readl | Hardware version; actual device value remains unknown |
| TBU +0x00 SID/HALT | readl/writel and readq/writeq | HALT request bit 16; local SID bits 9:0 |
| TBU +0x08 VA | writeq | Translation request address; driver accepts only IOVA < 2^36 |
| TBU +0x18 TRIGGER | writeq | trigger bit 0, write bit 1, AXCACHE shifted 2, AXPROT shifted 6 |
| TBU +0x20 STATUS | readl | bit 1 HALT_ACK, bit 0 ECATS_RUNNING |
| TBU +0x28 PAR | readq | bit 0 fault; driver decodes PA = (PAR >> 12) & ((1ULL << 36)-1) |
| TBU +0x30 AXUSER | writeq | CDMID bits 43:36; driver writes 255ULL << 36 |
| separate status-reg +0 | readl/writel | acquire loop waits for exactly 1; unlock writes 0 |

Full SID selects a TBU using its DT half-open interval. Only the low ten bits are written into that TBU's SID register. For this DT geometry, a reference implementation must recover the full SID from the owning TBU's start plus those low bits, preserving apps/KGSL separation.

The source's ordinary request is a privileged, nonsecure data read: TRIGGER=0xfd (AXCACHE=0xf, AXPROT=3, read direction). It disables context fault interrupt/stall controls temporarily, clears prior faults, halts the TBU, takes the ECATS lock, programs SID/VA/AXUSER, triggers, polls running or context-fault, reads PAR/FSR, clears state and resumes. The timeout is 100 microseconds; at most two redo attempts follow a zero physical result.

**ECATS PAR must not copy the architectural CB PAR value.** Its driver-visible packing is different. A successful modeled request must use a real existing SMMU lookup/page walk/permission result and encode the resulting byte PA appropriately for the expression above. Unmapped, permission-denied, unsupported-security and unsupported-translation requests must not yield successful identity mappings or a constant successful PAR. Errors need the existing SMMU fault route and status semantics where implemented.

The only revision-specific branch found is version == 0x01000000: version 1.0 avoids accessing status-reg. Every other value uses the read-1/write-0 path. No actual hardware version follows from this comparison. Choosing version 1.0 solely to avoid implementing the lock is not a functional solution.

## Explicit reference-model decisions, not measured hardware facts

For a useful bounded reference model, keep it opt-in and make its chosen identity/profile immutable and migration-checked. A non-v1 reference identity must be labelled as a reference identity, never as a measured TB371FC revision.

The source proves the status read/poll/unlock sequence, but not every hardware arbitration detail. A stateful read-to-acquire lock with explicit owner/busy/release behavior is a defensible reference contract; ownership granularity and repeated-read behavior must be stated and tested, rather than inferred as measured silicon. QOM observations must not acquire or release it.

HALT_ACK should correspond to an implemented quiescent/halted condition. If the model still admits ordinary traffic for that SID while claiming HALT_ACK, it needs a documented and tested supported boundary; a disconnected ACK flag is insufficient. ECATS may proceed through the maintenance translation path while the relevant TBU is halted. Requests must capture their parameters exactly once; rewriting staging registers or repeating trigger must not silently change a pending operation.

If virtual delays are introduced, they are explicit reference timings. Keep running, result/fault, captured parameters, lock owner and all deadlines coherent across cancellation, reset and migration. Restore observations and pending work before the first device MMIO; post_load must not execute a translation using an SMMU whose VMState has not loaded. Either use safe load ordering or defer work until resumed virtual time. Source documents neither silicon reset values nor retention for these registers, so clear/cancel policy must be marked as a reference choice.

Real migration should include idle, locked, halted and pending translation states; profile/geometry mismatch should reject both directions. Use actual stream identity fields rather than destination-only topology assumptions. A saved invalid state must fail instead of silently normalizing to a successful translation.

## Optional debug interfaces and unsupported boundary

With missing qcom,testbus-version, the source uses TBU +0x50 selector / +0x58 output. Testbus version 1 instead selects through TCU +0x8 and reads TBU +0x24. TCU clock testbus select is TCU +0x8; PTW/cache select and output are main SMMU +0x25e4/+0x25e8.

Capturebus uses TBU +0x100/108/110 masks, +0x118/120/128 matches, +0x130 control, eight 64-bit snapshots spanning +0x138 through +0x170, and +0x178 validity. Control defines interrupt clear bit 0 and reset-valid bit 7. Source is arm-smmu-debug.[ch]; no actual capture IRQ exists in this DT slice.

Testbus/capturebus need their own semantic model if supported. Until then they can remain explicit unsupported holes; merely storing selector values and returning zero snapshots would not prove capture functionality. CONFIG_ARM_SMMU_TESTBUS_DUMP can cause diagnostic accesses after faults/timeouts, so its state in the private Image should be recorded separately if such a path occurs. Debug interface existence does not itself make these probe prerequisites.

The fixed source does not establish a complete hardware register map, analog power behavior, real hardware revision, clock rate, reset retention or transaction timing. Passing a future reference test or getting beyond this VERSION abort must not be reported as full native SMMU registration, all-TBU power readiness, physical GPU support or Android boot success.
