/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * SM8250 ANOC1/ANOC2 reference debug adapter, not complete QSMMUv500.
 *
 * Register protocol: official OnePlus SM8250 arm-smmu.c at
 * 94304e12a3509eac7b3716bf574ce0b1fcaacbae, qsmmuv500_tbu_halt and
 * qsmmuv500_iova_to_phys. Addresses/ranges: allowlisted TB371FC live DT.
 *
 * HALT closes the modeled DMA admission gate immediately and ACK follows a
 * reference timer. Gated DMA fails: there is no hardware transaction stall,
 * queue drain, clock controller, secure firmware or silicon timing claim.
 * ECATS uses the existing SMMUv2 page-table walker and never fabricates a
 * successful address for a bypass stream or failed translation.
 */
#include "qemu/osdep.h"
#include "hw/arm/qcom-sm8250-tbu.h"
#include "hw/arm/arm-smmuv2.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define TBU_HALT BIT_ULL(16)
#define TBU_SID_MASK UINT64_C(0x3ff)
#define TBU_AXUSER (UINT64_C(255) << 36)
#define TBU_VA_LIMIT (UINT64_C(1) << 36)
#define TBU_FAULT UINT64_C(1)

enum {
    TBU_IDLE,
    TBU_HALT_PENDING,
    TBU_QUERY_PENDING,
};

typedef struct QcomSm8250TbuBank {
    QcomSm8250TbuState *parent;
    unsigned index;
    MemoryRegion base, status;
    QEMUTimer *timer;
    uint64_t halt, va, trigger, par, axuser;
    uint32_t phase;
    bool ack, acquired;
    int64_t remaining_ns;
} QcomSm8250TbuBank;

struct QcomSm8250TbuState {
    SysBusDevice parent_obj;
    DeviceState *smmu;
    MemoryRegion tcu;
    uint32_t reference_profile;
    uint32_t reference_version;
    uint64_t latency_ns;
    QcomSm8250TbuBank bank[QCOM_SM8250_TBU_COUNT];
};

static void tbu_update_gates(QcomSm8250TbuState *s)
{
    uint32_t groups = 0;
    unsigned i;

    if (s->reference_profile) {
        for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
            if (s->bank[i].halt & TBU_HALT) {
                groups |= UINT32_C(1) << i;
            }
        }
    }
    if (s->smmu) {
        arm_smmuv2_set_debug_halt_groups(s->smmu, groups);
    }
}

static bool tbu_valid_trigger(uint64_t value)
{
    unsigned prot = (value >> 6) & 7;

    /* The driver uses cacheable nonsecure data accesses; user or privileged. */
    return !(value & ~UINT64_C(0x1ff)) && (value & 1) &&
           ((value >> 2) & 15) == 15 && (prot == 2 || prot == 3);
}

static void tbu_timer(void *opaque)
{
    QcomSm8250TbuBank *b = opaque;
    QcomSm8250TbuState *s = b->parent;
    uint64_t pa;
    MemTxAttrs attrs = { .secure = false };
    IOMMUAccessFlags access;
    uint16_t sid;

    if (b->phase == TBU_HALT_PENDING) {
        b->ack = !!(b->halt & TBU_HALT);
    } else if (b->phase == TBU_QUERY_PENDING) {
        b->par = TBU_FAULT;
        attrs.user = !((b->trigger >> 6) & 1);
        access = (b->trigger & 2) ? IOMMU_WO : IOMMU_RO;
        sid = b->index * 0x400 + (b->halt & TBU_SID_MASK);
        if (b->ack && b->acquired && (b->halt & TBU_HALT) &&
            arm_smmuv2_debug_translate(s->smmu, sid, b->va, access, attrs,
                                       &pa) && pa < TBU_VA_LIMIT) {
            /* Vendor ECATS PAR places the 36-bit physical address at bit 12. */
            b->par = pa << 12;
        }
    }
    b->phase = TBU_IDLE;
    b->remaining_ns = -1;
}

static MemTxResult tbu_bad(hwaddr offset, unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_TBU
                  ": unsupported %s offset=0x%" HWADDR_PRIx " size=%u\n",
                  write ? "write" : "read", offset, size);
    return MEMTX_ERROR;
}

static bool tbu_decode(hwaddr offset, unsigned size, unsigned *reg,
                       unsigned *shift)
{
    if ((size != 4 && size != 8) || (offset & (size - 1))) {
        return false;
    }
    *reg = offset & ~7;
    *shift = (offset & 4) * 8;
    return true;
}

static MemTxResult tbu_read(void *opaque, hwaddr offset, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomSm8250TbuBank *b = opaque;
    unsigned reg, shift;
    uint64_t v;

    *value = 0;
    if (!b->parent->reference_profile ||
        !tbu_decode(offset, size, &reg, &shift)) {
        return tbu_bad(offset, size, false);
    }
    switch (reg) {
    case 0x00:
        v = b->halt;
        break;
    case 0x08:
        v = b->va;
        break;
    case 0x18:
        v = b->trigger;
        break;
    case 0x20:
        v = (b->ack ? 2 : 0) | (b->phase == TBU_QUERY_PENDING ? 1 : 0);
        break;
    case 0x28:
        v = b->par;
        break;
    case 0x30:
        v = b->axuser;
        break;
    default:
        return tbu_bad(offset, size, false);
    }
    *value = size == 4 ? (uint32_t)(v >> shift) : v;
    return MEMTX_OK;
}

static MemTxResult tbu_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomSm8250TbuBank *b = opaque;
    QcomSm8250TbuState *s = b->parent;
    unsigned reg, shift;
    uint64_t old, merged;

    if (!s->reference_profile || !tbu_decode(offset, size, &reg, &shift)) {
        return tbu_bad(offset, size, true);
    }
    switch (reg) {
    case 0x00:
        old = b->halt;
        break;
    case 0x08:
        old = b->va;
        break;
    case 0x18:
        old = b->trigger;
        break;
    case 0x30:
        old = b->axuser;
        break;
    default:
        return tbu_bad(offset, size, true);
    }
    merged = size == 8 ? value :
        (old & ~(UINT64_C(0xffffffff) << shift)) |
        ((uint64_t)(uint32_t)value << shift);

    if (reg == 0x00) {
        if (merged & ~(TBU_HALT | TBU_SID_MASK)) {
            return tbu_bad(offset, size, true);
        }
        if (!(merged & TBU_HALT)) {
            timer_del(b->timer);
            b->phase = TBU_IDLE;
            b->ack = b->acquired = false;
            b->trigger = 0;
            b->par = TBU_FAULT;
            b->remaining_ns = -1;
        } else if (b->phase == TBU_QUERY_PENDING) {
            return tbu_bad(offset, size, true);
        } else if (!(old & TBU_HALT)) {
            b->phase = TBU_HALT_PENDING;
            b->ack = false;
            timer_mod(b->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                       s->latency_ns);
        }
        b->halt = merged;
        tbu_update_gates(s);
        return MEMTX_OK;
    }

    if (reg == 0x18 && !merged) {
        /* Explicit query cancellation must not execute on a later timer. */
        if (b->phase == TBU_QUERY_PENDING) {
            timer_del(b->timer);
            b->phase = TBU_IDLE;
            b->remaining_ns = -1;
            b->par = TBU_FAULT;
        }
        b->trigger = 0;
        return MEMTX_OK;
    }
    if (b->phase == TBU_QUERY_PENDING) {
        return tbu_bad(offset, size, true);
    }
    if (reg == 0x08) {
        if (merged >= TBU_VA_LIMIT) {
            return tbu_bad(offset, size, true);
        }
        b->va = merged;
    } else if (reg == 0x30) {
        if (merged & ~TBU_AXUSER) {
            return tbu_bad(offset, size, true);
        }
        b->axuser = merged;
    } else {
        if (!b->ack || !b->acquired || !(b->halt & TBU_HALT) ||
            b->phase != TBU_IDLE || b->trigger || b->axuser != TBU_AXUSER ||
            !tbu_valid_trigger(merged)) {
            return tbu_bad(offset, size, true);
        }
        b->trigger = merged;
        b->par = TBU_FAULT;
        b->phase = TBU_QUERY_PENDING;
        timer_mod(b->timer, qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                   s->latency_ns);
    }
    return MEMTX_OK;
}

static MemTxResult tbu_status_read(void *opaque, hwaddr offset, uint64_t *value,
                                   unsigned size, MemTxAttrs attrs)
{
    QcomSm8250TbuBank *b = opaque;

    *value = 0;
    if (!b->parent->reference_profile || offset || size != 4) {
        return tbu_bad(offset, size, false);
    }
    if (b->ack && !b->acquired) {
        b->acquired = true;
        *value = 1;
    }
    return MEMTX_OK;
}

static MemTxResult tbu_status_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size, MemTxAttrs attrs)
{
    QcomSm8250TbuBank *b = opaque;

    if (!b->parent->reference_profile || offset || size != 4 || value ||
        b->phase == TBU_QUERY_PENDING) {
        return tbu_bad(offset, size, true);
    }
    b->acquired = false;
    return MEMTX_OK;
}

static MemTxResult tcu_read(void *opaque, hwaddr offset, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomSm8250TbuState *s = opaque;

    *value = 0;
    if (s->reference_profile && offset == 0x18 && size == 4) {
        *value = s->reference_version;
        return MEMTX_OK;
    }
    return tbu_bad(offset, size, false);
}

static MemTxResult tcu_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    return tbu_bad(offset, size, true);
}

#define TBU_OPS(_read, _write) { \
    .read_with_attrs = _read, .write_with_attrs = _write, \
    .endianness = DEVICE_LITTLE_ENDIAN, \
    .valid = { .min_access_size = 1, .max_access_size = 8, \
               .unaligned = true }, \
    .impl = { .min_access_size = 1, .max_access_size = 8, \
              .unaligned = true }, \
}
static const MemoryRegionOps tbu_ops = TBU_OPS(tbu_read, tbu_write);
static const MemoryRegionOps tbu_status_ops =
    TBU_OPS(tbu_status_read, tbu_status_write);
static const MemoryRegionOps tcu_ops = TBU_OPS(tcu_read, tcu_write);

static void tbu_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250TbuState *s = QCOM_SM8250_TBU(obj);
    unsigned i;

    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        QcomSm8250TbuBank *b = &s->bank[i];

        timer_del(b->timer);
        b->halt = b->va = b->trigger = b->axuser = 0;
        b->par = TBU_FAULT;
        b->phase = TBU_IDLE;
        b->ack = b->acquired = false;
        b->remaining_ns = -1;
    }
    tbu_update_gates(s);
}

static int tbu_pre_save(void *opaque)
{
    QcomSm8250TbuState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        QcomSm8250TbuBank *b = &s->bank[i];

        b->remaining_ns = timer_pending(b->timer) ?
            MAX(timer_expire_time_ns(b->timer) - now, 0) : -1;
    }
    return 0;
}

static int tbu_post_load(void *opaque, int version)
{
    QcomSm8250TbuState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    /* Validate all banks before changing admission gates or scheduling work. */
    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        QcomSm8250TbuBank *b = &s->bank[i];

        if ((b->halt & ~(TBU_HALT | TBU_SID_MASK)) ||
            b->va >= TBU_VA_LIMIT || (b->axuser & ~TBU_AXUSER) ||
            (b->trigger && !tbu_valid_trigger(b->trigger)) ||
            (b->par != TBU_FAULT &&
             ((b->par & 0xfff) || b->par >> 48)) ||
            b->phase > TBU_QUERY_PENDING ||
            (b->ack && !(b->halt & TBU_HALT)) ||
            (b->acquired && !b->ack) ||
            (!(b->halt & TBU_HALT) &&
             (b->phase != TBU_IDLE || b->trigger || b->par != TBU_FAULT)) ||
            ((b->halt & TBU_HALT) && b->phase == TBU_IDLE && !b->ack) ||
            (b->phase == TBU_IDLE && b->remaining_ns != -1) ||
            (b->phase != TBU_IDLE &&
             (b->remaining_ns < 0 || b->remaining_ns > s->latency_ns)) ||
            (b->phase == TBU_HALT_PENDING &&
             (!(b->halt & TBU_HALT) || b->ack || b->acquired ||
              b->trigger || b->par != TBU_FAULT)) ||
            (b->phase == TBU_QUERY_PENDING &&
             (!b->ack || !b->acquired || !b->trigger ||
              b->axuser != TBU_AXUSER || b->par != TBU_FAULT)) ||
            (!s->reference_profile &&
             (b->halt || b->va || b->trigger || b->axuser ||
              b->par != TBU_FAULT || b->phase || b->ack || b->acquired))) {
            error_report(TYPE_QCOM_SM8250_TBU ": invalid incoming bank %u", i);
            return -EINVAL;
        }
    }
    tbu_update_gates(s);
    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        QcomSm8250TbuBank *b = &s->bank[i];

        timer_del(b->timer);
        if (b->phase != TBU_IDLE) {
            timer_mod(b->timer, now + b->remaining_ns);
        }
    }
    return 0;
}

static const VMStateDescription vmstate_tbu_bank = {
    .name = TYPE_QCOM_SM8250_TBU "/bank",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT64(halt, QcomSm8250TbuBank),
        VMSTATE_UINT64(va, QcomSm8250TbuBank),
        VMSTATE_UINT64(trigger, QcomSm8250TbuBank),
        VMSTATE_UINT64(par, QcomSm8250TbuBank),
        VMSTATE_UINT64(axuser, QcomSm8250TbuBank),
        VMSTATE_UINT32(phase, QcomSm8250TbuBank),
        VMSTATE_BOOL(ack, QcomSm8250TbuBank),
        VMSTATE_BOOL(acquired, QcomSm8250TbuBank),
        VMSTATE_INT64(remaining_ns, QcomSm8250TbuBank),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_tbu = {
    .name = TYPE_QCOM_SM8250_TBU,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = tbu_pre_save,
    .post_load = tbu_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250TbuState),
        VMSTATE_UINT32_EQUAL(reference_version, QcomSm8250TbuState),
        VMSTATE_UINT64_EQUAL(latency_ns, QcomSm8250TbuState),
        VMSTATE_STRUCT_ARRAY(bank, QcomSm8250TbuState, QCOM_SM8250_TBU_COUNT,
                              0, vmstate_tbu_bank, QcomSm8250TbuBank),
        VMSTATE_END_OF_LIST()
    },
};

static void tbu_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250TbuState *s = QCOM_SM8250_TBU(dev);

    if (!s->smmu || s->reference_profile > 1 ||
        (s->reference_version >> 24) != 2 ||
        !s->latency_ns || s->latency_ns > 1000000000) {
        error_setg(errp, TYPE_QCOM_SM8250_TBU
                   ": requires SMMUv2, profile 0/1, reference version 2.x "
                   "and latency in 1..1000000000 ns");
    }
}

static void tbu_init(Object *obj)
{
    QcomSm8250TbuState *s = QCOM_SM8250_TBU(obj);
    SysBusDevice *sbd = SYS_BUS_DEVICE(obj);
    unsigned i;

    memory_region_init_io(&s->tcu, obj, &tcu_ops, s,
                          "sm8250-tcu-reference", 0x20);
    sysbus_init_mmio(sbd, &s->tcu);
    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        QcomSm8250TbuBank *b = &s->bank[i];
        g_autofree char *name = g_strdup_printf("sm8250-anoc%u-debug", i + 1);
        g_autofree char *status = g_strdup_printf("sm8250-anoc%u-ecats", i + 1);

        b->parent = s;
        b->index = i;
        b->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, tbu_timer, b);
        memory_region_init_io(&b->base, obj, &tbu_ops, b, name, 0x1000);
        memory_region_init_io(&b->status, obj, &tbu_status_ops, b, status, 8);
        sysbus_init_mmio(sbd, &b->base);
        sysbus_init_mmio(sbd, &b->status);
    }
}

static void tbu_finalize(Object *obj)
{
    QcomSm8250TbuState *s = QCOM_SM8250_TBU(obj);
    unsigned i;

    for (i = 0; i < QCOM_SM8250_TBU_COUNT; i++) {
        timer_free(s->bank[i].timer);
    }
}

static const Property tbu_properties[] = {
    DEFINE_PROP_LINK("smmu", QcomSm8250TbuState, smmu,
                     TYPE_ARM_SMMUV2, DeviceState *),
    DEFINE_PROP_UINT32("reference-profile", QcomSm8250TbuState,
                       reference_profile, 0),
    DEFINE_PROP_UINT32("reference-version", QcomSm8250TbuState,
                       reference_version, 0x02000000),
    DEFINE_PROP_UINT64("operation-latency-ns", QcomSm8250TbuState,
                       latency_ns, 1000),
};

static void tbu_class_init(ObjectClass *oc, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(oc);
    ResettableClass *rc = RESETTABLE_CLASS(oc);

    dc->desc = "SM8250 reference ANOC TBU debug/ECATS adapter";
    dc->realize = tbu_realize;
    dc->vmsd = &vmstate_tbu;
    dc->user_creatable = false;
    device_class_set_props(dc, tbu_properties);
    rc->phases.hold = tbu_reset_hold;
}

static const TypeInfo tbu_type = {
    .name = TYPE_QCOM_SM8250_TBU,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250TbuState),
    .instance_init = tbu_init,
    .instance_finalize = tbu_finalize,
    .class_init = tbu_class_init,
};

static void tbu_register_types(void)
{
    type_register_static(&tbu_type);
}

type_init(tbu_register_types)
