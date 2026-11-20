"""Rendering contract: finite short grains, actual front/tail, no phantom sand."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
from short_grain_stream import grain_pitch, flow_phase, stream_limits, motion_parameters


class ShortGrainTests(unittest.TestCase):
    def test_speed_is_not_controlled_by_grain_size(self):
        fine = motion_parameters(12, 1, 400, 1)
        coarse = motion_parameters(12, 3, 400, 1)
        self.assertEqual(fine[:2], coarse[:2])

    def test_speed_is_screen_relative_and_accelerates(self):
        v, g, rate = motion_parameters(12, 2, 400, 1)
        big = motion_parameters(24, 4, 800, 1)
        self.assertGreaterEqual(v, 3 * 24)
        self.assertGreater(g, 0)
        self.assertAlmostEqual(big[0], 2*v)
        self.assertAlmostEqual(big[1], 2*g)
        self.assertAlmostEqual(big[2], rate)

    def test_empty_has_no_stream(self):
        self.assertIsNone(stream_limits([], 100, 0, 2, False))

    def test_front_does_not_instantly_reach_mound(self):
        top, bottom = stream_limits([96, 90], 100, 0, 2, False)
        self.assertLessEqual(top, 100)
        self.assertGreaterEqual(bottom, 88)

    def test_contact_ends_inside_actual_surface(self):
        top, bottom = stream_limits([96, 32], 100, 30, 2, True)
        self.assertLess(bottom, 30)
        self.assertGreater(bottom, 27)

    def test_tail_does_not_reconnect_to_empty_neck(self):
        top, bottom = stream_limits([43, 40], 100, 0, 2, False)
        self.assertLess(top, 50)
        self.assertLess(bottom, top)

    def test_grain_scales_with_canvas(self):
        self.assertAlmostEqual(grain_pitch(400, 1.8) / grain_pitch(150, 1.8), 400 / 150)

    def test_settings_remain_distinct(self):
        sizes = [grain_pitch(180, c) for c in (1, 1.4, 1.8, 2.2, 2.6, 3)]
        self.assertTrue(all(a < b for a, b in zip(sizes, sizes[1:])))

    def test_phase_is_bounded_and_pause_is_exact(self):
        for t in (0, 1, 25, 360000):
            self.assertTrue(0 <= flow_phase(t) < 4096)
            self.assertEqual(flow_phase(t), flow_phase(t))

    def test_phase_wrap_matches_hash_period(self):
        self.assertAlmostEqual(flow_phase(1), flow_phase(1 + 4096 / 12), places=9)
        for rate in (70.3, 121.7, 250.2):
            boundary = 4096 / rate
            before = flow_phase(boundary - .001, rate)
            after = flow_phase(boundary + .001, rate)
            self.assertAlmostEqual((after-before) % 4096, .002*rate, places=8)


class B13StreamTests(unittest.TestCase):
    def test_widget_plays_the_same_grains_across_the_one_second_bell(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        import numpy as np
        from b13_stream import install, REFERENCE_HALF, REFERENCE_RADIUS

        class Widget:
            def _build_dynamic_canvas(self):
                pass
            def _draw_stream(self):
                raise AssertionError('unexpected fallback')

        install(Widget)
        def positions(duration, at):
            w = Widget()
            w._cx, w._R_inner, w._neck_y, w._lower_sand_bot = 200., REFERENCE_RADIUS, 450., 10.
            w._taper = {'t_in': REFERENCE_HALF, 'y_bot': 460.}
            w.get_mound_top_y = lambda: 10.
            w._neck_fill_time, w.duration = .25, duration
            w._transfer_timing = lambda: (.25, max(5., duration) - 1./60., .02)
            w.elapsed, w._stream_settle_age = min(at, duration), max(0., at - duration)
            w._sand_active, w.pn, w.sand_base = True, 50, (.84, .64, .38)
            w._mound_carve = SimpleNamespace(_v=[])
            w._b13_mesh = SimpleNamespace(surface_x=np.arange(10.), bowl=np.full(10, 10.), update=Mock())
            w._draw_stream()
            return w._b13_mesh.update.call_args.args[0]

        with patch.object(sys.modules[__name__], 'SAND_MATERIAL_COARSE', 1.8, create=True):
            for t in (.55, 1., 1.40, 2.5):
                short, long = positions(1., t), positions(20., t)
                self.assertGreater(len(short.serial), 300)
                np.testing.assert_array_equal(short.serial, long.serial)
                np.testing.assert_array_equal(short.depth, long.depth)

    def test_exit_and_landing_speeds_follow_distance_on_every_timer(self):
        import numpy as np
        from b13_stream import REFERENCE_HALF, sample_grains
        from grain_style import b13_depth, b13_flight

        # A tablet pixel is about 0.45 reference pixels in the user's recording.
        # Follow a grain near the wall at the lip, 10 and 40 tablet pixels below,
        # then halfway down and just above the empty floor.
        for distance, bounds in ((0., (16., 24.)), (4.5, (18., 29.)),
                                 (18., (32., 49.)), (216., (160., 212.)),
                                 (427., (190., 232.))):
            age = b13_flight(distance, 20.)
            speed = (b13_depth(age + .001, 20.) - b13_depth(age, 20.)) / .001
            with self.subTest(distance=distance):
                self.assertTrue(bounds[0] < speed < bounds[1], speed)
        # A short burst can already be entirely airborne at the bell. Its grains
        # must still travel at the same speed as the long timer's surviving IDs.
        for age in (.12, .55, 1.35, 2.8):
            short = sample_grains(age, .75, REFERENCE_HALF, 433.)
            long = sample_grains(age, 19.75, REFERENCE_HALF, 433.)
            ids, i, j = np.intersect1d(short.serial, long.serial, return_indices=True)
            early = ids < 800
            self.assertGreater(np.sum(early), 50)
            np.testing.assert_allclose(short.depth[i[early]], long.depth[j[early]])

    def test_native_disc_coverage_matches_numpy_at_native_size(self):
        import ctypes
        import os
        import shutil
        import subprocess
        import tempfile
        import numpy as np
        from unittest.mock import patch
        from b13_raster import GrainImage, _configure_accumulator
        from b13_stream import REFERENCE_HALF, sample_grains

        source = Path(__file__).resolve().parents[1] / "build/recipes/b13raster/raster.c"
        compiler = shutil.which("cl" if os.name == "nt" else "cc")
        if compiler is None:
            self.skipTest("A host C compiler is needed for the native comparison")
        with tempfile.TemporaryDirectory() as tmp:
            lib = Path(tmp) / ("raster.dll" if os.name == "nt" else "raster.so")
            command = ([compiler, "/nologo", "/O2", "/LD", str(source), "/link", "/OUT:" + str(lib)]
                       if os.name == "nt" else
                       [compiler, "-O3", "-shared", "-fPIC", str(source), "-o", str(lib), "-lm"])
            subprocess.run(command, check=True, cwd=tmp, capture_output=True)
            library = ctypes.CDLL(str(lib))
            native = _configure_accumulator(library)
            origin, size, jet = (7., 14.), (176, 1021), (94., 1002.3, 2.35, REFERENCE_HALF)
            with patch("b13_raster._load_accumulator", return_value=None):
                reference = GrainImage(origin, size, jet)
            with patch("b13_raster._load_accumulator", return_value=native):
                actual = GrainImage(origin, size, jet)
            surface = 220. + .21 * np.arange(size[0]+1)
            for t, pitch in ((.02, .6), (2., 1.), (4.95, 2.), (10., 1.)):
                grains = sample_grains(t, 5., REFERENCE_HALF, 350., pitch)
                for image in (reference, actual):
                    image.paint(grains, surface, (.72,.25,.34), 326.)
                self.assertLessEqual(np.abs(actual.rgba.astype(int)-reference.rgba).max(), 1)
            if os.name == "nt":
                import _ctypes
                _ctypes.FreeLibrary(library._handle)

    def test_short_fall_keeps_slow_launch_and_limits_lower_grain_speed(self):
        import numpy as np
        from b13_stream import REFERENCE_HALF, sample_grains
        def speed(time):
            a = sample_grains(time, 20., REFERENCE_HALF, 600.)
            b = sample_grains(time + .01, 20., REFERENCE_HALF, 600.)
            serial, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            return np.median((b.depth[j] - a.depth[i])[serial < 140] / .01)
        self.assertTrue(20. < speed(.045) < 45.)
        # In 3.34 this same cohort was already moving at 250–310 reference
        # pixels/s here. The new ramp is still well below its cruising speed.
        self.assertTrue(100. < speed(1.6) < 150.)

    def test_y700_first_batch_uses_only_an_image_on_the_main_canvas(self):
        from unittest.mock import Mock, patch
        from types import SimpleNamespace
        import numpy as np
        import b13_stream as b13

        widget = SimpleNamespace(_cx=100., _neck_y=140., _R_inner=b13.REFERENCE_RADIUS,
                                 _lower_y_c=220., _lower_sand_bot=10.,
                                 _taper={'t_in': b13.REFERENCE_HALF, 'y_bot': 150.})
        seen = []
        class Texture:
            def add_reload_observer(self, reload):
                self.reload = reload
            def blit_buffer(self, pixels, **kwargs):
                seen.append(np.frombuffer(pixels, dtype=np.uint8).copy())

        graphics = Mock()
        graphics.InstructionGroup.return_value = Mock()
        texture = Texture()
        factory = Mock()
        factory.Texture.create.return_value = texture
        graphics.Fbo.side_effect = graphics.Mesh.side_effect = AssertionError('particle GPU draw')
        modules = {'kivy.graphics': graphics, 'kivy.graphics.texture': factory}
        with patch.dict(sys.modules, modules):
            stream_class = b13.stream_for_renderer(b'Adreno (TM) 840')
            self.assertEqual(stream_class.__name__, 'TextureStream')
            self.assertIs(b13.stream_for_renderer(b'Adreno(TM)840'), stream_class)
            self.assertIs(b13.stream_for_renderer(b'Adreno (TM) 740'), b13.StreamMesh)
            stream = stream_class(widget)
            self.assertFalse(np.any(seen[-1]))
            grains = b13.Grains(*(v[:1240] for v in b13.sample_grains(.4, 50., b13.REFERENCE_HALF, 120.)))
            surface = np.full_like(stream.surface_x, 25.)
            stream.update(grains, surface, (.84, .64, .38), 105.)
            self.assertEqual(stream.source_count, 1240)
            active = seen[-1].copy()
            self.assertGreater(np.count_nonzero(active[3::4]), 0)
            texture.reload(texture)
            np.testing.assert_array_equal(seen[-1], active)
            stream.clear()
            self.assertEqual(stream.source_count, 0)
            self.assertFalse(np.any(seen[-1]))
            texture.reload(texture)
            self.assertFalse(np.any(seen[-1]))
            graphics.Rectangle.assert_called_once()
            graphics.Fbo.assert_not_called()
            graphics.Mesh.assert_not_called()
            graphics.RenderContext.assert_not_called()

    def test_b13_image_overlaps_coloured_discs_and_clips_at_lip_and_pile(self):
        import numpy as np
        from b13_raster import GrainImage
        from b13_stream import Grains

        image = GrainImage((0, 0), (15, 18), (7.5, 15.5, 1., 4.))
        pigment = (.8, .6, .4)
        floor = np.full(16, 4.)
        def draw(x, depth, radius, tones):
            data = Grains(np.arange(len(x)), *(np.array(v) for v in (x, depth, radius, tones)))
            image.paint(data, floor, pigment, 12.)

        draw([0., 0.], [8., 8.], [.5, .5], [1., -1.])
        t = (8. - 4.) / 39.
        extinction = 1.65 * (2. - t**2 * (3. - 2. * t))
        expected_alpha = round(255. * (1. - np.exp(-extinction * 2. * .86)))
        np.testing.assert_array_equal(image.rgba[7, 7], (204, 153, 102, expected_alpha))
        self.assertEqual(np.count_nonzero(image.rgba[:, :, 3]), 1)
        draw([5., 0., 1.], [-1., 13., 6.], [.5, .5, .5], [-1., 0., 1.])
        self.assertEqual(image.rgba[16, 12, 3], 0)  # outside the neck, above the outlet
        self.assertEqual(image.rgba[2, 7, 3], 0)    # below the mound
        self.assertEqual(image.rgba[7, 7, 3], 0)    # no trail from the previous frame
        np.testing.assert_array_equal(image.rgba[9, 8, :3], (222, 173, 119))
        self.assertGreater(image.rgba[9, 8, 3], 0)

    def test_first_upload_never_advertises_indices_without_vertices(self):
        import numpy as np
        from types import SimpleNamespace
        from unittest.mock import patch
        from b13_stream import StreamMesh, sample_grains, REFERENCE_HALF

        stream = object.__new__(StreamMesh)
        stream.chunk = 4096
        data = np.zeros((stream.chunk, 4, 7), dtype=np.float32)
        mesh = SimpleNamespace(indices=[], vertices=[])
        stream.indices = (np.arange(stream.chunk, dtype=np.uint16)[:, None] * 4
                          + [0, 1, 2, 0, 2, 3]).astype(np.uint16).ravel()
        stream.batches = [(mesh, data)]
        grains = sample_grains(.26, 20., REFERENCE_HALF, 430.)
        xs, ys = np.array([0., 240.]), np.array([0., 0.])
        stream.jet, stream.surface_x, stream.context = (120., 400., 1., REFERENCE_HALF), xs, {}
        with patch('b13_stream.np.interp', side_effect=MemoryError('first vertex upload')):
            with self.assertRaises(MemoryError):
                stream.update(grains, ys, (.84, .64, .38), 400.)
        self.assertEqual(len(mesh.indices), 0)
        stream.update(grains, ys, (.84, .64, .38), 400.)
        self.assertEqual(len(mesh.indices), 6 * len(grains.x))
        self.assertLess(max(mesh.indices), len(mesh.vertices) // 7)
        second = SimpleNamespace(indices=[], vertices=[])
        stream.batches.append((second, np.zeros_like(data)))
        more = sample_grains(.65, 20., REFERENCE_HALF, 430.)
        interpolate = np.interp
        def fail_next_batch(x, xp, fp):
            if x.shape[0] < stream.chunk:
                raise MemoryError('second vertex upload')
            return interpolate(x, xp, fp)
        with patch('b13_stream.np.interp', side_effect=fail_next_batch):
            with self.assertRaises(MemoryError):
                stream.update(more, ys, (.84, .64, .38), 400.)
        self.assertEqual(len(mesh.indices), 6 * stream.chunk)
        self.assertEqual(len(second.indices), 0)
        stream.update(more, ys, (.84, .64, .38), 400.)
        self.assertEqual(len(second.indices), 6 * (len(more.x) - stream.chunk))

    def test_draw_failure_keeps_the_timer_and_detaches_the_b13_pass(self):
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from b13_stream import install, REFERENCE_RADIUS

        class Widget:
            def _build_dynamic_canvas(self):
                pass

            def _draw_stream(self):
                self.drawn_with_lines.append(self.elapsed)

        for successful_frames in (0, 3):
            with self.subTest(successful_frames=successful_frames):
                class TestWidget(Widget):
                    pass
                widget = TestWidget()
                install(TestWidget)
                widget._cx, widget._R_inner, widget._neck_y = 200., REFERENCE_RADIUS, 450.
                widget._taper = {'t_in': 30., 'y_bot': 460.}
                widget._lower_sand_bot = 10.
                widget.get_mound_top_y = lambda: 10.
                widget._particle_motion_scale, widget._neck_fill_time = 1., .25
                widget.elapsed, widget.duration, widget._stream_settle_age = .6, 20., 0.
                widget._transfer_timing = lambda: (.25, 20. - 1./60., .02)
                widget.pn, widget._sand_active = 987, True
                widget.sand_base = (.84, .64, .38)
                widget._mound_carve = SimpleNamespace(_v=[])
                widget._reserve_stream_lines, widget.drawn_with_lines = Mock(), []
                context = {}
                import numpy as np
                stream = SimpleNamespace(context=context, surface_x=np.arange(10.),
                    bowl=np.ones(10), update=Mock(side_effect=[None] * successful_frames
                                                  + [RuntimeError('mesh upload failed')]))
                widget._b13_mesh, widget._short_flow_context = stream, context
                widget.canvas = [context]
                with patch.object(sys.modules[__name__], 'SAND_MATERIAL_COARSE', 1.8, create=True):
                    for remaining in range(successful_frames, 0, -1):
                        widget.elapsed = .6 - remaining * .1
                        widget._draw_stream()
                        self.assertGreater(widget._short_flow_state['rendered_particles'], 0)
                    widget.elapsed = .6
                    with self.assertLogs('b13_stream', level='ERROR') as diagnostic:
                        widget._draw_stream()
                    self.assertIn('0.600', diagnostic.output[0])
                    self.assertIn('mesh upload failed', diagnostic.output[0])
                    widget.elapsed += .1
                    widget._draw_stream()
                self.assertEqual(widget.drawn_with_lines, [.6, .7])
                self.assertEqual(widget.pn, 987)
                self.assertEqual(widget.flow_renderer, 'line_pool')
                self.assertNotIn(context, widget.canvas)
                self.assertIsNone(widget._b13_mesh)
                widget._reserve_stream_lines.assert_called_once()
                self.assertEqual(stream.update.call_count, successful_frames + 1)

    def test_same_grains_fall_across_a_birth_block_and_a_long_timer(self):
        import numpy as np
        from b13_stream import REFERENCE_HALF, sample_grains
        for half, time in ((REFERENCE_HALF, 5.), (5., 360000.)):
            a = sample_grains(time - .001, time + 5., half, 400.)
            b = sample_grains(time + .001, time + 5., half, 400.)
            ids, i, j = np.intersect1d(a.serial, b.serial, return_indices=True)
            self.assertGreater(len(ids), .97 * len(a.serial))
            self.assertTrue(np.all(b.depth[j] > a.depth[i]))
            self.assertTrue(np.array_equal(a.radius[i], b.radius[j]))
            self.assertTrue(np.array_equal(a.tone[i], b.tone[j]))
            paused = sample_grains(time + .001, time + 5., half, 400.)
            self.assertTrue(np.array_equal(paused.x, b.x))

    def test_short_glass_front_accelerates_gently_and_tail_clears(self):
        import numpy as np
        from b13_stream import REFERENCE_HALF, sample_grains
        front = sample_grains(1.33, 5., REFERENCE_HALF, 433.)
        # The leading grains have travelled roughly one mouth width in 1.33 s.
        self.assertTrue(REFERENCE_HALF * 2. < front.depth.max() < REFERENCE_HALF * 2.6)
        # Follow the narrowing rear across the gap, then require it to leave
        # that band. A steady full-width column or an early cut fails this.
        for t in (5.35, 5.36, 5.37):
            tail = sample_grains(t, 5., REFERENCE_HALF, 15.)
            mid = tail.x[np.abs(tail.depth - 7.) < 1.]
            self.assertTrue(len(mid) >= 2 and np.ptp(mid) < 16.)
        tail = sample_grains(5.45, 5., REFERENCE_HALF, 15.)
        self.assertGreater(tail.depth.min(), 8.)
        settled = sample_grains(5.65, 5., REFERENCE_HALF, 15.)
        self.assertEqual(len(settled.serial), 0)

    def test_duration_scales_supply_with_aperture(self):
        from b13_stream import REFERENCE_HALF, sample_grains
        wide = sample_grains(3., 50., REFERENCE_HALF, 300.)
        narrow = sample_grains(3., 6000., REFERENCE_HALF / 5., 300.)
        self.assertAlmostEqual(len(narrow.serial) / len(wide.serial), .2, delta=.012)


if __name__ == "__main__":
    unittest.main()
