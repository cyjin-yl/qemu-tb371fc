/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_DSI_PLL7NM_H
#define HW_MISC_QCOM_SM8250_DSI_PLL7NM_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_DSI_PLL7NM "qcom-sm8250-dsi-pll7nm"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250DsiPll7nmState, QCOM_SM8250_DSI_PLL7NM)

#define DSI_PLL7NM_BANKS 2
#define DSI_PLL7NM_REGISTERS_PER_BANK 154
#define DSI_PLL7NM_REGISTERS (DSI_PLL7NM_BANKS * DSI_PLL7NM_REGISTERS_PER_BANK)
#define DSI_PLL7NM_WINDOWS_PER_BANK 8
#define DSI_PLL7NM_CLOCKS_PER_BANK 8
#define DSI_PLL7NM_CLOCKS 16
#define DSI_PLL7NM_INPUTS 1
#define DSI_PLL7NM_PHY0_BASE UINT64_C(0x0ae94400)
#define DSI_PLL7NM_BANK_STRIDE UINT64_C(0x2000)
#define DSI_PLL7NM_REFERENCE_IDENTITY 0x44534941U
#define DSI_PLL7NM_LOCK_NS 5000

typedef struct DsiPll7nmWindow {
    QcomSm8250DsiPll7nmState *state;
    unsigned bank;
    uint32_t offset;
    bool dynamic;
} DsiPll7nmWindow;

struct QcomSm8250DsiPll7nmState {
    SysBusDevice parent_obj;
    MemoryRegion phy[DSI_PLL7NM_BANKS];
    MemoryRegion dynamic[DSI_PLL7NM_BANKS];
    MemoryRegion iomem[DSI_PLL7NM_BANKS][DSI_PLL7NM_WINDOWS_PER_BANK];
    DsiPll7nmWindow windows[DSI_PLL7NM_BANKS][DSI_PLL7NM_WINDOWS_PER_BANK];
    DsiPll7nmWindow dynamic_windows[DSI_PLL7NM_BANKS];
    Clock *input[DSI_PLL7NM_INPUTS];
    Clock *clocks[DSI_PLL7NM_CLOCKS];
    DeviceState *mdss_provider;
    QEMUTimer *timer;

    bool enabled_reference, ready, updating;
    uint32_t reference_profile, reference_identity;
    uint32_t bank_count, registers_per_bank, register_count;
    uint32_t clock_count, input_count, window_count, mapped_bytes;
    uint64_t physical_base[DSI_PLL7NM_BANKS];
    uint64_t dynamic_base[DSI_PLL7NM_BANKS];
    uint32_t regs[DSI_PLL7NM_REGISTERS];
    uint32_t active_factor[DSI_PLL7NM_BANKS];
    uint32_t pending_factor[DSI_PLL7NM_BANKS];
    int64_t lock_deadline[DSI_PLL7NM_BANKS];
    uint64_t last_xo_hz;
    bool locked[DSI_PLL7NM_BANKS];
    bool power_known, power_on;
    bool refgen_known[DSI_PLL7NM_BANKS];
    bool refgen_ready[DSI_PLL7NM_BANKS];
};

#endif
