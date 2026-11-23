/*
 * Qualcomm QSEE notification interrupt banks.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Protocol evidence: Android qsee_ipc_irq.c and captured TB371FC DT.
 * This models a normal-world notification latch, not secure firmware or SCM.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-qsee-irq.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define QSEE_STATUS 0x00
#define QSEE_CLEAR  0x08
#define QSEE_MASK   0x1c
#define QSEE_VALID  0x0fU

typedef struct QcomQseeIrqBank {
    QcomQseeIrqState *owner;
    MemoryRegion iomem;
    qemu_irq irq;
    uint32_t pending;
    uint32_t mask;
} QcomQseeIrqBank;

struct QcomQseeIrqState {
    SysBusDevice parent_obj;
    QcomQseeIrqBank banks[QCOM_QSEE_IRQ_BANKS];
    uint8_t input_levels;
    bool ready;
};

static void qsee_update(QcomQseeIrqBank *bank)
{
    if (bank->owner->ready) {
        qemu_set_irq(bank->irq, (bank->pending & ~bank->mask) != 0);
    }
}

bool qcom_qsee_irq_notify(DeviceState *dev, unsigned bank, unsigned bit)
{
    QcomQseeIrqState *s = QCOM_QSEE_IRQ(dev);

    if (!s->ready || bank >= QCOM_QSEE_IRQ_BANKS ||
        bit >= QCOM_QSEE_IRQ_BITS) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_QSEE_IRQ
                      ": invalid notification bank=%u bit=%u or not realized\n",
                      bank, bit);
        return false;
    }
    s->banks[bank].pending |= 1U << bit;
    qsee_update(&s->banks[bank]);
    return true;
}

static void qsee_notify_input(void *opaque, int input, int level)
{
    QcomQseeIrqState *s = opaque;
    uint8_t bit = 1U << input;
    bool rising = level && !(s->input_levels & bit);

    if (level) {
        s->input_levels |= bit;
    } else {
        s->input_levels &= ~bit;
    }
    /*
     * A reference adapter for discrete notification events, not a claim
     * about the waveform or edge detector inside the secure firmware.
     */
    if (rising) {
        qcom_qsee_irq_notify(DEVICE(s), input / QCOM_QSEE_IRQ_BITS,
                            input % QCOM_QSEE_IRQ_BITS);
    }
}

static MemTxResult qsee_bad_access(QcomQseeIrqBank *bank, hwaddr addr,
                                   unsigned size, bool write, uint64_t value)
{
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_QSEE_IRQ
                  ": bank%td unsupported %s offset=0x%" HWADDR_PRIx
                  " size=%u value=0x%" PRIx64 "\n",
                  bank - bank->owner->banks, write ? "write" : "read",
                  addr, size, value);
    return MEMTX_ERROR;
}

static MemTxResult qsee_read(void *opaque, hwaddr addr, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomQseeIrqBank *bank = opaque;

    *value = 0;
    if (size != 4 || (addr & 3)) {
        return qsee_bad_access(bank, addr, size, false, 0);
    }
    switch (addr) {
    case QSEE_STATUS:
        *value = bank->pending;
        return MEMTX_OK;
    case QSEE_MASK:
        *value = bank->mask;
        return MEMTX_OK;
    default:
        /* The cited driver never reads the write-only CLEAR register. */
        return qsee_bad_access(bank, addr, size, false, 0);
    }
}

static MemTxResult qsee_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomQseeIrqBank *bank = opaque;

    if (size != 4 || (addr & 3) || (value & ~QSEE_VALID)) {
        return qsee_bad_access(bank, addr, size, true, value);
    }
    switch (addr) {
    case QSEE_CLEAR:
        bank->pending &= ~value;
        break;
    case QSEE_MASK:
        bank->mask = value;
        break;
    default:
        return qsee_bad_access(bank, addr, size, true, value);
    }
    qsee_update(bank);
    return MEMTX_OK;
}

static const MemoryRegionOps qsee_ops = {
    .read_with_attrs = qsee_read,
    .write_with_attrs = qsee_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /*
     * Let the handlers reject invalid widths/alignment explicitly rather
     * than accepting a wide access as multiple valid register operations.
     */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void qsee_reset(DeviceState *dev)
{
    QcomQseeIrqState *s = QCOM_QSEE_IRQ(dev);
    unsigned i;

    for (i = 0; i < QCOM_QSEE_IRQ_BANKS; i++) {
        s->banks[i].pending = 0;
        s->banks[i].mask = QSEE_VALID;
        qsee_update(&s->banks[i]);
    }
    /* Resetting the protocol does not change external input levels. */
}

static int qsee_post_load(void *opaque, int version_id)
{
    QcomQseeIrqState *s = opaque;
    unsigned i;

    for (i = 0; i < QCOM_QSEE_IRQ_BANKS; i++) {
        if ((s->banks[i].pending | s->banks[i].mask) & ~QSEE_VALID) {
            return -EINVAL;
        }
    }
    s->ready = true;
    for (i = 0; i < QCOM_QSEE_IRQ_BANKS; i++) {
        qsee_update(&s->banks[i]);
    }
    return 0;
}

static const VMStateDescription vmstate_qsee_bank = {
    .name = TYPE_QCOM_QSEE_IRQ "/bank",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(pending, QcomQseeIrqBank),
        VMSTATE_UINT32(mask, QcomQseeIrqBank),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_qsee = {
    .name = TYPE_QCOM_QSEE_IRQ,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = qsee_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_STRUCT_ARRAY(banks, QcomQseeIrqState, QCOM_QSEE_IRQ_BANKS, 1,
                             vmstate_qsee_bank, QcomQseeIrqBank),
        VMSTATE_UINT8(input_levels, QcomQseeIrqState),
        VMSTATE_END_OF_LIST()
    },
};

static void qsee_realize(DeviceState *dev, Error **errp)
{
    QcomQseeIrqState *s = QCOM_QSEE_IRQ(dev);

    s->ready = true;
    qsee_reset(dev);
}

static void qsee_init(Object *obj)
{
    QcomQseeIrqState *s = QCOM_QSEE_IRQ(obj);
    unsigned i;

    for (i = 0; i < QCOM_QSEE_IRQ_BANKS; i++) {
        QcomQseeIrqBank *bank = &s->banks[i];
        g_autofree char *name = g_strdup_printf(TYPE_QCOM_QSEE_IRQ ".bank%u", i);

        bank->owner = s;
        memory_region_init_io(&bank->iomem, obj, &qsee_ops, bank, name,
                              QCOM_QSEE_IRQ_WINDOW_SIZE);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &bank->iomem);
        sysbus_init_irq(SYS_BUS_DEVICE(s), &bank->irq);
    }
    qdev_init_gpio_in_named(DEVICE(s), qsee_notify_input, "notify",
                           QCOM_QSEE_IRQ_BANKS * QCOM_QSEE_IRQ_BITS);
}

static void qsee_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Qualcomm QSEE digital notification interrupt banks";
    dc->realize = qsee_realize;
    dc->vmsd = &vmstate_qsee;
    device_class_set_legacy_reset(dc, qsee_reset);
}

static const TypeInfo qsee_info = {
    .name = TYPE_QCOM_QSEE_IRQ,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomQseeIrqState),
    .instance_init = qsee_init,
    .class_init = qsee_class_init,
};

static void qsee_register_types(void)
{
    type_register_static(&qsee_info);
}
type_init(qsee_register_types)
