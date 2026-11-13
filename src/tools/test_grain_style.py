"""Windowless invariants for shared grain coordinates and local landing style.

These tests exercise the parameters actually supplied to rendering, and the
real uniform updater. They do not certify shader output or visual quality.
"""

import ast
from copy import deepcopy
import importlib.util
import math
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src/app"))


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


class UpperSurfaceStyleTests(unittest.TestCase):
    def model(self):
        path = ROOT/'main.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        names = {'UPPER_FUNNEL_DEPTH','UPPER_FUNNEL_WIDTH','UPPER_FUNNEL_MAXH','UPPER_FUNNEL_MAXB'}
        nodes = [deepcopy(n) for n in tree.body if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id in names for t in n.targets)]
        cls = next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
        nodes += [deepcopy(n) for n in cls.body if isinstance(n,ast.FunctionDef)
                  and n.name in ('_upper_funnel_params','_upper_surface_drop')]
        def smooth(a,b,x):
            t=max(0.,min(1.,(x-a)/(b-a)))
            return t*t*(3.-2.*t)
        ns=dict(math=math,_smoothstep=smooth)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),str(path),'exec'),ns)
        return ns,SimpleNamespace(_R_inner=180.,_upper_sand_bot=0.,_taper={'in_pts':[(20.,0.)]})

    def test_sink_is_broad_and_readable_at_normal_size(self):
        ns,w=self.model()
        d,b=ns['_upper_funnel_params'](w,.5,180.)
        drop=ns['_upper_surface_drop']
        self.assertGreater(drop(w,0.,d,b)-drop(w,.8*b,d,b),5.)
        self.assertGreater(b,150.)
        self.assertLess(d/b,.08)

    def test_full_empty_and_thin_surface_stay_bounded(self):
        ns,w=self.model();fn=ns['_upper_funnel_params']
        self.assertEqual(fn(w,0.,360.)[0],0.)
        self.assertEqual(fn(w,1.,0.)[0],0.)
        for height in (.01,.5,2.,10.,60.,180.,359.):
            d,b=fn(w,.95,height)
            self.assertGreaterEqual(d,0.)
            self.assertLessEqual(d,.25*height)
            self.assertLessEqual(b,math.sqrt(180.**2-(180.-height)**2)+1e-8)


class DrainedStreamTests(unittest.TestCase):
    def test_live_supply_and_unexposed_outlet_are_unchanged(self):
        import numpy as np
        shape=style_function('drained_stream_y')
        x=np.array([-16.,0.,16.]);y=np.array([100.,100.,100.])
        for supplied,outlet in ((.99,120.),(1.,100.)):
            np.testing.assert_array_equal(shape(x,y,center=0.,outlet=outlet,half=19.,
                pitch=1.6,supplied=supplied),y)

    def test_exposed_cap_rounds_shoulders_without_rescaling_or_deleting_grains(self):
        import numpy as np
        shape=style_function('drained_stream_y')
        x=np.array([-16.,0.,16.,0.]);y=np.array([100.,100.,100.,85.])
        before=y.copy()
        result=shape(x,y,center=0.,outlet=120.,half=19.,pitch=1.6,supplied=1.)
        self.assertEqual(result.shape,y.shape)
        self.assertGreater(result[1]-result[0],2.)
        self.assertEqual(result[0],result[2])
        self.assertEqual(result[3],y[3])
        self.assertTrue(np.all(result<=y))
        np.testing.assert_array_equal(y,before)

    def test_empty_last_grain_and_paused_calls_are_stable(self):
        import numpy as np
        shape=style_function('drained_stream_y')
        args=dict(center=0.,outlet=120.,half=19.,pitch=1.6,supplied=1.)
        self.assertEqual(len(shape([],[],**args)),0)
        np.testing.assert_array_equal(shape([10.],[100.],**args),[100.])
        first=shape([-16.,0.,16.,0.],[100.,100.,100.,85.],**args)
        np.testing.assert_array_equal(shape([-16.,0.,16.,0.],[100.,100.,100.,85.],**args),first)


class StreamArtConsistencyTests(unittest.TestCase):
    """The same spatial sample must not switch style with timer phase."""

    def test_style_has_no_cycle_or_contact_inputs(self):
        import inspect
        from short_grain_stream import art_stream_positions
        self.assertEqual(set(inspect.signature(art_stream_positions).parameters),
                         {'xs', 'ys', 'phases', 'center', 'outlet', 'half', 'pitch', 'strength'})

    def test_first_middle_and_last_grains_share_one_drawing_rule(self):
        import numpy as np
        from short_grain_stream import art_stream_positions as shape
        x = np.linspace(276., 308., 101)
        y = np.full(101, 170.)
        phase = np.linspace(.1, 6.2, 101)
        args = dict(center=292., outlet=388., half=19., pitch=1.6)
        original = [a.copy() for a in (x, y, phase)]
        first = shape(x, y, phase, **args)
        self.assertTrue(np.any(first != x), 'The first rendered grains must use the style')
        for _ in range(3):
            np.testing.assert_array_equal(shape(x, y, phase, **args), first)
        # Reordering/compaction must preserve the same grain's appearance.
        selected = np.arange(100, -1, -3)
        np.testing.assert_array_equal(shape(x[selected], y[selected], phase[selected], **args),
                                      first[selected])
        for before, after in zip(original, (x, y, phase)):
            np.testing.assert_array_equal(before, after)

    def test_outlet_and_narrow_stream_remain_connected(self):
        import numpy as np
        from short_grain_stream import art_stream_positions as shape
        x = np.array([279., 292., 305.])
        for half, y in ((19., 387.), (4., 200.)):
            np.testing.assert_array_equal(shape(x, np.full(3,y), [0.,1.,2.],
                center=292., outlet=388., half=half, pitch=1.6), x)

    def test_falling_grain_has_no_horizontal_jump_or_material_rescale(self):
        import numpy as np
        from short_grain_stream import art_stream_positions as shape, particle_vertices
        y = np.linspace(388., 0., 3881)
        x = np.full_like(y, 307.)
        phase = np.full_like(y, 1.2)
        drawn = shape(x,y,phase,center=292.,outlet=388.,half=19.,pitch=1.6)
        self.assertLess(np.max(np.abs(np.diff(drawn))), .02)
        self.assertTrue(np.all((drawn >= 292.) & (drawn <= x)))
        old = particle_vertices(x,y,phase,np.ones_like(y),1.6)
        new = particle_vertices(drawn,y,phase,np.ones_like(y),1.6)
        np.testing.assert_array_equal(new[:,:,2:],old[:,:,2:])
        np.testing.assert_allclose(np.ptp(new[:,:,:2],axis=1),np.ptp(old[:,:,:2],axis=1),atol=4e-5,rtol=0)


if __name__ == "__main__":
    unittest.main()
