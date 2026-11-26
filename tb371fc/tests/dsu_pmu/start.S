/* SPDX-License-Identifier: GPL-2.0-or-later */
/* DSU bare-metal test only. No original firmware, kernel, storage or DT. */
.arch armv8.2-a
.section .text.boot, "ax"
.global _start, secondary_entry
_start:
    mov x0, #0
secondary_entry:
    /* PSCI starts the secondary at EL2 with its context ID in x0. */
    msr tpidr_el0, x0
    mov x20, x0
    ldr x1, =0x80500000
    add x1, x1, x0, lsl #16
    mov sp, x1
    ldr x1, =0x80600000
    add x1, x1, x0, lsl #16
    msr sp_el1, x1
    ldr x1, =0x80700000
    add x1, x1, x0, lsl #16
    msr sp_el0, x1
    adr x1, vectors_el2
    msr vbar_el2, x1
    adr x1, vectors_el1
    msr vbar_el1, x1
    mov x1, #0x80000000
    msr hcr_el2, x1
    msr cptr_el2, xzr
    mov x1, #3
    msr cnthctl_el2, x1
    msr cntkctl_el1, x1
    mov x1, #15
    msr ICC_SRE_EL2, x1
    msr cpacr_el1, xzr
    msr far_el1, xzr
    msr far_el2, xzr
    /* RES1 bits, MMU/caches off; all data/stack accesses are aligned. */
    ldr x1, =0x30d00800
    msr sctlr_el1, x1
    isb
    mov x0, x20
    bl probe_main
1:  yield
    b 1b

.global enter_el1
.type enter_el1, %function
enter_el1:
    stp x29, x30, [sp, #-16]!
    mov x29, sp
    adr x1, el1_entry
    msr elr_el2, x1
    mov x1, #0x3c5
    msr spsr_el2, x1
    eret
el1_entry:
    bl probe_el1
.global el1_return_hvc
el1_return_hvc:
    hvc #0x52
    b .
.global el2_return
el2_return:
    ldp x29, x30, [sp], #16
    ret

.global enter_el0
.type enter_el0, %function
enter_el0:
    stp x29, x30, [sp, #-16]!
    mov x29, sp
    adr x1, el0_entry
    msr elr_el1, x1
    mov x1, #0x3c0
    msr spsr_el1, x1
    eret
el0_entry:
    bl probe_el0
.global el0_return_svc
el0_return_svc:
    svc #0x51
    b .
.global el1_return
el1_return:
    ldp x29, x30, [sp], #16
    ret

.macro save_all
    sub sp, sp, #256
    stp x0, x1, [sp, #0]
    stp x2, x3, [sp, #16]
    stp x4, x5, [sp, #32]
    stp x6, x7, [sp, #48]
    stp x8, x9, [sp, #64]
    stp x10, x11, [sp, #80]
    stp x12, x13, [sp, #96]
    stp x14, x15, [sp, #112]
    stp x16, x17, [sp, #128]
    stp x18, x19, [sp, #144]
    stp x20, x21, [sp, #160]
    stp x22, x23, [sp, #176]
    stp x24, x25, [sp, #192]
    stp x26, x27, [sp, #208]
    stp x28, x29, [sp, #224]
    str x30, [sp, #240]
.endm
.macro restore_all
    ldp x0, x1, [sp, #0]
    ldp x2, x3, [sp, #16]
    ldp x4, x5, [sp, #32]
    ldp x6, x7, [sp, #48]
    ldp x8, x9, [sp, #64]
    ldp x10, x11, [sp, #80]
    ldp x12, x13, [sp, #96]
    ldp x14, x15, [sp, #112]
    ldp x16, x17, [sp, #128]
    ldp x18, x19, [sp, #144]
    ldp x20, x21, [sp, #160]
    ldp x22, x23, [sp, #176]
    ldp x24, x25, [sp, #192]
    ldp x26, x27, [sp, #208]
    ldp x28, x29, [sp, #224]
    ldr x30, [sp, #240]
    add sp, sp, #256
.endm
.macro handler level
    save_all
    mov x0, #\level
    mrs x1, esr_el\level
    mrs x2, elr_el\level
    mrs x3, spsr_el\level
    mrs x4, far_el\level
    bl probe_exception
    restore_all
    eret
.endm
.macro irq_handler level
    save_all
    mov x0, #\level
    mrs x1, elr_el\level
    mrs x2, spsr_el\level
    bl probe_irq
    restore_all
    eret
.endm
.macro vector_table sync, irq
.rept 4
    b \sync
    .balign 128
    b \irq
    .balign 128
    b \sync
    .balign 128
    b \sync
    .balign 128
.endr
.endm
.balign 2048
vectors_el2:
    vector_table exception_el2, interrupt_el2
exception_el2:
    handler 2
interrupt_el2:
    irq_handler 2
.balign 2048
vectors_el1:
    vector_table exception_el1, interrupt_el1
exception_el1:
    handler 1
interrupt_el1:
    irq_handler 1

/* Every operation has exactly one tested instruction followed by RET. */
.macro regops name, encoding
.global read_\name, write_\name
.type read_\name, %function
read_\name:
    mrs x0, \encoding
    ret
.type write_\name, %function
write_\name:
    msr \encoding, x0
    ret
.endm
regops pmcr, S3_0_C15_C5_0
regops cntenset, S3_0_C15_C5_1
regops cntenclr, S3_0_C15_C5_2
regops ovsset, S3_0_C15_C5_3
regops ovsclr, S3_0_C15_C5_4
regops selr, S3_0_C15_C5_5
regops intenset, S3_0_C15_C5_6
regops intenclr, S3_0_C15_C5_7
regops ccntr, S3_0_C15_C6_0
regops xevtyper, S3_0_C15_C6_1
regops xevcntr, S3_0_C15_C6_2
regops ceid0, S3_0_C15_C6_4
regops ceid1, S3_0_C15_C6_5
regops hole0, S3_0_C15_C6_3
regops hole1, S3_0_C15_C6_7

/* Explicit witness sites used by the timing, access and real IRQ oracle. */
.global read_reference_counter, read_reference_frequency
read_reference_counter:
    mrs x0, CNTPCT_EL0
    ret
read_reference_frequency:
    mrs x0, CNTFRQ_EL0
    ret
.global read_actlr, write_actlr, read_hcr, write_hcr
read_actlr:
    mrs x0, ACTLR_EL2
    ret
write_actlr:
    msr ACTLR_EL2, x0
    ret
read_hcr:
    mrs x0, HCR_EL2
    ret
write_hcr:
    msr HCR_EL2, x0
    ret
.global read_irq_ack, write_irq_eoi
read_irq_ack:
    mrs x0, ICC_IAR1_EL1
    ret
write_irq_eoi:
    msr ICC_EOIR1_EL1, x0
    ret
.section .note.GNU-stack, "", %progbits
