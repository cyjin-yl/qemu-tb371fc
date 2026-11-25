# Exact 8e8eeb RPM statistics artifact: native diagnostic and completed audit

The qualified standalone **8e8eeb** artifact ran once with the original inputs,
zero attached LUNs, and the recorded 30-global configuration. Its first retained
fatal fault is a four-byte read at **0x0c3f001c** in
**ddr_stats_probe+0x88/0x180**, after reaching initramfs unpack.
**Original Android boot remains unpassed.** Android userspace, boot completion,
an interactive display, and the OEM boot chain were not established.

The compiled artifact passed **97 component gates, five Linux gates, and seven
independent artifact audits**. A later independent audit of the completed
native run passed **69/69 checks**, with **31 recorded regular files fully
read**, no section errors, strict cleanup true, and Android boot false.
The native run remains a failed kernel diagnostic.

This directory contains metadata and selected observations. Its JSON files
bind the exact artifact and private retained evidence through hashes.
No original input hash inventory, private absolute paths, full argv, boot
arguments, raw RAM, or complete native logs are published here.

## Exact source and artifact

| Item | Identity |
| --- | --- |
| Observed QEMU source | 8e8eeb295722d18a45d6d5567e32b06fca4b49f8 |
| Observed source tree | a719da4aebdf5ba5ec944b353066e5bf290f2279 |
| Push workflow | [45536800888](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45536800888), attempt 1 |
| Pull-request workflow | [45536804986](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45536804986) |
| Qualified push artifact | 14988830641 |
| ZIP bytes | 55703959 |
| ZIP SHA256 | 6451f4892319c4fbbea3a671494c7be1e53a1887c45773b4d6cf08bdb1313211 |
| QEMU ELF bytes | 63709384 |
| QEMU ELF SHA256 | 056d68984206413da6df6dad51e63899ba2b493936e6c92345fe264cf0b5f5e4 |
| Actual runtime boot runner SHA256 | 61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1 |

Both workflows completed successfully. The audited and executed binary is the
exact push artifact above. The pull-request workflow has its own run identity
and does not supply the binary identified by this record.
[manifest.json](manifest.json) records the complete five-runner identities,
compiled-artifact qualification, native observation, and retained history.

The actual configuration preserved the original machine and 19-global policy,
then retained the previous ten additional globals in their original order.
Its single additional property was
**qcom-sm8250-rpm-stats.enabled-reference=on**, giving 30 unique globals.
The independent native audit checked the recorded plan, actual command
arguments, selected inputs, deployed ELF, and all five runner files.

## Seven-part compiled-artifact qualification

The seven reviews covered aggregate/VIDEOCC, SPSS, DSI, DISPCC, DP, LLCC,
and the RPM ordinary fixtures. Their immutable composite receipt is 7127 bytes,
SHA256:

5acba43834a0da17646e48199e40c097b0ecb8be6b257c84b4398f87022c4765

The manifest records each part's complete receipt length, SHA256, auditor
identity, and passed verdict. All seven complete receipt files were re-read
and their identities checked during this documentation preparation.

The aggregate audit validated all **97 component and five Linux contracts**.
It also read the retained ci-verdict-unit component logs and found exactly
one “Ran 36 tests” line and a standalone “OK” line. This count refers to those
actual component logs and is separate from the workflow's initial validation
job log.

The RPM review reconstructed the actual **15 ordinary fixtures**, including
the directory cell and complete **96-byte, three-record initialized window**.
Source-generation evidence verifies **six complete pinned public source files**
and **two generated outputs**. The model represents an immutable initialized
reference, with reference mode explicitly enabled for the tests and native run.
It does not execute AOP firmware or produce current statistics.

**RPM migration remains unqualified.** No normal RPM migration, profile
mismatch, or RPM wire-payload result is claimed. The RPM fixture audit also
does not claim EOF verification or an independently asserted shutdown-event
reason and order. The other component audits parsed existing CI-generated
migration evidence read only. These audits created, modified, loaded, or
injected no stream.

Public-source verification does not reverify the private firmware evidence
behind initialized record values and does not establish pristine factory
provenance.

The composite was assembled at **2026-11-25T03:32:37.317647+00:00**, before the
native run. Its then-recorded native-execution and release-ready flags remain
false in the unchanged receipt. A separate exact-tree single-owner review
later released this lane for the one diagnostic below. Model integration and
merge decisions remain separate.

## Single native diagnostic

The native invocation ran from **2026-11-25T03:51:11.656930+00:00** to
**2026-11-25T03:51:19.898178+00:00**. The selected full-storage ready artifact
was absent, so the recorded selection attached **zero original LUNs**.
Storage completeness, snapshot consistency, restore qualification, and
pristine-factory qualification remain false.

| Guest time | Retained observation |
| --- | --- |
| 0.000000 s | Kernel entry |
| 2.823067 s | Initramfs unpack began |
| 3.210327 s | First fatal synchronous external abort, syndrome 0x96000010 |
| 3.210710 s | Faulting PC reported as ddr_stats_probe+0x88/0x180 |
| 3.212890 s | Kernel panic: attempted to kill init, exit code 0x0000000b |

The complete QEMU log includes a rejected four-byte read at **0x0c3f001c**.
It also records a four-byte rejection at **0x0000001c**; that observation is
retained literally, without inferring a further mapping or alias.
The earlier fatal RPM directory read at 0x0c3f0004 is absent from the
recomputed hardware diagnostics. The observed DDR probe establishes this
run's later frontier; it does not establish DDR table contents, record count,
names, residency values, or a functioning DDR statistics device.

The native runner and invocation returned **2** with **kernel-failure**.
The QEMU process returned **0** after its recorded host-requested shutdown.
No Android userspace or boot-completed milestone was observed.

The private native result is 7334 bytes, SHA256
354333ad46fea798dee671b934bc48b9034cde651f597ad65bade0cadfd5d104.
The manifest separately records hashes for the run receipt, saved QMP list,
kernel printk log, QEMU log, and completed audits. These hashes bind retained
private evidence; they do not make the native files publicly downloadable.

## QMP cleanup

The saved QMP list contains **22 messages**. Its event order is RESUME,
WATCHDOG reset, guest-reset SHUTDOWN, STOP, and host-qmp-quit SHUTDOWN.
The final status is **shutdown, running=false**. Quit request **16** has its
matching acknowledgement, and the saved quit tail matches the result.

The drain ended with **connection reset, errno 104**, with the connection
closed, **zero residual bytes**, no prior-command error, and no drain error.
This is the actual reset-terminal record; it is not reported as EOF.
No host termination of QEMU was recorded.

The actual runtime uses strict runner 61093aab. The audit evaluated its
unchanged pure functions and an identically hashed historical strict source
against the original report and against the report aligned with saved QMP
history and independently read logs. Both evaluations return
**cleanup=true, Android boot=false**. The audit reopened no socket and
replayed no guest.

## Completed independent native audit

The auditor ran once from **2026-11-25T03:58:10.456980+00:00** to
**2026-11-25T03:58:13.534677+00:00**. It was admitted through the original shared
heavy-work helper with a bounded 45-second admission wait; admission and
auditor return codes were **0**, with no retry. Its complete result is
30630 bytes, SHA256:

66974b06b3deba68ee4cd369ec1ee7b68e5690ccd2825c50adb84bf271e000b6

The auditor SHA256 is
58027f04fe3abe360ea2e7d363a8ce884e292ba16cebcda371acb246cfac2760.
Its source retains every check call site from the reviewed earlier native
auditor, with the new exact artifact, strict610 runtime, RPM lane paths,
97+5 gates, and 30 globals bound explicitly. Neither the fault location nor
a fixed count of 69 was a pass predicate.

[native-audit-completion.json](native-audit-completion.json) preserves all
**69 actual check names and passed verdicts**. Its role inventory covers the
**31 fully read regular files**, distinguishing complete-file digest
comparisons from record-consistency comparisons. It includes the deployed
ELF, selected original core inputs, saved original resources, runtime files,
configuration records, QMP history, and all four actual logs. The separately
pinned predicate-source copies are identified independently of that 31-file
Reader inventory.

The audit reclassified the complete logs and reproduced the original
observations. It verified original-input and QEMU preservation, exact actual
arguments without publishing them, and the original result's unchanged
bytes. The execution wrapper also retained before/after source and native-file
hashes. No section failed.

This native audit **did not read or rehash the CI ZIP**; its archive evidence
is the earlier seven-part qualification. It ran no QEMU or guest, opened no
QMP connection, and created, modified, or loaded no migration stream.
Preparing these publication files read existing receipts and performed no
additional auditor, native, CI, or component-test execution.

## Retained failure history and separate source lineages

The initial PR37 head
9ebae17e394171560ffc55bea52ad1df4a29b2d7 failed both workflows:
96 of 97 component gates passed, but the RPM test expected QOM type “str”
where the actual type is “string”. No Linux result or native qualification is
claimed for that failed artifact. Its identities remain recorded separately
in the manifest.

The 8e8eeb follow-up changed only that test's type spelling, one line removed
and one added. It retained the model, contract, harness, case count, and other
assertions. The successful artifact and native observation above have their
own exact identities; they do not replace the earlier failed records.

PR36's 66d9e20a9541b503ed4f5cac5c79c8a5b4a0809b is a separate four-byte
offset-only implementation. Its CI and native qualification remain separate
from this full-record-window implementation.

The selected documentation parent is
420819df9cfd76dc98cb4705987c269047d7f214, whose tree contains the PR36
lineage. The observed executable is the standalone PR37 8e8eeb tree.
Recording this evidence on the documentation parent does not claim that
PR37's source is integrated into mainline, transfer qualifications between
the two implementations, or qualify a merge that instantiates both devices.
