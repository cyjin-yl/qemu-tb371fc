# Console-independent guest acceptance

The supplied, unmodified TB371FC production kernel passed the eight PID1 checks
on the integrated A55/A77 machine with exact counter conversion on 2026-11-23. This run uses the
machine-generated functional-subset device tree and our validation initramfs.
It is **not a stock Android boot and not a full real-device-tree boot**.

The production kernel does not provide an enabled serial console, so its
serial log is empty. The new observer instead reads PID1's completed result
from ordinary guest RAM at an EL0 breakpoint, removes the breakpoint, and
requires the guest to finish its own poweroff. An exit code alone cannot pass.

## Measured integrated run

The complete receipt is
[production-gdb-exact-20261123/result.json](../evidence/production-gdb-exact-20261123/result.json).
The raw probe stdout, 3,120-byte result, GDB transcript and empty serial log are
retained alongside it, with a SHA-256 manifest.

| Observation | Measured value |
| --- | --- |
| QEMU SHA-256 | `a70862bfa8aa957ea26e362b5defd9ae60b4cc4bb53f2ac2c70c0b4fe3b67e2a` |
| Supplied Image SHA-256 | `ef9e7b5cdb9ef3c66977a04d6b6e21cf83e83f2f45476390c861827d1bb1cf0f` |
| Kernel release | `4.19.157-perf+` |
| Online CPUs reported by Linux | `0-7` |
| Actual QMP MIDRs | Four `0x412fd050`, four `0x411fd0d0` |
| RAM / CPU configuration | 8 GiB / eight CPUs |
| Completed / passed / failed masks | `0xff / 0xff / 0` |
| Measured nanosleep interval | 22,502,344 ns |
| Checkpoint | `0x08000000`, EL0 |
| Native-probe ELF SHA-256 | `0ed51cb6c6dd24685beb3f77056d17d47c5c2f697df96d87eef4a678d4c5cb1b` |
| Probe's CNTFRQ read | 19,200,000 Hz |
| Shutdown | QMP `SHUTDOWN`, `guest=true`, `reason=guest-shutdown` |
| Process exit / duration | 0 / 4.841 s |
| Serial log | 0 bytes |

CNTFRQ is a software-visible frequency annotation. The unchanged native
probe checks that value and counter progress, not rate against a separately
calibrated time source. Independent [MMIO and CPU rate gates](mmio-timer.md)
verify the exact-mode conversion: one virtual second produces 19,200,000
ticks, and a real instruction-counted ARM loop matches the independent
time reference.

The earlier [GCC integration receipt](../evidence/production-gdb-gcc-20261123/result.json)
remains available. That older binary used QEMU's 52 ns integer period,
approximately 19,230,769.23 ticks/s or 1602.564 ppm above the annotation.
Its successful PID1 result did not establish exact timer rate.

## What the guest actually checks

The original eight checks remain: mounting procfs, mounting sysfs, AArch64
uname, eight online CPUs, monotonic timer/scheduler progress, fork/wait,
TB371FC device-tree model, and execution of the exact native-tested
`/arch-probe` ELF. The last check records the original stdout bytes through
a pipe and also prints them unchanged when a console exists. The parent waits
for the real child status; truncated, missing or invalid output fails.

The guest result ABI lives in [guest-result.h](../tests/guest-result.h).
It records completion/pass/fail masks, actual uname strings, online-CPU
contents, the device-tree model, monotonic timestamps, probe wait status,
byte count and raw stdout. `guest-init.c` marks the record complete only
after all checks and then calls a no-inline, no-side-effect checkpoint.

The standalone observer links that checkpoint into a fixed user virtual
address and discovers both symbols in the actual static ELF. It verifies:

1. QMP exposes eight CPUs and records their MIDRs.
2. The CPU stops at the intended checkpoint with EL0 PSTATE.
3. The complete ABI record exists, with all eight checks completed and passed.
4. Kernel-reported architecture, CPU list, timing and model match the contract.
5. The exact raw probe JSON has the native reference frequency, a consistent
   positive counter delta and the expected loop count.
6. After removing the breakpoint and resuming, QMP reports a real
   guest-requested shutdown and QEMU exits successfully.

The GDB observer issues register/memory reads, breakpoint requests and
continue requests. It does not write guest RAM, rewrite a register, patch
the vendor kernel, or inject a pass value. The checkpoint is part of the
validation PID1, not a fictional hardware device.

## Reproduce

Provide the private production Image and the same native-tested probe ELF.
Run in the development container:

```sh
python3 /src/tb371fc/tests/test_guest_result.py
python3 /src/tb371fc/scripts/boot_smoke_gdb.py \
  --qemu /work/integration-20261123-3/qemu-system-aarch64 \
  --kernel /work/device-assets/current-boot_a-kernel.Image \
  --arch-probe /work/arch-probe/arch-probe \
  --out /work/gdb-smoke/a-new-output-directory
```

The runner refuses to overwrite an existing result. `--dtb` can supply
a separate DTB; the receipt states which DT source was used and hashes it.
A full real-device-tree failure is preserved as a failure and is not
equivalent to the generated-DT success above.

## Boundaries

This gate demonstrates that the supplied production kernel executes the
validation initramfs with working SMP, timers, process creation and guest
poweroff under the modeled boot contract. It does not demonstrate original
Android init, verified boot, vendor partitions, secure firmware, GPU/display,
audio, touch, camera or radio functionality. With an empty console it also
does not prove a serial-console handoff; the clean-kernel serial gate remains
the separate evidence for that path.
