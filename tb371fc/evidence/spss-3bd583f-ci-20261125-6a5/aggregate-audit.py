#!/usr/bin/env python3
"""Read-only aggregate and VIDEOCC audit of the SPSS integration CI ZIP.

No extraction, network, guest execution or repository imports are performed.
The embedded acceptance plan is bound to the reviewed SPSS integration source.
The independent validators and wire encoders do not call the test producers.
QMP and QTest have separate logs without a shared timestamp: cross-channel
ordering is tied to the pinned harness, its counters and the QTest epoch marker.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import struct
import sys
import traceback
from zipfile import ZipFile

REVIEWED_HEAD = '3bd583f2079634d01c12160ab3e3f0c5a3e47e86'
RUNNER_SHA256 = '22d0482115a1cb252161dce33d79355d93a435b4b8f4ca8df129e7bb3c721117'
VIDEOCC_TEST_SHA256 = 'bd62eeb39964815a8b1bc921857992ea0a537eefb57ae536bb6d598dbcbff620'
SOURCE_REVISION = "94304e12a3509eac7b3716bf574ce0b1fcaacbae"
SOURCE_HASHES = {
    "drivers/clk/clk-divider.c": "df5a4f90c2352429307b97c317010e7424a88c02a90a253fad27baf45fd045b2",
    "drivers/clk/qcom/videocc-kona.c": "33a0f85c8d9efae1cc97f1916b0742a533ca0c79e35516b485c61b5eddd909e5",
    "include/dt-bindings/clock/qcom,videocc-kona.h": "0f51753e9eb497399d3c2d9a362e6d3ee7baf4f789b6e6628a7911b00f89d774",
    "drivers/clk/qcom/clk-alpha-pll.c": "cdfb050d522069590b780819e5ca3c291c7886e63c38330f61726457606e2fac",
    "drivers/clk/qcom/clk-rcg2.c": "7eb6e9f83e84a6596056f0d0aab88b7ba8f565d5a85f85a7a9893c624852be16",
    "drivers/clk/qcom/clk-branch.c": "375830dc9cd0ecbc12ea018eee1d260d8ba724dc52f1ac8b3589a07bc75d08aa",
    "drivers/clk/qcom/clk-regmap-divider.c": "55b2484e485b897df3636db7944d29c1b4c01e0d8fd145f6075eff61c7bc1155",
    "drivers/clk/qcom/clk-branch.h": "da132bd1a093a650abf64073a4e695719e0612b67bb0166726737e0f2a3332d5",
    "drivers/clk/qcom/reset.c": "db88de0848c75cecff57623b94d63993a8f869a27686df4bd7b7be0765317f11",
    "drivers/clk/qcom/clk-rpmh.c": "75537429fad6cac6aeb7b27f8ff4cd8e37f28f14740155e68c6c486f924f3c76",
}
GENERATED_HASHES = {
    "hw/misc/qcom-sm8250-videocc-data.inc": "7e98e8885f0cca0781063890c7e0011dac370f60c4ebb8e885f31fa1fd84cbc8",
    "tb371fc/docs/videocc-sm8250-registers.json": "ffbfd3bacb39d4fc22780b42a161b64142a784fda05b595bbb933c117ab44a14",
}
PLAN = json.loads('{"components":[{"name":"ci-verdict-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"smem-observer-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"aop-observer-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"board-profile-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"machine-validator-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"dt-inventory-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"hardware-sysfs-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"guest-result-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"console-boot-verdict-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"tlmm-map-unit","kind":"exit","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"gcc-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"source_pins_verified":true},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"camcc-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-camcc-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":8},"tap_count":null,"guest_errno":false},{"name":"dispcc-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-dispcc-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":9},"tap_count":null,"guest_errno":false},{"name":"videocc-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-videocc-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":10},"tap_count":null,"guest_errno":false},{"name":"dsi-pll-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-dsi-pll-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":13},"tap_count":null,"guest_errno":false},{"name":"dp-pll-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-dp-pll-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":17},"tap_count":null,"guest_errno":false},{"name":"llcc-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-llcc-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":8},"tap_count":null,"guest_errno":false},{"name":"spss-irq-mask-source-generation","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{"schema":"tb371fc-spss-irq-mask-generation-v1","source_pins_verified":true},"expected_lengths":{"inputs":2},"tap_count":null,"guest_errno":false},{"name":"live-machine-profile","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"geni-registers","kind":"stdout","verdict":"success","expected_count":14,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"geni-migration","kind":"stdout","verdict":"success","expected_count":6,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"gcc-registers","kind":"stdout","verdict":"success","expected_count":19,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"gcc-migration","kind":"stdout","verdict":"success","expected_count":5,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"gcc-cpuss-branch","kind":"file","verdict":"passed","expected_count":11,"count_field":"checks","expected_values":{"expected_checks":11,"vmstate_version":3,"external_parent_inputs":83,"guest_cpu_executed":false,"native_kernel_probe_observed":false},"expected_lengths":{"migrations":2},"tap_count":null,"guest_errno":false},{"name":"gcc-usb-pipe-mux","kind":"file","verdict":"passed","expected_count":15,"count_field":"checks","expected_values":{"expected_checks":15,"vmstate_version":3,"external_parent_inputs":83,"expected_completed_migrations":1,"expected_rejected_migrations":1,"completed_migrations":1,"rejected_migration_attempts":1,"binary_unchanged":true,"guest_cpu_executed":false,"usb_phy_emulated":false,"native_kernel_probe_observed":false},"expected_lengths":{"migrations":1,"rejected_migrations":1},"tap_count":null,"guest_errno":false},{"name":"geni-clock-reset","kind":"stdout","verdict":"success","expected_count":6,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"geni-clock-migration","kind":"stdout","verdict":"success","expected_count":4,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"mpm2-sleep-counter","kind":"file","verdict":"passed","expected_count":17,"count_field":"checks","expected_values":{"expected_checks":17,"expected_completed_migrations":2,"expected_corrupted_stream_rejections":1,"vmstate_version":1,"clock_frequency_hz":32768,"mmio_register_count":1,"mmio_window_bytes":4,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"corrupted_stream_rejections":1},"tap_count":null,"guest_errno":false},{"name":"counter-arithmetic","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{"cases":1848,"errors":[]},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"mmio-timer-exact","kind":"file","verdict":"passed","expected_count":10,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"mmio-timer-legacy","kind":"file","verdict":"passed","expected_count":10,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-counter-exact","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{"cpu_timer_probe_passed":true},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-counter-legacy","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{"cpu_timer_probe_passed":true},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"mmio-timer-migration","kind":"file","verdict":"passed","expected_count":4,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"mmio-timer-legacy-migration","kind":"file","verdict":"passed","expected_count":4,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"reject-exact-to-legacy","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"reject-legacy-to-exact","kind":"file","verdict":"passed","expected_count":2,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-timer-migration","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-timer-control","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-irq-realtime","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-irq-exact-icount","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"cpu-irq-legacy-icount","kind":"file","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"smmuv2-c-qtest","kind":"tap","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":23,"guest_errno":false},{"name":"tlmm-c-qtest","kind":"tap","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":25,"guest_errno":false},{"name":"pdc-c-qtest","kind":"tap","verdict":"passed","expected_count":null,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":18,"guest_errno":false},{"name":"tbu-reference-ecats","kind":"file","verdict":"all_passed","expected_count":17,"count_field":"checks","expected_values":{},"expected_lengths":{"migrations":1,"rejections":6},"tap_count":null,"guest_errno":false},{"name":"rpmh-reference","kind":"stdout","verdict":"success","expected_count":22,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"rpmh-reference-migration","kind":"stdout","verdict":"success","expected_count":5,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"rpmh-display","kind":"stdout","verdict":"success","expected_count":11,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"rpmh-display-migration","kind":"stdout","verdict":"success","expected_count":6,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"rpmh-wait-mask-reference","kind":"file","verdict":"success","expected_count":17,"count_field":"checks","expected_values":{"schema":"tb371fc-rpmh-wait-mask-v1","success":true,"expected_checks":17,"expected_completed_migrations":1,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"vmstate_version":3,"vmstate_minimum_version":2,"subsection_version":1,"subsection_payload_bytes":24,"main_payload_bytes_with_default_clock":19424,"commands_per_tcs":16,"voter_count":4,"apps_tcs_per_drv":8,"display_tcs_count":2,"wait_command_mask":65535,"strict_and_wait_reference_main_v3_payload_identical":true,"old_rpmh_tests_changed":false,"old_rpmh_test_blobs_match":true,"silicon_reserved_bit_semantics_proved":false,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"guest_image_patched":false,"android_boot_passed":false,"binary_unchanged":true,"diagnostic_counters_are_model_observations":true,"reference_policy_auto_enabled":false},"expected_lengths":{"migrations":1,"rejected_migrations":2,"corrupted_stream_rejections":2,"source_streams":3},"tap_count":null,"guest_errno":false},{"name":"qsee-notification","kind":"stdout","verdict":"success","expected_count":12,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"qsee-notification-migration","kind":"stdout","verdict":"success","expected_count":6,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"tcsr-mutex","kind":"file","verdict":"passed","expected_count":24,"count_field":"checks","expected_values":{"all_passed":true,"expected_checks":24,"native_reset_observed":false,"external_processor_emulated":false},"expected_lengths":{"migrations":3},"tap_count":null,"guest_errno":false},{"name":"refgen-request","kind":"file","verdict":"passed","expected_count":16,"count_field":"checks","expected_values":{"all_passed":true,"success":true,"expected_checks":16,"migration_expected_checks":6,"socket_directories_retained":true},"expected_lengths":{"migrations":6},"tap_count":null,"guest_errno":false},{"name":"gpucc-sm8250","kind":"file","verdict":"passed","expected_count":30,"count_field":"checks","expected_values":{"expected_checks":30,"expected_completed_migrations":2,"expected_rejected_migrations":2,"vmstate_version":1,"register_count":36,"sparse_window_count":7,"branch_count":15,"reset_count":5,"input_clock_count":15,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"gpu_execution_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2},"tap_count":null,"guest_errno":false},{"name":"npucc-sm8250","kind":"file","verdict":"passed","expected_count":37,"count_field":"checks","expected_values":{"expected_checks":37,"expected_completed_migrations":2,"expected_rejected_migrations":2,"vmstate_version":1,"register_count":95,"sparse_window_count":26,"branch_count":33,"reset_count":7,"input_clock_count":12,"pll_count":3,"rcg_count":6,"output_clock_count":45,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"npu_execution_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2},"tap_count":null,"guest_errno":false},{"name":"camcc-sm8250-reference","kind":"file","verdict":"passed","expected_count":41,"count_field":"checks","expected_values":{"schema":"tb371fc-camcc-sm8250-v1","expected_checks":41,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"expected_unmodified_streams":4,"expected_stream_files":6,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":2579,"reference_identity":1128353074,"physical_base":181403648,"resource_bytes":65536,"register_count":236,"sparse_window_count":74,"mapped_bytes":944,"output_clock_count":111,"input_clock_count":5,"connected_input_clock_count":2,"unknown_external_input_count":3,"pll_count":5,"lucid_pll_count":4,"zonda_pll_count":1,"postdivider_count":6,"rcg_count":33,"mnd_rcg_count":10,"mnd_width":8,"branch_count":66,"reset_count":6,"read_only_divider_count":1,"modeled_timer_count":1,"camera_vmstate_version":1,"camera_vmstate_payload_bytes":194,"reference_enabled_by_default":false,"remote_vote_gpio_assumed":false,"postdivider_zero_assumed_divide_one":false,"sbi_divider_rounds_up":true,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"camera_execution_observed":false,"analog_pll_behavior_modeled":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":2,"migration_sources":4,"actual_stream_files":6},"tap_count":null,"guest_errno":false},{"name":"dispcc-sm8250-reference","kind":"file","verdict":"passed","expected_count":42,"count_field":"checks","expected_values":{"schema":"tb371fc-dispcc-sm8250-v1","expected_checks":42,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":3,"expected_unmodified_streams":4,"expected_stream_files":7,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":1676,"reference_identity":1145656113,"physical_base":183500800,"resource_bytes":131072,"register_count":130,"sparse_window_count":39,"mapped_bytes":520,"output_clock_count":61,"input_clock_count":17,"connected_input_clock_count":1,"unknown_external_input_count":16,"pll_count":2,"lucid_pll_count":2,"postdivider_count":0,"rcg_count":22,"mnd_rcg_count":6,"mnd_width8_count":2,"mnd_width16_count":4,"hw_control_rcg_count":3,"branch_count":32,"divider_count":5,"writable_divider_count":2,"read_only_divider_count":3,"reset_count":2,"remote_vote_count":2,"misc_word_count":1,"modeled_timer_count":1,"mdss_vmstate_version":1,"mdss_vmstate_payload_bytes":64,"reference_enabled_by_default":false,"pll1_even_alias_assumed":false,"critical_xo_parent_assumed":false,"generic_dividers_round_up":true,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"display_execution_observed":false,"analog_pll_behavior_modeled":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":3,"migration_sources":4,"actual_stream_files":7},"tap_count":null,"guest_errno":false},{"name":"videocc-sm8250-reference","kind":"file","verdict":"passed","expected_count":41,"count_field":"checks","expected_values":{"schema":"tb371fc-videocc-sm8250-v1","expected_checks":41,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"expected_unmodified_streams":4,"expected_stream_files":6,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":474,"reference_identity":1447641906,"physical_base":180289536,"resource_bytes":65536,"register_count":55,"sparse_window_count":26,"mapped_bytes":220,"output_clock_count":19,"input_clock_count":4,"connected_input_clock_count":2,"unknown_external_input_count":2,"pll_count":2,"lucid_pll_count":2,"postdivider_count":0,"rcg_count":5,"mnd_rcg_count":0,"hw_control_rcg_count":3,"branch_count":8,"divider_count":4,"writable_divider_count":0,"read_only_divider_count":4,"reset_count":7,"remote_vote_count":4,"shared_branch_ares_word_count":2,"modeled_timer_count":1,"video_gdsc_vmstate_version":1,"video_gdsc_vmstate_payload_bytes":142,"video_gdsc_domain_count":4,"reference_enabled_by_default":false,"pll1_v2_fixup_replayed":true,"ares_implies_clock_gating_assumed":false,"generic_dividers_round_up":true,"actual_compatible":"qcom,videocc-kona-v2","guest_cpu_executed":false,"native_kernel_probe_observed":false,"video_execution_observed":false,"analog_pll_behavior_modeled":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":2,"migration_sources":4,"actual_stream_files":6},"tap_count":null,"guest_errno":false},{"name":"dsi-pll-7nm-reference","kind":"file","verdict":"passed","expected_count":36,"count_field":"checks","expected_values":{"schema":"tb371fc-dsi-pll-7nm-v1","expected_checks":36,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":4,"expected_unmodified_streams":4,"expected_stream_files":8,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":2926,"reference_identity":1146308916,"physical_base":183059456,"bank_stride":8192,"bank_count":2,"bank_container_bytes":2048,"words_per_bank":154,"register_count":308,"source_variant":1025,"physical_pll_count":2,"sparse_window_count":16,"mapped_bytes":1232,"output_clock_count":52,"input_clock_count":1,"connected_input_clock_count":1,"dispcc_carrier_connection_count":4,"required_programmed_words_per_bank":128,"status_zero_only_words_per_bank":15,"bias_settle_ns":250,"lock_reference_cycles":64,"lock_reference_ns_ceiling":3334,"generic_dividers_round_up":true,"fixed_factors_round_down":true,"frequency_checks_use_exact_clock_period":true,"runtime_xo_change_tested":true,"first_observation_qom_get_count":1163,"stock_compiled_dsi_source_identity_claimed":false,"mdss_vmstate_payload_bytes":64,"reference_enabled_by_default":false,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"analog_pll_behavior_modeled":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":4,"migration_sources":4,"actual_stream_files":8},"tap_count":null,"guest_errno":false},{"name":"dp-pll-7nm-reference","kind":"file","verdict":"passed","expected_count":36,"count_field":"checks","expected_values":{"schema":"tb371fc-dp-pll-7nm-v1","suite":"dp-pll-7nm-reference","expected_checks":36,"expected_completed_migrations":4,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":4,"expected_unmodified_streams":5,"expected_stream_files":9,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":791,"reference_identity":1146107697,"source_variant":"qcom,mdss_dp_pll_7nm","physical_base":143564800,"region_count":4,"resource_bytes_per_region":512,"register_count":73,"sparse_window_count":42,"mapped_bytes":292,"output_clock_count":6,"input_clock_count":1,"connected_input_clock_count":1,"dispcc_carrier_connection_count":2,"timer_count":1,"ccf_rate_scale":1000,"required_programmed_words":58,"readonly_status_words":3,"pll_reference_cycles":64,"phy_reference_cycles":64,"pll_reference_ns_ceiling":3334,"phy_reference_ns_ceiling":3334,"late_phy_wait_ns":2000000,"mdss_vmstate_payload_bytes":64,"mdss_vmstate_version":1,"first_observation_qom_get_count":304,"reference_enabled_by_default":false,"frequency_checks_use_exact_clock_period":true,"runtime_xo_change_tested":true,"stock_compiled_dp_source_identity_claimed":false,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"analog_pll_behavior_modeled":false,"aux_protocol_modeled":false,"link_training_modeled":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"checks":36,"migrations":4,"rejected_migrations":2,"corrupted_stream_rejections":4,"migration_sources":5,"actual_stream_files":9},"tap_count":null,"guest_errno":false},{"name":"llcc-sm8250-reference","kind":"file","verdict":"passed","expected_count":32,"count_field":"checks","expected_values":{"schema":"tb371fc-llcc-sm8250-v1","suite":"llcc-sm8250-reference","expected_checks":32,"expected_completed_migrations":4,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":4,"expected_unmodified_streams":5,"expected_stream_files":9,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":783,"reference_identity":1280066354,"physical_base":153092096,"broadcast_base":157286400,"region_count":2,"banks_resource_bytes":1900544,"broadcast_resource_bytes":327680,"bank_count":4,"bank_stride":524288,"bank_resource_bytes":327680,"slice_count":16,"register_count":71,"mapped_word_count":349,"sparse_window_count":139,"mapped_bytes":1396,"timer_count":1,"input_clock_count":0,"output_clock_count":0,"provider_count":0,"transition_delay_ns":1000,"first_observation_qom_get_count":313,"reference_enabled_by_default":false,"bank_geometry_is_reference":true,"physical_bank_count_measured":false,"hardware_version_known":false,"memory_controller_count_known":false,"cache_data_modeled":false,"cache_performance_modeled":false,"ecc_error_injection_modeled":false,"runtime_provider_revocation_tested":false,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"checks":32,"migrations":4,"rejected_migrations":2,"corrupted_stream_rejections":4,"migration_sources":5,"actual_stream_files":9},"tap_count":null,"guest_errno":false},{"name":"spss-irq-mask-sm8250-reference","kind":"file","verdict":"passed","expected_count":21,"count_field":"checks","expected_values":{"schema":"tb371fc-spss-irq-mask-sm8250-v1","suite":"spss-irq-mask-sm8250-reference","expected_checks":21,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":6,"expected_unmodified_streams":4,"expected_stream_files":10,"vmstate_version":1,"vmstate_minimum_version":1,"vmstate_payload_bytes":25,"reference_identity":1397771569,"physical_base":25694248,"register_count":1,"sparse_window_count":1,"mapped_bytes":4,"first_observation_qom_get_count":18,"irq_output_count":0,"input_clock_count":0,"output_clock_count":0,"timer_count":0,"reference_enabled_by_default":false,"readback_supported":false,"ready_modeled":false,"firmware_modeled":false,"pas_modeled":false,"hardware_reset_value_known":false,"programmed_zero_distinct_from_unknown":true,"source_probe_mask":4244635647,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"checks":21,"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":6,"migration_sources":4,"actual_stream_files":10},"tap_count":null,"guest_errno":false},{"name":"noc-qos","kind":"file","verdict":"passed","expected_count":20,"count_field":"checks","expected_values":{"expected_checks":20,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"vmstate_version":1,"port_count":47,"register_count":188,"sparse_window_count":188,"mapped_bytes":752,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2},"tap_count":null,"guest_errno":false},{"name":"noc-sbm-reference","kind":"file","verdict":"passed","expected_count":28,"count_field":"checks","expected_values":{"schema":"tb371fc-noc-sbm-v1","expected_checks":28,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"vmstate_version":1,"vmstate_payload_bytes_per_device":152,"device_count":1,"port_count":8,"register_count":3,"sparse_window_count":3,"mapped_bytes":12,"reference_latency_ns":1000,"supported_port_mask":255,"reference_boot_disconnected_mask":0,"reference_profile_is_default":false,"default_profile_leaves_mmio_unmapped":true,"diagnostic_counters_are_hardware":false,"hardware_latency_measured":false,"traffic_blocking_implemented":false,"traffic_drain_observed":false,"clock_control_implemented":false,"irq_implemented":false,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2},"tap_count":null,"guest_errno":false},{"name":"smmuv500-reference","kind":"file","verdict":"passed","expected_count":36,"count_field":"checks","expected_values":{"expected_checks":36,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"vmstate_version":1,"architectural_smmuv2_vmstate_version":3,"tcu_count":1,"tbu_count":10,"register_count":71,"sparse_window_count":71,"mapped_bytes":484,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2},"tap_count":null,"guest_errno":false},{"name":"tsens24xx-reference","kind":"file","verdict":"passed","expected_count":34,"count_field":"checks","expected_values":{"expected_checks":34,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"vmstate_version":1,"vmstate_payload_bytes_per_controller":375,"controller_count":2,"sensor_count":32,"sensors_per_controller":16,"register_count":114,"sparse_window_count":6,"mapped_bytes":456,"irq_count":4,"sampling_period_ns":1000000,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2},"tap_count":null,"guest_errno":false},{"name":"cpufreq-hw-reference","kind":"file","verdict":"passed","expected_count":30,"count_field":"checks","expected_values":{"schema":"tb371fc-cpufreq-hw-v1","expected_checks":30,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"domain_count":3,"cpu_count":8,"per_cpu_counter_count":8,"register_count":263,"sparse_window_count":24,"mapped_bytes":1052,"registers_per_domain":[89,88,86],"windows_per_domain":8,"lut_rows_per_domain":40,"lut_row_bytes":4,"valid_perf_states_per_domain":4,"irq_count":3,"input_clock_count":6,"output_clock_count":3,"vmstate_version":1,"vmstate_payload_bytes_per_domain":84,"reference_profile_is_default":false,"reference_lut_is_synthetic":true,"factory_lut_observed":false,"counter_source_is_virtual_time":true,"counter_measures_retired_cpu_cycles":false,"cpu_execution_speed_controlled":false,"analogue_voltage_control_implemented":false,"hardware_thermal_throttling_observed":false,"reference_lut_lvals":[16,32,64,96],"reference_lut_voltage_words":[750,800,900,1000],"reference_max_cores":[4,4,4],"actual_domain_cpu_counts":[4,3,1],"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2},"tap_count":null,"guest_errno":false},{"name":"devfreq-qcom-fw-reference","kind":"file","verdict":"passed","expected_count":20,"count_field":"checks","expected_values":{"schema":"tb371fc-devfreq-qcom-fw-v1","expected_checks":20,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"vmstate_version":1,"vmstate_payload_bytes_per_device":72,"device_count":1,"register_count":42,"sparse_window_count":3,"mapped_bytes":168,"lut_rows":40,"lut_row_bytes":4,"valid_perf_states":4,"software_voter_count":4,"reference_nominal_xo_hz":19200000,"reference_profile_is_default":false,"reference_lut_is_synthetic":true,"factory_lut_observed":false,"hardware_voter_slots_implemented":false,"clock_control_implemented":false,"actual_cache_frequency_measured":false,"irq_implemented":false,"timer_implemented":false,"counter_implemented":false,"voltage_control_implemented":false,"firmware_execution_observed":false,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2},"tap_count":null,"guest_errno":false},{"name":"gpu-gx-gdsc","kind":"file","verdict":"passed","expected_count":37,"count_field":"checks","expected_values":{"all_passed":true,"success":true,"expected_checks":37,"migration_expected_checks":12,"expected_migration_attempts":12,"completed_migration_attempts":8,"rejected_migration_attempts":4,"passed_checks":37},"expected_lengths":{"migrations":12,"migration_attempts":12},"tap_count":null,"guest_errno":false},{"name":"gpu-cx-gdsc","kind":"file","verdict":"passed","expected_count":26,"count_field":"checks","expected_values":{"all_passed":true,"success":true,"expected_checks":26,"migration_expected_checks":8,"expected_migration_attempts":9,"completed_migration_attempts":6,"rejected_migration_attempts":3,"socket_directories_retained":true},"expected_lengths":{"migrations":8,"migration_attempts":9},"tap_count":null,"guest_errno":false},{"name":"camera-gdsc-reference","kind":"file","verdict":"passed","expected_count":29,"count_field":"checks","expected_values":{"schema":"tb371fc-camera-gdsc-v1","expected_checks":29,"expected_completed_migrations":2,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"expected_unmodified_streams":3,"expected_stream_files":5,"vmstate_version":1,"vmstate_payload_bytes":194,"domain_count":6,"register_count":6,"sparse_window_count":6,"mapped_bytes":24,"gpio_output_count":12,"parent_resource_index":3,"reference_identity":1128743985,"hardware_mode_domain_mask":3,"modeled_timer_count":0,"modeled_clock_count":0,"gcc_vmstate_version":3,"gcc_camera_ahb_clock_id":10,"gcc_camera_ahb_write_mask":24577,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"camera_execution_observed":false,"analog_power_modeled":false,"retention_effect_modeled":false,"android_boot_passed":false,"binary_unchanged":true},"expected_lengths":{"migrations":2,"rejected_migrations":2,"corrupted_stream_rejections":2,"migration_sources":3},"tap_count":null,"guest_errno":false},{"name":"mdss-gdsc-reference","kind":"file","verdict":"passed","expected_count":20,"count_field":"checks","expected_values":{"schema":"tb371fc-mdss-gdsc-v1","expected_checks":20,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"expected_unmodified_streams":4,"expected_stream_files":6,"vmstate_version":1,"vmstate_payload_bytes":64,"domain_count":1,"register_count":1,"sparse_window_count":1,"mapped_bytes":4,"gpio_output_count":2,"parent_resource_index":3,"reference_identity":1296323377,"camera_vmstate_version":1,"camera_vmstate_payload_bytes":194,"modeled_timer_count":0,"modeled_clock_count":0,"gcc_vmstate_version":3,"gcc_display_ahb_clock_id":23,"gcc_display_ahb_write_mask":24577,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"display_execution_observed":false,"analog_power_modeled":false,"retention_effect_modeled":false,"android_boot_passed":false,"parent_output_intercepted":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":2,"migration_sources":4,"actual_stream_files":6},"tap_count":null,"guest_errno":false},{"name":"npu-gdsc-reference","kind":"file","verdict":"passed","expected_count":21,"count_field":"checks","expected_values":{"schema":"tb371fc-npu-gdsc-v1","expected_checks":21,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"expected_unmodified_streams":4,"expected_stream_files":6,"vmstate_version":1,"vmstate_payload_bytes":64,"domain_count":1,"register_count":1,"sparse_window_count":1,"mapped_bytes":4,"gpio_output_count":2,"parent_resource_index":4294967295,"parent_supply_declared":false,"parent_power_connected":false,"hardware_mode_domain_mask":0,"domain_dt_flags":1,"reference_identity":1313883953,"camera_vmstate_version":1,"camera_vmstate_payload_bytes":194,"mdss_vmstate_version":1,"mdss_vmstate_payload_bytes":64,"modeled_timer_count":0,"modeled_clock_count":0,"gcc_vmstate_version":3,"gcc_npu_cfg_ahb_clock_id":42,"gcc_npu_cfg_ahb_write_mask":24579,"gcc_npu_cfg_ahb_external_parent_index":21,"gcc_npu_cfg_ahb_status_mask":4026531840,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"npu_execution_observed":false,"analog_power_modeled":false,"retention_effect_modeled":false,"android_boot_passed":false,"parent_output_intercepted":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":2,"migration_sources":4,"actual_stream_files":6},"tap_count":null,"guest_errno":false},{"name":"video-gdsc-reference","kind":"file","verdict":"passed","expected_count":22,"count_field":"checks","expected_values":{"schema":"tb371fc-video-gdsc-v1","expected_checks":22,"expected_completed_migrations":3,"expected_rejected_migrations":2,"expected_corrupted_stream_rejections":2,"expected_unmodified_streams":4,"expected_stream_files":6,"vmstate_version":1,"vmstate_payload_bytes":142,"domain_count":4,"register_count":4,"sparse_window_count":4,"mapped_bytes":16,"gpio_output_count":8,"parent_resource_index":3,"parent_supply_declared":true,"parent_power_connected":true,"fanout_consumer_count":3,"npu_parent_power_connected":false,"hardware_mode_domain_mask":12,"domain_dt_flags":[1,1,3,3],"reference_identity":1447315249,"camera_vmstate_version":1,"camera_vmstate_payload_bytes":194,"mdss_vmstate_version":1,"mdss_vmstate_payload_bytes":64,"npu_vmstate_version":1,"npu_vmstate_payload_bytes":64,"modeled_timer_count":0,"modeled_clock_count":0,"gcc_vmstate_version":3,"gcc_video_ahb_clock_id":196,"gcc_video_ahb_write_mask":24577,"gcc_video_ahb_external_parent_index":79,"gcc_video_ahb_status_mask":0,"guest_cpu_executed":false,"native_kernel_probe_observed":false,"video_execution_observed":false,"analog_power_modeled":false,"retention_effect_modeled":false,"android_boot_passed":false,"parent_output_intercepted":false,"binary_unchanged":true},"expected_lengths":{"migrations":3,"rejected_migrations":2,"corrupted_stream_rejections":2,"migration_sources":4,"actual_stream_files":6},"tap_count":null,"guest_errno":false},{"name":"aop-reference-peer","kind":"file","verdict":"passed","expected_count":30,"count_field":"checks","expected_values":{"all_passed":true,"expected_checks":30,"expected_completed_migrations":6,"expected_rejected_migrations":2,"completed_migrations":6,"rejected_migration_attempts":2,"guest_cpu_executed":false,"ipcc_output_intercepted":false},"expected_lengths":{"migrations":6,"rejected_migrations":2},"tap_count":null,"guest_errno":false},{"name":"aop-msgram","kind":"file","verdict":"passed","expected_count":10,"count_field":"checks","expected_values":{"all_passed":true,"expected_checks":10,"output_interception_installed":false},"expected_lengths":{"migrations":1},"tap_count":null,"guest_errno":false},{"name":"smem-reference-handoff","kind":"file","verdict":"passed","expected_count":22,"count_field":"checks","expected_values":{"all_passed":true,"expected_checks":22,"guest_cpu_executed":false,"native_sbl_captured":false,"remote_firmware_emulated":false},"expected_lengths":{"migrations":4},"tap_count":null,"guest_errno":false},{"name":"dcc-operations","kind":"file","verdict":"passed","expected_count":16,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"ufs-register-dma","kind":"file","verdict":"passed","expected_count":56,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"ufs-crypto-rejection","kind":"file","verdict":"passed","expected_count":14,"count_field":"checks","expected_values":{"qemu_unchanged":true,"private_inputs_used":false},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"ufs-auto-hibern8","kind":"file","verdict":"all_passed","expected_count":18,"count_field":"checks","expected_values":{},"expected_lengths":{"migrations":2,"rejections":3},"tap_count":null,"guest_errno":false},{"name":"power-checkpoint","kind":"file","verdict":"all_passed","expected_count":7,"count_field":"checks","expected_values":{"expected_checks":7,"qemu_unchanged":true},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"watchdog-registers","kind":"file","verdict":"passed","expected_count":16,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"watchdog-migration","kind":"file","verdict":"passed","expected_count":1,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"boot-resources","kind":"file","verdict":"passed","expected_count":7,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"ipcc-messages","kind":"file","verdict":"passed","expected_count":16,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"dcc-capture","kind":"file","verdict":"passed","expected_count":17,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"spmi-pmic-arb","kind":"file","verdict":"passed","expected_count":25,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"spmi-pdc-gic-route","kind":"file","verdict":"passed","expected_count":8,"count_field":"checks","expected_values":{"all_passed":true,"expected_checks":8,"output_interception_installed":false},"expected_lengths":{"migrations":1},"tap_count":null,"guest_errno":false},{"name":"tlmm-migration","kind":"file","verdict":"all_passed","expected_count":15,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"pdc-migration","kind":"file","verdict":"all_passed","expected_count":10,"count_field":"checks","expected_values":{},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"smmuv2-migration","kind":"file","verdict":"all_passed","expected_count":18,"count_field":"checks","expected_values":{"schema_version":1,"kind":"tb371fc-smmuv2-process-migration","geometry_only":false},"expected_lengths":{"migrations":4},"tap_count":null,"guest_errno":false},{"name":"smmuv2-granules","kind":"file","verdict":"all_passed","expected_count":26,"count_field":"checks","expected_values":{"expected_checks":26,"legacy_v2_transfer_tested":false},"expected_lengths":{"migrations":3},"tap_count":null,"guest_errno":false}],"linux":[{"name":"linux-console-userspace","kind":"file","verdict":"passed","expected_count":8,"count_field":"guest_checks","expected_values":{"arch_counter_matches_native_frequency_and_progress":true,"guest_shutdown_observed":true,"validation_errors":[]},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"linux-console-empty-ufs","kind":"file","verdict":"passed","expected_count":8,"count_field":"guest_checks","expected_values":{"arch_counter_matches_native_frequency_and_progress":true,"guest_shutdown_observed":true,"validation_errors":[],"ufs_fixture":{"populated":false,"kind":"none","bytes":0}},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"linux-ufs-smmu","kind":"file","verdict":"passed","expected_count":19,"count_field":"guest_checks","expected_values":{"host_verified_disk_payload":true},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"linux-ufs-rpmh","kind":"file","verdict":"passed","expected_count":23,"count_field":"guest_checks","expected_values":{"host_verified_disk_payload":true,"require_rpmh":true,"expected_guest_checks":23},"expected_lengths":{},"tap_count":null,"guest_errno":false},{"name":"linux-ufs-runtime-pm","kind":"file","verdict":"passed","expected_count":40,"count_field":"guest_checks","expected_values":{"inputs_unchanged":true,"qemu_exit_code":0,"kernel_failure_lines":[],"guest_verdicts":[["PASS","40","0"]],"host_sleep_checks":{"vm_remains_running":true,"ufs_link_hibern8":true,"core_clock_gated":true,"iface_clock_gated":true,"unipro_clock_gated":true},"system_suspend_tested":false,"inflight_checkpoint_tested":false},"expected_lengths":{"host_observations":3},"tap_count":null,"guest_errno":true}]}')
CHECK_NAMES = ['cold_ahb_safe_enable_sequence_and_branch_demand_are_source_bound', 'shared_mvs1c_divider_fans_out_without_fixed_divide_two_assumption', 'every_hid_value_covers_bypass_integer_and_half_division', 'literal_v2_mvs_frequency_tables_route_the_correct_pll', 'already_running_configuration_preserves_both_pll_banks', 'cold_inventory_has_only_two_evidenced_inputs', 'literal_v2_probe_overwrites_pll1_base_recipe_but_skips_zero_writes', 'cold_critical_ahb_and_xo_require_requests_and_the_ao_provider', 'only_the_v2_pll1_recipe_can_lock_and_banks_are_independent', 'independent_lock_deadlines_require_both_output_gates', 'recipe_and_quantized_vco_bounds_never_invent_lock', 'two_lucid_updates_capture_once_then_ack_and_relock', 'update_cancel_standby_and_reset_keep_the_other_pll_independent', 'clock_jump_finishes_both_latches_and_relocks_without_mmio_repair', 'five_rcgs_capture_cfg_once_and_cancel_without_shadow_recapture', 'integer_rcg_masks_distinguish_hw_control_and_have_no_mnd_words', 'unsupported_muxes_and_fraction_modes_never_ack', 'every_declared_mux_routes_only_its_named_carrier', 'hid_floor_and_literal_v2_frequency_vectors_use_programmed_parents', 'unknown_sleep_and_test_inputs_never_gain_rates_from_waiting', 'all_four_readonly_dividers_round_up_and_propagate_branch_demand', 'all_eight_branches_keep_independent_local_requests', 'four_remote_votes_or_local_requests_but_do_not_supply_parents', 'remote_votes_never_become_local_or_sibling_requests', 'readonly_feedback_cannot_forge_or_erase_actual_status', 'reserved_bits_narrow_accesses_and_real_holes_reject_atomically', 'system_reset_cancels_work_but_retains_external_inputs_and_votes', 'pure_getters_and_fixtures_protect_connected_sources_and_types', 'shared_branch_ares_words_preserve_control_bits_independently', 'all_seven_reset_lines_leave_plls_clocks_and_gdsc_owners_independent', 'real_rpmh_xo_votes_revoke_both_evidenced_carriers', 'default_profile_rejects_all_words_even_with_sibling_profiles_enabled', 'paused_tcg_has_no_mutating_fixtures_and_executes_no_guest', 'exact_26_leaves_preserve_all_four_enabled_video_gdsc_owners', 'v1_steady_clocks_ares_dividers_votes_and_gdsc_replace_stale_target', 'v1_two_locks_and_five_rcg_deadlines_resume_without_mmio_repair', 'v1_two_latches_and_five_cfg_captures_preserve_original_deadlines', 'profile_enabled_to_disabled_rejected', 'profile_disabled_to_enabled_rejected', 'single_bit_physical_base_rejected', 'single_bit_shared_branch_reserved_bit_rejected']

CC, RAM = 0x0abf0000, 0x80010000
TYPE, DEVICE, VIDEO = "qcom-sm8250-videocc", "/machine/videocc", "/machine/video-gdsc"
PERIOD_UNIT = 1_000_000_000 << 32
INPUTS = ("bi_tcxo", "bi_tcxo_ao", "core_bi_pll_test_se", "chip_sleep_clk")
OUTPUTS = (
    "video_cc_ahb_clk", "video_cc_ahb_clk_src", "video_cc_mvs0_clk",
    "video_cc_mvs0_clk_src", "video_cc_mvs0_div_clk_src", "video_cc_mvs0c_clk",
    "video_cc_mvs0c_div2_div_clk_src", "video_cc_mvs1_clk", "video_cc_mvs1_clk_src",
    "video_cc_mvs1_div2_clk", "video_cc_mvs1_div_clk_src", "video_cc_mvs1c_clk",
    "video_cc_mvs1c_div2_div_clk_src", "video_cc_sleep_clk", "video_cc_sleep_clk_src",
    "video_cc_xo_clk", "video_cc_xo_clk_src", "video_pll0", "video_pll1")
GLOBALS = ("reference-profile", "reference-identity", "register-count", "clock-count",
           "input-count", "pll-count", "rcg-count", "branch-count", "divider-count",
           "reset-count", "remote-vote-count", "window-count", "mapped-bytes", "physical-base")
PLL_FIELDS = ("active-l", "active-alpha", "pending-l", "pending-alpha", "locked", "ack",
              "calibrated", "lock-pending", "latch-pending", "lock-deadline", "latch-deadline")
RCG_FIELDS = ("active-cfg", "pending-cfg", "pending", "deadline")
GDSC_GLOBALS = ("enabled-profile", "reference-identity", "domain-count", "register-bytes",
                "mapped-bytes", "parent-resource-index", "cold-control",
                "control-write-mask", "power-status-mask")
GDSC_FIELDS = ("name", "address", "dt-flags", "timeout-us", "control", "request-known",
               "requested", "state-known", "powered", "hw-request-known", "hw-request")
PLL_BASES = (0x42c, 0x7d0)
PLL_OFFSETS = (0, 4, 8, 12, 16, 20, 24, 28, 32, 36, 40, 44, 56, 64)
# Rows use binding IDs and source declaration order, independently of the .inc.
RCGS = ((0xbd4, 1, ("bi_tcxo_ao", None, "core_bi_pll_test_se")),
        (0xb94, 3, ("bi_tcxo", 17, "core_bi_pll_test_se")),
        (0xbb4, 8, ("bi_tcxo", 18, "core_bi_pll_test_se")),
        (0xef0, 14, ("chip_sleep_clk", None, "core_bi_pll_test_se")),
        (0xecc, 16, ("bi_tcxo_ao", None, "core_bi_pll_test_se")))
DIVIDERS = ((0xd54, 4, 3), (0xc54, 6, 3), (0xdd4, 10, 8), (0xcf4, 12, 8))
BRANCHES = ((0xe58, 0, 1, -1), (0xd34, 2, 4, 0), (0xc34, 5, 6, -1),
            (0xdb4, 7, 10, 1), (0xdf4, 9, 12, 2), (0xcd4, 11, 12, 3),
            (0xf10, 13, 14, -1), (0xeec, 15, 16, -1))
RESETS = ((0xe54, 1), (0xd14, 1), (0xc34, 4), (0xbf4, 1),
          (0xd94, 1), (0xcd4, 4), (0xc94, 1))
REGISTERS = tuple(sorted(set(
    [b + o for b in PLL_BASES for o in PLL_OFFSETS] +
    [b + o for b, *_ in RCGS for o in (0, 4)] +
    [b for b, *_ in DIVIDERS] + [b for b, *_ in BRANCHES] + [b for b, _ in RESETS])))
GDSC_OFFSETS = (0xbf8, 0xc98, 0xd18, 0xd98)
GDSC_NAMES = ("mvs0c", "mvs1c", "mvs0", "mvs1")
ORIGINALS = (
    "videocc-v1-steady.migration", "videocc-v1-two-locks-five-rcgs.migration",
    "videocc-v1-two-latches-five-cfg.migration", "videocc-v1-default.migration")
CORRUPTIONS = ("videocc-corrupt-physical_base.migration",
               "videocc-corrupt-shared_branch_reserved_bit.migration")
SOURCE_DIRS = ("migration-steady-source", "migration-two-locks-five-rcgs-source",
               "migration-two-latches-five-cfg-source", "migration-default-source")
MIGRATION_NAMES = (
    "v1_steady_clocks_ares_dividers_votes_and_gdsc_replace_stale_target",
    "v1_two_locks_and_five_rcg_deadlines_resume_without_mmio_repair",
    "v1_two_latches_and_five_cfg_captures_preserve_original_deadlines")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


class AuditFailure(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise AuditFailure(message)


def equal(actual, expected, where):
    require(type(actual) is type(expected), f"{where}: wrong type")
    if isinstance(expected, dict):
        require(actual.keys() == expected.keys(), f"{where}: dictionary keys differ")
        for k, v in expected.items():
            equal(actual[k], v, f"{where}.{k}")
    elif isinstance(expected, (list, tuple)):
        require(len(actual) == len(expected), f"{where}: length differs")
        for i, (a, b) in enumerate(zip(actual, expected)):
            equal(a, b, f"{where}[{i}]")
    else:
        require(actual == expected, f"{where}: {actual!r} != {expected!r}")


def integer(value, bits, where, signed=False):
    minimum = -(1 << (bits - 1)) if signed else 0
    maximum = (1 << (bits - int(signed))) - 1
    require(type(value) is int and minimum <= value <= maximum, f"{where}: invalid integer")
    return value


def boolean(value, where):
    require(type(value) is bool, f"{where}: not a boolean")
    return value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def object_unique(items):
    result = {}
    for key, value in items:
        require(key not in result, f"Duplicate JSON key {key!r}")
        result[key] = value
    return result


def parse_json(raw, where):
    try:
        return json.loads(raw, object_pairs_hook=object_unique,
                          parse_constant=lambda x: (_ for _ in ()).throw(AuditFailure("nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise AuditFailure(f"Invalid JSON in {where}: {exc}") from exc


def relative(name):
    require(isinstance(name, str) and name and "\\" not in name, "Invalid archive path")
    p = PurePosixPath(name)
    require(not p.is_absolute() and all(x not in ("", ".", "..") for x in p.parts),
            f"Unsafe archive path {name!r}")
    require(str(p) == name.rstrip("/"), f"Noncanonical archive path {name!r}")
    return name.rstrip("/")


def basename(name):
    require(relative(name) == name and "/" not in name, f"Expected a basename: {name!r}")
    return name


class Artifact:
    def __init__(self, path):
        self.path = path
        self.before = path.stat()
        self.zip = ZipFile(path)
        self.entries, self.manifests, self.verified = {}, [], {}
        for info in self.zip.infolist():
            key = relative(info.filename)
            require(key not in self.entries, f"Duplicate ZIP member {key}")
            require(not stat.S_ISLNK(info.external_attr >> 16), f"Symlink ZIP member {key}")
            self.entries[key] = info
        require(len(self.entries) < 200000, "Unreasonable ZIP member count")

    def locate(self, suffix):
        candidates = [n for n, i in self.entries.items()
                      if not i.is_dir() and (n == suffix or n.endswith("/" + suffix))]
        require(len(candidates) == 1, f"Expected one {suffix}: {candidates}")
        return candidates[0]

    def register_manifest(self, prefix):
        values = self.json(prefix + "/sha256.json")
        require(type(values) is dict and values, f"Empty SHA inventory {prefix}")
        for name, digest in values.items():
            relative(name)
            require(isinstance(digest, str) and HEX64.fullmatch(digest), f"Bad digest for {name}")
        self.manifests.append((prefix + "/", values))

    def note(self, name, size, digest):
        for prefix, values in self.manifests:
            if name.startswith(prefix) and name != prefix + "sha256.json":
                key = name[len(prefix):]
                require(values.get(key) == digest, f"Actual bytes differ from SHA inventory: {name}")
        self.verified[name] = {"bytes": size, "sha256": digest}

    def read(self, name, limit=32 * 1024 * 1024):
        require(name in self.entries and not self.entries[name].is_dir(), f"Missing ZIP member {name}")
        require(self.entries[name].file_size <= limit, f"Member exceeds audit bound: {name}")
        raw = self.zip.read(self.entries[name])
        require(len(raw) == self.entries[name].file_size, f"Truncated member {name}")
        self.note(name, len(raw), sha(raw))
        return raw

    def hash(self, name):
        require(name in self.entries and not self.entries[name].is_dir(), f"Missing ZIP member {name}")
        h, size = hashlib.sha256(), 0
        with self.zip.open(self.entries[name]) as stream:
            for part in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(part)
                h.update(part)
        require(size == self.entries[name].file_size, f"Truncated member {name}")
        digest = h.hexdigest()
        self.note(name, size, digest)
        return digest

    def json(self, name):
        return parse_json(self.read(name).decode("utf-8"), name)

    def text(self, name, limit=64 * 1024 * 1024):
        return self.read(name, limit).decode("utf-8", errors="strict")

    def unchanged(self):
        after = self.path.stat()
        require((self.before.st_dev, self.before.st_ino, self.before.st_size, self.before.st_mtime_ns) ==
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), "ZIP changed during audit")


def check_verdicts(data, where, required=False):
    require(type(data) is dict, f"{where}: expected object")
    found = False
    for key in ("passed", "success", "all_passed"):
        if key in data:
            found = True
            require(data[key] is True, f"{where}.{key}: contradictory verdict")
    if required:
        require(found, f"{where}: no positive verdict")


def check_name(row, where, guest=False, errno=False):
    if guest and isinstance(row, str):
        m = re.fullmatch(r"TB371FC_CHECK PASS ([A-Za-z0-9_]+)", row)
        require(m is not None, f"{where}: malformed guest check")
        return m[1]
    if guest and type(row) is list:
        require(len(row) == (3 if errno else 2) and row[0] == "PASS" and
                isinstance(row[1], str) and re.fullmatch(r"[A-Za-z0-9_]+", row[1]),
                f"{where}: unsuccessful guest row")
        if errno:
            require(isinstance(row[2], str) and re.fullmatch(r"[0-9]+", row[2]), f"{where}: bad errno")
        return row[1]
    require(type(row) is dict, f"{where}: invalid check row")
    positive = any(k in row for k in ("passed", "success", "all_passed"))
    check_verdicts(row, where)
    if "status" in row:
        require(isinstance(row["status"], str) and row["status"].lower() in ("pass", "passed", "ok"),
                f"{where}: unsuccessful status")
        positive = True
    require(positive, f"{where}: no check verdict")
    names = [row[k] for k in ("name", "test", "check") if k in row]
    require(names and all(isinstance(n, str) and n.strip() for n in names) and len(set(names)) == 1,
            f"{where}: invalid check identity")
    return names[0]


def validate_json_case(data, contract, where):
    check_verdicts(data, where)
    require(data.get(contract["verdict"]) is True, f"{where}: required verdict missing")
    if contract["expected_count"] is not None:
        field = contract["count_field"]
        checks = data.get(field)
        require(type(checks) is list and len(checks) == contract["expected_count"], f"{where}: wrong count")
        names = [check_name(row, where + f".{field}[{i}]", field == "guest_checks", contract["guest_errno"])
                 for i, row in enumerate(checks)]
        require(len(set(names)) == len(names), f"{where}: duplicate checks")
    for key, expected in contract["expected_values"].items():
        equal(data.get(key), expected, where + "." + key)
    for key, length in contract["expected_lengths"].items():
        value = data.get(key)
        require(type(value) is list and len(value) == length, f"{where}.{key}: wrong list length")
        for i, row in enumerate(value):
            if type(row) is dict:
                check_verdicts(row, where + f".{key}[{i}]")


def validate_tap(text, count, where):
    require(not re.search(r"^(?:not ok\b|Bail out!)", text, re.M), f"{where}: TAP failure")
    rows = re.findall(r"^ok\s+([0-9]+)\b(.*)$", text, re.M)
    equal([int(n) for n, _ in rows], list(range(1, count + 1)), where + ".TAP ids")
    equal(re.findall(r"^1\.\.([0-9]+)(?:\s*#.*)?$", text, re.M), [str(count)], where + ".TAP plan")
    require(not any(re.search(r"#\s*(?:SKIP|TODO)\b", tail, re.I) for _, tail in rows), f"{where}: skipped TAP")


def audit_suite(a, suite, count, expected_head, binary_hash):
    aggregate_path = a.locate(suite + "/result.json")
    prefix = str(PurePosixPath(aggregate_path).parent)
    a.register_manifest(prefix)
    report = a.json(aggregate_path)
    for key, expected in (("schema_version", 1), ("suite", suite), ("source_commit", expected_head),
                          ("source_dirty", False), ("private_inputs_used", False),
                          ("qemu_unchanged", True), ("runner_sha256", RUNNER_SHA256),
                          ("qemu_sha256", binary_hash), ("expected_gates", count), ("passed_gates", count)):
        equal(report.get(key), expected, aggregate_path + "." + key)
    require(report.get("passed") is True, f"{suite}: aggregate failed")
    require(isinstance(report.get("completed_utc"), str) and report["completed_utc"], f"{suite}: incomplete aggregate")
    gates, registered = report.get("gates"), a.json(prefix + "/plan.json")
    require(type(gates) is list and type(registered) is list and len(gates) == len(registered) == count,
            f"{suite}: incomplete gate inventory")
    require(len(PLAN[suite]) == count, "Internal contract length mismatch")
    equal([g["name"] for g in gates], [g["name"] for g in PLAN[suite]], suite + ".gate order")
    selected, receipts = {}, []
    for i, (contract, declared, gate) in enumerate(zip(PLAN[suite], registered, gates)):
        name = contract["name"]
        root = f"{prefix}/{i:02d}-{name}"
        case = a.json(root + "/case.json")
        equal(case, gate, root + ".case versus aggregate")
        for key in ("name", "verdict", "expected_count", "count_field", "expected_values", "expected_lengths", "tap_count"):
            equal(declared.get(key), contract[key], root + ".plan." + key)
        result = declared.get("result")
        kind = result if result in ("stdout", "tap", "exit") else "file"
        equal(kind, contract["kind"], root + ".result kind")
        equal(case.get("command"), declared["command"], root + ".command")
        require(case.get("passed") is True and type(case.get("exit_code")) is int and case["exit_code"] == 0,
                f"{root}: failed process")
        require(not any(k in case for k in ("error", "artifact_error", "timed_out")), f"{root}: incomplete process")
        require(isinstance(case.get("completed_utc"), str), f"{root}: missing completion")
        logs = {}
        for log in ("stdout.log", "stderr.log"):
            raw = a.read(root + "/" + log, 64 * 1024 * 1024)
            equal(case.get("log_sha256", {}).get(log), sha(raw), root + "." + log)
            logs[log] = raw.decode("utf-8", errors="strict")
        data, data_path = None, None
        if kind == "tap":
            validate_tap(logs["stdout.log"], contract["tap_count"], root)
            equal(case["verified"], {"tap_passed": contract["tap_count"], "tap_skipped": False}, root + ".verified TAP")
        elif kind == "exit":
            equal(case["verified"], {"verdict": "successful test process"}, root + ".verified exit")
            combined = logs["stdout.log"] + logs["stderr.log"]
            require(re.search(r"Ran [1-9][0-9]* tests? in ", combined) is not None and
                    re.search(r"^OK$", combined, re.M) is not None,
                    f"{root}: missing actual unittest success log")
            if name == "ci-verdict-unit":
                equal(re.findall(r"Ran ([0-9]+) tests in ", combined),
                      ['33'], "Actual CI harness test count")
        else:
            if kind == "stdout":
                data_path = root + "/stdout.log"
                data = parse_json(logs["stdout.log"], data_path)
            else:
                fixture = case["fixture"].rstrip("/") + "/"
                require(isinstance(result, str) and result.startswith(fixture), f"{root}: unrelated result path")
                rel = relative(result[len(fixture):])
                data_path = root + "/fixtures/" + rel
                data = a.json(data_path)
            validate_json_case(data, contract, data_path)
            for key in ("qemu_sha256", "qemu_sha256_before", "qemu_sha256_after"):
                if key in data:
                    equal(data[key], binary_hash, data_path + "." + key)
            require(case["verified"].get("verdict") is True and
                    case["verified"].get("verdict_field") == contract["verdict"], f"{root}: wrong adjudication")
            if contract["expected_count"] is not None:
                equal(case["verified"].get("check_count"), contract["expected_count"], root + ".verified count")
            if contract["expected_lengths"]:
                equal(case["verified"].get("verified_lengths"), contract["expected_lengths"], root + ".verified lengths")
        receipts.append({"name": name, "case": root + "/case.json", "receipt": data_path,
                         "exit_code": 0, "validated_against_independent_contract": True})
        if name.startswith(("videocc-", "dispcc-", "dsi-pll-", "dp-pll-", "llcc-", "spss-")):
            selected[name] = (str(PurePosixPath(data_path).parent), data)
    return {"aggregate": aggregate_path, "count": count, "gates": receipts}, selected, report


def audit_generation(a, root, report):
    require(report.get("generator_exit") == 0, "Generator did not exit zero")
    inputs = report["inputs"]
    require(len(inputs) == 10, "Incorrect source pin count")
    base_url = "https://raw.githubusercontent.com/OnePlusOSS/android_kernel_oneplus_sm8250/" + SOURCE_REVISION + "/"
    seen = set()
    for item in inputs:
        require(isinstance(item.get("url"), str) and item["url"].startswith(base_url), "Unpinned source URL")
        name = item["url"][len(base_url):]
        require(name in SOURCE_HASHES and name not in seen, "Unknown or duplicate source input")
        seen.add(name)
        raw = a.read(root + "/inputs/" + name, 512 * 1024)
        equal(item["sha256"], sha(raw), name + ".actual source digest")
        equal(item["expected_sha256"], SOURCE_HASHES[name], name + ".expected source pin")
        equal(sha(raw), SOURCE_HASHES[name], name + ".fixed source pin")
        equal(item["bytes"], len(raw), name + ".source size")
    equal(seen, set(SOURCE_HASHES), "Complete source pin set")
    checks = report["checks"]
    equal([c["name"] for c in checks], list(GENERATED_HASHES), "Generated file order")
    for item in checks:
        name = item["name"]
        raw = a.read(root + "/generated/" + name)
        for key in ("generated_sha256", "committed_sha256"):
            equal(item[key], sha(raw), name + "." + key)
        equal(sha(raw), GENERATED_HASHES[name], name + ".reviewed generated bytes")
    stdout = parse_json(a.text(root + "/generation.stdout"), "generation.stdout")
    equal(stdout, {"registers": 55, "windows": 26, "clocks": 19, "inputs": 4,
                   "mapped_bytes": 220, "source_pins_verified": True}, "Generator geometry")
    require(not a.text(root + "/generation.stderr").strip(), "Generator stderr is nonempty")
    return {"inputs_verified_from_actual_bytes": 10, "outputs_verified_from_actual_bytes": 2,
            "source_revision": SOURCE_REVISION, "committed_output_pins": GENERATED_HASHES}


DSI_SOURCE_HASHES = {'techpack/display/pll/dsi_pll_7nm.c': 'a286700822a0fb2fa1e9bff39cc6720295254c6137f29de65a37bcb819a8dd64', 'techpack/display/pll/pll_drv.c': '14652116c7ea1ff9c5380fe5441087c910d57b890d52aec3a2a031b76b250c56', 'techpack/display/pll/pll_drv.h': 'ba1df695799b9c4b078fcde5c6cfa3bddf35ed026f818f4fe372cf89d3221feb', 'techpack/display/pll/pll_util.c': '652450b062d1a957a2db5b83c46244e11168974e259506f0c9a3e5b959f2e49a', 'techpack/display/pll/dsi_pll.h': '2f1afd78313228853871d028c8ae035a5080d56d4a64b4c8c2e566bff1a188bf', 'techpack/display/pll/dsi_pll_util.c': '01f80def383b13ca240f409dc11ff4982f68e669ddf24d970f3d523d2da0363f', 'techpack/display/pll/Makefile': '83598d0660b38fb30979ca52a17e2ac2ece01573ca49ed9a86ea01cd3624bcf0', 'include/dt-bindings/clock/mdss-7nm-pll-clk.h': '51632c1a73cfce8c4915e5e5c56f46b0fddae983d3e0ac44cd2731c488105d82', 'arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi': 'd275792039d00a135a3f809e49954196c62f1e68202702597fe9d93fb5484118', 'techpack/display/msm/dsi/dsi_phy_hw_v4_0.c': '89d6e0cc0783f197a73bb2d6ece734dcdebb9291ffaaadd37cb2f48911bc084d', 'techpack/display/msm/dsi/dsi_hw.h': 'f09364f20032944a1a886e90f627c3a23af70e0d2927cf9a35358f6a1087cccc', 'techpack/display/msm/dsi/dsi_phy_hw.h': '201412264a93eacff63d6d9022e75c27e8b9cad2c4ed7da74ac6474216ab9138', 'techpack/display/msm/dsi/dsi_catalog.c': '5e53e4d2f82034d8b86b8b92727f05760e2e54ff2de227e8ad581f997e2e58a1'}
DSI_GENERATED_HASHES = {'hw/misc/qcom-sm8250-dsi-pll-data.inc': 'c193666bb41923063ca295cdb0f0327cea5f0e5b917c23d1b4b94d16bff23c16', 'tb371fc/docs/dsi-pll-7nm-registers.json': 'c73a7e5ad6f36c008f2e9991c8d7a56d3b30cb2781b015a46fd3c13ca0d5b6df'}
DSI_SOURCE_REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"

def audit_dsi_generation(a, root, report):
    equal(report.get("generator_exit"), 0, "DSI generator exit")
    equal(report.get("source_revision"), DSI_SOURCE_REVISION,
          "DSI source revision")
    inputs = report["inputs"]
    equal(len(inputs), 13, "DSI source pin count")
    base_url = ("https://raw.githubusercontent.com/"
                "OnePlusOSS/android_kernel_oneplus_sm8250/" +
                DSI_SOURCE_REVISION + "/")
    seen = set()
    for item in inputs:
        name = relative(item["path"])
        require(name in DSI_SOURCE_HASHES and name not in seen,
                "Unknown or duplicated DSI source input")
        seen.add(name)
        equal(item["url"], base_url + name, "DSI immutable input URL")
        raw = a.read(root + "/inputs/" + name, 512 * 1024)
        equal(item["bytes"], len(raw), "DSI source byte count")
        equal(item["sha256"], sha(raw), "DSI actual source digest")
        equal(item["expected_sha256"], DSI_SOURCE_HASHES[name],
              "DSI expected source pin")
        equal(sha(raw), DSI_SOURCE_HASHES[name], "DSI fixed primary bytes")
    equal(seen, set(DSI_SOURCE_HASHES), "Complete DSI table source set")
    checks = report["checks"]
    equal([c["name"] for c in checks], list(DSI_GENERATED_HASHES),
          "DSI generated file order")
    for item in checks:
        name = item["name"]
        raw = a.read(root + "/generated/" + name)
        equal(item["passed"], True, "DSI generated comparison verdict")
        for key in ("generated_bytes", "committed_bytes"):
            equal(item[key], len(raw), "DSI " + key)
        for key in ("generated_sha256", "committed_sha256"):
            equal(item[key], sha(raw), "DSI " + key)
        equal(sha(raw), DSI_GENERATED_HASHES[name],
              "Reviewed DSI generated bytes")
    stdout = parse_json(a.text(root + "/generation.stdout"),
                        "DSI generation.stdout")
    equal(stdout, {"source_pins": 13, "per_bank_registers": 154,
                   "windows": 16, "mapped_bytes": 1232, "clocks": 52,
                   "outputs": list(DSI_GENERATED_HASHES)},
          "DSI generated geometry")
    require(not a.text(root + "/generation.stderr").strip(),
            "DSI generator stderr is nonempty")
    return {"inputs_verified_from_actual_bytes": 13,
            "outputs_verified_from_actual_bytes": 2,
            "source_revision": DSI_SOURCE_REVISION,
            "committed_output_pins": DSI_GENERATED_HASHES}



DP_SOURCE_REVISION = "98719830b0de5584f5588bbe9f4c8304d97a64c6"
DP_SOURCE_HASHES = {'arch/arm64/boot/dts/vendor/qcom/kona-sde-pll.dtsi': 'd275792039d00a135a3f809e49954196c62f1e68202702597fe9d93fb5484118', 'arch/arm64/boot/dts/vendor/qcom/kona-sde.dtsi': '1247f63e2538357a0ca825064ad3b44ce1e0bfa17d45b13f90ca9e5f7b88f352', 'include/dt-bindings/clock/mdss-7nm-pll-clk.h': '51632c1a73cfce8c4915e5e5c56f46b0fddae983d3e0ac44cd2731c488105d82', 'include/dt-bindings/clock/qcom,dispcc-kona.h': 'a624fe5555fd4eddc10a46a80ff3a52da66ddb56f81567c7ea4df591f5949ac2', 'include/dt-bindings/clock/qcom,gcc-kona.h': 'ec7b2a775a7f96a543c064c7fc733fb83ffa7e3dae3c4bb517200627ae5f7fed', 'include/dt-bindings/clock/qcom,rpmh.h': '9b8ad7915183052c15a1acd4b227099904120639d885e46ea80a5c4be4d13136', 'include/linux/usb/usbpd.h': '6128e1eeaaac1e4ffe13d2ec1a2a6a50c2dda9184346edb8e21e4b1fc39ec957', 'techpack/display/msm/dp/dp_catalog_v420.c': '6c68e49ac69d52b1394e4c64344734f3312ff5cccda04f35eefba04dbc3dddc3', 'techpack/display/msm/dp/dp_reg.h': '9f359d5c920644fc573fe9478be62e3ae0ecc197daf0c81efbb7a886836f8cab', 'techpack/display/pll/Makefile': '83598d0660b38fb30979ca52a17e2ac2ece01573ca49ed9a86ea01cd3624bcf0', 'techpack/display/pll/dp_pll.h': 'f9d9cae58e7720bd4415d1c33d3645850ce3d3c5dba2adf835d6d9acd07f4a1c', 'techpack/display/pll/dp_pll_7nm.c': 'b8c40f85bb9ee966b905434e91225c70e27ecd945a5ea2495cc7604219d483a1', 'techpack/display/pll/dp_pll_7nm.h': '7c95097365330c9aad6999ec62e9b15ee20957ca76f94f599e6fc5be615705fd', 'techpack/display/pll/dp_pll_7nm_util.c': '1135de217c3ec633a4539565e699baa86fdbe452e48e444cd3660b5cab981532', 'techpack/display/pll/pll_drv.c': '14652116c7ea1ff9c5380fe5441087c910d57b890d52aec3a2a031b76b250c56', 'techpack/display/pll/pll_drv.h': 'ba1df695799b9c4b078fcde5c6cfa3bddf35ed026f818f4fe372cf89d3221feb', 'techpack/display/pll/pll_util.c': '652450b062d1a957a2db5b83c46244e11168974e259506f0c9a3e5b959f2e49a'}
DP_GENERATED_HASHES = {'hw/misc/qcom-sm8250-dp-pll-data.inc': '36871a517d97b694252bfee383adf781878ac013215b98fd17089e7e73a5dd0d', 'tb371fc/docs/dp-pll-7nm-registers.json': 'a9150fda61e16a0deb6acabe17f9e3b532963a61453fb98c125dab0cf08ec101'}
DP_GENERATED_SUMMARY = {'source_input_count': 17, 'register_count': 73, 'required_register_count': 58, 'window_count': 42, 'mapped_bytes': 292, 'clock_count': 6}

def audit_dp_generation(a, root, report):
    equal(report.get("generator_exit"), 0, "DP generator exit")
    equal(report.get("source_revision"), DP_SOURCE_REVISION,
          "DP source revision")
    inputs = report["inputs"]
    equal(len(inputs), 17, "DP source pin count")
    base_url = ("https://raw.githubusercontent.com/"
                "OnePlusOSS/android_kernel_oneplus_sm8250/" +
                DP_SOURCE_REVISION + "/")
    seen = set()
    for item in inputs:
        name = relative(item["path"])
        require(name in DP_SOURCE_HASHES and name not in seen,
                "Unknown or duplicated DP source input")
        seen.add(name)
        equal(item["url"], base_url + name, "DP immutable input URL")
        raw = a.read(root + "/inputs/" + name, 512 * 1024)
        equal(item["bytes"], len(raw), "DP source byte count")
        equal(item["sha256"], sha(raw), "DP actual source digest")
        equal(item["expected_sha256"], DP_SOURCE_HASHES[name],
              "DP expected source pin")
        equal(sha(raw), DP_SOURCE_HASHES[name], "DP fixed primary bytes")
    equal(seen, set(DP_SOURCE_HASHES), "Complete DP table source set")
    checks = report["checks"]
    equal([c["name"] for c in checks], list(DP_GENERATED_HASHES),
          "DP generated file order")
    for item in checks:
        name = item["name"]
        raw = a.read(root + "/generated/" + name)
        equal(item["passed"], True, "DP generated comparison verdict")
        for key in ("generated_bytes", "committed_bytes"):
            equal(item[key], len(raw), "DP " + key)
        for key in ("generated_sha256", "committed_sha256"):
            equal(item[key], sha(raw), "DP " + key)
        equal(sha(raw), DP_GENERATED_HASHES[name],
              "Reviewed DP generated bytes")
    stdout = [parse_json(line, "DP generation.stdout line")
              for line in a.text(root + "/generation.stdout").splitlines() if line.strip()]
    equal(stdout, [{"path": name,
                    "bytes": len(a.read(root + "/generated/" + name)),
                    "sha256": digest} for name, digest in DP_GENERATED_HASHES.items()],
          "DP generated output byte receipts")
    equal(report.get("generated_contract"), DP_GENERATED_SUMMARY,
          "DP generated geometry")
    require(not a.text(root + "/generation.stderr").strip(),
            "DP generator stderr is nonempty")
    return {"inputs_verified_from_actual_bytes": 17,
            "outputs_verified_from_actual_bytes": 2,
            "source_revision": DP_SOURCE_REVISION,
            "committed_output_pins": DP_GENERATED_HASHES}


LLCC_SOURCE_PINS = {'primary-r11/drivers/soc/qcom/llcc-slice.c': {'path': 'primary-r11/drivers/soc/qcom/llcc-slice.c', 'source_path': 'drivers/soc/qcom/llcc-slice.c', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 10965, 'git_blob_sha': 'a933f377db535f12f47942eff31fffe214af0912', 'sha256': '5a49480bebfa84ca94240202abc3fc3bc8eaf6379df9f4b71a92640e140eb1c5'}, 'primary-r11/drivers/soc/qcom/llcc-kona.c': {'path': 'primary-r11/drivers/soc/qcom/llcc-kona.c', 'source_path': 'drivers/soc/qcom/llcc-kona.c', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 3764, 'git_blob_sha': 'a155d6365246ceaf2e8bca5a289ed72772cac74e', 'sha256': '140c5774139c0827003754bb083d0b6945fbd9982bc5538105084614eac1b2a9'}, 'primary-r11/include/linux/soc/qcom/llcc-qcom.h': {'path': 'primary-r11/include/linux/soc/qcom/llcc-qcom.h', 'source_path': 'include/linux/soc/qcom/llcc-qcom.h', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 5789, 'git_blob_sha': 'f0e21085aec911e1f9d854fefd77418666e77d4d', 'sha256': '1d348978460ba7a32087af7567c80ddeaa98399855c719e7c0c2b1fcc4d7f093'}, 'primary-r11/drivers/soc/qcom/llcc_perfmon.c': {'path': 'primary-r11/drivers/soc/qcom/llcc_perfmon.c', 'source_path': 'drivers/soc/qcom/llcc_perfmon.c', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 37150, 'git_blob_sha': '99278c99cc311838d0b612bd5a96e908a5ec03ed', 'sha256': '38f1df996a946b0a6ce8f4cd71559367d1f4498d475605bc9089311d63defeeb'}, 'primary-r11/drivers/soc/qcom/llcc_perfmon.h': {'path': 'primary-r11/drivers/soc/qcom/llcc_perfmon.h', 'source_path': 'drivers/soc/qcom/llcc_perfmon.h', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 9820, 'git_blob_sha': '4a3accda43e9a2f681c118f43b502e13a52d2eba', 'sha256': '536de10d400093fa406b88f7a3ebfaf30966b43e15f88533be98c85ac5baebfd'}, 'primary-r11/drivers/edac/qcom_edac.c': {'path': 'primary-r11/drivers/edac/qcom_edac.c', 'source_path': 'drivers/edac/qcom_edac.c', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 11972, 'git_blob_sha': '560c96c1e5b88dc8b9b622ce4d3d590370470d7a', 'sha256': 'ae3c0da558345d3c471fd35a5ae7f878a84c61d21093056d4ac66359f2486a24'}, 'primary-r11/arch/arm64/boot/dts/vendor/qcom/kona.dtsi': {'path': 'primary-r11/arch/arm64/boot/dts/vendor/qcom/kona.dtsi', 'source_path': 'arch/arm64/boot/dts/vendor/qcom/kona.dtsi', 'repository': 'OnePlusOSS/android_kernel_oneplus_sm8250', 'revision': '98719830b0de5584f5588bbe9f4c8304d97a64c6', 'bytes': 132499, 'git_blob_sha': 'e9651f09bd579616787894dc8514429063623b2d', 'sha256': '12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55'}, 'primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi': {'path': 'primary-upstream/arch/arm64/boot/dts/qcom/sm8250.dtsi', 'source_path': 'arch/arm64/boot/dts/qcom/sm8250.dtsi', 'repository': 'torvalds/linux', 'revision': 'adc218676eef25575469234709c2d87185ca223a', 'bytes': 176883, 'git_blob_sha': '630f4eff20bf810e347685066dce74ab92a23b56', 'sha256': '8735b5d8bb61d6a3a579e5b088ff2a719132266cef57c91c319ec19e12770499'}}
LLCC_GENERATED_HASHES = {'hw/misc/qcom-sm8250-llcc-data.inc': 'f3f7979c7973b2fbf9d8d398c064b8a404a290fbdd9d5a141407c0b28f974a46', 'tb371fc/docs/llcc-sm8250-registers.json': '9b9989a04b3719665439dab6287be8ced18511021c58ef5c7011f36a587b4772'}
LLCC_GENERATED_SUMMARY = {'source_input_count': 8, 'bank_count': 4, 'slice_count': 16, 'register_count': 71, 'region_count': 2, 'mapped_register_count': 349, 'window_count': 139, 'mapped_bytes': 1396}

def audit_llcc_generation(a, root, report):
    equal(report.get("generator_exit"), 0, "LLCC generator exit")
    equal(report.get("source_revision"), "98719830b0de5584f5588bbe9f4c8304d97a64c6",
          "LLCC source revision")
    equal(report.get("geometry_source_revision"), "adc218676eef25575469234709c2d87185ca223a",
          "LLCC explicit geometry source revision")
    equal([item["path"] for item in report["inputs"]], list(LLCC_SOURCE_PINS),
          "Complete ordered LLCC primary input set")
    for item in report["inputs"]:
        name = relative(item["path"])
        pin = LLCC_SOURCE_PINS[name]
        raw = a.read(root + "/inputs/" + name, 512 * 1024)
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        equal({key: item[key] for key in pin}, pin, "LLCC complete declared primary pin")
        equal(item["url"], "https://raw.githubusercontent.com/" + pin["repository"] +
              "/" + pin["revision"] + "/" + pin["source_path"], "LLCC immutable source URL")
        equal(len(raw), pin["bytes"], "LLCC primary byte count")
        equal(sha(raw), pin["sha256"], "LLCC actual primary SHA-256")
        equal(blob, pin["git_blob_sha"], "LLCC actual primary Git blob")
        equal(item["expected_sha256"], pin["sha256"], "LLCC claimed SHA-256 pin")
        equal(item["expected_git_blob_sha"], pin["git_blob_sha"], "LLCC claimed Git blob pin")
    checks = report["checks"]
    equal([item["name"] for item in checks], list(LLCC_GENERATED_HASHES),
          "LLCC generated output order")
    for item in checks:
        name = item["name"]
        raw = a.read(root + "/generated/" + name)
        equal(item["passed"], True, "LLCC generated comparison verdict")
        for key in ("generated_bytes", "committed_bytes"):
            equal(item[key], len(raw), "LLCC " + key)
        for key in ("generated_sha256", "committed_sha256"):
            equal(item[key], sha(raw), "LLCC " + key)
        equal(sha(raw), LLCC_GENERATED_HASHES[name], "LLCC reviewed generated byte identity")
    stdout = [parse_json(line, "LLCC generation.stdout line")
              for line in a.text(root + "/generation.stdout").splitlines() if line.strip()]
    equal(stdout, [{"path": name, "bytes": len(a.read(root + "/generated/" + name)),
                    "sha256": digest} for name, digest in LLCC_GENERATED_HASHES.items()],
          "LLCC generated output byte receipts")
    equal(report.get("generated_contract"), LLCC_GENERATED_SUMMARY,
          "LLCC generated geometry")
    equal(report.get("source_table_bytes"), 640, "LLCC SCT table length")
    equal(report.get("source_table_sha256"),
          "1ae851c29537ec405b15db1bd151710c25ad0f063a74970253f9d16754dc5d55",
          "LLCC original-production-corresponding SCT bytes")
    manifest = a.json(root + "/generated/tb371fc/docs/llcc-sm8250-registers.json")
    equal(manifest["sources"], list(LLCC_SOURCE_PINS.values()), "LLCC manifest source identities")
    equal({key: manifest[key] for key in LLCC_GENERATED_SUMMARY}, LLCC_GENERATED_SUMMARY,
          "LLCC actual manifest geometry")
    equal(manifest["source_init_slice_ids"], [1, 13, 31], "LLCC source init slice sequence")
    equal(manifest["geometry"]["original_physical_common_status_measured"], False,
          "LLCC physical bank measurement boundary")
    require(not a.text(root + "/generation.stderr").strip(), "LLCC generator stderr is nonempty")
    return {"inputs_verified_from_actual_bytes": 8, "outputs_verified_from_actual_bytes": 2,
            "all_primary_git_blob_identities_verified": True,
            "source_revision": report["source_revision"],
            "geometry_source_revision": report["geometry_source_revision"],
            "source_table_bytes": 640, "physical_bank_count_measured": False,
            "committed_output_pins": LLCC_GENERATED_HASHES}

SPSS_SOURCE_PINS = {
    "primary-r11/drivers/soc/qcom/subsys-pil-tz.c": {
        "source_path": "drivers/soc/qcom/subsys-pil-tz.c",
        "repository": "OnePlusOSS/android_kernel_oneplus_sm8250",
        "revision": "98719830b0de5584f5588bbe9f4c8304d97a64c6",
        "bytes": 32203,
        "git_blob_sha": "ed5e07c193b3841c60c94d92a4db259335d665c9",
        "sha256": "c574017a8f4e5ad5a4215434029d73bdc2c9ee9736b006429aa30a30ac219608",
    },
    "primary-r11/arch/arm64/boot/dts/vendor/qcom/kona.dtsi": {
        "source_path": "arch/arm64/boot/dts/vendor/qcom/kona.dtsi",
        "repository": "OnePlusOSS/android_kernel_oneplus_sm8250",
        "revision": "98719830b0de5584f5588bbe9f4c8304d97a64c6",
        "bytes": 132499,
        "git_blob_sha": "e9651f09bd579616787894dc8514429063623b2d",
        "sha256": "12b4b191b0f517b0c9fdd42c23ad428193ef71e63831bade996f9a18b88ece55",
    },
}
SPSS_GENERATED_HASHES = {
    "hw/misc/qcom-sm8250-spss-irq-mask-data.inc": "0bc4c9cbac7893f470ea1271274da41fa22b3fd0ea4b8e0fbd5ec1b4047c6cec",
    "tb371fc/docs/spss-irq-mask-sm8250-registers.json": "93cc367d15a60d82b8c765205791b16e3348c1a2b6e16e72d47c7d487b2627cb",
}
SPSS_GENERATED_SUMMARY = {
    "source_input_count": 2, "register_count": 1, "window_count": 1,
    "mapped_bytes": 4, "physical_base": 0x01881028,
    "err_ready_bit": 24, "pbl_done_bit": 25, "driver_mask": 0xfcffffff,
}


def audit_spss_generation(a, root, report):
    equal(report.get("generator_exit"), 0, "SPSS generator exit")
    equal(report.get("source_revision"), "98719830b0de5584f5588bbe9f4c8304d97a64c6",
          "SPSS fixed source revision")
    equal([item["path"] for item in report["inputs"]], list(SPSS_SOURCE_PINS),
          "Complete ordered SPSS primary input set")
    attempt_receipts = []
    for item in report["inputs"]:
        name = relative(item["path"])
        pin = SPSS_SOURCE_PINS[name]
        raw = a.read(root + "/inputs/" + name, 256 * 1024)
        blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        equal({key: item[key] for key in pin}, pin, "SPSS complete declared primary pin")
        equal(item["url"], "https://raw.githubusercontent.com/" + pin["repository"] +
              "/" + pin["revision"] + "/" + pin["source_path"], "SPSS immutable source URL")
        equal(len(raw), pin["bytes"], "SPSS actual primary byte count")
        equal(sha(raw), pin["sha256"], "SPSS actual primary SHA-256")
        equal(blob, pin["git_blob_sha"], "SPSS actual primary Git blob")
        for reported, fixed in (("expected_bytes", "bytes"),
                                ("expected_sha256", "sha256"),
                                ("expected_git_blob_sha", "git_blob_sha")):
            equal(item[reported], pin[fixed], "SPSS declared " + reported)
        attempts = item["attempts"]
        require(type(attempts) is list and 1 <= len(attempts) <= 3,
                "SPSS fetch attempts exceed bounded policy")
        for index, attempt in enumerate(attempts, 1):
            equal(attempt["attempt"], index, "SPSS fetch attempt sequence")
            require("source" not in attempt, "CI must fetch the pinned public URL, not a local substitute")
            if index == len(attempts):
                equal(attempt, {"attempt": index, "success": True, "bytes": len(raw)},
                      "SPSS final successful source fetch")
            else:
                equal(attempt["success"], False, "SPSS preceding fetch failed")
                equal(attempt["retryable"], True, "SPSS preceding failure is transient")
                equal(attempt["retry_delay_seconds"], index, "SPSS bounded backoff")
                code, kind = attempt["http_status"], attempt["error_type"]
                require((kind == "HTTPError" and type(code) is int and
                         (code == 429 or 500 <= code <= 599)) or
                        (code is None and kind in ("TimeoutError", "URLError")),
                        "SPSS source retry outside the transient-error policy")
                require(isinstance(attempt["error"], str) and attempt["error"],
                        "SPSS failed attempt has no diagnostic")
                if code is None:
                    require(re.search(r"timed?\s*out|timeout", attempt["error"], re.I),
                            "SPSS transport retry lacks a timeout diagnostic")
        attempt_receipts.append({"path": name, "attempts": attempts})
    checks = report["checks"]
    equal([item["name"] for item in checks], list(SPSS_GENERATED_HASHES),
          "SPSS generated output order")
    for item in checks:
        name = item["name"]
        raw = a.read(root + "/generated/" + name)
        equal(item["passed"], True, "SPSS generated comparison verdict")
        for key in ("generated_bytes", "committed_bytes"):
            equal(item[key], len(raw), "SPSS " + key)
        for key in ("generated_sha256", "committed_sha256"):
            equal(item[key], sha(raw), "SPSS " + key)
        equal(sha(raw), SPSS_GENERATED_HASHES[name], "SPSS reviewed output byte identity")
    stdout = [parse_json(line, "SPSS generation.stdout line")
              for line in a.text(root + "/generation.stdout").splitlines() if line.strip()]
    equal(stdout, [{"source_inputs": 2, "outputs": list(SPSS_GENERATED_HASHES),
                    "physical_base": 0x01881028, "mapped_bytes": 4,
                    "driver_mask": 0xfcffffff}], "SPSS actual generator stdout")
    equal(report["generated_contract"], SPSS_GENERATED_SUMMARY, "SPSS derived contract")
    manifest = a.json(root + "/generated/tb371fc/docs/spss-irq-mask-sm8250-registers.json")
    equal({key: manifest[key] for key in SPSS_GENERATED_SUMMARY}, SPSS_GENERATED_SUMMARY,
          "SPSS actual manifest geometry and source mask")
    equal(manifest["source_pins"], [dict(path=name, **pin,
          url="https://raw.githubusercontent.com/" + pin["repository"] + "/" +
              pin["revision"] + "/" + pin["source_path"])
          for name, pin in SPSS_SOURCE_PINS.items()], "SPSS manifest source identities")
    equal(manifest["source_resources"], [
        {"name": "sp2soc_irq_status", "base": 0x0188101c, "bytes": 4},
        {"name": "sp2soc_irq_clr", "base": 0x01881024, "bytes": 4},
        {"name": "sp2soc_irq_mask", "base": 0x01881028, "bytes": 4},
        {"name": "rmb_err", "base": 0x0188103c, "bytes": 4},
        {"name": "rmb_err_spare2", "base": 0x01882014, "bytes": 4},
    ], "SPSS named source resources")
    equal(manifest["out_of_scope_resources"], [r for r in manifest["source_resources"]
          if r["name"] != "sp2soc_irq_mask"], "SPSS unmapped source resources")
    equal(manifest["source_interrupt_cells"], [0, 352, 1], "SPSS source interrupt only")
    equal(manifest["source_pas_id"], 14, "SPSS source PAS ID only")
    equal(manifest["source_firmware_name"], "spss", "SPSS source firmware name only")
    require(not a.text(root + "/generation.stderr").strip(), "SPSS generator stderr is nonempty")
    return {"inputs_verified_from_actual_bytes": 2, "outputs_verified_from_actual_bytes": 2,
            "all_primary_git_blob_identities_verified": True,
            "source_revision": report["source_revision"],
            "immutable_source_fetch_attempts": attempt_receipts,
            "driver_mask": 0xfcffffff, "mapped_bytes": 4,
            "hardware_reset_value_measured": False,
            "firmware_or_pas_execution_claimed": False,
            "committed_output_pins": SPSS_GENERATED_HASHES}


def pack_values(values, code, bits, where, signed=False):
    return struct.pack(">" + str(len(values)) + code,
                       *(integer(v, bits, where, signed) for v in values))


def videocc_wire(s):
    require(type(s) is dict and type(s["globals"]) is dict, "Invalid VIDEOCC snapshot")
    equal(set(s["globals"]), set(GLOBALS), "VIDEOCC global field set")
    g = s["globals"]
    require(g["reference-profile"] in (0, 1), "Bad VIDEOCC profile")
    equal([g[k] for k in GLOBALS[1:]],
          [0x56494332, 55, 19, 4, 2, 5, 8, 4, 7, 4, 26, 220, CC], "VIDEOCC geometry")
    require(len(REGISTERS) == 55, "Internal register inventory error")
    equal(set(s["registers"]), {f"{o:04x}" for o in REGISTERS}, "VIDEOCC register keys")
    require(len(s["plls"]) == 2 and len(s["rcgs"]) == 5 and len(s["remote-votes"]) == 4,
            "Invalid VIDEOCC state array lengths")
    raw = pack_values([g[k] for k in GLOBALS[:13]], "I", 32, "VIDEOCC globals")
    raw += pack_values([g["physical-base"]], "Q", 64, "VIDEOCC base")
    raw += pack_values([s["registers"][f"{o:04x}"] for o in REGISTERS], "I", 32, "VIDEOCC registers")
    for field in ("active-l", "active-alpha", "pending-l", "pending-alpha"):
        raw += pack_values([p[field] for p in s["plls"]], "I", 32, field)
    for field in ("active-cfg", "pending-cfg"):
        raw += pack_values([r[field] for r in s["rcgs"]], "I", 32, field)
    for field in ("lock-deadline", "latch-deadline"):
        raw += pack_values([p[field] for p in s["plls"]], "q", 64, field, True)
    raw += pack_values([r["deadline"] for r in s["rcgs"]], "q", 64, "RCG deadlines", True)
    raw += pack_values([s["last-xo-hz"]], "Q", 64, "last XO")
    for field in ("locked", "ack", "calibrated"):
        raw += bytes(boolean(p[field], field) for p in s["plls"])
    raw += bytes(boolean(v, "remote vote") for v in s["remote-votes"])
    equal(set(s["inputs"]), set(INPUTS), "Input period keys")
    raw += pack_values([s["inputs"][name] for name in INPUTS], "Q", 64, "input periods")
    require(len(raw) == 474, "Independent VIDEOCC wire length differs")
    return raw


def video_wire(s):
    equal(set(s["globals"]), set(GDSC_GLOBALS), "VIDEO GDSC globals")
    equal([s["globals"][k] for k in GDSC_GLOBALS],
          [1, 0x56444731, 4, 4, 16, 3, 1, 0xf803, 0x80000000], "VIDEO GDSC identity")
    require(s["enabled-reference"] is True and len(s["domains"]) == 4, "Invalid VIDEO GDSC profile")
    raw = pack_values([s["globals"][k] for k in GDSC_GLOBALS], "I", 32, "VIDEO GDSC globals")
    raw += bytes((boolean(s["parent-known"], "parent-known"), boolean(s["parent-on"], "parent-on")))
    for i, d in enumerate(s["domains"]):
        equal(d["name"], GDSC_NAMES[i], "VIDEO domain name")
        equal([d["address"], d["dt-flags"], d["timeout-us"]],
              [CC + GDSC_OFFSETS[i], (1, 1, 3, 3)[i], 100], "VIDEO domain geometry")
        require(integer(d["control"], 32, "GDSC control") & ~0xf803 == 0, "Invalid GDSC control mask")
        raw += struct.pack(">IQIII", i, integer(d["address"], 64, "domain address"),
                           d["dt-flags"], d["timeout-us"], d["control"])
        raw += bytes((boolean(d["hw-request-known"], "hw known"), boolean(d["hw-request"], "hw request")))
        hw = bool(d["control"] & 2)
        require(not hw or i in (2, 3), "Hardware mode on undeclared VIDEO domain")
        request_known = d["hw-request-known"] if hw else True
        requested = d["hw-request"] if hw else not bool(d["control"] & 1)
        parents = [s["parent-on"]] if s["parent-known"] else [False, True]
        requests = [requested] if request_known else [False, True]
        states = {p and r for p in parents for r in requests}
        equal([d["request-known"], d["requested"], d["state-known"], d["powered"]],
              [request_known, requested, len(states) == 1, states == {True}], "VIDEO derived power state")
    require(len(raw) == 142, "Independent VIDEO GDSC wire length differs")
    return raw


def derive_clocks(s):
    periods = s["inputs"]
    inputs = {k: PERIOD_UNIT // integer(v, 64, "input period") if v else 0 for k, v in periods.items()}
    equal(inputs["bi_tcxo"], s["last-xo-hz"], "XO period versus observation")
    regs = {int(k, 16): v for k, v in s["registers"].items()}
    rates, demand = [0] * 19, [False] * 19
    for i, (offset, *_rest) in enumerate(DIVIDERS):
        equal(s["dividers"][i], regs[offset], "Divider QOM versus raw field")
        require(0 <= regs[offset] <= 3, "Out-of-range divider")
    if s["globals"]["reference-profile"]:
        for i, base in enumerate(PLL_BASES):
            p = s["plls"][i]
            if p["locked"] and regs[base] & 1 and regs[base + 12] == 7:
                require(regs[base] & 4 and regs[base + 56] == 1, "Locked PLL is not running")
                equal([regs[base + o] for o in (8, 16, 20, 24, 28, 32, 36, 40, 44)],
                      [0x44, 0x805, 0, 0x20485699, 0x2261,
                       0x029a699c if i == 0 else 0x329a699c, 0, 0, 0], "PLL fixed recipe")
                rates[17 + i] = inputs["bi_tcxo"] * p["active-l"] + ((inputs["bi_tcxo"] * p["active-alpha"]) >> 16)
        for offset, clock, _ in RCGS:
            demand[clock] = bool(regs[offset] & 2)
        for offset, _clock, parent, vote in BRANCHES:
            if regs[offset] & 1 or (vote >= 0 and s["remote-votes"][vote]):
                demand[parent] = True
        for _offset, clock, parent in DIVIDERS:
            if demand[clock]:
                demand[parent] = True
        for i, (_offset, clock, parents) in enumerate(RCGS):
            if not demand[clock]:
                continue
            cfg = s["rcgs"][i]["active-cfg"]
            selector, hid = (cfg >> 8) & 7, cfg & 31
            require(selector in (0, 1, 7) and not cfg & 0x3000, "Invalid active RCG configuration")
            parent = parents[(0, 1, 7).index(selector)]
            require(parent is not None, "Undeclared RCG input")
            hz = rates[parent] if type(parent) is int else inputs[parent]
            rates[clock] = hz * 2 // (hid + 1) if hid else hz
        for offset, clock, parent in DIVIDERS:
            divisor = regs[offset] + 1
            rates[clock] = (rates[parent] + divisor - 1) // divisor
        for offset, clock, parent, vote in BRANCHES:
            if regs[offset] & 1 or (vote >= 0 and s["remote-votes"][vote]):
                rates[clock] = rates[parent]
    equal(s["output_hz"], dict(zip(OUTPUTS, rates)), "Independent output frequency calculation")
    equal(s["outputs"], dict(zip(OUTPUTS, (PERIOD_UNIT // hz if hz else 0 for hz in rates))),
          "Independent output Clock periods")


def section(data, name, size):
    require(data[:8] == b"QEVM\x00\x00\x00\x03", "Invalid QEMU migration magic/version")
    encoded = name.encode("ascii")
    needle = bytes([len(encoded)]) + encoded + struct.pack(">II", 0, 1)
    positions, at = [], 0
    while True:
        at = data.find(needle, at)
        if at < 0:
            break
        header = at - 5
        if header >= 8 and data[header] in (1, 4):
            positions.append((header, at + len(needle)))
        at += 1
    require(len(positions) == 1, f"{name}: missing or duplicate actual section")
    header, payload = positions[0]
    identity = struct.unpack_from(">I", data, header + 1)[0]
    footer = b"\x7e" + struct.pack(">I", identity)
    require(data[payload + size:payload + size + 5] == footer, f"{name}: wrong complete section length/footer")
    return {"name": name, "instance_id": 0, "version": 1, "header_offset": header,
            "section_id": identity, "payload_offset": payload, "payload_bytes": size,
            "footer_offset": payload + size, "footer_hex": footer.hex()}


def qmp_calls(a, directory):
    path = directory + "/qmp.jsonl"
    calls, pending, last_id = [], None, 0
    for line_number, line in enumerate(a.text(path).splitlines(), 1):
        event = parse_json(line, f"{path}:{line_number}")
        require(type(event) is dict and len(event) == 1, f"Malformed QMP log {path}:{line_number}")
        if "send" in event:
            require(pending is None, f"Overlapping QMP requests {path}")
            request = event["send"]
            require(type(request) is dict and request.get("id") == last_id + 1 and
                    type(request["id"]) is int, f"Bad QMP request ID {path}")
            last_id = request["id"]
            pending = {"request": request, "response": None, "line": line_number}
            calls.append(pending)
        elif "receive" in event:
            response = event["receive"]
            require(type(response) is dict, f"Bad QMP response {path}")
            if "event" in response or "QMP" in response:
                continue
            require(pending is not None and response.get("id") == pending["request"]["id"],
                    f"Unmatched QMP response {path}")
            pending["response"] = response
            pending = None
        else:
            raise AuditFailure(f"Unknown QMP log entry {path}")
    return calls


def execute(call):
    return call["request"]["execute"]


def answer(call):
    response = call.get("response")
    require(type(response) is dict and "return" in response and "error" not in response,
            "Missing successful QMP response")
    return response["return"]


def flat_snapshot(s, v):
    queries = []
    def add(path, prop, value):
        queries.append(({"path": path, "property": prop}, value))
    for key in GLOBALS:
        add(DEVICE, key, s["globals"][key])
    for offset in REGISTERS:
        add(DEVICE, f"reg.{offset:04x}", s["registers"][f"{offset:04x}"])
    for i in range(2):
        for key in PLL_FIELDS:
            add(DEVICE, f"pll{i}-{key}", s["plls"][i][key])
    for i in range(5):
        for key in RCG_FIELDS:
            add(DEVICE, f"rcg{i}-{key}", s["rcgs"][i][key])
    add(DEVICE, "last-xo-hz", s["last-xo-hz"])
    for i in range(4):
        add(DEVICE, f"divider{i}-value", s["dividers"][i])
    for i in range(4):
        add(DEVICE, f"remote-vote{i}", s["remote-votes"][i])
    for name in OUTPUTS:
        add(DEVICE, "clk." + name + ".hz", s["output_hz"][name])
    for name in OUTPUTS:
        add(DEVICE + "/" + name, "qtest-clock-period", s["outputs"][name])
    for name in INPUTS:
        add(DEVICE + "/" + name, "qtest-clock-period", s["inputs"][name])
    add(VIDEO, "enabled-reference", v["enabled-reference"])
    for key in GDSC_GLOBALS:
        add(VIDEO, key, v["globals"][key])
    for key in ("parent-known", "parent-on"):
        add(VIDEO, key, v[key])
    for i in range(4):
        for key in GDSC_FIELDS:
            add(VIDEO, f"domain{i}." + key, v["domains"][i][key])
    return queries


def verify_qom(calls, expected, where):
    equal(len(calls), len(expected), where + ".getter count")
    for i, (call, (args, value)) in enumerate(zip(calls, expected)):
        equal(execute(call), "qom-get", where + ".pure getter")
        equal(call["request"].get("arguments"), args, where + ".getter arguments")
        equal(answer(call), value, where + f".reply[{i}]")


def migration_file_argument(call, expected_name):
    args = call["request"].get("arguments")
    require(type(args) is dict and type(args.get("channels")) is list and len(args["channels"]) == 1,
            "Missing file migration channel")
    channel = args["channels"][0]
    equal(channel.get("channel-type"), "main", "migration channel type")
    addr = channel.get("addr")
    require(type(addr) is dict and addr.get("transport") == "file" and
            PurePosixPath(addr.get("filename", "")).name == expected_name, "Wrong actual migration input file")


def qtest_commands(a, directory):
    path, calls, pending = directory + "/qtest.jsonl", [], None
    for i, line in enumerate(a.text(path).splitlines(), 1):
        record = parse_json(line, f"{path}:{i}")
        if "send" in record:
            require(pending is None and isinstance(record["send"], str), "Invalid QTest command framing")
            integer(record.get("virtual_ns_before"), 64, "QTest virtual time")
            pending = {"command": record["send"], "virtual_ns_before": record["virtual_ns_before"], "reply": None}
            calls.append(pending)
        elif "receive" in record:
            text = record["receive"]
            require(isinstance(text, str), "Invalid QTest reply")
            if text.startswith("IRQ "):
                continue
            require(pending is not None and (text == "OK" or text.startswith("OK ")), "Failed QTest command")
            pending["reply"] = text
            pending = None
        else:
            raise AuditFailure("Unknown QTest log entry")
    require(pending is None, "Truncated QTest transcript")
    return calls


def device_mmio(command):
    parts = command.split()
    if not parts or parts[0] not in ("readb", "readw", "readl", "readq", "writeb", "writew", "writel", "writeq"):
        return False
    address = int(parts[1], 0)
    camera = (0xad07004, 0xad08004, 0xad09004, 0xad0a004, 0xad0b004, 0xad0c144)
    return (CC <= address < CC + 0x10000 or 0x100000 <= address < 0x2f0000 or
            0x18200000 <= address < 0x18230000 or any(b <= address < b + 4 for b in camera))


def source_trace(a, directory, saved, snapshot, video):
    calls = qmp_calls(a, directory)
    indices = [i for i, c in enumerate(calls) if execute(c) == "migrate"]
    require(len(indices) == 1, "Source must perform one actual migrate")
    end = indices[0]
    migration_file_argument(calls[end], saved["file"])
    starts = [i for i in range(end) if execute(calls[i]) == "stop"]
    require(starts, "No actual source stop")
    segment = calls[starts[-1] + 1:end]
    require(segment and execute(segment[0]) == "query-status" and answer(segment[0]).get("running") is False,
            "Source did not confirm stopped state")
    verify_qom(segment[1:], flat_snapshot(snapshot, video), "source stopped QOM")
    returned = [answer(c) for c in calls[end + 1:] if execute(c) == "query-migrate"]
    require(any(r == saved["source_query_migrate"] for r in returned), "Source completion missing from actual QMP")
    require(saved["source_query_migrate"].get("status") == "completed", "Source migration failed")
    require(any(execute(c) == "query-status" and answer(c).get("status") == "postmigrate" for c in calls[end + 1:]),
            "Source postmigrate status missing")
    qt = qtest_commands(a, directory)
    writes = [c for c in qt if re.fullmatch(r"writel 0x80010000 (?:0x)?[0-9a-fA-F]+", c["command"])]
    require(writes and int(writes[-1]["command"].split()[2], 0) == saved["ram_witness"], "Missing source RAM witness")
    return {"source_qom_getters_verified": len(flat_snapshot(snapshot, video)), "source_qmp_completed": True}


def target_trace(a, directory, saved, completed):
    first = a.json(directory + "/destination-before-first-device-mmio.json")
    equal(first, completed["first_target_observation"], "Saved versus reported first target observation")
    equal(first["snapshot"], saved["snapshot"], "First target complete VIDEOCC snapshot")
    equal(first["video_gdsc"], saved["video_gdsc"], "First target complete VIDEO GDSC snapshot")
    for key in ("device_mmio_since_incoming", "qmp_cont_since_incoming", "guest_cpu_executed"):
        require(first.get(key) is False, f"First observation {key} is not false")
    before, after = first["device_mmio_count_before_incoming"], first["device_mmio_count_after_first_observation"]
    integer(before, 32, "target prewarm MMIO count")
    equal(after, before, "Target MMIO count across first observation")
    calls = qmp_calls(a, directory)
    incoming = [i for i, c in enumerate(calls) if execute(c) == "migrate-incoming"]
    require(len(incoming) == 1, "Target must perform one incoming migration")
    start = incoming[0]
    migration_file_argument(calls[start], saved["file"])
    getter_indices = [i for i in range(start + 1, len(calls)) if execute(calls[i]) == "qom-get"]
    require(getter_indices, "No actual first-target QOM reads")
    initial = getter_indices[0]
    waiting = calls[start + 1:initial]
    require(waiting and all(execute(c) in ("query-migrate", "query-status") for c in waiting),
            "Target mutated before first QOM observation")
    require(any(execute(c) == "query-migrate" and answer(c) == first["query_migrate"] for c in waiting),
            "Actual target completed response differs")
    require(first["query_migrate"].get("status") == "completed", "Target did not complete migration")
    require(execute(waiting[-1]) == "query-status" and answer(waiting[-1]).get("running") is False,
            "Target not observed paused before QOM")
    expected = flat_snapshot(saved["snapshot"], saved["video_gdsc"])
    verify_qom(calls[initial:initial + len(expected)], expected, "First target actual QOM")
    require(not any(execute(c) == "cont" for c in calls[:initial + len(expected)]),
            "Target cont occurred before complete first observation")
    require(any(execute(c) == "cont" for c in calls[initial + len(expected):]), "Target never continued")
    qt = qtest_commands(a, directory)
    markers = [i for i, c in enumerate(qt) if c["command"] == f"clock_set {saved['source_virtual_ns']}"]
    require(len(markers) == 1, "Ambiguous target incoming virtual-time marker")
    marker = markers[0]
    equal(sum(device_mmio(c["command"]) for c in qt[:marker]), before, "Recounted prewarm device MMIO")
    require(len(qt) > marker + 2, "Missing target RAM witness/drain commands")
    for c in qt[marker + 1:marker + 3]:
        equal(c["command"], "readl 0x80010000", "Only RAM read before resumed time")
        equal(int(c["reply"].split()[1], 0), saved["ram_witness"], "Actual target RAM witness")
        equal(c["virtual_ns_before"], saved["source_virtual_ns"], "First target RAM read epoch")
    return {"qmp_first_getters_verified": len(expected), "prewarm_mmio_recounted": before,
            "mmio_delta_before_first_observation": after - before, "qmp_cont_before_first_observation": 0,
            "ram_witness_verified": True,
            "cross_channel_order_basis": "pinned harness order + counters + QTest clock_set boundary; no shared timestamps"}


def audit_video(a, root, report, binary_hash):
    equal(report.get("test_script_sha256"), VIDEOCC_TEST_SHA256, "Pinned VIDEOCC harness")
    equal([r["name"] for r in report["checks"]], CHECK_NAMES, "41 unique VIDEOCC check names/order")
    for key in ("qemu_sha256_before", "qemu_sha256_after"):
        equal(report[key], binary_hash, "VIDEOCC " + key)
    equal(report["source_sha256"], {PurePosixPath(k).name: v for k, v in SOURCE_HASHES.items()}, "VIDEOCC ten source pins")
    sources, completed = report["migration_sources"], report["migrations"]
    equal([s["file"] for s in sources], list(ORIGINALS), "Four original source streams")
    equal([m["name"] for m in completed], list(MIGRATION_NAMES), "Three completed migration identities")
    equal([s["source_directory"] for s in sources], list(SOURCE_DIRS), "Source fixture directories")
    file_records = report["actual_stream_files"]
    equal([f["name"] for f in file_records], sorted(ORIGINALS + CORRUPTIONS), "Six retained stream files")
    actual_files = {}
    for item in file_records:
        name = basename(item["name"])
        raw = a.read(root + "/" + name, 256 * 1024 * 1024)
        equal(item["bytes"], len(raw), name + ".file size")
        equal(item["sha256"], sha(raw), name + ".file digest")
        actual_files[name] = raw
    details = []
    for i, saved in enumerate(sources):
        name, directory = saved["file"], root + "/" + basename(saved["source_directory"])
        raw = actual_files[name]
        equal(saved["bytes"], len(raw), name + ".source size")
        equal(saved["sha256"], sha(raw), name + ".source hash")
        require(saved.get("stream_modified") is False and saved.get("source_status") == "completed", "Invalid original stream status")
        observed = a.json(directory + "/source-before-save.json")
        equal(observed, {"videocc": saved["snapshot"], "video_gdsc": saved["video_gdsc"]}, "Saved source QOM versus report")
        equal(a.json(directory + "/source-query-migrate.json"), saved["source_query_migrate"], "Source actual migrate receipt")
        snapshot, video = observed["videocc"], observed["video_gdsc"]
        expected = {"videocc": videocc_wire(snapshot), "video_gdsc": video_wire(video)}
        derive_clocks(snapshot)
        equal(snapshot["globals"]["reference-profile"], int(i != 3), "Source profile case")
        expected_inputs = [19_200_000, 19_200_000, (111_111_113, 96_000_001, 123_456_789, 0)[i],
                           (32_003, 0, 0, 32_003)[i]]
        equal(snapshot["inputs"], dict(zip(INPUTS, (PERIOD_UNIT // hz if hz else 0 for hz in expected_inputs))),
              "Scenario source Clock periods")
        declared_sections = a.json(root + "/" + name + ".sections.json")
        equal(declared_sections, saved["sections"], "Actual section receipt versus report")
        inspected = {}
        for label, section_name in (("videocc", TYPE), ("video_gdsc", "qcom-video-gdsc")):
            wire = expected[label]
            found = section(raw, section_name, len(wire))
            payload = raw[found["payload_offset"]:found["footer_offset"]]
            require(payload == wire, f"{name}/{label}: full actual payload differs from independent encoding")
            declared = saved["sections"][label]
            for key, value in found.items():
                equal(declared.get(key), value, name + ".section." + key)
            payload_name = name + f".{label}-v1.payload.bin"
            equal(declared["payload_file"], payload_name, "Payload filename")
            require(a.read(root + "/" + payload_name) == payload, "Retained payload bytes differ from actual stream")
            equal(declared["payload_sha256"], sha(payload), "Actual payload digest")
            inspected[label] = {**found, "sha256": sha(payload), "every_byte_matches_source_qom": True}
        trace = source_trace(a, directory, saved, snapshot, video)
        if i < 3:
            migration = completed[i]
            check_verdicts(migration, "Completed migration", True)
            for key in ("source_status", "target_status"):
                equal(migration[key], "completed", key)
            equal(migration["snapshot_file"], name, "Completed source file")
            equal(migration["snapshot_sha256"], sha(raw), "Completed stream digest")
            require(migration["stream_modified"] is False and migration["continued_after_restore"] is True,
                    "Missing completed continuation")
            equal(migration["source_directory"], saved["source_directory"], "Completed source fixture")
            equal(migration["target_directory"], saved["source_directory"].replace("-source", "-target"), "Completed target fixture")
            trace.update(target_trace(a, root + "/" + migration["target_directory"], saved, migration))
        details.append({"file": name, "bytes": len(raw), "sha256": sha(raw), "sections": inspected, **trace})
    steady = actual_files[ORIGINALS[0]]
    cc_section = section(steady, TYPE, 474)
    negatives = []
    rejection_rows = report["rejected_migrations"] + report["corrupted_stream_rejections"]
    expected_negative = (
        ("enabled_to_disabled", ORIGINALS[0], "reject-enabled_to_disabled", "0 != 1", False),
        ("disabled_to_enabled", ORIGINALS[3], "reject-disabled_to_enabled", "1 != 0", False),
        ("physical_base", CORRUPTIONS[0], "reject-corrupt-physical_base", "abf0000 != abf0004", True),
        ("shared_branch_reserved_bit", CORRUPTIONS[1], "reject-corrupt-shared_branch_reserved_bit",
         TYPE + f": invalid migrated register {REGISTERS.index(0xc34)}", True))
    require(len(rejection_rows) == 4, "Wrong rejection count")
    for row, (name, filename, target, diagnostic, mutated) in zip(rejection_rows, expected_negative):
        equal(row["name"], name, "Rejection identity")
        equal(row["snapshot_file"], filename, "Rejected file")
        equal(row["target_directory"], target, "Rejection directory")
        equal(row["stream_modified"], mutated, "Rejection modified flag")
        equal(row["snapshot_sha256"], sha(actual_files[filename]), "Rejected actual file hash")
        equal(row["destination_exit"], 1, "Actual recorded receiver exit")
        equal(row["expected_diagnostic"], diagnostic, "Specific rejection diagnostic")
        directory = root + "/" + target
        stderr = a.text(directory + "/qemu.stderr")
        require(all(s in stderr for s in (TYPE, diagnostic, "load of migration failed", "error while loading state")),
                f"{target}: actual rejection stderr is missing required detail")
        calls = qmp_calls(a, directory)
        incoming = [c for c in calls if execute(c) == "migrate-incoming"]
        require(len(incoming) == 1, "Rejection did not execute incoming migration")
        migration_file_argument(incoming[0], filename)
        evidence = {"name": name, "file": filename, "destination_exit_from_pinned_harness": 1,
                    "actual_specific_stderr_verified": True}
        if mutated:
            changed = actual_files[filename]
            require(len(changed) == len(steady), "Corruption changed stream length")
            positions = [i for i, (a0, b0) in enumerate(zip(steady, changed)) if a0 != b0]
            require(len(positions) == 1 and (steady[positions[0]] ^ changed[positions[0]]).bit_count() == 1,
                    "Corruption is not exactly one bit in the entire actual stream")
            relative_offset, width, old, mask = ((52, 8, CC, 4) if name == "physical_base" else
                (60 + 4 * REGISTERS.index(0xc34), 4, 4, 2))
            absolute = cc_section["payload_offset"] + relative_offset
            equal(int.from_bytes(steady[absolute:absolute + width], "big"), old, "Original mutation field")
            equal(int.from_bytes(changed[absolute:absolute + width], "big"), old ^ mask, "Mutated field")
            equal(positions, [absolute + width - 1], "Actual one-bit change location")
            equal(row["field_offset"], relative_offset, "Reported mutation field")
            equal(row["changed_byte_offsets"], positions, "Reported changed bytes")
            equal(row["changed_bits"], 1, "Reported changed bits")
            # Other device section and both exact footers must remain intact.
            section(changed, TYPE, 474)
            gs = section(changed, "qcom-video-gdsc", 142)
            require(changed[gs["payload_offset"]:gs["footer_offset"]] == video_wire(sources[0]["video_gdsc"]),
                    "Corruption damaged independent VIDEO GDSC")
            evidence.update(changed_bits_recomputed=1, changed_byte_offsets=positions)
        negatives.append(evidence)
    return {"checks": 41, "completed": 3, "profile_rejections": 2, "single_bit_rejections": 2,
            "original_files": 4, "total_stream_files": 6, "sources": details, "rejections": negatives,
            "all_source_and_target_qom_replies_checked": True,
            "source_and_target_output_rates_recomputed": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--out", type=Path, help="New JSON receipt file; omitted means stdout only")
    parser.add_argument("--expected-zip-sha256", help="Optional independent GitHub artifact digest")
    args = parser.parse_args()
    result = {"schema": "tb371fc-spss-integration-independent-zip-audit-v1", "passed": False,
              "expected_head": args.expected_head, "reviewed_contract_head": REVIEWED_HEAD,
              "auditor_sha256": file_sha(Path(__file__)),
              "read_only_zip": True, "extracted_members": 0, "guest_executed": False,
              "native_android_boot_claimed": False,
              "scope": "All 100 gate contracts and real aggregate/individual receipts, "
                       "actual QEMU/clean-Linux bytes, VIDEOCC complete streams, "
                       "VIDEOCC, DSI, DP, LLCC and SPSS source regeneration",
              "dsi_wire_audit_performed_by_this_script": False,
              "dispcc_wire_audit_performed_by_this_script": False,
              "dp_wire_audit_performed_by_this_script": False,
              "llcc_wire_audit_performed_by_this_script": False,
              "spss_wire_audit_performed_by_this_script": False,
              "requires_supplementary_wire_audits": ["DSI 2926 + MDSS 64",
                                                       "DISPCC 1676 + MDSS 64", "DP 791 + MDSS 64", "LLCC 783", "SPSS 25"]}
    artifact = None
    try:
        require(re.fullmatch(r"[0-9a-f]{40}", args.expected_head) is not None, "Expected a complete commit SHA")
        equal(args.expected_head, REVIEWED_HEAD, "Reviewed exact source commit")
        require(args.zip.is_file(), "ZIP path is not a file")
        if args.out:
            require(not args.out.exists() and args.out.resolve() != args.zip.resolve(), "Output must be a new receipt file")
        artifact = Artifact(args.zip)
        zip_hash = file_sha(args.zip)
        if args.expected_zip_sha256:
            equal(zip_hash, args.expected_zip_sha256, "Independent artifact API digest")
        result["zip"] = {"path": str(args.zip.resolve()), "bytes": args.zip.stat().st_size,
                         "sha256": zip_hash, "members": len(artifact.entries)}
        print("Auditing aggregate and all individual gate receipts", file=sys.stderr, flush=True)
        binary = artifact.locate("qemu-system-aarch64")
        binary_hash = artifact.hash(binary)
        with artifact.zip.open(binary) as stream:
            require(stream.read(4) == b"\x7fELF", "QEMU member is not ELF")
        result["qemu"] = {"member": binary, **artifact.verified[binary]}
        components, selected, _ = audit_suite(artifact, "components", 95, args.expected_head, binary_hash)
        linux, _, linux_report = audit_suite(artifact, "linux", 5, args.expected_head, binary_hash)
        result["components"], result["linux"] = components, linux
        ci_root = str(PurePosixPath(components["aggregate"]).parent.parent)
        equal(artifact.text(ci_root + "/source-commit.txt"),
              args.expected_head + "\n", "Actual artifact source identity")
        result["ci_harness_unittests"] = 33
        kernel = artifact.locate("clean-linux/artifacts/Image")
        kernel_hash = artifact.hash(kernel)
        equal(linux_report["kernel_sha256"], kernel_hash, "Actual clean Linux kernel bytes")
        build = artifact.json(artifact.locate("clean-linux/artifacts/build.json"))
        equal(build, linux_report["kernel_build_receipt"], "Clean Linux build receipt")
        for key, expected in (("source_version", "6.12.114"), ("source_tar_sha256", "7a8589912bd7a8adb07fed074283652cf482a35facef8954554654f0154b4af3"),
                              ("patches_applied_by_script", []), ("build_completed", True), ("image_sha256", kernel_hash)):
            equal(build[key], expected, "Clean Linux " + key)
        print("Auditing pinned source inputs and generated bytes", file=sys.stderr, flush=True)
        result["generation"] = audit_generation(artifact, *selected["videocc-source-generation"])
        result["dsi_generation"] = audit_dsi_generation(
            artifact, *selected["dsi-pll-source-generation"])
        result["dp_generation"] = audit_dp_generation(
            artifact, *selected["dp-pll-source-generation"])
        result["llcc_generation"] = audit_llcc_generation(
            artifact, *selected["llcc-source-generation"])
        result["spss_generation"] = audit_spss_generation(
            artifact, *selected["spss-irq-mask-source-generation"])
        print("Auditing actual migration bytes, source QOM and first target observations", file=sys.stderr, flush=True)
        result["videocc"] = audit_video(artifact, *selected["videocc-sm8250-reference"], binary_hash)
        artifact.unchanged()
        result["verified_files"] = artifact.verified
        result["passed"] = True
    except Exception as exc:
        result["error"] = str(exc)
        result["traceback"] = traceback.format_exc()
        if artifact:
            result["verified_files_before_failure"] = artifact.verified
    finally:
        if artifact:
            artifact.zip.close()
    encoded = json.dumps(result, indent=2) + "\n"
    if args.out and not args.out.exists():
        try:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            with args.out.open("x", encoding="utf-8") as stream:
                stream.write(encoded)
        except Exception as exc:
            result["passed"] = False
            result["output_error"] = str(exc)
    if args.out:
        print(json.dumps({"passed": result["passed"], "out": str(args.out), "error": result.get("error"),
                          "qemu_sha256": result.get("qemu", {}).get("sha256")}))
    else:
        print(encoded, end="")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
