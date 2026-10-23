"""Independent size/period contract; never creates a window."""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import neck_geometry as geometry


class NeckGeometryTests(unittest.TestCase):
    def test_short_periods_share_fifteen_percent(self):
        for period in (1,2,5):
            self.assertAlmostEqual(geometry.inner_width(period,400),60)

    def test_long_period_halves_previous_minimum(self):
        for period in (36000,360000):
            self.assertAlmostEqual(geometry.inner_width(period,400),6)

    def test_period_mapping_is_monotonic_and_bounded(self):
        values=[geometry.inner_width(math.exp(i/20),300) for i in range(260)]
        self.assertTrue(all(4.5<=v<=45 for v in values))
        self.assertTrue(all(b<=a for a,b in zip(values,values[1:])))

    def test_period_mapping_is_nonlinear(self):
        midpoint=(5+36000)/2
        linear_width=(60+6)/2
        self.assertLess(geometry.inner_width(midpoint,400),linear_width/2)

    def test_reference_middle(self):
        self.assertAlmostEqual(geometry.inner_width(50,400)/400,.08257427054279469)

    def test_scales_with_body_not_window_width(self):
        for period in (1,5,50,36000,360000):
            for diameter in (.5,20,200):
                self.assertAlmostEqual(geometry.inner_width(period,diameter),
                                       geometry.inner_width(period,400)*diameter/400)

    def test_outer_half_includes_wall_once(self):
        self.assertEqual(geometry.outer_half(5,400,6),36)
        self.assertEqual(geometry.half_limits(400,6),(9,36))

    def test_invalid_geometry_rejected(self):
        for value in (0,-1,float('nan'),float('inf')):
            with self.assertRaises(ValueError):geometry.inner_width(5,value)


if __name__=='__main__':unittest.main()
