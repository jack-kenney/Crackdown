"""Check audit leads against PPC address and timing patterns without game assets."""
import importlib.util
from pathlib import Path
import struct
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('fixed_timing_audit', ROOT / 'tools/audit-fixed-timing.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def scan(instructions, value=.0333333):
    image = bytearray(256)
    struct.pack_into('>f', image, 64, value)
    code = []
    for line, text in enumerate(instructions, 1):
        op, _, args = text.partition(' ')
        code.append((line, op, args, text))
    return audit.scan_function('sub_82130000', code, Path('fixture.cpp'), image,
                               0x82000000, [(0x82000000, 0x82000100, '.rdata')])[0]


class AuditTests(unittest.TestCase):
    def test_rounded_animation_step_and_field_accumulation(self):
        hits = scan(['lis r11,-32256', 'addi r26,r11,64', 'lfs f13,228(r31)',
                     'lfs f0,0(r26)', 'fadds f0,f13,f0', 'stfs f0,228(r31)'])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]['label'], '1/30 second')
        self.assertTrue(hits[0]['self_accumulator'])

    def test_single_definition_nonvolatile_base_survives_call_and_join(self):
        hits = scan(['lis r11,-32256', 'addi r26,r11,64', 'bl 0x82131000',
                     'label loc_82130010:', 'lfs f22,0(r26)'], value=.4)
        self.assertEqual(hits[0]['label'], '40% gain')

    def test_unknown_branch_base_and_volatile_call_base_not_assumed(self):
        self.assertEqual(scan(['lis r11,-32256', 'addi r26,r11,64',
                               'label loc_8213000C:', 'mr r26,r3', 'lfs f0,0(r26)']), [])
        self.assertEqual(scan(['lis r11,-32256', 'bl 0x82131000', 'lfs f0,64(r11)']), [])

    def test_non_timing_values_and_unknown_pointers_not_classified(self):
        self.assertEqual(scan(['lis r11,-32256', 'lfs f0,64(r11)'], value=.031), [])
        # Unknown loads also cannot be mistaken for address construction.
        self.assertEqual(scan(['lis r11,-32256', 'lwz r26,64(r11)', 'lfs f0,0(r26)']), [])

    def test_counter_pattern_kept_separate_from_timestep_evidence(self):
        hits = scan(['lwz r11,176(r31)', 'addi r11,r11,-1', 'stw r11,176(r31)'])
        self.assertEqual(hits[0]['kind'], 'per_call_counter')
        self.assertEqual(hits[0]['change'], -1)


if __name__ == '__main__':
    unittest.main()
