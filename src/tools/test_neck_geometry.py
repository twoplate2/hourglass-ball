"""Independent size/period contract; never creates a window."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import neck_geometry as geometry


class NeckGeometryTests(unittest.TestCase):
    def test_short_periods_have_visibly_distinct_apertures(self):
        widths = [geometry.inner_width(period, 400) for period in (1, 2, 3, 4, 5)]
        self.assertAlmostEqual(widths[0], 156.)
        self.assertAlmostEqual(widths[-1], 112.)
        self.assertTrue(all(a > b for a, b in zip(widths, widths[1:])))

    def test_long_period_halves_previous_minimum(self):
        for period in (36000,360000):
            self.assertAlmostEqual(geometry.inner_width(period,400),6)

    def test_period_mapping_is_monotonic_and_bounded(self):
        values=[geometry.inner_width(math.exp(i/20),300) for i in range(260)]
        self.assertTrue(all(4.5<=v<=117. for v in values))
        self.assertTrue(all(b<=a for a,b in zip(values,values[1:])))

    def test_period_mapping_is_nonlinear(self):
        midpoint=(5+36000)/2
        linear_width=(60+6)/2
        self.assertLess(geometry.inner_width(midpoint,400),linear_width/2)

    def test_reference_middle(self):
        self.assertAlmostEqual(geometry.inner_width(50,400)/400,.24 * math.sqrt(20. / 50.))

    def test_scales_with_body_not_window_width(self):
        for period in (1,5,50,36000,360000):
            for diameter in (.5,20,200):
                self.assertAlmostEqual(geometry.inner_width(period,diameter),
                                       geometry.inner_width(period,400)*diameter/400)

    def test_first_exit_lift_matches_the_packed_outline_at_the_lip(self):
        from grain_style import B13_REFERENCE_RADIUS, hourglass_transit
        flow = hourglass_transit(50., 30.)
        for scale, gap in ((1., 0.), (.75, .5), (1., 30.), (2., 60.)):
            radius = B13_REFERENCE_RADIUS * scale
            neck = geometry.SandNeck(radius, gap, [(40.*scale, gap+8.*scale), (30.*scale, 0.)],
                                     flow, scale)
            travel = neck.first_exit_lift
            self.assertAlmostEqual(travel, neck._lift_at(travel), places=6)
            if gap:
                crossing = flow.age_at(travel/scale) / neck.lane
                self.assertEqual(neck.opening(crossing - .001)[1][-1][0], 0.)
                self.assertGreater(neck.opening(crossing + .001)[1][-1][0], 0.)

    def test_outer_half_includes_wall_once(self):
        self.assertAlmostEqual(geometry.outer_half(5,400,6),62)
        narrow, wide = geometry.half_limits(400,6)
        self.assertAlmostEqual(narrow, 9.)
        self.assertAlmostEqual(wide, 84.)

    def test_invalid_geometry_rejected(self):
        for value in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):geometry.inner_width(5,value)


if __name__=='__main__':unittest.main()
