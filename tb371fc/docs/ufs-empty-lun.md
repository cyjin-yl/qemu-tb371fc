# Absent UFS logical unit recovery

## Observed failure and implemented behavior

An integrated unpatched Linux 6.12.114 run with the UFS controller enabled
and no logical unit attached failed before PID 1. The transport returned
OCS `INVALID_CMD_TABLE_ATTR` for an ordinary command addressed to an
absent LUN. Linux entered controller-error diagnostics and then encountered
an external abort while reading the vendor register block at offset
`0xd4`. The [failed receipt](../evidence/ufs-empty-lun-20261123/before-result.json)
and [console](../evidence/ufs-empty-lun-20261123/before-serial.log) are retained.

The transport now completes a well-formed absent-LUN request with its SCSI
response. Standard INQUIRY reports peripheral type `0x7f` (no logical unit);
REQUEST SENSE reports `05/25/00` (logical unit not supported); other commands
return CHECK CONDITION with that sense. The UTP completion itself reports
success because the command table was valid. Invalid inquiry fields still
return their own invalid-field sense, and an undersized PRDT returns the
existing OCS data-buffer mismatch.

The data path respects both CDB allocation length and the guest PRDT.
Residuals describe unused expected data, and bytes outside the actual
response remain untouched. Eight new QTest groups exercise absent ordinary
LUNs, out-of-range LUNs, an unknown WLUN, invalid fields, short buffers and
both DMA/allocation bounds.

The wrapper also recognizes the exact diagnostic apertures read by upstream
`ufs_qcom_dump_dbg_regs()`, including the reserved vendor-block word and
test-bus output. Reads of unsupported internal diagnostic samples return
zero and emit an explicit unimplemented diagnostic. Their writes and
unrelated HCI offsets remain rejected. This read accommodation does **not**
model the internal debug bus, SRAM contents or measured hardware telemetry,
and is not counted as a separate successful peripheral implementation.

## Measured validation

| Gate | Executed result |
| --- | --- |
| UFS MMIO/PHY/DMA/SCSI suite | 56/56 passed, including all eight absent-LUN groups |
| Linux, UFS enabled with no attached LUN | 8/8 PID 1 checks, counter probe and clean shutdown |
| Integrated frozen-10 Linux suite | 4/4 gates: populated console, empty-UFS console, UFS/SMMU I/O, RPMh/UFS/SMMU I/O |
| CI runner unit checks | 14/14 passed |

The first two gates used frozen-9 QEMU SHA-256
`694270c10710275d100bc4e410fef511b2ccc2810620fd76cb2d9fb3b4069e0e`.
Their receipts are [QTest](../evidence/ufs-empty-lun-20261123/qtest-result.json)
and [empty-bus Linux](../evidence/ufs-empty-lun-20261123/result.json).

All four Linux gates were then run on the same frozen-10 binary; the
[aggregate receipt](../evidence/ufs-empty-lun-20261123/linux-suite/result.json)
records its hash, unchanged-binary check, exact commands and individual
verdicts. Both runs used unpatched Image SHA-256
`c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`.
Its pinned build provenance remains in
[the RPMh Linux evidence](../evidence/rpmh-linux-20261123/kernel-build.json).

These runs use the generated functional-subset device tree and the validation
initramfs. They establish recovery from an absent storage logical unit,
console/user-space execution and the separately checked synthetic disk I/O.
They do not establish a complete native-device-tree or Android boot.

## Reproduce

`boot_smoke.py` normally creates a fresh 64 MiB synthetic UFS disk for its
console/shutdown fixture. The separate empty-bus gate keeps the controller
enabled and omits every `ufs-lu` device:

```sh
python3 tb371fc/scripts/boot_smoke.py \
  --qemu /absolute/path/qemu-system-aarch64 \
  --kernel /absolute/path/smoke/artifacts/Image \
  --without-ufs-lu --debug-mmio --out /work/a-new-empty-ufs-run
```

`ci_device_tests.py --suite linux` requires both configurations independently.
The empty-bus case must report `ufs_fixture.populated=false`, eight completed
checks and a successful native-frequency counter probe. A kernel panic or an
exit before PID 1 cannot pass.

## Implementation sources

The SCSI discovery and sense conventions follow the existing
[QEMU target implementation](../../hw/scsi/scsi-bus.c).
The bounded diagnostic apertures come from Linux 6.12.114
[`ufs-qcom.c`](https://github.com/gregkh/linux/blob/v6.12.114/drivers/ufs/host/ufs-qcom.c)
and [`ufs-qcom.h`](https://github.com/gregkh/linux/blob/v6.12.114/drivers/ufs/host/ufs-qcom.h).
No device-specific sampling semantics are inferred from those register-window
definitions.
