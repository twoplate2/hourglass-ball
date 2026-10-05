"""Reviewer-1 probe: paired captures + geometry dump for the 8-claim audit.

Renders ONE deterministic state (5 s period, t=2.5 s), then captures:
  1. baseline full frame
  2. same frame with the neck-grain layer ablated (paired)
  3. same frame with the upper-surface shaping ablated (paired)
  4. same frame with splashes removed (paired)   [not needed for A1..A8, kept cheap]
Also dumps geometry (cx, Ri, neck_y, taper) and particle statistics that the
claims assert numbers about (trail length, x spread, colours).

Writes into benchmark_logs/_r1/.
"""
from pathlib import Path
import math
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_r1"


def main():
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r1-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.graphics.opengl import glReadPixels, GL_RGBA, GL_UNSIGNED_BYTE
        from PIL import Image
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
                self.real_draw = w.redraw
                w.redraw = lambda: None
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = self.real_draw

                self.dump(w)
                self.shots = ["base", "nograins", "nosurface", "nosplash"]
                self.next_shot(0)

            def dump(self, w):
                pv = w._pv
                n = pv.n
                print("PROBE geometry cx=%.1f Ri=%.2f R=%.2f ow=%.2f neck_y=%.2f "
                      "tube_h=%.2f y_bot=%.2f outlet=%.2f up_cut=%.2f lower_cut=%.2f "
                      "mound_top=%.2f upper_sand_bot=%.2f lower_sand_bot=%.2f"
                      % (w._cx, w._R_inner, w._R, w._ow, w._neck_y, w._tube_h,
                         w._taper["y_bot"], 2 * w._neck_y - w._taper["y_bot"],
                         w._upper_ball_cut, w._lower_ball_cut,
                         w.get_mound_top_y(), w._upper_sand_bot, w._lower_sand_bot))
                print("PROBE taper t_in=%.2f t_out=%.2f" % (w._taper["t_in"], w._taper["t_out"]))
                print("PROBE particles n=%d  neck_grains=%d" % (n, w._neck_grain_count))
                ys = sorted(pv.y[:n])
                # trail model from the renderer
                scale = w._particle_motion_scale or 1.0
                trails = []
                vxs = []
                for i in range(n):
                    vy = abs(pv.vy[i])
                    trails.append(max(2.0, vy * pv.tl[i] / scale))
                    vxs.append(abs(pv.x[i] - w._cx))
                trails.sort()
                vxs.sort()
                def q(v, f):
                    return v[min(len(v) - 1, int(f * len(v)))]
                print("PROBE trail_px min/med/p90/max = %.1f / %.1f / %.1f / %.1f"
                      % (trails[0], q(trails, .5), q(trails, .9), trails[-1]))
                print("PROBE |x-cx| med/p90/max = %.1f / %.1f / %.1f  (t_in=%.1f)"
                      % (q(vxs, .5), q(vxs, .9), vxs[-1], w._taper["t_in"]))
                # how many particles are below the outlet (i.e. in the visible jet)?
                outlet = 2 * w._neck_y - w._taper["y_bot"]
                below = [i for i in range(n) if pv.y[i] < outlet]
                print("PROBE particles below outlet: %d" % len(below))
                # light group share
                nl = sum(1 for i in range(n) if pv.light[i])
                print("PROBE light group %d/%d = %.2f" % (nl, n, nl / max(1, n)))
                # colour table used by the renderer
                print("PROBE color_table head/tail: %s ... %s"
                      % ([tuple(round(c, 3) for c in w._color_table[0])],
                         [tuple(round(c, 3) for c in w._color_table[-1])]))

            def grab(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(
                    OUT / (name + ".png"))

            def next_shot(self, _dt):
                if not self.shots:
                    print("PROBE done")
                    self.stop()
                    return
                name = self.shots.pop(0)
                w = self.hourglass
                # reset every ablation
                w.__dict__.pop("_draw_neck_grains", None)
                w.__dict__.pop("_draw_surface_shape", None)
                if name == "nograins":
                    w._draw_neck_grains = lambda side: w._hide_neck_grains()
                    w._hide_neck_grains()
                elif name == "nosurface":
                    w._draw_surface_shape = lambda uh, peak: None
                elif name == "nosplash":
                    self.kept = list(w.splashes)
                    w.splashes = []
                w.redraw()
                Clock.schedule_once(lambda _dt: self.take(name), 0.08)

            def take(self, name):
                self.grab(name)
                if name == "nosplash":
                    self.hourglass.splashes = self.kept
                Clock.schedule_once(self.next_shot, 0.08)

        ProbeApp().run()


main()
