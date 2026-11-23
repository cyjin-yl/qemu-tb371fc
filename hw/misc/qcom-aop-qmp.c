/*
 * Explicit reference AOP QMP transport and QDSS clock-vote service.
 *
 * This is not AOP firmware, a physical frequency model, or a general JSON
 * command sink. Only the four documented clock-vote commands are consumed.
 * See tb371fc/docs/aop-reference-peer.md for sources and model conventions.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-aop-qmp.h"
#include "hw/misc/qcom-aop-msgram.h"
#include "hw/misc/qcom-ipcc.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/bswap.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/timer.h"

#define QMP_MAGIC       0x4d41494c
#define QMP_VERSION     1
#define QMP_UP          0x0000ffff
#define QMP_DOWN        0xffff0000
#define QMP_U_LINK      0x0c
#define QMP_U_LINK_ACK  0x10
#define QMP_U_CH        0x14
#define QMP_U_CH_ACK    0x18
#define QMP_U_SIZE      0x1c
#define QMP_U_OFFSET    0x20
#define QMP_M_LINK      0x24
#define QMP_M_LINK_ACK  0x28
#define QMP_M_CH        0x2c
#define QMP_M_CH_ACK    0x30
#define QMP_M_SIZE      0x34
#define QMP_M_OFFSET    0x38

/* Explicit reference geometry, not captured firmware-provided offsets. */
#define QMP_RX          0x100
#define QMP_TX          0x200
#define QMP_MBOX_SIZE   0x100

/* Deterministic virtual delays, not measured AOP processing latency. */
#define QMP_WORK_NS     1000
#define QMP_RETRY_NS    100000

enum {
    QMP_LINK_DOWN,
    QMP_LINK_UP,
    QMP_CHANNEL_OPENING,
    QMP_CONNECTED,
};

enum {
    QMP_ERR_NONE,
    QMP_ERR_DESCRIPTOR,
    QMP_ERR_LINK,
    QMP_ERR_CHANNEL,
    QMP_ERR_LENGTH,
    QMP_ERR_COMMAND,
};

struct QcomAopQmpState {
    DeviceState parent_obj;
    QcomAopMsgramState *msgram;
    DeviceState *ipcc;
    QEMUTimer *timer;
    uint8_t *ram;
    qemu_irq qdss_vote;
    qemu_irq qdss_ao_vote;

    uint32_t phase;
    uint32_t last_error;
    bool votes[2];
    bool work_pending;
    bool notify_pending;
    uint64_t accepted;
    uint64_t rejected;
    uint64_t notifications;
    bool ready;
};

static uint32_t aop_read(QcomAopQmpState *s, unsigned offset)
{
    return ldl_le_p(s->ram + offset);
}

static void aop_write(QcomAopQmpState *s, unsigned offset, uint32_t value)
{
    stl_le_p(s->ram + offset, value);
    memory_region_set_dirty(&s->msgram->sram, offset, sizeof(value));
}

static void aop_outputs(QcomAopQmpState *s)
{
    qemu_set_irq(s->qdss_vote, s->votes[0]);
    qemu_set_irq(s->qdss_ao_vote, s->votes[1]);
}

static void aop_reject(QcomAopQmpState *s, uint32_t error)
{
    s->last_error = error;
    s->rejected++;
    qemu_log_mask(LOG_UNIMP, TYPE_QCOM_AOP_QMP
                  ": request not consumed (reason=%u, phase=%u)\n",
                  error, s->phase);
}

static bool aop_descriptor_valid(QcomAopQmpState *s)
{
    return aop_read(s, 0) == QMP_MAGIC &&
           aop_read(s, 4) == QMP_VERSION && aop_read(s, 8) == 0 &&
           aop_read(s, QMP_U_LINK) == QMP_UP &&
           aop_read(s, QMP_U_SIZE) == QMP_MBOX_SIZE &&
           aop_read(s, QMP_U_OFFSET) == QMP_RX &&
           aop_read(s, QMP_M_SIZE) == QMP_MBOX_SIZE &&
           aop_read(s, QMP_M_OFFSET) == QMP_TX;
}

static void aop_channel_down(QcomAopQmpState *s)
{
    aop_write(s, QMP_U_CH, QMP_DOWN);
    aop_write(s, QMP_M_CH_ACK, QMP_DOWN);
    s->phase = QMP_LINK_UP;
    s->notify_pending = true;
}

/*
 * All peer state changes occur on an actual APSS -> AOP IPCC doorbell.
 * Internal phase prevents guest-written ACK fields from fabricating a link.
 * A close preserves accepted votes; only a supported off request or the
 * explicit reference reset changes an existing vote.
 */
static void aop_process(QcomAopQmpState *s)
{
    uint32_t link, channel, len, expected_channel;
    unsigned resource;
    bool vote;
    const char *command;
    static const char *const commands[2][2] = {
        { "{class: clock, res: qdss, val: 0}",
          "{class: clock, res: qdss, val: 1}" },
        { "{class: clock, res: qdss_ao, val: 0}",
          "{class: clock, res: qdss_ao, val: 1}" },
    };

    if (!aop_descriptor_valid(s)) {
        aop_reject(s, QMP_ERR_DESCRIPTOR);
        return;
    }
    expected_channel = s->phase >= QMP_CHANNEL_OPENING ? QMP_UP : QMP_DOWN;
    if (aop_read(s, QMP_U_CH) != expected_channel ||
        aop_read(s, QMP_M_LINK_ACK) !=
            (s->phase == QMP_LINK_DOWN ? QMP_DOWN : QMP_UP) ||
        aop_read(s, QMP_M_CH_ACK) !=
            (s->phase == QMP_CONNECTED ? QMP_UP : QMP_DOWN)) {
        aop_reject(s, QMP_ERR_DESCRIPTOR);
        return;
    }
    link = aop_read(s, QMP_M_LINK);
    channel = aop_read(s, QMP_M_CH);
    if (link == QMP_DOWN) {
        if (s->phase != QMP_LINK_DOWN) {
            aop_channel_down(s);
            aop_write(s, QMP_M_LINK_ACK, QMP_DOWN);
            s->phase = QMP_LINK_DOWN;
        }
        if (aop_read(s, QMP_TX)) {
            aop_reject(s, QMP_ERR_LINK);
        }
        return;
    }
    if (link != QMP_UP || aop_read(s, QMP_U_LINK_ACK) != QMP_UP) {
        aop_reject(s, QMP_ERR_LINK);
        return;
    }
    if (s->phase == QMP_LINK_DOWN) {
        aop_write(s, QMP_M_LINK_ACK, QMP_UP);
        s->phase = QMP_LINK_UP;
        s->notify_pending = true;
        return;
    }
    if (channel == QMP_DOWN) {
        if (s->phase >= QMP_CHANNEL_OPENING) {
            aop_channel_down(s);
        }
        if (aop_read(s, QMP_TX)) {
            aop_reject(s, QMP_ERR_CHANNEL);
        }
        return;
    }
    if (channel != QMP_UP) {
        aop_reject(s, QMP_ERR_CHANNEL);
        return;
    }
    if (s->phase == QMP_LINK_UP) {
        aop_write(s, QMP_U_CH, QMP_UP);
        s->phase = QMP_CHANNEL_OPENING;
        s->notify_pending = true;
        return;
    }
    if (aop_read(s, QMP_U_CH_ACK) != QMP_UP) {
        if (aop_read(s, QMP_TX)) {
            aop_reject(s, QMP_ERR_CHANNEL);
        }
        return;
    }
    if (s->phase == QMP_CHANNEL_OPENING) {
        aop_write(s, QMP_M_CH_ACK, QMP_UP);
        s->phase = QMP_CONNECTED;
        s->notify_pending = true;
        /* Connection completion does not also consume a premature message. */
        return;
    }

    len = aop_read(s, QMP_TX);
    if (!len) {
        return;
    }
    if ((len & 3) || len > QMP_MBOX_SIZE - sizeof(uint32_t) ||
        !memchr(s->ram + QMP_TX + 4, 0, len)) {
        aop_reject(s, QMP_ERR_LENGTH);
        return;
    }
    /*
     * Vendor clk-aop-qmp sends a 96-byte stack buffer. Bytes after its first
     * NUL are padding and need not be zero. Upstream qcom_aoss sends 64 bytes.
     */
    command = (const char *)s->ram + QMP_TX + 4;
    for (resource = 0; resource < ARRAY_SIZE(commands); resource++) {
        for (vote = false; ; vote = true) {
            if (!strcmp(command, commands[resource][vote])) {
                s->votes[resource] = vote;
                aop_outputs(s);
                s->accepted++;
                s->last_error = QMP_ERR_NONE;
                aop_write(s, QMP_TX, 0);
                s->notify_pending = true;
                return;
            }
            if (vote) {
                break;
            }
        }
    }
    aop_reject(s, QMP_ERR_COMMAND);
}

static void aop_timer(void *opaque)
{
    QcomAopQmpState *s = opaque;

    if (s->work_pending) {
        s->work_pending = false;
        aop_process(s);
    }
    if (s->notify_pending) {
        /*
         * Descriptor/message writes precede notification. A saturated IPCC
         * sparse table must not silently discard an already completed action.
         * Retain/retry the notification, without reprocessing the message.
         */
        if (qcom_ipcc_receive(s->ipcc, 0, 0)) {
            s->notify_pending = false;
            s->notifications++;
        } else {
            timer_mod_ns(s->timer,
                         qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + QMP_RETRY_NS);
        }
    }
}

static void aop_doorbell(void *opaque, uint16_t sender,
                         uint16_t recipient, uint16_t signal)
{
    QcomAopQmpState *s = opaque;
    int64_t deadline;

    if (sender != 8 || recipient != 0 || signal != 0) {
        return;
    }
    s->work_pending = true;
    deadline = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + QMP_WORK_NS;
    /* Repeated doorbells cannot postpone or replay the pending transaction. */
    if (!timer_pending(s->timer) ||
        (int64_t)timer_expire_time_ns(s->timer) > deadline) {
        timer_mod_ns(s->timer, deadline);
    }
}

static void aop_reset(DeviceState *dev)
{
    QcomAopQmpState *s = QCOM_AOP_QMP(dev);

    if (!s->ready) {
        return;
    }
    timer_del(s->timer);
    s->work_pending = s->notify_pending = false;
    s->phase = QMP_LINK_DOWN;
    s->last_error = QMP_ERR_NONE;
    s->votes[0] = s->votes[1] = false;
    s->accepted = s->rejected = s->notifications = 0;
    /*
     * Only the explicitly selected reference peer resets its descriptor and
     * mailboxes. The raw SRAM device, and its default reset retention, remain
     * unchanged when this peer is absent.
     */
    memset(s->ram, 0, 0x40);
    memset(s->ram + QMP_RX, 0, QMP_MBOX_SIZE);
    memset(s->ram + QMP_TX, 0, QMP_MBOX_SIZE);
    memory_region_set_dirty(&s->msgram->sram, 0, QCOM_AOP_MSGRAM_SIZE);
    aop_write(s, 0, QMP_MAGIC);
    aop_write(s, 4, QMP_VERSION);
    aop_write(s, QMP_U_LINK, QMP_UP);
    aop_write(s, QMP_U_LINK_ACK, QMP_DOWN);
    aop_write(s, QMP_U_CH, QMP_DOWN);
    aop_write(s, QMP_U_CH_ACK, QMP_DOWN);
    aop_write(s, QMP_U_SIZE, QMP_MBOX_SIZE);
    aop_write(s, QMP_U_OFFSET, QMP_RX);
    aop_write(s, QMP_M_LINK, QMP_DOWN);
    aop_write(s, QMP_M_LINK_ACK, QMP_DOWN);
    aop_write(s, QMP_M_CH, QMP_DOWN);
    aop_write(s, QMP_M_CH_ACK, QMP_DOWN);
    aop_write(s, QMP_M_SIZE, QMP_MBOX_SIZE);
    aop_write(s, QMP_M_OFFSET, QMP_TX);
    aop_outputs(s);
}

static int aop_pre_load(void *opaque)
{
    QcomAopQmpState *s = opaque;

    timer_del(s->timer);
    return 0;
}

static int aop_post_load(void *opaque, int version_id)
{
    QcomAopQmpState *s = opaque;

    if (s->phase > QMP_CONNECTED || s->last_error > QMP_ERR_COMMAND ||
        timer_pending(s->timer) != (s->work_pending || s->notify_pending)) {
        error_report(TYPE_QCOM_AOP_QMP ": invalid migrated peer state");
        return -EINVAL;
    }
    /*
     * RAM/IPCC may be loaded later. Never process RAM, send an interrupt, or
     * initialize a descriptor here. Virtual timers run after full VM restore.
     */
    aop_outputs(s);
    return 0;
}

static const VMStateDescription vmstate_aop = {
    .name = TYPE_QCOM_AOP_QMP,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_load = aop_pre_load,
    .post_load = aop_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(phase, QcomAopQmpState),
        VMSTATE_UINT32(last_error, QcomAopQmpState),
        VMSTATE_BOOL_ARRAY(votes, QcomAopQmpState, 2),
        VMSTATE_BOOL(work_pending, QcomAopQmpState),
        VMSTATE_BOOL(notify_pending, QcomAopQmpState),
        VMSTATE_UINT64(accepted, QcomAopQmpState),
        VMSTATE_UINT64(rejected, QcomAopQmpState),
        VMSTATE_UINT64(notifications, QcomAopQmpState),
        VMSTATE_TIMER_PTR(timer, QcomAopQmpState),
        VMSTATE_END_OF_LIST()
    },
};

static bool aop_get_qdss(Object *obj, Error **errp)
{
    return QCOM_AOP_QMP(obj)->votes[0];
}

static bool aop_get_qdss_ao(Object *obj, Error **errp)
{
    return QCOM_AOP_QMP(obj)->votes[1];
}

static bool aop_get_work_pending(Object *obj, Error **errp)
{
    return QCOM_AOP_QMP(obj)->work_pending;
}

static bool aop_get_notify_pending(Object *obj, Error **errp)
{
    return QCOM_AOP_QMP(obj)->notify_pending;
}

static char *aop_get_state(Object *obj, Error **errp)
{
    static const char *const names[] = {
        "link-down", "link-up", "channel-opening", "connected"
    };
    return g_strdup(names[QCOM_AOP_QMP(obj)->phase]);
}

static char *aop_get_error(Object *obj, Error **errp)
{
    static const char *const names[] = {
        "none", "descriptor", "link", "channel", "length", "command"
    };
    return g_strdup(names[QCOM_AOP_QMP(obj)->last_error]);
}

static void aop_get_count(Object *obj, Visitor *visitor, const char *name,
                           void *opaque, Error **errp)
{
    QcomAopQmpState *s = QCOM_AOP_QMP(obj);
    uint64_t count = !strcmp(name, "accepted-messages") ? s->accepted :
                     !strcmp(name, "rejected-requests") ? s->rejected :
                     s->notifications;
    visit_type_uint64(visitor, name, &count, errp);
}

static void aop_realize(DeviceState *dev, Error **errp)
{
    QcomAopQmpState *s = QCOM_AOP_QMP(dev);

    if (!s->msgram || !s->ipcc ||
        !qdev_is_realized(DEVICE(s->msgram)) || !qdev_is_realized(s->ipcc)) {
        error_setg(errp, TYPE_QCOM_AOP_QMP
                   " requires realized message SRAM and IPCC links");
        return;
    }
    s->ram = memory_region_get_ram_ptr(&s->msgram->sram);
    s->ready = true;
    aop_reset(dev);
    qcom_ipcc_set_peer_send(s->ipcc, aop_doorbell, s);
}

static const Property aop_properties[] = {
    DEFINE_PROP_LINK("msgram", QcomAopQmpState, msgram,
                     TYPE_QCOM_AOP_MSGRAM, QcomAopMsgramState *),
    DEFINE_PROP_LINK("ipcc", QcomAopQmpState, ipcc,
                     TYPE_QCOM_IPCC, DeviceState *),
};

static void aop_init(Object *obj)
{
    QcomAopQmpState *s = QCOM_AOP_QMP(obj);

    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, aop_timer, s);
    qdev_init_gpio_out_named(DEVICE(s), &s->qdss_vote, "qdss-vote", 1);
    qdev_init_gpio_out_named(DEVICE(s), &s->qdss_ao_vote, "qdss-ao-vote", 1);
    object_property_add_bool(obj, "qdss-vote", aop_get_qdss, NULL);
    object_property_add_bool(obj, "qdss-ao-vote", aop_get_qdss_ao, NULL);
    object_property_add_bool(obj, "work-pending", aop_get_work_pending, NULL);
    object_property_add_bool(obj, "notification-pending", aop_get_notify_pending,
                              NULL);
    object_property_add_str(obj, "state", aop_get_state, NULL);
    object_property_add_str(obj, "last-error", aop_get_error, NULL);
    object_property_add(obj, "accepted-messages", "uint64", aop_get_count,
                         NULL, NULL, NULL);
    object_property_add(obj, "rejected-requests", "uint64", aop_get_count,
                         NULL, NULL, NULL);
    object_property_add(obj, "notifications", "uint64", aop_get_count,
                         NULL, NULL, NULL);
}

static void aop_finalize(Object *obj)
{
    timer_free(QCOM_AOP_QMP(obj)->timer);
}

static void aop_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "Explicit AOP QMP reference peer for QDSS off/dynamic votes";
    dc->realize = aop_realize;
    dc->vmsd = &vmstate_aop;
    dc->user_creatable = false;
    device_class_set_props(dc, aop_properties);
    device_class_set_legacy_reset(dc, aop_reset);
}

static const TypeInfo aop_info = {
    .name = TYPE_QCOM_AOP_QMP,
    .parent = TYPE_DEVICE,
    .instance_size = sizeof(QcomAopQmpState),
    .instance_init = aop_init,
    .instance_finalize = aop_finalize,
    .class_init = aop_class_init,
};

static void aop_register_types(void)
{
    type_register_static(&aop_info);
}
type_init(aop_register_types)
