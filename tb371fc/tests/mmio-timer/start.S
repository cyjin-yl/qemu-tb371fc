/* SPDX-License-Identifier: GPL-2.0-or-later */
.arch armv8-a
.section .text.boot, "ax"
.global _start
_start:
    ldr x0, =0x80100000
    mov sp, x0
    /* RAM is Normal memory; MMIO remains Device. MMU-off memory is
     * Device and cannot support the compiler's unaligned C data loads. */
    adr x0, translation_table
    msr ttbr0_el2, x0
    ldr x0, =0x80823519
    msr tcr_el2, x0
    mov x0, #0xff00
    msr mair_el2, x0
    dsb sy
    tlbi alle2
    dsb sy
    isb
    mrs x0, sctlr_el2
    orr x0, x0, #1
    orr x0, x0, #4
    orr x0, x0, #0x1000
    msr sctlr_el2, x0
    isb
    bl probe_main
1:  wfi
    b 1b

/*
 * Exactly 65536 iterations of 16 NOP + SUBS + B.NE. With QEMU icount
 * shift=0 these consume 1,179,648 virtual ns, plus the two bracketing
 * instructions. This is an independent rate reference, not CNTFRQ.
 */
.global probe_rate_delta
.type probe_rate_delta, %function
probe_rate_delta:
    mov x2, #0x10000
    isb
    mrs x0, cntpct_el0
2:
.rept 16
    nop
.endr
    subs x2, x2, #1
    b.ne 2b
    isb
    mrs x1, cntpct_el0
    sub x0, x1, x0
    ret

.balign 4096
translation_table:
    .quad 0x00000401             /* 0..1 GiB: Device, AF, block */
    .quad 0
    .quad 0x80000705             /* 2..3 GiB: Normal WB, ISH, AF, block */
    .fill 509, 8, 0
