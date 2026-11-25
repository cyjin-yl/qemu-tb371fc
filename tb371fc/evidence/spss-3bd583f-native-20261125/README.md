# Exact 3bd583f SPSS artifact: native diagnostic observation

The unchanged original-input diagnostic using the qualified SPSS artifact
advanced beyond the previous interrupt-mask abort at `0x01881028`, began
unpacking initramfs, and reached a new fatal 32-bit read at `0x0c3f0004` in
`msm_rpmstats_probe+0x84/0x1c8`. **Original Android boot remains unpassed.**
This was a zero-LUN kernel diagnostic; Android userspace, boot completion,
and an interactive display were not observed.

This directory is a small publication draft containing a source/artifact
manifest and selected observations. The raw native receipts and original
inputs remain private. The full independent native input/binary rehash remains
pending. A separate, completed evaluation of the original and PR32 pure
cleanup/acceptance predicates on three small saved JSON files returns
`cleanup=true, Android boot=false` for both sources. That evaluation does not
claim a complete native input audit.

## Exact source and compiled artifact

| Item | Identity |
| --- | --- |
| QEMU source | `3bd583f2079634d01c12160ab3e3f0c5a3e47e86` |
| Source tree | `9ca3711e9aa2e27e6eac8a17ce7b3367dc73c08c` |
| Push workflow run | [45513996787](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45513996787) |
| Board artifact | `14978715300` |
| ZIP bytes | `55529329` |
| ZIP SHA256 | `b857eb4e34ac954159ad8aaca1d43979879aa035e5966dbd03c0ca41c27a71d8` |
| QEMU ELF bytes | `63675912` |
| QEMU ELF SHA256 | `fbb1081261d2dea28b267142db7fe48e8e80853fbc9dc53513e82b543087aea5` |
| Runtime boot runner SHA256 | `ebba3b55ae4f931e5768717d635647dd855ab56a1efb19350acbaf1a4498b3eb` |

The board ZIP is the exact push artifact of the source above. It is not a
pull-request merge-ref binary. The artifact API metadata independently names
the same source and ZIP digest. The public CI artifact identifier does not
provide public access to the separate native-run receipts.

All five deployed runner files were independently read and matched to their
exact 3bd583f source pins. Their complete identities appear in
[manifest.json](manifest.json). The native runtime kept the original
`ebba3b55...` boot runner; it did not substitute the later PR32 runner.

## Six-part compiled-artifact qualification

The actual push artifact passed **95 component gates, five Linux gates, and
all 33 acceptance-harness unit tests**. Six independent reviews covered the
aggregate and VIDEOCC, SPSS, DSI, DISPCC, DP, and LLCC evidence. The composite
receipt is SHA256
`7e9abd0faf58f18f96d0b3231cb0ff6a49ef525d3cc466b22b2f41bf9ddad150`.
The manifest records every part's scope, byte length, complete receipt hash,
and passed verdict. Those six small receipts were re-read and their exact
bytes checked while preparing this draft.

The reviews read existing streams from the actual CI ZIP. They did not
generate, modify, or inject migration streams, and this preparation did not
repeat the artifact execution. The composite predates the native run: its
recorded `native_executed=false` describes qualification at that time.
The native observation below is a separate, later record.

## Actual original-input diagnostic

The invocation ran from `2026-11-25T00:51:18.666533+00:00` to
`2026-11-25T00:51:28.347909+00:00`. Selection attached **zero original LUNs**;
the full-storage ready artifact was absent at selection. Storage completeness
and snapshot consistency remain false.

| Guest time | Retained observation |
| --- | --- |
| `4.104428 s` | `Trying to unpack rootfs image as initramfs...` |
| `4.200159 s` | `Freeing initrd memory: 1264K` |
| `5.079403 s` | First fatal synchronous external abort, syndrome `0x96000010` |
| `5.079805 s` | Faulting PC reported as `msm_rpmstats_probe+0x84/0x1c8` |

The corresponding QEMU log records a rejected four-byte read at
`0x0c3f0004`, with no mapped region. It contains no recurrence of the prior
`0x01881028` SPSS rejection. The observed later execution establishes that
the prior abort no longer blocked this run. It does not establish SPSS READY,
IRQ delivery, firmware authentication, PAS success, or a complete SPSS model.

The original coordinator and pinned runtime runner report that the kernel,
initrd, DTB, boot arguments, AOP input, symbols, and QEMU bytes remained
unchanged. These are explicitly labeled **coordinator/runtime-reported**
preservation claims here. The limited independent inspection did not yet
rehash every original input and the complete deployed ELF.

The native result SHA256 is
`f601f7b1b1ffb42f6f8bfab67997353a3718dccb94e34bc85135375dcb6561f3`.
The manifest separately identifies the saved QMP messages and fault logs by
hash. These hashes bind private retained records; they are not download links
or a claim that the full native artifacts were published.

## QMP cleanup and its exact review boundary

The saved complete QMP list has 22 messages. Independent read-only inspection
confirmed its event projection and quit tail match the retained result.
After the watchdog reset, the guest emitted `SHUTDOWN` with
`guest=true, reason=guest-reset`; this preceded the host's
`guest=false, reason=host-qmp-quit` event. The final status was
`shutdown, running=false`. Quit request `16` has its matching acknowledgement;
the retained drain reports EOF, zero residual bytes, and no command/drain
error. QEMU's process return code was **0**, while the runner and invocation
returned **2** with `kernel-failure`.

Thus the original runtime reported `qmp_shutdown_normal=true` together with
`stock_android_boot_passed=false`. Cleanup and Android acceptance are separate
results. The initial limited independent inspection read the saved evidence;
the subsequent predicate evaluation used exact small JSON copies. Neither
operation reopened QMP or replayed the guest.

The later strict predicate source is separately pinned to PR32 commit
`da25f3a5d1128a03e0cd4e1c0a5d4fa20d0892d7`, merged as
`28def523ecd39c90bdedee7a2b38ba71353b5f88`, with boot runner SHA256
`61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1`.
Its unchanged pure functions were separately evaluated on the exact saved
result, plan, and QMP message JSON copies. Both the original-report and
raw-QMP-aligned evaluations return `normal_qmp_shutdown=true` and
`stock_boot_passed=false`. The original ebba functions return the same results.
The strict `stopped_before_quit` result is true: the earlier WATCHDOG remains
in the history, and the following guest-reset SHUTDOWN is the latest relevant
guest transition before host quit. The runtime runner was not replaced.

The initial limited-inspection receipt is
`4783b52d94c6c711afa55f070b0554a590bbfb6e4ef66b59a0b118f0ca25096c`.
It retains its then-pending predicate status. The later, separate predicate
receipt is
`1d52c4824706d104d46f76e17be3d1dd86a16fea174aad20b0b2f6c1f7bf2b48`.
The manifest records both sources, selected AST identities, input hashes,
and actual function results. The complete remote native auditor, full original
input/ELF rehash, log reclassification, and any guest replay remain outside
this small predicate operation.

## Relation to the active SPSS pull request

[PR30](https://github.com/cyjin-yl/qemu-tb371fc/pull/30) had advanced to
`54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7` on base
`28def523ecd39c90bdedee7a2b38ba71353b5f88` when this status was read.
It remained draft/open; its A77 and ready-handoff checks had passed while
its board checks were still running. This is a retained status snapshot,
not a statement about the pull request's later state.

The exact 3bd583f-to-54ec744 comparison contains the PR29 public-source
retry integration, PR32 runner provenance integration, and the owner's two
merge commits. Nineteen files changed. No device model, board mapping,
generated device data, SPSS device test, or acceptance-gate harness changed.
The source history and complete changed-file inventory are recorded in the
manifest. The newer tree still requires its own compiled-artifact identity
and qualification; this directory does not transfer the 3bd583f qualification
or native observation to it.

**PR31's DISPCC connected-provider guard was not included in the observed
3bd583f binary.** The diagnostic does not qualify that guard or claim that
its coverage exists in this runtime. PR29 and PR32 were also absent from the
observed source; recording this evidence on a newer base does not alter the
identity of the executable that actually ran.

No original firmware bytes, boot arguments, serial number, private absolute
paths, or private host identity are included in these publication files.
