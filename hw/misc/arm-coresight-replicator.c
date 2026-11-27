/*
 * CoreSight dynamic replicator APB control reference for DT part 0x909.
 *
 * The TB371FC's original DT supplies two instances. Their filter, software-lock
 * and claim state is independent and migratable. No ATB producer or trace
 * routing datapath is attached; register tests do not establish trace delivery.
 *
 * The part/designer matches the original DT's AMBA override. PIDR2 follows
 * the Arm reference component revision; it is not a silicon measurement.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/arm-coresight-replicator.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define REPLICATOR_PROFILE 0x52504c01U
#define REPLICATOR_UNLOCK  0xc5acce55U

#define REPLICATOR_IDFILTER0 0x000
#define REPLICATOR_IDFILTER1 0x004
#define REPLICATOR_ITCTRL    0xf00
#define REPLICATOR_CLAIMSET  0xfa0
#define REPLICATOR_CLAIMCLR  0xfa4
#define REPLICATOR_LAR       0xfb0
#define REPLICATOR_LSR       0xfb4
#define REPLICATOR_AUTH      0xfb8
#define REPLICATOR_DEVID     0xfc8
#define REPLICATOR_DEVTYPE   0xfcc

struct ArmCoresightReplicatorState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;

    uint32_t profile;
    uint32_t idfilter[2];
    uint32_t claim;
    bool locked;
};

static MemTxResult replicator_reject(hwaddr offset, unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "arm-coresight-replicator: rejected %s offset=0x%"
                  HWADDR_PRIx " size=%u\n", write ? "write" : "read",
                  offset, size);
    return MEMTX_ACCESS_ERROR;
}

/* Strict control bank; integration datapath registers and reserved holes are absent. */
static bool replicator_register(hwaddr offset)
{
    switch (offset) {
    case REPLICATOR_IDFILTER0: case REPLICATOR_IDFILTER1:
    case REPLICATOR_ITCTRL:
    case REPLICATOR_CLAIMSET: case REPLICATOR_CLAIMCLR:
    case REPLICATOR_LAR: case REPLICATOR_LSR:
    case REPLICATOR_AUTH: case REPLICATOR_DEVID: case REPLICATOR_DEVTYPE:
    case 0xfd0: case 0xfd4: case 0xfd8: case 0xfdc:
    case 0xfe0: case 0xfe4: case 0xfe8: case 0xfec:
    case 0xff0: case 0xff4: case 0xff8: case 0xffc:
        return true;
    default:
        return false;
    }
}

static MemTxResult replicator_read(void *opaque, hwaddr offset, uint64_t *value,
                                   unsigned size, MemTxAttrs attrs)
{
    ArmCoresightReplicatorState *s = opaque;

    *value = 0;
    if (size != 4 || !replicator_register(offset)) {
        return replicator_reject(offset, size, false);
    }
    switch (offset) {
    case REPLICATOR_IDFILTER0: *value = s->idfilter[0]; break;
    case REPLICATOR_IDFILTER1: *value = s->idfilter[1]; break;
    case REPLICATOR_CLAIMSET: *value = 0xf; break;
    case REPLICATOR_CLAIMCLR: *value = s->claim; break;
    case REPLICATOR_LSR: *value = 1 | (s->locked ? 2 : 0); break;
    case REPLICATOR_ITCTRL: case REPLICATOR_AUTH:
        break; /* Integration datapath and local authentication are absent. */
    case REPLICATOR_DEVID: *value = 2; break; /* Two master output ports. */
    case REPLICATOR_DEVTYPE: *value = 0x22; break;
    case 0xfd0: *value = 4; break;
    case 0xfd4: case 0xfd8: case 0xfdc: case 0xfec:
        break;
    case 0xfe0: *value = 0x09; break;
    case 0xfe4: *value = 0xb9; break;
    case 0xfe8: *value = 0x1b; break; /* DDI 0480A component r1p0. */
    case 0xff0: *value = 0x0d; break;
    case 0xff4: *value = 0x90; break;
    case 0xff8: *value = 0x05; break;
    case 0xffc: *value = 0xb1; break;
    default: /* LAR is write-only. */
        return replicator_reject(offset, size, false);
    }
    return MEMTX_OK;
}

static MemTxResult replicator_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size, MemTxAttrs attrs)
{
    ArmCoresightReplicatorState *s = opaque;
    uint32_t data = value;

    if (size != 4 || !replicator_register(offset)) {
        return replicator_reject(offset, size, true);
    }
    if (offset == REPLICATOR_LAR) {
        s->locked = data != REPLICATOR_UNLOCK;
        return MEMTX_OK;
    }
    if (s->locked) {
        return MEMTX_OK;
    }
    switch (offset) {
    case REPLICATOR_IDFILTER0: case REPLICATOR_IDFILTER1:
        s->idfilter[offset / 4] = data & 0xff;
        break;
    case REPLICATOR_ITCTRL:
        if (data) {
            return replicator_reject(offset, size, true);
        }
        break;
    case REPLICATOR_CLAIMSET:
        s->claim |= data & 0xf;
        break;
    case REPLICATOR_CLAIMCLR:
        s->claim &= ~(data & 0xf);
        break;
    default:
        /* Identification/status is never writable shadow storage. */
        return replicator_reject(offset, size, true);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps replicator_ops = {
    .read_with_attrs = replicator_read,
    .write_with_attrs = replicator_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Reject an invalid transaction whole instead of splitting it. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void replicator_reset(DeviceState *dev)
{
    ArmCoresightReplicatorState *s = ARM_CORESIGHT_REPLICATOR(dev);

    s->idfilter[0] = s->idfilter[1] = 0;
    s->claim = 0;
    s->locked = true;
}

static int replicator_post_load(void *opaque, int version)
{
    ArmCoresightReplicatorState *s = opaque;

    if (s->idfilter[0] > 0xff || s->idfilter[1] > 0xff || s->claim > 0xf) {
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_replicator = {
    .name = TYPE_ARM_CORESIGHT_REPLICATOR,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = replicator_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile, ArmCoresightReplicatorState),
        VMSTATE_UINT32_ARRAY(idfilter, ArmCoresightReplicatorState, 2),
        VMSTATE_UINT32(claim, ArmCoresightReplicatorState),
        VMSTATE_BOOL(locked, ArmCoresightReplicatorState),
        VMSTATE_END_OF_LIST()
    },
};

static void replicator_init(Object *obj)
{
    ArmCoresightReplicatorState *s = ARM_CORESIGHT_REPLICATOR(obj);

    s->profile = REPLICATOR_PROFILE;
    memory_region_init_io(&s->iomem, obj, &replicator_ops, s,
                          TYPE_ARM_CORESIGHT_REPLICATOR, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static void replicator_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "CoreSight dynamic replicator APB control reference";
    dc->user_creatable = false;
    dc->vmsd = &vmstate_replicator;
    device_class_set_legacy_reset(dc, replicator_reset);
}

static const TypeInfo replicator_info = {
    .name = TYPE_ARM_CORESIGHT_REPLICATOR,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ArmCoresightReplicatorState),
    .instance_init = replicator_init,
    .class_init = replicator_class_init,
};

static void replicator_register_types(void)
{
    type_register_static(&replicator_info);
}
type_init(replicator_register_types)
