/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Architectural and real translated-DMA tests for the TB371FC SMMUv2 subset.
 * The machine creates the 0x50000000 test alias only under qtest: it aliases
 * IOVA 0..16MiB of SID 0x42. It is absent from normal execution and guest DT.
 */
#include "qemu/osdep.h"
#include "libqtest.h"

#define APPS UINT64_C(0x15000000)
#define GPU UINT64_C(0x03da0000)
#define CB(n) (APPS + 0x80000 + (n) * 0x1000)
#define DMA UINT64_C(0x50000000)
#define TABLE UINT64_C(0x82000000)
#define L2 (TABLE + 0x1000)
#define L3 (TABLE + 0x2000)
#define HIGH_TABLE (TABLE + 0x10000)
#define PA UINT64_C(0x81000000)
#define PA2 UINT64_C(0x81002000)
#define IOVA UINT64_C(0x1000)
#define PTE_RW (UINT64_C(0x443))
#define PTE_RO (UINT64_C(0x4c3))
#define FSR_FAULT UINT32_C(0x800001fe)
#define CTRL UINT32_C(0x200436)
#define SCTRL UINT32_C(0x1067)

static const uint8_t zero_tables[0x14000];

static void write_leaf(QTestState *s, uint64_t value)
{
    qtest_writeq(s, L3 + 8, value);
}

static void configure_cb(QTestState *s, unsigned cb, uint64_t ttbr)
{
    qtest_writel(s, APPS + 0x1000 + cb * 4, 1 << 16);
    qtest_writel(s, APPS + 0x1800 + cb * 4, 1);
    qtest_writel(s, CB(cb) + 0x10, 5 | (7 << 15));
    qtest_writel(s, CB(cb) + 0x30, 25 | (1 << 23));
    qtest_writeq(s, CB(cb) + 0x20, ttbr | (UINT64_C(0x42) << 48));
    qtest_writel(s, CB(cb) + 0x38, 0xff);
    qtest_writel(s, CB(cb), SCTRL);
}

static QTestState *start(void)
{
    QTestState *s = qtest_init(
        "-machine tb371fc,ufs-enabled=off -m 8G -smp 8 "
        "-display none -serial null");

    qtest_irq_intercept_out_named(s, "/machine/apps-smmu", "sysbus-irq");
    qtest_memwrite(s, TABLE, zero_tables, sizeof(zero_tables));
    qtest_writeq(s, TABLE, L2 | 3);
    qtest_writeq(s, L2, L3 | 3);
    write_leaf(s, PA | PTE_RW);
    qtest_writel(s, PA, 0x12345678);
    qtest_writel(s, PA2, 0xabcddcba);
    configure_cb(s, 0, TABLE);
    qtest_writel(s, APPS + 0x800, 0x80000042);
    qtest_writel(s, APPS + 0xc00, 0);
    qtest_writel(s, APPS, CTRL);
    return s;
}

static uint32_t fsr(QTestState *s, unsigned cb)
{
    return qtest_readl(s, CB(cb) + 0x58) & FSR_FAULT;
}

static uint64_t ats(QTestState *s, unsigned offset, uint64_t va)
{
    qtest_writeq(s, CB(0) + offset, va);
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x8f0), ==, 0);
    return qtest_readq(s, CB(0) + 0x50);
}

static void assert_global(QTestState *s, uint32_t flag)
{
    g_assert_cmphex(qtest_readl(s, APPS + 0x48) & flag, ==, flag);
    /* IHI0062D.c 9-208/212: GFAR is input VA, global WNR/PNU bits 1/2. */
    g_assert_cmphex(qtest_readq(s, APPS + 0x40), ==, IOVA);
    g_assert_cmphex(qtest_readl(s, APPS + 0x50) & 0x7f, ==, 6);
    g_assert_cmphex(qtest_readl(s, APPS + 0x54), ==, 0x42);
    g_assert_true(qtest_get_irq(s, 0));
    qtest_writel(s, APPS + 0x48, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, APPS + 0x48), ==, 0);
    g_assert_false(qtest_get_irq(s, 0));
}

static void capabilities(void)
{
    QTestState *s = start();
    uint32_t id0 = qtest_readl(s, APPS + 0x20);

    g_assert_cmphex(id0 & (1U << 30), !=, 0);
    g_assert_cmphex(id0 & ((1U << 29) | (1U << 28)), ==, 0);
    g_assert_cmphex(id0 & (1U << 14), !=, 0);
    g_assert_cmphex((id0 >> 24) & 3, ==, 2);
    /* EXIDS is absent: EXIDENABLE must be RAZ/WI (IHI0062D.c 9-194). */
    g_assert_cmphex(id0 & (1 << 8), ==, 0);
    qtest_writel(s, APPS, CTRL | (1 << 3));
    g_assert_cmphex(qtest_readl(s, APPS) & (1 << 3), ==, 0);
    g_assert_cmphex(qtest_readl(s, APPS + 0x24) & 0xff, ==, 96);
    g_assert_cmphex(qtest_readl(s, GPU + 0x24) & 0xff, ==, 8);
    g_assert_cmphex((qtest_readl(s, APPS + 0x28) >> 12) & 7, ==, 1);
    qtest_writel(s, APPS + 0x20, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, APPS + 0x20), ==, id0);
    qtest_writel(s, GPU + 0xf000, SCTRL);
    g_assert_cmphex(qtest_readl(s, GPU + 0xf000), ==, SCTRL);
    qtest_quit(s);
}

static void dma_page_read_write(void)
{
    QTestState *s = start();

    g_assert_cmphex(qtest_readl(s, DMA + IOVA), ==, 0x12345678);
    qtest_writel(s, DMA + IOVA, 0x87654321);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x87654321);
    g_assert_cmphex(fsr(s, 0), ==, 0);
    g_assert_false(qtest_get_irq(s, 2));
    qtest_quit(s);
}

static void stream_mask(void)
{
    QTestState *s = start();

    qtest_writel(s, APPS + 0x800, 0x80030040);
    g_assert_cmphex(qtest_readl(s, DMA + IOVA), ==, 0x12345678);
    qtest_quit(s);
}

static void unmapped_stream(void)
{
    QTestState *s = start();

    qtest_writel(s, APPS + 0x800, 0);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    assert_global(s, 1 << 1);
    qtest_quit(s);
}

static void stream_conflict(void)
{
    QTestState *s = start();

    qtest_writel(s, APPS + 0x804, 0x80000042);
    qtest_writel(s, APPS + 0xc04, 0);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    assert_global(s, 1 << 2);
    qtest_quit(s);
}

static void stream_fault_context(void)
{
    QTestState *s = start();

    qtest_writel(s, APPS + 0xc00, 2 << 16);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    assert_global(s, 1);
    qtest_quit(s);
}

static void unimplemented_context(void)
{
    QTestState *s = start();

    qtest_writel(s, APPS + 0xc00, 96);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    assert_global(s, 1 << 3);
    qtest_quit(s);
}

static void readonly_dma_fault(void)
{
    QTestState *s = start();

    write_leaf(s, PA | PTE_RO);
    g_assert_cmphex(qtest_readl(s, DMA + IOVA), ==, 0x12345678);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 3);
    g_assert_cmphex(qtest_readq(s, CB(0) + 0x60), ==, IOVA);
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x68) & (1 << 4), !=, 0);
    /* PNU=1 means privileged, unlike the user flag in MemTxAttrs. */
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x68) & (1 << 5), !=, 0);
    g_assert_cmphex(qtest_readl(s, APPS + 0x1400), ==, 0x42);
    g_assert_true(qtest_get_irq(s, 2));
    qtest_writel(s, CB(0) + 0x58, 1 << 3);
    g_assert_cmphex(fsr(s, 0), ==, 0);
    g_assert_false(qtest_get_irq(s, 2));
    qtest_quit(s);
}

static void access_flag_fault(void)
{
    QTestState *s = start();

    write_leaf(s, PA | (PTE_RW & ~UINT64_C(0x400)));
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 2);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    g_assert_true(qtest_get_irq(s, 2));
    qtest_quit(s);
}

static void invalid_leaf_fault(void)
{
    QTestState *s = start();

    write_leaf(s, 0);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 1);
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x68) & 3, ==, 3);
    g_assert_true(qtest_get_irq(s, 2));
    qtest_quit(s);
}

static void hierarchical_readonly(void)
{
    QTestState *s = start();

    qtest_writeq(s, TABLE, L2 | 3 | (UINT64_C(1) << 62));
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 3);
    qtest_quit(s);
}

static void unprivileged_permission(void)
{
    QTestState *s = start();

    write_leaf(s, PA | 0x403); /* Privileged RW, no EL0 access. */
    g_assert_cmphex(ats(s, 0x800, IOVA) & 1, ==, 0);
    g_assert_cmphex(ats(s, 0x810, IOVA) & 9, ==, 9);
    g_assert_cmphex(fsr(s, 0), ==, 0); /* ATS is not a client transaction. */
    qtest_writel(s, APPS + 0xc00, 2 << 24);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 3);
    /* IHI0062D.c 16-303: PNU=0 for forced-unprivileged access. */
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x68) & (1 << 5), ==, 0);
    qtest_quit(s);
}

static void output_address_size(void)
{
    QTestState *s = start();

    qtest_writel(s, CB(0) + 0x10, 7 << 15); /* 32-bit output. */
    write_leaf(s, UINT64_C(0x100000000) | PTE_RW);
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 7);
    qtest_quit(s);
}

static void external_page_walk_fault(void)
{
    QTestState *s = start();

    qtest_writeq(s, CB(0) + 0x20, UINT64_C(0x4000000000));
    qtest_writel(s, DMA + IOVA, 0xbad);
    g_assert_cmphex(fsr(s, 0), ==, 1 << 4);
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x68) & (1 << 10), !=, 0);
    qtest_quit(s);
}

static void block_translation(void)
{
    QTestState *s = start();

    /* Level 2 2MiB block: descriptor bit 1 is clear. */
    qtest_writeq(s, L2, PA | 0x441);
    qtest_writel(s, PA + IOVA, 0x88776655);
    g_assert_cmphex(qtest_readl(s, DMA + IOVA), ==, 0x88776655);
    qtest_writel(s, DMA + IOVA + 4, 0x55443322);
    g_assert_cmphex(qtest_readl(s, PA + IOVA + 4), ==, 0x55443322);
    qtest_quit(s);
}

static void invalidation_observes_new_table(void)
{
    QTestState *s = start();

    g_assert_cmphex(qtest_readl(s, DMA + IOVA), ==, 0x12345678);
    write_leaf(s, PA2 | PTE_RW);
    qtest_writel(s, CB(0) + 0x618, 0);
    qtest_writel(s, CB(0) + 0x7f0, 0);
    g_assert_cmphex(qtest_readl(s, CB(0) + 0x7f4), ==, 0);
    g_assert_cmphex(qtest_readl(s, DMA + IOVA), ==, 0xabcddcba);
    qtest_quit(s);
}

static void ats_fault_does_not_raise_client_irq(void)
{
    QTestState *s = start();
    uint64_t par;

    write_leaf(s, 0);
    par = ats(s, 0x800, IOVA);
    g_assert_cmphex(par & 3, ==, 3);
    g_assert_cmphex((par >> 32) & 3, ==, 3);
    g_assert_cmphex(fsr(s, 0), ==, 0);
    g_assert_false(qtest_get_irq(s, 2));
    qtest_quit(s);
}

static void high_table_setup(QTestState *s)
{
    qtest_writeq(s, HIGH_TABLE + 256 * 8, (HIGH_TABLE + 0x1000) | 3);
    qtest_writeq(s, HIGH_TABLE + 0x1000, (HIGH_TABLE + 0x2000) | 3);
    qtest_writeq(s, HIGH_TABLE + 0x2000, (HIGH_TABLE + 0x3000) | 3);
    qtest_writeq(s, HIGH_TABLE + 0x3000 + 8, PA2 | PTE_RW);
    qtest_writeq(s, CB(0) + 0x28, HIGH_TABLE);
    qtest_writel(s, CB(0) + 0x30, 25 | (16 << 16) | (2U << 30));
}

static void ttbr1_is_distinct(void)
{
    QTestState *s = start();
    uint64_t par;

    high_table_setup(s);
    par = ats(s, 0x800, UINT64_C(0xffff800000001234));
    g_assert_cmphex(par & 1, ==, 0);
    g_assert_cmphex(par & UINT64_C(0x0000fffffffff000), ==, PA2);
    par = ats(s, 0x800, UINT64_C(0x1234));
    g_assert_cmphex(par & UINT64_C(0x0000fffffffff000), ==, PA);
    /* Upper noncanonical addresses must not fold into TTBR0. */
    par = ats(s, 0x800, UINT64_C(0x1000000001234));
    g_assert_cmphex(par & (1 | (1 << 7)), ==, 1 | (1 << 7));
    qtest_quit(s);
}

static void ttbr1_tbi(void)
{
    QTestState *s = start();
    uint64_t par;

    high_table_setup(s);
    par = ats(s, 0x800, UINT64_C(0xaaff800000001234));
    g_assert_cmphex(par & 1, !=, 0);
    qtest_writel(s, CB(0) + 0x10, 5 | (7 << 15) | (1 << 6));
    par = ats(s, 0x800, UINT64_C(0xaaff800000001234));
    g_assert_cmphex(par & 1, ==, 0);
    g_assert_cmphex(par & UINT64_C(0x0000fffffffff000), ==, PA2);
    qtest_quit(s);
}

static void unsupported_granules_fault(void)
{
    QTestState *s = start();
    unsigned tg;

    for (tg = 1; tg < 4; tg++) {
        qtest_writel(s, CB(0) + 0x30, 25 | (1 << 23) | (tg << 14));
        qtest_writel(s, DMA + IOVA, 0xbad);
        g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
        assert_global(s, 1);
    }
    qtest_quit(s);
}

static void unsupported_stages_and_aarch32_fault(void)
{
    QTestState *s = start();
    unsigned type;

    for (type = 0; type < 4; type++) {
        if (type == 1) {
            continue;
        }
        qtest_writel(s, APPS + 0x1000, type << 16);
        qtest_writel(s, DMA + IOVA, 0xbad);
        g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
        assert_global(s, 1);
    }
    qtest_writel(s, APPS + 0x1000, 1 << 16);
    qtest_writel(s, APPS + 0x1800, 0);
    qtest_writel(s, DMA + IOVA, 0xbad);
    assert_global(s, 1);
    qtest_quit(s);
}

static void pending_fault_blocks_until_clear(void)
{
    QTestState *s = start();

    write_leaf(s, 0);
    qtest_writel(s, DMA + IOVA, 0xbad);
    write_leaf(s, PA | PTE_RW);
    qtest_writel(s, DMA + IOVA, 0x11111111);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x12345678);
    qtest_writel(s, CB(0) + 0x58, UINT32_MAX);
    qtest_writel(s, DMA + IOVA, 0x22222222);
    g_assert_cmphex(qtest_readl(s, PA), ==, 0x22222222);
    g_assert_cmphex(fsr(s, 0), ==, 0);
    qtest_quit(s);
}

static void multiple_fault_preserves_first_syndrome(void)
{
    QTestState *s = start();

    qtest_writel(s, CB(0), SCTRL | (1 << 8));
    write_leaf(s, 0);
    qtest_writel(s, DMA + IOVA, 0xbad);
    qtest_writel(s, DMA + IOVA + 4, 0xbad);
    g_assert_cmphex(fsr(s, 0), ==, (1U << 31) | (1 << 1));
    g_assert_cmphex(qtest_readq(s, CB(0) + 0x60), ==, IOVA);
    qtest_quit(s);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    qtest_add_func("/tb371fc/smmuv2/capabilities", capabilities);
    qtest_add_func("/tb371fc/smmuv2/dma-page-read-write", dma_page_read_write);
    qtest_add_func("/tb371fc/smmuv2/stream-mask", stream_mask);
    qtest_add_func("/tb371fc/smmuv2/unidentified-stream", unmapped_stream);
    qtest_add_func("/tb371fc/smmuv2/stream-conflict", stream_conflict);
    qtest_add_func("/tb371fc/smmuv2/fault-context", stream_fault_context);
    qtest_add_func("/tb371fc/smmuv2/unimplemented-context", unimplemented_context);
    qtest_add_func("/tb371fc/smmuv2/readonly-dma-fault", readonly_dma_fault);
    qtest_add_func("/tb371fc/smmuv2/access-flag", access_flag_fault);
    qtest_add_func("/tb371fc/smmuv2/invalid-leaf", invalid_leaf_fault);
    qtest_add_func("/tb371fc/smmuv2/hierarchical-readonly", hierarchical_readonly);
    qtest_add_func("/tb371fc/smmuv2/unprivileged-permission", unprivileged_permission);
    qtest_add_func("/tb371fc/smmuv2/output-address-size", output_address_size);
    qtest_add_func("/tb371fc/smmuv2/external-walk-fault", external_page_walk_fault);
    qtest_add_func("/tb371fc/smmuv2/block-translation", block_translation);
    qtest_add_func("/tb371fc/smmuv2/invalidation", invalidation_observes_new_table);
    qtest_add_func("/tb371fc/smmuv2/ats-fault-par-only", ats_fault_does_not_raise_client_irq);
    qtest_add_func("/tb371fc/smmuv2/ttbr1-distinct", ttbr1_is_distinct);
    qtest_add_func("/tb371fc/smmuv2/ttbr1-tbi", ttbr1_tbi);
    qtest_add_func("/tb371fc/smmuv2/unsupported-granules", unsupported_granules_fault);
    qtest_add_func("/tb371fc/smmuv2/unsupported-stages-aarch32", unsupported_stages_and_aarch32_fault);
    qtest_add_func("/tb371fc/smmuv2/pending-fault-blocks", pending_fault_blocks_until_clear);
    qtest_add_func("/tb371fc/smmuv2/multi-fault", multiple_fault_preserves_first_syndrome);
    return g_test_run();
}
