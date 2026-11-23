/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm PS_HOLD output register. The platform PMIC determines what a
 * deasserted signal does. This device does not invent a PMIC reset policy.
 * The initialized high level is the direct-kernel firmware handoff profile.
 * Register use: upstream Linux drivers/power/reset/msm-poweroff.c.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-pshold.h"
#include "hw/core/irq.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qemu/module.h"

struct QcomPsHoldState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq output;
    bool level;
};

static uint64_t qcom_pshold_read(void *opaque, hwaddr addr, unsigned size)
{
    QcomPsHoldState *s = opaque;

    return s->level;
}

static void qcom_pshold_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size)
{
    QcomPsHoldState *s = opaque;

    s->level = value & 1;
    qemu_set_irq(s->output, s->level);
}

static const MemoryRegionOps qcom_pshold_ops = {
    .read = qcom_pshold_read,
    .write = qcom_pshold_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static void qcom_pshold_reset_hold(Object *obj, ResetType type)
{
    QcomPsHoldState *s = QCOM_PSHOLD(obj);

    s->level = true;
    qemu_set_irq(s->output, s->level);
}

static int qcom_pshold_post_load(void *opaque, int version_id)
{
    QcomPsHoldState *s = opaque;

    qemu_set_irq(s->output, s->level);
    return 0;
}

static const VMStateDescription vmstate_qcom_pshold = {
    .name = TYPE_QCOM_PSHOLD,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = qcom_pshold_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_BOOL(level, QcomPsHoldState),
        VMSTATE_END_OF_LIST()
    },
};

static void qcom_pshold_init(Object *obj)
{
    QcomPsHoldState *s = QCOM_PSHOLD(obj);

    memory_region_init_io(&s->mmio, obj, &qcom_pshold_ops, s,
                           TYPE_QCOM_PSHOLD, 4);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    qdev_init_gpio_out_named(DEVICE(obj), &s->output, "ps-hold", 1);
}

static void qcom_pshold_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm PS_HOLD digital output";
    dc->vmsd = &vmstate_qcom_pshold;
    rc->phases.hold = qcom_pshold_reset_hold;
}

static const TypeInfo qcom_pshold_info = {
    .name = TYPE_QCOM_PSHOLD,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomPsHoldState),
    .instance_init = qcom_pshold_init,
    .class_init = qcom_pshold_class_init,
};

static void qcom_pshold_register_types(void)
{
    type_register_static(&qcom_pshold_info);
}

type_init(qcom_pshold_register_types)
