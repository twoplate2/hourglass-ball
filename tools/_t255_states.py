# -*- coding: utf-8 -*-
"""2026-10-11 测试员: 暂停/重置/重启 的状态帧序列(轻量)。

跑法:
    python tools/_t255_states.py --label t255_states --pixels 1080,1920 --period 50
输出: benchmark_logs/flow_visual_<label>/state-*.png
"""
import argparse
import importlib.util
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="t255_states")
    ap.add_argument("--pixels", default="1080,1920")
    ap.add_argument("--period", type=float, default=50.0)
    args = ap.parse_args()
    output = ROOT / "benchmark_logs" / ("flow_visual_" + args.label)
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="t255-states-") as home:
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
        import main

        module = main
        module.HourglassWidget._make_sound_proxy = lambda *_: None
        module.HourglassWidget._make_completion_sound = lambda *_: None
        module.HourglassApp.on_completed = lambda *_: None
        module.HourglassWidget.load_config = lambda *_: {"duration": 60}
        module.HourglassWidget.save_config = lambda *_: None
        now = [1000.0]
        module.time = SimpleNamespace(perf_counter=lambda: now[0])
        import random

        class VisualApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.resize, 1)

            def resize(self, _dt):
                width, height = map(int, args.pixels.split(","))
                self._want = (width, height)
                ratio = Window.width / Window.system_size[0]
                Window.system_size = (round(width / ratio), round(height / ratio))
                self._prev = None
                self._stable = 0
                Clock.schedule_interval(self._settle, 0.1)

            def _settle(self, _dt):
                w = getattr(self, "hourglass", None)
                if w is None:
                    return
                key = (round(Window.width), round(Window.height),
                       round(getattr(w, "_R_inner", 0.0), 3))
                if key == self._prev:
                    self._stable += 1
                else:
                    self._stable, self._prev = 0, key
                if self._stable >= 3 and round(Window.width) == self._want[0]:
                    Clock.unschedule(self._settle)
                    print("geom stable: Window=%dx%d 2R_inner=%.1f"
                          % (round(Window.width), round(Window.height),
                             2 * getattr(w, "_R_inner", 0.0)))
                    Clock.schedule_once(self.begin, 0.2)

            def begin(self, _dt):
                self.root.apply_orientation()
                self.root.do_layout()
                self.root._anchor.do_layout()
                self.hourglass.parent.do_layout()
                Clock.unschedule(self.hourglass.tick)
                self.real_draw = self.hourglass.redraw
                self.steps = [
                    ("A-run7.0", 7.0),
                    ("B-pause", None),
                    ("C-resume", 7.05),
                    ("D-reset", None),
                    ("E-run0.5", 0.5),
                    ("F-run7.0-again", 7.0),
                ]
                self._resumed = False
                self.next(0)

            def advance(self, target):
                w = self.hourglass
                while w.elapsed + 1e-8 < target and w.running:
                    step = min(1 / 120, target - w.elapsed)
                    now[0] += step
                    w.tick(step)

            def snap(self, name):
                w = self.hourglass
                w.redraw = lambda: None
                Clock.idle()
                w.redraw = self.real_draw
                self.root.do_layout()
                w.redraw()
                Clock.schedule_once(lambda _dt, n=name: self.capture(n), 0.05)

            def capture(self, name):
                width, height = map(int, Window.size)
                pixels = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                image = Image.frombytes("RGBA", (width, height), pixels)
                image = image.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
                image.save(output / ("state-%s.png" % name))
                Clock.schedule_once(self.next, 0.1)

            def next(self, _dt):
                w = self.hourglass
                if w.duration != args.period:
                    w.set_duration(args.period)
                    w.completion_enabled = False
                    for _ in range(400):
                        _b = len(getattr(w, "_warm_queue", ()))
                        w._warm_batches_step()
                        if len(getattr(w, "_warm_queue", ())) >= _b:
                            break
                if not self.steps:
                    self.stop()
                    return
                name, tgt = self.steps.pop(0)
                if name.startswith("A") and not w.running:
                    random.seed(23)
                    w.toggle()
                if name.startswith("B"):
                    w.running = False          # 暂停(与 App 的 pause 同义)
                if name.startswith("C"):
                    w.running = True
                if name.startswith("D"):
                    w.reset()
                if name.startswith("E"):
                    random.seed(23)
                    w.toggle()
                if tgt is not None:
                    self.advance(tgt)
                print("[state] %-14s elapsed=%.3f running=%s particles=%d"
                      % (name, w.elapsed, w.running, getattr(w, "pn", -1)))
                self.snap(name)

        VisualApp().run()
        print("states:", output)


if __name__ == "__main__":
    main()
