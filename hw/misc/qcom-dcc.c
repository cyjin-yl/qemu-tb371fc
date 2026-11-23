/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm DCC v2 map2, bounded SRAM capture subset.
 *
 * Register and descriptor inputs are derived from the Qualcomm Linux driver;
 * see tb371fc/docs/dcc.md for pinned sources and the explicit reference
 * geometry. This is not a complete debug/trace/CTI or CRC implementation.
 * Unsupported requests stay busy and are diagnosed; they never complete
 * with made-up data. Source MMIO reads go through the actual address space.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-dcc.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bswap.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "system/memory.h"

#define DCC_REG_SIZE           0x1000
#define DCC_RAM_SIZE           0x6000
#define DCC_RAM_OFFSET         0x1a000
#define DCC_RAM_WORDS          (DCC_RAM_SIZE / 4)
#define DCC_LISTS              8
#define DCC_LIST_STRIDE         0x80
#define DCC_LIST_FIRST          0x2c

#define DCC_HW_VERSION         0x00
#define DCC_HW_INFO             0x04
#define DCC_LL_NUM_INFO         0x10
#define DCC_STATUS             0x1c

/* Offsets within one map2 list's register bank. */
#define DCC_LL_LOCK             0x00
#define DCC_LL_CFG              0x04
#define DCC_LL_BASE             0x08
#define DCC_FD_BASE             0x0c
#define DCC_LL_TIMEOUT          0x10
#define DCC_TRANS_TIMEOUT       0x14
#define DCC_LL_INT_ENABLE       0x18
#define DCC_LL_INT_STATUS       0x1c
#define DCC_FDA_CAPTURED        0x20
#define DCC_LLA_CAPTURED        0x24
#define DCC_LL_CRC_CAPTURED     0x28
#define DCC_LL_SW_TRIGGER       0x2c
#define DCC_LL_BUS_STATUS       0x30

#define DCC_DESCRIPTOR_TYPE    0xc0000000u
#define DCC_DESC_ADDRESS       0x00000000u
#define DCC_DESC_LINK          0xc0000000u
#define DCC_DESC_WRITE         BIT(28)
#define DCC_CFG_TRIGGER        (BIT(8) | BIT(9))
#define DCC_CFG_KNOWN           (BIT(0) | BIT(4) | DCC_CFG_TRIGGER)

typedef struct QcomDccList {
    uint32_t lock;
    uint32_t cfg;
    uint32_t base;
    uint32_t data_base;
    uint32_t timeout;
    uint32_t transaction_timeout;
    uint32_t interrupt_enable;
    uint32_t interrupt_status;
    uint32_t bus_status;
    bool busy;
    bool stalled;
    int64_t deadline;
} QcomDccList;

struct QcomDccState {
    SysBusDevice parent_obj;
    MemoryRegion registers;
    MemoryRegion sram;
    MemoryRegion *memory;
    AddressSpace dma_as;
    QEMUTimer *timer;
    QcomDccList lists[DCC_LISTS];
    uint64_t capture_delay_ns;
    uint32_t geometry;
    bool dma_initialized;
};

static void dcc_schedule(QcomDccState *s)
{
    int64_t next = INT64_MAX;

    for (unsigned i = 0; i < DCC_LISTS; i++) {
        QcomDccList *l = &s->lists[i];

        if (l->busy && !l->stalled) {
            next = MIN(next, l->deadline);
        }
    }
    if (next == INT64_MAX) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, next);
    }
}

static void dcc_stall(QcomDccState *s, unsigned list, const char *reason)
{
    QcomDccList *l = &s->lists[list];

    l->busy = true;
    l->stalled = true;
    l->deadline = -1;
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_DCC
                  ": list %u %s; capture incomplete\n", list, reason);
}

static bool dcc_sram_index(uint32_t address, uint32_t *index)
{
    uint64_t byte_address = (uint64_t)address * 4;

    if (byte_address < DCC_RAM_OFFSET ||
        byte_address >= DCC_RAM_OFFSET + DCC_RAM_SIZE) {
        return false;
    }
    *index = byte_address - DCC_RAM_OFFSET;
    return true;
}

/*
 * Decode and bound the entire read-only list before performing source reads.
 * LINK pairs are 15-bit relative-word-offset/length tuples. A zero-length
 * second pair is the driver's single-pair padding; an all-zero LINK ends
 * the list. Each ADDRESS resets the relative cursor.
 */
static bool dcc_decode(QcomDccState *s, unsigned list,
                       uint64_t *addresses, unsigned *count,
                       uint32_t *destination)
{
    QcomDccList *l = &s->lists[list];
    uint8_t *ram = memory_region_get_ram_ptr(&s->sram);
    uint32_t start, pc, dest, previous = 0;
    uint64_t base = 0;
    bool have_address = false;

    *count = 0;
    if (!dcc_sram_index(l->base, &start) ||
        !dcc_sram_index(l->data_base, &dest)) {
        dcc_stall(s, list, "SRAM base outside supplied aperture");
        return false;
    }
    pc = start;
    for (unsigned descriptors = 0; descriptors < DCC_RAM_WORDS;
         descriptors++) {
        uint32_t descriptor;

        if (pc > DCC_RAM_SIZE - 4) {
            break;
        }
        descriptor = ldl_le_p(ram + pc);
        pc += 4;
        if (descriptor == DCC_DESC_LINK) {
            uint64_t end = (uint64_t)dest + (uint64_t)*count * 4;

            if (end > DCC_RAM_SIZE ||
                (*count && dest < pc && end > start)) {
                dcc_stall(s, list, "capture overlaps descriptors or SRAM end");
                return false;
            }
            *destination = dest;
            return true;
        }
        switch (descriptor & DCC_DESCRIPTOR_TYPE) {
        case DCC_DESC_ADDRESS:
            if (descriptor & DCC_DESC_WRITE) {
                dcc_stall(s, list, "write descriptor is unsupported");
                return false;
            }
            base = (uint64_t)(descriptor & 0x0fffffff) << 4;
            previous = 0;
            have_address = true;
            break;
        case DCC_DESC_LINK:
            if (!have_address) {
                dcc_stall(s, list, "LINK without an ADDRESS");
                return false;
            }
            for (unsigned pair = 0; pair < 2; pair++) {
                uint32_t tuple = (descriptor >> (pair * 15)) & 0x7fff;
                unsigned delta = tuple & 0xff;
                unsigned length = tuple >> 8;
                uint64_t offset, last;

                if (!length) {
                    if (pair && tuple <= 1) {
                        continue;
                    }
                    dcc_stall(s, list, "unsupported zero-length LINK tuple");
                    return false;
                }
                offset = (uint64_t)previous + delta;
                last = base + (offset + length - 1) * 4;
                if (last > UINT32_MAX - 3 ||
                    *count + length > DCC_RAM_WORDS) {
                    dcc_stall(s, list, "capture address/count overflow");
                    return false;
                }
                for (unsigned n = 0; n < length; n++) {
                    addresses[(*count)++] = base + (offset + n) * 4;
                }
                previous = offset + length - 1;
            }
            break;
        default:
            dcc_stall(s, list, "loop or read-modify-write descriptor unsupported");
            return false;
        }
    }
    dcc_stall(s, list, "unterminated or oversized descriptor list");
    return false;
}

static void dcc_capture(QcomDccState *s, unsigned list)
{
    QcomDccList *l = &s->lists[list];
    g_autofree uint64_t *addresses = g_new(uint64_t, DCC_RAM_WORDS);
    uint8_t *ram = memory_region_get_ram_ptr(&s->sram);
    uint32_t destination;
    unsigned count;

    if (!dcc_decode(s, list, addresses, &count, &destination)) {
        return;
    }
    for (unsigned n = 0; n < count; n++) {
        uint8_t data[4];
        MemTxResult result;

        result = address_space_read(&s->dma_as, addresses[n],
                                    MEMTXATTRS_UNSPECIFIED, data, sizeof(data));
        if (result != MEMTX_OK) {
            /*
             * The driver checks any nonzero low-two-bit bus-access status
             * and clears it with 3. The exact silicon error-bit taxonomy is
             * not specified by our sources: do not invent it here. The
             * capture remains busy, with a diagnostic, rather than reporting
             * successful completion or fabricating zero data.
             */
            dcc_stall(s, list, "source transaction failed");
            return;
        }
        memcpy(ram + destination + n * 4, data, sizeof(data));
        memory_region_set_dirty(&s->sram, destination + n * 4, 4);
    }
    l->busy = false;
    l->deadline = -1;
    /*
     * Sources establish polling completion, but not completion-IRQ bit
     * meanings. Do not invent an interrupt or a CRC result.
     */
}

static void dcc_timer(void *opaque)
{
    QcomDccState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    for (unsigned i = 0; i < DCC_LISTS; i++) {
        QcomDccList *l = &s->lists[i];

        if (l->busy && !l->stalled && l->deadline <= now) {
            dcc_capture(s, i);
        }
    }
    dcc_schedule(s);
}

static void dcc_trigger(QcomDccState *s, unsigned list)
{
    QcomDccList *l = &s->lists[list];

    if (l->busy) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_DCC
                      ": trigger of busy list %u ignored\n", list);
        return;
    }
    l->busy = true;
    if (!(l->lock & 1)) {
        dcc_stall(s, list, "list has no software owner");
    } else if (l->cfg & ~DCC_CFG_KNOWN) {
        dcc_stall(s, list, "reserved configuration bits");
    } else if (l->cfg & (BIT(0) | BIT(4))) {
        dcc_stall(s, list, "CRC or ATB sink unsupported");
    } else {
        l->stalled = false;
        l->deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
                      s->capture_delay_ns;
    }
    dcc_schedule(s);
}

static bool dcc_list_offset(hwaddr address, unsigned *list, unsigned *reg)
{
    if (address < DCC_LIST_FIRST) {
        return false;
    }
    *list = (address - DCC_LIST_FIRST) / DCC_LIST_STRIDE;
    *reg = (address - DCC_LIST_FIRST) % DCC_LIST_STRIDE;
    return *list < DCC_LISTS && *reg <= DCC_LL_BUS_STATUS;
}

static MemTxResult dcc_read_result(uint64_t *data, uint32_t value)
{
    *data = value;
    return MEMTX_OK;
}

static MemTxResult dcc_read(void *opaque, hwaddr address, uint64_t *data,
                            unsigned size, MemTxAttrs attrs)
{
    QcomDccState *s = opaque;
    unsigned list, reg;

    switch (address) {
    case DCC_HW_VERSION:
        /* Explicitly unspecified reference revision, not an unknown access. */
        return dcc_read_result(data, 0);
    case DCC_HW_INFO:
        /* Reference map2 + fixed 16-bit loop-offset indication. */
        return dcc_read_result(data, 0x7f);
    case DCC_LL_NUM_INFO:
        return dcc_read_result(data, DCC_LISTS);
    case DCC_STATUS:
        for (unsigned i = 0; i < DCC_LISTS; i++) {
            if (s->lists[i].busy) {
                return dcc_read_result(data, BIT(1));
            }
        }
        return dcc_read_result(data, 0);
    default:
        break;
    }
    if (dcc_list_offset(address, &list, &reg)) {
        QcomDccList *l = &s->lists[list];

        switch (reg) {
        case DCC_LL_LOCK: return dcc_read_result(data, l->lock);
        case DCC_LL_CFG: return dcc_read_result(data, l->cfg);
        case DCC_LL_BASE: return dcc_read_result(data, l->base);
        case DCC_FD_BASE: return dcc_read_result(data, l->data_base);
        case DCC_LL_TIMEOUT: return dcc_read_result(data, l->timeout);
        case DCC_TRANS_TIMEOUT:
            return dcc_read_result(data, l->transaction_timeout);
        case DCC_LL_INT_ENABLE:
            return dcc_read_result(data, l->interrupt_enable);
        case DCC_LL_INT_STATUS:
            return dcc_read_result(data, l->interrupt_status);
        case DCC_LL_SW_TRIGGER: return dcc_read_result(data, 0);
        case DCC_LL_BUS_STATUS: return dcc_read_result(data, l->bus_status);
        default: break;
        }
    }
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_DCC
                  ": unsupported read at 0x%" HWADDR_PRIx "\n", address);
    *data = 0;
    return MEMTX_ERROR;
}

static void dcc_write(void *opaque, hwaddr address, uint64_t value,
                       unsigned size)
{
    QcomDccState *s = opaque;
    unsigned list, reg;

    if (!dcc_list_offset(address, &list, &reg)) {
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_DCC
                      ": unsupported write at 0x%" HWADDR_PRIx "\n", address);
        return;
    }
    QcomDccList *l = &s->lists[list];

    switch (reg) {
    case DCC_LL_LOCK:
        l->lock = value & 1;
        if (!l->lock) {
            /* Releasing ownership cancels this functional transaction. */
            l->busy = false;
            l->stalled = false;
            l->deadline = -1;
            dcc_schedule(s);
        }
        return;
    case DCC_LL_CFG:
        /* Linux gates hardware triggers around its software trigger. */
        if (!l->busy || !((l->cfg ^ value) & ~DCC_CFG_TRIGGER)) {
            l->cfg = value;
            return;
        }
        break;
    case DCC_LL_BASE:
    case DCC_FD_BASE:
    case DCC_LL_TIMEOUT:
    case DCC_TRANS_TIMEOUT:
        if (l->busy) {
            break;
        }
        if (reg == DCC_LL_BASE) {
            l->base = value;
        } else if (reg == DCC_FD_BASE) {
            l->data_base = value;
        } else if (reg == DCC_LL_TIMEOUT) {
            l->timeout = value;
        } else {
            l->transaction_timeout = value;
        }
        return;
    case DCC_LL_INT_ENABLE:
        l->interrupt_enable = value & 7;
        if (value) {
            qemu_log_mask(LOG_UNIMP, TYPE_QCOM_DCC
                          ": interrupt delivery is not modeled\n");
        }
        return;
    case DCC_LL_INT_STATUS:
        l->interrupt_status &= ~(value & 7);
        return;
    case DCC_LL_BUS_STATUS:
        l->bus_status &= ~(value & 3);
        return;
    case DCC_LL_SW_TRIGGER:
        if (value == 1) {
            dcc_trigger(s, list);
        } else if (value) {
            dcc_stall(s, list, "invalid software trigger value");
            dcc_schedule(s);
        }
        return;
    default:
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_DCC
                      ": unsupported write at 0x%" HWADDR_PRIx "\n", address);
        return;
    }
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_DCC
                  ": mutation of running list %u rejected\n", list);
}

static const MemoryRegionOps dcc_ops = {
    .read_with_attrs = dcc_read,
    .write = dcc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static void dcc_reset(Object *obj, ResetType type)
{
    QcomDccState *s = QCOM_DCC(obj);

    memset(s->lists, 0, sizeof(s->lists));
    for (unsigned i = 0; i < DCC_LISTS; i++) {
        s->lists[i].deadline = -1;
    }
    timer_del(s->timer);
    /* SRAM has normal RAM retention; firmware/guest initializes its bytes. */
}

static int dcc_post_load(void *opaque, int version)
{
    QcomDccState *s = opaque;

    for (unsigned i = 0; i < DCC_LISTS; i++) {
        QcomDccList *l = &s->lists[i];

        if (l->lock > 1 || (l->interrupt_status & ~7u) ||
            (l->interrupt_enable & ~7u) || (l->bus_status & ~3u) ||
            (l->stalled && !l->busy) ||
            (l->busy && !l->stalled &&
             (l->deadline < 0 || l->lock != 1 ||
              (l->cfg & ~DCC_CFG_TRIGGER))) ||
            ((!l->busy || l->stalled) && l->deadline != -1)) {
            return -EINVAL;
        }
    }
    dcc_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_dcc_list = {
    .name = TYPE_QCOM_DCC "/list",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(lock, QcomDccList),
        VMSTATE_UINT32(cfg, QcomDccList),
        VMSTATE_UINT32(base, QcomDccList),
        VMSTATE_UINT32(data_base, QcomDccList),
        VMSTATE_UINT32(timeout, QcomDccList),
        VMSTATE_UINT32(transaction_timeout, QcomDccList),
        VMSTATE_UINT32(interrupt_enable, QcomDccList),
        VMSTATE_UINT32(interrupt_status, QcomDccList),
        VMSTATE_UINT32(bus_status, QcomDccList),
        VMSTATE_BOOL(busy, QcomDccList),
        VMSTATE_BOOL(stalled, QcomDccList),
        VMSTATE_INT64(deadline, QcomDccList),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_dcc = {
    .name = TYPE_QCOM_DCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = dcc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(geometry, QcomDccState),
        VMSTATE_UINT64_EQUAL(capture_delay_ns, QcomDccState),
        VMSTATE_STRUCT_ARRAY(lists, QcomDccState, DCC_LISTS, 1,
                             vmstate_dcc_list, QcomDccList),
        VMSTATE_TIMER_PTR(timer, QcomDccState),
        VMSTATE_END_OF_LIST()
    },
};

static const Property dcc_properties[] = {
    DEFINE_PROP_LINK("memory", QcomDccState, memory, TYPE_MEMORY_REGION,
                     MemoryRegion *),
    DEFINE_PROP_UINT64("capture-delay-ns", QcomDccState, capture_delay_ns, 1000),
};

static void dcc_realize(DeviceState *dev, Error **errp)
{
    QcomDccState *s = QCOM_DCC(dev);

    if (!s->memory) {
        error_setg(errp, TYPE_QCOM_DCC " requires a memory link");
        return;
    }
    if (s->capture_delay_ns < 1 || s->capture_delay_ns > 1000000000) {
        error_setg(errp, TYPE_QCOM_DCC
                    " capture-delay-ns must be between 1 and 1000000000");
        return;
    }
    address_space_init(&s->dma_as, s->memory, TYPE_QCOM_DCC "/dma");
    s->dma_initialized = true;
}

static void dcc_init(Object *obj)
{
    QcomDccState *s = QCOM_DCC(obj);

    s->geometry = 0x00020800; /* ABI guard: map2, eight reference lists. */
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dcc_timer, s);
    memory_region_init_io(&s->registers, obj, &dcc_ops, s,
                          TYPE_QCOM_DCC "/registers", DCC_REG_SIZE);
    memory_region_init_ram(&s->sram, obj, TYPE_QCOM_DCC "/sram",
                           DCC_RAM_SIZE, &error_fatal);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->registers);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->sram);
}

static void dcc_finalize(Object *obj)
{
    QcomDccState *s = QCOM_DCC(obj);

    timer_free(s->timer);
    if (s->dma_initialized) {
        address_space_destroy(&s->dma_as);
    }
}

static void dcc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm DCC v2 reference map2 SRAM capture subset";
    dc->realize = dcc_realize;
    dc->vmsd = &vmstate_dcc;
    device_class_set_props(dc, dcc_properties);
    rc->phases.hold = dcc_reset;
}

static const TypeInfo dcc_type = {
    .name = TYPE_QCOM_DCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomDccState),
    .instance_init = dcc_init,
    .instance_finalize = dcc_finalize,
    .class_init = dcc_class_init,
};

static void dcc_register_types(void)
{
    type_register_static(&dcc_type);
}

type_init(dcc_register_types)
