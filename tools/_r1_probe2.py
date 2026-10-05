"""Reviewer-1 probe #2: neck-column ablation + material-off ablation (paired).

Base frame: identical deterministic state to _r1_probe.py (5 s, t=2.5 s, seed 23).
Shots: base, nocolumn (hide the neck sand column), nomaterial (flat sand colour),
nosurface2 (surface carve/band quads zeroed).
"""
from pathlib import Path
import os
import random
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_r1"


def main():
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r1b-") as home:
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
                self.shots = ["base2", "nocolumn", "nosurface2"]
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
                w.__dict__.pop("_neck_sand_side", None)
                w.__dict__.pop("_draw_surface_shape", None)
                if name == "nocolumn":
                    w._neck_sand_side = lambda: []
                elif name == "nosurface2":
                    def hide_surface(uh, peak, _w=w):
                        for quad in _w._surface_carve:
                            quad.points = [0] * 8
                        for quad in _w._surface_band:
                            quad.points = [0] * 8
                    w._draw_surface_shape = hide_surface
                w.redraw()
                Clock.schedule_once(lambda _dt: self.grab(name), 0.08)
                Clock.schedule_once(self.next_shot, 0.16)

        ProbeApp().run()


main()
