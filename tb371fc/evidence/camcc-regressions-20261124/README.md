# CAMCC regression additions: execution qualification pending

The frozen test adds coverage for two source-derived digital protocol states.
Static review found no blocking issue. **Neither local attempt completed a
CAMCC check. The added regressions require the repository CI gate before
acceptance.** The archived failures remain failures; the earlier baseline CI
pass does not validate the modified test.

## Frozen source and binary

| Item | Identity |
| --- | --- |
| Test baseline | `8798465b1b70f9c86f5cd5e1e38ce595bd6088ce` |
| Baseline merge into main | `57b6b16eb861bfea49395d1e2c3497aab06d2e89` |
| Tree shared by those two commits | `73034a7c75ed1fe6ebee125e954f8add4d9bba19` |
| Modified test SHA-256 | `f5ac3859e1ddf48746464ca8d0bd884deef967533d5822f049194d6c7bf67050` |
| Unmodified baseline test SHA-256 | `bac9cafaf83292ecde02f5f658450dc28ba56c84b7ec5d7e049f9035579dd983` |
| CI binary SHA-256 | `026f93a8c26950dd66cdb5c44eaf5e1575db638e59b69cbf025591d810be5594` |
| CI artifact ZIP SHA-256 | `a37519a493f28337eb5c705a7a759e6f8bc3fe90709cd56a4b41bb5c568b6950` |

The binary came from [run 45426713919](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45426713919),
artifact `14938651765`, built from the baseline commit. Its original CAMCC
test reported 41 checks. The merge has parents
`19d2382236f74ba72444d18f9555c60ad6b1c329` and the baseline commit; local Git
objects confirm the two tree IDs are identical. This change modifies the test
and adds this evidence directory. It does not change the C model or CI scripts.

## Added coverage

The existing Lucid update check now clears `UPDATE_BYPASS` while leaving
`UPDATE` high in two distinct states: an already acknowledged update and a
not-yet-due pending latch. It checks cancellation, preserves the appropriate
committed active values, advances virtual time, and checks that no canceled
latch or ACK reappears. The same supported state is carried through the
existing successful steady-state migration, using QOM/Clock observers before
device MMIO. This is a control-state and migration consistency regression;
it does not assert that the vendor kernel normally uses this cancellation
sequence.

The existing branch check now starts with a cold critical XO source. It uses
the vendor `ROOT_EN` → configure/`UPDATE` → clear `ROOT_EN` sequence, then
checks that branch 37 (`cam_cc_gdsc_clk`) independently demands source 110
(`cam_cc_xo_clk_src`) while `ROOT_EN` remains clear. Releasing the branch must
drop that demand. The register-update helper omits unchanged writes, matching
the relevant regmap behavior. The source ordering is bound to vendor commit
`94304e12a3509eac7b3716bf574ce0b1fcaacbae`, `clk-rcg2.c` lines 649–690,
and the literal binding/table already in the test.

The acceptance targets remain 31 ordinary cases and 41 total checks,
including three successful migrations, two profile-mismatch rejections,
two corrupted-stream rejections, and six stream files. These are expected
counts, not counts completed by the local attempts below.

## Preserved local attempts

| Attempt | Result | Completed checks / migrations |
| --- | --- | --- |
| `attempt-001` | Exit 1 during Python import: the sparse checkout omitted `rpmh-reference.json`. Test main and QEMU were not entered. | 0 / 0 |
| `attempt-002` | Exit 1 during the first QMP connection setup: `socket(AF_UNIX, SOCK_STREAM)` returned `EPERM`. The first case did not complete. | 0 / 0 |

Between attempts, the missing fixture was restored byte-for-byte from the
baseline commit; its SHA-256 is
`3643c21533d446ae42e0f84f71a1b8604cba0da500baf539b10d5c9aa578ddfc`.
An import-only `--help` preflight then exited 0. The isolated runtime library
resolved through `LD_LIBRARY_PATH`; its provenance is recorded in
`runtime-library-receipt.json`. The binary, library and modified test hashes
were unchanged before and after both attempts. Attempt 002 additionally
bound the fixture hash.

The second attempt launched the initial paused QEMU fixture, but its setup
failed before QMP negotiation or a QTest transaction was recorded. Its
`checks`, migrations and stream lists are empty, and `passed` is false.
A subsequent read-only process listing found no matching local QEMU
process. This was a cleanup observation, not a successful test gate. No
further local or alternate-host retry is claimed here.

`static-preparation.json` is the original record from before either attempt;
its `tests_executed: false` and migration counts describe that earlier
preparation stage and the planned matrix. `binary-receipt.json` describes
the earlier unmodified CI gate. `qualification.json` records the current
pending status. Attempt commands, receipts, stdout/stderr and the second
attempt's result and initial fixture logs are copied byte-for-byte from the
local originals. In the raw result, scope and `expected_*` fields describe
the intended suite; the empty lists and failure fields describe execution.
`SHA256SUMS` binds every other file in this evidence directory.

This package contains no executable binary, runtime library, migration
stream, device payload or original Android capture. It records no native
kernel probe, camera execution or Android boot success. Required CI must run
the modified test before these new assertions are treated as qualified.
