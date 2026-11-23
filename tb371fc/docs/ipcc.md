# Qualcomm IPCC APPS interrupt-message frame

This model implements the APPS IPCC frame's interrupt-message transport.
Incoming messages have real pending latches and independent enable masks,
and they drive the board's real GIC input. Sending to an absent coprocessor
never produces a receive event or firmware reply.

## Native mapping and source contract

The captured native node `/soc/qcom,ipcc@408000` has compatible
`qcom,ipcc`, MMIO `0x00408000 + 0x1000`, GIC SPI **229**, and
level-high signaling. It uses three interrupt cells and two mailbox cells.

The native `ipcc-self-ping-apss` node requests client **8**, signal **2**
for both its mailbox and interrupt. The public Linux binding independently
names client 8 as APSS. The model therefore uses `local-client-id=8`.
It does not infer that every other client is an implemented device.

| Offset | Implemented contract |
| --- | --- |
| `0x08 CONFIG` | Bit 0 selects clear-on-RECV_ID-read; other bits rejected |
| `0x0c SEND_ID` | Recipient in bits 31:16, signal in bits 15:0 |
| `0x10 RECV_ID` | Selected enabled pending sender/signal, or `0xffffffff` |
| `0x14 RECV_SIGNAL_ENABLE` | Enable one exact packed sender/signal |
| `0x18 RECV_SIGNAL_DISABLE` | Mask one pair without clearing its pending latch |
| `0x1c RECV_SIGNAL_CLEAR` | Acknowledge only the specified pair |
| `0x38 CLIENT_CLEAR` | Value 1 resets this frame's protocol state |

The Linux upstream and downstream drivers establish the packed-ID format,
explicit acknowledge, mask/unmask and software-reset operations. A later
upstream fix documents CONFIG's clear-on-read behavior; the model supports
both modes.

## Message and interrupt semantics

A peer calls `qcom_ipcc_receive()` with its sender ID and signal. That
sets the corresponding pending latch. The summary interrupt is high
exactly while at least one pending pair is enabled. Multiple arrivals to an
already-pending pair coalesce as a latch; they are not an invented firmware
message queue.

RECV_ID selects the lowest packed enabled-pending ID in this reference
implementation. The sources establish selection but not silicon priority
arbitration, so this ordering is an explicit model choice. Masked pending
messages are retained and become visible when enabled. Physical masked-edge
and priority differential tests remain future work.

With clear-on-read disabled, repeated RECV_ID reads retain the same pair
until software writes CLEAR. With it enabled, the read consumes that pair
and updates the summary line. Clearing a different ID leaves the original
event pending.

An APPS SEND_ID to APPS routes through the same receive implementation.
This is the local transport path used by the native self-ping node, not a
fabricated reply from a DSP. Sending to any other client invokes an
explicitly installed peer callback, if present. With no callback, the model
records the outgoing request, logs the missing recipient and produces
**no receive event**. The write itself is a fire-and-forget transport action;
it is not a firmware service acknowledgement.

Normal TCG execution has no test injection property. QTest adds an explicit
`qtest-rx-message` QOM property which invokes the ordinary peer-receive API.
It permits independently controlled source events for controller tests;
it is not a guest register or a simulated firmware response.
Read-only `last-transmit` and `transmit-count` properties are observer
instrumentation, and reset with the frame.

## Bounds and unsupported behavior

The protocol accepts full 16-bit sender/recipient and signal fields, except
the reserved `0xffffffff` no-pending sentinel. A sparse table retains at
most 4096 simultaneously enabled or pending pairs. This is an explicit
emulator memory bound, not a measured silicon capacity. Exhaustion returns
a failed transaction/event and preserves existing state. Clearing and
disabling a pair releases its slot.

Unknown register accesses, invalid widths, unsupported CONFIG bits and
invalid reset values fail with `MEMTX_ERROR`; they cannot be captured as
successful fabricated zeros by another DMA device. Power collapse,
protocol/frame routing beyond this APPS frame, coprocessor execution,
SMEM/GLINK/SMP2P payloads and remote firmware services remain outside this
device's scope.

## Reset, migration and tests

Reset clears masks, pending latches, CONFIG and observer transmit state.
Migration serializes those states and checks the destination's local client
identity. Incoming state rejects duplicate IDs, the no-pending sentinel as
a live pair, inconsistent unused slots and unsupported CONFIG bits. The
destination reconstructs the summary interrupt from actual pending masks.

```sh
python3 tb371fc/tests/test_ipcc.py \
  --qemu /absolute/path/qemu-system-aarch64 --out /work/ipcc-new-result
```

The suite exercises actual QTest MMIO/GPIO behavior, APPS self routing,
separately injected peer events, negative remote-send cases, capacity
bounds and real process-to-process migration. One case preserves the real
GIC connection and checks SPI229 (architectural INTID261) pending delivery,
mask/unmask, explicit acknowledge and clear-on-read deassertion. The other
IRQ cases and migration observe the output wire through QTest interception,
which disconnects that wire from the parent GIC. This distinction is
explicit in the receipt. The migration fixture disables UFS; it is not a
full-board storage migration claim.

On 2026-11-23 the frozen integrated executable passed **16/16** groups.
The real-GIC case and the actual two-process transfer both passed.
The executable SHA256 is
`f22f5f844d3b12229ca159c8435235ba483dea22854e0b755eb2602442f3ea77`;
the test SHA256 is
`3cce319a0759c70d7b5cb1dc4604ef899a3bbb54b289cbb77144cebbdf6a91a5`.
See [the result receipt](../evidence/ipcc-20261123/result.json) and
[the source/native-DT evidence](../evidence/ipcc-20261123/source-evidence.json).
This is controller validation, not a claim that DSP firmware, Android
services or the complete tablet have booted.

## Sources

- [Linux v6.12.114 mailbox driver](https://github.com/gregkh/linux/blob/v6.12.114/drivers/mailbox/qcom-ipcc.c),
  SHA256 `2015e9a0c29f2cf947ed2f93c4a2b4701b946f20e014d152ef85da7a85b926a3`.
- [Qualcomm downstream driver, Google kernel/msm snapshot
  129c41b37ca84cc74569f71fa20a1d8f58609931](https://android.googlesource.com/kernel/msm/+/129c41b37ca84cc74569f71fa20a1d8f58609931/drivers/soc/qcom/qcom_ipcc.c),
  SHA256 `99d27e38912f687febbeee171cf8f2788f2409efada025e479350cd363c2785c`.
- [Linux mailbox binding IDs](https://github.com/gregkh/linux/blob/v6.12.114/include/dt-bindings/mailbox/qcom-ipcc.h),
  SHA256 `e49a0b0731c1a56fea0ad82863e1daa905fad71c5d5780c11976f097d485b677`.
- [Upstream clear-on-read handling](https://android.googlesource.com/kernel/common/+/b2426b5bc9b062c2d87d00ca37c9215a80701e42/drivers/mailbox/qcom-ipcc.c).
- [Lenovo community candidate](https://github.com/tem423/android_kernel_lenovo_tb371fc/blob/9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1/drivers/soc/qcom/qcom_ipcc.c)
  corroborates the downstream probe/reset behavior; it is not asserted to be
  the exact complete source of the installed production Image.
  SHA256 `5cfb28e31e8d97e047f3a59022a3c29d193c416df6c70a56ce4a9bdce5f2d880`.
