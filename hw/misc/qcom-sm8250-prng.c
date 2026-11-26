/*
 * Explicit TB371FC virtual PRNG data path for the preserved msm-rng DT node.
 *
 * The host RNG backend supplies entropy; this is not a silicon QRNG model.
 * Only the DT-selected, firmware-configured data/status aperture is exposed.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-prng.h"
#include "hw/core/qdev-clock.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qom/object_interfaces.h"
#include "system/rng.h"
#include "system/rng-random.h"

#define PRNG_FIFO_BYTES 64
#define PRNG_PROFILE_HOST 0x50524e01U
#define PRNG_PROFILE_EXTERNAL 0x50524e02U

struct QcomSm8250PrngState {
    SysBusDevice parent_obj;
    MemoryRegion iomem;
    Clock *iface;
    RngBackend *rng;
    bool enabled;
    bool ready;
    bool pending;
    uint32_t pending_bytes;
    uint32_t profile_id;
    uint32_t head;
    uint32_t count;
    uint8_t fifo[PRNG_FIFO_BYTES];
    uint64_t words_read;
    uint64_t backend_requests;
    uint64_t empty_responses;
};

static void prng_receive(void *opaque, const void *data, size_t size);

static bool prng_clocked(QcomSm8250PrngState *s)
{
    return s->ready && s->enabled && clock_get_hz(s->iface) != 0;
}

static void prng_cancel(QcomSm8250PrngState *s)
{
    if (s->rng) {
        rng_backend_cancel_requests(s->rng, prng_receive, s);
    }
    s->pending = false;
    s->pending_bytes = 0;
}

static void prng_request(QcomSm8250PrngState *s)
{
    if (!prng_clocked(s) || s->pending || s->count == PRNG_FIFO_BYTES) {
        return;
    }
    s->pending = true;
    s->pending_bytes = PRNG_FIFO_BYTES - s->count;
    s->backend_requests++;
    rng_backend_request_entropy(s->rng, s->pending_bytes, prng_receive, s);
}

static void prng_receive(void *opaque, const void *data, size_t size)
{
    QcomSm8250PrngState *s = opaque;
    const uint8_t *bytes = data;
    uint32_t expected = s->pending_bytes;
    bool pending = s->pending;
    size_t i;

    s->pending = false;
    s->pending_bytes = 0;
    /*
     * A backend may deliver a short result (including EOF from rng-random).
     * Never manufacture the missing bytes or advertise a partial word.
     * Do not cancel the active request here: the backend frees it on return.
     */
    if (!pending || !prng_clocked(s) || size > expected ||
        size > PRNG_FIFO_BYTES - s->count) {
        return;
    }
    if (!size) {
        s->empty_responses++;
        return;
    }
    for (i = 0; i < size; i++) {
        s->fifo[(s->head + s->count + i) % PRNG_FIFO_BYTES] = bytes[i];
    }
    s->count += size;
    /* Further requests come from guest accesses, never an EOF busy loop. */
}

static void prng_clock_changed(void *opaque, ClockEvent event)
{
    QcomSm8250PrngState *s = opaque;

    if (prng_clocked(s)) {
        prng_request(s);
    } else {
        prng_cancel(s);
    }
}

static MemTxResult prng_read(void *opaque, hwaddr offset, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    QcomSm8250PrngState *s = opaque;
    uint32_t word = 0;
    unsigned i;

    *value = 0;
    if (size != 4 || (offset != 0 && offset != 4)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "qcom-sm8250-prng: rejected read offset=0x%"
                      HWADDR_PRIx " size=%u\n", offset, size);
        return MEMTX_ACCESS_ERROR;
    }
    if (offset == 4) {
        prng_request(s);
        *value = prng_clocked(s) && s->count >= 4;
        return MEMTX_OK;
    }
    if (offset != 0 || !prng_clocked(s) || s->count < 4) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "qcom-sm8250-prng: data read without a ready word\n");
        return MEMTX_ACCESS_ERROR;
    }
    for (i = 0; i < 4; i++) {
        word |= (uint32_t)s->fifo[s->head] << (i * 8);
        s->fifo[s->head] = 0;
        s->head = (s->head + 1) % PRNG_FIFO_BYTES;
    }
    s->count -= 4;
    if (!s->count) {
        s->head = 0;
    }
    s->words_read++;
    *value = word;
    prng_request(s);
    return MEMTX_OK;
}

static MemTxResult prng_write(void *opaque, hwaddr offset, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  "qcom-sm8250-prng: rejected write offset=0x%"
                  HWADDR_PRIx " size=%u\n", offset, size);
    return MEMTX_ACCESS_ERROR;
}

static const MemoryRegionOps prng_ops = {
    .read_with_attrs = prng_read,
    .write_with_attrs = prng_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Preserve 1--8 byte transactions so the callback can reject them whole. */
    .valid = {
        .min_access_size = 1,
        .max_access_size = 8,
        .unaligned = true,
    },
    .impl = {
        .min_access_size = 1,
        .max_access_size = 8,
        .unaligned = true,
    },
};

static void prng_reset(DeviceState *dev)
{
    QcomSm8250PrngState *s = QCOM_SM8250_PRNG(dev);

    prng_cancel(s);
    memset(s->fifo, 0, sizeof(s->fifo));
    s->head = 0;
    s->count = 0;
    s->words_read = 0;
    s->backend_requests = 0;
    s->empty_responses = 0;
    prng_request(s);
}

static int prng_pre_save(void *opaque)
{
    prng_cancel(opaque);
    return 0;
}

static int prng_pre_load(void *opaque)
{
    prng_cancel(opaque);
    return 0;
}

static int prng_post_load(void *opaque, int version)
{
    QcomSm8250PrngState *s = opaque;
    unsigned i;

    if (s->count > PRNG_FIFO_BYTES || s->head >= PRNG_FIFO_BYTES ||
        (!s->count && s->head)) {
        return -EINVAL;
    }
    if (!s->enabled &&
        (s->count || s->head || s->words_read ||
         s->backend_requests || s->empty_responses)) {
        return -EINVAL;
    }
    for (i = s->count; i < PRNG_FIFO_BYTES; i++) {
        if (s->fifo[(s->head + i) % PRNG_FIFO_BYTES]) {
            return -EINVAL;
        }
    }
    /*
     * GCC owns the input clock. Host requests are not migratable.
     * Preserve only unconsumed bytes; the next guest access may refill.
     */
    return 0;
}

static const VMStateDescription vmstate_prng = {
    .name = TYPE_QCOM_SM8250_PRNG,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = prng_pre_save,
    .pre_load = prng_pre_load,
    .post_load = prng_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile_id, QcomSm8250PrngState),
        VMSTATE_UINT32(head, QcomSm8250PrngState),
        VMSTATE_UINT32(count, QcomSm8250PrngState),
        VMSTATE_UINT8_ARRAY(fifo, QcomSm8250PrngState, PRNG_FIFO_BYTES),
        VMSTATE_UINT64(words_read, QcomSm8250PrngState),
        VMSTATE_UINT64(backend_requests, QcomSm8250PrngState),
        VMSTATE_UINT64(empty_responses, QcomSm8250PrngState),
        VMSTATE_END_OF_LIST()
    }
};

static void prng_get_state(Object *obj, Visitor *v, const char *name,
                           void *opaque, Error **errp)
{
    QcomSm8250PrngState *s = QCOM_SM8250_PRNG(obj);
    uint64_t value;

    if (!strcmp(name, "profile-id")) {
        value = s->profile_id;
    } else if (!strcmp(name, "fifo-bytes")) {
        value = s->count;
    } else if (!strcmp(name, "words-read")) {
        value = s->words_read;
    } else if (!strcmp(name, "backend-requests")) {
        value = s->backend_requests;
    } else if (!strcmp(name, "empty-responses")) {
        value = s->empty_responses;
    } else if (!strcmp(name, "request-pending")) {
        value = s->pending;
    } else {
        value = clock_get_hz(s->iface);
    }
    visit_type_uint64(v, name, &value, errp);
}

static char *prng_get_backend_type(Object *obj, Error **errp)
{
    QcomSm8250PrngState *s = QCOM_SM8250_PRNG(obj);

    return g_strdup(s->rng ? object_get_typename(OBJECT(s->rng)) : "none");
}

static void prng_realize(DeviceState *dev, Error **errp)
{
    QcomSm8250PrngState *s = QCOM_SM8250_PRNG(dev);

    if (!s->enabled) {
        s->profile_id = 0;
        s->ready = true;
        return;
    }
    if (!s->rng) {
        Object *backend;

        if (!object_class_by_name(TYPE_RNG_RANDOM)) {
            error_setg(errp, "virtual PRNG requires the rng-random backend");
            return;
        }
        backend = object_new(TYPE_RNG_RANDOM);
        object_property_set_str(backend, "filename", "/dev/urandom",
                                &error_abort);
        if (!user_creatable_complete(USER_CREATABLE(backend), errp)) {
            object_unref(backend);
            return;
        }
        object_property_add_child(OBJECT(dev), "default-backend", backend);
        object_unref(backend);
        object_property_set_link(OBJECT(dev), "rng", backend, &error_abort);
        s->profile_id = PRNG_PROFILE_HOST;
    } else {
        s->profile_id = PRNG_PROFILE_EXTERNAL;
    }
    if (!object_property_get_bool(OBJECT(s->rng), "opened", &error_abort) ||
        !RNG_BACKEND_GET_CLASS(s->rng)->request_entropy) {
        error_setg(errp, "virtual PRNG needs an opened entropy backend");
        return;
    }
    s->ready = true;
    prng_request(s);
}

static void prng_unrealize(DeviceState *dev)
{
    QcomSm8250PrngState *s = QCOM_SM8250_PRNG(dev);

    s->ready = false;
    prng_cancel(s);
}

static void prng_init(Object *obj)
{
    QcomSm8250PrngState *s = QCOM_SM8250_PRNG(obj);
    static const char *const state[] = {
        "profile-id", "fifo-bytes", "words-read", "backend-requests",
        "empty-responses", "request-pending", "clock-hz",
    };
    unsigned i;

    memory_region_init_io(&s->iomem, obj, &prng_ops, s,
                          TYPE_QCOM_SM8250_PRNG ".decode-guard", 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    s->iface = qdev_init_clock_in(DEVICE(obj), "iface",
                                 prng_clock_changed, s, ClockUpdate);
    for (i = 0; i < ARRAY_SIZE(state); i++) {
        object_property_add(obj, state[i], "uint64", prng_get_state,
                            NULL, NULL, NULL);
    }
    object_property_add_str(obj, "backend-type", prng_get_backend_type, NULL);
}

static const Property prng_properties[] = {
    DEFINE_PROP_BOOL("enabled", QcomSm8250PrngState, enabled, false),
    DEFINE_PROP_LINK("rng", QcomSm8250PrngState, rng,
                     TYPE_RNG_BACKEND, RngBackend *),
};

static void prng_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Explicit TB371FC virtual PRNG with a host entropy backend";
    dc->user_creatable = false;
    dc->realize = prng_realize;
    dc->unrealize = prng_unrealize;
    dc->vmsd = &vmstate_prng;
    device_class_set_legacy_reset(dc, prng_reset);
    device_class_set_props(dc, prng_properties);
}

static const TypeInfo prng_info = {
    .name = TYPE_QCOM_SM8250_PRNG,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250PrngState),
    .instance_init = prng_init,
    .class_init = prng_class_init,
};

static void prng_register_types(void)
{
    type_register_static(&prng_info);
}
type_init(prng_register_types)
