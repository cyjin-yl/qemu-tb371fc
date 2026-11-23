/*
 * Memory-mapped Arm Generic Timer, CNTCTLBase and CNTBase/CNTEL0Base.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Register semantics follow Arm ARM DDI 0406C.d, Appendix D5. The
 * unsigned compare and rollover scheduling follow the Arm CPU generic
 * timer in target/arm/helper.c (Copyright (c) 2003-2005 Fabrice Bellard,
 * Copyright (c) 2005-2007 CodeSourcery, GPL-2.0-or-later).
 *
 * A frame owns independent physical and optional virtual timers. It is
 * not a second register view of any CPU's private timer. The system
 * counter shares the CPU's QEMU virtual clock and epoch. The board selects
 * matching exact or legacy-quantized conversion modes on both devices.
 * See tb371fc/docs/mmio-timer.md for the measured rate and its limits.
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/bitops.h"
#include "qemu/host-utils.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "hw/timer/arm-arch-timer-mmio.h"
#include "hw/timer/arm-gt-counter.h"
#include "migration/vmstate.h"

#define CNTFRQ_CONTROL  0x000
#define CNTNSAR         0x004
#define CNTTIDR         0x008
#define CNTACR_BASE     0x040
#define CNTVOFF_BASE    0x080

#define CNTPCT          0x000
#define CNTVCT          0x008
#define CNTFRQ          0x010
#define CNTEL0ACR       0x014
#define CNTVOFF         0x018
#define CNTP_CVAL       0x020
#define CNTP_TVAL       0x028
#define CNTP_CTL        0x02c
#define CNTV_CVAL       0x030
#define CNTV_TVAL       0x038
#define CNTV_CTL        0x03c

#define ACR_RPCT        BIT(0)
#define ACR_RVCT        BIT(1)
#define ACR_RFRQ        BIT(2)
#define ACR_RVOFF       BIT(3)
#define ACR_RWVT        BIT(4)
#define ACR_RWPT        BIT(5)
#define EL0_PCTEN       BIT(0)
#define EL0_VCTEN       BIT(1)
#define EL0_VTEN        BIT(8)
#define EL0_PTEN        BIT(9)
#define CTL_ENABLE      BIT(0)
#define CTL_IMASK       BIT(1)
#define CTL_ISTATUS     BIT(2)

static bool frame_virtual(ArmArchTimerMmioFrame *f)
{
    return f->parent->virtual_frame_mask & BIT(f->index);
}

static bool frame_el0(ArmArchTimerMmioFrame *f)
{
    return f->parent->el0_frame_mask & BIT(f->index);
}

static bool frame_access(ArmArchTimerMmioFrame *f, MemTxAttrs attrs)
{
    return attrs.secure || (f->parent->cntnsar & BIT(f->index));
}

static uint32_t frame_acr_mask(ArmArchTimerMmioFrame *f)
{
    return ACR_RPCT | ACR_RFRQ | ACR_RWPT |
           (frame_virtual(f) ? ACR_RVCT | ACR_RVOFF | ACR_RWVT : 0);
}

static uint32_t frame_el0_mask(ArmArchTimerMmioFrame *f)
{
    if (!frame_el0(f)) {
        return 0;
    }
    return EL0_PCTEN | EL0_PTEN |
           (frame_virtual(f) ? EL0_VCTEN | EL0_VTEN : 0);
}

static uint64_t physical_count(ArmArchTimerMmioState *s)
{
    return arm_gt_ns_to_ticks(qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL),
                              s->frequency, s->exact_frequency);
}

static uint64_t frame_count(ArmArchTimerMmioFrame *f, unsigned int timer)
{
    return physical_count(f->parent) - (timer ? f->voff : 0);
}

static bool timer_status(ArmArchTimerMmioFrame *f, unsigned int timer)
{
    return (f->ctl[timer] & CTL_ENABLE) &&
           frame_count(f, timer) >= f->cval[timer];
}

static void timer_update(ArmArchTimerMmioFrame *f, unsigned int timer)
{
    ArmArchTimerMmioState *s = f->parent;
    uint64_t count, offset, nexttick;
    bool status;

    if (!(f->ctl[timer] & CTL_ENABLE) ||
        (timer && !frame_virtual(f))) {
        timer_del(&f->timer[timer]);
        qemu_set_irq(f->irq[timer], 0);
        return;
    }

    count = physical_count(s);
    offset = timer ? f->voff : 0;
    /* All compare arithmetic is unsigned, including virtual wraparound. */
    status = count - offset >= f->cval[timer];
    qemu_set_irq(f->irq[timer], status && !(f->ctl[timer] & CTL_IMASK));

    if (status) {
        /* A virtual offset ahead of the count eventually wraps back to 0. */
        nexttick = offset > count ? offset : UINT64_MAX;
    } else if (uadd64_overflow(count, f->cval[timer] - (count - offset),
                              &nexttick)) {
        nexttick = UINT64_MAX;
    }

    timer_mod_ns(&f->timer[timer],
                 arm_gt_ticks_to_ns(nexttick, s->frequency,
                                    s->exact_frequency));
}

static void physical_timer_cb(void *opaque)
{
    timer_update(opaque, 0);
}

static void virtual_timer_cb(void *opaque)
{
    timer_update(opaque, 1);
}

/* A 64-bit access is atomic only for an architected 64-bit register. */
static bool frame_accepts(void *opaque, hwaddr addr, unsigned int size,
                          bool is_write, MemTxAttrs attrs)
{
    if (size == 4 && !(addr & 3)) {
        return true;
    }
    return size == 8 && (addr == CNTPCT || addr == CNTVCT ||
                        addr == CNTVOFF || addr == CNTP_CVAL ||
                        addr == CNTV_CVAL);
}

static bool control_accepts(void *opaque, hwaddr addr, unsigned int size,
                            bool is_write, MemTxAttrs attrs)
{
    if (size == 4 && !(addr & 3)) {
        return true;
    }
    return size == 8 && !(addr & 7) &&
           addr >= CNTVOFF_BASE && addr < CNTVOFF_BASE + 8 * 8;
}

static uint64_t read_part(uint64_t value, hwaddr addr, unsigned int size)
{
    if (size == 8) {
        return value;
    }
    return extract64(value, (addr & 4) * 8, 32);
}

static uint64_t write_part(uint64_t old, uint64_t value,
                           hwaddr addr, unsigned int size)
{
    if (size == 8) {
        return value;
    }
    return deposit64(old, (addr & 4) * 8, 32, value);
}

/*
 * Permission checks apply to Secure accesses too, apart from CNTNSAR.
 * The EL0 view adds a second set of permissions; it cannot grant access
 * denied by CNTACR. CNTEL0ACR and CNTVOFF are never in the EL0 view.
 */
static bool view_access(ArmArchTimerMmioView *v, uint32_t acr, uint32_t el0)
{
    return (v->frame->cntacr & acr) && (!v->el0 || (v->frame->el0acr & el0));
}

static MemTxResult frame_read(void *opaque, hwaddr addr, uint64_t *value,
                              unsigned int size, MemTxAttrs attrs)
{
    ArmArchTimerMmioView *v = opaque;
    ArmArchTimerMmioFrame *f = v->frame;
    uint64_t data = 0;

    *value = 0;
    if (!frame_access(f, attrs) || (v->el0 && !frame_el0(f))) {
        return MEMTX_OK;
    }

    switch (addr) {
    case CNTPCT:
    case CNTPCT + 4:
        if (view_access(v, ACR_RPCT, EL0_PCTEN)) {
            data = physical_count(f->parent);
        }
        break;
    case CNTVCT:
    case CNTVCT + 4:
        if (frame_virtual(f) && view_access(v, ACR_RVCT, EL0_VCTEN)) {
            data = frame_count(f, 1);
        }
        break;
    case CNTFRQ:
        if (view_access(v, ACR_RFRQ, EL0_PCTEN | EL0_VCTEN)) {
            *value = f->parent->cntfrq;
        }
        return MEMTX_OK;
    case CNTEL0ACR:
        *value = v->el0 ? 0 : f->el0acr;
        return MEMTX_OK;
    case CNTVOFF:
    case CNTVOFF + 4:
        if (!v->el0 && frame_virtual(f) && (f->cntacr & ACR_RVOFF)) {
            data = f->voff;
        }
        break;
    case CNTP_CVAL:
    case CNTP_CVAL + 4:
        if (view_access(v, ACR_RWPT, EL0_PTEN)) {
            data = f->cval[0];
        }
        break;
    case CNTV_CVAL:
    case CNTV_CVAL + 4:
        if (frame_virtual(f) && view_access(v, ACR_RWVT, EL0_VTEN)) {
            data = f->cval[1];
        }
        break;
    case CNTP_TVAL:
    case CNTV_TVAL:
    {
        unsigned int timer = addr == CNTV_TVAL;
        if ((!timer || frame_virtual(f)) &&
            view_access(v, timer ? ACR_RWVT : ACR_RWPT,
                        timer ? EL0_VTEN : EL0_PTEN)) {
            *value = (uint32_t)(f->cval[timer] - frame_count(f, timer));
        }
        return MEMTX_OK;
    }
    case CNTP_CTL:
    case CNTV_CTL:
    {
        unsigned int timer = addr == CNTV_CTL;
        if ((!timer || frame_virtual(f)) &&
            view_access(v, timer ? ACR_RWVT : ACR_RWPT,
                        timer ? EL0_VTEN : EL0_PTEN)) {
            *value = f->ctl[timer] |
                     (timer_status(f, timer) ? CTL_ISTATUS : 0);
        }
        return MEMTX_OK;
    }
    default:
        /* Reserved registers, including unclaimed implementation IDs: RAZ. */
        break;
    }
    *value = read_part(data, addr, size);
    return MEMTX_OK;
}

static MemTxResult frame_write(void *opaque, hwaddr addr, uint64_t value,
                               unsigned int size, MemTxAttrs attrs)
{
    ArmArchTimerMmioView *v = opaque;
    ArmArchTimerMmioFrame *f = v->frame;
    unsigned int timer;

    if (!frame_access(f, attrs) || (v->el0 && !frame_el0(f))) {
        return MEMTX_OK;
    }
    if (addr == CNTEL0ACR) {
        if (!v->el0) {
            f->el0acr = value & frame_el0_mask(f);
        }
        return MEMTX_OK;
    }
    if (addr < CNTP_CVAL || addr > CNTV_CTL) {
        /* Counter, frequency, offset and reserved register writes ignored. */
        return MEMTX_OK;
    }
    timer = addr >= CNTV_CVAL;
    if ((timer && !frame_virtual(f)) ||
        !view_access(v, timer ? ACR_RWVT : ACR_RWPT,
                     timer ? EL0_VTEN : EL0_PTEN)) {
        return MEMTX_OK;
    }
    switch (addr & 0xf) {
    case 0:
    case 4:
        f->cval[timer] = write_part(f->cval[timer], value, addr, size);
        break;
    case 8:
        f->cval[timer] = frame_count(f, timer) + (int64_t)(int32_t)value;
        break;
    case 12:
        /* ISTATUS is read-only; it is always derived from count vs compare. */
        f->ctl[timer] = value & (CTL_ENABLE | CTL_IMASK);
        break;
    }
    timer_update(f, timer);
    return MEMTX_OK;
}

static MemTxResult control_read(void *opaque, hwaddr addr, uint64_t *value,
                                unsigned int size, MemTxAttrs attrs)
{
    ArmArchTimerMmioState *s = opaque;
    unsigned int index;

    *value = 0;
    switch (addr) {
    case CNTFRQ_CONTROL:
        *value = attrs.secure ? s->cntfrq : 0;
        return MEMTX_OK;
    case CNTNSAR:
        *value = attrs.secure ? s->cntnsar : 0;
        return MEMTX_OK;
    case CNTTIDR:
        for (index = 0; index < s->num_frames; index++) {
            *value |= (1 | (frame_virtual(&s->frame[index]) ? 2 : 0) |
                       (frame_el0(&s->frame[index]) ? 4 : 0)) << (index * 4);
        }
        return MEMTX_OK;
    default:
        break;
    }
    if (addr >= CNTACR_BASE && addr < CNTACR_BASE + 4 * s->num_frames) {
        index = (addr - CNTACR_BASE) / 4;
        if (frame_access(&s->frame[index], attrs)) {
            *value = s->frame[index].cntacr;
        }
    } else if (addr >= CNTVOFF_BASE &&
               addr < CNTVOFF_BASE + 8 * s->num_frames) {
        index = (addr - CNTVOFF_BASE) / 8;
        if (frame_access(&s->frame[index], attrs) &&
            frame_virtual(&s->frame[index])) {
            *value = read_part(s->frame[index].voff, addr, size);
        }
    }
    return MEMTX_OK;
}

static MemTxResult control_write(void *opaque, hwaddr addr, uint64_t value,
                                 unsigned int size, MemTxAttrs attrs)
{
    ArmArchTimerMmioState *s = opaque;
    unsigned int index;

    switch (addr) {
    case CNTFRQ_CONTROL:
        if (attrs.secure) {
            /* A software frequency annotation, not a clock-rate control. */
            s->cntfrq = value;
        }
        return MEMTX_OK;
    case CNTNSAR:
        if (attrs.secure) {
            s->cntnsar = value & ((1U << s->num_frames) - 1);
        }
        return MEMTX_OK;
    case CNTTIDR:
        return MEMTX_OK;
    default:
        break;
    }
    if (addr >= CNTACR_BASE && addr < CNTACR_BASE + 4 * s->num_frames) {
        index = (addr - CNTACR_BASE) / 4;
        if (frame_access(&s->frame[index], attrs)) {
            s->frame[index].cntacr = value & frame_acr_mask(&s->frame[index]);
        }
    } else if (addr >= CNTVOFF_BASE &&
               addr < CNTVOFF_BASE + 8 * s->num_frames) {
        index = (addr - CNTVOFF_BASE) / 8;
        if (frame_access(&s->frame[index], attrs) &&
            frame_virtual(&s->frame[index])) {
            s->frame[index].voff = write_part(s->frame[index].voff, value,
                                             addr, size);
            timer_update(&s->frame[index], 1);
        }
    }
    return MEMTX_OK;
}

static const MemoryRegionOps frame_ops = {
    .read_with_attrs = frame_read,
    .write_with_attrs = frame_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 8,
        .accepts = frame_accepts,
    },
    .impl.min_access_size = 4,
    .impl.max_access_size = 8,
};

static const MemoryRegionOps control_ops = {
    .read_with_attrs = control_read,
    .write_with_attrs = control_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 8,
        .accepts = control_accepts,
    },
    .impl.min_access_size = 4,
    .impl.max_access_size = 8,
};

static void mmio_timer_reset_hold(Object *obj, ResetType type)
{
    ArmArchTimerMmioState *s = ARM_ARCH_TIMER_MMIO(obj);
    unsigned int i, t;

    s->cntfrq = s->frequency;
    s->cntnsar = s->reset_ns_access;
    for (i = 0; i < ARM_ARCH_TIMER_MMIO_MAX_FRAMES; i++) {
        ArmArchTimerMmioFrame *f = &s->frame[i];
        f->cntacr = 0;
        f->el0acr = 0;
        f->voff = 0;
        for (t = 0; t < 2; t++) {
            f->cval[t] = 0;
            f->ctl[t] = 0;
            timer_del(&f->timer[t]);
            qemu_set_irq(f->irq[t], 0);
        }
    }
}

static bool mmio_timer_exact_needed(void *opaque)
{
    return ARM_ARCH_TIMER_MMIO(opaque)->exact_frequency;
}

static int mmio_timer_exact_loaded(void *opaque, int version_id)
{
    ArmArchTimerMmioState *s = opaque;

    if (!s->exact_frequency) {
        return -EINVAL;
    }
    s->exact_frequency_received = true;
    return 0;
}

static const VMStateDescription vmstate_mmio_timer_exact = {
    .name = TYPE_ARM_ARCH_TIMER_MMIO "/exact-frequency",
    .version_id = 1,
    .minimum_version_id = 1,
    .needed = mmio_timer_exact_needed,
    .post_load = mmio_timer_exact_loaded,
    .fields = (const VMStateField[]) {
        VMSTATE_END_OF_LIST()
    },
};

static int mmio_timer_pre_load(void *opaque)
{
    ARM_ARCH_TIMER_MMIO(opaque)->exact_frequency_received = false;
    return 0;
}

static int mmio_timer_post_load(void *opaque, int version_id)
{
    ArmArchTimerMmioState *s = opaque;
    unsigned int i, t;

    if (s->exact_frequency != s->exact_frequency_received) {
        return -EINVAL;
    }
    if (s->cntnsar & ~((1U << s->num_frames) - 1)) {
        return -EINVAL;
    }
    for (i = 0; i < s->num_frames; i++) {
        ArmArchTimerMmioFrame *f = &s->frame[i];
        if ((f->cntacr & ~frame_acr_mask(f)) ||
            (f->el0acr & ~frame_el0_mask(f))) {
            return -EINVAL;
        }
        for (t = 0; t < 2; t++) {
            if (f->ctl[t] & ~(CTL_ENABLE | CTL_IMASK)) {
                return -EINVAL;
            }
            timer_update(f, t);
        }
    }
    return 0;
}

static const VMStateDescription vmstate_mmio_timer_frame = {
    .name = TYPE_ARM_ARCH_TIMER_MMIO "/frame",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(cntacr, ArmArchTimerMmioFrame),
        VMSTATE_UINT32(el0acr, ArmArchTimerMmioFrame),
        VMSTATE_UINT64(voff, ArmArchTimerMmioFrame),
        VMSTATE_UINT64_ARRAY(cval, ArmArchTimerMmioFrame, 2),
        VMSTATE_UINT32_ARRAY(ctl, ArmArchTimerMmioFrame, 2),
        VMSTATE_TIMER_ARRAY(timer, ArmArchTimerMmioFrame, 2),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_mmio_timer = {
    .name = TYPE_ARM_ARCH_TIMER_MMIO,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = mmio_timer_pre_load,
    .post_load = mmio_timer_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(num_frames, ArmArchTimerMmioState),
        VMSTATE_UINT64_EQUAL(frequency, ArmArchTimerMmioState),
        VMSTATE_UINT8_EQUAL(virtual_frame_mask, ArmArchTimerMmioState),
        VMSTATE_UINT8_EQUAL(el0_frame_mask, ArmArchTimerMmioState),
        VMSTATE_UINT8_EQUAL(reset_ns_access, ArmArchTimerMmioState),
        VMSTATE_UINT32(cntfrq, ArmArchTimerMmioState),
        VMSTATE_UINT32(cntnsar, ArmArchTimerMmioState),
        VMSTATE_STRUCT_ARRAY(frame, ArmArchTimerMmioState,
                             ARM_ARCH_TIMER_MMIO_MAX_FRAMES, 0,
                             vmstate_mmio_timer_frame, ArmArchTimerMmioFrame),
        VMSTATE_END_OF_LIST()
    },
    .subsections = (const VMStateDescription * const []) {
        &vmstate_mmio_timer_exact,
        NULL,
    },
};

static void mmio_timer_realize(DeviceState *dev, Error **errp)
{
    ArmArchTimerMmioState *s = ARM_ARCH_TIMER_MMIO(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);
    unsigned int i, view;

    if (s->num_frames == 0 ||
        s->num_frames > ARM_ARCH_TIMER_MMIO_MAX_FRAMES) {
        error_setg(errp, "num-frames must be in [1, 8]");
        return;
    }
    if (s->frequency == 0 || s->frequency > NANOSECONDS_PER_SECOND) {
        error_setg(errp, "frequency must be in [1, 1000000000]");
        return;
    }
    if ((s->virtual_frame_mask | s->el0_frame_mask | s->reset_ns_access) &
        ~((1U << s->num_frames) - 1)) {
        error_setg(errp, "frame masks include an unimplemented frame");
        return;
    }
    s->period_ns = NANOSECONDS_PER_SECOND / s->frequency;
    memory_region_init_io(&s->control, OBJECT(s), &control_ops, s,
                          TYPE_ARM_ARCH_TIMER_MMIO ".control", 0x1000);
    sysbus_init_mmio(sbd, &s->control);

    for (i = 0; i < s->num_frames; i++) {
        ArmArchTimerMmioFrame *f = &s->frame[i];
        for (view = 0; view < 2; view++) {
            g_autofree char *name = g_strdup_printf(
                TYPE_ARM_ARCH_TIMER_MMIO ".frame%u%s", i,
                view ? ".el0" : "");
            f->view[view].frame = f;
            f->view[view].el0 = view;
            memory_region_init_io(&f->view[view].mr, OBJECT(s), &frame_ops,
                                  &f->view[view], name, 0x1000);
            sysbus_init_mmio(sbd, &f->view[view].mr);
        }
        sysbus_init_irq(sbd, &f->irq[0]);
        sysbus_init_irq(sbd, &f->irq[1]);
    }
}

static void mmio_timer_init(Object *obj)
{
    ArmArchTimerMmioState *s = ARM_ARCH_TIMER_MMIO(obj);
    unsigned int i;

    for (i = 0; i < ARM_ARCH_TIMER_MMIO_MAX_FRAMES; i++) {
        ArmArchTimerMmioFrame *f = &s->frame[i];
        f->parent = s;
        f->index = i;
        timer_init_ns(&f->timer[0], QEMU_CLOCK_VIRTUAL, physical_timer_cb, f);
        timer_init_ns(&f->timer[1], QEMU_CLOCK_VIRTUAL, virtual_timer_cb, f);
    }
}

static void mmio_timer_finalize(Object *obj)
{
    ArmArchTimerMmioState *s = ARM_ARCH_TIMER_MMIO(obj);
    unsigned int i;

    for (i = 0; i < ARM_ARCH_TIMER_MMIO_MAX_FRAMES; i++) {
        timer_del(&s->frame[i].timer[0]);
        timer_del(&s->frame[i].timer[1]);
    }
}

static const Property mmio_timer_properties[] = {
    DEFINE_PROP_UINT32("num-frames", ArmArchTimerMmioState, num_frames, 1),
    DEFINE_PROP_UINT64("frequency", ArmArchTimerMmioState, frequency, 19200000),
    DEFINE_PROP_BOOL("exact-frequency", ArmArchTimerMmioState,
                     exact_frequency, false),
    DEFINE_PROP_UINT8("virtual-frame-mask", ArmArchTimerMmioState,
                      virtual_frame_mask, 1),
    DEFINE_PROP_UINT8("el0-frame-mask", ArmArchTimerMmioState,
                      el0_frame_mask, 1),
    DEFINE_PROP_UINT8("reset-ns-access", ArmArchTimerMmioState,
                      reset_ns_access, 0),
};

static void mmio_timer_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Arm Generic Timer memory-mapped control and timer frames";
    dc->realize = mmio_timer_realize;
    dc->vmsd = &vmstate_mmio_timer;
    device_class_set_props(dc, mmio_timer_properties);
    rc->phases.hold = mmio_timer_reset_hold;
}

static const TypeInfo mmio_timer_type = {
    .name = TYPE_ARM_ARCH_TIMER_MMIO,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ArmArchTimerMmioState),
    .instance_init = mmio_timer_init,
    .instance_finalize = mmio_timer_finalize,
    .class_init = mmio_timer_class_init,
};

static void mmio_timer_register_types(void)
{
    type_register_static(&mmio_timer_type);
}
type_init(mmio_timer_register_types)
