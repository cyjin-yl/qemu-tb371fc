# SM8250 UFS, SMMU and power behavior audit

This follow-up checks the November 24 external hardware review against the
actual model, public driver code and a new **read-only** tablet sample.
The base is `8babe85b1cd7990a1a113b223d51ec8a7732fed5`.
It does not certify stock Android, complete native-DT boot or silicon equality.

## Findings and changes

| Area | What the base already had | Defect or missing behavior addressed here |
| --- | --- | --- |
| ICE | Crypto capability clear; no ICE MMIO window | Map an explicitly unsupported read-only probe at `0x1d90000/0x8000`; reject crypto descriptors instead of processing plaintext. |
| UFS power | Explicit UIC Hibern8 entry/exit, PHY clock dependencies and quiescent migration | Implement AHIT idle timing, automatic wake and migration of the remaining idle interval; cancel pending UIC on device reset. |
| SMMU | Real UFS DMA through SID `0xe0`, stage-1 walks, permissions, faults and migration; DT also lists `0x4e0` | Correct the SID evidence attribution; add opt-in ANOC1/ANOC2 HALT/ACK and ECATS using the real SMMU walker. |
| PDC/RPMh | PDC pin-to-GIC routes, edge/level state, RPMh TCS/resources/timers and component migration | Retain sleep/wake input levels received while a voter is busy; verify PDC/GIC pending interrupts and delayed wake in the same process checkpoint. |

### ICE is an unsupported probe, not an encryption engine

The tablet's native DT gives both `ufshc@1d84000` and `ufsice@1d90000` the
ICE resource. No native ICE VERSION or fuse register was read. The model
returns a deliberate VERSION **major 255** sentinel at `+0x8`, unavailable
fuses at `+0x10`, and an unsuccessful BIST indication at `+0x70`. Writes,
unsupported offsets and access widths fail. These values are immutable and
are explicitly **reference values**, not a captured tablet identity.

The pinned upstream driver accepts major 3/4, while the official SM8250 vendor
reference rejects versions newer than its supported major. Major zero is
therefore not a reliable rejection sentinel across both paths. Upstream
`ufs_qcom_ice_init()` explicitly tolerates `-EOPNOTSUPP`. However, SCM and
clock prerequisites run before the version check. The generated functional
DT does **not** add an ICE consumer resource: advertising it without SCM
could cause permanent probe deferral. A mapped window does not implement
SCM, QTEE, keyslots, encrypted UFS traffic or a complete native probe.

The important storage bug was independent of MMIO: CAP.CS was zero but the
shared UFS core ignored the descriptor crypto-enable bit. A real baseline
QTest run with a new empty 64 MiB disk returned OCS=0 for encrypted-marked
writes and reads, wrote the supplied bytes in plaintext, and returned them.
The fix returns `OCS_INVALID_CRYPTO_CONFIG` (9) before PRDT/data access or
SCSI submission. Legacy completion writes its UTRD without a response UPIU;
MCQ preserves the original tag/LUN and completes its CQE without response
DMA. Invalid response/PRDT addresses cannot turn this rejection into an
unfinished request. Ordinary I/O remains usable after the errors.

### Automatic UFS idle and wake

See [Auto-Hibern8](ufs-auto-hibern8.md) for the exact timer and migration
contract. The controller observes actual request completion even with
interrupts masked. It cannot sleep with queued requests, UIC work or failed
unfinished DMA. Successful automatic transitions do not invent UIC error
interrupts. A synchronous task-management completion restarts a full idle
interval. Clock loss cancels the interval; restoring the necessary clocks
starts a new one. PHY-off Hibern8 can be retained across a process boundary
and subsequently awakened for real translated block I/O.

The v1 UFS main wire layout remains intact. Old streams use the explicit
`-global qcom-sm8250-ufs.auto-hibern8=off` destination capability profile;
the new default capability cannot be silently substituted for the old one.
Storage queues must still be quiescent for a supported checkpoint. External
media is shared with normal file locking and is not embedded in VMState.

The public Linux runtime-PM gate independently uses the real qcom UFS driver:
platform host, LUN and device WLUN suspend, core/iface/UniPro clocks gate,
and an ordinary read on the original direct-I/O descriptor wakes the host.
Forty ordered checks and the host's complete 64 MiB comparison passed in
both the [frozen and portable runs](../evidence/sm8250-contract-20261124/README.md#linux-runtime-pm-extension).
The reference clock remains running. This is not system suspend or a
checkpoint taken while Linux is runtime-suspended.

### SID and TBU evidence

The earlier UFS document incorrectly called `0xe0/0x4e0` a native-DT
observation. They are from upstream `sm8250.dtsi`; the sampled Lenovo 4.19
UFS node has no `iommus` property and no sysfs IOMMU group link.

Native ANOC1 and ANOC2 ranges are `0x000..0x3ff` and `0x400..0x7ff`.
Combining those ranges with the upstream UFS SIDs suggests e0→ANOC1 and
4e0→ANOC2. That is a cross-source inference, not a captured DMA trace.
The UFS model still emits one physical transport stream, e0; publishing a
second DT ID does not establish the native traffic-class split.

No standalone ANOC clock-gating MMIO contract was found in the inspected
driver. The sampled apps SMMU and both ANOC TBU nodes have no `clocks` or
`clock-names`, and apps SMMU has `qcom,skip-init`. The usable bounded
extension is the HALT/ACK and ECATS debug translation interface described
in [the TBU review](tbu-contract-review.md), not a fabricated always-ready
clock bit. Architectural SMMUv2 and Qualcomm implementation identity must
remain separate profiles.

The new `tbu-profile=reference-ecats` profile maps the evidenced TCU/ANOC1/
ANOC2 debug windows. HALT immediately closes the selected SID group's DMA
admission gate; a virtual timer later reports ACK. A gated DMA transaction
fails instead of physically stalling. ECATS requires ACK and its lock, and
uses the existing SMMU page-table walker, permissions and faults. Bypass,
disabled contexts and invalid mappings cannot manufacture success. Remaining
query/ACK durations and admission gates survive a real process checkpoint;
profile/version/latency mismatches and impossible states reject before resume.
The reference TCU version and 1 us default delay are not native measurements.
See the [implemented TBU contract](tbu-reference-ecats.md).

### Retained wake requests and real PDC/GIC continuation

RPMh previously discarded a sleep/wake input transition whenever any TCS
in that voter was busy. If a wake level arrived during an unfinished sleep
command and remained asserted, no later edge reissued it. The baseline
fails the targeted held-wake test for this reason.

The fix records the latest requested level, reconciles it once the voter
becomes idle, and migrates that state. An unsupported stalled command is
not falsely acknowledged; explicit cancellation can release the delayed
request. Reset clears it. RPMh VMState v3 continues accepting v2, initializing
its new latch from the previous accepted sleeping state.

The new combined test leaves real PDC edge/level interrupts pending in the
GIC while a sleep TCS is unfinished and wake is queued. A new QEMU process
must preserve those interrupts, consume the remaining deadlines and deliver
RPMh completion to the GIC exactly once. This checks explicit GPIO-driven
power state. CPU WFI, automatic PDC-to-RPMh coordination, remote firmware
and physical power collapse remain outside that contract.

## Reproduction

```sh
python3 tb371fc/tests/test_ufs_crypto.py --qemu "$QEMU" --out /tmp/ice-new
python3 tb371fc/tests/test_ufs_autohibern8.py --qemu "$QEMU" --out /tmp/ahit-new
python3 tb371fc/tests/test_tbu_sm8250.py --qemu "$QEMU" --out /tmp/tbu-new
python3 tb371fc/tests/test_power_checkpoint.py --qemu "$QEMU" \
  --legacy-qemu "$BASELINE_QEMU" --out /tmp/power-new
python3 tb371fc/scripts/run_ufs_pci_compat.py --qemu "$PCI_QEMU" \
  --qos-test "$QOS_TEST" --out /tmp/pci-new
```

Each output directory must be new. Tests use synthetic disks only. Detailed
run receipts, binary hashes, failure diagnostics and final acceptance totals
belong to the evidence directory; test definitions alone are not a pass.

## Evidence and primary references

- [Read-only tablet facts and fixed source blobs](../evidence/sm8250-contract-20261124/hardware-facts.json).
- [Linux v6.12.114 ICE driver](https://github.com/gregkh/linux/blob/v6.12.114/drivers/soc/qcom/ice.c).
- [Linux v6.12.114 Qualcomm UFS driver](https://github.com/gregkh/linux/blob/v6.12.114/drivers/ufs/host/ufs-qcom.c).
- [Linux v6.12.114 SM8250 device tree](https://github.com/gregkh/linux/blob/v6.12.114/arch/arm64/boot/dts/qcom/sm8250.dtsi).
- [Official OnePlus SM8250 SMMU reference](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/iommu/arm-smmu.c).
- [Official OnePlus SM8250 ICE reference](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/crypto/msm/ice.c).

The OnePlus sources are public SM8250 implementation references, not the
binary-exact Lenovo kernel. The device sample changes no physical power,
clocks or storage and does not claim a new native suspend/resume acceptance.
