/*
 * Qualcomm apps SMMUv500 TCU/TBU digital reference companion.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Pinned OnePlus arm-smmu.c and original TB371FC DT establish the register
 * protocol and ten TBU ranges. The explicit version and 1 us delays are
 * reference assumptions, not measured silicon identity or timing.
 * Translation and faults use the linked architectural SMMUv2 core.
 * See tb371fc/docs/smmuv500-reference.md.
 */
#include "qemu/osdep.h"
#include "hw/arm/qcom-smmuv500.h"
#include "hw/arm/arm-smmuv2.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/atomic.h"
#include "qemu/bitops.h"
#include "qemu/bswap.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "qemu/units.h"
#include "system/qtest.h"

#define TBU_COUNT 10
#define TBU_SIDS 0x400
#define CONTAINER_SIZE 0x28000
#define CACHE_SIZE (16 * MiB)
#define REFERENCE_VERSION UINT32_C(0x02000000)
#define DELAY_NS 1000
#define HALT_REQ (UINT64_C(1) << 16)
#define SID_MASK UINT64_C(0x3ff)
#define VA_MASK ((UINT64_C(1) << 36) - 1)
#define AXUSER_MASK (UINT64_C(0xff) << 36)
#define TRIGGER_MASK UINT64_C(0x1ff)
#define PAR_MASK UINT64_C(0x0000fffffffff001)
#define ALL_TBUS ((1U << TBU_COUNT) - 1)

enum WindowKind {
    W_VERSION, W_SEMAPHORE, W_HALT_SID, W_VA, W_TRIGGER, W_STATUS,
    W_PAR, W_AXUSER, W_COUNT
};

typedef struct Smmuv500Window {
    QcomSmmuv500State *s;
    unsigned tbu;
    enum WindowKind kind;
    MemoryRegion mr;
} Smmuv500Window;

typedef struct Smmuv500Tbu {
    uint64_t halt_sid;
    uint64_t va;
    uint64_t trigger;
    uint64_t axuser;
    uint64_t par;
    bool halt_pending;
    bool halted;
    bool ecats_pending;
    int64_t halt_deadline_ns;
    int64_t ecats_deadline_ns;
    uint64_t captured_va;
    uint32_t captured_sid;
    bool captured_write;
} Smmuv500Tbu;

struct QcomSmmuv500State {
    SysBusDevice parent_obj;
    DeviceState *smmu;
    MemoryRegion container;
    Smmuv500Window version_window;
    Smmuv500Window windows[TBU_COUNT][W_COUNT - 1];
    Smmuv500Tbu tbu[TBU_COUNT];
    QEMUTimer *timer;
    bool ready;
    bool attached;
    bool enabled_reference;
    uint32_t reference_profile;
    uint32_t reference_hw_version;
    uint64_t tcu_base;
    uint32_t num_tbus;
    int32_t ecats_owner;
    /* Only this word is read from a possible emulated DMA thread. */
    uint32_t dma_blocked_mask;

    /* A real external IOMMU cache client, created only under qtest. */
    MemoryRegion cache_mmio;
    MemoryRegion *cache_iommu;
    IOMMUNotifier cache_notifier;
    bool cache_registered;
    bool cache_valid;
    IOMMUTLBEntry cache_entry;
    uint64_t cache_hits;
    uint64_t cache_fills;
    uint64_t cache_unmaps;
};

static const char *const window_names[W_COUNT] = {
    "version", "semaphore", "halt-sid", "va", "trigger", "status",
    "par", "axuser",
};

static hwaddr tbu_offset(unsigned tbu)
{
    return 0x3000 + tbu * 0x4000;
}

static uint32_t tbu_status(const Smmuv500Tbu *t)
{
    return (t->halted ? 2 : 0) | (t->ecats_pending ? 1 : 0);
}

static uint32_t requested_mask(QcomSmmuv500State *s)
{
    uint32_t mask = 0;
    unsigned i;

    for (i = 0; i < TBU_COUNT; i++) {
        if (s->tbu[i].halt_sid & HALT_REQ) {
            mask |= 1U << i;
        }
    }
    return mask;
}

static bool smmuv500_stream_blocked(void *opaque, uint16_t sid)
{
    QcomSmmuv500State *s = opaque;
    unsigned tbu = sid / TBU_SIDS;

    return tbu < TBU_COUNT &&
           (qatomic_read(&s->dma_blocked_mask) & (1U << tbu));
}

static void smmuv500_publish_blocker(QcomSmmuv500State *s, bool invalidate)
{
    uint32_t mask = s->enabled_reference ? requested_mask(s) : 0;

    qatomic_set(&s->dma_blocked_mask, mask);
    if (invalidate) {
        arm_smmuv2_stream_permissions_changed(s->smmu);
    }
}

static void smmuv500_schedule(QcomSmmuv500State *s)
{
    int64_t next = INT64_MAX;
    unsigned i;

    timer_del(s->timer);
    if (!s->ready || !s->enabled_reference) {
        return;
    }
    for (i = 0; i < TBU_COUNT; i++) {
        Smmuv500Tbu *t = &s->tbu[i];

        if (t->halt_pending) {
            next = MIN(next, t->halt_deadline_ns);
        }
        if (t->ecats_pending) {
            next = MIN(next, t->ecats_deadline_ns);
        }
    }
    if (next != INT64_MAX) {
        timer_mod(s->timer, next);
    }
}

static void smmuv500_timer(void *opaque)
{
    QcomSmmuv500State *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (!s->ready || !s->enabled_reference) {
        return;
    }
    for (i = 0; i < TBU_COUNT; i++) {
        Smmuv500Tbu *t = &s->tbu[i];

        if (t->halt_pending && now >= t->halt_deadline_ns) {
            t->halt_pending = false;
            t->halt_deadline_ns = 0;
            t->halted = true;
        }
        if (t->ecats_pending && now >= t->ecats_deadline_ns) {
            IOMMUTLBEntry entry;
            IOMMUAccessFlags access = t->captured_write ?
                                      IOMMU_WO : IOMMU_RO;
            uint64_t pa;

            t->ecats_pending = false;
            t->ecats_deadline_ns = 0;
            entry = arm_smmuv2_debug_translate_entry(s->smmu, t->captured_sid,
                                               t->captured_va,
                                               t->captured_write);
            pa = entry.translated_addr | (t->captured_va & entry.addr_mask);
            if (!(entry.perm & access) || pa > VA_MASK) {
                /*
                 * An unrepresentable result must not be truncated into
                 * a successful translation. The core owns walk faults;
                 * this output-width restriction invents no core FSR.
                 */
                t->par = 1;
            } else {
                /* Vendor ECATS packs the complete byte PA at bit 12. */
                t->par = pa << 12;
            }
        }
    }
    smmuv500_schedule(s);
}

static MemTxResult smmuv500_error(Smmuv500Window *w, const char *operation,
                                  hwaddr offset, unsigned size,
                                  const char *reason)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SMMUV500
                  ": %s %s tbu %u +0x%" HWADDR_PRIx
                  " size %u: %s\n", operation, window_names[w->kind],
                  w->tbu, offset, size, reason);
    return MEMTX_ERROR;
}

static uint64_t window_value(QcomSmmuv500State *s, unsigned tbu,
                              enum WindowKind kind)
{
    Smmuv500Tbu *t = &s->tbu[tbu];

    switch (kind) {
    case W_VERSION:
        return s->reference_hw_version;
    case W_HALT_SID:
        return t->halt_sid;
    case W_VA:
        return t->va;
    case W_TRIGGER:
        return t->trigger;
    case W_STATUS:
        return tbu_status(t);
    case W_PAR:
        return t->par;
    case W_AXUSER:
        return t->axuser;
    default:
        g_assert_not_reached();
    }
}

static MemTxResult smmuv500_read(void *opaque, hwaddr offset, uint64_t *value,
                                 unsigned size, MemTxAttrs attrs)
{
    Smmuv500Window *w = opaque;
    QcomSmmuv500State *s = w->s;

    *value = 0;
    if (!s->enabled_reference || !s->ready) {
        return smmuv500_error(w, "read", offset, size, "unknown profile");
    }
    if (w->kind == W_SEMAPHORE) {
        if (s->ecats_owner == -1) {
            s->ecats_owner = w->tbu;
            *value = 1;
        }
        /* A repeated read, including by the owner, does not acquire again. */
    } else {
        *value = window_value(s, w->tbu, w->kind);
        if (size == 4) {
            *value = extract64(*value, offset * 8, 32);
        }
    }
    return MEMTX_OK;
}

static bool new_deadline(int64_t *deadline)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (now < 0 || now >= INT64_MAX - DELAY_NS) {
        return false;
    }
    *deadline = now + DELAY_NS;
    return true;
}

static void cancel_ecats(Smmuv500Tbu *t)
{
    t->ecats_pending = false;
    t->ecats_deadline_ns = 0;
    t->captured_sid = 0;
    t->captured_va = 0;
    t->captured_write = false;
    t->par = 1;
}

static MemTxResult smmuv500_write(void *opaque, hwaddr offset, uint64_t value,
                                  unsigned size, MemTxAttrs attrs)
{
    Smmuv500Window *w = opaque;
    QcomSmmuv500State *s = w->s;
    Smmuv500Tbu *t = &s->tbu[w->tbu];
    uint64_t old, mask;
    int64_t deadline;

    if (!s->enabled_reference || !s->ready) {
        return smmuv500_error(w, "write", offset, size, "unknown profile");
    }
    if (w->kind == W_VERSION || w->kind == W_STATUS || w->kind == W_PAR) {
        return smmuv500_error(w, "write", offset, size, "read-only register");
    }
    if (w->kind == W_SEMAPHORE) {
        if (value || s->ecats_owner != (int32_t)w->tbu || t->ecats_pending) {
            return smmuv500_error(w, "write", offset, size,
                                  "invalid semaphore release");
        }
        s->ecats_owner = -1;
        return MEMTX_OK;
    }

    old = window_value(s, w->tbu, w->kind);
    if (size == 4) {
        value = deposit64(old, offset * 8, 32, value);
    }
    switch (w->kind) {
    case W_HALT_SID:
        mask = HALT_REQ | SID_MASK;
        break;
    case W_VA:
        mask = VA_MASK;
        break;
    case W_TRIGGER:
        mask = TRIGGER_MASK;
        break;
    case W_AXUSER:
        mask = AXUSER_MASK;
        break;
    default:
        g_assert_not_reached();
    }
    if (value & ~mask) {
        return smmuv500_error(w, "write", offset, size, "unsupported bits");
    }

    switch (w->kind) {
    case W_HALT_SID:
        if ((old & HALT_REQ) && !(value & HALT_REQ) && t->ecats_pending) {
            return smmuv500_error(w, "write", offset, size,
                                  "ECATS still pending");
        }
        if (!(old & HALT_REQ) && (value & HALT_REQ)) {
            if (!new_deadline(&deadline)) {
                return smmuv500_error(w, "write", offset, size,
                                      "virtual deadline overflow");
            }
            t->halt_pending = true;
            t->halted = false;
            t->halt_deadline_ns = deadline;
        } else if (!(value & HALT_REQ)) {
            t->halt_pending = false;
            t->halted = false;
            t->halt_deadline_ns = 0;
        }
        t->halt_sid = value;
        if ((old ^ value) & HALT_REQ) {
            /*
             * The DMA gate takes effect immediately; ACK follows the
             * reference delay. Notify actual cache clients after gating.
             */
            smmuv500_publish_blocker(s, true);
        }
        break;
    case W_VA:
        t->va = value;
        break;
    case W_AXUSER:
        t->axuser = value;
        break;
    case W_TRIGGER:
        /* A zero high half completes a split 64-bit write, not a retrigger. */
        if (size == 4 && offset == 4 && value == old) {
            return MEMTX_OK;
        }
        if (value & 1) {
            if ((old & 1) || t->ecats_pending) {
                return smmuv500_error(w, "write", offset, size,
                                      "trigger must be cleared before reuse");
            }
            if ((value != 0xfd && value != 0xff) ||
                s->ecats_owner != (int32_t)w->tbu || !t->halted ||
                t->axuser != AXUSER_MASK) {
                return smmuv500_error(w, "write", offset, size,
                                      "unsupported ECATS request or state");
            }
            if (!new_deadline(&deadline)) {
                return smmuv500_error(w, "write", offset, size,
                                      "virtual deadline overflow");
            }
            t->captured_sid = w->tbu * TBU_SIDS + (t->halt_sid & SID_MASK);
            t->captured_va = t->va;
            t->captured_write = value & 2;
            t->ecats_pending = true;
            t->ecats_deadline_ns = deadline;
        } else {
            /* The vendor sequence uses zero to reset the debug engine. */
            cancel_ecats(t);
        }
        t->trigger = value;
        break;
    default:
        g_assert_not_reached();
    }
    smmuv500_schedule(s);
    return MEMTX_OK;
}

static const MemoryRegionOps smmuv500_word_ops = {
    .read_with_attrs = smmuv500_read,
    .write_with_attrs = smmuv500_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 4,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 4 },
};

static const MemoryRegionOps smmuv500_pair_ops = {
    .read_with_attrs = smmuv500_read,
    .write_with_attrs = smmuv500_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 8,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 8 },
};

static void smmuv500_cache_unmap(IOMMUNotifier *notifier, IOMMUTLBEntry *entry)
{
    QcomSmmuv500State *s = container_of(notifier, QcomSmmuv500State,
                                       cache_notifier);

    s->cache_valid = false;
    s->cache_unmaps++;
}

/*
 * QTest-only external DMA cache. A cache hit deliberately uses the saved
 * entry and does not consult the TBU or rewalk. Only a real UNMAP callback
 * revokes that entry when permissions change. Restrict the fixture's target
 * to RAM so an adversarial test PTE cannot recurse into this MMIO region.
 */
static MemTxResult smmuv500_cache_access(QcomSmmuv500State *s, hwaddr addr,
                                         uint8_t *buf, unsigned size,
                                         bool write)
{
    IOMMUMemoryRegion *iommu = memory_region_get_iommu(s->cache_iommu);
    IOMMUMemoryRegionClass *imrc =
        memory_region_get_iommu_class_nocheck(iommu);
    IOMMUAccessFlags access = write ? IOMMU_WO : IOMMU_RO;
    MemoryRegionSection section;
    uint64_t pa;
    MemTxResult result = MEMTX_DECODE_ERROR;
    bool hit = s->cache_valid &&
               (s->cache_entry.perm & access) &&
               addr >= s->cache_entry.iova &&
               addr + size - 1 <=
                   s->cache_entry.iova + s->cache_entry.addr_mask;

    if (hit) {
        s->cache_hits++;
    } else {
        s->cache_entry = imrc->translate(iommu, addr, access, 0);
        s->cache_valid = !!(s->cache_entry.perm & access);
        if (!s->cache_valid) {
            return MEMTX_ERROR;
        }
        s->cache_fills++;
    }
    pa = s->cache_entry.translated_addr | (addr & s->cache_entry.addr_mask);
    section = memory_region_find(s->cache_entry.target_as->root, pa, size);
    if (section.mr && memory_region_is_ram(section.mr) &&
        int128_get64(section.size) == size) {
        result = address_space_rw(s->cache_entry.target_as, pa,
                                  MEMTXATTRS_UNSPECIFIED, buf, size, write);
    }
    if (section.mr) {
        memory_region_unref(section.mr);
    }
    return result;
}

static MemTxResult smmuv500_cache_read(void *opaque, hwaddr offset,
                                       uint64_t *value, unsigned size,
                                       MemTxAttrs attrs)
{
    QcomSmmuv500State *s = opaque;
    uint8_t buf[8] = { 0 };
    MemTxResult result = smmuv500_cache_access(s, offset, buf, size, false);

    *value = size == 8 ? ldq_le_p(buf) : ldl_le_p(buf);
    return result;
}

static MemTxResult smmuv500_cache_write(void *opaque, hwaddr offset,
                                        uint64_t value, unsigned size,
                                        MemTxAttrs attrs)
{
    QcomSmmuv500State *s = opaque;
    uint8_t buf[8];

    if (size == 8) {
        stq_le_p(buf, value);
    } else {
        stl_le_p(buf, value);
    }
    return smmuv500_cache_access(s, offset, buf, size, true);
}

static const MemoryRegionOps smmuv500_cache_ops = {
    .read_with_attrs = smmuv500_cache_read,
    .write_with_attrs = smmuv500_cache_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 4, .max_access_size = 8,
               .unaligned = false },
    .impl = { .min_access_size = 4, .max_access_size = 8 },
};

static void smmuv500_reset_enter(Object *obj, ResetType type)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(obj);
    unsigned i;

    s->ready = false;
    timer_del(s->timer);
    s->ecats_owner = -1;
    memset(s->tbu, 0, sizeof(s->tbu));
    for (i = 0; i < TBU_COUNT; i++) {
        s->tbu[i].par = 1;
    }
    qatomic_set(&s->dma_blocked_mask, 0);
}

static void smmuv500_reset_hold(Object *obj, ResetType type)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(obj);

    s->ready = true;
    smmuv500_publish_blocker(s, true);
}

static int smmuv500_pre_load(void *opaque)
{
    QcomSmmuv500State *s = opaque;

    s->ready = false;
    timer_del(s->timer);
    qatomic_set(&s->dma_blocked_mask, s->enabled_reference ? ALL_TBUS : 0);
    return 0;
}

static bool valid_tbu_state(QcomSmmuv500State *s, unsigned i)
{
    Smmuv500Tbu *t = &s->tbu[i];
    bool request = t->halt_sid & HALT_REQ;

    if ((t->halt_sid & ~(HALT_REQ | SID_MASK)) ||
        (t->va & ~VA_MASK) || (t->trigger & ~TRIGGER_MASK) ||
        (t->axuser & ~AXUSER_MASK) || (t->par & ~PAR_MASK) ||
        ((t->par & 1) && t->par != 1) ||
        (t->captured_va & ~VA_MASK) ||
        t->captured_sid >= TBU_COUNT * TBU_SIDS) {
        return false;
    }
    if ((t->halt_pending && t->halted) ||
        (request != (t->halt_pending || t->halted)) ||
        (t->halt_pending ?
            (t->halt_deadline_ns <= 0 || t->halt_deadline_ns == INT64_MAX) :
            t->halt_deadline_ns != 0) ||
        (t->ecats_pending ?
            (t->ecats_deadline_ns <= 0 || t->ecats_deadline_ns == INT64_MAX) :
            t->ecats_deadline_ns != 0)) {
        return false;
    }
    if (t->trigger & 1) {
        if ((t->trigger != 0xfd && t->trigger != 0xff) ||
            t->captured_sid / TBU_SIDS != i ||
            t->captured_write != !!(t->trigger & 2)) {
            return false;
        }
    } else if (t->captured_va || t->captured_sid ||
               t->captured_write || t->par != 1) {
        /* Clearing TRIGGER resets the captured request and result. */
        return false;
    }
    if (t->ecats_pending &&
        (s->ecats_owner != (int32_t)i || !t->halted || !(t->trigger & 1))) {
        return false;
    }
    if (!s->enabled_reference &&
        (t->halt_sid || t->va || t->trigger || t->axuser ||
         t->par != 1 || t->halt_pending || t->halted || t->ecats_pending ||
         t->captured_va || t->captured_sid || t->captured_write)) {
        return false;
    }
    return true;
}

static int smmuv500_post_load(void *opaque, int version)
{
    QcomSmmuv500State *s = opaque;
    unsigned i;

    if (s->ecats_owner < -1 || s->ecats_owner >= TBU_COUNT ||
        (!s->enabled_reference && s->ecats_owner != -1)) {
        error_report(TYPE_QCOM_SMMUV500 ": invalid migrated ECATS owner");
        return -EINVAL;
    }
    for (i = 0; i < TBU_COUNT; i++) {
        if (!valid_tbu_state(s, i)) {
            error_report(TYPE_QCOM_SMMUV500
                         ": invalid migrated TBU %u state", i);
            return -EINVAL;
        }
    }
    s->ready = true;
    /*
     * Restore the live DMA gate and invalidate external cached translations
     * before the first MMIO access. No translation or deadline is executed
     * here; the restored virtual timer owns completion after resume.
     */
    smmuv500_publish_blocker(s, true);
    smmuv500_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_smmuv500_tbu = {
    .name = TYPE_QCOM_SMMUV500 "-tbu-state",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT64(halt_sid, Smmuv500Tbu),
        VMSTATE_UINT64(va, Smmuv500Tbu),
        VMSTATE_UINT64(trigger, Smmuv500Tbu),
        VMSTATE_UINT64(axuser, Smmuv500Tbu),
        VMSTATE_UINT64(par, Smmuv500Tbu),
        VMSTATE_BOOL(halt_pending, Smmuv500Tbu),
        VMSTATE_BOOL(halted, Smmuv500Tbu),
        VMSTATE_BOOL(ecats_pending, Smmuv500Tbu),
        VMSTATE_INT64(halt_deadline_ns, Smmuv500Tbu),
        VMSTATE_INT64(ecats_deadline_ns, Smmuv500Tbu),
        VMSTATE_UINT64(captured_va, Smmuv500Tbu),
        VMSTATE_UINT32(captured_sid, Smmuv500Tbu),
        VMSTATE_BOOL(captured_write, Smmuv500Tbu),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_smmuv500 = {
    .name = TYPE_QCOM_SMMUV500,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = smmuv500_pre_load,
    .post_load = smmuv500_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSmmuv500State),
        VMSTATE_UINT32_EQUAL(reference_hw_version, QcomSmmuv500State),
        VMSTATE_UINT64_EQUAL(tcu_base, QcomSmmuv500State),
        VMSTATE_UINT32_EQUAL(num_tbus, QcomSmmuv500State),
        VMSTATE_INT32(ecats_owner, QcomSmmuv500State),
        VMSTATE_STRUCT_ARRAY(tbu, QcomSmmuv500State, TBU_COUNT, 1,
                             vmstate_smmuv500_tbu, Smmuv500Tbu),
        VMSTATE_END_OF_LIST()
    },
};

enum Observation {
    OBS_TCU_BASE = 1, OBS_TBU_COUNT, OBS_OWNER, OBS_BLOCKED,
    OBS_BASE, OBS_STATUS_ADDRESS, OBS_SID_START,
    OBS_HALT_SID, OBS_VA, OBS_TRIGGER, OBS_AXUSER, OBS_PAR, OBS_STATUS,
    OBS_HALT_PENDING, OBS_HALTED, OBS_ECATS_PENDING,
    OBS_HALT_DEADLINE, OBS_ECATS_DEADLINE,
    OBS_CAPTURED_SID, OBS_CAPTURED_VA, OBS_CAPTURED_WRITE,
    OBS_CACHE_VALID, OBS_CACHE_HITS, OBS_CACHE_FILLS, OBS_CACHE_UNMAPS,
};

static void smmuv500_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(obj);
    unsigned encoded = GPOINTER_TO_UINT(opaque);
    unsigned field = encoded >> 8, i = encoded & 0xff;
    Smmuv500Tbu *t = &s->tbu[i];
    uint64_t u64 = 0;
    uint32_t u32 = 0;
    int64_t i64 = 0;
    int32_t i32 = 0;
    bool boolean = false;

    if (!s->enabled_reference &&
        (field == OBS_OWNER || field == OBS_BLOCKED ||
         (field >= OBS_HALT_SID && field <= OBS_CAPTURED_WRITE))) {
        error_setg(errp, TYPE_QCOM_SMMUV500 ": unknown profile");
        return;
    }
    switch (field) {
    case OBS_TCU_BASE:
        u64 = s->tcu_base;
        break;
    case OBS_BASE:
        u64 = s->tcu_base + tbu_offset(i);
        break;
    case OBS_STATUS_ADDRESS:
        u64 = s->tcu_base + 0x200 + i * 8;
        break;
    case OBS_HALT_SID:
        u64 = t->halt_sid;
        break;
    case OBS_VA:
        u64 = t->va;
        break;
    case OBS_TRIGGER:
        u64 = t->trigger;
        break;
    case OBS_AXUSER:
        u64 = t->axuser;
        break;
    case OBS_PAR:
        u64 = t->par;
        break;
    case OBS_CAPTURED_VA:
        u64 = t->captured_va;
        break;
    case OBS_CACHE_HITS:
        u64 = s->cache_hits;
        break;
    case OBS_CACHE_FILLS:
        u64 = s->cache_fills;
        break;
    case OBS_CACHE_UNMAPS:
        u64 = s->cache_unmaps;
        break;
    case OBS_TBU_COUNT:
        u32 = s->num_tbus;
        goto uint32;
    case OBS_BLOCKED:
        u32 = qatomic_read(&s->dma_blocked_mask);
        goto uint32;
    case OBS_SID_START:
        u32 = i * TBU_SIDS;
        goto uint32;
    case OBS_STATUS:
        u32 = tbu_status(t);
        goto uint32;
    case OBS_CAPTURED_SID:
        u32 = t->captured_sid;
        goto uint32;
    case OBS_OWNER:
        i32 = s->ecats_owner;
        visit_type_int32(v, name, &i32, errp);
        return;
    case OBS_HALT_DEADLINE:
        i64 = t->halt_deadline_ns;
        goto int64;
    case OBS_ECATS_DEADLINE:
        i64 = t->ecats_deadline_ns;
        goto int64;
    case OBS_HALT_PENDING:
        boolean = t->halt_pending;
        goto boolean;
    case OBS_HALTED:
        boolean = t->halted;
        goto boolean;
    case OBS_ECATS_PENDING:
        boolean = t->ecats_pending;
        goto boolean;
    case OBS_CAPTURED_WRITE:
        boolean = t->captured_write;
        goto boolean;
    case OBS_CACHE_VALID:
        boolean = s->cache_valid;
        goto boolean;
    default:
        g_assert_not_reached();
    }
    visit_type_uint64(v, name, &u64, errp);
    return;
uint32:
    visit_type_uint32(v, name, &u32, errp);
    return;
int64:
    visit_type_int64(v, name, &i64, errp);
    return;
boolean:
    visit_type_bool(v, name, &boolean, errp);
}

typedef struct ObservationInfo {
    const char *name;
    const char *type;
    enum Observation field;
} ObservationInfo;

static const ObservationInfo tbu_observations[] = {
    { "base", "uint64", OBS_BASE },
    { "status-address", "uint64", OBS_STATUS_ADDRESS },
    { "sid-start", "uint32", OBS_SID_START },
    { "halt-sid", "uint64", OBS_HALT_SID },
    { "va", "uint64", OBS_VA },
    { "trigger", "uint64", OBS_TRIGGER },
    { "axuser", "uint64", OBS_AXUSER },
    { "par", "uint64", OBS_PAR },
    { "status", "uint32", OBS_STATUS },
    { "halt-pending", "bool", OBS_HALT_PENDING },
    { "halted", "bool", OBS_HALTED },
    { "ecats-pending", "bool", OBS_ECATS_PENDING },
    { "halt-deadline-ns", "int64", OBS_HALT_DEADLINE },
    { "ecats-deadline-ns", "int64", OBS_ECATS_DEADLINE },
    { "captured-sid", "uint32", OBS_CAPTURED_SID },
    { "captured-va", "uint64", OBS_CAPTURED_VA },
    { "captured-write", "bool", OBS_CAPTURED_WRITE },
};

static void add_observation(Object *obj, const char *name, const char *type,
                             enum Observation field, unsigned tbu)
{
    object_property_add(obj, name, type, smmuv500_get, NULL, NULL,
                        GUINT_TO_POINTER((field << 8) | tbu));
}

static void init_window(QcomSmmuv500State *s, Smmuv500Window *w,
                         unsigned tbu, enum WindowKind kind,
                         hwaddr offset, unsigned size)
{
    g_autofree char *name = g_strdup_printf(TYPE_QCOM_SMMUV500 "-%s",
                                            window_names[kind]);

    w->s = s;
    w->tbu = tbu;
    w->kind = kind;
    memory_region_init_io(&w->mr, OBJECT(s),
                          size == 4 ? &smmuv500_word_ops : &smmuv500_pair_ops,
                          w, name, size);
    memory_region_add_subregion(&s->container, offset, &w->mr);
}

static void smmuv500_unrealize(DeviceState *dev)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(dev);

    s->ready = false;
    timer_del(s->timer);
    if (s->cache_registered) {
        memory_region_unregister_iommu_notifier(s->cache_iommu,
                                                &s->cache_notifier);
        s->cache_registered = false;
    }
    if (s->attached) {
        arm_smmuv2_set_stream_blocker(s->smmu, NULL, s, &error_abort);
        arm_smmuv2_stream_permissions_changed(s->smmu);
        s->attached = false;
    }
}

static void smmuv500_realize(DeviceState *dev, Error **errp)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(dev);

    if ((s->enabled_reference &&
         s->reference_hw_version != REFERENCE_VERSION) ||
        (!s->enabled_reference && s->reference_hw_version != 0)) {
        error_setg(errp, TYPE_QCOM_SMMUV500
                   ": reference mode requires explicit reference-hw-version"
                   " 0x02000000; unknown mode requires 0");
        return;
    }
    if (!s->smmu || !s->smmu->realized) {
        error_setg(errp, TYPE_QCOM_SMMUV500
                   ": a realized architectural SMMUv2 core is required");
        return;
    }
    s->reference_profile = s->enabled_reference ? 1 : 0;
    if (!arm_smmuv2_set_stream_blocker(s->smmu, smmuv500_stream_blocked,
                                      s, errp)) {
        return;
    }
    s->attached = true;
    if (qtest_enabled()) {
        s->cache_iommu = arm_smmuv2_get_dma_mr(s->smmu, 0x42);
        iommu_notifier_init(&s->cache_notifier, smmuv500_cache_unmap,
                            IOMMU_NOTIFIER_UNMAP, 0, CACHE_SIZE - 1, 0);
        if (memory_region_register_iommu_notifier(s->cache_iommu,
                                                  &s->cache_notifier,
                                                  errp) < 0) {
            smmuv500_unrealize(dev);
            return;
        }
        s->cache_registered = true;
        memory_region_init_io(&s->cache_mmio, OBJECT(s), &smmuv500_cache_ops,
                              s, TYPE_QCOM_SMMUV500 "-qtest-cache", CACHE_SIZE);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->cache_mmio);
    }
    s->ready = true;
}

static void smmuv500_init(Object *obj)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(obj);
    unsigned i, j;

    s->tcu_base = QCOM_SMMUV500_TCU_BASE;
    s->num_tbus = TBU_COUNT;
    s->ecats_owner = -1;
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, smmuv500_timer, s);
    memory_region_init(&s->container, obj, TYPE_QCOM_SMMUV500 "-sparse",
                       CONTAINER_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container);
    init_window(s, &s->version_window, 0, W_VERSION, 0x18, 4);
    for (i = 0; i < TBU_COUNT; i++) {
        hwaddr base = tbu_offset(i);

        s->tbu[i].par = 1;
        init_window(s, &s->windows[i][W_SEMAPHORE - 1], i, W_SEMAPHORE,
                    0x200 + i * 8, 4);
        init_window(s, &s->windows[i][W_HALT_SID - 1], i, W_HALT_SID,
                    base + 0x00, 8);
        init_window(s, &s->windows[i][W_VA - 1], i, W_VA, base + 0x08, 8);
        init_window(s, &s->windows[i][W_TRIGGER - 1], i, W_TRIGGER,
                    base + 0x18, 8);
        init_window(s, &s->windows[i][W_STATUS - 1], i, W_STATUS,
                    base + 0x20, 4);
        init_window(s, &s->windows[i][W_PAR - 1], i, W_PAR, base + 0x28, 8);
        init_window(s, &s->windows[i][W_AXUSER - 1], i, W_AXUSER,
                    base + 0x30, 8);
        for (j = 0; j < ARRAY_SIZE(tbu_observations); j++) {
            const ObservationInfo *o = &tbu_observations[j];
            g_autofree char *name = g_strdup_printf("tbu%u-%s", i, o->name);

            add_observation(obj, name, o->type, o->field, i);
        }
    }
    add_observation(obj, "tcu-base", "uint64", OBS_TCU_BASE, 0);
    add_observation(obj, "tbu-count", "uint32", OBS_TBU_COUNT, 0);
    add_observation(obj, "ecats-owner", "int32", OBS_OWNER, 0);
    add_observation(obj, "dma-blocked-mask", "uint32", OBS_BLOCKED, 0);
    if (qtest_enabled()) {
        add_observation(obj, "qtest-cache-valid", "bool", OBS_CACHE_VALID, 0);
        add_observation(obj, "qtest-cache-hits", "uint64", OBS_CACHE_HITS, 0);
        add_observation(obj, "qtest-cache-fills", "uint64", OBS_CACHE_FILLS, 0);
        add_observation(obj, "qtest-cache-unmaps", "uint64", OBS_CACHE_UNMAPS, 0);
    }
}

static void smmuv500_finalize(Object *obj)
{
    QcomSmmuv500State *s = QCOM_SMMUV500(obj);

    timer_free(s->timer);
}

static const Property smmuv500_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSmmuv500State,
                     enabled_reference, false),
    DEFINE_PROP_UINT32("reference-hw-version", QcomSmmuv500State,
                       reference_hw_version, 0),
    DEFINE_PROP_LINK("smmu", QcomSmmuv500State, smmu, TYPE_ARM_SMMUV2,
                     DeviceState *),
};

static void smmuv500_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm apps SMMUv500 finite reference companion";
    dc->realize = smmuv500_realize;
    dc->unrealize = smmuv500_unrealize;
    dc->vmsd = &vmstate_smmuv500;
    dc->user_creatable = false;
    device_class_set_props(dc, smmuv500_properties);
    rc->phases.enter = smmuv500_reset_enter;
    rc->phases.hold = smmuv500_reset_hold;
}

static const TypeInfo smmuv500_type = {
    .name = TYPE_QCOM_SMMUV500,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSmmuv500State),
    .instance_init = smmuv500_init,
    .instance_finalize = smmuv500_finalize,
    .class_init = smmuv500_class_init,
};

static void smmuv500_register_types(void)
{
    type_register_static(&smmuv500_type);
}
type_init(smmuv500_register_types)
