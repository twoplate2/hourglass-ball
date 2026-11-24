"""Regressions from the 3.36 recording, in reference-glass pixels."""
import ast
from pathlib import Path
import sys
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/app"))
from b13_stream import sample_grains
from grain_style import B13_REFERENCE_RADIUS as R, hourglass_transit
from neck_geometry import SandNeck, inner_width


def glass(period):
    half = inner_width(period, 2 * R) / 2
    return half, hourglass_transit(period, half)


class RecordedFlowTests(unittest.TestCase):
    def test_leading_grains_travel_with_the_first_visible_front(self):
        for period in (1., 5., 15., 50., 6000.):
            half, flow = glass(period)
            t0, t1 = (flow.age_at(z) for z in (155., 225.))
            a = sample_grains(t0, period, half, 460.)
            b = sample_grains(t1, period, half, 460.)
            _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            behind = a.depth.max() - a.depth[i]
            band = (behind < max(7., .30*half)) & (abs(a.x[i]) < .7*half)
            with self.subTest(period=period):
                self.assertGreater(band.sum(), 8)
                movement = np.median(b.depth[j[band]] - a.depth[i[band]])
                self.assertAlmostEqual(movement / (b.depth.max() - a.depth.max()), 1., delta=.24)

    def test_stable_acceleration_remains_visible_in_the_lower_half(self):
        half, _ = glass(15.)
        a = sample_grains(3., 15., half, 433.)
        b = sample_grains(3.04, 15., half, 433.)
        _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
        speeds = (b.depth[j] - a.depth[i]) / .04
        values = [np.median(speeds[(abs(a.depth[i]-z) < 4) & (abs(a.x[i]) < .4*half)])
                  for z in (10., 73., 136., 199.)]
        increments = np.diff(values)
        self.assertGreater(increments.min(), .55 * increments.max(), values)

    def test_flat_last_layer_and_contact_follow_the_inner_wall(self):
        from check_recorded_flow import closing_surface, shoulder_gap
        mask = np.zeros((100,100), dtype=bool)
        mask[51:] = True
        mask[38:51,40:61] = True
        with self.assertRaisesRegex(ValueError, "台阶"):
            closing_surface(mask, 50, 50, 24)
        mask[38:51] = False
        mask[50, 48:58] = True
        closing_surface(mask, 50, 50, 24)
        with_slot = mask.copy()
        with_slot[52:69, 69:79] = False
        with self.assertRaisesRegex(ValueError, "空槽"):
            shoulder_gap(with_slot, mask, 50, 50, 24, 100)
        shoulder_gap(mask, mask, 50, 50, 24, 100)

    def test_resting_bulbs_are_mirrored_circles_with_an_empty_neck(self):
        from check_recorded_flow import sphere_endpoint
        y, x = np.ogrid[:240, :120]
        geometry = dict(cx=60., radius=50., crown_y=5., surface_y=235., outlet_y=120.)
        for upper, cy in ((True, 55.), (False, 185.)):
            mask = (x+.5-60.)**2 + (y+.5-cy)**2 <= 50.**2
            def image():
                return np.where(mask[:,:,None], [217,166,96], [234,243,248]).astype(np.uint8)
            self.assertLessEqual(sphere_endpoint(image(), geometry, upper), 1.)
            if upper:
                mask[97:117, 52:69] = True
            else:
                mask[123:143, 52:69] = True
            with self.assertRaisesRegex(ValueError, "偏离圆弧"):
                sphere_endpoint(image(), geometry, upper)

    def test_initial_sand_reaches_the_crown_and_discharges_from_the_first_step(self):
        from test_splash33_impulse import method
        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        names = ('_transfer_timing', '_released_fraction_at', '_upper_sand_fraction',
                 '_upper_sand_height_px', '_neck_fill_time', '_supply_elapsed')
        widget = ast.ClassDef(name='Glass', bases=[], keywords=[], decorator_list=[],
                              body=[method(source, name) for name in names])
        ns = {'FLOW_RENDERER': 'b13'}
        exec(compile(ast.fix_missing_locations(ast.Module([widget], [])), 'main.py', 'exec'), ns)
        w = ns['Glass']()
        w._geom_ready, w._R_inner, w._raw_height_ratio = True, R, lambda volume: volume
        for period in (1., 5., 15., 20., 50., 6000.):
            w.duration, w._sand_timing, w.elapsed = period, None, 0.
            half, transit = glass(period)
            shoulder = half + 30.
            join_y = 99. + R - np.sqrt(R**2-shoulder**2)
            w._sand_neck = SandNeck(R, 99., [(shoulder, join_y), (half, 89.), (half, 80.)],
                                    transit, 1.)
            self.assertAlmostEqual(w._upper_sand_height_px(), 2 * R)
            self.assertLess(w._upper_sand_fraction(1/120), w._upper_sand_fraction(0))
            w.elapsed = period
            self.assertEqual(w._upper_sand_height_px(), 0.)

    def test_first_contact_has_visible_travel_time_and_fits_the_opening(self):
        for period, earliest, latest in ((1, .145, .25), (5, .32, .60),
                                        (15, .80, 1.55), (20, 1.20, 1.60),
                                        (50, 1.20, 1.60), (6000, 1.20, 1.60)):
            with self.subTest(period=period):
                half, flow = glass(period)
                hit = flow.age_at(433.) / flow.lane_factor(0., 1.)
                self.assertGreaterEqual(hit, earliest)
                self.assertLessEqual(hit, latest)
                self.assertLessEqual(hit / period, half**2 * 433. / (4 * R**3 / 3))
                for depth in (20., 70., 180., 320.):
                    self.assertLess(flow.age_at(depth), hit)
                    self.assertAlmostEqual(flow.depth_at(flow.age_at(depth)), depth, delta=.05)

    def test_pigment_accelerates_gently_after_the_lip(self):
        for period in (1., 5., 15., 50., 6000.):
            half, _ = glass(period)
            t = min(.7 * period, 3.)
            a = sample_grains(t, period, half, 433.)
            b = sample_grains(t+.02, period, half, 433.)
            _, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            speeds = (b.depth[j] - a.depth[i]) / .02
            medians = []
            for z in (3., 100., 365.):
                band = (abs(a.depth[i]-z) < 3) & (abs(a.x[i]) < .65*half)
                self.assertGreater(np.count_nonzero(band), 5)
                medians.append(np.median(speeds[band]))
            top, middle, bottom = medians
            self.assertTrue(76. <= top <= 83., medians)
            self.assertTrue(1.08 * top < middle < bottom, medians)
            self.assertTrue(1.20 * top < bottom < 1.47 * top, medians)

    def test_dense_flow_reaches_both_edges_immediately_below_the_lip(self):
        for period in (1., 5., 15., 50.):
            half, _ = glass(period)
            positions = []
            for t in np.arange(.35, .70, .025):
                grains = sample_grains(t, period, half, 220.)
                near = (grains.depth < 3.) & (grains.depth > .2)
                positions.extend(grains.x[near] / half)
            left, right = np.percentile(positions, [5, 95])
            self.assertLess(left, -.84, (period, left))
            self.assertGreater(right, .84, (period, right))

    def test_rising_impact_carries_only_exposed_outward_grains_and_respects_glass(self):
        from types import SimpleNamespace
        from splash_motion import follow_rising_impact

        w = SimpleNamespace(_R_inner=100., _lower_y_c=100., _lower_sand_bot=0.,
                            _taper={'t_in': 30.}, _cx=0., _sn=6)
        w.sx = np.array([-24., 24., 26., 24., 1., 98.])
        w.sy = np.array([103., 103., 103., 79., 103., 103.])
        w.svx = np.array([-20., 20., -20., 20., 20., 20.])
        w.srest = np.array([0., 0., 1., 0., 0., 0.])
        w.sgd = np.ones(6)
        w.shas = np.zeros(6)
        w.shw = w.shh = np.ones(6)
        old_x, old_y = w.sx.copy(), w.sy.copy()
        table = np.full(129, 105.)
        follow_rising_impact(w, .01, 1/60, table, .64)
        np.testing.assert_array_equal(w.sx, old_x)
        follow_rising_impact(w, 4., 1/60, table, .64)
        self.assertLess(w.sx[0], old_x[0])
        self.assertGreater(w.sx[1], old_x[1])
        self.assertTrue(np.all(w.sy[:2] > old_y[:2]))
        np.testing.assert_array_equal(w.sy[2:5], old_y[2:5])
        np.testing.assert_array_equal(w.sx[2:5], old_x[2:5])
        self.assertLess(np.hypot(w.sx[-1], w.sy[-1]-100), 100.)

    def test_rebinding_a_new_grain_material_also_changes_the_crest_and_surface_colours(self):
        from types import SimpleNamespace
        from unittest.mock import Mock

        source = (ROOT / 'main.py').read_text(encoding='utf-8')
        widget = next(node for node in ast.parse(source).body if getattr(node, 'name', '') == 'HourglassWidget')
        methods = [node for node in widget.body if getattr(node, 'name', '') in
                   ('rebind_sand_material', '_bind_surface_colours')]
        ns = {}
        exec(compile(ast.Module(methods, []), 'main.py', 'exec'), ns)
        w = SimpleNamespace(_sand_chords=[], _neck_quads=Mock(), _contact_grains=Mock(),
                            _neck_solid_rect=Mock(), _neck_fade_rect=Mock(), _neck_color=Mock(),
                            _neck_solid_color=Mock(), _neck_fade_color=Mock(), _render_colors=None,
                            _stream_pools={}, _upper_band_color=Mock(), _mound_band_color=Mock(),
                            _splash_color=Mock(), _dust_color=Mock(), redraw=Mock(),
                            sand_base=(.2,.35,.6), sand_dark=(.1,.2,.3), sand_light=(.4,.5,.7))
        w._bind_surface_colours = ns['_bind_surface_colours'].__get__(w)
        material = Mock()
        ns['rebind_sand_material'](w, material)
        w._contact_grains.set_texture.assert_called_once_with(material.texture)
        self.assertEqual(w._upper_band_color.rgb, w.sand_light)
        self.assertEqual(w._mound_band_color.rgb, w.sand_light)
        self.assertEqual(w._splash_color.rgb, w.sand_light)
        self.assertEqual(w._dust_color.rgb, w.sand_light)

    def test_picture_measurements_detect_a_missing_cap_and_follow_actual_motion(self):
        from check_recorded_flow import crown_gap, motion_speed, splash_pixels

        rng = np.random.default_rng(336)
        rgb = np.full((50, 50, 3), (226, 239, 247), dtype=np.uint8)
        rgb[15:] = (215, 172, 107)
        self.assertEqual(crown_gap(rgb, dict(cx=25, crown_y=4)), 11)
        rgb[4:15] = (215, 172, 107)
        self.assertEqual(crown_gap(rgb, dict(cx=25, crown_y=4)), 0)
        a = rng.integers(90, 170, (120,90), dtype=np.uint8)
        for distance in (3, 5):
            speed = motion_speed(a, np.roll(a, distance, axis=0), .1, 45, 30, 10, 100)
            self.assertAlmostEqual(speed, distance/.1, delta=.8)
        flat = np.ones_like(a)
        self.assertIsNone(motion_speed(flat, flat, .1, 45, 30, 10, 100))
        mask = np.zeros((100,100), dtype=bool)
        mask[80:] = True
        self.assertEqual(splash_pixels(mask, 10, 50, 10, 40), 0)
        mask[75, 30] = True
        self.assertEqual(splash_pixels(mask, 10, 50, 10, 40), 1)


if __name__ == "__main__":
    unittest.main()
