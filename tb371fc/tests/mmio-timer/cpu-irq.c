/* SPDX-License-Identifier: GPL-2.0-or-later */
#include <stdint.h>

/* CPU0's physical/virtual timer PPIs are INTID 30 and INTID 27. */
#define GICR_ISPENDR0 0x17a70200UL

static uint64_t count(unsigned int virt)
{
    uint64_t value;

    if (virt) {
        __asm__ volatile("isb; mrs %0, cntvct_el0" : "=r"(value) :: "memory");
    } else {
        __asm__ volatile("isb; mrs %0, cntpct_el0" : "=r"(value) :: "memory");
    }
    return value;
}

static uint64_t control(unsigned int virt)
{
    uint64_t value;

    if (virt) {
        __asm__ volatile("isb; mrs %0, cntv_ctl_el0" : "=r"(value) :: "memory");
    } else {
        __asm__ volatile("isb; mrs %0, cntp_ctl_el0" : "=r"(value) :: "memory");
    }
    return value;
}

static void set_control(unsigned int virt, uint64_t value)
{
    if (virt) {
        __asm__ volatile("msr cntv_ctl_el0, %0; isb" :: "r"(value) : "memory");
    } else {
        __asm__ volatile("msr cntp_ctl_el0, %0; isb" :: "r"(value) : "memory");
    }
}

static void set_compare(unsigned int virt, uint64_t value)
{
    if (virt) {
        __asm__ volatile("msr cntv_cval_el0, %0; isb" :: "r"(value) : "memory");
    } else {
        __asm__ volatile("msr cntp_cval_el0, %0; isb" :: "r"(value) : "memory");
    }
}

static unsigned int pending(unsigned int virt)
{
    uint32_t value;

    __asm__ volatile("dsb sy" ::: "memory");
    value = *(volatile uint32_t *)GICR_ISPENDR0;
    return (value >> (virt ? 27 : 30)) & 1;
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
                      "TB371FC-CPU-TIMER-IRQ:PASS 16 line observations\n");
    semihost(0x20, block);
    for (;;) {
        __asm__ volatile("wfi");
    }
}

void probe_main(void)
{
    unsigned int virt;
    uint64_t deadline;

    for (virt = 0; virt < 2; virt++) {
        set_control(virt, 0);
        set_compare(virt, UINT64_MAX);
        if (pending(virt)) {
            finish(1 + 16 * virt);
        }

        /* Expire while masked, without a CTL read that can refresh state. */
        deadline = count(virt) + 192000;
        set_compare(virt, deadline);
        set_control(virt, 3);
        while (count(virt) <= deadline) {
            __asm__ volatile("nop");
        }
        if (pending(virt)) {
            finish(2 + 16 * virt);
        }

        /* Check the wire before reading CTL: writes must update it. */
        set_control(virt, 1);
        if (!pending(virt) || control(virt) != 5) {
            finish(3 + 16 * virt);
        }
        set_control(virt, 3);
        if (pending(virt) || control(virt) != 7) {
            finish(4 + 16 * virt);
        }
        set_control(virt, 1);
        if (!pending(virt) || control(virt) != 5) {
            finish(5 + 16 * virt);
        }
        set_control(virt, 0);
        if (pending(virt) || control(virt) != 0) {
            finish(6 + 16 * virt);
        }

        /* Enabling a past compare asserts; moving it forward deasserts. */
        set_control(virt, 1);
        if (!pending(virt) || control(virt) != 5) {
            finish(7 + 16 * virt);
        }
        set_compare(virt, UINT64_MAX);
        if (pending(virt) || control(virt) != 1) {
            finish(8 + 16 * virt);
        }
        set_control(virt, 0);
    }
    finish(0);
}
