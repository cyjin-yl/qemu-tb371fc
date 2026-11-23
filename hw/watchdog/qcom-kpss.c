/*
 * Qualcomm KPSS watchdog: counter, bark interrupt and non-secure bite.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Register ABI: Linux drivers/watchdog/qcom-wdt.c and Qualcomm's
 * drivers/soc/qcom/watchdog_v2.c. See tb371fc/docs/watchdog.md.
 * The default 32765 Hz is the vendor driver's reference, not a measured
 * TB371FC oscillator. Secure watchdog firmware and power-off clock behavior
 * are outside this device. A bite invokes QEMU's configured watchdog action.
 */

#include "qemu/osdep.h"
#include "hw/watchdog/qcom-kpss.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bitops.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "system/watchdog.h"

#define WDT_RESET       0x04
#define WDT_ENABLE      0x08
#define WDT_STATUS      0x0c
#define WDT_BARK        0x10
#define WDT_BITE        0x14
#define WDT_EN          BIT(0)
#define WDT_WAKE_EN     BIT(1)
#define WDT_COUNT_MASK  0xfffff

struct QcomKpssWatchdogState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq bark_irq;
    qemu_irq bite_irq;
    qemu_irq wakeup;
    QEMUTimer *timer;
    uint32_t frequency;
    uint32_t enable;
    uint32_t bark;
    uint32_t bite;
    uint64_t elapsed_ns;
    int64_t epoch_ns;
    bool barked;
    bool bitten;
};

static uint64_t qcom_wdt_elapsed(QcomKpssWatchdogState *s, int64_t now)
{
    uint64_t elapsed = s->elapsed_ns;

    if ((s->enable & WDT_EN) && now > s->epoch_ns) {
        elapsed += now - s->epoch_ns;
    }
    return elapsed;
}

static uint64_t qcom_wdt_ticks(QcomKpssWatchdogState *s, int64_t now)
{
    return muldiv64(qcom_wdt_elapsed(s, now), s->frequency,
                    NANOSECONDS_PER_SECOND);
}

static void qcom_wdt_irqs(QcomKpssWatchdogState *s)
{
    bool running = s->enable & WDT_EN;

    qemu_set_irq(s->bark_irq, running && s->barked);
    qemu_set_irq(s->bite_irq, running && s->bitten);
    qemu_set_irq(s->wakeup, running && s->barked &&
                             (s->enable & WDT_WAKE_EN));
}

static void qcom_wdt_schedule(QcomKpssWatchdogState *s)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    uint64_t elapsed, compare, target, remaining;

    timer_del(s->timer);
    if (!(s->enable & WDT_EN) || (s->barked && s->bitten)) {
        return;
    }

    compare = s->barked ? s->bite : s->bark;
    if (!s->bitten) {
        compare = MIN(compare, s->bite);
    }
    /*
     * Ceil division preserves the first complete counter tick at any rate.
     * Compare is 20 bits; multiplication cannot overflow uint64_t.
     */
    target = (compare * NANOSECONDS_PER_SECOND + s->frequency - 1) /
             s->frequency;
    elapsed = qcom_wdt_elapsed(s, now);
    remaining = target > elapsed ? target - elapsed : 0;
    timer_mod(s->timer, remaining > INT64_MAX - now ?
              INT64_MAX : now + remaining);
}

static void qcom_wdt_expire(void *opaque)
{
    QcomKpssWatchdogState *s = opaque;
    bool bite_now = false;
    uint64_t count;

    if (!(s->enable & WDT_EN)) {
        return;
    }
    count = qcom_wdt_ticks(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL));
    if (!s->barked && count >= s->bark) {
        s->barked = true;
    }
    if (!s->bitten && count >= s->bite) {
        s->bitten = true;
        bite_now = true;
    }
    qcom_wdt_irqs(s);
    qcom_wdt_schedule(s);

    if (bite_now) {
        /*
         * Set the complete state before the action, which may drop the BQL.
         * VM reset clears the device through its ordinary reset callback.
         */
        watchdog_perform_action();
    }
}

static void qcom_wdt_pet(QcomKpssWatchdogState *s)
{
    s->elapsed_ns = 0;
    s->epoch_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    s->barked = false;
    s->bitten = false;
    qcom_wdt_irqs(s);
    qcom_wdt_schedule(s);
}

static MemTxResult qcom_wdt_read(void *opaque, hwaddr offset,
                                uint64_t *value, unsigned size,
                                MemTxAttrs attrs)
{
    QcomKpssWatchdogState *s = opaque;

    switch (offset) {
    case WDT_RESET:
        *value = 0;
        break;
    case WDT_ENABLE:
        *value = s->enable;
        break;
    case WDT_STATUS:
        *value = (qcom_wdt_ticks(s, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL)) &
                  WDT_COUNT_MASK) << 1;
        break;
    case WDT_BARK:
        *value = s->bark;
        break;
    case WDT_BITE:
        *value = s->bite;
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "qcom-kpss-watchdog: read at 0x%"
                      HWADDR_PRIx "\n", offset);
        return MEMTX_ERROR;
    }
    return MEMTX_OK;
}

static MemTxResult qcom_wdt_write(void *opaque, hwaddr offset,
                                 uint64_t value, unsigned size,
                                 MemTxAttrs attrs)
{
    QcomKpssWatchdogState *s = opaque;
    int64_t now;

    switch (offset) {
    case WDT_RESET:
        if (value & 1) {
            qcom_wdt_pet(s);
        }
        break;
    case WDT_ENABLE:
        now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
        s->elapsed_ns = qcom_wdt_elapsed(s, now);
        s->epoch_ns = now;
        s->enable = value & (WDT_EN | WDT_WAKE_EN);
        qcom_wdt_irqs(s);
        qcom_wdt_schedule(s);
        break;
    case WDT_STATUS:
        /* The exposed counter field is read-only. */
        break;
    case WDT_BARK:
        s->bark = value & WDT_COUNT_MASK;
        qcom_wdt_schedule(s);
        break;
    case WDT_BITE:
        s->bite = value & WDT_COUNT_MASK;
        qcom_wdt_schedule(s);
        break;
    default:
        qemu_log_mask(LOG_UNIMP, "qcom-kpss-watchdog: write at 0x%"
                      HWADDR_PRIx "\n", offset);
        return MEMTX_ERROR;
    }
    return MEMTX_OK;
}

static const MemoryRegionOps qcom_wdt_ops = {
    .read_with_attrs = qcom_wdt_read,
    .write_with_attrs = qcom_wdt_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
    .impl = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void qcom_wdt_reset(DeviceState *dev)
{
    QcomKpssWatchdogState *s = QCOM_KPSS_WATCHDOG(dev);

    s->enable = 0;
    s->bark = 0;
    s->bite = 0;
    qcom_wdt_pet(s);
}

static int qcom_wdt_post_load(void *opaque, int version_id)
{
    QcomKpssWatchdogState *s = opaque;

    if ((s->enable & ~(WDT_EN | WDT_WAKE_EN)) ||
        s->bark > WDT_COUNT_MASK || s->bite > WDT_COUNT_MASK ||
        s->epoch_ns < 0 || s->elapsed_ns > INT64_MAX) {
        return -EINVAL;
    }
    qcom_wdt_irqs(s);
    qcom_wdt_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_qcom_wdt = {
    .name = TYPE_QCOM_KPSS_WATCHDOG,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = qcom_wdt_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(frequency, QcomKpssWatchdogState),
        VMSTATE_UINT32(enable, QcomKpssWatchdogState),
        VMSTATE_UINT32(bark, QcomKpssWatchdogState),
        VMSTATE_UINT32(bite, QcomKpssWatchdogState),
        VMSTATE_UINT64(elapsed_ns, QcomKpssWatchdogState),
        VMSTATE_INT64(epoch_ns, QcomKpssWatchdogState),
        VMSTATE_BOOL(barked, QcomKpssWatchdogState),
        VMSTATE_BOOL(bitten, QcomKpssWatchdogState),
        VMSTATE_TIMER_PTR(timer, QcomKpssWatchdogState),
        VMSTATE_END_OF_LIST()
    },
};

static void qcom_wdt_realize(DeviceState *dev, Error **errp)
{
    QcomKpssWatchdogState *s = QCOM_KPSS_WATCHDOG(dev);

    if (!s->frequency || s->frequency > NANOSECONDS_PER_SECOND) {
        error_setg(errp, "qcom-kpss-watchdog: frequency must be 1..1000000000");
    }
}

static void qcom_wdt_init(Object *obj)
{
    QcomKpssWatchdogState *s = QCOM_KPSS_WATCHDOG(obj);
    SysBusDevice *sbd = SYS_BUS_DEVICE(obj);

    memory_region_init_io(&s->mmio, obj, &qcom_wdt_ops, s,
                          TYPE_QCOM_KPSS_WATCHDOG, QCOM_KPSS_WATCHDOG_SIZE);
    sysbus_init_mmio(sbd, &s->mmio);
    sysbus_init_irq(sbd, &s->bark_irq);
    sysbus_init_irq(sbd, &s->bite_irq);
    qdev_init_gpio_out_named(DEVICE(obj), &s->wakeup, "wakeup", 1);
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, qcom_wdt_expire, s);
}

static void qcom_wdt_finalize(Object *obj)
{
    QcomKpssWatchdogState *s = QCOM_KPSS_WATCHDOG(obj);

    timer_free(s->timer);
}

static const Property qcom_wdt_properties[] = {
    DEFINE_PROP_UINT32("frequency", QcomKpssWatchdogState, frequency, 32765),
};

static void qcom_wdt_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm KPSS digital watchdog (reference clock profile)";
    dc->realize = qcom_wdt_realize;
    device_class_set_legacy_reset(dc, qcom_wdt_reset);
    device_class_set_props(dc, qcom_wdt_properties);
    dc->vmsd = &vmstate_qcom_wdt;
}

static const TypeInfo qcom_wdt_info = {
    .name = TYPE_QCOM_KPSS_WATCHDOG,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomKpssWatchdogState),
    .instance_init = qcom_wdt_init,
    .instance_finalize = qcom_wdt_finalize,
    .class_init = qcom_wdt_class_init,
};

static void qcom_wdt_register_types(void)
{
    type_register_static(&qcom_wdt_info);
}

type_init(qcom_wdt_register_types)
