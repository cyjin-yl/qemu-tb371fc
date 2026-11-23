/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdint.h>

#define CONTROL 0x17c20000UL
#define FRAME 0x17c21000UL

static uint64_t mrs_pct(void)
{
    uint64_t v;
    __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(v) :: "memory");
    return v;
}

static uint64_t mrs_vct(void)
{
    uint64_t v;
    __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(v) :: "memory");
    return v;
}

static uint64_t readq(uint64_t addr)
{
    uint64_t v = *(volatile uint64_t *)addr;
    __asm__ volatile("dsb sy" ::: "memory");
    return v;
}

static void writeq(uint64_t addr, uint64_t v)
{
    *(volatile uint64_t *)addr = v;
    __asm__ volatile("dsb sy" ::: "memory");
}

static void writel(uint64_t addr, uint32_t v)
{
    *(volatile uint32_t *)addr = v;
    __asm__ volatile("dsb sy" ::: "memory");
}

static void semihost(uint64_t op, const void *data)
{
    register uint64_t x0 __asm__("x0") = op;
    register const void *x1 __asm__("x1") = data;
    __asm__ volatile("hlt #0xf000" : "+r"(x0) : "r"(x1) : "memory");
}

#ifdef ICOUNT_RATE_TEST
static uint64_t read_ctl(unsigned int virt)
{
    uint64_t v;
    if (virt) {
        __asm__ volatile("mrs %0, cntv_ctl_el0" : "=r"(v));
    } else {
        __asm__ volatile("mrs %0, cntp_ctl_el0" : "=r"(v));
    }
    return v;
}

static void write_ctl(unsigned int virt, uint64_t v)
{
    if (virt) {
        __asm__ volatile("msr cntv_ctl_el0, %0; isb" :: "r"(v) : "memory");
    } else {
        __asm__ volatile("msr cntp_ctl_el0, %0; isb" :: "r"(v) : "memory");
    }
}

static uint64_t read_cval(unsigned int virt)
{
    uint64_t v;
    if (virt) {
        __asm__ volatile("mrs %0, cntv_cval_el0" : "=r"(v));
    } else {
        __asm__ volatile("mrs %0, cntp_cval_el0" : "=r"(v));
    }
    return v;
}

static void write_cval(unsigned int virt, uint64_t v)
{
    if (virt) {
        __asm__ volatile("msr cntv_cval_el0, %0; isb" :: "r"(v) : "memory");
    } else {
        __asm__ volatile("msr cntp_cval_el0, %0; isb" :: "r"(v) : "memory");
    }
}

static void write_tval(unsigned int virt, uint64_t v)
{
    if (virt) {
        __asm__ volatile("msr cntv_tval_el0, %0; isb" :: "r"(v) : "memory");
    } else {
        __asm__ volatile("msr cntp_tval_el0, %0; isb" :: "r"(v) : "memory");
    }
}

static uint64_t read_count(unsigned int virt)
{
    return virt ? mrs_vct() : mrs_pct();
}

static int wait_for_expiry(unsigned int virt, uint64_t cval)
{
    unsigned int i;
    for (i = 0; i < 100000; i++) {
        uint64_t before = read_count(virt);
        uint64_t status = read_ctl(virt) & 4;
        uint64_t after = read_count(virt);
        if (status) {
            return after >= cval;
        }
        if (before > cval) {
            return 0;
        }
    }
    return 0;
}

static int cpu_timer_probe(void)
{
    unsigned int virt, i;
    uint64_t before, after, compare, offset;
    int middle = 0;

    for (virt = 0; virt < 2; virt++) {
        write_ctl(virt, 0);
        write_cval(virt, UINT64_MAX);
        if (read_cval(virt) != UINT64_MAX) {
            return 0;
        }
        write_ctl(virt, 1);
        if (read_ctl(virt) != 1) {
            return 0;
        }
        before = read_count(virt);
        write_tval(virt, 100);
        compare = read_cval(virt);
        after = read_count(virt);
        if (compare < before + 100 || compare > after + 100 ||
            !wait_for_expiry(virt, compare)) {
            return 0;
        }
        write_ctl(virt, 3);
        if (read_ctl(virt) != 7) {
            return 0;
        }
        write_ctl(virt, 1);
        if (read_ctl(virt) != 5) {
            return 0;
        }
        write_ctl(virt, 0);
        if (read_ctl(virt) != 0) {
            return 0;
        }
        before = read_count(virt);
        write_tval(virt, (uint64_t)-2);
        compare = read_cval(virt);
        after = read_count(virt);
        if (compare < before - 2 || compare > after - 2) {
            return 0;
        }
        write_ctl(virt, 1);
        if (read_ctl(virt) != 5) {
            return 0;
        }
        write_ctl(virt, 0);
    }

    /* Virtual subtraction wraps, deasserts at offset, then expires again. */
    offset = mrs_pct() + 128;
    __asm__ volatile("msr cntvoff_el2, %0; isb" :: "r"(offset) : "memory");
    write_cval(1, 64);
    write_ctl(1, 1);
    if (read_ctl(1) != 5) {
        return 0;
    }
    for (i = 0; i < 100000; i++) {
        before = mrs_vct();
        compare = read_ctl(1);
        after = mrs_vct();
        if (before < 64 && after < 64) {
            if (compare != 1) {
                return 0;
            }
            middle = 1;
            break;
        }
    }
    if (!middle || !wait_for_expiry(1, 64)) {
        return 0;
    }
    write_ctl(1, 0);
    __asm__ volatile("msr cntvoff_el2, xzr; isb" ::: "memory");
    semihost(4, "TB371FC-CPU-TIMERS:PASS physical virtual signed-tval wrap\n");
    return 1;
}

extern uint64_t probe_rate_delta(void);

static void report_rate(void)
{
    static char output[] = "TB371FC-ICOUNT-RATE:0x0000000000000000\n";
    const char digits[] = "0123456789abcdef";
    const unsigned int base = sizeof("TB371FC-ICOUNT-RATE:0x") - 1;
    uint64_t annotation = 123;
    uint64_t value;
    /* CNTFRQ writes annotate frequency; they must not retime the counter. */
    __asm__ volatile("msr cntfrq_el0, %0; isb" :: "r"(annotation) : "memory");
    value = probe_rate_delta();
    annotation = 19200000;
    __asm__ volatile("msr cntfrq_el0, %0; isb" :: "r"(annotation) : "memory");
    unsigned int i;

    for (i = 0; i < 16; i++) {
        output[base + i] = digits[(value >> ((15 - i) * 4)) & 15];
    }
    semihost(4, output);
}
#endif

static void finish(uint64_t result)
{
    uint64_t block[2] = { 0x20026, result };
    semihost(4, result ? "TB371FC-MMIO-MRS:FAIL\n" :
                         "TB371FC-MMIO-MRS:PASS 1024 comparisons\n");
    semihost(0x20, block);
    for (;;) {
        __asm__ volatile("wfi");
    }
}

void probe_main(void)
{
    uint64_t el, frequency, before, after, value, cpu_offset = 0x1000;
    uint64_t frame_offset = 0x100;
    unsigned int i;

    __asm__ volatile("mrs %0, CurrentEL" : "=r"(el));
    __asm__ volatile("mrs %0, cntfrq_el0" : "=r"(frequency));
    if (el != 8 || frequency != 19200000) {
        finish(1);
    }
    writel(CONTROL + 0x40, 0x3f);
    writeq(CONTROL + 0x80, frame_offset);
    __asm__ volatile("msr cntvoff_el2, %0; isb" :: "r"(cpu_offset) : "memory");

    for (i = 0; i < 256; i++) {
        before = mrs_pct();
        value = readq(FRAME);
        after = mrs_pct();
        if (value < before || value > after) {
            finish(2);
        }
        before = mrs_pct();
        value = readq(FRAME + 8) + frame_offset;
        after = mrs_pct();
        if (value < before || value > after) {
            finish(3);
        }
        before = mrs_pct();
        value = mrs_vct() + cpu_offset;
        after = mrs_pct();
        if (value < before || value > after) {
            finish(4);
        }
        if (readq(FRAME + 0x18) != frame_offset) {
            finish(5);
        }
    }
    __asm__ volatile("msr cntvoff_el2, xzr; isb" ::: "memory");
#ifdef ICOUNT_RATE_TEST
    if (!cpu_timer_probe()) {
        finish(6);
    }
    report_rate();
#endif
    finish(0);
}
