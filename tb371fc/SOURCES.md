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
| NoC QoS | [noc-qos-reference.md](docs/noc-qos-reference.md), pinned RPMh programming sequence and bounded DT; [actual 20-check receipt](evidence/wholeboard-20261123-a47/noc-qos-99ef.json) and [47-port native snapshot](evidence/wholeboard-20261123-a47/native-99ef.json); no traffic or SBM claim |
| QSMMUv500 reference | [source contract](docs/smmuv500-source-contract.md) and [14-node actual DT slice](evidence/wholeboard-20261123-a47/smmuv500-dt-evidence-99ef.json); directly observed TCU VERSION fault, source-defined HALT/ECATS, [apps-only companion](docs/smmuv500-reference.md), and [exact Image IKCONFIG](evidence/wholeboard-20261123-a47/smmuv500-kernel-config.json); version, timing and firmware handoff remain unmeasured. Absent in 99ef; [3ef98 passed 36 checks and real migrations](evidence/wholeboard-20261123-a47/smmuv500-3ef98.json), with [separate native TBU resource progress](evidence/wholeboard-20261123-a47/native-3ef98.json) |
| TBU power ownership | [Four complete GDSC DT nodes](evidence/wholeboard-20261123-a47/smmuv500-tbu-gdsc-dt.json) and [current GCC ownership/source review](evidence/wholeboard-20261123-a47/smmuv500-tbu-gdsc-review.json); existing GCC domains 7–10 own the words, while traffic/power coupling is not added by the companion |
| TSENS24xx digital reference | [Two actual controllers and direct thermal references](evidence/wholeboard-20261123-a47/tsens-dt-evidence-3ef98.json), [pinned vendor/upstream source review](evidence/wholeboard-20261123-a47/tsens-dt-review-3ef98.json) and [model contract](docs/tsens24xx-reference.md); actual PA `0x0c222004` abort in [3ef98 native run](evidence/wholeboard-20261123-a47/native-3ef98.json). SROT DT size 4 remains distinct from sourced +4 access. Explicit temperature, boot mask, 1-ms timing, reset defaults and held-clear policy are reference assumptions. [52492a CI](evidence/wholeboard-20261123-a47/public-ci-52492a.json) and its [original TSENS 34/2/2/2 receipt](evidence/wholeboard-20261123-a47/tsens24xx-52492a.json) passed; the [same-binary full-DT run](evidence/wholeboard-20261123-a47/native-52492a.json) completed reference sampling on both controllers and next aborted in CPUFreq at `0x18591100` |
| CPUFreq HW EPSS reference | [Bounded original-DT resources](evidence/wholeboard-20261123-a47/cpufreq-dt-evidence-52492a.json), [fixed official driver/clock review](evidence/wholeboard-20261123-a47/cpufreq-dt-review-52492a.json) and [model contract](docs/cpufreq-hw-reference.md). Three domains, actual 4/3/1 CPU counter addresses and SPI30/31/19; all DT max_cores arguments are 4. Synthetic all-XO LUTs, voltages, limit-event policy and always-clocked virtual cycles are explicit references. The 30-check component and 74-gate combination require a new actual run |
| VIDEOCC preparation | [fixed vendor source note](docs/videocc-source-note.md) and [actual v2 DT binding](evidence/wholeboard-20261123-a47/videocc-dt-evidence.json); probe was deferred, not the next fatal |
| MPM sleep counter | [mpm2-sleep-counter.md](docs/mpm2-sleep-counter.md), pinned boot_stats read32 contract and actual TB371FC DT 32768-Hz declaration; explicit virtual-time, cold-epoch/reset and migration policies; [17-check/two-transfer receipt](evidence/wholeboard-20261123-a47/mpm2-0508.json) and [native read](evidence/wholeboard-20261123-a47/native-0508.json) |
| Generic Timer | [mmio-timer.md](docs/mmio-timer.md), Arm ARM external-register contract, exact-count arithmetic and retained regression controls |
| GENI | [geni-uart.md](docs/geni-uart.md), Linux FIFO/IRQ contract and actual clock/reset integration |
| GCC | [gcc-sm8250.md](docs/gcc-sm8250.md), pinned Linux-derived register manifest and clock-parent assumptions |
| GPUCC | [gpucc-sm8250.md](docs/gpucc-sm8250.md), pinned Kona Lucid PLL1, GMU, branch/reset contracts, actual Clock outputs and explicit analog/rendering limits |
| NPUCC | [npucc-sm8250.md](docs/npucc-sm8250.md), pinned Kona v2 three-bank PLL/RCG/branch/reset contracts, bounded CRC divider and unresolved-parent limits |
| SMMUv2 | [Architectural contract](docs/smmuv2.md), [migration geometry and v3 granule equality](docs/smmuv2-migration.md), and [explicit 16/64 KiB primary sources and vectors](docs/smmuv2-granules.md). Default remains 4 KiB; original default-branch [b5f4415/69ca28 receipts](evidence/smmuv2-granules-20261123/publication.json) retain their own binary scope. The merged core preserves apps companion hooks and requires a new combined run |
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
