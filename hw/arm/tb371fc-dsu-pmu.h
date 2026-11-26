/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_ARM_TB371FC_DSU_PMU_H
#define HW_ARM_TB371FC_DSU_PMU_H

#include "hw/core/sysbus.h"
#include "target/arm/cpu.h"

#define TYPE_TB371FC_DSU_PMU "tb371fc-dsu-pmu"
OBJECT_DECLARE_SIMPLE_TYPE(Tb371fcDsuPmuState, TB371FC_DSU_PMU)

/* Attach the board's one shared PMU to a realized CPU; no-op when disabled. */
void tb371fc_dsu_pmu_attach_cpu(DeviceState *dev, ARMCPU *cpu);

#endif
