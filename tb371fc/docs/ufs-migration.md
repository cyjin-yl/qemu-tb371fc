# SM8250 UFS quiescent checkpoints

This change replaces the QCOM wrapper's unconditional migration blocker with
a bounded, versioned checkpoint format. The complete runtime gate passed at
`03cd8843de48b55de6ef6aa9ee28255052f8b2c8`: **21 check groups, three completed
source/receiver migrations and seven rejection scenarios**. The
[recorded validation](#recorded-validation) below retains the raw receipts and
binds them to the tested source tree and binaries.

The generic `ufs` PCI and `ufs-sysbus` devices remain unmigratable. Enabling this
format does not establish a whole-board boot or migration claim.

## Boundary

The source must have empty legacy transfer and task-management doorbells, no
non-idle UFS request or outstanding SCSI request, no reset in progress and no
MCQ configuration. A failed source check reports the actual reason without
cancelling or destroying the source's requests. A block request which finishes
during QEMU's normal drain may become eligible; this is not in-flight request
serialization. A permanently blocked backend can still prevent the generic
block drain from finishing before this device's pre-save check is reached.

UIC and PHY control timers are not storage requests. Their remaining virtual
time, pending commands, link states and completion interrupts are preserved.
Hibern8 may retain the link with the PHY powered off, as the existing model
already permits. Symbol clocks and the UFS output interrupt are reconstructed
after validated state is loaded.

The medium is external. Source and receiver must use the same contents, LUN
numbers, capacities and read/write permissions. This implementation does not
copy, hash or attest disk contents. The process test completes the source
checkpoint, exits that process, and only then launches a receiver using the
same disk path with ordinary file locking. There is never a pair of active
writers and the test does not disable block file locking.

## VMState v1

The format has no compatibility promise with older QCOM binaries, which could
not save this device at all. It is registered only by `qcom-sm8250-ufs`.

| State | Representation and constraint |
| --- | --- |
| Hardware/HCI versions and reference symbol rate | Explicit equality fields; destination configuration is not overwritten. |
| Serial, queue counts, WB configuration, device/geometry descriptors and every LUN | SHA-256 of length-delimited/canonical fields, packed wire descriptors, each backend size and read-only setting; receiver must match. |
| Legacy HCI registers | Individual native integers encoded by VMState, including reserved arrays; capability and identity registers are equality checked. |
| Query attributes and flags | Field-by-field encoding; multibyte attributes retain their big-endian wire bytes. Native struct padding is never serialized. |
| WB/HID and idle timer | Counters, state and virtual-RT deadline; incoming arithmetic bounds and state enums are checked before continuation. |
| Vendor registers, PHY programming and DME | Fixed-size arrays and typed fields; PHY ranges, writable DME IDs/selectors/values and duplicate entries are validated. |
| Link, clocks and timers | Input clock periods, power/reset inputs, link state and virtual deadlines; derived symbol clocks and IRQ are reconstructed. |
| UFS request and SCSI queues | Not serialized by the wrapper; must be empty. The existing SCSI child migrates sense/unit-attention state. A nonempty incoming SCSI queue is rejected before enqueueing. |
| Backend media bytes | External shared-storage requirement; not in this device stream. |

The small change to the SCSI loader propagates an HBA `load_request` error
before request-specific loading or enqueueing. The UFS callback uses that
existing error channel to reject a nonempty incoming queue, which otherwise
has no migrated `UfsRequest` to associate with `hba_private`.

## Reproducible gate

```sh
python3 tb371fc/tests/test_ufs_migration.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --out /absolute/path/new-empty-result-directory
```

The dedicated workflow step uses the same binary as the public component and
Linux gates. It runs three actual QMP file checkpoint transfers and seven
rejection scenarios, with at most one 128 MiB QEMU process at a time:

- Active link, a partially elapsed UIC command and a pending transfer IRQ;
  receiver checks the original completion deadline, W1C behavior, DME state,
  Query values, flags, WB accounting and SCSI sense.
- Hibern8 with the PHY off; data doorbells remain rejected after restore.
- A partially elapsed PHY-ready timer; receiver verifies its deadline, symbol
  propagation, link exit and continued DMA and backing-file I/O.
- Source and receivers use nonidentity stage-1 translations for actual UTRD,
  UPIU and data DMA on UFS SID `0xe0`. Writes are flushed and compared to the
  host file; resumed reads check exact payloads.
- Controller version profile, transfer-slot count and LUN capacity mismatches reject
  the receiver. Three additional quiescent source captures provide each proposed
  receiver's actual immutable prefix; they are not counted as successful
  transfers. The gate identifies the differing field from these real streams
  and requires its exact VMState diagnostic. Corrupt doorbell, WB bound and
  SCSI queue streams also reject. Every rejected receiver must exit 1 without
  changing its medium; QMP is drained to EOF after exit before checking for
  any RESUME event, including one queued after the command response.
- A real unmapped UTRD produces an unfinished source DMA request and a failed
  checkpoint. The test then clears its SMMU fault, resets HCE and continues
  actual I/O on that same source.

Every process command, exit status, QMP migration result, stream hash and log
hash is retained in `result.json`. Any missing scenario, failed assertion or
changed binary fails the test. A sent stream alone is never a receiver pass.
QTest's external clock is not part of migrated device state. As in the existing
MMIO timer migration test, the receiver explicitly aligns that test fixture to
the recorded source timestamp before checking remaining deadlines. QTest
clock steps briefly resume the VM and return it to its prior paused state.

## Recorded validation

### Profile and QMP-drain follow-up

The stricter quiescent UFS gate passed at exact source commit
`839ae46791ecf9db3bc6f622fd69b4ffd4496f6c`:
[21 groups](../evidence/ufs-migration-20261123/verified-839ae467/ufs-migration-result.json),
three actual process transfers, seven rejection scenarios and three additional
profile captures. All six rejected receivers were drained to QMP EOF before
the no-RESUME check, closing the earlier fixture's gap after the command reply.
The same unchanged QEMU binary passed
[55 component gates](../evidence/ufs-migration-20261123/verified-839ae467/components-result.json)
and [four Linux gates](../evidence/ufs-migration-20261123/verified-839ae467/linux-result.json).
The [artifact/source binding](../evidence/ufs-migration-20261123/verified-839ae467/source-binding.json)
records every hash and the separately pending A77/PCI job at collection; the
full workflow is not described as complete from the board job alone.

### Original UFS acceptance

[CI run 45260760828](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45260760828)
completed successfully. It checked out merge commit
`48246223abcaaabbc3a9b33cbdca2c2a40dc8ecf`, whose tree
`ce17eabc680d6eef5f9984530e541e544623a948` is identical to implementation head
`03cd8843de48b55de6ef6aa9ee28255052f8b2c8`.

| Gate | Actual result | Unmodified receipt |
| --- | --- | --- |
| UFS process checkpoints | 21/21 groups, three completed transfers, seven rejections | [UFS result](../evidence/ufs-migration-20261123/accepted-03cd8843/ufs-migration/result.json) |
| Existing public components | 55/55 gates; C QTests include 23 SMMUv2, 25 TLMM and 18 PDC cases with no skips | [Component result](../evidence/ufs-migration-20261123/accepted-03cd8843/components/result.json) |
| Public unpatched Linux | 4/4 boots/driver gates, including UFS with SMMU and RPMh | [Linux result](../evidence/ufs-migration-20261123/accepted-03cd8843/linux/result.json) |
| Shared PCI UFS regression | 15/15 cases with no skips | [PCI result](../evidence/ufs-migration-20261123/accepted-03cd8843/ufs-pci/result.json) |
| Isolated architectural A77 | 852 guest checks across nine configurations and two invalid-configuration rejections | [A77 result](../evidence/ufs-migration-20261123/accepted-03cd8843/a77/result.json) |

The [source/artifact binding](../evidence/ufs-migration-20261123/accepted-03cd8843/source-binding.json)
records both archive digests, tested QEMU/QTest binaries, test/helper hashes,
the Linux image hash and hashes of every retained file. The downloaded archives,
binary contents, successful migration streams, process stderr, all 909 component
and Linux manifest entries, A77 payload/log hashes and PCI manifest entries were
checked against their original receipts. Process stderr and PCI/A77 logs are
retained beside the reports.

The six invalid incoming streams/configurations exited with status 1, before
resume and without changing their media. The incoming SCSI-queue case emitted
the UFS-specific rejection hook diagnostic. The seventh rejection came from an
actual unfinished UTRD DMA request on the source; its migration failed with the
quiescent-queue diagnostic while the request remained visible. HCE reset and
fault clearing then restored actual I/O on that same source.

### Earlier attempts

The first executed gate at `c0ebdb7` passed source DMA/I/O but failed before
sending a migration command, because QTest rejects `clock_step 0`. Its unmodified
[failure report](../evidence/ufs-migration-20261123/first-attempt-c0ebdb7/result.json)
and [binding](../evidence/ufs-migration-20261123/first-attempt-c0ebdb7/source-binding.json)
are retained. The fixture now caches the actual timestamp returned by each
successful nonzero step and uses it to align the receiver.

The next gate at `b2a9131` completed three migrations and 15 groups. It correctly
rejected the LUN-capacity mismatch, but the generic byte-equality diagnostic
omitted the field name required by the fixture. Later rejection cases had not
run. Its unmodified [partial report](../evidence/ufs-migration-20261123/second-attempt-b2a9131/result.json)
and [binding](../evidence/ufs-migration-20261123/second-attempt-b2a9131/source-binding.json)
are retained. The final configuration comparison reports an explicit
`SM8250 UFS migration: configuration mismatch`, preserving the same 32 wire
bytes and equality requirement.

## Fixed implementation sources

This work extends QEMU's existing software contract; it does not infer a new
silicon feature from a reference-model test. Relevant baseline source at
`69ca28dec6add998d252fa6436dfcc9b9cab6e36`:

- [QEMU device migration guidance](https://github.com/cyjin-yl/qemu-tb371fc/blob/69ca28dec6add998d252fa6436dfcc9b9cab6e36/docs/devel/migration/main.rst)
- [UFS transport, background state and reset](https://github.com/cyjin-yl/qemu-tb371fc/blob/69ca28dec6add998d252fa6436dfcc9b9cab6e36/hw/ufs/ufs.c)
- [UFS logical units and virtual SCSI children](https://github.com/cyjin-yl/qemu-tb371fc/blob/69ca28dec6add998d252fa6436dfcc9b9cab6e36/hw/ufs/lu.c)
- [SCSI migration request loading](https://github.com/cyjin-yl/qemu-tb371fc/blob/69ca28dec6add998d252fa6436dfcc9b9cab6e36/hw/scsi/scsi-bus.c)
- [QCOM UFS digital wrapper](https://github.com/cyjin-yl/qemu-tb371fc/blob/69ca28dec6add998d252fa6436dfcc9b9cab6e36/hw/ufs/qcom-sm8250-ufs.c)

The existing [UFS model notes](ufs-sm8250.md) retain the fixed Linux driver
references and hardware-equivalence limits. This checkpoint gate does not
cover ICE, RPMB, analog PHY behavior, stock Android, a native-DT boot, an XNU
boot or arbitrary active storage queues.

