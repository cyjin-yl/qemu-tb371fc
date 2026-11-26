/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Shared SM8250 QCE/BAM cold-control reference, disabled by default.
 *
 * The two original DT clients share one CE and one BAM. Their resources
 * overlap; a single strict register dispatcher covers the union without
 * backing RAM or mutually shadowing devices. The pinned vendor source uses
 * the SPS 4K register map, EE0 pipe attributes and remote BAM management.
 *
 * Only read-only identities and the idle control state of pipes 4..7 are
 * in scope. A pipe enable configures a ring; it is not a DMA submission.
 * Every doorbell, crypto GO, key, payload and unsupported-register access
 * rejects without changing device state, guest memory or completion IRQs.
 * No descriptor fetch, cryptography, secure firmware or readiness engine
 * exists. No clock behavior is claimed: the selected DT says no-clock-support.
 *
 * The generated profile is permanently a virtual software reference and is
 * disabled by default. Review never makes it a physical capture or evidence
 * of a silicon version. Any physical profile requires a separate design and
 * identity; this virtual profile must never be relabeled as one.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-qce-bam.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"

#include "qcom-sm8250-qce-bam-profile.inc"
#include "qcom-sm8250-qce-bam-state.h"

#define QCB_ABI_ID UINT32_C(0x51434231)
#define QCB_CRYPTO_BASE UINT64_C(0x01de0000)
#define QCB_CRYPTO_SIZE UINT32_C(0x20000)
#define QCB_BAM_SIZE UINT32_C(0x24000)
#define QCB_CRYPTO_IN_UNION UINT32_C(0x1c000)
#define QCB_QCE_VERSION (QCB_CRYPTO_IN_UNION + UINT32_C(0x1a000))
#define QCB_QCE_ENGINES (QCB_CRYPTO_IN_UNION + UINT32_C(0x1a108))
#define QCB_BAM_CONFIG UINT32_C(0x7c)
#define QCB_BAM_REVISION UINT32_C(0x1000)
#define QCB_BAM_NUM_PIPES UINT32_C(0x1008)
#define QCB_EE_IRQ_SOURCES UINT32_C(0x3000)
#define QCB_EE_IRQ_MASK UINT32_C(0x3004)
#define QCB_EE_IRQ_UNMASKED UINT32_C(0x3008)
#define QCB_EE_ATTRIBUTES UINT32_C(0x300c)
#define QCB_GUARD_BYTES 116
#define QCB_PIPE_BYTES 32
#define QCB_PAYLOAD_BYTES (QCB_GUARD_BYTES + 4 + QCB_PIPE_COUNT * QCB_PIPE_BYTES)

typedef struct QceBamGuard {
    QcomSm8250QceBamState *owner; /* Local pointer, never serialized. */
    uint32_t abi_identity;
    uint32_t profile;
    uint8_t profile_digest[32];
    uint64_t crypto_base;
    uint32_t crypto_bytes;
    uint64_t bam_base;
    uint32_t bam_bytes;
    uint64_t union_base;
    uint32_t union_bytes;
    uint32_t first_pipe;
    uint32_t pipe_count;
    uint32_t ee;
    uint32_t qce_version;
    uint32_t qce_engines;
    uint32_t bam_config;
    uint32_t bam_revision;
    uint32_t bam_num_pipes;
    uint32_t bam_ee_attr;
    uint32_t bam_ee_initial_mask;
} QceBamGuard;

struct QcomSm8250QceBamState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    qemu_irq irq;
    bool enabled_reference;
    QceBamGuard guard;
    uint32_t ee_irq_mask;
    QceBamPipe pipes[QCB_PIPE_COUNT];
};

static void qcb_make_guard(QcomSm8250QceBamState *s, QceBamGuard *g)
{
    static const uint8_t digest[32] = QCB_PROFILE_DIGEST;

    memset(g, 0, sizeof(*g));
    g->owner = s;
    g->abi_identity = QCB_ABI_ID;
    g->profile = s->enabled_reference;
    memcpy(g->profile_digest, digest, sizeof(digest));
    g->crypto_base = QCB_CRYPTO_BASE;
    g->crypto_bytes = QCB_CRYPTO_SIZE;
    g->bam_base = QCOM_SM8250_QCE_BAM_BASE;
    g->bam_bytes = QCB_BAM_SIZE;
    g->union_base = QCOM_SM8250_QCE_BAM_BASE;
    g->union_bytes = QCOM_SM8250_QCE_BAM_SIZE;
    g->first_pipe = QCB_FIRST_PIPE;
    g->pipe_count = QCB_PIPE_COUNT;
    g->ee = 0;
    if (s->enabled_reference) {
        g->qce_version = QCB_PROFILE_QCE_VERSION;
        g->qce_engines = QCB_PROFILE_QCE_ENGINES;
        g->bam_config = QCB_PROFILE_BAM_CONFIG;
        g->bam_revision = QCB_PROFILE_BAM_REVISION;
        g->bam_num_pipes = QCB_PROFILE_BAM_NUM_PIPES;
        g->bam_ee_attr = QCB_PROFILE_BAM_EE_ATTR;
        g->bam_ee_initial_mask = QCB_PROFILE_BAM_EE_MASK;
    }
}

static bool qcb_guard_valid(const QceBamGuard *g)
{
    QceBamGuard expected;

    qcb_make_guard(g->owner, &expected);
    return g->abi_identity == expected.abi_identity &&
           g->profile == expected.profile &&
           memcmp(g->profile_digest, expected.profile_digest, 32) == 0 &&
           g->crypto_base == expected.crypto_base &&
           g->crypto_bytes == expected.crypto_bytes &&
           g->bam_base == expected.bam_base &&
           g->bam_bytes == expected.bam_bytes &&
           g->union_base == expected.union_base &&
           g->union_bytes == expected.union_bytes &&
           g->first_pipe == expected.first_pipe &&
           g->pipe_count == expected.pipe_count && g->ee == expected.ee &&
           g->qce_version == expected.qce_version &&
           g->qce_engines == expected.qce_engines &&
           g->bam_config == expected.bam_config &&
           g->bam_revision == expected.bam_revision &&
           g->bam_num_pipes == expected.bam_num_pipes &&
           g->bam_ee_attr == expected.bam_ee_attr &&
           g->bam_ee_initial_mask == expected.bam_ee_initial_mask;
}

static bool qcb_state_valid(const QcomSm8250QceBamState *s)
{
    unsigned i;

    if (!qcb_ee_mask_valid(s->ee_irq_mask, s->guard.bam_ee_initial_mask)) {
        return false;
    }
    if (!s->enabled_reference && s->ee_irq_mask != 0) {
        return false;
    }
    for (i = 0; i < QCB_PIPE_COUNT; i++) {
        const QceBamPipe *p = &s->pipes[i];

        if (!qcb_pipe_valid(p) ||
            (!s->enabled_reference &&
             (p->reset != 0 || !qcb_pipe_zero_except_reset(p)))) {
            return false;
        }
    }
    return true;
}

static uint32_t qcb_irq_sources(const QcomSm8250QceBamState *s)
{
    uint32_t result = 0;
    unsigned i;

    for (i = 0; i < QCB_PIPE_COUNT; i++) {
        if (s->pipes[i].irq_status & s->pipes[i].irq_enable) {
            result |= UINT32_C(1) << (QCB_FIRST_PIPE + i);
        }
    }
    return result;
}

static void qcb_update_irq(QcomSm8250QceBamState *s)
{
    qemu_set_irq(s->irq, s->enabled_reference &&
                 (qcb_irq_sources(s) & s->ee_irq_mask) != 0);
}

static MemTxResult qcb_reject(hwaddr addr, unsigned size, bool write,
                              const char *reason)
{
    /* Never log guest-provided key, payload or descriptor values. */
    qemu_log_mask(LOG_UNIMP | LOG_GUEST_ERROR, TYPE_QCOM_SM8250_QCE_BAM
                  ": rejected %s offset=0x%" HWADDR_PRIx
                  " size %u (%s)\n", write ? "write" : "read",
                  addr, size, reason);
    return MEMTX_ERROR;
}

static bool qcb_pipe_address(hwaddr addr, unsigned *index, uint32_t *offset)
{
    const hwaddr first = QCB_PIPE_BASE + QCB_FIRST_PIPE * QCB_PIPE_STRIDE;
    const hwaddr end = first + QCB_PIPE_COUNT * QCB_PIPE_STRIDE;

    if (addr < first || addr >= end) {
        return false;
    }
    *index = (addr - first) / QCB_PIPE_STRIDE;
    *offset = (addr - first) % QCB_PIPE_STRIDE;
    return true;
}

static MemTxResult qcb_read(void *opaque, hwaddr addr, uint64_t *value,
                            unsigned size, MemTxAttrs attrs)
{
    QcomSm8250QceBamState *s = opaque;
    unsigned index;
    uint32_t offset, v;

    *value = 0;
    if (!s->enabled_reference || size != 4 || (addr & 3)) {
        return qcb_reject(addr, size, false, "disabled-width-or-alignment");
    }
    switch (addr) {
    case QCB_QCE_VERSION: *value = s->guard.qce_version; return MEMTX_OK;
    case QCB_QCE_ENGINES: *value = s->guard.qce_engines; return MEMTX_OK;
    case QCB_BAM_CONFIG: *value = s->guard.bam_config; return MEMTX_OK;
    case QCB_BAM_REVISION: *value = s->guard.bam_revision; return MEMTX_OK;
    case QCB_BAM_NUM_PIPES: *value = s->guard.bam_num_pipes; return MEMTX_OK;
    case QCB_EE_ATTRIBUTES: *value = s->guard.bam_ee_attr; return MEMTX_OK;
    case QCB_EE_IRQ_MASK: *value = s->ee_irq_mask; return MEMTX_OK;
    case QCB_EE_IRQ_SOURCES:
        *value = qcb_irq_sources(s) & s->ee_irq_mask;
        return MEMTX_OK;
    case QCB_EE_IRQ_UNMASKED:
        *value = qcb_irq_sources(s);
        return MEMTX_OK;
    default:
        break;
    }
    if (qcb_pipe_address(addr, &index, &offset) &&
        qcb_pipe_read(&s->pipes[index], offset, &v) == QCB_ACCESS_OK) {
        *value = v;
        return MEMTX_OK;
    }
    return qcb_reject(addr, size, false, "unsupported-or-write-only-register");
}

static MemTxResult qcb_write(void *opaque, hwaddr addr, uint64_t value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomSm8250QceBamState *s = opaque;
    unsigned index;
    uint32_t offset;
    QceBamAccess result;

    if (!s->enabled_reference || size != 4 || (addr & 3) || value >> 32) {
        return qcb_reject(addr, size, true, "disabled-width-or-alignment");
    }
    if (addr == QCB_EE_IRQ_MASK) {
        if (!qcb_ee_mask_valid(value, s->guard.bam_ee_initial_mask)) {
            return qcb_reject(addr, size, true, "unmodeled-EE-mask-bits");
        }
        s->ee_irq_mask = value;
        qcb_update_irq(s);
        return MEMTX_OK;
    }
    if (qcb_pipe_address(addr, &index, &offset)) {
        result = qcb_pipe_write(&s->pipes[index], offset, value);
        if (result == QCB_ACCESS_OK) {
            qcb_update_irq(s);
            return MEMTX_OK;
        }
        return qcb_reject(addr, size, true,
                          offset == QCB_P_EVNT_REG ? "unsupported-DMA-submit" :
                          "unsupported-or-invalid-pipe-state");
    }
    return qcb_reject(addr, size, true, "read-only-or-unsupported-register");
}

static const MemoryRegionOps qcb_ops = {
    .read_with_attrs = qcb_read,
    .write_with_attrs = qcb_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Deliver invalid widths unchanged so the callback rejects atomically. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void qcb_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(obj);

    /* Explicit idle reference policy; not measured silicon reset state. */
    memset(s->pipes, 0, sizeof(s->pipes));
    s->ee_irq_mask = s->guard.bam_ee_initial_mask;
}

static void qcb_reset_hold(Object *obj, ResetType type)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(obj);

    /* Reset enter must not change another device through an IRQ line. */
    qcb_update_irq(s);
}

static int qcb_guard_post_load(void *opaque, int version_id)
{
    QceBamGuard *g = opaque;

    if (!qcb_guard_valid(g)) {
        error_report(TYPE_QCOM_SM8250_QCE_BAM
                     ": migrated identity, profile or geometry mismatch");
        return -EINVAL;
    }
    return 0;
}

static int qcb_pre_save(void *opaque)
{
    QcomSm8250QceBamState *s = opaque;

    if (!qcb_guard_valid(&s->guard) || !qcb_state_valid(s)) {
        error_report(TYPE_QCOM_SM8250_QCE_BAM ": invalid outgoing state");
        return -EINVAL;
    }
    return 0;
}

static int qcb_post_load(void *opaque, int version_id)
{
    QcomSm8250QceBamState *s = opaque;

    if (!qcb_guard_valid(&s->guard) || !qcb_state_valid(s)) {
        error_report(TYPE_QCOM_SM8250_QCE_BAM ": invalid migrated control state");
        return -EINVAL;
    }
    /* Only after every field is checked. There is no backend or DMA work. */
    qcb_update_irq(s);
    return 0;
}

static const VMStateDescription vmstate_qcb_guard = {
    .name = TYPE_QCOM_SM8250_QCE_BAM "/guard",
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = qcb_guard_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(abi_identity, QceBamGuard),
        VMSTATE_UINT32(profile, QceBamGuard),
        VMSTATE_UINT8_ARRAY(profile_digest, QceBamGuard, 32),
        VMSTATE_UINT64(crypto_base, QceBamGuard),
        VMSTATE_UINT32(crypto_bytes, QceBamGuard),
        VMSTATE_UINT64(bam_base, QceBamGuard),
        VMSTATE_UINT32(bam_bytes, QceBamGuard),
        VMSTATE_UINT64(union_base, QceBamGuard),
        VMSTATE_UINT32(union_bytes, QceBamGuard),
        VMSTATE_UINT32(first_pipe, QceBamGuard),
        VMSTATE_UINT32(pipe_count, QceBamGuard),
        VMSTATE_UINT32(ee, QceBamGuard),
        VMSTATE_UINT32(qce_version, QceBamGuard),
        VMSTATE_UINT32(qce_engines, QceBamGuard),
        VMSTATE_UINT32(bam_config, QceBamGuard),
        VMSTATE_UINT32(bam_revision, QceBamGuard),
        VMSTATE_UINT32(bam_num_pipes, QceBamGuard),
        VMSTATE_UINT32(bam_ee_attr, QceBamGuard),
        VMSTATE_UINT32(bam_ee_initial_mask, QceBamGuard),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_qcb_pipe = {
    .name = TYPE_QCOM_SM8250_QCE_BAM "/pipe",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, QceBamPipe),
        VMSTATE_UINT32(reset, QceBamPipe),
        VMSTATE_UINT32(irq_enable, QceBamPipe),
        VMSTATE_UINT32(irq_status, QceBamPipe),
        VMSTATE_UINT32(descriptor_lo, QceBamPipe),
        VMSTATE_UINT32(descriptor_hi, QceBamPipe),
        VMSTATE_UINT32(fifo_size, QceBamPipe),
        VMSTATE_UINT32(threshold, QceBamPipe),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_qcb = {
    .name = TYPE_QCOM_SM8250_QCE_BAM,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = qcb_pre_save,
    .post_load = qcb_post_load,
    .fields = (const VMStateField[]) {
        /* The 116-byte guard is validated before any mutable state loads. */
        VMSTATE_STRUCT(guard, QcomSm8250QceBamState, 1,
                       vmstate_qcb_guard, QceBamGuard),
        VMSTATE_UINT32(ee_irq_mask, QcomSm8250QceBamState),
        VMSTATE_STRUCT_ARRAY(pipes, QcomSm8250QceBamState, QCB_PIPE_COUNT, 1,
                             vmstate_qcb_pipe, QceBamPipe),
        VMSTATE_END_OF_LIST()
    },
};

static uint32_t qcb_pipe_property(const QceBamPipe *p, unsigned field)
{
    switch (field) {
    case 0: return p->control;
    case 1: return p->reset;
    case 2: return p->irq_enable;
    case 3: return p->irq_status;
    case 4: return p->descriptor_lo;
    case 5: return p->descriptor_hi;
    case 6: return p->fifo_size;
    case 7: return p->threshold;
    default: g_assert_not_reached();
    }
}

static void qcb_uint_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(obj);
    unsigned field = GPOINTER_TO_UINT(opaque);
    uint64_t value;

    if (field >= 100 && field < 100 + QCB_PIPE_COUNT * 8) {
        field -= 100;
        value = qcb_pipe_property(&s->pipes[field / 8], field % 8);
    } else {
        switch (field) {
        case 0: value = s->guard.abi_identity; break;
        case 1: value = s->guard.union_base; break;
        case 2: value = s->guard.union_bytes; break;
        case 3: value = s->guard.crypto_base; break;
        case 4: value = s->guard.crypto_bytes; break;
        case 5: value = s->guard.bam_base; break;
        case 6: value = s->guard.bam_bytes; break;
        case 7: value = s->guard.first_pipe; break;
        case 8: value = s->guard.pipe_count; break;
        case 9: value = s->guard.ee; break;
        case 10: value = s->guard.qce_version; break;
        case 11: value = s->guard.qce_engines; break;
        case 12: value = s->guard.bam_config; break;
        case 13: value = s->guard.bam_revision; break;
        case 14: value = s->guard.bam_num_pipes; break;
        case 15: value = s->guard.bam_ee_attr; break;
        case 16: value = s->ee_irq_mask; break;
        case 17: value = s->enabled_reference &&
                         (qcb_irq_sources(s) & s->ee_irq_mask) != 0; break;
        case 18: value = QCB_GUARD_BYTES; break;
        case 19: value = QCB_PIPE_BYTES; break;
        case 20: value = QCB_PAYLOAD_BYTES; break;
        case 21: value = 1; break;
        case 22:
        case 23:
        case 24: value = 0; break;
        case 25: value = s->guard.profile; break;
        case 26: value = s->guard.bam_ee_initial_mask; break;
        default: g_assert_not_reached();
        }
    }
    visit_type_uint64(v, name, &value, errp);
}

static void qcb_bool_get(Object *obj, Visitor *v, const char *name,
                         void *opaque, Error **errp)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(obj);
    bool value = false;

    if (GPOINTER_TO_UINT(opaque) == 1) {
        value = QCB_PROFILE_BOUND;
    } else if (GPOINTER_TO_UINT(opaque) == 2) {
        value = s->enabled_reference && QCB_PROFILE_BOUND;
    }
    visit_type_bool(v, name, &value, errp);
}

static char *qcb_profile_name_get(Object *obj, Error **errp)
{
    return g_strdup(QCB_PROFILE_NAME);
}

static char *qcb_profile_digest_get(Object *obj, Error **errp)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(obj);
    char *result = g_malloc(65);
    unsigned i;

    for (i = 0; i < 32; i++) {
        g_snprintf(result + 2 * i, 3, "%02x", s->guard.profile_digest[i]);
    }
    return result;
}

static bool qcb_bound_profile_valid(void)
{
    static const uint8_t digest[32] = QCB_PROFILE_DIGEST;
    static const uint8_t zero_digest[32];
    uint32_t rev = QCB_PROFILE_BAM_REVISION & 0xff;
    uint32_t pipes = QCB_PROFILE_BAM_NUM_PIPES & 0xff;

    return QCB_PROFILE_BOUND && strcmp(QCB_PROFILE_NAME, "UNBOUND") != 0 &&
           memcmp(digest, zero_digest, 32) != 0 &&
           (QCB_PROFILE_QCE_VERSION >> 24) == 5 && rev > 2 && rev <= 0x2f &&
           pipes >= QCB_FIRST_PIPE + QCB_PIPE_COUNT && pipes <= 31 &&
           (QCB_PROFILE_BAM_EE_ATTR & UINT32_C(0x800000f0)) ==
            UINT32_C(0x800000f0);
}

static void qcb_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(dev);

    if (s->enabled_reference && !qcb_bound_profile_valid()) {
        error_setg(errp, TYPE_QCOM_SM8250_QCE_BAM
                   ": reference profile is UNBOUND or invalid");
        return;
    }
    qcb_make_guard(s, &s->guard);
    s->ee_irq_mask = s->guard.bam_ee_initial_mask;
    memory_region_set_enabled(&s->iomem, s->enabled_reference);
}

static void qcb_init(Object *obj)
{
    QcomSm8250QceBamState *s = QCOM_SM8250_QCE_BAM(obj);
    static const char *const uint_properties[] = {
        "abi-identity", "physical-base", "window-bytes", "crypto-base",
        "crypto-window-bytes", "bam-base", "bam-window-bytes", "first-pipe",
        "pipe-count", "ee-index", "qce-version", "qce-engines", "bam-config",
        "bam-revision", "bam-num-pipes", "bam-ee-attr", "bam-ee-irq-mask",
        "irq-level", "vmstate-guard-bytes", "vmstate-pipe-bytes",
        "vmstate-payload-bytes", "irq-output-count", "clock-input-count",
        "clock-output-count", "timer-count", "enabled-profile",
        "bam-ee-initial-irq-mask",
    };
    static const char *const pipe_properties[] = {
        "control", "reset", "irq-enable", "irq-status", "descriptor-lo",
        "descriptor-hi", "fifo-size", "threshold",
    };
    static const char *const unsupported[] = {
        "dma-modeled", "crypto-operations-modeled", "secure-key-modeled",
        "firmware-modeled", "physical-identity-measured",
        "guest-interrupt-handling-verified",
    };
    unsigned i, j;

    qcb_make_guard(s, &s->guard);
    for (i = 0; i < ARRAY_SIZE(uint_properties); i++) {
        object_property_add(obj, uint_properties[i], "uint64", qcb_uint_get,
                             NULL, NULL, GUINT_TO_POINTER(i));
    }
    for (i = 0; i < QCB_PIPE_COUNT; i++) {
        for (j = 0; j < ARRAY_SIZE(pipe_properties); j++) {
            g_autofree char *name = g_strdup_printf("pipe-%u-%s",
                QCB_FIRST_PIPE + i, pipe_properties[j]);

            object_property_add(obj, name, "uint64", qcb_uint_get, NULL,
                                 NULL, GUINT_TO_POINTER(100 + i * 8 + j));
        }
    }
    object_property_add(obj, "reference-bound", "bool", qcb_bool_get,
                         NULL, NULL, GUINT_TO_POINTER(1));
    object_property_add(obj, "identity-known", "bool", qcb_bool_get,
                         NULL, NULL, GUINT_TO_POINTER(2));
    object_property_add_str(obj, "reference-profile-name",
                            qcb_profile_name_get, NULL);
    object_property_add_str(obj, "reference-profile-sha256",
                            qcb_profile_digest_get, NULL);
    for (i = 0; i < ARRAY_SIZE(unsupported); i++) {
        object_property_add(obj, unsupported[i], "bool", qcb_bool_get,
                             NULL, NULL, GUINT_TO_POINTER(0));
    }
    memory_region_init_io(&s->iomem, obj, &qcb_ops, s,
                          TYPE_QCOM_SM8250_QCE_BAM ".union",
                          QCOM_SM8250_QCE_BAM_SIZE);
    memory_region_set_enabled(&s->iomem, false);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
}

static const Property qcb_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250QceBamState,
                     enabled_reference, false),
};

static void qcb_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 shared QCE/BAM cold-control reference";
    dc->realize = qcb_realize;
    dc->vmsd = &vmstate_qcb;
    device_class_set_props(dc, qcb_properties);
    rc->phases.enter = qcb_reset_enter;
    rc->phases.hold = qcb_reset_hold;
}

static const TypeInfo qcb_type_info = {
    .name = TYPE_QCOM_SM8250_QCE_BAM,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250QceBamState),
    .instance_init = qcb_init,
    .class_init = qcb_class_init,
};

static void qcb_register_types(void)
{
    type_register_static(&qcb_type_info);
}
type_init(qcb_register_types)
