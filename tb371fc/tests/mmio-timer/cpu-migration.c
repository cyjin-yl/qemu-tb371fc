/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdint.h>

struct Result {
    uint64_t magic;
    uint64_t before;
    uint64_t offset;
    uint64_t physical_compare;
    uint64_t virtual_compare;
    uint64_t resumed;
    uint64_t physical_expired;
    uint64_t virtual_expired;
    uint64_t passed;
};
volatile struct Result timer_migration_result = { .magic = 0x54494d45524d4947 };

#define READ(reg, value) __asm__ volatile("isb; mrs %0, " #reg : "=r"(value) :: "memory")
#define WRITE(reg, value) __asm__ volatile("msr " #reg ", %0; isb" :: "r"((uint64_t)(value)) : "memory")

static void semihost(uint64_t op, const void *data)
{
    register uint64_t x0 __asm__("x0") = op;
    register const void *x1 __asm__("x1") = data;
    __asm__ volatile("hlt #0xf000" : "+r"(x0) : "r"(x1) : "memory");
}

__attribute__((noinline, noclone, noipa, used))
void timer_migration_checkpoint(void)
{
    __asm__ volatile("" ::: "memory");
}

__attribute__((noinline, noclone, noipa, used))
void timer_migration_done(void)
{
    __asm__ volatile("" ::: "memory");
}

__attribute__((noinline, noclone, noipa))
static void finish(uint64_t code)
{
    uint64_t block[2] = { 0x20026, code };
    semihost(4, code ? "TB371FC-CPU-TIMER-MIGRATION:FAIL\n" :
                      "TB371FC-CPU-TIMER-MIGRATION:PASS\n");
    semihost(0x20, block);
    for (;;) {
        __asm__ volatile("wfi");
    }
}

void probe_main(void)
{
    uint64_t v, p, control, after, saved;
    unsigned int i;

    READ(CurrentEL, v);
    if (v != 8) {
        finish(1);
    }
    READ(cntpct_el0, p);
    timer_migration_result.before = p;
    timer_migration_result.offset = p / 2;
    WRITE(cntvoff_el2, p / 2);
    timer_migration_result.physical_compare = p + 1920000;
    timer_migration_result.virtual_compare = p - p / 2 + 2400000;
    WRITE(cntp_cval_el0, timer_migration_result.physical_compare);
    WRITE(cntv_cval_el0, timer_migration_result.virtual_compare);
    WRITE(cntp_ctl_el0, 1);
    WRITE(cntv_ctl_el0, 1);
    timer_migration_checkpoint();

    /*
     * Give the host an ordinary running interval in which to request a
     * supported PAUSED migration state after observing the debugger stop.
     * No host write or injected branch is needed to leave this interval.
     */
    do {
        READ(cntpct_el0, p);
    } while (p < timer_migration_result.before + 960000);

    READ(cntpct_el0, p);
    timer_migration_result.resumed = p;
    READ(cntp_cval_el0, saved);
    if (p < timer_migration_result.before ||
        p >= timer_migration_result.physical_compare ||
        saved != timer_migration_result.physical_compare) {
        finish(2);
    }
    READ(cntv_cval_el0, saved);
    if (saved != timer_migration_result.virtual_compare) {
        finish(3);
    }
    READ(cntvoff_el2, saved);
    if (saved != timer_migration_result.offset) {
        finish(4);
    }
    READ(cntp_ctl_el0, control);
    if (control != 1) {
        finish(5);
    }
    READ(cntv_ctl_el0, control);
    if (control != 1) {
        finish(6);
    }
    for (i = 0; i < 100000000; i++) {
        READ(cntpct_el0, p);
        READ(cntp_ctl_el0, control);
        READ(cntpct_el0, after);
        if (control & 4) {
            if (after < timer_migration_result.physical_compare) {
                finish(7);
            }
            timer_migration_result.physical_expired = after;
            break;
        }
        if (p > timer_migration_result.physical_compare) {
            finish(8);
        }
    }
    if (!timer_migration_result.physical_expired) {
        finish(9);
    }
    for (i = 0; i < 100000000; i++) {
        READ(cntvct_el0, v);
        READ(cntv_ctl_el0, control);
        READ(cntvct_el0, after);
        if (control & 4) {
            if (after < timer_migration_result.virtual_compare) {
                finish(10);
            }
            timer_migration_result.virtual_expired = after;
            break;
        }
        if (v > timer_migration_result.virtual_compare) {
            finish(11);
        }
    }
    if (!timer_migration_result.virtual_expired) {
        finish(12);
    }
    WRITE(cntp_ctl_el0, 0);
    WRITE(cntv_ctl_el0, 0);
    timer_migration_result.passed = 1;
    timer_migration_done();
    finish(0);
}
