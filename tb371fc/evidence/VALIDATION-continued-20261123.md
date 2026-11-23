# Continued validation ledger — 2026-11-23 UTC

The implemented board now contains architectural A55/A77 CPU groups, exact
counter conversion, GENI/GCC, UFS/PHY, SMMUv2, optional RPMh, TLMM, watchdog,
IMEM/PS_HOLD, bounded DCC ordered operations, PDC/IPCC, explicit SPMI reference
subsets, QSEE notification banks, TCSR mutexes, AOP SRAM and REFGEN requests.
Upstream Linux performs real translated UFS I/O.
The unmodified production kernel also executes the validation initramfs with
the generated subset DT. **Full native-DT and stock Android boot remain
unaccepted.**

This ledger retains independent frozen-build series from both development
histories. The [frozen12 run](resume-final-20261123/result.json),
[08:20 isolated run](verified-20261123-0820/final-verification.json), and later
49-, 52- and 55-gate integrations each identify their own source, binary and
input hashes. A passing result applies only to that recorded run. Component
receipts do not all use the same executable; a partial or failing receipt is
not an aggregate pass, and check counts are not hardware-completion percentages.

## Frozen12 public-input run (46 gates)

The [final 46-gate receipt](resume-final-20261123/result.json) passed
**46/46 gates** on frozen build12, SHA256
`178bace28c84bced9471b824d413feb18e76ddb15212ec32412ad7a7ec32a675`, in 74.755 seconds.
The executable was unchanged across the run. Its clean, unpatched Linux
6.12.114 Image SHA256 is
`c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`.
The 42 component/validation gates and four Linux gates include SPMI 25,
IPCC 16, PDC 18 C tests plus ten migration checks, and DCC 17 groups.
No missing or failed gate is waived, and no private production Image, full
native DT or AOP firmware is needed by this public-input run.

A [separate production-kernel run](production-gdb-frozen12-20261123/result.json)
on the same QEMU build passes eight PID1 checks and actual guest-requested
power-off with the unmodified production Image, generated DT and validation
initramfs. This is distinct from the public suite and from the
[full native-DT reference-profile diagnostic](native-dt-spmi-reference-20261123/result.json),
which remains a failed boot at `qsee_irq_probe+0x140`.

## Frozen12-associated generic CPU and PCI runs

After the incremental generic build, the [final UFS PCI replay](compat-final-20261123/ufs-pci/result.json)
passes **15/15 cases, all executed without skips** on generic ARM virt/PCI.
The [final A77 replay](compat-final-20261123/a77/result.json) passes **852 guest checks across nine configurations**
and rejects **two invalid configurations** as required. Both use the same copied generic QEMU,
SHA256 `c06bb42b52bc47839fbe40eb5a247c04d70b64840411af6efff1497d0a1d16c0`;
the PCI qos-test SHA256 is `4a4ecc52deefed0eeec3d3f632c53533a6960a6d4faaf0cd5d44c2fc5e4653be`.
These are separate from the `178bace2…` TB371FC board executable and its 46-gate result.

The [source provenance](compat-final-20261123/a77/source-provenance.json) records a successful
incremental build, 4,444 matching files under `hw/include/target/configs`,
1,088 matching actual target source inputs, and the keymap CSV verified against
its wrap-pinned Git blob. It reports no content mismatch and unchanged binaries
and relevant sources after testing. The published receipts contain synthetic
case/build logs and hashes, with no QEMU, qos-test or guest ELF binaries.

The then-current plan contained 42 component/validation gates and four
Linux boots. The separate 08:20 series below adds the eight-check real
SPMI/PDC/GIC route as another required gate; its 47-gate result and generic
CPU/PCI binary hashes must not be substituted for the frozen12 receipts.

## 08:20 isolated-build verification (47 public gates)

The [isolated final verification](verified-20261123-0820/final-verification.json)
records **47/47 public gates, 852 A77 guest checks across nine variants
plus two invalid-configuration rejections, and 15/15 PCI UFS tests**.
The board suite took 73.261 seconds on SHA256
`87f52240ff55e0b60a87e9a0dc57b76dff75e653f3dbe1d5dbdc64b2b7d9ac89`;
A77/PCI used `2605ddb7a6c503cff9532bcd1f24a0945b76f5f148149170ee3af7813beeec29`.
Source and binary hashes were unchanged during verification.
The frozen source snapshot is `9a80cddf1bd6ad8ecdbba34fa164acb5524e206b`.

The [original sequential aggregate](verified-20261123-0820/initial-sequential-verification.json)
remains false because its first PCI discovery used a noncanonical executable
basename and yielded zero tests. The exact same binary bytes under the
canonical basename passed all 15 tests in 8.643 seconds. The separate final
receipt cites both attempts; no historical verdict was overwritten.
A [post-verification DCC formatting record](verified-20261123-0820/dcc-style-only-after-verification.json)
records the one whitespace-only source cleanup without relabelling the
frozen-run hashes.

## Earlier integrated runs and retained failures

The earlier [42-gate result](resume-20261123/result.json) passed all gates in
41.861 seconds with exit zero, four real Linux boots and an unchanged QEMU
SHA256 `5a5c76062c5345d2be5960932a3a7ccefc73c6523e1531ea1b65088f5cdad555`.
The clean Linux Image was
`c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40`.
The archive retains exact aggregate/per-gate reports, stdout/stderr, selected
serial logs, the actual runner source and 69 stable existing suite source hashes.
No private kernel, DT or AOP input was needed by this run.

The next [45-gate attempt on the older binary](public-ci-20261123/attempt-45-old-binary/result.json)
is a retained **failure: 40/45, exit one**. Shared source changes added IPCC,
changed PDC counts, and left a plan/unit mismatch before launch; the old binary
did not contain IPCC. A separate RPMh Linux attempt also stopped at QMP
greeting acquisition before guest checks. No missing or failed gate is waived.
New source requires matching binaries and a new complete run.

The [independent isolated A77 receipt](cpu-a77-current-20261123/result.json) passed
852 guest checks plus two invalid-configuration rejections on its own
`c06bb42b52bc47839fbe40eb5a247c04d70b64840411af6efff1497d0a1d16c0` binary.
The [first PCI UFS compatibility listing](ufs-pci-20261123/initial-listing-failure/result.json)
failed before any of its 15 cases ran: the runner did not accept the TAP
comment prefix on listed test paths. It is retained as a harness failure,
not a passing storage result. The [frozen12-associated corrected replay](compat-final-20261123/ufs-pci/result.json)
executes and passes all 15 cases; the separate 08:20 discovery failure and
canonical-name correction are recorded above.

The 08:20 CI plan registered 47 gates: 43 component/parser gates and four
Linux boots, with PDC 18/10, IPCC 16, reference SPMI 25 and the real SPMI/PDC/GIC
route 8. All were exercised in that isolated run. Later plan expansions and
actual executions are recorded separately below. The earlier independently measured
PDC 17/9 receipt remains accurate for its original test and executable hashes.

## Recorded component and guest results

| Layer | Executed result and evidence | Meaning and boundary |
| --- | --- | --- |
| A77 architecture | [Original 852 checks across nine configurations and two invalid-configuration rejections](cpu-a77-r1p0/result.json), [frozen12-associated generic replay](compat-final-20261123/a77/result.json), and [08:20 verification](verified-20261123-0820/final-verification.json) | Privileged instruction/ID/cache-description/PMU execution. Each run retains its binary hash; reference cache geometry is not measured tablet timing or complete Kryo equivalence. |
| CPU/MMIO timers | [Exact/legacy, IRQ and migration receipts](mmio-timer-live-status-20261123/) and [regression comparison](mmio-timer-live-status-20261123/comparison.json) | Independent controlled-time rate checks, physical/virtual timer behavior, actual migration and same-ELF no-migration control. |
| GENI/GCC | [GENI 14 + migration 6; GCC 19 + migration 5; wired clock/reset 6 + migration 4](expansion-20261123/revalidation-4/summary.json) | Actual sockets, FIFO/IRQ, clock/reset state and process-to-process transfer. |
| SMMUv2 | [23/23 C QTests](smmuv2-20261123/qtest-result.json) | Translating DMA, permissions, faults, context-bank operations and invalidation in the implemented AArch64 stage-1 subset. |
| UFS host/PHY/DMA | [56/56](resume-20261123/reports/32-ufs-register-dma.json), with the [earlier 48-case receipt](ufs-bringup-20261123/qtest-48-after-hibern8-fix.json) retained | GCC/PHY/UIC/SCSI/DMA and WriteBooster/Hibern8 behavior on new synthetic media. |
| Shared UFS core / PCI | [Frozen12-associated generic build: 15/15 unskipped cases](compat-final-20261123/ufs-pci/result.json) and [08:20 build: 15/15](verified-20261123-0820/pci-ufs/result.json) | Shared-core/PCI compatibility on generic ARM virt; distinct binaries, source provenance and harness failures remain recorded. |
| Linux storage | [19/19 guest checks in the strict-IOMMU log](ufs-bringup-20261123/linux-strict-iommu-19.log) | Real Qualcomm host/PHY/GCC/SMMU bindings, DMA domain, direct I/O, flush/reopen and error paths. Host disk verification is a separate required check in the runner. |
| RPMh components | [22 reference + 5 migration + 6 private partial-input checks](expansion-20261123/revalidation-5/rpmh-summary.json) | Synthetic reference database and limited AOP-derived static data are separately identified. Neither is a live full command-db capture. |
| RPMh Linux integration | [23 guest checks and 3 host checks](rpmh-linux-20261123/result.json) | RSC/clock driver binding, 19.2 MHz bi_tcxo, real completion IRQ, completed XO transaction and cleared pending IRQ, plus host-verified UFS contents. Uses the explicit reference profile. |
| Watchdog | [16 behavior groups plus pending-bite migration](watchdog-20261123/) | Digital elapsed count, bark/bite ordering, configured QEMU action and continuation after migration. |
| IMEM/PS_HOLD | [7 checks on frozen build7](boot-resources-20261123/qtest-migration-7.json) | SRAM bounds/subwords/guest-owned fields, reset retention, digital output, invalid-width behavior and real migration; no PMIC power policy. |
| DCC | [Original 17 read-capture checks](dcc-20261123/result.json); [same-suite rerun](qsee-dcc-20261123/dcc-existing/report.json) and [16 ordered-operation checks](qsee-dcc-20261123/dcc-operations/report.json) on the QSEE/DCC build | Real source transactions, SRAM output and bounded WRITE/LOOP/RMW with pending-operation migration. Reference map2 identity; no native full-list/CRC/ATB/CTI claim. |
| PDC | [18 C tests and 10 migration checks in the isolated run](verified-20261123-0820/public-47/result.json) | The earlier [17/9 receipt](pdc-20261123/) retains its historical scope. Native VERSION remains unobserved. |
| IPCC | [16 message/IRQ/negative/migration checks](verified-20261123-0820/public-47/37-ipcc-messages/fixtures/data/result.json) | Includes real GIC SPI 229; no fabricated remote firmware reply. |
| SPMI / PDC / GIC | [SPMI 25](verified-20261123-0820/public-47/39-spmi-pmic-arb/fixtures/data/result.json) and [real route/migration 8](verified-20261123-0820/public-47/40-spmi-pdc-gic-route/fixtures/data/result.json) | Explicit reference and synthetic endpoint. One actual process transfer retains IRQ and the remaining transaction deadline; not native PMIC or low-power equivalence. |
| Boot layout | [13/13 paused checks](boot-layout-20261123/runtime-layout-frozen4.json) | Actual ROM/DTB intervals and static reservation checks. CPUs remained paused; this is not a boot verdict. |
| Production kernel with generated DT | [Original 8/8 PID1 checks and guest shutdown](production-gdb-exact-20261123/result.json) and [independent frozen12 rerun](production-gdb-frozen12-20261123/result.json) | Unmodified 4.19.157-perf+ Image, eight CPUs, exact-counter machine and a validation initramfs. Not original Android userspace or full native DT. |

The implemented TLMM gate exercises 25 C QTest cases and 15 checks across four
real transfers. Its register contract and commands are in
[tlmm.md](../docs/tlmm.md); the continued integrated replay retains its actual
binary-specific verdict. No missing or skipped C test can satisfy the public
runner.

Frozen build7 is SHA-256
`f3fa0d2db54d9f59ba9b0ea25239553900b805c3c05e53e0dd8577e94607e41b`.
Do not assign that hash to older rows whose own receipts identify a different
executable. The exact-mode production guest receipt, for example, identifies
`a70862bfa8aa957ea26e362b5defd9ae60b4cc4bb53f2ac2c70c0b4fe3b67e2a`.

## Production kernel and full native DT are separate gates

The [console-independent observer](../docs/guest-acceptance.md) observes a
completed PID1 record at an EL0 breakpoint, reads the original counter-probe
stdout, removes the breakpoint, and requires a real guest-requested shutdown.
It does not patch the production Image, write a successful result to RAM,
or treat a zero process status alone as acceptance. The empty production
serial log is retained; this run does not prove console handoff.

Full native-DT diagnosis uses the unmodified production Image and the complete
private captured DT, under the declared direct-kernel loader and RAM fixup.
The [earlier progression receipt](native-dt-progression-20261123/progress.json)
records the watchdog and restart faults. Frozen build7's IMEM mapping removes
the observed `msm_restart_probe+0xec` external abort; the subsequent recorded
fatal point was `dcc_probe+0x114`. Frozen10 subsequently programmed native DCC
lists 3/6/7, passed probe, then faulted in `qcom_ipcc_probe+0x120` and a parallel
`spmi_pmic_arb_probe+0xb0`. That run used a private explicitly partial command
database; it is distinct from public reference-profile CI. DCC capture
execution was not proven. These historical outcomes remain unchanged.

The [frozen12 native-DT diagnostic](native-dt-spmi-reference-20261123/result.json)
explicitly enables `spmi-profile=reference`. It passes the former IPCC/SPMI
probe blockers and records the next fault in `qsee_irq_probe+0x140`, a write
of `0xf` to syscon address `0x0188601c`. The captured QSEE node supplies
`0x01880000/0x10000` and SPIs 348/349. Its source corroboration is not asserted
to be the exact production-kernel source. The diagnostic has no successful
PID1 or Android verdict, and neither its explicit SPMI reference identities
nor the DCC list programming proves full native peripheral behavior.

The later display, QSEE/DCC and boot-resource diagnostics below each retain
their own failure boundary. Advancing to another driver does not establish
a full native-DT or Android boot.

## Historical initial baseline

The original files at this directory's top level remain immutable records of
the initial A55/A76 and fixed-clock implementation. They are not descriptions
of today's A77/GCC/UFS board.

| Initial measurement | Preserved receipt |
| --- | --- |
| 24 captured DT nodes; 21 parser tests observed | [Capture](android-board.json), [profile](board-profile.json), parser source |
| 20/20 required runtime profile checks with 3 declared deviations | [Machine result](machine-profile-validation.json), [exported DT](tb371fc-generated.dts) |
| GENI 13 checks and 6 migration checks | [Register result](geni-qtest.json), [migration](geni-migration.json) |
| 19 runtime-validator unit tests observed | Validator test source; no original raw unit-test log claimed |
| Native EL0 probe: 6 consistency checks from one execution | [Native result](android-arch-probe.json) |
| Clean Linux: 8/8 guest checks and exit 0 | [Linux result](linux-smoke.json), [unaltered serial log](linux-smoke.log) |

That clean-Linux run started at **2026-11-22 22:47:01 UTC** (06:47:01 on
2026-11-23 in Asia/Shanghai), completed in 3.554 seconds, and did not use
`keep_bootcon`. Its PID1 output continues after earlycon is disabled. The
identical native/guest probe ELF reports 19,200,000 Hz and counter progress;
that probe alone did not measure the exact counter rate. Later independent
timer gates supply the rate evidence.

The original kernel's [build metadata](linux-smoke-kernel-build.json),
[fragment](linux-smoke-kernel-fragment.config) and
[effective configuration](linux-smoke-kernel.config) remain unchanged.
The retained [research-kernel failure](linux-research-kernel-failure.json),
[log](linux-research-kernel-failure.log) and
[source manifest](linux-research-kernel-source-manifest.json) are still
failures; subsequent clean-kernel success does not validate the proposed
research-kernel patch or physical-tablet handoff.

## Limits of the demonstrated implementation

- CPU0–3 remain upstream A55 substitutes for measured Qualcomm identities.
  A77 is now implemented, but native cache geometry, undocumented controls,
  microarchitecture, physical timing and power are not reproduced.
- Exact counters are verified against QEMU virtual time. CNTFRQ annotation
  and an advancing counter alone are insufficient evidence of that rate.
- UFS I/O gates finish with host QMP quit after checking data and verdicts.
  Ordinary console and production GDB gates separately require guest shutdown.
- Component migrations use `ufs-enabled=off`. UFS explicitly blocks migration;
  no whole-board snapshot or migration with in-flight storage I/O is claimed.
- RPMh reference addresses/auxiliary data, clock handoff state, fabric rate,
  PHY timing and watchdog frequency have explicitly documented reference or
  inference status. They are not freshly measured silicon reset values.
- PDC VERSION, IPCC priority/capacity and SPMI v5/APID/ownership are explicit
  reference choices or remain unmeasured. Synthetic IRQ peripherals validate
  controller transactions and routing, not native PMIC voltage or power behavior.
- SMMUv2 driver binding does not demonstrate GPU operation. Adreno/GMU,
  display, USB, radios, audio, camera, touch/pen/keyboard, PMIC/SCM and full
  suspend/resume still require their own models and end-to-end evidence.

## Publication integrity and reproduction

Public CI uses pinned, unpatched upstream Linux plus synthetic test media and
reference resource data. It does not need a production Image, full private
DT or AOP firmware. Its [runner](../scripts/ci_device_tests.py) stores commands,
stdout/stderr, exact binary/input hashes and every gate verdict; missing
binaries, skips, failed checks and timeouts fail acceptance. The workflow's
presence and local results do not imply a passing Actions run for a commit.

The [original validation ledger](VALIDATION.md) and top-level Linux result/log and kernel metadata/configuration files
were reviewed and copied byte for byte, including serial line endings.
Their QEMU hash is
`baea240b28c1428dfd42a27ad9248455dc3095f18ddb0ab0e4c7261085282a5e`,
with the original package string `v11.1.3-1-gfea2412-dirty`.
Their [SHA256SUMS](SHA256SUMS) applies to that original set. Later evidence
directories carry their own manifests and publication descriptions.

New hardware captures retain only the documented allowlists and provenance.
Full captured DTs, original partition/firmware inputs, complete native-DT
diagnostic logs and process core dumps remain outside the published set.
Where a receipt is an allowlisted summary rather than an unchanged original
log, its scope and source hashes must say so explicitly.

## 2026-11-23 display RSC integration

Frozen binary 05c617462f87d5bacea9b86007ca8e37de5ed9a2644a5e3af5ac0d60485ec55b passes 49/49 public gates and 53 targeted RPMh checks. Source hashes remain unchanged across the public run. Evidence: [display-rsc-20261123](display-rsc-20261123/publication.json). Untouched production kernel/full private DT still fails at qsee_irq_probe+0x140 under explicit reference SPMI and partial RPMh file profiles; no stock Android success is claimed.

## 2026-11-23 QSEE and DCC integration

Frozen binary 51fe61d35213a6602a8eaa865faba04bc85f88df341947ca8a2e6300a8ab5be6 passes 52/52 public gates in 128.434 seconds and 51 targeted checks (QSEE 12 + migration 6, DCC operations 16 + original 17). Source and binary hashes were unchanged across the successful run. The initial QSEE migration harness failed before transfer on clock_step 0; the preserved correction advances the source by one virtual nanosecond, without changing the device C or binary. See [publication record](qsee-dcc-20261123/publication.json). The original kernel/full DT still fails at AOP mailbox memcpy and refgen_kona_is_enabled; stock Android and full native-DT boot remain unpassed. The preceding display commit dc11891 has two successful GitHub Actions jobs, recorded separately and not attributed to this newer commit.

## 2026-11-23 TCSR, AOP SRAM and REFGEN integration

Frozen binary 2173fa4da68f8f769c53942b056124c5621e4714652c7f572deacc9440ec09cf passed 55/55 public gates in 125.169 seconds and 50 targeted checks (TCSR 24, AOP SRAM 10, REFGEN 16). Ten migration attempts comprise seven positive transfers and three expected configuration mismatch rejections. Sources/binary were unchanged during tests. All 22 CI-plan unit tests passed. See [publication record](boot-resources-20261123-1005/publication.json). Both production-kernel/full-DT runs panicked: unknown REFGEN reset at refgen_kona_is_enabled; explicit disabled reference at gdsc_probe/regmap_mmio_read32le. The previous AOP mailbox abort was absent in these logs; firmware and QMP services remain unvalidated. Prior QSEE/DCC commit 5f8bca77 passed both GitHub Actions jobs, separately recorded.

## 2026-11-23 GDSC and empty SMEM: public build 408d82a

The [sanitized CI log review](gdsc-smem-20261123/ci-408d82a.json) records
commit `408d82ae4acc0fce05d16d3f17ee3123eda82ab4`, workflow run
[45164018326](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45164018326).
Both jobs succeeded. The board job ran 53/53 component/parser gates in
54.967 seconds and, separately, four of four unpatched-Linux gates in 5.156
seconds. Their sum is 57 successful gate invocations; it is not a separate
all-suite run. Both runner summaries reported an unchanged QEMU executable.

The successful gate contracts require GDSC 29 checks and ten migration
outcomes, and SMEM 22 checks and four migration outcomes. GDSC's six
successful transfers and four expected configuration rejections are distinct
from SMEM's two successful transfers and two expected rejections. The SMEM
component fixture executes no guest CPU and captures no native SBL state.
The log review identifies its count provenance and does not pretend to have
read the nested artifact JSON files at that stage.

The independent generic job passed 852 A77 guest checks across nine
configurations, rejected two invalid CPU configurations, and executed all
15 PCI UFS cases without skips. These generic executables are separate from
the board QEMU and are not assigned its hash.

The initial 2819462 attempt remains a failure in the same CI review:
52/53 component gates, a failing GDSC gate, skipped later Linux steps, and
a cancelled generic job. The correcting 408d82a commit changed the QMP
`human-monitor-command` argument from `command_line` to `command-line` in
the GDSC test. It did not turn the earlier failed run into a pass.

## 2026-11-23 production SMEM probe and GCC halt-status boundary

The subsequent [native diagnostic receipt](gdsc-smem-20261123/native-ci-408.json)
records a downloaded-and-verified artifact, independently of the preceding
log-only review. Board artifact 14813532760 has ZIP SHA256
`7c7812bf16b93e92b04574c1b0900d14e771ff96702fec590ff9be0c05e8c2de`.
The executable extracted from it has SHA256
`0190794d7890fcc34caeff3c7f30d00218a6f0027bddc4500c34f79c45051a93`
and matches the QEMU hash in both the 53-component and four-Linux suite
receipts. It remained unchanged after the diagnostic. The archive digest
is not substituted for the executable digest.

The unmodified production Image SHA256 is
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`.
The complete native DT input SHA256 is
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
Runtime loader memory fixups are explicitly recorded.

| Selected interface | Recorded configuration |
| --- | --- |
| RPMh | File profile with a partial command-db input |
| SPMI | Explicit reference |
| GPU GDSC | `reference-off` |
| SMEM | `reference-empty` |
| REFGEN | Explicit disabled-reference handoff |
| TCSR mutex | Explicit unlocked-reference handoff |

The production `qcom_smem_probe` successful-publication instructions and
live driver pointer were verified. With the guest paused,
virtual-memory reads through CPU 0 found nonzero `__smem` and consistent
driver/lock pointers, item count, directory, global partition, region
geometry, allocation frontiers and header words. All 23 consistency checks
passed. The observer made no guest-memory writes; it did not manufacture
the kernel's published pointer. The explicit loader profile still supplies
its declared synthetic initial RAM image.

This is successful production-driver probe acceptance. It is separate from
the 22-group public QTest fixture and does not demonstrate subsequent guest
allocator operations, native SBL contents, remote-processor services or GPU
execution. The prior GDSC abort was absent and both CX/GX supply-registration
messages were observed, without implying a working GPU.

The same native run then took a synchronous external abort, ESR
`0x96000210`, on a 32-bit read of physical `0x00148198`, GCC offset `0x48198`.
The leaf was `regmap_mmio_read32le+0x8`, through `clk_branch2_check_halt`,
`clk_branch_toggle`, `clk_branch2_enable`, `clk_register`,
`qcom_cc_really_probe` and `gcc_kona_probe`. It was stopped by timeout after
25.143 seconds. QEMU and diagnostic-tool exit 0 reflect cleanup, not a
successful native-DT or Android boot. Both boot verdicts remain false.

The subsequent GCC source at 0a617bab implements binding 143
`gcc_sys_noc_cpuss_ahb_clk`, its separate halt status and binding 18
`gcc_cpuss_ahb_postdiv_clk_src` parent. Its current gate requirements are
24 behavior checks and eight migration checks, while the top-level plan
still has 57 gates. The 408d82a receipts above predate that code and cannot
attest the new branch/parent implementation; its matching-binary results
must be recorded separately. Earlier GCC 19/5 and the independent
46/47/49/52/55-gate histories retain their original counts and hashes.

## 2026-11-23 GCC follow-up: verified build 0a617bab

The [reviewed CI record](gdsc-smem-20261123/ci-0a617ba.json) covers
`0a617bab7cb2277073aa24092ec9a12b63fceb50` and workflow run
[45170677624](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45170677624).
Both jobs succeeded. The board job passed 53/53 component/parser gates in
106.054 seconds and a separate four of four clean Linux gates in 5.555
seconds. The original [component](gdsc-smem-20261123/components-0a617ba.json)
and [Linux](gdsc-smem-20261123/linux-0a617ba.json) JSON files are retained
without modification. Again, 57 is the sum of those two invocations, not an
additional all-suite run. Their QEMU executable remained unchanged.

The later [artifact verification](gdsc-smem-20261123/artifact-0a617ba.json)
records a downloaded 62,152,896-byte board QEMU with SHA256
`a2e19b188f6253928ad47630896e0967fb99f55abbddaf98212f312e8194ee1b`.
Its computed executable digest matches both raw suite records. The
downloaded board ZIP is separately identified by SHA256
`d102b406d3ef9278dfa358271ee7164bdb819d80fad9417305793a47d4af06f1`.
The artifact manifest also records exact hashes and sizes for the copied
component, Linux, GDSC and SMEM result members. These later member/executable
checks supplement the earlier log-only CI review; they do not rewrite that
review's truthful account of what it had read at the time.

| Gate | Executed evidence and precise counting |
| --- | --- |
| GCC behavior | [24/24 checks](gdsc-smem-20261123/gcc-registers-0a617ba.json), including SYS_NOC_CPUSS status, shared votes, parent demand, remote vote and reset |
| GCC migration | [8/8 checks around one actual QMP process transfer](gdsc-smem-20261123/gcc-migration-0a617ba.json); same binary on both sides, not eight transfers or cross-version compatibility |
| GPU GDSC | [29 checks including ten migration outcomes](gdsc-smem-20261123/gdsc-0a617ba.json): six accepted transfers and four expected profile rejections |
| Empty SMEM reference | [22 checks with four migration outcomes](gdsc-smem-20261123/smem-0a617ba.json): two accepted transfers and two expected profile rejections; guest CPU not executed |
| A77 | [852 guest checks over nine configurations and two rejected invalid configurations](gdsc-smem-20261123/ci-0a617ba.json), independent generic job |
| Shared UFS PCI | [15/15 actual cases without skips](gdsc-smem-20261123/ci-0a617ba.json), independent generic job |

The preceding 4f271b5 run was cancelled before public gates executed.
The CI review preserves the skipped gate steps and identifies the
review-driven test-only reset-wait correction in 0a617bab. That cancelled
attempt supplies neither a hardware-failure verdict nor a pass. The
correction changed four test lines and did not change the device model.

The [actual generator check](gdsc-smem-20261123/gcc-regeneration-0a617ba.json)
ran Python 3.12.14 against the fixed Linux 6.12.114 source and binding inputs,
with their manifest hashes checked. It reproduced both
`hw/misc/qcom-sm8250-gcc-data.inc` and
`tb371fc/docs/gcc-sm8250-registers.json` byte for byte against 0a617bab.
The generated outputs describe 198 clocks, 84 external input slots, 460
allowed register offsets, 45 resets and 11 power domains. This was an
actual generation and byte-comparison run, not QEMU execution.

## 2026-11-23 production-native follow-up on the 0a617bab CI binary

The [separate production diagnostic](gdsc-smem-20261123/native-ci-0a.json)
used the downloaded and verified 0a617bab board QEMU, SHA256
`a2e19b188f6253928ad47630896e0967fb99f55abbddaf98212f312e8194ee1b`,
matching the successful 53-component and four-Linux CI suite records.
Its unmodified production Image and full DT input retain SHA256
`ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f`
and `5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`,
respectively. The explicit reference handoff and loader adjustments remain
part of the configuration; this is not native SBL execution.

The 21.166-second diagnostic again observed the production SMEM success
path: `success_path_observed=true`, all 23 state checks passed and
`guest_memory_written=false`. It passed the prior GCC halt-status abort
at physical `0x00148198`. This is a new measured result on the corrected
executable, while the older 408d82a receipt retains its original failure.

The next fatal access was a GCC unsupported read at offset `0xf060`,
physical `0x0010f060`. The stack passed through `mux_get_parent`,
`clk_register`, `qcom_cc_really_probe` and `gcc_kona_probe`; the production
kernel recorded ESR `0x96000210` and panicked. The [fixed official Kona
driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/94304e12a3509eac7b3716bf574ce0b1fcaacbae/drivers/clk/qcom/gcc-kona.c)
identifies the offset as `gcc_usb3_prim_phy_pipe_clk_src`, the USB3 primary
PIPE source mux with selector bits 1:0. Its getter/source and setter/config
mapping are asymmetric in that snapshot; production getter disassembly
still needs checking. The PHY/test parents' 1 kHz dummy declarations are
not measured frequencies and do not provide a PHY model. The remaining
[selector and external-parent contract](../docs/gcc-sm8250.md#next-observed-boundary-usb3-primary-pipe-source-mux)
is not implemented in this follow-up.

Complete native-DT userspace PID 1, Android userspace, GPU execution, native SBL
contents and firmware services remain unpassed. SMEM probe success does
not by itself demonstrate subsequent guest allocator operations.

## 2026-11-23 independent doesvm all-suite acceptance of 0a617bab

The [local summary](gdsc-smem-20261123/local-final-summary.json) and
[original all-suite report](gdsc-smem-20261123/local-all-0a617ba.json)
record a completed, independent local build and a single `--suite all`
invocation. All 57 gates passed: 53 component/parser gates and four Linux
gates, with zero failures and zero skips. This is one actual all-suite run;
the earlier CI's separate 53-component and four-Linux invocations retain
their own records.

The source at `/work/continue-gdsc-20261123/source` was clean and remained
unchanged at commit
`0a617bab7cb2277073aa24092ec9a12b63fceb50`, tree
`88407a0fbefb12318c64ab8cc11ce7b5fbbf3418`.
The local branch is `tb371fc-local-gdsc-smem-20261123` at that same
commit, with no source changes. The earlier local snapshot is retained
under `refs/heads/tb371fc-pre-sync-aa80a656646e`. Git fetch, the
incremental build and the completed acceptance invocation each returned
exit 0.

| Recorded field | Value |
| --- | --- |
| Start, UTC | `2026-11-23T12:59:48.192340+00:00` |
| End, UTC | `2026-11-23T13:02:59.070133+00:00` |
| Runner-reported suite elapsed time | 186.76 seconds |
| Local QEMU size | 64,039,496 bytes |
| Local QEMU SHA256 | `89fcb435a7ca7ca9b30106f8deb712828fdf66a26df88e9297e27e9b0f96e957` |
| QEMU before/after acceptance | Same SHA256; unchanged |
| Local Linux version | 6.12.114 |
| Local Linux Image SHA256 | `c513268fb0727d289fdfc0f02637c53cf5f976cc836ea9c586763f5f584bce40` |
| Original all-suite report SHA256 | `a228e94e014e05baa52fdb38a4e33890924e5620ebf8f4ea9656f44d4a7d80a3` |

The retained executable is
`/work/continue-gdsc-20261123/frozen-final/qemu-system-aarch64`.
The timestamps and the runner's own elapsed-time measurement are reported
separately. The local executable and locally compiled Linux Image differ
from the corresponding CI artifacts; their distinct hashes identify the
inputs to this run.

Earlier watcher timeouts and the summary-field `KeyError` remain
separately retained non-passing checkpoints.

This local invocation did not rerun A77, PCI or the production-native-DT
diagnostic. A77/PCI acceptance remains the independent CI job's result.
The production SMEM success and the latest USB3 PIPE mux abort remain
observations of the verified CI executable `a2e19b18…`, not this local
`89fcb435…` executable. Full native-DT userspace PID 1, Android userspace,
GPU execution and native firmware services remain incomplete.
