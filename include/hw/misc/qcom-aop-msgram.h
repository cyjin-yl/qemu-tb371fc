/*
 * AOP message SRAM aperture used by the TB371FC Qualcomm mailbox transport.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_MISC_QCOM_AOP_MSGRAM_H
#define HW_MISC_QCOM_AOP_MSGRAM_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_AOP_MSGRAM "qcom-aop-msgram"
OBJECT_DECLARE_SIMPLE_TYPE(QcomAopMsgramState, QCOM_AOP_MSGRAM)

#define QCOM_AOP_MSGRAM_SIZE 0x1000

struct QcomAopMsgramState {
    SysBusDevice parent_obj;

    MemoryRegion sram;
    /* Fixed by the board before realization; not a writable QOM property. */
    uint32_t reference_peer;
};

#endif /* HW_MISC_QCOM_AOP_MSGRAM_H */
