"""Reviewer-2 Kivy probe:
 1) GL_SAMPLES / multisamples  (is the desktop frame MSAA'd?)
 2) NECK_UV_ANCHOR test: rerender the same frame with every neck-quad v >= 0
    (reviewer-1's own falsification condition for their A1 UV attribution)
 3) impact-x distribution of the main jet (flare spawns) over a full 5 s cycle
 4) material texture wrap mode
Writes to benchmark_logs/_x2/.
"""
from pathlib import Path
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_x2"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="x2-") as home:
        os.environ["KIVY_HOME"] = home
        os.environ["KIVY_NO_ARGS"] = "1"
        os.environ["KIVY_NO_FILELOG"] = "1"
        os.environ["KIVY_METRICS_DENSITY"] = "1"
        os.environ["KIVY_METRICS_FONTSCALE"] = "1"
        sys.path.insert(0, str(ROOT))
        from kivy.app import App
        from kivy.clock import Clock
        from kivy.core.window import Window
        from kivy.config import Config
        from kivy.graphics.opengl import (glReadPixels, glGetIntegerv, GL_RGBA,
                                          GL_UNSIGNED_BYTE, GL_SAMPLES,
                                          GL_SAMPLE_BUFFERS)
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
                # --- A4: collect main-jet impact x over the whole 5 s cycle
                hits = []
                real_flares = w.flares

                class LogList(list):
                    def append(self, item):
                        hits.append(item["x"])
                        super().append(item)

                w.flares = LogList()
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = self.real_draw
                self.hits_mid = list(hits)
                # keep counting to the end of the cycle
                while w.elapsed < 5.0:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                self.hits_all = list(hits)
                w.flares = real_flares
                print("PROBE multisamples config=%s  Window.multisamples=%s"
                      % (Config.get('graphics', 'multisamples'),
                         getattr(Window, 'multisamples', 'n/a')))
                print("PROBE GL_SAMPLES=%s GL_SAMPLE_BUFFERS=%s"
                      % (glGetIntegerv(GL_SAMPLES), glGetIntegerv(GL_SAMPLE_BUFFERS)))
                mat = w._sand_material
                print("PROBE material texture wrap=%s size=%s"
                      % (mat.texture.wrap if mat else None,
                         mat.texture.size if mat else None))
                cx = w._cx
                def dist(v):
                    v = sorted(abs(x - cx) for x in v)
                    if not v:
                        return "n=0"
                    return ("n=%d med=%.1f p90=%.1f p95=%.1f max=%.2f"
                            % (len(v), v[len(v) // 2], v[int(len(v) * .9)],
                               v[int(len(v) * .95)], v[-1]))
                print("PROBE impact |x-cx| up to t=2.5s: %s" % dist(self.hits_mid))
                print("PROBE impact |x-cx| full cycle: %s" % dist(self.hits_all))
                print("PROBE tube half width t_in=%.2f  chord(Ri)=%.1f"
                      % (w._taper["t_in"], w._R_inner))
                # --- back to t=2.5 for the paired shots
                w.reset()
                random.seed(23)
                w.toggle()
                w.redraw = lambda: None
                while w.elapsed < 2.5:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = self.real_draw
                self.shots = ["base", "anchor", "nograins", "anchor_nograins"]
                self.next_shot(0)

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
                w.__dict__.pop("_draw_neck_grains", None)
                module.NECK_UV_ANCHOR = 0.0
                if "nograins" in name:
                    w._draw_neck_grains = lambda side: w._hide_neck_grains()
                    w._hide_neck_grains()
                if name.startswith("anchor"):
                    module.NECK_UV_ANCHOR = 0.021
                w.redraw()
                Clock.schedule_once(lambda _dt: self.take(name), 0.08)

            def take(self, name):
                self.grab(name)
                Clock.schedule_once(self.next_shot, 0.08)

        ProbeApp().run()


main()
