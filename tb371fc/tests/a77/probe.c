/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Bare-metal Cortex-A77 architectural checks, executed at EL3 on -M none.
 * Values come from Arm 101111_0101_05, not from QEMU source introspection.
 * This validates architectural behavior, not physical cache or timing fidelity.
 */
#include <stdint.h>
#include <stddef.h>

#ifndef EXPECT_REVIDR
#define EXPECT_REVIDR 0
#endif
#ifndef EXPECT_L2
#define EXPECT_L2 (512 * 1024)
#endif
#ifndef EXPECT_L3
#define EXPECT_L3 0
#endif
#ifndef EXPECT_CRYPTO
#define EXPECT_CRYPTO 1
#endif

volatile uint64_t expect_undefined;
volatile uint64_t saw_undefined;
static unsigned checks;

static uint64_t semi(uint64_t operation, uint64_t argument)
{
    register uint64_t x0 __asm__("x0") = operation;
    register uint64_t x1 __asm__("x1") = argument;
    __asm__ volatile("hlt #0xf000" : "+r"(x0) : "r"(x1) : "memory");
    return x0;
}

static void puts(const char *s)
{
    semi(4, (uintptr_t)s);
}

static void hex(uint64_t value)
{
    char out[19] = "0x0000000000000000";
    static const char digits[] = "0123456789abcdef";
    for (unsigned i = 0; i < 16; ++i) {
        out[17 - i] = digits[value & 15];
        value >>= 4;
    }
    semi(4, (uintptr_t)out);
}

__attribute__((noreturn)) static void finish(unsigned status)
{
    uint64_t args[2] = { 0x20026, status };
    semi(0x20, (uintptr_t)args); /* SYS_EXIT_EXTENDED */
    for (;;) {
        __asm__ volatile("wfi");
    }
}

static void check(const char *name, uint64_t actual, uint64_t expected)
{
    if (actual != expected) {
        puts("FAIL "); puts(name); puts(" actual="); hex(actual);
        puts(" expected="); hex(expected); puts("\n");
        finish(1);
    }
    ++checks;
    puts("PASS "); puts(name); puts("\n");
}

static void id(const char *name, uint64_t actual, uint64_t expected)
{
    puts("ID "); puts(name); puts("="); hex(actual); puts("\n");
    check(name, actual, expected);
}

#define READ(reg) ({ uint64_t value; \
    __asm__ volatile("mrs %0, " #reg : "=r"(value)); value; })
#define WRITE(reg, value) \
    __asm__ volatile("msr " #reg ", %0" : : "r"((uint64_t)(value)) : "memory")
#define ID(reg, value) id(#reg, READ(reg), (value))

/* Called with all general-purpose registers saved by the assembly vectors. */
void exception(uint64_t esr, uint64_t elr, uint64_t far)
{
    if (expect_undefined && (esr >> 26) == 0) {
        expect_undefined = 0;
        saw_undefined = 1;
        WRITE(elr_el3, elr + 4);
        return;
    }
    puts("FAIL unexpected-exception ESR="); hex(esr);
    puts(" ELR="); hex(elr); puts(" FAR="); hex(far); puts("\n");
    finish(1);
}

extern uint64_t insn_ldadd(uint64_t *, uint64_t);
extern uint64_t insn_cas(uint64_t *, uint64_t, uint64_t);
extern uint64_t insn_ldapr(uint64_t *);
extern uint64_t insn_fp16(void);
extern uint64_t insn_sdot(void);
extern uint64_t insn_udot(void);
extern uint64_t insn_rdm(void);
extern uint64_t insn_aese(void);
extern uint64_t insn_aesd(void);
extern uint64_t insn_pmull(void);
extern uint64_t insn_sha1h(uint32_t);
extern void insn_sha256su0(uint32_t, uint32_t, uint32_t *);
extern uint64_t insn_crc32(uint32_t);
extern uint64_t insn_crc32c(uint32_t);
extern void undef_sha256(void);
extern void undef_sve(void);
extern void undef_pauth(void);
extern void undef_sha512(void);
extern void undef_fhm(void);
extern void undef_rng(void);
extern void undef_bf16(void);

static void undefined(const char *name, void (*instruction)(void))
{
    saw_undefined = 0;
    expect_undefined = 1;
    instruction();
    check(name, saw_undefined, 1);
    check("undefined-handler-consumed-expectation", expect_undefined, 0);
}

static uint64_t cache(unsigned selector)
{
    WRITE(csselr_el1, selector);
    __asm__ volatile("isb");
    return READ(ccsidr_el1);
}

static uint64_t ccsidr(unsigned size, unsigned ways, unsigned policy)
{
    return ((uint64_t)policy << 28) |
           ((uint64_t)(size / (ways * 64) - 1) << 13) |
           ((ways - 1) << 3) | 2;
}

static uint32_t rotate(uint32_t x, unsigned n)
{
    return (x >> n) | (x << (32 - n));
}

static uint32_t sigma0(uint32_t x)
{
    return rotate(x, 7) ^ rotate(x, 18) ^ (x >> 3);
}

static uint32_t crc(uint32_t x, uint32_t polynomial)
{
    uint32_t result = 0;
    for (unsigned i = 0; i < 32; ++i) {
        uint32_t bit = (result ^ (x >> i)) & 1;
        result = (result >> 1) ^ (bit ? polynomial : 0);
    }
    return result;
}

static unsigned char zero_area[192] __attribute__((aligned(64)));

void probe_main(void)
{
    puts("Cortex-A77 bare-metal architectural probe\n");
    ID(CurrentEL, 12);
    ID(midr_el1, 0x411fd0d0);
    ID(revidr_el1, EXPECT_REVIDR);
    ID(id_aa64isar0_el1, EXPECT_CRYPTO ? 0x0000100010211120ull :
                                       0x0000100010210000ull);
    ID(id_aa64isar1_el1, 0x100001);
    ID(id_aa64mmfr0_el1, 0x101122);
    ID(id_aa64mmfr1_el1, 0x10212122);
    ID(id_aa64mmfr2_el1, 0x1011);
    /* -M none has no GIC; the board adds its GIC interface feature. */
    ID(id_aa64pfr0_el1, 0x1100000010111112ull);
    ID(id_aa64pfr1_el1, 0x10);
    ID(id_aa64dfr0_el1, 0x10305408);
    ID(id_aa64afr0_el1, 0);
    ID(id_aa64afr1_el1, 0);
    ID(id_aa64dfr1_el1, 0);
    ID(id_isar0_el1, 0x02101110);
    ID(id_isar1_el1, 0x13112111);
    ID(id_isar2_el1, 0x21232042);
    ID(id_isar3_el1, 0x01112131);
    ID(id_isar4_el1, 0x00010142);
    ID(id_isar5_el1, EXPECT_CRYPTO ? 0x01011121 : 0x01010001);
    ID(id_isar6_el1, 0x10);
    ID(id_mmfr0_el1, 0x10201105);
    ID(id_mmfr1_el1, 0x40000000);
    ID(id_mmfr2_el1, 0x01260000);
    ID(id_mmfr3_el1, 0x02122211);
    ID(id_mmfr4_el1, 0x00021110);
    ID(id_pfr0_el1, 0x10010131);
    ID(id_pfr1_el1, 0x00010000);
    id("id_pfr2_el1", READ(S3_0_C0_C3_4), 0x11);
    /* TCG removes MMAPTRC because it does not implement the trace macrocell. */
    ID(id_dfr0_el1, 0x04000088);
    ID(ctr_el0, 0x8444c004);
    ID(dczid_el0, 4);
    ID(clidr_el1, EXPECT_L3 ? 0xc3000123 : 0x82000023);
    ID(mvfr0_el1, 0x10110222);
    ID(mvfr1_el1, 0x13211111);
    ID(mvfr2_el1, 0x43);
    id("ccsidr_l1d", cache(0), 0x701fe01a);
    id("ccsidr_l1i", cache(1), 0x201fe01a);
    id("ccsidr_l2", cache(2), ccsidr(EXPECT_L2, 8, 7));
    if (EXPECT_L3) {
        id("ccsidr_l3", cache(4), ccsidr(EXPECT_L3, 16, 7));
    }
    id("pmcr_identity_and_six_counters", READ(pmcr_el0) & 0xffffff80,
       0x41103000);

    uint64_t value = 17;
    check("LSE-LDADD-old", insn_ldadd(&value, 5), 17);
    check("LSE-LDADD-memory", value, 22);
    check("LSE-CAS-match", insn_cas(&value, 22, 39), 22);
    check("LSE-CAS-match-memory", value, 39);
    check("LSE-CAS-mismatch", insn_cas(&value, 50, 88), 39);
    check("LSE-CAS-mismatch-memory", value, 39);
    check("RCpc-LDAPR", insn_ldapr(&value), 39);
    check("FP16-FADD", insn_fp16(), 0x4200);
    check("SDOT", insn_sdot(), 8);
    check("UDOT", insn_udot(), 2040);
    check("RDM-SQRDMLAH", insn_rdm(), 8292);
    check("CRC32", insn_crc32(0x12345678), crc(0x12345678, 0xedb88320));
    check("CRC32C", insn_crc32c(0x12345678), crc(0x12345678, 0x82f63b78));

    if (EXPECT_CRYPTO) {
        check("AES-AESE", insn_aese(), 0x63636363);
        check("AES-AESD", insn_aesd(), 0x52525252);
        check("PMULL", insn_pmull(), 0x3f7e);
        check("SHA1H", insn_sha1h(0x12345678), rotate(0x12345678, 2));
        uint32_t result[4];
        insn_sha256su0(0x12345678, 0x9abcdef0, result);
        for (unsigned i = 0; i < 4; ++i) {
            check("SHA256SU0-lane", result[i], 0x12345678u +
                  sigma0(i == 3 ? 0x9abcdef0u : 0x12345678u));
        }
    } else {
        undefined("crypto-off-AESE-undefined", (void (*)(void))insn_aese);
        undefined("crypto-off-AESD-undefined", (void (*)(void))insn_aesd);
        undefined("crypto-off-PMULL-undefined", (void (*)(void))insn_pmull);
        /* SHA1H's operand is irrelevant to the expected decode fault. */
        undefined("crypto-off-SHA1-undefined", (void (*)(void))insn_sha1h);
        undefined("crypto-off-SHA256-undefined", undef_sha256);
    }

    for (unsigned i = 0; i < sizeof(zero_area); ++i) {
        zero_area[i] = 0xa5;
    }
    __asm__ volatile("dc zva, %0" : : "r"(zero_area + 64) : "memory");
    for (unsigned i = 0; i < sizeof(zero_area); ++i) {
        if (zero_area[i] != ((i >= 64 && i < 128) ? 0 : 0xa5)) {
            check("DC-ZVA-block-and-guards", i, ~0ull);
        }
    }
    check("DC-ZVA-block-and-guards", 1, 1);
    __asm__ volatile("dc cvap, %0; dsb sy" : : "r"(zero_area) : "memory");
    check("DC-CVAP-decode", 1, 1);

    /* Deterministic architectural software-increment event, not cycle timing. */
    check("EL3-secure-lower-state", READ(scr_el3) & 1, 0);
    WRITE(mdcr_el3, (1u << 17)); /* SPME: count in Secure state. */
    WRITE(mdcr_el2, 6); /* All six counters controlled by PMCR.E. */
    WRITE(pmcr_el0, 7); /* E plus event and cycle counter reset. */
    for (unsigned counter = 0; counter < 6; ++counter) {
        WRITE(pmselr_el0, counter);
        __asm__ volatile("isb");
        WRITE(pmxevtyper_el0, 0); /* SW_INCR. */
        WRITE(pmxevcntr_el0, 0);
        WRITE(pmcntenset_el0, 1u << counter);
        WRITE(pmswinc_el0, 1u << counter);
        __asm__ volatile("isb");
        check("PMU-SWINC-counter", READ(pmxevcntr_el0), 1);
    }
    /*
     * HPMN/HPME still partition event counters while EL2 is disabled in
     * Secure state.  PMCR.E controls counters 0-2; HPME controls 3-5.
     */
    WRITE(mdcr_el2, 3);
    WRITE(pmcr_el0, 7);
    WRITE(pmswinc_el0, 0x3f);
    __asm__ volatile("isb");
    for (unsigned counter = 0; counter < 6; ++counter) {
        WRITE(pmselr_el0, counter);
        __asm__ volatile("isb");
        check("PMU-secure-HPME-off-partition", READ(pmxevcntr_el0),
              counter < 3 ? 1 : 0);
    }
    WRITE(pmcr_el0, 0);
    WRITE(mdcr_el2, 3 | (1u << 7));
    WRITE(pmswinc_el0, 0x3f);
    __asm__ volatile("isb");
    for (unsigned counter = 0; counter < 6; ++counter) {
        WRITE(pmselr_el0, counter);
        __asm__ volatile("isb");
        check("PMU-secure-HPME-on-PMCR-off-partition",
              READ(pmxevcntr_el0), 1);
    }
    WRITE(mdcr_el2, 6);

    undefined("SVE-undefined", undef_sve);
    undefined("PAuth-undefined", undef_pauth);
    undefined("SHA512-undefined", undef_sha512);
    undefined("FHM-undefined", undef_fhm);
    undefined("RNG-undefined", undef_rng);
    undefined("BF16-undefined", undef_bf16);
    puts("A77_PROBE_PASS checks="); hex(checks); puts("\n");
    finish(0);
}
