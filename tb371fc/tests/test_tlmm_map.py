#!/usr/bin/env python3
"""Bounds, uniqueness and provenance-parser regression checks for TLMM mapping."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location(
    "generate_tlmm_map", Path(__file__).resolve().parents[1] / "scripts/generate_tlmm_map.py")
MAP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MAP)

def fixture():
    return "\n".join("[%d] = PINGROUP(%d, %s, foo)," %
                     (i, i, ("WEST", "SOUTH", "NORTH")[i % 3])
                     for i in range(180))

class TLMMMapTests(unittest.TestCase):
    def test_complete_and_sorted(self):
        groups = MAP.parse_groups(fixture())
        self.assertEqual([g["pin"] for g in groups], list(range(180)))
        self.assertEqual(groups[0]["offset"], 0x100000)
        self.assertEqual(groups[1]["offset"], 0x501000)
        self.assertEqual(groups[179]["offset"], 0x9b3000)

    def test_input_order_independent(self):
        self.assertEqual(MAP.parse_groups(fixture()),
                         MAP.parse_groups("\n".join(reversed(fixture().splitlines()))))

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            MAP.parse_groups(fixture() + "\n[0] = PINGROUP(0, WEST, foo),")

    def test_missing_rejected(self):
        with self.assertRaises(ValueError):
            MAP.parse_groups("\n".join(fixture().splitlines()[1:]))

    def test_index_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            MAP.parse_groups(fixture().replace("[0] = PINGROUP(0,", "[0] = PINGROUP(1,"))

    def test_no_ordinary_pin180(self):
        with self.assertRaises(ValueError):
            MAP.parse_groups(fixture() + "\n[180] = PINGROUP(180, SOUTH, foo),")

    def test_unknown_tile_rejected_as_missing(self):
        with self.assertRaises(ValueError):
            MAP.parse_groups(fixture().replace("[0] = PINGROUP(0, WEST", "[0] = PINGROUP(0, EAST"))

    def test_dedicated_group_not_counted(self):
        text = fixture() + "\n[180] = UFS_RESET(ufs_reset, 0xb8000),"
        self.assertEqual(len(MAP.parse_groups(text)), 180)

    def test_render_has_provenance_and_180_offsets(self):
        rendered = MAP.render(MAP.parse_groups(fixture()))
        self.assertIn(MAP.PIN_SOURCE_SHA256, rendered)
        self.assertEqual(len(MAP.re.findall(r"0x[0-9a-f]{6},", rendered)), 180)

if __name__ == "__main__":
    unittest.main()
