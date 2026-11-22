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
| Control/config registers | Known initialization registers retain values, but do not implement parity, packing permutations, modem flow control, loopback, or clock/power gating |
| Firmware identity | UART protocol identity only; no claim about a ROM/firmware revision |

Only 8-bit characters packed four per 32-bit FIFO word are implemented. Changing
packing or character format does not create the corresponding serial behavior.
DMA, GSI, firmware program RAM, hardware register reset fidelity, UART clock
selection, pinmux, physical RTS/CTS, break/parity error generation, and silicon
performance are not implemented. Unsupported accesses are logged with QEMU's
`LOG_UNIMP` category (`-d unimp`); unimplemented reads return zero. These reads
must not be interpreted as valid silicon observations.

The `GENI_FORCE_DEFAULT_REG` is retained as an IO-policy register. It is **not**
used as a fictitious device-wide FIFO reset. Reset is QEMU device reset; the
sequencer cancel/abort semantics are independently modeled.

## Linux contract used

Definitions and FIFO sequencing were checked against upstream Linux **v6.12**:

- [GENI register definitions](https://github.com/torvalds/linux/blob/v6.12/include/linux/soc/qcom/geni-se.h)
- [GENI UART driver](https://github.com/torvalds/linux/blob/v6.12/drivers/tty/serial/qcom_geni_serial.c)
- [GENI wrapper and initialization](https://github.com/torvalds/linux/blob/v6.12/drivers/soc/qcom/qcom-geni-se.c)

These sources describe the software-visible contract. They do not establish
that the model matches undocumented downstream firmware behavior. The Linux
console driver uses FIFO mode; its high-speed UART DMA path is outside scope.

## Executable acceptance

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
scan bounded. Its presence alone does not establish a passing migration result;
record its actual JSON report against the built binary.

Neither suite establishes Android boot, mainline full-driver probe, hardware
timing accuracy, or full tablet emulation; those require their own results.
