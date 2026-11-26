/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * SM8250 GENI I2C empty-bus reference controller.
 * This interface describes a virtual policy, not captured firmware or slaves.
 */
#ifndef HW_MISC_QCOM_SM8250_GENI_I2C_H
#define HW_MISC_QCOM_SM8250_GENI_I2C_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_QCOM_SM8250_GENI_I2C "qcom-sm8250-geni-i2c"
OBJECT_DECLARE_SIMPLE_TYPE(QcomGeniI2CState, QCOM_SM8250_GENI_I2C)

#define QCOM_GENI_I2C_WINDOW_BYTES UINT32_C(0x4000)
#define QCOM_GENI_I2C_INSTANCE_COUNT 9
#define QCOM_GENI_I2C_FIFO_WORDS 16
#define QCOM_GENI_I2C_FIFO_BITS 32
#define QCOM_GENI_I2C_REGISTER_COUNT 37
#define QCOM_GENI_I2C_PROFILE_BYTES 64
#define QCOM_GENI_I2C_VMSTATE_BYTES 305
#define QCOM_GENI_I2C_PROFILE_ID UINT32_C(0x47493231)
#define QCOM_GENI_I2C_FW_REVISION UINT32_C(0x00000300)
#define QCOM_GENI_I2C_HW_PARAM UINT32_C(0x20100000)
#define QCOM_GENI_I2C_IOS_IDLE UINT32_C(3)

/*
 * Only original-DT enabled instances are in the frozen table. Clock indices
 * are the current QEMU GCC table indices, not the vendor DT binding numbers.
 * The shared reset indices come from the public GCC provider, not from a
 * nonexistent resets property on the original I2C nodes.
 */
typedef struct QcomGeniI2CInstance {
    uint32_t id;
    hwaddr base;
    uint32_t spi;
    uint32_t wrapper;
    const char *se_clock;
    const char *m_ahb_clock;
    const char *s_ahb_clock;
    unsigned se_clock_index;
    unsigned m_ahb_clock_index;
    unsigned s_ahb_clock_index;
    unsigned reset_index;
} QcomGeniI2CInstance;

unsigned qcom_geni_i2c_instance_count(void);
const QcomGeniI2CInstance *qcom_geni_i2c_instance_at(unsigned index);
const QcomGeniI2CInstance *qcom_geni_i2c_instance_by_id(uint32_t id);

#endif
