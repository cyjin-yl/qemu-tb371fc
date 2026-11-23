/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm TCSR mutex driver-protocol model.
 *
 * Geometry: TB371FC live DT and current boot_a first DTB, independently
 * extracted in smem-mutex-draft-20261123 metadata.json, SHA256
 * 542935cce710f1f3251333f4ebb2bfc42218847899259ff1fa43ac0665ae5fd5.
 *
 * Protocol source: public TB371FC kernel mirror commit
 * 9d8abebf4ac4d45cf4cc11b5b6bc5e4483d10fd1,
 * drivers/hwspinlock/qcom_hwspinlock.c, SHA256
 * 19722f9e4e3a8ddeb11500185745e8bf72911be1a3e751f5d62986d9d66e6778.
 * This is not claimed to be an official complete vendor source release.
 *
 * The driver writes APPS owner 1 and reads the owner back. It writes zero
 * on unlock even after warning about an unexpected owner. It does not
 * identify the initiating CPU. The hwspinlock core provides local SMP
 * exclusion before calling this driver.
 *
 * The nonzero-claim/held-winner rule is the explicit reference arbitration
 * contract required by that driver; native arbitration timing, owner bit
 * limits, reset values and security restrictions have not been measured.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-tcsr-mutex.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

struct QcomTcsrMutexState {
    SysBusDevice parent_obj;

    MemoryRegion mmio;
    bool boot_unlocked_reference;

    uint32_t owner[QCOM_TCSR_MUTEX_NUM_LOCKS];
    uint32_t owner_known;
    uint32_t saved_boot_unlocked_reference;
};

static bool mutex_decode(hwaddr addr, unsigned size, unsigned *slot)
{
    if (size != 4 || addr >= QCOM_TCSR_MUTEX_MMIO_SIZE ||
        addr % QCOM_TCSR_MUTEX_LOCK_STRIDE) {
        return false;
    }

    *slot = addr / QCOM_TCSR_MUTEX_LOCK_STRIDE;
    return *slot < QCOM_TCSR_MUTEX_NUM_LOCKS;
}

static MemTxResult mutex_read(void *opaque, hwaddr addr, uint64_t *data,
                              unsigned size, MemTxAttrs attrs)
{
    QcomTcsrMutexState *s = opaque;
    unsigned slot;

    *data = 0;
    if (!mutex_decode(addr, size, &slot)) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: unsupported offset 0x%" HWADDR_PRIx " size %u\n",
                      TYPE_QCOM_TCSR_MUTEX, addr, size);
        return MEMTX_ERROR;
    }

    if (!(s->owner_known & (UINT32_C(1) << slot))) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: owner state unknown for lock %u\n",
                      TYPE_QCOM_TCSR_MUTEX, slot);
        return MEMTX_ERROR;
    }

    *data = s->owner[slot];
    return MEMTX_OK;
}

static MemTxResult mutex_write(void *opaque, hwaddr addr, uint64_t data,
                               unsigned size, MemTxAttrs attrs)
{
    QcomTcsrMutexState *s = opaque;
    uint32_t value = data;
    uint32_t bit;
    unsigned slot;

    if (!mutex_decode(addr, size, &slot)) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: unsupported offset 0x%" HWADDR_PRIx " size %u\n",
                      TYPE_QCOM_TCSR_MUTEX, addr, size);
        return MEMTX_ERROR;
    }

    bit = UINT32_C(1) << slot;
    if (!value) {
        /*
         * Do not infer an owner-only hardware release ACL from the driver's
         * warning. There is no owner token in the write-zero transaction.
         * This operation establishes a known free state even after an
         * unknown initial handoff.
         */
        s->owner[slot] = 0;
        s->owner_known |= bit;
        return MEMTX_OK;
    }

    if (!(s->owner_known & bit)) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: cannot claim lock %u with unknown owner state\n",
                      TYPE_QCOM_TCSR_MUTEX, slot);
        return MEMTX_ERROR;
    }

    /*
     * The default MemoryRegion device locking serializes MMIO callbacks.
     * A held slot retains its current owner, including a repeated write of
     * the same owner. No recursive count or initiating-CPU identity exists.
     */
    if (!s->owner[slot]) {
        s->owner[slot] = value;
    }
    return MEMTX_OK;
}

static const MemoryRegionOps mutex_ops = {
    .read_with_attrs = mutex_read,
    .write_with_attrs = mutex_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /*
     * Let the callbacks reject an entire malformed transaction rather than
     * allowing the memory subsystem to split it into accepted word writes.
     */
    .valid = {
        .min_access_size = 1,
        .max_access_size = 8,
        .unaligned = true,
    },
    .impl = {
        .min_access_size = 1,
        .max_access_size = 8,
        .unaligned = true,
    },
};

static void mutex_reset_hold(Object *obj, ResetType type)
{
    QcomTcsrMutexState *s = QCOM_TCSR_MUTEX(obj);

    memset(s->owner, 0, sizeof(s->owner));
    s->owner_known = s->boot_unlocked_reference ? UINT32_MAX : 0;
}

static int mutex_pre_save(void *opaque)
{
    QcomTcsrMutexState *s = opaque;

    s->saved_boot_unlocked_reference = s->boot_unlocked_reference;
    return 0;
}

static int mutex_post_load(void *opaque, int version_id)
{
    QcomTcsrMutexState *s = opaque;
    unsigned slot;

    if (s->saved_boot_unlocked_reference != s->boot_unlocked_reference) {
        return -EINVAL;
    }

    for (slot = 0; slot < QCOM_TCSR_MUTEX_NUM_LOCKS; slot++) {
        if (!(s->owner_known & (UINT32_C(1) << slot)) && s->owner[slot]) {
            return -EINVAL;
        }
    }
    return 0;
}

static const VMStateDescription vmstate_mutex = {
    .name = TYPE_QCOM_TCSR_MUTEX,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = mutex_pre_save,
    .post_load = mutex_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(saved_boot_unlocked_reference, QcomTcsrMutexState),
        VMSTATE_UINT32(owner_known, QcomTcsrMutexState),
        VMSTATE_UINT32_ARRAY(owner, QcomTcsrMutexState,
                             QCOM_TCSR_MUTEX_NUM_LOCKS),
        VMSTATE_END_OF_LIST()
    },
};

static void mutex_realize(DeviceState *dev, Error **errp)
{
    QcomTcsrMutexState *s = QCOM_TCSR_MUTEX(dev);

    memory_region_init_io(&s->mmio, OBJECT(s), &mutex_ops, s,
                          TYPE_QCOM_TCSR_MUTEX, QCOM_TCSR_MUTEX_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mmio);
}

static const Property mutex_properties[] = {
    DEFINE_PROP_BOOL("boot-unlocked-reference", QcomTcsrMutexState,
                     boot_unlocked_reference, false),
};

static void mutex_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm TCSR mutex driver-protocol model";
    dc->realize = mutex_realize;
    dc->vmsd = &vmstate_mutex;
    dc->user_creatable = false;
    device_class_set_props(dc, mutex_properties);
    rc->phases.hold = mutex_reset_hold;
}

static const TypeInfo mutex_info = {
    .name = TYPE_QCOM_TCSR_MUTEX,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomTcsrMutexState),
    .class_init = mutex_class_init,
};

static void mutex_register_types(void)
{
    type_register_static(&mutex_info);
}
type_init(mutex_register_types)
