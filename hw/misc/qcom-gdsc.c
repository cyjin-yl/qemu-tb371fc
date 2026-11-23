/*
 * Qualcomm GDSC software control and digital power-status model.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Evidence: downstream gdsc-regulator.c and captured TB371FC GPU domains.
 * Reference inputs represent external requests, not GPU or secure firmware.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-gdsc.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bitops.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define GDSCR_COLLAPSE       BIT(0)
#define GDSCR_HW_CONTROL     BIT(1)
#define GDSCR_RETAIN_FF      BIT(11)
#define GDSCR_CLK_DIS_WAIT   (0xfU << 12)
#define GDSCR_POWER_ON       BIT(31)
#define GDSCR_WRITE_MASK     (GDSCR_COLLAPSE | GDSCR_HW_CONTROL | \
                              GDSCR_RETAIN_FF | GDSCR_CLK_DIS_WAIT)
#define DOMAIN_CLAMP_IO      BIT(0)
#define DOMAIN_RESET         BIT(4)
#define DOMAIN_WRITE_MASK    (DOMAIN_CLAMP_IO | DOMAIN_RESET)
#define SW_RESET_ASSERT     BIT(0)

typedef struct QcomGdscWindow {
    QcomGdscState *owner;
    MemoryRegion iomem;
    unsigned index;
} QcomGdscWindow;

struct QcomGdscState {
    SysBusDevice parent_obj;
    QcomGdscWindow windows[QCOM_GDSC_NUM_REGIONS];
    qemu_irq power_on;
    qemu_irq state_known;
    qemu_irq clamp_io;
    qemu_irq reset_asserted;
    uint32_t control;
    uint32_t domain_control;
    uint32_t sw_reset;
    bool hw_enable;
    bool external_vote;
    bool parent_known;
    bool parent_on;
    bool enabled_reference;
    bool saved_enabled_reference;
    bool reference_on;
    bool saved_reference_on;
    bool ready;
};

static bool gdsc_requested(QcomGdscState *s)
{
    bool requested = s->control & GDSCR_HW_CONTROL ?
                     s->hw_enable : !(s->control & GDSCR_COLLAPSE);

    return s->external_vote || requested;
}

static bool gdsc_power_known(QcomGdscState *s)
{
    /* A missing local request resolves the AND even with unknown parent. */
    return s->enabled_reference && (s->parent_known || !gdsc_requested(s));
}

static bool gdsc_powered(QcomGdscState *s)
{
    return s->enabled_reference && s->parent_known && s->parent_on &&
           gdsc_requested(s);
}

static bool gdsc_reset_asserted(QcomGdscState *s)
{
    return s->enabled_reference &&
           ((s->domain_control & DOMAIN_RESET) ||
            (s->sw_reset & SW_RESET_ASSERT));
}

static void gdsc_update(QcomGdscState *s)
{
    if (!s->ready) {
        return;
    }
    if (!gdsc_power_known(s)) {
        qemu_set_irq(s->state_known, 0);
        qemu_set_irq(s->power_on, 0);
    } else {
        qemu_set_irq(s->power_on, gdsc_powered(s));
        qemu_set_irq(s->state_known, 1);
    }
    qemu_set_irq(s->clamp_io, s->enabled_reference &&
                 (s->domain_control & DOMAIN_CLAMP_IO));
    qemu_set_irq(s->reset_asserted, gdsc_reset_asserted(s));
}

static void gdsc_hw_input(void *opaque, int input, int level)
{
    QcomGdscState *s = opaque;

    s->hw_enable = !!level;
    gdsc_update(s);
}

static void gdsc_vote_input(void *opaque, int input, int level)
{
    QcomGdscState *s = opaque;

    s->external_vote = !!level;
    gdsc_update(s);
}

static void gdsc_parent_known_input(void *opaque, int input, int level)
{
    QcomGdscState *s = opaque;

    s->parent_known = !!level;
    gdsc_update(s);
}

static void gdsc_parent_on_input(void *opaque, int input, int level)
{
    QcomGdscState *s = opaque;

    s->parent_on = !!level;
    gdsc_update(s);
}

static MemTxResult gdsc_bad_access(QcomGdscWindow *window, hwaddr addr,
                                   unsigned size, bool write, uint64_t value)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_GDSC
                  ": unsupported %s region=%u offset=0x%" HWADDR_PRIx
                  " size=%u value=0x%" PRIx64 "\n",
                  write ? "write" : "read", window->index, addr, size, value);
    return MEMTX_ERROR;
}

static MemTxResult gdsc_read(void *opaque, hwaddr addr, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomGdscWindow *window = opaque;
    QcomGdscState *s = window->owner;
    uint32_t power = gdsc_powered(s) ? GDSCR_POWER_ON : 0;

    *value = 0;
    if (!s->enabled_reference || addr || size != 4) {
        return gdsc_bad_access(window, addr, size, false, 0);
    }
    if ((window->index == QCOM_GDSC_CONTROL ||
         window->index == QCOM_GDSC_HW_STATUS) && !gdsc_power_known(s)) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_GDSC
                      ": unknown parent availability; refusing power feedback\n");
        return MEMTX_ERROR;
    }
    switch (window->index) {
    case QCOM_GDSC_CONTROL:
        *value = s->control | power;
        break;
    case QCOM_GDSC_HW_STATUS:
        *value = power;
        break;
    case QCOM_GDSC_DOMAIN_CONTROL:
        *value = s->domain_control;
        break;
    case QCOM_GDSC_SW_RESET:
        *value = s->sw_reset;
        break;
    default:
        g_assert_not_reached();
    }
    return MEMTX_OK;
}

static MemTxResult gdsc_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomGdscWindow *window = opaque;
    QcomGdscState *s = window->owner;

    if (!s->enabled_reference || addr || size != 4) {
        return gdsc_bad_access(window, addr, size, true, value);
    }
    switch (window->index) {
    case QCOM_GDSC_CONTROL:
        /*
         * Preserve normal driver read/modify/write of the read-only status.
         * SW_OVERRIDE and unknown fields require unmodeled sequencing, so
         * reject the complete write instead of partially applying it.
         */
        if (value & ~((uint64_t)GDSCR_WRITE_MASK | GDSCR_POWER_ON)) {
            return gdsc_bad_access(window, addr, size, true, value);
        }
        s->control = value & GDSCR_WRITE_MASK;
        break;
    case QCOM_GDSC_DOMAIN_CONTROL:
        if (value & ~(uint64_t)DOMAIN_WRITE_MASK) {
            return gdsc_bad_access(window, addr, size, true, value);
        }
        s->domain_control = value;
        break;
    case QCOM_GDSC_SW_RESET:
        if (value & ~(uint64_t)SW_RESET_ASSERT) {
            return gdsc_bad_access(window, addr, size, true, value);
        }
        s->sw_reset = value;
        break;
    default:
        return gdsc_bad_access(window, addr, size, true, value);
    }
    gdsc_update(s);
    return MEMTX_OK;
}

static const MemoryRegionOps gdsc_ops = {
    .read_with_attrs = gdsc_read,
    .write_with_attrs = gdsc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* The system bus can split transactions at these four-byte boundaries. */
    .valid = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
};

static bool gdsc_get_powered(Object *obj, Error **errp)
{
    return gdsc_powered(QCOM_GDSC(obj));
}

static bool gdsc_get_hw_enable(Object *obj, Error **errp)
{
    return QCOM_GDSC(obj)->hw_enable;
}

static bool gdsc_get_external_vote(Object *obj, Error **errp)
{
    return QCOM_GDSC(obj)->external_vote;
}

static bool gdsc_get_clamp(Object *obj, Error **errp)
{
    QcomGdscState *s = QCOM_GDSC(obj);

    return s->enabled_reference && (s->domain_control & DOMAIN_CLAMP_IO);
}

static bool gdsc_get_reset(Object *obj, Error **errp)
{
    return gdsc_reset_asserted(QCOM_GDSC(obj));
}

static bool gdsc_get_known(Object *obj, Error **errp)
{
    return gdsc_power_known(QCOM_GDSC(obj));
}

static bool gdsc_get_parent_known(Object *obj, Error **errp)
{
    return QCOM_GDSC(obj)->parent_known;
}

static bool gdsc_get_parent_on(Object *obj, Error **errp)
{
    return QCOM_GDSC(obj)->parent_on;
}

static void gdsc_reset(DeviceState *dev)
{
    QcomGdscState *s = QCOM_GDSC(dev);

    s->control = s->reference_on ? 0 : GDSCR_COLLAPSE;
    s->domain_control = s->reference_on ? 0 : DOMAIN_CLAMP_IO;
    s->sw_reset = 0;
    /* Protocol reset cannot lower requests originating outside this device. */
    gdsc_update(s);
}

static int gdsc_pre_save(void *opaque)
{
    QcomGdscState *s = opaque;

    s->saved_enabled_reference = s->enabled_reference;
    s->saved_reference_on = s->reference_on;
    return 0;
}

static int gdsc_post_load(void *opaque, int version_id)
{
    QcomGdscState *s = opaque;

    if (s->saved_enabled_reference != s->enabled_reference) {
        error_report(TYPE_QCOM_GDSC
                     ": enabled-reference differs between source"
                     " and destination");
        return -EINVAL;
    }
    if (s->saved_reference_on != s->reference_on) {
        error_report(TYPE_QCOM_GDSC
                     ": reference-on differs between source and destination");
        return -EINVAL;
    }
    if ((s->control & ~GDSCR_WRITE_MASK) ||
        (s->domain_control & ~DOMAIN_WRITE_MASK) ||
        (s->sw_reset & ~SW_RESET_ASSERT)) {
        error_report(TYPE_QCOM_GDSC ": invalid migrated register state");
        return -EINVAL;
    }
    gdsc_update(s);
    return 0;
}

static const VMStateDescription vmstate_gdsc = {
    .name = TYPE_QCOM_GDSC,
    /* Version 1 omitted the parent and cannot establish valid feedback. */
    .version_id = 2,
    .minimum_version_id = 2,
    .pre_save = gdsc_pre_save,
    .post_load = gdsc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, QcomGdscState),
        VMSTATE_UINT32(domain_control, QcomGdscState),
        VMSTATE_UINT32(sw_reset, QcomGdscState),
        VMSTATE_BOOL(hw_enable, QcomGdscState),
        VMSTATE_BOOL(external_vote, QcomGdscState),
        VMSTATE_BOOL(parent_known, QcomGdscState),
        VMSTATE_BOOL(parent_on, QcomGdscState),
        VMSTATE_BOOL(saved_enabled_reference, QcomGdscState),
        VMSTATE_BOOL(saved_reference_on, QcomGdscState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property gdsc_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomGdscState,
                     enabled_reference, false),
    DEFINE_PROP_BOOL("reference-on", QcomGdscState, reference_on, false),
};

static void gdsc_realize(DeviceState *dev, Error **errp)
{
    QcomGdscState *s = QCOM_GDSC(dev);

    s->ready = true;
    gdsc_reset(dev);
}

static void gdsc_init(Object *obj)
{
    QcomGdscState *s = QCOM_GDSC(obj);
    static const char *names[QCOM_GDSC_NUM_REGIONS] = {
        "control", "hw-status", "domain-control", "sw-reset",
    };
    unsigned i;

    for (i = 0; i < QCOM_GDSC_NUM_REGIONS; i++) {
        QcomGdscWindow *window = &s->windows[i];
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_GDSC ".%s", names[i]);

        window->owner = s;
        window->index = i;
        memory_region_init_io(&window->iomem, obj, &gdsc_ops, window,
                              name, QCOM_GDSC_REGISTER_SIZE);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &window->iomem);
    }
    qdev_init_gpio_in_named(DEVICE(s), gdsc_hw_input, "hw-enable", 1);
    qdev_init_gpio_in_named(DEVICE(s), gdsc_vote_input, "external-vote", 1);
    qdev_init_gpio_in_named(DEVICE(s), gdsc_parent_known_input, "parent-known", 1);
    qdev_init_gpio_in_named(DEVICE(s), gdsc_parent_on_input, "parent-on", 1);
    qdev_init_gpio_out_named(DEVICE(s), &s->power_on, "power-on", 1);
    qdev_init_gpio_out_named(DEVICE(s), &s->state_known, "state-known", 1);
    qdev_init_gpio_out_named(DEVICE(s), &s->clamp_io, "clamp-io", 1);
    qdev_init_gpio_out_named(DEVICE(s), &s->reset_asserted, "reset-asserted", 1);
    object_property_add_bool(obj, "power-on", gdsc_get_powered, NULL);
    object_property_add_bool(obj, "hw-enable-state", gdsc_get_hw_enable, NULL);
    object_property_add_bool(obj, "external-vote-state",
                             gdsc_get_external_vote, NULL);
    object_property_add_bool(obj, "clamp-io", gdsc_get_clamp, NULL);
    object_property_add_bool(obj, "reset-asserted", gdsc_get_reset, NULL);
    object_property_add_bool(obj, "state-known", gdsc_get_known, NULL);
    object_property_add_bool(obj, "parent-known", gdsc_get_parent_known, NULL);
    object_property_add_bool(obj, "parent-on", gdsc_get_parent_on, NULL);
}

static void gdsc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm GDSC digital requests and status (reference power)";
    dc->realize = gdsc_realize;
    dc->vmsd = &vmstate_gdsc;
    device_class_set_props(dc, gdsc_properties);
    device_class_set_legacy_reset(dc, gdsc_reset);
}

static const TypeInfo gdsc_info = {
    .name = TYPE_QCOM_GDSC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomGdscState),
    .instance_init = gdsc_init,
    .class_init = gdsc_class_init,
};

static void gdsc_register_types(void)
{
    type_register_static(&gdsc_info);
}
type_init(gdsc_register_types)
