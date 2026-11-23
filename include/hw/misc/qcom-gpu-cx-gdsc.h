/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_GPU_CX_GDSC_H
#define HW_MISC_QCOM_GPU_CX_GDSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_GPU_CX_GDSC "qcom-gpu-cx-gdsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomGpuCxGdscState, QCOM_GPU_CX_GDSC)

#define QCOM_GPU_CX_GDSC_WINDOW_SIZE 4

/*
 * GPU CX normal software/votable mode only.
 * MMIO0: GDSCR, mapped by the board at 0x03d9106c.
 * MMIO1: GDS_HW_CTRL status, mapped at 0x03d91540.
 * Both windows expose the same completed digital power state.
 *
 * Named one-line GPIO inputs:
 *   parent-known, parent-on: validity and modeled parent availability.
 *   external-known, external-vote: aggregate non-HLOS vote and validity.
 * Named one-line GPIO outputs:
 *   state-known, power-on: validity and completed digital power state.
 *
 * GPIOs are model integration signals, not measured PMIC voltage feedback.
 * A known-low parent does not make the control registers inaccessible.
 *
 * boot-collapsed-reference (default false) selects a known SW_COLLAPSE=1,
 * zero-config reset request. At realize, with no external inputs driven,
 * it also explicitly selects the reference no-external-vote environment.
 * It never assumes a parent-on value.
 *
 * transition-ns (default 1000) is a reference feedback delay, not silicon
 * timing. The reset profile and this delay must match on migration.
 */
#endif
