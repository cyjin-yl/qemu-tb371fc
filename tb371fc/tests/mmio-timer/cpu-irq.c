/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdint.h>

/* Independent expectations from the stock and Linux candidate DT contracts. */
#ifndef HYP_TIMER_INTID
#error HYP_TIMER_INTID must select the expected stock (28) or Linux (26) route
#endif
#if HYP_TIMER_INTID != 26 && HYP_TIMER_INTID != 28
#error Unsupported HYP_TIMER_INTID
#endif

#define GICD_CTLR         0x17a00000UL
#define GICR_WAKER        0x17a60014UL
#define GICR_IGROUPR0     0x17a70080UL
#define GICR_ISENABLER0   0x17a70100UL
#define GICR_ICENABLER0   0x17a70180UL
#define GICR_ISPENDR0     0x17a70200UL
#define GICR_ICPENDR0     0x17a70280UL
#define GICR_ISACTIVER0   0x17a70300UL
#define GICR_ICACTIVER0   0x17a70380UL
#define GICR_IPRIORITYR   0x17a70400UL
#define GICR_ICFGR1       0x17a70c04UL
#define TIMER_LINES      ((1U << 26) | (1U << 27) | (1U << 28) | (1U << 30))

enum Timer {
    PHYSICAL,
    VIRTUAL,
    HYPERVISOR,
    NUM_TIMERS,
};

static const unsigned int timer_intid[NUM_TIMERS] = { 30, 27, HYP_TIMER_INTID };

static uint32_t read32(uintptr_t address)
{
    __asm__ volatile("dsb sy" ::: "memory");
    return *(volatile uint32_t *)address;
}

static void write32(uintptr_t address, uint32_t value)
{
    *(volatile uint32_t *)address = value;
    __asm__ volatile("dsb sy" ::: "memory");
}

static uint64_t count(unsigned int timer)
{
    uint64_t value;

    if (timer == VIRTUAL) {
        __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(value) :: "memory");
    } else {
        __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(value) :: "memory");
    }
    return value;
}

static uint64_t control(unsigned int timer)
{
    uint64_t value;

    if (timer == HYPERVISOR) {
        __asm__ volatile("isb; mrs %0, cnthp_ctl_el2" : "=r"(value) :: "memory");
    } else if (timer == VIRTUAL) {
        __asm__ volatile("isb; mrs %0, cntv_ctl_el0" : "=r"(value) :: "memory");
    } else {
        __asm__ volatile("isb; mrs %0, cntp_ctl_el0" : "=r"(value) :: "memory");
    }
    return value;
}

static void set_control(unsigned int timer, uint64_t value)
{
    if (timer == HYPERVISOR) {
        __asm__ volatile("msr cnthp_ctl_el2, %0; isb" :: "r"(value) : "memory");
    } else if (timer == VIRTUAL) {
        __asm__ volatile("msr cntv_ctl_el0, %0; isb" :: "r"(value) : "memory");
    } else {
        __asm__ volatile("msr cntp_ctl_el0, %0; isb" :: "r"(value) : "memory");
    }
}

static void set_compare(unsigned int timer, uint64_t value)
{
    if (timer == HYPERVISOR) {
        __asm__ volatile("msr cnthp_cval_el2, %0; isb" :: "r"(value) : "memory");
    } else if (timer == VIRTUAL) {
        __asm__ volatile("msr cntv_cval_el0, %0; isb" :: "r"(value) : "memory");
    } else {
        __asm__ volatile("msr cntp_cval_el0, %0; isb" :: "r"(value) : "memory");
    }
}

static int line_is(unsigned int timer, unsigned int asserted)
{
    uint32_t expected = asserted ? 1U << timer_intid[timer] : 0;

    /* Check both HYP alternatives too: a second or misplaced wire must fail. */
    return (read32(GICR_ISPENDR0) & TIMER_LINES) == expected;
}

static void semihost(uint64_t op, const void *data)
{
    register uint64_t x0 __asm__("x0") = op;
    register const void *x1 __asm__("x1") = data;

    __asm__ volatile("hlt #0xf000" : "+r"(x0) : "r"(x1) : "memory");
}

static void finish(uint64_t code)
{
    uint64_t block[2] = { 0x20026, code };

    semihost(4, code ? "TB371FC-CPU-TIMER-IRQ:FAIL\n" :
             "TB371FC-CPU-TIMER-IRQ:PASS 24 line observations, 3 IAR/EOI cycles\n");
    semihost(0x20, block);
    for (;;) {
        __asm__ volatile("wfi");
    }
}

static uint64_t acknowledge(void)
{
    uint64_t intid;

    __asm__ volatile("dsb sy; isb; mrs %0, icc_iar1_el1"
                     : "=r"(intid) :: "memory");
    return intid;
}

static void setup_gic(void)
{
    unsigned int intid, retry;
    uint64_t value;

    write32(GICR_WAKER, read32(GICR_WAKER) & ~2U);
    for (retry = 0; read32(GICR_WAKER) & 4; retry++) {
        if (retry == 100000) {
            finish(101);
        }
    }
    write32(GICD_CTLR, 2); /* Group 1; this board has one security state. */
    write32(GICR_ICENABLER0, UINT32_MAX);
    write32(GICR_IGROUPR0, TIMER_LINES);
    write32(GICR_ICFGR1, 0); /* Level-triggered PPIs. */
    write32(GICR_ICPENDR0, TIMER_LINES);
    write32(GICR_ICACTIVER0, TIMER_LINES);
    for (intid = 0; intid < 32; intid++) {
        if (TIMER_LINES & (1U << intid)) {
            *(volatile uint8_t *)(GICR_IPRIORITYR + intid) = 0x80;
        }
    }
    write32(GICR_ISENABLER0, TIMER_LINES);
    value = 15;
    __asm__ volatile("msr icc_sre_el2, %0; isb" :: "r"(value) : "memory");
    value = 7;
    __asm__ volatile("msr icc_sre_el1, %0; isb" :: "r"(value) : "memory");
    value = 2; /* EOImode=1: explicitly exercise priority drop and DIR. */
    __asm__ volatile("msr icc_ctlr_el1, %0; isb" :: "r"(value) : "memory");
    value = 0;
    __asm__ volatile("msr icc_bpr1_el1, %0" :: "r"(value) : "memory");
    value = 255;
    __asm__ volatile("msr icc_pmr_el1, %0" :: "r"(value) : "memory");
    value = 1;
    __asm__ volatile("msr icc_igrpen1_el1, %0; isb" :: "r"(value) : "memory");
}

static void observe_acknowledge(unsigned int timer)
{
    unsigned int expected = timer_intid[timer];
    uint32_t active = 1U << expected;
    uint64_t actual;
    static char observed[] = "TB371FC-CPU-TIMER-IRQ:IAR 00 EOI-CLEAR\n";
    char *digits = observed + sizeof("TB371FC-CPU-TIMER-IRQ:IAR ") - 1;

    set_compare(timer, count(timer) - 1);
    set_control(timer, 1);
    if (!line_is(timer, 1)) {
        finish(9 + 16 * timer);
    }
    actual = acknowledge();
    if (actual != expected || (read32(GICR_ISACTIVER0) & TIMER_LINES) != active) {
        finish(10 + 16 * timer);
    }
    set_control(timer, 0); /* Deassert the level before deactivating. */
    if (!line_is(timer, 0)) {
        finish(11 + 16 * timer);
    }
    __asm__ volatile("msr icc_eoir1_el1, %0; isb"
                     :: "r"(actual) : "memory");
    if ((read32(GICR_ISACTIVER0) & TIMER_LINES) != active) {
        finish(12 + 16 * timer);
    }
    __asm__ volatile("msr icc_dir_el1, %0; isb"
                     :: "r"(actual) : "memory");
    if ((read32(GICR_ISACTIVER0) & TIMER_LINES) || acknowledge() != 1023) {
        finish(13 + 16 * timer);
    }
    digits[0] = '0' + actual / 10;
    digits[1] = '0' + actual % 10;
    semihost(4, observed);
}

void probe_main(void)
{
    unsigned int timer;
    uint64_t deadline, current_el, hcr;

    __asm__ volatile("msr daifset, #15; mrs %0, CurrentEL; mrs %1, hcr_el2"
                     : "=r"(current_el), "=r"(hcr) :: "memory");
    /* Preserve EL1 timer-bank tests, executed at EL2 without VHE aliases. */
    if (current_el != 8 || (hcr & (UINT64_C(1) << 34))) {
        finish(100);
    }
    semihost(4, "TB371FC-CPU-TIMER-IRQ:EL2 E2H=0\n");
    for (timer = 0; timer < NUM_TIMERS; timer++) {
        set_control(timer, 0);
        set_compare(timer, UINT64_MAX);
    }
    setup_gic();
    for (timer = 0; timer < NUM_TIMERS; timer++) {
        if (!line_is(timer, 0)) {
            finish(1 + 16 * timer);
        }

        /* Expire while masked, without a CTL read that can refresh state. */
        deadline = count(timer) + 192000;
        set_compare(timer, deadline);
        set_control(timer, 3);
        while (count(timer) <= deadline) {
            __asm__ volatile("nop");
        }
        if (!line_is(timer, 0)) {
            finish(2 + 16 * timer);
        }

        /* Check the wire before reading CTL: writes must update it. */
        set_control(timer, 1);
        if (!line_is(timer, 1) || control(timer) != 5) {
            finish(3 + 16 * timer);
        }
        set_control(timer, 3);
        if (!line_is(timer, 0) || control(timer) != 7) {
            finish(4 + 16 * timer);
        }
        set_control(timer, 1);
        if (!line_is(timer, 1) || control(timer) != 5) {
            finish(5 + 16 * timer);
        }
        set_control(timer, 0);
        if (!line_is(timer, 0) || control(timer) != 0) {
            finish(6 + 16 * timer);
        }

        /* Enabling a past compare asserts; moving it forward deasserts. */
        set_control(timer, 1);
        if (!line_is(timer, 1) || control(timer) != 5) {
            finish(7 + 16 * timer);
        }
        set_compare(timer, UINT64_MAX);
        if (!line_is(timer, 0) || control(timer) != 1) {
            finish(8 + 16 * timer);
        }
        set_control(timer, 0);
        observe_acknowledge(timer);
    }
    finish(0);
}
