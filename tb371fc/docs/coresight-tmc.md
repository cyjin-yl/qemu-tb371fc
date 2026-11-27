# TB371FC CoreSight TMC APB reference

The original Android diagnostic at source 7954e5e reached
`tmc_probe+0xc8` and faulted on the previously unmapped 32-bit read at
`0x06b05fc8`. This is the ETF's DEVID register. The preserved device tree
has two `arm,primecell` nodes with `arm,primecell-periphid = <0xbb961>`:

| Node | Base | Size | DT name | Model profile |
| --- | --- | --- | --- | --- |
| ETF | 0x06b05000 | 0x1000 | coresight-tmc-etf | 32-bit ATB, 64-bit memory, 4 KiB RAM |
| ETR | 0x06048000 | 0x1000 | coresight-tmc-etr | invasive debug authentication disabled |

The DT's ETR also describes a BAM window at 0x06064000. This change does not map
that window or claim a BAM, AXI DMA, scatter/gather or trace-source implementation.
It does not change the kernel, ramdisk, DTB, boot arguments or native launcher.

## Reference contract

The original DT overrides the AMBA identity with 0x000bb961 (revision r0p0).
The primary register/state reference is
[Arm CoreSight TMC r0p1 TRM, DDI 0461B](https://documentation-service.arm.com/static/5f1060dd0daa596235e7ab7a).
The relevant software consumer is the vendor
[coresight-tmc.c](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/coresight/coresight-tmc.c),
particularly the AMBA probe, type/memory-width decoding, ETR authentication check
and manual flush/ready polling.

The PID/CID, ETF/ETR DEVTYPE, lock registers and claim tags follow the part-0x961
APB contract. ETF memory width is twice its ATB width. Its 4 KiB RAM size and the
one-microsecond empty-input flush delay are explicit digital reference choices;
they have not been measured on the tablet.

ETF RWD writes real RAM only in Disabled plus integration mode. Disabled RRD
reads that RAM independently of MODE. RRP/RWP software writes align to 16 bytes;
two 32-bit data accesses complete one 64-bit memory beat before the visible
byte pointer advances by eight bytes and wraps. Writes commit the complete beat
atomically, and reads latch both halves of one beat. A write-pointer wrap sets
Full even for an APB RAM test; the next capture enable clears that latch. Active capture receives no ATB
input, remains empty and never reports fabricated trace or advances a pointer.
Empty, locked and hardware-FIFO reads return 0xffffffff without consuming RAM.

A software lock ignores implemented register writes except LAR. CLAIMSET
reports the supported four tag bits and ORs writes into them; CLAIMCLR reads
current tags and clears the written bits. Manual flush is ignored while
Disabled. The formatter-enable and trigger-insertion bits change only in
Disabled state. Circular-buffer ATB DrainBuffer is explicitly unsupported and rejected. In capture, FLUSHMAN and FFSR stay pending until the model timer
completes. StopOnFlush enters Stopped while CTL remains one; a zero-to-one CTL
transition rearms capture. Circular mode retains the configured write pointer;
FIFO modes reset it. With no input the reference read pointer starts at the
same byte address. Disable and system reset cancel pending work. TMCReady's
low-to-high edge clears the bounded trigger counter.
With no captured input, STS is 0x0c in Disabled, 0x10 in Running and 0x1c in
Stopped (plus a retained Full bit where applicable). FtEmpty is distinct from
Empty; FFSR mirrors FtEmpty in bit 1 and adds FlushInProgress in bit 0.
PSCR resets to 0x0a. The model deliberately zeros the reference RAM on system reset; silicon does
not promise these contents.

ETF AUTHSTATUS is zero because its authentication is controlled elsewhere.
The disabled ETR identity uses a legal 32-bit memory data path and four-beat
write-buffer configuration. ETR AUTHSTATUS is 0x22: nonsecure and secure invasive debug are implemented but
not authorized, corresponding to DBGEN and SPIDEN low. The vendor ETR probe
therefore fails its real authentication check. This is a supported disabled
component, not a successful ETR trace session. Its AXI memory interfaces and
BAM window remain unimplemented, and no r0p1 reserved NOSCAT bit is repurposed.

Only explicitly implemented aligned 32-bit registers are decoded. Wrong-size,
misaligned, reserved-hole and unsupported operations return a bus error, with a
diagnostic. Read-only identification does not become writable shadow storage.
The migration stream preserves configuration identity, actual ETF RAM, both
partial memory-beat lanes, lock, claims and pending flush timer; invalid state
combinations are rejected on load.

## Verification

`tb371fc/tests/test_coresight_tmc.py` drives the real QEMU binary through
QMP/QTest. Its sixteen required checks cover original-DT AMBA identification,
the actual probe read sequence, lock/claim isolation, real RAM values,
lane/pointer alignment and wrap, positive and rejected accesses, all empty
capture modes, timed flush/stop/rearm, reset cancellation, disabled ETR/no DMA,
and two ordinary process checkpoints. One checkpoint preserves two independent
partially consumed memory beats; the other preserves a locked, pending flush.
QTest clocks are aligned before loading the unmodified absolute timer deadline.

Every test process is created by the fixture, quit via QMP, actually waited and
reaped, and its output channels are drained. Forced cleanup fails acceptance.
The test records the tested binary SHA-256 and checks it again at completion.

The public workflow requires this test as a separate step before the existing
component and Linux gates. The existing 111 component plus five Linux gate
definitions are unchanged. A successful test establishes this bounded digital
APB contract; it does not establish original Android userspace, physical trace
capture, ETR DMA or complete-device equivalence.
