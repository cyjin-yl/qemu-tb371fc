/*
 * Qualcomm CPUFreq HW EPSS: explicit synthetic LUT and digital reference.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Interface source: OnePlusOSS/android_kernel_oneplus_sm8250,
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae,
 * drivers/cpufreq/qcom-cpufreq-hw.c.
 *
 * Frequencies, voltages, counter activity and IRQ arbitration below are
 * reference policies, not a dump of the tablet's firmware LUT or analog
 * DVFS implementation. See tb371fc/docs/cpufreq-hw-reference.md.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-cpufreq-hw.h"
#include "hw/core/clock.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/host-utils.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define LUT_ENTRIES 40
#define LUT_STRIDE 4
#define VALID_PERF_STATES 4
#define LUT_IDENTITY UINT32_C(0x584f3031) /* "XO01", immutable reference table */
#define LIMIT_IRQ BIT(2)
#define WINDOW_COUNT 8

enum {
    REG_ENABLE = 0x000,
    REG_DOMAIN_STATE = 0x020,
    REG_FREQ_LUT = 0x100,
    REG_VOLT_LUT = 0x200,
    REG_INTR_CLEAR = 0x308,
    REG_INTR_STATUS = 0x30c,
    REG_PERF_STATE = 0x320,
    REG_CORE_COUNTER = 0x3c8,
};

/* Actual DT resource/CPU/interrupt topology; max_cores is ABI data, not count. */
const QcomCpufreqHwDomain qcom_cpufreq_hw_domains[QCOM_CPUFREQ_HW_DOMAINS] = {
    { 0x18591000, 0x1000, 0, 4, 4, 0x0f, 30 },
    { 0x18592000, 0x1000, 4, 3, 4, 0x70, 31 },
    { 0x18593000, 0x1000, 7, 1, 4, 0x80, 19 },
};

static const uint32_t reference_lval[VALID_PERF_STATES] = { 16, 32, 64, 96 };
static const uint32_t reference_mv[VALID_PERF_STATES] = { 750, 800, 900, 1000 };

typedef struct CpufreqWindowDef {
    uint32_t offset, size;
    const char *name;
} CpufreqWindowDef;

static const CpufreqWindowDef windows[WINDOW_COUNT] = {
    { REG_ENABLE, 4, "enable" },
    { REG_DOMAIN_STATE, 4, "domain-state" },
    { REG_FREQ_LUT, LUT_ENTRIES * LUT_STRIDE, "frequency-lut" },
    { REG_VOLT_LUT, LUT_ENTRIES * LUT_STRIDE, "voltage-lut" },
    { REG_INTR_CLEAR, 4, "interrupt-clear" },
    { REG_INTR_STATUS, 4, "interrupt-status" },
    { REG_PERF_STATE, 4, "performance-state" },
    { REG_CORE_COUNTER, 0, "core-counters" }, /* actual CPU count at realize */
};

typedef struct CpufreqWindow {
    QcomCpufreqHwState *state;
    uint32_t offset;
} CpufreqWindow;

struct QcomCpufreqHwState {
    SysBusDevice parent_obj;
    MemoryRegion container;
    MemoryRegion mmio[WINDOW_COUNT];
    CpufreqWindow window[WINDOW_COUNT];
    qemu_irq irq;
    Clock *xo, *alternate, *cpu_frequency;

    bool enabled_reference;
    uint32_t domain_id;
    uint32_t reference_boot_perf_index;

    /* Immutable VMState identity, populated before the device is realized. */
    uint32_t profile_marker;
    uint64_t base;
    uint32_t resource_size, cpu_mask, max_cores, cpu_count;
    uint32_t lut_identity, lut_entries, lut_stride;

    uint32_t perf_index;
    uint32_t frequency_limit_lval;
    uint32_t irq_status;
    uint32_t counter_value;
    uint32_t counter_fraction;
    int64_t counter_epoch_ns;

    /* Derived state, not a second independent frequency source. */
    uint64_t rate_hz;
    bool limited;
    bool ready;
};

static uint64_t input_hz(Clock *clk)
{
    return CLOCK_PERIOD_TO_HZ(clock_get(clk));
}

static uint32_t requested_lval(QcomCpufreqHwState *s)
{
    return reference_lval[s->perf_index];
}

static uint32_t effective_lval(QcomCpufreqHwState *s)
{
    uint32_t lval = requested_lval(s);

    return s->frequency_limit_lval ?
           MIN(lval, s->frequency_limit_lval) : lval;
}

static uint64_t lval_hz(QcomCpufreqHwState *s, uint32_t lval)
{
    uint64_t xo = input_hz(s->xo);

    /*
     * The real board carrier is 19.2 MHz when available. Keep a missing
     * provider at zero, and never overflow or publish an unrepresentable
     * Clock period if a different provider is connected to this device.
     */
    return xo && xo <= CLOCK_PERIOD_1SEC / lval ? xo * lval : 0;
}

static uint32_t lut_frequency_word(QcomCpufreqHwState *s, unsigned row)
{
    unsigned i = MIN(row, VALID_PERF_STATES - 1);

    return (1U << 30) | (s->max_cores << 16) | reference_lval[i];
}

static uint32_t lut_voltage_word(unsigned row)
{
    return reference_mv[MIN(row, VALID_PERF_STATES - 1)];
}

/*
 * Exact floor(elapsed_ns * Hz / 1e9), retaining the numerator remainder.
 * The 128-bit quotient may exceed 64 bits after a very large clock jump;
 * discarding its high bits is correct for the guest's modulo-2^32 count.
 * mulu64/divu128 also provide portable implementations without __int128.
 */
static void counter_at(QcomCpufreqHwState *s, int64_t now,
                       uint32_t *value, uint32_t *fraction)
{
    uint64_t elapsed = now > s->counter_epoch_ns ?
                       (uint64_t)now - (uint64_t)s->counter_epoch_ns : 0;
    uint64_t ticks, high, remainder;

    mulu64(&ticks, &high, elapsed, s->rate_hz);
    remainder = divu128(&ticks, &high, NANOSECONDS_PER_SECOND);
    remainder += s->counter_fraction;
    *value = s->counter_value + (uint32_t)ticks +
             remainder / NANOSECONDS_PER_SECOND;
    *fraction = remainder % NANOSECONDS_PER_SECOND;
}

static void counter_commit(QcomCpufreqHwState *s, int64_t now)
{
    counter_at(s, now, &s->counter_value, &s->counter_fraction);
    s->counter_epoch_ns = now;
}

static void recompute(QcomCpufreqHwState *s)
{
    s->rate_hz = s->enabled_reference ?
                 lval_hz(s, effective_lval(s)) : 0;
    s->limited = s->enabled_reference && s->rate_hz &&
                 effective_lval(s) < requested_lval(s);
}

static void publish(QcomCpufreqHwState *s, bool propagate)
{
    uint64_t period = CLOCK_PERIOD_FROM_HZ(s->rate_hz);

    if (propagate) {
        clock_update(s->cpu_frequency, period);
    } else {
        /* No clock callbacks into devices which have not yet loaded. */
        clock_set(s->cpu_frequency, period);
    }
    qemu_set_irq(s->irq, s->enabled_reference &&
                         (s->irq_status & LIMIT_IRQ));
}

static void update(QcomCpufreqHwState *s)
{
    uint64_t previous_rate;
    bool previously_limited;

    if (!s->ready) {
        return;
    }
    previous_rate = s->rate_hz;
    previously_limited = s->limited;
    counter_commit(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    recompute(s);
    /*
     * Explicit edge-latch policy: entering a known restriction, or a
     * further effective-rate decrease while restricted, records an event.
     * W1C during an unchanged restriction stays clear; no polling timer
     * or fictitious interrupt-enable register silently rearms it.
     */
    if (s->limited &&
        (!previously_limited ||
         (previous_rate && s->rate_hz < previous_rate))) {
        s->irq_status |= LIMIT_IRQ;
    }
    publish(s, true);
}

static void clock_changed(void *opaque, ClockEvent event)
{
    update(opaque);
}

static MemTxResult access_error(QcomCpufreqHwState *s, bool write,
                                hwaddr offset, unsigned size,
                                const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_CPUFREQ_HW_EPSS
                  ": %s domain %u +0x%" HWADDR_PRIx " size %u: %s\n",
                  write ? "write" : "read", s->domain_id, offset, size, reason);
    return MEMTX_ERROR;
}

static MemTxResult cpufreq_read(void *opaque, hwaddr offset, uint64_t *value,
                               unsigned size, MemTxAttrs attrs)
{
    CpufreqWindow *w = opaque;
    QcomCpufreqHwState *s = w->state;
    uint32_t address = w->offset + offset;
    uint32_t fraction, count;

    *value = 0;
    if (!s->enabled_reference) {
        return access_error(s, false, address, size, "unknown profile");
    }
    if (size != 4 || (address & 3)) {
        return access_error(s, false, address, size, "unsupported access");
    }
    if (address >= REG_FREQ_LUT &&
        address < REG_FREQ_LUT + LUT_ENTRIES * LUT_STRIDE) {
        *value = lut_frequency_word(s, (address - REG_FREQ_LUT) / LUT_STRIDE);
    } else if (address >= REG_VOLT_LUT &&
               address < REG_VOLT_LUT + LUT_ENTRIES * LUT_STRIDE) {
        *value = lut_voltage_word((address - REG_VOLT_LUT) / LUT_STRIDE);
    } else if (address >= REG_CORE_COUNTER &&
               address < REG_CORE_COUNTER + 4 * s->cpu_count) {
        /*
         * Each related core has a separate source-declared address. This
         * limited profile clocks every related core at the domain rate
         * with the same cold origin, so their reference counts coincide.
         * This is not executed/retired cycles or WFI power accounting.
         */
        counter_at(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), &count, &fraction);
        *value = count;
    } else {
        switch (address) {
        case REG_ENABLE:
            *value = 1; /* Explicit reference firmware handoff. */
            break;
        case REG_DOMAIN_STATE:
            *value = effective_lval(s);
            break;
        case REG_INTR_CLEAR:
            *value = 0; /* Reference W1C command register, not held state. */
            break;
        case REG_INTR_STATUS:
            *value = s->irq_status;
            break;
        case REG_PERF_STATE:
            *value = s->perf_index;
            break;
        default:
            return access_error(s, false, address, size, "unsupported register");
        }
    }
    return MEMTX_OK;
}

static MemTxResult cpufreq_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size, MemTxAttrs attrs)
{
    CpufreqWindow *w = opaque;
    QcomCpufreqHwState *s = w->state;
    uint32_t address = w->offset + offset;

    if (!s->enabled_reference) {
        return access_error(s, true, address, size, "unknown profile");
    }
    if (size != 4 || (address & 3)) {
        return access_error(s, true, address, size, "unsupported access");
    }
    switch (address) {
    case REG_PERF_STATE:
        if (value >= VALID_PERF_STATES) {
            return access_error(s, true, address, size,
                                "invalid performance index");
        }
        /*
         * Settle the old frequency before replacing the requested row.
         * update() uses the cached old rate, not the just-written index.
         */
        s->perf_index = value;
        update(s);
        return MEMTX_OK;
    case REG_INTR_CLEAR:
        if (value & ~LIMIT_IRQ) {
            return access_error(s, true, address, size, "unsupported bits");
        }
        s->irq_status &= ~value;
        publish(s, true);
        return MEMTX_OK;
    default:
        return access_error(s, true, address, size, "read-only register");
    }
}

static const MemoryRegionOps cpufreq_ops = {
    .read_with_attrs = cpufreq_read,
    .write_with_attrs = cpufreq_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void cpufreq_reset_enter(Object *obj, ResetType type)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);

    s->ready = false;
    s->perf_index = s->enabled_reference ? s->reference_boot_perf_index : 0;
    s->irq_status = 0;
    s->counter_value = 0;
    s->counter_fraction = 0;
    s->counter_epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->rate_hz = 0;
    s->limited = false;
    /* Provider Clocks and the external limit stimulus are not reset. */
}

static void cpufreq_reset_hold(Object *obj, ResetType type)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);

    recompute(s);
    s->ready = true;
    /* Reapply a retained cap, but reset itself does not synthesize an IRQ. */
    publish(s, true);
}

static int cpufreq_pre_save(void *opaque)
{
    QcomCpufreqHwState *s = opaque;

    counter_commit(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    return 0;
}

static int cpufreq_pre_load(void *opaque)
{
    QcomCpufreqHwState *s = opaque;

    s->ready = false;
    return 0;
}

static int cpufreq_post_load(void *opaque, int version_id)
{
    QcomCpufreqHwState *s = opaque;

    if (s->perf_index >= VALID_PERF_STATES) {
        error_report(TYPE_QCOM_CPUFREQ_HW_EPSS
                     ": invalid migrated performance index");
        return -EINVAL;
    }
    if (s->frequency_limit_lval > 255 || (s->irq_status & ~LIMIT_IRQ) ||
        s->counter_fraction >= NANOSECONDS_PER_SECOND ||
        (!s->enabled_reference &&
         (s->perf_index || s->frequency_limit_lval || s->irq_status ||
          s->counter_value || s->counter_fraction))) {
        error_report(TYPE_QCOM_CPUFREQ_HW_EPSS
                     ": invalid migrated reference state");
        return -EINVAL;
    }
    /*
     * Save count + phase, not the source's absolute epoch. QTest clock
     * origins need not match. Migration/host downtime earns no cycles.
     * Normal CPU clock state is restored before board devices.
     */
    s->counter_epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    recompute(s);
    s->ready = true;
    publish(s, false);
    return 0;
}

static const VMStateDescription vmstate_cpufreq = {
    .name = TYPE_QCOM_CPUFREQ_HW_EPSS,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = cpufreq_pre_save,
    .pre_load = cpufreq_pre_load,
    .post_load = cpufreq_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile_marker, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(domain_id, QcomCpufreqHwState),
        VMSTATE_UINT64_EQUAL(base, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(resource_size, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(cpu_mask, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(max_cores, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(cpu_count, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(reference_boot_perf_index, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(lut_identity, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(lut_entries, QcomCpufreqHwState),
        VMSTATE_UINT32_EQUAL(lut_stride, QcomCpufreqHwState),
        VMSTATE_UINT32(perf_index, QcomCpufreqHwState),
        VMSTATE_UINT32(frequency_limit_lval, QcomCpufreqHwState),
        VMSTATE_UINT32(irq_status, QcomCpufreqHwState),
        VMSTATE_UINT32(counter_value, QcomCpufreqHwState),
        VMSTATE_UINT32(counter_fraction, QcomCpufreqHwState),
        VMSTATE_CLOCK(xo, QcomCpufreqHwState),
        VMSTATE_CLOCK(alternate, QcomCpufreqHwState),
        VMSTATE_END_OF_LIST()
    },
};

enum {
    Q_BASE, Q_SIZE, Q_CPU_MASK, Q_MAX_CORES, Q_CPU_COUNT, Q_LUT_ENTRIES,
    Q_LUT_STRIDE, Q_VALID_STATES, Q_XO_HZ, Q_ALTERNATE_HZ,
    Q_PERF, Q_REQUESTED_LVAL, Q_EFFECTIVE_LVAL, Q_REQUESTED_HZ, Q_RATE_HZ,
    Q_VOLTAGE_UV, Q_IRQ_STATUS, Q_COUNTER, Q_FRACTION, Q_COUNTER_ANCHOR,
    Q_FRACTION_ANCHOR, Q_EPOCH,
    Q_LUT_BASE = 100,
    Q_CORE_COUNTER_BASE = 300,
};

static void scalar_get(Object *obj, Visitor *v, const char *name,
                       void *opaque, Error **errp)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);
    unsigned key = GPOINTER_TO_UINT(opaque);
    uint64_t value = 0;
    uint32_t count, fraction, value32;
    int64_t signed_value;
    bool wide = false;

    if (key >= Q_PERF && !s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS ": unknown profile");
        return;
    }
    if (key >= Q_CORE_COUNTER_BASE) {
        counter_at(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), &count, &fraction);
        value = count;
    } else if (key >= Q_LUT_BASE) {
        unsigned row = (key - Q_LUT_BASE) / 3;
        unsigned field = (key - Q_LUT_BASE) % 3;

        if (field == 0) {
            value = lut_frequency_word(s, row);
        } else if (field == 1) {
            value = lut_voltage_word(row);
        } else {
            value = lval_hz(s, reference_lval[MIN(row, VALID_PERF_STATES - 1)]);
            wide = true;
        }
    } else {
        switch (key) {
        case Q_BASE: value = s->base; wide = true; break;
        case Q_SIZE: value = s->resource_size; break;
        case Q_CPU_MASK: value = s->cpu_mask; break;
        case Q_MAX_CORES: value = s->max_cores; break;
        case Q_CPU_COUNT: value = s->cpu_count; break;
        case Q_LUT_ENTRIES: value = LUT_ENTRIES; break;
        case Q_LUT_STRIDE: value = LUT_STRIDE; break;
        case Q_VALID_STATES: value = VALID_PERF_STATES; break;
        case Q_XO_HZ: value = input_hz(s->xo); wide = true; break;
        case Q_ALTERNATE_HZ: value = input_hz(s->alternate); wide = true; break;
        case Q_PERF: value = s->perf_index; break;
        case Q_REQUESTED_LVAL: value = requested_lval(s); break;
        case Q_EFFECTIVE_LVAL: value = effective_lval(s); break;
        case Q_REQUESTED_HZ: value = lval_hz(s, requested_lval(s));
            wide = true; break;
        case Q_RATE_HZ: value = s->rate_hz; wide = true; break;
        case Q_VOLTAGE_UV: value = reference_mv[s->perf_index] * 1000; break;
        case Q_IRQ_STATUS: value = s->irq_status; break;
        case Q_COUNTER:
        case Q_FRACTION:
            counter_at(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
                       &count, &fraction);
            value = key == Q_COUNTER ? count : fraction;
            break;
        case Q_COUNTER_ANCHOR: value = s->counter_value; break;
        case Q_FRACTION_ANCHOR: value = s->counter_fraction; break;
        case Q_EPOCH:
            signed_value = s->counter_epoch_ns;
            visit_type_int64(v, name, &signed_value, errp);
            return;
        default:
            g_assert_not_reached();
        }
    }
    if (wide) {
        visit_type_uint64(v, name, &value, errp);
    } else {
        value32 = value;
        visit_type_uint32(v, name, &value32, errp);
    }
}

static void bool_get(Object *obj, Visitor *v, const char *name,
                     void *opaque, Error **errp)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);
    unsigned key = GPOINTER_TO_UINT(opaque);
    bool value;

    if (!s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS ": unknown profile");
        return;
    }
    value = key == 0 ? s->limited :
            key == 1 ? s->rate_hz != 0 : (s->irq_status & LIMIT_IRQ) != 0;
    visit_type_bool(v, name, &value, errp);
}

static void limit_get(Object *obj, Visitor *v, const char *name,
                      void *opaque, Error **errp)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);
    uint32_t value = s->frequency_limit_lval;

    if (!s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS ": unknown profile");
        return;
    }
    visit_type_uint32(v, name, &value, errp);
}

static void limit_set(Object *obj, Visitor *v, const char *name,
                      void *opaque, Error **errp)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);
    uint32_t value;

    if (!visit_type_uint32(v, name, &value, errp)) {
        return;
    }
    if (!DEVICE(obj)->realized || !s->ready || !s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS
                   ": external limit requires a realized reference profile");
        return;
    }
    if (value > 255) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS
                   ": frequency-limit-lval must be in 0..255");
        return;
    }
    s->frequency_limit_lval = value;
    update(s);
}

static void cpufreq_realize(DeviceState *dev, Error **errp)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(dev);
    const QcomCpufreqHwDomain *d;
    unsigned i;

    if (s->domain_id >= QCOM_CPUFREQ_HW_DOMAINS) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS ": domain-id must be in 0..2");
        return;
    }
    if (s->reference_boot_perf_index >= VALID_PERF_STATES ||
        (!s->enabled_reference && s->reference_boot_perf_index)) {
        error_setg(errp, TYPE_QCOM_CPUFREQ_HW_EPSS
                   ": reference-boot-perf-index requires 0..3 and"
                   " must be zero in the unknown profile");
        return;
    }
    d = &qcom_cpufreq_hw_domains[s->domain_id];
    s->base = d->base;
    s->resource_size = d->size;
    s->cpu_mask = d->cpu_mask;
    s->max_cores = d->max_cores;
    s->cpu_count = d->cpu_count;
    s->profile_marker = s->enabled_reference ? 1 : 0;
    s->lut_identity = LUT_IDENTITY;
    s->lut_entries = LUT_ENTRIES;
    s->lut_stride = LUT_STRIDE;

    memory_region_init(&s->container, OBJECT(s),
                       TYPE_QCOM_CPUFREQ_HW_EPSS, d->size);
    for (i = 0; i < WINDOW_COUNT; i++) {
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_CPUFREQ_HW_EPSS "-%s",
                                               windows[i].name);
        uint32_t size = windows[i].size ? windows[i].size : 4 * d->cpu_count;

        s->window[i].state = s;
        s->window[i].offset = windows[i].offset;
        memory_region_init_io(&s->mmio[i], OBJECT(s), &cpufreq_ops,
                              &s->window[i], name, size);
        memory_region_add_subregion(&s->container, windows[i].offset, &s->mmio[i]);
    }
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container);
    for (i = 0; i < d->cpu_count; i++) {
        g_autofree char *name = g_strdup_printf("core%u-counter", i);
        object_property_add(OBJECT(s), name, "uint32", scalar_get, NULL,
                            NULL, GUINT_TO_POINTER(Q_CORE_COUNTER_BASE + i));
    }
    s->perf_index = s->enabled_reference ? s->reference_boot_perf_index : 0;
    s->counter_epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    recompute(s);
    /* Set output without callbacks during realization. */
    clock_set(s->cpu_frequency, CLOCK_PERIOD_FROM_HZ(s->rate_hz));
    s->ready = true;
}

static void cpufreq_init(Object *obj)
{
    QcomCpufreqHwState *s = QCOM_CPUFREQ_HW_EPSS(obj);
    DeviceState *dev = DEVICE(obj);
    static const struct {
        const char *name, *type;
        unsigned key;
    } props[] = {
        { "memory-base", "uint64", Q_BASE },
        { "resource-size", "uint32", Q_SIZE },
        { "related-cpu-mask", "uint32", Q_CPU_MASK },
        { "max-cores", "uint32", Q_MAX_CORES },
        { "core-counter-count", "uint32", Q_CPU_COUNT },
        { "lut-max-entries", "uint32", Q_LUT_ENTRIES },
        { "lut-row-size", "uint32", Q_LUT_STRIDE },
        { "valid-perf-count", "uint32", Q_VALID_STATES },
        { "xo-frequency-hz", "uint64", Q_XO_HZ },
        { "alternate-frequency-hz", "uint64", Q_ALTERNATE_HZ },
        { "performance-index", "uint32", Q_PERF },
        { "requested-lval", "uint32", Q_REQUESTED_LVAL },
        { "effective-lval", "uint32", Q_EFFECTIVE_LVAL },
        { "requested-frequency-hz", "uint64", Q_REQUESTED_HZ },
        { "frequency-hz", "uint64", Q_RATE_HZ },
        { "selected-voltage-microvolt", "uint32", Q_VOLTAGE_UV },
        { "interrupt-status", "uint32", Q_IRQ_STATUS },
        { "counter", "uint32", Q_COUNTER },
        { "counter-fraction", "uint32", Q_FRACTION },
        { "counter-value-at-epoch", "uint32", Q_COUNTER_ANCHOR },
        { "counter-fraction-at-epoch", "uint32", Q_FRACTION_ANCHOR },
        { "counter-epoch-ns", "int64", Q_EPOCH },
    };
    unsigned i, field;
    const char *const fields[] = {
        "frequency-word", "voltage-word", "frequency-hz"
    };

    s->xo = qdev_init_clock_in(dev, "xo", clock_changed, s, ClockUpdate);
    s->alternate = qdev_init_clock_in(dev, "alternate", clock_changed, s,
                                      ClockUpdate);
    s->cpu_frequency = qdev_init_clock_out(dev, "cpu-frequency");
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
    for (i = 0; i < ARRAY_SIZE(props); i++) {
        object_property_add(obj, props[i].name, props[i].type, scalar_get,
                            NULL, NULL, GUINT_TO_POINTER(props[i].key));
    }
    for (i = 0; i < LUT_ENTRIES; i++) {
        for (field = 0; field < ARRAY_SIZE(fields); field++) {
            g_autofree char *name = g_strdup_printf("lut%u-%s", i, fields[field]);

            object_property_add(obj, name, field == 2 ? "uint64" : "uint32",
                                scalar_get, NULL, NULL,
                                GUINT_TO_POINTER(Q_LUT_BASE + i * 3 + field));
        }
    }
    object_property_add(obj, "limited", "bool", bool_get, NULL, NULL,
                        GUINT_TO_POINTER(0));
    object_property_add(obj, "frequency-known", "bool", bool_get, NULL, NULL,
                        GUINT_TO_POINTER(1));
    object_property_add(obj, "irq-level", "bool", bool_get, NULL, NULL,
                        GUINT_TO_POINTER(2));
    object_property_add(obj, "frequency-limit-lval", "uint32",
                        limit_get, limit_set, NULL, NULL);
}

static const Property cpufreq_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomCpufreqHwState,
                     enabled_reference, false),
    DEFINE_PROP_UINT32("domain-id", QcomCpufreqHwState, domain_id, 0),
    DEFINE_PROP_UINT32("reference-boot-perf-index", QcomCpufreqHwState,
                      reference_boot_perf_index, 0),
};

static void cpufreq_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm EPSS explicit CPU-frequency reference";
    dc->realize = cpufreq_realize;
    dc->vmsd = &vmstate_cpufreq;
    device_class_set_props(dc, cpufreq_properties);
    rc->phases.enter = cpufreq_reset_enter;
    rc->phases.hold = cpufreq_reset_hold;
}

static const TypeInfo cpufreq_info = {
    .name = TYPE_QCOM_CPUFREQ_HW_EPSS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomCpufreqHwState),
    .instance_init = cpufreq_init,
    .class_init = cpufreq_class_init,
};

static void cpufreq_register_types(void)
{
    type_register_static(&cpufreq_info);
}
type_init(cpufreq_register_types)
