# GENI UART behavioral model

The `qcom-geni-uart` SysBus device implements a **FIFO-only behavioral subset**
for developing the TB371FC board machine. The board maps its console at
`0x00a90000`, size `0x4000`, SPI 357, based on the owner's live Android DT.
The device is reachable at the QOM path `/machine/uart`.

## Implemented behavior

- 32-bit little-endian MMIO; TX/RX packed bytes are transferred low byte first.
- Primary sequencer `START_TX`, transfer byte count, transmitted-length counter,
  FIFO watermark, command complete, cancel/abort, command overrun, illegal command,
  and writes without an active transmit command.
- Secondary sequencer `START_READ`, FIFO receive through a QEMU chardev, word count
  and last valid byte count, receive-length counter, read underflow, cancel and
  abort. Aborting clears the model's unread RX FIFO; cancel preserves it.
- Independent M/S interrupt status and masks, enable-set/enable-clear aliases,
  write-one-to-clear event bits, global M/S interrupt gates, and the secondary
  interrupt summary. Watermark conditions are level-derived and therefore may
  remain asserted after a W1C write while their condition remains true.
- Nonblocking TX chardev writes, host backpressure, bounded FIFOs, reset, and
  VMState serialization with incoming FIFO-count validation. The output watch is
  recreated rather than migrated as a host-specific descriptor.
- Firmware protocol identity is UART (`GENI_FW_REVISION_RO[15:8] = 2`). No GENI
  firmware is loaded, interpreted, redistributed, or authenticated by this model.
- DMA-mode sequencer commands fail explicitly with `CMD_FAILURE`; an unknown
  opcode produces `ILLEGAL_CMD`. Neither reports successful completion.

## Model choices and limits

The following values or behaviors are **not measured TB371FC register values**:

| Choice | Model behavior |
| --- | --- |
| FIFO geometry | 16 words of 32 bits, 64 bytes for each direction; exposed in `SE_HW_PARAM_0/1` |
| Reset word length | Eight bits for TX and RX |
| RX stale timing | Every received host chunk becomes immediately available as a last/stale packet; no baud-derived timer |
| TX timing | Bytes drain as fast as the chardev accepts them; no bit-rate delay |
| Unconnected chardev | TX bytes are consumed as a sink, as with other emulated serial ports |
| CTS/RX line status | RX idle high, CTS asserted; no electrical line simulation |
| Control/config registers | Known initialization registers retain values, but do not implement parity, packing permutations, modem flow control, loopback, or power collapse |
| Firmware identity | UART protocol identity only; no claim about a ROM/firmware revision |

Only 8-bit characters packed four per 32-bit FIFO word are implemented. Changing
packing or character format does not create the corresponding serial behavior.
DMA, GSI, firmware program RAM, hardware register reset fidelity, UART clock
selection, pinmux, physical RTS/CTS, break/parity error generation, and silicon
performance are not implemented. Unsupported accesses are logged with QEMU's
`LOG_UNIMP` category (`-d unimp`); unimplemented reads return zero. These reads
must not be interpreted as valid silicon observations.

The `GENI_FORCE_DEFAULT_REG` is retained as an IO-policy register. It is **not**
used as a fictitious device-wide FIFO reset. Reset comes from QEMU device reset
or the explicit external reset input; sequencer cancel/abort semantics are
independently modeled.

## Linux contract used

Definitions and FIFO sequencing were checked against upstream Linux **v6.12**:

- [GENI register definitions](https://github.com/torvalds/linux/blob/v6.12/include/linux/soc/qcom/geni-se.h)
- [GENI UART driver](https://github.com/torvalds/linux/blob/v6.12/drivers/tty/serial/qcom_geni_serial.c)
- [GENI wrapper and initialization](https://github.com/torvalds/linux/blob/v6.12/drivers/soc/qcom/qcom-geni-se.c)

These sources describe the software-visible contract. They do not establish
that the model matches undocumented downstream firmware behavior. The Linux
console driver uses FIFO mode; its high-speed UART DMA path is outside scope.

## Recorded acceptance — 2026-11-23

These results were obtained from the compiled QEMU variant on doesvm in the
isolated CT102 development container. They are executed results, separate from
the passive Android DT evidence used to choose the board address and interrupt.

| Acceptance | Recorded result | Evidence |
| --- | --- | --- |
| Actual QTest MMIO, IRQ and chardev suite | **13/13 passed**, exit 0, 128 MiB test RAM | [GENI QTest report](../evidence/geni-qtest.json) |
| Actual process-to-process QMP migration | **6/6 stages passed**, source and destination completed, 128 MiB per process | [GENI migration report](../evidence/geni-migration.json) |
| Unpatched upstream Linux 6.12.114 smoke | **8/8 guest checks passed**, QEMU exit 0, eight CPUs and 8 GiB configured | [Linux result and binary identities](../evidence/linux-smoke.json), [serial log](../evidence/linux-smoke.log) |

The migration run preserved an active eight-byte TX command after `ABCD` had
already been transmitted, seven unread RX bytes including a partial FIFO word,
interrupt state, related registers, and a RAM marker. The destination transmitted
only the remaining `EFGH`, drained the original RX bytes, cleared the pending
IRQ, and then received a new `Z` byte with a new interrupt. This demonstrates
those serialized states and the tested continuation path; it does not cover
all possible backend failures or every in-flight migration state.

The published Linux smoke run started at 2026-11-22 22:47:01 UTC
(2026-11-23 in Asia/Shanghai). Its serial log establishes the actual console
handoff and subsequent output:

- The early console selected `qcom_geni` at `0xa90000` and printed the boot log.
- The normal driver registered `a90000.serial` as `ttyMSM0` and enabled its
  console.
- With **no `keep_bootcon` argument**, Linux explicitly disabled the
  `qcom_geni0` boot console at guest log time `0.367111`.
- After that handoff, Linux reached PID 1, emitted all eight passing guest
  checks through the console, ran the same AArch64 counter probe binary used
  on the tablet, and powered down with QEMU exit status zero.

An earlier exploratory success retained the boot console and could not establish
an exclusive handoff; the published acceptance run removes that ambiguity.
This proves the exercised early-console and normal-console TX paths. It does
not exercise interactive Linux TTY receive or modem control. The direct QTest
suite independently exercises the RX FIFO and IRQ behavior.

The counter probe reports 19,200,000 Hz and a counter that advances in both
Android and the QEMU guest. That limited architectural agreement is not GENI
hardware equivalence, UART baud/timing calibration, or a performance benchmark.
DMA, GSI, silicon timing, stock Android boot and complete tablet emulation remain
outside the passing results above.

## Reproduce acceptance

Run against the built QEMU variant:

```sh
python3 tb371fc/tests/test_geni_uart.py --qemu /work/build/qemu-system-aarch64
```

The suite starts the actual `tb371fc` machine with the QTest accelerator and
128 MiB of test RAM (the board default remains 8 GiB),
connects separate QTest/QMP/serial Unix sockets, performs MMIO reads and writes,
checks the intercepted hardware interrupt output, and verifies real serial
bytes. It does not mock the C model and does not touch the physical tablet.
It emits JSON and returns nonzero on a missing/failed check.

The 13 checks cover identity, TX partial-word padding, masked pending/W1C IRQs,
global gates, watermark reassertion, enable aliases, cancel/abort/overrun,
RX partial words and IRQs, RX underflow, RX FIFO backpressure, RX abort,
unsupported DMA/opcodes, and reset. Assertions are explicitly required: launching
with Python `-O` exits with an error rather than silently skipping checks.

The independent migration suite uses two real QEMU processes and a Unix socket
migration channel:

```sh
python3 tb371fc/tests/test_geni_migration.py --qemu /work/build/qemu-system-aarch64
```

It migrates an unfinished transmit command, seven unread RX bytes, pending
interrupts, and a RAM marker. It checks the destination's register state and IRQ,
then completes TX without replaying the already sent prefix, drains RX including
the partial word, clears the pending interrupt, and receives new data through
the destination chardev. This suite also uses 128 MiB per process to keep the RAM
scan bounded. The recorded run above passed; when changing the model, retain
the new JSON report and binary identity instead of inferring a pass from the
existence of the test.

The two QTest suites do not establish Linux driver probe or boot. The separate
Linux smoke result above provides the recorded probe, console handoff and boot
evidence. None of these results establishes stock Android boot, hardware timing
accuracy, or full tablet emulation.

## Clock and reset inputs

The expanded machine connects three optional QEMU Clock inputs named `se-clk`,
`m-ahb`, and `s-ahb`, plus an active-high named GPIO `reset`, index 0. They are
fed by the [GCC model](gcc-sm8250.md); wrapper 2 BCR is GCC reset binding 26.
Unconnected inputs retain the original standalone device behavior. The
TB371FC board connects all three, so its UART operation is governed by actual
GCC state rather than merely advertising clock rates in DT.

The digital gating policy is explicit:

- Stopping `se-clk` pauses TX draining, applies RX backend backpressure and
  quiesces the output IRQ while retaining FIFO, command and latched status.
  AHB register access remains possible. A zero-length TX command also waits
  for the engine clock before reporting completion.
- Stopping either AHB input rejects MMIO with `MEMTX_ERROR` and diagnostics;
  rejected writes do not change register or FIFO state. The engine remains
  paused until the needed clocks return.
- Restoring clocks drains queued TX bytes, accepts queued host input and
  recomputes IRQ from the retained state. This host backpressure is not a
  physical claim that a UART wire preserves characters during clock loss.
- Asserting `reset` clears commands, FIFOs, counters and interrupt state.
  MMIO is rejected while reset is held. Deassertion permits fresh commands
  when the required clocks are available; reset does not replay prior bytes.

These are documented functional policies for the digital model. They do not
establish the exact silicon AHB gating topology, asynchronous signal behavior,
reset pulse timing, baud rate or physical RX data loss. GENI's internal clock
selection/divider registers and electrical serial waveform remain unimplemented.

Connected clock periods, held reset and deferred zero-length completion state
are serialized in an optional migration subsection. A linked integration test
transfers queued TX and unread RX while the SE clock is stopped, checks the
destination stays paused, then restores GCC's clock vote and checks exact
continuation without duplicate bytes.

```sh
python3 tb371fc/tests/test_geni_clock_reset.py --qemu /work/build-next/qemu-system-aarch64 --work-dir /work/geni-clock-qtest
python3 tb371fc/tests/test_geni_clock_reset.py --qemu /work/build-next/qemu-system-aarch64 --migration-only --work-dir /work/geni-clock-migration
```

The earlier 13/13, 6/6 and Linux receipts above predate this GCC integration and
remain unchanged. The new binary SHA-256
`56717a3c95ba31da84ed46da8c305bc6de910370a359eee475b3587b93d5cabf`
actually passed [6/6 clock/reset checks](../evidence/expansion-20261123/geni-clock-qtest.json),
[4/4 stopped-clock migration checks](../evidence/expansion-20261123/geni-clock-migration.json),
and the original [13/13](../evidence/expansion-20261123/geni-regression.json) and
[6/6](../evidence/expansion-20261123/geni-migration-regression.json) regression
suites, all with exit 0. The paused migration retained four queued TX bytes and
three unread RX bytes while the destination clock stayed off; reenabling its
GCC vote transmitted only the unsent `EFGH` and restored the pending IRQ.

Those receipts precede default UFS integration. Current scripts isolate these
components with `-machine tb371fc,ufs-enabled=off` because UFS core in-flight
migration is not implemented. They do not establish migration of the full new
board. For the historical recorded binary, which lacks that property, explicitly
use `--machine tb371fc`. The separate Linux boot receipt remains evidence for
the Linux version and machine configuration it records.

## Idle FIFO watermark and repeated TTY writes

Linux `qcom_geni_serial_start_tx_fifo()` enables the TX watermark interrupt
before issuing a fresh TX command when the hardware FIFO is empty. The model
originally conditioned that watermark on an already active command. This
allowed polled console output and a first interrupt transfer but could strand
later unbuffered TTY writes. The watermark condition now depends on the FIFO
level and programmed threshold even when no command is active; clock/reset
availability continues to gate the external IRQ.

`idle_watermark_restarts_tty_transfers` repeats the Linux sequence three times,
checking the idle interrupt before START_TX, W1C reassertion, exact chardev
bytes, completion, and the stop/restart boundary. It reproduced the missing
idle watermark on frozen binary
`a70862bfa8aa957ea26e362b5defd9ae60b4cc4bb53f2ac2c70c0b4fe3b67e2a`.
The existing historical console receipts do not establish this repeated TTY
write behavior. New-binary validation is recorded separately.

The corrected binary
`781d6ae66e9918bf1e3eed08c4bfaa3cdb6381cb1d63495d6ef5cc1a97dabb39`
passed GENI 14/14, GENI process migration 6/6, clock/reset 6/6, and paused-clock
migration 4/4. GCC 19/19 and GCC migration 5/5 also passed on that exact binary.
The receipts and the two corrected historical test assumptions are preserved
in [`revalidation-4`](../evidence/expansion-20261123/revalidation-4/summary.json).
The clock-off IRQ-negative checks remained intact and passed.

A real Linux 6.12 UFS/SMMU acceptance guest then emitted all 19 checks through
the normal TTY path, with unbuffered stdout, and its disk payload was independently
verified on the host. See [`geni-linux-repeated-tty-tx.json`](../evidence/expansion-20261123/geni-linux-repeated-tty-tx.json).
This establishes repeated Linux TTY TX progress across the previously failing
idle boundary; it does not establish Linux TTY RX or baud/electrical fidelity.
