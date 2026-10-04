"""Probe: how much contrast do the neck grains actually add? (meishu2.md sec 8.1)

Diagnosis from reading the code: the neck column samples the material at
u in [0.452, 0.548], v in [0, 0.11], where `broad` is a near-constant -0.045 --
so the neck base colour is essentially pure +/-0.35 grain noise, while the
ordinary grains are painted at base + [0, 0.289]*(light-base). The two ranges
overlap almost completely, so the grains should be invisible except for the
~10% "light" group (0.85).

This measures it instead of asserting it: render the same frame with the grain
layer disabled and with it enabled, then compare
  (a) the pixel delta the grain layer introduces, and
  (b) the material's own local contrast in that same region.
If (a) is not clearly above (b), the layer is being swamped.
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "benchmark_logs" / "flow_visual_neck_contrast"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neck-contrast-") as home:
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
        import random

        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])
        geometry = {}

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Window.size = (400, 800)
                Clock.schedule_once(self.prepare, 0.4)

            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                # Turn the completion popup off before it can cover the captures.
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
                geometry.update(
                    cx=w._cx, neck_y=w._neck_y, t_in=w._taper["t_in"],
                    y_bot=w._taper["y_bot"],
                    outlet=2 * w._neck_y - w._taper["y_bot"],
                    height=w.height, width=w.width)
                self.visible_grains = w._neck_grain_count

                def _q(vals):
                    vals = sorted(vals)
                    if not vals:
                        return None
                    return [round(vals[0], 1), round(vals[len(vals) // 2], 1),
                            round(vals[-1], 1)]

                # NOTE: an earlier version of this probe took sorted(...)[:6] and
                # I read it as "the splashes only bounce 0.4-4.3 px". That slice is
                # the SMALLEST six, not the typical ones -- always report min/median/max.
                self.splash_stats = {
                    "count": len(w.splashes),
                    "sizes": sorted({s["size"] for s in w.splashes}),
                    "vy_up min/med/max": _q([s["vy"] for s in w.splashes]),
                    "rise_px min/med/max": _q([s["vy"] ** 2 / 900.0 for s in w.splashes]),
                    "vx min/med/max": _q([abs(s["vx"]) for s in w.splashes]),
                }
                # Four shots: the two probes must be independent, so restore the
                # grain layer before disabling the splashes.
                self.shots = [("neck", "with"), ("neck", "without"),
                              ("splash", "with"), ("splash", "without")]
                self.next_shot(0)

            def grab(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(
                    out / name)

            def next_shot(self, _dt):
                if not self.shots:
                    print("PROBE grains_visible=%d" % self.visible_grains)
                    print("PROBE splash %r" % (self.splash_stats,))
                    print("PROBE geometry %r" % (geometry,))
                    self.stop()
                    return
                kind, tag = self.shots.pop(0)
                w = self.hourglass
                if kind == "neck":
                    # "with" restores the real renderer; "without" stubs it out.
                    if tag == "with":
                        w.__dict__.pop("_draw_neck_grains", None)
                    else:
                        w._draw_neck_grains = lambda side: w._hide_neck_grains()
                        w._hide_neck_grains()
                else:
                    # The two probes must not overlap: un-stub the grain layer so
                    # the splash pair differs ONLY in the splash layer.
                    w.__dict__.pop("_draw_neck_grains", None)
                    # Clearing the list is enough: _sync_rects collapses every
                    # pooled rect to (0, 0) on the next redraw.
                    if tag == "without":
                        self.kept_splashes = list(w.splashes)
                        w.splashes = []
                w.redraw()
                # glReadPixels sees the CURRENT framebuffer; Kivy paints on the
                # next on_draw. Without this gap the capture is the previous frame.
                Clock.schedule_once(lambda _dt: self.take(kind, tag), 0.08)

            def take(self, kind, tag):
                self.grab("%s_%s.png" % (kind, tag))
                if kind == "splash" and tag == "without":
                    self.hourglass.splashes = self.kept_splashes
                Clock.schedule_once(self.next_shot, 0.08)

        ProbeApp().run()


main()
