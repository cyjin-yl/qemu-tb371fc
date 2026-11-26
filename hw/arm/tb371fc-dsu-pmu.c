/*
 * TB371FC opt-in, AArch64 DSU cycle-only compatibility profile.
 *
 * The captured DT connects all eight CPUs to one DSU and GIC SPI 50.
 * This synthetic profile reports N=0, no programmable events, and one
 * 64-bit virtual cycle counter at one tick per QEMU virtual nanosecond.
 * It does not model physical A77/DSU cache events or silicon clock timing.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/visitor.h"
#include "qemu/module.h"
#include "qemu/timer.h"
#include "hw/core/irq.h"
#include "hw/core/qdev-properties.h"
#include "migration/vmstate.h"
#include "tb371fc-dsu-pmu.h"
#include "target/arm/cpregs.h"
#include "target/arm/internals.h"

#define DSU_CPUS 8
#define DSU_PROFILE UINT32_C(0x44535501)
#define DSU_PMCR_FIXED UINT32_C(0x51000040) /* synthetic QEMU, N=0, RES1 */
#define DSU_CYCLE UINT32_C(0x80000000)
#define DSU_EL1_WRITE_ENABLE (UINT64_C(1) << 12)

struct Tb371fcDsuPmuState {
    SysBusDevice parent_obj;
    bool enabled;
    uint32_t profile_id;
    uint32_t control;
    uint32_t counter_enable;
    uint32_t interrupt_enable;
    uint32_t overflow;
    uint32_t selector;
    uint64_t cycle_count;
    int64_t last_ns;
    uint64_t actlr_el2[DSU_CPUS];
    QEMUTimer *timer;
    qemu_irq irq;
};

static Tb371fcDsuPmuState *dsu_from_cpu(CPUARMState *env)
{
    return TB371FC_DSU_PMU(object_property_get_link(
        OBJECT(env_archcpu(env)), "tb371fc-dsu-pmu", &error_abort));
}

static unsigned dsu_cpu_index(CPUARMState *env)
{
    unsigned index = CPU(env_archcpu(env))->cpu_index;

    assert(index < DSU_CPUS);
    return index;
}

static bool dsu_counting(Tb371fcDsuPmuState *s)
{
    return s->enabled && (s->control & 1) && (s->counter_enable & DSU_CYCLE);
}

static void dsu_sync(Tb371fcDsuPmuState *s)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    if (dsu_counting(s)) {
        uint64_t before = s->cycle_count;
        uint64_t elapsed = now - s->last_ns;

        s->cycle_count += elapsed;
        if (s->cycle_count < before) {
            s->overflow |= DSU_CYCLE;
        }
    }
    s->last_ns = now;
}

static void dsu_update(Tb371fcDsuPmuState *s)
{
    int64_t now = s->last_ns;

    qemu_set_irq(s->irq, s->enabled &&
                 !!(s->overflow & s->interrupt_enable & DSU_CYCLE));
    timer_del(s->timer);
    if (dsu_counting(s) && s->cycle_count) {
        uint64_t remaining = -s->cycle_count;

        /*
         * A full 2^64-tick interval cannot be represented by QEMUTimer.
         * Do not wrap a far-future unsigned delay into an immediate timer.
         * No observable wrap can occur before the virtual clock's horizon.
         */
        if (remaining <= (uint64_t)(INT64_MAX - now)) {
            timer_mod(s->timer, now + remaining);
        }
    }
}

static void dsu_timer(void *opaque)
{
    Tb371fcDsuPmuState *s = opaque;

    dsu_sync(s);
    dsu_update(s);
}

static CPAccessResult dsu_access(CPUARMState *env, const ARMCPRegInfo *ri,
                                bool isread)
{
    Tb371fcDsuPmuState *s = dsu_from_cpu(env);

    if (!isread && arm_current_el(env) == 1 && arm_is_el2_enabled(env) &&
        !(s->actlr_el2[dsu_cpu_index(env)] & DSU_EL1_WRITE_ENABLE)) {
        return CP_ACCESS_TRAP_EL2;
    }
    return CP_ACCESS_OK;
}

static uint64_t dsu_read(CPUARMState *env, const ARMCPRegInfo *ri)
{
    Tb371fcDsuPmuState *s = dsu_from_cpu(env);

    dsu_sync(s);
    dsu_update(s);
    if (ri->crm == 5) {
        switch (ri->opc2) {
        case 0: return DSU_PMCR_FIXED | s->control;
        case 1:
        case 2: return s->counter_enable;
        case 3:
        case 4: return s->overflow;
        case 5: return s->selector;
        case 6:
        case 7: return s->interrupt_enable;
        }
    } else if (ri->crm == 6 && ri->opc2 == 0) {
        return s->cycle_count;
    }
    /* N=0: all selected event counters/types and both CEID bitmaps are RAZ. */
    return 0;
}

static void dsu_write(CPUARMState *env, const ARMCPRegInfo *ri, uint64_t value)
{
    Tb371fcDsuPmuState *s = dsu_from_cpu(env);

    dsu_sync(s);
    if (ri->crm == 5) {
        switch (ri->opc2) {
        case 0:
            s->control = value & 1;
            if (value & 4) {
                s->cycle_count = 0;
            }
            /* P has no counters to reset; neither P nor C clears overflow. */
            break;
        case 1: s->counter_enable |= value & DSU_CYCLE; break;
        case 2: s->counter_enable &= ~(value & DSU_CYCLE); break;
        case 3: s->overflow |= value & DSU_CYCLE; break;
        case 4: s->overflow &= ~(value & DSU_CYCLE); break;
        case 5: s->selector = value & 31; break;
        case 6: s->interrupt_enable |= value & DSU_CYCLE; break;
        case 7: s->interrupt_enable &= ~(value & DSU_CYCLE); break;
        }
    } else if (ri->crm == 6 && ri->opc2 == 0) {
        s->cycle_count = value;
    }
    /* Selected event writes are ignored. */
    dsu_update(s);
}

static uint64_t dsu_actlr_read(CPUARMState *env, const ARMCPRegInfo *ri)
{
    return dsu_from_cpu(env)->actlr_el2[dsu_cpu_index(env)];
}

static void dsu_actlr_write(CPUARMState *env, const ARMCPRegInfo *ri,
                            uint64_t value)
{
    dsu_from_cpu(env)->actlr_el2[dsu_cpu_index(env)] =
        value & DSU_EL1_WRITE_ENABLE;
}

static void dsu_actlr_reset(CPUARMState *env, const ARMCPRegInfo *ri)
{
    /*
     * Direct-kernel handoff grants EL1 PMU writes. A CPU reset only restores
     * that CPU's access gate; it must never reset the cluster's counters.
     */
    dsu_from_cpu(env)->actlr_el2[dsu_cpu_index(env)] = DSU_EL1_WRITE_ENABLE;
}

#define DSU_REG(NAME, CRM, OP2) \
    { .name = NAME, .state = ARM_CP_STATE_AA64, \
      .opc0 = 3, .opc1 = 0, .crn = 15, .crm = CRM, .opc2 = OP2, \
      .access = PL1_RW, .type = ARM_CP_IO | ARM_CP_NO_RAW, \
      .accessfn = dsu_access, .readfn = dsu_read, .writefn = dsu_write }

static const ARMCPRegInfo dsu_regs[] = {
    DSU_REG("CLUSTERPMCR_EL1", 5, 0),
    DSU_REG("CLUSTERPMCNTENSET_EL1", 5, 1),
    DSU_REG("CLUSTERPMCNTENCLR_EL1", 5, 2),
    DSU_REG("CLUSTERPMOVSSET_EL1", 5, 3),
    DSU_REG("CLUSTERPMOVSCLR_EL1", 5, 4),
    DSU_REG("CLUSTERPMSELR_EL1", 5, 5),
    DSU_REG("CLUSTERPMINTENSET_EL1", 5, 6),
    DSU_REG("CLUSTERPMINTENCLR_EL1", 5, 7),
    DSU_REG("CLUSTERPMCCNTR_EL1", 6, 0),
    DSU_REG("CLUSTERPMXEVTYPER_EL1", 6, 1),
    DSU_REG("CLUSTERPMXEVCNTR_EL1", 6, 2),
    { .name = "CLUSTERPMCEID0_EL1", .state = ARM_CP_STATE_AA64,
      .opc0 = 3, .opc1 = 0, .crn = 15, .crm = 6, .opc2 = 4,
      .access = PL1_R, .type = ARM_CP_CONST, .resetvalue = 0 },
    { .name = "CLUSTERPMCEID1_EL1", .state = ARM_CP_STATE_AA64,
      .opc0 = 3, .opc1 = 0, .crn = 15, .crm = 6, .opc2 = 5,
      .access = PL1_R, .type = ARM_CP_CONST, .resetvalue = 0 },
    /*
     * Upstream ACTLR_EL2/HACTLR is RAZ/WI on this board. Override only the
     * DSU gate, leaving every other bit RAZ/WI. EL3 is absent on TB371FC.
     * CPU realization already added the old constant ACTLR migration slots.
     * Keep those slots canonically zero; the device owns the live access gates.
     */
    { .name = "ACTLR_EL2", .state = ARM_CP_STATE_BOTH,
      .opc0 = 3, .opc1 = 4, .crn = 1, .crm = 0, .opc2 = 1,
      .access = PL2_RW, .type = ARM_CP_IO | ARM_CP_OVERRIDE,
      .readfn = dsu_actlr_read, .writefn = dsu_actlr_write,
      .raw_readfn = arm_cp_read_zero, .raw_writefn = arm_cp_write_ignore,
      .resetfn = dsu_actlr_reset },
};

void tb371fc_dsu_pmu_attach_cpu(DeviceState *dev, ARMCPU *cpu)
{
    Tb371fcDsuPmuState *s = TB371FC_DSU_PMU(dev);
    unsigned index = CPU(cpu)->cpu_index;

    if (!s->enabled) {
        return;
    }
    assert(index < DSU_CPUS && !arm_feature(&cpu->env, ARM_FEATURE_EL3));
    object_property_add_const_link(OBJECT(cpu), "tb371fc-dsu-pmu", OBJECT(s));
    s->actlr_el2[index] = DSU_EL1_WRITE_ENABLE;
    define_arm_cp_regs(cpu, dsu_regs);
}

static void dsu_reset(DeviceState *dev)
{
    Tb371fcDsuPmuState *s = TB371FC_DSU_PMU(dev);

    s->control = 0;
    s->counter_enable = 0;
    s->interrupt_enable = 0;
    s->overflow = 0;
    s->selector = 0;
    s->cycle_count = 0;
    s->last_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    for (unsigned i = 0; i < DSU_CPUS; i++) {
        s->actlr_el2[i] = s->enabled ? DSU_EL1_WRITE_ENABLE : 0;
    }
    dsu_update(s);
}

static int dsu_pre_save(void *opaque)
{
    dsu_sync(opaque);
    dsu_update(opaque);
    return 0;
}

static int dsu_post_load(void *opaque, int version)
{
    Tb371fcDsuPmuState *s = opaque;

    if (s->control > 1 || s->selector > 31 ||
        ((s->counter_enable | s->interrupt_enable | s->overflow) & ~DSU_CYCLE)) {
        return -EINVAL;
    }
    if (!s->enabled && (s->control || s->selector || s->counter_enable ||
                        s->interrupt_enable || s->overflow || s->cycle_count)) {
        return -EINVAL;
    }
    for (unsigned i = 0; i < DSU_CPUS; i++) {
        if ((s->actlr_el2[i] & ~DSU_EL1_WRITE_ENABLE) ||
            (!s->enabled && s->actlr_el2[i])) {
            return -EINVAL;
        }
    }
    /* pre_save materialized the counter; migration downtime is not cycles. */
    s->last_ns = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);
    dsu_update(s);
    return 0;
}

static const VMStateDescription vmstate_dsu = {
    .name = TYPE_TB371FC_DSU_PMU,
    .version_id = 1,
    .minimum_version_id = 1,
    .pre_save = dsu_pre_save,
    .post_load = dsu_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_EQUAL(profile_id, Tb371fcDsuPmuState),
        VMSTATE_UINT32(control, Tb371fcDsuPmuState),
        VMSTATE_UINT32(counter_enable, Tb371fcDsuPmuState),
        VMSTATE_UINT32(interrupt_enable, Tb371fcDsuPmuState),
        VMSTATE_UINT32(overflow, Tb371fcDsuPmuState),
        VMSTATE_UINT32(selector, Tb371fcDsuPmuState),
        VMSTATE_UINT64(cycle_count, Tb371fcDsuPmuState),
        VMSTATE_UINT64_ARRAY(actlr_el2, Tb371fcDsuPmuState, DSU_CPUS),
        VMSTATE_END_OF_LIST()
    },
};

static void dsu_realize(DeviceState *dev, Error **errp)
{
    Tb371fcDsuPmuState *s = TB371FC_DSU_PMU(dev);

    s->profile_id = s->enabled ? DSU_PROFILE : 0;
}

static void dsu_get(Object *obj, Visitor *v, const char *name, void *opaque,
                    Error **errp)
{
    Tb371fcDsuPmuState *s = TB371FC_DSU_PMU(obj);
    uint64_t value;

    /* Pure observation: do not refresh timers, counters or IRQs here. */
    if (!strcmp(name, "profile-id")) {
        value = s->profile_id;
    } else if (!strcmp(name, "frequency-hz")) {
        value = s->enabled ? 1000000000 : 0;
    } else if (!strcmp(name, "cycle-count")) {
        value = s->cycle_count;
    } else if (!strcmp(name, "control")) {
        value = s->control;
    } else if (!strcmp(name, "counter-enable")) {
        value = s->counter_enable;
    } else if (!strcmp(name, "interrupt-enable")) {
        value = s->interrupt_enable;
    } else {
        value = s->overflow;
    }
    visit_type_uint64(v, name, &value, errp);
}

static void dsu_init(Object *obj)
{
    Tb371fcDsuPmuState *s = TB371FC_DSU_PMU(obj);
    const char *names[] = {
        "profile-id", "frequency-hz", "cycle-count", "control",
        "counter-enable", "interrupt-enable", "overflow",
    };

    s->timer = timer_new_ns(QEMU_CLOCK_VIRTUAL, dsu_timer, s);
    sysbus_init_irq(SYS_BUS_DEVICE(obj), &s->irq);
    for (unsigned i = 0; i < ARRAY_SIZE(names); i++) {
        object_property_add(obj, names[i], "uint64", dsu_get, NULL, NULL, NULL);
    }
}

static void dsu_finalize(Object *obj)
{
    timer_free(TB371FC_DSU_PMU(obj)->timer);
}

static const Property dsu_properties[] = {
    DEFINE_PROP_BOOL("enabled", Tb371fcDsuPmuState, enabled, false),
};

static void dsu_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = dsu_realize;
    device_class_set_legacy_reset(dc, dsu_reset);
    dc->vmsd = &vmstate_dsu;
    dc->user_creatable = false;
    device_class_set_props(dc, dsu_properties);
}

static const TypeInfo dsu_type = {
    .name = TYPE_TB371FC_DSU_PMU,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(Tb371fcDsuPmuState),
    .instance_init = dsu_init,
    .instance_finalize = dsu_finalize,
    .class_init = dsu_class_init,
};

static void dsu_register_types(void)
{
    type_register_static(&dsu_type);
}
type_init(dsu_register_types)
