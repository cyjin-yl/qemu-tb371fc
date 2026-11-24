/*
 * Qualcomm TSENS24xx finite digital reference.
 *
 * Register consumers: OnePlusOSS/android_kernel_oneplus_sm8250
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae, drivers/thermal/tsens2xxx.c
 * and tsens.h. SW_RST and writable critical thresholds have additional
 * upstream Linux v6.12.114 tsens-v2.c/tsens.c evidence.
 *
 * Explicit host temperature inputs are not silicon calibration or firmware.
 * Sampling timing, reset defaults and held-clear behavior are reference policy.
 * Watchdog/cycle-monitor producers, analog calibration, SCM, debug and 0C/min
 * temperature paths are not implemented. See tb371fc/docs/tsens24xx-reference.md.
 *
 * Copyright (c) 2026
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "hw/misc/qcom-tsens24xx.h"
#include "migration/vmstate.h"
#include "qemu/bitops.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define SENSOR_COUNT 16
#define SENSOR_MASK UINT32_C(0xffff)
#define SAMPLE_PERIOD_NS UINT32_C(1000000)
#define CTRL_EN BIT(0)
#define CTRL_RESET BIT(1)
#define CTRL_MASK UINT32_C(0x0007fffb)
#define CRIT_MASK UINT32_C(0xc000ffff)
#define TEMP_MASK UINT32_C(0xfff)
#define THRESHOLD_MASK UINT32_C(0xffffff)
#define RESET_THRESHOLD UINT32_C(0x7ff800)
#define RESET_CRITICAL UINT32_C(0x7ff)
#define UNKNOWN_TEMPERATURE INT32_MIN
#define TM_SIZE 0x1ff

enum WindowKind {
    W_SROT,
    W_TM,
    W_TRDY,
    WINDOW_COUNT,
};

typedef struct TsensWindow {
    MemoryRegion mr;
    QcomTsens24xxState *s;
    enum WindowKind kind;
    hwaddr offset;
} TsensWindow;

struct QcomTsens24xxState {
    SysBusDevice parent_obj;
    MemoryRegion tm_container;
    TsensWindow window[WINDOW_COUNT];
    QEMUTimer *timer;
    qemu_irq irq[2];
    bool ready;
    bool enabled_reference;
    uint32_t reference_profile;
    uint32_t controller_id;
    uint64_t srot_base;
    uint64_t tm_base;
    uint32_t sampling_period_ns;
    uint32_t reference_boot_enable_mask;
    int32_t reference_temperature_mc;
    uint32_t reference_boot_temperature;

    uint32_t input_valid;
    int32_t input_mc[SENSOR_COUNT];
    uint32_t control;
    uint32_t interrupt_enable;
    uint32_t ul_status;
    uint32_t ul_clear;
    uint32_t ul_mask;
    uint32_t critical_status;
    uint32_t critical_clear;
    uint32_t critical_mask;
    uint32_t threshold[SENSOR_COUNT];
    uint32_t critical_threshold[SENSOR_COUNT];
    uint16_t last_code[SENSOR_COUNT];
    uint32_t valid_mask;
    bool first_round_complete;
    bool sample_pending;
    bool deadline_exhausted;
    int64_t sample_deadline_ns;
    uint32_t captured_enable_mask;
    uint32_t captured_valid_mask;
    uint16_t captured_code[SENSOR_COUNT];
    uint64_t sample_rounds;
    uint64_t complete_rounds;
    int64_t last_sample_ns;
};

static uint32_t active_sensors(const QcomTsens24xxState *s)
{
    if (!s->enabled_reference || !(s->control & CTRL_EN) ||
        (s->control & CTRL_RESET)) {
        return 0;
    }
    return (s->control >> 3) & SENSOR_MASK;
}

static int32_t signed_code(uint32_t value)
{
    value &= TEMP_MASK;
    return value & 0x800 ? (int32_t)value - 0x1000 : value;
}

static bool temperature_valid(int32_t mc)
{
    return mc == UNKNOWN_TEMPERATURE ||
           (mc >= -204800 && mc <= 204700 && mc % 100 == 0);
}

static uint32_t sensor_status(const QcomTsens24xxState *s, unsigned i)
{
    uint32_t value = s->last_code[i];

    if (s->valid_mask & BIT(i)) {
        value |= BIT(21);
    }
    if (s->ul_status & BIT(i)) {
        value |= BIT(17);
    }
    if (s->ul_status & BIT(i + 16)) {
        value |= BIT(18);
    }
    if (s->critical_status & BIT(i)) {
        value |= BIT(19);
    }
    return value;
}

static bool irq_level(const QcomTsens24xxState *s, unsigned n)
{
    uint32_t active = active_sensors(s);
    uint32_t pending;

    if (n == 1) {
        return (s->interrupt_enable & BIT(2)) &&
               (s->critical_status & ~s->critical_mask & active);
    }
    pending = s->ul_status & ~s->ul_mask;
    return ((s->interrupt_enable & BIT(0)) && (pending & active)) ||
           ((s->interrupt_enable & BIT(1)) && ((pending >> 16) & active));
}

static void publish_irqs(QcomTsens24xxState *s)
{
    if (s->ready) {
        qemu_set_irq(s->irq[0], irq_level(s, 0));
        qemu_set_irq(s->irq[1], irq_level(s, 1));
    }
}

static void cancel_sample(QcomTsens24xxState *s)
{
    timer_del(s->timer);
    s->sample_pending = false;
    s->sample_deadline_ns = 0;
    s->captured_enable_mask = 0;
    s->captured_valid_mask = 0;
    memset(s->captured_code, 0, sizeof(s->captured_code));
}

static void arm_sample(QcomTsens24xxState *s, int64_t now)
{
    uint32_t active = active_sensors(s);
    unsigned i;

    if (!active || !s->ready) {
        return;
    }
    if (now < 0 || now > INT64_MAX - s->sampling_period_ns) {
        s->deadline_exhausted = true;
        return;
    }
    s->deadline_exhausted = false;
    s->captured_enable_mask = active;
    s->captured_valid_mask = s->input_valid & active;
    for (i = 0; i < SENSOR_COUNT; i++) {
        s->captured_code[i] = s->captured_valid_mask & BIT(i) ?
                             (s->input_mc[i] / 100) & TEMP_MASK : 0;
    }
    s->sample_deadline_ns = now + s->sampling_period_ns;
    s->sample_pending = true;
    timer_mod_ns(s->timer, s->sample_deadline_ns);
}

static void restart_sample(QcomTsens24xxState *s)
{
    cancel_sample(s);
    s->deadline_exhausted = false;
    s->valid_mask = 0;
    s->first_round_complete = false;
    arm_sample(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
}

static void tsens_sample(void *opaque)
{
    QcomTsens24xxState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (!s->ready || !s->sample_pending || now < s->sample_deadline_ns) {
        return;
    }

    s->valid_mask = s->captured_valid_mask;
    for (i = 0; i < SENSOR_COUNT; i++) {
        int32_t sample;

        if (!(s->captured_valid_mask & BIT(i))) {
            continue;
        }
        s->last_code[i] = s->captured_code[i];
        sample = signed_code(s->last_code[i]);
        if (sample <= signed_code(s->threshold[i])) {
            s->ul_status |= BIT(i);
        }
        if (sample >= signed_code(s->threshold[i] >> 12)) {
            s->ul_status |= BIT(i + 16);
        }
        if (sample >= signed_code(s->critical_threshold[i])) {
            s->critical_status |= BIT(i);
        }
    }
    s->ul_status &= ~s->ul_clear;
    s->critical_status &= ~s->critical_clear;
    if (s->captured_valid_mask == s->captured_enable_mask) {
        s->first_round_complete = true;
        if (s->complete_rounds != UINT64_MAX) {
            s->complete_rounds++;
        }
    }
    if (s->sample_rounds != UINT64_MAX) {
        s->sample_rounds++;
    }
    s->last_sample_ns = now;
    cancel_sample(s);

    /*
     * A late callback commits its one captured round once. It cannot reconstruct
     * historical external inputs. Start the next round at actual callback time,
     * instead of inventing intermediate samples or catching up in a loop.
     */
    arm_sample(s, now);
    publish_irqs(s);
}

static void reset_registers(QcomTsens24xxState *s, uint32_t control)
{
    unsigned i;

    cancel_sample(s);
    s->control = control;
    s->interrupt_enable = 0;
    s->ul_status = 0;
    s->ul_clear = 0;
    s->ul_mask = UINT32_MAX;
    s->critical_status = 0;
    s->critical_clear = 0;
    s->critical_mask = CRIT_MASK;
    s->valid_mask = 0;
    s->first_round_complete = false;
    s->deadline_exhausted = false;
    s->sample_rounds = 0;
    s->complete_rounds = 0;
    s->last_sample_ns = -1;
    for (i = 0; i < SENSOR_COUNT; i++) {
        s->threshold[i] = RESET_THRESHOLD;
        s->critical_threshold[i] = RESET_CRITICAL;
        s->last_code[i] = 0;
    }
}

static void tsens_reset_enter(Object *obj, ResetType type)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(obj);
    uint32_t boot = s->enabled_reference && s->reference_boot_enable_mask ?
                    CTRL_EN | (s->reference_boot_enable_mask << 3) : 0;

    s->ready = false;
    reset_registers(s, boot);
}

static void tsens_reset_hold(Object *obj, ResetType type)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(obj);

    s->ready = true;
    arm_sample(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    publish_irqs(s);
}

static MemTxResult access_error(TsensWindow *w, bool write, hwaddr offset,
                                unsigned size, const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  TYPE_QCOM_TSENS24XX ": %s controller %u %s +0x%"
                  HWADDR_PRIx " size %u: %s\n",
                  write ? "write" : "read", w->s->controller_id,
                  w->kind == W_SROT ? "srot" : "tm",
                  w->offset + offset, size, reason);
    return MEMTX_ERROR;
}

static MemTxResult tsens_read(void *opaque, hwaddr offset, uint64_t *value,
                              unsigned size, MemTxAttrs attrs)
{
    TsensWindow *w = opaque;
    QcomTsens24xxState *s = w->s;
    hwaddr reg = w->offset + offset;

    *value = 0;
    if (!s->enabled_reference) {
        return access_error(w, false, offset, size, "unknown profile");
    }
    if (w->kind == W_SROT) {
        *value = s->control;
        return MEMTX_OK;
    }
    switch (reg) {
    case 0x04: *value = s->interrupt_enable; break;
    case 0x08: *value = s->ul_status; break;
    case 0x0c: *value = s->ul_clear; break;
    case 0x10: *value = s->ul_mask; break;
    case 0x14: *value = s->critical_status; break;
    case 0x18: *value = s->critical_clear; break;
    case 0x1c: *value = s->critical_mask; break;
    case 0xe4: *value = s->first_round_complete ? BIT(3) : 0; break;
    default:
        if (reg >= 0x20 && reg < 0x60) {
            *value = s->threshold[(reg - 0x20) / 4];
        } else if (reg >= 0x60 && reg < 0xa0) {
            *value = s->critical_threshold[(reg - 0x60) / 4];
        } else if (reg >= 0xa0 && reg < 0xe0) {
            *value = sensor_status(s, (reg - 0xa0) / 4);
        } else {
            return access_error(w, false, offset, size, "unsupported register");
        }
    }
    return MEMTX_OK;
}

static MemTxResult tsens_write(void *opaque, hwaddr offset, uint64_t raw,
                               unsigned size, MemTxAttrs attrs)
{
    TsensWindow *w = opaque;
    QcomTsens24xxState *s = w->s;
    hwaddr reg = w->offset + offset;
    uint32_t value = raw, mask;
    uint32_t *destination;
    int64_t now;

    if (!s->enabled_reference) {
        return access_error(w, true, offset, size, "unknown profile");
    }
    if (w->kind == W_SROT) {
        if (value & ~CTRL_MASK) {
            return access_error(w, true, offset, size, "unsupported bits");
        }
        now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        if ((value & CTRL_EN) && !(value & CTRL_RESET) &&
            ((value >> 3) & SENSOR_MASK) && value != s->control &&
            (now < 0 || now > INT64_MAX - s->sampling_period_ns)) {
            return access_error(w, true, offset, size,
                                "virtual deadline overflow");
        }
        if (value & CTRL_RESET) {
            reset_registers(s, value);
        } else if (value != s->control) {
            s->control = value;
            restart_sample(s);
        }
        publish_irqs(s);
        return MEMTX_OK;
    }

    switch (reg) {
    case 0x04:
        destination = &s->interrupt_enable;
        mask = 7;
        break;
    case 0x0c:
        destination = &s->ul_clear;
        mask = UINT32_MAX;
        break;
    case 0x10:
        destination = &s->ul_mask;
        mask = UINT32_MAX;
        break;
    case 0x18:
        destination = &s->critical_clear;
        mask = CRIT_MASK;
        break;
    case 0x1c:
        destination = &s->critical_mask;
        mask = CRIT_MASK;
        break;
    case 0x08:
    case 0x14:
    case 0xe4:
        return access_error(w, true, offset, size, "read-only register");
    default:
        if (reg >= 0x20 && reg < 0x60) {
            destination = &s->threshold[(reg - 0x20) / 4];
            mask = THRESHOLD_MASK;
        } else if (reg >= 0x60 && reg < 0xa0) {
            destination = &s->critical_threshold[(reg - 0x60) / 4];
            mask = TEMP_MASK;
        } else if (reg >= 0xa0 && reg < 0xe0) {
            return access_error(w, true, offset, size, "read-only register");
        } else {
            return access_error(w, true, offset, size, "unsupported register");
        }
    }
    if (value & ~mask) {
        return access_error(w, true, offset, size, "unsupported bits");
    }
    *destination = value;
    s->ul_status &= ~s->ul_clear;
    s->critical_status &= ~s->critical_clear;
    publish_irqs(s);
    return MEMTX_OK;
}

static const MemoryRegionOps tsens_ops = {
    .read_with_attrs = tsens_read,
    .write_with_attrs = tsens_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4, .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static int tsens_pre_load(void *opaque)
{
    QcomTsens24xxState *s = opaque;

    s->ready = false;
    timer_del(s->timer);
    return 0;
}

static bool global_state_valid(const QcomTsens24xxState *s)
{
    uint32_t active = active_sensors(s);

    if ((s->input_valid & ~SENSOR_MASK) || (s->control & ~CTRL_MASK) ||
        (s->interrupt_enable & ~7) || (s->critical_status & ~SENSOR_MASK) ||
        (s->critical_clear & ~CRIT_MASK) || (s->critical_mask & ~CRIT_MASK) ||
        (s->ul_status & s->ul_clear) ||
        (s->critical_status & s->critical_clear) ||
        (s->valid_mask & ~active) ||
        s->complete_rounds > s->sample_rounds ||
        (!s->sample_rounds && (s->last_sample_ns != -1 || s->valid_mask ||
                              s->ul_status || s->critical_status)) ||
        ((s->control & CTRL_RESET) && s->sample_rounds) ||
        (s->sample_rounds && s->last_sample_ns < 0) ||
        (s->first_round_complete && (!s->complete_rounds || !active))) {
        return false;
    }
    if (s->sample_pending) {
        if (!active || s->deadline_exhausted || s->sample_deadline_ns <= 0 ||
            s->captured_enable_mask != active ||
            (s->captured_valid_mask & ~active) ||
            (s->sample_rounds &&
             s->sample_deadline_ns - s->last_sample_ns <
             s->sampling_period_ns)) {
            return false;
        }
    } else if (s->sample_deadline_ns || s->captured_enable_mask ||
               s->captured_valid_mask || (!!active != s->deadline_exhausted)) {
        return false;
    }
    if (!s->enabled_reference &&
        (s->input_valid || s->control || s->interrupt_enable || s->ul_status ||
         s->ul_clear || s->ul_mask != UINT32_MAX || s->critical_status ||
         s->critical_clear || s->critical_mask != CRIT_MASK ||
         s->first_round_complete || s->sample_rounds || s->complete_rounds)) {
        return false;
    }
    return true;
}

static int tsens_post_load(void *opaque, int version_id)
{
    QcomTsens24xxState *s = opaque;
    unsigned i;

    if (!global_state_valid(s)) {
        error_report(TYPE_QCOM_TSENS24XX ": invalid migrated TSENS state");
        return -EINVAL;
    }
    for (i = 0; i < SENSOR_COUNT; i++) {
        if (!temperature_valid(s->input_mc[i]) ||
            ((s->input_valid & BIT(i)) &&
             s->input_mc[i] == UNKNOWN_TEMPERATURE) ||
            (s->threshold[i] & ~THRESHOLD_MASK) ||
            (s->critical_threshold[i] & ~TEMP_MASK) ||
            (s->last_code[i] & ~TEMP_MASK) ||
            (s->captured_code[i] & ~TEMP_MASK) ||
            (!(s->captured_valid_mask & BIT(i)) && s->captured_code[i]) ||
            (!s->sample_rounds && s->last_code[i]) ||
            (!s->enabled_reference &&
             (s->input_mc[i] != UNKNOWN_TEMPERATURE ||
              s->threshold[i] != RESET_THRESHOLD ||
              s->critical_threshold[i] != RESET_CRITICAL))) {
            error_report(TYPE_QCOM_TSENS24XX
                          ": invalid migrated sensor %u state", i);
            return -EINVAL;
        }
    }

    /*
     * Inputs and the already-captured round are distinct migratable state.
     * Do not sample, advance time, recapture inputs or postpone a deadline here.
     * Drive both real IRQs before the destination's first device MMIO.
     */
    s->ready = true;
    publish_irqs(s);
    if (s->sample_pending) {
        timer_mod_ns(s->timer, s->sample_deadline_ns);
    }
    return 0;
}

static const VMStateDescription vmstate_tsens = {
    .name = TYPE_QCOM_TSENS24XX,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = tsens_pre_load,
    .post_load = tsens_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomTsens24xxState),
        VMSTATE_UINT32_EQUAL(controller_id, QcomTsens24xxState),
        VMSTATE_UINT64_EQUAL(srot_base, QcomTsens24xxState),
        VMSTATE_UINT64_EQUAL(tm_base, QcomTsens24xxState),
        VMSTATE_UINT32_EQUAL(sampling_period_ns, QcomTsens24xxState),
        VMSTATE_UINT32_EQUAL(reference_boot_enable_mask, QcomTsens24xxState),
        VMSTATE_UINT32_EQUAL(reference_boot_temperature, QcomTsens24xxState),
        VMSTATE_UINT32(input_valid, QcomTsens24xxState),
        VMSTATE_INT32_ARRAY(input_mc, QcomTsens24xxState, SENSOR_COUNT),
        VMSTATE_UINT32(control, QcomTsens24xxState),
        VMSTATE_UINT32(interrupt_enable, QcomTsens24xxState),
        VMSTATE_UINT32(ul_status, QcomTsens24xxState),
        VMSTATE_UINT32(ul_clear, QcomTsens24xxState),
        VMSTATE_UINT32(ul_mask, QcomTsens24xxState),
        VMSTATE_UINT32(critical_status, QcomTsens24xxState),
        VMSTATE_UINT32(critical_clear, QcomTsens24xxState),
        VMSTATE_UINT32(critical_mask, QcomTsens24xxState),
        VMSTATE_UINT32_ARRAY(threshold, QcomTsens24xxState, SENSOR_COUNT),
        VMSTATE_UINT32_ARRAY(critical_threshold, QcomTsens24xxState, SENSOR_COUNT),
        VMSTATE_UINT16_ARRAY(last_code, QcomTsens24xxState, SENSOR_COUNT),
        VMSTATE_UINT32(valid_mask, QcomTsens24xxState),
        VMSTATE_BOOL(first_round_complete, QcomTsens24xxState),
        VMSTATE_BOOL(sample_pending, QcomTsens24xxState),
        VMSTATE_BOOL(deadline_exhausted, QcomTsens24xxState),
        VMSTATE_INT64(sample_deadline_ns, QcomTsens24xxState),
        VMSTATE_UINT32(captured_enable_mask, QcomTsens24xxState),
        VMSTATE_UINT32(captured_valid_mask, QcomTsens24xxState),
        VMSTATE_UINT16_ARRAY(captured_code, QcomTsens24xxState, SENSOR_COUNT),
        VMSTATE_UINT64(sample_rounds, QcomTsens24xxState),
        VMSTATE_UINT64(complete_rounds, QcomTsens24xxState),
        VMSTATE_INT64(last_sample_ns, QcomTsens24xxState),
        VMSTATE_END_OF_LIST()
    },
};

enum Observation {
    O_SROT_BASE = 1, O_TM_BASE, O_SENSOR_COUNT, O_PERIOD,
    O_CONTROL, O_INTERRUPT_ENABLE, O_UL_STATUS, O_UL_CLEAR, O_UL_MASK,
    O_CRIT_STATUS, O_CRIT_CLEAR, O_CRIT_MASK, O_VALID_MASK, O_TRDY,
    O_CAPTURED_ENABLE, O_CAPTURED_VALID, O_PENDING, O_EXHAUSTED,
    O_UL_IRQ, O_CRIT_IRQ, O_DEADLINE, O_LAST_SAMPLE, O_ROUNDS, O_COMPLETE,
    O_THRESHOLD, O_CRIT_THRESHOLD, O_STATUS, O_LAST_CODE, O_CAPTURED_CODE,
    O_LAST_MC, O_VALID, O_INPUT_MC, O_INPUT_VALID,
};

static void tsens_get(Object *obj, Visitor *v, const char *name,
                       void *opaque, Error **errp)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(obj);
    unsigned encoded = GPOINTER_TO_UINT(opaque);
    unsigned field = encoded >> 8, i = encoded & 0xff;
    uint64_t u64 = 0;
    uint32_t u32 = 0;
    int64_t i64 = 0;
    int32_t i32 = 0;
    bool boolean = false;

    if (!s->enabled_reference && field >= O_CONTROL && field <= O_VALID) {
        error_setg(errp, TYPE_QCOM_TSENS24XX ": unknown profile");
        return;
    }
    switch (field) {
    case O_SROT_BASE: u64 = s->srot_base; break;
    case O_TM_BASE: u64 = s->tm_base; break;
    case O_ROUNDS: u64 = s->sample_rounds; break;
    case O_COMPLETE: u64 = s->complete_rounds; break;
    case O_SENSOR_COUNT: u32 = SENSOR_COUNT; goto uint32;
    case O_PERIOD: u32 = s->sampling_period_ns; goto uint32;
    case O_CONTROL: u32 = s->control; goto uint32;
    case O_INTERRUPT_ENABLE: u32 = s->interrupt_enable; goto uint32;
    case O_UL_STATUS: u32 = s->ul_status; goto uint32;
    case O_UL_CLEAR: u32 = s->ul_clear; goto uint32;
    case O_UL_MASK: u32 = s->ul_mask; goto uint32;
    case O_CRIT_STATUS: u32 = s->critical_status; goto uint32;
    case O_CRIT_CLEAR: u32 = s->critical_clear; goto uint32;
    case O_CRIT_MASK: u32 = s->critical_mask; goto uint32;
    case O_VALID_MASK: u32 = s->valid_mask; goto uint32;
    case O_TRDY: u32 = s->first_round_complete ? BIT(3) : 0; goto uint32;
    case O_CAPTURED_ENABLE: u32 = s->captured_enable_mask; goto uint32;
    case O_CAPTURED_VALID: u32 = s->captured_valid_mask; goto uint32;
    case O_THRESHOLD: u32 = s->threshold[i]; goto uint32;
    case O_CRIT_THRESHOLD: u32 = s->critical_threshold[i]; goto uint32;
    case O_STATUS: u32 = sensor_status(s, i); goto uint32;
    case O_LAST_CODE: u32 = s->last_code[i]; goto uint32;
    case O_CAPTURED_CODE: u32 = s->captured_code[i]; goto uint32;
    case O_DEADLINE: i64 = s->sample_deadline_ns; goto int64;
    case O_LAST_SAMPLE: i64 = s->last_sample_ns; goto int64;
    case O_PENDING: boolean = s->sample_pending; goto boolean;
    case O_EXHAUSTED: boolean = s->deadline_exhausted; goto boolean;
    case O_UL_IRQ: boolean = irq_level(s, 0); goto boolean;
    case O_CRIT_IRQ: boolean = irq_level(s, 1); goto boolean;
    case O_VALID: boolean = s->valid_mask & BIT(i); goto boolean;
    case O_INPUT_VALID: boolean = s->input_valid & BIT(i); goto boolean;
    case O_INPUT_MC: i32 = s->input_mc[i]; goto int32;
    case O_LAST_MC: i32 = signed_code(s->last_code[i]) * 100; goto int32;
    default: g_assert_not_reached();
    }
    visit_type_uint64(v, name, &u64, errp);
    return;
uint32:
    visit_type_uint32(v, name, &u32, errp);
    return;
int64:
    visit_type_int64(v, name, &i64, errp);
    return;
int32:
    visit_type_int32(v, name, &i32, errp);
    return;
boolean:
    visit_type_bool(v, name, &boolean, errp);
}

static void tsens_set_input(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(obj);
    unsigned encoded = GPOINTER_TO_UINT(opaque);
    unsigned field = encoded >> 8, i = encoded & 0xff;
    int32_t mc;
    bool valid;

    if (!s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_TSENS24XX ": unknown profile");
        return;
    }
    if (field == O_INPUT_MC) {
        if (!visit_type_int32(v, name, &mc, errp)) {
            return;
        }
        if (!temperature_valid(mc)) {
            error_setg(errp, TYPE_QCOM_TSENS24XX
                       ": temperature must be INT32_MIN (unknown) or"
                       " -204800..204700 millicelsius in steps of 100");
            return;
        }
        s->input_mc[i] = mc;
        valid = mc != UNKNOWN_TEMPERATURE;
    } else {
        if (!visit_type_bool(v, name, &valid, errp)) {
            return;
        }
        if (valid && s->input_mc[i] == UNKNOWN_TEMPERATURE) {
            error_setg(errp, TYPE_QCOM_TSENS24XX
                       ": a known temperature is required before input-valid");
            return;
        }
    }
    if (valid) {
        s->input_valid |= BIT(i);
    } else {
        s->input_valid &= ~BIT(i);
    }
    /* Host environment changes never modify an already captured round. */
}

typedef struct ObservationInfo {
    const char *name;
    const char *type;
    enum Observation field;
} ObservationInfo;

static const ObservationInfo controller_observations[] = {
    { "srot-base", "uint64", O_SROT_BASE },
    { "tm-base", "uint64", O_TM_BASE },
    { "sensor-count", "uint32", O_SENSOR_COUNT },
    { "sampling-period-ns", "uint32", O_PERIOD },
    { "control", "uint32", O_CONTROL },
    { "interrupt-enable", "uint32", O_INTERRUPT_ENABLE },
    { "upper-lower-status", "uint32", O_UL_STATUS },
    { "upper-lower-clear", "uint32", O_UL_CLEAR },
    { "upper-lower-mask", "uint32", O_UL_MASK },
    { "critical-status", "uint32", O_CRIT_STATUS },
    { "critical-clear", "uint32", O_CRIT_CLEAR },
    { "critical-mask", "uint32", O_CRIT_MASK },
    { "valid-mask", "uint32", O_VALID_MASK },
    { "trdy", "uint32", O_TRDY },
    { "captured-enable-mask", "uint32", O_CAPTURED_ENABLE },
    { "captured-valid-mask", "uint32", O_CAPTURED_VALID },
    { "sample-pending", "bool", O_PENDING },
    { "deadline-exhausted", "bool", O_EXHAUSTED },
    { "upper-lower-irq", "bool", O_UL_IRQ },
    { "critical-irq", "bool", O_CRIT_IRQ },
    { "sample-deadline-ns", "int64", O_DEADLINE },
    { "last-sample-ns", "int64", O_LAST_SAMPLE },
    { "sample-rounds", "uint64", O_ROUNDS },
    { "complete-rounds", "uint64", O_COMPLETE },
};

static const ObservationInfo sensor_observations[] = {
    { "threshold", "uint32", O_THRESHOLD },
    { "critical-threshold", "uint32", O_CRIT_THRESHOLD },
    { "status", "uint32", O_STATUS },
    { "last-code", "uint32", O_LAST_CODE },
    { "captured-code", "uint32", O_CAPTURED_CODE },
    { "last-millicelsius", "int32", O_LAST_MC },
    { "valid", "bool", O_VALID },
    { "input-millicelsius", "int32", O_INPUT_MC },
    { "input-valid", "bool", O_INPUT_VALID },
};

static void add_observation(Object *obj, const char *name,
                              const ObservationInfo *o, unsigned sensor)
{
    object_property_add(obj, name, o->type, tsens_get,
                        o->field == O_INPUT_MC || o->field == O_INPUT_VALID ?
                        tsens_set_input : NULL, NULL,
                        GUINT_TO_POINTER((o->field << 8) | sensor));
}

static void tsens_realize(DeviceState *dev, Error **errp)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(dev);
    unsigned i;

    if (s->controller_id > 1) {
        error_setg(errp, TYPE_QCOM_TSENS24XX ": controller-id must be 0 or 1");
        return;
    }
    if (s->reference_boot_enable_mask & ~SENSOR_MASK) {
        error_setg(errp, TYPE_QCOM_TSENS24XX
                   ": reference-boot-enable-mask must fit 16 sensors");
        return;
    }
    if (!temperature_valid(s->reference_temperature_mc)) {
        error_setg(errp, TYPE_QCOM_TSENS24XX
                   ": reference-temperature-millicelsius must be INT32_MIN"
                   " (unknown) or -204800..204700 in steps of 100");
        return;
    }
    if (!s->enabled_reference &&
        (s->reference_boot_enable_mask ||
         s->reference_temperature_mc != UNKNOWN_TEMPERATURE)) {
        error_setg(errp, TYPE_QCOM_TSENS24XX
                   ": reference boot state requires enabled-reference");
        return;
    }
    s->reference_profile = s->enabled_reference ? 1 : 0;
    s->reference_boot_temperature = (uint32_t)s->reference_temperature_mc;
    s->srot_base = UINT64_C(0x0c222000) + s->controller_id * 0x1000;
    s->tm_base = UINT64_C(0x0c263000) + s->controller_id * 0x2000;
    s->input_valid = s->reference_temperature_mc == UNKNOWN_TEMPERATURE ?
                     0 : SENSOR_MASK;
    for (i = 0; i < SENSOR_COUNT; i++) {
        s->input_mc[i] = s->reference_temperature_mc;
    }
    tsens_reset_enter(OBJECT(s), RESET_TYPE_COLD);
    tsens_reset_hold(OBJECT(s), RESET_TYPE_COLD);
}

static void tsens_unrealize(DeviceState *dev)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(dev);

    s->ready = false;
    timer_del(s->timer);
}

static void init_window(QcomTsens24xxState *s, enum WindowKind kind,
                         const char *name, hwaddr offset, unsigned size)
{
    TsensWindow *w = &s->window[kind];

    w->s = s;
    w->kind = kind;
    w->offset = offset;
    memory_region_init_io(&w->mr, OBJECT(s), &tsens_ops, w, name, size);
}

static void tsens_init(Object *obj)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(obj);
    unsigned i, j;

    s->sampling_period_ns = SAMPLE_PERIOD_NS;
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, tsens_sample, s);
    memory_region_init(&s->tm_container, obj,
                       TYPE_QCOM_TSENS24XX "-tm-sparse", TM_SIZE);
    init_window(s, W_SROT, TYPE_QCOM_TSENS24XX "-srot-control", 4, 4);
    init_window(s, W_TM, TYPE_QCOM_TSENS24XX "-tm-registers", 4, 0xdc);
    init_window(s, W_TRDY, TYPE_QCOM_TSENS24XX "-trdy", 0xe4, 4);
    memory_region_add_subregion(&s->tm_container, 4, &s->window[W_TM].mr);
    memory_region_add_subregion(&s->tm_container, 0xe4,
                                &s->window[W_TRDY].mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->window[W_SROT].mr);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->tm_container);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[0]);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[1]);
    for (i = 0; i < ARRAY_SIZE(controller_observations); i++) {
        const ObservationInfo *o = &controller_observations[i];
        add_observation(obj, o->name, o, 0);
    }
    for (i = 0; i < SENSOR_COUNT; i++) {
        s->input_mc[i] = UNKNOWN_TEMPERATURE;
        for (j = 0; j < ARRAY_SIZE(sensor_observations); j++) {
            const ObservationInfo *o = &sensor_observations[j];
            g_autofree char *name = g_strdup_printf("sensor%u-%s", i, o->name);
            add_observation(obj, name, o, i);
        }
    }
}

static void tsens_finalize(Object *obj)
{
    QcomTsens24xxState *s = QCOM_TSENS24XX(obj);

    timer_free(s->timer);
}

static const Property tsens_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomTsens24xxState,
                     enabled_reference, false),
    DEFINE_PROP_UINT32("controller-id", QcomTsens24xxState, controller_id, 0),
    DEFINE_PROP_UINT32("reference-boot-enable-mask", QcomTsens24xxState,
                       reference_boot_enable_mask, 0),
    DEFINE_PROP_INT32("reference-temperature-millicelsius", QcomTsens24xxState,
                      reference_temperature_mc, UNKNOWN_TEMPERATURE),
};

static void tsens_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm TSENS24xx finite digital reference";
    dc->realize = tsens_realize;
    dc->unrealize = tsens_unrealize;
    dc->vmsd = &vmstate_tsens;
    dc->user_creatable = false;
    device_class_set_props(dc, tsens_properties);
    rc->phases.enter = tsens_reset_enter;
    rc->phases.hold = tsens_reset_hold;
}

static const TypeInfo tsens_type = {
    .name = TYPE_QCOM_TSENS24XX,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomTsens24xxState),
    .instance_init = tsens_init,
    .instance_finalize = tsens_finalize,
    .class_init = tsens_class_init,
};

static void tsens_register_types(void)
{
    type_register_static(&tsens_type);
}
type_init(tsens_register_types)
