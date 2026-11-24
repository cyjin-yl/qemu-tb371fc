/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_DSI_PLL_H
#define HW_MISC_QCOM_SM8250_DSI_PLL_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_DSI_PLL "qcom-sm8250-dsi-pll"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250DsiPllState, QCOM_SM8250_DSI_PLL)

#define DSI_PLL_BANKS 2
#define DSI_PLL_WORDS_PER_BANK 154
#define DSI_PLL_REGISTERS (DSI_PLL_BANKS * DSI_PLL_WORDS_PER_BANK)
#define DSI_PLL_WINDOWS_PER_BANK 8
#define DSI_PLL_WINDOWS (DSI_PLL_BANKS * DSI_PLL_WINDOWS_PER_BANK)
#define DSI_PLL_CLOCKS_PER_BANK 26
#define DSI_PLL_CLOCKS (DSI_PLL_BANKS * DSI_PLL_CLOCKS_PER_BANK)
#define DSI_PLL_INPUTS 1
#define DSI_PLL_MAPPED_BYTES (DSI_PLL_REGISTERS * 4)
#define DSI_PLL_BANK_SIZE 0x800
#define DSI_PLL_BANK_STRIDE UINT64_C(0x2000)
#define DSI_PLL_PHYSICAL_BASE UINT64_C(0x0ae94400)
#define DSI_PLL_REFERENCE_IDENTITY 0x44534934U
#define DSI_PLL_SOURCE_VARIANT 0x00000401U
#define DSI_PLL_LOCK_REFERENCE_CYCLES 64
#define DSI_PLL_BIAS_SETTLE_NS 250

typedef struct QcomDsiPllWindowDef {
    uint32_t offset, size;
} QcomDsiPllWindowDef;

extern const QcomDsiPllWindowDef
    dsi_pll_windows[DSI_PLL_WINDOWS_PER_BANK];

typedef struct QcomDsiPllWindow {
    QcomSm8250DsiPllState *state;
    unsigned bank;
    uint32_t offset;
} QcomDsiPllWindow;

struct QcomSm8250DsiPllState {
    SysBusDevice parent_obj;
    MemoryRegion container[DSI_PLL_BANKS];
    MemoryRegion iomem[DSI_PLL_WINDOWS];
    QcomDsiPllWindow windows[DSI_PLL_WINDOWS];
    Clock *input[DSI_PLL_INPUTS];
    Clock *clocks[DSI_PLL_CLOCKS];
    QEMUTimer *timer;

    bool enabled_reference;
    bool ready, publishing;
    uint32_t reference_profile, reference_identity, source_variant;
    uint32_t bank_count, words_per_bank, register_count, clock_count;
    uint32_t input_count, window_count, mapped_bytes;
    uint32_t lock_reference_cycles, bias_settle_ns;
    uint64_t physical_base, bank_stride;

    uint32_t regs[DSI_PLL_REGISTERS];
    uint8_t written[DSI_PLL_REGISTERS];
    uint32_t captured[DSI_PLL_REGISTERS];
    uint64_t captured_xo_period[DSI_PLL_BANKS];
    uint64_t last_xo_period;
    int64_t bias_deadline[DSI_PLL_BANKS];
    int64_t lock_deadline[DSI_PLL_BANKS];
    int64_t lock_qualify_after[DSI_PLL_BANKS];
    uint8_t pll_locked[DSI_PLL_BANKS];
    uint8_t bias_settled[DSI_PLL_BANKS];
    uint8_t reset_asserted[DSI_PLL_BANKS];
    uint8_t digital_ready[DSI_PLL_BANKS];
    uint8_t power_known, power_on;
};

#endif
