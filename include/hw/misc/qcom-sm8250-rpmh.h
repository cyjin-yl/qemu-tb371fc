/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HW_MISC_QCOM_SM8250_RPMH_H
#define HW_MISC_QCOM_SM8250_RPMH_H

#include "hw/core/sysbus.h"
#include "hw/core/clock.h"
#include "qemu/timer.h"

#define TYPE_QCOM_SM8250_RPMH "qcom-sm8250-rpmh"
OBJECT_DECLARE_SIMPLE_TYPE(QcomSm8250RpmhState, QCOM_SM8250_RPMH)

#define RPMH_DRVS 3 /* APPS controller windows; preserved board API. */
#define RPMH_VOTERS 4
#define RPMH_DISPLAY_DRV 3
#define RPMH_DISPLAY_MMIO 4
#define RPMH_TCS_PER_DRV 8
#define RPMH_COMMANDS 16
#define RPMH_RESOURCES 98
#define RPMH_DRV_SIZE 0x10000
#define RPMH_CMD_DB_SIZE 0x20000

/*
 * MMIO 0..2: APPS DRV0..2, 3: command-db, 4: display DRV0.
 * IRQ and sleep GPIO indices 0..2 are APPS, index 3 is display.
 * All four voter slots share one typed resource backend.
 */
typedef struct QcomRpmhCommand {
    uint32_t msgid;
    uint32_t address;
    uint32_t data;
    uint32_t status;
    uint32_t response;
} QcomRpmhCommand;

typedef struct QcomRpmhTcs {
    uint32_t wait;
    uint32_t control;
    uint32_t enable;
    QcomRpmhCommand commands[RPMH_COMMANDS];
    int32_t cursor;
    int64_t deadline;
    bool running;
    bool stalled;
} QcomRpmhTcs;

typedef struct QcomRpmhDrv {
    QcomSm8250RpmhState *owner;
    MemoryRegion iomem;
    qemu_irq irq;
    uint32_t irq_enable;
    uint32_t irq_status;
    uint32_t wake_hi;
    uint32_t wake_lo;
    bool sleeping;
    QcomRpmhTcs tcs[RPMH_TCS_PER_DRV];
} QcomRpmhDrv;

typedef struct QcomRpmhResource {
    /* Parsed configuration, never inferred from arbitrary command addresses. */
    uint32_t address;
    uint32_t unit;
    uint16_t width;
    uint16_t levels[16];
    uint8_t level_count;
    uint8_t vcd;
    bool present;
    /* Per-DRV requests and BCM staging; only these fields migrate. */
    uint32_t votes[RPMH_VOTERS][4];
    uint32_t staged[RPMH_VOTERS];
    bool dirty[RPMH_VOTERS];
} QcomRpmhResource;

struct QcomSm8250RpmhState {
    SysBusDevice parent_obj;
    QcomRpmhDrv drv[RPMH_VOTERS];
    QcomRpmhResource resources[RPMH_RESOURCES];
    MemoryRegion command_db_region;
    uint8_t command_db[RPMH_CMD_DB_SIZE];
    uint8_t digest[32];
    uint8_t expected_digest[32];
    Clock *xo_board;
    Clock *clocks[RPMH_RESOURCES];
    qemu_irq power[RPMH_RESOURCES];
    QEMUTimer *timer;
    char *cmd_db_file;
    char *cmd_db_sha256;
    uint64_t command_latency_ns;
    bool reference_cmd_db;
    bool boot_configured;
    bool ready;
};

#endif
