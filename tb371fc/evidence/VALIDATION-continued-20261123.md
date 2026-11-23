# Continued validation ledger — 2026-11-23 UTC

The implemented board now contains architectural A55/A77 CPU groups, exact
counter conversion, GENI/GCC, UFS/PHY, SMMUv2, optional RPMh, TLMM, watchdog,
and IMEM/PS_HOLD and bounded DCC read-capture subsets. Upstream Linux performs real translated UFS I/O.
The unmodified production kernel also executes the validation initramfs with
the generated subset DT. **Full native-DT and stock Android boot remain
unaccepted.**

The [current integrated replay](resume-20261123/result.json) records the
continued-build results. Its actual per-gate verdicts, inputs and binary hash
govern that replay; a partial or failing receipt is not an aggregate pass.
The component receipts below are independent measurements and do not all use
the same QEMU build. Test counts are not a hardware-completion percentage.

## Integrated public-input run

The [42-gate result](resume-20261123/result.json) passed all gates in
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

The [new isolated A77 receipt](cpu-a77-current-20261123/result.json) passed
852 guest checks plus two invalid-configuration rejections on its own
`c06bb42b52bc47839fbe40eb5a247c04d70b64840411af6efff1497d0a1d16c0` binary.
The [first PCI UFS compatibility listing](ufs-pci-20261123/initial-listing-failure/result.json)
failed before any of its 15 cases ran: the runner did not accept the TAP
comment prefix on listed test paths. It is retained as a harness failure,
not a passing storage result.

The current CI plan registers 45 gates: 41 component/parser gates and four
Linux boots, with PDC 18/10 and IPCC 15. The earlier independently measured
PDC 17/9 receipt remains accurate for its original test and executable hashes.

## Recorded component and guest results

| Layer | Executed result and evidence | Meaning and boundary |
| --- | --- | --- |
| A77 architecture | [852 guest checks across 9 configurations; 2 invalid configurations rejected](cpu-a77-r1p0/result.json) | Privileged instruction/ID/cache-description/PMU execution. Reference cache geometry, not measured tablet timing or complete Kryo equivalence. |
| CPU/MMIO timers | [Exact/legacy, IRQ and migration receipts](mmio-timer-live-status-20261123/) and [regression comparison](mmio-timer-live-status-20261123/comparison.json) | Independent controlled-time rate checks, physical/virtual timer behavior, actual migration and same-ELF no-migration control. |
| GENI/GCC | [GENI 14 + migration 6; GCC 19 + migration 5; wired clock/reset 6 + migration 4](expansion-20261123/revalidation-4/summary.json) | Actual sockets, FIFO/IRQ, clock/reset state and process-to-process transfer. |
| SMMUv2 | [23/23 C QTests](smmuv2-20261123/qtest-result.json) | Translating DMA, permissions, faults, context-bank operations and invalidation in the implemented AArch64 stage-1 subset. |
| UFS host/PHY/DMA | [56/56](resume-20261123/reports/32-ufs-register-dma.json), with the [earlier 48-case receipt](ufs-bringup-20261123/qtest-48-after-hibern8-fix.json) retained | GCC/PHY/UIC/SCSI/DMA and WriteBooster/Hibern8 behavior on new synthetic media. |
| Linux storage | [19/19 guest checks in the strict-IOMMU log](ufs-bringup-20261123/linux-strict-iommu-19.log) | Real Qualcomm host/PHY/GCC/SMMU bindings, DMA domain, direct I/O, flush/reopen and error paths. Host disk verification is a separate required check in the runner. |
| RPMh components | [22 reference + 5 migration + 6 private partial-input checks](expansion-20261123/revalidation-5/rpmh-summary.json) | Synthetic reference database and limited AOP-derived static data are separately identified. Neither is a live full command-db capture. |
| RPMh Linux integration | [23 guest checks and 3 host checks](rpmh-linux-20261123/result.json) | RSC/clock driver binding, 19.2 MHz bi_tcxo, real completion IRQ, completed XO transaction and cleared pending IRQ, plus host-verified UFS contents. Uses the explicit reference profile. |
| Watchdog | [16 behavior groups plus pending-bite migration](watchdog-20261123/) | Digital elapsed count, bark/bite ordering, configured QEMU action and continuation after migration. |
| IMEM/PS_HOLD | [7 checks on frozen build7](boot-resources-20261123/qtest-migration-7.json) | SRAM bounds/subwords/guest-owned fields, reset retention, digital output, invalid-width behavior and real migration; no PMIC power policy. |
| DCC | [17/17 read-capture and migration checks](dcc-20261123/result.json) | Real source transactions and SRAM output; reference map2 identity, no native full-list/CRC/ATB/loop/write/RMW claim. |
| PDC | [17 C tests and 9 migration checks](pdc-20261123/) | Exact historical scope. Expanded 18/10 source is separately gated against its corresponding build. |
| Boot layout | [13/13 paused checks](boot-layout-20261123/runtime-layout-frozen4.json) | Actual ROM/DTB intervals and static reservation checks. CPUs remained paused; this is not a boot verdict. |
| Production kernel with generated DT | [8/8 PID1 checks and guest shutdown](production-gdb-exact-20261123/result.json) | Unmodified 4.19.157-perf+ Image, eight CPUs, exact-counter machine and a validation initramfs. Not original Android userspace or full native DT. |

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
execution was not proven. Advancing to a later driver is not full-DT success. Later progression belongs in the
current integrated receipt, without overwriting the earlier failed outcomes.

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
