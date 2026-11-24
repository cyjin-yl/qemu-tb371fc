# SM8250 behavior-contract acceptance, 2026-11-24

The isolated doesvm build passed the complete final run below. These are
actual execution receipts. The independent original GitHub Actions run is
recorded below with its own binary and four-Linux-gate scope.
The [summary](acceptance.json) and [full source binding](source-binding.json)
identify source commit `ad81a9ecf33958725797e5a71a78f4128401572a` and tree
`c3ea357f376b923656e6dd0e2b80194d303ea31d`.
The corresponding [published source commit](https://github.com/cyjin-yl/qemu-tb371fc/commit/df588eba3dbde6188d6e61a2c893c6a071407bf9)
has that identical complete tree; its different commit envelope does not
change any tested source byte.

The remote checkout retained its baseline Git HEAD and patch-applied working
files. A separate temporary Git index included **all tracked and newly added
files**, and its tree hash exactly matched that committed local tree. The
dirty flag in the original runner reports is retained, not rewritten.
Later documentation/evidence commits do not change the tested device code.

## Final execution

| Run | Result | Receipt |
| --- | --- | --- |
| Complete component/parser suite | 59/59 gates; binary unchanged | [components](components.json) |
| Clean Linux boots | 4/4 gates; 8, 8, 19 and 23 guest checks respectively | [Linux](linux.json) |
| Real Linux UFS process checkpoint | Source 21 + target 18 = 39 PASS, zero FAIL | [checkpoint](linux-ufs-migration.json) |
| Existing quiescent UFS checkpoint gate | 21 checks, three actual transfers, seven rejections | [UFS migration](ufs-migration.json) |
| Combined power gate with old v2 input | 8 checks, including actual old-binary-to-new-binary transfer | [power legacy](power-legacy.json) |
| Separate shared-core PCI/MCQ build | 17/17, no skips | [PCI](pci.json) |
| Separate A77 configuration | 852 guest checks in nine configurations; two invalid configurations rejected | [A77](a77.json) |

The component suite includes the new [14 crypto checks](ufs-crypto-rejection.json),
[18 Auto-Hibern8 checks](ufs-auto-hibern8.json), [16 TBU checks](tbu-reference-ecats.json)
and [seven combined power checks](power-checkpoint.json). These are constituent
tests, not additional aggregate gates. The [independent TBU run](tbu-targeted.json)
also passed, including one real process transfer and six rejected receivers.

The Linux checkpoint preserves the same PID 1, boot ID, nonce and monotonic
state. Source process 277594 exited successfully before target process 277607
started. The target first loaded paused, then received only `CONTINUE`, without
being told the nonce again. It read the source payload, wrote a new payload,
flushed, closed and reopened the same LUN. The host compared all 67,108,864 disk
bytes at both boundaries, including the expected untouched zero regions.
The migration stream hash remained unchanged. Both [source serial](linux-migration-source.log)
and [target serial](linux-migration-target.log) retain their exact bytes and
match the hashes in the checkpoint receipt; the source log contains CRLF.

## Linux runtime PM extension

These are two independent executions, not additional checks retroactively
inserted into the original four-gate Linux receipt.

| Run | Result | Raw receipt and serial |
| --- | --- | --- |
| Frozen exploratory fixture | 40/40, QEMU exit 0, 8.358 seconds | [result](runtime-pm-frozen/result.json), [serial](runtime-pm-frozen/serial.log), [debug](runtime-pm-frozen/qemu-debug.log) |
| Portable public gate | 40/40, QEMU exit 0, 6.322 seconds | [result](runtime-pm-portable/result.json), [serial](runtime-pm-portable/serial.log), [debug](runtime-pm-portable/qemu-debug.log) |

The [original guest](runtime-pm-fixture/guest.c) and [original harness](runtime-pm-fixture/run.py)
were frozen before the first run. The public [guest](../../tests/ufs-runtime-pm-guest.c)
preserves those C source bytes. The public [harness](../../scripts/boot_ufs_runtime_pm.py)
adds explicit input/output arguments and validates the pinned public kernel
receipt/configuration; its different hash has a separate actual execution
receipt. Both runs use board binary `ee1fa5c9...`, public Image `48bc7593...`
and effective configuration `e088dcae...`, unchanged before and after.

The actual Linux qcom UFS driver suspends the platform host, ordinary LUN
and device WLUN. The original run waited 1,260 ms for all three states and
then observed host suspended time increase from 43 to 169 ms. The original
direct-I/O fd stays open: the next ordinary `pread`, issued while the host is
still suspended, wakes the controller and returns the original payload.
Both runs also write and flush a second payload, close/reopen the same device
and independently compare every one of the 67,108,864 media bytes.

Read-only QMP/qtree observations show active -> Hibern8 -> active. The
core/interface/UniPro clocks become 0/0/0 Hz during suspend and recover their
pre-suspend rates after I/O. Those active rates are 300/100/300 MHz in the
frozen run and 37.5/100/37.5 MHz in the portable run; the gate does not require
a fixed active frequency. The reference clock remains at 19.2 MHz in both. No post-suspend `control=on`,
QMP device-state mutation, QTest clock forcing, replacement kernel or native
tablet access is involved. Each run covers one runtime-PM cycle, not system
suspend, physical-media energy state or migration while Linux is suspended.

The current public CI plan contains 59 component gates and five Linux gates.
Its new runtime-PM gate requires 40 ordered checks, the five host sleep checks,
zero kernel failure lines, complete media integrity and unchanged inputs.
Each CI revision must independently execute that expanded plan.

## Original independent GitHub Actions run

[Run 45318637466](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45318637466)
completed both jobs successfully on PR merge checkout
`b8adfc7aa557e6cec4bfa3fe5986b9d9fa91c6a1`, whose parents are base
`7bc8c66286112573ed940066a246cf8215459aff` and PR head
`6c4a433eb740bc5e4709ff73741ae2058549562d`. Its tree is exactly
`4dd0026fb58a352fe85b5151168d5059bbabc776`, matching that PR head.

The actual job logs and board artifact were independently reviewed: 59/59
component gates, 4/4 Linux gates, 21 quiescent UFS checks, 39 Linux process
checkpoint checks, 17 unskipped PCI cases, and 852 A77 checks plus two rejected
configurations. This run predates the fifth Linux runtime-PM gate.
Its board executable is
`add626bc7b58000730df5afa86f53efd01f6bc3e511b181be7d4b3b9119b83e7`,
distinct from the doesvm build. The [board artifact](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45318637466/artifacts/14886948410)
and [CPU artifact](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45318637466/artifacts/14885614013)
retain their original CI inputs and receipts. Required board files were
fully read and matched to the recorded source/binary/Image hashes.

The automatic PR review service reported a usage-limit failure; it is not
counted as a passed review. Independent source/evidence review and the actual
functional CI results are separate observations.

## Binary and input hashes

| Artifact | SHA-256 |
| --- | --- |
| Final board QEMU | `ee1fa5c90a908936093f30c4687cfdd5b872a448ccb26648a17c6b5f49b76530` |
| Separate PCI/A77 QEMU | `aece56b0633ec371b01b5146e2547622a40799dbee0c1f2723fa1c5cca7608b1` |
| Fresh unpatched Linux Image | `48bc7593651ea0410ef4745554ec455670d4bc125a41743c63baabfa0cdf58f3` |
| Frozen original board QEMU | `9fc5281827139ac51811d7912ed3c7fb307c83bc8b3c61d025921ee242386508` |

The [kernel build receipt](linux-build.json) binds Linux 6.12.114 to the pinned
archive, effective configuration, compiler and Image. No production tablet
kernel or storage image is used by these execution gates. Builds and retained
large fixtures remain in `/work/sm8250-contract-c538` on doesvm's isolated
CT102 `qemu-tb371fc-dev` container; disk/checkpoint binaries are not committed.

## Baseline failures and corrected attempts

[Preintegration red/green evidence](preintegration-red-green.json) preserves
the original receipts and their distinct binary hashes. The baseline lost a
held RPMh wake request, did not advertise Auto-Hibern8, and silently executed
crypto-marked reads/writes as plaintext even though CAP.CS was clear. The
crypto reproducer's exit 0 means **the old defect was reproduced**, not that
the baseline was correct. The early green runs bind to `ce9153c9...`, not the
final `ee1fa5c9...`; final component receipts above rerun those checks.

The [initial Linux attempt](initial-linux-failure.json) failed all four gates
using an earlier cached Image `0d5bb19a...`. Its configuration lacked the
current GCC/RPMh/SMMU driver selections and PROC_SYSCTL; serial diagnostics
included GENI clock probe deferral (`Err getting clks -517`). It is retained
as a failed input-selection attempt. A newly extracted, pinned source tree
was rebuilt with the current checked configuration; the final Image and four
successful boot receipts are separate, not relabeled earlier results.

The [first TBU compile](tbu-first-build-failure.log) failed because its new
source used an undefined `BIT32` macro. Commit `ad81a9ec` uses a fixed-width
constant; the [next build](tbu-final-build.log) and actual targeted/full-suite
execution passed. No failed attempt is counted as acceptance.

## Hardware evidence and limits

[Hardware facts](hardware-facts.json) contain only allowlisted read-only DT
properties and public UFS sysfs values, with pinned upstream/vendor source
references. No credentials, unique device identifiers, raw disk contents or
native MMIO traces are included. The Linux checkpoint nonce and boot ID are
generated inside this synthetic test, not captured from the physical tablet.

The [behavior audit](../../docs/sm8250-behavior-audit.md) explains the changes
and exact boundaries: ICE is explicitly unsupported, TBU is an opt-in reference
profile, and power tests drive explicit modeled inputs. UFS migration still
requires quiescent queues. These results do not certify inline encryption,
SCM/QTEE, complete native-DT/Android boot, physical power collapse or full
SM8250 equivalence.
