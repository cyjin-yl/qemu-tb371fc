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

[Automatic Hibern8](ufs-auto-hibern8.md) additionally saves its idle deadline
and automatic-sleep ownership in a versioned subsection, while retaining this
v1 main layout. CAP.AUTOH8 profile mismatches remain rejected; the explicit-only
`auto-hibern8=off` profile is available for older v1 streams.

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

## Real Linux driver continuation gate

The separate `boot_ufs_migration.py` gate exercises the real Linux drivers across
one stopped checkpoint. It requires the matching `build.json` for the public,
unpatched Linux 6.12.114 Image produced by `build_smoke_kernel.sh`; the source
archive, Image and receipt are checked before QEMU starts.
The smoke configuration explicitly enables `CONFIG_PROC_SYSCTL=y` and its build
gate checks that setting, so `/proc/sys/kernel/random/boot_id` is available for
the continuity assertion. Earlier minimal smoke kernels disabled that setting
and cannot satisfy this new guest requirement.

```sh
python3 tb371fc/scripts/boot_ufs_migration.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --kernel /absolute/path/clean-linux/artifacts/Image \
  --out /absolute/path/new-linux-migration-results
```

The gate uses eight CPUs, 8 GiB RAM and a newly created 64 MiB synthetic LUN.
Its PID 1 verifies the qcom UFS, QMP PHY, GCC and apps SMMU bindings, the `DMA`
IOMMU domain, capacity and 4 KiB sectors. It completes an aligned 32 KiB
`O_DIRECT` write at 4 MiB, `fsync`, readback and close before acknowledging the
checkpoint. The host sends a random nonce once over GENI RX; the guest keeps
that value, its boot ID, PID, monotonic timestamp and check counter in RAM.

After a successful QMP `stop`, a received STOP event and a paused status, the
host verifies **every byte of the disk**, saves the checkpoint, requires
`completed` and `postmigrate`, and exits the source process. Only then does it
start the receiver with `-incoming defer -S`, the same media and ordinary file
locking. The receiver must finish loading while paused before an explicit
`cont`. Its sole serial input is `CONTINUE`; the nonce is never resent or
placed in the guest command line, initramfs or disk.

The resumed PID 1 must retain its original nonce, boot ID, monotonic time and
counter. It verifies the translated domain again, reads the source payload,
writes a different aligned 32 KiB payload at 8 MiB, flushes it, and checks both
payloads again after close/reopen. The host requires exactly **21 source and
18 continuation checks**, all passing in the expected order. Separate serial
logs must contain one source boot and no target boot or replayed source phase.
It then checks the entire 64 MiB image against the two payloads and zeros
everywhere else. The migration stream hash must be unchanged across loading,
and the QEMU, kernel, receipt, guest source, harness, init and initramfs hashes
must remain unchanged from before source startup through receiver completion.

The workflow runs this gate in addition to all existing component, four Linux,
21-group QTest migration, PCI and CPU gates. `result.json`, per-process receipts,
commands, QMP messages, serial/debug logs, public kernel receipt and hashes of
the runnable inputs are retained even on failure. Read the receipt for the
actual verdict; the measured run below binds it to the tested source.
The CI upload excludes the temporary migration stream and synthetic disk;
their exact sizes, SHA-256 values and whole-media comparison results remain
in the receipt. The public kernel, QEMU binary, init and initramfs are retained.

This is one quiescent Linux UFS continuation on a generated device tree.
In-flight UFS requests, a changed external disk, a native device-tree boot,
stock Android, power-cycle persistence and full-device migration equivalence
remain outside the result. The host ends the successful receiver with QMP;
guest shutdown is not tested.

### Runtime-suspended Linux continuation

The same gate has an additional `--runtime-suspended` mode. It reuses the
independent [runtime-PM fixture](../scripts/boot_ufs_runtime_pm.py)'s pinned
Linux Image/config validation and read-only QMP observations. This mode tests
an additional combination; the historical acceptance below covers the
original active-controller mode only. A new run's `result.json` is authoritative
for whether the suspended mode actually passed.

```sh
python3 tb371fc/scripts/boot_ufs_migration.py \
  --runtime-suspended \
  --qemu /absolute/path/qemu-system-aarch64 \
  --kernel /absolute/path/clean-linux/artifacts/Image \
  --out /absolute/path/new-suspended-migration-results
```

The source PID 1 completes the original payload/nonce preparation, opens the
synthetic LUN and keeps its descriptor open. Linux enables SCSI autosuspend
through the normal sysfs interfaces, waits for the host, LUN and device-WLUN
to suspend, and requires the suspended-time counter to advance while idle.
Before stopping the source, read-only host observations require Hibern8 and
zero core/iface/unipro clocks while the VM itself is still running. The same
sleep state is checked after the VM pauses.

The source checkpoint is completed and its process exits before the receiver
opens the ordinary locked disk file. After loading while paused, the receiver
must retain all observed UFS input clocks and Hibern8. It must still be asleep
after `cont`, before receiving the sole `CONTINUE` serial command. The resumed
PID 1 checks its original boot ID, nonce, counters and descriptor's block-device
identity, then checks host/LUN/device-WLUN runtime-suspended state and preserved
suspended-time accounting. An `O_DIRECT` read on that retained descriptor must
wake Linux's UFS controller and return the original payload. The receiver does
not write `power/control=on`, reopen the block device or inject device state to
perform this wake. It then completes the original write/read/reopen sequence.

This adds **13 source and 9 receiver checks** to the existing 39: successful
suspended-mode runs require **34 source plus 27 receiver checks** in exact
order. The source/receiver logs remain separate; every byte of the 64 MiB
medium is checked before and after migration. Source, harness, imported Python
helpers, kernel/config/receipt, compiled init, initramfs and QEMU hashes are
checked for changes. Raw QMP observations, commands, exit codes and failures
are retained. CI runs both modes and excludes both temporary media and migration
streams from its uploaded artifacts, retaining their size and hash receipts.

The queues are still quiescent. These checks do not implement in-flight SCSI
or MCQ serialization, system suspend, external-disk replication, ICE, physical
power behavior or native tablet Linux acceptance. No physical tablet is used.

### CNTHP event restoration

The first suspended-mode run at `3ed38f3d7fa04e974e5fe904f8771e4b8c5252ec`
([run 45370015290](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45370015290))
passed all 34 source checks and the first 11 receiver checks, then timed out on
the first ordinary direct read. The source Linux log reports VHE and CPUs
started at EL2. Linux 6.12.114 selects the hypervisor physical timer in this
configuration: its CNTP accesses are redirected to CNTHP.

The CPU migration stream preserved CNTHP's compare/control registers through
the cpreg list, but only serialized the CNTP and CNTV `QEMUTimer` objects.
Raw cpreg restoration does not re-arm CNTHP. A receiver could therefore run
until it needed a scheduler tick, then sleep with no CNTHP event to wake it.
The new `cpu/gt-hyp-timer` version 1 subsection preserves that pending event
using the same timer serializer as CNTP/CNTV. It does not write guest registers,
inject an interrupt or change the UFS wake path.

The subsection is emitted only when the CNTHP timer object exists and has a
pending event. Disabled/unarmed timers add no data. Armed masked timers and
already-expired timers retain the existing timer deadline and architectural
control state; interrupt mask handling remains in the existing timer callback.
An older receiver rejects an unknown active-timer subsection. Streams emitted
by the earlier implementation with an active CNTHP timer lack that event and
are outside this fix's acceptance boundary; the Linux gate requires identical
source and receiver QEMU binaries.

The existing CPU timer migration probe now also arms CNTHP and observes its
actual GIC redistributor input, INTID 28 on this board. It requires the line to
be clear before the checkpoint, asserted after the migrated compare deadline,
then clear after the guest disables CNTHP. It does not read CNTHP CTL or write
CNTHP between restore and expiry: an exact-mode CTL read recalculates a timer
and could hide a missing event. The identical ELF also runs without migration
as a control. The original physical/virtual checks, all original Linux checks
and their host timeouts remain mandatory. A successful new CI result is still
required before claiming that the suspended Linux continuation passes.

### Recorded Linux continuation acceptance

[Run 45312366652](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45312366652)
completed successfully on **2026-11-24 at 02:44:38 UTC**, for code head
`6d9d834397720318a51f070977df985d9beb7443`. GitHub actually checked out merge
`9d69a45f18ecac42c65a3aad2fa11da431a61cd7`, whose other parent was the already
merged documentation head `8babe85b1cd7990a1a113b223d51ec8a7732fed5`.
The [source binding](../evidence/linux-ufs-migration-20261124/verified-6d9d8343/source-binding.json)
records both trees, the five runtime/config/workflow Git blobs, input hashes,
artifact identities, independent review and the complete evidence manifest.

| Measured gate in this run | Actual result |
| --- | --- |
| New real Linux UFS continuation | **39/39 guest checks**: 21 source, 18 receiver; one completed source/receiver pair |
| Existing public components | **55/55** |
| Existing clean Linux boots | **4/4** |
| Existing UFS QTest migration | **21/21 groups**, three transfers, seven rejection scenarios and three additional profile captures |
| Shared PCI UFS regression | **15/15**, no skips |
| Separate Cortex-A77 configuration | **852 guest checks** across nine variants, plus two rejected invalid configurations |

The [raw continuation result](../evidence/linux-ufs-migration-20261124/verified-6d9d8343/linux-ufs-migration-result.json)
and separate source/receiver serial and QMP receipts retain the original
nonce and boot ID. Both QEMU processes exited zero, the source exited before
the receiver started, and the receiver completed loading while paused before
its explicit `cont`. The source received one `STORE` command and the receiver
received only `CONTINUE`. Both complete 64 MiB comparisons passed, including
every unwritten byte; all seven immutable inputs kept the same hashes.

The ordinary Linux/QTest/component gates and this new continuation used the
same board binary, SHA-256
`f4b02d882acb0e83a90ea258ac61689a77690728e03bfd22b5f37ab35f108a3b`.
The public unpatched kernel was SHA-256
`f4e00305947da98b7ed38d66ab46411b35fc3f317cf462233f0be17d10257299`;
the archived resolved configuration confirms both `PROC_SYSCTL` and its selected
`SYSCTL` dependency are enabled. The separate A77/PCI binary has its own hash
in the binding and raw results.

The 190,152,799-byte temporary checkpoint and the 64 MiB synthetic medium were
verified absent from the downloaded CI ZIP. Their size/hash receipts remain
in Git alongside the actual logs and results. The public runnable binaries
remain in the separately identified CI artifacts, subject to their recorded
retention period. The two earlier revisions were superseded before this new
Linux gate ran: one addressed review of artifact scope and input identity,
and one enabled the necessary boot-ID interface discovered in the actual
previous kernel configuration. They are recorded without counting them as
runtime passes or failures. This evidence/doc update leaves the tested runtime
files unchanged and does not widen the quiescent-only boundary above.

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

The six invalid incoming streams/configurations exited with status 1 without
changing their media. The original fixture inspected QMP events read while
awaiting the command response; it did not drain the exited receiver's QMP stream
to EOF. This receipt therefore does not establish a complete no-RESUME event
audit. The incoming SCSI-queue case emitted
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

