/*
 * TPDM management interface with no collection datasets.
 *
 * The original TB371FC DT overrides AMBA bus matching with 0x000bb968 or
 * 0x0003b968. These values do not establish the hardware PIDR0: the vendor
 * driver separately decodes its bits as dataset presence. This deliberately
 * limited virtual profile returns PIDR0=0, exposing no GPR, BC, TC, DSB,
 * CMB, MCMB or implementation-defined collection interface.
 *
 * Other ID bytes retain the old Arm designer family; neither this virtual
 * identity nor AUTHSTATUS=0x88 claims measured SM8250 silicon values.
 * The vendor probe does not check AUTHSTATUS. No samples, trace, DMA, IRQ,
 * software increments, counter activity or integration datapath is provided.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-coresight-tpdm.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define TPDM_PROFILE 0x54504d01U
#define TPDM_UNLOCK  0xc5acce55U
#define TPDM_AUTH_DISABLED 0x88U

/*
 * Vendor support enum: 2 means no support for TC triggers, BC triggers and
 * BC ganging. Their counter-number fields are zero encodings, interpreted
 * only when the corresponding dataset exists. PIDR0=0 advertises none.
 */
#define TPDM_DEVID_VALUE ((2U << 27) | (2U << 25) | (2U << 23))

#define TPDM_ITCTRL   0xf00
#define TPDM_CLAIMSET 0xfa0
#define TPDM_CLAIMCLR 0xfa4
#define TPDM_LAR      0xfb0
#define TPDM_LSR      0xfb4
#define TPDM_AUTH     0xfb8
#define TPDM_DEVARCH  0xfbc
#define TPDM_DEVID    0xfc8

struct QcomCoresightTPDMState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;

    uint32_t profile;
    uint32_t claim;
    bool locked;
};

static MemTxResult tpdm_reject(hwaddr offset, unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "qcom-coresight-tpdm: rejected %s offset=0x%"
                  HWADDR_PRIx " size=%u\n", write ? "write" : "read",
                  offset, size);
    return MEMTX_ACCESS_ERROR;
}

static bool tpdm_management_register(hwaddr offset)
{
    switch (offset) {
    case TPDM_ITCTRL:
    case TPDM_CLAIMSET: case TPDM_CLAIMCLR:
    case TPDM_LAR: case TPDM_LSR: case TPDM_AUTH: case TPDM_DEVARCH:
    case TPDM_DEVID:
    case 0xfd0: case 0xfd4: case 0xfd8: case 0xfdc:
    case 0xfe0: case 0xfe4: case 0xfe8: case 0xfec:
    case 0xff0: case 0xff4: case 0xff8: case 0xffc:
        return true;
    default:
        /* Absent dataset/control registers fail even while software-locked. */
        return false;
    }
}

static MemTxResult tpdm_read(void *opaque, hwaddr offset, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomCoresightTPDMState *s = opaque;

    *value = 0;
    if (size != 4 || !tpdm_management_register(offset)) {
        return tpdm_reject(offset, size, false);
    }
    switch (offset) {
    case TPDM_CLAIMSET:
        *value = 0xf; /* Four implementation-selected software claim tags. */
        break;
    case TPDM_CLAIMCLR:
        *value = s->claim;
        break;
    case TPDM_LSR:
        *value = 1 | (s->locked ? 2 : 0);
        break;
    case TPDM_AUTH:
        *value = TPDM_AUTH_DISABLED;
        break;
    case TPDM_DEVID:
        *value = TPDM_DEVID_VALUE;
        break;
    case TPDM_ITCTRL:
    case TPDM_DEVARCH:
        /* Integration is absent; no component architecture is advertised. */
        break;
    case 0xfd0:
        *value = 4; /* Arm JEP106 continuation, one 4-KiB aperture. */
        break;
    case 0xfd4: case 0xfd8: case 0xfdc: case 0xfec:
        break;
    case 0xfe0:
        /* Immutable zero dataset bitmap, not the DT's AMBA match override. */
        break;
    case 0xfe4:
        *value = 0xb9;
        break;
    case 0xfe8:
        *value = 0x0b; /* Old designer family, revision zero: no MSR support. */
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
        return tpdm_reject(offset, size, false); /* LAR is write-only. */
    }
    return MEMTX_OK;
}

static MemTxResult tpdm_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomCoresightTPDMState *s = opaque;
    uint32_t data = value;

    if (size != 4 || !tpdm_management_register(offset)) {
        return tpdm_reject(offset, size, true);
    }
    if (offset == TPDM_LAR) {
        s->locked = data != TPDM_UNLOCK;
        return MEMTX_OK;
    }
    if (s->locked) {
        return MEMTX_OK;
    }
    switch (offset) {
    case TPDM_ITCTRL:
        break; /* RAZ/WI: no integration mode can be enabled. */
    case TPDM_CLAIMSET:
        s->claim |= data & 0xf;
        break;
    case TPDM_CLAIMCLR:
        s->claim &= ~(data & 0xf);
        break;
    default:
        return tpdm_reject(offset, size, true);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps tpdm_ops = {
    .read_with_attrs = tpdm_read,
    .write_with_attrs = tpdm_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Reject the original malformed transaction without partial writes. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void tpdm_reset(DeviceState *dev)
{
    QcomCoresightTPDMState *s = QCOM_CORESIGHT_TPDM(dev);

    s->claim = 0;
    s->locked = true;
}

static int tpdm_post_load(void *opaque, int version)
{
    QcomCoresightTPDMState *s = opaque;

    if (s->claim > 0xf) {
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_tpdm = {
    .name = TYPE_QCOM_CORESIGHT_TPDM,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = tpdm_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile, QcomCoresightTPDMState),
        VMSTATE_UINT32(claim, QcomCoresightTPDMState),
        VMSTATE_BOOL(locked, QcomCoresightTPDMState),
        VMSTATE_END_OF_LIST()
    },
};

static void tpdm_init(Object *obj)
{
    QcomCoresightTPDMState *s = QCOM_CORESIGHT_TPDM(obj);

    s->profile = TPDM_PROFILE;
    memory_region_init_io(&s->iomem, obj, &tpdm_ops, s,
                          TYPE_QCOM_CORESIGHT_TPDM, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static void tpdm_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "CoreSight TPDM management interface with no collection datasets";
    dc->user_creatable = false;
    dc->vmsd = &vmstate_tpdm;
    device_class_set_legacy_reset(dc, tpdm_reset);
}

static const TypeInfo tpdm_info = {
    .name = TYPE_QCOM_CORESIGHT_TPDM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomCoresightTPDMState),
    .instance_init = tpdm_init,
    .class_init = tpdm_class_init,
};

static void tpdm_register_types(void)
{
    type_register_static(&tpdm_info);
}
type_init(tpdm_register_types)
