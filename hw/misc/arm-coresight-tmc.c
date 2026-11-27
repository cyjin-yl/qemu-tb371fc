/*
 * CoreSight TMC APB reference for the TB371FC DT's part 0x961 (r0p0 ID).
 *
 * ETF: 4 KiB test RAM, a 32-bit ATB / 64-bit memory configuration, software
 * lock, claims and a timed empty-input capture/flush state machine.
 * ETR: authenticated off (DBGEN and SPIDEN low); no AXI or scatter/gather
 * implementation is advertised. No ATB producer is attached to either TMC.
 *
 * References: Arm DDI 0461B and the original DT's tmc@6b05000/tmc@6048000.
 * RAM size and flush latency are explicit reference choices, not silicon
 * measurements. An APB RAM test does not manufacture captured trace.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/arm-coresight-tmc.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define TMC_RAM_BYTES 4096
#define TMC_RAM_WORDS (TMC_RAM_BYTES / 4)
#define TMC_RAM_MASK (TMC_RAM_BYTES - 1)
#define TMC_PROFILE 0x544d4301U
#define TMC_UNLOCK 0xc5acce55U
#define TMC_FLUSH_NS 1000

#define TMC_RSZ       0x004
#define TMC_STS       0x00c
#define TMC_RRD       0x010
#define TMC_RRP       0x014
#define TMC_RWP       0x018
#define TMC_TRG       0x01c
#define TMC_CTL       0x020
#define TMC_RWD       0x024
#define TMC_MODE      0x028
#define TMC_LBUFLEVEL 0x02c
#define TMC_CBUFLEVEL 0x030
#define TMC_BUFWM     0x034
#define TMC_FFSR      0x300
#define TMC_FFCR      0x304
#define TMC_PSCR      0x308
#define TMC_ITCTRL    0xf00
#define TMC_CLAIMSET  0xfa0
#define TMC_CLAIMCLR  0xfa4
#define TMC_LAR       0xfb0
#define TMC_LSR       0xfb4
#define TMC_AUTH      0xfb8
#define TMC_DEVID     0xfc8
#define TMC_DEVTYPE   0xfcc

#define TMC_READY         (1U << 2)
#define TMC_FTEMPTY       (1U << 3)
#define TMC_EMPTY         (1U << 4)
#define TMC_DRAIN_BUFFER (1U << 14)
#define TMC_FLUSHMAN      (1U << 6)
#define TMC_STOP_ON_FLUSH (1U << 12)
#define TMC_FFCR_MASK    0x3773U

struct ArmCoresightTmcState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    QEMUTimer *flush_timer;

    uint32_t configuration;
    uint32_t profile;
    uint32_t ctl;
    uint32_t mode;
    uint32_t ffcr;
    uint32_t trigger;
    uint32_t watermark;
    uint32_t pscr;
    uint32_t itctrl;
    uint32_t claim;
    uint32_t rrp;
    uint32_t rwp;
    uint32_t read_lane;
    uint32_t write_lane;
    uint32_t ram[TMC_RAM_WORDS];
    uint32_t read_beat[2];
    uint32_t write_beat[2];
    bool full;
    bool locked;
    bool stopped;
};

static bool tmc_is_etf(ArmCoresightTmcState *s)
{
    return s->configuration == ARM_TMC_ETF;
}

static MemTxResult tmc_reject(hwaddr offset, unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "arm-coresight-tmc: rejected %s offset=0x%"
                  HWADDR_PRIx " size=%u\n", write ? "write" : "read",
                  offset, size);
    return MEMTX_ACCESS_ERROR;
}

static void tmc_flush_complete(void *opaque)
{
    ArmCoresightTmcState *s = opaque;

    s->ffcr &= ~TMC_FLUSHMAN;
    if (s->ctl && (s->ffcr & TMC_STOP_ON_FLUSH)) {
        if (!s->stopped) {
            s->trigger = 0; /* TMCReady low-to-high edge. */
        }
        s->stopped = true;
    }
}

/* Only the implemented 4 KiB APB bank is decoded; holes are not zero stubs. */
static bool tmc_register(hwaddr offset)
{
    switch (offset) {
    case TMC_RSZ: case TMC_STS: case TMC_RRD: case TMC_RRP: case TMC_RWP:
    case TMC_TRG: case TMC_CTL: case TMC_RWD: case TMC_MODE:
    case TMC_LBUFLEVEL: case TMC_CBUFLEVEL: case TMC_BUFWM:
    case TMC_FFSR: case TMC_FFCR: case TMC_PSCR: case TMC_ITCTRL:
    case TMC_CLAIMSET: case TMC_CLAIMCLR: case TMC_LAR: case TMC_LSR:
    case TMC_AUTH: case TMC_DEVID: case TMC_DEVTYPE:
    case 0xfd0: case 0xfd4: case 0xfd8: case 0xfdc:
    case 0xfe0: case 0xfe4: case 0xfe8: case 0xfec:
    case 0xff0: case 0xff4: case 0xff8: case 0xffc:
        return true;
    default:
        return false;
    }
}

static MemTxResult tmc_read(void *opaque, hwaddr offset, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    ArmCoresightTmcState *s = opaque;

    *value = 0;
    if (size != 4 || !tmc_register(offset)) {
        return tmc_reject(offset, size, false);
    }
    switch (offset) {
    case TMC_RSZ:
        /* Disabled ETR has no internal SRAM; ETF RSZ is in 32-bit words. */
        *value = tmc_is_etf(s) ? TMC_RAM_WORDS : 0;
        break;
    case TMC_STS:
        *value = (s->full ? 1 : 0) | (s->ctl ? TMC_EMPTY : 0) |
                 ((!s->ctl || s->stopped) ? TMC_READY | TMC_FTEMPTY : 0);
        break;
    case TMC_RRD:
        *value = UINT32_MAX;
        if (tmc_is_etf(s) && !s->locked && !s->ctl) {
            /* Disabled mode ignores MODE; data exists only in real test RAM. */
            if (!s->read_lane) {
                memcpy(s->read_beat, &s->ram[s->rrp / 4],
                       sizeof(s->read_beat));
            }
            *value = s->read_beat[s->read_lane];
            if (++s->read_lane == 2) {
                s->read_lane = 0;
                memset(s->read_beat, 0, sizeof(s->read_beat));
                s->rrp = (s->rrp + 8) & TMC_RAM_MASK;
            }
        }
        /*
         * With no ATB source all capture modes remain empty, including after
         * stopping. RRD returns the architectural empty/locked sentinel and
         * must not consume RAM or change either pointer in these states.
         */
        break;
    case TMC_RRP: *value = s->rrp; break;
    case TMC_RWP: *value = s->rwp; break;
    case TMC_TRG: *value = s->trigger; break;
    case TMC_CTL: *value = s->ctl; break;
    case TMC_MODE: *value = s->mode; break;
    case TMC_LBUFLEVEL: case TMC_CBUFLEVEL:
        *value = 0; /* APB integration writes are not captured trace. */
        break;
    case TMC_BUFWM: *value = s->watermark; break;
    case TMC_FFSR:
        *value = ((s->ffcr & TMC_FLUSHMAN) ? 1 : 0) |
                 ((!s->ctl || s->stopped) ? 2 : 0);
        break;
    case TMC_FFCR: *value = s->ffcr; break;
    case TMC_PSCR: *value = s->pscr; break;
    case TMC_ITCTRL: *value = s->itctrl; break;
    case TMC_CLAIMSET: *value = 0xf; break;
    case TMC_CLAIMCLR: *value = s->claim; break;
    case TMC_LSR: *value = 1 | (s->locked ? 2 : 0); break;
    case TMC_AUTH:
        /* ETF authentication is elsewhere. ETR invasive debug is disabled. */
        *value = tmc_is_etf(s) ? 0 : 0x22;
        break;
    case TMC_DEVID:
        /* ETF memory = 2 x 32-bit ATB; disabled ETR has a 32-bit data path. */
        *value = (tmc_is_etf(s) ? 3U : 2U) << 8 | s->configuration << 6 |
                 (tmc_is_etf(s) ? 0 : 2U << 11); /* ETR: four-beat WBUF. */
        break;
    case TMC_DEVTYPE: *value = tmc_is_etf(s) ? 0x32 : 0x21; break;
    case 0xfd0: *value = 0x04; break; /* PIDR4: JEP106 continuation. */
    case 0xfd4: case 0xfd8: case 0xfdc: case 0xfec:
        break;
    case 0xfe0: *value = 0x61; break;
    case 0xfe4: *value = 0xb9; break;
    case 0xfe8: *value = 0x0b; break; /* Original DT identity: r0p0. */
    case 0xff0: *value = 0x0d; break;
    case 0xff4: *value = 0x90; break;
    case 0xff8: *value = 0x05; break;
    case 0xffc: *value = 0xb1; break;
    default: /* RWD and LAR are write-only. */
        return tmc_reject(offset, size, false);
    }
    return MEMTX_OK;
}

static MemTxResult tmc_write(void *opaque, hwaddr offset, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    ArmCoresightTmcState *s = opaque;
    uint32_t data = value;

    if (size != 4 || !tmc_register(offset)) {
        return tmc_reject(offset, size, true);
    }
    if (offset == TMC_LAR) {
        s->locked = data != TMC_UNLOCK;
        return MEMTX_OK;
    }
    if (s->locked) {
        return MEMTX_OK;
    }
    switch (offset) {
    case TMC_CLAIMSET:
        s->claim |= data & 0xf;
        break;
    case TMC_CLAIMCLR:
        s->claim &= ~(data & 0xf);
        break;
    case TMC_CTL:
        if (!tmc_is_etf(s)) {
            /* DBGEN/SPIDEN low: this ETR cannot start a trace session. */
            break;
        }
        if (!(data & 1)) {
            timer_del(s->flush_timer);
            s->ffcr &= ~TMC_FLUSHMAN;
            if (s->ctl && !s->stopped) {
                s->trigger = 0;
            }
            s->ctl = 0;
            s->stopped = false;
        } else if (!s->ctl && !s->itctrl) {
            s->ctl = 1;
            s->stopped = false;
            s->full = false;
            if (s->mode != 0) {
                s->rwp = 0; /* FIFO modes start at the beginning of the RAM. */
            }
            /*
             * Circular capture retains the configured RWP. With no input,
             * this reference places the empty read pointer at that start.
             */
            s->rrp = s->rwp;
            s->read_lane = s->write_lane = 0;
            memset(s->read_beat, 0, sizeof(s->read_beat));
            memset(s->write_beat, 0, sizeof(s->write_beat));
        }
        break;
    case TMC_FFCR:
        if (!tmc_is_etf(s)) {
            break;
        }
        if (data & TMC_DRAIN_BUFFER) {
            /* Circular-buffer ATB DrainBuffer is not an implemented path. */
            return tmc_reject(offset, size, true);
        }
        if (s->ctl) {
            /* EnFt/EnTI can only change in Disabled state. */
            data = (data & ~3U) | (s->ffcr & 3);
        }
        /* A second write cannot cancel an already requested hardware flush. */
        s->ffcr = (data & (TMC_FFCR_MASK & ~TMC_FLUSHMAN)) |
                  (s->ffcr & TMC_FLUSHMAN);
        if (s->ctl && (data & TMC_FLUSHMAN) &&
            !timer_pending(s->flush_timer)) {
            s->ffcr |= TMC_FLUSHMAN;
            timer_mod(s->flush_timer,
                      qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + TMC_FLUSH_NS);
        }
        break;
    case TMC_RWD:
        if (!tmc_is_etf(s)) {
            return tmc_reject(offset, size, true);
        }
        /* The supported direct RAM path is Disabled + integration mode. */
        if (!s->ctl && s->itctrl) {
            s->write_beat[s->write_lane] = data;
            if (++s->write_lane == 2) {
                memcpy(&s->ram[s->rwp / 4], s->write_beat,
                       sizeof(s->write_beat));
                s->write_lane = 0;
                memset(s->write_beat, 0, sizeof(s->write_beat));
                s->rwp = (s->rwp + 8) & TMC_RAM_MASK;
                if (!s->rwp) {
                    s->full = true;
                }
            }
        }
        break;
    case TMC_RRP: case TMC_RWP: case TMC_TRG: case TMC_MODE:
    case TMC_BUFWM: case TMC_PSCR: case TMC_ITCTRL:
        if (!tmc_is_etf(s)) {
            return tmc_reject(offset, size, true);
        }
        if (s->ctl) {
            break;
        }
        switch (offset) {
        case TMC_RRP:
            s->rrp = data & (TMC_RAM_MASK & ~0xfU);
            s->read_lane = 0;
            memset(s->read_beat, 0, sizeof(s->read_beat));
            break;
        case TMC_RWP:
            s->rwp = data & (TMC_RAM_MASK & ~0xfU);
            s->write_lane = 0;
            memset(s->write_beat, 0, sizeof(s->write_beat));
            break;
        case TMC_TRG: s->trigger = data & (TMC_RAM_WORDS - 1); break;
        case TMC_MODE:
            if ((data & 3) <= 2) {
                s->mode = data & 3;
            }
            break;
        case TMC_BUFWM: s->watermark = data & (TMC_RAM_WORDS - 1); break;
        case TMC_PSCR: s->pscr = data & 0x1f; break;
        case TMC_ITCTRL: s->itctrl = data & 1; break;
        default: g_assert_not_reached();
        }
        break;
    default: /* Read-only registers never become writable shadow storage. */
        return tmc_reject(offset, size, true);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps tmc_ops = {
    .read_with_attrs = tmc_read,
    .write_with_attrs = tmc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Reject wrong-width/unaligned transactions whole; never split them. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void tmc_reset(DeviceState *dev)
{
    ArmCoresightTmcState *s = ARM_CORESIGHT_TMC(dev);

    timer_del(s->flush_timer);
    s->ctl = s->mode = s->ffcr = s->trigger = s->watermark = 0;
    s->pscr = 0xa;
    s->itctrl = s->claim = s->rrp = s->rwp = 0;
    s->read_lane = s->write_lane = 0;
    memset(s->read_beat, 0, sizeof(s->read_beat));
    memset(s->write_beat, 0, sizeof(s->write_beat));
    s->full = false;
    s->locked = true;
    s->stopped = false;
    /* Reset RAM contents are unspecified in silicon; this reference zeros it. */
    memset(s->ram, 0, sizeof(s->ram));
}

static int tmc_post_load(void *opaque, int version)
{
    ArmCoresightTmcState *s = opaque;
    unsigned i;

    if (s->ctl > 1 || s->mode > 2 || s->itctrl > 1 || s->claim > 0xf ||
        (s->ffcr & ~TMC_FFCR_MASK) || s->trigger >= TMC_RAM_WORDS ||
        s->watermark >= TMC_RAM_WORDS || s->pscr > 0x1f ||
        s->rrp >= TMC_RAM_BYTES || s->rwp >= TMC_RAM_BYTES ||
        (s->rrp & 7) || (s->rwp & 7) ||
        s->read_lane > 1 || s->write_lane > 1 ||
        (s->ctl && s->itctrl) || (s->stopped && (!s->ctl || s->trigger)) ||
        (!!(s->ffcr & TMC_FLUSHMAN) != timer_pending(s->flush_timer)) ||
        ((s->ffcr & TMC_FLUSHMAN) && !s->ctl) ||
        (s->ctl && (s->rrp != s->rwp || s->read_lane ||
                    s->write_lane || s->full || (s->mode && s->rwp))) ||
        (!s->read_lane && (s->read_beat[0] || s->read_beat[1])) ||
        (!s->write_lane && s->write_beat[0]) || s->write_beat[1]) {
        return -EINVAL;
    }
    if (!tmc_is_etf(s)) {
        if (s->ctl || s->mode || s->ffcr || s->trigger || s->watermark ||
            s->pscr != 0xa || s->itctrl || s->rrp || s->rwp ||
            s->read_lane || s->write_lane || s->stopped || s->full) {
            return -EINVAL;
        }
        for (i = 0; i < TMC_RAM_WORDS; i++) {
            if (s->ram[i]) {
                return -EINVAL;
            }
        }
    }
    return 0;
}

static const VMStateDescription vmstate_tmc = {
    .name = TYPE_ARM_CORESIGHT_TMC,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = tmc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile, ArmCoresightTmcState),
        VMSTATE_UINT32_EQUAL(configuration, ArmCoresightTmcState),
        VMSTATE_UINT32(ctl, ArmCoresightTmcState),
        VMSTATE_UINT32(mode, ArmCoresightTmcState),
        VMSTATE_UINT32(ffcr, ArmCoresightTmcState),
        VMSTATE_UINT32(trigger, ArmCoresightTmcState),
        VMSTATE_UINT32(watermark, ArmCoresightTmcState),
        VMSTATE_UINT32(pscr, ArmCoresightTmcState),
        VMSTATE_UINT32(itctrl, ArmCoresightTmcState),
        VMSTATE_UINT32(claim, ArmCoresightTmcState),
        VMSTATE_UINT32(rrp, ArmCoresightTmcState),
        VMSTATE_UINT32(rwp, ArmCoresightTmcState),
        VMSTATE_UINT32(read_lane, ArmCoresightTmcState),
        VMSTATE_UINT32(write_lane, ArmCoresightTmcState),
        VMSTATE_UINT32_ARRAY(ram, ArmCoresightTmcState, TMC_RAM_WORDS),
        VMSTATE_UINT32_ARRAY(read_beat, ArmCoresightTmcState, 2),
        VMSTATE_UINT32_ARRAY(write_beat, ArmCoresightTmcState, 2),
        VMSTATE_BOOL(full, ArmCoresightTmcState),
        VMSTATE_BOOL(locked, ArmCoresightTmcState),
        VMSTATE_BOOL(stopped, ArmCoresightTmcState),
        VMSTATE_TIMER_PTR(flush_timer, ArmCoresightTmcState),
        VMSTATE_END_OF_LIST()
    },
};

static void tmc_realize(DeviceState *dev, Error **errp)
{
    ArmCoresightTmcState *s = ARM_CORESIGHT_TMC(dev);

    if (s->configuration != ARM_TMC_ETF && s->configuration != ARM_TMC_ETR) {
        error_setg(errp, "arm-coresight-tmc supports ETF and disabled ETR only");
        return;
    }
    s->profile = TMC_PROFILE;
}

static void tmc_init(Object *obj)
{
    ArmCoresightTmcState *s = ARM_CORESIGHT_TMC(obj);

    memory_region_init_io(&s->iomem, obj, &tmc_ops, s,
                          TYPE_ARM_CORESIGHT_TMC, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    s->flush_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, tmc_flush_complete, s);
}

static void tmc_finalize(Object *obj)
{
    ArmCoresightTmcState *s = ARM_CORESIGHT_TMC(obj);

    timer_free(s->flush_timer);
}

static const Property tmc_properties[] = {
    DEFINE_PROP_UINT32("configuration", ArmCoresightTmcState, configuration,
                      ARM_TMC_ETF),
};

static void tmc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "CoreSight TMC ETF APB reference and authenticated-off ETR";
    dc->user_creatable = false;
    dc->realize = tmc_realize;
    dc->vmsd = &vmstate_tmc;
    device_class_set_legacy_reset(dc, tmc_reset);
    device_class_set_props(dc, tmc_properties);
}

static const TypeInfo tmc_info = {
    .name = TYPE_ARM_CORESIGHT_TMC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(ArmCoresightTmcState),
    .instance_init = tmc_init,
    .instance_finalize = tmc_finalize,
    .class_init = tmc_class_init,
};

static void tmc_register_types(void)
{
    type_register_static(&tmc_info);
}
type_init(tmc_register_types)
