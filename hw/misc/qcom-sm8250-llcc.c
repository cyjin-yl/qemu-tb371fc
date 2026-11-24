/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Source-bound SM8250 LLCC v2 digital control reference.
 *
 * The Kona table and register operations are from OnePlusOSS
 * android_kernel_oneplus_sm8250@98719830b0de5584f5588bbe9f4c8304d97a64c6.
 * Four banks are an explicit reference geometry backed by the SM8250 DT in
 * torvalds/linux@adc218676eef25575469234709c2d87185ca223a, not a measured
 * production COMMON_STATUS0 value. The 1000 ns transition is a reference
 * policy; the driver specifies a 100 us polling budget, not silicon latency.
 *
 * Only broadcast control writes are implemented. Read-only bank views share
 * their canonical broadcast state. There is no cache/coherency datapath,
 * performance counter, memory-controller count, hardware revision, or ECC
 * error injection. The two cold ECC status words report no modeled errors.
 * HW_INFO and the unknown MC field remain zero; the guest's fallback does
 * not establish a hardware version or a memory-controller count here.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-llcc.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define LLCC_CAD 0x21f00
#define LLCC_PCB 0x21f04
#define LLCC_WRSC 0x21f20

enum {
    LLCC_IDLE,
    LLCC_ARMED_ACTIVATE,
    LLCC_ARMED_DEACTIVATE,
    LLCC_PENDING_ACTIVATE,
    LLCC_PENDING_DEACTIVATE,
};

const uint64_t qcom_sm8250_llcc_bases[LLCC_REGIONS] = {
    0x09200000, 0x09600000,
};

#include "qcom-sm8250-llcc-data.inc"

typedef struct LlccWindow {
    QcomSm8250LlccState *state;
    uint32_t offset;
    uint8_t region;
    uint8_t bank;
} LlccWindow;

struct QcomSm8250LlccState {
    SysBusDevice parent_obj;
    MemoryRegion container[LLCC_REGIONS];
    MemoryRegion iomem[LLCC_WINDOWS];
    LlccWindow windows[LLCC_WINDOWS];
    QEMUTimer *timer;
    bool enabled_reference;

    uint32_t reference_profile;
    uint32_t reference_identity;
    uint32_t bank_count;
    uint32_t slice_count;
    uint32_t register_count;
    uint32_t window_count;
    uint32_t mapped_bytes;
    uint32_t region_count;
    uint32_t bank_stride;
    uint32_t bank_bytes;
    uint32_t transition_ns;
    uint32_t common_status;
    uint32_t hardware_info;
    uint64_t physical_base[LLCC_REGIONS];
    uint32_t resource_bytes[LLCC_REGIONS];

    uint32_t regs[LLCC_REGISTERS];
    uint8_t written[LLCC_REGISTERS];
    uint8_t active[LLCC_SLICES];
    uint8_t phase[LLCC_SLICES];
    uint32_t captured_attr0[LLCC_SLICES];
    uint32_t captured_attr1[LLCC_SLICES];
    uint32_t captured_flags[LLCC_SLICES];
    int64_t deadline[LLCC_SLICES];
};

static int llcc_register(hwaddr offset)
{
    unsigned i;

    for (i = 0; i < LLCC_REGISTERS; i++) {
        if (llcc_regs[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static unsigned llcc_attr_register(unsigned slice, unsigned attr)
{
    int reg = llcc_register(0x21000 + llcc_slices[slice].sid * 8 + attr * 4);

    g_assert(reg >= 0);
    return reg;
}

static unsigned llcc_ctrl_register(unsigned slice)
{
    int reg = llcc_register(llcc_slices[slice].sid * 0x1000);

    g_assert(reg >= 0);
    return reg;
}

static uint32_t llcc_slice_flags(QcomSm8250LlccState *s, unsigned slice)
{
    unsigned sid = llcc_slices[slice].sid;

    return ((s->regs[llcc_register(LLCC_WRSC)] >> sid) & 1) |
           (((s->regs[llcc_register(LLCC_CAD)] >> sid) & 1) << 1) |
           (((s->regs[llcc_register(LLCC_PCB)] >> sid) & 1) << 2);
}

static bool llcc_recipe_valid(QcomSm8250LlccState *s, unsigned slice)
{
    const LlccSliceDef *d = &llcc_slices[slice];
    unsigned a0 = llcc_attr_register(slice, 0);
    unsigned a1 = llcc_attr_register(slice, 1);
    uint32_t flags = d->write_scid | (d->dis_cap << 1) | (d->retain << 2);

    return s->enabled_reference && s->written[a0] && s->written[a1] &&
           s->written[llcc_register(LLCC_WRSC)] &&
           s->written[llcc_register(LLCC_CAD)] &&
           s->written[llcc_register(LLCC_PCB)] &&
           s->regs[a0] == d->attr0 && s->regs[a1] == d->attr1 &&
           llcc_slice_flags(s, slice) == flags;
}

static bool llcc_pending(unsigned phase)
{
    return phase == LLCC_PENDING_ACTIVATE ||
           phase == LLCC_PENDING_DEACTIVATE;
}

static bool llcc_activating(unsigned phase)
{
    return phase == LLCC_ARMED_ACTIVATE ||
           phase == LLCC_PENDING_ACTIVATE;
}

static void llcc_cancel(QcomSm8250LlccState *s, unsigned slice)
{
    s->phase[slice] = LLCC_IDLE;
    s->captured_attr0[slice] = 0;
    s->captured_attr1[slice] = 0;
    s->captured_flags[slice] = 0;
    s->deadline[slice] = -1;
}

static void llcc_schedule(QcomSm8250LlccState *s)
{
    int64_t earliest = INT64_MAX;
    unsigned i;

    for (i = 0; i < LLCC_SLICES; i++) {
        if (llcc_pending(s->phase[i])) {
            earliest = MIN(earliest, s->deadline[i]);
        }
    }
    if (earliest == INT64_MAX) {
        timer_del(s->timer);
    } else {
        timer_mod_ns(s->timer, earliest);
    }
}

static bool llcc_capture_valid(QcomSm8250LlccState *s, unsigned slice)
{
    unsigned phase = s->phase[slice];
    uint32_t command;

    if (!s->enabled_reference || phase == LLCC_IDLE ||
        phase > LLCC_PENDING_DEACTIVATE) {
        return false;
    }
    command = llcc_activating(phase) ? 2 : 4;
    if (!llcc_pending(phase)) {
        command |= 1;
    }
    return s->regs[llcc_ctrl_register(slice)] == command &&
           s->captured_attr0[slice] ==
               s->regs[llcc_attr_register(slice, 0)] &&
           s->captured_attr1[slice] ==
               s->regs[llcc_attr_register(slice, 1)] &&
           s->captured_flags[slice] == llcc_slice_flags(s, slice) &&
           (!llcc_activating(phase) || llcc_recipe_valid(s, slice));
}

static void llcc_timer(void *opaque)
{
    QcomSm8250LlccState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    for (i = 0; i < LLCC_SLICES; i++) {
        if (llcc_pending(s->phase[i]) && s->deadline[i] <= now) {
            if (llcc_capture_valid(s, i)) {
                s->active[i] = s->phase[i] == LLCC_PENDING_ACTIVATE;
            }
            llcc_cancel(s, i);
        }
    }
    llcc_schedule(s);
}

static void llcc_command(QcomSm8250LlccState *s, unsigned slice,
                         uint32_t value)
{
    unsigned old_phase = s->phase[slice];
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (value == 3 || value == 5) {
        llcc_cancel(s, slice);
        if (value == 5 || llcc_recipe_valid(s, slice)) {
            s->phase[slice] = value == 3 ?
                LLCC_ARMED_ACTIVATE : LLCC_ARMED_DEACTIVATE;
            s->captured_attr0[slice] =
                s->regs[llcc_attr_register(slice, 0)];
            s->captured_attr1[slice] =
                s->regs[llcc_attr_register(slice, 1)];
            s->captured_flags[slice] = llcc_slice_flags(s, slice);
        }
    } else if (((value == 2 && old_phase == LLCC_ARMED_ACTIVATE) ||
                (value == 4 && old_phase == LLCC_ARMED_DEACTIVATE)) &&
               now >= 0 && now < INT64_MAX - LLCC_TRANSITION_NS) {
        s->phase[slice] = value == 2 ?
            LLCC_PENDING_ACTIVATE : LLCC_PENDING_DEACTIVATE;
        if (llcc_capture_valid(s, slice)) {
            s->deadline[slice] = now + LLCC_TRANSITION_NS;
        } else {
            llcc_cancel(s, slice);
        }
    } else {
        llcc_cancel(s, slice);
    }
    llcc_schedule(s);
}

static MemTxResult llcc_reject(LlccWindow *w, hwaddr offset,
                               const char *operation, unsigned size,
                               const char *reason)
{
    if (w->region) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_LLCC
                      ": rejected %s broadcast+0x%05" HWADDR_PRIx
                      " size %u (%s)\n", operation, offset, size, reason);
    } else {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_LLCC
                      ": rejected %s bank%u+0x%05" HWADDR_PRIx
                      " size %u (%s)\n", operation, w->bank, offset,
                      size, reason);
    }
    return MEMTX_ERROR;
}

static bool llcc_view_allows(const LlccWindow *w, unsigned kind)
{
    return w->region ?
        kind != LLCC_REG_TRP_ERROR && kind != LLCC_REG_DRP_ERROR :
        kind != LLCC_REG_HW_INFO;
}

static MemTxResult llcc_read(void *opaque, hwaddr address, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    LlccWindow *w = opaque;
    QcomSm8250LlccState *s = w->state;
    hwaddr offset = w->offset + address;
    int reg = llcc_register(offset);
    const LlccRegDef *d;

    *value = 0;
    if (!s->enabled_reference || size != 4 || (offset & 3) || reg < 0 ||
        !llcc_view_allows(w, llcc_regs[reg].kind)) {
        return llcc_reject(w, offset, "read", size,
                           "disabled-or-invalid-access");
    }
    d = &llcc_regs[reg];
    switch (d->kind) {
    case LLCC_REG_STATUS:
        *value = s->active[d->slice] ? 1 : 2;
        break;
    case LLCC_REG_COMMON_STATUS:
        *value = s->common_status;
        break;
    case LLCC_REG_HW_INFO:
    case LLCC_REG_TRP_ERROR:
    case LLCC_REG_DRP_ERROR:
        /* Unknown revision and no injected ECC errors grant no readiness. */
        *value = 0;
        break;
    default:
        *value = s->regs[reg];
        break;
    }
    return MEMTX_OK;
}

static MemTxResult llcc_write(void *opaque, hwaddr address, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    LlccWindow *w = opaque;
    QcomSm8250LlccState *s = w->state;
    hwaddr offset = w->offset + address;
    int reg = llcc_register(offset);
    const LlccRegDef *d;
    uint32_t changed;
    unsigned i;

    if (!s->enabled_reference || size != 4 || (offset & 3) || reg < 0 ||
        !llcc_view_allows(w, llcc_regs[reg].kind)) {
        return llcc_reject(w, offset, "write", size,
                           "disabled-or-invalid-access");
    }
    if (!w->region) {
        return llcc_reject(w, offset, "write", size,
                           "read-only-bank-view");
    }
    d = &llcc_regs[reg];
    if (!d->write_mask) {
        return llcc_reject(w, offset, "write", size, "read-only-status");
    }
    if ((value & ~((uint64_t)d->write_mask)) ||
        (d->kind == LLCC_REG_CTRL && value != 0 && value != 2 &&
         value != 3 && value != 4 && value != 5)) {
        return llcc_reject(w, offset, "write", size, "unsupported-bits");
    }
    changed = s->regs[reg] ^ value;
    if (changed && (d->kind == LLCC_REG_ATTR0 || d->kind == LLCC_REG_ATTR1)) {
        llcc_cancel(s, d->slice);
    } else if (changed && (d->kind == LLCC_REG_CAD ||
                           d->kind == LLCC_REG_PCB ||
                           d->kind == LLCC_REG_WRSC)) {
        for (i = 0; i < LLCC_SLICES; i++) {
            if (changed & (UINT32_C(1) << llcc_slices[i].sid)) {
                llcc_cancel(s, i);
            }
        }
    }
    s->regs[reg] = value;
    if (d->kind == LLCC_REG_CTRL) {
        llcc_command(s, d->slice, value);
    } else {
        s->written[reg] = 1;
        llcc_schedule(s);
    }
    return MEMTX_OK;
}

static const MemoryRegionOps llcc_ops = {
    .read_with_attrs = llcc_read,
    .write_with_attrs = llcc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Preserve the transaction width delivered to a single leaf. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void llcc_reset_enter(Object *obj, ResetType type)
{
    QcomSm8250LlccState *s = QCOM_SM8250_LLCC(obj);
    unsigned i;

    timer_del(s->timer);
    memset(s->regs, 0, sizeof(s->regs));
    memset(s->written, 0, sizeof(s->written));
    memset(s->active, 0, sizeof(s->active));
    for (i = 0; i < LLCC_SLICES; i++) {
        llcc_cancel(s, i);
    }
}

static int llcc_pre_load(void *opaque)
{
    QcomSm8250LlccState *s = opaque;

    timer_del(s->timer);
    return 0;
}

static int llcc_bad_state(const char *category)
{
    error_report(TYPE_QCOM_SM8250_LLCC ": invalid migrated %s", category);
    return -EINVAL;
}

static int llcc_post_load(void *opaque, int version_id)
{
    QcomSm8250LlccState *s = opaque;
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    unsigned i;

    if (now < 0) {
        return llcc_bad_state("transition");
    }
    for (i = 0; i < LLCC_REGISTERS; i++) {
        const LlccRegDef *d = &llcc_regs[i];
        uint32_t value = s->regs[i];

        if ((value & ~d->write_mask) ||
            (d->kind == LLCC_REG_CTRL && value != 0 && value != 2 &&
             value != 3 && value != 4 && value != 5)) {
            return llcc_bad_state("register");
        }
        if (s->written[i] > 1 ||
            ((!d->write_mask || d->kind == LLCC_REG_CTRL) && s->written[i]) ||
            (d->write_mask && d->kind != LLCC_REG_CTRL &&
             value && !s->written[i])) {
            return llcc_bad_state("written");
        }
        if (!s->enabled_reference && (value || s->written[i])) {
            return llcc_bad_state("disabled state");
        }
    }
    for (i = 0; i < LLCC_SLICES; i++) {
        unsigned phase = s->phase[i];

        if (s->active[i] > 1 || phase > LLCC_PENDING_DEACTIVATE) {
            return llcc_bad_state("transition");
        }
        if (!s->enabled_reference && (s->active[i] || phase)) {
            return llcc_bad_state("disabled state");
        }
        /* A successful activation always leaves these programming credits. */
        if (s->active[i] &&
            (!s->written[llcc_attr_register(i, 0)] ||
             !s->written[llcc_attr_register(i, 1)] ||
             !s->written[llcc_register(LLCC_WRSC)] ||
             !s->written[llcc_register(LLCC_CAD)] ||
             !s->written[llcc_register(LLCC_PCB)])) {
            return llcc_bad_state("written");
        }
        if (phase == LLCC_IDLE) {
            if (s->captured_attr0[i] || s->captured_attr1[i] ||
                s->captured_flags[i]) {
                return llcc_bad_state("capture");
            }
            if (s->deadline[i] != -1) {
                return llcc_bad_state("transition");
            }
        } else {
            if (!llcc_capture_valid(s, i)) {
                return llcc_bad_state("capture");
            }
            if (llcc_pending(phase)) {
                if (s->deadline[i] == INT64_MAX || s->deadline[i] < now ||
                    s->deadline[i] - now > LLCC_TRANSITION_NS) {
                    return llcc_bad_state("transition");
                }
            } else if (s->deadline[i] != -1) {
                return llcc_bad_state("transition");
            }
        }
    }
    llcc_schedule(s);
    return 0;
}

static const VMStateDescription vmstate_llcc = {
    .name = TYPE_QCOM_SM8250_LLCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = llcc_pre_load,
    .post_load = llcc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(reference_profile, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(reference_identity, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(bank_count, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(slice_count, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(register_count, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(window_count, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(mapped_bytes, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(region_count, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(bank_stride, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(bank_bytes, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(transition_ns, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(common_status, QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(hardware_info, QcomSm8250LlccState),
        VMSTATE_UINT64_EQUAL(physical_base[0], QcomSm8250LlccState),
        VMSTATE_UINT64_EQUAL(physical_base[1], QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(resource_bytes[0], QcomSm8250LlccState),
        VMSTATE_UINT32_EQUAL(resource_bytes[1], QcomSm8250LlccState),
        VMSTATE_UINT32_ARRAY(regs, QcomSm8250LlccState, LLCC_REGISTERS),
        VMSTATE_UINT8_ARRAY(written, QcomSm8250LlccState, LLCC_REGISTERS),
        VMSTATE_UINT8_ARRAY(active, QcomSm8250LlccState, LLCC_SLICES),
        VMSTATE_UINT8_ARRAY(phase, QcomSm8250LlccState, LLCC_SLICES),
        VMSTATE_UINT32_ARRAY(captured_attr0, QcomSm8250LlccState, LLCC_SLICES),
        VMSTATE_UINT32_ARRAY(captured_attr1, QcomSm8250LlccState, LLCC_SLICES),
        VMSTATE_UINT32_ARRAY(captured_flags, QcomSm8250LlccState, LLCC_SLICES),
        VMSTATE_INT64_ARRAY(deadline, QcomSm8250LlccState, LLCC_SLICES),
        VMSTATE_END_OF_LIST()
    },
};

static void llcc_byte_bool_get(Object *obj, Visitor *v, const char *name,
                               void *opaque, Error **errp)
{
    bool value = *(uint8_t *)opaque;

    visit_type_bool(v, name, &value, errp);
}

static void llcc_byte_get(Object *obj, Visitor *v, const char *name,
                          void *opaque, Error **errp)
{
    uint32_t value = *(uint8_t *)opaque;

    visit_type_uint32(v, name, &value, errp);
}

static void llcc_signed_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    int64_t value = *(int64_t *)opaque;

    visit_type_int64(v, name, &value, errp);
}

static void llcc_known_get(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    QcomSm8250LlccState *s = QCOM_SM8250_LLCC(obj);
    bool value = GPOINTER_TO_UINT(opaque) && s->enabled_reference;

    visit_type_bool(v, name, &value, errp);
}

static void llcc_constant_get(Object *obj, Visitor *v, const char *name,
                              void *opaque, Error **errp)
{
    uint32_t value = GPOINTER_TO_UINT(opaque);

    visit_type_uint32(v, name, &value, errp);
}

static void llcc_recipe_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    QcomSm8250LlccState *s = QCOM_SM8250_LLCC(obj);
    bool value = llcc_recipe_valid(s, GPOINTER_TO_UINT(opaque));

    visit_type_bool(v, name, &value, errp);
}

static void llcc_status_get(Object *obj, Visitor *v, const char *name,
                            void *opaque, Error **errp)
{
    QcomSm8250LlccState *s = QCOM_SM8250_LLCC(obj);
    uint32_t value = !s->enabled_reference ? 3 :
        s->active[GPOINTER_TO_UINT(opaque)] ? 1 : 2;

    visit_type_uint32(v, name, &value, errp);
}

static void llcc_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250LlccState *s = QCOM_SM8250_LLCC(dev);

    s->reference_profile = s->enabled_reference;
}

static void llcc_init(Object *obj)
{
    QcomSm8250LlccState *s = QCOM_SM8250_LLCC(obj);
    static const char *const metadata[] = {
        "enabled-profile", "reference-identity", "bank-count", "slice-count",
        "register-count", "sparse-window-count", "mapped-bytes", "region-count",
        "bank-stride", "bank-resource-bytes", "transition-delay-ns",
        "reference-common-status", "reference-hardware-info",
    };
    uint32_t *values[] = {
        &s->reference_profile, &s->reference_identity, &s->bank_count,
        &s->slice_count, &s->register_count, &s->window_count, &s->mapped_bytes,
        &s->region_count, &s->bank_stride, &s->bank_bytes, &s->transition_ns,
        &s->common_status, &s->hardware_info,
    };
    static const uint32_t initial[] = {
        0, LLCC_REFERENCE_ID, LLCC_BANK_COUNT, LLCC_SLICES, LLCC_REGISTERS,
        LLCC_WINDOWS, LLCC_MAPPED_BYTES, LLCC_REGIONS, LLCC_BANK_STRIDE,
        LLCC_BANK_BYTES, LLCC_TRANSITION_NS, 0x40000000, 0,
    };
    static const char *const unknown[] = {
        "hardware-version-known", "memory-controller-count-known",
        "cache-data-modelled", "performance-counters-modelled",
        "ecc-error-injection-modelled",
    };
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(values); i++) {
        *values[i] = initial[i];
        object_property_add_uint32_ptr(obj, metadata[i], values[i],
                                       OBJ_PROP_FLAG_READ);
    }
    for (i = 0; i < ARRAY_SIZE(unknown); i++) {
        object_property_add(obj, unknown[i], "bool", llcc_known_get,
                            NULL, NULL, GUINT_TO_POINTER(0));
    }
    object_property_add(obj, "bank-count-known", "bool", llcc_known_get,
                        NULL, NULL, GUINT_TO_POINTER(1));
    object_property_add(obj, "mapped-word-count", "uint32", llcc_constant_get,
                        NULL, NULL, GUINT_TO_POINTER(LLCC_MAPPED_WORDS));
    object_property_add(obj, "timer-count", "uint32", llcc_constant_get,
                        NULL, NULL, GUINT_TO_POINTER(1));
    object_property_add(obj, "vmstate-bytes", "uint32", llcc_constant_get,
                        NULL, NULL, GUINT_TO_POINTER(LLCC_VMSTATE_BYTES));
    for (i = 0; i < LLCC_REGIONS; i++) {
        g_autofree char *name = g_strdup_printf("%s.%s", TYPE_QCOM_SM8250_LLCC,
                                               i ? "broadcast" : "banks");
        g_autofree char *base = g_strdup_printf("region%u.physical-base", i);
        g_autofree char *bytes = g_strdup_printf("region%u.resource-bytes", i);

        s->physical_base[i] = qcom_sm8250_llcc_bases[i];
        s->resource_bytes[i] = i ? LLCC_BANK_BYTES : LLCC_BANKS_REGION_BYTES;
        object_property_add_uint64_ptr(obj, base, &s->physical_base[i],
                                       OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, bytes, &s->resource_bytes[i],
                                       OBJ_PROP_FLAG_READ);
        memory_region_init(&s->container[i], obj, name, s->resource_bytes[i]);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->container[i]);
    }
    for (i = 0; i < LLCC_WINDOWS; i++) {
        const LlccWindowDef *d = &llcc_windows[i];
        uint32_t local = d->offset - (d->region ? 0 :
                                            d->bank * LLCC_BANK_STRIDE);
        g_autofree char *name = d->region ?
            g_strdup_printf("%s.broadcast.%05x", TYPE_QCOM_SM8250_LLCC, local) :
            g_strdup_printf("%s.bank%u.%05x", TYPE_QCOM_SM8250_LLCC,
                            d->bank, local);

        s->windows[i].state = s;
        s->windows[i].region = d->region;
        s->windows[i].bank = d->bank;
        s->windows[i].offset = local;
        memory_region_init_io(&s->iomem[i], obj, &llcc_ops, &s->windows[i],
                              name, d->size);
        memory_region_add_subregion(&s->container[d->region], d->offset,
                                     &s->iomem[i]);
    }
    for (i = 0; i < LLCC_REGISTERS; i++) {
        g_autofree char *reg = g_strdup_printf("reg.%05x", llcc_regs[i].offset);
        g_autofree char *written = g_strdup_printf("written.%05x",
                                                   llcc_regs[i].offset);

        object_property_add_uint32_ptr(obj, reg, &s->regs[i],
                                       OBJ_PROP_FLAG_READ);
        object_property_add(obj, written, "bool", llcc_byte_bool_get,
                            NULL, NULL, &s->written[i]);
    }
    for (i = 0; i < LLCC_SLICES; i++) {
        unsigned sid = llcc_slices[i].sid;
        g_autofree char *active = g_strdup_printf("slice%u.active", sid);
        g_autofree char *known = g_strdup_printf("slice%u.state-known", sid);
        g_autofree char *recipe = g_strdup_printf("slice%u.recipe-valid", sid);
        g_autofree char *status = g_strdup_printf("slice%u.status", sid);
        g_autofree char *phase = g_strdup_printf("slice%u.phase", sid);
        g_autofree char *attr0 = g_strdup_printf("slice%u.captured-attr0", sid);
        g_autofree char *attr1 = g_strdup_printf("slice%u.captured-attr1", sid);
        g_autofree char *flags = g_strdup_printf("slice%u.captured-flags", sid);
        g_autofree char *deadline = g_strdup_printf("slice%u.deadline-ns", sid);

        s->deadline[i] = -1;
        object_property_add(obj, active, "bool", llcc_byte_bool_get,
                            NULL, NULL, &s->active[i]);
        object_property_add(obj, known, "bool", llcc_known_get,
                            NULL, NULL, GUINT_TO_POINTER(1));
        object_property_add(obj, recipe, "bool", llcc_recipe_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
        object_property_add(obj, status, "uint32", llcc_status_get,
                            NULL, NULL, GUINT_TO_POINTER(i));
        object_property_add(obj, phase, "uint32", llcc_byte_get,
                            NULL, NULL, &s->phase[i]);
        object_property_add_uint32_ptr(obj, attr0, &s->captured_attr0[i],
                                       OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, attr1, &s->captured_attr1[i],
                                       OBJ_PROP_FLAG_READ);
        object_property_add_uint32_ptr(obj, flags, &s->captured_flags[i],
                                       OBJ_PROP_FLAG_READ);
        object_property_add(obj, deadline, "int64", llcc_signed_get,
                            NULL, NULL, &s->deadline[i]);
    }
    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, llcc_timer, s);
}

static void llcc_finalize(Object *obj)
{
    timer_free(QCOM_SM8250_LLCC(obj)->timer);
}

static const Property llcc_properties[] = {
    DEFINE_PROP_BOOL("enabled-reference", QcomSm8250LlccState,
                     enabled_reference, false),
};

static void llcc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "SM8250 LLCC v2 bounded digital control reference";
    dc->realize = llcc_realize;
    dc->vmsd = &vmstate_llcc;
    device_class_set_props(dc, llcc_properties);
    rc->phases.enter = llcc_reset_enter;
}

static const TypeInfo llcc_info = {
    .name = TYPE_QCOM_SM8250_LLCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250LlccState),
    .instance_init = llcc_init,
    .instance_finalize = llcc_finalize,
    .class_init = llcc_class_init,
};

static void llcc_register_types(void)
{
    type_register_static(&llcc_info);
}
type_init(llcc_register_types)
