# Public CI acceptance gates

The workflow in [tb371fc.yml](../../.github/workflows/tb371fc.yml) builds the
dedicated AArch64 machine on Ubuntu 24.04 and runs public tests in sequence.
The separate CPU job builds the `a77-test` configuration, which adds the
generic loader needed by its isolated `-M none` instruction probes.
Neither job depends on a production tablet Image or AOP firmware.

The workflow is an executable acceptance plan. Its presence does not mean a
particular commit has passed. Each run publishes the actual per-gate verdict,
command, stdout, stderr, input hashes and relevant fixture files. A missing
test binary, failed check, skipped C QTest, timeout or changed QEMU executable
fails the aggregate result and exits nonzero. Later component gates still run
after an earlier component failure so their independent results remain visible.

## GPUCC increment

The `gpucc-sm8250` gate runs `test_gpucc_sm8250.py` against the compiled binary. It requires 30 unique successful checks, two completed process migrations, two explicit profile rejections, VMState 1, 36 registers, seven sparse windows, 15 branches, five reset outputs and 15 input Clocks. The binary must remain unchanged. Its paused TCG check verifies that QTest fixture setters are absent; its original-kernel/GPU-execution/Android claims remain false. [The model contract](gpucc-sm8250.md) distinguishes the explicit reference profile from tablet handoff and analog timing. Commit `df667a3e8dbb31368ebbd6af1a7569a2b4c785d0` has now actually passed the complete 62 + 4 plan and the independent A77/UFS job; [its receipt](../evidence/wholeboard-20261123-a47/public-ci-df667.json) records its own source and binary. The earlier a813 result below remains frozen separately.

## NPUCC increment

The `npucc-sm8250` gate adds 37 unique checks against the compiled three-bank digital reference model, with two completed migrations and two profile rejections. It requires VMState 1, 95 registers in 26 sparse windows, three PLLs, six RCGs, 33 branches, seven resets, 12 input Clocks and 45 output Clocks. Shared ARES/CBCR words, the exact Q16 upper endpoint, unknown inputs and restored pending deadlines are included. [The contract](npucc-sm8250.md) does not claim NPU/DSP command execution or an implemented NPU GDSC. Commit `d79052f306ec37a797d05c9e4b2f8f7ea69c0675` actually passed this gate (37/37, two completed transfers, two real profile rejections), all 63 component gates, four Linux boots and the independent A77/UFS job. [The receipt](../evidence/wholeboard-20261123-a47/public-ci-d790.json) binds the inspected artifact and exact source; the older df667 receipt remains a separate 66-gate result.

## MPM counter and concurrent SMMUv2 increments

The `mpm2-sleep-counter` gate requires 17 actual compiled-device checks, two completed process migrations and one explicitly modified-stream rejection. It verifies a single four-byte read-only register at 32768 Hz, exact fractional boundaries, wrap, different destination time origins, reset continuity and paused host-time behavior. Its modified-frequency stream is a corruption test, not a migration between two supported configurations. [The contract](mpm2-sleep-counter.md) distinguishes cold/reset reference policies from measured hardware.

The `smmuv2-migration` gate preserves the default branch's 18-check test and version-2 geometry contract. It requires four recorded attempts: two successful transfers and two incompatible SMR-capacity rejections. The default branch and its original evidence remain intact; this combined source must pass its own 65 + 4 plan; previous successes do not attest the new combination. See [SMMUv2 migration](smmuv2-migration.md).

## NoC QoS increment

The `noc-qos` gate exercises the [four-word configuration contract](noc-qos-reference.md) on 47 actual-DT physical ports. The plan requires 20 successful checks, two completed same-profile transfers, two explicit profile mismatch rejections and two deliberately modified-stream rejections. The latter are corruption tests, not alternate supported board geometries. The gate checks 188 sparse four-byte windows, literal vendor RMW behavior, nonzero complete-word patterns, forbidden accesses, reset and raw destination observations before device MMIO. It does not establish NoC arbitration, bandwidth enforcement, SBM completion or native-driver progress. The complete 70-gate plan has now actually passed on 99ef; its original NoC receipt and matching native run are frozen below.

## Apps SMMUv500 increment

The new `smmuv500-reference` gate requires 36 successful checks, two completed migrations, two profile mismatch rejections and two single-field corrupted-stream rejections. It checks companion VMState 1 with architectural SMMUv2 VMState 3, one TCU, ten TBUs, 71 sparse windows and 484 mapped bytes. It targets the [apps-only TCU/TBU companion](smmuv500-reference.md), using the existing SMMUv2 stage-1 walker and an actual external IOMMU cache client. HALT, semaphore arbitration, captured ECATS requests, translation permissions/faults, reset and process migration are exercised against a compiled binary. The explicit non-v1 version and virtual delays are reference policies, not tablet measurements. Ordinary synchronous DMA denial is not a hardware stall queue. Native SMMU DMA and Android remain outside this component verdict. Commit `3ef98a8afb49a873d677288693d6491c50fd75e1` has now actually passed this complete 71-gate plan; its separate receipt below binds this new combination. The older 70-gate result remains tied to 99ef.

## TSENS24xx increment

The `tsens24xx-reference` gate runs `test_tsens24xx.py` and requires 34 unique successful checks, two completed process migrations, two profile mismatch rejections and two single-field corrupted-stream rejections. The [digital-reference contract](tsens24xx-reference.md) covers two controllers with 16 channels each: 114 words in six sparse windows, 456 mapped bytes, four IRQ outputs and a declared 1000000-ns sampling period. Each VMState-1 controller payload is 375 bytes and binds immutable profile, physical identity, sampling period and boot configuration.

The gate distinguishes live host temperature inputs from the captured in-flight round, checks VALID bit 21 and vendor TRDY bit 3 at actual timer boundaries, exercises signed thresholds, held-clear behavior, reset cancellation and late-callback policy, and checks all four actual GIC routes without output interception. Normal TCG retains the intended host environment input properties; they are not QTest-only fixtures. Migration verdicts require unmodified real source streams and raw restored state before TSENS MMIO. The default profile remains unknown. Temperature, boot sensor mask and timing are explicit reference choices; native probe progress, analog calibration, firmware equivalence and Android are not implied.

The first `298f21c` run [failed one of 68 component gates](../evidence/wholeboard-20261123-a47/public-ci-298f21-failure.json), after TSENS passed 17 checks. The narrow-access test expected the accepts-callback diagnostic `rejected`, but MemoryRegion correctly emitted `invalid size (min:4 max:4)`. The exact width diagnostic is now required without changing device code, the 34-check count or any migration requirements. The four Linux gates were skipped, and native execution was blocked by the failed aggregate receipt. The fixed source `52492a797d2acd9c918a8639564e8ff3e7beab3f` has now actually passed all 68 component and four Linux gates, plus the separate A77/UFS job. [Its receipt](../evidence/wholeboard-20261123-a47/public-ci-52492a.json) records the exact binary, eleven inspected component reports and seventeen actual migration files. [The original TSENS report](../evidence/wholeboard-20261123-a47/tsens24xx-52492a.json) passes 34 checks, two completed migrations, two profile rejections and two corruptions. Independent verification reads all five TSENS files, decodes six complete 375-byte controller sections, checks actual stopped source observations and verifies real receiver rejection diagnostics. The unknown-profile source stream is not counted as a third successful roundtrip. The same-binary [native run](../evidence/wholeboard-20261123-a47/native-52492a.json) completes TSENS reference sampling and next faults in CPUFreq at physical address `0x18591100`; native userspace and Android are still unpassed. The frozen 71-gate result below remains tied to `3ef98`.

## CPUFreq hardware EPSS increment

The `cpufreq-hw-reference` gate requires 30 unique successful checks, two
completed process roundtrips, two profile mismatches and two single-bit
corrupt-stream rejections. The [source-bounded reference](cpufreq-hw-reference.md)
has three actual-DT domains, eight per-core counter addresses, 263 words in
24 sparse windows, three IRQs and 84-byte per-domain VMState v1. Four explicit
all-XO synthetic LUT states preserve the real driver's duplicate-row sentinel,
voltage units and max_cores=4 in every domain.

PERF and host limit changes update real Clock outputs and exact fractional
virtual-cycle accounting. Tests cover actual GIC routes, strict access errors,
reset and raw restored QOM/Clock/IRQ state before the first target EPSS MMIO.
Source snapshots prove that pure QOM projection does not settle stored counter
anchors; `pre_save` must perform that settlement. Targets use different virtual
origins, then continue for one nanosecond before device MMIO. Physical LUTs,
analog voltage, TCG speed, native policy registration and Android are outside
the component verdict. This new combined 70+4 plan still requires actual CI;
the older 52492a 68+4 pass remains a separate frozen result.

## Gates

The current public plan has **74 gates: 70 component/parser gates and four
Linux boots**, plus the separate A77 and PCI UFS compatibility job. The archived
[47-gate isolated run](../evidence/verified-20261123-0820/public-47/result.json)
actually passed all gates in 73.261 seconds on board binary `87f52240ff55e0b60a87e9a0dc57b76dff75e653f3dbe1d5dbdc64b2b7d9ac89`.
The [same-boundary A77 and PCI results](../evidence/verified-20261123-0820/final-verification.json)
also passed 852 + 2 and 15 checks respectively. The older
[42-gate integrated run](../evidence/resume-20261123/result.json) retains its own frozen-binary scope. A subsequent
[45-gate run using that older binary](../evidence/public-ci-20261123/attempt-45-old-binary/result.json)
failed and is retained: shared plan and PDC/IPCC inputs changed before launch.
New entries require matching builds and an actual complete run; an earlier
pass does not attest newly added devices.


| Area | Required checks |
| --- | --- |
| Capture and result parsers | Strict CI verdict/plan checks, SMEM observer, AOP observer, board profile, machine validator, DT inventory, sysfs collector, guest-result ABI and TLMM map unit tests |
| Live board configuration | QMP CPU information and generated DT compared with the public tablet capture |
| GENI UART | 14 register/FIFO/IRQ/reset groups and 6 actual migration checks |
| GCC and GENI wiring | 19 GCC groups, 5 GCC migration checks, 6 clock/reset groups and 4 wired migration checks |
| Pinned Kona GCC supplements | Exact three-source regeneration of both generated files; CPUSS 11 checks and USB PIPE mux 15 checks, including real v3 transfers and explicit old-version rejection; no USB PHY execution claim |
| MPM sleep counter | 17 checks; exact 32768 Hz, one read-only word, reset continuity, two actual migrations and one deliberately modified-stream rejection |
| SMMUv2 migration | 18 checks; geometry-bound VMState 3 with default granule mask 1, two completed process migrations and two incompatible SMR capacity rejections |
| Apps SMMUv500 | 36 checks; one TCU / ten TBUs, HALT/cache invalidation and captured ECATS through the core SMMUv2 walker, two completed transfers, two profile mismatches and two corrupted-field rejections |
| TSENS24xx | 34 checks; two controllers / 32 channels, captured sampling, signed thresholds, four real GIC routes, two completed transfers, two profile mismatches and two corrupted-field rejections |
| CPUFreq EPSS | 30 checks; three domains / eight per-core counters, four synthetic all-XO states, actual Clock and IRQ behavior, two completed transfers, two profile mismatches and two single-bit corruptions |
| SMMUv2 explicit granules | 26 checks; opt-in 16/64 KiB ATS/DMA, permissions and faults, matching profile and two profile mismatches; no historical binary is fetched by this CI invocation |
| NoC QoS | 20 checks; 47 independent ports / 188 words, two completed transfers, two profile mismatches and two deliberately corrupted-stream rejections; no traffic or SBM synthesis |
| NPUCC | 37 checks across three physical banks, actual Clock outputs, source-bound CRC/dividers, two completed migrations and two profile rejections; full NPU/DSP execution remains outside scope |
| GPUCC | 30 actual digital PLL/RCG/branch/reset checks, two completed migrations and two profile rejections; actual Clock providers and sparse ownership boundaries |
| GPU CX and GX power domains | CX 26 checks with 9 migration attempts (6 completed, 3 rejected); GX 37 checks with 12 attempts (8 completed, 4 rejected); explicit digital reference states only |
| SMEM reference handoff | 22 checks and 4 real migration cases; the separate stopped-production-kernel observer never substitutes for a native run |
| AOP SRAM and reference peer | Raw SRAM 10 checks retained; explicit reference peer 30 groups, 6 completed migrations and 2 same-binary profile rejections; actual IPCC/GIC routing, supported vote state and no fabricated unknown-service ACK |
| TCSR and REFGEN | TCSR 24 checks and REFGEN 16 checks, including actual state transfer and rejected reference mismatches |
| Counter arithmetic | 1,848 independent arbitrary-precision floor/ceiling/overflow reference cases |
| MMIO Generic Timer | 10 exact and 10 legacy groups; exact and legacy four-check migrations; both incompatible-mode rejection directions |
| CPU counters and timers | Independent icount rate measurement in both modes; physical/virtual CVAL, signed TVAL, mask and wrap behavior |
| CPU timer migration | Two real QEMU processes, guest RAM checkpoints, counter/compare/offset continuity and actual guest expiry; an uninstrumented control reuses the same ELF bytes |
| CPU timer output wires | 16 GIC input-line observations in each of real-time exact, icount exact and icount legacy mode |
| SMMUv2 | 23 C QTests with no skips |
| RPMh | 22 reference-profile tests, including the synthetic command-db parser cases, and 5 migration checks |
| Display RSC | 11 shared-service and real SPI 129 checks, plus 6 actual component migration checks |
| UFS | 56 MMIO/PHY/clock/DMA/SCSI/WriteBooster/hibernation checks against a synthetic disk, including absent-LUN responses and DMA bounds |
| Watchdog | 16 register/timing/action/negative groups and actual pending-bite migration |
| IMEM and PS_HOLD | 7 groups covering SRAM boundaries, byte order, guest-owned handoff fields, reset retention, digital output, invalid widths and an actual two-process migration |
| TLMM | 25 C QTests, including the explicit UFS reset boot handoff, and 15 checks across four actual migrations |
| PDC | 18 C QTests and 10 migration checks in the expanded plan; earlier 17/9 results retain their original scope |
| IPCC | 16 message, real GIC SPI229, mask, clear, negative and migration checks; no fabricated remote firmware reply |
| DCC | 17 original checks plus 16 ordered WRITE/LOOP/RMW, bounds, side-effect and pending-operation migration checks |
| QSEE notification | 12 state/real SPI348/349/invalid-access checks and 6 actual migration checks; no secure-service replies |
| SPMI arbiter | 25 checks against explicit reference fixtures; missing physical PMIC behavior is not inferred from a passing arbiter test |
| SPMI → PDC → GIC | 8 checks with the real parent IRQ wiring preserved and exactly one actual migration, including the remaining transaction deadline |
| Clean Linux user space | 8 PID 1 checks with 8 CPUs, 8 GiB RAM, normal GENI console handoff and a native AArch64 counter probe; a newly created synthetic UFS disk is attached |
| Clean Linux empty UFS bus | The same 8 PID 1 checks and shutdown with UFS enabled and no logical unit attached; missing-LUN recovery is an independent required gate |
| Clean Linux UFS and SMMU | 19 guest checks plus a host check of the bytes written to the synthetic disk |
| Clean Linux UFS, SMMU and RPMh | 23 guest checks, three host RPMh observations and the host disk payload comparison; real RSC/clock drivers and completion interrupt with an explicit synthetic reference command database |
| Isolated Cortex-A77 | 852 guest checks across 9 feature/cache-ID configurations, plus 2 invalid-configuration rejections |

Component tests generally use 128 MiB to keep their fixtures small. The four
Linux integration gates explicitly use 8 GiB and eight CPUs. Timer and other
unrelated-device component tests disable UFS; the UFS and Linux I/O gates
exercise the implemented controller and its GCC/SMMU connections.

The Linux input comes from the pinned, unpatched source archive verified by
[build_smoke_kernel.sh](../scripts/build_smoke_kernel.sh). Its companion
`build.json` must identify that archive, contain no applied patches, and match
the Image hash before the CI runner accepts it. The kernel recipe also checks
the effective Kconfig requirements before compilation.

## Reproduction

Build QEMU and the C tests, then run the component suite:

```sh
BUILD_DIR=/work/ci-build JOBS=2 sh tb371fc/scripts/build.sh
ninja -C /work/ci-build -j2 \
  tests/qtest/tb371fc-smmuv2-test tests/qtest/tb371fc-tlmm-test \
  tests/qtest/tb371fc-pdc-test
python3 tb371fc/scripts/ci_device_tests.py \
  --qemu /work/ci-build/qemu-system-aarch64 --build-dir /work/ci-build \
  --suite components --out /work/ci-components
```

Build the public kernel independently and run all four Linux gates:

```sh
SMOKE_JOBS=2 bash tb371fc/scripts/build_smoke_kernel.sh /work/ci-linux
python3 tb371fc/scripts/ci_device_tests.py \
  --qemu /work/ci-build/qemu-system-aarch64 --build-dir /work/ci-build \
  --kernel /work/ci-linux/artifacts/Image \
  --suite linux --out /work/ci-linux-results
```

Output directories must be new. `--suite all` runs both suites in order;
`--list` prints the commands without executing them. The runner creates short
temporary fixture paths to avoid the Linux Unix-socket pathname limit, retains
those paths for diagnosis, and copies regular artifacts into the requested
output directory. It never follows a fixture symlink or copies a socket.

The aggregate `result.json` records every return code and verdict, the QEMU
hash before and after the run, source commit and dirty status. `sha256.json`
covers the retained files. No private device asset is uploaded by this plan.

The verdict reader requires a true boolean aggregate, the exact check count,
no contradictory individual verdict, and strictly typed required metadata.
C TAP output must contain exactly one matching plan and unique consecutive
test numbers; SKIP and TODO do not satisfy a gate. The verdict and plan unit suite covers these rejection paths and the registered plan contracts.

## What a pass means

The large-core model is an architectural Cortex-A77 implementation. The
small cores remain A55 substitutes for the measured Qualcomm identities.
The timer rate is checked against controlled QEMU virtual time, including an
instruction-count reference. These results do not measure emulator wall-clock
speed or prove cycle-accurate silicon timing.

RPMh tests use the explicitly synthetic reference command database. Digital
clock, reset, GPIO, DMA, timer and power-vote behavior does not establish
analog PMIC behavior, firmware equivalence or every peripheral mode.

The clean kernel boots with the machine-generated device tree and an
instrumented test initramfs. The UFS I/O gates finish with host QMP quit after
checking its guest verdict and disk contents; it does not claim a guest
shutdown. The two console/user-space gates require a real QMP SHUTDOWN event with guest=true and reason=guest-shutdown, in addition to exact guest checks and exit zero. Production GDB validation has its own stopped-checkpoint and guest-shutdown contract.

A passing public job does not establish stock Android boot, complete native
device-tree boot, Adreno graphics, modem, camera, audio, sensors, or full-tablet
equivalence. Those claims require their own implemented devices and actual
end-to-end evidence.

The route receipt must contain literal true aggregate verdicts, eight checks, an explicit false output-interception flag, and exactly one migration record. Missing or incorrectly typed secondary evidence lists are rejected. The verdict and plan tests include these failure cases.

## Display RSC integration

The expanded [49-gate run](../evidence/display-rsc-20261123/public-49/result.json) passed all gates on the matching frozen binary. The preceding 47-gate receipt remains an immutable baseline. [53 additional RPMh checks](../evidence/display-rsc-20261123/tests-final.json) include the private partial-file clock suite; those six checks are not public CI inputs. That full-DT run failed at qsee_irq_probe; the subsequent QSEE/DCC run below advances past that write and remains a failed native boot.

## QSEE and DCC integration

The [52-gate run](../evidence/qsee-dcc-20261123/public-52/result.json) passed on binary SHA256 51fe61d35213a6602a8eaa865faba04bc85f88df341947ca8a2e6300a8ab5be6. Its [51 targeted checks](../evidence/qsee-dcc-20261123/workflow.json) also passed. The initial QSEE migration harness attempted a non-increasing QTest clock before transfer; that failed receipt and the one-nanosecond harness correction remain archived. Device C and the binary were unchanged. The native diagnostic still fails in AOP mailbox and REFGEN accesses; observer exit 0 is not a guest boot verdict.

## TCSR mutex, AOP SRAM and REFGEN integration

The [55-gate frozen run](../evidence/boot-resources-20261123-1005/public-55/result.json) passed on binary SHA256 2173fa4da68f8f769c53942b056124c5621e4714652c7f572deacc9440ec09cf.
The [targeted run](../evidence/boot-resources-20261123-1005/workflow.json) passed all 50 checks:

| Suite | Checks | Actual migration attempts |
| --- | --- | --- |
| TCSR mutex | 24 | 2 success; 1 expected profile mismatch rejection |
| AOP message SRAM | 10 | 1 success |
| REFGEN request | 16 | 4 success; 2 expected profile mismatch rejections |

All 22 CI-plan unit tests also passed. This validates the documented component protocols and storage.
The production kernel/full DT panics in both unknown and explicit-reference REFGEN configurations; observer exit 0 is not boot success.

## Frozen unified a813 result

Commit `a8136d1b1871d5a90aced67cc269dfb312be5c65` actually passed 61 component/parser gates and 4 clean-Linux boots, plus the independent A77/shared-UFS job. [The receipt](../evidence/wholeboard-20261123-a47/public-ci-a813.json) binds the actual ZIP member SHA256, both clean-source aggregate receipts, exact gate order and selected component details. The two observer unit suites ran 17 and 14 mock-monitor tests; [the separate production-kernel run](../evidence/wholeboard-20261123-a47/native-a813.json) records their real stopped-QEMU observation. That native run registered GCC clocks and then panicked at GPUCC `0x03d90100`; full native-DT userspace and Android remain unpassed.

## Frozen GPUCC-inclusive df667 result

[The 66-gate receipt](../evidence/wholeboard-20261123-a47/public-ci-df667.json) records 62/62 component gates, 4/4 Linux boots and the separate A77/UFS success. Actual ZIP member SHA256 is `654d2eed0cd057028b68f1dba87e4bde49d6e60431538ed991f624de9dba08a0`. The first db3 GPUCC test failed because core invalid-access diagnostics were not enabled; [that failure and the unchanged-binary corrected test](../evidence/wholeboard-20261123-a47/gpucc-fixture-fix.json) remain distinct from this later complete run. [The matching production-kernel/full-DT run](../evidence/wholeboard-20261123-a47/native-df667.json) registers GCC and GPUCC, keeps SMEM 23/23 and the AOP reference exchange, then panics at NPUCC `0x09980000`. GPU command execution, full native-DT userspace and Android remain unpassed.

## Frozen NPUCC-inclusive d790 result

[The 67-gate receipt](../evidence/wholeboard-20261123-a47/public-ci-d790.json) records 63/63 component gates, 4/4 Linux boots and independent A77/UFS success. Actual board ZIP member SHA256 is `5e508e06b6ed66b264c1559a3e29a7ea9e9d6574950db5a81e5cf022252b29ba`. Both aggregate source-clean receipts and six selected component receipts were read from that ZIP. [The matching production-Image/full-DT run](../evidence/wholeboard-20261123-a47/native-d790.json) registers GCC, GPUCC and NPUCC, retains SMEM 23/23 and the AOP reference exchange, then faults in `boot_stats_init+0x14c`. Pinned public source and the actual DT identify the MPM counter candidate at `0x0c221000`, 32768 Hz; the recorded fault VA was not independently translated. VIDEOCC was deferred for an unavailable bandwidth-voting bus driver. Full native-DT userspace, Android and GPU/NPU command execution remain unpassed.

## Frozen MPM/SMMU-inclusive 0508 result

[The 69-gate receipt](../evidence/wholeboard-20261123-a47/public-ci-0508.json) records 65/65 component gates, 4/4 Linux boots and independent A77/UFS success for `0508fe9d5382b3e94ee0664012ff1e6f0457e5b7`. The actual board ZIP binary hashes to `f3582740b3ccbe3689181f796412d8d28cdfc92ad0ea48e639334dd464c6548b`. Both clean-source aggregate receipts, eight selected component receipts and all three MPM migration files were read from the downloaded artifact. The MPM [original JSON](../evidence/wholeboard-20261123-a47/mpm2-0508.json) records 17/17 checks, two completed transfers and one deliberately corrupted-frequency rejection. SMMUv2 records 18/18 checks, two completed transfers and two incompatible-SMR-capacity rejections; rejection senders report failed, not completed. The actual CI unit suites passed 29, 17 and 14 tests. The earlier [local preflight](../evidence/wholeboard-20261123-a47/development-validation-0508.json) preserves its one environment-denied AF_UNIX test; it is not substituted for formal CI.

[The matching production-Image/full-DT run](../evidence/wholeboard-20261123-a47/native-0508.json) retained GCC/GPUCC/NPUCC registration, SMEM 23/23 and the supported AOP reference exchange, then actually printed the MPM timestamp and 32768-Hz frequency. Its next fatal is `msm_bus_noc_qos_init+0x48`; logging directly identifies the rejected physical read at `0x016e9008`. The exact Image's embedded configuration and [actual DT layout](../evidence/wholeboard-20261123-a47/noc-layout-0508.json) select RPMh NoC QoS Aggre1 port 7. Full native-DT userspace and Android still did not pass. The diagnostic's exit zero is observer completion, not a successful guest boot.

## Frozen NoC-inclusive 99ef result

[The 70-gate receipt](../evidence/wholeboard-20261123-a47/public-ci-99ef.json) binds `99ef6052e3e10e84c67f3482471f4bde12ab295a` to actual 66/66 component passes, 4/4 Linux boots and the separate A77/UFS success. QEMU SHA256 is `226f826f02a97978f9f0afbcc1241dbcd6c25faca4840f8e1f72cee22f3d0000`; the downloaded ZIP hash, unique binary member, clean-source aggregate receipts and nine selected component reports were checked. All 65 previous component names remain in their original order. The CI unit suites actually ran 29, 17 and 14 successful tests.

The [original NoC JSON](../evidence/wholeboard-20261123-a47/noc-qos-99ef.json) passed 20 checks in 5.372 seconds, two completed transfers, two real profile mismatch rejections and two single-field corruption rejections. The four actual stream files were read from the ZIP and independently hashed; both complete transfers contain all 47 physical device sections and 188 stored words. The original report bytes are preserved without text normalization. Three MPM stream files were also rechecked for this binary. The separate A77 job passed 852 guest checks over nine variants, two rejected configurations and 15 shared PCI UFS cases.

[The matching production-Image/full-DT run](../evidence/wholeboard-20261123-a47/native-99ef.json) retained the earlier clock, MPM, SMEM and AOP milestones. A [strictly stopped observer](../evidence/wholeboard-20261123-a47/native-noc-observer-99ef.py) found 47/47 NoC addresses and 46/46 nondeferred DT configurations matching; deferred IPA remained zero. This is configuration evidence, not bandwidth or SBM execution. The next actual fatal is the directly logged read at `0x15182018` in `qsmmuv500_arch_init+0x88`. The [bounded DT slice](../evidence/wholeboard-20261123-a47/smmuv500-dt-evidence-99ef.json) identifies apps TCU VERSION; full native-DT userspace, Android and native SMMU DMA remain unpassed.

## Frozen SMMUv500-inclusive 3ef98 result

[The 71-gate receipt](../evidence/wholeboard-20261123-a47/public-ci-3ef98.json) binds `3ef98a8afb49a873d677288693d6491c50fd75e1` to actual 67/67 component gates, four Linux boots and the separate A77/UFS success. QEMU SHA256 is `703af9a899455c1c32636a9e3535294031c93cd9624fdc193177c61cb8ff7600`. Both aggregate receipts attest clean source and an unchanged executable. All 66 previous component names retain their order. The 29/17/14 unit suites and architectural SMMUv2's 23 C cases/18 migration checks remain successful.

The [byte-preserved SMMUv500 report](../evidence/wholeboard-20261123-a47/smmuv500-3ef98.json) passed 36 checks in 10.862 seconds: two completed migration targets, two profile rejections and two deliberately corrupted-field rejections. All five actual stream files were read and hashed from the ZIP; the two complete 744-byte device sections were independently decoded, including all ten pending TBU states. The third unmodified stream exists for reverse-profile rejection and is not counted as a third completed roundtrip. Seven MPM/NoC streams and ten selected component reports were also checked. The independent CPU job passed 852 guest checks, two invalid configurations and 15 PCI UFS regressions.

[The same-binary native run](../evidence/wholeboard-20261123-a47/native-3ef98.json) passed the former VERSION abort, registered TBU resource clients and continued into SCSI/USB initialization. Its 10/10 stopped TBU geometry observations and cold request state are distinct from native DMA execution. The next directly logged abort is TSENS SROT +4 at `0x0c222004`; full native-DT userspace and Android remain unpassed.

## Concurrent default-branch granule integration

The merged `smmuv2-granules` gate retains the original 26-check invocation and three migration attempts. Its report must explicitly state `legacy_v2_transfer_tested=false`; the public gate does not fetch a historical binary. Default-branch b5f4415 and 69ca28 records are preserved byte for byte, including the failed missing-library attempt and later actual v2-to-v3 extended run. Their successes apply to their own source/binaries. This branch's default 4 KiB SMMUv500 companion gate keeps 36 checks and now verifies the actual core v3 headers/profile in its source streams, while companion VMState remains v1. A fresh unified run is required after CPUFreq and these changes are combined.

## First integrated EPSS build failure

Run [45237654368](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45237654368), source `530909a61f59ac6bf4a644ac0d13f4d1fc9c95ea`, failed while compiling the board translation unit: the new descriptor member `first_cpu` expanded the existing CPU-list macro from `hw/core/cpu.h`. No component or Linux gate ran. The failed board artifact contains only `qemu-build.log`; the exact-artifact guard stopped before installation or native execution. The separate CPU job actually passed 852 guest checks, two rejected configurations and 15 UFS regressions. [The failure receipt and full official board log](../evidence/wholeboard-20261123-a47/public-ci-530909-build-failure.json) are retained.

The reviewed fix `4dbcb3e07a3c844e75043c4589a63125906174ae` changes only that member's name to `first_cpu_index`. Its type and position, all model C, VMState, acceptance scripts and the 70 + 4 plan are unchanged. Static review is not a replacement for the next formal CI run.
