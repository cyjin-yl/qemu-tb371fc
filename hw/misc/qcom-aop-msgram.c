/*
 * AOP message SRAM aperture used by the TB371FC Qualcomm mailbox transport.
 *
 * The captured DT describes 4 KiB of shared message RAM. Descriptor contents,
 * protocol acknowledgements, and message consumption belong to AOP firmware;
 * this device supplies storage and does not implement that firmware.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/misc/qcom-aop-msgram.h"
#include "qemu/module.h"

static void qcom_aop_msgram_realize(DeviceState *dev, Error **errp)
{
    QcomAopMsgramState *s = QCOM_AOP_MSGRAM(dev);

    /*
     * A DeviceState owner gives the RAMBlock a stable migration identity.
     * QEMU initializes RAM to zero; this is a deterministic model convention,
     * not captured firmware initialization. No reset method clears the RAM.
     */
    if (!memory_region_init_ram(&s->sram, OBJECT(dev),
                                TYPE_QCOM_AOP_MSGRAM "/sram",
                                QCOM_AOP_MSGRAM_SIZE, errp)) {
        return;
    }
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->sram);
}

static void qcom_aop_msgram_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm AOP message SRAM without a firmware peer";
    dc->realize = qcom_aop_msgram_realize;
    dc->user_creatable = false;
}

static const TypeInfo qcom_aop_msgram_type = {
    .name = TYPE_QCOM_AOP_MSGRAM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomAopMsgramState),
    .class_init = qcom_aop_msgram_class_init,
};

static void qcom_aop_msgram_register_types(void)
{
    type_register_static(&qcom_aop_msgram_type);
}

type_init(qcom_aop_msgram_register_types)
