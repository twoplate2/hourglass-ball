"""Ablation: which neck layer makes the column read (225,176,112) instead of the
material's own (215,161,95)?

`redraw()` re-writes every quad's points and both rects' size each frame, so an
ablation that runs BEFORE it gets overwritten. Correct order: redraw() normally,
then break one layer, then capture.

Capture timing: glReadPixels returns the CURRENT framebuffer and Kivy paints on
the next on_draw, so each state change needs a frame gap before the grab.
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]


def main():
    out = ROOT / "benchmark_logs" / "flow_visual_neck_ablate"
    out.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="neck-ablate-") as home:
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
        import random
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
                self.steps = ["all", "no_particles", "no_splash", "no_flares",
                              "no_glass_hl", "everything_off"]
                self.saved = {}
                self.next_step(0)

            def grab(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB").save(out / name)

            @staticmethod
            def ablate(w, step):
                if step in ("no_particles", "everything_off"):
                    for _g, _c, pool in w._stream_pools.values():
                        for line in pool:
                            line.points = []
                if step in ("no_splash", "everything_off"):
                    for rect in w._splash_rects:      # _sync_rects stores bare Rects
                        rect.size = (0, 0)
                if step in ("no_flares", "everything_off"):
                    for _c, rect in w._flare_rects:
                        rect.size = (0, 0)
                if step in ("no_glass_hl", "everything_off"):
                    w._glass_hl_group.clear()
                if step == "everything_off":
                    w._hide_neck_grains()
                    for q in w._neck_quads:
                        q.points = [0] * 8
                    w._neck_solid_rect.size = (0, 0)
                    w._neck_fade_rect.size = (0, 0)

            def next_step(self, _dt):
                if not self.steps:
                    for k, v in self.saved.items():
                        print("PROBE %-18s 颈柱带 x196..205 y430..450 中位 (%d,%d,%d)"
                              % (k, *v))
                    print("PROBE 参照: 材质本身 (215,161,95) | 上/下球 (215,161,94)")
                    w = self.hourglass
                    print("PROBE 色表 len=%d" % len(w._color_table))
                    for i, c in enumerate(w._color_table):
                        print("   [%2d] (%3d,%3d,%3d)"
                              % (i, *[round(v * 255) for v in c]))
                    print("PROBE sand_base=%s sand_light=%s"
                          % (tuple(round(v * 255) for v in w.sand_base),
                             tuple(round(v * 255) for v in w.sand_light)))
                    counts = {}
                    for (idx, size), n in w._stream_counts.items():
                        if n:
                            counts["idx=%s size=%d" % ("LIGHT" if idx < 0 else idx, size)] = n
                    print("PROBE 活动图元桶: %s" % (counts,))
                    print("PROBE 色表最亮档[10]=%s  高光组色=%s"
                          % (tuple(round(v * 255) for v in w._color_table[10]),
                             tuple(round(v * 255) for v in w.sand_light)))
                    self.stop()
                    return
                step = self.steps.pop(0)
                w = self.hourglass
                w.redraw()                 # normal frame first ...
                self.ablate(w, step)       # ... THEN break one layer
                Clock.schedule_once(lambda _dt, s=step: self.take(s), 0.10)

            def take(self, step):
                self.grab("abl_%s.png" % step)
                img = np.asarray(Image.open(out / ("abl_%s.png" % step)).convert("RGB")).astype(int)
                blk = img[430:451, 196:205].reshape(-1, 3)
                self.saved[step] = tuple(np.median(blk, axis=0).astype(int))
                Clock.schedule_once(self.next_step, 0.10)

        ProbeApp().run()


main()
