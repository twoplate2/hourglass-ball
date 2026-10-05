"""Reviewer-1 probe #3: dump the neck column quads (points + tex_coords) at the
same deterministic state, and dump the material texture's local statistics."""
from pathlib import Path
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="r1c-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        import main as module

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.prepare, 0.4)

            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(5.0)
                random.seed(23)
                w.toggle()
                real = w.redraw
                w.redraw = lambda: None
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = real
                w.redraw()
                tp = w._taper
                print("PROBE in_pts:")
                for p in tp["in_pts"]:
                    print("   x=%8.3f y=%9.4f" % p)
                side = w._neck_sand_side()
                print("PROBE side (len=%d):" % len(side))
                for p in side:
                    print("   x=%8.3f y=%9.4f" % p)
                print("PROBE upper_sand_bot=%.3f NECK_UV_ANCHOR=%s diameter=%.2f su=%.6f"
                      % (w._upper_sand_bot, module.NECK_UV_ANCHOR, 2 * w._R_inner,
                         1.0 / (2 * w._R_inner)))
                print("PROBE neck quads:")
                for i, q in enumerate(w._neck_quads):
                    pts = [round(v, 2) for v in q.points]
                    tc = [round(v, 5) for v in q.tex_coords]
                    if any(pts):
                        print("   q%d pts=%s" % (i, pts))
                        print("      tc=%s" % tc)
                print("PROBE solid_rect size=%s fade size=%s"
                      % (tuple(w._neck_solid_rect.size), tuple(w._neck_fade_rect.size)))
                # material texture local stats at the u range the funnel samples
                mat = w._sand_material
                if mat is not None:
                    import numpy as np
                    rgba = np.frombuffer(mat.rgba, dtype=np.uint8).reshape(512, 512, 4)
                    su = 1.0 / (2 * w._R_inner)
                    u0, u1 = 0.5 - 43.62 * su, 0.5 + 43.62 * su
                    vb = module.NECK_UV_ANCHOR + (w._upper_sand_bot - 410.15) * su
                    vt = module.NECK_UV_ANCHOR + (w._upper_sand_bot - 391.3) * su
                    print("PROBE material sample window u[%.3f,%.3f] v[%.3f,%.3f]"
                          % (u0, u1, vb, vt))
                    x0, x1 = int(u0 * 512), int(u1 * 512)
                    y0, y1 = int(min(vb, vt) * 512) - 4, int(max(vb, vt) * 512) + 4
                    y0 = max(0, y0)
                    win = rgba[y0:y1, x0:x1, 0].astype(float)
                    print("PROBE window shape=%s R mean %.1f std %.1f" % (win.shape, win.mean(), win.std()))
                    # vertical vs horizontal structure in the window
                    print("PROBE window row-to-row mean |diff| %.2f  col-to-col mean |diff| %.2f"
                          % (np.abs(np.diff(win.mean(axis=1))).mean(),
                             np.abs(np.diff(win.mean(axis=0))).mean()))
                self.stop()

        ProbeApp().run()


main()
