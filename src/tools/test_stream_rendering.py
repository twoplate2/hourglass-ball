"""Check the real shared drawing formulas without opening a window."""

from pathlib import Path
import random
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.stream_rendering import (
    CONTACT_REACH_PX, DENSITY_PAD_PX, EDGE_PERIOD, contact_reach, edge_phase,
    trail_length, trail_lengths,
)


class StreamDrawingTests(unittest.TestCase):
    def test_density_does_not_wait_for_landing(self):
        for speed in (35, 60, 300, 700):
            expected = max(2.0, speed * 0.025) + DENSITY_PAD_PX
            self.assertEqual(trail_length(-speed, 0.025, 1.0), expected)

    def test_keeps_dense_body(self):
        self.assertGreaterEqual(DENSITY_PAD_PX, 0.75 * CONTACT_REACH_PX)
        self.assertGreater(trail_length(-500, 0.025, 1.0), 20.0)

    def test_contact_does_not_elongate_distant_particles(self):
        gaps = np.array([-5, 0, 0.1, 4, CONTACT_REACH_PX, CONTACT_REACH_PX + 0.1, 400])
        np.testing.assert_array_equal(
            contact_reach(np, gaps, np.zeros_like(gaps)),
            [0, 0, 0.1, 4, CONTACT_REACH_PX, 0, 0])

    def test_contact_preserves_input_arrays(self):
        ys, surfaces = np.array([400.0, 20]), np.array([0.0, 10])
        contact_reach(np, ys, surfaces)
        np.testing.assert_array_equal(ys, [400, 20])
        np.testing.assert_array_equal(surfaces, [0, 10])

    def test_tail_drains_to_one_pixel(self):
        for speed in (0, 35, 500, 4000):
            self.assertEqual(trail_length(speed, 0.025, 7.0, 0.8, 1.0), 1.0)

    def test_scalar_vector_bytes_match(self):
        rng = random.Random(297)
        speeds = np.array([rng.uniform(-4000, 0) for _ in range(1200)] + [0.0, -0.0])
        times = np.array([rng.uniform(0, 0.04) for _ in range(len(speeds))])
        for motion in (1.0, 2.09, 8.0):
            for scale in (0.2, 0.8, 1.0):
                for blend in (0.0, 0.3, 1.0):
                    scalar = np.array([trail_length(v, t, motion, scale, blend)
                                       for v, t in zip(speeds, times)], dtype="<f4")
                    vector = trail_lengths(np, speeds, times, motion, scale, blend)
                    self.assertEqual(scalar.tobytes(), vector.astype("<f4").tobytes())

    def test_fused_and_bucket_math_match(self):
        speeds = np.array([-35.0, -300, -60, -700])
        times = np.array([0.018, 0.032, 0.025, 0.022])
        full = trail_lengths(np, speeds, times, 1.0)
        for indices in ([0, 2], [1, 3]):
            bucket = trail_lengths(np, speeds[indices], times[indices], 1.0)
            np.testing.assert_array_equal(bucket, full[indices])

    def test_trail_is_behind_particle_not_in_front(self):
        y = 400.0
        top = y + trail_length(-500, 0.025, 1.0)
        self.assertGreater(top, y)  # Kivy y increases upwards.

    def test_edge_phase_does_not_jump_at_64_seconds(self):
        for motion in (1.0, 8.0):
            for half in (2.0, 23.0, 50.0):
                delta = (edge_phase(64.001, motion, half)
                         - edge_phase(63.999, motion, half)) % EDGE_PERIOD
                grain_px = max(1.0, min(4.0, half * 0.15))
                self.assertAlmostEqual(delta, 400.0 * motion * 0.002 / grain_px,
                                       places=6)

    def test_edge_phase_stays_bounded_for_long_timers(self):
        for elapsed in (0.0, 64.0, 600.0, 36000.0, 360000.0):
            for motion in (0.25, 1.0, 8.0):
                phase = edge_phase(elapsed, motion, 23.0)
                self.assertGreaterEqual(phase, 0.0)
                self.assertLess(phase, EDGE_PERIOD)


if __name__ == "__main__":
    unittest.main()
