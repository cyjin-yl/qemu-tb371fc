/*
 * TPDA identification and authentication-disabled management profile.
 *
 * The original TB371FC DT contains five AMBA part 0x000bb969 instances.
 * This model exposes their common CoreSight management registers, with both
 * non-invasive authentication classes permanently disabled. Invasive debug
 * support is absent. AUTHSTATUS=0x88 is a model configuration, not a measured
 * silicon reset value.
 *
 * No trace aggregation, port programming, packet production, flush engine or
 * integration datapath is implemented. Unsupported registers fail explicitly;
 * disabled authentication is not used to block the implemented register bank.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-coresight-tpda.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define TPDA_PROFILE 0x54504401U
#define TPDA_UNLOCK  0xc5acce55U
#define TPDA_AUTH_DISABLED 0x88U

#define TPDA_ITCTRL   0xf00
#define TPDA_CLAIMSET 0xfa0
#define TPDA_CLAIMCLR 0xfa4
#define TPDA_LAR      0xfb0
#define TPDA_LSR      0xfb4
#define TPDA_AUTH     0xfb8
#define TPDA_DEVARCH  0xfbc
#define TPDA_DEVID    0xfc8
#define TPDA_DEVTYPE  0xfcc

struct QcomCoresightTPDAState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;

    uint32_t profile;
    uint32_t claim;
    bool locked;
};

static MemTxResult tpda_reject(hwaddr offset, unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "qcom-coresight-tpda: rejected %s offset=0x%"
                  HWADDR_PRIx " size=%u\n", write ? "write" : "read",
                  offset, size);
    return MEMTX_ACCESS_ERROR;
}

/*
 * Recognize the vendor's control offsets so that the architectural software
 * lock ignores their writes while locked. Their unimplemented operations
 * remain errors when unlocked; no writable shadow bank pretends to aggregate.
 */
static bool tpda_register(hwaddr offset)
{
    if (offset <= 0x098 && !(offset & 3)) {
        return true; /* CR, 32 port controls, FPID/frequency/sync/flush. */
    }
    switch (offset) {
    case 0xefc: /* Vendor SPARE control. */
    case TPDA_ITCTRL:
    case TPDA_CLAIMSET: case TPDA_CLAIMCLR:
    case TPDA_LAR: case TPDA_LSR: case TPDA_AUTH: case TPDA_DEVARCH:
    case TPDA_DEVID: case TPDA_DEVTYPE:
    case 0xfd0: case 0xfd4: case 0xfd8: case 0xfdc:
    case 0xfe0: case 0xfe4: case 0xfe8: case 0xfec:
    case 0xff0: case 0xff4: case 0xff8: case 0xffc:
        return true;
    default:
        return false;
    }
}

static MemTxResult tpda_read(void *opaque, hwaddr offset, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomCoresightTPDAState *s = opaque;

    *value = 0;
    if (size != 4 || !tpda_register(offset)) {
        return tpda_reject(offset, size, false);
    }
    switch (offset) {
    case TPDA_CLAIMSET:
        *value = 0xf; /* Four implementation-selected software claim tags. */
        break;
    case TPDA_CLAIMCLR:
        *value = s->claim;
        break;
    case TPDA_LSR:
        *value = 1 | (s->locked ? 2 : 0);
        break;
    case TPDA_AUTH:
        *value = TPDA_AUTH_DISABLED;
        break;
    case TPDA_ITCTRL:
    case TPDA_DEVARCH:
        /* Integration is absent; no component architecture is advertised. */
        break;
    case 0xfd0:
        *value = 4; /* Arm JEP106 continuation, one 4-KiB aperture. */
        break;
    case 0xfd4: case 0xfd8: case 0xfdc: case 0xfec:
        break;
    case 0xfe0:
        *value = 0x69;
        break;
    case 0xfe4:
        *value = 0xb9;
        break;
    case 0xfe8:
        *value = 0x0b; /* Identity chosen to match the original DT override. */
        break;
    case 0xff0:
        *value = 0x0d;
        break;
    case 0xff4:
        *value = 0x90;
        break;
    case 0xff8:
        *value = 0x05;
        break;
    case 0xffc:
        *value = 0xb1;
        break;
    default:
        /* LAR is write-only; no speculative DEVID/DEVTYPE or datapath. */
        return tpda_reject(offset, size, false);
    }
    return MEMTX_OK;
}

static MemTxResult tpda_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomCoresightTPDAState *s = opaque;
    uint32_t data = value;

    if (size != 4 || !tpda_register(offset)) {
        return tpda_reject(offset, size, true);
    }
    if (offset == TPDA_LAR) {
        s->locked = data != TPDA_UNLOCK;
        return MEMTX_OK;
    }
    if (s->locked) {
        return MEMTX_OK;
    }
    switch (offset) {
    case TPDA_ITCTRL:
        break; /* RAZ/WI: there is no integration mode to enable. */
    case TPDA_CLAIMSET:
        s->claim |= data & 0xf;
        break;
    case TPDA_CLAIMCLR:
        s->claim &= ~(data & 0xf);
        break;
    default:
        return tpda_reject(offset, size, true);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps tpda_ops = {
    .read_with_attrs = tpda_read,
    .write_with_attrs = tpda_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* The callback rejects a malformed transaction without splitting writes. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void tpda_reset(DeviceState *dev)
{
    QcomCoresightTPDAState *s = QCOM_CORESIGHT_TPDA(dev);

    s->claim = 0;
    s->locked = true;
}

static int tpda_post_load(void *opaque, int version)
{
    QcomCoresightTPDAState *s = opaque;

    if (s->claim > 0xf) {
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_tpda = {
    .name = TYPE_QCOM_CORESIGHT_TPDA,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = tpda_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile, QcomCoresightTPDAState),
        VMSTATE_UINT32(claim, QcomCoresightTPDAState),
        VMSTATE_BOOL(locked, QcomCoresightTPDAState),
        VMSTATE_END_OF_LIST()
    },
};

static void tpda_init(Object *obj)
{
    QcomCoresightTPDAState *s = QCOM_CORESIGHT_TPDA(obj);

    s->profile = TPDA_PROFILE;
    memory_region_init_io(&s->iomem, obj, &tpda_ops, s,
                          TYPE_QCOM_CORESIGHT_TPDA, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static void tpda_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "CoreSight TPDA authentication-disabled management profile";
    dc->user_creatable = false;
    dc->vmsd = &vmstate_tpda;
    device_class_set_legacy_reset(dc, tpda_reset);
}

static const TypeInfo tpda_info = {
    .name = TYPE_QCOM_CORESIGHT_TPDA,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomCoresightTPDAState),
    .instance_init = tpda_init,
    .class_init = tpda_class_init,
};

static void tpda_register_types(void)
{
    type_register_static(&tpda_info);
}
type_init(tpda_register_types)
