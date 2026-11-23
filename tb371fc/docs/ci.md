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

The `npucc-sm8250` gate adds 37 unique checks against the compiled three-bank digital reference model, with two completed migrations and two profile rejections. It requires VMState 1, 95 registers in 26 sparse windows, three PLLs, six RCGs, 33 branches, seven resets, 12 input Clocks and 45 output Clocks. Shared ARES/CBCR words, the exact Q16 upper endpoint, unknown inputs and restored pending deadlines are included. [The contract](npucc-sm8250.md) does not claim NPU/DSP command execution or an implemented NPU GDSC. Registration of this gate is a plan; the frozen df667 result below applies to the preceding 66-gate source.

## Gates

The current public plan has **67 gates: 63 component/parser gates and four
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
