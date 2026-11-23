/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Qualcomm PDC legacy checks. Golden register offsets/types are from the
 * pinned Linux/vendor drivers; pin-to-SPI ranges are the captured native DT.
 * GPIO interception disconnects parent wiring in QTest, so routing/edge
 * cases deliberately avoid interception and inspect the real GIC pending bit.
 */
#include "qemu/osdep.h"
#include "libqtest.h"

#define PDC UINT64_C(0x0b220000)
#define SPI_CONFIG UINT64_C(0x17c000f0)
#define GICD UINT64_C(0x17a00000)
#define PATH "/machine/pdc"
#define CONFIG(n) (PDC + 0x110 + 4 * (n))
#define ENABLE(n) (PDC + 0x10 + 4 * ((n) / 32))

static QTestState *start(bool intercept)
{
    QTestState *s = qtest_init(
        "-machine tb371fc,ufs-enabled=off -m 128M -smp 8 "
        "-global qcom-pdc.reference-version=0x20000 "
        "-display none -serial null");

    if (intercept) {
        qtest_irq_intercept_out_named(s, PATH, "sysbus-irq");
    }
    return s;
}

static unsigned gic_id(unsigned pin)
{
    if (pin <= 93) {
        return 32 + 480 + pin;
    }
    if (pin <= 124) {
        return 32 + 609 + pin - 94;
    }
    if (pin == 125) {
        return 32 + 63;
    }
    g_assert_cmpuint(pin, <=, 137);
    return 32 + 716 + pin - 126;
}

static void input(QTestState *s, unsigned pin, bool level)
{
    qtest_set_irq_in(s, PATH, "irq-in", pin, level);
}

static void enable(QTestState *s, unsigned pin, bool on)
{
    uint32_t old = qtest_readl(s, ENABLE(pin));
    uint32_t bit = 1U << (pin % 32);

    qtest_writel(s, ENABLE(pin), on ? old | bit : old & ~bit);
}

static void parent_edge(QTestState *s, unsigned pin)
{
    unsigned id = gic_id(pin);
    uint64_t config = GICD + 0xc00 + (id / 16) * 4;
    uint32_t shift = (id % 16) * 2;
    uint32_t old = qtest_readl(s, config);

    qtest_writel(s, config, (old & ~(3U << shift)) | (2U << shift));
    g_assert_cmphex(qtest_readl(s, config) & (3U << shift), ==, 2U << shift);
    qtest_writel(s, GICD + 0x100 + (id / 32) * 4, 1U << (id % 32));
    qtest_writel(s, GICD, qtest_readl(s, GICD) | 3);
}

static void parent_clear(QTestState *s, unsigned pin)
{
    unsigned id = gic_id(pin);

    qtest_writel(s, GICD + 0x280 + (id / 32) * 4, 1U << (id % 32));
}

static bool parent_pending(QTestState *s, unsigned pin)
{
    unsigned id = gic_id(pin);

    return !!(qtest_readl(s, GICD + 0x200 + (id / 32) * 4) &
              (1U << (id % 32)));
}

static void version_reference(void)
{
    QTestState *s = start(false);

    /* This test explicitly requests reference v2.0, not native identity. */
    g_assert_cmphex(qtest_readl(s, PDC + 0x1000), ==, 0x20000);
    qtest_writel(s, PDC + 0x1000, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, PDC + 0x1000), ==, 0x20000);
    qtest_quit(s);
}

static void enable_banks(void)
{
    QTestState *s = start(false);

    /* Native mapped interval is 0..137: five banks, last bank has ten bits. */
    qtest_writel(s, ENABLE(0), UINT32_MAX);
    qtest_writel(s, ENABLE(32), 0x12345678);
    qtest_writel(s, ENABLE(137), UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, ENABLE(0)), ==, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, ENABLE(32)), ==, 0x12345678);
    g_assert_cmphex(qtest_readl(s, ENABLE(64)), ==, 0);
    g_assert_cmphex(qtest_readl(s, ENABLE(137)), ==, 0x3ff);
    qtest_quit(s);
}

static void level_high(void)
{
    QTestState *s = start(true);

    qtest_writel(s, CONFIG(1), 4);
    input(s, 1, true);
    g_assert_false(qtest_get_irq(s, 1));
    enable(s, 1, true);
    g_assert_true(qtest_get_irq(s, 1));
    input(s, 1, false);
    g_assert_false(qtest_get_irq(s, 1));
    input(s, 1, true);
    g_assert_true(qtest_get_irq(s, 1));
    qtest_quit(s);
}

static void level_low(void)
{
    QTestState *s = start(true);

    input(s, 125, true);
    qtest_writel(s, CONFIG(125), 0);
    enable(s, 125, true);
    g_assert_false(qtest_get_irq(s, 125));
    input(s, 125, false);
    g_assert_true(qtest_get_irq(s, 125));
    enable(s, 125, false);
    g_assert_false(qtest_get_irq(s, 125));
    qtest_quit(s);
}

static void rising_parent(void)
{
    QTestState *s = start(false);

    parent_edge(s, 1);
    qtest_writel(s, CONFIG(1), 6);
    enable(s, 1, true);
    parent_clear(s, 1);
    input(s, 1, true);
    g_assert_true(parent_pending(s, 1));
    parent_clear(s, 1);
    input(s, 1, true);
    g_assert_false(parent_pending(s, 1));
    input(s, 1, false);
    g_assert_false(parent_pending(s, 1));
    input(s, 1, true);
    g_assert_true(parent_pending(s, 1));
    qtest_quit(s);
}

static void falling_parent(void)
{
    QTestState *s = start(false);

    input(s, 94, true);
    parent_edge(s, 94);
    qtest_writel(s, CONFIG(94), 2);
    enable(s, 94, true);
    parent_clear(s, 94);
    input(s, 94, false);
    g_assert_true(parent_pending(s, 94));
    parent_clear(s, 94);
    input(s, 94, true);
    g_assert_false(parent_pending(s, 94));
    input(s, 94, false);
    g_assert_true(parent_pending(s, 94));
    qtest_quit(s);
}

static void dual_parent(void)
{
    QTestState *s = start(false);

    parent_edge(s, 137);
    qtest_writel(s, CONFIG(137), 7);
    enable(s, 137, true);
    parent_clear(s, 137);
    input(s, 137, true);
    g_assert_true(parent_pending(s, 137));
    parent_clear(s, 137);
    input(s, 137, true);
    g_assert_false(parent_pending(s, 137));
    input(s, 137, false);
    g_assert_true(parent_pending(s, 137));
    qtest_quit(s);
}

static void disabled_edge_not_replayed(void)
{
    QTestState *s = start(false);

    parent_edge(s, 1);
    qtest_writel(s, CONFIG(1), 6);
    input(s, 1, true);
    g_assert_false(parent_pending(s, 1));
    enable(s, 1, true);
    g_assert_false(parent_pending(s, 1));
    input(s, 1, false);
    input(s, 1, true);
    g_assert_true(parent_pending(s, 1));
    qtest_quit(s);
}

static void mask_level(void)
{
    QTestState *s = start(true);

    qtest_writel(s, CONFIG(1), 4);
    enable(s, 1, true);
    input(s, 1, true);
    g_assert_true(qtest_get_irq(s, 1));
    enable(s, 1, false);
    g_assert_false(qtest_get_irq(s, 1));
    enable(s, 1, true);
    g_assert_true(qtest_get_irq(s, 1));
    input(s, 1, false);
    g_assert_false(qtest_get_irq(s, 1));
    qtest_quit(s);
}

static void no_configuration_edge(void)
{
    QTestState *s = start(false);

    input(s, 1, true);
    parent_edge(s, 1);
    qtest_writel(s, CONFIG(1), 6);
    enable(s, 1, true);
    parent_clear(s, 1);
    qtest_writel(s, CONFIG(1), 2);
    g_assert_false(parent_pending(s, 1));
    input(s, 1, false);
    g_assert_true(parent_pending(s, 1));
    qtest_quit(s);
}

static void parent_owns_pending(void)
{
    QTestState *s = start(false);

    parent_edge(s, 1);
    qtest_writel(s, CONFIG(1), 6);
    enable(s, 1, true);
    input(s, 1, true);
    g_assert_true(parent_pending(s, 1));
    enable(s, 1, false);
    g_assert_true(parent_pending(s, 1));
    parent_clear(s, 1);
    g_assert_false(parent_pending(s, 1));
    enable(s, 1, true);
    g_assert_false(parent_pending(s, 1));
    qtest_quit(s);
}

static void native_four_range_routes(void)
{
    QTestState *s = start(false);
    const unsigned pins[] = {0, 93, 94, 124, 125, 126, 137};
    unsigned i, j;

    for (i = 0; i < G_N_ELEMENTS(pins); i++) {
        parent_edge(s, pins[i]);
        qtest_writel(s, CONFIG(pins[i]), 6);
        enable(s, pins[i], true);
        parent_clear(s, pins[i]);
    }
    for (i = 0; i < G_N_ELEMENTS(pins); i++) {
        input(s, pins[i], true);
        for (j = 0; j < G_N_ELEMENTS(pins); j++) {
            g_assert_cmpint(parent_pending(s, pins[j]), ==, i == j);
        }
        parent_clear(s, pins[i]);
        input(s, pins[i], false);
        g_assert_false(parent_pending(s, pins[i]));
    }
    qtest_quit(s);
}

static void independent_levels(void)
{
    QTestState *s = start(true);

    qtest_writel(s, CONFIG(0), 4);
    qtest_writel(s, CONFIG(137), 4);
    enable(s, 0, true);
    enable(s, 137, true);
    input(s, 0, true);
    g_assert_true(qtest_get_irq(s, 0));
    g_assert_false(qtest_get_irq(s, 137));
    input(s, 137, true);
    g_assert_true(qtest_get_irq(s, 0));
    g_assert_true(qtest_get_irq(s, 137));
    input(s, 0, false);
    g_assert_false(qtest_get_irq(s, 0));
    g_assert_true(qtest_get_irq(s, 137));
    qtest_quit(s);
}

static void spi_firmware_bitmap(void)
{
    QTestState *s = start(false);

    /* Vendor bitmap indexes GIC SPI, excluding the 32 SGI/PPI interrupts. */
    qtest_writel(s, SPI_CONFIG, 0x80000001);
    qtest_writel(s, SPI_CONFIG + 4 * (481 / 32), 1U << (481 % 32));
    qtest_writel(s, SPI_CONFIG + 4 * (727 / 32), 1U << (727 % 32));
    g_assert_cmphex(qtest_readl(s, SPI_CONFIG), ==, 0x80000001);
    g_assert_cmphex(qtest_readl(s, SPI_CONFIG + 0x3c), ==, 2);
    g_assert_cmphex(qtest_readl(s, SPI_CONFIG + 0x58), ==, 0x800000);
    g_assert_cmphex(qtest_readl(s, SPI_CONFIG + 0x5c), ==, 0);
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 0);
    qtest_quit(s);
}

static void unsupported_type_no_mutation(void)
{
    QTestState *s = start(false);

    qtest_writel(s, CONFIG(1), 6);
    qtest_writel(s, CONFIG(1), 5); /* Not one of the five driver encodings. */
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 6);
    qtest_writel(s, CONFIG(1), 3);
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 6);
    qtest_writel(s, CONFIG(1), 1);
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 6);
    qtest_quit(s);
}

static void unsupported_config_bits_no_mutation(void)
{
    QTestState *s = start(false);

    qtest_writel(s, CONFIG(1), 4);
    /* Bit 3 belongs to the unsupported v3.2+ enable layout. */
    qtest_writel(s, CONFIG(1), 0xe);
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 4);
    qtest_writel(s, CONFIG(1), 0x100006);
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 4);
    qtest_quit(s);
}

static void unsupported_offset_no_alias(void)
{
    QTestState *s = start(false);

    qtest_writel(s, CONFIG(1), 6);
    qtest_writel(s, SPI_CONFIG + 0x3c, 2);
    qtest_writel(s, PDC + 4, UINT32_MAX);
    qtest_writel(s, CONFIG(138), UINT32_MAX);
    qtest_writel(s, PDC + 0x4000, UINT32_MAX);
    /* Raw QTest does not expose MemTxResult, so never assert fake read data. */
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 6);
    g_assert_cmphex(qtest_readl(s, CONFIG(137)), ==, 0);
    g_assert_cmphex(qtest_readl(s, SPI_CONFIG + 0x3c), ==, 2);
    qtest_quit(s);
}

static void controller_reset(void)
{
    QTestState *s = start(true);

    qtest_writel(s, CONFIG(1), 4);
    enable(s, 1, true);
    input(s, 1, true);
    qtest_writel(s, SPI_CONFIG + 0x3c, 2);
    g_assert_true(qtest_get_irq(s, 1));
    qtest_system_reset(s);
    g_assert_cmphex(qtest_readl(s, CONFIG(1)), ==, 0);
    g_assert_cmphex(qtest_readl(s, ENABLE(1)), ==, 0);
    g_assert_cmphex(qtest_readl(s, SPI_CONFIG + 0x3c), ==, 0);
    g_assert_cmphex(qtest_readl(s, PDC + 0x1000), ==, 0x20000);
    g_assert_false(qtest_get_irq(s, 1));
    qtest_writel(s, CONFIG(1), 4);
    enable(s, 1, true);
    g_assert_true(qtest_get_irq(s, 1)); /* External input was retained. */
    qtest_quit(s);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    qtest_add_func("/tb371fc/pdc/version-reference", version_reference);
    qtest_add_func("/tb371fc/pdc/enable-banks", enable_banks);
    qtest_add_func("/tb371fc/pdc/level-high", level_high);
    qtest_add_func("/tb371fc/pdc/level-low", level_low);
    qtest_add_func("/tb371fc/pdc/rising-real-gic", rising_parent);
    qtest_add_func("/tb371fc/pdc/falling-real-gic", falling_parent);
    qtest_add_func("/tb371fc/pdc/dual-real-gic", dual_parent);
    qtest_add_func("/tb371fc/pdc/disabled-edge-not-replayed",
                   disabled_edge_not_replayed);
    qtest_add_func("/tb371fc/pdc/mask-level", mask_level);
    qtest_add_func("/tb371fc/pdc/no-configuration-edge", no_configuration_edge);
    qtest_add_func("/tb371fc/pdc/parent-owns-pending", parent_owns_pending);
    qtest_add_func("/tb371fc/pdc/native-four-range-routes",
                   native_four_range_routes);
    qtest_add_func("/tb371fc/pdc/independent-levels", independent_levels);
    qtest_add_func("/tb371fc/pdc/spi-firmware-bitmap", spi_firmware_bitmap);
    qtest_add_func("/tb371fc/pdc/unsupported-type-no-mutation",
                   unsupported_type_no_mutation);
    qtest_add_func("/tb371fc/pdc/unsupported-config-bits-no-mutation",
                   unsupported_config_bits_no_mutation);
    qtest_add_func("/tb371fc/pdc/unsupported-offset-no-alias",
                   unsupported_offset_no_alias);
    qtest_add_func("/tb371fc/pdc/controller-reset", controller_reset);
    return g_test_run();
}
