/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_NOC_QOS_H
#define HW_MISC_QCOM_NOC_QOS_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_NOC_QOS "qcom-noc-qos"
OBJECT_DECLARE_SIMPLE_TYPE(QcomNocQosState, QCOM_NOC_QOS)

#define QCOM_NOC_QOS_REGISTERS 4
extern const uint32_t qcom_noc_qos_offsets[QCOM_NOC_QOS_REGISTERS];

typedef struct QcomNocQosWindow {
    QcomNocQosState *state;
    unsigned reg;
} QcomNocQosWindow;

struct QcomNocQosState {
    SysBusDevice parent_obj;
    MemoryRegion mmio[QCOM_NOC_QOS_REGISTERS];
    QcomNocQosWindow windows[QCOM_NOC_QOS_REGISTERS];
    bool enabled_reference;
    uint32_t reference_profile;
    /* Immutable board identity, assigned before realization; not a setter. */
    uint64_t port_address;
    uint32_t regs[QCOM_NOC_QOS_REGISTERS];
};

#endif
