/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm IPCC APPS frame, explicit interrupt-message transport.
 * Sources and limits: tb371fc/docs/ipcc.md.
 *
 * Receive bits are actual pending latches gated by independent masks.
 * Sending to an absent peer never creates a receive event or firmware reply.
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-ipcc.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "hw/core/resettable.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "system/qtest.h"

#define IPCC_CONFIG             0x08
#define IPCC_SEND_ID            0x0c
#define IPCC_RECV_ID            0x10
#define IPCC_RECV_ENABLE        0x14
#define IPCC_RECV_DISABLE       0x18
#define IPCC_RECV_CLEAR         0x1c
#define IPCC_CLIENT_CLEAR       0x38
#define IPCC_CLEAR_ON_READ      BIT(0)
#define IPCC_NO_PENDING         UINT32_MAX
/*
 * Sparse transport capacity, not a measured hardware dimension. Keeping a
 * bound makes hostile guest streams/migrations consume fixed host memory.
 */
#define IPCC_MAX_PAIRS          4096

typedef struct QcomIpccPair {
    uint32_t id;
    bool used;
    bool enabled;
    bool pending;
} QcomIpccPair;

struct QcomIpccState {
    SysBusDevice parent_obj;
    MemoryRegion mmio;
    qemu_irq irq;
    uint32_t local_client_id;
    uint32_t config;
    QcomIpccPair pairs[IPCC_MAX_PAIRS];
    uint32_t last_tx;
    uint64_t tx_count;
    QcomIpccPeerSend peer_send;
    void *peer_opaque;
};

static QcomIpccPair *ipcc_find(QcomIpccState *s, uint32_t id, bool create)
{
    QcomIpccPair *empty = NULL;

    if (id == IPCC_NO_PENDING) {
        return NULL;
    }
    for (unsigned i = 0; i < IPCC_MAX_PAIRS; i++) {
        QcomIpccPair *p = &s->pairs[i];

        if (p->used && p->id == id) {
            return p;
        }
        if (!p->used && !empty) {
            empty = p;
        }
    }
    if (create && empty) {
        empty->id = id;
        empty->used = true;
        return empty;
    }
    return NULL;
}

static void ipcc_release_empty(QcomIpccPair *p)
{
    if (p && !p->enabled && !p->pending) {
        memset(p, 0, sizeof(*p));
    }
}

static QcomIpccPair *ipcc_selected(QcomIpccState *s)
{
    QcomIpccPair *selected = NULL;

    /*
     * Deterministic reference arbitration. The source drivers establish
     * pending selection, not the silicon's tie-break order.
     */
    for (unsigned i = 0; i < IPCC_MAX_PAIRS; i++) {
        QcomIpccPair *p = &s->pairs[i];

        if (p->used && p->enabled && p->pending &&
            (!selected || p->id < selected->id)) {
            selected = p;
        }
    }
    return selected;
}

static void ipcc_update_irq(QcomIpccState *s)
{
    qemu_set_irq(s->irq, ipcc_selected(s) != NULL);
}

bool qcom_ipcc_receive(DeviceState *dev, uint16_t sender, uint16_t signal)
{
    QcomIpccState *s = QCOM_IPCC(dev);
    uint32_t id = ((uint32_t)sender << 16) | signal;
    QcomIpccPair *p = ipcc_find(s, id, true);

    if (!p) {
        qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_IPCC
                      ": receive ID %08x invalid or sparse table full\n", id);
        return false;
    }
    p->pending = true;
    ipcc_update_irq(s);
    return true;
}

void qcom_ipcc_set_peer_send(DeviceState *dev, QcomIpccPeerSend send,
                             void *opaque)
{
    QcomIpccState *s = QCOM_IPCC(dev);

    s->peer_send = send;
    s->peer_opaque = opaque;
}

static void ipcc_clear(QcomIpccState *s)
{
    memset(s->pairs, 0, sizeof(s->pairs));
    s->config = 0;
    s->last_tx = IPCC_NO_PENDING;
    s->tx_count = 0;
    ipcc_update_irq(s);
}

static MemTxResult ipcc_read(void *opaque, hwaddr address, uint64_t *data,
                             unsigned size, MemTxAttrs attrs)
{
    QcomIpccState *s = opaque;
    QcomIpccPair *p;

    switch (address) {
    case IPCC_CONFIG:
        *data = s->config;
        return MEMTX_OK;
    case IPCC_RECV_ID:
        p = ipcc_selected(s);
        *data = p ? p->id : IPCC_NO_PENDING;
        if (p && (s->config & IPCC_CLEAR_ON_READ)) {
            p->pending = false;
            ipcc_release_empty(p);
            ipcc_update_irq(s);
        }
        return MEMTX_OK;
    default:
        *data = 0;
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_IPCC
                      ": unsupported read at 0x%" HWADDR_PRIx "\n", address);
        return MEMTX_ERROR;
    }
}

static MemTxResult ipcc_write(void *opaque, hwaddr address, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    QcomIpccState *s = opaque;
    QcomIpccPair *p;
    uint32_t id = value;
    uint16_t recipient = id >> 16;
    uint16_t signal = id;

    switch (address) {
    case IPCC_CONFIG:
        if (id & ~IPCC_CLEAR_ON_READ) {
            break;
        }
        s->config = id;
        return MEMTX_OK;
    case IPCC_CLIENT_CLEAR:
        if (id == 1) {
            ipcc_clear(s);
        } else if (id != 0) {
            break;
        }
        return MEMTX_OK;
    case IPCC_SEND_ID:
        if (id == IPCC_NO_PENDING) {
            break;
        }
        if (recipient == s->local_client_id &&
            !qcom_ipcc_receive(DEVICE(s), s->local_client_id, signal)) {
            return MEMTX_ERROR;
        }
        s->last_tx = id;
        s->tx_count++;
        if (recipient != s->local_client_id) {
            if (s->peer_send) {
                s->peer_send(s->peer_opaque, s->local_client_id,
                             recipient, signal);
            } else {
                qemu_log_mask(LOG_UNIMP, TYPE_QCOM_IPCC
                              ": transmit to unmodeled client %u signal %u; "
                              "no receive/reply fabricated\n", recipient, signal);
            }
        }
        return MEMTX_OK;
    case IPCC_RECV_ENABLE:
        p = ipcc_find(s, id, true);
        if (!p) {
            break;
        }
        p->enabled = true;
        ipcc_update_irq(s);
        return MEMTX_OK;
    case IPCC_RECV_DISABLE:
    case IPCC_RECV_CLEAR:
        if (id == IPCC_NO_PENDING) {
            break;
        }
        p = ipcc_find(s, id, false);
        if (p) {
            if (address == IPCC_RECV_DISABLE) {
                p->enabled = false;
            } else {
                p->pending = false;
            }
            ipcc_release_empty(p);
            ipcc_update_irq(s);
        }
        return MEMTX_OK;
    default:
        qemu_log_mask(LOG_UNIMP, TYPE_QCOM_IPCC
                      ": unsupported write at 0x%" HWADDR_PRIx "\n", address);
        return MEMTX_ERROR;
    }
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_IPCC
                  ": rejected write %08x at 0x%" HWADDR_PRIx "\n", id, address);
    return MEMTX_ERROR;
}

static const MemoryRegionOps ipcc_ops = {
    .read_with_attrs = ipcc_read,
    .write_with_attrs = ipcc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static void ipcc_reset(Object *obj, ResetType type)
{
    ipcc_clear(QCOM_IPCC(obj));
}

static int ipcc_post_load(void *opaque, int version)
{
    QcomIpccState *s = opaque;
    g_autoptr(GHashTable) ids = g_hash_table_new(g_direct_hash, g_direct_equal);

    if (s->config & ~IPCC_CLEAR_ON_READ) {
        return -EINVAL;
    }
    for (unsigned i = 0; i < IPCC_MAX_PAIRS; i++) {
        QcomIpccPair *p = &s->pairs[i];

        if (!p->used) {
            if (p->id || p->enabled || p->pending) {
                return -EINVAL;
            }
            continue;
        }
        if (p->id == IPCC_NO_PENDING || (!p->enabled && !p->pending) ||
            g_hash_table_contains(ids, GUINT_TO_POINTER(p->id))) {
            return -EINVAL;
        }
        g_hash_table_add(ids, GUINT_TO_POINTER(p->id));
    }
    ipcc_update_irq(s);
    return 0;
}

static const VMStateDescription vmstate_ipcc_pair = {
    .name = TYPE_QCOM_IPCC "/pair",
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(id, QcomIpccPair),
        VMSTATE_BOOL(used, QcomIpccPair),
        VMSTATE_BOOL(enabled, QcomIpccPair),
        VMSTATE_BOOL(pending, QcomIpccPair),
        VMSTATE_END_OF_LIST()
    },
};

static const VMStateDescription vmstate_ipcc = {
    .name = TYPE_QCOM_IPCC,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = ipcc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(local_client_id, QcomIpccState),
        VMSTATE_UINT32(config, QcomIpccState),
        VMSTATE_UINT32(last_tx, QcomIpccState),
        VMSTATE_UINT64(tx_count, QcomIpccState),
        VMSTATE_STRUCT_ARRAY(pairs, QcomIpccState, IPCC_MAX_PAIRS, 1,
                             vmstate_ipcc_pair, QcomIpccPair),
        VMSTATE_END_OF_LIST()
    },
};

static void ipcc_qtest_receive(Object *obj, Visitor *visitor, const char *name,
                                void *opaque, Error **errp)
{
    uint32_t id;

    if (!visit_type_uint32(visitor, name, &id, errp)) {
        return;
    }
    if (!qcom_ipcc_receive(DEVICE(obj), id >> 16, id)) {
        error_setg(errp, "IPCC receive ID is invalid or sparse capacity exhausted");
    }
}

static const Property ipcc_properties[] = {
    DEFINE_PROP_UINT32("local-client-id", QcomIpccState, local_client_id, 8),
};

static void ipcc_realize(DeviceState *dev, Error **errp)
{
    QcomIpccState *s = QCOM_IPCC(dev);

    if (s->local_client_id > UINT16_MAX) {
        error_setg(errp, TYPE_QCOM_IPCC " local-client-id must fit 16 bits");
        return;
    }
    if (qtest_enabled()) {
        /*
         * Test-only peer event input invokes the same receiver API as a
         * modeled coprocessor. It is absent from normal guest execution.
         */
        object_property_add(OBJECT(s), "qtest-rx-message", "uint32",
                             NULL, ipcc_qtest_receive, NULL, NULL);
    }
}

static void ipcc_init(Object *obj)
{
    QcomIpccState *s = QCOM_IPCC(obj);

    memory_region_init_io(&s->mmio, obj, &ipcc_ops, s,
                          TYPE_QCOM_IPCC, 0x1000);
    sysbus_init_mmio(SYS_BUS_DEVICE(obj), &s->mmio);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    object_property_add_uint32_ptr(obj, "last-transmit", &s->last_tx,
                                   OBJ_PROP_FLAG_READ);
    object_property_add_uint64_ptr(obj, "transmit-count", &s->tx_count,
                                   OBJ_PROP_FLAG_READ);
}

static void ipcc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    dc->desc = "Qualcomm IPCC APPS interrupt-message frame";
    dc->realize = ipcc_realize;
    dc->vmsd = &vmstate_ipcc;
    device_class_set_props(dc, ipcc_properties);
    rc->phases.hold = ipcc_reset;
}

static const TypeInfo ipcc_type = {
    .name = TYPE_QCOM_IPCC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomIpccState),
    .instance_init = ipcc_init,
    .class_init = ipcc_class_init,
};

static void ipcc_register_types(void)
{
    type_register_static(&ipcc_type);
}

type_init(ipcc_register_types)
