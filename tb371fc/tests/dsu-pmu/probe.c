/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Record instructions and exceptions; register expectations live on the host. */
#include <stdint.h>
typedef uint64_t u64;
#define CONTROL ((volatile u64 *)0x80200000UL)
#define BOX_BASE 0x80210000UL
#define STRIDE 0x1000UL
#define MAGIC 0x44535550524f4245UL
struct box {
    u64 magic, cpu, boot_el, reserved, request, done, el, reg, write, input;
    u64 output, traps, trap_el, esr, elr, pc, fatal, active;
    u64 padding[14];
};
_Static_assert(sizeof(struct box) == 256, "mailbox ABI");
extern void secondary_entry(void), enter_el1(u64), enter_el0(void);
extern void el1_return_hvc(void), el0_return_svc(void), el2_return(void), el1_return(void);
#define DECL(n) extern u64 read_##n(u64), write_##n(u64)
DECL(pmcr); DECL(cntenset); DECL(cntenclr); DECL(ovsset); DECL(ovsclr);
DECL(selr); DECL(intenset); DECL(intenclr); DECL(ccnt); DECL(evtype);
DECL(evcnt); DECL(ceid0); DECL(ceid1); DECL(actlr); DECL(unknown);
static u64 gic_read(u64 unused) {
    (void)unused;
    __asm__ volatile("dsb sy" ::: "memory");
    return *(volatile uint32_t *)0x17a00208UL; /* GICD_ISPENDR2, INTID 82 */
}
static u64 gic_clear(u64 value) {
    *(volatile uint32_t *)0x17a00288UL = value; /* GICD_ICPENDR2 */
    __asm__ volatile("dsb sy" ::: "memory");
    return value;
}
#define PAIR(n) {read_##n, write_##n}
static u64 (*const ops[16][2])(u64) = {
    PAIR(pmcr), PAIR(cntenset), PAIR(cntenclr), PAIR(ovsset), PAIR(ovsclr),
    PAIR(selr), PAIR(intenset), PAIR(intenclr), PAIR(ccnt), PAIR(evtype),
    PAIR(evcnt), PAIR(ceid0), PAIR(ceid1), PAIR(actlr),
    {gic_read, gic_clear}, PAIR(unknown)
};
static void barrier(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static volatile struct box *box(void) {
    u64 id; __asm__ volatile("mrs %0, tpidr_el0" : "=r"(id));
    return (volatile struct box *)(BOX_BASE + id * STRIDE);
}
static void fatal(u64 value) {
    box()->fatal = value; barrier();
    for (;;) { __asm__ volatile("yield"); }
}
static void execute(void) {
    volatile struct box *r = box();
    if (r->reg >= 16 || r->write > 1) { fatal(1); }
    r->pc = (u64)ops[r->reg][r->write]; r->active = 1; barrier();
    r->output = ops[r->reg][r->write](r->input);
    r->active = 0; barrier();
}
void probe_el0(void) { execute(); }
void probe_el1(u64 unused) {
    (void)unused;
    if (box()->el == 0) { enter_el0(); } else { execute(); }
}
void probe_exception(u64 level, u64 esr, u64 elr, u64 spsr, u64 far) {
    (void)far;
    volatile struct box *r=box(); u64 ec=esr >> 26;
    if (level==2 && ec==0x16 && (esr & 0xffff)==0x52 &&
        elr==(u64)el1_return_hvc+4 && (spsr & 15)==5) {
        __asm__ volatile("msr elr_el2, %0; msr spsr_el2, %1" ::
            "r"((u64)el2_return), "r"(0x3c9UL) : "memory"); return;
    }
    if (level==1 && ec==0x15 && (esr & 0xffff)==0x51 &&
        elr==(u64)el0_return_svc+4 && (spsr & 15)==0) {
        __asm__ volatile("msr elr_el1, %0; msr spsr_el1, %1" ::
            "r"((u64)el1_return), "r"(0x3c5UL) : "memory"); return;
    }
    if (!r->active || r->traps || r->pc!=elr || (ec!=0 && ec!=0x18)) {
        fatal(0x100 | ec);
    }
    r->traps=1; r->trap_el=level; r->esr=esr; r->elr=elr;
    if (level==2) { __asm__ volatile("msr elr_el2, %0" :: "r"(elr+4) : "memory"); }
    else if (level==1) { __asm__ volatile("msr elr_el1, %0" :: "r"(elr+4) : "memory"); }
    else { fatal(0x200); }
}
static u64 cpu_on(u64 id) {
    register u64 x0 __asm__("x0")=0xc4000003, x1 __asm__("x1")=id<<8;
    register u64 x2 __asm__("x2")=(u64)secondary_entry, x3 __asm__("x3")=id;
    __asm__ volatile("smc #0" : "+r"(x0) : "r"(x1),"r"(x2),"r"(x3) : "memory");
    return x0;
}
void probe_main(u64 id) {
    volatile struct box *r=box(); volatile u64 *words=(volatile u64 *)r;
    for (unsigned i=0;i<32;i++) { words[i]=0; }
    r->cpu=id; u64 el;
    __asm__ volatile("mrs %0, CurrentEL" : "=r"(el));
    r->boot_el=el; if (id>=8 || el!=8) { fatal(0x300); }
    r->magic=MAGIC; barrier();
    if (!id) {
        for (unsigned i=1;i<8;i++) { CONTROL[i]=cpu_on(i); }
        barrier(); CONTROL[0]=1; barrier();
    }
    u64 previous=0;
    for (;;) {
        u64 request=r->request;
        if (!request || request==previous) { __asm__ volatile("yield"); continue; }
        previous=request; r->traps=0; r->trap_el=0; r->esr=0; r->elr=0;
        r->fatal=0; r->output=0; barrier();
        if (r->el==2) { execute(); }
        else if (r->el<=1) { enter_el1(0); }
        else { fatal(0x400); }
        barrier(); r->done=request; barrier();
    }
}
