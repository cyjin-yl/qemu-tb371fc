# Explicit AOP SCS reference continuation

Status: draft source and public synthetic tests; this change has not yet been
compiled or executed. No additional private execution has been performed.

The separate profile
`local-alias-dtcm32k-scs-reference`
adds a limited architectural SCS/NVIC interface to the existing Cortex-M3
reference. The three original profiles and the original fourteen-case runner
remain unchanged. This is a diagnostic configuration, not a measured Kona AOP
memory map, CPU identity, reset configuration, or successful firmware boot.

## Evidence for the next interface

The previously completed private diagnostic stopped before a read at
0xe000e004, with PC 0x1f30 in the explicit local-alias/32-KiB-DTCM reference.
The address is ICTR in Arm's Cortex-M3 TRM, section 6.2. It describes the
implemented external interrupt capacity in groups of 32.

The reference already instantiates QEMU's real ARMv7M and NVIC. The original
profiles deliberately overlay their whole PPB with an error region. Removing
that whole overlay would expose QEMU's generic privileged RAZ/WI fallback for
unimplemented PPB and some unimplemented SCS offsets, so the continuation keeps
the overlay and adds only the exact windows below.

The pinned public SDM670 REX source initializes ICTR-derived interrupt banks,
AIRCR grouping, priorities, VTOR, CCR, and system-handler controls. It supports
the choice of a useful architectural subset; it does not prove the captured
Kona firmware's CPU, interrupt count, clock, register values, or entire boot
path. Its target's 128 IRQ setting is not applied to this profile.

## Reference configuration

The CPU remains QEMU Cortex-M3. External IRQ count is explicitly 32, implemented
priority width is explicitly 8 bits, and the supplied CPU clock remains 1 MHz.
All three are reference settings rather than tablet measurements. Existing
ELF-derived physical ranges and their actual shared low aliases remain the
same as the previous explicit 32-KiB-DTCM profile.

The SCS values are supplied by QEMU's realized NVIC, not by constants in this
board. In particular, ICTR is derived by that NVIC from its actual configured
IRQ count; NVIC enable, pending, priority, VTOR and keyed AIRCR accesses change
and read the same architectural state used by the CPU.

| Address / extent | Interface | Access contract |
| --- | --- | --- |
| 0xe000e004 / 4 B | ICTR | aligned word reads |
| 0xe000e100 / 4 B | NVIC ISER0 | aligned word reads/writes |
| 0xe000e180 / 4 B | NVIC ICER0 | aligned word reads/writes |
| 0xe000e200 / 4 B | NVIC ISPR0 | aligned word reads/writes |
| 0xe000e280 / 4 B | NVIC ICPR0 | aligned word reads/writes |
| 0xe000e300 / 4 B | NVIC IABR0 | aligned word reads |
| 0xe000e400 / 32 B | IRQ0..31 priority bytes | naturally aligned byte/halfword/word |
| 0xe000ed00 / 4 B | CPUID | aligned word reads |
| 0xe000ed04 / 4 B | ICSR | aligned word reads/writes |
| 0xe000ed08 / 4 B | VTOR | aligned word reads/writes |
| 0xe000ed0c / 4 B | AIRCR | aligned word reads/writes; debug-only bits rejected |
| 0xe000ed14 / 4 B | CCR | aligned word reads/writes |
| 0xe000ed18 / 12 B | SHPR1..3 | naturally aligned byte/halfword/word |
| 0xe000ed24 / 4 B | SHCSR | aligned word reads/writes |

The set/clear/pending banks cover only the configured 32 external IRQs; another
bank is not implicitly accepted. SysTick, MPU, ACTLR, AFSR, trace/debug units,
other PPB and every SoC peripheral remain guarded. The architecture's reserved
bits inside supported registers follow the real NVIC implementation; this
does not turn unlisted registers into successful zero reads.

## Dispatch and rejection

Each window is a child of the existing PPB error region. A validated
transaction calls memory_region_dispatch_read/write directly on the original
realized NVIC sysreg MemoryRegion. The forwarding target is not the ARMv7M
address-space container or the guarded overlay, preventing recursive dispatch.
CPU transaction attributes are passed through without changing privilege.

The wrapper preserves the width of each transaction it actually receives,
checks natural alignment and that this transaction fits one window, and permits
subword access only for the priority arrays. Address-space and debugger memory
operations can split a wider request at a window boundary: for example, an
eight-byte read starting at ICTR can first dispatch its legal four-byte word
and then fail on the guarded following word. Earlier legal bus beats can commit;
there is no whole-request atomicity or rollback claim. The tests record and
check the actual successful and failed dispatch counts for each request.
Incorrect-width/alignment/direction and AIRCR debug-only UNPREDICTABLE operations
return MEMTX_ERROR; they are not synthesized successes.
A wrong AIRCR VECTKEY is forwarded and ignored by the actual NVIC, as required
for normal software accesses.

The read-only host properties scs-reference-windows and scs-reference-state
expose the exact map, reference parameters, and successful/failed forwarding
counts. State observation requires a stopped vCPU and the BQL. These counters
also include direct debugger accesses, so tests take explicit before/after
snapshots rather than confusing host setup with guest CPU operations.

## Public synthetic gate

The new test_aop_scs_reference.py imports the unchanged original entry runner
only after checking its exact SHA256
18896b8b53720df6809441624b68342245bb7e09bfab8eee26b161dbb3ab613b.
It never reads firmware. The planned gate has eighteen checks:

1. Exact new geometry, CPU, window map and actual debugger stepping flags.
2. All three original profiles still stop ICTR before the bus callback.
3. Real ICTR capacity and Cortex-M3 CPUID reads.
4. NVIC write-one-to-set enable state.
5. NVIC write-one-to-clear disable state.
6. Pending while disabled, read aliases and selective clear.
7. Byte, halfword and word priority lanes.
8. VTOR state and alignment masking.
9. Valid/invalid AIRCR keys and priority grouping.
10. CCR configuration state.
11. System-handler priorities and fault-enable state.
12. ICSR PendSV set/clear with normal debugger IRQ suppression retained.
13. Unlisted PPB/SoC addresses stop before any successful dispatch.
14. Read-only window writes stop before dispatch.
15. STRD across the last priority word retains only its earlier legal beat.
16. Invalid-width/alignment/cross-window debugger accesses return errors.
17. AIRCR debug-only UNPREDICTABLE operations return errors.
18. Actual unprivileged CPU access reaches the NVIC's fault path, without a
    fake loaded value; only the expected architectural stack frame may change.

The gate also checks source and QEMU hashes before/after. New gate success is
not presumed. It must run on the new frozen binary together with the unchanged
XML11 and original14 gates; all original failures and prior proof artifacts
must remain intact.

Example after the draft is built and reviewed:

    python3 -E -B tb371fc/tests/test_aop_scs_reference.py \
      --qemu /absolute/path/to/frozen/qemu-system-aarch64 \
      --out /absolute/path/to/new-scs-result

The outer executor must give this process its own process group and a hard
time limit, keep all logs/socket directories, and verify no residual live
children. The runner retains temporary sockets and uses the original cleanup
implementation; it does not delete files or start any firmware worker.

## Limits of this diagnostic

QEMU's GDB single-step defaults include NOIRQ and NOTIMER. The new runner
queries the actual supported bits and current mask and keeps these defaults.
NVIC state transitions therefore do not claim an IRQ handler was delivered
during those tests. The unprivileged-access case intentionally exercises a
synchronous architectural fault; it is not proof that every internal exception
vector fetch or stacking access is caught before access.

Ordinary forbidden TCG accesses retain the reviewed RSP range-base plus QOM
hit-address correlation. Earlier legal STRD/STM bus beats are not rolled back.
Debugger malformed-access rejection is a separate bus-error observation, not
an ordinary CPU before-access proof. The private worker must still stop at the
first unknown access, architectural exception, blocking instruction, or bound;
new private execution requires a separately reviewed bounded supervisor and
actual success receipts for this variant.

No AOP heap hooks, firmware patches, peer messages, mailbox acknowledgements or
fabricated SoC responses are introduced. Full firmware start, real reset-map
verification, actual AOP core identity and complete tablet equivalence remain
unproven.

## Primary sources

- Arm Cortex-M3 TRM r2p1, 100165_0201_02, sections 3.9 and 6.2:
  https://documentation-service.arm.com/static/5f2286f2f3ce30357bc28b2a
- Arm Cortex-M3 Devices Generic User Guide, DUI0552A, sections 4.2 and 4.3
  (NVIC state, register widths, AIRCR key and debug-only controls):
  https://documentation-service.arm.com/static/5ea823e69931941038df1af5
- Fixed QEMU implementation:
  https://github.com/cyjin-yl/qemu-tb371fc/blob/fe89e5a553fe038babe07abb279a0975d4306715/hw/intc/armv7m_nvic.c
  https://github.com/cyjin-yl/qemu-tb371fc/blob/fe89e5a553fe038babe07abb279a0975d4306715/hw/arm/armv7m.c
  https://github.com/cyjin-yl/qemu-tb371fc/blob/fe89e5a553fe038babe07abb279a0975d4306715/gdbstub/gdbstub.c
- Earlier public target software, used as software-format evidence only:
  https://github.com/Rivko/android-firmware-qti-sdm670/blob/20bb8ae36c93fc16bbadda0e0a83f930c0c8a271/aop_proc/core/kernel/rex/arch/cortex-m3/rexarm.c
  https://github.com/Rivko/android-firmware-qti-sdm670/blob/20bb8ae36c93fc16bbadda0e0a83f930c0c8a271/aop_proc/core/kernel/rex/inc/arch/cortex-m3.h

Exact file hashes are listed in aop-scs-reference-sources.json. No proprietary
source text or captured firmware bytes are included in this implementation.
