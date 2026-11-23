# AOP message SRAM

## Implemented hardware and deliberate boundary

The captured TB371FC DT describes a 4 KiB message SRAM aperture at
`0x0c300000`, named `msgram`, with the mailbox descriptor at offset zero.
The AOP doorbell and incoming notification use the existing IPCC controller
at `0x00408000`, client 0, signal 0. That controller feeds GIC SPI 229.
The allowlisted captured fields and original DT hash are recorded in
[geometry.json](../evidence/aop-msgram-20261123/geometry.json).

The `qcom-aop-msgram` device implements that aperture as writable RAM.
All bytes are stored and migrated by QEMU's RAM migration machinery.
Its owner is the actual SysBus DeviceState, so the migration RAMBlock has
a stable device identity. The board must attach it at
`/machine/aop-msgram` before realization and map MMIO region 0.

Cold zero contents and retention across QEMU machine reset are explicit
model conventions. Neither is claimed as a measurement of physical SRAM
power-on or power-domain reset behavior. There is no file importer,
firmware initialization, automatic descriptor, remote acknowledgement,
message consumption, IRQ generation, or AOP processor in this device.

## Why the production kernel reads this memory

The Qualcomm vendor driver copies a 64-byte mailbox descriptor in its RX
worker before checking the magic. Its `qcom,early-boot` option schedules an
initial check even without a received interrupt. Thus the observed native
kernel fault in `rx_worker -> __memcpy_fromio` identifies an inaccessible
descriptor read; an expected `MAIL` comparison constant in a CPU register
does not show what was in the memory.

The descriptor is shared firmware state. Its layout is:

| Offset | Meaning | Normal protocol writer |
| --- | --- | --- |
| 0x00 / 0x04 / 0x08 | Magic / version / features | AOP initialization |
| 0x0c / 0x14 | AOP link / channel state | AOP |
| 0x10 / 0x18 | Acknowledgement of AOP state | APSS |
| 0x1c / 0x20 | AOP mailbox size / offset | AOP initialization |
| 0x24 / 0x2c | APSS link / channel state | APSS |
| 0x28 / 0x30 | Acknowledgement of APSS state | AOP |
| 0x34 / 0x38 | APSS mailbox size / offset | Firmware configuration, written back by vendor driver |
| 0x3c | Reserved | No implemented protocol meaning |

A message consists of a 32-bit length and payload. The sender writes the
payload and publishes its length before ringing the doorbell. The remote
processor processes the message, clears the length, and notifies the sender.
The lower-level doorbell's software TX completion is separate from QMP
message completion. SRAM storage must not perform those firmware actions.

With no peer and no initialized descriptor, the expected vendor behavior
is a readable aperture with an unconnected QMP link. Removing the external
abort does not establish that AOP, power control, or full Android boot works.

## Source evidence

- [Qualcomm vendor driver, commit a70fac8a1606e46484717ac9f39f3348d4b47e6e](https://android.googlesource.com/kernel/msm.git/+/a70fac8a1606e46484717ac9f39f3348d4b47e6e/drivers/mailbox/msm_qmp.c):
  descriptor structure, RX worker, startup and message completion.
- [Qualcomm Kona DTS, commit 2952a9d05441686543b187386d3843ae17e3d14e](https://android.googlesource.com/kernel/msm.git/+/2952a9d05441686543b187386d3843ae17e3d14e/arch/arm64/boot/dts/qcom/kona.dtsi):
  independent platform source consistent with the captured aperture and route.
- [Upstream qcom_aoss.c, commit 141b164bbe186423e295b139e40e8e2d11cf1341](https://kernel.googlesource.com/pub/scm/linux/kernel/git/torvalds/linux/+/141b164bbe186423e295b139e40e8e2d11cf1341/drivers/soc/qcom/qcom_aoss.c):
  independent implementation of descriptor offsets and handshake.
- [Original upstream author explanation](https://lkml.rescloud.iu.edu/1905.0/00026.html):
  clearing length and the return interrupt follow AOSS message processing.

These sources establish a transport contract, not the actual tablet's
firmware-provided mailbox size, offsets, initialized contents, AOP CPU type,
service implementation, or physical timing.

## Acceptance

Run the test using a freshly created short output path:

```sh
python3 tb371fc/tests/test_aop_msgram.py \
  --qemu /absolute/path/to/qemu-system-aarch64 \
  --out /tmp/aop-msgram-test
```

The suite requires exactly 10 passing groups and one completed two-process
migration. It covers all 4096 bytes with varying patterns, 1/2/4/8-byte
accesses, a 64-byte descriptor read, boundaries, reset retention, and
preservation of a pending message through migration. It sends real IPCC
doorbells and checks unchanged ACK/length fields, no receive event, and no
GIC SPI 229 pending state. It does not intercept the IRQ output.

The constructed descriptor in the tests is labelled reference input and
is never a board default. Wide QTest transactions test the memory aperture;
the production-kernel rerun separately verifies the original CPU load path.

Upstream qcom_aoss.c describes message RAM as supporting only 32-bit accesses
and uses __iowrite32_copy. The observed production kernel instead performs a
64-bit load during descriptor copying. This device permits QEMU RAM access
widths 1/2/4/8 to preserve storage semantics and the observed CPU load path.
That is a memory-model contract, not evidence that physical silicon accepts
every width, performs a particular bus beat, or makes such accesses atomic.

Actual compiled-device validation is recorded below. The results establish
the stated storage contract; AOP firmware and remote services remain unvalidated.

## Recorded integrated validation (2026-11-23)

The [10 targeted checks](../evidence/boot-resources-20261123-1005/aop-msgram/report.json) and [55 public gates](../evidence/boot-resources-20261123-1005/public-55/result.json) passed on frozen binary SHA256 2173fa4da68f8f769c53942b056124c5621e4714652c7f572deacc9440ec09cf. Original receipts retain build/test source hashes. This validates the documented contract without expanding it to physical reset values, analog behavior, remote firmware services or full-tablet equivalence.
