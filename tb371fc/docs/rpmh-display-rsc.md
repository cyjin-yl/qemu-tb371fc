# Shared APPS and display RPMh RSC

The TB371FC live device tree contains two qcom,rpmh-rsc controllers.
The display controller has its own MMIO layout and completion interrupt, but
its requests target the same AOP resource service as APPS. The model therefore
uses four voter slots in one device, with one command database, one set of
typed resources and one aggregate clock/power result.

The matching binary was built and executed on 2026-11-23. All 11 display
behavior cases and 6 actual migration checks passed, together with the existing
22 APPS cases, 5 APPS migration checks, 3 file-loader cases and 6 private partial-DB
clock checks. See [actual receipts](../evidence/display-rsc-20261123/tests-final.json).
The same production kernel and full private DT progressed beyond the previous
display RSC and SPMI controller MMIO aborts, then faulted in qsee_irq_probe.
This remains a failed full-DT boot, not a stock Android success.

## Captured topology and the native failure

The source is the existing read-only live-DT inventory. The selected original
properties and source hash are in
[rpmh-display-topology.json](../evidence/expansion-20261123/rpmh-display-topology.json).

| Controller | Physical frames | Selected DRV | TCS base | Configured banks | GIC SPI |
| --- | --- | --- | --- | --- | --- |
| APPS | 0x18200000, 0x18210000, 0x18220000, each 0x10000 | DRV2 | 0xd00 | active 2, sleep 3, wake 3; control 1 is not an executable bank in the cited driver | 3, 4, 5 |
| Display | 0x0af20000, length 0x10000 | DRV0 | 0x1c00 | active 0, sleep 1, wake 1, control 0 | 129 |

The APPS qcom,tcs-config cells are [2,2, 0,3, 1,3, 3,1].
The display cells are [2,0, 0,1, 1,1, 3,0]. Bank offsets follow their DT
order: display sleep is TCS0 and wake is TCS1.

The native native-f11-root-0816 run faulted at
rpmh_rsc_probe+0x194/0x530. Its instruction b9400119 is
ldr w25, [x8], with x8=ffffff800af6000c, x9=ffffff800af61c00
and x10=0x1c00. This directly identifies a frame +0x0c load.
The mapping to the display RSC is an inference supported by the matching
0x1c00 TCS offset and the complete DT topology. It is not a physical
page-table walk. It is also distinct from TCS-local status register +0x18.

The driver source explains the access: rpmh_probe_tcs_config() maps the
named drv-N frame, adds qcom,tcs-offset, and reads
DRV_PRNT_CHLD_CONFIG at frame +0x0c. The executable bank count excludes
CONTROL_TCS. For a controller with no dedicated active banks,
get_tcs_for_msg() borrows a wake TCS for active requests.

## Protocol and sharing

The model retains its existing APPS register ABI. Display uses its own
0x1c00 TCS base, two implemented TCS banks, sixteen command slots per TCS,
and the same documented 0x2a0 TCS / 0x14 command strides. Software
AMC_ENABLE plus a rising AMC_TRIGGER works on display TCS1, so the
driver's borrowed-wake active-request path can execute normally.

Capabilities describe this implementation. APPS +0x0c is 0x80008208;
display +0x0c is 0x80000002. These values encode three APPS slots of eight
TCS, or the single display slot of two TCS, with sixteen commands each.
They are not captured silicon capability/reset values. The 0x20700
protocol revision remains an explicit reference ABI choice.

All four vote slots participate in the same ARC/VRM maximum and BCM
aggregation. A release from APPS cannot disable a resource that display
still requests. BCM staged data and commits remain separate per voter:
committing the display virtual clock domain cannot commit a pending APPS
transaction. Both sides resolve addresses through the same loaded database.
Missing resources and invalid commands still stall without completion ACK.

The two controllers have separate IRQ masks and completion latches.
Display accepts only bits 0 and 1; writes targeting nonexistent TCS banks
or other registers fail with a diagnostic. W1C on one frame cannot clear the
other frame's pending completion.

The downstream print_tcs_info() routine reads RSC_DRV_STATUS and prints
nonzero as IDLE, zero as BUSY. The model now provides a Boolean 1 for
idle and 0 for an executing or stalled TCS. The polarity follows this
driver evidence; the exact Boolean encoding is a model contract, not a
sample of the whole silicon status word.

Explicit sleep input 3 triggers display TCS0 on entry and TCS1 on wake.
The APPS inputs retain their existing three-bank ranges. Programming banks
alone never executes them, and a transition while any bank in the same
frame is busy is diagnosed and rejected. This input is an explicit digital
event interface; it does not claim an autonomous SDE/PDC solver.

## Board interface

The device remains one qcom-sm8250-rpmh child at /machine/rpmh.

| Interface | APPS | Display |
| --- | --- | --- |
| SysBus MMIO indices | 0, 1, 2 | 4 |
| SysBus IRQ indices | 0, 1, 2 | 3 |
| sleep GPIO indices | 0, 1, 2 | 3 |

Command-db keeps MMIO index **3**, with the board's existing read-only
reserved-RAM overlay. RPMH_DRVS stays 3 for existing APPS board loops.
RPMH_VOTERS is 4. RPMH_DISPLAY_DRV is 3 and
RPMH_DISPLAY_MMIO is 4.

The board integration adds MMIO4 at 0x0af20000 and connects IRQ3 to
GIC SPI129. It must not create a second independent RPMh device.

The native /soc/qcom,sde_rscc@af20000 node also describes a drv range
inside the display RSC frame, plus a separate wrapper at 0xaf30000.
The RSC mapping must not be covered by an unrelated stub. SDE sequencer,
wrapper, display clocks/power transitions and other unimplemented accesses
remain outside this patch and receive no fabricated success.

## Migration and tests

Adding the fourth vote slot changes the serialized state layout. The root
device and resource VMState descriptions therefore use **version 2,
minimum version 2**. Version 1 streams from the old three-voter device are
rejected; there is no silent reinterpretation or claimed backward migration
compatibility. The destination validates display IRQ masks and rejects
non-reset state in its six nonexistent backing TCS slots.

tests/test_rpmh_display.py contains eleven behavior cases and a separate
six-check real process migration case. The cases include the borrowed wake
path, shared ARC/VRM release, BCM commit isolation, per-frame IRQ W1C,
display sleep/wake geometry, final command slot and invalid bounds,
cancellation, unsupported requests, guest reset, and the actual board
GIC SPI129 connection. The GIC case does not intercept the output or
write GICD_ISPENDR.

The migration case transfers both controllers in one real QEMU stream,
including committed votes, per-controller pending IRQs, two separate BCM
staging sets, an unsupported stalled command and an in-flight display
command. It then checks the exact remaining virtual deadline, shared vote
release, per-controller BCM commit and continued sleep/wake operation.

Examples after board integration and compilation:

    python3 tb371fc/tests/test_rpmh_display.py \
      --qemu /path/to/matching/qemu-system-aarch64 \
      --work-dir /work/rsc-display-behavior
    python3 tb371fc/tests/test_rpmh_display.py \
      --qemu /path/to/matching/qemu-system-aarch64 \
      --migration-only --work-dir /work/rsc-display-migration

Both default to rpmh-profile=reference,rpmh-boot-configured=off,ufs-enabled=off.
They use synthetic reference addresses and test component migration.
They do not establish full-board migration, native command-db completeness,
AOP firmware execution, PMIC analogue behavior or a successful native
display stack.

## Primary sources

- Captured live-DT inventory:
  tb371fc/evidence/expansion-20261123/android-device-inventory.json.
- Pinned downstream driver:
  [drivers/soc/qcom/rpmh-rsc.c at 9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/rpmh-rsc.c).
  Relevant routines are rpmh_probe_tcs_config(), get_tcs_for_msg(),
  tcs_write() and print_tcs_info().
- Upstream protocol:
  [drivers/soc/qcom/rpmh-rsc.c, Linux v6.12](https://git.kernel.org/pub/scm/linux/kernel/git/torvalds/linux.git/tree/drivers/soc/qcom/rpmh-rsc.c?h=v6.12).
