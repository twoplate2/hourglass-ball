"""PC visual clips and focused checks for collision-driven grain splashes."""

import json
import os
from pathlib import Path
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "contact_flow_2_3"


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
                    self.peak = max(self.peak, self.hourglass._sn)

            def shot(self, name):
                path = OUT / (name + ".png")
                self.hourglass.export_to_png(str(path))
                with Image.open(path) as image:
                    return image.convert("RGB").copy()

            def clip(self, name, frames=None, seconds=1.2):
                frames = [] if frames is None else frames
                for k in range(round(seconds * 10)):
                    self.advance(0.1)
                    frames.append(self.shot(name + "_%02d" % k))
                frames[0].save(OUT / (name + ".gif"), save_all=True,
                               append_images=frames[1:], duration=100, loop=0)

            def check(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                w = self.hourglass
                w.set_duration(50)
                w._rebuild_height_table()
                w.reset()
                self.peak = 0
                random.seed(23)
                w.toggle()
                self.advance(0.1)
                assert w._last_impact_clock is None and w._sn == 0
                before = []
                while w._last_impact_clock is None and w.elapsed < 3.0:
                    self.advance(0.1)
                    before.append(self.shot("before_hit"))
                assert w._last_impact_clock is not None
                self.clip("first_contact", before[-2:])
                self.advance(8.0 - w.elapsed)
                live = w._sn
                airborne = int(np.count_nonzero(
                    (w.sslide[:live] == 0.0) & (w.shas[:live] == 0.0)))
                aloft = int(sum(w.sy[i] - w._mound_top_at(w.sx[i]) > 2.0 for i in range(live)))
                normal = self.shot("elapsed_8")
                w._sn = 0
                w.redraw()
                hidden = self.shot("no_splashes")
                w._sn = live
                w.redraw()
                difference = np.max(np.abs(np.asarray(normal).astype(int)
                                            - np.asarray(hidden).astype(int)), axis=2)
                visible = int(np.count_nonzero(difference >= 5))
                assert airborne > 40 and aloft > 40, "not enough readable airborne grains"
                assert visible > 60, "splashes exist but are not visible in the rendered frame"
                random_state = random.getstate()
                saved = w.splashes
                for _ in range(10):
                    w._eject_splash(w._cx, w.get_mound_top_y(), 400)
                assert random.getstate() == random_state, "effect draws changed the main RNG"
                w.splashes = saved
                w.toggle()
                state = tuple(tuple(getattr(w, field)[:w._sn]) for field in m._S_FIELDS)
                self.advance(0.2)
                assert state == tuple(tuple(getattr(w, field)[:w._sn]) for field in m._S_FIELDS)
                w.toggle()
                self.clip("steady")
                for fraction in (0.01, 0.1, 0.5, 0.9, 0.99, 1.0):
                    profile = w._mound_profile
                    apex = profile.apex_for_fraction(fraction)
                    assert abs(profile.heap.area_at(apex) / profile.heap.capacity - fraction) < 1e-9
                self.advance(48.8 - w.elapsed)
                self.clip("finish", seconds=1.8)
                self.advance(1.6)
                assert w.pn == 0 and w._sn == 0 and not w.running
                stats = dict(w._splash_stats)
                assert self.peak <= m.SPLASH_MAX
                w.reset()
                w.redraw()
                assert w._sn == 0 and not w._contact_hits and w._last_impact_clock is None
                report = dict(version=m.APP_VERSION, live=live, airborne=airborne,
                              above_surface_2px=aloft, visible_pixels_delta5=visible,
                              peak=self.peak, drops=stats)
                (OUT / "report.json").write_text(json.dumps(report, indent=2))
                print("PASS contact splashes:", report)
                self.stop()

        Probe().run()


if __name__ == "__main__":
    run()
