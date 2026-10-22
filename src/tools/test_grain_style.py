"""Windowless invariants for shared grain coordinates and local landing style.

These tests exercise the parameters actually supplied to rendering, and the
real uniform updater. They do not certify shader output or visual quality.
"""

import ast
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]


def style_function(name):
    path = ROOT / "src/app/grain_style.py"
    if not path.is_file():
        raise AssertionError("Missing shared rendering module: src/app/grain_style.py")
    spec = importlib.util.spec_from_file_location("grain_style_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, name)


def real_update_flow():
    path = ROOT / "src/app/sand_flow_material.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "SandFlowContext")
    node = next(node for node in cls.body if isinstance(node, ast.FunctionDef)
                and node.name == "update_flow")
    namespace = {}
    exec(compile(ast.Module(body=[deepcopy(node)], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["update_flow"]


class Uniforms(dict):
    def __init__(self):
        super().__init__(sand_mix_pin=-1.0)
        self._material_key = None
        self._clock_key = None
        self._time_key = None
        self._tail_key = None
        self._free_key = None
        self._mat_key = None
        self._mix_key = None


class GrainStyleTests(unittest.TestCase):
    def test_texture_phase_is_finite_and_bounded_for_long_timers(self):
        phase = style_function("stream_texture_phase")
        for elapsed in (0.0, 0.1, 64.0, 600.0, 36000.0, 360000.0):
            for motion in (0.0, 0.25, 1.0, 8.0):
                for diameter in (0.0, 2.0, 47.0, 800.0):
                    with self.subTest(elapsed=elapsed, motion=motion, diameter=diameter):
                        value = phase(elapsed, motion, diameter)
                        self.assertTrue(math.isfinite(value))
                        self.assertGreaterEqual(value, 0.0)
                        self.assertLess(value, 1.0)

    def test_texture_phase_freezes_and_wraps_without_material_jump(self):
        phase = style_function("stream_texture_phase")
        for elapsed in (1.23, 63.99, 36000.125):
            before = phase(elapsed, 1.0, 47.0)
            self.assertEqual(phase(elapsed, 1.0, 47.0), before)
            period = 47.0 / 400.0
            after = phase(elapsed + period, 1.0, 47.0)
            circular_error = min((after - before) % 1.0, (before - after) % 1.0)
            self.assertLess(circular_error, 1e-9)

    def test_texture_phase_rate_tracks_motion_and_fixed_diameter(self):
        phase = style_function("stream_texture_phase")
        t, dt = 17.123, 0.00001
        slow = (phase(t + dt, 1.0, 47.0) - phase(t, 1.0, 47.0)) % 1.0
        fast = (phase(t + dt, 4.0, 47.0) - phase(t, 4.0, 47.0)) % 1.0
        wide = (phase(t + dt, 1.0, 94.0) - phase(t, 1.0, 94.0)) % 1.0
        self.assertGreater(slow, 0.0)
        self.assertAlmostEqual(fast, slow * 4.0, places=10)
        self.assertAlmostEqual(wide, slow / 2.0, places=10)
        self.assertEqual(phase(t, 0.0, 0.0), phase(t, 0.25, 2.0))

    def test_contact_is_absent_outside_local_band_or_without_sand(self):
        profile = style_function("contact_surface_profile")
        for offset, thickness in ((-100, 30), (-40, 30), (40, 30),
                                  (100, 30), (0, 0), (5, 0), (0, -1)):
            with self.subTest(offset=offset, thickness=thickness):
                self.assertEqual(profile(offset, 20.0, thickness, 0.5), (0.0, 0.0, 0.0))

    def test_contact_depth_and_displacement_stay_inside_budgets(self):
        profile = style_function("contact_surface_profile")
        for diameter in (2.0, 20.0, 120.0):
            for thickness in (0.01, 0.5, 50.0):
                for slope in (-10.0, -1.0, 0.0, 1.0, 10.0):
                    for fraction in (-1.99, -1.5, -0.5, 0.0, 0.5, 1.5, 1.99):
                        with self.subTest(diameter=diameter, thickness=thickness,
                                          slope=slope, fraction=fraction):
                            depth, dx, dy = profile(fraction * diameter, diameter, thickness, slope)
                            self.assertTrue(all(math.isfinite(v) for v in (depth, dx, dy)))
                            self.assertGreaterEqual(depth, 0.0)
                            self.assertLessEqual(depth, min(0.16 * diameter, thickness) + 1e-12)
                            self.assertLessEqual(math.hypot(dx, dy), 0.28 * diameter + 1e-12)
                            self.assertGreaterEqual(dx * fraction, 0.0)
                            if dx:
                                self.assertAlmostEqual(dy / dx, slope, places=10)

    def test_contact_is_centered_and_left_right_symmetric(self):
        profile = style_function("contact_surface_profile")
        depth, dx, _dy = profile(0.0, 20.0, 10.0, 0.0)
        self.assertGreater(depth, 0.0)  # An everywhere-zero effect cannot satisfy this test.
        self.assertEqual(dx, 0.0)
        for offset in (0.1, 5.0, 15.0, 30.0):
            right = profile(offset, 20.0, 10.0, -0.8)
            left = profile(-offset, 20.0, 10.0, 0.8)
            self.assertGreater(right[1], 0.0)
            self.assertLess(right[2], 0.0)
            self.assertAlmostEqual(right[0], left[0], places=12)
            self.assertAlmostEqual(right[1], -left[1], places=12)
            self.assertAlmostEqual(right[2], left[2], places=12)

    def test_contact_local_boundary_has_no_value_or_slope_step(self):
        profile = style_function("contact_surface_profile")
        for diameter in (2.0, 20.0, 120.0):
            step = diameter * 1e-5
            for sign in (-1.0, 1.0):
                edge = sign * 2.0 * diameter
                at_edge = profile(edge, diameter, 100.0, -0.5)
                inside = profile(edge - sign * step, diameter, 100.0, -0.5)
                outside = profile(edge + sign * step, diameter, 100.0, -0.5)
                self.assertEqual(at_edge, (0.0, 0.0, 0.0))
                self.assertEqual(outside, at_edge)
                for value in inside:
                    self.assertLess(abs(value) / step, 1e-3)

    def test_uniform_time_updates_after_a_full_wrapped_clock_period(self):
        update = real_update_flow()
        context = Uniforms()
        material = SimpleNamespace(flow_shade=1.0, flow_grad=0.35)
        palette = ((0.8, 0.6, 0.3), (0.6, 0.4, 0.2), (1.0, 0.8, 0.5))
        update(context, 1.0, 1.0, material, palette)
        self.assertEqual(context["sand_time"], 1.0)
        wrapped = context["sand_clock"]
        update(context, 17.0, 1.0, material, palette)
        self.assertEqual(context["sand_clock"], wrapped)
        self.assertEqual(context["sand_time"], 17.0)
        frozen = dict(context)
        update(context, 17.0, 1.0, material, palette)
        self.assertEqual(dict(context), frozen)


if __name__ == "__main__":
    unittest.main()
