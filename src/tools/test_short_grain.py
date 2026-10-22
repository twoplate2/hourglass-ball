"""Rendering contract: finite short grains, actual front/tail, no phantom sand."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from short_grain_stream import grain_pitch, flow_phase, stream_limits


class ShortGrainTests(unittest.TestCase):
    def test_empty_has_no_stream(self):
        self.assertIsNone(stream_limits([], 100, 0, 2, False))

    def test_front_does_not_instantly_reach_mound(self):
        top, bottom = stream_limits([96, 90], 100, 0, 2, False)
        self.assertLessEqual(top, 100)
        self.assertGreaterEqual(bottom, 88)

    def test_contact_ends_inside_actual_surface(self):
        top, bottom = stream_limits([96, 32], 100, 30, 2, True)
        self.assertLess(bottom, 30)
        self.assertGreater(bottom, 27)

    def test_tail_does_not_reconnect_to_empty_neck(self):
        top, bottom = stream_limits([43, 40], 100, 0, 2, False)
        self.assertLess(top, 50)
        self.assertLess(bottom, top)

    def test_grain_scales_with_canvas(self):
        self.assertAlmostEqual(grain_pitch(400, 1.8) / grain_pitch(150, 1.8), 400 / 150)

    def test_settings_remain_distinct(self):
        sizes = [grain_pitch(180, c) for c in (1, 1.4, 1.8, 2.2, 2.6, 3)]
        self.assertTrue(all(a < b for a, b in zip(sizes, sizes[1:])))

    def test_phase_is_bounded_and_pause_is_exact(self):
        for t in (0, 1, 25, 360000):
            self.assertTrue(0 <= flow_phase(t) < 256)
            self.assertEqual(flow_phase(t), flow_phase(t))

    def test_phase_wrap_matches_hash_period(self):
        self.assertAlmostEqual(flow_phase(1), flow_phase(1 + 256 / 12), places=9)


if __name__ == "__main__":
    unittest.main()
