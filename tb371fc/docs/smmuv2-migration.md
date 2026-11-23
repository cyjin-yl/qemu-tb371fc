# SMMUv2 process migration and configuration compatibility

The SMMUv2 gate transfers state between distinct paused QEMU processes. It
checks actual RAM page tables, translated DMA through the apps test-only SID
`0x42`, independent context faults, IRQs and continuation on the receiver.
It uses `-M tb371fc,ufs-enabled=off -m 128M -smp 8`; it is a component gate,
not a whole-board migration or Android boot claim.

The [retained after report](../evidence/smmuv2-migration-20261123/after.json)
passes all **18 checks** in CT102 on the unchanged CI-built executable. The
[publication record](../evidence/smmuv2-migration-20261123/publication.json)
binds that executable, its CI source revision, the diagnostic-corrected test
revision, and the actual before/after verdicts.

## Bug and migration format

VMState version 1 stored the register arrays but omitted `mmio-size`,
`num-context-banks` and `num-smrs`. A receiver with a different stream-match
capacity could accept the state and expose different capability registers;
part of the sender's mapping table could cease to participate in translation.

The retained pre-fix run used frozen build 12, SHA-256
`178bace28c84bced9471b824d413feb18e76ddb15212ec32412ad7a7ec32a675`.
Its 16 same-configuration restoration checks passed, but the 128-to-64 SMR
negative case failed: both sender and receiver reported migration `completed`.
This is a configuration compatibility defect, not a failed same-configuration
page-table transfer. The [original failing report](../evidence/smmuv2-migration-20261123/before.json)
is retained.

Version 2 serializes all three geometry fields using equality checks before
loading the register arrays. Both directions of 64/128 SMR mismatch must be
rejected by the destination with an SMMUv2 load error identifying the expected
and incoming capacities (`40 != 80` or `80 != 40`, in hexadecimal). The existing version
1 stream cannot establish the geometry contract and is intentionally rejected;
old snapshots are not accepted as compatible with this version.

The first CI run already rejected the incompatible receiver, but failed because
the test expected a field-name diagnostic that this QEMU version does not emit.
That [failed CI report](../evidence/smmuv2-migration-20261123/first-ci-diagnostic-failure.json)
is preserved. The corrected assertion checks the SMMUv2 device and the observed
capacity pair while still requiring a nonzero receiver exit; it does not accept
a sender-only success or arbitrary receiver failure.

## Reproduce

Build the branch with the normal dedicated-machine build command, then use a
fresh, short output path:

```sh
python3 tb371fc/tests/test_smmuv2_migration.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/smmu-migration
```

The script preserves a structured `result.json`, each process's stderr, the
complete register snapshot, QMP migration results, the executable SHA-256 and
test/helper hashes. The tested binary must remain unchanged. A failure returns
nonzero, records the traceback and preserves earlier successful observations.
The public component runner requires all 18 checks; `--geometry-only` runs the
two rejection cases for focused diagnosis and does not satisfy the full gate.

## Checked behavior

| Transfer | Observations |
| --- | --- |
| Real GIC wiring | Apps global fault, contexts 0 and 95, and masked GPU context 7 retain their independent registers and interrupt states. The GIC path is never intercepted in this transfer. |
| DMA and page tables | Source and destination actually read/write through the apps SID alias; a retained context fault blocks writes until W1C, then accesses reach the preserved physical RAM. |
| GPU register/ATS state | The independent GPU aperture retains TTBR, TCR, MAIR, PAR and a masked architectural FSRRESTORE fixture. ATS reads succeed and writes to a read-only page fail; ATS failure does not create a client IRQ. |
| Destination continuation | Replacing a leaf and issuing TLBI makes subsequent walks use the new physical page. Reset clears mappings/faults, preserves RAM, and allows reconfiguration and DMA to resume. |
| Separate output wires | A second transfer intercepts apps SMMU outputs and checks global/configuration/context IRQ restoration and independent clearing. It makes no simultaneous GIC claim. |
| Geometry rejection | Two separate receiver processes reject 128-to-64 and 64-to-128 SMR capacity transfers. |

FSR bit 10 is the AArch64 format annotation. Fault assertions mask the
architected fault bits; complete FSR values remain in the migration snapshot.
The GPU masked-fault fixture uses the architectural FSRRESTORE register, and
the separate configuration-fault wire uses GFSRRESTORE. They are synthetic
controller-state inputs, not claims about a real GPU or firmware fault.

## Remaining limits

UFS is disabled because its core still blocks migration. These tests do not
establish whole-board migration with storage I/O, a running Android userspace,
GPU command execution/rendering, secure state or native silicon behavior.
The walker has no internal IOTLB. The TLBI check proves later page walks see
the replacement table, but does not register an external DMA client's cache
notifier or establish its eviction behavior. Matching virtual geometry is a
necessary compatibility check, not proof of every peripheral configuration.
Runtime mismatch cases cover SMR capacity in both directions. The aperture
and context-bank fields use the same equality primitive, but do not have
separate negative runtime cases in this suite; the version-1 rejection contract
also is not counted as a separately executed legacy-stream test.

The test contract is public and registered in CI. Actual pass/fail evidence
belongs to a specific run and binary; workflow registration alone is not a
passing result.
