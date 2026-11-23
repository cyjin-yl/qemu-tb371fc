/*
 * Qualcomm MPM2 sleep counter: source-bounded read-only digital reference.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * OnePlusOSS/android_kernel_oneplus_sm8250, commit
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae,
 * drivers/soc/qcom/boot_stats.c reads only a 32-bit word at offset zero.
 * TB371FC's captured DT declares 32768 Hz, independently of GCC's 32000 Hz
 * sleep input. See tb371fc/docs/mpm2-sleep-counter.md for reference policies.
 */

#include "qemu/osdep.h"
#include "hw/timer/qcom-mpm2-sleep-counter.h"
#include "hw/timer/arm-gt-counter.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define MPM2_DT_FREQUENCY 32768

struct QcomMpm2SleepCounterState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    uint32_t frequency;
    uint32_t count;
    /* Fraction of a tick, with NANOSECONDS_PER_SECOND as the denominator. */
    uint32_t fraction;
    int64_t epoch_ns;
};

static void mpm2_sample(QcomMpm2SleepCounterState *s, int64_t now,
                        uint32_t *count, uint32_t *fraction)
{
    uint64_t elapsed = now > s->epoch_ns ?
                       (uint64_t)now - (uint64_t)s->epoch_ns : 0;
    uint64_t ticks, remainder;

    /*
     * Reuse the exact wide conversion used by the Arm timer model; the
     * counter and epoch themselves are independent of that timer.
     * Do not approximate a 32768 Hz period as 30517 integer nanoseconds.
     */
    ticks = arm_gt_ns_to_ticks(elapsed, s->frequency, true);
    /*
     * elapsed % 1e9 < 1e9 and frequency is exactly 32768, so this product
     * fits uint64_t. Adding a saved fraction leaves remainder < 2e9.
     */
    remainder = ((elapsed % NANOSECONDS_PER_SECOND) * s->frequency) %
                NANOSECONDS_PER_SECOND;
    remainder += s->fraction;
    *count = s->count + ticks + remainder / NANOSECONDS_PER_SECOND;
    *fraction = remainder % NANOSECONDS_PER_SECOND;
}

static uint32_t mpm2_count(QcomMpm2SleepCounterState *s)
{
    uint32_t count, fraction;

    mpm2_sample(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL), &count, &fraction);
    return count;
}

static MemTxResult mpm2_read(void *opaque, hwaddr offset, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomMpm2SleepCounterState *s = opaque;

    *value = 0;
    if (offset != 0 || size != 4) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_MPM2_SLEEP_COUNTER
                      ": unsupported read at +0x%" HWADDR_PRIx " size %u\n",
                      offset, size);
        return MEMTX_ERROR;
    }
    *value = mpm2_count(s);
    return MEMTX_OK;
}

static MemTxResult mpm2_write(void *opaque, hwaddr offset, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_MPM2_SLEEP_COUNTER
                  ": write to read-only counter at +0x%" HWADDR_PRIx
                  " size %u\n", offset, size);
    return MEMTX_ERROR;
}

static const MemoryRegionOps mpm2_ops = {
    .read_with_attrs = mpm2_read,
    .write_with_attrs = mpm2_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static int mpm2_pre_save(void *opaque)
{
    QcomMpm2SleepCounterState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    mpm2_sample(s, now, &s->count, &s->fraction);
    s->epoch_ns = now;
    return 0;
}

static int mpm2_post_load(void *opaque, int version_id)
{
    QcomMpm2SleepCounterState *s = opaque;

    if (s->fraction >= NANOSECONDS_PER_SECOND) {
        return -EINVAL;
    }
    /*
     * QTest's virtual origin is not part of the migration stream. Normal
     * CPU clock state loads before board devices. Anchor the saved phase
     * at this destination's current virtual time, rather than importing
     * the source's absolute epoch or accounting host migration downtime.
     */
    s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    return 0;
}

static const VMStateDescription vmstate_mpm2 = {
    .name = TYPE_QCOM_MPM2_SLEEP_COUNTER,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = mpm2_pre_save,
    .post_load = mpm2_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(frequency, QcomMpm2SleepCounterState),
        VMSTATE_UINT32(count, QcomMpm2SleepCounterState),
        VMSTATE_UINT32(fraction, QcomMpm2SleepCounterState),
        VMSTATE_END_OF_LIST()
    },
};

static void mpm2_count_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    uint32_t count = mpm2_count(QCOM_MPM2_SLEEP_COUNTER(obj));

    visit_type_uint32(v, name, &count, errp);
}

static void mpm2_realize(DeviceState *dev, Error **errp)
{
    QcomMpm2SleepCounterState *s = QCOM_MPM2_SLEEP_COUNTER(dev);

    if (s->frequency != MPM2_DT_FREQUENCY) {
        error_setg(errp, TYPE_QCOM_MPM2_SLEEP_COUNTER
                   ": only the DT-declared clock-frequency 32768 is supported");
        return;
    }
    /* Explicit cold reference origin; system reset does not restart it. */
    s->count = 0;
    s->fraction = 0;
    s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
}

static void mpm2_init(Object *obj)
{
    QcomMpm2SleepCounterState *s = QCOM_MPM2_SLEEP_COUNTER(obj);

    memory_region_init_io(&s->mmio, obj, &mpm2_ops, s,
                          TYPE_QCOM_MPM2_SLEEP_COUNTER,
                          QCOM_MPM2_SLEEP_COUNTER_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio);
    object_property_add(obj, "counter", "uint32", mpm2_count_get, NULL, NULL, NULL);
}

static const Property mpm2_properties[] = {
    DEFINE_PROP_UINT32("clock-frequency", QcomMpm2SleepCounterState,
                       frequency, MPM2_DT_FREQUENCY),
};

static void mpm2_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm MPM2 32768 Hz sleep counter reference";
    dc->realize = mpm2_realize;
    dc->vmsd = &vmstate_mpm2;
    device_class_set_props(dc, mpm2_properties);
    /*
     * No reset callback: the source provides no counter reset interface.
     * Preserve the always-running reference domain across system reset.
     */
}

static const TypeInfo mpm2_info = {
    .name = TYPE_QCOM_MPM2_SLEEP_COUNTER,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomMpm2SleepCounterState),
    .instance_init = mpm2_init,
    .class_init = mpm2_class_init,
};

static void mpm2_register_types(void)
{
    type_register_static(&mpm2_info);
}
type_init(mpm2_register_types)
