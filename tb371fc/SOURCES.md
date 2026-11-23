# Sources and provenance

## Upstream baseline

- [QEMU 11.1.3 release](https://www.qemu.org/download/)
- [Pinned source](https://github.com/qemu/qemu/tree/005c61c0ef6f6ae615283440b4bb7be51b2c6ddd)
- [Arm machine guidance](https://www.qemu.org/docs/master/system/target-arm.html)
- Local QEMU `target/arm/tcg/cpu64.c`, `hw/arm/boot.c`, `hw/arm/sbsa-ref.c`,
  `hw/intc/arm_gicv3*.c` and device-model APIs at the pinned revision.

## Hardware/driver references

- The owner's direct [Android board capture](evidence/android-board.json),
  [normalized profile](evidence/board-profile.json) and collector source hash.
- [Linux v6.12 SM8250 DTS](https://github.com/torvalds/linux/blob/v6.12/arch/arm64/boot/dts/qcom/sm8250.dtsi)
- [Linux v6.12 GENI serial driver](https://github.com/torvalds/linux/blob/v6.12/drivers/tty/serial/qcom_geni_serial.c)
- [Linux v6.12 GENI register definitions](https://github.com/torvalds/linux/blob/v6.12/include/linux/soc/qcom/geni-se.h)
- [Linux v6.12 QUP/GENI support](https://github.com/torvalds/linux/blob/v6.12/drivers/soc/qcom/qcom-geni-se.c)
- [Original TB371FC Linux port](https://github.com/oproskyrin/TB371FC-6.12)
- [Owner's Linux research fork](https://github.com/cyjin-yl/TB371FC-6.12)

The board capture takes precedence for board-specific active paths. For
example, its enabled Android console is at `0xa90000`, while the generic
SM8250 UART at `0x988000` is disabled in the captured Android tree.

## Qualcomm QEMU research: useful but not a TB371FC emulator

- [39C3 presentation source](https://github.com/rmalmain/39C3-build-a-fake-phone-find-real-bugs)
- [Qualcomm QEMU commit ac2521b0dfca2dbe19fb75661f8f1637186a44a4](https://github.com/qualcomm/qemu/tree/ac2521b0dfca2dbe19fb75661f8f1637186a44a4)

That revision identifies itself as QEMU 10.0.3. Its `hw/arm/qcom-virt.c`
targets KaanapaliT r2 and extends generic `virt`. Its graphics code targets
Adreno 840/Gen8 (`qcom,adreno-gpu-gen8-2-1`), while `franksmmu.c` implements
a custom mapping protocol used with a modified guest driver. In `hfi.c`,
the raw command issue message is handled by a simple acknowledgement.

The implementation is useful research for resource/firmware contracts,
but is neither an SM8250 SMMUv2 model nor A650 rendering support. No code
from that branch is imported by this initial TB371FC model. Future reuse
must preserve each file's license and attribution and port the actual
Kona resource tables and device contracts.

## Component contracts and evidence

The independent device documents distinguish native observations, published
architectural/driver contracts, synthetic bootstrap choices and actual tests.
Use their per-run hashes rather than treating a source table as execution proof.

| Component | Contract and provenance |
| --- | --- |
| Board and loader | [machine.md](docs/machine.md), [runtime layout evidence](evidence/boot-layout-20261123/runtime-layout-frozen4.json) |
| A77 CPU and PMU | [cpu.md](docs/cpu.md), Arm Cortex-A77 TRM references and native MIDR/REVIDR observations |
| MPM sleep counter | [mpm2-sleep-counter.md](docs/mpm2-sleep-counter.md), pinned boot_stats read32 contract and actual TB371FC DT 32768-Hz declaration; explicit virtual-time, cold-epoch/reset and migration policies |
| Generic Timer | [mmio-timer.md](docs/mmio-timer.md), Arm ARM external-register contract, exact-count arithmetic and retained regression controls |
| GENI | [geni-uart.md](docs/geni-uart.md), Linux FIFO/IRQ contract and actual clock/reset integration |
| GCC | [gcc-sm8250.md](docs/gcc-sm8250.md), pinned Linux-derived register manifest and clock-parent assumptions |
| GPUCC | [gpucc-sm8250.md](docs/gpucc-sm8250.md), pinned Kona Lucid PLL1, GMU, branch/reset contracts, actual Clock outputs and explicit analog/rendering limits |
| NPUCC | [npucc-sm8250.md](docs/npucc-sm8250.md), pinned Kona v2 three-bank PLL/RCG/branch/reset contracts, bounded CRC divider and unresolved-parent limits |
| SMMUv2 | [smmuv2.md](docs/smmuv2.md), architectural stage-1 model and explicit Qualcomm-extension limits; [version-2 geometry-bound migration](docs/smmuv2-migration.md) and actual retained counterexample/transfer receipts |
| UFS | [ufs-sm8250.md](docs/ufs-sm8250.md), [clock diagnosis](docs/ufs-clock-diagnosis.md), [pinned upstream backports](docs/ufs-upstream-backports.json) |
| RPMh | [rpmh-sm8250.md](docs/rpmh-sm8250.md), synthetic reference database versus optional external-file and AOP-derived partial metadata |
| TLMM | [tlmm.md](docs/tlmm.md), native enclosing aperture and pinned Linux per-pin layout |
| Watchdog | [watchdog.md](docs/watchdog.md), software-derived rate, digital timer/action behavior and migration |
| IMEM / PS_HOLD | [boot-resources.md](docs/boot-resources.md), scoped SRAM/pin contracts and boot dependency evidence |
| Guest acceptance | [guest-acceptance.md](docs/guest-acceptance.md), console-independent read-only GDB record and real shutdown requirement |
| Public CI | [ci.md](docs/ci.md), mandatory component and unpatched-Linux gates with retained failures |

UFS transport/SCSI reuse is pinned to the original upstream QEMU authors and
commit/patch hashes in its backport manifest. Linux-derived register tables
retain GPL-2.0-only notices where required; newly written files retain their
own SPDX identifiers. The Qualcomm research fork discussed above has not been
silently substituted for an SM8250 or A650 implementation.

Authorized production kernel/AOP assets remain private. Public derived
metadata records source hashes, extraction assumptions and unresolved fields;
it does not distribute firmware or imply that a static table is a live
hardware capture. The public CI plan requires no such private inputs.


## Retained timer regression ELF

The public [cpu-timer-regression.elf](evidence/mmio-timer-live-status-20261123/cpu-timer-regression.elf)
is an independently compiled GPL-2.0-or-later bare-metal test program, not a
tablet boot image or extracted proprietary firmware. Its SHA256 is
`37e1b51ffc5ab43a34f794aa264fbe5cf5cea015501ff61722538a95e682d35f`.
It was built from [cpu-migration.c](tests/mmio-timer/cpu-migration.c),
[start.S](tests/mmio-timer/start.S) and [link.ld](tests/mmio-timer/link.ld).
The [original failing receipt](evidence/mmio-timer-live-status-20261123/cpu-migration-before.json)
records the actual cross-compiler command. The preserved byte-identical ELF
was used for the corrected migration run and uninstrumented control; their
comparison and source hashes are in
[comparison.json](evidence/mmio-timer-live-status-20261123/comparison.json).
Recompilation is not claimed to reproduce incidental ELF metadata bytes.

[DCC](docs/dcc.md), [PDC](docs/pdc.md) and [IPCC](docs/ipcc.md) describe their
pinned driver sources, reference identity choices and unsupported modes.

[QSEE notification banks](docs/qsee-irq.md) combine selected captured Kona topology, the actual native mask-write abort and fixed Android/vendor register tables. [DCC ordered operations](docs/dcc-operations.md) retain the vendor descriptor formats and Qualcomm author clarification; source ambiguity and reference timing are explicit. Their [actual validation receipts](evidence/qsee-dcc-20261123/publication.json) preserve the initial harness failure.

The [TCSR mutex](docs/tcsr-mutex.md), [AOP message SRAM](docs/aop-msgram.md), and [REFGEN request](docs/refgen.md) use selected captured DT fields and pinned driver sources. Each document distinguishes protocol evidence from unknown native initialization. The [integrated receipts](evidence/boot-resources-20261123-1005/publication.json) record actual checks and separately configured, unsuccessful native diagnostics.
