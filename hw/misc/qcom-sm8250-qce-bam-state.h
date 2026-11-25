/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Pure cold-control state transitions shared by the QEMU device and its
 * local preparation checks. This file has no guest-memory, clock, IRQ,
 * asynchronous or QEMU interfaces. No descriptor is consumed here.
 *
 * Register offsets/masks follow the pinned vendor SPS BAM 4K table. The
 * stricter supported subset is a reference-model boundary, not a claim that
 * silicon rejects every omitted feature or has the chosen reset values.
 */
#ifndef QCOM_SM8250_QCE_BAM_STATE_H
#define QCOM_SM8250_QCE_BAM_STATE_H

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#define QCB_FIRST_PIPE 4
#define QCB_PIPE_COUNT 4
#define QCB_PIPE_STRIDE UINT32_C(0x1000)
#define QCB_PIPE_BASE UINT32_C(0x13000)
#define QCB_EE_PIPE_MASK UINT32_C(0xf0)

#define QCB_P_CTRL UINT32_C(0x000)
#define QCB_P_RST UINT32_C(0x004)
#define QCB_P_IRQ_STTS UINT32_C(0x010)
#define QCB_P_IRQ_CLR UINT32_C(0x014)
#define QCB_P_IRQ_EN UINT32_C(0x018)
#define QCB_P_SW_OFSTS UINT32_C(0x800)
#define QCB_P_EVNT_REG UINT32_C(0x818)
#define QCB_P_DESC_LO UINT32_C(0x81c)
#define QCB_P_FIFO_SIZE UINT32_C(0x820)
#define QCB_P_THRESHOLD UINT32_C(0x828)
#define QCB_P_DESC_HI UINT32_C(0x914)

#define QCB_CTRL_ENABLE UINT32_C(0x2)
#define QCB_CTRL_DIRECTION UINT32_C(0x8)
#define QCB_CTRL_STREAM UINT32_C(0x10)
#define QCB_CTRL_SYSTEM UINT32_C(0x20)
#define QCB_CTRL_GROUP UINT32_C(0x1f0000)
#define QCB_CTRL_MASK UINT32_C(0x1f003a)
#define QCB_IRQ_MASK UINT32_C(0xff)

typedef struct QceBamPipe {
    uint32_t control;
    uint32_t reset;
    uint32_t irq_enable;
    uint32_t irq_status;
    uint32_t descriptor_lo;
    uint32_t descriptor_hi;
    uint32_t fifo_size;
    uint32_t threshold;
} QceBamPipe;

typedef enum QceBamAccess {
    QCB_ACCESS_OK,
    QCB_ACCESS_READ_ONLY,
    QCB_ACCESS_WRITE_ONLY,
    QCB_ACCESS_UNSUPPORTED,
    QCB_ACCESS_INVALID_STATE,
    QCB_ACCESS_HELD_RESET,
} QceBamAccess;

static inline bool qcb_pipe_zero_except_reset(const QceBamPipe *p)
{
    return p->control == 0 && p->irq_enable == 0 && p->irq_status == 0 &&
           p->descriptor_lo == 0 && p->descriptor_hi == 0 &&
           p->fifo_size == 0 && p->threshold == 0;
}

static inline bool qcb_pipe_valid(const QceBamPipe *p)
{
    if ((p->control & ~QCB_CTRL_MASK) || (p->control & QCB_CTRL_STREAM) ||
        p->reset > 1 || (p->irq_enable & ~QCB_IRQ_MASK) ||
        p->irq_status != 0 || (p->descriptor_lo & 7) ||
        (p->descriptor_hi & ~UINT32_C(0xf)) ||
        (p->fifo_size & ~UINT32_C(0xffff)) || (p->fifo_size & 7) ||
        (p->threshold & ~UINT32_C(0xffff))) {
        return false;
    }
    if (p->reset && !qcb_pipe_zero_except_reset(p)) {
        return false;
    }
    /* No accepted submit can leave pending events in this model. */
    if (p->control & QCB_CTRL_ENABLE) {
        return !p->reset && (p->control & QCB_CTRL_SYSTEM) &&
               p->fifo_size >= 16;
    }
    return true;
}

static inline QceBamAccess qcb_pipe_read(const QceBamPipe *p,
                                        uint32_t offset, uint32_t *value)
{
    *value = 0;
    switch (offset) {
    case QCB_P_CTRL: *value = p->control; break;
    case QCB_P_RST: *value = p->reset; break;
    case QCB_P_IRQ_STTS: *value = p->irq_status; break;
    case QCB_P_IRQ_EN: *value = p->irq_enable; break;
    case QCB_P_DESC_LO: *value = p->descriptor_lo; break;
    case QCB_P_DESC_HI: *value = p->descriptor_hi; break;
    case QCB_P_FIFO_SIZE: *value = p->fifo_size; break;
    case QCB_P_THRESHOLD: *value = p->threshold; break;
    case QCB_P_SW_OFSTS:
        /* Reset zero cannot advance: every submission is rejected. */
        break;
    case QCB_P_IRQ_CLR:
        return QCB_ACCESS_WRITE_ONLY;
    default:
        return QCB_ACCESS_UNSUPPORTED;
    }
    return QCB_ACCESS_OK;
}

static inline QceBamAccess qcb_pipe_write(QceBamPipe *p,
                                         uint32_t offset, uint32_t value)
{
    QceBamPipe next = *p;

    if (offset == QCB_P_RST) {
        if (value > 1) {
            return QCB_ACCESS_INVALID_STATE;
        }
        if (value) {
            memset(&next, 0, sizeof(next));
        }
        next.reset = value;
    } else {
        if (offset == QCB_P_IRQ_STTS || offset == QCB_P_SW_OFSTS) {
            return QCB_ACCESS_READ_ONLY;
        }
        /* Includes every P_EVNT_REG write, even a zero doorbell. */
        switch (offset) {
        case QCB_P_CTRL:
        case QCB_P_IRQ_EN:
        case QCB_P_IRQ_CLR:
        case QCB_P_DESC_LO:
        case QCB_P_DESC_HI:
        case QCB_P_FIFO_SIZE:
        case QCB_P_THRESHOLD:
            break;
        default:
            return QCB_ACCESS_UNSUPPORTED;
        }
        if (p->reset) {
            return QCB_ACCESS_HELD_RESET;
        }
        switch (offset) {
        case QCB_P_CTRL: next.control = value; break;
        case QCB_P_IRQ_EN: next.irq_enable = value; break;
        case QCB_P_IRQ_CLR:
            if (value & ~QCB_IRQ_MASK) {
                return QCB_ACCESS_INVALID_STATE;
            }
            next.irq_status &= ~value;
            break;
        case QCB_P_DESC_LO: next.descriptor_lo = value; break;
        case QCB_P_DESC_HI: next.descriptor_hi = value; break;
        case QCB_P_FIFO_SIZE: next.fifo_size = value; break;
        case QCB_P_THRESHOLD: next.threshold = value; break;
        default:
            return QCB_ACCESS_UNSUPPORTED;
        }
    }
    if (!qcb_pipe_valid(&next)) {
        return QCB_ACCESS_INVALID_STATE;
    }
    *p = next;
    return QCB_ACCESS_OK;
}

static inline bool qcb_ee_mask_valid(uint32_t value, uint32_t initial)
{
    return (value & ~QCB_EE_PIPE_MASK) == (initial & ~QCB_EE_PIPE_MASK);
}

#endif
