# Sources and provenance

The [cross-project hardware contract](docs/cross-project-hardware-contract.md)
links the current owner-device inventory, mainline driver roadmap and UniXNU
Route A work without equating source availability, model tests and device acceptance.

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
| Config NoC SBM | [noc-sbm-reference.md](docs/noc-sbm-reference.md), [fixed vendor source/actual-DT contract](evidence/wholeboard-20261123-a47/noc-sbm-source-contract.json), [independent DT geometry](evidence/wholeboard-20261123-a47/noc-sbm-independent-dt-layout.json); component CI and all five streams verified. [09c638 native](evidence/wholeboard-20261123-a47/native-09c638.json) recorded three CLR writes and zero SET writes; settled cold bits prove neither traffic drain nor consumer power success. The earlier 3b31e8 unused cold state remains separate |
| QSMMUv500 reference | [source contract](docs/smmuv500-source-contract.md) and [14-node actual DT slice](evidence/wholeboard-20261123-a47/smmuv500-dt-evidence-99ef.json); directly observed TCU VERSION fault, source-defined HALT/ECATS, [apps-only companion](docs/smmuv500-reference.md), and [exact Image IKCONFIG](evidence/wholeboard-20261123-a47/smmuv500-kernel-config.json); version, timing and firmware handoff remain unmeasured. Absent in 99ef; [3ef98 passed 36 checks and real migrations](evidence/wholeboard-20261123-a47/smmuv500-3ef98.json), with [separate native TBU resource progress](evidence/wholeboard-20261123-a47/native-3ef98.json) |
| TBU power ownership | [Four complete GDSC DT nodes](evidence/wholeboard-20261123-a47/smmuv500-tbu-gdsc-dt.json) and [current GCC ownership/source review](evidence/wholeboard-20261123-a47/smmuv500-tbu-gdsc-review.json); existing GCC domains 7–10 own the words, while traffic/power coupling is not added by the companion |
| TSENS24xx digital reference | [Two actual controllers and direct thermal references](evidence/wholeboard-20261123-a47/tsens-dt-evidence-3ef98.json), [pinned vendor/upstream source review](evidence/wholeboard-20261123-a47/tsens-dt-review-3ef98.json) and [model contract](docs/tsens24xx-reference.md); actual PA `0x0c222004` abort in [3ef98 native run](evidence/wholeboard-20261123-a47/native-3ef98.json). SROT DT size 4 remains distinct from sourced +4 access. Explicit temperature, boot mask, 1-ms timing, reset defaults and held-clear policy are reference assumptions. [52492a CI](evidence/wholeboard-20261123-a47/public-ci-52492a.json) and its [original TSENS 34/2/2/2 receipt](evidence/wholeboard-20261123-a47/tsens24xx-52492a.json) passed; the [same-binary full-DT run](evidence/wholeboard-20261123-a47/native-52492a.json) completed reference sampling on both controllers and next aborted in CPUFreq at `0x18591100` |
| CPUFreq HW EPSS reference | [Bounded original-DT resources](evidence/wholeboard-20261123-a47/cpufreq-dt-evidence-52492a.json), [fixed official driver/clock review](evidence/wholeboard-20261123-a47/cpufreq-dt-review-52492a.json) and [model contract](docs/cpufreq-hw-reference.md). Three domains, actual 4/3/1 CPU counter addresses and SPI30/31/19; all DT max_cores arguments are 4. Synthetic all-XO LUTs, voltages, limit-event policy and always-clocked virtual cycles are explicit references. [1fe4e1 actual 30/2/2/2 receipt](evidence/wholeboard-20261123-a47/cpufreq-hw-1fe4e1.json) and [all 74 gates](evidence/wholeboard-20261123-a47/public-ci-1fe4e1.json) passed. Same-binary full DT explicitly registers CPUFreq, then faults in L3 devfreq at 0x18590000 |
| VIDEOCC | [Kona-v2 contract](docs/videocc-sm8250.md), [generated register facts](docs/videocc-sm8250-registers.json) and [actual node](evidence/videocc-20261124-6a5/actual-dt-videocc-node.json); ten pinned primary sources, 55 unique words/26 windows and independent VIDEO GDSCs. [Run006](evidence/videocc-20261124-6a5/native-frontier-after-dispcc.json) registered Display CC before the actual VIDEOCC read32 abort at `0x0abf042c`; the earlier [deferred-probe source note](docs/videocc-source-note.md) remains historical |
| MPM sleep counter | [mpm2-sleep-counter.md](docs/mpm2-sleep-counter.md), pinned boot_stats read32 contract and actual TB371FC DT 32768-Hz declaration; explicit virtual-time, cold-epoch/reset and migration policies; [17-check/two-transfer receipt](evidence/wholeboard-20261123-a47/mpm2-0508.json) and [native read](evidence/wholeboard-20261123-a47/native-0508.json) |
| Generic Timer | [mmio-timer.md](docs/mmio-timer.md), Arm ARM external-register contract, exact-count arithmetic and retained regression controls |
| GENI | [geni-uart.md](docs/geni-uart.md), Linux FIFO/IRQ contract and actual clock/reset integration |
| GCC | [gcc-sm8250.md](docs/gcc-sm8250.md), pinned Linux-derived register manifest and clock-parent assumptions |
| GPUCC | [gpucc-sm8250.md](docs/gpucc-sm8250.md), pinned Kona Lucid PLL1, GMU, branch/reset contracts, actual Clock outputs and explicit analog/rendering limits |
| NPUCC | [npucc-sm8250.md](docs/npucc-sm8250.md), pinned Kona v2 three-bank PLL/RCG/branch/reset contracts, bounded CRC divider and unresolved-parent limits |
| CAMCC | [camcc-sm8250.md](docs/camcc-sm8250.md), actual Kona v2 compatible and run004 read32 at 0x0ad00000; eight pinned primary sources, four Lucid plus one Zonda PLL, exact 236-word whitelist, independent GDSC ownership and explicit unknown input clocks |
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

## L3 devfreq native boundary

The [exact-DT and production-symbol capture](evidence/wholeboard-20261123-a47/devfreq-dt-symbols-1fe4e1.json) has 6381 bytes, SHA256 `3b4d663de9e5ea33f5a86fc7d9f49f26223a6217657594423691d59f0ded124b`. It identifies one `qcom,devfreq-fw` parent, en-base `0x18590000/4`, ftbl-base `0x18590100/160`, perf-base `0x18590320/4`, and four software voters. The [source/DT review](evidence/wholeboard-20261123-a47/devfreq-dt-review-1fe4e1.json) pins `drivers/devfreq/devfreq_qcom_fw.c` at `94304e12a3509eac7b3716bf574ce0b1fcaacbae`, SHA256 `745457c2a20c90521772ef29c5f572570f481541d01ef427707d3da35bc5eb16`. Its first enable read agrees with the actual abort, but exact production C provenance remains source-correlated. No L3 hardware frequency table, firmware handoff or physical cache rate was measured.

The [implemented request-reference contract](docs/devfreq-qcom-fw-reference.md) keeps this source boundary: four synthetic all-XO rows, the driver's adjacent-frequency sentinel, guest software vote aggregation, and no hardware Clock/IRQ/timer/counter claims. The [independent model/test review](evidence/wholeboard-20261123-a47/devfreq-model-test-review.json) binds model commit `cfda4e5c9e17ca540425b809ab21dbb983d26eda` and final test commit `c1bd8c03a625115f037d09518d0ccaca48167e1c` (test SHA256 `8c4e49817f58cb2dca002d446486e75fee45c65ee2142963ed9112fbe24669cf`). The [actual b95f97 75-gate run](evidence/wholeboard-20261123-a47/public-ci-b95f97.json) and [raw 20/2/2/2 receipt](evidence/wholeboard-20261123-a47/devfreq-qcom-fw-b95f97.json) passed. The same accepted binary's full-DT printk explicitly registers all four voters and initializes the parent, then records a new configuration-NoC SBM worker abort at 0x01506080. Synthetic nominal rates remain model data, not physical cache or firmware measurements.

## Config NoC SBM and startup-input boundary

The [SBM source contract](evidence/wholeboard-20261123-a47/noc-sbm-source-contract.json)
pins OnePlus vendor commit `94304e12a3509eac7b3716bf574ce0b1fcaacbae` and
the RPMh NoC implementation selected by the recorded production configuration.
The actual DT supplies config NoC base 0x01500000, SBM offset 0x6000 and
ports 0..7. The source defines low CLR/SET/SENSE offsets 0x80/0x88/0x100.
Polling and software timeout do not establish physical response latency;
the model's 1000 ns, connected cold state and external stalls are explicit
reference policies. The vendor commit is correlated source, not a proven
byte-for-byte reproduction of the production kernel build.

The [actual fixed-symbol and fstab collector result](evidence/wholeboard-20261123-a47/boot-fstab-and-sbm-symbols-b95f97.json)
hashes the existing production kallsyms and locates the observed PC at
`msm_bus_noc_sbm_config+0x84`. The next distinct symbol is 0x1e8 bytes away;
that span is not an independent ELF function-size measurement. The same
bounded read-only collection reports sanitized mount requirements from the
already-hashed ramdisk slice, without opening any referenced partition or
key path. The separate [boot metadata receipt](evidence/wholeboard-20261123-a47/boot-ramdisk-inputs-b95f97.json)
records the full existing boot-file hash and raw init ELF metadata. Neither
collector executes init, authenticates AVB or proves complete Android inputs.

## Actual RPMh WAIT and complete-DT model evidence

The [3b31e8 native observations](evidence/wholeboard-20261123-a47/native-observations-3b31e8.json)
record the exact rejected DRV2 write, CPU7 PID1 abort and full printk context.
The [bounded Image disassembly](evidence/wholeboard-20261123-a47/rpmh-production-disassembly-3b31e8.json)
hashes the unchanged production Image and kallsyms, captures five named
functions (4584 bytes) and matches the fault instruction plus four preceding
instructions against the native Code dump. The Image offset mapping is
therefore checked independently of a guessed symbol address. These are
software execution facts; no hardware reserved-bit behavior was measured.

The [direct actual-DT root-model read](evidence/wholeboard-20261123-a47/actual-dt-model-3b31e8.json)
binds the full 5114262a DTB to the exact 37-byte property including its NUL:
`Qualcomm Technologies, Inc. kona QRD`. The [legacy fixture review](evidence/wholeboard-20261123-a47/legacy-userspace-board-model-review.json)
preserves its incompatible TB371FC-name predicate. Staging those old inputs
was not a guest execution or an eight-check pass.

The [independent source/production review](evidence/wholeboard-20261123-a47/rpmh-source-production-review-3b31e8.json) confirms the exact producer stack slot, 12-byte command copy and unnormalized WAIT accumulation in the unchanged production machine code. It distinguishes this local path equivalence from whole-kernel source provenance and from unmeasured silicon behavior.

## WAIT compatibility policy and fixture provenance

The [WAIT reference contract](docs/rpmh-wait-reserved-reference.md) separates
the confirmed production uninitialized-command path from unknown silicon
reserved-bit behavior. Only the explicit nondefault model policy permits
high-bit masking; its original low16 command semantics and busy rejection
remain required. The optional migration identity prevents importing a
different policy as if it were the same machine configuration.

The new `guest-init-actual-dt.c` and `boot_verdict_actual_dt.py` preserve every
non-model check of the existing eight-check ABI. The separate CI builder
records actual output hashes and symbols; its successful-build flag never
means guest execution. The source-pinned consumer and pure ZIP-stage helper
check the actual ELF/RET/CPIO relationships and bind any later execution to
the same source, binary, original Image and complete DT. Original Android
images and hardware endpoint behavior remain separate missing acceptance.

## Actual 77-gate checkpoint and unexecuted full-DT fixture

The [09c638 public receipt](evidence/wholeboard-20261123-a47/public-ci-09c638.json)
binds 73 component passes, four Linux passes and the exact board executable to
the [complete job-log audit](evidence/wholeboard-20261123-a47/official-ci-log-audit-09c638.json).
The [raw WAIT result](evidence/wholeboard-20261123-a47/rpmh-wait-09c638.json)
has 17 checks, one completed transfer and four rejection cases. The executed
artifact guard read sixteen original reports and thirty-seven actual stream
files. Its full raw JSON is preserved in gzip; the [index](evidence/wholeboard-20261123-a47/ci-artifact-audit-index-09c638.json)
is a derived navigation record with both compressed and original hashes.

The [original fixture manifest](evidence/wholeboard-20261123-a47/actual-dt-fixture-manifest-09c638.json)
and [executed stage audit](evidence/wholeboard-20261123-a47/full-dtb-fixture-stage-audit-09c638.json)
bind the new ELF, fixed native-tested arch probe and initrd to that same source
and ZIP. They prove build and content validation, not guest execution. The
subsequent [native diagnostic](evidence/wholeboard-20261123-a47/native-09c638.json)
has a direct read fault at BPS GDSC 0x0ad07004 in a deferred-probe worker.
The complete kernel log contains an Oops but no PID1 panic; the full-DT
eight-check consumer was not run. WAIT masking was enabled with zero usage,
so this trace does not establish it caused the later progress. No Android,
physical reserved-bit behavior or endpoint traffic success is inferred.

## Camera GDSC candidate and bounded GCC field correction

The [camera source contract](docs/camera-gdsc-source-contract.md) binds the next
increment to two read-only slices of the original full DT and fixed official
vendor files. It accounts for all 24 DT GDSC owners and selects only the six
same-protocol camera words. The second capture proves the parent resource
name `mmcx.lvl`; RPMh index 3 and its digital level behavior remain properties
of the existing explicit reference, not measured PMIC behavior.

The [camera protocol](docs/camera-gdsc-reference.md) specifies the independent
type, sparse geometry, unknown-input rules and 194-byte v1 state. The separate
[GCC correction](docs/gcc-sm8250.md#camera-ahb-retention-request-fields) follows
the exact `clk_set_flags` dispatch to bits 13/14 of `0xb008`; its generator now
checks four fixed inputs while still reproducing two output files. No parent
clock rate, retained camera memory or hidden hardware voter is inferred.
Formal execution evidence for this combined candidate is still required.

## MDSS GDSC and DISP_AHB bounded increment

The [MDSS contract](docs/mdss-gdsc-reference.md) consumes the existing
[remaining-frontier source analysis](evidence/wholeboard-20261123-a47/remaining-gdsc-frontier-contract.md).
It selects only the MDSS word at `0x0af03000`, preserves the camera type and
194-byte state, and shares the real mmcx output through a stateless splitter.
Its self proxy remains a software consumer rather than a hardware parent.
The GCC generator checks the fixed display branch declaration and stores
only its two source-supported retention requests. The unknown parent clock
and all other DISPCC addresses retain their old scope.
Independent 64-byte MDSS migration and real-wiring acceptance are required;
neither source generation nor component checks establish display or Android
execution.

## NPU GDSC and NPU_CFG_AHB bounded increment

The [NPU contract](docs/npu-gdsc-reference.md) binds the next actual native
read failure at `0x09981004` to the same fixed vendor regulator/branch
sources and original DT. That node declares no parent supply and no hardware
trigger, so the independent reference leaves parent inputs unconnected and
rejects hardware mode. A collapsed request is known-off; an asserted
request cannot produce ON feedback without an explicitly known parent.
There is no inferred mmcx link. NPUCC's 26 sparse windows and VMState stay
unchanged. The GCC generator adds only the two source-supported retention
request bits to its existing NPU_CFG_AHB word, keeping HWCG, status and the
unknown clock input. The 21-check gate and independent 64-byte migration
require actual compiled execution; source inspection is not an Android or
NPU workload result.

## VIDEO GDSC and VIDEO_AHB bounded increment

The [VIDEO contract](docs/video-gdsc-reference.md) follows the next actual
original-input failure at `0x0abf0d18` in run003, using the same fixed driver
and DT extraction. It gives four sparse words their own default-disabled
type and 142-byte v1 state. Only mvs0 and mvs1 have hardware mode declared;
the four domains share the evidenced mmcx parent as siblings. The stateless
fanout adds a third output while preserving camera and MDSS connections;
NPU remains unconnected. The generator adds only VIDEO_AHB retention bits
13/14 at `0xb004`, preserving descriptor 196, unknown external input 79,
all clock geometry and GCC v3. The 22-check compiled gate requires actual
MMIO, three completed transfers and real negative cases; codec, firmware,
DMA and Android execution are not established by these control words.

## Kona DISPCC increment after native run005

The [DISPCC contract](docs/dispcc-sm8250.md),
[register manifest](docs/dispcc-sm8250-registers.json) and
[generator](scripts/generate_dispcc_sm8250.py) pin nine primary files to
OnePlusOSS/android_kernel_oneplus_sm8250 commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`. The controller file SHA-256 is
`63eef01db44478e3be216025f672f73c652f9f3952f840bedf1791bc62d77061`;
the binding SHA-256 is
`a624fe5555fd4eddc10a46a80ff3a52da66ddb56f81567c7ea4df591f5949ac2`.
The other seven pins cover Lucid, RCG, branch source/header, regmap/generic
dividers and RPMh XO naming. The complete
[actual DT node](evidence/dispcc-20261124-e1a9/actual-dt-dispcc-node.json)
is copied from the existing public inventory, original DT SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
No unspecified PHY rate, PLL1-even provider, parentless critical-XO carrier,
opaque PLL STATUS, physical display execution or panel behavior is inferred.

## Kona-v2 VIDEOCC increment after native run006

The [VIDEOCC contract](docs/videocc-sm8250.md),
[register manifest](docs/videocc-sm8250-registers.json),
[generator](scripts/generate_videocc_sm8250.py) and
[regeneration verifier](scripts/verify_videocc_generation.py) pin ten complete
primary files to OnePlusOSS/android_kernel_oneplus_sm8250 commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`. The controller source SHA-256 is
`33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5`;
the clock/reset binding SHA-256 is
`0f51753e9eb497399d3c2d9a362e6d3ee7baf4f789b6e6628a7911b00f89d774`.
The other eight pins cover Lucid, RCG, branch source/header, regmap/generic
dividers, reset masked updates and RPMh XO provenance.

The [actual DT extraction](evidence/videocc-20261124-6a5/actual-dt-videocc-node.json)
records `qcom,videocc-kona-v2` at `0x0abf0000`, size `0x10000`, with
GCC binding 205, MX phandle 105 and MMCX phandle 104. The
[run006 receipt](evidence/videocc-20261124-6a5/native-frontier-after-dispcc.json)
binds unchanged original inputs and source `f209f65` to Display CC registration
at guest 3.503753 s and the first VIDEOCC read32 failure at `0x0abf042c` at
3.508680 s. It is a zero-LUN kernel-failure diagnostic, not Android acceptance.

Generation preserves PLL1's base-then-v2 fixup, the final `0x329a699c`
CONFIG_CTL_U1 value and both shared ARES/branch words. Four dividers use
the source-defined read-only `field + 1` encoding; neither `DIV2` names nor
the sleep frequency table establish additional input rates. Four VIDEO GDSC
owners remain separate. The original 36-group fixture passed at c232d14e. The expanded
[41-group compiled fixture](tests/test_videocc_sm8250.py) retains main VIC2
and the complete 474-byte wire contract. Together with the new DSI gates it
requires a new exact-source 89-component plus five-Linux receipt; no video
engine, firmware or Android success is inferred.


## DSI 7 nm v4.1 increment after native run007

The [DSI reference contract](docs/dsi-pll-7nm.md),
[register/clock manifest](docs/dsi-pll-7nm-registers.json),
[generator](scripts/generate_dsi_pll_7nm.py) and
[regeneration verifier](scripts/verify_dsi_pll_generation.py) pin thirteen
complete primary files at OnePlusOSS/android_kernel_oneplus_sm8250 commit
`98719830b0de5584f5588bbe9f4c8304d97a64c6`. The clock-controller pin
`94304e12...` lacks the required display directory and is not reused as DSI
provenance. The main PLL source SHA-256 is
`a286700822a0fb2fa1e9bff39cc6720295254c6137f29de65a37bcb819a8dd64`.

The [actual full-DT extraction](evidence/dsi-pll-20261124-6a5/actual-dt-dsi-pll-nodes.json)
identifies both `qcom,mdss_dsi_pll_7nm_v4_1` banks, their overlapping PHY/PLL
resources, shared MDSS GDSC and explicit downspread SSC properties.
The [hash-bound actual Image comparison](evidence/dsi-pll-20261124-6a5/actual-kernel-dsi-probe-instructions.json)
confirms only the bounded cold probe correspondence: PHY +0x18 bit6 and the
7 nm PLL +0x1b0 LOCK helper. It does not prove complete Lenovo/OnePlus kernel
source identity. Historical [run007](evidence/dsi-pll-20261124-6a5/native-run007-observation.json)
registered Video CC before this read fault and PID1 panic; its VID2 binary
is preserved separately from main VIC2 and is not reclassified as successful.

Two physical banks supply 52 normal/shadow CCF views. The eight exact leaves
per bank include the source-accessed PLL +0x260 once inside the unchanged PHY
aperture. Existing MDSS power state is the only shared supply observation.
The 250 ns bias interval is source-evidenced; the 64-XO-cycle lock deadline
is a declared digital reference choice. The sole source SPARE4 enable pulse
follows successful LOCK, which bounds output qualification. Unsupported
REFGEN readiness, dynamic sequencing, analog behavior and lane/panel activity
remain outside the implementation.

Three separately hash-verified [divider semantic sources](evidence/dsi-pll-20261124-6a5/divider-semantics-sources.json) establish generic-divider ceiling and fixed-factor floor behavior. These supplement the thirteen table-generator inputs; they are not represented as additional inputs to that two-output generation gate.

The same pinned graph permits DPHY pixel taps above 32-bit Hz, up to a 5 GHz
requested VCO. Only those two DISPCC inputs receive the wider period bound;
all other inputs retain their prior limit. Period quantization is preserved
through input reads, actual output Clocks and the unchanged DIS1 payload.
This does not establish whole-machine migration to an older receiver that
rejects these high-frequency inputs. [The source validation](evidence/dsi-pll-20261124-6a5/source-validation.json)
records thirteen exact pins, two reproduced outputs and deliberate input/output
negative checks; it is not a compiled-QEMU or native-boot receipt.

## Non-V2 DP 7 nm increment after the qualified DSI native run

The [DP reference contract](docs/dp-pll-7nm.md),
[register/clock manifest](docs/dp-pll-7nm-registers.json),
[generator](scripts/generate_dp_pll_7nm.py) and
[regeneration verifier](scripts/verify_dp_pll_generation.py) pin seventeen
complete primary inputs to OnePlusOSS/android_kernel_oneplus_sm8250 commit
`98719830b0de5584f5588bbe9f4c8304d97a64c6`. These cover the DP PLL topology and
utility, dispatch/resource helpers, bindings, named DTS resources, v420 shared
register client and orientation enum. Six separately byte-hashed
[rate-unit support sources](evidence/dp-pll-20261124-6a5/rate-unit-sources.json)
trace the kHz-valued CCF call chain. The generation gate still has seventeen
inputs and two outputs; it does not claim all twenty-three files as its inputs.

The [original DT and Image instructions](evidence/dp-pll-20261124-6a5/actual-kernel-dp-probe-instructions.json)
select `qcom,mdss_dp_pll_7nm` and confirm the cold 32-bit read at PLL `+0x158`,
then PHY `+0x28`. The [DP controller resource](evidence/dp-pll-20261124-6a5/actual-dp-controller-dt.json)
at `0x088ea040` has length `0x10`; its `+0x4` BIAS word aliases PLL `+0x44`.
The [actual PHY-version property](evidence/dp-pll-20261124-6a5/actual-dp-phy-version.json)
is `0x420`. These are bounded source/production correspondences, not proof of
whole Lenovo Image source identity or analog hardware behavior.

The [before-DP native receipt](evidence/dp-pll-20261124-6a5/native-frontier-before-dp.json)
binds the DSI `8e8e19a` binary to both DSI registrations and the first DP fault
at `0x088ea158`, guest `6.260019`. It remains a zero-LUN kernel-failure
diagnostic. The new DP tables own 73 words in 42 sparse leaves, preserve the
existing MDSS owner, and retain unknown carriers until qualification. The
[DP source validation](evidence/dp-pll-20261124-6a5/source-validation.json)
records exact regeneration and seventeen input-tamper rejections; compiled DP,
native DP and Android acceptance require their own subsequent evidence.
