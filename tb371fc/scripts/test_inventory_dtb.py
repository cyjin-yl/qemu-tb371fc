#!/usr/bin/env python3
"""Safety and cell-decoding tests for publication of private board DT input."""
import base64
import hashlib
import json
import struct
import unittest

import inventory_dtb as inv


def cells(*items):
    return struct.pack('>' + 'I' * len(items), *items)


def strings(*items):
    return ('\0'.join(items) + '\0').encode('ascii')


def fdt(tree, nop=False):
    """Construct independent FDT bytes, including chosen secrets in tests."""
    names = sorted({key for props in tree.values() for key in props})
    table, offsets = bytearray(), {}
    for name in names:
        offsets[name] = len(table)
        table.extend(name.encode('ascii') + b'\0')
    structure = bytearray()

    def add(raw):
        structure.extend(raw)
        structure.extend(b'\0' * (-len(structure) % 4))

    def node(path):
        add(cells(1) + ('' if path == '/' else path.rsplit('/', 1)[1]).encode() + b'\0')
        if nop:
            add(cells(4))
        for name, raw in tree[path].items():
            add(cells(3, len(raw), offsets[name]) + raw)
        for child in tree:
            if child != '/' and inv.parent(child) == path:
                node(child)
        add(cells(2))

    node('/')
    add(cells(9))
    offset = 56
    header = cells(0xd00dfeed, offset + len(structure) + len(table), offset,
                   offset + len(structure), 40, 17, 16, 0, len(table), len(structure))
    return header + b'\0' * 16 + structure + table


class InventoryTests(unittest.TestCase):
    def test_private_values_never_leave_the_normalizer(self):
        secret = b'private-serial-seed-raw-bootargs\0'
        tree = {'/': {'compatible': strings('lenovo,tb371fc'), 'serial-number': secret},
                '/chosen': {'bootargs': secret, 'status': secret, 'compatible': secret},
                '/aliases': {'console': secret}, '/__symbols__': {'wifi': secret},
                '/device': {'unknown-firmware-payload': secret}}
        text = json.dumps(inv.build_inventory(fdt(tree)))
        for value in (secret[:-1].decode(), base64.b64encode(secret).decode(),
                      hashlib.sha256(secret).hexdigest()):
            self.assertNotIn(value, text)
        self.assertIn('bootargs', text)  # The omission itself stays reviewable.

    def test_nop_tokens_are_legal(self):
        tree = {'/': {'compatible': strings('test')}, '/child': {'reg': cells(4)}}
        self.assertEqual(inv.parse_dtb(fdt(tree, nop=True)), tree)

    def test_truncated_dtb_fails(self):
        with self.assertRaises(inv.InvalidDTB):
            inv.parse_dtb(fdt({'/': {}})[:-1])

    def test_corrupt_property_offset_fails(self):
        raw = bytearray(fdt({'/': {'compatible': strings('test')}}))
        struct.pack_into('>I', raw, 56 + 8 + 8, 0xffffffff)
        with self.assertRaises(inv.InvalidDTB):
            inv.parse_dtb(bytes(raw))

    def test_64bit_reg_and_parent_ranges_translation(self):
        nodes = {'/': {'#address-cells': cells(2), '#size-cells': cells(2)},
                 '/soc': {'#address-cells': cells(1), '#size-cells': cells(1),
                          'ranges': cells(0, 1, 0, 0x10000)},
                 '/soc/uart': {'reg': cells(0x900, 0x100)}}
        decoded = inv.decode_reg(nodes, '/soc/uart')[0]
        self.assertEqual(decoded['system_address'], 0x100000900)
        self.assertEqual(decoded['size'], 0x100)

    def test_reg_without_ranges_is_not_called_physical(self):
        nodes = {'/': {}, '/bus': {'#address-cells': cells(1), '#size-cells': cells(1)},
                 '/bus/device': {'reg': cells(0x40, 4)}}
        self.assertNotIn('system_address', inv.decode_reg(nodes, '/bus/device')[0])

    def test_empty_ranges_is_identity(self):
        nodes = {'/': {}, '/bus': {'#address-cells': cells(1), '#size-cells': cells(1),
                                  'ranges': b''}, '/bus/device': {'reg': cells(0x40, 4)}}
        self.assertEqual(inv.decode_reg(nodes, '/bus/device')[0]['system_address'], 0x40)

    def test_cpu_reg_uses_two_cells_and_no_size(self):
        nodes = {'/': {}, '/cpus': {'#address-cells': cells(2), '#size-cells': cells(0)},
                 '/cpus/cpu@700': {'reg': cells(0, 0x700)}}
        value = inv.decode_reg(nodes, '/cpus/cpu@700')[0]
        self.assertEqual(value['bus_address'], 0x700)
        self.assertNotIn('size', value)

    def test_variable_width_clock_providers(self):
        nodes = {'/': {}, '/xo': {'#clock-cells': cells(0)},
                 '/gcc': {'#clock-cells': cells(1)}, '/dev': {'clocks': cells(1, 2, 357)}}
        edges, errors = inv.resolve_dependencies(nodes, {1: '/xo', 2: '/gcc'}, '/dev')
        self.assertFalse(errors)
        self.assertEqual([e['arguments'] for e in edges], [[], [357]])

    def test_truncated_tuple_is_not_silently_accepted(self):
        nodes = {'/': {}, '/gcc': {'#clock-cells': cells(1)}, '/dev': {'clocks': cells(2)}}
        edges, errors = inv.resolve_dependencies(nodes, {2: '/gcc'}, '/dev')
        self.assertEqual(edges, [])
        self.assertIn('truncated', errors[0]['error'])

    def test_unknown_provider_is_explicit(self):
        edges, errors = inv.resolve_dependencies({'/': {}, '/dev': {'clocks': cells(999)}}, {}, '/dev')
        self.assertEqual(edges, [])
        self.assertIn('unknown provider', errors[0]['error'])

    def test_mixed_interrupts_extended(self):
        nodes = {'/': {}, '/gic': {'#interrupt-cells': cells(3)},
                 '/gpio': {'#interrupt-cells': cells(2)},
                 '/uart': {'interrupts-extended': cells(1, 0, 357, 4, 2, 20, 2)}}
        edges, errors = inv.resolve_dependencies(nodes, {1: '/gic', 2: '/gpio'}, '/uart')
        self.assertFalse(errors)
        self.assertEqual([e['arguments'] for e in edges], [[0, 357, 4], [20, 2]])

    def test_spmi_uses_closest_four_cell_domain(self):
        nodes = {'/': {'interrupt-parent': cells(1)}, '/gic': {'#interrupt-cells': cells(3)},
                 '/spmi': {'#interrupt-cells': cells(4), 'phandle': cells(2)},
                 '/spmi/pmic': {}, '/spmi/pmic/adc': {'interrupts': cells(0, 0x31, 0, 1)}}
        edges, errors = inv.resolve_dependencies(nodes, {1: '/gic', 2: '/spmi'}, '/spmi/pmic/adc')
        self.assertFalse(errors)
        self.assertEqual(edges[-1]['provider'], '/spmi')
        self.assertEqual(edges[-1]['arguments'], [0, 0x31, 0, 1])

    def test_no_irq_dependency_invented_for_non_irq_node(self):
        nodes = {'/': {'interrupt-parent': cells(1)}, '/dev': {}}
        self.assertEqual(inv.resolve_dependencies(nodes, {}, '/dev'), ([], []))

    def test_cyclic_interrupt_chain_is_reported(self):
        nodes = {'/': {}, '/a': {'interrupt-parent': cells(2), 'interrupts': cells(4)},
                 '/b': {'interrupt-parent': cells(1)}}
        _, errors = inv.resolve_dependencies(nodes, {1: '/a', 2: '/b'}, '/a')
        self.assertIn('cyclic', errors[0]['error'])

    def test_gpio_count_is_not_a_reference(self):
        nodes = {'/': {}, '/pinctrl': {'qcom,num-gpios': cells(8)}}
        self.assertEqual(inv.resolve_dependencies(nodes, {}, '/pinctrl'), ([], []))

    def test_vendor_singular_gpio_uses_provider_width(self):
        nodes = {'/': {}, '/tlmm': {'#gpio-cells': cells(2)},
                 '/touch': {'novatek,irq-gpio': cells(101, 39, 0)}}
        edges, errors = inv.resolve_dependencies(nodes, {101: '/tlmm'}, '/touch')
        self.assertFalse(errors)
        self.assertEqual(edges[0]['arguments'], [39, 0])
        self.assertEqual(inv.property_kind('/touch', 'novatek,irq-gpio'), 'cells')

    def test_display_references_preserve_both_controllers(self):
        nodes = {'/': {}, '/dsi0': {}, '/dsi1': {},
                 '/display': {'qcom,dsi-ctrl': cells(10, 11)}}
        edges, errors = inv.resolve_dependencies(nodes, {10: '/dsi0', 11: '/dsi1'}, '/display')
        self.assertFalse(errors)
        self.assertEqual([item['provider'] for item in edges], ['/dsi0', '/dsi1'])

    def test_disabled_ancestor_applies_to_child(self):
        nodes = {'/': {}, '/usb': {'status': strings('disabled')},
                 '/usb/dwc3': {'status': strings('okay')}}
        state = inv.effective_status(nodes, '/usb/dwc3')
        self.assertFalse(state['dt_available'])
        self.assertEqual(state['restrictions'], [{'node': '/usb', 'status': 'disabled'}])

    def test_conflicting_phandles_fail(self):
        tree = {'/': {}, '/a': {'phandle': cells(1)}, '/b': {'phandle': cells(1)}}
        with self.assertRaises(inv.InvalidDTB):
            inv.build_inventory(fdt(tree))

    def test_malformed_allowlisted_value_is_rejected(self):
        result = inv.build_inventory(fdt({'/': {}, '/uart': {'clocks': b'\x01'}}))
        self.assertIn('clocks', result['nodes']['/uart']['rejected_properties'])
        self.assertNotIn('clocks', result['nodes']['/uart']['properties'])


if __name__ == '__main__':
    unittest.main()
