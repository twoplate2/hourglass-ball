"""Focused PC check for contact flow, volume inversion and outward splashes."""

import math
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "contact_flow"


def run():
    with tempfile.TemporaryDirectory(prefix="contact-flow-") as home:
        os.environ.update(KIVY_HOME=home, KIVY_NO_ARGS="1", KIVY_NO_FILELOG="1")
        sys.path.insert(0, str(ROOT))
        import main as m
        import numpy as np
        from kivy.clock import Clock
        from kivy.core.window import Window
        from PIL import Image

        m.HourglassWidget._make_sound_proxy = lambda *_: None
        m.HourglassWidget._make_completion_sound = lambda *_: None
        m.HourglassWidget._play_completion_sound = lambda *_: None
        m.HourglassWidget.load_config = lambda *_: {"duration": 50}
        m.HourglassWidget.save_config = lambda *_: None
        m.HourglassApp.on_completed = lambda *_: None
        now = [1000.0]
        m.time = SimpleNamespace(perf_counter=lambda: now[0])
        OUT.mkdir(parents=True, exist_ok=True)

        class Probe(m.HourglassApp):
            def on_start(self):
                Clock.unschedule(self.hourglass.tick)
                Window.system_size = (480, 900)
                Clock.schedule_once(self.check, 0.5)

            def advance(self, seconds):
                for _ in range(round(seconds * 60)):
                    now[0] += 1 / 60
                    self.hourglass.tick(1 / 60)

            def capture(self, name):
                path = OUT / (name + ".png")
                self.hourglass.export_to_png(str(path))
                with Image.open(path) as image:
                    return np.asarray(image.convert("RGB")).copy()

            def check(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(50)
                w._rebuild_height_table()
                w.reset()
                assert w._geom_ready
                flow = w._mound_surface_flow
                assert flow is not None and flow.shader.success, "GPU path did not compile"
                random.seed(23)
                w.toggle()
                self.advance(0.1)
                assert w._last_impact_clock is None and w._sn == 0
                assert w._mound_flow_strength() == 0
                for target in (6.0, 25.0):
                    self.advance(target - w.elapsed)
                    assert abs(w.elapsed - target) < 0.02
                    assert flow._node_count > 2 and w._mound_flow_strength() > 0
                    profile = w._mound_profile
                    for fraction in (0.01, 0.1, 0.5, 0.9, 0.99, 1.0):
                        apex = profile.apex_for_fraction(fraction)
                        actual = profile.heap.area_at(apex) / profile.heap.capacity
                        assert abs(actual - fraction) < 1e-9
                    vertices = flow._vertices
                    left = right = 0
                    for k in range(flow._node_count):
                        x, top, _, _, vx, vy, coverage = vertices[k * 14:k * 14 + 7]
                        bottom = vertices[k * 14 + 8]
                        floor = w._lower_sand_bot + profile.bounds(x - w._cx)[0]
                        assert floor - 1e-6 <= bottom <= top + 1e-6
                        assert abs(top - w._mound_top_at(x)) < 1e-6
                        if coverage:
                            assert vx * (x - w._cx) >= 0
                            left += vx < 0
                            right += vx > 0
                    assert left and right
                    self.capture("elapsed_%d" % target)
                # Change only the surface shader clock, not the physics or other layers.
                first = self.capture("flow_a")
                clock = flow["sand_clock"]
                flow["sand_clock"] = clock + 0.2
                second = self.capture("flow_b")
                flow["sand_clock"] = clock
                pixels = int(np.count_nonzero(np.any(first != second, axis=2)))
                assert pixels > 10, "surface flow has no visible pixel change"
                w.toggle()
                phase = flow["sand_clock"]
                strength = w._mound_flow_strength()
                self.advance(0.3)
                assert flow["sand_clock"] == phase and w._mound_flow_strength() == strength
                w.toggle()
                saved = w.splashes
                w.splashes = []
                slides = flights = 0
                for side in (-1.0, 1.0):
                    x = w._cx + side * w._contact_flow_diameter()
                    y = w._mound_top_at(x)
                    step = max(1.0, w._R_inner / 256.0)
                    slope = (w._mound_top_at(x + step) - w._mound_top_at(x - step)) / (2 * step)
                    norm = math.hypot(1, slope)
                    for _ in range(100):
                        i = w._eject_splash(x, y, 400, surface_aligned=True)
                        assert i >= 0
                        vx, vy = w.svx[i], w.svy[i]
                        assert side * vx > 0
                        if w.sslide[i]:
                            slides += 1
                        else:
                            flights += 1
                            tangent = side * (vx + slope * vy) / norm
                            normal = (-slope * vx + vy) / norm
                            assert tangent > 0 and normal >= 0
                            assert math.atan2(normal, tangent) <= math.radians(12) + 1e-9
                assert 0.6 <= slides / (slides + flights) <= 0.9
                w.splashes = saved
                self.advance(49.0 - w.elapsed)
                self.capture("elapsed_49")
                # A roof-clipped contact region has no exposed flowing surface.
                reach = 2.5 * w._contact_flow_diameter()
                if not any(w._mound_profile.free_surface(
                        reach * (k / 8 - 1), w._mound_apex()) for k in range(17)):
                    assert flow._node_count == 0
                self.advance(50.5 - w.elapsed)
                assert not w.running and w.pn == 0
                assert w._mound_flow_strength() == 0 and flow._node_count == 0
                w.reset()
                w.redraw()
                assert w._sn == 0 and w._last_impact_clock is None
                assert flow._node_count == 0
                print("PASS contact flow: GPU pixels=%d, slides=%d, flights=%d; "
                      "volume, pause, reset and tail checked" % (pixels, slides, flights))
                self.stop()

        Probe().run()


if __name__ == "__main__":
    run()
