"""Rendering contract: finite short grains, actual front/tail, no phantom sand."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from short_grain_stream import grain_pitch, flow_phase, stream_limits, motion_parameters


class ShortGrainTests(unittest.TestCase):
    def test_speed_is_not_controlled_by_grain_size(self):
        fine = motion_parameters(12, 1, 400, 1)
        coarse = motion_parameters(12, 3, 400, 1)
        self.assertEqual(fine[:2], coarse[:2])

    def test_speed_is_screen_relative_and_accelerates(self):
        v, g, rate = motion_parameters(12, 2, 400, 1)
        big = motion_parameters(24, 4, 800, 1)
        self.assertGreaterEqual(v, 3 * 24)
        self.assertGreater(g, 0)
        self.assertAlmostEqual(big[0], 2*v)
        self.assertAlmostEqual(big[1], 2*g)
        self.assertAlmostEqual(big[2], rate)

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
            self.assertTrue(0 <= flow_phase(t) < 4096)
            self.assertEqual(flow_phase(t), flow_phase(t))

    def test_phase_wrap_matches_hash_period(self):
        self.assertAlmostEqual(flow_phase(1), flow_phase(1 + 4096 / 12), places=9)
        for rate in (70.3, 121.7, 250.2):
            boundary = 4096 / rate
            before = flow_phase(boundary - .001, rate)
            after = flow_phase(boundary + .001, rate)
            self.assertAlmostEqual((after-before) % 4096, .002*rate, places=8)


class B13StreamTests(unittest.TestCase):
    def test_same_grains_fall_across_a_birth_block_and_a_long_timer(self):
        import numpy as np
        from b13_stream import REFERENCE_HALF, sample_grains
        for half, time in ((REFERENCE_HALF, 5.), (5., 360000.)):
            a = sample_grains(time - .001, time + 5., half, 400.)
            b = sample_grains(time + .001, time + 5., half, 400.)
            ids, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            self.assertGreater(len(ids), .97 * len(a.serial))
            self.assertTrue(np.all(b.depth[j] > a.depth[i]))
            self.assertTrue(np.array_equal(a.radius[i], b.radius[j]))
            self.assertTrue(np.array_equal(a.tone[i], b.tone[j]))
            paused = sample_grains(time + .001, time + 5., half, 400.)
            self.assertTrue(np.array_equal(paused.x, b.x))

    def test_b13_front_and_tail_reach_the_reference_heights(self):
        import numpy as np
        from b13_stream import REFERENCE_HALF, sample_grains
        front = sample_grains(1.33, 5., REFERENCE_HALF, 433.)
        self.assertTrue(320. < front.depth.max() < 334.)
        tail = sample_grains(5.1, 5., REFERENCE_HALF, 15.)
        self.assertGreater(tail.depth.min(), 3.)
        mid = tail.x[np.abs(tail.depth - 7.) < 1.]
        self.assertTrue(0 < len(mid) and np.ptp(mid) < 16.)
        settled = sample_grains(5.4, 5., REFERENCE_HALF, 15.)
        self.assertEqual(len(settled.serial), 0)

    def test_duration_scales_supply_with_aperture(self):
        from b13_stream import REFERENCE_HALF, sample_grains
        wide = sample_grains(3., 50., REFERENCE_HALF, 300.)
        narrow = sample_grains(3., 6000., REFERENCE_HALF / 5., 300.)
        self.assertAlmostEqual(len(narrow.serial) / len(wide.serial), .2, delta=.012)


if __name__ == "__main__":
    unittest.main()
