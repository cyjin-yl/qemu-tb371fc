# SM8250 UFS host, QMP PHY and DMA model

This implementation is a functional digital host/PHY model for driver work.
It is not a claim of complete UFS silicon, flash timing or TB371FC equivalence.
The first real Linux run established working block I/O; later tests extend the
clock, Hibern8 and IOMMU coverage without changing the guest driver.

## Evidence and reference choices

| Item | Source and treatment |
| --- | --- |
| Host / PHY address, interrupt | Native DT: `0x01d84000/0x3000`, `0x01d87000/0x1000`, GIC SPI 265. |
| DMA stream IDs | Upstream Linux v6.12.114 `sm8250.dtsi`: `0xe0` and `0x4e0`. The native 4.19 UFS DT node has no `iommus` property. The modeled single transport port uses `0xe0`; both IDs are described to Linux. Their native traffic-class split is unknown. |
| ICE window | Native DT: `0x01d90000/0x8000`. Read-only unsupported reference probe; VERSION major 255 is an explicit sentinel, not a native register measurement. See [the behavioral audit](sm8250-behavior-audit.md). |
| Device revision / vendor | Safe UFS sysfs: device specification `0x0310`, manufacturer `0x01ce`. These are not the host HCI revision. |
| Clock ranges | Native DT and upstream SM8250: core/UniPro 37.5–300 MHz. They are retained. |
| Qualcomm host revision | Configurable **reference 4.0.0**, inferred from the 300 MHz driver contract. Minor/step and the native register were not measured. See [the diagnosis](ufs-clock-diagnosis.md). |
| HCI revision | Configurable reference 3.0 (2.1 is also accepted); native HCI revision remains unknown. |
| PHY lane symbol clock | Explicit reference 150 MHz. It is not inferred from the sampled gear and is not a throughput/timing claim. |
| Logical units | The tablet exposes six ordinary LUs and four WLUNs. [Native unit evidence](../evidence/expansion-20261123/android-ufs-units.json) preserves block counts and read errors. Test disks are new synthetic media, not copies of those units. |

The implementation is in
[`hw/ufs/qcom-sm8250-ufs.c`](../../hw/ufs/qcom-sm8250-ufs.c).
The 120-register QMP allowlist and 111 required base-programming addresses are
derived from the exact Linux 6.12.114 files listed in
[`ufs-qmp-registers.json`](ufs-qmp-registers.json).
The generation script preserves source hashes and GPL-2.0-only licensing.

The transport and SCSI core reuse QEMU upstream work. Eleven cleanly applied
upstream patches separate the PCI frontend, add Sysbus support and fix reset,
cancellation, task-management, PRDT and SCSI handling. Their original authors,
subjects, order, commit URLs and exact patch hashes are in
[`ufs-upstream-backports.json`](ufs-upstream-backports.json).
The TB371FC gate exercises the legacy host path; it does not establish MCQ or
generic PCI frontend acceptance.

## Connected behavior

The board connects the GCC UFS GDSC and BCR, the AXI, aggregate AXI, AHB,
UniPro, PHY auxiliary and reference clocks, and all three lane symbol branches.
An inaccessible host returns a transaction error. UIC work requires its host
clocks; link startup and new DMA submissions also require the PHY and lane
clocks. The PHY produces lane clock inputs for GCC, and the controller consumes
the gated outputs. This is a clock dependency graph, not a forced-ready switch.

The QMP PHY requires its documented base programming addresses, power,
released reset, start, auxiliary/reference clocks and reference clock gate.
A virtual 10 microsecond interval separates programming from ready state.
This interval establishes event ordering; it is not measured analog settling.
Known programming register values are retained. Analog calibration validity,
PLL jitter and electrical signal behavior are not simulated.

UIC commands complete asynchronously after a virtual one-microsecond ordering
interval. Commands snapshot their arguments, change readiness, and deliver
normal completion and error interrupts. The implemented link states are down,
active and Hibern8. Unknown MIBs, read-only writes, invalid lane/gear counts and
invalid power modes return defined errors. The local FC/replay/AFC timeout
attributes retain programmed values; packet-level retransmission machinery
and its physical timing are not implemented.

Legacy UTRD and UPIU processing uses actual guest-memory DMA. PRDTs feed real
SCSI commands and block backends. The Qualcomm wrapper calls the transport
register helpers directly inside its outer MMIO guard; it does not disable
QEMU's reentrancy guard to bypass a self-dispatch error. HCE/BCR reset cancels
pending work and resets controller/PHY state while preserving media contents.

The [automatic Hibern8 contract](ufs-auto-hibern8.md) adds CAP.AUTOH8, AHIT idle
timing, automatic request wake, runtime clock/PHY retention and a versioned
checkpoint subsection. Explicit UIC Hibern8 remains separately controlled.

WriteBooster's Hibern8 flush flag is readable and writable. Background buffer
occupancy is flushed when explicit flush is enabled, or when the link is in
Hibern8 and its Hibern8 flush flag is enabled. The existing core idle timer
governs that work. This is the upstream software occupancy model, not a model
of NAND programming latency or flash endurance.

Apps SMMUv2 is on the actual DMA path through SID `0xe0`. The generated DT
uses the architectural `arm,smmu-v2` compatible; Qualcomm TCU/TBU extensions
are not implied. The strict Linux gate requires the UFS device to belong to a
`DMA` domain. Page translation, permissions, table faults and IRQ semantics are
tested independently by the SMMUv2 QTest suite.

## Running the gates

Build the dedicated QEMU machine and pinned smoke kernel as described in the
repository README, then use fresh output directories:

```sh
python3 tb371fc/tests/test_ufs_sm8250.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/ufs-register-test

python3 tb371fc/scripts/boot_ufs.py \
  --qemu /work/build/qemu-system-aarch64 \
  --kernel /work/clean-linux/artifacts/Image \
  --out /work/ufs-linux-test
```

Both gates create their own empty 64 MiB raw disk and attach LU0 to `ufs0`.
The QTest gate programs GCC, PHY, UIC, UTRD and SCSI interfaces and checks
negative paths as well as actual data movement. It independently reads the
host backing file after guest DMA writes, and checks that rejected/short
transfers do not corrupt adjacent sectors.

The Linux gate builds a static PID 1 and uses unmodified upstream Qualcomm UFS,
QMP PHY, GCC and SMMU drivers. It checks binding, a strict translated DMA domain,
4 KiB sectors, capacity, direct multiblock I/O, flush/reopen persistence,
READ CAPACITY, invalid-command sense and end-of-device behavior. The host
independently verifies the 32 KiB pattern in the backing file.

The storage PID 1 prints its verdict and waits. **The host ends this particular
run through QMP; it does not test guest shutdown.** The separate normal boot
and GDB-observed production-kernel gates continue to require actual guest
PSCI shutdown. A host quit or a timeout must never satisfy their shutdown gate.

## Recorded first runs and remaining limits

The [first register/DMA run](../evidence/ufs-bringup-20261123/first-qtest/result.json)
passed 31 checks, and the
[first Linux run](../evidence/ufs-bringup-20261123/first-linux/result.json)
passed 16 storage/driver checks with an independent backing-file match.
Those runs used the exact QEMU hash recorded in their receipts. The first
Linux log also contains the revision/clock error diagnosed above; its data
success is not an error-free DVFS result. Subsequent acceptance receipts must
identify their own binaries and extended checks.

The HCI/PHY model does not yet implement ICE, RPMB authentication, native LU
boot selection/write protection, all descriptors and native feature bits,
full task-management/reset coverage, UFS migration, packet-level link timing
or analog PHY behavior. PMIC supplies are not yet modeled and the initial
Linux gate reports dummy/assumed-on supplies. Whole-board migration remains
blocked by the upstream UFS core; component migration tests explicitly use
`-M tb371fc,ufs-enabled=off`.

The two measured device feature fields and exact native LU geometry are
evidence, not permission to advertise unimplemented functions. Only currently
implemented features are advertised. No factory images, user data, device
secrets or redistributability-unknown firmware are included in the repository.

## Primary implementation sources

- [Linux v6.12.114 UFS Qualcomm host](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/ufs/host/ufs-qcom.c?h=v6.12.114)
- [Linux v6.12.114 UFS core](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/ufs/core/ufshcd.c?h=v6.12.114)
- [Linux v6.12.114 UniPro definitions](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/include/ufs/unipro.h?h=v6.12.114)
- [Linux v6.12.114 QMP UFS PHY](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/phy/qualcomm/phy-qcom-qmp-ufs.c?h=v6.12.114)
- [QEMU UFS documentation](https://www.qemu.org/docs/master/system/devices/ufs.html), with exact imported revisions pinned in the backport manifest.
