/*
 * 32-bit CoreSight STM r0p0 reference for the original DT's part 0x962.
 *
 * DBGEN, NIDEN, SPIDEN and SPNIDEN are fixed low. Authentication status is
 * consequently 0xaa, and no trace is generated (IHI 0054A section 2.6).
 * The original DT's stimulus aperture accepts writes and discards them;
 * reads return zero. There is no ATB output, DMA request or HW event input.
 *
 * This is a documented reference configuration, not a measurement of the
 * tablet's debug authorization or a claim that a trace engine is available.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/arm-coresight-stm.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define STM_PROFILE 0x53544d01U
#define STM_UNLOCK  0xc5acce55U

#define STM_DMASTART  0xc04
#define STM_DMASTOP   0xc08
#define STM_DMASTAT   0xc0c
#define STM_DMACTRL   0xc10
#define STM_DMAID     0xcfc
#define STM_HEER      0xd00
#define STM_HETER     0xd20
#define STM_HEBSR     0xd60
#define STM_HEMCR     0xd64
#define STM_HEMASTR   0xdf4
#define STM_HEFEAT1   0xdf8
#define STM_HEID      0xdfc
#define STM_SPER      0xe00
#define STM_SPTER     0xe20
#define STM_PRIVMASK  0xe40
#define STM_SPSCR     0xe60
#define STM_SPMSCR    0xe64
#define STM_SPOVERRIDE 0xe68
#define STM_SPMOVERRIDE 0xe6c
#define STM_SPTRIGCSR 0xe70
#define STM_TCSR      0xe80
#define STM_TSSTIM    0xe84
#define STM_TSFREQ    0xe8c
#define STM_SYNCR     0xe90
#define STM_AUXCR     0xe94
#define STM_SPFEAT1   0xea0
#define STM_SPFEAT2   0xea4
#define STM_SPFEAT3   0xea8
#define STM_ITTRIGGER 0xee8
#define STM_ITDATA    0xeec
#define STM_ITCTRL2   0xef0
#define STM_ITID      0xef4
#define STM_ITCTRL0   0xef8
#define STM_ITCTRL    0xf00
#define STM_CLAIMSET  0xfa0
#define STM_CLAIMCLR  0xfa4
#define STM_LAR       0xfb0
#define STM_LSR       0xfb4
#define STM_AUTH      0xfb8
#define STM_DEVID     0xfc8
#define STM_DEVTYPE   0xfcc

#define STM_SPSCR_MASK       0xfff00003U
#define STM_MASTER_MASK      0x007f8001U
#define STM_SPOVERRIDE_MASK  0xffff8007U
#define STM_SPTRIGCSR_MASK   0x00000019U
#define STM_TCSR_MASK        0x007f0023U
#define STM_HEMCR_MASK       0x00000097U
#define STM_SYNCR_MASK       0x00001fffU
#define STM_AUXCR_MASK       0x0000001fU
#define STM_STIMULUS_SIZE    0x00180000U

struct ArmCoresightSTMState {
    SysBusDevice parent_obj;
    MemoryRegion control;
    MemoryRegion stimulus;

    uint32_t profile;
    uint32_t heer;
    uint32_t heter;
    uint32_t hemcr;
    uint32_t sper;
    uint32_t spter;
    uint32_t spscr;
    uint32_t spmscr;
    uint32_t spoverride;
    uint32_t spmoverride;
    uint32_t sptrigcsr;
    uint32_t tcsr;
    uint32_t tsfreq;
    uint32_t syncr;
    uint32_t auxcr;
    uint32_t claim;
    bool locked;
};

static MemTxResult stm_reject(hwaddr offset, unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "arm-coresight-stm: rejected %s offset=0x%"
                  HWADDR_PRIx " size=%u\n", write ? "write" : "read",
                  offset, size);
    return MEMTX_ACCESS_ERROR;
}

static bool stm_register(hwaddr offset)
{
    switch (offset) {
    case STM_DMASTART: case STM_DMASTOP: case STM_DMASTAT:
    case STM_DMACTRL: case STM_DMAID:
    case STM_HEER: case STM_HETER: case STM_HEBSR: case STM_HEMCR:
    case STM_HEMASTR: case STM_HEFEAT1: case STM_HEID:
    case STM_SPER: case STM_SPTER: case STM_PRIVMASK:
    case STM_SPSCR: case STM_SPMSCR:
    case STM_SPOVERRIDE: case STM_SPMOVERRIDE: case STM_SPTRIGCSR:
    case STM_TCSR: case STM_TSSTIM: case STM_TSFREQ:
    case STM_SYNCR: case STM_AUXCR:
    case STM_SPFEAT1: case STM_SPFEAT2: case STM_SPFEAT3:
    case STM_ITTRIGGER: case STM_ITDATA: case STM_ITCTRL2:
    case STM_ITID: case STM_ITCTRL0: case STM_ITCTRL:
    case STM_CLAIMSET: case STM_CLAIMCLR: case STM_LAR: case STM_LSR:
    case STM_AUTH: case STM_DEVID: case STM_DEVTYPE:
    case 0xfd0: case 0xfd4: case 0xfd8: case 0xfdc:
    case 0xfe0: case 0xfe4: case 0xfe8: case 0xfec:
    case 0xff0: case 0xff4: case 0xff8: case 0xffc:
        return true;
    default:
        return false;
    }
}

static bool stm_spscr_valid(uint32_t data)
{
    unsigned ctl = data & 3;

    return !(data & ~STM_SPSCR_MASK) && ctl != 2 &&
           (!ctl || (data >> 20));
}

static bool stm_master_valid(uint32_t data)
{
    return !(data & ~STM_MASTER_MASK) && (!(data & 1) || (data >> 15));
}

static bool stm_spoverride_valid(uint32_t data)
{
    unsigned ctl = data & 3;

    /* OVERTS is independent of PORTSEL; only OVERCTL selects ports. */
    return !(data & ~STM_SPOVERRIDE_MASK) && ctl != 3 &&
           (!ctl || (data >> 15));
}

static bool stm_syncr_valid(uint32_t data)
{
    unsigned count = (data >> 7) & 0x1f;

    return !(data & ~STM_SYNCR_MASK) &&
           (!(data & 0x1000) || (count >= 12 && count <= 27));
}

static MemTxResult stm_read(void *opaque, hwaddr offset, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    ArmCoresightSTMState *s = opaque;

    *value = 0;
    if (size != 4 || !stm_register(offset)) {
        return stm_reject(offset, size, false);
    }
    switch (offset) {
    case STM_HEER: *value = s->heer; break;
    case STM_HETER: *value = s->heter; break;
    case STM_HEMCR: *value = s->hemcr; break;
    case STM_SPER: *value = s->sper; break;
    case STM_SPTER: *value = s->spter; break;
    case STM_SPSCR: *value = s->spscr; break;
    case STM_SPMSCR: *value = s->spmscr; break;
    case STM_SPOVERRIDE: *value = s->spoverride; break;
    case STM_SPMOVERRIDE: *value = s->spmoverride; break;
    case STM_SPTRIGCSR: *value = s->sptrigcsr; break;
    case STM_TCSR: *value = s->tcsr | 4; break; /* SYNCEN is RAO; BUSY is 0. */
    case STM_TSFREQ: *value = s->tsfreq; break;
    case STM_SYNCR: *value = s->syncr; break;
    case STM_AUXCR: *value = s->auxcr; break;
    case STM_DMASTAT: case STM_DMACTRL:
    case STM_HEBSR: case STM_PRIVMASK:
    case STM_ITCTRL2: case STM_ITCTRL:
        break;
    case STM_DMAID: *value = 2; break; /* Architectural DMA class ID. */
    case STM_HEMASTR: *value = 0x80; break;
    case STM_HEFEAT1: *value = 0x00100035; break;
    case STM_HEID: *value = 1; break;
    case STM_SPFEAT1: *value = 0x006587d1; break;
    case STM_SPFEAT2: *value = 0x000104f2; break;
    case STM_SPFEAT3: *value = 0x7f; break;
    case STM_CLAIMSET: *value = 0xf; break;
    case STM_CLAIMCLR: *value = s->claim; break;
    case STM_LSR: *value = 1 | (s->locked ? 2 : 0); break;
    case STM_AUTH: *value = 0xaa; break;
    case STM_DEVID: *value = 0x10000; break; /* 65,536 architectural ports. */
    case STM_DEVTYPE: *value = 0x63; break;
    case 0xfd0: *value = 4; break;
    case 0xfd4: case 0xfd8: case 0xfdc: case 0xfec: break;
    case 0xfe0: *value = 0x62; break;
    case 0xfe4: *value = 0xb9; break;
    case 0xfe8: *value = 0x0b; break;
    case 0xff0: *value = 0x0d; break;
    case 0xff4: *value = 0x90; break;
    case 0xff8: *value = 0x05; break;
    case 0xffc: *value = 0xb1; break;
    default: /* Write-only command registers have no readable shadow. */
        return stm_reject(offset, size, false);
    }
    return MEMTX_OK;
}

static MemTxResult stm_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    ArmCoresightSTMState *s = opaque;
    uint32_t data = value;

    if (size != 4 || !stm_register(offset)) {
        return stm_reject(offset, size, true);
    }
    if (offset == STM_LAR) {
        s->locked = data != STM_UNLOCK;
        return MEMTX_OK;
    }
    if (s->locked) {
        return MEMTX_OK;
    }
    switch (offset) {
    case STM_HEER: s->heer = data; break;
    case STM_HETER: s->heter = data; break;
    case STM_HEMCR:
        if ((data & 0x40) && !(data & 0x10)) {
            return stm_reject(offset, size, true);
        }
        /* No HW event input or trace is admitted; status is already clear. */
        s->hemcr = data & STM_HEMCR_MASK;
        break;
    case STM_SPER: s->sper = data; break;
    case STM_SPTER: s->spter = data; break;
    case STM_SPSCR:
        data &= STM_SPSCR_MASK;
        if (!stm_spscr_valid(data)) {
            return stm_reject(offset, size, true);
        }
        s->spscr = data;
        break;
    case STM_SPMSCR: case STM_SPMOVERRIDE:
        data &= STM_MASTER_MASK;
        if (!stm_master_valid(data)) {
            return stm_reject(offset, size, true);
        }
        if (offset == STM_SPMSCR) {
            s->spmscr = data;
        } else {
            s->spmoverride = data;
        }
        break;
    case STM_SPOVERRIDE:
        data &= STM_SPOVERRIDE_MASK;
        if (!stm_spoverride_valid(data)) {
            return stm_reject(offset, size, true);
        }
        s->spoverride = data;
        break;
    case STM_SPTRIGCSR:
        if ((data & 4) && !(data & 1)) {
            return stm_reject(offset, size, true);
        }
        /* The write-only clear bit cannot be retained as trigger status. */
        s->sptrigcsr = data & STM_SPTRIGCSR_MASK;
        break;
    case STM_TCSR:
        if ((s->tcsr & 1) && ((s->tcsr ^ data) & 0x007f0000)) {
            /* TRACEID must be changed only after disabling and observing idle. */
            return stm_reject(offset, size, true);
        }
        s->tcsr = data & STM_TCSR_MASK;
        break;
    case STM_TSFREQ: s->tsfreq = data; break;
    case STM_SYNCR:
        data &= STM_SYNCR_MASK;
        if (!stm_syncr_valid(data)) {
            return stm_reject(offset, size, true);
        }
        s->syncr = data;
        break;
    case STM_AUXCR: s->auxcr = data & STM_AUXCR_MASK; break;
    case STM_HEBSR: case STM_PRIVMASK:
        /* 32 hardware events need no bank selector; PRIVMASK is absent. */
        break;
    case STM_TSSTIM:
        /* Timestamp requests are suppressed by fixed non-invasive denial. */
        break;
    case STM_DMASTART: case STM_DMASTOP: case STM_DMACTRL:
    case STM_ITTRIGGER: case STM_ITDATA: case STM_ITID:
    case STM_ITCTRL0: case STM_ITCTRL:
        /* No DMA request interface or integration output is implemented. */
        if (data) {
            return stm_reject(offset, size, true);
        }
        break;
    case STM_CLAIMSET: s->claim |= data & 0xf; break;
    case STM_CLAIMCLR: s->claim &= ~(data & 0xf); break;
    default:
        return stm_reject(offset, size, true);
    }
    return MEMTX_OK;
}

static MemTxResult stm_stimulus_read(void *opaque, hwaddr offset,
                                     uint64_t *value, unsigned size,
                                     MemTxAttrs attrs)
{
    /* All extended stimulus reads return OKAY and zero (DDI 0444A 2.7.4). */
    *value = 0;
    return MEMTX_OK;
}

static MemTxResult stm_stimulus_write(void *opaque, hwaddr offset,
                                      uint64_t value, unsigned size,
                                      MemTxAttrs attrs)
{
    /*
     * All four authentication inputs are fixed low, independent of security
     * attributes, EN, port masks and the APB software lock. Accept and discard
     * the access. Do not store payload, generate trace, DMA, a timer or IRQ.
     */
    return MEMTX_OK;
}

static const MemoryRegionOps stm_control_ops = {
    .read_with_attrs = stm_read,
    .write_with_attrs = stm_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Reject an invalid APB access whole; do not split 64-bit control writes. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static const MemoryRegionOps stm_stimulus_ops = {
    .read_with_attrs = stm_stimulus_read,
    .write_with_attrs = stm_stimulus_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* A wider QEMU bus request is split; it is not a 64-bit STM data packet. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 4, .unaligned = true },
};

static void stm_reset(DeviceState *dev)
{
    ArmCoresightSTMState *s = ARM_CORESIGHT_STM(dev);

    s->heer = s->heter = s->hemcr = 0;
    s->sper = s->spter = s->spscr = s->spmscr = 0;
    s->spoverride = s->spmoverride = s->sptrigcsr = 0;
    s->tcsr = s->tsfreq = s->syncr = s->auxcr = 0;
    s->claim = 0;
    s->locked = true;
}

static int stm_post_load(void *opaque, int version)
{
    ArmCoresightSTMState *s = opaque;

    if ((s->hemcr & ~STM_HEMCR_MASK) ||
        !stm_spscr_valid(s->spscr) ||
        !stm_master_valid(s->spmscr) ||
        !stm_master_valid(s->spmoverride) ||
        !stm_spoverride_valid(s->spoverride) ||
        (s->sptrigcsr & ~STM_SPTRIGCSR_MASK) ||
        (s->tcsr & ~STM_TCSR_MASK) ||
        !stm_syncr_valid(s->syncr) ||
        (s->auxcr & ~STM_AUXCR_MASK) || s->claim > 0xf) {
        return -EINVAL;
    }
    return 0;
}

static const VMStateDescription vmstate_stm = {
    .name = TYPE_ARM_CORESIGHT_STM,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = stm_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile, ArmCoresightSTMState),
        VMSTATE_UINT32(heer, ArmCoresightSTMState),
        VMSTATE_UINT32(heter, ArmCoresightSTMState),
        VMSTATE_UINT32(hemcr, ArmCoresightSTMState),
        VMSTATE_UINT32(sper, ArmCoresightSTMState),
        VMSTATE_UINT32(spter, ArmCoresightSTMState),
        VMSTATE_UINT32(spscr, ArmCoresightSTMState),
        VMSTATE_UINT32(spmscr, ArmCoresightSTMState),
        VMSTATE_UINT32(spoverride, ArmCoresightSTMState),
        VMSTATE_UINT32(spmoverride, ArmCoresightSTMState),
        VMSTATE_UINT32(sptrigcsr, ArmCoresightSTMState),
        VMSTATE_UINT32(tcsr, ArmCoresightSTMState),
        VMSTATE_UINT32(tsfreq, ArmCoresightSTMState),
        VMSTATE_UINT32(syncr, ArmCoresightSTMState),
        VMSTATE_UINT32(auxcr, ArmCoresightSTMState),
        VMSTATE_UINT32(claim, ArmCoresightSTMState),
        VMSTATE_BOOL(locked, ArmCoresightSTMState),
        VMSTATE_END_OF_LIST()
    },
};

static void stm_init(Object *obj)
{
    ArmCoresightSTMState *s = ARM_CORESIGHT_STM(obj);

    s->profile = STM_PROFILE;
    memory_region_init_io(&s->control, obj, &stm_control_ops, s,
                          TYPE_ARM_CORESIGHT_STM, 0x1000);
    memory_region_init_io(&s->stimulus, obj, &stm_stimulus_ops, s,
                          TYPE_ARM_CORESIGHT_STM "-stimulus",
                          STM_STIMULUS_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->control);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->stimulus);
}

static void stm_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "32-bit CoreSight STM reference with debug authentication off";
    dc->user_creatable = false;
    dc->vmsd = &vmstate_stm;
    device_class_set_legacy_reset(dc, stm_reset);
}

static const TypeInfo stm_info = {
    .name = TYPE_ARM_CORESIGHT_STM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ArmCoresightSTMState),
    .instance_init = stm_init,
    .class_init = stm_class_init,
};

static void stm_register_types(void)
{
    type_register_static(&stm_info);
}
type_init(stm_register_types)

