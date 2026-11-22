# Native EL0 architectural-counter probe

`arch-probe.S` is a small, static Linux/AArch64 executable with no libc or
dynamic interpreter. It reads **CNTFRQ_EL0** once and **CNTVCT_EL0** before and
after a 65,536-iteration integer loop. Its only system calls are writes to
standard output and process exit. Other writes are to its own stack; it does
not open files, touch MMIO, execute MSR, change drivers or change power policy.
It does not read MIDR_EL1 or require access to privileged system registers.

The architectural counter frequency is not the CPU's core frequency. The
loop's elapsed counter ticks are not a performance benchmark: scheduling,
interrupts, QEMU timekeeping and execution mode can change the interval.
Comparing native and emulated runs should require the expected counter
frequency and forward progress, not an equal tick delta.

## Build in the development container

From the QEMU repository root:

```sh
sh tb371fc/scripts/build_arch_probe.sh /work/arch-probe/arch-probe
aarch64-linux-gnu-readelf -h -l /work/arch-probe/arch-probe
aarch64-linux-gnu-objdump -d /work/arch-probe/arch-probe
```

The build script keeps both the object and ELF. It does not install anything
on a device. `TB371FC_CROSS_PREFIX` can select a different binutils prefix.

The same ELF can run as an ordinary program in an AArch64 Linux initramfs and
in the existing Android Termux application environment. A successful run
prints a single JSON object and exits zero. A zero frequency or non-increasing
counter exits one; a write failure exits two. If a kernel traps EL0 counter
access, retain that failure rather than changing global kernel settings.

## Actual tablet result

The owner tablet ran the ELF as an **unprivileged Termux EL0 process**, without
requiring `su`, at **2026-11-22 22:11:47 UTC** (2026-11-23 06:11:47 in
Asia/Shanghai). It exited zero, reported **19,200,000 Hz**, and observed the
counter advance by **525 ticks**. This agrees with the counter frequency in
the independently captured running DT.

The exact source, ELF hashes, runtime command, timestamps, stdout, stderr,
exit status and checks are in
[`android-arch-probe.json`](../evidence/android-arch-probe.json). The 2,264-byte
ELF was checked as AArch64 ET_EXEC, with no PT_INTERP, an RX load segment and
a non-executable RW stack. Its retained device path is:

```text
~/qemu-tb371fc-probe/arch-probe-0ed51cb6c6dd2468
```

This is a real instruction-execution observation on the running Android
kernel. It does not establish CPU timing equivalence, a new kernel boot,
GPU correctness or peripheral behavior.
