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


class CoarseFlowTests(unittest.TestCase):
    def test_sand_accelerates_inside_the_neck_before_free_fall(self):
        release = style_function("neck_release_motion")
        self.assertEqual(release(50., 0.), (0., 47.5, 0.))
        for half, stem in ((2., 15.), (20., 12.), (50., 74.)):
            length, speed, transit = release(half, stem)
            self.assertGreater(length, 0.)
            self.assertLessEqual(length, stem)
            # Acceleration rises from zero to gravity at the lip. The visual
            # clock and actual birth use the speed at the end of this integral.
            k = math.sqrt(450. / length)
            self.assertAlmostEqual(47.5 / k * math.sinh(k * transit), length)
            self.assertAlmostEqual(47.5 * math.cosh(k * transit), speed)
            self.assertGreater(speed, 47.5)
            self.assertLess(2. * length / (speed + math.sqrt(speed**2 + 900.*length)), transit)

    def test_remaining_sand_and_emission_drain_together(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        widget = next(n for n in tree.body if getattr(n, "name", None) == "HourglassWidget")
        method = next(n for n in widget.body if getattr(n, "name", None) == "_released_fraction_at")
        env = {"FLOW_RENDERER": "grain"}
        exec(compile(ast.Module([deepcopy(method)], []), "main.py", "exec"), env)
        for duration in (1., 5., 50.):
            start, end = .10, duration - 1./60.
            span = min(.4, .2 * (end - start))
            w = SimpleNamespace(_transfer_timing=lambda: (start, end, .002), _sand_tail_span=span)
            release = lambda t: env[method.name](w, t)
            self.assertEqual(release(0.), 0.)
            self.assertEqual(release(duration), 1.)
            remaining = 1. - release(end - span)
            self.assertGreater(remaining, 0.)
            self.assertAlmostEqual((1. - release(end - .5 * span)) / remaining, .125)
            self.assertAlmostEqual(release(.35) - release(.30), release(.30) - release(.25))

    def test_tablet_jet_spreads_contraction_over_the_visible_fall(self):
        parameters = style_function("coarse_width_parameters")
        # Y700 recordings: outlet half-widths for the 5, 20 and 50 s timers.
        for half in (72., 51., 40.):
            with self.subTest(half=half):
                length, floor, ramp = parameters(half, 4.3, .87, 92.)
                def width(depth):
                    target = max(floor, (1. + depth / length)**-.25)
                    return 1. + (target - 1.) * min(1., depth / ramp)
                self.assertGreater(width(2. * half), .89)
                self.assertGreater(width(400.) - width(850.), .065)

    def test_wide_stream_gradually_contracts_but_thin_mouth_keeps_grain_support(self):
        parameters = style_function("coarse_width_parameters")
        mouth, floor, ramp = parameters(31., 1.85, .87, 92.)
        def ratio(depth):
            target = max(floor, (1. + depth / mouth)**-.25)
            return 1. + (target - 1.) * min(1., depth / ramp)
        self.assertGreater(ratio(.1 * mouth), .96)
        self.assertAlmostEqual(ratio(4. * mouth), 5.**-.25)
        self.assertLess(ratio(6. * mouth), ratio(4. * mouth))
        self.assertGreaterEqual(parameters(3., .8, .87, 92.)[1], .85)

    def test_contact_patch_returns_to_both_slopes_without_a_vertical_step(self):
        tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
        widget = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "HourglassWidget")
        method = next(n for n in widget.body if getattr(n, "name", None) == "_draw_contact_grains")
        ns = {"FLOW_SHRINK_MIN": .87, "MOUND_CREST_MARGIN": 10.}
        exec(compile(ast.Module([deepcopy(method)], []), "main.py", "exec"), ns)
        segments = {}
        band = SimpleNamespace(set=segments.__setitem__, flush=lambda: None)
        surface = lambda dx: 100. - .6 * abs(dx)
        w = SimpleNamespace(pn=100, _mound_profile=SimpleNamespace(
            column=lambda dx, apex: (surface(dx), True, 20.)), _last_impact_clock=1.,
            _mound_flow_clock=1., _contact_grains=band, _taper={"t_in":62., "y_bot":400.},
            _R_inner=419., _neck_y=450., _mound_apex=lambda:100., _cx=0., _lower_sand_bot=0.,
            _contact_grain_color=SimpleNamespace(), _sand_material=None, sand_base=(1.,.5,.2))
        ns[method.name](w)
        self.assertAlmostEqual(segments[0][7], surface(segments[0][0]))
        self.assertAlmostEqual(segments[7][5], surface(segments[7][2]))
        self.assertGreater(segments[4][7], surface(0.) + 1.)

    def test_landing_keeps_an_immediate_pile_and_spreads_the_initial_jump(self):
        limit = style_function("first_pile_height")
        mouth = 62.65
        ages = (0, 1/30, 2/30, .20, .45, 1.)
        raw = (22.5, 75.8, 89.7, 130., 164., 200.)
        heights = [limit(h, age, mouth) for h, age in zip(raw, ages)]
        self.assertGreater(heights[0], 8)
        self.assertLess(heights[1], 43)
        self.assertLess(heights[2] - heights[1], 25)
        self.assertEqual(heights[-2:], list(raw[-2:]))
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(limit(0., 0., mouth), 0.)
        self.assertAlmostEqual(limit(2*raw[1], ages[1], 2*mouth), 2*heights[1])

    def test_b13_first_contact_starts_with_grains_and_retains_the_final_height(self):
        limit = style_function("b13_pile_height")
        heights = [limit(160., age, 62.) for age in (0., .04, .12, .22, .4, .6, .8)]
        self.assertTrue(0. < heights[0] < 3.)
        self.assertLess(heights[3], 46.)
        self.assertEqual(heights, sorted(heights))
        self.assertEqual(heights[-1], 160.)

    def test_last_packed_sand_narrows_before_the_throat_empties(self):
        ratio = style_function("b13_neck_ratio")
        widths = [ratio(h, 40.) for h in (0., .25, 2., 12., 60.)]
        self.assertEqual(widths[0], 0.)
        self.assertLess(widths[1], .30)
        self.assertEqual(widths, sorted(widths))
        self.assertEqual(widths[-1], 1.)

    def test_tail_births_remain_ordered_across_the_rate_transition(self):
        budget = style_function("tail_emission_time")
        inverse = style_function("tail_remaining_time")
        span = .13
        for remaining in (0., .0001, .03, .1, span, .25, 1.):
            self.assertAlmostEqual(inverse(budget(remaining, span), span), remaining, places=12)
        self.assertAlmostEqual(budget(.30, span) - budget(.20, span), .1)
        self.assertLess(budget(.03, span), .003)
        self.assertAlmostEqual(budget(span, span), span / 3)

    def test_terminal_release_covers_the_flight_to_the_full_pile_at_both_sizes(self):
        duration = style_function("tail_duration")
        for gap, scale in ((15., 1.), (31., 1.4)):
            span = duration(gap, scale, 4.7)
            distance = 47.5 * scale * span + 225. * (scale * span)**2
            self.assertGreaterEqual(distance, gap)
            self.assertLess(distance, 2. * gap)
        self.assertLessEqual(duration(50., 2., .25), .05)


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
    def test_landing_lower_grains_does_not_flatten_or_lift_the_exposed_cap(self):
        import numpy as np
        shape=style_function('drained_stream_y')
        args=dict(center=0.,outlet=120.,half=19.,pitch=1.6,supplied=1.)
        # Same rear cohort before/after a lower grain reaches the mound.
        before=shape([-16.,0.,16.,0.],[100.,100.,100.,85.],**args)
        after=shape([-16.,0.,16.],[100.,100.,100.],**args)
        np.testing.assert_allclose(after,before[:3],rtol=0.,atol=1e-12)
        self.assertGreater(after[1]-after[0],2.)

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
        x=np.array([-16.,0.,16.,0.]);y=np.array([100.,100.,100.,60.])
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
        # The last survivor must not jump back up when the penultimate lands.
        before=shape([10.,0.],[100.,85.],**args)
        np.testing.assert_array_equal(shape([10.],[100.],**args),before[:1])
        first=shape([-16.,0.,16.,0.],[100.,100.,100.,85.],**args)
        np.testing.assert_array_equal(shape([-16.,0.,16.,0.],[100.,100.,100.,85.],**args),first)


class MoundShoulderTests(unittest.TestCase):
    def test_rounding_remains_visible_beside_the_stream(self):
        tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='HourglassWidget')
        rebuild=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='_rebuild_height_table')
        cap=next(n for n in ast.walk(rebuild) if isinstance(n,ast.Assign)
                 and any(isinstance(t,ast.Name) and t.id=='cap_radius' for t in n.targets))
        drop=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_mound_base_drop')
        for half in (8.,19.,27.):
            ns=dict(Ri=180.,self=SimpleNamespace(_taper={'t_in':half}),FLOW_RENDERER='grain',
                    MOUND_SLOPE_L=.58,MOUND_SLOPE_R=.62)
            exec(compile(ast.Module(body=[deepcopy(cap),deepcopy(drop)],type_ignores=[]),'mound','exec'),ns)
            f=ns['_mound_base_drop']; r=ns['cap_radius']; dx=half*.20
            for sign in (-1.,1.):
                x=sign*1.25*half
                # Positive curvature outside the visible stream; an entirely
                # hidden cap leaves a straight triangular shoulder here.
                curvature=f(x-dx,r)+f(x+dx,r)-2*f(x,r)
                self.assertGreater(curvature,.01*half)
                far=3.*half
                slope=.58 if sign<0 else .62
                self.assertAlmostEqual(f(sign*far,r),slope*far)
            ns['FLOW_RENDERER']='b13'
            exec(compile(ast.Module(body=[deepcopy(cap)],type_ignores=[]),'mound','exec'),ns)
            self.assertAlmostEqual(ns['cap_radius'], min(.12*ns['Ri'], 1.1*half))


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


class StreamProfileTests(unittest.TestCase):
    def test_bulk_width_budget_does_not_depend_on_timer_aperture(self):
        import numpy as np
        from short_grain_stream import cohort_stream_positions
        birth=np.linspace(0.,80.,16001)
        for half in (14.8,18.3,19.,27.):
            x=np.full_like(birth,.74*half); y=np.full_like(birth,-10.*half)
            args=dict(center=0.,outlet=0.,half=half,pitch=1.6,radius=180.)
            right=cohort_stream_positions(x,y,birth,np.ones_like(birth),**args)
            left=cohort_stream_positions(-x,y,birth,np.ones_like(birth),**args)
            width=right-left
            # A stable body has at most 2.5% peak-to-peak geometric inflation.
            # This is independent of raster rounding and individual rim grains.
            self.assertLessEqual(np.ptp(width)/(1.48*half),.025+1e-9)
            self.assertGreater(np.std((right+left)*.5),.05,
                               'Fine streams retain local contour motion without bulk inflation')

    def test_outlet_policy_is_scale_invariant_and_has_a_smooth_transition(self):
        import numpy as np
        from short_grain_stream import steady_profile_mix
        halves=np.linspace(8.,11.,301)
        values=[steady_profile_mix(h,100.) for h in halves]
        self.assertEqual(values[0],0.)
        self.assertEqual(values[-1],1.)
        self.assertTrue(np.all(np.diff(values)>=0.))
        for h in halves:
            self.assertAlmostEqual(steady_profile_mix(h,100.),steady_profile_mix(h*3,300.),places=12)

    def test_axis_sway_is_reduced_without_erasing_width_variation(self):
        import numpy as np
        birth=np.linspace(0.,20.,4001)
        x=np.full_like(birth,14.);y=np.full_like(birth,-160.)
        right=self.shape(x,y,birth);left=self.shape(-x,y,birth)
        axis=(right+left)*.5
        width=(right-left)*.5
        # Independent v5 oracle: both sides used unrelated 14% fluctuations.
        cell=np.floor(birth*4.);t=birth*4.-cell;t=t*t*(3.-2.*t)
        def old_noise(side):
            a=(np.sin(cell*12.9898+side)*43758.5453)%1.
            b=(np.sin((cell+1.)*12.9898+side)*43758.5453)%1.
            return 2.*(a*(1.-t)+b*t)-1.
        old_right=x*(1.+.14*old_noise(63.71))
        old_left=-x*(1.+.14*old_noise(17.13))
        self.assertLess(np.std(axis),.35*np.std((old_right+old_left)*.5))
        # The old requirement to retain 60% of v5's inflation conflicts with
        # the stable-body design. Keep a nonconstant field; GL review owns
        # visible edge detail, not a minimum whole-column swelling amplitude.
        self.assertGreater(np.std(width),.05)
        self.assertLessEqual(np.max(np.abs(width/14.-1.)),.10+1e-9)

    def test_fine_core_mapping_keeps_order_and_ignores_individual_grain_seed(self):
        import numpy as np
        from short_grain_stream import art_stream_positions
        x=np.linspace(-11.,11.,1001);y=np.full_like(x,-120.)
        phase=np.linspace(0.,6.28,len(x))
        args=dict(center=0.,outlet=0.,half=14.8,pitch=1.85)
        result=art_stream_positions(x,y,phase,**args)
        np.testing.assert_array_equal(result,art_stream_positions(x,y,phase[::-1],**args))
        self.assertTrue(np.all(np.diff(result)>0.))
        self.assertTrue(np.all(np.abs(result)<14.8))

    def shape(self, x, y, births, scales=1.):
        import short_grain_stream as stream
        self.assertTrue(hasattr(stream, 'cohort_stream_positions'),
                        'Static straight carrier envelope has no advected profile')
        return stream.cohort_stream_positions(x,y,births,scales,
                                              center=0,outlet=0,half=19,pitch=1.6)

    def test_profile_is_nonconstant_and_coherent_across_neighbouring_grains(self):
        import numpy as np
        births=np.linspace(0,20,4001);x=np.full_like(births,14.);y=np.full_like(births,-160.)
        result=self.shape(x,y,births)
        self.assertGreater(np.std(result), .20)
        self.assertLess(np.max(np.abs(np.diff(result))), .30)
        self.assertLess(np.max(np.abs(result-x)), 2.7)

    def test_outlet_pause_and_speed_scaled_birth_coordinate(self):
        import numpy as np
        births=np.linspace(0,20,101);x=np.full_like(births,14.)
        np.testing.assert_array_equal(self.shape(x,np.zeros_like(x),births),x)
        first=self.shape(x,np.full_like(x,-160.),births)
        np.testing.assert_array_equal(self.shape(x,np.full_like(x,-160.),births),first)
        np.testing.assert_array_equal(self.shape(x,np.full_like(x,-160.),births/2,2.),first)
        selected=np.arange(100,-1,-3)
        np.testing.assert_array_equal(self.shape(x[selected],np.full(len(selected),-160.),births[selected]),first[selected])



if __name__ == "__main__":
    unittest.main()
