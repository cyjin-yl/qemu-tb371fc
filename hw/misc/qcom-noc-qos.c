/*
 * Qualcomm NoC QoS configuration port, explicit digital reference profile.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * The fixed vendor msm_bus_noc_rpmh.c supplies four 32-bit configuration
 * words and masks. This models stored configuration, not NoC bandwidth,
 * arbitration, power, wakeup or SBM handshakes.
 * See tb371fc/docs/noc-qos-reference.md for sources and original-DT geometry.
 */

#include "qemu/osdep.h"
#include "hw/misc/qcom-noc-qos.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

const uint32_t qcom_noc_qos_offsets[QCOM_NOC_QOS_REGISTERS] = {
    0x08, 0x18, 0x40, 0x48,
};

static const uint32_t qos_masks[QCOM_NOC_QOS_REGISTERS] = {
    0x00000079, 0x03ff07ff, 0x00007703, 0x03ff07ff,
};

static const char *const qos_names[QCOM_NOC_QOS_REGISTERS] = {
    "mainctl", "limitbw", "regul0ctl", "regul0bw",
};

static MemTxResult noc_qos_read(void *opaque, hwaddr offset, uint64_t *value,
                                unsigned size, MemTxAttrs attrs)
{
    QcomNocQosWindow *w = opaque;
    QcomNocQosState *s = w->state;

    *value = 0;
    if (!s->enabled_reference || offset != 0 || size != 4) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_NOC_QOS
                      ": unmodeled read at port 0x%" PRIx64
                      " +0x%" HWADDR_PRIx " size %u\n",
                      s->port_address, qcom_noc_qos_offsets[w->reg] + offset,
                      size);
        return MEMTX_ERROR;
    }
    *value = s->regs[w->reg];
    return MEMTX_OK;
}

static MemTxResult noc_qos_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size, MemTxAttrs attrs)
{
    QcomNocQosWindow *w = opaque;
    QcomNocQosState *s = w->state;

    if (!s->enabled_reference || offset != 0 || size != 4) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_NOC_QOS
                      ": unmodeled write at port 0x%" PRIx64
                      " +0x%" HWADDR_PRIx " size %u\n",
                      s->port_address, qcom_noc_qos_offsets[w->reg] + offset,
                      size);
        return MEMTX_ERROR;
    }
    if (value & ~(uint64_t)qos_masks[w->reg]) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_NOC_QOS
                      ": unsupported bits at port 0x%" PRIx64 " +0x%x\n",
                      s->port_address, qcom_noc_qos_offsets[w->reg]);
        return MEMTX_ERROR;
    }
    /*
     * A legal write replaces the complete word. In particular, preserve
     * the literal vendor RMW result even when its mask/shift expressions
     * discard a field previously written by the driver. Do not repair it.
     */
    s->regs[w->reg] = value;
    return MEMTX_OK;
}

static const MemoryRegionOps noc_qos_ops = {
    .read_with_attrs = noc_qos_read,
    .write_with_attrs = noc_qos_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static void noc_qos_reset_enter(Object *obj, ResetType type)
{
    QcomNocQosState *s = QCOM_NOC_QOS(obj);

    /*
     * Explicit reference initial condition, not a measured hardware reset
     * value. In the default unknown profile no guest access can read it.
     */
    memset(s->regs, 0, sizeof(s->regs));
}

static int noc_qos_post_load(void *opaque, int version_id)
{
    QcomNocQosState *s = opaque;
    unsigned i;

    for (i = 0; i < QCOM_NOC_QOS_REGISTERS; i++) {
        if (s->regs[i] & ~qos_masks[i]) {
            error_report(TYPE_QCOM_NOC_QOS
                         ": invalid migrated bits at port 0x%" PRIx64
                         " +0x%x", s->port_address, qcom_noc_qos_offsets[i]);
            return -EINVAL;
        }
        if (!s->enabled_reference && s->regs[i]) {
            error_report(TYPE_QCOM_NOC_QOS
                         ": disabled profile has migrated configuration");
            return -EINVAL;
        }
    }
    return 0;
}

static const VMStateDescription vmstate_noc_qos = {
    .name = TYPE_QCOM_NOC_QOS,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = noc_qos_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomNocQosState),
        VMSTATE_UINT64_EQUAL(port_address, QcomNocQosState),
        VMSTATE_UINT32_ARRAY(regs, QcomNocQosState, QCOM_NOC_QOS_REGISTERS),
        VMSTATE_END_OF_LIST()
    },
};

static void noc_qos_register_get(Object *obj, Visitor *v, const char *name,
                                 void *opaque, Error **errp)
{
    QcomNocQosState *s = QCOM_NOC_QOS(obj);
    unsigned reg = GPOINTER_TO_UINT(opaque);

    if (!s->enabled_reference) {
        error_setg(errp, TYPE_QCOM_NOC_QOS ": reference profile is disabled");
        return;
    }
    visit_type_uint32(v, name, &s->regs[reg], errp);
}

static void noc_qos_address_get(Object *obj, Visitor *v, const char *name,
                                void *opaque, Error **errp)
{
    QcomNocQosState *s = QCOM_NOC_QOS(obj);

    visit_type_uint64(v, name, &s->port_address, errp);
}

static void noc_qos_realize(DeviceState *dev, Error **errp)
{
    QcomNocQosState *s = QCOM_NOC_QOS(dev);

    if (!s->port_address || (s->port_address & 3) ||
        s->port_address > UINT64_MAX - 0x4c) {
        error_setg(errp, TYPE_QCOM_NOC_QOS
                   ": board must set an aligned physical port address");
        return;
    }
    s->reference_profile = s->enabled_reference ? 1 : 0;
}

static void noc_qos_init(Object *obj)
{
    QcomNocQosState *s = QCOM_NOC_QOS(obj);
    unsigned i;

    for (i = 0; i < QCOM_NOC_QOS_REGISTERS; i++) {
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_NOC_QOS "-%s",
                                                qos_names[i]);

        s->windows[i].state = s;
        s->windows[i].reg = i;
        memory_region_init_io(&s->mmio[i], obj, &noc_qos_ops, &s->windows[i],
                              name, 4);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->mmio[i]);
        object_property_add(obj, qos_names[i], "uint32", noc_qos_register_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
    }
    object_property_add(obj, "physical-port-address", "uint64",
                        noc_qos_address_get, NULL, NULL, NULL);
}

static const Property noc_qos_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomNocQosState,
                     enabled_reference, false),
};

static void noc_qos_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm NoC QoS configuration reference port";
    dc->realize = noc_qos_realize;
    dc->vmsd = &vmstate_noc_qos;
    dc->user_creatable = false;
    device_class_set_props(dc, noc_qos_properties);
    rc->phases.enter = noc_qos_reset_enter;
}

static const TypeInfo noc_qos_info = {
    .name = TYPE_QCOM_NOC_QOS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomNocQosState),
    .instance_init = noc_qos_init,
    .class_init = noc_qos_class_init,
};

static void noc_qos_register_types(void)
{
    type_register_static(&noc_qos_info);
}
type_init(noc_qos_register_types)
