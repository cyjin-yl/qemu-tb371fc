# Qualcomm KPSS watchdog

The board maps `qcom-kpss-watchdog` at `0x17c10000 + 0x1000`.
Bark is GIC SPI 0 and non-secure bite is SPI 1. These addresses and routes
come from the captured TB371FC node `/soc/qcom,wdt@17c10000`; the node has no
clock-rate property.

## Evidence and frequency

The default device property `frequency=32765` is a **software reference**.
The production kernel Image initializes its `WDT_HZ` variable to 32765;
this agrees with the Qualcomm driver published in Android's kernel tree.
It is not a physical oscillator measurement or a read of the live variable
after module-parameter parsing. The initialized value, Image hash, symbol
offset and source hashes are recorded in
[watchdog-sources.json](watchdog-sources.json).

Primary source:
[Qualcomm watchdog_v2.c](https://android.googlesource.com/kernel/msm/+/7b459140379bdbbd665998db1fab170ebfb269e7/drivers/soc/qcom/watchdog_v2.c).
The independently built Linux 6.12.114
[`qcom-wdt.c`](https://git.kernel.org/pub/scm/linux/kernel/git/stable/linux.git/tree/drivers/watchdog/qcom-wdt.c?h=v6.12.114)
provides the KPSS layout and pretimeout driver contract. A community Lenovo
kernel candidate is recorded as corroboration; it is not asserted to be the
exact source of the installed production binary.

The native DT uses an 11,000 ms bark interval and a 9,360 ms pet interval.
The generated test DT instead exposes the upstream `qcom,kpss-wdt` binding
and an explicit fixed clock matching the QOM property.

## Implemented digital interface

| Offset | Interface |
| --- | --- |
| `0x04` | Pet/reset command, bit 0; read returns zero. |
| `0x08` | Enable bit 0 and wakeup-output enable bit 1. |
| `0x0c` | Elapsed 20-bit counter in bits 20:1, read-only. |
| `0x10` | 20-bit bark compare. |
| `0x14` | 20-bit bite compare. |

Only aligned 32-bit device accesses are accepted. Other offsets return a
memory-transaction error. The elapsed count uses floor division at the selected
frequency, and deadlines use ceil division, so a compare cannot fire before its
first complete tick. Disable preserves the accumulated sub-tick phase;
pet clears the phase and both pending events. Bark and bite can occur in either
order. The visible counter field wraps at 20 bits; each compare latches once
per pet.

Bark and bite have independent outputs. Bite also calls QEMU's configured
watchdog action; `-watchdog-action none` is useful for inspecting a latched
event, and the normal `reset` action performs a real machine reset. QEMU
reports a `WATCHDOG` event followed by `RESET` with
`guest=true, reason=guest-reset`. The latter reason comes from QEMU's core
watchdog implementation, not a special Qualcomm firmware result.

The separate named `wakeup` output is a digital reference interface. It is not
yet connected to a complete PDC/PMIC low-power model. The firmware handoff reset
state is disabled with zero compares; silicon POR values have not been read.
Other STATUS fields, including reset-cause retention, are not modeled.
Secure watchdog firmware, secure dump handling and oscillator/power-domain
behavior remain outside this component.

State migration includes the enable/compare fields, counter phase, latched
interrupts and pending timer. Different configured frequencies are rejected by
the VMState contract. This does not make the UFS-containing board migratable.

## Executed acceptance

The frozen build with SHA-256
`99dfb4fc3ff472624f0a079c67ba26b99e60935a4afc859d1e66afc8942e8c4c`
passed **16 QTest groups**, including the first-nanosecond boundary, pet,
clock-disable phase, both compare orders, actual machine reset and invalid
frequency rejection. A separate real QMP transfer preserved asserted bark,
a pending bite deadline, nonzero start epoch and post-migration pet.

See [watchdog receipts](../evidence/watchdog-20261123/).
The initial reset test expected the wrong QMP reset-reason string; that failed
receipt is retained. The correction follows `hw/watchdog/watchdog.c` and also
requires event order and cleared machine registers; the device C did not change.

With the untouched production Image and the full captured DT, the same binary
logged **MSM Watchdog Initialized** and advanced to a fault in
`msm_restart_probe+0xec`. That is a diagnostic progression, not a successful
full-DT or Android boot. Only selected outcomes and hashes are published in
[native-DT progression](../evidence/native-dt-progression-20261123/progress.json);
the private DT, production Image and raw kernel log are not distributed.

```sh
python3 tb371fc/tests/test_watchdog_kpss.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/watchdog-test
python3 tb371fc/tests/test_watchdog_kpss.py \
  --qemu /work/build/qemu-system-aarch64 --out /work/watchdog-migration \
  --migration-only
```
