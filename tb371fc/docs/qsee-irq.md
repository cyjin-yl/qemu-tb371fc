# QSEE notification interrupt banks

This device implements the two four-bit normal-world notification banks used
by the TB371FC qcom,kona-qsee-irq node. It provides the documented status,
mask and clear protocol and real interrupt outputs. It does not execute TEE
or SPSS firmware, implement SCM calls, or fabricate secure-service replies.

## Evidence and the observed abort

The captured node /soc/qcom,qsee_irq has a syscon phandle of 0x84, resolving
to /soc/syscon@1880000 with reg <0x1880000 0x10000>. The parent IRQs are
SPI348 and SPI349, named sp_ipc0 and sp_ipc1, both level high.

In native-root-0903 the production kernel faulted at
qsee_irq_probe+0x140/0x238, called through regmap_mmio_write32le+0xc.
The instruction b9000102 stores w2 through x8. Its register offset was
0x601c and value 0xf, identifying physical address 0x188601c. The source
routine masks all four notifications while registering the first bank.

The official Android kernel source at commit
1a4b384857a9b5ec3dceebc060096f7cf88f82ec contains the status, clear and mask
table for the matching SM8150 protocol. A fixed third-party source mirror
at commit 9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1 contains the same table
and also matches qcom,kona-qsee-irq. The mirror is not claimed to be a
verified exact OEM source tree. The captured Kona node, actual fault
offset/value and both source tables support this limited register model.

Selected DT properties, source hashes and the decoded fault are retained in
[qsee-irq-topology.json](../evidence/expansion-20261123/qsee-irq-topology.json).
The original full private DT and raw production log are not included.

## Two small windows

The model maps two independent **0x20-byte** windows. It does not map or
supply generic responses for the rest of the syscon's 64 KiB address range.

| Bank | QEMU MMIO index | Physical base | Status | Clear | Mask | GIC SPI |
| --- | --- | --- | --- | --- | --- | --- |
| sp_ipc0 | 0 | 0x1886000 | +0x00 | +0x08 | +0x1c | 348 |
| sp_ipc1 | 1 | 0x1888000 | +0x00 | +0x08 | +0x1c | 349 |

Status is read-only. Clear is write-only and clears only the written-one
pending bits. Mask is read/write: a one disables that notification; a zero
enables it. Each bank implements bits 0 through 3. The parent output is
asserted while pending & ~mask is nonzero.

The driver only writes the clear register. Its readback behavior is not
established by the sources, so this model rejects reads instead of
inventing a read value. Reserved bits, unsupported registers, writes to
status, and unsupported access widths/alignment are diagnosed and rejected.
Handlers accept aligned 32-bit operations only. Transactions crossing
the boundary between mapped and unmapped regions are subject to QEMU's
normal address-space splitting; no wider atomic bus behavior is claimed.

## Notification input contract

The exported C entry point is:

    bool qcom_qsee_irq_notify(DeviceState *dev, unsigned bank, unsigned bit);

It latches the indicated event, including while that bit is masked. A true
return means that the local digital latch accepted the event. It is not
a secure-firmware completion or verification response. An invalid bank,
bit or call before realization returns false and is diagnosed.

There are eight standard notify GPIO inputs, numbered bank * 4 + bit.
They adapt a low-to-high transition to a call of the same notification API.
Holding an input high, clearing its pending bit and writing the same high
level again does not create another event. A low level followed by a new
high level does.

This rising-edge adapter is an explicit reference digital interface.
The cited Linux driver does not establish the physical secure-side
notification waveform or edge-detector implementation. Its software
edge-type bitmaps do not justify inventing an extra hardware configuration
register. No automatic notifications or remote firmware responses occur.

## Reset and migration

Reference reset sets both pending words to zero and both masks to 0xf.
Those values are a model reset contract, not captured silicon reset values.
Reset preserves the current external GPIO input levels, so a held input
does not become a fabricated new edge. Power-on starts with zero input
levels because the device instance is newly initialized.

VMState version 1 serializes both pending/mask pairs and the eight input
levels. The destination rejects state containing reserved pending or mask
bits, then re-evaluates both outputs. Component migration tests use
ufs-enabled=off; they do not establish whole-board migration or migration
of any unimplemented secure-firmware service.

## Board interface

The QOM device type is qcom-qsee-irq. The tests use the board child
/machine/qsee-irq. IRQ outputs 0 and 1 route to GIC SPI348 and SPI349.
The two MMIO indices map to the physical bases in the table. No new syscon
catch-all mapping, clock bootstrap, secure memory contents or SCM success
path is needed for this device.

## Validation contract

tests/test_qsee_irq.py contains twelve behavior cases and six checks in a
real process migration case. The behavior cases cover both banks and all
four input bits, masked pending retention, selective W1C, bank independence,
held inputs, guest reset, invalid register/bit/width accesses, absence of
fabricated events, and the actual two GIC routes.

The physical-route fixture leaves the sysbus outputs connected and never
writes GICD_ISPENDR. It checks assertion, masking, reassertion of retained
pending events, bank independence and deassertion after clear.

The migration case holds two inputs high, transfers one enabled pending
event and multiple masked pending events, and then checks the preserved
levels do not fabricate new edges. It verifies post-load unmask/W1C, new
edges and reset while inputs remain high.

These tests ran successfully on binary SHA256
51fe61d35213a6602a8eaa865faba04bc85f88df341947ca8a2e6300a8ab5be6:
[12 behavior checks](../evidence/qsee-dcc-20261123/qsee/report.json) and
[6 actual migration checks](../evidence/qsee-dcc-20261123/qsee-migration/report.json).
The first migration attempt stopped before transfer because clock_step 0
requires a strict time increase. The original failure and the harness-only
correction remain in the evidence directory; all six checks were retained.

    python3 tb371fc/tests/test_qsee_irq.py \
      --qemu /path/to/matching/qemu-system-aarch64 \
      --work-dir /work/qsee-behavior
    python3 tb371fc/tests/test_qsee_irq.py \
      --qemu /path/to/matching/qemu-system-aarch64 \
      --migration-only --work-dir /work/qsee-migration

## Sources

- [Official Android qsee_ipc_irq.c](https://android.googlesource.com/kernel/msm/+/1a4b384857a9b5ec3dceebc060096f7cf88f82ec/drivers/soc/qcom/qsee_ipc_irq.c),
  blob 0e397261488bf360e4560b84f23894e8b768b541.
- [Fixed third-party mirror, qsee_ipc_irq.c](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/qsee_ipc_irq.c),
  SHA256 0bc893aba2ec43d2ae6568c37e30ee7fa069148bd046f9a104aa77a38707acbf.
- Read-only captured TB371FC DT, selected syscon/interrupt topology in the
  accompanying JSON. The register value 0xf and fault offset 0x601c were
  observed in the actual production-kernel run, not inferred from a generic
  success response.
