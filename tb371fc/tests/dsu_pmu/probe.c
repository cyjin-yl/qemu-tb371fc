/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Record actual DSU operations; expected values live in independent host code. */
#include <stdint.h>
#include <stddef.h>
typedef uint64_t u64;
typedef uint32_t u32;
#define CONTROL ((volatile u64 *)0x80200000UL)
#define BOX_BASE 0x80210000UL
#define BOX_STRIDE 0x10000UL
#define MAGIC 0x445355504d555030UL
#define SENTINEL 0xd5a5c33cdeadbeefUL
#define NO_ACTIVE (~0UL)
#define MAX_EVENTS 256
#define MAX_IRQS 16
#define MAX_MMIO 32
#define MAX_DELAYS 8
#define HCR_RW (1UL << 31)
#define HCR_TIDCP (1UL << 20)
#define ACTLR_WRITE (1UL << 12)
#define ENABLE_MASK 0x8000003fUL
#define GICD 0x17a00000UL
#define GICR 0x17a60000UL
#define INTID 82UL

enum { ACCESS=1, SHARED=2, CLOCK=3, IRQ=4, ARM=5, SNAPSHOT=6 };
enum { PMCR, CNTENSET, CNTENCLR, OVSSET, OVSCLR, SELR,
       INTENSET, INTENCLR, CCNTR, XEVTYPER, XEVCNTR, CEID0, CEID1, HOLE0, HOLE1 };
struct event {
    u64 seq, phase, el, reg, write, input, output, pc;
    u64 traps, trap_el, esr, elr, spsr, far, complete, reserved;
    u64 time_before, time_after, counter_frequency, hcr, actlr, reserved1, reserved2, reserved3;
};
struct irq_event {
    u64 seq, iar, overflow_before, overflow_after, counter_enable, interrupt_enable;
    u64 time_before, time_after, elr, spsr, level, eoir;
};
struct mmio_event { u64 seq, address, width, write, input, output, complete, reserved; };
struct delay_event { u64 seq, phase, start, end, requested_ticks; };
struct report {
    u64 magic, abi, cpu, mpidr, midr, pfr0, boot_el, epoch;
    u64 recipe, count, fatal, active, done, nonce, hvc_returns, svc_returns;
    u64 irq_count, cntfrq, actlr_initial, actlr_final, hcr_final, mmio_count, delay_count, reserved0;
    u64 phase_hcr, phase_actlr, irq_goal, reserved1, reserved2, reserved3, reserved4, reserved5;
    struct event events[MAX_EVENTS];
    struct irq_event irqs[MAX_IRQS];
    struct mmio_event mmio[MAX_MMIO];
    struct delay_event delays[MAX_DELAYS];
};
_Static_assert(sizeof(struct event)==192, "event ABI");
_Static_assert(offsetof(struct report,events)==256, "header ABI");
_Static_assert(sizeof(struct report)==53312, "complete mailbox ABI");
_Static_assert(sizeof(struct report)<BOX_STRIDE, "mailbox bounds");
extern void secondary_entry(void), enter_el1(u64), enter_el0(void);
extern void el1_return_hvc(void), el0_return_svc(void), el2_return(void), el1_return(void);
extern u64 read_reference_counter(void), read_reference_frequency(void);
extern u64 read_actlr(void), write_actlr(u64), read_hcr(void), write_hcr(u64);
extern u64 read_irq_ack(void), write_irq_eoi(u64);
#define DECL(n) extern u64 read_##n(u64), write_##n(u64)
DECL(pmcr);DECL(cntenset);DECL(cntenclr);DECL(ovsset);DECL(ovsclr);DECL(selr);
DECL(intenset);DECL(intenclr);DECL(ccntr);DECL(xevtyper);DECL(xevcntr);
DECL(ceid0);DECL(ceid1);DECL(hole0);DECL(hole1);
#define PAIR(n) {read_##n,write_##n}
static u64 (*const op[15][2])(u64)={PAIR(pmcr),PAIR(cntenset),PAIR(cntenclr),
    PAIR(ovsset),PAIR(ovsclr),PAIR(selr),PAIR(intenset),PAIR(intenclr),PAIR(ccntr),
    PAIR(xevtyper),PAIR(xevcntr),PAIR(ceid0),PAIR(ceid1),PAIR(hole0),PAIR(hole1)};
static inline void barrier(void) { __asm__ volatile("dsb sy" ::: "memory"); }
static inline void sync(void) { __asm__ volatile("isb" ::: "memory"); }
static inline void yield(void) { __asm__ volatile("yield" ::: "memory"); }
static inline u64 cpu_id(void) {u64 v;__asm__ volatile("mrs %0,tpidr_el0":"=r"(v));return v;}
static volatile struct report *box(void) {return (volatile struct report *)(BOX_BASE+cpu_id()*BOX_STRIDE);}
static void fatal(u64 reason) {box()->fatal=reason;barrier();for(;;)yield();}
static void operation(unsigned phase,unsigned el,unsigned reg,unsigned wr,u64 value)
{
    volatile struct report *r=box();u64 n=r->count;
    if(n>=MAX_EVENTS||reg>=15||wr>1)fatal(0x100);
    volatile struct event *e=&r->events[n];volatile u64 *p=(volatile u64 *)e;
    for(unsigned i=0;i<sizeof(*e)/8;i++)p[i]=0;
    e->seq=n;e->phase=phase;e->el=el;e->reg=reg;e->write=wr;e->input=value;
    e->pc=(u64)op[reg][wr];e->counter_frequency=r->cntfrq;
    e->hcr=r->phase_hcr;e->actlr=r->phase_actlr;
    r->active=n;barrier();e->time_before=read_reference_counter();
    e->output=op[reg][wr](value);sync();e->time_after=read_reference_counter();
    e->complete=1;r->active=NO_ACTIVE;r->count=n+1;barrier();
}
static void rd(unsigned p,unsigned el,unsigned reg){operation(p,el,reg,0,SENTINEL);}
static void wr(unsigned p,unsigned el,unsigned reg,u64 v){operation(p,el,reg,1,v);}
static void controls(u64 hcr,u64 actlr)
{
    write_hcr(hcr);write_actlr(actlr);sync();
    box()->phase_hcr=read_hcr();box()->phase_actlr=read_actlr();
}
static void access_matrix(unsigned phase,unsigned el,int read_after)
{
    for(unsigned reg=0;reg<13;reg++){
        rd(phase,el,reg);wr(phase,el,reg,0);
        if(read_after)rd(phase,el,reg);
    }
    for(unsigned reg=13;reg<15;reg++){rd(phase,el,reg);wr(phase,el,reg,~0UL);}
}
void probe_el0(void)
{
    for(unsigned reg=0;reg<15;reg++){rd(2,0,reg);wr(2,0,reg,~0UL);}
}
static void reset_bank(unsigned p,unsigned el)
{
    wr(p,el,PMCR,6);wr(p,el,CNTENCLR,~0UL);wr(p,el,INTENCLR,~0UL);wr(p,el,OVSCLR,~0UL);
    for(unsigned i=0;i<6;i++){wr(p,el,SELR,i);wr(p,el,XEVTYPER,0);}
    wr(p,el,SELR,0);
}
static void delay_ticks(unsigned phase,u64 ticks)
{
    volatile struct report *r=box();u64 n=r->delay_count;
    if(n>=MAX_DELAYS||ticks==0||ticks>r->cntfrq)fatal(0x110);
    volatile struct delay_event *e=&r->delays[n];e->seq=n;e->phase=phase;e->requested_ticks=ticks;
    e->start=read_reference_counter();do {yield();e->end=read_reference_counter();}while(e->end-e->start<ticks);
    r->delay_count=n+1;barrier();
}
static void read_bank(unsigned p,unsigned el)
{
    for(unsigned i=0;i<6;i++){wr(p,el,SELR,i);rd(p,el,XEVTYPER);rd(p,el,XEVCNTR);}
    rd(p,el,CCNTR);rd(p,el,PMCR);rd(p,el,CNTENSET);rd(p,el,INTENSET);rd(p,el,OVSCLR);
}
static void shared_first(void)
{
    unsigned id=(unsigned)cpu_id();
    if(id==0)reset_bank(10,2);
    rd(11,2,SELR);rd(11,2,CCNTR);
    wr(11,2,SELR,id%6);wr(11,2,XEVTYPER,0xffffffff00000000UL|0x20000011UL|id);
    wr(11,2,XEVCNTR,0xa55a000000000000UL|0x12340000UL|id);
    wr(11,2,CCNTR,0x1122334400000000UL|id);
    rd(11,2,XEVTYPER);rd(11,2,XEVCNTR);rd(11,2,CCNTR);
    wr(12,2,SELR,~0UL);rd(12,2,SELR);wr(12,2,XEVTYPER,~0UL);wr(12,2,XEVCNTR,~0UL);
    rd(12,2,XEVTYPER);rd(12,2,XEVCNTR);wr(12,2,SELR,6);rd(12,2,XEVCNTR);
    wr(12,2,SELR,id%6);rd(12,2,XEVCNTR);
}
static void shared_last(void)
{
    read_bank(13,2);
    if(cpu_id()==7){
        wr(14,2,CNTENSET,~0UL);rd(14,2,CNTENCLR);wr(14,2,CNTENCLR,0x80000015);rd(14,2,CNTENSET);
        wr(14,2,INTENSET,~0UL);rd(14,2,INTENCLR);wr(14,2,INTENCLR,~0UL);rd(14,2,INTENSET);
        wr(14,2,OVSSET,~0UL);rd(14,2,OVSCLR);wr(14,2,OVSCLR,0x80000015);rd(14,2,OVSSET);
        wr(14,2,OVSCLR,~0UL);wr(14,2,CNTENCLR,~0UL);
        /* DSU bit3 is RES0; bit4 X is RAZ/WI. No core-PMU /64 mode. */
        wr(15,2,PMCR,~1UL);rd(15,2,PMCR);read_bank(15,2);wr(15,2,PMCR,0);
    }
}
static void clock_recipe(void)
{
    reset_bank(20,2);
    static const u64 types[6]={0x11,0xa0000011,0x80000011,0x20000011,0x17,0xffff};
    for(unsigned i=0;i<6;i++){
        wr(20,2,SELR,i);wr(20,2,XEVTYPER,types[i]);wr(20,2,XEVCNTR,1000+i*100);
    }
    wr(20,2,CCNTR,10000);wr(20,2,CNTENSET,~0UL);
    wr(21,2,PMCR,1);delay_ticks(21,box()->cntfrq/100);wr(21,2,PMCR,0);read_bank(21,2);
    delay_ticks(22,box()->cntfrq/100);read_bank(22,2);
    wr(23,2,CNTENCLR,1);wr(23,2,PMCR,1);delay_ticks(23,box()->cntfrq/100);
    wr(23,2,PMCR,0);read_bank(23,2);
    wr(24,2,PMCR,2);read_bank(24,2);wr(24,2,PMCR,4);read_bank(24,2);
}
static u64 mmio(unsigned width,u64 address,unsigned write,u64 value)
{
    volatile struct report *r=box();u64 n=r->mmio_count;
    if(n>=MAX_MMIO||(width!=4&&width!=8&&width!=1))fatal(0x120);
    volatile struct mmio_event *e=&r->mmio[n];e->seq=n;e->address=address;e->width=width;e->write=write;e->input=value;
    if(width==8){if(write)*(volatile u64 *)address=value;else value=*(volatile u64 *)address;}
    else if(width==4){if(write)*(volatile u32 *)address=(u32)value;else value=*(volatile u32 *)address;}
    else {if(write)*(volatile uint8_t *)address=(uint8_t)value;else value=*(volatile uint8_t *)address;}
    barrier();e->output=value;e->complete=1;r->mmio_count=n+1;return value;
}
static void gic_configure(void)
{
    /* Actual board: one security state; GICD DS/ARE are RAO/WI. */
    mmio(4,GICD,1,0);
    mmio(4,GICR+0x14,1,0);mmio(4,GICR+0x14,0,0);
    mmio(4,GICD+0x80+(INTID/32)*4,1,1UL<<(INTID%32));
    mmio(1,GICD+0x400+INTID,1,0x80);
    mmio(4,GICD+0xc00+(INTID/16)*4,1,0);
    mmio(8,GICD+0x6000+INTID*8,1,0);
    mmio(4,GICD+0x100+(INTID/32)*4,1,1UL<<(INTID%32));
    mmio(4,GICD,1,2);mmio(4,GICD,0,0);
    __asm__ volatile("msr ICC_SRE_EL1,%0; msr ICC_CTLR_EL1,xzr; msr ICC_BPR1_EL1,xzr; msr ICC_PMR_EL1,%1; msr ICC_IGRPEN1_EL1,%0; isb"::"r"(1UL),"r"(255UL):"memory");
}
static void wait_irq(unsigned goal)
{
    volatile struct report *r=box();u64 start=read_reference_counter();r->irq_goal=goal;
    __asm__ volatile("msr daifclr,#2; isb":::"memory");
    while(r->irq_count<goal){if(read_reference_counter()-start>r->cntfrq)fatal(0x130);yield();}
    __asm__ volatile("msr daifset,#2; isb":::"memory");
}
static void irq_recipe(void)
{
    volatile struct report *r=box();reset_bank(30,1);gic_configure();
    wr(30,1,INTENSET,1);wr(30,1,OVSSET,1);wait_irq(1);
    wr(31,1,SELR,0);wr(31,1,XEVTYPER,0x11);wr(31,1,XEVCNTR,0xffffffffUL-CONTROL[4]/100+1);
    wr(31,1,CNTENSET,1);wr(31,1,PMCR,1);wait_irq(2);wr(31,1,PMCR,0);read_bank(31,1);
    wr(32,1,INTENCLR,~0UL);wr(32,1,INTENSET,1UL<<31);wr(32,1,CCNTR,~0UL-CONTROL[4]/100+1);
    wr(32,1,CNTENSET,1UL<<31);wr(32,1,PMCR,1);wait_irq(3);wr(32,1,PMCR,0);read_bank(32,1);
    wr(33,1,INTENCLR,~0UL);wr(33,1,OVSCLR,~0UL);wr(33,1,CNTENCLR,~0UL);
    __asm__ volatile("msr ICC_IGRPEN1_EL1,xzr; isb":::"memory");
    mmio(4,GICD+0x180+(INTID/32)*4,1,1UL<<(INTID%32));
    r->irq_goal=3;
}
static void arm_recipe(void)
{
    reset_bank(40,2);
    for(unsigned i=0;i<6;i++){
        wr(40,2,SELR,i);wr(40,2,XEVTYPER,0x11);wr(40,2,XEVCNTR,0xffffffffUL-CONTROL[4]*2+i);
    }
    wr(40,2,CCNTR,~0UL-CONTROL[4]*2);wr(40,2,CNTENSET,ENABLE_MASK);
    wr(40,2,INTENSET,0x80000001);wr(40,2,PMCR,1);
    /* Separate software-status witness: restore must publish this high line only after all eight CPU guards. */
    wr(40,2,OVSSET,1);
}
void probe_el1(u64 phase)
{
    if(phase==1){access_matrix(1,1,1);enter_el0();}
    else if(phase==3){access_matrix(3,1,1);}
    else if(phase==5){access_matrix(5,1,0);}
    else if(phase==30){irq_recipe();}
    else fatal(0x140);
}
void probe_exception(u64 level,u64 esr,u64 elr,u64 spsr,u64 far)
{
    volatile struct report *r=box();u64 ec=esr>>26;
    if(level==2&&ec==0x16&&(esr&0xffff)==0x52&&elr==(u64)el1_return_hvc+4&&(spsr&15)==5){
        r->hvc_returns++;__asm__ volatile("msr elr_el2,%0; msr spsr_el2,%1"::"r"((u64)el2_return),"r"(0x3c9UL):"memory");return;
    }
    if(level==1&&ec==0x15&&(esr&0xffff)==0x51&&elr==(u64)el0_return_svc+4&&(spsr&15)==0){
        r->svc_returns++;__asm__ volatile("msr elr_el1,%0; msr spsr_el1,%1"::"r"((u64)el1_return),"r"(0x3c5UL):"memory");return;
    }
    u64 n=r->active;if(n>=MAX_EVENTS||(ec!=0&&ec!=0x18))fatal(0x200|ec);
    volatile struct event *e=&r->events[n];if(e->pc!=elr||e->traps)fatal(0x300);
    e->traps=1;e->trap_el=level;e->esr=esr;e->elr=elr;e->spsr=spsr;e->far=far;
    if(level==2)__asm__ volatile("msr elr_el2,%0"::"r"(elr+4):"memory");
    else if(level==1)__asm__ volatile("msr elr_el1,%0"::"r"(elr+4):"memory");
    else fatal(0x400);
}
void probe_irq(u64 level,u64 elr,u64 spsr)
{
    volatile struct report *r=box();u64 n=r->irq_count;if(n>=MAX_IRQS||level!=1)fatal(0x410);
    volatile struct irq_event *e=&r->irqs[n];e->seq=n;e->level=level;e->elr=elr;e->spsr=spsr;
    e->time_before=read_reference_counter();e->iar=read_irq_ack();if(e->iar!=INTID)fatal(0x420);
    e->overflow_before=read_ovsclr(0);write_cntenclr(e->overflow_before);write_ovsclr(e->overflow_before);sync();
    e->overflow_after=read_ovsclr(0);e->counter_enable=read_cntenset(0);e->interrupt_enable=read_intenset(0);
    write_irq_eoi(e->iar);sync();e->eoir=e->iar;e->time_after=read_reference_counter();
    barrier();r->irq_count=n+1;barrier();
}
static u64 cpu_on(u64 id)
{
    register u64 x0 __asm__("x0")=0xc4000003;register u64 x1 __asm__("x1")=id<<8;
    register u64 x2 __asm__("x2")=(u64)secondary_entry;register u64 x3 __asm__("x3")=id;
    __asm__ volatile("smc #0":"+r"(x0):"r"(x1),"r"(x2),"r"(x3):"memory");return x0;
}
void probe_main(u64 id)
{
    volatile struct report *r=box();volatile u64 *p=(volatile u64 *)r;
    for(unsigned i=0;i<sizeof(*r)/8;i++)p[i]=0;
    r->magic=MAGIC;r->abi=1;r->cpu=id;r->active=NO_ACTIVE;u64 v;
    __asm__ volatile("mrs %0,mpidr_el1":"=r"(v));r->mpidr=v;
    __asm__ volatile("mrs %0,midr_el1":"=r"(v));r->midr=v;
    __asm__ volatile("mrs %0,id_aa64pfr0_el1":"=r"(v));r->pfr0=v;
    __asm__ volatile("mrs %0,CurrentEL":"=r"(v));r->boot_el=v;
    r->cntfrq=read_reference_frequency();r->actlr_initial=read_actlr();
    if(id>=8||v!=8||r->cntfrq==0)fatal(0x500);
    if(id==0){for(unsigned i=0;i<8;i++)CONTROL[8+i]=~0UL;CONTROL[8]=0;
        for(unsigned i=1;i<8;i++)CONTROL[8+i]=cpu_on(i);barrier();}
    u64 previous=0;
    for(;;){
        u64 epoch=CONTROL[0];if(epoch==0||epoch==previous){yield();continue;}
        previous=epoch;r->done=0;r->count=0;r->fatal=0;r->active=NO_ACTIVE;
        r->hvc_returns=0;r->svc_returns=0;r->irq_count=0;r->mmio_count=0;r->delay_count=0;
        r->epoch=epoch;r->nonce=CONTROL[1];r->recipe=CONTROL[2];r->irq_goal=0;
        /* Clear evidence arrays so unused slots remain a strict zero witness. */
        p=(volatile u64 *)&r->events[0];
        for(unsigned i=0;i<(sizeof(*r)-offsetof(struct report,events))/8;i++)p[i]=0;
        while(CONTROL[3]!=id)yield();
        controls(HCR_RW,ACTLR_WRITE);
        if(r->recipe==ACCESS){
            enter_el1(1);controls(HCR_RW,0);enter_el1(3);
            controls(HCR_RW|HCR_TIDCP,ACTLR_WRITE);access_matrix(4,2,1);enter_el1(5);
            controls(HCR_RW,ACTLR_WRITE);
        }else if(r->recipe==SHARED){shared_first();}
        else if(id==0&&r->recipe==CLOCK){clock_recipe();}
        else if(id==0&&r->recipe==IRQ){enter_el1(30);}
        else if(id==0&&r->recipe==ARM){arm_recipe();}
        else if(id==0&&r->recipe==SNAPSHOT){read_bank(50,2);wr(50,2,PMCR,0);read_bank(51,2);}
        else if(r->recipe<CLOCK||r->recipe>SNAPSHOT){fatal(0x510);}
        barrier();CONTROL[3]=id+1;barrier();
        if(r->recipe==SHARED){while(CONTROL[3]!=8+id)yield();shared_last();barrier();CONTROL[3]=9+id;barrier();}
        r->actlr_final=read_actlr();r->hcr_final=read_hcr();barrier();r->done=epoch;barrier();
    }
}
