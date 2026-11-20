"""Compare the candidate application trajectory to measured v3.19 frames."""

import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import grain_style
from grain_style import B13_REFERENCE_RADIUS, B13Transit, hourglass_transit
from neck_geometry import inner_width


def transit_for(duration):
    half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
    return hourglass_transit(duration, half)


class B14OpeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not grain_style.B14_TRIAL:
            # Exercise the trial profile in an ordinary CI checkout. Package
            # commands separately require the actual marker before any tests run.
            trial = patch.object(grain_style, 'B14_TRIAL', True)
            trial.start()
            cls.addClassCleanup(trial.stop)

    def test_leading_edge_matches_measured_normal_speed_positions(self):
        # 540 by 924, outlet y=477.27. The floor was still empty at frame 45.
        for duration in (20, 50, 6000):
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

    def test_short_timers_keep_their_existing_transport_clock(self):
        for duration in (1., 2., 5.):
            half = inner_width(duration, 2 * B13_REFERENCE_RADIUS) / 2
            previous = B13Transit.for_glass(duration, half)
            for depth in (2., 15., 216., 435.):
                self.assertEqual(transit_for(duration).age_at(depth), previous.age_at(depth))

    def test_user_selected_intermediate_periods_join_both_ends_continuously(self):
        periods = (5., 5.001, 6., 10., 15., 19.999, 20., 50.)
        for depth in (2., 130., 435.):
            times = [transit_for(period).age_at(depth) for period in periods]
            self.assertEqual(times, sorted(times))
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


def run_packaged(command):
    root = Path(__file__).resolve().parents[2]
    expected = root / 'src/app/b14_trial_build.py'
    marker = sys.modules.get('b14_trial_build')
    if (not expected.is_file() or not marker or Path(marker.__file__).resolve() != expected
            or marker.B14_TRIAL is not True or grain_style.B14_TRIAL is not True):
        print('B14 trial build marker missing or inactive', file=sys.stderr)
        return 1
    if command == '--packaged-capacity':
        from test_splash33_impulse import opening_capacity_samples

        source = (root / 'main.py').read_text(encoding='utf-8')
        oversized = False
        for glass, arrival, released, capacity in opening_capacity_samples(source):
            if not all(math.isfinite(v) and v > 0. for v in (arrival, released, capacity)):
                raise ValueError(f'Invalid capacity measurement for {glass.duration} seconds')
            print('CAPACITY', json.dumps(dict(duration=glass.duration, released=released,
                                               capacity=capacity)))
            if released > capacity:
                oversized = True
                print(f'{glass.duration:g} s FAIL: released {released:.3%}, '
                      f'maximum {capacity:.3%}', file=sys.stderr)
        return 2 if oversized else 0

    from test_outlet_flow import ApplicationOutletFlowTests

    suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(case)
                               for case in (B14OpeningTests, ApplicationOutletFlowTests))
    return 0 if unittest.TextTestRunner().run(suite).wasSuccessful() else 1


if __name__ == '__main__':
    if sys.argv[1:] in (['--packaged-trial'], ['--packaged-capacity']):
        sys.exit(run_packaged(sys.argv[1]))
    unittest.main()
