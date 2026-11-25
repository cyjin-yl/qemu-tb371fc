#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure parser/producer/scope tests; no network, compiled emulator or device."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('geni_hs_generation_under_test',
                                             Path(__file__).with_name('gen_geni_hs_sm8250.py'))
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)


def consumer_fixture():
    # Minimal independent syntax fixtures, not excerpts from the implementation
    # under test. Full immutable inputs and their individual mutants are covered
    # separately by verify_geni_hs_sm8250_generation.py.
    return {
        'vendor_header': 'enum se_protocol_types { NONE, SPI, UART, I2C, I3C };\n'
                         'enum se_xfer_mode { INVALID, FIFO_MODE, GSI_DMA, SE_DMA };\n',
        'upstream_header': 'enum geni_se_protocol_type { GENI_SE_NONE, GENI_SE_SPI, GENI_SE_UART };\n'
                           'enum geni_se_xfer_mode { GENI_SE_INVALID, GENI_SE_FIFO, GENI_SE_DMA, GENI_GPI_DMA };\n',
        'vendor_platform_se': '''
int get_se_proto(void *base) { return (geni_read_reg(base, GENI_FW_REVISION_RO) & FW_REV_PROTOCOL_MSK) >> FW_REV_PROTOCOL_SHFT; }
int get_se_m_fw(void *base) { return geni_read_reg(base, GENI_FW_REVISION_RO) & FW_REV_VERSION_MSK; }
int get_se_s_fw(void *base) { return geni_read_reg(base, GENI_FW_S_REVISION_RO) & FW_REV_VERSION_MSK; }
int se_geni_clks_on(void *rsc) {
 clk_prepare_enable(rsc->m_ahb_clk); clk_prepare_enable(rsc->s_ahb_clk); clk_prepare_enable(rsc->se_clk);
 return 0;
}
''',
        'vendor-serial': '''
static int msm_geni_serial_get_ver_info(void *uport) {
 if (get_se_proto(uport->membase) != UART) return -1;
 get_se_m_fw(uport->membase); get_se_s_fw(uport->membase); geni_se_qupv3_hw_version(uport);
 return 0;
}
static int msm_geni_serial_port_setup(void *msm_port) { msm_port->xfer_mode = SE_DMA; return 0; }
''',
        'upstream_serial': '''
static int qcom_geni_serial_port_setup(void *port) {
 int proto = geni_se_read_proto(port);
 if (proto != GENI_SE_UART) return -1;
 return 0;
}
''',
        'vendor_se': '''
unsigned int geni_se_get_qup_hw_version(void *se) { return readl_relaxed(wrapper->base + QUP_HW_VER_REG); }
void geni_se_select_mode(void *se, int mode) { switch (mode) { case GENI_SE_FIFO: break; case GENI_SE_DMA: break; } }
''',
        'upstream_se': '''
struct se_fw_hdr {
 __le32 magic; __le32 version; __le32 core_version;
 __le16 serial_protocol; __le16 fw_version; __le16 cfg_version;
 __le16 fw_size_in_items; __le16 fw_offset; __le16 cfg_size_in_items;
 __le16 cfg_idx_offset; __le16 cfg_val_offset;
};
#define SE_MAGIC_NUM 0x57464553
#define FW_REV_VERSION_MSK GENMASK(7, 0)
#define SE_GENI_FW_REVISION 0x1000
#define SE_GENI_S_FW_REVISION 0x1004
static void geni_write_fw_revision(void *se, unsigned serial_protocol, unsigned fw_version) {
 unsigned reg = FIELD_PREP(FW_REV_PROTOCOL_MSK, serial_protocol);
 reg |= FIELD_PREP(FW_REV_VERSION_MSK, fw_version);
 writel(reg, se->base + SE_GENI_FW_REVISION);
 writel(reg, se->base + SE_GENI_S_FW_REVISION);
}
''',
    }


class IntegerTests(unittest.TestCase):
    def test_bit_and_genmask(self):
        self.assertEqual(g.CIntegers().expression('BIT(14) | GENMASK(3, 0)'), 0x400f)

    def test_complete_rx_mask(self):
        self.assertEqual(g.CIntegers().expression('GENMASK(4, 0) | GENMASK(10, 5) | BIT(14) | GENMASK(13, 12)'), 0x77ff)

    def test_c_unsigned_suffixes(self):
        self.assertEqual(g.CIntegers().expression('(32U << 24) | (16u << 16)'), 0x20100000)

    def test_recursive_named_definitions(self):
        self.assertEqual(g.CIntegers('#define SIZE 64\n#define WORDS (SIZE / 4)\n').name('WORDS'), 16)

    def test_continued_macro(self):
        self.assertEqual(g.CIntegers('#define MASK (BIT(0) | \\\n+ BIT(4))\n').name('MASK'), 17)

    def test_duplicate_define_rejected(self):
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            g.CIntegers('#define X 1\n#define X 1\n').name('X')

    def test_missing_define_rejected(self):
        with self.assertRaises(ValueError):
            g.CIntegers().name('MISSING')

    def test_cyclic_define_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cyclic'):
            g.CIntegers('#define A B\n#define B A\n').name('A')

    def test_python_call_not_executed(self):
        with self.assertRaises(ValueError):
            g.CIntegers().expression('__import__("os").system("false")')

    def test_boolean_not_an_integer(self):
        with self.assertRaises(ValueError):
            g.CIntegers().expression('True')

    def test_shift_out_of_range(self):
        with self.assertRaises(ValueError):
            g.CIntegers().expression('1 << 64')

    def test_reversed_mask(self):
        with self.assertRaises(ValueError):
            g.CIntegers().expression('GENMASK(3, 4)')

    def test_fractional_quotient(self):
        with self.assertRaises(ValueError):
            g.CIntegers().expression('7 / 2')

    def test_negative_clock_parent(self):
        self.assertEqual(g.CIntegers().expression('-4'), -4)


class StructureTests(unittest.TestCase):
    def test_real_definition_not_if_call(self):
        text = 'static int probe(int x) { return x; }\nint other(void) { if (!probe(1)) { return 0; } return 1; }'
        self.assertIn('return x;', g.function(text, 'probe'))

    def test_duplicate_function_rejected(self):
        with self.assertRaisesRegex(ValueError, 'found 2'):
            g.function('int probe(void) { return 1; }\nint probe(void) { return 2; }', 'probe')

    def test_comment_definition_ignored(self):
        self.assertEqual(g.function('/* int f(void) { nope; } */\nint f(void) { ok; }', 'f').strip(), 'ok;')

    def test_braces_in_string_and_comment(self):
        self.assertIn('return 3;', g.function('int f(void) { const char *s = "} // {"; /* } */ return 3; }', 'f'))

    def test_block_trailing_space(self):
        self.assertEqual(g.block('x = {   3 };', r'x = \{\s*', 'x').strip(), '3')

    def test_unterminated_block_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unterminated'):
            g.function('int f(void) { return 1;', 'f')

    def test_initializer_nested_commas(self):
        self.assertEqual(g.split_fields('"a,b", GENMASK(3, 0), {1, 2}, 4'), ['"a,b"', 'GENMASK(3, 0)', '{1, 2}', '4'])

    def test_initializer_unbalanced_rejected(self):
        with self.assertRaises(ValueError):
            g.split_fields('1, (2, 3]')

    def test_implicit_and_explicit_enum(self):
        self.assertEqual(g.enum_values('enum test { NONE, UART = 2, NEXT, LAST = 255 };', 'test'),
                         {'NONE': 0, 'UART': 2, 'NEXT': 3, 'LAST': 255})

    def test_duplicate_enum_member_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            g.enum_values('enum test { UART, UART };', 'test')

    def test_named_dt_node_avoids_other_same_address(self):
        text = 'spi: spi@998000 { reg = <0x998000 0x4000>; }; uart: serial@998000 { reg = <0x998000 0x4000>; };'
        self.assertEqual(g.dt_reg(g.dt_node(text, 'uart')), [0x998000, 0x4000])

    def test_duplicate_dt_node_rejected(self):
        with self.assertRaises(ValueError):
            g.dt_node('uart: a@1 { reg=<1 4>; }; uart: a@2 { reg=<2 4>; };', 'uart')

    def test_duplicate_property_rejected(self):
        with self.assertRaises(ValueError):
            g.dt_reg('reg = <1 4>; reg = <2 4>;')

    def test_unsupported_dt_cell_count_rejected(self):
        with self.assertRaises(ValueError):
            g.dt_reg('reg = <0 1 0 4>;')


class ProducerAndScopeTests(unittest.TestCase):
    def setUp(self):
        self.policy, self.facts, _ = g.load_reference_inputs()
        self.sources = consumer_fixture()
        self.registers = {'fields': {'FW_REV_PROTOCOL_MSK': 0xff00, 'FW_REV_PROTOCOL_SHFT': 8,
                                     'FW_REV_VERSION_MSK': 0xff}}

    def derive(self):
        return g.parse_producer_and_consumers(self.sources, self.registers, self.facts)

    def test_reviewed_header_0301_produces_0201(self):
        result = self.derive()
        self.assertEqual(result['revision_word'], 0x201)
        self.assertEqual(result['ignored_firmware_high_byte'], 3)
        self.assertEqual(result['configuration_revision_byte'], 8)

    def test_firmware_high_byte_does_not_change_word(self):
        self.facts['header_scalars']['firmware_version'] = 0xa301
        self.assertEqual(self.derive()['revision_word'], 0x201)

    def test_firmware_low_byte_disagreement_rejected(self):
        self.facts['header_scalars']['firmware_version'] = 0x302
        with self.assertRaisesRegex(ValueError, 'producer result disagreement'):
            self.derive()

    def test_protocol_disagreement_rejected(self):
        self.facts['header_scalars']['protocol'] = 3
        with self.assertRaisesRegex(ValueError, 'protocol'):
            self.derive()

    def test_public_layout_and_private_offset_crosscheck(self):
        result = self.derive()
        self.assertEqual(result['sefw_header_fields']['serial_protocol'], {'offset': 12, 'bytes': 2})
        self.assertEqual(result['sefw_header_fields']['fw_version'], {'offset': 14, 'bytes': 2})
        self.assertEqual(result['sefw_header_bytes'], 28)

    def test_changed_header_field_width_rejected(self):
        self.sources['upstream_se'] = self.sources['upstream_se'].replace('__le16 serial_protocol', '__le32 serial_protocol')
        with self.assertRaises(ValueError):
            self.derive()

    def test_programming_offset_disagreement_rejected(self):
        self.sources['upstream_se'] = self.sources['upstream_se'].replace('0x1004', '0x1008')
        with self.assertRaisesRegex(ValueError, 'programming offset'):
            self.derive()

    def test_extra_producer_write_rejected(self):
        self.sources['upstream_se'] = self.sources['upstream_se'].replace('writel(reg, se->base + SE_GENI_S_FW_REVISION);',
            'writel(reg, se->base + SE_GENI_S_FW_REVISION); writel(reg, se->base);')
        with self.assertRaisesRegex(ValueError, 'additional writes'):
            self.derive()

    def test_vendor_and_upstream_dma_numbers_are_distinct(self):
        result = self.derive()
        self.assertEqual(result['vendor_se_dma_mode'], 3)
        self.assertEqual(result['upstream_se_dma_mode'], 2)

    def test_default_off_policy(self):
        g.validate_reference_inputs(self.policy, self.facts)
        self.policy['default_enabled'] = True
        with self.assertRaises(ValueError):
            g.validate_reference_inputs(self.policy, self.facts)

    def test_physical_selection_claim_rejected(self):
        self.facts['selection']['se6_selection_established'] = True
        with self.assertRaises(ValueError):
            g.validate_reference_inputs(self.policy, self.facts)

    def test_physical_wrapper_claim_rejected(self):
        self.policy['physical_wrapper_version_known'] = True
        with self.assertRaises(ValueError):
            g.validate_reference_inputs(self.policy, self.facts)

    def test_dma_completion_claim_rejected(self):
        self.policy['dma_status_or_completion_implemented'] = True
        with self.assertRaises(ValueError):
            g.validate_reference_inputs(self.policy, self.facts)

    def test_hardware_alias_readback_claim_rejected(self):
        self.facts['producer']['write_to_readonly_alias_proved'] = True
        with self.assertRaises(ValueError):
            g.validate_reference_inputs(self.policy, self.facts)

    def test_raw_binary_inclusion_claim_rejected(self):
        self.facts['raw_binary_disassembly_paths_serials_or_bootargs_included'] = True
        with self.assertRaises(ValueError):
            g.validate_reference_inputs(self.policy, self.facts)


class IdentityAndOutputTests(unittest.TestCase):
    def test_full_bytes_not_only_parsed_prefix_are_pinned(self):
        raw = b'#define VALUE 1\n/* complete trailing evidence */\n'
        pin = {'id': 'synthetic', 'bytes': len(raw), 'sha256': g.digest(raw), 'git_blob_sha1': g.git_blob(raw)}
        self.assertEqual(g.verify_source_bytes(pin, raw), raw.decode())
        with self.assertRaisesRegex(ValueError, 'complete public source identity'):
            g.verify_source_bytes(pin, raw[:-2] + b'x\n')

    def test_utf8_not_normalized_before_hashing(self):
        raw = '\ufeffx\r\n'.encode()
        pin = {'id': 'utf8', 'bytes': len(raw), 'sha256': g.digest(raw), 'git_blob_sha1': g.git_blob(raw)}
        self.assertEqual(g.verify_source_bytes(pin, raw).encode(), raw)

    def test_identity_check_precedes_decode(self):
        raw = b'valid'; pin = {'id': 'decode-order', 'bytes': len(raw), 'sha256': g.digest(raw), 'git_blob_sha1': g.git_blob(raw)}
        with self.assertRaisesRegex(ValueError, '^complete public source identity'):
            g.verify_source_bytes(pin, b'\xffalid')

    def test_wrong_git_blob_is_rejected_even_with_correct_sha256(self):
        raw = b'abc'; pin = {'id': 'blob', 'bytes': 3, 'sha256': g.digest(raw), 'git_blob_sha1': '0' * 40}
        with self.assertRaises(ValueError):
            g.verify_source_bytes(pin, raw)

    def test_missing_generated_output_rejected(self):
        expected = {path: b'x' for path in g.OUTPUTS}
        with self.assertRaisesRegex(ValueError, 'output set'):
            g.compare_outputs(expected, {g.OUTPUTS[0]: b'x'})

    def test_mutated_generated_output_rejected(self):
        expected = {path: b'x' for path in g.OUTPUTS}; changed = dict(expected)
        changed[g.OUTPUTS[1]] = b'y'
        with self.assertRaisesRegex(ValueError, 'output mismatch'):
            g.compare_outputs(expected, changed)


if __name__ == '__main__':
    unittest.main()
