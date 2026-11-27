# 32-bit CoreSight STM with authentication disabled

This reference device addresses the first fatal access observed in native006:
a 32-bit read at `0x06002ea4`, in the original kernel's
`stm_probe+0x1c0/0x3b0`. The failing instruction reads `STMSPFEAT2R`.
The preceding replicator fault no longer appeared after PR54.

Native006 used the original kernel, ramdisk, full DTB and boot arguments.
It ended with a kernel failure, with no Android userspace observed. Its
artifact was consumed once. Those facts do not establish a successful boot.

## Original DT and reference identity

The retained DTB has SHA-256
`5114262a0dea5ef82b53db69b3d489ddbe57040e1a50b57365e87a28d2946207`.
Node `/soc/stm@6002000` has compatible `arm,primecell` and AMBA override
`0x000bb962`. “STM32” in the vendor's ID table names the **32-bit CoreSight
STM**, not an STMicroelectronics STM32 microcontroller.

| DT resource | Address | Size | This model |
| --- | --- | --- | --- |
| stm-base | 0x06002000 | 0x1000 | APB control |
| stm-stimulus-base | 0x16280000 | 0x180000 | Fixed authentication-denied stimulus accesses |
| stm-debug-status | 0x007820f0 | 4 | Not added by this change; the observed probe does not access it |

Reference IDs and feature words follow Arm DDI 0444A r0p0:
PID `0x000bb962`, CID `0xb105900d`, DEVTYPE `0x63`,
DEVID `0x10000`, and SPFEAT1/2/3
`0x006587d1 / 0x000104f2 / 0x0000007f`.
The data size is 32 bits; the architectural capability is 65,536 stimulus ports.
The original DT exposes a 1.5 MiB window, not the complete 16 MiB architectural
address space. The model does not infer the chip's channel/master routing from
that window.

The fixed vendor driver reads the feature words but selects 32 channels
unconditionally. The retained Image confirms that behavior:
`stm_probe` at `0xffffff8008d26290`, file offset `0xca6290`, is 944 bytes
with SHA-256
`b81aabfca039458f5b8ff8b54afbdde314bb741b57456997f202018584b321d0`.
At +0x1c0 it reads SPFEAT2 and tests bits 15:12 for a 4/8-byte choice;
at +0x1fc it reads DEVID, then stores the constant 32 at +0x200.
The model retains the reference hardware DEVID rather than replacing it with
the driver's channel limit. Reference revision and authentication values are
implementation choices, not measurements of the tablet.

## Authentication and observable behavior

DBGEN, NIDEN, SPIDEN and SPNIDEN are all fixed low. AUTHSTATUS is consequently
`0xaa`. IHI 0054A section 2.6 requires no trace generation when non-invasive
debug is disabled; DDI 0444A section 2.7 specifies successful, discarded
stimulus writes and zero-valued reads.

Legal APB configuration and timestamp requests remain accessible. They cannot
enable trace in this fixed profile. The stimulus window accepts natural byte,
halfword and word accesses independently of the APB software lock. A wider
QEMU bus transaction is split into at most 32-bit accesses; it does not add
64-bit STM packet support. Tests do not claim defined hardware behavior for
architecturally unpredictable unaligned stimulus transactions.

There is no trace payload storage, ATB output, hardware event producer,
timestamp engine, DMA request line, interrupt or packet-completion claim.
BUSY and trigger status remain zero. Registration or a driver's enable message
must not be reported as successful tracing.

## Control state

Controls are explicit fields with masks, access types and ordinary migration
state, not a writable catch-all register array.

| Register | Writable mask or behavior |
| --- | --- |
| HEER, HETER, SPER, SPTER, TSFREQR | 32-bit state |
| HEMCR | 0x97; clear command bit 6 is not retained; status bit 5 reads zero |
| SPSCR | 0xfff00003; reject reserved PORTCTL=2 and active selection with PORTSEL=0 |
| SPMSCR, SPMOVERRIDER | 0x007f8001; reject active selection with MASTSEL=0 |
| SPOVERRIDER | 0xffff8007; reject OVERCTL=3 and active port selection with PORTSEL=0 |
| SPTRIGCSR | 0x19; clear command bit 2 is not retained; status bit 1 reads zero |
| TCSR | 0x007f0023; SYNCEN reads one, BUSY reads zero |
| SYNCR | 0x1fff; MODE=1 accepts N=12..27 in COUNT[11:7] |
| AUXCR | 0x1f |
| PRIVMASKR, HEBSR | Read zero, ignore writes |
| CLAIMSET / CLAIMCLR | Four-bit W1S / W1C, read mask / current claims |
| LAR / LSR | Key 0xc5acce55 unlocks; other keys lock; LSR is 1 or 3 |

OVERTS is independent of the port/master override selection, so OVERTS alone
is legal. Trigger-clear requests in multi-shot mode are rejected. TRACEID
changes require the previous TCSR.EN value to be zero; disabling and changing
TRACEID are separate writes. Undefined selector combinations are rejected
before any state changes. The same constraints are checked during migration.

The documented reset choice for architecturally UNKNOWN or implementation
defined mutable values is zero. TCSR reads 4 after reset and the software lock
is closed. No read-only status or write-only command is migrated as shadow
storage. PRIVMASK is absent in this reference feature word; the 32 hardware
events need no bank selector.

### Explicit implementation limits

The DMA class ID reads 2, DMA status/control remain idle, and zero DMA
operations are accepted. Nonzero DMA requests or configuration are rejected.
Integration control/status read zero, zero output writes are no-ops, and
nonzero integration requests are rejected. These interfaces are not reported
as operational.

Reserved holes, write-only reads, read-only writes when unlocked, and APB
accesses other than aligned 32-bit transactions report a memory access error.
Locked supported control writes, except LAR, leave state unchanged. Unsupported
accesses are diagnosed without fabricating completion or register state.

## Verification

`tb371fc/tests/test_coresight_stm.py` uses the real QTest AddressSpace path and
QMP protocol. Its twelve required scenarios cover:

- Reference IDs, exact original-probe reads, reset and the software lock.
- Writable masks, claims, legal independent timestamp override, invalid
  selections, trigger clears and active TRACEID changes.
- Vendor enable/disable sequences with authentication still denied.
- Natural stimulus accesses at both ends of the DT window, real flat-map
  boundaries, and unassigned reads immediately outside it.
- Forty-one rejected control accesses/combinations, plus two distinct
  unassigned boundary reads, with unchanged state.
- System reset and one ordinary, unmodified migration between two processes,
  followed by control and stimulus continuation.

Every created process must be quit, actually waited and reaped; protocol and
stdio receipts retain the evidence. No guest CPU, QOM setter, modified
migration payload or original Android launch is used by this component test.
CI retains the result, flat map and protocol/cleanup logs. The raw migration
stream is not uploaded; its hash is checked by the test before and after load.

The new test is additive. Existing TMC, replicator, 111 component, five Linux,
both Linux UFS migration, CPU and shared PCI gates remain required. Native
qualification must bind all receipts to the same new artifact and tested head
before a subsequent original-input diagnostic is eligible.

## Primary references

- [Arm CoreSight System Trace Macrocell TRM, DDI 0444A r0p0](https://documentation-service.arm.com/static/5f10692b0daa596235e7f8cb).
- [Arm System Trace Macrocell Programmers' Model Architecture, IHI 0054A](https://documentation-service.arm.com/static/5f8ffb02f86e16515cdbfec0?token=).
- [Fixed vendor STM driver](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/coresight/coresight-stm.c),
  Git blob `4e8fd28a0ab42710da3f0fbfc14ebeeca1ecd295`.
- [Fixed vendor STM framework](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/stm/core.c)
  and [OST setup](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8250/blob/98719830b0de5584f5588bbe9f4c8304d97a64c6/drivers/hwtracing/coresight/coresight-ost.c).
