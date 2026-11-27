/*
 * Explicit SM8250 SDE RSC v3 initialization reference.
 *
 * Off by default at the board. This stores the bounded configuration written
 * by the preserved vendor initializer; it does not execute sequencer words,
 * start a solver, count time, deliver an interrupt, collapse power, or complete
 * a bandwidth transaction. Active requests fail before changing any state.
 * Reset values and accepted subsets are reference choices, not silicon data.
 *
 * Register offsets and initialization values: OnePlusOSS SM8250 kernel
 * 98719830b0de5584f5588bbe9f4c8304d97a64c6,
 * techpack/display/msm/sde_rsc_hw{,_v3}.{h,c}.
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "hw/misc/qcom-sm8250-sde-rsc.h"
#include "migration/vmstate.h"
#include "qapi/visitor.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define RSC_INIT_PROFILE 0x52534301U
#define SEQ_WORDS 17

typedef enum RscPolicy {
    RSC_U32,                 /* Passive 32-bit configuration storage. */
    RSC_RO_ZERO,             /* No executed operation can set status. */
    RSC_ZERO_ONLY,           /* Nonzero requests would start absent logic. */
    RSC_OVERRIDE,            /* Only inactive 0 or double-buffer bit 8. */
    RSC_BIT0,                /* Reference supports values 0 and 1. */
    RSC_SEQ_ADDRESS,         /* Byte address within the 17-word store. */
    RSC_MODE,                /* Three configured solver modes. */
    RSC_MODES,               /* Passive configured mode bitmap. */
    RSC_MODE_PARAM1,          /* Only reset or initializer's bit 31. */
    RSC_ROUTE,               /* Aligned PDC register address, no transaction. */
    RSC_FRAME_SELECT,         /* Reset or initializer's all-ones selection. */
} RscPolicy;

typedef enum RscRegister {
    W_CTRL, W_OVERRIDE, W_STATIC, W_THRESHOLD, W_DEBUG, W_VSYNC0, W_VSYNC1,
    W_SPARE, W_POWER, W_OVERRIDE2, W_MIN_THRESHOLD, W_BW, W_DEBUG2,
    W_AC0, W_AC1, W_FRAME1, W_FRAME2,
    W_CVAL0_LO, W_CVAL0_HI, W_CTL0, W_CVAL1_LO, W_CVAL1_HI, W_CTL1,
    D_PDC_SEQ, D_PDC_LO, D_PDC_HI, D_PDC_SLAVE,
    D_SEQ_BUSY, D_SEQ_PC, D_BRANCH0, D_BRANCH1,
    D_TIMERS, D_WAKE_LO, D_WAKE_HI, D_MAX_IDLE, D_OVERRIDE_CTRL,
    D_OVERRIDE_MODE, D_OVERRIDE_IDLE, D_MODES, D_STATUS0, D_STATUS1, D_STATUS2,
    D_MODE0_P0, D_MODE0_P1, D_MODE0_P2, D_MODE0_P3,
    D_MODE1_P0, D_MODE1_P1, D_MODE1_P2, D_MODE1_P3,
    D_MODE2_P0, D_MODE2_P1, D_MODE2_P2, D_MODE2_P3,
    D_SLOT0, D_SLOT1, D_SLOT2, D_SLOT3,
    D_PRI00, D_PRI10, D_PRI13, D_PRI20, D_PRI23,
    RSC_REGISTER_COUNT,
} RscRegister;

typedef struct RscRegDesc {
    bool wrapper;
    hwaddr offset;
    RscPolicy policy;
} RscRegDesc;

static const RscRegDesc registers[RSC_REGISTER_COUNT] = {
    [W_CTRL] =          { true,  0x000, RSC_ZERO_ONLY },
    [W_OVERRIDE] =      { true,  0x004, RSC_OVERRIDE },
    [W_STATIC] =        { true,  0x008, RSC_U32 },
    [W_THRESHOLD] =     { true,  0x00c, RSC_U32 },
    [W_DEBUG] =         { true,  0x010, RSC_RO_ZERO },
    [W_VSYNC0] =        { true,  0x018, RSC_RO_ZERO },
    [W_VSYNC1] =        { true,  0x01c, RSC_RO_ZERO },
    [W_SPARE] =         { true,  0x020, RSC_ZERO_ONLY },
    [W_POWER] =         { true,  0x024, RSC_RO_ZERO },
    [W_OVERRIDE2] =     { true,  0x040, RSC_ZERO_ONLY },
    [W_MIN_THRESHOLD] = { true,  0x044, RSC_U32 },
    [W_BW] =            { true,  0x048, RSC_ZERO_ONLY },
    [W_DEBUG2] =        { true,  0x050, RSC_RO_ZERO },
    [W_AC0] =           { true, 0x1040, RSC_BIT0 },
    [W_AC1] =           { true, 0x1044, RSC_BIT0 },
    [W_FRAME1] =        { true, 0x1fe0, RSC_FRAME_SELECT },
    [W_FRAME2] =        { true, 0x1ff0, RSC_FRAME_SELECT },
    [W_CVAL0_LO] =      { true, 0x2020, RSC_U32 },
    [W_CVAL0_HI] =      { true, 0x2024, RSC_U32 },
    [W_CTL0] =          { true, 0x202c, RSC_BIT0 },
    [W_CVAL1_LO] =      { true, 0x3020, RSC_U32 },
    [W_CVAL1_HI] =      { true, 0x3024, RSC_U32 },
    [W_CTL1] =          { true, 0x302c, RSC_BIT0 },
    [D_PDC_SEQ] =       { false, 0x020, RSC_ROUTE },
    [D_PDC_LO] =        { false, 0x024, RSC_ROUTE },
    [D_PDC_HI] =        { false, 0x028, RSC_ROUTE },
    [D_PDC_SLAVE] =     { false, 0x02c, RSC_BIT0 },
    [D_SEQ_BUSY] =      { false, 0x404, RSC_RO_ZERO },
    [D_SEQ_PC] =        { false, 0x408, RSC_RO_ZERO },
    [D_BRANCH0] =       { false, 0x410, RSC_SEQ_ADDRESS },
    [D_BRANCH1] =       { false, 0x414, RSC_SEQ_ADDRESS },
    [D_TIMERS] =        { false, 0xc00, RSC_BIT0 },
    [D_WAKE_LO] =       { false, 0xc04, RSC_U32 },
    [D_WAKE_HI] =       { false, 0xc08, RSC_U32 },
    [D_MAX_IDLE] =      { false, 0xc0c, RSC_U32 },
    [D_OVERRIDE_CTRL] = { false, 0xc14, RSC_ZERO_ONLY },
    [D_OVERRIDE_MODE] = { false, 0xc18, RSC_MODE },
    [D_OVERRIDE_IDLE] = { false, 0xc1c, RSC_U32 },
    [D_MODES] =         { false, 0xc20, RSC_MODES },
    [D_STATUS0] =       { false, 0xc24, RSC_RO_ZERO },
    [D_STATUS1] =       { false, 0xc28, RSC_RO_ZERO },
    [D_STATUS2] =       { false, 0xc2c, RSC_RO_ZERO },
    [D_MODE0_P0] =      { false, 0xc30, RSC_SEQ_ADDRESS },
    [D_MODE0_P1] =      { false, 0xc34, RSC_MODE_PARAM1 },
    [D_MODE0_P2] =      { false, 0xc38, RSC_U32 },
    [D_MODE0_P3] =      { false, 0xc40, RSC_U32 },
    [D_MODE1_P0] =      { false, 0xc4c, RSC_SEQ_ADDRESS },
    [D_MODE1_P1] =      { false, 0xc50, RSC_MODE_PARAM1 },
    [D_MODE1_P2] =      { false, 0xc54, RSC_U32 },
    [D_MODE1_P3] =      { false, 0xc5c, RSC_U32 },
    [D_MODE2_P0] =      { false, 0xc68, RSC_SEQ_ADDRESS },
    [D_MODE2_P1] =      { false, 0xc6c, RSC_MODE_PARAM1 },
    [D_MODE2_P2] =      { false, 0xc70, RSC_U32 },
    [D_MODE2_P3] =      { false, 0xc78, RSC_U32 },
    [D_SLOT0] =         { false, 0x1000, RSC_U32 },
    [D_SLOT1] =         { false, 0x1004, RSC_U32 },
    [D_SLOT2] =         { false, 0x1008, RSC_U32 },
    [D_SLOT3] =         { false, 0x100c, RSC_U32 },
    [D_PRI00] =         { false, 0x1080, RSC_MODE },
    [D_PRI10] =         { false, 0x1100, RSC_MODE },
    [D_PRI13] =         { false, 0x110c, RSC_MODE },
    [D_PRI20] =         { false, 0x1180, RSC_MODE },
    [D_PRI23] =         { false, 0x118c, RSC_MODE },
};

/* No ID, wake, IRQ or TCS register is inside a DRV overlay. */
static const struct {
    hwaddr offset, size;
    const char *name;
} drv_windows[] = {
    { 0x020, 0x10, "qcom-sm8250-sde-rsc-drv-020" },
    { 0x404, 0x14, "qcom-sm8250-sde-rsc-drv-404" },
    { 0x600, 0x44, "qcom-sm8250-sde-rsc-drv-600" },
    { 0xc00, 0x7c, "qcom-sm8250-sde-rsc-drv-c00" },
    { 0x1000, 0x10, "qcom-sm8250-sde-rsc-drv-1000" },
    { 0x1080, 0x04, "qcom-sm8250-sde-rsc-drv-1080" },
    { 0x1100, 0x04, "qcom-sm8250-sde-rsc-drv-1100" },
    { 0x110c, 0x04, "qcom-sm8250-sde-rsc-drv-110c" },
    { 0x1180, 0x04, "qcom-sm8250-sde-rsc-drv-1180" },
    { 0x118c, 0x04, "qcom-sm8250-sde-rsc-drv-118c" },
};

typedef struct RscWindow {
    MemoryRegion iomem;
    QcomSm8250SdeRscState *owner;
    hwaddr offset, size;
    bool wrapper;
} RscWindow;

struct QcomSm8250SdeRscState {
    SysBusDevice parent_obj;
    RscWindow windows[1 + ARRAY_SIZE(drv_windows)];
    uint32_t profile;
    uint32_t config[RSC_REGISTER_COUNT];
    uint32_t sequence[SEQ_WORDS];
};

static bool rsc_value_valid(RscPolicy policy, uint32_t value)
{
    switch (policy) {
    case RSC_U32:
        return true;
    case RSC_RO_ZERO:
    case RSC_ZERO_ONLY:
        return value == 0;
    case RSC_OVERRIDE:
        return value == 0 || value == 0x100;
    case RSC_BIT0:
        return value <= 1;
    case RSC_SEQ_ADDRESS:
        return !(value & 3) && value < 4 * SEQ_WORDS;
    case RSC_MODE:
        return value <= 2;
    case RSC_MODES:
        return !(value & ~7U);
    case RSC_MODE_PARAM1:
        return value == 0 || value == 0x80000000U;
    case RSC_ROUTE:
        return !(value & 3);
    case RSC_FRAME_SELECT:
        return value == 0 || value == UINT32_MAX;
    }
    g_assert_not_reached();
}

static bool rsc_config_valid(const uint32_t *config)
{
    unsigned i;

    for (i = 0; i < RSC_REGISTER_COUNT; i++) {
        if (!rsc_value_valid(registers[i].policy, config[i])) {
            return false;
        }
    }
    /* Only the initializer's inert all-ones compare may be armed. */
    return (!config[W_CTL0] || (config[W_CVAL0_LO] == UINT32_MAX &&
                                config[W_CVAL0_HI] == UINT32_MAX)) &&
           (!config[W_CTL1] || (config[W_CVAL1_LO] == UINT32_MAX &&
                                config[W_CVAL1_HI] == UINT32_MAX));
}

static int rsc_register_index(bool wrapper, hwaddr offset)
{
    unsigned i;

    for (i = 0; i < RSC_REGISTER_COUNT; i++) {
        if (registers[i].wrapper == wrapper && registers[i].offset == offset) {
            return i;
        }
    }
    return -1;
}

static MemTxResult rsc_reject(RscWindow *window, hwaddr offset,
                              unsigned size, bool write)
{
    qemu_log_mask(LOG_GUEST_ERROR, TYPE_QCOM_SM8250_SDE_RSC
                  ": rejected %s %s offset=0x%" HWADDR_PRIx " size=%u\n",
                  window->wrapper ? "wrapper" : "drv",
                  write ? "write" : "read", offset, size);
    return MEMTX_ERROR;
}

static MemTxResult rsc_read(void *opaque, hwaddr addr, uint64_t *value,
                             unsigned size, MemTxAttrs attrs)
{
    RscWindow *window = opaque;
    QcomSm8250SdeRscState *s = window->owner;
    hwaddr offset = window->offset + addr;
    int index;

    *value = 0;
    if (size != 4 || (addr & 3) || addr > window->size - 4) {
        return rsc_reject(window, offset, size, false);
    }
    if (!window->wrapper && offset >= 0x600 && offset < 0x644) {
        *value = s->sequence[(offset - 0x600) / 4];
        return MEMTX_OK;
    }
    index = rsc_register_index(window->wrapper, offset);
    if (index < 0) {
        return rsc_reject(window, offset, size, false);
    }
    *value = s->config[index];
    return MEMTX_OK;
}

static MemTxResult rsc_write(void *opaque, hwaddr addr, uint64_t value,
                              unsigned size, MemTxAttrs attrs)
{
    RscWindow *window = opaque;
    QcomSm8250SdeRscState *s = window->owner;
    hwaddr offset = window->offset + addr;
    uint32_t candidate[RSC_REGISTER_COUNT];
    int index;

    if (size != 4 || (addr & 3) || addr > window->size - 4) {
        return rsc_reject(window, offset, size, true);
    }
    if (!window->wrapper && offset >= 0x600 && offset < 0x644) {
        s->sequence[(offset - 0x600) / 4] = value;
        return MEMTX_OK;
    }
    index = rsc_register_index(window->wrapper, offset);
    if (index < 0 || registers[index].policy == RSC_RO_ZERO) {
        return rsc_reject(window, offset, size, true);
    }
    memcpy(candidate, s->config, sizeof(candidate));
    candidate[index] = value;
    if (!rsc_config_valid(candidate)) {
        return rsc_reject(window, offset, size, true);
    }
    s->config[index] = value;
    return MEMTX_OK;
}

static const MemoryRegionOps rsc_ops = {
    .read_with_attrs = rsc_read,
    .write_with_attrs = rsc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    /* Preserve the original transaction for atomic malformed-access errors. */
    .valid = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
    .impl = { .min_access_size = 1, .max_access_size = 8, .unaligned = true },
};

static void rsc_reset(DeviceState *dev)
{
    QcomSm8250SdeRscState *s = QCOM_SM8250_SDE_RSC(dev);

    memset(s->config, 0, sizeof(s->config));
    memset(s->sequence, 0, sizeof(s->sequence));
}

static int rsc_post_load(void *opaque, int version)
{
    QcomSm8250SdeRscState *s = opaque;

    return rsc_config_valid(s->config) ? 0 : -EINVAL;
}

static const VMStateDescription vmstate_rsc = {
    .name = TYPE_QCOM_SM8250_SDE_RSC,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = rsc_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile, QcomSm8250SdeRscState),
        VMSTATE_UINT32_ARRAY(config, QcomSm8250SdeRscState, RSC_REGISTER_COUNT),
        VMSTATE_UINT32_ARRAY(sequence, QcomSm8250SdeRscState, SEQ_WORDS),
        VMSTATE_END_OF_LIST()
    },
};

void qcom_sm8250_sde_rsc_map(DeviceState *dev, hwaddr drv, hwaddr wrapper)
{
    SysBusDevice *bus = SYS_BUS_DEVICE(dev);
    unsigned i;

    sysbus_mmio_map(bus, 0, wrapper);
    for (i = 0; i < ARRAY_SIZE(drv_windows); i++) {
        sysbus_mmio_map_overlap(bus, i + 1, drv + drv_windows[i].offset, 1);
    }
}

static void rsc_get_profile(Object *obj, Visitor *v, const char *name,
                             void *opaque, Error **errp)
{
    uint32_t profile = QCOM_SM8250_SDE_RSC(obj)->profile;

    visit_type_uint32(v, name, &profile, errp);
}

static void rsc_init(Object *obj)
{
    QcomSm8250SdeRscState *s = QCOM_SM8250_SDE_RSC(obj);
    unsigned i;

    s->profile = RSC_INIT_PROFILE;
    for (i = 0; i < ARRAY_SIZE(s->windows); i++) {
        RscWindow *w = &s->windows[i];
        const char *name = i ? drv_windows[i - 1].name :
                              "qcom-sm8250-sde-rsc-wrapper";

        w->owner = s;
        w->wrapper = i == 0;
        w->offset = i ? drv_windows[i - 1].offset : 0;
        w->size = i ? drv_windows[i - 1].size : 0x3fd4;
        memory_region_init_io(&w->iomem, obj, &rsc_ops, w, name, w->size);
        sysbus_init_mmio(SYS_BUS_DEVICE(s), &w->iomem);
    }
    object_property_add(obj, "profile-id", "uint32", rsc_get_profile,
                        NULL, NULL, NULL);
}

static void rsc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->desc = "SDE RSC v3 passive initialization reference; no solver or timers";
    dc->user_creatable = false;
    dc->vmsd = &vmstate_rsc;
    device_class_set_legacy_reset(dc, rsc_reset);
}

static const TypeInfo rsc_info = {
    .name = TYPE_QCOM_SM8250_SDE_RSC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(QcomSm8250SdeRscState),
    .instance_init = rsc_init,
    .class_init = rsc_class_init,
};

static void rsc_register_types(void)
{
    type_register_static(&rsc_info);
}
type_init(rsc_register_types)
