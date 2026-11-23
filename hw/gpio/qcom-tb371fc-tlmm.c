/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Functional SM8250 TLMM GPIO subset for the Lenovo TB371FC board.
 *
 * Register layout: Linux v6.12.114 pinctrl-sm8250.c and sm8250.dtsi.
 * IRQ semantics: pinctrl-msm.c msm_gpio_irq_mask(), msm_gpio_irq_set_type()
 * and msm_ack_intr_status(). Source hashes and limitations are recorded in
 * tb371fc/docs/tlmm.md and tb371fc/evidence/power-topology-20261123/.
 *
 * The native board supplies the enclosing MMIO window, IRQ and GPIO count.
 * This does not claim measured silicon reset values, analog pad behavior,
 * an alternate-function peripheral fabric, or PDC wakeup routing.
 */

#include "qemu/osdep.h"
#include "hw/gpio/qcom-tb371fc-tlmm.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/bitops.h"
#include "qemu/module.h"
#include "qcom-tb371fc-tlmm-map.h"

#define CTL_MASK        0x3ff
#define CTL_OE          (1U << 9)
#define CTL_MUX_MASK    (0xfU << 2)
#define IRQ_MASK        0xff
#define IRQ_ENABLE      (1U << 0)
#define IRQ_POLARITY    (1U << 1)
#define IRQ_RAW_ENABLE  (1U << 4)
#define IRQ_TARGET_KPSS 3

#define UFS_CTL         0x5b8000
#define UFS_IO          0x5b8004
#define UFS_CTL_MASK    0x1f
#define SDC2_CTL        0x9b7000
/* CLK/CMD/DATA pull and drive fields; bit 13 is reserved. */
#define SDC2_CTL_MASK   0xdfff

struct QcomTB371FCTLMMState {
    SysBusDevice parent_obj;

    MemoryRegion mmio;
    qemu_irq irq;
    qemu_irq output[QCOM_TB371FC_TLMM_GPIOS];
    qemu_irq output_enable[QCOM_TB371FC_TLMM_GPIOS];
    qemu_irq ufs_reset;

    uint32_t control[QCOM_TB371FC_TLMM_GPIOS];
    uint32_t intr_cfg[QCOM_TB371FC_TLMM_GPIOS];
    uint8_t input[QCOM_TB371FC_TLMM_GPIOS];
    uint8_t out_latch[QCOM_TB371FC_TLMM_GPIOS];
    uint8_t intr_status[QCOM_TB371FC_TLMM_GPIOS];

    uint32_t ufs_control;
    uint32_t sdc2_control;
    uint8_t ufs_out;
    bool boot_ufs_reset_high;
};

static bool tlmm_output_enabled(QcomTB371FCTLMMState *s, unsigned pin)
{
    return (s->control[pin] & (CTL_OE | CTL_MUX_MASK)) == CTL_OE;
}

/* A digital GPIO output reads back its driven level. No contention model. */
static bool tlmm_pad_level(QcomTB371FCTLMMState *s, unsigned pin)
{
    return tlmm_output_enabled(s, pin) ? s->out_latch[pin] : s->input[pin];
}

static void tlmm_update_summary(QcomTB371FCTLMMState *s)
{
    unsigned pin;
    bool pending = false;

    for (pin = 0; pin < QCOM_TB371FC_TLMM_GPIOS; pin++) {
        uint32_t cfg = s->intr_cfg[pin];

        if (s->intr_status[pin] && (cfg & IRQ_ENABLE) &&
            extract32(cfg, 5, 3) == IRQ_TARGET_KPSS) {
            pending = true;
            break;
        }
    }
    qemu_set_irq(s->irq, pending);
}

static void tlmm_drive_output(QcomTB371FCTLMMState *s, unsigned pin)
{
    qemu_set_irq(s->output[pin], s->out_latch[pin]);
    qemu_set_irq(s->output_enable[pin], tlmm_output_enabled(s, pin));
}

static void tlmm_latch(QcomTB371FCTLMMState *s, unsigned pin,
                       bool old_level, bool new_level, bool detect_edges)
{
    uint32_t cfg = s->intr_cfg[pin];
    unsigned detection = extract32(cfg, 2, 2);
    bool event = false;

    if (!(cfg & IRQ_RAW_ENABLE)) {
        return;
    }

    switch (detection) {
    case 0: /* Level mode: polarity selects high or low. */
        event = new_level == !!(cfg & IRQ_POLARITY);
        break;
    case 1: /* Linux two-bit detection encoding for a rising edge. */
        event = detect_edges && !old_level && new_level;
        break;
    case 2: /* Falling, not the one-bit TLMM polarity encoding. */
        event = detect_edges && old_level && !new_level;
        break;
    case 3:
        event = detect_edges && old_level != new_level;
        break;
    }

    if (event) {
        s->intr_status[pin] = 1;
    }
}

static void tlmm_input(void *opaque, int pin, int level)
{
    QcomTB371FCTLMMState *s = opaque;
    bool old_level;

    assert(pin >= 0 && pin < QCOM_TB371FC_TLMM_GPIOS);
    old_level = tlmm_pad_level(s, pin);
    s->input[pin] = !!level;
    tlmm_latch(s, pin, old_level, tlmm_pad_level(s, pin), true);
    tlmm_update_summary(s);
}

static int tlmm_pin(hwaddr addr)
{
    unsigned pin = (addr & 0xfffff) >> 12;

    if (pin < QCOM_TB371FC_TLMM_GPIOS &&
        (addr & ~(hwaddr)0xfff) == tlmm_gpio_offsets[pin]) {
        return pin;
    }
    return -1;
}

static MemTxResult tlmm_read(void *opaque, hwaddr addr, uint64_t *data,
                              unsigned size, MemTxAttrs attrs)
{
    QcomTB371FCTLMMState *s = opaque;
    int pin = tlmm_pin(addr);

    *data = 0;
    if (addr == UFS_CTL) {
        *data = s->ufs_control;
        return MEMTX_OK;
    }
    if (addr == UFS_IO) {
        *data = s->ufs_out;
        return MEMTX_OK;
    }
    if (addr == SDC2_CTL) {
        *data = s->sdc2_control;
        return MEMTX_OK;
    }
    if (pin >= 0) {
        switch (addr & 0xfff) {
        case 0x0:
            *data = s->control[pin];
            return MEMTX_OK;
        case 0x4:
            *data = (s->out_latch[pin] << 1) | tlmm_pad_level(s, pin);
            return MEMTX_OK;
        case 0x8:
            *data = s->intr_cfg[pin];
            return MEMTX_OK;
        case 0xc:
            *data = s->intr_status[pin];
            return MEMTX_OK;
        }
    }
    qemu_log_mask(LOG_UNIMP, "%s: unimplemented read offset 0x%"
                  HWADDR_PRIx "\n", TYPE_QCOM_TB371FC_TLMM, addr);
    return MEMTX_ERROR;
}

static MemTxResult tlmm_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned size, MemTxAttrs attrs)
{
    QcomTB371FCTLMMState *s = opaque;
    int pin = tlmm_pin(addr);
    bool old_level;

    if (addr == UFS_CTL) {
        s->ufs_control = value & UFS_CTL_MASK;
        return MEMTX_OK;
    }
    if (addr == UFS_IO) {
        s->ufs_out = value & 1;
        qemu_set_irq(s->ufs_reset, s->ufs_out);
        return MEMTX_OK;
    }
    if (addr == SDC2_CTL) {
        s->sdc2_control = value & SDC2_CTL_MASK;
        return MEMTX_OK;
    }
    if (pin >= 0) {
        old_level = tlmm_pad_level(s, pin);
        switch (addr & 0xfff) {
        case 0x0:
            s->control[pin] = value & CTL_MASK;
            tlmm_drive_output(s, pin);
            tlmm_latch(s, pin, old_level, tlmm_pad_level(s, pin), true);
            break;
        case 0x4:
            /* Input bit 0 is read-only; bit 1 is the output latch. */
            s->out_latch[pin] = (value >> 1) & 1;
            tlmm_drive_output(s, pin);
            tlmm_latch(s, pin, old_level, tlmm_pad_level(s, pin), true);
            break;
        case 0x8:
            s->intr_cfg[pin] = value & IRQ_MASK;
            /* Configuration changes do not invent a previous edge. */
            tlmm_latch(s, pin, old_level, old_level, false);
            break;
        case 0xc:
            /* SM8250 intr_ack_high=false: WRITE ZERO TO CLEAR. */
            if (!(value & 1)) {
                s->intr_status[pin] = 0;
            }
            /* Active levels re-latch while RAW_STATUS_EN remains set. */
            tlmm_latch(s, pin, old_level, old_level, false);
            break;
        default:
            goto unimplemented;
        }
        tlmm_update_summary(s);
        return MEMTX_OK;
    }

unimplemented:
    qemu_log_mask(LOG_UNIMP, "%s: unimplemented write offset 0x%"
                  HWADDR_PRIx "\n", TYPE_QCOM_TB371FC_TLMM, addr);
    return MEMTX_ERROR;
}

static const MemoryRegionOps tlmm_ops = {
    .read_with_attrs = tlmm_read,
    .write_with_attrs = tlmm_write,
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

static void tlmm_reset_hold(Object *obj, ResetType type)
{
    QcomTB371FCTLMMState *s = QCOM_TB371FC_TLMM(obj);
    unsigned pin;

    memset(s->control, 0, sizeof(s->control));
    memset(s->intr_cfg, 0, sizeof(s->intr_cfg));
    memset(s->out_latch, 0, sizeof(s->out_latch));
    memset(s->intr_status, 0, sizeof(s->intr_status));
    s->ufs_control = 0;
    s->ufs_out = s->boot_ufs_reset_high;
    s->sdc2_control = 0;

    /* Externally driven input levels survive a controller reset. */
    for (pin = 0; pin < QCOM_TB371FC_TLMM_GPIOS; pin++) {
        tlmm_drive_output(s, pin);
    }
    qemu_set_irq(s->ufs_reset, s->ufs_out);
    tlmm_update_summary(s);
}

static int tlmm_post_load(void *opaque, int version_id)
{
    QcomTB371FCTLMMState *s = opaque;
    unsigned pin;

    for (pin = 0; pin < QCOM_TB371FC_TLMM_GPIOS; pin++) {
        if (s->control[pin] & ~CTL_MASK || s->intr_cfg[pin] & ~IRQ_MASK ||
            s->input[pin] > 1 || s->out_latch[pin] > 1 ||
            s->intr_status[pin] > 1) {
            return -EINVAL;
        }
    }
    if (s->ufs_control & ~UFS_CTL_MASK ||
        s->sdc2_control & ~SDC2_CTL_MASK || s->ufs_out > 1) {
        return -EINVAL;
    }
    /* Restore outputs and pending IRQ without synthesizing input edges. */
    for (pin = 0; pin < QCOM_TB371FC_TLMM_GPIOS; pin++) {
        tlmm_drive_output(s, pin);
    }
    qemu_set_irq(s->ufs_reset, s->ufs_out);
    tlmm_update_summary(s);
    return 0;
}

static const VMStateDescription vmstate_tlmm = {
    .name = TYPE_QCOM_TB371FC_TLMM,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = tlmm_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(control, QcomTB371FCTLMMState,
                             QCOM_TB371FC_TLMM_GPIOS),
        VMSTATE_UINT32_ARRAY(intr_cfg, QcomTB371FCTLMMState,
                             QCOM_TB371FC_TLMM_GPIOS),
        VMSTATE_UINT8_ARRAY(input, QcomTB371FCTLMMState,
                            QCOM_TB371FC_TLMM_GPIOS),
        VMSTATE_UINT8_ARRAY(out_latch, QcomTB371FCTLMMState,
                            QCOM_TB371FC_TLMM_GPIOS),
        VMSTATE_UINT8_ARRAY(intr_status, QcomTB371FCTLMMState,
                            QCOM_TB371FC_TLMM_GPIOS),
        VMSTATE_UINT32(ufs_control, QcomTB371FCTLMMState),
        VMSTATE_UINT32(sdc2_control, QcomTB371FCTLMMState),
        VMSTATE_UINT8(ufs_out, QcomTB371FCTLMMState),
        VMSTATE_END_OF_LIST()
    },
};

static void tlmm_init(Object *obj)
{
    QcomTB371FCTLMMState *s = QCOM_TB371FC_TLMM(obj);
    DeviceState *dev = DEVICE(obj);
    SysBusDevice *sbd = SYS_BUS_DEVICE(obj);

    memory_region_init_io(&s->mmio, obj, &tlmm_ops, s,
                           TYPE_QCOM_TB371FC_TLMM, QCOM_TB371FC_TLMM_SIZE);
    sysbus_init_mmio(sbd, &s->mmio);
    sysbus_init_irq(sbd, &s->irq);
    qdev_init_gpio_in_named(dev, tlmm_input, "gpio-in",
                            QCOM_TB371FC_TLMM_GPIOS);
    qdev_init_gpio_out_named(dev, s->output, "gpio-out",
                             QCOM_TB371FC_TLMM_GPIOS);
    qdev_init_gpio_out_named(dev, s->output_enable, "gpio-oe",
                             QCOM_TB371FC_TLMM_GPIOS);
    qdev_init_gpio_out_named(dev, &s->ufs_reset, "ufs-reset", 1);
}

static const Property tlmm_properties[] = {
    DEFINE_PROP_BOOL("boot-ufs-reset-high", QcomTB371FCTLMMState,
                     boot_ufs_reset_high, false),
};

static void tlmm_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "TB371FC SM8250 TLMM functional GPIO subset";
    dc->user_creatable = false;
    dc->vmsd = &vmstate_tlmm;
    device_class_set_props(dc, tlmm_properties);
    rc->phases.hold = tlmm_reset_hold;
}

static const TypeInfo tlmm_info = {
    .name = TYPE_QCOM_TB371FC_TLMM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomTB371FCTLMMState),
    .instance_init = tlmm_init,
    .class_init = tlmm_class_init,
};

static void tlmm_register_types(void)
{
    type_register_static(&tlmm_info);
}
type_init(tlmm_register_types)
