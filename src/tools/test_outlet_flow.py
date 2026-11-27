"""Outlet discharge per active second, independent of particle speed and landing time."""

import ast
from pathlib import Path
import sys
import unittest

import numpy as np

from check_visible_sand_volume import check_outlet_flow


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/app"))


def from_curve(times, discharged):
    return np.column_stack((times, 1. - discharged, discharged))


class OutletFlowControls(unittest.TestCase):
    def test_upper_cavity_is_checked_even_with_no_flight_debit(self):
        from check_visible_sand_volume import check_opening_inventory

        # Each entry contains time, upper surface row, projected volume above
        # the lip, volume below it, and a resolved visual crossing.
        radius = 209.
        full = 4.*radius**3/3.
        rows = [(0., 14, full, 0., False), (.133, 27, full*.99731, 1., False),
                (.183, 30, full*.99639, 1., False), (.433, 43, full*.991, .009*full, True),
                (.450, 44, full*.990, .010*full, True)]
        with self.assertRaisesRegex(ValueError, "上杯露空"):
            check_opening_inventory(rows, radius, 32.)
        # If the missing upper sand actually filled the empty throat the
        # volume above the outlet is conserved, even if the upper level fell.
        rows[1:3] = [(t, y, full-dust, dust, flag) for t, y, _, dust, flag in rows[1:3]]
        check_opening_inventory(rows, radius, 32.)

    def test_flight_debit_requires_visible_outlet_crossing(self):
        from check_visible_sand_volume import check_first_crossing

        # The book alone balances even if no sand has passed the real opening.
        times = [0., 1./60., .2, .433, .45]
        frames = [dict(time=t, transfer={"flight": t/50.},
                       draw_state={"rendered_particles": int(t >= .433)}) for t in times]
        samples = [(t, 1.-t/50., (t/50. if t >= .433 else 0.)) for t in times]
        with self.assertRaisesRegex(ValueError, "0.200"):
            check_first_crossing(frames, samples, 50.)
        # A single ambiguous pixel and draw call cannot certify the start
        # of a continuous flow and suppress the following confirmed gap.
        frames[1]["draw_state"]["rendered_particles"] = 1
        samples[1] = (times[1], 1.-times[1]/50., 1e-9)
        with self.assertRaisesRegex(ValueError, "0.200"):
            check_first_crossing(frames, samples, 50.)
        for frame in frames[:3]:
            frame["transfer"]["flight"] = 0.
        check_first_crossing(frames, samples, 50.)

    def test_equal_volume_passes_with_flight_delay_and_fractional_last_second(self):
        duration = 5.4
        times = np.arange(0., 8., 1. / 30.)
        released = np.clip(times / duration, 0., 1.)
        landed = np.clip((times - .8) / duration, 0., 1.)
        flight = released - landed
        samples = np.column_stack((times, 1. - released, flight + landed))
        self.assertEqual(check_outlet_flow(samples, duration), 1.)
        self.assertEqual(check_outlet_flow(samples, duration, pixels=True), 1.)

    def test_correct_total_and_finish_do_not_hide_unequal_seconds(self):
        times = np.arange(0., 6., 1. / 30.)
        released = np.interp(times, [0., 1., 2., 3., 4., 5.], [0., .01, .02, .06, .5, 1.])
        for pixels in (False, True):
            with self.assertRaisesRegex(ValueError, "应为"):
                check_outlet_flow(from_curve(times, released), 5., pixels=pixels)

    def test_deadline_between_capture_frames_uses_only_observed_amounts(self):
        times = np.arange(0., 7., 1. / 9.)
        duration = 5.43
        samples = from_curve(times, np.minimum(times / duration, 1.))
        self.assertEqual(check_outlet_flow(samples, duration), 1.)

    def test_one_second_bursts_and_late_discharge_fail(self):
        times = np.arange(0., 2., 1. / 30.)
        for released in (np.minimum(1., np.floor(4. * times) / 4.),
                         np.clip((times - .3), 0., 1.)):
            with self.assertRaisesRegex(ValueError, "应为"):
                check_outlet_flow(from_curve(times, released), 1.)

    def test_each_side_of_the_outlet_must_match(self):
        times = np.arange(0., 6., 1. / 30.)
        good = np.minimum(1., times / 5.)
        for above, below, name in ((1. - good, good**2, "出口以下"),
                                   (1. - good**2, good, "出口以上")):
            with self.assertRaisesRegex(ValueError, name):
                check_outlet_flow(np.column_stack((times, above, below)), 5., pixels=True)

    def test_long_timer_cannot_hide_a_missing_second_in_pixel_uncertainty(self):
        times = np.arange(0., 601., .25)
        released = np.minimum(1., times / 600.)
        samples = from_curve(times, released)
        self.assertEqual(check_outlet_flow(samples, 600., pixels=True), 60.)
        released[(times >= 300.) & (times <= 301.)] = .5
        with self.assertRaisesRegex(ValueError, "应为"):
            check_outlet_flow(from_curve(times, released), 600.)

    def test_missing_opening_tail_or_intermediate_frames_cannot_pass(self):
        times = np.arange(0., 6., 1. / 30.)
        samples = from_curve(times, np.minimum(1., times / 5.))
        for incomplete in (samples[10:], samples[times <= 5.], samples[::45]):
            with self.assertRaises(ValueError):
                check_outlet_flow(incomplete, 5.)


class ApplicationOutletFlowTests(unittest.TestCase):
    def test_selected_timer_drives_the_actual_b13_release_curve(self):
        from grain_style import B13_REFERENCE_RADIUS, hourglass_transit
        from neck_geometry import SandNeck

        # Exercise the same widget methods that select the supply clock and
        # debit the reservoir. No Kivy window or capture files are needed in CI.
        path = ROOT / "main.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        widget = next(node for node in tree.body if getattr(node, "name", "") == "HourglassWidget")
        names = {"_supply_duration", "_supply_elapsed", "_neck_fill_time", "_transfer_timing",
                 "_released_fraction_at", "_upper_sand_fraction"}
        methods = [node for node in widget.body if getattr(node, "name", "") in names]
        self.assertEqual({node.name for node in methods}, names)
        namespace = {"FLOW_RENDERER": "b13"}
        exec(compile(ast.Module(methods, []), str(path), "exec"), namespace)
        model = type("Glass", (), {name: namespace[name] for name in names})()
        model._R_inner = radius = B13_REFERENCE_RADIUS
        model._geom_ready = True
        for duration, half in ((1., 31.), (5., 31.), (20., 22.), (50., 17.), (6000., 5.)):
            with self.subTest(duration=duration):
                model.duration, model._sand_timing = duration, None
                model._sand_neck = SandNeck(radius, 99., [(half+15., 109.), (half, 80.)],
                                            hourglass_transit(duration, half), 1.)
                model._taper = {"t_in": half, "y_bot": 432.}
                start, cutoff, reserve = model._transfer_timing()
                self.assertEqual((start, cutoff), (0., duration))
                neck = model._sand_neck
                bulb = 4. * radius**3 / 3.
                cap_height = neck.heights[-1] - neck.pole
                cap = cap_height**2 * (radius - cap_height/3.)
                self.assertAlmostEqual(reserve, neck.volumes[-1] / (bulb - cap + neck.volumes[-1]))
                self.assertAlmostEqual(model._upper_sand_fraction(0.) + reserve, 1.)
                times = np.arange(0., duration + 1., .125)
                values = [model._released_fraction_at(t) for t in times]
                model.elapsed = duration
                model._stream_settle_age = .5
                self.assertEqual(model._upper_sand_fraction(), 0.)
                self.assertEqual(model._supply_elapsed, duration)
                try:
                    check_outlet_flow(from_curve(times, np.array(values)), duration)
                except ValueError as error:
                    raise self.failureException(str(error)) from None


if __name__ == "__main__":
    unittest.main()
