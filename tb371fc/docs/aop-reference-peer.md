# Explicit reference AOP QMP peer

## Select the profile

`-machine tb371fc,aop-profile=reference-qdss-vote` creates
`/machine/aop-peer`. The default `aop-profile=none` creates no peer.
The existing [message SRAM](aop-msgram.md) remains ordinary writable,
migratable RAM. Its default cold-zero/reset-retention behavior is unchanged.

This profile implements a QMP transport and two logical clock votes. It is
not captured AOP firmware, a complete clock controller, a trace engine, or a
physical frequency/power model. A passing native mailbox/clock probe would
establish only this transport/service boundary.

## Reference initialization and actual routing

The captured 4 KiB SRAM at `0x0c300000` and IPCC at `0x00408000` are reused.
The peer accepts only APSS client 8 -> AOP client 0, signal 0. It replies
through `qcom_ipcc_receive(0, 0)`; existing masking, pending, clear, migration,
and the board's real GIC SPI 229 connection remain in effect.

Descriptor offset zero contains MAIL magic `0x4d41494c`, version 1, features
zero, AOP link UP and initially disconnected channels. The reference assigns
two 0x100-byte mailboxes, including each length word: AOP at offset 0x100 and
APSS at offset 0x200. These mailbox sizes/offsets are deliberately chosen
reference inputs, not a capture of the tablet's firmware state. The peer
rejects modified geometry rather than trusting guest-controlled offsets.

The reference waits 1,000 virtual ns after an actual doorbell. Repeated
doorbells coalesce without postponing that deadline. This delay only exposes
a deterministic asynchronous boundary; it is not an AOP timing measurement.

## Link, channel and message semantics

The peer owns AOP link/channel state and ACKs of APSS state. The guest owns
APSS link/channel state and ACKs of AOP state. Internal phase is independent
of guest-writable SRAM ACK bytes:

1. APSS must acknowledge the AOP link and publish its own UP link before a
   doorbell causes the peer to acknowledge APSS link UP and notify IPCC.
2. An APSS channel-UP request opens the AOP channel and generates a notification.
3. A subsequent doorbell after APSS acknowledges AOP channel UP causes the
   APSS channel ACK. Completing this handshake does not consume a prematurely
   staged packet.
4. Only a connected channel with a new doorbell may consume a message.
   Payload publication precedes its nonzero 32-bit length. Length must be a
   multiple of four, fit the APSS mailbox, and contain a NUL terminator.
5. A supported command updates its logical vote and named GPIO output before
   the peer clears the APSS length and notifies IPCC.

The two supported resources are `qdss` and `qdss_ao`. Each independently
accepts the exact driver command `{class: clock, res: RESOURCE, val: VALUE}`
with VALUE 0 (off) or 1 (dynamic vote). They are votes, not Hz. There is no
invented frequency map or arbitration between these two resource names.

The vendor driver sends a 96-byte stack buffer; bytes after its first NUL
may be nonzero. The upstream driver sends a 64-byte zero-filled buffer.
The peer therefore parses only the command before the first NUL within the
published length. Trailing bytes are padding, never a second command.

Unknown classes, resources, values (including unimplemented known levels
2..7), additional keys, alternate syntax, missing NUL, invalid lengths,
modified descriptors and disconnected-channel packets remain unconsumed.
They do not generate a new completion IRQ. `last-error` and
`rejected-requests` record the refusal; `-d unimp` logs its category without
dumping payload contents. The guest's own timeout cleanup may clear length;
that is not an AOP acknowledgement.

The transport does not invent an error-response wire protocol. This limited
service deliberately leaves unsupported requests to the guest timeout path.
A preexisting IPCC interrupt also cannot establish success: the mailbox
length and the actually updated service state are the relevant evidence.

## Outputs, reset and migration

The read-only QOM properties `qdss-vote` and `qdss-ao-vote` match the
corresponding named GPIO outputs. They represent accepted off/dynamic votes.
There are no QDSS frequency or trace consumers in this board yet. Further
devices must consume these outputs before their behavior can be attributed
to the votes. `accepted-messages`, `notifications`, `state`,
`work-pending` and `notification-pending` provide transport observations.

A channel/link close prevents consumption but retains the last accepted
votes. Reference reset clears the votes, cancels pending work, and resets the
64-byte descriptor plus both reference mailboxes. Other SRAM bytes survive.
These are explicit reference reset/retention conventions. They do not change
the peer-absent SRAM behavior or claim silicon power-domain semantics.

A failed IPCC insertion (for example, a full sparse pending table) leaves an
outstanding notification and retries after 100,000 virtual ns. A retry never
reexecutes the command. Notification state, phase, votes, counters and virtual
timer migrate; SRAM and IPCC retain their existing migration machinery.
The always-present SRAM device carries a fixed migration configuration marker.
Both directions of a none/reference profile mismatch must fail, including
migration into a destination that has an extra peer device. The marker is
set only by the board before realization and cannot be changed through QOM.
All peer RAM writes mark the SRAM dirty. Post-load restores outputs and the
timer only: it never rewrites a descriptor or reads SRAM before RAM migration
has finished.

## Source evidence and remaining uncertainty

The following pinned primary sources establish the guest-visible protocol
and command vocabulary. They do not provide the tablet's AOP firmware image,
physical mailbox initialization, clock frequencies, analog behavior or timing.

- [Qualcomm vendor msm_qmp.c at a70fac8a](https://github.com/aosp-mirror/kernel_msm/blob/a70fac8a1606e46484717ac9f39f3348d4b47e6e/drivers/mailbox/msm_qmp.c):
  descriptor layout, bidirectional state/ACK transitions, payload publication,
  length-zero completion and separate lower-level doorbell completion.
- [Qualcomm clk-aop-qmp.c at a70fac8a](https://github.com/aosp-mirror/kernel_msm/blob/a70fac8a1606e46484717ac9f39f3348d4b47e6e/drivers/clk/qcom/clk-aop-qmp.c):
  blocking mailbox client, fixed 96-byte packet, QDSS proxy prepare vote and
  the qdss/qdss_ao resources.
- [Qualcomm clock binding at a70fac8a](https://github.com/aosp-mirror/kernel_msm/blob/a70fac8a1606e46484717ac9f39f3348d4b47e6e/include/dt-bindings/clock/qcom,aop-qmp.h):
  OFF=0, DYNAMIC=1, and the additional unimplemented performance levels.
- [Upstream qcom_aoss.c at 141b164b](https://github.com/torvalds/linux/blob/141b164bbe186423e295b139e40e8e2d11cf1341/drivers/soc/qcom/qcom_aoss.c):
  independent link/channel handshake and 64-byte QDSS off/on requests.
- [Captured geometry](../evidence/aop-msgram-20261123/geometry.json):
  public allowlisted SRAM and IPCC route fields. No firmware mailbox size,
  offset or clock frequency is inferred from this inventory.

Native production-kernel tests must still distinguish removal of a transport
timeout from full AOP functionality. Other AOP services, performance levels,
remote-originated messages and real QDSS execution remain unsupported.

## Acceptance gate

Run `python3 tb371fc/tests/test_aop_reference.py --qemu /path/to/qemu-system-aarch64
--out /tmp/aop-reference-test` with a fresh short output directory.
The independent public gate requires exactly 30 unique passing groups:
20 compiled-device protocol/service/IRQ checks, six completed two-process
migrations, two profile-mismatch migration rejections and two profile checks.
Subcases inside a group are reported as such and are not added to the group
count. The existing ten-check raw-SRAM gate remains separate.

The six completed migrations cover idle votes/GPIO output restoration, a
message with one virtual ns left, a pending link negotiation, an unconsumed
unknown command, a masked real IPCC reply and a notification retry while the
IPCC pending table is full. The two expected failures require a nonzero
destination exit and the SRAM profile-marker diagnostic. They are recorded
separately as rejected attempts, never counted as completed migrations.

The tests observe actual named vote GPIO outputs where needed. They never
intercept the IPCC IRQ; those cases inspect the real GIC pending state.
QTest drives registers and shared RAM directly and does not execute the
vendor CPU driver. Native driver validation remains a separate requirement.
