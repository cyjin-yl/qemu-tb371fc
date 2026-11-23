/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 * Qualcomm IPCC application-processor frame.
 */
#ifndef HW_MISC_QCOM_IPCC_H
#define HW_MISC_QCOM_IPCC_H

#include "hw/core/sysbus.h"

#define TYPE_QCOM_IPCC "qcom-ipcc"
OBJECT_DECLARE_SIMPLE_TYPE(QcomIpccState, QCOM_IPCC)

typedef void (*QcomIpccPeerSend)(void *opaque, uint16_t sender,
                                uint16_t recipient, uint16_t signal);

/* Called by a modeled peer in the BQL context; no firmware reply is implied. */
bool qcom_ipcc_receive(DeviceState *dev, uint16_t sender, uint16_t signal);
void qcom_ipcc_set_peer_send(DeviceState *dev, QcomIpccPeerSend send,
                             void *opaque);

#endif
