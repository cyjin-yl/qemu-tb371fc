/*
 * Host fixtures for the actual QEMU FDT producer helpers.
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * test_boot_fdt_handoff.py extracts verbatim production function bodies.
 * The shim below replaces QEMU object plumbing, not any FDT operation.
 */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <limits.h>
#include <libfdt.h>

#define QEMU_BUILD_BUG_ON(x) _Static_assert(!(x), "QEMU constant mismatch")
#include "target/arm/kvm-consts.h"

#define g_strdup strdup
#define g_free free
#define g_assert_not_reached() abort()
#define cpu_to_be32 cpu_to_fdt32
#define cpu_to_be64 cpu_to_fdt64
#define OBJECT(x) (x)
#define ARM_FEATURE_AARCH64 0
#define QEMU_PSCI_CONDUIT_DISABLED 0
#define QEMU_PSCI_CONDUIT_HVC 1
#define QEMU_PSCI_CONDUIT_SMC 2

typedef struct FixtureEnv {
    bool aarch64;
} FixtureEnv;

typedef struct ARMCPU {
    FixtureEnv env;
    uint32_t psci_version;
    int conduit;
} ARMCPU;

static void *error_abort;

static int64_t object_property_get_int(ARMCPU *cpu, const char *name, void **errp)
{
    (void)name;
    (void)errp;
    return cpu->conduit;
}

static bool arm_feature(const FixtureEnv *env, int feature)
{
    (void)feature;
    return env->aarch64;
}

static void error_report(const char *format, ...)
{
    va_list args;

    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
}

#include "handoff-functions.inc"

#define CHECK(test) do { \
    if (!(test)) { \
        fprintf(stderr, "%s:%d: %s\n", __func__, __LINE__, #test); \
        exit(1); \
    } \
} while (0)

#define FDT_CAPACITY 32768
static unsigned cases;

static void new_tree(void *fdt)
{
    memset(fdt, 0xa5, FDT_CAPACITY);
    CHECK(fdt_create_empty_tree(fdt, FDT_CAPACITY) == 0);
    CHECK(fdt_add_mem_rsv(fdt, 0xbb000000, 0x100000) == 0);
}

static const void *value_at(const void *fdt, const char *path,
                            const char *name, int size)
{
    int actual;
    const void *value = fdt_getprop(fdt, fdt_path_offset(fdt, path), name, &actual);

    CHECK(value != NULL);
    CHECK(actual == size);
    return value;
}

static void check_reservation(const void *fdt)
{
    uint64_t address, size;

    CHECK(fdt_num_mem_rsv(fdt) == 1);
    CHECK(fdt_get_mem_rsv(fdt, 0, &address, &size) == 0);
    CHECK(address == 0xbb000000 && size == 0x100000);
}

static void padding_case(int size)
{
    unsigned char fdt[FDT_CAPACITY];
    const unsigned char replacement[] = "abcdefgh";
    const unsigned char *actual;
    unsigned char *untouched;
    int length, i;

    new_tree(fdt);
    qemu_fdt_setprop_string(fdt, "/", "sentinel", "next-token-survives");
    qemu_fdt_setprop_string(fdt, "/", "untouched", "x");
    untouched = fdt_getprop_w(fdt, 0, "untouched", &length);
    CHECK(length == 2);
    untouched[2] = 0x71;
    untouched[3] = 0x72;
    /* Shrinking over nonzero bytes reproduces older libfdt's stale tail. */
    qemu_fdt_setprop_string(fdt, "/", "target", "ABCDEFGHIJKLMNO");
    qemu_fdt_setprop(fdt, "/", "target", replacement, size);
    actual = value_at(fdt, "/", "target", size);
    CHECK(memcmp(actual, replacement, size) == 0);
    for (i = size; i < ((size + 3) & ~3); i++) {
        CHECK(actual[i] == 0);
    }
    CHECK(strcmp(value_at(fdt, "/", "sentinel", 20),
                 "next-token-survives") == 0);
    untouched = (unsigned char *)value_at(fdt, "/", "untouched", 2);
    CHECK(untouched[2] == 0x71 && untouched[3] == 0x72);
    check_reservation(fdt);
    CHECK(fdt_check_header(fdt) == 0);
    cases++;
}

static void wrapper_case(void)
{
    unsigned char fdt[FDT_CAPACITY];
    const unsigned char *value;
    uint32_t cell = cpu_to_fdt32(0x12345678);
    uint64_t wide = cpu_to_fdt64(UINT64_C(0x123456789abcdef0));

    new_tree(fdt);
    qemu_fdt_setprop_string(fdt, "/", "string", "ABCDEF");
    qemu_fdt_setprop_string(fdt, "/", "string", "xy");
    value = value_at(fdt, "/", "string", 3);
    CHECK(memcmp(value, "xy", 3) == 0 && value[3] == 0);
    qemu_fdt_setprop_cell(fdt, "/", "cell", 0x12345678);
    CHECK(memcmp(value_at(fdt, "/", "cell", 4), &cell, 4) == 0);
    qemu_fdt_setprop_u64(fdt, "/", "wide", UINT64_C(0x123456789abcdef0));
    CHECK(memcmp(value_at(fdt, "/", "wide", 8), &wide, 8) == 0);
    check_reservation(fdt);
    cases++;
}

static bool psci_abi_property(const char *name)
{
    const char *names[] = { "compatible", "method", "cpu_suspend", "cpu_off",
                            "cpu_on", "migrate" };
    unsigned i;

    for (i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
        if (!strcmp(name, names[i])) {
            return true;
        }
    }
    return false;
}

/* Called both ways: no extra/missing node or unknown property is permitted. */
static void compare_preserved_tree(const void *before, const void *after)
{
    int offset = -1;

    while ((offset = fdt_next_node(before, offset, NULL)) >= 0) {
        char path[256];
        int property, other;

        CHECK(fdt_get_path(before, offset, path, sizeof(path)) == 0);
        other = fdt_path_offset(after, path);
        CHECK(other >= 0);
        fdt_for_each_property_offset(property, before, offset) {
            int size, actual_size;
            const char *name;
            const void *value = fdt_getprop_by_offset(before, property,
                                                      &name, &size);
            const void *actual;

            CHECK(value != NULL);
            if (!strcmp(path, "/psci") && psci_abi_property(name)) {
                continue;
            }
            actual = fdt_getprop(after, other, name, &actual_size);
            CHECK(actual != NULL && actual_size == size);
            CHECK(memcmp(value, actual, size) == 0);
        }
        CHECK(property == -FDT_ERR_NOTFOUND);
    }
    CHECK(offset == -FDT_ERR_NOTFOUND);
}

static void psci_tree(void *fdt)
{
    int i;
    char domain[128], cpu[128];
    const char *abi[] = { "cpu_suspend", "cpu_off", "cpu_on", "migrate" };

    new_tree(fdt);
    qemu_fdt_add_subnode(fdt, "/psci");
    qemu_fdt_setprop_string(fdt, "/psci", "compatible", "vendor,old-psci");
    qemu_fdt_setprop_string(fdt, "/psci", "method", "old-method");
    for (i = 0; i < 4; i++) {
        qemu_fdt_setprop_cell(fdt, "/psci", abi[i], 0xdead0000 + i);
    }
    qemu_fdt_setprop_cell(fdt, "/psci", "phandle", 0x10);
    qemu_fdt_setprop_cell(fdt, "/psci", "#power-domain-cells", 0);
    qemu_fdt_setprop_string(fdt, "/psci", "vendor,marker", "keep-parent");
    qemu_fdt_add_subnode(fdt, "/psci/power-domain-cluster0");
    qemu_fdt_setprop_cell(fdt, "/psci/power-domain-cluster0", "phandle", 0x20);
    qemu_fdt_setprop_cell(fdt, "/psci/power-domain-cluster0",
                          "#power-domain-cells", 0);
    qemu_fdt_add_subnode(fdt, "/cpus");
    for (i = 0; i < 8; i++) {
        snprintf(domain, sizeof(domain), "/psci/power-domain-cpu%d", i);
        snprintf(cpu, sizeof(cpu), "/cpus/cpu@%d", i);
        qemu_fdt_add_subnode(fdt, domain);
        qemu_fdt_setprop_cell(fdt, domain, "phandle", 0x30 + i);
        qemu_fdt_setprop_cell(fdt, domain, "#power-domain-cells", 0);
        qemu_fdt_setprop_cell(fdt, domain, "power-domains", 0x20);
        qemu_fdt_setprop_string(fdt, domain, "vendor,marker", domain);
        qemu_fdt_add_subnode(fdt, cpu);
        qemu_fdt_setprop_cell(fdt, cpu, "power-domains", 0x30 + i);
        qemu_fdt_setprop_cell(fdt, cpu, "vendor,parent-psci", 0x10);
    }
}

static void check_cell(const void *fdt, const char *property, uint32_t expected)
{
    uint32_t value;

    memcpy(&value, value_at(fdt, "/psci", property, 4), 4);
    CHECK(fdt32_to_cpu(value) == expected);
}

static void psci_case(uint32_t version, bool aarch64, int conduit)
{
    unsigned char fdt[FDT_CAPACITY], before[FDT_CAPACITY];
    ARMCPU cpu = { .env.aarch64 = aarch64, .psci_version = version,
                   .conduit = conduit };
    const char v01[] = "arm,psci";
    const char v02[] = "arm,psci-0.2\0arm,psci";
    const char v10[] = "arm,psci-1.0\0arm,psci-0.2\0arm,psci";
    const char *compat = version >= QEMU_PSCI_VERSION_1_0 ? v10 :
                         version >= QEMU_PSCI_VERSION_0_2 ? v02 : v01;
    int size = version >= QEMU_PSCI_VERSION_1_0 ? sizeof(v10) :
               version >= QEMU_PSCI_VERSION_0_2 ? sizeof(v02) : sizeof(v01);
    uint32_t base = version < QEMU_PSCI_VERSION_0_2 ? 0x95c1ba5e :
                    aarch64 ? 0xc4000000 : 0x84000000;
    int node, descendants = 0;

    psci_tree(fdt);
    memcpy(before, fdt, sizeof(before));
    fdt_add_psci_node(fdt, &cpu, true);
    compare_preserved_tree(before, fdt);
    compare_preserved_tree(fdt, before);
    CHECK(memcmp(value_at(fdt, "/psci", "compatible", size), compat, size) == 0);
    CHECK(strcmp(value_at(fdt, "/psci", "method", 4),
                 conduit == QEMU_PSCI_CONDUIT_SMC ? "smc" : "hvc") == 0);
    check_cell(fdt, "cpu_suspend", base + (version < 2 ? 0 : 1));
    check_cell(fdt, "cpu_off", version < 2 ? base + 1 : 0x84000002);
    check_cell(fdt, "cpu_on", base + (version < 2 ? 2 : 3));
    check_cell(fdt, "migrate", base + (version < 2 ? 3 : 5));
    fdt_for_each_subnode(node, fdt, fdt_path_offset(fdt, "/psci")) {
        descendants++;
    }
    CHECK(node == -FDT_ERR_NOTFOUND && descendants == 9);
    check_reservation(fdt);
    cases++;
}

static void legacy_and_missing_cases(void)
{
    unsigned char fdt[FDT_CAPACITY], before[FDT_CAPACITY];
    ARMCPU cpu = { .env.aarch64 = true, .psci_version = QEMU_PSCI_VERSION_1_0,
                   .conduit = QEMU_PSCI_CONDUIT_SMC };
    int size;

    psci_tree(fdt);
    fdt_add_psci_node(fdt, &cpu, false);
    CHECK(fdt_first_subnode(fdt, fdt_path_offset(fdt, "/psci")) ==
          -FDT_ERR_NOTFOUND);
    CHECK(fdt_getprop(fdt, fdt_path_offset(fdt, "/psci"), "phandle", &size) ==
          NULL && size == -FDT_ERR_NOTFOUND);
    check_cell(fdt, "cpu_on", 0xc4000003);
    cases++;

    new_tree(fdt);
    fdt_add_psci_node(fdt, &cpu, true);
    check_cell(fdt, "cpu_on", 0xc4000003);
    cases++;

    psci_tree(fdt);
    memcpy(before, fdt, sizeof(before));
    cpu.conduit = QEMU_PSCI_CONDUIT_DISABLED;
    fdt_add_psci_node(fdt, &cpu, true);
    CHECK(memcmp(before, fdt, sizeof(before)) == 0);
    cases++;
}

int main(void)
{
    int size;

    for (size = 0; size <= 8; size++) {
        padding_case(size);
    }
    wrapper_case();
    psci_case(QEMU_PSCI_VERSION_0_1, false, QEMU_PSCI_CONDUIT_HVC);
    psci_case(QEMU_PSCI_VERSION_0_2, false, QEMU_PSCI_CONDUIT_SMC);
    psci_case(QEMU_PSCI_VERSION_0_2, true, QEMU_PSCI_CONDUIT_HVC);
    psci_case(QEMU_PSCI_VERSION_1_0, true, QEMU_PSCI_CONDUIT_SMC);
    legacy_and_missing_cases();
    CHECK(cases == 17);
    printf("{\"status\":\"passed\",\"cases\":%u,"
           "\"preserved_psci_domains\":9}\n", cases);
    return 0;
}
