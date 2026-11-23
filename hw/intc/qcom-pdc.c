/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm PDC legacy interrupt conversion subset.
 *
 * Primary protocol sources:
 * Linux v6.12.114 drivers/irqchip/qcom-pdc.c;
 * TB371FC public kernel-source mirror, commit
 * 9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1, same driver path.
 *
 * The mirror supports qcom,kona-pdc and the second SPI configuration region.
 * It does not read a VERSION register. The current Linux driver does, so
 * exposing a version requires an explicit, unmeasured reference profile.
 *
 * No PDC power-domain transitions, firmware execution, analog timing or
 * hardware-version identity is inferred from this functional model.
 */

#include "qemu/osdep.h"
#include "hw/intc/qcom-pdc.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bitops.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define PDC_BANKS (QCOM_PDC_MAX_PINS / 32)
#define PDC_SPI_WORDS (QCOM_PDC_SPI_CONFIG_SIZE / 4)
#define PDC_ENABLE 0x10
#define PDC_CONFIG 0x110
#define PDC_VERSION 0x1000
#define PDC_VERSION_3_2 0x30200

enum {
    PDC_LEVEL_LOW = 0,
    PDC_EDGE_FALLING = 2,
    PDC_LEVEL_HIGH = 4,
    PDC_EDGE_RISING = 6,
    PDC_EDGE_DUAL = 7,
};

struct QcomPDCState {
    SysBusDevice parent_obj;

    MemoryRegion mmio;
    MemoryRegion spi_mmio;
    qemu_irq irq[QCOM_PDC_MAX_PINS];

    uint32_t num_pins;
    uint32_t reference_version;
    uint32_t saved_num_pins;
    uint32_t saved_reference_version;

    uint32_t enable[PDC_BANKS];
    uint32_t config[QCOM_PDC_MAX_PINS];
    uint32_t spi_config[PDC_SPI_WORDS];
    uint8_t input[QCOM_PDC_MAX_PINS];
};

static bool pdc_valid_config(uint32_t value)
{
    return value == PDC_LEVEL_LOW || value == PDC_EDGE_FALLING ||
           value == PDC_LEVEL_HIGH || value == PDC_EDGE_RISING ||
           value == PDC_EDGE_DUAL;
}

static bool pdc_enabled(QcomPDCState *s, unsigned pin)
{
    return !!(s->enable[pin / 32] & (1U << (pin % 32)));
}

static uint32_t pdc_bank_mask(QcomPDCState *s, unsigned bank)
{
    unsigned first = bank * 32;

    if (first >= s->num_pins) {
        return 0;
    }
    if (s->num_pins - first >= 32) {
        return UINT32_MAX;
    }
    return (1U << (s->num_pins - first)) - 1;
}

static void pdc_sync_level(QcomPDCState *s, unsigned pin)
{
    bool level = false;

    if (pdc_enabled(s, pin)) {
        if (s->config[pin] == PDC_LEVEL_LOW) {
            level = !s->input[pin];
        } else if (s->config[pin] == PDC_LEVEL_HIGH) {
            level = s->input[pin];
        }
    }
    qemu_set_irq(s->irq[pin], level);
}

static void pdc_input(void *opaque, int pin, int level)
{
    QcomPDCState *s = opaque;
    bool old_level;
    bool pulse = false;

    assert(pin >= 0 && pin < s->num_pins);
    old_level = s->input[pin];
    s->input[pin] = !!level;

    if (pdc_enabled(s, pin)) {
        switch (s->config[pin]) {
        case PDC_EDGE_FALLING:
            pulse = old_level && !s->input[pin];
            break;
        case PDC_EDGE_RISING:
            pulse = !old_level && s->input[pin];
            break;
        case PDC_EDGE_DUAL:
            pulse = old_level != s->input[pin];
            break;
        }
    }
    if (pulse) {
        /* The parent edge detector owns pending state and acknowledgement. */
        qemu_irq_pulse(s->irq[pin]);
    } else {
        pdc_sync_level(s, pin);
    }
}

static MemTxResult pdc_read(void *opaque, hwaddr addr, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomPDCState *s = opaque;
    unsigned index;

    *value = 0;
    if (addr >= PDC_ENABLE &&
        addr < PDC_ENABLE + DIV_ROUND_UP(s->num_pins, 32) * 4) {
        index = (addr - PDC_ENABLE) / 4;
        *value = s->enable[index];
        return MEMTX_OK;
    }
    if (addr >= PDC_CONFIG && addr < PDC_CONFIG + s->num_pins * 4) {
        *value = s->config[(addr - PDC_CONFIG) / 4];
        return MEMTX_OK;
    }
    if (addr == PDC_VERSION && s->reference_version) {
        *value = s->reference_version;
        return MEMTX_OK;
    }

    qemu_log_mask(LOG_UNIMP, "%s: unsupported read offset 0x%" HWADDR_PRIx
                  "\n", TYPE_QCOM_PDC, addr);
    return MEMTX_ERROR;
}

static MemTxResult pdc_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomPDCState *s = opaque;
    unsigned index, pin, end;

    if (addr >= PDC_ENABLE &&
        addr < PDC_ENABLE + DIV_ROUND_UP(s->num_pins, 32) * 4) {
        index = (addr - PDC_ENABLE) / 4;
        s->enable[index] = value & pdc_bank_mask(s, index);
        end = MIN((index + 1) * 32, s->num_pins);
        for (pin = index * 32; pin < end; pin++) {
            /* Enable changes cannot replay an old edge. */
            pdc_sync_level(s, pin);
        }
        return MEMTX_OK;
    }
    if (addr >= PDC_CONFIG && addr < PDC_CONFIG + s->num_pins * 4) {
        index = (addr - PDC_CONFIG) / 4;
        /* Reject newer enable bits/unknown fields, rather than aliasing them. */
        if (!pdc_valid_config(value)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "%s: unsupported trigger encoding %" PRIu64 "\n",
                          TYPE_QCOM_PDC, value);
            return MEMTX_ERROR;
        }
        s->config[index] = value;
        pdc_sync_level(s, index);
        return MEMTX_OK;
    }
    if (addr == PDC_VERSION && s->reference_version) {
        return MEMTX_OK; /* Explicit reference-version register is read-only. */
    }

    qemu_log_mask(LOG_UNIMP, "%s: unsupported write offset 0x%" HWADDR_PRIx
                  "\n", TYPE_QCOM_PDC, addr);
    return MEMTX_ERROR;
}

static MemTxResult pdc_spi_read(void *opaque, hwaddr addr, uint64_t *value,
                                 unsigned size, MemTxAttrs attrs)
{
    QcomPDCState *s = opaque;

    if (addr < QCOM_PDC_SPI_CONFIG_SIZE) {
        *value = s->spi_config[addr / 4];
        return MEMTX_OK;
    }
    return MEMTX_ERROR;
}

static MemTxResult pdc_spi_write(void *opaque, hwaddr addr, uint64_t value,
                                  unsigned size, MemTxAttrs attrs)
{
    QcomPDCState *s = opaque;

    if (addr < QCOM_PDC_SPI_CONFIG_SIZE) {
        /*
         * Vendor spi_configure_type() writes a level bitmap for firmware.
         * Preserve this shared configuration, without claiming that an
         * unmodeled power-management firmware has acted on it.
         */
        s->spi_config[addr / 4] = value;
        return MEMTX_OK;
    }
    return MEMTX_ERROR;
}

static const MemoryRegionOps pdc_ops = {
    .read_with_attrs = pdc_read,
    .write_with_attrs = pdc_write,
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

static const MemoryRegionOps pdc_spi_ops = {
    .read_with_attrs = pdc_spi_read,
    .write_with_attrs = pdc_spi_write,
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

static void pdc_reset_hold(Object *obj, ResetType type)
{
    QcomPDCState *s = QCOM_PDC(obj);
    unsigned pin;

    memset(s->enable, 0, sizeof(s->enable));
    memset(s->config, 0, sizeof(s->config));
    memset(s->spi_config, 0, sizeof(s->spi_config));
    /* External inputs survive reset; outputs are disabled. */
    for (pin = 0; pin < s->num_pins; pin++) {
        qemu_set_irq(s->irq[pin], 0);
    }
}

static int pdc_pre_save(void *opaque)
{
    QcomPDCState *s = opaque;

    s->saved_num_pins = s->num_pins;
    s->saved_reference_version = s->reference_version;
    return 0;
}

static int pdc_post_load(void *opaque, int version_id)
{
    QcomPDCState *s = opaque;
    unsigned i;

    if (s->saved_num_pins != s->num_pins ||
        s->saved_reference_version != s->reference_version) {
        return -EINVAL;
    }
    for (i = 0; i < PDC_BANKS; i++) {
        if (s->enable[i] & ~pdc_bank_mask(s, i)) {
            return -EINVAL;
        }
    }
    for (i = 0; i < QCOM_PDC_MAX_PINS; i++) {
        if (!pdc_valid_config(s->config[i]) || s->input[i] > 1) {
            return -EINVAL;
        }
    }
    for (i = 0; i < s->num_pins; i++) {
        /* Do not replay edge pulses: the GIC migrates its own pending bits. */
        pdc_sync_level(s, i);
    }
    return 0;
}

static const VMStateDescription vmstate_pdc = {
    .name = TYPE_QCOM_PDC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = pdc_pre_save,
    .post_load = pdc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(saved_num_pins, QcomPDCState),
        VMSTATE_UINT32(saved_reference_version, QcomPDCState),
        VMSTATE_UINT32_ARRAY(enable, QcomPDCState, PDC_BANKS),
        VMSTATE_UINT32_ARRAY(config, QcomPDCState, QCOM_PDC_MAX_PINS),
        VMSTATE_UINT32_ARRAY(spi_config, QcomPDCState, PDC_SPI_WORDS),
        VMSTATE_UINT8_ARRAY(input, QcomPDCState, QCOM_PDC_MAX_PINS),
        VMSTATE_END_OF_LIST()
    },
};

static void pdc_realize(DeviceState *dev, Error **errp)
{
    QcomPDCState *s = QCOM_PDC(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);
    unsigned pin;

    if (!s->num_pins || s->num_pins > QCOM_PDC_MAX_PINS) {
        error_setg(errp, "PDC num-pins must be in 1..%u", QCOM_PDC_MAX_PINS);
        return;
    }
    if (s->reference_version >= PDC_VERSION_3_2) {
        error_setg(errp, "PDC v3.2+ enable layout is not implemented");
        return;
    }
    memory_region_init_io(&s->mmio, OBJECT(s), &pdc_ops, s,
                           "qcom-pdc-registers", QCOM_PDC_MMIO_SIZE);
    memory_region_init_io(&s->spi_mmio, OBJECT(s), &pdc_spi_ops, s,
                           "qcom-pdc-spi-config", QCOM_PDC_SPI_CONFIG_SIZE);
    sysbus_init_mmio(sbd, &s->mmio);
    sysbus_init_mmio(sbd, &s->spi_mmio);
    qdev_init_gpio_in_named(dev, pdc_input, "irq-in", s->num_pins);
    for (pin = 0; pin < s->num_pins; pin++) {
        sysbus_init_irq(sbd, &s->irq[pin]);
    }
}

static const Property pdc_properties[] = {
    DEFINE_PROP_UINT32("num-pins", QcomPDCState, num_pins, QCOM_PDC_NATIVE_PINS),
    DEFINE_PROP_UINT32("reference-version", QcomPDCState, reference_version, 0),
};

static void pdc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm PDC legacy interrupt conversion subset";
    dc->realize = pdc_realize;
    dc->vmsd = &vmstate_pdc;
    dc->user_creatable = false;
    device_class_set_props(dc, pdc_properties);
    rc->phases.hold = pdc_reset_hold;
}

static const TypeInfo pdc_info = {
    .name = TYPE_QCOM_PDC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomPDCState),
    .class_init = pdc_class_init,
};

static void pdc_register_types(void)
{
    type_register_static(&pdc_info);
}
type_init(pdc_register_types)
