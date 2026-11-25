# Exact 54ec744 SPSS artifact: original-input diagnostic

The qualified SPSS binary advances beyond the previous invalid write to
`0x01881028` and stops at a new unmapped four-byte read from
`0x0c3f0004`, in `msm_rpmstats_probe+0x84/0x1c8`.
**Android boot remains unpassed.** This was one guarded zero-LUN diagnostic
using the original direct-kernel inputs.

## Source, binary and execution

| Item | Identity or observation |
| --- | --- |
| QEMU source | `54ec7443bc75460baa35a0c0b0502ae6cb4d8fa7` |
| Source tree | `6602490b518ab849dc9a0fd2636045c26954c145` |
| Push workflow | [45521455233](https://github.com/cyjin-yl/qemu-tb371fc/actions/runs/45521455233) |
| Actual push artifact | `14982026237` |
| ZIP bytes / SHA-256 | `55527863` / `9fdd4ad25d9b9f272456144de43f124381e03571c7d0d91d022d151ea82d687e` |
| QEMU bytes / SHA-256 | `63675200` / `b50345fe7dba411426779f27963ecb1b08d1ae6fa4d83e50e7cea4351cbf7d25` |
| Runtime boot runner | `61093aab7176842ef14f592e90456c0c0b91206d594447474d0a02cbf3fb10d1` |
| Invocation started / finished UTC | `2026-11-25T01:45:10.333865+00:00` / `2026-11-25T01:45:17.318711+00:00` |
| Result | `kernel-failure`; runner exit `2`; QEMU exit `0` |
| Original LUNs attached | `0`; complete-storage ready input absent |
| Android boot acceptance | `false` |

The [qualification](../spss-54ec744-qualification.json) and six actual
artifact audits were completed before execution. Its recorded native-false
state remains unchanged as a historical statement about that earlier stage.
The first 41 evidence files are preserved byte-for-byte by this publication.

## Independent review boundary

[independent-review.json](independent-review.json) is the exact completed
review receipt: **11,847 bytes**, SHA-256
`e729693fe5d6a7b5c7fb77472648cd78a45512e8ba7021ab052713fd0dd13c5b`.
[observation.json](observation.json) contains the selected result and its
bounds.

The offline review reconstructed and verified the collection payload and all
**20 retained files, totaling 814,924 bytes**, then cross-checked the input,
runtime, invocation, kernel-failure and QMP records. The publication step
independently rehashed those 20 local files against that receipt. Review
success describes the consistency and byte identity of the retained evidence;
it does not mean that Android boot passed.

The input review checks exact planned identities, the emitted QEMU command,
and separate runner/coordinator post-run digest records. It confirms that
the actual `-append` string retains the original **1,392 bytes** and SHA-256.
The original kernel, ramdisk, full DT, bootargs, AOP input and symbols retain
their recorded before/after identities. Original input byte files were not
copied to the independent reviewer. Deployed ELF and runner pins are the
root collector's runtime hash observations, cross-checked against the
qualified artifact; the offline reviewer did not query the device again.

The invocation contains **29 unique global property keys**. The previous
28 are retained in order, with
`qcom-sm8250-spss-irq-mask.enabled-reference=on` as the sole added global.
The machine remains the same as the preceding LLCC diagnostic. There was
no test-init injection, reduced DT, bootargs change, or firmware boot-chain
execution.

## Actual next frontier

The sole invalid MMIO record is a four-byte read at `0x0c3f0004`.
The production kernel reports PC `msm_rpmstats_probe+0x84/0x1c8`,
LR `msm_rpmstats_probe+0x80/0x1c8`, and opcode `b9400017`
(`LDR W23, [X0]`). The first failure occurs at guest **3.064255 seconds**;
panic follows at **3.067152 seconds**.

The earlier SPSS invalid access is absent, and a SPSS consumer-link message
is present. There is no explicit SPSS probe-success message. These observations
establish progress beyond the prior abort without establishing SPSS READY,
IRQ delivery, firmware or PAS behavior.

## QMP cleanup

The 22 saved QMP messages contain this event order:
`RESUME → WATCHDOG(reset) → guest SHUTDOWN(guest-reset) → STOP →
host SHUTDOWN(host-qmp-quit)`.
The most recent relevant guest transition before host quit is the qualifying
guest-reset SHUTDOWN. Final status is `shutdown, running=false`.

Quit request `16` has its unique empty-object acknowledgement. Drain ended
with connection reset (errno `104`), **zero residual bytes**, and QEMU exit
`0`; no forced host termination occurred. The actual strict PR32 runtime
therefore reports normal QMP cleanup while preserving
`stock_android_boot_passed=false`. This exercises its new shutdown-state
provenance path. No QMP replay or guest rerun was used for review.

The receipt also retains the reviewer's initial option-name assertion error:
the saved command uses `-M`, while the first review expected `-machine`.
Only that review assertion was corrected; native execution and evidence
bytes were not changed.

## Limits

Storage completeness and snapshot consistency remain false. Android
userspace, interactive display and boot completion were not observed.
The separate earlier `3bd583f` execution has its own artifact and evidence;
its timing and results are not substituted for this run.

Only this README, the bounded observation and the exact independent review
are published here. Full private printk/QEMU logs, commands, bootargs and
host/Windows checkpoints remain private. The hashes in the review bind those
retained files and are not public download links.
