"""Compare the candidate application trajectory to measured v3.19 frames."""

from pathlib import Path
import sys
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from grain_style import B13_REFERENCE_RADIUS, B13Transit, hourglass_transit
from neck_geometry import inner_width


def transit_for(duration):
    half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
    return hourglass_transit(duration, half)


class OpeningTests(unittest.TestCase):
    def test_leading_edge_matches_measured_normal_speed_positions(self):
        # 540 by 924, outlet y=477.27. The floor was still empty at frame 45.
        for duration in (30, 50, 6000):
            with self.subTest(duration=duration):
                transit = transit_for(duration)
                for age, depth in ((1/30, 0.), (.40, 17.7), (.80, 102.7),
                                   (32/30, 199.7), (40/30, 328.7), (1.50, 425.7)):
                    self.assertAlmostEqual(transit.depth_at(age), depth, delta=1.)
                self.assertGreater(transit.age_at(432.67), 1.50)
                self.assertLess(transit.age_at(432.67), 46/30)

    def test_first_contact_and_carrier_speed_share_the_same_motion(self):
        transit = transit_for(6000.)
        ages = np.linspace(.001, 2.1, 113)
        lanes = np.linspace(-1., 1., len(ages))
        jitter = np.cos(ages)
        for scale in (.6, 1., 2.4):
            y, vy = transit.motion(ages, lanes, jitter, 480., scale)
            depth = (480. - y) / scale
            np.testing.assert_allclose(transit.age_at(depth) / transit.lane_factor(lanes, jitter), ages, atol=2e-5)
            self.assertTrue(np.all(vy < 0.))
            for i in (0, 17, 65, 98, 112):
                scalar = transit.motion(float(ages[i]), float(lanes[i]), float(jitter[i]), 480., scale)
                np.testing.assert_allclose(scalar, (y[i], vy[i]), atol=1e-8)
        parcels = list(transit.cohorts(0., 4., 4., 440., 6000., 600))
        self.assertAlmostEqual(sum(mass for _, mass in parcels), 4. / 6000.)

    def test_short_timers_use_their_actual_opening_capacity(self):
        for duration in (1., 2., 5.):
            half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
            previous = B13Transit.for_glass(duration, half)
            for depth in (2., 15., 216., 435.):
                self.assertEqual(transit_for(duration).age_at(depth), previous.age_at(depth))

    def test_short_glass_spreads_the_first_arrivals_and_recovers_steady_flow(self):
        for duration in (1., 2., 5., 15.):
            half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
            bulk = hourglass_transit(duration, half)
            clear = bulk.age_at(2. * B13_REFERENCE_RADIUS)
            age = .75 * clear
            lanes = np.array([0., -.35, .50])
            jitter = np.array([1., -.70, .25])
            expected, _ = bulk.motion(age, lanes, jitter, 500., 1.)
            early, _ = bulk.motion(age, lanes, jitter, 500., 1., birth=0.)
            steady, _ = bulk.motion(age, lanes, jitter, 500., 1., birth=clear)
            np.testing.assert_array_equal(steady, expected)
            self.assertEqual(early[0], expected[0])
            if duration < 5.:
                self.assertTrue(np.all(early[1:] > expected[1:]))
            else:
                np.testing.assert_array_equal(early, expected)
            births = np.array([0., .5*clear, clear])
            batch = bulk.motion(age, lanes, jitter, 500., 1., birth=births)
            single = [bulk.motion(age, x, j, 500., 1., birth=b) for x, j, b in zip(lanes, jitter, births)]
            np.testing.assert_allclose(batch, np.transpose(single), rtol=0., atol=1e-9)

    def test_user_selected_intermediate_periods_join_both_ends_continuously(self):
        periods = (5., 5.000001, 6., 10., 15., 20., 29.999999, 30., 50.)
        for depth in (2., 130., 435.):
            times = [transit_for(period).age_at(depth) for period in periods]
            self.assertTrue(np.all(np.diff(times) >= -1e-9), times)
            self.assertLess(times[1] - times[0], 1e-6)
            self.assertLess(times[-2] - times[-3], 1e-6)
            self.assertEqual(times[-2], times[-1])

    def test_flowing_tail_clears_the_lip_instead_of_replaying_the_opening(self):
        from b13_stream import sample_grains
        for duration in (20., 50., 6000.):
            half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
            flow = hourglass_transit(duration, half, clock=duration)
            self.assertLess(flow.age_at(2.), .01)
            self.assertLess(flow.age_at(15.), .05)
            self.assertLess(flow.age_at(35.), .10)
            last = sample_grains(duration + .11, duration, half, 35., 1.)
            self.assertEqual(len(last.serial), 0)

    def test_material_stays_in_order_as_the_flow_establishes_itself(self):
        for duration in (6., 10., 20., 6000.):
            half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
            # Follow particular grains through the transition, including grains
            # born just before and just after the onset of the steady flow.
            for birth in (2.3, 5.4, 6.4, 8.2):
                if birth > duration:
                    continue
                previous = 0.
                for age in np.linspace(0., 1.2, 81):
                    flow = hourglass_transit(duration, half, clock=birth + float(age))
                    y, vy = flow.motion(float(age), .8, -.3, 0., 1.)
                    depth = -y
                    self.assertGreaterEqual(depth, previous - 1e-8)
                    self.assertLess(vy, 0.)
                    self.assertAlmostEqual(flow.age_at(depth) / flow.lane_factor(.8, -.3), age, delta=.0002)
                    previous = depth


if __name__ == "__main__":
    unittest.main()
