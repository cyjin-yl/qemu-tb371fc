/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Freestanding Linux/AArch64 EL0 architectural-counter probe.
 * No libc, file opens, MMIO, MSR, privileged instructions or device writes.
 * Only CNTFRQ_EL0/CNTVCT_EL0 reads, a bounded integer loop, stdout and exit.
 * The interval is not a benchmark: scheduling/interrupts may extend it.
 */

    .section .text
    .global _start
    .type _start, %function

    .macro literal name
    adr x1, \name
    mov x2, \name\()_end - \name
    bl write_all
    .endm

_start:
    mrs x19, cntfrq_el0
    isb
    mrs x20, cntvct_el0
    mov x23, #0
    mov x22, #65536
1:
    add x23, x23, #1
    subs x22, x22, #1
    b.ne 1b
    isb
    mrs x21, cntvct_el0
    sub x22, x21, x20
    cmp x21, x20
    cset x24, hi

    literal prefix
    mov x0, x19
    bl write_u64
    literal start_label
    mov x0, x20
    bl write_u64
    literal end_label
    mov x0, x21
    bl write_u64
    literal delta_label
    mov x0, x22
    bl write_u64
    literal increased_label
    cbz x24, 2f
    literal true_label
    b 3f
2:
    literal false_label
3:
    literal suffix
    cbz x19, 4f
    cmp x24, #1
    cset x0, ne
    b exit_process
4:
    mov x0, #1
    b exit_process
    .size _start, . - _start

/* x1=buffer, x2=length. Retry EINTR and partial writes; fail with exit 2. */
    .type write_all, %function
write_all:
    mov x0, #1
    mov x8, #64
    svc #0
    cmp x0, #0
    b.gt 5f
    cmn x0, #4
    b.eq write_all
    mov x0, #2
    b exit_process
5:
    add x1, x1, x0
    sub x2, x2, x0
    cbnz x2, write_all
    ret
    .size write_all, . - write_all

/* x0=unsigned integer. Buffer is on the private process stack. */
    .type write_u64, %function
write_u64:
    stp x29, x30, [sp, #-48]!
    mov x29, sp
    add x1, sp, #48
    mov x2, #0
    mov x9, #10
6:
    udiv x10, x0, x9
    msub x11, x10, x9, x0
    add x11, x11, #48
    sub x1, x1, #1
    strb w11, [x1]
    add x2, x2, #1
    mov x0, x10
    cbnz x0, 6b
    bl write_all
    ldp x29, x30, [sp], #48
    ret
    .size write_u64, . - write_u64

exit_process:
    mov x8, #93
    svc #0
    /* exit cannot return; trap rather than execute the data below. */
    brk #0

    .section .rodata
prefix:
    .ascii "{\"schema_version\":1,\"kind\":\"tb371fc-el0-counter-probe\",\"counter_frequency_hz\":"
prefix_end:
start_label:
    .ascii ",\"counter_start\":"
start_label_end:
end_label:
    .ascii ",\"counter_end\":"
end_label_end:
delta_label:
    .ascii ",\"counter_delta\":"
delta_label_end:
increased_label:
    .ascii ",\"increased\":"
increased_label_end:
true_label:
    .ascii "true"
true_label_end:
false_label:
    .ascii "false"
false_label_end:
suffix:
    .ascii ",\"loop_iterations\":65536,\"scope\":\"EL0 counter access only; not a performance benchmark\"}\n"
suffix_end:
    .section .note.GNU-stack,"",%progbits
