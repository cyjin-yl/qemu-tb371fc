/* SPDX-License-Identifier: GPL-2.0-or-later */
#include "qemu/osdep.h"
#include "hw/timer/arm-gt-counter.h"

int main(void)
{
    uint64_t ns, tick, freq;
    unsigned int exact;

    while (scanf("%" SCNu64 " %" SCNu64 " %" SCNu64 " %u",
                 &ns, &tick, &freq, &exact) == 4) {
        if (!freq || freq > NANOSECONDS_PER_SECOND || exact > 1) {
            return 2;
        }
        printf("%" PRIu64 " %" PRId64 "\n",
               arm_gt_ns_to_ticks(ns, freq, exact),
               arm_gt_ticks_to_ns(tick, freq, exact));
    }
    return 0;
}
