/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_ARM_CORESIGHT_TMC_H
#define HW_MISC_ARM_CORESIGHT_TMC_H

#include "hw/core/sysbus.h"

#define TYPE_ARM_CORESIGHT_TMC "arm-coresight-tmc"
OBJECT_DECLARE_SIMPLE_TYPE(ArmCoresightTmcState, ARM_CORESIGHT_TMC)

#define ARM_TMC_ETR 1
#define ARM_TMC_ETF 2

#endif
