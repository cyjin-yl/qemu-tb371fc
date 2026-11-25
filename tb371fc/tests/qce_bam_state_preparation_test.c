/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Local preparation checks of the exact pure state helper. These are not
 * QEMU, MMIO-dispatch, VMState, IRQ-routing or native-boot tests. Literal
 * cold transactions come from the pinned vendor bam_pipe_init/set_irq
 * sequence. Descriptor addresses and M0 below are synthetic test fixtures.
 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include "qcom-sm8250-qce-bam-state.h"

static unsigned checks;
#define CHECK(expr) do { \
    checks++; \
    if (!(expr)) { \
        fprintf(stderr, "check failed at line %u: %s\n", __LINE__, #expr); \
        exit(1); \
    } \
} while (0)

static void write_ok(QceBamPipe p[4], unsigned n,
                     uint32_t offset, uint32_t value)
{
    QceBamPipe before[4];
    unsigned i;

    memcpy(before, p, sizeof(before));
    CHECK(qcb_pipe_write(&p[n], offset, value) == QCB_ACCESS_OK);
    CHECK(qcb_pipe_valid(&p[n]));
    for (i = 0; i < 4; i++) {
        if (i != n) {
            CHECK(memcmp(&before[i], &p[i], sizeof(p[i])) == 0);
        }
    }
}

static void write_rejected(QceBamPipe *p, uint32_t offset, uint32_t value)
{
    QceBamPipe before = *p;

    CHECK(qcb_pipe_write(p, offset, value) != QCB_ACCESS_OK);
    CHECK(memcmp(&before, p, sizeof(before)) == 0);
}

static void cold_connect(QceBamPipe p[4], unsigned n, bool producer,
                         unsigned group, uint32_t *ee_mask, uint32_t initial)
{
    unsigned pipe = n + 4;
    uint32_t word, next_mask;

    write_ok(p, n, 0x004, 1);
    write_ok(p, n, 0x004, 0);
    next_mask = *ee_mask | (1u << pipe);
    CHECK(qcb_ee_mask_valid(next_mask, initial));
    *ee_mask = next_mask;
    write_ok(p, n, 0x018, 0);
    CHECK(qcb_pipe_read(&p[n], 0, &word) == QCB_ACCESS_OK);
    write_ok(p, n, 0, (word & ~8u) | (producer ? 8u : 0));
    CHECK(qcb_pipe_read(&p[n], 0, &word) == QCB_ACCESS_OK);
    write_ok(p, n, 0, word | 0x20);
    write_ok(p, n, 0x828, 0x10);
    write_ok(p, n, 0x81c, 0x81234000u + 0x10000u * n);
    write_ok(p, n, 0x820, 0x9000);
    CHECK(qcb_pipe_read(&p[n], 0, &word) == QCB_ACCESS_OK);
    write_ok(p, n, 0, word & ~0x10u);
    write_ok(p, n, 0x914, 3);
    CHECK(qcb_pipe_read(&p[n], 0, &word) == QCB_ACCESS_OK);
    write_ok(p, n, 0, (word & ~0x1f0000u) | (group << 16));
    CHECK(qcb_pipe_read(&p[n], 0, &word) == QCB_ACCESS_OK);
    write_ok(p, n, 0, word | 2);
    write_ok(p, n, 0x018, producer ? 1 : 0);
    next_mask = producer ? *ee_mask | (1u << pipe) :
                          *ee_mask & ~(1u << pipe);
    CHECK(qcb_ee_mask_valid(next_mask, initial));
    *ee_mask = next_mask;
    CHECK(p[n].control == (group << 16) + (producer ? 0x2au : 0x22u));
    CHECK(p[n].irq_status == 0);
}

int main(void)
{
    QceBamPipe p[4] = {0};
    const uint32_t initial = 0x80000003; /* Synthetic nonzero preserved bits. */
    uint32_t ee_mask = initial;
    uint32_t word;
    unsigned i;
    QceBamPipe before, broken;
    static const struct { uint32_t off, value; } rejected[] = {
        { 0x000, 0x2002b }, /* reserved bit */
        { 0x000, 0x2003a }, /* streaming unimplemented */
        { 0x000, 0x2000a }, /* enabled BAM-to-BAM mode */
        { 0x000, 0x2082a }, /* write-NWD unimplemented */
        { 0x004, 2 },
        { 0x010, 1 }, /* status read-only */
        { 0x014, 0x100 },
        { 0x018, 0x100 },
        { 0x800, 8 }, /* completed offset read-only */
        { 0x818, 0 }, /* zero doorbell still unsupported */
        { 0x818, 8 },
        { 0x818, 0xffff },
        { 0x81c, 0x81234001 },
        { 0x914, 0x10 }, /* 36-bit source limit */
        { 0x820, 0 }, /* cannot shrink enabled ring to zero */
        { 0x820, 9 },
        { 0x820, 0x10000 },
        { 0x828, 0x10000 },
        { 0x008, 1 }, /* halt/timer/BAM-to-BAM data omitted */
        { 0x01c, 1 },
        { 0x020, 1 },
        { 0x824, 0x80000000 },
        { 0x82c, 0x80000000 },
        { 0x934, 1 },
        { 0xffc, 0 },
    };

    CHECK(sizeof(QceBamPipe) == 32);
    /* qcedev producer/consumer, then qcrypto producer/consumer. */
    cold_connect(p, 3, true, 3, &ee_mask, initial);
    cold_connect(p, 2, false, 3, &ee_mask, initial);
    cold_connect(p, 1, true, 2, &ee_mask, initial);
    cold_connect(p, 0, false, 2, &ee_mask, initial);
    CHECK(ee_mask == 0x800000a3);
    CHECK(!qcb_ee_mask_valid(ee_mask ^ 0x80000000u, initial));
    CHECK(!qcb_ee_mask_valid(ee_mask ^ 1u, initial));

    for (i = 0; i < sizeof(rejected) / sizeof(rejected[0]); i++) {
        write_rejected(&p[1], rejected[i].off, rejected[i].value);
    }
    before = p[1];
    CHECK(qcb_pipe_read(&p[1], 0x014, &word) == QCB_ACCESS_WRITE_ONLY);
    CHECK(qcb_pipe_read(&p[1], 0x818, &word) == QCB_ACCESS_UNSUPPORTED);
    CHECK(qcb_pipe_read(&p[1], 0x008, &word) == QCB_ACCESS_UNSUPPORTED);
    CHECK(qcb_pipe_read(&p[1], 0x800, &word) == QCB_ACCESS_OK && word == 0);
    CHECK(memcmp(&before, &p[1], sizeof(before)) == 0);
    write_ok(p, 1, 0x014, 0xff); /* W1C cannot create an event. */
    CHECK(p[1].irq_status == 0);

    broken = p[1]; broken.irq_status = 1;
    CHECK(!qcb_pipe_valid(&broken)); /* Migration must reject fake completion. */
    broken = p[1]; broken.descriptor_hi = 0x10;
    CHECK(!qcb_pipe_valid(&broken));
    broken = p[1]; broken.reset = 1;
    CHECK(!qcb_pipe_valid(&broken));
    broken = p[1]; broken.irq_enable = 0x100;
    CHECK(!qcb_pipe_valid(&broken));

    write_ok(p, 3, 0x004, 1);
    CHECK(qcb_pipe_zero_except_reset(&p[3]) && p[3].reset == 1);
    before = p[3];
    CHECK(qcb_pipe_write(&p[3], 0x81c, 0x80000000) == QCB_ACCESS_HELD_RESET);
    CHECK(memcmp(&before, &p[3], sizeof(before)) == 0);
    write_ok(p, 3, 0x004, 0);
    CHECK(qcb_pipe_zero_except_reset(&p[3]) && p[3].reset == 0);
    CHECK(ee_mask == 0x800000a3); /* Pipe reset cannot edit the global mask. */

    /* Source's SPS_ADDR_INVALID is 0xdeadbeef; address zero is not that value. */
    write_ok(p, 3, 0x820, 16);
    write_ok(p, 3, 0x000, 0x22);
    CHECK(p[3].descriptor_lo == 0 && p[3].descriptor_hi == 0);
    CHECK(qcb_pipe_valid(&p[3]));

    printf("{\"scope\":\"pure-C-state-helper-preparation\",\"checks\":%u,"
           "\"four_cold_pipes\":true,\"atomic_rejection_cases\":25,"
           "\"synthetic_nonpipe_mask_preserved\":true,"
           "\"QEMU_executed\":false,\"MMIO_dispatch_tested\":false,"
           "\"VMState_runtime_tested\":false,\"native_executed\":false}\n", checks);
    return 0;
}
