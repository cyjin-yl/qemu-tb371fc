/* SPDX-License-Identifier: GPL-2.0-or-later */
#ifndef HW_MISC_QCOM_GDSC_H
#define HW_MISC_QCOM_GDSC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_GDSC "qcom-gdsc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomGdscState, QCOM_GDSC)

enum {
    QCOM_GDSC_CONTROL,
    QCOM_GDSC_HW_STATUS,
    QCOM_GDSC_DOMAIN_CONTROL,
    QCOM_GDSC_SW_RESET,
    QCOM_GDSC_NUM_REGIONS,
};

#define QCOM_GDSC_REGISTER_SIZE 4

/*
 * Four independent optional register windows; map only captured addresses.
 * CONTROL: RW bits 0, 1, 11, 12..15; read-only digital power-on at bit 31.
 * HW_STATUS: read-only bit 31, the same digital state as CONTROL.
 * DOMAIN_CONTROL: RW clamp bit 0 and reset bit 4.
 * SW_RESET: RW reset bit 0.
 *
 * "hw-enable" and "external-vote" are one-bit reference GPIO inputs.
 * "parent-known" / "parent-on" carry modeled upstream availability.
 * "state-known" validates "power-on"; unknown feedback reads are rejected.
 * "power-on", "clamp-io", "reset-asserted" are one-bit digital outputs.
 * "enabled-reference" explicitly enables the reference register protocol.
 * "reference-on" selects a documented reset assumption, not a silicon dump.
 * Neither profile asserts a parent input. Without a parent, requested power
 * remains unknown; an absent request resolves to known off in reference mode.
 * VMState v2 includes the parent; old streams without it are rejected.
 * This interface does not execute GPU/GMU/TrustZone firmware or model rails.
 */
#endif
