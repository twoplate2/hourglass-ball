"""Focused checks for the isolated previews; never starts the main app."""

import unittest

import numpy as np

from preview_common import Column, STYLES, scene, mound_surface
from sand_motion import SandMotion, CX, OUTLET, GRAVITY, LOOP_SECONDS


class PreviewChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.columns = {name: Column(name) for name in STYLES}

    def test_irregular_supply_keeps_a_continuous_base_flow(self):
        for name, column in self.columns.items():
            with self.subTest(candidate=name):
                motion = column.motion
                gaps = np.diff(np.r_[motion.packet_time, motion.packet_time[0] + LOOP_SECONDS])
                self.assertGreater(gaps.std(), 0.035)
                self.assertGreater(np.ptp(motion.packet_duration), 0.04)
                self.assertEqual(len(motion.birth), column.style.rate * LOOP_SECONDS)
                count, _ = np.histogram(motion.birth, bins=200, range=(0, LOOP_SECONDS))
                self.assertGreater(count.min(), len(motion.birth) / 200 * 0.75)

    def test_grains_keep_their_source_identity_and_speed_difference(self):
        for name, column in self.columns.items():
            with self.subTest(candidate=name):
                motion = column.motion
                self.assertGreater(np.count_nonzero(motion.grouped), len(motion.birth) * 0.15)
                indices = np.flatnonzero(motion.grouped)[::100]
                a, b = np.full(len(indices), 0.25), np.full(len(indices), 0.85)
                xa, ya = motion.positions(a, indices)
                xb, yb = motion.positions(b, indices)
                np.testing.assert_allclose(
                    yb - ya, motion.vy[indices] * (b - a) + 0.5 * GRAVITY * (b*b - a*a))
                self.assertGreater(np.mean(np.abs(xb - xa)), 0.1)
                self.assertLess(np.max(np.abs(xb - CX - motion.x0[indices])), 4.01)
                emitted_ids, _, _ = motion.airborne(motion.birth[indices[0]] + 0.25)
                self.assertIn(indices[0], emitted_ids)
                later_ids, _, _ = motion.airborne(motion.birth[indices[0]] + 0.85)
                self.assertIn(indices[0], later_ids)

    def test_collision_and_landing_effects_use_actual_parent_grains(self):
        for name, column in self.columns.items():
            with self.subTest(candidate=name):
                motion = column.motion
                np.testing.assert_allclose(
                    motion.hit_y, mound_surface(motion.hit_x, 0.0), atol=0.001)
                np.testing.assert_allclose(
                    motion.hit_time, (motion.birth + motion.hit_age) % LOOP_SECONDS)
                for parents in (motion.splash_parent, motion.roll_parent):
                    self.assertGreater(len(parents), 1000)
                    self.assertTrue(np.all((parents >= 0) & (parents < len(motion.birth))))
                    x, y = motion.positions(motion.hit_age[parents], parents)
                    np.testing.assert_allclose(x, motion.hit_x[parents])
                    np.testing.assert_allclose(y, motion.hit_y[parents])
                self.assertTrue(np.all(motion.splash_vy < 0))

    def test_changing_upstream_changes_the_downstream_impacts(self):
        old = self.columns["fine_grains"].motion
        new = SandMotion(STYLES["fine_grains"].rate, STYLES["fine_grains"].radius,
                         mound_surface, source_seed=30032)
        self.assertGreater(np.mean(np.abs(new.hit_x - old.hit_x)), 1.0)
        self.assertGreater(np.mean(np.abs(new.hit_time - old.hit_time)), 0.01)

    def test_visual_contact_neck_density_and_loop(self):
        for name, column in self.columns.items():
            with self.subTest(candidate=name):
                np.testing.assert_array_equal(column.frame(0), column.frame(LOOP_SECONDS))
                self.assertFalse(hasattr(column, "_envelope"))
                for t in (0.0, 2.0, 4.0, 6.0, 8.0):
                    base = scene(column.texture, t)
                    column._reservoir_motion(base, t)
                    image = base.copy()
                    column._particles(image, t)
                    column._outlet_blend(image, base)
                    np.testing.assert_array_equal(image[226:232], base[226:232])
                    sand = ((image[..., 0] > 150) & (image[..., 1] > 125)
                            & (image[..., 2] < 180))
                    widths = [np.ptp(np.flatnonzero(row)) + 1
                              for row in sand[232:250, 280:361]]
                    self.assertGreater(np.mean(widths), 41.0)
                    self.assertLess(np.mean(widths), 47.0)
                    self.assertGreater(sand[600:740, 304:337].mean(), 0.75)
                    xs = np.arange(280, 361)
                    ys = np.rint(mound_surface(xs, t)).astype(int)
                    touching = [np.any(image[y-3:y+2, x] != base[y-3:y+2, x])
                                for x, y in zip(xs, ys)]
                    self.assertGreaterEqual(sum(touching), 36)
                zero = np.asarray(column.frame(0))[240:770]
                before = np.asarray(column.frame(LOOP_SECONDS - 1/30))[240:770]
                after = np.asarray(column.frame(1/30))[240:770]
                join = np.any(zero != before, axis=2).sum()
                ordinary = np.any(zero != after, axis=2).sum()
                self.assertLess(join, ordinary * 1.35)


if __name__ == "__main__":
    unittest.main(verbosity=2)
