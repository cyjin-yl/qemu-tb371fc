#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure XML protocol regression tests; no QEMU, device, or firmware execution."""
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

SOURCE = Path(__file__).with_name("test_aop_entry_reference.py")
SPEC = importlib.util.spec_from_file_location("aop_entry_reference", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

# Source-derived envelope: QEMU gdbstub/gdbstub.c:get_feature_xml().
# These fixtures are authored protocol data, not captured guest register values.
PREFIX = (b'<?xml version="1.0"?>'
          b'<!DOCTYPE target SYSTEM "gdb-target.dtd"><target>')
TARGET = (PREFIX + b'<architecture>arm</architecture>'
          b'<xi:include href="core.xml"/><xi:include href="system.xml"/></target>')
CORE = (b'<feature name="reference.core">'
        b'<reg name="r0" bitsize="32" regnum="0"/>'
        b'<reg name="pc" bitsize="32" regnum="15"/>'
        b'<reg name="xpsr" bitsize="32" regnum="25"/></feature>')
SYSTEM = (b'<feature name="reference.system">'
          b'<reg name="msp" bitsize="32"/>'
          b'<reg name="psp" bitsize="32"/></feature>')


class XMLProtocolTests(unittest.TestCase):
    def test_original_elementtree_failure_reproduces(self):
        with self.assertRaisesRegex(ET.ParseError, "unbound prefix"):
            ET.fromstring(TARGET)

    def test_exact_qemu_envelope_preserves_include_order(self):
        root = MODULE.parse_gdb_feature_xml("target.xml", TARGET)
        includes = list(root.findall("{http://www.w3.org/2001/XInclude}include"))
        self.assertEqual([x.attrib["href"] for x in includes],
                         ["core.xml", "system.xml"])
        self.assertEqual(root.find("architecture").text, "arm")

    def test_already_bound_namespace_is_supported(self):
        raw = (b'<target xmlns:xi="http://www.w3.org/2001/XInclude">'
               b'<xi:include href="core.xml"/></target>')
        root = MODULE.parse_gdb_feature_xml("target.xml", raw)
        self.assertEqual(root[0].tag, "{http://www.w3.org/2001/XInclude}include")
        self.assertEqual(root[0].attrib, {"href": "core.xml"})

    def test_feature_attributes_are_unchanged(self):
        root = MODULE.parse_gdb_feature_xml("core.xml", CORE)
        self.assertEqual(root.attrib, {"name": "reference.core"})
        self.assertEqual(root[-1].attrib,
                         {"name": "xpsr", "bitsize": "32", "regnum": "25"})

    def test_other_annex_is_not_silently_normalized(self):
        with self.assertRaisesRegex(ET.ParseError, "unbound prefix"):
            MODULE.parse_gdb_feature_xml("feature.xml", TARGET)

    def test_unknown_unbound_prefix_is_rejected(self):
        raw = PREFIX + b'<unknown:include href="core.xml"/></target>'
        with self.assertRaisesRegex(ET.ParseError, "unbound prefix"):
            MODULE.parse_gdb_feature_xml("target.xml", raw)

    def test_unrecognized_unbound_target_envelope_is_rejected(self):
        raw = b'<target version="1.0"><xi:include href="core.xml"/></target>'
        with self.assertRaisesRegex(ET.ParseError, "unbound prefix"):
            MODULE.parse_gdb_feature_xml("target.xml", raw)

    def test_malformed_xml_remains_rejected(self):
        raw = PREFIX + b'<xi:include href="core.xml"></target>'
        with self.assertRaises(ET.ParseError):
            MODULE.parse_gdb_feature_xml("target.xml", raw)

    def test_duplicate_attributes_remain_rejected(self):
        raw = PREFIX + b'<xi:include href="core.xml" href="other.xml"/></target>'
        with self.assertRaisesRegex(ET.ParseError, "duplicate attribute"):
            MODULE.parse_gdb_feature_xml("target.xml", raw)

    def test_include_walk_keeps_explicit_and_implicit_register_numbers(self):
        documents = {"target.xml": TARGET, "core.xml": CORE, "system.xml": SYSTEM}
        rsp = MODULE.RSP.__new__(MODULE.RSP)
        rsp.registers = {}
        rsp._next_register = 0
        rsp.xml = lambda name: MODULE.parse_gdb_feature_xml(name, documents[name])
        rsp._read_feature("target.xml", set())
        self.assertEqual(rsp.registers,
                         {"r0": (0, 32), "pc": (15, 32), "xpsr": (25, 32),
                          "msp": (26, 32), "psp": (27, 32)})

    def test_recursive_annex_is_still_rejected(self):
        raw = PREFIX + b'<xi:include href="target.xml"/></target>'
        rsp = MODULE.RSP.__new__(MODULE.RSP)
        rsp.registers = {}
        rsp._next_register = 0
        rsp.xml = lambda name: MODULE.parse_gdb_feature_xml(name, raw)
        with self.assertRaisesRegex(AssertionError, "Recursive GDB target XML"):
            rsp._read_feature("target.xml", set())


if __name__ == "__main__":
    unittest.main()
