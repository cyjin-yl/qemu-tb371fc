/* SPDX-License-Identifier: GPL-2.0-or-later */
.arch armv8.6-a+crypto+sve+sha3+rng
.section .text.boot, "ax"
.global _start
_start:
    ldr x0, =0x300000
    mov sp, x0
    adr x0, vectors
    msr vbar_el3, x0
    msr cptr_el3, xzr
    msr fpcr, xzr
    /*
     * Reset MMU-off accesses are Device memory. Map the first 1 GiB as
     * Normal WB memory, using a 4 KiB level-1 table and a 1 GiB block.
     * TCR: 39-bit VA, 40-bit PA, inner shareable WBWA, required RES1 bits.
     * This also exercises the architectural stage-1 translation machinery.
     */
    adr x0, translation_table
    msr ttbr0_el3, x0
    ldr x0, =0x80823519
    msr tcr_el3, x0
    mov x0, #0xff
    msr mair_el3, x0
    dsb sy
    tlbi alle3
    dsb sy
    isb
    mrs x0, sctlr_el3
    orr x0, x0, #1
    orr x0, x0, #4
    orr x0, x0, #0x1000
    msr sctlr_el3, x0
    isb
    bl probe_main
1:  wfi
    b 1b

.balign 2048
vectors:
.rept 16
    b handle_exception
    .balign 128
.endr

handle_exception:
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
    mrs x0, esr_el3
    mrs x1, elr_el3
    mrs x2, far_el3
    bl exception
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
    eret

.macro function name
.global \name
.type \name, %function
\name:
.endm

function insn_ldadd
    ldadd x1, x2, [x0]
    mov x0, x2
    ret
function insn_cas
    cas x1, x2, [x0]
    mov x0, x1
    ret
function insn_ldapr
    ldapr x0, [x0]
    ret
function insn_fp16
    fmov h0, #1.0
    fmov h1, #2.0
    fadd h0, h0, h1
    fmov w0, h0
    ret
function insn_sdot
    movi v0.4s, #0
    movi v1.16b, #1
    movi v2.16b, #2
    sdot v0.4s, v1.16b, v2.16b
    umov w0, v0.s[0]
    ret
function insn_udot
    movi v0.4s, #0
    movi v1.16b, #255
    movi v2.16b, #2
    udot v0.4s, v1.16b, v2.16b
    umov w0, v0.s[0]
    ret
function insn_rdm
    movi v0.8h, #100
    movi v1.8h, #0x40, lsl #8
    sqrdmlah v0.8h, v1.8h, v1.8h
    umov w0, v0.h[0]
    ret
function insn_aese
    movi v0.16b, #0
    movi v1.16b, #0
    aese v0.16b, v1.16b
    umov w0, v0.s[0]
    ret
function insn_aesd
    movi v0.16b, #0
    movi v1.16b, #0
    aesd v0.16b, v1.16b
    umov w0, v0.s[0]
    ret
function insn_pmull
    mov x0, #0x53
    fmov d0, x0
    mov x0, #0xca
    fmov d1, x0
    pmull v0.1q, v0.1d, v1.1d
    fmov x0, d0
    ret
function insn_sha1h
    fmov s0, w0
    sha1h s0, s0
    fmov w0, s0
    ret
function insn_sha256su0
    dup v0.4s, w0
    dup v1.4s, w1
    sha256su0 v0.4s, v1.4s
    str q0, [x2]
    ret
function insn_crc32
    mov w1, w0
    mov w0, #0
    crc32w w0, w0, w1
    ret
function insn_crc32c
    mov w1, w0
    mov w0, #0
    crc32cw w0, w0, w1
    ret

/* Non-HINT encodings must actually take an Undefined Instruction exception. */
function undef_sha256
    sha256su0 v0.4s, v1.4s
    ret
function undef_sve
    ptrue p0.b
    ret
function undef_pauth
    pacia x0, x1
    ret
function undef_sha512
    sha512h q0, q1, v2.2d
    ret
function undef_fhm
    fmlal v0.2s, v1.2h, v2.2h
    ret
function undef_rng
    mrs x0, rndr
    ret
function undef_bf16
    bfdot v0.4s, v1.8h, v2.8h
    ret
.section .data, "aw"
.balign 4096
translation_table:
    .quad 0x701 /* AF, inner shareable, AttrIdx0, EL3 RW, block at PA 0. */
    .zero 4088
.section .note.GNU-stack, "", %progbits
