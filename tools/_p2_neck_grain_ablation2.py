"""2 号评审 · A1 取证 v2: 颈部 grain 层的**作用范围**与观感。

关键修正: 400x800 的窗口里 `HourglassWidget.height` 只有 631(它在布局里占一块),
所以 app 坐标 -> 图像坐标要用 `w.pos` 换算, 不能写 `height - y`。这里改用**实测**:
直接从图里找窄柱(沙色行宽 < 40px 的连续行段)当"颈部", 再分区统计。
"""
from pathlib import Path
import os
import sys
import tempfile
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "benchmark_logs" / "_p2_neck_grains2"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p2-neck2-") as home:
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
        mode = os.environ.get("P2_MODE", "default")

        class ProbeApp(module.HourglassApp):
            def on_start(self):
                Clock.schedule_once(self.setup, 1.0)

            def setup(self, _dt):
                Clock.schedule_once(self.prepare, 0.4)

            def prepare(self, _dt):
                w = self.hourglass
                Clock.unschedule(w.tick)
                w.completion_enabled = False
                w.set_duration(float(os.environ.get("P2_PERIOD", "5")))
                random.seed(23)
                w.toggle()
                real = w.redraw
                w.redraw = lambda: None
                target = float(os.environ.get("P2_TARGET", "2.5"))
                while w.elapsed < target:
                    now[0] += 1 / 120
                    w.tick(1 / 120)
                w.redraw = real
                print("PROBE window %s  widget pos=%s size=%s" %
                      (Window.size, w.pos, w.size))
                self.saved = {}
                self.steps = ["on", "on2", "off"]
                self.next_step(0)

            def next_step(self, _dt):
                if not self.steps:
                    self.finish()
                    return
                step = self.steps.pop(0)
                w = self.hourglass
                w.redraw()
                print("PROBE step %-4s grain_lines=%d" % (step, w._neck_grain_count))
                if step == "on2":
                    ys = []
                    for _c, _ln in w._neck_grain_pool[:w._neck_grain_count]:
                        pts = _ln.points
                        if len(pts) >= 4:
                            ys.append((pts[1], pts[3], pts[0]))
                    if ys:
                        import collections
                        hist = collections.Counter(int((a + b) / 2) for a, b, _x in ys)
                        print("PROBE tick y histogram:",
                              sorted(hist.items())[:40])
                        print("PROBE tick y range %d..%d  x range %.1f..%.1f" %
                              (min(a for a, _, _ in ys), max(b for _, b, _ in ys),
                               min(x for _, _, x in ys), max(x for _, _, x in ys)))
                    else:
                        print("PROBE no ticks with points")
                if step == "off":
                    w._hide_neck_grains()
                Clock.schedule_once(lambda _dt, s=step: self.take(s), 0.10)

            def grab(self, name):
                width, height = map(int, Window.size)
                px = glReadPixels(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE)
                img = Image.frombytes("RGBA", (width, height), px)
                img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM).convert("RGB")
                img.save(OUT / ("abl_%s.png" % name))
                return np.asarray(img).astype(int)

            def take(self, step):
                self.saved[step] = self.grab(step)
                Clock.schedule_once(self.next_step, 0.10)

            def finish(self):
                a, a2, b = self.saved["on"], self.saved["on2"], self.saved["off"]
                ctl = np.abs(a - a2).max(axis=2)
                d = np.abs(a - b).max(axis=2)
                print("PROBE control on/on2 changed px = %d" % int((ctl > 0).sum()))
                H, W, _ = a.shape
                # 找颈部: 沙色行宽 < 40 的连续行段
                sand = (np.abs(a - np.array([217, 163, 96])).max(axis=2) < 70)
                widths = sand.sum(axis=1)
                narrow = [y for y in range(H) if 6 <= widths[y] <= 40]
                runs = []
                if narrow:
                    s = narrow[0]
                    prev = s
                    for y in narrow[1:]:
                        if y - prev > 2:
                            runs.append((s, prev))
                            s = y
                        prev = y
                    runs.append((s, prev))
                print("PROBE narrow-column runs (y0,y1,px):",
                      [(r[0], r[1], int(widths[r[0]:r[1] + 1].max())) for r in runs])
                for (y0, y1, px) in [(r[0], r[1], int(widths[r[0]:r[1] + 1].max()))
                                     for r in runs]:
                    if px > 40 or y1 - y0 < 8:
                        continue
                    sub_d = d[y0 - 6:y1 + 7]
                    ch = int((sub_d > 0).sum())
                    tot = sub_d.size
                    print("PROBE neck run y %d..%d : changed px %d / %d  (%.1f%%)  "
                          "row-std on %s off %s"
                          % (y0, y1, ch, tot, 100.0 * ch / tot,
                             np.array2string(np.round(a[y0:y1 + 1].std(axis=(1, 2)), 2),
                                             max_line_width=200)[:180],
                             np.array2string(np.round(b[y0:y1 + 1].std(axis=(1, 2)), 2),
                                             max_line_width=200)[:180]))
                    # 逐行 std 剖面落盘
                    np.save(OUT / ("rowstd_on_%d.npy" % y0), a[y0:y1 + 1].std(axis=(1, 2)))
                    np.save(OUT / ("rowstd_off_%d.npy" % y0), b[y0:y1 + 1].std(axis=(1, 2)))
                im_on = Image.open(OUT / "abl_on.png")
                im_off = Image.open(OUT / "abl_off.png")
                for (y0, y1, px) in [(r[0], r[1], int(widths[r[0]:r[1] + 1].max()))
                                     for r in runs]:
                    if px > 40 or y1 - y0 < 8:
                        continue
                    box = (150, max(0, y0 - 30), 250, min(H, y1 + 30))
                    pair = Image.new("RGB", ((box[2] - box[0]) * 2 + 12,
                                             box[3] - box[1]), (255, 0, 0))
                    pair.paste(im_on.crop(box), (0, 0))
                    pair.paste(im_off.crop(box), ((box[2] - box[0]) + 12, 0))
                    pair = pair.resize((pair.width * 4, pair.height * 4), Image.NEAREST)
                    pair.save(OUT / ("pair4x_%d.png" % y0))
                print("PROBE wrote", OUT)
                self.stop()

        ProbeApp().run()


main()
