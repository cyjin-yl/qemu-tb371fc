/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Test payload: record actual operations; the host owns the expected oracle. */
#include <stdint.h>
#include <stddef.h>

typedef uint64_t u64;
#define CONTROL ((volatile u64 *)0x80200000UL)
#define BOX_BASE 0x80210000UL
#define BOX_STRIDE 0x8000UL
#define MAGIC 0x525a524153505230UL
#define SENTINEL 0x52a5c33cdeadbeefUL
#define NO_ACTIVE (~0UL)
#define MAX_EVENTS 128
#define HCR_RW (1UL << 31)
#define HCR_TERR (1UL << 36)

struct event {
    u64 seq, phase, el, reg, write, input, output, pc;
    u64 traps, trap_el, esr, elr, spsr, far, complete, reserved;
};
struct report {
    u64 magic, abi, cpu, mpidr, midr, pfr0, boot_el, epoch;
    u64 count, fatal, active, done, nonce, hvc_returns, svc_returns, reserved;
    struct event events[MAX_EVENTS];
};
_Static_assert(sizeof(struct event) == 128, "event ABI");
_Static_assert(offsetof(struct report, events) == 128, "header ABI");
_Static_assert(sizeof(struct report) < BOX_STRIDE, "mailbox bounds");

extern void secondary_entry(void), enter_el1(u64 phase), enter_el0(void);
extern void el1_return_hvc(void), el0_return_svc(void);
extern void el2_return(void), el1_return(void);
#define DECL(n) extern u64 read_##n(u64), write_##n(u64)
DECL(erridr); DECL(errselr); DECL(erxfr); DECL(erxctlr); DECL(erxstatus);
DECL(erxaddr); DECL(erxmisc0); DECL(erxmisc1); DECL(unknown0); DECL(unknown1);
#define PAIR(n) {read_##n, write_##n}
static u64 (*const operations[10][2])(u64) = {
    PAIR(erridr), PAIR(errselr), PAIR(erxfr), PAIR(erxctlr), PAIR(erxstatus),
    PAIR(erxaddr), PAIR(erxmisc0), PAIR(erxmisc1), PAIR(unknown0), PAIR(unknown1)
};
static const unsigned rw[6] = {1, 3, 4, 5, 6, 7};

static inline void barrier(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static inline u64 cpu_id(void)
{
    u64 v;
    __asm__ volatile("mrs %0, tpidr_el0" : "=r"(v));
    return v;
}
static volatile struct report *box(void)
{
    return (volatile struct report *)(BOX_BASE + cpu_id() * BOX_STRIDE);
}
static void fatal(u64 reason)
{
    volatile struct report *r = box();
    r->fatal = reason;
    barrier();
    for (;;) { __asm__ volatile("yield"); }
}
static void operation(unsigned phase, unsigned el, unsigned reg, unsigned wr, u64 input)
{
    volatile struct report *r = box();
    u64 n = r->count;
    if (n >= MAX_EVENTS || reg >= 10 || wr > 1) { fatal(0x100); }
    volatile struct event *e = &r->events[n];
    volatile u64 *p = (volatile u64 *)e;
    for (unsigned j = 0; j < sizeof(*e) / sizeof(u64); j++) { p[j] = 0; }
    e->seq = n;
    e->phase = phase;
    e->el = el;
    e->reg = reg;
    e->write = wr;
    e->input = input;
    e->pc = (u64)operations[reg][wr];
    r->active = n;
    barrier();
    e->output = operations[reg][wr](input);
    e->complete = 1;
    r->active = NO_ACTIVE;
    r->count = n + 1;
    barrier();
}
static void rd(unsigned phase, unsigned el, unsigned reg)
{
    operation(phase, el, reg, 0, SENTINEL);
}
static void wr(unsigned phase, unsigned el, unsigned reg, u64 value)
{
    operation(phase, el, reg, 1, value);
}
/* 36 instructions: reads, ignored writes, selectors, original-probe values,
 * both read-only writes, and neighboring unknown encodings. */
static void full_matrix(unsigned phase, unsigned el)
{
    for (unsigned i = 0; i < 8; i++) { rd(phase, el, i); }
    for (unsigned i = 0; i < 6; i++) {
        wr(phase, el, rw[i], ~0UL); rd(phase, el, rw[i]);
    }
    static const u64 selectors[3] = {0, 1, 65535};
    for (unsigned i = 0; i < 3; i++) {
        wr(phase, el, 1, selectors[i]); rd(phase, el, 1);
    }
    wr(phase, el, 3, 0x10f); rd(phase, el, 3);
    wr(phase, el, 6, 0x7f7f00000000UL); rd(phase, el, 6);
    wr(phase, el, 0, ~0UL); wr(phase, el, 2, ~0UL);
    for (unsigned i = 8; i < 10; i++) {
        rd(phase, el, i); wr(phase, el, i, ~0UL);
    }
}
void probe_el0(void)
{
    for (unsigned i = 0; i < 10; i++) {
        rd(2, 0, i); wr(2, 0, i, ~0UL);
    }
}
void probe_el1(u64 phase)
{
    if (phase == 1) {
        full_matrix(1, 1);
        enter_el0();
    } else if (phase == 4) {
        for (unsigned i = 0; i < 8; i++) { rd(4, 1, i); }
        for (unsigned i = 0; i < 6; i++) { wr(4, 1, rw[i], ~0UL); }
        wr(4, 1, 0, ~0UL); wr(4, 1, 2, ~0UL);
        for (unsigned i = 8; i < 10; i++) {
            rd(4, 1, i); wr(4, 1, i, ~0UL);
        }
    } else { fatal(0x101); }
}
void probe_exception(u64 level, u64 esr, u64 elr, u64 spsr, u64 far)
{
    volatile struct report *r = box();
    u64 ec = esr >> 26;
    if (level == 2 && ec == 0x16 && (esr & 0xffff) == 0x52 &&
        elr == (u64)el1_return_hvc + 4 && (spsr & 15) == 5) {
        r->hvc_returns++;
        __asm__ volatile("msr elr_el2, %0; msr spsr_el2, %1" ::
                         "r"((u64)el2_return), "r"(0x3c9UL) : "memory");
        return;
    }
    if (level == 1 && ec == 0x15 && (esr & 0xffff) == 0x51 &&
        elr == (u64)el0_return_svc + 4 && (spsr & 15) == 0) {
        r->svc_returns++;
        __asm__ volatile("msr elr_el1, %0; msr spsr_el1, %1" ::
                         "r"((u64)el1_return), "r"(0x3c5UL) : "memory");
        return;
    }
    u64 n = r->active;
    if (n >= MAX_EVENTS || (ec != 0 && ec != 0x18)) { fatal(0x200 | ec); }
    volatile struct event *e = &r->events[n];
    if (e->pc != elr || e->traps != 0) { fatal(0x300); }
    e->traps = 1;
    e->trap_el = level;
    e->esr = esr;
    e->elr = elr;
    e->spsr = spsr;
    e->far = far;
    if (level == 2) {
        __asm__ volatile("msr elr_el2, %0" :: "r"(elr + 4) : "memory");
    } else if (level == 1) {
        __asm__ volatile("msr elr_el1, %0" :: "r"(elr + 4) : "memory");
    } else { fatal(0x400); }
}
static u64 cpu_on(u64 id)
{
    register u64 x0 __asm__("x0") = 0xc4000003;
    register u64 x1 __asm__("x1") = id << 8;
    register u64 x2 __asm__("x2") = (u64)secondary_entry;
    register u64 x3 __asm__("x3") = id;
    __asm__ volatile("smc #0" : "+r"(x0) : "r"(x1), "r"(x2), "r"(x3) : "memory");
    return x0;
}
void probe_main(u64 id)
{
    volatile struct report *r = box();
    volatile u64 *p = (volatile u64 *)r;
    for (unsigned i = 0; i < sizeof(*r) / sizeof(u64); i++) { p[i] = 0; }
    r->magic = MAGIC; r->abi = 1; r->cpu = id; r->active = NO_ACTIVE;
    u64 value;
    __asm__ volatile("mrs %0, mpidr_el1" : "=r"(value)); r->mpidr = value;
    __asm__ volatile("mrs %0, midr_el1" : "=r"(value)); r->midr = value;
    __asm__ volatile("mrs %0, id_aa64pfr0_el1" : "=r"(value)); r->pfr0 = value;
    __asm__ volatile("mrs %0, CurrentEL" : "=r"(value)); r->boot_el = value;
    if (id >= 8 || value != 8) { fatal(0x500); }
    if (id == 0) {
        for (unsigned i = 0; i < 8; i++) { CONTROL[2 + i] = ~0UL; }
        CONTROL[2] = 0;
        for (unsigned i = 1; i < 8; i++) { CONTROL[2 + i] = cpu_on(i); }
        barrier();
    }
    u64 previous = 0;
    for (;;) {
        u64 epoch = CONTROL[0];
        if (epoch == 0 || epoch == previous) { __asm__ volatile("yield"); continue; }
        previous = epoch;
        r->done = 0; r->count = 0; r->fatal = 0;
        r->hvc_returns = 0; r->svc_returns = 0;
        r->epoch = epoch; r->nonce = CONTROL[1];
        __asm__ volatile("msr hcr_el2, %0; isb" :: "r"(HCR_RW) : "memory");
        enter_el1(1);
        __asm__ volatile("msr hcr_el2, %0; isb" :: "r"(HCR_RW | HCR_TERR) : "memory");
        full_matrix(3, 2);
        enter_el1(4);
        __asm__ volatile("msr hcr_el2, %0; isb" :: "r"(HCR_RW) : "memory");
        barrier();
        r->done = epoch;
        barrier();
    }
}
