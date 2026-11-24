/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Arm SMMUv2 functional AArch64 stage-1 model.
 * Sources: Arm IHI0062D.c and Linux drivers/iommu/arm/arm-smmu/.
 *
 * Implemented: stream matching, per-context AArch64 walks (4 KiB by default,
 * opt-in 16/64 KiB), blocks, permissions/AF, ATS/PAR, faults and migration.
 * Not implemented: stage 2/nesting, AArch32 tables, stalls, hardware AF/DBM,
 * secure-world contexts or timing fidelity. Qualcomm TCU/TBU registers
 * belong to an optional board companion, not this architectural aperture.
 * Capability registers describe this model, not measured Qualcomm IDRs.
 *
 * Walks are uncached and limited to RAM/ROM table storage. Faults fail closed
 * with IOMMU_NONE; masked upstream fault responses are not a RAZ/WI sink.
 */
#include "qemu/osdep.h"
#include "hw/arm/arm-smmuv2.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/bitops.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/thread.h"
#include "system/address-spaces.h"

#define BIT32(n) (UINT32_C(1) << (n))
#define MAX_CB 128
#define MAX_SMR 128
#define WORDS 1024
#define TYPE_SMMUV2_IOMMU "arm-smmuv2-iommu"

enum {
    CR0 = 0x000, ACR = 0x010,
    ID0 = 0x020, ID1 = 0x024, ID2 = 0x028,
    GFAR = 0x040, GFSR = 0x048, GFSRRESTORE = 0x04c,
    GFSYNR0 = 0x050, GFSYNR1 = 0x054, GFSYNR2 = 0x058,
    TLBIVMID = 0x064, TLBIALLNSNH = 0x068, TLBIALLH = 0x06c,
    TLBGSYNC = 0x070, TLBGSTATUS = 0x074,
    SMR_BASE = 0x800, S2CR_BASE = 0xc00,
    SCTLR = 0x000, ACTLR = 0x004, RESUME = 0x008,
    TCR2 = 0x010, TTBR0 = 0x020, TTBR1 = 0x028, TCR = 0x030,
    CONTEXTIDR = 0x034, MAIR0 = 0x038, MAIR1 = 0x03c,
    PAR = 0x050, FSR = 0x058, FSRRESTORE = 0x05c,
    FAR = 0x060, FSYNR0 = 0x068, FSYNR1 = 0x06c,
    TLBSYNC = 0x7f0, TLBSTATUS = 0x7f4,
    ATS1PR = 0x800, ATS1PW = 0x808, ATS1UR = 0x810, ATS1UW = 0x818,
    ATSR = 0x8f0,
};
#define CR0_CLIENTPD BIT32(0)
#define CR0_GFIE BIT32(2)
#define CR0_GCFGFIE BIT32(5)
#define CR0_STALLD BIT32(8)
#define CR0_USFCFG BIT32(10)
#define CR0_SMCFCFG BIT32(21)
#define SCTLR_M BIT32(0)
#define SCTLR_E BIT32(4)
#define SCTLR_CFIE BIT32(6)
#define SCTLR_HUPCF BIT32(8)
#define FAULT_MULTI BIT32(31)
#define FSR_TF BIT32(1)
#define FSR_AFF BIT32(2)
#define FSR_PF BIT32(3)
#define FSR_EF BIT32(4)
#define FSR_ASF BIT32(7)
#define FSR_MASK (FAULT_MULTI | 0x1fe)
#define GFSR_ICF BIT32(0)
#define GFSR_USF BIT32(1)
#define GFSR_SMCF BIT32(2)
#define GFSR_UCBF BIT32(3)
#define GFSR_CAF BIT32(5)
#define GFSR_UUT BIT32(8)
#define PA_MASK UINT64_C(0x0000fffffffff000)
#define GRANULE_4K BIT32(0)
#define GRANULE_16K BIT32(1)
#define GRANULE_64K BIT32(2)
#define GRANULE_MASK (GRANULE_4K | GRANULE_16K | GRANULE_64K)

typedef struct SMMUv2Stream {
    IOMMUMemoryRegion iommu;
    AddressSpace as;
    ArmSMMUv2State *s;
    uint16_t sid;
} SMMUv2Stream;

struct ArmSMMUv2State {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    MemoryRegion *memory;
    AddressSpace memory_as;
    QemuMutex lock;
    ArmSMMUv2StreamBlocked stream_blocked;
    void *stream_blocked_opaque;
    /* Reconstructed by the legacy ANOC1/2 companion, not architectural state. */
    uint32_t debug_halt_groups;
    GPtrArray *streams;
    uint32_t mmio_size;
    uint32_t num_context_banks;
    uint32_t num_smrs;
    uint32_t aarch64_granules;
    uint32_t gr0[WORDS];
    uint32_t gr1[WORDS];
    uint32_t cb[MAX_CB][WORDS];
    qemu_irq global_irq[2];
    qemu_irq context_irq[MAX_CB];
};

typedef struct WalkResult {
    uint64_t pa;
    uint64_t mask;
    uint64_t par;
    uint32_t fsr;
    uint32_t global_fault;
    unsigned level;
    IOMMUAccessFlags perm;
    bool ptw;
    bool blocked;
} WalkResult;

static uint64_t reg64(const uint32_t *r, unsigned off)
{
    return r[off / 4] | ((uint64_t)r[off / 4 + 1] << 32);
}

static void set_reg64(uint32_t *r, unsigned off, uint64_t value)
{
    r[off / 4] = value;
    r[off / 4 + 1] = value >> 32;
}

static void smmuv2_update_irqs(ArmSMMUv2State *s)
{
    uint32_t fault = s->gr0[GFSR / 4];
    uint32_t cr0 = s->gr0[CR0 / 4];
    unsigned i;

    qemu_set_irq(s->global_irq[0],
                 (fault & ~GFSR_CAF) && (cr0 & CR0_GFIE));
    qemu_set_irq(s->global_irq[1],
                 (fault & GFSR_CAF) && (cr0 & CR0_GCFGFIE));
    for (i = 0; i < s->num_context_banks; i++) {
        qemu_set_irq(s->context_irq[i],
                     (s->cb[i][FSR / 4] & FSR_MASK) &&
                     (s->cb[i][SCTLR / 4] & SCTLR_CFIE));
    }
}

static void smmuv2_global_fault(ArmSMMUv2State *s, uint32_t fault,
                                uint16_t sid, uint64_t addr, bool write, bool user)
{
    if (s->gr0[GFSR / 4]) {
        s->gr0[GFSR / 4] |= FAULT_MULTI;
    } else {
        s->gr0[GFSR / 4] = fault;
        set_reg64(s->gr0, GFAR, addr);
        /* Global syndrome layout differs from context FSYNR0. */
        s->gr0[GFSYNR0 / 4] = (write ? BIT32(1) : 0) |
                              (user ? 0 : BIT32(2));
        s->gr0[GFSYNR1 / 4] = sid;
        s->gr0[GFSYNR2 / 4] = 0;
    }
    smmuv2_update_irqs(s);
}

static void smmuv2_context_fault(ArmSMMUv2State *s, unsigned cb,
                                 uint16_t sid, uint64_t addr,
                                 bool write, bool user, WalkResult *r)
{
    uint32_t *regs = s->cb[cb];

    if (regs[FSR / 4] & FSR_MASK) {
        regs[FSR / 4] |= FAULT_MULTI;
    } else {
        regs[FSR / 4] = r->fsr;
        set_reg64(regs, FAR, addr);
        regs[FSYNR0 / 4] = r->level | (write ? BIT32(4) : 0) |
                           (user ? 0 : BIT32(5)) |
                           (r->ptw ? BIT32(10) : 0);
        regs[FSYNR1 / 4] = 0;
        s->gr1[(0x400 + cb * 4) / 4] = sid;
    }
    smmuv2_update_irqs(s);
}

/* Avoid recursively walking an MMIO or another IOMMU as a page table. */
static bool smmuv2_read_pte(ArmSMMUv2State *s, uint64_t pa,
                            bool big_endian, uint64_t *value)
{
    MemoryRegionSection section = memory_region_find(s->memory, pa, 8);
    MemTxResult result = MEMTX_DECODE_ERROR;
    bool ram = section.mr && memory_region_is_ram(section.mr) &&
               int128_get64(section.size) == 8;

    if (ram) {
        *value = big_endian ?
            address_space_ldq_be(&s->memory_as, pa,
                                 MEMTXATTRS_UNSPECIFIED, &result) :
            address_space_ldq_le(&s->memory_as, pa,
                                 MEMTXATTRS_UNSPECIFIED, &result);
    }
    if (section.mr) {
        memory_region_unref(section.mr);
    }
    return ram && result == MEMTX_OK;
}

static WalkResult smmuv2_walk(ArmSMMUv2State *s, unsigned cb,
                               uint64_t original, IOMMUAccessFlags access,
                               bool user, bool ats)
{
    static const unsigned pa_bits[] = {32, 36, 40, 42, 44, 48, 48, 48};
    static const unsigned sep_bits[] = {31, 35, 39, 41, 43, 47};
    uint32_t *r = s->cb[cb];
    uint32_t tcr = r[TCR / 4], tcr2 = r[TCR2 / 4];
    unsigned type = (s->gr1[cb] >> 16) & 3;
    unsigned oas = pa_bits[tcr2 & 7];
    uint64_t addr = original, base, pte, mask;
    unsigned which, bits = 0, start, level, shift, top_bits, tsz, tg;
    unsigned granule_shift, stride, granule;
    uint64_t table_mask;
    bool table_ro = false, table_no_user = false;
    WalkResult out = {.mask = 0xfff};

    if (type != 1 || !(s->gr1[(0x800 + cb * 4) / 4] & 1)) {
        out.global_fault = GFSR_ICF;
        goto fault;
    }
    if (!ats && (r[FSR / 4] & FSR_MASK) &&
        !(r[SCTLR / 4] & SCTLR_HUPCF)) {
        out.blocked = true;
        goto fault;
    }
    if (!(r[SCTLR / 4] & SCTLR_M)) {
        out.pa = addr & ~out.mask;
        out.perm = IOMMU_RW;
        out.par = (addr & PA_MASK) | BIT32(11);
        return out;
    }
    if (!ats && ((tcr2 >> 15) & 7) < ARRAY_SIZE(sep_bits)) {
        addr = sextract64(addr, 0, sep_bits[(tcr2 >> 15) & 7] + 1);
    }
    /* Select TTBR by the valid low/high canonical address region. */
    for (which = 0; which < 2; which++) {
        uint64_t candidate = addr;

        tsz = (tcr >> (which ? 16 : 0)) & 0x3f;
        if (tsz < 16 || tsz > 39) {
            continue;
        }
        bits = 64 - tsz;
        if (ats && (tcr2 & BIT32(which ? 6 : 5))) {
            candidate = which ? (addr | UINT64_C(0xff00000000000000)) :
                                 (addr & UINT64_C(0x00ffffffffffffff));
        }
        if ((!which && !(candidate >> bits)) ||
            (which && (candidate >> bits) == (UINT64_MAX >> bits))) {
            addr = candidate;
            break;
        }
    }
    if (which == 2) {
        out.fsr = FSR_ASF;
        goto fault;
    }
    if (tcr & BIT32(which ? 23 : 7)) {
        out.fsr = FSR_TF;
        goto fault;
    }
    tg = (tcr >> (which ? 30 : 14)) & 3;
    /* TG0: 4K, 64K, 16K, reserved; TG1: reserved, 16K, 4K, 64K. */
    if ((!which && tg == 3) || (which && tg == 0)) {
        out.global_fault = GFSR_ICF;
        goto fault;
    }
    if (tg == (which ? 2 : 0)) {
        granule_shift = 12;
        granule = GRANULE_4K;
    } else if (tg == (which ? 1 : 2)) {
        granule_shift = 14;
        granule = GRANULE_16K;
    } else {
        granule_shift = 16;
        granule = GRANULE_64K;
    }
    if (!(s->aarch64_granules & granule)) {
        /* Deliberate fail-closed model policy, not architectural TG fallback. */
        out.global_fault = GFSR_ICF;
        goto fault;
    }
    stride = granule_shift - 3;
    start = 4 - DIV_ROUND_UP(bits - granule_shift, stride);
    top_bits = bits - (granule_shift + stride * (3 - start));
    table_mask = PA_MASK & ~((UINT64_C(1) << granule_shift) - 1);
    /* An initial table may be smaller than one translation granule. */
    base = reg64(r, which ? TTBR1 : TTBR0) & UINT64_C(0x0000ffffffffffff);
    base &= ~((UINT64_C(1) << (top_bits + 3)) - 1);
    if (base >> oas) {
        out.fsr = FSR_ASF;
        goto fault;
    }
    for (level = start; level < 4; level++) {
        uint64_t index, pa;
        unsigned ap, attr_index, shareability;
        uint64_t mair;

        out.level = level;
        shift = granule_shift + stride * (3 - level);
        index = (addr >> shift) & (level == start ?
                ((UINT64_C(1) << top_bits) - 1) :
                ((UINT64_C(1) << stride) - 1));
        pa = base + index * 8;
        if (pa >> oas) {
            out.fsr = FSR_ASF;
            out.ptw = true;
            goto fault;
        }
        if (!smmuv2_read_pte(s, pa, r[SCTLR / 4] & SCTLR_E, &pte)) {
            out.fsr = FSR_EF;
            out.ptw = true;
            goto fault;
        }
        if (!(pte & 1) || (!(pte & 2) &&
            (level == 0 || level == 3 ||
             (level == 1 && granule_shift != 12)))) {
            out.fsr = FSR_TF;
            goto fault;
        }
        if (level < 3 && (pte & 2)) {
            table_no_user |= !!(pte & (UINT64_C(1) << 61));
            table_ro |= !!(pte & (UINT64_C(1) << 62));
            base = pte & table_mask;
            if (base >> oas) {
                out.fsr = FSR_ASF;
                out.ptw = true;
                goto fault;
            }
            continue;
        }
        mask = (UINT64_C(1) << shift) - 1;
        pa = (pte & PA_MASK) & ~mask;
        if ((pte & PA_MASK) >> oas) {
            out.fsr = FSR_ASF;
            goto fault;
        }
        if (!(pte & BIT32(10))) {
            out.fsr = FSR_AFF;
            goto fault;
        }
        ap = (pte >> 6) & 3;
        if (user && (table_no_user || !(ap & 1))) {
            out.fsr = FSR_PF;
            goto fault;
        }
        out.perm = (table_ro || (ap & 2)) ? IOMMU_RO : IOMMU_RW;
        if ((access & IOMMU_WO) && !(out.perm & IOMMU_WO)) {
            out.fsr = FSR_PF;
            goto fault;
        }
        out.pa = pa;
        out.mask = mask;
        attr_index = (pte >> 2) & 7;
        shareability = (pte >> 8) & 3;
        mair = reg64(r, MAIR0);
        out.par = ((pa | (addr & mask)) & PA_MASK) | BIT32(11) |
                  ((uint64_t)shareability << 7) |
                  (((mair >> (attr_index * 8)) & 0xff) << 56);
        return out;
    }
    out.fsr = FSR_TF;
fault:
    out.perm = IOMMU_NONE;
    out.par = 1 | BIT32(9) | out.fsr | ((uint64_t)out.level << 32);
    if (out.global_fault) {
        out.par |= BIT32(29);
    }
    return out;
}

static void smmuv2_invalidate(ArmSMMUv2State *s)
{
    unsigned i;

    /* No internal TLB. External caches must still observe invalidation. */
    for (i = 0; i < s->streams->len; i++) {
        SMMUv2Stream *stream = g_ptr_array_index(s->streams, i);
        IOMMUNotifier *n;

        IOMMU_NOTIFIER_FOREACH(n, &stream->iommu) {
            memory_region_unmap_iommu_notifier_range(n);
        }
    }
}

/*
 * Caller holds s->lock. Debug transactions bypass the companion halt filters.
 * Legacy ANOC1/2 ECATS additionally requires a real translation; the SMMUv500
 * reference profile preserves architectural bypass semantics.
 */
static IOMMUTLBEntry smmuv2_translate_sid_locked(ArmSMMUv2State *s,
                                                uint16_t sid, hwaddr addr,
                                                IOMMUAccessFlags access,
                                                int index, bool debug,
                                                bool require_translation)
{
    bool user = index & 1, write = access & IOMMU_WO;
    IOMMUTLBEntry entry = {
        .target_as = &s->memory_as, .iova = addr & ~UINT64_C(0xfff),
        .addr_mask = 0xfff, .perm = IOMMU_NONE,
    };
    uint32_t mapping, cr0;
    unsigned i, matches = 0, selected = 0, cb, type, priv;
    WalkResult result;

    cr0 = s->gr0[CR0 / 4];
    if ((index & 2) || sid >= 0x8000) {
        smmuv2_global_fault(s, GFSR_UUT, sid, addr, write, user);
        goto done;
    }
    if (!debug && ((s->debug_halt_groups & BIT32(sid >> 10)) ||
                   (s->stream_blocked &&
                    s->stream_blocked(s->stream_blocked_opaque, sid)))) {
        /* Functional admission barrier; no hardware stall/drain queue. */
        goto done;
    }
    if (cr0 & CR0_CLIENTPD) {
        goto bypass;
    }
    for (i = 0; i < s->num_smrs; i++) {
        uint32_t smr = s->gr0[SMR_BASE / 4 + i];
        uint16_t mask = (smr >> 16) & 0x7fff;

        if ((smr & BIT32(31)) &&
            ((sid & ~mask) == ((smr & 0x7fff) & ~mask))) {
            selected = i;
            matches++;
        }
    }
    if (!matches) {
        if (!(cr0 & CR0_USFCFG)) {
            goto bypass;
        }
        smmuv2_global_fault(s, GFSR_USF, sid, addr, write, user);
        goto done;
    }
    if (matches > 1) {
        if (!(cr0 & CR0_SMCFCFG)) {
            goto bypass;
        }
        smmuv2_global_fault(s, GFSR_SMCF, sid, addr, write, user);
        goto done;
    }
    mapping = s->gr0[S2CR_BASE / 4 + selected];
    type = (mapping >> 16) & 3;
    if (type == 1) {
        goto bypass;
    }
    if (type != 0) {
        smmuv2_global_fault(s, GFSR_ICF, sid, addr, write, user);
        goto done;
    }
    cb = mapping & 0xff;
    if (cb >= s->num_context_banks) {
        smmuv2_global_fault(s, GFSR_UCBF, sid, addr, write, user);
        goto done;
    }
    priv = (mapping >> 24) & 3;
    if (priv == 1) {
        smmuv2_global_fault(s, GFSR_UUT, sid, addr, write, user);
        goto done;
    }
    if (priv == 2) {
        user = true;
    } else if (priv == 3) {
        user = false;
    }
    if (require_translation && !(s->cb[cb][SCTLR / 4] & SCTLR_M)) {
        goto done;
    }
    result = smmuv2_walk(s, cb, addr, access, user, false);
    if (result.global_fault) {
        smmuv2_global_fault(s, result.global_fault, sid, addr, write, user);
    } else if (result.fsr && !result.blocked) {
        smmuv2_context_fault(s, cb, sid, addr, write, user, &result);
    } else if (result.perm) {
        entry.iova = addr & ~result.mask;
        entry.translated_addr = result.pa;
        entry.addr_mask = result.mask;
        entry.perm = result.perm;
    }
    goto done;
bypass:
    if (!require_translation) {
        entry.translated_addr = entry.iova;
        entry.perm = IOMMU_RW;
    }
done:
    return entry;
}

static IOMMUTLBEntry smmuv2_translate(IOMMUMemoryRegion *mr, hwaddr addr,
                                      IOMMUAccessFlags access, int index)
{
    SMMUv2Stream *stream = container_of(mr, SMMUv2Stream, iommu);
    ArmSMMUv2State *s = stream->s;
    IOMMUTLBEntry entry;

    qemu_mutex_lock(&s->lock);
    entry = smmuv2_translate_sid_locked(s, stream->sid, addr, access, index,
                                       false, false);
    qemu_mutex_unlock(&s->lock);
    return entry;
}

IOMMUTLBEntry arm_smmuv2_debug_translate_entry(DeviceState *dev, uint16_t sid,
                                              hwaddr addr, bool write)
{
    ArmSMMUv2State *s = ARM_SMMUV2(dev);
    IOMMUTLBEntry entry;

    qemu_mutex_lock(&s->lock);
    entry = smmuv2_translate_sid_locked(s, sid, addr,
                                       write ? IOMMU_WO : IOMMU_RO, 0,
                                       true, false);
    qemu_mutex_unlock(&s->lock);
    return entry;
}

bool arm_smmuv2_set_stream_blocker(DeviceState *dev,
                                  ArmSMMUv2StreamBlocked blocked,
                                  void *opaque, Error **errp)
{
    ArmSMMUv2State *s = ARM_SMMUV2(dev);

    qemu_mutex_lock(&s->lock);
    if (s->stream_blocked && s->stream_blocked_opaque != opaque) {
        error_setg(errp, "arm-smmuv2: stream blocker already attached");
        qemu_mutex_unlock(&s->lock);
        return false;
    }
    s->stream_blocked = blocked;
    s->stream_blocked_opaque = blocked ? opaque : NULL;
    qemu_mutex_unlock(&s->lock);
    return true;
}

void arm_smmuv2_stream_permissions_changed(DeviceState *dev)
{
    /* A TBU halt must revoke entries already cached by emulated clients. */
    smmuv2_invalidate(ARM_SMMUV2(dev));
}

void arm_smmuv2_set_debug_halt_groups(DeviceState *dev, uint32_t groups)
{
    ArmSMMUv2State *s = ARM_SMMUV2(dev);

    qemu_mutex_lock(&s->lock);
    s->debug_halt_groups = groups;
    qemu_mutex_unlock(&s->lock);
    smmuv2_invalidate(s);
}

bool arm_smmuv2_debug_translate(DeviceState *dev, uint16_t sid, uint64_t va,
                                IOMMUAccessFlags access, MemTxAttrs attrs,
                                uint64_t *pa)
{
    ArmSMMUv2State *s = ARM_SMMUV2(dev);
    IOMMUTLBEntry entry;
    int index = (attrs.user ? 1 : 0) | (attrs.secure ? 2 : 0);

    if (access != IOMMU_RO && access != IOMMU_WO) {
        return false;
    }
    qemu_mutex_lock(&s->lock);
    entry = smmuv2_translate_sid_locked(s, sid, va, access, index, true, true);
    qemu_mutex_unlock(&s->lock);
    if (!(entry.perm & access)) {
        return false;
    }
    *pa = entry.translated_addr | (va & entry.addr_mask);
    return true;
}

static int smmuv2_attrs_to_index(IOMMUMemoryRegion *mr, MemTxAttrs attrs)
{
    return (attrs.user ? 1 : 0) | (attrs.secure ? 2 : 0);
}

static int smmuv2_num_indexes(IOMMUMemoryRegion *mr)
{
    return 4;
}

static uint64_t smmuv2_min_page_size(IOMMUMemoryRegion *mr)
{
    return 4096;
}

static int smmuv2_notify_flags(IOMMUMemoryRegion *mr,
                               IOMMUNotifierFlag old_flags,
                               IOMMUNotifierFlag new_flags, Error **errp)
{
    if (new_flags & (IOMMU_NOTIFIER_MAP | IOMMU_NOTIFIER_DEVIOTLB_UNMAP)) {
        error_setg(errp, "arm-smmuv2 currently supports emulated DMA and UNMAP notifiers only");
        return -EINVAL;
    }
    return 0;
}

static SMMUv2Stream *smmuv2_get_stream(ArmSMMUv2State *s, uint16_t sid)
{
    SMMUv2Stream *stream;
    unsigned i;
    g_autofree char *name = NULL;

    g_assert(DEVICE(s)->realized);
    for (i = 0; i < s->streams->len; i++) {
        stream = g_ptr_array_index(s->streams, i);
        if (stream->sid == sid) {
            return stream;
        }
    }
    stream = g_new0(SMMUv2Stream, 1);
    stream->s = s;
    stream->sid = sid;
    name = g_strdup_printf("smmuv2-sid-%04x", sid);
    memory_region_init_iommu(&stream->iommu, sizeof(stream->iommu),
                             TYPE_SMMUV2_IOMMU, OBJECT(s), name, UINT64_MAX);
    address_space_init(&stream->as, MEMORY_REGION(&stream->iommu), name);
    g_ptr_array_add(s->streams, stream);
    return stream;
}

AddressSpace *arm_smmuv2_get_dma_as(DeviceState *dev, uint16_t sid)
{
    return &smmuv2_get_stream(ARM_SMMUV2(dev), sid)->as;
}

MemoryRegion *arm_smmuv2_get_dma_mr(DeviceState *dev, uint16_t sid)
{
    return MEMORY_REGION(&smmuv2_get_stream(ARM_SMMUV2(dev), sid)->iommu);
}

static uint32_t smmuv2_read32(ArmSMMUv2State *s, hwaddr offset)
{
    unsigned cb, off;

    if (offset >= s->mmio_size / 2) {
        cb = (offset - s->mmio_size / 2) / 4096;
        off = offset & 4095;
        if (cb >= s->num_context_banks) {
            return 0;
        }
        if (off == FSR) {
            return s->cb[cb][FSR / 4] |
                ((s->gr1[(0x800 + cb * 4) / 4] & 1) ? BIT32(10) : 0);
        }
        if (off == TLBSTATUS || off == ATSR) {
            return 0; /* operations complete synchronously */
        }
        return s->cb[cb][off / 4];
    }
    if (offset >= 0x1000 && offset < 0x2000) {
        return s->gr1[(offset - 0x1000) / 4];
    }
    if (offset >= 0x1000) {
        return 0;
    }
    switch (offset) {
    case ID0:
        return BIT32(30) | BIT32(27) | BIT32(25) | BIT32(16) |
               BIT32(14) | (15 << 9) | s->num_smrs;
    case ID1:
        return ((ctz32(s->mmio_size) - 14) << 28) | s->num_context_banks;
    case ID2:
        return (s->aarch64_granules << 12) | (15 << 8) | (5 << 4) | 5;
    case 0x2c ... 0x3c:
    case TLBGSTATUS:
        return 0;
    default:
        return s->gr0[offset / 4];
    }
}

static uint64_t smmuv2_mmio_read(void *opaque, hwaddr offset, unsigned size)
{
    ArmSMMUv2State *s = opaque;
    uint64_t value;

    qemu_mutex_lock(&s->lock);
    value = smmuv2_read32(s, offset);
    if (size == 8) {
        value |= (uint64_t)smmuv2_read32(s, offset + 4) << 32;
    }
    qemu_mutex_unlock(&s->lock);
    return value;
}

static bool smmuv2_write32(ArmSMMUv2State *s, hwaddr offset, uint32_t value)
{
    unsigned cb, off, idx;

    if (offset >= s->mmio_size / 2) {
        cb = (offset - s->mmio_size / 2) / 4096;
        off = offset & 4095;
        if (cb >= s->num_context_banks) {
            return false;
        }
        switch (off) {
        case SCTLR:
            s->cb[cb][off / 4] = value & 0x11f7;
            smmuv2_update_irqs(s);
            return true;
        case ACTLR:
        case CONTEXTIDR:
        case MAIR0:
        case MAIR1:
        case TCR:
        case TTBR0:
        case TTBR0 + 4:
        case TTBR1:
        case TTBR1 + 4:
        case PAR:
        case PAR + 4:
        case FAR:
        case FAR + 4:
        case FSYNR0:
        case FSYNR1:
            s->cb[cb][off / 4] = value;
            return off <= MAIR1;
        case TCR2:
            s->cb[cb][off / 4] = value & ~UINT32_C(0xf00);
            return true;
        case FSR:
            s->cb[cb][FSR / 4] &= ~(value & FSR_MASK);
            smmuv2_update_irqs(s);
            return false;
        case FSRRESTORE:
            s->cb[cb][FSR / 4] = value & FSR_MASK;
            smmuv2_update_irqs(s);
            return false;
        case RESUME:
            return false; /* no stalled transactions */
        case 0x600 ... 0x62c:
        case TLBSYNC:
            return true;
        case TLBSTATUS:
        case ATSR:
            return false;
        default:
            qemu_log_mask(LOG_UNIMP,
                          "arm-smmuv2: unimplemented CB%u write +0x%x\n",
                          cb, off);
            return false;
        }
    }
    if (offset >= 0x1000 && offset < 0x2000) {
        off = offset - 0x1000;
        if (off < s->num_context_banks * 4) {
            s->gr1[off / 4] = value & 0x3ffff;
            return true;
        }
        if (off >= 0x800 && off < 0x800 + s->num_context_banks * 4) {
            s->gr1[off / 4] = value & 1;
            return true;
        }
        return false;
    }
    if (offset >= 0x1000) {
        return false;
    }
    if (offset >= SMR_BASE && offset < SMR_BASE + s->num_smrs * 4) {
        s->gr0[offset / 4] = value & UINT32_C(0xffff7fff);
        return true;
    }
    if (offset >= S2CR_BASE && offset < S2CR_BASE + s->num_smrs * 4) {
        s->gr0[offset / 4] = value & UINT32_C(0x030300ff);
        return true;
    }
    switch (offset) {
    case CR0:
        s->gr0[CR0 / 4] = (value & UINT32_C(0x003ffff7)) | CR0_STALLD;
        smmuv2_update_irqs(s);
        return true;
    case ACR:
        s->gr0[ACR / 4] = value;
        return false;
    case GFSR:
        s->gr0[GFSR / 4] &= ~(value & (FAULT_MULTI | 0x1ff));
        smmuv2_update_irqs(s);
        return false;
    case GFSRRESTORE:
        s->gr0[GFSR / 4] = value & (FAULT_MULTI | 0x1ff);
        smmuv2_update_irqs(s);
        return false;
    case GFAR:
    case GFAR + 4:
    case GFSYNR0:
    case GFSYNR1:
    case GFSYNR2:
        s->gr0[offset / 4] = value;
        return false;
    case TLBIVMID:
    case TLBIALLNSNH:
    case TLBIALLH:
    case TLBGSYNC:
        return true;
    default:
        idx = offset / 4;
        if (idx < WORDS && (offset < ID0 || offset > 0x3c)) {
            qemu_log_mask(LOG_UNIMP,
                          "arm-smmuv2: unimplemented global write +0x%"
                          HWADDR_PRIx "\n", offset);
        }
        return false;
    }
}

static void smmuv2_mmio_write(void *opaque, hwaddr offset,
                               uint64_t value, unsigned size)
{
    ArmSMMUv2State *s = opaque;
    unsigned cb, off;
    bool invalidate = false;

    qemu_mutex_lock(&s->lock);
    if (size == 8 && offset >= s->mmio_size / 2) {
        cb = (offset - s->mmio_size / 2) / 4096;
        off = offset & 4095;
        if (cb < s->num_context_banks &&
            (off == ATS1PR || off == ATS1PW ||
             off == ATS1UR || off == ATS1UW)) {
            WalkResult result = smmuv2_walk(s, cb, value,
                 (off == ATS1PW || off == ATS1UW) ? IOMMU_WO : IOMMU_RO,
                 off == ATS1UR || off == ATS1UW, true);
            /* Stage-1 ATS failures update PAR, not the client FSR/IRQ. */
            set_reg64(s->cb[cb], PAR, result.par);
            qemu_mutex_unlock(&s->lock);
            return;
        }
    }
    invalidate = smmuv2_write32(s, offset, value);
    if (size == 8) {
        invalidate |= smmuv2_write32(s, offset + 4, value >> 32);
    }
    qemu_mutex_unlock(&s->lock);
    if (invalidate) {
        smmuv2_invalidate(s);
    }
}

static const MemoryRegionOps smmuv2_mmio_ops = {
    .read = smmuv2_mmio_read,
    .write = smmuv2_mmio_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 8,
    .impl.min_access_size = 4,
    .impl.max_access_size = 8,
};

static void smmuv2_reset_hold(Object *obj, ResetType type)
{
    ArmSMMUv2State *s = ARM_SMMUV2(obj);
    unsigned i;

    qemu_mutex_lock(&s->lock);
    memset(s->gr0, 0, sizeof(s->gr0));
    memset(s->gr1, 0, sizeof(s->gr1));
    memset(s->cb, 0, sizeof(s->cb));
    s->gr0[CR0 / 4] = CR0_CLIENTPD | CR0_STALLD;
    for (i = 0; i < s->num_smrs; i++) {
        s->gr0[S2CR_BASE / 4 + i] = 2 << 16;
    }
    smmuv2_update_irqs(s);
    qemu_mutex_unlock(&s->lock);
    smmuv2_invalidate(s);
}

static int smmuv2_post_load(void *opaque, int version)
{
    ArmSMMUv2State *s = opaque;

    /* Version 2 could only expose 4K; do not change its guest-visible IDR2. */
    if (version < 3 && s->aarch64_granules != GRANULE_4K) {
        error_report("arm-smmuv2: version 2 requires aarch64-granules=1");
        return -EINVAL;
    }
    s->gr0[CR0 / 4] |= CR0_STALLD;
    smmuv2_update_irqs(s);
    smmuv2_invalidate(s);
    return 0;
}

static const VMStateDescription vmstate_smmuv2 = {
    .name = TYPE_ARM_SMMUV2,
    /* Version 1 did not identify the geometry; it cannot be checked safely. */
    .version_id = 3,
    .minimum_version_id = 2,
    .post_load = smmuv2_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(mmio_size, ArmSMMUv2State),
        VMSTATE_UINT32_EQUAL(num_context_banks, ArmSMMUv2State),
        VMSTATE_UINT32_EQUAL(num_smrs, ArmSMMUv2State),
        VMSTATE_UINT32_EQUAL_V(aarch64_granules, ArmSMMUv2State, 3),
        VMSTATE_UINT32_ARRAY(gr0, ArmSMMUv2State, WORDS),
        VMSTATE_UINT32_ARRAY(gr1, ArmSMMUv2State, WORDS),
        VMSTATE_UINT32_2DARRAY(cb, ArmSMMUv2State, MAX_CB, WORDS),
        VMSTATE_END_OF_LIST()
    },
};

static void smmuv2_realize(DeviceState *dev, Error **errp)
{
    ArmSMMUv2State *s = ARM_SMMUV2(dev);
    SysBusDevice *sbd = SYS_BUS_DEVICE(dev);
    unsigned i;

    if (!is_power_of_2(s->mmio_size) || s->mmio_size < 0x10000 ||
        s->mmio_size > 0x200000 || !s->num_context_banks ||
        s->num_context_banks > MAX_CB ||
        s->num_context_banks > s->mmio_size / 8192 ||
        !s->num_smrs || s->num_smrs > MAX_SMR) {
        error_setg(errp, "invalid arm-smmuv2 aperture/context/SMR geometry");
        return;
    }
    if (!(s->aarch64_granules & GRANULE_4K) ||
        (s->aarch64_granules & ~GRANULE_MASK)) {
        error_setg(errp, "arm-smmuv2 aarch64-granules must include 4K (bit 0) "
                   "and may include 16K (bit 1) and 64K (bit 2)");
        return;
    }
    if (!s->memory) {
        s->memory = get_system_memory();
    }
    address_space_init(&s->memory_as, s->memory, "smmuv2-physical-memory");
    memory_region_init_io(&s->mmio, OBJECT(s), &smmuv2_mmio_ops,
                           s, "arm-smmuv2-registers", s->mmio_size);
    sysbus_init_mmio(sbd, &s->mmio);
    for (i = 0; i < 2; i++) {
        sysbus_init_irq(sbd, &s->global_irq[i]);
    }
    for (i = 0; i < s->num_context_banks; i++) {
        sysbus_init_irq(sbd, &s->context_irq[i]);
    }
}

static void smmuv2_init(Object *obj)
{
    ArmSMMUv2State *s = ARM_SMMUV2(obj);

    qemu_mutex_init(&s->lock);
    s->streams = g_ptr_array_new();
}

static void smmuv2_finalize(Object *obj)
{
    ArmSMMUv2State *s = ARM_SMMUV2(obj);
    unsigned i;

    for (i = 0; i < s->streams->len; i++) {
        SMMUv2Stream *stream = g_ptr_array_index(s->streams, i);

        address_space_destroy(&stream->as);
        object_unparent(OBJECT(&stream->iommu));
        g_free(stream);
    }
    g_ptr_array_free(s->streams, true);
    if (s->memory_as.root) {
        address_space_destroy(&s->memory_as);
    }
    qemu_mutex_destroy(&s->lock);
}

static const Property smmuv2_properties[] = {
    DEFINE_PROP_UINT32("mmio-size", ArmSMMUv2State, mmio_size, 0x10000),
    DEFINE_PROP_UINT32("num-context-banks", ArmSMMUv2State, num_context_banks, 8),
    DEFINE_PROP_UINT32("num-smrs", ArmSMMUv2State, num_smrs, 128),
    DEFINE_PROP_UINT32("aarch64-granules", ArmSMMUv2State, aarch64_granules,
                       GRANULE_4K),
    DEFINE_PROP_LINK("memory", ArmSMMUv2State, memory,
                     TYPE_MEMORY_REGION, MemoryRegion *),
};

static void smmuv2_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Arm SMMUv2 AArch64 stage-1 functional subset";
    dc->realize = smmuv2_realize;
    dc->vmsd = &vmstate_smmuv2;
    dc->user_creatable = false;
    device_class_set_props(dc, smmuv2_properties);
    rc->phases.hold = smmuv2_reset_hold;
}

static void smmuv2_iommu_class_init(ObjectClass *klass, const void *data)
{
    IOMMUMemoryRegionClass *imrc = IOMMU_MEMORY_REGION_CLASS(klass);

    imrc->translate = smmuv2_translate;
    imrc->attrs_to_index = smmuv2_attrs_to_index;
    imrc->num_indexes = smmuv2_num_indexes;
    imrc->get_min_page_size = smmuv2_min_page_size;
    imrc->notify_flag_changed = smmuv2_notify_flags;
}

static const TypeInfo smmuv2_types[] = {
    {
        .name = TYPE_ARM_SMMUV2,
        .parent = TYPE_SYS_BUS_DEVICE,
        .instance_size = sizeof(ArmSMMUv2State),
        .instance_init = smmuv2_init,
        .instance_finalize = smmuv2_finalize,
        .class_init = smmuv2_class_init,
    },
    {
        .name = TYPE_SMMUV2_IOMMU,
        .parent = TYPE_IOMMU_MEMORY_REGION,
        .class_init = smmuv2_iommu_class_init,
    },
};
DEFINE_TYPES(smmuv2_types)
