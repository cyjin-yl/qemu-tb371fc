# SM8250 behavior-contract acceptance, 2026-11-24

The isolated doesvm build passed the complete final run below. These are
actual execution receipts, not a claim of GitHub Actions completion.
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
