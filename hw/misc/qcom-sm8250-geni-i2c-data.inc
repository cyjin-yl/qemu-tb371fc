/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Original-DT enabled I2C instances; no slave device is instantiated. */
/* Facts SHA-256: ef6f3919d1eb65d90a3362742026564acff1ffba553eb09355097217dd9f436f */
static const QcomGeniI2CInstance geni_i2c_instances[] = {
    { .id = 1, .base = UINT64_C(0x00984000), .spi = 602, .wrapper = 0,
      .se_clock = "gcc_qupv3_wrap0_s1_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_0_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_0_s_ahb_clk",
      .se_clock_index = 89, .m_ahb_clock_index = 131,
      .s_ahb_clock_index = 132, .reset_index = 24 },
    { .id = 3, .base = UINT64_C(0x0098c000), .spi = 604, .wrapper = 0,
      .se_clock = "gcc_qupv3_wrap0_s3_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_0_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_0_s_ahb_clk",
      .se_clock_index = 93, .m_ahb_clock_index = 131,
      .s_ahb_clock_index = 132, .reset_index = 24 },
    { .id = 5, .base = UINT64_C(0x00994000), .spi = 606, .wrapper = 0,
      .se_clock = "gcc_qupv3_wrap0_s5_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_0_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_0_s_ahb_clk",
      .se_clock_index = 97, .m_ahb_clock_index = 131,
      .s_ahb_clock_index = 132, .reset_index = 24 },
    { .id = 9, .base = UINT64_C(0x00a84000), .spi = 354, .wrapper = 1,
      .se_clock = "gcc_qupv3_wrap1_s1_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_1_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_1_s_ahb_clk",
      .se_clock_index = 107, .m_ahb_clock_index = 133,
      .s_ahb_clock_index = 134, .reset_index = 25 },
    { .id = 11, .base = UINT64_C(0x00a8c000), .spi = 356, .wrapper = 1,
      .se_clock = "gcc_qupv3_wrap1_s3_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_1_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_1_s_ahb_clk",
      .se_clock_index = 111, .m_ahb_clock_index = 133,
      .s_ahb_clock_index = 134, .reset_index = 25 },
    { .id = 13, .base = UINT64_C(0x00a94000), .spi = 358, .wrapper = 1,
      .se_clock = "gcc_qupv3_wrap1_s5_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_1_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_1_s_ahb_clk",
      .se_clock_index = 115, .m_ahb_clock_index = 133,
      .s_ahb_clock_index = 134, .reset_index = 25 },
    { .id = 15, .base = UINT64_C(0x00884000), .spi = 583, .wrapper = 2,
      .se_clock = "gcc_qupv3_wrap2_s1_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_2_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_2_s_ahb_clk",
      .se_clock_index = 121, .m_ahb_clock_index = 135,
      .s_ahb_clock_index = 136, .reset_index = 26 },
    { .id = 16, .base = UINT64_C(0x00888000), .spi = 584, .wrapper = 2,
      .se_clock = "gcc_qupv3_wrap2_s2_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_2_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_2_s_ahb_clk",
      .se_clock_index = 123, .m_ahb_clock_index = 135,
      .s_ahb_clock_index = 136, .reset_index = 26 },
    { .id = 19, .base = UINT64_C(0x00894000), .spi = 587, .wrapper = 2,
      .se_clock = "gcc_qupv3_wrap2_s5_clk",
      .m_ahb_clock = "gcc_qupv3_wrap_2_m_ahb_clk",
      .s_ahb_clock = "gcc_qupv3_wrap_2_s_ahb_clk",
      .se_clock_index = 129, .m_ahb_clock_index = 135,
      .s_ahb_clock_index = 136, .reset_index = 26 },
};

unsigned qcom_geni_i2c_instance_count(void)
{
    QEMU_BUILD_BUG_ON(ARRAY_SIZE(geni_i2c_instances) !=
                      QCOM_GENI_I2C_INSTANCE_COUNT);
    return ARRAY_SIZE(geni_i2c_instances);
}

const QcomGeniI2CInstance *qcom_geni_i2c_instance_at(unsigned index)
{
    assert(index < ARRAY_SIZE(geni_i2c_instances));
    return &geni_i2c_instances[index];
}

const QcomGeniI2CInstance *qcom_geni_i2c_instance_by_id(uint32_t id)
{
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(geni_i2c_instances); i++) {
        if (geni_i2c_instances[i].id == id) {
            return &geni_i2c_instances[i];
        }
    }
    return NULL;
}
