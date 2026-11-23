/*
 * SPDX-License-Identifier: GPL-2.0-or-later
 *
 * Functional TLMM checks using real QTest input wires and IRQ/output wires.
 * Golden addresses/encodings are independently taken from Linux v6.12.114
 * sm8250.dtsi, pinctrl-sm8250.c and pinctrl-msm.c (see tb371fc/docs/tlmm.md).
 * No generated model map header is included.
 */
#include "qemu/osdep.h"
#include "libqtest.h"

#define PIN0 UINT64_C(0x0f500000)
#define PIN4 UINT64_C(0x0f904000)
#define PIN133 UINT64_C(0x0f185000)
#define PIN179 UINT64_C(0x0f1b3000)
#define UFS UINT64_C(0x0f5b8000)
#define SDC2 UINT64_C(0x0f9b7000)
#define PATH "/machine/tlmm"

#define IO 4
#define CFG 8
#define STATUS 12
#define RISING 0x77
#define FALLING 0x7b
#define BOTH 0x7f
#define HIGH 0x73
#define LOW 0x71

static QTestState *start_group(const char *group)
{
    QTestState *s = qtest_init(
        "-machine tb371fc,ufs-enabled=off -m 128M -smp 8 "
        "-display none -serial null");

    qtest_irq_intercept_out_named(s, PATH, group);
    return s;
}

static QTestState *start(void)
{
    return start_group("sysbus-irq");
}

static void input(QTestState *s, unsigned pin, bool level)
{
    qtest_set_irq_in(s, PATH, "gpio-in", pin, level);
}

static void assert_pending(QTestState *s, uint64_t base, bool pending,
                            bool irq)
{
    g_assert_cmphex(qtest_readl(s, base + STATUS), ==, pending);
    g_assert_cmpint(qtest_get_irq(s, 0), ==, irq);
}

static void reset_registers(void)
{
    QTestState *s = start();
    const uint64_t pins[] = {PIN0, PIN4, PIN133, PIN179};
    unsigned i, off;

    for (i = 0; i < G_N_ELEMENTS(pins); i++) {
        for (off = 0; off <= STATUS; off += 4) {
            g_assert_cmphex(qtest_readl(s, pins[i] + off), ==, 0);
        }
    }
    g_assert_cmphex(qtest_readl(s, UFS), ==, 0);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 0);
    g_assert_cmphex(qtest_readl(s, SDC2), ==, 0);
    g_assert_false(qtest_get_irq(s, 0));
    qtest_quit(s);
}

static void tile_isolation(void)
{
    QTestState *s = start();
    const uint64_t pins[] = {PIN0, PIN4, PIN133, PIN179};
    const uint32_t values[] = {0x43, 0x81, 0xc2, 0x101};
    unsigned i;

    for (i = 0; i < G_N_ELEMENTS(pins); i++) {
        qtest_writel(s, pins[i], values[i]);
    }
    /* GPIO 0 is in SOUTH, not at its apparent WEST or NORTH offset. */
    qtest_writel(s, 0x0f100000, 0x3ff);
    qtest_writel(s, 0x0f900000, 0x3ff);
    for (i = 0; i < G_N_ELEMENTS(pins); i++) {
        g_assert_cmphex(qtest_readl(s, pins[i]), ==, values[i]);
    }
    /* No ordinary GPIO 180 at SOUTH+180*0x1000. */
    qtest_writel(s, 0x0f5b4000, 0x3ff);
    g_assert_cmphex(qtest_readl(s, UFS), ==, 0);
    qtest_quit(s);
}

static void input_read_only(void)
{
    QTestState *s = start();

    input(s, 0, true);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 1);
    qtest_writel(s, PIN0 + IO, 0);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 1);
    qtest_writel(s, PIN0 + IO, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 3);
    input(s, 0, false);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 2);
    qtest_quit(s);
}

static void output_latch(void)
{
    QTestState *s = start_group("gpio-out");

    qtest_writel(s, PIN0 + IO, 2);
    g_assert_true(qtest_get_irq(s, 0));
    g_assert_false(qtest_get_irq(s, 4));
    qtest_writel(s, PIN4 + IO, 2);
    g_assert_true(qtest_get_irq(s, 4));
    qtest_writel(s, PIN0 + IO, 0);
    g_assert_false(qtest_get_irq(s, 0));
    g_assert_true(qtest_get_irq(s, 4));
    qtest_quit(s);
}

static void output_enable(void)
{
    QTestState *s = start_group("gpio-oe");

    qtest_writel(s, PIN0, 0x200);
    g_assert_true(qtest_get_irq(s, 0));
    qtest_writel(s, PIN0, 0x204); /* Alternate function 1, not GPIO drive. */
    g_assert_false(qtest_get_irq(s, 0));
    qtest_writel(s, PIN0, 0x200);
    g_assert_true(qtest_get_irq(s, 0));
    qtest_writel(s, PIN0, 0);
    g_assert_false(qtest_get_irq(s, 0));
    qtest_quit(s);
}

static void digital_output_readback(void)
{
    QTestState *s = start();

    input(s, 0, false);
    qtest_writel(s, PIN0 + IO, 2);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 2);
    qtest_writel(s, PIN0, 0x200);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 3);
    input(s, 0, true);
    input(s, 0, false);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 3);
    qtest_writel(s, PIN0, 0);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 2);
    qtest_quit(s);
}

static void register_masks(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, PIN0), ==, 0x3ff);
    qtest_writel(s, PIN0 + CFG, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, PIN0 + CFG), ==, 0xff);
    qtest_writel(s, PIN0 + IO, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 2);
    qtest_writel(s, PIN0 + STATUS, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, PIN0 + STATUS), ==, 0);
    qtest_writel(s, PIN0 + 0x10, UINT32_MAX);
    /* QTest raw reads do not expose MemTxResult; test no state alias. */
    g_assert_cmphex(qtest_readl(s, PIN0), ==, 0x3ff);
    qtest_quit(s);
}

static void rising_edge(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, RISING);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    input(s, 0, true);
    assert_pending(s, PIN0, false, false);
    input(s, 0, false);
    assert_pending(s, PIN0, false, false);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    qtest_quit(s);
}

static void falling_edge(void)
{
    QTestState *s = start();

    input(s, 0, true);
    qtest_writel(s, PIN0 + CFG, FALLING);
    assert_pending(s, PIN0, false, false);
    input(s, 0, false);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    input(s, 0, true);
    assert_pending(s, PIN0, false, false);
    input(s, 0, false);
    assert_pending(s, PIN0, true, true);
    qtest_quit(s);
}

static void both_edges(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, BOTH);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    input(s, 0, true);
    assert_pending(s, PIN0, false, false);
    input(s, 0, false);
    assert_pending(s, PIN0, true, true);
    qtest_quit(s);
}

static void level_high(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, HIGH);
    assert_pending(s, PIN0, false, false);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, true, true); /* Present level re-latches. */
    input(s, 0, false);
    assert_pending(s, PIN0, true, true); /* Latched until ACK. */
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_quit(s);
}

static void level_low(void)
{
    QTestState *s = start();

    input(s, 0, true);
    qtest_writel(s, PIN0 + CFG, LOW);
    assert_pending(s, PIN0, false, false);
    input(s, 0, false);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, true, true);
    input(s, 0, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_quit(s);
}

static void write_zero_ack(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, RISING);
    input(s, 0, true);
    qtest_writel(s, PIN0 + STATUS, 1);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_writel(s, PIN0 + STATUS, UINT32_MAX);
    assert_pending(s, PIN0, false, false); /* Writes cannot inject status. */
    qtest_quit(s);
}

static void masked_edge(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, RISING & ~1);
    input(s, 0, true);
    assert_pending(s, PIN0, true, false);
    qtest_writel(s, PIN0 + CFG, RISING);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_quit(s);
}

static void raw_status_enable(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, RISING & ~0x10);
    input(s, 0, true);
    input(s, 0, false);
    input(s, 0, true);
    assert_pending(s, PIN0, false, false);
    qtest_writel(s, PIN0 + CFG, RISING);
    assert_pending(s, PIN0, false, false); /* No invented historical edge. */
    input(s, 0, false);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + CFG, RISING & ~0x10);
    assert_pending(s, PIN0, true, true); /* RAW_EN does not clear pending. */
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_quit(s);
}

static void irq_target(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, RISING - 0x40); /* Target 1, not KPSS 3. */
    input(s, 0, true);
    assert_pending(s, PIN0, true, false);
    qtest_writel(s, PIN0 + CFG, RISING);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + CFG, RISING & ~0xe0);
    assert_pending(s, PIN0, true, false);
    qtest_writel(s, PIN0 + CFG, RISING);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_quit(s);
}

static void two_pending_pins(void)
{
    QTestState *s = start();

    input(s, 4, true);
    qtest_writel(s, PIN0 + CFG, RISING);
    qtest_writel(s, PIN4 + CFG, FALLING);
    input(s, 0, true);
    input(s, 4, false);
    assert_pending(s, PIN0, true, true);
    assert_pending(s, PIN4, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, true);
    assert_pending(s, PIN4, true, true);
    qtest_writel(s, PIN4 + STATUS, 0);
    assert_pending(s, PIN4, false, false);
    qtest_quit(s);
}

static void masked_level(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, HIGH);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    /* Linux masks both RAW_STATUS_EN and INTR_ENABLE for level interrupts. */
    qtest_writel(s, PIN0 + CFG, HIGH & ~0x11);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_writel(s, PIN0 + CFG, HIGH);
    assert_pending(s, PIN0, true, true);
    qtest_quit(s);
}

static void no_configuration_edge(void)
{
    QTestState *s = start();

    input(s, 0, true);
    qtest_writel(s, PIN0 + CFG, RISING);
    assert_pending(s, PIN0, false, false);
    qtest_writel(s, PIN0 + CFG, FALLING);
    assert_pending(s, PIN0, false, false);
    qtest_writel(s, PIN0 + CFG, BOTH);
    assert_pending(s, PIN0, false, false);
    input(s, 0, false);
    assert_pending(s, PIN0, true, true);
    qtest_quit(s);
}

static void dedicated_ufs_reset(void)
{
    QTestState *s = start_group("ufs-reset");

    qtest_writel(s, UFS, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, UFS), ==, 0x1f);
    qtest_writel(s, UFS + IO, 2);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 0);
    g_assert_false(qtest_get_irq(s, 0));
    qtest_writel(s, UFS + IO, 3);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 1);
    g_assert_true(qtest_get_irq(s, 0));
    qtest_writel(s, UFS + STATUS, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 1);
    g_assert_cmphex(qtest_readl(s, UFS), ==, 0x1f);
    qtest_writel(s, UFS + IO, 0);
    g_assert_false(qtest_get_irq(s, 0));
    qtest_quit(s);
}

static void boot_ufs_reset_handoff(void)
{
    QTestState *s = qtest_init(
        "-machine tb371fc,ufs-enabled=off -m 128M -smp 8 "
        "-global qcom-tb371fc-tlmm.boot-ufs-reset-high=on "
        "-display none -serial null");

    qtest_irq_intercept_out_named(s, PATH, "ufs-reset");
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 1);
    qtest_writel(s, UFS + IO, 0);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 0);
    qtest_system_reset(s);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 1);
    g_assert_true(qtest_get_irq(s, 0));
    qtest_quit(s);
}

static void sdc2_pad_fields(void)
{
    QTestState *s = start();

    qtest_writel(s, SDC2, UINT32_MAX);
    g_assert_cmphex(qtest_readl(s, SDC2), ==, 0xdfff);
    qtest_writel(s, SDC2, 0x2000); /* Reserved bit 13. */
    g_assert_cmphex(qtest_readl(s, SDC2), ==, 0);
    qtest_writel(s, SDC2, 0xc000); /* Clock pull only. */
    g_assert_cmphex(qtest_readl(s, SDC2), ==, 0xc000);
    g_assert_cmphex(qtest_readl(s, PIN179), ==, 0);
    g_assert_cmphex(qtest_readl(s, UFS), ==, 0);
    g_assert_false(qtest_get_irq(s, 0));
    qtest_quit(s);
}

static void controller_reset(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0, 0x243);
    qtest_writel(s, PIN0 + IO, 2);
    qtest_writel(s, PIN4 + CFG, RISING);
    input(s, 4, true);
    qtest_writel(s, UFS, 0x1f);
    qtest_writel(s, UFS + IO, 1);
    qtest_writel(s, SDC2, 0xdfff);
    assert_pending(s, PIN4, true, true);
    qtest_system_reset(s);
    g_assert_cmphex(qtest_readl(s, PIN0), ==, 0);
    g_assert_cmphex(qtest_readl(s, PIN0 + IO), ==, 0);
    g_assert_cmphex(qtest_readl(s, PIN4 + CFG), ==, 0);
    /* External input remains. */
    g_assert_cmphex(qtest_readl(s, PIN4 + IO), ==, 1);
    assert_pending(s, PIN4, false, false);
    g_assert_cmphex(qtest_readl(s, UFS), ==, 0);
    g_assert_cmphex(qtest_readl(s, UFS + IO), ==, 0);
    g_assert_cmphex(qtest_readl(s, SDC2), ==, 0);
    qtest_quit(s);
}

static void pending_survives_configuration(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN0 + CFG, RISING);
    input(s, 0, true);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + CFG, FALLING);
    assert_pending(s, PIN0, true, true);
    qtest_writel(s, PIN0 + STATUS, 0);
    assert_pending(s, PIN0, false, false);
    qtest_quit(s);
}

static void last_gpio(void)
{
    QTestState *s = start();

    qtest_writel(s, PIN179 + CFG, RISING);
    input(s, 179, true);
    g_assert_cmphex(qtest_readl(s, PIN179 + IO), ==, 1);
    assert_pending(s, PIN179, true, true);
    assert_pending(s, PIN0, false, true);
    qtest_writel(s, PIN179 + STATUS, 0);
    assert_pending(s, PIN179, false, false);
    qtest_quit(s);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    qtest_add_func("/tb371fc/tlmm/reset-registers", reset_registers);
    qtest_add_func("/tb371fc/tlmm/tile-isolation", tile_isolation);
    qtest_add_func("/tb371fc/tlmm/input-read-only", input_read_only);
    qtest_add_func("/tb371fc/tlmm/output-latch", output_latch);
    qtest_add_func("/tb371fc/tlmm/output-enable", output_enable);
    qtest_add_func("/tb371fc/tlmm/digital-output-readback",
                   digital_output_readback);
    qtest_add_func("/tb371fc/tlmm/register-masks", register_masks);
    qtest_add_func("/tb371fc/tlmm/rising-edge", rising_edge);
    qtest_add_func("/tb371fc/tlmm/falling-edge", falling_edge);
    qtest_add_func("/tb371fc/tlmm/both-edges", both_edges);
    qtest_add_func("/tb371fc/tlmm/level-high", level_high);
    qtest_add_func("/tb371fc/tlmm/level-low", level_low);
    qtest_add_func("/tb371fc/tlmm/write-zero-ack", write_zero_ack);
    qtest_add_func("/tb371fc/tlmm/masked-edge", masked_edge);
    qtest_add_func("/tb371fc/tlmm/raw-status-enable", raw_status_enable);
    qtest_add_func("/tb371fc/tlmm/irq-target", irq_target);
    qtest_add_func("/tb371fc/tlmm/two-pending-pins", two_pending_pins);
    qtest_add_func("/tb371fc/tlmm/masked-level", masked_level);
    qtest_add_func("/tb371fc/tlmm/no-configuration-edge",
                   no_configuration_edge);
    qtest_add_func("/tb371fc/tlmm/dedicated-ufs-reset", dedicated_ufs_reset);
    qtest_add_func("/tb371fc/tlmm/boot-ufs-reset-handoff", boot_ufs_reset_handoff);
    qtest_add_func("/tb371fc/tlmm/sdc2-pad-fields", sdc2_pad_fields);
    qtest_add_func("/tb371fc/tlmm/controller-reset", controller_reset);
    qtest_add_func("/tb371fc/tlmm/pending-survives-configuration",
                   pending_survives_configuration);
    qtest_add_func("/tb371fc/tlmm/last-gpio", last_gpio);
    return g_test_run();
}
